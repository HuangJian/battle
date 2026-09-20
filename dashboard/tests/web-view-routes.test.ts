/**
 * web-view-routes.test.ts — 控制台路由纯函数（src/web/view/routes.ts）
 *
 * 分层：src/web/view/routes.ts（服务端 SSR 与浏览器 pushState 共用同一份路由表）
 *
 * 依据 docs/dashboard-redesign.md §3.2 路由表 / §5.1 导航与 URL。
 * 本文件只测纯函数（无 DOM、无环境夹具）：路由是「首屏 SSR 与客户端 hydrate 必须一致」
 * 的那一处真相，漂移的症状最难在别处被发现（直接输 URL / 刷新 / 前进后退各看到一页）。
 */

import { describe, expect, it } from 'bun:test'
import {
  bootstrapPage,
  canonicalPath,
  DEFAULT_LOG_COMPONENT,
  DEFAULT_PAGE,
  isNavActive,
  NAV_GROUPS,
  NAV_ITEMS,
  normalizePath,
  PAGES,
  pageForPath,
  withCourse,
  type PageKey,
} from '../src/web/view'

const ALL_PAGES: PageKey[] = ['overview', 'metrics', 'nodes', 'wire']

describe('pageForPath — 路径 → 页面键', () => {
  it('/ 与历史别名 /console 都归到总览', () => {
    expect(pageForPath('/')).toBe('overview')
    expect(pageForPath('/console')).toBe('overview')
    expect(pageForPath('')).toBe('overview')
  })

  it('四个页面键各自可解析', () => {
    expect(pageForPath('/metrics')).toBe('metrics')
    expect(pageForPath('/nodes')).toBe('nodes')
    expect(pageForPath('/wire')).toBe('wire')
  })

  it('容忍尾斜杠 / 重复斜杠 / 大小写（手工输入不 404）', () => {
    expect(pageForPath('/metrics/')).toBe('metrics')
    expect(pageForPath('/Metrics')).toBe('metrics')
    expect(pageForPath('//nodes//')).toBe('nodes')
    expect(pageForPath('/WIRE')).toBe('wire')
  })

  it('本 bundle 之外的路径一律 null（不吞独立页）', () => {
    expect(pageForPath('/eval')).toBe(null)
    expect(pageForPath(`/log/${DEFAULT_LOG_COMPONENT}`)).toBe(null)
    expect(pageForPath('/log')).toBe(null)
    expect(pageForPath('/api/state')).toBe(null)
    expect(pageForPath('/nope')).toBe(null)
  })
})

describe('normalizePath — 路径归一化', () => {
  it('空与纯斜杠都归到根', () => {
    expect(normalizePath('')).toBe('/')
    expect(normalizePath('/')).toBe('/')
    expect(normalizePath('///')).toBe('/')
  })

  it('去尾斜杠 + 折叠重复 + 小写', () => {
    expect(normalizePath('/metrics//')).toBe('/metrics')
    expect(normalizePath('//Nodes/')).toBe('/nodes')
    expect(normalizePath('/a/b/')).toBe('/a/b')
  })
})

describe('canonicalPath — 页面键 → 规范路径（往返一致）', () => {
  it('每个页面键的规范路径都能解析回它自己', () => {
    for (const k of ALL_PAGES) {
      expect(pageForPath(canonicalPath(k))).toBe(k)
    }
  })

  it('规范路径与 PAGES 元信息同源（不允许两处各写一份）', () => {
    for (const k of ALL_PAGES) expect(canonicalPath(k)).toBe(PAGES[k].path)
    expect(canonicalPath(DEFAULT_PAGE)).toBe('/')
  })

  it('每页都有非空标题与「回答什么问题」的描述', () => {
    for (const k of ALL_PAGES) {
      expect(PAGES[k].title.length).toBeGreaterThan(0)
      expect(PAGES[k].desc.length).toBeGreaterThan(0)
      expect(PAGES[k].key).toBe(k)
    }
  })
})

describe('withCourse — 内部链接保留查看课程', () => {
  it('无课程时原样返回（不产生空 ?course=）', () => {
    expect(withCourse('/metrics', '')).toBe('/metrics')
    expect(withCourse('/metrics', null)).toBe('/metrics')
    expect(withCourse('/metrics', undefined)).toBe('/metrics')
  })

  it('无查询串时用 ? 拼接', () => {
    expect(withCourse('/metrics', 'c6-chip')).toBe('/metrics?course=c6-chip')
  })

  it('已有查询串时用 & 拼接（不覆盖既有参数）', () => {
    expect(withCourse('/eval?x=1', 'c6')).toBe('/eval?x=1&course=c6')
  })

  it('课程名做 URL 编码（课程名可能含 . 与其它字符）', () => {
    expect(withCourse('/nodes', 'x20-rebirth')).toBe('/nodes?course=x20-rebirth')
    expect(withCourse('/nodes', 'a b')).toBe('/nodes?course=a%20b')
  })
})

