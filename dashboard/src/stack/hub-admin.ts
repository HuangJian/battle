/** hub-admin.ts — hub-server 的 /admin/* 观测面客户端（控制台多课程总览 + worker 登记）。
 *
 *  为什么需要这一层：hub 是**独立 python 进程**，多课程后一个进程同时服务 N 门课
 *  （`/admin/queue` 的 courses 块覆盖全部课程表）。控制台要回答「哪门课在饿着 / 谁是
 *  在飞持有人 / push 派发器登记了哪几台」，只能问它；而进程内既没有这些状态，也不该有
 *  （调度状态归 hub，控制台只读）。
 *
 *  基址解析（`liveHub`）：多课程下「哪台 hub」不再唯一由查看课程决定——课程表是 hub
 *  启动参数给的，控制台按**账本里活着的 hub 条目**逐个试，第一个应答的就用它。这样
 *  单 hub 服务多课（新形状）与每课一 hub（旧形状）都能读对，且不引入新的配置键。
 *  都没有 → null（面板显示「无 hub 应答」，而不是编一个 URL）。
 *
 *  鉴权：与 hub 其余端点同源（`Authorization: Bearer <rl.remote_token>`）。失败一律
 *  返回 null（缺字段/401/超时同一处理）——观测面坏了不该把整页 /api/state 带崩。
 */

import { pidAlive } from '../core/net'
import { loadRegistry } from '../core/registry'
import { sharedHubUrl } from '../core/slots'
import type { RlConfig } from '../core/types'
import {
  type HubQueueView,
  type OfflineAdminView,
  type PushWorkerView,
  type OfflineRunView,
  parseHubQueue,
  parseOfflineAdmin,
} from '../web/view'

/** GET 一个 hub 管理端点 → 解析后的 JSON；网络失败/非 2xx/坏 JSON → null。 */
async function hubGet(url: string, token: string, timeoutMs: number): Promise<unknown | null> {
  try {
    const resp = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (resp.status !== 200) return null
    return (await resp.json()) as unknown
  } catch {
    return null
  }
}

/** hub 候选基址（按可信度排序）：账本里**活着**的 hub 条目 → 查看课程的槽位端口。
 *  账本条目带 url（登记时写入），故不必重算端口；末位兜底用槽位算术。 */
export function hubCandidates(cfg: RlConfig, course: string): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  try {
    const reg = loadRegistry()
    for (const ent of Object.values(reg.hubServers ?? {})) {
      if (!ent || typeof ent.pid !== 'number' || !pidAlive(ent.pid)) continue
      const u = typeof ent.url === 'string' ? ent.url.replace(/\/+$/, '') : ''
      if (u && !seen.has(u)) {
        seen.add(u)
        out.push(u)
      }
    }
  } catch {
    /* 账本不可读 → 只留槽位兜底 */
  }
  // 末位兜底 = **共享** hub 地址（2026-09-18）：一个进程服务所有课程，地址与 course 无关。
  void course
  const fallback = sharedHubUrl(cfg)
  if (!seen.has(fallback)) out.push(fallback)
  return out
}

/** 探测超时：控制台轮询间隔最密 60s，而 hub 在本机——1.2s 足够，且总预算
 *  （候选数 × 1.2s）仍远小于慢快照 5s 的刷新周期。 */
export const HUB_PROBE_TIMEOUT_MS = 1200

/** 找出应答 `/admin/queue` 的 hub 基址 + 它的队列视图。 */
export async function liveHub(
  cfg: RlConfig,
  course: string,
): Promise<{ url: string; queue: HubQueueView } | null> {
  const token = String(cfg.rl?.remote_token ?? '')
  for (const base of hubCandidates(cfg, course)) {
    const body = await hubGet(`${base}/admin/queue`, token, HUB_PROBE_TIMEOUT_MS)
    const queue = parseHubQueue(body)
    if (queue) return { url: base, queue }
  }
  return null
}

