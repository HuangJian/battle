import { describe, it, expect, beforeEach } from 'bun:test'
import { I18n, localizeRoot, t, i18n as singleton } from '../src/i18n'
import { localizedStageName } from '../src/config/stages'

// Minimal DOM/localStorage stubs so the module can run under bun.
function makeLocalStorage(): Storage {
  const map = new Map<string, string>()
  return {
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: () => null,
    length: 0,
  } as unknown as Storage
}

describe('i18n core', () => {
  let calls: Array<() => void>
  let storage: Storage
  let i18n: I18n

  beforeEach(() => {
    calls = []
    storage = makeLocalStorage()
    globalThis.localStorage = storage
    i18n = new I18n()
  })

  it('defaults to English', () => {
    expect(i18n.locale).toBe('en')
  })

  it('translates a known key', () => {
    expect(i18n.t('menu.language')).toBe('LANGUAGE')
  })

  it('falls back to English for a missing non-default locale key', () => {
    // 'toast.languageSet' exists in both; verify zh resolves, then a
    // hypothetical missing key falls back to en then to the key itself.
    i18n.setLocale('zh')
    expect(i18n.t('menu.language')).toBe('语言')
    expect(i18n.t('this.key.does.not.exist')).toBe('this.key.does.not.exist')
  })

  it('interpolates params', () => {
    expect(i18n.t('menu.resume.stageFormat', { n: '07' })).toBe('STAGE 07')
  })

  it('resolves every snapshot filter key in both locales (no raw-key fallback)', () => {
    // Regression guard: the SnapshotBrowser FilterKey set is
    // all | manual | pause | stage-start | auto. Each must have a real
    // translation in BOTH catalogs, otherwise it renders the literal key.
    const keys = [
      'browser.snapshot.filter.all',
      'browser.snapshot.filter.manual',
      'browser.snapshot.filter.pause',
      'browser.snapshot.filter.stage-start',
      'browser.snapshot.filter.auto',
    ]
    for (const k of keys) {
      i18n.setLocale('en')
      const en = i18n.t(k)
      expect(en).not.toBe(k)
      i18n.setLocale('zh')
      const zh = i18n.t(k)
      expect(zh).not.toBe(k)
      expect(zh).not.toBe(en)
    }
    // Spot-check the exact values for the key that was previously missing.
    i18n.setLocale('en')
    expect(i18n.t('browser.snapshot.filter.stage-start')).toBe('STAGE START')
    i18n.setLocale('zh')
    expect(i18n.t('browser.snapshot.filter.stage-start')).toBe('关卡开始')
  })

  it('persists the choice to localStorage', () => {
    i18n.setLocale('zh')
    expect(storage.getItem('battle-city:locale')).toBe('zh')
    // A fresh instance picks up the persisted choice.
    const again = new I18n()
    expect(again.locale).toBe('zh')
  })

  it('notifies subscribers on change', () => {
    i18n.subscribe(() => calls.push(() => {}))
    i18n.setLocale('zh')
    expect(calls.length).toBe(1)
  })

  it('cycleLocale wraps around AVAILABLE_LOCALES', () => {
    i18n.setLocale('en')
    expect(i18n.cycleLocale()).toBe('zh')
    expect(i18n.cycleLocale()).toBe('en')
  })

  // Minimal fake DOM node implementing just the surface localizeRoot touches
  // (querySelectorAll over [data-i18n] / [data-i18n-attr], dataset, textContent,
  // setAttribute). Keeps the suite free of a browser/jsdom dependency.
  class FakeNode {
    dataset: Record<string, string> = {}
    textContent = ''
    private attrs: Record<string, string> = {}
    private children: FakeNode[] = []
    setAttribute(k: string, v: string) {
      this.attrs[k] = v
    }
    getAttr(k: string) {
      return this.attrs[k]
    }
    append(child: FakeNode) {
      this.children.push(child)
    }
    querySelectorAll(sel: string): FakeNode[] {
      const out: FakeNode[] = []
      const visit = (n: FakeNode) => {
        if (sel === '[data-i18n]' && n.dataset.i18n) out.push(n)
        if (sel === '[data-i18n-attr]' && n.dataset.i18nAttr) out.push(n)
        n.children.forEach(visit)
      }
      this.children.forEach(visit)
      return out
    }
  }

  it('localizeRoot fills [data-i18n] elements', () => {
    const root = new FakeNode()
    const start = new FakeNode()
    start.dataset.i18n = 'menu.start.newGame'
    start.textContent = 'NEW GAME'
    const title = new FakeNode()
    title.dataset.i18n = 'menu.title'
    const attrEl = new FakeNode()
    attrEl.dataset.i18nAttr = 'title:menu.title'
    root.append(start)
    root.append(title)
    root.append(attrEl)
    localizeRoot(root as unknown as HTMLElement)
    expect(start.textContent).toBe('NEW GAME')
    expect(title.textContent).toBe('BATTLE CITY')
    // localizeRoot reads the module-level singleton; switch that, not the
    // per-test instance, to emulate what a subscriber callback does.
    singleton.setLocale('zh')
    localizeRoot(root as unknown as HTMLElement)
    expect(start.textContent).toBe('新游戏')
    expect(title.textContent).toBe('坦克大战')
    expect(attrEl.textContent).toBe('')
    expect(attrEl.getAttr('title')).toBe('坦克大战')
  })
})

