// ================================================================
// TerrainSpriteSlice — extracted from the former SpriteArtistTerrain.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Bodies moved verbatim:
// `this.<slice-own>` stayed; everything else goes through the owning
// core instance back-reference (`this.r`).
// ================================================================
import { drawWaterTile } from './SpriteArtistCore'
import type { SpriteArtistCore } from './SpriteArtistCore'

export class TerrainSpriteSlice {
  constructor(private r: SpriteArtistCore) {}
  // ================================================================
  // Terrain
  // ================================================================

  drawBrick(x: number, y: number, size: number): void {
    if (!this.r.skipSvg && this.r.drawSvgCentered('terrain.brick', x, y, size)) return
    const t = this.r.theme
    const ctx = this.r.ctx
    const s = size / 4

    // Base
    ctx.fillStyle = t.brick
    ctx.fillRect(x, y, size, size)

    // Brick pattern with mortar
    ctx.fillStyle = t.brickDark
    // Horizontal mortar lines
    ctx.fillRect(x, y + s - 1, size, 1)
    ctx.fillRect(x, y + s * 2 - 1, size, 1)
    ctx.fillRect(x, y + s * 3 - 1, size, 1)
    // Vertical mortar (offset rows)
    ctx.fillRect(x + s * 2 - 1, y, 1, s)
    ctx.fillRect(x + s - 1, y + s, 1, s)
    ctx.fillRect(x + s * 3 - 1, y + s, 1, s)
    ctx.fillRect(x + s * 2 - 1, y + s * 2, 1, s)
    ctx.fillRect(x + s - 1, y + s * 3, 1, s)
    ctx.fillRect(x + s * 3 - 1, y + s * 3, 1, s)

    // Highlights on brick faces
    ctx.fillStyle = 'rgba(255,255,255,0.08)'
    ctx.fillRect(x + 1, y + 1, s - 2, s - 2)
    ctx.fillRect(x + s * 2 + 1, y + 1, s - 2, s - 2)
    ctx.fillRect(x + s + 1, y + s + 1, s - 2, s - 2)
    ctx.fillRect(x + s * 3 + 1, y + s + 1, s - 2, s - 2)
    ctx.fillRect(x + 1, y + s * 2 + 1, s - 2, s - 2)
    ctx.fillRect(x + s * 2 + 1, y + s * 2 + 1, s - 2, s - 2)
  }

  /**
   * Steel wall, auto-tiled. `n/e/s/w` are true when the orthogonal neighbour is
   * also steel. A connected patch reads as ONE reinforced wall (铜墙铁壁): the
   * interior is a seamless fill, hinges strap every internal steel-steel seam
   * together, and obvious rivets pin the four outer corners.
   */
  drawSteel(x: number, y: number, size: number, n = false, e = false, s = false, w = false): void {
    const t = this.r.theme
    const ctx = this.r.ctx
    const s4 = size / 4
    const TAU = Math.PI * 2

    // Base fill — flat (no per-tile gradient), so a patch is one continuous slab.
    ctx.fillStyle = t.steel
    ctx.fillRect(x, y, size, size)

    // Seamless brushed-metal hatch: parallel diagonal lines, period = size,
    // so the texture tiles across cells without a visible seam.
    ctx.save()
    ctx.beginPath()
    ctx.rect(x, y, size, size)
    ctx.clip()
    ctx.lineWidth = 1
    for (let b = -size; b <= size; b += s4) {
      const k = Math.round(b / s4)
      ctx.strokeStyle = k % 2 === 0 ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.05)'
      ctx.beginPath()
      ctx.moveTo(x, y + b)
      ctx.lineTo(x + size, y + size + b)
      ctx.stroke()
    }
    ctx.restore()

