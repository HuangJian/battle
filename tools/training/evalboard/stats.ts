/** stats.ts — EvalBoard 统计口径（plan/rl-eval-system.md §5）。
 *
 * 数值口径唯一事实来源是 §2.4：MDD 是**函数不是常量**，必须按实测 flips 重算，
 * 禁止查表。配对 = McNemar 单向变化对数；非配对 = 两样本比例。
 * 只有 T1/T2 参与判定；T3 参与诊断；T4 参与告警（§5.1）。
 */

import { playerProfile, profileToStats } from '../../../src/config/combat'
import type { EvalGameRow } from './store'

// ────────────────────────── §2.4 MDD ──────────────────────────

/**
 * 配对 MDD（§2.4）：`1.96 × sqrt(flips) / n`，flips = b + c（McNemar 单向变化对数）。
 * 返回比例（如 0.062 = 6.2pp）。n<=0 或 flips<0 返回 NaN。
 */
export function mddPaired(n: number, flips: number): number {
  if (!(n > 0) || !(flips >= 0)) return NaN
  return (1.96 * Math.sqrt(flips)) / n
}

/**
 * 非配对 MDD（§2.4）：`1.96 × sqrt(p1(1−p1)/n1 + p2(1−p2)/n2)`。
 * 跨窗/跨种子空间比较用此口径（跨种子空间实际被 §3.5 禁止，这里只服务跨窗）。
 */
export function mddUnpaired(n1: number, p1: number, n2: number, p2: number): number {
  if (!(n1 > 0) || !(n2 > 0)) return NaN
  return 1.96 * Math.sqrt((p1 * (1 - p1)) / n1 + (p2 * (1 - p2)) / n2)
}

// ────────────────────────── §5.1 派生指标 ──────────────────────────

export interface TierMetrics {
  n: number
  /** T1 */ winRate: number
  /** T1 */ clearRate: number
  /** T1 */ killCompletion: number
  /** T2：wins=0 ⇒ +∞（§4.4，代价门自动不过，S1 按 +∞ 触发）。 */
  lifePrice: number
  /** T2：空集 ⇒ null（N/A，不进哨兵判定，§4.4）。 */
  winDmgMedian: number | null
  /** T2：空集 ⇒ null。 */
  winTickMedian: number | null
  /** T2 */ deathRate: number
  /** T2 */ timeoutRate: number
  /** T3 */ accuracy: number
  /** T3 */ shotsPerGame: number
  /** T3 */ firstKillTickMedian: number | null
  /** T3 */ stuckP95: number
  /** T3 */ cellsVisitedMean: number
  // ── R2 二级指标（plan/evalboard-console-ux.md §4-R2）；只新增，不改既有字段语义 ──
  /** 局均击杀 `Σkills / n`。 */
  meanKills: number
  /** 局均拾取道具 `ΣpowerUpsCollected / n`（schema 已有字段，首次被消费）。 */
  meanPowerUps: number
  /** 胜局平均耗时 ticks（与 winTickMedian 并存，不改后者）。 */
  winTickMean: number | null
  /**
   * 胜局平均剩余 HP = `mean(maxHp(playerLevel) − playerDamageTaken | win)`。
   * `playerDamageTaken` 是**非致命扣血累计**（`tools/sim/export-eval-game.ts`），
   * 跨复活累计 ⇒ 多命局可能为负（信息性读数，UI 标筛查级）。空集 ⇒ null。
   */
  winHpLeftMean: number | null
}

/** 星位 → 玩家满额 HP（`src/config/combat.ts` playerProfile × PLAYER_HP_MULT：L0 = 263）。 */
function maxHpOfPlayerLevel(level: number): number {
  return profileToStats(playerProfile(level), 'player', level).maxHp
}

function median(xs: number[]): number | null {
  if (xs.length === 0) return null
  const s = [...xs].sort((a, b) => a - b)
  const m = Math.floor(s.length / 2)
  return s.length % 2 === 1 ? s[m] : (s[m - 1] + s[m]) / 2
}

function p95(xs: number[]): number {
  if (xs.length === 0) return 0
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.min(s.length - 1, Math.ceil(0.95 * s.length) - 1)]
}

/** 同 outcome 家族判定（与 export-eval-game.ts 枚举对齐）。
 *  注：2026-09-12 起上游 `win` 口径 = `stage_clear ∪ cleared`（单关训练场景二者等价），
 *  但本函数按**原始 outcome 值**分类（timeout / gameover），不受该口径变更影响。 */
export function isTimeoutOutcome(outcome: string): boolean {
  return outcome === 'max_ticks' || outcome === 'timeout'
}

export function isDeathOutcome(outcome: string): boolean {
  return outcome === 'gameover'
}

