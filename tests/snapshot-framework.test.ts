import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { SnapshotManager } from '../src/snapshot/SnapshotManager'
import { cloneWorld } from '../src/snapshot/WorldSerializer'
import {
  RecoveryController,
  RECOVERY_OPTIONS,
  RECOVERY_OPTION_COUNT,
} from '../src/snapshot/RecoveryController'
import { RETENTION_POLICIES, LATEST_FALLBACK_WINDOW_MS } from '../src/snapshot/config'
import type { GameSnapshot, SnapshotStorageBackend } from '../src/snapshot/types'

/**
 * Snapshot Management Framework — plan/Snapshot-Management-Framework.md
 *
 * Guards the Definition of Done (§19):
 *  1. Four snapshot origins share ONE model (unified create())
 *  2. Retention is policy-declared, not code-branched
 *  3. Manual snapshots are never overwritten (cleanup notification instead)
 *  4. UUID identity + parent timeline links
 *  5. Metadata is first-class (13 fields, readable without loading the world)
 *  6. Atomic restore — the loaded world reproduces the saved one exactly
 *  7. "Load Latest" 15 s fallback rule (§11)
 *  8. Recovery menu — 5 options with correct availability
 */

// ---- helpers ----

/** A deterministic clock we can advance by hand. */
function makeClock(start = 1_000_000) {
  let t = start
  return {
    now: () => t,
    advance: (ms: number) => {
      t += ms
    },
  }
}

function makeManager(opts: { now?: () => number } = {}): SnapshotManager {
  return new SnapshotManager({ now: opts.now })
}

/** A World mid-run, mutated enough to be distinguishable. */
function makePlayingWorld(stage = 0): World {
  const world = new World()
  world.startGame('classic', world.themeKey, stage)
  world.score = 4321
  world.killCount = 7
  world.playTimeMs = 65_000
  return world
}

// ============================================================
// One Snapshot Model (§2, §6) — unified creation
// ============================================================

describe('Snapshot Framework — one model, four origins', () => {
  it('all four origins produce the same GameSnapshot shape', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const types = ['stage-start', 'pause', 'auto', 'manual'] as const
    for (const type of types) {
      const snap = mgr.create(type, world)
      expect(snap).not.toBeNull()
      expect(snap!.type).toBe(type)
      expect(typeof snap!.id).toBe('string')
      expect(snap!.id.length).toBeGreaterThanOrEqual(36)
      expect(snap!.world).toBeDefined()
      expect(snap!.metadata).toBeDefined()
    }
    expect(mgr.count()).toBe(4)
  })

  it('snapshot IDs are unique UUIDs', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const ids = new Set<string>()
    for (let i = 0; i < 20; i++) {
      ids.add(mgr.create('auto', world)!.id)
    }
    expect(ids.size).toBe(20)
  })

  it('parent links form a timeline (§14)', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const a = mgr.create('stage-start', world)!
    const b = mgr.create('auto', world)!
    const c = mgr.create('manual', world)!
    expect(a.parentId).toBeNull()
    expect(b.parentId).toBe(a.id)
    expect(c.parentId).toBe(b.id)
  })

  it('restoring rewires the timeline head — a branch is recorded', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const a = mgr.create('stage-start', world)!
    mgr.create('auto', world)! // b — the abandoned branch tip
    mgr.restore(a.id, world)
    const c = mgr.create('manual', world)!
    expect(c.parentId).toBe(a.id) // parent is the restored snapshot, not b
  })
})

// ============================================================
// Metadata-first (§7) — 13 fields, no world deserialization needed
// ============================================================

describe('Snapshot Framework — metadata', () => {
  it('captures the full §7 field list from the live World', () => {
    const mgr = makeManager()
    const world = makePlayingWorld(2)
    const m = mgr.create('manual', world)!.metadata
    expect(m.stage).toBe(2)
    expect(typeof m.stageName).toBe('string')
    expect(m.stageName.length).toBeGreaterThan(0)
    expect(m.difficulty).toBe('Classic')
    expect(m.lives).toBe(world.lives)
    expect(m.starLevel).toBe(world.playerLevel)
    expect(m.hp).toBe(world.player!.hp)
    expect(m.maxHp).toBe(world.player!.maxHp)
    expect(m.combatLevel).toBeGreaterThan(0)
    expect(m.enemiesRemaining).toBe(world.enemiesRemaining)
    expect(typeof m.commanderPresent).toBe('boolean')
    expect(m.killCount).toBe(7)
    expect(m.score).toBe(4321)
    expect(m.playTimeMs).toBe(65_000)
  })
})

