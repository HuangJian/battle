/** views-cloudflared-health.test.ts — cloudflared 黄点：隧道在 ≠ 链路可用。 */
import { describe, expect, it } from 'bun:test'
import { cloudflaredHealthy } from '../src/server/api/views'

describe('cloudflaredHealthy（2026-09-18 origin 不通 → 黄）', () => {
  it('hub 不通 → false（无论隧道 /ping 如何）', () => {
    expect(cloudflaredHealthy(false, true)).toBe(false)
    expect(cloudflaredHealthy(false, false)).toBe(false)
    expect(cloudflaredHealthy(false, null)).toBe(false)
  })
  it('hub 通：隧道探测优先；无隧道 URL 时以 hub 为准', () => {
    expect(cloudflaredHealthy(true, true)).toBe(true)
    expect(cloudflaredHealthy(true, false)).toBe(false)
    expect(cloudflaredHealthy(true, null)).toBe(true)
  })
  it('hub 未知：隧道探测优先，都未知则 null', () => {
    expect(cloudflaredHealthy(null, true)).toBe(true)
    expect(cloudflaredHealthy(null, false)).toBe(false)
    expect(cloudflaredHealthy(null, null)).toBeNull()
  })
})
