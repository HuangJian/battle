// Moved verbatim from GodAIInput.ts during the giant-file split — params/
// config surface (GodAIParams + DEFAULT/SKILLED_HUMAN + stage adaptation).
import type { World } from '../../game/World'
import { GRID } from '../../constants'

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

  // ---- §58: Stage-level adaptive params (Strategy G, data-driven adaptation) ----
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

  /**
   * §84: Aggressive branch stall detection. When > 0, the aggressive
   * (freeze/shield) stop-and-aim code tracks how long the player has been
   * stopped at the same cell with no kills. If the player camps for more
   * than `aggCampTimeoutTicks` ticks without a kill, the AI falls through
   * to navigate (which repositions the player toward the enemy).
   *
   * Root cause (replay classic-s03-…-seed1785643123096, 0:20–0:36):
   * the aggressive branch has NO anti-stall guard (unlike T2a which has
   * `_campTicks` and navigate which has `_navStuckTicks`). When the player
   * stops to aim at an enemy but the bullet keeps missing (enemy slightly
   * offset in the perpendicular axis, so the 6px bullet passes above/below
   * the 32px tank), the player stays put firing at nothing for the ENTIRE
   * freeze window — measured at 1080+ ticks (18 seconds) in the replay.
   *
   * 0 = OFF (byte-identical to pre-§84 behavior). 120 = 2 seconds (default).
   */
  aggCampTimeoutTicks: number

  /**
   * §85: Close-range enemy exposure check in navigate. When > 0, the
   * navigate branch checks if the player's _moveDir would expose the
   * player to a close-range enemy's line of fire. If an enemy is within
   * `closeCombatDangerRange` cells, aligned with the player (same row/col),
   * has no wall between them, and the player's moveDir is NOT toward that
   * enemy, the move is cancelled — the player stops and fires at the
   * enemy instead (or turns to face it).
   *
   * Root cause (replay classic-s03-…-seed1785643123096, 1:03): the player
   * was in close combat with an enemy, turned away (moveDir = away from
   * enemy), and was killed by the enemy's bullet before it could dodge.
   * The navigate branch only checks for BULLET threats (findPathThreat),
   * not for enemy tanks that could fire. This check adds enemy-tank
   * threat assessment: "don't turn your back on a close enemy."
   *
   * 0 = OFF (byte-identical to pre-§85). 1 = ON (default).
   */
  closeCombatDangerCheck: number
  /** §85: max distance (cells) for the close-range enemy exposure check. */
  closeCombatDangerRange: number

  /**
   * §86: Threat hysteresis — `findMostDangerousBulletImpl` uses TANK+2
   * alignment threshold for the recently-dodged bullet. Prevents boundary
   * flickering at |dist|=32. 0 = OFF and is the SHIPPED default
   * (byte-identical to pre-§86). 1 = ON (experimental, A/B-only — never in
   * shipped default).
   */
  dodgeHysteresis: number

  /**
   * §86: Dodge direction persistence — `dodgeDirectionImpl` returns the last
   * dodge direction if the same threat persists. Prevents 1px oscillation.
   * 0 = OFF and is the SHIPPED default (byte-identical). 1 = ON
   * (experimental, A/B-only — never in shipped default).
   */
  dodgeDirPersistence: number

  /**
   * §86: Oscillation detection + counter-fire. When the dodge direction
   * flips 3+ consecutive times for the same threat (oscillation caused by
   * snap() Math.round discontinuity), face the bullet and fire to cancel it
   * (对枪抵消). 0 = OFF (A/B baseline). 1 = ON and is the SHIPPED default —
   * the only §86 param enabled in production. The simulation-layer turn
   * cooldown (§86c) is the canonical fix, so this AI-layer counter-fire is a
   * rare-boundary fallback (C-B = +0.1pp per A/B/C).
   */
  dodgeOscillationCounterFire: number

  /**
   * §86: Use `Math.floor` instead of `Math.round` for the snap in
   * `canMoveDirRaw`. The `snap()` function uses `Math.round(v / CELL) * CELL`,
   * which has a discontinuity at cell midpoints (e.g., y=56 → snap=64, but
   * y=55 → snap=48). This 16px jump flips `canMoveDir` results, causing the
   * dodge direction to oscillate every tick. With `Math.floor`, the snap is
   * stable across 1px differences (y=55 and y=56 both → 48). 0 = OFF and is
   * the SHIPPED default (byte-identical). 1 = ON (experimental, A/B-only —
   * never in shipped default; REJECTED).
   */
  canMoveDirFloorSnap: number
}

