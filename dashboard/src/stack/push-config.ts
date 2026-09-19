/** push-config.ts — Push 模式启动前的执行面解析（endpoint/auth ping 门 + rl-config 回写）。
 *
 *  两种来源（`configurePushEndpoint` 裁决）：
 *  ① **用户填的 endpoint**：GET {url}/ping（Bearer authKey）通了才继续；回写 rl-config
 *     （upsert 云 `nodes[].gpu_push` + `courses.<课>.push_node_url`）；
 *  ② **复用 config**：endpoint 留空且某 enabled gpu_push 节点 ping 通 → 只把该 URL 写进
 *     `courses.<课>.push_node_url`（节点条目原样不动）。
 *
 *  **缺执行面 = 响亮报错（2026-09-15 用户指令），不自动回落本机。**
 *  旧实现有第 ③ 条「回落本机 worker_server」：endpoint 留空且 config 无 ping 通节点时，
 *  自动把执行面改指本机 `workerServe` 伪 GPU 节点。实测代价（同日）：用户已填好云 endpoint
 *  （丢参 bug 见 `app.tsx:726`），却看到训练"正常"跑在本机——**故障被伪装成成功**。
 *  **2026-09-19 起本机伪节点彻底退出控制台**（用户指令：「它只是用于 trainingloop 冒烟测试」）：
 *  它不再是受管组件，也不再是可选执行面——「一键本机 push」这条能力连同 `allowLocal`
 *  opt-in 一并删除（留着就是一条会把训练指向**死端点**的路径）。`local_push` 只剩**遗留标记**
 *  一个作用：`pushTargetFromConfig` 把历史条目识别成「本机」并在卡片上说出来（看得见的坏
 *  过静默的）。
 *
 *  之后 startPreset('push') 都不注入 REMOTE_PUSH_NODE（env 会强制 remote_token），由 Python
 *  `_gpu_push_nodes` 从 config 读节点（按 `courses.<课>.push_node_url` 过滤），authKey 以
 *  config 里的为准——本机节点写的就是 rl.remote_token，与 worker_server `--token` 同源。
 */

import { loadConfig, saveConfig } from '../core/config'
import { httpOk } from '../core/net'
import type { NodeConf, RlConfig } from '../core/types'

/** 稳定节点 id：同一 URL 复用同一 id（多课 N:1 共享时只改 authKey/enabled）。 */
const GPU_PUSH_NODE_ID = 'gpu-push'

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

/** upsert **云** gpu_push 节点 + 课程 push_node_url（原 cfg 上改写，返回同一对象）。
 *
 *  `!n.local_push`：历史遗留的本机伪节点条目（`local_push`，2026-09-15 那版回落写的；
 *  写入器已于 2026-09-19 随「伪节点退出控制台」删除）是**独立条目**——云 URL 的写入绝不吃掉
 *  它（那是别人的数据，删它得由操作员在节点面板里显式做）。 */
export function applyPushNodeConfig(
  cfg: RlConfig,
  course: string,
  url: string,
  authKey: string,
): RlConfig {
  const nodes: NodeConf[] = Array.isArray(cfg.nodes) ? [...cfg.nodes] : []
  const key = authKey.trim()
  const idx = nodes.findIndex((n) => n.gpu_push && !n.local_push)
  const base: NodeConf = {
    id: idx >= 0 ? nodes[idx]!.id || GPU_PUSH_NODE_ID : GPU_PUSH_NODE_ID,
    url,
    authKey: key,
    concurrency: idx >= 0 ? (nodes[idx]!.concurrency ?? 1) : 1,
    enabled: true,
    gpu_push: true,
  }
  if (idx >= 0) nodes[idx] = { ...nodes[idx], ...base }
  else nodes.push(base)
  cfg.nodes = nodes
  // 展开可能为 undefined 的旧值即得新副本（spread 忽略 undefined，无需 `?? {}` 回退）。
  cfg.courses = { ...cfg.courses }
  cfg.courses[course] = {
    ...cfg.courses[course],
    push_node_url: url,
  }
  return cfg
}

/** enabled 且 gpu_push 的节点（config 扫描；enabled 缺省视为 true）。
 *  **含**遗留的 `local_push` 条目：它仍要在卡片的「执行面」徽章里可见（否则操作员看到的
 *  是「训练正常」，而实际目标是一条没人服务的本机地址）。 */
export function enabledGpuPushNodes(cfg: RlConfig): NodeConf[] {
  return (cfg.nodes ?? []).filter((n) => n.gpu_push && n.enabled !== false)
}

/** 遍历 enabled gpu_push，返回第一个 GET /ping 通的节点；全不通 → null。
 *
 *  **一律排除** `local_push` 遗留节点（不再有 opt-in）：策略是「缺执行面就响亮报错」，
 *  而本机伪节点已经不由控制台提供（2026-09-19）——把它算作候选，就只会在复用时把执行面
 *  指向一条没人服务的本机地址。 */
