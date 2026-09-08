/**
 * Minimal fake DOM for UI regression tests — no jsdom/happy-dom dependency
 * (AGENTS §8 permits DOM when the system under test requires it; the repo
 * deliberately avoids a browser emulator, see tests/i18n-smoke.test.ts).
 *
 * Supports exactly the surface HudView / ControlsPanel touch: element
 * creation with className, `innerHTML` templates parsed into children,
 * `querySelector(All)` for `[data-x="y"]` and `.class` selectors,
 * textContent, hidden, style, dataset/attrs, classList, appendChild,
 * addEventListener. Text nodes are dropped (templates' static text is never
 * asserted; dynamic text is set via textContent).
 */
export class FakeEl {
  tagName: string
  className = ''
  hidden = false
  style: Record<string, string> = {}
  /** Mirrors `attrs['data-x']` (assignment writes attrs, reads resolve attrs),
   *  so `el.dataset.action = 'fire'` is queryable via `[data-action="fire"]`. */
  readonly dataset: Record<string, string>
  attrs: Record<string, string> = {}
  parentElement: FakeEl | null = null
  children: FakeEl[] = []
  private _text = ''
  private listeners: Record<string, Array<() => void>> = {}

  constructor(tag: string, className = '') {
    this.tagName = tag.toUpperCase()
    this.className = className
    this.dataset = new Proxy({} as Record<string, string>, {
      get: (_, key) => this.attrs[`data-${String(key)}`],
      set: (_, key, value: string) => {
        this.attrs[`data-${String(key)}`] = String(value)
        return true
      },
    })
  }

  set textContent(v: string) {
    this._text = String(v)
  }
  get textContent(): string {
    return this._text
  }

  /** Parse a static template into children (see {@link parseHtml}). */
  set innerHTML(html: string) {
    this.children = []
    for (const el of parseHtml(html)) this.appendChild(el)
  }
  get innerHTML(): string {
    return ''
  }

  readonly classList = {
    add: (c: string): void => {
      if (!this.classList.contains(c)) {
        this.className = this.className ? `${this.className} ${c}` : c
      }
    },
    remove: (c: string): void => {
      this.className = this.className
        .split(/\s+/)
        .filter((x) => x !== c)
        .join(' ')
    },
    toggle: (c: string, force?: boolean): boolean => {
      const on = force ?? !this.classList.contains(c)
      if (on) this.classList.add(c)
      else this.classList.remove(c)
      return on
    },
    contains: (c: string): boolean => this.className.split(/\s+/).includes(c),
  }

  appendChild(el: FakeEl): void {
    el.parentElement = this
    this.children.push(el)
  }

  setAttribute(name: string, value: string): void {
    this.attrs[name] = value
    if (name.startsWith('data-')) this.dataset[name.slice(5)] = value
  }

  addEventListener(type: string, fn: () => void): void {
    ;(this.listeners[type] ??= []).push(fn)
  }

  /** Test helper: fire all listeners registered for `type`. */
  dispatch(type: string): void {
    for (const fn of this.listeners[type] ?? []) fn()
  }

  querySelector(sel: string): FakeEl | null {
    return this.querySelectorAll(sel)[0] ?? null
  }

  querySelectorAll(sel: string): FakeEl[] {
    const out: FakeEl[] = []
    const walk = (el: FakeEl): void => {
      for (const c of el.children) {
        if (matchesSelector(c, sel)) out.push(c)
        walk(c)
      }
    }
    walk(this)
    return out
  }
}

function matchesSelector(el: FakeEl, sel: string): boolean {
  if (sel.startsWith('.')) {
    return el.className.split(/\s+/).includes(sel.slice(1))
  }
  const attr = sel.match(/^\[([a-z][a-z-]*)="([^"]*)"\]$/)
  if (attr) {
    const [, name, value] = attr
    return el.attrs[name] === value
  }
  return false
}

const TAG_RE = /<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|[^'">]|'[^']*')*?)(\/?)>/g

/** Parse a static innerHTML template into a tree of FakeEls. */
export function parseHtml(html: string): FakeEl[] {
  const roots: FakeEl[] = []
  const stack: FakeEl[] = []
  let m: RegExpExecArray | null
  TAG_RE.lastIndex = 0
  while ((m = TAG_RE.exec(html))) {
    const [, close, tag, attrsStr, selfClose] = m
    if (close) {
      if (stack.length > 0) stack.pop()
      continue
    }
    const el = new FakeEl(tag)
    parseAttrs(attrsStr, el)
    if (stack.length > 0) stack[stack.length - 1].appendChild(el)
    else roots.push(el)
    if (!selfClose) stack.push(el)
  }
  return roots
}

const ATTR_RE = /([a-zA-Z][a-zA-Z-]*)(?:="([^"]*)")?/g

function parseAttrs(s: string, el: FakeEl): void {
  let m: RegExpExecArray | null
  ATTR_RE.lastIndex = 0
  while ((m = ATTR_RE.exec(s))) {
    const name = m[1]
    const value = m[2] ?? ''
    if (name === 'class') {
      el.className = value
    } else if (name === 'hidden') {
      el.hidden = true
    } else if (name.startsWith('data-')) {
      el.setAttribute(name, value)
    } else {
      el.attrs[name] = value
    }
  }
}

/**
 * Casting bridge for components typed against `HTMLElement` (HudView /
 * ControlsPanel take a `(tag, cls) => HTMLElement` factory): the tests hand
 * them FakeEls, which are structurally unrelated to HTMLElement, so the cast
 * lives here instead of in every test file.
 */
export function fakeCreateElement(tag: string, className: string): HTMLElement {
  return new FakeEl(tag, className) as unknown as HTMLElement
}

/** Stub `window` globals a UI component may touch (listeners / timers). */
export function installWindowStub(): { listeners: Record<string, Array<() => void>> } {
  const listeners: Record<string, Array<() => void>> = {}
  ;(globalThis as Record<string, unknown>).window = {
    addEventListener: (type: string, fn: () => void): void => {
      ;(listeners[type] ??= []).push(fn)
    },
    setTimeout: (): number => 0,
    clearTimeout: (): void => {},
  }
  return { listeners }
}

/** Stub requestAnimationFrame/cancelAnimationFrame to capture callbacks. */
export function installRafStub(): {
  nextFrame: () => void
  restore: () => void
} {
  let pending: (() => void) | null = null
  const origRaf = (globalThis as Record<string, unknown>).requestAnimationFrame
  const origCaf = (globalThis as Record<string, unknown>).cancelAnimationFrame
  ;(globalThis as Record<string, unknown>).requestAnimationFrame = (fn: () => void): number => {
    pending = fn
    return 1
  }
  ;(globalThis as Record<string, unknown>).cancelAnimationFrame = (): void => {
    pending = null
  }
  return {
    nextFrame: () => {
      const fn = pending
      pending = null
      fn?.()
    },
    restore: () => {
      if (origRaf) (globalThis as Record<string, unknown>).requestAnimationFrame = origRaf
      if (origCaf) (globalThis as Record<string, unknown>).cancelAnimationFrame = origCaf
    },
  }
}
