import type { TankKind, PowerUpType } from '../../types'

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

/** T3: kind-based threat weights for target selection. */
export const KIND_THREAT_WEIGHT: Record<TankKind, number> = {
  power: 4,
  armor: 3,
  fast: 2,
  basic: 1,
  player: 0,
}

/**
 * (perf) Kind → threat weight as a switch. `KIND_THREAT_WEIGHT[t.kind]` is a
 * string-keyed Record lookup that V8 cannot constant-fold (Record is a mutable
 * exported object); in `findEnemyDirectionImpl` and `selectTargetImpl` it runs
 * once per live enemy per tick. A switch over the 5 literal kinds compiles to
 * a jump table — ~1ns vs ~5-8ns for the dict probe. Same return values as the
 * Record for every TankKind. The original `?? 1` defensive fallback at the
 * call sites was a no-op (TankKind is a closed 5-member union, all covered).
 */
export function kindThreatWeight(kind: TankKind): number {
  switch (kind) {
    case 'power':
      return 4
    case 'armor':
      return 3
    case 'fast':
      return 2
    case 'basic':
      return 1
    default:
      return 0 // 'player'
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
