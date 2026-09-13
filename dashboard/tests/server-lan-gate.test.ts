/**
 * server-lan-gate.test.ts — 回环判定、LAN 的 POST 必拒（403）与 GET 放行、服务端确实接了门控（fail closed）
 *
 * 分层：src/server 的 LAN 门控（isLoopbackAddress / isReadonlyAction / server.ts 接线）
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'

describe('console 局域网只读边界（§…：LAN 查看 / localhost 控制）', () => {
  it('isLoopbackAddress：回环 IPv4/IPv6/mapped 为真，局域网地址与未知为假（fail closed）', async () => {
    const net = await import('../src/core/net')
    expect(net.isLoopbackAddress('127.0.0.1')).toBe(true)
    expect(net.isLoopbackAddress('::1')).toBe(true)
    expect(net.isLoopbackAddress('0:0:0:0:0:0:0:1')).toBe(true)
    expect(net.isLoopbackAddress('::ffff:127.0.0.1')).toBe(true)
    expect(net.isLoopbackAddress('192.168.1.23')).toBe(false)
    expect(net.isLoopbackAddress('10.0.0.5')).toBe(false)
    expect(net.isLoopbackAddress(null)).toBe(false)
    expect(net.isLoopbackAddress(undefined)).toBe(false)
  })

  it('isReadonlyAction：LAN 的 POST 必拒（→403），回环 POST 放行，GET 一律放行', async () => {
    const net = await import('../src/core/net')
    // 写动作（POST）：仅回环放行
    expect(net.isReadonlyAction('POST', '127.0.0.1')).toBe(false)
    expect(net.isReadonlyAction('POST', '::1')).toBe(false)
    expect(net.isReadonlyAction('POST', '::ffff:127.0.0.1')).toBe(false)
    expect(net.isReadonlyAction('POST', '192.168.1.23')).toBe(true)
    expect(net.isReadonlyAction('POST', '10.0.0.5')).toBe(true)
    // fail closed：来源不可得 = 非回环 → 拒
    expect(net.isReadonlyAction('POST', null)).toBe(true)
    expect(net.isReadonlyAction('POST', undefined)).toBe(true)
    // 查看（GET 等）：局域网同权
    expect(net.isReadonlyAction('GET', '192.168.1.23')).toBe(false)
    expect(net.isReadonlyAction('GET', null)).toBe(false)
    expect(net.isReadonlyAction('HEAD', '10.0.0.5')).toBe(false)
  })

  it('服务端确实接了这个门控（回归：删掉门控/改成放行 LAN 会被这条抓住）', () => {
    // 纯函数对不等于接线对——真正要守的是「server.ts 的 fetch 首行用它判 403」。
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'server', 'server.ts'), 'utf-8')
    expect(src).toContain('isReadonlyAction(req.method')
    expect(src).toMatch(/只读模式：动作仅限本机/) // 403 文案就在门控分支里
    expect(src).toMatch(/\},\s*403\b/) // 拒绝响应状态码
  })
})
