/** NodeStats.tsx — 抽屉「节点统计」tab：/api/pool 独立慢节奏 + 窗口切换 + 列 + 行展开 + 仅看异常。
 *  迁移自旧 NodesPanel 的 StatsView（控制视图由 NodePills 承接）。
 *
 *  ★ 2026-09-26（plan/nodes-decouple-from-course.plan.md）：节点统计与课程解耦 ——
 *   · 顶部 Segmented 切**本地日窗口**（今天/昨天/7 天/全部）；切天只重取、服务端零重算；
 *   · 「上轮贡献」→ **窗口内局数**（rollout/eval 两列）；「状态」列改名「成功率」
 *     （与 pill 的「产能」分列分名）；
 *   · 课程内序号 `it` 不再出现在任何列（只作服务端内部过滤器）。 */

import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import {
  fmtBytes,
  fmtTs,
  TC_NODE_VIEW,
  WINDOW_OPTIONS,
  type NodeHistoryRow,
  type PoolView,
  type PoolWindowKey,
} from '../../view'
import { Badge, Pill } from '../../components/Pill'
import { DataTable, type Col } from '../../components/DataTable'
import { SegmentedControl } from '../../components/SegmentedControl'
import { usePolling } from '../lib/usePolling'
import { fetchPool } from '../lib/api-client'

export interface NodeStatsProps {
  /** 抽屉开着才轮询（DS-U5 语义；展开补拉一次）。 */
  enabled: boolean
  poolFreshNonce: number
}

/** 成功率（窗口内最近 ≤10 次结算的完成率；阈值 ≥90% / ≥70% / <70%）。
 *  ★ 与 pill 的「产能」（`nodeHealth`：贡献 vs 并发）**分列分名**——两者不是一个指标。 */
const statusBadge = (s: NodeHistoryRow['status'], okN: number, recentN: number) => {
  switch (s) {
    case 'healthy':
      return (
        <Badge tone="g">
          达标 {okN}/{recentN}
        </Badge>
      )
    case 'warn':
      return (
        <Badge tone="y">
          波动 {okN}/{recentN}
        </Badge>
      )
    case 'bad':
      return (
        <Badge tone="r">
          异常 {okN}/{recentN}
        </Badge>
      )
    case 'noping':
      return <Badge tone="r">无 ping · 无历史</Badge>
    case 'nodata':
      return <Badge tone="gray">无数据</Badge>
    case 'disabled':
      return <Badge tone="gray">disabled</Badge>
  }
}

/** 展示秒：仅正有限数渲染 `Ns`；null/undefined/旧 API 缺键 → `-`（防 undefineds）。 */
const secCell = (v: number | null | undefined): string =>
  typeof v === 'number' && Number.isFinite(v) ? `${v}s` : '-'

const poolColumns: Col<NodeHistoryRow>[] = [
  {
    key: 'id',
    label: '节点',
    cell: (r) => (
      <b>
        {r.id}
        {r.kind === 'local' ? <span className="tc-muted tc-muted--plain">（本机直跑）</span> : null}
      </b>
    ),
  },
  {
    key: 'status',
    label: '成功率',
    thTitle:
      '成功率 = 窗口内最近 ≤10 次结算的完成率（≥90% 达标 / ≥70% 波动 / <70% 异常）。' +
      '与节点 pill 的「产能」（最近完成轮贡献 vs 并发）不是同一个指标。成功率随所选窗口变。',
    sortValue: (r) =>
      ({ healthy: 4, warn: 3, bad: 2, noping: 1, nodata: 1, disabled: 0 })[r.status],
    cell: (r) => statusBadge(r.status, r.okN, r.recentN),
  },
  {
    key: 'spec',
    label: '规格',
    cell: (r) => (r.spec ? <span className="tc-muted">{r.spec}</span> : '-'),
  },
  {
    key: 'version',
    label: '版本',
    cell: (r) => (
      <>
        {r.version ? (
          <span className="tc-mono tc-small">{r.version}</span>
        ) : (
          <span className="tc-muted">-</span>
        )}
        {r.versionOk === false ? (
          <Pill
            tone="y"
            title={`local=${r.versionLocal || '?'} remote=${r.version || '?'} — 在节点跑 bun tools/agent/codehash-report.ts 与本机 diff`}
          >
            旧
          </Pill>
        ) : null}
      </>
    ),
  },
  {
    key: 'pingMs',
    label: 'ping',
    align: 'num',
    cell: (r) => (r.pingMs !== null ? `${r.pingMs}ms` : '-'),
  },
  { key: 'ok', label: '已结算', align: 'num', cell: (r) => r.ok },
  {
    key: 'fail',
    label: '失败',
    align: 'num',
    cell: (r) => (r.fail > 0 ? r.fail : <span className="tc-muted">0</span>),
  },
  {
    // 2026-09-26：贡献改**窗口口径**（所选「天」窗口内成功局数），按 mode 分两列。
    key: 'win',
    label: '窗口内局数 rl/ev',
    align: 'num',
    thTitle: '所选窗口内该节点的成功局数（rollout / eval）。课程内序号 it 不作展示口径。',
    cell: (r) => (
      <span title={`rollout ${r.winRollout} · eval ${r.winEval}（窗口内成功局数）`}>
        {r.winRollout}/{r.winEval}
      </span>
    ),
  },
  {
    key: 'avgElapsedSec',
    label: '平均耗时',
    align: 'num',
    thTitle: '节点侧服务时长滑动平均（≤50 成功局）：接单→结果就绪，含冷启动，不含网络',
    cell: (r) => secCell(r.avgElapsedSec),
  },
  {
    key: 'avgWallSec',
    label: '机侧墙钟',
    align: 'num',
    thTitle: '训练机派发→结算墙钟滑动平均（≤50 成功局）：含网络/异步轮询；与平均耗时并列，不覆盖',
    cell: (r) => secCell(r.avgWallSec),
  },
  { key: 'lastOkTs', label: '最近成功', cell: (r) => r.lastOkTs || '-' },
  {
    key: 'lastError',
    label: '最近错误',
    cell: (r) =>
      r.lastError ? (
        <span>
          <span className="tc-errtext">{r.lastError}</span>
          {r.lastFailTs ? <br /> : null}
          <span className="tc-muted">{r.lastFailTs}</span>
        </span>
      ) : (
        <span className="tc-muted">-</span>
      ),
  },
]

