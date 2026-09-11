/** training-multi-course.test.ts — 多课程并行训练的互斥证伪测试。

 *  plan：`plan/multi-course-parallel-training.md`（P0-W1/W3/W4/W5）。
 *
 *  **P0 阶段本文件全部是红测试，以 `it.skip` 暂存**（P0 只落盘证伪测试，不改生产
 *  代码；`bun run check` 必须绿）。打开节奏：
 *    - W1 双课程 spec 隔离（端口 / 日志路径）→ P1a；
 *    - W3 加法校验 checkCapacity           → P1a（slots.ts 落盘）；
 *    - W4 spec 重建快照（逐字段一致）      → P1b（账本双条目）；
 *    - W5① hub_port 单一归宿               → P1a；
 *    - W5② Object.keys(reg) 单一归宿       → P1b。
 *
 *  纪律：断言里的容量/端口一律从真实 `rl-config.json` 读值推导，**不写死数字**
 *  （F-B2：分支间配置会漂，写死即谎言）。P1a 起把 `loadSlots()` 的计算 specifier
 *  换成 `../tools/training/slots` 的静态 import。
 */

import { describe, expect, it } from 'bun:test'
import { readdirSync, readFileSync, statSync } from 'fs'
import path from 'path'
import { CONFIG_PATH, REPO_ROOT } from '../tools/training/paths'
import { hubServerSpec, workerServeSpec } from '../tools/training/specs'
import type { RlConfig } from '../tools/training/types'

// ────────────────────────── rl-config 实测值（禁止写死） ──────────────────────────

function loadRealConfig(): RlConfig {
  return JSON.parse(readFileSync(CONFIG_PATH, 'utf-8')) as RlConfig
}

/** 裸机容量 = max(rl.workers, rl.local_slots)（plan §1.1「裸机容量」）。 */
function bareCapacity(cfg: RlConfig): number {
  const rl = cfg.rl as Record<string, unknown>
  return Math.max(Number(rl.workers ?? 0), Number(rl.local_slots ?? 0))
}

/** 双课程测试配置：两课各占一个槽位（§1.3 配置 schema）。 */
function dualCourseCfg(): RlConfig {
  const cfg = loadRealConfig()
  return {
    ...cfg,
    courses: {
      'course-a': { slot: 0, workers: 1, local_slots: 1 },
      'course-b': { slot: 1, workers: 1, local_slots: 1 },
    },
  } as unknown as RlConfig
}

function cmdPort(spec: { cmd: string[] }): number | null {
  for (let i = 0; i < spec.cmd.length; i++) {
    if (spec.cmd[i] === '--port') return Number(spec.cmd[i + 1])
  }
  return null
}

// ────────────────────────── P0 骨架：slots.ts 的契约（P1a 落盘） ──────────────────────────

interface SlotsModule {
  /** Σ max(workers, local_slots) ≤ capacity 判定（§1.1/§3.4）。 */
  checkCapacity: (
    courses: Record<string, { workers: number; local_slots: number }>,
    capacity: number,
  ) => { ok: boolean; used: number; capacity: number; over: string[] }
  /** per-course 锁文件名（§1.2）。 */
  lockName: (course: string, kind: 'run_rl' | 'train_loop') => string
}

/** P0 红线基准：slots.ts 随 P1a 落盘；计算 specifier 让 tsc 不在此刻静态解析。 */
async function loadSlots(): Promise<SlotsModule> {
  const spec = ['..', 'tools', 'training', 'slots'].join('/')
  return (await import(spec)) as SlotsModule
}

// ────────────────────────── W1：双课程 spec 隔离 ──────────────────────────

describe('W1 双课程 spec 隔离（P1a 打开）', () => {
  it.skip('两门课程的 hub-server 端口与日志路径互不相同', () => {
    const cfg = dualCourseCfg()
    const a = hubServerSpec(cfg, 'course-a')
    const b = hubServerSpec(cfg, 'course-b')
    expect(cmdPort(a)).not.toBeNull()
    expect(cmdPort(a)).not.toBe(cmdPort(b))
    expect(a.log).not.toBe(b.log)
  })

  it.skip('两门课程的 worker_server 端口与 work 目录互不相同', () => {
    const cfg = dualCourseCfg()
    const venv = { python: 'python', sitePackages: 'sp' }
    // P1a：workerServeSpec 第三参 = course（P0 阶段签名未定，先按目标契约调用）
    const specOf = workerServeSpec as unknown as (
      c: RlConfig,
      v: typeof venv,
      course: string,
    ) => { cmd: string[]; log: string }
    const a = specOf(cfg, venv, 'course-a')
    const b = specOf(cfg, venv, 'course-b')
    expect(cmdPort(a)).not.toBe(cmdPort(b))
    expect(a.log).not.toBe(b.log)
    const workDir = (spec: { cmd: string[] }) => {
      const i = spec.cmd.indexOf('--work')
      return i < 0 ? null : spec.cmd[i + 1]
    }
    expect(workDir(a)).not.toBe(workDir(b))
  })

  it.skip('两门课程的锁文件名互不相同（无课程沿用旧文件名，默认行为零变化）', async () => {
    const { lockName } = await loadSlots()
    expect(lockName('course-a', 'run_rl')).not.toBe(lockName('course-b', 'run_rl'))
    expect(lockName('course-a', 'train_loop')).not.toBe(lockName('course-b', 'train_loop'))
    expect(lockName('', 'run_rl')).toBe('.run_rl.lock')
    expect(lockName('', 'train_loop')).toBe('.train_loop.lock')
  })
})

