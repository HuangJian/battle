import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  OUTPUT_LINE_EVERY_MS,
  OUTPUT_MARKS_CAP,
  OUTPUT_WINDOW_MS,
  formatOutputLine,
  newOutputState,
  noteOutput,
  outputWindow,
} from '../../tools/agent/output-window'

/**
 * 采样节点「最近半小时产出」滑动窗口（2026-10-18 用户口径：「rollout.ipynb 及 sampler-agent
 * 的 log 无法观察最近贡献情况，请持续统计半小时滑动窗口产出，每十分钟打印一行」）。
 *
 * 这里钉五件事：
 *   ① 窗口 = **最近 30 分钟**（更早的局不进数字，也不是「开机以来」）；
 *   ② 分列口径与看板一致：`mode === 'eval'` 才是 eval，其余计 rollout；失败单列；
 *   ③ 速率分母是「本窗口观察到的时长」，开机不足 30 分钟时不拿 30 分钟当分母；
 *   ④ 环封顶后有**诚实的截断标注**，且它随窗口自然过期（不做永久脏标记）；
 *   ⑤ 日志行形状稳定（agent 与云机侧同一串，好对账）。
 */

const MIN = 60_000
const T0 = 1_700_000_000_000 // 固定基准时刻（用例不吃墙钟；§8 确定性）

function out(
  state: ReturnType<typeof newOutputState>,
  atMs: number,
  mode = 'rollout',
  iterId = 'y22-a:it1',
  ok = true,
): void {
  noteOutput(state, { ms: atMs, mode, iterId, ok })
}

describe('常量：30 分钟窗口 / 10 分钟一行', () => {
  it('窗口 30 分钟、节拍 10 分钟（改这两个数 = 改用户口径，必须显式改）', () => {
    expect(OUTPUT_WINDOW_MS).toBe(30 * MIN)
    expect(OUTPUT_LINE_EVERY_MS).toBe(10 * MIN)
  })
})

describe('outputWindow：窗口边界', () => {
  it('只数最近 30 分钟：31 分钟前那笔不进窗口', () => {
    const s = newOutputState()
    out(s, T0 - 31 * MIN)
    out(s, T0 - 29 * MIN)
    out(s, T0 - 1 * MIN)
    const w = outputWindow(s, T0)
    expect(w.games).toBe(2)
  })

  it('窗口外的旧事件会被剪掉（环不无界增长）', () => {
    const s = newOutputState()
    for (let i = 40; i > 0; i--) out(s, T0 - i * MIN)
    // 记录时按「自己的时刻」剪、汇总时按 now 再剪一次 ⇒ 环里只剩窗口内的 30 笔（有界），不是 40。
    const w = outputWindow(s, T0)
    // 窗口边界含等号：30 分钟整那笔仍在窗内（`ms < from` 才出窗）⇒ 1..30 分钟前 = 30 笔
    expect(w.games).toBe(30)
    expect(s.marks.length).toBe(30)
  })

  it('空窗口：全 0，且速率不是 NaN', () => {
    const w = outputWindow(newOutputState(), T0)
    expect(w).toEqual({
      games: 0,
      rollout: 0,
      eval: 0,
      fail: 0,
      iters: 0,
      perMin: 0,
      spanMin: 1,
      truncated: false,
    })
  })
})

describe('outputWindow：分列与轮次', () => {
  it('mode=eval 才计 eval；其余（含历史 kind 写法）计 rollout', () => {
    const s = newOutputState()
    out(s, T0 - 5 * MIN, 'rollout')
    out(s, T0 - 4 * MIN, 'eval')
    out(s, T0 - 3 * MIN, 'goal') // 非 eval ⇒ 与看板同规计 rollout
    const w = outputWindow(s, T0)
    expect(w.games).toBe(3)
    expect(w.rollout).toBe(2)
    expect(w.eval).toBe(1)
  })

  it('失败单列，且不计入完成局数（失败是事实，但不能冒充产出）', () => {
    const s = newOutputState()
    out(s, T0 - 6 * MIN)
    out(s, T0 - 5 * MIN, 'rollout', 'y22-a:it1', false)
    const w = outputWindow(s, T0)
    expect(w.games).toBe(1)
    expect(w.fail).toBe(1)
  })

  it('轮次 = 窗口内 iterId 去重（同一轮跑 100 局仍是 1 轮）', () => {
    const s = newOutputState()
    for (let i = 0; i < 100; i++) out(s, T0 - 10 * MIN, 'rollout', 'y22-a:it7')
    out(s, T0 - 9 * MIN, 'eval', 'y22-a:it8')
    expect(outputWindow(s, T0).iters).toBe(2)
  })
})

