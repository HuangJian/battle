/** ledger.ts — 训练账本尾派生：课程编辑态、BC 轮次与多地图 eval 行。 */
import { readFileSync, readdirSync, statSync } from 'fs'
import path from 'path'
import { curriculaDir, tmpLogsDir } from '../../core/paths'
import type { CourseEdit } from '../../web/view'
import { readCourseTail } from './course-source'

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
  /** v6 第三头（#11）；旧账本行没有该键 ⇒ null。 */
  holdAcc: number | null
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
  /** 行为门口径（B5）：拾取率 / 每局生成 / 探索格 / 移动占比。 */
  pickupRate?: number | null
  pickupSpawnMean?: number | null
  cellsMean?: number | null
  moveFrac?: number | null
  /** 面板口径（B4）：禁止跨面板相减。 */
  panel?: {
    level?: string
    maxTicks?: number | null
    courseMaxTicks?: number | null
    difficulty?: string
    lives?: number | null
    gamesPerStage?: number | null
    seed0?: number | null
    stages?: number | null
  }
}

export interface BcEvalRow {
  it: number
  epoch: number
  wver: string
  levels: BcEvalLevelRow[]
  /** B5：周期 eval 只作哨兵，判决只认 200 局行为门。 */
  sentinelOnly?: boolean
  ts: number
}

export function bcRowsFromLedgerTail(lines: string[]): {
  epochs: BcEpochRow[]
  evals: BcEvalRow[]
} {
  const epochs: BcEpochRow[] = []
  const evals: BcEvalRow[] = []
  // 只认**最近一次 run_start 之后**的行（2026-09-14 混轮修复）。run_bc 每轮启动写
  // run_start（含 runId/epochs/seed），而 bc_epoch / bc_eval 全部留在同一份账本里：
  // traj 目录不变、只换语料口径时，两轮的数据会挂同一个 it=1 —— 不过滤就会把上一轮
  // 的 59 行与本轮的 150 行拼成一条曲线（实测 10:38 那轮）。无 run_start（旧账本、
  // 纯 local 训练）⇒ 不过滤，保持原行为。
  let from = 0
  for (let i = lines.length - 1; i >= 0; i--) {
    const t = lines[i]!.trim()
    if (!t) continue
    try {
      if ((JSON.parse(t) as { event?: string }).event === 'run_start') {
        from = i
        break
      }
    } catch {
      /* 坏行跳过 */
    }
  }
  const num = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null
  /** eval 行的 panel（B4）：旧账本没有它 ⇒ undefined；坏值逐键退 null，不整体丢行。 */
  const parsePanel = (v: unknown): BcEvalLevelRow['panel'] => {
    if (!v || typeof v !== 'object') return undefined
    const p = v as Record<string, unknown>
    return {
      level: typeof p.level === 'string' ? p.level : undefined,
      maxTicks: num(p.max_ticks),
      courseMaxTicks: num(p.course_max_ticks),
      difficulty: typeof p.difficulty === 'string' ? p.difficulty : undefined,
      lives: num(p.lives),
      gamesPerStage: num(p.games_per_stage),
      seed0: num(p.seed0),
      stages: num(p.stages),
    }
  }
  for (const line of lines.slice(from)) {
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
        holdAcc: num(r.hold_acc),
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
        pickupRate: num(l.pickup_rate),
        pickupSpawnMean: num(l.pickup_spawn_mean),
        cellsMean: num(l.cells_mean),
        moveFrac: num(l.move_frac),
        panel: parsePanel(l.panel),
      }))
      evals.push({
        it: num(r.it) ?? 0,
        epoch: num(r.epoch) ?? 0,
        wver: typeof r.wver === 'string' ? r.wver : '',
        levels,
        sentinelOnly: r.sentinel_only === true,
        ts: num(r.ts) ?? 0,
      })
    }
  }
  return { epochs, evals }
}

/** 最近一轮 BC job 的代码快照旁挂（B7；旧 job 没有旁挂 ⇒ stale=null）。 */
export interface BcCodeSnapshotRow {
  jobId: string
  snapshotSha: string
  workspaceSha: string
  stale: boolean | null
  commit: string
  ts: number
}

function latestBcCodeSnapshot(course: string): BcCodeSnapshotRow | null {
  const root = path.join(tmpLogsDir(), course, 'remote-jobs')
  let best: {
    mt: number
    manifest: Record<string, unknown>
    sidecar: Record<string, unknown>
  } | null = null
  try {
    for (const name of readdirSync(root)) {
      if (name.startsWith('.')) continue
      const dir = path.join(root, name)
      const manifestPath = path.join(dir, 'manifest.json')
      let manifest: Record<string, unknown>
      try {
        manifest = JSON.parse(readFileSync(manifestPath, 'utf-8')) as Record<string, unknown>
      } catch {
        continue
      }
      if (manifest.kind !== 'bc') continue
      let mt = 0
      try {
        mt = statSync(manifestPath).mtimeMs
      } catch {
        /* 读不到 mtime 仍可作候选 */
      }
      if (best && mt <= best.mt) continue
      let sidecar: Record<string, unknown> = {}
      try {
        sidecar = JSON.parse(
          readFileSync(path.join(dir, 'code-freshness.json'), 'utf-8'),
        ) as Record<string, unknown>
      } catch {
        /* 旧 job / 旁挂还没写：只显示 manifest 的 sha，不猜新旧 */
      }
      best = { mt, manifest, sidecar }
    }
  } catch {
    return null
  }
  if (!best) return null
  const m = best.manifest
  const s = best.sidecar
  return {
    jobId: String(m.job_id ?? ''),
    snapshotSha: String(m.code_sha256 ?? ''),
    workspaceSha: typeof s.workspace_sha === 'string' ? s.workspace_sha : '',
    stale: typeof s.stale === 'boolean' ? s.stale : null,
    commit: String(m.commit ?? ''),
    ts: typeof s.ts === 'number' && Number.isFinite(s.ts) ? s.ts : best.mt / 1000,
  }
}

/** BC epoch 面板视图：账本尾窗解析 + 课程 eval.every_epochs（面板头提示用）。 */
export async function buildBcEpochsView(course: string | undefined): Promise<{
  epochs: BcEpochRow[]
  evals: BcEvalRow[]
  evalEveryEpochs: number | null
  codeSnapshot: BcCodeSnapshotRow | null
}> {
  if (!course) return { epochs: [], evals: [], evalEveryEpochs: null, codeSnapshot: null }
  let epochs: BcEpochRow[] = []
  let evals: BcEvalRow[] = []
  try {
    // 读历史可回退备份（plan/course-metrics-persistence §3.3 R5）。
    const tail = await readCourseTail(course, 'training_log.jsonl', 4000)
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
  return { epochs, evals, evalEveryEpochs, codeSnapshot: latestBcCodeSnapshot(course) }
}
