/**
 * node-upgrade.test.ts ↔ tools/lib/node-upgrade.ts
 *
 * 契约核心：TS **不**自己 ping、**不**自己判 stale —— 它只把 cfg 路径与跨调用 memo
 * 交给 `nn-training/dist_upgrade_cli.py`（扫描模式），探测/判门/护栏全在 dist_common。
 * 因此这里的断言聚焦「发给 Python 的 spec 长什么样」与「memo 往返」。
 */
import { describe, expect, it } from 'bun:test'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import {
  buildUpgradeSpec,
  latestSeenEntries,
  memoKey,
  parseMemoKey,
  parseUpgradeOutput,
  pythonCandidates,
  requestNodeUpgrades,
  resolveUpgradeBranch,
  upgradeLogLines,
  type UpgradeOutcome,
} from '../tools/lib/node-upgrade'

const REPO = path.resolve(import.meta.dir, '..')
const EXP = 'ba6f7b4e'.padEnd(64, '0')
const STALE = 'aeccc983'.padEnd(64, '0')
const CFG = 'nn-training/rl-config.json'

/** 假子进程：把收到的 spec 记下来，返回预设 stdout。 */
function fakeSpawn(
  out: string,
  exitCode = 0,
  seen?: { argv: string[]; spec: string; cwd: string }[],
) {
  return (argv: string[], input: string, cwd: string) => {
    seen?.push({ argv, spec: input, cwd })
    return { exitCode, stdout: out, stderr: '' }
  }
}

describe('buildUpgradeSpec / parseUpgradeOutput', () => {
  it('扫描模式 spec：cfg_path + branch + expected_hash + seen（**没有** nodes/ping）', () => {
    const spec = buildUpgradeSpec({
      expectedHash: EXP,
      branch: 'goal-nn',
      cfgPath: CFG,
      seen: [{ id: 'mac', pingHash: STALE, expectedHash: EXP }],
      dirty: null,
    })
    expect(spec.cfg_path).toBe(CFG)
    expect(spec.branch).toBe('goal-nn')
    expect(spec.expected_hash).toBe(EXP)
    expect(spec.dirty).toBeNull()
    expect(spec.dry_run).toBe(false)
    expect(spec.nodes).toBeUndefined()
    expect((spec.seen as Array<Record<string, unknown>>)[0]?.pingHash).toBe(STALE)
  })

  it('缺省字段不硬塞：无 expectedHash（Python 自己算 code_hash）/ 无 seen 时不写该键', () => {
    const spec = buildUpgradeSpec({ branch: '', cfgPath: CFG })
    expect(spec.expected_hash).toBeUndefined()
    expect(spec.seen).toBeUndefined()
    expect(spec.branch).toBe('')
  })

  it('解析单行 JSON（含 pingHash）；error 字段与坏 JSON 都抛（调用方转成响亮告警）', () => {
    const p = parseUpgradeOutput(
      '{"dirty":["src/a.ts"],"results":[{"id":"mac","ok":true,"reason":"restart-requested","pingHash":"aa"}]}\n',
    )
    expect(p.dirty).toEqual(['src/a.ts'])
    expect(p.results).toEqual([
      { id: 'mac', ok: true, reason: 'restart-requested', pingHash: 'aa' },
    ])
    // 老格式（无 pingHash）→ 空串，不抛：写 memo 时按空跳过即可。
    expect(
      parseUpgradeOutput('{"results":[{"id":"mac","ok":false,"reason":"current"}]}').results[0],
    ).toEqual({ id: 'mac', ok: false, reason: 'current', pingHash: '' })
    expect(() => parseUpgradeOutput('{"error":"spec 非法"}')).toThrow('spec 非法')
    expect(() => parseUpgradeOutput('not json')).toThrow()
    expect(() => parseUpgradeOutput('')).toThrow()
  })
})

