import type { Direction } from './constants'

// ============================================================
// Core Types
// ============================================================

export type GameState =
  | 'menu'
  | 'playing'
  | 'paused'
  | 'stageclear'
  | 'gameover'
  | 'victory'
  | 'recovery'

export type TerrainType = 'empty' | 'brick' | 'steel' | 'water' | 'forest' | 'ice' | 'base'

export type TankKind = 'player' | 'basic' | 'fast' | 'power' | 'armor'

export type PowerUpType =
  | 'star'
  | 'bomb'
  | 'shield'
  | 'freeze'
  | 'tank'
  | 'fence'
  | 'boat'
  // --- Super power-ups (强力道具, DECISIONS.md §31) ---
  // Picked up into an inventory (accumulated), not applied instantly.
  | 'guard' // 天降神兵 — summon a base guard (Phase 2: spawn + ally AI + faction)
  | 'frenzy' // 狂暴宣泄 — active F6 barrage (Phase 1)
  | 'sacrifice' // 同归于尽 — passive AoE on losing a life (Phase 1)

export interface Vec2 {
  x: number
  y: number
}

// ============================================================
// Entities
// ============================================================

export interface Entity {
  id: number
  x: number
  y: number
  w: number
  h: number
  dir: Direction
  alive: boolean
}

/**
 * Combat Capability System — the six universal dimensions every tank owns.
 *
 * Each value is an abstract 0..100 score (50 = baseline). Concrete gameplay
 * numbers (speed, HP, cooldown, …) are *derived* from these via
 * `profileToStats` in `config/combat.ts` — the engine never hardcodes per-tank
 * stats, so a new tank type is just a profile (data), not a code branch.
 */
export interface CombatProfile {
  /** Projectile destructive power → bullet damage / wall & armor damage. */
  firepower: number
  /** Bullet travel speed → hit probability / reaction difficulty. */
  projectileSpeed: number
  /** Shooting effectiveness → fire cadence & fire-control (ties to AI). */
  fireControl: number
  /** Overall movement capability → speed / maneuverability. */
  mobility: number
  /** Tank durability → maximum HP. */
  armor: number
  /** Reserved extension attribute (shield / regen / stealth / …), v1.0 unused. */
  special: number
}

/** One of the six capability dimensions. */
export type CombatDimension = keyof CombatProfile

/** Concrete gameplay stats derived from a CombatProfile. */
export interface TankStats {
  speed: number
  bulletSpeed: number
  /** 1 = normal bullet, 2 = can destroy steel (player-only, level-gated). */
  bulletPower: number
  /** Maximum HP ("HP 值"). Derived from `armor` via HP_SCALE. */
  maxHp: number
  /** Per-shot damage ("火力强度值"). Derived from `firepower` via DAMAGE_SCALE. */
  damage: number
  /** True only for the player at/above STEEL_PIERCE_PLAYER_LEVEL. */
  canPierceSteel: boolean
  fireCooldown: number
}

export interface Tank extends Entity {
  kind: TankKind
  speed: number
  hp: number
  maxHp: number
  /** Damage dealt per bullet (derived from firepower, kept for terrain/legacy refs). */
  bulletPower: number
  /** Per-shot damage dealt to tanks ("火力强度值"). */
  damage: number
  /** Bullet travel speed (derived from projectileSpeed). */
  bulletSpeed: number
  /** Base (no-jitter) fire interval in ms, derived from the fire-rate standard. */
  fireCooldown: number
  /**
   * The *actual* cooldown (ms) the tank must wait before its NEXT shot. It is
   * the base interval (`fireCooldown`) multiplied by a per-fire jitter in
   * random(0.95, 1.05), frozen at fire time (see `nextFireIntervalMs` in
   * config/fire-rate.ts). Stored on the tank — not recomputed per tick — so the
   * jitter stays deterministic/snapshot-safe and the gate is stable.
   */
  nextFireInterval: number
  /**
   * Monotonic count of shots this tank has fired (per-World, reset at spawn).
   * Used as the deterministic seed for the per-fire jitter so the jitter does
   * NOT depend on the global `genId` counter (which is NOT reset between
   * Worlds) — keeping firing timing reproducible across runs / snapshots.
   */
  fireCount: number
  lastFire: number
  moving: boolean
  /**
   * Per-tick velocity (px/tick) for the ice momentum / slide model.
   * Movement stays strictly axis-locked (only one of vx/vy is ever non-zero
   * at a time — see Simulation.updateMovement), so the off-axis coordinate
   * always stays grid-aligned and the whole collision system is unaffected.
   * On normal ground velocity snaps instantly to the desired value; on ice it
   * eases toward it (acceleration) and keeps gliding after input is released
   * (low deceleration) — that glide is what makes ice feel slippery.
   */
  vx: number
  vy: number
  spawnTimer: number // >0 means still spawning (invulnerable, not active)
  // Player-specific
  level?: number
  shieldTimer?: number
  boatTimer?: number // amphibious boat power-up timer
  isPlayer?: boolean
  // Combat Capability System — every tank owns a profile (immutable config).
  profile: CombatProfile
  // Enemy-specific
  flashTimer?: number // armor tank flashing
  hitCount?: number // number of non-lethal hits taken (drives hit-state overlay)
  aiState?: AIState
  bonus?: boolean // drops a power-up when destroyed
  // --- Faction (third-faction ally, DECISIONS.md §31 Phase 2) ---
  /** Combat allegiance. Drives bullet friendly-fire rules and AI targeting.
   *  `player` + `ally` are on the same team (no friendly fire between them);
   *  `enemy` is hostile to both. `isPlayer` is derived (= allegiance==='player'). */
  allegiance: 'player' | 'enemy' | 'ally'
  /** True for the balance "accompanying enemy" spawned by 天降神兵. Excluded
   *  from the per-stage 20-enemy cap (MAX_ENEMIES_ALIVE) and from
   *  `enemiesRemaining` (stage-clear count); still counts killCount/score. */
  isExtra?: boolean
  /** Absolute world.frame at which an allied guard auto-expires (2-min
   *  lifespan). Undefined for non-guard tanks. */
  guardExpireFrame?: number
}

