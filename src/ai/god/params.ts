// Moved verbatim from GodAIInput.ts during the giant-file split — params/
// config surface (GodAIParams + DEFAULT/SKILLED_HUMAN + stage adaptation).
import type { World } from '../../game/World'
import { GRID } from '../../constants'
import type { ActionId } from './DecisionCore'

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
  /**
   * §134 / 方向 D (defense-position lane intercept): 0 = OFF (byte-identical).
   * 1 = ON: when the player is within `defenseInterceptMaxDist` of the base
   * (i.e. at/near the defense position) and a live enemy is ALREADY aligned
   * with the base — `enemyCanShootBase`, it can destroy the base with its
   * next bullet — AND aligned with the player (same row/col, clear LOS), the
   * player stops and fires at that enemy WITHOUT leaving the defense
   * position (outranked by dodge/interceptBase/aggro; weight 550 > engage 500
   * so it pre-empts the aimDir-based T2a which may be aiming elsewhere).
   *
   * Why (hard 35×120 forensics, §131-§133): three prior directions failed on
   * Battlement (base lost, 59% fast killers) — T8 intercept (bullet already
   * in flight), target re-rank (fast 4.5cps outruns the 1★ player), defense
   * distance tightening (returning early = chasing kills for the enemy). The
   * survivor is to shoot the enemy ON the base's firing lane from the defense
   * position — the base's own defenders stop the rush before it fires.
   */
  defenseInterceptMode: number
  /** §134: max player dist-to-base (cells) for the lane-intercept to apply. */
  defenseInterceptMaxDist: number
  /** §134: max player→enemy distance (cells) for the intercept shot. */
  defenseInterceptRangeCells: number
  /**
   * §137 / 基地守位格 (base guard anchor): 0 = OFF (byte-identical — the
   * defense position stays at (BASE_POS.col, baseRow − defenseRowOffset),
   * which sits on the base protection ring on ALL 35 stages and is therefore
   * never reachable by navigate). 1 = ON: when the default defense position
   * is not standable, compute a guard cell in the base box (cols bc−2..bc+3
   * × rows br−3..br+1) scoring ring-defense coverage + lane coverage + cover
   * − distance, and hold it as the defense anchor. Battlement picks (12,22)
   * — the row-22 antechamber mouth above the ring — intercepting enemies
   * before they breach the ring (data-driven, no stage-name overrides §81).
   */
  baseGuardAnchorMode: number
  /**
   * §137 v2: max player→anchor distance (cells) for the anchor HOLD to
   * apply. When the base is under threat, no enemy has a clear shot at it
   * yet, and the player is within this many cells of the guard anchor, hold
   * the anchor instead of chasing — the §134 lane-intercept shoots enemies
   * crossing the approach band before they reach the ring. 0 = never hold
   * (only the v1 defense-position replacement applies). Only read when
   * baseGuardAnchorMode > 0.
   */
  baseGuardAnchorHoldRange: number
  /**
   * §139 / 方向 A（进攻侧）: 火力死区解除 (firing-lane re-engage). 0 = OFF
   * (byte-identical). 1 = ON: when the player has NO enemy LOS in any of the
   * 4 directions (dead zone — standing with nothing to shoot) and all live
   * enemies are beyond firingLaneMinEnemyDist (too far to chase directly),
   * the FIRING_LANE candidate searches the radius-firingLaneRadius box for
   * the best standable cell that can SEE an enemy (same row/col, clear LOS)
   * and navigates there instead of idling. Re-engage, not hold — the
   * opposite of §137/§138 (which parked the player at a guard cell).
   * Battlement: 34% of all ticks parked in the (11,24) firing dead zone is
   * the #1 output bottleneck (shots/run 24.9 vs 67.7 on winning stages).
   */
  firingLaneMode: number
  /** §139: lookout-search radius (cells) around the player. */
  firingLaneRadius: number
  /** §139: enemies closer than this (cells) are chased directly, not diverted. */
  firingLaneMinEnemyDist: number
  /** §139: ticks between lookout-cell re-searches (throttle). */
  firingLaneReplanTicks: number
  /**
   * §135 / 方向 D 预测版: cells of approach lead-time to ALSO intercept. 0 =
   * OFF (byte-identical to §134 SHIPPED — only enemies already ON the lane,
   * enemyCanShootBase, trigger). >0: an enemy that shares the base's column
   * (or the base's row) and is FACING the base within this many cells of the
   * lane is treated as an imminent base threat too — the DEFENSE_INTERCEPT
   * candidate stops and fires at it from the defense position BEFORE it
   * reaches the ring and fires (Battlement: the fast arrives at the ring and
   * fires before the static §134 predicate ever becomes true — §135 closes
   * that final-approach gap). The candidate still re-verifies LOS via
   * scanAheadImpl before committing (brick between player and enemy blocks
   * the shot, same as §134).
   */
  defenseInterceptPredictCells: number
  /**
   * §136 / 方向 D 破砖版: when the §135 predict predicate fires but the shot
   * is blocked by a SCENE brick (not the base-protection ring, not steel),
   * the player fires at the brick to open the lane (first shot clears the
   * brick, the enemy walking in eats the next). 0 = OFF (byte-identical to
   * §134 SHIPPED — predict commits only when scan.enemy confirms a clear
   * shot). 1 = ON.
   *
   * §135 probe: the predict predicate hit 168× on Battlement but ZERO
   * became commits — every one was blocked by the scan LOS check. Most were
   * the base ring (correctly untouchable — never dig baseWall), but on
   * stages where scene bricks sit between the defense position and the
   * enemy's approach lane, digging opens a real firing window the enemy
   * must cross. Uses shouldFireInDirImpl (default allowWallFire=true) which
   * inherently forbids baseWall/steel — the bullet only ever clears scene
   * brick.
   */
  defenseInterceptDigBricks: number
  /** S7: cells around the base to scan for wall integrity. */
  baseWallScanRadius: number
  /** Re-plan interval (ticks). */
  replanInterval: number
  /**
   * §127 (perf): 1 = cross-tick replan cache ON (default). 0 = OFF
   * (byte-identical to pre-§127 — replanImpl re-runs full A* every tick).
   *
   * replanInterval defaults to 1, so followPath→replanImpl ran A* EVERY tick
   * — measured 73-89% of all findPath calls (docs/perf-optimization.progress.md
   * §2.10). The cache reuses the (playerCell, target) key + 60-tick safety
   * timer discipline of the §68 navigateTowards cache, applied to the MAIN
   * navigation path §68 missed. replanImpl draws NO RNG, so the cache is
   * BYTE-IDENTICAL — no §68-style signature relaxation needed.
   */
  replanCache: number
  /**
   * §129 (perf): 1 = pickup-reachability memo ON (default). 0 = OFF
   * (byte-identical to pre-§129 — powerUpCellReachable runs corridor A*
   * then dig A*, uncached, on every query).
   *
   * §129 turns the pickup reachability check into a strict pure memo of
   * (playerCell, target, terrain): 1) the corridor A* call is dropped
   * (breakBrick's search space strictly contains the corridor space, so the
   * boolean answer is identical — halves the per-query A* count), and 2) the
   * dig A* result is memoized under the §127-style (playerCell, target) +
   * tileMap.revision key. findPath draws no RNG, so this is byte-identical.
   */
  pickupReachCache: number
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

  // ---- M0.5 退役（2026-08-03, DECISIONS §96）----
  // guardBandMode/Row/HalfWidth + damagedArmorBonus（D1/D2 否决）与
  // smartThreatModel 族（Phase A 否决）已移入 experimental.ts 归档。
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
   * weight is 30-120). Gated independently for A/B testing.
   */
  defenseClearShotBonus: number

  /**
   * §132 / 方向 B (fast × base-proximity threat weight): score bonus in
   * defense-mode target selection for FAST enemies approaching the base.
   * A fast tank 6 cells from the base is a bigger threat than a basic tank
   * 3 cells out — the fast reaches the base in half the time, and the static
   * defenseKindWeight (fast=2) + linear -distToBase*10 cannot express that
   * (both score identically). When > 0, the base-threat score adds
   *   weight × speedRatio(kind) × clamp01((range − distToBase) / range)
   * where speedRatio = kind's BASE_SPEED_CPS / BALANCED_ENEMY_CPS
   * (fast 1.2, basic 1.0, power 0.95, armor 0.85) and the approach factor
   * ramps 1 at the base ring → 0 at fastBaseApproachRangeCells. Battlement
   * forensics (§132): 59% of base deaths are fast-tank kills, with the
   * player 4+ cells away 74% of the time. 0 = OFF (byte-identical).
   */
  fastBaseApproachWeight: number
  /** §132: distance (cells) at which the speed×proximity term fully fades. */
  fastBaseApproachRangeCells: number

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

  // ---- §133 / 方向 C: brick-heavy defense tightening (brickW ≥ threshold) ----
  /**
   * §133 / 方向 C (DECISIONS §133): brick/(brick+steel) ratio AT OR ABOVE
   * which the stage gets TIGHTER defense distances — the player returns to
   * the base EARLIER instead of deep-hunting while fast tanks rush the
   * base through pure-brick lanes. 0 = never adapt (byte-identical).
   *
   * Why (hard 35×120 forensics, §131/§132): brick-heavy stages have NO
   * indestructible steel — enemies (59% fast on Battlement) reach the base
   * ring through breakable brick, and the base dies early (Battlement
   * median tick ≈ 3244) with the player 9+ cells away 42% of the time. The
   * §115 M4 search widened all three defense distances GLOBALLY
   * (maxPlayerDistFromBase 26 / outnumberedFieldDistCells 26 / race 18),
   * and §60's open-defense even TIGHTENS race to 14 on these very stages —
   * the combination leaves the player deep-hunting when the base is
   * undefendable. §133 re-tightens ONLY the brick-heavy set: earlier race
   * trigger (bigger range), earlier forced return under threat (smaller
   * maxPlayerDistFromBase), earlier M13 field-pressure retreat (smaller
   * outnumberedFieldDistCells).
   *
   * Default 0.9 — matches the pure-brick set S0/S3/S14/S30/S33/S34 while
   * leaving S2 (0.833) and S16 (0.892) on the global values.
   */
  brickHeavyDefenseWallRatio: number
  /** §133: baseRaceRangeCells for brick-heavy stages (bigger = earlier race trigger). */
  brickHeavyBaseRaceRangeCells: number
  /** §133: maxPlayerDistFromBase for brick-heavy stages (smaller = earlier return). */
  brickHeavyMaxPlayerDistFromBase: number
  /** §133: outnumberedFieldDistCells for brick-heavy stages (smaller = earlier M13 retreat). */
  brickHeavyFieldDistCells: number

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

  // M0.5 退役（2026-08-03）: §68-v2 crossfireAwareness / §69-B crossfirePathCost
  // 已移入 experimental.ts 归档（双否决）。路径威胁基础设施（findPathThreat /
  // findSafeMoveDir / computeThreatCosts）保留在 experimental.ts 供 v2 survive
  // 候选与 M2+ risk 分复用（设计 §3.2 / §4.4 整合条款）。

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

  // M0.5 退役（2026-08-03）: trapAvoidance 族（3 项）已移入 experimental.ts
  // 归档（默认 0 未发布）。"包围风险"输入并入 v2 survive 候选设计（§3.2）。

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
   * M5 (plan/God-AI-Redesign-v2, DECISIONS §103): 站位提前规避 — path-threat
   * avoidance in the navigate/hunt branch.
   *
   * When > 0, after `_moveDir` is chosen in the HUNT candidate (long-range
   * followPath or close-range directMove), the path ahead is checked for
   * in-flight enemy bullets via `findPathThreat` (time-aware, ±10-tick window
   * over 3 lookahead cells). If a bullet would arrive while the player is
   * walking into that cell, `findSafeMoveDir` finds a safer alternative
   * (perpendicular first, backward as fallback, cell-1 only) and overrides
   * `_moveDir` — the player does NOT walk into crossfire, from the root
   * reducing the number of times it enters the reactive dodge branch
   * (83% of deaths on hard/chaos, DECISIONS §96).
   *
   * Distinction from the retired §68-v2 diversion (DECISIONS §73): §68
   * committed to a perpendicular A* path diversion at 12-23 ticks lead time
   * and died (premature commitment off the path); M5 only swaps the
   * IMMEDIATE next step (cell-1) when `findPathThreat` already flagged an
   * imminent collision, and re-evaluates every tick (no path commitment).
   * The A* path cache is untouched — the next tick re-plans from the new
   * cell. This is strictly less committed than §68: same detection, but the
   * response never leaves the current corridor plan for more than one step.
   *
   * 0 = OFF (byte-identical to M0). 1 = ON.
   */
  pathThreatAvoidance: number

  // M0.5 退役（2026-08-03）: dodgeHysteresis 已移入 experimental.ts 归档
  // （A/B -1.1pp，未发布）。

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

  // ---- §M3: Dodge quality (plan/God-AI-Redesign-v2 M3) ----
  /**
   * §M3-revisit (round 3): PINNED-GATED counter-fire in the DODGE branch —
   * "counter-only-when-pinned". When the threat's perpendicular dodge is
   * mathematically infeasible (isDodgePinnedImpl: no perpendicular direction
   * can clear the bullet's hit band before arrival, or all are blocked /
   * covered by other bullets), the player faces the bullet and fires to
   * cancel it (对枪抵消 — bullet-bullet collision,
   * SimulationCombat.bulletHitsBullet). Only then is counter-fire the ONLY
   * reliable survival move.
   *
   * Why (DECISIONS §96 death attribution + per-tick traces): 83% of
   * hard/chaos player deaths occur in the dodge branch; the branch never
   * fires at the incoming bullet (fire is gated on the MOVE direction only);
   * a close bullet (~4px/tick) arrives in ~8 ticks while a perpendicular
   * dodge needs ~19 — the dodge is mathematically futile at close range
   * (maze seeds 2/16 failure mode). Round 1 gated on DISTANCE alone and was
   * reverted (DECISIONS §98, 2026-08-03): it counter-fired mid-maneuver
   * during a VIABLE dodge (S25 Ice Palace seed 10 → deterministic
   * 5/20→1/20 regression). The pinned gate is the refinement: a dodge that
   * can actually clear (offset-aware — the player already partially off the
   * line needs less movement) stays a dodge; only when dodging cannot
   * possibly save the player does the AI stand and cancel.
   *
   * 0 = OFF (shipped default, byte-identical to M0). 1 = ON (A/B knob).
   * Must be validated with the official 口径 (no stageIndex) + 60-seed truth.
   */
  dodgeCounterFire: number
  /**
   * §M3: max lateral offset (px) between the player center and the threat
   * bullet for the counter-fire to trigger. The player's 6px bullet must
   * actually collide with the enemy bullet (half-width sum ≈ 6px) — a
   * bullet more than ~6px off-center passes beside the player's shot.
   */
  dodgeCounterFireAlignPx: number
  /**
   * §M3: multi-bullet dodge direction scoring. When > 0,
   * `dodgeDirectionImpl` scores each passable perpendicular candidate by its
   * nearest-bullet CLEARANCE (minimum arrival tick of any other enemy bullet
   * at the cell the player would move into) and picks the candidate with the
   * most clearance, instead of the binary next-cell isSafeDir check.
   * Addresses crossfire deaths: dodging INTO a cell where another bullet
   * arrives in 2 ticks scores badly vs one with 15 ticks of clearance.
   * 0 = OFF (binary, byte-identical). 1 = ON.
   */
  dodgeClearanceScore: number
  /**
   * M9 (plan/God-AI-Redesign-v2): survival-horizon dodge commitment scoring.
   * When > 0, `dodgeDirectionImpl` scores each passable perpendicular candidate
   * by its SURVIVAL HORIZON — the earliest tick any enemy bullet could hit the
   * player if it COMMITS to moving that way (per-tick hit-time estimation:
   * t_arrive vs perpendicular escape time to clear the bullet's hit band,
   * clamped by the terrain-limited free path) — and commits to the
   * longer-horizon direction.
   *
   * Addresses the measured dominant dodge-death failure mode (M9 probe,
   * probe-m9-commit.ts): hard 31.8% / chaos 35.0% of dodge-branch deaths are
   * "commitment failures" — the threat was escapable when the dodge started
   * (t_arrive >= escape time) but the player oscillated within the 32px hit
   * band (perpendicular displacement < 19px) instead of sustaining an escape
   * (crossfire direction-mischoice measured ~0%, so the binary next-cell
   * isSafeDir was NOT the bottleneck). The horizon model fixes both: it
   * commits to the escaping side and downgrades sides covered by other bullets.
   * 0 = OFF (byte-identical to M0). 1 = ON (A/B knob).
   */
  dodgeHorizonScore: number

  /**
   * M10: min escape-margin gate for the horizon commitment (tick). Only
   * commit to a dodge direction when its survival margin exceeds this
   * threshold — i.e. the escape is CLEARLY winnable, not a marginal knife-edge.
   * 0 = no margin gate: any escapable side commits; when BOTH perpendiculars
   * are doomed (both margins ≤ 0) the gate also fails and the legacy binary
   * path runs (differs from M9's later-hit pick — see ThreatAssessor).
   * Positive values filter out low-value commitments (DECISIONS §108: MARGIN6
   * was hard +1.6pp but chaos -2.4pp at 60-seed → not shipped).
   */
  dodgeHorizonMinMarginTicks: number

  /**
   * M10: max distance-to-base gate for the horizon commitment (cells). When
   * the player is farther than this from the base, horizon commitment is
   * skipped (legacy binary dodge) — the player must not chase survival
   * escapes far from the base while the base is undefended. Only applies
   * when the stage has a base (hasBase). 0 = unlimited (no distance gate).
   * NOTE: A/B-measured HARMFUL (chaos -4.0pp at maxDist=8, DECISIONS §108)
   * — kept only as an experimental knob, do not ship.
   */
  dodgeHorizonMaxDistCells: number

  // ---- M12: player HP buffer awareness (DECISIONS §112) ----
  /**
   * M12: master gate for HP-adaptive dodge commitment. 0 = OFF
   * (byte-identical — the margin stays dodgeHorizonMinMarginTicks).
   * 1 = ON. Only applies in the 'pool' combat model (instant/classic has no
   * HP buffer — 1 hit = death, there is no trade/commit gradient).
   * Mechanism: adjusts the horizon commit gate inside dodgeDirectionImpl
   * based on the player's hits-to-die vs the current threat bullet
   * (ceil(player.hp / bullet.damage)).
   */
  playerHpAwareness: number

  /**
   * M12 danger mode: when hits-to-die <= this value, the player is in the
   * danger zone (measured: hard 70% / chaos 67% of deaths absorb >= 3 hits,
   * and ~1/5 of survival time is spent at <= 2 hits — §111 probe). In danger
   * mode the horizon commit margin is RELAXED to hpDangerCommitMargin — the
   * escape is survival, not efficiency, so the player commits to the
   * longer-horizon perpendicular side instead of oscillating inside the hit
   * band (M9 measured commitment-failure as 32-35% of dodge deaths).
   * 0 = danger mode disabled.
   */
  hpDangerHits: number

  /**
   * M12 danger-mode commit margin (ticks). When hpDangerHits is satisfied,
   * the horizon commit gate uses this margin instead of
   * dodgeHorizonMinMarginTicks. 0 = keep dodgeHorizonMinMarginTicks (danger
   * mode only removes the distance gate, not tested — keep simple).
   */
  hpDangerCommitMargin: number

  /**
   * M12 trade mode: when hits-to-die >= this value, the player has a full HP
   * buffer (pool 1★ = 315 HP ≈ 4 hits) and can afford to TRADE a hit for
   * progress. In trade mode the commit margin is TIGHTENED by
   * hpTradeCommitPenalty — the player accepts the partial dodge (legacy
   * binary path, keeps moving/attacking) instead of over-committing to an
   * escape that costs base-defense and kill efficiency (M9/M10 measured the
   * ungated escape as an efficiency loss). 0 = trade mode disabled.
   */
  hpTradeHits: number

  /**
   * M12 trade-mode margin penalty (ticks), ADDED to
   * dodgeHorizonMinMarginTicks when hpTradeHits is satisfied. Larger = more
   * risk acceptance (fewer horizon commits at high HP).
   */
  hpTradeCommitPenalty: number

  // ---- §87: Urgent power-up pickup priority (user request 2026-08-02) ----
  /**
   * §87: 0 = OFF (byte-identical to pre-§87). 1 = ON: a CLOSE power-up with a
   * SAFE PATH outranks base defense (回防) and enemy-kill (杀敌) targets.
   *
   * When ON, the think() loop checks — before T2a stop-and-aim and before the
   * normal S5 power-up economy — whether a power-up within its category range
   * (see below) has a safe path (route danger <= pickupPriorityMaxDanger,
   * i.e. no enemy between the player and the item) and is actually reachable
   * (A*, same corridor/dig path the navigator drives). If so, the player
   * diverts to collect it instead of camping on an aligned enemy or returning
   * to the defense position.
   *
   * Deliberately placed AFTER dodge (survive) and T8 (intercept an in-flight
   * bullet aimed at the base — an immediate loss) and gated to NORMAL mode
   * (not freeze/aggressive: during a freeze window an aligned frozen enemy
   * is a free kill the aggressive branch already exploits, and it already
   * grabs power-ups when no enemy is aligned).
   *
   * Why: in classic the AI ignored nearby power-ups whenever it was fighting
   * (aimDir set, not on cooldown) or defending (base under threat) — the S5
   * gate `(!aimDir || onCooldown) && !baseUnderThreat && no enemies within 5
   * cells` never fired. A bomb/freeze/fence at 5 cells that would clear the
   * screen or buy the base a steel ring was walked past. Ranges are the
   * tuning knobs for the A/B sweep (default target: 8/4/2).
   */
  pickupPriorityMode: number
  /** §87: max distance (cells) for HIGH-value power-ups: bomb/freeze/fence. */
  pickupPriorityHighRange: number
  /** §87: max distance (cells) for MID-value power-ups: star/tank/shield. */
  pickupPriorityMidRange: number
  /** §87: max distance (cells) for LOW-value power-ups: boat. */
  pickupPriorityLowRange: number
  /**
   * §87: max route danger for the path to be considered "safe" (see
   * calculateRouteDanger). 0 = no enemy between player and item — the
   * strictest, intended default.
   */
  pickupPriorityMaxDanger: number
  /**
   * §87: nearby-enemy radius (cells). When any fully-spawned enemy is within
   * this Manhattan distance of the PLAYER, urgent pickups are skipped — even
   * if no enemy lies strictly between the player and the item.
   *
   * per-seed tick-diff finding (2026-08-02, Lattice s2 / Battlement s3):
   * the original "path safe" gate (danger <= maxDanger) only counted enemies
   * BETWEEN player and item. An enemy 5 cells away, or an active firefight,
   * still got abandoned while the player walked to the item — the player
   * then stalled or stopped firing and died. Same radius as S5's P3.2 gate
   * (5 cells) — this is the "don't divert while enemies are breathing down
   * your neck" rule, applied to the urgent branch.
   */
  pickupPriorityMinEnemyDist: number
  /**
   * §87: enemy spawn-zone gate. When > 0, urgent pickups whose ITEM cell row
   * is <= this value are skipped — classic enemies spawn at row 0 (ENEMY_SPAWNS
   * {0,0}/{12,0}/{6,0}), so rows 0..max are the spawn band where the player
   * gets funneled into fresh enemies.
   *
   * per-seed tick-diff finding (2026-08-02, Lattice s2/s32): diving for a
   * "safe" pickup in the top band put the player inside the enemy spawn
   * corridor — s32 walked up to a fence at (1,0) while an enemy spawned at
   * (0,0) beside it; s2 diverted to a star at row 2. Both died. The gate
   * excludes the band outright — the S5 economy can still fetch far items
   * there via normal navigation, but the AI never treats the spawn band as
   * an urgent short-range errand.
   */
  pickupPrioritySpawnRowMax: number

  // ---- §88: 据守咽喉要地 (chokepoint holding, user request 2026-08-02) ----
  /**
   * §88: 0 = OFF (byte-identical to pre-§88). 1 = ON: when the base is NOT
   * under threat, the player HOLDS a 咽喉要地 (chokepoint) instead of chasing
   * the nearest enemy — a lower-half map cell from which the player can
   * shoot enemies traversing the most 威胁路径 (threat paths: A* routes
   * from each enemy to its nearest 威胁点, facing-gated per rule 3). When
   * few enemies remain (<= chokepointHoldThreshold), the player chases the
   * enemy nearest a threat point instead.
   *
   * 威胁点 (threat point) = a cell from which an enemy can directly shoot the
   * base — exactly the `canShootBaseFrom` predicate (SmartThreatModel).
   *
   * §88 also reorders the §87 urgent-pickup priority chain to the user's
   * spec: HIGH tier (bomb/freeze/fence 8格) > 回防基地 > MID tier
   * (star/tank/shield 4格) > 据守咽喉要地. Only active when chokepointMode
   * is ON; when OFF, §87 keeps its shipped all-tiers-before-defense order
   * (byte-identical).
   */
  chokepointMode: number
  /**
   * §88: margin (cells) around a threat point that still counts as "base
   * threatened" (rule 1: 威胁点外 2 格, needs tuning). An enemy within this
   * Manhattan distance of ANY threat point triggers the base-threat state.
   */
  threatPointMargin: number
  /**
   * §88: hold-threshold (rule 2: 敌人数目 > 2 → 据守). When the number of
   * live enemies on field is GREATER than this value (and the base is not
   * threatened), the player holds the chokepoint; at or below it, the
   * player chases the enemy nearest a threat point.
   */
  chokepointHoldThreshold: number
  /** §88: lowest row for chokepoint candidates (地图下半区, default 13). */
  chokepointMinRow: number
  /**
   * §88: tie-break weight for STEEL cover around a chokepoint candidate
   * (rule 5: 钢铁优先级远高于砖墙). Steel-adjacent cells score
   * chokepointSteelWeight each; brick cells chokepointBrickWeight.
   */
  chokepointSteelWeight: number
  /** §88: tie-break weight for BRICK cover around a chokepoint candidate. */
  chokepointBrickWeight: number
  /**
   * §88: facing gate (rule 3). 1 = a threat path is only counted when the
   * enemy's turret (炮口朝向) is along the path's dominant direction toward
   * the threat point (an enemy facing AWAY from the base is not about to
   * attack it). 0 = ignore facing (all paths counted).
   */
  chokepointFacingGate: number
  /**
   * §88: max number of nearest threat points per enemy to A*-path (throttled
   * chokepoint computation — bounds cost; default 4). The threat-point set
   * itself is terrain-derived and small (base column + base rows).
   */
  chokepointPathsPerEnemy: number
  /**
   * §88: enemies farther than this Manhattan distance from their NEAREST
   * threat point contribute NO threat paths (perf bound: a failed A* explores
   * the whole grid — skipping far enemies keeps the throttled plan cheap; a
   * distant enemy is not an imminent base threat, so its path adds little to
   * chokepoint selection). Default 14. 0 = no cap.
   */
  chokepointMaxThreatDist: number
  /** §88: recompute interval (ticks) for the throttled chokepoint plan. */
  chokepointReplanTicks: number
  /**
   * §88 A/B round 2: chase arm imminence gate (cells). Only enemies whose
   * NEAREST threat point is within this Manhattan distance count as imminent
   * base threats worth diverting for — otherwise the chase arm dragged the
   * player after distant enemies (S15 seed 24) and the hold arm idled at a
   * stale chokepoint (S19 seed 23). 0 = gate off (chase any nearest).
   */
  chokepointChaseMaxDist: number
  /**
   * §88 A/B round 3: hold-arm max distance. A chokepoint 9 cells away is not
   * worth marching to — the enemy turns / gets killed en route and the player
   * idles (S26 seed 12: player at (7,14) marched to hold (14,16), the fast
   * threat died, and the stale path derailed navigation). When the hold cell
   * is farther than this, prefer chasing the imminent threat directly. 0 = unlimited.
   */
  chokepointHoldMaxDist: number
  /** §88 A/B round 2: hold-arm imminence re-check cadence (unused, reserved). */
  chokepointHoldCheckTicks: number
  /**
   * §88 A/B round 3: chase-arm max PLAYER distance. The chase arm intercepts
   * an enemy about to reach a threat point, but only pays off when the player
   * can actually get there in time. S32 seed 10: chase sent the player from
   * (8,3) on a 27-cell march to intercept (0,22) — the enemy reached the
   * threat point long before the player arrived, and the march derailed the
   * game while A's normal nearest-enemy hunt won. 0 = unlimited (pre-round-3).
   */
  chokepointChaseMaxPlayerDist: number

  /**
   * M2 (plan/God-AI-Redesign-v2 §3.2): per-params decision-chain weight
   * overrides. When provided, the M1 candidate chain is evaluated in
   * effective-weight order (`overrides[id] ?? ACTION_WEIGHTS[id]`, descending,
   * stable) instead of the fixed chain order. The ordered candidate list is
   * pre-built once per reset in GodAIInput — never sorted per tick
   * (AGENTS §14.3/14.5).
   *
   * Default (undefined) = the M1 chain order (dodge > interceptBase >
   * pickupHigh > aggro > pickupMid > engage > pickupLow > hunt) —
   * byte-identical to pre-M2. Changing a weight IS a behavior change and
   * must go through the 60-seed A/B discipline (official 口径, no stageIndex).
   */
  actionWeights?: Partial<Record<ActionId, number>>

  // ---- M3: 敌情感知 EnemyModel + 命数感知 (plan/God-AI-Redesign-v2 §4.2b/§4.3) ----
  /**
   * M3: 0 = OFF (byte-identical to pre-M3 — the model never updates and all
   * consumers return 0). 1 = pure dynamic EnemyModel (features only).
   * 2 = 混合模式 (50/50 blend with the static `aiState.level` prior, which
   * requires enemyTierWeightCommander/Veteran > 0). The model is a per-tick
   * EMA of observable enemy behavior (fire accuracy / base approach /
   * alignment / turn discipline) — pure World observation, no RNG, no
   * difficultyKey reads (评审决议 3).
   */
  enemyModelMode: number
  /** M3: EMA window (ticks) for the EnemyModel features. >0 required for
   *  the model to run (`enemyModelMode > 0 && window > 0`). */
  enemyModelWindowTicks: number
  /**
   * M3: 感知敌人强度 → 目标选择加权. When > 0, findEnemyDirectionImpl scales
   * the T9 threat score by `1 + tierWeightScale * estimatedEnemyLevel` — the
   * AI commits its fire priority to the enemies it has SEEN play well, rather
   * than a static kind weight. Default 0 (byte-identical).
   */
  tierWeightScale: number
  /**
   * M3: 感知闪避率 → 有效 T2a 射程缩放. When > 0, the ENGAGE candidate's
   * effective 1-HP range shrinks as the model's `discipline` (turn frequency)
   * rises: `effectiveRange = t2aMaxRange * (1 - dodgeRateShrinksT2a * discipline)`.
   * A stage where enemies dodge/redirect a lot forces point-blank engagement
   * (shorter bullet travel → fewer dodged shots). Default 0 (byte-identical).
   */
  dodgeRateShrinksT2a: number
  /**
   * M3: 配合压力 → 保命压力提前. When > 0, survival pressure activates when
   * `coordination * weight >= 1` (many enemies aligned with the player at
   * once) — even while lives are still plentiful. Default 0 (byte-identical).
   */
  coordinationRiskWeight: number
  /**
   * M3: 敌人命中率 → 保命压力提前. When > 0, survival pressure activates when
   * the model's estimated fire accuracy reaches this threshold (0..1) — the
   * enemy is hitting, so stop taking risks NOW, not when lives run out.
   * Default 0 (byte-identical).
   */
  enemyAccuracyRaisesSurvival: number
  /** M3 (混合模式 prior): static weight for commander-tier enemies in
   *  `staticPriorLevel`. 0 = no prior signal from commanders. */
  enemyTierWeightCommander: number
  /** M3 (混合模式 prior): static weight for veteran-tier enemies. */
  enemyTierWeightVeteran: number
  /**
   * M3 (P0-3 命数盲 fix, plan §4.3): 0 = OFF (byte-identical). >0 = survival
   * pressure activates when `world.lives <= this value` — the AI stops taking
   * high-risk actions (deep hunts, long-range duels) on its last lives.
   * chaos startLives=3 ⇒ survivalModeLives=1 means "act safe on the last life".
   */
  survivalModeLives: number
  /**
   * M3: 生存模式下高风险动作的风险惩罚系数 γ. When survival pressure is 1,
   * high-risk candidates (hunt / long-range engage) are suppressed — the
   * higher this weight, the earlier/deeper the retreat to defense. Default 0
   * (byte-identical — no suppression).
   */
  survivalRiskWeight: number
  /**
   * M3: survive 候选（主动换位）的包围触发阈值. When > 0, the survive
   * candidate may activate: at least this many live enemies within
   * `surviveEnemyRadiusCells` of a ≤2-exit cell the player occupies. 0 = OFF
   * (candidate never commits — byte-identical). Default 0.
   */
  surviveMinEnemies: number
  /** M3: survive 候选的包围判定半径 (cells). */
  surviveEnemyRadiusCells: number

  // ---- M13: field-wide outnumbered positioning (DECISIONS §113) ----
  /**
   * M13: 0 = OFF (byte-identical). 1 = ON: when the FIELD-wide live enemy
   * count is at/over `outnumberedFieldEnemies` (P4.2 only counts enemies
   * WITHIN outnumberedRadiusCells — converging) AND the player is beyond
   * `outnumberedFieldDistCells` from the base, selectTarget returns the
   * defense position instead of deep-hunting. Targets the dominant death
   * mode (M13 probe): 70% of hard/chaos deaths happen with the full 4-enemy
   * field alive, 39% at >20 cells from base, 85% at 1★ — the player
   * over-extends while outnumbered and gets ground down (1★ single bullet
   * cannot out-race 4 enemies). Skipped when the base is under threat or in
   * aggressive (freeze) mode.
   */
  outnumberedFieldRetreat: number

  /** M13: field-wide live-enemy threshold for the retreat (default 4 = MAX_ENEMIES_ALIVE). */
  outnumberedFieldEnemies: number

  /** M13: min player dist-to-base (cells) for the retreat. */
  outnumberedFieldDistCells: number

  // ---- 自杀秒回 (suicide quick-return, user request 2026-08-04, §116/§117) ----
  /**
   * 0 = OFF (byte-identical to pre-§116). 1 = §116 original — trigger on
   * condition ⑤ (a LETHAL bullet hits within suicideReturnBulletTimeTicks;
   * the player is about to die anyway, so the life-trade is nearly free).
   * 2 = §117 condition-① variant, STAND — trigger when an enemy is at a
   *   threat point while a bullet is actively flying at the base; the player
   *   stands still and waits to be killed, with a suicideReturnStandMaxTicks
   *   timeout (resumes if no death comes). 3 = §117 condition-① variant,
   *   CHARGE — same trigger, but the player actively drives at the threat
   *   enemy (no dodging) to die fast and respawn near the base, or to kill
   *   the enemy first; whichever happens first ends the trade.
   *
   * The shared preconditions (checked in the SUICIDE_RETURN candidate,
   * think.ts; condition ⑤ only for mode 1):
   *   1. An enemy is at a threat point (can directly shoot the base).
   *   2. The player's spawn point can hit that enemy (immediately or 1 turn).
   *   3. The player has spare lives (lives ≥ suicideReturnMinLives).
   *   4. The player is too far from that enemy (> suicideReturnEnemyDistTicks
   *      at full speed — the player can't save the base by running back).
   *   5. (mode 1 only) A lethal bullet will hit the player within
   *      suicideReturnBulletTimeTicks (dodging is futile or the player is
   *      about to die anyway — trade the life for a better position).
   *   GATE (all modes): a bullet is actively flying at the base
   *   (findBulletThreatToBaseImpl) — the S23 seed-14 regression fix.
   */
  suicideReturnMode: number
  /** Max ticks for the lethal bullet to reach the player (default 60 = 1s). */
  suicideReturnBulletTimeTicks: number
  /** Min ticks the player would need at full speed to reach the threat enemy
   * (default 300 = 5s). If the player can reach it faster, no suicide. */
  suicideReturnEnemyDistTicks: number
  /** Min lives required (default 2 = at least 1 spare life beyond the current).
   * When the player dies, lives-- then respawn only if lives > 0, so lives
   * must be ≥ 2 before death to guarantee a respawn. */
  suicideReturnMinLives: number
  /**
   * Spawn-point usefulness margin for the suicide return (condition 2). The
   * spawn must be able to deal with a threat enemy in time — either a clear
   * 0-1-turn shot (spawnCanHitEnemyImpl) OR within this many Manhattan cells
   * of the enemy (respawn puts the player close enough to reach it fast with
   * its 3s shield). Strict 0-1-turn geometry is rare on real stages (base
   * walls/topology), so the distance fallback makes the strategy fire where
   * it genuinely helps. Larger = more permissive (respawn more often); 0 = the
   * distance fallback is disabled (only the strict 0-1-turn shot counts).
   */
  suicideReturnSpawnDistCells: number
  /**
   * Mode-2 (STAND) standing timeout in ticks (default 300 = 5s). The player
   * stands still waiting to be killed for at most this many ticks; if no
   * death comes (nobody shoots the stationary player), it resumes normal
   * play instead of freezing forever (the §116 S30 standing-freeze
   * pathology, moved to the healthy-player case). Only used when
   * suicideReturnMode === 2.
   */
  suicideReturnStandMaxTicks: number

  // ---- §118: strict-doom guard for modes 2/3 (baseHp + defense-lost) ----
  /**
   * §118: base-HP doom threshold for modes 2/3, as a FRACTION of baseMaxHp.
   *
   * Root cause of the §117 flip losses: the condition-① trade committed while
   * the base was at FULL HP with the normal defense (T8 intercept / 据守 /
   * return) still running — one in-flight bullet is NOT proof the base will
   * fall (hard S35 seed-8: base 120/120, the OFF arm returned and cleared;
   * the ON arm abandoned the defense and lost). When > 0, modes 2/3 require
   * baseHp ≤ this fraction × baseMaxHp — the base is genuinely a hit or two
   * from falling, so trading a life for a respawn near it is a true last
   * resort. 0 = disabled (byte-identical to §117).
   *
   * Pool-model oriented (hard/chaos). On classic (baseMaxHp = 1) any positive
   * fraction < 1 is unsatisfiable, which safely disables the trade there.
   */
  suicideReturnBaseHpFrac: number
  /**
   * §118: defense-position-lost distance for modes 2/3 (Manhattan cells).
   *
   * When > 0, the trade only commits when the player is farther than this
   * from the base — i.e. the player CANNOT return in time to intercept the
   * in-flight base bullet (measured: killer-bullet travel to base is 14-15
   * ticks ≈ 0.24s). A close player must NOT abandon a working defense
   * position for a gamble. 0 = disabled (byte-identical to §117).
   */
  suicideReturnDefendDistCells: number

  // ---- §121: t2a/aggressive 停射自毁守卫 (self-fire base guard, §120 forensics) ----
  /**
   * 0 = OFF (byte-identical to pre-§121). 1 = STRICT: suppress the
   * t2a/aggressive stop-and-aim fire when the bullet's CENTER line (the
   * actual 6px path — NOT the scan's ±8px offset lines) can reach the base
   * eagle; the terrain walk ignores tanks (they can dodge off the line).
   * 2 = LENIENT: same walk, but only suppress when NO enemy tank body
   * overlaps the bullet corridor before the base (keeps true point-blank
   * overlap kills where the bullet provably hits the enemy first).
   *
   * Root cause (§120, 32/32 self-kill forensics): the scan's two ±8px
   * offset lines catch an enemy up to ~25px off the bullet's 6px center
   * path and report scan.enemy with the enemy CLOSER than the base eagle —
   * the §74 dual-offset guard then allows fire ("the enemy is in the way").
   * The 6px bullet misses the off-line enemy and continues into the base
   * (hard S6 s43: killer shot at x=200, enemy body at x∈[206,238] — 6px
   * bullet [197,203] passed beside it into the eagle).
   */
  selfFireBaseGuard: number
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
  // §115 (M4 round-2, 2026-08-04): full-corpus CMA-ES search shipped values —
  // hard/chaos (pool model) +5.0pp / +8.2pp at 60 seeds vs the §113 shipped
  // baseline (49.0% / 48.3% → 54.0% / 56.5%). See CLASSIC_MODEL_PARAMS below
  // for the instant/classic restore table (91% gate byte-identical).
  defenseColSpread: 3,
  threatRangeCells: 23,
  maxPlayerDistFromBase: 26,
  // P4: race-to-base emergency defense (see interface docs). Range 18 /
  // margin 2 — M4 search widened the race window (earlier, more committed
  // defense before the enemy reaches the base).
  baseRaceRangeCells: 18,
  baseRaceMarginCells: 2,
  // P4.2: retreat when 3+ enemies converge within 9 cells — the player
  // trades 1-for-1 at best in open crossfire; falling back to the defense
  // row funnels enemies into single-file corridors instead.
  // §115: M4 search disabled P4.2 (outnumberedEnemyCount 3→5 = never fires,
  // max 4 enemies alive) — the replan=1 + wider threat-range combo made the
  // nearby-retreat counterproductive; the field-wide M13 retreat still guards
  // the base. Kept as a knob (classic keeps 3/9 via CLASSIC_MODEL_PARAMS).
  outnumberedEnemyCount: 5,
  outnumberedRadiusCells: 7,
  t8MaxInterceptDistCells: 2,
  // §134 / 方向 D: 防守位停射拦截 — SHIPPED（2026-08-05, DECISIONS §134）。
  // A/B 官方口径：20-seed +8/+11/+8；60-seed hard +0.76pp（p=0.17，S32 +15pp /
  // S34 +10pp / Battlement +2.5pp）、chaos +2.15pp（p=0.0087 显著）→ 双难度净正。
  // classic（instant 1-HP）未 A/B — 经 CLASSIC_MODEL_PARAMS restore 0。
  defenseInterceptMode: 1,
  defenseInterceptMaxDist: 12,
  defenseInterceptRangeCells: 15,
  // §137 / 基地守位格: 默认防守位 (12,23) 在全部 35 关都是环砖、navigate 永远到不了
  // ——AI 没有有效防守锚点（Battlement 漏斗几何把这个洞暴露了）。默认 0 = OFF
  // （byte-identical）。A/B 候选：mode=1（Battlement 应选 (12,22) 前厅口）。
  baseGuardAnchorMode: 0,
  // §137 v2: 受威胁且无 clear-shot 敌人时、玩家距守位格 ≤ 此值 → 驻守守位格
  // （让 §134 在前厅口拦截）。仅 mode>0 时读。A/B 候选：holdRange 0/6/10。
  baseGuardAnchorHoldRange: 6,
  // §139 / 方向 A（进攻侧）: 火力死区解除。默认 0 = OFF（byte-identical）。
  // A/B 候选：mode=1（Battlement 死区 34% 占用 → 去有射界的瞭望格重新接战）。
  firingLaneMode: 0,
  firingLaneRadius: 5,
  firingLaneMinEnemyDist: 4,
  firingLaneReplanTicks: 15,
  // §135 / 方向 D 预测版: 提前拦截格数。默认 0 = OFF（byte-identical 到 §134
  // SHIPPED——只拦已上车道者）。A/B 候选：predict=1/2/3。
  defenseInterceptPredictCells: 0,
  // §136 / 方向 D 破砖版: 预测命中但被场景砖挡时打砖开路。默认 0 = OFF
  // （byte-identical 到 §134——预测只在 scan.enemy 确认时才提交）。
  defenseInterceptDigBricks: 0,
  baseWallScanRadius: 5,
  replanInterval: 1,
  replanCache: 1,
  pickupReachCache: 1,
  powerupMaxDivertDistance: 18,
  endgameEnemyThreshold: 10,
  huntAllyCount: 1,

  // P0: Anti-camp / T2a deadlock fix (plan/God-AI-Next-Round).
  // campTimeoutTicks=20 (M4 search: far less patient — replan=1 keeps the
  // player moving, so camping patience is worth less) — if the player hasn't
  // gotten a kill in the timeout, something is wrong (enemy dodging, wall in
  // the way, etc.). antiCampSuppressTicks=60 (1s) — enough to move ~2 cells
  // at player speed, changing the tactical situation before T2a can re-trigger.
  campTimeoutTicks: 20,
  antiCampSuppressTicks: 60,
  // P0.3: navStuckTicks=180 (3s) — if the player hasn't progressed (stayed
  // at the same cell) for 3 seconds of navigating, force a roam to the map
  // center. This breaks pursuit loops with faster enemies.
  navStuckTicks: 180,

  // M0.5 退役（2026-08-03）: D1/D2 guardBand + damagedArmor、smartThreatModel
  // 族已移入 experimental.ts 归档（见该文件参数规格表）。
  // Close-combat: default 15 (= AIM_RANGE_CELLS, unchanged behavior for
  // 1-HP enemies). For multi-HP enemies (armor), t2aHighHpMaxRange=2
  // triggers point-blank engagement (§56 — generalizes S32 close-combat).
  t2aMaxRange: 15,
  t2aHighHpMaxRange: 2,

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
  // 0 = OFF (byte-identical to pre-§59).
  defenseClearShotBonus: 500,

  // §132 / 方向 B: speed × base-proximity threat weight in defense target
  // selection. Default 0 = OFF (byte-identical — the scoring term short-
  // circuits). A/B sweep candidates: weight 500/1000 × range 10/12.
  fastBaseApproachWeight: 0,
  fastBaseApproachRangeCells: 10,

  // §60: Open-defense adaptation. On non-steel-maze stages, widen
  // baseRaceRangeCells from 11 to 14 for earlier threat detection. Steel
  // mazes (brick/(brick+steel) < 0.10) keep the default 11 — early retreat
  // hurts there because enemies bypass the defense position via corridors.
  openDefenseBrickWallRatio: 0.1,
  openDefenseBaseRaceRangeCells: 14,

  // §133 / 方向 C: brick-heavy defense tightening. Default 0 = OFF
  // (byte-identical — the adaptation block never runs). Candidate values
  // for the 20-seed sweep (all injected together via --params):
  //   mild   race=20 maxDist=20 fieldDist=16
  //   balance race=22 maxDist=18 fieldDist=12
  //   tight  race=24 maxDist=14 fieldDist=8
  brickHeavyDefenseWallRatio: 0,
  brickHeavyBaseRaceRangeCells: 22,
  brickHeavyMaxPlayerDistFromBase: 18,
  brickHeavyFieldDistCells: 12,

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
  // §64: armor-heavy + high-steel + non-steel-maze → widen outnumberedRadius.
  // Only S26 matches. Probes: S26 +10pp (75% → 85%, 30 seeds). 9 = no change.
  armorSteelOutnumberedRadiusCells: 12,
  // §66: steel-maze non-armor camp timeout. Only S6 matches. +16pp (60 seeds).
  steelMazeCampTimeoutTicks: 20,

  // M0.5 退役: §63 openT2a1HpMaxRange / §65 armorMazeSuboptimalPathProb /
  // crossfire 族（§68-v2/§69/§69-B）已移入 experimental.ts 归档（60-seed 验证
  // 均为净负或否决）。

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
  // M0.5 退役（2026-08-03）: trapAvoidance 族已移入 experimental.ts 归档
  // （默认 0 未发布；"包围风险"输入并入 v2 survive 候选设计 §3.2）。
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
  // M5: 站位提前规避 — 0 = OFF (byte-identical to M0). 1 = ON (A/B knob).
  pathThreatAvoidance: 0,
  // ── §86 oscillation-experiment params (A/B-only knobs) ──────────────
  // Evaluated for the §86 dodge-oscillation fix. Only `dodgeOscillationCounterFire`
  // ships ON. The other three are A/B-only and are NEVER part of the shipped
  // default (intentionally left OFF — see interface docs). The canonical fix
  // is the simulation-layer turn cooldown (§86c), not these AI-layer patches.
  // M0.5 退役（2026-08-03）: dodgeHysteresis / dodgeDirPersistence /
  // canMoveDirFloorSnap 已移入 experimental.ts 归档（A/B 均净负，从未发布；
  // §86c 模拟层转弯冷却为规范修复，dodgeOscillationCounterFire 为唯一发布项）。
  dodgeOscillationCounterFire: 1,

  // ── §M3: Dodge quality (plan/God-AI-Redesign-v2 M3) ──────────────────
  // dodgeCounterFire (round 1, distance-gated) REVERTED to OFF (DECISIONS
  // §98): official-shape 35x20 showed chaos 34.6→34.1% (flat-to-negative)
  // with a deterministic S25 Ice Palace regression (5/20→1/20 — counter-fire
  // interrupted a working dodge mid-move). Round 3 (DECISIONS §101) replaces
  // the distance gate with the PINNED gate (isDodgePinnedImpl) — still OFF by
  // default; the A/B runs in the M3 milestone. dodgeCounterFireRangeCells
  // was removed (obsolete: the pinned gate is geometric, not distance-based).
  // Align 6px = bullet half-width sum (cancellation needs a near-dead-on shot).
  dodgeCounterFire: 0,
  dodgeCounterFireAlignPx: 6,
  dodgeClearanceScore: 0,
  // M9: survival-horizon dodge commitment (DECISIONS §107 pending) — OFF by
  // default; the A/B runs in the M9 milestone. See interface docs for the
  // measured failure mode (commitment failure 32-35% of dodge deaths).
  dodgeHorizonScore: 0,
  // M10: time-margin + distance-to-base gates for the horizon commitment
  // (both 0 = no gate = pure M9 semantics; A/B knobs, DECISIONS §108 pending).
  dodgeHorizonMinMarginTicks: 0,
  dodgeHorizonMaxDistCells: 0,
  // M12: player HP buffer awareness (DECISIONS §112) — OFF by default; the
  // A/B runs in the M12 milestone. All five params 0 = byte-identical.
  playerHpAwareness: 0,
  hpDangerHits: 0,
  hpDangerCommitMargin: 0,
  hpTradeHits: 0,
  hpTradeCommitPenalty: 0,

  // ── §87: Urgent power-up pickup priority (user request 2026-08-02) ───
  // SHIPPED default: ON with the A/B-validated ranges/gates (DECISIONS §87).
  // 35×60 A/B (classic, 18000t): 1899/2100 → 1908/2100 (+9 wins), win rate
  // 90%→91%, suite 0.7439→0.7551, net flips +54/−45, zero significant
  // regressions (Lattice −8→−2, Star Fort −5→+1 after the gates).
  // Setting pickupPriorityMode=0 (and/or any gate to 0) restores the
  // pre-§87 behavior — the OFF state was verified byte-identical via
  // per-seed tick-diff.
  pickupPriorityMode: 1,
  pickupPriorityHighRange: 8,
  pickupPriorityMidRange: 4,
  pickupPriorityLowRange: 2,
  pickupPriorityMaxDanger: 0,
  pickupPriorityMinEnemyDist: 5,
  pickupPrioritySpawnRowMax: 3,

  // ── §88: 据守咽喉要地 (chokepoint holding) ────────────────────────
  // SHIPPED ON (2026-08-03, DECISIONS §93/§94): 120-seed A/B on S6/S16/S32
  // confirmed 全面持平或提升 (S6 0.000, S16 +0.018, S32 +0.011; win 83→85%,
  // 13 better / 8 worse / 339 tied), no stage regression. Tuned knobs:
  // margin 1 (威胁点外 1 格 — A/B round 3 从用户规格 2 下调: margin=2 在
  // S32 把玩家拖离击杀过频), hold threshold 2 (敌人数目 > 2), minRow 13
  // (下半区), steel 10 / brick 1 (钢铁优先远高于砖墙), facing gate ON,
  // 4 paths/enemy, replan every 30 ticks, chaseMaxDist 3 / holdMaxDist 6 /
  // chaseMaxPlayerDist 10 (速度缩放).
  chokepointMode: 1,
  threatPointMargin: 1,
  chokepointHoldThreshold: 2,
  chokepointMinRow: 13,
  chokepointSteelWeight: 10,
  chokepointBrickWeight: 1,
  chokepointFacingGate: 1,
  chokepointPathsPerEnemy: 4,
  chokepointMaxThreatDist: 14,
  chokepointReplanTicks: 30,
  // A/B round 2 (per-seed tick-diff): chase 分支把玩家引去追距威胁点 10 格
  // 的远敌（S15 seed 24），拖慢清场。chase 的本意是拦截「即将到达威胁点」
  // 的敌人——距威胁点超过 chaseMaxDist 格不算紧迫威胁，fall-through 到原
  // 最近敌人追杀（S6/正常选择），与 OFF 字节相同。
  chokepointChaseMaxDist: 3,
  // A/B round 2 (per-seed tick-diff): 玩家到达据守点后敌人已转向、威胁路径
  // 消失，缓存计划仍锁死玩家守株待兔（S19 seed 23：玩家在 (4,20) 空转 ~1200
  // tick 直到 base 从另一侧被破）。到达据守点后若威胁态已解除（threatState
  // false），fall-through 到正常目标选择。
  chokepointHoldCheckTicks: 1,
  // A/B round 3: 据守点超过 6 格（chokepointHoldMaxDist）不值得走过去——
  // 敌人中途转向/被杀，玩家空转且路径残留污染导航（S26 seed 12）。
  chokepointHoldMaxDist: 6,
  // A/B round 3: chase 目标距玩家超过 10 格同样不值得追（S32 seed 10：玩家在
  // (8,3) 被引去 27 格外追 (0,22)，敌先到威胁点，玩家白跑整局）。
  chokepointChaseMaxPlayerDist: 10,

  // ── M3: 敌情感知 EnemyModel + 命数感知 (plan/God-AI-Redesign-v2 §4.2b/§4.3) ──
  // 全部默认 OFF/0 = 逐字节不变（M3 里程碑验收：开启后无回退，而非默认启用）。
  // enemyModelMode: 0=OFF, 1=纯动态, 2=混合（+静态 aiState 先验）。
  enemyModelMode: 0,
  // EMA 窗口（ticks）。>0 且 enemyModelMode>0 时模型每 tick 更新。
  enemyModelWindowTicks: 0,
  // 感知敌人强度 → T9 威胁加权（FireControl）。
  tierWeightScale: 0,
  // 感知闪避率（discipline）→ 有效 T2a 射程缩放（ENGAGE）。
  dodgeRateShrinksT2a: 0,
  // 配合压力（coordination）→ 保命压力提前。
  coordinationRiskWeight: 0,
  // 命中率阈值（0..1）→ 保命压力提前。
  enemyAccuracyRaisesSurvival: 0,
  // 混合模式静态先验权重（0 = 无先验）。
  enemyTierWeightCommander: 0,
  enemyTierWeightVeteran: 0,
  // 命数 ≤ 该值激活保命压力（chaos 3 命 → 1 = 末命保命）。
  survivalModeLives: 0,
  // 保命压力下高风险候选（hunt/远距 engage）的抑制系数。
  survivalRiskWeight: 0,
  // survive 候选（主动换位）：0 = OFF（不提交）。
  surviveMinEnemies: 0,
  surviveEnemyRadiusCells: 3,
  // M13: 全场压力撤退（DECISIONS §113，SHIPPED 2026-08-04）— 默认 ON，
  // pool 模型专属（classic instant 无磨血死亡，91% 门禁字节不变）。
  // 60-seed：hard +2.3pp / chaos +0.6pp（无 chaos 负向；基地失守与死亡
  // 双难度均下降）。ON4@10 实测有害（-5.3pp 过于被动）——3 只即撤 + 15 格。
  outnumberedFieldRetreat: 1,
  // §115: M4 search widened M13's field retreat (enemies 3→4, dist 15→26) —
  // with replan=1 + wider threatRange the player defends more dynamically and
  // the retreat fires only in truly full-pressure states. 60-seed cross-check
  // (HARD_BEST set): hard +4.2pp / chaos +8.6pp vs shipped — net positive
  // even with the retreat weakened.
  outnumberedFieldEnemies: 4,
  outnumberedFieldDistCells: 26,
  // 自杀秒回 (suicide quick-return, §116/§117): default OFF — A/B tested
  // (per-seed tick-diff + §117 forensics) before enabling, per the §88
  // methodology. When ON, the player trades a life for a better position to
  // save the base.
  suicideReturnMode: 0,
  suicideReturnBulletTimeTicks: 60, // 1s
  suicideReturnEnemyDistTicks: 300, // 5s
  suicideReturnMinLives: 2, // at least 1 spare life
  suicideReturnSpawnDistCells: 6, // spawn within 6 cells of the threat enemy
  suicideReturnStandMaxTicks: 300, // 5s — mode-2 standing timeout
  // §118 strict-doom guard (modes 2/3): 0 = OFF — A/B tested before enabling.
  suicideReturnBaseHpFrac: 0, // base must be at/below this × baseMaxHp
  suicideReturnDefendDistCells: 0, // player must be farther than this from base
  // §121 t2a/aggressive 停射自毁守卫 — SHIPPED default 2 (lenient). A/B
  // (35 关 × 120 seeds × hard+chaos, 3 arms): strict(mode 1) regresses
  // (hard −29 / chaos −24 flips — over-suppresses legitimate kill shots),
  // lenient(mode 2) wins on both (hard +12 / chaos +8 flips, Δbase_destroyed
  // −7/−12, guardBlocks 16K vs 82K). Classic restored to 0 via
  // CLASSIC_MODEL_PARAMS (instant 1-HP combat has zero margin — untested,
  // keep byte-identical per §115).
  selfFireBaseGuard: 2,
}

