// ================================================================
// Generic IndexedDB object-store wrapper.
//
// One implementation of the open/transaction/request plumbing shared by
// the snapshot and replay persistence backends (plan/refactor.agy.md
// §3.2). Each domain keeps its own database + store names so the two
// never interfere; only the boilerplate is deduplicated here.
// ================================================================

/**
 * Thin typed wrapper around a single IndexedDB object store keyed by
 * `'id'`. All methods return promises; failures reject with the raw
 * `DOMException` from the underlying request.
 */
export class IndexedDBStore<T> {
  private dbPromise: Promise<IDBDatabase> | null = null

  constructor(
    /** Database name (e.g. `'bc-snapshots'`). */
    private readonly dbName: string,
    /** Schema version passed to `indexedDB.open`. */
    private readonly dbVersion: number,
    /** Object-store name created on upgrade if missing. */
    private readonly storeName: string,
  ) {}

  private open(): Promise<IDBDatabase> {
    if (this.dbPromise) return this.dbPromise
    this.dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(this.dbName, this.dbVersion)
      req.onupgradeneeded = () => {
        const db = req.result
        if (!db.objectStoreNames.contains(this.storeName)) {
          db.createObjectStore(this.storeName, { keyPath: 'id' })
        }
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
    return this.dbPromise
  }

  private async tx(mode: IDBTransactionMode): Promise<IDBObjectStore> {
    const db = await this.open()
    return db.transaction(this.storeName, mode).objectStore(this.storeName)
  }

  private request<R>(req: IDBRequest<R>): Promise<R> {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
  }

  /** Insert or overwrite a value (keyed by its `id` property). */
  async put(value: T): Promise<void> {
    const store = await this.tx('readwrite')
    await this.request(store.put(value))
  }

  /** Delete the entry with the given id. */
  async delete(id: string): Promise<void> {
    const store = await this.tx('readwrite')
    await this.request(store.delete(id))
  }

  /** Fetch every entry in the store. */
  async getAll(): Promise<T[]> {
    const store = await this.tx('readonly')
    return this.request(store.getAll() as IDBRequest<T[]>)
  }
}
