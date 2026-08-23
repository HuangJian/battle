import type { Direction } from './constants'
import type { AIState } from './ai/types'

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
  | 'rewind' // 时光宝盒 — active F7 rewind to recent snapshot (new-powerups-plan §4.3)
  // --- Normal power-ups (new-powerups-plan §4) ---
  | 'repair' // 维修 — restore player HP to max (§4.1)
  | 'emp' // 电磁静默 — silence enemy fire (§4.2)
  | 'decoy' // 诱饵 — spawn a fake player that draws enemy fire (§4.4)
  | 'mine' // 地雷 — place a mine at player position (§4.5)

/**
 * Mine entity — a stationary explosive placed by the player.
 * Lives on World.mines[] and is cloned/restored by WorldSerializer.
 * armTimer > 0 means the mine is still arming (no detonation yet).
 */
export interface Mine {
  id: number
  x: number
  y: number
  w: number
  h: number
  /** Arming delay (ms). Mine does not detonate while armTimer > 0.
   *  Set to MINE_ARM_MS on creation; decremented in updatePlaying. */
  armTimer: number
  alive: boolean
}

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
  /** True for a Decoy tank (诱饵). Decoys move toward enemies but never fire.
   *  Lives on the World via allies[].isDecoy for snapshot safety. */
  isDecoy?: boolean

  // --- Per-tank frenzy state (Lie-Back-Win-Mode Q9: God unaffected by human frenzy) ---
  /** Active 狂暴宣泄 barrage ms remaining (0 = inactive). Only used by human player tanks. */
  frenzyTimer?: number
  /** Shells left to fire this barrage. */
  frenzyShotsLeft?: number
  /** Locked firing direction during the barrage. */
  frenzyDir?: Direction
  /** Ms between frenzy shells (= player fire interval / 5). */
  frenzyInterval?: number
  /** Ms timestamp of the last frenzy shell. */
  frenzyLastFire?: number

  // --- §86c: Turn cooldown (minimum turn period) ---
  /** The direction used for movement in the previous tick. Used by
   *  `updateMovement` to detect direction changes and enforce the minimum
   *  turn period (`turnCooldownMs` in rules). When the input/AI sets
   *  `tank.dir` to a new direction, `updateMovement` checks if enough time
   *  has passed since the last turn. If not, it reverts to `prevMoveDir`. */
  prevMoveDir?: Direction
  /** Absolute time (ms, derived from `world.frame * 1000/60`) of the last
   *  accepted direction change. NOT a frame number — it is wall-clock ms.
   *  Initialized to -9999 so the first turn is always allowed. */
  lastTurnMs?: number
}

/**
 * (§2.6) Enemy-brain types live in `src/ai/types.ts`; re-exported here for
 * compatibility. `AIState` is imported above for `Tank.aiState`.
 */
export type {
  IntelligenceLevel,
  GoalType,
  CommanderDirective,
  AIState,
} from './ai/types'

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
  | {
      type: 'tank_destroyed'
      tank: Tank
      by: 'player' | 'enemy'
      /** The tank id that fired the killing bullet (additive death-attribution
       *  metadata; undefined for non-bullet kills). Read-only observation —
       *  never feeds back into gameplay. Consumed by tools/sim forensics
       *  (killer-kind attribution). */
      byId?: number
    }
  | { type: 'bullet_fired'; bullet: Bullet }
  | { type: 'powerup_collected'; powerUp: PowerUpType; by: 'player' }
  | { type: 'base_destroyed'; by: TankKind }
  | { type: 'stage_clear'; stage: number }
  | { type: 'player_hit' }
  | { type: 'explosion'; x: number; y: number; kind: 'small' | 'big' }

// ============================================================
// Config Types
// ============================================================
//
// (§2.6) The config-layer data contracts moved to `src/config/types.ts`;
// re-exported here for compatibility.

export type {
  DifficultyConfig,
  StageData,
  ThemeColors,
  ThemeDefinition,
} from './config/types'

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
  /** Active super-item: rewind to recent snapshot (时光宝盒). Default F7. */
  rewind: string
  /**
   * Fullscreen toggle shortcut. Bound to a modifier combo ('Alt+KeyF')
   * to avoid browser conflicts (Alt+F opens browser menus in some browsers).
   * See `reset` above for why non-combat shortcuts use modifiers.
   */
  fullscreen: string
}

// ============================================================
// Presentation Layer Types
// ============================================================
//
// (§2.6) The presentation-only types moved to `src/presentation/types.ts`;
// re-exported here for compatibility. The config-layer contracts moved to
// `src/config/types.ts` and are re-exported in the Config Types section above.

export type { VisualComponent, Particle, EmitterConfig, CameraState } from './presentation/types'
