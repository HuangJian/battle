#!/usr/bin/env bun
/**
 * a3-dose-probe.ts — P-1b control-side dose measurement for A3
 * (plan/nn-input-inference-rules.plan.md §4.1).
 *
 * Runs the BARE policy (NNInput, rules off) and, on the same trace, evaluates
 * the frozen `usefulFire` predicate at every rejected-turn tick. No rule is
 * applied to the simulation — every "fallback shot" below is a local
 * counterfactual, not an actual bullet.
 *
 * Usage:
 *   bun tools/diag/a3-dose-probe.ts \
 *     --weights tmp/g2-classic35/remote-jobs/230f0ed1fb418205/epoch-weights/ep80.json \
 *     --stage-json-file tmp/god-o2-four-stages.json --stages 0-3 --seeds 1-2 \
 *     --out tmp/a3-dose-pilot.jsonl
 *
 * Emits one JSON line per game (type=game) plus a final aggregate line.
 */
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { NNInput } from '../../src/nn/policy-input'
import { decodeStageGrid, type StageJson } from '../../src/nn/config-stage'
import { usefulFire, turnWillCommit } from '../../src/nn/inference-rules'
import { START_LIVES, TICK_MS, TURN_SENTINEL_MS, type Direction } from '../../src/constants'
import type { StageData } from '../../src/config/types'

interface GameDose {
  type: 'game'
  stage: number
  seed: number
  outcome: string
  ticks: number
  decisions: number
  aliveTicks: number
  intent2Decisions: number
  exposedDecisions: number
  rejectedTicks: number
  fallbackTicks: number
  fallbackReadyTicks: number
  suppressedTicks: number
  suppressedReadyTicks: number
  suppressedBlockedTicks: number
  bareShots: number
  a3ModeledShots: number
  netShotDelta: number
  fallbackWindows: number
  rLeKExposed: number
  rGtKExposed: number
  turningWindows: number
  intent2Turning: number
  intent2TurningR0: number
  intent2TurningRPosLeK: number
  intent2TurningRGtK: number
  firstAfterTurnDelaySum: number
  firstAfterTurnDelayCount: number
  wallSec: number
}

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function parseIntRange(spec: string): number[] {
  const [a, b] = spec.split('-').map((s) => parseInt(s, 10))
  if (!Number.isFinite(a)) return []
  if (b === undefined || !Number.isFinite(b)) return [a]
  const out: number[] = []
  for (let i = a; i <= b; i++) out.push(i)
  return out
}

/** Remaining turn-cooldown ticks for the UPCOMING tick (matches the engine clock). */
function remainingTurnTicks(world: World, p: NonNullable<World['player']>): number {
  const cd = world.rules?.turnCooldownMs ?? 0
  if (cd <= 0) return 0
  const now = (world.frame + 1) * TICK_MS
  const elapsed = now - (p.lastTurnMs ?? TURN_SENTINEL_MS)
  return elapsed >= cd ? 0 : (cd - elapsed) / TICK_MS
}

/** Same predicate as `obs-encoder.isFireReady`, evaluated on the upcoming tick. */
function fireReadyAt(world: World, p: NonNullable<World['player']>): boolean {
  const iv = p.nextFireInterval ?? 0
  if (!(iv > 0)) return false
  return (world.frame + 1) * TICK_MS - (p.lastFire ?? -9999) >= iv
}

function currentIntent(input: NNInput): 0 | 1 | 2 {
  if (!input.isFiring()) return 0
  return input.isFireBeforeTurn() ? 1 : 2
}

