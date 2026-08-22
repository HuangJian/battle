// ================================================================
// PlayerSystem — extracted from the former SimulationPlayer.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Behavior is byte-
// identical: bodies moved verbatim; `this.world` became `this.d.world`
// and cross-system calls go through the shared SimulationSystems
// registry wired once in Simulation's constructor.
// ================================================================
import {
  CELL,
  TANK,
  BULLET,
  DIR_VECTORS,
  Direction,
  MINE_ARM_MS,
  MINE_RADIUS_CELLS,
  DECOY_LIFESPAN_FRAMES,
  TICK_MS,
} from '../constants'
import { FRENZY_SHOTS } from '../config/powerups'
import { spawnBulletSpeedPxPerTick } from '../config/speed'
import { recordEnemyKill, destroyBrickAoE } from './KillPipeline'
import { genId } from './World'
import { aabb } from '../utils/helpers'
import type { Bullet, Tank } from '../types'
import type { InputLike } from './Input'
import type { SimulationSystems } from './systems'

/**
 * the Player System (per-tank input driving), plus the
 * Decoy and Mine systems (new-powerups-plan §4.4 / §4.5).
 *
 */

export class PlayerSystem {
  constructor(private d: SimulationSystems) {}
  // ================================================================
  // Player System
  // ================================================================

  /**
   * Generalized player update: drives one player tank with one input source.
   * Called twice per tick when coop is active (once for human, once for God AI).
   * When input is null (coop disabled or player2 dead), this is a no-op.
   * Lie-Back-Win-Mode §3.1: per-tank frenzy check (Q9).
   */
  updatePlayerTank(p: Tank | null, input: InputLike | null): void {
    const w = this.d.world
    if (!p || !p.alive) return
    if (p.spawnTimer > 0) return // still spawning

    // --- Per-tank 狂暴宣泄 barrage in progress: player is locked (no move /
    //     turn / other items). Auto-fire only. ---
    if ((p.frenzyTimer ?? 0) > 0) {
      this.updateFrenzy(p)
      return
    }

    // --- Active super-item release (F5 天降神兵 / F6 狂暴宣泄) ---
    // Super items are human-only (God AI wasItemPressed returns false).
    if (input && input.wasItemPressed('guard') && w.guardStock > 0) {
      this.d.enemies.activateGuard(p)
    }
    if (input && input.wasItemPressed('frenzy') && w.frenzyStock > 0) {
      this.activateFrenzy(p)
    }
    // Rewind (时光宝盒): triggered by F7
    if (input && input.wasItemPressed('rewind') && w.rewindStock > 0) {
      this.activateRewind(p)
    }

    // Movement
    if (!input) return // no input source (God AI tank without input2)
    const dir = input.getMoveDirection()
    if (dir !== null) {
      p.dir = dir
      p.moving = true
    } else {
      p.moving = false
    }

    // Firing
    if (input.isFiring()) {
      this.d.combat.tryFire(p)
    }
  }

  /**
   * Advance an active 狂暴宣泄 barrage. The player is locked to `frenzyDir`
   * and cannot move/turn/use other items (updatePlayer returns before reading
   * movement). Shells fire at 1/5 of the player's normal fire interval using
   * the player's CURRENT bullet stats (star buff included). Driven by the same
   * `frame * (1000/60)` clock as tryFire so it is deterministic/snapshot-safe.
   */
  private updateFrenzy(p: Tank): void {
    const w = this.d.world
    const now = w.frame * TICK_MS
    const dir = p.frenzyDir ?? 'up'
    const interval = p.frenzyInterval ?? 1
    let shotsLeft = p.frenzyShotsLeft ?? 0
    let lastFire = p.frenzyLastFire ?? 0
    p.dir = dir
    p.moving = false
    while (shotsLeft > 0 && now - lastFire >= interval) {
      this.spawnFrenzyShot(p, dir)
      shotsLeft -= 1
      lastFire += interval
    }
    p.frenzyShotsLeft = shotsLeft
    p.frenzyLastFire = lastFire
    const timer = (p.frenzyTimer ?? 0) - TICK_MS
    p.frenzyTimer = timer
    if (timer <= 0 || shotsLeft <= 0) {
      p.frenzyTimer = 0
      p.frenzyShotsLeft = 0
    }
  }

  /** Fire one 狂暴宣泄 shell (player's current stats, locked direction). */
  private spawnFrenzyShot(p: Tank, dir: Direction): void {
    const w = this.d.world
    const v = DIR_VECTORS[dir]
    const bx = p.x + p.w / 2 - BULLET / 2 + v.dx * (p.w / 2)
    const by = p.y + p.h / 2 - BULLET / 2 + v.dy * (p.h / 2)
    const bullet: Bullet = {
      id: genId(),
      x: bx,
      y: by,
      w: BULLET,
      h: BULLET,
      dir,
      alive: true,
      ownerId: p.id,
      ownerKind: p.kind,
      isPlayer: true,
      allegiance: 'player',
      speed: spawnBulletSpeedPxPerTick(
        p.kind,
        p.level ?? 0,
        w.bulletSeq++,
        w.frame,
        w.rules.bulletSpeedCps,
        w.rules.playerBulletSpeedPerStarCps,
      ),
      power: p.bulletPower,
      damage: p.damage,
    }
    w.addBullet(bullet)
    w.pushEvent({ type: 'bullet_fired', bullet })
  }

