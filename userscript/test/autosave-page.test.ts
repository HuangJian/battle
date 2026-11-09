import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * autosave-page.test.ts — 自动保存设置必须按页隔离的接线守卫。
 *
 * 存储层的隔离由 config.test.ts 覆盖；这里钉住 boot 的三个角色都应用本页覆盖，
 * 且面板改动只写本页键（绝不回写全局 bnc:config），防止以后又串页。
 */
const BOOT = readFileSync(join(import.meta.dir, '../src/notebook-companion/app/boot.ts'), 'utf8')

describe('自动保存设置：按页接线', () => {
  it('host / single / frame 都应用本页覆盖', () => {
    expect(BOOT.match(/applyPageAutoSave\(runtime, page, config\)/g)?.length ?? 0).toBe(3)
  })

  it('面板改动只写本页键，绝不回写全局 bnc:config', () => {
    expect(BOOT.match(/saveAutoSave\(runtime, page, \{/g)?.length ?? 0).toBe(2)
    expect(BOOT).not.toContain('saveConfig(runtime, config)')
  })

  it('host attach 下发本页设置，frame 在 startLoop 前应用', () => {
    expect(BOOT).toContain('bridge.setAutoSave({')
    const apply = BOOT.indexOf('if (m.autoSave) loop.setAutoSave(')
    expect(apply).toBeGreaterThan(0)
    expect(apply).toBeLessThan(BOOT.indexOf('startLoop()', apply))
  })
})
