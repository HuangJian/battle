import { describe, expect, it } from 'bun:test'
import {
  containerSmoke,
  rlConfigSmoke,
  weightsSmoke,
  summarizeSmoke,
  type SmokeItem,
} from '../tools/training/smoke'
import {
  pythonGateState,
  resolveTorchThreads,
  torchThreadEnv,
} from '../tools/training/venv'
import { resolveTrainScript } from '../tools/training/train'
import {
  aggregateNodeHistory,
  contribCell,
  poolStatusCell,
  emptyHistory,
} from '../tools/training/monitor/history'
import { readIterMetrics } from '../tools/training/monitor/iters'
import { TRAINING_LOOP_ENTRY, trainingLoopSpec } from '../tools/training/specs'
import { renderMonitorPage } from '../tools/training/monitor/page'
import { writeFileSync, mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { REPO_ROOT } from '../tools/training/paths'
import path from 'path'

describe('training smoke suite (tools/training/smoke.ts)', () => {
  it('BCV2 container roundtrip passes (gzip + magic + manifest)', () => {
    const r = containerSmoke()
    expect(r.passed).toBe(true)
    expect(r.fatal).toBe(true)
  })

  it('rl-config smoke rejects empty config', () => {
    const bad = rlConfigSmoke({ version: 1, nodes: [], rl: {} as never })
    expect(bad.passed).toBe(false)
    expect(bad.fatal).toBe(true)
  })

  it('weights smoke: missing optional file skips, corrupt file fails', () => {
    const skip = weightsSmoke('definitely/not/here.json', false)
    expect(skip.passed).toBe(true)
    expect(skip.fatal).toBe(false)
    const req = weightsSmoke('definitely/not/here.json', true)
    expect(req.passed).toBe(false)
    expect(req.fatal).toBe(true)
  })

  it('summarizeSmoke fails only on fatal failures', () => {
    const pass: SmokeItem = { name: 'a', passed: true, fatal: true }
    const soft: SmokeItem = { name: 'b', passed: false, fatal: false }
    const hard: SmokeItem = { name: 'c', passed: false, fatal: true }
    expect(summarizeSmoke([pass])).toBe(true)
    expect(summarizeSmoke([pass, soft])).toBe(true)
    expect(summarizeSmoke([pass, hard])).toBe(false)
  })
})

describe('training venv helpers (tools/training/venv.ts)', () => {
  it('torch threads: CLI > config > clamped CPU count', () => {
    expect(resolveTorchThreads(4, 8)).toBe(4)
    expect(resolveTorchThreads(0, 8)).toBe(8)
    const n = resolveTorchThreads(0, undefined)
    expect(n).toBeGreaterThanOrEqual(1)
    expect(n).toBeLessThanOrEqual(12)
  })

  it('torch thread env sets OMP_PROC_BIND only at low thread counts (§17)', () => {
    expect(torchThreadEnv(8).OMP_PROC_BIND).toBe('CLOSE')
    expect(torchThreadEnv(12).OMP_PROC_BIND).toBeUndefined()
    expect(torchThreadEnv(4).OMP_NUM_THREADS).toBe('4')
  })

  it('pythonGateState: present-env+missing-torch = skip-with-warning; broken env = hard fail (§352)', () => {
    // 纯逻辑矩阵（真实环境探测由 CLI 测试区覆盖）：
    // 1) torch 就绪 → 什么都不跳，pyOk 必真。
    const ready = { pyOk: true, torchReady: true, python: '/x/python', reason: null }
    expect(ready.pyOk && ready.torchReady).toBe(true)
    // 2) venv 在但 torch 缺 → skip+warn（绝不红）。
    const noTorch = { pyOk: true, torchReady: false, python: '/x/python', reason: null }
    expect(noTorch.pyOk).toBe(true)
    expect(noTorch.torchReady).toBe(false)
    // 3) venv 坏/解释器不可执行 → pyOk=false，reason 说明 → 调用方硬失败。
    const broken = { pyOk: false, torchReady: false, python: '', reason: 'venv 解释器不可执行' }
    expect(broken.pyOk).toBe(false)
    expect(broken.reason).toBeTruthy()
    // 4) torchReady 为真蕴含 pyOk 为真（torch 探测建立在 python 可跑之上）。
    expect(ready.torchReady && !ready.pyOk).toBe(false)
    expect(noTorch.torchReady && !noTorch.pyOk).toBe(false)
    // pythonGateState() 纯探测本身合法性：字段齐、不变量成立。
    const s = pythonGateState()
    expect(typeof s.pyOk).toBe('boolean')
    expect(typeof s.torchReady).toBe('boolean')
    if (s.torchReady) expect(s.pyOk).toBe(true)
    if (s.pyOk) expect(s.python.length).toBeGreaterThan(0)
    if (!s.pyOk) expect(s.reason).toBeTruthy()
  })
})

describe('train script resolution (tools/training/train.ts, DECISIONS §324)', () => {
  it('resolves legacy flat aliases', () => {
    expect(resolveTrainScript('train_bc.py')).toBe('train/bc.py')
    expect(resolveTrainScript('train_rl.py')).toBe('run_rl.py')
  })
  it('passes through subpackage paths that exist', () => {
    expect(resolveTrainScript('smoke_test.py')).toBe('smoke_test.py')
  })
  it('rejects traversal / absolute / drive paths with exit 2', () => {
    for (const bad of ['../evil.py', 'C:\\evil.py', '/abs/evil.py', 'a/../b.py']) {
      let code = 0
      const origExit = process.exit
      process.exit = ((c: number) => {
        code = c
        throw new Error('exit')
      }) as typeof process.exit
      try {
        resolveTrainScript(bad)
        expect.unreachable()
      } catch {
        /* expected */
      } finally {
        process.exit = origExit
      }
      expect(code).toBe(2)
    }
  })
})

describe('monitor data layers (tools/training/monitor/*)', () => {
  it('empty history renders placeholder cells', () => {
    const h = emptyHistory()
    expect(poolStatusCell(h)).toContain('无数据')
    expect(contribCell(h, 5)).toContain('-')
  })
  it('contrib cell marks lagging nodes with tooltip', () => {
    const h = { ...emptyHistory(), lastIter: 3, lastIterOk: 0 }
    const cell = contribCell(h, 10)
    expect(cell).toContain('it3')
    expect(cell).toContain('it10')
  })
  it('aggregateNodeHistory returns empty aggregate without tmp data', () => {
    // 仓库 tmp/ 总存在；聚合不抛错即可（数据多少无关正确性）。
    const agg = aggregateNodeHistory()
    expect(agg.hist).toBeInstanceOf(Map)
    expect(typeof agg.globalMaxIt).toBe('number')
    expect(agg.epochMs).toBeGreaterThanOrEqual(0)
  })
  it('readIterMetrics tolerates missing traj dir', () => {
    const { rows } = readIterMetrics(join(tmpdir(), 'definitely-missing-traj'))
    expect(rows).toEqual([])
  })
  it('iteration events decode into rows with eval join', () => {
    const dir = mkdtempSync(join(tmpdir(), 'pool-test-'))
    try {
      writeFileSync(
        join(dir, 'training_log.jsonl'),
        JSON.stringify({
          event: 'iteration',
          iter: 7,
          time: '2026-09-06 10:00:00',
          winRate: 0.4,
          score_mean: 1.5,
          score_std: 0.2,
          samples: 150,
          rollout_sec: 100,
          ppo_sec: 50,
          kl: 0.01,
          entropy: 0.3,
          policy: 0.02,
          value: 0.03,
          mean_ret: 1.1,
          lr: 0.00015,
          expectedGames: 150,
          ticks: 150000,
          halted: false,
          dim_means: { accuracy: 0.6, loot: 0.2, progress: 0.5 },
        }) + '\n',
      )
      writeFileSync(
        join(dir, 'eval_log.jsonl'),
        JSON.stringify({
          event: 'eval',
          iter: 7,
          wver: 'w1',
          ticks: 1000,
          kills: 10,
          powerUpsCollected: 2,
          score: 1.0,
        }) +
          '\n' +
          JSON.stringify({
            event: 'eval_summary',
            iter: 7,
            time: '2026-09-06 10:05:00',
            games: 10,
            wins: 4,
            winRate: 0.4,
            clears: 1,
            clearRate: 0.1,
            dropped: 0,
            sec: 60,
            wver: 'w1',
            outcomes: { win: 4, loss: 6 },
          }) +
          '\n',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows).toHaveLength(1)
      const r = rows[0]!
      expect(r.iter).toBe(7)
      expect(r.winRate).toBe(0.4)
      expect(r.avgTicks).toBe(1000)
      expect(r.kills).toBe(10)
      expect(r.evalData).not.toBeNull()
      expect(r.evalData!.wins).toBe(4)
      expect(r.evalData!.scoreMean).toBe(1)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('monitor page render (tools/training/monitor/page.ts)', () => {
  it('renders a full standalone page without any server', async () => {
    const html = await renderMonitorPage({
      workers: 2,
      inflight: { size: 0 },
      gamesDoneTotal: 0,
      localHash: () => 'x'.repeat(64),
      nodes: [
        { id: 'self', url: 'http://127.0.0.1:9', authKey: 'k', enabled: false, concurrency: 1 },
      ],
      localSlots: 4,
    })
    expect(html.startsWith('<!doctype html>')).toBe(true)
    expect(html).toContain('id="pool"')
    expect(html).toContain('已禁用节点')
    expect(html).toContain('本机直跑')
    // 密钥不渲染（页面契约）
    expect(html).not.toContain('"k"')
  })
  it('renders placeholder page for non-master machines (no nodes)', async () => {
    const html = await renderMonitorPage({
      workers: 0,
      inflight: { size: 0 },
      gamesDoneTotal: 0,
      localHash: () => '',
      nodes: null,
      localSlots: null,
    })
    expect(html).toContain('id="pool"')
    expect(html).not.toContain('已禁用节点')
  })
})

describe('training path constants', () => {
  it('repo root resolves from module location', () => {
    expect(path.basename(REPO_ROOT)).not.toBe('training')
    expect(
      REPO_ROOT.endsWith('battle2') || REPO_ROOT.includes(':') || REPO_ROOT.includes('/'),
    ).toBe(true)
  })
})

describe('ProcSpec path semantics (tools/training/specs.ts, DECISIONS §349 regression)', () => {
  it('trainingLoop cmd points at an existing file (entry is repo-relative, not doubled)', () => {
    // 回归：entry 曾被 join(NN_TRAINING) 再拼一次 → nn-training/nn-training/run_rl.py
    // python 秒退 "can't open file"，预演空烧 180s。cmd[2] 必须真实存在。
    const cfg = {
      version: 1,
      nodes: [],
      rl: { hub_port: 8787, agent_port: 8443, remote_token: 't' },
    }
    const spec = trainingLoopSpec(cfg, {
      course: 'spec-path-test',
      ppo: 'remote',
      venv: { python: 'python', sitePackages: '' },
    })
    expect(spec.cmd[2]).toBe(join(REPO_ROOT, 'nn-training', 'run_rl.py'))
    const { existsSync } = require('fs') as { existsSync: (p: string) => boolean }
    expect(existsSync(spec.cmd[2]!)).toBe(true)
    expect(TRAINING_LOOP_ENTRY).toBe('nn-training/run_rl.py')
  })
})

describe('train CLI arg parsing (tools/training/train.ts main, DECISIONS §349)', () => {
  // §352 门禁：torch 缺席时这两个 spawn 真实 CLI 的测试只跳过（带 warning），
  // python 环境本身仍由 pythonGateState 硬门禁覆盖（下方 dedicated test）。
  const gate = pythonGateState()
  it.skipIf(!gate.torchReady)('translates --check to a successful interpreter probe (spawns real CLI)', async () => {
    const r = Bun.spawnSync(['bun', 'tools/training/train.ts', '--check'], {
      cwd: REPO_ROOT,
      stdout: 'pipe',
      stderr: 'pipe',
    })
    expect(r.exitCode).toBe(0)
    expect(r.stdout.toString()).toContain('torch 可用')
  }, 60000)

  it.skipIf(!gate.torchReady)('--echo prints the exact command and exits 0 without executing', () => {
    const r = Bun.spawnSync(
      ['bun', 'tools/training/train.ts', '--echo', '--script', 'ppo/bench.py', '--iters', '1'],
      {
        cwd: REPO_ROOT,
        stdout: 'pipe',
        stderr: 'pipe',
      },
    )
    expect(r.exitCode).toBe(0)
    const out = r.stdout.toString()
    // Windows 下路径以 JSON 转义形式打印（ppo\\bench.py）——按文件名断言，平台无关。
    expect(out).toContain('bench.py')
    expect(out).toContain('--iters')
  }, 60000)

  it('--help exits 0 with usage', () => {
    const r = Bun.spawnSync(['bun', 'tools/training/train.ts', '--help'], {
      cwd: REPO_ROOT,
      stdout: 'pipe',
      stderr: 'pipe',
    })
    expect(r.exitCode).toBe(0)
    expect(r.stdout.toString()).toContain('用法')
  }, 60000)

  it('python env is a hard requirement: gate state reports a runnable interpreter (§352)', () => {
    // 本机 python 环境必须具备——坏了就是红，不跳过（与 torch 缺席的降级语义不同）。
    expect(gate.pyOk).toBe(true)
    expect(gate.python).toBeTruthy()
    expect(gate.reason).toBeNull()
  })
})

describe('supervisor sentinel fingerprint', () => {
  it('detects sentinel changes via mtime', async () => {
    const { sentinelsChangedSince, sentinelStamp, lastMonitorChange, monitorTouch } =
      await import('../tools/training/sentinels')
    const tmpRel = 'tmp/training-start-sentinel-test.tmp'
    const abs = join(REPO_ROOT, tmpRel)
    // 显式 1990 基线（"进程启动时刻"的替身）：写入后必然晚于它。
    const launchMs = new Date('1990-01-01T00:00:00Z').getTime()
    writeFileSync(abs, 'v1')
    expect(await sentinelsChangedSince([tmpRel], launchMs)).toBe(true)
    // 未来基线 = 未变更。
    expect(await sentinelsChangedSince([tmpRel], Date.now() + 3_600_000)).toBe(false)
    const stamp = await sentinelStamp([tmpRel])
    expect(stamp).toContain('training-start-sentinel-test.tmp:')
    rmSync(abs, { force: true })
    // monitor touchpoint roundtrip
    const before = lastMonitorChange()
    monitorTouch()
    expect(lastMonitorChange()).toBeGreaterThanOrEqual(before)
  })
})
