/** push-config.ts — push 执行面的**读模型**（登记节点 → 这轮 PPO 会去哪）。
 *
 *  ★ **课程与 worker 节点正交**（2026-09-19 用户口径：「课程任务与 worker 节点互相正交！所有
 *  worker 都可能接到在训的课程任务，不管它是哪个课程的」）。由此本模块只剩两件事：
 *
 *  ① **登记入口的公共件**：`normalizePushUrl` / `pingPushEndpoint`（写 `nodes[].gpu_push` 的
 *     动作在 `server/actions/workers.ts`，ping 门与 URL 归一住这里）；
 *  ② **执行面读模型** `remoteExecutionFace(cfg)`：由**部署事实**推出这轮 PPO 会走哪条路
 *     —— 登记在册的 `nodes[].gpu_push` + `rl.hub_push`（缺省开）+ hub 可达性。
 *
 *  删掉的东西（逐条都有理由）：
 *   · `applyPushNodeConfig` / `configurePushEndpoint`：启动时按课解析 endpoint 并回写
 *     `courses.<课>.push_node_url` —— 那就是「把某门课钉到某台机器」，与正交性矛盾。
 *   · `applyLocalPushNodeConfig` / `localPushUrl`（更早一轮已删）：本机伪节点已退出控制台。
 *   · `pushTargetFromConfig`：按 `push_node_url` 认领节点 —— 前提的键不存在了。
 *   · `findHealthyGpuPushNode`：复用扫描（「有活的就用」）被「登记即候选」取代，且它按
 *     ping 挑节点会把「该谁跑」变成随时变化的探测结果。
 *
 *  python 侧的对应语义（`rl/loop_steps.py`）：auto 取**全部** enabled 的 gpu_push 节点；
 *  `rl.hub_push`（缺省开）+ hub_url/token 齐备 ⇒ 发布带 `manifest.dispatch=\"push\"`，由 hub
 *  按队列顺序推给空闲 worker；否则直推节点（按序 failover）；都没有 ⇒ hub pull（worker 来领）。
 */

import { httpOk } from '../core/net'
import type { NodeConf, RlConfig } from '../core/types'

/** 归一化 endpoint：补协议、去尾斜杠；非法抛 Error。 */
export function normalizePushUrl(raw: string): string {
  let u = (raw ?? '').trim()
  if (!u) throw new Error('Push endpoint 不能为空（worker_server / cloudflared 隧道 URL）')
  // 已有 scheme 但不是 http(s)：直接拒绝（勿再包一层 https://）
  const scheme = /^([a-z][a-z0-9+.-]*):\/\//i.exec(u)
  if (scheme && !/^https?$/i.test(scheme[1]!)) {
    throw new Error(`Push endpoint 协议非法: ${scheme[1]}`)
  }
  if (!/^https?:\/\//i.test(u)) u = `https://${u}`
  u = u.replace(/\/+$/, '')
  let parsed: URL
  try {
    parsed = new URL(u)
  } catch {
    throw new Error(`Push endpoint 非法: ${raw}`)
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`Push endpoint 协议非法: ${parsed.protocol}`)
  }
  return u
}

/** ping worker_server：GET /ping + Bearer；200 = 通。 */
export async function pingPushEndpoint(
  url: string,
  authKey: string,
  timeoutMs = 8000,
): Promise<void> {
  if (!authKey?.trim()) throw new Error('Push auth key 不能为空（worker_server --token）')
  const base = normalizePushUrl(url)
  const ok = await httpOk(`${base}/ping`, authKey.trim(), timeoutMs)
  if (!ok) {
    throw new Error(
      `Push endpoint ping 失败: ${base}/ping —— 确认 worker_server 已启动、cloudflared 隧道可达、auth key 与 --token 一致`,
    )
  }
}

/** enabled 且 gpu_push 的节点（config 扫描；enabled 缺省视为 true）。**登记即候选**。 */
export function enabledGpuPushNodes(cfg: RlConfig): NodeConf[] {
  return (cfg.nodes ?? []).filter((n) => n.gpu_push && n.enabled !== false)
}

/** `rl.hub_push` 的**生效值**（缺省 `true`；python `loop_steps._hub_push_opt_in` 同口径）。
 *
 *  用户口径（2026-09-19）：「配了节点就默认走 hub 中介派发」——hub 在新模型下始终在线
 *  （pull 本来就要求它在），而 hub 派发把队列顺序 / 空闲判定 / 超时回落 / 多课程公平全集中
 *  在一处。显式 `\"hub_push\": false` 回到直推节点。 */
