/** Hero.tsx — 训练状态 hero（一屏焦点）：胜率大数字 + 大走势图；右侧击杀/道具/eval 三格趋势；
 *  下方最新 6 轮完整指标（紧凑表）。数据口径 = /api/state.metrics；「完整指标表 ›」进抽屉。
 *  走势图支持悬停显示坐标，并由统一档位开关切换 全量/最近30/最近10（持久化到 localStorage）。 */

import {
  filterGroups,
  fmtPct,
  iterGroups,
  klTone,
  latestRow,
  metricSeries,
  retTone,
  TC_HERO_ITER_VIEW,
  TC_TREND_RANGE,
  winTone,
  type ConsoleStateView,
  type IterRow,
  type Series,
  type TrendRange,
} from '../../../ui/view'
import { Badge } from '../../../ui/components/Pill'
import { SegmentedControl } from '../../../ui/components/SegmentedControl'
import { TrendChart } from '../../../ui/components/TrendChart'
import { useEffect, useState } from 'preact/hooks'

export interface HeroProps {
  stateView: ConsoleStateView | null
  onMore: () => void
}

/** 右侧趋势格：上「标签 + 最新值」下「走势图」；数据缺失显示占位。 */
function TrendCell({
  series,
  fmt,
  tone,
  range,
  yFloor,
}: {
  series: Series | undefined
  fmt: (v: number | null) => string
  tone?: 'g' | 'y' | 'r'
  range: TrendRange
  /** y 轴下界上限：击杀/道具 0；胜率 0.3（基底不得高于 30%）。 */
  yFloor?: number
}) {
  const last = series ? (series.vals.filter(Number.isFinite).slice(-1)[0] ?? null) : null
  return (
    <div className="tc-tcell">
      <span className="tc-tcell__hd">
        <span className="tc-tcell__lbl">{series ? series.label : '—'}</span>
        <b className={tone ? `tc-mtrend__val--${tone}` : undefined}>{fmt(last)}</b>
      </span>
      {series ? (
        <TrendChart
          series={series}
          range={range}
          fmt={fmt}
          tone={tone}
          height={64}
          yFloor={yFloor}
        />
      ) : null}
    </div>
  )
}

/** 击杀/道具/残血 展示辅助：每局平均；残血仅胜局，无数据显示 -。 */
function fmtPerGame(total: number, games: number): string {
  if (games <= 0) return String(total)
  return (total / games).toFixed(1)
}

function fmtResidual(hp: number | null | undefined): string {
  return hp == null ? '-' : String(hp)
}

/** 主行视图：最新 6 轮完整指标（紧凑表，iter 倒序）：主行口径与抽屉指标表一致（实际值优先、
 *  ≈ 为磁盘清理后的估算）；eval 列 = 干净评估（greedy 固定语料）。
 *  击杀/道具 = 每局平均；残血 = 胜局平均剩余 hp（多命每命计满额）；得分不显示 ±std。 */
