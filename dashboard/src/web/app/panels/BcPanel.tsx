/** BcPanel.tsx — 首页 BC epoch 指标 + 多地图 eval 面板（2026-09-13，plan/bc-cloud-integration.plan.md）。
 *
 * 数据源：GET /api/bcEpochs?course=（run_bc 写入训练账本的 bc_epoch / bc_eval 事件，
 * 账本尾部窗口解析）。轮询用 usePolling（后台 tab 停链）。
 * 首页形态与 RL Hero 同构（§367）：**只显示最新 6 个 epoch** 的训练指标；
 * 「完整指标表 ›」弹 modal（tc-modal--wide）展示全部 epoch（排序/搜索/列显隐齐全）。
 *   · 上表：每 epoch 训练指标（train/val loss、move/fire/hold acc、lr、该 epoch 的 eval 胜率）
 *     ——云端/本地同构入账。
 *   · 趋势图：按**全局 epoch** 画 train/val loss、三头 acc、lr 与 eval win_rate（橙色独立序列，
 *     不与 loss 共用 y 轴；`bc-trend.ts` 纯函数供测试）。
 *   · 下表：eval 边界结果，**行 = (epoch × 地图)**，列 = RL 门指标口径
 *     win_rate / kills_mean / phits_mean / pickup_mean / timeout_frac（+ score/ticks）。
 * BC 课程未启用 eval 或无 epoch 数据 → 空态说明，不渲染误导空行。
 */

import { useCallback, useEffect, useState } from 'preact/hooks'
import {
  bcProgressWord,
  buildBcTrend,
  evalWinByEpoch,
  missingEpochs,
  pointerTitle,
  type BcEpochRow,
  type BcEvalRow,
  type BcPointerView,
} from '../../view'
import { DataTable, type Col } from '../../components/DataTable'
import { Pill } from '../../components/Pill'
import { COLOR_EVAL, TrendChart } from '../../components/TrendChart'
import { usePolling } from '../lib/usePolling'
import { postAction } from '../lib/api-client'

export interface BcPanelProps {
  course?: string
  enabled?: boolean
}

interface BcCodeSnapshot {
  jobId: string
  snapshotSha: string
  workspaceSha: string
  stale: boolean | null
  commit: string
  ts: number
}

interface BcEpochsView {
  epochs: BcEpochRow[]
  evals: BcEvalRow[]
  evalEveryEpochs: number | null
  /** 本课进度指针（账本段内最大 epoch + `run_start.epochs`）——表头 `ep57/60` 的来源。 */
  pointer: BcPointerView
  codeSnapshot: BcCodeSnapshot | null
}

const EMPTY: BcEpochsView = {
  epochs: [],
  evals: [],
  evalEveryEpochs: null,
  pointer: { epoch: null, total: null },
  codeSnapshot: null,
}

function fmt(v: number | null, digits = 4): string {
  return v === null || Number.isNaN(v) ? '—' : v.toFixed(digits)
}

function tsTime(ts: number): string {
  const d = new Date(ts * 1000)
  return Number.isFinite(ts) ? d.toLocaleTimeString('sv-SE') : '—'
}

/** epoch 行 + 该 epoch 的 eval 总胜率（跨地图按局数加权；无 eval = null）。 */
interface BcEpochViewRow extends BcEpochRow {
  evalWinRate: number | null
}

