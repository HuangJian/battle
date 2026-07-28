import type {
  Tank,
  Bullet,
  PowerUp,
  PowerUpType,
  Explosion,
  ScorePopup,
  GameEvent,
  GameState,
  TankKind,
  DifficultyConfig,
  ThemeColors,
  AIState,
  GoalType,
  StageData,
} from '../types'
import type { Direction } from '../constants'
import { TileMap } from './TileMap'
import { RNG } from '../utils/RNG'
import { STAGES } from '../config/stages'
import { DIFFICULTIES } from '../config/difficulty'
import { THEMES, DEFAULT_THEME } from '../config/theme'
import { resolveProfile, profileToStats } from '../config/combat'
import { RULES, DEFAULT_RULES, hasStarPerk } from '../config/rules'
import type { GameplayRules } from '../config/rules'
import { rollSpeedJitter } from '../config/speed'
import { INTELLIGENCE_LEVELS, COMMANDER_FLOOR } from '../ai/config'
import { BASE_MAX_HP, CLASSIC_BASE_MAX_HP } from '../config/base'
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
  allies: Tank[] // 天降神兵 allied guard tanks (third faction, DECISIONS.md §31 Phase 2)
  bullets: Bullet[]
  powerUps: PowerUp[]
  explosions: Explosion[]
  popups: ScorePopup[]
  /**
   * Power-up drops deferred because they were triggered by the FINAL enemy of
   * a stage (dropping them would have them wiped by the stage-clear
   * transition). Released on the first enemy kill of the following stage
   * (item-drop v1, DECISIONS.md §30). Stores the already-resolved type +
   * position so the buffered drop stays deterministic and snapshot-safe.
   */
  pendingDrops: { type: PowerUpType; x: number; y: number }[]

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
  /** Active gameplay-rules profile (MANIFEST §2.2: lives on the World, never a
   *  module global). Set in `startGame` from `RULES[difficultyKey]`. Survives
   *  snapshot rewind because `restoreWorld` never touches it (plan §1). */
  rules: GameplayRules
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

  /**
   * Per-World monotonic birth-order counter. Incremented in createTank for
   * every enemy tank; the value is stamped onto `aiState.spawnSeq`. Command
   * authority is `argmax(spawnSeq)` over alive commander-tier tanks, so this
   * must be a *per-World* counter (NOT genId(), which is not reset between
   * Worlds). Monotonic — not reset per stage (plan §7). Snapshotted.
   */
  spawnSeqCounter: number

  /**
   * The tank currently holding command authority (highest-spawnSeq alive
   * commander-tier tank), or null when none is alive. Recomputed once per
   * tick by Simulation (before ai.update) — the AI layer only reads it.
   */
  activeCommanderId: number | null

  /**
   * Per-stage remaining Commander *spawn attempts* (the floor guarantee,
   * plan §5.1 [D9]). Decremented on every Commander roll (incl.
   * cap-downgraded ones) so the floor is always satisfiable. Initialized
   * from the difficulty floor in `loadStage`.
   */
  commanderQuotaRemaining: number

  /**
   * Monotonic counter incremented on every active-Commander broadcast.
   * Each directive carries this seq; receiving tanks re-roll compliance only
   * when it changes (plan §2.2 [D7]). Snapshotted.
   */
  directiveSeqCounter: number

  // Base (eagle) hit points (2026-07-27): ONE fixed HP (BASE_MAX_HP) on every
  // difficulty; damage per bullet equals the shooter's firepower value.
  baseHp: number
  baseMaxHp: number

  // --- Super power-up inventory & frenzy state (DECISIONS.md §31) ---
  // Accumulated counts from picking up super power-ups (强力道具).
  guardStock: number // 天降神兵 — Phase 2 summons a base guard
  frenzyStock: number // 狂暴宣泄 — active F6 barrage
  sacrificeStock: number // 同归于尽 — passive AoE on losing a life
  // Active 狂暴宣泄 barrage runtime. Snapshot-safe so a rewind mid-barrage
  // (and the enemy-kill that may trigger it) is faithful.
  frenzyTimer: number // ms remaining in the current barrage (0 = inactive)
  frenzyShotsLeft: number // shells left to fire this barrage
  frenzyLastFire: number // ms timestamp of the last frenzy shell
  frenzyInterval: number // ms between frenzy shells (= player fire interval / 5)
  frenzyDir: Direction // locked firing direction during the barrage
  // 栅栏道具 (fence): the frame at which the temporary steel ring around the
  // base reverts to brick. undefined = no active fence. Snapshot-safe.
  fenceExpireFrame?: number

  // Recovery UI state (read by UIManager, written by RecoveryController)
  recoveryCursor: number // selected recovery menu option index
  recoveryCountdown: number // 0 = none, 3/2/1 = counting down
  recoveryFading: boolean // true while fading to black before restore

  constructor() {
    this.tileMap = new TileMap()
    this.player = null
    this.tanks = []
    this.allies = []
    this.bullets = []
    this.powerUps = []
    this.explosions = []
    this.popups = []
    this.pendingDrops = []
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
    // Before startGame() is called there is no real game; use the modern
    // DEFAULT_RULES so a pre-game World (and the menu preview) behaves like
    // every modern difficulty. startGame() re-derives `rules` from the chosen
    // difficulty — including the faithful classic profile.
    this.rules = DEFAULT_RULES
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
    this.spawnSeqCounter = 0
    this.activeCommanderId = null
    this.commanderQuotaRemaining = 0
    this.directiveSeqCounter = 0
    this.baseHp = 1
    this.baseMaxHp = 1
    this.recoveryCursor = 0
    this.recoveryCountdown = 0
    this.recoveryFading = false
    // Super power-up inventory & frenzy (DECISIONS.md §31)
    this.guardStock = 0
    this.frenzyStock = 0
    this.sacrificeStock = 0
    this.frenzyTimer = 0
    this.frenzyShotsLeft = 0
    this.frenzyLastFire = 0
    this.frenzyInterval = 0
    this.frenzyDir = 'up'
    this.fenceExpireFrame = undefined
  }

  // ---- Lifecycle ----

  startGame(difficultyKey: string, themeKey: string, startStage = 0): void {
    this.difficultyKey = difficultyKey
    this.rules = RULES[difficultyKey] ?? DEFAULT_RULES
    this.themeKey = themeKey
    this.difficulty = DIFFICULTIES[difficultyKey] ?? DIFFICULTIES['classic']
    this.theme = THEMES[themeKey] ?? THEMES[DEFAULT_THEME]
    this.score = 0
    this.lives = this.difficulty.startLives
    this.playerLevel = this.difficulty.playerStartLevel
    this.killCount = 0
    this.playTimeMs = 0
    // Fresh run: clear any deferred drops left over from a previous game
    // (e.g. a buffered drop from the final stage of a won run).
    this.pendingDrops = []
    // Fresh run: reset super power-up inventory & frenzy state (§31).
    this.guardStock = 0
    this.frenzyStock = 0
    this.sacrificeStock = 0
    this.frenzyTimer = 0
    this.frenzyShotsLeft = 0
    this.frenzyLastFire = 0
    this.frenzyInterval = 0
    this.frenzyDir = 'up'
    this.loadStage(startStage)
  }

  loadStage(index: number): void {
    const stage = STAGES[index]
    if (!stage) {
      this.state = 'victory'
      this.pushEvent({ type: 'stage_clear', stage: this.stageIndex })
      return
    }
    this.loadStageData(stage, index)
  }

  /**
   * Load an arbitrary `StageData` into the World (plan/Automated-Level-Design
   * §3.5 / Phase 0.3). This is the entry point for headless simulation of
   * generated or custom stages — it accepts any `StageData` (not just one from
   * the `STAGES` config array) and performs the exact same setup as
   * `loadStage(index)`: terrain load, entity reset, spawn-queue build, player
   * spawn, and state transition to `'playing'`.
   *
   * The `index` parameter defaults to 0 and is used only for scoring formulas
   * (`killScore` / `stageClearScore` scale with stage index). Generated stages
   * use index 0 so their scoring matches stage 1 — the simulation runner cares
   * about pass/fail, not score magnitude.
   */
  loadStageData(stage: StageData, index = 0): void {
    this.tileMap.loadStage(stage)
    this.tanks = []
    this.allies = []
    this.bullets = []
    this.powerUps = []
    this.explosions = []
    this.popups = []

    this.stageIndex = index
    this.enemiesSpawned = 0
    this.enemiesRemaining = ENEMIES_PER_STAGE
    this.spawnPointIndex = 0
    // Commander floor quota for this stage (plan §5.1 [D9]). Relax 1 /
    // Hard 2 / Chaos 4 / Classic 0. Decremented per Commander roll in
    // Simulation.updateSpawning; never reset per stage beyond this point.
    this.commanderQuotaRemaining = COMMANDER_FLOOR[this.difficultyKey] ?? 0
    // Base (eagle) HP resets to full for the (re)loaded stage. Classic is
    // the authentic one-shot (HP 1); every other difficulty uses BASE_MAX_HP
    // (one fixed pool, hit by raw firepower).
    this.baseMaxHp = this.difficultyKey === 'classic' ? CLASSIC_BASE_MAX_HP : BASE_MAX_HP
    this.baseHp = this.baseMaxHp
    this.freezeTimer = 0
    this.stageClearTimer = 0
    this.gameOverTimer = 0
    this.spawnTimer = 0
    this.pickupWindowTimer = 0
    this.pickupWindowEntered = false
    this.fenceExpireFrame = undefined // fence is stage-scoped; never carries across stages

    // Build spawn queue. The tier roll is intentionally NOT performed here:
    // it happens at spawn time in `Simulation.updateSpawning` so the RNG cost
    // is paid per-spawn (and is skipped entirely on difficulties whose
    // distribution is a single tier, e.g. 100%-none classic) instead of
    // consuming 20 RNG calls up front and shifting the whole downstream stream
    // (DECISIONS.md).
    this.spawnQueue = []
    const enemies = stage.enemies
    const r = this.rules
    for (let i = 0; i < ENEMIES_PER_STAGE; i++) {
      const kind = enemies[i % enemies.length]
      // The bonus carrier flag is DATA, not a hardcoded cadence (MANIFEST §2.4).
      //  - classic (`fixed`): carriers are the stage's power-up enemies from
      //    `fixedDropKillIndices` (1-based spawn index) — the red-box enemies
      //    that drop when destroyed (faithful 1985 FC).
      //  - modern: every `bonusEnemyEveryNSpawns`-th spawned enemy is a carrier
      //    (the old `i % 4 === 3` cadence, now config-driven).
      const isCarrier =
        r.dropSchedule === 'fixed'
          ? r.fixedDropKillIndices.includes(i + 1)
          : r.bonusEnemyEveryNSpawns > 0 && (i + 1) % r.bonusEnemyEveryNSpawns === 0
      this.spawnQueue.push({
        kind,
        bonus: isCarrier,
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
    // Preview shows an intact base behind the menu (no damage overlay).
    this.baseMaxHp = this.difficultyKey === 'classic' ? CLASSIC_BASE_MAX_HP : BASE_MAX_HP
    this.baseHp = this.baseMaxHp
    this.player = null
    this.tanks = []
    this.allies = []
    this.bullets = []
    this.powerUps = []
    this.explosions = []
    this.popups = []
    this.pendingDrops = []
    this.events = []
    this.stageIndex = index
    this.fenceExpireFrame = undefined
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
    const stats = profileToStats(
      profile,
      kind,
      kind === 'player' ? this.playerLevel : 0,
      this.rules,
    )
    // Enemy combat stats (including HP/armor) are fixed per archetype and never
    // scaled by difficulty — difficulty only changes the tier distribution that
    // enemies are rolled from (plan/AI-Tier-System-Revision.md §5). Scaling
    // enemy HP here would "enhance enemy power", which is explicitly forbidden.
    const hp = stats.maxHp

    // Functional star ladder: the player's `fastBullet` perk (classic) is a
    // multiplier on the base bullet speed. Apply it at spawn so a stage-
    // persistent star level is correct, not just on star pickup (Simulation).
    let bulletSpeed = stats.bulletSpeed
    if (
      kind === 'player' &&
      this.rules.starModel === 'functional' &&
      hasStarPerk(this.rules, this.playerLevel, 'fastBullet')
    ) {
      bulletSpeed *= this.rules.fastBulletMult
    }

    // Enemy brains are initialized here (on the World — no hidden state).
    // The Tactical Intelligence Framework reads/writes these fields every tick.
    // `level` is a PLACEHOLDER ('rookie'); the real tier is rolled at spawn
    // time in `Simulation.updateSpawning` (plan §5) which overwrites
    // `aiState.level` / `isCommander` there. `spawnSeq` is stamped from
    // the World's monotonic counter so command authority is derivable.
    let aiState: AIState | undefined
    if (kind !== 'player') {
      const base = this.tileMap.getBasePos()
      const placeholder = INTELLIGENCE_LEVELS['rookie']
      aiState = {
        level: 'rookie',
        isCommander: false,
        spawnSeq: this.spawnSeqCounter++,
        thinkTimer: 200 + this.rng.next() * 600,
        fireTimer: 400 + this.rng.next() * 600,
        currentDir: dir,
        tacticalGoal: 'advance' as GoalType,
        targetX: base ? base.x + CELL : x + TANK / 2,
        targetY: base ? base.y + CELL : y + TANK / 2,
        strategicTimer: STRATEGIC_INTERVAL_MS * (0.8 + this.rng.next() * 0.4),
        strategicGoal: 'attackBase' as GoalType,
        reactionTimer: placeholder.reactionTime,
        dodgeLock: 0,
        vertOnlyTicks: 0,
        commanderTimer: COMMANDER_INTERVAL_MS,
        directive: 'none',
        directiveAge: 1e9,
        directiveSeq: 0,
        directiveCompliant: false,
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
      speed: stats.speed * (this.rules.speedJitter ? rollSpeedJitter(this.rng) : 1),
      hp,
      maxHp: hp,
      bulletPower: stats.bulletPower,
      damage: stats.damage,
      bulletSpeed,
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
      allegiance: kind === 'player' ? 'player' : 'enemy',
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
    this.compact(this.allies, (t) => t.alive)
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
    // Reuse buffer — avoids creating a new array every call (called ~10×/tick).
    // Order: player, then allied guards, then enemy tanks.
    const buf = this._allTanksBuf
    let i = 0
    if (this.player) buf[i++] = this.player
    const allies = this.allies
    for (let a = 0; a < allies.length; a++) buf[i++] = allies[a]
    const tanks = this.tanks
    for (let t = 0; t < tanks.length; t++) buf[i++] = tanks[t]
    buf.length = i
    return buf
  }

  get enemyCount(): number {
    let count = 0
    const tanks = this.tanks
    for (let i = 0; i < tanks.length; i++) {
      // Accompanying "balance" enemies (isExtra) are outside the per-stage
      // 20-enemy cap (DECISIONS.md §31 Phase 2).
      if (tanks[i].spawnTimer <= 0 && !tanks[i].isExtra) count++
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
