// ================================================================
// PowerUpSystem — extracted from the former SimulationPowerUps.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Behavior is byte-
// identical: bodies moved verbatim; `this.world` became `this.d.world`
// and cross-system calls go through the shared SimulationSystems
// registry wired once in Simulation's constructor.
// ================================================================
import {
  CELL,
  TANK,
  FIELD,
  POWERUP_TIMEOUT_MS,
  POWERUP_DURATION_MS,
  FENCE_STEEL_COUNT,
  FENCE_DURATION_FRAMES,
  BOAT_DURATION_MS,
  EMP_DURATION_MS,
  GRID,
  TICK_MS,
  BASE_POS,
  REPAIR_HEAL_AMOUNT,
} from '../constants'
import { SUPER_POWERUP_TYPES, POWERUP_TIERS, POWERUP_TIER_WEIGHTS } from '../config/powerups'
import { resolveProfile, profileToStats, PLAYER_PROGRESSION } from '../config/combat'
import { rollSpeedJitter } from '../config/speed'
import { hasStarPerk } from '../config/rules'
import { recordEnemyKill } from './KillPipeline'
import { findNearestFreeCell } from './GridQuery'
import { genId } from './World'
import { aabb } from '../utils/helpers'
import type { PowerUpType, Tank } from '../types'
import type { SimulationSystems } from './systems'

/**
 * In-grid cells forming the 1-tile-thick protective ring around the 2×2 base
 * (base at BASE_POS col/row). Shared by applyFencePowerUp (place steel) and
 * updateFence/ expireFence (revert to brick) so both always agree on which
 * cells are "the ring". The bottom edge (row BASE_POS.row + 2) is off-grid
 * (GRID=26, base at row 24), so the ring is the 3 in-grid sides (top + left +
 * right) — exactly where the original permanent fence placed its steel.
 */
function baseRingPositions(): Array<{ col: number; row: number }> {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const cells: Array<{ col: number; row: number }> = []
  const consider = (col: number, row: number) => {
    if (col >= 0 && col < GRID && row >= 0 && row < GRID) cells.push({ col, row })
  }
  // Top edge
  for (let c = bc - 1; c <= bc + 2; c++) consider(c, br - 1)
  // Left edge (mid rows only; corners already covered by top edge)
  consider(bc - 1, br)
  consider(bc - 1, br + 1)
  // Right edge (mid rows only)
  consider(bc + 2, br)
  consider(bc + 2, br + 1)
  return cells
}

/**
 * the Power-up System: drop construction (buildDrop /
 * rollPowerUpType), spawning (spawnPowerUp / spawnBuiltDrop / flushPendingDrops),
 * pickup updates, the applyPowerUp switch, and the fence / boat / repair helpers.
 *
 */

export class PowerUpSystem {
  constructor(private d: SimulationSystems) {}
  // ================================================================
  // Power-up System
  // ================================================================

  /**
   * True if a TANK-sized rect at (x,y) overlaps any enemy spawn point.
   * Drops must avoid spawn cells so a power-up never materialises on top of
   * an enemy entry point — otherwise it looks like it spawns inside an enemy
   * and becomes instantly unreachable / confusing.
   */
  private rectHitsSpawnPoint(x: number, y: number): boolean {
    const sps = this.d.world.enemySpawnPoints
    for (let i = 0; i < sps.length; i++) {
      if (aabb(x, y, TANK, TANK, sps[i].x, sps[i].y, TANK, TANK)) return true
    }
    return false
  }