export async function findHealthyGpuPushNode(
  cfg: RlConfig,
  timeoutMs = 5000,
): Promise<NodeConf | null> {
  const cands = enabledGpuPushNodes(cfg).filter((n) => n.local_push !== true)
  for (const n of cands) {
    const url = String(n.url ?? '').trim()
    const key = String(n.authKey ?? '').trim()
    if (!url || !key) continue
    try {
      const ok = await httpOk(`${url.replace(/\/+$/, '')}/ping`, key, timeoutMs)
      if (ok) return n
    } catch {
      /* 下一个 */
    }
  }
  return null
}

/** 本课当前 push 执行面（纯函数，不碰网络/磁盘）——控制台卡片与启动弹窗的展示数据源。
 *
 *  `courses.<课>.push_node_url` 是唯一指针（python `_gpu_push_nodes` 也按它过滤），
 *  再回到 `nodes[]` 认领它的那个节点，判定是本机回落节点还是云 GPU：
 *    local      = 指到 `local_push` 节点（**历史遗留**的本机伪节点地址：控制台不再提供该执行面，
 *                 卡片必须说出来，否则操作员看到的是「训练正常」而目标无人服务）
 *    cloud      = 指到普通 gpu_push 节点（云机）
 *    unresolved = 指向了一个 config 里不存在的 URL（python 侧「匹配 0 个」→ 回落 pull，
 *                 必须显式暴露：否则操作员看到的是「训练正常」）
 *  未配置 push 目标 → null（非 push 场景不显示任何徽章）。
 */
export interface ConfiguredPushTarget {
  kind: 'local' | 'cloud' | 'unresolved'
  url: string
  /** 认领该 URL 的节点 id（unresolved 时 null）。 */
  nodeId: string | null
  /** 该目标的鉴权键（节点 authKey；无节点时回退 rl.remote_token）——探测 /ping 用。 */
  authKey: string
}

export function pushTargetFromConfig(cfg: RlConfig, course: string): ConfiguredPushTarget | null {
  const raw = String(cfg.courses?.[course]?.push_node_url ?? '').trim()
  if (!raw) return null
  const url = raw.replace(/\/+$/, '')
  const node = (cfg.nodes ?? []).find(
    (n) => n.gpu_push && String(n.url ?? '').replace(/\/+$/, '') === url,
  )
  return {
    kind: node ? (node.local_push ? 'local' : 'cloud') : 'unresolved',
    url,
    nodeId: node?.id ?? null,
    authKey: String(node?.authKey ?? cfg.rl?.remote_token ?? '').trim(),
  }
}

/** Push 执行面裁决结果。 */
export interface PushTarget {
  url: string
  /** 来源：manual=用户填的 endpoint（已 ping 通并回写）；config=复用 config 里 ping 通的
   *  节点。缺执行面时**抛错**——没有第三条路（本机伪节点已退出控制台，2026-09-19）。 */
  source: 'manual' | 'config'
}

/** 控制台 Push 启动前置：解析执行面并回写 rl-config。优先级：
 *  ① 用户填的 endpoint（ping 门 + upsert 云节点）；
 *  ② endpoint 留空 → config 里 enabled 且 ping 通的 gpu_push（**不含**遗留 local_push）；
 *  ③ 都没有 → **响亮报错**（绝不回落本机执行面——那会把「云机连不上」伪装成训练正常；
 *     且本机伪节点自 2026-09-19 起只服务冒烟预演，不由控制台提供）。
 *  失败抛 Error（不写盘、不启动），由控制台横幅呈现。 */
export async function configurePushEndpoint(
  course: string,
  endpoint: string,
  authKey: string,
): Promise<PushTarget> {
  if (!course) throw new Error('Push 配置需要 course（先在顶部设置课程）')
  const cfg = loadConfig()
  const manual = (endpoint ?? '').trim()
  if (!manual) {
    const hit = await findHealthyGpuPushNode(cfg, 5000)
    if (hit) {
      const url = String(hit.url).replace(/\/+$/, '')
      cfg.courses = { ...cfg.courses }
      cfg.courses[course] = { ...cfg.courses[course], push_node_url: url }
      saveConfig(cfg)
      return { url, source: 'config' }
    }
    // 缺执行面 = 响亮报错（2026-09-15 用户指令「就算云机连接不上，也不能直接开本地
    // worker，横幅报错就好」）。**绝不静默回落本机**：那会把「云机连不上」伪装成
    // 「训练正常」，操作员看到的是一切照旧，实际 PPO 跑在本机伪节点上。
    const cands = enabledGpuPushNodes(cfg)
      .map((n) => String(n.url ?? '').trim())
      .filter(Boolean)
    throw new Error(
      'Push 执行面不可用：rl-config 里没有 enabled 且 GET /ping 通的 gpu_push 节点' +
        (cands.length
          ? `（候选 ${cands.length} 个：${cands.join(' / ')}）`
          : '（config 里连候选节点都没有）') +
        '。请确认云机 worker_server 在跑、cloudflared 隧道可达（HTTP 530 = 隧道无客户端）、' +
        'auth key 与 worker_server --token 一致，或在启动弹窗直接填 endpoint 与 auth key。' +
        '（本机伪节点只服务冒烟预演，不由控制台提供——2026-09-19）',
    )
  }
  const url = normalizePushUrl(manual)
  await pingPushEndpoint(url, authKey)
  applyPushNodeConfig(cfg, course, url, authKey)
  saveConfig(cfg)
  return { url, source: 'manual' }
}
