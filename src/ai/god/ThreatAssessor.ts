import type { GodAIInput } from '../GodAIInput'
import type { World } from '../../game/World'
import type { Bullet, Tank } from '../../types'
import type { Direction } from '../../constants'
import { CELL, TANK, DIR_VECTORS, BASE_POS, FIELD, GRID } from '../../constants'
import { type Cell } from './pathfind'
import { ALL_DIRS, opposite } from '../../utils/direction'
import { snap, aabb, manhattan, bulletLaneDist } from '../../utils/helpers'
import {
  BULLET_TRAJECTORY_MAX_CELLS,
  BULLET_ALIGN_NEXT_CELL,
  HIT_HALF_SPAN,
  BASE_CENTER_X_PX,
  BASE_CENTER_Y_PX,
} from './constants'
import { enemyCanShootBase } from './SmartThreatModel'
import { isThreatStateImpl } from './Chokepoint'
import { scanAheadImpl } from './FireControl'

/** §223 centroid-escape: bullets within this L1 radius (px) of the player
 *  count toward the cluster. 6 cells — the immediate hit vicinity. */
const CENTROID_RADIUS_PX = 6 * CELL
/** §223 centroid-escape base gate: the new cell may NOT be further from the
 *  base than the current cell (S10s6-style runaway protection — a single
 *  dodge step is 16px, so any >0 slack would make the gate dead code; a
 *  multi-tick sustained escape therefore always reduces base distance or
 *  degrades to the legacy binary path). */
const CENTROID_BASE_SLACK_CELLS = 0

// ============================================================
// ThreatAssessor — bullet-threat assessment + dodging (T8, M3)
// Moved verbatim from GodAIInput.ts during the §0.5 split.
// Each `impl` takes `self: GodAIInput` so it can read shared state and
// call sibling methods via the public wrappers on GodAIInput.
// ============================================================

/**
 * Find the most dangerous incoming enemy bullet. "Dangerous" = aligned with
 * the player and approaching. Returns null if no threat.
 */
export function findMostDangerousBulletImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
): Bullet | null {
  const w = self.world
  let best: Bullet | null = null
  let bestDist = Infinity

  // §48-revisit: steel-only occlusion. When ON, skip bullets whose path to
  // the player is blocked by steel (permanent for enemy bullets —
  // STEEL_PIERCE_PLAYER_LEVEL is player-only). Brick is NOT checked: dodging
  // brick-blocked bullets is load-bearing anticipatory dodging (DECISIONS
  // §48). Computed once (loop-invariant) so V8 guards the scan block when OFF.
  const steelOcclusion = self.params.evasionSteelOcclusion > 0
  // Distance gate (px): 0 = suppress all steel-blocked bullets; >0 suppresses
  // only blocked bullets at dist >= range. Near blocked bullets keep their
  // dodge (load-bearing repositioning — per-seed tick-diff, S32 seed 11).
  const steelOcclusionRangePx =
    self.params.evasionSteelOcclusionRange > 0 ? self.params.evasionSteelOcclusionRange * CELL : 0

  // Pinned-position gate (user finding, §48-revisit): the S32 seed-11
  // regression is NOT about dodging vs not dodging — it's that suppressing
  // the dodge left the player PINNED in a corner (tick 738: player at (0,1)
  // stayed + fired while the baseline dodged down and escaped). When the
  // player is geometrically constrained (≤ 2 open directions), the dodge IS
  // the escape — never suppress it, even for a steel-blocked bullet. Only in
  // open space (3-4 open directions) is a steel-blocked dodge genuinely
  // wasteful. Computed only when occlusion is active (OFF stays byte-identical).
  let playerConstrained = false
  if (steelOcclusion) {
    const pTank = self.controlledTank(self.world)
    if (pTank) {
      let openDirs = 0
      for (let di = 0; di < ALL_DIRS.length; di++) {
        if (self.canMoveDir(pTank, ALL_DIRS[di])) openDirs++
      }
      playerConstrained = openDirs <= 2
    }
  }

  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    // M0.5 退役（2026-08-03）: dodgeHysteresis（TANK+2 对齐阈值）已退役
    // 归档（A/B -1.1pp，从未发布）——固定标准 TANK 阈值。
    // §3.1: lane geometry single-sourced in utils/helpers.bulletLaneDist.
    const dist = bulletLaneDist(b.dir, bcx, bcy, pcx, pcy, TANK)
    if (dist < 0) continue

    // §48-revisit: scan the bullet→player path for steel. If any steel cell
    // blocks the path, this bullet (and all future bullets from this enemy
    // in this direction) can never reach the player — skip the threat.
    // OOB-safe: break on off-field, never use TileMap.get's OOB→'steel'
    // default (the §70 false-positive bug). Brick does NOT cause a skip —
    // keep scanning past brick for steel behind it.
    // Distance gate: only suppress when dist >= the range threshold, so
    // NEAR blocked bullets keep their load-bearing repositioning dodge.
    if (steelOcclusion && dist >= steelOcclusionRangePx) {
      const v = DIR_VECTORS[b.dir]
      const grid = w.tileMap.grid
      let steelBlocked = false
      for (let d = CELL; d < dist; d += CELL) {
        const fx = bcx + v.dx * d
        const fy = bcy + v.dy * d
        const col = Math.floor(fx / CELL)
        const row = Math.floor(fy / CELL)
        if (col < 0 || col >= GRID || row < 0 || row >= GRID) break
        if (grid[row][col] === 'steel') {
          steelBlocked = true
          break
        }
      }
      // Pinned gate: only suppress when the player is NOT geometrically
      // constrained. When pinned (≤2 open directions), the dodge is the
      // escape from the corner — keep it (S32 seed-11 regression mechanism).
      if (steelBlocked && !playerConstrained) continue
    }

    if (dist < bestDist) {
      bestDist = dist
      best = b
    }
  }
  return best
}

/**
 * T8: Find an enemy bullet whose trajectory will cross the base area.
 * This is the ultimate defense — intercept bullets heading for the base
 * even if they're not threatening the player.
 * Gap B: returns null when the stage has no base.
 */
export function findBulletThreatToBaseImpl(self: GodAIInput): Bullet | null {
  if (!self.hasBase) return null
  const w = self.world
  const baseCx = BASE_CENTER_X_PX
  const baseCy = BASE_CENTER_Y_PX
  const baseHalf = CELL // base is 2×2 cells = 32px, half = 16px

  let best: Bullet | null = null
  let bestDist = Infinity

  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue

    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2

    // Directional pre-filter (perf): the base sits at rows 24-25 (bottom of
    // the field). Skip bullets that physically cannot reach it, avoiding the
    // per-cell trajectory scan for the majority of in-flight bullets.
    // - 'up' bullets move away from the base → never a threat.
    // - 'left'/'right' bullets stay at their row → only a threat if already
    //   in the base's row band (≥ row 22, generous — base is rows 24-25).
    // - 'down' bullets move toward the base → always potentially a threat.
    // Strict superset: any filtered bullet provably cannot cross the base
    // area or hit base terrain, so no threat is missed.
    if (b.dir === 'up') continue
    if ((b.dir === 'left' || b.dir === 'right') && bcy < 22 * CELL) continue

    const v = DIR_VECTORS[b.dir]

    // Project the bullet's trajectory forward and check if it crosses the base.
    // Fix Bug 4: terrain check must come BEFORE base-area check — otherwise
    // walls protecting the base are ignored, causing false-positive threats
    // and wasted interception actions.
    let crossesBase = false
    for (let d = CELL; d <= BULLET_TRAJECTORY_MAX_CELLS * CELL; d += CELL) {
      const fx = bcx + v.dx * d
      const fy = bcy + v.dy * d

      // If the trajectory goes off-field, stop.
      if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break

      // Check terrain FIRST — if a wall blocks the bullet, it never
      // reaches the base, so there's no threat.
      const col = Math.floor(fx / CELL)
      const row = Math.floor(fy / CELL)
      const terrain = w.tileMap.get(col, row)
      if (terrain === 'brick' || terrain === 'steel') break

      // If it hits the base itself, that's a direct hit.
      if (terrain === 'base') {
        crossesBase = true
        break
      }

      // Check if the trajectory point is within the base area.
      // Use baseHalf (16px = 1 cell) instead of baseHalf * 2 — the base
      // is 2×2 cells centered at (baseCx, baseCy), so half-width = CELL.
      if (Math.abs(fx - baseCx) < baseHalf && Math.abs(fy - baseCy) < baseHalf) {
        crossesBase = true
        break
      }
    }

    if (crossesBase) {
      const dist = manhattan(bcx, bcy, baseCx, baseCy)
      if (dist < bestDist) {
        bestDist = dist
        best = b
      }
    }
  }

  return best
}

