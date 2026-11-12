/**
 * ledger-bc-segmentation.test.ts —— BC 账本的**轮次分段**（面板不拼两轮的曲线）。
 *
 * 同一份 training_log.jsonl 被多轮复用（traj 不变、只换语料口径）：两轮的
 * bc_epoch / bc_eval 全挂同一个 it=1 —— 不过滤的话，epoch/eval 面板会把上一轮
 * 59 行与本轮 150 行拼成一条曲线（实测 10:38 那轮）。
 *
 * 段锚：**优先 `job_pending`（job 身份，2026-11-12）**，没有它才退回 `run_start`（2026-09-14）。
 * 为什么不再单靠 run_start：重启 trainer 会多写一条 run_start，但 job 没换（hub 的 `bc-resume`
 * 继续）⇒ 按 run_start 分段会把重启前算过的 epoch 全遮掉（g1c-4s2e 报障「之前算的都丢了」）。
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

  // ★2026-11-12（g1c-4s2e 报障）：重启 trainer 会多写一条 `run_start`，但**job 没变**
  //   （hub 的 `bc-resume` 继续）。按 run_start 分段会把重启前已算的 epoch 全遮掉 ⇒ 用户读成
  //   「之前算的都丢了」。job 身份写在 hub 追加的 `job_pending` 里 ⇒ 有它就按它分段。
  const RESTARTED_SAME_JOB = [
    L('run_start', { runId: 'run-1' }),
    L('job_pending', { job_id: 'abc123', it: 1 }),
    L('bc_epoch', { it: 1, epoch: 1, val_loss: 1.5, ts: 1 }),
    L('bc_epoch', { it: 1, epoch: 2, val_loss: 1.4, ts: 2 }),
    L('run_start', { runId: 'run-2' }), // 重启（同一个 job）
    L('bc_epoch', { it: 1, epoch: 3, val_loss: 1.3, ts: 3 }),
  ]

  it('重启（多一条 run_start、job 未变）⇒ 段不重置：重启前的 epoch 仍在曲线里', () => {
    const { epochs } = bcRowsFromLedgerTail(RESTARTED_SAME_JOB)
    expect(epochs.map((e) => e.epoch)).toEqual([1, 2, 3])
  })

  it('换 job（新的 job_pending）⇒ 段重置到新 job（不拼上一份 job 的曲线）', () => {
    const { epochs } = bcRowsFromLedgerTail([
      ...RESTARTED_SAME_JOB,
      L('job_pending', { job_id: 'def456', it: 1 }),
      L('bc_epoch', { it: 1, epoch: 1, val_loss: 2.0, ts: 4 }),
    ])
    expect(epochs.map((e) => e.epoch)).toEqual([1])
    expect(epochs[0]!.valLoss).toBe(2.0) // 是新 job 的 ep1
  })

  it('同一 (it, epoch) 的重写行只留第一条（重启重放不让表格报错 epoch 数）', () => {
    const { epochs, evals } = bcRowsFromLedgerTail([
      L('job_pending', { job_id: 'abc123', it: 1 }),
      L('bc_epoch', { it: 1, epoch: 1, val_loss: 1.5, ts: 1 }),
      L('bc_eval', { it: 1, epoch: 2, wver: 'a', levels: [], ts: 2 }),
      L('bc_epoch', { it: 1, epoch: 2, val_loss: 1.4, ts: 2 }),
      // 重启重放：同样的 ep1/ep2（值一様）——同一个 epoch 不是新点
      L('bc_epoch', { it: 1, epoch: 1, val_loss: 1.5, ts: 9 }),
      L('bc_eval', { it: 1, epoch: 2, wver: 'a', levels: [], ts: 9 }),
      // 另一轮（iters>1）的 ep1 是**新点**（键含 it）
      L('bc_epoch', { it: 2, epoch: 1, val_loss: 0.9, ts: 10 }),
    ])
    expect(epochs.map((e) => [e.it, e.epoch])).toEqual([
      [1, 1],
      [1, 2],
      [2, 1],
    ])
    expect(evals.map((e) => [e.it, e.epoch])).toEqual([[1, 2]])
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
