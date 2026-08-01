import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { Tank, Bullet, TankKind } from '../types'
import type { Direction } from '../constants'
import type { Cell } from '../utils/pathfind'
import type { RNG } from '../utils/RNG'
import { BASE_POS, CELL, GRID } from '../constants'
import { ALL_DIRS } from '../utils/helpers'
import {
  findEnemyDirectionImpl,
  findEnemyFacingPlayerImpl,
  scanAheadImpl,
  shouldFireInDirImpl,
  isBaseProtectionBrickImpl,
  shouldFireBreakThroughImpl,
  aimSurvivesTurnImpl,
} from './god/FireControl'
import {
  findMostDangerousBulletImpl,
  findBulletThreatToBaseImpl,
  baseBulletInterceptCellImpl,
  dodgeDirectionImpl,
  isSafeDirImpl,
  hasEnemyBulletInLineImpl,
  findPathThreatImpl,
  findSafeMoveDirImpl,
} from './god/ThreatAssessor'
import {
  findPowerUpTargetImpl,
  calculateRouteDangerImpl,
  getDefaultDefensePositionImpl,
  selectTargetImpl,
} from './god/StrategyPlanner'
import {
  playerCellImpl,
  tankCellImpl,
  navigateTowardsImpl,
  followPathImpl,
  replanImpl,
  directMoveImpl,
  canMoveOrBreakImpl,
  canMoveDirImpl,
  computeThreatCostsImpl,
  trapAvoidanceImpl,
} from './god/Navigator'
import { threatScoreImpl, smartIsBaseUnderThreatImpl } from './god/SmartThreatModel'

/**
 * GodAIInput — a "theoretically optimal player" simulator that implements
 * `InputLike`.
 *
 * Tuning plan: plan/God-AI-Tuning.md
 *
 * Techniques implemented (by ID from the plan):
 *   T2a  — Stop-and-aim: when an enemy is in the same row/col, turn to face
 *          it and fire (instead of firing in the A* movement direction).
 *   T2b  — No fire while moving in wrong direction: fire only when the
 *          movement direction matches the aim direction, or when stopped
 *          with a target in the facing direction.
 *   T1   — Interception geometry: navigate to a point between the enemy and
 *          the base, not to the enemy's current cell (which is a moving target).
 *   T3   — Priority target selection: threat-to-base > power > armor > fast >
 *          basic; bonus enemies get extra weight.
 *   T6   — Don't destroy own base walls: proactive fire checks the ray for
 *          base-protection bricks.
 *   T8   — Base bullet interception: if an enemy bullet's trajectory crosses
 *          the base, move to intercept it (ultimate defense).
 *   T9   — Same-row/col priority sorting: when multiple enemies share a
 *          row/col, fire at the most dangerous (by kind + HP), not nearest.
 *   T11  — Steel wall avoidance: don't fire at steel unless player level ≥ 3.
 *   M3   — Dodge safety: candidate dodge cells checked against other bullets.
 *   M6   — Cooldown-aware firing: don't set _fire=true while on cooldown.
 *   S5a  — Power-up priority: bomb > star > freeze > fence > tank > shield.
 *   S5c  — Bonus enemy priority: tank.bonus === true gets extra target weight.
 *   S7   — Base wall integrity: scan base defense walls; thin walls → urgent
 *          return to defense.
 *   S8   — Freeze window: when freezeTimer > 0, switch to aggressive hunting.
 *   S9   — Shield exploitation: when shieldTimer > 0, skip dodge, go aggro.
 *   S10  — Endgame mode: when ≤ 2 enemies remain, switch from defense to
 *          active hunting to shorten clear time.
 *
 * **Determinism**: all randomness flows through `this.rng` (AGENTS §2.3).
 * When an independent RNG is provided (headless sim recording), `this.rng`
 * is decoupled from `world.rng` so God AI decisions don't consume the
 * world RNG stream — enabling faithful replay playback (DECISIONS #47).
 *
 * **§0.5 split (plan/God-AI-Curriculum)**: the decision logic was extracted
 * into `./god/*` sub-modules (FireControl, ThreatAssessor, StrategyPlanner,
 * Navigator). Each moved method body lives in a `<name>Impl(self, ...)` free
 * function; this class keeps the shared state, `think()` (verbatim), and a
 * thin `public` wrapper per method so call sites and external consumers are
 * unchanged. This is a pure relocation — runtime behavior is identical.
 */

// ============================================================
// Parameters
// ============================================================

/**
 * Configurable parameters (plan/God-AI-Tuning §1).
 *
 * Contains both "imperfection" params (reactionDelay, aimError,
 * suboptimalPathProb) and "strategy/threshold" params (defense positioning,
 * threat ranges, replan intervals, etc.). All tunable values live here per
 * the plan's "implementation discipline" — data over code.
 */
export interface GodAIParams {
  // ---- Imperfection params ----
  /** Ticks of delay before reacting to a new threat. */
  reactionDelay: number
  /** Probability of a fire-control mistake. */
  aimError: number
  /** Probability of taking a suboptimal route step. */
  suboptimalPathProb: number

  // ---- Strategy / threshold params ----
  /** How many rows above the base to position for defense. */
  defenseRowOffset: number
  /** Max columns to shift from base column for defense. */
  defenseColSpread: number
  /** Only consider enemies within this Manhattan distance from base as threats. */
  threatRangeCells: number
  /** Player returns to base if farther than this from the base. */
  maxPlayerDistFromBase: number
  /**
   * P4: race-to-base check range. An enemy within this Manhattan distance
   * of the base triggers emergency defense IF the player would lose the
   * race back (player farther from base than the enemy, plus margin).
   * Catches flanking runners that the static ±3-col threat box misses
   * (S6 Iron Curtain: enemies sweep the bottom edge from the far corner
   * while the player roams the steel maze 20+ cells away).
   */
  baseRaceRangeCells: number
  /**
   * P4: safety margin (cells) for the race check. The player must be at
   * least this many cells CLOSER to the base than the enemy to keep
   * hunting; otherwise it returns to defense.
   */
  baseRaceMarginCells: number
  /**
   * P4.2: outnumbered retreat — if at least this many live enemies are
   * within `outnumberedRadiusCells` of the player, fall back to the
   * defense position instead of pressing the attack. Fixes the S18
   * (Frozen Field) failure family: the AI over-extends into the corridor
   * band below the enemy spawn rows and dies in 3-way crossfire.
   * Set to 5+ to effectively disable (max 4 enemies alive on field).
   */
  outnumberedEnemyCount: number
  /** P4.2: radius (cells) for the outnumbered check. */
  outnumberedRadiusCells: number
  /** T8: max distance (cells) to travel to intercept a base-bound bullet. */
  t8MaxInterceptDistCells: number
  /** S7: cells around the base to scan for wall integrity. */
  baseWallScanRadius: number
  /** Re-plan interval (ticks). */
  replanInterval: number
  /** S5: max distance (cells) to divert for a power-up. */
  powerupMaxDivertDistance: number
  /**
   * S6/S10: enemies remaining in the spawn queue at which the AI switches
   * from defense to aggressive hunting (plan/God-AI-Curriculum §5.3).
   * Previously hardcoded to 3 (too conservative — 0% win rate root cause).
   * Raising it lets the AI start clearing earlier instead of turtling.
   */
  endgameEnemyThreshold: number
  /**
   * S6: max enemies *alive on field* at which the AI may enter hunt mode
   * (plan/God-AI-Curriculum §5.3). Replaces the hardcoded `2` in `canHunt`.
   * Set to 4 (= MAX_ENEMIES_ALIVE) so the binding constraint is
   * `endgameEnemyThreshold` (queue count), not field count.
   */
  huntAllyCount: number

  // ---- P0: Anti-camp / T2a deadlock fix (plan/God-AI-Next-Round) ----
  /**
   * P0.1: max ticks the player may camp (stop-and-aim) at the same cell
   * with no kills before falling through to navigate. Eliminates the T2a
   * deadlock where the player stops and fires at a wall/enemy forever.
   */
  campTimeoutTicks: number
  /**
   * P0.1: ticks to suppress T2a camping after an anti-camp escape. Ensures
   * the player gets enough consecutive navigate ticks to actually move
   * away from the stuck position (otherwise the next tick re-enters T2a
   * and the cycle repeats with no movement).
   */
  antiCampSuppressTicks: number
  /**
   * P0.3: max ticks the player may stay at the same cell in the navigate
   * branch without a kill before forcing a roam to the map center. Breaks
   * pursuit loops where the player chases a faster enemy indefinitely.
   */
  navStuckTicks: number

  // ---- D1: Guard band mode (plan/god-ai-progress Round 4) ----
  /**
   * D1: 0=off, 1=on. When on, the player stays in a horizontal band above
   * the base, engaging enemies that enter the band rather than chasing
   * across the map. Designed for armor-heavy stages with an open bottom
   * (S32 Diamond): armor tanks are slow and must approach the player,
   * giving close-range fire rate (kills 4-HP armor faster); fast tanks
   * are intercepted before reaching the base.
   */
  guardBandMode: number
  /** D1: the guard row (player patrols at this row when idle). */
  guardBandRow: number
  /** D1: half-width of the patrol zone (cols from base col). */
  guardBandHalfWidth: number
  /**
   * D2: score bonus for targeting damaged armor tanks. When > 0, the AI
   * prefers finishing an armor tank that has already taken damage, avoiding
   * the "spread damage across 4 full-HP armor" trap where each gets 1-2
   * hits but none dies. Damage is persistent (HP doesn't regen), so
   * finishing a damaged tank is strictly better than starting a new one.
   */
  damagedArmorBonus: number
  /**
   * Close-combat T2a range: max distance (in cells) at which the player
   * stops to aim-and-fire at an enemy in the same row/col. Default 15
   * (AIM_RANGE_CELLS — unchanged behavior). When set lower (e.g. 5), the
   * player only stops at POINT-BLANK range, maximizing fire rate against
   * high-HP armor tanks (bullet travel time ≈ 0 → kills 4-HP armor in
   * ~0.5s instead of ~4s at long range). Beyond this range, the player
   * keeps moving to close the distance.
   *
   * This applies to 1-HP enemies (basic/fast/power). For multi-HP enemies
   * (armor, maxHp > 1), see `t2aHighHpMaxRange`.
   */
  t2aMaxRange: number
  /**
   * §56: Close-combat T2a range for HIGH-HP enemies (maxHp > 1, i.e. armor).
   * Default 2 — the player only stops to aim at multi-HP enemies when at
   * point-blank range. At longer ranges, the player keeps NAVIGATING to
   * close the distance instead of stopping for a low-DPS long-range duel.
   *
   * Rationale: a 4-HP armor tank at 15 cells takes ~4s to kill (4 bullets
   * × ~1s travel time each). During that time the enemy advances ~12 cells
   * and fires back. At 2 cells, bullet travel time ≈ 0, so 4 shots land in
   * ~0.5s — the enemy barely moves or fires. This generalizes the S32
   * close-combat strategy into a universal mechanism triggered by enemy
   * HP, not by stage name.
   *
   * For 1-HP enemies, `t2aMaxRange` (default 15) still applies — one shot
   * kills regardless of distance, so there's no DPS penalty for long range.
   */
  t2aHighHpMaxRange: number

  // ---- Smart threat model (Phase A, plan/God-AI-Next-Round §3) ----
  /**
   * 0=OFF (default), non-zero=ON. When ON, selectTargetImpl base-threat
   * branch uses type/speed/facing/HP-aware threat scoring to prioritize
   * fast rushers over slow armor. isBaseUnderThreat() and skipT2aForDefense
   * are NOT changed (the old box + race check remains for threat detection).
   * OFF = byte-identical to pre-smart-model behavior.
   */
  smartThreatModel: number
  /**
   * Minimum threat score [0..1] for an enemy to be considered a base
   * threat. Default 0.55 — armor at 3 cells scores ~0.5 (not a threat),
   * fast at 6 cells scores ~0.7 (threat), armor at 1 cell scores ~0.83
   * (threat).
   */
  smartThreatThreshold: number
  /**
   * Weight for the time-to-base feature (distToBase / speedFactor) in
   * threat scoring (default 0.6 — dominant). This combines speed and
   * distance into "how soon can this enemy reach the base?"
   */
  smartThreatSpeedWeight: number
  /** Weight for enemy facing direction in threat scoring (default 0.2). */
  smartThreatFacingWeight: number
  /** Weight for enemy HP in threat scoring (default 0.2). */
  smartThreatHpWeight: number
  /**
   * Time-to-base normalization range (default 12). Enemies whose
   * timeToBase (= distToBase / speedFactor) ≥ this value get timeScore 0.
   * Effectively: a fast tank at 12+ cells or an armor at 4+ cells won't
   * trigger the threat threshold via the time feature alone.
   */
  smartThreatDistRange: number
  /**
   * Phase A: extra race-check range (cells) for fast/power tanks when
   * smartThreatModel is ON. Extends the base race range
   * (baseRaceRangeCells) by this amount for fast/power tanks in the
   * lower half of the map (row ≥ 10). Default 4 → effective range 15
   * for fast tanks vs 11 for armor/basic. Detects fast rushers earlier,
   * giving the player more time to intercept.
   */
  smartRushDetectBonus: number

