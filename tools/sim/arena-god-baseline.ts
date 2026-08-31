/**
 * arena-god-baseline.ts — 卡 A0（plan/goal-nn-action.md）：God-AI arena 锚值表。
 *
 * 产出（全部相对门的基准，§2.4）：
 *   1. 五场（S1/S2/S3/S3H/S4a）× 3 布局变异 × 60 seed × {hard 主, classic 对照}
 *      的 God-AI 行为锚：通关率 / 击杀 / 开火数 / 受伤数 / 存活 tick。
 *   2. obs 位势表：每通道占用率 + 标量均值（arena 各级 vs 真实 hard 35 关并排）。
 *   3. SimResult.ticks 分布（均值/中位/p95）——验证"短回合"前提 + A1 钉 max-ticks。
 *   4. 每场布局散列（与 reports/arena-layout-hashes.json 对账）。
 *
 * 锚可用性（§2.4）：通关 <60% 的场 ⇒ 锚不可用，该级改绝对阈值。
 * 功率冒烟（§4.2）在本工具之外：m1-eval 重跑 ×2 + paired-gate --*-ledger。
 *
 * Usage:
 *   bun tools/sim/arena-god-baseline.ts                      # 全量
 *   bun tools/sim/arena-god-baseline.ts --levels S1,S2      # 部分级
 *   bun tools/sim/arena-god-baseline.ts --skip-census       # 跳过 obs 位势表
 */
import { writeFileSync } from 'node:fs'
import { arg } from '../lib/cli'
import { SimWorkerPool } from './sim-pool'
import type { SimTask, SimTaskResult } from './sim-worker'
import {
  ARENA_LADDER,
  resolveArenaStage,
  stageLayoutHash,
  type ArenaLevel,
} from '../../src/nn/arena-ladder'
import { STAGES } from '../../src/config/stages'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { ObsEncoder, OBS_CHANNELS, SCALAR_DIM } from '../../src/nn/obs-encoder'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { START_LIVES } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'

// 各级锚定跑的最大 tick（curriculum.ts 同级口径——足以让 God-AI 打完/明显超时）
const LEVEL_MAX_TICKS: Record<ArenaLevel, number> = {
  S1: 6000,
  S2: 8000,
  S3: 20000,
  S3H: 20000,
  S4a: 20000,
  'S-Dodge': 6000, // 锚定后确认：P95=4577, P90×1.2≈4800，保守取 6000
}
const SEEDS = Array.from({ length: 60 }, (_, i) => i + 1) // 1..60（与 pinned 基线同约定）
const LEVEL_ORDER: ArenaLevel[] = ['S1', 'S2', 'S3', 'S3H', 'S4a', 'S-Dodge']

interface GameOutcome {
  level: ArenaLevel
  variant: number
  seed: number
  difficulty: string
  outcome: string
  ticks: number
  kills: number
  shots: number
  deaths: number
  baseAlive: boolean
  firstKillTick: number | undefined
}

interface MetricStats {
  mean: number
  std: number
  median: number
  p95: number
}

function stats(xs: number[]): MetricStats {
  if (xs.length === 0) return { mean: NaN, std: NaN, median: NaN, p95: NaN }
  const sorted = [...xs].sort((a, b) => a - b)
  const mean = xs.reduce((a, b) => a + b, 0) / xs.length
  const std =
    xs.length > 1 ? Math.sqrt(xs.reduce((a, x) => a + (x - mean) ** 2, 0) / (xs.length - 1)) : 0
  const q = (p: number): number =>
    sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]
  return { mean, std, median: q(0.5), p95: q(0.95) }
}

function simResultToOutcome(
  g: SimTaskResult,
  level: ArenaLevel,
  variant: number,
  seed: number,
  difficulty: string,
): GameOutcome {
  const tel = g.telemetry
  return {
    level,
    variant,
    seed,
    difficulty,
    outcome: g.outcome,
    ticks: g.ticks,
    kills: g.killCount,
    shots: tel?.playerShots ?? 0,
    deaths: tel?.playerDeaths ?? 0,
    baseAlive: g.baseAlive,
    firstKillTick: g.firstKillTick,
  }
}

// ============================================================
// obs 位势表（零埋点，卡 A0 步骤 3）
// ============================================================

interface ObsCensus {
  channelOccupancy: number[] // 每通道：非零格占比的样本均值
  scalarMeans: number[] // 每标量：样本均值
  samples: number
}

function emptyCensus(): ObsCensus {
  return {
    channelOccupancy: new Array(OBS_CHANNELS).fill(0),
    scalarMeans: new Array(SCALAR_DIM).fill(0),
    samples: 0,
  }
}

