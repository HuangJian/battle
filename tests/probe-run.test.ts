import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { InputRecorder } from '../src/replay/InputRecorder'
import { worldTickHash } from '../src/replay/tickHash'
import { ProbeController, ProbeBootError } from '../src/game/ProbeController'
import { parseProbeManifestText } from '../src/probe/manifest'
import { applyProbeRun } from '../src/probe/setup'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES } from '../src/config/rules'
import { CELL } from '../src/constants'
import type { InputLike } from '../src/game/Input'
import type { Direction } from '../src/constants'
import { ProbeVerdictError } from '../src/probe/verdict'
import { readStoreZip } from '../src/probe/zip'

// ============================================================
// Human-opening probe — run lifecycle (plan v7 §T3 / §T5a)
//
// Covers the five setup steps via the SHARED function used by the browser and
// the headless twin, the archived tick-0 hash, and the parts of the controller
// the loop depends on (budget, park-on-end, explicit recording, verdicts).
// ============================================================

const MANIFEST_TEXT = readFileSync(
  join(import.meta.dir, '..', 'public/probe/x20-opening.json'),
  'utf8',
)
const MANIFEST = parseProbeManifestText(MANIFEST_TEXT)

const OPTIONS = {
  difficulty: MANIFEST.difficulty,
  lives: MANIFEST.lives,
  level: MANIFEST.level,
}

/** Idle input: never moves, never fires — a deterministic stand-in for a human. */
const IDLE: InputLike = {
  getMoveDirection: () => null as Direction | null,
  isFiring: () => false,
  wasItemPressed: () => false,
  endFrame: () => {},
  reset: () => {},
}

/** The manifest's (stage, game) pair for one game index. */
function runFor(game: number): {
  stage: (typeof MANIFEST.stages)[number]
  entry: (typeof MANIFEST.games)[number]
} {
  const entry = MANIFEST.games[game]
  const stage = MANIFEST.stages.find((s) => s.id === entry.stage)!
  return { stage, entry }
}

function makeHost() {
  const world = new World()
  const recorder = new InputRecorder()
  return { world, recorder, now: () => new Date('2026-09-21T00:00:00.000Z') }
}

describe('applyProbeRun — shared setup (T3 ①–⑤)', () => {
  it('reproduces the archived tick-0 hash for every manifest game', () => {
    for (const g of MANIFEST.games) {
      const { stage } = runFor(g.game)
      const world = new World()
      applyProbeRun(world, { stage, game: g }, OPTIONS)
      expect(worldTickHash(world)).toBe(g.tick0Hash)
    }
  })

  it('is idempotent on the SAME world (re-run == fresh run)', () => {
    const { stage, entry } = runFor(2)
    const world = new World()
    applyProbeRun(world, { stage, game: entry }, OPTIONS)
    const first = worldTickHash(world)
    const sim = new Simulation(world, IDLE)
    for (let i = 0; i < 250; i++) sim.tick()
    applyProbeRun(world, { stage, game: entry }, OPTIONS)
    expect(worldTickHash(world)).toBe(first)
    expect(worldTickHash(world)).toBe(entry.tick0Hash)
  })

  it('sets the difficulty trio so loadStageData cannot fall back to classic', () => {
    const { stage, entry } = runFor(0)
    const world = new World()
    applyProbeRun(world, { stage, game: entry }, OPTIONS)
    expect(world.difficultyKey).toBe('hard')
    expect(world.difficulty).toBe(DIFFICULTIES.hard)
    expect(world.rules).toBe(RULES.hard)
  })

  it('applies the task definition (1 life, star 0) before the player spawns', () => {
    const { stage, entry } = runFor(0)
    const world = new World()
    applyProbeRun(world, { stage, game: entry }, OPTIONS)
    expect(world.lives).toBe(1)
    expect(world.playerLevel).toBe(0)
    expect(world.player).not.toBeNull()
    expect(world.state).toBe('playing')
  })

  it('loads the manifest stage verbatim (tiles, spawns, enemy count)', () => {
    const { stage, entry } = runFor(2)
    const world = new World()
    applyProbeRun(world, { stage, game: entry }, OPTIONS)
    expect(world.enemiesTotal).toBe(stage.enemyCount)
    expect(world.playerSpawnPoint).toEqual(stage.playerSpawn)
    expect(world.enemySpawnPoints).toEqual(
      stage.enemySpawns.map((s) => ({ x: s.col * CELL, y: s.row * CELL })),
    )
    expect(world.stageIndex).toBe(0) // never the 2000+ id (1.05^index caliber)
    expect(world.seed).toBe(entry.seed)
  })

  it('clears every run field a previous run could leave behind (T3 ①)', () => {
    const { stage, entry } = runFor(1)
    const world = new World()
    // Dirty the world the way a played run would.
    world.score = 1234
    world.killCount = 7
    world.playTimeMs = 9999
    world.pendingDrops = [{ type: 'star', x: 1, y: 2 }]
    world.guardStock = 2
    world.frenzyStock = 1
    world.sacrificeStock = 3
    world.rewindStock = 1
    world.lives = 3
    world.playerLevel = 2
    world.mines = [{ id: 1, x: 0, y: 0, w: 1, h: 1, dir: 'up', alive: true } as never]
    world.stuckTicks = 42
    applyProbeRun(world, { stage, game: entry }, OPTIONS)
    expect(world.score).toBe(0)
    expect(world.killCount).toBe(0)
    expect(world.playTimeMs).toBe(0)
    expect(world.pendingDrops).toEqual([])
    expect(world.guardStock).toBe(0)
    expect(world.frenzyStock).toBe(0)
    expect(world.sacrificeStock).toBe(0)
    expect(world.rewindStock).toBe(0)
    expect(world.lives).toBe(1)
    expect(world.playerLevel).toBe(0)
    expect(world.mines).toEqual([])
    expect(world.stuckTicks).toBe(0)
    // The reset is complete enough that even the hash agrees with the archive.
    expect(worldTickHash(world)).toBe(entry.tick0Hash)
  })
})

