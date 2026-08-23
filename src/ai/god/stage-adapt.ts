// stage-adapt.ts — §58 Strategy G stage-adapted params (§3.1 split; pure
// relocation): detectCentralBreachRisk + computeStageAdaptedParams. Pure
// functions of World state; called once per reset(), never per-tick.
import type { World } from '../../game/World'
import { GRID, ENEMY_SPAWNS } from '../../constants'
import type { GodAIParams } from './params.interface'

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
/**
 * Dual central breach detector (plan/dual-central-breach-strategy.md §A):
 * Scan the central band (cols 11–13, rows 0–22) for steel. If steel count = 0
 * AND an enemy spawn point exists at col 12±1 (the center column — the default
 * ENEMY_SPAWNS always includes col 12), the stage has a "central breach risk":
 * enemies spawning at col 12 can drive straight down through breakable brick
 * to the base with no indestructible barrier.
 *
 * Pure function of World state (tileMap only) — deterministic, no RNG.
 * Called once per reset() — not a hot path.
 */
export function detectCentralBreachRisk(world: World): boolean {
  const tm = world.tileMap
  // Condition 1: no steel in the central band (cols 11–13, rows 0–22).
  for (let row = 0; row <= 22; row++) {
    for (let col = 11; col <= 13; col++) {
      if (tm.get(col, row) === 'steel') return false
    }
  }
  // Condition 2: an enemy spawn point at col 12±1 (center column).
  // The default ENEMY_SPAWNS always includes {col: 12, row: 0}, so this is
  // virtually always true — but check explicitly for correctness if spawns
  // ever change.
  let centerSpawn = false
  for (let i = 0; i < ENEMY_SPAWNS.length; i++) {
    if (Math.abs(ENEMY_SPAWNS[i].col - 12) <= 1) {
      centerSpawn = true
      break
    }
  }
  if (!centerSpawn) return false
  // Condition 3: col 12 must have an OPEN approach from the top (rows 0–9
  // must be mostly empty) — enemies spawning at (12,0) can drive straight
  // down through open terrain to the brick wall below. Stages where col 12
  // is brick from row 2 (e.g. S14 Steel Web) slow the approach and don't
  // need the central breach strategy. Threshold: ≥4 empty cells in rows 0–9.
  let openCells = 0
  for (let row = 0; row <= 9; row++) {
    if (tm.get(12, row) === 'empty') openCells++
  }
  return openCells >= 4
}

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

  // ---- 3. Dual central breach strategy (plan/dual-central-breach-strategy.md) ----
  // When spectateDual AND the stage has centralBreachRisk (no steel in the
  // central band + center enemy spawn), override the defense enhancement knobs
  // that are normally OFF (0) in single-player. The gating is strict:
  // world.spectateDual === true is the FIRST check — single-player NEVER enters
  // this block, so the overrides are never applied, and the existing knobs
  // stay at their default 0 → byte-identical to pre-change behavior.
  // §190: Also active in coop (躺赢模式) — the God AI controlling P2 needs
  // the same dual central breach params overrides as in 督战双玩家.
  if ((world.spectateDual || world.coop) && detectCentralBreachRisk(world)) {
    overrides.defenseBreachBonus = p.dualCentralBreachDefenseBreachBonus
    overrides.baseGuardAnchorMode = p.dualCentralBreachAnchorMode
    overrides.threatStickyTicks = p.dualCentralBreachStickyTicks
    overrides.baseDamageRecall = p.dualCentralBreachDamageRecall
    // §178: let the carve-dig nav-stuck escape punch through the central wall so
    // the two tanks REACH their guard anchors (otherwise pinned at the top).
    overrides.carveMaxBaseColumn = p.dualCentralBreachCarveMaxBaseColumn
    overrides.carveBaseColumnCost = p.dualCentralBreachCarveBaseColumnCost
    adapted = true
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
