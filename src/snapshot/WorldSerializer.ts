import type { World, SpawnEntry } from '../game/World'
import type { Tank, Bullet, PowerUp, TerrainType } from '../types'
import type { WorldSnapshot } from './types'
import { GRID } from '../constants'
import { RULES, DEFAULT_RULES } from '../config/rules'
import { DIFFICULTIES } from '../config/difficulty'
import { THEMES, DEFAULT_THEME } from '../config/theme'

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
    allies: world.allies.map(cloneTank),
    bullets: world.bullets.map(cloneBullet),
    powerUps: world.powerUps.map(clonePowerUp),
    pendingDrops: world.pendingDrops.map((d) => ({ type: d.type, x: d.x, y: d.y })),
    stageIndex: world.stageIndex,
    spawnQueue: world.spawnQueue.map(cloneSpawnEntry),
    enemiesSpawned: world.enemiesSpawned,
    enemiesRemaining: world.enemiesRemaining,
    enemiesTotal: world.enemiesTotal,
    spawnPointIndex: world.spawnPointIndex,
    enemySpawnPoints: world.enemySpawnPoints.map((p) => ({ x: p.x, y: p.y })),
    playerSpawnPoint: { ...world.playerSpawnPoint },
    score: world.score,
    lives: world.lives,
    playerLevel: world.playerLevel,
    // Lie-Back-Win-Mode: coop state
    coop: world.coop,
    // 督战 (supervise) mode: God AI as player1
    spectate: world.spectate,
    player2: world.player2 ? cloneTank(world.player2) : null,
    lives2: world.lives2,
    playerLevel2: world.playerLevel2,
    score2: world.score2,
    player2SpawnPoint: world.player2SpawnPoint ? { ...world.player2SpawnPoint } : undefined,
    highScore: world.highScore,
    killCount: world.killCount,
    playTimeMs: world.playTimeMs,
    // Run profile — persisted so a loaded save restores the exact rules
    // profile it was created with (bug: a classic save must not run modern
    // rules after load). rules/difficulty/theme are re-derived on restore.
    difficultyKey: world.difficultyKey,
    themeKey: world.themeKey,
    freezeTimer: world.freezeTimer,
    stageClearTimer: world.stageClearTimer,
    gameOverTimer: world.gameOverTimer,
    spawnTimer: world.spawnTimer,
    // Post-victory bonus pickup window — a mid-window save must restore the
    // remaining time, not re-open the full 10s window.
    pickupWindowTimer: world.pickupWindowTimer,
    pickupWindowEntered: world.pickupWindowEntered,
    rngState: world.rng.getState(),
    frame: world.frame,
    bulletSeq: world.bulletSeq,
    spawnSeqCounter: world.spawnSeqCounter,
    activeCommanderId: world.activeCommanderId,
    commanderQuotaRemaining: world.commanderQuotaRemaining,
    directiveSeqCounter: world.directiveSeqCounter,
    baseHp: world.baseHp,
    baseMaxHp: world.baseMaxHp,
    // Super power-up inventory & frenzy state (§31)
    guardStock: world.guardStock,
    frenzyStock: world.frenzyStock,
    sacrificeStock: world.sacrificeStock,
    fenceExpireFrame: world.fenceExpireFrame,
    // New power-ups (new-powerups-plan.md)
    empTimer: world.empTimer,
    rewindStock: world.rewindStock,
    mines: world.mines.map((m) => ({ ...m })),
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
  world.allies = snap.allies ? snap.allies.map(cloneTank) : []
  world.bullets = snap.bullets.map(cloneBullet)
  world.powerUps = snap.powerUps.map(clonePowerUp)
  world.pendingDrops = snap.pendingDrops
    ? snap.pendingDrops.map((d) => ({ type: d.type, x: d.x, y: d.y }))
    : []

  // Clear transient visual data — Presentation will rebuild
  world.explosions = []
  world.popups = []
  world.events = []

  // Stage info
  world.stageIndex = snap.stageIndex
  world.spawnQueue = snap.spawnQueue.map(cloneSpawnEntry)
  world.enemiesSpawned = snap.enemiesSpawned
  world.enemiesRemaining = snap.enemiesRemaining
  world.enemiesTotal = snap.enemiesTotal ?? snap.enemiesRemaining // legacy fallback
  world.spawnPointIndex = snap.spawnPointIndex ?? 0
  // Restore stage-specific spawn points (plan/God-AI-Curriculum §3.5)
  world.enemySpawnPoints = snap.enemySpawnPoints
    ? snap.enemySpawnPoints.map((p) => ({ x: p.x, y: p.y }))
    : world.enemySpawnPoints // keep defaults if missing (legacy snapshot)
  world.playerSpawnPoint = snap.playerSpawnPoint
    ? { ...snap.playerSpawnPoint }
    : world.playerSpawnPoint

  // Game state
  world.score = snap.score
  world.lives = snap.lives
  world.playerLevel = snap.playerLevel
  // Lie-Back-Win-Mode: restore coop state (backward compat: default to off)
  world.coop = snap.coop ?? false
  // 督战 (supervise) mode: restore spectate state (backward compat: off)
  world.spectate = snap.spectate ?? false
  world.player2 = snap.player2 ? cloneTank(snap.player2) : null
  world.lives2 = snap.lives2 ?? 0
  world.playerLevel2 = snap.playerLevel2 ?? 0
  world.score2 = snap.score2 ?? 0
  world.player2SpawnPoint = snap.player2SpawnPoint ?? { col: 16, row: 24 }
  world.highScore = snap.highScore
  world.killCount = snap.killCount ?? 0
  world.playTimeMs = snap.playTimeMs ?? 0

  // Run profile: restore the difficulty/theme keys and re-derive the rules,
  // difficulty, and theme so a loaded save plays with the EXACT profile it was
  // created under. Previously restoreWorld left `rules`/`difficulty`/`theme`
  // untouched, so a classic save loaded into a World holding modern rules (the
  // menu default) silently ran the modern ruleset. A snapshot is a complete
  // World description (Constitution §6), so the profile must travel with it.
  world.difficultyKey = snap.difficultyKey ?? world.difficultyKey
  world.themeKey = snap.themeKey ?? world.themeKey
  world.rules = RULES[world.difficultyKey] ?? DEFAULT_RULES
  world.difficulty = DIFFICULTIES[world.difficultyKey] ?? DIFFICULTIES['classic']
  world.theme = THEMES[world.themeKey] ?? THEMES[DEFAULT_THEME]

  // Timers
  world.freezeTimer = snap.freezeTimer
  world.stageClearTimer = snap.stageClearTimer
  world.gameOverTimer = 0
  world.spawnTimer = snap.spawnTimer
  // Bonus pickup window (legacy fallback: pre-window state). A fresh-session
  // restore mid-window must keep the remaining time — dropping these would
  // make checkConditions re-open the full 10s window and extend BONUS TIME.
  world.pickupWindowTimer = snap.pickupWindowTimer ?? 0
  world.pickupWindowEntered = snap.pickupWindowEntered ?? false

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

  // Super power-up inventory & frenzy state (§31)
  world.guardStock = snap.guardStock ?? 0
  world.frenzyStock = snap.frenzyStock ?? 0
  world.sacrificeStock = snap.sacrificeStock ?? 0
  world.fenceExpireFrame = snap.fenceExpireFrame

  // New power-ups (new-powerups-plan.md)
  world.empTimer = snap.empTimer ?? 0
  world.rewindStock = snap.rewindStock ?? 0
  world.mines = snap.mines ? snap.mines.map((m) => ({ ...m })) : []

  // Resume playing
  world.state = 'playing'
}