/** `/admin/offline` 的完整观测面（逐课进度 + 段末摘要 + 停滞告警）；hub 不可达 / 旧版本 → null。
 *
 *  ★ 2026-10-03（plan/auto-offline-handoff T6/T8）：从「只取 progress」扩成三段。段末摘要
 *  是导入转交的判决输入（`run_id` + `end_it_reached`），停滞告警是自动交接固有代价的
 *  显式出口——三块同一个端点、同一次探测，分两次取只会让「面板与导入看到不同的 hub」。 */
export async function hubOfflineAdmin(
  url: string,
  token: string,
): Promise<OfflineAdminView | null> {
  return parseOfflineAdmin(await hubGet(`${url}/admin/offline`, token, HUB_PROBE_TIMEOUT_MS))
}

/** 只取逐课进度的薄壳（旧读面：overview / 课程行；hub 不可达 → null）。 */
export async function hubOfflineProgress(
  url: string,
  token: string,
): Promise<Record<string, Record<string, OfflineRunView>> | null> {
  return (await hubOfflineAdmin(url, token))?.progress ?? null
}

/** `/admin/push-workers` 的登记表归一化：id → hub 侧探活结论。
 *  hub 未启用 push 派发时该端点 409 ⇒ null（面板显示「未挂载」）。 */
export function parsePushWorkerRegistry(body: unknown): Map<string, boolean> | null {
  if (!body || typeof body !== 'object') return null
  const reg = (body as Record<string, unknown>).registry
  if (!reg || typeof reg !== 'object') return null
  const workers = (reg as Record<string, unknown>).workers
  if (!Array.isArray(workers)) return null
  const out = new Map<string, boolean>()
  for (const w of workers) {
    if (!w || typeof w !== 'object') continue
    const rec = w as Record<string, unknown>
    const id = typeof rec.id === 'string' ? rec.id : ''
    if (!id) continue
    out.set(id, rec.online === true)
  }
  return out
}

/** hub 的 push worker 登记表 + 派发器状态；hub 不可达 / 未启用 → null。 */
export async function hubPushWorkers(
  url: string,
  token: string,
): Promise<Map<string, boolean> | null> {
  return parsePushWorkerRegistry(
    await hubGet(`${url}/admin/push-workers`, token, HUB_PROBE_TIMEOUT_MS),
  )
}

/** 让 hub **立刻**重读 rl-config（不等下一拍）。返回是否成功。
 *  best-effort：失败不抛——配置已经落盘，hub 下一拍的热重载仍会拾取新条目。 */
export async function hubReloadPushWorkers(url: string, token: string): Promise<boolean> {
  try {
    const resp = await fetch(`${url}/admin/push-workers`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reload' }),
      signal: AbortSignal.timeout(3000),
    })
    return resp.status === 200
  } catch {
    return false
  }
}

// ★M4：`hubSetCourseMode()`（`POST /admin/courses?course=X&mode=…[&pin=…][&drop_jobs=1]`）
// 已删除——「课程模式」这个语义随 plan/worker-type-dispatch-model §3-M4 退役：hub 侧不再有
// `_modes`/pin（M4 同批删），控制台也不再有意图表可落。**取代它的唯一写面** = 下面的
// `hubReleaseCourseHold`（人工解除接管）与自主 worker 的 claim 链（后者不经过控制台）。

/** **强制解除接管**（plan §1.3 状态表「强制解除」行）：
 *  `POST /admin/courses?course=<课>&release_hold=1`（★M1b 起的 hub 端点）。
 *
 *  语义（hub 侧同口径）：走 `revoke_offline_lease` 立**墓碑**——现场看得见「有人把它踢下来了」
 *  （`holder_info` 照返 tombstone 形状），下一次 claim 直接覆盖；而不是一个凭空消失的 owner。
 *  这是「live hold 不可被顶」（不变量 3）留给人的那条出口。
 *
 *  返回 `{ok, message}`：200 = 已解除；409 = 本来就没接管（「没接管可解」与「解不了」是
 *  两件事，混成 false 会让操作员重复点）。 */
