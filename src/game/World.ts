import type {
  Tank,
  Bullet,
  PowerUp,
  PowerUpType,
  Mine,
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
import { STAGES, localizedStageName } from '../config/stages'
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
  PLAYER_SPAWN,
  ENEMY_SPAWNS,
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
  player2: Tank | null = null // Lie-Back-Win-Mode: God AI controlled second player
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
   * Total enemies for this stage (plan/God-AI-Curriculum §3 Gap A).
   * Defaults to `ENEMIES_PER_STAGE`; overridden by `StageData.enemyCount`.
   * Snapshotted so RecoverySystem restores the correct count.
   */
  enemiesTotal: number
  /**
   * Pixel-coordinate enemy spawn points for this stage (plan §3.5 影响 1).
   * Cached from `StageData.enemySpawns` at `loadStageData`; defaults to the
   * module-level `ENEMY_SPAWN_POINTS` derived from `ENEMY_SPAWNS`.
   */
  enemySpawnPoints: { x: number; y: number }[]
  /**
   * Player spawn point in sub-block coords for this stage (plan §3.5 影响 1).
   * Cached from `StageData.playerSpawn`; defaults to `PLAYER_SPAWN`.
   */
  playerSpawnPoint: { col: number; row: number }
  /**
   * Round-robin cursor over the enemy spawn points. Lives on the World (not
   * the Simulation) because it affects gameplay — a rewound World must
   * reproduce the exact same spawn positions (AGENTS §2.2, §2.3).
   */
  spawnPointIndex: number

  // Game state
  state: GameState
  score: number
  score2: number // Lie-Back-Win-Mode: God AI score (Q1: separate scoring)
  lives: number
  lives2: number // Lie-Back-Win-Mode: God AI lives
  playerLevel: number
  playerLevel2: number // Lie-Back-Win-Mode: God AI star level
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
  /** Initial RNG seed (Date.now() at construction). Surfaces as the replay
   *  filename seed so browser recordings are reproducible / round-trippable. */
  seed: number

  // Events (consumed by renderer/audio/stats)
  events: GameEvent[]

  /**
   * Spare event buffer for double-buffering. consumeEvents() swaps the active
   * and spare buffers so the per-frame event array is never reallocated.
   */
  private eventsSpare: GameEvent[] = []

  // Reusable buffer for allTanks getter — avoids allocating a new array each call
  private _allTanksBuf: Tank[] = []

  /**
   * (perf §67) Dirty flag for `removeDeadEntities`. Set to true whenever any
   * entity becomes dead (alive=false) or an explosion/popup timer reaches 0.
   * When false at the start of `removeDeadEntities`, the entire 6-array
   * compaction pass is skipped — most ticks have no deaths, so this saves
   * ~30-60 property probes per tick across tanks/allies/bullets/powerUps/
   * explosions/popups. Reset to false at the end of the compaction. Initially
   * true so the first tick after construction/load always processes.
   */
  _needsCleanup = true

  /**
   * (perf §68 Round 9) Dirty flag for `updateMines`. Set to true whenever a
   * mine is added (placeMine) or an armed mine is removed (detonation /
   * compaction). When false, `updateMines` skips the entire mines scan,
   * including the per-mine arm-timer decrement and the per-mine × per-tank
   * AABB collision check. classic mode rarely uses mines, so this saves the
   * per-tick loop overhead on the vast majority of ticks. The flag is
   * intentionally kept TRUE while any mine is arming (armTimer > 0): even
   * though arming doesn't need a collision check yet, the armTimer must keep
   * decrementing, so we keep the loop running. Only when ALL mines have
   * either detonated or been removed (mines.length === 0) does the flag
   * go false. Initially false (no mines at stage start).
   */
  _hasActiveMines = false

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

  // --- Lie-Back-Win-Mode: cooperative mode state ---
  /** Whether cooperative mode is active (God AI as P2). */
  coop: boolean
  /** 督战 (supervise) mode: God AI fights as PLAYER1, no human input at all. */
  spectate: boolean
  /** P2 spawn point in sub-block coords (经典位 col 16, row 24). */
  player2SpawnPoint: { col: number; row: number }

  // --- Super power-up inventory & frenzy state (DECISIONS.md §31) ---
  // Accumulated counts from picking up super power-ups (强力道具).
  guardStock: number // 天降神兵 — Phase 2 summons a base guard
  frenzyStock: number // 狂暴宣泄 — active F6 barrage
  sacrificeStock: number // 同归于尽 — passive AoE on losing a life
  // Active 狂暴宣泄 barrage runtime. Snapshot-safe so a rewind mid-barrage
  // (and the enemy-kill that may trigger it) is faithful.
  // 栅栏道具 (fence): the frame at which the temporary steel ring around the
  // base reverts to brick. undefined = no active fence. Snapshot-safe.
  fenceExpireFrame?: number

  // --- New power-ups (new-powerups-plan.md) ---
  /** EMP timer: when > 0, all enemy tanks are silenced (can move but not fire). */
  empTimer: number
  /** Rewind stock: number of 时光宝盒 items in inventory. */
  rewindStock: number
  /** Signal flag: set by Simulation.activateRewind, consumed by Game.ts to
   *  trigger RecoveryController.beginManualRewind(). Cleared by Game.ts.
   *  Lives on the World (no hidden state outside it — AGENTS §2.2). */
  rewindPending: boolean
  /** Active mines placed by the player. Snapshot-safe. */
  mines: Mine[]

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
    this.enemiesTotal = ENEMIES_PER_STAGE
    this.enemySpawnPoints = ENEMY_SPAWNS.map((s) => ({ x: s.col * CELL, y: s.row * CELL }))
    this.playerSpawnPoint = { ...PLAYER_SPAWN }
    this.spawnPointIndex = 0
    this.state = 'menu'
    this.score = 0
    this.score2 = 0
    this.lives = START_LIVES
    this.lives2 = 0
    this.playerLevel = 0
    this.playerLevel2 = 0
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
    this.seed = Date.now()
    this.rng = new RNG(this.seed)
    this.events = []
    this.frame = 0
    this.bulletSeq = 0
    this.spawnSeqCounter = 0
    this.activeCommanderId = null
    this.commanderQuotaRemaining = 0
    this.directiveSeqCounter = 0
    this.baseHp = 1
    this.baseMaxHp = 1
    this.coop = false
    this.spectate = false
    this.player2SpawnPoint = { col: 16, row: 24 }
    this.recoveryCursor = 0
    this.recoveryCountdown = 0
    this.recoveryFading = false
    // Super power-up inventory & frenzy (DECISIONS.md §31)
    this.guardStock = 0
    this.frenzyStock = 0
    this.sacrificeStock = 0
    this.fenceExpireFrame = undefined
    this.empTimer = 0
    this.rewindStock = 0
    this.rewindPending = false
    this.mines = []
  }

  // ---- Lifecycle ----

  startGame(difficultyKey: string, themeKey: string, startStage = 0): void {
    this.difficultyKey = difficultyKey
    this.rules = RULES[difficultyKey] ?? DEFAULT_RULES
    this.themeKey = themeKey
    this.difficulty = DIFFICULTIES[difficultyKey] ?? DIFFICULTIES['classic']
    this.theme = THEMES[themeKey] ?? THEMES[DEFAULT_THEME]
    this.score = 0
    this.score2 = 0
    this.lives = this.difficulty.startLives
    this.lives2 = 0
    this.playerLevel = this.difficulty.playerStartLevel
    this.playerLevel2 = 0
    this.killCount = 0
    this.playTimeMs = 0
    this.coop = false
    this.spectate = false
    // Fresh run: clear any deferred drops left over from a previous game
    // (e.g. a buffered drop from the final stage of a won run).
    this.pendingDrops = []
    // Fresh run: reset super power-up inventory & frenzy state (§31).
    this.guardStock = 0
    this.frenzyStock = 0
    this.sacrificeStock = 0
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
    // Gap A (plan/God-AI-Curriculum §3): respect per-stage enemy count.
    this.enemiesTotal = stage.enemyCount ?? ENEMIES_PER_STAGE
    this.enemiesRemaining = this.enemiesTotal
    // 影响 1 (plan §3.5): respect per-stage spawn-point overrides.
    this.enemySpawnPoints =
      stage.enemySpawns && stage.enemySpawns.length > 0
        ? stage.enemySpawns.map((s) => ({ x: s.col * CELL, y: s.row * CELL }))
        : ENEMY_SPAWNS.map((s) => ({ x: s.col * CELL, y: s.row * CELL }))
    this.playerSpawnPoint = stage.playerSpawn ?? PLAYER_SPAWN
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
    const total = this.enemiesTotal
    for (let i = 0; i < total; i++) {
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
    // Lie-Back-Win-Mode §3.8: recompute P2 spawn point from new stage's
    // playerSpawn and respawn player2 if coop is active.
    if (this.coop) {
      const p1Col = this.playerSpawnPoint.col
      this.player2SpawnPoint = { col: 24 - p1Col, row: 24 }
      this.spawnPlayer2()
    }
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
    this.player2 = null
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
    // 影响 1 (plan §3.5): respect per-stage player spawn override.
    const col = this.playerSpawnPoint.col
    const row = this.playerSpawnPoint.row
    this.player = this.createTank('player', col * CELL, row * CELL, 'up')
    this.player.level = this.playerLevel
    this.player.shieldTimer = 3000
    this.player.isPlayer = true
  }

  /**
   * Spawn player2 (God AI tank) at the P2 spawn point.
   * Lie-Back-Win-Mode §3.8: symmetric to spawnPlayer.
   */
  spawnPlayer2(): void {
    const col = this.player2SpawnPoint.col
    const row = this.player2SpawnPoint.row
    this.player2 = this.createTank('player', col * CELL, row * CELL, 'up')
    this.player2.level = this.playerLevel2
    this.player2.shieldTimer = 3000
    this.player2.isPlayer = true
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
      // §86c: Initialize turn cooldown tracking. prevMoveDir = dir so the
      // first frame doesn't register as a turn. lastTurnMs = -9999 so
      // the first real turn is always allowed.
      prevMoveDir: dir,
      lastTurnMs: -9999,
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
    // (perf §67) Fast path: skip the 6-array compaction entirely on ticks
    // where no entity died. The _needsCleanup flag is set by Simulation
    // whenever an entity becomes dead (alive=false) or an explosion/popup
    // timer crosses 0. Most ticks have zero deaths, so this saves ~30-60
    // property probes per tick. Behavior is byte-identical: dead entities
    // are still removed in the same tick they died (the flag is set BEFORE
    // this call, which happens at the end of updatePlaying).
    if (!this._needsCleanup) return
    this._needsCleanup = false

    // In-place compaction (swap-and-pop) — avoids creating new arrays every
    // tick. Inlined per-array (perf): the generic `compact<T>(arr, predicate)`
    // version paid a per-element callback-call overhead V8 could not inline
    // (6 arrays × varying element types ⇒ polymorphic call site). Inlining the
    // property check directly lets V8 keep it monomorphic and branch-free.
    // Behavior is identical: keep elements matching the predicate, preserve
    // relative order, truncate the tail.
    let w: number
    const tanks = this.tanks
    w = 0
    for (let r = 0; r < tanks.length; r++) {
      if (tanks[r].alive) tanks[w++] = tanks[r]
    }
    tanks.length = w

    const allies = this.allies
    w = 0
    for (let r = 0; r < allies.length; r++) {
      if (allies[r].alive) allies[w++] = allies[r]
    }
    allies.length = w

    const bullets = this.bullets
    w = 0
    for (let r = 0; r < bullets.length; r++) {
      if (bullets[r].alive) bullets[w++] = bullets[r]
    }
    bullets.length = w

    const powerUps = this.powerUps
    w = 0
    for (let r = 0; r < powerUps.length; r++) {
      if (powerUps[r].alive) powerUps[w++] = powerUps[r]
    }
    powerUps.length = w

    const explosions = this.explosions
    w = 0
    for (let r = 0; r < explosions.length; r++) {
      if (explosions[r].timer > 0) explosions[w++] = explosions[r]
    }
    explosions.length = w

    const popups = this.popups
    w = 0
    for (let r = 0; r < popups.length; r++) {
      if (popups[r].timer > 0) popups[w++] = popups[r]
    }
    popups.length = w
  }

  // ---- Queries ----

  get allTanks(): Tank[] {
    // Reuse buffer — avoids creating a new array every call (called ~10×/tick).
    // Order: player, player2, allied guards, then enemy tanks.
    const buf = this._allTanksBuf
    let i = 0
    if (this.player) buf[i++] = this.player
    if (this.player2) buf[i++] = this.player2
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
    // Localized at read time so the stage-clear / status-line name follows
    // the active language without storing locale on the World.
    return localizedStageName(this.stageIndex)
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
    // Inlined `tileMap.get` + `TileMap.blocksTank` — this is the #2 hot-path
    // function (~11% self-time in classic/stage32 profiling). Caching `grid`
    // to a local + caching the row in the outer loop removes two property
    // accesses and two method calls per cell. Out-of-bounds cells are treated
    // as 'steel' (blocking) to match `TileMap.get`'s bounds behavior; this
    // preserves the original semantics exactly (verified by determinism
    // signature + 644 tests).
    //
    // (perf §64): bounds check hoisted out of the loop — `r` and `c` only
    // increment, so checking `r0 < 0 || r1 >= GRID || c0 < 0 || c1 >= GRID`
    // once up front is equivalent to per-iteration checks but skips the
    // inner bounds branches (4-9 cells × 2 branches per call).
    const grid = this.tileMap.grid
    const c0 = Math.floor(x / CELL)
    const r0 = Math.floor(y / CELL)
    const c1 = Math.floor((x + w - 1) / CELL)
    const r1 = Math.floor((y + h - 1) / CELL)
    if (r0 < 0 || r1 >= GRID || c0 < 0 || c1 >= GRID) return true
    for (let r = r0; r <= r1; r++) {
      const row = grid[r]
      for (let c = c0; c <= c1; c++) {
        const type = row[c]
        if (type === 'water') {
          if (!ignoreWater) return true
          continue
        }
        if (type === 'brick' || type === 'steel' || type === 'base') return true
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
