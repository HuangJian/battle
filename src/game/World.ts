import type {
  Tank,
  Bullet,
  PowerUp,
  Explosion,
  ScorePopup,
  GameEvent,
  GameState,
  TankKind,
  DifficultyConfig,
  ThemeColors,
  AIState,
  GoalType,
} from '../types'
import type { Direction } from '../constants'
import { TileMap } from './TileMap'
import { RNG } from '../utils/RNG'
import { STAGES } from '../config/stages'
import { DIFFICULTIES } from '../config/difficulty'
import { THEMES, DEFAULT_THEME } from '../config/theme'
import { resolveProfile, profileToStats } from '../config/combat'
import { rollSpeedJitter } from '../config/speed'
import { resolveConfig, levelForKind } from '../ai/config'
import { restoreWorld } from '../snapshot/WorldSerializer'
import type { WorldSnapshot } from '../snapshot/types'
import {
  GRID,
  CELL,
  TANK,
  ENEMIES_PER_STAGE,
  START_LIVES,
  STRATEGIC_INTERVAL_MS,
  COMMANDER_INTERVAL_MS,
} from '../constants'

let nextId = 1
export function genId(): number {
  return nextId++
}

/** Spawn queue entry */
export interface SpawnEntry {
  kind: TankKind
  bonus: boolean
  spawnIndex: number
}

/**
 * World — the complete runtime game state.
 * No hidden state outside this object.
 * Only Simulation may modify the World.
 */
export class World {
  // Terrain
  tileMap: TileMap

  // Entities
  player: Tank | null
  tanks: Tank[] // enemy tanks
  bullets: Bullet[]
  powerUps: PowerUp[]
  explosions: Explosion[]
  popups: ScorePopup[]

  // Stage info
  stageIndex: number
  spawnQueue: SpawnEntry[]
  enemiesSpawned: number
  enemiesRemaining: number
  /**
   * Round-robin cursor over the enemy spawn points. Lives on the World (not
   * the Simulation) because it affects gameplay — a rewound World must
   * reproduce the exact same spawn positions (AGENTS §2.2, §2.3).
   */
  spawnPointIndex: number

  // Game state
  state: GameState
  score: number
  lives: number
  playerLevel: number
  highScore: number
  /** Enemies destroyed by the player this run (snapshot metadata). */
  killCount: number
  /** Total gameplay time this run (ms) — advanced only while playing. */
  playTimeMs: number

  // Timers
  freezeTimer: number // enemy freeze countdown (ms)
  stageClearTimer: number // transition timer
  gameOverTimer: number
  spawnTimer: number // delay between spawns
  /** Post-victory bonus collection window (ms) — counts down while the player
   *  grabs any power-ups left after the last enemy is destroyed. 0 = inactive. */
  pickupWindowTimer: number
  /** True once the bonus window has begun for the current stage (so it only
   *  starts once, even though checkConditions runs every frame). */
  pickupWindowEntered: boolean

  // Config
  difficulty: DifficultyConfig
  theme: ThemeColors
  themeKey: string
  difficultyKey: string
  menuCursor: number
  selectedStage: number
  rng: RNG

  // Events (consumed by renderer/audio/stats)
  events: GameEvent[]

  /**
   * Spare event buffer for double-buffering. consumeEvents() swaps the active
   * and spare buffers so the per-frame event array is never reallocated.
   */
  private eventsSpare: GameEvent[] = []

  // Reusable buffer for allTanks getter — avoids allocating a new array each call
  private _allTanksBuf: Tank[] = []

  // Animation frame counter
  frame: number

  /**
   * Monotonic per-world counter incremented each time a bullet is fired. It is
   * the deterministic seed for per-bullet speed jitter (see config/speed.ts
   * `bulletSpeedJitter`) so cosmetic variation never depends on the
   * module-level `genId()` counter (which is NOT reset between Worlds and would
   * break cross-run determinism) nor on the AI's world-RNG stream. Snapshotted
   * by WorldSerializer so recovery restores a consistent jitter sequence.
   */
  bulletSeq: number

  // Recovery UI state (read by UIManager, written by RecoveryController)
  recoveryCursor: number // selected recovery menu option index
  recoveryCountdown: number // 0 = none, 3/2/1 = counting down
  recoveryFading: boolean // true while fading to black before restore

