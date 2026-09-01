/**
 * restart-guard.test.ts — /v1/restart 防重启循环护栏（2026-09-01 重启循环修复）。
 *
 * 背景：协调器 ping 门 / rescan 周期（~15s）短于「重启 → 新实例起听 → 协调器观察
 * 到新 codeHash」的链路延迟时，新实例刚起听就被下一次 POST /v1/restart 杀掉 →
 * 无限重启循环（实测四连杀），节点永远无法贡献。护栏：进程启动 grace 窗口内
 * 拒绝重启（agent 回 409，协调器 request_upgrade 记失败、下轮 rescan 重试）。
 */
import { describe, it, expect } from 'bun:test'
import { RESTART_GRACE_MS, shouldAcceptRestart } from '../../tools/agent/restart-guard'

describe('shouldAcceptRestart — /v1/restart 防循环 grace 窗口', () => {
  const BOOT = 1_000_000

  it('grace 窗口内（now − boot < grace）拒绝重启', () => {
    expect(shouldAcceptRestart(BOOT, BOOT)).toBe(false)
    expect(shouldAcceptRestart(BOOT + 1, BOOT)).toBe(false)
    expect(shouldAcceptRestart(BOOT + RESTART_GRACE_MS - 1, BOOT)).toBe(false)
  })

  it('grace 窗口届满（now − boot ≥ grace）放行', () => {
    expect(shouldAcceptRestart(BOOT + RESTART_GRACE_MS, BOOT)).toBe(true)
    expect(shouldAcceptRestart(BOOT + RESTART_GRACE_MS + 1, BOOT)).toBe(true)
  })

  it('自定义 grace：0/负值视为禁用护栏（恒放行）', () => {
    expect(shouldAcceptRestart(BOOT, BOOT, 0)).toBe(true)
    expect(shouldAcceptRestart(BOOT, BOOT, -1)).toBe(true)
    expect(shouldAcceptRestart(BOOT + 5, BOOT, 10)).toBe(false)
    expect(shouldAcceptRestart(BOOT + 10, BOOT, 10)).toBe(true)
  })

  it('grace 默认 30s：覆盖协调器 rescan 周期（~15s）两个周期', () => {
    expect(RESTART_GRACE_MS).toBe(30_000)
  })
})
