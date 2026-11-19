import type { World } from '../game/World'
import type { Tank, Bullet, PowerUp } from '../types'
import { GRID } from '../constants'

// ================================================================
// World Tick-Hash — desync checkpoint anchors for replay files
// (plan/Replay-TickHash-Chain.md §1.1)
//
// Phase discipline (shared contract, §1.2 — keep in sync with
// InputRecorder.recordFrame and verify-replay's replay loop):
//   Both sides use the SAME expression — `tickCount % REPLAY_HASH_INTERVAL
//   === 0`, where tickCount = number of completed `sim.tick()` calls
//   (= `frames.length` after the push on the recorder side). The k-th hash
//   is the world state sampled after the (k×100)-th `sim.tick()`, and the
//   verifier samples BEFORE any terminal-state break. Round-trip test T1
//   locks this alignment; a T1 failure means the phase drifted.
//
// Design notes:
//   - Hashes the WORLD state, not input — immune to the suspected
//     input-sampling race (recordFrame samples input AFTER sim.tick()
//     consumed it; a key change in between records a frame that was never
//     consumed — the hash chain measures world consequences, not this race).
//   - Entity `id`s come from genId(), a PROCESS-GLOBAL counter that is NOT
//     reset between Worlds — recorder and verifier compute different
//     absolute ids for the same world. Every id (tank/bullet/powerUp) is
//     remapped by FIRST OCCURRENCE in the traversal order so the hash
//     compares world state, not counter state.
//   - Flat canonical string with explicit field order — never
//     JSON.stringify of world objects (key order would vary by construction
//     path). FNV-1a precedent: tools/lib/stage-spec.ts paramsHash.
//   - Fields are tick-sensitive first: a ±1-frame phase shift shows up in
//     TIMERS (spawnTimer, cooldowns, shield) before coordinates. A lean
//     field set would push firstHashMismatch into a later window.
// ================================================================

const FNV_OFFSET = 0x811c9dc5
const FNV_PRIME = 0x01000193

/** FNV-1a (32-bit) — same family as tools/lib/stage-spec.ts paramsHash. */
export function fnv1a(str: string): string {
  let h = FNV_OFFSET
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, FNV_PRIME) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

/** TerrainType → single char (mirrors TileMap.charToTerrain). */
const TERRAIN_CHAR: Record<string, string> = {
  empty: '.',
  brick: 'b',
  steel: 's',
  water: 'w',
  forest: 'f',
  ice: 'i',
  base: 'E',
}

/**
 * Canonical payload builder — deterministic field order, first-occurrence
 * id remap. Entity traversal order (player → player2 → tanks → allies →
 * bullets → powerUps) is deterministic given an equal world, so both sides
 * remap ids identically.
 */
class CanonBuilder {
  /**
   * **增量 FNV**（不是先拼串再 `fnv1a`）。每次 hash 要拼 ~700 个字段：旧形态先建
   * `parts[]`（`join('|')`）再整串 FNV，2026-11-16 profile 里 `push`+`join`+`fnv1a` 占了
   * hash 的全部成本，且大字符串是 rope —— 首次 `charCodeAt` 还要先拍平一次。
   *
   * 这里逐字段、逐字符直接推进同一个 FNV 状态：字段间插 `|`、无尾分隔符 ——
   * 与 `fnv1a(parts.join('|'))` **逐位等价**（字节序与乘法次数完全一致），
   * 省掉整串构造与拍平。
   */
  private h = FNV_OFFSET
  private started = false
  private remap = new Map<number, number>()
  private next = 1

  /** Remap an entity id by first occurrence; ids <= 0 pass through. */
  canon(id: number): number {
    if (id <= 0) return id
    let k = this.remap.get(id)
    if (k === undefined) {
      k = this.next++
      this.remap.set(id, k)
    }
    return k
  }