  constructor() {
    this.tileMap = new TileMap()
    this.player = null
    this.tanks = []
    this.bullets = []
    this.powerUps = []
    this.explosions = []
    this.popups = []
    this.stageIndex = 0
    this.spawnQueue = []
    this.enemiesSpawned = 0
    this.enemiesRemaining = 0
    this.spawnPointIndex = 0
    this.state = 'menu'
    this.score = 0
    this.lives = START_LIVES
    this.playerLevel = 0
    this.killCount = 0
    this.playTimeMs = 0
    this.highScore = this.loadHighScore()
    this.freezeTimer = 0
    this.stageClearTimer = 0
    this.gameOverTimer = 0
    this.spawnTimer = 0
    this.pickupWindowTimer = 0
    this.pickupWindowEntered = false
    this.difficulty = DIFFICULTIES['classic']
    this.difficultyKey = 'classic'
    this.theme = THEMES[DEFAULT_THEME]
    this.themeKey = DEFAULT_THEME
    this.menuCursor = 0
    this.selectedStage = 0
    // Show the selected stage's layout behind the start menu from the outset.
    this.previewStage(this.selectedStage)
    this.rng = new RNG(Date.now())
    this.events = []
    this.frame = 0
    this.bulletSeq = 0
    this.recoveryCursor = 0
    this.recoveryCountdown = 0
    this.recoveryFading = false
  }

  // ---- Lifecycle ----

  startGame(difficultyKey: string, themeKey: string, startStage = 0): void {
    this.difficultyKey = difficultyKey
    this.themeKey = themeKey
    this.difficulty = DIFFICULTIES[difficultyKey] ?? DIFFICULTIES['classic']
    this.theme = THEMES[themeKey] ?? THEMES[DEFAULT_THEME]
    this.score = 0
    this.lives = this.difficulty.startLives
    this.playerLevel = this.difficulty.playerStartLevel
    this.killCount = 0
    this.playTimeMs = 0
    this.loadStage(startStage)
  }

  loadStage(index: number): void {
    const stage = STAGES[index]
    if (!stage) {
      this.state = 'victory'
      this.pushEvent({ type: 'stage_clear', stage: this.stageIndex })
      return
    }

    this.tileMap.loadStage(stage)
    this.tanks = []
    this.bullets = []
    this.powerUps = []
    this.explosions = []
    this.popups = []

    this.stageIndex = index
    this.enemiesSpawned = 0
    this.enemiesRemaining = ENEMIES_PER_STAGE
    this.spawnPointIndex = 0
    this.freezeTimer = 0
    this.stageClearTimer = 0
    this.gameOverTimer = 0
    this.spawnTimer = 0
    this.pickupWindowTimer = 0
    this.pickupWindowEntered = false

    // Build spawn queue. The elite roll is intentionally NOT performed here:
    // it happens at spawn time in `Simulation.updateSpawning` so the RNG cost
    // is paid per-spawn (and is skipped entirely on difficulties with
    // `eliteChance === 0`, e.g. classic) instead of consuming 20 RNG calls
    // up front and shifting the whole downstream stream (DECISIONS.md).
    this.spawnQueue = []
    const enemies = stage.enemies
    for (let i = 0; i < ENEMIES_PER_STAGE; i++) {
      const kind = enemies[i % enemies.length]
      this.spawnQueue.push({
        kind,
        bonus: i % 4 === 3,
        spawnIndex: i,
      })
    }

    // Spawn player
    this.spawnPlayer()
    this.state = 'playing'
  }

  /**
   * Load a stage's terrain into the tileMap for the start-screen preview.
   * Unlike loadStage, this does NOT spawn entities or change game state — it
   * only swaps the static layout so the battle field behind the menu shows the
   * selected stage's starting formation (and updates when the selection moves).
   * Any entities left behind by a snapshot preview are cleared so the field
   * shows only the selected stage's terrain (no stray tanks / bullets).
   */
  previewStage(index: number): void {
    const stage = STAGES[index]
    if (!stage) return
    this.tileMap.loadStage(stage)
    this.player = null
    this.tanks = []
    this.bullets = []
    this.powerUps = []
    this.explosions = []
    this.popups = []
    this.events = []
    this.stageIndex = index
  }

