/** slots.ts — 多课程槽位算术 · 并发配额校验 · per-course 锁名（唯一聚合点）。

 *  plan：`plan/multi-course-parallel-training.md`（§1.1/§1.2/§1.3、§3.2、§3.4）。
 *
 *  为什么需要本模块：单课时代 `hub_port` / `hub_port + 1` / `hub_port + 2` 的算术散在
 *  specs / push / proc / hub / console 的七处手写；多课并行时「谁在哪个端口」必须只有
 *  一个答案，否则第二门课程必然与第一门撞端口。**除此模块外任何地方都不许再读
 *  `rl.hub_port` 或手写偏移**（`tests/training-multi-course.test.ts` 的门禁①守这条）。
 *
 *  槽位定义（§3.2）：`hub_port(slot) = base + slot*10`；+1 = cloudflared metrics；
 *  +2 = 本机伪 push（worker_server）。槽位 0–3（常态 2 课，4 槽留余量）。
 *  缺省 = slot 0 = 旧单课端口，故**未配置 `courses` 时不改变任何现有行为**。
 */

import path from 'path'
import { NN_TRAINING } from './paths'
import { portListen } from './net'
import type { RlConfig } from './types'

/** 槽位数（§0.4 已定案：常态 2 课、上限 4）。 */
export const SLOT_COUNT = 4
/** 槽位端口步长（一个槽位吃掉 base 起 10 个端口：hub/+1/+2，余量留人工进程）。 */
export const SLOT_STRIDE = 10

/** 派生端口的相对偏移（唯一事实来源：调用方永远传 kind，不传数字）。 */
export type PortKind = 'hub' | 'metrics' | 'push'
export const PORT_OFFSET: Record<PortKind, number> = { hub: 0, metrics: 1, push: 2 }

/** hub 端口基数——**全仓唯一的 `rl.hub_port` 读取点**（其余一律经 slotPort）。 */
export function hubBasePort(cfg: RlConfig): number {
  return Number(cfg.rl?.hub_port ?? 0)
}

/** 槽位端口纯算术：`base + slot*stride + offset`。 */
export function portForSlot(base: number, slot: number, offset = 0): number {
  return base + slot * SLOT_STRIDE + offset
}

/** 课程 → 槽位（未配置/越界 → 0 = 旧单课行为，绝不静默顶替到别的槽位）。 */
export function slotOf(cfg: RlConfig, course: string): number {
  const s = cfg.courses?.[course]?.slot
  return Number.isInteger(s) && (s as number) >= 0 && (s as number) < SLOT_COUNT ? (s as number) : 0
}

/** 槽位端口：按课程（或显式槽位）+ 用途取端口。唯一的调用面。 */
export function slotPort(
  cfg: RlConfig,
  courseOrSlot: string | number,
  kind: PortKind = 'hub',
): number {
  const slot = typeof courseOrSlot === 'number' ? courseOrSlot : slotOf(cfg, courseOrSlot)
  return portForSlot(hubBasePort(cfg), slot, PORT_OFFSET[kind])
}

/** 共享 hub 端口 = `rl.hub_port` 基数**本身**（= 槽位 0 的 hub 端口）。
 *
 *  2026-09-18 起 hub/隧道收敛为**单实例**（一个 hub 进程服务所有并行课程、一条隧道
 *  指向它）：hub 不再按课程占端口，`hub` 这个 kind 只剩这一个地址，任何「按课程推 hub
 *  端口」都是错的（课程槽位现在只决定 push 端口与旧的 metrics 命）。全部调用者一律走
 *  本函数 / `sharedHubUrl`，门禁（`single-hub-tunnel.test.ts`）守这一点。 */
export function sharedHubPort(cfg: RlConfig): number {
  return hubBasePort(cfg)
}

/** 共享 hub 的基址（`local`/`pull` 预设、健康探测、冒烟、halt 达令共用一份口径）。 */
export function sharedHubUrl(cfg: RlConfig, host = '127.0.0.1'): string {
  return `http://${host}:${sharedHubPort(cfg)}`
}

/** 共享**单**隧道的 metrics 端口（= 槽位 0 的 metrics 口；cloudflared `--metrics` 与
 *  `/ready` 探测共用，且旧 per-course 隧道被杀后那个口就空出来了）。 */
export function sharedTunnelMetricsPort(cfg: RlConfig): number {
  return portForSlot(hubBasePort(cfg), 0, PORT_OFFSET.metrics)
}

/** 全部槽位端口（slot0–3 × {hub,metrics,push} + agent）——端口兜底清场的唯一清单。 */
export function allSlotPorts(cfg: RlConfig): number[] {
  const ports: number[] = []
  for (let s = 0; s < SLOT_COUNT; s++) {
    for (const kind of ['hub', 'metrics', 'push'] as PortKind[]) {
      ports.push(slotPort(cfg, s, kind))
    }
  }
  ports.push(Number(cfg.rl?.agent_port ?? 0))
  return ports
}

// ────────────────────────── 并发配额（加法校验） ──────────────────────────

