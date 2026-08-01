import type { Catalog, Locale, LocaleMeta } from './types'
import { en } from './locales/en'
import { zh } from './locales/zh'

/**
 * Battle City Web — multi-language subsystem (i18n core).
 *
 * Philosophy (see MANIFEST §6 / §8):
 * - Strings are DATA, kept in per-locale catalogs. Adding a language means
 *   adding a `locales/<code>.ts` file + one line in `AVAILABLE_LOCALES`.
 * - Language is presentation-only state: it lives here (and in localStorage),
 *   never on the `World`, so it cannot leak into snapshots, replays, or the
 *   deterministic simulation.
 *
 * Usage:
 *   import { t, i18n } from '../i18n'
 *   el.textContent = t('menu.start')                 // static
 *   el.textContent = t('stageclear.name', {n, name}) // interpolated
 *   <p data-i18n="pause.hint"></p>                   // auto-localized
 *   i18n.subscribe(() => ui.refreshText())           // react to switches
 */

const DEFAULT_LOCALE: Locale = 'en'
const STORAGE_KEY = 'battle-city:locale'

/** Locales offered in the LANGUAGE menu, in display order. */
export const AVAILABLE_LOCALES: Locale[] = ['en', 'zh']

/** End-user-facing names for each locale (native spelling). */
export const LOCALE_METAS: Record<Locale, LocaleMeta> = {
  en: { code: 'en', name: 'English' },
  zh: { code: 'zh', name: '中文' },
}

const CATALOGS: Record<Locale, Catalog> = {
  en,
  zh,
}

/** Interpolate `{name}` placeholders from a `params` map. */
function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (_, key: string) =>
    key in params ? String(params[key]) : `{${key}}`,
  )
}

/** Normalize params into a fresh object (so callers' maps aren't mutated). */
function resolveParams(
  params?: Record<string, string | number>,
): Record<string, string | number> | undefined {
  if (!params) return undefined
  const out: Record<string, string | number> = {}
  for (const [k, v] of Object.entries(params)) out[k] = v
  return out
}

export class I18n {
  private current: Locale
  private readonly listeners = new Set<() => void>()

  constructor() {
    this.current = I18n.loadInitial()
  }

  /** Read the persisted choice, falling back to the default (English). */
  private static loadInitial(): Locale {
    try {
      const stored = localStorage.getItem(STORAGE_KEY) as Locale | null
      if (stored && (AVAILABLE_LOCALES as string[]).includes(stored)) {
        return stored
      }
    } catch {
      // localStorage may be unavailable (private mode / SSR) — ignore.
    }
    return DEFAULT_LOCALE
  }

  /** The active locale code. */
  get locale(): Locale {
    return this.current
  }

  /** All locales the UI can switch to, in menu order. */
  get available(): Locale[] {
    return AVAILABLE_LOCALES
  }

  /** Native display name for a locale (for the LANGUAGE menu options). */
  name(code: Locale): string {
    return LOCALE_METAS[code]?.name ?? code
  }

  /**
   * Translate `key`.
   * Resolution order: current locale → English (fallback) → the key itself
   * (so a missing translation is visible, never blank). Params interpolate
   * `{name}` placeholders.
   */
  t(key: string, params?: Record<string, string | number>): string {
    const tmpl = CATALOGS[this.current][key] ?? CATALOGS.en[key] ?? key
    return interpolate(tmpl, resolveParams(params))
  }

  /** Switch locale. Persists to localStorage and notifies subscribers. */
  setLocale(code: Locale): void {
    if (!(AVAILABLE_LOCALES as string[]).includes(code)) return
    if (code === this.current) return
    this.current = code
    try {
      localStorage.setItem(STORAGE_KEY, code)
    } catch {
      // Persistence is best-effort; the in-memory switch still applies.
    }
    for (const cb of this.listeners) cb()
  }

  /** Toggle to the next available locale (used by the menu LANGUAGE row). */
  cycleLocale(): Locale {
    const idx = AVAILABLE_LOCALES.indexOf(this.current)
    const next = AVAILABLE_LOCALES[(idx + 1) % AVAILABLE_LOCALES.length]
    this.setLocale(next)
    return next
  }

  /** Register a callback fired after every locale change. Returns an unsubscribe. */
  subscribe(cb: () => void): () => void {
    this.listeners.add(cb)
    return () => this.listeners.delete(cb)
  }
}

/** Shared singleton — the single source of truth for the active language. */
export const i18n = new I18n()

/** Convenience translate function bound to the singleton. */
export function t(key: string, params?: Record<string, string | number>): string {
  return i18n.t(key, params)
}

/**
 * Re-localize every `[data-i18n]` element under `root` in place. Elements
 * declare their key via `data-i18n="some.key"`; this sets `textContent`.
 * Dynamic (interpolated) strings are handled explicitly in update loops via
 * `t()` and are intentionally not marked `data-i18n`.
 */
export function localizeRoot(root: HTMLElement): void {
  const nodes = root.querySelectorAll<HTMLElement>('[data-i18n]')
  nodes.forEach((el) => {
    const key = el.dataset.i18n
    if (key) el.textContent = i18n.t(key)
  })
  // Optional attribute localization: data-i18n-attr="title:some.key;aria-label:other.key"
  const attrNodes = root.querySelectorAll<HTMLElement>('[data-i18n-attr]')
  attrNodes.forEach((el) => {
    const spec = el.dataset.i18nAttr
    if (!spec) return
    for (const part of spec.split(';')) {
      const sep = part.indexOf(':')
      if (sep < 0) continue
      const attr = part.slice(0, sep).trim()
      const key = part.slice(sep + 1).trim()
      if (attr && key) el.setAttribute(attr, i18n.t(key))
    }
  })
}