function runOne(
  world: World,
  stage: StageData,
  stageIndex: number,
  loadIndex: number,
  seed: number,
  difficulty: string,
  weightsPath: string,
  maxTicks: number,
  decisionK: number,
): GameDose {
  const t0 = Date.now()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const input = new NNInput(world, { weightsPath, decisionK })
  if (!input.usesHoldFire3()) {
    throw new Error('a3-dose-probe requires a v6 checkpoint (usesHoldFire3)')
  }
  const sim = new Simulation(world, input)
  world.loadStageData(stage, loadIndex)
  input.reset()

  const d: GameDose = {
    type: 'game',
    stage: stageIndex,
    seed,
    outcome: 'max_ticks',
    ticks: 0,
    decisions: 0,
    aliveTicks: 0,
    intent2Decisions: 0,
    exposedDecisions: 0,
    rejectedTicks: 0,
    fallbackTicks: 0,
    fallbackReadyTicks: 0,
    suppressedTicks: 0,
    suppressedReadyTicks: 0,
    suppressedBlockedTicks: 0,
    bareShots: 0,
    a3ModeledShots: 0,
    netShotDelta: 0,
    fallbackWindows: 0,
    rLeKExposed: 0,
    rGtKExposed: 0,
    turningWindows: 0,
    intent2Turning: 0,
    intent2TurningR0: 0,
    intent2TurningRPosLeK: 0,
    intent2TurningRGtK: 0,
    firstAfterTurnDelaySum: 0,
    firstAfterTurnDelayCount: 0,
    wallSec: 0,
  }

  let lastDecisions = input.readDecisionStats().n
  let windowActive = false
  let windowIntent: 0 | 1 | 2 = 0
  let windowR = 0
  let rejected = 0
  let fallback = 0
  let fallbackReady = 0
  let suppressed = 0
  let suppressedReady = 0
  let suppressedBlocked = 0
  let bareShots = 0
  let acceptedTick = -1
  let firstShotTick = -1

  const finalizeWindow = (endTick: number): void => {
    if (windowIntent === 2) {
      d.intent2Decisions++
      if (rejected > 0) {
        d.exposedDecisions++
        if (windowR <= decisionK) d.rLeKExposed++
        else d.rGtKExposed++
      }
      d.rejectedTicks += rejected
      d.fallbackTicks += fallback
      d.fallbackReadyTicks += fallbackReady
      d.suppressedTicks += suppressed
      d.suppressedReadyTicks += suppressedReady
      d.suppressedBlockedTicks += suppressedBlocked
      d.bareShots += bareShots
      const a3Shots = fallbackReady > 0 ? 1 : bareShots
      d.a3ModeledShots += a3Shots
      d.netShotDelta += a3Shots - bareShots
      if (fallbackReady > 0) d.fallbackWindows++
      if (acceptedTick >= 0 && firstShotTick >= 0) {
        d.firstAfterTurnDelaySum += firstShotTick - acceptedTick
        d.firstAfterTurnDelayCount++
      }
    }
    void endTick
    windowActive = false
    rejected = 0
    fallback = 0
    fallbackReady = 0
    suppressed = 0
    suppressedReady = 0
    suppressedBlocked = 0
    bareShots = 0
    acceptedTick = -1
    firstShotTick = -1
  }

  let t = 0
  while (t < maxTicks) {
    const p = world.player
    if (!windowActive) {
      windowActive = true
      windowIntent = currentIntent(input)
      windowR = p ? remainingTurnTicks(world, p) : 0
      if (p) {
        const turning = input.getMoveDirection() !== p.prevMoveDir
        if (turning) d.turningWindows++
        if (turning && windowIntent === 2) {
          d.intent2Turning++
          if (windowR <= 0) d.intent2TurningR0++
          else if (windowR <= decisionK) d.intent2TurningRPosLeK++
          else d.intent2TurningRGtK++
        }
      }
    }

    const active = !!p && p.alive && p.spawnTimer <= 0
    if (active) {
      d.aliveTicks++
      if (windowIntent === 2) {
        const dir: Direction | null = input.getMoveDirection()
        const commit = turnWillCommit(world, p!, dir, (world.frame + 1) * TICK_MS)
        const ready = fireReadyAt(world, p!)
        if (!commit) {
          rejected++
          const useful = usefulFire(world, p!, p!.prevMoveDir ?? p!.dir)
          if (useful) {
            fallback++
            if (ready) fallbackReady++
          } else {
            suppressed++
            if (ready) suppressedReady++
            else suppressedBlocked++
          }
        } else if (acceptedTick < 0) {
          acceptedTick = t
        }
      }
    }

    sim.tick()
    for (const e of world.consumeEvents()) {
      if (e.type === 'bullet_fired' && e.bullet.isPlayer) {
        bareShots++
        if (firstShotTick < 0) firstShotTick = t
      }
    }
    input.endFrame()

    const decisions = input.readDecisionStats().n
    if (decisions > lastDecisions) {
      finalizeWindow(t)
      lastDecisions = decisions
    }

    t++
    const st = world.state
    if (st === 'stageclear' || st === 'victory' || st === 'gameover') {
      d.outcome = st === 'gameover' ? 'gameover' : 'stage_clear'
      break
    }
  }
  if (windowActive) finalizeWindow(t - 1)

  d.ticks = t
  d.decisions = input.readDecisionStats().n
  d.wallSec = (Date.now() - t0) / 1000
  if (d.outcome === 'max_ticks' && world.state === 'stageclear') d.outcome = 'stage_clear'
  return d
}

