/**
 * ladder-gate.ts —— I4 晋级门 runner（roadmap v2.0 §4-I4 / §5.2 / §7 运营手册）。
 *
 * 每级毕业评估的固定流程（D4 单轨门）：
 *   两轮各 N 局（默认 200，种子批次不重叠：round1 seed0=0、round2 seed0=200，
 *   间隔 ≥5 训练轮由 runbook 保证）→ pooled 点估计 ≥80%（Wilson 95% LB 随报告）
 *   → verdict = graduate | stay。sentinel：超时占比 ≤0.15（口径已修 d17e9f0）。
 *
 * 报告必附（M1/M3/B8）：
 *   · hazard-by-k（k0/k1/k2/k3+ = 未过关局的击杀分桶）+ 局长分布 p50/p90 + bonus
 *     台数（bonusKills 遥测就位前为 null——worker 行扩展后自动点亮）；
 *   · 同级多腿同种子配对 McNemar（X1）：多 --weight 时逐 seed 配对；
 *   · 跨级召回（B8）：--recall level=weights 对前 1-2 级各 50 局快速召回；
 *   · c01/c02 行为探针：首杀 tick（遥测就位前 null）+ dmg/kill。
 *
 * 用法：
 *   bun tools/sim/ladder-gate.ts --level ladder-c04 \
 *     --weight it30=nn-training/weights/.../it30.json --weight bc=<bc.json> \
 *     --out nn-training/ladder/reports/ladder-c04-it30 [--recall ladder-c03=<w>] \
 *     [--games 200] [--rounds 2]
 *
 * verdict 落报告 JSON + 更新 LEDGER（nn-training/ladder/LEDGER.jsonc，原子写）。
 * tier 边界（c07→c08 / c14→c15）的人工放行由 LEDGER 的 tierBoundaryAck 承载——
 * runner 只出 verdict，不替人做 D11 立案。
 */

import { mkdirSync, readFileSync, writeFileSync, existsSync, renameSync } from 'fs'
import { join, dirname } from 'path'

const NN_ROOT = join(import.meta.dir, '..', '..')
const LEDGER_PATH = join(NN_ROOT, 'ladder', 'LEDGER.jsonc')

export interface GateRow {
  label: string
  seed: number
  win: boolean
  cleared: boolean
  outcome: string
  kills: number
  ticks: number
  playerHits: number
  playerDamageTaken: number
  playerShots: number
  powerUpsCollected: number
  [k: string]: unknown
}

export interface RoundStat {
  games: number
  passed: number
  passRate: number
  timeoutFrac: number
  hazardByK: { k0: number; k1: number; k2: number; k3plus: number }
  ticksP50: number
  ticksP90: number
  dmgPerKill: number | null
  pickupsPerGame: number
}

export interface LegPair {
  b: number // A win & B loss
  c: number // A loss & B win
  z: number | null
}

export interface GateVerdict {
  verdict: 'graduate' | 'stay'
  reason: string
  pooledPassRate: number
  wilsonLB: number
  gate: number
}

/** Wilson 95% 下界（z=1.96）——比点估计保守的通过率区间下沿。 */
export function wilsonLowerBound(passed: number, n: number, z = 1.96): number {
  if (n <= 0) return 0
  const p = passed / n
  const denom = 1 + (z * z) / n
  const centre = p + (z * z) / (2 * n)
  const margin = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))
  return Math.max(0, (centre - margin) / denom)
}

/** percentile（线性插值），rows 需先排序。 */
export function percentile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0
  const pos = (sorted.length - 1) * q
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}

/** hazard-by-k：未过关局按死亡时击杀数分桶（k0/k1/k2/k3+），过关局单列。M1 归因三行之一。 */
export function hazardBuckets(rows: GateRow[]): {
  k0: number
  k1: number
  k2: number
  k3plus: number
  passed: number
} {
  const b = { k0: 0, k1: 0, k2: 0, k3plus: 0, passed: 0 }
  for (const r of rows) {
    const passed = r.win || r.cleared
    if (passed) {
      b.passed++
      continue
    }
    if (r.kills <= 0) b.k0++
    else if (r.kills === 1) b.k1++
    else if (r.kills === 2) b.k2++
    else b.k3plus++
  }
  return b
}

