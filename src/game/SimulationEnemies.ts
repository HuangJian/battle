import { CELL, TANK, GRID, DIR_VECTORS, Direction, SEED_HASH, POPUP_DURATION_MS } from '../constants'
import { SACRIFICE_BASE_RADIUS_CELLS } from '../config/powerups'
import { killScore } from '../config/score'
import { genId } from './World'
import { aabb } from '../utils/helpers'
import { RNG } from '../utils/RNG'
import { GodAIInput } from '../ai/GodAIInput'
import { GUARD_GOD_AI_PARAMS } from '../ai/god/params'
import { scanAhead } from '../ai/perception'
import type { Tank, TankKind } from '../types'
import type { SimulationConstructor, SimulationCore } from './SimulationCore'

/** 天降神兵 guard lifespan: 2 minutes at 60 ticks/s (§31 Phase 2). */
const GUARD_LIFESPAN_FRAMES = 120 * 60
/** Guard kinds are randomly chosen; all use normal enemy combat stats. */
const GUARD_KINDS: TankKind[] = ['basic', 'fast', 'power', 'armor']
/** Accompanying "balance" enemies use a lighter pool. */
const EXTRA_ENEMY_KINDS: TankKind[] = ['basic', 'fast', 'power']
/**
 * Decoy engagement range (new-powerups-plan §4.4, user req): an enemy within
 * this many cells of a live decoy turns to face it and fires whenever the shot
 * is clear. The decoy is a stationary lure, so this is the whole point — pull
 * enemy fire off the real players.
 */
const DECOY_ENGAGE_CELLS = 4

/**
 * §159 避让: perpendicular candidate pairs for the yield step, indexed by
 * whether the player's lane is vertical (0) or horizontal (1). Hoisted to
 * module scope — updateGuardYield runs per tick per yielding guard and must
 * not allocate (AGENTS §14.1).
 */
