/**
 * ActionCandidates — M4 unified action candidates
 * (plan/God-AI-Hard-Open-Test-Protocol.md §7, default OFF via candidateMode).
 *
 * Every tick under an ACTIVE base threat, the defense decision compares a
 * FIXED candidate set instead of letting the ad-hoc branch cascade commit
 * with an already-closed window (the dominant M3 finding: 8/14 stall events
 * had threatSlack −81..−573 at commit — the interception window closed
 * BEFORE the AI stood still):
 *
 *   kill-current    — keep killing the current hunt target (standing shot
 *                     when already ray-aligned, approach otherwise)
 *   intercept-base  — approach the MOST URGENT base threat (min
 *                     enemyDamageDeadline among csb/cbr) and shoot en route
 *   clear-lane      — a non-ring brick blocks the aligned ray to the
 *                     urgent threat: blast it, then shoot (§7.2 gate d)
 *   return-defense  — no local candidate wins in time; fall back to the
 *                     defense anchor while the arrival window is still open
 *
 * Each candidate carries the §7.1 metric set (firstOutputTick,
 * playerKillEta, enemyDamageEarliest, killSlack, interceptSlack,
 * secondThreatRisk) and must pass ALL §7.2 gates before it may replace the
 * current action:
 *
 *   a. relevant slack > 0 — the SAFE deadline (enemyDamageDeadline, safety
 *      margin already subtracted) must be beaten;
 *   b. firstOutputTick finite and not a meaningless statue: a standing
 *      commit is only valid when the STANDING shot beats the deadline
 *      (M2 contractStandingHold semantics);
 *   c. no second threat enters its irreversible window before the candidate
 *      completes (killAssessment.missesSecondThreat);
 *   d. expected base damage not above continuing: fire rays may not cross
 *      an INTACT ring cell OR the base eagle itself (S30s27 counterfactual:
 *      shooting "at the threat" broke the own ring and CAUSED the loss;
 *      shooting through the eagle self-hits the base), and clear-lane may
 *      never target a ring brick and its follow-up shot must stay
 *      ring/base-clear;
 *   e. (coverage rule — not applicable to this set; coverageMode is a
 *      separate OFF-by-default layer.)
 *
 * Selection priority: kill-current addressing the urgent threat, then
 * intercept-base, clear-lane, kill-current on a non-urgent target, then
 * return-defense. Deterministic tie-break = this order.
 *
 * Pure functions of (world, tanks) — no RNG, no World mutation. The verdict
 * is written into a CALLER-OWNED scratch object (§14.2 — no per-tick result
 * allocations); ThreatBudget helpers take caller-owned `out` buffers and the
 * per-evaluate buffers below are module-level scratch (§14.1/§14.2 — zero
 * per-tick allocation even with candidateMode > 0 on the default sim path).
 */

import { CELL, BASE_POS, GRID, type Direction } from '../../constants'
import type { World } from '../../game/World'
import type { Tank } from '../../types'
import {
  RING_CELLS,
  enemyDeadline,
  killAssessment,
  playerActionEta,
  standingKillAssessment,
  tankCenterCell,
  aimDirTo,
  ticksUntilFire,
  ticksUntilLegalTurn,
  turnCostTicks,
  type ActionEta,
  type EnemyDeadline,
  type KillAssessment,
  type StandingAssessment,
} from './ThreatBudget'

