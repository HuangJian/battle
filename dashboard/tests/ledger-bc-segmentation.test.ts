/**
 * ledger-bc-segmentation.test.ts —— BC 账本的 run_start 分段（2026-09-14 混轮修复）。
 *
 * 同一份 training_log.jsonl 被多轮复用（traj 不变、只换语料口径）：两轮的
 * bc_epoch / bc_eval 全挂同一个 it=1 —— 不过滤的话，epoch/eval 面板会把上一轮
 * 59 行与本轮 150 行拼成一条曲线（实测 10:38 那轮）。
 * `bcRowsFromLedgerTail` 现按「最后一次 run_start 之后」取行。
 */

import { describe, expect, it } from 'bun:test'
import { bcRowsFromLedgerTail } from '../src/server/api/ledger'

const L = (event: string, extra: Record<string, unknown> = {}): string =>
  JSON.stringify({ event, ...extra })

describe('bcRowsFromLedgerTail：run_start 分段', () => {
  const TWO_ROUNDS = [
    L('run_start', { runId: 'run-1', epochs: 60 }),
    L('bc_epoch', { it: 1, epoch: 1, val_loss: 1.5, move_acc: 0.5, ts: 1 }),
    L('bc_round_completed', { it: 1 }),
    L('run_start', { runId: 'run-2', epochs: 150 }),
    L('bc_epoch', { it: 1, epoch: 1, val_loss: 2.0, move_acc: 0.55, ts: 2 }),
    L('bc_epoch', { it: 1, epoch: 2, val_loss: 1.9, move_acc: 0.61, ts: 3 }),
  ]

  it('只取最后一次 run_start 之后的行（两轮不拼曲线）', () => {
    const { epochs, evals } = bcRowsFromLedgerTail(TWO_ROUNDS)
    expect(epochs.map((e) => e.epoch)).toEqual([1, 2])
    expect(epochs[0]!.valLoss).toBe(2.0) // 是新一轮的 ep1，不是旧轮的
    expect(evals).toEqual([])
  })

  it('换回旧课程（再次 run_start）⇒ 段锚跟着移动', () => {
    const THREE = [
      ...TWO_ROUNDS,
      L('run_start', { runId: 'run-3' }),
      L('bc_epoch', { it: 1, epoch: 9, val_loss: 1.2, move_acc: 0.7, ts: 4 }),
    ]
    const { epochs } = bcRowsFromLedgerTail(THREE)
    expect(epochs).toHaveLength(1)
    expect(epochs[0]!.epoch).toBe(9)
  })

  it('无 run_start（旧账本/纯 local 训练）⇒ 不过滤（向后兼容）', () => {
    const { epochs } = bcRowsFromLedgerTail([
      L('bc_epoch', { it: 1, epoch: 1, val_loss: 1.5, move_acc: 0.5, ts: 1 }),
    ])
    expect(epochs.map((e) => e.epoch)).toEqual([1])
  })

  it('hold_acc 入账（v6 第三头）；旧行缺键 ⇒ null', () => {
    const { epochs } = bcRowsFromLedgerTail([
      L('bc_epoch', { it: 1, epoch: 1, hold_acc: 0.42, ts: 1 }),
      L('bc_epoch', { it: 1, epoch: 2, ts: 2 }),
    ])
    expect(epochs.map((e) => e.holdAcc)).toEqual([0.42, null])
  })

  it('bc_eval 行为门字段与 panel 入账（B4/B5）；旧行无 panel ⇒ undefined', () => {
    const { evals } = bcRowsFromLedgerTail([
      L('bc_eval', {
        it: 1,
        epoch: 10,
        wver: 'abc',
        sentinel_only: true,
        levels: [
          {
            level: 'a',
            n: 30,
            wins: 9,
            pickup_rate: 0.66,
            cells_mean: 120,
            move_frac: 0.9,
            panel: { max_ticks: 36000, course_max_ticks: 20000, difficulty: 'hard', lives: 3 },
          },
          { level: 'b', n: 10, wins: 1 },
        ],
      }),
    ])
    expect(evals[0]!.sentinelOnly).toBe(true)
    const [a, b] = evals[0]!.levels
    expect(a!.pickupRate).toBe(0.66)
    expect(a!.cellsMean).toBe(120)
    expect(a!.moveFrac).toBe(0.9)
    expect(a!.panel?.maxTicks).toBe(36000)
    expect(a!.panel?.courseMaxTicks).toBe(20000)
    expect(b!.pickupRate).toBeNull()
    expect(b!.panel).toBeUndefined()
  })

  it('坏行/空行不致命', () => {
    const { epochs } = bcRowsFromLedgerTail([
      'not json',
      '',
      L('run_start', { runId: 'r' }),
      '',
      L('bc_epoch', { it: 1, epoch: 5, val_loss: 1.0, move_acc: 0.6, ts: 5 }),
    ])
    expect(epochs.map((e) => e.epoch)).toEqual([5])
  })
})