  // ---- §58: Stage-level adaptive params (Strategy G, replaces override table) ----
  /**
   * §58 / Strategy G: armor ratio in the stage's enemy queue above which the
   * AI switches to close-combat camp/nav timing. 0 = never adapt.
   *
   * Armor-heavy stages (S32 Diamond 40%, S18 Frozen Field 45%, S25 Ice Palace
   * 50%) suffer from T2a deadlocks and pursuit loops: armor is slow and creates
   * traffic jams, so the player gets stuck camping or chasing. Shorter camp/nav
   * timers (the old S32 override values) break these loops faster.
   *
   * Default 0.35 — catches S18/S25/S32/S16/S24/S27/S30/S34 (≥35% armor) without
   * affecting low-armor stages like S6 (0%), S10 (25%), S15 (10%). The threshold
   * was chosen so the adaptation fires on stages where the failure mode is
   * lives_exhausted (stuck chasing armor) rather than base_destroyed.
   */
  armorAdaptRatio: number
  /** §58: camp timeout for armor-heavy stages (default 50 = S32 override value). */
  armorCampTimeoutTicks: number
  /** §58: anti-camp suppress for armor-heavy stages (default 50). */
  armorAntiCampSuppressTicks: number
  /** §58: nav stuck for armor-heavy stages (default 90). */
  armorNavStuckTicks: number
  /**
   * §58: brick density (fraction of solid cells that are brick) above which
   * the AI uses faster replanning + path noise. 0 = never adapt.
   *
   * Brick-dense stages (S26 Brick Maze, S4 Maze) cause deadlock patrol loops
   * where the player and enemies circle each other through narrow corridors.
   * Faster replan (30 vs 50) + small path noise (0.05) break the symmetry.
   * Default 0.45 — catches S26/S4 without affecting open stages.
   */
  brickDenseAdaptRatio: number
  /** §58: replan interval for brick-dense stages (default 30 = S26 override). */
  brickDenseReplanInterval: number
  /** §58: path noise for brick-dense stages (default 0.05). */
  brickDenseSuboptimalPathProb: number

  /**
   * §59 / Strategy C: score bonus in defense-mode target selection for
   * enemies that have a CLEAR shot at the base (aligned + no brick/steel
   * in between). Such enemies can destroy the base with their next bullet,
   * so they must be the highest-priority target. 0 = OFF (byte-identical
   * to pre-§59). Default 500 — large enough to dominate the scoring
   * (urgencyBonus maxes at ~1000 for row 25, proximityBonus is 50, kind
   * weight is 30-120). Decouples the clear-shot bonus from the
   * smartThreatModel gate so it can be A/B-tested independently.
   */
  defenseClearShotBonus: number

  // ---- §60: Open-defense adaptation (baseRaceRangeCells by terrain) ----
  /**
   * §60: brick/(brick+steel) ratio at or above which the stage is eligible
   * for "open defense" (wider baseRaceRangeCells). Stages below this ratio
   * are "steel mazes" — enemies approach through indestructible corridors
   * that bypass the defense position, so early retreat HURTS (the player
   * can't intercept from the defense cell). 0 = never adapt.
   *
   * Default 0.10 — protects S6 Iron Curtain (3.7%) and S32 Diamond (5.3%)
   * while letting all other stages (≥12.5%) use the wider range. Verified
   * via 30-seed probes: baseRaceRangeCells=14 gives +13pp on S12/S26/S33
   * but -16pp on S6 and -33pp on S32.
   */
  openDefenseBrickWallRatio: number
  /** §60: baseRaceRangeCells for open-defense stages (default 14). */
  openDefenseBaseRaceRangeCells: number

  // ---- §61: Terrain-adaptive T2a range for high-HP enemies ----
  /**
   * §61: forest density below which the stage is "open sightline" for armor
   * engagement. On open stages, the player can see and hit armor from range 4
   * (bullets travel through empty space fast). On forest-dense stages, enemies
   * are hidden in trees — engaging from range 4 wastes bullets on invisible
   * targets and the player takes ambush damage. 0 = never adapt.
   *
   * Default 0.15 — catches S26 (7%), S30 (12%), S16/S19/S24/S31 (0-9%) while
   * protecting S14 (31%), S18 (18%), S32 (16%), S34 (17%). Verified via
   * 40-seed probes: t2aHighHpMaxRange=4 gives +12pp on S26, +17pp on S30,
   * but -25pp on S18, -8pp on S34.
   */
  openT2aForestRatio: number
  /**
   * §61: t2aHighHpMaxRange for open-sightline stages (default 4). At range 4,
   * the player engages armor from 4 cells away instead of 2 — bullet travel
   * time is still short (~0.3s for 4 cells) but the player has room to dodge
   * return fire. On forest-dense stages, keep the default 2 (point-blank).
   */
  openT2aHighHpMaxRange: number
  /**
   * §61: water density at or above which the stage is "open sightline"
   * (water creates unobstructed lanes). Overrides the forest check — even
   * with forest, high water means open firing lanes. Default 0.25 — catches
   * S30 Eagle Nest (36% water) which has 12% forest (below threshold anyway)
   * but this also future-proofs for high-water stages.
   */
  openT2aWaterRatio: number
  /**
   * §62: armor ratio at or above which open-T2a is SUPPRESSED (on forest-triggered,
   * non-steel-heavy stages). Stages with significant armor need point-blank
   * engagement (range 2) in open terrain — armor tanks take 4 hits, and at
   * range 4 the player trades kills inefficiently. 1 = never suppress.
   *
   * SUPPRESSION IS BYPASSED (open-T2a fires) when:
   *   - The open-sightline trigger was WATER (waterRatio ≥ openT2aWaterRatio) —
   *     water lanes allow safe long-range engagement (S30: 36% water, 30% armor).
   *   - OR the stage is steel-heavy (steelRatio ≥ openT2aSteelRatio) — steel
   *     corridors force head-on encounters where range 4 gives reaction time
   *     (S26: 26% steel, 40% armor).
   *
   * Default 0.25 — suppresses on S11 (30% armor, 4% steel), S16 (50%, 3%),
   * S19 (40%, 4%) while keeping open-T2a on S26 (40% armor, 26% steel) and
   * S30 (30% armor, 36% water).
   */
  openT2aMaxArmorRatio: number
  /**
   * §62: steel density at or above which armor suppression is BYPASSED
   * (open-T2a fires even with heavy armor). Steel corridors force head-on
   * encounters where the player needs range 4 to react. Default 0.15.
   */
  openT2aSteelRatio: number
  /**
   * §62: forest density at or above which armor-heavy stages use range 3
   * (instead of the default 2) for high-HP T2a. On forest-dense armor stages,
   * the forest absorbs enemy bullets, giving the player room to maneuver at
   * range 3 without taking damage. Range 2 is too close (point-blank hits),
   * range 4 is too far in forest (bullets hit trees). 0 = never adapt.
   * Default 0.25 — catches S14 (31%) while leaving S18 (18%) and S32 (16%)
   * at range 2 (range 3 hurt both in probes).
   */
  armorForestDenseRatio: number
  /** §62: t2aHighHpMaxRange for forest-dense armor-heavy stages (default 3). */
  armorForestDenseRange: number

  /**
   * §63: t2aMaxRange for 1-HP enemies on open-sightline, non-armor-heavy
   * stages. When > 0 AND the stage has open sightline (low forest or high
   * water, same trigger as open-T2a for high-HP) AND armor ratio < 35%,
   * t2aMaxRange is reduced from 15 to this value. On open stages, the
   * player closes in on 1-HP enemies for more reliable kills (less bullet
   * travel time, better positioning for base defense). On brick/forest-dense
   * stages, the default 15 is kept — close combat in corridors is dangerous.
   * 0 = OFF (byte-identical to pre-§63).
   *
   * Default 12 — probes showed S1 +7pp, S7 +13pp, S11 +8pp, S12 neutral,
   * while brick-dense stages (S3/S5/S18) prefer the default 15.
   */
  openT2a1HpMaxRange: number

  /**
   * §64: outnumberedRadiusCells for armor-heavy + high-steel + non-steel-maze
   * stages. On these stages (only S26 Brick Maze: 40% armor, 26% steel,
   * brickWallRatio 0.26), the player gets swarmed in steel corridors and
   * needs to retreat from a wider radius (12 vs default 9) to avoid being
   * pinned. 9 = no change (byte-identical to pre-§64).
   *
   * Gated by: armorHeavy AND steelRatio ≥ openT2aSteelRatio AND !isSteelMaze.
   * Only S26 matches this regime across all 35 stages. Probes: S26 +10pp
   * (75% → 85%, 30 seeds). S32 (steel-maze) is excluded — it needs the
   * opposite (range 12 hurts S32 by -7pp).
   */
  armorSteelOutnumberedRadiusCells: number

  /**
   * §65: suboptimalPathProb for armor-heavy + steel-maze stages. On these
   * stages (only S32 Diamond: 40% armor, brickWallRatio 0.05), the player
   * gets pinned in predictable positions by the steel corridors. Adding
   * path randomness (0.05) breaks the deterministic pinning pattern and
   * improves survival. 0 = no change (byte-identical to pre-§65).
   *
   * Gated by: armorHeavy AND isSteelMaze. Only S32 matches this regime
   * across all 35 stages. Probes: S32 +3pp (77% → 80%, 30 seeds).
   */
  armorMazeSuboptimalPathProb: number

  /**
   * §66: campTimeoutTicks for steel-maze stages with low armor. On these
   * stages (only S6 Iron Curtain: 0% armor, brickWallRatio 0.04), the
   * player gets stuck camping at indestructible steel walls — the default
   * 60-tick camp timeout is far too long, wasting ~1s per deadlock. A
   * shorter timeout (20) breaks the camping deadlock fast, letting the
   * player reposition through alternative corridors.
   *
   * Gated by: isSteelMaze AND !armorHeavy. Only S6 matches this regime
   * across all 35 stages. Probes: S6 +16pp (72% → 88%, 60 seeds).
   */
  steelMazeCampTimeoutTicks: number

  /**
   * §68-v2: Crossfire awareness via time-aware path threat projection.
   *
   * When > 0, the navigation branch checks the player's movement path
   * (4 cells ahead) for bullets that would arrive at any cell before the
   * player clears it. Unlike the old v1 approach (which only checked the
   * next cell with a fixed proximity threshold), this uses actual bullet
   * speed for time-of-arrival estimation, checking ALL enemy bullets from
   * ALL directions.
   *
   * When a threat is detected, the player tries alternative directions
   * (perpendicular first, then backward) or stays put — NOT a perpendicular
   * dodge like v1. This avoids the navigation oscillation that plagued v1-v3.
   *
   * The check runs in the navigation section (T2b) only, after the existing
   * dodge (findMostDangerousBullet) has already handled current-position
   * threats. T8 base interception and T2a close combat are not affected.
   */
  crossfireAwareness: number
  /**
   * §69: Terrain-gated crossfire awareness. When > 0, crossfire awareness is
   * automatically enabled on "open" stages (obstacle density < this ratio AND
   * not a steel maze). On maze stages, diversion from the A* path is too
   * expensive — the cost of taking an alternative route outweighs the bullet
   * risk. 0 = never auto-enable (byte-identical to pre-§69).
   *
   * Obstacle density = (brick + steel + water) / totalCells. Water is included
   * because it creates impassable corridors just like walls.
   *
   * Default 0.40 — gates off S3 (43%), S7 (39%), S9 (42%), S24 (43%), S30 (44%),
   * S34 (45%) plus steel mazes S6/S32. Keeps improvements S1 (37%), S8 (23%),
   * S27 (9%), S28 (29%) enabled. Residual regressions S14/S18/S26 remain ON
   * — terrain density cannot fully separate them (see §69 analysis).
   */
  crossfireOpenObstacleRatio: number

  /**
   * §69-B: A* pathfinding threat cost. When > 0, the A* pathfinder adds a
   * threat cost penalty to cells where enemy bullets are expected to arrive
   * at the same time as the player. Unlike §68-v2's post-hoc diversion
   * (which switches direction AFTER the path is computed), this bakes threat
   * avoidance into the path itself — A* finds the optimal trade-off between
   * path length and safety.
   *
   * The threat cost is time-aware: for each cell in a bullet's trajectory,
   * the bullet's arrival tick (dist / bullet.speed) is compared with the
   * player's estimated arrival tick (manhattanDist * CELL / playerSpeed).
   * If they overlap within ±10 ticks, the cell gets a threat cost penalty.
   *
   * 0 = OFF (byte-identical to pre-§69-B). 3 = a threatened cell costs as
   * much as 4 safe cells (1 + 3), so A* prefers detours up to 3 extra cells.
   */
  crossfirePathCost: number