// ============================================================
// Retention policies (§9) — declarative, per-type
// ============================================================

describe('Snapshot Framework — retention', () => {
  it('circular types overwrite the oldest when full', () => {
    const clock = makeClock()
    const mgr = new SnapshotManager({ now: clock.now })
    const world = makePlayingWorld()
    const limit = RETENTION_POLICIES['auto'].limit
    const created: GameSnapshot[] = []
    for (let i = 0; i < limit + 3; i++) {
      created.push(mgr.create('auto', world)!)
      clock.advance(1000)
    }
    expect(mgr.count('auto')).toBe(limit)
    // The three oldest are gone; the newest survive.
    expect(mgr.get(created[0].id)).toBeNull()
    expect(mgr.get(created[2].id)).toBeNull()
    expect(mgr.get(created[3].id)).not.toBeNull()
    expect(mgr.get(created[created.length - 1].id)).not.toBeNull()
  })

  it('manual snapshots are NEVER overwritten — create() returns null when full', () => {
    const mgr = new SnapshotManager({
      policies: { ...RETENTION_POLICIES, manual: { limit: 5, overwrite: 'never' } },
    })
    const world = makePlayingWorld()
    for (let i = 0; i < 5; i++) {
      expect(mgr.create('manual', world)).not.toBeNull()
    }
    expect(mgr.isFull('manual')).toBe(true)
    expect(mgr.create('manual', world)).toBeNull() // caller notifies the player
    expect(mgr.count('manual')).toBe(5) // nothing was evicted
  })

  it('retention is per-type — filling auto does not touch manual', () => {
    const clock = makeClock()
    const mgr = new SnapshotManager({ now: clock.now })
    const world = makePlayingWorld()
    const keep = mgr.create('manual', world)!
    for (let i = 0; i < RETENTION_POLICIES['auto'].limit + 5; i++) {
      mgr.create('auto', world)
      clock.advance(1000)
    }
    expect(mgr.get(keep.id)).not.toBeNull()
  })

  it('unknown snapshot types get a sane default policy (§6 — types are open)', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const snap = mgr.create('boss-phase', world) // future type, zero code changes
    expect(snap).not.toBeNull()
    expect(mgr.policyFor('boss-phase').overwrite).toBe('circular')
  })
})

// ============================================================
// Auto snapshots (§10) — cadence
// ============================================================

describe('Snapshot Framework — auto cadence', () => {
  it('creates one snapshot per interval of accumulated play time', () => {
    const mgr = new SnapshotManager({ autoIntervalMs: 30_000 })
    const world = makePlayingWorld()
    let created = 0
    for (let i = 0; i < 90; i++) {
      if (mgr.updateAuto(world, 1000) !== null) created++
    }
    expect(created).toBe(3) // 90 s / 30 s
  })

  it('resetAutoTimer restarts the countdown (stage entry / recovery)', () => {
    const mgr = new SnapshotManager({ autoIntervalMs: 30_000 })
    const world = makePlayingWorld()
    mgr.updateAuto(world, 29_000)
    mgr.resetAutoTimer()
    expect(mgr.updateAuto(world, 29_000)).toBeNull() // would have fired without reset
    expect(mgr.updateAuto(world, 1000)).not.toBeNull()
  })
})

// ============================================================
// Atomic restore (§15) — full world round-trip
// ============================================================

