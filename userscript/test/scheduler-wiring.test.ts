import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * scheduler-wiring.test.ts — 接线不可漂（与 dashboard 的 notebook-wiring 同思路）：
 * 这些点在单测里看不见，但漏一处就会让「每页只看得见自己设的任务」静默失效。
 *
 *  ① 两个角色（host / single）都必须把本页标签 `page` 交给调度器（分桶键 + 归属阀）；
 *  ② 操作日志必须写在**唯一出口**上（面板按钮 → `run()` / `send()`），并且用 typed POST 通道；
 *  ③ 调度事件必须接到 op 日志（否则「到点没跑」又变成没痕迹）。
 */
const BOOT = readFileSync(join(import.meta.dir, '../src/notebook-companion/app/boot.ts'), 'utf8')
const OP_LOG = readFileSync(
  join(import.meta.dir, '../src/notebook-companion/app/op-log.ts'),
  'utf8',
)

describe('调度器接线：页面身份 + 操作日志', () => {
  it('两个角色都交 page（host 的 frameSession 与 single 的 loop.session 各一份）', () => {
    expect(BOOT).toContain('identity: () => ({ notebookKey, session: frameSession, page })')
    expect(BOOT).toContain('identity: () => ({ notebookKey, session: loop.session, page })')
    // page 必须来自 sessionStorage 的页标签（跨刷新稳定、页签之间必然不同）
    expect(BOOT.match(/const page = pageTag\(win\)/g)?.length ?? 0).toBe(2)
  })

  it('调度事件 → op 日志：两个角色都挂 onEvent', () => {
    expect(BOOT.match(/onEvent: opEvent,/g)?.length ?? 0).toBe(2)
    expect(BOOT.match(/const opEvent = \(e: SchedulerEvent\)/g)?.length ?? 0).toBe(2)
  })

  it('面板按钮走唯一出口：cmd:<name> 在 host 与 single 的 run() 里各记一条', () => {
    expect(BOOT.match(/op\(`cmd:\$\{name\}`, r\.ok \? 'ok'/g)?.length ?? 0).toBe(2)
  })

  it('操作日志走自己的 typed POST 通道（不污染 cell 搬运的 epoch/offset）', () => {
    expect(OP_LOG).toContain('/api/notebookOp')
    expect(OP_LOG).toContain("'X-Notebook-Companion': '1'")
    // 零状态：不 import 搬运器（不碰 shipper / meta / 游标）
    expect(OP_LOG).not.toContain("from '../log-shipper'")
    expect(OP_LOG).not.toContain('createShipState')
  })

  it('卸下页面时把队列尽量投完（否则「点了但没日志」）', () => {
    expect(BOOT.match(/op\('dispose'\)\n\s+ops\.stop\(\)/g)?.length ?? 0).toBe(2)
  })
})
