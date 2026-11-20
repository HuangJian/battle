/** TrendConfigModal.tsx — 首页「走势配置」弹窗。
 *
 *  为什么改成弹窗（用户 2026-11-09）：90+ 个逐局指标原先从控制条下方展开，
 *  在 180px 限高里挤成一片，既分不清类型，也读不到释义。现在：
 *   · 按 `BEHAVIOR_METRIC_GROUPS` 分七节，两列排布；基础六图单独一节；
 *   · 每行同时显示中文名与**账本英文键**（与课程文件 formula 对照）；
 *   · hover / focus 任一指标，底部固定解释栏给出大白话释义——不占行内空间，
 *     也不会像浮层那样被滚动容器裁掉。
 *
 *  2026-11-14（用户）：弹窗最顶端多一节「已选走势」——按当前上屏顺序三列列出已勾选的图，
 *  ↑↓ 换位，× 取消显示（**原地置灰**，同一位置换 ✓ 恢复，行不消失——不然灰都没地方灰）。
 *  为什么单独占一节而不是在下面每个勾选项旁加箭头：一屏能看清「最后会出现在首页上的顺序」，
 *  不用在七个分组里来回找自己勾了哪些。
 *
 *  **草稿制（用户 2026-11-14）**：勾选 / 统计量 / 顺序都只改弹窗内的草稿，点「完成」才一次性
 *  提交（Hero 写三份 localStorage）；「关闭 / Esc / 点遮罩」= 放弃这次改动。理由：90+ 项一次
 *  改好几处时，即时写盘会让首页在弹窗底下反复重排，也把误点永久记下来。
 *
 *  多课对比（用户 2026-11-20）：同一份草稿里再带一组**全局叠线课**（最多 6 门，当前课也要自己勾进来）。
 *  为什么是全局一组而不是每图各配：色序跨图必须一致，否则「红=kb2」会在另一张图上变成别的课；
 *  首页小图再逐图配课只会把「看趋势」变成「配表」。开不开、选哪些课都跟勾选/顺序一起在「完成」落盘。
 */

