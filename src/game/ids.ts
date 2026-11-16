/**
 * Entity id allocation — the single source of `genId()` (AGENTS §3).
 *
 * Deliberate hidden-state exemption (refactor.trae.md §4.3): the counter is
 * module-level and survives World resets so restored snapshots in the SAME
 * process never reissue ids that are still live in replay/undo history.
 * Rationale recorded at types.ts (WorldSnapshot id field doc) — this does NOT
 * contradict the "no hidden state" banner in World.ts, which is about gameplay
 * state.
 *
 * `reserveNextId` closes the other half of that invariant: a snapshot may come
 * from ANOTHER process (replay playback in a fresh session, replay→corpus
 * rebuild, imported saves). There the local counter can be far below the ids
 * stored in the snapshot, so newly spawned entities would eventually reissue an
 * id that is already live. Two live entities sharing an id silently break every
 * identity check (`ownerId === tank.id`, guard-AI map, `activeCommanderId`) and
 * tickHash's first-occurrence canonicalisation — observed 2026-11-17 as a rare
 * worker-history-dependent tickHash mismatch in replay→shard write pools.
 */

let nextId = 1

/** Allocate the next process-unique entity id. */
export function genId(): number {
  return nextId++
}

/**
 * Guarantee that every future `genId()` is greater than `maxId` — call after
 * restoring a snapshot whose ids were issued by another process/allocator.
 * Monotonic: never lowers the counter.
 */
export function reserveNextId(maxId: number): void {
  if (Number.isFinite(maxId) && maxId >= nextId) nextId = Math.floor(maxId) + 1
}
