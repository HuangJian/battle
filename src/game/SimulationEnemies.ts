import { CELL, TANK, GRID, DIR_VECTORS, Direction } from '../constants'
import { SACRIFICE_BASE_RADIUS_CELLS } from '../config/powerups'
import { killScore } from '../config/score'
import { genId } from './World'
import { aabb } from '../utils/helpers'
import type { Tank, TankKind } from '../types'
import type { SimulationConstructor, SimulationCore } from './SimulationCore'

/** 天降神兵 guard lifespan: 2 minutes at 60 ticks/s (§31 Phase 2). */
const GUARD_LIFESPAN_FRAMES = 120 * 60
/** Guard kinds are randomly chosen; all use normal enemy combat stats. */
const GUARD_KINDS: TankKind[] = ['basic', 'fast', 'power', 'armor']
/** Accompanying "balance" enemies use a lighter pool. */
const EXTRA_ENEMY_KINDS: TankKind[] = ['basic', 'fast', 'power']

/**
 * SimulationEnemiesMixin — the allied guard system (天降神兵, §31 Phase 2),
 * the 同归于尽 sacrifice AoE, and the enemy AI layer (Tactical Intelligence
 * Framework + command-authority recompute).
 *
 * Composes onto {@link SimulationCore}. See `Simulation.ts` for the final
 * mixin order. Cross-mixin calls (firing, explosions, commander recompute)
 * resolve to the stubs declared on `SimulationCore`.
 */
