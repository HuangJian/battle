import { afterEach, describe, expect, it } from 'bun:test'
import { loadDom, type DomHandle } from '../happydom'
import type { Runtime } from '../src/runtime'
import {
  findQuotaText,
  fetchQuotaUsage,
  mountQuotaBanner,
  parseQuota,
  parseQuotaApi,
  parseQuotaPosition,
  QUOTA_API_URL,
  QUOTA_HOST_ID,
  QUOTA_POS_KEY,
} from '../src/notebook-companion/ui/quota-banner'

const opened: DomHandle[] = []
function dom(html: string): DomHandle {
  const w = loadDom(html)
  opened.push(w)
  return w
}
afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

const QUOTA_HTML =
  '<html><body><button><span>Quota:</span><span>09:20 / 20 hrs</span></button></body></html>'

function runtimeWith(mem: Map<string, unknown> = new Map(), responseText = ''): Runtime {
  return {
    gmRequest: async () => ({ status: 200, statusText: 'OK', responseText, responseHeaders: '' }),
    getValue: <T>(key: string, fallback: T): T => (mem.has(key) ? (mem.get(key) as T) : fallback),
    setValue: (key, value) => mem.set(key, value),
    deleteValue: (key) => mem.delete(key),
    registerMenuCommand: () => {},
    setClipboard: () => {},
  }
}

const shadowCard = (w: DomHandle): HTMLElement | null =>
  (w.document.getElementById(QUOTA_HOST_ID) as HTMLElement | null)?.shadowRoot?.querySelector(
    '.bq',
  ) as HTMLElement | null

function pointer(w: DomHandle, type: string, x: number, y: number, target: EventTarget): void {
  const Ctor = (w as unknown as { PointerEvent?: new (t: string, i: unknown) => Event })
    .PointerEvent
  const init = { clientX: x, clientY: y, button: 0, bubbles: true, cancelable: true, pointerId: 1 }
  const ev: Event = Ctor ? new Ctor(type, init) : (new w.MouseEvent(type) as unknown as Event)
  if (!Ctor) {
    for (const [key, value] of Object.entries(init)) {
      Object.defineProperty(ev, key, { value, configurable: true })
    }
  }
  target.dispatchEvent(ev)
}

describe('parseQuota', () => {
  it('解析页面真实文本', () => {
    const usage = parseQuota('Quota:09:20 / 20 hrs')!
    expect(usage.usedLabel).toBe('09:20')
    expect(usage.totalLabel).toBe('20 hrs')
    expect(usage.percent).toBe(47)
    expect(usage.remainingLabel).toBe('10:40')
    expect(usage.tier).toBe('ok')
  })

  it('50% 进 warn、80% 进 danger', () => {
    expect(parseQuota('Quota:10:00 / 20 hrs')!.tier).toBe('warn')
    expect(parseQuota('Quota:16:00/20hrs')!.tier).toBe('danger')
  })

  it('超额截断 100%，无匹配返回 null', () => {
    expect(parseQuota('Quota:25:00 / 20 hrs')!.percent).toBe(100)
    expect(parseQuota('Quota: unknown')).toBeNull()
  })
})

const QUOTA_API_JSON = JSON.stringify({
  quotaRefreshTime: '2026-11-28T00:00:00Z',
  tpuQuota: {
    hasEverRun: true,
    timeReserved: '0s',
    timeUsed: '33632.099314900s',
    totalTimeAllowed: '72000s',
  },
  gpuQuota: {
    hasEverRun: true,
    timeReserved: '0s',
    timeUsed: '31468.330738800s',
    totalTimeAllowed: '108000s',
  },
})

