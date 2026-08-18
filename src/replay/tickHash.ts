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
  private parts: string[] = []
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
    for (const v of vals) this.parts.push(v === null || v === undefined ? '' : String(v))
  }

  build(): string {
    return this.parts.join('|')
  }
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
  b.push('P', b.canon(pu.id), pu.type, pu.x, pu.y, pu.w, pu.h, pu.alive, pu.blinkTimer, pu.lifeTimer)
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
  let grid = ''
  for (let r = 0; r < GRID; r++) {
    const row = world.tileMap.grid[r]
    for (let c = 0; c < GRID; c++) {
      const t = row[c]
      grid += TERRAIN_CHAR[t] ?? t
    }
  }
  b.push('G', grid)

  // Entities — deterministic traversal order (see class doc)
  if (world.player) pushTank(b, world.player)
  if (world.player2) pushTank(b, world.player2)
  for (const t of world.tanks) pushTank(b, t)
  for (const t of world.allies) pushTank(b, t)
  for (const bl of world.bullets) pushBullet(b, bl)
  for (const pu of world.powerUps) pushPowerUp(b, pu)

  return fnv1a(b.build())
}