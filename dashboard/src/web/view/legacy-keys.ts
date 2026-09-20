/** legacy-keys.ts — localStorage 旧键迁移与白名单清理（GLM-U6）。 */
// ────────────────────────── 纯函数：localStorage 迁移（GLM-U6） ──────────────────────────

export const TC_KEY_PREFIX = 'tc.'
export const TC_CARD_KEY = (id: string): string => `${TC_KEY_PREFIX}card.${id}`
export const TC_INTERVAL_KEY = (id: string): string => `${TC_KEY_PREFIX}interval.${id}`
export const TC_GLOBAL_INTERVAL = `${TC_KEY_PREFIX}globalInterval`
export const TC_METRICS_FILTER = `${TC_KEY_PREFIX}metrics.filter`
/** 局域网只读横幅关闭键（用户关闭后不再显示；tc. 前缀保证不被 cleanupNonTcKeys 误删）。 */
export const TC_RO_BANNER_DISMISSED = `${TC_KEY_PREFIX}ro.bannerDismissed`
/** 云端停机灰横幅已读键（§386：值=clearedAt，同一恢复事件只提示一次）。 */
export const TC_CLOUDHALT_ACK = `${TC_KEY_PREFIX}cloudHalt.ack`
/** hero 最新 6 轮区块视图（'main' 主行 / 'eval' 干净评估）。 */
export const TC_HERO_ITER_VIEW = `${TC_KEY_PREFIX}hero.iters`
/** hero 最新 6 轮区块折叠态（'1' = 折叠只留标题行）。 */
export const TC_HERO_ITERS_COLLAPSED = `${TC_KEY_PREFIX}hero.iters.collapsed`
export const TC_TREND_RANGE = `${TC_KEY_PREFIX}trend.range`
/** hero 走势图数据源档位（'all' 全部叠加 / 'rollout' 只看采样 / 'eval' 只看干净评估）。 */
export const TC_TREND_SOURCE = `${TC_KEY_PREFIX}trend.source`
// ★ `tc.train.mode`（last-used pull/push/local）已随「启动训练不选模式」于 2026-09-19 退役；
//   旧值留在 localStorage 里无害（tc.* 前缀不会被 cleanupNonTcKeys 误删），不再有任何读者。
export const TC_TRAIN_TOGGLES = `${TC_KEY_PREFIX}train.toggles`
export const TC_NODE_VIEW = (view: 'ctl' | 'pool', key: string): string =>
  `${TC_KEY_PREFIX}node.${view}.${key}`

/** 旧页 localStorage 键 → 新键 + 合法值集（值不在集合内 = 不迁移、保留旧键 + warn）。 */
export interface LegacyKeyRule {
  old: string
  newKey: () => string
  legalValues: ReadonlySet<string>
}

export const LEGACY_KEY_RULES: LegacyKeyRule[] = [
  {
    old: 'pool.disableCollapsed',
    newKey: (): string => `${TC_KEY_PREFIX}card.nodes`,
    legalValues: new Set(['0', '1']),
  },
  {
    old: 'pool.iterFilter',
    newKey: (): string => TC_METRICS_FILTER,
    legalValues: new Set(['all', 'rollout', 'eval']),
  },
  {
    old: 'pool.refreshSec',
    newKey: (): string => TC_GLOBAL_INTERVAL,
    legalValues: new Set(['60', '180', '300', '600', '1800']),
  },
]

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
  key?(index: number): string | null
  readonly length?: number
}

/** 迁移一条旧键：合法 → 写新键 + 删旧键；非法 → 保留旧键 + 返回 false（调用方 console.warn）。 */
export function migrateLegacyKey(storage: StorageLike, rule: LegacyKeyRule): boolean {
  let raw: string | null = null
  try {
    raw = storage.getItem(rule.old)
  } catch {
    return true
  }
  if (raw === null) return true
  if (rule.legalValues.has(raw)) {
    try {
      storage.setItem(rule.newKey(), raw)
      storage.removeItem(rule.old)
    } catch {
      return false
    }
    return true
  }
  return false
}

/** 启动时白名单清理：遍历 storage，前缀非 tc.* 且不在遗留键集合内的键删除。
 *  返回被清理的键列表（便于测试断言）。 */
export function cleanupNonTcKeys(storage: StorageLike): string[] {
  const legacy = new Set(LEGACY_KEY_RULES.map((r) => r.old))
  const removed: string[] = []
  if (!storage.key) return removed
  for (let i = 0; i < (storage.length ?? 0); i++) {
    const k = storage.key(i)
    if (!k) continue
    if (k.startsWith(TC_KEY_PREFIX) || legacy.has(k)) continue
    try {
      storage.removeItem(k)
      removed.push(k)
    } catch {
      /* ignore */
    }
  }
  return removed
}
