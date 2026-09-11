/** evalboard.ts — 控制台 EvalBoard 数据端点（plan/rl-eval-system.md §8）。
 *
 * - `GET /api/evalboard`：独立路由（不塞 /api/state），照抄 /api/pool 模板
 *   （首屏外异步拉 + 30s TTL + 课程键控）。
 * - W1 自动入账：每次构建视图时 read-through——扫描 `tmp/<course>/eval_log.jsonl`
 *   （真实布局：`loop_core.py:189` `_traj_dir = _traj_root/it<N>`，`eval_dispatch.py:93`
 *   取 `traj_dir.parent` ⇒ 账本在 `<traj 根>/eval_log.jsonl`，**不是** `traj/` 子目录；
 *   旧写法路径错误导致恒 0 入账 —— 2026-09-10 修复，保留 legacy 候选兼容），
 *   新行经 `ingestRows` 入 EvalStore（A 层 run_id 代理 = course；
 *   同 course/iter/wver/stage/seed 即同一局，去重天然正确）。
 *   训练循环零改动、零新增跑批成本。
 * - `POST /api/evalProbeRun`：只 append 一行 `status=pending` 到 batches.jsonl
 *  （§6.7 队列文件桥）；派发期节点配置冻结经 busy 前置检查（§8）。
 */

import { existsSync, readdirSync, readFileSync, statSync } from 'fs'
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
import { DIFFICULTIES } from '../../../src/config/difficulty'
// 视图类型唯一源：ui/view.ts（本文件实现与面板共用形态）。
import type {
  EvalAlert,
  EvalBatchRow,
  EvalBoardView,
  EvalCkptFile,
  EvalCkptsView,
  EvalLadderRow,
} from '../ui/view'

/** `*.it<N>.*.json` 文件名 → N（R3/D-b：iter 不靠前端手传）。无匹配 ⇒ null。 */
export function iterFromCkpt(ckpt: string): number | null {
  const m = /\.it(\d+)\./.exec(path.basename(ckpt))
  return m ? Number(m[1]) : null
}

// ────────────────────────── R7 ckpt/iter 发现（只读元数据，不读内容） ──────────────────────────

const WEIGHTS_DIR = path.join(REPO_ROOT, 'nn-training', 'weights')
/** 单次返回文件数上限（腿很大时防 payload 爆）。 */
const CKPT_MAX_FILES = 500

/**
 * GET /api/evalCkpts（R7）：扫 `nn-training/weights/<leg>/` + `tmp/<course>/weights.json`。
 * 硬约束：**只 stat，不读内容**（996 文件 / 434MB，读内容会把 TTL 打穿）。
 * 懒加载：无 `leg` 时只返回腿列表；`leg` = 某腿名时返回该腿文件明细；
 * `course` 与某腿同名则默认取该腿（首页 iter select 一次请求）。
 */