function accumulateCensus(c: ObsCensus, enc: ObsEncoder): void {
  // 通道内偏移 = 26×26（obs 布局 [ch*676 + r*26 + col]）——不能用 OBS_CHANNELS×BOARD²
  // 当步长（那是整张 obs 的长度；乘出来 ch1 起点就越界，undefined !== 0 全计成非零
  // ⇒ ch1–ch13 假 100% 占用率，census 失去判别力，2026-08-30 用户复核发现）。
  const perCh = 26 * 26
  for (let ch = 0; ch < OBS_CHANNELS; ch++) {
    let nz = 0
    const base = ch * perCh
    for (let i = 0; i < perCh; i++) if (enc.obs[base + i] !== 0) nz++
    c.channelOccupancy[ch] += nz / perCh
  }
  for (let i = 0; i < SCALAR_DIM; i++) c.scalarMeans[i] += enc.scalars[i]
  c.samples++
}

function finishCensus(c: ObsCensus): ObsCensus {
  const n = Math.max(1, c.samples)
  return {
    channelOccupancy: c.channelOccupancy.map((x) => +(x / n).toFixed(5)),
    scalarMeans: c.scalarMeans.map((x) => +(x / n).toFixed(5)),
    samples: c.samples,
  }
}

/** 单局 in-process 观测普查（镜像 runSimulation 的 World 装载序列；只读采样）。 */
function censusGame(
  stage: import('../../src/types').StageData,
  seed: number,
  difficulty: string,
  maxTicks: number,
  enc: ObsEncoder,
): number {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(stage, 0)
  input.reset()
  let t = 0
  let samples = 0
  while (t < maxTicks) {
    sim.tick()
    input.endFrame()
    t++
    if (t % 10 === 0 && world.state === 'playing') {
      enc.encode(world)
      accumulateCensus(censusAcc, enc)
      samples++
    }
    if (world.state !== 'playing' && world.state !== 'paused') break
  }
  return samples
}

// 模块级累加器（censusGame 由同一主线程串行调用——无并发问题）
let censusAcc = emptyCensus()

