/** iters.ts — 每轮训练指标聚合 + eval 汇总（由 monitor/iters.ts 迁入，§3.4 #5；
 *  类型移居 ui/view.ts 单一源，本层只留实现）。api.ts 与 pool 共用。 */

import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'fs'
import { join } from 'path'
import type { EvalSummary, IterActuals, IterRow } from '../ui/view'

// ---------------- 每轮实际值（it{N}/**/manifest.json 聚合） ----------------

/** 聚合某一轮的真实终局值：递归扫 it{N} 下全部 shard 的 manifest.json。
 *
 * - 按 (stage,seed) 去重：fan-out 竞速副本会重复写盘，取 nSamples 最大者。
 * - 跳过 local-eval：干净评估局不是 rollout 局。
 * - 无任何 manifest / it 目录不存在（已被轮转删除）→ null。 */
/** 玩家满额 HP（pool 模型 L0）：armor 50 × PLAYER_HP_MULT 1.05 × HP_SCALE 5 = 263。
 *  hard/chaos/relax 走 DEFAULT_RULES（combatModel=pool）；classic instant 为 100，
 *  训练课程几乎全是 hard → 统一 263（与 c4-dodge「剩 119/263」口径一致）。 */
const PLAYER_MAX_HP = Math.round(50 * 1.05 * 5) // 263
/** 训练课程常见 startLives（curricula 普遍 player.lives=1；难度表默认 3 但课程覆盖）。 */
const ASSUMED_START_LIVES = 1

/**
 * 胜局残血（hp）：从已有字段推算，不改 export 写端。
 *   (startLives + puGotTank - playerDeaths) × maxHp − playerDamageTaken
 * - startLives 课程侧未落盘 → 取训练默认 1；puGotTank 仅 eval 行有（RL manifest 缺 → 0）
 * - playerDeaths 仅 eval 行有（RL 缺 → 0）；playerDamageTaken 为非致命扣血累计
 * - 仅胜局（stage_clear）计入；缺 playerDamageTaken 返回 null
 */
function residualHpFromFields(m: {
  outcome?: unknown
  playerDamageTaken?: number
  playerDeaths?: number
  puGotTank?: number
}): number | null {
  const outcome = String(m.outcome ?? '')
  if (outcome && outcome !== 'stage_clear') return null
  if (typeof m.playerDamageTaken !== 'number' || !Number.isFinite(m.playerDamageTaken)) {
    return null
  }
  const deaths = typeof m.playerDeaths === 'number' ? m.playerDeaths : 0
  const tank = typeof m.puGotTank === 'number' ? m.puGotTank : 0
  const capacity = (ASSUMED_START_LIVES + tank - deaths) * PLAYER_MAX_HP
  return Math.max(0, Math.round(capacity - m.playerDamageTaken))
}

export function readIterActuals(trajDir: string, iter: number): IterActuals | null {
  const itDir = join(trajDir, `it${iter}`)
  try {
    if (!existsSync(itDir)) return null
    const best = new Map<
      string,
      { nSamples: number; kills: number; pu: number; ticks: number; residualHp: number | null }
    >()
    const walk = (base: string, rel: string): void => {
      if (rel.split('/').length > 6) return
      let names: string[]
      try {
        names = readdirSync(join(base, rel), { withFileTypes: true }).map((d) => d.name)
      } catch {
        return
      }
      for (const name of names) {
        if (name === 'local-eval') continue
        const childRel = rel ? `${rel}/${name}` : name
        const p = join(base, childRel)
        let isDir = false
        try {
          isDir = statSync(p).isDirectory()
        } catch {
          continue
        }
        if (isDir) {
          walk(base, childRel)
          continue
        }
        if (name !== 'manifest.json') continue
        try {
          const m = JSON.parse(readFileSync(p, 'utf8')) as {
            stage?: unknown
            seed?: unknown
            kills?: number
            powerUpsCollected?: number
            ticks?: number
            nSamples?: number
            outcome?: string
            playerDamageTaken?: number
            playerDeaths?: number
            puGotTank?: number
          }
          const stage = Number(m.stage)
          const seed = Number(m.seed)
          if (!Number.isFinite(stage) || !Number.isFinite(seed)) continue
          const nSamples = Number(m.nSamples ?? 0)
          const prev = best.get(`${stage}:${seed}`)
          if (!prev || nSamples > prev.nSamples) {
            best.set(`${stage}:${seed}`, {
              nSamples,
              kills: Number(m.kills ?? 0) || 0,
              pu: Number(m.powerUpsCollected ?? 0) || 0,
              ticks: Number(m.ticks ?? 0) || 0,
              residualHp: residualHpFromFields(m),
            })
          }
        } catch {
          /* skip bad manifest */
        }
      }
    }
    walk(itDir, '')
    if (best.size === 0) return null
    let totalKills = 0
    let totalPU = 0
    let totalTicks = 0
    let residualSum = 0
    let residualN = 0
    for (const v of best.values()) {
      totalKills += v.kills
      totalPU += v.pu
      totalTicks += v.ticks
      if (v.residualHp !== null) {
        residualSum += v.residualHp
        residualN++
      }
    }
    return {
      games: best.size,
      totalKills,
      totalPU,
      avgTicks: Math.round(totalTicks / best.size),
      avgResidualHp: residualN > 0 ? Math.round(residualSum / residualN) : null,
    }
  } catch {
    return null
  }
}

