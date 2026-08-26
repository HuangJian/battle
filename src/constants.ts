// ============================================================
// Game Constants
// ============================================================
//
// ── Naming convention for time-bearing constants (plan/refactor.agy.md §2.9)
// ─────────────────────────────────────────────────────────────
//   *_MS     milliseconds (wall-clock or sim-clock durations/timestamps)
//   *_FRAMES simulation frames @60Hz (e.g. DECOY_LIFESPAN_FRAMES = 30 * 60)
//   *_TICKS  simulation ticks (1 tick = TICK_MS; prefer *_MS at API borders)
// The suffix is part of the contract: a value's unit must be obvious at the
// call site without reading its definition.

/** Size of one sub-block in pixels (smallest terrain unit) */
export const CELL = 16

/** Number of sub-blocks per map side (26×26 grid) */
export const GRID = 26

/** Playfield pixel size = GRID × CELL */
export const FIELD = GRID * CELL // 416

/** Tank pixel size (2×2 cells) */
export const TANK = CELL * 2 // 32

/** Bullet pixel size */
export const BULLET = 6

/** Fixed timestep for simulation (ms) */
export const TICK_MS = 1000 / 60

/**
 * Golden-ratio hash used to derive per-purpose RNG seed streams from
 * `world.seed` (replay RNG, AoE rolls, P2 streams): `(seed ^ SEED_HASH) >>> 0`.
 * Changing this value invalidates every recorded replay/snapshot.
 */
export const SEED_HASH = 0x9e3779b9

/** XOR offset that keeps Player-2 (God AI) RNG streams independent of P1's. */
export const P2_SEED_OFFSET = 0xdeadbeef

/**
 * Sentinel timestamp for `lastTurnMs` / `lastFire`: "never happened". Being far
 * in the past guarantees the first turn / first shot is always allowed.
 */
export const TURN_SENTINEL_MS = -9999

/**
 * Render-rate cap (frames/sec). Decoupled from the simulation: the sim always
 * ticks at 60Hz for responsiveness/determinism, but the *canvas repaint* can be
 * throttled to cut GPU load and power draw.
 *   0 = uncapped (paint every animation frame — default, preserves 60fps feel)
 *   e.g. 30 = halve GPU work during action (battery / low-power mode)
 * On-demand skip (PresentationLayer.shouldRender) already eliminates repaints
 * during idle/menu/pause regardless of this value.
 */
/** Render FPS cap applied while Performance Mode is ON (cuts GPU load ~half).
 *  0 would mean uncapped; the game uses this value only in perf mode. */
export const PERF_MODE_RENDER_FPS = 30

/** Max enemies alive at once (single-player / non-coop). */
export const MAX_ENEMIES_ALIVE = 4

/**
 * Co-op (躺赢 / 督战x2) concurrent-enemy floor. The spawner tops up to this
 * cap every spawn interval, so a higher co-op cap naturally enforces a
 * "minimum N on field" rule: with this set to 5, co-op always keeps 5 regular
 * enemies alive (until the per-stage queue is exhausted — see plan/tasks.chat.md
 * §26/§27). Balance enemies spawned by 天降神兵 (isExtra) are outside this cap,
 * so a guard summon adds 1–2 on top. Equal to MAX here, but kept as a separate
 * named constant so the floor can be raised independently of the solo cap.
 */
export const COOP_MAX_ENEMIES_ALIVE = 5

/** Total enemies per stage */
export const ENEMIES_PER_STAGE = 20

/** Player lives at start */
export const START_LIVES = 3

/** Player respawn position (tile coords) */
export const PLAYER_SPAWN = { col: 8, row: 24 } // 4×8 grid → center-bottom

/**
 * Default P2 (God AI / 躺赢) spawn point — the classic mirrored position
 * col 16. Mid-game enable paths recompute it from P1's column via
 * `computePlayer2SpawnCol` (utils/helpers); this constant is the fallback
 * when no stage/player data drives the mirror.
 */
export const DEFAULT_P2_SPAWN = { col: 16, row: 24 }

/** Base (eagle) position */
export const BASE_POS = { col: 12, row: 24 }

/** Enemy spawn positions (tile coords, 4×8 grid) */
export const ENEMY_SPAWNS = [
  { col: 0, row: 0 },
  { col: 12, row: 0 },
  { col: 6, row: 0 },
]

/** Power-up duration (ms) — unified to 20s for all timed power-ups */
export const POWERUP_DURATION_MS = 20000

/** Star power-up respawn invulnerability */
export const RESPAWN_SHIELD_MS = 3000

/**
 * Classic FC "star shield" grace: when a 3★ player is hit and spends its top
 * star to survive (dropping to 2★), it gets this brief invulnerability so a
 * coincident bullet in the same volley can't instantly re-kill the now-2★
 * tank. Shorter than the respawn shield — it is a one-time protection, not a
 * respawn.
 */
export const STAR_SHIELD_GRACE_MS = 1500

/** Power-up despawn timeout (ms) — how long a power-up stays on the field before disappearing */
export const POWERUP_TIMEOUT_MS = 20000

/** Fence power-up: number of steel tiles to place around base */
export const FENCE_STEEL_COUNT = 12

/** Boat power-up: amphibious movement duration (ms) */
export const BOAT_DURATION_MS = 20000

