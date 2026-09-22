/**
 * server-api-course-switch.test.ts — 切课程不得重做**机群级**探测（2026-09-22 用户报障）
 *
 * 分层：src/server/api/state-view.ts + snapshot-cache.ts + snapshot-refresher.ts
 *
 * 症状（用户原话）：「多课程并行训练时，dashboard 切换课程需要几秒钟的延迟才能显示
 * 选中课程的数据指标和趋势图」。
 *
 * 根因：慢快照**整体**按课程键控，而快照里的节点 ping（超时 1.5s）、共享 hub 观测面
 * （超时 1.2s）、push 机群探活与「在看哪门课」**无关**——切到一门没看过的课 =
 * 把这些探测原地重做一遍（本机实测冷 2883ms vs 暖 2ms）。
 *
 * 本用例钉住两件事：
 *   ① 换课程**不再**新增机群级探测（探测计数不涨）——这是「瞬间切换」的来源；
 *   ② 课程级部分**仍然是按新课算的**（组件日志尾路径指向新课程）——不能靠「整块复用
 *      上一门课的快照」把延迟换成串数据。
 *
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { guardMs, probeStub } from './helpers/probe-stub'
import { api, readConfigText, scratchConfig } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

/** 空账本（没有任何组件在跑）→ 课程级重算里也不会有 HTTP 探测，计数才可断言。 */
function withEmptyRegistry(): { restore: () => void } {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-switch-'))
  const file = path.join(dir, 'registry.json')
  writeFileSync(file, '{}')
  const prev = process.env.BCITY_REGISTRY_FILE
  process.env.BCITY_REGISTRY_FILE = file
  return {
    restore: () => {
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
      rmSync(dir, { recursive: true, force: true })
    },
  }
}

describe('console/api 切课程（机群级探测跨课程共用）', () => {
  it('换课程零新增机群级探测（节点 ping / hub 观测面），但课程级部分按新课算', async () => {
    const reg = withEmptyRegistry()
    api.invalidateSlowSnapshot()
    api.invalidateHubAdmin()
    const probe = probeStub()
    try {
      const a = await api.buildStateView('course-a')
      // 冷算那一下确实探了（节点 ping + hub 观测面）——否则本用例是空转
      expect(probe.pings()).toBeGreaterThan(0)
      expect(probe.hub()).toBeGreaterThan(0)
      const coldPings = probe.pings()
      const coldHub = probe.hub()

      // ★ 切换课程：没看过的课，此前会在这里把上面两笔探测整个重做一遍（2-3s）
      const b = await api.buildStateView('course-b')
      expect(b.course).toBe('course-b')
      expect(probe.pings()).toBe(coldPings)
      expect(probe.hub()).toBe(coldHub)

      // 课程级部分**没有**被串：组件日志尾按新课程解析（复用旧课整块快照是最省事、
      // 也最错的做法——那会把「秒级延迟」换成「显示别课数据」）。
      const logOf = (s: Awaited<ReturnType<typeof api.buildStateView>>): string =>
        s.components.find((c) => c.key === 'trainingLoop')?.log ?? ''
      expect(logOf(a)).toContain('course-a')
      expect(logOf(b)).toContain('course-b')
      expect(logOf(a)).not.toBe(logOf(b))

      // 切回去也不重探（课程级快照在 TTL 内仍命中）
      await api.buildStateView('course-a')
      expect(probe.pings()).toBe(coldPings)
    } finally {
      probe.stop()
      reg.restore()
      api.invalidateSlowSnapshot()
      api.invalidateHubAdmin()
    }
  })

  it('动作后的第一帧也不冷算：软作废先给旧值 + 重算落后台，结构改动即时上屏', async () => {
    const reg = withEmptyRegistry()
    const prev = readConfigText() // 夹具种子的原始文本（用例末尾字节级还原）
    api.invalidateSlowSnapshot()
    api.invalidateHubAdmin()
    const probe = probeStub()
    try {
      const warm = await api.buildStateView('course-a')
      const warmPings = probe.pings()
      const warmHub = probe.hub()
      expect(warmPings).toBeGreaterThan(0)
      expect(warmHub).toBeGreaterThan(0)

      // 动作：**结构改了**（新登记一个 push 节点）——这是「动作结果要即时上屏」的那一半。
      const cfg = JSON.parse(prev) as Record<string, unknown>
      cfg.nodes = [
        ...((cfg.nodes as unknown[]) ?? []),
        {
          id: 'action-probe-node',
          url: 'https://127.0.0.1:1',
          authKey: 'k',
          concurrency: 1,
          enabled: true,
          gpu_push: true,
        },
      ]
      writeFileSync(scratchConfig, JSON.stringify(cfg, null, 2))
      api.invalidateAfterAction() // 生产路径（server.ts 的 invalidatesSnapshot 分支）

      // ★ 闸门：此后所有探测悬挂不决。前实现（硬清）会在第一帧原地等它们
      //   （节点 ping 1.5s + hub 1.2s 各一轮），于是这里的 1s 上限把它变成一条明确的红。
      probe.hold()
      const guard = guardMs()
      const after = await Promise.race([api.buildStateView('course-a'), guard.promise])
      guard.done()
      // ……而动作结果照样即时上屏：新登记的节点在**这一帧**就在执行面里（结构现算，
      // 不经机群级缓存——软作废只换探测列，不换结构）。
      expect(after.pushFleet?.nodes ?? 0).toBeGreaterThan(warm.pushFleet?.nodes ?? 0)
      expect(after.pushFleet?.mode ?? 'pull').not.toBe('pull')

      // 软作废 ≠ 不作废：重算已经在第一帧读缓存时起跑（计数已涨），放闸让它落地，
      // 免得悬挂的探测泄漏到后序用例。
      probe.release()
      expect(probe.pings()).toBeGreaterThan(warmPings)
    } finally {
      probe.stop()
      writeFileSync(scratchConfig, prev)
      reg.restore()
      // 硬清（= 测试夹具的归零手段；生产路径的动作走 invalidateAfterAction）
      api.invalidateSlowSnapshot()
      api.invalidateHubAdmin()
    }
  })

  it('视图态动作不作废快照缓存（切课自己发的 setCourse 不在其列）', () => {
    // ★ 这是「切课卡顿」的另一半：`setCourse` 是切课自己发出的动作，只写 console-state；
    //   此前 server.ts 对**所有**动作一律作废慢快照 + hub 观测面 ⇒ 每切一次课都把机群级
    //   探测丢掉重做。判据是纯函数，直接在门禁里钉住（不再靠「server.ts 那行还在不在」）。
    expect(api.invalidatesSnapshot('setCourse')).toBe(false)
    expect(api.invalidatesSnapshot('getGateHaltMode')).toBe(false)
    // 真正改状态的动作照旧作废
    expect(api.invalidatesSnapshot('start')).toBe(true)
    expect(api.invalidatesSnapshot('stopCourse')).toBe(true)
    expect(api.invalidatesSnapshot('setNodeEnabled')).toBe(true)
    expect(api.invalidatesSnapshot('registerPushWorker')).toBe(true)
  })
})
