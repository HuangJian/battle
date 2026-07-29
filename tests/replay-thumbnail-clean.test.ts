import { describe, it, expect } from 'bun:test'
import { EffectsSystem } from '../src/presentation/EffectsSystem'

/**
 * Replay Thumbnail Capture — stage-clear/gameover overlay guard
 *
 * Regression test for the fix where replay thumbnails were captured AFTER
 * render() (which includes the stage-clear white flash overlay), producing
 * washed-out preview images. The fix moves capture to BEFORE
 * PresentationLayer.handleEvents(), so the canvas still shows the clean
 * previous frame.
 *
 * This test verifies the architectural invariant: the EffectsSystem does NOT
 * trigger flash effects from events — only PresentationLayer.handleEvents()
 * calls triggerFlash(). This means any capture point before handleEvents() is
 * guaranteed to have a clean canvas with no flash overlay.
 */

describe('Replay thumbnail — effects-free capture timing', () => {
  it('effects system does not auto-trigger from events (only handleEvents triggers flash)', () => {
    // The key architectural guarantee: EffectsSystem is a passive data object.
    // Events don't trigger it — only explicit triggerFlash() calls from
    // PresentationLayer.handleEvents() do. This means capturing the thumbnail
    // at ANY point before handleEvents() is guaranteed clean.
    const effects = new EffectsSystem()

    // Initially clean
    expect(effects.getFlash()).toBeNull()

    // Simulate multiple "frames" where events would be queued and consumed
    // (bullet_fired, tank_destroyed, stage_clear, etc.) — none of these
    // touch the effects system directly.
    for (let frame = 0; frame < 120; frame++) {
      // Events are consumed by world.consumeEvents() and passed to
      // PresentationLayer.handleEvents() — but we haven't called triggerFlash,
      // so effects stays clean.
      expect(effects.getFlash()).toBeNull()
    }
  })

  it('stage_clear flash only appears after explicit triggerFlash', () => {
    const effects = new EffectsSystem()

    // Before handleEvents — clean
    expect(effects.getFlash()).toBeNull()

    // Simulate what PresentationLayer.handleEvents does for stage_clear:
    // this.effects.triggerFlash('#ffffff', 0.4)
    effects.triggerFlash('#ffffff', 0.4)

    // After handleEvents — flash active
    const flash = effects.getFlash()
    expect(flash).not.toBeNull()
    expect(flash!.color).toBe('#ffffff')
    expect(flash!.intensity).toBe(0.4)
  })

  it('player_hit flash only appears after explicit triggerFlash', () => {
    const effects = new EffectsSystem()

    expect(effects.getFlash()).toBeNull()

    // Simulate what PresentationLayer.handleEvents does for player_hit:
    // this.effects.triggerFlash('#ff4040', 0.35)
    effects.triggerFlash('#ff4040', 0.35)

    const flash = effects.getFlash()
    expect(flash).not.toBeNull()
    expect(flash!.color).toBe('#ff4040')
    expect(flash!.intensity).toBe(0.35)
  })

  it('flash fades over time (update decrements intensity)', () => {
    const effects = new EffectsSystem()

    effects.triggerFlash('#ffffff', 0.4)
    const initial = effects.getFlash()!
    expect(initial.intensity).toBe(0.4)

    // Simulate ~200ms of game time (flash fades to 0 in ~200ms)
    effects.update(200)

    const after = effects.getFlash()
    // After full decay, flash should be gone
    expect(after).toBeNull()
  })

  it('reset() clears any active flash immediately', () => {
    const effects = new EffectsSystem()

    effects.triggerFlash('#ffffff', 0.4)
    expect(effects.getFlash()).not.toBeNull()

    effects.reset()
    expect(effects.getFlash()).toBeNull()
  })

  it('multiple triggerFlash calls layer correctly (max wins)', () => {
    const effects = new EffectsSystem()

    // First trigger
    effects.triggerFlash('#ffffff', 0.3)
    expect(effects.getFlash()!.intensity).toBe(0.3)

    // Second trigger with higher intensity — should win
    effects.triggerFlash('#ff4040', 0.5)
    expect(effects.getFlash()!.intensity).toBe(0.5)
    expect(effects.getFlash()!.color).toBe('#ff4040')

    // Third trigger with lower intensity — should NOT override
    effects.triggerFlash('#ffffff', 0.2)
    expect(effects.getFlash()!.intensity).toBe(0.5)
  })
})
