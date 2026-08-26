import type { TankKind } from '../types'
import { FIELD, TICK_MS } from '../constants'
import { baseBulletSpeedPxPerTick } from './speed'
import { specField } from './tank-spec'

/**
 * Fire-rate standard (user requirement, 2026-07-26).
 *
 * ── Core constraint ────────────────────────────────────────────────────────
 * A *balanced* (basic) enemy, continuously firing straight down from the top
 * of the map, must have AT MOST 3 bullets in flight on the vertical route —
 * i.e. the 3rd bullet is fired exactly when the 1st reaches the bottom.
 *
 * That pins the balanced fire interval to half the bullet's full-field travel
 * time:
 *     travel_ticks = FIELD / bulletSpeed_ppt
 *     travel_ms    = travel_ticks * TICK_MS
 *     balancedInterval = travel_ms / 2          (2 intervals == full travel)
 *
 * With the per-kind bullet speeds in `config/speed.ts`
 * (basic bullet = 2.5 cps × 4 = 10.0 cps = 160/60 px/tick) this works out to
 * exactly 156 travel ticks / 2600 ms, so the balanced interval is 1300 ms.
 *
 * ── "开火频率" = firing frequency (rate) ────────────────────────────────────
 * A multiplier > 1 means the tank fires MORE often, i.e. its interval is
 * SHORTER:  interval = balancedInterval / multiplier.
 * The multipliers are taken directly from the user spec:
 *     basic  1.00×  (the reference / 均衡敌人)
 *     fast   1.05×  (快速敌人)
 *     power  1.10×  (强力敌人)
 *     armor  0.90×  (重甲敌人)
 *     player 1.05×  (无星星玩家; scales up per star — see PLAYER_FIRE_… below)
 *
 * ── Per-fire random variation ──────────────────────────────────────────────
 * Every actual shot's NEXT interval is jittered by random(0.95, 1.05):
 *     nextInterval = baseInterval * jitter(id, frame)
 * The jitter is a deterministic hash of (tank id, world frame) — mirroring
 * `bulletSpeedJitter` in `config/speed.ts` — so it is reproducible from World
 * state (snapshot/Replay-safe) and, crucially, never draws from `world.rng`
 * (which would silently perturb the AI's decision stream).
 */

// ============================================================
// Derived balanced baseline
// ============================================================

/** Balanced (basic) enemy bullet speed in px per tick. */
const BALANCED_BULLET_SPEED_PPT = baseBulletSpeedPxPerTick('basic', 0)

/** Ticks for a basic bullet to cross the whole field vertically (exact: 156). */
export const BALANCED_BULLET_TRAVEL_TICKS = FIELD / BALANCED_BULLET_SPEED_PPT

/** Milliseconds for a basic bullet to cross the whole field vertically (exact: 2600). */
export const BALANCED_BULLET_TRAVEL_MS = BALANCED_BULLET_TRAVEL_TICKS * TICK_MS

/**
 * Balanced fire interval (ms). With the 3-bullet constraint this is exactly
 * half the bullet's full-field travel time (exact: 1300).
 */
export const BALANCED_FIRE_INTERVAL_MS = BALANCED_BULLET_TRAVEL_MS / 2

// ============================================================
// Per-kind firing-frequency multiplier (relative to balanced)
// ============================================================

/**
 * > 1 = fires more often (shorter interval). Anchored to the user spec:
 *   basic 1.00×, fast 1.05×, power 1.10×, armor 0.90×, player 1.05×.
 *
 * Derived view of `TANK_SPEC.fireFreqMult` (refactor.trae.md §2.1) — the only
 * authoritative per-kind copy lives in config/tank-spec.ts, so adding a tank
 * needs no hand-edit here.
 */
export const FIRE_FREQUENCY_MULTIPLIER: Record<TankKind, number> = specField('fireFreqMult')

/**
 * Per-star bonus added to the player's firing-frequency multiplier. The spec
 * fixes the NO-STAR player at 1.05×; stars keep the classic "stronger as you
 * rank up" feel by nudging cadence up a touch per star (bullet speed already
 * scales with stars via `config/speed.ts`; this adds a gentle cadence bump so
 * a max-level player out-rates even the power enemy). Granularity matches the
 * spec's multiplier steps (0.05 / 0.10).
 */
export const PLAYER_FIRE_FREQUENCY_PER_STAR = 0.05

// ============================================================
// Per-fire random jitter — random(0.95, 1.05)
// ============================================================

export const FIRE_JITTER_MIN = 0.95
export const FIRE_JITTER_MAX = 1.05

/** Map a uniform u∈[0,1) onto [FIRE_JITTER_MIN, FIRE_JITTER_MAX). Pure. */
export function mapFireJitter(u: number): number {
  const c = u < 0 ? 0 : u > 1 ? 1 : u
  return FIRE_JITTER_MIN + c * (FIRE_JITTER_MAX - FIRE_JITTER_MIN)
}

/**
 * Deterministic per-fire jitter from a hash of (tank fire-count, world frame).
 * The seed is the tank's *fire count* (a per-World counter reset at spawn),
 * NOT the global `genId` — so the jitter is identical across separate runs /
 * snapshots and does not perturb the AI's decision stream (which reads
 * `world.rng`). Same (fireCount, frame) ⇒ same jitter every time; reproducible
 * from World state so it survives snapshots/Replay.
 */
export function fireIntervalJitter(seed: number, frame: number): number {
  let h = (Math.imul(seed, 0x9e3779b1) ^ Math.imul(frame, 0x85ebca77)) >>> 0
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b)
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35)
  h ^= h >>> 16
  return mapFireJitter((h >>> 0) / 4294967296)
}

// ============================================================
// Lookups
// ============================================================

/** Resolve a tank kind's firing-frequency multiplier (player scales with stars). */
export function fireFrequencyMultiplier(kind: TankKind, level = 0): number {
  if (kind === 'player') {
    return FIRE_FREQUENCY_MULTIPLIER.player + level * PLAYER_FIRE_FREQUENCY_PER_STAR
  }
  return FIRE_FREQUENCY_MULTIPLIER[kind]
}

/** Base (no jitter) fire interval in ms for a kind at a star level. */
export function baseFireIntervalMs(kind: TankKind, level = 0): number {
  return BALANCED_FIRE_INTERVAL_MS / fireFrequencyMultiplier(kind, level)
}

/** Actual next fire interval (ms) for a specific shot: base × jitter(fireCount, frame). */
export function nextFireIntervalMs(
  kind: TankKind,
  level: number,
  seed: number,
  frame: number,
): number {
  return baseFireIntervalMs(kind, level) * fireIntervalJitter(seed, frame)
}

/**
 * Idealized bullets-in-flight on the vertical route when a tank fires at its
 * base cadence: floor(travelTicks / intervalTicks) + 1. With the 3-bullet
 * constraint this equals exactly 3 (the design target). Real bullets spawn
 * from the muzzle (slightly below the top edge), so the live count is ≤ 3.
 */
export function idealizedBulletsInFlight(kind: TankKind, level = 0): number {
  const intervalTicks = baseFireIntervalMs(kind, level) / TICK_MS
  return Math.floor(BALANCED_BULLET_TRAVEL_TICKS / intervalTicks) + 1
}
