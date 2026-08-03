import type { GodAIInput } from '../GodAIInput'
import type { Tank, PowerUpType } from '../../types'
import { findPath, type Cell } from '../../utils/pathfind'
import { CELL, BASE_POS, POWERUP_TIMEOUT_MS, GRID } from '../../constants'
import { POWERUP_PRIORITY, kindThreatWeight } from './constants'
import type { GodAIParams } from './params'
import { enemyCanShootBase } from './SmartThreatModel'
import { blocksBullet } from './Chokepoint'

// ============================================================
// StrategyPlanner — target selection (S6 attack-defense), power-up
// economy (S5), and default defense positioning.
// Moved verbatim from GodAIInput.ts during the §0.5 split.
// Each `impl` takes `self: GodAIInput` so it can read shared state and
// call sibling methods via the public wrappers on GodAIInput.
// ============================================================

/**
 * S5a/S5c/NEW-Requirement-3: Find a power-up worth collecting.
 * Returns the target cell if a power-up is available and worth the risk,
 * null otherwise.
 *
 * NEW: Dynamic priority based on:
 *   - Power-up effect (bomb > star > freeze > fence > tank > shield/helmet > boat)
 *   - Travel distance (cost in time/opportunity)
 *   - Route danger (how many enemies are between player and power-up)
 */
export function findPowerUpTargetImpl(self: GodAIInput, pcx: number, pcy: number): Cell | null {
  const w = self.world
  const powerUps = w.powerUps
  if (powerUps.length === 0) return null

  // Reusable best-cell fields (perf §65): avoid allocating `{cell, score}`
  // per upgrade — track the best cell's (col,row) + score as scalars, and
  // return a single fresh Cell at the end. findPowerUpTarget is called up
  // to ~2× per think (normal + aggressive branches); each call used to
  // allocate up to N (1 per power-up) intermediate `{cell, score}` objects.
  let bestCol = 0
  let bestRow = 0
  let bestScore = -Infinity
  let hasBest = false

  // BONUS TIME (DECISIONS §84): the stage is cleared (last enemy destroyed)
  // and the pickup window is running — the player is in the "grab the bonus"
  // grace period (POWERUP_PICKUP_WINDOW_MS = 10s) before the stage auto-ends.
  // With nothing left to fight or defend, the normal divert-distance /
  // route-danger caps are lifted: the WHOLE field is fair game, and the only
  // scarce resource is TIME. Items are scored by despawn urgency first (an
  // item about to expire must be grabbed NOW), then nearest-first to maximize
  // how many items are collected inside the window, with a small priority
  // tie-break. Without this, an item farther than `powerupMaxDivertDistance`
  // cells was never targeted and the AI stood at the defense position while
  // the window expired (督战 spectate: the God player never collected the
  // bonus loot).
  const bonusWindow = w.pickupWindowEntered && w.pickupWindowTimer > 0

  for (let pi = 0; pi < powerUps.length; pi++) {
    const pu = powerUps[pi]
    if (!pu.alive) continue
    const cx = pu.x + pu.w / 2
    const cy = pu.y + pu.h / 2
    const dist = Math.round((Math.abs(cx - pcx) + Math.abs(cy - pcy)) / CELL)

    // S5d: if about to expire and too far, skip.
    const lifeRemaining = POWERUP_TIMEOUT_MS - pu.lifeTimer
    if (!bonusWindow && lifeRemaining < 3000 && dist > 5) continue

    // S5a: base priority by type.
    const priority = POWERUP_PRIORITY[pu.type] ?? 5

    let score: number
    // The cell the tank must drive to — the item's own cell normally, or an
    // overlapping passable neighbour when the item sits on blocking terrain.
    let collectCol = Math.floor(pu.x / CELL)
    let collectRow = Math.floor(pu.y / CELL)
    if (bonusWindow) {
      // §84-revisit (DECISIONS §85): skip items the tank can never reach
      // (steel/water-enclosed pockets). Chasing an unreachable item every
      // tick burns the whole window — `navigateTowards` always answers null
      // for it. Uses the exact same A* the navigator drives (corridors, then
      // dig-through-brick), so this matches what the tank can actually drive.
      // Pure function of World state — no RNG, no cache mutation.
      const collect = powerUpCollectCell(self, collectCol, collectRow)
      if (!collect) continue
      collectCol = collect.col
      collectRow = collect.row
      // Time-boxed loot run: an item with <5s of life left is in imminent
      // despawn danger — its urgency boost (up to 2000) outweighs any
      // distance/priority difference. Otherwise nearest-first (dist*10)
      // sweeps the field, with a mild priority tie-break ((6-priority)*10).
      const urgency = lifeRemaining < 5000 ? (5000 - lifeRemaining) / 2.5 : 0
      score = urgency - dist * 10 + (6 - priority) * 10
    } else {
      // NEW Requirement 3: Calculate route danger
      const dangerLevel = self.calculateRouteDanger(pcx, pcy, cx, cy)

      // Fix Bug 2: Score formula was reversed — priority * 1000 gave bomb
      // (priority=0) a base score of 0 and boat (priority=6) a base of 6000.
      // Now (6 - priority) * 1000 gives bomb the highest base score.
      score = (6 - priority) * 1000 - dist * 10 - dangerLevel * 500

      // Extra bonus for bomb (it clears the whole screen, worth high risk)
      if (pu.type === 'bomb') {
        score += 2000
      }

      // Extra bonus for star (permanent upgrade)
      if (pu.type === 'star') {
        score += 1000
      }

      // Penalty for boat (only situationally useful)
      if (pu.type === 'boat') {
        score -= 500
      }

      // Fix Bug 3: maxDist logic was reversed — high-value power-ups (bomb/star)
      // should allow LONGER diversion distance, not shorter.
      const maxDist = priority <= 1 ? 8 : self.params.powerupMaxDivertDistance
      if (dist > maxDist) continue

      // NEW: Don't collect if route is too dangerous unless it's a bomb/star
      if (dangerLevel > 3 && priority > 2) continue // Too dangerous for low-value power-ups
      if (dangerLevel > 5 && pu.type !== 'bomb') continue // Bomb is worth almost any risk
    }

    if (score > bestScore) {
      bestScore = score
      bestCol = collectCol
      bestRow = collectRow
      hasBest = true
    }
  }

  return hasBest ? { col: bestCol, row: bestRow } : null
}

