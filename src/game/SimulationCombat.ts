import {
  CELL,
  BULLET,
  FIELD,
  GRID,
  DIR_VECTORS,
  DIR_DX,
  DIR_DY,
  ICE_ACCEL_TRACTION,
  ICE_DECEL_TRACTION,
  STAR_SHIELD_GRACE_MS,
  BASE_POS,
} from '../constants'
import { resolveProfile, profileToStats, PLAYER_PROGRESSION } from '../config/combat'
import { rollSpeedJitter, spawnBulletSpeedPxPerTick } from '../config/speed'
import { hasStarPerk } from '../config/rules'
import { nextFireIntervalMs } from '../config/fire-rate'
import { killScore } from '../config/score'
import { genId } from './World'
import { snap, aabb } from '../utils/helpers'
import type { Bullet, Tank } from '../types'
import type { SimulationConstructor, SimulationCore } from './SimulationCore'

/**
 * Return whether a cell belongs to the permanent in-grid base protection
 * ring. This mirrors baseRingPositions() without allocating in the bullet
 * hot path. The bottom edge is outside the field, so only the top, left, and
 * right edges are valid protection cells.
 */
function isBaseProtectionCell(col: number, row: number): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  if (row === br - 1 && col >= bc - 1 && col <= bc + 2) return true
  if (col === bc - 1 && (row === br || row === br + 1)) return true
  return col === bc + 2 && (row === br || row === br + 1)
}

/**
 * SimulationCombatMixin — the Movement System (velocity / ice-momentum / tank
 * collision), the Fire System (tryFire), and the Bullet System (travel,
 * terrain/tank/bullet collisions, base damage, star shield).
 *
 * Composes onto {@link SimulationCore}. See `Simulation.ts` for the final
 * mixin order. Cross-mixin calls (drop release, explosions) resolve to the
 * stubs declared on `SimulationCore`.
 */