/**
 * Intelligence tier names. Every AI tank above 'none' runs the same decision
 * pipeline; differences are entirely configuration-driven (see
 * `src/ai/config.ts`). 'none' is a separate minimal classic-behavior branch
 * (random wander + base bias + random fire — AI-Tier-System-Revision §3).
 * The tier is ROLLED AT SPAWN TIME from the difficulty's distribution table;
 * tank kind no longer implies a tier.
 */
export type IntelligenceLevel = 'none' | 'rookie' | 'soldier' | 'veteran' | 'commander'

/**
 * Candidate tactical/strategic goals. Goals compete through dynamic scores
 * (see `src/ai/TacticalIntelligence.ts`) rather than a fixed priority list.
 */
export type GoalType =
  | 'attackBase'
  | 'attackPlayer'
  | 'destroyWall'
  | 'retreat'
  | 'regroup'
  | 'advance'
  | 'defendBase' // 天降神兵 allied guard posture (§31 Phase 2)

/**
 * Lightweight cooperation directives broadcast by the (elected) commander.
 * Tanks remain autonomous — they may follow or ignore a directive according
 * to their own intelligence (teamwork flag).
 */
export type CommanderDirective =
  | 'none'
  | 'pushLeft'
  | 'pushRight'
  | 'defendBase'
  | 'attackTogether'
  | 'spreadOut'

/**
 * AIBrain — the complete, serializable decision state for one enemy tank.
 *
 * This is the Tactical Intelligence Framework's per-tank memory and lives on
 * the World (no hidden state outside it — AGENTS.md §2.2). It is a flat
 * structure of primitives only, so the snapshot `WorldSerializer` can
 * shallow-clone it safely when snapshotting the World.
 *
 * The fields `thinkTimer` / `fireTimer` / `currentDir` are kept from the
 * previous AI for backwards compatibility with the determinism tests.
 */
export interface AIState {
  // ---- Identity / intelligence ----
  level: IntelligenceLevel
  /** Born at Commander tier (render flag for crown/aura; NOT command authority —
   *  the active commander is `world.activeCommanderId`). */
  isCommander: boolean
  /** Monotonic per-World birth order (from `world.spawnSeqCounter`). The alive
   *  Commander with the highest spawnSeq holds command authority. */
  spawnSeq: number

  // ---- Tactical layer (reactive + short horizon) ----
  thinkTimer: number // ms until the next tactical re-evaluation
  fireTimer: number // ms until the next fire attempt
  currentDir: Direction // direction the tank intends to move this tick
  tacticalGoal: GoalType // current short-term objective
  targetX: number // route target (px, tank-center aligned)
  targetY: number

  // ---- Strategic layer (long horizon) ----
  strategicTimer: number // ms until the next strategic re-evaluation
  strategicGoal: GoalType // stable long-term objective

  // ---- Reaction / imperfection ----
  reactionTimer: number // ms of remaining "delayed reaction" before dodging
  dodgeLock: number // ms the current dodge direction is committed