  /**
   * Load a saved snapshot into the world for the start-screen RESUME preview
   * only. The full battlefield (terrain + tanks + bullets + power-ups) is
   * restored so the canvas behind the menu renders exactly where the player
   * left off — but `state` is kept as 'menu' so no gameplay runs. Differs from
   * recover.beginLoad(), which also fades and counts down into 'playing'.
   */
  previewSnapshot(snap: WorldSnapshot): void {
    restoreWorld(this, snap)
    this.state = 'menu'
  }

  spawnPlayer(): void {
    const col = 8 // sub-block coords
    const row = 24
    this.player = this.createTank('player', col * CELL, row * CELL, 'up')
    this.player.level = this.playerLevel
    this.player.shieldTimer = 3000
    this.player.isPlayer = true
  }

  createTank(kind: TankKind, x: number, y: number, dir: Direction): Tank {
    // Combat Capability System: stats come from the tank's profile, not
    // hardcoded numbers. Player profiles scale with star level; enemies use
    // their fixed archetype profile (modified only when promoted to elite).
    const profile = resolveProfile(kind, kind === 'player' ? this.playerLevel : 0)
    const stats = profileToStats(profile, kind, kind === 'player' ? this.playerLevel : 0)
    // Enemy combat stats (including HP/armor) are fixed per archetype and never
    // scaled by difficulty — difficulty only makes enemies smarter via
    // DIFFICULTY_AI (see DECISIONS.md: Tactical Intelligence Framework). Scaling
    // enemy HP here would "enhance enemy power", which is explicitly forbidden.
    const hp = stats.maxHp

    // Enemy brains are initialized here (on the World — no hidden state).
    // The Tactical Intelligence Framework reads/writes these fields every tick.
    let aiState: AIState | undefined
    if (kind !== 'player') {
      const level = levelForKind(kind)
      const ai = resolveConfig(level, this.difficultyKey)
      const base = this.tileMap.getBasePos()
      aiState = {
        level,
        isCommander: false,
        thinkTimer: 200 + this.rng.next() * 600,
        fireTimer: 400 + this.rng.next() * 600,
        currentDir: dir,
        tacticalGoal: 'advance' as GoalType,
        targetX: base ? base.x + CELL : x + TANK / 2,
        targetY: base ? base.y + CELL : y + TANK / 2,
        strategicTimer: STRATEGIC_INTERVAL_MS * (0.8 + this.rng.next() * 0.4),
        strategicGoal: 'attackBase' as GoalType,
        reactionTimer: ai.reactionTime,
        dodgeLock: 0,
        commanderTimer: COMMANDER_INTERVAL_MS,
        directive: 'none',
        directiveAge: 1e9,
      }
    }

    return {
      id: genId(),
      x,
      y,
      w: TANK,
      h: TANK,
      dir,
      alive: true,
      kind,
      // Per-instance speed jitter (±5%): identical archetypes don't move in
      // lockstep, but it's drawn from world.rng so it stays deterministic.
      speed: stats.speed * rollSpeedJitter(this.rng),
      hp,
      maxHp: hp,
      bulletPower: stats.bulletPower,
      damage: stats.damage,
      bulletSpeed: stats.bulletSpeed,
      fireCooldown: stats.fireCooldown,
      nextFireInterval: stats.fireCooldown,
      fireCount: 0,
      lastFire: 0,
      moving: false,
      vx: 0,
      vy: 0,
      spawnTimer: 1000,
      level: kind === 'player' ? this.playerLevel : 0,
      shieldTimer: kind === 'player' ? 3000 : 0,
      isPlayer: kind === 'player',
      profile,
      flashTimer: 0,
      hitCount: 0,
      aiState,
      bonus: false,
    }
  }

  // ---- Entity Management ----

  addBullet(bullet: Bullet): void {
    this.bullets.push(bullet)
  }

  removeBullet(id: number): void {
    // In-place swap-and-pop (bullet order is irrelevant) — avoids allocating a
    // fresh array on every removal.
    const arr = this.bullets
    for (let i = 0; i < arr.length; i++) {
      if (arr[i].id === id) {
        const last = arr.length - 1
        arr[i] = arr[last]
        arr.pop()
        return
      }
    }
  }

  addExplosion(exp: Explosion): void {
    this.explosions.push(exp)
  }

  addPowerUp(pu: PowerUp): void {
    this.powerUps.push(pu)
  }

  addPopup(popup: ScorePopup): void {
    this.popups.push(popup)
  }

