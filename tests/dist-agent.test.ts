import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  collectCodeHashEntries,
  computeCodeHashFromFiles,
  packContainer,
  unpackContainer,
  SHARD_FILES,
} from '../tools/agent/sampler-agent'
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
})