  // ---- Commander ----
  commanderTimer: number // ms until this commander's next broadcast
  directive: CommanderDirective // last directive received (or 'none')
  directiveAge: number // ms since the directive was received
  /** Seq id (world.directiveSeqCounter) of the directive last rolled for. */
  directiveSeq: number
  /** Cached compliance roll for that directive (rolled once, on arrival). */
  directiveCompliant: boolean
}

export interface Bullet extends Entity {
  ownerId: number
  ownerKind: TankKind
  isPlayer: boolean
  /** Combat allegiance of the firing tank (mirrors Tank.allegiance). Drives
   *  the 3-way friendly-fire rule in Simulation.bulletHitsTank. */
  allegiance: 'player' | 'enemy' | 'ally'
  speed: number
  power: number // 1 = normal, 2 = destroys steel
  /** Per-shot damage dealt to tanks. */
  damage: number
}

export interface PowerUp {
  id: number
  type: PowerUpType
  x: number
  y: number
  w: number
  h: number
  alive: boolean
  blinkTimer: number
  /** Time since spawn (ms). Used for despawn timeout and countdown display. */
  lifeTimer: number
}

export interface Explosion {
  id: number
  x: number
  y: number
  size: number // radius
  timer: number
  maxTimer: number
  kind: 'small' | 'big'
}

export interface ScorePopup {
  id: number
  x: number
  y: number
  text: string
  timer: number
}

// ============================================================
// Tile Map
// ============================================================

export interface TileData {
  type: TerrainType
  /** For brick/steel: which sub-blocks are intact (4 quadrants) */
  quadrants: boolean[] // [TL, TR, BL, BR] — true = intact
}

// ============================================================
// Game Events
// ============================================================

export type GameEvent =
  | { type: 'tank_destroyed'; tank: Tank; by: 'player' | 'enemy' | 'self' }
  | { type: 'bullet_fired'; bullet: Bullet }
  | { type: 'powerup_collected'; powerUp: PowerUpType; by: 'player' }
  | { type: 'base_destroyed' }
  | { type: 'stage_clear'; stage: number }
  | { type: 'player_hit' }
  | { type: 'explosion'; x: number; y: number; kind: 'small' | 'big' }

// ============================================================
// Config Types
// ============================================================

export interface DifficultyConfig {
  name: string
  /**
   * Difficulty affects enemy AI ONLY through the spawn-time tier distribution
   * (`DIFFICULTY_TIER_DISTRIBUTION` in src/ai/config.ts). Tier capability
   * numbers are FIXED — difficulty never scales them, and it must NEVER scale
   * enemy combat stats (armor / speed / bullet speed / HP), which is explicitly
   * forbidden by DECISIONS.md. Lives and the player's starting star level are
   * player-side resources, not enemy combat power.
   *
   * Scoped carve-out [D10]: a Commander-tier spawn receives the +15% elite
   * combat boost (`applyEliteModifier`), and the Commander probability is
   * difficulty-driven. Provisional — see plan/AI-Tier-System-Revision.md §5.3.
   */
  startLives: number
  playerStartLevel: number
}

export interface StageData {
  id: number
  name: string
  /** 26×26 grid (one char per 16px sub-block): '.', 'b', 's', 'w', 'f', 'i', 'E' */
  tiles: string[]
  /** Enemy queue: list of tank kinds */
  enemies: TankKind[]
  /**
   * Optional override for the total enemy count this stage (plan/God-AI-Curriculum
   * §3 Gap A). When omitted, falls back to `ENEMIES_PER_STAGE` (20). The enemy
   * `enemies` array still determines the *kind* queue (cycled if shorter); this
   * field controls how many enemies spawn in total. Data-over-code (AGENTS §2.4) —
   * existing stages are unaffected (they don't set this field).
   */
  enemyCount?: number
  /**
   * Optional override for the player spawn position in sub-block coords (plan §3.5
   * 影响 1). When omitted, falls back to `PLAYER_SPAWN`. Curriculum arenas use this
   * to place the player inside the open area instead of the default bottom-left.
   */
  playerSpawn?: { col: number; row: number }
  /**
   * Optional override for enemy spawn positions in sub-block coords (plan §3.5
   * 影响 1). When omitted, falls back to `ENEMY_SPAWNS`. Curriculum arenas use this
   * to place enemies inside the open area.
   */
  enemySpawns?: { col: number; row: number }[]
}

