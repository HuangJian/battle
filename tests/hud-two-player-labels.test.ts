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
  it('creates the P2 key chips lazily when P2 bindings are first supplied', () => {
    const hud = makeHud()
    // No P2 chips exist before P2 bindings arrive.
    expect(hud.superRail.querySelectorAll('.hud-super-p2').length).toBe(0)

    hud.updateSuperKeyLabels(DEFAULT_KEYS, DEFAULT_P2_KEYS)

    const chips = hud.superRail.querySelectorAll('.hud-super-p2')
    expect(chips.length).toBe(3)
    // Each chip carries P2's own BARE rebound key (the P1 label already names
    // the item — HUD redesign: no second full row), P1's label untouched.
    expect(labelOf(hud, 'guardLabel2').textContent).toBe('R')
    expect(labelOf(hud, 'frenzyLabel2').textContent).toBe('T')
    expect(labelOf(hud, 'rewindLabel2').textContent).toBe('G')
    const hudAny = hud as unknown as Record<string, HTMLElement | null>
    expect(hudAny.guardLabel!.textContent).toBe('Guardian<F5>')
    // The chip joins the P1 row on the SAME line (after the stock counter) —
    // it must never create a stacked row inside the item.
    const guardItem = hudAny.guardLabel!.parentElement!
    const valueEl = guardItem.querySelector('.hud-value')!
    expect(valueEl.nextElementSibling).toBe(labelOf(hud, 'guardLabel2'))
    // The rail is a SEPARATE element from the HUD bar (sits beside the
    // playfield, not inside the bar).
    expect(hud.el.contains(hud.superRail)).toBe(false)
    // Created hidden — only visible in two-player mode.
    expect(labelOf(hud, 'guardLabel2').hidden).toBe(true)
  })

  it('keeps chips in sync after a P2 rebind (panel remap reaches the P2 chip)', () => {
    const hud = makeHud()
    hud.updateSuperKeyLabels(DEFAULT_KEYS, DEFAULT_P2_KEYS)
    const k2 = { ...DEFAULT_P2_KEYS, guard: 'KeyQ' }
    hud.updateSuperKeyLabels(DEFAULT_KEYS, k2)
    expect(labelOf(hud, 'guardLabel2').textContent).toBe('Q')
    // Creation is idempotent — no duplicate chips after a second update.
    expect(hud.superRail.querySelectorAll('.hud-super-p2').length).toBe(3)
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

  it('classic mode (no super drops) hides the P2 rows and the whole rail', () => {
    const hud = makeHud()
    hud.updateSuperKeyLabels(DEFAULT_KEYS, DEFAULT_P2_KEYS)
    const world = new World()
    world.rules = RULES.classic // superDropChance === 0
    world.twoPlayer = true
    hud.setVisible(true)
    hud.syncWorld(world)
    expect(labelOf(hud, 'guardLabel2').hidden).toBe(true)
    expect(hud.superRail.hidden).toBe(true) // no super items → no rail
  })
})

describe('HudView super-item rail visibility (HUD redesign follow-up)', () => {
  it('shows the rail only when the HUD is up AND the mode has super items', () => {
    const hud = makeHud()
    const world = new World()
    expect(world.rules.superDropChance).toBeGreaterThan(0)

    // Menu (HUD hidden): rail stays hidden even in a super-items mode.
    hud.syncWorld(world)
    expect(hud.superRail.hidden).toBe(true)
    hud.setVisible(false)
    expect(hud.superRail.hidden).toBe(true)

    // In play (HUD visible): rail appears.
    hud.setVisible(true)
    expect(hud.superRail.hidden).toBe(false)
  })

  it('fires the toggle callback only when the rail VISIBLE state flips', () => {
    let flips = 0
    const hud = new HudView(
      makeCreateElement,
      () => {},
      () => flips++,
    )
    const world = new World()

    hud.setVisible(true)
    expect(flips).toBe(1) // hidden → visible

    hud.syncWorld(world) // super items, still visible — no flip
    expect(flips).toBe(1)

    world.rules = RULES.classic
    hud.syncWorld(world) // rail hides
    expect(flips).toBe(2)
    expect(hud.superRail.hidden).toBe(true)
  })

  it('shows a panel header with title + total stock (change-guarded)', () => {
    const hud = makeHud()
    hud.setVisible(true)
    const world = new World()
    hud.syncWorld(world)

    // The header marks the rail as its own inventory panel.
    const title = hud.superRail.querySelector<HTMLElement>('.hud-super-title')!
    expect(title).not.toBeNull()
    expect(title.dataset.i18n).toBe('hud.superItems')
    const total = hud.superRail.querySelector('[data-hud="super-total"]')!
    expect(total.textContent).toBe('×0')

    // Total = sum of BOTH players' inventories, updated only on change.
    world.guardStock = 2
    world.frenzyStock = 1
    world.sacrificeStock = 0
    world.rewindStock = 4
    hud.syncWorld(world)
    expect(total.textContent).toBe('×7')
    hud.syncWorld(world) // unchanged — no rewrite churn
    expect(total.textContent).toBe('×7')
  })

  it('shows P2 stock counters only while a player2 tank exists (per-player inventories)', () => {
    const hud = makeHud()
    hud.setVisible(true)
    const world = new World()

    // Single-player: P2 counters exist in the DOM but stay hidden, even when
    // P2's fields carry stock (they belong to nobody on the field yet).
    world.guardStock = 2
    world.rewindStock2 = 3
    hud.syncWorld(world)
    const guard2 = hud.superRail.querySelector<HTMLElement>('[data-hud="guard2"]')!
    const rewind2 = hud.superRail.querySelector<HTMLElement>('[data-hud="rewind2"]')!
    expect(guard2.hidden).toBe(true)
    expect(rewind2.hidden).toBe(true)

    // 双打 Two-Player: P2 joins — its own counters appear with P2's values.
    world.twoPlayer = true
    world.enablePlayer2()
    hud.syncWorld(world)
    expect(guard2.hidden).toBe(false)
    expect(guard2.textContent).toBe('0')
    expect(rewind2.textContent).toBe('3')
    // Header total counts BOTH players' inventories.
    const total = hud.superRail.querySelector('[data-hud="super-total"]')!
    expect(total.textContent).toBe('×5')

    // P2 leaves (menu / disable): counters hide again.
    world.twoPlayer = false
    world.disablePlayer2()
    hud.syncWorld(world)
    expect(guard2.hidden).toBe(true)
  })
})
