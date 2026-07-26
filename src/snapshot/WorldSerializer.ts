import type { World, SpawnEntry } from '../game/World'
import type { Tank, Bullet, PowerUp, TerrainType } from '../types'
import type { WorldSnapshot } from './types'
import { GRID } from '../constants'

// ================================================================
// World (De)Serialization — deep clone & atomic restore
//
// Moved from the retired RecoverySystem and extended with the run
// statistics (killCount / playTimeMs) required by snapshot metadata.
// The serializer is the ONLY place that knows how to copy a World;
// SnapshotManager and RecoveryController build on it.
// ================================================================

function cloneTank(t: Tank): Tank {
  return {
    ...t,
    aiState: t.aiState ? { ...t.aiState } : undefined,
  }
}

function cloneBullet(b: Bullet): Bullet {
  return { ...b }
}

function clonePowerUp(p: PowerUp): PowerUp {
  return { ...p }
}

function cloneSpawnEntry(s: SpawnEntry): SpawnEntry {
  return { ...s }
}

/**
 * Deep-clone all gameplay-relevant state from the World into a
 * self-contained snapshot. The snapshot shares no references with the
 * live World, so subsequent gameplay mutations never corrupt it.
 */
export function cloneWorld(world: World): WorldSnapshot {
  // Tile grid — copy each row
  const tileGrid: TerrainType[][] = []
  for (let r = 0; r < GRID; r++) {
    tileGrid.push([...world.tileMap.grid[r]])
  }

  return {
    tileGrid,
    player: world.player ? cloneTank(world.player) : null,
    tanks: world.tanks.map(cloneTank),
    bullets: world.bullets.map(cloneBullet),
    powerUps: world.powerUps.map(clonePowerUp),
    stageIndex: world.stageIndex,
    spawnQueue: world.spawnQueue.map(cloneSpawnEntry),
    enemiesSpawned: world.enemiesSpawned,
    enemiesRemaining: world.enemiesRemaining,
    spawnPointIndex: world.spawnPointIndex,
    score: world.score,
    lives: world.lives,
    playerLevel: world.playerLevel,
    highScore: world.highScore,
    killCount: world.killCount,
    playTimeMs: world.playTimeMs,
    freezeTimer: world.freezeTimer,
    stageClearTimer: world.stageClearTimer,
    gameOverTimer: world.gameOverTimer,
    spawnTimer: world.spawnTimer,
    rngState: world.rng.getState(),
    frame: world.frame,
    bulletSeq: world.bulletSeq,
    spawnSeqCounter: world.spawnSeqCounter,
    activeCommanderId: world.activeCommanderId,
    commanderQuotaRemaining: world.commanderQuotaRemaining,
    directiveSeqCounter: world.directiveSeqCounter,
    baseHp: world.baseHp,
    baseMaxHp: world.baseMaxHp,
  }
}

/**
 * Restore the World from a snapshot. Every gameplay field is overwritten
 * atomically; transient visual data (explosions, popups, events) is
 * cleared. The World object identity is preserved so that all existing
 * references (Game, Simulation, Presentation) remain valid.
 *
 * The snapshot itself is re-cloned during restoration so that it stays
 * immutable and can be reused for future recoveries (Constitution §4 —
 * loading a snapshot restores history, it does not rewrite it).
 */
export function restoreWorld(world: World, snap: WorldSnapshot): void {
  // Tile grid
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) {
      world.tileMap.grid[r][c] = snap.tileGrid[r][c]
    }
  }
  // Rebuild cached base state and mark terrain dirty for renderer
  world.tileMap.rebuildBaseCache()
  world.tileMap.dirty = true

  // Entities — clone from snapshot so the snapshot stays pristine
  world.player = snap.player ? cloneTank(snap.player) : null
  world.tanks = snap.tanks.map(cloneTank)
  world.bullets = snap.bullets.map(cloneBullet)
  world.powerUps = snap.powerUps.map(clonePowerUp)

  // Clear transient visual data — Presentation will rebuild
  world.explosions = []
  world.popups = []
  world.events = []

  // Stage info
  world.stageIndex = snap.stageIndex
  world.spawnQueue = snap.spawnQueue.map(cloneSpawnEntry)
  world.enemiesSpawned = snap.enemiesSpawned
  world.enemiesRemaining = snap.enemiesRemaining
  world.spawnPointIndex = snap.spawnPointIndex ?? 0

  // Game state
  world.score = snap.score
  world.lives = snap.lives
  world.playerLevel = snap.playerLevel
  world.highScore = snap.highScore
  world.killCount = snap.killCount ?? 0
  world.playTimeMs = snap.playTimeMs ?? 0

  // Timers
  world.freezeTimer = snap.freezeTimer
  world.stageClearTimer = snap.stageClearTimer
  world.gameOverTimer = 0
  world.spawnTimer = snap.spawnTimer

  // RNG
  world.rng.reseed(snap.rngState)

  // Frame counter
  world.frame = snap.frame

  // Bullet counter (per-bullet jitter seed)
  world.bulletSeq = snap.bulletSeq ?? 0

  // AI command authority (plan §4, §7)
  world.spawnSeqCounter = snap.spawnSeqCounter ?? 0
  world.activeCommanderId = snap.activeCommanderId ?? null
  world.commanderQuotaRemaining = snap.commanderQuotaRemaining ?? 0
  world.directiveSeqCounter = snap.directiveSeqCounter ?? 0

  // Base (eagle) HP
  world.baseMaxHp = snap.baseMaxHp ?? 0
  world.baseHp = snap.baseHp ?? world.baseMaxHp ?? 0

  // Resume playing
  world.state = 'playing'
}
