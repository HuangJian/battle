/** exit-watchdog.test.ts — §380 非正常退出日志（TrainingLoop 静默失败治理）单测。
 *
 * 覆盖：
 *  - nextExitFailures：两帧确认（死 pid 连续两轮才判定）、复活清零、error 幂等跳过；
 *  - recordExitFailure：标记文本（label/PID/日志尾）落盘 + save(error/exitAt) 记录。
 * 纯函数 + 注入 —— 不碰磁盘 registry / 不拉真实组件日志。
 *
 * 真实 io 的默认 save（saveComponent）会把条目写进 tmp/training-start/registry.json——
 * 必须先把账本重定向到临时目录（BCITY_REGISTRY_FILE），否则测试覆写线上账本、
 * 运行中的控制台会全部误报「已退出」（2026-09-09 事故根因）。
 */

import { afterAll, afterEach, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

// 模块级：测试账本重定向到临时目录（registry.ts 惰性读取 env，top-level 赋值即可生效）。
const REG_SCRATCH = mkdtempSync(path.join(os.tmpdir(), 'bcity-registry-'))
process.env.BCITY_REGISTRY_FILE = path.join(REG_SCRATCH, 'registry.json')
afterAll(() => {
  try {
    rmSync(REG_SCRATCH, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})
import {
  buildExitMarker,
  classifyExit,
  healRecoveredErrors,
  nextExitFailures,
  recentPlannedStop,
  recordExitFailure,
  specPort,
  tailNormalCompletion,
  type FailureLogIO,
} from '../tools/training/console/exit-watchdog'
import type { Component, ProcSpec, Registry, RegistryEntry } from '../tools/training/types'

// ────────────────────────── nextExitFailures：两帧锁 ──────────────────────────

function mkEntry(pid: number): RegistryEntry {
  return { pid }
}

describe('nextExitFailures（两帧确认）', () => {
  it('同一死 pid 连续两帧才判定退出；第一帧只记状态', () => {
    const reg: Registry = { trainingLoop: mkEntry(999) }
    const seen = new Set<Component>()
    expect(nextExitFailures(reg, seen, () => false)).toEqual([]) // 帧 1：仅记
    const hit = nextExitFailures(reg, seen, () => false) // 帧 2：确证
    expect(hit).toHaveLength(1)
    expect(hit[0]!.key).toBe('trainingLoop')
    expect(hit[0]!.entry.pid).toBe(999)
    expect(nextExitFailures(reg, seen, () => false)).toEqual([]) // 已消费，不再重复
  })

  it('进程存活时清零帧计数；之后死亡需重新两帧', () => {
    const reg: Registry = { trainingLoop: mkEntry(999) }
    const seen = new Set<Component>()
    expect(nextExitFailures(reg, seen, () => false)).toEqual([]) // 帧1（死）
    expect(nextExitFailures(reg, seen, () => true)).toEqual([]) // 复活 → 清帧
    expect(seen.size).toBe(0)
    expect(nextExitFailures(reg, seen, () => false)).toEqual([]) // 帧1（死）
    expect(nextExitFailures(reg, seen, () => false)).toHaveLength(1) // 帧2（确证）
  })

  it('条目已带 error（此前已记录）→ 跳过；两次轮询只确证无 error 的死 pid', () => {
    const seen = new Set<Component>()
    const reg: Registry = {
      trainingLoop: { pid: 1, error: '意外退出 (PID 1)' },
      hubServer: { pid: 2 },
    }
    expect(nextExitFailures(reg, seen, () => false)).toEqual([]) // 帧1：hubServer 仅记
    const hits = nextExitFailures(reg, seen, () => false) // 帧2：确证（error 的仍跳过）
    expect(hits).toHaveLength(1)
    expect(hits[0]!.key).toBe('hubServer')
  })
})

// ────────────────────────── recordExitFailure：标记 + 落盘 + save ──────────────────────────

describe('recordExitFailure', () => {
  let dir: string
  afterEach(() => {
    try {
      rmSync(dir, { recursive: true, force: true })
    } catch {
      /* noop */
    }
  })

  it('标记含 label/PID/日志尾，追加进日志文件，且 save 写入 error/exitAt', () => {
    dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-exit-watchdog-'))
    const logAbs = path.join(dir, 'training-loop.log')
    writeFileSync(logAbs, '[run_rl] boot...\n[run_rl] FileNotFoundError: bc 缺\n', 'utf-8')

    const appended: Array<[string, string]> = []
    const captured: { entry: RegistryEntry | null } = { entry: null }
    const io: FailureLogIO = {
      append: (p, t) => appended.push([p, t]),
      warnFn: () => {},
      save: (_k, e) => {
        captured.entry = e
      },
    }
    const tail = ['[run_rl] boot...', '[run_rl] FileNotFoundError: bc 缺']
    const marker = recordExitFailure(
      'trainingLoop',
      { pid: 4242, log: logAbs },
      logAbs,
      tail,
      io,
      '2026-09-08T05:00:00.000Z',
    )

    expect(marker).toContain('[console]')
    expect(marker).toContain('意外退出 (PID 4242)')
    expect(marker).toContain('FileNotFoundError: bc 缺') // 日志尾带出原因
    expect(appended).toHaveLength(1)
    expect(appended[0]![0]).toBe(logAbs)
    expect(appended[0]![1]).toContain('意外退出 (PID 4242)')
    expect(captured.entry?.error).toBe('意外退出 (PID 4242)')
    expect(captured.entry?.exitAt).toBe('2026-09-08T05:00:00.000Z')
  })

  it('真实 io：标记确实被 appendFileSync 写进日志文件尾部', () => {
    dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-exit-watchdog-'))
    const logAbs = path.join(dir, 'training-loop.log')
    writeFileSync(logAbs, 'before\n', 'utf-8')
    recordExitFailure(
      'trainingLoop',
      { pid: 7 },
      logAbs,
      ['cause line'],
      {},
      '2026-09-08T05:00:00.000Z',
    )
    const text = readFileSync(logAbs, 'utf-8')
    expect(text).toContain('before')
    expect(text).toContain('[console] 2026-09-08T05:00:00.000Z ')
    expect(text).toContain('意外退出 (PID 7)')
    expect(text).toContain('| cause line')
  })

  it('logAbs 为 null：不落盘也正常返回标记（组件无日志语义）', () => {
    const marker = recordExitFailure('hubServer', { pid: 1 }, null, [], {}, 'T')
    expect(marker).toContain('-')
  })
})

describe('buildExitMarker', () => {
  it('还原可读单块文案', () => {
    const m = buildExitMarker('trainingLoop', { pid: 5 }, ['a', 'b'], 'T1')
    expect(m).toContain('[console] T1 ')
    expect(m).toContain('意外退出 (PID 5)')
    expect(m).toContain('| a')
    expect(m).toContain('| b')
  })

  it('planned 传入 → 「已停车」而非「意外退出」', () => {
    const m = buildExitMarker('trainingLoop', { pid: 5 }, [], 'T1', 'REMEDIATE: 复诊')
    expect(m).toContain('已停车（PID 5）')
    expect(m).toContain('REMEDIATE: 复诊')
    expect(m).not.toContain('意外退出')
  })
})

// ────────────────────── 设计内停车识别（§385：门判决停车不标「意外退出」） ──────────────────────

/** "YYYY-MM-DD HH:mm:ss" 本地时间串（与 training_log.jsonl 事件 time 同格式）。 */
function localFmt(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

describe('recentPlannedStop（gate_verdict / circuit_break 识别）', () => {
  const NOW = new Date(2026, 8, 11, 10, 45, 20).getTime() // 本地 2026-09-11 10:45:20
  function jsonl(rows: Record<string, unknown>[]): string {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-planned-'))
    const j = path.join(dir, 'training_log.jsonl')
    writeFileSync(j, rows.map((r) => JSON.stringify(r)).join('\n') + '\n', 'utf-8')
    return j
  }

  it('近 300s 内 gate_verdict → 返回停车原因（设计内停车）', () => {
    const j = jsonl([
      { event: 'iteration', time: localFmt(NOW - 200_000), ppo_sec: 1 },
      {
        event: 'gate_verdict',
        time: localFmt(NOW - 5_000),
        verdict: 'REMEDIATE',
        reason: '腿在烧事故',
      },
    ])
    try {
      expect(recentPlannedStop(j, 300_000, NOW)).toContain('REMEDIATE')
      expect(recentPlannedStop(j, 300_000, NOW)).toContain('腿在烧事故')
    } finally {
      rmSync(path.dirname(j), { recursive: true, force: true })
    }
  })

  it('最近相关事件太旧（>300s）→ null（与本次退出无关，仍走意外路径）', () => {
    const j = jsonl([
      { event: 'gate_verdict', time: localFmt(NOW - 400_000), verdict: 'STOP', reason: '旧判决' },
    ])
    try {
      expect(recentPlannedStop(j, 300_000, NOW)).toBeNull()
    } finally {
      rmSync(path.dirname(j), { recursive: true, force: true })
    }
  })

  it('circuit_break 也判为设计内停车；文件缺失 → null', () => {
    const j = jsonl([
      { event: 'circuit_break', time: localFmt(NOW - 1_000), reason: 'KL collapse' },
    ])
    try {
      expect(recentPlannedStop(j, 300_000, NOW)).toContain('ABORT')
      expect(recentPlannedStop(path.join(os.tmpdir(), 'no-such.jsonl'), 300_000, NOW)).toBeNull()
    } finally {
      rmSync(path.dirname(j), { recursive: true, force: true })
    }
  })
})

describe('tailNormalCompletion（iters 跑满 ALL DONE 识别）', () => {
  it('日志尾行是 ALL DONE → 返回正常完成原因（2026-09-12 c5-ent it80 误报事故）', () => {
    const tail = [
      '[eval] it80 DONE wver=b922935d810f clean winRate=37.0% (37/100, dropped=0)',
      '[run_rl] metrics_stats it80: shards=148 steps=16757 elapsed_ms=62.9',
      '[run_rl] ALL DONE -> tmp/c5-ent/weights.json',
    ]
    expect(tailNormalCompletion(tail)).toContain('正常完成')
    expect(tailNormalCompletion(tail)).toContain('ALL DONE')
  })

  it('无 ALL DONE → null（崩溃仍走意外路径）', () => {
    expect(tailNormalCompletion(['[run_rl] boot...', 'FileNotFoundError: bc 缺'])).toBeNull()
    expect(tailNormalCompletion([])).toBeNull()
  })

  it('ALL DONE 不在尾行（旧轮残留、之后还有输出）→ null（不得把新崩溃洗成完成）', () => {
    const tail = ['[run_rl] ALL DONE -> tmp/x/weights.json', '[run_rl] boot...', 'Traceback: boom']
    expect(tailNormalCompletion(tail)).toBeNull()
  })

  it('原因进 buildExitMarker → 「已停车」而非「意外退出」', () => {
    const reason = tailNormalCompletion(['[run_rl] ALL DONE -> tmp/c5-ent/weights.json'])
    expect(reason).not.toBeNull()
    const m = buildExitMarker('trainingLoop', { pid: 28112 }, [], 'T1', reason)
    expect(m).toContain('已停车（PID 28112）')
    expect(m).not.toContain('意外退出')
  })
})

describe('recordExitFailure（设计内停车）', () => {
  it('planned 传入 → 标记「已停车」且 save.error 以前缀「已停车」写账', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-watchdog-planned-'))
    try {
      const logAbs = path.join(dir, 'training-loop.log')
      writeFileSync(logAbs, 'boot\n', 'utf-8')
      const captured: { entry: RegistryEntry | null } = { entry: null }
      const io: FailureLogIO = {
        append: () => {},
        warnFn: () => {},
        save: (_k, e) => {
          captured.entry = e
        },
      }
      const marker = recordExitFailure(
        'trainingLoop',
        { pid: 99 },
        logAbs,
        [],
        io,
        '2026-09-11T02:45:20.000Z',
        'REMEDIATE: 有效训练占空比过低，复诊',
      )
      expect(marker).toContain('已停车（PID 99）')
      expect(marker).not.toContain('意外退出')
      expect(captured.entry?.error).toBe('已停车：REMEDIATE: 有效训练占空比过低，复诊')
      expect(captured.entry?.exitAt).toBe('2026-09-11T02:45:20.000Z')
    } finally {
      try {
        rmSync(dir, { recursive: true, force: true })
      } catch {
        /* noop */
      }
    }
  })
})

// ────────────────────── 进程换代护栏（2026-09-09 selfNode 误报事故） ──────────────────────

function mkSpec(cmd: string[]): ProcSpec {
  return {
    key: 'selfNode',
    name: 'self-node',
    cmd,
    log: 'l',
    healthy: async () => true,
  } as ProcSpec
}

describe('specPort', () => {
  it('--port N / --port=N / 无端口 / 无 spec', () => {
    expect(specPort(mkSpec(['bun', 'run', 'a.ts', '--port', '8443']))).toBe(8443)
    expect(specPort(mkSpec(['bun', 'a.ts', '--port=8787']))).toBe(8787)
    expect(specPort(mkSpec(['bun', 'a.ts']))).toBeNull()
    expect(specPort(null)).toBeNull()
  })
})

describe('classifyExit', () => {
  it('服务仍在应答 → alive：修正账本 pid，不写失败标记', async () => {
    const repaired: Array<[Component, number]> = []
    const v = await classifyExit(
      'selfNode',
      { pid: 8100 },
      {
        healthyOf: async () => true,
        ownerPidOf: () => 30332,
        repair: (k, _e, p) => repaired.push([k, p]),
        warnFn: () => {},
      },
    )
    expect(v).toBe('alive')
    expect(repaired).toEqual([['selfNode', 30332]])
  })

  it('健康检查不通 → exited（走原失败记录路径）', async () => {
    let repaired = 0
    const v = await classifyExit(
      'selfNode',
      { pid: 8100 },
      {
        healthyOf: async () => false,
        ownerPidOf: () => 30332,
        repair: () => {
          repaired++
        },
        warnFn: () => {},
      },
    )
    expect(v).toBe('exited')
    expect(repaired).toBe(0)
  })

  it('健康但拿不到新 pid → 仍 alive（跳过标记，不改账本）', async () => {
    let repaired = 0
    const v = await classifyExit(
      'selfNode',
      { pid: 8100 },
      {
        healthyOf: async () => true,
        ownerPidOf: () => null,
        repair: () => {
          repaired++
        },
        warnFn: () => {},
      },
    )
    expect(v).toBe('alive')
    expect(repaired).toBe(0)
  })

  it('健康检查抛异常 → exited（探测失败不得漏记真退出）', async () => {
    const v = await classifyExit(
      'selfNode',
      { pid: 8100 },
      {
        healthyOf: async () => {
          throw new Error('boom')
        },
        warnFn: () => {},
      },
    )
    expect(v).toBe('exited')
  })
})

describe('healRecoveredErrors（误报自愈）', () => {
  it('带 error 且服务健康 → 清 error/exitAt + 修 pid', async () => {
    const saved: Array<[Component, RegistryEntry]> = []
    const healed = await healRecoveredErrors(
      { selfNode: { pid: 8100, error: '意外退出 (PID 8100)', exitAt: 'T0' } },
      {
        healthyOf: async () => true,
        ownerPidOf: () => 30332,
        save: (k, e) => saved.push([k, e]),
        warnFn: () => {},
      },
    )
    expect(healed).toEqual(['selfNode'])
    expect(saved[0]![1].pid).toBe(30332)
    expect(saved[0]![1].error).toBeUndefined()
    expect(saved[0]![1].exitAt).toBeUndefined()
  })

  it('服务不通 → 保留 error（真退出不被误清）', async () => {
    const saved: Array<[Component, RegistryEntry]> = []
    const healed = await healRecoveredErrors(
      { trainingLoop: { pid: 1, error: '意外退出 (PID 1)' } },
      { healthyOf: async () => false, save: (k, e) => saved.push([k, e]), warnFn: () => {} },
    )
    expect(healed).toEqual([])
    expect(saved).toHaveLength(0)
  })

  it('无 error 的条目不动', async () => {
    const saved: Array<[Component, RegistryEntry]> = []
    const healed = await healRecoveredErrors(
      { selfNode: { pid: 30332 } },
      { healthyOf: async () => true, save: (k, e) => saved.push([k, e]), warnFn: () => {} },
    )
    expect(healed).toEqual([])
    expect(saved).toHaveLength(0)
  })
})