/**
 * §87 (user request 2026-08-02): urgent power-up pickup — a CLOSE power-up
 * with a SAFE PATH outranks base defense (回防) and enemy-kill (杀敌)
 * targets.
 *
 * Categories (distance gates, tunable):
 *   HIGH — bomb/freeze/fence:   pickupPriorityHighRange cells (target 8)
 *   MID  — star/tank/shield:    pickupPriorityMidRange cells (target 4)
 *   LOW  — boat:                pickupPriorityLowRange cells (target 2)
 *
 * "Path safe" = route danger (enemies between the player and the item,
 * calculateRouteDanger) <= pickupPriorityMaxDanger (target 0) AND the item
 * is reachable via A* (powerUpCollectCell — the same corridor/dig path the
 * navigator drives; steel/water-enclosed pockets are skipped, never chased).
 *
 * Nearest-first, ties broken toward the higher-value item (lower
 * POWERUP_PRIORITY). Returns the collect cell (possibly an overlapping
 * passable neighbour) or null. Gated by pickupPriorityMode — the caller
 * (think) only invokes this when > 0, so OFF is byte-identical.
 */
/** §88 tier filter: HIGH = bomb/freeze/fence (+ modern emp/guard); MID/LOW = the rest. */
function urgentTier(type: PowerUpType): 'high' | 'midlow' {
  switch (type) {
    case 'bomb':
    case 'freeze':
    case 'fence':
    case 'emp':
    case 'guard':
      return 'high'
    default:
      return 'midlow'
  }
}

export function findUrgentPowerUpTargetImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  tier: 'all' | 'high' | 'midlow' = 'all',
): Cell | null {
  const w = self.world
  const powerUps = w.powerUps
  if (powerUps.length === 0) return null
  const p = self.params
  if (p.pickupPriorityMode <= 0) return null

  // Nearby-enemy gate (per-seed tick-diff finding, Lattice s2 / Battlement
  // s3): "path safe" must also mean no enemy is breathing down the player's
  // neck. The route-danger check below only counts enemies BETWEEN the player
  // and the item; an enemy 5 cells away (or an active firefight) was still
  // abandoned while the player walked to the item, then the player stalled
  // or stopped firing and died. Same radius as the S5 P3.2 gate (5 cells).
  if (p.pickupPriorityMinEnemyDist > 0) {
    const playerCol = Math.floor(pcx / CELL)
    const playerRow = Math.floor(pcy / CELL)
    // Cluster C: reuse the per-tick enemy snapshot (falls back to w.tanks).
    const nearbyScan = self._enemies.length > 0 ? self._enemies : w.tanks
    for (let ni = 0; ni < nearbyScan.length; ni++) {
      const t = nearbyScan[ni]
      if (!t.alive || t.spawnTimer > 0) continue
      const eCol = Math.floor(t.x / CELL)
      const eRow = Math.floor(t.y / CELL)
      if (Math.abs(eCol - playerCol) + Math.abs(eRow - playerRow) <= p.pickupPriorityMinEnemyDist) {
        return null
      }
    }
  }

  let bestCol = 0
  let bestRow = 0
  let bestDist = Infinity
  let bestPriority = Infinity
  let hasBest = false

  for (let pi = 0; pi < powerUps.length; pi++) {
    const pu = powerUps[pi]
    if (!pu.alive) continue
    const cx = pu.x + pu.w / 2
    const cy = pu.y + pu.h / 2
    // §88 tier gate: only consider items of the requested tier (default 'all'
    // = pre-§88 behavior, byte-identical).
    if (tier !== 'all' && urgentTier(pu.type) !== tier) continue
    const dist = Math.round((Math.abs(cx - pcx) + Math.abs(cy - pcy)) / CELL)
    const range = urgentPickupRange(pu.type, p)
    // Distance gate by category — the core of the tuning sweep.
    if (range <= 0 || dist > range) continue

    // Enemy spawn-zone gate (per-seed tick-diff finding, Lattice s2/s32):
    // items in the enemy spawn band (classic spawns at row 0) are traps —
    // the player dives in and meets fresh spawns. Never treat the band as
    // an urgent errand; S5 navigation can still fetch far items there.
    if (p.pickupPrioritySpawnRowMax > 0 && Math.floor(pu.y / CELL) <= p.pickupPrioritySpawnRowMax) {
      continue
    }

    // Path safety gate: no enemy between the player and the item.
    if (self.calculateRouteDanger(pcx, pcy, cx, cy) > p.pickupPriorityMaxDanger) continue

    // Reachability gate: the tank must be able to drive to a cell that
    // overlaps the item (same A* as the bonus window). Without this the
    // player would chase a steel/water-enclosed pocket every tick.
    const collect = powerUpCollectCell(self, Math.floor(pu.x / CELL), Math.floor(pu.y / CELL))
    if (!collect) continue

    const priority = POWERUP_PRIORITY[pu.type] ?? 5
    if (dist < bestDist || (dist === bestDist && priority < bestPriority)) {
      bestDist = dist
      bestPriority = priority
      bestCol = collect.col
      bestRow = collect.row
      hasBest = true
    }
  }

  return hasBest ? { col: bestCol, row: bestRow } : null
}

/** §87: distance gate (cells) for a power-up type. 0 = never urgent. */
function urgentPickupRange(type: PowerUpType, p: GodAIParams): number {
  switch (type) {
    case 'bomb':
    case 'freeze':
    case 'fence':
    case 'emp': // modern: crowd-control, freeze-like
    case 'guard': // modern: base protection, fence-like
      return p.pickupPriorityHighRange
    case 'boat':
      return p.pickupPriorityLowRange
    default:
      // star / tank / shield (+ modern extras rewind/repair/decoy/mine/…)
      return p.pickupPriorityMidRange
  }
}

/**
 * §84-revisit (DECISIONS §85): the tank-position cell from which a power-up
 * at (col,row) can be collected, or null when it is genuinely unreachable
 * (steel/water-enclosed pocket). The tank collects by OVERLAPPING the item's
 * TANK-sized rect, so the item's own cell works normally; when the item sits
 * on blocking terrain (a deferred drop materialized on another stage's
 * layout, fence steel placed over a drop), the tank parks on the nearest
 * overlapping passable neighbour instead — and THAT cell is what the caller
 * must navigate to (targeting the item's own impassable cell would re-create
 * the stuck state). Uses the exact same A* the navigator drives (corridor
 * paths, then dig-through-brick), so this matches what the tank can actually
 * drive. Pure function of World state: no RNG draws, no cache mutation.
 */