function rowExpand(r: NodeHistoryRow) {
  return (
    <div>
      <div className="tc-recent-dots">
        <span className="tc-muted">最近 10 次结算：</span>
        {r.recent.length === 0 ? (
          <span className="tc-muted">无记录</span>
        ) : (
          r.recent.map((ok, i) => (
            <span
              key={i}
              className={`tc-dot tc-dot--${ok ? 'ok' : 'fail'}`}
              title={ok ? '成功' : '失败'}
            />
          ))
        )}
      </div>
      {r.kind === 'local' ? (
        <div className="tc-muted">本机直跑槽位（rl-config rl.local_slots）</div>
      ) : (
        <div className="tc-muted">
          窗口内 {r.winRollout} rollout / {r.winEval} eval 局 · 最近完成轮贡献{' '}
          {r.lastContrib < 0 ? '—' : r.lastContrib} 局 · 平均耗时 {secCell(r.avgElapsedSec)} ·
          机侧墙钟 {secCell(r.avgWallSec)}
        </div>
      )}
      {r.lastError ? (
        <div className="tc-errtext tc-mt-2">
          错误明细：{r.lastError}
          {r.lastFailTs ? <span className="tc-muted">（{r.lastFailTs}）</span> : null}
        </div>
      ) : null}
    </div>
  )
}

