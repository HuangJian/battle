/** config.ts — GM 配置（plan §4.7）：每个字段都做形状/范围校验，非法值回默认、绝不抛。 */
import type { Runtime } from '../runtime'

export interface BncConfig {
  dashboardBase: string
  enabled: boolean
  shipIntervalSec: number
  maxChunkBytes: number
  maxBytesPerTick: number
  backlogDropBytes: number
  keepBytes: number
  autoClearBytes: number
  autoClearDeferWarnSec: number
  autoClearForceBytes: number
  largeCellBytes: number
  largeCellIntervalSec: number
  /** 周期性「保存并清空」间隔（秒）；0 = 关闭。 */
  autoSaveIntervalSec: number
  /** 周期性「保存并清空」总开关（面板可切换）。 */
  autoSaveEnabled: boolean
  claimSettleMs: number
  stopGraceMs: number
  confirmStopOnCreate: boolean
  configMarkers: string[]
}

export const CONFIG_KEY = 'bnc:config'

/** 面板「自动保存并清空」的本页设置（开关 + 间隔）。 */
export interface AutoSaveSettings {
  enabled: boolean
  intervalSec: number
}

/** 本页自动保存设置的键前缀：页标签跨刷新稳定、不同页签分开。 */
export const AUTO_SAVE_KEY_PREFIX = 'bnc:autoSave:'

export function autoSaveKeyFor(page: string): string {
  return `${AUTO_SAVE_KEY_PREFIX}${page}`
}

export const DEFAULT_CONFIG: BncConfig = {
  dashboardBase: 'http://127.0.0.1:8900',
  enabled: true,
  shipIntervalSec: 5,
  maxChunkBytes: 48 * 1024,
  maxBytesPerTick: 1024 * 1024,
  backlogDropBytes: 8 * 1024 * 1024,
  keepBytes: 1024 * 1024,
  autoClearBytes: 1024 * 1024,
  autoClearDeferWarnSec: 60,
  autoClearForceBytes: 8 * 1024 * 1024,
  largeCellBytes: 8 * 1024 * 1024,
  largeCellIntervalSec: 30,
  autoSaveIntervalSec: 30 * 60,
  autoSaveEnabled: true,
  claimSettleMs: 1000,
  stopGraceMs: 600_000,
  confirmStopOnCreate: true,
  configMarkers: ['CFG = {', 'CFG={', '# @title'],
}

/** 基址只接受**回环**主机名：DoD 的「不发 POST 到非回环地址」不能与「基址可改」自相矛盾。 */
export function isLoopbackHttpBase(value: string): boolean {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return false
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false
  const host = url.hostname.toLowerCase()
  return host === '127.0.0.1' || host === 'localhost' || host === '::1' || host === '[::1]'
}

function num(raw: Record<string, unknown>, key: keyof BncConfig, min: number, max: number): number {
  const v = raw[key]
  if (typeof v !== 'number' || !Number.isFinite(v)) return DEFAULT_CONFIG[key] as number
  return Math.min(max, Math.max(min, v))
}

function bool(raw: Record<string, unknown>, key: keyof BncConfig): boolean {
  const v = raw[key]
  return typeof v === 'boolean' ? v : (DEFAULT_CONFIG[key] as boolean)
}

/**
 * 存储值 → 配置。非法值逐字段回默认（不是整份丢弃）：一个坏字段不该让其余调参失效。
 * `dashboardBase` 非回环 ⇒ 回默认（面板仍会显示实际生效值）。
 */
export function sanitizeConfig(raw: unknown): BncConfig {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return { ...DEFAULT_CONFIG }
  const o = raw as Record<string, unknown>
  const base =
    typeof o.dashboardBase === 'string' && isLoopbackHttpBase(o.dashboardBase)
      ? o.dashboardBase
      : DEFAULT_CONFIG.dashboardBase
  const markers = Array.isArray(o.configMarkers)
    ? o.configMarkers.filter((m): m is string => typeof m === 'string' && m.length > 0).slice(0, 8)
    : DEFAULT_CONFIG.configMarkers
  return {
    dashboardBase: base,
    enabled: bool(o, 'enabled'),
    shipIntervalSec: num(o, 'shipIntervalSec', 1, 60),
    maxChunkBytes: num(o, 'maxChunkBytes', 1024, 48 * 1024),
    maxBytesPerTick: num(o, 'maxBytesPerTick', 4096, 8 * 1024 * 1024),
    backlogDropBytes: num(o, 'backlogDropBytes', 1024 * 1024, 64 * 1024 * 1024),
    keepBytes: num(o, 'keepBytes', 1024, 4 * 1024 * 1024),
    autoClearBytes: num(o, 'autoClearBytes', 0, 64 * 1024 * 1024),
    autoClearDeferWarnSec: num(o, 'autoClearDeferWarnSec', 10, 600),
    autoClearForceBytes: num(o, 'autoClearForceBytes', 1024 * 1024, 64 * 1024 * 1024),
    largeCellBytes: num(o, 'largeCellBytes', 1024 * 1024, 64 * 1024 * 1024),
    largeCellIntervalSec: num(o, 'largeCellIntervalSec', 5, 300),
    autoSaveIntervalSec: num(o, 'autoSaveIntervalSec', 0, 24 * 3600),
    autoSaveEnabled: bool(o, 'autoSaveEnabled'),
    claimSettleMs: num(o, 'claimSettleMs', 200, 5000),
    stopGraceMs: num(o, 'stopGraceMs', 60_000, 30 * 60_000),
    confirmStopOnCreate: bool(o, 'confirmStopOnCreate'),
    configMarkers: markers.length > 0 ? markers : DEFAULT_CONFIG.configMarkers,
  }
}

