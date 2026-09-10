/** evalboard.ts — 控制台 EvalBoard 数据端点（plan/rl-eval-system.md §8）。
 *
 * - `GET /api/evalboard`：独立路由（不塞 /api/state），照抄 /api/pool 模板
 *   （首屏外异步拉 + 30s TTL + 课程键控）。
 * - W1 自动入账：每次构建视图时 read-through——扫描 `tmp/<course>/traj/`
 *   的 `eval_log.jsonl`，新行经 `ingestRows` 入 EvalStore（A 层 run_id 代理 =
 *   course；同 course/iter/wver/stage/seed 即同一局，去重天然正确）。
 *   训练循环零改动、零新增跑批成本。
 * - `POST /api/evalProbeRun`：只 append 一行 `status=pending` 到 batches.jsonl
 *  （§6.7 队列文件桥）；派发期节点配置冻结经 busy 前置检查（§8）。
 */

import { existsSync, readFileSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../paths'
import { loadBatches, enqueueBatch } from '../evalboard/batches'
import { ingestRows, type IngestCtx, type RawEvalRow } from '../evalboard/ingest'
import { computeEngineEpoch, gitCommit } from '../evalboard/engine'
import { REPO_ROOT as EVAL_REPO_ROOT } from '../../agent/codehash-files'
import { evaluateGate, markProvisional, type LadderRung } from '../evalboard/ladder'
import { deriveMetrics, flipRate, mddPaired, windowAgg, type TierMetrics } from '../evalboard/stats'
import {
  checkS1,
  checkS10,
  checkS11,
  checkS2,
  checkS3,
  checkS4,
  checkS5norm,
  checkS6,
  checkS8,
  type PairedDelta,
} from '../evalboard/sentinels'
import { loadRows, type EvalGameRow } from '../evalboard/store'
// 视图类型唯一源：ui/view.ts（本文件实现与面板共用形态）。
import type { EvalAlert, EvalBatchRow, EvalBoardView, EvalLadderRow } from '../ui/view'

export function evalDataRoot(): string {
  return (
    process.env.EVALBOARD_DATA ?? path.join(REPO_ROOT, 'tools', 'training', 'data', 'evalboard')
  )
}

/** 入库阶梯正本（含可执行载荷；god 为空 = 基线未跑）。 */
function trackedLadder(): LadderRung[] {
  const p = path.join(REPO_ROOT, 'tools', 'training', 'evalboard', 'ladder.json')
  const doc = JSON.parse(readFileSync(p, 'utf-8')) as { rungs: LadderRung[] }
  return doc.rungs
}

/** 工作副本 god 覆盖（data/ladder.json；缺失则只用正本）。 */
function ladderWithGod(): LadderRung[] {
  const base = trackedLadder()
  const work = path.join(evalDataRoot(), 'ladder.json')
  try {
    if (!existsSync(work)) return base
    const doc = JSON.parse(readFileSync(work, 'utf-8')) as { rungs: LadderRung[] }
    const godById = new Map(doc.rungs.map((r) => [r.id, r.god]))
    return markProvisional(
      base.map((r) => (godById.has(r.id) ? { ...r, god: godById.get(r.id)! } : r)),
    )
  } catch {
    return base
  }
}

// ────────────────────────── W1 read-through 入账 ──────────────────────────

let engineMemo: EvalGameRow['engine'] | null = null
function engineOfConsole(): EvalGameRow['engine'] {
  if (!engineMemo) {
    // 惰性一次：tree walk 秒级，memo 后零成本。
    try {
      const { engine_epoch } = computeEngineEpoch(EVAL_REPO_ROOT, gitCommit(EVAL_REPO_ROOT))
      engineMemo = { git_commit: gitCommit(EVAL_REPO_ROOT), dist_codehash: '', engine_epoch }
    } catch {
      engineMemo = { git_commit: 'unknown', dist_codehash: '', engine_epoch: 'unknown' }
    }
  }
  return engineMemo
}

/** 课程 eval_log.jsonl（训练落盘处，tmp 缓冲）→ EvalStore。返回新入账行数。 */
export function ingestCourseEvalLog(course: string): number {
  if (!course) return 0
  const evalLog = path.join(REPO_ROOT, 'tmp', course, 'traj', 'eval_log.jsonl')
  if (!existsSync(evalLog)) return 0
  const raws: RawEvalRow[] = []
  for (const line of readFileSync(evalLog, 'utf-8').split('\n')) {
    if (!line.trim()) continue
    try {
      const r = JSON.parse(line) as RawEvalRow
      if (r.event === 'eval') raws.push(r)
    } catch {
      /* 坏行跳过 */
    }
  }
  if (raws.length === 0) return 0
  const ctx: IngestCtx = {
    run_id: course,
    course,
    batch_id: `A-${course}`,
    batch_of: 1,
    rungOfStage: (s) => `stage-${s}`,
    engine: engineOfConsole(),
    source: 'A',
    ckpt_path: '',
    ckpt_sha16: '',
    init_sha16: '',
    difficulty: 'hard',
    maxTicks: 12000,
  }
  // wver/iter 逐行透传：ingestEvalRow 从 raw 取 iter/wver（A 行自带）。
  const { appended } = ingestRows(evalDataRoot(), raws, {
    ...ctx,
    rungOfStage: (s) => `stage-${s}`,
  })
  return appended
}

// ────────────────────────── 视图 ──────────────────────────

type LadderRowView = EvalLadderRow
type BatchView = EvalBatchRow

const VIEW_TTL_MS = 30_000
const viewCache = new Map<string, { at: number; view: EvalBoardView }>()

function courseEvalEvery(course: string): number | null {
  try {
    const p = path.join(REPO_ROOT, 'nn-training', 'curricula', `${course}.jsonc`)
    if (!existsSync(p)) return null
    const text = readFileSync(p, 'utf-8').replace(/\/\/.*$/gm, '')
    const m = text.match(/"eval_every"\s*:\s*(\d+)/)
    return m ? Number(m[1]) : null
  } catch {
    return null
  }
}

function toBatchView(b: ReturnType<typeof loadBatches>[number]): BatchView {
  return {
    batch_id: b.batch_id,
    course: b.course,
    rung_from: b.rung_from,
    status: b.status,
    iter: b.iter,
    trigger: b.trigger,
    units: b.units,
    elapsed_sec: b.elapsed_sec,
    created_ts: b.created_ts,
  }
}

export function buildEvalBoardView(course = '', fresh = false): EvalBoardView {
  const key = `evalboard:${course}`
  const cached = viewCache.get(key)
  if (!fresh && cached && Date.now() - cached.at < VIEW_TTL_MS) return cached.view

  const ingested = ingestCourseEvalLog(course)
  const root = evalDataRoot()
  const allRows = loadRows(root).filter((r) => !course || r.course === course)
  const batches = loadBatches(root)
    .filter((b) => !course || b.course === course)
    .sort((a, b) => (a.created_ts < b.created_ts ? -1 : 1))
  const ladder = ladderWithGod()
  const spaceCalibrated = existsSync(path.join(root, 'space_calibration.json'))

  // rung → 按批次分组的行（批次按 created_ts 序；窗 = 最近连续 4 批）。
  const alerts: EvalAlert[] = []
  const flips: EvalBoardView['flips'] = []
  // 窗 = 该 rung 最近连续 4 批（有行才算一批；台账顺序优先，未知 batch_id 殿后）。
  const batchOrder = new Map(batches.map((b, i) => [b.batch_id, i]))
  const ladderViews: LadderRowView[] = ladder.map((r) => {
    const byBatch = new Map<string, EvalGameRow[]>()
    for (const x of allRows) {
      if (x.rung !== r.id) continue
      const arr = byBatch.get(x.batch_id) ?? []
      arr.push(x)
      byBatch.set(x.batch_id, arr)
    }
    const keys = [...byBatch.keys()].sort(
      (a, b) => (batchOrder.get(a) ?? 1e9) - (batchOrder.get(b) ?? 1e9),
    )
    const recentKeys = keys.slice(-4)
    const recent = recentKeys.map((k) => batches.find((b) => b.batch_id === k) ?? { batch_id: k })
    const groups = recentKeys.map((k) => byBatch.get(k) ?? [])
    const w = windowAgg(
      r.id,
      groups.filter((g) => g.length > 0),
    )
    const student: TierMetrics = w.metrics
    const godWin = r.god.winRate
    const godPrice = r.god.lifePrice
    let gate: LadderRowView['gate'] = null
    let deltaVsGod: number | null = null
    if (godWin !== null && godPrice !== null && !w.partial && w.n > 0) {
      const god: TierMetrics = { ...student, n: r.god.n, winRate: godWin, lifePrice: godPrice }
      const g = evaluateGate({ student, god, godWinRate1600: godWin })
      gate = { main: g.main, cost: g.cost, style: g.style, credible: g.credible, pass: g.pass }
      deltaVsGod = student.winRate - godWin
      // S1 / S11（B 窗输入；MDD 用配对口径近似，flips 按 40% 保守计 §2.4）。
      const s1 = checkS1(student, god)
      if (s1) alerts.push({ ...s1, message: `${r.id} ${s1.message}` })
      const s11 = checkS11(
        { student, god, mdd: mddPaired(w.n, Math.round(w.n * 0.4)) },
        spaceCalibrated,
      )
      if (s11) alerts.push({ ...s11, message: `${r.id} ${s11.message}` })
    }
    // S10：窗内跨 epoch。
    if (w.n > 0) {
      const s10 = checkS10(groups.flat().map((x) => x.engine.engine_epoch))
      if (s10) alerts.push({ ...s10, message: `${r.id} ${s10.message}` })
    }
    // 翻转矩阵：同 rung 相邻批次（seed 相交 = 配对）。
    for (let i = 1; i < groups.length; i++) {
      const pa = new Map(groups[i - 1].map((x) => [x.seed, x.win]))
      const pb = new Map(groups[i].map((x) => [x.seed, x.win]))
      const shared = [...pa.keys()].filter((s) => pb.has(s))
      const ma = deriveMetrics(groups[i - 1])
      const mb = deriveMetrics(groups[i])
      if (shared.length >= 20) {
        const f = flipRate(pa, pb)
        flips.push({
          rung: r.id,
          from: recent[i - 1].batch_id,
          to: recent[i].batch_id,
          delta: f.delta,
          paired: true,
          flips: f.flips,
          rate: f.rate,
        })
        const s6 = checkS6(f.rate, f.delta, mddPaired(f.n, f.flips))
        if (s6) alerts.push({ ...s6, message: `${r.id} ${recent[i].batch_id} ${s6.message}` })
      } else if (groups[i - 1].length > 0 && groups[i].length > 0) {
        flips.push({
          rung: r.id,
          from: recent[i - 1].batch_id,
          to: recent[i].batch_id,
          delta: mb.winRate - ma.winRate,
          paired: false,
          flips: null,
          rate: null,
        })
      }
    }
    return {
      rung: r.id,
      dimension: r.dimension,
      lives: r.lives,
      god: r.god,
      batches: recent.length,
      n: w.n,
      latestWin: w.latest ? w.latest.winRate : null,
      windowWin: w.partial ? null : w.metrics.winRate,
      deltaVsGod,
      gate,
      partial: w.partial,
    }
  })

  // A 层相邻 ckpt 配对哨兵（S2/S3/S4/S5/S6）：同 rung 同 seed 集的连续 wver 对。
  const aRows = allRows.filter((r) => r.source === 'A')
  const byRungWver = new Map<string, EvalGameRow[]>()
  for (const r of aRows) {
    const k = `${r.rung}\u0001${r.wver}\u0001${r.iter}`
    const arr = byRungWver.get(k) ?? []
    arr.push(r)
    byRungWver.set(k, arr)
  }
  const rungGroups = new Map<string, { key: string; rows: EvalGameRow[] }[]>()
  for (const [k, rows] of byRungWver) {
    const rung = k.split('\u0001')[0]
    const arr = rungGroups.get(rung) ?? []
    arr.push({ key: k, rows })
    rungGroups.set(rung, arr)
  }
  for (const [rung, groups] of rungGroups) {
    const sorted = groups.sort((a, b) => (a.key < b.key ? -1 : 1))
    for (let i = 1; i < sorted.length; i++) {
      const A = sorted[i - 1].rows
      const B = sorted[i].rows
      const sa = new Set(A.map((x) => x.seed))
      if (A.length === 0 || B.length === 0 || !B.every((x) => sa.has(x.seed))) continue
      const ma = deriveMetrics(A)
      const mb = deriveMetrics(B)
      const f = flipRate(
        new Map(A.map((x) => [x.seed, x.win])),
        new Map(B.map((x) => [x.seed, x.win])),
      )
      const mdd = mddPaired(f.n, f.flips)
      const d: PairedDelta = {
        dWin: mb.winRate - ma.winRate,
        dDeath: mb.deathRate - ma.deathRate,
        dKillCompletion: mb.killCompletion - ma.killCompletion,
        dTimeout: mb.timeoutRate - ma.timeoutRate,
        dAccuracy: mb.accuracy - ma.accuracy,
        dShotsPerGame: mb.shotsPerGame - ma.shotsPerGame,
        dWinTickMedian: 0,
        mdd,
        winTickNa: mb.winTickMedian === null || ma.winTickMedian === null,
      }
      if (mb.winTickMedian !== null && ma.winTickMedian !== null) {
        d.dWinTickMedian = mb.winTickMedian - ma.winTickMedian
      }
      for (const h of [
        checkS2(d),
        checkS3(d),
        checkS4(d),
        checkS5norm(
          mb.winTickMedian !== null && ma.winTickMedian !== null
            ? (mb.winTickMedian - ma.winTickMedian) / 12000
            : 0,
          mdd,
          d.winTickNa,
        ),
        checkS6(f.rate, f.delta, mdd),
      ]) {
        if (h) alerts.push({ ...h, message: `${rung} ${h.message}` })
      }
    }
  }

  // S8：B/C 行数据污染（整批拒绝信号）。
  for (const r of allRows.filter((r) => r.source !== 'A')) {
    const h = checkS8({
      dropped: 0,
      metrics_version: r.metrics_version,
      ckpt_sha16: r.ckpt_sha16 || null,
    })
    if (h) {
      alerts.push({ ...h, message: `${r.batch_id}/${r.rung} ${h.message}` })
      break
    }
  }

  const evalEvery = course ? courseEvalEvery(course) : null
  const abWarn =
    evalEvery === 1
      ? 'eval_every=1：A 层每轮跑，B 层无派发窗口（§6.4）——先放宽 eval_every 或走 standalone 触发'
      : null

  const view: EvalBoardView = {
    cachedAt: Date.now(),
    course,
    ingested,
    evalEvery,
    abWarn,
    ladder: ladderViews,
    alerts,
    batches: batches.map(toBatchView),
    flips,
    rows: allRows.length,
    spaceCalibrated,
  }
  viewCache.set(key, { at: view.cachedAt, view })
  return view
}

/** POST /api/evalProbeRun 入队（只 append pending；派发期节点配置冻结经 busy 前置检查）。 */
export function enqueueProbeRun(spec: {
  course: string
  rung_from: string
  ckpt: string
  requester: string
  iter: number
  policy?: 'nn' | 'god'
  ladder_pos?: number
  k_seq?: number
  init_sha16?: string
}): { batch_id: string; deduped: boolean } {
  const root = evalDataRoot()
  const dup = loadBatches(root).find(
    (b) =>
      b.status === 'pending' &&
      b.course === spec.course &&
      b.rung_from === spec.rung_from &&
      b.ckpt === spec.ckpt,
  )
  if (dup) return { batch_id: dup.batch_id, deduped: true }
  const b = enqueueBatch(root, {
    course: spec.course,
    rung_from: spec.rung_from,
    ckpt: spec.ckpt,
    requester: spec.requester || 'web',
    trigger: 'standalone',
    iter: spec.iter,
    units: { of: 2, done: [] },
    k_seq: spec.k_seq ?? 0,
    window_seq: 0,
    ...(spec.policy ? { policy: spec.policy } : {}),
    ...(spec.ladder_pos !== undefined ? { ladder_pos: spec.ladder_pos } : {}),
    ...(spec.init_sha16 ? { init_sha16: spec.init_sha16 } : {}),
  })
  return { batch_id: b.batch_id, deduped: false }
}

export function invalidateEvalBoard(): void {
  viewCache.clear()
}