// §14.2 hot-path scratch: the layer commits per tick when candidateMode > 0,
// so ALL result buffers below are module-level (written before read, single
// threaded — same contract as ThreatBudget's internal buffers). Passed to
// every ThreatBudget helper instead of allocating per call. The verdict
// itself is still the caller-owned `out` (think.ts `self._candVerdict`).
const _CELL_P = { col: 0, row: 0 }
const _CELL_U = { col: 0, row: 0 }
const _CELL_K = { col: 0, row: 0 }
const _CELL_TMP = { col: 0, row: 0 }
const _DL_LOOP: EnemyDeadline = {
  enemyArrivalLowerBound: 0,
  enemyDamageEarliest: 0,
  enemyDamageDeadline: 0,
  enemyDamageWindow: 0,
  enemyUrgency: 0,
  directThreat: false,
}
const _DL_U: EnemyDeadline = {
  enemyArrivalLowerBound: 0,
  enemyDamageEarliest: 0,
  enemyDamageDeadline: 0,
  enemyDamageWindow: 0,
  enemyUrgency: 0,
  directThreat: false,
}
const _KA_U: KillAssessment = {
  playerArrivalAndAimEta: 0,
  firstFireEta: 0,
  playerKillEta: 0,
  killSlack: 0,
  interceptSlack: 0,
  missesSecondThreat: false,
}
const _KA_TMP: KillAssessment = {
  playerArrivalAndAimEta: 0,
  firstFireEta: 0,
  playerKillEta: 0,
  killSlack: 0,
  interceptSlack: 0,
  missesSecondThreat: false,
}
const _SK: StandingAssessment = { killEta: 0, killSlack: 0, deadline: 0 }
const _BRICK_OUT = [-1, -1]
const _KRAY_OUT = [-1, -1]
const _CELL_FB_A = { col: 0, row: 0 }
const _CELL_FB_B = { col: 0, row: 0 }
const _ETA: ActionEta = {
  nextLegalTurnEta: 0,
  movementEta: 0,
  aimAlignmentEta: 0,
  fireCooldownEta: 0,
  requiredShotsEta: 0,
  total: 0,
}

export type UnifiedCandidateKind = 'killCurrent' | 'interceptBase' | 'clearLane' | 'returnDefense'

/** §217 travel-fire detour: one legal turn window (200ms rule → 12 ticks,
 * +1 for the turn snap) is the detour cost; killSlack already carries the
 * 12-tick enemyDeadline safety margin. */
export const DETOUR_TURN_WINDOW_TICKS = 13

/** §7.1 metrics for the winning candidate (kind === null ⇒ layer declines). */
export interface CandidateVerdict {
  kind: UnifiedCandidateKind | null
  /** Enemy the candidate addresses (−1 for returnDefense). */
  threatId: number
  /** Earliest tick the candidate produces a move or a shot (§7.2 b). */
  firstOutputTick: number
  /** ETA of the killing shot landing (NaN for returnDefense). */
  playerKillEta: number
  /** enemyDamageEarliest of the addressed threat (NaN for returnDefense). */
  enemyDamageEarliest: number
  killSlack: number
  interceptSlack: number
  secondThreatRisk: boolean
  /** §7.2 (d): the aligned shot does not cross an intact ring cell. */
  fireClear: boolean
  /**
   * True only when the commit is a STANDING shot (verdict from the standing
   * assessment — the standing kill/intercept beats the deadline). False for
   * every approach commit. Callers must read THIS field, never re-derive
   * "standing" from firstOutputTick === 0: an aligned approach has zero
   * arrival cost yet may still have a blocked ray.
   */
  standingShot: boolean
  /** Diagnostic for forensics (which gate accepted/rejected what). */
  reason: string
}

/** Caller-owned scratch — pass the same object every tick. */
export function makeCandidateVerdict(): CandidateVerdict {
  return {
    kind: null,
    threatId: -1,
    firstOutputTick: 0,
    playerKillEta: NaN,
    enemyDamageEarliest: NaN,
    killSlack: -Infinity,
    interceptSlack: -Infinity,
    secondThreatRisk: false,
    fireClear: false,
    standingShot: false,
    reason: '',
  }
}

/** Cell is one of the 8 base-protection ring cells. */
function isRingCell(col: number, row: number): boolean {
  for (let i = 0; i < RING_CELLS.length; i++) {
    if (RING_CELLS[i].col === col && RING_CELLS[i].row === row) return true
  }
  return false
}

/** Cell is inside the 2×2 eagle footprint (BASE_POS..+1). Firing THROUGH it
 * is a self-inflicted base hit — the S30s27 lesson, base form. */
function isBaseCell(col: number, row: number): boolean {
  return (
    col >= BASE_POS.col &&
    col <= BASE_POS.col + 1 &&
    row >= BASE_POS.row &&
    row <= BASE_POS.row + 1
  )
}

/**
 * §7.2 (d): does the aligned ray player→target cross an INTACT ring cell
 * (brick/steel still standing) or the base eagle itself, strictly between
 * the two tanks? Firing then would chip the own ring or self-hit the base
 * (S30s27). Center-cell space (tankCenterCell), same as the rest of this
 * module — never compare with CoveragePlanner's corner space.
 */
