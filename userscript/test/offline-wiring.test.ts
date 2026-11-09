import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * offline-wiring.test.ts — 「离线训练开始 → 上报 dashboard」的接线守卫。
 *
 * 这一阶段只做上报：userscript 检测 battle.offline 的训练 cell 真正开始执行，经现有
 * `/api/notebookOp` 审计通道上报；dashboard 不发停止命令。这里钉住三件事：
 *   ① frame 检测到离线页 / 开始执行都要经桥发 note；
 *   ② host 与 single 都落到 op 日志，且带上本页 stopOthers 开关；
 *   ③ 开关只在识别到离线页后显示，默认关。
 */
const BOOT = readFileSync(join(import.meta.dir, '../src/notebook-companion/app/boot.ts'), 'utf8')
const PANEL = readFileSync(join(import.meta.dir, '../src/notebook-companion/ui/panel.ts'), 'utf8')
const CONFIG = readFileSync(join(import.meta.dir, '../src/notebook-companion/config.ts'), 'utf8')

describe('离线训练上报：接线', () => {
  it('frame 检测到离线页 / 训练开始都经桥上报', () => {
    expect(BOOT.match(/bridge\.note\('offline:detected'/g)?.length ?? 0).toBe(1)
    expect(BOOT.match(/bridge\.note\('offline:cell-start'/g)?.length ?? 0).toBe(1)
  })

  it('host 与 single 都落 op 日志，并带本页 stopOthers 开关', () => {
    expect(BOOT.match(/op\('offline:cell-start'/g)?.length ?? 0).toBe(2)
    expect(BOOT.match(/loadStopPpo\(runtime, page, false\)/g)?.length ?? 0).toBe(4)
    expect(BOOT.match(/void ops\.flush\(\)/g)?.length ?? 0).toBe(2)
  })

  it('面板选项默认关；只在识别到离线页后显示', () => {
    expect(PANEL).toContain('启动后停止其它 ppo worker')
    expect(PANEL).toContain('data-ppo-group')
    expect(BOOT.match(/panel\?\.setStopPpoVisible\(true\)/g)?.length ?? 0).toBe(2)
  })

  it('开关按页存（同站多页互不影响）', () => {
    expect(CONFIG).toContain("STOP_PPO_KEY_PREFIX = 'bnc:stopPpo:'")
    expect(CONFIG).toContain('export function loadStopPpo')
    expect(CONFIG).toContain('export function saveStopPpo')
  })
})
