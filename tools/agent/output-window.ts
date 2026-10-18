/** output-window.ts — 采样节点「最近半小时产出」滑动窗口（纯逻辑，agent 与单测共享）。
 *
 *  为什么需要它（用户 2026-10-18）：「rollout.ipynb 及 sampler-agent 的 log 无法观察最近贡献
 *  情况」——agent **故意不逐局打日志**（多 worker RL 采样下会刷爆 `tmp/sampler-agent.log`），
 *  于是日志里只剩 keepalive 与偶发错误，`/v1/status` 的 `gamesDoneTotal` 又只是**开机以来的
 *  累计数**：读不出「最近这半小时这台节点还在不在产出」。累计数还会被两件事骗：
 *  ① 早班攒下的大数字掩盖了当前的停滞；② agent 重启把累计数清零（看数字掉下去 ≠ 变慢）。
 *
 *  口径与看板一致：**完成局数按 `mode` 分 rollout / eval**
 *  （`dashboard/src/server/pool-history.ts` 的 `mode === 'eval' ? eval : rollout`），失败局单列（失败也是产出的事实，不能悄悄吞掉）。
 *  窗口 = **最近 30 分钟**（`OUTPUT_WINDOW_MS`），每 10 分钟打一行（`OUTPUT_LINE_EVERY_MS`）。
 *
 *  内存有界：窗口内事件环封顶 `OUTPUT_MARKS_CAP`，真触顶时丢最旧一半并**诚实标注**截断
 *  （`truncated`）——窗口数字偏低是可接受的，假装完整不可接受（与 `pool-history` 的
 *  `rollingTruncated` 同一哲学）。
 */

/** 滑动窗口长度：最近 30 分钟。 */
export const OUTPUT_WINDOW_MS = 30 * 60_000
/** 打印节拍：每 10 分钟一行。 */
export const OUTPUT_LINE_EVERY_MS = 10 * 60_000
/** 窗口事件环硬上限（30 分钟窗口内 20k 局 ≈ 11 局/秒，现实不可达；有界内存的兜底）。 */
export const OUTPUT_MARKS_CAP = 20_000

/** 一笔产出事件（完成或失败）。 */
export interface OutputMark {
  /** 事件时刻（ms，`Date.now()`）。 */
  ms: number
  /** 任务的 `mode`（协议里就是 rollout / eval）；非 `eval` 一律计 rollout（与看板同判据）。 */
  mode: string
  /** 轮标识（iterId）——「窗口里的轮次数」= 去重后的它。 */
  iterId: string
  /** false = 这一局失败了（失败也进窗口：它同样是「这台节点在不在干活」的事实）。 */
  ok: boolean
}

/** 窗口视图（`/v1/status` 的 `output` 字段 = 这个形状）。 */
export interface OutputWindow {
  /** 窗口内完成的局数（rollout + eval）。 */
  games: number
  /** 其中 rollout 局数。 */
  rollout: number
  /** 其中 eval 局数。 */
  eval: number
  /** 窗口内失败局数。 */
  fail: number
  /** 窗口内出现过的轮次数（iterId 去重）。 */
  iters: number
  /** 速率（局/分，1 位小数）——分母见 `spanMin`。 */
  perMin: number
  /** 速率分母（分钟）：`min(30, 自本次启动)`，下限 1 分钟。 */
  spanMin: number
  /** 窗口是否被硬上限截断过（true = 数字可能偏低）。 */
  truncated: boolean
}

/** 事件环 + 截断锚点（截断标记随窗口自然过期，不做「一次性脏标记」）。 */
export interface OutputState {
  marks: OutputMark[]
  /** 被硬上限丢掉的最晚事件的时刻（0 = 从未丢过）。 */
  droppedThroughMs: number
}

export function newOutputState(): OutputState {
  return { marks: [], droppedThroughMs: 0 }
}