export function fireRayBlocked(world: World, p: Tank, t: Tank): boolean {
  const pc = tankCenterCell(p, _CELL_FB_A)
  const tc = tankCenterCell(t, _CELL_FB_B)
  const tm = world.tileMap
  if (pc.col === tc.col && pc.row === tc.row) return false
  if (pc.col === tc.col) {
    const step = tc.row > pc.row ? 1 : -1
    for (let r = pc.row + step; r !== tc.row; r += step) {
      if (isBaseCell(pc.col, r)) return true
      if (!isRingCell(pc.col, r)) continue
      const tile = tm.get(pc.col, r)
      if (tile === 'brick' || tile === 'steel') return true
    }
  } else if (pc.row === tc.row) {
    const step = tc.col > pc.col ? 1 : -1
    for (let c = pc.col + step; c !== tc.col; c += step) {
      if (isBaseCell(c, pc.row)) return true
      if (!isRingCell(c, pc.row)) continue
      const tile = tm.get(c, pc.row)
      if (tile === 'brick' || tile === 'steel') return true
    }
  }
  return false
}

/** First blocker on the aligned ray strictly between player and target.
 * Writes the cell into out[0]/out[1]; returns 'brick' (non-ring),
 * 'ring' (own ring — never clearable), 'steel', 'base' (the eagle itself —
 * never clearable, firing through it self-hits the base), or 'none'. */
function firstBrickOnRay(
  world: World,
  pcol: number,
  prow: number,
  tcol: number,
  trow: number,
  out: Array<number>,
): 'brick' | 'ring' | 'steel' | 'base' | 'none' {
  const tm = world.tileMap
  const walk = (col: number, row: number): 'brick' | 'ring' | 'steel' | 'base' | 'none' => {
    const t = tm.get(col, row)
    if (t === 'brick') {
      out[0] = col
      out[1] = row
      return isRingCell(col, row) ? 'ring' : 'brick'
    }
    if (t === 'steel') return 'steel'
    if (t === 'base') return 'base'
    return 'none'
  }
  if (pcol === tcol && prow === trow) return 'none'
  if (pcol === tcol) {
    const step = trow > prow ? 1 : -1
    for (let r = prow + step; r !== trow; r += step) {
      const hit = walk(pcol, r)
      if (hit !== 'none') return hit
    }
  } else if (prow === trow) {
    const step = tcol > pcol ? 1 : -1
    for (let c = pcol + step; c !== tcol; c += step) {
      const hit = walk(c, prow)
      if (hit !== 'none') return hit
    }
  }
  return 'none'
}

/**
 * Evaluate the four candidates. `enemies` = alive, spawned enemies (the
 * per-tick Cluster C snapshot). `huntTarget` = the current hunt target
 * (may be null → nearest enemy proxies it). `anchor` = return-defense
 * position (col, row). Declines (kind === null) when there is no direct
 * (csb/cbr) threat — outside a threat window the existing cascade owns the
 * tick (M3: idle-alert elimination is NOT a goal in itself).
 */