describe('parseQuotaApi / fetchQuotaUsage', () => {
  it('解析真实 API 响应，GPU / TPU 各自一行', () => {
    const pools = parseQuotaApi(JSON.parse(QUOTA_API_JSON))!
    // (108000 - 31468.33) / 60 = 1275.52 分 = 21:15
    expect(pools.gpu!.totalLabel).toBe('30 hrs')
    expect(pools.gpu!.remainingLabel).toBe('21:15')
    expect(pools.gpu!.percent).toBe(29)
    expect(pools.gpu!.tier).toBe('ok')
    // (72000 - 33632.1) / 60 = 639.46 分 = 10:39
    expect(pools.tpu!.totalLabel).toBe('20 hrs')
    expect(pools.tpu!.remainingLabel).toBe('10:39')
  })

  it('只有 TPU 数据时 gpu 为 null，不影响 tpu', () => {
    const onlyTpu = {
      tpuQuota: { timeUsed: '36000s', timeReserved: '0s', totalTimeAllowed: '72000s' },
    }
    const pools = parseQuotaApi(onlyTpu)!
    expect(pools.gpu).toBeNull()
    expect(pools.tpu!.totalLabel).toBe('20 hrs')
    expect(pools.tpu!.remainingLabel).toBe('10:00')
    expect(pools.tpu!.tier).toBe('warn')
  })

  it('reserved 计入已用（剩余 = total - used - reserved）', () => {
    const withReserved = {
      gpuQuota: { timeUsed: '0s', timeReserved: '3600s', totalTimeAllowed: '36000s' },
    }
    const pools = parseQuotaApi(withReserved)!
    expect(pools.gpu!.remainingLabel).toBe('9:00')
    expect(pools.tpu).toBeNull()
  })

  it('非 200 / 坏 JSON / 非法对象都返回 null，不抛', async () => {
    const failing: Runtime = {
      ...runtimeWith(),
      gmRequest: async () => ({
        status: 500,
        statusText: 'ERR',
        responseText: '',
        responseHeaders: '',
      }),
    }
    expect(parseQuotaApi(null)).toBeNull()
    expect(parseQuotaApi({ gpuQuota: {} })).toBeNull()
    expect(parseQuotaApi({ gpuQuota: { timeUsed: 'x', totalTimeAllowed: '108000s' } })).toBeNull()
    expect(await fetchQuotaUsage(failing)).toBeNull()
    expect(QUOTA_API_URL).toContain('GetAcceleratorQuotaStatistics')
  })

  it('gmRequest 成功时拿到 usage', async () => {
    const ok = runtimeWith(new Map(), QUOTA_API_JSON)
    const pools = await fetchQuotaUsage(ok)
    expect(pools!.gpu!.totalLabel).toBe('30 hrs')
    expect(pools!.gpu!.remainingLabel).toBe('21:15')
    expect(pools!.tpu!.totalLabel).toBe('20 hrs')
  })
})

describe('findQuotaText', () => {
  it('只读 Quota 标签父节点，不扫 body 全文', () => {
    const w = dom(QUOTA_HTML)
    expect(findQuotaText(w.document as unknown as Document)).toBe('Quota:09:20 / 20 hrs')
  })

  it('只有标签没有数值时返回空', () => {
    const w = dom('<html><body><div><span>Quota:</span><span>Starting</span></div></body></html>')
    expect(parseQuota(findQuotaText(w.document as unknown as Document))).toBeNull()
  })
})

