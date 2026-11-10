/** MetricsConfigModal.tsx — 逐轮指标表的列配置弹窗（用户 2026-11-10）。
 *
 *  两个调用方：指标页「指标配置」与首页速览表「列配置」。两页的**备选列是同一份**（都来自
 *  `metric-table-cols.tsx` 的列工厂），所以弹窗只需窄接口（键/表头/释义）；`title` 区分页名。
 *
 *  与首页「走势配置」（`TrendConfigModal`）同一形状、同一理由：逐局指标有 90+ 项，
 *  挤在表头的小「列▾」里既分不清类型，也看不到释义。这里：
 *   · 基础列一节、逐局指标按 `BEHAVIOR_METRIC_GROUPS` 分七节，两列排布；
 *   · 每行一个勾选框 = 这一列是否上屏（显隐的**唯一**入口：本页不再渲染表格自带的「列▾」）；
 *   · 逐局指标另有统计量下拉（均值/中位数/最小/最大/p90/p99）——表格里放哪个读数由它决定；
 *   · hover / 键盘聚焦任一项，底部固定释义栏用大白话解释它是什么、拿它看什么。
 *
 *  改动即时生效并记住（存储键见 `view/legacy-keys.ts` 的 `tc.metrics.cols` / `tc.metrics.stat`）。 */

import { useEffect, useState } from 'preact/hooks'
import {
  BEHAVIOR_METRICS,
  BEHAVIOR_METRIC_GROUPS,
  behaviorColKeyOf,
  METRIC_STAT_OPTIONS,
  parseMetricStat,
  type BehaviorMetricKey,
  type BehaviorMetricStatMap,
  type MetricStatKey,
} from '../../view'

/** 弹窗只需要列的这三个字段（`MetricCol` 与 `DataTable` 的 `Col` 都长这样，不必各转一次）。 */
export interface MetricsConfigCol {
  key: string
  label: string
  /** 这一列的大白话解释（表头 hover 口径）。 */
  hint?: string
}

export interface MetricsConfigModalProps {
  open: boolean
  /** 当前行过滤模式下的列（含 `bm:*` 逐局指标列；顺序 = 表里的顺序）。 */
  cols: MetricsConfigCol[]
  /** 已隐藏的列键。 */
  hidden: Set<string>
  /** 逐指标统计量（两张带逐局指标列的表都会传；不传 = 这页不渲染统计量下拉）。 */
  stats?: BehaviorMetricStatMap
  onToggle: (key: string, on: boolean) => void
  onStat?: (key: BehaviorMetricKey, stat: MetricStatKey) => void
  onClose: () => void
  /** 弹窗标题（缺省「指标表配置」）。 */
  title?: string
  /** 顶部说明第二句（缺省按有没有统计量分两套）。 */
  intro?: string
}

/** 底部解释栏当前指向的项。 */
interface ActiveHint {
  label: string
  key: string
  hint: string
}

