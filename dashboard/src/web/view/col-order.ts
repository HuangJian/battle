/** col-order.ts — **列顺序偏好**的解析与应用（用户 2026-11-10：拖拽表头自定义列位置）。
 *
 *  存储形态 = **全部列键**的数组，不是「当前可见列」：
 *  只存可见列的话，藏一列再拖表头，重新显示时那一列就掉回模型位置了（用户点过的地方会漂）。
 *
 *  三条规则（改这里之前先读）：
 *    1. 存储里认识的键按存储序；不认识的（模型新加的列、**另一个视图独有的列**）按模型序
 *       插到「模型上它前面最近的那一列」之后——升级后新列不会消失，也不会因为另一个视图
 *       没存过它就被甩到表格尾（2026-11-10：拖「胜率」后 eval 胜率跑到最后）；
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

/** 键序 → 列序。认识的键按存储序；存储里没有的键**回到模型位**（见下）；不改入参。 */
export function applyColOrder<T extends { key: string }>(
  cols: readonly T[],
  order: readonly string[] | null,
): T[] {
  // 空顺序 / 单列 / 空表 ⇒ 原样（不必拼装）
  if (!order || order.length === 0 || cols.length < 2) return [...cols]
  const byKey = new Map(cols.map((c) => [c.key, c]))
  const used = new Set<string>()
  const out: T[] = []
  // ① 存储序里认识的键：用户拖出来的相对顺序原样保留。
  for (const k of order) {
    const c = byKey.get(k)
    if (!c || used.has(c.key)) continue
    out.push(c)
    used.add(c.key)
  }
  // ② 存储里没有的键：按模型序插到「前一个模型邻居」之后。
  //    为什么不是一律接尾（旧行为）：同一个存储键服务列集合不同的多个视图（全部 / eval /
  //    首页主行 / 首页 eval），接尾会让对方视图独有的列（eval 胜率、配对四列…）在切换时
  //    全部甩到最后；旧存储缺键也靠这条自动归位。列数 ≤ ~107，二次方足够快。
  for (let i = 0; i < cols.length; i++) {
    const c = cols[i]
    if (used.has(c.key)) continue
    let at = 0
    for (let j = i - 1; j >= 0; j--) {
      const pos = out.findIndex((x) => x.key === cols[j].key)
      if (pos >= 0) {
        at = pos + 1
        break
      }
    }
    out.splice(at, 0, c)
    used.add(c.key)
  }
  return out
}

/** 键全集 + 存储序 → **完整键序**（缺的键按**各自视图的模型序**归位）。
 *
 *  `order` 是权威：已经在存储里的键（包括另一视图的列、被藏起来的列）原样保留。这是
 *  「动 rollout 的胜率不许带走 eval 的胜率」的关键——旧版每次拖拽都用当前可见列重算，
 *  另一视图独有的键被重新摆位，于是两边互相拉扯（2026-11-10 报障）。
 *
 *  只有存储里**没有**的键才由 `refs`（每个视图一份模型序，按顺序尝试）归位：扁平模型序里
 *  `evalRate` 紧跟在 `winRate` 后面，anchoring 到它就等于让 eval 胜率跟着 rollout 胜率走
 *  ——必须用 eval 视图自己的模型序（`EVAL_ROW_KEYS`）把它锚在 `time` 后面。
 *
 *  无存储（首次拖拽）时调用方传「当前渲染顺序」进来，整张表因此不会跳列。
 */
export function resolveColOrder(
  keys: readonly string[],
  order: readonly string[] | null,
  refs: readonly (readonly string[])[] = [],
): string[] {
  if (!order || order.length === 0) return [...keys]
  const known = new Set(keys)
  const used = new Set<string>()
  const out: string[] = []
  // ① 存储序是权威：认识的键原样搬过来（另一视图 / 隐藏列都保住位置）。
  for (const k of order) {
    if (!known.has(k) || used.has(k)) continue
    out.push(k)
    used.add(k)
  }
  // ② 缺键按参照序归位：插到前驱之后；没有前驱就落到后继之前（再没有就接尾）。
  const place = (ref: readonly string[]): void => {
    for (let i = 0; i < ref.length; i++) {
      const k = ref[i]
      if (!known.has(k) || used.has(k)) continue
      let pred = -1
      for (let j = i - 1; j >= 0 && pred < 0; j--) pred = out.indexOf(ref[j])
      if (pred >= 0) {
        out.splice(pred + 1, 0, k)
      } else {
        let succ = -1
        for (let j = i + 1; j < ref.length && succ < 0; j++) succ = out.indexOf(ref[j])
        out.splice(succ >= 0 ? succ : out.length, 0, k)
      }
      used.add(k)
    }
  }
  for (const ref of refs) place(ref)
  // ③ 参照序没覆盖的键（模型新加的列）：按全集序接尾（不会消失）。
  for (const k of keys) {
    if (used.has(k)) continue
    out.push(k)
    used.add(k)
  }
  return out
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