    // Unified patch outline + bevel — only on sides bordering a different tile.
    const dark = t.steelDark
    const light = 'rgba(255,255,255,0.22)'
    const bevel = 2
    if (!n) {
      ctx.fillStyle = light
      ctx.fillRect(x, y, size, bevel)
      ctx.fillStyle = dark
      ctx.fillRect(x, y, size, 1)
    }
    if (!s) {
      ctx.fillStyle = dark
      ctx.fillRect(x, y + size - bevel, size, bevel)
    }
    if (!w) {
      ctx.fillStyle = light
      ctx.fillRect(x, y, bevel, size)
      ctx.fillStyle = dark
      ctx.fillRect(x, y, 1, size)
    }
    if (!e) {
      ctx.fillStyle = dark
      ctx.fillRect(x + size - bevel, y, bevel, size)
    }

    // Hinges straddling each internal (steel↔steel) seam — plates bolted together.
    const cx = x + size / 2
    const cy = y + size / 2
    const hinge = (ex: number, ey: number, vertical: boolean) => {
      const len = size * 0.5
      const thick = 4
      ctx.fillStyle = dark
      if (vertical) ctx.fillRect(ex - thick / 2, ey - len / 2, thick, len)
      else ctx.fillRect(ex - len / 2, ey - thick / 2, len, thick)
      ctx.fillStyle = 'rgba(255,255,255,0.3)'
      ctx.beginPath()
      ctx.arc(ex, ey, 1.4, 0, TAU)
      ctx.fill()
      ctx.fillStyle = 'rgba(0,0,0,0.35)'
      ctx.beginPath()
      ctx.arc(ex, ey, 0.8, 0, TAU)
      ctx.fill()
    }
    if (n) hinge(cx, y, false)
    if (s) hinge(cx, y + size, false)
    if (w) hinge(x, cy, true)
    if (e) hinge(x + size, cy, true)

