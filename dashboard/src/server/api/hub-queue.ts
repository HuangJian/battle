/**
 * hub-queue.ts — notebook 伴生脚本的「hub 还有没有活」读面（`GET /api/hubQueue`）。
 *
 * 为什么要它：云机 worker 页的工作是「领活 → 算 → 回传」，会话本身按分钟烧配额。hub 不可达
 * （隧道挂了 / hub 进程停了）或整条队列静默（没有 pending、没有 in-flight）时，会话开着也拿不到
 * 活——伴生脚本据此**停会话**省配额（选项在它的面板上）。
 *
 * 读面给四个合计数（`pending` / `inflight` / `pendingUnheld` / `heldLive`）：前两个是全局事实，
 * 后两个区分「被自主盘 live hold 压住的课」——协作盘领不到那些 pending（`pendingUnheld` 才是它能
 * 领的），而自主盘整课在跑时队列全静默、只有 `heldLive` 能证明还在训练。
 *
 * 为什么不在页面里直接问 hub：
 *   · 伴生脚本的 `@connect` 只有 `127.0.0.1` / `localhost`（它整份设计的前提就是「只跟回环说话」），
 *     隧道域名与 tailnet IP 都不可枚举；
 *   · hub 的 token 不该搬进浏览器的 GM 存储/请求里——它在本机配置里，dashboard 本来就有。
 * 所以判断留在页面、事实只在这里取一次（`liveHub` 已经带着「找谁说、怎么说」的全部口径）。
 *
 * 与 `/api/notebookHub` 同一道防线（自定义头 `X-Notebook-Companion`）：跨源网页发不了这个头。
 * 只读：不碰任何调度状态，也不作废快照缓存（一次 `/admin/queue` 探测，与总览同一条腿）。
 */
import { liveHub } from '../../stack/hub-admin'
import type { HubQueueView } from '../../web/view'
import { loadConfigSafe } from './config'

/** 单课程的队列深度（页面只需要「有没有活」，不需要 jid/持有人）。 */
export interface HubQueueCourseRow {
  pending: number
  inflight: number
}

export interface HubQueueSummary {
  /** hub 应答了吗。`false` = 不可达（候选全试完），此时计数一律 0 且 `reason` 带原因。 */
  ok: boolean
  /** 应答的那个 hub 基址；`ok:false` 时为空串（不编地址）。 */
  hubUrl: string
  /** 全部课程合计：可领取 job 数（含被 live hold 压住的课）。 */
  pending: number
  /** 全部课程合计：在飞（租约未过期）条数。 */
  inflight: number
  /**
   * 未被 live hold 压住的课里的可领取 job 数。
   *
   * 协作盘（只领 ppo）在别的自主盘持着课时领不到那些 pending——它们对「本页还能不能拿到活」
   * 不算数。自主盘另有自己的 hold 要算（见 `heldLive`）。
   */
  pendingUnheld: number
  /**
   * 有 **live hold** 的课程数（有人正在跑整课）。
   *
   * 自主盘跑课**不经过** hub 的 job 队列（rollout/ppo 在本机，只回传产物）⇒ 光看
   * `pending`/`inflight` 会把「正在训练」读成「没活」而掐掉会话。`stale` 不算（那是掉线，
   * 按设计可被自动接管）。
   */
  heldLive: number
  /** 逐课明细（轮转序空 ⇒ `{}`）。 */
  courses: Record<string, HubQueueCourseRow>
  reason?: string
}

/** hub 探测结果 → 页面读面（纯函数：不碰网络/时间，用例直接喂形状）。 */
export function summarizeHubQueue(
  live: { url: string; queue: HubQueueView } | null,
): HubQueueSummary {
  if (!live) {
    return {
      ok: false,
      hubUrl: '',
      pending: 0,
      inflight: 0,
      pendingUnheld: 0,
      heldLive: 0,
      courses: {},
      reason: '没有 hub 在应答 /admin/queue（候选全试完）',
    }
  }
  const courses: Record<string, HubQueueCourseRow> = {}
  let pending = 0
  let inflight = 0
  let pendingUnheld = 0
  let heldLive = 0
  for (const [course, row] of Object.entries(live.queue.courses)) {
    // 旧 hub / 坏行可能不给数字：按 0 计而不是 NaN 传播（NaN 会让「有没有活」永远为假）。
    const p = Number.isFinite(row.pending) ? Math.max(0, Math.trunc(row.pending)) : 0
    const f = Number.isFinite(row.inflight) ? Math.max(0, Math.trunc(row.inflight)) : 0
    courses[course] = { pending: p, inflight: f }
    pending += p
    inflight += f
    // live hold = 有人正在这课上干整段；它的 pending 对协作盘不可领，对自主盘则另有 hold 读面。
    const held = row.hold?.state === 'live'
    if (held) heldLive += 1
    else pendingUnheld += p
  }
  return { ok: true, hubUrl: live.url, pending, inflight, pendingUnheld, heldLive, courses }
}

/** 现探一次（`/admin/queue`；序列化前的对象）。探测抛错只掉这一拍，不回 500。 */
export async function buildHubQueueSummary(): Promise<HubQueueSummary> {
  let live: { url: string; queue: HubQueueView } | null = null
  try {
    // 共享 single-hub：课程只是探测入口参数（候选清单来自账本），空串与总览同口径。
    live = await liveHub(loadConfigSafe(), '')
  } catch {
    live = null
  }
  return summarizeHubQueue(live)
}