export function evaluateUnifiedCandidates(
  world: World,
  p: Tank,
  enemies: Tank[],
  huntTarget: Tank | null,
  anchorCol: number,
  anchorRow: number,
  out: CandidateVerdict,
): CandidateVerdict {
  out.kind = null
  out.threatId = -1
  out.firstOutputTick = 0
  out.playerKillEta = NaN
  out.enemyDamageEarliest = NaN
  out.killSlack = -Infinity
  out.interceptSlack = -Infinity
  out.secondThreatRisk = false
  out.fireClear = false
  out.standingShot = false

  const pc = tankCenterCell(p, _CELL_P)

  // ---- identify the urgent direct threat + the kill-current target ----
  let urgent: Tank | null = null
  let urgentDl = Infinity
  let nearest: Tank | null = null
  let nearestD = Infinity
  for (let i = 0; i < enemies.length; i++) {
    const e = enemies[i]
    if (e.isPlayer) continue
    const dl = enemyDeadline(world, e, _DL_LOOP)
    if (dl.directThreat && dl.enemyDamageDeadline < urgentDl) {
      urgentDl = dl.enemyDamageDeadline
      urgent = e
    }
    const ec = tankCenterCell(e, _CELL_TMP)
    const d = Math.abs(ec.col - pc.col) + Math.abs(ec.row - pc.row)
    if (d < nearestD) {
      nearestD = d
      nearest = e
    }
  }
  if (!urgent) {
    out.reason = 'no direct (csb/cbr) threat — layer declines'
    return out
  }
  const K = huntTarget ?? nearest
  if (!K) {
    out.reason = 'no enemy to act on'
    return out
  }
  const uc = tankCenterCell(urgent, _CELL_U)
  const kc = tankCenterCell(K, _CELL_K)
  const dlU = enemyDeadline(world, urgent, _DL_U)
  out.enemyDamageEarliest = dlU.enemyDamageEarliest

  const kaU = killAssessment(world, p, urgent, _KA_U)
  const alignedU = uc.col === pc.col || uc.row === pc.row
  const facingU = p.dir === (aimDirTo(uc.col, uc.row, pc.col, pc.row) ?? p.dir)
  const fireClearU = alignedU && !fireRayBlocked(world, p, urgent)

  // ---- clear-lane pre-scan (shared by gates below) ----
  const rayHit = alignedU ? firstBrickOnRay(world, pc.col, pc.row, uc.col, uc.row, _BRICK_OUT) : 'none'

  // ---- candidate: kill-current (standing when aligned+facing+ray clear) ----
  // killSlack per §7.2 (a)/(b); standing commits use the STANDING shot eta
  // (M3 S28s26: the full-travel killAssessment is pessimistic — the standing
  // shot that was almost ready did win the window). A standing "kill" with a
  // brick on the ray is NOT a kill — the bullet stops at the brick — so the
  // standing branch requires a clear ray (that shot is then clear-lane's
  // business, ranked below intercept).
  const kRayHit = firstBrickOnRay(world, pc.col, pc.row, kc.col, kc.row, _KRAY_OUT)
  let killValid = false
  let killSlack = -Infinity
  let killEta = NaN
  let killFirstOutput = 0
  let killStanding = false
  let killFireClear = true
  let killSecondThreat = false
  let killReason = ''
  if (K.alive) {
    const alignedK = kc.col === pc.col || kc.row === pc.row
    const facingK = p.dir === (aimDirTo(kc.col, kc.row, pc.col, pc.row) ?? p.dir)
    if (alignedK && facingK && kRayHit === 'none') {
      const s = standingKillAssessment(world, p, K, _SK)
      killSlack = s.killSlack
      killEta = s.killEta
      killStanding = true
      killFirstOutput = 0 // the standing shot leaves at fire-ready — output is immediate
      killFireClear = !fireRayBlocked(world, p, K)
      // Review P1: the standing commit must pass the SAME second-threat gate
      // as the approach — committing a kill while a second threat's deadline
      // passes before the shot lands leaves that threat in an irreversible
      // window (§7.2 (c)). The full killAssessment horizon is conservative
      // (movement ETA ≥ standing ETA) — false rejections are safe.
      killSecondThreat = killAssessment(world, p, K, _KA_TMP).missesSecondThreat
      killValid = killSlack > 0 && killFireClear && !killSecondThreat
      killReason = killValid
        ? `kill-current standing killSlack=${killSlack.toFixed(1)}`
        : `kill-current standing killSlack=${killSlack.toFixed(1)} secondThreat=${killSecondThreat} fireClear=${killFireClear}`
    } else {
      const kaK = killAssessment(world, p, K, _KA_TMP)
      killSlack = kaK.killSlack
      killEta = kaK.playerKillEta
      killStanding = false
      killFirstOutput = kaK.playerArrivalAndAimEta
      // §7.2 (a)/(d): an approach "kill" that is ALREADY on the aligned ray
      // with a blocker (non-ring brick / ring / steel / base) is fiction —
      // the flight leg would stop at the blocker, so the committed slack is
      // invalid. Defer to clear-lane (which charges the brick-clear cost) or
      // intercept; an approach only exists when the player is NOT yet
      // aligned with the target.
      const blockedRay = alignedK && kRayHit !== 'none'
      killFireClear = !(alignedK && fireRayBlocked(world, p, K))
      killSecondThreat = kaK.missesSecondThreat
      killValid = killSlack > 0 && !kaK.missesSecondThreat && killFireClear && !blockedRay
      killReason = killValid
        ? `kill-current approach killSlack=${killSlack.toFixed(1)}`
        : `kill-current approach killSlack=${killSlack.toFixed(1)} secondThreat=${kaK.missesSecondThreat} fireClear=${killFireClear} blockedRay=${blockedRay}`
    }
  }

  // ---- candidate: intercept-base (approach the urgent threat) ----
  let interceptValid = false
  let interceptReason = ''
  if (alignedU && facingU && fireClearU && rayHit === 'none') {
    // Already holding the winning ray: this IS the interception — same
    // standing verdict as kill-current on the urgent threat. Review P1: the
    // standing commit must pass the second-threat gate too (§7.2 (c)).
    const s = standingKillAssessment(world, p, urgent, _SK)
    out.interceptSlack = s.killSlack
    interceptValid = s.killSlack > 0 && !kaU.missesSecondThreat
    interceptReason = interceptValid
      ? `intercept standing on urgent ray killSlack=${s.killSlack.toFixed(1)}`
      : `intercept standing killSlack=${s.killSlack.toFixed(1)} secondThreat=${kaU.missesSecondThreat} (window closed)`
  } else {
    out.interceptSlack = kaU.interceptSlack
    // §7.2 (a)/(d): an already-aligned intercept with a blocker on the ray
    // cannot shoot and moving along the axis is a no-output statue — the
    // slack must be real. Off-axis approaches keep their geometric slack.
    const blockedU = alignedU && rayHit !== 'none'
    interceptValid =
      (kaU.interceptSlack > 0 || kaU.killSlack > 0) && !kaU.missesSecondThreat && !blockedU
    interceptReason = interceptValid
      ? `intercept approach interceptSlack=${kaU.interceptSlack.toFixed(1)} killSlack=${kaU.killSlack.toFixed(1)}`
      : `intercept interceptSlack=${kaU.interceptSlack.toFixed(1)} killSlack=${kaU.killSlack.toFixed(1)} secondThreat=${kaU.missesSecondThreat} blockedRay=${blockedU}`
  }

  // ---- candidate: clear-lane (§7.2 d: never a ring brick) ----
  let clearValid = false
  let clearReason = ''
  let clearSlack = -Infinity
  let clearEta = NaN
  if (rayHit === 'brick') {
    const dirToBrick = aimDirTo(_BRICK_OUT[0], _BRICK_OUT[1], pc.col, pc.row) ?? p.dir
    // Standing brick shot: legal-turn wait (when a turn is needed) + fire
    // readiness + flight to the brick, then one re-arm + flight to the
    // threat for the follow-up shot through the opened lane.
    const turnBrick =
      p.dir !== dirToBrick ? ticksUntilLegalTurn(world, p) + turnCostTicks(world) : 0
    const flightBrick =
      (Math.abs(_BRICK_OUT[0] - pc.col) + Math.abs(_BRICK_OUT[1] - pc.row)) *
      (p.bulletSpeed > 0 ? CELL / p.bulletSpeed : 0)
    const cadenceTicks = p.nextFireInterval > 0 ? p.nextFireInterval / (1000 / 60) : 0
    const flightU = (Math.abs(uc.col - pc.col) + Math.abs(uc.row - pc.row)) * (p.bulletSpeed > 0 ? CELL / p.bulletSpeed : 0)
    clearEta = turnBrick + Math.max(0, ticksUntilFire(world, p)) + flightBrick + cadenceTicks + flightU
    clearSlack = dlU.enemyDamageDeadline - clearEta
    // §7.2 (d): the follow-up shot through the opened lane must still not
    // cross a ring brick or the base itself (a brick cleared on the way does
    // not make a base-crossing ray safe — S30s27 base form).
    const followUpClear = !fireRayBlocked(world, p, urgent)
    clearValid = clearSlack > 0 && !kaU.missesSecondThreat && followUpClear
    clearReason = clearValid
      ? `clear-lane brick(${_BRICK_OUT[0]},${_BRICK_OUT[1]}) slack=${clearSlack.toFixed(1)}`
      : `clear-lane brick(${_BRICK_OUT[0]},${_BRICK_OUT[1]}) slack=${clearSlack.toFixed(1)} secondThreat=${kaU.missesSecondThreat}`
  } else {
    clearReason = `clear-lane n/a (rayHit=${rayHit})`
  }

  // ---- candidate: return-defense ----
  let returnValid = false
  let returnReason = ''
  let returnArrival = 0
  {
    const etaA = playerActionEta(world, p, anchorCol, anchorRow, 'down', 1, _ETA)
    const arrival = etaA.nextLegalTurnEta + etaA.movementEta + etaA.aimAlignmentEta
    const anchorD = Math.abs(anchorCol - pc.col) + Math.abs(anchorRow - pc.row)
    if (anchorD <= 2) {
      returnReason = 'return-defense already at anchor'
    } else {
      let arriveInTime = true
      for (let i = 0; i < enemies.length; i++) {
        const e = enemies[i]
        if (e.isPlayer) continue
        if (enemyDeadline(world, e, _DL_LOOP).enemyDamageEarliest <= arrival) {
          arriveInTime = false
          break
        }
      }
      returnValid = arriveInTime
      returnReason = returnValid
        ? `return-defense arrival=${arrival.toFixed(1)} beats every earliest`
        : `return-defense arrival=${arrival.toFixed(1)} after some earliest`
    }
    returnArrival = arrival
  }

  // ---- selection (§7.2: all gates already checked per candidate) ----
  // kill-current tops the order only when its ray is actually clear —
  // through a brick the "kill" is fiction, and clear-lane (which opens that
  // ray) outranks the approach variants.
  const kIsUrgent = K.id === urgent.id
  if (killValid && kIsUrgent && kRayHit === 'none') {
    out.kind = 'killCurrent'
    out.threatId = K.id
    out.firstOutputTick = killStanding ? 0 : killFirstOutput
    out.playerKillEta = killEta
    out.killSlack = killSlack
    out.interceptSlack = out.interceptSlack === -Infinity ? kaU.interceptSlack : out.interceptSlack
    out.secondThreatRisk = killSecondThreat // gate (c) passed — derived, not hardcoded
    out.fireClear = killFireClear
    out.standingShot = killStanding
    out.reason = killReason
  } else if (interceptValid) {
    out.kind = 'interceptBase'
    out.threatId = urgent.id
    if (alignedU && facingU && fireClearU && rayHit === 'none') {
      const s = standingKillAssessment(world, p, urgent, _SK)
      out.firstOutputTick = 0
      out.playerKillEta = s.killEta
      out.killSlack = s.killSlack
      out.standingShot = true
    } else {
      out.firstOutputTick = kaU.playerArrivalAndAimEta
      out.playerKillEta = kaU.playerKillEta
      out.killSlack = kaU.killSlack
      out.standingShot = false
    }
    out.secondThreatRisk = kaU.missesSecondThreat
    out.fireClear = fireClearU
    out.reason = interceptReason
  } else if (clearValid) {
    out.kind = 'clearLane'
    out.threatId = urgent.id
    out.firstOutputTick = 0
    out.playerKillEta = clearEta
    out.killSlack = clearSlack
    out.interceptSlack = kaU.interceptSlack
    out.secondThreatRisk = kaU.missesSecondThreat
    out.fireClear = true // the cleared ray was already ring-safe (rayHit === 'brick', not 'ring')
    out.standingShot = false
    out.reason = clearReason
  } else if (killValid) {
    out.kind = 'killCurrent'
    out.threatId = K.id
    out.firstOutputTick = killStanding ? 0 : killFirstOutput
    out.playerKillEta = killEta
    out.killSlack = killSlack
    out.interceptSlack = kaU.interceptSlack
    out.secondThreatRisk = killSecondThreat
    out.fireClear = killFireClear
    out.standingShot = killStanding
    out.reason = killReason
  } else if (returnValid) {
    out.kind = 'returnDefense'
    out.threatId = -1
    out.firstOutputTick = returnArrival
    out.playerKillEta = NaN
    out.killSlack = -Infinity
    out.interceptSlack = kaU.interceptSlack
    out.secondThreatRisk = false
    out.fireClear = false
    out.reason = returnReason
  } else {
    out.reason = `all gates closed: ${killReason}; ${interceptReason}; ${clearReason}; ${returnReason}`
  }
  return out
}

