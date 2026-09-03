#!/usr/bin/env bun
/**
 * export-godai-labels.ts — God-AI teacher corpus exporter (plan/RL-Net-Selection.md P1.5).
 *
 * Runs the live God-AI (GodAIInput) through real engine games and captures, at
 * each decision tick (the same event-type predicate as the BC exporter,
 * plan §1.3: turn / fire-edge / item / subsample every K=10), the pair
 *   (obs-encoder(world), God-AI action label + invalid-action mask + condition)
 * written as npy shards in the SAME format as `tools/replay/export-observations.ts`
 * (obs/scalars/actions/masks/conditions + manifest), so the existing
 * `train_bc.py` can consume the corpus directly.
 *
 * v2 (plan/AI-No-Items-Warmstart.md M2, OBS_SCHEMA_MAJOR=2)：
 *   ① item 头删除 —— actions→(N,2) [move,fire]，masks→(N,7) [move5,fire2]；
 *   ② SCALAR_DIM 24→19（删 guard/frenzy/rewind stock + frenzy 状态 5 标量）；
 *   ③ wins-only 口径（--wins 默认 1）：仅保留 stage_clear 局的 shard；
 *      濒危（near-miss）帧分层超采样（--near-miss-times 默认 2×）：护圈受损
 *      或基地受压帧复制入样本（训练侧对守家帧加权）；
 *   ⑥ returns：按 RL reward 定义（tools/sim/rl-reward.ts，与 export-rl-rollout
 *      相同）逐决策帧结算 reward、γ=0.995 反向折现成 return，落盘 returns.npy
 *      —— M3 value 头 MC 预置的数据源。
 *
 * Determinism: same (stage, seed, difficulty) → identical shard bytes. The
 * God-AI driver consumes its own independent RNG (seeded `seed ^ 0x9e3779b9`,
 * same as `tools/sim/simulation-runner.ts`), so the run is fully reproducible.
 *
 * Modes / flags:
 *   --stages all|N|N-M   stage range (1-based, default all)
 *   --seeds  N|N-M       seed range (default 1-10)
 *   --difficulty <key>   difficulty (default hard)
 *   --out <dir>          output root (default tmp/godai-labels)
 *   --max-ticks <N>      per-game tick cap (default 36000)
 *   --wins 0|1           wins-only filter (default 1)
 *   --near-miss-times N  near-miss oversample multiplicity (default 2; 0=off)
 *   --verify-determinism re-run the FIRST seed of each stage twice and
 *                        byte-compare the shards (gate ②). No training shards.
 *
 * Usage:
 *   bun tools/sim/export-godai-labels.ts --stages 1-5 --seeds 1-3 --out tmp/godai-labels-v2
 *   bun tools/sim/export-godai-labels.ts --stages 1 --seeds 1 --verify-determinism --wins 0
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, GRID, BASE_POS, CELL, ENEMIES_PER_STAGE } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { GodAIInput } from '../../src/ai/GodAIInput'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/god/params'
import {
  ObsEncoder,
  decisionTick,
  actionFromFrame,
  computeMasks,
  OBS_SCHEMA_MAJOR,
  SCALAR_DIM,
} from '../../src/nn/obs-encoder'
import { decodeStageGrid, CUSTOM_STAGE_BASE } from '../../src/nn/config-stage'
import { writeNpy } from '../../src/nn/npy'
import { mkdirSync, writeFileSync, readdirSync, readFileSync, rmSync } from 'fs'
import { platform, arch, cpus } from 'os'
import {
  REWARD_SCALE,
  discountReturns,
  phiNow,
  countBaseWall,
  sampleBasePressure,
  BASE_PRESSURE_RADIUS,
  TELEMETRY_SAMPLE_TICKS,
  type PhiCounters,
} from './rl-reward'
import { scoreRun, V7_SCORE_CONFIG, type Weights } from '../eval/godai-score'
import type { RunTelemetry } from './simulation-runner'

const EXPORTER_VERSION = '2.0.0'
const K = 10
const OBS_N = 14 * 26 * 26
const MASK_DIM = 7

const ENV = {
  runtime: `bun ${Bun.version}`,
  platform: platform(),
  arch: arch(),
  cpuCores: cpus().length,
}

// RL 打分配置（与 export-rl-rollout 同源：v7 带 + 守家优先败局带）
const RL_SCORE_CONFIG = {
  ...V7_SCORE_CONFIG,
  lossWeights: {
    progress: 0.3,
    baseIntegrity: 0.25,
    baseSafety: 0.25,
    tempo: 0.08,
    accuracy: 0.06,
    openingTempo: 0.03,
    loot: 0.03,
  } as Weights,
}

interface Sample {
  obs: Uint8Array
  scalars: Float32Array
  move: number
  fire: number
  masks: number[]
  nearMiss: boolean
  cond: number
}

interface GameResult {
  samples: Sample[]
  outcome: 'stage_clear' | 'base_destroyed' | 'lives_exhausted' | 'timeout'
  ticks: number
  gatedScore: number
  score: number
  rewards: number[]
  perFramePhi: number[]
  nearMissFrames: number
}

function parseRange(spec: string): number[] {
  if (spec.includes('-')) {
    const [a, b] = spec.split('-').map(Number)
    return Array.from({ length: b - a + 1 }, (_, i) => a + i)
  }
  return [Number(spec)]
}

// ---- telemetry（逐字段对齐 simulation-runner / export-rl-rollout）----
interface Tel {
  enemyTotal: number
  startLives: number
  playerDeaths: number
  playerShots: number
  powerUpsSpawned: number
  powerUpsCollected: number
  starsCollected: number
  baseWallTotal: number
  baseWallIntact: number
  basePressureSum: number
  basePressureSamples: number
  cellsVisited: Set<number>
  firstKillTick: number | undefined
}

function newTel(stage: unknown, world: World): Tel {
  return {
    enemyTotal: (stage as { enemyCount?: number })?.enemyCount ?? ENEMIES_PER_STAGE,
    startLives: world.difficulty?.startLives ?? START_LIVES,
    playerDeaths: 0,
    playerShots: 0,
    powerUpsSpawned: 0,
    powerUpsCollected: 0,
    starsCollected: 0,
    baseWallTotal: countBaseWall(world),
    baseWallIntact: countBaseWall(world),
    basePressureSum: 0,
    basePressureSamples: 0,
    cellsVisited: new Set<number>(),
    firstKillTick: undefined,
  }
}

function makeCounters(tel: Tel, world: World, ticks: number, baseAlive: boolean): PhiCounters {
  return {
    enemyTotal: tel.enemyTotal,
    startLives: tel.startLives,
    kills: world.killCount,
    lives: world.lives,
    ticks,
    baseAlive,
    baseWallTotal: tel.baseWallTotal,
    baseWallIntact: tel.baseWallIntact,
    playerShots: tel.playerShots,
    powerUpsCollected: tel.powerUpsCollected,
    powerUpsSpawned: tel.powerUpsSpawned,
    basePressureMean:
      tel.basePressureSamples > 0 ? tel.basePressureSum / tel.basePressureSamples : 0,
    basePressureSamples: tel.basePressureSamples,
    firstKillTick: tel.firstKillTick,
  }
}

/**
 * Run one God-AI game and capture decision-tick (state, label) samples +
 * per-frame Φ (→ reward/returns)。World setup mirrors simulation-runner.ts.
 */