/**
 * §115 (M4 round-2): instant/classic restore table. The M4 search was
 * optimized on the POOL combat model (hard/chaos — HP buffers, 磨血死亡).
 * classic ('instant': flat per-bullet damage, 1 hit ≈ death for most kinds)
 * has no 磨血死亡 and the search-tuned aggression is MEASURED HARMFUL there
 * (classic 91.0% → 88.6% at 35×20 if the M4 defaults leak in). GodAIInput.reset()
 * applies this restore when world.rules.combatModel === 'instant', keeping
 * the classic regression gate byte-identical (DECISIONS §115).
 */
export const CLASSIC_MODEL_PARAMS: Partial<GodAIParams> = {
  defenseColSpread: 5,
  threatRangeCells: 10,
  baseRaceRangeCells: 11,
  baseRaceMarginCells: 0,
  outnumberedEnemyCount: 3,
  outnumberedRadiusCells: 9,
  t8MaxInterceptDistCells: 8,
  baseWallScanRadius: 3,
  replanInterval: 50,
  powerupMaxDivertDistance: 16,
  endgameEnemyThreshold: 6,
  campTimeoutTicks: 90,
  outnumberedFieldEnemies: 3,
  outnumberedFieldDistCells: 15,
  // §121: the lenient self-fire base guard is a pool-model (hard/chaos) fix.
  // classic is instant 1-HP combat with zero margin for suppressed kill shots
  // and was never A/B'd here — restore 0 (byte-identical classic gate).
  selfFireBaseGuard: 0,
  // §134: 防守位停射拦截是 pool-model（hard/chaos）修复 — classic instant
  // 1-HP 未 A/B，restore 0（byte-identical classic gate）。
  defenseInterceptMode: 0,
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

    // §133 / 方向 C: brick-heavy defense tightening. Runs AFTER §60 so it
    // re-tightens the race range §60 just widened (open-defense's 14 is
    // SMALLER than the §115 global 18 — on pure-brick stages that means
    // later, not earlier, defense). On brickW ≥ brickHeavyDefenseWallRatio
    // stages there is no indestructible steel: fast tanks rush the base
    // ring through breakable brick and the base dies early while the
    // player deep-hunts (§131/§132 forensics). Override the three defense
    // distances — earlier race trigger (bigger range), earlier forced
    // return under threat (smaller maxPlayerDistFromBase), earlier M13
    // field-pressure retreat (smaller outnumberedFieldDistCells). 0 = OFF.
    if (p.brickHeavyDefenseWallRatio > 0 && brickWallRatio >= p.brickHeavyDefenseWallRatio) {
      overrides.baseRaceRangeCells = p.brickHeavyBaseRaceRangeCells
      overrides.maxPlayerDistFromBase = p.brickHeavyMaxPlayerDistFromBase
      overrides.outnumberedFieldDistCells = p.brickHeavyFieldDistCells
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

      // M0.5 退役（2026-08-03）: §63 openT2a1HpMaxRange 适配已移入 experimental.ts
      // 归档（60-seed 验证净负 -0.6pp，回退）。
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

    // M0.5 退役: §65 armorMazeSuboptimalPathProb 适配已移入 experimental.ts
    // 归档（30-seed +3pp 但 60-seed -1.7pp，回退）。

    // §66: steel-maze + low-armor → shorter camp timeout. On S6 Iron Curtain
    // (0% armor, brickWallRatio 0.04), the player camps at indestructible
    // steel walls for the full 60-tick timeout, wasting ~1s per deadlock.
    // A 20-tick timeout breaks the deadlock 3× faster. Excluded: S32
    // (armor-heavy steel-maze) where the armor camp timing already applies.
    if (isSteelMaze && !armorHeavy && p.steelMazeCampTimeoutTicks !== base.campTimeoutTicks) {
      overrides.campTimeoutTicks = p.steelMazeCampTimeoutTicks
      adapted = true
    }

    // M0.5 退役: §69 crossfireOpenObstacleRatio 适配已移入 experimental.ts 归档
    // （crossfire 族 §68/§69 双否决）。

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

  // Always return a fresh object, even when unadapted: callers must never be
  // handed a reference to a shared singleton (DEFAULT_GOD_AI_PARAMS) that they
  // could mutate. Cross-file module state IS shared inside `bun test`, and a
  // leaked mutation silently corrupts every later simulation in the process
  // (DECISIONS §98 — a test mutation flipped the gate's S25 result).
  // GodAIInput also clones at construction; this closes the vector for ALL
  // callers of this exported function.
  return adapted ? { ...base, ...overrides } : { ...base }
}
