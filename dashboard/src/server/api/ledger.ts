/** ledger.ts — 训练账本尾派生：课程编辑态、BC 轮次与多地图 eval 行。 */
import { readFileSync, readdirSync, statSync } from 'fs'
import path from 'path'
import { curriculaDir, tmpLogsDir } from '../../core/paths'
import type { BcPointerView, CourseEdit } from '../../web/view'
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

/** 账本尾行的**段起点**：优先 `job_pending`（job 身份），没有才退回 `run_start`，两者都无 ⇒ 0。
 *
 *  面板（`bcRowsFromLedgerTail`）与侧栏/pill 的 epoch 指针（`bcPointerFromLedgerTail`）必须用
 *  **同一个锚**：否则会出现「面板画到 ep57、侧栏写 ep58」这种两处各说一个数。
 */
function bcSegmentStart(lines: string[]): number {
  // 段锚**优先 job 身份**（hub 发布时追加的 `job_pending`，2026-11-12），退回 `run_start`
  // （2026-09-14）。为什么先 job：重启 trainer 会多写一条 `run_start`，但 **job 没换**
  // （hub 的 `bc-resume` 继续、重认领不重发）——按 run_start 分段会把重启前已算的 epoch
  // 全遮掉，用户读成「之前算的都丢了」（g1c-4s2e 报障）。换 job 时新的 `job_pending` 把段
  // 切开，这正是原本要防的「两轮拼一条曲线」（traj 不变、两轮都挂 it=1），而且比 run_start
  // 更准：同一个 job 的重启不切。
  // 旧账本没有 `job_pending`（纯 local 训练 / 旧链路）⇒ 退回 run_start；两者都没有 ⇒ 不过滤。
  let from = 0
  let jobAt = -1
  let startAt = -1
  for (let i = lines.length - 1; i >= 0; i--) {
    const t = lines[i]!.trim()
    if (!t) continue
    try {
      const ev = (JSON.parse(t) as { event?: string }).event
      if (ev === 'job_pending') {
        jobAt = i
        break
      }
      if (ev === 'run_start' && startAt < 0) startAt = i
    } catch {
      /* 坏行跳过 */
    }
  }
  if (jobAt >= 0) from = jobAt
  else if (startAt >= 0) from = startAt
  return from
}

/** BC 课的进度指针（侧栏「最近训练」与顶部 pill 的读数；2026-11-12 用户口径）。
 *
 *  `epoch` = 段内 `bc_epoch` 的最大 epoch（= 面板曲线的右端，两处同一个数）；
 *  `total` = 最近一条 `run_start` 声明的 `epochs`（课程总 epoch；旧账本没这个键 ⇒ null）。
 *  读不到任何一行 ⇒ `{ epoch: null, total: null }`（不可知，**不编 0**）。
 */
export function bcPointerFromLedgerTail(lines: string[]): BcPointerView {
  let epoch: number | null = null
  let total: number | null = null
  const from = bcSegmentStart(lines)
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i]!.trim()
    if (!t) continue
    let r: { event?: unknown; epoch?: unknown; epochs?: unknown }
    try {
      r = JSON.parse(t) as typeof r
    } catch {
      continue
    }
    if (!r || typeof r !== 'object') continue
    // 总 epoch 属于**这一趟 run**（run_start 写在 job_pending 之前，不在段内）。
    if (r.event === 'run_start' && typeof r.epochs === 'number' && r.epochs > 0) total = r.epochs
    if (i < from || r.event !== 'bc_epoch') continue
    const ep = typeof r.epoch === 'number' && Number.isFinite(r.epoch) ? r.epoch : 0
    if (ep > 0 && (epoch === null || ep > epoch)) epoch = ep
  }
  return { epoch, total }
}

export function bcRowsFromLedgerTail(lines: string[]): {
  epochs: BcEpochRow[]
  evals: BcEvalRow[]
} {
  const epochs: BcEpochRow[] = []
  const evals: BcEvalRow[] = []
  const from = bcSegmentStart(lines)
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
  // ★2026-11-12：同一 `(it, epoch)` 只留**第一条**。账本里出现第二次 = 同一个 epoch 被重写了
  // 一遍（重启重放 / 旧链路那批 30 条重复行），面板按 epoch 连曲线，重复点只会把「共 N epoch」
  // 与表行数说错（g1c 现场 112 行 / 60 epoch）。`it` 一起进键：iters>1 的课每轮都有 ep1。
  const dedupe = <T extends { it: number; epoch: number }>(rows: T[]): T[] => {
    const seen = new Set<string>()
    const out: T[] = []
    for (const r of rows) {
      const k = `${r.it}:${r.epoch}`
      if (seen.has(k)) continue
      seen.add(k)
      out.push(r)
    }
    return out
  }
  return { epochs: dedupe(epochs), evals: dedupe(evals) }
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

/** BC epoch 面板视图：账本尾窗解析 + 课程 eval.every_epochs（面板头提示用）。
 *
 *  `pointer` 与侧栏/pill 的读数**同一个函数**（`bcPointerFromLedgerTail`，同一份尾行）——
 *  面板表头的 `ep57/60` 与侧栏的 `ep57` 不会各说一个数。 */
export async function buildBcEpochsView(course: string | undefined): Promise<{
  epochs: BcEpochRow[]
  evals: BcEvalRow[]
  evalEveryEpochs: number | null
  pointer: BcPointerView
  codeSnapshot: BcCodeSnapshotRow | null
}> {
  const noPointer: BcPointerView = { epoch: null, total: null }
  if (!course) {
    return { epochs: [], evals: [], evalEveryEpochs: null, pointer: noPointer, codeSnapshot: null }
  }
  let epochs: BcEpochRow[] = []
  let evals: BcEvalRow[] = []
  let pointer: BcPointerView = noPointer
  try {
    // 读历史可回退备份（plan/course-metrics-persistence §3.3 R5）。
    const tail = await readCourseTail(course, 'training_log.jsonl', 4000)
    const parsed = bcRowsFromLedgerTail(tail)
    epochs = parsed.epochs.slice(-100)
    evals = parsed.evals.slice(-20)
    pointer = bcPointerFromLedgerTail(tail)
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
  return { epochs, evals, evalEveryEpochs, pointer, codeSnapshot: latestBcCodeSnapshot(course) }
}