  /** Append scalars in canonical order (undefined/null → empty string). */
  push(...vals: Array<string | number | boolean | null | undefined>): void {
    for (let i = 0; i < vals.length; i++) {
      // 分隔符直接推进（'|' = 124）：省掉每字段一次单字符 `feed` 调用。
      if (this.started) this.feedCode(124)
      const v = vals[i]
      if (v !== null && v !== undefined) {
        // 数字走整数快路径；字符串就地推进（每 hash ~700 字段，省一次函数调用/字段）。
        if (typeof v === 'number') this.feedNum(v)
        else if (typeof v === 'string') {
          let h = this.h
          for (let k = 0; k < v.length; k++) {
            h = Math.imul(h ^ v.charCodeAt(k), FNV_PRIME) >>> 0
          }
          this.h = h
        } else this.feed(v === true ? 'true' : 'false')
      }
      this.started = true
    }
  }

  /**
   * 数字直接推进 FNV（整数快路径）。
   *
   * `String(v)` 是 `push` 自耗的大头（本机 profile 4.1%）。整数（|v| ≤ 2^31-1）
   * 逐位算字符码写入，与 `String(v)` 的十进制表示**逐字节相同**（含 `-0` → "0"）；
   * 浮点与大整数（rng state、带小数的坐标）仍走 `String`，格式完全一致。
   */
  private feedNum(v: number): void {
    if (!Number.isInteger(v) || v > 2147483647 || v < -2147483647) {
      this.feed(String(v))
      return
    }
    if (v < 0) {
      this.feedCode(45) // '-'
      v = -v
    }
    if (v === 0) {
      this.feedCode(48) // '0'（-0 也落在这里）
      return
    }
    let mag = 1
    while (v >= mag * 10) mag *= 10
    for (; mag >= 1; mag /= 10) {
      this.feedCode(48 + (((v / mag) | 0) % 10))
    }
  }

  /** 单字符推进（与 `feed(String.fromCharCode(code))` 逐位等价）。 */
  private feedCode(code: number): void {
    this.h = Math.imul(this.h ^ code, FNV_PRIME) >>> 0
  }

  /** FNV-1a 推进（与 `fnv1a` 同一算式：`h ^= code; h = imul(h, prime) >>> 0`）。 */
  private feed(s: string): void {
    let h = this.h
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i)
      h = Math.imul(h, FNV_PRIME) >>> 0
    }
    this.h = h
  }

  build(): string {
    return this.h.toString(16).padStart(8, '0')
  }
}

/**
 * 地形文本（676 字符）的纯 memo，按 `TileMap.revision` 失效。
 *
 * 为什么要它：地形串每 100 tick 的 tickHash 都要重拼一遍，而地形**绝大多数时候没变** ——
 * 本机 profile 里 `worldTickHash` 自身 14.6 µs/hash 几乎全是这 676 次字符串拼接。
 *
 * 为什么不算 §2.2 的隐藏状态：它是 `(tileMap, revision) → string` 的**纯函数缓存**，
 * 不参与任何玩法判定；revision 覆盖 `loadStage`/`set`/`destroy`/`destroyAllBaseCells`
 * 与快照还原（`TileMap.ts` 的 §127 契约），因此命中与否的返回值逐字节相同。
 */
const TERRAIN_TEXT_CACHE = new WeakMap<object, { revision: number; text: string }>()

function terrainText(map: {
  revision: number
  grid: ReadonlyArray<ReadonlyArray<string>>
}): string {
  const hit = TERRAIN_TEXT_CACHE.get(map)
  if (hit !== undefined && hit.revision === map.revision) return hit.text
  const grid = map.grid
  let text = ''
  for (let r = 0; r < GRID; r++) {
    const row = grid[r]
    for (let c = 0; c < GRID; c++) {
      const t = row[c]
      text += TERRAIN_CHAR[t] ?? t
    }
  }
  TERRAIN_TEXT_CACHE.set(map, { revision: map.revision, text })
  return text
}

