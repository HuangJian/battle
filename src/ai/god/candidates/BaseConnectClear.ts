// candidates/BaseConnectClear.ts — the baseConnectClear candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type Cell } from '../../../utils/grid-search'
import { type GodAIInput } from '../../GodAIInput'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import { digPathInfoCached } from '../PathCarve'
import { manhattan } from '../../../utils/helpers'
import { carveFire } from '../candidates/shared'

export function evalBaseConnectClear(self: GodAIInput, ctx: DecisionContext): boolean {
  const prm = self.params
  if (prm.baseConnectClearMode <= 0 || !self.hasBase) return false
  const pc = self.playerCell()
  // Lower-half gate — only clear walls in the lower half.
  if (pc.row < prm.baseConnectClearLowerRow) return false
  // Base under threat → defense candidates handle it. Do NOT reset the
  // travel flag — the player should resume traveling to the P2 spawn
  // after the threat is dealt with.
  if (self.isBaseUnderThreat()) return false

  // Fixed target: P2 spawn point (the opposite side of the base).
  const p2Col = self.world.player2SpawnPoint.col
  const p2Row = self.world.player2SpawnPoint.row
  // Arrived: player is within 2 cells of the P2 spawn. Reset the flag
  // and let normal AI take over (出击/防守).
  const distToP2 = manhattan(pc.col, pc.row, p2Col, p2Row)
  if (distToP2 <= 2) {
    self._baseConnectClearActive = false
    return false
  }

  const target: Cell = { col: p2Col, row: p2Row }
  const info = digPathInfoCached(self, pc, target)
  if (!info.path || info.path.length === 0) return false

  if (info.corridor) {
    // A corridor path exists. Only fire if we were previously carving
    // (travel mode) — the player needs to follow the opened corridor.
    // On stages where the corridor always existed, the flag is never set,
    // so the candidate never fires (no regression).
    if (!self._baseConnectClearActive) return false
  } else {
    // No corridor — carving needed. Activate travel mode.
    self._baseConnectClearActive = true
  }

  // Tick limit: bound the total active duration (carve + travel) so the
  // player eventually yields to combat even if they haven't reached P2.
  if (self._baseConnectClearActiveTicks >= prm.baseConnectClearMaxTicks) {
    self._baseConnectClearActive = false
    return false
  }
  self._baseConnectClearActiveTicks++

  // Follow the path toward the P2 spawn — carve walls if needed (dig path)
  // or navigate along the opened corridor.
  const dir = info.path[0]
  self._moveDir = dir
  carveFire(self, ctx, dir)
  self.branchCounts.baseConnectClear++
  self._lastBranch = 'baseConnectClear'
  return true
}


/**
 * baseConnectClear(270) — §189 / 开局联通清墙 (user request 2026-08-11).
 *
 * At game start, the player observes the lower-half layout. If the player's
 * side of the base is NOT corridor-connected to the P2 spawn point (the
 * opposite side), the player proactively clears walls to reach it — going
 * AROUND the base ring, not through it. Once the corridor path is open,
 * the candidate stops and normal AI behavior takes over (出击/防守).
 *
 * The target is FIXED (P2 spawn point = 24 - P1 spawn col), not dynamic —
 * this ensures the player keeps carving toward the same goal regardless of
 * their current position, preventing the "reached base column → switched
 * target → went back" oscillation that the wing-anchor approach caused.
 *
 * Same hard constraints as §161 carve (R5/R6): never break steel, never
 * break base-ring bricks. The fire control (carveFire) already prevents
 * firing at ring/base walls. Reuses digPathInfoCached (findPath with
 * breakBrick + buildDigCosts) so A* routes AROUND the ring.
 * Runs only when baseConnectClearMode > 0 (default ON for hard/chaos).
 */

export const BASE_CONNECT_CLEAR: Candidate = {
  id: 'baseConnectClear',
  weight: ACTION_WEIGHTS.baseConnectClear,
  evaluate: evalBaseConnectClear,
}