describe('Snapshot Framework — restore round-trip', () => {
  it('restores every gameplay field, including the RNG state', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const sim = new Simulation(world, new Input())
    for (let i = 0; i < 120; i++) sim.tick() // 2 s of play

    const snap = mgr.create('manual', world)!
    const savedScore = world.score
    const savedLives = world.lives
    const savedKills = world.killCount
    const savedPlayTime = world.playTimeMs
    const savedRng = world.rng.getState()
    const savedTankCount = world.tanks.length
    const savedPlayerX = world.player!.x

    // Diverge hard
    for (let i = 0; i < 300; i++) sim.tick()
    world.score += 9999
    world.killCount += 50

    expect(mgr.restore(snap.id, world)).toBe(true)
    expect(world.score).toBe(savedScore)
    expect(world.lives).toBe(savedLives)
    expect(world.killCount).toBe(savedKills)
    expect(world.playTimeMs).toBe(savedPlayTime)
    expect(world.rng.getState()).toBe(savedRng)
    expect(world.tanks.length).toBe(savedTankCount)
    expect(world.player!.x).toBe(savedPlayerX)
  })

  it('a restored world reproduces the exact same future (determinism §2.3)', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const sim = new Simulation(world, new Input())
    for (let i = 0; i < 60; i++) sim.tick()

    const snap = mgr.create('auto', world)!

    // First future
    for (let i = 0; i < 180; i++) sim.tick()
    const future1 = { rng: world.rng.getState(), score: world.score, tanks: world.tanks.length }

    // Rewind and replay
    mgr.restore(snap.id, world)
    for (let i = 0; i < 180; i++) sim.tick()
    expect(world.rng.getState()).toBe(future1.rng)
    expect(world.score).toBe(future1.score)
    expect(world.tanks.length).toBe(future1.tanks)
  })

  it('deleting a snapshot removes it from queries', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const snap = mgr.create('manual', world)!
    mgr.delete(snap.id)
    expect(mgr.get(snap.id)).toBeNull()
    expect(mgr.count()).toBe(0)
    expect(mgr.restore(snap.id, world)).toBe(false)
  })
})

// ============================================================
// Bonus pickup window (§2.2 — timers are World state) — mid-window save
// ============================================================

describe('Snapshot Framework — bonus pickup window round-trip', () => {
  it('preserves the mid-window remaining time — restore must NOT re-open the 10s window', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const sim = new Simulation(world, new Input())
    for (let i = 0; i < 60; i++) sim.tick() // settle in

    // Mid bonus-time: window entered, 3.5 s of the 10 s window remaining.
    world.pickupWindowEntered = true
    world.pickupWindowTimer = 3500
    const savedEntered = world.pickupWindowEntered
    const savedTimer = world.pickupWindowTimer

    const snap = mgr.create('manual', world)!

    // A fresh session starts with a never-started window (false / 0).
    // Restoring must restore the saved mid-window state — otherwise
    // checkConditions re-opens the window and BONUS TIME extends by up to 10 s.
    world.pickupWindowEntered = false
    world.pickupWindowTimer = 0
    expect(mgr.restore(snap.id, world)).toBe(true)
    expect(world.pickupWindowEntered).toBe(savedEntered)
    expect(world.pickupWindowTimer).toBe(savedTimer)
  })

  it('cloneWorld captures the pickup window fields (serializer completeness)', () => {
    const world = makePlayingWorld()
    world.pickupWindowEntered = true
    world.pickupWindowTimer = 6200
    const snap = cloneWorld(world)
    expect(snap.pickupWindowEntered).toBe(true)
    expect(snap.pickupWindowTimer).toBe(6200)
  })

  it('restores legacy snapshots without pickup-window fields (defaults, no crash)', () => {
    const mgr = makeManager()
    const world = makePlayingWorld()
    const snap = mgr.create('manual', world)!
    delete snap.world.pickupWindowEntered
    delete snap.world.pickupWindowTimer
    world.pickupWindowEntered = true
    world.pickupWindowTimer = 1234
    expect(mgr.restore(snap.id, world)).toBe(true)
    expect(world.pickupWindowEntered).toBe(false)
    expect(world.pickupWindowTimer).toBe(0)
  })
})

// ============================================================
// "Load Latest" fallback rule (§11)
// ============================================================