export function exportGame(
  seed: number,
  stageIndex: number,
  difficulty: string,
  maxTicks: number,
  winsOnly: boolean,
  nearMissTimes: number,
  stageJson?: string,
  livesOverride?: number,
  playerLevelOverride?: number,
): GameResult | 'loss-skipped' {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  // 课程命数/星级覆盖（plan §5.2）：语料分布须与 RL 课程 rollout 口径一致
  if (livesOverride !== undefined) world.lives = livesOverride
  if (playerLevelOverride !== undefined) world.playerLevel = playerLevelOverride

  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, godRng)
  const sim = new Simulation(world, god)
  // 自定义关（课程 stageJson，plan/rl-training-config.md §5.2）：与 export-rl-rollout
  // 同款四守卫——① 短路在真实关表解析之前；② index-0 显式（loadStageData 第二参驱动
  // 1.05^index 关卡缩放，传 2003 会把 1 敌课程放大成满编关）；③ grid/出生点校验在
  // decodeStageGrid 内部。
  const stage = stageJson
    ? decodeStageGrid(stageJson, stageIndex, seed) // seed → spawn_variants 确定性选点
    : STAGES[stageIndex]
  world.loadStageData(stage, stageJson ? 0 : stageIndex)
  god.reset()

  const encoder = new ObsEncoder()
  const samples: Sample[] = []
  const phis: number[] = [] // Φ(决策帧状态)，长度 == samples.length
  const tel = newTel(stage, world)
  const seenPuIds = new Set<number>()
  let prevLivePuIds = new Set<number>()

  let prevDir: ReturnType<GodAIInput['getMoveDirection']> = null
  let prevGuard = false
  let prevFrenzy = false
  let t = 0
  let outcome: GameResult['outcome'] = 'timeout'
  let phiFinal = 0

  const captureDecision = (
    dir: ReturnType<GodAIInput['getMoveDirection']>,
    firing: boolean,
    condition: number,
  ): void => {
    const label = actionFromFrame({ direction: dir, firing })
    const masks = computeMasks(world)
    const baseAlive = !world.tileMap.isBaseDestroyed()
    tel.baseWallIntact = countBaseWall(world)
    phis.push(phiNow(makeCounters(tel, world, t, baseAlive)))
    const ringFrac = encoder.scalars[6]
    // near-miss（守家帧，预注册口径 = 环受损 OR 敌压基地）→ 训练侧超采样权重。
    // 无基地关（课程自定义关 grid 无 base 码）：near-miss 语义不存在，恒 false——
    // 否则 countBaseWall=0 → ringFrac 判 0<1 恒 true，无基地语料全被误标守家帧
    //（实测 coverage.nearMissFrac=1.0；near-miss-times>0 会把每帧复制 N 份）。
    const basePressed =
      !!world.tileMap.getBasePos() &&
      (ringFrac < 1 ||
        world.tanks.some(
          (e) =>
            e.alive &&
            e.spawnTimer <= 0 &&
            e.allegiance === 'enemy' &&
            Math.abs(Math.floor((e.x + 16) / CELL) - BASE_POS.col) +
              Math.abs(Math.floor((e.y + 16) / CELL) - BASE_POS.row) <=
              BASE_PRESSURE_RADIUS,
        ))
    samples.push({
      obs: encoder.obs.slice(),
      scalars: encoder.scalars.slice(),
      move: label.move,
      fire: label.fire,
      masks: [...masks.move, ...masks.fire],
      nearMiss: basePressed,
      cond: condition,
    })
  }

  while (t < maxTicks) {
    // obs(t): world state BEFORE this tick's input is consumed (plan §1.3).
    encoder.encode(world)
    // 逐 tick 读取（prevDir/prevGuard/prevFrenzy 每 tick 更新，供 decisionTick 判边）。
    const dir = god.getMoveDirection()
    const firing = god.isFiring()
    const guard = god.wasItemPressed('guard')
    const frenzy = god.wasItemPressed('frenzy')
    const { isDecision, condition } = decisionTick(
      t,
      world,
      prevDir,
      dir,
      prevGuard,
      guard,
      prevFrenzy,
      frenzy,
      K,
    )
    if (isDecision) captureDecision(dir, firing, condition)

    sim.tick()
    god.endFrame()
    prevDir = dir
    prevGuard = guard
    prevFrenzy = frenzy
    t++

    // ---- telemetry（语义对齐 simulation-runner）----
    let collectedThisTick = 0
    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed') {
        if ((e as any).by === 'player' && tel.firstKillTick === undefined) tel.firstKillTick = t - 1
        if ((e as any).tank?.isPlayer) tel.playerDeaths++
      } else if (e.type === 'bullet_fired' && (e as any).bullet?.isPlayer) {
        tel.playerShots++
      } else if (e.type === 'powerup_collected') {
        collectedThisTick++
        tel.powerUpsCollected++
        if ((e as any).powerUp === 'star') tel.starsCollected++
      }
    }
    {
      const live = new Set<number>()
      for (const pu of world.powerUps) {
        live.add(pu.id)
        if (!seenPuIds.has(pu.id)) {
          seenPuIds.add(pu.id)
          tel.powerUpsSpawned++
        }
      }
      let vanished = 0
      for (const id of prevLivePuIds) if (!live.has(id)) vanished++
      tel.powerUpsSpawned += Math.max(0, collectedThisTick - vanished)
      prevLivePuIds = live
    }
    if (t % TELEMETRY_SAMPLE_TICKS === 0) {
      tel.basePressureSum += sampleBasePressure(world)
      tel.basePressureSamples++
      if (world.player?.alive) {
        const col = Math.floor((world.player.x + world.player.w / 2) / CELL)
        const row = Math.floor((world.player.y + world.player.h / 2) / CELL)
        tel.cellsVisited.add(row * GRID + col)
      }
    }

    const st: string = world.state
    if (st === 'stageclear' || st === 'victory' || st === 'gameover') {
      outcome =
        st === 'gameover'
          ? world.tileMap.isBaseDestroyed()
            ? 'base_destroyed'
            : 'lives_exhausted'
          : 'stage_clear'
      break
    }
  }

  if (outcome === 'timeout') {
    // fall through: cap reached — count as timeout loss
  }

  // ---- wins-only 过滤 ----
  if (winsOnly && outcome !== 'stage_clear') return 'loss-skipped'

  // ---- 终局打分 + reward/returns（telescoping：Σr ≡ REWARD_SCALE × gatedScore）----
  const baseAliveFinal = !world.tileMap.isBaseDestroyed()
  tel.baseWallIntact = countBaseWall(world)
  const scorable = {
    outcome,
    ticks: t,
    finalState: {
      killCount: world.killCount,
      lives: world.lives,
      baseAlive: baseAliveFinal,
    },
    firstKillTick: tel.firstKillTick,
    telemetry: {
      enemyTotal: tel.enemyTotal,
      startLives: tel.startLives,
      playerDeaths: tel.playerDeaths,
      playerShots: tel.playerShots,
      powerUpsSpawned: tel.powerUpsSpawned,
      powerUpsCollected: tel.powerUpsCollected,
      starsCollected: tel.starsCollected,
      finalPlayerLevel: world.playerLevel,
      baseWallIntact: tel.baseWallIntact,
      baseWallTotal: tel.baseWallTotal,
      basePressureMean:
        tel.basePressureSamples > 0 ? tel.basePressureSum / tel.basePressureSamples : 0,
      basePressureSamples: tel.basePressureSamples,
      cellsVisited: tel.cellsVisited.size,
      deaths: [],
    } satisfies Omit<RunTelemetry, 'deaths'> & { deaths: never[] },
  } as any
  const scored = scoreRun(scorable, RL_SCORE_CONFIG as any)
  const gatedScore = outcome === 'base_destroyed' ? scored.score * 0.1 : scored.score
  phiFinal = phiNow(makeCounters(tel, world, t, baseAliveFinal))

  // near-miss 超采样：样本与 Φ 同步扩增（dup 帧相同状态 → 帧间 reward=0）。
  const rawNearMiss = samples.filter((s) => s.nearMiss).length // 覆盖统计（扩张前）
  const dupCount = samples.map((s) => (nearMissTimes > 1 && s.nearMiss ? nearMissTimes : 1))
  const n2 = dupCount.reduce((a, b) => a + b, 0)
  const expandedSamples = new Array<Sample>(n2)
  const expandedPhi = new Array<number>(n2)
  let idx = 0
  for (let i = 0; i < samples.length; i++) {
    for (let d = 0; d < dupCount[i]; d++) {
      expandedSamples[idx] = samples[i]
      expandedPhi[idx] = phis[i]
      idx++
    }
  }
  const n = n2
  const rewards = new Array<number>(n).fill(0)
  if (n > 0) {
    for (let i = 0; i < n - 1; i++) rewards[i] = expandedPhi[i + 1] - expandedPhi[i]
    // 末帧：ΦFinal − Φ(n−1) + 锚定项（使 Σ ≡ REWARD_SCALE × gatedScore）
    rewards[n - 1] =
      phiFinal - expandedPhi[n - 1] + (REWARD_SCALE * gatedScore - (phiFinal - expandedPhi[0]))
  }

  return {
    samples: expandedSamples,
    outcome,
    ticks: t,
    gatedScore,
    score: scored.score,
    rewards,
    perFramePhi: expandedPhi,
    nearMissFrames: rawNearMiss,
  }
}

