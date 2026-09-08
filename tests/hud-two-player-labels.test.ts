import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { HudView } from '../src/presentation/ui/HudView'
import { DEFAULT_KEYS, DEFAULT_P2_KEYS } from '../src/game/Input'
import { RULES } from '../src/config/rules'

/**
 * Two-player HUD super-item label rows (2p-review P0-1): the 2p commit shipped
 * the update/show-hide logic for `guardLabel2/frenzyLabel2/rewindLabel2` but
 * never the create step, so the rows could never appear. DOM regression test
 * against happy-dom's real DOM (DECISIONS §2026-09-08-ps2-happydom) — the
 * element factory mirrors UIManager.createElement (document.createElement +
 * className).
 */

/** Same element factory UIManager hands to HudView in production. */
function makeCreateElement(tag: string, className: string): HTMLElement {
  const el = document.createElement(tag)
  el.className = className
  return el
}

function makeHud(): HudView {
  return new HudView(makeCreateElement, () => {})
}

/** Read a private HudView label field (the DOM elements the class owns). */
function labelOf(
  hud: HudView,
  field: 'guardLabel2' | 'frenzyLabel2' | 'rewindLabel2',
): HTMLElement {
  const el = (hud as unknown as Record<string, HTMLElement | null>)[field]
  if (!el) throw new Error(`P2 label ${field} was never created (P0-1 regression)`)
  return el
}

describe('HudView two-player super-key labels (P0-1)', () => {
  it('creates the P2 label rows lazily when P2 bindings are first supplied', () => {
    const hud = makeHud()
    // No P2 rows exist before P2 bindings arrive.
    expect(hud.el.querySelectorAll('.hud-super-p2').length).toBe(0)

    hud.updateSuperKeyLabels(DEFAULT_KEYS, DEFAULT_P2_KEYS)

    const rows = hud.el.querySelectorAll('.hud-super-p2')
    expect(rows.length).toBe(3)
    // Each row carries P2's own rebound key, P1's row untouched.
    expect(labelOf(hud, 'guardLabel2').textContent).toBe('Guardian<R>')
    expect(labelOf(hud, 'frenzyLabel2').textContent).toBe('Frenzy<T>')
    expect(labelOf(hud, 'rewindLabel2').textContent).toBe('Time Box<G>')
    const hudAny = hud as unknown as Record<string, HTMLElement | null>
    expect(hudAny.guardLabel!.textContent).toBe('Guardian<F5>')
    // Created hidden — only visible in two-player mode.
    expect(labelOf(hud, 'guardLabel2').hidden).toBe(true)
  })

  it('keeps rows in sync after a P2 rebind (panel remap reaches the P2 row)', () => {
    const hud = makeHud()
    hud.updateSuperKeyLabels(DEFAULT_KEYS, DEFAULT_P2_KEYS)
    const k2 = { ...DEFAULT_P2_KEYS, guard: 'KeyQ' }
    hud.updateSuperKeyLabels(DEFAULT_KEYS, k2)
    expect(labelOf(hud, 'guardLabel2').textContent).toBe('Guardian<Q>')
    // Creation is idempotent — no duplicate rows after a second update.
    expect(hud.el.querySelectorAll('.hud-super-p2').length).toBe(3)
  })

  it('syncWorld flips the P2 rows with the twoPlayer flag', () => {
    const hud = makeHud()
    hud.updateSuperKeyLabels(DEFAULT_KEYS, DEFAULT_P2_KEYS)
    const world = new World()
    // A fresh World uses DEFAULT_RULES (super items shown, superDropChance > 0).
    expect(world.rules.superDropChance).toBeGreaterThan(0)

    world.twoPlayer = true
    hud.syncWorld(world)
    expect(labelOf(hud, 'guardLabel2').hidden).toBe(false)
    expect(labelOf(hud, 'rewindLabel2').hidden).toBe(false)

    world.twoPlayer = false
    hud.syncWorld(world)
    expect(labelOf(hud, 'guardLabel2').hidden).toBe(true)
    expect(labelOf(hud, 'rewindLabel2').hidden).toBe(true)
  })

  it('classic mode (no super drops) keeps the P2 rows hidden even in twoPlayer', () => {
    const hud = makeHud()
    hud.updateSuperKeyLabels(DEFAULT_KEYS, DEFAULT_P2_KEYS)
    const world = new World()
    world.rules = RULES.classic // superDropChance === 0
    world.twoPlayer = true
    hud.syncWorld(world)
    expect(labelOf(hud, 'guardLabel2').hidden).toBe(true)
  })
})
