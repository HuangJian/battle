import type { World } from '../game/World'
import type { Tank } from '../types'
import type { Direction } from '../constants'
import { CELL, TANK, DIR_VECTORS, FIELD, GRID } from '../constants'
import { aabb, snap, bulletLaneDist } from '../utils/helpers'
import type { Perception, Situation, IntelligenceConfig } from './types'

import { manhattan } from '../utils/helpers'
import { BULLET_ALIGN_NEXT_CELL } from './god/constants'

/**
 * ai/perception.ts — the "eyes" of the framework.
 *
 * Perception converts the World into an observation snapshot the AI is allowed
 * to reason about. The AI never reads gameplay objects directly (AI
 * Constitution #1: "Perceive before deciding"). Situation Analysis then turns
 * observations into tactical knowledge — analysis only, no decisions.
 */

/** Manhattan distance between two tank centers (px). */
// Canonical manhattan moved to utils/helpers (遗留 #2 unification) —
// re-exported to keep TacticalIntelligence's import path stable.
export { manhattan } from '../utils/helpers'

/** Primary-axis direction from a tank toward a target point (used for routing). */
export function dirToward(x: number, y: number, tx: number, ty: number): Direction {
  const dx = tx - x
  const dy = ty - y
  if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? 'right' : 'left'
  return dy > 0 ? 'down' : 'up'
}

/**
 * Can the tank take one full step in `dir` from its current position?
 * `considerTanks` includes other tanks as obstacles (used for routing to avoid
 * gridlock); the movement system still resolves final collisions regardless.
 *
 * The check covers the *swept union* of the tank's current and destination
 * footprints, not just the destination. Checking only the destination was the
 * root cause of "enemies stuck on each other / against walls": a tank already
 * touching a wall (or another tank) on the side it wants to move along would
 * see a clear destination (the destination sits *past* the obstacle) while the
 * very first fine-step already overlaps it — so the AI kept choosing that
 * "open" lane, rammed the obstacle every tick, and froze. The swept union
 * catches the obstacle the tank is adjacent to, so such a direction is
 * correctly reported as blocked.
 */
export function canStep(
  world: World,
  tank: Tank,
  dir: Direction,
  considerTanks: boolean,
  others?: Tank[],
): boolean {
  const v = DIR_VECTORS[dir]
  // Reason about the tank on its conceptual grid. The cross-axis is kept
  // aligned by `alignTank` every tick, but the movement axis can accumulate
  // sub-cell drift (speed is fractional). Using the raw (drifted) position in
  // the swept union makes two tanks stacked in a column wrongly report each
  // other as blocking their perpendicular move — a primary cause of "enemies
  // stuck on each other". Snapping to the grid removes that phantom overlap;
  // since a tank can never legally overlap a real obstacle, the grid-aligned
  // union agrees with the actual movement collision.
  const gx = snap(tank.x, CELL)
  const gy = snap(tank.y, CELL)
  const nx = gx + v.dx * TANK
  const ny = gy + v.dy * TANK
  // Swept rectangle: union of the current footprint [gx,gx+TANK] and the
  // destination footprint [nx,nx+TANK]. Width/height = step distance + size.
  const sx = Math.min(gx, nx)
  const sy = Math.min(gy, ny)
  const sw = Math.abs(nx - gx) + TANK
  const sh = Math.abs(ny - gy) + TANK
  if (!world.isInBounds(sx, sy, sw, sh)) return false
  if (world.rectHitsTerrain(sx, sy, sw, sh)) return false
  if (considerTanks) {
    // `others` (when provided by perceive) is the precomputed set of all
    // other live tanks — avoids re-scanning world.allTanks for every one of
    // the 4 direction checks. Falls back to world.allTanks otherwise.
    const list = others ?? world.allTanks
    for (let i = 0; i < list.length; i++) {
      const o = list[i]
      if (o === tank || !o.alive || o.spawnTimer > 0) continue
      // Decoys (诱饵) never block movement (不卡位置) — enemies path through
      // them freely; only their bullets may strike (bulletHitsTank).
      if (o.isDecoy) continue
      if (aabb(sx, sy, sw, sh, o.x, o.y, o.w, o.h)) return false
    }
  }
  return true
}