function flushShard(
  res: GameResult,
  dir: string,
  name: string,
  baseManifest: Record<string, unknown>,
): void {
  const N = res.samples.length
  if (N === 0) return
  const obs = new Uint8Array(N * OBS_N)
  const scalars = new Float32Array(N * SCALAR_DIM)
  const actions = new Uint8Array(N * 2)
  const masks = new Uint8Array(N * MASK_DIM)
  const conditions = new Uint8Array(N)
  const returns = new Float32Array(N)
  const ret = discountReturns(res.rewards)
  for (let i = 0; i < N; i++) {
    obs.set(res.samples[i].obs, i * OBS_N)
    scalars.set(res.samples[i].scalars, i * SCALAR_DIM)
    actions[i * 2] = res.samples[i].move
    actions[i * 2 + 1] = res.samples[i].fire
    for (let j = 0; j < MASK_DIM; j++) masks[i * MASK_DIM + j] = res.samples[i].masks[j]
    conditions[i] = res.samples[i].cond
    returns[i] = ret[i]
  }
  const manifest = {
    schemaMajor: OBS_SCHEMA_MAJOR,
    obsSchemaMajor: OBS_SCHEMA_MAJOR,
    exporterVersion: EXPORTER_VERSION,
    shard: name,
    teacher: 'god-ai',
    nSamples: N,
    ...baseManifest,
  }
  mkdirSync(dir, { recursive: true })
  writeNpy(`${dir}/obs.npy`, obs, [N, 14, 26, 26], 'u1')
  writeNpy(`${dir}/scalars.npy`, scalars, [N, SCALAR_DIM], 'f4')
  writeNpy(`${dir}/actions.npy`, actions, [N, 2], 'u1')
  writeNpy(`${dir}/masks.npy`, masks, [N, MASK_DIM], 'u1')
  writeNpy(`${dir}/conditions.npy`, conditions, [N], 'u1')
  writeNpy(`${dir}/returns.npy`, returns, [N], 'f4')
  writeFileSync(`${dir}/manifest.json`, JSON.stringify(manifest, null, 2))
}

