/** node-upgrade.test.ts ↔ tools/lib/node-upgrade.ts（复用训练循环守卫的 TS 客户端）。 */
import { describe, expect, it } from 'bun:test'
import { existsSync, mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import {
  buildUpgradeSpec,
  memoKey,
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
const NODES = [
  { id: 'mac', url: 'http://192.168.0.88:8443', authKey: 'k', pingHash: STALE },
  { id: 'a95', url: 'http://192.168.0.95:8443', pingHash: STALE },
]

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
  it('spec 字段名与 Python 侧契约一致（expected_hash/branch/nodes[].pingHash/dirty/dry_run）', () => {
    const spec = buildUpgradeSpec({
      expectedHash: EXP,
      branch: 'goal-nn',
      nodes: NODES,
      dirty: null,
    })
    expect(spec.expected_hash).toBe(EXP)
    expect(spec.branch).toBe('goal-nn')
    expect(spec.dirty).toBeNull()
    expect(spec.dry_run).toBe(false)
    expect((spec.nodes as Array<Record<string, unknown>>)[0]?.pingHash).toBe(STALE)
  })

  it('解析单行 JSON；error 字段与坏 JSON 都抛（调用方转成响亮告警）', () => {
    const p = parseUpgradeOutput(
      '{"dirty":["src/a.ts"],"results":[{"id":"mac","ok":true,"reason":"restart-requested"}]}\n',
    )
    expect(p.dirty).toEqual(['src/a.ts'])
    expect(p.results).toEqual([{ id: 'mac', ok: true, reason: 'restart-requested' }])
    expect(() => parseUpgradeOutput('{"error":"spec 非法"}')).toThrow('spec 非法')
    expect(() => parseUpgradeOutput('not json')).toThrow()
    expect(() => parseUpgradeOutput('')).toThrow()
  })
})

