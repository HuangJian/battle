/** ledger.ts — 训练账本尾派生：课程编辑态、BC 轮次与多地图 eval 行。 */
import { readFileSync } from 'fs'
import path from 'path'
import { REPO_ROOT, curriculaDir } from '../../core/paths'
import type { CourseEdit } from '../../web/view'
import { readLogTail } from './logs'

export function courseEditFromLedgerTail(lines: string[]): CourseEdit | null {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]!.trim()
    if (!line) continue
    let r: {
      event?: unknown
      verdict?: unknown
      fields?: unknown
      detail?: unknown
      time?: unknown
      it?: unknown
    }
    try {
      r = JSON.parse(line) as typeof r
    } catch {
      continue
    }
    if (!r || typeof r !== 'object' || r.event !== 'course_edit') continue
    const verdict =
      r.verdict === 'applied' || r.verdict === 'rejected' || r.verdict === 'restored'
        ? r.verdict
        : null
    if (!verdict) return null
    return {
      verdict,
      fields: Array.isArray(r.fields) ? (r.fields as unknown[]).map(String) : [],
      detail: typeof r.detail === 'string' ? r.detail : '',
      at: typeof r.time === 'string' ? r.time : '',
      it: typeof r.it === 'number' ? r.it : 0,
    }
  }
  return null
}

/** BC epoch / eval 账本行（2026-09-13；纯函数，可单测）。
 *  bc_epoch = run_bc 从 hub bc-metrics / 本地 bc.py 流入账的每 epoch 指标；
 *  bc_eval = eval.every_epochs 边界的多地图干净评估聚合（RL 门指标口径）。 */
export interface BcEpochRow {
  it: number
  epoch: number
  trainLoss: number | null
  valLoss: number | null
  moveAcc: number | null
  fireAcc: number | null
  lr: number | null
  ts: number
}

export interface BcEvalLevelRow {
  level: string
  n: number
  wins: number
  winRate: number
  killsMean: number
  phitsMean: number
  pickupMean: number
  timeoutFrac: number
  scoreMean: number
  meanTicks: number
  failed: number
}

export interface BcEvalRow {
  it: number
  epoch: number
  wver: string
  levels: BcEvalLevelRow[]
  ts: number
}

export function bcRowsFromLedgerTail(lines: string[]): {
  epochs: BcEpochRow[]
  evals: BcEvalRow[]
} {
  const epochs: BcEpochRow[] = []
  const evals: BcEvalRow[] = []
  const num = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null
  for (const line of lines) {
    const t = line.trim()
    if (!t) continue
    let r: Record<string, unknown>
    try {
      r = JSON.parse(t) as Record<string, unknown>
    } catch {
      continue
    }
    if (!r || typeof r !== 'object') continue
    if (r.event === 'bc_epoch') {
      epochs.push({
        it: num(r.it) ?? 0,
        epoch: num(r.epoch) ?? 0,
        trainLoss: num(r.train_loss),
        valLoss: num(r.val_loss),
        moveAcc: num(r.move_acc),
        fireAcc: num(r.fire_acc),
        lr: num(r.lr),
        ts: num(r.ts) ?? 0,
      })
    } else if (r.event === 'bc_eval' && Array.isArray(r.levels)) {
      const levels = (r.levels as Array<Record<string, unknown>>).map((l) => ({
        level: typeof l.level === 'string' ? l.level : '?',
        n: num(l.n) ?? 0,
        wins: num(l.wins) ?? 0,
        winRate: num(l.win_rate) ?? 0,
        killsMean: num(l.kills_mean) ?? 0,
        phitsMean: num(l.phits_mean) ?? 0,
        pickupMean: num(l.pickup_mean) ?? 0,
        timeoutFrac: num(l.timeout_frac) ?? 0,
        scoreMean: num(l.score_mean) ?? 0,
        meanTicks: num(l.mean_ticks) ?? 0,
        failed: num(l.failed) ?? 0,
      }))
      evals.push({
        it: num(r.it) ?? 0,
        epoch: num(r.epoch) ?? 0,
        wver: typeof r.wver === 'string' ? r.wver : '',
        levels,
        ts: num(r.ts) ?? 0,
      })
    }
  }
  return { epochs, evals }
}

/** BC epoch 面板视图：账本尾窗解析 + 课程 eval.every_epochs（面板头提示用）。 */
export function buildBcEpochsView(course: string | undefined): {
  epochs: BcEpochRow[]
  evals: BcEvalRow[]
  evalEveryEpochs: number | null
} {
  if (!course) return { epochs: [], evals: [], evalEveryEpochs: null }
  let epochs: BcEpochRow[] = []
  let evals: BcEvalRow[] = []
  try {
    const tail = readLogTail(path.join(REPO_ROOT, 'tmp', course, 'training_log.jsonl'), 4000).lines
    const parsed = bcRowsFromLedgerTail(tail)
    epochs = parsed.epochs.slice(-100)
    evals = parsed.evals.slice(-20)
  } catch {
    /* 无账本 → 空态 */
  }
  let evalEveryEpochs: number | null = null
  try {
    const raw = readFileSync(path.join(curriculaDir(), `${course}.bc.jsonc`), 'utf-8')
    const m = /"every_epochs"\s*:\s*(\d+)/.exec(raw)
    if (m) evalEveryEpochs = Number(m[1])
  } catch {
    /* 非 BC 课程 / 读不到 → null */
  }
  return { epochs, evals, evalEveryEpochs }
}