function main(): void {
  const weightsPath = arg('weights')
  if (!weightsPath) {
    console.error('a3-dose-probe: --weights <path> is required')
    process.exit(2)
  }
  const difficulty = arg('difficulty', 'hard')!
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const decisionK = parseInt(arg('decision-k', '10')!, 10)
  const stageSpec = arg('stages', '0-3')!
  const seedSpec = arg('seeds', '1-2')!
  const stages = parseIntRange(stageSpec)
  const seeds = parseIntRange(seedSpec)
  const stageJsonFile = arg('stage-json-file', '')!
  const outPath = arg('out', '')!

  let custom: StageJson[] | null = null
  if (stageJsonFile) {
    const raw = JSON.parse(readFileSync(stageJsonFile, 'utf8'))
    custom = Array.isArray(raw) ? (raw as StageJson[]) : [raw as StageJson]
  }

  const lines: string[] = []
  if (outPath) writeFileSync(outPath, '')
  const t0 = Date.now()
  for (const si of stages) {
    if (custom && !custom[si]) {
      console.error(`a3-dose-probe: stage json ${si} not found`)
      process.exit(2)
    }
    const stage = custom ? decodeStageGrid(custom[si], si, seeds[0]) : STAGES[si]
    if (!stage) {
      console.error(`a3-dose-probe: stage ${si} not found`)
      process.exit(2)
    }
    const loadIndex = custom ? 0 : si
    for (const seed of seeds) {
      const world = new World()
      const d = runOne(
        world,
        stage,
        si,
        loadIndex,
        seed,
        difficulty,
        weightsPath,
        maxTicks,
        decisionK,
      )
      const line = JSON.stringify(d)
      lines.push(line)
      if (outPath) appendFileSync(outPath, line + '\n')
      console.error(
        `[a3-dose] stage=${si} seed=${seed} out=${d.outcome} ticks=${d.ticks} ` +
          `dec=${d.decisions} intent2=${d.intent2Decisions} exposed=${d.exposedDecisions} ` +
          `rejected=${d.rejectedTicks} fallback=${d.fallbackTicks}/${d.fallbackReadyTicks} ` +
          `suppressed=${d.suppressedTicks} bareShots=${d.bareShots} net=${d.netShotDelta} ` +
          `intent2Turn=${d.intent2Turning} (r0=${d.intent2TurningR0}, r<=K=${d.intent2TurningRPosLeK}, r>K=${d.intent2TurningRGtK}) ` +
          `wall=${d.wallSec.toFixed(2)}s`,
      )
    }
  }
  const wall = (Date.now() - t0) / 1000
  const sum = lines.reduce(
    (a, l) => {
      const d = JSON.parse(l) as GameDose
      a.ticks += d.ticks
      a.aliveTicks += d.aliveTicks
      a.decisions += d.decisions
      a.intent2Decisions += d.intent2Decisions
      a.exposedDecisions += d.exposedDecisions
      a.rejectedTicks += d.rejectedTicks
      a.fallbackTicks += d.fallbackTicks
      a.fallbackReadyTicks += d.fallbackReadyTicks
      a.suppressedTicks += d.suppressedTicks
      a.bareShots += d.bareShots
      a.a3ModeledShots += d.a3ModeledShots
      a.netShotDelta += d.netShotDelta
      a.turningWindows += d.turningWindows
      a.intent2Turning += d.intent2Turning
      a.intent2TurningR0 += d.intent2TurningR0
      a.intent2TurningRPosLeK += d.intent2TurningRPosLeK
      a.intent2TurningRGtK += d.intent2TurningRGtK
      return a
    },
    {
      ticks: 0,
      aliveTicks: 0,
      decisions: 0,
      intent2Decisions: 0,
      exposedDecisions: 0,
      rejectedTicks: 0,
      fallbackTicks: 0,
      fallbackReadyTicks: 0,
      suppressedTicks: 0,
      bareShots: 0,
      a3ModeledShots: 0,
      netShotDelta: 0,
      turningWindows: 0,
      intent2Turning: 0,
      intent2TurningR0: 0,
      intent2TurningRPosLeK: 0,
      intent2TurningRGtK: 0,
    },
  )
  const summary = {
    type: 'summary',
    games: lines.length,
    wallSec: wall,
    ticksPerSec: sum.ticks > 0 ? sum.ticks / wall : 0,
    ...sum,
    exposureRate: sum.decisions > 0 ? sum.exposedDecisions / sum.decisions : 0,
    lossTickRate: sum.aliveTicks > 0 ? sum.rejectedTicks / sum.aliveTicks : 0,
    triggerRate: sum.rejectedTicks > 0 ? sum.fallbackTicks / sum.rejectedTicks : 0,
    gateReadyRate: sum.fallbackTicks > 0 ? sum.fallbackReadyTicks / sum.fallbackTicks : 0,
  }
  const body = [...lines, JSON.stringify(summary)].join('\n') + '\n'
  if (outPath) writeFileSync(outPath, body)
  else process.stdout.write(body)
  console.error(
    `[a3-dose] summary games=${summary.games} wall=${wall.toFixed(1)}s ` +
      `ticks/s=${summary.ticksPerSec.toFixed(0)} exposure=${(summary.exposureRate * 100).toFixed(2)}% ` +
      `loss=${(summary.lossTickRate * 100).toFixed(2)}% trigger=${(summary.triggerRate * 100).toFixed(1)}%`,
  )
}

if (import.meta.main) main()
