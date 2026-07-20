import type { Direction } from './constants'

// ============================================================
// Core Types
// ============================================================

export type GameState = 'menu' | 'playing' | 'paused' | 'stageclear' | 'gameover' | 'victory'

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
  /** 13×13 grid, each cell is a 2-char code */
  tiles: string[]
  /** Enemy queue: list of tank kinds */
  enemies: TankKind[]
}

export interface ThemeColors {
  bg: string
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
  // UI
  hudBg: string
  hudText: string
  hudAccent: string
  // Effects
  explosion1: string
  explosion2: string
  explosion3: string
  bullet: string
  powerUp: string
  powerUpGlow: string
  spawn: string
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
