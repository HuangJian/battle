// candidates/Engage.ts — the engage candidate body.
// Extracted verbatim from think.ts (plan/refactor.zcode.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { BASE_POS, CELL } from '../../../constants'
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'
import {
  bulletPathSteelBlockedImpl,
  enemyInShotCorridorImpl,
  scanAheadImpl,
  shotReachesBaseImpl,
} from '../FireControl'
import { countAlignedEnemiesImpl } from '../ThreatAssessor'

export function evalEngage(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy, onCooldown, aimDir } = ctx
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
  // fall through to navigate and hunt the enemy directly.
  //
  // P1: Skip T2a when the base is under threat and the player is too far
  // from the base. Camping far from the base while enemies approach it
  // was the #1 cause of base_destroyed gameovers.
  // §159: Override — don't skip when a CLOSE enemy is in the line of fire.
  // A close kill takes 1–2 shots (a few ticks) and directly helps defense.
  // Root cause: hard S20 Bastion, player 1 cell past the threshold with an
  // armor enemy 2 cells left → 160+ tick up/down oscillation, zero fire.
  let skipT2aForDefense =
    self.hasBase &&
    self.isBaseUnderThreat() &&
    Math.abs(self.playerCell().col - BASE_POS.col) +
      Math.abs(self.playerCell().row - BASE_POS.row) >
      self.params.maxPlayerDistFromBase

  // §6.3-D: Dual central breach — P2 is the free tank (flanker/pickup).
  // Don't force P2 to skip T2a when base is threatened — P1 holds the
  // anchor and handles base defense. P2 should be free to engage close
  // enemies it encounters while roaming. Gated by dualStrategyActive
  // (spectateDual || coop) && centralBreachRisk && isPlayer2 — P1 and
  // single-player byte-identical.
  if (skipT2aForDefense && self.dualStrategyActive && self.isPlayer2()) {
    skipT2aForDefense = false
  }

  if (skipT2aForDefense && aimDir && self.params.t2aDefenseOverrideRange > 0) {
    // Distance guard: only override when the player is slightly past the
    // threshold (within t2aDefenseOverrideRange cells past it). Far from
    // the base, even a quick kill takes too long — the base falls while
    // the player is engaged. This guard eliminates the Iron Curtain /
    // Quarry regressions (player 26+ cells from base stopped to engage).
    const overrideDist =
      Math.abs(self.playerCell().col - BASE_POS.col) +
      Math.abs(self.playerCell().row - BASE_POS.row)
    if (overrideDist <= self.params.maxPlayerDistFromBase + self.params.t2aDefenseOverrideRange) {
      // Reuses the per-tick scan memo (scanAheadImpl caches by origin+dir),
      // so the later scan in the engage body below is free.
      const defScan = scanAheadImpl(self, pcx, pcy, aimDir)
      if (defScan.enemy && defScan.enemyDist <= self.params.t2aDefenseOverrideRange) {
        skipT2aForDefense = false
      }
    }
  }

  if (aimDir && self._antiCampSuppress <= 0 && !skipT2aForDefense) {
    // Inline scanAheadImpl (perf §66, see aggressive branch above).
    const scan = scanAheadImpl(self, pcx, pcy, aimDir)

    // §121: T2a self-fire base guard (default OFF, selfFireBaseGuard=0 →
    // byte-identical). Root cause of the §120 32-run self-kill corpus
    // (t2a 81%): the scan's dual ±8px offset lines catch an enemy up to
    // ~25px off the bullet's 6px center path and report scan.enemy CLOSER
    // than the base eagle — the §74 guard below then allows fire, but the
    // bullet misses the off-line enemy and continues into the base (hard
    // S6 s43: killer shot x=200, enemy body x∈[206,238], bullet [197,203]
    // passed beside it into the eagle). Walk the bullet's real center
    // line: if it reaches the base, don't stop-and-aim here — fall
    // through to navigate (which repositions off the base line).
    // Mode 2 (lenient): keep the shot when an enemy body truly overlaps
    // the 6px corridor (point-blank overlap kill — bullet hits enemy first).
    const selfFireBlocked =
      self.params.selfFireBaseGuard > 0 &&
      shotReachesBaseImpl(self, pcx, pcy, aimDir) &&
      (self.params.selfFireBaseGuard < 2 || !enemyInShotCorridorImpl(self, pcx, pcy, aimDir))
    if (selfFireBlocked) self._selfFireGuardBlocks++

    // §74: Don't enter T2a when a base-protection wall is closer than
    // (or at the same distance as) the enemy on the other offset line.
    // Fall through to navigate when blocked by a closer base wall.
    // §152-W1: same as the aggressive branch — suppress the stop-and-aim
    // when the bullet's actual 6px path hits non-ring steel before the
    // enemy (the scan's offset lines can see the enemy while the center
    // line clips a steel column edge — hard S12 seed 934391936 W1).
    const steelPathBlocked152 =
      self.params.t2aSteelPathBlock > 0 &&
      bulletPathSteelBlockedImpl(self, pcx, pcy, aimDir, scan.enemyDist * CELL)
    if (
      scan.enemy &&
      !selfFireBlocked &&
      !steelPathBlocked152 &&
      !(scan.baseWall && scan.baseWallDist <= scan.enemyDist) &&
      !(scan.baseSteel && (p.level ?? 0) >= 3)
    ) {
      // §56: dynamic T2a range based on enemy kind.
      // For non-armor enemies (basic/fast/power): use t2aMaxRange (15) —
      // one shot kills at any distance, no DPS penalty for range.
      // For armor (4 hitsToKill): use t2aHighHpMaxRange (2) — close combat.
      // M3 (dodgeRateShrinksT2a): shrink the 1-HP range by the EnemyModel's
      // perceived turn discipline — enemies that dodge/redirect a lot make
      // long-range shots wasteful, so engage point-blank (shorter bullet
      // travel → fewer dodged shots). 0 at default ⇒ byte-identical.
      let effectiveRange =
        scan.enemyKind === 'armor' ? self.params.t2aHighHpMaxRange : self.params.t2aMaxRange
      if (self.params.dodgeRateShrinksT2a > 0 && scan.enemyKind !== 'armor') {
        const m = self._enemyModel
        if (m && m.active) {
          const shrink = self.params.dodgeRateShrinksT2a * m.discipline
          if (shrink > 0) effectiveRange = Math.max(1, effectiveRange * (1 - shrink))
        }
      }
      if (scan.enemyDist <= effectiveRange) {
        // §165: T2a outnumbered retreat — when 2+ aligned enemies are within
        // t2aOutnumberedRange cells in the scan direction, a stationary T2a
        // duel is a losing trade (the 2nd enemy fires while the player is
        // locked aiming at the 1st). Fall through to navigate (which moves
        // to a safer angle or triggers P4.2 outnumbered retreat). 0 = OFF
        // (byte-identical). Only applies in pool model (classic instant
        // 1-HP has no grinding — a single shot kills, no trade gradient).
        const outgunned =
          self.params.t2aOutnumberedRetreat > 0 &&
          w.rules.combatModel === 'pool' &&
          countAlignedEnemiesImpl(self, pcx, pcy, aimDir, self.params.t2aOutnumberedRange) >=
            self.params.t2aOutnumberedCount
        // Track camping duration in a ZONE (±1 cell), not exact cell.
        // P2.1fix: the old exact-cell check was defeated by sub-cell
        // oscillation — the player bounces between two adjacent cells
        // (e.g., x=32→40→32) at the TANK/CELL boundary, resetting the
        // camp cell each time the boundary is crossed. This prevented
        // the anti-camp escape from EVER firing, causing the Stage 3/4
        // deadlocks (player stuck at one spot for 17000+ ticks). The
        // zone fix accumulates camp time across nearby cells, so the
        // escape triggers even if the player wiggles between two cells.
        const pc = self.playerCell()
        if (
          self._campCell &&
          Math.abs(self._campCell.col - pc.col) <= 1 &&
          Math.abs(self._campCell.row - pc.row) <= 1
        ) {
          self._campTicks++
          // If a kill happened since camping started, reset the camp timer.
          // The player is being productive — let it continue camping.
          if (w.killCount !== self._campKillsAtStart) {
            self._campTicks = 1
            self._campKillsAtStart = w.killCount
          }
        } else {
          // Moved outside the camp zone — start fresh camp tracking.
          self._campCell = { col: pc.col, row: pc.row }
          self._campTicks = 1
          self._campKillsAtStart = w.killCount
        }

        // Anti-camp: if too long at this cell with no kills, break out.
        const campedTooLong =
          self._campTicks > self.params.campTimeoutTicks && w.killCount === self._campKillsAtStart

        if (!campedTooLong && !outgunned) {
          // ---- §49: 炮口相向分场景策略 ----
          // When an enemy faces the player, adapt per enemy type: ice skips,
          // 1HP enemies fight normally (counter-fire still applies — it is a
          // firing action, not movement dodge), armor uses counter-fire +
          // keep-alignment. 对枪抵消 applies to ALL kinds: when an enemy
          // bullet is already in the line, firing to cancel is safer than
          // trading hits. 120-seed validation: +5 wins all kinds.
          // §49-revisit: parameterized for A/B.
          const facing =
            self.params.counterFire > 0 ? self.findEnemyFacingPlayer(pcx, pcy, aimDir) : null
          const onIce = w.isTankOnIce(p)

          if (facing && !onIce && facing.dist <= self.params.counterFireMaxRange * CELL) {
            // ---- 对枪抵消逻辑（适用于所有敌人类型）----
            const enemyBulletInLine = self.hasEnemyBulletInLine(pcx, pcy, aimDir)

            if (enemyBulletInLine && !onCooldown) {
              // 对枪：敌方子弹已在直线上 → 开火抵消
              if (p.dir === aimDir) {
                self._moveDir = null
              } else {
                self._moveDir = aimDir
              }
              self._fire = true
              self.branchCounts.t2a++
              self._lastBranch = 't2a'
              return true
            }

            // 先手开火 / 冷却中等待：保持对齐以备对枪
            // 不横移——横移会脱离防守位，在密集关卡导致更多死亡
            if (p.dir === aimDir) {
              self._moveDir = null
            } else {
              self._moveDir = aimDir
            }
            self._fire = !onCooldown && self.rng.next() >= self.params.aimError
            self.branchCounts.t2a++
            self._lastBranch = 't2a'
            return true
          }

          // ---- 正常 T2a（非炮口相向 / 1HP / 冰面）----
          if (p.dir === aimDir) {
            self._moveDir = null // Already facing — stop and shoot
          } else {
            self._moveDir = aimDir // Turn to face enemy
          }
          self._fire = !onCooldown && self.rng.next() >= self.params.aimError
          self.branchCounts.t2a++
          self._lastBranch = 't2a'
          return true
        }

        // Camped too long with no kills — suppress T2a and fall through
        // to navigate, which will move the player toward the enemy.
        self._campCell = null
        self._campTicks = 0
        self._antiCampSuppress = self.params.antiCampSuppressTicks
      }
      // Enemy in line of fire but beyond effective range — fall through
      // to navigate (close the distance for high-HP enemies).
    }
    // No real enemy in line of fire (wall-only or clear) — fall through.
  } else if (self._campCell) {
    // Not in T2a (suppressed or no aimDir) — reset camp tracking.
    self._campCell = null
    self._campTicks = 0
  }
  return false
}
