import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const svg = readFileSync(
  fileURLToPath(new URL('../src/assets/sprites/fx_shield.svg', import.meta.url)),
  'utf8',
)

/**
 * Regression guard for the shield visual.
 *
 * The shield sprite is an overlay drawn ON TOP of the (rotated) tank, so it
 * must be a direction-agnostic energy bubble. An earlier hand-edit baked a
 * full tank — treads + body + barrel facing up — into fx_shield.svg, which
 * overlaid a static "cannon-up" shape on the player every frame
 * ("玩家获得护盾后，一直叠加炮口朝上的视觉形状"). This test pins the sprite to
 * a pure bubble.
 */
describe('Shield sprite (fx_shield.svg)', () => {
  it('is a pure energy bubble — no embedded tank silhouette', () => {
    // Tank-specific gradients / geometry from the old broken sprite.
    expect(svg).not.toContain('gradient_1') // tank tread/body gradient defs
    expect(svg).not.toContain('width="48" height="58"') // tank body rect
    expect(svg).not.toContain('#F4C430') // tank body gold
    expect(svg).not.toContain('gradient_3') // tank barrel gradient def

    // Sanity: the bubble itself is still present.
    expect(svg).toContain('M36 0C60 0') // dome fill path
    expect(svg).toContain('opacity="0.9"') // outer hexagon edge highlight
  })
})
