import { describe, it, expect, beforeEach } from 'bun:test'
import { loadSettings, SETTINGS_KEY } from '../src/game/settings'
import { DEFAULT_VOLUME } from '../src/audio/AudioManager'

// Minimal localStorage stub so the module can run under bun (same pattern as
// tests/i18n-smoke.test.ts).
function makeLocalStorage(): Storage {
  const map = new Map<string, string>()
  return {
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: (i: number) => [...map.keys()][i] ?? null,
    get length() {
      return map.size
    },
  } as Storage
}

describe('loadSettings — persisted volume', () => {
  let storage: Storage

  beforeEach(() => {
    storage = makeLocalStorage()
    ;(globalThis as unknown as { localStorage: Storage }).localStorage = storage
  })

  it('keeps a valid persisted volume', () => {
    storage.setItem(SETTINGS_KEY, JSON.stringify({ volume: 0.6 }))
    expect(loadSettings().volume).toBe(0.6)
  })

  it('defaults when nothing was persisted', () => {
    expect(loadSettings().volume).toBe(DEFAULT_VOLUME)
  })

  it('repairs a null / non-numeric volume instead of passing it to Web Audio', () => {
    // Regression: a null volume used to reach the GainNode, throw inside
    // AudioManager.init(), and mute the game for the whole session.
    storage.setItem(SETTINGS_KEY, JSON.stringify({ volume: null }))
    expect(loadSettings().volume).toBe(DEFAULT_VOLUME)

    storage.setItem(SETTINGS_KEY, JSON.stringify({ volume: '0.4' }))
    expect(loadSettings().volume).toBe(DEFAULT_VOLUME)
  })

  it('clamps an out-of-range volume into the gain range', () => {
    storage.setItem(SETTINGS_KEY, JSON.stringify({ volume: 12 }))
    expect(loadSettings().volume).toBe(1)

    storage.setItem(SETTINGS_KEY, JSON.stringify({ volume: -3 }))
    expect(loadSettings().volume).toBe(0)
  })

  it('falls back for any non-number shape (array / object / boolean)', () => {
    for (const bad of [[0.2, 0.4], { v: 1 }, true]) {
      storage.setItem(SETTINGS_KEY, JSON.stringify({ volume: bad }))
      expect(loadSettings().volume).toBe(DEFAULT_VOLUME)
    }
  })

  it('still repairs un-fireable key bindings', () => {
    storage.setItem(SETTINGS_KEY, JSON.stringify({ volume: 0.5, keys: { pause: 'Alt+AltLeft' } }))
    const settings = loadSettings()
    expect(settings.keys.pause).not.toBe('Alt+AltLeft')
    expect(settings.volume).toBe(0.5)
  })
})
