/**
 * P′-0 test support：把实体 id 归一成「首次出现序号」，用于跨分支/跨 run 的
 * 全字段对账。
 *
 * 背景：`genId()` 是**进程级单调计数器**（src/game/ids.ts），快照恢复刻意不回拨
 * （否则会与 replay/undo 历史里的 live id 冲突）。因此同一逻辑实体的 id 在两次
 * 独立 run / 分支之间会相差一个常量偏移。改动不在本卡范围内；分支等价性的正确
 * 口径是「结构逐字段一致、id 一一对应」，本函数负责后一半。
 */
const ID_KEY = /id$/i

function canonId(id: number, map: Map<number, number>): number {
  if (!Number.isFinite(id) || id < 0) return id
  const hit = map.get(id)
  if (hit !== undefined) return hit
  const next = map.size + 1
  map.set(id, next)
  return next
}

function walk(value: unknown, map: Map<number, number>): unknown {
  if (value === null || value === undefined) return value
  if (typeof value !== 'object') return value
  if (Array.isArray(value)) return value.map((v) => walk(v, map))
  if (value instanceof Map) {
    const out = new Map<unknown, unknown>()
    for (const [k, v] of value) {
      out.set(typeof k === 'number' ? canonId(k, map) : k, walk(v, map))
    }
    return out
  }
  if (value instanceof Set) {
    const out = new Set<unknown>()
    for (const v of value) out.add(walk(v, map))
    return out
  }
  if (value instanceof Int32Array) {
    const out = new Int32Array(value.length)
    for (let i = 0; i < value.length; i++) out[i] = canonId(value[i], map)
    return out
  }
  if (ArrayBuffer.isView(value)) return value
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    out[k] = ID_KEY.test(k) && typeof v === 'number' ? canonId(v, map) : walk(v, map)
  }
  return out
}

/** 深拷贝并把 id 归一（不修改输入）。 */
export function canonicalizeIds<T>(value: T): T {
  return walk(value, new Map()) as T
}