    // Obvious rivets pinning the patch's four outer corners.
    const rivet = (rcx: number, rcy: number) => {
      ctx.fillStyle = 'rgba(0,0,0,0.4)'
      ctx.beginPath()
      ctx.arc(rcx, rcy + 0.5, 3.2, 0, TAU)
      ctx.fill()
      ctx.fillStyle = t.steelDark
      ctx.beginPath()
      ctx.arc(rcx, rcy, 2.6, 0, TAU)
      ctx.fill()
      ctx.fillStyle = 'rgba(255,255,255,0.25)'
      ctx.beginPath()
      ctx.arc(rcx, rcy, 1.6, 0, TAU)
      ctx.fill()
      ctx.fillStyle = 'rgba(255,255,255,0.85)'
      ctx.beginPath()
      ctx.arc(rcx - 0.8, rcy - 0.8, 0.9, 0, TAU)
      ctx.fill()
    }
    const o = 4.5
    if (!n && !w) rivet(x + o, y + o)
    if (!n && !e) rivet(x + size - o, y + o)
    if (!s && !w) rivet(x + o, y + size - o)
    if (!s && !e) rivet(x + size - o, y + size - o)
  }

  drawWater(x: number, y: number, size: number, frame: number): void {
    // Fast path: pre-rasterized, phase-animated water bitmap (cheap blit, no
    // per-frame save/translate/restore). Replaces the old static-SVG path that
    // allocated a graphics state per water cell every frame and never animated.
    const cache = this.r.spriteCache
    if (cache?.built) {
      const phase = Math.floor(frame / 20) % 2
      const sprite = cache.getWaterSprite(phase)
      if (sprite) {
        this.r.ctx.drawImage(sprite, x, y, size, size)
        return
      }
    }
    // Fallback (no cache built yet): procedural animated water
    const phase = Math.floor(frame / 20) % 2
    drawWaterTile(this.r.ctx, x, y, size, this.r.theme, phase)
  }

  drawForest(x: number, y: number, size: number): void {
    if (!this.r.skipSvg && this.r.drawSvgCentered('terrain.forest', x, y, size)) return
    const t = this.r.theme
    const ctx = this.r.ctx
    const s = size / 4

    // Base
    ctx.fillStyle = t.forest
    ctx.fillRect(x, y, size, size)

    // Tree canopy clusters
    ctx.fillStyle = t.forestDark
    // Tree 1 (top-left)
    ctx.fillRect(x + s, y, s * 2, s)
    ctx.fillRect(x, y + s, s, s)
    ctx.fillRect(x + s, y + s, s * 2, s)
    // Tree 2 (bottom-right)
    ctx.fillRect(x + s * 2, y + s * 2, s * 2, s)
    ctx.fillRect(x + s * 3, y + s * 3, s, s)
    ctx.fillRect(x + s, y + s * 3, s * 2, s)

    // Highlights
    ctx.fillStyle = 'rgba(255,255,255,0.1)'
    ctx.fillRect(x + s, y, s, 1)
    ctx.fillRect(x + s * 2, y + s * 2, s, 1)
  }

  /**
   * Ice / snow field, auto-tiled. `n/e/s/w` true when the orthogonal neighbour is
   * also ice. A connected snowfield reads as ONE frozen surface: flat fill with
   * no per-tile gradient, a crack web whose fissures join across tiles (so the
   * whole patch shares one continuous network instead of per-tile snowflakes),
   * and a frost rim only around the perimeter.
   */
  drawIce(x: number, y: number, size: number, n = false, e = false, s = false, w = false): void {
    const t = this.r.theme
    const ctx = this.r.ctx
    const a = size / 3

    // Flat base — no per-tile gradient, so a snowfield is one continuous surface.
    ctx.fillStyle = t.ice
    ctx.fillRect(x, y, size, size)

    // Seamless crack web: 4 diagonal segments touching edges at 1/3 & 2/3.
    // Neighbours use the same fractions, so fissures join across tiles into one
    // connected frozen network rather than a grid of separate snowflakes.
    ctx.strokeStyle = 'rgba(255,255,255,0.45)'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, y + a)
    ctx.lineTo(x + a * 2, y) // left@1/3 -> top@2/3
    ctx.moveTo(x, y + a * 2)
    ctx.lineTo(x + a, y + size) // left@2/3 -> bottom@1/3
    ctx.moveTo(x + size, y + a)
    ctx.lineTo(x + a * 2, y + size) // right@1/3 -> bottom@2/3
    ctx.moveTo(x + size, y + a * 2)
    ctx.lineTo(x + a, y) // right@2/3 -> top@1/3
    ctx.stroke()

    // Frost rim around the patch perimeter (boundary sides only).
    ctx.fillStyle = 'rgba(255,255,255,0.4)'
    const f = 2
    if (!n) ctx.fillRect(x, y, size, f)
    if (!s) ctx.fillRect(x, y + size - f, size, f)
    if (!w) ctx.fillRect(x, y, f, size)
    if (!e) ctx.fillRect(x + size - f, y, f, size)

    // A couple of sparkles for a frosty sheen (fixed, tiling positions).
    ctx.fillStyle = 'rgba(255,255,255,0.7)'
    ctx.fillRect(x + size * 0.22, y + size * 0.28, 1, 1)
    ctx.fillRect(x + size * 0.72, y + size * 0.62, 1, 1)
  }

  /**
   * Draws the base. `x,y` is the TOP-LEFT pixel of the 2×2 base block and
   * `size` is the full block size (2×CELL). The base is a single 3D energy
   * crystal spanning the whole block (not four separate tiles) — the caller
   * (GameRenderer) only invokes this once, for the block's top-left cell.
   */
  drawBase(x: number, y: number, size: number, destroyed: boolean, damage = 0): void {
    const key = destroyed ? 'terrain.base_ruins' : 'terrain.base'
    if (!this.r.skipSvg && this.r.drawSvgCentered(key, x, y, size)) {
      if (damage > 0 && !destroyed) this.drawBaseDamage(x, y, size, damage)
      return
    }

    // Procedural fallback (only when the SVG is not yet loaded)
    const ctx = this.r.ctx
    const cx = x + size / 2
    if (destroyed) {
      ctx.fillStyle = '#5b6670'
      ctx.beginPath()
      ctx.moveTo(cx, y + size * 0.15)
      ctx.lineTo(x + size * 0.3, y + size * 0.55)
      ctx.lineTo(cx, y + size * 0.9)
      ctx.lineTo(x + size * 0.7, y + size * 0.55)
      ctx.closePath()
      ctx.fill()
      return
    }
    // Intact crystal fallback
    const g = ctx.createLinearGradient(0, y, 0, y + size)
    g.addColorStop(0, '#EAFBFF')
    g.addColorStop(1, '#3E9BE0')
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.moveTo(cx, y + size * 0.1)
    ctx.lineTo(x + size * 0.22, y + size * 0.45)
    ctx.lineTo(cx, y + size * 0.55)
    ctx.lineTo(x + size * 0.78, y + size * 0.45)
    ctx.closePath()
    ctx.fill()
    ctx.beginPath()
    ctx.moveTo(x + size * 0.22, y + size * 0.45)
    ctx.lineTo(cx, y + size * 0.55)
    ctx.lineTo(cx, y + size * 0.9)
    ctx.closePath()
    ctx.fill()
    ctx.fillStyle = '#ffffff'
    ctx.globalAlpha = 0.5
    ctx.beginPath()
    ctx.ellipse(cx, y + size * 0.5, size * 0.1, size * 0.14, 0, 0, Math.PI * 2)
    ctx.fill()
    ctx.globalAlpha = 1
    if (damage > 0) this.drawBaseDamage(x, y, size, damage)
  }

  /**
   * Deterministic (no RNG) crack + scorch overlay drawn on top of the base
   * crystal. `damage` is 0..1; more damage → more / darker cracks. Geometry is
   * derived from the crack index (never from random draws) so the overlay stays
   * stable across frames (no flicker).
   */
  drawBaseDamage(x: number, y: number, size: number, damage: number): void {
    const ctx = this.r.ctx
    const cx = x + size / 2
    const cy = y + size / 2
    const d = Math.max(0, Math.min(1, damage))

    // Save only the properties we mutate (avoids save()/restore() allocation).
    const prevAlpha = ctx.globalAlpha
    const prevFill = ctx.fillStyle
    const prevStroke = ctx.strokeStyle
    const prevLW = ctx.lineWidth
    const prevCap = ctx.lineCap
    const prevJoin = ctx.lineJoin

    // Scorch tint over the crystal body.
    ctx.globalAlpha = 0.16 + d * 0.34
    ctx.fillStyle = '#14181d'
    ctx.beginPath()
    ctx.moveTo(cx, y + size * 0.12)
    ctx.lineTo(x + size * 0.2, y + size * 0.45)
    ctx.lineTo(cx, y + size * 0.55)
    ctx.lineTo(x + size * 0.8, y + size * 0.45)
    ctx.closePath()
    ctx.fill()

    // Jagged cracks radiating in from the perimeter toward the core.
    const n = Math.max(1, Math.round(d * 6))
    ctx.strokeStyle = 'rgba(15,18,22,0.85)'
    ctx.lineWidth = Math.max(1, size * 0.03)
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    for (let i = 0; i < n; i++) {
      const ang = (Math.PI * 2 * i) / n + ((i * 2.3999632) % 1) * 0.7
      const r0 = size * 0.5
      const r1 = size * (0.1 + (i % 3) * 0.06)
      const px = cx + Math.cos(ang) * r0
      const py = cy + Math.sin(ang) * r0
      const ex = cx + Math.cos(ang) * r1
      const ey = cy + Math.sin(ang) * r1
      const kink = (i % 2 === 0 ? 1 : -1) * size * 0.09
      const mx = cx + (Math.cos(ang) * (r0 + r1)) / 2 + Math.sin(ang) * kink
      const my = cy + (Math.sin(ang) * (r0 + r1)) / 2 - Math.cos(ang) * kink
      ctx.beginPath()
      ctx.moveTo(px, py)
      ctx.lineTo(mx, my)
      ctx.lineTo(ex, ey)
      ctx.stroke()
    }

    ctx.globalAlpha = prevAlpha
    ctx.fillStyle = prevFill
    ctx.strokeStyle = prevStroke
    ctx.lineWidth = prevLW
    ctx.lineCap = prevCap
    ctx.lineJoin = prevJoin
  }
}
