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

export interface Tank extends Entity {
  kind: TankKind
  speed: number
  hp: number
  maxHp: number
  fireCooldown: number
  lastFire: number
  moving: boolean
  spawnTimer: number // >0 means still spawning (invulnerable, not active)
  // Player-specific
  level?: number
  shieldTimer?: number
  isPlayer?: boolean
  // Enemy-specific
  flashTimer?: number // armor tank flashing
  hitCount?: number // number of non-lethal hits taken (drives hit-state overlay)
  aiState?: AIState
  bonus?: boolean // drops a power-up when destroyed
}

export interface AIState {
  thinkTimer: number
  currentDir: Direction
  fireTimer: number
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
  hp: number
  speed: number
  bulletSpeed: number
  bulletPower: number
  score: number
  color: string
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
