/** col-order.ts — **列顺序偏好**的解析与应用（用户 2026-11-10：拖拽表头自定义列位置）。
 *
 *  存储形态 = **全部列键**的数组，不是「当前可见列」：
 *  只存可见列的话，藏一列再拖表头，重新显示时那一列就掉回模型位置了（用户点过的地方会漂）。
 *
 *  三条规则（改这里之前先读）：
 *    1. 存储里认识的键按存储序；不认识的（模型新加的列、别表的键）按**模型序接在后面**
 *       —— 升级后新列不会消失，旧存储也不会把别的表的顺序注入本表；
 *    2. 顺序只管显示，不改模型（表头文字/口径仍唯一来自 `metric-columns.ts`）；
 *    3. 坏存储 = 没有顺序（模型序），永不抛。
 *
 *  纯数据 + 纯函数（与其它 `view/` 模块同规：服务端与客户端共用）。
 */

/** 存储值（JSON 字符串数组）→ 键序。坏值 / 非字符串元素 / 空 ⇒ null（= 模型顺序）。 */
export function parseColOrder(raw: string | null | undefined): string[] | null {
  if (!raw) return null
  try {
    const v = JSON.parse(raw) as unknown
    if (!Array.isArray(v) || !v.every((x) => typeof x === 'string')) return null
    // 重复键只留第一次（换位算法按「移除 + 插入」工作，重复键会让它插错位）
    return [...new Set(v as string[])]
  } catch {
    return null
  }
}

/** 键序 → 列序。认识的键按存储序，其余按模型序接在尾部；不改入参。 */
export function applyColOrder<T extends { key: string }>(
  cols: readonly T[],
  order: readonly string[] | null,
): T[] {
  // 空顺序 / 单列 / 空表 ⇒ 原样（也不必进 sort）
  if (!order || order.length === 0 || cols.length < 2) return [...cols]
  const rank = new Map(order.map((k, i) => [k, i]))
  const last = Number.MAX_SAFE_INTEGER
  return [...cols].sort((a, b) => (rank.get(a.key) ?? last) - (rank.get(b.key) ?? last))
}

/** 把 `from` 挪到 `to` 之前（拖到谁身上就插到谁前面）。
 *  任一键不在 `order` 里 ⇒ 原样返回（陈旧存储/拖错目标都不许制造乱序）。 */
export function moveColKey(order: readonly string[], from: string, to: string): string[] {
  if (from === to) return [...order]
  const next = [...order]
  const i = next.indexOf(from)
  if (i < 0) return next
  next.splice(i, 1)
  const j = next.indexOf(to)
  if (j < 0) return [...order]
  next.splice(j, 0, from)
  return next
}