  /**
   * Deterministic safety net for {@link buildDrop}'s random fallback: when
   * 20 random 32-aligned candidates all fail the terrain / spawn-point
   * check (very plausible on dense water / steel layouts), scan the entire
   * 32-aligned grid and return the cell nearest to (originX, originY) that
   * clears the same predicate the random fallback uses. No RNG is drawn, so
   * determinism is preserved on top of the existing random draws.
   *
   * Without this, buildDrop used to keep the last (blocked) coordinates and
   * a power-up would materialise on top of water / brick / steel / a spawn
   * cell — the "道具出现在水域上" bug.
   *
   * Scan is O(N²) with N=13 (13×13 = 169 32-aligned cells). Only invoked in
   * the rare exhaustion case, so cost is negligible.
   */
  private findFreeDropCell(originX: number, originY: number): { x: number; y: number } {
    const w = this.d.world
    // Scan skeleton shared with World.findFreeSpawnCell via GridQuery (§2.3).
    // Drops require terrain-clear AND off the spawn points — tanks allowed.
    return findNearestFreeCell(
      originX,
      originY,
      (gx, gy) => !w.rectHitsTerrain(gx, gy, TANK, TANK) && !this.rectHitsSpawnPoint(gx, gy),
    )
  }

  /**
   * Build a drop descriptor (type + terrain-safe position). The `world.rng`
   * pick happens HERE so a buffered drop is fully resolved and deterministic —
   * flushing later only materialises it (no extra RNG consumption).
   *
   * Position randomization: applies a weighted random offset from the enemy's
   * position based on `rules.dropPositionWeights` (50/30/20 near/mid/far).
   * The offset is snapped to grid-aligned cells and falls back to a random
   * clear tile if the offset position is blocked (terrain/out-of-bounds).
   */
  buildDrop(at?: { x: number; y: number }): {
    type: PowerUpType
    x: number
    y: number
  } {
    const w = this.d.world
    const type = this.rollPowerUpType()

    // --- Position: weighted random offset from enemy position ---
    // Roll a tier (near/mid/far) from the configured weights, then pick a
    // random direction and distance within that tier's range. All randomness
    // flows through world.rng → deterministic / snapshot-safe.
    let x = 0
    let y = 0
    let placed = false

    if (at) {
      // Roll tier: near(0.5) / mid(0.3) / far(0.2)
      const weights = w.rules.dropPositionWeights
      const ranges = w.rules.dropPositionRanges
      const totalWeight = weights.near + weights.mid + weights.far

      // Guard: if all weights are 0, no offset — use exact enemy position.
      let tierRange = 0
      if (totalWeight > 0) {
        const roll = w.rng.next() * totalWeight
        if (roll < weights.near) {
          tierRange = ranges.near
        } else if (roll < weights.near + weights.mid) {
          tierRange = ranges.mid
        } else {
          tierRange = ranges.far
        }
      }

      // Pick a random direction (4 cardinal) and distance (1..tierRange cells).
      const dirs = [
        { dx: 1, dy: 0 },
        { dx: -1, dy: 0 },
        { dx: 0, dy: 1 },
        { dx: 0, dy: -1 },
      ]
      const dirIdx = Math.floor(w.rng.next() * 4)
      const dir = dirs[dirIdx]
      const dist = 1 + Math.floor(w.rng.next() * tierRange)

      // Snap anchor to grid — tank.x/y may not be grid-aligned after
      // movement (on-axis coordinate is not snapped, only off-axis).
      const anchorX = Math.round(at.x / CELL) * CELL
      const anchorY = Math.round(at.y / CELL) * CELL
      const clampedX = Math.max(0, Math.min(FIELD - TANK, anchorX + dir.dx * dist * CELL))
      const clampedY = Math.max(0, Math.min(FIELD - TANK, anchorY + dir.dy * dist * CELL))

      if (
        !w.rectHitsTerrain(clampedX, clampedY, TANK, TANK) &&
        !this.rectHitsSpawnPoint(clampedX, clampedY)
      ) {
        x = clampedX
        y = clampedY
        placed = true
      }
    }

    // Fallback: random clear tile that is also clear of enemy spawn points.
    // Keep the last valid position so we never materialise a drop on terrain
    // or a spawn cell even if some random candidates were blocked. If all 20
    // random tries are blocked (a dense water / steel layout CAN swallow
    // every candidate), fall through to a deterministic nearest-free-cell
    // scan so the drop still lands on walkable ground. Without that safety
    // net we used to retain the last (blocked) coordinates and materialise
    // a power-up on top of water / brick / steel — the "道具出现在水域上"
    // bug. The scan draws no RNG, so determinism is preserved on top of the
    // 20 random draws already consumed.
    if (!placed) {
      let tries = 0
      let lastValid: { x: number; y: number } | null = null
      do {
        x = w.rng.int(12) * 2 * CELL
        y = w.rng.int(12) * 2 * CELL
        tries++
        const blocked = w.rectHitsTerrain(x, y, TANK, TANK) || this.rectHitsSpawnPoint(x, y)
        if (!blocked) lastValid = { x, y }
      } while (tries < 20 && (w.rectHitsTerrain(x, y, TANK, TANK) || this.rectHitsSpawnPoint(x, y)))
      if (lastValid) {
        x = lastValid.x
        y = lastValid.y
      } else {
        const origin = at ?? { x: FIELD / 2 - TANK / 2, y: FIELD / 2 - TANK / 2 }
        const safe = this.findFreeDropCell(origin.x, origin.y)
        x = safe.x
        y = safe.y
      }
    }

    return { type, x, y }
  }

