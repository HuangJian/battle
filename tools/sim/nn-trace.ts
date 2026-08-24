#!/usr/bin/env bun
/**
 * nn-trace.ts — single-game deployment trace of the NN player (P1.5 debug).
 *
 * Drives the real NNInput (the SAME runtime the sim uses) headlessly for one
 * (stage, seed), logging the committed action at every decision tick plus the
 * world telemetry, so we can SEE the deployment failure mode that produces a
 * 0% win rate. Logs the first N decisions verbatim, then a behavior summary.
 *
 * Usage:
 *   bun tools/sim/nn-trace.ts --weights-dir tmp/student-weights-full --stage 1 --seed 1
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { NNInput } from '../../src/nn/policy-input'
import { ObsEncoder } from '../../src/nn/obs-encoder'
import { buildModelFromText, type ModelLike } from '../../src/nn/infer'
import { resolveLatestWeights } from '../../src/nn/weights'
import { START_LIVES } from '../../src/constants'
import { readFileSync } from 'fs'
import { join } from 'path'

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function runTrace(
  stageIdx: number,
  seed: number,
  difficulty: string,
  weightsDir: string,
  maxTicks: number,
  verboseDecisions: number,
): void {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const input = new NNInput(world, { weightsDir })
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], stageIdx)
  input.reset()

  // Parallel raw model (bypass NNInput gating) to isolate obs-vs-gating bugs.
  const wpath = resolveLatestWeights(weightsDir) ?? join(weightsDir, 'weights.json')
  const model: ModelLike = buildModelFromText(readFileSync(wpath, 'utf8'))
  const enc2 = new ObsEncoder()
  const DIR_DECODE: string[] = ['up', 'down', 'left', 'right']

  let t = 0
  let decisions = 0
  const moveCount = { up: 0, down: 0, left: 0, right: 0, none: 0 }
  let fireCount = 0
  let guardCount = 0
  let frenzyCount = 0
  const distinctCells = new Set<string>()
  let lastPos = { x: -1, y: -1 }
  let maxKills = 0
  const log: string[] = []

  while (t < maxTicks) {
    sim.tick()
    const dir = input.getMoveDirection()
    const firing = input.isFiring()
    const g = input.wasItemPressed('guard')
    const fr = input.wasItemPressed('frenzy')

    if (t % 10 === 0) {
      decisions++
      moveCount[dir ?? 'none']++
      if (firing) fireCount++
      if (g) guardCount++
      if (fr) frenzyCount++
      const p = world.player
      if (p) {
        distinctCells.add(`${Math.round(p.x)},${Math.round(p.y)}`)
        lastPos = { x: p.x, y: p.y }
      }
      // Raw model prediction on the deployment-encoded obs (no gating).
      enc2.encode(world)
      model.forward(enc2.obs, enc2.scalars)
      const mv = model.moveLogits
      let bestMv = 0
      let bv = mv[0]
      for (let i = 1; i < 5; i++)
        if (mv[i] > bv) {
          bv = mv[i]
          bestMv = i
        }
      const manualDir = bestMv === 0 ? 'none' : DIR_DECODE[bestMv - 1]
      const manualFire = model.fireLogits[1] > model.fireLogits[0] ? 1 : 0
      if (decisions <= verboseDecisions) {
        const kc = world.killCount
        maxKills = Math.max(maxKills, kc)
        log.push(
          `t=${String(t).padStart(5)} frame=${String(world.frame).padStart(5)} dec#${String(decisions).padStart(3)} ` +
            `NN[dir=${String(dir).padEnd(5)} fire=${firing ? 1 : 0}] ` +
            `RAW[dir=${manualDir.padEnd(5)} fire=${manualFire} mvLogits=${Array.from(mv)
              .map((x) => x.toFixed(1))
              .join(',')} ` +
            `fireLogits=${model.fireLogits[0].toFixed(1)},${model.fireLogits[1].toFixed(1)} ready=${world.player ? world.frame * (1000 / 60) - (world.player.lastFire ?? -9999) >= (world.player.nextFireInterval ?? 0) && (world.player.nextFireInterval ?? 0) > 0 : '?'} ` +
            `pos=(${p ? Math.round(p.x) : '?'},${p ? Math.round(p.y) : '?'}) kills=${kc} baseAlive=${!world.tileMap.isBaseDestroyed()}`,
        )
      }
    }
    input.endFrame()
    t++
    const st: string = world.state
    if (st === 'stageclear' || st === 'victory') {
      log.push(`OUTCOME=stage_clear ticks=${t} kills=${world.killCount}`)
      break
    }
    if (st === 'gameover') {
      log.push(
        `OUTCOME=${world.tileMap.isBaseDestroyed() ? 'base_destroyed' : 'lives_exhausted'} ticks=${t} kills=${world.killCount}`,
      )
      break
    }
  }

  process.stdout.write(`[nn-trace] stage=${stageIdx + 1} seed=${seed} difficulty=${difficulty}\n`)
  process.stdout.write(log.join('\n') + '\n')
  process.stdout.write(
    `\nsummary: decisions=${decisions} ` +
      `move[up=${moveCount.up} down=${moveCount.down} left=${moveCount.left} right=${moveCount.right} none=${moveCount.none}] ` +
      `fire=${fireCount} guard=${guardCount} frenzy=${frenzyCount} ` +
      `distinctCells=${distinctCells.size} finalPos=(${Math.round(lastPos.x)},${Math.round(lastPos.y)}) ` +
      `finalKills=${world.killCount}\n`,
  )
}

function main(): void {
  const weightsDir = arg('weights-dir', 'tmp/student-weights-full')!
  const stage = parseInt(arg('stage', '1')!, 10)
  const seed = parseInt(arg('seed', '1')!, 10)
  const difficulty = arg('difficulty', 'hard')!
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const verbose = parseInt(arg('verbose', '40')!, 10)
  runTrace(stage - 1, seed, difficulty, weightsDir, maxTicks, verbose)
}

if (import.meta.main) main()