  /**
   * §48-revisit: Steel-only evasion occlusion. When > 0, the bullet-threat
   * scanner (`findMostDangerousBulletImpl`) skips enemy bullets whose path to
   * the player is blocked by STEEL. Steel is a permanent barrier for enemy
   * bullets — `STEEL_PIERCE_PLAYER_LEVEL = 3` is a player-only privilege, so
   * every enemy bullet (basic/fast/power/armor) dies on steel and can never
   * pass through, nor can any future bullet from the same direction. Dodging
   * a steel-blocked bullet is therefore purely wasteful: it moves the player
   * off-position for a threat that can never arrive.
   *
   * BRICK is deliberately NOT treated as occlusion (the original §48 fix's
   * mistake). Brick is temporary — the bullet destroys it and dies, but the
   * enemy fires again and the next bullet comes through. Dodging a
   * brick-blocked bullet is load-bearing "anticipatory dodging" (DECISIONS
   * §48 negative result: blocking brick cost S32 −10pp @120).
   *
   * The scan walks the full bullet→player path checking every cell for steel.
   * It does NOT stop at brick (there may be steel behind a brick that the
   * bullet will eventually clear through to). OOB cells cause a break (not a
   * false "steel" — avoiding the TileMap.get OOB→'steel' default that caused
   * §70's baseSteel false positive).
   *
   * 0 = OFF (byte-identical to pre-§48-revisit). 1 = ON.
   *
   * NOTE (per-seed tick-diff finding, S32 seed 11): a blanket steel occlusion
   * is NET NEUTRAL — suppressing a dodge removes load-bearing repositioning,
   * so the player can get pinned in a corner (mechanism confirmed at tick 738:
   * near steel-blocked bullet, player stayed at (0,1) and died, while the
   * baseline dodged down and won). The `evasionSteelOcclusionRange` gate
   * addresses this by only suppressing FAR blocked bullets (dist >= range
   * cells), whose dodge is genuinely wasteful, while keeping NEAR dodges.
   */
  evasionSteelOcclusion: number

  /**
   * §48-revisit: distance gate (cells) for `evasionSteelOcclusion`. Only
   * suppress the dodge for a steel-blocked bullet when it is at least this
   * many cells from the player. A far blocked bullet's dodge is genuinely
   * wasteful (the player is not in imminent danger); a near blocked bullet's
   * dodge is load-bearing repositioning (the player is being pressured and
   * needs to move) — keeping it avoids the S32 pinning regression.
   *
   * 0 = suppress ALL steel-blocked bullets (no distance gate). N = suppress
   * only blocked bullets at dist >= N cells.
   */
  evasionSteelOcclusionRange: number

  /**
   * §48-revisit (terrain gate): brick/(brick+steel) ratio BELOW which
   * `evasionSteelOcclusion` is auto-enabled in computeStageAdaptedParams.
   * 0 = never auto-enable (byte-identical to pre-§48-revisit).
   *
   * Discriminator discovered via per-seed tick-diff + stage probes: steel
   * ratio is NOT the predictor (S26 Brick Maze has MORE steel than S32
   * Diamond — 26% vs 18% — yet regresses while S32 gains). The predictor
   * is brickWallRatio:
   *   - Steel mazes (S32 0.063, S6 0.04): occlusion HELPS (+2.5~3.3pp S32
   *     @120). The player fights in open guard bands / steel corridors;
   *     a steel-blocked bullet's dodge is genuinely wasteful.
   *   - Brick-heavy (S14 0.915, S26 0.254): occlusion HURTS (-5pp S14,
   *     -6.7pp S26 @120). Dodging a blocked bullet is load-bearing
   *     repositioning through breakable cover; skipping it re-ranks the
   *     scan to a farther bullet (S26 seed-7: player dodged down one tick
   *     early and lost).
   * Default 0.10 = enable on steel mazes only (matches isSteelMaze / §60).
   */
  evasionSteelOcclusionBrickRatio: number

  /**
   * §48-revisit: Trap avoidance (user idea 2 — don't walk into surround
   * positions). When > 0, the navigation branch (T2b) checks the NEXT cell
   * before committing to the current move direction: if that cell has few
   * passable exits (≤ 2 — a corridor / corner / dead-end) AND `trapEnemyCount`
   * or more enemies are within `trapEnemyRadiusCells` of it, the player is
   * at risk of being surrounded there. The move direction is overridden to
   * the open direction whose next cell has the most exits (tie-broken toward
   * the base). Runs at the END of navigation (after _moveDir is chosen), so
   * it only perturbs the final move — dodge / T8 / T2a priorities are intact.
   *
   * 0 = OFF (byte-identical). 1 = ON.
   */
  trapAvoidance: number
  /** Radius (cells) around the destination cell for the trap enemy census. */
  trapEnemyRadiusCells: number
  /** Min live enemies within the radius that make a low-exit cell a trap. */
  trapEnemyCount: number

  /**
   * §49-revisit: 炮口相向对枪抵消（§52 v2，T2a 内联，当前保留形态）。
   *
   * When > 0, the T2a branch detects an enemy facing the player within
   * `counterFireMaxRange` cells (and not on ice) and: (a) if an enemy bullet
   * is already in the line of fire, fires to cancel it (对枪抵消 — bullet
   * elimination is safer than trading hits); (b) otherwise keeps alignment
   * toward the enemy without strafing (保持对齐以备对枪).
   *
   * When 0, T2a uses the plain stop-and-aim behavior (pre-§52 form — turn
   * to face and fire, no facing-enemy special-casing).
   *
   * Default 1 = current shipped behavior (byte-identical to pre-parameter-
   * ization). OFF (0) is the A/B baseline (pre-§52 v2).
   */
  counterFire: number
  /**
   * §49-revisit: max distance (cells) for the facing-enemy counter-fire /
   * keep-alignment block in T2a. The original hardcoded value was 5.
   */
  counterFireMaxRange: number

  /**
   * §74: Steel-fire gate — don't fire at steel walls the player cannot
   * pierce while trying to BREAK THROUGH a wall. When > 0, the two
   * break-through fire sites in think() that bypass shouldFireInDirImpl
   * (aggressive navigate break-through + T2b navigate break-through) apply
   * the same steel gate that shouldFireInDirImpl's T11 already enforces:
   * steel blocks fire while `p.level < STEEL_PIERCE_PLAYER_LEVEL` (3).
   *
   * Without this gate, the AI fires at indestructible steel to open a path
   * (wasting the bullet cap) and then camps at the wall for the full
   * campTimeoutTicks (90 default) before the anti-camp escape — the
   * reported "shoot steel, can't break through, stuck in place" behavior.
   * With the gate ON, the AI falls through to navigation instead, which
   * routes AROUND the steel via corridors (steel is impassable to A*),
   * restoring mobility.
   *
   * Scope note (per-seed A/B, 2026-08-01): deliberately NOT applied to the
   * T2a/aggressive stop-and-aim sites (which fire when scan.enemy is true)
   * — the dual-offset case (steel on one scan line, enemy on the other)
   * means the enemy is genuinely reachable by the center-line bullet, and
   * suppressing that fire costs kills (arena A/B: 20 kills → 7 kills).
   *
   * At level ≥ 3 the player CAN pierce steel, so break-through fire at
   * steel is correct and the gate is inert (byte-identical to pre-§74
   * behavior).
   *
   * 0 = OFF (byte-identical to pre-§74 behavior — A/B baseline).
   * 1 = ON (default — the fix).
   */
  steelFireGate: number

  /**
   * §80: Turn-snap aim guard — don't commit to a stop-and-aim TURN whose own
   * grid-snap would take the tank off the line it wanted to fire along.
   *
   * Root cause (replay classic-s11-…-seed1785622102123, 0:31–0:47):
   * turning is not free. `Simulation.updateMovement` axis-locks the tank and
   * snaps the PERPENDICULAR coordinate to the grid on every direction change
   * (`axis === 'x' ? tank.y = snap(tank.y, CELL) : tank.x = snap(...)`).
   * A tank sitting at a non-grid-aligned sub-cell offset therefore MOVES up
   * to CELL/2 px sideways just by turning — which can slide the enemy out of
   * its firing line.
   *
   * That creates a stable period-2 deadlock in the `aggressive` (freeze)
   * branch, which has no anti-stall guard of its own (T2a has the
   * `_campTicks` escape, navigate has `_navStuckTicks`; aggressive has
   * neither, and during a freeze window it is the ONLY branch that runs):
   *
   *   tick A: not aligned → `scanAhead(aimDir).enemy` false → fall through to
   *           navigate → move along path (perpendicular) → snap puts the tank
   *           BACK on the firing line
   *   tick B: `scanAhead(aimDir).enemy` true → turn to aim → snap puts the
   *           tank back OFF the firing line → back to tick A
   *
   * Net displacement per 2 ticks: zero. The AI burns the entire freeze
   * window — the single highest-value window in the game, when enemies are
   * helpless — jittering between two sub-cell positions while firing at
   * nothing. Measured pre-fix over 35 stages × 10 seeds: 4.3% of all freeze
   * ticks (coop) / 2.9% (single) were this oscillation, with worst-case runs
   * losing 1166 of 1200 freeze ticks (97% of the window).
   *
   * The guard re-runs the line-of-fire scan from the position the tank would
   * actually occupy AFTER the turn-snap. If the enemy is no longer on that
   * line, the aim is a lie — fall through to navigate and keep moving.
   *
   * Inert when the tank is already grid-aligned on the perpendicular axis
   * (`snap(v) === v`), which is the overwhelmingly common case — so this is
   * byte-identical to pre-§80 behavior except in the pathological geometry
   * that causes the deadlock.
   *
   * Known residual (measured, 2026-08-01): the guard does NOT eliminate ALL
   * freeze-window oscillation — the worst measured streak (Brick Maze
   * s27/seed1, 1166 of 1200 freeze ticks) is byte-identical with the guard
   * ON: the guard is inert on that run (its scans never change the outcome).
   * The mechanism was not per-tick traced; a grid-aligned early-return is one
   * candidate. The aggressive branch still lacks an anti-stall guard for this
   * residual — see DECISIONS §80.
   *
   * 0 = OFF (byte-identical to pre-§80 behavior — A/B baseline).
   * 1 = ON (default — the fix).
   */
  aimTurnSnapGuard: number
}

/** Default God AI parameters — optimized via CMA-ES P4 round 7 (2026-07-29).
 * See .workbuddy/optimization-p4-r7/ for details.
 *
 * P4 R7 CMA-ES used the floor-aware v5.0 fitness over ALL 35 classic
 * stages × 20 seeds (18000 ticks), warm-started from R6, with the
 * per-stage override table (godai-stage-overrides.ts) active in the
 * inner loop — the optimizer pushes the global mean while the override
 * table guards the per-stage floor.
 *
 * P4 R7 truth-scale results (35 stages × 60 seeds, classic, 18000 ticks,
 * override table active):
 *   Mean win rate: 81.9%  (target > 80% — PASS)
 *   Below 60% floor: 1/35 — S32 Diamond 52% (known structural hard case;
 *   verified not param-tunable at 60 seeds: manual probes on R6+R7 bases
 *   and a dedicated single-stage CMA-ES all scored at or below base).
 *
 * Key changes from P3:
 *   - defenseColSpread 9→5, threatRangeCells 20→10 (defense triggers only
 *     on real threats; the race-to-base check covers flankers)
 *   - maxPlayerDistFromBase 19→26 (roam freely; race check guards base)
 *   - baseRaceRangeCells 12→11, margin 2→0 (leaner race-to-base trigger)
 *   - t8MaxInterceptDistCells 2→8, baseWallScanRadius 1→3 (protect base
 *     bricks more actively)
 *   - powerupMaxDivertDistance 3→16 (power-ups are worth a detour)
 *   - endgameEnemyThreshold 4→6, huntAllyCount 6→1 (hunt earlier, alone)
 *   - aimError 0→0.03 (counter-intuitive: tiny aim noise breaks mutual-
 *     block standoffs; per-stage overrides set it back to 0 where armor
 *     density punishes wasted shots)
 */
