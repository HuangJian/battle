/**
 * server-api-pool-swr.test.ts — 池视图（/api/pool）的 SWR 语义（2026-09-22）
 *
 * 分层：src/server/api/pool.ts + snapshot-refresher.ts（动作后的缓存处置）
 *
 * 症状（用户指令）：「把池视图的结构与探测拆成两层，让节点编辑真的第一帧就上屏」——
 * 这个视图曾把两件快慢差三个数量级的事装在一条缓存里：**结构**（节点行的 enabled/status、
 * local 槽数，来自 cfg，毫秒级）与**探测**（逐节点 ping 2.5s 超时 ∥ + 池历史聚合 + codeHash，
 * 冷算实测 2448–2552ms）。于是无论怎么处置那条缓存都不对：不碰 ⇒ 池表还显示旧状态最多
 * 30s（TTL）/5min（面板轮询）而同一页的注册表行已写「已停用」；硬清 ⇒ 面板被按住 2.5s；
 * 整条软作废 ⇒ 首帧给的还是旧状态（要等后台重算，实测 ~2.5s）。
 *
 * 修法 = 拆两层：**探测层**（`PoolProbes`，单条目全局 SWR——它全是机器事实，与看哪门课无关）
 * ⊕ **结构层**（`assemblePoolView` 每请求现算）。
 *
 * 本用例钉住三条：
 *   ① 动作后第一读就拿到**新结构**（节点已 disabled、local 槽数已变）且**不等探测**；
 *   ② 同一次读里**探测列仍是旧的那一份**（`cachedAt` 未推进）——两层真的拆开了，不是
 *      “先给旧值碰巧看着新”；且重算已在后台跑（悬挂桩下计数照走）；
 *   ③ 显式 `?fresh=1` 仍是**硬清 + 等重算**（旧语义不退化：手动刷新就是要等一次新值）。
 *
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts；悬挂探测桩见
 * ./helpers/probe-stub.ts。
 */

import { guardMs, probeStub } from './helpers/probe-stub'
import { api, loadConfig, scratchConfig } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'
import { writeFileSync } from 'fs'

/** 改 seed 配置：第一个节点的 enabled（池行 status 由它决定 = **结构**）+ 本机槽数。 */
function patchConfig(enabled: boolean, localSlots?: number): string {
  const cfg = loadConfig()
  cfg.nodes[0]!.enabled = enabled
  if (localSlots !== undefined) cfg.rl.local_slots = localSlots
  writeFileSync(scratchConfig, JSON.stringify(cfg, null, 2))
  return cfg.nodes[0]!.id
}

/** 让出事件循环一拍（`setTimeout 0`）：给后台重算的 I/O 回调一次推进机会。这**不是**「等墙钟」
 *  ——循环的退出判据仍是可观测的 `cachedAt` 推进（落地即退），只是不拿定值步长去猜它要多久。 */
const tick = (): Promise<void> => new Promise((r) => setTimeout(r, 0))

describe('console/api /api/pool（结构现算 ⊕ 探测 SWR）', () => {
  it('节点编辑第一帧就上屏（结构现算、不等探测），探测列才落后台重算', async () => {
    const prev = loadConfig()
    const probe = probeStub()
    try {
      const nodeId = patchConfig(true, 0)
      api.invalidatePoolViews()
      const warm = await api.buildPoolView(false)
      expect(warm.nodes.find((n) => n.id === nodeId)?.status).not.toBe('disabled')
      expect(warm.local?.spec).toBe('0 槽')
      const warmAt = warm.cachedAt
      const warmOk = warm.nodes.find((n) => n.id === nodeId)?.ok

      // 动作：**结构改了**（停用该节点 + 本机槽数 0 → 3）→ 生产路径的缓存处置
      patchConfig(false, 3)
      api.invalidateAfterAction()

      // ★ 第一读：探测全部悬挂也**必须**立刻回来（硬清会在这里挂死 → 由 guard 明确报红）
      probe.hold()
      const guard = guardMs()
      const first = await Promise.race([api.buildPoolView(false), guard.promise])
      guard.done()
      // ① 结构 = 当下 cfg：第一帧就是新的（这正是「节点编辑真的第一帧就上屏」）
      expect(first.nodes.find((n) => n.id === nodeId)?.status).toBe('disabled')
      expect(first.local?.spec).toBe('3 槽')
      // ② 探测层还是旧的那一份（客户端靠 `cachedAt` 推进判定「新的一份到了」）
      expect(first.cachedAt).toBe(warmAt)
      // 停用行的历史列仍来自旧探测（探测列允许粗一个重算周期）
      expect(first.nodes.find((n) => n.id === nodeId)?.ok).toBe(warmOk)
      // 重算已在后台起跑（它发的 ping 计得到数）
      expect(probe.pings()).toBeGreaterThan(0)

      // 放闸 → 后台重算落地（NodeStats 的有界再校验就是按同一条判据轮询；这里同样有界，
      // 不做定值 sleep）
      probe.release()
      let fresh = first
      // 按**拍**有界（200 拍 ≫ 重算的 await 跳数）：条件是可观测的，落地即退——
      // 换成 20ms 定值步长则是每轮白等半拍，而定步长本身也不能证明「到了」
      for (let i = 0; i < 200 && fresh.cachedAt === warmAt; i++) {
        await tick()
        fresh = await api.buildPoolView(false)
      }
      expect(fresh.cachedAt).not.toBe(warmAt)
      expect(fresh.nodes.find((n) => n.id === nodeId)?.status).toBe('disabled') // 结构仍是新的
    } finally {
      probe.stop()
      writeFileSync(scratchConfig, JSON.stringify(prev, null, 2))
      api.invalidatePoolViews()
    }
  })

  it('显式 ?fresh=1 仍是硬清 + 等重算（手动刷新要的是新值，不是先给旧的）', async () => {
    const prev = loadConfig()
    const probe = probeStub()
    try {
      api.invalidatePoolViews()
      const warm = await api.buildPoolView(false)
      probe.hold()
      let resolved = false
      const pending = api.buildPoolView(true)
      void pending.then(() => {
        resolved = true
      })
      // ★ 屏障 = 等**重算自己的探测**注册（事件驱动，不定值 sleep）：
      //   · 硬清 ⇒ 重算在做探测 → 探测先到；
      //   · 软作废回归 ⇒ 立刻兑现旧值 → `pending` 先到，「第一拍就红」（不用等墙钟）。
      const guard = guardMs('硬清读没有起重算（软作废回归？）')
      const first = await Promise.race([
        probe.nextProbe().then(() => 'probe' as const),
        pending.then(() => 'resolved' as const),
        guard.promise,
      ])
      guard.done()
      expect(first).toBe('probe')
      expect(resolved).toBe(false) // 硬清：还在等那一次重算（软作废才会先给旧值）
      probe.release()
      const fresh = await pending
      expect(fresh.cachedAt).toBeGreaterThanOrEqual(warm.cachedAt)
      expect(resolved).toBe(true)
    } finally {
      probe.stop()
      writeFileSync(scratchConfig, JSON.stringify(prev, null, 2))
      api.invalidatePoolViews()
    }
  })
})
