/**
 * repro-seek.ts — reproduce the "drag the progress bar → replay desyncs" bug.
 *
 * Uses the REAL PlaybackController (start / seekTo / update) so it reflects the
 * current code. Compares a clean full playback against a "seek to <frac> then
 * resume to end" run. If the seek path is broken, the two outcomes differ.
 *
 * Usage:
 *   bun tools/replay/repro-seek.ts <file.replay> [frac=0.5]
 * Exit 0 = seek matches full playback (no desync), 1 = desync reproduced.
 */

import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { PlaybackController } from '../../src/replay/PlaybackController'
import { parseReplayFile } from '../../src/replay/file'

interface Outcome {
  finalState: string
  endedTick: number
  score: number
  kills: number
  lives: number
  baseAlive: boolean
  playerAlive: boolean
}

function setup(text: string): { world: World; sim: Simulation; playback: PlaybackController } {
  const parsed = parseReplayFile(text)
  if ('error' in parsed) throw new Error(`Parse failed: ${parsed.error}`)
  const replay = parsed.replay
  const meta = replay.metadata

  const world = new World()
  world.rng.reseed(replay.seed)
  const dkey = meta.difficulty || 'classic'
  world.difficultyKey = dkey
  world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
  world.rules = RULES[dkey] ?? DEFAULT_RULES
  const stage = STAGES[meta.stage] ?? STAGES[0]
  world.loadStageData(stage, 0)

  const playback = new PlaybackController(replay)
  playback.start(world, new Simulation(world, null as any))
  // Note: PlaybackController.start swaps simulation.input itself; we need the
  // simulation instance it actually drives, so re-create around it.
  const sim = (playback as any).simulation as Simulation
  return { world, sim, playback }
}

function runFull(text: string): Outcome {
  const { world, playback } = setup(text)
  let tick = 0
  while (!playback.isFinished && tick < 20000) {
    playback.update(16.67)
    tick++
    const s = world.state
    if (s === 'stageclear' || s === 'gameover' || s === 'victory') break
  }
  return {
    finalState: world.state,
    endedTick: tick,
    score: world.score,
    kills: world.killCount,
    lives: world.lives,
    baseAlive: !world.tileMap.isBaseDestroyed(),
    playerAlive: Boolean(world.player?.alive),
  }
}

function runSeek(text: string, frac: number): Outcome {
  const { world, sim, playback } = setup(text)
  playback.seekTo(world, sim, frac)
  if (playback.isPaused) playback.togglePause() // resume after seek
  let tick = 0
  while (!playback.isFinished && tick < 20000) {
    playback.update(16.67)
    tick++
    const s = world.state
    if (s === 'stageclear' || s === 'gameover' || s === 'victory') break
  }
  return {
    finalState: world.state,
    endedTick: tick,
    score: world.score,
    kills: world.killCount,
    lives: world.lives,
    baseAlive: !world.tileMap.isBaseDestroyed(),
    playerAlive: Boolean(world.player?.alive),
  }
}

function same(a: Outcome, b: Outcome): boolean {
  // endedTick is deliberately excluded: the seek run fast-forwards inside
  // PlaybackController.seekTo() (its internal tick count is invisible to this
  // harness), so its absolute tick count differs even when the world state is
  // identical. The outcome tuple below is what must match.
  return (
    a.finalState === b.finalState &&
    a.score === b.score &&
    a.kills === b.kills &&
    a.baseAlive === b.baseAlive &&
    a.playerAlive === b.playerAlive
  )
}

function fmt(o: Outcome): string {
  return `${o.finalState} @${o.endedTick} score=${o.score} kills=${o.kills} lives=${o.lives} baseAlive=${o.baseAlive} playerAlive=${o.playerAlive}`
}

if (import.meta.main) {
  const args = process.argv.slice(2)
  const files = args.filter((a) => a.endsWith('.replay'))
  const fracArg = args.find((a) => !a.startsWith('--') && !a.endsWith('.replay'))
  const frac = fracArg ? parseFloat(fracArg) : 0.5
  if (files.length === 0) {
    console.error('usage: bun tools/replay/repro-seek.ts <file.replay> [frac]')
    process.exit(2)
  }
  let bad = 0
  for (const f of files) {
    const text = await Bun.file(f).text()
    const full = runFull(text)
    const seek = runSeek(text, frac)
    const ok = same(full, seek)
    console.log(`[${ok ? 'OK    ' : 'DESYNC'}] ${f} (seek@${frac})`)
    console.log(`  full : ${fmt(full)}`)
    console.log(`  seek : ${fmt(seek)}`)
    if (!ok) bad++
  }
  process.exit(bad > 0 ? 1 : 0)
}
