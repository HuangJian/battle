/** Hero.tsx — 训练状态 hero（一屏焦点）：胜率大数字 + 大走势图；右侧击杀/道具/eval 三格趋势；
 *  下方最新 6 轮完整指标（紧凑表）。数据口径 = /api/state.metrics；「完整指标表 ›」进抽屉。 */

import {
  fmtPct,
  klTone,
  latestRow,
  metricSeries,
  retTone,
  winTone,
  type ConsoleStateView,
  type IterRow,
  type Series,
} from '../../../ui/view'
import { Badge } from '../../../ui/components/Pill'
import { Sparkline } from '../../../ui/components/Sparkline'

export interface HeroProps {
  stateView: ConsoleStateView | null
  onMore: () => void
}

/** 右侧趋势格：上「标签 + 最新值」下「通栏 sparkline」；数据缺失显示占位。 */
function TrendCell({
  series,
  fmt,
  tone,
}: {
  series: Series | undefined
  fmt: (v: number | null) => string
  tone?: 'g' | 'y' | 'r'
}) {
  const last = series ? (series.vals.filter(Number.isFinite).slice(-1)[0] ?? null) : null
  return (
    <div className="tc-tcell">
      <span className="tc-tcell__hd">
        <span className="tc-tcell__lbl">{series ? series.label : '—'}</span>
        <b className={tone ? `tc-mtrend__val--${tone}` : undefined}>{fmt(last)}</b>
      </span>
      {series ? <Sparkline values={series.vals} width={120} height={26} /> : null}
    </div>
  )
}

/** 最新 6 轮完整指标（紧凑表，iter 倒序）：主行口径与抽屉指标表一致（实际值优先、
 *  ≈ 为磁盘清理后的估算）；eval 列 = 干净评估（greedy 固定语料）。 */
function LastIters({ iters }: { iters: IterRow[] }) {
  const rows = [...iters].sort((a, b) => b.iter - a.iter).slice(0, 6)
  if (rows.length === 0) return null
  return (
    <div className="tc-hero__iters">
      <span className="tc-hero__iters-hd">最新 {rows.length} 轮完整指标</span>
      <table className="tc-table tc-table--dense">
        <thead>
          <tr>
            <th>iter</th>
            <th>时间</th>
            <th>胜率</th>
            <th>eval</th>
            <th className="tc-num">存活</th>
            <th className="tc-num">击杀</th>
            <th className="tc-num">道具</th>
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
                  <>
                    {r.actuals.totalKills}
                    <span className="tc-muted"> /{r.actuals.games}局</span>
                  </>
                ) : (
                  <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
                    {r.kills.toFixed(1)}≈
                  </span>
                )}
              </td>
              <td className="tc-num">
                {r.actuals ? (
                  <>
                    {r.actuals.totalPU}
                    <span className="tc-muted"> /{r.actuals.games}局</span>
                  </>
                ) : (
                  <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
                    {(r.loot * 100).toFixed(0)}%≈
                  </span>
                )}
              </td>
              <td className="tc-num">
                {r.scoreMean.toFixed(4)}
                <span className="tc-muted">±{r.scoreStd.toFixed(4)}</span>
              </td>
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

  if (!head) {
    return (
      <section className="tc-hero" aria-label="训练状态">
        <div className="tc-hero__kpi">
          <span className="tc-hero__lbl">采样胜率</span>
          <span className="tc-hero__val">—</span>
          <span className="tc-hero__sub">
            {stateView && stateView.metrics.available === false
              ? '该课程暂无迭代记录'
              : '等待最新迭代…'}
          </span>
        </div>
        <div className="tc-hero__right" style={{ alignContent: 'end' }}>
          <button type="button" className="tc-link" onClick={onMore}>
            完整指标表 ›
          </button>
        </div>
      </section>
    )
  }

  const winVal = fmtPct(head.winRate)
  const tone = winTone(head.winRate) === 'g' ? 'ok' : 'danger'
  const killsTxt = head.actuals ? `${head.actuals.totalKills}/${head.actuals.games}局` : '—'
  const evalTxt =
    head.evalData && head.evalData.winRate !== null ? `eval ${fmtPct(head.evalData.winRate)}` : null

  return (
    <section className="tc-hero" aria-label="训练状态">
      <div className="tc-hero__kpi">
        <span className="tc-hero__lbl">采样胜率 · it{head.iter}</span>
        <span className={`tc-hero__val tc-hero__val--${tone}`}>{winVal}</span>
        {winSeries ? <Sparkline values={winSeries.vals} width={170} height={30} /> : null}
        <span className="tc-hero__sub">
          击杀 {killsTxt}
          {evalTxt ? ` · ${evalTxt}` : ''}
        </span>
      </div>
      <div className="tc-hero__right">
        <div className="tc-trends">
          <TrendCell series={killsSeries} fmt={(v) => (v != null ? `${v.toFixed(0)}` : '—')} />
          <TrendCell series={puSeries} fmt={(v) => (v != null ? `${v.toFixed(0)}` : '—')} />
          <TrendCell
            series={evalSeries}
            tone={winTone(evalSeries?.vals.filter(Number.isFinite).slice(-1)[0] ?? 0)}
            fmt={fmtPct}
          />
        </div>
        <button type="button" className="tc-link tc-hero__more" onClick={onMore}>
          完整指标表 ›
        </button>
      </div>
      <LastIters iters={iters} />
    </section>
  )
}
