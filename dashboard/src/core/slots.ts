/** slots.ts — 多课程槽位算术 · 并发配额校验 · per-course 锁名（唯一聚合点）。

 *  plan：`plan/multi-course-parallel-training.md`（§1.1/§1.2/§1.3、§3.2、§3.4）。
 *
 *  为什么需要本模块：单课时代 `hub_port` / `hub_port + 1` / `hub_port + 2` 的算术散在
 *  specs / push / proc / hub / console 的七处手写；多课并行时「谁在哪个端口」必须只有
 *  一个答案，否则第二门课程必然与第一门撞端口。**除此模块外任何地方都不许再读
 *  `rl.hub_port` 或手写偏移**（`tests/training-multi-course.test.ts` 的门禁①守这条）。
 *
 *  槽位定义（§3.2）：`hub_port(slot) = base + slot*10`；+1 = cloudflared metrics；
 *  +2 = 本机伪 push（worker_server）。槽位 = **可并行课程数**（"slot 只是 push 口的历史
 *  命名空间"——hub/隧道自 2026-09-18 起是单实例，不再按课程占口）。
 *  缺省 = slot 0 = 旧单课端口，故**未配置 `courses` 时不改变任何现有行为**。
 */

import path from 'path'
import { NN_TRAINING } from './paths'
import { portListen } from './net'
import type { RlConfig } from './types'

/** 槽位数 = 可并行课程数上限（用户 2026-09-18 口径：**先设为 5**）。
 *
 *  R3-1（2026-09-19）：此前是 4 —— 而用户口径是 5 ⇒ 第 5 门课必然越界；叠加 `slotOf`
 *  的静默回落 0，第 5 门课会**静默**用第 1 门课的 push 端口（两门课的推送互相顶掉，
 *  只有日志里的端口冲突能看出来）。现在两头都堵：上限 5 + 越界响亮拒启（见 `slotOf`）。 */
export const SLOT_COUNT = 5
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

/** 单个槽位值的合法性（`undefined`/`null` = 未配置 ⇒ 合法；返回 null）。
 *
 *  未配置与非法是**两件事**：未配置 = 旧单课语义（回落 0，默认行为零变化）；非法 =
 *  用户写了越界/非整数的值 —— 那时回落 0 就是「静默顶到别的课头上」，必须拒。 */
function slotIssue(course: string, s: unknown): string | null {
  if (s === undefined || s === null) return null
  if (Number.isInteger(s) && (s as number) >= 0 && (s as number) < SLOT_COUNT) return null
  return (
    `courses.${course}.slot = ${JSON.stringify(s)} 非法（合法槽位 0..${SLOT_COUNT - 1}，` +
    `共 ${SLOT_COUNT} 个；只有未配置才回落 0）`
  )
}

/** 课程 → 槽位。未配置/`null` ⇒ 0（旧单课行为）；**配置了非法值 ⇒ 响亮抛错**。
 *
 *  R3-1（2026-09-19）：旧实现对越界值也静默回落 0 ⇒ 第 5 门课（`slot: 4`，上限当时 4）
 *  与第 1 门课撞同一个 push 端口，而且是**静默**的。现在这一类不存在：要么合法，要么
 *  当场报错点名课程 + 越界值 + 合法范围（plan §5 R3-1②）。 */
export function slotOf(cfg: RlConfig, course: string): number {
  const s = cfg.courses?.[course]?.slot
  const issue = slotIssue(course, s)
  if (issue) {
    throw new Error(
      `课程槽位非法: ${issue}——请改成一个空闲槽位（或提升 SLOT_COUNT）；` +
        '越界静默回落会与别的课程撞 push 端口（plan R3-1）',
    )
  }
  return s === undefined || s === null ? 0 : (s as number)
}

/** 配置级槽位守卫（`saveConfig` 的第二个守卫，R3-1）：非法槽位 + **显式重复槽位**。
 *
 *  重复 = 两门课配到同一个槽位 ⇒ 撞 push 端口（真正的病根，比单课越界更早发生）。
 *  只管**显式配置**的槽位：未配置 = 旧单课语义，不因「多门课都没配槽位」拒存（legacy
 *  单课配置根本没有 `courses` 块）。null = 通过（与 `capacityError` 同一契约）。 */
export function slotError(cfg: RlConfig): string | null {
  const courses = cfg.courses ?? {}
  const bad: string[] = []
  const bySlot = new Map<number, string[]>()
  for (const name of Object.keys(courses).sort()) {
    const s = courses[name]?.slot
    const issue = slotIssue(name, s)
    if (issue) {
      bad.push(issue)
      continue
    }
    if (s === undefined || s === null) continue
    const group = bySlot.get(s as number) ?? []
    group.push(name)
    bySlot.set(s as number, group)
  }
  const dupes = [...bySlot.entries()].filter(([, names]) => names.length > 1)
  if (!bad.length && !dupes.length) return null
  const parts: string[] = []
  if (bad.length) parts.push(`非法槽位: ${bad.join('；')}`)
  for (const [s, names] of dupes) {
    parts.push(`槽位 ${s} 被多门课占用: ${names.join(', ')}（会撞同一个 push 端口）`)
  }
  return `课程槽位配置有问题——${parts.join('；')}（plan R3-1）`
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

/** 全部槽位端口（`0..SLOT_COUNT-1` × {hub,metrics,push} + agent）——端口兜底清场的唯一清单。
 *
 *  随 `SLOT_COUNT` 自动扩容（不写死上限）：上限提高后旧槽位的口仍被清场覆盖。 */
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

export type LockKind = 'run_rl' | 'train_loop' | 'run_bc' | 'run_cluster'

/** per-course 单实例锁名；无课程沿用旧全局文件名（默认行为零变化，plan §0.5-4）。 */
export function lockName(course: string, kind: LockKind): string {
  const c = validateCourseName(course)
  return c ? `.${kind}.${c}.lock` : `.${kind}.lock`
}

/** 锁文件目录（默认 `nn-training/`——python 侧 `course_lock_path` 也锚在脚本目录）。
 *
 *  单测以 `BCITY_LOCKS_DIR` 重定向：锁文件**真的会改变控制台判据**（「另一份按课 runner
 *  在跑 ⇒ 拒开课」），而 `assertCourseExists` 又要求课程是真课程 ⇒ 测试若走真实派生路径
 *  就会往仓库 `nn-training/` 里写 `.run_rl.<真课程>.lock`。默认行为零变化，故**惰性**取值
 *  （与 `paths.ts` 里那几个 `BCITY_*` 重定向同一个规矩）。
 */
export function locksDir(): string {
  return process.env.BCITY_LOCKS_DIR ?? NN_TRAINING
}

/** 锁文件绝对路径（`locksDir()` 下；run_rl/train_loop 的锁都锚在脚本目录）。 */
export function lockPathFor(course: string, kind: LockKind): string {
  return path.join(locksDir(), lockName(course, kind))
}
