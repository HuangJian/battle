/** TrendConfigModal.tsx — 首页「走势配置」弹窗。
 *
 *  为什么改成弹窗（用户 2026-11-09）：90+ 个逐局指标原先从控制条下方展开，
 *  在 180px 限高里挤成一片，既分不清类型，也读不到释义。现在：
 *   · 按 `BEHAVIOR_METRIC_GROUPS` 分七节，两列排布；基础六图单独一节；
 *   · 每行同时显示中文名与**账本英文键**（与课程文件 formula 对照）；
 *   · hover / focus 任一指标，底部固定解释栏给出大白话释义——不占行内空间，
 *     也不会像浮层那样被滚动容器裁掉。
 *
 *  改动仍走 Hero 的两条 localStorage 通道（显示表 + 逐指标统计量），即时生效。
 */

import { useEffect, useState } from 'preact/hooks'
import {
  BEHAVIOR_METRICS,
  BEHAVIOR_METRIC_GROUPS,
  behaviorTrendKey,
  METRIC_STAT_OPTIONS,
  parseMetricStat,
  TREND_BASE_CHARTS,
  type BehaviorMetricKey,
  type BehaviorMetricStatMap,
  type MetricStatKey,
  type TrendChartKey,
} from '../../view'

export interface TrendConfigModalProps {
  open: boolean
  visibility: Record<TrendChartKey, boolean>
  metricStats: BehaviorMetricStatMap
  onToggleChart: (key: TrendChartKey, on: boolean) => void
  onMetricStat: (key: BehaviorMetricKey, stat: MetricStatKey) => void
  onClose: () => void
}

/** 底部解释栏当前指向的指标（label / 账本英文键 / 大白话释义）。 */
interface ActiveHint {
  label: string
  key: string
  hint: string
}

export function TrendConfigModal({
  open,
  visibility,
  metricStats,
  onToggleChart,
  onMetricStat,
  onClose,
}: TrendConfigModalProps) {
  const [active, setActive] = useState<ActiveHint | null>(null)

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

  const explain = (label: string, key: string, hint: string): void => {
    setActive({ label, key, hint })
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
            勾选首页要显示的走势图。每项都标了账本英文键，方便和课程文件的 formula 对照；
            鼠标移上去（或键盘聚焦），底部会用大白话解释它是什么、该看什么。
          </p>
          <button type="button" className="tc-link" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="tc-trendcfg__body">
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
                      checked={visibility[c.key]}
                      onChange={(e) => onToggleChart(c.key, e.currentTarget.checked)}
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
                            checked={visibility[key]}
                            onChange={(e) => onToggleChart(key, e.currentTarget.checked)}
                          />
                          <span className="tc-trendcfg__names">
                            <span className="tc-trendcfg__cn">{m.label}</span>
                            <code className="tc-trendcfg__key">{m.key}</code>
                          </span>
                        </label>
                        <select
                          className="tc-trendcfg__stat"
                          aria-label={`${m.label}统计量`}
                          value={metricStats[m.key]}
                          onChange={(e) =>
                            onMetricStat(m.key, parseMetricStat(e.currentTarget.value))
                          }
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
            改动即时生效并记住。统计量默认均值；放大浮窗里还能临时加看其它统计曲线。
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
