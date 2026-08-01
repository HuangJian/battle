/**
 * verify-replay.ts — headless `.replay` file verifier.
 *
 * Loads a saved `.replay` file, replays it through the real Simulation using
 * exactly the same wiring as PlaybackController.start()/update(), and reports
 * the outcome. Use it to prove (or disprove) a replay desync without a browser.
 *
 * Usage:
 *   bun tools/replay/verify-replay.ts <file.replay> [--verbose] [--trace-p1]
 *
 * Exit code 0 = replay reproduces the recorded outcome, 1 = desync detected.
 */

import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { unpackFrames } from '../../src/replay/pack'
import type { GameState } from '../../src/types'

/** Read world.state without letting TS narrow it to the last assigned literal. */
const stateOf = (w: World): GameState => w.state

interface VerifyResult {
  file: string
  coop: boolean
  totalTicks: number
  expectedType: string
  finalState: string
  endedAtTick: number
  score: number
  killCount: number
  lives: number
  baseAlive: boolean
  playerAlive: boolean
  verdict: 'OK' | 'DESYNC'
  reason: string
}

export function verifyReplayText(text: string, file: string, verbose = false): VerifyResult {
  const parsed = parseReplayFile(text)
  if ('error' in parsed) throw new Error(`Parse failed: ${parsed.error}`)
  const replay = parsed.replay
  const meta = replay.metadata
  const coop = Boolean((parsed.envelope as any)?.replay?.metadata?.coop)

  // Expected outcome: filename convention `<difficulty>-s<NN>-<type>-...`
  const m = /-s\d+-([a-z]+)-/.exec(file)
  const expectedType = m ? m[1] : replay.type

  // ---- Rebuild the world exactly as PlaybackController.start() does ----
  const world = new World()
  world.rng.reseed(replay.seed)
  const dkey = meta.difficulty || 'classic'
  world.difficultyKey = dkey
  world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
  world.rules = RULES[dkey] ?? DEFAULT_RULES
  const stage = STAGES[meta.stage] ?? STAGES[0]
  world.loadStageData(stage, 0)

  restoreWorld(world, replay.initialSnapshot)
  const input = new ReplayInput(replay.frames)
  const sim = new Simulation(world, input)
  sim.input = input
  sim.input2 = input.input2 ?? null
  world.state = 'playing'

  let tick = 0
  let endState: GameState = stateOf(world)
  while (!input.isFinished && tick < replay.totalTicks + 10) {
    sim.tick()
    input.advance()
    tick++
    world.consumeEvents?.()
    endState = stateOf(world)
    if (endState === 'stageclear' || endState === 'gameover' || endState === 'victory') break
  }

  const baseAlive = !world.tileMap.isBaseDestroyed()
  const playerAlive = Boolean(world.player?.alive)

  let verdict: 'OK' | 'DESYNC' = 'OK'
  let reason = 'replay reproduced the recorded outcome'
  if (expectedType === 'clear') {
    if (endState !== 'stageclear' && endState !== 'victory') {
      verdict = 'DESYNC'
      reason = `expected stage clear, got '${endState}'` + (!baseAlive ? ' (BASE DESTROYED)' : '')
    }
  }
  if (verbose) {
    const un = unpackFrames(replay.frames)
    if (un) {
      const p1fire = un.p1.filter((f) => f.firing).length
      const p1move = un.p1.filter((f) => f.direction !== null).length
      const firstFire = un.p1.findIndex((f) => f.firing)
      const p2fire = un.p2 ? un.p2.filter((f) => f.firing).length : 0
      const p2move = un.p2 ? un.p2.filter((f) => f.direction !== null).length : 0
      console.log(
        `  frames: p1 fire=${p1fire}/${un.p1.length} move=${p1move} firstFire@${firstFire} | p2 fire=${p2fire} move=${p2move}`,
      )
    }
  }

  return {
    file,
    coop,
    totalTicks: replay.totalTicks,
    expectedType,
    finalState: endState,
    endedAtTick: tick,
    score: world.score,
    killCount: world.killCount,
    lives: world.lives,
    baseAlive,
    playerAlive,
    verdict,
    reason,
  }
}

if (import.meta.main) {
  const args = process.argv.slice(2)
  const verbose = args.includes('--verbose')
  const files = args.filter((a) => !a.startsWith('--'))
  if (files.length === 0) {
    console.error('usage: bun tools/replay/verify-replay.ts <file.replay> [--verbose]')
    process.exit(2)
  }
  let bad = 0
  for (const f of files) {
    const text = await Bun.file(f).text()
    const r = verifyReplayText(text, f, verbose)
    const tag = r.verdict === 'OK' ? 'OK    ' : 'DESYNC'
    console.log(
      `[${tag}] ${r.file}\n` +
        `  coop=${r.coop} expected=${r.expectedType} -> final='${r.finalState}' @tick ${r.endedAtTick}/${r.totalTicks}\n` +
        `  score=${r.score} kills=${r.killCount} lives=${r.lives} baseAlive=${r.baseAlive} playerAlive=${r.playerAlive}\n` +
        `  ${r.reason}`,
    )
    if (r.verdict !== 'OK') bad++
  }
  process.exit(bad > 0 ? 1 : 0)
}