/**
 * T8: Calculate the cell where the player should move to intercept
 * a bullet heading toward the base. This is the cell on the bullet's
 * trajectory that is closest to the player.
 */
export function baseBulletInterceptCellImpl(self: GodAIInput, bullet: Bullet): Cell | null {
  const w = self.world
  const p = self.controlledTank(self.world)!
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const bcx = bullet.x + bullet.w / 2
  const bcy = bullet.y + bullet.h / 2
  const v = DIR_VECTORS[bullet.dir]

  // Walk along the bullet's trajectory and find the closest point to the player
  // that is BETWEEN the bullet and the base (in front of the bullet).
  let bestCell: Cell | null = null
  let bestDist = Infinity

  for (let d = 0; d <= BULLET_TRAJECTORY_MAX_CELLS * CELL; d += CELL) {
    const fx = bcx + v.dx * d
    const fy = bcy + v.dy * d
    if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break

    const col = Math.floor(fx / CELL)
    const row = Math.floor(fy / CELL)

    // Stop if the trajectory hits a wall.
    const terrain = w.tileMap.get(col, row)
    if (terrain === 'brick' || terrain === 'steel') break

    // Check if the player can reach this cell.
    const cellCx = col * CELL + CELL / 2
    const cellCy = row * CELL + CELL / 2
    const dist = manhattan(cellCx, cellCy, pcx, pcy)
    if (dist < bestDist) {
      bestDist = dist
      bestCell = { col, row }
    }

    // If we've passed the base, stop searching.
    if (terrain === 'base') break
  }

  // Only intercept if the player can reach the intercept point in time.
  // If the closest point is too far, the player can't get there before
  // the bullet — intercepting would just send the player on a wild goose
  // chase, leaving the base undefended.
  if (bestDist > self.params.t8MaxInterceptDistCells * CELL) return null

  return bestCell
}

/**
 * M9/M10: escape margin of a candidate dodge direction (tick).
 * For each enemy bullet threatening the player's lane, estimate whether
 * committing to `dir` can CLEAR the bullet's hit band (TANK/2 + b.w/2 ≈ 19px)
 * at player speed within the terrain-limited free path, and how much time is
 * LEFT OVER after the escape (t_arrive − escape_ticks). Returns the MIN across
 * bullets (most threatening bullet wins):
 *   > 0  — the direction escapes with that many ticks of margin (higher = safer)
 *   <= 0 — the direction gets hit (negative ≈ how far past the deadline)
 * Bullets covering the NEXT cell (dodging INTO a crossfire lane) count as
 * hits. M10 gates the commitment on this margin so only CLEARLY-winnable
 * escapes commit (DECISIONS §108); M9 compared min-tick horizons instead.
 * No allocations (AGENTS §14.1): indexed bullet loop, scalar locals.
 * Called only when dodgeHorizonScore > 0 (byte-identical to M0 otherwise).
 */
function dodgeHorizonTicksImpl(
  self: GodAIInput,
  p: Tank,
  pcx: number,
  pcy: number,
  dir: Direction,
): number {
  const w = self.world
  const pSpeed = p.speed
  const v = DIR_VECTORS[dir]
  const nx = pcx + v.dx * CELL
  const ny = pcy + v.dy * CELL
  // Terrain-limited free path in `dir` (px) — caps the escape distance.
  const freeDist = freePathDistPxImpl(self, p, dir)
  let minMargin = Infinity
  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    // §3.1: lane geometry single-sourced in utils/helpers.bulletLaneDist.

    // (1) Next-cell coverage (dodging INTO another bullet's lane) — a hit.
    const tArrNext = bulletLaneDist(b.dir, bcx, bcy, nx, ny, BULLET_ALIGN_NEXT_CELL)
    if (tArrNext >= 0) {
      const tn = tArrNext / b.speed
      if (-tn < minMargin) minMargin = -tn
    }

    // (2) Current-lane escape model (the primary commitment mechanism).
    const dist = bulletLaneDist(b.dir, bcx, bcy, pcx, pcy, TANK)
    if (dist < 0) continue
    const tArr = dist / b.speed
    const vertical = b.dir === 'up' || b.dir === 'down'
    // Is `dir` perpendicular to the bullet's travel (can the player escape its band)?
    const perp =
      (vertical && (dir === 'left' || dir === 'right')) ||
      (!vertical && (dir === 'up' || dir === 'down'))
    let margin = -tArr // non-perpendicular → the bullet hits at t_arrive
    if (perp) {
      const off = vertical ? Math.abs(bcx - pcx) : Math.abs(bcy - pcy)
      const band = TANK / 2 + b.w / 2
      const needDist = band - off // lateral movement needed to clear the band
      if (needDist <= 0) {
        margin = Infinity // already outside the band — no threat from this bullet
      } else if (needDist <= freeDist) {
        const escapeTicks = needDist / pSpeed
        margin = tArr - escapeTicks // leftover time after clearing the band
      }
    }
    if (margin < minMargin) minMargin = margin
  }
  return minMargin
}

/**
 * M9: max distance (px) the player can move from its current position in `dir`
 * before terrain (or the field edge, or another tank) blocks the 32px footprint
 * — mirrors canMoveDirRaw's snap-to-cell + rectHitsTerrain + tank-AABB checks,
 * stepping one CELL at a time. Cap 4 cells (64px): beyond that the escape
 * decision is already made. Called only from dodgeHorizonTicksImpl.
 */
function freePathDistPxImpl(self: GodAIInput, p: Tank, dir: Direction): number {
  const w = self.world
  const v = DIR_VECTORS[dir]
  // Mirror canMoveDirRaw: snap the current position, then step CELL by CELL.
  let gx = snap(p.x, CELL)
  let gy = snap(p.y, CELL)
  let dist = 0
  const scan = self._otherTanks.length > 0 ? self._otherTanks : w.allTanks
  for (let step = 0; step < 4; step++) {
    const nx = gx + v.dx * CELL
    const ny = gy + v.dy * CELL
    if (!w.isInBounds(nx, ny, TANK, TANK)) break
    if (w.rectHitsTerrain(nx, ny, TANK, TANK)) break
    let blocked = false
    for (let oi = 0; oi < scan.length; oi++) {
      const o = scan[oi]
      if (o === p || !o.alive) continue
      if (aabb(nx, ny, TANK, TANK, o.x, o.y, o.w, o.h)) {
        blocked = true
        break
      }
    }
    if (blocked) break
    gx = nx
    gy = ny
    dist += CELL
  }
  return dist
}

/**
 * §201: escape depth — how many full CELL steps in `dir` stay passable
 * (terrain + bounds only; tanks move, so they are not counted as walls).
 * Pure World read, no RNG. Mirrors canMoveDirRaw's geometry (snap grid,
 * TANK box, rectHitsTerrain).
 */
export function escapeDepthImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
  maxCells: number,
): number {
  const v = DIR_VECTORS[dir]
  const w = self.world
  const lx = pcx - TANK / 2
  const ly = pcy - TANK / 2
  let depth = 0
  for (let i = 1; i <= maxCells; i++) {
    const nx = lx + v.dx * i * CELL
    const ny = ly + v.dy * i * CELL
    if (!w.isInBounds(nx, ny, TANK, TANK)) break
    if (w.rectHitsTerrain(nx, ny, TANK, TANK)) break
    depth++
  }
  return depth
}

/**
 * Choose a dodge direction perpendicular to the incoming bullet.
 * M3: verify the candidate direction is safe (not into another bullet's path).
 */
/** §14.2 reusable dodge-strategy out-buffer (one dodge decision per tick;
 * dodgeDirectionImpl runs at most once per tick from think's dodge branch).
 * dir ≠ null ⇒ the strategy committed; otherwise safeA/safeB carry the legacy
 * binary scan verdicts into the shared pinned/tie-break tails. */
const _dodgeOut: { dir: Direction | null; safeA: boolean; safeB: boolean } = {
  dir: null,
  safeA: false,
  safeB: false,
}

