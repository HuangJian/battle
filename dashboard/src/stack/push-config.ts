/** push-config.ts — Push 模式启动前的 endpoint/auth 配置（ping 门 + rl-config 回写）。
 *
 *  控制台启动 Push 时要求用户填 cloudflared（worker_server）endpoint 与 auth key：
 *  ① GET {url}/ping（Bearer authKey）通了才继续；
 *  ② 回写 rl-config：upsert `nodes[].gpu_push` + `courses.<课>.push_node_url`；
 *  ③ 之后 startPreset('push') 不再注入 REMOTE_PUSH_NODE（env 会强制 remote_token），
 *     由 Python `_gpu_push_nodes` 从 config 读节点，**authKey 以用户填写的为准**。
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

/** upsert gpu_push 节点 + 课程 push_node_url（原 cfg 上改写，返回同一对象）。 */
export function applyPushNodeConfig(
  cfg: RlConfig,
  course: string,
  url: string,
  authKey: string,
): RlConfig {
  const nodes: NodeConf[] = Array.isArray(cfg.nodes) ? [...cfg.nodes] : []
  const key = authKey.trim()
  const idx = nodes.findIndex((n) => n.gpu_push)
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
  cfg.courses = { ...(cfg.courses ?? {}) }
  cfg.courses[course] = {
    ...(cfg.courses[course] ?? {}),
    push_node_url: url,
  }
  return cfg
}

/** 控制台 Push 启动前置：ping 门 → 回写 rl-config。失败抛 Error（不写盘）。 */
export async function configurePushEndpoint(
  course: string,
  endpoint: string,
  authKey: string,
): Promise<{ url: string }> {
  if (!course) throw new Error('Push 配置需要 course（先在顶部设置课程）')
  const url = normalizePushUrl(endpoint)
  await pingPushEndpoint(url, authKey)
  const cfg = loadConfig()
  applyPushNodeConfig(cfg, course, url, authKey)
  saveConfig(cfg)
  return { url }
}