/** 清掉窗口外的事件（并处理硬上限）。每次记录/汇总前调用，保证环只装窗口内的东西。 */
export function pruneOutput(state: OutputState, nowMs: number): void {
  const from = nowMs - OUTPUT_WINDOW_MS
  let i = 0
  while (i < state.marks.length && state.marks[i]!.ms < from) i++
  if (i > 0) state.marks.splice(0, i)
  // 硬上限：丢最旧一半（一次丢一半 ⇒ 摊还 O(1)，不每局 splice 一次）。
  if (state.marks.length > OUTPUT_MARKS_CAP) {
    const drop = state.marks.length - Math.floor(OUTPUT_MARKS_CAP / 2)
    const last = state.marks[drop - 1]
    if (last) state.droppedThroughMs = Math.max(state.droppedThroughMs, last.ms)
    state.marks.splice(0, drop)
  }
}

/** 记一笔产出（完成 / 失败）。事件环是窗口的唯一数据源。 */
export function noteOutput(state: OutputState, mark: OutputMark, nowMs = mark.ms): void {
  state.marks.push(mark)
  pruneOutput(state, nowMs)
}

/** 汇总最近 30 分钟的产出。
 *
 *  速率分母 = `min(窗口, 自本次启动)`、下限 1 分钟（`sinceMs`）：
 *  · 开机 3 分钟就报「30 分钟 412 局」不公平，开机 10 秒报「∞ 局/分」也不公平；
 *  · 传 0（不知道启动时刻）⇒ 退化为「自窗口内最早那笔起算」，同样下限 1 分钟——
 *    单测与旧调用方不需要知道 uptime。
 */
export function outputWindow(state: OutputState, nowMs: number, sinceMs = 0): OutputWindow {
  pruneOutput(state, nowMs)
  const from = nowMs - OUTPUT_WINDOW_MS
  let rollout = 0
  let evalN = 0
  let fail = 0
  let oldest = 0
  const iters = new Set<string>()
  for (const m of state.marks) {
    if (m.ms < from) continue
    if (m.ok) {
      if (m.mode === 'eval') evalN++
      else rollout++
    } else fail++
    if (oldest === 0 || m.ms < oldest) oldest = m.ms
    if (m.iterId) iters.add(m.iterId)
  }
  const games = rollout + evalN
  const startMs = sinceMs > 0 ? sinceMs : oldest > 0 ? oldest : nowMs
  const spanMin = Math.max(1, Math.min(OUTPUT_WINDOW_MS, nowMs - startMs) / 60_000)
  return {
    games,
    rollout,
    eval: evalN,
    fail,
    iters: iters.size,
    perMin: +(games / spanMin).toFixed(1),
    spanMin: +spanMin.toFixed(1),
    // 截断锚点落进当前窗口才算「这一次窗口可能偏低」；过了窗口自然恢复诚实。
    truncated: state.droppedThroughMs > 0 && state.droppedThroughMs >= from,
  }
}

/** 一行日志（agent 与云机侧原样上屏；两侧逐字相同才好对账）。
 *
 *  形状：`games=412（rollout=380 / eval=32）· 13.7 局/分 · 失败=3 · 轮次=2 · 在飞=8 · 累计=1240`。
 *  `累计` 是 `gamesDoneTotal`（开机以来）——它回答「这台节点一共交了多少」，与窗口的
 *  「最近还在不在交」互补，两个都要（只有累计就会把停滞看成功劳）。
 */
export function formatOutputLine(
  w: OutputWindow,
  extra: { inflight?: number; total?: number; failedRecent?: number } = {},
): string {
  const parts = [
    `games=${w.games}（rollout=${w.rollout} / eval=${w.eval}）`,
    `${w.perMin} 局/分（${w.spanMin}min）`,
    `失败=${w.fail}`,
    `轮次=${w.iters}`,
  ]
  if (extra.inflight !== undefined) parts.push(`在飞=${extra.inflight}`)
  if (extra.failedRecent !== undefined) parts.push(`待取失败=${extra.failedRecent}`)
  if (extra.total !== undefined) parts.push(`累计=${extra.total}`)
  if (w.truncated) parts.push('窗口截断（环封顶，数字偏低）')
  return parts.join(' · ')
}
