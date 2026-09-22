/** swr-cache.ts — 单条目「陈旧先用、后台重算」缓存原语（stale-while-revalidate）。
 *
 *  为什么需要它（2026-09-22，多课程并行下切课要等几秒）：控制台里有一类**进程级事实**
 *  ——节点 ping、共享 hub 观测面、push 机群探活——它们与「操作员在看哪门课」无关，
 *  却各带 1.2–1.5s 的超时预算，是慢快照里最贵的几笔。此前它们住在**按课程键控**的
 *  快照里：切到一门没看过的课 = 把这几个探测原地重做一遍，请求就被压住几秒
 *  （用户报障原话：「切换课程需要几秒钟的延迟才能显示选中课程的数据」）。
 *
 *  两个用法约定：
 *    · **进程级事实**用本缓存（单条目、跨课程共用）——键控错了就是每门课各探一遍；
 *    · 缓存新鲜窗口抵达下界（TTL 边界的那个请求）时**不许阻塞**：有陈旧值就先给陈旧值，
 *      重算丢后台 —— 这正是「刷新周期」与「请求到达时刻」的竞态，也是抖动来源。
 *
 *  语义（四条，缺一不可）：
 *    ① 新鲜（age < freshMs）→ 直接给缓存值；
 *    ② 有陈旧值 → 立刻给陈旧值 + 后台重算（重算落地即刷新）；
 *    ③ 被 `clear()` 后第一次 get → **必须等**重算（需要对「刚发生的事」马上给结论时用）；
 *    ④ 被 `refresh()` 后第一次 get → 仍先给旧值，重算丢后台（动作路径用：**首帧不冷算**）。
 *
 *  ③/④ 的分工是刻意的（2026-09-22）：**结构事实**（cfg/账本派生）随请求现算、不经本缓存，
 *  所以动作后的第一帧本来就是新的；经本缓存的是**探测事实**（ping / hub 应答），
 *  它们的「新」本来就要等一次探测 —— 那就别让请求等：`refresh()` 保留旧值、重算丢后台。
 *  需要「作废后必须等新值」的少数场合（例如单测要断言刚改的输入已生效）才用 `clear()`。
 *
 *  `clear()` / `refresh()` 时已经在飞的重算**不回填**（代际计数）：否则作废会被一个早于它的
 *  重算结果悄悄撤销 —— 作废看起来生效了，实际是旧值回填，最难查的一类假绿。
 */

/** 单条目缓存。`peek` 只读（诊断/测试用），`get` 是唯一取数入口。 */
export interface SwrCache<T> {
  /** 取数：新鲜直接回、陈旧先回再后台重算、已作废则等重算。 */
  get: (compute: () => Promise<T>) => Promise<T>
  /** 硬作废：下一次 get **等**重算（结果即时上屏的场合）；在飞的重算结果作废。 */
  clear: () => void
  /** 软作废（动作路径）：保留当前值供读路径立即返回，并安排一次后台重算（下一次 get 起跑）。 */
  refresh: () => void
  /** 当前缓存值（不触发重算；无值 = null）。 */
  peek: () => T | null
}

/**
 * 建一个单条目 SWR 缓存。
 * @param freshMs 新鲜窗口（毫秒）；超过它即「陈旧」（先给旧值、后台重算）。
 * @param now 时钟（默认 `Date.now`；测试注入可控时钟，避免 sleep）。
 */
export function createSwrCache<T>(freshMs: number, now: () => number = Date.now): SwrCache<T> {
  let cur: { at: number; val: T } | null = null
  let inflight: Promise<T> | null = null
  /** 代际：`clear()` / `refresh()` 自增；早于作废的重算不许回填。 */
  let gen = 0
  /** 软作废标记：置位后即使 TTL 未到，下一次 get 也要起一次后台重算。 */
  let needRefresh = false
  return {
    peek: () => cur?.val ?? null,
    clear: () => {
      gen++
      cur = null
      inflight = null
      needRefresh = false
    },
    refresh: () => {
      gen++
      inflight = null // 丢弃作废之前起的那次重算（它看不到刚刚发生的事）
      if (cur) needRefresh = true // 无值可留 = 等价于 clear（只能等重算）
    },
    get(compute) {
      if (cur && !needRefresh && now() - cur.at < freshMs) return Promise.resolve(cur.val)
      // 已在飞：有旧值就先回它（请求不等探测），否则等这一次（首次冷算无值可给）。
      if (inflight) return cur ? Promise.resolve(cur.val) : inflight
      const g = gen
      const p = compute().then(
        (val) => {
          // 作废之后才落地 → 不回填（否则动作的作废被静默撤销）。
          if (g === gen) {
            cur = { at: now(), val }
            inflight = null
            needRefresh = false
          }
          return val
        },
        (e) => {
          if (g === gen) inflight = null
          throw e
        },
      )
      inflight = p
      if (cur) {
        // 后台重算失败不上抛：请求已经用旧值返回了（失败由下一拍再试）。
        void p.catch(() => undefined)
        return Promise.resolve(cur.val)
      }
      return p
    },
  }
}