import { useEffect, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import {
  BEHAVIOR_METRICS,
  BEHAVIOR_METRIC_GROUPS,
  behaviorMetricDef,
  behaviorTrendKey,
  colorOf,
  filterCourseCandidates,
  HOME_COMPARE_MAX_COURSES,
  METRIC_STAT_OPTIONS,
  moveTrendKey,
  parseMetricStat,
  resolveHomeCompareColors,
  toggleHomeCompareCourseInConfig,
  TREND_BASE_CHARTS,
  type BehaviorMetricKey,
  type BehaviorMetricStatMap,
  type HomeCompareConfig,
  type MetricStatKey,
  type TrendChartKey,
} from '../../view'

/** 点「完成」时交给 Hero 的整份草稿（顺序 / 显示表 / 统计量 / 多课对比一起提交）。 */
export interface TrendConfigDraft {
  /** 完整键序（含没勾选的键），成为首页的上屏顺序。 */
  order: TrendChartKey[]
  visibility: Record<TrendChartKey, boolean>
  metricStats: BehaviorMetricStatMap
  /** 首页走势图的多课对比（当前课恒在；courses 只存额外课）。 */
  compare: HomeCompareConfig
}

export interface TrendConfigModalProps {
  open: boolean
  /** 完整键序（含没勾选的），由 Hero 的 `resolveTrendOrder` 给出；顶部「已选走势」按它排。 */
  order: TrendChartKey[]
  visibility: Record<TrendChartKey, boolean>
  metricStats: BehaviorMetricStatMap
  /** 当前查看课程（多课对比里恒在、不占额外名额）。 */
  currentCourse: string
  /** 全部可选课程（已排除封存课）。 */
  courses: string[]
  /** 封存课（只列出来说明为什么不能选；没有逐轮账本）。 */
  archived?: string[]
  /** 首页多课对比配置（草稿的初始值）。 */
  compare: HomeCompareConfig
  /** 「完成」= 一次性提交草稿（唯一的生效通道）。 */
  onApply: (next: TrendConfigDraft) => void
  onClose: () => void
}

/** 草稿快照：连每门课的固定色位一起拷走。「完成」后 colors 丢了就会按位置重排颜色。 */
function snapshotCompare(c: HomeCompareConfig): HomeCompareConfig {
  return {
    on: c.on,
    courses: [...c.courses],
    ...(c.colors ? { colors: { ...c.colors } } : {}),
  }
}

/** 底部解释栏当前指向的指标（label / 账本英文键 / 大白话释义）。 */
interface ActiveHint {
  label: string
  key: string
  hint: string
}

/** 走势键 → 展示名 / 账本英文键 / 释义（基础图与逐局指标同一份，顶部列表用）。 */
function chartMeta(key: TrendChartKey): { label: string; ledger: string; hint: string } {
  if (key.startsWith('bm:')) {
    const m = behaviorMetricDef(key.slice(3))
    if (m) return { label: m.label, ledger: m.key, hint: m.hint }
  } else {
    const c = TREND_BASE_CHARTS.find((x) => x.key === key)
    if (c) return { label: c.label, ledger: c.key, hint: c.hint }
  }
  // 模型里已经没有这个键（旧存储残留）⇒ 照样列出来，不让序号/箭头错位。
  return { label: key, ledger: key, hint: '这一项在当前版本里已不存在。' }
}

export function TrendConfigModal({
  open,
  order,
  visibility,
  metricStats,
  currentCourse,
  courses,
  archived = [],
  compare,
  onApply,
  onClose,
}: TrendConfigModalProps) {
  const [active, setActive] = useState<ActiveHint | null>(null)
  /** 草稿：打开时从 props 快照一份，之后只改这里（关掉 = 丢弃）。 */
  const [draft, setDraft] = useState<TrendConfigDraft>(() => ({
    order: [...order],
    visibility: { ...visibility },
    metricStats: { ...metricStats },
    compare: snapshotCompare(compare),
  }))
  /** 本次打开时勾过的键：× 之后置灰但不离场，点 ✓ 就地恢复。 */
  const [pinned, setPinned] = useState<ReadonlySet<TrendChartKey>>(() => new Set())
  /** 对比课候选的搜索词（课程多时不用滚到底找）。 */
  const [courseQuery, setCourseQuery] = useState('')

  useEffect(() => {
    if (!open) return
    setDraft({
      order: [...order],
      visibility: { ...visibility },
      metricStats: { ...metricStats },
      compare: snapshotCompare(compare),
    })
    setPinned(new Set(order.filter((k) => visibility[k])))
    setCourseQuery('')
    // 只在「打开」这一拍快照；开着的过程中 props 变了（轮询）不应该冲掉用户正在改的草稿。
    // oxlint 未开 react 插件，这里不需要依赖表。
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  useEffect(() => {
    if (!open) setActive(null)
  }, [open])

  if (!open) return null

  /** 草稿里每门课的当前色位（增减课由 toggleHomeCompareCourseInConfig 同步维护）。 */
  const compareColors = resolveHomeCompareColors(draft.compare, currentCourse)

  const explain = (label: string, key: string, hint: string): void => {
    setActive({ label, key, hint })
  }

  const setOn = (key: TrendChartKey, on: boolean): void => {
    setDraft((d) => ({ ...d, visibility: { ...d.visibility, [key]: on } }))
    if (on) setPinned((p) => (p.has(key) ? p : new Set(p).add(key)))
  }
  const setStat = (key: BehaviorMetricKey, stat: MetricStatKey): void => {
    setDraft((d) => ({ ...d, metricStats: { ...d.metricStats, [key]: stat } }))
  }
  const move = (key: TrendChartKey, delta: -1 | 1): void => {
    setDraft((d) => ({
      ...d,
      order: moveTrendKey(d.order, key, delta, (k) => d.visibility[k]),
    }))
  }
  const setCompareOn = (on: boolean): void => {
    setDraft((d) => ({ ...d, compare: { ...d.compare, on } }))
  }
  const toggleCompareCourse = (course: string): void => {
    setDraft((d) => ({
      ...d,
      compare: toggleHomeCompareCourseInConfig(d.compare, currentCourse, course),
    }))
  }

  /** 顶部行：勾选中的 + 本次打开后取消过显示的（置灰那批）。 */
  const rows = draft.order.filter((k) => draft.visibility[k] || pinned.has(k))
  const onCount = rows.filter((k) => draft.visibility[k]).length

  /** 候选 = 全部课程（当前课也是列表里的普通一员：勾上它才会在首页走多课）。 */
  const candidates = courses
  const visibleCandidates = filterCourseCandidates(candidates, courseQuery)
  const archivedCandidates = archived
  const compareAtCap = draft.compare.courses.length >= HOME_COMPARE_MAX_COURSES

  const apply = (): void => {
    onApply({
      order: draft.order,
      visibility: draft.visibility,
      metricStats: draft.metricStats,
      compare: draft.compare,
    })
    onClose()
  }

  return (
    <div className="tc-modal-mask" onClick={onClose}>
      <div
        className="tc-modal tc-trendcfg"
        role="dialog"
        aria-modal="true"
        aria-label="走势配置"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="tc-trendcfg__hd">
          <h3>走势配置</h3>
          <p className="tc-muted tc-small tc-mt-0">
            勾选首页要显示的走势图，顶上按上屏顺序列出已勾选的图（↑↓ 换位置，× 取消显示）。
            每项都标了账本英文键，方便和课程文件的 formula 对照；鼠标移上去（或键盘聚焦），
            底部会用大白话解释它是什么、该看什么。下面的「多课对比」让首页走势图叠课：
            勾了哪些就叠哪些，不勾就只画当前课。改完点「完成」才生效。
          </p>
          <button type="button" className="tc-link" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="tc-trendcfg__body">
          {/* 顶部：勾选中的图按上屏顺序三列排开，↑↓ / × ✓ 直接改首页布局（用户 2026-11-14） */}
          <section className="tc-trendcfg__sec">
            <div className="tc-trendcfg__sec-hd">
              <h4>
                已选走势
                <span className="tc-trendcfg__count">{onCount}</span>
              </h4>
              <span className="tc-muted tc-small">
                首页按这里的顺序从左到右、从上到下铺图；↑↓ 换位置，× 取消显示（原地置灰，✓
                恢复），点「完成」才生效。
              </span>
            </div>
            {rows.length === 0 ? (
              <p className="tc-muted tc-small tc-mt-0">
                还没勾选任何走势图；在下面勾一项，它就会出现在这里。
              </p>
            ) : (
              <ol className="tc-trendcfg__picked" aria-label="已选走势（按上屏顺序）">
                {rows.map((k, i) => {
                  const meta = chartMeta(k)
                  const on = draft.visibility[k] === true
                  return (
                    <li
                      className={`tc-trendcfg__picked-row${on ? '' : ' tc-trendcfg__picked-row--off'}`}
                      key={k}
                      onMouseEnter={() => explain(meta.label, meta.ledger, meta.hint)}
                      onMouseLeave={() => setActive(null)}
                      onFocus={() => explain(meta.label, meta.ledger, meta.hint)}
                    >
                      <span className="tc-trendcfg__picked-i">{i + 1}</span>
                      <span className="tc-trendcfg__names">
                        <span className="tc-trendcfg__cn">{meta.label}</span>
                        <code className="tc-trendcfg__key">{meta.ledger}</code>
                      </span>
                      <span className="tc-trendcfg__picked-mv">
                        <button
                          type="button"
                          className="tc-trendcfg__mv"
                          aria-label={`${meta.label}上移`}
                          title="上移一位（只在已勾选的图之间换位）"
                          disabled={i === 0}
                          onClick={() => move(k, -1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className="tc-trendcfg__mv"
                          aria-label={`${meta.label}下移`}
                          title="下移一位（只在已勾选的图之间换位）"
                          disabled={i === rows.length - 1}
                          onClick={() => move(k, 1)}
                        >
                          ↓
                        </button>
                        {/* 就地取消 / 恢复：不必滚到下面七个分组里找同一项。 */}
                        {on ? (
                          <button
                            type="button"
                            className="tc-trendcfg__mv tc-trendcfg__mv--off"
                            aria-label={`${meta.label}取消显示`}
                            title="取消显示：原地置灰，点 ✓ 恢复（下面那一项同步取消勾选）"
                            onClick={() => setOn(k, false)}
                          >
                            ×
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="tc-trendcfg__mv tc-trendcfg__mv--on"
                            aria-label={`${meta.label}恢复显示`}
                            title="恢复显示（下面那一项同步勾上）"
                            onClick={() => setOn(k, true)}
                          >
                            ✓
                          </button>
                        )}
                      </span>
                    </li>
                  )
                })}
              </ol>
            )}
          </section>

          {/* 多课对比（用户 2026-11-20）：全局一组课，首页所有走势图同图叠线。
              当前课只是列表里的普通一员：勾进来才叠线，不勾就只画当前课。色序 = 这里的顺序。 */}
          <section className="tc-trendcfg__sec">
            <div className="tc-trendcfg__sec-hd">
              <h4>
                多课对比
                <span className="tc-trendcfg__count">{draft.compare.courses.length}</span>
              </h4>
              <span className="tc-muted tc-small">
                从下面勾选要叠线的课（最多 {HOME_COMPARE_MAX_COURSES} 门）。当前课也要自己勾上，
                没勾不会自动加进来，首页只画当前课一条；颜色跟课走，跨图一致。
              </span>
            </div>
            <div className="tc-trendcfg__cmp">
              <label className="tc-trendcfg__check">
                <input
                  type="checkbox"
                  checked={draft.compare.on}
                  onChange={(e) => setCompareOn(e.currentTarget.checked)}
                />
                <span>开启多课同图</span>
              </label>
              {draft.compare.courses.map((c, i) => (
                <span className="tc-chip" key={c}>
                  <svg
                    className="tc-trendcfg__cmp-dot"
                    viewBox="0 0 8 8"
                    width="8"
                    height="8"
                    aria-hidden="true"
                  >
                    <circle cx="4" cy="4" r="4" fill={colorOf(compareColors[c] ?? i + 1)} />
                  </svg>
                  {c}
                  <button
                    type="button"
                    className="tc-chip__x"
                    aria-label={`移除对比课 ${c}`}
                    title={`移除对比课 ${c}`}
                    onClick={() => toggleCompareCourse(c)}
                  >
                    ×
                  </button>
                </span>
              ))}
              {draft.compare.courses.length === 0 ? (
                <span className="tc-muted tc-small">
                  还没勾课。不勾就只画当前课；把当前课和其它课都勾上，首页才叠多课曲线。
                </span>
              ) : null}
              {compareAtCap ? (
                <span className="tc-muted tc-small">
                  最多 {HOME_COMPARE_MAX_COURSES} 门（含当前课；读账本成本随门数线性上涨）
                </span>
              ) : null}
            </div>
            <div className="tc-trendcfg__cmp-pick">
              <input
                className="tc-trendcfg__srch"
                type="search"
                placeholder="搜索课程"
                aria-label="搜索对比课"
                value={courseQuery}
                onInput={(e: JSX.TargetedInputEvent<HTMLInputElement>) =>
                  setCourseQuery(e.currentTarget.value)
                }
              />
              <div className="tc-trendcfg__list">
                {visibleCandidates.map((c) => {
                  const picked = draft.compare.courses.includes(c)
                  const disabled = !picked && compareAtCap
                  return (
                    <button
                      type="button"
                      className={`tc-trendcfg__cand${picked ? ' tc-trendcfg__cand--on' : ''}${
                        disabled ? ' tc-trendcfg__cand--off' : ''
                      }`}
                      key={c}
                      disabled={disabled}
                      aria-pressed={picked}
                      title={
                        disabled
                          ? `最多 ${HOME_COMPARE_MAX_COURSES} 门（含当前课）`
                          : picked
                            ? `取消对比 ${c}`
                            : `加入对比 ${c}`
                      }
                      onClick={() => toggleCompareCourse(c)}
                    >
                      {picked ? '✓ ' : ''}
                      {c}
                    </button>
                  )
                })}
                {archivedCandidates.map((a) => (
                  <span
                    className="tc-trendcfg__cand tc-trendcfg__cand--off"
                    key={`arch:${a}`}
                    title="封存课只留归档清单（不扫盘不解压）⇒ 没有逐轮账本可比"
                  >
                    {a} · 封存 · 无逐轮账本
                  </span>
                ))}
                {visibleCandidates.length === 0 && archivedCandidates.length === 0 ? (
                  <span className="tc-muted tc-small">没有可选的课程</span>
                ) : null}
              </div>
            </div>
          </section>

          <section className="tc-trendcfg__sec">
            <div className="tc-trendcfg__sec-hd">
              <h4>
                基础走势
                <span className="tc-trendcfg__count">{TREND_BASE_CHARTS.length}</span>
              </h4>
              <span className="tc-muted tc-small">
                首页常驻的六张图；统计量切换只作用于逐局指标。
              </span>
            </div>
            <div className="tc-trendcfg__grid tc-trendcfg__grid--base">
              {TREND_BASE_CHARTS.map((c) => (
                <div
                  className="tc-trendcfg__row"
                  key={c.key}
                  onMouseEnter={() => explain(c.label, c.key, c.hint)}
                  onMouseLeave={() => setActive(null)}
                  onFocus={() => explain(c.label, c.key, c.hint)}
                >
                  <label className="tc-trendcfg__check">
                    <input
                      type="checkbox"
                      checked={draft.visibility[c.key]}
                      onChange={(e) => setOn(c.key, e.currentTarget.checked)}
                    />
                    <span className="tc-trendcfg__names">
                      <span className="tc-trendcfg__cn">{c.label}</span>
                      <code className="tc-trendcfg__key">{c.key}</code>
                    </span>
                  </label>
                </div>
              ))}
            </div>
          </section>

          {BEHAVIOR_METRIC_GROUPS.map((g) => {
            const items = BEHAVIOR_METRICS.filter((m) => m.group === g.key)
            if (items.length === 0) return null
            return (
              <section className="tc-trendcfg__sec" key={g.key}>
                <div className="tc-trendcfg__sec-hd">
                  <h4>
                    {g.label}
                    <span className="tc-trendcfg__count">{items.length}</span>
                  </h4>
                  <span className="tc-muted tc-small">{g.desc}</span>
                </div>
                <div className="tc-trendcfg__grid">
                  {items.map((m) => {
                    const key = behaviorTrendKey(m.key)
                    return (
                      <div
                        className="tc-trendcfg__row"
                        key={key}
                        onMouseEnter={() => explain(m.label, m.key, m.hint)}
                        onMouseLeave={() => setActive(null)}
                        onFocus={() => explain(m.label, m.key, m.hint)}
                      >
                        <label className="tc-trendcfg__check">
                          <input
                            type="checkbox"
                            checked={draft.visibility[key]}
                            onChange={(e) => setOn(key, e.currentTarget.checked)}
                          />
                          <span className="tc-trendcfg__names">
                            <span className="tc-trendcfg__cn">{m.label}</span>
                            <code className="tc-trendcfg__key">{m.key}</code>
                          </span>
                        </label>
                        <select
                          className="tc-trendcfg__stat"
                          aria-label={`${m.label}统计量`}
                          value={draft.metricStats[m.key]}
                          onChange={(e) => setStat(m.key, parseMetricStat(e.currentTarget.value))}
                        >
                          {METRIC_STAT_OPTIONS.map((o) => (
                            <option value={o.value} key={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                      </div>
                    )
                  })}
                </div>
              </section>
            )
          })}
        </div>

        <div className="tc-trendcfg__explain" aria-live="polite">
          {active ? (
            <>
              <b>{active.label}</b>
              <code className="tc-trendcfg__key">{active.key}</code>
              <span>{active.hint}</span>
            </>
          ) : (
            <span className="tc-muted">把鼠标移到任一指标上：这里会说清它是什么、拿它看什么。</span>
          )}
        </div>

        <div className="tc-modal__foot tc-trendcfg__foot">
          <span className="tc-muted tc-small">
            点「完成」才生效并记住；关闭 / Esc / 点遮罩 =
            放弃这次改动。统计量默认均值；放大浮窗里还能临时加看其它统计曲线。
          </span>
          <span className="sp" />
          <button type="button" className="tc-btn tc-btn--primary" onClick={apply}>
            完成
          </button>
        </div>
      </div>
    </div>
  )
}
