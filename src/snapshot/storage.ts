import type { GameSnapshot, SnapshotID, SnapshotStorageBackend } from './types'
import { IndexedDBStore } from '../utils/idb-store'

// ================================================================
// IndexedDB persistence backend
//
// The physical storage layout is internal to this file (plan §16).
// IndexedDB is used instead of localStorage because a full snapshot
// (world state + thumbnail) is ~30 KB and up to 160 snapshots may be
// retained — comfortably beyond localStorage quotas.
//
// The open/tx/request plumbing lives in utils/idb-store.ts (shared
// with the replay backend).
// ================================================================

const DB_NAME = 'bc-snapshots'
const DB_VERSION = 1
const STORE = 'snapshots'

export class IndexedDBStorage implements SnapshotStorageBackend {
  private store = new IndexedDBStore<GameSnapshot>(DB_NAME, DB_VERSION, STORE)

  save(snapshot: GameSnapshot): Promise<void> {
    return this.store.put(snapshot)
  }

  delete(id: SnapshotID): Promise<void> {
    return this.store.delete(id)
  }

  loadAll(): Promise<GameSnapshot[]> {
    return this.store.getAll()
  }
}

/** Backend factory — returns null where IndexedDB is unavailable (tests). */
export function createDefaultStorage(): SnapshotStorageBackend | null {
  if (typeof indexedDB === 'undefined') return null
  return new IndexedDBStorage()
}
