import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  applyPullResult,
  collectCodeHashEntries,
  computeCodeHashFromFiles,
  memoizedCodeHash,
  packContainer,
  unpackContainer,
  SHARD_FILES,
  latestWeightsOfKind,
  readWeightsFile,
  weightFileBase,
  weightsCachedInBucket,
  weightsKeyOk,
  weightsOf,
} from '../tools/agent/sampler-agent'
import { WEIGHT_RE } from '../tools/agent/workdir-cleanup'
// F3/F4 纯实现（与 sampler-agent 同源，诊断工具/单测共用）
import {
  codeHashReport,
  collectCodeHashEntries as collectCodeHashEntriesPure,
  computeCodeHash,
  computeEngineEpoch,
  engineEpochFromCodeHash,
  REPO_ROOT,
} from '../tools/agent/codehash-files'
import { buildPack, PACK_MAGIC } from '../tools/sim/pack-container'

/**
 * BCV2 独立解码器：镜像 nn-training/dist_common.py 的 struct 解析路径重写，
 * 不复用写入端任何内部逻辑（tests/stages.test.ts 的独立重实现惯例）。
 */
function decodeBcv2(buf: Uint8Array): {
  manifest: Record<string, unknown>
  files: Map<string, Uint8Array>
} {
  const frame = Bun.gunzipSync(new Uint8Array(buf))
  const dv = new DataView(frame.buffer, frame.byteOffset, frame.byteLength)
  let off = 0
  const magic = dv.getUint32(off, false)
  off += 4
  expect(magic).toBe(PACK_MAGIC)
  const hlen = dv.getUint32(off, false)
  off += 4
  const header = JSON.parse(new TextDecoder().decode(frame.subarray(off, off + hlen))) as {
    fmt: string
    manifest: Record<string, unknown>
    files: { name: string; len: number }[]
  }
  off += hlen
  expect(header.fmt).toBe('bcv2')
  const files = new Map<string, Uint8Array>()
  for (const spec of header.files) {
    const nlen = dv.getUint16(off, false)
    off += 2
    const name = new TextDecoder().decode(frame.subarray(off, off + nlen))
    off += nlen
    const dlen = Number(dv.getBigUint64(off, false))
    off += 8
    expect(name).toBe(spec.name)
    expect(dlen).toBe(spec.len)
    files.set(name, frame.subarray(off, off + dlen))
    off += dlen
  }
  expect(off).toBe(frame.length)
  return { manifest: header.manifest, files }
}

describe('dist sampler-agent helpers (plan/distributed-rollout.md v3.3)', () => {
  it('container roundtrip preserves manifest + files', () => {
    const report = { stage: 3, seed: 42, wver: 'ab'.repeat(32), games: 1, scoreList: [0.5] }
    const files = Object.fromEntries(
      SHARD_FILES.map((n: string, i: number) => [n, Buffer.from([i, 1, 2, 3]).toString('base64')]),
    )
    const buf = packContainer(report, files)
    const back = unpackContainer(buf)
    expect(back.manifest).toEqual(report)
    expect(back.files).toEqual(files)
  })

  it('codeHash is order-independent and content-sensitive', () => {
    const mk = (relPath: string, content: string) => ({ relPath, content: Buffer.from(content) })
    const a = computeCodeHashFromFiles([mk('b.ts', 'x'), mk('a.ts', 'y')])
    const b = computeCodeHashFromFiles([mk('a.ts', 'y'), mk('b.ts', 'x')])
    expect(a).toBe(b)
    expect(a).toMatch(/^[0-9a-f]{64}$/)
    const c = computeCodeHashFromFiles([mk('b.ts', 'x!'), mk('a.ts', 'y')])
    expect(c).not.toBe(a)
    const d = computeCodeHashFromFiles([mk('b.ts', 'x'), mk('aa.ts', 'y')])
    expect(d).not.toBe(a) // 路径变化也要改变 hash
  })

  it('shard file set matches ppo.py load_shard expectation (10 npy)', () => {
    // v2 schema (AI-No-Items-Warmstart M2) deleted the item head → the shard
    // dropped a_item/lp_item (12 → 10 npy). ppo.py load_shard loads exactly
    // these 10 keys — mirror them here (independent re-statement).
    const expected = [
      'obs.npy',
      'scalars.npy',
      'a_move.npy',
      'a_fire.npy',
      'lp_move.npy',
      'lp_fire.npy',
      'value.npy',
      'metrics.npy',
      'done.npy',
      'mask.npy',
    ]
    const names: string[] = [...SHARD_FILES]
    expect(names.length).toBe(expected.length)
    expect([...names].sort()).toEqual([...expected].sort())
  })
})