export function NodeStats({ enabled, poolFreshNonce }: NodeStatsProps) {
  const [pool, setPool] = useState<PoolView | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [showOnlyBad, setShowOnlyBad] = useState(false)
  /** 统计窗口（本地日）：今天 / 昨天 / 7 天 / 全部。切天只改它 + 重取（服务端零重算）。 */
  const [days, setDays] = useState<PoolWindowKey>('today')

  /** 拉一次池视图。`fresh` = 显式「现在就给我新的」（服务端**硬清 + 等一次重算**，2.5s 级）；
   *  其余一律软拉（服务端在动作后已软作废：立刻给旧值 + 后台重算）。返回本次视图供再校验比对。 */
  const load = useCallback(
    async (fresh: boolean): Promise<PoolView | null> => {
      try {
        const p = await fetchPool(fresh, days)
        setPool(p)
        setErr(null)
        return p
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e))
        return null
      }
    },
    [days],
  )

  usePolling({ enabled, intervalSec: 300, fetch: () => load(false).then(() => undefined) })

  // 切天 → 重取（跳过首次：usePolling 已在 enabled 翻转时补拉）。服务端只重投影、不重算探测。
  const firstDays = useRef(true)
  useEffect(() => {
    if (firstDays.current) {
      firstDays.current = false
      return
    }
    if (enabled) void load(false)
  }, [days, enabled, load])

  // ── 再校验（2026-09-22）──
  // 动作 / 切课 / iter 前进 / 连接恢复 → `poolFreshNonce++`。这里**不再** `load(true)`：
  // 那会硬清服务端缓存并**把面板按住 2.5s** 等逐节点 ping（实测冷 2448–2503ms）。现在是：
  //   ① 立即**软拉**一次 —— 服务端的软作废保证这一读即时（旧值 + 重算已在后台跑）；
  //   ② 服务端 `cachedAt` 没推进（= 重算还没落地）就在 1.5s 后重拉，最多 3 次（≈4.5s）
  //      —— 池轮询间隔是 300s，少了这一步就会停在 5 分钟前的数字上（「停用节点」看不到反馈）；
  //   ③ 首读本来就是服务端刚算的（cachedAt ≈ 现在，例如切到一门没看过的课 ⇒ 那一次本来就
  //      得现算）就直接收工，不做无谓重拉。
  useEffect(() => {
    if (!enabled || poolFreshNonce === 0) return
    let cancelled = false
    const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))
    void (async () => {
      const first = await load(false)
      if (!first || Date.now() - first.cachedAt < 1000) return
      for (let i = 0; i < 3; i++) {
        await sleep(1500)
        if (cancelled) return
        const p = await load(false)
        if (p && p.cachedAt !== first.cachedAt) return
      }
    })()
    return () => {
      cancelled = true
    }
  }, [enabled, poolFreshNonce, load])

  if (!pool) {
    return (
      <div>
        {err ? (
          <p className="tc-errtext">
            池数据加载失败：{err}
            <button type="button" className="tc-btn tc-btn--sm" onClick={() => void load(true)}>
              重试
            </button>
          </p>
        ) : (
          <div className="tc-loading">池统计加载中…</div>
        )}
      </div>
    )
  }

  const all = [...pool.nodes, ...(pool.local ? [pool.local] : [])]
  const filtered = showOnlyBad
    ? all.filter((r) => r.status !== 'healthy' && r.status !== 'nodata' && r.status !== 'disabled')
    : all
  const st = pool.selfStatus
  return (
    <div className="tc-panelbody">
      <div className="tc-toolbar tc-toolbar--flush">
        {st ? (
          <span className="tc-badge tc-badge--a">
            workers {st.workers} · inflight {st.inflight} · done {st.gamesDoneTotal}
          </span>
        ) : (
          <span className="tc-badge tc-badge--gray">agent 未启动</span>
        )}
        <SegmentedControl<PoolWindowKey>
          value={days}
          options={WINDOW_OPTIONS.map((o) => ({ value: o.key, label: o.label }))}
          onChange={setDays}
          ariaLabel="统计窗口"
        />
        {pool.sources.length > 0 ? (
          <span
            className="tc-badge tc-badge--a"
            title={pool.sources
              .map((s) => `${s.dir}（${s.lines} 条${s.truncated ? ' · 仅尾部' : ''}）`)
              .join('\n')}
          >
            数据来自 {pool.sources.length} 个训练流 · 最新 {fmtTs(pool.sources[0]!.mtimeMs)}
          </span>
        ) : null}
        <span className="tc-muted tc-small">本机 v{pool.localHash.slice(0, 7) || '-'}</span>
        {st ? (
          <span
            className="tc-muted tc-small"
            title={`结果缓存 ${st.resultCacheItems} 项 · ${fmtBytes(st.resultCacheBytes)} · 最近失败 ${st.recentFailed}`}
          >
            缓存 {st.resultCacheItems}/{fmtBytes(st.resultCacheBytes)}
          </span>
        ) : null}
        <label className="tc-toggle tc-small tc-push-right">
          <input
            type="checkbox"
            checked={showOnlyBad}
            onChange={(e) => setShowOnlyBad((e.target as HTMLInputElement).checked)}
          />
          <span>仅看异常</span>
        </label>
      </div>
      <DataTable<NodeHistoryRow>
        rows={filtered}
        rowKey={(r) => r.id}
        searchKeys={['id', 'lastError']}
        columns={poolColumns}
        storagePrefix={TC_NODE_VIEW('pool', 'table')}
        initialSortKey="ok"
        initialSortDir="desc"
        expandRender={rowExpand}
        emptyText="无节点历史数据"
        ariaLabel="节点统计"
      />
      <p className="tc-caption tc-caption--flush">
        窗口「{pool.window.label}」内统计（本地日，含所有训练流合并）：成功率 = 窗口内最近 ≤10
        次结算完成率（≥90% 达标 / ≥70% 波动 / &lt;70% 异常），随窗口变；与节点 pill 的「产能」
        （最近完成轮贡献 vs 并发）不是一个指标。窗口内局数 = rollout / eval 成功局（无 it 口径）。
        平均耗时 = 窗口内≤50 局节点侧服务时长均值（接单→结果就绪，不含网络）；机侧墙钟 = 训练机
        派发→结算（含网络/轮询），历史 meta 无 wallSec 时显示 -。最近错误近一小时窗口。
        启用/停用等结构实时（看当下配置），探测列（ping/成功率/版本）
        {Math.round((Date.now() - pool.cachedAt) / 1000)}s 前更新。
      </p>
    </div>
  )
}
