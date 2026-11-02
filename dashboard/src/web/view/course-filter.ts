/** course-filter.ts — 侧栏「当前课程」选择器的过滤（2026-11-02 用户报障：300+ 门课靠滚动找课）。
 *
 *  只做一件事：把**查什么**变成**哪几项还在**。
 *    · 空格分隔多个词 ⇒ AND 收窄（`z4 pick` 与 `pick z4` 同义），大小写不敏感；
 *    · 系列名（`z4` / `x20` / `h4` / `c4`）就是课名的第一段，天然是子串 ⇒ 输入系列名即命中整个系列。
 *      刻意**不建系列表**：那是第二份真相，新课一多就会漂（本仓已踩过白名单漂移）。
 *
 *  **过滤不切课**：当前查看的课永远留在候选里（`pinned`）。把 value 所在的 `<option>` 滤掉，
 *  select 会静默把显示换成列表第一项——看起来像「课被切走了」，而那只是渲染细节。
 *  它不命中时排在最前并标注（`pinnedKept`）：既不藏起来，也不冒充命中。
 */

/** 系列名 = 课名第一段（`z4-pick-approach` → `z4`；`c4` 无后缀 → `c4`）。 */
export function courseSeries(course: string): string {
  const i = course.indexOf('-')
  return i > 0 ? course.slice(0, i) : course
}

/** 过滤词：空白分隔、去空、小写（`' Z4  Pick '` → `['z4','pick']`）。 */
export function courseFilterTokens(query: string): string[] {
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter((t) => t.length > 0)
}

export interface CourseFilterResult {
  /** 上屏候选：被强制保留的 pinned 在前，其余保持传入顺序。 */
  matches: string[]
  /** 命中数（**不含**强制保留的当前课——它不是被「找到」的）。 */
  hits: number
  /** 过滤前的候选总数。 */
  total: number
  /** 有过滤词 ⇒ 调用方才画计数行（无词时不占一行，列表也与过滤前逐项相同）。 */
  active: boolean
  /** 当前课不命中过滤词、被强制留在列表里（select 的 value 需要一个合法落点）。 */
  pinnedKept: boolean
}

/** 过滤课程清单（纯函数：同输入同输出，不动传入数组）。 */
export function filterCourses(
  courses: readonly string[],
  query: string,
  pinned = '',
): CourseFilterResult {
  const tokens = courseFilterTokens(query)
  const active = tokens.length > 0
  const total = courses.length
  if (!active) {
    return { matches: courses.slice(), hits: total, total, active, pinnedKept: false }
  }
  const hitList = courses.filter((c) => {
    const low = c.toLowerCase()
    return tokens.every((t) => low.includes(t))
  })
  // pinned 只在「确实存在且没命中」时补进首位：命中时保持原顺序（不因被查看而跳位）。
  const pinnedKept = pinned !== '' && courses.includes(pinned) && !hitList.includes(pinned)
  return {
    matches: pinnedKept ? [pinned, ...hitList] : hitList.slice(),
    hits: hitList.length,
    total,
    active,
    pinnedKept,
  }
}

/** 计数行文案（空查询 = 空串：没过滤就不说一个字）。 */
export function courseFilterNote(r: CourseFilterResult): string {
  if (!r.active) return ''
  if (r.total === 0) return '没有可选课程'
  if (r.hits === 0) {
    return r.pinnedKept ? `无匹配 · 仅保留当前课（共 ${r.total} 门）` : `无匹配（共 ${r.total} 门）`
  }
  return `命中 ${r.hits} / ${r.total} 门`
}
