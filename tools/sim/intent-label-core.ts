/**
 * intent-label-core.ts — 机械意图 tagger 核心（M0b，plan/Intent-Policy-NN-Plan.md
 * §3.6）。worker 壳（export-intent-labels-worker.ts）与测试共用本模块——对齐
 * score-gate-core 模式：逻辑在 core、可单测，worker 只留消息壳。
 *
 * 每个 job = 一局 (stage, seed)：两遍法（①全量逐 tick 打标+分段 → ②确定性重跑
 * 在采样帧编码 obs）。任务为纯函数（fresh World + 独立 RNG），线程归属不影响
 * 结果（AGENTS §2.2/§2.3，worker-pool 确定性注记）。
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, BASE_POS } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import {
  segmentIntents,
  expandSegments,
  forwardMapLabel,
  INTENT_IDS,
  type TagFrame,
} from '../../src/ai/intent/vocab'
import { ObsEncoder, SCALAR_DIM } from '../../src/nn/obs-encoder'
import { writeNpy } from '../../src/nn/npy'
import { mkdirSync, writeFileSync, existsSync } from 'fs'
import { CELL } from '../../src/constants'

const OBS_N = 14 * 26 * 26

// ─── divergence-probe 三桶（同款谓词/半径/优先级，禁两份口径漂移）──────────
// tools/diag/divergence-probe.ts：base(环损∨敌距基地≤12 Manhattan) > combat
// (敌距玩家≤14 欧氏 ∨ 存活敌弹) > cruise。
const BASE_PRESSURE_RADIUS = 12
const ENGAGE_RADIUS = 14

export function probeBucketOf(world: World, sc: Float32Array): number {
  const enemyNearBase = world.tanks.some((e) => {
    if (!e.alive || e.spawnTimer > 0 || e.allegiance !== 'enemy') return false
    return (
      Math.abs(Math.floor((e.x + 16) / CELL) - BASE_POS.col) +
        Math.abs(Math.floor((e.y + 16) / CELL) - BASE_POS.row) <=
      BASE_PRESSURE_RADIUS
    )
  })
  if (sc[6] < 1 || enemyNearBase) return 0 // base
  const p = world.player
  let enemyNearPlayer = false
  if (p) {
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    enemyNearPlayer = world.tanks.some((e) => {
      if (!e.alive || e.spawnTimer > 0 || e.allegiance !== 'enemy') return false
      const dx = e.x + e.w / 2 - pcx
      const dy = e.y + e.h / 2 - pcy
      return Math.sqrt(dx * dx + dy * dy) <= ENGAGE_RADIUS * CELL
    })
  }
  const enemyBullet = world.bullets.some((b) => b.alive && b.allegiance === 'enemy')
  return enemyNearPlayer || enemyBullet ? 1 : 2
}

export interface TaggerJob {
  id: number
  si: number
  seed: number
}

export interface TaggerPayload {
  jobs: TaggerJob[]
  difficulty: string
  maxTicks: number
  gridPeriod: number
  shardDir: string
  force: boolean
}

export interface TaggerAggregate {
  id: number
  si: number
  seed: number
  outcome: string
  ticks: number
  sampled: number
  windows: Record<string, number>
  windowCount: number
  flipFrames: number
  flipComparable: number
}

interface TagFrameLite {
  label: string
  combat: {
    isBaseUnderThreat: boolean
    playerDistToBase: number
    maxPlayerDistFromBase: number
    isEndgame: boolean
  } | null
}

/** 与 simulation-runner / export-godai-labels 同款 World 装载序列。 */
function loadWorld(
  seed: number,
  si: number,
  difficulty: string,
): {
  world: World
  god: GodAIInput
  sim: Simulation
} {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const god = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, godRng)
  const sim = new Simulation(world, god)
  world.loadStageData(STAGES[si], si)
  god.reset()
  return { world, god, sim }
}

/** 读本 tick 决策（pre-endFrame：_lastBranch 是本 tick 基于 tick 前状态的决策）。 */
function readFrame(god: GodAIInput): TagFrameLite {
  const label = god._lastBranch
  const m = forwardMapLabel(label)
  let combat: TagFrameLite['combat'] = null
  if (m.kind === 'combat-chain') {
    const pc = god.playerCell()
    combat = {
      isBaseUnderThreat: god.hasBase && god.isBaseUnderThreat(),
      playerDistToBase: Math.abs(pc.col - BASE_POS.col) + Math.abs(pc.row - BASE_POS.row),
      maxPlayerDistFromBase: god.params.maxPlayerDistFromBase,
      isEndgame: god.world.enemiesRemaining <= god.params.endgameEnemyThreshold,
    }
  }
  return { label, combat }
}