describe('Snapshot Framework — 15 s fallback rule', () => {
  it('skips a too-recent latest snapshot when an older one exists', () => {
    const clock = makeClock()
    const mgr = new SnapshotManager({ now: clock.now })
    const world = makePlayingWorld()
    const older = mgr.create('auto', world)!
    clock.advance(60_000)
    mgr.create('auto', world)! // newest — 5 s before failure
    clock.advance(5_000)
    const picked = mgr.pickRecoverySnapshot(clock.now())
    expect(picked!.id).toBe(older.id)
  })

  it('uses the latest snapshot when it is old enough', () => {
    const clock = makeClock()
    const mgr = new SnapshotManager({ now: clock.now })
    const world = makePlayingWorld()
    mgr.create('auto', world)
    clock.advance(60_000)
    const newest = mgr.create('auto', world)!
    clock.advance(LATEST_FALLBACK_WINDOW_MS + 1)
    expect(mgr.pickRecoverySnapshot(clock.now())!.id).toBe(newest.id)
  })

  it('falls back to the only snapshot even if recent', () => {
    const clock = makeClock()
    const mgr = new SnapshotManager({ now: clock.now })
    const world = makePlayingWorld()
    const only = mgr.create('auto', world)!
    clock.advance(1000)
    expect(mgr.pickRecoverySnapshot(clock.now())!.id).toBe(only.id)
  })

  it('returns null with no snapshots', () => {
    expect(makeManager().pickRecoverySnapshot()).toBeNull()
  })
})

// ============================================================
// Queries (§12 — browser data source)
// ============================================================

describe('Snapshot Framework — queries', () => {
  it('getAll returns newest-first and supports type/stage filters', () => {
    const clock = makeClock()
    const mgr = new SnapshotManager({ now: clock.now })
    const w0 = makePlayingWorld(0)
    const w1 = makePlayingWorld(1)
    mgr.create('stage-start', w0)
    clock.advance(1000)
    mgr.create('auto', w0)
    clock.advance(1000)
    const latest = mgr.create('stage-start', w1)!

    const all = mgr.getAll()
    expect(all.length).toBe(3)
    expect(all[0].id).toBe(latest.id) // newest first

    expect(mgr.getAll({ type: 'stage-start' }).length).toBe(2)
    expect(mgr.getAll({ type: 'stage-start', stage: 1 }).length).toBe(1)
    expect(mgr.getAll({ stage: 0 }).length).toBe(2)
  })
})

// ============================================================
// Recovery flow (§11) — five options
// ============================================================

