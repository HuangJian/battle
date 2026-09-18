/** iters.ts — 每轮训练指标聚合 + eval 汇总（由 monitor/iters.ts 迁入，§3.4 #5；
 *  类型移居 ui/view.ts 单一源，本层只留实现）。api.ts 与 pool 共用。 */

import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'fs'
import { basename, join } from 'path'
import type {
  EvalGameClass,
  EvalGameRow,
  EvalGamesData,
  EvalSummary,
  IterActuals,
  IterRow,
  IterWire,
  PairedCompare,
  PairedReferee,
} from '../web/view'
import { mcnemarP, pairedVerdict } from '../../../tools/eval/mcnemar'
import { STAGES } from '../../../src/config/stages'
import { isArenaId, resolveArenaStage } from '../../../src/nn/arena-ladder'
import { parseJsonc } from '../core/jsonc'
import { curriculaDir, NN_TRAINING } from '../core/paths'

// ---------------- M0 传输账（iteration 事件的 `wire` 子字典） ----------------

/** 数值兜底：数字才收，其余（含 JSON null / 字符串）统一 null。
 *  为什么严格：`wire` 是**对账**口径，把 `"12"` 之类的字符串 "12" 转成 12
 *  会掩盖上游写端 bug（与 ppo_sec 这类展示字段的 Number() 宽松口径不同）。 */
function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function strOrNull(v: unknown): string | null {
  return typeof v === 'string' && v ? v : null
}

/** iteration 行 → 传输账视图；无 `wire` 键（旧账本）= null。
 *
 *  worker 子字典只保留数字项：探针/对账要的是「字节到哪去了」，键集不固定
 *  （M0/M2 先后加过键），故不白名单硬编码 —— 新增键自动可见，UI 逐项列出。 */
export function parseIterWire(raw: unknown): IterWire | null {
  // 数组也是 typeof 'object'：`wire: []` 是写端写坏了，不是「空账」——若放行会渲染成
  // 一整块「—」，看起来像「这轮没量到字节」而不是「这个字段本身坏了」。
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const w = raw as Record<string, unknown>
  const worker: Record<string, number | null> = {}
  const ww = w.worker
  if (ww && typeof ww === 'object') {
    for (const [k, v] of Object.entries(ww as Record<string, unknown>)) {
      const n = numOrNull(v)
      if (n !== null) worker[k] = n
    }
  }
  return {
    upBytes: numOrNull(w.up_bytes),
    upSec: numOrNull(w.up_sec),
    packSec: numOrNull(w.pack_sec),
    downBytes: numOrNull(w.down_bytes),
    downSec: numOrNull(w.down_sec),
    blobsMiss: numOrNull(w.blobs_miss),
    protocol: strOrNull(w.protocol),
    edgeIp: strOrNull(w.edge_ip),
    slim: typeof w.slim === 'boolean' ? w.slim : null,
    rolloutSrc: strOrNull(w.rollout_src),
    worker: Object.keys(worker).length > 0 ? worker : null,
  }
}

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

/** 自定义关 2000+i 的敌数：curricula/<course>.jsonc → level → levels/<level>.jsonc stages[i].count。
 *  按课缓存；失败返回 null（不拖垮聚合）。 */
const courseEnemyByStage = new Map<string, Map<number, number>>()

function loadCourseCustomEnemies(course: string): Map<number, number> {
  const hit = courseEnemyByStage.get(course)
  if (hit) return hit
  const out = new Map<number, number>()
  courseEnemyByStage.set(course, out)
  try {
    const curPath = join(curriculaDir(), `${course}.jsonc`)
    if (!existsSync(curPath)) return out
    const cur = parseJsonc(readFileSync(curPath, 'utf8')) as { level?: string }
    const level = String(cur.level ?? '')
    if (!level) return out
    const lvPath = join(NN_TRAINING, 'levels', `${level}.jsonc`)
    if (!existsSync(lvPath)) return out
    const lv = parseJsonc(readFileSync(lvPath, 'utf8')) as {
      stages?: Array<{ count?: number; forces?: string }>
    }
    const stages = Array.isArray(lv.stages) ? lv.stages : []
    stages.forEach((st, i) => {
      const c = Number(st.count)
      const enemy =
        Number.isFinite(c) && c > 0 ? c : typeof st.forces === 'string' ? st.forces.length : null
      if (enemy != null && enemy > 0) out.set(2000 + i, enemy)
    })
  } catch {
    /* unreadable curriculum → empty map */
  }
  return out
}