export function roundStat(rows: GateRow[]): RoundStat {
  const passedRows = rows.filter((r) => r.win || r.cleared)
  const ticks = rows.map((r) => r.ticks).sort((a, b) => a - b)
  const timeoutFrac =
    rows.filter((r) => r.outcome === 'max_ticks' && !r.cleared).length / Math.max(1, rows.length)
  const dmg = rows.reduce((s, r) => s + r.playerDamageTaken, 0)
  const kills = rows.reduce((s, r) => s + r.kills, 0)
  return {
    games: rows.length,
    passed: passedRows.length,
    passRate: rows.length > 0 ? passedRows.length / rows.length : 0,
    timeoutFrac,
    hazardByK: hazardBuckets(rows),
    ticksP50: Math.round(percentile(ticks, 0.5)),
    ticksP90: Math.round(percentile(ticks, 0.9)),
    dmgPerKill: kills > 0 ? Math.round((dmg / kills) * 10) / 10 : null,
    pickupsPerGame:
      Math.round(
        (rows.reduce((s, r) => s + r.powerUpsCollected, 0) / Math.max(1, rows.length)) * 100,
      ) / 100,
  }
}

/** 同种子配对 McNemar（X1）：A/B 两腿在相同 seed 上的 win 差异。|z|≥1.96 显著。 */
export function mcnemarPaired(a: GateRow[], bRows: GateRow[]): LegPair | null {
  const bySeed = new Map<number, boolean>()
  for (const r of a) bySeed.set(r.seed, r.win || r.cleared)
  let bWin = 0
  let cWin = 0
  let pairs = 0
  for (const r of bRows) {
    const aw = bySeed.get(r.seed)
    if (aw === undefined) continue
    const bw = r.win || r.cleared
    pairs++
    if (aw && !bw) bWin++
    if (!aw && bw) cWin++
  }
  if (pairs === 0 || bWin + cWin === 0) return null
  const z = (bWin - cWin) / Math.sqrt(bWin + cWin)
  return { b: bWin, c: cWin, z: Math.round(z * 100) / 100 }
}

export function decideVerdict(
  pooledPassed: number,
  pooledGames: number,
  timeoutFrac: number,
  gate = 0.8,
  minGames = 400,
): GateVerdict {
  const lb = wilsonLowerBound(pooledPassed, pooledGames)
  const rate = pooledGames > 0 ? pooledPassed / pooledGames : 0
  // 哨兵：超时占比 >15% = 苟活信号（c4-margin 门线；口径已修 d17e9f0）。
  if (timeoutFrac > 0.15) {
    return {
      verdict: 'stay',
      reason: `哨兵红：超时占比 ${(timeoutFrac * 100).toFixed(1)}% > 15%（pooled ${(rate * 100).toFixed(1)}%）`,
      pooledPassRate: rate,
      wilsonLB: lb,
      gate,
    }
  }
  if (rate >= gate && pooledGames >= minGames) {
    return {
      verdict: 'graduate',
      reason: `pooled ${pooledPassed}/${pooledGames} = ${(rate * 100).toFixed(1)}% ≥ ${gate * 100}%`,
      pooledPassRate: rate,
      wilsonLB: lb,
      gate,
    }
  }
  return {
    verdict: 'stay',
    reason:
      rate < gate
        ? `pooled ${(rate * 100).toFixed(1)}% < ${(gate * 100).toFixed(0)}%（Wilson LB ${(lb * 100).toFixed(1)}%）`
        : `样本不足（${pooledGames} < ${minGames}，双轮各 200）`,
    pooledPassRate: rate,
    wilsonLB: lb,
    gate,
  }
}

/** 跑一轮 eval-course-ckpt（多 weight 同 seed 配对），返回 rows。 */
export function runRound(
  level: string,
  weights: Array<{ label: string; path: string }>,
  seed0: number,
  games: number,
  workers: number,
): GateRow[] {
  const args = [
    'tools/sim/eval-course-ckpt.ts',
    '--course',
    join(NN_ROOT, 'curricula', `${level}.jsonc`),
    '--games',
    String(games),
    '--seed0',
    String(seed0),
    '--workers',
    String(workers),
  ]
  for (const w of weights) args.push('--weights', `${w.label}=${w.path}`)
  const proc = Bun.spawnSync(['bun', ...args], { cwd: NN_ROOT, stdout: 'pipe', stderr: 'pipe' })
  if (proc.exitCode !== 0) {
    throw new Error(
      `eval-course-ckpt failed: ${new TextDecoder().decode(proc.stderr).slice(0, 500)}`,
    )
  }
  const lines = new TextDecoder()
    .decode(proc.stdout)
    .split('\n')
    .filter((l) => l.startsWith('{'))
  return lines.map((l) => JSON.parse(l) as GateRow)
}

// ---- LEDGER（I5）：原子读写的统一 identity 台账 ----

export function loadLedger(path = LEDGER_PATH): Record<string, unknown> {
  if (!existsSync(path)) return {}
  return JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>
}