describe('mountQuotaBanner', () => {
  it('加载完即显示横幅', () => {
    const w = dom(QUOTA_HTML)
    mountQuotaBanner(w.document as unknown as Document, runtimeWith(), w as unknown as Window, 0)
    const card = shadowCard(w)!
    expect(card).not.toBeNull()
    expect(card.hidden).toBe(false)
    expect(card.textContent).toBe('本周配额 剩余 10:40 / 20 hrs')
    expect(card.querySelector('.row span')!.className).toContain('ok')
  })

  it('数值后出现时轮询能显示', () => {
    const html =
      '<html><body><div id="q"><span>Quota:</span><span>Starting</span></div></body></html>'
    const w = dom(html)
    const banner = mountQuotaBanner(
      w.document as unknown as Document,
      runtimeWith(),
      w as unknown as Window,
      0,
    )
    expect(shadowCard(w)!.hidden).toBe(true)
    w.document.getElementById('q')!.innerHTML = '<span>Quota:</span><span>03:00 / 20 hrs</span>'
    expect(banner.sync()).toBe(true)
    expect(shadowCard(w)!.hidden).toBe(false)
    expect(shadowCard(w)!.textContent).toBe('本周配额 剩余 17:00 / 20 hrs')
  })

  it('API 数据优先：启动拉取后 DOM 文本不再覆盖', async () => {
    const w = dom(QUOTA_HTML)
    const banner = mountQuotaBanner(
      w.document as unknown as Document,
      runtimeWith(new Map(), QUOTA_API_JSON),
      w as unknown as Window,
      0,
    )
    await new Promise((r) => setTimeout(r, 0))
    const rows = shadowCard(w)!.querySelectorAll('.row')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.textContent).toBe('GPU 剩余 21:15 / 30 hrs')
    expect(rows[1]!.textContent).toBe('TPU 剩余 10:39 / 20 hrs')

    // 点开 Session options 后 DOM 出现另一套数值；API 已到位，不应被覆盖
    w.document.querySelector('button')!.innerHTML = '<span>Quota:</span><span>01:00 / 20 hrs</span>'
    banner.sync()
    expect(shadowCard(w)!.querySelectorAll('.row')).toHaveLength(2)
    expect(shadowCard(w)!.querySelectorAll('.row')[0]!.textContent).toBe('GPU 剩余 21:15 / 30 hrs')
  })

  it('API 只有 GPU 时只显示一行', async () => {
    const w = dom(QUOTA_HTML)
    const json = JSON.stringify({
      gpuQuota: { timeUsed: '0s', timeReserved: '0s', totalTimeAllowed: '108000s' },
    })
    mountQuotaBanner(
      w.document as unknown as Document,
      runtimeWith(new Map(), json),
      w as unknown as Window,
      0,
    )
    await new Promise((r) => setTimeout(r, 0))
    const rows = shadowCard(w)!.querySelectorAll('.row')
    expect(rows).toHaveLength(1)
    expect(rows[0]!.textContent).toBe('GPU 剩余 30:00 / 30 hrs')
  })

  it('位置存 GM，重挂后恢复', () => {
    const mem = new Map<string, unknown>([[QUOTA_POS_KEY, { left: 321, top: 123 }]])
    const w = dom(QUOTA_HTML)
    const b1 = mountQuotaBanner(
      w.document as unknown as Document,
      runtimeWith(mem),
      w as unknown as Window,
      0,
    )
    const card = shadowCard(w)!
    expect(card.style.left).toBe('321px')
    expect(card.style.top).toBe('123px')
    b1.destroy()
    mountQuotaBanner(w.document as unknown as Document, runtimeWith(mem), w as unknown as Window, 0)
    expect(shadowCard(w)!.style.left).toBe('321px')
  })

  it('拖拽落点夹进视口并写 GM', () => {
    const mem = new Map<string, unknown>()
    const w = dom(QUOTA_HTML)
    Object.defineProperties(w, {
      innerWidth: { value: 1024, configurable: true },
      innerHeight: { value: 768, configurable: true },
    })
    mountQuotaBanner(w.document as unknown as Document, runtimeWith(mem), w as unknown as Window, 0)
    const card = shadowCard(w)!
    Object.defineProperties(card, {
      offsetLeft: { value: 500, configurable: true },
      offsetTop: { value: 12, configurable: true },
      offsetWidth: { value: 300, configurable: true },
      offsetHeight: { value: 40, configurable: true },
    })
    pointer(w, 'pointerdown', 510, 20, card)
    expect(card.dataset.dragging).toBe('1')
    pointer(w, 'pointermove', 610, 70, card)
    pointer(w, 'pointerup', 610, 70, card)
    expect(card.dataset.dragging).toBeUndefined()
    expect(mem.get(QUOTA_POS_KEY)).toEqual({ left: 600, top: 62 })
  })

  it('位置解析拒绝非法值', () => {
    expect(parseQuotaPosition({ left: 1, top: 2 })).toEqual({ left: 1, top: 2 })
    expect(parseQuotaPosition({ left: '1', top: 2 })).toBeNull()
    expect(parseQuotaPosition(null)).toBeNull()
  })

  it('重复挂载只留一个横幅', () => {
    const w = dom(QUOTA_HTML)
    mountQuotaBanner(w.document as unknown as Document, runtimeWith(), w as unknown as Window, 0)
    mountQuotaBanner(w.document as unknown as Document, runtimeWith(), w as unknown as Window, 0)
    expect(w.document.querySelectorAll(`#${QUOTA_HOST_ID}`)).toHaveLength(1)
  })
})