function powerUpCollectCell(self: GodAIInput, col: number, row: number): Cell | null {
  const pc = self.playerCell()
  if (col === pc.col && row === pc.row) return { col, row }
  // Item's own cell first — drops always land on passable ground, so this is
  // the overwhelmingly common case (1-2 A* runs per item).
  if (powerUpCellReachable(self, col, row)) return { col, row }
  // Wall/base-adjacent edge case: overlap the item from a neighbouring
  // passable tank position; prefer the one closest to the player.
  let bestCol = -1
  let bestRow = -1
  let bestDist = Infinity
  for (let dr = -1; dr <= 1; dr++) {
    for (let dc = -1; dc <= 1; dc++) {
      if (dr === 0 && dc === 0) continue
      const nc = col + dc
      const nr = row + dr
      if (nc === pc.col && nr === pc.row) return { col: nc, row: nr }
      if (!powerUpCellReachable(self, nc, nr)) continue
      const d = Math.abs(nc - pc.col) + Math.abs(nr - pc.row)
      if (d < bestDist) {
        bestDist = d
        bestCol = nc
        bestRow = nr
      }
    }
  }
  return bestCol >= 0 ? { col: bestCol, row: bestRow } : null
}

/** A* reachability from the player's cell to one specific tank-position cell. */
function powerUpCellReachable(self: GodAIInput, col: number, row: number): boolean {
  const pc = self.playerCell()
  const target: Cell = { col, row }
  const corridor = findPath(self.world.tileMap, pc, target)
  if (corridor && corridor.length > 0) return true
  const dig = findPath(self.world.tileMap, pc, target, { breakBrick: true })
  return !!dig && dig.length > 0
}

/**
 * NEW Requirement 3: Calculate how dangerous a route is.
 * Returns a danger level from 0 (safe) to N (many enemies on the path).
 *
 * (perf §65): eliminated the 3× `pxToCell` Cell allocations per call
 * (target/player/enemy cells). calculateRouteDanger is called once per
 * power-up candidate per think → with N power-ups and M enemies this was
 * up to N×M Cell allocations per think, ~3M allocs over a 30-game batch.
 * Scalar col/row locals are byte-identical (same Math.floor division).
 */
export function calculateRouteDangerImpl(
  self: GodAIInput,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
): number {
  const w = self.world
  let danger = 0

  // Simple heuristic: count enemies that are closer to the target than we are.
  // Inline pxToCell as scalar col/row — no Cell allocation.
  const targetCol = Math.floor(toX / CELL)
  const targetRow = Math.floor(toY / CELL)
  const playerCol = Math.floor(fromX / CELL)
  const playerRow = Math.floor(fromY / CELL)
  const playerDistToTarget = Math.abs(targetCol - playerCol) + Math.abs(targetRow - playerRow)

  // Cluster C: reuse the per-tick enemy snapshot (falls back to w.tanks).
  const dangerScan = self._enemies.length > 0 ? self._enemies : w.tanks
  for (let ti = 0; ti < dangerScan.length; ti++) {
    const t = dangerScan[ti]
    if (!t.alive || t.spawnTimer > 0) continue

    // Inline pxToCell(t.x, t.y) — scalar col/row, no Cell allocation.
    const enemyCol = Math.floor(t.x / CELL)
    const enemyRow = Math.floor(t.y / CELL)
    const enemyDistToTarget = Math.abs(targetCol - enemyCol) + Math.abs(targetRow - enemyRow)

    // If enemy is closer to target than player, and on the path, add danger
    if (enemyDistToTarget < playerDistToTarget) {
      // Check if enemy is roughly between player and target
      const dx = enemyCol - playerCol
      const dy = enemyRow - playerRow
      const tx = targetCol - playerCol
      const ty = targetRow - playerRow

      // Simple projection check
      if (Math.sign(dx) === Math.sign(tx) && Math.sign(dy) === Math.sign(ty)) {
        danger += 1
        // Extra danger for power/armor tanks
        if (t.kind === 'power' || t.kind === 'armor') {
          danger += 1
        }
      }
    }
  }

  return danger
}

/**
 * Default defense position: centered above the base at the defense row.
 * This is the fallback when no enemies are present.
 * Gap B: when the stage has no base, returns the player's current cell
 * (stay put — there's nothing to defend).
 */
export function getDefaultDefensePositionImpl(self: GodAIInput): Cell {
  if (!self.hasBase) return self.playerCell()
  return { col: BASE_POS.col, row: BASE_POS.row - self.params.defenseRowOffset }
}