export function MetricsConfigModal({
  open,
  cols,
  hidden,
  stats,
  onToggle,
  onStat,
  onClose,
  title = '指标表配置',
  intro,
}: MetricsConfigModalProps) {
  const [active, setActive] = useState<ActiveHint | null>(null)
  /** 统计量下拉只在调用方同时给出状态与回调时渲染（首页速览表没有逐局指标列）。 */
  const withStats = stats !== undefined && onStat !== undefined

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

  // 列 → 两个视图：逐局指标（按账本键归组）与基础列（保持表里的顺序）。
  const bmCols = new Map<BehaviorMetricKey, MetricsConfigCol>()
  const baseCols: MetricsConfigCol[] = []
  for (const c of cols) {
    const k = behaviorColKeyOf(c.key)
    if (k) bmCols.set(k, c)
    else baseCols.push(c)
  }

  const explain = (label: string, key: string, hint: string): void => {
    setActive({ label, key, hint })
  }

  /** 基础列一行：只有显隐（它们不是逐局分布，统计量切换不适用）。 */
  const baseRow = (c: MetricsConfigCol) => (
    <div
      className="tc-trendcfg__row"
      key={c.key}
      onMouseEnter={() => explain(c.label, c.key, c.hint ?? '这一列不需要额外解释，看表头就知道。')}
      onMouseLeave={() => setActive(null)}
      onFocus={() => explain(c.label, c.key, c.hint ?? '这一列不需要额外解释，看表头就知道。')}
    >
      <label className="tc-trendcfg__check">
        <input
          type="checkbox"
          checked={!hidden.has(c.key)}
          onChange={(e) => onToggle(c.key, e.currentTarget.checked)}
        />
        <span className="tc-trendcfg__names">
          <span className="tc-trendcfg__cn">{c.label}</span>
        </span>
      </label>
    </div>
  )

  return (
    <div className="tc-modal-mask" onClick={onClose}>
      <div
        className="tc-modal tc-trendcfg"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="tc-trendcfg__hd">
          <h3>{title}</h3>
          <p className="tc-muted tc-small tc-mt-0">
            {intro ??
              (withStats
                ? '勾选表里要显示的列。逐局指标列还能各自选统计量（均值 / 中位数 / 最小 / 最大 / p90 / p99）——同一列可以放均值，也可以放中位数。鼠标移上去（或键盘聚焦），底部会用大白话解释它是什么。'
                : '勾选表里要显示的列。鼠标移上去（或键盘聚焦），底部会用大白话解释它是什么、该看什么。')}
          </p>
          <button type="button" className="tc-link" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="tc-trendcfg__body">
          {baseCols.length > 0 ? (
            <section className="tc-trendcfg__sec">
              <div className="tc-trendcfg__sec-hd">
                <h4>
                  基础列
                  <span className="tc-trendcfg__count">{baseCols.length}</span>
                </h4>
                <span className="tc-muted tc-small">
                  iter / 时间 / 胜率 / 技能读数 / PPO 诊断这些固定列
                  {withStats ? '；统计量切换只作用于逐局指标。' : '。'}
                </span>
              </div>
              <div className="tc-trendcfg__grid tc-trendcfg__grid--base">
                {baseCols.map(baseRow)}
              </div>
            </section>
          ) : null}

          {BEHAVIOR_METRIC_GROUPS.map((g) => {
            const items = BEHAVIOR_METRICS.filter((m) => m.group === g.key && bmCols.has(m.key))
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
                    const col = bmCols.get(m.key)!
                    return (
                      <div
                        className="tc-trendcfg__row"
                        key={col.key}
                        onMouseEnter={() => explain(m.label, m.key, m.hint)}
                        onMouseLeave={() => setActive(null)}
                        onFocus={() => explain(m.label, m.key, m.hint)}
                      >
                        <label className="tc-trendcfg__check">
                          <input
                            type="checkbox"
                            checked={!hidden.has(col.key)}
                            onChange={(e) => onToggle(col.key, e.currentTarget.checked)}
                          />
                          <span className="tc-trendcfg__names">
                            <span className="tc-trendcfg__cn">{m.label}</span>
                            <code className="tc-trendcfg__key">{m.key}</code>
                          </span>
                        </label>
                        {withStats ? (
                          <select
                            className="tc-trendcfg__stat"
                            aria-label={`${m.label}统计量`}
                            value={stats![m.key]}
                            onChange={(e) => onStat!(m.key, parseMetricStat(e.currentTarget.value))}
                          >
                            {METRIC_STAT_OPTIONS.map((o) => (
                              <option value={o.value} key={o.value}>
                                {o.label}
                              </option>
                            ))}
                          </select>
                        ) : null}
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
            <span className="tc-muted">把鼠标移到任一列上：这里会说清它是什么、拿它看什么。</span>
          )}
        </div>

        <div className="tc-modal__foot tc-trendcfg__foot">
          <span className="tc-muted tc-small">
            改动即时生效并记住。
            {withStats
              ? '逐局指标默认隐藏、统计量默认均值；表格单元格 hover 仍列全六种统计量。'
              : '藏起来的列不会上屏，数据本身不受影响。'}
          </span>
          <span className="sp" />
          <button type="button" className="tc-btn tc-btn--primary" onClick={onClose}>
            完成
          </button>
        </div>
      </div>
    </div>
  )
}