// ---------------- 实际值留底缓存（计算一次永久使用） ----------------
// keep_iters 一到，trainer 的 _rotate_cleanup 删旧 it{N} 目录；且 manifest 聚合是
// 递归目录扫描——每刷新都重算既慢又不必要。实际值是终局值、聚合一次不再变化。

export interface CachedActuals extends IterActuals {
  /** 该轮 iteration 事件的 time（防串门闩：同 iter 号不同轮 → 不采用缓存）。 */
  time: string
}

function actualsCachePath(trajDir: string): string {
  return join(trajDir, '.pool-actuals-cache.json')
}

function loadActualsCache(trajDir: string): Map<number, CachedActuals> {
  const out = new Map<number, CachedActuals>()
  try {
    const raw = JSON.parse(readFileSync(actualsCachePath(trajDir), 'utf8')) as Record<
      string,
      CachedActuals
    >
    for (const [k, v] of Object.entries(raw)) {
      const it = Number(k)
      if (
        Number.isInteger(it) &&
        it >= 0 &&
        v &&
        typeof v.time === 'string' &&
        typeof v.games === 'number' &&
        typeof v.totalKills === 'number' &&
        typeof v.totalPU === 'number' &&
        typeof v.avgTicks === 'number'
      ) {
        out.set(it, v)
      }
    }
  } catch {
    /* 缺失/损坏 → 空缓存重建 */
  }
  return out
}

function saveActualsCache(trajDir: string, cache: Map<number, CachedActuals>): void {
  try {
    // 只留最近 500 轮，防无限增长。
    const obj: Record<string, CachedActuals> = {}
    for (const k of [...cache.keys()].sort((a, b) => b - a).slice(0, 500)) {
      const v = cache.get(k)
      if (v) obj[String(k)] = v
    }
    writeFileSync(actualsCachePath(trajDir), JSON.stringify(obj))
  } catch {
    /* 写失败不致命——下次渲染带新数据重试 */
  }
}

// ---------------- 干净评估汇总（eval_log.jsonl） ----------------

/** 读取 eval_log.jsonl：eval_summary 按 iter 归并（同 iter 多条 = 断点续跑补评估
 *  后的重复落账，取最后一条 = 最新对账结果），并聚合该 (iter,wver) 的 event=eval
 *  逐局行。
 *
 *  it 序数语义：eval_summary.iter = N 评估的是第 N 轮 PPO 更新前的权重——即第 N 轮
 *  rollout 采样所用的同一权重。summary 可能晚到——按 iter 字段匹配，绝不按时间邻近。 */