// ────────────────────────── W3：加法校验（纯函数） ──────────────────────────

describe('W3 checkCapacity 加法校验（P1a 打开）', () => {
  it.skip('两课各占满裸机容量 → 超量拒绝，并点名超量课程', async () => {
    const { checkCapacity } = await loadSlots()
    const cap = bareCapacity(loadRealConfig())
    expect(cap).toBeGreaterThan(1)
    const r = checkCapacity(
      { a: { workers: cap, local_slots: 0 }, b: { workers: 0, local_slots: cap } },
      cap,
    )
    expect(r.ok).toBe(false)
    expect(r.used).toBe(2 * cap)
    expect(r.capacity).toBe(cap)
    expect(r.over.length).toBeGreaterThan(0)
  })

  it.skip('Σ max(workers, local_slots) 恰等于容量 → 放行（eff 取 max 再求和）', async () => {
    const { checkCapacity } = await loadSlots()
    const cap = bareCapacity(loadRealConfig())
    const r = checkCapacity({ a: { workers: cap, local_slots: 0 } }, cap)
    expect(r.ok).toBe(true)
    expect(r.used).toBe(cap)
    // 0 是合法值（语义 = 关闭该课本机直跑），不参与 eff
    const z = checkCapacity({ a: { workers: 0, local_slots: 0 } }, cap)
    expect(z.ok).toBe(true)
    expect(z.used).toBe(0)
  })
})

// ────────────────────────── W4：spec 重建快照（契约占位） ──────────────────────────

describe('W4 spec 重建逐字段一致（P1b 打开）', () => {
  it.skip('A 课 hub spec 重建与原 spec 逐字段一致（含 slot/jobRoot/port）', () => {
    // P1b 落地：账本按下式登记可重建字段，restartSpecFor 不再用全局 console-state 猜课程。
    //   saveCourseComponent('hubServer', 'course-a', { pid, course:'course-a', slot:0,
    //     entry, log, jobRoot: <tmp/course-a/remote-jobs> })
    //   restartSpecFor('hubServer', 'course-a') === hubServerSpec(cfg, 'course-a') 逐字段
    // 此处为 P0 占位（真实断言随 P1b 的 saveCourseComponent 一起落盘）。
    expect(true).toBe(true)
  })
})

// ────────────────────────── W5：grep 门禁（纯文本扫描） ──────────────────────────

/** tools/training/** 下全部 .ts/.tsx（排除 tests 与生成物）。 */
function trainingSources(): Array<{ rel: string; text: string }> {
  const root = path.join(REPO_ROOT, 'tools', 'training')
  const out: Array<{ rel: string; text: string }> = []
  const walk = (dir: string): void => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const abs = path.join(dir, ent.name)
      if (ent.isDirectory()) {
        if (ent.name === 'node_modules' || ent.name === 'data') continue
        walk(abs)
        continue
      }
      if (!/\.tsx?$/.test(ent.name)) continue
      out.push({
        rel: path.relative(root, abs).split(path.sep).join('/'),
        text: readFileSync(abs, 'utf-8'),
      })
    }
  }
  walk(root)
  return out
}

describe('W5 grep 门禁', () => {
  const srcs = trainingSources()

  it.skip('门禁① hub_port 唯一归宿 = slots.ts（+ types.ts 的字段声明）（P1a 打开）', () => {
    // 现状：specs/push/proc/hub/actions/api 六处各自算术端口（+1 / +2），多课时代
    // 每一处都是串线风险。P1a 起一律经 slots.ts 的槽位算术，base 只在那一处读。
    const allowed = new Set(['slots.ts', 'types.ts'])
    const offenders = srcs
      .filter((s) => /hub_port/.test(s.text) && !allowed.has(path.basename(s.rel)))
      .map((s) => s.rel)
    expect(offenders).toEqual([])
    // 派生端口不许手写偏移（必须走 portForSlot/slotPort 的 offset 参数）
    const handArith = srcs.filter((s) => /hub_port\s*\+\s*\d/.test(s.text)).map((s) => s.rel)
    expect(handArith).toEqual([])
  })

  it.skip('门禁② Object.keys(reg) 唯一归宿 = registry.ts（枚举走 registryComponents 三元组）（P1b 打开）', () => {
    const offenders = srcs
      .filter(
        (s) => /Object\.keys\(\s*reg\b/.test(s.text) && path.basename(s.rel) !== 'registry.ts',
      )
      .map((s) => s.rel)
    expect(offenders).toEqual([])
  })

  it('门禁自身有效：扫描确实覆盖到源码（防 glob 静默为空）', () => {
    expect(srcs.length).toBeGreaterThan(10)
    expect(srcs.some((s) => s.rel === 'specs.ts')).toBe(true)
    // 防止 walk 只扫到目录本身
    expect(
      srcs.every((s) => statSync(path.join(REPO_ROOT, 'tools', 'training', s.rel)).isFile()),
    ).toBe(true)
  })
})