/**
 * P1/P2.3: Check if any enemy is threatening the base. Six OR-combined
 * rules (each gated; result is cached per tick in `_baseUnderThreatCache`):
 *   1. Static box — enemy within 3 cols of base AND row >= 18
 *      (close lateral threat, the original rule).
 *   2. P4 race — enemy inside `baseRaceRangeCells` of the base AND would
 *      beat the player back (playerDist + baseRaceMarginCells >= enemyDist);
 *      catches edge-lane flank runners the box misses.
 *   3. §88 threat point — an enemy at/near a chokepoint-plan threat point
 *      (gated by chokepointMode > 0).
 *   4. §157 clear shot — an enemy aligned with the base with no brick/steel
 *      between (enemyCanShootBase), regardless of distance (gated by
 *      baseClearShotThreat > 0).
 *   5. §173 damage recall — base has actually taken a hit AND player is
 *      farther than baseDamageRecall cells (damage never flickers back).
 *   6. §169 sticky hold — once true, stays true for threatStickyTicks
 *      (only extends, never shortens) so the defense cascade doesn't
 *      flicker off between predictive gaps.
 * Used to skip power-ups/T2a and prioritize defense.
 */
export function isBaseUnderThreatImpl(self: GodAIInput): boolean {
  if (!self.hasBase) return false
  if (self._baseUnderThreatCache !== null) return self._baseUnderThreatCache
  const bc = BASE_POS.col
  const br = BASE_POS.row
  // P4: race-to-base check — player's distance to the base. If the player
  // is dead/respawning, treat any near-base enemy as a threat.
  const p = self.controlledTank(self.world)
  const pc = p ? self.playerCell() : null
  const playerDistToBase = pc ? manhattan(pc.col, pc.row, bc, br) : Infinity
  // Cluster C: reuse the per-tick snapshot (falls back to a fresh scan only
  // if think() hasn't populated it yet — should never happen in normal flow).
  const list = self._enemies.length > 0 ? self._enemies : self.world.tanks
  let result = false
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (!t.alive || t.spawnTimer > 0) continue
    const tc = self.tankCell(t)
    // Static box: close lateral threat (original P1/P2.3 rule).
    if (Math.abs(tc.col - bc) <= 3 && tc.row >= 18) {
      result = true
      break
    }
    // P4: race check — enemy is in the base region AND would beat the
    // player back to the base (with safety margin). Catches flanking
    // runners along the map edges that the static box misses (S6 root
    // cause: base died with the player 20+ cells away behind steel).
    const enemyDistToBase = manhattan(tc.col, tc.row, bc, br)
    if (
      enemyDistToBase <= self.params.baseRaceRangeCells &&
      playerDistToBase + self.params.baseRaceMarginCells >= enemyDistToBase
    ) {
      result = true
      break
    }
  }
  // §88 rule 1: an enemy at/near a threat point (威胁点外 margin 格) also
  // puts the base into the threatened state — the enemy can shoot the base
  // from there, so defense must outrank MID-tier pickups and chokepoint
  // holding. OR'd with the existing box/race detection (never reduces it).
  if (!result && self.params.chokepointMode > 0) {
    self.chokepointPlan() // ensure the throttled threat-point cache
    const plan = self._chokepointPlan
    if (plan && plan.threatPoints.length > 0 && isThreatStateImpl(self, plan.threatPoints)) {
      result = true
    }
  }
  // §157: an enemy with a CLEAR SHOT at the base (enemyCanShootBase —
  // aligned + no brick/steel in between) is a threat regardless of
  // distance. The static box (row >= 18) and race check (range ≤ 18)
  // miss enemies firing at the base from far away through cleared lanes.
  // The next bullet could destroy the base, so defense must activate.
  // Gated by baseClearShotThreat (0 = OFF, byte-identical).
  if (!result && self.params.baseClearShotThreat > 0) {
    for (let li2 = 0; li2 < list.length; li2++) {
      const t2 = list[li2]
      if (!t2.alive || t2.spawnTimer > 0) continue
      if (enemyCanShootBase(self, t2)) {
        result = true
        break
      }
    }
  }
  // §173: factual damage recall — once the base has actually TAKEN A HIT
  // (baseHp < baseMaxHp), the threat is no longer a prediction: the ring
  // bricks are breached and direct fire is landing. The predictive checks
  // above flicker (§169: 9.8 flips/10s before the first hit); damage never
  // flickers back. OR'd with the existing detection (never reduces it).
  // baseDamageRecall = 0 → OFF (byte-identical); >0 → the trigger engages
  // only while the player is farther than this many cells from the base
  // (arm 1 = unconditional was net −24: the permanent threat cascade hurt
  // open stages; the probe asymmetry is player-distance, so gate on it).
  if (!result && self.params.baseDamageRecall > 0) {
    if (
      self.world.baseHp < self.world.baseMaxHp &&
      playerDistToBase > self.params.baseDamageRecall
    ) {
      result = true
    }
  }
  // §169: sticky hold — the threat signal flickers as enemies cross the
  // race-range/alignment boundaries (defeat probe: 9.8 flips/10s before
  // the base's first hit). Once true, keep it true for threatStickyTicks
  // so the defense cascade (selectTarget, skipT2aForDefense, item gates,
  // F5 guard summon, carve gate) stays engaged through the gaps. Only
  // extends, never shortens; 0 = OFF = byte-identical.
  if (self.params.threatStickyTicks > 0) {
    if (result) {
      self._threatStickyHold = self.params.threatStickyTicks
    } else if (self._threatStickyHold > 0) {
      result = true
    }
  }
  self._baseUnderThreatCache = result
  return result
}

/**
 * §86 oscillation counter-fire (§3.8 extraction): after 3+ direction flips on
 * the same threat, face the bullet so think()'s fire cancels it.
 */
function dodgeOscillationDir(self: GodAIInput, bullet: Bullet): Direction | null {
  if (
    self.params.dodgeOscillationCounterFire > 0 &&
    self._dodgeFlipCount >= 3 &&
    bullet.id === self._lastDodgeThreatId
  ) {
    return opposite(bullet.dir) // face the bullet → think() fire cancels it
  }
  return null
}

/**
 * M9/M10/M12 survival-horizon commitment (§3.8 extraction). Writes the commit
 * direction or the legacy-binary safe flags into _dodgeOut.
 */
function dodgeHorizonCommit(
  self: GodAIInput,
  w: World,
  bullet: Bullet,
  p: Tank,
  pcx: number,
  pcy: number,
  candA: Direction,
  candB: Direction,
): void {
  _dodgeOut.dir = null
  _dodgeOut.safeA = false
  _dodgeOut.safeB = false
  const passA = self.canMoveDir(p, candA)
  const passB = self.canMoveDir(p, candB)
  if (passA || passB) {
    if (passA && passB) {
      const hA = dodgeHorizonTicksImpl(self, p, pcx, pcy, candA)
      const hB = dodgeHorizonTicksImpl(self, p, pcx, pcy, candB)
      const bestH = hA > hB ? hA : hB
      // M12 (DECISIONS §112): player HP buffer awareness — the commit
      // margin is HP-adaptive, but ONLY in the 'pool' combat model (classic
      // 'instant' has no HP buffer — 1 hit = death, no commit/trade
      // gradient exists). hits-to-die = ceil(player.hp / threat.damage).
      //   danger: hits-to-die <= hpDangerHits → RELAX to hpDangerCommitMargin
      //     (low HP: the escape is survival — commit to the longer-horizon
      //     side and stop oscillating inside the hit band; §111 probe: 70%
      //     of hard/chaos deaths absorb >= 3 hits while grinding).
      //   trade:  hits-to-die >= hpTradeHits → ADD hpTradeCommitPenalty
      //     (high HP: the buffer absorbs a hit — accept the partial dodge,
      //     keep moving/attacking instead of over-committing to an escape
      //     that costs base-defense and kill efficiency, M9/M10 measured).
      // Default playerHpAwareness=0 → margin unchanged (byte-identical).
      let margin = self.params.dodgeHorizonMinMarginTicks
      if (self.params.playerHpAwareness > 0 && w.rules.combatModel === 'pool') {
        const hitsToDie = Math.ceil(p.hp / Math.max(1, bullet.damage))
        // Danger takes precedence: when the danger condition matches, only
        // hpDangerCommitMargin applies (trade is skipped for that HP range
        // even if hpTradeHits is also satisfied). Overlapping thresholds
        // resolve to danger — the more urgent mode.
        if (self.params.hpDangerHits > 0 && hitsToDie <= self.params.hpDangerHits) {
          if (self.params.hpDangerCommitMargin > 0) margin = self.params.hpDangerCommitMargin
        } else if (self.params.hpTradeHits > 0 && hitsToDie >= self.params.hpTradeHits) {
          margin += self.params.hpTradeCommitPenalty
        }
      }
      let commit = bestH >= margin
      // Distance gate — only meaningful when the stage has a base; on
      // no-base stages the fixed BASE_POS is not a defense anchor.
      if (commit && self.hasBase && self.params.dodgeHorizonMaxDistCells > 0) {
        const pc = self.playerCell()
        const baseCol = BASE_POS.col + 1
        const baseRow = BASE_POS.row + 1
        const distCells = manhattan(pc.col, pc.row, baseCol, baseRow)
        if (distCells > self.params.dodgeHorizonMaxDistCells) commit = false
      }
      if (commit) {
        if (hA > hB) {
          _dodgeOut.dir = candA
          return
        }
        if (hB > hA) {
          _dodgeOut.dir = candB
          return
        }
      }
      // Gate failed or tied — legacy binary path (isSafeDir + passable
      // fallback, same as the default branch below).
      if (self.canMoveDir(p, candA) && self.isSafeDir(pcx, pcy, candA, bullet.id))
        _dodgeOut.safeA = true
      if (self.canMoveDir(p, candB) && self.isSafeDir(pcx, pcy, candB, bullet.id))
        _dodgeOut.safeB = true
      if (!_dodgeOut.safeA && !_dodgeOut.safeB) {
        if (self.canMoveDir(p, candA)) _dodgeOut.safeA = true
        if (self.canMoveDir(p, candB)) _dodgeOut.safeB = true
      }
    } else {
      // Only ONE perpendicular is passable — commit to it (the legacy path
      // also falls back to a passable-but-unsafe side when nothing is safe,
      // so the outcome is the same; the crossfire next-cell count is
      // redundant here since there is no alternative direction).
      {
        _dodgeOut.dir = passA ? candA : candB
        return
      }
    }
  }
  // Neither passable → fall through to the pinned (no-escape) logic below.
}