export async function hubReleaseCourseHold(
  url: string,
  token: string,
  course: string,
): Promise<{ ok: boolean; message: string }> {
  const qs = `course=${encodeURIComponent(course)}&release_hold=1`
  try {
    const resp = await fetch(`${url.replace(/\/+$/, '')}/admin/courses?${qs}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(3000),
    })
    const body = (await resp.json().catch(() => null)) as { error?: unknown } | null
    if (resp.status === 200) {
      return { ok: true, message: '已解除接管（已立墓碑，下一次 claim 可直接覆盖）' }
    }
    const err = typeof body?.error === 'string' ? body.error : `HTTP ${resp.status}`
    if (resp.status === 409) {
      return { ok: true, message: '这门课当前没有被接管（无需解除）' }
    }
    return { ok: false, message: err }
  } catch (e) {
    return { ok: false, message: `hub 不可达：${e instanceof Error ? e.message : String(e)}` }
  }
}

/** 人工解冻一份被毒包熔断冻住的 job（§4.1 唯一的可逆口）：`POST /admin/unfreeze?job_id=`。
 *
 *  语义（hub 侧同口径）：解冻 = 清冻结 + **清计数**（下一次重领从头计数）⇒ job 立即回池可重领。
 *  重发（`publish`）**刻意不**走这条路（重发不清冻结，否则「重发即重试」会把熔断当场抹掉）；
 *  所以这是操作员唯一的确认口：看懂了为什么它被冻（`reclaims` 次零回传）再放回去。
 *
 *  返回 `{ok, message}`：404（未知 job）/409（本来就没冻）都要把 hub 的原话带回来
 *  ——「没冻可解」与「解冻失败」是两件事，静默把它们混成 false 会让操作员重复点。 */
export async function hubUnfreeze(
  url: string,
  token: string,
  jobId: string,
): Promise<{ ok: boolean; message: string }> {
  const base = url.replace(/\/+$/, '')
  const qs = `job_id=${encodeURIComponent(jobId)}`
  try {
    const resp = await fetch(`${base}/admin/unfreeze?${qs}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(3000),
    })
    const body = (await resp.json().catch(() => null)) as { error?: unknown } | null
    if (resp.status === 200) return { ok: true, message: '已解冻，回池可重领' }
    const err = typeof body?.error === 'string' ? body.error : `HTTP ${resp.status}`
    return { ok: false, message: err }
  } catch (e) {
    return { ok: false, message: `hub 不可达：${e instanceof Error ? e.message : String(e)}` }
  }
}

/** 直探一台 worker_server 的 `/ping`（登记/列表用）：`{online, busy}`。
 *  `online=false` 与「未探」是**两种**状态：前者是探过不通（隧道没起来/机器没开），
 *  后者是根本没法探（无鉴权键 / 已停用）——面板要给不同的提示。 */
export async function probePushWorker(
  url: string,
  authKey: string,
  timeoutMs = 1500,
): Promise<{ online: boolean; busy: boolean | null }> {
  const key = authKey.trim()
  if (!key) return { online: false, busy: null }
  try {
    const resp = await fetch(`${url.replace(/\/+$/, '')}/ping`, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (resp.status !== 200) return { online: false, busy: null }
    const body = (await resp.json().catch(() => null)) as { busy?: unknown } | null
    return { online: true, busy: typeof body?.busy === 'boolean' ? body.busy : null }
  } catch {
    return { online: false, busy: null }
  }
}

/** push worker 视图的直探填充（并发探；任一失败只影响自己那一行）。 */
export async function withWorkerProbes(
  rows: Array<Omit<PushWorkerView, 'online' | 'busy' | 'kind'>>,
  cfg: RlConfig,
): Promise<PushWorkerView[]> {
  const keyById = new Map(
    (cfg.nodes ?? []).map((n) => [String(n.id ?? ''), String(n.authKey ?? '')] as const),
  )
  // `kind`（★P2-5）不在这里定：它要 hub 观测面（接管/报名/在飞），由 `overview.getHubAdmin` 补。
  return Promise.all(
    rows.map(async (row) => {
      if (!row.enabled) return { ...row, online: null, busy: null, kind: null }
      const key = keyById.get(row.id) ?? ''
      if (!key.trim()) return { ...row, online: null, busy: null, kind: null }
      const r = await probePushWorker(row.url, key)
      return { ...row, online: r.online, busy: r.busy, kind: null }
    }),
  )
}
