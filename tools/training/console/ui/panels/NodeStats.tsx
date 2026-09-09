/** NodeStats.tsx — 抽屉「节点统计」tab：/api/pool 独立慢节奏 + 10 列 + 行展开 + 仅看异常。
 *  迁移自旧 NodesPanel 的 StatsView（控制视图由 NodePills 承接）。 */

import { useCallback, useEffect, useState } from 'preact/hooks'
import { fmtBytes, fmtTs, TC_NODE_VIEW, type NodeHistoryRow, type PoolView } from '../../../ui/view'
import { Badge, Pill } from '../../../ui/components/Pill'
import { DataTable, type Col } from '../../../ui/components/DataTable'
import { usePolling } from '../lib/usePolling'
import { fetchPool } from '../lib/api-client'

export interface NodeStatsProps {
  /** 抽屉开着才轮询（DS-U5 语义；展开补拉一次）。 */
  enabled: boolean
  poolFreshNonce: number
}

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
    // F5（plan/dist-codehash-stale-fix.md）：贡献按 mode 分桶——"只跑 eval 的节点"
    // 不再看起来在贡献 rollout。合计 contrib 保留，展示 rollout/eval 两数。
    key: 'contrib',
    label: '上轮贡献 rl/ev',
    align: 'num',
    cell: (r) =>
      r.contrib > 0 ? (
        <span title={`rollout ${r.contribRollout} · eval ${r.contribEval}`}>
          {r.contribRollout}/{r.contribEval}
        </span>
      ) : r.lastIter >= 0 && r.globalMaxIt >= 0 ? (
        <span
          className="tc-muted"
          title={`该节点最近一次成功结算在 it${r.lastIter}，已落后当前 it${r.globalMaxIt}`}
        >
          0/0
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

export function NodeStats({ enabled, poolFreshNonce }: NodeStatsProps) {
  const [pool, setPool] = useState<PoolView | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [showOnlyBad, setShowOnlyBad] = useState(false)

  const load = useCallback(async (fresh: boolean): Promise<void> => {
    try {
      const p = await fetchPool(fresh)
      setPool(p)
      setErr(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [])

  usePolling({ enabled, intervalSec: 300, fetch: () => load(false) })

  useEffect(() => {
    if (enabled && poolFreshNonce > 0) void load(true)
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
    <div>
      <div className="tc-toolbar" style={{ padding: '0 0 8px' }}>
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
        <label className="tc-toggle tc-small" style={{ marginLeft: 'auto' }}>
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
      <p className="tc-caption" style={{ border: 'none', padding: '8px 0 0' }}>
        状态 = 最近 10 次结算完成率（≥90% 健康 / ≥70% 波动 / &lt;70% 异常）；ping
        仅实时参考。平均耗时 = 最近 50 局端到端服务时长滑动平均。 最近错误半小时窗口。服务端缓存{' '}
        {Math.round((Date.now() - pool.cachedAt) / 1000)}s 前构建。
      </p>
    </div>
  )
}