/** Direction toward the first non-ring brick on the aligned ray to the
 * threat (the clear-lane commit target); null when there is none. */
export function clearLaneFireDir(world: World, p: Tank, threat: Tank): Direction | null {
  const pc = tankCenterCell(p, _CELL_TMP)
  const tc = tankCenterCell(threat, _CELL_U)
  const hit = firstBrickOnRay(world, pc.col, pc.row, tc.col, tc.row, _BRICK_OUT)
  if (hit !== 'brick') return null
  return aimDirTo(_BRICK_OUT[0], _BRICK_OUT[1], pc.col, pc.row)
}

/**
 * §217 M5 travel-phase fire-line detour — 决策点前移 (open-test round 2).
 *
 * 旅行 (HUNT/navigate) 中, 若此刻有目标满足: 中心格对齐 + 射线全清 (任何非空
 * 地形, 含 base 格 — S30s27 双保险) + killSlack > 转弯窗 13t (一次转向 + 击杀
 * 仍胜 enemyDamageDeadline, deadline 已含 12t 安全余量) + 未面向 (需一次转弯 —
 * 已面向则 baseline navigate 本就会开火), 则本 tick 转向 + 开火, 用掉一个转弯
 * 窗换掉一个即将进带的敌人, 而非等到达后才对齐 (S3s46: navigate 段 187 tick 0 发,
 * 带 +209 slack 的目标放跑; S22s28: defenseIntercept 冷却恢复后 48 tick 才开火)。
 *
 * 目标覆盖由调用方注入 `isWorthKillNow` (csb/cbr/基地逼近带 — csb/cbr 几何上
 * 全部落入带内, 带规则是其超集; 探针显示机会 94% 是带内 fb 游走威胁)。
 *
 * 纯函数: 无 RNG 消耗 (fire roll 由调用方按 aimError 纪律执行), 不写 World。
 * 探针测量 (tools/diag/travel-fire-probe.ts, §217): baseline 败局 33.3% 有 ≥1
 * 机会 tick, 75% 先于首次基地受伤。
 */