function compareShards(a: string, b: string): { ok: boolean; detail: string } {
  const files = readdirSync(a).filter((f) => f.endsWith('.npy'))
  for (const f of files) {
    const ba = readFileSync(`${a}/${f}`)
    const bb = readFileSync(`${b}/${f}`)
    if (ba.length !== bb.length)
      return { ok: false, detail: `${f} size ${ba.length} vs ${bb.length}` }
    if (Buffer.compare(ba, bb) !== 0) return { ok: false, detail: `${f} bytes differ` }
  }
  return { ok: true, detail: `${files.length} npy files byte-identical` }
}

function main(): void {
  const arg = (name: string, fallback?: string): string | undefined => {
    const i = process.argv.indexOf(`--${name}`)
    return i >= 0 ? process.argv[i + 1] : fallback
  }
  const has = (name: string): boolean => process.argv.includes(`--${name}`)

  const difficulty = arg('difficulty', 'hard')!
  const stageSpec = arg('stages', 'all')!
  const seedSpec = arg('seeds', '1-10')!
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const outDir = arg('out', 'tmp/godai-labels')!
  const winsOnly = (arg('wins', '1') ?? '1') === '1'
  // M1 探针证据：守家桶分歧率最高（base 74.6%）→ 濒危帧超采样默认 3×（守家回补）
  const nearMissTimes = parseInt(arg('near-miss-times', '3')!, 10)
  const determinism = has('verify-determinism')

  // 课程自定义关（`--stage-json-file <path>`）：JSON 数组，第 i 项对应 stage 2000+i
  // （decodeStageGrid 的 StageJson 载荷）。不传时逐字节不变（真实关表路径）——God AI
  // 语料此前只能采真实关，课程自定义关（无基地/单敌/自定义出生点）是唯一缺口。
  const stageJsonFile = arg('stage-json-file', '')!
  let customStages: string[] = []
  if (stageJsonFile) {
    const parsed: unknown = JSON.parse(readFileSync(stageJsonFile, 'utf-8'))
    if (!Array.isArray(parsed) || parsed.length === 0) {
      console.error('--stage-json-file 必须是非空的 stageJson 对象数组')
      process.exit(2)
    }
    customStages = parsed.map((x) => JSON.stringify(x))
  }
  const stageJsonFor = (stageId: number): string | undefined =>
    customStages.length ? customStages[stageId - CUSTOM_STAGE_BASE] : undefined
  const livesRaw = arg('lives-override', '')!
  const livesOverride = livesRaw === '' ? undefined : parseInt(livesRaw, 10)
  const levelRaw = arg('player-level', '')!
  const playerLevelOverride = levelRaw === '' ? undefined : parseInt(levelRaw, 10)

  let stages: number[]
  if (stageSpec === 'all') stages = STAGES.map((_, i) => i)
  // 自定义关：stage ID 就是 2000+i（不是 1-based 关卡号），不做 -1 换算
  else if (customStages.length) stages = parseRange(stageSpec)
  else stages = parseRange(stageSpec).map((s) => s - 1)
  const seeds = parseRange(seedSpec)
  if (seeds.length === 0 || stages.length === 0) {
    console.error(
      'usage: bun tools/sim/export-godai-labels.ts --stages all|N|N-M --seeds N|N-M [--out dir] [--wins 0|1] [--near-miss-times N] [--verify-determinism]',
    )
    process.exit(2)
  }

  if (determinism) {
    // Gate ②: re-run each (stage, first seed) twice and byte-compare (wins off
    // so the shard set is outcome-independent and non-trivial).
    const detA = `${outDir}/__det_a`
    const detB = `${outDir}/__det_b`
    rmSync(detA, { recursive: true, force: true })
    rmSync(detB, { recursive: true, force: true })
    let fails = 0
    const results: string[] = []
    let kept = 0
    for (const si of stages) {
      const seed = seeds[0]
      const label = `s${si + 1}_seed${seed}`
      const shardName = `shard_${label}`
      for (const target of [detA, detB]) {
        const res = exportGame(
          seed,
          si,
          difficulty,
          maxTicks,
          false,
          1,
          stageJsonFor(si),
          livesOverride,
          playerLevelOverride,
        )
        if (res !== 'loss-skipped') {
          flushShard(res, `${target}/${shardName}`, shardName, {
            stage: si,
            seed,
            difficulty,
            totalTicks: 0,
            outcome: res.outcome,
          })
        }
      }
      const cmp = compareShards(`${detA}/${shardName}`, `${detB}/${shardName}`)
      if (cmp.detail.startsWith('0 npy files')) {
        results.push(`[det-empty] ${label}: no shards (outcome skipped in non-wins mode)`)
        continue
      }
      if (!cmp.ok) fails++
      else kept++
      results.push(`[${cmp.ok ? 'DET OK' : 'DET FAIL'}] ${label}: ${cmp.detail}`)
    }
    console.log(results.join('\n'))
    console.log(`\n=== determinism check ===`)
    console.log(`games=${stages.length} byteIdentical=${kept} fails=${fails}`)
    writeFileSync(
      `${outDir}/_determinism_report.json`,
      JSON.stringify({ games: stages.length, fails, env: ENV, results }, null, 2),
    )
    process.exit(fails > 0 ? 1 : 0)
  }

  mkdirSync(outDir, { recursive: true })
  let totalSamples = 0
  let totalNearMiss = 0
  let totalRawSamples = 0
  let games = 0
  let keptGames = 0
  let skippedLoss = 0
  const perFile: string[] = []
  const t0 = Date.now()
  const outcomes: Record<string, number> = {}
  for (const si of stages) {
    for (const seed of seeds) {
      const res = exportGame(
        seed,
        si,
        difficulty,
        maxTicks,
        winsOnly,
        nearMissTimes,
        stageJsonFor(si),
        livesOverride,
        playerLevelOverride,
      )
      games++
      if (res === 'loss-skipped') {
        skippedLoss++
        continue
      }
      keptGames++
      totalSamples += res.samples.length
      totalNearMiss += res.nearMissFrames
      totalRawSamples +=
        res.samples.length - res.nearMissFrames * (nearMissTimes > 1 ? nearMissTimes - 1 : 0)
      outcomes[res.outcome] = (outcomes[res.outcome] ?? 0) + 1
      const name = `shard_s${String(si + 1).padStart(2, '0')}_seed${String(seed).padStart(3, '0')}`
      flushShard(res, `${outDir}/${name}`, name, {
        stage: si,
        seed,
        difficulty,
        teacher: 'god-ai',
        outcome: res.outcome,
        gatedScore: res.gatedScore,
        score: res.score,
        nearMissFrames: res.nearMissFrames,
      })
      perFile.push(
        `[OK] stage=${si + 1} seed=${seed} samples=${res.samples.length} nearMiss=${res.nearMissFrames} outcome=${res.outcome}`,
      )
    }
  }
  const elapsed = ((Date.now() - t0) / 1000).toFixed(1)

  // ---- 状态覆盖率报告（M2 验收 ⑩）：高压/濒危帧占比 ----
  const nearMissFrac = totalRawSamples > 0 ? totalNearMiss / totalRawSamples : 0
  const coverage = {
    winsOnly,
    nearMissTimes,
    corpusRawFrames: totalRawSamples,
    nearMissFrames: totalNearMiss,
    nearMissFrac: +nearMissFrac.toFixed(4),
    // 参考触发线（plan §5 验收）：高压桶占比 < 50% 或样本 < 2000 帧 → 承压关定向补录
    gate: {
      belowHalf: nearMissFrac < 0.5,
      belowMinSamples: totalNearMiss < 2000,
    },
  }
  console.log(perFile.join('\n'))
  console.log(`\n=== export summary ===`)
  console.log(
    `games=${games} kept=${keptGames} lossSkipped=${skippedLoss} samples=${totalSamples} elapsed=${elapsed}s winsOnly=${winsOnly} nearMissTimes=${nearMissTimes}`,
  )
  console.log(
    `coverage: rawFrames=${totalRawSamples} nearMiss=${totalNearMiss} frac=${nearMissFrac.toFixed(4)}`,
  )
  console.log(`shards written under: ${outDir}`)
  writeFileSync(
    `${outDir}/_export_report.json`,
    JSON.stringify(
      {
        games,
        keptGames,
        lossSkipped: skippedLoss,
        totalSamples,
        elapsedSec: Number(elapsed),
        stages: stages.map((s) => s + 1),
        seeds,
        difficulty,
        winsOnly,
        nearMissTimes,
        outcomes,
        coverage,
        outDir,
        env: ENV,
      },
      null,
      2,
    ),
  )
}

if (import.meta.main) {
  main()
}
