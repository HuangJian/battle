/**
 * idle-stop.ts — 云机 worker 页的「没人要活就别继续烧配额」判决。
 *
 * 用户口径（2026-11-13）：「云机 worker 启动或者完成课程训练后，如果 hub 不可达或者没有待训练任务，
 * 则自动关闭对话节省配额。」本模块只做**判决**，停会话由调用方执行（面板倒计时 → 站点适配器）。
 *
 * 两个触发点由同一拍检查覆盖，判据只有 hub 事实，**不猜 cell 状态**：
 *   · worker 启动 = 页面开（`poll()` 从第一拍就在问）；
 *   · 训练完成 = 队列静默（`pendingUnheld=0` ∧ `inflight=0`，自主盘再加 `heldLive=0`，
 *     持续 `IDLE_WINDOW_MS`）。
 *
 * 判据（全部来自 dashboard 的 `/api/hubQueue`，页面本身不碰 hub / token）：
 *   · `pendingUnheld=0 ∧ inflight=0` 连续 `IDLE_WINDOW_MS` ⇒ `empty`。**在飞也算活**：本机正在算
 *     的那份活同样是 inflight，缺了它就会把「正在训练」读成「没活」而掐掉自己的 job；
 *   · **自主盘**（`battle.offline.ipynb`）另有一条：整课在跑时 rollout/ppo 全在本机、hub 队列
 *     完全静默，只有课程的 live hold 能证明「正在训练」⇒ 自主盘把 `heldLive>0` 也算活；
 *     协作盘领不到 live hold 压住的课，那些 `pending` 对它不算活（用 `pendingUnheld`）。
 *   · 连续 `UNREACHABLE_ATTEMPTS` 拍问不到（dashboard 或 hub 不可达）⇒ `unreachable`；
 *   · 有活 / 开关关掉 ⇒ 计窗清零（`reset()` 手动复位，供「取消」后重新武装）。
 *
 * 为什么计窗而不是「一拍就停」：一轮训练结束后训练侧往往隔几分钟才发下一轮 job，一拍就停会把
 * 云机会话在两次派发之间掐掉（下一轮要重新排队起 worker）。默认窗口 10 分钟 + 停前 60s 倒计时。
 */
import type { Runtime } from '../../runtime'

/** 一拍间隔（60s：一次 `/api/hubQueue` 探测；偷懒也算得过来，hub 那边 1.2s 超时）。 */
export const IDLE_POLL_MS = 60_000
/** 队列静默多久才判「没有待训练任务」（默认 10 分钟）。 */
export const IDLE_WINDOW_MS = 10 * 60_000
/** 连续几拍问不到才判「hub 不可达」（默认 3 拍 ≈ 3 分钟，避免一次网络抖动就停会话）。 */
export const UNREACHABLE_ATTEMPTS = 3
/** 停会话前的倒计时秒数（可取消）。 */
export const IDLE_CONFIRM_SEC = 60
/** `/api/hubQueue` 的请求超时。 */
export const HUB_QUEUE_TIMEOUT_MS = 5000

export interface HubQueueFacts {
  /** 全部课程合计的可领取 job 数（含被 live hold 压住的课）。 */
  pending: number
  /** 全部课程合计的在飞条数。 */
  inflight: number
  /** 未被 live hold 压住的课里的可领取 job 数（协作盘真能领的那部分）。 */
  pendingUnheld: number
  /** 有 live hold 的课程数（自主盘整课在跑的唯一队列侧证据）。 */
  heldLive: number
}

export interface IdleStopDue {
  /** `unreachable` = 问不到 hub；`empty` = hub 上确实没有活。 */
  kind: 'unreachable' | 'empty'
  /** 一句话证据（写进 op 日志 / 面板状态栏）。 */
  detail: string
}

/** 一拍的结果（`due` = 本拍刚喊出判决；之后到 `reset()` 之前不再喊）。 */
export type IdleStopStep = 'disabled' | 'busy' | 'quiet' | 'miss' | 'due'

export interface IdleStopState {
  /** 连续问不到的拍数。 */
  misses: number
  /** 队列静默的起点（0 = 还没开始计窗）。 */
  quietSince: number
  /** 已经喊过的判决（null = 还没喊）。 */
  fired: IdleStopDue | null
  /** 最近一拍读到的合计（null = 最近一拍问不到）。 */
  last: HubQueueFacts | null
}

export interface IdleStopDeps {
  /** 本页开关（每拍都读：面板改完立刻生效，不用刷页面）。 */
  enabled: () => boolean
  /**
   * 本页是不是**自主盘**（`battle.offline.ipynb`）。
   *
   * 只有自主盘要把 `heldLive` 算成活：整课在本机跑时 hub 的 `pending`/`inflight` 都是 0，
   * 判空的唯一证据就是自己那门课的 live hold。协作盘领不到被 hold 压住的课，不能跟着一起等。
   */
  autonomous?: () => boolean
  /** 问 dashboard 要 hub 队列事实；null = 这一拍不可知（不可知 ≠ 空队列）。 */
  facts: () => Promise<HubQueueFacts | null>
  /** 判决成立一次（调用方负责倒计时 + 停会话）。 */
  onDue: (due: IdleStopDue) => void
  /** 读数（进 op 日志：事后能对账「为什么停了 / 为什么没停」）。 */
  onEvent?: (op: string, detail?: string) => void
  now?: () => number
  idleWindowMs?: number
  unreachableAttempts?: number
}