export const DEFAULT_GOD_AI_PARAMS: GodAIParams = {
  reactionDelay: 0,
  aimError: 0.03030591179971963,
  suboptimalPathProb: 0,

  defenseRowOffset: 1,
  defenseColSpread: 5,
  threatRangeCells: 10,
  maxPlayerDistFromBase: 26,
  // P4: race-to-base emergency defense (see interface docs). Range 11 keeps
  // the check regional; margin 0 = defend only when the enemy would win
  // the race outright.
  baseRaceRangeCells: 11,
  baseRaceMarginCells: 0,
  // P4.2: retreat when 3+ enemies converge within 9 cells — the player
  // trades 1-for-1 at best in open crossfire; falling back to the defense
  // row funnels enemies into single-file corridors instead.
  outnumberedEnemyCount: 3,
  outnumberedRadiusCells: 9,
  t8MaxInterceptDistCells: 8,
  baseWallScanRadius: 3,
  replanInterval: 50,
  powerupMaxDivertDistance: 16,
  endgameEnemyThreshold: 6,
  huntAllyCount: 1,

  // P0: Anti-camp / T2a deadlock fix (plan/God-AI-Next-Round).
  // campTimeoutTicks=90 (1.5s) — if the player hasn't gotten a kill in 1.5s
  // of camping, something is wrong (enemy dodging, wall in the way, etc.).
  // antiCampSuppressTicks=60 (1s) — enough to move ~2 cells at player speed,
  // changing the tactical situation before T2a can re-trigger.
  campTimeoutTicks: 90,
  antiCampSuppressTicks: 60,
  // P0.3: navStuckTicks=180 (3s) — if the player hasn't progressed (stayed
  // at the same cell) for 3 seconds of navigating, force a roam to the map
  // center. This breaks pursuit loops with faster enemies.
  navStuckTicks: 180,

  // D1/D2: Guard band mode + damaged armor priority. Default OFF (0) for
  // both — regression-safe. damagedArmorBonus was tested at 1 but HURT S32
  // (-8.4pp) by causing target-switching that interrupts armor grinding.
  // The HP-dependent t2aHighHpMaxRange already handles the "finish damaged
  // armor" use case by extending range as HP drops.
  guardBandMode: 0,
  guardBandRow: 20,
  guardBandHalfWidth: 7,
  damagedArmorBonus: 0,
  // Close-combat: default 15 (= AIM_RANGE_CELLS, unchanged behavior for
  // 1-HP enemies). For multi-HP enemies (armor), t2aHighHpMaxRange=2
  // triggers point-blank engagement (§56 — generalizes S32 close-combat).
  t2aMaxRange: 15,
  t2aHighHpMaxRange: 2,

  // Smart threat model (Phase A): default OFF (0). All 35 stages are
  // byte-identical when OFF. Only stages with smartThreatModel > 0 in
  // the override table activate the smart scoring.
  smartThreatModel: 0,
  smartThreatThreshold: 0.55,
  smartThreatSpeedWeight: 0.6,
  smartThreatFacingWeight: 0.2,
  smartThreatHpWeight: 0.2,
  smartThreatDistRange: 12,
  smartRushDetectBonus: 4,

  // §58: Stage-level adaptive params (Strategy G). These generalize the old
  // per-stage override table (godai-stage-overrides.ts) into a data-driven
  // adaptation based on stage characteristics computed in reset(). Default ON
  // — the thresholds and adapted values are tuned to match the old overrides
  // exactly on the stages they covered (S26 Brick Maze, S32 Diamond), while
  // leaving other stages on the base params. See DECISIONS §58.
  armorAdaptRatio: 0.35,
  armorCampTimeoutTicks: 50,
  armorAntiCampSuppressTicks: 50,
  armorNavStuckTicks: 90,
  brickDenseAdaptRatio: 0.45,
  brickDenseReplanInterval: 30,
  brickDenseSuboptimalPathProb: 0.05,

  // §59 / Strategy C: clear-shot bonus in defense-mode target selection.
  // Default 500 — prioritizes enemies with a clear line of fire to the base.
  // 0 = OFF (byte-identical to pre-§59). Decoupled from smartThreatModel.
  defenseClearShotBonus: 500,

  // §60: Open-defense adaptation. On non-steel-maze stages, widen
  // baseRaceRangeCells from 11 to 14 for earlier threat detection. Steel
  // mazes (brick/(brick+steel) < 0.10) keep the default 11 — early retreat
  // hurts there because enemies bypass the defense position via corridors.
  openDefenseBrickWallRatio: 0.1,
  openDefenseBaseRaceRangeCells: 14,

  // §61: Terrain-adaptive T2a range. On open-sightline stages (low forest or
  // high water), engage armor from range 4 instead of 2 — faster kills, less
  // damage taken. On forest-dense stages, keep point-blank (range 2).
  // §62: suppressed when armor ratio ≥ 25% — armor-heavy stages need
  // point-blank regardless of sightline (range 4 trades inefficiently).
  openT2aForestRatio: 0.15,
  openT2aHighHpMaxRange: 4,
  openT2aWaterRatio: 0.25,
  openT2aMaxArmorRatio: 0.25,
  openT2aSteelRatio: 0.15,
  // §62: forest-dense armor T2a range. On armor-heavy stages with forest
  // ≥ 25%, use range 3 (not 2) — the forest absorbs enemy bullets, giving
  // the player room to maneuver at range 3 without taking damage. Range 2
  // is too close (player takes point-blank hits), range 4 is too far in
  // forest (bullets hit trees). Probes: S14 +10pp with range 3.
  armorForestDenseRatio: 0.25,
  armorForestDenseRange: 3,
  // §63: REVERTED — open-sightline 1-HP T2a range. Probes showed improvement
  // on S1/S7 but regression on S8/S11/S30/S33 in full 60-seed validation.
  // The adaptation is net negative (-0.6pp mean). Kept as 0 (OFF) for safety.
  openT2a1HpMaxRange: 0,
  // §64: armor-heavy + high-steel + non-steel-maze → widen outnumberedRadius.
  // Only S26 matches. Probes: S26 +10pp (75% → 85%, 30 seeds). 9 = no change.
  armorSteelOutnumberedRadiusCells: 12,
  // §65: REVERTED — armor-heavy + steel-maze path randomness. 30-seed probe
  // showed S32 +3pp, but 60-seed validation showed -1.7pp (66.7% → 65%).
  // The 30-seed gain was seed-specific noise. Kept as 0 (OFF) for safety.
  armorMazeSuboptimalPathProb: 0,
  // §66: steel-maze non-armor camp timeout. Only S6 matches. +16pp (60 seeds).
  steelMazeCampTimeoutTicks: 20,

  // §68-v2: Crossfire awareness — see interface docs. Default 0 (OFF).
  // v1 was neutral (-0.4pp); v2 uses time-aware projection + multi-strategy
  // response instead of fixed-proximity + perpendicular dodge.
  crossfireAwareness: 0,
  // §69: Terrain-gated crossfire. 0 = never auto-enable (byte-identical).
  // 0.40 = enable on open stages (obstacle density < 40%, not steel maze).
  crossfireOpenObstacleRatio: 0,
  // §69-B: A* threat cost. 0 = OFF (byte-identical). 3 = prefer detours up
  // to 3 extra cells to avoid bullet-threatened cells.
  crossfirePathCost: 0,

  // §48-revisit: Steel-only evasion occlusion. 0 = OFF (byte-identical to
  // pre-§48-revisit). See interface docs. Only steel (permanent for enemy
  // bullets) is treated as occlusion; brick (temporary) is NOT — dodging
  // brick-blocked bullets is load-bearing anticipatory dodging (DECISIONS §48).
  // Default range 0 = suppress ALL steel-blocked (per-seed tick-diff showed
  // this is NET NEUTRAL — see interface docs for the pinning mechanism).
  evasionSteelOcclusion: 0,
  // Distance gate for steel occlusion (cells). 0 = no gate (suppress all).
  // >0 suppresses only blocked bullets at dist >= range.
  evasionSteelOcclusionRange: 0,
  // §48-revisit terrain gate: 0 = never auto-enable (byte-identical).
  // 0.10 = auto-enable occlusion on steel-maze stages (brickWallRatio below
  // 0.10 — S32 Diamond 0.063, S6 Iron Curtain 0.04). Brick-heavy stages
  // (S14 0.915, S26 0.254) stay OFF — they regress under occlusion (the
  // dodge is load-bearing repositioning). Verified 2026-08-01: 35×60 net 0
  // with zero per-stage regressions (S14/S26 byte-identical); 120-seed
  // confirmations S32 +2.5pp (68.3→70.8), S6 +0.8pp (80.0→80.8).
  evasionSteelOcclusionBrickRatio: 0.1,
  // §48-revisit: trap avoidance (user idea 2). 0 = OFF (byte-identical).
  trapAvoidance: 0,
  trapEnemyRadiusCells: 5,
  trapEnemyCount: 2,
  // §49-revisit: 炮口相向对枪抵消 (§52 v2). 1 = ON (current shipped
  // behavior, byte-identical to pre-parameterization). 0 = OFF (plain T2a).
  counterFire: 1,
  // Max range (cells) for the facing-enemy block. 5 = the original §52 v2
  // hardcoded value.
  counterFireMaxRange: 5,
  // §74: Steel-fire gate — 1 = ON (default). 0 = OFF (pre-§74 behavior,
  // A/B baseline). See interface docs.
  steelFireGate: 1,
  // §80: Turn-snap aim guard — 1 = ON (default, the fix). 0 = OFF (pre-§80
  // behavior, A/B baseline). See interface docs.
  aimTurnSnapGuard: 1,
}

/**
 * Skilled Human proxy parameters (plan §3.3C): God AI + double reaction
 * delay + 20% aim error. Represents an experienced but non-perfect human.
 * MUST remain derived from God params — God gets stronger → human proxy
 * gets stronger automatically. Strategy thresholds are inherited as-is.
 * Minimums ensure the human is always weaker than God (even when God is perfect).
 */
export const SKILLED_HUMAN_PARAMS: GodAIParams = {
  ...DEFAULT_GOD_AI_PARAMS,
  reactionDelay: Math.max(2, DEFAULT_GOD_AI_PARAMS.reactionDelay * 2),
  aimError: Math.max(0.15, DEFAULT_GOD_AI_PARAMS.aimError + 0.15),
  suboptimalPathProb: Math.max(0.15, DEFAULT_GOD_AI_PARAMS.suboptimalPathProb * 1.5),
}

// ============================================================
// §58: Stage-level adaptive params (Strategy G)
// ============================================================

/**
 * §58 / Strategy G: compute stage-adapted God AI params from stage
 * characteristics, replacing the per-stage override table.
 *
 * Two adaptations are applied (both data-driven, both OFF when the threshold
 * param is 0):
 *
 *  1. Armor-ratio adaptation: when the stage's enemy queue has an armor
 *     ratio ≥ `armorAdaptRatio`, switch to close-combat camp/nav timing
 *     (`armorCampTimeoutTicks` / `armorAntiCampSuppressTicks` /
 *     `armorNavStuckTicks`). Armor-heavy stages suffer from T2a deadlocks
 *     and pursuit loops — armor is slow and creates traffic jams. Shorter
 *     timers break these loops faster (generalizes the old S32 override).
 *
 *  2. Brick-density adaptation: when the stage's terrain has a brick
 *     density ≥ `brickDenseAdaptRatio`, use faster replanning + small path
 *     noise (`brickDenseReplanInterval` / `brickDenseSuboptimalPathProb`).
 *     Brick-dense stages cause deadlock patrol loops in narrow corridors;
 *     faster replan + noise break the symmetry (generalizes the old S26
 *     override).
 *
 *  3. Open-defense adaptation (§60): when the stage's brick/(brick+steel)
 *     ratio ≥ `openDefenseBrickWallRatio`, widen `baseRaceRangeCells` to
 *     `openDefenseBaseRaceRangeCells` (14) for earlier threat detection.
 *     Steel-maze stages (brick/(brick+steel) < 0.10, e.g. S6/S32) keep the
 *     default — early retreat hurts there because enemies bypass the defense
 *     position through indestructible corridors.
 *
 * Determinism: both computations are pure functions of World state
 * (spawnQueue + tileMap), so the same stage always yields the same adapted
 * params. Called once per reset() — never per-tick.
 */