describe('BCV2 result container v2 (plan/distributed-rollout.md v3.6)', () => {
  const manifest = {
    stage: 7,
    seed: 860001,
    mode: 'rollout',
    wver: 'cd'.repeat(32),
    elapsedSec: 3.2,
    dimLists: { progress: [0.5, 1] },
  }

  it('roundtrip preserves manifest + raw file bytes (12 npy, incl. multi-MB payload)', () => {
    // obs 用 >1MB 伪随机体覆盖 u64 长度路径；其余用定长小体。
    const big = Buffer.alloc(3 * 1024 * 1024 + 7)
    let s = 12345
    for (let i = 0; i < big.length; i++) {
      s = (s * 1103515245 + 12345) & 0x7fffffff
      big[i] = s & 0xff
    }
    const entries = SHARD_FILES.map((n: string, i: number) => ({
      name: n,
      data: i === 0 ? big : Buffer.from([i, 9, 9, 9]),
    }))
    const packed = buildPack(manifest, entries)
    const { manifest: m, files } = decodeBcv2(packed)
    expect(m).toEqual(manifest)
    expect([...files.keys()]).toEqual([...SHARD_FILES])
    expect(Buffer.compare(files.get('obs.npy') as Buffer, big)).toBe(0)
  })

  it('deterministic bytes for identical input (replay-safe)', () => {
    const entries = [{ name: 'value.npy', data: Buffer.from([1, 2, 3]) }]
    expect(Buffer.compare(buildPack(manifest, entries), buildPack(manifest, entries))).toBe(0)
  })

  it('eval case with empty entry list still decodes', () => {
    const packed = buildPack({ ...manifest, mode: 'eval' }, [])
    const { manifest: m, files } = decodeBcv2(packed)
    expect(files.size).toBe(0)
    expect(m.mode).toBe('eval')
  })

  it('v1 legacy helper still decodes its own format (old-agent compat path)', () => {
    const buf = packContainer(
      { stage: 1, seed: 2 },
      { 'value.npy': Buffer.from([7]).toString('base64') },
    )
    const back = unpackContainer(buf)
    expect(back.manifest).toEqual({ stage: 1, seed: 2 })
    expect(back.files['value.npy']).toBe(Buffer.from([7]).toString('base64'))
  })
})