export function SimulationCombatMixin<TBase extends SimulationConstructor<SimulationCore>>(
  Base: TBase,
) {
  return class SimulationCombat extends Base {
    // ================================================================
    // Movement System
    // ================================================================

    protected updateMovement(): void {
      const w = this.world
      // Cache allTanks once — tankHitsTank calls the getter per moving tank.
      // The buffer is stable during movement (no tanks added/removed).
      const allTanks = w.allTanks
      // Cache grid + FIELD for inlined isTankOnIce + isInBounds (perf §64):
      // - isTankOnIce(tank) does tileMap.get(c,r)==='ice' — direct grid[r][c]
      //   access skips one method dispatch per tank per tick.
      // - isInBounds(...) is a 4-compare boolean; inlined as `newX < 0 || ...`.
      // - canTankTraverseWater(tank) is `!!(boatTimer && boatTimer > 0)`.
      // tank.w/h are TANK=32 by invariant (World.createTank); using the literal
      // skips 2 property accesses per call site. The bounds check in
      // updateMovement guarantees tank.x ∈ [0, FIELD-TANK] ⇒ the inlined
      // isTankOnIce cell coords (floor((tank.x+16)/CELL)) are always in [0, GRID-1].
      const grid = w.tileMap.grid
      for (let ti = 0; ti < allTanks.length; ti++) {
        const tank = allTanks[ti]
        if (!tank.alive || tank.spawnTimer > 0) continue
        // A non-moving tank with residual ice velocity must still be simulated
        // so it keeps gliding to a stop; only a fully-stopped, idle tank is skipped.
        if (!tank.moving && tank.vx === 0 && tank.vy === 0) continue

        // §86c: Turn cooldown — enforce minimum turn period. After a tank
        // turns (dir changes), it must wait turnCooldownMs before turning
        // again. This blocks per-tick direction oscillation at the source
        // (the simulation refuses to turn faster than this), making the
        // God AI's dodgeOscillationCounterFire unnecessary in practice.
        const turnCd = this.world.rules?.turnCooldownMs ?? 0
        if (turnCd > 0 && tank.dir !== tank.prevMoveDir) {
          const now = w.frame * (1000 / 60)
          if (now - (tank.lastTurnMs ?? -9999) < turnCd) {
            // Cooldown active — the requested turn is deferred. Revert to
            // the previous movement direction AND halt: at turnCooldownMs
            // ≥160ms the tank would otherwise keep drifting along the old
            // axis for ~10 ticks, overshooting maze corners / walking into
            // walls / failing dodges (§95 per-seed tick-diff finding: the
            // 160ms A/B's flip-to-lose seeds — S10 s1, S12 s6, S26 s1 — all
            // diverge exactly at a deferred turn). Clearing `moving` makes
            // the velocity integrate to 0 (instant stop on normal ground;
            // on ice the residual velocity eases toward 0 via
            // ICE_DECEL_TRACTION, preserving the glide).
            tank.dir = tank.prevMoveDir ?? tank.dir
            tank.moving = false
          } else {
            // Cooldown expired — accept the turn
            tank.prevMoveDir = tank.dir
            tank.lastTurnMs = now
          }
        }

        // Enemy freeze — a frozen tank can't act, so bleed off any momentum and skip.
        if (!tank.isPlayer && w.freezeTimer > 0) {
          tank.vx = 0
          tank.vy = 0
          continue
        }

        // ---- Velocity / ice-momentum integration ----
        // Desired velocity comes from the tank's movement intent (dir when moving).
        // Inline DIR_VECTORS[tank.dir] with flat DIR_DX/DIR_DY arrays — avoids
        // a string-keyed Record lookup per tank per tick (perf §64).
        const di = tank.dir === 'up' ? 0 : tank.dir === 'down' ? 1 : tank.dir === 'left' ? 2 : 3
        const dirDx = DIR_DX[di]
        const dirDy = DIR_DY[di]
        const wantX = tank.moving ? dirDx * tank.speed : 0
        const wantY = tank.moving ? dirDy * tank.speed : 0

        // Inline isTankOnIce: tank center cell, direct grid access.
        // Equivalent to `w.tileMap.get(ic, ir) === 'ice'` for in-bounds tanks
        // (which all tanks are — see invariant above).
        const ic = Math.floor((tank.x + 16) / CELL)
        const ir = Math.floor((tank.y + 16) / CELL)
        const onIce = grid[ir][ic] === 'ice'
        if (onIce) {
          // Low traction: ease velocity toward the desired value. Accelerating
          // (target non-zero) uses ICE_ACCEL_TRACTION; decelerating (target zero
          // — input released, or the axis being abandoned on a perpendicular
          // turn) uses ICE_DECEL_TRACTION so the tank coasts. That glide is the
          // "slippery" ice feel.
          const tx = wantX === 0 ? ICE_DECEL_TRACTION : ICE_ACCEL_TRACTION
          const ty = wantY === 0 ? ICE_DECEL_TRACTION : ICE_ACCEL_TRACTION
          tank.vx += (wantX - tank.vx) * tx
          tank.vy += (wantY - tank.vy) * ty
          // Kill sub-pixel jitter once a decelerating axis has all but stopped.
          if (wantX === 0 && Math.abs(tank.vx) < 0.02) tank.vx = 0
          if (wantY === 0 && Math.abs(tank.vy) < 0.02) tank.vy = 0
        } else {
          // Normal ground: instant traction = crisp, current control (unchanged).
          tank.vx = wantX
          tank.vy = wantY
        }

        // ---- Axis-lock: keep movement strictly one axis at a time so the
        // off-axis coordinate stays grid-aligned (the collision system assumes
        // axis-aligned tanks; a tank is exactly one 2×2 block wide). The
        // dominant (larger |velocity|) axis wins; the other is zeroed. During a
        // perpendicular turn on ice the OLD axis keeps gliding until it decays
        // below the new one — i.e. you can't instantly change direction on ice.
        const axis: 'x' | 'y' = Math.abs(tank.vx) >= Math.abs(tank.vy) ? 'x' : 'y'
        if (axis === 'x') tank.vy = 0
        else tank.vx = 0

        // No actual motion this tick (both velocities rounded to 0) → done.
        if (tank.vx === 0 && tank.vy === 0) continue

        // Snap the perpendicular (off-axis) coordinate to the grid so the tank
        // stays aligned to a row/column while sliding along the other axis.
        if (axis === 'x') tank.y = snap(tank.y, CELL)
        else tank.x = snap(tank.x, CELL)

        // Try to move along the (single) velocity axis.
        const newX = tank.x + tank.vx
        const newY = tank.y + tank.vy

        // Inline isInBounds(newX, newY, TANK, TANK): TANK=32, FIELD=GRID*CELL.
        // `!isInBounds(...)` ⟺ `newX < 0 || newY < 0 || newX + 32 > FIELD || newY + 32 > FIELD`.
        if (newX < 0 || newY < 0 || newX + 32 > FIELD || newY + 32 > FIELD) {
          if (axis === 'x') {
            tank.x = tank.vx < 0 ? 0 : FIELD - 32
            tank.vx = 0
          } else {
            tank.y = tank.vy < 0 ? 0 : FIELD - 32
            tank.vy = 0
          }
          if (tank.aiState) tank.aiState.thinkTimer = 0
          continue
        }

        // Inline canTankTraverseWater: `!!(tank.boatTimer && tank.boatTimer > 0)`.
        const canTraverseWater = !!(tank.boatTimer && tank.boatTimer > 0)
        if (w.rectHitsTerrain(newX, newY, 32, 32, canTraverseWater)) {
          // Snap to the cell boundary on the travel axis and stop there.
          if (axis === 'x') {
            tank.x = snap(tank.x, CELL)
            tank.vx = 0
          } else {
            tank.y = snap(tank.y, CELL)
            tank.vy = 0
          }
          if (tank.aiState) tank.aiState.thinkTimer = 0
          continue
        }

        // Check tank-tank collision
        if (this.tankHitsTank(tank, newX, newY, allTanks)) {
          if (axis === 'x') tank.vx = 0
          else tank.vy = 0
          if (tank.aiState) tank.aiState.thinkTimer = 0
          continue
        }

        // Move is valid
        tank.x = newX
        tank.y = newY
      }
    }

    private tankHitsTank(self: Tank, newX: number, newY: number, allTanks: Tank[]): boolean {
      for (let i = 0; i < allTanks.length; i++) {
        const other = allTanks[i]
        if (other === self || !other.alive) continue
        // NOTE: spawning tanks (spawnTimer > 0) DO block movement. Previously
        // they were skipped here, which let a moving tank drive *into* a tank
        // that was still in its spawn animation. The two would overlap, and once
        // the spawn timer expired they were jammed at the corner/edge with zero
        // free directions — multiple enemies permanently stuck at the spawn point.
        // Spawn safety is still guaranteed: updateSpawning() refuses to create a
        // tank on top of any existing tank (including spawning ones), so the only
        // overlap path was movement, now closed. (Bullets still pass through
        // spawning tanks — they are invulnerable — via the separate check in
        // bulletHitsTank.)
        if (aabb(newX, newY, self.w, self.h, other.x, other.y, other.w, other.h)) {
          return true
        }
      }
      return false
    }

    // ================================================================
    // Fire System
    // ================================================================

    protected tryFire(tank: Tank): void {
      const w = this.world
      const now = w.frame * (1000 / 60)

      // EMP silencer: when empTimer > 0, ENEMY tanks cannot fire. Friendly
      // 天降神兵 guards must keep firing — they are allies, not hostiles, so the
      // player's own EMP must not neutralize them (new-powerups-plan §4.2).
      if (tank.allegiance === 'enemy' && w.empTimer > 0) return

      // Decoys never fire — they are fake tanks whose only job is to draw enemy
      // fire, never to shoot back (new-powerups-plan §4.4).
      if (tank.isDecoy) return

      // Fire-rate limiter — two mutually-exclusive models (config/rules.ts,
      // plan/classic-faithful-feel.md Phase 2):
      //
      //  - 'cooldown' (modern relax/hard/chaos): a fixed per-type TIME gate.
      //    `nextFireInterval` is frozen at the previous shot from the fire-rate
      //    standard (config/fire-rate.ts: base interval × deterministic per-fire
      //    jitter). Rate is independent of whether the previous bullet is still
      //    in flight — purely a time cadence.
      //
      //  - 'bulletCap' (classic FC-1985): fire rate is governed ONLY by the
      //    on-screen bullet cap. The player may fire again the instant the
      //    previous shell resolves (strikes terrain/a tank or leaves the field)
      //    — there is NO separate time cooldown. This is the faithful FC feel:
      //    holding fire yields a steady cadence paced by bullet *travel*, not by
      //    an artificial timer. A prior draft layered `baseFireIntervalMs()` on
      //    top of the cap; that produced a spurious ~1.2 s wait after every shot
      //    and is removed (user-reported bug, 2026-07-28).
      if (w.rules.fireModel === 'cooldown') {
        if (now - tank.lastFire < tank.nextFireInterval) return
      }

      // On-screen bullet cap (classic 'bulletCap' model, plan Phase 2). Count the
      // tank's own live bullets; block the shot once the cap is reached. The
      // cap is `maxBullets[kind]`, plus +1 for the player at/above
      // `playerDoubleShotLevel` (2★ → double-shot, FC-style). A canceled bullet
      // frees its slot on the next frame (no twin-spawn — issue #12).
      if (w.rules.fireModel === 'bulletCap') {
        // Minimum cooldown between shots: prevents instant refire when a bullet
        // resolves at close range. Without this floor, a bullet that hits a tank
        // 1 cell away resolves in 1 frame and the player fires again immediately
        // — machine-gun feel. 300ms ≈ 18 frames at 60fps is responsive but
        // prevents the exploit. (Data: rules.bulletCapMinCooldownMs.)
        if (
          w.rules.bulletCapMinCooldownMs > 0 &&
          now - tank.lastFire < w.rules.bulletCapMinCooldownMs
        ) {
          return
        }
        const cap =
          (w.rules.maxBullets[tank.kind] ?? 1) +
          (tank.kind === 'player' && (tank.level ?? 0) >= w.rules.playerDoubleShotLevel ? 1 : 0)
        let inFlight = 0
        const liveBullets = w.bullets
        for (let bi = 0; bi < liveBullets.length; bi++) {
          const b = liveBullets[bi]
          if (b.alive && b.ownerId === tank.id) inFlight++
        }
        if (inFlight >= cap) return
      }

      const v = DIR_VECTORS[tank.dir]

      // Bullet starts at the center of the tank's front edge
      const bx = tank.x + tank.w / 2 - BULLET / 2 + v.dx * (tank.w / 2)
      const by = tank.y + tank.h / 2 - BULLET / 2 + v.dy * (tank.h / 2)

      const bullet: Bullet = {
        id: genId(),
        x: bx,
        y: by,
        w: BULLET,
        h: BULLET,
        dir: tank.dir,
        alive: true,
        ownerId: tank.id,
        ownerKind: tank.kind,
        isPlayer: tank.isPlayer ?? false,
        allegiance: tank.allegiance,
        // Per-bullet speed jitter: actual = base × random(0.95, 1.05). The jitter
        // seeds off the World's monotonic `bulletSeq` (not the module-level
        // genId, not the AI's world-RNG), so it is reproducible across runs and
        // snapshot-safe while NEVER perturbing enemy AI decisions (see
        // config/speed.ts). The base (tank.bulletSpeed) comes from the per-kind
        // table there.
        speed: spawnBulletSpeedPxPerTick(
          tank.kind,
          tank.level ?? 0,
          w.bulletSeq++,
          w.frame,
          w.rules.bulletSpeedCps,
          w.rules.playerBulletSpeedPerStarCps,
        ),
        power: tank.bulletPower,
        damage: tank.damage,
      }

      w.addBullet(bullet)
      tank.lastFire = now
      // Freeze the NEXT shot's interval now: base interval × per-fire jitter
      // random(0.95, 1.05), seeded deterministically from (tank fire-count,
      // world frame) so it is reproducible from World state (snapshot/Replay-safe)
      // and — critically — does NOT depend on the global `genId` counter, which
      // is NOT reset between Worlds. Using the per-World fire-count keeps firing
      // timing identical across separate runs (determinism invariant, AGENTS §2.3)
      // and never draws from `world.rng` (which would perturb the AI's stream).
      tank.fireCount += 1
      tank.nextFireInterval = nextFireIntervalMs(
        tank.kind,
        tank.level ?? 0,
        tank.fireCount,
        w.frame,
      )
      w.pushEvent({ type: 'bullet_fired', bullet })
    }

    // ================================================================
    // Bullet System
    // ================================================================

    protected updateBullets(): void {
      const w = this.world
      // Cache allTanks once — bulletHitsTank calls the getter per bullet.
      // The buffer is stable during bullet updates (tanks may be flagged dead
      // but are not removed from the array until removeDeadEntities).
      const allTanks = w.allTanks
      // Indexed loop — `for...of` allocates an iterator per tick (AGENTS §14.1).
      const bullets = w.bullets
      for (let bi = 0; bi < bullets.length; bi++) {
        const bullet = bullets[bi]
        if (!bullet.alive) continue

        // Move
        const v = DIR_VECTORS[bullet.dir]
        bullet.x += v.dx * bullet.speed
        bullet.y += v.dy * bullet.speed

        // Out of bounds
        if (bullet.x < 0 || bullet.x > FIELD || bullet.y < 0 || bullet.y > FIELD) {
          bullet.alive = false
          w._needsCleanup = true
          this.createExplosion(bullet.x, bullet.y, 'small')
          continue
        }

        // Check terrain collision
        if (this.bulletHitsTerrain(bullet)) {
          bullet.alive = false
          w._needsCleanup = true
          continue
        }

        // Check tank collision
        if (this.bulletHitsTank(bullet, allTanks)) {
          bullet.alive = false
          w._needsCleanup = true
          continue
        }

        // Check bullet-bullet collision
        if (this.bulletHitsBullet(bullet)) {
          bullet.alive = false
          w._needsCleanup = true
          continue
        }
      }
    }

    private bulletHitsTerrain(bullet: Bullet): boolean {
      const w = this.world
      const c0 = Math.floor(bullet.x / CELL)
      const r0 = Math.floor(bullet.y / CELL)
      const c1 = Math.floor((bullet.x + bullet.w - 1) / CELL)
      const r1 = Math.floor((bullet.y + bullet.h - 1) / CELL)

      // Cache grid for inlined tileMap.get (perf §66): bulletHitsTerrain is
      // called per bullet per tick; each w.tileMap.get(c,r) is a method dispatch
      // + bounds check. Inline as grid[r][c] with the same OOB→'steel' fallback
      // that TileMap.get uses (bullets at the trailing edge can reach c1/r1 = GRID).
      const grid = w.tileMap.grid

      // A bullet can overlap the last protection brick and the base in the same
      // tick. The normal scan intentionally preserves the existing multi-brick
      // break-through behavior used by dense maze stages, but the permanent
      // base ring must be a real barrier: destroy the ring cell and stop before
      // the bullet can damage the base behind it. This also prevents a player
      // bullet from self-destroying the base through its protection wall.
      if (w.tileMap.hasBase()) {
        const v = DIR_VECTORS[bullet.dir]
        const rowStep = v.dy < 0 ? -1 : 1
        const colStep = v.dx < 0 ? -1 : 1
        const rowStart = v.dy < 0 ? r1 : r0
        const rowEnd = v.dy < 0 ? r0 : r1
        const colStart = v.dx < 0 ? c1 : c0
        const colEnd = v.dx < 0 ? c0 : c1
        for (let r = rowStart; v.dy < 0 ? r >= rowEnd : r <= rowEnd; r += rowStep) {
          const row = r >= 0 && r < GRID ? grid[r] : null
          for (let c = colStart; v.dx < 0 ? c >= colEnd : c <= colEnd; c += colStep) {
            if (!isBaseProtectionCell(c, r)) continue
            // Inline tileMap.get with OOB→'steel' (matches TileMap.get bounds).
            const type = row && c >= 0 && c < GRID ? row[c] : 'steel'
            if (type === 'brick') {
              w.tileMap.destroy(c, r)
              this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
              return true
            }
            if (type === 'steel') {
              if (bullet.power >= 2) {
                w.tileMap.destroy(c, r)
                this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
              } else {
                this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
              }
              return true
            }
          }
        }
      }

      let hit = false
      for (let r = r0; r <= r1; r++) {
        const row = r >= 0 && r < GRID ? grid[r] : null
        for (let c = c0; c <= c1; c++) {
          // Inline tileMap.get with OOB→'steel' (matches TileMap.get bounds).
          const type = row && c >= 0 && c < GRID ? row[c] : 'steel'
          if (type === 'empty') continue

          if (type === 'base') {
            // Player AND enemy bullets damage the base via the same firepower
            // formula (classic still instakills). Allied guard bullets DEFEND the
            // base and must never damage it (§31 Phase 2) — but they still stop
            // on it.
            if (bullet.allegiance !== 'ally') this.damageBase(bullet)
            return true
          } else if (type === 'brick') {
            w.tileMap.destroy(c, r)
            hit = true
            this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
          } else if (type === 'steel') {
            if (bullet.power >= 2) {
              w.tileMap.destroy(c, r)
              this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
            } else {
              // Ricochet effect on steel — small spark explosion even when not destroyed
              this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
            }
            hit = true // Bullet stops regardless
          }
        }
      }
      return hit
    }

    /**
     * Resolve a bullet's shooter into a single `firepower` number (0..100), which
     * is all the base-damage routine needs. The player's firepower scales with
     * its current star level; enemy tiers do NOT change firepower (level 0).
     */
    private bulletFirePower(bullet: Bullet): number {
      const w = this.world
      return bullet.ownerKind === 'player'
        ? resolveProfile('player', w.playerLevel).firepower
        : resolveProfile(bullet.ownerKind, 0).firepower
    }

    /**
     * Apply one bullet's damage to the base (eagle). The bullet is retained as
     * the input so the terminal event can identify the actual shooter. Damage
     * is still resolved only from the shooter's firepower, preserving the
     * single-pool base rule.
     *
     * Only when baseHp reaches 0 are all base cells cleared and the
     * `base_destroyed` event emitted; otherwise the base stays up but its damage
     * overlay is refreshed for the renderer.
     */
    private damageBase(bullet: Bullet): void {
      const w = this.world
      const dmg = this.bulletFirePower(bullet)
      const bp = w.tileMap.getBasePos()
      if (bp) {
        this.createExplosion(bp.x + CELL, bp.y + CELL, 'small')
      }
      if (dmg >= w.baseHp) {
        w.baseHp = 0
        w.tileMap.destroyAllBaseCells()
        w.pushEvent({ type: 'base_destroyed', by: bullet.ownerKind })
      } else {
        w.baseHp -= dmg
        w.tileMap.markBaseDamaged()
      }
    }

    private bulletHitsTank(bullet: Bullet, allTanks: Tank[]): boolean {
      const w = this.world
      for (let i = 0; i < allTanks.length; i++) {
        const tank = allTanks[i]
        if (!tank.alive || tank.id === bullet.ownerId) continue
        if (tank.spawnTimer > 0) continue // spawning = invulnerable

        // 3-way friendly-fire (DECISIONS.md §31 Phase 2): a bullet hits a tank
        // only when exactly one of them is on the enemy side. The player+ally
        // team has no friendly fire among/between itself; enemy bullets also
        // strike allied guards.
        const bulletEnemy = bullet.allegiance === 'enemy'
        const tankEnemy = tank.allegiance === 'enemy'
        if (bulletEnemy === tankEnemy) continue

        if (!aabb(bullet.x, bullet.y, bullet.w, bullet.h, tank.x, tank.y, tank.w, tank.h)) continue

        // Shield check
        if (tank.shieldTimer && tank.shieldTimer > 0) {
          // Bullet bounces off shield — just destroy bullet
          this.createExplosion(bullet.x, bullet.y, 'small')
          return true
        }

        // Damage tank — cosmetic / HP effect ONLY. A non-lethal hit must NEVER
        // change a tank's identity or combat capability (issue #2): we do not
        // swap `kind`, mutate `profile`, or alter any derived stat (speed,
        // bulletSpeed, fireCooldown, bulletPower). The tank keeps its type and
        // appearance; only `hp` drops (by the bullet's per-shot `damage`) and a
        // damage *decoration* (hitCount) rises so the renderer can layer on
        // type-preserving scorch/crack decals. This is the canonical
        // firepower/HP model: hits-to-kill = ceil(target.maxHp / bullet.damage).
        tank.hp -= bullet.damage
        tank.hitCount = Math.min((tank.hitCount ?? 0) + 1, 4)
        this.createExplosion(bullet.x, bullet.y, 'small')

        if (tank.hp <= 0) {
          // FC "star shield" (plan: 三星 player 被击中 → 掉落回两星状态, DECISIONS
          // §111: 2026-08-04 从 classic-only 扩展到所有难度). A max-level player does
          // NOT die from a would-be-lethal hit: the shield is spent and the tank
          // drops back to 2★, keeping its life. 0..2★ players die on a lethal hit
          // (classic 一击毙命; pool-model players still burn their whole HP buffer).
          if (tank.isPlayer && (tank.level ?? 0) >= PLAYER_PROGRESSION.maximumLevel) {
            this.spendStarShield(tank)
            return true
          }
          tank.alive = false
          w._needsCleanup = true
          this.createExplosion(tank.x + tank.w / 2, tank.y + tank.h / 2, 'big')

          if (tank.isPlayer) {
            w.pushEvent({ type: 'tank_destroyed', tank, by: 'enemy', byId: bullet.ownerId })
            w.pushEvent({ type: 'player_hit' })
          } else if (tank.allegiance === 'ally') {
            // Allied guard destroyed — no score, no kill credit, no drops. The
            // guard simply stops fighting (§31 Phase 2).
            w.pushEvent({ type: 'tank_destroyed', tank, by: 'enemy', byId: bullet.ownerId })
          } else {
            const gained = killScore(
              w.difficultyKey,
              tank.aiState?.level,
              w.stageIndex,
              w.rules,
              tank.kind,
            )
            // Lie-Back-Win-Mode Q1: route kill score to the shooter's pool.
            const isGodKill = w.coop && bullet.ownerId === w.player2?.id
            if (isGodKill) {
              w.score2 += gained
            } else {
              w.score += gained
            }
            w.killCount++
            // Accompanying "balance" enemies (isExtra) are outside the per-stage
            // 20-enemy count, so they never decrement enemiesRemaining / block
            // stage clear — but they still count as a normal kill for score
            // (§31 Phase 2).
            if (!tank.isExtra) w.enemiesRemaining--
            w.addPopup({
              id: genId(),
              x: tank.x,
              y: tank.y,
              text: String(gained),
              timer: 1500,
            })
            w.pushEvent({ type: 'tank_destroyed', tank, by: 'player', byId: bullet.ownerId })

            // --- Item drop rules (item-drop v1, DECISIONS.md §30) ---
            // 1) Bonus enemies (level-design flagged) drop a power-up on death.
            // 2) Elite (commander-tier) enemies always drop a power-up on death.
            // 3) Every 10th enemy killed drops a power-up (kill-cadence reward).
            // 4) Score milestone: every SCORE_DROP_INTERVAL (5000) points
            //    accumulated drops a power-up. A single large score gain can
            //    cross several milestones at once and thus drop several.
            // A drop triggered by the FINAL enemy of a non-final stage is deferred
            // (buffered on world.pendingDrops) so the stage-clear transition
            // doesn't wipe it; it is released on the first enemy kill of the next
            // stage — which may therefore drop several power-ups at once.
            // Extra (balance) enemies are excluded — they don't progress drops.
            if (!tank.isExtra) {
              this.flushPendingDrops() // release drops deferred from a prior stage
              const r = w.rules
              // Collect this kill's guaranteed drops, each anchored on the slain
              // enemy's tile. The rule set depends on the active GameplayRules.
              const drops: { x: number; y: number }[] = []
              if (r.dropSchedule === 'fixed') {
                // FC: the power-up carrier enemies (marked `bonus` at spawn from
                // `fixedDropKillIndices`) drop when destroyed — faithful to the
                // 1985 game where the flashing red enemy IS the drop, regardless
                // of kill order. No kill-counter logic leaks in here.
                if (tank.bonus) {
                  drops.push({ x: tank.x, y: tank.y })
                }
              } else {
                const isElite = tank.aiState?.isCommander === true
                const isTenthKill =
                  r.dropOnEveryNKills > 0 && w.killCount % r.dropOnEveryNKills === 0
                if (
                  tank.bonus ||
                  (r.dropOnEliteKill && isElite) ||
                  (r.dropOnEveryNKills > 0 && isTenthKill)
                ) {
                  drops.push({ x: tank.x, y: tank.y })
                }
                // Score milestone: every `dropOnScoreMilestone` points crossed in
                // this single kill can drop several power-ups at once.
                if (r.dropOnScoreMilestone > 0) {
                  const beforeScore = w.score - gained
                  const milestones =
                    Math.floor(w.score / r.dropOnScoreMilestone) -
                    Math.floor(beforeScore / r.dropOnScoreMilestone)
                  for (let i = 0; i < milestones; i++) drops.push({ x: tank.x, y: tank.y })
                }
              }

              if (drops.length > 0) {
                const isFinalEnemy = w.enemiesRemaining <= 0
                const hasNextStage = w.stageIndex + 1 < w.totalStages
                if (isFinalEnemy && hasNextStage) {
                  for (const d of drops) w.pendingDrops.push(this.buildDrop(d)) // defer
                } else {
                  for (const d of drops) this.spawnPowerUp(d) // drop immediately
                }
              }
            }
          }
        } else {
          // Armor tank flash
          if (tank.kind === 'armor') {
            tank.flashTimer = 200
          }
        }
        return true
      }
      return false
    }

    /**
     * FC "star shield" (plan: 三星 player 被击中 → 掉落回两星状态; all difficulties
     * since DECISIONS §111, 2026-08-04).
     *
     * Called from `bulletHitsTank` when a max-level player would otherwise take
     * a lethal hit. The top star is spent: the tank reverts to the next-lower
     * star level (2★), its stats are re-derived from the new profile, HP is
     * restored to full, and a brief `STAR_SHIELD_GRACE_MS` invulnerability is
     * granted so a coincident bullet in the same volley cannot instantly
     * re-kill the now-2★ tank. The player does NOT lose a life. Both the live
     * `tank.level` and `world.playerLevel` are kept in sync (the canonical
     * source for base-damage derivation).
     *
     * Demotion semantics: always `maximumLevel - 1` (2★) — i.e. below the
     * shield threshold — NOT "current level − 1". In classic (level cap 3)
     * that is exactly "lose one star". In pool modes (levels accumulate
     * unboundedly) a 4★+ player therefore loses more than one star in a single
     * spend, but the shield can NEVER chain (a demoted player is below the
     * threshold), preserving the classic single-spend privilege.
     */
    private spendStarShield(tank: Tank): void {
      const w = this.world
      const newLevel = PLAYER_PROGRESSION.maximumLevel - 1 // 3★ → 2★
      tank.level = newLevel
      w.playerLevel = newLevel
      const stats = profileToStats(resolveProfile('player', newLevel), 'player', newLevel, w.rules)
      tank.speed = stats.speed * (w.rules.speedJitter ? rollSpeedJitter(w.rng) : 1)
      tank.bulletSpeed = stats.bulletSpeed
      tank.bulletPower = stats.bulletPower
      tank.fireCooldown = stats.fireCooldown
      tank.maxHp = stats.maxHp
      tank.hp = stats.maxHp
      tank.profile = resolveProfile('player', newLevel)
      // Functional star ladder (classic): perks are cumulative (a 2★ tank keeps
      // the fast bullet earned at 1★), so query across every level ≤ current.
      if (w.rules.starModel === 'functional') {
        if (hasStarPerk(w.rules, newLevel, 'fastBullet')) {
          tank.bulletSpeed = stats.bulletSpeed * w.rules.fastBulletMult
        }
        if (hasStarPerk(w.rules, newLevel, 'steelPierce')) {
          tank.bulletPower = 2
        }
      }
      // Brief grace: the shield was just spent, so a same-volley bullet can't
      // immediately re-kill the demoted 2★ player.
      tank.shieldTimer = STAR_SHIELD_GRACE_MS
      w.pushEvent({ type: 'player_hit' })
    }

    private bulletHitsBullet(bullet: Bullet): boolean {
      const w = this.world
      const bullets = w.bullets
      for (let i = 0; i < bullets.length; i++) {
        const other = bullets[i]
        if (other === bullet || !other.alive) continue
        // Bullets cancel only across opposing sides (player/ally vs enemy).
        const bulletEnemy = bullet.allegiance === 'enemy'
        const otherEnemy = other.allegiance === 'enemy'
        if (bulletEnemy === otherEnemy) continue
        if (aabb(bullet.x, bullet.y, bullet.w, bullet.h, other.x, other.y, other.w, other.h)) {
          other.alive = false
          w._needsCleanup = true
          this.createExplosion((bullet.x + other.x) / 2, (bullet.y + other.y) / 2, 'small')
          return true
        }
      }
      return false
    }
  }
}