/**
 * M3 multi-bullet clearance scoring (§3.8 extraction). Writes the pick or the
 * tie-safe flags into _dodgeOut.
 */
function dodgeClearanceCommit(
  self: GodAIInput,
  bullet: Bullet,
  p: Tank,
  pcx: number,
  pcy: number,
  candA: Direction,
  candB: Direction,
): void {
  _dodgeOut.dir = null
  _dodgeOut.safeA = false
  _dodgeOut.safeB = false
  const passA = self.canMoveDir(p, candA)
  const passB = self.canMoveDir(p, candB)
  if (passA || passB) {
    if (passA && passB) {
      const clearA = dodgeClearanceTicksImpl(self, pcx, pcy, candA, bullet.id)
      const clearB = dodgeClearanceTicksImpl(self, pcx, pcy, candB, bullet.id)
      if (clearA > clearB) {
        _dodgeOut.dir = candA
        return
      }
      if (clearB > clearA) {
        _dodgeOut.dir = candB
        return
      }
      // Tie — fall through with both safe; the shared base-closer tail
      // below breaks the tie (same as the binary path).
      _dodgeOut.safeA = true
      _dodgeOut.safeB = true
    } else if (passA) {
      _dodgeOut.dir = candA
      return
    } else {
      _dodgeOut.dir = candB
      return
    }
  }
  // Neither perpendicular passable → fall through to the pinned logic.
}

/**
 * Default-path strategies (§3.8 extraction): §223 centroid escape, §201
 * escape-depth probe, then the legacy binary safe scan → _dodgeOut.
 */
function dodgeDefaultStrategies(
  self: GodAIInput,
  w: World,
  bullet: Bullet,
  p: Tank,
  pcx: number,
  pcy: number,
  vertical: boolean,
  candA: Direction,
  candB: Direction,
): void {
  _dodgeOut.dir = null
  _dodgeOut.safeA = false
  _dodgeOut.safeB = false
  // §223: multi-bullet centroid escape (dodgeCentroidMode). The
  // counterfactual-dodge hard-away arm survived 75.3% of dodge-death
  // windows vs 0% factual — running away from the CENTROID of the bullet
  // cluster beats dodging the single nearest bullet. Active only when ≥2
  // enemy bullets threaten the immediate vicinity; single-bullet and
  // no-bullet situations fall through to the legacy path (byte-identical).
  if (self.params.dodgeCentroidMode > 0) {
    self._centroidChecks++
    let cSumX = 0
    let cSumY = 0
    let cN = 0
    const bullets = w.bullets
    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (!b.alive || b.isPlayer) continue
      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      if (manhattan(bcx, bcy, pcx, pcy) <= CENTROID_RADIUS_PX) {
        cSumX += bcx
        cSumY += bcy
        cN++
      }
    }
    if (cN >= 2) {
      self._centroidTriggers++
      const cx = cSumX / cN
      const cy = cSumY / cN
      let bestDir: Direction | null = null
      let bestScore = -Infinity
      for (let di = 0; di < ALL_DIRS.length; di++) {
        const d = ALL_DIRS[di]
        // §83 stays in force: never flee in the bullet's own travel
        // direction, and the centroid escape never advances INTO the
        // dodged bullet's lane (that is the oscillation counter-fire's
        // job; the perpendicular escape semantics are preserved).
        if (d === bullet.dir || d === opposite(bullet.dir)) continue
        if (!self.canMoveDir(p, d)) continue
        if (!self.isSafeDir(pcx, pcy, d, bullet.id)) continue
        const v = DIR_VECTORS[d]
        const nx = pcx + v.dx * CELL
        const ny = pcy + v.dy * CELL
        if (self.hasBase) {
          const baseCx = BASE_CENTER_X_PX
          const baseCy = BASE_CENTER_Y_PX
          const distNow = manhattan(pcx, pcy, baseCx, baseCy)
          const distNext = manhattan(nx, ny, baseCx, baseCy)
          if (distNext > distNow + CENTROID_BASE_SLACK_CELLS * CELL) continue
        }
        const away = (nx - cx) * (nx - cx) + (ny - cy) * (ny - cy)
        if (away > bestScore) {
          bestScore = away
          bestDir = d
        }
      }
      if (bestDir) {
        self._centroidEscapes++
        _dodgeOut.dir = bestDir
        return
      }
    }
  }
  // §201: escape-depth-aware dodge — dead-end perpendiculars. When BOTH
  // perpendicular sides are shallow pockets (< dodgeEscapeDepth cells of
  // travel), the binary step-into-pocket dodge oscillates between them
  // while the enemy keeps firing (S14 hard s60: 93-tick up/down jitter,
  // hp 315→0 in a water-belt pocket). Probe the bullet-axis directions
  // for a genuinely longer escape and take it (safe-gated like every
  // dodge; §83's no-flee-in-bullet-axis rule applies to fleeing ALONG a
  // corridor the bullet traverses — the axis probe only fires when the
  // perpendicular pockets are dead ends and the axis move is clear).
  if (self.params.dodgeEscapeDepth > 0) {
    const depthA = escapeDepthImpl(self, pcx, pcy, candA, 10)
    const depthB = escapeDepthImpl(self, pcx, pcy, candB, 10)
    const minDepth = self.params.dodgeEscapeDepth
    if (depthA < minDepth && depthB < minDepth) {
      const axisA: Direction = vertical ? 'up' : 'left'
      const axisB: Direction = vertical ? 'down' : 'right'
      const depthAxisA = escapeDepthImpl(self, pcx, pcy, axisA, 10)
      const depthAxisB = escapeDepthImpl(self, pcx, pcy, axisB, 10)
      const takeAxis = (dir: Direction): boolean =>
        dir === bullet.dir
          ? false // §83: never flee in the bullet's travel direction
          : self.canMoveDir(p, dir) && self.isSafeDir(pcx, pcy, dir, bullet.id)
      const axisAName = axisA
      const axisBName = axisB
      if (depthAxisA >= minDepth && takeAxis(axisAName)) {
        if (depthAxisB >= minDepth && takeAxis(axisBName)) {
          // Both axes long — prefer the base-closer side (defense bias).
          const baseCx = BASE_CENTER_X_PX
          const baseCy = BASE_CENTER_Y_PX
          const va = DIR_VECTORS[axisAName]
          const vb = DIR_VECTORS[axisBName]
          const distA = manhattan(pcx + va.dx * CELL, pcy + va.dy * CELL, baseCx, baseCy)
          const distB = manhattan(pcx + vb.dx * CELL, pcy + vb.dy * CELL, baseCx, baseCy)
          _dodgeOut.dir = distA <= distB ? axisAName : axisBName
          return
        }
        _dodgeOut.dir = axisAName
        return
      }
      if (depthAxisB >= minDepth && takeAxis(axisBName)) {
        _dodgeOut.dir = axisBName
        return
      }
    }
  }
  // Try each candidate; prefer the one that's passable AND safe (M3).
  // Use local booleans instead of allocating an `open` array.
  if (self.canMoveDir(p, candA) && self.isSafeDir(pcx, pcy, candA, bullet.id))
    _dodgeOut.safeA = true
  if (self.canMoveDir(p, candB) && self.isSafeDir(pcx, pcy, candB, bullet.id))
    _dodgeOut.safeB = true

  // If no safe candidate, try passable but unsafe.
  if (!_dodgeOut.safeA && !_dodgeOut.safeB) {
    if (self.canMoveDir(p, candA)) _dodgeOut.safeA = true
    if (self.canMoveDir(p, candB)) _dodgeOut.safeB = true
  }
}

