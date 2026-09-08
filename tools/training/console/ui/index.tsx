/** index.tsx — 控制台浏览器入口：旧键迁移（GLM-U6）+ tc.* 白名单清理（DS-E9）+ hydrate。 */

import { hydrate } from 'preact'
import { App } from './app'
import {
  cleanupNonTcKeys,
  LEGACY_KEY_RULES,
  migrateLegacyKey,
  type ConsoleStateView,
} from '../../ui/view'

declare const window: Window & { __INITIAL__?: ConsoleStateView }

const data = window.__INITIAL__
if (!data) throw new Error('missing window.__INITIAL__（首屏 SSR 应内联 /api/state 快照）')

// 旧页 localStorage 键迁移：失败保留旧键 + warn 一次（下次刷新仍可读）；成功写新键删旧键
for (const rule of LEGACY_KEY_RULES) {
  if (!migrateLegacyKey(localStorage, rule)) {
    console.warn(`[console] 旧键 ${rule.old} 迁移失败——保留原键（值不在合法集合）`)
  }
}
// 白名单清理：前缀非 tc.* 且非遗留键的残留删除
const removed = cleanupNonTcKeys(localStorage)
if (removed.length > 0)
  console.warn(`[console] 已清理 ${removed.length} 个非白名单 localStorage 键`)

const root = document.getElementById('root')
if (!root) throw new Error('missing #root')
hydrate(<App initial={data} />, root)