export function saveLedger(data: unknown, path = LEDGER_PATH): void {
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.tmp`
  writeFileSync(tmp, JSON.stringify(data, null, 2) + '\n')
  renameSync(tmp, path) // 原子替换：崩溃不留半截台账
}

/** 把 gate 结果并进 LEDGER 的对应 level 条目（无则创建）。 */
export function upsertLedgerEntry(
  level: string,
  patch: Record<string, unknown>,
  ledgerPath = LEDGER_PATH,
): void {
  const ledger = loadLedger(ledgerPath)
  const levels = (ledger.levels as Record<string, unknown>) ?? {}
  const entry = (levels[level] as Record<string, unknown>) ?? {}
  levels[level] = { ...entry, ...patch }
  ledger.levels = levels
  saveLedger(ledger, ledgerPath)
}

export async function main(): Promise<void> {
  const arg = (n: string): string | undefined => {
    const i = process.argv.indexOf(`--${n}`)
    return i > 0 ? process.argv[i + 1] : undefined
  }
  const all = (n: string): Array<{ label: string; path: string }> => {
    const out: Array<{ label: string; path: string }> = []
    const list = process.argv
    for (let i = 0; i < list.length; i++) {
      if (list[i] === `--${n}`) {
        const [label, path] = (list[i + 1] ?? '').split('=')
        if (label && path) out.push({ label, path })
      }
    }
    return out
  }

  const level = arg('level')
  const weights = all('weight')
  const outDir = arg('out')
  const games = parseInt(arg('games') ?? '200', 10)
  const workers = parseInt(arg('workers') ?? '6', 10)
  const recalls = all('recall')
  if (!level || weights.length === 0 || !outDir) {
    console.error(
      '[ladder-gate] --level <name> --weight label=path (repeatable) --out <dir> [--recall level=path] [--games 200]',
    )
    process.exit(2)
  }

  mkdirSync(outDir, { recursive: true })
  const rounds: Record<string, { seed0: number; games: number; stats: Record<string, RoundStat> }> =
    {}
  const perLabelRows: Record<string, GateRow[]> = {}
  for (const [ri, seed0] of [0, 200].entries()) {
    const rows = runRound(level, weights, seed0, games, workers)
    mkdirSync(dirname(join(outDir, `round${ri + 1}.jsonl`)), { recursive: true })
    writeFileSync(
      join(outDir, `round${ri + 1}.jsonl`),
      rows.map((r) => JSON.stringify(r)).join('\n') + '\n',
    )
    for (const w of weights) {
      perLabelRows[w.label] = (perLabelRows[w.label] ?? []).concat(
        rows.filter((r) => r.label === w.label),
      )
    }
    rounds[`round${ri + 1}`] = {
      seed0,
      games,
      stats: Object.fromEntries(
        weights.map((w) => [w.label, roundStat(rows.filter((r) => r.label === w.label))]),
      ),
    }
  }

  // 候选腿（第一个 --weight）= 毕业判定对象；其余腿与它做同种子 McNemar。
  const candidate = weights[0].label
  const pooledRows = perLabelRows[candidate] ?? []
  const stat = roundStat(pooledRows)
  const verdict = decideVerdict(stat.passed, stat.games, stat.timeoutFrac)

  const mcnemar: Record<string, LegPair | null> = {}
  for (const w of weights.slice(1)) {
    mcnemar[w.label] = mcnemarPaired(perLabelRows[candidate] ?? [], perLabelRows[w.label] ?? [])
  }

  // 跨级召回（B8）：前 1-2 级各 50 局，灾难遗忘哨兵。
  const recallStats: Record<string, { passRate: number; games: number }> = {}
  for (const r of recalls) {
    const lvl = r.label
    const rows = runRound(lvl, [{ label: 'recall', path: r.path }], 400, 50, workers)
    const s = roundStat(rows)
    recallStats[lvl] = { passRate: s.passRate, games: s.games }
  }

  const report = {
    format: 'ladder-gate-report',
    version: 1,
    level,
    candidate,
    date: new Date().toISOString(),
    verdict,
    rounds,
    mcnemar,
    recallStats,
    notes: {
      bonusKills: 'pending worker telemetry extension (M1)',
      firstKillTick: 'pending worker telemetry extension (M3)',
    },
  }
  writeFileSync(join(outDir, 'report.json'), JSON.stringify(report, null, 2) + '\n')

  upsertLedgerEntry(level, {
    lastGate: {
      date: report.date,
      verdict: verdict.verdict,
      pooledPassRate: verdict.pooledPassRate,
      wilsonLB: verdict.wilsonLB,
      report: join(outDir, 'report.json'),
    },
  })
  console.log(
    `[ladder-gate] ${level} ${candidate}: ${verdict.verdict.toUpperCase()} — ${verdict.reason} ` +
      `(timeout ${(stat.timeoutFrac * 100).toFixed(1)}%, ticks p50 ${stat.ticksP50}/p90 ${stat.ticksP90}, dmg/kill ${stat.dmgPerKill ?? 'n/a'})`,
  )
}

if (import.meta.main) void main()