describe('requestNodeUpgrades（经 nn-py-safe.sh 调 python 守卫）', () => {
  it('正常路径：走官方启动器 + 传 spec + 回填结果，并落 memo', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'upg-'))
    try {
      const seen: Array<{ argv: string[]; spec: string; cwd: string }> = []
      const out = JSON.stringify({
        dirty: ['src/x.ts'],
        results: [
          { id: 'mac', ok: false, reason: 'dirty-tree:1' },
          { id: 'a95', ok: true, reason: 'restart-requested' },
        ],
      })
      const r = requestNodeUpgrades(
        { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', nodes: NODES, dirty: null },
        { spawn: fakeSpawn(out, 0, seen), memoPath: path.join(dir, 'memo.json'), env: {} },
      )
      expect(r.ok).toBe(true)
      expect(r.dirty).toEqual(['src/x.ts'])
      expect(r.results.map((x) => x.reason)).toEqual(['dirty-tree:1', 'restart-requested'])
      // 必须经沙箱免疫启动器（严禁裸 python）。
      expect(seen[0]?.argv.slice(0, 2)).toEqual(['bash', 'tools/githook/nn-py-safe.sh'])
      expect(seen[0]?.argv[2]).toBe('nn-training/dist_upgrade_cli.py')
      expect(JSON.parse(seen[0]!.spec).expected_hash).toBe(EXP)
      // memo 只记成功的 restart-requested（被拒的不记，下轮仍可尝试）。
      const lines = upgradeLogLines('[t]', r)
      expect(lines.join('\n')).toContain('工作区有 1 个集内文件未提交')
      expect(lines.join('\n')).toContain('已下发 pull+restart')
      const second = requestNodeUpgrades(
        { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', nodes: NODES, dirty: null },
        { spawn: fakeSpawn(out), memoPath: path.join(dir, 'memo.json'), env: {} },
      )
      // 跨调用去重：a95 已在 memo ⇒ 本次只对 mac 下发（不重复打扰节点）。
      expect(second.results.some((x) => x.id === 'a95' && x.reason.startsWith('dedup (memo'))).toBe(
        true,
      )
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('子进程失败 ⇒ ok:false + error（绝不静默）', () => {
    const r = requestNodeUpgrades(
      { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', nodes: NODES },
      { spawn: fakeSpawn('', 1), noMemo: true, env: {} },
    )
    expect(r.ok).toBe(false)
    expect(r.error).toContain('升级子进程退出 1')
    expect(upgradeLogLines('[t]', r).join('\n')).toContain('node upgrade FAILED')
  })

  it('spawn 抛（bash/venv 不可用）⇒ ok:false 且提示解释器覆盖办法', () => {
    const r = requestNodeUpgrades(
      { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', nodes: NODES },
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

  it('dry_run 只计划不发（stdout 的 reason=planned 原样回传）', () => {
    const out = JSON.stringify({
      dirty: [],
      results: [{ id: 'mac', ok: false, reason: 'planned' }],
    })
    const r = requestNodeUpgrades(
      { repoRoot: REPO, expectedHash: EXP, branch: 'goal-nn', nodes: NODES, dryRun: true },
      { spawn: fakeSpawn(out), noMemo: true, env: {} },
    )
    expect(r.results[0]?.reason).toBe('planned')
  })

  it('全部节点已在 memo ⇒ 一个子进程都不起', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'upg-'))
    try {
      let calls = 0
      const memoPath = path.join(dir, 'memo.json')
      const out = JSON.stringify({
        dirty: [],
        results: [
          { id: 'mac', ok: true, reason: 'restart-requested' },
          { id: 'a95', ok: true, reason: 'restart-requested' },
        ],
      })
      const deps = { spawn: fakeSpawn(out), memoPath, env: {} }
      requestNodeUpgrades(
        { repoRoot: REPO, expectedHash: EXP, branch: 'b', nodes: NODES },
        { ...deps, spawn: (a, i, c) => (calls++, deps.spawn(a, i, c)) },
      )
      expect(calls).toBe(1)
      const again = requestNodeUpgrades(
        { repoRoot: REPO, expectedHash: EXP, branch: 'b', nodes: NODES },
        { ...deps, spawn: (a, i, c) => (calls++, deps.spawn(a, i, c)) },
      )
      expect(calls).toBe(1)
      expect(again.results.every((x) => x.reason.startsWith('dedup (memo'))).toBe(true)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('memoKey / resolveUpgradeBranch / pythonCandidates', () => {
  it('memo 键 = 节点 + agent hash 前缀 + 期望 hash 前缀（与 _RESTART_SEEN 同语义）', () => {
    expect(memoKey('mac', STALE, EXP)).toBe('mac|aeccc9830000|ba6f7b4e0000')
  })

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

describe('真子进程（dry-run：不发 POST，只验证链路与守卫可达）', () => {
  const hasVenv = pythonCandidates(REPO).some((p) => existsSync(p))
  it.skipIf(!hasVenv)(
    '经 nn-py-safe.sh 调 dist_upgrade_cli.py，dry_run 返回 planned/dirty-tree',
    () => {
      const r = requestNodeUpgrades(
        {
          repoRoot: REPO,
          expectedHash: EXP,
          branch: 'goal-nn',
          nodes: [{ id: 'fake-remote', url: 'http://192.168.0.88:8443', pingHash: STALE }],
          dryRun: true,
        },
        { noMemo: true, env: {} },
      )
      expect(r.ok).toBe(true)
      const reason = r.results[0]?.reason ?? ''
      expect(reason === 'planned' || reason.startsWith('dirty-tree')).toBe(true)
    },
  )
})

describe('upgradeLogLines 逐 reason 文案', () => {
  const out = (reason: string): UpgradeOutcome => ({
    ok: true,
    dirty: [],
    results: [{ id: 'mac', ok: false, reason }],
  })
  it('current / dedup / dirty-tree / restart-failed 都有可读文案', () => {
    expect(upgradeLogLines('[t]', out('current')).join('\n')).toContain('已是期望 codeHash')
    expect(upgradeLogLines('[t]', out('dedup')).join('\n')).toContain('已发过')
    expect(upgradeLogLines('[t]', out('dirty-tree:3')).join('\n')).toContain('拒发（dirty-tree:3）')
    expect(upgradeLogLines('[t]', out('restart-failed')).join('\n')).toContain('agent 拒绝/不可达')
  })
})