  /**
   * Pick a power-up type for a drop (DECISIONS.md §31). Every drop source
   * (elite / every-10-kills / every-5000-pts / bonus) funnels through here, so
   * the 10% super-item chance is uniform across all of them. A super drop rolls
   * equally among `SUPER_POWERUP_TYPES` (frenzy / sacrifice / guard / rewind).
   * All randomness comes from `world.rng` → deterministic.
   */
  rollPowerUpType(): PowerUpType {
    const w = this.d.world
    const r = w.rules

    // Only modern mode uses the 3-tier weighted system (plan §3.1).
    // Classic uses fixedDropKillIndices and does not run this path.
    if (r.dropSchedule === 'modern') {
      // Build active tiers (super only if superDropChance > 0)
      const activeTiers: { name: string; items: PowerUpType[]; weight: number }[] = []
      // The 3-tier system (plan §3.1) defines the drop pool directly via
      // POWERUP_TIERS — these lists are the source of truth, NOT
      // r.allowedPowerups (which only gates classic-vs-modern and the old
      // single pool). Filtering the SUPER tier against allowedPowerups would
      // DROP every 强力道具 in modern mode, because super items are
      // intentionally absent from allowedPowerups — they are inventory/stock
      // items, not instant pickups. So we use the tier lists verbatim and only
      // apply the (unchanged) water-gate to the boat.
      const superItems = POWERUP_TIERS.super
      if (r.superDropChance > 0 && superItems.length > 0) {
        activeTiers.push({ name: 'super', items: superItems, weight: POWERUP_TIER_WEIGHTS.super })
      }
      const practicalItems = POWERUP_TIERS.practical
      if (practicalItems.length > 0) {
        activeTiers.push({
          name: 'practical',
          items: practicalItems,
          weight: POWERUP_TIER_WEIGHTS.practical,
        })
      }
      let normalItems = POWERUP_TIERS.normal
      if (!w.tileMap.hasWater()) {
        normalItems = normalItems.filter((t) => t !== 'boat')
      }
      if (normalItems.length > 0) {
        activeTiers.push({
          name: 'normal',
          items: normalItems,
          weight: POWERUP_TIER_WEIGHTS.normal,
        })
      }

      // Normalize weights across active tiers and pick one tier
      const totalWeight = activeTiers.reduce((s, t) => s + t.weight, 0)
      if (totalWeight <= 0) {
        // Fallback: pick from allowedPowerups directly
        let pool = r.allowedPowerups
        if (!w.tileMap.hasWater()) pool = pool.filter((t) => t !== 'boat')
        return w.rng.pick(pool)
      }
      const roll = w.rng.next() * totalWeight
      let cumulative = 0
      for (const tier of activeTiers) {
        cumulative += tier.weight
        if (roll < cumulative) {
          return w.rng.pick(tier.items)
        }
      }
      return w.rng.pick(activeTiers[activeTiers.length - 1].items)
    }

    // Classic path: use SUPER_POWERUP_TYPES directly (unchanged)
    if (r.superDropChance > 0 && w.rng.next() < r.superDropChance) {
      return w.rng.pick(SUPER_POWERUP_TYPES)
    }
    let pool = r.allowedPowerups
    if (!w.tileMap.hasWater()) {
      pool = pool.filter((t) => t !== 'boat')
    }
    return w.rng.pick(pool)
  }

