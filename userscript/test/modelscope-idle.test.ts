/**
 * modelscope-idle.test.ts — 魔搭「静默提醒」自动点确定（2026-11-10 用户要求）。
 *
 * 判据只认这一个弹窗（标题 + 两段正文），点之前随机等几十秒；本套件用注入的排程器
 * 把「延迟」变成可控时钟，逐条钉住：不立即点、延迟落在 20–60s、别的弹窗不碰、
 * 弹窗消失/已 dispose 都不点、同一弹窗只排一次。
 */

import { afterEach, describe, expect, it } from 'bun:test'
import { loadDom, type DomHandle } from '../happydom'
import {
  IDLE_DELAY_MAX_MS,
  IDLE_DELAY_MIN_MS,
  installModelscopeIdleDismiss,
} from '../src/notebook-companion/app/modelscope-idle'

const HREF = 'https://modelscope.cn/code/workspace'

const dialog = (title: string, content: string): string => `
<div class="antd5-modal-content"><div class="antd5-modal-body">
  <div class="antd5-modal-confirm-body-wrapper">
    <div class="antd5-modal-confirm-body antd5-modal-confirm-body-has-title">
      <span role="img" aria-label="exclamation-circle" class="anticon anticon-exclamation-circle"></span>
      <div class="antd5-modal-confirm-paragraph">
        <span class="antd5-modal-confirm-title">${title}</span>
        <div class="antd5-modal-confirm-content">${content}</div>
      </div>
    </div>
    <div class="antd5-modal-confirm-btns">
      <button type="button" class="antd5-btn antd5-btn-primary"><span>确 定</span></button>
    </div>
  </div>
</div></div>`

/** 真实文案（用户 2026-11-10 贴的 DOM）。 */
const IDLE = dialog(
  '静默提醒',
  '您已超过30分钟未在 notebook 内执行操作，静默超过1小时会自动关闭实例，请及时处理重要文件',
)

const opened: DomHandle[] = []
afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

function dom(html: string): DomHandle {
  const w = loadDom(html, HREF)
  opened.push(w)
  return w
}

/** 可控排程：记录 (fn, ms)，手动 flush。 */
function clock(): {
  timers: { fn: () => void; ms: number }[]
  schedule: (fn: () => void, ms: number) => void
} {
  const timers: { fn: () => void; ms: number }[] = []
  return { timers, schedule: (fn, ms) => timers.push({ fn, ms }) }
}

function clicks(w: DomHandle): { count: number } {
  const state = { count: 0 }
  for (const b of w.document.querySelectorAll('button')) {
    b.addEventListener('click', () => {
      state.count += 1
    })
  }
  return state
}

describe('installModelscopeIdleDismiss：只认「静默提醒」', () => {
  it('不立即点：先排一个 20–60s 的定时器；到点才点确定', () => {
    const w = dom(IDLE)
    const c = clock()
    const clicked = clicks(w)
    const dismissed: number[] = []
    installModelscopeIdleDismiss(w.document as unknown as Document, {
      schedule: c.schedule,
      onDismiss: (ms) => dismissed.push(ms),
    })
    // 刚出现时一下都不点（保留「人还在」的自然延迟）
    expect(clicked.count).toBe(0)
    expect(c.timers.length).toBe(1)
    const wait = c.timers[0]!.ms
    expect(wait).toBeGreaterThanOrEqual(IDLE_DELAY_MIN_MS)
    expect(wait).toBeLessThanOrEqual(IDLE_DELAY_MAX_MS)
    // 到点：点了，且报出实际延迟
    c.timers[0]!.fn()
    expect(clicked.count).toBe(1)
    expect(dismissed).toEqual([wait])
  })

  it('标题对但正文不对 ⇒ 不碰（同标题可能被别的场景复用）', () => {
    const w = dom(dialog('静默提醒', '这是一条普通提示'))
    const c = clock()
    installModelscopeIdleDismiss(w.document as unknown as Document, { schedule: c.schedule })
    expect(c.timers.length).toBe(0)
  })

  it('别的弹窗（标题不同）⇒ 不碰', () => {
    const w = dom(
      dialog('提示', '您已超过30分钟未在 notebook 内执行操作，静默超过1小时会自动关闭实例'),
    )
    const c = clock()
    installModelscopeIdleDismiss(w.document as unknown as Document, { schedule: c.schedule })
    expect(c.timers.length).toBe(0)
  })

  it('同一弹窗只排一次（DOM 重渲染/反复 scan 不会点两次）', () => {
    const w = dom(IDLE)
    const c = clock()
    const clicked = clicks(w)
    const d = installModelscopeIdleDismiss(w.document as unknown as Document, {
      schedule: c.schedule,
    })
    d.scan()
    d.scan()
    expect(c.timers.length).toBe(1)
    c.timers[0]!.fn()
    expect(clicked.count).toBe(1)
  })

  it('到点时弹窗已经消失（人自己关了）⇒ 不点', () => {
    const w = dom(IDLE)
    const c = clock()
    const clicked = clicks(w)
    installModelscopeIdleDismiss(w.document as unknown as Document, { schedule: c.schedule })
    w.document.querySelector('.antd5-modal-content')!.remove()
    c.timers[0]!.fn()
    expect(clicked.count).toBe(0)
  })

  it('dispose 之后挂起的排程失效', () => {
    const w = dom(IDLE)
    const c = clock()
    const clicked = clicks(w)
    const d = installModelscopeIdleDismiss(w.document as unknown as Document, {
      schedule: c.schedule,
    })
    d.dispose()
    c.timers[0]!.fn()
    expect(clicked.count).toBe(0)
  })

  it('装好之前就堆在页面上的弹窗也会被扫到', () => {
    const w = dom(IDLE)
    const c = clock()
    installModelscopeIdleDismiss(w.document as unknown as Document, { schedule: c.schedule })
    expect(c.timers.length).toBe(1)
  })
})
