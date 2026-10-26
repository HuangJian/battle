/**
 * server-api-first-paint.test.ts — 首帧渐进渲染（2026-10-25）
 *
 * 契约（`state-view.BuildStateOptions.firstPaint` + `snapshot-refresher.getFirstPaintSnapshot`）：
 *   ① **请求不等探测**：冷缓存的页面首帧必须是毫秒级——结构（账本/registry/课程/指标）
 *      现算上屏，节点 ping / 组件健康 / hub 应答留在后台；
 *   ② **未知不是离线**：这一帧的探测列带着 `probesPending`，节点 `online = null`、
 *      贡献数 `-1`，UI 据此画灰「探测中」而不是红「离线」；
 *   ③ 首帧**不污染**后续语义：后台算落地后，`/api/state` 拿到的就是真值（`probesPending` 消失）。
 *
 * 为什么用「永不 resolve 的 fetch」而不是计时：墙钟在忙机上必抖，而上面①的**定义**就是
 * 「不依赖探测返回」——探测永不返回时请求仍能返回，才是被钉住的那条性质。
 */

import { afterEach, describe, expect, it } from 'bun:test'
import { api } from './helpers/console-fixture'

afterEach(() => {
  // 硬清 + 复位「刚刚有人看」：不把夹具的挂起探测/空闲态漏给别的用例。
  // ★ 首帧会把 hub 探测也丢到后台（`getHubAdmin`）——它也得清：本文件故意用「永不返回的
  //   fetch」，「在飞」的那次 hub 探测会一直挂着；不清的话下一条用例 await 它 = 白等满超时。
  api.invalidateSlowSnapshot()
  api.invalidateHubAdmin()
  api.markConsoleActivity()
})

/** 挂起的 `fetch`（永不 resolve）：任何**await 探测**的路径都会卡死 ⇒ 用例以此证明「不等」。 */
function hangFetch(): { calls: () => number; restore: () => void } {
  const orig = globalThis.fetch
  let n = 0
  globalThis.fetch = (() => {
    n += 1
    return new Promise(() => undefined)
  }) as unknown as typeof fetch
  return { calls: () => n, restore: () => (globalThis.fetch = orig) }
}

describe('首帧渐进渲染：结构先上屏，探测后台刷新', () => {
  it('冷缓存首帧：探测永不返回也照样给出完整结构，且探测列标为未知', async () => {
    api.invalidateSlowSnapshot()
    api.markConsoleActivity()
    const hang = hangFetch()
    let view: Awaited<ReturnType<typeof api.buildStateView>>
    try {
      view = await api.buildStateView(undefined, { firstPaint: true })
    } finally {
      hang.restore() // 后台那一算还挂着，丢掉即可（没有 timer ⇒ 不拖住进程）
    }
    // ① 结构齐全（不是空壳）：课程/组件/节点/阶段都在，来自现算而不是探测
    expect(view.courses.length).toBeGreaterThan(0)
    expect(view.components.length).toBeGreaterThan(0)
    expect(view.nodes.length).toBeGreaterThan(0)
    expect(view.course).toBeTruthy()
    // ② 探测列 = 未知（`null`），不是 false
    expect(view.probesPending).toBe(true)
    for (const n of view.nodes) expect(n.online).toBeNull()
    // 贡献数来自探测层（同一拍）⇒ 这一帧是 -1（UI 不得把它当 0 局）
    for (const n of view.nodes) expect(n.lastContrib).toBe(-1)
    // ③ hub 观测面不等：首帧给 null（下一拍轮询填上），且不冒充「滞留」
    expect(view.overview).toBeNull()
    expect(view.workerRegistry).toBeNull()
    expect(view.offlinePush).toBeNull()
    expect(view.ppoQueueStall).toBeNull() // 「不可知」不许得出「滞留」结论
  })

  it('后台算落地后：普通快照是真值（probesPending 消失）', async () => {
    api.invalidateSlowSnapshot()
    api.markConsoleActivity()
    const orig = globalThis.fetch
    globalThis.fetch = (() =>
      Promise.resolve(
        new Response(JSON.stringify({ codeHash: 'x', cpus: 4 }), { status: 200 }),
      )) as unknown as typeof fetch
    try {
      const first = await api.buildStateView(undefined, { firstPaint: true })
      expect(first.probesPending).toBe(true)
      // 让丢后台的那一算跑完（fetch 即时返回，两拍微任务足够）
      await new Promise((r) => setTimeout(r, 50))
      const settled = await api.buildStateView()
      expect(settled.probesPending).toBeFalsy()
      // 真探过：`online` 有结论（布尔），不再是「未知」
      expect(settled.nodes.every((n) => typeof n.online === 'boolean' || n.online === null)).toBe(
        true,
      )
      expect(settled.nodes.some((n) => n.online !== null)).toBe(true)
    } finally {
      globalThis.fetch = orig
    }
  })
})