  removeDeadEntities(): void {
    // In-place compaction — avoids creating 5 new arrays every tick
    this.compact(this.tanks, (t) => t.alive)
    this.compact(this.bullets, (b) => b.alive)
    this.compact(this.powerUps, (p) => p.alive)
    this.compact(this.explosions, (e) => e.timer > 0)
    this.compact(this.popups, (p) => p.timer > 0)
  }

  /** Remove elements that don't match the predicate, in-place (swap-and-pop). */
  private compact<T>(arr: T[], predicate: (item: T) => boolean): void {
    let w = 0
    for (let r = 0; r < arr.length; r++) {
      if (predicate(arr[r])) {
        arr[w++] = arr[r]
      }
    }
    arr.length = w
  }

  // ---- Queries ----

  get allTanks(): Tank[] {
    if (this.player) {
      // Reuse buffer — avoids creating a new array every call (called ~10×/tick)
      this._allTanksBuf[0] = this.player
      const tanks = this.tanks
      for (let i = 0; i < tanks.length; i++) {
        this._allTanksBuf[i + 1] = tanks[i]
      }
      this._allTanksBuf.length = tanks.length + 1
      return this._allTanksBuf
    }
    return this.tanks
  }

  get enemyCount(): number {
    let count = 0
    const tanks = this.tanks
    for (let i = 0; i < tanks.length; i++) {
      if (tanks[i].spawnTimer <= 0) count++
    }
    return count
  }

  get totalEnemiesLeft(): number {
    return this.enemiesRemaining
  }

  get currentStageName(): string {
    return STAGES[this.stageIndex]?.name ?? '?'
  }

  get totalStages(): number {
    return STAGES.length
  }

  // ---- Events ----

  pushEvent(event: GameEvent): void {
    this.events.push(event)
  }

  consumeEvents(): GameEvent[] {
    // Double-buffer: return the current accumulation buffer and start fresh in
    // the spare (now-cleared) buffer. No per-frame array allocation.
    const out = this.events
    this.events = this.eventsSpare
    this.eventsSpare = out
    this.events.length = 0
    return out
  }

  // ---- Persistence ----

  private loadHighScore(): number {
    try {
      return parseInt(localStorage.getItem('bc_highscore') || '0', 10) || 0
    } catch {
      return 0
    }
  }

  saveHighScore(): void {
    if (this.score > this.highScore) {
      this.highScore = this.score
      try {
        localStorage.setItem('bc_highscore', String(this.highScore))
      } catch {
        /* ignore */
      }
    }
  }

  // ---- Grid helpers ----

  isCellBlocked(col: number, row: number): boolean {
    return TileMap.blocksTank(this.tileMap.get(col, row))
  }

  /** Check if a rectangle (in pixels) collides with blocking terrain */
  rectHitsTerrain(x: number, y: number, w: number, h: number, ignoreWater = false): boolean {
    const c0 = Math.floor(x / CELL)
    const r0 = Math.floor(y / CELL)
    const c1 = Math.floor((x + w - 1) / CELL)
    const r1 = Math.floor((y + h - 1) / CELL)
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        const type = this.tileMap.get(c, r)
        if (TileMap.blocksTank(type)) {
          if (ignoreWater && type === 'water') continue
          return true
        }
      }
    }
    return false
  }

  /** Check if a tank can move through water (has boat power-up) */
  canTankTraverseWater(tank: { boatTimer?: number }): boolean {
    return !!(tank.boatTimer && tank.boatTimer > 0)
  }

  /** Check if a rect is fully inside the playfield */
  isInBounds(x: number, y: number, w: number, h: number): boolean {
    return x >= 0 && y >= 0 && x + w <= GRID * CELL && y + h <= GRID * CELL
  }

  /**
   * True when the tank's footprint center sits over `ice` terrain. Drives the
   * ice-momentum model in Simulation.updateMovement (low traction ⇒ slide).
   * Uses the tank center so a tank is "on ice" exactly when its body is over
   * the slippery tile. Pure function of World state — no RNG, deterministic.
   */
  isTankOnIce(tank: { x: number; y: number; w: number; h: number }): boolean {
    const cx = tank.x + tank.w / 2
    const cy = tank.y + tank.h / 2
    const c = Math.floor(cx / CELL)
    const r = Math.floor(cy / CELL)
    return this.tileMap.get(c, r) === 'ice'
  }
}
