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
import { type HubQueueView, type PushWorkerView, parseHubQueue } from '../web/view'

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
  rows: Array<Omit<PushWorkerView, 'online' | 'busy'>>,
  cfg: RlConfig,
): Promise<PushWorkerView[]> {
  const keyById = new Map(
    (cfg.nodes ?? []).map((n) => [String(n.id ?? ''), String(n.authKey ?? '')] as const),
  )
  return Promise.all(
    rows.map(async (row) => {
      if (!row.enabled) return { ...row, online: null, busy: null }
      const key = keyById.get(row.id) ?? ''
      if (!key.trim()) return { ...row, online: null, busy: null }
      const r = await probePushWorker(row.url, key)
      return { ...row, online: r.online, busy: r.busy }
    }),
  )
}
