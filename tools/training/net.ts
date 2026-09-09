/** net.ts — 健康探测原语（全 Bun 原生 API，无平台分支，沿袭 hub-start §339）。

 *  - portListen: 原生 TCP 连接探测（连上即有人在听）。
 *  - httpOk:    HTTP 健康检查（可选 Bearer、超时）。
 *  - waitUntil: 一切等待以"命令输出/健康探测"触发，无硬编码 sleep。
 *  - pidAlive / killPid: 信号原语（SIGTERM → 轮询 → SIGKILL）。
 */

/** 端口是否在监听：原生 TCP 连接探测。 */
export async function portListen(port: number, host = '127.0.0.1'): Promise<boolean> {
  try {
    const conn = await Bun.connect({ hostname: host, port, socket: { data() {} } })
    conn.end()
    return true
  } catch {
    return false
  }
}

/** HTTP 健康检查（status === 200 判定）。 */
export async function httpOk(url: string, token: string, timeout = 5000): Promise<boolean> {
  try {
    const resp = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: AbortSignal.timeout(timeout),
    })
    return resp.status === 200
  } catch {
    return false
  }
}

/** PID 是否存活（signal 0 = 仅探测）。 */
export function pidAlive(pid: number | undefined | null): boolean {
  if (!pid) return false
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

/** 轮询等待条件成立（探测即输出源），超时前最后再测一次。 */
export async function waitUntil(
  check: () => Promise<boolean>,
  timeoutMs: number,
  stepMs = 500,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await check()) return true
    await sleep(stepMs)
  }
  return check()
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

/** 停止单个 PID：SIGTERM → 轮询存活（输出触发）→ 仍活则 SIGKILL。 */
export async function killPid(pid: number): Promise<boolean> {
  try {
    process.kill(pid, 'SIGTERM')
  } catch {
    return !pidAlive(pid)
  }
  const gone = await waitUntil(() => Promise.resolve(!pidAlive(pid)), 3000, 100)
  if (!gone && pidAlive(pid)) {
    try {
      process.kill(pid, 'SIGKILL')
    } catch {
      /* already dead */
    }
  }
  return !pidAlive(pid)
}

/** 代理环境整形（回环流量永远直连）：检测到 HTTP(S)_PROXY 时把 localhost /
 *  127.0.0.1 / .trycloudflare.com 追加进 NO_PROXY——本机健康探测、rollout 预演与
 *  隧道回环流量一旦被代理规则截走就全是假阴性（原 start.ts shapeProxyEnv 语义）。 */
export function shapeLoopbackNoProxy(): boolean {
  const hadProxy = !!(
    process.env.HTTP_PROXY ||
    process.env.HTTPS_PROXY ||
    process.env.http_proxy ||
    process.env.https_proxy
  )
  if (!hadProxy) return false
  const cur = (process.env.NO_PROXY || process.env.no_proxy || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  const merged = [...new Set([...cur, 'localhost', '127.0.0.1', '.trycloudflare.com'])].join(',')
  process.env.NO_PROXY = merged
  process.env.no_proxy = merged
  return true
}

/** SHA-256 hex 摘要（文件指纹 / 权重 wver）。 */
export async function sha256Hex(data: Uint8Array): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', data as unknown as ArrayBuffer)
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/** 是否为回环地址（控制台局域网只读边界的判断）：IPv4 回环、IPv6 回环与
 *  IPv4-mapped 回环（::ffff:127.x.x.x）均为真；null/未知 → 假（fail closed，
 *  无法判定来源的一律视为非本机，动作被拒）。 */
export function isLoopbackAddress(ip: string | null | undefined): boolean {
  if (!ip) return false
  if (ip === '127.0.0.1' || ip === '::1' || ip === '0:0:0:0:0:0:0:1') return true
  if (ip.startsWith('::ffff:127.')) return true // IPv4-mapped 回环
  return false
}