export function readEvalSummaries(trajDir: string): Map<number, EvalSummary> {
  const out = new Map<number, EvalSummary>()
  const logPath = join(trajDir, 'eval_log.jsonl')
  interface GameAgg {
    n: number
    ticks: number
    kills: number
    pu: number
    scoreSum: number
    scoreSqSum: number
    residualSum: number
    residualN: number
    /** 胜局累计耗时（ticks）与胜局数：「胜局耗时」口径（avgWinTicks）。 */
    winTicks: number
    winN: number
  }
  const games = new Map<number, Map<string, GameAgg>>()
  try {
    if (!existsSync(logPath)) return out
    for (const line of readFileSync(logPath, 'utf8').split(String.fromCharCode(10))) {
      if (!line.trim()) continue
      try {
        const r = JSON.parse(line) as Record<string, unknown>
        const iter = Number(r.iter ?? -1)
        if (!Number.isInteger(iter) || iter < 0) continue
        if (r.event === 'eval') {
          const wver = String(r.wver ?? '')
          if (!wver) continue
          let byWver = games.get(iter)
          if (!byWver) {
            byWver = new Map()
            games.set(iter, byWver)
          }
          let agg = byWver.get(wver)
          if (!agg) {
            agg = {
              n: 0,
              ticks: 0,
              kills: 0,
              pu: 0,
              scoreSum: 0,
              scoreSqSum: 0,
              residualSum: 0,
              residualN: 0,
              winTicks: 0,
              winN: 0,
            }
            byWver.set(wver, agg)
          }
          const score = Number(r.score ?? 0)
          agg.n++
          agg.ticks += Number(r.ticks ?? 0) || 0
          agg.kills += Number(r.kills ?? 0) || 0
          agg.pu += Number(r.powerUpsCollected ?? 0) || 0
          agg.scoreSum += score
          agg.scoreSqSum += score * score
          // 残血：仅胜局；(startLives + puGotTank − deaths) × maxHp − playerDamageTaken
          const won = r.win === true || r.win === 1
          if (won) {
            // 胜局耗时：仅胜局累计 ticks（胜局缺 ticks/0 = 数据缺口，不计入分母）。
            const wt = Number(r.ticks ?? 0) || 0
            if (wt > 0) {
              agg.winTicks += wt
              agg.winN++
            }
            const rh = residualHpFromFields({
              outcome: 'stage_clear',
              playerDamageTaken:
                typeof r.playerDamageTaken === 'number' ? r.playerDamageTaken : undefined,
              playerDeaths: typeof r.playerDeaths === 'number' ? r.playerDeaths : undefined,
              puGotTank: typeof r.puGotTank === 'number' ? r.puGotTank : undefined,
            })
            if (rh !== null) {
              agg.residualSum += rh
              agg.residualN++
            }
          }
          continue
        }
        if (r.event !== 'eval_summary') continue
        out.set(iter, {
          time: String(r.time ?? ''),
          games: Number(r.games ?? 0),
          wins: Number(r.wins ?? 0),
          winRate: typeof r.winRate === 'number' ? r.winRate : null,
          clears: Number(r.clears ?? 0),
          clearRate: typeof r.clearRate === 'number' ? r.clearRate : null,
          dropped: Number(r.dropped ?? 0),
          sec: Number(r.sec ?? 0),
          wver: String(r.wver ?? ''),
          outcomes: (r.outcomes ?? {}) as Record<string, number>,
          avgTicks: null,
          avgWinTicks: null,
          totalKills: null,
          totalPU: null,
          avgResidualHp: null,
          scoreMean: null,
          scoreStd: null,
        })
      } catch {
        /* skip bad line */
      }
    }
    // 合并：每个 summary 只配它自己 wver 的逐局桶。总体 std（÷n）。
    for (const [iter, s] of out) {
      const agg = games.get(iter)?.get(s.wver)
      if (!agg || agg.n === 0) continue
      const mean = agg.scoreSum / agg.n
      s.avgTicks = Math.round(agg.ticks / agg.n)
      s.avgWinTicks = agg.winN > 0 ? Math.round(agg.winTicks / agg.winN) : null
      s.totalKills = agg.kills
      s.totalPU = agg.pu
      s.avgResidualHp = agg.residualN > 0 ? Math.round(agg.residualSum / agg.residualN) : null
      s.scoreMean = +mean.toFixed(4)
      s.scoreStd = +Math.sqrt(Math.max(0, agg.scoreSqSum / agg.n - mean * mean)).toFixed(4)
    }
  } catch {
    /* unreadable */
  }
  return out
}

