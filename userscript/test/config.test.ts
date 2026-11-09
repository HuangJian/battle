import { describe, expect, it } from 'bun:test'
import {
  applyPageAutoSave,
  autoSaveKeyFor,
  DEFAULT_CONFIG,
  isLoopbackHttpBase,
  loadAutoSave,
  sanitizeConfig,
  saveAutoSave,
} from '../src/notebook-companion/config'
import type { Runtime } from '../src/runtime'

function memoryRuntime(): { runtime: Runtime; data: Map<string, unknown> } {
  const data = new Map<string, unknown>()
  return {
    runtime: {
      getValue: <T>(key: string, fallback: T): T =>
        data.has(key) ? (data.get(key) as T) : fallback,
      setValue: (key: string, value: unknown) => void data.set(key, value),
    } as unknown as Runtime,
    data,
  }
}

describe('config：非法存储值逐字段回默认（不抛）', () => {
  it('非对象 / null / 数组 ⇒ 全默认', () => {
    expect(sanitizeConfig(null)).toEqual(DEFAULT_CONFIG)
    expect(sanitizeConfig('x')).toEqual(DEFAULT_CONFIG)
    expect(sanitizeConfig([])).toEqual(DEFAULT_CONFIG)
  })

  it('坏字段各回各的默认，好字段保留', () => {
    const c = sanitizeConfig({
      dashboardBase: 'http://127.0.0.1:9999',
      shipIntervalSec: 'fast',
      maxChunkBytes: 999999,
      enabled: false,
      configMarkers: ['CFG = {', 42, ''],
    })
    expect(c.dashboardBase).toBe('http://127.0.0.1:9999')
    expect(c.shipIntervalSec).toBe(DEFAULT_CONFIG.shipIntervalSec)
    expect(c.maxChunkBytes).toBe(48 * 1024) // 夹到上限
    expect(c.enabled).toBe(false)
    expect(c.configMarkers).toEqual(['CFG = {'])
  })

  it('非回环 dashboardBase 被拒（DoD：不向非回环地址发 POST）', () => {
    for (const bad of [
      'http://example.com:8900',
      'https://10.0.0.2:8900',
      'ftp://127.0.0.1',
      'not a url',
      'http://127.0.0.1.evil.com',
    ]) {
      expect(isLoopbackHttpBase(bad)).toBe(false)
      expect(sanitizeConfig({ dashboardBase: bad }).dashboardBase).toBe(
        DEFAULT_CONFIG.dashboardBase,
      )
    }
    for (const ok of ['http://127.0.0.1:8900', 'http://localhost:8900', 'http://[::1]:8900']) {
      expect(isLoopbackHttpBase(ok)).toBe(true)
    }
  })

  it('autoSave：默认开、30 分钟；0 = 关闭间隔；上限 24h', () => {
    expect(DEFAULT_CONFIG.autoSaveEnabled).toBe(true)
    expect(DEFAULT_CONFIG.autoSaveIntervalSec).toBe(30 * 60)
    expect(sanitizeConfig({}).autoSaveIntervalSec).toBe(30 * 60)
    expect(sanitizeConfig({ autoSaveEnabled: false }).autoSaveEnabled).toBe(false)
    expect(sanitizeConfig({ autoSaveEnabled: 'no' }).autoSaveEnabled).toBe(true)
    expect(sanitizeConfig({ autoSaveIntervalSec: 0 }).autoSaveIntervalSec).toBe(0)
    expect(sanitizeConfig({ autoSaveIntervalSec: 10 * 86_400 }).autoSaveIntervalSec).toBe(86_400)
  })

  it('数值夹取 + 布尔只认布尔', () => {
    const c = sanitizeConfig({
      shipIntervalSec: 0,
      autoClearDeferWarnSec: 9999,
      keepBytes: 0,
      confirmStopOnCreate: 'yes',
    })
    expect(c.shipIntervalSec).toBe(1)
    expect(c.autoClearDeferWarnSec).toBe(600)
    expect(c.keepBytes).toBe(1024)
    expect(c.confirmStopOnCreate).toBe(DEFAULT_CONFIG.confirmStopOnCreate)
  })
})

describe('自动保存设置：按页隔离', () => {
  const base = { enabled: true, intervalSec: 1800 }

  it('两个页面用不同的键；一页的改动读不到另一页', () => {
    const { runtime } = memoryRuntime()
    saveAutoSave(runtime, 'p1', { enabled: false, intervalSec: 120 })
    expect(autoSaveKeyFor('p1')).toBe('bnc:autoSave:p1')
    expect(autoSaveKeyFor('p1')).not.toBe(autoSaveKeyFor('p2'))
    expect(loadAutoSave(runtime, 'p1', base)).toEqual({ enabled: false, intervalSec: 120 })
    expect(loadAutoSave(runtime, 'p2', base)).toEqual(base)
  })

  it('apply 只改本页 config；坏字段回退，间隔夹到 0–24h', () => {
    const { runtime, data } = memoryRuntime()
    saveAutoSave(runtime, 'p1', { enabled: true, intervalSec: 10 * 86_400 })
    data.set(autoSaveKeyFor('p2'), { enabled: 'no', intervalSec: -5 })

    const a = { ...DEFAULT_CONFIG }
    const b = { ...DEFAULT_CONFIG }
    applyPageAutoSave(runtime, 'p1', a)
    applyPageAutoSave(runtime, 'p2', b)

    expect(a.autoSaveEnabled).toBe(true)
    expect(a.autoSaveIntervalSec).toBe(86_400)
    expect(b.autoSaveEnabled).toBe(DEFAULT_CONFIG.autoSaveEnabled)
    expect(b.autoSaveIntervalSec).toBe(0)
  })
})
