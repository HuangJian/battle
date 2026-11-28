/**
 * nn-branch-headroom.ts — P′-2b0：NN→NN 单拍 24 动作 headroom（零训练测量仪器）。
 *
 * 问题：在 NN 自己的轨迹上，把**某一拍**的动作替换成 24 个动作之一（hold2 × move4 ×
 * fire3），然后交还 NN 继续跑 H 拍，能比 NN 实际动作好多少？这就是低层策略的 headroom
 * （P′-2b0；plan/nn-hierarchical-options.plan.md §9.5）。
 *
 * 契约：
 *   · 强制拍走正式钩子 `ForcedTickInput`（P′-0），不碰 Simulation/World；
 *   · 每个分支 = `cloneWorld` + `NNInput.captureDecisionState` 起跑，结束后
 *     `restoreWorld` + `restoreDecisionState`（CRN：世界 RNG 共享快照；NN 贪心无 RNG）；
 *   · self-check：NN 实际动作分支必须与「不强制」分支在 H 拍后逐字段一致，否则响亮失败；
 *   · 评分主读数 = Σ γ^k·ΔΦ + γ^H·V(s_H)（Φ 与训练同式：tools/sim/rl-reward.ts），
 *     终局用 scoreRun 的 gated 分折现；副读数 = raw terminal。
 *   · 状态分层（opening_300 / base_pressure / ring_enemy / combat / free_nav）配额采样。
 *
 * 语料纪律（AGENTS §15）：pilot 与 confirm 用不相交 seed 段（`--seed0` 推移）。
 *
 * Usage:
 *   bun tools/sim/nn-branch-headroom.ts --weights <file> --states 500 --horizon 60 \
 *       --out tmp/p2b0/pilot-h60.jsonl --seed0 1000000001 --games 20
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { BASE_POS, CELL, START_LIVES, ENEMIES_PER_STAGE } from '../../src/constants'
import { NNInput } from '../../src/nn/policy-input'
import { MOVE4_DECODE } from '../../src/nn/action-space-v6'
import { sampleBasePressure } from '../../src/nn/danger-metrics'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import type { Direction } from '../../src/constants'
import { ForcedTickInput, type ForcedAction } from './forced-input'
import {
  BASE_LOSS_MULT,
  RL_GAMMA,
  REWARD_SCALE,
  countBaseWall,
  phiNow,
  type PhiCounters,
} from './rl-reward'
import { scoreRun, V7_SCORE_CONFIG } from '../eval/godai-score'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

const MAX_TICKS = 36000
const DECISION_K = 10
const PRESSURE_SAMPLE_TICKS = 6

/** 状态分层（顺序即判据优先级）。 */
export const STRATA = ['opening_300', 'base_pressure', 'ring_enemy', 'combat', 'free_nav'] as const
export type Stratum = (typeof STRATA)[number]

/** 动作编码：hold(2) × move(4) × fire(3) = 24；index = hold*12 + move*3 + fire。 */
export const ACTION_COUNT = 24

export function actionOf(
  index: number,
): ForcedAction & { holdIdx: number; moveIdx: number; fireIdx: number } {
  const holdIdx = Math.floor(index / 12)
  const rem = index % 12
  const moveIdx = Math.floor(rem / 3)
  const fireIdx = rem % 3
  const dir = MOVE4_DECODE[moveIdx] as Direction
  return {
    holdIdx,
    moveIdx,
    fireIdx,
    moveDir: dir,
    fire: fireIdx !== 0,
    fireBeforeTurn: fireIdx === 1,
    holding: holdIdx === 1,
  }
}

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function flag(name: string): boolean {
  return process.argv.includes(`--${name}`)
}

export interface Tel {
  enemyTotal: number
  startLives: number
  playerShots: number
  powerUpsSpawned: number
  powerUpsCollected: number
  baseWallTotal: number
  basePressureSum: number
  basePressureSamples: number
  firstKillTick: number | undefined
}