  /** Spawn a power-up immediately at the given (or random) position. */
  spawnPowerUp(at?: { x: number; y: number }): void {
    this.spawnBuiltDrop(this.buildDrop(at))
  }

  private spawnBuiltDrop(d: { type: PowerUpType; x: number; y: number }): void {
    const w = this.d.world
    w.addPowerUp({
      id: genId(),
      type: d.type,
      x: d.x,
      y: d.y,
      w: TANK,
      h: TANK,
      alive: true,
      blinkTimer: 0,
      lifeTimer: 0,
    })
  }

  /** Release every drop deferred from a previous stage (item-drop v1). */
  flushPendingDrops(): void {
    const w = this.d.world
    if (w.pendingDrops.length === 0) return
    const hasWater = w.tileMap.hasWater()
    for (const d of w.pendingDrops) {
      // Don't materialise a boat drop on a stage with no water (it would be
      // useless and look like a bug). Deferred drops keep their already-rolled
      // type/position, so we just skip them here rather than re-rolling.
      if (d.type === 'boat' && !hasWater) continue
      this.spawnBuiltDrop(d)
    }
    w.pendingDrops = []
  }

  updatePowerUps(): void {
    const w = this.d.world
    const dt = TICK_MS

    const pus = w.powerUps
    for (let i = 0; i < pus.length; i++) {
      const pu = pus[i]
      if (!pu.alive) continue
      pu.blinkTimer += dt
      pu.lifeTimer += dt

      // Despawn power-up after timeout
      if (pu.lifeTimer >= POWERUP_TIMEOUT_MS) {
        pu.alive = false
        w._needsCleanup = true
        continue
      }

      // Check player1 pickup
      const p1 = w.player
      if (p1 && p1.alive && aabb(p1.x, p1.y, p1.w, p1.h, pu.x, pu.y, pu.w, pu.h)) {
        pu.alive = false
        w._needsCleanup = true
        this.applyPowerUp(pu.type, p1)
        w.score += w.rules.itemScore
        w.pushEvent({ type: 'powerup_collected', powerUp: pu.type, by: 'player' })
        continue
      }
      // Check player2 pickup (Lie-Back-Win-Mode §3.1)
      const p2 = w.player2
      if (p2 && p2.alive && aabb(p2.x, p2.y, p2.w, p2.h, pu.x, pu.y, pu.w, pu.h)) {
        pu.alive = false
        w._needsCleanup = true
        this.applyPowerUp(pu.type, p2)
        w.score2 += w.rules.itemScore
        w.pushEvent({ type: 'powerup_collected', powerUp: pu.type, by: 'player' })
      }
    }
  }