export function dodgeDirectionImpl(
  self: GodAIInput,
  bullet: Bullet,
  pcx: number,
  pcy: number,
): Direction | null {
  const w = self.world
  const p = self.controlledTank(w)!
  const vertical = bullet.dir === 'up' || bullet.dir === 'down'
  // Use module-level constants instead of allocating arrays on every dodge.
  const candA: Direction = vertical ? 'left' : 'up'
  const candB: Direction = vertical ? 'right' : 'down'

  // M0.5 退役（2026-08-03）: dodgeDirPersistence（同威胁保持闪避方向）已退役
  // 归档（A/B -1.7pp，从未发布）。

  // §86: Oscillation detection + counter-fire — see dodgeOscillationDir.
  // A/B: threshold=3 is -0.8pp net. threshold=2 is -0.9pp.
  // threshold=3+distance_gate is -1.4pp. persistence is -1.7pp.
  // hysteresis is -1.1pp. floorSnap is -2.6pp.
  const oscillationDir = dodgeOscillationDir(self, bullet)
  if (oscillationDir) return oscillationDir

  // ---- Strategy dispatch (mutually exclusive A/B arms) ----
  if (self.params.dodgeHorizonScore > 0) {
    dodgeHorizonCommit(self, w, bullet, p, pcx, pcy, candA, candB)
    if (_dodgeOut.dir) return _dodgeOut.dir
  } else if (self.params.dodgeClearanceScore > 0) {
    dodgeClearanceCommit(self, bullet, p, pcx, pcy, candA, candB)
    if (_dodgeOut.dir) return _dodgeOut.dir
  } else {
    dodgeDefaultStrategies(self, w, bullet, p, pcx, pcy, vertical, candA, candB)
    if (_dodgeOut.dir) return _dodgeOut.dir
  }
  const safeA = _dodgeOut.safeA
  const safeB = _dodgeOut.safeB

  // If still nothing [no perpendicular dodge passable], the player is pinned
  // in a corridor aligned with the bullet. §83: NEVER flee in the bullet's own
  // travel direction — the bullet is faster, so fleeing down the corridor in
  // its wake is futile death (reproduced in classic-s02 seed 1785636440494
  // @00:27: player fled 'down' for 39 ticks, bullet overtook it). Instead,
  // prefer turning TOWARD the bullet (the opposite of its travel): the player
  // faces the incoming bullet and the T5 fire logic cancels it (对枪抵消). This
  // dominates fleeing in BOTH cases: when not on cooldown the player cancels
  // and survives; when on cooldown the player at least FACES the bullet so the
  // instant its shot resolves it fires to cancel (fleeing faces AWAY → the
  // cooldown-end shot goes the wrong way → certain death). Only fall back to
  // the flee direction as an absolute last resort (when toward is blocked too).
  if (!safeA && !safeB) {
    const fleeDir: Direction = bullet.dir
    const towardDir: Direction = opposite(bullet.dir)
    const baseCx = BASE_CENTER_X_PX
    const baseCy = BASE_CENTER_Y_PX
    // First pass: open directions EXCLUDING the futile flee direction. Prefer
    // towardDir, then (for hasBase) the one closest to the base.
    let bestDist = Infinity
    for (let di = 0; di < ALL_DIRS.length; di++) {
      const d = ALL_DIRS[di]
      if (d === fleeDir) continue
      if (!self.canMoveDir(p, d)) continue
      // Toward the bullet wins outright (it enables counter-fire).
      if (d === towardDir) {
        bestDist = -1
        break
      }
      if (self.hasBase) {
        const vd = DIR_VECTORS[d]
        const dist = manhattan(pcx + vd.dx * CELL, pcy + vd.dy * CELL, baseCx, baseCy)
        if (dist < bestDist) bestDist = dist
      } else {
        bestDist = 0 // no base — first non-flee open direction
      }
    }
    if (bestDist < Infinity) {
      if (bestDist === -1) return towardDir
      for (let di = 0; di < ALL_DIRS.length; di++) {
        const d = ALL_DIRS[di]
        if (d === fleeDir) continue
        if (!self.canMoveDir(p, d)) continue
        if (self.hasBase) {
          const vd = DIR_VECTORS[d]
          const dist = manhattan(pcx + vd.dx * CELL, pcy + vd.dy * CELL, baseCx, baseCy)
          if (dist === bestDist) return d
        } else {
          return d
        }
      }
    }
    // Last resort: any open direction (including the flee direction — any
    // movement beats standing still when the player truly cannot turn toward).
    for (let di = 0; di < ALL_DIRS.length; di++) {
      if (self.canMoveDir(p, ALL_DIRS[di])) return ALL_DIRS[di]
    }
    return null
  }

  // We have at least one perpendicular candidate (safeA or safeB).
  // Prefer the direction that keeps the player closer to the base.
  if (self.hasBase) {
    const baseCx = BASE_CENTER_X_PX
    const baseCy = BASE_CENTER_Y_PX
    const va = DIR_VECTORS[candA]
    const vb = DIR_VECTORS[candB]
    const distA = manhattan(pcx + va.dx * CELL, pcy + va.dy * CELL, baseCx, baseCy)
    const distB = manhattan(pcx + vb.dx * CELL, pcy + vb.dy * CELL, baseCx, baseCy)
    if (safeA && safeB) return distA <= distB ? candA : candB
    return safeA ? candA : candB
  }
  // No base — first safe candidate.
  return safeA ? candA : candB
}

/**
 * M3: Check if moving in direction `d` would put the player into another
 * bullet's trajectory (excluding the one we're already dodging).
 */
export function isSafeDirImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
  excludeBulletId: number,
): boolean {
  const w = self.world
  const v = DIR_VECTORS[dir]
  // Check the cell we'd move into.
  const newCx = pcx + v.dx * CELL
  const newCy = pcy + v.dy * CELL

  // Indexed loop (AGENTS §14.1): isSafeDir is called up to 2× per dodge
  // (candA + candB), and dodgeDirection runs whenever a threat is detected.
  // `for (const b of w.bullets)` allocates an iterator per call.
  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer || b.id === excludeBulletId) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    // §3.1 single-sourced lane geometry.
    if (bulletLaneDist(b.dir, bcx, bcy, newCx, newCy, BULLET_ALIGN_NEXT_CELL) >= 0) return false
  }
  return true
}

/**
 * §M3: clearance (ticks) of a dodge candidate — the MINIMUM arrival tick of
 * any enemy bullet (excluding the dodged one) at the cell the player would
 * occupy after moving one CELL in `dir`. Infinity = clear (no bullet
 * threatens the new cell). Higher = safer. Used by `dodgeDirectionImpl` when
 * `dodgeClearanceScore > 0` to pick the perpendicular side with the most
 * room — dodging into a cell where another bullet arrives in 2 ticks is a
 * crossfire death, dodging into one with 15 ticks of clearance is safe.
 * Not called when the param is 0 (byte-identical to pre-§M3).
 */
function dodgeClearanceTicksImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
  excludeBulletId: number,
): number {
  const w = self.world
  const v = DIR_VECTORS[dir]
  const newCx = pcx + v.dx * CELL
  const newCy = pcy + v.dy * CELL
  let minTicks = Infinity
  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer || b.id === excludeBulletId) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    // Same next-cell alignment gate as isSafeDirImpl (§3.1 single-sourced).
    const dist = bulletLaneDist(b.dir, bcx, bcy, newCx, newCy, BULLET_ALIGN_NEXT_CELL)
    if (dist < 0) continue
    const ticks = dist / b.speed
    if (ticks < minTicks) minTicks = ticks
  }
  return minTicks
}