export interface IdleStop {
  /** 跑一拍（调用方定时调；用例直接调）。 */
  poll(): Promise<IdleStopStep>
  /** 复位（取消后重新计窗 / 手动停用后重开）。 */
  reset(): void
  /** 只读快照（诊断/面板用）。 */
  state(): IdleStopState
}

/** 计数夹成非负整数；不是有限数字 ⇒ null（坏值不能悄悄变成 0）。 */
function count(v: unknown): number | null {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null
  return Math.max(0, Math.trunc(v))
}

/** `/api/hubQueue` 载荷 → 四个计数。`ok !== true` / 形状不对 ⇒ null（不可知不当空队列）。 */
export function parseHubQueueFacts(body: unknown): HubQueueFacts | null {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) return null
  const o = body as Record<string, unknown>
  if (o.ok !== true) return null
  const pending = count(o.pending)
  const inflight = count(o.inflight)
  if (pending === null || inflight === null) return null
  // 旧版 dashboard 没有这两个键：pendingUnheld 退化成全部 pending（保守），heldLive 退化成 0
  // （旧 hub 也没有 hold 概念）。键**在但值坏** ⇒ 整拍不可知，别把坏值当 0 停掉在训会话。
  const pendingUnheld = 'pendingUnheld' in o ? count(o.pendingUnheld) : pending
  const heldLive = 'heldLive' in o ? count(o.heldLive) : 0
  if (pendingUnheld === null || heldLive === null) return null
  return { pending, inflight, pendingUnheld, heldLive }
}

/** 问一次 dashboard（回环；hub 的地址与 token 都在 dashboard 侧，页面不碰）。 */
export async function fetchHubQueueFacts(deps: {
  runtime: Pick<Runtime, 'gmRequest'>
  dashboardBase: string
}): Promise<HubQueueFacts | null> {
  let res: { status: number; responseText: string }
  try {
    res = await deps.runtime.gmRequest({
      method: 'GET',
      url: `${deps.dashboardBase}/api/hubQueue`,
      headers: { 'X-Notebook-Companion': '1' },
      timeout: HUB_QUEUE_TIMEOUT_MS,
    })
  } catch {
    return null
  }
  if (res.status !== 200) return null
  try {
    return parseHubQueueFacts(JSON.parse(res.responseText))
  } catch {
    return null
  }
}

export function createIdleStop(deps: IdleStopDeps): IdleStop {
  const now = deps.now ?? (() => Date.now())
  const idleWindowMs = Math.max(0, deps.idleWindowMs ?? IDLE_WINDOW_MS)
  const attempts = Math.max(1, deps.unreachableAttempts ?? UNREACHABLE_ATTEMPTS)
  let misses = 0
  let quietSince = 0
  let fired: IdleStopDue | null = null
  let last: HubQueueFacts | null = null
  /** 上一拍开关是不是开着：关掉/打开各留一行 op（不每拍刷屏）。 */
  let wasEnabled: boolean | null = null

  // 判决本身不写 op（调用方那条线有「为什么 + 倒计时结果」，两条只会重复）；这里只保证只喊一次。
  const fire = (due: IdleStopDue): IdleStopStep => {
    if (fired) return 'due'
    fired = due
    deps.onDue(due)
    return 'due'
  }

  return {
    reset() {
      misses = 0
      quietSince = 0
      fired = null
      last = null
    },
    state: () => ({ misses, quietSince, fired, last }),
    async poll() {
      const enabled = deps.enabled()
      if (!enabled) {
        if (wasEnabled === true) deps.onEvent?.('idleStop:off', '开关已关，停止判决')
        wasEnabled = false
        misses = 0
        quietSince = 0
        fired = null
        return 'disabled'
      }
      if (wasEnabled !== true) {
        deps.onEvent?.(
          'idleStop:on',
          `开始判决：队列静默 ${Math.round(idleWindowMs / 60_000)} 分钟或连续 ${attempts} 拍问不到 hub 就停会话`,
        )
      }
      wasEnabled = true

      const facts = await deps.facts()
      if (facts === null) {
        last = null
        misses += 1
        quietSince = 0
        if (misses >= attempts) {
          return fire({
            kind: 'unreachable',
            detail: `连续 ${misses} 拍问不到 hub 队列（dashboard/hub 不可达）`,
          })
        }
        deps.onEvent?.('idleStop:miss', `问不到 hub 队列（${misses}/${attempts}）`)
        return 'miss'
      }
      misses = 0
      last = facts
      // 自主盘的整课在跑：hub 队列静默，只有 live hold 能说明「正在训练」。
      const selfTraining = facts.heldLive > 0 && (deps.autonomous?.() ?? false)
      if (facts.pendingUnheld + facts.inflight > 0 || selfTraining) {
        quietSince = 0
        return 'busy'
      }
      const factsText =
        `可领=${facts.pendingUnheld} 在飞=${facts.inflight} ` +
        `待领合计=${facts.pending} liveHold=${facts.heldLive}`
      if (quietSince === 0) {
        quietSince = now()
        deps.onEvent?.(
          'idleStop:quiet',
          `队列静默（${factsText}），开始计窗 ${Math.round(idleWindowMs / 60_000)} 分钟`,
        )
        return 'quiet'
      }
      const quietMs = now() - quietSince
      if (quietMs < idleWindowMs) return 'quiet'
      return fire({
        kind: 'empty',
        detail: `队列静默 ${Math.round(quietMs / 60_000)} 分钟（${factsText}）`,
      })
    },
  }
}
