/** config.ts — rl-config 读取兜底：瞬时读损坏回退上次成功配置，不让 /api/state 500。 */
import { loadConfig } from '../../core/config'
import type { RlConfig } from '../../core/types'

// ────────────────────────── 配置读取（§361③：rl-config.json 瞬时读损坏兜底） ──────────────────────────

const CFG_RETRY_MS = 1000
let cfgLastOk: RlConfig | null = null
let cfgBadUntil = 0
const EMPTY_CONFIG = { version: 1, nodes: [], rl: {} } as unknown as RlConfig

/**
 * 读 rl-config.json + 损坏兜底。控制台自身 saveConfig 用 writeFileSync 非原子写
 * （2026-09-06 §339 同款竞态家族）——轮询恰落在写盘窗口会读到半截/空 JSON，loadConfig
 * 抛错会让 /api/state 整条 500 → 前端「刷新失败，正在重试」banner 假阳性（§361③根因）。
 * 失败回退上次成功配置（内存缓存；1s 坏窗内直接命中缓存，不再反复解析半截文件）；
 * 从未成功过则回退空配置（UI 降级为空节点/组件列表，不 500）。
 */
export function loadConfigSafe(): RlConfig {
  if (Date.now() < cfgBadUntil && cfgLastOk) return cfgLastOk
  try {
    const cfg = loadConfig()
    cfgLastOk = cfg
    cfgBadUntil = 0
    return cfg
  } catch {
    cfgBadUntil = Date.now() + CFG_RETRY_MS
    return cfgLastOk ?? EMPTY_CONFIG
  }
}
