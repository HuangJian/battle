import type { Replay, ReplayID, ReplayStorageBackend } from './types'
import { IndexedDBStore } from '../utils/idb-store'

// ================================================================
// IndexedDB persistence backend for replays
// (plan/replay.md §10.1, §10.2)
//
// Mirrors the snapshot storage pattern — separate database to avoid
// interfering with snapshot storage. The open/tx/request plumbing
// lives in utils/idb-store.ts (shared with the snapshot backend).
// ================================================================

const DB_NAME = 'bc-replays'
const DB_VERSION = 1
const STORE = 'replays'

export class IndexedDBReplayStorage implements ReplayStorageBackend {
  private store = new IndexedDBStore<Replay>(DB_NAME, DB_VERSION, STORE)

  save(replay: Replay): Promise<void> {
    return this.store.put(replay)
  }

  delete(id: ReplayID): Promise<void> {
    return this.store.delete(id)
  }

  loadAll(): Promise<Replay[]> {
    return this.store.getAll()
  }
}

/** Backend factory — returns null where IndexedDB is unavailable (tests). */
export function createReplayStorage(): ReplayStorageBackend | null {
  if (typeof indexedDB === 'undefined') return null
  return new IndexedDBReplayStorage()
}