/** Result of scanning ahead along a direction. */
export type ScanHit = 'none' | 'base' | 'player' | 'decoy' | 'wall' | 'steel'

/**
 * Step along `dir` (in CELL increments) up to `maxDist` px and report the
 * first thing an enemy bullet would strike: base, player, brick (wall),
 * steel, or nothing. Forest/water/ice do NOT block bullets (TileMap), so they
 * are skipped. Other enemy tanks never block enemy bullets, so they are
 * ignored — only the player tank and terrain matter for an enemy's line of
 * fire.
 *
 * Returns the hit type as a string (no object allocation) — the caller
 * (`analyze`) only checks the hit category, never the distance.
 */
export function scanAhead(world: World, tank: Tank, dir: Direction, maxDist: number): ScanHit {
  const v = DIR_VECTORS[dir]
  const sx = tank.x + tank.w / 2
  const sy = tank.y + tank.h / 2
  // Lie-Back-Win-Mode §3.8 P3: detect both players in line of fire.
  const player = world.player
  // 督战双玩家 (spectateDual) is a second, machine-controlled player — it must
  // be perceived exactly like the Lie-Back-Win coop partner, or the P2 God AI
  // is blind to its own tank (scanAhead) and mis-targets (perceive picks P1).
  const player2 = world.coop || world.spectateDual ? world.player2 : null
  // §233 (perf): the scan walks whole cells along an axis-aligned dir, so the
  // scanned cell increments by exactly ±1 per step. Integer cell stepping
  // replaces the per-step multiply + division + floor; pixel coordinates are
  // derived from the cell + precomputed center offset ONLY when a tank/decoy
  // AABB check needs them. Arithmetic identity: floor((sx + dx·k·CELL)/CELL)
  // = floor(sx/CELL) + dx·k for integer k, and cx = col·CELL + (sx mod CELL)
  // — byte-identical cells and pixels.
  const allies = world.allies
  // 界内直读 grid：`TileMap.get` 只有「越界 → steel / 界内 → grid[row][col]」两条路，
  // 而越界已在循环里先行 return，所以两者逐值等价（省一次方法派发 + 一次重复界内判断）。
  const grid = world.tileMap.grid
  const offX = sx - Math.floor(sx / CELL) * CELL
  const offY = sy - Math.floor(sy / CELL) * CELL
  let col = Math.floor(sx / CELL)
  let row = Math.floor(sy / CELL)
  const steps = Math.floor(maxDist / CELL)
  for (let i = 1; i <= steps; i++) {
    col += v.dx
    row += v.dy
    // Out-of-bounds is treated as steel (impassable) — matches TileMap.get's
    // behavior for the original string grid.
    if (col < 0 || col >= GRID || row < 0 || row >= GRID) return 'steel'
    const tt = grid[row][col]
    if (tt === 'base') return 'base'
    if (tt === 'brick') return 'wall'
    if (tt === 'steel') return 'steel'
    // 本格像素中心只算一次（旧实现在 player / player2 / decoy 三处各重算一遍）。
    const cx = col * CELL + offX
    const cy = row * CELL + offY
    if (player && player.alive) {
      if (aabb(cx - 1, cy - 1, 2, 2, player.x, player.y, player.w, player.h)) {
        return 'player'
      }
    }
    if (player2 && player2.alive) {
      if (aabb(cx - 1, cy - 1, 2, 2, player2.x, player2.y, player2.w, player2.h)) {
        return 'player'
      }
    }
    // Decoy (诱饵): a fake tank that draws enemy fire. If an enemy's line of
    // fire crosses a decoy, the decoy is a valid (and desirable) target so the
    // enemy shoots it instead of pushing toward the base/player (new-powerups
    // §4.4). Bullets pass through allies, but the decoy is the exception we
    // want enemies to aim at.
    for (let ai = 0; ai < allies.length; ai++) {
      const dec = allies[ai]
      if (!dec.alive || !dec.isDecoy || dec.spawnTimer > 0) continue
      if (aabb(cx - 1, cy - 1, 2, 2, dec.x, dec.y, dec.w, dec.h)) {
        return 'decoy'
      }
    }
  }
  return 'none'
}

