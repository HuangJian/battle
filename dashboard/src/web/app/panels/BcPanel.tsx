/** BcPanel.tsx — 首页 BC epoch 指标 + 多地图 eval 面板（2026-09-13，plan/bc-cloud-integration.plan.md）。
 *
 * 数据源：GET /api/bcEpochs?course=（run_bc 写入训练账本的 bc_epoch / bc_eval 事件，
 * 账本尾部窗口解析）。轮询用 usePolling（后台 tab 停链）。
 * 首页形态与 RL Hero 同构（§367）：**只显示最新 6 个 epoch** 的训练指标；
 * 「完整指标表 ›」弹 modal（tc-modal--wide）展示全部 epoch（排序/搜索/列显隐齐全）。
 *   · 上表：每 epoch 训练指标（train/val loss、move/fire acc、lr）——云端/本地同构入账。
 *   · 下表：eval 边界结果，**行 = (epoch × 地图)**，列 = RL 门指标口径
 *     win_rate / kills_mean / phits_mean / pickup_mean / timeout_frac（+ score/ticks）。
 * BC 课程未启用 eval 或无 epoch 数据 → 空态说明，不渲染误导空行。
 */

import { useCallback, useEffect, useState } from 'preact/hooks'
import type { BcEpochRow, BcEvalRow } from '../../view'
import { DataTable, type Col } from '../../components/DataTable'
import { Pill } from '../../components/Pill'
import { usePolling } from '../lib/usePolling'

export interface BcPanelProps {
  course?: string
  enabled?: boolean
}

interface BcEpochsView {
  epochs: BcEpochRow[]
  evals: BcEvalRow[]
  evalEveryEpochs: number | null
}

const EMPTY: BcEpochsView = { epochs: [], evals: [], evalEveryEpochs: null }

function fmt(v: number | null, digits = 4): string {
  return v === null || Number.isNaN(v) ? '—' : v.toFixed(digits)
}

function tsTime(ts: number): string {
  const d = new Date(ts * 1000)
  return Number.isFinite(ts) ? d.toLocaleTimeString('sv-SE') : '—'
}

/** epoch 指标列（完整指标表 modal 用；首页 6 行简表是同一份列语义的静态展开）。 */
const EPOCH_COLUMNS: Col<BcEpochRow>[] = [
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

  // 最新 6 个 epoch（it/epoch 倒序：重启后 it 递增，同一 run 内 epoch 递增）。
  const latest = [...epochs].sort((a, b) => b.it - a.it || b.epoch - a.epoch).slice(0, 6)

  // 扁平 eval 行：一行 = (epoch × 地图)
  const evalFlat = evals.flatMap((e) =>
    (e.levels ?? []).map((l) => ({ ...l, epoch: e.epoch, it: e.it, wver: e.wver, ts: e.ts })),
  )

  return (
    <div className="tc-card tc-card--pad">
      <div className="tc-line tc-mb-2">
        <b>BC Epoch</b>
        {view.evalEveryEpochs !== null && view.evalEveryEpochs > 0 ? (
          <Pill tone="gray" title="课程 eval.every_epochs">
            每 {view.evalEveryEpochs} epoch eval
          </Pill>
        ) : (
          <Pill tone="gray">eval 关闭</Pill>
        )}
        {error ? <Pill tone="r">{error}</Pill> : null}
        <span className="tc-grow" />
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
              <span className="tc-muted tc-small">共 {epochs.length} epoch</span>
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
            <DataTable<BcEpochRow>
              columns={EPOCH_COLUMNS}
              rows={epochs}
              rowKey={(r) => `${r.it}-${r.epoch}`}
              initialSortKey="epoch"
              initialSortDir="desc"
              searchKeys={['epoch', 'it']}
              emptyText="无 epoch 数据"
            />
          </div>
        </div>
      ) : null}
      <div className="tc-mt-3">
        <div className="tc-line tc-mb-2">
          <b>BC Eval（多地图 · RL 口径）</b>
          <span className="tc-muted tc-small">
            win_rate / kills_mean / phits_mean / pickup_mean / timeout_frac
          </span>
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