/** 一组逐局行 → T1/T2/T3 派生指标（纯函数）。 */
export function deriveMetrics(rows: EvalGameRow[]): TierMetrics {
  const n = rows.length
  if (n === 0) {
    return {
      n: 0,
      winRate: 0,
      clearRate: 0,
      killCompletion: 0,
      lifePrice: Infinity,
      winDmgMedian: null,
      winTickMedian: null,
      deathRate: 0,
      timeoutRate: 0,
      accuracy: 0,
      shotsPerGame: 0,
      firstKillTickMedian: null,
      stuckP95: 0,
      cellsVisitedMean: 0,
      meanKills: 0,
      meanPowerUps: 0,
      winTickMean: null,
      winHpLeftMean: null,
    }
  }
  const wins = rows.filter((r) => r.win)
  const winCount = wins.length
  const kills = rows.reduce((s, r) => s + r.kills, 0)
  const enemyTotal = rows.reduce((s, r) => s + r.enemyTotal, 0)
  const playerHits = rows.reduce((s, r) => s + r.playerHits, 0)
  const playerShots = rows.reduce((s, r) => s + r.playerShots, 0)
  const enemyHits = rows.reduce((s, r) => s + r.enemyHits, 0)
  const stuck = rows.map((r) => r.stuckTicks)
  const cells = rows.map((r) => r.cellsVisited)
  return {
    n,
    winRate: rows.filter((r) => r.win).length / n,
    clearRate: rows.filter((r) => r.cleared).length / n,
    killCompletion: enemyTotal > 0 ? kills / enemyTotal : 0,
    lifePrice: winCount > 0 ? playerHits / winCount : Infinity,
    winDmgMedian: median(wins.map((r) => r.playerDamageTaken)),
    winTickMedian: median(wins.map((r) => r.ticks)),
    deathRate: rows.filter((r) => isDeathOutcome(r.outcome)).length / n,
    // 「清场后 BONUS TIME 未走完而被 max_ticks 截断」的局不算超时（2026-09-12 口径统一）：
    // 它们已被计为胜（win = stage_clear ∪ cleared），若同时计进 timeoutRate 会自相矛盾，
    // 也可能误触发以 timeout_frac 为判据的熔断。
    timeoutRate: rows.filter((r) => !r.cleared && isTimeoutOutcome(r.outcome)).length / n,
    accuracy: playerShots > 0 ? enemyHits / playerShots : 0,
    shotsPerGame: playerShots / n,
    firstKillTickMedian: median(
      rows.map((r) => r.firstKillTick).filter((v): v is number => v !== null),
    ),
    stuckP95: p95(stuck),
    cellsVisitedMean: cells.reduce((s, v) => s + v, 0) / n,
    meanKills: kills / n,
    meanPowerUps: rows.reduce((s, r) => s + r.powerUpsCollected, 0) / n,
    winTickMean: winCount > 0 ? wins.reduce((s, r) => s + r.ticks, 0) / winCount : null,
    winHpLeftMean:
      winCount > 0
        ? wins.reduce((s, r) => s + maxHpOfPlayerLevel(r.playerLevel) - r.playerDamageTaken, 0) /
          winCount
        : null,
  }
}

// ────────────────────────── 配对翻转率 ──────────────────────────

/**
 * 配对翻转率（S6 输入）：同 seed 两组胜负向量 → flips=b+c 与 flipRate=flips/n。
 * `a`/`b` 按 seed 对齐（Map<seed, win>）。
 */
export function flipRate(
  a: Map<number, boolean>,
  b: Map<number, boolean>,
): { flips: number; n: number; rate: number; delta: number } {
  let flips = 0
  let n = 0
  let winA = 0
  let winB = 0
  for (const [seed, wa] of a) {
    const wb = b.get(seed)
    if (wb === undefined) continue
    n++
    if (wa) winA++
    if (wb) winB++
    if (wa !== wb) flips++
  }
  return { flips, n, rate: n > 0 ? flips / n : 0, delta: n > 0 ? (winB - winA) / n : 0 }
}

// ────────────────────────── §5.4 窗聚合 ──────────────────────────

export interface WindowAgg {
  rung: string
  /** 窗内批数（满窗=4；不足标 partial，不判过门，§4.3）。 */
  batches: number
  partial: boolean
  n: number
  segments: number[]
  metrics: TierMetrics
  /** 按 ckpt_sha16 分桶留痕（供事后归因，不做选择，§4.3）。 */
  byCkpt: Record<string, { n: number; winRate: number }>
  latest: TierMetrics | null
}

/**
 * 窗聚合（§5.4）：同一 rung 连续 4 批（段互不相交）= 400 局，窗是判定主体。
 * 调用方保证传入的已是"该 rung 最近连续批"的行；这里只做聚合 + partial 标记。
 * 窗结论不属于任何一个 ckpt（§4.3）：网页报 latest + 窗均值，无 best 列。
 */
export function windowAgg(rung: string, batches: EvalGameRow[][]): WindowAgg {
  const rows = batches.flat()
  const segments = batches
    .map((b) => b[0]?.seed_segment)
    .filter((s): s is number => typeof s === 'number')
  const metrics = deriveMetrics(rows)
  const byCkpt: WindowAgg['byCkpt'] = {}
  for (const r of rows) {
    const e = (byCkpt[r.ckpt_sha16] ??= { n: 0, winRate: 0 })
    e.n++
    e.winRate += r.win ? 1 : 0
  }
  for (const k of Object.keys(byCkpt)) byCkpt[k].winRate /= byCkpt[k].n
  const last = batches[batches.length - 1] ?? []
  return {
    rung,
    batches: batches.length,
    partial: batches.length < 4,
    n: rows.length,
    segments,
    metrics,
    byCkpt,
    latest: last.length > 0 ? deriveMetrics(last) : null,
  }
}
