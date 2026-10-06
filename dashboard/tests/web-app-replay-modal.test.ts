/** web-app-replay-modal.test.ts — 「导出 replay」弹窗的交互纯函数
 *  （src/web/app/panels/ReplayExportModal.tsx::roundLabel / jobOutcome）。
 *
 *  口径（plan/replay-export-eval-round-picker §3.6/§3.7）：
 *   · 轮选择器文案：it0 = 基线轮、winRate 缺失显式 '-'、回填读数标在末尾；
 *   · **归因判据**：只有 `manifest.iter === 所选轮` 且真的产出了文件才是「本次结果」；
 *     异轮 / 无清单 / 失败一律不得自动交付（`kind !== 'done'`）。
 *  状态机的复位（切轮清勾选 / 导出中禁选）在组件里由这些判据驱动，本文件钉判据本身。 */

import { describe, expect, it } from 'bun:test'
import type { EvalReplayManifest, EvalRoundOption } from '../src/web/view'
import {
  emptyViewText,
  jobOutcome,
  pickerDisabled,
  roundLabel,
} from '../src/web/app/panels/ReplayExportModal'

function round(extra: Partial<EvalRoundOption> = {}): EvalRoundOption {
  return {
    iter: 12,
    wver: 'a'.repeat(16),
    time: '2026-10-06 10:00:00',
    games: 200,
    wins: 60,
    winRate: 0.3,
    reusedWver: false,
    ...extra,
  }
}

function manifest(extra: Partial<EvalReplayManifest> = {}): EvalReplayManifest {
  return {
    ok: true,
    course: 'c1',
    iter: 12,
    wver: 'a'.repeat(16),
    weightsPath: 'w.json',
    difficulty: 'hard',
    maxTicks: 12000,
    generatedAt: '2026-10-06 10:00:00',
    sec: 42,
    requested: 2,
    files: [
      { stage: 0, seed: 1, file: 'hard-s01-clear-l1-t40-seed1.replay' },
      { stage: 0, seed: 2, file: 'hard-s01-died-l0-t30-seed2.replay' },
    ],
    errors: [],
    mismatches: [],
    ...extra,
  }
}

describe('roundLabel（轮选择器文案）', () => {
  it('普通轮：itN · 胜率 · 局数 · 时间', () => {
    expect(roundLabel(round())).toBe('it12 · 30% · 200 局 · 2026-10-06 10:00:00')
  })

  it('it0 = 基线（bc 权重）；winRate/games 缺失显式占位（不伪造 0）', () => {
    expect(roundLabel(round({ iter: 0, winRate: 0.2 }))).toBe(
      '基线 it0（bc 权重） · 20% · 200 局 · 2026-10-06 10:00:00',
    )
    expect(roundLabel(round({ winRate: null, games: null, time: '' }))).toBe('it12 · - · 局数未知')
  })

  it('回填读数（reused_wver）标在末尾——用户知道这轮没有本轮逐局行', () => {
    expect(roundLabel(round({ reusedWver: true }))).toContain('· 回填读数')
  })
})

describe('选择器状态与空轮文案（§3.2/§3.7）', () => {
  it('导出中 / 无评估轮 ⇒ 选择器禁用；空闲且有轮 ⇒ 可切', () => {
    expect(pickerDisabled('exporting', 3)).toBe(true)
    expect(pickerDisabled('idle', 0)).toBe(true)
    expect(pickerDisabled('idle', 3)).toBe(false)
    expect(pickerDisabled('done', 1)).toBe(false)
  })

  it('选过轮 ⇒「该轮无评估记录」（不再冒充「该课程暂无」）', () => {
    expect(emptyViewText(7)).toBe('该轮无评估记录')
    expect(emptyViewText(null)).toBe('该课程暂无 eval 评估记录')
  })
})

describe('jobOutcome（§3.6 归因判据：只有 done 可自动交付）', () => {
  it('无清单 ⇒ no-manifest，不交付', () => {
    const o = jobOutcome(null, 12)
    expect(o.kind).toBe('no-manifest')
    expect(o.files).toEqual([])
    expect(o.message).toContain('无产物清单')
  })

  it('异轮（含未选轮）⇒ other-round，files 为空（绝不把别的轮的产物当本次结果）', () => {
    const o = jobOutcome(manifest({ iter: 7 }), 12)
    expect(o.kind).toBe('other-round')
    expect(o.files).toEqual([])
    expect(o.message).toContain('it7')
    expect(o.message).toContain('it12')
    expect(o.message).toContain('不自动交付')

    const noSel = jobOutcome(manifest({ iter: 7 }), null)
    expect(noSel.kind).toBe('other-round')
    expect(noSel.files).toEqual([])
  })

  it('同轮 + 产出文件 ⇒ done（唯一允许交付的一档）+ 完成文案', () => {
    const o = jobOutcome(manifest(), 12)
    expect(o.kind).toBe('done')
    expect(o.files).toHaveLength(2)
    expect(o.message).toContain('导出完成：2/2 局')
  })

  it('同轮失败：failReason 上屏（不显示「导出完成」）；缺 failReason 回落 errors/日志尾', () => {
    const wf = jobOutcome(
      manifest({ ok: false, files: [], failReason: '未找到 wver 的权重文件' }),
      12,
    )
    expect(wf.kind).toBe('failed')
    expect(wf.files).toEqual([])
    expect(wf.message).toBe('导出失败：未找到 wver 的权重文件')
    expect(wf.message).not.toContain('导出完成')

    const byErr = jobOutcome(
      manifest({ ok: false, files: [], errors: [{ stage: 0, seed: 1, error: '未产出 .replay' }] }),
      12,
    )
    expect(byErr.message).toContain('未产出 .replay')

    const tail = jobOutcome(manifest({ ok: false, files: [] }), 12)
    expect(tail.message).toContain('看日志尾')
  })
})
