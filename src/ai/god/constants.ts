import type { TankKind, PowerUpType } from '../../types'
import { BASE_POS, CELL, TANK, BULLET, TICK_MS } from '../../constants'
import { TANK_SPEC, specField } from '../../config/tank-spec'

// ============================================================
// God AI shared constants (moved out of GodAIInput.ts during the
// §0.5 "split the God class" refactor — plan/God-AI-Curriculum).
// These are NOT tunable strategy params (those live in GodAIParams);
// they are game-rule-derived constants used across the sub-modules.
// ============================================================

/** T2a: max distance (in cells) at which to stop-and-aim.
 * At 15 cells (240px), the bullet takes ~120 ticks to arrive. An enemy
 * moving perpendicular at 1px/tick moves 120px = 7.5 cells in that time.
 * This is a balance: too short and the player rarely fires; too long and
 * the player wastes bullets on enemies that dodge. */
export const AIM_RANGE_CELLS = 15

/** T8: how far ahead to project a bullet's trajectory (entire field). */
export const BULLET_TRAJECTORY_MAX_CELLS = 26

/** T3: kind-based threat weights for target selection.
 * Derived view of `TANK_SPEC.threatWeight` (refactor.trae.md §2.1) — the only
 * authoritative per-kind copy lives in config/tank-spec.ts. */
export const KIND_THREAT_WEIGHT: Record<TankKind, number> = specField('threatWeight')

/**
 * (perf) Kind → threat weight as a switch. `KIND_THREAT_WEIGHT[t.kind]` is a
 * string-keyed Record lookup that V8 cannot constant-fold (Record is a mutable
 * exported object); in `findEnemyDirectionImpl` and `selectTargetImpl` it runs
 * once per live enemy per tick. A switch over the 5 literal kinds compiles to
 * a jump table — ~1ns vs ~5-8ns for the dict probe. The literals below are
 * sourced from `TANK_SPEC` (single source of truth) so the only per-kind edit
 * point for threat weight is config/tank-spec.ts.
 */
export function kindThreatWeight(kind: TankKind): number {
  switch (kind) {
    case 'power':
      return TANK_SPEC.power.threatWeight
    case 'armor':
      return TANK_SPEC.armor.threatWeight
    case 'fast':
      return TANK_SPEC.fast.threatWeight
    case 'basic':
      return TANK_SPEC.basic.threatWeight
    default:
      return TANK_SPEC.player.threatWeight // 'player'
  }
}

/** S5a: power-up collection priority (lower = higher priority).
 * New types added by main's powerup work (new-powerups-plan §4 / §4.3):
 *   - normal pool: repair (heal), emp (silence fire), decoy (draw fire), mine (AoE)
 *   - super pool:   rewind (rewind to snapshot)
 * Priorities mirror the existing scale: strong defensive/save effects rank
 * high (low number), situational offensive utility ranks mid. */
export const POWERUP_PRIORITY: Record<PowerUpType, number> = {
  bomb: 0,
  star: 1,
  freeze: 2,
  fence: 3,
  tank: 4,
  shield: 5,
  boat: 6,
  frenzy: 5,
  guard: 3,
  sacrifice: 5,
  // --- new normal power-ups (new-powerups-plan §4) ---
  repair: 3, // 维修 — restore HP to max (defensive utility)
  emp: 2, // 电磁静默 — silence enemy fire (strong crowd-control-like)
  decoy: 4, // 诱饵 — spawn fake player (situational, defensive)
  mine: 4, // 地雷 — place AoE mine (situational, offensive)
  // --- new super power-up (§4.3) ---
  rewind: 1, // 时光宝盒 — rewind to recent snapshot (high-value "save" card)
}

/** Next-cell bullet-lane alignment threshold (§3.11): a bullet within this
 * lateral distance of the next cell's axis counts as "on lane". Was inlined
 * as `CELL * 0.75` across FireControl/ThreatAssessor/perception. */
export const BULLET_ALIGN_NEXT_CELL = CELL * 0.75

/** Actual hitbox overlap half-span (§165): (TANK + BULLET) / 2 = 19px.
 * Replaces the literal 19 and the local windowHalf spellings. */
export const HIT_HALF_SPAN = (TANK + BULLET) / 2

/** Base eagle center pixel (center of the 2×2 base block at BASE_POS). */
export const BASE_CENTER_X_PX = BASE_POS.col * CELL + CELL
export const BASE_CENTER_Y_PX = BASE_POS.row * CELL + CELL

/** Emergency counter-fire range (§M4): within 5 cells (80px) out-dodging is
 * mathematically hopeless — face and cancel instead. Hardcoded by design
 * (the old dodgeCounterFireRangeCells param was removed in §101). */
export const COUNTER_FIRE_RANGE_CELLS = 5

/** Float ms→ticks (no rounding, min 0). Used by ThreatBudget for threat-timing
 *  arithmetic where fractional ticks are intentional. */
export const msToTicksFloat = (ms: number): number => Math.max(0, ms / TICK_MS)

/** Integer ms→ticks (rounded, min 1). Used by CoveragePlanner and
 *  ActionCandidates for ETA models where whole-tick precision suffices. */
export const msToTicksInt = (ms: number): number => Math.max(1, Math.round(ms / TICK_MS))
