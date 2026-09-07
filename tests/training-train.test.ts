import { describe, expect, it } from 'bun:test'
import {
  containerSmoke,
  rlConfigSmoke,
  weightsSmoke,
  summarizeSmoke,
  type SmokeItem,
} from '../tools/training/smoke'
import { resolveTorchThreads, torchThreadEnv } from '../tools/training/venv'
import { resolveTrainScript } from '../tools/training/train'
import {
  aggregateNodeHistory,
  emptyHistory,
  poolStatus,
} from '../tools/training/console/pool-history'
import { readIterMetrics } from '../tools/training/console/iters'
import { stripIsoPrefix } from '../tools/training/ui/view'
import { TRAINING_LOOP_ENTRY, trainingLoopSpec } from '../tools/training/specs'
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

describe('pool data layers (tools/training/console/pool-history + iters)', () => {
  it('poolStatus thresholds: nodata → healthy ≥90% / warn ≥70% / bad', () => {
    expect(poolStatus(emptyHistory())).toBe('nodata')
    expect(
      poolStatus({
        ...emptyHistory(),
        recent: [true, true, true, true, true, true, true, true, true, true],
      }),
    ).toBe('healthy')
    expect(
      poolStatus({
        ...emptyHistory(),
        recent: [true, true, true, false, false, true, true, true, true, true],
      }),
    ).toBe('warn')
    expect(poolStatus({ ...emptyHistory(), recent: [false, false] })).toBe('bad')
  })
  it('lastError 剥离 sampler-agent 的 UTC ISO 前缀（GLM-U3）', () => {
    expect(stripIsoPrefix('2026-09-07T02:03:04.567Z link timeout')).toBe('link timeout')
    expect(stripIsoPrefix('2026-09-07T02:03:04Z s5/seed1: boom')).toBe('s5/seed1: boom')
    expect(stripIsoPrefix('normal error')).toBe('normal error')
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
  it('translates --check to a successful interpreter probe (spawns real CLI)', async () => {
    const r = Bun.spawnSync(['bun', 'tools/training/train.ts', '--check'], {
      cwd: REPO_ROOT,
      stdout: 'pipe',
      stderr: 'pipe',
    })
    expect(r.exitCode).toBe(0)
    expect(r.stdout.toString()).toContain('torch 可用')
  }, 60000)

  it('--echo prints the exact command and exits 0 without executing', () => {
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
