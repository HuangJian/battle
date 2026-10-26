/**
 * dead-endpoint.ts — 「探不通，但**现在就**失败」的端点（测试夹具）。
 *
 * ## 为什么需要它
 *
 * 夹具里「不可达的主机」一直写作 `http://127.0.0.1:1`（没人监听的端口），前提是
 * 「内核立刻回 RST ⇒ fetch 毫秒级失败」。这个前提**不是所有主机都成立**：
 * 丢回环 SYN 的内核上（本仓 Linux 沙箱实测），connect 既不成功也不报错，fetch 一路挂到
 * 调用方自己的 `AbortSignal.timeout` 上界 —— 单条用例白等 1.5s / 3s / 5s。
 * 实测（2026-10-25，本仓 `bun run test`）：用例时长总和 83s，其中 60s+ 是这类超时；
 * 最狠的 `cloud-halt.test.ts` 一个文件 4 条用例 × 5s = 20s（= 全目录墙钟 21s 的唯一长杆）。
 *
 * 同一问题在本仓已有一份局部解（`server-actions-worker-register.test.ts` 的
 * `fakeWorkerReject`：起一台回 401 的假 worker）——本文件把它提升为共享原语。
 *
 * ## 做法
 *
 * 一个只 listen、**accept 后立刻 destroy** 的 TCP 端点：客户端拿到 `ECONNRESET`
 * （实测 1–3ms），对被测代码而言与「连接被拒」走**同一条**失败分支（fetch 抛错）——
 * 所以 `hub 不可达：<e.message>` 这类文案断言照旧成立。对 https 也一样（TLS 握手被 reset）。
 *
 * ## 边界
 *
 *  · 每个 worker 进程一份（`--parallel` 下各自 listen 在临机端口，不会撞固定端口）；
 *  · `unref()` 掉监听句柄：不拖住事件循环，进程该退就退；
 *  · 端口随进程生灭 ⇒ 需要**地址**的夹具请在写配置时调用 `deadPort()` / `deadHttpUrl()`，
 *    不要把它当固定端口缓存到别的进程去用。
 */
import { createServer } from 'node:net'

const srv = createServer((sock) => sock.destroy())
const port = await new Promise<number>((resolve, reject) => {
  srv.once('error', reject)
  srv.listen(0, '127.0.0.1', () => {
    const addr = srv.address()
    if (addr === null || typeof addr === 'string') {
      reject(new Error('dead-endpoint: 无法在回环上监听临机端口'))
      return
    }
    resolve(addr.port)
  })
})
// 不拖住事件循环：测试跑完就退，句柄不必显式 close。
srv.unref()

/** 进程内复用的「立刻失败」端点端口（模块加载时启动）。 */
export function deadPort(): number {
  return port
}

/** 「立刻失败」的 HTTP 端点（`http://127.0.0.1:<port>`）。 */
export function deadHttpUrl(): string {
  return `http://127.0.0.1:${deadPort()}`
}

/** 「立刻失败」的 HTTPS 端点（TLS 握手被 reset，同样毫秒级）。 */
export function deadHttpsUrl(): string {
  return `https://127.0.0.1:${deadPort()}`
}