function MainTable({ rows }: { rows: IterRow[] }) {
  return (
    <table className="tc-table tc-table--dense">
      <thead>
        <tr>
          <th>iter</th>
          <th>时间</th>
          <th>胜率</th>
          <th>eval</th>
          <th className="tc-num" title="每局平均耗时（ticks）">
            耗时
          </th>
          <th className="tc-num" title="每局平均击杀">
            击杀
          </th>
          <th className="tc-num" title="胜局平均剩余 hp（剩余命每命计满额）">
            残血
          </th>
          <th className="tc-num" title="每局平均道具">
            道具
          </th>
          <th className="tc-num">得分</th>
          <th className="tc-num">rollout</th>
          <th className="tc-num">PPO</th>
          <th>KL</th>
          <th className="tc-num">熵</th>
          <th>mean_ret</th>
          <th className="tc-num">lr</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.iter}>
            <td>
              <b>{r.iter}</b>
              {r.halted ? (
                <span className="tc-pill tc-pill--note" title="该轮被 KL halt 中止">
                  halted
                </span>
              ) : null}
            </td>
            <td className="tc-muted" style={{ whiteSpace: 'nowrap' }}>
              {r.time}
            </td>
            <td>
              <Badge tone={winTone(r.winRate)}>{fmtPct(r.winRate)}</Badge>
            </td>
            <td>
              {r.evalData && r.evalData.winRate !== null ? (
                <Badge tone={winTone(r.evalData.winRate)}>{fmtPct(r.evalData.winRate)}</Badge>
              ) : (
                <span className="tc-muted">-</span>
              )}
            </td>
            <td className="tc-num">
              {r.actuals ? (
                r.actuals.avgTicks
              ) : (
                <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
                  {r.avgTicks}≈
                </span>
              )}
            </td>
            <td className="tc-num">
              {r.actuals ? (
                fmtPerGame(r.actuals.totalKills, r.actuals.games)
              ) : (
                <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
                  {r.kills.toFixed(1)}≈
                </span>
              )}
            </td>
            <td className="tc-num">
              {r.actuals ? (
                <span title="胜局平均剩余 hp；剩余多命时每命加满额 hp">
                  {fmtResidual(r.actuals.avgResidualHp)}
                </span>
              ) : (
                <span className="tc-muted">-</span>
              )}
            </td>
            <td className="tc-num">
              {r.actuals ? (
                fmtPerGame(r.actuals.totalPU, r.actuals.games)
              ) : (
                <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
                  {(r.loot * 100).toFixed(0)}%≈
                </span>
              )}
            </td>
            <td className="tc-num">{r.scoreMean.toFixed(4)}</td>
            <td className="tc-num">{r.rolloutSec.toFixed(0)}s</td>
            <td className="tc-num">{r.ppoSec.toFixed(0)}s</td>
            <td>
              <Badge tone={klTone(r.kl)}>{r.kl.toFixed(4)}</Badge>
            </td>
            <td className="tc-num">{r.entropy.toFixed(3)}</td>
            <td>
              <Badge tone={retTone(r.meanRet)}>{r.meanRet.toFixed(3)}</Badge>
            </td>
            <td className="tc-num">{r.lr.toFixed(6)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** eval 视图：最新 6 轮干净评估（有 evalData 的轮，iter 倒序；与抽屉 eval 过滤同口径）。
 *  列 = 评估专属字段：eval 胜率（含局数）、全歼、耗时/击杀/残血/道具/得分（评估实际值；
 *  击杀/道具为每局平均，残血为胜局平均，得分无 ±std）、窗口用时、评估权重版本。 */
function EvalTable({ rows }: { rows: IterRow[] }) {
  const groups = filterGroups(iterGroups(rows), 'eval').slice(0, 6)
  return (
    <table className="tc-table tc-table--dense">
      <thead>
        <tr>
          <th>iter</th>
          <th>时间</th>
          <th>eval 胜率</th>
          <th className="tc-num">全歼</th>
          <th className="tc-num" title="每局平均耗时（ticks）">
            耗时
          </th>
          <th className="tc-num" title="每局平均击杀">
            击杀
          </th>
          <th className="tc-num" title="胜局平均剩余 hp（剩余命每命计满额）">
            残血
          </th>
          <th className="tc-num" title="每局平均道具">
            道具
          </th>
          <th className="tc-num">得分</th>
          <th className="tc-num">用时</th>
          <th>wver</th>
        </tr>
      </thead>
      <tbody>
        {groups.length === 0 ? (
          <tr>
            <td colSpan={11} className="tc-muted" style={{ textAlign: 'center' }}>
              该课程暂无 eval 评估记录
            </td>
          </tr>
        ) : (
          groups.map((g) => {
            const e = g.eval!
            return (
              <tr key={`e${g.iter}`}>
                <td>
                  <span className="tc-muted" style={{ whiteSpace: 'nowrap' }}>
                    eval it{g.iter}
                  </span>
                  {e.dropped > 0 ? (
                    <span
                      className="tc-pill tc-pill--note"
                      title="评估窗口内未收官、被下轮权重分发清场的评估局数"
                    >
                      缺{e.dropped}
                    </span>
                  ) : null}
                </td>
                <td className="tc-muted" style={{ whiteSpace: 'nowrap' }}>
                  {e.time}
                </td>
                <td>
                  {e.winRate !== null ? (
                    <>
                      <Badge
                        tone={winTone(e.winRate)}
                        title={`干净评估（greedy 固定语料）· 评估权重 = 第 ${g.iter} 轮 PPO 更新前 · ${e.games} 局 ${e.wins} 胜 · 全歼 ${e.clears} · 用时 ${e.sec}s`}
                      >
                        {fmtPct(e.winRate)}
                      </Badge>{' '}
                      <span className="tc-muted">
                        {e.wins}/{e.games}
                      </span>
                    </>
                  ) : (
                    <span className="tc-muted">-</span>
                  )}
                </td>
                <td className="tc-num">
                  {e.clearRate !== null ? (
                    <span title={`全歼率 ${fmtPct(e.clearRate)}`}>{e.clears}</span>
                  ) : (
                    <span className="tc-muted">{e.clears || '-'}</span>
                  )}
                </td>
                <td className="tc-num">
                  {e.avgTicks !== null ? e.avgTicks : <span className="tc-muted">-</span>}
                </td>
                <td className="tc-num">
                  {e.totalKills !== null ? (
                    fmtPerGame(e.totalKills, e.games)
                  ) : (
                    <span className="tc-muted">-</span>
                  )}
                </td>
                <td className="tc-num">
                  {e.avgResidualHp != null ? (
                    <span title="胜局平均剩余 hp；剩余多命时每命加满额 hp">{e.avgResidualHp}</span>
                  ) : (
                    <span className="tc-muted">-</span>
                  )}
                </td>
                <td className="tc-num">
                  {e.totalPU !== null ? (
                    fmtPerGame(e.totalPU, e.games)
                  ) : (
                    <span className="tc-muted">-</span>
                  )}
                </td>
                <td className="tc-num">
                  {e.scoreMean !== null ? (
                    e.scoreMean.toFixed(4)
                  ) : (
                    <span className="tc-muted">-</span>
                  )}
                </td>
                <td className="tc-num">{e.sec.toFixed(0)}s</td>
                <td className="tc-mono tc-muted tc-small">{e.wver.slice(0, 7)}</td>
              </tr>
            )
          })
        )}
      </tbody>
    </table>
  )
}

/** 最新 6 轮区块：主行 / eval 双视图 toggle（持久化 localStorage）；表头行右侧
 *  「完整指标表 ›」进指标抽屉（与标题同一行、右对齐）。 */
function LastIters({ iters, onMore }: { iters: IterRow[]; onMore: () => void }) {
  const [view, setView] = useState<'main' | 'eval'>('main')
  // hydrate 后从 localStorage 恢复视图（SSR 首帧恒主行，避免 hydration 不一致）。
  useEffect(() => {
    try {
      const v = localStorage.getItem(TC_HERO_ITER_VIEW)
      if (v === 'main' || v === 'eval') setView(v)
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onView = (v: 'main' | 'eval'): void => {
    setView(v)
    try {
      localStorage.setItem(TC_HERO_ITER_VIEW, v)
    } catch {
      /* ignore */
    }
  }

  const mains = [...iters].sort((a, b) => b.iter - a.iter).slice(0, 6)
  if (mains.length === 0) return null
  const ev = view === 'eval'
  const n = ev ? filterGroups(iterGroups(iters), 'eval').length : mains.length
  return (
    <div className="tc-hero__iters">
      <div className="tc-hero__iters-hd">
        <span className="tc-hero__iters-left">
          <span>
            最新 {Math.min(n, 6)} 轮{ev ? ' eval 评估' : '完整指标'}
          </span>
          <SegmentedControl<'main' | 'eval'>
            value={view}
            ariaLabel="指标行视图"
            options={[
              { value: 'main', label: '主行' },
              { value: 'eval', label: 'eval' },
            ]}
            onChange={onView}
          />
        </span>
        <button type="button" className="tc-link" onClick={onMore}>
          完整指标表 ›
        </button>
      </div>
      {ev ? <EvalTable rows={iters} /> : <MainTable rows={mains} />}
    </div>
  )
}

export function Hero({ stateView, onMore }: HeroProps) {
  const iters = stateView?.metrics.iters ?? []
  const head = latestRow(iters)
  const series = metricSeries(iters)
  const winSeries = series.find((m) => m.key === 'winRate')
  const killsSeries = series.find((m) => m.key === 'kills')
  const puSeries = series.find((m) => m.key === 'pu')
  const evalSeries = series.find((m) => m.key === 'eval')

  // 走势范围档位（全量 / 最近30 / 最近10）；持久化到 localStorage，hydrate 后恢复。
  const [range, setRange] = useState<TrendRange>('30')
  useEffect(() => {
    try {
      const v = localStorage.getItem(TC_TREND_RANGE)
      if (v === 'all' || v === '30' || v === '10') setRange(v)
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onRange = (r: TrendRange): void => {
    setRange(r)
    try {
      localStorage.setItem(TC_TREND_RANGE, r)
    } catch {
      /* ignore */
    }
  }

  if (!head) {
    return (
      <section className="tc-hero" aria-label="训练状态">
        <div className="tc-hero__right">
          <div className="tc-tcell">
            <span className="tc-tcell__hd">
              <span className="tc-tcell__lbl">采样胜率</span>
              <b>—</b>
            </span>
            <span className="tc-muted tc-small">
              {stateView && stateView.metrics.available === false
                ? '该课程暂无迭代记录'
                : '等待最新迭代…'}
            </span>
          </div>
          <button type="button" className="tc-link" onClick={onMore}>
            完整指标表 ›
          </button>
        </div>
      </section>
    )
  }

  const winVal = fmtPct(head.winRate)
  const tone = winTone(head.winRate)

  return (
    <section className="tc-hero" aria-label="训练状态">
      <div className="tc-hero__right">
        <div className="tc-trend-range" role="group" aria-label="走势范围">
          {(['all', '30', '10'] as TrendRange[]).map((r) => (
            <button
              key={r}
              type="button"
              className={`tc-trend-range__btn${range === r ? ' tc-trend-range__btn--on' : ''}`}
              aria-pressed={range === r}
              onClick={() => onRange(r)}
            >
              {r === 'all' ? '全量' : `最近${r}`}
            </button>
          ))}
        </div>
        <div className="tc-trends">
          <div className="tc-tcell">
            <span className="tc-tcell__hd">
              <span className="tc-tcell__lbl">胜率</span>
              <b className={`tc-mtrend__val--${tone}`}>{winVal}</b>
            </span>
            {winSeries ? (
              <TrendChart
                series={winSeries}
                range={range}
                fmt={fmtPct}
                tone={tone}
                height={64}
                yFloor={0.3}
              />
            ) : null}
          </div>
          <TrendCell
            series={killsSeries}
            fmt={(v) => (v != null ? `${v.toFixed(1)}` : '—')}
            range={range}
            yFloor={0}
          />
          <TrendCell
            series={puSeries}
            fmt={(v) => (v != null ? `${v.toFixed(1)}` : '—')}
            range={range}
            yFloor={0}
          />
          <TrendCell
            series={evalSeries}
            tone={winTone(evalSeries?.vals.filter(Number.isFinite).slice(-1)[0] ?? 0)}
            fmt={fmtPct}
            range={range}
            yFloor={0.3}
          />
        </div>
      </div>
      <LastIters iters={iters} onMore={onMore} />
    </section>
  )
}