const YIELD_PERPS: Direction[][] = [
  ['left', 'right'], // player moving up/down → step sideways
  ['up', 'down'], // player moving left/right → step vertically
]

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
     * Allied guard AI (天降神兵, §31 Phase 2). Each guard is driven by a full
     * God AI brain (GodAIInput) — the same decision pipeline as the God AI
     * player — so it dodges enemy bullets, intercepts base-bound fire (T8),
     * holds a defense position (§137) and stop-and-aim engages (T2a) like an
     * optimal player, while its ally-faction bullets only ever strike enemies
     * (3-way friendly fire, §31).
     *
     * On top of the God AI sits the §159 避让 (yield) override: while the guard
     * occupies the cell directly in front of a MOVING player, it must get out
     * of the way before anything else —
     *   1. prefer stepping perpendicular (优先垂直让开);
     *   2. if no perpendicular cell is open, unconditionally turn to the
     *      player's direction and advance (无条件转为与 player 同方向并前进 —
     *      the corridor-escort case);
     *   3. keep yielding until it no longer blocks the lane, then resume
     *      autonomous play (一直避让到不堵车才能自主行动);
     *   4. while yielding, keep firing to suppress enemies — a REAL enemy on
     *      the SWEEP axis (the perpendicular step the guard is moving along)
     *      first, else the player's forward lane (§160 避让中扫射压制 enemy-
     *      first / §159 避让过程保持向前方开火压制), both gated by the brain's
     *      scanAhead + shouldFireInDir so the guard never shoots the base
     *      ring or unpierceable steel (T6/T11/§121).
     *
     * Determinism: each brain owns a private RNG and the guard profile zeros
     * the imperfection gates (aimError / suboptimalPathProb), so every
     * rng.next() result is constant — the guard's decisions are pure functions
     * of World state, byte-identical across the original run and replay
     * playback (see GUARD_GOD_AI_PARAMS). Brains are keyed by guard id and
     * resolve the CURRENT tank object by id, so restoreWorld (which replaces
     * guard objects but preserves ids) keeps the same brain alive. Note: a
     * mid-run REWIND restores the World but not the brain's history-dependent
     * counters (_campTicks etc.) — the same accepted semantics as the player
     * GodAIInput (also never reset on restore); the zeroed gates guarantee a
     * rewind can never introduce RNG-seed divergence.
     */
    protected updateGuards(): void {
      const w = this.world
      const allies = w.allies

      // No guards at all — drop any stale brains (post-rewind / stage reset).
      if (allies.length === 0) {
        if (this.guardAIById.size > 0) this.guardAIById.clear()
        return
      }

      // Prune brains whose guard is gone (expired / destroyed).
      if (this.guardAIById.size > 0) {
        const ids = this.guardAIById.keys()
        let it = ids.next()
        while (!it.done) {
          const id = it.value
          let found = false
          for (let ai2 = 0; ai2 < allies.length; ai2++) {
            const a = allies[ai2]
            if (a.id === id && a.alive) {
              found = true
              break
            }
          }
          if (!found) this.guardAIById.delete(id)
          it = ids.next()
        }
      }

      // (perf §14.6) Resolve the tank list ONCE per tick and pass it down —
      // allTanks is a getter that rebuilds its buffer on every access.
      const allTanks = w.allTanks

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

        // Decoys (诱饵) are stationary lures: they never move and never fire,
        // and they must not block anyone's path (不卡位置). Skip the God AI
        // drive entirely — just keep them drawn/aimed-up and let the lifespan
        // timer retire them. Enemy fire is drawn to them via updateDecoyEngagement.
        if (g.isDecoy) {
          g.moving = false
          g.dir = 'up'
          continue
        }

        // Get-or-create this guard's God AI brain.
        const brain = this.guardAIFor(g)

        // §159 避让: while blocking a moving player's forward cell, yield.
        const fwd = this.guardYieldForward(g)
        if (fwd !== null) {
          this.updateGuardYield(g, brain, fwd, allTanks)
          brain.endFrame()
          continue
        }

        // Autonomous God AI drive (same contract as updatePlayerTank).
        const dir = brain.getMoveDirection()
        if (dir !== null) {
          g.dir = dir
          g.moving = true
        } else {
          g.moving = false
        }
        if (brain.isFiring()) this.tryFire(g)
        brain.endFrame()
      }
    }

    /**
     * §159: per-guard God AI brains, keyed by guard tank id. Pruned in
     * updateGuards when the guard dies/expires (a stale brain is harmless —
     * its controlledTank resolves null and thinkImpl idles — but pruning keeps
     * memory bounded).
     */
    private guardAIById = new Map<number, GodAIInput>()

    /** Get (or create) the God AI brain for one guard. */
    private guardAIFor(g: Tank): GodAIInput {
      let brain = this.guardAIById.get(g.id)
      if (brain) return brain
      const id = g.id
      // The seed is inert while GUARD_GOD_AI_PARAMS keeps aimError and
      // suboptimalPathProb at 0 (every rng.next() result is constant) — it
      // only matters if someone re-enables imperfection later. Drawn from
      // world.frame so different guards get different (harmless) sequences.
      const rng = new RNG(((this.world.frame * SEED_HASH) >>> 0) ^ (id * SEED_HASH))
      brain = new GodAIInput(this.world, GUARD_GOD_AI_PARAMS, rng, (w) => {
        // Resolve the CURRENT tank by id so snapshot restore (which replaces
        // the guard object but preserves its id) keeps the same brain alive.
        const all = w.allies
        for (let i = 0; i < all.length; i++) {
          if (all[i].id === id) return all[i]
        }
        return null
      })
      brain.isGuardAI = true // §187: guard A* treats player as obstacle
      brain.reset()
      // Re-zero the imperfection gates AFTER stage adaptation — §58 would
      // otherwise re-enable suboptimalPathProb (0.05) on brick-dense stages,
      // reintroducing RNG-seed dependence (see GUARD_GOD_AI_PARAMS).
      brain.params.aimError = 0
      brain.params.suboptimalPathProb = 0
      this.guardAIById.set(id, brain)
      return brain
    }

    /**
     * §159 避让 trigger: the direction the guard must yield toward — the
     * facing direction of a player whose forward cell the guard blocks — or
     * null when no player is jammed (full autonomy). Only a MOVING player
     * counts: a parked player creates no traffic jam, and requiring movement
     * stops the guard from dancing around a stationary player (e.g. a God AI
     * tank camping behind it in T2a).
     */
    private guardYieldForward(g: Tank): Direction | null {
      const w = this.world
      const p1 = w.player
      if (p1 && p1.alive && p1.spawnTimer <= 0 && p1.moving && this.tankInForwardCell(g, p1)) {
        return p1.dir
      }
      const p2 = w.player2
      if (p2 && p2.alive && p2.spawnTimer <= 0 && p2.moving && this.tankInForwardCell(g, p2)) {
        return p2.dir
      }
      return null
    }

    /** True when the guard's body overlaps the 16px cell directly ahead of `p`. */
    private tankInForwardCell(g: Tank, p: Tank): boolean {
      const upDown = p.dir === 'up' || p.dir === 'down'
      const fx = p.dir === 'left' ? p.x - CELL : p.dir === 'right' ? p.x + TANK : p.x
      const fy = p.dir === 'up' ? p.y - CELL : p.dir === 'down' ? p.y + TANK : p.y
      return aabb(g.x, g.y, g.w, g.h, fx, fy, upDown ? TANK : CELL, upDown ? CELL : TANK)
    }

    /**
     * §159 避让: unconditional yield. Step perpendicular first — the side with
     * more free lane wins the tie (so the guard clears the lane instead of
     * pocketing against a wall); when neither perpendicular is open, turn to
     * the player's direction and advance (escort).
     *
     * §160 避让中扫射压制 — fire control, enemy-first: the SWEEP axis (the
     * perpendicular step the guard is actually moving along) fires only when a
     * REAL enemy is on it — the barrel then matches the movement direction (no
     * more shooting up the lane at nothing while the body slides), and shots
     * from successive slide positions sweep a band across the corridor the
     * guard is crossing. A mere WALL on the flank must never outrank a live
     * enemy in the player's lane (the fire direction would 偏离目标), so when
     * the sweep axis has no enemy, the player's forward lane keeps the
     * original §159 gate (enemy OR wall: 避让过程保持向前方开火压制).
     * scanAhead(moveDir) is memoized per tick; the paired shouldFireInDir
     * reuses that scan and layers on the T6/T11/§121 safety gates (baseWall /
     * unpierceable steel). The engine's cooldown model caps fire rate — at
     * most one shot per tick. Note: the sim's turn cooldown may defer the
     * first perpendicular turn (~160 ms) — the guard then stands and
     * sweeps-fires in place until the turn is accepted.
     */
    private updateGuardYield(g: Tank, brain: GodAIInput, fwd: Direction, allTanks: Tank[]): void {
      const vertical = fwd === 'up' || fwd === 'down'
      const perps = YIELD_PERPS[vertical ? 0 : 1]
      let moveDir: Direction | null = null
      let bestFree = -1
      for (let pi = 0; pi < 2; pi++) {
        const d = perps[pi]
        if (!this.guardCanStep(g, d, allTanks)) continue
        const free = this.guardLaneFreeCells(g, d, 4, allTanks)
        if (free > bestFree) {
          bestFree = free
          moveDir = d
        }
      }
      if (moveDir === null) moveDir = fwd // 无条件转为与 player 同方向并前进

      // §160 避让中扫射压制 (enemy-first): the sweep axis wins ONLY when it
      // has a real enemy (scanAhead memoized; shouldFireInDir adds the safety
      // gates on top of the enemy flag). Otherwise the player's forward lane
      // keeps the original §159 gate. tryFire spawns the bullet along tank.dir,
      // so the barrel is set to the firing axis before the call.
      const gcx = g.x + g.w / 2
      const gcy = g.y + g.h / 2
      if (
        moveDir !== fwd &&
        brain.scanAhead(gcx, gcy, moveDir).enemy &&
        brain.shouldFireInDir(gcx, gcy, moveDir)
      ) {
        g.dir = moveDir
        this.tryFire(g)
      } else {
        g.dir = fwd
        if (brain.shouldFireInDir(gcx, gcy, fwd)) this.tryFire(g)
        g.dir = moveDir
      }
      g.moving = true
    }

    /** Can the guard move one cell in `dir` (bounds + terrain + any tank)? */
    private guardCanStep(g: Tank, dir: Direction, allTanks: Tank[]): boolean {
      const w = this.world
      const v = DIR_VECTORS[dir]
      const nx = g.x + v.dx * CELL
      const ny = g.y + v.dy * CELL
      if (!w.isInBounds(nx, ny, TANK, TANK)) return false
      if (w.rectHitsTerrain(nx, ny, TANK, TANK)) return false
      for (let ti = 0; ti < allTanks.length; ti++) {
        const t = allTanks[ti]
        if (t === g || !t.alive) continue
        if (aabb(nx, ny, TANK, TANK, t.x, t.y, t.w, t.h)) return false
      }
      return true
    }

    /** Number of consecutive free cells ahead in `dir` (perpendicular tie-break). */
    private guardLaneFreeCells(g: Tank, dir: Direction, max: number, allTanks: Tank[]): number {
      const w = this.world
      const v = DIR_VECTORS[dir]
      let free = 0
      let x = g.x
      let y = g.y
      for (let i = 1; i <= max; i++) {
        x += v.dx * CELL
        y += v.dy * CELL
        if (!w.isInBounds(x, y, TANK, TANK)) break
        if (w.rectHitsTerrain(x, y, TANK, TANK)) break
        let blocked = false
        for (let ti = 0; ti < allTanks.length; ti++) {
          const t = allTanks[ti]
          if (t === g || !t.alive) continue
          if (aabb(x, y, TANK, TANK, t.x, t.y, t.w, t.h)) {
            blocked = true
            break
          }
        }
        if (blocked) break
        free++
      }
      return free
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
          w.addPopup({ id: genId(), x: t.x, y: t.y, text: String(gained), timer: POPUP_DURATION_MS })
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
      // Decoy engagement override (new-powerups-plan §4.4, user req): when an
      // enemy is within DECOY_ENGAGE_CELLS of a live decoy, it PRIORITIZES the
      // decoy — turns to face it and fires whenever the shot is clear. Runs
      // after the tactical AI so it can override the aim for close decoys.
      this.updateDecoyEngagement()
    }

    /**
     * Decoy engagement (new-powerups-plan §4.4, user req): when an enemy comes
     * within {@link DECOY_ENGAGE_CELLS} of a live decoy, it turns to face the
     * decoy and fires the instant the shot is clear. Deterministic (no RNG);
     * `tryFire`'s cooldown prevents a double shot with the tactical AI's own
     * fire this tick. No-op when no decoy is present, so single-player (and any
     * run without a decoy) is byte-identical to before.
     */
    private updateDecoyEngagement(): void {
      const w = this.world
      const allies = w.allies
      // Quick reject when no live, vulnerable decoy exists.
      let hasDecoy = false
      for (let a = 0; a < allies.length; a++) {
        const d = allies[a]
        if (d.alive && d.isDecoy && d.spawnTimer <= 0) {
          hasDecoy = true
          break
        }
      }
      if (!hasDecoy) return

      const range = DECOY_ENGAGE_CELLS * CELL
      const tanks = w.tanks
      for (let i = 0; i < tanks.length; i++) {
        const t = tanks[i]
        if (!t.alive || t.spawnTimer > 0 || !t.aiState) continue
        if (t.allegiance !== 'enemy') continue

        // Nearest live decoy within range.
        const tcx = t.x + t.w / 2
        const tcy = t.y + t.h / 2
        let bd = Infinity
        let best: Tank | null = null
        for (let a = 0; a < allies.length; a++) {
          const d = allies[a]
          if (!d.alive || !d.isDecoy || d.spawnTimer > 0) continue
          const dd = Math.hypot(d.x + d.w / 2 - tcx, d.y + d.h / 2 - tcy)
          if (dd <= range && dd < bd) {
            bd = dd
            best = d
          }
        }
        if (!best) continue

        const dx = best.x + best.w / 2 - tcx
        const dy = best.y + best.h / 2 - tcy
        const horiz: Direction = dx >= 0 ? 'right' : 'left'
        const vert: Direction = dy >= 0 ? 'down' : 'up'
        // Prefer the dominant axis; fall back to the cross axis when the decoy
        // is nearly aligned on it (so a slightly-offset lure is still engaged).
        let chosen: Direction | null = null
        if (Math.abs(dx) >= Math.abs(dy)) {
          if (scanAhead(w, t, horiz, CELL * 6) === 'decoy') chosen = horiz
          else if (Math.abs(dy) <= CELL * 0.75 && scanAhead(w, t, vert, CELL * 6) === 'decoy')
            chosen = vert
        } else {
          if (scanAhead(w, t, vert, CELL * 6) === 'decoy') chosen = vert
          else if (Math.abs(dx) <= CELL * 0.75 && scanAhead(w, t, horiz, CELL * 6) === 'decoy')
            chosen = horiz
        }
        if (chosen) {
          t.dir = chosen
          this.tryFire(t) // cooldown-gated; never double-fires
        } else {
          // Not yet aligned for a clear shot — still turn toward the decoy so
          // the next ticks line up the shot (and the enemy is drawn in).
          t.dir = Math.abs(dx) >= Math.abs(dy) ? horiz : vert
        }
      }
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