export function buildEvalCkptsView(course = '', leg = ''): EvalCkptsView {
  const legs: EvalCkptsView['legs'] = []
  let names: string[] = []
  try {
    names = readdirSync(WEIGHTS_DIR, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
      .sort()
  } catch {
    names = []
  }
  for (const name of names) {
    let count = 0
    try {
      count = readdirSync(path.join(WEIGHTS_DIR, name)).filter((f) => f.endsWith('.json')).length
    } catch {
      /* 不可读腿跳过明细，仅计 0 */
    }
    legs.push({ leg: name, count })
  }
  // 路径安全：leg 必须命中已知腿名（防 ../ 穿越）。
  const target =
    (leg && names.includes(leg) ? leg : '') || (course && names.includes(course) ? course : '')
  const files: EvalCkptFile[] = []
  let truncated = false
  if (target) {
    let jsonFiles: string[] = []
    try {
      jsonFiles = readdirSync(path.join(WEIGHTS_DIR, target)).filter((f) => f.endsWith('.json'))
    } catch {
      jsonFiles = []
    }
    for (const f of jsonFiles) {
      if (files.length >= CKPT_MAX_FILES) {
        truncated = true
        break
      }
      const abs = path.join(WEIGHTS_DIR, target, f)
      let st: ReturnType<typeof statSync>
      try {
        st = statSync(abs)
      } catch {
        continue
      }
      files.push({
        leg: target,
        path: path.relative(REPO_ROOT, abs).replace(/\\/g, '/'),
        mtime: st.mtimeMs,
        sizeBytes: st.size,
        iter: iterFromCkpt(f),
      })
    }
    files.sort((a, b) => b.mtime - a.mtime)
  }
  // 活动权重兜底（tmp/<course>/weights.json，若存在）。
  if (course) {
    const act = path.join(REPO_ROOT, 'tmp', course, 'weights.json')
    try {
      const st = statSync(act)
      files.unshift({
        leg: '(active)',
        path: path.relative(REPO_ROOT, act).replace(/\\/g, '/'),
        mtime: st.mtimeMs,
        sizeBytes: st.size,
        iter: iterFromCkpt(act),
      })
    } catch {
      /* 无活动权重 */
    }
  }
  return { course, legs, files, truncated }
}

/** R4-G1 心跳（runner_state.json）：训练侧唯一写者，console 只读；缺失 ⇒ null。 */
function readRunnerState(): EvalBoardView['runnerState'] {
  const p = path.join(evalDataRoot(), 'runner_state.json')
  try {
    if (!existsSync(p)) return null
    const j = JSON.parse(readFileSync(p, 'utf-8')) as Record<string, unknown>
    const num = (v: unknown): number | null => (Number.isFinite(Number(v)) ? Number(v) : null)
    return {
      windowOpen: j.window_open === true,
      updatedTs: Number(j.updated_ts) || 0,
      batchId: typeof j.batch_id === 'string' ? j.batch_id : null,
      unitIdx: num(j.unit_idx),
      unitOf: num(j.unit_of),
      rung: typeof j.rung === 'string' ? j.rung : null,
      remainingUnits: Number(j.remaining_units) || 0,
      lastWindowClosedTs: num(j.last_window_closed_ts),
      engineEpoch: typeof j.engine_epoch === 'string' ? j.engine_epoch : '',
    }
  } catch {
    return null
  }
}

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

let rungIdsMemo: Set<string> | null = null
function ladderRungIds(): Set<string> {
  if (!rungIdsMemo) {
    try {
      rungIdsMemo = new Set(trackedLadder().map((r) => r.id))
    } catch {
      rungIdsMemo = new Set()
    }
  }
  return rungIdsMemo
}

/**
 * 课程 → 阶梯 rung id（`c<count>l<lives>`）。
 *
 * A 层 eval 行的 `stage` 是课程自定义关 id（如 2000），与阶梯 rung id（`c4l1`）不同名；
 * 若按 `stage-<id>` 落账，这些行永远 join 不上阶梯表（console 侧 `x.rung === r.id`），
 * 面板恒空。课程几何（count/lives）与 rung 一一对应，故按 count/lives 反查。
 * 无对应 rung 的课程（如 c5-margin）返回 null → 调用方回退 `stage-<id>`。
 */
export interface CourseRungMeta {
  /** 匹配到的阶梯 rung id；无对应 rung（如 c5-margin）为 null。 */
  rung: string | null
  /** 课程**实际**跑关参数 —— probe_key 的三个可比性分量必须取自这里，不能硬编码。 */
  maxTicks: number
  mapHash: string
}

/**
 * 课程 → 阶梯 rung 元信息（rung id / maxTicks / mapHash）。
 *
 * `probe_key = <rung>-<difficulty>-t<maxTicks>-<mapHash>-<seedSpace>` 是 §3.5 的
 * **可比性键**，三个分量都必须来自课程实跑参数：
 * - `maxTicks` 取自课程 jsonc（历史课程是 2400，阶梯 rung 是 12000 —— 两者不可比，
 *   硬编码会让 A 行冒充 t12000 数据，跨档相减不被断言拦截）；
 * - `mapHash` 取自阶梯 rung（原实现缺省 `mapHashOfStage` → 全部落成 `unknown`，
 *   等于把"可比性键"退化成常量）。
 */
export function courseRungMeta(course: string): CourseRungMeta | null {
  if (!course) return null
  try {
    const p = path.join(REPO_ROOT, 'nn-training', 'curricula', `${course}.jsonc`)
    if (!existsSync(p)) return null
    const text = readFileSync(p, 'utf-8').replace(/\/\/.*$/gm, '')
    const count = Number(text.match(/"count"\s*:\s*(\d+)/)?.[1])
    const lives = Number(text.match(/"lives"\s*:\s*(\d+)/)?.[1])
    const maxTicks = Number(text.match(/"max_ticks"\s*:\s*(\d+)/)?.[1])
    const id = Number.isInteger(count) && Number.isInteger(lives) ? `c${count}l${lives}` : null
    const rung = id && ladderRungIds().has(id) ? id : null
    let mapHash = 'unknown'
    if (rung) {
      mapHash = trackedLadder().find((r) => r.id === rung)?.mapHash ?? 'unknown'
    }
    return { rung, maxTicks: Number.isInteger(maxTicks) ? maxTicks : 12000, mapHash }
  } catch {
    return null
  }
}

/** 便捷包装：只要 rung id（无对应 rung 返回 null）。 */
export function courseRungId(course: string): string | null {
  return courseRungMeta(course)?.rung ?? null
}

/** 课程 eval_log.jsonl（训练落盘处，tmp 缓冲）→ EvalStore。返回新入账行数。 */
export function ingestCourseEvalLog(course: string): number {
  if (!course) return 0
  // 真实布局优先；legacy `traj/` 候选保留兼容（两者都不在则 0）。
  const candidates = [
    path.join(REPO_ROOT, 'tmp', course, 'eval_log.jsonl'),
    path.join(REPO_ROOT, 'tmp', course, 'traj', 'eval_log.jsonl'),
  ]
  const evalLog = candidates.find((p) => existsSync(p))
  if (!evalLog) return 0
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
  // 课程 → 阶梯 rung 元信息（A 行 stage 是自定义关 id，必须映射；maxTicks/mapHash
  // 取自课程实跑参数，不得硬编码，否则 probe_key 这个可比性键失真）。
  const meta = courseRungMeta(course)
  const rung = meta?.rung ?? null
  const rungOfStage = (s: string | number): string => rung ?? `stage-${s}`
  const ctx: IngestCtx = {
    run_id: course,
    course,
    batch_id: `A-${course}`,
    batch_of: 1,
    rungOfStage,
    engine: engineOfConsole(),
    source: 'A',
    ckpt_path: '',
    ckpt_sha16: '',
    init_sha16: '',
    difficulty: 'hard',
    maxTicks: meta?.maxTicks ?? 12000,
    mapHashOfStage: () => meta?.mapHash ?? 'unknown',
  }
  // wver/iter 逐行透传：ingestEvalRow 从 raw 取 iter/wver（A 行自带）。
  const { appended } = ingestRows(evalDataRoot(), raws, ctx)
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
    policy: b.policy ?? (b.ckpt === 'god' ? 'god' : 'nn'),
    ckpt: b.ckpt,
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

  // God 叠加：ladder 工作副本未写基线时，用已入账 C 层（policy=god）聚合补上——
  // 否则批 done 后首页 God 仍 TBD / eval now（2026-09-11）。
  const godOverlay = new Map<string, { winRate: number; n: number; provisional: boolean }>()
  for (const x of allRows) {
    if (x.source !== 'C' && x.policy !== 'god') continue
    const cur = godOverlay.get(x.rung) ?? { winRate: 0, n: 0, provisional: false }
    cur.n += 1
    if (x.win) cur.winRate += 1
    godOverlay.set(x.rung, cur)
  }
  for (const g of godOverlay.values()) {
    if (g.n > 0) g.winRate = g.winRate / g.n
    g.provisional = g.n > 0 && g.winRate < 0.3
  }
  const ladderMerged = ladder.map((r) =>
    r.god.winRate === null && godOverlay.has(r.id)
      ? {
          ...r,
          god: {
            winRate: godOverlay.get(r.id)!.winRate,
            lifePrice: r.god.lifePrice,
            n: godOverlay.get(r.id)!.n,
            provisional: godOverlay.get(r.id)!.provisional,
          },
        }
      : r,
  )

  // rung → 按批次分组的行（批次按 created_ts 序；窗 = 最近连续 4 批）。
  const alerts: EvalAlert[] = []
  const flips: EvalBoardView['flips'] = []
  // 窗 = 该 rung 最近连续 4 批（有行才算一批；台账顺序优先，未知 batch_id 殿后）。
  const batchOrder = new Map(batches.map((b, i) => [b.batch_id, i]))
  // A 层行按 rung 归集（§4.3：不进窗，只出"最近 iter 读数"作趋势；§2.1 不判能力）。
  const aRows = allRows.filter((x) => x.source === 'A')
  const aByRung = new Map<string, EvalGameRow[]>()
  for (const x of aRows) {
    const arr = aByRung.get(x.rung) ?? []
    arr.push(x)
    aByRung.set(x.rung, arr)
  }
  const ladderViews: LadderRowView[] = ladderMerged.map((r) => {
    const byBatch = new Map<string, EvalGameRow[]>()
    for (const x of allRows) {
      if (x.rung !== r.id) continue
      // §4.3：窗 = **B 层批次**。A 行固定 EVAL_SEEDS 逐 iter 重复采样（同 100 seed ×N 次），
      // 池进窗会重复计同一 seed（n 虚高、CI 失真），且 §2.1 规定 A 层只做健康/趋势不判能力。
      // A 行仍参与下方 A 配对哨兵（S2–S5）与趋势展示。
      if (x.source !== 'B') continue
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
    // A 层趋势：该 rung 最近一次 iter 的 100 局读数（同 seed 组，故只取最新 iter 不叠加）。
    const aList = aByRung.get(r.id) ?? []
    let aTrend: LadderRowView['aTrend'] = null
    if (aList.length > 0) {
      const maxIter = Math.max(...aList.map((x) => x.iter))
      const latestA = aList.filter((x) => x.iter === maxIter)
      aTrend = { n: latestA.length, winRate: deriveMetrics(latestA).winRate, iter: maxIter }
    }
    const tiles = r.stage.tiles ?? []
    const tileText = tiles.join('')
    const enemies = r.stage.enemies ?? []
    return {
      rung: r.id,
      dimension: r.dimension,
      lives: r.lives,
      starLevel: DIFFICULTIES[r.difficulty]?.playerStartLevel ?? 0,
      stageBrief: {
        name: r.stage.name,
        enemies,
        enemyCount: r.stage.enemyCount ?? enemies.length,
        hasBase: tileText.includes('E'),
        hasTerrain: /[bswfi]/.test(tileText),
      },
      god: r.god,
      batches: recent.length,
      n: w.n,
      latestWin: w.latest ? w.latest.winRate : null,
      windowWin: w.partial ? null : w.metrics.winRate,
      aTrend,
      deltaVsGod,
      gate,
      partial: w.partial,
    }
  })

  // ── R5 iter 矩阵：行 = God + 各 B 层 iter（cells key = `${rung}.${metric}`） ──
  const bRows = allRows.filter(
    (x) => x.source === 'B' && !!x.batch_id && !x.batch_id.startsWith('A-'),
  )
  const iterRows: EvalBoardView['iterRows'] = []
  // God 行：只填 winRate（God 基线只产 winRate/lifePrice，§4-R5④）。
  const godCells: Record<string, number | null> = {}
  for (const r of ladder) godCells[`${r.id}.winRate`] = r.god.winRate
  iterRows.push({
    kind: 'god',
    course,
    iter: -1,
    cells: godCells,
    n: 0,
    batchIds: [],
    screening: false,
  })
  if (bRows.length > 0) {
    const byIter = new Map<number, EvalGameRow[]>()
    for (const x of bRows) {
      const a = byIter.get(x.iter) ?? []
      a.push(x)
      byIter.set(x.iter, a)
    }
    // 仅有批（尚无行）的 nn iter 也出行，供用户看到「已入队」；A12 空态另在 UI 判定。
    for (const b of batches) {
      const pol = b.policy ?? (b.ckpt === 'god' ? 'god' : 'nn')
      if (pol === 'nn' && !byIter.has(b.iter)) byIter.set(b.iter, [])
    }
    for (const [it, rows] of [...byIter.entries()].sort((a, b) => b[0] - a[0])) {
      const cells: Record<string, number | null> = {}
      const byRung = new Map<string, EvalGameRow[]>()
      for (const x of rows) {
        const a = byRung.get(x.rung) ?? []
        a.push(x)
        byRung.set(x.rung, a)
      }
      for (const [rungId, rs] of byRung) {
        const m = deriveMetrics(rs)
        cells[`${rungId}.winRate`] = m.winRate
        cells[`${rungId}.clearRate`] = m.clearRate
        cells[`${rungId}.killCompletion`] = m.killCompletion
        cells[`${rungId}.meanKills`] = m.meanKills
        cells[`${rungId}.meanPowerUps`] = m.meanPowerUps
        cells[`${rungId}.winTickMean`] = m.winTickMean
        cells[`${rungId}.winHpLeftMean`] = m.winHpLeftMean
      }
      iterRows.push({
        kind: 'iter',
        course,
        iter: it,
        cells,
        n: rows.length,
        batchIds: [...new Set(rows.map((x) => x.batch_id))],
        // A4：单批判/未过窗均属筛查级，不作 verdict。
        screening: true,
      })
    }
  }

  // A 层相邻 ckpt 配对哨兵（S2/S3/S4/S5/S6）：同 rung 同 seed 集的连续 wver 对。
  // （aRows / aByRung 已在上方定义，此处复用。）
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
    iterRows,
    runnerState: readRunnerState(),
    ladderState: null,
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