/** 关卡敌数：manifest `enemyTotal` 优先；否则 stage id 反查（经典关 / arena / 自定义关）。 */
function resolveEnemyTotal(
  stageId: number,
  fromManifest?: number | null,
  course?: string,
): number | null {
  if (typeof fromManifest === 'number' && Number.isFinite(fromManifest) && fromManifest > 0) {
    return fromManifest
  }
  try {
    if (isArenaId(stageId)) {
      // 自定义关 2000+ 先走课程 level；arena-ladder 的 1000+n 再走 ARENA_LADDER。
      if (stageId >= 2000 && course) {
        const custom = loadCourseCustomEnemies(course).get(stageId)
        if (custom != null) return custom
      }
      const st = resolveArenaStage(stageId)
      if (!st) return null
      return st.enemyCount ?? st.enemies?.length ?? null
    }
    const st = STAGES[stageId]
    if (!st) return null
    return st.enemyCount ?? st.enemies?.length ?? null
  } catch {
    return null
  }
}

/** traj 目录名 = 课程名（tmp/<course>）。 */
function courseFromTrajDir(trajDir: string): string {
  try {
    return basename(trajDir)
  } catch {
    return ''
  }
}

/** 该局可支配生命容量：(startLives + puGotTank − playerDeaths) × maxHp。 */
function lifeCapacity(fields: {
  startLives?: number | null
  puGotTank?: number | null
  playerDeaths?: number | null
}): number {
  const lives =
    typeof fields.startLives === 'number' &&
    Number.isFinite(fields.startLives) &&
    fields.startLives > 0
      ? fields.startLives
      : ASSUMED_START_LIVES
  const tank = typeof fields.puGotTank === 'number' ? fields.puGotTank : 0
  const deaths = typeof fields.playerDeaths === 'number' ? fields.playerDeaths : 0
  return Math.max(1, lives + tank - deaths) * PLAYER_MAX_HP
}

/** 每杀承伤的分母（用户 2026-09-16：命数 × 满额单命 HP）。 */
function dmgPerKillCapacity(startLives?: number | null): number {
  const lives =
    typeof startLives === 'number' && Number.isFinite(startLives) && startLives > 0
      ? startLives
      : ASSUMED_START_LIVES
  return lives * PLAYER_MAX_HP
}

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
  startLives?: number
}): number | null {
  const outcome = String(m.outcome ?? '')
  if (outcome && outcome !== 'stage_clear') return null
  if (typeof m.playerDamageTaken !== 'number' || !Number.isFinite(m.playerDamageTaken)) {
    return null
  }
  const capacity = lifeCapacity(m)
  return Math.max(0, Math.round(capacity - m.playerDamageTaken))
}

/** 胜局残血占该局可支配容量的比例 0–1；null = 不可计算。 */
function residualHpPctFromFields(m: {
  outcome?: unknown
  playerDamageTaken?: number
  playerDeaths?: number
  puGotTank?: number
  startLives?: number
}): number | null {
  const hp = residualHpFromFields(m)
  if (hp === null) return null
  const capacity = lifeCapacity(m)
  return capacity > 0 ? hp / capacity : null
}