describe('isNavActive — 导航激活判定', () => {
  const item = (id: string) => {
    const found = NAV_ITEMS.find((n) => n.id === id)
    if (!found) throw new Error(`NAV_ITEMS 缺少 ${id}`)
    return found
  }

  it('route 项按页面键点亮（含尾斜杠与大小写）', () => {
    expect(isNavActive(item('metrics'), '/metrics')).toBe(true)
    expect(isNavActive(item('metrics'), '/metrics/')).toBe(true)
    expect(isNavActive(item('metrics'), '/Metrics')).toBe(true)
    expect(isNavActive(item('overview'), '/')).toBe(true)
    expect(isNavActive(item('overview'), '/console')).toBe(true)
  })

  it('route 项不被别页点亮（同组不互相污染）', () => {
    expect(isNavActive(item('overview'), '/metrics')).toBe(false)
    expect(isNavActive(item('nodes'), '/wire')).toBe(false)
    expect(isNavActive(item('wire'), '/nodes')).toBe(false)
  })

  it('日志项按 /log/<key> 前缀点亮，但 /eval 不点亮它', () => {
    expect(isNavActive(item('log'), `/log/${DEFAULT_LOG_COMPONENT}`)).toBe(true)
    expect(isNavActive(item('log'), '/log/hubServer')).toBe(true)
    expect(isNavActive(item('log'), '/log')).toBe(true)
    expect(isNavActive(item('log'), '/eval')).toBe(false)
    expect(isNavActive(item('log'), '/')).toBe(false)
  })

  it('评估项只点亮 /eval', () => {
    expect(isNavActive(item('eval'), '/eval')).toBe(true)
    expect(isNavActive(item('eval'), `/log/${DEFAULT_LOG_COMPONENT}`)).toBe(false)
  })
})

describe('bootstrapPage — 首屏引导载荷的页面判定', () => {
  it('服务端 stamp 的 page 优先（首帧不依赖 location）', () => {
    expect(bootstrapPage({ page: 'nodes' }, '/')).toBe('nodes')
    expect(bootstrapPage({ page: 'wire' }, '/metrics')).toBe('wire')
  })

  it('page 缺失时回退当前 URL 判定', () => {
    expect(bootstrapPage(null, '/metrics')).toBe('metrics')
    expect(bootstrapPage(undefined, '/nodes/')).toBe('nodes')
    expect(bootstrapPage({}, '/wire')).toBe('wire')
  })

  it('page 非法（注入/旧版本）不信任，按 URL 判定', () => {
    expect(bootstrapPage({ page: 'evil' }, '/metrics')).toBe('metrics')
    expect(bootstrapPage({ page: 42 }, '/nodes')).toBe('nodes')
    expect(bootstrapPage({ page: null }, '/wire')).toBe('wire')
  })

  it('既无 page 也不是本 bundle 内的 URL → 兜底总览（绝不返回 undefined）', () => {
    expect(bootstrapPage(null, '/eval')).toBe(DEFAULT_PAGE)
    expect(bootstrapPage(null, '/log/trainingLoop')).toBe(DEFAULT_PAGE)
    expect(bootstrapPage(null, '/')).toBe(DEFAULT_PAGE)
  })
})

describe('NAV_ITEMS / NAV_GROUPS 结构约束（防路由表漂移）', () => {
  it('id 唯一，icon/label/title 非空', () => {
    const ids = NAV_ITEMS.map((n) => n.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const n of NAV_ITEMS) {
      expect(n.label.length).toBeGreaterThan(0)
      expect(n.title.length).toBeGreaterThan(0)
      expect(n.icon.length).toBeGreaterThan(0)
    }
  })

  it('route 项的 href 必须等于其页面键的规范路径（两处不许各写一份）', () => {
    for (const n of NAV_ITEMS.filter((x) => x.kind === 'route')) {
      expect(n.page).toBeDefined()
      expect(n.href).toBe(PAGES[n.page as PageKey].path)
    }
  })

  it('link 项不带 page（独立页不属于本 bundle 的路由表）', () => {
    for (const n of NAV_ITEMS.filter((x) => x.kind === 'link')) {
      expect(n.page).toBeUndefined()
      // /eval 与 /log/<key> 都必须真的在服务端有路由（server.ts）。
      expect(n.href.startsWith('/eval') || n.href.startsWith('/log/')).toBe(true)
    }
  })

  it('每个导航项的 group 都在 NAV_GROUPS 里，且每个组都非空', () => {
    const groupIds = new Set(NAV_GROUPS.map((g) => g.id))
    for (const n of NAV_ITEMS) expect(groupIds.has(n.group)).toBe(true)
    for (const g of NAV_GROUPS) {
      expect(NAV_ITEMS.filter((n) => n.group === g.id).length).toBeGreaterThan(0)
    }
  })

  it('四个页面键都在导航里可达（没有只能靠手输 URL 到达的页）', () => {
    const routed = new Set(
      NAV_ITEMS.filter((n) => n.kind === 'route').map((n) => n.page as PageKey),
    )
    for (const k of ALL_PAGES) expect(routed.has(k)).toBe(true)
  })
})
