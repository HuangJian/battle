import { networkInterfaces } from 'node:os'

/** net.ts — 健康探测原语（全 Bun 原生 API，无平台分支，沿袭 hub-start §339）。

 *  - portListen: 原生 TCP 连接探测（连上即有人在听）。
 *  - httpOk:    HTTP 健康检查（可选 Bearer、超时）。
 *  - waitUntil: 一切等待以"命令输出/健康探测"触发，无硬编码 sleep。
 *  - pidAlive / killPid: 信号原语（SIGTERM → 轮询 → SIGKILL）。
 */

/** 端口探测的上界（毫秒）。回环上**真在听**的 socket，握手是 µs 级（内核直接回 SYN-ACK，
 *  不等 accept）——超过这个上界只可能是「没人听」，按 `false` 收。
 *
 *  为什么必须有上界（2026-10-20）：`Bun.connect` 到**没人监听**的端口，正常主机立刻
 *  `ECONNREFUSED`；可丢回环 SYN 的主机上它**既不报错也不返回**（本仓 Linux 沙箱实测 >6s）。
 *  而 `portListen` 是「就绪 / 归属 / 空闲」所有判断的代理：`proc.portOwnedBy` 的兜底、
 *  `allocateSlot` 的槽位探测、`hub/smoke` 的就绪复核都会跟着挂（实测
 *  `portOwnedBy(pid, 闭端口)` 挂死 >5s；`tests/training-port-reclaim.test.ts` 的「真监听」用例恒红）。
 */
export const PORT_PROBE_TIMEOUT_MS = 500

/** 端口是否在监听：原生 TCP 连接探测，**带上界**（见 `PORT_PROBE_TIMEOUT_MS`）。
 *
 *  超时的那一份连接如果**事后**还是连上了，就地把它 `end()` 掉——超时只意味着「这次不算数」，
 *  不能把 socket 漏在进程里。
 *
 *  **残余**（写下来免得下次当 bug 查）：超时后那次 connect 如果**永远不返回**（内核丢 SYN），
 *  它占的那一个 fd 要等内核自己放弃（~2min）才释放；它不再阻塞**任何调用方**，只是这段时间
 *  占一个句柄。为什么不在超时就销毁：`Bun.connect` 给的是“连上后的 promise”，没有取消句柄；
 *  要真取消得换 `node:net` 的 `socket.destroy()`——与本文件「全 Bun 原生 API、无平台分支」的
 *  模块头纪律冲突，故留此残余（触发条件是擕 SYN 的主机，那里控制台本来就不该跑满频率探测）。
 */