/** epoch 指标列（完整指标表 modal 用；首页 6 行简表是同一份列语义的静态展开）。 */
const EPOCH_COLUMNS: Col<BcEpochViewRow>[] = [
  {
    key: 'epoch',
    label: 'epoch',
    align: 'num',
    cell: (r) => <b>{r.epoch}</b>,
    sortValue: (r) => r.epoch,
  },
  {
    key: 'it',
    label: 'it',
    align: 'num',
    cell: (r) => r.it,
    sortValue: (r) => r.it,
  },
  {
    key: 'train_loss',
    label: 'train loss',
    align: 'num',
    cell: (r) => fmt(r.trainLoss),
    sortValue: (r) => r.trainLoss ?? -1,
  },
  {
    key: 'val_loss',
    label: 'val loss',
    align: 'num',
    cell: (r) => fmt(r.valLoss),
    sortValue: (r) => r.valLoss ?? -1,
  },
  {
    key: 'move_acc',
    label: 'move acc',
    align: 'num',
    cell: (r) => fmt(r.moveAcc, 3),
    sortValue: (r) => r.moveAcc ?? -1,
  },
  {
    key: 'fire_acc',
    label: 'fire acc',
    align: 'num',
    cell: (r) => fmt(r.fireAcc, 3),
    sortValue: (r) => r.fireAcc ?? -1,
  },
  {
    key: 'hold_acc',
    label: 'hold acc',
    align: 'num',
    hiddenByDefault: true,
    cell: (r) => fmt(r.holdAcc, 3),
    sortValue: (r) => r.holdAcc ?? -1,
  },
  {
    key: 'eval_win',
    label: 'eval win',
    align: 'num',
    cell: (r) => fmt(r.evalWinRate, 3),
    sortValue: (r) => r.evalWinRate ?? -1,
  },
  {
    key: 'lr',
    label: 'lr',
    align: 'num',
    hiddenByDefault: true,
    cell: (r) => (r.lr === null ? '—' : r.lr.toExponential(2)),
    sortValue: (r) => r.lr ?? -1,
  },
  {
    key: 'ts',
    label: '时间',
    cell: (r) => tsTime(r.ts),
    sortValue: (r) => r.ts,
  },
]
export function BcPanel({ course, enabled = true }: BcPanelProps) {
  const [view, setView] = useState<BcEpochsView>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [fullOpen, setFullOpen] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [clearMsg, setClearMsg] = useState('')
  const [rebuilding, setRebuilding] = useState(false)
  const [snapshotMsg, setSnapshotMsg] = useState('')

  /** #8：清空课程级 BC 续训索引（需要该课先暂停；动作层再校验一次）。 */
  const onClearProgress = async (): Promise<void> => {
    if (!course || clearing) return
    const ok = window.confirm(
      `清空 ${course} 的 BC 续训进度？\n\n` +
        '只删 hub 的课程级 (course,it) 续训索引；epoch 权重归档、账本、per-jid 权重不动。\n' +
        '要求本课已暂停；下次开课从 ep1 起。',
    )
    if (!ok) return
    setClearing(true)
    const r = await postAction('clearBcResume', { course })
    setClearing(false)
    setClearMsg(r.message)
  }

  /** B7：重打集群代码快照；只对下一次发布生效，不重启进程。 */
  const onRebuildSnapshot = async (): Promise<void> => {
    if (rebuilding) return
    setRebuilding(true)
    const r = await postAction('rebuildCodeSnapshot', {})
    setRebuilding(false)
    setSnapshotMsg(r.message)
  }

  const refresh = useCallback(async (): Promise<void> => {
    const res = await fetch(`/api/bcEpochs?course=${encodeURIComponent(course ?? '')}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    setView((await res.json()) as BcEpochsView)
    setError(null)
  }, [course])

  usePolling({
    enabled: enabled && !!course,
    intervalSec: 10,
    fetch: refresh,
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  })

  // 切课程**立即**重拉：`usePolling` 的链只按 `enabled` 重开（课程不是它的判据），
  // 否则两门 BC 课之间切换会顶旧课数据最长 10s（与 App 的 stateView 同一条竞态纪律）。
  useEffect(() => {
    if (enabled && course) void refresh().catch(() => undefined)
  }, [enabled, course, refresh])

  // 完整指标表 modal 的 Esc 关闭（自持，不依赖 App 全局键处理）。
  useEffect(() => {
    if (!fullOpen) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setFullOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullOpen])

  const epochs = view.epochs
  const evals = view.evals
  if (!course) return null

  // 该 epoch 的 eval 胜率（表列与趋势图共用一份聚合口径）。
  const evalWin = evalWinByEpoch(evals)
  const viewRows: BcEpochViewRow[] = epochs.map((e) => ({
    ...e,
    evalWinRate: evalWin.get(e.epoch) ?? null,
  }))
  // 最新 6 个 epoch（按**全局 epoch** 倒序；重启后 it 会重置，不能当新旧的判据）。
  const latest = [...viewRows].sort((a, b) => b.epoch - a.epoch).slice(0, 6)
  const trends = buildBcTrend(epochs, evals)

  // 扁平 eval 行：一行 = (epoch × 地图)
  const evalFlat = evals.flatMap((e) =>
    (e.levels ?? []).map((l) => ({ ...l, epoch: e.epoch, it: e.it, wver: e.wver, ts: e.ts })),
  )

  return (
    <div className="tc-card tc-card--pad">
      <div className="tc-line tc-mb-2">
        <b>BC Epoch</b>
        {/* 进度与侧栏/pill 同一口径（2026-11-12）：`ep57/60`，而不是「共 N epoch」的行数。 */}
        <span className="tc-muted tc-small" title={pointerTitle({ it: null, bc: view.pointer })}>
          {bcProgressWord(view.pointer)}
        </span>
        {view.evalEveryEpochs !== null && view.evalEveryEpochs > 0 ? (
          <Pill tone="gray" title="课程 eval.every_epochs">
            每 {view.evalEveryEpochs} epoch eval
          </Pill>
        ) : (
          <Pill tone="gray">eval 关闭</Pill>
        )}
        {view.codeSnapshot ? (
          <Pill
            tone={view.codeSnapshot.stale ? 'r' : 'gray'}
            title={
              `job ${view.codeSnapshot.jobId.slice(0, 12)} 的代码快照` +
              (view.codeSnapshot.stale === null
                ? '（旧 job 无新鲜度旁挂，无法判定）'
                : view.codeSnapshot.stale
                  ? ` 与工作区不一致（workspace ${view.codeSnapshot.workspaceSha.slice(0, 12)}）——点「重打快照」`
                  : '；与发布时工作区一致')
            }
          >
            快照 {view.codeSnapshot.snapshotSha.slice(0, 8) || '—'}
          </Pill>
        ) : null}
        {error ? <Pill tone="r">{error}</Pill> : null}
        {(() => {
          const gaps = missingEpochs(epochs)
          if (gaps.length === 0) return null
          const head = gaps.slice(0, 6).join(', ')
          return (
            <Pill tone="y" title={`账本缺号：${gaps.join(', ')}（入账重复/丢行，曲线在该处断开）`}>
              数据不全 · 缺 ep{head}
              {gaps.length > 6 ? ` 等 ${gaps.length} 个` : ''}
            </Pill>
          )
        })()}
        <span className="tc-grow" />
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          disabled={rebuilding}
          title="按当前工作区重打集群代码快照（只影响下一次发布；不重启进程）"
          onClick={() => void onRebuildSnapshot()}
        >
          {rebuilding ? '重打中…' : '重打快照'}
        </button>
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          disabled={clearing}
          title="清空 hub 的课程级 (course,it) 续训索引（要求先暂停；下次开课从 ep1 起）"
          onClick={() => void onClearProgress()}
        >
          {clearing ? '清空中…' : '清空 BC 进度'}
        </button>
        {epochs.length > 0 ? (
          <button
            type="button"
            className="tc-link"
            aria-label="打开 BC Epoch 完整指标表"
            onClick={() => setFullOpen(true)}
          >
            完整指标表 ›
          </button>
        ) : null}
      </div>

      {clearMsg ? <p className="tc-muted tc-small">{clearMsg}</p> : null}
      {snapshotMsg ? <p className="tc-muted tc-small">{snapshotMsg}</p> : null}

      {epochs.length === 0 ? (
        <p className="tc-muted tc-small">
          尚无 BC epoch 指标——BC 课程（*.bc.jsonc）启动训练后，每 epoch 的 train/val loss 与
          move/fire acc 会自动出现在这里（云端与本地训练同构入账）。
        </p>
      ) : (
        /* 首页只出最新 6 个 epoch（与 RL Hero「最新 6 轮」同构，§367）；全部数据进 modal。 */
        <table className="tc-table tc-table--dense">
          <thead>
            <tr>
              <th>epoch</th>
              <th className="tc-num">it</th>
              <th>时间</th>
              <th className="tc-num">train loss</th>
              <th className="tc-num">val loss</th>
              <th className="tc-num">move acc</th>
              <th className="tc-num">fire acc</th>
              <th className="tc-num">hold acc</th>
              <th className="tc-num">eval win</th>
              <th className="tc-num">lr</th>
            </tr>
          </thead>
          <tbody>
            {latest.map((r) => (
              <tr key={`${r.it}-${r.epoch}`}>
                <td>
                  <b>{r.epoch}</b>
                </td>
                <td className="tc-num">{r.it}</td>
                <td className="tc-muted tc-nowrap">{tsTime(r.ts)}</td>
                <td className="tc-num">{fmt(r.trainLoss)}</td>
                <td className="tc-num">{fmt(r.valLoss)}</td>
                <td className="tc-num">{fmt(r.moveAcc, 3)}</td>
                <td className="tc-num">{fmt(r.fireAcc, 3)}</td>
                <td className="tc-num">{fmt(r.holdAcc, 3)}</td>
                <td className="tc-num">{fmt(r.evalWinRate, 3)}</td>
                <td className="tc-num">{r.lr === null ? '—' : r.lr.toExponential(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {/* 完整指标表 modal：全部 epoch（排序 / 搜索 / 列显隐），Esc / 遮罩 / ✕ 关闭。 */}
      {fullOpen && epochs.length > 0 ? (
        <div className="tc-modal-mask" onClick={() => setFullOpen(false)}>
          <div
            className="tc-modal tc-modal--wide"
            role="dialog"
            aria-label="BC Epoch 完整指标表"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="tc-line">
              <h3 className="tc-m-0">BC Epoch 完整指标表</h3>
              <span
                className="tc-muted tc-small"
                title={pointerTitle({ it: null, bc: view.pointer })}
              >
                {bcProgressWord(view.pointer)}
              </span>
              <span className="tc-grow" />
              <button
                type="button"
                className="tc-btn tc-btn--sm"
                aria-label="关闭完整指标表"
                onClick={() => setFullOpen(false)}
              >
                ✕ 关闭
              </button>
            </div>
            <DataTable<BcEpochViewRow>
              columns={EPOCH_COLUMNS}
              rows={viewRows}
              rowKey={(r) => `${r.it}-${r.epoch}`}
              initialSortKey="epoch"
              initialSortDir="desc"
              searchKeys={['epoch', 'it']}
              emptyText="无 epoch 数据"
            />
          </div>
        </div>
      ) : null}
      {/* 趋势：x = 全局 epoch（续训/接替后 it 会变，epoch 不会）；eval win_rate 用橙线独立成图，
          不与 loss 共用 y 轴（0–1 与 ~0.01 同轴 = 假轴）。 */}
      {trends.length > 0 ? (
        <div className="tc-mt-3">
          <div className="tc-line tc-mb-2">
            <b>BC 趋势</b>
            <span className="tc-muted tc-small">x = 全局 epoch；橙色 = eval win_rate</span>
          </div>
          <div className="tc-trends">
            {trends.map((t) => (
              <div className="tc-tcell" key={t.key}>
                <span className="tc-tcell__hd">
                  <span className="tc-tcell__lbl" title={t.label}>
                    {t.label}
                  </span>
                </span>
                <TrendChart
                  series={t.series}
                  range="all"
                  fmt={(v) => fmt(v, 3)}
                  tone={t.tone}
                  color={t.evalSeries ? COLOR_EVAL : undefined}
                  height={56}
                />
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <div className="tc-mt-3">
        <div className="tc-line tc-mb-2">
          <b>BC Eval（多地图 · RL 口径）</b>
          <span className="tc-muted tc-small">
            win_rate / kills_mean / phits_mean / pickup_mean / timeout_frac
          </span>
          {evals.some((e) => e.sentinelOnly) ? (
            <Pill tone="y" title="周期 eval 只作哨兵；判决只认 200 局行为门（课程 §3 五项门）">
              哨兵 · 非判决
            </Pill>
          ) : null}
        </div>
        {evalFlat.length === 0 ? (
          <p className="tc-muted tc-small">
            尚无 eval 结果——在课程 `eval` 块配置 `every_epochs / games_per_stage /
            levels`（多张关卡图各自出结果），训练到边界 epoch 自动在 LAN 节点跑干净评估。
          </p>
        ) : (
          <DataTable<(typeof evalFlat)[number]>
            columns={[
              {
                key: 'epoch',
                label: 'epoch',
                align: 'num',
                cell: (r) => <b>{r.epoch}</b>,
                sortValue: (r) => r.epoch,
              },
              {
                key: 'level',
                label: '地图',
                cell: (r) => r.level,
                sortValue: (r) => r.level,
              },
              {
                key: 'n',
                label: '局数',
                align: 'num',
                cell: (r) => `${r.n}${r.failed > 0 ? ` (败 ${r.failed})` : ''}`,
                sortValue: (r) => r.n,
              },
              {
                key: 'win_rate',
                label: 'win_rate',
                align: 'num',
                cell: (r) => fmt(r.winRate, 3),
                sortValue: (r) => r.winRate,
              },
              {
                key: 'kills_mean',
                label: 'kills_mean',
                align: 'num',
                cell: (r) => fmt(r.killsMean, 2),
                sortValue: (r) => r.killsMean,
              },
              {
                key: 'phits_mean',
                label: 'phits_mean',
                align: 'num',
                cell: (r) => fmt(r.phitsMean, 2),
                sortValue: (r) => r.phitsMean,
              },
              {
                key: 'pickup_mean',
                label: 'pickup_mean',
                align: 'num',
                cell: (r) => fmt(r.pickupMean, 2),
                sortValue: (r) => r.pickupMean,
              },
              {
                key: 'timeout_frac',
                label: 'timeout_frac',
                align: 'num',
                cell: (r) => fmt(r.timeoutFrac, 3),
                sortValue: (r) => r.timeoutFrac,
              },
              {
                key: 'pickup_rate',
                label: 'pickup_rate',
                align: 'num',
                cell: (r) => fmt(r.pickupRate ?? null, 3),
                sortValue: (r) => r.pickupRate ?? -1,
              },
              {
                key: 'cells_mean',
                label: 'cells_mean',
                align: 'num',
                cell: (r) => fmt(r.cellsMean ?? null, 1),
                sortValue: (r) => r.cellsMean ?? -1,
              },
              {
                key: 'move_frac',
                label: 'move_frac',
                align: 'num',
                cell: (r) => fmt(r.moveFrac ?? null, 3),
                sortValue: (r) => r.moveFrac ?? -1,
              },
              {
                key: 'score_mean',
                label: 'score_mean',
                align: 'num',
                hiddenByDefault: true,
                cell: (r) => fmt(r.scoreMean, 2),
                sortValue: (r) => r.scoreMean,
              },
              {
                key: 'mean_ticks',
                label: 'mean_ticks',
                align: 'num',
                hiddenByDefault: true,
                cell: (r) => fmt(r.meanTicks, 0),
                sortValue: (r) => r.meanTicks,
              },
              {
                key: 'ts',
                label: '时间',
                cell: (r) => tsTime(r.ts),
                sortValue: (r) => r.ts,
              },
            ]}
            rows={evalFlat}
            rowKey={(r) => `${r.it}-${r.epoch}-${r.level}`}
            initialSortKey="epoch"
            initialSortDir="desc"
            searchKeys={['level', 'epoch']}
            emptyText="无 eval 数据"
          />
        )}
      </div>
    </div>
  )
}