// Hoisted constant — avoids allocating a 4-element array on every perceive call.
const PERCEIVE_DIRS: readonly Direction[] = ['up', 'down', 'left', 'right']

/**
 * 「这拍没算 openDirs」的共享空数组。只读：`reactiveDodge` 需要时是**整体替换字段**
 * （`p.openDirs = computeOpenDirs(...)`），从不 push。
 */
const EMPTY_DIRS: Direction[] = []

/** Compute open directions (4 × canStep). Exported for lazy on-demand use
 * by reactiveDodge when perceive skipped this computation (perf: the scan
 * is ~100 ops but only needed by throttled tacticalThink or rare threat-dodge). */
export function computeOpenDirs(world: World, tank: Tank, all: Tank[]): Direction[] {
  const openDirs: Direction[] = []
  for (let i = 0; i < PERCEIVE_DIRS.length; i++) {
    if (canStep(world, tank, PERCEIVE_DIRS[i], true, all)) openDirs.push(PERCEIVE_DIRS[i])
  }
  return openDirs
}

/** Build the observation snapshot for one tank.
 * When `computeOpenDirs` is false, the expensive 4 × canStep scan is skipped
 * (openDirs = []). The caller (reactiveDodge) must call computeOpenDirs()
 * on demand if it needs openDirs on a tick where perceive skipped it.
 *
 * `all` (when provided) is the precomputed `world.allTanks` buffer — passing
 * it avoids re-fetching the getter (AGENTS.md §14.6). Tests may omit it; the
 * function falls back to `world.allTanks` (slower path, but identical result).
 *
 * The returned Perception is allocation-free in the hot path: it carries flat
 * threat + teammate aggregates instead of `BulletObservation[]` /
 * `TeammateObservation[]` (those types were removed; see types.ts). */