describe('codeHash SSOT manifest (tools/agent/codehash-files.txt)', () => {
  it('collectCodeHashEntries expands dir + file entries with posix relPath', () => {
    const rels = collectCodeHashEntries().map((e) => e.relPath)
    expect(rels.length).toBeGreaterThan(10)
    // 2026-09-01 事故：Python 侧加了这 3 个文件、TS 侧漏同步 → 节点被永久误判 stale。
    for (const need of [
      'tools/agent/restart-guard.ts',
      'src/types.ts',
      'src/game/SimulationCombat.ts',
      'tools/agent/sampler-agent.ts',
    ]) {
      expect(rels).toContain(need)
    }
    expect(rels.some((r) => r.startsWith('src/nn/'))).toBe(true) // 目录条目递归纳入
    expect(rels.every((r) => !r.includes('\\'))).toBe(true) // 全 posix 正斜杠
    expect(new Set(rels).size).toBe(rels.length) // 无重复
  })

  it('F3 目录递归噪声过滤：隐藏/缓存/临时文件不计入；单文件条目不过滤', () => {
    // fixture 落在仓库 tmp/pytest-tmp（gitignored）：manifest 条目按 REPO_ROOT 相对
    // 路径解析，OS 临时目录的绝对路径无法经 walk 的 path.join(REPO_ROOT, ...) 到达。
    const base = mkdtempSync(join(REPO_ROOT, 'tmp', 'pytest-tmp', 'chfix-f3-'))
    const relDir = join(base, 'src', 'nn')
    mkdirSync(join(relDir, 'wasm'), { recursive: true })
    writeFileSync(join(relDir, 'conv.ts'), 'x')
    writeFileSync(join(relDir, 'wasm', 'conv_feats.wasm'), 'wasm')
    writeFileSync(join(relDir, '.DS_Store'), 'x')
    mkdirSync(join(relDir, '__pycache__'), { recursive: true })
    writeFileSync(join(relDir, '__pycache__', 'infer.pyc'), 'x')
    writeFileSync(join(relDir, 'x.pyc'), 'x')
    writeFileSync(join(relDir, 'x.tmp'), 'x')
    writeFileSync(join(relDir, 'x~'), 'x')
    const relPosix = base.slice(REPO_ROOT.length).replace(/[\\/]/g, '/').replace(/^\//, '')
    const manifest = join(base, 'codehash-files.txt')
    // 目录条目（受过滤）+ 显式单文件条目（不过滤）。
    writeFileSync(manifest, `# fixture\n${relPosix}/src/nn/\n${relPosix}/src/nn/x.tmp\n`)
    try {
      const rels = collectCodeHashEntriesPure(manifest).map((e) => e.relPath)
      const p = (f: string): string => `${relPosix}/src/nn/${f}`
      // 目录递归只留合法文件；显式单文件 x.tmp 不过滤（F3 契约）。
      expect(rels).toContain(p('conv.ts'))
      expect(rels).toContain(p('wasm/conv_feats.wasm'))
      expect(rels).toContain(p('x.tmp'))
      for (const bad of ['.DS_Store', '__pycache__', 'x.pyc', 'x~']) {
        expect(rels.some((r) => r === p(bad) || r.startsWith(p(bad) + '/'))).toBe(false)
      }
    } finally {
      rmSync(base, { recursive: true, force: true })
    }
  })

  it('SSOT 清单覆盖 eval 引擎面，且不含与 rollout/eval 无关的树（2026-09-17）', () => {
    const rels = collectCodeHashEntriesPure().map((e) => e.relPath)
    // 引擎语义（原 GAMEPLAY_SPECS 已并入本清单）：改 src/game 必须改 codeHash，
    // 否则 eval 节点会带着异构 gameplay 过门。
    for (const spec of ['src/game/', 'src/config/', 'src/utils/', 'src/ai/']) {
      expect(rels.some((r) => r.startsWith(spec))).toBe(true)
    }
    expect(rels).toContain('tools/det-golden.v1.sha256')
    expect(rels).toContain('tools/sim/export-eval-game.ts')
    // 无关树（dashboard / nn-training）入集 = 它们的每次提交都触发节点重启波。
    expect(rels.some((r) => r.startsWith('dashboard/') || r.startsWith('nn-training/'))).toBe(false)
  })

  it('engine_epoch = sha256(codeHash)[0:16]（eval 门与 rollout 门同源，不掺 git commit）', () => {
    const ch = computeCodeHash()
    const want = createHash('sha256').update(ch).digest('hex').slice(0, 16)
    expect(engineEpochFromCodeHash(ch)).toBe(want)
    expect(computeEngineEpoch()).toBe(want)
    expect(computeEngineEpoch()).toMatch(/^[0-9a-f]{16}$/)
    // 旧式实现掺 git full commit（sha256(git + gameplay)）——本文件源码不得再出现。
    const src = readFileSync(new URL('../tools/agent/sampler-agent.ts', import.meta.url), 'utf8')
    const body = src.slice(src.indexOf('function memoizedEngineEpoch'))
    expect(body.slice(0, 400)).not.toContain('rev-parse')
  })

  it('F4 codeHashReport 输出格式：sha8\\tsize\\trelPath + codeHash=<full> 末行', () => {
    const report = codeHashReport()
    const lines = report.split('\n')
    expect(lines[lines.length - 1]).toMatch(/^codeHash=[0-9a-f]{64}$/)
    expect(lines.length).toBe(collectCodeHashEntriesPure().length + 1)
    if (lines.length > 1) {
      const first = lines[0].split('\t')
      expect(first.length).toBe(3)
      expect(first[0]).toMatch(/^[0-9a-f]{8}$/)
      expect(Number.isInteger(Number(first[1]))).toBe(true)
    }
  })

  it('weightsCachedInBucket：sha 命中 / 空 sha / 无桶（kept 短路径探针，2026-09-19）', () => {
    const sha = 'a'.repeat(64)
    const bucket = new Map<string, unknown>([[sha, { sha }]])
    expect(weightsCachedInBucket(bucket, sha)).toBe(true)
    expect(weightsCachedInBucket(bucket, 'b'.repeat(64))).toBe(false)
    expect(weightsCachedInBucket(undefined, sha)).toBe(false)
    expect(weightsCachedInBucket(bucket, '')).toBe(false)
  })
})

describe('权重磁盘回查（agent 重启后不再对盘上已有的权重答 409，2026-09-19）', () => {
  const mkDir = (): string => mkdtempSync(join(REPO_ROOT, 'tmp', 'pytest-tmp', 'wdisk-'))
  /** 每次唯一内容 ⇒ 每次唯一 sha（避免同进程内复用桶缓存，测试相互独立）。 */
  const fresh = (tag: string): { sha: string; body: Buffer } => {
    const body = Buffer.from(`${tag}-${Date.now()}-${Math.random()}`)
    return { sha: createHash('sha256').update(body).digest('hex'), body }
  }

  it('readWeightsFile：命中要求**内容** sha 全量一致；不符/缺文件/非法键一律未命中', () => {
    const dir = mkDir()
    try {
      const { sha, body } = fresh('hit')
      writeFileSync(join(dir, weightFileBase('eval', sha)), body)
      expect(readWeightsFile('eval', sha, dir)).toEqual({
        sha,
        iterId: '',
        file: join(dir, `weights-eval-${sha.slice(0, 16)}.json`),
      })

      // 文件名只有 16 hex 前缀 —— **不足以判定内容**：同名不同内容必须拒绝
      writeFileSync(join(dir, weightFileBase('eval', sha)), Buffer.from('not the same bytes'))
      expect(readWeightsFile('eval', sha, dir)).toBeNull()

      // 前缀碰撞（前 16 hex 相同、后面不同）：只按文件名查会误命中，按内容查必须拒绝
      const collide = sha.slice(0, 16) + 'f'.repeat(48)
      expect(collide).not.toBe(sha)
      writeFileSync(join(dir, weightFileBase('eval', sha)), body)
      expect(readWeightsFile('eval', collide, dir)).toBeNull()

      // 缺文件 / 非法键（路径穿越、短 sha、大写 hex）
      expect(readWeightsFile('eval', 'a'.repeat(64), dir)).toBeNull()
      expect(readWeightsFile('../etc', sha, dir)).toBeNull()
      expect(weightsKeyOk('../etc', sha)).toBe(false)
      expect(weightsKeyOk('eval', sha.slice(0, 32))).toBe(false)
      expect(weightsKeyOk('eval', sha.toUpperCase())).toBe(false)
      expect(weightsKeyOk('eval', sha)).toBe(true)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('weightsOf：内存桶未命中 → 回查磁盘；命中即回填（此后文件消失也不影响出局）', () => {
    const dir = mkDir()
    const { sha, body } = fresh('rehydrate')
    const file = join(dir, weightFileBase('eval', sha))
    try {
      expect(weightsOf('eval', sha, dir)).toBeNull() // 盘上还没有 ⇒ 仍应 409
      writeFileSync(file, body)
      expect(weightsOf('eval', sha, dir)?.sha).toBe(sha) // 回查磁盘命中
      rmSync(file, { force: true })
      // 已回填内存桶：文件没了（被 sweep 清 / 另一进程删）也照样命中
      expect(weightsOf('eval', sha, dir)?.sha).toBe(sha)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('weightFileBase：POST 落盘与磁盘回查同名，且落在 retention 正则的命名域内', () => {
    const sha = 'ab'.repeat(32)
    expect(weightFileBase('rollout', sha)).toBe(`weights-rollout-${sha.slice(0, 16)}.json`)
    expect(weightFileBase('eval', sha)).toBe(`weights-eval-${sha.slice(0, 16)}.json`)
    // 一次性工具链的 god 占位权重走 kind='none'
    expect(weightFileBase('none', sha)).toBe(`weights-none-${sha.slice(0, 16)}.json`)
    // boot/切换时的清扫只认这个正则：名字不匹配 ⇒ 回查文件会被当垃圾删掉
    for (const kind of ['rollout', 'eval', 'intent', 'goal', 'none']) {
      const name = weightFileBase(kind, sha)
      expect(WEIGHT_RE.test(name)).toBe(true)
      expect(weightsKeyOk(kind, sha)).toBe(true)
    }
  })

  it('latestWeightsOfKind：桶空（重启后）→ 取盘上 mtime 最新的一份；坏/改名的跳过；不串 kind', () => {
    const dir = mkDir()
    const older = fresh('intent-old')
    const newer = fresh('intent-new')
    const goalOld = fresh('goal-old')
    const goalBad = fresh('goal-bad')
    const fOld = join(dir, weightFileBase('intent', older.sha))
    const fNew = join(dir, weightFileBase('intent', newer.sha))
    const gOld = join(dir, weightFileBase('goal', goalOld.sha))
    const gBad = join(dir, weightFileBase('goal', goalBad.sha))
    const at = (secAgo: number): Date => new Date(Date.now() - secAgo * 1000)
    try {
      // kind='intent'：旧 + 新两份有效权重，外加一个别的 kind 的干扰文件
      writeFileSync(fOld, older.body)
      writeFileSync(fNew, newer.body)
      const distractor = fresh('rollout-distractor')
      writeFileSync(join(dir, weightFileBase('rollout', distractor.sha)), distractor.body)
      utimesSync(fOld, at(120), at(120))
      utimesSync(fNew, at(60), at(60))
      expect(latestWeightsOfKind('intent', dir)?.sha).toBe(newer.sha)

      // 已回填内存桶 ⇒ 文件删掉也照样返回（不再读盘）
      rmSync(fNew, { force: true })
      expect(latestWeightsOfKind('intent', dir)?.sha).toBe(newer.sha)

      // kind='goal'：**最新那份是被改名的坏文件**（内容 sha ≠ 文件名前缀）⇒ 跳过它取旧的
      writeFileSync(gOld, goalOld.body)
      writeFileSync(gBad, Buffer.from('renamed/corrupted bytes'))
      utimesSync(gOld, at(120), at(120))
      utimesSync(gBad, at(10), at(10))
      expect(latestWeightsOfKind('goal', dir)?.sha).toBe(goalOld.sha)

      // 该 kind 盘上什么都没有 ⇒ null（不串到别的 kind）
      expect(latestWeightsOfKind('zzprobe', dir)).toBeNull()
      // 非法 kind（路径穿越）⇒ null
      expect(latestWeightsOfKind('../etc', dir)).toBeNull()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

// F2（2026-09-19 审计）：`/v1/update` 只 pull 不重启 ⇒ 它**不得**使 codeHash memo 失效，
// 否则节点会「报新 hash、跑旧代码」，codeHash 门（dist_common.check_code_hash）反向放行。
describe('F2 codeHash 归属：报的是**运行中代码**，不是盘上代码', () => {
  const SRC = readFileSync(
    join(import.meta.dir, '..', 'tools', 'agent', 'sampler-agent.ts'),
    'utf8',
  )

  it('applyPullResult：pull 成功（changed）也不得改变已算出的 codeHash', () => {
    const before = memoizedCodeHash()
    expect(before).toMatch(/^[0-9a-f]{64}$/)
    expect(
      applyPullResult({
        changed: true,
        branch: 'goal-nn',
        oldSha: 'a'.repeat(40),
        newSha: 'b'.repeat(40),
      }),
    ).toBe(true)
    expect(memoizedCodeHash()).toBe(before) // 本进程仍跑启动时那份代码 ⇒ hash 不变
    // 无变更时甚至连日志都不发（幂等）；拉过与否都不影响 hash
    expect(
      applyPullResult({
        changed: false,
        branch: '',
        oldSha: 'a'.repeat(40),
        newSha: 'a'.repeat(40),
      }),
    ).toBe(false)
    expect(memoizedCodeHash()).toBe(before)
  })

  it('源码守卫：/v1/update 不得置空 codeHash/gitShort memo（防「pull 后重算」回归）', () => {
    expect(SRC).not.toContain('codeHashMemo.value = null')
    expect(SRC).not.toContain('gitShortMemo.value = null')
    // 唯一允许 hash 变化的途径 = 重启（进程换代码）
    expect(SRC).toContain('memoizedCodeHash()')
  })

  it('源码守卫：/v1/restart 分支在退出前显式收长驻 worker 池（旧代码带着旧代码继续算）', () => {
    const start = SRC.indexOf("url.pathname === '/v1/restart'")
    expect(start).toBeGreaterThan(0)
    const branch = SRC.slice(start, SRC.indexOf('process.exit(0)', start))
    expect(branch).toContain('killPersistPool()')
  })
})