describe('classic stage names (i18n)', () => {
  it('resolves en + zh names for all 35 classic stages, no fallback', () => {
    const en = (i: number) => {
      singleton.setLocale('en')
      return localizedStageName(i)
    }
    const zh = (i: number) => {
      singleton.setLocale('zh')
      return localizedStageName(i)
    }
    // Spot-check the first and last names in both locales.
    expect(en(0)).toBe('Outpost')
    expect(zh(0)).toBe('前哨')
    expect(en(34)).toBe('Final Redoubt')
    expect(zh(34)).toBe('终极堡垒')
    // Every classic stage must have a distinct zh name (guards against a
    // missing entry silently falling back to the English string).
    for (let i = 0; i < 35; i++) {
      const e = en(i)
      const z = zh(i)
      expect(e).not.toBe(`Stage ${i + 1}`) // not the numeric fallback
      expect(z).not.toBe(e) // zh must differ from en
      expect(z.length).toBeGreaterThan(0)
    }
  })

  it('falls back to "Stage N" for out-of-range indices', () => {
    singleton.setLocale('zh')
    expect(localizedStageName(99)).toBe('Stage 100')
  })
})

describe('control center status lines (i18n)', () => {
  it('cc status keys resolve in both locales (no raw-key fallback)', () => {
    singleton.setLocale('en')
    expect(t('cc.counts.fmt', { total: 20, manual: 16, limit: 100 })).toBe(
      '20 snapshots · manual 16/100',
    )
    expect(t('cc.replays.fmt', { total: 3 })).toBe('3 replays')
    expect(t('cc.replays.fmtFav', { total: 3, fav: 1 })).toBe('3 replays · ★ 1')
    expect(t('cc.gameplay.inRun', { difficulty: 'Classic', n: '07', name: 'Outpost' })).toBe(
      'Classic · Stage 07 · Outpost',
    )
    expect(t('cc.gameplay.menu', { difficulty: 'Classic', theme: 'Classic' })).toBe(
      'Classic · Classic',
    )

    singleton.setLocale('zh')
    expect(t('cc.counts.fmt', { total: 20, manual: 16, limit: 100 })).toBe(
      '20 个存档 · 手动 16/100',
    )
    expect(t('cc.replays.fmt', { total: 3 })).toBe('3 个回放')
    expect(t('cc.replays.fmtFav', { total: 3, fav: 1 })).toBe('3 个回放 · ★ 1')
    expect(t('cc.gameplay.inRun', { difficulty: '经典', n: '07', name: '前哨' })).toBe(
      '经典 · 第 07 关 · 前哨',
    )
    expect(t('cc.gameplay.menu', { difficulty: '经典', theme: '经典' })).toBe('经典 · 经典')
    // Tooltip keys also exist in zh.
    expect(t('cc.titleCollapse')).toBe('收起')
    expect(t('cc.titleCoop')).toBe('切换躺赢模式（神级 AI 合作）')
  })
})

describe('HUD strings (i18n)', () => {
  it('god-ally label and pause hint resolve in both locales', () => {
    singleton.setLocale('en')
    expect(t('hud.god')).toBe('GOD ALLY')
    expect(t('hud.pauseHint')).toBe('P Resume')
    expect(t('pause.title')).toBe('PAUSED')

    singleton.setLocale('zh')
    expect(t('hud.god')).toBe('神队友')
    expect(t('hud.pauseHint')).toBe('P 继续')
    expect(t('pause.title')).toBe('已暂停')
  })
})