describe('Recovery Controller — mission failed flow', () => {
  it('exposes exactly the five plan options', () => {
    expect(RECOVERY_OPTION_COUNT).toBe(5)
    expect(RECOVERY_OPTIONS).toEqual([
      'continue',
      'loadLatest',
      'replayStage',
      'restartStage',
      'chooseSnapshot',
    ])
  })

  it('start() suspends the world into the recovery state', () => {
    const mgr = makeManager()
    const rc = new RecoveryController(mgr)
    const world = makePlayingWorld()
    world.state = 'gameover'
    rc.start(world)
    expect(world.state as string).toBe('recovery')
    expect(rc.phase).toBe('menu')
  })

  it('availability: snapshot options need snapshots; continue/restart never do', () => {
    const mgr = makeManager()
    const rc = new RecoveryController(mgr)
    const world = makePlayingWorld()
    expect(rc.isOptionAvailable('continue', world)).toBe(true)
    expect(rc.isOptionAvailable('restartStage', world)).toBe(true)
    expect(rc.isOptionAvailable('loadLatest', world)).toBe(false)
    expect(rc.isOptionAvailable('chooseSnapshot', world)).toBe(false)
    expect(rc.isOptionAvailable('replayStage', world)).toBe(false)

    mgr.create('stage-start', world)
    expect(rc.isOptionAvailable('loadLatest', world)).toBe(true)
    expect(rc.isOptionAvailable('chooseSnapshot', world)).toBe(true)
    expect(rc.isOptionAvailable('replayStage', world)).toBe(true)
  })

  it('continue hands back to the classic game over', () => {
    const mgr = makeManager()
    const rc = new RecoveryController(mgr)
    const world = makePlayingWorld()
    rc.start(world)
    const result = rc.select('continue', world)
    expect(result.kind).toBe('continue')
    expect(world.state).toBe('gameover')
    expect(rc.phase).toBe('idle')
  })

  it('loadLatest runs fade → restore → countdown → playing', () => {
    const mgr = makeManager()
    const rc = new RecoveryController(mgr)
    const world = makePlayingWorld()
    const snap = mgr.create('auto', world)!
    const savedScore = world.score

    world.score = 99_999
    world.state = 'gameover'
    rc.start(world)
    expect(rc.select('loadLatest', world).kind).toBe('transition')
    expect(rc.phase).toBe('fading')
    expect(world.ui.recoveryFading).toBe(true)
    expect(world.state as string).toBe('recovery')

    rc.update(world, 600) // > fade duration → restore happens
    expect(rc.phase).toBe('countdown')
    expect(world.score).toBe(savedScore) // snapshot restored
    expect(world.ui.recoveryCountdown).toBe(3)

    rc.update(world, 800)
    expect(world.ui.recoveryCountdown).toBe(2)
    rc.update(world, 800)
    expect(world.ui.recoveryCountdown).toBe(1)
    rc.update(world, 800)
    expect(world.state as string).toBe('playing')
    expect(rc.phase).toBe('idle')
    // sanity: the restored snapshot is the timeline head
    expect(mgr.create('manual', world)!.parentId).toBe(snap.id)
  })

  it('restartStage restarts fresh without loading any snapshot', () => {
    const mgr = makeManager()
    const rc = new RecoveryController(mgr)
    const world = makePlayingWorld(3)
    world.score = 5555
    world.state = 'gameover'
    rc.start(world)
    expect(rc.select('restartStage', world).kind).toBe('transition')
    rc.update(world, 600)
    expect(world.stageIndex).toBe(3) // same stage
    expect(world.score).toBe(0) // but a clean start
    expect(rc.phase).toBe('countdown')
  })

  it('chooseSnapshot only requests the browser — no world mutation', () => {
    const mgr = makeManager()
    const rc = new RecoveryController(mgr)
    const world = makePlayingWorld()
    mgr.create('manual', world)
    world.state = 'gameover'
    rc.start(world)
    expect(rc.select('chooseSnapshot', world).kind).toBe('browse')
    expect(world.state as string).toBe('recovery') // menu still active underneath
    expect(rc.phase).toBe('menu')
  })

  it('beginLoad works from any state (browser Load button)', () => {
    const mgr = makeManager()
    const rc = new RecoveryController(mgr)
    const world = makePlayingWorld()
    const snap = mgr.create('manual', world)!
    world.score = 77_777
    world.state = 'paused'
    expect(rc.beginLoad(snap.id, world)).toBe(true)
    expect(world.state as string).toBe('recovery')
    rc.update(world, 600)
    expect(world.score).toBe(4321) // restored
  })

  it('unavailable options are soft-denied', () => {
    const mgr = makeManager() // empty — no snapshots
    const rc = new RecoveryController(mgr)
    const world = makePlayingWorld()
    rc.start(world)
    expect(rc.select('loadLatest', world).kind).toBe('none')
    expect(rc.phase).toBe('menu') // still in the menu
  })
})

// ============================================================
// Persistence backend contract (§16) — in-memory stub
// ============================================================

describe('Snapshot Framework — storage backend', () => {
  it('persists on create, deletes on delete, hydrates on startup', async () => {
    const store = new Map<string, GameSnapshot>()
    const backend: SnapshotStorageBackend = {
      save: async (s) => {
        store.set(s.id, s)
      },
      delete: async (id) => {
        store.delete(id)
      },
      loadAll: async () => [...store.values()],
    }

    const mgr1 = new SnapshotManager({ backend })
    const world = makePlayingWorld()
    const a = mgr1.create('manual', world)!
    const b = mgr1.create('auto', world)!
    await Promise.resolve() // let fire-and-forget saves settle
    expect(store.size).toBe(2)

    mgr1.delete(b.id)
    await Promise.resolve()
    expect(store.size).toBe(1)

    // A fresh manager hydrates the survivor
    const mgr2 = new SnapshotManager({ backend })
    await mgr2.hydrate()
    expect(mgr2.count()).toBe(1)
    expect(mgr2.get(a.id)).not.toBeNull()
    expect(mgr2.restore(a.id, new World())).toBe(true)
  })
})