/** All gameplay-relevant scalar fields of a tank (types.ts Tank + AIState). */
function pushTank(b: CanonBuilder, t: Tank): void {
  b.push(
    'T',
    b.canon(t.id),
    t.x,
    t.y,
    t.dir,
    t.hp,
    t.alive,
    t.kind,
    t.level ?? 0,
    t.moving,
    t.boatTimer ?? 0,
    t.spawnTimer,
    t.shieldTimer ?? 0,
    t.lastFire,
    t.nextFireInterval,
    t.fireCount,
    t.vx,
    t.vy,
    t.frenzyTimer ?? 0,
    t.frenzyShotsLeft ?? 0,
    t.frenzyDir ?? '',
    t.frenzyInterval ?? 0,
    t.frenzyLastFire ?? 0,
    t.prevMoveDir ?? '',
    t.lastTurnMs ?? 0,
    t.guardExpireFrame ?? 0,
    t.flashTimer ?? 0,
    t.hitCount ?? 0,
    t.allegiance,
    t.isPlayer ?? false,
    t.isExtra ?? false,
    t.isDecoy ?? false,
    t.bonus ?? false,
  )
  if (t.aiState) {
    const a = t.aiState
    b.push(
      'A',
      a.level,
      a.isCommander,
      a.spawnSeq,
      a.thinkTimer,
      a.fireTimer,
      a.currentDir,
      a.tacticalGoal,
      a.targetX,
      a.targetY,
      a.strategicTimer,
      a.strategicGoal,
      a.reactionTimer,
      a.dodgeLock,
      a.vertOnlyTicks,
      a.commanderTimer,
      a.directive,
      a.directiveAge,
      a.directiveSeq,
      a.directiveCompliant,
    )
  }
}

function pushBullet(b: CanonBuilder, bl: Bullet): void {
  b.push(
    'B',
    b.canon(bl.id),
    bl.x,
    bl.y,
    bl.dir,
    b.canon(bl.ownerId),
    bl.speed,
    bl.power,
    bl.damage,
    bl.isPlayer,
    bl.allegiance,
    bl.ownerKind,
  )
}

function pushPowerUp(b: CanonBuilder, pu: PowerUp): void {
  b.push(
    'P',
    b.canon(pu.id),
    pu.type,
    pu.x,
    pu.y,
    pu.w,
    pu.h,
    pu.alive,
    pu.blinkTimer,
    pu.lifeTimer,
  )
}

/**
 * Pure function — reads the World read-only, never consumes RNG, never
 * mutates anything, never touches presentation state.
 */
export function worldTickHash(world: World): string {
  const b = new CanonBuilder()

  // World-level scalars (tick-sensitive first — plan §1.1)
  b.push(
    'W',
    world.frame,
    world.rng.getState(),
    world.spawnTimer,
    world.freezeTimer,
    world.fenceExpireFrame ?? 0,
    world.baseHp,
    world.score,
    world.score2,
    world.lives,
    world.lives2,
    world.playerLevel,
    world.playerLevel2,
    world.killCount,
    world.enemiesSpawned,
    world.enemiesRemaining,
    world.spawnSeqCounter,
    world.guardStock,
    world.frenzyStock,
    world.rewindStock,
    world.sacrificeStock,
    world.stageClearTimer,
    world.gameOverTimer,
    world.pickupWindowTimer,
    world.pickupWindowEntered,
  )

  // Tile grid — full 26×26 grid, one char per sub-block (676 chars)
  b.push('G', terrainText(world.tileMap))

  // Entities — deterministic traversal order (see class doc)
  if (world.player) pushTank(b, world.player)
  if (world.player2) pushTank(b, world.player2)
  for (const t of world.tanks) pushTank(b, t)
  for (const t of world.allies) pushTank(b, t)
  for (const bl of world.bullets) pushBullet(b, bl)
  for (const pu of world.powerUps) pushPowerUp(b, pu)

  // `build()` 已经是 FNV-1a 摘要（增量推进），**不要**再套一层 `fnv1a`。
  return b.build()
}
