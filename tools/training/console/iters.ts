/** iters.ts — 每轮训练指标聚合 + eval 汇总（由 monitor/iters.ts 迁入，§3.4 #5；
 *  类型移居 ui/view.ts 单一源，本层只留实现）。api.ts 与 pool 共用。 */

import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'fs'
import { join } from 'path'
import type { EvalSummary, IterActuals, IterRow, PairedCompare, PairedReferee } from '../ui/view'
import { mcnemarP, pairedVerdict } from '../../eval/mcnemar'

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
      {
        nSamples: number
        kills: number
        pu: number
        ticks: number
        residualHp: number | null
        /** outcome（stage_clear=胜局；null=manifest 未落盘）。 */
        outcome: string | null
        /** playerDamageTaken（全样本承伤，null=字段缺失 → 不计入承伤/杀）。 */
        dmgTaken: number | null
      }
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
              outcome: typeof m.outcome === 'string' && m.outcome.length > 0 ? m.outcome : null,
              dmgTaken:
                typeof m.playerDamageTaken === 'number' && Number.isFinite(m.playerDamageTaken)
                  ? m.playerDamageTaken
                  : null,
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
    // 胜局/败局耗时（ticks）与 承伤/杀（全样本，分子分母同口径）
    let winTickSum = 0
    let winN = 0
    let lossTickSum = 0
    let lossN = 0
    let dmgSum = 0
    let dmgKills = 0
    let dmgN = 0
    for (const v of best.values()) {
      totalKills += v.kills
      totalPU += v.pu
      totalTicks += v.ticks
      if (v.residualHp !== null) {
        residualSum += v.residualHp
        residualN++
      }
      if (v.outcome) {
        if (v.outcome === 'stage_clear' && v.ticks > 0) {
          winTickSum += v.ticks
          winN++
        } else if (v.outcome !== 'stage_clear' && v.ticks > 0) {
          lossTickSum += v.ticks
          lossN++
        }
      }
      // 承伤/杀：全样本、不区分胜负；分子分母同口径 = 仅累计带有 playerDamageTaken 的局。
      if (v.dmgTaken !== null) {
        dmgSum += v.dmgTaken
        dmgKills += v.kills
        dmgN++
      }
    }
    return {
      games: best.size,
      totalKills,
      totalPU,
      avgTicks: Math.round(totalTicks / best.size),
      avgResidualHp: residualN > 0 ? Math.round(residualSum / residualN) : null,
      avgWinTicks: winN > 0 ? Math.round(winTickSum / winN) : null,
      avgLossTicks: lossN > 0 ? Math.round(lossTickSum / lossN) : null,
      dmgPerKill: dmgN > 0 && dmgKills > 0 ? +(dmgSum / dmgKills).toFixed(1) : null,
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
        typeof v.avgTicks === 'number' &&
        // schema 版本门闩：缺 avgWinTicks（旧缓存）→ 作废重建，带出新增 rollout 胜局/败局/承伤字段。
        'avgWinTicks' in v
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
    /** 败局累计耗时（ticks）与败局数：「败局耗时」口径（avgLossTicks）。
     *  ⚠️ 该值高 = 清场停滞，不是"更会活"（见 EvalSummary.avgLossTicks 方向警告）。 */
    lossTicks: number
    lossN: number
    /** 全样本累计承伤（dmgPerKill 的分子）。 */
    dmg: number
    /** dmgPerKill 的**配套分母**（仅累计"行内有 playerDamageTaken"的那些局的 kills）与
     *  有效局数。分子分母必须同口径——否则老课程（eval_log 无该字段）会退化成假 0，
     *  或"分子只覆盖部分局、分母覆盖全部局"造成系统性低估。 */
    dmgKills: number
    dmgN: number
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
              lossTicks: 0,
              lossN: 0,
              dmg: 0,
              dmgKills: 0,
              dmgN: 0,
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
          // 承伤（dmgPerKill 分子）：全样本累计，不区分胜负；**分子分母同口径**——
          // 只有行内真有 playerDamageTaken 时才把该局计入（同时累计该局 kills），
          // 否则老课程 eval_log 缺字段会退化成假 0、或分子分母覆盖范围不一致而低估。
          const pd = Number(r.playerDamageTaken)
          if (Number.isFinite(pd)) {
            agg.dmg += pd
            agg.dmgKills += Number(r.kills ?? 0) || 0
            agg.dmgN++
          }
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
          } else {
            // 败局耗时：仅败局累计 ticks（败局缺 ticks/0 = 数据缺口，不计入分母）。
            const lt = Number(r.ticks ?? 0) || 0
            if (lt > 0) {
              agg.lossTicks += lt
              agg.lossN++
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
          avgLossTicks: null,
          dmgPerKill: null,
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
      s.avgLossTicks = agg.lossN > 0 ? Math.round(agg.lossTicks / agg.lossN) : null
      // 承伤/杀：全样本口径（Σdmg / Σ同批 kills），保留 1 位小数（够看趋势）。
      // 无任何带 playerDamageTaken 的局（老课程）→ null（显示 -），不是 0。
      s.dmgPerKill = agg.dmgN > 0 && agg.dmgKills > 0 ? +(agg.dmg / agg.dmgKills).toFixed(1) : null
      s.scoreMean = +mean.toFixed(4)
      s.scoreStd = +Math.sqrt(Math.max(0, agg.scoreSqSum / agg.n - mean * mean)).toFixed(4)
    }
  } catch {
    /* unreadable */
  }
  return out
}