/** 从 GM 存储读配置；坏值由 `sanitizeConfig` 逐字段兜底（读本身也不抛）。 */
export function loadConfig(runtime: Runtime): BncConfig {
  return sanitizeConfig(runtime.getValue(CONFIG_KEY, null))
}

export function saveConfig(runtime: Runtime, config: BncConfig): void {
  runtime.setValue(CONFIG_KEY, sanitizeConfig(config))
}

function sanitizeAutoSave(raw: unknown, fallback: AutoSaveSettings): AutoSaveSettings {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return { ...fallback }
  const o = raw as Record<string, unknown>
  return {
    enabled: typeof o.enabled === 'boolean' ? o.enabled : fallback.enabled,
    intervalSec:
      typeof o.intervalSec === 'number' && Number.isFinite(o.intervalSec)
        ? Math.min(24 * 3600, Math.max(0, Math.round(o.intervalSec)))
        : fallback.intervalSec,
  }
}

/** 读本页的自动保存设置；没有本页键（旧版）时沿用全局 config 的值。 */
export function loadAutoSave(
  runtime: Runtime,
  page: string,
  fallback: AutoSaveSettings,
): AutoSaveSettings {
  return sanitizeAutoSave(runtime.getValue(autoSaveKeyFor(page), null), fallback)
}

/** 只写本页键；绝不回写 `bnc:config`（否则又会串到同站的其他页面）。 */
export function saveAutoSave(runtime: Runtime, page: string, settings: AutoSaveSettings): void {
  runtime.setValue(
    autoSaveKeyFor(page),
    sanitizeAutoSave(settings, {
      enabled: DEFAULT_CONFIG.autoSaveEnabled,
      intervalSec: DEFAULT_CONFIG.autoSaveIntervalSec,
    }),
  )
}

/** 把本页覆盖应用到该页唯一的 config 对象（host / single / frame 各一份）。 */
export function applyPageAutoSave(runtime: Runtime, page: string, config: BncConfig): void {
  const saved = loadAutoSave(runtime, page, {
    enabled: config.autoSaveEnabled,
    intervalSec: config.autoSaveIntervalSec,
  })
  config.autoSaveEnabled = saved.enabled
  config.autoSaveIntervalSec = saved.intervalSec
}

/** 面板「启动后停止其它 ppo worker」的本页设置（只在离线训练页显示）。 */
export const STOP_PPO_KEY_PREFIX = 'bnc:stopPpo:'

export function stopPpoKeyFor(page: string): string {
  return `${STOP_PPO_KEY_PREFIX}${page}`
}

/** 读本页开关；缺省 / 坏值 ⇒ fallback（默认关）。 */
export function loadStopPpo(runtime: Runtime, page: string, fallback = false): boolean {
  const raw = runtime.getValue(stopPpoKeyFor(page), null)
  return typeof raw === 'boolean' ? raw : fallback
}

/** 只写本页键：同站多页互不影响，绝不回写 `bnc:config`。 */
export function saveStopPpo(runtime: Runtime, page: string, enabled: boolean): void {
  runtime.setValue(stopPpoKeyFor(page), enabled === true)
}

/**
 * 面板「空闲自动停会话」——**全局**开关（不按页）：停的是这台机器/这个会话的配额，一个会话只有
 * 一份，任何页面（host / 单文档站点）读到的是同一个值；多开页签各自停自己的会话本就该同时生效。
 *
 * 为什么不做成按页：Kaggle 的面板在外层页，而判据（hub 队列）在两层都能取，按页只会让
 * 「我在外层开了开关、里层却不生效」变成一道没人猜得到的坑。
 */
export const IDLE_STOP_KEY = 'bnc:idleStop'

/** 读全局开关；缺省 / 坏值 ⇒ fallback（默认关：停会话不可逆）。 */
export function loadIdleStop(runtime: Runtime, fallback = false): boolean {
  const raw = runtime.getValue(IDLE_STOP_KEY, null)
  return typeof raw === 'boolean' ? raw : fallback
}

export function saveIdleStop(runtime: Runtime, enabled: boolean): void {
  runtime.setValue(IDLE_STOP_KEY, enabled === true)
}