/** Default God AI parameters — optimized via CMA-ES P4 round 7 (2026-07-29).
 * See .workbuddy/optimization-p4-r7/ for details.
 *
 * P4 R7 CMA-ES used the floor-aware v5.0 fitness over ALL 35 classic
 * stages × 20 seeds (18000 ticks), warm-started from R6, with the
 * then-active per-stage override table in the inner loop — the
 * optimizer pushed the global mean while the overrides guarded the
 * per-stage floor.
 *
 * P4 R7 truth-scale results (35 stages × 60 seeds, classic, 18000 ticks):
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
  // byte-identical when OFF. Only consumers passing an explicit
  // smartThreatModel > 0 activate the smart scoring (no per-stage
  // override table exists anymore — DECISIONS §81).
  smartThreatModel: 0,
  smartThreatThreshold: 0.55,
  smartThreatSpeedWeight: 0.6,
  smartThreatFacingWeight: 0.2,
  smartThreatHpWeight: 0.2,
  smartThreatDistRange: 12,
  smartRushDetectBonus: 4,

  // §58: Stage-level adaptive params (Strategy G). These generalize the old
  // per-stage override mechanism into a data-driven adaptation based on
  // stage characteristics computed in reset(). Default ON
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
  // §84: Aggressive stall detection — 120 ticks (2s). 0 = OFF (byte-identical).
  aggCampTimeoutTicks: 120,
  // §85: Close-range enemy exposure check — 1 = ON (default). 0 = OFF.
  closeCombatDangerCheck: 1,
  // §85: max distance (cells) for the exposure check. Default 2 (point-blank)
  // — at range 4 the check was too aggressive, causing -1.6pp regression by
  // cancelling legitimate navigation. At range 2, the check only fires when
  // the enemy is truly adjacent (32px), where fleeing is almost certainly death.
  closeCombatDangerRange: 2,
  // ── §86 oscillation-experiment params (A/B-only knobs) ──────────────
  // Evaluated for the §86 dodge-oscillation fix. Only `dodgeOscillationCounterFire`
  // ships ON. The other three are A/B-only and are NEVER part of the shipped
  // default (intentionally left OFF — see interface docs). The canonical fix
  // is the simulation-layer turn cooldown (§86c), not these AI-layer patches.
  // §86: Threat hysteresis — 0 = OFF (A/B showed -1.1pp net regression).
  // A/B-only: not in shipped default.
  dodgeHysteresis: 0,
  // §86: Dodge direction persistence — 0 = OFF (A/B showed -0.6pp net).
  // A/B-only: not in shipped default.
  dodgeDirPersistence: 0,
  // §86: Oscillation detection + counter-fire — 1 = ON (shipped fallback).
  // 0 = OFF (A/B baseline). Simulation-layer cooldown is canonical; this is a
  // rare-boundary fallback (C-B = +0.1pp per A/B/C).
  dodgeOscillationCounterFire: 1,
  // §86: canMoveDirFloorSnap — 0 = OFF (causes -2.6pp at 35×60, S6 -21.7pp).
  // Math.floor in canMoveDirRaw breaks ALL navigation predictions, not just
  // dodging. REJECTED. A/B-only: never in shipped default.
  canMoveDirFloorSnap: 0,
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
 * characteristics — the unified, data-driven replacement for the former
 * per-stage override table (removed, DECISIONS §81: stage-name
 * special-casing is forbidden to prevent overfitting).
 *
 * Multiple adaptations are applied (all data-driven, each OFF when its
 * threshold param is 0):
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
