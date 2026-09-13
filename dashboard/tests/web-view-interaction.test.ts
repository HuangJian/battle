/**
 * web-view-interaction.test.ts — §7 评审 E5：用交互纯函数替代 DOM 测试（shouldFollow / isDirty / nextRefreshInterval / keywordMatch / cardVisible）
 *
 * 分层：src/web/view/interaction.ts
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import {
  isDirty,
  nextRefreshInterval,
  parsePhaseFromLog,
  refreshLabel,
  shouldFollow,
} from '../src/web/view'

// ────────────────────────── 纯函数：dirty / follow / 节奏 ──────────────────────────
describe('view 交互纯函数（§7 评审 E5 替代 DOM 测试）', () => {
  it('isDirty：pendingEdits 非空即 true', () => {
    expect(isDirty(new Map())).toBe(false)
    expect(isDirty(new Map([['conc:self', '8']]))).toBe(true)
  })

  it('nextRefreshInterval 轮转 1m→3m→5m→10m→30m→暂停→1m；refreshLabel 文案', () => {
    expect(nextRefreshInterval(60)).toBe(180)
    expect(nextRefreshInterval(180)).toBe(300)
    expect(nextRefreshInterval(300)).toBe(600)
    expect(nextRefreshInterval(600)).toBe(1800)
    expect(nextRefreshInterval(1800)).toBe('pause')
    expect(nextRefreshInterval('pause')).toBe(60)
    expect(refreshLabel('pause')).toBe('暂停')
    expect(refreshLabel(60)).toBe('1m')
    expect(refreshLabel(300)).toBe('5m')
    expect(refreshLabel(1800)).toBe('30m')
  })

  it('parsePhaseFromLog：iteration 头 → rollout；rollout itN/push/ppo → ppo；空/未知 → idle', () => {
    // 空日志 / 无时间戳行 → idle
    expect(parsePhaseFromLog([])).toEqual({ phase: 'idle', sinceMs: null, iter: null })
    expect(parsePhaseFromLog(['random noise line'])).toEqual({
      phase: 'idle',
      sinceMs: null,
      iter: null,
    })
    // === iteration N/M === → rollout（取 N）
    expect(parsePhaseFromLog(['[10:00:00] [run_rl] === iteration 7/12 ==='])).toEqual({
      phase: 'rollout',
      sinceMs: new Date().setHours(10, 0, 0, 0),
      iter: 7,
    })
    // rollout itN: → ppo（rollout 已结束，进入 PPO）
    expect(parsePhaseFromLog(['[10:01:30] [run_rl] rollout it7: win=0.5'])).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 1, 30, 0),
      iter: 7,
    })
    // push / ppo itN / weights archived → ppo
    expect(parsePhaseFromLog(['[10:02:00] [run_rl] push: it7 done'])).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 2, 0, 0),
      iter: 7,
    })
    expect(parsePhaseFromLog(['[10:03:00] [run_rl] ppo it7: kl=0.01'])).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 3, 0, 0),
      iter: 7,
    })
    // §380：published job（wait_job 等待云 worker 期间日志尾常停在此行）→ ppo
    expect(
      parsePhaseFromLog(['[10:04:00] published job abcd1234 it7: shards=150 data_fp=abc…']),
    ).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 4, 0, 0),
      iter: 7,
    })
    expect(parsePhaseFromLog(['[10:04:00] [run_rl] weights archived'])).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 4, 0, 0),
      iter: null,
    })
  })

  it('shouldFollow：贴底跟随 / 上滚不跟随（阈值 24）', () => {
    expect(shouldFollow(500, 100, 600)).toBe(true)
    expect(shouldFollow(0, 100, 600)).toBe(false)
    expect(shouldFollow(476, 100, 600)).toBe(true)
    expect(shouldFollow(475, 100, 600)).toBe(false)
    expect(shouldFollow(100, 100, 200, 0)).toBe(true)
  })
})
