import { describe, it, expect } from 'bun:test'
import {
  canOpenControls,
  canToggleCoop,
  canToggleSpectate,
  isReplayBrowserBlocked,
} from '../src/game/uiFlowGates'
import type { GameState } from '../src/types'

/**
 * Regression tests for the MISSION FAILED (recovery) screen fixes (2026-08-01).
 *
 * The Control Center buttons (Replay Browser / Lie-Back Win / Key Bindings)
 * were wired in Game.ts behind DOM-bound UI classes (UIManager / ControlCenter)
 * that cannot be instantiated in the headless Bun test runtime. The bugs were
 * each a wrong *state guard*, so the regression surface is the extracted pure
 * predicates in uiFlowGates.ts — the exact same guards Game.ts now consults
 * (requestCoopToggle, onOpenControls, openReplayBrowser all call them). The
 * predicate calls ARE the wiring contract: a fix that bypasses them and
 * re-adds an inline guard in Game.ts would not be caught here.
 *
 *  1. Key Bindings (openControls)     — errored on the recovery screen
 *  2. Lie-Back Win (requestCoopToggle) — silently did nothing on recovery
 *  3. Replay Browser (openReplayBrowser) — silently did nothing on recovery
 */

const ALL_STATES: GameState[] = [
  'menu',
  'playing',
  'paused',
  'stageclear',
  'gameover',
  'victory',
  'recovery',
]

describe('Recovery-screen flow gates (regression)', () => {
  describe('Key Bindings — canOpenControls', () => {
    it('opens from the MISSION FAILED (recovery) screen', () => {
      expect(canOpenControls('recovery')).toBe(true)
    })

    it('opens over every static screen: menu / paused / gameover', () => {
      expect(canOpenControls('menu')).toBe(true)
      expect(canOpenControls('paused')).toBe(true)
      expect(canOpenControls('gameover')).toBe(true)
    })

    it('never opens over a live world (playing / stageclear / victory)', () => {
      expect(canOpenControls('playing')).toBe(false)
      expect(canOpenControls('stageclear')).toBe(false)
      expect(canOpenControls('victory')).toBe(false)
    })
  })

  describe('Lie-Back Win — canToggleCoop', () => {
    it('accepts the MISSION FAILED (recovery) screen so co-op can be armed before retrying', () => {
      expect(canToggleCoop('recovery')).toBe(true)
    })

    it('accepts menu and paused', () => {
      expect(canToggleCoop('menu')).toBe(true)
      expect(canToggleCoop('paused')).toBe(true)
    })

    it('rejects live play and terminal states (playing / stageclear / gameover / victory)', () => {
      expect(canToggleCoop('playing')).toBe(false)
      expect(canToggleCoop('stageclear')).toBe(false)
      expect(canToggleCoop('gameover')).toBe(false)
      expect(canToggleCoop('victory')).toBe(false)
    })
  })

  describe('督战 Supervise — canToggleSpectate', () => {
    it('accepts the MISSION FAILED (recovery) screen so supervise can be armed before retrying', () => {
      expect(canToggleSpectate('recovery')).toBe(true)
    })

    it('accepts menu and paused', () => {
      expect(canToggleSpectate('menu')).toBe(true)
      expect(canToggleSpectate('paused')).toBe(true)
    })

    it('rejects live play and terminal states (playing / stageclear / gameover / victory)', () => {
      expect(canToggleSpectate('playing')).toBe(false)
      expect(canToggleSpectate('stageclear')).toBe(false)
      expect(canToggleSpectate('gameover')).toBe(false)
      expect(canToggleSpectate('victory')).toBe(false)
    })
  })

  describe('Replay Browser — isReplayBrowserBlocked', () => {
    it('is NEVER blocked on the MISSION FAILED (recovery) screen', () => {
      expect(isReplayBrowserBlocked('recovery')).toBe(false)
    })

    it('is never blocked on any screen — the browser layers over any static state', () => {
      for (const state of ALL_STATES) {
        expect(isReplayBrowserBlocked(state)).toBe(false)
      }
    })
  })
})