export interface ThemeColors {
  bg: string
  /** Optional vertical gradient [top, bottom] used as the play-field background. */
  bgGradient?: [string, string]
  brick: string
  brickDark: string
  steel: string
  steelDark: string
  water: string
  waterDark: string
  forest: string
  forestDark: string
  ice: string
  base: string
  baseDark: string
  // Tank colors
  playerBody: string
  playerTurret: string
  playerBody2: string // level 2+
  playerBody3: string // level 3
  enemyBasic: string
  enemyFast: string
  enemyPower: string
  enemyArmor: string
  enemyArmorFlash: string
  // UI — canvas
  hudBg: string
  hudText: string
  hudAccent: string
  // Effects
  explosion1: string
  explosion2: string
  explosion3: string
  bullet: string
  bulletGlow: string
  powerUp: string
  powerUpGlow: string
  powerUpFence: string
  powerUpFenceGlow: string
  powerUpBoat: string
  powerUpBoatGlow: string
  spawn: string
  // UI — HTML overlay
  panelBg: string
  panelBorder: string
  panelShadow: string
  textPrimary: string
  textSecondary: string
  textMuted: string
  accentPrimary: string
  accentSecondary: string
  buttonBg: string
  buttonHover: string
  buttonActive: string
  overlayBg: string
  danger: string
  success: string
  // Ambient
  vignetteColor: string
}

// ============================================================
// Settings
// ============================================================

export interface GameSettings {
  volume: number
  difficulty: string
  theme: string
  screenScale: number
  /** Performance Mode: cap render DPR at 1 (pixelated upscale) + cap render FPS.
   *  Drastically cuts GPU fill-rate for weak/integrated GPUs (e.g. Intel Iris
   *  Pro) so the fan stays off. Default ON — toggle in the start menu. */
  performanceMode: boolean
  keys: KeyBindings
}

export interface KeyBindings {
  up: string
  down: string
  left: string
  right: string
  fire: string
  pause: string
  /**
   * Reset-to-menu shortcut. Bound to a *modifier* combo (e.g. 'Alt+KeyR')
   * rather than a bare key so it can't be hit by accident mid-play. The Input
   * layer matches the full modifier+code spec, so a plain 'R' no longer fires.
   */
  reset: string
  /**
   * Manual-save (snapshot) shortcut. Bound to a *modifier* combo
   * ('Alt+KeyS' by default) so it can't be hit by accident and stays clear
   * of browser-reserved combos. It is also distinct from the bare 'KeyS' used
   * for menu navigation (the Input layer matches the full modifier+code spec).
   * See `reset` above for the rationale. (Snapshot Management Framework §3.)
   */
  snapshot: string
  /**
   * Theme-cycle shortcut. Also a modifier combo ('Alt+KeyT') — see `reset`
   * above for why non-combat shortcuts use modifiers. Free of browser-reserved
   * combos (unlike Ctrl+R / Ctrl+T, which collide with reload / new-tab).
   */
  theme: string
  /** Active super-item: summon base guard (天降神兵). Default F5. */
  guard: string
  /** Active super-item: frenzy barrage (狂暴宣泄). Default F6. */
  frenzy: string
}

// ============================================================
// Presentation Layer Types
// ============================================================

/** Visual component — tracks the visual state of a simulation entity */
export interface VisualComponent {
  entityId: number
  sprite: string // e.g. "tank.player", "bullet", "explosion.big"
  animation: string // e.g. "idle", "move", "spawn", "destroy"
  direction: Direction
  elapsed: number // ms since animation started
  alpha: number
  scale: number
  flash: boolean
  level: number
  /**
   * Frame stamp used for mark-and-sweep cleanup of stale visual components
   * (avoids allocating a Set every render frame). Set to world.frame each
   * time the component is seen as alive; components whose stamp is stale are
   * removed by AnimationSystem.cleanup().
   */
  lastSeenFrame?: number
}

/** Particle — a single visual particle */
export interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  life: number
  maxLife: number
  size: number
  color: string
  type: 'spark' | 'debris' | 'smoke' | 'ring' | 'flash'
  gravity: number
  drag: number
  rotation: number
  rotSpeed: number
  active: boolean
}

/** Particle emitter configuration */
export interface EmitterConfig {
  x: number
  y: number
  count: number
  speedMin: number
  speedMax: number
  lifeMin: number
  lifeMax: number
  sizeMin: number
  sizeMax: number
  colors: string[]
  type: Particle['type']
  gravity: number
  drag: number
  angleMin: number // radians
  angleMax: number
  spread: number // positional spread radius
}

/** Camera state */
export interface CameraState {
  x: number
  y: number
  shake: number
  shakeDecay: number
  offsetX: number
  offsetY: number
  scale: number
}

/** Theme definition with metadata */
export interface ThemeDefinition {
  key: string
  name: string
  description: string
  colors: ThemeColors
}