export function computeStageAdaptedParams(base: GodAIParams, world: World): GodAIParams {
  const p = base
  let adapted = false
  const overrides: Partial<GodAIParams> = {}

  // ---- 1. Armor-ratio adaptation ----
  // Compute armor ratio once — reused by open-T2a suppression (§62).
  let armorHeavy = false
  let armorRatio = 0
  if (world.spawnQueue.length > 0) {
    let armorCount = 0
    for (let i = 0; i < world.spawnQueue.length; i++) {
      if (world.spawnQueue[i].kind === 'armor') armorCount++
    }
    armorRatio = armorCount / world.spawnQueue.length
  }
  if (p.armorAdaptRatio > 0 && armorRatio >= p.armorAdaptRatio) {
    overrides.campTimeoutTicks = p.armorCampTimeoutTicks
    overrides.antiCampSuppressTicks = p.armorAntiCampSuppressTicks
    overrides.navStuckTicks = p.armorNavStuckTicks
    armorHeavy = true
    adapted = true
  }

  // ---- 2. Terrain scan: brick-density + open-defense + open-T2a + aimError (§60/§61/§62) ----
  // All terrain-based adaptations share one scan. Called once per reset() —
  // never per-tick — so the 676-cell iteration is not a hot path.
  {
    const tm = world.tileMap
    let brickCount = 0
    let steelCount = 0
    let forestCount = 0
    let waterCount = 0
    for (let row = 0; row < GRID; row++) {
      for (let col = 0; col < GRID; col++) {
        const t = tm.get(col, row)
        if (t === 'brick') brickCount++
        else if (t === 'steel') steelCount++
        else if (t === 'forest') forestCount++
        else if (t === 'water') waterCount++
      }
    }
    const totalCells = GRID * GRID
    const steelRatio = steelCount / totalCells

    // §62: on armor-heavy stages with LOW steel, eliminate aim noise — armor
    // takes 4 hits, so every wasted shot extends the fight. Probes showed
    // aimError=0 gives +4-8pp on S14/S19 (low steel). On steel-heavy stages
    // (S26: 26% steel), the noise breaks corridor standoffs — keep it.
    if (armorHeavy && steelRatio < p.openT2aSteelRatio) {
      overrides.aimError = 0
      adapted = true
    }

    const brickRatio = brickCount / totalCells
    if (p.brickDenseAdaptRatio > 0 && brickRatio >= p.brickDenseAdaptRatio) {
      overrides.replanInterval = p.brickDenseReplanInterval
      overrides.suboptimalPathProb = p.brickDenseSuboptimalPathProb
      adapted = true
    }

    // Compute brick/(brick+steel) once — used by both open-defense and open-T2a.
    const wallCount = brickCount + steelCount
    const brickWallRatio = wallCount > 0 ? brickCount / wallCount : 1
    const isSteelMaze = brickWallRatio < p.openDefenseBrickWallRatio

    // §60: open-defense — widen baseRaceRangeCells on non-steel-maze stages.
    // SKIPPED on armor-heavy stages (armor requires aggressive close-combat;
    // early retreat trades base defense for lives exhausted).
    if (p.openDefenseBrickWallRatio > 0 && !armorHeavy && !isSteelMaze) {
      overrides.baseRaceRangeCells = p.openDefenseBaseRaceRangeCells
      adapted = true
    }

    // §61/§62: open-T2a — widen t2aHighHpMaxRange on open-sightline stages.
    // On open stages (low forest or high water), the player can see and hit
    // armor from range 4 — faster kills, less damage taken. On forest-dense
    // stages, enemies are hidden — keep point-blank (range 2). Steel mazes
    // also keep range 2 (enemies advance through corridors, range 4 wastes
    // bullets on walls).
    // §62: SUPPRESSED when armor ratio ≥ openT2aMaxArmorRatio AND the trigger
    // was forest (not water) AND the stage is not steel-heavy. Bypassed when:
    //   - water-triggered (water lanes allow safe long-range engagement), OR
    //   - steel-heavy (steel corridors force head-on encounters needing range 4).
    // §63: openSightline is also reused for the 1-HP t2aMaxRange adaptation.
    const forestRatio = forestCount / totalCells
    const waterRatio = waterCount / totalCells
    const waterTriggered = waterRatio >= p.openT2aWaterRatio
    const openSightline = !isSteelMaze && (forestRatio < p.openT2aForestRatio || waterTriggered)
    if (openSightline) {
      const armorSuppress =
        armorRatio >= p.openT2aMaxArmorRatio && !waterTriggered && steelRatio < p.openT2aSteelRatio
      if (!armorSuppress) {
        overrides.t2aHighHpMaxRange = p.openT2aHighHpMaxRange
        adapted = true
      }

      // §63: on open-sightline non-armor-heavy stages, reduce t2aMaxRange for
      // 1-HP enemies. The player closes in for more reliable kills (less
      // bullet travel time) and better base-defense positioning. Suppressed
      // on armor-heavy stages (most enemies are armor; 1-HP range is less
      // relevant, and armor needs aggressive close-combat).
      if (p.openT2a1HpMaxRange > 0 && !armorHeavy) {
        overrides.t2aMaxRange = p.openT2a1HpMaxRange
        adapted = true
      }
    }

    // §62: forest-dense armor T2a range. On armor-heavy stages with forest
    // ≥ 25%, the forest absorbs enemy bullets, giving the player room to
    // engage at range 3 instead of point-blank 2. Only fires when open-T2a
    // did NOT already set a range (forest ≥ 15% blocks open-T2a, so these
    // stages would otherwise stay at the default 2). Probes: S14 +10pp.
    if (armorHeavy && p.armorForestDenseRatio > 0) {
      if (forestRatio >= p.armorForestDenseRatio) {
        overrides.t2aHighHpMaxRange = p.armorForestDenseRange
        adapted = true
      }
    }

    // §64: armor-heavy + high-steel + non-steel-maze → widen retreat radius.
    // On S26 (40% armor, 26% steel, brickWallRatio 0.26), the player gets
    // swarmed in steel corridors. Retreating from radius 12 (vs default 9)
    // gives more room to avoid being pinned. Excluded: S32 (steel-maze,
    // brickWallRatio 0.05) where wider retreat HURTS (-7pp). Only S26
    // matches this regime across all 35 stages.
    if (
      armorHeavy &&
      !isSteelMaze &&
      steelRatio >= p.openT2aSteelRatio &&
      p.armorSteelOutnumberedRadiusCells !== base.outnumberedRadiusCells
    ) {
      overrides.outnumberedRadiusCells = p.armorSteelOutnumberedRadiusCells
      adapted = true
    }

    // §65: armor-heavy + steel-maze → add path randomness. On S32 (40% armor,
    // brickWallRatio 0.05), the player gets pinned in predictable positions
    // by the steel corridors. Path randomness (0.05) breaks the deterministic
    // pinning pattern and improves survival. Only S32 matches this regime.
    if (armorHeavy && isSteelMaze && p.armorMazeSuboptimalPathProb !== base.suboptimalPathProb) {
      overrides.suboptimalPathProb = p.armorMazeSuboptimalPathProb
      adapted = true
    }

    // §66: steel-maze + low-armor → shorter camp timeout. On S6 Iron Curtain
    // (0% armor, brickWallRatio 0.04), the player camps at indestructible
    // steel walls for the full 60-tick timeout, wasting ~1s per deadlock.
    // A 20-tick timeout breaks the deadlock 3× faster. Excluded: S32
    // (armor-heavy steel-maze) where the armor camp timing already applies.
    if (isSteelMaze && !armorHeavy && p.steelMazeCampTimeoutTicks !== base.campTimeoutTicks) {
      overrides.campTimeoutTicks = p.steelMazeCampTimeoutTicks
      adapted = true
    }

    // §69: Terrain-gated crossfire awareness. Enable crossfire on open stages
    // (low obstacle density, not a steel maze). On maze stages, diversion from
    // the A* path is too expensive — §68 showed -15pp on S6/S26 (maze) vs
    // +12pp on S28 (open). Obstacle density = (brick+steel+water)/totalCells.
    // Water is included because it creates impassable corridors.
    if (p.crossfireOpenObstacleRatio > 0 && base.crossfireAwareness === 0 && !isSteelMaze) {
      const obstacleDensity = (brickCount + steelCount + waterCount) / totalCells
      if (obstacleDensity < p.crossfireOpenObstacleRatio) {
        overrides.crossfireAwareness = 1
        adapted = true
      }
    }

    // §48-revisit: terrain-gated steel-only evasion occlusion. Auto-enable
    // ONLY on steel-maze stages (brickWallRatio < evasionSteelOcclusionBrickRatio).
    // Steel ratio is NOT the predictor: S26 Brick Maze has MORE steel (26%)
    // than S32 Diamond (18%) yet regresses while S32 gains. The predictor is
    // brickWallRatio — steel mazes (S32 0.063, S6 0.04) gain +2.5~3.3pp @120,
    // brick-heavy stages (S14 0.915, S26 0.254) lose -5~6.7pp (dodge
    // suppression removes load-bearing repositioning; S26 seed-7 re-ranks to
    // a farther bullet and dodges one tick early). 0 = never auto-enable
    // (byte-identical to pre-§48-revisit).
    if (
      p.evasionSteelOcclusionBrickRatio > 0 &&
      base.evasionSteelOcclusion === 0 &&
      brickWallRatio < p.evasionSteelOcclusionBrickRatio
    ) {
      overrides.evasionSteelOcclusion = 1
      adapted = true
    }
  }

  return adapted ? { ...base, ...overrides } : base
}

// ============================================================
// GodAIInput (orchestrator)
// ============================================================

export class GodAIInput implements InputLike {
  // Shared state — public so the extracted ./god/* sub-modules can read/write
  // it via the `self: GodAIInput` they receive. This is the §0.5 split;
  // behavior is unchanged from before the split.
  world: World
  rng: RNG
  params: GodAIParams
  /**
   * §58: the original params passed to the constructor, before stage-level
   * adaptation is applied in reset(). reset() computes `this.params` from
   * this base each time, so the adaptation never compounds across resets.
   */
  _baseParams: GodAIParams

  /**
   * Lie-Back-Win-Mode §3.8 P1: returns the tank this AI controls.
   * Default: `w => w.player` (single-player / parity-safe).
   * Co-op mode: pass `w => w.player2` to make God AI control P2.
   */
  controlledTank: (w: World) => Tank | null = (w) => w.player

  /** Cached move direction for this tick. */
  _moveDir: Direction | null = null
  /** Cached fire decision for this tick. */
  _fire = false
  /** Whether think() ran this tick (avoids double-computation). */
  _thought = false

  /** Current A* path being followed. */
  path: Direction[] = []
  /** Re-plan counter. */
  replanTimer = 0

  /** Threat reaction: when a threat is first seen, count down before reacting. */
  reactionCounter = 0
  /** The last threat bullet id we reacted to. */
  lastThreatId: number = -1

  /** Whether the AI is in aggressive mode (freeze/shield — hunt, don't defend). */
  aggressive = false

  /**
   * Whether the current stage has a base (plan/God-AI-Curriculum §3 Gap B).
   * Cached in reset() from `world.tileMap.hasBase()`. When false, ALL base-
   * defense logic is skipped: no T8 bullet interception, no defense positioning,
   * no `baseUnderThreat` check, no `playerDistToBase` constraint. The AI
   * degrades to pure hunting (chase nearest enemy).
   */
  hasBase = true

  /** Last cell the player was at when consuming a path step (prevents oscillation). */
  _lastPathCell: Cell | null = null

  /** P0.1: the cell where the player is currently camping (T2a stop-and-aim). */
  _campCell: Cell | null = null
  /** P0.1: consecutive ticks spent at _campCell in T2a. */
  _campTicks = 0
  /** P0.1: world.killCount when camping started (to detect "no kills during camp"). */
  _campKillsAtStart = 0
  /** P0.1: countdown to suppress T2a after an anti-camp escape. */
  _antiCampSuppress = 0

  /** P0.3: the cell where the player is currently stuck in navigate. */
  _navStuckCell: Cell | null = null
  /** P0.3: consecutive ticks spent at _navStuckCell in navigate. */
  _navStuckTicks = 0

  /** Debug: branch counters for profiling. */
  branchCounts = { dodge: 0, t8: 0, aggressive: 0, t2a: 0, powerup: 0, navigate: 0, dead: 0 }

  /** Reusable scan result for scanAheadImpl — avoids allocating a result object on every call. */
  _scanResult: {
    enemy: boolean
    wall: boolean
    steel: boolean
    baseWall: boolean
    baseWallDist: number
    baseSteel: boolean
    steelCol: number
    steelRow: number
    enemyDist: number
    enemyKind: TankKind
    enemyHp: number
    enemyMaxHp: number
  } = {
    enemy: false,
    wall: false,
    steel: false,
    baseWall: false,
    baseWallDist: Infinity,
    baseSteel: false,
    steelCol: -1,
    steelRow: -1,
    enemyDist: Infinity,
    enemyKind: 'basic',
    enemyHp: 1,
    enemyMaxHp: 1,
  }

  /** Reusable buffer for scanAheadImpl's per-offset aligned-tank pre-filter.
   * Reset (via alignedCount=0) at the start of each offset — no allocation. */
  _scanAligned: Tank[] = []

  /**
   * Cluster C (perf): per-tick snapshot of live, fully-spawned enemy tanks —
   * identical to `w.tanks.filter(t => t.alive && t.spawnTimer <= 0)` in both
   * membership and iteration order. Reused by `isBaseUnderThreat`,
   * `selectTarget`, and `calculateRouteDanger` instead of re-filtering
   * `w.tanks` on every call. Same filter + order ⇒ byte-identical decisions
   * (incl. `enemies[0]` tie-breaks), so calibration stays valid.
   */
  _enemies: Tank[] = []

  /**
   * Cluster C (perf): per-tick snapshot of all live tanks (player included) —
   * identical to `w.allTanks.filter(o => o.alive)` in membership/order.
   * Reused by `canMoveOrBreak`/`canMoveDir` for collision checks (the loop
   * still does `if (o === tank ...) continue`, so every caller — only the
   * player in practice — gets the exact same obstacle set as before).
   */
  _otherTanks: Tank[] = []