  applyPowerUp(type: PowerUpType, collector?: Tank): void {
    const w = this.d.world
    const p = collector ?? w.player
    if (!p) return
    const isP1 = p === w.player

    switch (type) {
      case 'star':
        this.applyStarPowerUp(p, isP1)
        break

      case 'bomb':
        // Destroy all enemies on screen
        for (const tank of w.tanks) {
          if (!tank.alive) continue
          tank.alive = false
          w._needsCleanup = true
          this.d.effects.createExplosion(tank.x + tank.w / 2, tank.y + tank.h / 2, 'big')
          recordEnemyKill(w, tank)
        }
        break

      case 'shield':
        // Timed buff: accumulate duration so picking up another while one is
        // active stacks (e.g. 3s left + 20s = 23s). See DECISIONS.md §33.
        p.shieldTimer = (p.shieldTimer ?? 0) + POWERUP_DURATION_MS
        break

      case 'freeze':
        // Timed buff: accumulate duration (same rule as shield). Freezing all
        // enemies again adds a full POWERUP_DURATION_MS on top of any remaining.
        w.freezeTimer = w.freezeTimer + POWERUP_DURATION_MS
        break

      case 'tank':
        // Q1: lives go to the collector's pool (§3.1)
        if (isP1) w.lives++
        else w.lives2++
        break

      case 'fence':
        // Place steel tiles around the base (eagle) to protect it
        this.applyFencePowerUp()
        break

      case 'boat':
        // Grant amphibious movement to the COLLECTOR (拾取坦克): traverses water/ice.
        this.applyBoatPowerUp(collector)
        break

      // ---- New power-ups (new-powerups-plan) ----
      case 'repair':
        // Heal the COLLECTOR (拾取坦克) by a fixed amount — not a full restore (§189).
        this.applyRepairPowerUp(collector)
        break

      case 'emp':
        // Freeze all enemies for EMP_DURATION_MS (accumulates on re-pickup)
        w.empTimer += EMP_DURATION_MS
        break

      case 'rewind':
        // Add one rewind stock (accumulated); activated with F7
        w.rewindStock++
        break

      case 'decoy':
        // Spawn an ally decoy that attracts enemy fire
        this.d.player.activateDecoy(p)
        break

      case 'mine':
        // Place a mine at the player's current position
        this.d.player.placeMine(p)
        break

      // ---- Super power-ups (强力道具, DECISIONS.md §31) ----
      // Picked up into an inventory (accumulated), not applied instantly.
      case 'guard':
        // 天降神兵 — accumulate; released actively with F5 (Phase 2 summon).
        w.guardStock++
        break
      case 'frenzy':
        // 狂暴宣泄 — accumulate; released actively with F6.
        w.frenzyStock++
        break
      case 'sacrifice':
        // 同归于尽 — accumulate; released passively when a life is lost.
        w.sacrificeStock++
        break
    }
  }

  /**
   * Star pickup: universal player progression — every star raises ALL
   * capability dimensions together (plan §11). Re-derive the tank's concrete
   * stats from the new profile. Current HP is intentionally NOT refilled — a
   * star is power, not a repair.
   *
   * Classic mode caps the star *level* at maximumLevel; every other mode
   * accumulates WITHOUT bound (the per-star gain decays past the
   * balanced×150% threshold inside playerProfile). The cap is a
   * classic-only, pickup-time constraint.
   */
  private applyStarPowerUp(p: Tank, isP1: boolean): void {
    const w = this.d.world
    const atCap = w.difficultyKey === 'classic' && (p.level ?? 0) >= PLAYER_PROGRESSION.maximumLevel
    if (atCap) return
    p.level = (p.level ?? 0) + 1
    if (isP1) w.playerLevel = p.level
    else w.playerLevel2 = p.level
    const stats = profileToStats(resolveProfile('player', p.level), 'player', p.level, w.rules)
    p.speed = stats.speed * (w.rules.speedJitter ? rollSpeedJitter(this.d.world.rng) : 1)
    p.bulletSpeed = stats.bulletSpeed
    p.bulletPower = stats.bulletPower
    p.fireCooldown = stats.fireCooldown
    p.maxHp = stats.maxHp
    p.profile = resolveProfile('player', p.level)
    // Functional star ladder (classic only, plan Phase 3). Matches FC:
    // 1★ fast bullet → 2★ double-shot (realized by the bullet cap in
    // tryFire) → 3★ steel-pierce. Non-classic stays universal-growth.
    if (w.rules.starModel === 'functional') {
      // Perks are cumulative in FC (a 2★ tank keeps the fast bullet it
      // earned at 1★), so query across every level ≤ current, not just
      // the current level's introduced-perk list (see hasStarPerk).
      if (hasStarPerk(w.rules, p.level ?? 0, 'fastBullet')) {
        p.bulletSpeed = stats.bulletSpeed * w.rules.fastBulletMult
      }
      if (hasStarPerk(w.rules, p.level ?? 0, 'steelPierce')) {
        p.bulletPower = 2
      }
    }
  }

