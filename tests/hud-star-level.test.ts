import { describe, it, expect } from 'bun:test'
import { formatStarLevel, MAX_HUD_STARS, HudView } from '../src/presentation/ui/HudView'
import { World } from '../src/game/World'

/**
 * HUD star-level clamp (HUD redesign 2026-09): non-classic star levels grow
 * WITHOUT bound, so an uncapped string of ★ would widen the HUD row and — in
 * the old stacked layout — wrap it, stretching the bar and shrinking the
 * playfield. The HUD now renders at most MAX_HUD_STARS stars plus a `+N`
 * overflow counter. Pure function + happy-dom DOM application.
 */

/** Same element factory UIManager hands to HudView in production. */
function makeCreateElement(tag: string, className: string): HTMLElement {
  const el = document.createElement(tag)
  el.className = className
  return el
}

describe('formatStarLevel — HUD star clamp (pure)', () => {
  it('renders -- at zero and plain stars up to the cap', () => {
    expect(formatStarLevel(0)).toBe('--')
    expect(formatStarLevel(1)).toBe('★')
    expect(formatStarLevel(MAX_HUD_STARS)).toBe('★'.repeat(MAX_HUD_STARS))
    expect(formatStarLevel(-3)).toBe('--') // defensive: never negative
  })

  it('clamps beyond the cap to cap stars + overflow counter', () => {
    expect(formatStarLevel(MAX_HUD_STARS + 1)).toBe('★'.repeat(MAX_HUD_STARS) + '+1')
    expect(formatStarLevel(MAX_HUD_STARS + 7)).toBe('★'.repeat(MAX_HUD_STARS) + '+7')
    expect(formatStarLevel(99)).toBe('★'.repeat(MAX_HUD_STARS) + '+94')
  })

  it('never renders an unbounded star string (the HUD-widening regression)', () => {
    for (let lvl = 0; lvl <= 200; lvl++) {
      const s = formatStarLevel(lvl)
      expect(s.length).toBeLessThanOrEqual(MAX_HUD_STARS + 4)
    }
  })
})

describe('HudView star element (happy-dom)', () => {
  it('syncWorld writes the clamped star string into the DOM', () => {
    const hud = new HudView(makeCreateElement, () => {})
    const world = new World()
    world.playerLevel = 12
    hud.syncWorld(world)
    const star = hud.el.querySelector('[data-hud="star"]')!
    expect(star.textContent).toBe('★'.repeat(MAX_HUD_STARS) + '+7')
  })

  it('a level within the cap renders plainly (no overflow counter)', () => {
    const hud = new HudView(makeCreateElement, () => {})
    const world = new World()
    world.playerLevel = 3
    hud.syncWorld(world)
    const star = hud.el.querySelector('[data-hud="star"]')!
    expect(star.textContent).toBe('★★★')
  })
})