/** 从 training_log.jsonl 读取最近 MAX_ITER_ROWS 轮迭代指标（实际值缓存优先：
 *  manifest 聚合只做一次，落 .pool-actuals-cache.json；time 门闩匹配才命中）。 */
export function readIterMetrics(trajDir: string): { rows: IterRow[] } {
  // §361②：完整指标表要显示所有 iter，不再截断到 20。500 上限只防病态日志
  // （单行重复写/双 trainer 事故的千轮级日志）撑爆 /api/state payload。
  const MAX = 500
  const logPath = join(trajDir, 'training_log.jsonl')
  // eval 汇总整册读一次（按 iter 键控），逐行查表——不在循环里反复开文件。
  const evalSummaries = readEvalSummaries(trajDir)
  const actualsCache = loadActualsCache(trajDir)
  let cacheDirty = false
  try {
    if (!existsSync(logPath)) return { rows: [] }
    const lines = readFileSync(logPath, 'utf8').split(String.fromCharCode(10))
    const rows: IterRow[] = []
    for (const line of lines) {
      if (!line.trim()) continue
      try {
        const r = JSON.parse(line) as Record<string, unknown>
        if (r.event !== 'iteration') continue
        const dm = (r.dim_means ?? {}) as Record<string, number>
        const topDims = Object.entries(dm)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3)
          .map(([k, v]) => `${k}:${(v * 100).toFixed(0)}%`)
          .join(' ')
        const iter = Number(r.iter ?? 0)
        const rowTime = String(r.time ?? '')
        let actuals: IterActuals | null = null
        const cached = actualsCache.get(iter)
        if (cached && cached.time === rowTime) {
          actuals = {
            games: cached.games,
            totalKills: cached.totalKills,
            totalPU: cached.totalPU,
            avgTicks: cached.avgTicks,
            avgResidualHp:
              (cached as CachedActuals & { avgResidualHp?: number | null }).avgResidualHp ?? null,
          }
        } else {
          actuals = readIterActuals(trajDir, iter)
          if (actuals) {
            actualsCache.set(iter, { ...actuals, time: rowTime })
            cacheDirty = true
          }
        }
        rows.push({
          iter,
          time: rowTime,
          winRate: Number(r.winRate ?? 0),
          scoreMean: Number(r.score_mean ?? 0),
          scoreStd: Number(r.score_std ?? 0),
          samples: Number(r.samples ?? 0),
          rolloutSec: Number(r.rollout_sec ?? 0),
          ppoSec: Number(r.ppo_sec ?? 0),
          kl: Number(r.kl ?? 0),
          entropy: Number(r.entropy ?? 0),
          policyLoss: Number(r.policy ?? 0),
          valueLoss: Number(r.value ?? 0),
          meanRet: Number(r.mean_ret ?? 0),
          lr: Number(r.lr ?? 0),
          expectedGames: Number(r.expectedGames ?? 0),
          halted: Boolean(r.halted),
          topDims,
          avgTicks:
            Number(r.expectedGames) > 0
              ? Math.round(Number(r.ticks ?? 0) / Number(r.expectedGames))
              : 0,
          accuracy: Number(dm.accuracy ?? 0),
          loot: Number(dm.loot ?? 0),
          kills: +(Number(dm.progress ?? 0) * 20).toFixed(2),
          actuals,
          evalData: evalSummaries.get(iter) ?? null,
        })
      } catch {
        /* skip bad line */
      }
    }
    // 同 iter 去重：取最后一条 = 最新对账结果（双 trainer 并行事故的口径）。
    const byIter = new Map<number, IterRow>()
    for (const r of rows) byIter.set(r.iter, r)
    const merged = [...byIter.values()]
    merged.sort((a, b) => b.iter - a.iter)
    if (cacheDirty) saveActualsCache(trajDir, actualsCache)
    return { rows: merged.slice(0, MAX) }
  } catch {
    return { rows: [] }
  }
}
