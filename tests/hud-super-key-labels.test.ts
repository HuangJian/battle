import { describe, it, expect } from 'bun:test'
import { formatSuperKeyLabel } from '../src/presentation/ui/HudView'
import { DEFAULT_KEYS, DEFAULT_P2_KEYS } from '../src/game/Input'

/**
 * HUD super-item key labels (DECISIONS §347 follow-ups): the label text is a
 * pure function of (name, binding) so both the P1 row and the two-player P2
 * row are regression-tested headlessly (AGENTS §8 — no DOM).
 *
 * HUD semantics: the guard/frenzy/rewind stocks are WORLD-global (either
 * human may spend one), so in two-player mode the HUD shows ONE row per
 * player per item, each labeled with that player's own rebound key.
 */

describe('formatSuperKeyLabel', () => {
  it('renders name + P1 default keys', () => {
    expect(formatSuperKeyLabel('Guardian', DEFAULT_KEYS.guard)).toBe('Guardian<F5>')
    expect(formatSuperKeyLabel('Frenzy', DEFAULT_KEYS.frenzy)).toBe('Frenzy<F6>')
    expect(formatSuperKeyLabel('Time Box', DEFAULT_KEYS.rewind)).toBe('Time Box<F7>')
  })

  it('renders name + P2 default keys (WASD-cluster)', () => {
    expect(formatSuperKeyLabel('Guardian', DEFAULT_P2_KEYS.guard)).toBe('Guardian<R>')
    expect(formatSuperKeyLabel('Frenzy', DEFAULT_P2_KEYS.frenzy)).toBe('Frenzy<T>')
    expect(formatSuperKeyLabel('Time Box', DEFAULT_P2_KEYS.rewind)).toBe('Time Box<G>')
  })

  it('follows rebound keys immediately (panel remap reaches the HUD label)', () => {
    // P1 rebinds guard to KeyQ; P2 rebinds rewind to Shift+Digit5.
    expect(formatSuperKeyLabel('Guardian', 'KeyQ')).toBe('Guardian<Q>')
    // Modifier prefixes are dropped in the compact HUD label (code shown).
    expect(formatSuperKeyLabel('Time Box', 'Shift+Digit5')).toBe('Time Box<5>')
  })

  it('a pure-modifier binding degrades gracefully instead of showing a lie', () => {
    // Historically-captured broken binding "Alt+AltLeft" must not render as
    // a usable key hint.
    expect(formatSuperKeyLabel('Guardian', 'Alt+AltLeft')).toBe('Guardian<AltLeft>')
  })
})
