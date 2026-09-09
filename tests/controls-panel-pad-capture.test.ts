import { describe, it, expect, afterEach } from 'bun:test'
import { ControlsPanel } from '../src/presentation/ui/ControlsPanel'
import { DEFAULT_KEYS, DEFAULT_P2_KEYS } from '../src/game/Input'
import { DEFAULT_PAD_BINDINGS } from '../src/game/settings'
import type { PadBindings } from '../src/types'
import type { GamepadSnapshot } from '../src/game/GamepadInput'

/**
 * Gamepad rebind capture on STATIC screens (2p-review P0-2): menu / paused /
 * gameover run no rAF loop, so the GamepadManager is never polled there — the
 * capture loop must drive a fresh poll itself or a pure-pad user can never
 * rebind (the snapshot would be the stale pre-capture value forever).
 * DOM regression test against happy-dom's real DOM; the capture loop's rAF is
 * stubbed for deterministic frame stepping (DECISIONS §2026-09-08-ps2-happydom).
 */

/** A gamepad snapshot with a single button held. */
function padWithButtonPressed(button: number): GamepadSnapshot {
  return {
    index: 0,
    id: 'test-pad-0',
    axes: [0, 0, 0, 0],
    buttons: Array.from({ length: 17 }, (_, i) => ({
      pressed: i === button,
      value: i === button ? 1 : 0,
    })),
    connected: true,
    mapping: 'standard',
  }
}

/** Same element factory UIManager hands to ControlsPanel in production. */
function makeCreateElement(tag: string, className: string): HTMLElement {
  const el = document.createElement(tag)
  el.className = className
  return el
}

function makePanel(): ControlsPanel {
  return new ControlsPanel(makeCreateElement)
}

/** Real DOM element for a selector inside the panel. */
function elOf(panel: ControlsPanel, sel: string): HTMLElement {
  const el = panel.el.querySelector(sel)
  if (!el) throw new Error(`missing element for ${sel}`)
  return el as HTMLElement
}

/** Open the panel, switch to the Gamepad tab, and click the given row. */
function openPadTabAndClick(panel: ControlsPanel, action: string): void {
  panel.open()
  elOf(panel, '[data-controls="tab-pad"]').click()
  elOf(panel, `[data-action="${action}"]`).click()
}

/** Deterministic rAF stub: each nextFrame() runs the latest scheduled callback. */
function installRafStub(): { nextFrame: () => void; restore: () => void } {
  let pending: (() => void) | null = null
  const origRaf = globalThis.requestAnimationFrame.bind(globalThis)
  globalThis.requestAnimationFrame = (fn: FrameRequestCallback): number => {
    pending = () => fn(0)
    return 1
  }
  return {
    nextFrame: () => {
      const fn = pending
      pending = null
      fn?.()
    },
    restore: () => {
      globalThis.requestAnimationFrame = origRaf
    },
  }
}

describe('ControlsPanel gamepad capture (P0-2)', () => {
  afterEach(() => {
    // Panel constructors register a window keydown listener; detach via close.
  })

  it('capture polls fresh hardware state before reading, and binds the press', () => {
    const raf = installRafStub()
    const panel = makePanel()
    const pads: PadBindings = { ...DEFAULT_PAD_BINDINGS }
    panel.initControls({ ...DEFAULT_KEYS }, { ...DEFAULT_P2_KEYS }, () => {}, pads)

    openPadTabAndClick(panel, 'fire')

    // The capture loop's poll seam: production wires this to
    // `GamepadManager.poll() → p1Snapshot`. Count calls + return a fresh press.
    let pollCalls = 0
    panel.requestPadPoll = () => {
      pollCalls++
      return padWithButtonPressed(5) // rebind fire → BUTTON 5
    }

    raf.nextFrame() // run the first capture frame
    expect(pollCalls).toBe(1)
    expect(pads.fire).toBe(5) // the press was captured and bound
    raf.restore()
  })

  it('capture never binds when the snapshot is stale (no poll seam) — starvation repro', () => {
    const raf = installRafStub()
    const panel = makePanel()
    const pads: PadBindings = { ...DEFAULT_PAD_BINDINGS }
    panel.initControls({ ...DEFAULT_KEYS }, { ...DEFAULT_P2_KEYS }, () => {}, pads)

    openPadTabAndClick(panel, 'fire')

    // The pre-fix wiring: only the passive snapshot source exists, returning
    // the stale pre-capture snapshot with nothing pressed.
    panel.getSnapshot = () => padWithButtonPressed(-1) // no button pressed
    panel.requestPadPoll = null

    raf.nextFrame() // capture frame 1 — no press → re-arms
    raf.nextFrame() // capture frame 2 — still nothing
    expect(pads.fire).toBe(DEFAULT_PAD_BINDINGS.fire) // never bound
    raf.restore()
  })

  it('a conflicting press is rejected (flash) and the binding stays put', () => {
    const raf = installRafStub()
    const panel = makePanel()
    const pads: PadBindings = { ...DEFAULT_PAD_BINDINGS }
    panel.initControls({ ...DEFAULT_KEYS }, { ...DEFAULT_P2_KEYS }, () => {}, pads)

    openPadTabAndClick(panel, 'fire')

    let pollCalls = 0
    panel.requestPadPoll = () => {
      pollCalls++
      // Button 1 is already bound to guard — fire must be rejected.
      return padWithButtonPressed(1)
    }
    raf.nextFrame()
    expect(pollCalls).toBe(1)
    expect(pads.fire).toBe(DEFAULT_PAD_BINDINGS.fire) // unchanged
    raf.restore()
  })
})