export function perceive(
  world: World,
  tank: Tank,
  cfg: IntelligenceConfig,
  needOpenDirs = true,
  all?: Tank[],
  out?: Perception,
  needAggregates = true,
): Perception {
  const sx = tank.x + tank.w / 2
  const sy = tank.y + tank.h / 2
  // Lie-Back-Win-Mode §3.8 P3: pick the closest player as the perception target.
  let player = world.player
  // 督战双玩家 (spectateDual) is a second, machine-controlled player — it must
  // be perceived like the coop partner, or the P2 God AI anchors its awareness
  // to P1 instead of its own tank (grossly mis-targets, e.g. defends P1's side).
  if ((world.coop || world.spectateDual) && world.player2) {
    const p1Dist = player
      ? manhattan(player.x + player.w / 2, player.y + player.h / 2, sx, sy)
      : Infinity
    const p2Dist = manhattan(
      world.player2.x + world.player2.w / 2,
      world.player2.y + world.player2.h / 2,
      sx,
      sy,
    )
    if (p2Dist < p1Dist) player = world.player2
  }
  const base = world.tileMap.getBasePos()

  // Track the single closest threat via flat fields — consumers only read the
  // threat's direction (analyze → s.threatDir, used by reactiveDodge). The
  // prior design allocated a `BulletObservation[]` + per-element objects here
  // every call (~1.1M allocs per 30-game batch); the new design allocates
  // nothing.
  const range = cfg.predictionDepth * CELL
  let hasThreat = false
  let threatDist = Infinity
  let threatDir: Direction = 'up'
  const bullets = world.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    // Only hostile-to-enemy bullets are a threat: player OR ally fire (ally
    // bullets carry allegiance 'ally', never 'enemy').
    if (!b.alive || b.allegiance === 'enemy') continue
    // §3.1 single-sourced lane geometry (allegiance filtered above).
    const dist = bulletLaneDist(b.dir, b.x + b.w / 2, b.y + b.h / 2, sx, sy, BULLET_ALIGN_NEXT_CELL)
    if (dist < 0 || dist > range) continue
    // Track the closest threat. Sorting was only needed to pick threats[0];
    // a running min replaces the sort + array entirely.
    if (dist < threatDist) {
      threatDist = dist
      threatDir = b.dir
      hasThreat = true
    }
  }

  // Reuse the allTanks buffer directly instead of building a filtered `others`
  // array. canStep already skips dead/spawning/self entries with the same
  // filter (`o === tank || !o.alive || o.spawnTimer > 0`), so passing `all`
  // yields identical results. This eliminates one array allocation + N pushes
  // per perceive call (~550K allocs over a 30-game batch).
  const list = all ?? world.allTanks

  // Teammate aggregates: centroid (sumX/sumY/count) replaces the
  // TeammateObservation[] array. `targetForGoal`'s spreadOut directive is the
  // only consumer, and it only computes the centroid — never per-element data.
  let teammateCount = 0
  let teammateSumX = 0
  let teammateSumY = 0
  let congestion = 0
  // Nearest live decoy (for decoy-targeting). Decoys are allies, so they are
  // excluded from the teammate aggregates (they are not fellow enemies).
  // Tracked as flat fields — no object allocation.
  let hasDecoy = false
  let decoyX = 0
  let decoyY = 0
  let nearestDecoyDist = Infinity
  // 队友/诱饵/拥挤聚合的消费者全在节拍上（evaluateGoals / targetForGoal /
  // strategicThink）——每拍消费者只用 hasThreat/threatDir。非节拍拍子整趟跳过。
  for (let i = 0; needAggregates && i < list.length; i++) {
    const o = list[i]
    if (o === tank || !o.alive || o.spawnTimer > 0) continue
    if (o.isPlayer) continue
    const ocx = o.x + o.w / 2
    const ocy = o.y + o.h / 2
    if (o.isDecoy) {
      const d = manhattan(sx, sy, ocx, ocy)
      if (d < nearestDecoyDist) {
        nearestDecoyDist = d
        hasDecoy = true
        decoyX = ocx
        decoyY = ocy
      }
      continue
    }
    teammateCount++
    teammateSumX += ocx
    teammateSumY += ocy
    if (manhattan(sx, sy, ocx, ocy) < CELL * 8) congestion++
  }

  const openDirs: Direction[] = needOpenDirs ? computeOpenDirs(world, tank, list) : EMPTY_DIRS

  // `out` 是调用方提供的复用缓冲（TacticalIntelligence 的每-tank 热路径）：逐字段覆写，
  // 避免每调用分配一个 14 字段对象（语料级 ~300 万次）。契约：**调用方不得保留返回引用**
  // —— 下一次 perceive() 会覆写它；现有消费者都在同一次 updateTank 内同步读完。
  const o = out ?? blankPerception()
  o.selfX = sx
  o.selfY = sy
  o.selfDir = tank.dir
  o.hasPlayer = !!(player && player.alive)
  o.playerX = player ? player.x + player.w / 2 : 0
  o.playerY = player ? player.y + player.h / 2 : 0
  o.hasBase = !!base
  o.baseX = base ? base.x + CELL : 0
  o.baseY = base ? base.y + CELL : 0
  o.hasDecoy = hasDecoy
  o.decoyX = decoyX
  o.decoyY = decoyY
  o.hasThreat = hasThreat
  o.threatDir = threatDir
  o.teammateCount = teammateCount
  o.teammateSumX = teammateSumX
  o.teammateSumY = teammateSumY
  o.congestion = congestion
  o.openDirs = openDirs
  return o
}

/** 全字段默认值的 Perception（供复用缓冲初始化；perceive 会整体覆写）。 */
export function blankPerception(): Perception {
  return {
    selfX: 0,
    selfY: 0,
    selfDir: 'up',
    hasPlayer: false,
    playerX: 0,
    playerY: 0,
    hasBase: false,
    baseX: 0,
    baseY: 0,
    hasDecoy: false,
    decoyX: 0,
    decoyY: 0,
    hasThreat: false,
    threatDir: 'up',
    teammateCount: 0,
    teammateSumX: 0,
    teammateSumY: 0,
    congestion: 0,
    openDirs: EMPTY_DIRS,
  }
}

