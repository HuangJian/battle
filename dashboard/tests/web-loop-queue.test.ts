/**
 * web-loop-queue.test.ts — 调度器每课队列的**视图层**（src/web/view/loop-queue.ts）
 *
 * 分层：src/web/view/loop-queue.ts（纯函数，无 IO）
 *
 * 这里只放不依赖面板的判据：暂停四态（意图 × 事实）、在等什么的取值域与文案。
 * 面板能读出什么在 `web-app-coursematrix.test.ts`，join 规则在 `web-course-matrix.test.ts`。
 *
 * 为什么「意图」与「事实」要分成两个字段（而不是一个 `paused` 布尔）：训练进程每拍才读一次
 * 控制文件，而且它可能根本没在跑。合成一个字段就只剩两种可能——要么把「进程没跑」演成已暂停
 * （骗人），要么点完暂停毫无反馈（像坏了）。四态各自是一个真实且不同的局面。
 */

import { describe, expect, it } from 'bun:test'
import {
  type LoopQueueView,
  parseLoopQueue,
  pauseBadge,
  pauseLabel,
  pauseState,
  pauseTitle,
  trainingFromQueue,
  withPausedFacts,
  withTraining,
} from '../src/web/view'

/** 一课的原始行（形状与 run_rl_cluster.build_rows 一致，便于按需改）。 */
function row(patch: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    course: 'c4-dodge',
    it: 37,
    state: 'ready',
    current: 'ppo',
    pending: ['ppo', 'eval_join'],
    inflight: [],
    facts: { iterations: 36, games_settled: 150, games_planned: 0 },
    waiting: { kind: 'ready', text: '无外部等待，下一步 ppo' },
    ...patch,
  }
}

/** 组装一份视图：走**服务端同一串步骤**（parse → trainingFromQueue → withTraining →
 *  withPausedFacts），不手拼 LoopQueueView。
 *
 *  `schedulerAlive` 而不是「在训课程名单」：那个名单是 `trainingFromQueue` **推**出来的
 *  （调度器存活 ∧ 该课未收官），传名单进来就把被测的判据换成了测试自己写的答案。 */
function queue(
  raw: Record<string, unknown>[] = [row()],
  schedulerAlive = false,
  pause: { intent?: string[]; applied?: string[] } = {},
): LoopQueueView {
  const v = parseLoopQueue({
    courses: raw,
    pools: { local_ppo: { held: 1, capacity: 1 }, eval_local: { held: 0, capacity: 1 } },
  })!
  const training = trainingFromQueue(v, schedulerAlive)
  return withPausedFacts(withTraining(v, training), pause.intent ?? [], pause.applied ?? [])
}

describe('暂停态：意图与事实分开（四态）', () => {
  const one = (intent: boolean, applied: boolean) =>
    queue([row()], true, {
      intent: intent ? ['c4-dodge'] : [],
      applied: applied ? ['c4-dodge'] : [],
    }).rows[0]!

  it('四种组合各是一个真实局面（running / pending / paused / resuming）', () => {
    expect(pauseState(one(false, false))).toBe('running')
    expect(pauseState(one(true, false))).toBe('pending')
    expect(pauseState(one(true, true))).toBe('paused')
    expect(pauseState(one(false, true))).toBe('resuming')
  })

  it('按钮方向由**意图**定：点了暂停未生效时说「取消暂停」，已生效说「恢复」', () => {
    expect(pauseLabel(one(false, false))).toBe('暂停')
    expect(pauseLabel(one(true, false))).toBe('取消暂停')
    expect(pauseLabel(one(true, true))).toBe('恢复')
  })

  it('待生效的悬停解释分两种：进程没跑 vs 还没轮到读（不能一句「处理中」糊过去）', () => {
    const live = queue([row()], true, { intent: ['c4-dodge'] }).rows[0]!
    expect(pauseTitle(live)).toContain('每拍读一次')
    const dead = queue([row()], false, { intent: ['c4-dodge'] }).rows[0]!
    expect(pauseTitle(dead)).toContain('没有存活的 trainingLoop 进程')
  })

  it('徽标只在「意图 ≠ 事实」或「已暂停」时出现（没事可说的行不挂徽标）', () => {
    expect(pauseBadge(one(false, false))).toBeNull()
    expect(pauseBadge(one(true, true))!.text).toBe('已暂停')
    expect(pauseBadge(one(true, false))!.text).toBe('待生效')
    expect(pauseBadge(one(false, true))!.text).toBe('恢复中')
  })

  it('徽标给的是**语义档**（info/warn）而不是类名——徽章词表归 StatusRow 一处', () => {
    expect(pauseBadge(one(true, true))!.tone).toBe('info')
    expect(pauseBadge(one(true, false))!.tone).toBe('warn')
    expect(pauseBadge(one(false, true))!.tone).toBe('warn')
  })

  it('意图只作用于被点名的课（别的行不受污染）', () => {
    const v = queue([row(), row({ course: 'c5-tick' })], true, {
      intent: ['c4-dodge'],
      applied: ['c4-dodge'],
    })
    expect(v.rows.map((r) => [r.course, r.pausedIntent, r.pauseApplied])).toEqual([
      ['c4-dodge', true, true],
      ['c5-tick', false, false],
    ])
  })
})

describe('在训判据：调度器存活 ∧ 该课未收官（共享 trainer 时代）', () => {
  it('调度器没跑 ⇒ 一门都不算在训（盘上那套「可推进」只是计划）', () => {
    const v = queue([row()], false)
    expect(v.rows.every((r) => !r.training)).toBe(true)
    expect(trainingFromQueue(v, false)).toEqual([])
  })

  it('已收官（done / aborted）的课不算在训，其余算', () => {
    const v = queue(
      [
        row({ course: 'a' }),
        row({ course: 'b', state: 'done' }),
        row({ course: 'c', state: 'aborted' }),
      ],
      true,
    )
    expect(v.rows.map((r) => [r.course, r.training])).toEqual([
      ['a', true],
      ['b', false],
      ['c', false],
    ])
    expect(v.trainingCount).toBe(1)
  })
})

describe('容错解析：python 侧比控制台新/旧一个版本都不能炸', () => {
  it('形状不符 → null（UI 显空态），而不是抛', () => {
    expect(parseLoopQueue(null)).toBeNull()
    expect(parseLoopQueue({})).toBeNull()
    expect(parseLoopQueue({ courses: 'nope' })).toBeNull()
  })

  it('缺字段退化成空态：未知 wait kind 保留短语（宁可少一个颜色，不可丢整句）', () => {
    const v = parseLoopQueue({
      courses: [row({ waiting: { kind: 'brand-new-kind', text: '新的等待' } })],
    })!
    expect(v.rows[0]!.waiting.kind).toBe('ready')
    expect(v.rows[0]!.waiting.text).toBe('新的等待')
  })

  it('未知课程种类 → 当 RL（保守方向：误判成 BC 会给真 RL 课贴错标签）', () => {
    expect(parseLoopQueue({ courses: [row({ kind: 'weird' })] })!.rows[0]!.kind).toBe('rl')
    expect(parseLoopQueue({ courses: [row({ kind: 'bc' })] })!.rows[0]!.kind).toBe('bc')
  })
})