export function hubPushEnabled(cfg: RlConfig): boolean {
  const v = (cfg.rl as Record<string, unknown> | undefined)?.hub_push
  return v === undefined || v === null ? true : Boolean(v)
}

/** 这轮 PPO 会去哪（**纯函数**，不碰网络/磁盘）——卡面徽章、启动详情、总览共用一份。 */
export interface RemoteExecutionFace {
  /** hub-dispatch = hub 按队列推给登记节点；direct-push = 训练侧直推节点；pull = 等 worker 来领。 */
  mode: 'hub-dispatch' | 'direct-push' | 'pull'
  /** 上屏短语。 */
  text: string
  /** 一句话补充（为什么是这条；缺什么会导致落在下一条）。 */
  detail: string
  /** 登记在册的 enabled gpu_push 台数。 */
  nodes: number
  /** `rl.hub_push` 生效值。 */
  hubPush: boolean
  /** hub 地址（`rl.remote_hub_url`；空 = 未配置 ⇒ hubpush 退化为直推）。 */
  hubUrl: string
}

/** 由**部署事实**推执行面。顺序与 python `resolve_transport`/`resolve_hub_push` 同源：
 *  有节点 + hub_push + hub 地址 ⇒ hub 中介派发；有节点但缺 hub（或显式关掉）⇒ 直推；
 *  没节点 ⇒ pull（hub 队列等人来领，本机 worker 也是其中一个）。
 *  token 缺省只作 detail 提示（python 缺 token 时 hubpush 退化直推，不炸训练）。 */
export function remoteExecutionFace(cfg: RlConfig): RemoteExecutionFace {
  const nodes = enabledGpuPushNodes(cfg)
  const hubPush = hubPushEnabled(cfg)
  const hubUrl = String(cfg.rl?.remote_hub_url ?? '').trim()
  const token = String(cfg.rl?.remote_token ?? '').trim()
  if (nodes.length === 0) {
    return {
      mode: 'pull',
      text: '等 worker 拉取（hub 队列）',
      detail: '未登记 push 节点：谁在轮询 hub 谁就能领到这门课的活（本机 worker 与云机同权）',
      nodes: 0,
      hubPush,
      hubUrl,
    }
  }
  if (hubPush && hubUrl && token) {
    return {
      mode: 'hub-dispatch',
      text: `hub 中介派发 → ${nodes.length} 台 GPU 节点`,
      detail: `hub 按队列顺序推给空闲 worker（${hubUrl}）；停用节点请走 worker 登记面板`,
      nodes: nodes.length,
      hubPush,
      hubUrl,
    }
  }
  const why = !hubPush
    ? 'rl.hub_push=false（显式直推）'
    : !hubUrl
      ? 'hub 地址未配置（rl.remote_hub_url 为空）'
      : 'hub token 未配置'
  return {
    mode: 'direct-push',
    text: `直推 ${nodes.length} 台 GPU 节点`,
    detail: `${why} ⇒ 训练侧按登记顺序直连节点隧道（失败换下一个）`,
    nodes: nodes.length,
    hubPush,
    hubUrl,
  }
}

/** 执行面探针（后台慢快照用）：登记节点逐个 `/ping`（节点未配 authKey ⇒ healthy=null）。 */
export interface PushFleetProbe extends RemoteExecutionFace {
  /** 逐节点探活结果（顺序 = 登记顺序）。 */
  probes: Array<{ id: string; url: string; healthy: boolean | null }>
}

export async function probePushFleet(cfg: RlConfig, timeoutMs = 1500): Promise<PushFleetProbe> {
  const face = remoteExecutionFace(cfg)
  const nodes = enabledGpuPushNodes(cfg)
  const probes = await Promise.all(
    nodes.map(async (n) => {
      const url = String(n.url ?? '').replace(/\/+$/, '')
      const key = String(n.authKey ?? '').trim()
      if (!url) return { id: String(n.id ?? ''), url, healthy: null }
      if (!key) return { id: String(n.id ?? ''), url, healthy: null }
      let healthy: boolean | null = null
      try {
        healthy = await httpOk(`${url}/ping`, key, timeoutMs)
      } catch {
        healthy = false
      }
      return { id: String(n.id ?? ''), url, healthy }
    }),
  )
  return { ...face, probes }
}