  /** Activate a 狂暴宣泄 barrage (consume one from inventory). */
  activateFrenzy(p: Tank): void {
    const w = this.d.world
    w.frenzyStock--
    const interval = Math.max(1, p.nextFireInterval / 5)
    p.frenzyInterval = interval
    p.frenzyShotsLeft = FRENZY_SHOTS
    p.frenzyDir = p.dir
    // Fire the first shell immediately on the next tick.
    p.frenzyLastFire = w.frame * TICK_MS - interval
    p.frenzyTimer = FRENZY_SHOTS * interval
  }

  /**
   * Activate 时光宝盒 (new-powerups-plan §4.3): consume one rewind stock and
   * trigger a manual rewind to the most recent snapshot.
   */
  private activateRewind(_p: Tank): void {
    const w = this.d.world
    if (w.rewindStock <= 0) return
    if (w.state !== 'playing') return
    // Signal Game.ts to trigger RecoveryController.beginManualRewind()
    // (actual rewind logic is in Game.ts, not Simulation — AGENTS §2.1)
    w.rewindStock--
    w.rewindPending = true
  }

  // ================================================================
  // Decoy System (new-powerups-plan §4.4)
  // ================================================================

  /**
   * Activate 诱饵 (Decoy): spawn a fake player ally that draws enemy fire.
   * The decoy moves toward enemies but never fires.
   */
  activateDecoy(p: Tank): void {
    const w = this.d.world
    // §152-W4: never spawn the decoy ON TOP of the player. tankHitsTank
    // treats spawning tanks (spawnTimer > 0) as blockers, so a same-cell
    // decoy boxes the player in for its whole 500-tick spawn + until it
    // can drive clear — hard S12 seed 934391936: the player picked the
    // decoy up at t7502 and stayed frozen at (6,14) for the final 770
    // ticks until the base fell. Find the nearest clear tank cell within a
    // 3-cell ring instead (preferring cells that do NOT sit in the
    // player's 4 orthogonal neighbours — a corridor-adjacent decoy would
    // block the player's only egress). Deterministic: no RNG, fixed scan
    // order. If every candidate is blocked, skip (jam is worse than no-op).
    const pos = this.decoySpawnCell(p)
    if (!pos) return
    // Spawn as a basic enemy (normal enemy stats) then promote to the decoy
    // role. HP/maxHp are left at what createTank('basic') set them — i.e. the
    // normal (basic) enemy HP value (user req: 诱饵 HP 取普通敌人 HP 值).
    const tank = w.createTank('basic', pos.x, pos.y, p.dir)
    tank.allegiance = 'ally'
    tank.isPlayer = false
    tank.isDecoy = true
    tank.spawnTimer = 500
    if (tank.aiState) {
      // Decoys never move (updateGuards skips them), but keep a sane goal so
      // any stray AI bookkeeping stays consistent.
      tank.aiState.strategicGoal = 'advance'
      tank.aiState.tacticalGoal = 'advance'
    }
    // Lifespan expiry (absolute frame)
    tank.guardExpireFrame = w.frame + DECOY_LIFESPAN_FRAMES
    w.allies.push(tank)
  }

  /**
   * §152-W4: nearest clear tank cell within a 3-cell ring of the player for
   * the decoy spawn, or null when every candidate is blocked (terrain / any
   * live tank, allies included). Deterministic — no RNG, fixed scan order.
   * Prefers candidates outside the player's 4 orthogonal neighbours so a
   * corridor decoy cannot seal the player's only exit.
   */
  private decoySpawnCell(p: Tank): { x: number; y: number } | null {
    const w = this.d.world
    const pcCol = Math.round(p.x / CELL)
    const pcRow = Math.round(p.y / CELL)
    const tanks = w.allTanks
    let best: { x: number; y: number; d: number } | null = null
    let bestNonOrth: { x: number; y: number; d: number } | null = null
    for (let r = pcRow - 3; r <= pcRow + 3; r++) {
      for (let c = pcCol - 3; c <= pcCol + 3; c++) {
        if (c === pcCol && r === pcRow) continue
        const x = c * CELL
        const y = r * CELL
        if (!w.isInBounds(x, y, TANK, TANK)) continue
        if (w.rectHitsTerrain(x, y, TANK, TANK)) continue
        let blocked = false
        for (let ti = 0; ti < tanks.length; ti++) {
          const t = tanks[ti]
          if (t.alive && aabb(x, y, TANK, TANK, t.x, t.y, t.w, t.h)) {
            blocked = true
            break
          }
        }
        if (blocked) continue
        const orth =
          (c === pcCol && (r === pcRow - 1 || r === pcRow + 1)) ||
          (r === pcRow && (c === pcCol - 1 || c === pcCol + 1))
        const d = Math.abs(c - pcCol) + Math.abs(r - pcRow)
        if (orth) {
          if (!bestNonOrth || d < bestNonOrth.d) bestNonOrth = { x, y, d }
        } else {
          if (!best || d < best.d) best = { x, y, d }
        }
      }
    }
    const chosen = best ?? bestNonOrth
    return chosen ? { x: chosen.x, y: chosen.y } : null
  }