describe('requestNodeUpgrades（经 nn-py-safe.sh 调 python 扫描）', () => {
  it('正常路径：走官方启动器 + 传 cfg_path + 回填结果，并按 pingHash 落 memo', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'upg-'))
    try {
      const seen: Array<{ argv: string[]; spec: string; cwd: string }> = []
      const out = JSON.stringify({
        dirty: ['src/x.ts'],
        results: [
          { id: 'mac', ok: false, reason: 'dirty-tree:1', pingHash: STALE },
          { id: 'a95', ok: true, reason: 'restart-requested', pingHash: STALE },
          { id: 'down', ok: false, reason: 'unreachable', pingHash: '' },
        ],
      })
      const memoPath = path.join(dir, 'memo.json')
      const r = requestNodeUpgrades(
        { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', cfgPath: CFG, dirty: null },
        { spawn: fakeSpawn(out, 0, seen), memoPath, env: {} },
      )
      expect(r.ok).toBe(true)
      expect(r.dirty).toEqual(['src/x.ts'])
      expect(r.results.map((x) => x.reason)).toEqual([
        'dirty-tree:1',
        'restart-requested',
        'unreachable',
      ])
      // 必须经沙箱免疫启动器（严禁裸 python）；cfg 路径透传（探测在 python 侧）。
      expect(seen[0]?.argv.slice(0, 2)).toEqual(['bash', 'tools/githook/nn-py-safe.sh'])
      expect(seen[0]?.argv[2]).toBe('nn-training/dist_upgrade_cli.py')
      const sent = JSON.parse(seen[0]!.spec) as Record<string, unknown>
      expect(sent.cfg_path).toBe(CFG)
      expect(sent.expected_hash).toBe(EXP)
      expect(sent.seen).toBeUndefined() // 首次无 memo

      const lines = upgradeLogLines('[t]', r)
      expect(lines.join('\n')).toContain('工作区有 1 个集内文件未提交')
      expect(lines.join('\n')).toContain('已下发 pull+restart')
      expect(lines.join('\n')).toContain('ping 不通')

      // 第二次：memo 里已有 a95 的 (agent hash, 期望 hash) ⇒ 作为 seen 预置回 Python
      // （判据仍是 _RESTART_SEEN，TS 不重复实现；被拒/不可达的不记，下轮仍可尝试）。
      const seen2: Array<{ argv: string[]; spec: string; cwd: string }> = []
      requestNodeUpgrades(
        { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', cfgPath: CFG },
        { spawn: fakeSpawn(out, 0, seen2), memoPath, env: {} },
      )
      const sent2 = JSON.parse(seen2[0]!.spec) as { seen?: Array<Record<string, string>> }
      expect(sent2.seen).toEqual([{ id: 'a95', pingHash: STALE, expectedHash: EXP }])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('子进程失败 ⇒ ok:false + error（绝不静默）', () => {
    const r = requestNodeUpgrades(
      { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', cfgPath: CFG },
      { spawn: fakeSpawn('', 1), noMemo: true, env: {} },
    )
    expect(r.ok).toBe(false)
    expect(r.error).toContain('升级子进程退出 1')
    expect(upgradeLogLines('[t]', r).join('\n')).toContain('node upgrade FAILED')
  })

  it('spawn 抛（bash/venv 不可用）⇒ ok:false 且提示解释器覆盖办法', () => {
    const r = requestNodeUpgrades(
      { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', cfgPath: CFG },
      {
        spawn: () => {
          throw new Error('bash: not found')
        },
        noMemo: true,
        env: {},
      },
    )
    expect(r.ok).toBe(false)
    expect(r.error).toContain('bash: not found')
    expect(r.error).toContain('NN_PY')
  })

  it('dry_run 只计划不发（stdout 的 reason=stale/planned 原样回传）', () => {
    const out = JSON.stringify({
      dirty: [],
      results: [{ id: 'mac', ok: false, reason: 'stale', pingHash: STALE }],
    })
    const r = requestNodeUpgrades(
      { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', cfgPath: CFG, dryRun: true },
      { spawn: fakeSpawn(out), noMemo: true, env: {} },
    )
    expect(r.results[0]?.reason).toBe('stale')
    expect(upgradeLogLines('[t]', r).join('\n')).toContain('dry-run 未下发')
  })

  it('dry_run 也不写 memo（没真下发 ⇒ 下次仍应重试）', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'upg-'))
    try {
      const memoPath = path.join(dir, 'memo.json')
      const out = JSON.stringify({
        dirty: [],
        results: [{ id: 'mac', ok: true, reason: 'restart-requested', pingHash: STALE }],
      })
      requestNodeUpgrades(
        { repoRoot: REPO, expectedHash: EXP, branch: 'b', cfgPath: CFG, dryRun: true },
        { spawn: fakeSpawn(out), memoPath, env: {} },
      )
      const memo = existsSync(memoPath)
        ? (JSON.parse(readFileSync(memoPath, 'utf-8')) as Record<string, string>)
        : {}
      expect(latestSeenEntries(memo)).toEqual([])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('memo（跨调用去重状态；判据在 dist_common，这里只管持久化与往返）', () => {
  it('memo 键 = 节点 + agent hash + 期望 hash（**全量** hex，供 Python 预置 _RESTART_SEEN）', () => {
    expect(memoKey('mac', STALE, EXP)).toBe(`mac|${STALE}|${EXP}`)
    expect(parseMemoKey(memoKey('mac', STALE, EXP))).toEqual({
      id: 'mac',
      pingHash: STALE,
      expectedHash: EXP,
    })
  })

  it('latestSeenEntries：每节点只取最新一条；坏键忽略', () => {
    const entries = latestSeenEntries({
      [`mac|${STALE}|${EXP}`]: '2026-09-19T01:00:00Z',
      [`mac|${'c'.repeat(64)}|${EXP}`]: '2026-09-19T02:00:00Z', // 更新 ⇒ 取这条
      [`a95|${STALE}|${EXP}`]: '2026-09-19T01:30:00Z',
      garbage: 'x',
      'nodots|short|': 'y',
    })
    const byId = Object.fromEntries(entries.map((e) => [e.id, e]))
    expect(byId['mac']?.pingHash).toBe('c'.repeat(64))
    expect(byId['a95']?.pingHash).toBe(STALE)
    expect(entries.length).toBe(2)
  })
})

describe('resolveUpgradeBranch / pythonCandidates', () => {
  it('分支：UPGRADE_BRANCH 优先；否则 git 当前分支；取不到 ⇒ 空串', () => {
    expect(resolveUpgradeBranch(REPO, { env: { UPGRADE_BRANCH: 'from-env' } })).toBe('from-env')
    expect(
      resolveUpgradeBranch(REPO, {
        env: {},
        spawn: () => ({ exitCode: 0, stdout: 'goal-nn\n', stderr: '' }),
      }),
    ).toBe('goal-nn')
    expect(
      resolveUpgradeBranch(REPO, {
        env: {},
        spawn: () => ({ exitCode: 0, stdout: 'HEAD\n', stderr: '' }),
      }),
    ).toBe('')
    expect(
      resolveUpgradeBranch(REPO, {
        env: {},
        spawn: () => ({ exitCode: 128, stdout: '', stderr: 'x' }),
      }),
    ).toBe('')
  })

  it('venv python 候选（仅用于错误提示）', () => {
    const c = pythonCandidates(REPO)
    expect(c[0]?.endsWith(path.join('nn-training', '.venv', 'Scripts', 'python.exe'))).toBe(true)
  })
})

describe('真子进程（dry-run：不发 POST，只验证链路与探测可达）', () => {
  const hasVenv = pythonCandidates(REPO).some((p) => existsSync(p))
  it.skipIf(!hasVenv)(
    '经 nn-py-safe.sh 调 dist_upgrade_cli.py 扫描模式：不存在的节点 → unreachable/stale',
    () => {
      const dir = mkdtempSync(path.join(tmpdir(), 'upg-cfg-'))
      try {
        const cfgPath = path.join(dir, 'rl-config.json')
        writeFileSync(
          cfgPath,
          JSON.stringify({ nodes: [{ id: 'fake-remote', url: 'http://127.0.0.1:1' }] }),
          'utf-8',
        )
        const r = requestNodeUpgrades(
          { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', cfgPath, dryRun: true },
          { noMemo: true, env: {} },
        )
        expect(r.ok).toBe(true)
        expect(r.results[0]?.id).toBe('fake-remote')
        // 黑洞端口 → unreachable（探测由 python 侧做，TS 不参与判定）。
        expect(r.results[0]?.reason).toBe('unreachable')
      } finally {
        rmSync(dir, { recursive: true, force: true })
      }
    },
  )
})

describe('upgradeLogLines 逐 reason 文案', () => {
  const out = (reason: string): UpgradeOutcome => ({
    ok: true,
    dirty: [],
    results: [{ id: 'mac', ok: false, reason, pingHash: '' }],
  })
  it('current / dedup / dirty-tree / restart-failed / unreachable 都有可读文案', () => {
    expect(upgradeLogLines('[t]', out('current')).join('\n')).toContain('已是期望 codeHash')
    expect(upgradeLogLines('[t]', out('dedup')).join('\n')).toContain('已发过')
    expect(upgradeLogLines('[t]', out('dirty-tree:3')).join('\n')).toContain('拒发（dirty-tree:3）')
    expect(upgradeLogLines('[t]', out('restart-failed')).join('\n')).toContain('agent 拒绝/不可达')
    expect(upgradeLogLines('[t]', out('unreachable')).join('\n')).toContain('ping 不通')
  })
})