/**
 * Select the best target cell for the player to navigate toward.
 *
 * S6 Attack-defense switching (core strategy):
 *   - Emergency defense: enemies close to base → strict defense position
 *   - Aggressive hunt: few enemies remaining → chase directly, relax distance
 *   - Normal defense: intercept at defense row (default)
 *
 * Priority:
 *   1. Aggressive mode (freeze/shield) → chase nearest enemy directly
 *   2. S6 hunt mode (few enemies remaining) → chase nearest enemy directly
 *   3. S6 emergency defense → return to defense position, intercept close enemies
 *   4. Normal: enemy closest to base → intercept at defense row
 *   5. No enemies → default defense position
 */
/**
 * §88 A/B round 3: can a tank parked at `choke` shoot the imminent enemy
 * (cell `enemy`) or its nearest threat point? Same row or column with clear
 * bullet LOS (brick/steel/base block). Used to decide chase-vs-hold: when the
 * chokepoint covers the enemy's approach, holding is strictly better than a
 * direct chase (the player shoots the enemy as it crosses); when it does NOT
 * cover the approach, the player must chase the enemy directly (S32 seed 23:
 * chokepoint (15,18) could not shoot a fast at (24,22) heading for the base).
 *
 * This is an ENDPOINT proxy for path coverage: the real threat path is the
 * A* corridor from the enemy to its nearest threat point, and a path can
 * pass through a cell sharing the chokepoint's row/col even when neither
 * endpoint does. The endpoints are a sound approximation for the short
 * (<= chaseMaxDist 3 cells) imminent corridors that drive this decision —
 * do NOT "fix" this into a per-path A* re-walk without A/B evidence.
 */
export function chokepointCoversEnemy(self: GodAIInput, choke: Cell, enemy: Cell): boolean {
  const w = self.world
  const tm = w.tileMap
  const plan = self._chokepointPlan
  // Enemy's nearest threat point (reuse the throttled plan's cached set).
  let tpCol = -1
  let tpRow = -1
  let tpDist = Infinity
  if (plan) {
    for (let ti = 0; ti < plan.threatPoints.length; ti++) {
      const t = plan.threatPoints[ti]
      const d = Math.abs(t.col - enemy.col) + Math.abs(t.row - enemy.row)
      if (d < tpDist) {
        tpDist = d
        tpCol = t.col
        tpRow = t.row
      }
    }
  }

  // LOS check from the chokepoint to a target cell: same row or column with
  // no brick/steel/base between. (Water lets bullets pass — blocksBullet.)
  const clear = (c: number, r: number): boolean => {
    // Same cell — an enemy standing ON the chokepoint is trivially covered
    // (point-blank). The zero-length walk below would step off-grid forever
    // (crash: grid[-1] undefined) — the 60-seed chaos A/B exposed this latent
    // §88 bug via seeds > 20 (DECISIONS §101).
    if (c === choke.col && r === choke.row) return true
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return false
    if (choke.col === c) {
      const step = choke.row < r ? 1 : -1
      for (let rr = choke.row + step; rr !== r; rr += step) {
        if (rr < 0 || rr >= GRID) return false
        if (blocksBullet(tm.grid[rr][c])) return false
      }
      return true
    }
    if (choke.row === r) {
      const step = choke.col < c ? 1 : -1
      for (let cc = choke.col + step; cc !== c; cc += step) {
        if (cc < 0 || cc >= GRID) return false
        if (blocksBullet(tm.grid[r][cc])) return false
      }
      return true
    }
    return false
  }
  if (clear(enemy.col, enemy.row)) return true
  return tpCol >= 0 && clear(tpCol, tpRow)
}

export function selectTargetImpl(self: GodAIInput, playerCell: Cell): Cell | null {
  // (perf §68 Round 9) NOTE: cross-tick caching was evaluated and REJECTED.
  // A 30-tick (0.5s) cache caused S6 Iron Curtain win rate to drop from
  // 72% to 40% — the stage has heavy steel walls forcing frequent target
  // switches, and 0.5s staleness leaves the player stuck behind walls too
  // long. The per-tick cost (~3% self-time) is the price of responsiveness.
  // Kept the wrapper signature so callers don't change; the body is just
  // a direct call to the uncached implementation.
  return selectTargetUncached(self, playerCell)
}

