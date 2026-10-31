import { describe, expect, it } from 'bun:test'
import {
  DEFAULT_CONFIG,
  isLoopbackHttpBase,
  sanitizeConfig,
} from '../src/notebook-companion/config'

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
