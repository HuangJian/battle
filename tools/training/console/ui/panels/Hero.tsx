/** Hero.tsx — 训练状态 hero：走势图（rollout 实线 + eval 琥珀虚线叠加，hover 双口径）
 *  + 最新 6 轮完整指标（非 eval 轮可手动入队 eval A，完成后回填）。
 *  数据口径 = /api/state.metrics；「完整指标表 ›」进抽屉。 */

import {
  filterGroups,
  fmtPaired,
  fmtPct,
  iterGroups,
  klTone,
  latestRow,
  metricSeries,
  retTone,
  TC_HERO_ITER_VIEW,
  TC_HERO_ITERS_COLLAPSED,
  TC_TREND_RANGE,
  winTone,
  type ConsoleStateView,
  type EvalCkptFile,
  type IterRow,
  type PairedReferee,
  type Series,
  type TrendRange,
} from '../../../ui/view'
import { Badge } from '../../../ui/components/Pill'
import { SegmentedControl } from '../../../ui/components/SegmentedControl'
import { TrendChart } from '../../../ui/components/TrendChart'
import { ckptForIter, loadCourseCkpts, startEvalA } from '../lib/eval-a'
import { useEffect, useRef, useState } from 'preact/hooks'

export interface HeroProps {
  stateView: ConsoleStateView | null
  onMore: () => void
  /** 触发评估入队后拉一次 /api/state（回填 eval 列）。 */
  onRefresh?: () => void
  readOnly?: boolean
}

/** 右侧趋势格：主序列实线 + 可选 eval 虚线叠加；hover 双口径。 */
function TrendCell({
  series,
  seriesEval,
  fmt,
  tone,
  range,
  yFloor,
  title,
}: {
  series: Series | undefined
  seriesEval?: Series | undefined
  fmt: (v: number | null) => string
  tone?: 'g' | 'y' | 'r'
  range: TrendRange
  /** y 轴下界上限：击杀/道具 0；胜率 0.3（基底不得高于 30%）。 */
  yFloor?: number
  /** 标签悬停提示（口径说明）。 */
  title?: string
}) {
  const last = series ? (series.vals.filter(Number.isFinite).slice(-1)[0] ?? null) : null
  const lastEval = seriesEval
    ? (seriesEval.vals.filter(Number.isFinite).slice(-1)[0] ?? null)
    : null
  return (
    <div className="tc-tcell">
      <span className="tc-tcell__hd">
        <span className="tc-tcell__lbl" title={title}>
          {series ? series.label : '—'}
        </span>
        <b className={tone ? `tc-mtrend__val--${tone}` : undefined}>
          {fmt(last)}
          {seriesEval && lastEval != null ? (
            <span className="tc-muted" style={{ fontWeight: 500 }}>
              {' / '}
              {fmt(lastEval)}
            </span>
          ) : null}
        </b>
      </span>
      {series ? (
        <TrendChart
          series={series}
          series2={seriesEval}
          range={range}
          fmt={fmt}
          tone={tone}
          height={56}
          yFloor={yFloor}
        />
      ) : null}
    </div>
  )
}

/** 击杀/道具 展示辅助：每局平均；击杀 1 位、道具 2 位小数。 */
function fmtPerGame(total: number, games: number, digits = 1): string {
  if (games <= 0) return String(total)
  return (total / games).toFixed(digits)
}

function fmtResidual(hp: number | null | undefined): string {
  return hp == null ? '-' : String(hp)
}

/** 胜局耗时（ticks）/ 胜局残血 展示：整数（平均值已四舍五入）。 */
function fmtInt(v: number | null): string {
  return v != null ? String(Math.round(v)) : '—'
}

/** 胜局耗时列：优先 avgWinTicks，旧缓存无该字段时回退全样本 avgTicks（标题注明）。 */
function winTicksCell(r: IterRow): string {
  if (r.actuals?.avgWinTicks != null) return String(r.actuals.avgWinTicks)
  if (r.actuals) return `${r.actuals.avgTicks}≈`
  return `${r.avgTicks}≈`
}

/** 配对裁判行（只读哨子，不进门判）：最新 eval vs 开腿 / vs 上一轮，同语料逐 seed 配对。
 * 灰（flat）是正常态——100 对下 99% 时间证据不够，不是故障。 */
