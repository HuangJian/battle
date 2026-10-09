/**
 * web-app-nodestats.test.ts — 抽屉「节点统计」头部的**本机磁盘徽标**（plan/self-node-disk-alert G6）。
 *
 * 分层：`src/web/app/panels/NodeStats.tsx`（渲染）+ `src/web/view/console-types.ts::selfDiskBadge`
 * （口径，纯函数）。
 *
 * 为什么只渲染 `SelfDiskBadge` 而不是整块 `NodeStats`：面板自己 `fetch('/api/pool')`（useEffect），
 * 而本仓 web 用例全是 SSR（`preact-render-to-string` 不跑 effect）、无 DOM 夹具 ⇒ 拿不到带数据的
 * 整块渲染。这里钉住的是**徽标口径**（MB 怎么显示、档位何时出现、旧 agent 怎么降级）；
 * 「NodeStats 里到底有没有挂这一行」代码审查兜底——不假称有接线用例（plan §11-F4）。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { SelfDiskBadge } from '../src/web/app/panels/NodeStats'
import { selfDiskBadge, type SelfStatus } from '../src/web/view'

const st = (over: Partial<SelfStatus> = {}): SelfStatus => ({
  workers: 8,
  inflight: 3,
  gamesDoneTotal: 1234,
  diskFreeMB: 1212,
  diskLevel: 'ok',
  diskWarnMB: 4096,
  diskFloorMB: 2048,
  lastError: null,
  uptimeSec: 10,
  resultCacheItems: 0,
  resultCacheBytes: 0,
  recentFailed: 0,
  ...over,
})

describe('selfDiskBadge 口径（纯函数）', () => {
  it('ok ⇒ 只显 MB（正常盘位不喊狼）', () => {
    expect(selfDiskBadge({ freeMB: 1212, level: 'ok' })).toEqual({
      text: 'disk 1212MB',
      tone: 'a',
    })
  })

  it('warn / critical ⇒ 带档位字样与色（warn=y / critical=r）', () => {
    expect(selfDiskBadge({ freeMB: 1212, level: 'warn' })).toEqual({
      text: 'disk 1212MB（warn）',
      tone: 'y',
    })
    expect(selfDiskBadge({ freeMB: 900, level: 'critical' })).toEqual({
      text: 'disk 900MB（critical）',
      tone: 'r',
    })
  })

  it('level=null（旧 agent 没报档位）⇒ 只显 MB：不编档位，也不谎报 ok 字样', () => {
    expect(selfDiskBadge({ freeMB: 1212, level: null })?.text).toBe('disk 1212MB')
  })

  it('没有盘位事实 ⇒ null（不画、不占位）', () => {
    expect(selfDiskBadge(null)).toBeNull()
  })
})

describe('SelfDiskBadge 渲染（SSR）', () => {
  const html = (s: SelfStatus | null): string => renderToString(h(SelfDiskBadge, { st: s }))

  it('warn ⇒ tc-badge--y + 「disk 1212MB（warn）」', () => {
    const out = html(st({ diskLevel: 'warn' }))
    expect(out).toContain('tc-badge--y')
    expect(out).toContain('disk 1212MB（warn）')
  })

  it('ok ⇒ tc-badge--a 且**不带**档位字样；critical ⇒ tc-badge--r', () => {
    const ok = html(st({ diskLevel: 'ok' }))
    expect(ok).toContain('tc-badge--a')
    expect(ok).not.toContain('（ok）')
    expect(html(st({ diskLevel: 'critical', diskFreeMB: 900 }))).toContain('tc-badge--r')
  })

  it('旧 agent（无档位、只有 MB）⇒ 照样显 MB（降级，不缺失）', () => {
    const out = html(st({ diskLevel: null }))
    expect(out).toContain('disk 1212MB')
    expect(out).not.toContain('（warn）')
  })

  it('agent 未启动（selfStatus=null）或没有盘位读数 ⇒ 什么都不渲染', () => {
    expect(html(null)).toBe('')
    expect(html(st({ diskFreeMB: null }))).toBe('')
  })
})