export function readIterActuals(trajDir: string, iter: number): IterActuals | null {
  const course = courseFromTrajDir(trajDir)
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
        residualPct: number | null
        /** outcome（stage_clear=胜局；null=manifest 未落盘）。 */
        outcome: string | null
        /** playerDamageTaken（全样本承伤，null=字段缺失 → 不计入承伤/杀）。 */
        dmgTaken: number | null
        /** 关卡敌数（manifest.enemyTotal 或 stage 反查；null=未知）。 */
        enemyTotal: number | null
        startLives: number | null
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
            enemyTotal?: number
            startLives?: number
          }
          const stage = Number(m.stage)
          const seed = Number(m.seed)
          if (!Number.isFinite(stage) || !Number.isFinite(seed)) continue
          const nSamples = Number(m.nSamples ?? 0)
          const prev = best.get(`${stage}:${seed}`)
          if (!prev || nSamples > prev.nSamples) {
            const enemyTotal = resolveEnemyTotal(stage, m.enemyTotal, course)
            best.set(`${stage}:${seed}`, {
              nSamples,
              kills: Number(m.kills ?? 0) || 0,
              pu: Number(m.powerUpsCollected ?? 0) || 0,
              ticks: Number(m.ticks ?? 0) || 0,
              residualHp: residualHpFromFields(m),
              residualPct: residualHpPctFromFields(m),
              outcome: typeof m.outcome === 'string' && m.outcome.length > 0 ? m.outcome : null,
              dmgTaken:
                typeof m.playerDamageTaken === 'number' && Number.isFinite(m.playerDamageTaken)
                  ? m.playerDamageTaken
                  : null,
              enemyTotal,
              startLives:
                typeof m.startLives === 'number' && Number.isFinite(m.startLives)
                  ? m.startLives
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
    let residualPctSum = 0
    let residualPctN = 0
    // 胜局/败局耗时（ticks）与 承伤/杀（全样本，分子分母同口径）
    let winTickSum = 0
    let winN = 0
    let lossTickSum = 0
    let lossN = 0
    let dmgSum = 0
    let dmgKills = 0
    let dmgN = 0
    // 歼灭率：仅累计「知道敌数」的局（Σkills / ΣenemyTotal）
    let killRateKills = 0
    let killRateEnemies = 0
    let killRateN = 0
    let startLivesSum = 0
    let startLivesN = 0
    for (const v of best.values()) {
      totalKills += v.kills
      totalPU += v.pu
      totalTicks += v.ticks
      if (v.residualHp !== null) {
        residualSum += v.residualHp
        residualN++
      }
      if (v.residualPct !== null) {
        residualPctSum += v.residualPct
        residualPctN++
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
      if (v.enemyTotal != null && v.enemyTotal > 0) {
        killRateKills += v.kills
        killRateEnemies += v.enemyTotal
        killRateN++
      }
      if (v.startLives != null && v.startLives > 0) {
        startLivesSum += v.startLives
        startLivesN++
      }
    }
    const dmgPerKillAbs = dmgN > 0 && dmgKills > 0 ? +(dmgSum / dmgKills).toFixed(1) : null
    const meanStartLives = startLivesN > 0 ? startLivesSum / startLivesN : ASSUMED_START_LIVES
    return {
      games: best.size,
      totalKills,
      totalPU,
      avgTicks: Math.round(totalTicks / best.size),
      avgResidualHp: residualN > 0 ? Math.round(residualSum / residualN) : null,
      avgWinTicks: winN > 0 ? Math.round(winTickSum / winN) : null,
      avgLossTicks: lossN > 0 ? Math.round(lossTickSum / lossN) : null,
      dmgPerKill: dmgPerKillAbs,
      killRate: killRateN > 0 && killRateEnemies > 0 ? killRateKills / killRateEnemies : null,
      dmgPerKillPct:
        dmgPerKillAbs != null ? dmgPerKillAbs / dmgPerKillCapacity(meanStartLives) : null,
      avgResidualHpPct: residualPctN > 0 ? residualPctSum / residualPctN : null,
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
  /** 缓存 schema 版本：v2 = 自定义关敌数反查（2000+）已接入，killRate 可非 null。 */
  schemaV: number
}

/** 当前 actuals 缓存 schema 版本（门闩：旧缓存一律作废重建）。 */
const ACTUALS_SCHEMA_V = 2

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
        // schema 版本门闩：缺 schemaV 或版本过旧（含 v1 百分比改造）→ 作废重建，
        // 带出自定义关敌数反查后的 killRate。
        'schemaV' in v &&
        Number((v as CachedActuals).schemaV) >= ACTUALS_SCHEMA_V &&
        'killRate' in v &&
        'dmgPerKillPct' in v &&
        'avgResidualHpPct' in v
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
  const course = courseFromTrajDir(trajDir)
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
    /** 歼灭率分子分母（仅累计有 enemyTotal 的局）。 */
    killRateKills: number
    killRateEnemies: number
    killRateN: number
    /** 胜局残血占比累计（0–1）。 */
    residualPctSum: number
    residualPctN: number
    startLivesSum: number
    startLivesN: number
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
              killRateKills: 0,
              killRateEnemies: 0,
              killRateN: 0,
              residualPctSum: 0,
              residualPctN: 0,
              startLivesSum: 0,
              startLivesN: 0,
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
          // 歼灭率：manifest/eval 行的 enemyTotal，缺则 stage 反查。
          const stageId = Number(r.stage)
          const enemyTotal = resolveEnemyTotal(
            Number.isFinite(stageId) ? stageId : NaN,
            typeof r.enemyTotal === 'number' ? r.enemyTotal : null,
            course,
          )
          if (enemyTotal != null && enemyTotal > 0) {
            agg.killRateKills += Number(r.kills ?? 0) || 0
            agg.killRateEnemies += enemyTotal
            agg.killRateN++
          }
          const sl = Number(r.startLives)
          if (Number.isFinite(sl) && sl > 0) {
            agg.startLivesSum += sl
            agg.startLivesN++
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
            const fields = {
              outcome: 'stage_clear',
              playerDamageTaken:
                typeof r.playerDamageTaken === 'number' ? r.playerDamageTaken : undefined,
              playerDeaths: typeof r.playerDeaths === 'number' ? r.playerDeaths : undefined,
              puGotTank: typeof r.puGotTank === 'number' ? r.puGotTank : undefined,
              startLives: Number.isFinite(sl) && sl > 0 ? sl : undefined,
            }
            const rh = residualHpFromFields(fields)
            if (rh !== null) {
              agg.residualSum += rh
              agg.residualN++
            }
            const rp = residualHpPctFromFields(fields)
            if (rp !== null) {
              agg.residualPctSum += rp
              agg.residualPctN++
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
          anchorWr: typeof r.anchor_wr === 'number' ? r.anchor_wr : null,
          rotorWr: typeof r.rotor_wr === 'number' ? r.rotor_wr : null,
          overfitGapPp: typeof r.overfit_gap_pp === 'number' ? r.overfit_gap_pp : null,
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
      s.killRate =
        agg.killRateN > 0 && agg.killRateEnemies > 0
          ? agg.killRateKills / agg.killRateEnemies
          : null
      const evalStartLives =
        agg.startLivesN > 0 ? agg.startLivesSum / agg.startLivesN : ASSUMED_START_LIVES
      s.dmgPerKillPct =
        s.dmgPerKill != null ? s.dmgPerKill / dmgPerKillCapacity(evalStartLives) : null
      s.avgResidualHpPct = agg.residualPctN > 0 ? agg.residualPctSum / agg.residualPctN : null
      s.scoreMean = +mean.toFixed(4)
      s.scoreStd = +Math.sqrt(Math.max(0, agg.scoreSqSum / agg.n - mean * mean)).toFixed(4)
    }
  } catch {
    /* unreadable */
  }
  return out
}

// ---------------- 最新 in-loop eval 逐局视图（导出 replay 弹窗数据源） ----------------

/** 单局类型判定：win→胜利；outcome=max_ticks→超时；其余（gameover）→失败。
 *  BONUS 截断局（cleared 但 outcome=max_ticks）按 win=true 归「胜利」，cleared 徽标单独披露。 */
function evalGameClass(win: boolean, outcome: string): EvalGameClass {
  if (win) return 'win'
  return outcome === 'max_ticks' ? 'timeout' : 'fail'
}

/** 读取最新 in-loop eval 的逐局行：latest = eval_summary 的最大 iter（与指标表
 *  「eval」视图同口径），行 = 该 (iter, wver) 的全部 event=eval 行。
 *
 *  - 排除带 `source` 字段的行（EvalBoard B/C 批与 A 层同册不同源，见 eval_done_keys）。
 *  - 同键重复落账取最后一条（断点重试诚实覆盖）。
 *  - 无任何 summary / 文件不可读 → null（弹窗显示「暂无 eval 评估记录」）。 */
export function readLatestEvalGames(trajDir: string): EvalGamesData | null {
  const logPath = join(trajDir, 'eval_log.jsonl')
  if (!existsSync(logPath)) return null
  // 第一趟：summary 按 iter 归并（同 iter 重复 = 补跑后的重复落账，取最后一条）。
  const summaries = new Map<number, { wver: string; time: string }>()
  try {
    for (const line of readFileSync(logPath, 'utf8').split(String.fromCharCode(10))) {
      if (!line.trim()) continue
      try {
        const r = JSON.parse(line) as Record<string, unknown>
        if (r.event !== 'eval_summary') continue
        const iter = Number(r.iter ?? -1)
        if (!Number.isInteger(iter) || iter < 0) continue
        summaries.set(iter, { wver: String(r.wver ?? ''), time: String(r.time ?? '') })
      } catch {
        /* skip bad line */
      }
    }
  } catch {
    return null
  }
  if (summaries.size === 0) return null
  let latest = -1
  for (const it of summaries.keys()) if (it > latest) latest = it
  const head = summaries.get(latest)
  if (!head || !head.wver) return null

  // 第二趟：收集该 (iter, wver) 的逐局行（同键覆盖；B/C source 行排除）。
  const rows = new Map<string, EvalGameRow>()
  const outcomes: Record<string, number> = {}
  try {
    for (const line of readFileSync(logPath, 'utf8').split(String.fromCharCode(10))) {
      if (!line.trim()) continue
      try {
        const r = JSON.parse(line) as Record<string, unknown>
        if (r.event !== 'eval' || 'source' in r) continue
        if (Number(r.iter ?? -1) !== latest || String(r.wver ?? '') !== head.wver) continue
        const stage = Number(r.stage)
        const seed = Number(r.seed)
        if (!Number.isInteger(stage) || !Number.isInteger(seed)) continue
        const outcome = String(r.outcome ?? '')
        const win = r.win === true || r.win === 1
        const cleared = r.cleared === true || r.cleared === 1
        const kills = Number(r.kills ?? 0) || 0
        const dmgTaken =
          typeof r.playerDamageTaken === 'number' && Number.isFinite(r.playerDamageTaken)
            ? r.playerDamageTaken
            : null
        // 残血：与 readEvalSummaries 聚合同口径——胜局按 stage_clear 推算
        // （BONUS 截断胜局 outcome=max_ticks，容量公式同样成立），败局恒 null。
        const residualHp = win
          ? residualHpFromFields({
              outcome: 'stage_clear',
              playerDamageTaken: dmgTaken ?? undefined,
              playerDeaths: typeof r.playerDeaths === 'number' ? r.playerDeaths : undefined,
              puGotTank: typeof r.puGotTank === 'number' ? r.puGotTank : undefined,
            })
          : null
        rows.set(`${stage}:${seed}`, {
          stage,
          seed,
          stageName: '', // api.buildEvalGamesView 按 stage id 解析（本层不 import src/config）
          cls: evalGameClass(win, outcome),
          cleared,
          outcome,
          ticks: Number(r.ticks ?? 0) || 0,
          kills,
          dmgTaken,
          dmgPerKill: dmgTaken != null && kills > 0 ? +(dmgTaken / kills).toFixed(1) : null,
          residualHp,
          pu: Number(r.powerUpsCollected ?? 0) || 0,
          score: typeof r.score === 'number' ? r.score : null,
          node: String(r.node ?? ''),
          time: String(r.time ?? ''),
        })
        outcomes[outcome] = (outcomes[outcome] ?? 0) + 1
      } catch {
        /* skip bad line */
      }
    }
  } catch {
    /* unreadable → 有 summary 无行也照常返回（rows 空，弹窗只显示概要） */
  }
  const all = [...rows.values()].sort((a, b) => a.stage - b.stage || a.seed - b.seed)
  let wins = 0
  let clears = 0
  for (const row of all) {
    if (row.cls === 'win') wins++
    if (row.cleared) clears++
  }
  return {
    iter: latest,
    wver: head.wver,
    time: head.time,
    games: all.length,
    wins,
    winRate: all.length > 0 ? wins / all.length : null,
    clears,
    outcomes,
    rows: all,
  }
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

/** 配对裁判（只读哨子，不进门判）：最新 eval vs it0 基线（bc 权重）/ vs 上一 eval 轮。
 *
 * 同 (stage,seed) 逐局配对，比较的是同一语料下两个 checkpoint 的贪心胜负——
 * 跨语料（bc 在 0-99 vs 新权重在 860001+）时差分把卷面难度抵消掉。一边缺席的
 * 局只计 unpaired 诚实披露。单轮/无数据 → 对应项 null（UI 空态）。 */
export function readPairedReferee(trajDir: string): PairedReferee | null {
  const byIter = readEvalGameWins(trajDir)
  const iters = [...byIter.keys()].sort((a, b) => a - b)
  if (iters.length === 0) return null
  const latest = iters[iters.length - 1]
  // 开腿基准同上：优先 it0（bc 权重基线），无则退回首个 eval 轮。
  const first = iters.includes(0) ? 0 : iters[0]
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
  // 配对基线 = it0（课程 bc 权重的干净评估，trainer 在本 run 首次 rollout 收官后补派）
  // ——恒定、跨腿可比。无 it0 时退回首个 eval 轮（兼容 it0 上线前已跑完的腿）：
  // "首条 eval" 会随 run 起点漂移（resume 时首条可能是 it50，配对比的是中途两点）。
  const evalBaseline = evalIters.length === 0 ? null : evalIters.includes(0) ? 0 : evalIters[0]
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
          // 缓存命中必须**整对象透传**：百分比字段（killRate/dmgPerKillPct/avgResidualHpPct）
          // 漏拷会让表列恒空（schema 门闩已在 loadActualsCache 挡掉旧缓存）。
          actuals = {
            games: cached.games,
            totalKills: cached.totalKills,
            totalPU: cached.totalPU,
            avgTicks: cached.avgTicks,
            avgResidualHp: cached.avgResidualHp ?? null,
            avgWinTicks: cached.avgWinTicks ?? null,
            avgLossTicks: cached.avgLossTicks ?? null,
            dmgPerKill: cached.dmgPerKill ?? null,
            killRate: cached.killRate ?? null,
            dmgPerKillPct: cached.dmgPerKillPct ?? null,
            avgResidualHpPct: cached.avgResidualHpPct ?? null,
          }
        } else {
          actuals = readIterActuals(trajDir, iter)
          if (actuals) {
            actualsCache.set(iter, { ...actuals, time: rowTime, schemaV: ACTUALS_SCHEMA_V })
            cacheDirty = true
          }
        }
        rows.push({
          iter,
          time: rowTime,
          wire: parseIterWire(r.wire),
          winRate: Number(r.winRate ?? 0),
          scoreMean: Number(r.score_mean ?? 0),
          scoreStd: Number(r.score_std ?? 0),
          samples: Number(r.samples ?? 0),
          rolloutSec: Number(r.rollout_sec ?? 0),
          ppoSec: Number(r.ppo_sec ?? 0),
          pureCollectSec: r.pure_collect_sec != null ? Number(r.pure_collect_sec) : null,
          ppoCloudSec: r.ppo_cloud_sec != null ? Number(r.ppo_cloud_sec) : null,
          distPhaseSec: r.dist_phase_sec != null ? Number(r.dist_phase_sec) : null,
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
    // it0 基线行（bc 权重评估）：它没有 rollout 采样、只有干净评估 ⇒ 合成一行；
    // rollout 派生字段一律 NaN（趋势图的缺口约定——写成 0 会在图上多画一个假零点）。
    // 表格只在 eval 子行渲染它（MetricsTable.buildRows 跳过 iter<=0 的主行）。
    // 必须先有 ≥1 条真实 iteration 行才合成：否则 latestRow 会把基线当成"最新轮"。
    const base0 = evalSummaries.get(0)
    if (base0 && merged.some((r) => r.iter > 0) && !merged.some((r) => r.iter === 0)) {
      merged.push({
        iter: 0,
        time: base0.time,
        winRate: Number.NaN,
        scoreMean: Number.NaN,
        scoreStd: Number.NaN,
        samples: 0,
        rolloutSec: 0,
        ppoSec: 0,
        pureCollectSec: null,
        ppoCloudSec: null,
        distPhaseSec: null,
        kl: Number.NaN,
        entropy: Number.NaN,
        policyLoss: Number.NaN,
        valueLoss: Number.NaN,
        meanRet: Number.NaN,
        lr: 0,
        expectedGames: 0,
        halted: false,
        topDims: '',
        avgTicks: 0,
        accuracy: Number.NaN,
        loot: Number.NaN,
        kills: Number.NaN,
        actuals: null,
        // 基线自己：vs 自己不判（pairedVsFirst = null → UI 标"基线"）
        evalData: withPaired(base0, null),
      })
      merged.sort((a, b) => b.iter - a.iter)
    }
    if (cacheDirty) saveActualsCache(trajDir, actualsCache)
    return { rows: merged.slice(0, MAX) }
  } catch {
    return { rows: [] }
  }
}
