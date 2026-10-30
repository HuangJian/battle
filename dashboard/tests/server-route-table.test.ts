/**
 * server-route-table.test.ts — 原生路由表（`Bun.serve({ routes })`）的等价门禁（2026-10-25）。
 *
 * 换了实现就要能证明**语义没变**，否则「更快」只是把 404 藏起来。本文件钉三件事：
 *   ① 表本身的完整性：页面族全部在表里、方法逐条声明、静态在通配之前、同一 path 不重复；
 *   ② 「表里有 ⇒ handler 必有」是**启动期抛错**（漏接端点不再退化成静默 404）；
 *   ③ 用**同一张表**起一个桩服务器，实测 Bun 的匹配与优先级：
 *      静态 > 通配、方法不匹配落兜底、`/api/*` POST 是动作面、非规范路径交给 `/*`。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import path from 'path'
import { CONSOLE_ROUTES, buildRouteTable, type RouteHandlers } from '../src/server/route-table'
import { PAGES } from '../src/web/view/routes'

const DASHBOARD_ROOT = path.join(import.meta.dir, '..')

describe('路由表：完整性（与旧 if 链的路径/方法逐条对齐）', () => {
  it('页面族的每一条规范路径都在表里（GET）——新增页只加客户端、忘了服务端会在这里红', () => {
    const get = new Set(CONSOLE_ROUTES.filter((r) => r.methods.includes('GET')).map((r) => r.path))
    for (const meta of Object.values(PAGES)) {
      // `/log` 是**前缀页**（`/log/<key>`，表里是 `/log/*`），其余页面都有精确路径。
      if (meta.path === '/log') expect(get.has('/log/*')).toBe(true)
      else expect(get.has(meta.path)).toBe(true)
    }
    // `/console` 是 `/` 的历史别名（server.ts 一直两处都服务）。
    expect(get.has('/')).toBe(true)
    expect(get.has('/console')).toBe(true)
  })

  it('方法逐条声明：GET 面不得接受 POST，动作面只走 `/api/*` POST', () => {
    for (const r of CONSOLE_ROUTES) {
      if (
        r.path.startsWith('/api/') &&
        r.path !== '/api/deliverUpload' &&
        r.path !== '/api/notebookLog' &&
        r.path !== '/api/*'
      ) {
        expect(r.methods).toEqual(['GET'])
      }
    }
    expect(CONSOLE_ROUTES.find((r) => r.path === '/api/*')?.methods).toEqual(['POST'])
    expect(CONSOLE_ROUTES.find((r) => r.path === '/api/deliverUpload')?.methods).toEqual(['POST'])
    // 第二个 typed POST：notebook 日志搬运（清单端点仍是 GET）
    expect(CONSOLE_ROUTES.find((r) => r.path === '/api/notebookLog')?.methods).toEqual(['POST'])
    expect(CONSOLE_ROUTES.find((r) => r.path === '/api/notebookLogs')?.methods).toEqual(['GET'])
    // 兜底同时接两种方法（GET = 宽松页面/404；POST = 非 API 路径的 404）。
    expect(CONSOLE_ROUTES.find((r) => r.path === '/*')?.methods).toEqual(['GET', 'POST'])
  })

  it('顺序：静态在前、通配在后；同一 path 只登记一次', () => {
    const seen = new Set<string>()
    let sawWildcard = false
    for (const r of CONSOLE_ROUTES) {
      expect(seen.has(r.path)).toBe(false)
      seen.add(r.path)
      const wildcard = r.path.includes('*')
      if (wildcard) sawWildcard = true
      // 通配之后不允许再出现静态路径（表是给人读的；顺序错乱会掩盖优先级问题）
      else expect(sawWildcard).toBe(false)
    }
    // 最后一个必须是 `/*` 兜底
    expect(CONSOLE_ROUTES[CONSOLE_ROUTES.length - 1]?.path).toBe('/*')
  })

  it('buildRouteTable：表里有、handler 缺席 ⇒ 启动期抛（不再静默 404）', () => {
    const ok: RouteHandlers<() => Response> = Object.fromEntries(
      CONSOLE_ROUTES.map((r) => [
        r.path,
        { GET: () => new Response('g'), POST: () => new Response('p') },
      ]),
    )
    expect(Object.keys(buildRouteTable(CONSOLE_ROUTES, ok))).toHaveLength(CONSOLE_ROUTES.length)
    const missing = { ...ok }
    delete missing['/api/state']
    expect(() => buildRouteTable(CONSOLE_ROUTES, missing)).toThrow('/api/state')
    const partial = { ...ok, '/api/state': { POST: () => new Response('p') } }
    expect(() => buildRouteTable(CONSOLE_ROUTES, partial)).toThrow('/api/state GET')
  })

  it('接线：server.ts 用原生 routes，旧的 pathname 比较链已退场', () => {
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'server', 'server.ts'), 'utf-8')
    expect(src).toContain('routes: { ...buildRouteTable(CONSOLE_ROUTES, routeHandlers)')
    expect(src).toContain('fetch: guard(fallbackRoute)')
    // 旧的 `url.pathname === '...'` 判断链不得复活（每个端点都得回表里登记）。
    // 注：注释里提到这串字符不算——判据是「与字面量比较」。
    expect(src.match(/url\.pathname === ['"`]/g) ?? []).toHaveLength(0)
  })
})

describe('路由表：Bun 原生匹配（优先级 / 方法 / 兜底）', () => {
  /** 桩 handler：回显「自己是谁」，就能证明是**哪一条**路由接住的。 */
  const stubTable = (): Record<string, Partial<Record<'GET' | 'POST', () => Response>>> => {
    const handlers: RouteHandlers<() => Response> = Object.fromEntries(
      CONSOLE_ROUTES.map((r) => [
        r.path,
        {
          GET: () => new Response(`GET ${r.path}`),
          POST: () => new Response(`POST ${r.path}`),
        },
      ]),
    )
    return buildRouteTable(CONSOLE_ROUTES, handlers) as Record<
      string,
      Partial<Record<'GET' | 'POST', () => Response>>
    >
  }

  // Bun 的 Routes 是字面量键的映射；这里传的是「按同一张表生成」的结构，故收口到 Options。
  const serveOptions = {
    hostname: '127.0.0.1',
    port: 0,
    routes: stubTable(),
    fetch: () => new Response('FALLBACK'),
  } as unknown as Bun.Serve.Options<undefined>
  const server = Bun.serve(serveOptions)

  afterAll(() => void server.stop(true))

  const get = async (p: string): Promise<string> =>
    await (await fetch(`http://127.0.0.1:${server.port}${p}`)).text()
  const post = async (p: string): Promise<string> =>
    await (await fetch(`http://127.0.0.1:${server.port}${p}`, { method: 'POST' })).text()

  it('静态路径优先于通配：/api/state 不被 /api/* 或 /* 抢走', async () => {
    expect(await get('/api/state')).toBe('GET /api/state')
    expect(await get('/api/taskBundle')).toBe('GET /api/taskBundle')
    expect(await get('/')).toBe('GET /')
    expect(await get('/courses')).toBe('GET /courses')
  })

  it('通配按前缀归口：/api/log/<key> → /api/log/*；/log/<key> → /log/*', async () => {
    expect(await get('/api/log/trainingLoop')).toBe('GET /api/log/*')
    expect(await get('/log/trainingLoop')).toBe('GET /log/*')
  })

  it('动作面：POST /api/<act> → /api/*；静态 POST 端点优先', async () => {
    expect(await post('/api/nodeSmoke')).toBe('POST /api/*')
    expect(await post('/api/deliverUpload')).toBe('POST /api/deliverUpload')
    expect(await post('/api/notebookLog')).toBe('POST /api/notebookLog')
    expect(await get('/api/notebookLogs')).toBe('GET /api/notebookLogs')
  })

  it('方法不匹配 ⇒ 落兜底（不是 405/静默成功）：POST 页面 / GET 动作端点', async () => {
    expect(await post('/metrics')).toBe('POST /*')
    expect(await get('/api/deliverUpload')).toBe('GET /*')
  })

  it('非规范路径由 /* 接住（server.ts 在那里做 pageForPath 归一化）', async () => {
    expect(await get('/Metrics')).toBe('GET /*')
    expect(await get('/metrics/')).toBe('GET /*')
  })

  it('HEAD 与 GET 同路由（Bun 原生语义；旧 if 链对 HEAD 一律 404）——但无响应体', async () => {
    const r = await fetch(`http://127.0.0.1:${server.port}/api/state`, { method: 'HEAD' })
    expect(r.status).toBe(200)
    expect(await r.text()).toBe('')
  })

  it('表外路径仍落 `/*`（兜底不是 404 短路，而是 server.ts 的「宽松页面解析 / 404」）', async () => {
    // bundles（/app.js 等）是 server.ts 按构建产物**动态注册**的静态路由，不在契约表里；
    // 这里只证明「表外的 GET 会被 `/*` 接住」——最外层的 `fetch` 只是协议形态兜底。
    expect(await get('/app.js')).toBe('GET /*')
  })
})
