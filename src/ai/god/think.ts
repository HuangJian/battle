// Moved verbatim from GodAIInput.ts during the giant-file split — the core
// decision loop (private think()) relocated as thinkImpl(self) following the
// §0.5 `<name>Impl(self, ...)` convention.
import type { GodAIInput } from '../GodAIInput'
import type { Cell } from '../../utils/pathfind'
import type { Direction } from '../../constants'
import { BASE_POS, CELL } from '../../constants'
import { ALL_DIRS } from '../../utils/helpers'
import { scanAheadImpl, shouldFireBreakThroughImpl, aimSurvivesTurnImpl } from './FireControl'

export function thinkImpl(self: GodAIInput): void {
  if (self._thought) return
  self._thought = true

  const w = self.world
  const p = self.controlledTank(w)
  if (!p || !p.alive || p.spawnTimer > 0) {
    self._moveDir = null
    self._fire = false
    self.branchCounts.dead++
    return
  }

  // ---- Cluster C: per-tick snapshots (built once, reused across modules) ----
  // These mirror the exact filters the god/* sub-modules used to run on every
  // call, in the same iteration order, so no decision (incl. enemies[0]
  // tie-breaks) changes. Pure recomputation elimination, not a behavior change.
  const tanks = w.tanks
  self._enemies.length = 0
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.alive && t.spawnTimer <= 0) self._enemies.push(t)
  }
  const all = w.allTanks
  self._otherTanks.length = 0
  for (let i = 0; i < all.length; i++) {
    const o = all[i]
    if (o.alive) self._otherTanks.push(o)
  }

  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const now = w.frame * (1000 / 60)

  // P0.1: Decrement anti-camp suppression every tick the player is alive.
  if (self._antiCampSuppress > 0) self._antiCampSuppress--
  // §84: Decrement aggressive camp suppression every tick.
  if (self._aggCampSuppress > 0) self._aggCampSuppress--

  // ---- M6: Cooldown-aware firing ----
  // In 'bulletCap' mode (classic FC), the engine gates fire by on-screen
  // bullet count, NOT by a time cooldown. The AI must mirror this:
  // "on cooldown" means the player's bullet is still in flight (cap
  // reached), not that a timer hasn't elapsed. Using the time check here
  // would suppress fire for ~1.3s after each shot even though the engine
  // allows refire the instant the previous bullet resolves — this was the
  // #1 root cause of the AI's abysmal kill count (1-3 kills/game) in classic.
  let onCooldown: boolean
  if (w.rules.fireModel === 'bulletCap') {
    const cap =
      (w.rules.maxBullets['player'] ?? 1) +
      ((p.level ?? 0) >= w.rules.playerDoubleShotLevel ? 1 : 0)
    let inFlight = 0
    const bullets = w.bullets
    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (b.alive && b.ownerId === p.id) {
        if (++inFlight >= cap) break // early exit — cap reached
      }
    }
    onCooldown = inFlight >= cap
  } else {
    onCooldown = now - p.lastFire < p.nextFireInterval
  }

  // ---- S8: Freeze window — aggressive hunt mode ----
  // When enemies are frozen, the player can hunt freely — enemies can't
  // fight back or approach the base. This is a free-clear window.
  const frozen = w.freezeTimer > 0

  // ---- S9: Shield — skip dodge but DON'T abandon defense ----
  // The 3-second respawn shield makes the player invulnerable, so dodge
  // is unnecessary. But the player must STILL defend the base — chasing
  // enemies across the map during the shield window leaves the base
  // undefended (the #1 cause of 330-tick base losses in classic).
  const shielded = (p.shieldTimer ?? 0) > 0

  // ---- S8: Set aggressive mode (freeze only, NOT shield) ----
  self.aggressive = frozen

  // ---- Scan for enemy targets (global vision, T9 priority) ----
  const aimDir = self.findEnemyDirection(pcx, pcy)

  // ---- Threat assessment (dodge incoming bullets) ----
  // Dodge FIRST: survive before defending the base.
  const threat = shielded ? null : self.findMostDangerousBullet(pcx, pcy)

  if (threat) {
    if (threat.id !== self.lastThreatId) {
      self.lastThreatId = threat.id
      self.reactionCounter = self.params.reactionDelay
    }

    if (self.reactionCounter > 0) {
      self.reactionCounter--
      // While reacting, keep navigating but fire only at targets in facing dir.
      self._moveDir = self.followPath()
      if (!self._moveDir) self._moveDir = self.directMove(self.playerCell())
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
      return
    }

    // Dodge: move perpendicular to the bullet (M3: verify safety).
    self._moveDir = self.dodgeDirection(threat, pcx, pcy)
    // §86: Track dodge state for oscillation detection + persistence/hysteresis.
    // _lastDodgeThreatId is always set (needed by oscillation detection,
    // hysteresis, and persistence in ThreatAssessor). _lastDodgeDir is always
    // set (needed by oscillation detection to compare against next tick's dir).
    // _dodgeFlipCount tracks consecutive direction flips for the same threat.
    if (threat.id === self._lastDodgeThreatId && self._lastDodgeDir !== null) {
      // Same threat as last tick — check if direction flipped.
      if (self._moveDir !== null && self._moveDir !== self._lastDodgeDir) {
        self._dodgeFlipCount++
      } else {
        // Direction stable or null — reset flip counter.
        self._dodgeFlipCount = 0
      }
    } else {
      // New threat — reset flip counter.
      self._dodgeFlipCount = 0
    }
    self._lastDodgeThreatId = threat.id
    self._lastDodgeDir = self._moveDir
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
    self.branchCounts.dodge++
    return
  }

  // No threat — reset reaction state.
  self.reactionCounter = 0
  self.lastThreatId = -1
  // §86: reset dodge state when no threat is active.
  self._lastDodgeThreatId = -1
  self._lastDodgeDir = null
  self._dodgeFlipCount = 0

  // ---- T8: Base bullet interception (ultimate defense) ----
  // Check AFTER dodge (survive first) but BEFORE aggressive/T2a.
  // Skip only when enemies are frozen (aggressive hunt — no bullets to
  // intercept). When shielded, the player can still intercept bullets
  // headed for the base — the shield protects the player, not the base.
  // Gap B (plan §3): skip entirely when the stage has no base.
  if (!self.aggressive && self.hasBase) {
    const baseThreat = self.findBulletThreatToBase()
    if (baseThreat) {
      const interceptCell = self.baseBulletInterceptCell(baseThreat)
      if (interceptCell) {
        self._moveDir = self.navigateTowards(interceptCell)
        // Fire to intercept the bullet (T5 extended to base defense).
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
        self.branchCounts.t8++
        return
      }
    }
  }

  // ---- S8/S9: Aggressive mode (freeze or shield) ----
  if (self.aggressive) {
    // Skip defense, go straight for the nearest enemy or power-up.
    //
    // §80: `aimSurvivesTurnImpl` MUST be evaluated BEFORE `scanAheadImpl`
    // below — both write into the shared `self._scanResult`, so running the
    // guard afterwards would clobber `aggScan`. The `&&` short-circuit gives
    // us that ordering for free. When the guard rejects the aim (the turn's
    // grid-snap would shove the tank off the firing line) we fall through to
    // the navigate path, which has real stall detection — this is what
    // breaks the period-2 freeze-window deadlock.
    if (aimDir && self._aggCampSuppress <= 0 && aimSurvivesTurnImpl(self, p, aimDir)) {
      // T2a: stop-and-aim — check if enemy is visible (no steel blocking).
      // Without this check, the AI fires through steel walls at enemies
      // it can see via global vision but cannot actually hit.
      // Inline scanAheadImpl directly (perf §66): the thin scanAhead
      // wrapper adds ~14ms (2.8%) of function-call overhead across 30 games.
      // V8 does not inline it because scanAheadImpl is large (100+ lines).
      const aggScan = scanAheadImpl(self, pcx, pcy, aimDir)
      // §74: Don't fire when a base-protection wall is on the other offset
      // line — the bullet travels from the player center (one of the two
      // offset columns) and would hit the base wall, not the enemy.
      // §74: Don't fire when a base-protection wall is closer than (or at
      // the same distance as) the enemy on the other offset line. The
      // 6px bullet spans both offset columns, so it WILL hit a closer base
      // wall before reaching the enemy. But if the enemy is closer, the
      // bullet hits the enemy first — firing is safe.
      if (
        aggScan.enemy &&
        !(aggScan.baseWall && aggScan.baseWallDist <= aggScan.enemyDist) &&
        !(aggScan.baseSteel && (p.level ?? 0) >= 3)
      ) {
        // §84: Aggressive stall detection — track how long the player has
        // been stopped at this cell. The aggressive branch has NO anti-stall
        // guard (unlike T2a's _campTicks and navigate's _navStuckTicks).
        // Without this, the player can sit at one cell firing at an enemy
        // whose body is slightly offset from the bullet path (the 6px bullet
        // passes above/below the 32px tank) for the ENTIRE freeze window.
        // When camping exceeds aggCampTimeoutTicks with no kills, fall
        // through to navigate, which repositions the player toward the enemy.
        if (self.params.aggCampTimeoutTicks > 0) {
          const pc84 = self.playerCell()
          if (
            self._aggCampCell &&
            Math.abs(self._aggCampCell.col - pc84.col) <= 1 &&
            Math.abs(self._aggCampCell.row - pc84.row) <= 1
          ) {
            self._aggCampTicks++
            if (w.killCount !== self._aggCampKillsAtStart) {
              self._aggCampTicks = 1
              self._aggCampKillsAtStart = w.killCount
            }
          } else {
            self._aggCampCell = { col: pc84.col, row: pc84.row }
            self._aggCampTicks = 1
            self._aggCampKillsAtStart = w.killCount
          }

          if (
            self._aggCampTicks > self.params.aggCampTimeoutTicks &&
            w.killCount === self._aggCampKillsAtStart
          ) {
            // Camped too long with no kills — suppress aggressive
            // stop-and-aim for a while and fall through to navigate.
            // Without the suppress, the player re-enters stop-and-aim on
            // the very next tick (aimDir still finds the enemy, scanAhead
            // still finds it) and camps for another full timeout cycle —
            // net movement: ~1 tick of navigate per 120 ticks of camp.
            self._aggCampCell = null
            self._aggCampTicks = 0
            self._aggCampSuppress = self.params.antiCampSuppressTicks
            // Fall through to power-up / navigate below.
          } else {
            if (p.dir === aimDir) {
              self._moveDir = null
            } else {
              self._moveDir = aimDir
            }
            self._fire = !onCooldown && self.rng.next() >= self.params.aimError
            return
          }
        } else {
          if (p.dir === aimDir) {
            self._moveDir = null
          } else {
            self._moveDir = aimDir
          }
          self._fire = !onCooldown && self.rng.next() >= self.params.aimError
          return
        }
      }
      // Enemy behind obstacle — fall through to navigate toward it.
    }
    // No enemy in row/col — check for power-up (S5).
    const puTarget = self.findPowerUpTarget(pcx, pcy)
    if (puTarget) {
      self._moveDir = self.navigateTowards(puTarget)
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
      return
    }
    // Navigate to nearest enemy.
    self._moveDir = self.followPath()
    if (!self._moveDir) self._moveDir = self.directMove(self.playerCell())
    // Proactive fire — but ALWAYS check shouldFireInDir to avoid shooting
    // the player's own base (T6). In classic instant combat the base has
    // 1 HP, so a single self-inflicted bullet destroys it.
    if (self._moveDir && !self.canMoveDir(p, self._moveDir)) {
      // §70/§74: break-through fire — never fire through base brick/steel
      // (§70) or at steel the player can't pierce (§74). Both guards live
      // in shouldFireBreakThroughImpl, which also drops the old `bs.enemy ||
      // ...` short-circuit that fired through the base wall on dual-offset
      // scans (DECISIONS §75 / commit 54600f9 — 4 S32 player suicides).
      const bs = scanAheadImpl(self, pcx, pcy, self._moveDir)
      const lvl = p.level ?? 0
      if (shouldFireBreakThroughImpl(bs, lvl, self.params.steelFireGate)) {
        self._fire = !onCooldown
      }
    } else {
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
    }
    self.branchCounts.aggressive++
    return
  }

  // §84: Reset aggressive camp tracking when not in aggressive mode.
  if (self._aggCampCell) {
    self._aggCampCell = null
    self._aggCampTicks = 0
  }
  if (self._aggCampSuppress > 0) self._aggCampSuppress = 0

  // ---- T2a: Stop-and-aim (enemy in same row/col) ----
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
  // fall through to navigate and hunt the enemy directly. A suppression
  // timer (antiCampSuppressTicks) ensures the player gets enough
  // consecutive navigate ticks to actually move away from the stuck cell.
  //
  // P1: Skip T2a when the base is under threat and the player is too far
  // from the base. Camping far from the base while enemies approach it
  // was the #1 cause of base_destroyed gameovers.
  //
  // D1 (guard band mode): when enabled, skip T2a on fast/power tank
  // threats near the base ONLY (not armor — armor is slow and can wait).
  // This is the targeted version of the guard band: the player camps at
  // armor for efficient point-blank kills, but the instant a fast tank
  // approaches the base, it disengages to intercept. The previous
  // untargeted version (any base threat) was too aggressive and caused
  // the player to disengage from armor too often, increasing deaths.
  const fastThreat = self.params.guardBandMode > 0 && self.hasFastThreatNearBase()

  const skipT2aForDefense =
    self.hasBase &&
    (fastThreat ||
      (self.isBaseUnderThreat() &&
        Math.abs(self.playerCell().col - BASE_POS.col) +
          Math.abs(self.playerCell().row - BASE_POS.row) >
          self.params.maxPlayerDistFromBase))

  if (aimDir && self._antiCampSuppress <= 0 && !skipT2aForDefense) {
    // Inline scanAheadImpl (perf §66, see aggressive branch above).
    const scan = scanAheadImpl(self, pcx, pcy, aimDir)

    // §74: Don't enter T2a when a base-protection wall is closer than
    // (or at the same distance as) the enemy on the other offset line.
    // The 6px bullet spans both offset columns. If the base wall is
    // closer, the bullet hits it before the enemy → suicide. If the
    // enemy is closer, the bullet hits the enemy first → safe to fire.
    // Fall through to navigate when blocked by a closer base wall.
    if (
      scan.enemy &&
      !(scan.baseWall && scan.baseWallDist <= scan.enemyDist) &&
      !(scan.baseSteel && (p.level ?? 0) >= 3)
    ) {
      // §56: dynamic T2a range based on enemy kind.
      // For non-armor enemies (basic/fast/power): use t2aMaxRange (15) —
      // one shot kills at any distance, no DPS penalty for range.
      // For armor (4 hitsToKill): use t2aHighHpMaxRange (2) — close combat.
      // At long range, 4 shots × 1s travel = 4s of camping; at point-blank,
      // 4 shots in <0.5s. The approach time is always worth it for 4-HP armor.
      // Note: in the instant combat model, maxHp = hitsToKill × referenceDamage,
      // so ALL enemies have maxHp >= 100. The kind check is the correct way
      // to identify armor (4 hitsToKill) vs basic/fast/power (1 hitsToKill).
      const effectiveRange =
        scan.enemyKind === 'armor' ? self.params.t2aHighHpMaxRange : self.params.t2aMaxRange
      if (scan.enemyDist <= effectiveRange) {
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

        if (!campedTooLong) {
          // ---- §49: 炮口相向分场景策略 ----
          // 当敌人面向 player 时，根据敌人类型采取不同策略：
          //   - 冰面：跳过（垂直移动在冰面上失控）
          //   - 1HP 敌人：正常 T2a 开火（一枪击毙），但对枪抵消仍然生效
          //     （对枪是开火行为，不是移动闪避——与"1HP 不闪避"不矛盾）
          //   - Armor（多血）：对枪抵消 + 保持对齐等待
          //
          // 对枪抵消对所有敌人类型都适用：当敌方子弹已在直线上时，
          // 开火抵消比打死敌人更安全（子弹被消除→玩家安全）。
          // 120-seed 验证：对枪对 ALL 敌人 +5 wins，仅 armor +1 win。
          // §49-revisit: 炮口相向对枪抵消 is parameterized for A/B.
          // counterFire=0 → facing stays null → plain T2a (pre-§52 form).
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
              return
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
            return
          }

          // ---- 正常 T2a（非炮口相向 / 1HP / 冰面）----
          if (p.dir === aimDir) {
            self._moveDir = null // Already facing — stop and shoot
          } else {
            self._moveDir = aimDir // Turn to face enemy
          }
          self._fire = !onCooldown && self.rng.next() >= self.params.aimError
          self.branchCounts.t2a++
          return
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

  // ---- S5: Power-up economy (normal mode) ----
  // Check for power-ups when no enemy is in line of fire. Previously this
  // only ran in aggressive mode (freeze/shield), wasting bomb/star pickups.
  // Now the AI opportunistically grabs power-ups when it's safe to divert.
  // P1: Skip power-ups when the base is under threat — defense first.
  // P3.2: Also skip when there are enemies within 5 cells of the player —
  // chasing power-ups while enemies are nearby was a major cause of
  // defense-collapse gameovers on S6/S26/S32 (player diverted to a power-up
  // at the top of the map while enemies destroyed the base).
  if ((!aimDir || onCooldown) && !(self.hasBase && self.isBaseUnderThreat())) {
    // P3.2: Don't divert to power-ups when enemies are close.
    const pc2 = self.playerCell()
    let nearbyEnemy = false
    // Cluster C: reuse the per-tick enemy snapshot.
    const nearbyScan = self._enemies.length > 0 ? self._enemies : w.tanks
    for (let ni = 0; ni < nearbyScan.length; ni++) {
      const t = nearbyScan[ni]
      if (!t.alive || t.spawnTimer > 0) continue
      const tc = self.tankCell(t)
      if (Math.abs(tc.col - pc2.col) + Math.abs(tc.row - pc2.row) <= 5) {
        nearbyEnemy = true
        break
      }
    }
    if (!nearbyEnemy) {
      const puTarget = self.findPowerUpTarget(pcx, pcy)
      if (puTarget) {
        self._moveDir = self.navigateTowards(puTarget)
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
        self.branchCounts.powerup++
        return
      }
    }
  }

  // ---- T2b: Navigate towards target (distance-adaptive) ----
  // Far from target (>5 cells): A* pathfinding routes around walls via
  // corridors — essential for maze stages. A* finds the corridor, not the
  // direct path through walls.
  //
  // Close to target (≤5 cells): directMove chases the moving enemy
  // directly, adjusting every tick. A* paths go stale before the player
  // arrives (the enemy moves away), causing the player to chase the
  // enemy's old position — directMove tracks the enemy's current position.
  //
  // When A* can't find a path (target walled off), directMove breaks
  // through brick walls by firing at them.
  //
  // P0.3: Navigate stuck escape — if the player has been at the same cell
  // in the navigate branch for too long (pursuit loop with a faster enemy),
  // override the target to the map center. This breaks the loop by moving
  // the player to a crossroads position where enemies are more likely to
  // cross its row/col, creating new T2a opportunities.
  //
  // Fire control in classic bulletCap mode (1 bullet in flight):
  // - If blocked by a wall in the path direction → fire to break through.
  // - If moving freely → fire only at enemies in the line of fire.
  const pc = self.playerCell()
  if (
    self._navStuckCell &&
    self._navStuckCell.col === pc.col &&
    self._navStuckCell.row === pc.row
  ) {
    self._navStuckTicks++
  } else {
    self._navStuckCell = { col: pc.col, row: pc.row }
    self._navStuckTicks = 1
  }

  // Reset stuck timer when a kill happens (player is making progress).
  if (self._navStuckTicks > 1 && w.killCount !== self._campKillsAtStart) {
    self._navStuckTicks = 1
    self._campKillsAtStart = w.killCount
  }

  const navStuck = self._navStuckTicks > self.params.navStuckTicks

  let navTarget: Cell | null
  // P3.1: When nav-stuck triggers, only go to center if the player is
  // NOT already at/near center. Going to center when already there causes
  // a deadlock (target == current cell → no movement → stuck forever,
  // which was the S9 root cause: player stuck at (12,12) for 2600+ ticks).
  // When the player IS at/near center, chase the nearest enemy directly
  // (directMove breaks through walls) — this gets the player moving.
  const distToCenter = Math.abs(pc.col - 12) + Math.abs(pc.row - 12)
  const stuckAtCenter = distToCenter <= 2
  if (navStuck && !stuckAtCenter) {
    navTarget = { col: 12, row: 12 }
  } else {
    navTarget = self.selectTarget(pc)
  }

  const navDist = navTarget
    ? Math.abs(navTarget.col - pc.col) + Math.abs(navTarget.row - pc.row)
    : Infinity

  if (navStuck && !stuckAtCenter) {
    // P2.2: Stuck too long — break the loop. Try A* to center first, then
    // fall back to any passable direction (not directMove, which would
    // re-select the enemy target and re-enter the stuck loop). Trying any
    // open direction ensures the player physically moves away from the
    // stuck cell, which is the whole point of the escape.
    self._moveDir = self.navigateTowards(navTarget!)
    if (!self._moveDir) {
      // A* failed (walled off) — try directions toward center first,
      // then any passable direction.
      const dx = navTarget!.col - pc.col
      const dy = navTarget!.row - pc.row
      const pref: Direction[] = []
      if (Math.abs(dy) > Math.abs(dx)) {
        pref.push(dy > 0 ? 'down' : 'up')
        pref.push(dx > 0 ? 'right' : 'left')
      } else {
        pref.push(dx > 0 ? 'right' : 'left')
        pref.push(dy > 0 ? 'down' : 'up')
      }
      let moved = false
      for (const d of pref) {
        if (self.canMoveDir(p, d)) {
          self._moveDir = d
          moved = true
          break
        }
      }
      if (!moved) {
        // All preferred directions blocked — try any open direction.
        for (const d of ALL_DIRS) {
          if (self.canMoveDir(p, d)) {
            self._moveDir = d
            break
          }
        }
      }
    }
  } else if (navStuck && stuckAtCenter) {
    // P3.1: Stuck at/near center — chase nearest enemy directly instead
    // of re-targeting center. directMove breaks through brick walls,
    // which (combined with the A* dig-through-brick fix) gets the player
    // moving toward enemies instead of deadlocking at center.
    self._moveDir = self.directMove(pc)
    if (!self._moveDir) {
      // directMove also failed — try any passable direction to get moving.
      for (const d of ALL_DIRS) {
        if (self.canMoveDir(p, d)) {
          self._moveDir = d
          break
        }
      }
    }
  } else if (navDist <= 5) {
    // Close range — directMove (responsive, tracks moving enemies).
    self._moveDir = self.directMove(pc)
  } else {
    // Long range — A* pathfinding (finds corridors in mazes).
    self._moveDir = self.followPath()
    if (!self._moveDir) {
      // A* failed or path exhausted — fall back to direct movement.
      self._moveDir = self.directMove(pc)
    }
  }
  // §68-v2: Path threat check — don't move into crossfire.
  // After navigation determines _moveDir, check if the path ahead has
  // bullets that would arrive at any cell before the player clears it.
  // If threatened: try alternative directions (perpendicular first,
  // then backward) or stay put. Does NOT do a perpendicular dodge —
  // the player either detours or waits, avoiding navigation oscillation.
  // Only runs in the navigate branch (T2b); T8/T2a/aggressive are exempt.
  if (!shielded && self.params.crossfireAwareness > 0 && self._moveDir && p.speed > 0.1) {
    const pathThreat = self.findPathThreat(pcx, pcy, self._moveDir, p.speed)
    if (pathThreat) {
      // Try to find a safe alternative direction. If none found,
      // KEEP the original direction — don't stop! Stopping in a crossfire
      // is more dangerous than continuing; the existing dodge system
      // (findMostDangerousBullet) will handle the bullet when it arrives.
      const safeDir = self.findSafeMoveDir(pcx, pcy, self._moveDir, p.speed)
      if (safeDir) {
        self._moveDir = safeDir
      }
    }
  }
  // §48-revisit: Trap avoidance (user idea 2). After navigation determines
  // _moveDir, check the NEXT cell for a surround risk (few exits + enemies
  // nearby). If it's a trap, override toward open space / the base. Runs at
  // the END of navigation, so it only perturbs the final move — dodge/T8/T2a
  // priorities are intact (same placement discipline as §68-v2 above).
  if (!shielded && self.params.trapAvoidance > 0 && self._moveDir) {
    self._moveDir = self.trapAvoidance(p, self._moveDir)
  }
  // §85: Close-range enemy exposure check — don't turn your back on a
  // close enemy. If an enemy is within closeCombatDangerRange cells,
  // aligned with the player (same row/col), has no wall between them,
  // and the player's moveDir is NOT toward that enemy, cancel the move
  // and face the enemy to fire instead. This prevents the "turn and walk
  // away from a close enemy, get shot in the back" death pattern.
  if (!shielded && self.params.closeCombatDangerCheck > 0 && self._moveDir) {
    const dangerDir = self.closeCombatExposure(
      pcx,
      pcy,
      self._moveDir,
      self.params.closeCombatDangerRange,
    )
    if (dangerDir) {
      // Cancel the move — face the enemy and fire.
      self._moveDir = p.dir === dangerDir ? null : dangerDir
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dangerDir)
      self.branchCounts.navigate++
      return
    }
  }
  // Fire control: when blocked by a breakable wall (verified by
  // canMoveOrBreak in directMove), fire immediately to break through.
  // Don't check shouldFireInDir here — it might fire at enemy bullets
  // (T5) instead of the wall, leaving the player stuck. When moving
  // freely, fire only at enemies (not walls) to save the bullet cap.
  if (self._moveDir && !self.canMoveDir(p, self._moveDir)) {
    // §70/§74: break-through fire — never fire through base brick/steel
    // (§70) or at steel the player can't pierce (§74). Both guards live
    // in shouldFireBreakThroughImpl, which also drops the old `bs.enemy ||
    // ...` short-circuit that fired through the base wall on dual-offset
    // scans (DECISIONS §75 / commit 54600f9 — 4 S32 player suicides).
    const bs = scanAheadImpl(self, pcx, pcy, self._moveDir)
    const lvl = p.level ?? 0
    if (shouldFireBreakThroughImpl(bs, lvl, self.params.steelFireGate)) {
      self._fire = !onCooldown
    }
  } else {
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir, false)
  }
  self.branchCounts.navigate++
}
