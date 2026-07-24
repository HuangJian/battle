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

export type PowerUpType = 'star' | 'bomb' | 'shield' | 'freeze' | 'tank' | 'helmet'

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
  bulletPower: number
  maxHp: number
  fireCooldown: number
}

export interface Tank extends Entity {
  kind: TankKind
  speed: number
  hp: number
  maxHp: number
  /** Damage dealt per bullet (derived from firepower). */
  bulletPower: number
  /** Bullet travel speed (derived from projectileSpeed). */
  bulletSpeed: number
  fireCooldown: number
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
  isPlayer?: boolean
  // Combat Capability System — every tank owns a profile (immutable config).
  profile: CombatProfile
  // Enemy-specific
  flashTimer?: number // armor tank flashing
  hitCount?: number // number of non-lethal hits taken (drives hit-state overlay)
  aiState?: AIState
  bonus?: boolean // drops a power-up when destroyed
}

/**
 * Intelligence tier names. Every AI tank runs the same decision pipeline;
 * differences are entirely configuration-driven (see `src/ai/config.ts`).
 * New tiers can be added there without touching the engine.
 */
export type IntelligenceLevel = 'rookie' | 'soldier' | 'veteran' | 'commander'

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
  isCommander: boolean

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
}

export interface Bullet extends Entity {
  ownerId: number
  ownerKind: TankKind
  isPlayer: boolean
  speed: number
  power: number // 1 = normal, 2 = destroys steel
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

export interface TankConfig {
  kind: TankKind
  /** Display color (kept for themes/UI that sample it). */
  color: string
  /** Score awarded when this tank is destroyed. */
  score: number
  /** Whether a bonus variant of this tank can drop a power-up. */
  dropsBonus: boolean
}

export interface DifficultyConfig {
  name: string
  enemySpeedMult: number
  enemyFireMult: number
  enemyHpMult: number
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
  gridLineColor: string
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
  keys: KeyBindings
}

export interface KeyBindings {
  up: string
  down: string
  left: string
  right: string
  fire: string
  pause: string
  reset: string
  /** Manual snapshot shortcut (Snapshot Management Framework §3). */
  snapshot: string
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
