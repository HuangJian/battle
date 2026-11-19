import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import { worldTickHash } from '../src/replay/tickHash'
import { seedWorld, placeEnemy, makeBullet } from './helpers'
import type { WorldSnapshot } from '../src/snapshot/types'

/**
 * Regression for the 2026-11-16/17 replay→shard tickHash flake
 * (plan/tickhash-flake.plan.md).
 *
 * Symptom: 7/10-worker WRITE pools failed ~1/700 shards with a mid-game
 * `tickHash mismatch`, while the same shard passed isolated and repeated runs.
 * Root cause: `restoreWorld` loaded a snapshot whose entity ids came from the
 * EXPORT process, while the local module-global id allocator was still low.
 * Newly spawned entities could reissue a restored id; two live entities then
 * shared one id, so `tickHash`'s first-occurrence canonicalisation collapsed
 * them into the same canonical slot. Whether the collision happened depended on
 * how many entities earlier shards had allocated on that worker — hence the
 * worker-history dependence and the "passes in isolation" signature.
 *
 * Contract pinned here:
 *  1. `restoreWorld` reserves the allocator past EVERY id in a foreign snapshot
 *     (entity ids, bullet ownerIds, mines, active commander).
 *  2. With unique ids, `worldTickHash` is invariant under any bijective
 *     renumbering of ids (canonicalisation works).
 */

function populatedSnapshot(): WorldSnapshot {
  const w = seedWorld(123)
  w.startGame('hard', 'modern', 0)
  placeEnemy(w, 5, 5, 'basic', 'down')
  placeEnemy(w, 8, 3, 'fast', 'left')
  w.bullets.push(makeBullet({ ownerId: w.tanks[0].id }))
  w.mines.push({ id: genId(), x: 0, y: 0, w: 0, h: 0, armTimer: 0, alive: true })
  return cloneWorld(w)
}

/** Renumber every id-bearing field into [start, start+n) — models another process's id space. */
function renumber(snap: WorldSnapshot, start: number): void {
  let n = start
  if (snap.player) snap.player.id = n++
  if (snap.player2) snap.player2.id = n++
  const firstTankId = n
  for (const t of snap.tanks) t.id = n++
  for (const t of snap.allies) t.id = n++
  for (const b of snap.bullets) {
    b.id = n++
    b.ownerId = firstTankId
  }
  for (const p of snap.powerUps) p.id = n++
  for (const m of snap.mines) m.id = n++
  snap.activeCommanderId = firstTankId
}

function maxSnapshotId(snap: WorldSnapshot): number {
  let max = 0
  const track = (id: number | null | undefined): void => {
    if (typeof id === 'number' && id > max) max = id
  }
  track(snap.player?.id)
  track(snap.player2?.id)
  for (const t of snap.tanks) track(t.id)
  for (const t of snap.allies) track(t.id)
  for (const b of snap.bullets) {
    track(b.id)
    track(b.ownerId)
  }
  for (const p of snap.powerUps) track(p.id)
  for (const m of snap.mines) track(m.id)
  track(snap.activeCommanderId)
  return max
}

describe('snapshot restore reserves the id space (replay→shard flake regression)', () => {
  it('never reissues an id that a foreign snapshot already carries', () => {
    const snap = populatedSnapshot()
    // Model the exact failure: the next local allocation lands on the first id
    // the foreign snapshot uses, so an unguarded allocator collides immediately.
    const start = genId() + 1
    renumber(snap, start)
    const max = maxSnapshotId(snap)
    expect(max).toBeGreaterThanOrEqual(start)

    const restored = new World()
    restoreWorld(restored, snap)
    expect(genId()).toBeGreaterThan(max)
  })

  it('a worker prefix of foreign restores never aliases a live id', () => {
    // Cross-shard form of the flake: a worker has already allocated ids for
    // earlier shards, then restores a foreign snapshot whose id range starts at
    // the local counter. Without the reserve, the very next spawn reissues an id
    // that is still live in the restored world.
    const foreign = populatedSnapshot()
    renumber(foreign, genId() + 5)
    const restored = new World()
    restoreWorld(restored, foreign)
    for (let i = 0; i < 16; i++)
      placeEnemy(restored, 1 + (i % 20), 1 + Math.floor(i / 20), 'basic', 'down')

    // A second worker-ordered restore must behave the same way.
    const next = populatedSnapshot()
    renumber(next, genId() + 1)
    restoreWorld(restored, next)
    for (let i = 0; i < 16; i++)
      placeEnemy(restored, 1 + (i % 20), 1 + Math.floor(i / 20), 'basic', 'down')

    const ids = [
      restored.player?.id,
      restored.player2?.id,
      ...restored.tanks.map((t) => t.id),
      ...restored.allies.map((t) => t.id),
      ...restored.bullets.map((b) => b.id),
      ...restored.powerUps.map((p) => p.id),
      ...restored.mines.map((m) => m.id),
    ].filter((id): id is number => typeof id === 'number' && id > 0)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('tickHash is invariant under bijective id renumbering (no collisions)', () => {
    const snap = populatedSnapshot()
    const foreign = JSON.parse(JSON.stringify(snap)) as WorldSnapshot
    const start = genId() + 1
    renumber(foreign, start)

    const a = new World()
    restoreWorld(a, foreign)
    const b = new World()
    restoreWorld(b, snap)

    // Same structural spawn in both worlds. Before the fix, A reissues `start`
    // (already used by the restored player), which canonicalises to the same
    // slot as the player and changes the hash; B stays collision-free.
    placeEnemy(a, 12, 12, 'power', 'up')
    placeEnemy(b, 12, 12, 'power', 'up')

    expect(worldTickHash(a)).toBe(worldTickHash(b))
  })
})