describe('ProbeController — lifecycle', () => {
  it('boots from a valid query and arms recording explicitly (T3 ⑥)', () => {
    const host = makeHost()
    const controller = new ProbeController(host)
    expect(controller.bootFromText('?probe=x20-opening&game=3', MANIFEST_TEXT)).toBe(true)
    expect(controller.isActive).toBe(true)
    expect(host.recorder.isActive).toBe(true)
    expect(controller.snapshot()?.game).toBe(3)
    expect(controller.snapshot()?.totalGames).toBe(MANIFEST.games.length)
  })

  it('returns false (no run) when the query has no probe param', () => {
    const host = makeHost()
    const controller = new ProbeController(host)
    expect(controller.bootFromText('?foo=1', MANIFEST_TEXT)).toBe(false)
    expect(controller.isActive).toBe(false)
  })

  it('refuses the three invalid launches (T2 rejection paths)', () => {
    const controller = new ProbeController(makeHost())
    expect(() => controller.bootFromText('?probe=nope&game=0', MANIFEST_TEXT)).toThrow(
      ProbeBootError,
    )
    expect(() => controller.bootFromText('?probe=x20-opening&game=99', MANIFEST_TEXT)).toThrow(
      /game-out-of-range/,
    )
    expect(() => controller.bootFromText('?probe=x20-opening&game=x', MANIFEST_TEXT)).toThrow(
      /game-not-integer/,
    )
  })

  it('enforces the tick budget (T3 ⑦)', () => {
    const controller = new ProbeController(makeHost())
    controller.bootFromText('?probe=x20-opening&game=0', MANIFEST_TEXT)
    expect(controller.maxTicks).toBe(MANIFEST.max_ticks)
    expect(controller.tickBudgetSpent).toBe(false)
    for (let i = 0; i < MANIFEST.max_ticks - 1; i++) controller.noteTick()
    expect(controller.tickBudgetSpent).toBe(false)
    controller.noteTick()
    expect(controller.tickBudgetSpent).toBe(true)
  })

  it('finishRun stores the replay, counts kills and PARKS the world (T3 ⑧)', () => {
    const host = makeHost()
    const controller = new ProbeController(host)
    controller.bootFromText('?probe=x20-opening&game=0', MANIFEST_TEXT)
    const sim = new Simulation(host.world, IDLE)
    for (let i = 0; i < 30; i++) {
      sim.tick()
      host.recorder.recordFrame(IDLE, null)
      controller.noteTick()
    }
    host.world.killCount = 4
    expect(controller.runEnded).toBe(false)
    controller.finishRun('gameover')

    // Parked: without this the 'gameover' state would keep ticking and a
    // 'stageclear' timer would advance the world into the NEXT stage.
    expect(host.world.state).toBe('paused')
    // end-of-run marker the pause key consults (P must not resume a parked run)
    expect(controller.runEnded).toBe(true)
    const snap = controller.snapshot()!
    expect(snap.outcome).toBe('gameover')
    expect(snap.kills).toBe(4)
    expect(snap.deathTick).toBe(30)
    expect(host.recorder.isActive).toBe(false)

    // The recording landed in the pack, not in the ReplayManager.
    const names = unpackNames(controller)
    expect(names).toContain('0.replay')
  })

  it('finishRun is idempotent (a second terminal branch must not double-record)', () => {
    const host = makeHost()
    const controller = new ProbeController(host)
    controller.bootFromText('?probe=x20-opening&game=0', MANIFEST_TEXT)
    const sim = new Simulation(host.world, IDLE)
    for (let i = 0; i < 5; i++) {
      sim.tick()
      host.recorder.recordFrame(IDLE, null)
    }
    controller.finishRun('gameover')
    controller.finishRun('clear')
    expect(controller.snapshot()?.outcome).toBe('gameover')
  })

  it('retry re-arms the recording and resets the tick counter', () => {
    const host = makeHost()
    const controller = new ProbeController(host)
    controller.bootFromText('?probe=x20-opening&game=2', MANIFEST_TEXT)
    const sim = new Simulation(host.world, IDLE)
    for (let i = 0; i < 12; i++) {
      sim.tick()
      host.recorder.recordFrame(IDLE, null)
      controller.noteTick()
    }
    controller.retry()
    const snap = controller.snapshot()!
    expect(snap.ticks).toBe(0)
    expect(snap.outcome).toBeNull()
    expect(snap.attempts).toBe(2)
    expect(host.recorder.isActive).toBe(true)
    expect(host.world.state).toBe('playing')
  })

  it('retry after a finished run clears the parked marker (P works again)', () => {
    const host = makeHost()
    const controller = new ProbeController(host)
    controller.bootFromText('?probe=x20-opening&game=0', MANIFEST_TEXT)
    const sim = new Simulation(host.world, IDLE)
    for (let i = 0; i < 9; i++) {
      sim.tick()
      host.recorder.recordFrame(IDLE, null)
    }
    controller.finishRun('gameover')
    expect(controller.runEnded).toBe(true)
    controller.retry()
    expect(controller.runEnded).toBe(false)
    expect(host.world.state).toBe('playing')
  })

  it('navigate moves within range and is a no-op at the edges', () => {
    const controller = new ProbeController(makeHost())
    controller.bootFromText('?probe=x20-opening&game=0', MANIFEST_TEXT)
    controller.navigate(-1)
    expect(controller.snapshot()?.game).toBe(0)
    controller.navigate(1)
    expect(controller.snapshot()?.game).toBe(1)
  })

  it('collects verdicts, refuses unsolvable without a reason, and aggregates', () => {
    const host = makeHost()
    const controller = new ProbeController(host)
    controller.bootFromText('?probe=x20-opening&game=1', MANIFEST_TEXT)
    // A verdict before the run ends is refused up front (nothing to judge yet).
    expect(() => controller.submitVerdict('unsolvable', '')).toThrow(/尚未结束/)
    controller.finishRun('gameover')
    // Then the protocol's own rule applies: unsolvable without a reason.
    expect(() => controller.submitVerdict('unsolvable', '  ')).toThrow(ProbeVerdictError)
    expect(controller.suggestedBand()).toBe('unsolvable')
    // The operator's readable no-path read IS the negative arm (§0 v8): it is
    // not discarded as "no information".
    controller.submitVerdict('unsolvable', 'spawn is walled in')
    expect(controller.seedVerdict()).toBe('unsolvable')
    controller.navigate(1)
    controller.finishRun('clear')
    controller.submitVerdict('solvable', '')
    expect(controller.seedVerdict()).toBe('solvable')
    expect(controller.snapshot()?.verdictsDone).toBe(2)
  })

  it('builds a pack containing every played replay', () => {
    const host = makeHost()
    const controller = new ProbeController(host)
    controller.bootFromText('?probe=x20-opening&game=0', MANIFEST_TEXT)
    const sim = new Simulation(host.world, IDLE)
    for (let i = 0; i < 8; i++) {
      sim.tick()
      host.recorder.recordFrame(IDLE, null)
    }
    controller.finishRun('gameover')
    controller.submitVerdict('tough', '')
    const pack = controller.buildPack()
    expect(pack.filename).toBe('probe-x20-opening-20260921.zip')
    expect(unpackNames(controller)).toEqual(['0.replay'])
  })

  it('exit clears the session surface', () => {
    const controller = new ProbeController(makeHost())
    controller.bootFromText('?probe=x20-opening&game=0', MANIFEST_TEXT)
    controller.exit()
    expect(controller.isActive).toBe(false)
    expect(controller.snapshot()).toBeNull()
  })
})

/** Read the replay names out of the controller's own pack (black-box). */
function unpackNames(controller: ProbeController): string[] {
  return readStoreZip(controller.buildPack().bytes)
    .map((e) => e.name)
    .filter((n) => n.endsWith('.replay'))
}