describe('outputWindow：速率分母（别拿 30 分钟骗人）', () => {
  it('开机 3 分钟：分母是 3 分钟，不是 30 分钟', () => {
    const s = newOutputState()
    for (let i = 0; i < 30; i++) out(s, T0 - 2 * MIN + i * 1000)
    const w = outputWindow(s, T0, T0 - 3 * MIN)
    expect(w.games).toBe(30)
    expect(w.spanMin).toBe(3)
    expect(w.perMin).toBe(10)
  })

  it('开机超过 30 分钟：分母封顶 30', () => {
    const s = newOutputState()
    for (let i = 0; i < 60; i++) out(s, T0 - 20 * MIN + i * 1000)
    const w = outputWindow(s, T0, T0 - 4 * 3600_000)
    expect(w.spanMin).toBe(30)
    expect(w.perMin).toBe(2)
  })

  it('同一秒内刚跑完一局：分母下限 1 分钟（不出现 120 局/分这种噪声）', () => {
    const s = newOutputState()
    out(s, T0)
    out(s, T0)
    const w = outputWindow(s, T0, T0)
    expect(w.spanMin).toBe(1)
    expect(w.perMin).toBe(2)
  })
})

describe('截断：诚实标注，且随窗口自然过期', () => {
  it('环封顶 ⇒ truncated=true；窗口滑过那段后恢复 false', () => {
    const s = newOutputState()
    // 造 OUTPUT_MARKS_CAP+10 笔，全部落在窗口内 ⇒ 必须丢最旧的那批
    const spanMs = 20 * MIN
    const n = OUTPUT_MARKS_CAP + 10
    for (let i = 0; i < n; i++) out(s, T0 - spanMs + Math.floor((i * spanMs) / n))
    const w = outputWindow(s, T0)
    expect(s.marks.length).toBeLessThanOrEqual(OUTPUT_MARKS_CAP)
    expect(w.truncated).toBe(true)
    // 40 分钟后：那批事件整体出窗 ⇒ 截断标记不再适用于当前窗口
    expect(outputWindow(s, T0 + 40 * MIN).truncated).toBe(false)
  })
})

describe('formatOutputLine：一行日志', () => {
  it('完整形状（数值、分隔、累计/在飞）', () => {
    const s = newOutputState()
    out(s, T0 - 2 * MIN, 'rollout', 'y22-a:it1')
    out(s, T0 - 2 * MIN, 'rollout', 'y22-a:it1')
    out(s, T0 - 1 * MIN, 'eval', 'y22-a:it2')
    out(s, T0 - 1 * MIN, 'rollout', 'y22-a:it2', false)
    const line = formatOutputLine(outputWindow(s, T0, T0 - 10 * MIN), {
      inflight: 8,
      total: 1240,
    })
    expect(line).toBe(
      'games=3（rollout=2 / eval=1） · 0.3 局/分（10min） · 失败=1 · 轮次=2 · 在飞=8 · 累计=1240',
    )
  })

  it('截断时点名「数字偏低」（不把不完整伪装成完整）', () => {
    const s = newOutputState()
    const n = OUTPUT_MARKS_CAP + 1
    for (let i = 0; i < n; i++) out(s, T0 - 10 * MIN + i)
    const line = formatOutputLine(outputWindow(s, T0))
    expect(line).toContain('窗口截断（环封顶，数字偏低）')
  })
})

// ────────────────────────── 接线：每 10 分钟真打一行 ──────────────────────────

describe('startOutputTicker：接线到 agent 日志（不是纯函数银弹）', () => {
  it('★ 假时钟拨 10 分钟：agent 日志里出现那一行（含刚记的产出）', async () => {
    // 为何用假时钟：真等 10 分钟不可测，而「只在单测里算数字」正是「看不到最近贡献」的
    // 原形态（算对了但没人上屏）。这里让 ticker 真跑一次节拍。
    const { jest } = await import('bun:test')
    const { noteTaskOutput, startOutputTicker } = await import('../../tools/agent/sampler-agent')
    jest.useFakeTimers()
    const seen: string[] = []
    const spy = jest.spyOn(console, 'log').mockImplementation((...a: unknown[]) => {
      seen.push(a.map((x) => String(x)).join(' '))
    })
    try {
      noteTaskOutput('rollout', 'y22-a:it1', true)
      startOutputTicker()
      expect(seen.some((l) => l.includes('近30min产出'))).toBe(false) // 不到点不打
      jest.advanceTimersByTime(OUTPUT_LINE_EVERY_MS)
      const line = seen.find((l) => l.includes('近30min产出'))
      expect(line).toBeDefined()
      expect(line!).toContain('games=1（rollout=1 / eval=0）')
      expect(line!).toContain('累计=') // 累计数与窗口并给（只有累计会把停滞看成功劳）
      // 再拨一拍：第二次照常（不是一次性定时器）
      jest.advanceTimersByTime(OUTPUT_LINE_EVERY_MS)
      expect(seen.filter((l) => l.includes('近30min产出')).length).toBe(2)
    } finally {
      spy.mockRestore()
      jest.useRealTimers()
    }
  })

  it('/v1/status 暴露同一份窗口：结构给看板，人读行给云机侧原样搬运', () => {
    // 结构钉子（与 result-stamp.test.ts 同手法）：这两行是「一行数字只有一个来源」的接线，
    // 删掉任一 → 云机侧只剩「不可知」，而纯函数单测仍会全绿。
    const src = readFileSync(
      join(import.meta.dir, '..', '..', 'tools', 'agent', 'sampler-agent.ts'),
      'utf8',
    )
    expect(src).toContain('output: currentOutputWindow(),')
    expect(src).toContain('outputLine: formatOutputLine(currentOutputWindow(), {')
  })
})
