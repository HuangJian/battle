/**
 * trainer-heartbeat.test.ts — 共享 trainer 心跳的读面与判据（plan/single-resident-trainer §2）。
 *
 * 判据只有三个判决，而**误杀比漏报贵**（杀一个正在写盘/等 PPO 的活进程 = 重跑一轮）：
 *   · fresh  —— 文件在、pid 对得上、ts 在窗口内；
 *   · stale  —— 同上但超窗（这才允许告警/人工处置）；
 *   · unknown——判不了就不判：文件缺失 / pid 对不上（冒烟预演占同一槽）/ 判据上线前启动的进程 /
 *               启动宽限。
 * 本文件把那四条「不可知」逐条钉住（它们才是真正的防误杀面）。
 */

import { describe, expect, it } from 'bun:test'
import { mkdtempSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import {
  HEARTBEAT_SINCE_MS,
  heartbeatVerdict,
  readHeartbeat,
  resolveTrainerHeartbeat,
} from '../src/core/trainer-heartbeat'

const NOW = 1_800_000_000_000 // ms
const nowSec = Math.floor(NOW / 1000)

function withFile(content: string | null): string {
  const dir = mkdtempSync(join(tmpdir(), 'hb-'))
  const p = join(dir, 'trainer-heartbeat.json')
  if (content !== null) writeFileSync(p, content)
  return p
}

const hb = (over: Record<string, unknown> = {}): string =>
  JSON.stringify({
    pid: 4242,
    ts: nowSec - 3,
    steps: 17,
    waiting: 1,
    courses: { c1: { state: 'WAIT_EXTERNAL', rounds_done: 3, next_it: 4 } },
    ...over,
  })

const entry = { pid: 4242, startedAt: NOW - 10 * 60 * 1000 }

describe('心跳判据（core/trainer-heartbeat）', () => {
  it('新鲜 ⇒ fresh（含 steps 不涨但 ts 新鲜：真在等外部）', () => {
    expect(heartbeatVerdict(entry, { pid: 4242, ts: nowSec - 3 }, NOW)).toBe('fresh')
    // 等外部等 20 分钟：ts 一直被写线程刷新 ⇒ 仍 fresh（评审 P0-2 的护栏 2）
    expect(heartbeatVerdict(entry, { pid: 4242, ts: nowSec - 3, steps: 17 }, NOW)).toBe('fresh')
  })

  it('超窗 ⇒ stale（唯一允许告警的判决）', () => {
    expect(heartbeatVerdict(entry, { pid: 4242, ts: nowSec - 301 }, NOW)).toBe('stale')
    expect(heartbeatVerdict(entry, { pid: 4242, ts: nowSec - 300 }, NOW)).toBe('fresh')
  })

  it('pid 对不上 ⇒ unknown（冒烟预演占同一个 trainingLoop 槽）', () => {
    expect(heartbeatVerdict(entry, { pid: 9999, ts: nowSec - 3 }, NOW)).toBe('unknown')
  })

  it('文件缺失：判据上线前启动 ⇒ unknown；上线后启动且过了宽限 ⇒ stale；宽限内 ⇒ unknown', () => {
    const legacy = { pid: 4242, startedAt: HEARTBEAT_SINCE_MS - 60_000 }
    expect(heartbeatVerdict(legacy, null, NOW)).toBe('unknown')
    const freshStart = { pid: 4242, startedAt: HEARTBEAT_SINCE_MS + 60_000 }
    expect(heartbeatVerdict(freshStart, null, NOW)).toBe('stale')
    const justStarted = { pid: 4242, startedAt: NOW - 30_000 }
    expect(heartbeatVerdict(justStarted, null, NOW)).toBe('unknown')
  })

  it('旧条目没有启动时刻 ⇒ unknown（不拿历史条目猜）', () => {
    expect(heartbeatVerdict({ pid: 4242 }, null, NOW)).toBe('unknown')
    expect(heartbeatVerdict(undefined, null, NOW)).toBe('unknown')
  })
})

describe('心跳读面（core/trainer-heartbeat）', () => {
  it('坏 JSON / 非对象 / 缺 pid-ts ⇒ null（不可知 ≠ 有病）', () => {
    expect(readHeartbeat(withFile(null))).toBeNull()
    expect(readHeartbeat(withFile('{ not json'))).toBeNull()
    expect(readHeartbeat(withFile('[1,2]'))).toBeNull()
    expect(readHeartbeat(withFile('{"pid": "x", "ts": 1}'))).toBeNull()
  })

  it('resolve：fresh/stale 投影出 ageSec 与各课状态；unknown ⇒ null', () => {
    const fresh = resolveTrainerHeartbeat({ path: withFile(hb()), now: NOW, entry })
    expect(fresh).not.toBeNull()
    expect(fresh!.verdict).toBe('fresh')
    expect(fresh!.ageSec).toBe(3)
    expect(fresh!.courses.c1?.state).toBe('WAIT_EXTERNAL')
    expect(fresh!.waiting).toBe(1)

    const stale = resolveTrainerHeartbeat({
      path: withFile(hb({ ts: nowSec - 900 })),
      now: NOW,
      entry,
    })
    expect(stale!.verdict).toBe('stale')
    expect(stale!.ageSec).toBe(900)

    expect(resolveTrainerHeartbeat({ path: withFile(hb({ pid: 1 })), now: NOW, entry })).toBeNull()
    expect(resolveTrainerHeartbeat({ path: withFile(null), now: NOW, entry })).toBeNull()
  })
})

describe('接线：trainerServeSpec.healthy 用心跳判（plan §2.2/§2.4）', () => {
  /** 真账本 + 真心跳文件（`heartbeatAlive` 自己 `Date.now()`，没有 now 注入 ⇒ 用相对时间）。
   *  registry 走 `BCITY_REGISTRY_FILE`、心跳走 `BCITY_TRAINER_HEARTBEAT`（paths.ts 惰性取值）。 */
  function sandbox(opts: { hbTsSecAgo: number | null; hbPid?: number }) {
    const dir = mkdtempSync(join(tmpdir(), 'hb-wire-'))
    const reg = join(dir, 'registry.json')
    writeFileSync(
      reg,
      JSON.stringify({
        trainingLoops: { '': { pid: process.pid, startedAt: Date.now() - 3600_000 } },
      }),
    )
    const hbPath = join(dir, 'trainer-heartbeat.json')
    if (opts.hbTsSecAgo !== null) {
      writeFileSync(
        hbPath,
        JSON.stringify({
          pid: opts.hbPid ?? process.pid,
          ts: Math.floor(Date.now() / 1000) - opts.hbTsSecAgo,
        }),
      )
    }
    process.env.BCITY_REGISTRY_FILE = reg
    process.env.BCITY_TRAINER_HEARTBEAT = hbPath
  }
  function restore(): void {
    delete process.env.BCITY_REGISTRY_FILE
    delete process.env.BCITY_TRAINER_HEARTBEAT
  }

  it('新鲜 ⇒ 健康；陈旧 ⇒ 不健康（serve 卡死时进程还在，旧判据照绿）', async () => {
    const { TRAINER_SERVE_ENTRY, trainerServeSpec } = await import('../src/stack/specs')
    expect(TRAINER_SERVE_ENTRY).toContain('run_rl_cluster.py')
    const spec = trainerServeSpec({} as never, { python: '/usr/bin/python3', sitePackages: '/sp' })
    try {
      sandbox({ hbTsSecAgo: 2 })
      expect(await spec.healthy!()).toBe(true)
      restore()
      sandbox({ hbTsSecAgo: 600 })
      expect(await spec.healthy!()).toBe(false)
    } finally {
      restore()
    }
  })

  it('不可知一律不判死：无心跳文件 / pid 对不上（冒烟预演占同一槽）', async () => {
    const { trainerServeSpec } = await import('../src/stack/specs')
    const spec = trainerServeSpec({} as never, { python: '/usr/bin/python3', sitePackages: '/sp' })
    try {
      sandbox({ hbTsSecAgo: null })
      expect(await spec.healthy!()).toBe(true)
      restore()
      sandbox({ hbTsSecAgo: 900, hbPid: 999_999 })
      expect(await spec.healthy!()).toBe(true)
    } finally {
      restore()
    }
  })
})