function PairedRefereeLine({ ref }: { ref: PairedReferee | null | undefined }) {
  if (!ref || (!ref.vsFirst && !ref.vsPrev)) return null
  const title =
    '同语料逐 seed 配对（McNemar）：b01=基线输新权重赢（政绩）/b10=反之（学费）；' +
    '只看不一致对。灰=证据不够（正常态），不是故障；显著跌也只变色，不触发任何动作。'
  return (
    <div className="tc-muted tc-small" title={title} style={{ marginBottom: 4 }} role="status">
      配对裁判（贪心同卷）
      {ref.vsFirst ? (
        <span> · {fmtPaired(ref.vsFirst, `vs开腿it${ref.vsFirst.baseIter}`)}</span>
      ) : null}
      {ref.vsPrev ? (
        <span> · {fmtPaired(ref.vsPrev, `vs上一轮it${ref.vsPrev.baseIter}`)}</span>
      ) : null}
    </div>
  )
}

/** 按 iter 解析权重路径 → 见 lib/eval-a.ts */

/** 主行视图：最新 6 轮完整指标。非 eval 轮可手动启动 evalA（课程 A 层，完成后回填）。 */
function MainTable({
  rows,
  course,
  ckpts,
  readOnly,
  onEvalQueued,
  busyIters,
}: {
  rows: IterRow[]
  course: string
  ckpts: EvalCkptFile[]
  readOnly: boolean
  onEvalQueued: (iter: number, ckpt: string) => void
  busyIters: Set<number>
}) {
  return (
    <table className="tc-table tc-table--dense">
      <thead>
        <tr>
          <th>iter</th>
          <th>时间</th>
          <th>胜率</th>
          <th>eval</th>
          <th className="tc-num" title="胜局平均耗时（ticks）">
            胜局耗时
          </th>
          <th className="tc-num" title="每局平均击杀">
            击杀
          </th>
          <th className="tc-num" title="总承伤 / 总击杀（越小越会周旋）">
            承伤/杀
          </th>
          <th className="tc-num" title="胜局平均剩余 hp（剩余命每命计满额）">
            残血
          </th>
          <th className="tc-num" title="每局平均道具">
            道具
          </th>
          <th className="tc-num">rollout</th>
          <th className="tc-num">PPO</th>
          <th className="tc-num">得分</th>
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
              ) : !readOnly && course ? (
                <button
                  type="button"
                  className="tc-btn tc-btn--sm"
                  disabled={busyIters.has(r.iter)}
                  title={
                    ckptForIter(ckpts, r.iter)
                      ? `为 it${r.iter} 启动课程设计评估（evalA：该轮权重 × 固定语料，写 eval_log）`
                      : `未发现 it${r.iter} 权重归档，无法启动 evalA`
                  }
                  onClick={() => {
                    const ckpt = ckptForIter(ckpts, r.iter)
                    if (ckpt) onEvalQueued(r.iter, ckpt)
                  }}
                >
                  {busyIters.has(r.iter) ? '…' : 'eval A'}
                </button>
              ) : (
                <span className="tc-muted">-</span>
              )}
            </td>
            <td className="tc-num">
              <span title="胜局平均耗时（ticks）">{winTicksCell(r)}</span>
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
              {r.actuals?.dmgPerKill != null ? (
                <span title="总承伤 / 总击杀">{r.actuals.dmgPerKill.toFixed(1)}</span>
              ) : (
                <span className="tc-muted">-</span>
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
                fmtPerGame(r.actuals.totalPU, r.actuals.games, 2)
              ) : (
                <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
                  {(r.loot * 100).toFixed(0)}%≈
                </span>
              )}
            </td>
            <td className="tc-num">{r.rolloutSec.toFixed(0)}s</td>
            <td className="tc-num">{r.ppoSec.toFixed(0)}s</td>
            <td className="tc-num">{r.scoreMean.toFixed(4)}</td>
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
          <th className="tc-num" title="胜局平均耗时（ticks）">
            胜局耗时
          </th>
          <th className="tc-num" title="每局平均击杀">
            击杀
          </th>
          <th className="tc-num" title="总承伤 / 总击杀">
            承伤/杀
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
                  {e.avgWinTicks != null ? (
                    e.avgWinTicks
                  ) : e.avgTicks !== null ? (
                    <span title="无胜局耗时字段，回退全样本均值">{e.avgTicks}≈</span>
                  ) : (
                    <span className="tc-muted">-</span>
                  )}
                </td>
                <td className="tc-num">
                  {e.totalKills !== null ? (
                    fmtPerGame(e.totalKills, e.games)
                  ) : (
                    <span className="tc-muted">-</span>
                  )}
                </td>
                <td className="tc-num">
                  {e.dmgPerKill != null ? (
                    <span title="总承伤 / 总击杀">{e.dmgPerKill.toFixed(1)}</span>
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
                    fmtPerGame(e.totalPU, e.games, 2)
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
function LastIters({
  iters,
  onMore,
  course,
  ckpts,
  readOnly,
  onEvalQueued,
  busyIters,
}: {
  iters: IterRow[]
  onMore: () => void
  course: string
  ckpts: EvalCkptFile[]
  readOnly: boolean
  onEvalQueued: (iter: number, ckpt: string) => void
  busyIters: Set<number>
}) {
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
  // 折叠/展开（持久化；SSR 首帧恒展开，与 hydrate 一致，偏好 hydrate 后恢复）。
  const [collapsed, setCollapsed] = useState(false)
  useEffect(() => {
    try {
      if (localStorage.getItem(TC_HERO_ITERS_COLLAPSED) === '1') setCollapsed(true)
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onToggleCollapsed = (): void => {
    setCollapsed((c) => {
      const next = !c
      try {
        localStorage.setItem(TC_HERO_ITERS_COLLAPSED, next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }

  const mains = [...iters].sort((a, b) => b.iter - a.iter).slice(0, 6)
  if (mains.length === 0) return null
  const ev = view === 'eval'
  const n = ev ? filterGroups(iterGroups(iters), 'eval').length : mains.length
  return (
    <div className="tc-hero__iters">
      <div className="tc-hero__iters-hd">
        <span className="tc-hero__iters-left">
          <button
            type="button"
            className="tc-hero__iters-toggle"
            aria-expanded={!collapsed}
            aria-label={collapsed ? '展开最新指标表' : '折叠最新指标表'}
            title={collapsed ? '展开' : '折叠'}
            onClick={onToggleCollapsed}
          >
            {collapsed ? '▸' : '▾'}
          </button>
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
      {collapsed ? null : ev ? (
        <EvalTable rows={iters} />
      ) : (
        <MainTable
          rows={mains}
          course={course}
          ckpts={ckpts}
          readOnly={readOnly}
          onEvalQueued={onEvalQueued}
          busyIters={busyIters}
        />
      )}
    </div>
  )
}

export function Hero({ stateView, onMore, onRefresh, readOnly = false }: HeroProps) {
  const iters = stateView?.metrics.iters ?? []
  const course = stateView?.course ?? ''
  const head = latestRow(iters)
  const series = metricSeries(iters)
  const winSeries = series.find((m) => m.key === 'winRate')
  const evalSeries = series.find((m) => m.key === 'eval')
  const killsSeries = series.find((m) => m.key === 'kills')
  const evalKillsSeries = series.find((m) => m.key === 'evalKills')
  const puSeries = series.find((m) => m.key === 'pu')
  const evalPuSeries = series.find((m) => m.key === 'evalPu')
  const winTicksSeries = series.find((m) => m.key === 'winTicks')
  const evalWinTicksSeries = series.find((m) => m.key === 'evalWinTicks')
  const winHpSeries = series.find((m) => m.key === 'winHp')
  const evalWinHpSeries = series.find((m) => m.key === 'evalWinHp')
  const dmgPerKillSeries = series.find((m) => m.key === 'dmgPerKill')
  const evalDmgPerKillSeries = series.find((m) => m.key === 'evalDmgPerKill')

  // ckpt 发现（eval A 入队用）；课程切换重拉。
  const [ckpts, setCkpts] = useState<EvalCkptFile[]>([])
  const [busyIters, setBusyIters] = useState<Set<number>>(() => new Set())
  const [evalFlash, setEvalFlash] = useState<string | null>(null)
  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])
  useEffect(() => {
    if (!course || readOnly) {
      setCkpts([])
      return
    }
    let ok = true
    void loadCourseCkpts(course).then((files) => {
      if (ok) setCkpts(files)
    })
    return () => {
      ok = false
    }
  }, [course, readOnly])

  const onEvalQueued = (iter: number, ckpt: string): void => {
    if (readOnly || !course) return
    setBusyIters((s) => new Set(s).add(iter))
    setEvalFlash(`it${iter} evalA 启动中…`)
    void (async () => {
      try {
        const { ok, message } = await startEvalA(course, iter, ckpt)
        if (!aliveRef.current) return
        setEvalFlash(message)
        if (!ok) {
          setBusyIters((s) => {
            const n = new Set(s)
            n.delete(iter)
            return n
          })
          return
        }
        // 轻量回填：每 20s 拉一次 state，直到 iters 带上该 iter 的 evalData（effect 清 busy）。
        for (let i = 0; i < 30 && aliveRef.current; i++) {
          await new Promise((res) => setTimeout(res, 20_000))
          if (!aliveRef.current) return
          onRefresh?.()
        }
        if (aliveRef.current) {
          setBusyIters((s) => {
            const n = new Set(s)
            n.delete(iter)
            return n
          })
        }
      } catch (e) {
        if (!aliveRef.current) return
        setEvalFlash(String(e))
        setBusyIters((s) => {
          const n = new Set(s)
          n.delete(iter)
          return n
        })
      }
    })()
  }

  // eval 回填：iters 里出现 evalData 的轮，清除 busy。
  useEffect(() => {
    setBusyIters((s) => {
      if (s.size === 0) return s
      const next = new Set(s)
      for (const r of iters) {
        if (r.evalData && next.has(r.iter)) next.delete(r.iter)
      }
      return next.size === s.size ? s : next
    })
  }, [iters])

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
        {evalFlash ? (
          <div className="tc-muted tc-small" style={{ marginBottom: 4 }} role="status">
            {evalFlash}
          </div>
        ) : null}
        <div className="tc-trends">
          {/* 行1：胜率（rollout+eval） / 承伤·杀 / 击杀 */}
          <TrendCell
            series={winSeries}
            seriesEval={evalSeries}
            fmt={fmtPct}
            tone={tone}
            range={range}
            yFloor={0.3}
            title="rollout 采样胜率 + eval 胜率（橙色）"
          />
          <TrendCell
            series={dmgPerKillSeries}
            seriesEval={evalDmgPerKillSeries}
            fmt={(v) => (v != null ? v.toFixed(1) : '—')}
            range={range}
            yFloor={0}
            title="每杀承伤 = 总承伤 / 总击杀（rollout 实线 · eval 橙点）；越小越会周旋"
          />
          <TrendCell
            series={killsSeries}
            seriesEval={evalKillsSeries}
            fmt={(v) => (v != null ? `${v.toFixed(1)}` : '—')}
            range={range}
            yFloor={0}
            title="每局平均击杀（rollout 实线 · eval 橙点）"
          />
          {/* 行2：胜局耗时 / 胜局残血 / 道具 */}
          <TrendCell
            series={winTicksSeries}
            seriesEval={evalWinTicksSeries}
            fmt={fmtInt}
            range={range}
            title="胜局平均耗时（ticks；rollout 实线 · eval 橙点）"
          />
          <TrendCell
            series={winHpSeries}
            seriesEval={evalWinHpSeries}
            fmt={fmtInt}
            range={range}
            yFloor={0}
            title="胜局平均剩余 hp（rollout 实线 · eval 橙点）；剩余命每命计满额"
          />
          <TrendCell
            series={puSeries}
            seriesEval={evalPuSeries}
            fmt={(v) => (v != null ? `${v.toFixed(2)}` : '—')}
            range={range}
            yFloor={0}
            title="每局平均道具（rollout 实线 · eval 橙点）"
          />
        </div>
        <PairedRefereeLine ref={stateView?.metrics.pairedReferee} />
      </div>
      <LastIters
        iters={iters}
        onMore={onMore}
        course={course}
        ckpts={ckpts}
        readOnly={readOnly}
        onEvalQueued={onEvalQueued}
        busyIters={busyIters}
      />
    </section>
  )
}