/** EMP power-up: enemy silence duration (ms) — enemies can move but not fire */
export const EMP_DURATION_MS = 8000

/** Mine power-up: arming delay (ms) before the mine becomes live */
export const MINE_ARM_MS = 500

/** Mine power-up: explosion radius (cells) */
export const MINE_RADIUS_CELLS = 3

/** Repair power-up: HP restored per pickup = one basic enemy bullet damage
 *  (firepower 50 × DAMAGE_SCALE 2 = 100). §189: single pickup supplements HP
 *  by the 普通敌人一发炮弹伤害值, not a full restore. */
export const REPAIR_HEAL_AMOUNT = 100

/** Decoy power-up: lifespan (frames @ 60fps = 30s) */
export const DECOY_LIFESPAN_FRAMES = 30 * 60

/** Fence power-up: how long the steel ring around the base stays up (frames @ 60fps = 20s).
 *  After it expires the ring reverts to brick walls. */
export const FENCE_DURATION_FRAMES = 20 * 60

/**
 * After the last enemy is destroyed, if power-ups are still on the field the
 * player gets this long (ms) to drive over and collect them before the stage
 * auto-ends. Mirrors the classic "grab the bonus" grace period.
 */
export const POWERUP_PICKUP_WINDOW_MS = 10000

/**
 * Once the last remaining power-up is collected (or the pickup window expires
 * with items still unclaimed), the stage auto-ends after this grace delay (ms).
 */
export const POWERUP_PICKUP_END_DELAY_MS = 1000

/** Stage-clear transition delay (ms) when there is nothing left to collect. */
export const STAGE_CLEAR_DELAY_MS = 3000

/** Score-popup lifetime (ms). */
export const POPUP_DURATION_MS = 1500

/** Game-over screen countdown (ms) before the menu takes over. */
export const GAME_OVER_TIMER_MS = 3000

/** Small (bullet-size) explosion animation lifetime (ms). */
export const SMALL_EXPLOSION_MS = 200

/** Big (tank-size) explosion animation lifetime (ms). */
export const BIG_EXPLOSION_MS = 500

/**
 * Direction data & helpers — canonical definitions live in
 * `utils/direction.ts` (plan/refactor.agy.md §2.8); re-exported here so
 * existing imports keep working. New code should import from
 * `utils/direction` directly.
 */
export { DIR_VECTORS, DIR_DX, DIR_DY, dirIdx, type Direction } from './utils/direction'

// ================================================================
// Ice momentum (slide / glide) model — see Simulation.updateMovement.
//
// Tanks move with a per-tick velocity (vx/vy, px/tick). Each tick the velocity
// eases toward the desired velocity (= DIR_VECTORS[dir] * speed when moving,
// otherwise 0). TRACTION is the fraction of the remaining gap closed per tick:
//   1.0  = instant  → crisp, current control (normal ground, plain ice-free)
//   <1.0 = gradual  → slippery (ice)
// Acceleration (input held / turning onto a new axis) and deceleration (input
// released / still gliding) use SEPARATE coefficients so a tank ramps up
// responsively yet coasts a long way after you let go — the classic ice feel.
// These are pure simulation constants (no RNG), so determinism is preserved.
// ================================================================
/** Fraction of the velocity gap closed per tick while accelerating on ice. */
export const ICE_ACCEL_TRACTION = 0.35
/** Fraction of the velocity gap closed per tick while decelerating on ice. */
export const ICE_DECEL_TRACTION = 0.05

// NOTE: player & bullet speeds are no longer hardcoded here. They are derived
// from each tank's CombatProfile by `profileToStats()` in `config/combat.ts`,
// which keeps bullets strictly faster than tanks (see that file for the
// speed-ratio invariant). These old constants are intentionally removed.

// ============================================================
// Game loop policy (Game / loop driver)
// ============================================================

/**
 * True idle for static screens — event-driven 0-loop.
 *
 * The game must hold 60 FPS while *playing* (the vsync rAF loop). But on the
 * menu / pause / game-over / victory screens nothing animates at 60 FPS, and
 * the on-demand render gate already skips the canvas repaint there, so the
 * GPU is already idle. These static states are therefore fully *event-driven*:
 * a single `keydown` listener processes menu/pause/recovery input the instant
 * a key is pressed — the main thread goes fully to sleep. The moment input
 * changes the state (start → playing, snapshot load → recovery, unpause →
 * playing) the vsync rAF loop is re-armed with zero perceptible delay.
 *
 * Playback is an ACTION state regardless of world.state: a replay can drive
 * the world into 'gameover' (∈ LOW_POWER_STATES), and the rAF loop must keep
 * running so PlaybackController.update() and handlePlaybackInput() stay alive.
 */
export const LOW_POWER_STATES = new Set(['menu', 'paused', 'gameover', 'victory'])

/**
 * Max live sim ticks per render frame. Covers the fastest battle speed (×4 →
 * 4 ticks/frame at 60 FPS) with headroom for frame-rate dips; anything beyond
 * is dropped by the anti-spiral clamp below instead of spiraling. Mirrors
 * PlaybackController.MAX_STEPS_PER_FRAME (DECISIONS #78 pattern).
 */
export const MAX_LIVE_STEPS = 8
