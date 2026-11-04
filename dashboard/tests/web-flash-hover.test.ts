/**
 * web-flash-hover.test.ts — 右下角动作结果浮层：鼠标停在上面时 8s 自动隐藏停表。
 *
 * 分层：src/web/components/Flash.tsx
 *
 * 为什么是「SSR 形状 + 源码锚点」两条腿：本仓 web 用例全是 SSR（无 happy-dom/jsdom），
 * `preact-render-to-string` **丢弃全部事件处理器、也不跑 effect**（README「测试分层」）——
 * 悬停与定时器在渲染出的 HTML 里一个字都看不到。能钉住的只有：
 *   ① 形状（浮层仍是 role=status + 手动 ✕，ok/bad 配色没被改动）；
 *   ② 接线（两个 hover 处理器把暂停态置真/置假，计时 effect 以它为门）——读源码。
 * 真交互（悬停 10s 不消失）仍需人眼在浏览器里点一次：本仓没有 DOM 夹具。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { Flash } from '../src/web/components/Flash'

const src = readFileSync('src/web/components/Flash.tsx', 'utf8')

describe('Flash · 悬停暂停自动隐藏', () => {
  it('浮层形状不变：role=status + ok/bad 配色 + 手动关闭键', () => {
    const bad = renderToString(
      h(Flash, { flash: { ok: false, message: 'hub 不通' }, onHide: () => {} }),
    )
    expect(bad).toContain('tc-flash--show')
    expect(bad).toContain('tc-flash--bad')
    expect(bad).toContain('role="status"')
    expect(bad).toContain('❌ hub 不通')
    expect(bad).toContain('aria-label="关闭"')

    const ok = renderToString(
      h(Flash, { flash: { ok: true, message: '已停止' }, onHide: () => {} }),
    )
    expect(ok).toContain('tc-flash--ok')
    expect(ok).toContain('✅ 已停止')
  })

  it('悬停进入暂停、离开恢复：暂停态挂在浮层容器上，计时 effect 以它为门', () => {
    // 处理器挂在容器（不是 ✕ 键）上：鼠标停在浮层任意处都不该被抽走。
    expect(src).toContain('onMouseEnter={() => setPaused(true)}')
    expect(src).toContain('onMouseLeave={() => setPaused(false)}')
    // 悬停期间连 timer 都不建（不是建了再取消）。
    expect(src).toContain('if (!flash || paused) return')
    expect(src).toContain('setTimeout(onHide, FLASH_MS)')
    expect(src).toContain('clearTimeout(t)')
  })

  it('浮层消失时暂停态归零：指针留在原地没有 mouseleave，不清会让下一条永远暂停', () => {
    expect(src).toContain('if (!flash) setPaused(false)')
  })
})
