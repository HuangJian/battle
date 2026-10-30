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
  claimSettleMs: number
  stopGraceMs: number
  confirmStopOnCreate: boolean
  configMarkers: string[]
}

export const CONFIG_KEY = 'bnc:config'

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
