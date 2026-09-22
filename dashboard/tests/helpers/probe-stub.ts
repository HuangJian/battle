/** probe-stub.ts — 控制台单测的**悬挂探测桩**（机群级探测的计数 + 闸门）。
 *
 *  为什么需要它：动作路径改成 SWR（陈旧先给旧值 + 重算丢后台）之后，「第一帧没有冷算」不再
 *  等于「探测计数不涨」——后台重算照样发探测。真正的判据是：把探测**悬挂不决**，看这一帧还
 *  回不回得来（退回硬清的实现会挂死，所以调用方一律配一个 ~1s 上限的 `Promise.race`，把回归
 *  变成一条明确的红，而不是干等 bun 的用例超时）。
 *
 *  细口径：
 *    · 只数两类**机群级**探测：节点 `/v1/ping`、hub `/admin/*`；
 *    · 默认**即时应答**（不真去 ping 夹具里的 `*.fixture.invalid` 节点——那会每条用例多等
 *      两个超时窗口）；
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
}

export function probeStub(): ProbeStub {
  const orig = globalThis.fetch
  const body = JSON.stringify({ codeHash: 'deadbeef', cpus: 4 })
  const response = (): Response => new Response(body, { status: 200 })
  const pending: Array<() => void> = []
  let pings = 0
  let hub = 0
  let hold = false
  globalThis.fetch = ((url: unknown, init?: RequestInit) => {
    void init
    const u = String(url)
    if (u.endsWith('/v1/ping')) pings++
    if (u.includes('/admin/')) hub++
    if (!hold) return Promise.resolve(response())
    return new Promise<Response>((resolve) => pending.push(() => resolve(response())))
  }) as typeof fetch
  return {
    pings: () => pings,
    hub: () => hub,
    hold: (): void => {
      hold = true
    },
    release: (): void => {
      hold = false // 关闸：之后注册的探测即时应答（见文件头）
      for (const r of pending.splice(0)) r()
    },
    stop: () => {
      hold = false
      for (const r of pending.splice(0)) r()
      globalThis.fetch = orig
    },
  }
}

/** ~1s 上限的 race 守卫：等待被桩悬挂的探测的实现会在这里**明确报错**（而不是挂死用例）。 */
export function guardMs(): { promise: Promise<never>; done: () => void } {
  let timer: ReturnType<typeof setTimeout> | null = null
  const promise = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error('这一帧仍在等机群级探测（硬清回归？）')), 1000)
  })
  return {
    promise,
    done: (): void => {
      if (timer) clearTimeout(timer)
    },
  }
}