  /**
   * Per-tick lazy caches (perf): these methods are pure functions of World
   * state and are called multiple times per tick from different branches of
   * think(). The player doesn't move during think() (movement is applied
   * later in Simulation.updateMovement), so caching within a tick is
   * byte-identical. Invalidated in endFrame() alongside _thought.
   */
  _baseUnderThreatCache: boolean | null = null
  _fastThreatCache: boolean | null = null
  _playerCellCache: Cell = { col: 0, row: 0 }
  _playerCellValid = false
  /** Reusable buffer for tankCellImpl — avoids allocating a {col,row} per call
   * (tankCell is called ~15× per think, many in enemy loops). Callers must
   * consume the result before calling tankCell again. */
  _tankCellBuf: Cell = { col: 0, row: 0 }
  /** Per-tick canMoveDir cache for the player (perf): bitmask. Bit i
   * (0=up,1=down,2=left,3=right) set in _canMoveComputed when computed,
   * in _canMoveResult when passable. Invalidated in endFrame(). */
  _canMoveComputed = 0
  _canMoveResult = 0

  /**
   * (perf §68 Round 9) Cross-tick navigateTowards cache.
   *
   * `navigateTowardsImpl` is called every tick from think()'s navigate branch,
   * but its inputs (playerCell + targetCell) only change when the player
   * crosses a cell boundary (~every 23 ticks at player speed) or the target
   * cell changes (also second-scale, since enemies move at most ~1 cell/sec).
   * The terrain that A* reads also only changes when a brick is destroyed,
   * which always coincides with the player entering a new cell or a target
   * shift. Re-running A* while `(playerCell, target)` is unchanged is pure
   * waste — the result is byte-identical.
   *
   * The cache holds the last (playerCell, target, result) triple and is
   * reused while both inputs are stable. A safety timer forces a replan
   * every `_navReplanMax` ticks (default 60 = 1 second) to bound staleness
   * in edge cases (e.g. an enemy destroys a brick between cells).
   *
   * NOT invalidated in endFrame() — this is intentionally a cross-tick cache
   * (the inputs are the same across ticks; that's the whole point). It is
   * reset on resetForRun() / loadStage().
   */
  _navCacheValid = false
  _navPlayerCol = 0
  _navPlayerRow = 0
  _navTargetCol = 0
  _navTargetRow = 0
  _navCache: Direction | null = null
  _navReplanTimer = 0
  _navReplanMax = 60

  /**
   * §69-B: Reusable threat cost buffer for A* pathfinding. Size GRID*GRID.
   * When crossfirePathCost > 0, computeThreatCosts fills this array with
   * per-cell threat penalties based on current bullet trajectories, and
   * navigateTowards/replan pass it to findPath via PathConstraints.threatCosts.
   * Reused across calls (same as _pfGScore etc. in pathfind.ts) — findPath
   * is synchronous and never reentrant.
   */
  _threatCostsBuf: Float64Array = new Float64Array(GRID * GRID)

  constructor(
    world: World,
    params: GodAIParams = DEFAULT_GOD_AI_PARAMS,
    rng?: RNG,
    controlledTank?: (w: World) => Tank | null,
  ) {
    this.world = world
    this.rng = rng ?? world.rng
    this._baseParams = params
    this.params = params
    if (controlledTank) this.controlledTank = controlledTank
  }

  reset(): void {
    this._moveDir = null
    this._fire = false
    this._thought = false
    this.path = []
    this.replanTimer = 0
    this.reactionCounter = 0
    this.lastThreatId = -1
    this._lastPathCell = null
    this._campCell = null
    this._campTicks = 0
    this._campKillsAtStart = 0
    this._antiCampSuppress = 0
    this._navStuckCell = null
    this._navStuckTicks = 0
    this.aggressive = false
    this._enemies = []
    this._otherTanks = []
    // (perf §68 Round 9) Invalidate cross-tick navigateTowards cache on
    // stage reset — the next tick must recompute A* from scratch.
    this._navCacheValid = false
    this._navReplanTimer = 0
    // Gap B (plan §3): cache whether this stage has a base. All BASE_POS-
    // dependent logic checks this flag instead of assuming a base exists.
    this.hasBase = this.world.tileMap.hasBase()
    // §58: compute stage-level adaptive params from the base params. This
    // replaces the per-stage override table with a data-driven adaptation
    // based on stage characteristics (armor ratio, brick density).
    this.params = computeStageAdaptedParams(this._baseParams, this.world)
  }

  getMoveDirection(): Direction | null {
    this.think()
    return this._moveDir
  }

  isFiring(): boolean {
    if (!this._thought) this.think()
    return this._fire
  }

  wasItemPressed(_kind: 'guard' | 'frenzy'): boolean {
    return false
  }

  endFrame(): void {
    this._thought = false
    // Invalidate per-tick lazy caches.
    this._baseUnderThreatCache = null
    this._fastThreatCache = null
    this._playerCellValid = false
    this._canMoveComputed = 0
  }

  // ================================================================
  // Core decision loop (think)
  // ================================================================

