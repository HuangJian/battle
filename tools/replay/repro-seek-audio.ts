/**
 * repro-seek-audio.ts — reproduce the "drag the seek bar → harsh audio burst" bug.
 *
 * Root cause (DECISIONS #78): PlaybackController.seekTo (and buildKeyframes) fast-
 * forward `targetFrame` sim ticks, each of which pushes sound events into
 * world.events. The render loop only drains world.events() one frame per rendered
 * frame, so during the silent catch-up nobody drains — the whole stage's worth of
 * sound effects pile up and detonate at once when the next render frame runs
 * consumeEvents(). With the fix, the catch-up loop drains (discards) events every
 * tick, so the pending queue is empty after a seek.
 *
 * Usage:
 *   bun tools/replay/repro-seek-audio.ts <file.replay> [frac=0.5]
 * Prints the FIXED pending-queue size and the magnitude the bug would have queued.
 */

import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { PlaybackController } from '../../src/replay/PlaybackController'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import type { Replay } from '../../src/replay/types'

function buildWorld(replay: Replay): { world: World; sim: Simulation } {
  const meta = replay.metadata
  const world = new World()
  world.rng.reseed(replay.seed)
  const dkey = meta.difficulty || 'classic'
  world.difficultyKey = dkey
  world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
  world.rules = RULES[dkey] ?? DEFAULT_RULES
  const stage = STAGES[meta.stage] ?? STAGES[0]
  world.loadStageData(stage, 0)
  const sim = new Simulation(world, null as any)
  return { world, sim }
}

/** FIXED path: real seekTo drains events each tick. Report the pending queue. */
function pendingAfterSeek(text: string, frac: number): number {
  const parsed = parseReplayFile(text)
  if ('error' in parsed) throw new Error(parsed.error)
  const replay = parsed.replay
  const { world, sim } = buildWorld(replay)
  const playback = new PlaybackController(replay)
  playback.start(world, sim)
  playback.seekTo(world, (playback as any).simulation as Simulation, frac)
  return world.events.items.length
}

/** Buggy path: replicate the OLD catch-up (no drain) to show magnitude. */
function buggyBacklog(text: string, frac: number): number {
  const parsed = parseReplayFile(text)
  if ('error' in parsed) throw new Error(parsed.error)
  const replay = parsed.replay
  const { world, sim } = buildWorld(replay)
  restoreWorld(world, replay.initialSnapshot)
  const input = new ReplayInput(replay.frames)
  sim.input = input
  sim.input2 = input.input2 ?? null
  const targetFrame = Math.floor(frac * input.totalFrames)
  for (let i = 0; i < targetFrame; i++) {
    sim.tick()
    input.advance()
  }
  return world.events.items.length
}

if (import.meta.main) {
  const args = process.argv.slice(2)
  const files = args.filter((a) => a.endsWith('.replay'))
  const fracArg = args.find((a) => !a.startsWith('--') && !a.endsWith('.replay'))
  const frac = fracArg ? parseFloat(fracArg) : 0.5
  if (files.length === 0) {
    console.error('usage: bun tools/replay/repro-seek-audio.ts <file.replay> [frac]')
    process.exit(2)
  }
  for (const f of files) {
    const text = await Bun.file(f).text()
    const fixed = pendingAfterSeek(text, frac)
    const bug = buggyBacklog(text, frac)
    console.log(`[FIXED]   pending events after seekTo(${frac}): ${fixed}`)
    console.log(`[OLD-BUG] events a non-draining catch-up would queue: ${bug}`)
    console.log(
      `  -> ${bug > 0 && fixed === 0 ? 'FIXED: no audio burst' : fixed === 0 ? 'no backlog' : 'STILL BROKEN'}`,
    )
  }
}