function selectTargetUncached(self: GodAIInput, playerCell: Cell): Cell | null {
  const w = self.world
  // §79: controlled tank, not `w.player`. In co-op the God AI drives P2, so
  // gating target selection on P1's existence would blank P2's target list
  // whenever P1 is permanently dead.
  const p = self.controlledTank(w)
  if (!p) return null

  const baseCol = BASE_POS.col
  const baseRow = BASE_POS.row
  const defenseRow = baseRow - self.params.defenseRowOffset

  // Cluster C: reuse the per-tick enemy snapshot (built in think()) instead
  // of allocating a filtered array on every call (AGENTS §14.1).
  // Falls back to a fresh scan only if think() hasn't populated it yet.
  const enemies =
    self._enemies.length > 0 ? self._enemies : w.tanks.filter((t) => t.alive && t.spawnTimer <= 0)
  if (enemies.length === 0) return self.getDefaultDefensePosition()

  // ---- Gap B: no-base fast path (plan/God-AI-Curriculum §3) ----
  // When the stage has no base, the AI is a pure hunter: always chase the
  // nearest enemy. No defense positioning, no base-threat checks, no
  // distance-from-base constraint. This is the correct behavior for
  // curriculum stages 1-4 (no-base) and also for real stages where the
  // base has already been destroyed (the AI should still try to clear).
  if (!self.hasBase) {
    let best = enemies[0]
    let bestDist = Infinity
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      const adjustedDist = d - (t.bonus ? 2 : 0)
      if (adjustedDist < bestDist) {
        bestDist = adjustedDist
        best = t
      }
    }
    return self.tankCell(best)
  }

  // ---- S6: Determine strategy mode ----
  // Emergency defense: delegated to isBaseUnderThreat() so target selection
  // and the T2a/power-up defense skips share ONE threat model. This includes
  // the static ±3-col box AND the P4 race-to-base check (flanking runners
  // that would beat the player back to the base).
  const baseUnderThreat = self.isBaseUnderThreat()

  // S6 Aggressive hunt (§5.3): few enemies on field AND few remaining in
  // queue. Both conditions must hold — requiring only one sent the player
  // chasing across the map between spawns (enemies.length dips to 0-1
  // during the 1.8s spawn gap), leaving the base undefended.
  //
  // §5.3 parameterization: the old hardcoded 2/3 thresholds were the root
  // cause of 0% win rate — the AI only hunted when ≤3 enemies remained in
  // the queue, spending 85% of the game turtling. Now reads from params:
  //   huntAllyCount (default 4 = MAX_ENEMIES_ALIVE) — field count gate
  //   endgameEnemyThreshold (default 6) — queue count gate
  const canHunt =
    enemies.length <= self.params.huntAllyCount &&
    w.enemiesRemaining <= self.params.endgameEnemyThreshold

  // If the player is too far from the base when it's under threat, return
  // to defense position. This applies regardless of canHunt — even in the
  // endgame, base defense takes priority over hunting when the player is
  // too far away to intercept in time.
  const playerDistToBase = Math.abs(playerCell.col - baseCol) + Math.abs(playerCell.row - baseRow)
  if (baseUnderThreat && playerDistToBase > self.params.maxPlayerDistFromBase) {
    return self.getDefaultDefensePosition()
  }

  // ---- P4.2: Outnumbered retreat (S18 crossfire family) ----
  // When several enemies converge on the player away from the base, pressing
  // the attack trades 1-for-1 at best (three tanks can fire from three
  // directions; the player has one barrel). Fall back toward the defense
  // position: corridors funnel pursuers into single file, and the base
  // gains a defender. Skipped when the base is already under threat (the
  // defense logic below handles that) and in aggressive/freeze mode.
  if (!baseUnderThreat && !self.aggressive) {
    let nearby = 0
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      if (d <= self.params.outnumberedRadiusCells) nearby++
    }
    if (nearby >= self.params.outnumberedEnemyCount && playerDistToBase > 6) {
      return self.getDefaultDefensePosition()
    }
  }

  // ---- M13: field-wide pressure retreat (SHIPPED, DECISIONS §113) ----
  // P4.2 only fires when 3+ enemies CONVERGE within outnumberedRadiusCells.
  // The M13 probe showed 70% of hard/chaos deaths happen with the FULL
  // enemy field alive (4/4) and 39% at >20 cells from the base — the player
  // deep-hunts while the field is at max pressure, and 1★ single-bullet
  // firepower cannot out-race 3-4 enemies (grinding death, §111). When the
  // FIELD count (not just nearby) is at/over outnumberedFieldEnemies and the
  // player is beyond outnumberedFieldDistCells, return to the defense
  // position — stop over-extending into a full battlefield. `enemies` here
  // is the Cluster C field-wide live snapshot, not the nearby count.
  // A/B (official 口径): 20-seed hard +2.7pp / chaos +2.6pp; 60-seed hard
  // +2.3pp / chaos +0.6pp — the FIRST mechanism without a chaos downside
  // (base losses and deaths down in BOTH difficulties; every dodge/horizon
  // mechanism before had the hard+/chaos- signature). The winning tuning
  // retreats at 3+ alive (attrition starts at 3, not 4 — 1★ can't out-race
  // 3 enemies either) beyond 15 cells; ON4@10 was measured HARMFUL
  // (too passive, base falls: hard -5.3pp). Pool-model only: classic
  // 'instant' has no grinding deaths (1-shot kills, 91% gate byte-locked).
  // NOTE (endgame interplay): this block runs BEFORE the S6 aggressive-hunt
  // below, so with 3+ enemies alive in the endgame (queue <= 6) the player
  // retreats instead of hunting. The 60-seed A/B empirically validated this
  // as net positive (hard +2.3pp / chaos +0.6pp) — do not "fix" it into a
  // regression by reordering the blocks.
  if (
    self.params.outnumberedFieldRetreat > 0 &&
    w.rules.combatModel === 'pool' &&
    !baseUnderThreat &&
    !self.aggressive &&
    playerDistToBase > self.params.outnumberedFieldDistCells &&
    enemies.length >= self.params.outnumberedFieldEnemies
  ) {
    return self.getDefaultDefensePosition()
  }

  // Aggressive mode (freeze): enemies can't move — chase nearest directly.
  if (self.aggressive) {
    let best = enemies[0]
    let bestDist = Infinity
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      if (d < bestDist) {
        bestDist = d
        best = t
      }
    }
    return self.tankCell(best)
  }

  // ---- §88: 据守咽喉要地 (chokepoint holding, user request 2026-08-02) ----
  // Rule 2: when the base is NOT under threat, hold the chokepoint (咽喉要地)
  // while swarmed — enemies on field > chokepointHoldThreshold — and chase the
  // enemy nearest a threat point otherwise (<= threshold). The chokepoint is
  // the lower-half cell that can shoot the most threat paths (see
  // Chokepoint.ts); navigating there and holding lets the player intercept
  // base-bound enemies instead of roaming. Gated by chokepointMode (0 = OFF,
  // byte-identical to pre-§88). Falls through to the normal target selection
  // below when no chokepoint/coverage exists (no threat points, no enemies
  // heading for the base, steel-sealed base, etc.).
  //
  // A/B round 2 (per-seed tick-diff): the hold arm ALSO requires a live
  // imminent threat (threatChaseTarget non-null — some enemy within
  // chokepointChaseMaxDist of a threat point). Without it the player walked
  // to the (30-tick cached) chokepoint, found the enemies had turned away,
  // and idled there while the base fell from another side (S19 seed 23:
  // player oscillated at (4,20) for ~1200 ticks). Once at the hold cell with
  // no imminent threat, fall through to the normal nearest-enemy chase.
  if (self.params.chokepointMode > 0 && self.hasBase && !baseUnderThreat) {
    // ---- Rule 1 (imminent enemy) outranks rule 2 (hold) ----
    // An enemy within chokepointChaseMaxDist of a threat point, facing the
    // base, is about to attack it — 优先击杀这些敌人. Chase it directly
    // UNLESS the chokepoint already covers its approach (same row/col with
    // clear LOS to the enemy or its nearest threat point): then holding lets
    // the player shoot it as it crosses — strictly better than a chase.
    // A/B round 3 (S32 seed 23): without this, the hold arm (enemies > 2)
    // marched the player to a chokepoint that could NOT shoot the imminent
    // fast tank's lane, and the fast broke through while A chased it.
    // (chase computed once — the hold arm reuses it, same-tick identical.)
    const chase = self.threatChaseTarget()
    const choke = self.chokepointCell()
    if (chase && (!choke || !chokepointCoversEnemy(self, choke, chase))) {
      self.branchCounts.chokepoint++
      return chase
    }
    if (enemies.length > self.params.chokepointHoldThreshold && choke) {
      // Hold only when an enemy is still approaching a threat point (the
      // imminence gate) AND the hold cell is close enough to march to — a
      // far hold cell is pure march time the enemy turns during (S26 seed
      // 12). Otherwise fall through to the normal hunt below.
      const holdDist = Math.abs(choke.col - playerCell.col) + Math.abs(choke.row - playerCell.row)
      if (
        chase &&
        (self.params.chokepointHoldMaxDist <= 0 || holdDist <= self.params.chokepointHoldMaxDist)
      ) {
        self.branchCounts.chokepoint++
        return choke
      }
    }
  }

  // ---- S6: Aggressive hunt mode ----
  // When few enemies remain, go directly for the nearest enemy.
  // This replaces the old endgame check (which was too restrictive:
  // enemiesRemaining <= 1 && enemies.length <= 1).
  if (canHunt) {
    let best = enemies[0]
    let bestDist = Infinity
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      // Prefer bonus enemies (they drop power-ups) when distances are close.
      const adjustedDist = d - (t.bonus ? 2 : 0)
      if (adjustedDist < bestDist) {
        bestDist = adjustedDist
        best = t
      }
    }
    return self.tankCell(best)
  }

  // M0.5 退役（2026-08-03）: D1/D2 guardBand + damagedArmor 空块已移除
  // （否决，移入 experimental.ts 归档）。

  // ---- Normal target selection ----
  // When the base is NOT under threat, behave like the no-base case:
  // chase the nearest enemy to the player. This prevents the AI from
  // chasing enemies near the base while ignoring closer enemies,
  // which was the #1 cause of low kill counts in maze stages.
  // The baseUnderThreat check runs every tick, so the AI immediately
  // switches to defense mode when an enemy approaches the base.
  if (!baseUnderThreat) {
    let best = enemies[0]
    let bestDist = Infinity
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      const adjustedDist = d - (t.bonus ? 2 : 0)
      if (adjustedDist < bestDist) {
        bestDist = adjustedDist
        best = t
      }
    }
    return self.tankCell(best)
  }

  // Base is under threat — find the most threatening enemy. Enemies with a
  // clear shot at the base (aligned + no walls in between) get a huge bonus
  // (defenseClearShotBonus, §59) — they can destroy the base with their next
  // bullet and must be prioritized. Others use kindThreatWeight scoring.
  let bestEnemy: Tank | null = null
  let bestScore = -Infinity
  for (let ti = 0; ti < enemies.length; ti++) {
    const t = enemies[ti]
    const tc = self.tankCell(t)
    const distToBase = Math.abs(tc.col - baseCol) + Math.abs(tc.row - baseRow)
    // §59: an enemy with a clear shot at the base is always considered,
    // even beyond threatRangeCells — it can destroy the base NOW from any
    // distance. Other enemies are filtered by threatRangeCells as before.
    const hasClearShot = self.params.defenseClearShotBonus > 0 && enemyCanShootBase(self, t)
    if (distToBase > self.params.threatRangeCells && !hasClearShot) continue

    // M0.5 退役: smartThreatModel 的 defense-priority kind weights 已移除
    // （Phase A 否决）——始终使用 kindThreatWeight（原 OFF 路径，字节相同）。
    const defenseKindWeight = kindThreatWeight(t.kind)
    const bonusWeight = t.bonus ? 3 : 0
    const urgencyBonus = tc.row >= defenseRow ? (tc.row - defenseRow + 1) * 100 : 0
    const proximityBonus = tc.row >= 20 ? 50 : 0
    // §59: canShootBaseFrom bonus — enemy has a clear shot at the base. This
    // is the highest-priority target: it can destroy the base NOW. Controlled
    // by defenseClearShotBonus (default 500, 0 = OFF = byte-identical to pre-§59).
    const clearShotBonus = hasClearShot ? self.params.defenseClearShotBonus : 0
    const score =
      -distToBase * 10 +
      (defenseKindWeight + bonusWeight) * 30 +
      urgencyBonus +
      proximityBonus +
      clearShotBonus
    if (score > bestScore) {
      bestScore = score
      bestEnemy = t
    }
  }

  if (!bestEnemy) {
    // No enemy within threat range — go after the nearest enemy anyway.
    // Sitting idle at the defense position while enemies roam the top of
    // the map means the player never engages and never clears the stage.
    let nearest = enemies[0]
    let nearestDist = Infinity
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - baseCol) + Math.abs(tc.row - baseRow)
      if (d < nearestDist) {
        nearestDist = d
        nearest = t
      }
    }
    return self.tankCell(nearest)
  }

  // Go directly toward the best enemy. With the bulletCap-aware onCooldown
  // fix, the player fires frequently and can kill enemies while pursuing.
  // The interception-point strategy was abandoned because wandering enemies
  // rarely cross the fixed interception column, leaving the player idle.
  return self.tankCell(bestEnemy)
}