  // ================================================================
  // Mine System (new-powerups-plan §4.5)
  // ================================================================

  /**
   * Place a mine at the player's current grid position.
   * Called when the player picks up a 'mine' power-up.
   */
  placeMine(p: Tank): void {
    const w = this.d.world
    const x = Math.round(p.x / CELL) * CELL
    const y = Math.round(p.y / CELL) * CELL
    w.mines.push({
      id: genId(),
      x,
      y,
      w: TANK,
      h: TANK,
      armTimer: MINE_ARM_MS,
      alive: true,
    })
    // (perf §68) New mine added — enable the updateMines loop.
    w._hasActiveMines = true
  }

  /**
   * Check mine collisions: enemy tanks stepping on armed mines, or enemy
   * bullets hitting armed mines. On detonation, deal AoE damage to nearby
   * enemies and destroy nearby brick walls (same pattern as sacrifice).
   */
  updateMines(): void {
    const w = this.d.world
    // (perf §68 Round 9) Fast path: skip the entire mines scan when no mines
    // are on the field. classic mode rarely uses mines, so this saves the
    // per-tick arm-timer loop + per-mine × per-tank AABB checks on the vast
    // majority of ticks. The flag is set by placeMine and cleared below when
    // compaction removes the last mine.
    if (!w._hasActiveMines) return

    // In-place compaction (swap-and-pop pattern of removeDeadEntities) —
    // avoids allocating a fresh array via `.filter()` every tick (AGENTS §14.1).
    // Mines are usually empty; even so the old code paid for a new [] each tick.
    const mines = w.mines
    let mw = 0
    for (let mi = 0; mi < mines.length; mi++) {
      const mine = mines[mi]
      if (!mine.alive) continue
      // Mine must be armed (armTimer <= 0) to detonate
      if (mine.armTimer > 0) {
        mines[mw++] = mine
        continue
      }

      let detonate = false

      // Check enemy tank collision
      const tanks = w.tanks
      for (let ti = 0; ti < tanks.length; ti++) {
        const tank = tanks[ti]
        if (!tank.alive || tank.allegiance !== 'enemy' || tank.spawnTimer > 0) continue
        if (aabb(mine.x, mine.y, mine.w, mine.h, tank.x, tank.y, tank.w, tank.h)) {
          detonate = true
          break
        }
      }

      // Check enemy bullet collision
      if (!detonate) {
        const bullets = w.bullets
        for (let bi = 0; bi < bullets.length; bi++) {
          const bullet = bullets[bi]
          if (!bullet.alive || bullet.allegiance !== 'enemy') continue
          if (aabb(mine.x, mine.y, mine.w, mine.h, bullet.x, bullet.y, bullet.w, bullet.h)) {
            detonate = true
            bullet.alive = false
            w._needsCleanup = true
            break
          }
        }
      }

      if (detonate) {
        // Mark consumed (matches original semantics; the mine is dropped from
        // the compacted tail below so it won't be observable next tick).
        mine.alive = false
        w._needsCleanup = true
        const cx = mine.x + mine.w / 2
        const cy = mine.y + mine.h / 2
        const radiusPx = MINE_RADIUS_CELLS * CELL

        // Damage enemies in radius (normal kill accounting)
        for (let ti = 0; ti < tanks.length; ti++) {
          const tank = tanks[ti]
          if (!tank.alive || tank.allegiance !== 'enemy' || tank.spawnTimer > 0) continue
          const tx = tank.x + tank.w / 2
          const ty = tank.y + tank.h / 2
          if (Math.hypot(tx - cx, ty - cy) <= radiusPx) {
            tank.alive = false
            w._needsCleanup = true
            this.d.effects.createExplosion(tank.x + tank.w / 2, tank.y + tank.h / 2, 'big')
            recordEnemyKill(w, tank)
            w.pushEvent({ type: 'tank_destroyed', tank, by: 'player' })
          }
        }

        // Destroy brick walls in radius
        destroyBrickAoE(w, cx, cy, radiusPx)

        this.d.effects.createExplosion(cx, cy, 'big')
        // mine is consumed — do NOT copy to compacted tail
      } else {
        mines[mw++] = mine
      }
    }
    mines.length = mw
    // (perf §68) Clear the flag when all mines have been removed by
    // detonation / compaction. Next tick updateMines will early-return until
    // placeMine sets it true again.
    if (mines.length === 0) w._hasActiveMines = false
  }
}
