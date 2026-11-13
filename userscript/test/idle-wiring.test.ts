/**
 * idle-wiring.test.ts — 「空闲自动停会话」的接线守卫。
 *
 * 选项在伴生脚本面板上，但判据只能来自 dashboard 的 `/api/hubQueue`（页面不碰 hub 地址/token，
 * 也不扩大 `@connect`）。这里钉住四件事：
 *   ① 两个角色（Kaggle host / 单文档站点）都挂了判决器与定时拍；
 *   ② 停会话前一定走倒计时（可取消），且每一步都有 op 证据；
 *   ③ 面板开关是**全局**一个键（不按页），默认关；
 *   ④ 判据只问回环 dashboard，不带自定义头之外的东西。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(import.meta.dir, '../src/notebook-companion')
const BOOT = readFileSync(join(SRC, 'app/boot.ts'), 'utf8')
const PANEL = readFileSync(join(SRC, 'ui/panel.ts'), 'utf8')
const CONFIG = readFileSync(join(SRC, 'config.ts'), 'utf8')
const IDLE = readFileSync(join(SRC, 'app/idle-stop.ts'), 'utf8')

const count = (text: string, re: RegExp): number => text.match(re)?.length ?? 0

describe('空闲停会话：接线', () => {
  it('两个角色都挂了判决器 + 定时拍，卸载时都收掉', () => {
    expect(count(BOOT, /createIdleStop\(\{/g)).toBe(2)
    expect(count(BOOT, /idleStop\.poll\(\)/g)).toBe(2)
    expect(count(BOOT, /clearInterval\(idleTimer\)/g)).toBe(2)
    // 面板改完立刻生效：开关每拍从存储读（不是创建时快照）——面板初值 + 判决器各一次 × 两角色
    expect(count(BOOT, /loadIdleStop\(runtime, false\)/g)).toBe(4)
    // 关掉开关 / 取消倒计时都要重新武装（否则那次判决之后再也不会喊）：每角色两处
    expect(count(BOOT, /idleStop\?\.reset\(\)/g)).toBe(4)
    // 两角色都按「本页是不是离线训练页」把 live hold 算成活（否则整课在跑时会掐掉会话）
    expect(count(BOOT, /autonomous: \(\) => offlineDetected/g)).toBe(2)
  })

  it('停会话前有倒计时（可取消），每一步都留 op 证据', () => {
    expect(count(BOOT, /IDLE_CONFIRM_SEC/g)).toBe(3) // import + 两处 countdown
    expect(count(BOOT, /panel\.countdown\(`即将停会话/g)).toBe(2)
    expect(count(BOOT, /op\('idleStop:due'/g)).toBe(2)
    expect(count(BOOT, /op\('idleStop:cancel'/g)).toBe(2)
    expect(count(BOOT, /op\('idleStop:stop'/g)).toBe(2)
    // 证据先排空：会话停掉之后这份日志可能没人再搬
    expect(count(BOOT, /await ops\.flush\(\)/g)).toBe(2)
    // 后台页弹不出倒计时 ⇒ 预授权执行（与调度器 confirm 同口径）
    expect(count(BOOT, /doc\.hidden \|\| !panel/g)).toBe(2)
  })

  it('面板开关：默认关、全局一个键、文案说清判据', () => {
    expect(PANEL).toContain('空闲自动停会话')
    expect(PANEL).toContain('data-idle="enabled"')
    expect(PANEL).toContain('idleEnabled.checked = options.idleStop === true')
    expect(PANEL).toContain('onIdleStopChange')
    expect(CONFIG).toContain("IDLE_STOP_KEY = 'bnc:idleStop'")
    // 不按页：开关函数带 page 参数就是在分桶（这里是反例守卫）
    expect(CONFIG).not.toMatch(/idleStopKeyFor/)
  })

  it('判据只问回环 dashboard（不扩大 @connect、不碰 hub token）', () => {
    expect(IDLE).toContain('/api/hubQueue')
    expect(IDLE).toContain("'X-Notebook-Companion': '1'")
    expect(IDLE).toContain('IDLE_WINDOW_MS')
    expect(IDLE).toContain('UNREACHABLE_ATTEMPTS')
    // 在飞也算活：缺这一条会把「本机正在算」读成「没活」
    expect(IDLE).toContain('facts.pendingUnheld + facts.inflight > 0')
    // 自主盘整课在跑时 hub 队列静默：live hold 必须算活（缺了会掐掉在训会话）
    expect(IDLE).toContain('facts.heldLive > 0 && (deps.autonomous?.() ?? false)')
  })
})
