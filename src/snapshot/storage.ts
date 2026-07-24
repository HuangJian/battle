import type { GameSnapshot, SnapshotID, SnapshotStorageBackend } from './types'

// ================================================================
// IndexedDB persistence backend
//
// The physical storage layout is internal to this file (plan §16).
// IndexedDB is used instead of localStorage because a full snapshot
// (world state + thumbnail) is ~30 KB and up to 160 snapshots may be
// retained — comfortably beyond localStorage quotas.
// ================================================================

const DB_NAME = 'bc-snapshots'
const DB_VERSION = 1
const STORE = 'snapshots'

export class IndexedDBStorage implements SnapshotStorageBackend {
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

  async save(snapshot: GameSnapshot): Promise<void> {
    const store = await this.tx('readwrite')
    await this.request(store.put(snapshot))
  }

  async delete(id: SnapshotID): Promise<void> {
    const store = await this.tx('readwrite')
    await this.request(store.delete(id))
  }

  async loadAll(): Promise<GameSnapshot[]> {
    const store = await this.tx('readonly')
    const all = await this.request(store.getAll())
    return all as GameSnapshot[]
  }
}

/** Backend factory — returns null where IndexedDB is unavailable (tests). */
export function createDefaultStorage(): SnapshotStorageBackend | null {
  if (typeof indexedDB === 'undefined') return null
  return new IndexedDBStorage()
}