  private think(): void {
    if (this._thought) return
    this._thought = true

    const w = this.world
    const p = this.controlledTank(w)
    if (!p || !p.alive || p.spawnTimer > 0) {
      this._moveDir = null
      this._fire = false
      this.branchCounts.dead++
      return
    }

    // ---- Cluster C: per-tick snapshots (built once, reused across modules) ----
    // These mirror the exact filters the god/* sub-modules used to run on every
    // call, in the same iteration order, so no decision (incl. enemies[0]
    // tie-breaks) changes. Pure recomputation elimination, not a behavior change.
    const tanks = w.tanks
    this._enemies.length = 0
    for (let i = 0; i < tanks.length; i++) {
      const t = tanks[i]
      if (t.alive && t.spawnTimer <= 0) this._enemies.push(t)
    }
    const all = w.allTanks
    this._otherTanks.length = 0
    for (let i = 0; i < all.length; i++) {
      const o = all[i]
      if (o.alive) this._otherTanks.push(o)
    }

    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    const now = w.frame * (1000 / 60)

    // P0.1: Decrement anti-camp suppression every tick the player is alive.
    if (this._antiCampSuppress > 0) this._antiCampSuppress--

    // ---- M6: Cooldown-aware firing ----
    // In 'bulletCap' mode (classic FC), the engine gates fire by on-screen
    // bullet count, NOT by a time cooldown. The AI must mirror this:
    // "on cooldown" means the player's bullet is still in flight (cap
    // reached), not that a timer hasn't elapsed. Using the time check here
    // would suppress fire for ~1.3s after each shot even though the engine
    // allows refire the instant the previous bullet resolves — this was the
    // #1 root cause of the AI's abysmal kill count (1-3 kills/game) in classic.
    let onCooldown: boolean
    if (w.rules.fireModel === 'bulletCap') {
      const cap =
        (w.rules.maxBullets['player'] ?? 1) +
        ((p.level ?? 0) >= w.rules.playerDoubleShotLevel ? 1 : 0)
      let inFlight = 0
      const bullets = w.bullets
      for (let bi = 0; bi < bullets.length; bi++) {
        const b = bullets[bi]
        if (b.alive && b.ownerId === p.id) {
          if (++inFlight >= cap) break // early exit — cap reached
        }
      }
      onCooldown = inFlight >= cap
    } else {
      onCooldown = now - p.lastFire < p.nextFireInterval
    }

    // ---- S8: Freeze window — aggressive hunt mode ----
    // When enemies are frozen, the player can hunt freely — enemies can't
    // fight back or approach the base. This is a free-clear window.
    const frozen = w.freezeTimer > 0

    // ---- S9: Shield — skip dodge but DON'T abandon defense ----
    // The 3-second respawn shield makes the player invulnerable, so dodge
    // is unnecessary. But the player must STILL defend the base — chasing
    // enemies across the map during the shield window leaves the base
    // undefended (the #1 cause of 330-tick base losses in classic).
    const shielded = (p.shieldTimer ?? 0) > 0

    // ---- S8: Set aggressive mode (freeze only, NOT shield) ----
    this.aggressive = frozen

    // ---- Scan for enemy targets (global vision, T9 priority) ----
    const aimDir = this.findEnemyDirection(pcx, pcy)

    // ---- Threat assessment (dodge incoming bullets) ----
    // Dodge FIRST: survive before defending the base.
    const threat = shielded ? null : this.findMostDangerousBullet(pcx, pcy)

    if (threat) {
      if (threat.id !== this.lastThreatId) {
        this.lastThreatId = threat.id
        this.reactionCounter = this.params.reactionDelay
      }

      if (this.reactionCounter > 0) {
        this.reactionCounter--
        // While reacting, keep navigating but fire only at targets in facing dir.
        this._moveDir = this.followPath()
        if (!this._moveDir) this._moveDir = this.directMove(this.playerCell())
        this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
        return
      }

      // Dodge: move perpendicular to the bullet (M3: verify safety).
      this._moveDir = this.dodgeDirection(threat, pcx, pcy)
      this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
      this.branchCounts.dodge++
      return
    }

    // No threat — reset reaction state.
    this.reactionCounter = 0
    this.lastThreatId = -1

    // ---- T8: Base bullet interception (ultimate defense) ----
    // Check AFTER dodge (survive first) but BEFORE aggressive/T2a.
    // Skip only when enemies are frozen (aggressive hunt — no bullets to
    // intercept). When shielded, the player can still intercept bullets
    // headed for the base — the shield protects the player, not the base.
    // Gap B (plan §3): skip entirely when the stage has no base.
    if (!this.aggressive && this.hasBase) {
      const baseThreat = this.findBulletThreatToBase()
      if (baseThreat) {
        const interceptCell = this.baseBulletInterceptCell(baseThreat)
        if (interceptCell) {
          this._moveDir = this.navigateTowards(interceptCell)
          // Fire to intercept the bullet (T5 extended to base defense).
          this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
          this.branchCounts.t8++
          return
        }
      }
    }

    // ---- S8/S9: Aggressive mode (freeze or shield) ----
    if (this.aggressive) {
      // Skip defense, go straight for the nearest enemy or power-up.
      //
      // §80: `aimSurvivesTurnImpl` MUST be evaluated BEFORE `scanAheadImpl`
      // below — both write into the shared `this._scanResult`, so running the
      // guard afterwards would clobber `aggScan`. The `&&` short-circuit gives
      // us that ordering for free. When the guard rejects the aim (the turn's
      // grid-snap would shove the tank off the firing line) we fall through to
      // the navigate path, which has real stall detection — this is what
      // breaks the period-2 freeze-window deadlock.
      if (aimDir && aimSurvivesTurnImpl(this, p, aimDir)) {
        // T2a: stop-and-aim — check if enemy is visible (no steel blocking).
        // Without this check, the AI fires through steel walls at enemies
        // it can see via global vision but cannot actually hit.
        // Inline scanAheadImpl directly (perf §66): the thin scanAhead
        // wrapper adds ~14ms (2.8%) of function-call overhead across 30 games.
        // V8 does not inline it because scanAheadImpl is large (100+ lines).
        const aggScan = scanAheadImpl(this, pcx, pcy, aimDir)
        // §74: Don't fire when a base-protection wall is on the other offset
        // line — the bullet travels from the player center (one of the two
        // offset columns) and would hit the base wall, not the enemy.
        // §74: Don't fire when a base-protection wall is closer than (or at
        // the same distance as) the enemy on the other offset line. The
        // 6px bullet spans both offset columns, so it WILL hit a closer base
        // wall before reaching the enemy. But if the enemy is closer, the
        // bullet hits the enemy first — firing is safe.
        if (
          aggScan.enemy &&
          !(aggScan.baseWall && aggScan.baseWallDist <= aggScan.enemyDist) &&
          !(aggScan.baseSteel && (p.level ?? 0) >= 3)
        ) {
          if (p.dir === aimDir) {
            this._moveDir = null
          } else {
            this._moveDir = aimDir
          }
          this._fire = !onCooldown && this.rng.next() >= this.params.aimError
          return
        }
        // Enemy behind obstacle — fall through to navigate toward it.
      }
      // No enemy in row/col — check for power-up (S5).
      const puTarget = this.findPowerUpTarget(pcx, pcy)
      if (puTarget) {
        this._moveDir = this.navigateTowards(puTarget)
        this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
        return
      }
      // Navigate to nearest enemy.
      this._moveDir = this.followPath()
      if (!this._moveDir) this._moveDir = this.directMove(this.playerCell())
      // Proactive fire — but ALWAYS check shouldFireInDir to avoid shooting
      // the player's own base (T6). In classic instant combat the base has
      // 1 HP, so a single self-inflicted bullet destroys it.
      if (this._moveDir && !this.canMoveDir(p, this._moveDir)) {
        // §70/§74: break-through fire — never fire through base brick/steel
        // (§70) or at steel the player can't pierce (§74). Both guards live
        // in shouldFireBreakThroughImpl, which also drops the old `bs.enemy ||
        // ...` short-circuit that fired through the base wall on dual-offset
        // scans (DECISIONS §75 / commit 54600f9 — 4 S32 player suicides).
        const bs = scanAheadImpl(this, pcx, pcy, this._moveDir)
        const lvl = p.level ?? 0
        if (shouldFireBreakThroughImpl(bs, lvl, this.params.steelFireGate)) {
          this._fire = !onCooldown
        }
      } else {
        this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
      }
      this.branchCounts.aggressive++
      return
    }

    // ---- T2a: Stop-and-aim (enemy in same row/col) ----
    // P0.2: Only camp when there's a REAL enemy in the line of fire
    // (scan.enemy == true). The old code also camped when there was just a
    // wall (scan.wall && !scan.baseWall), which caused the T2a deadlock:
    // the player would stop and fire at a wall endlessly, never advancing.
    // Now the player only stops to aim when there's an actual enemy to shoot.
    // When the enemy is behind a wall, the player falls through to navigate,
    // which moves toward the enemy and breaks walls via directMove/canMoveOrBreak.
    //
    // P0.1: Anti-camp escape — track how long the player has been at the
    // same cell in T2a. If camping exceeds campTimeoutTicks with no kills,
    // fall through to navigate and hunt the enemy directly. A suppression
    // timer (antiCampSuppressTicks) ensures the player gets enough
    // consecutive navigate ticks to actually move away from the stuck cell.
    //
    // P1: Skip T2a when the base is under threat and the player is too far
    // from the base. Camping far from the base while enemies approach it
    // was the #1 cause of base_destroyed gameovers.
    //
    // D1 (guard band mode): when enabled, skip T2a on fast/power tank
    // threats near the base ONLY (not armor — armor is slow and can wait).
    // This is the targeted version of the guard band: the player camps at
    // armor for efficient point-blank kills, but the instant a fast tank
    // approaches the base, it disengages to intercept. The previous
    // untargeted version (any base threat) was too aggressive and caused
    // the player to disengage from armor too often, increasing deaths.
    const fastThreat = this.params.guardBandMode > 0 && this.hasFastThreatNearBase()

    const skipT2aForDefense =
      this.hasBase &&
      (fastThreat ||
        (this.isBaseUnderThreat() &&
          Math.abs(this.playerCell().col - BASE_POS.col) +
            Math.abs(this.playerCell().row - BASE_POS.row) >
            this.params.maxPlayerDistFromBase))

    if (aimDir && this._antiCampSuppress <= 0 && !skipT2aForDefense) {
      // Inline scanAheadImpl (perf §66, see aggressive branch above).
      const scan = scanAheadImpl(this, pcx, pcy, aimDir)

      // §74: Don't enter T2a when a base-protection wall is closer than
      // (or at the same distance as) the enemy on the other offset line.
      // The 6px bullet spans both offset columns. If the base wall is
      // closer, the bullet hits it before the enemy → suicide. If the
      // enemy is closer, the bullet hits the enemy first → safe to fire.
      // Fall through to navigate when blocked by a closer base wall.
      if (
        scan.enemy &&
        !(scan.baseWall && scan.baseWallDist <= scan.enemyDist) &&
        !(scan.baseSteel && (p.level ?? 0) >= 3)
      ) {
        // §56: dynamic T2a range based on enemy kind.
        // For non-armor enemies (basic/fast/power): use t2aMaxRange (15) —
        // one shot kills at any distance, no DPS penalty for range.
        // For armor (4 hitsToKill): use t2aHighHpMaxRange (2) — close combat.
        // At long range, 4 shots × 1s travel = 4s of camping; at point-blank,
        // 4 shots in <0.5s. The approach time is always worth it for 4-HP armor.
        // Note: in the instant combat model, maxHp = hitsToKill × referenceDamage,
        // so ALL enemies have maxHp >= 100. The kind check is the correct way
        // to identify armor (4 hitsToKill) vs basic/fast/power (1 hitsToKill).
        const effectiveRange =
          scan.enemyKind === 'armor' ? this.params.t2aHighHpMaxRange : this.params.t2aMaxRange
        if (scan.enemyDist <= effectiveRange) {
          // Track camping duration in a ZONE (±1 cell), not exact cell.
          // P2.1fix: the old exact-cell check was defeated by sub-cell
          // oscillation — the player bounces between two adjacent cells
          // (e.g., x=32→40→32) at the TANK/CELL boundary, resetting the
          // camp cell each time the boundary is crossed. This prevented
          // the anti-camp escape from EVER firing, causing the Stage 3/4
          // deadlocks (player stuck at one spot for 17000+ ticks). The
          // zone fix accumulates camp time across nearby cells, so the
          // escape triggers even if the player wiggles between two cells.
          const pc = this.playerCell()
          if (
            this._campCell &&
            Math.abs(this._campCell.col - pc.col) <= 1 &&
            Math.abs(this._campCell.row - pc.row) <= 1
          ) {
            this._campTicks++
            // If a kill happened since camping started, reset the camp timer.
            // The player is being productive — let it continue camping.
            if (w.killCount !== this._campKillsAtStart) {
              this._campTicks = 1
              this._campKillsAtStart = w.killCount
            }
          } else {
            // Moved outside the camp zone — start fresh camp tracking.
            this._campCell = { col: pc.col, row: pc.row }
            this._campTicks = 1
            this._campKillsAtStart = w.killCount
          }

          // Anti-camp: if too long at this cell with no kills, break out.
          const campedTooLong =
            this._campTicks > this.params.campTimeoutTicks && w.killCount === this._campKillsAtStart

          if (!campedTooLong) {
            // ---- §49: 炮口相向分场景策略 ----
            // 当敌人面向 player 时，根据敌人类型采取不同策略：
            //   - 冰面：跳过（垂直移动在冰面上失控）
            //   - 1HP 敌人：正常 T2a 开火（一枪击毙），但对枪抵消仍然生效
            //     （对枪是开火行为，不是移动闪避——与"1HP 不闪避"不矛盾）
            //   - Armor（多血）：对枪抵消 + 保持对齐等待
            //
            // 对枪抵消对所有敌人类型都适用：当敌方子弹已在直线上时，
            // 开火抵消比打死敌人更安全（子弹被消除→玩家安全）。
            // 120-seed 验证：对枪对 ALL 敌人 +5 wins，仅 armor +1 win。
            // §49-revisit: 炮口相向对枪抵消 is parameterized for A/B.
            // counterFire=0 → facing stays null → plain T2a (pre-§52 form).
            const facing =
              this.params.counterFire > 0 ? this.findEnemyFacingPlayer(pcx, pcy, aimDir) : null
            const onIce = w.isTankOnIce(p)

            if (facing && !onIce && facing.dist <= this.params.counterFireMaxRange * CELL) {
              // ---- 对枪抵消逻辑（适用于所有敌人类型）----
              const enemyBulletInLine = this.hasEnemyBulletInLine(pcx, pcy, aimDir)

              if (enemyBulletInLine && !onCooldown) {
                // 对枪：敌方子弹已在直线上 → 开火抵消
                if (p.dir === aimDir) {
                  this._moveDir = null
                } else {
                  this._moveDir = aimDir
                }
                this._fire = true
                this.branchCounts.t2a++
                return
              }

              // 先手开火 / 冷却中等待：保持对齐以备对枪
              // 不横移——横移会脱离防守位，在密集关卡导致更多死亡
              if (p.dir === aimDir) {
                this._moveDir = null
              } else {
                this._moveDir = aimDir
              }
              this._fire = !onCooldown && this.rng.next() >= this.params.aimError
              this.branchCounts.t2a++
              return
            }

            // ---- 正常 T2a（非炮口相向 / 1HP / 冰面）----
            if (p.dir === aimDir) {
              this._moveDir = null // Already facing — stop and shoot
            } else {
              this._moveDir = aimDir // Turn to face enemy
            }
            this._fire = !onCooldown && this.rng.next() >= this.params.aimError
            this.branchCounts.t2a++
            return
          }

          // Camped too long with no kills — suppress T2a and fall through
          // to navigate, which will move the player toward the enemy.
          this._campCell = null
          this._campTicks = 0
          this._antiCampSuppress = this.params.antiCampSuppressTicks
        }
        // Enemy in line of fire but beyond effective range — fall through
        // to navigate (close the distance for high-HP enemies).
      }
      // No real enemy in line of fire (wall-only or clear) — fall through.
    } else if (this._campCell) {
      // Not in T2a (suppressed or no aimDir) — reset camp tracking.
      this._campCell = null
      this._campTicks = 0
    }

    // ---- S5: Power-up economy (normal mode) ----
    // Check for power-ups when no enemy is in line of fire. Previously this
    // only ran in aggressive mode (freeze/shield), wasting bomb/star pickups.
    // Now the AI opportunistically grabs power-ups when it's safe to divert.
    // P1: Skip power-ups when the base is under threat — defense first.
    // P3.2: Also skip when there are enemies within 5 cells of the player —
    // chasing power-ups while enemies are nearby was a major cause of
    // defense-collapse gameovers on S6/S26/S32 (player diverted to a power-up
    // at the top of the map while enemies destroyed the base).
    if ((!aimDir || onCooldown) && !(this.hasBase && this.isBaseUnderThreat())) {
      // P3.2: Don't divert to power-ups when enemies are close.
      const pc2 = this.playerCell()
      let nearbyEnemy = false
      // Cluster C: reuse the per-tick enemy snapshot.
      const nearbyScan = this._enemies.length > 0 ? this._enemies : w.tanks
      for (let ni = 0; ni < nearbyScan.length; ni++) {
        const t = nearbyScan[ni]
        if (!t.alive || t.spawnTimer > 0) continue
        const tc = this.tankCell(t)
        if (Math.abs(tc.col - pc2.col) + Math.abs(tc.row - pc2.row) <= 5) {
          nearbyEnemy = true
          break
        }
      }
      if (!nearbyEnemy) {
        const puTarget = this.findPowerUpTarget(pcx, pcy)
        if (puTarget) {
          this._moveDir = this.navigateTowards(puTarget)
          this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
          this.branchCounts.powerup++
          return
        }
      }
    }

    // ---- T2b: Navigate towards target (distance-adaptive) ----
    // Far from target (>5 cells): A* pathfinding routes around walls via
    // corridors — essential for maze stages. A* finds the corridor, not the
    // direct path through walls.
    //
    // Close to target (≤5 cells): directMove chases the moving enemy
    // directly, adjusting every tick. A* paths go stale before the player
    // arrives (the enemy moves away), causing the player to chase the
    // enemy's old position — directMove tracks the enemy's current position.
    //
    // When A* can't find a path (target walled off), directMove breaks
    // through brick walls by firing at them.
    //
    // P0.3: Navigate stuck escape — if the player has been at the same cell
    // in the navigate branch for too long (pursuit loop with a faster enemy),
    // override the target to the map center. This breaks the loop by moving
    // the player to a crossroads position where enemies are more likely to
    // cross its row/col, creating new T2a opportunities.
    //
    // Fire control in classic bulletCap mode (1 bullet in flight):
    // - If blocked by a wall in the path direction → fire to break through.
    // - If moving freely → fire only at enemies in the line of fire.
    const pc = this.playerCell()
    if (
      this._navStuckCell &&
      this._navStuckCell.col === pc.col &&
      this._navStuckCell.row === pc.row
    ) {
      this._navStuckTicks++
    } else {
      this._navStuckCell = { col: pc.col, row: pc.row }
      this._navStuckTicks = 1
    }

    // Reset stuck timer when a kill happens (player is making progress).
    if (this._navStuckTicks > 1 && w.killCount !== this._campKillsAtStart) {
      this._navStuckTicks = 1
      this._campKillsAtStart = w.killCount
    }

    const navStuck = this._navStuckTicks > this.params.navStuckTicks

    let navTarget: Cell | null
    // P3.1: When nav-stuck triggers, only go to center if the player is
    // NOT already at/near center. Going to center when already there causes
    // a deadlock (target == current cell → no movement → stuck forever,
    // which was the S9 root cause: player stuck at (12,12) for 2600+ ticks).
    // When the player IS at/near center, chase the nearest enemy directly
    // (directMove breaks through walls) — this gets the player moving.
    const distToCenter = Math.abs(pc.col - 12) + Math.abs(pc.row - 12)
    const stuckAtCenter = distToCenter <= 2
    if (navStuck && !stuckAtCenter) {
      navTarget = { col: 12, row: 12 }
    } else {
      navTarget = this.selectTarget(pc)
    }

    const navDist = navTarget
      ? Math.abs(navTarget.col - pc.col) + Math.abs(navTarget.row - pc.row)
      : Infinity

    if (navStuck && !stuckAtCenter) {
      // P2.2: Stuck too long — break the loop. Try A* to center first, then
      // fall back to any passable direction (not directMove, which would
      // re-select the enemy target and re-enter the stuck loop). Trying any
      // open direction ensures the player physically moves away from the
      // stuck cell, which is the whole point of the escape.
      this._moveDir = this.navigateTowards(navTarget!)
      if (!this._moveDir) {
        // A* failed (walled off) — try directions toward center first,
        // then any passable direction.
        const dx = navTarget!.col - pc.col
        const dy = navTarget!.row - pc.row
        const pref: Direction[] = []
        if (Math.abs(dy) > Math.abs(dx)) {
          pref.push(dy > 0 ? 'down' : 'up')
          pref.push(dx > 0 ? 'right' : 'left')
        } else {
          pref.push(dx > 0 ? 'right' : 'left')
          pref.push(dy > 0 ? 'down' : 'up')
        }
        let moved = false
        for (const d of pref) {
          if (this.canMoveDir(p, d)) {
            this._moveDir = d
            moved = true
            break
          }
        }
        if (!moved) {
          // All preferred directions blocked — try any open direction.
          for (const d of ALL_DIRS) {
            if (this.canMoveDir(p, d)) {
              this._moveDir = d
              break
            }
          }
        }
      }
    } else if (navStuck && stuckAtCenter) {
      // P3.1: Stuck at/near center — chase nearest enemy directly instead
      // of re-targeting center. directMove breaks through brick walls,
      // which (combined with the A* dig-through-brick fix) gets the player
      // moving toward enemies instead of deadlocking at center.
      this._moveDir = this.directMove(pc)
      if (!this._moveDir) {
        // directMove also failed — try any passable direction to get moving.
        for (const d of ALL_DIRS) {
          if (this.canMoveDir(p, d)) {
            this._moveDir = d
            break
          }
        }
      }
    } else if (navDist <= 5) {
      // Close range — directMove (responsive, tracks moving enemies).
      this._moveDir = this.directMove(pc)
    } else {
      // Long range — A* pathfinding (finds corridors in mazes).
      this._moveDir = this.followPath()
      if (!this._moveDir) {
        // A* failed or path exhausted — fall back to direct movement.
        this._moveDir = this.directMove(pc)
      }
    }
    // §68-v2: Path threat check — don't move into crossfire.
    // After navigation determines _moveDir, check if the path ahead has
    // bullets that would arrive at any cell before the player clears it.
    // If threatened: try alternative directions (perpendicular first,
    // then backward) or stay put. Does NOT do a perpendicular dodge —
    // the player either detours or waits, avoiding navigation oscillation.
    // Only runs in the navigate branch (T2b); T8/T2a/aggressive are exempt.
    if (!shielded && this.params.crossfireAwareness > 0 && this._moveDir && p.speed > 0.1) {
      const pathThreat = this.findPathThreat(pcx, pcy, this._moveDir, p.speed)
      if (pathThreat) {
        // Try to find a safe alternative direction. If none found,
        // KEEP the original direction — don't stop! Stopping in a crossfire
        // is more dangerous than continuing; the existing dodge system
        // (findMostDangerousBullet) will handle the bullet when it arrives.
        const safeDir = this.findSafeMoveDir(pcx, pcy, this._moveDir, p.speed)
        if (safeDir) {
          this._moveDir = safeDir
        }
      }
    }
    // §48-revisit: Trap avoidance (user idea 2). After navigation determines
    // _moveDir, check the NEXT cell for a surround risk (few exits + enemies
    // nearby). If it's a trap, override toward open space / the base. Runs at
    // the END of navigation, so it only perturbs the final move — dodge/T8/T2a
    // priorities are intact (same placement discipline as §68-v2 above).
    if (!shielded && this.params.trapAvoidance > 0 && this._moveDir) {
      this._moveDir = this.trapAvoidance(p, this._moveDir)
    }
    // Fire control: when blocked by a breakable wall (verified by
    // canMoveOrBreak in directMove), fire immediately to break through.
    // Don't check shouldFireInDir here — it might fire at enemy bullets
    // (T5) instead of the wall, leaving the player stuck. When moving
    // freely, fire only at enemies (not walls) to save the bullet cap.
    if (this._moveDir && !this.canMoveDir(p, this._moveDir)) {
      // §70/§74: break-through fire — never fire through base brick/steel
      // (§70) or at steel the player can't pierce (§74). Both guards live
      // in shouldFireBreakThroughImpl, which also drops the old `bs.enemy ||
      // ...` short-circuit that fired through the base wall on dual-offset
      // scans (DECISIONS §75 / commit 54600f9 — 4 S32 player suicides).
      const bs = scanAheadImpl(this, pcx, pcy, this._moveDir)
      const lvl = p.level ?? 0
      if (shouldFireBreakThroughImpl(bs, lvl, this.params.steelFireGate)) {
        this._fire = !onCooldown
      }
    } else {
      this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir, false)
    }
    this.branchCounts.navigate++
  }

  // ================================================================
  // Delegating wrappers — the decision logic lives in ./god/* (§0.5 split).
  // Each wrapper is a thin pass-through to the matching <name>Impl(self, ...).
  // `think()` and all external callers keep using the same method names.
  // ================================================================

  // --- FireControl ---
  findEnemyDirection(pcx: number, pcy: number): Direction | null {
    return findEnemyDirectionImpl(this, pcx, pcy)
  }
  findEnemyFacingPlayer(
    pcx: number,
    pcy: number,
    aimDir: Direction,
  ): { enemy: Tank; dist: number } | null {
    return findEnemyFacingPlayerImpl(this, pcx, pcy, aimDir)
  }

  /**
   * P1/P2.3: Check if any enemy is threatening the base. An enemy is a
   * threat if:
   *   - Within 3 cols of base AND row >= 18 (close lateral threat), OR
   * Used to skip power-ups/T2a and prioritize defense.
   */
  isBaseUnderThreat(): boolean {
    if (!this.hasBase) return false
    // Per-tick cache: called up to 3× per tick (think skipT2a, think powerup
    // gate, selectTarget). Pure function of World state — byte-identical.
    if (this._baseUnderThreatCache !== null) return this._baseUnderThreatCache
    const bc = BASE_POS.col
    const br = BASE_POS.row
    // P4: race-to-base check — player's distance to the base. If the player
    // is dead/respawning, treat any near-base enemy as a threat.
    const p = this.controlledTank(this.world)
    const pc = p ? this.playerCell() : null
    const playerDistToBase = pc ? Math.abs(pc.col - bc) + Math.abs(pc.row - br) : Infinity
    // Cluster C: reuse the per-tick snapshot (falls back to a fresh scan only
    // if think() hasn't populated it yet — should never happen in normal flow).
    const list = this._enemies.length > 0 ? this._enemies : this.world.tanks
    let result = false
    for (let li = 0; li < list.length; li++) {
      const t = list[li]
      if (!t.alive || t.spawnTimer > 0) continue
      const tc = this.tankCell(t)
      // Static box: close lateral threat (original P1/P2.3 rule).
      if (Math.abs(tc.col - bc) <= 3 && tc.row >= 18) {
        result = true
        break
      }
      // P4: race check — enemy is in the base region AND would beat the
      // player back to the base (with safety margin). Catches flanking
      // runners along the map edges that the static box misses (S6 root
      // cause: base died with the player 20+ cells away behind steel).
      const enemyDistToBase = Math.abs(tc.col - bc) + Math.abs(tc.row - br)
      if (
        enemyDistToBase <= this.params.baseRaceRangeCells &&
        playerDistToBase + this.params.baseRaceMarginCells >= enemyDistToBase
      ) {
        result = true
        break
      }
    }
    this._baseUnderThreatCache = result
    return result
  }

  /**
   * D1: Check if any fast/power tank is near the base (within the existing
   * threat detection zone). Used by the T2a skip to decide whether the
   * player should disengage from armor camping. Only checks fast/power
   * kinds — armor tanks are slow and don't require immediate disengagement.
   */
  hasFastThreatNearBase(): boolean {
    if (!this.hasBase) return false
    // Per-tick cache: called once per tick from think() (only when
    // guardBandMode > 0). Pure function of World state — byte-identical.
    if (this._fastThreatCache !== null) return this._fastThreatCache
    const bc = BASE_POS.col
    const br = BASE_POS.row
    const list = this._enemies.length > 0 ? this._enemies : this.world.tanks
    let result = false
    for (let li = 0; li < list.length; li++) {
      const t = list[li]
      if (!t.alive || t.spawnTimer > 0) continue
      if (t.kind !== 'fast' && t.kind !== 'power') continue
      const tc = this.tankCell(t)
      // Static box: within 3 cols of base AND row >= 18
      if (Math.abs(tc.col - bc) <= 3 && tc.row >= 18) {
        result = true
        break
      }
      // Race check: within baseRaceRangeCells of base
      const enemyDist = Math.abs(tc.col - bc) + Math.abs(tc.row - br)
      if (enemyDist <= this.params.baseRaceRangeCells) {
        result = true
        break
      }
    }
    this._fastThreatCache = result
    return result
  }

  // --- SmartThreatModel (Phase A, plan/God-AI-Next-Round §3) ---
  /** Threat score for an enemy [0..1]. Higher = more dangerous to base. */
  threatScore(t: Tank): number {
    return threatScoreImpl(this, t)
  }
  /** Smart isBaseUnderThreat: any enemy with threatScore ≥ threshold. */
  smartIsBaseUnderThreat(): boolean {
    return smartIsBaseUnderThreatImpl(this)
  }

  scanAhead(
    pcx: number,
    pcy: number,
    dir: Direction,
  ): {
    enemy: boolean
    wall: boolean
    steel: boolean
    baseWall: boolean
    enemyDist: number
    enemyKind: TankKind
    enemyHp: number
    enemyMaxHp: number
  } {
    return scanAheadImpl(this, pcx, pcy, dir)
  }
  isBaseProtectionBrick(col: number, row: number): boolean {
    return isBaseProtectionBrickImpl(this, col, row)
  }
  shouldFireInDir(pcx: number, pcy: number, dir: Direction, allowWallFire = true): boolean {
    return shouldFireInDirImpl(this, pcx, pcy, dir, allowWallFire)
  }

  // --- ThreatAssessor ---
  findMostDangerousBullet(pcx: number, pcy: number): Bullet | null {
    return findMostDangerousBulletImpl(this, pcx, pcy)
  }
  findBulletThreatToBase(): Bullet | null {
    return findBulletThreatToBaseImpl(this)
  }
  baseBulletInterceptCell(bullet: Bullet): Cell | null {
    return baseBulletInterceptCellImpl(this, bullet)
  }
  dodgeDirection(bullet: Bullet, pcx: number, pcy: number): Direction | null {
    return dodgeDirectionImpl(this, bullet, pcx, pcy)
  }
  isSafeDir(pcx: number, pcy: number, dir: Direction, excludeBulletId: number): boolean {
    return isSafeDirImpl(this, pcx, pcy, dir, excludeBulletId)
  }
  hasEnemyBulletInLine(pcx: number, pcy: number, aimDir: Direction): boolean {
    return hasEnemyBulletInLineImpl(this, pcx, pcy, aimDir)
  }
  findPathThreat(pcx: number, pcy: number, moveDir: Direction, playerSpeed: number): Bullet | null {
    return findPathThreatImpl(this, pcx, pcy, moveDir, playerSpeed)
  }
  findSafeMoveDir(
    pcx: number,
    pcy: number,
    threatenedDir: Direction,
    playerSpeed: number,
  ): Direction | null {
    return findSafeMoveDirImpl(this, pcx, pcy, threatenedDir, playerSpeed)
  }

  // --- StrategyPlanner ---
  findPowerUpTarget(pcx: number, pcy: number): Cell | null {
    return findPowerUpTargetImpl(this, pcx, pcy)
  }
  calculateRouteDanger(fromX: number, fromY: number, toX: number, toY: number): number {
    return calculateRouteDangerImpl(this, fromX, fromY, toX, toY)
  }
  getDefaultDefensePosition(): Cell {
    return getDefaultDefensePositionImpl(this)
  }
  selectTarget(playerCell: Cell): Cell | null {
    return selectTargetImpl(this, playerCell)
  }

  // --- Navigator ---
  playerCell(): Cell {
    return playerCellImpl(this)
  }
  tankCell(t: Tank): Cell {
    return tankCellImpl(this, t)
  }
  navigateTowards(target: Cell): Direction | null {
    return navigateTowardsImpl(this, target)
  }
  followPath(): Direction | null {
    return followPathImpl(this)
  }
  replan(playerCell: Cell): void {
    replanImpl(this, playerCell)
  }
  directMove(playerCell: Cell): Direction | null {
    return directMoveImpl(this, playerCell)
  }
  canMoveOrBreak(tank: Tank, dir: Direction): boolean {
    return canMoveOrBreakImpl(this, tank, dir)
  }
  canMoveDir(tank: Tank, dir: Direction): boolean {
    return canMoveDirImpl(this, tank, dir)
  }
  trapAvoidance(tank: Tank, moveDir: Direction): Direction {
    return trapAvoidanceImpl(this, tank, moveDir)
  }
  computeThreatCosts(fromCell: Cell, playerSpeed: number): Float64Array | undefined {
    return computeThreatCostsImpl(this, fromCell, playerSpeed)
  }
}