  applyFencePowerUp(): void {
    const w = this.d.world
    // Place a protective steel ring around the base (top + left + right sides;
    // the bottom edge is off-grid). The ring lasts FENCE_DURATION_FRAMES, then
    // reverts to brick in updateFence().
    //
    // §188→§189: Instead of skipping ring cells that overlap a tank (which
    // leaves gaps in the steel ring), FORCE-MOVE the tank to the nearest
    // clear position outside the ring before placing steel. This avoids
    // trapping the tank (S9@seed119: 532.7s stuck, game timeout) while
    // ensuring a complete ring. If no clear position is found, the cell
    // is still skipped as a safety net. allTanks includes player, player2,
    // allies, and enemies.
    const allTanks = w.allTanks
    let placed = 0
    for (const pos of baseRingPositions()) {
      if (placed >= FENCE_STEEL_COUNT) break
      const existing = w.tileMap.get(pos.col, pos.row)
      if (existing === 'empty' || existing === 'brick') {
        const cx = pos.col * CELL
        const cy = pos.row * CELL
        // Push any tanks overlapping this cell outside the ring.
        for (let ti = 0; ti < allTanks.length; ti++) {
          const t = allTanks[ti]
          if (!t.alive) continue
          if (aabb(cx, cy, CELL, CELL, t.x, t.y, t.w, t.h)) {
            this.pushTankOutsideRing(t, pos)
          }
        }
        // Re-check: is the cell now clear of tanks?
        let stillBlocked = false
        for (let ti = 0; ti < allTanks.length; ti++) {
          const t = allTanks[ti]
          if (!t.alive) continue
          if (aabb(cx, cy, CELL, CELL, t.x, t.y, t.w, t.h)) {
            stillBlocked = true
            break
          }
        }
        if (stillBlocked) continue
        w.tileMap.set(pos.col, pos.row, 'steel')
        placed++
      }
    }
    // Timed buff: accumulate duration rather than reset. If a fence ring is
    // already up, picking up another extends it by a full FENCE_DURATION_FRAMES
    // (same stacking rule as shield/freeze/boat). The steel ring is re-laid
    // idempotently over empty/brick cells, so re-applying is safe.
    w.fenceExpireFrame = (w.fenceExpireFrame ?? w.frame) + FENCE_DURATION_FRAMES
  }