export function phiOf(tel: Tel, world: World): number {
  const c: PhiCounters = {
    enemyTotal: tel.enemyTotal,
    startLives: tel.startLives,
    kills: world.killCount,
    lives: world.lives,
    ticks: world.frame,
    baseAlive: !world.tileMap.isBaseDestroyed(),
    baseWallTotal: tel.baseWallTotal,
    baseWallIntact: countBaseWall(world),
    playerShots: tel.playerShots,
    powerUpsCollected: tel.powerUpsCollected,
    powerUpsSpawned: tel.powerUpsSpawned,
    basePressureMean:
      tel.basePressureSamples > 0 ? tel.basePressureSum / tel.basePressureSamples : 0,
    basePressureSamples: tel.basePressureSamples,
    firstKillTick: tel.firstKillTick,
  }
  return phiNow(c)
}

/** 无 id 的世界签名（self-check 用：分支之间新生成实体的 id 必然不同）。 */
export function worldSig(w: World): string {
  const parts: Array<number | string> = [
    w.frame,
    w.killCount,
    w.lives,
    w.baseHp,
    w.state,
    w.rng.getState(),
  ]
  const p = w.player
  if (p) parts.push(p.x, p.y, p.dir, p.hp, p.alive ? 1 : 0, p.lastFire)
  const tanks = w.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.isPlayer) continue
    parts.push(t.kind, t.x, t.y, t.dir, t.hp, t.alive ? 1 : 0, t.spawnTimer)
  }
  for (let i = 0; i < w.bullets.length; i++) {
    const b = w.bullets[i]
    if (!b.alive) continue
    parts.push(b.x, b.y, b.dir, b.isPlayer ? 1 : 0)
  }
  return parts.join(',')
}

export function outcomeOf(world: World): string {
  if (world.state === 'stageclear' || world.state === 'victory') return 'stage_clear'
  if (world.state === 'gameover')
    return world.tileMap.isBaseDestroyed() ? 'base_destroyed' : 'lives_exhausted'
  return 'max_ticks'
}

/**
 * 环内有敌（P′-2b 的 `ring_enemy` 层）：任一活敌的曼哈顿格距 ≤2。
 * 判据必须先于 `base_pressure`——否则「贴近基地」的状态会被压力层全部吃掉，
 * ring_enemy 永远采不到（pilot 首跑实测 n=0）。
 */
function ringEnemy(world: World): boolean {
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.isPlayer || t.spawnTimer > 0) continue
    const col = Math.floor((t.x + t.w / 2) / CELL)
    const row = Math.floor((t.y + t.h / 2) / CELL)
    if (Math.abs(col - BASE_POS.col) + Math.abs(row - BASE_POS.row) <= 2) return true
  }
  return false
}

function combatNow(world: World): boolean {
  const p = world.player
  if (!p || !p.alive) return false
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.isPlayer || t.spawnTimer > 0) continue
    const d = Math.abs(t.x + t.w / 2 - pcx) + Math.abs(t.y + t.h / 2 - pcy)
    if (d <= 4 * CELL) return true
  }
  for (let i = 0; i < world.bullets.length; i++) {
    const b = world.bullets[i]
    if (!b.alive || b.isPlayer) continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical ? Math.abs(bx - pcx) < CELL * 0.75 : Math.abs(by - pcy) < CELL * 0.75
    const approaching =
      (b.dir === 'down' && by < pcy) ||
      (b.dir === 'up' && by > pcy) ||
      (b.dir === 'right' && bx < pcx) ||
      (b.dir === 'left' && bx > pcx)
    if (aligned && approaching) return true
  }
  return false
}

export function classify(world: World): Stratum {
  if (world.frame <= 300) return 'opening_300'
  if (ringEnemy(world)) return 'ring_enemy'
  if (sampleBasePressure(world) >= 0.5) return 'base_pressure'
  if (combatNow(world)) return 'combat'
  return 'free_nav'
}

interface BranchResult {
  score: number
  v: number | null
  terminal: string | null
  kills: number
  deaths: number
  wallLoss: number
  sig: string
}

