/**
 * rl-reward.ts — RL reward 定义 + return 折现（M2 ⑥，plan/AI-No-Items-Warmstart.md §5）。
 *
 * 供语料导出器（export-godai-labels.ts / export-observations.ts）在逐决策帧结算
 * RL reward（与 `tools/sim/export-rl-rollout.ts` 完全同一套定义），再按 PPO 的
 * GAMMA 反向折扣累积成 return 落盘（shard 内 `returns.npy`），作为 M3 BC 的
 * value 头 MC 预置目标（P0③）与 M4 的 value 热启动数据。
 *
 * 定义（镜像 export-rl-rollout / ppo.py，禁止两处漂移）：
 *   Φ(s)   = REWARD_SCALE × V7_LOSS_BAND_MAX × Q_partial(s)
 *            ×（baseAlive ? 1 : BASE_LOSS_MULT）
 *   r_t    = Φ(t) − Φ(t−1)                        （决策帧间势差）
 *   终局项 = REWARD_SCALE × gatedScore − Σ r       （Σr ≡ SCALE × gatedScore 恒等式）
 *   G_t    = Σ_{k≥t} γ^(k−t) r_k                  （γ = RL_GAMMA = ppo.GAMMA）
 */
import {
  DEFAULT_STAGE_REFS,
  V7_SCORE_CONFIG,
  type DimensionKey,
  type Weights,
} from '../eval/godai-score'
import { BASE_POS, CELL } from '../../src/constants'
import type { World } from '../../src/game/World'

export const REWARD_SCALE = 10 // 与 export-rl-rollout 相同
export const BASE_LOSS_MULT = 0.1 // F3：base_destroyed 局势 Φ ×0.1（守家目标化）
export const RL_GAMMA = 0.995 // 必须与 ppo.py GAMMA 一致（K=10 决策间隔下 33s 信用时域）

// R6 守家优先败局带（与 export-rl-rollout 的 RL_LOSS_WEIGHTS 逐值一致）。
export const RL_LOSS_WEIGHTS: Weights = {
  progress: 0.3,
  baseIntegrity: 0.25,
  baseSafety: 0.25,
  tempo: 0.08,
  accuracy: 0.06,
  openingTempo: 0.03,
  loot: 0.03,
}

export const TELEMETRY_SAMPLE_TICKS = 6
export const BASE_PRESSURE_RADIUS = 12 // 与 export-rl-rollout 同半径

export interface PhiCounters {
  enemyTotal: number
  startLives: number
  kills: number
  lives: number
  ticks: number
  baseAlive: boolean
  baseWallTotal: number
  baseWallIntact: number
  playerShots: number
  powerUpsCollected: number
  powerUpsSpawned: number
  basePressureMean: number
  basePressureSamples: number
  firstKillTick: number | undefined
}

function clamp01(x: number): number {
  if (!Number.isFinite(x)) return 0
  return x < 0 ? 0 : Math.min(x, 1)
}

function ramp(x: number, lo: number, hi: number): number {
  if (hi === lo) return x >= hi ? 1 : 0
  return clamp01((x - lo) / (hi - lo))
}

export function isSolid(world: World, col: number, row: number): boolean {
  const t = world.tileMap.get(col, row)
  return t === 'brick' || t === 'steel'
}

/** 与 runner/export-rl-rollout 相同的环格定义（8 格保护圈，brick/steel 算完整）。 */
export function countBaseWall(world: World): number {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  let n = 0
  for (let col = bc - 1; col <= bc + 2; col++) if (isSolid(world, col, br - 1)) n++
  for (let row = br; row <= br + 1; row++) {
    if (isSolid(world, bc - 1, row)) n++
    if (isSolid(world, bc + 2, row)) n++
  }
  return n
}

export function sampleBasePressure(world: World): number {
  if (!world.tileMap.hasBase()) return 0
  let worst = 0
  for (const t of world.tanks) {
    if (!t.alive || t.spawnTimer > 0) continue
    const col = Math.floor((t.x + t.w / 2) / CELL)
    const row = Math.floor((t.y + t.h / 2) / CELL)
    const dist = Math.abs(col - BASE_POS.col) + Math.abs(row - BASE_POS.row)
    const p = 1 - dist / BASE_PRESSURE_RADIUS
    if (p > worst) worst = p
  }
  return worst > 0 ? Math.min(1, worst) : 0
}

/** 当前计数器下 losses 带部分质量（权重重分配规则镜像 godai-score.weightedQuality）。 */
export function lossPartialQ(c: PhiCounters, w: Weights = RL_LOSS_WEIGHTS): number {
  let acc = 0
  let wsum = 0
  const add = (key: DimensionKey, v: number | null): void => {
    const weight = w[key]
    if (v === null || weight === undefined || weight <= 0) return
    acc += weight * v
    wsum += weight
  }
  const minutes = c.ticks / 3600
  add('progress', c.enemyTotal > 0 ? clamp01(c.kills / c.enemyTotal) : null)
  add('lives', c.startLives > 0 ? clamp01(c.lives / c.startLives) : null)
  add(
    'baseIntegrity',
    !c.baseAlive
      ? 0
      : c.baseWallTotal > 0
        ? 0.55 + 0.45 * clamp01(c.baseWallIntact / c.baseWallTotal)
        : null,
  )
  add(
    'tempo',
    DEFAULT_STAGE_REFS.kpmRef > 0
      ? clamp01(minutes > 0 ? c.kills / minutes / DEFAULT_STAGE_REFS.kpmRef : 0)
      : null,
  )
  add(
    'accuracy',
    c.playerShots > 0 && DEFAULT_STAGE_REFS.accuracyRef > 0
      ? clamp01(c.kills / c.playerShots / DEFAULT_STAGE_REFS.accuracyRef)
      : null,
  )
  add('loot', c.powerUpsSpawned > 0 ? clamp01(c.powerUpsCollected / c.powerUpsSpawned) : null)
  add(
    'baseSafety',
    c.basePressureSamples > 0 ? clamp01(1 - c.basePressureMean / c.basePressureSamples) : null,
  )
  add('openingTempo', c.firstKillTick === undefined ? 0 : 1 - ramp(c.firstKillTick, 0, 1800))
  return wsum > 0 ? acc / wsum : 0
}

/** 势 Φ（F3 门控并入）。lossBandMax 取自 v7 打分配置（与 export-rl-rollout 同源）。 */
export function phiNow(c: PhiCounters): number {
  return (
    REWARD_SCALE *
    V7_SCORE_CONFIG.lossBandMax *
    lossPartialQ(c) *
    (c.baseAlive ? 1 : BASE_LOSS_MULT)
  )
}

/**
 * 逐决策帧 reward 序列 → γ 折扣 return（末帧 done 语义：G_T = r_T；无 bootstrap）。
 * 长度与 rewards 一致，G[i] = Σ_{k≥i} γ^(k−i) rewards[k]。
 */
export function discountReturns(rewards: number[], gamma = RL_GAMMA): number[] {
  const n = rewards.length
  const out = new Array<number>(n)
  let acc = 0
  for (let i = n - 1; i >= 0; i--) {
    acc = rewards[i] + gamma * acc
    out[i] = acc
  }
  return out
}