/**
 * §M3-revisit round 3 (DECISIONS §101): TERRAIN-ONLY pinning. Returns true
 * only when BOTH perpendicular directions are impassable — the player is
 * physically boxed in (corridor/corner) and cannot dodge at all.
 *
 * Rationale (round-3 A/B): the timing-aware pinned gate (round 2) gained
 * +3.4pp chaos at 60-seed but regressed open-field stages (Twin Spires
 * 55→30%, Bastion 35→15%, Final Redoubt 95→80%): on open ground a bullet
 * too close to FULLY clear in time still benefits from a PARTIAL dodge
 * (each tick of sideways movement shrinks the hit window and keeps the
 * player mobile), while standing to counter-fire — which only works when
 * the player's 6px shot actually times out — turned those partial dodges
 * into stationary deaths. Corridor/corner pinning (the M-B maze seeds 2/16
 * failure mode) is always terrain-based, so the terrain gate covers every
 * case where the dodge is TRULY impossible, without ever standing still on
 * open ground.
 */
/**
 * M4: Check if there are other enemy bullets threatening the player's current
 * position (excludes the one we're already watching). Used as a safety gate
 * for emergency counter-fire: if crossfire is active, the player should keep
 * dodging (vertical movement) rather than standing to counter-fire, because
 * canceling one bullet leaves the player exposed to the others.
 *
 * Returns true when at least `threshold` other bullets are approaching the
 * player within `rangeCells` — i.e., crossfire is active.
 */
export function hasCrossFireBulletImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  excludeBulletId: number,
  rangeCells: number,
  threshold: number,
): boolean {
  const w = self.world
  const rangePx = rangeCells * CELL
  let count = 0
  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer || b.id === excludeBulletId) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    // §3.1 single-sourced lane geometry.
    const dist = bulletLaneDist(b.dir, bcx, bcy, pcx, pcy, TANK)
    if (dist < 0) continue
    if (dist <= rangePx) {
      if (++count >= threshold) return true
    }
  }
  return false
}

export function isTerrainPinnedImpl(self: GodAIInput, p: Tank, bullet: Bullet): boolean {
  const vertical = bullet.dir === 'up' || bullet.dir === 'down'
  const candA: Direction = vertical ? 'left' : 'up'
  const candB: Direction = vertical ? 'right' : 'down'
  return !self.canMoveDir(p, candA) && !self.canMoveDir(p, candB)
}

/**
 * §M3: direction to fire in order to CANCEL the incoming threat bullet
 * (对枪抵消 in the dodge branch), or null when counter-fire is not viable.
 *
 * Returns `opposite(bullet.dir)` (face the bullet's source) only when:
 *   1. The bullet is closely aligned with the player center (lateral offset
 *      < `dodgeCounterFireAlignPx`) — the player's 6px bullet must actually
 *      collide with the enemy bullet (SimulationCombat.bulletHitsBullet:
 *      bullets cancel across opposing sides when their hitboxes overlap).
 *   2. The lane from the player to the bullet is clear (no brick/steel/base
 *      before the bullet's cell) — otherwise the player's shot hits the wall
 *      first and the cancellation never happens.
 *
 * Called from think()'s dodge branch when `dodgeCounterFire > 0` and the
 * threat is within the emergency counter-fire range — a hardcoded
 * `5 * CELL` gate at candidates/Dodge.ts (not a params field; the old
 * dodgeCounterFireRangeCells param was removed in §101).
 */
export function dodgeCounterFireDirImpl(
  self: GodAIInput,
  bullet: Bullet,
  pcx: number,
  pcy: number,
): Direction | null {
  const bcx = bullet.x + bullet.w / 2
  const bcy = bullet.y + bullet.h / 2
  const vertical = bullet.dir === 'up' || bullet.dir === 'down'
  const offset = vertical ? Math.abs(bcx - pcx) : Math.abs(bcy - pcy)
  if (offset > self.params.dodgeCounterFireAlignPx) return null

  const faceDir = opposite(bullet.dir)
  const p = self.controlledTank(self.world)
  if (!p) return null
  // Lane-clear check: walk from the player center toward the bullet center.
  const v = DIR_VECTORS[faceDir]
  const dist = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)
  for (let d = CELL; d < dist; d += CELL) {
    const fx = pcx + v.dx * d
    const fy = pcy + v.dy * d
    if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break
    const col = Math.floor(fx / CELL)
    const row = Math.floor(fy / CELL)
    const terrain = self.world.tileMap.get(col, row)
    if (terrain === 'brick' || terrain === 'steel' || terrain === 'base') return null
  }
  return faceDir
}

/**
 * §68-v2: Time-aware path threat projection.
 *
 * Scans the player's movement path from cell 1 to LOOKAHEAD cells ahead in
 * moveDir. For each cell, checks ALL enemy bullets (from target or non-target
 * enemies) using time-of-arrival estimation:
 *
 *   - Player arrives at cell i at tick:  i * CELL / playerSpeed
 *   - Player departs (clears TANK hitbox) at tick:  arrival + TANK / playerSpeed
 *   - Bullet arrives at cell i at tick:  dist / bullet.speed
 *   - Threat if bullet arrives before player departs the cell
 *
 * This replaces the old fixed-proximity approach (which used TANK or TANK*2
 * as a distance threshold). The time-aware check naturally adapts to bullet
 * speed: a fast bullet (4.2 px/tick) is flagged at a greater distance than
 * a slow one (3.6 px/tick), giving the player appropriate warning time.
 *
 * The current position (i=0) is NOT checked here — findMostDangerousBullet
 * in the dodge section of think() already handles it. This function only
 * catches threats to FUTURE positions: bullets the player would move INTO
 * by following moveDir.
 *
 * Returns the bullet with the earliest arrival time, or null if the path
 * is safe.
 */
const PATH_THREAT_LOOKAHEAD = 3

/** §165: the actual hitbox overlap threshold — (TANK + BULLET) / 2 = 19px.
 * A bullet can only hit the player if their center distance is < this.
 * The old `< TANK` (32px) flagged bullets up to 2 cells away — the primary
 * source of false positives on maze stages where bullets fly in adjacent
 * corridors that never actually cross the player's body. */
const PATH_THREAT_HIT_RADIUS = HIT_HALF_SPAN

export function findPathThreatImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  moveDir: Direction,
  playerSpeed: number,
): Bullet | null {
  const w = self.world
  const v = DIR_VECTORS[moveDir]
  const ps = playerSpeed > 0.1 ? playerSpeed : 1.0
  const grid = w.tileMap.grid

  let bestBullet: Bullet | null = null
  let bestThreatTick = Infinity

  const bullets = w.bullets
  for (let i = 1; i <= PATH_THREAT_LOOKAHEAD; i++) {
    const ccx = pcx + v.dx * i * CELL
    const ccy = pcy + v.dy * i * CELL

    const playerArrivalTick = (i * CELL) / ps
    // §165: the ±10 tick window is tighter than the actual overlap window
    // (±15 ticks at ps=1.1, bs=4), so it never causes false positives from
    // timing — only from spatial alignment and terrain occlusion.
    const threatWindow = 10
    const playerDepartureTick = playerArrivalTick + threatWindow
    const playerEnterTick = playerArrivalTick - threatWindow

    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (!b.alive || b.isPlayer) continue

      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      // §165 fix 1: tighten alignment from TANK (32px) to the actual hitbox
      // overlap threshold (19px). Bullets in adjacent corridors that never
      // cross the player's body are no longer flagged.
      // §3.1 single-sourced lane geometry.
      const dist = bulletLaneDist(b.dir, bcx, bcy, ccx, ccy, PATH_THREAT_HIT_RADIUS)
      if (dist < 0) continue
      const bulletArrivalTick = dist / b.speed

      if (bulletArrivalTick >= playerEnterTick && bulletArrivalTick <= playerDepartureTick) {
        // §165 fix 2: terrain occlusion — skip bullets whose path to the
        // crossing cell is blocked by steel. Steel is the ONLY terrain
        // that permanently blocks bullets — brick is destructible (bullets
        // break through and continue). On maze stages, many bullets are
        // behind steel walls and can never reach the player's path.
        // NOTE: brick is NOT checked — a bullet behind a brick wall will
        // destroy it and continue toward the player (real threat).
        const bv = DIR_VECTORS[b.dir]
        let terrainBlocked = false
        for (let d = CELL; d < dist; d += CELL) {
          const fx = bcx + bv.dx * d
          const fy = bcy + bv.dy * d
          if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break
          const tcol = Math.floor(fx / CELL)
          const trow = Math.floor(fy / CELL)
          const t = grid[trow][tcol]
          if (t === 'steel') {
            terrainBlocked = true
            break
          }
        }
        if (terrainBlocked) continue

        if (bulletArrivalTick < bestThreatTick) {
          bestThreatTick = bulletArrivalTick
          bestBullet = b
        }
      }
    }
  }

  return bestBullet
}