export async function portListen(
  port: number,
  host = '127.0.0.1',
  timeoutMs = PORT_PROBE_TIMEOUT_MS,
): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const probe = (async (): Promise<boolean> => {
    try {
      const conn = await Bun.connect({ hostname: host, port, socket: { data() {} } })
      conn.end()
      return true
    } catch {
      return false
    }
  })()
  const bounded = new Promise<boolean>((resolve) => {
    timer = setTimeout(() => resolve(false), timeoutMs)
  })
  try {
    return await Promise.race([probe, bounded])
  } finally {
    if (timer !== undefined) clearTimeout(timer)
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

/** 读一个 pid 的命令行（**kill 前的身份联锁**：PID 会被复用）。
 *
 *  POSIX：`ps -o command= -p`；Windows：`pwsh` + `Get-CimInstance Win32_Process`（仓库只允许
 *  `pwsh`，见 AGENTS §17.7）。读不到 / 命令不可用 ⇒ 空串（调用方按「身份不明」处理，**不杀**）。
 */
export function pidCommandLine(pid: number): string {
  if (!pid) return ''
  try {
    if (process.platform === 'win32') {
      const r = Bun.spawnSync(
        [
          'pwsh',
          '-NoProfile',
          '-NonInteractive',
          '-Command',
          `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").CommandLine`,
        ],
        { stdout: 'pipe', stderr: 'ignore', windowsHide: true },
      )
      return r.stdout.toString('utf8').trim()
    }
    const r = Bun.spawnSync(['ps', '-o', 'command=', '-p', String(pid)], {
      stdout: 'pipe',
      stderr: 'ignore',
      windowsHide: true,
    })
    return r.stdout.toString('utf8').trim()
  } catch {
    return ''
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

/** 停止单个 PID **及其整棵进程树**。
 *
 *  只有「自身还带一个子进程监督器」的组件需要它（localWorker 跑云端同款
 *  `remote.worker`：父 supervise_worker + 子 worker_loop）——只杀父进程会留下仍在
 *  轮询 hub 抢 job 的孤儿，「随时启停」就形同虚设。判定唯一来源见
 *  `core/types.ts::COMPONENT_KILL_TREE`（本函数不自作主张选组件）。
 *
 *  Windows：`taskkill /T /F`（SIGTERM 在 Windows 上没有进程树语义）；POSIX：spawnBg
 *  的 `detached` 让被启动进程自成进程组（setsid），但**先校验 pgid === pid** 才敢
 *  组杀（否则可能误伤同组的控制台自己），核对不上就退回单进程 stop。任何一步失败都
 *  退化为 killPid(pid)：保守 = 至少把登记的那个进程停掉。 */
export async function killPidTree(pid: number): Promise<boolean> {
  if (!pid) return true
  try {
    if (process.platform === 'win32') {
      Bun.spawnSync(['taskkill', '/F', '/T', '/PID', String(pid)], {
        stdout: 'ignore',
        stderr: 'ignore',
        windowsHide: true,
      })
      if (!pidAlive(pid)) return true
    } else {
      const pgid = Number.parseInt(
        Bun.spawnSync(['ps', '-o', 'pgid=', '-p', String(pid)], { windowsHide: true })
          .stdout.toString()
          .trim(),
        10,
      )
      if (Number.isInteger(pgid) && pgid === pid) {
        const signal = (sig: 'SIGTERM' | 'SIGKILL'): void => {
          try {
            process.kill(-pid, sig)
          } catch {
            /* 组已空/已死 */
          }
        }
        signal('SIGTERM')
        const gone = await waitUntil(() => Promise.resolve(!pidAlive(pid)), 3000, 100)
        if (!gone) signal('SIGKILL')
        if (!pidAlive(pid)) return true
      }
    }
  } catch {
    /* 落到下面的单进程兜底 */
  }
  return killPid(pid)
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

/** Tailscale CGNAT 段 100.64.0.0/10（100.64.0.0 – 100.127.255.255）。 */
function isTailscaleV4(ip: string): boolean {
  const m = /^100\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(ip)
  if (!m) return false
  const second = Number(m[1])
  return second >= 64 && second <= 127
}

/** 本机 tailnet IPv4（Tailscale 网卡地址）；未接入 tailnet / 解析不到 → ''。
 *
 *  2026-09-16：pull 模式改用 tailnet 直连——cloudflared 回源会把**所有**云端流量
 *  归成 127.0.0.1，hub 的 D9 闭锁（5 次鉴权失败封 IP 3600s）一封就把训练主循环
 *  连坐掉（x3-step 事故：训练循环连续 403 自杀退出、云机空转）。走 tailnet 后
 *  每台云机是独立 IP，不再互相连坐，也少一跳公网。 */
export function tailscaleIp(): string {
  let ifaces: Record<string, unknown[] | undefined> = {}
  try {
    ifaces = networkInterfaces() as unknown as Record<string, unknown[] | undefined>
  } catch {
    return ''
  }
  for (const list of Object.values(ifaces)) {
    for (const ni of list ?? []) {
      const e = ni as { family?: string | number; address?: string }
      if ((e.family === 'IPv4' || e.family === 4) && e.address && isTailscaleV4(e.address))
        return e.address
    }
  }
  return ''
}

/** 只读动作门控（局域网只读边界）：写动作（POST）仅限回环来源，其余方法（查看）一律放行。
 *  服务端唯一门控点（`console/server.ts` 的 fetch 首行）——把「方法 + 来源」的判定抽成纯函数，
 *  好让「LAN 的 POST 必 403」成为可回归测试的断言（fail closed：来源不可得 = 非回环 → 拒）。 */
export function isReadonlyAction(method: string, ip: string | null | undefined): boolean {
  return method === 'POST' && !isLoopbackAddress(ip)
}