  /**
   * §189: Push a tank to the nearest clear position outside the base ring.
   * Tries the primary direction (away from base center based on which ring
   * edge the cell is on) first, then falls back to the other cardinal
   * directions. Each direction is tried at increasing distances (TANK,
   * TANK+CELL, … up to TANK×4) until a collision-free position is found.
   * If no clear position exists in any direction, the tank is left in place
   * and the caller skips the steel for that cell.
   */
  private pushTankOutsideRing(tank: Tank, ringCell: { col: number; row: number }): void {
    const bc = BASE_POS.col
    const br = BASE_POS.row
    // Primary push direction based on which ring edge the cell is on.
    const primary: { dx: number; dy: number } =
      ringCell.row === br - 1
        ? { dx: 0, dy: -1 } // top edge → push up
        : ringCell.col === bc - 1
          ? { dx: -1, dy: 0 } // left edge → push left
          : ringCell.col === bc + 2
            ? { dx: 1, dy: 0 } // right edge → push right
            : { dx: 0, dy: -1 } // fallback → push up
    // Direction order: primary first, then the rest (deduped).
    const allDirs: Array<{ dx: number; dy: number }> = [primary]
    for (const d of [
      { dx: 0, dy: -1 },
      { dx: -1, dy: 0 },
      { dx: 1, dy: 0 },
      { dx: 0, dy: 1 },
    ]) {
      if (d.dx !== primary.dx || d.dy !== primary.dy) allDirs.push(d)
    }
    for (const dir of allDirs) {
      for (let dist = TANK; dist <= TANK * 4; dist += CELL) {
        const nx = tank.x + dir.dx * dist
        const ny = tank.y + dir.dy * dist
        if (this.isTankPositionClear(nx, ny, tank)) {
          tank.x = nx
          tank.y = ny
          return
        }
      }
    }
    // No clear position found — leave tank in place (cell will be skipped).
  }

  /**
   * §189: Check if a TANK-sized rect at (x,y) is free of terrain and other
   * tanks. Used by pushTankOutsideRing to find a safe teleport target.
   */
  private isTankPositionClear(x: number, y: number, excludeTank: Tank): boolean {
    const w = this.d.world
    // Bounds check
    if (x < 0 || x + TANK > FIELD || y < 0 || y + TANK > FIELD) return false
    // Terrain check — rectHitsTerrain blocks brick/steel/water/base
    // (identical set to the former inline loop; §2.3 dedup)
    if (w.rectHitsTerrain(x, y, TANK, TANK)) return false
    // Tank collision check
    const allTanks = w.allTanks
    for (let ti = 0; ti < allTanks.length; ti++) {
      const other = allTanks[ti]
      if (!other.alive || other === excludeTank) continue
      if (aabb(x, y, TANK, TANK, other.x, other.y, other.w, other.h)) {
        return false
      }
    }
    return true
  }

  /**
   * Tick the fence power-up: when its steel ring timer expires, revert the ring
   * cells that are still steel back to brick walls. Cells left as steel are the
   * ones the fence created; original brick/empty/steel terrain is untouched.
   */
  updateFence(): void {
    const w = this.d.world
    if (w.fenceExpireFrame === undefined) return
    if (w.frame < w.fenceExpireFrame) return
    for (const pos of baseRingPositions()) {
      if (w.tileMap.get(pos.col, pos.row) === 'steel') {
        w.tileMap.set(pos.col, pos.row, 'brick')
      }
    }
    w.fenceExpireFrame = undefined
  }

  private applyBoatPowerUp(collector?: Tank): void {
    const w = this.d.world
    const p = collector ?? w.player
    if (!p) return

    // Timed buff: accumulate duration (same rule as shield/freeze). Picking up
    // another boat while one is active extends amphibious movement. Applies to
    // the COLLECTOR (拾取坦克) — in coop that is player2, not always player1.
    p.boatTimer = (p.boatTimer ?? 0) + BOAT_DURATION_MS
  }

  /**
   * Apply Repair power-up: restore the COLLECTOR tank's HP by a fixed amount
   * (= one basic enemy bullet damage: firepower 50 × DAMAGE_SCALE 2 = 100).
   * §189: single pickup supplements HP by the 普通敌人一发炮弹伤害值, not a
   * full restore. Unlike 星星 (star) — which does NOT refill HP — Repair is
   * the dedicated healing item. Heals the 拾取坦克 (collector); in coop that
   * is player2, not always player1. (The eagle/base has its own HP and is unaffected.)
   */
  private applyRepairPowerUp(collector?: Tank): void {
    const w = this.d.world
    const p = collector ?? w.player
    if (!p) return
    p.hp = Math.min(p.hp + REPAIR_HEAL_AMOUNT, p.maxHp)
  }
}