interface SampleRow {
  stage: number
  seed: number
  tick: number
  stratum: Stratum
  actual: number
  best: number
  headroom: number
  vActual: number | null
  scores: number[]
}

/** 单局：跑 NN 基线并在采样点做 24 动作分支。返回本局新增的采样行。 */
function runGame(
  stageIndex: number,
  seed: number,
  opts: {
    weightsPath: string
    horizon: number
    gamma: number
    maxTicks: number
    difficulty: string
    playerLevel: number
    quotas: Record<Stratum, number>
    used: Record<Stratum, number>
    maxStatesPerGame: number
    selfCheck: boolean
  },
): SampleRow[] {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = opts.difficulty
  world.difficulty = DIFFICULTIES[opts.difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[opts.difficulty] ?? DEFAULT_RULES
  world.playerLevel = opts.playerLevel
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const input = new NNInput(world, { weightsPath: opts.weightsPath, decisionK: DECISION_K })
  const sim = new Simulation(world, input)
  const stage = STAGES[stageIndex]
  world.loadStageData(stage, stageIndex)
  input.reset()

  const rows: SampleRow[] = []
  const tel: Tel = {
    enemyTotal: stage.enemyCount ?? ENEMIES_PER_STAGE,
    startLives: world.lives,
    playerShots: 0,
    powerUpsSpawned: 0,
    powerUpsCollected: 0,
    baseWallTotal: countBaseWall(world),
    basePressureSum: 0,
    basePressureSamples: 0,
    firstKillTick: undefined,
  }
  const seenPowerUps = new Set<number>()
  let statesThisGame = 0

  for (let t = 0; t < opts.maxTicks; t++) {
    if (world.state !== 'playing') break
    sim.tick()
    input.endFrame()
    const events = world.consumeEvents()
    for (const e of events) {
      if (e.type === 'bullet_fired' && e.bullet.isPlayer) tel.playerShots++
      else if (e.type === 'powerup_collected') tel.powerUpsCollected++
      else if (e.type === 'tank_destroyed' && e.by === 'player' && tel.firstKillTick === undefined)
        tel.firstKillTick = world.frame
    }
    for (let i = 0; i < world.powerUps.length; i++) seenPowerUps.add(world.powerUps[i].id)
    tel.powerUpsSpawned = seenPowerUps.size
    if (world.frame % PRESSURE_SAMPLE_TICKS === 0) {
      tel.basePressureSum += sampleBasePressure(world)
      tel.basePressureSamples++
    }
    if (world.state !== 'playing') break

    const strat = classify(world)
    if (opts.used[strat] >= opts.quotas[strat]) continue
    if (statesThisGame >= opts.maxStatesPerGame) continue
    if (world.frame % DECISION_K !== 0) continue

    // 采样点：已消费本拍事件（队列空），分支 restore 清事件不会丢 telemetry。
    const snap = cloneWorld(world)
    const st = input.captureDecisionState()
    const actualMove = input.getMoveDirection()
    const actualHold = input.isHolding() ? 1 : 0
    const actualFire = input.isFiring() ? (input.isFireBeforeTurn() ? 1 : 2) : 0
    const actualMoveIdx = actualMove ? MOVE4_DECODE.indexOf(actualMove) : 0
    const actual = actualHold * 12 + Math.max(0, actualMoveIdx) * 3 + actualFire

    const scores: number[] = []
    let actualV: number | null = null

    const runBranch = (index: number, force: boolean): BranchResult => {
      restoreWorld(world, snap)
      input.restoreDecisionState(st)
      const telB: Tel = { ...tel }
      let prevPhi = phiOf(telB, world)
      let ret = 0
      let stepped = 0
      let terminal: string | null = null
      let wallNow = countBaseWall(world)
      let wallLoss = 0
      const kills0 = world.killCount
      const lives0 = world.lives

      for (let k = 0; k < opts.horizon; k++) {
        if (force && k === 0) {
          const forced = new ForcedTickInput(input, actionOf(index), 1)
          sim.input = forced
          sim.tick()
          forced.endFrame()
          sim.input = input
        } else {
          sim.tick()
          input.endFrame()
        }
        stepped++
        const ev = world.consumeEvents()
        for (const e of ev) {
          if (e.type === 'bullet_fired' && e.bullet.isPlayer) telB.playerShots++
          else if (e.type === 'powerup_collected') telB.powerUpsCollected++
          else if (
            e.type === 'tank_destroyed' &&
            e.by === 'player' &&
            telB.firstKillTick === undefined
          )
            telB.firstKillTick = world.frame
        }
        if (world.frame % PRESSURE_SAMPLE_TICKS === 0) {
          telB.basePressureSum += sampleBasePressure(world)
          telB.basePressureSamples++
        }
        const wall = countBaseWall(world)
        if (wall < wallNow) {
          wallLoss += wallNow - wall
          wallNow = wall
        }
        const phi = phiOf(telB, world)
        ret += Math.pow(opts.gamma, k) * (phi - prevPhi)
        prevPhi = phi
        if (world.state !== 'playing') {
          terminal = outcomeOf(world)
          const scored = scoreRun(
            {
              outcome: terminal,
              ticks: world.frame,
              finalState: {
                killCount: world.killCount,
                lives: world.lives,
                baseAlive: !world.tileMap.isBaseDestroyed(),
              },
              firstKillTick: telB.firstKillTick,
            },
            V7_SCORE_CONFIG,
          )
          const gated = terminal === 'base_destroyed' ? scored.score * BASE_LOSS_MULT : scored.score
          ret += Math.pow(opts.gamma, stepped) * REWARD_SCALE * gated
          break
        }
      }
      const vEnd = terminal === null ? input.valueEstimate() : null
      if (vEnd !== null) ret += Math.pow(opts.gamma, stepped) * vEnd
      if (force && index === actual) actualV = vEnd
      return {
        score: ret,
        v: vEnd,
        terminal,
        kills: world.killCount - kills0,
        deaths: Math.max(0, lives0 - world.lives),
        wallLoss,
        sig: worldSig(world),
      }
    }

    // 逐分支（每个候选都从同一快照重新起跑）。
    let best = 0
    let bestScore = -Infinity
    let actualRes: BranchResult | null = null
    for (let a = 0; a < ACTION_COUNT; a++) {
      const res = runBranch(a, true)
      scores.push(res.score)
      if (a === actual) actualRes = res
      if (res.score > bestScore) {
        bestScore = res.score
        best = a
      }
    }

    // self-check：实际动作分支 ≡ 不强制分支（逐字段）。
    if (opts.selfCheck) {
      const control = runBranch(actual, false)
      if (control.sig !== actualRes!.sig) {
        throw new Error(
          `[b0] self-check failed at stage ${stageIndex} seed ${seed} tick ${world.frame}: ` +
            `actual-action branch != no-force branch`,
        )
      }
    }

    // 恢复主现场（分支循环最后一次已 restore；再保险一次）。
    restoreWorld(world, snap)
    input.restoreDecisionState(st)
    sim.input = input

    rows.push({
      stage: stageIndex,
      seed,
      tick: world.frame,
      stratum: strat,
      actual,
      best,
      headroom: bestScore - scores[actual],
      vActual: actualV,
      scores,
    })
    opts.used[strat]++
    statesThisGame++
    if (STRATA.every((s) => opts.used[s] >= opts.quotas[s])) break
  }
  return rows
}

/** 均值 + 正态近似 CI（90/95 由 z 决定；筛查用 90%，确认用 95%）。 */
export function meanCi(
  values: number[],
  z: number,
): { mean: number; lo: number; hi: number; n: number } {
  const n = values.length
  if (n === 0) return { mean: 0, lo: 0, hi: 0, n: 0 }
  let sum = 0
  for (const v of values) sum += v
  const mean = sum / n
  if (n < 2) return { mean, lo: mean, hi: mean, n }
  let ss = 0
  for (const v of values) ss += (v - mean) * (v - mean)
  const se = Math.sqrt(ss / (n - 1) / n)
  return { mean, lo: mean - z * se, hi: mean + z * se, n }
}

function main(): void {
  const weights = arg('weights')
  if (!weights) {
    process.stderr.write('[b0] --weights is required\n')
    process.exit(2)
  }
  const states = parseInt(arg('states', '500')!, 10)
  const horizon = parseInt(arg('horizon', '60')!, 10)
  const games = parseInt(arg('games', '20')!, 10)
  const seed0 = parseInt(arg('seed0', '1000000001')!, 10)
  const stageStart = parseInt(arg('stage-start', '2000')!, 10)
  const stageCount = parseInt(arg('stage-count', '35')!, 10)
  const maxStatesPerGame = parseInt(arg('max-states-per-game', '40')!, 10)
  const maxTicks = parseInt(arg('max-ticks', String(MAX_TICKS))!, 10)
  const difficulty = arg('difficulty', 'hard')!
  const playerLevel = parseInt(arg('player-level', '0')!, 10)
  const gamma = parseFloat(arg('gamma', String(RL_GAMMA))!)
  const out = arg('out', 'tmp/p2b0/b0.jsonl')!
  const selfCheck = !flag('no-self-check')
  if (!Number.isInteger(states) || states < 1 || !Number.isInteger(horizon) || horizon < 1) {
    process.stderr.write('[b0] --states/--horizon must be positive integers\n')
    process.exit(2)
  }
  const quota = Math.ceil(states / STRATA.length)
  const quotas = Object.fromEntries(STRATA.map((s) => [s, quota])) as Record<Stratum, number>
  const used = Object.fromEntries(STRATA.map((s) => [s, 0])) as Record<Stratum, number>

  const t0 = Date.now()
  const rows: SampleRow[] = []
  for (let g = 0; g < games; g++) {
    if (STRATA.every((s) => used[s] >= quotas[s])) break
    const stageIndex = stageStart - 2000 + (g % stageCount)
    const seed = seed0 + Math.floor(g / stageCount)
    const got = runGame(stageIndex, seed, {
      weightsPath: weights,
      horizon,
      gamma,
      maxTicks,
      difficulty,
      playerLevel,
      quotas,
      used,
      maxStatesPerGame,
      selfCheck,
    })
    rows.push(...got)
    process.stderr.write(
      `[b0] game ${g + 1}/${games} s${stageIndex + 2000} seed${seed}: +${got.length} states ` +
        `(${rows.length}/${states}) elapsed ${((Date.now() - t0) / 1000).toFixed(0)}s\n`,
    )
  }

  mkdirSync(dirname(out), { recursive: true })
  writeFileSync(out, rows.map((r) => JSON.stringify(r)).join('\n') + '\n', 'utf8')

  const z = states >= 1000 ? 1.96 : 1.645 // 筛查 90% / 确认 95%（预注册 p2b）
  const byStratum: Record<string, unknown> = {}
  for (const s of STRATA) {
    const sub = rows.filter((r) => r.stratum === s)
    const hr = sub.map((r) => r.headroom)
    const changed = sub.filter((r) => r.best !== r.actual).length
    byStratum[s] = {
      ...meanCi(hr, z),
      positiveRate: sub.length > 0 ? +(changed / sub.length).toFixed(4) : 0,
      meanV: meanCi(
        sub.map((r) => r.vActual ?? 0),
        z,
      ).mean,
    }
  }
  const all = rows.map((r) => r.headroom)
  const summary = {
    weights,
    states: rows.length,
    targetStates: states,
    horizon,
    gamma,
    seed0,
    games: games,
    strata: byStratum,
    overall: meanCi(all, z),
    elapsedSec: +((Date.now() - t0) / 1000).toFixed(1),
    statesPerSec: rows.length > 0 ? +(rows.length / ((Date.now() - t0) / 1000)).toFixed(2) : 0,
    out,
  }
  console.log(JSON.stringify(summary, null, 2))
}

if (import.meta.main) main()
