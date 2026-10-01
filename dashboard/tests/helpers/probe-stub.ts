/** probe-stub.ts — 控制台单测的**悬挂探测桩**（机群级探测的计数 + 闸门）。
 *
 *  另提供「重算确实在飞」的**事件屏障**（`nextProbe`）：用定值 sleep 猜「放闸后重算该到了」
 *  既慢又不稳——等一次真实探测的注册既立刻兑现也把因果钉住（2026-10-01）。
 *
 *  为什么需要它：动作路径改成 SWR（陈旧先给旧值 + 重算丢后台）之后，「第一帧没有冷算」不再
 *  等于「探测计数不涨」——后台重算照样发探测。真正的判据是：把探测**悬挂不决**，看这一帧还
 *  回不回得来（退回硬清的实现会挂死，所以调用方一律配一个 ~1s 上限的 `Promise.race`，把回归
 *  变成一条明确的红，而不是干等 bun 的用例超时）。
 *
 *  细口径：
 *    · 只数两类**机群级**探测：节点 `/v1/ping`、hub `/admin/*`；
 *    · 默认**即时应答**（不真去 ping 夹具节点——那会把真实连接窗口（乃至 DNS）的墙钟带进用例，
 *      而本桩要测的是「等不等它」，不是它多快）；
 *    · `hold()` 后所有探测悬挂不决；`release()` 放闸让后台重算落地（**并且不再拦后续探测**）；
 *      `stop()` 还原原 fetch。
 *
 *  ★ `release()` 必须**关掉闸门**而不只是放行当前那批：后台重算里的探测注册时刻取决于它前面的
 *  `await`（例：`localCodeHash` 的 async import）——只放行已注册的那批，会让**放闸之后**才注册的
 *  探测被永久悬挂（实测踩到：池视图用例从绿变红，重算永远不落地）。
 */

export interface ProbeStub {
  pings: () => number
  hub: () => number
  hold: () => void
  release: () => void
  stop: () => void
  /** 等「**自上一次 hold() 起**注册的第一个探测」（= 重算确实在飞）。已在飞的不算失败——
   *  口径是计数而非时刻，所以同步派发的探测也能兑现（调用方不必关心重算内部的 await 次序）。
   *  只能配 hold() 用：没有 hold 就没有「该等的那一拍」。 */
  nextProbe: () => Promise<void>
}

export function probeStub(): ProbeStub {
  const orig = globalThis.fetch
  const body = JSON.stringify({ codeHash: 'deadbeef', cpus: 4 })
  const response = (): Response => new Response(body, { status: 200 })
  const pending: Array<() => void> = []
  /** 自上一次 hold() 起注册的探测数 + 等它的兑现者（见 nextProbe 口径）。 */
  const probeWaiters: Array<() => void> = []
  let probesSinceHold = 0
  let pings = 0
  let hub = 0
  let hold = false
  globalThis.fetch = ((url: unknown, init?: RequestInit) => {
    void init
    const u = String(url)
    if (u.endsWith('/v1/ping')) pings++
    if (u.includes('/admin/')) hub++
    probesSinceHold++
    for (const r of probeWaiters.splice(0)) r()
    if (!hold) return Promise.resolve(response())
    return new Promise<Response>((resolve) => pending.push(() => resolve(response())))
  }) as typeof fetch
  return {
    pings: () => pings,
    hub: () => hub,
    hold: (): void => {
      hold = true
      probesSinceHold = 0 // 新一回合：只数这之后的探测
    },
    release: (): void => {
      hold = false // 关闸：之后注册的探测即时应答（见文件头）
      probesSinceHold = 0
      for (const r of pending.splice(0)) r()
    },
    stop: () => {
      hold = false
      for (const r of pending.splice(0)) r()
      for (const r of probeWaiters.splice(0)) r() // 别让等在新一拍的调用方挂死
      globalThis.fetch = orig
    },
    nextProbe: (): Promise<void> => {
      if (probesSinceHold > 0) return Promise.resolve()
      return new Promise<void>((resolve) => probeWaiters.push(resolve))
    },
  }
}

/** ~1s 上限的 race 守卫：等待被桩悬挂的探测的实现会在这里**明确报错**（而不是挂死用例）。
 *  `msg` 按场景传（同一个上限、不同的「谁该来却没来」）。 */
export function guardMs(msg = '这一帧仍在等机群级探测（硬清回归？）'): {
  promise: Promise<never>
  done: () => void
} {
  let timer: ReturnType<typeof setTimeout> | null = null
  const promise = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(msg)), 1000)
  })
  return {
    promise,
    done: (): void => {
      if (timer) clearTimeout(timer)
    },
  }
}