async function main(): Promise<void> {
  const t0 = Date.now()
  const levelsArg = arg('levels')
  const levels = levelsArg ? (levelsArg.split(',') as ArenaLevel[]) : LEVEL_ORDER
  const skipCensus = argsHas('skip-census')

  const pool = new SimWorkerPool()
  const tasks: SimTask[] = []
  const taskMeta: Array<{ level: ArenaLevel; variant: number; seed: number; difficulty: string }> =
    []
  let id = 0
  const layoutHashes: Record<string, string> = {}
  for (const level of levels) {
    for (const [aid, spec] of ARENA_LADDER) {
      if (spec.level !== level) continue
      const stage = resolveArenaStage(aid)!
      layoutHashes[`${spec.level}-v${spec.variant}`] = stageLayoutHash(stage)
      for (const difficulty of ['hard', 'classic']) {
        for (const seed of SEEDS) {
          tasks.push({
            id: id++,
            seed,
            stage,
            difficulty,
            params: { ...DEFAULT_GOD_AI_PARAMS },
            maxTicks: LEVEL_MAX_TICKS[level],
            telemetry: true,
            // 一命锚定（plan/dodge-item-curriculum.md §1b F1）：S-Dodge/hard 覆写为 1 命。
            // classic 天然 instant 一命但 combatModel 不同，不可当 S-Dodge 锚。
            ...(level === 'S-Dodge' && difficulty === 'hard' ? { livesOverride: 1 } : {}),
          })
          taskMeta.push({ level, variant: spec.variant, seed, difficulty })
        }
      }
    }
  }
  process.stderr.write(
    `[arena-god-baseline] ${tasks.length} games (levels=${levels.join(',')} × 3 variants × 60 seeds × {hard,classic}) ` +
      `pool=${pool.size} workers\n`,
  )
  const results = await pool.runBatch(tasks).finally(() => pool.terminate())
  const games = results.map((r, i) =>
    simResultToOutcome(
      r,
      taskMeta[i].level,
      taskMeta[i].variant,
      taskMeta[i].seed,
      taskMeta[i].difficulty,
    ),
  )

  // ---- 聚合：每 (level, difficulty) ----
  const anchors: Record<string, unknown> = {}
  for (const level of levels) {
    for (const difficulty of ['hard', 'classic']) {
      const gs = games.filter((g) => g.level === level && g.difficulty === difficulty)
      if (gs.length === 0) continue
      const win = (g: GameOutcome): number => (g.outcome === 'stage_clear' ? 1 : 0)
      const byVariant = [0, 1, 2].map((v) => gs.filter((g) => g.variant === v))
      const variantWin = byVariant.map((vs) => vs.reduce((a, g) => a + win(g), 0) / vs.length)
      const variantWinStd = Math.sqrt(
        variantWin.reduce(
          (a, x) => a + (x - variantWin.reduce((s, y) => s + y, 0) / variantWin.length) ** 2,
          0,
        ) / variantWin.length,
      )
      const anchor = {
        games: gs.length,
        layoutHashes: Object.fromEntries(
          Object.entries(layoutHashes).filter(([k]) => k.startsWith(level)),
        ),
        winRate: +((gs.reduce((a, g) => a + win(g), 0) / gs.length) * 100).toFixed(2),
        kills: round2(stats(gs.map((g) => g.kills))),
        shots: round2(stats(gs.map((g) => g.shots))),
        deaths: round2(stats(gs.map((g) => g.deaths))),
        aliveTicks: round2(stats(gs.map((g) => g.ticks))),
        firstKillTick: round2(
          stats(gs.filter((g) => g.firstKillTick !== undefined).map((g) => g.firstKillTick!)),
        ),
        perVariant: byVariant.map((vs, vi) => ({
          variant: vi,
          winRate: +((vs.reduce((a, g) => a + win(g), 0) / vs.length) * 100).toFixed(2),
          kills: +round2(stats(vs.map((g) => g.kills))).mean.toFixed(2),
          deaths: +round2(stats(vs.map((g) => g.deaths))).mean.toFixed(2),
          ticks: +round2(stats(vs.map((g) => g.ticks))).mean.toFixed(1),
        })),
        variantWinStd: +(variantWinStd * 100).toFixed(2), // 变异间方差（pp）
        anchorUsable:
          variantWin.every((w) => w >= 0.6) &&
          gs.reduce((a, g) => a + win(g), 0) / gs.length >= 0.6,
      }
      anchors[`${level}.${difficulty}`] = anchor
    }
  }

  // ---- obs 位势表（arena 各级 + 真实 hard 35 关并排）----
  const census: Record<string, ObsCensus> = {}
  if (!skipCensus) {
    const enc = new ObsEncoder()
    for (const level of levels) {
      censusAcc = emptyCensus()
      for (const [aid, spec] of ARENA_LADDER) {
        if (spec.level !== level) continue
        censusGame(resolveArenaStage(aid)!, 42, 'hard', LEVEL_MAX_TICKS[level], enc)
      }
      census[level] = finishCensus(censusAcc)
    }
    censusAcc = emptyCensus()
    for (let s = 0; s < STAGES.length; s++) {
      for (const seed of [42, 43]) {
        censusGame(STAGES[s], seed, 'hard', 36000, enc)
      }
    }
    census['real-hard-35'] = finishCensus(censusAcc)
  }

  const report = {
    tool: 'arena-god-baseline',
    generated: new Date().toISOString(),
    seeds: `1-60`,
    layoutHashes,
    anchors,
    ...(skipCensus ? {} : { obsCensus: census }),
    wallSec: +((Date.now() - t0) / 1000).toFixed(1),
  }
  writeFileSync('reports/arena-god-baseline.json', JSON.stringify(report, null, 2) + '\n')

  // ---- markdown 摘要 ----
  const md: string[] = [
    '# arena-god-baseline（卡 A0）',
    '',
    `生成 ${report.generated} · seeds 1-60 × 3 布局变异 · hard 主 / classic 对照`,
    '',
    '| 场.难度 | 通关率 | 击杀 | 开火 | 受伤 | 存活tick | tick中位/p95 | 变异间σ | 锚可用 |',
    '|---|---|---|---|---|---|---|---|---|',
  ]
  for (const key of Object.keys(anchors)) {
    const a = anchors[key] as any
    md.push(
      `| ${key} | ${a.winRate}% | ${a.kills.mean.toFixed(2)}±${a.kills.std.toFixed(2)} | ` +
        `${a.shots.mean.toFixed(1)} | ${a.deaths.mean.toFixed(2)} | ${a.aliveTicks.mean.toFixed(0)} | ` +
        `${a.aliveTicks.median.toFixed(0)}/${a.aliveTicks.p95.toFixed(0)} | ${a.variantWinStd}pp | ${a.anchorUsable ? '✅' : '❌ 绝对阈值'} |`,
    )
  }
  if (!skipCensus) {
    md.push(
      '',
      '## obs 位势表（通道占用率 %，hard）',
      '',
      '| 级 | ' + [...Array(OBS_CHANNELS).keys()].map((i) => `ch${i}`).join(' | ') + ' |',
      '|---|' + '---|'.repeat(OBS_CHANNELS),
    )
    for (const [k, c] of Object.entries(census)) {
      md.push(`| ${k} | ${c.channelOccupancy.map((x) => (x * 100).toFixed(1)).join(' | ')} |`)
    }
    md.push(
      '',
      '## 标量均值（s0..s18）',
      '',
      '| 级 | ' + [...Array(SCALAR_DIM).keys()].map((i) => `s${i}`).join(' | ') + ' |',
      '|---|' + '---|'.repeat(SCALAR_DIM),
    )
    for (const [k, c] of Object.entries(census)) {
      md.push(`| ${k} | ${c.scalarMeans.map((x) => x.toFixed(3)).join(' | ')} |`)
    }
  }
  writeFileSync('reports/arena-god-baseline.md', md.join('\n') + '\n')
  process.stderr.write(
    `[arena-god-baseline] done in ${report.wallSec}s → reports/arena-god-baseline.{json,md}\n`,
  )
}

function round2(s: MetricStats): MetricStats {
  return {
    mean: +s.mean.toFixed(2),
    std: +s.std.toFixed(2),
    median: +s.median.toFixed(2),
    p95: +s.p95.toFixed(2),
  }
}

function argsHas(name: string): boolean {
  return process.argv.slice(2).includes(`--${name}`)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
