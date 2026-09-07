/** NodesPanel.tsx — 节点池卡（§4.8 双视图合一）：
 *  控制（rl-config 启停/并发/冒烟，即时态）· 统计（/api/pool 独立慢节奏 + 行展开）。
 *  双视图状态隔离（DS-U7）：各视图过滤/排序/列显隐独立，localStorage 按 tc.node.<view>.*。
 *  并发输入 = 唯一 L2（非即时应用）：pendingEdits 非空即全局暂停轮询（失焦即弃 + 保存提交）。 */

import { useCallback, useEffect, useState } from 'preact/hooks'
import {
  fmtBytes,
  fmtTs,
  TC_INTERVAL_KEY,
  TC_NODE_VIEW,
  type NodeHistoryRow,
  type NodeView,
  type PanelProps,
  type PoolView,
  type StaleState,
} from '../../../ui/view'
import { Badge, Pill } from '../../../ui/components/Pill'
import { DataTable, type Col } from '../../../ui/components/DataTable'
import { SegmentedControl } from '../../../ui/components/SegmentedControl'
import { Toggle } from '../../../ui/components/Toggle'
import { usePolling } from '../lib/usePolling'
import { fetchPool } from '../lib/api-client'

const VIEW_KEY = 'tc.node.view'
const POOL_INTERVAL_KEY = TC_INTERVAL_KEY('nodes')
type NodeViewMode = 'ctl' | 'pool'

