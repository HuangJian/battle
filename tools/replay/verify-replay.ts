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
import { REPLAY_HASH_INTERVAL } from '../../src/replay/config'
import { worldTickHash } from '../../src/replay/tickHash'
import type { GameState } from '../../src/types'

/** Read world.state without letting TS narrow it to the last assigned literal. */
const stateOf = (w: World): GameState => w.state

/** First recorded hash checkpoint whose computed hash diverged (plan/Replay-TickHash-Chain.md §1.3). */
export interface HashMismatchInfo {
  /** Checkpoint tick of the mismatch (== tickWindow[1], the sampled tick). */
  checkpoint: number
  /** Hash recorded in the file at this checkpoint. */
  recorded: string
  /** Hash computed from the replayed world at this checkpoint. */
  computed: string
  /** Divergence window — [start, checkpoint) ticks. */
  tickWindow: [number, number]
}

/**
 * Verdict decision table (plan §1.4) — hashVerified × terminalMatch:
 *   true×true ⇒ OK/0 · true×false ⇒ DESYNC/1 · false×true ⇒ DESYNC/1
 *   false×false ⇒ DESYNC/1 · null×true ⇒ OK/0 · null×false ⇒ DESYNC/1
 * A terminal mismatch is always DESYNC (the recorded outcome label was not
 * reproduced); a computed hash mismatch is always DESYNC regardless of the
 * terminal state (the worlds diverged — the file's frames are not the run's
 * frames). Legacy files (null) fall back to the terminal state alone.
 */
export function decideVerdict(hashVerified: boolean | null, terminalMatch: boolean): {
  verdict: 'OK' | 'DESYNC'
  exitCode: 0 | 1
} {
  if (!terminalMatch) return { verdict: 'DESYNC', exitCode: 1 }
  if (hashVerified === false) return { verdict: 'DESYNC', exitCode: 1 }
  return { verdict: 'OK', exitCode: 0 }
}

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
  /** True = every compared checkpoint matched, false = mismatch, null = no hash chain. */
  hashVerified: boolean | null
  firstHashMismatch: HashMismatchInfo | null
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

  // Tick-hash chain — phase contract (tickHash.ts header): the recorder
  // sampled one hash per `interval` ticks AFTER the corresponding sim.tick(),
  // using the expression `frames.length % interval === 0`; we sample at the
  // SAME tick count, BEFORE any terminal-state break. tick here is the number
  // of completed sim.tick() calls, mirroring frames.length on the recorder.
  const recordedHashes = replay.tickHashes ?? []
  const interval = replay.hashInterval ?? REPLAY_HASH_INTERVAL
  let hashVerified: boolean | null = null
  let firstHashMismatch: HashMismatchInfo | null = null
  let hashIdx = 0

  while (!input.isFinished && tick < replay.totalTicks + 10) {
    sim.tick()
    input.advance()
    tick++
    world.consumeEvents?.()

    if (hashIdx < recordedHashes.length && tick % interval === 0) {
      const computed = worldTickHash(world)
      if (computed === recordedHashes[hashIdx]) {
        if (hashVerified !== false) hashVerified = true
      } else if (hashVerified !== false) {
        hashVerified = false
        firstHashMismatch = {
          checkpoint: tick,
          recorded: recordedHashes[hashIdx],
          computed,
          tickWindow: [tick - interval, tick],
        }
      }
      hashIdx++
    }

    endState = stateOf(world)
    if (endState === 'stageclear' || endState === 'gameover' || endState === 'victory') break
  }

  const baseAlive = !world.tileMap.isBaseDestroyed()
  const playerAlive = Boolean(world.player?.alive)

  // Terminal-state match: only 'clear' expectations are verdict-bearing —
  // a base/died/timeout label has no exact state to match against.
  const terminalMatch =
    expectedType !== 'clear' || endState === 'stageclear' || endState === 'victory'

  let verdict: 'OK' | 'DESYNC' = 'OK'
  let reason = 'replay reproduced the recorded outcome'
  if (!terminalMatch) {
    verdict = 'DESYNC'
    reason = `expected stage clear, got '${endState}'` + (!baseAlive ? ' (BASE DESTROYED)' : '')
  }
  const decided = decideVerdict(hashVerified, terminalMatch)
  verdict = decided.verdict
  if (decided.verdict === 'DESYNC' && terminalMatch) {
    if (firstHashMismatch) {
      reason = `hash mismatch at t${firstHashMismatch.checkpoint} (window [${firstHashMismatch.tickWindow[0]},${firstHashMismatch.tickWindow[1]}))`
    } else {
      reason = 'hash chain present but unverified'
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
    hashVerified,
    firstHashMismatch,
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
        (r.hashVerified !== null
          ? `  hash=${r.hashVerified === true ? 'ok' : `mismatch@t${r.firstHashMismatch?.checkpoint ?? '?'}`}${r.firstHashMismatch ? ` window=[${r.firstHashMismatch.tickWindow[0]},${r.firstHashMismatch.tickWindow[1]})` : ''}\n`
          : '') +
        `  ${r.reason}`,
    )
    if (r.verdict !== 'OK') bad++
  }
  process.exit(bad > 0 ? 1 : 0)
}
