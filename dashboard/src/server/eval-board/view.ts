/** view.ts — 评估板看板视图合成（阶梯 / 批次 / 告警 / 迭代行，含 TTL 缓存）。 */
import { existsSync, readFileSync } from 'fs'
import path from 'path'
import { DIFFICULTIES } from '../../../../src/config/difficulty'
import { REPO_ROOT } from '../../core/paths'
import { loadBatches } from '../../evalboard/batches'
import { evaluateGate } from '../../evalboard/ladder'
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
} from '../../evalboard/sentinels'
import {
  deriveMetrics,
  flipRate,
  mddPaired,
  type TierMetrics,
  windowAgg,
} from '../../evalboard/stats'
import { type EvalGameRow, loadRows } from '../../evalboard/store'
import type { EvalAlert, EvalBatchRow, EvalBoardView, EvalLadderRow } from '../../web/view'
import { ladderStateFor } from './auto-ladder'
import { ingestCourseEvalLog } from './ingest'
import { evalDataRoot, ladderWithGod, readRunnerState } from './ladder-data'

// ────────────────────────── 视图 ──────────────────────────

type LadderRowView = EvalLadderRow
type BatchView = EvalBatchRow

const VIEW_TTL_MS = 30_000
export const viewCache = new Map<string, { at: number; view: EvalBoardView }>()

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
    ladderState: ladderStateFor(course, allRows, batches),
  }
  viewCache.set(key, { at: view.cachedAt, view })
  return view
}

/** POST /api/evalProbeRun 入队（P1：只写请求文件，runner 下窗物化为批）。 */