const statusBadge = (s: NodeHistoryRow['status'], okN: number, recentN: number) => {
  switch (s) {
    case 'healthy':
      return (
        <Badge tone="g">
          健康 {okN}/{recentN}
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

const poolColumns: Col<NodeHistoryRow>[] = [
  {
    key: 'id',
    label: '节点',
    cell: (r) => (
      <b>
        {r.id}
        {r.kind === 'local' ? (
          <span className="tc-muted" style={{ fontWeight: 400 }}>
            （本机直跑）
          </span>
        ) : null}
      </b>
    ),
  },
  {
    key: 'status',
    label: '状态',
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
        {r.versionOk === false ? <Pill tone="y">旧</Pill> : null}
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
    key: 'contrib',
    label: '上轮贡献',
    align: 'num',
    cell: (r) =>
      r.contrib > 0 ? (
        r.contrib
      ) : r.lastIter >= 0 && r.globalMaxIt >= 0 ? (
        <span
          className="tc-muted"
          title={`该节点最近一次成功结算在 it${r.lastIter}，已落后当前 it${r.globalMaxIt}`}
        >
          {r.contrib}
        </span>
      ) : (
        '-'
      ),
  },
  {
    key: 'avgElapsedSec',
    label: '平均耗时',
    align: 'num',
    cell: (r) => (r.avgElapsedSec !== null ? `${r.avgElapsedSec}s` : '-'),
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
          最近贡献轮 it{r.lastIter}（全局最新 it{r.globalMaxIt}） · 平均耗时{' '}
          {r.avgElapsedSec !== null ? `${r.avgElapsedSec}s` : '-'}
        </div>
      )}
      {r.lastError ? (
        <div className="tc-errtext" style={{ marginTop: 6 }}>
          错误明细：{r.lastError}
          {r.lastFailTs ? <span className="tc-muted">（{r.lastFailTs}）</span> : null}
        </div>
      ) : null}
    </div>
  )
}

export function NodesPanel({
  stateView,
  active = true,
  paused = false,
  maximizedCard = null,
  pendingEdits = new Map<string, string>(),
  onPending = () => {},
  onDiscardPending = () => {},
  onCommitConcurrency = () => {},
  onAction = () => {},
  onPoolState = () => {},
  poolFreshNonce = 0,
}: PanelProps) {
  const [view, setView] = useState<NodeViewMode>(() => {
    try {
      if (typeof localStorage !== 'undefined') {
        const v = localStorage.getItem(VIEW_KEY)
        if (v === 'ctl' || v === 'pool') return v
      }
    } catch {
      /* ignore */
    }
    return 'ctl'
  })
  const [pool, setPool] = useState<PoolView | null>(null)
  const [poolAt, setPoolAt] = useState(0)
  const [poolErr, setPoolErr] = useState<string | null>(null)
  const [poolState, setPoolState] = useState<StaleState>('refresh')
  const [poolInterval, setPoolIntervalState] = useState<number>(() => {
    try {
      if (typeof localStorage !== 'undefined') {
        const v = Number(localStorage.getItem(POOL_INTERVAL_KEY))
        if ([60, 300, 600, 1800].includes(v)) return v
      }
    } catch {
      /* ignore */
    }
    return 300
  })
  const [showOnlyBad, setShowOnlyBad] = useState(false)

  const setViewPersist = (v: NodeViewMode): void => {
    setView(v)
    try {
      if (typeof localStorage !== 'undefined') localStorage.setItem(VIEW_KEY, v)
    } catch {
      /* ignore */
    }
  }

  const setPoolInterval = (sec: number): void => {
    setPoolIntervalState(sec)
    try {
      if (typeof localStorage !== 'undefined') localStorage.setItem(POOL_INTERVAL_KEY, String(sec))
    } catch {
      /* ignore */
    }
  }

  const loadPool = useCallback(async (fresh: boolean): Promise<void> => {
    try {
      const p = await fetchPool(fresh)
      setPool(p)
      setPoolAt(Date.now())
      setPoolState('ok')
      setPoolErr(null)
    } catch (e) {
      setPoolState('err')
      setPoolErr(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const poolEnabled = active && !paused && !(maximizedCard !== null && maximizedCard !== 'nodes')
  usePolling({
    enabled: poolEnabled,
    intervalSec: poolInterval,
    fetch: () => loadPool(false),
  })

  // 汇总给 App：卡级陈旧度圆点
  useEffect(() => {
    onPoolState(poolState, poolAt)
  }, [poolState, poolAt, onPoolState])

  // 外部强制 fresh（连接恢复探针 / 卡级 ⟳ 动作）
  useEffect(() => {
    if (poolFreshNonce > 0) void loadPool(true)
  }, [poolFreshNonce, loadPool])

  if (!stateView) return <div className="tc-loading">加载中…</div>

  return (
    <div>
      <div className="tc-toolbar">
        <SegmentedControl<NodeViewMode>
          value={view}
          ariaLabel="节点视图"
          options={[
            { value: 'ctl', label: '控制' },
            { value: 'pool', label: '统计' },
          ]}
          onChange={setViewPersist}
        />
        {view === 'pool' ? (
          <>
            <label className="tc-toggle tc-small">
              刷新
              <select
                className="tc-sel"
                value={poolInterval}
                onChange={(e) => setPoolInterval(Number((e.target as HTMLSelectElement).value))}
              >
                <option value={60}>1 分钟</option>
                <option value={300}>5 分钟</option>
                <option value={600}>10 分钟</option>
                <option value={1800}>30 分钟</option>
              </select>
            </label>
            <button
              type="button"
              className="tc-btn tc-btn--sm"
              aria-label="立即刷新池统计"
              onClick={() => void loadPool(true)}
            >
              ⟳ 立即刷新
            </button>
            {poolErr ? (
              <span className="tc-errtext">
                池数据加载失败：{poolErr}
                <button
                  type="button"
                  className="tc-btn tc-btn--sm"
                  onClick={() => void loadPool(true)}
                >
                  重试
                </button>
              </span>
            ) : null}
          </>
        ) : null}
      </div>

      {view === 'ctl' ? (
        <ControlView
          nodes={stateView.nodes}
          pendingEdits={pendingEdits}
          onPending={onPending}
          onDiscard={onDiscardPending}
          onCommit={(id, v) => onCommitConcurrency(id, v)}
          onAction={onAction}
        />
      ) : (
        <StatsView pool={pool} showOnlyBad={showOnlyBad} onShowOnlyBad={setShowOnlyBad} />
      )}
    </div>
  )
}

// ────────────────────────── 控制视图 ──────────────────────────

interface ControlViewProps {
  nodes: NodeView[]
  pendingEdits: ReadonlyMap<string, string>
  onPending: (key: string, value: string) => void
  onDiscard: () => void
  onCommit: (id: string, value: string) => void
  onAction: (act: string, body: Record<string, unknown>) => void
}

function ControlView({
  nodes,
  pendingEdits,
  onPending,
  onDiscard,
  onCommit,
  onAction,
}: ControlViewProps) {
  return (
    <div className="tc-tablewrap">
      <table className="tc-table">
        <thead>
          <tr>
            <th>节点</th>
            <th>状态</th>
            <th>codeHash</th>
            <th>启用</th>
            <th>并行采集数</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((n) => {
            const state =
              n.online === null ? (
                <Pill tone="gray">停用</Pill>
              ) : n.online ? (
                <Pill tone="g">在线</Pill>
              ) : (
                <Pill tone="r">离线</Pill>
              )
            const draft = pendingEdits.get(`conc:${n.id}`)
            const val = draft !== undefined ? draft : String(n.concurrency)
            return (
              <tr key={n.id}>
                <td>
                  <b>{n.id}</b>
                  {n.gpuPush ? <Pill tone="a">GPU push</Pill> : null}
                  <div className="tc-mono tc-muted tc-small">{n.url}</div>
                </td>
                <td>
                  {state}
                  {n.cpus ? <div className="tc-muted tc-small">cpus {n.cpus}</div> : null}
                </td>
                <td>
                  {n.codeHash ? (
                    <span className="tc-mono tc-small">{n.codeHash}…</span>
                  ) : (
                    <span className="tc-muted">—</span>
                  )}
                </td>
                <td>
                  <Toggle
                    label={n.enabled ? '启用' : '停用'}
                    checked={n.enabled}
                    onChange={(v) => onAction('setNodeEnabled', { id: n.id, enabled: v })}
                  />
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <input
                    type="number"
                    min={1}
                    max={64}
                    className="tc-conc"
                    value={val}
                    disabled={n.busy}
                    onInput={(e) => onPending(`conc:${n.id}`, (e.target as HTMLInputElement).value)}
                    onBlur={() => setTimeout(onDiscard, 0)}
                  />
                  <button
                    type="button"
                    className="tc-btn tc-btn--sm"
                    disabled={n.busy}
                    onClick={(e) => {
                      const input = (e.currentTarget.previousElementSibling ??
                        e.target) as HTMLInputElement
                      onCommit(n.id, input.value)
                    }}
                  >
                    保存
                  </button>
                </td>
                <td>
                  <button
                    type="button"
                    className="tc-btn tc-btn--sm"
                    disabled={n.busy || !n.enabled}
                    aria-label={`冒烟 ${n.id}`}
                    onClick={() => onAction('nodeSmoke', { id: n.id })}
                  >
                    冒烟
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ────────────────────────── 统计视图 ──────────────────────────

interface StatsViewProps {
  pool: PoolView | null
  showOnlyBad: boolean
  onShowOnlyBad: (v: boolean) => void
}

function StatsView({ pool, showOnlyBad, onShowOnlyBad }: StatsViewProps) {
  if (!pool) return <div className="tc-loading">池统计加载中…（独立于 /api/state，慢节奏）</div>
  const all = [...pool.nodes, ...(pool.local ? [pool.local] : [])]
  const filtered = showOnlyBad
    ? all.filter((r) => r.status !== 'healthy' && r.status !== 'nodata' && r.status !== 'disabled')
    : all
  const st = pool.selfStatus
  return (
    <div>
      <div className="tc-toolbar">
        {st ? (
          <span className="tc-badge tc-badge--a">
            workers {st.workers} · inflight {st.inflight} · done {st.gamesDoneTotal}
          </span>
        ) : (
          <span className="tc-badge tc-badge--gray">agent 未启动</span>
        )}
        {pool.activeFlow ? (
          <span className="tc-badge tc-badge--a">
            流 {pool.activeFlow.dir}（{pool.activeFlow.lines} 条 · 更新于{' '}
            {fmtTs(pool.activeFlow.mtimeMs)}）
          </span>
        ) : (
          <span className="tc-badge tc-badge--gray">无活跃训练流</span>
        )}
        <span className="tc-muted tc-small">本机 v{pool.localHash.slice(0, 7) || '-'}</span>
        {st ? (
          <span
            className="tc-muted tc-small"
            title={`结果缓存 ${st.resultCacheItems} 项 · ${fmtBytes(st.resultCacheBytes)} · 最近失败 ${st.recentFailed}`}
          >
            结果缓存 {st.resultCacheItems}/{fmtBytes(st.resultCacheBytes)}
          </span>
        ) : null}
        <label className="tc-toggle tc-small">
          <input
            type="checkbox"
            checked={showOnlyBad}
            onChange={(e) => onShowOnlyBad((e.target as HTMLInputElement).checked)}
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
      <p className="tc-caption">
        状态 = 最近 10 次 rollout/eval 结算完成率（<b>健康</b>≥90% · <b>波动</b>≥70% · <b>异常</b>
        &lt;70%）。ping 仅实时参考。上轮贡献 = <b>全局最新轮</b>成功局数（灰 0 =
        落后当前轮，悬停见贡献轮次）。 平均耗时 = 最近 50 局<b>端到端服务时长滑动平均</b>
        。最近错误仅显示最近 1 小时。数据源：最新训练流 dist-agent-meta.jsonl（
        {pool.epochMs > 0 ? <>历史自 {fmtTs(pool.epochMs)} 起重新累计</> : <>累计全部历史</>}）。
        服务端结果缓存 {Math.round((Date.now() - pool.cachedAt) / 1000)}s 前构建。
      </p>
    </div>
  )
}