/**
 * §68-v2: Find a safe alternative movement direction.
 *
 * Called when findPathThreat detected a threat in the current movement
 * direction. Checks perpendicular and backward directions for immediate
 * safety (cell 1 only — not the full 3-cell path). This is less conservative
 * than checking the full path: a direction is accepted if the immediate next
 * cell is safe, even if farther cells have threats. The full path check will
 * run again next tick for the new direction.
 *
 * Returns the first safe direction, or null if no direction is safe.
 * Caller keeps the original direction when null is returned — never stops.
 */
export function findSafeMoveDirImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  threatenedDir: Direction,
  playerSpeed: number,
): Direction | null {
  const p = self.controlledTank(self.world)!
  const w = self.world
  const ps = playerSpeed > 0.1 ? playerSpeed : 1.0

  // Time window for cell 1 (immediate next cell) — same tight ±10 as findPathThreat
  const arrivalTick = CELL / ps
  const threatWin = 10
  const departTick = arrivalTick + threatWin
  const enterTick = arrivalTick - threatWin

  // Check if cell 1 in direction `dir` is safe from bullets
  function isCell1Safe(dir: Direction): boolean {
    const v = DIR_VECTORS[dir]
    const ccx = pcx + v.dx * CELL
    const ccy = pcy + v.dy * CELL
    const bullets = w.bullets
    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (!b.alive || b.isPlayer) continue
      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      // §3.1 single-sourced lane geometry.
      const dist = bulletLaneDist(b.dir, bcx, bcy, ccx, ccy, PATH_THREAT_HIT_RADIUS)
      if (dist < 0) continue
      const bat = dist / b.speed
      if (bat >= enterTick && bat <= departTick) return false
    }
    return true
  }

  const threatenedVertical = threatenedDir === 'up' || threatenedDir === 'down'
  const perpA: Direction = threatenedVertical ? 'left' : 'up'
  const perpB: Direction = threatenedVertical ? 'right' : 'down'
  const backward: Direction =
    threatenedDir === 'up'
      ? 'down'
      : threatenedDir === 'down'
        ? 'up'
        : threatenedDir === 'left'
          ? 'right'
          : 'left'

  if (self.canMoveDir(p, perpA) && isCell1Safe(perpA)) return perpA
  if (self.canMoveDir(p, perpB) && isCell1Safe(perpB)) return perpB
  if (self.canMoveDir(p, backward) && isCell1Safe(backward)) return backward

  return null
}

/**
 * §49: Check if there's an enemy bullet traveling toward the player in the
 * given direction's line of fire. Used by the armor "对枪" (trade-shots)
 * logic to decide whether to fire for bullet cancellation.
 *
 * Returns true if an enemy bullet is in the line, approaching the player,
 * within a reasonable distance.
 */
export function hasEnemyBulletInLineImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  aimDir: Direction,
): boolean {
  const w = self.world
  const vertical = aimDir === 'up' || aimDir === 'down'
  const bullets = w.bullets

  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.isPlayer) continue

    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2

    // Bullet must be roughly aligned with the player in the aimDir line
    const aligned = vertical ? Math.abs(bcx - pcx) < TANK : Math.abs(bcy - pcy) < TANK
    if (!aligned) continue

    // Bullet must be approaching the player (in front, heading toward player)
    const approaching =
      (aimDir === 'up' && b.dir === 'down' && bcy < pcy) ||
      (aimDir === 'down' && b.dir === 'up' && bcy > pcy) ||
      (aimDir === 'left' && b.dir === 'right' && bcx < pcx) ||
      (aimDir === 'right' && b.dir === 'left' && bcx > pcx)
    if (!approaching) continue

    // Within a reasonable distance (8 cells)
    const dist = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)
    if (dist < TANK * 8) return true
  }
  return false
}

/**
 * §153-W1: "wait for the bullet to clear" — predictive next-move collision guard.
 *
 * Root cause (hard S12 Lattice seed 3214953618, ~0:26, tick 1599): the player
 * was oscillating at x≈23.6 (col 1) while an enemy bullet ran straight DOWN
 * column 0 (box x≈[13,19], y passing the player's body). `findMostDangerousBullet`
 * did NOT flag it as a threat — the bullet's center had already crossed the
 * player's center in y (so `approaching` was false) and its box was in an
 * ADJACENT column (so it was "aligned" by the loose <TANK center test but not
 * actually striking). When the player then turned vertical, the turn-snap
 * pushed its left edge from x=24 to x=16 — INTO the bullet's lane — and it was
 * clipped (hp 315→187) while `threat` was still null.
 *
 * This helper answers the user's expected behavior literally: "wait for the
 * bullet to move away and become harmless before moving." It returns FALSE
 * (not clear → the navigate/hunt branch should HOLD, moving is unsafe) when
 * following `moveDir` for one tick would put the player's body ON an enemy
 * bullet — i.e. any enemy bullet's CURRENT box overlaps the player's body at
 * its NEXT position (one step along `moveDir`, with the off-axis coordinate
 * grid-snapped exactly like the axis-lock in SimulationCombat — the snap is
 * what drives the body into the adjacent lane in the t1599 case). Uses the
 * sim's own exclusive AABB (`aabb`) so a 0px edge touch never holds.
 *
 * §154 (net-negative diagnosis, DECISIONS §153 follow-up): the original
 * expanded-box version (any bullet within a px margin of the body) held the
 * player for 18 losing hard seeds — 17 were bullets on an axis PERPENDICULAR
 * to the intended move (a reactive-dodge concern, not a navigate hold: freezing
 * in crossfire is the §48 "fake dodge = stationary death" pattern), and S12-1
 * was a same-axis bullet the turn cooldown would have let pass anyway. The
 * predictive next-position check has none of those false positives: a bullet
 * that does not overlap the post-move body never holds. An axis filter was
 * measured and REJECTED (§154): perpendicular 1px grazes are protective in
 * corridor stages (S12 s5/s44/s57 flips-to-win) even though one similar graze
 * loses S1 s48 — the residual flips are the freeze-vs-hit context trade, not a
 * geometric artifact. `marginPx` stays at 1 (measured optimum: a 1px expansion
 * catches bullets within a tick of grazing the body; margin 0 loses the S12
 * protections, larger margins re-catch perpendicular near-misses).
 *
 * Return true = safe to move; false = an enemy bullet would collide with the
 * player's next-tick body (hold this tick). The call site gates on
 * `bulletLaneWait > 0`.
 */
export function bulletLaneClearImpl(
  self: GodAIInput,
  p: Tank,
  moveDir: Direction,
  marginPx = 1,
): boolean {
  if (!p.alive || !moveDir) return true
  const w = self.world
  const speed = p.speed || 2
  const vertical = moveDir === 'up' || moveDir === 'down'
  // Next-tick body: advance along moveDir, snap the off-axis coordinate to the
  // 16px grid (the axis-lock in SimulationCombat does exactly this on the
  // first move tick after a turn — the t1599 crash was this snap).
  const nx = vertical ? snap(p.x, CELL) : p.x + (moveDir === 'right' ? speed : -speed)
  const ny = vertical ? p.y + (moveDir === 'down' ? speed : -speed) : snap(p.y, CELL)
  const px = nx - marginPx
  const py = ny - marginPx
  const pw = (p.w || TANK) + 2 * marginPx
  const ph = (p.h || TANK) + 2 * marginPx
  const bullets = w.bullets
  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.isPlayer) continue
    // Same exclusive AABB semantics as the sim's bullet-tank collision
    // (SimulationCombat line ~546, `aabb`): a 0px edge touch is NOT a
    // collision — the original inclusive test held on exact edge touches
    // (S1 s48 @4723: bullet top == predicted body bottom) and froze the
    // player for a non-collision.
    if (!aabb(b.x, b.y, b.w, b.h, px, py, pw, ph)) continue
    // Enemy bullet box overlaps the player's predicted next body → unsafe.
    return false
  }
  return true
}