/** 课程的本机并发配额（§1.3，住 `rl-config.json` 的 `courses` 块）。 */
export interface CourseQuota {
  workers?: number
  local_slots?: number
}

export interface CapacityReport {
  ok: boolean
  /** Σ eff（eff = max(workers, local_slots)）。 */
  used: number
  capacity: number
  /** 使累计量越过容量的课程名（点名，不静默顶替）。 */
  over: string[]
}

/** 本机直跑并发总量校验（纯函数，§3.4）。

 *  `eff(course) = max(workers_c, local_slots_c)`（孵化式 dispatch 的真实并发），
 *  `Σ eff ≤ 裸机容量 max(rl.workers, rl.local_slots)`。超载而非饥饿——两个半量的课程
 *  相加会直接把对局进程数翻倍，表现为 `rollout_sec` 全线恶化。
 *  0 是合法值（语义 = 关闭该课本机直跑），照常参与（eff 为 0）。
 */
export function checkCapacity(
  courses: Record<string, CourseQuota>,
  capacity: number,
): CapacityReport {
  const num = (v: unknown): number => {
    const n = Number(v)
    return Number.isFinite(n) && n > 0 ? n : 0
  }
  let used = 0
  const over: string[] = []
  // 课程名排序 → 报告与判定顺序稳定（同一份配置永远给同一份诊断）
  for (const name of Object.keys(courses).sort()) {
    const q = courses[name] ?? {}
    used += Math.max(num(q.workers), num(q.local_slots))
    if (used > capacity) over.push(name)
  }
  return { ok: over.length === 0, used, capacity, over }
}

/** 裸机容量 = max(rl.workers, rl.local_slots)（§1.1「裸机容量」）。 */
export function bareCapacity(cfg: RlConfig): number {
  const rl = cfg.rl as Record<string, unknown>
  return Math.max(Number(rl.workers ?? 0), Number(rl.local_slots ?? 0))
}

/** 配置整体容量校验文本（fail-fast；null = 通过）。
 *
 *  `saveConfig` 的单一守卫（plan P4-W1「控制台保存路径接 checkCapacity」）：
 *  任何把 `courses` 块写回 rl-config 的路径都不得把超量配额落到磁盘。点名超量课程，
 *  不静默顶替；附「配额只住 courses 块，勿写 curricula」的 C1 提醒。
 */
export function capacityError(cfg: RlConfig): string | null {
  const rep = checkCapacity(cfg.courses ?? {}, bareCapacity(cfg))
  if (rep.ok) return null
  return (
    `本机并发配额超量: Σeff=${rep.used} > 容量 ${rep.capacity}` +
    `（超量课程: ${rep.over.join(', ')}）——请下调 courses.<课>.workers/local_slots；` +
    '配额只住 rl-config 的 courses 块，勿写进 curricula（plan §3.4/C1）'
  )
}

// ────────────────────────── 槽位分配 ──────────────────────────

/** 分配一个空闲槽位：跳过 `taken` 与**端口已被占用**的槽位；无可用 → null（fail-fast）。 */
export async function allocateSlot(
  cfg: RlConfig,
  taken: Iterable<number> = [],
  probe: (port: number) => Promise<boolean> = portListen,
): Promise<number | null> {
  const used = new Set(taken)
  for (let s = 0; s < SLOT_COUNT; s++) {
    if (used.has(s)) continue
    const ports = (['hub', 'metrics', 'push'] as PortKind[]).map((k) => slotPort(cfg, s, k))
    const busy = await Promise.all(ports.map((p) => probe(p)))
    if (!busy.some(Boolean)) return s
  }
  return null
}

// ────────────────────────── 课程名与锁文件名 ──────────────────────────

/** 课程名合法字符集（与 `api.sanitizeViewCourse` 同约束；另禁 `..` 防穿越）。 */
const COURSE_NAME_RE = /^[A-Za-z0-9._-]+$/

/** 校验课程名（运行时命名空间 + 锁文件名的安全前提）。空串 = 无课程（合法）。 */
export function validateCourseName(course: string): string {
  const c = String(course ?? '')
  if (!c) return ''
  if (c.includes('..') || !COURSE_NAME_RE.test(c)) {
    throw new Error(
      `课程名非法: ${JSON.stringify(c)}——只允许 [A-Za-z0-9._-] 且不含 '..'（plan §1.1）`,
    )
  }
  return c
}

export type LockKind = 'run_rl' | 'train_loop' | 'run_bc'

/** per-course 单实例锁名；无课程沿用旧全局文件名（默认行为零变化，plan §0.5-4）。 */
export function lockName(course: string, kind: LockKind): string {
  const c = validateCourseName(course)
  return c ? `.${kind}.${c}.lock` : `.${kind}.lock`
}

/** 锁文件绝对路径（nn-training/ 下；run_rl/train_loop 的锁都锚在脚本目录）。 */
export function lockPathFor(course: string, kind: LockKind): string {
  return path.join(NN_TRAINING, lockName(course, kind))
}