/**
 * 每拍必算的「响应式」半部 —— 只写**每 tick 消费者**会读的字段。
 *
 * 消费者证据（`TacticalIntelligence` 内全部 `s.` 读点）：
 *   - `reactiveDodge`（每拍）：`hasThreat`、`threatDir`
 *   - `updateFiring`（每拍）：`baseInLineOfFire`、`playerInLineOfFire`、
 *     `decoyInLineOfFire`、`wallInLineOfFire`
 *   - `maybeTunnelOut`：不读 Situation
 * 节拍字段（`distToBase`/`distToPlayer`/`pathBlocked`/`baseDanger`）在这里只给中性默认值，
 * 由 `analyzeTactical` 在「本拍会跑 tactical/strategic think」时覆写。
 */
export function analyzeReactive(
  world: World,
  tank: Tank,
  p: Perception,
  cfg: IntelligenceConfig,
  needLos = true,
): Situation {
  const losRange = cfg.predictionDepth * CELL + TANK
  // `needLos=false` ⇒ 四个布尔全为 false（'none' 不匹配任何分支）。只在「本拍
  // updateFiring 会真正执行（fireTimer<=0）」或「本拍跑 tacticalThink」时才需要它们。
  const baseLOS: ScanHit = needLos ? scanAhead(world, tank, tank.dir, losRange) : 'none'
  // 字面量顺序与拆分前完全一致（同形对象，便于 diff/热路径稳定）。
  return {
    distToBase: Infinity,
    distToPlayer: Infinity,
    baseInLineOfFire: baseLOS === 'base',
    playerVisible: p.hasPlayer,
    playerInLineOfFire: baseLOS === 'player',
    decoyInLineOfFire: baseLOS === 'decoy',
    wallInLineOfFire: baseLOS === 'wall',
    pathBlocked: false,
    hasThreat: p.hasThreat,
    threatDir: p.hasThreat ? p.threatDir : null,
    baseDanger: 0,
    teammateCount: p.teammateCount,
    congestion: p.congestion,
    openDirs: p.openDirs,
  }
}

/**
 * 节拍半部 —— 只在 `tacticalThink`（`thinkTimer <= 0`）或 `strategicThink`
 * （指挥官 `strategicTimer <= 0`）会跑的那一拍调用，覆写四个节拍字段。
 *
 * 惰性时机是纯性能选择：目标朝向的 `scanAhead` 是每拍整段网格步行，却只喂 `pathBlocked`
 * （仅 `evaluateGoals` 读）。**字段值与拆分前逐字节相同**：`scanAhead` 是
 * `(World, tank, dir, maxDist)` 的纯函数（不读 RNG、不写 World），重扫与复用同一行的
 * 结果一致，因此不再需要「与朝向相同就复用」的短路。
 */
export function analyzeTactical(
  world: World,
  tank: Tank,
  p: Perception,
  cfg: IntelligenceConfig,
  s: Situation,
): void {
  const maxDist = FIELD
  s.distToBase = p.hasBase ? manhattan(p.selfX, p.selfY, p.baseX, p.baseY) : Infinity
  s.distToPlayer = p.hasPlayer ? manhattan(p.selfX, p.selfY, p.playerX, p.playerY) : Infinity

  // Path blocked by a breakable brick directly ahead toward the objective?
  const losRange = cfg.predictionDepth * CELL + TANK
  const objX = p.hasBase ? p.baseX : p.playerX
  const objY = p.hasBase ? p.baseY : p.playerY
  const objDir = p.hasBase || p.hasPlayer ? dirToward(p.selfX, p.selfY, objX, objY) : tank.dir
  s.pathBlocked = scanAhead(world, tank, objDir, losRange) === 'wall'

  s.baseDanger = p.hasBase ? Math.max(0, 1 - s.distToBase / maxDist) : 0
}

/**
 * 完整分析 = 响应式半部 + 节拍半部（公开 API）。
 * 对同一个 World/tank/p/cfg，返回值与拆分前的 `analyze` 逐字段相同。
 */
export function analyze(
  world: World,
  tank: Tank,
  p: Perception,
  cfg: IntelligenceConfig,
): Situation {
  const s = analyzeReactive(world, tank, p, cfg)
  analyzeTactical(world, tank, p, cfg, s)
  return s
}