/** eval 逐局胜负表：iter → `stage:seed` → win（同键重复落账取最后一条）。
 * 配对裁判与逐轮装配共用这一次扫描。 */
export function readEvalGameWins(trajDir: string): Map<number, Map<string, boolean>> {
  const byIter = new Map<number, Map<string, boolean>>()
  const logPath = join(trajDir, 'eval_log.jsonl')
  try {
    if (!existsSync(logPath)) return byIter
    for (const line of readFileSync(logPath, 'utf8').split(String.fromCharCode(10))) {
      if (!line.trim()) continue
      try {
        const r = JSON.parse(line) as Record<string, unknown>
        if (r.event !== 'eval') continue
        const iter = Number(r.iter ?? -1)
        const stage = Number(r.stage)
        const seed = Number(r.seed)
        if (
          !Number.isInteger(iter) ||
          iter < 0 ||
          !Number.isInteger(stage) ||
          !Number.isInteger(seed)
        ) {
          continue
        }
        let m = byIter.get(iter)
        if (!m) {
          m = new Map()
          byIter.set(iter, m)
        }
        m.set(`${stage}:${seed}`, r.win === true || r.win === 1)
      } catch {
        /* skip bad line */
      }
    }
  } catch {
    /* unreadable */
  }
  return byIter
}

/** 两轮胜负表配对比较（baseIter → ckptIter）：b01/b10 只看不一致对。 */
export function compareSeedMaps(
  ma: Map<string, boolean>,
  mb: Map<string, boolean>,
  baseIter: number,
  ckptIter: number,
): PairedCompare {
  let b01 = 0
  let b10 = 0
  let paired = 0
  for (const [k, wa] of ma) {
    const wb = mb.get(k)
    if (wb === undefined) continue
    paired++
    if (!wa && wb) b01++
    else if (wa && !wb) b10++
  }
  const union = new Set([...ma.keys(), ...mb.keys()])
  return {
    baseIter,
    ckptIter,
    paired,
    unpaired: union.size - paired,
    b01,
    b10,
    deltaPp: paired > 0 ? +((100 * (b01 - b10)) / paired).toFixed(1) : 0,
    p: mcnemarP(b01, b10),
    verdict: pairedVerdict({ b01, b10, b11: 0, b00: 0 }),
  }
}

/** 配对裁判（只读哨子，不进门判）：最新 eval vs 开腿首轮 / vs 上一 eval 轮。
 *
 * 同 (stage,seed) 逐局配对，比较的是同一语料下两个 checkpoint 的贪心胜负——
 * 跨语料（bc 在 0-99 vs 新权重在 860001+）时差分把卷面难度抵消掉。一边缺席的
 * 局只计 unpaired 诚实披露。单轮/无数据 → 对应项 null（UI 空态）。 */
export function readPairedReferee(trajDir: string): PairedReferee | null {
  const byIter = readEvalGameWins(trajDir)
  const iters = [...byIter.keys()].sort((a, b) => a - b)
  if (iters.length === 0) return null
  const latest = iters[iters.length - 1]
  const first = iters[0]
  const get = (it: number): Map<string, boolean> => byIter.get(it) ?? new Map<string, boolean>()
  const vsFirst = first === latest ? null : compareSeedMaps(get(first), get(latest), first, latest)
  const vsPrev =
    iters.length < 2
      ? null
      : compareSeedMaps(get(iters[iters.length - 2]), get(latest), iters[iters.length - 2], latest)
  if (!vsFirst && !vsPrev) return null
  return { vsFirst, vsPrev }
}

/** evalData 挂载逐轮配对（无 evalData 原样返回 null；浅拷贝不污染共享表）。 */
function withPaired(
  s: EvalSummary | undefined,
  pairedVsFirst: PairedCompare | null,
): EvalSummary | null {
  if (!s) return null
  return { ...s, pairedVsFirst }
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
  // 逐轮 vs 开腿配对：同趟扫描的副产品，每轮 evalData 自带（表格配对列的数据源）。
  const evalWins = readEvalGameWins(trajDir)
  const evalIters = [...evalWins.keys()].sort((a, b) => a - b)
  const evalBaseline = evalIters.length > 0 ? evalIters[0] : null
  const pairedByIter = new Map<number, PairedCompare | null>()
  if (evalBaseline !== null) {
    for (const it of evalIters) {
      if (it === evalBaseline) {
        pairedByIter.set(it, null) // 基线本轮：vs自己不判，UI 显示“基线”
        continue
      }
      const c = compareSeedMaps(
        evalWins.get(evalBaseline) ?? new Map<string, boolean>(),
        evalWins.get(it) ?? new Map<string, boolean>(),
        evalBaseline,
        it,
      )
      pairedByIter.set(it, c.paired > 0 ? c : null)
    }
  }
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
            avgWinTicks:
              (cached as CachedActuals & { avgWinTicks?: number | null }).avgWinTicks ?? null,
            avgLossTicks:
              (cached as CachedActuals & { avgLossTicks?: number | null }).avgLossTicks ?? null,
            dmgPerKill:
              (cached as CachedActuals & { dmgPerKill?: number | null }).dmgPerKill ?? null,
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
          evalData: withPaired(evalSummaries.get(iter), pairedByIter.get(iter) ?? null),
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
