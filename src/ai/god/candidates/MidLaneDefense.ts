// candidates/MidLaneDefense.ts — the midLaneDefense candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type Direction, BULLET, TANK } from '../../../constants'
import { type GodAIInput } from '../../GodAIInput'
import { contractStandingHold, enemyBulletOnRay, ownBulletOnRay } from '../ActionContract'
import { type DecisionContext } from '../DecisionCore'

import { manhattan } from '../../../utils/helpers'
import {
  carveFireAheadImpl,
  carvePathInfoCached,
  findLaneDefensePointImpl,
  laneShellAboveImpl,
  laneShellInColumnImpl,
  laneThreatImpl,
} from '../PathCarve'

export function evalMidLaneDefense(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy, onCooldown } = ctx
  const prm = self.params
  if (prm.midLaneDefense <= 0 || !self.hasBase || self.aggressive) return false
  const pc = self.playerCell()
  // The ONLY trigger is a real enemy bullet in the base column heading
  // down (bullet-bullet collision cancels it 对消). Enemy presence/facing
  // signals were A/B-measured catastrophic (14-35% of ticks on most maps
  // → player statue, 29/35 worse): enemies merely PASSING the base column
  // is not a threat. Bullets are the actual carve moment — rare, precise.
  //
  // §164: midLaneStickyTicks — a drill bullet dies on a brick after
  // 10-60 ticks, leaving 70-130 tick gaps where laneThreatImpl is false
  // and the player releases mid-walk, never reaching the lane point (S8
  // drill chain: ring bricks fall at t1305, the next bullet has a clear
  // 71-tick lane, player 6+ cells away loses the race). Once a lane shell
  // is seen, keep the candidate engaged for midLaneStickyTicks so the
  // player commits to the walk and holds through the whole drill.
  if (!laneThreatImpl(self)) {
    if (prm.midLaneStickyTicks <= 0 || self._midLaneStickyHold <= 0) return false
  } else if (prm.midLaneStickyTicks > 0) {
    self._midLaneStickyHold = prm.midLaneStickyTicks
  }

  // ---- 换持枪判定 (Tuning round 3): the hold does NOT require being at
  // the lane defense point. 对消 (bullet-bullet cancellation) needs the
  // player's UPWARD bullet to physically overlap the shell's line: both
  // bullets are 6px wide, so |bx − pcx| < 6 or they never meet. Standing
  // still in the 32px column matches a random shell only ~37% of the time
  // — so the player must WALK the column, searching for a shell line to
  // lock. When a cancellable shell is in the column (laneThreatImpl), the
  // player: (a) if aligned (|bx−pcx| < 6) → hold & fire UP to cancel;
  // (b) if a shell line is LEFT/RIGHT of the player within the column →
  // step sideways to acquire it, firing meanwhile; (c) else navigate to
  // the lane point and hold. This is the actual Battlement-independent
  // mechanism — the old point-only hold could never fire on maps where
  // the point sits inside a sealed fortress (Battlement (12,21)).
  // --- 换持枪判定 round 3: acquire the shell's bullet line. ---
  const shellOff = laneShellAboveImpl(self, pcx, pcy)
  if (shellOff !== null) {
    const absOff = Math.abs(shellOff)
    if (absOff < BULLET) {
      // Aligned with a coming shell — hold in place, face up, fire to
      // cancel. The shell is the target; fire whenever the gun is ready.
      // 行动有效性契约 (open-test §5.2 — all three defense branches gated
      // identically): a standing, already-facing, on-cooldown, no-output
      // hold is only valid with waiting value — an interceptable shell on
      // the up-ray / own bullet resolving / a standing shot beating the
      // threat deadline. Otherwise yield (fall through produces output).
      // mode=0 短路 → byte-identical。
      if (
        prm.actionContractMode > 0 &&
        p.dir === 'up' &&
        onCooldown &&
        !contractStandingHold({
          world: w,
          player: p,
          threat: null,
          enemyBulletOnRay: enemyBulletOnRay(w, p, 'up'),
          ownBulletOnRay: ownBulletOnRay(w, p, true),
        }).valid
      ) {
        return false
      }
      const laneDir: Direction = 'up'
      self._moveDir = p.dir === laneDir ? null : laneDir
      self._fire = !onCooldown
      self.branchCounts.midLaneDefense++
      self._lastBranch = 'midLaneDefense'
      return true
    }
    if (absOff <= TANK) {
      // A shell line is one side-step away — acquire it: step toward the
      // offset. NOTE: _fire stays FALSE here — the engine fires along
      // tank.dir (= the step direction), so an up-lane suppression shot is
      // impossible while stepping sideways; firing would waste the bullet
      // cap on a horizontal shot. Pure reposition; the aligned-hold branch
      // above does the actual 对消. Keeps the player hunting the shell
      // line instead of standing at the point where a random shell matches
      // only ~37% of the time.
      const stepDir: Direction = shellOff > 0 ? 'right' : 'left'
      self._moveDir = stepDir
      self._fire = false
      self.branchCounts.midLaneDefense++
      self._lastBranch = 'midLaneDefense'
      return true
    }
    // Shell line too far sideways to acquire by stepping — fall through to
    // the lane point, which is at least inside the column.
  }

  const point = findLaneDefensePointImpl(self, pc)
  if (!point) return false
  const distToPoint = manhattan(point.col, point.row, pc.col, pc.row)
  const inHold = distToPoint <= prm.midLaneHoldRange

  // Leash: only engage when the player is NEAR the lane point. Pulling
  // from across the map is a cross-map tug-of-war with HUNT (§163 A/B:
  // Battlement pocket escape → lane point inside the sealed pocket is an
  // 8-step dig the player just escaped — dragging it back lost 20→16
  // kills). The user spec: 不能偏离中路防守点太远 (<3 cells).
  if (distToPoint > prm.midLaneMaxDist) return false

  // 2. In hold range → stand and fire up the lane. Fire when a shell is
  //    ANYWHERE in the base column (laneShellInColumnImpl — the 128px T5
  //    intercept range can't see shells 16 cells up the column), or when
  //    a normal enemy/target is in the line (shouldFireInDir).
  if (inHold) {
    const laneDir: Direction = 'up'
    // 行动有效性契约 (open-test §5.2) — 同上: 站立 + 已朝向 + 冷却中且无输出
    // 的哨位提交必须先有等待价值，否则让位。有开火输出时契约不否决。
    if (
      prm.actionContractMode > 0 &&
      p.dir === laneDir &&
      onCooldown &&
      !laneShellInColumnImpl(self) &&
      !self.shouldFireInDir(pcx, pcy, laneDir) &&
      !contractStandingHold({
        world: w,
        player: p,
        threat: null,
        enemyBulletOnRay: enemyBulletOnRay(w, p, 'up'),
        ownBulletOnRay: ownBulletOnRay(w, p, true),
      }).valid
    ) {
      return false
    }
    self._moveDir = p.dir === laneDir ? null : laneDir
    self._fire =
      !onCooldown && (laneShellInColumnImpl(self) || self.shouldFireInDir(pcx, pcy, laneDir))
    self.branchCounts.midLaneDefense++
    self._lastBranch = 'midLaneDefense'
    return true
  }

  // 1/3. Out of hold range but within the leash → navigate to the point.
  //      Require the point be corridor-reachable or a SHORT dig (≤3
  //      cells) — a long dig through the sealed pocket is self-defeating
  //      (the player already escaped it); let HUNT fight the field and the
  //      §162 carve-dig handle pocket exits.
  const info = carvePathInfoCached(self, pc, point)
  const dig = info.path
  if (dig && dig.length > 0) {
    if (!info.corridor && dig.length > prm.midLaneMaxDigCells) return false
    const d = dig[0]
    self._moveDir = d
    if (self.canMoveDir(p, d)) {
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, d)
    } else if (carveFireAheadImpl(self, pcx, pcy, d)) {
      self._fire = !onCooldown
    } else {
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, d)
    }
    self.branchCounts.midLaneDefense++
    self._lastBranch = 'midLaneDefense'
    return true
  }
  // No carve path (rare — point unreachable) — plain navigation.
  self._moveDir = self.navigateTowards(point)
  self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
  self.branchCounts.midLaneDefense++
  self._lastBranch = 'midLaneDefense'
  return true
}