export function travelFireDetourDir(
  world: World,
  p: Tank,
  pc: { col: number; row: number },
  list: Tank[],
  huntId: number,
  isWorthKillNow: (t: Tank) => boolean,
  minSlack: number = DETOUR_TURN_WINDOW_TICKS,
): Direction | null {
  const tm = world.tileMap
  // 目标代理 (mirror UNIFIED_CANDIDATES): 最后 selectTarget 目标, 否则最近敌。
  let hunt: Tank | null = null
  let nearest: Tank | null = null
  let nearestD = Infinity
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue
    if (t.id === huntId) hunt = t
    const tc = tankCenterCell(t, _CELL_TMP)
    const dd = Math.abs(tc.col - pc.col) + Math.abs(tc.row - pc.row)
    if (dd < nearestD) {
      nearestD = dd
      nearest = t
    }
  }
  const target = hunt ?? nearest
  if (!target) return null
  if (!isWorthKillNow(target)) return null
  const tc = tankCenterCell(target, _CELL_U)
  if (tc.col !== pc.col && tc.row !== pc.row) return null // 未对齐
  const dir: Direction =
    tc.col === pc.col ? (tc.row > pc.row ? 'down' : 'up') : tc.col > pc.col ? 'right' : 'left'
  if (p.dir === dir) return null // 已面向 — baseline navigate 本就会开火
  // 走廊: 两格之间逐格扫描, 任何非空地形 (含 base) 都挡 — 与 think.ts
  // laneCorridorBlocked 同语义 (single source: think.ts:483)。
  const g = tm.grid
  if (tc.col === pc.col) {
    const step = tc.row > pc.row ? 1 : -1
    for (let r = pc.row + step; r !== tc.row; r += step) {
      if (r < 0 || r >= GRID) return null
      if (g[r][tc.col] !== 'empty') return null
    }
  } else {
    const step = tc.col > pc.col ? 1 : -1
    for (let c = pc.col + step; c !== tc.col; c += step) {
      if (c < 0 || c >= GRID) return null
      if (g[tc.row][c] !== 'empty') return null
    }
  }
  if (fireRayBlocked(world, p, target)) return null // 环砖/基地双保险
  const slack = killAssessment(world, p, target, _KA_TMP).killSlack
  if (!(slack > minSlack)) return null
  return dir
}
