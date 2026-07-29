import type { Replay, ReplayID, ReplayStorageBackend } from './types'

// ================================================================
// IndexedDB persistence backend for replays
// (plan/replay.md §10.1, §10.2)
//
// Mirrors the snapshot storage pattern — separate database to avoid
// interfering with snapshot storage.
// ================================================================

const DB_NAME = 'bc-replays'
const DB_VERSION = 1
const STORE = 'replays'

export class IndexedDBReplayStorage implements ReplayStorageBackend {
  private dbPromise: Promise<IDBDatabase> | null = null

  private open(): Promise<IDBDatabase> {
    if (this.dbPromise) return this.dbPromise
    this.dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION)
      req.onupgradeneeded = () => {
        const db = req.result
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: 'id' })
        }
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
    return this.dbPromise
  }

  private async tx(mode: IDBTransactionMode): Promise<IDBObjectStore> {
    const db = await this.open()
    return db.transaction(STORE, mode).objectStore(STORE)
  }

  private request<T>(req: IDBRequest<T>): Promise<T> {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
  }

  async save(replay: Replay): Promise<void> {
    const store = await this.tx('readwrite')
    await this.request(store.put(replay))
  }

  async delete(id: ReplayID): Promise<void> {
    const store = await this.tx('readwrite')
    await this.request(store.delete(id))
  }

  async loadAll(): Promise<Replay[]> {
    const store = await this.tx('readonly')
    const all = await this.request(store.getAll())
    return all as Replay[]
  }
}

/** Backend factory — returns null where IndexedDB is unavailable (tests). */
export function createReplayStorage(): ReplayStorageBackend | null {
  if (typeof indexedDB === 'undefined') return null
  return new IndexedDBReplayStorage()
}