export function SimulationEnemiesMixin<TBase extends SimulationConstructor<SimulationCore>>(
  Base: TBase,
) {
  return class SimulationEnemies extends Base {
    // ================================================================
    // Guard System (天降神兵, §31 Phase 2)
    // ================================================================

    /**
     * Activate 天降神兵 (DECISIONS.md §31 Phase 2): summon a base guard ally and
     * (for balance) accompanying "balance" enemies that are outside the per-stage
     * 20-enemy cap. When no guards are currently active, 1 accompanying enemy is
     * spawned; once 1+ guards are active, each new summon adds 2 (one already
     * alive + the one being summoned counts as "active" only after this check,
     * so the FIRST guard → 1, subsequent → 2).
     */
    protected activateGuard(p: Tank): void {
      const w = this.world
      if (w.guardStock <= 0) return

      let activeGuards = 0
      for (const a of w.allies) {
        if (a.alive && a.spawnTimer <= 0) activeGuards++
      }
      const extraCount = activeGuards === 0 ? 1 : 2

      w.guardStock--
      this.spawnGuard(p)
      for (let i = 0; i < extraCount; i++) this.spawnAccompanyingEnemy(p)
    }

    /** Spawn one allied guard of a random type beside the base. */
    private spawnGuard(p: Tank): void {
      const w = this.world
      const kind = GUARD_KINDS[Math.floor(w.rng.next() * GUARD_KINDS.length)]
      const base = w.tileMap.getBasePos()
      // Spawn on the side of the base OPPOSITE the player (spec): if the player is
      // left of the base, spawn right; otherwise left.
      let side: 'left' | 'right' = 'right'
      if (base) {
        const baseCx = base.x + CELL
        const playerCx = p.x + p.w / 2
        side = playerCx < baseCx ? 'right' : 'left'
      }
      const pos = this.baseSideSpawnCell(side)
      const tank = w.createTank(kind, pos.x, pos.y, 'up')
      // Promotion to third faction (§31 Phase 2).
      tank.allegiance = 'ally'
      tank.isPlayer = false
      tank.spawnTimer = 1000
      if (tank.aiState) {
        // Commander-grade brain so the guard fights competently; pinned to a
        // base-defence posture. It is NEVER considered for enemy command
        // authority (recomputeActiveCommander only scans world.tanks).
        tank.aiState.level = 'commander'
        tank.aiState.isCommander = true
        tank.aiState.strategicGoal = 'defendBase'
        tank.aiState.tacticalGoal = 'defendBase'
        const bx = base ? base.x + CELL : pos.x
        const by = base ? base.y + CELL : pos.y
        tank.aiState.targetX = bx
        tank.aiState.targetY = by
      }
      // 2-minute lifespan (absolute frame).
      tank.guardExpireFrame = w.frame + GUARD_LIFESPAN_FRAMES
      w.allies.push(tank)
    }

    /**
     * Find a clear spawn cell on the requested side of the base (scanning rows
     * around the base for terrain- and tank-free space). Falls back to the base's
     * own column if every candidate is blocked.
     */
    private baseSideSpawnCell(side: 'left' | 'right'): { x: number; y: number } {
      const w = this.world
      const base = w.tileMap.getBasePos()
      const fallback = { x: CELL * 8, y: CELL * 24 }
      if (!base) return fallback
      const baseCol = Math.floor(base.x / CELL)
      const baseRow = Math.floor(base.y / CELL)
      // One cell to the right of the 2×2 base (col baseCol+2) or one to the left
      // (col baseCol-1).
      const col = side === 'right' ? baseCol + 2 : baseCol - 1
      for (let r = baseRow - 2; r <= baseRow + 2; r++) {
        const x = col * CELL
        const y = r * CELL
        if (!w.isInBounds(x, y, TANK, TANK)) continue
        if (w.rectHitsTerrain(x, y, TANK, TANK)) continue
        let blocked = false
        for (const t of w.allTanks) {
          if (t.alive && aabb(x, y, TANK, TANK, t.x, t.y, t.w, t.h)) {
            blocked = true
            break
          }
        }
        if (!blocked) return { x, y }
      }
      return { x: col * CELL, y: baseRow * CELL }
    }

    /**
     * Spawn one accompanying "balance" enemy (outside the per-stage 20-cap). Uses
     * the normal enemy spawn points and AI; flagged isExtra so it never counts
     * toward enemiesRemaining / stage clear, but still scores when killed.
     */
    private spawnAccompanyingEnemy(_p: Tank): void {
      const w = this.world
      const pt = this.findClearEnemySpawnPoint()
      if (!pt) return // all spawn points blocked — skip (never force a jam)
      const kind = EXTRA_ENEMY_KINDS[Math.floor(w.rng.next() * EXTRA_ENEMY_KINDS.length)]
      const tank = w.createTank(kind, pt.x, pt.y, 'down')
      tank.isExtra = true
      tank.bonus = false
      // Note: does NOT increment enemiesSpawned / enemiesRemaining — deliberately
      // outside the per-stage progression (§31 Phase 2).
      w.tanks.push(tank)
    }

    /** Pick the first clear enemy spawn point (rotation starts at spawnPointIndex). */
    private findClearEnemySpawnPoint(): { x: number; y: number } | null {
      const w = this.world
      const spawnPoints = w.enemySpawnPoints
      const n = spawnPoints.length
      for (let i = 0; i < n; i++) {
        const idx = (w.spawnPointIndex + i) % n
        const pt = spawnPoints[idx]
        if (w.rectHitsTerrain(pt.x, pt.y, TANK, TANK)) continue
        let can = true
        for (const t of w.allTanks) {
          if (t.alive && aabb(pt.x, pt.y, TANK, TANK, t.x, t.y, t.w, t.h)) {
            can = false
            break
          }
        }
        if (can) return pt
      }
      return null
    }

    /**
     * Allied guard AI (天降神兵, §31 Phase 2). A focused "Commander-defend"
     * policy (deterministic via world.rng): seek the nearest enemy, defend the
     * base when none, and fire only when aligned with a target and the line of
     * sight is clear of terrain. Reuses the standard movement/fire primitives so
     * the guard obeys the same collision & friendly-fire rules as everyone else.
     *
     * (Design note: the spec says "use the Commander AI". The enemy tactical
     * pipeline is goaled at ATTACKING the base/player, so running it verbatim on
     * an ally would steer it into the player's base. This dedicated defender
     * policy honours the observable intent — competent, base-defending fire —
     * without that hazard. It can be promoted to the full pipeline later if a
     * 'defendBase'-only goal branch is added.)
     */
    protected updateGuards(): void {
      const w = this.world
      const allies = w.allies
      const enemyTanks = w.tanks
      for (let ai = 0; ai < allies.length; ai++) {
        const g = allies[ai]
        if (!g.alive) continue
        if (g.spawnTimer > 0) continue // still spawning — no intent yet

        // Lifespan expiry → retire the guard (no score, no drops).
        if (g.guardExpireFrame !== undefined && w.frame >= g.guardExpireFrame) {
          g.alive = false
          w._needsCleanup = true
          this.createExplosion(g.x + g.w / 2, g.y + g.h / 2, 'big')
          continue
        }

        const gx = g.x + g.w / 2
        const gy = g.y + g.h / 2

        // Nearest hostile tank.
        let target: Tank | null = null
        let bestD = Infinity
        for (let ei = 0; ei < enemyTanks.length; ei++) {
          const e = enemyTanks[ei]
          if (!e.alive || e.spawnTimer > 0 || e.allegiance !== 'enemy') continue
          const d = Math.hypot(e.x + e.w / 2 - gx, e.y + e.h / 2 - gy)
          if (d < bestD) {
            bestD = d
            target = e
          }
        }

        let tx = gx
        let ty = gy
        if (target) {
          tx = target.x + target.w / 2
          ty = target.y + target.h / 2
        } else {
          const base = w.tileMap.getBasePos()
          if (base) {
            tx = base.x + CELL
            ty = base.y + CELL
          }
        }

        // Primary-axis direction toward the target (defend-by-intercept).
        const dx = tx - gx
        const dy = ty - gy
        const dir: Direction =
          Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up'
        g.dir = dir
        g.moving = true

        // Fire when aligned with the target and the LOS is clear of terrain.
        if (target) {
          const ex = target.x + target.w / 2
          const ey = target.y + target.h / 2
          let fireDir: Direction | null = null
          if (Math.abs(ex - gx) < CELL * 0.6 && Math.abs(ey - gy) > CELL * 0.6) {
            fireDir = ey < gy ? 'up' : 'down'
          } else if (Math.abs(ey - gy) < CELL * 0.6 && Math.abs(ex - gx) > CELL * 0.6) {
            fireDir = ex < gx ? 'left' : 'right'
          }
          if (fireDir && this.lineClearForAlly(g, fireDir, target)) {
            g.dir = fireDir
            this.tryFire(g)
          }
        }
      }
    }

    /** True if no brick/steel/base tile lies between the guard and its target. */
    private lineClearForAlly(g: Tank, dir: Direction, target: Tank): boolean {
      const w = this.world
      const v = DIR_VECTORS[dir]
      const sx = g.x + g.w / 2
      const sy = g.y + g.h / 2
      const tx = target.x + target.w / 2
      const ty = target.y + target.h / 2
      const maxDist = Math.hypot(tx - sx, ty - sy)
      for (let d = CELL; d <= maxDist; d += CELL) {
        const cx = sx + v.dx * d
        const cy = sy + v.dy * d
        const col = Math.floor(cx / CELL)
        const row = Math.floor(cy / CELL)
        const tt = w.tileMap.get(col, row)
        if (tt === 'brick' || tt === 'steel' || tt === 'base') return false
      }
      return true
    }

    /**
     * 同归于尽 (DECISIONS.md §31): when the player loses a life, release ALL
     * accumulated sacrifice items at once. Blast radius = 5 + (stock−1) cells,
     * destroying every enemy and every brick wall within it. Enemies killed by
     * the blast use the normal kill accounting (score / killCount /
     * enemiesRemaining), so they count exactly like a regular kill.
     */
    protected triggerSacrificeAoE(player: Tank): void {
      const w = this.world
      if (w.sacrificeStock <= 0) return

      const radiusCells = SACRIFICE_BASE_RADIUS_CELLS + (w.sacrificeStock - 1)
      const radiusPx = radiusCells * CELL
      const cx = player.x + player.w / 2
      const cy = player.y + player.h / 2

      // Destroy enemies within radius (normal kill accounting). Allies are
      // friendly — the blast only consumes hostile tanks (§31 Phase 2).
      for (const t of w.tanks) {
        if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
        const tx = t.x + t.w / 2
        const ty = t.y + t.h / 2
        if (Math.hypot(tx - cx, ty - cy) <= radiusPx) {
          t.alive = false
          w._needsCleanup = true
          this.createExplosion(t.x + t.w / 2, t.y + t.h / 2, 'big')
          const gained = killScore(w.difficultyKey, t.aiState?.level, w.stageIndex, w.rules, t.kind)
          w.score += gained
          w.enemiesRemaining--
          w.killCount++
          w.addPopup({ id: genId(), x: t.x, y: t.y, text: String(gained), timer: 1500 })
          w.pushEvent({ type: 'tank_destroyed', tank: t, by: 'player' })
        }
      }

      // Destroy brick walls within radius (16×16 cells).
      const c0 = Math.max(0, Math.floor((cx - radiusPx) / CELL))
      const c1 = Math.min(GRID - 1, Math.floor((cx + radiusPx) / CELL))
      const r0 = Math.max(0, Math.floor((cy - radiusPx) / CELL))
      const r1 = Math.min(GRID - 1, Math.floor((cy + radiusPx) / CELL))
      for (let r = r0; r <= r1; r++) {
        for (let c = c0; c <= c1; c++) {
          if (w.tileMap.get(c, r) === 'brick') {
            w.tileMap.destroy(c, r)
          }
        }
      }

      this.createExplosion(cx, cy, 'big')
      w.sacrificeStock = 0
    }

    // ================================================================
    // Enemy AI System
    // ================================================================

    protected updateEnemyAI(): void {
      // Recompute command authority ONCE per tick, before the AI layer runs
      // (plan §4). The One-Author invariant: Simulation owns World writes;
      // the AI layer only reads `world.activeCommanderId`.
      this.recomputeActiveCommander()
      // Delegate all enemy decision-making to the Tactical Intelligence
      // Framework. It reads the World (Perception) and writes tank intent
      // (direction / firing) back — never hidden state, never Math.random().
      this.ai.update(this.world, (tank) => this.tryFire(tank))
    }

    /**
     * Derive command authority for this tick (plan §4 [D2][D3]).
     * Active Commander = the alive commander-tier tank with the highest
     * `spawnSeq` (most-recently born); null when none is alive. On a
     * change, the new active tank's `commanderTimer` is overwritten to
     * 1000 ms — its 1s office delay measured from taking office (not
     * from spawn). Succession is automatic: when the active dies, the
     * previously-born survivor is now the argmax and regains command.
     */
    private recomputeActiveCommander(): void {
      const w = this.world
      let bestId: number | null = null
      let bestSeq = -Infinity
      const tanks = w.tanks
      for (let i = 0; i < tanks.length; i++) {
        const t = tanks[i]
        if (!t.alive || t.spawnTimer > 0 || !t.aiState) continue
        if (t.aiState.level === 'commander') {
          if (t.aiState.spawnSeq > bestSeq) {
            bestSeq = t.aiState.spawnSeq
            bestId = t.id
          }
        }
      }
      const prev = w.activeCommanderId
      w.activeCommanderId = bestId
      if (bestId !== null && bestId !== prev) {
        // Linear scan — only on commander change (rare). Avoids allocating a
        // `.find()` closure every tick (AGENTS §14.1).
        for (let i = 0; i < tanks.length; i++) {
          if (tanks[i].id === bestId) {
            const active = tanks[i]
            if (active.aiState) active.aiState.commanderTimer = 1000
            break
          }
        }
      }
    }
  }
}