/**
 * §153-W2: fire-rate comparison for close combat.
 *
 * "当与敌人近距离缠斗、无足够时间躲开敌人可能发出的子弹时：若玩家开火频率高于
 * 目标敌人 → 走到与它对齐的行/列对枪（duel）；若低于目标敌人 → 躲到安全位置。"
 *
 * Fire rate ∝ 1/cooldown-inverval: lower configured interval = higher rate.
 * Uses `nextFireInterval` (the configured cadence) falling back to the current
 * `fireCooldown` when unavailable. Returns true when the PLAYER fires faster
 * than `enemy` (a stand-and-duel is a winning trade).
 */
export function playerFasterThanImpl(p: Tank, enemy: Tank): boolean {
  const pCd =
    (p as unknown as { nextFireInterval?: number }).nextFireInterval ??
    (p as unknown as { fireCooldown?: number }).fireCooldown ??
    Infinity
  const eCd =
    (enemy as unknown as { nextFireInterval?: number }).nextFireInterval ??
    (enemy as unknown as { fireCooldown?: number }).fireCooldown ??
    Infinity
  // Lower cooldown interval = faster firing. Player faster ⇔ player interval
  // is strictly smaller than the enemy's.
  return pCd < eCd
}

/**
 * §153-W2: find the closest aligned enemy tank in `dangerDir` within `rangeCells`
 * with no wall between (scanAhead sees the enemy). Used to read its fire rate.
 */
export function findCloseEnemyImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dangerDir: Direction,
  rangeCells: number,
): Tank | null {
  const w = self.world
  const tanks = w.tanks
  const rangePx = rangeCells * CELL
  const vertical = dangerDir === 'up' || dangerDir === 'down'
  let best: Tank | null = null
  let bestDist = Infinity
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue
    const tcx = t.x + t.w / 2
    const tcy = t.y + t.h / 2
    const aligned = vertical ? Math.abs(tcx - pcx) < TANK : Math.abs(tcy - pcy) < TANK
    if (!aligned) continue
    // Must be in the dangerDir half-plane.
    if (dangerDir === 'up' && tcy >= pcy) continue
    if (dangerDir === 'down' && tcy <= pcy) continue
    if (dangerDir === 'left' && tcx >= pcx) continue
    if (dangerDir === 'right' && tcx <= pcx) continue
    const dist = vertical ? Math.abs(tcy - pcy) : Math.abs(tcx - pcx)
    if (dist > rangePx) continue
    // No wall between player and enemy (scanAhead finds the enemy first).
    const scan = scanAheadImpl(self, pcx, pcy, dangerDir)
    if (!scan.enemy) continue
    if (dist < bestDist) {
      bestDist = dist
      best = t
    }
  }
  return best
}

/**
 * §153-W2: pick a safe perpendicular dodge direction (relative to `dangerDir`).
 * Only cell-1 passability + bullet safety counts — none safe returns null.
 */
export function safePerpDodgeImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dangerDir: Direction,
): Direction | null {
  const p = self.controlledTank(self.world)!
  const verticalDanger = dangerDir === 'up' || dangerDir === 'down'
  const perpA: Direction = verticalDanger ? 'left' : 'up'
  const perpB: Direction = verticalDanger ? 'right' : 'down'
  if (self.canMoveDir(p, perpA) && self.isSafeDir(pcx, pcy, perpA, -1)) return perpA
  if (self.canMoveDir(p, perpB) && self.isSafeDir(pcx, pcy, perpB, -1)) return perpB
  return null
}

/**
 * §85: Close-range enemy exposure check — is the player about to turn its
 * back on a close enemy that could fire and kill it before it can dodge?
 *
 * The navigate branch only checks for BULLET threats (findPathThreat). But
 * an enemy tank that is aligned with the player (same row/col), close
 * (within `range` cells), and has no wall between them can fire at any
 * moment. If the player's moveDir moves it ALONG the enemy's line of fire
 * (not perpendicular — a perpendicular move would be a dodge), the player
 * is exposed: the enemy fires, the bullet is faster, and the player gets
 * hit in the back.
 *
 * This function returns the direction the player should face to engage the
 * threatening enemy (stop-and-fire), or null if no close-range exposure
 * is detected.
 *
 * Condition for "exposed":
 *   1. Enemy within `range` cells (Manhattan distance in the scan axis)
 *   2. Enemy aligned with the player (same row or col, within TANK px)
 *   3. No wall/steel between player and enemy (scanAhead finds enemy, not wall)
 *   4. The player's moveDir is NOT toward the enemy (turning away)
 *
 * When exposed, the player should stop and fire at the enemy instead of
 * moving away. If the enemy is in a different direction than the player's
 * current facing, the player should turn to face the enemy.
 */
export function closeCombatExposureImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  moveDir: Direction | null,
  range: number,
): Direction | null {
  if (!moveDir) return null
  const w = self.world
  const tanksArr = w.tanks
  const rangePx = range * CELL

  for (let ti = 0; ti < tanksArr.length; ti++) {
    const t = tanksArr[ti]
    if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue

    const tcx = t.x + t.w / 2
    const tcy = t.y + t.h / 2
    const dx = tcx - pcx
    const dy = tcy - pcy

    // Check alignment: same row or col (within TANK px)
    let enemyDir: Direction | null = null
    let scanDist = 0
    if (Math.abs(dx) < TANK) {
      if (dy < 0) {
        enemyDir = 'up'
        scanDist = -dy
      } else {
        enemyDir = 'down'
        scanDist = dy
      }
    } else if (Math.abs(dy) < TANK) {
      if (dx < 0) {
        enemyDir = 'left'
        scanDist = -dx
      } else {
        enemyDir = 'right'
        scanDist = dx
      }
    }
    if (!enemyDir) continue
    if (scanDist > rangePx) continue

    // Check no wall between player and enemy (scanAhead finds enemy)
    const scan = scanAheadImpl(self, pcx, pcy, enemyDir)
    if (!scan.enemy) continue

    // Check if moveDir is NOT toward the enemy — the player is turning away.
    // "Toward the enemy" = same direction as enemyDir (closing distance — safe).
    // Perpendicular moves are dodges — also safe (the player clears the
    // enemy's line of fire before a bullet can arrive).
    // Only FLEEING (moving in the opposite direction = exposing the back)
    // is the dangerous case the check is designed to prevent.
    if (moveDir === enemyDir) continue // moving toward enemy — safe
    if (moveDir !== opposite(enemyDir)) continue // perpendicular — dodge, safe

    // The player is fleeing from a close enemy with a clear shot.
    // Return the direction to face the enemy and fire instead.
    return enemyDir
  }
  return null
}

/**
 * §165: count aligned enemies in a given direction within `rangeCells`.
 *
 * "Aligned" = same row (for left/right) or same column (for up/down), within
 * TANK px center distance, with no wall between (scanAhead finds the enemy).
 * Used by the ENGAGE candidate to detect when the player is outgunned: 2+
 * aligned enemies in the scan direction means a stationary T2a duel is a
 * losing trade (the second enemy fires while the player is locked aiming at
 * the first). The player should keep moving to find a 1v1 angle instead.
 *
 * Pure World read — no RNG, no mutation. Returns the count.
 */
export function countAlignedEnemiesImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
  rangeCells: number,
): number {
  const w = self.world
  const tanks = w.tanks
  const rangePx = rangeCells * CELL
  const vertical = dir === 'up' || dir === 'down'
  // Wall occlusion: only count if the FIRST thing in the scan direction is an
  // enemy (no wall/steel between player and enemies). scanAheadImpl caches by
  // origin+dir, so this is a single call. If a wall is closer than all enemies,
  // none of them can fire at the player — return 0.
  const scan = scanAheadImpl(self, pcx, pcy, dir)
  if (!scan.enemy) return 0
  let count = 0
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue
    const tcx = t.x + t.w / 2
    const tcy = t.y + t.h / 2
    const aligned = vertical ? Math.abs(tcx - pcx) < TANK : Math.abs(tcy - pcy) < TANK
    if (!aligned) continue
    // Must be in the dir half-plane.
    if (dir === 'up' && tcy >= pcy) continue
    if (dir === 'down' && tcy <= pcy) continue
    if (dir === 'left' && tcx >= pcx) continue
    if (dir === 'right' && tcx <= pcx) continue
    const dist = vertical ? Math.abs(tcy - pcy) : Math.abs(tcx - pcx)
    if (dist > rangePx) continue
    count++
  }
  return count
}