export function processJob(payload: TaggerPayload, job: TaggerJob): TaggerAggregate {
  const { difficulty, maxTicks, gridPeriod, shardDir, force } = payload
  const { si, seed } = job

  // ---- 第一遍：逐 tick 打标流 → 分段（自然分布口径的窗口计数底座）----
  const first = loadWorld(seed, si, difficulty)
  const frames: TagFrame[] = []
  let outcome = 'timeout'
  for (let t = 0; t < maxTicks; t++) {
    if (first.world.state !== 'playing') break
    first.sim.tick()
    frames.push(readFrame(first.god))
    first.god.endFrame()
    const st: string = first.world.state
    if (st === 'stageclear') {
      outcome = 'stage_clear'
      break
    }
    if (st === 'gameover') {
      outcome = first.world.tileMap.isBaseDestroyed() ? 'base_destroyed' : 'lives_exhausted'
      break
    }
  }

  const segments = segmentIntents(frames)
  const windows: Record<string, number> = {}
  for (const s of segments) windows[s.intent] = (windows[s.intent] ?? 0) + 1

  // 时序翻转指标：±5 tick 标签不同帧占比。
  const expanded = expandSegments(segments, frames.length)
  let flipFrames = 0
  for (let i = 0; i < expanded.length; i++) {
    const j = Math.min(i + 5, expanded.length - 1)
    const k = Math.max(i - 5, 0)
    if (expanded[i] !== expanded[j] || expanded[i] !== expanded[k]) flipFrames++
  }

  // 采样帧集合 = 均匀网格 ∪ 段首边界帧。
  const sampledSet = new Set<number>()
  for (let f = 0; f < frames.length; f += gridPeriod) sampledSet.add(f)
  for (const s of segments) sampledSet.add(s.start)
  const idxList = [...sampledSet].sort((a, b) => a - b)

  // ---- 第二遍：确定性重跑，采样帧编码 obs（obs(t)=tick 前状态，§1.3 口径）----
  const dirName = `${shardDir}/s${String(si + 1).padStart(2, '0')}-seed${seed}-${difficulty}`
  if (!force && existsSync(`${dirName}/manifest.json`)) {
    return {
      id: job.id,
      si,
      seed,
      outcome,
      ticks: frames.length,
      sampled: idxList.length,
      windows,
      windowCount: segments.length,
      flipFrames,
      flipComparable: Math.max(0, expanded.length),
    }
  }

  const second = loadWorld(seed, si, difficulty)
  const encoder = new ObsEncoder()
  const obsOut = new Uint8Array(idxList.length * OBS_N)
  const scalarsOut = new Float32Array(idxList.length * SCALAR_DIM)
  const intentOut = new Uint8Array(idxList.length)
  const bucketOut = new Uint8Array(idxList.length)
  const frameOut = new Float64Array(idxList.length)
  const wantSet = new Set(idxList)
  let written = 0
  const frames2: TagFrame[] = []
  for (let t = 0; t < maxTicks; t++) {
    if (second.world.state !== 'playing') break
    const n = wantSet.has(t) ? written++ : -1
    if (n >= 0) {
      encoder.encode(second.world)
      bucketOut[n] = probeBucketOf(second.world, encoder.scalars)
    }
    second.sim.tick()
    frames2.push(readFrame(second.god))
    if (n >= 0) {
      obsOut.set(encoder.obs, n * OBS_N)
      scalarsOut.set(encoder.scalars, n * SCALAR_DIM)
      frameOut[n] = t
    }
    second.god.endFrame()
    const st: string = second.world.state
    if (st !== 'playing') break
  }

  // 两遍一致性断言（确定性自检）：轨迹决策流必须逐帧相同。
  if (frames2.length !== frames.length)
    throw new Error(
      `two-pass divergence @s${si + 1} seed${seed}: ${frames.length} vs ${frames2.length} frames`,
    )
  const segments2 = segmentIntents(frames2)
  const expanded2 = expandSegments(segments2, frames2.length)
  for (let n = 0; n < idxList.length; n++) {
    const v = expanded2[idxList[n]]
    if (v == null) throw new Error(`sampled frame ${idxList[n]} outside segment coverage`)
    intentOut[n] = INTENT_IDS.indexOf(v)
  }

  mkdirSync(dirName, { recursive: true })
  writeNpy(`${dirName}/obs.npy`, obsOut, [idxList.length, 14, 26, 26], 'u1')
  writeNpy(`${dirName}/scalars.npy`, scalarsOut, [idxList.length, SCALAR_DIM], 'f4')
  writeNpy(`${dirName}/intent.npy`, intentOut, [idxList.length], 'u1')
  writeNpy(`${dirName}/bucket.npy`, bucketOut, [idxList.length], 'u1')
  writeNpy(`${dirName}/frame.npy`, frameOut, [idxList.length], 'f8')
  writeFileSync(
    `${dirName}/manifest.json`,
    JSON.stringify(
      {
        exporter: 'export-intent-labels',
        exporterVersion: '0.2.0',
        stage: si + 1,
        seed,
        difficulty,
        outcome,
        ticks: frames.length,
        gridPeriod,
        sampled: idxList.length,
        segments,
      },
      null,
      2,
    ),
  )

  return {
    id: job.id,
    si,
    seed,
    outcome,
    ticks: frames.length,
    sampled: idxList.length,
    windows,
    windowCount: segments.length,
    flipFrames,
    flipComparable: Math.max(0, expanded.length),
  }
}
