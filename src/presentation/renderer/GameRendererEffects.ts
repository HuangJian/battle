// ================================================================
// EffectsRenderSlice — extracted from the former GameRendererEffects.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Bodies moved verbatim:
// `this.<slice-own>` stayed; everything else goes through the owning
// core instance back-reference (`this.r`).
// ================================================================
import type { World } from '../../game/World'
import { FIELD, TANK, POPUP_DURATION_MS } from '../../constants'
import type { GameRendererCore } from './GameRendererCore'

export class EffectsRenderSlice {
  constructor(private r: GameRendererCore) {}
  // ---- Explosions ----

  renderExplosions(world: World): void {
    const artist = this.r.artist
    const exps = world.explosions
    for (let i = 0; i < exps.length; i++) {
      const exp = exps[i]
      const progress = 1 - exp.timer / exp.maxTimer
      artist.drawExplosion(exp.x, exp.y, exp.size, progress, exp.kind)
    }
  }

  // ---- Particles (batched by type to minimize state changes) ----

  renderParticles(): void {
    const ctx = this.r.ctx
    const pool = this.r.particles.pool
    const count = this.r.particles.activeCount
    // Common case: no live particles. Skip five loop set-ups and — more
    // importantly — the unconditional `setTransform` below, which is a real
    // Skia/napi call. Still normalize globalAlpha exactly as the full path does,
    // so a leftover alpha from an earlier stage cannot bleed into popups.
    if (count === 0) {
      ctx.globalAlpha = 1
      return
    }

    // Pass 1: spark particles (fillRect — batch fillStyle changes)
    let lastFill = ''
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'spark') continue
      ctx.globalAlpha = p.life / p.maxLife
      if (p.color !== lastFill) {
        ctx.fillStyle = p.color
        lastFill = p.color
      }
      ctx.fillRect(p.x - p.size / 2, p.y - p.size / 2, p.size, p.size)
    }

    // Low-quality mode: skip the four decorative particle passes below (debris,
    // smoke, ring, flash). These use expensive per-particle path rasterization
    // (`beginPath`+`arc`+`fill`/`stroke`) or per-particle `setTransform`+`rotate`
    // — the dominant render cost during explosions on software rasterizers (old
    // machines without GPU). The explosion sprite itself (`renderExplosions`)
    // is still drawn, so the event remains clearly visible; sparks (pass 1) are
    // retained as hit-direction feedback. This is the single largest lowQuality
    // saving during the burst-heavy frames that would otherwise drop FPS.
    if (this.r.lowQuality) {
      ctx.globalAlpha = 1
      return
    }

    // Pass 2: debris particles (rotated). Use setTransform directly instead of
    // save()/restore() per particle — save() allocates a graphics-state object
    // on every call, which is GC pressure during explosions (lots of debris).
    let drewDebris = false
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'debris') continue
      drewDebris = true
      ctx.globalAlpha = p.life / p.maxLife
      ctx.fillStyle = p.color
      // Equivalent to translate(p) then rotate, without pushing a saved state:
      // screen = dpr * (local + p + cameraOffset).
      ctx.setTransform(
        this.r._baseDpr,
        0,
        0,
        this.r._baseDpr,
        (p.x + this.r._baseCamX) * this.r._baseDpr,
        (p.y + this.r._baseCamY) * this.r._baseDpr,
      )
      ctx.rotate(p.rotation)
      ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size)
    }
    // Restore base transform for the remaining passes (smoke/ring/flash/popups).
    // Only needed if pass 2 actually moved the transform — a `setTransform` is a
    // real napi/Skia call (~300ns), and on the common frame there is no debris.
    if (drewDebris) {
      ctx.setTransform(
        this.r._baseDpr,
        0,
        0,
        this.r._baseDpr,
        this.r._baseCamX * this.r._baseDpr,
        this.r._baseCamY * this.r._baseDpr,
      )
    }

    // Pass 3: smoke particles (arc fill — batch by minimizing fillStyle changes)
    lastFill = ''
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'smoke') continue
      const alpha = p.life / p.maxLife
      ctx.globalAlpha = alpha * 0.4
      if (p.color !== lastFill) {
        ctx.fillStyle = p.color
        lastFill = p.color
      }
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * (1 + (1 - alpha) * 0.5), 0, Math.PI * 2)
      ctx.fill()
    }

    // Pass 4: ring particles (arc stroke)
    let lastStroke = ''
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'ring') continue
      const alpha = p.life / p.maxLife
      ctx.globalAlpha = alpha
      if (p.color !== lastStroke) {
        ctx.strokeStyle = p.color
        lastStroke = p.color
      }
      ctx.lineWidth = Math.max(1, p.size * alpha)
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * (1 + (1 - alpha) * 2), 0, Math.PI * 2)
      ctx.stroke()
    }

    // Pass 5: flash particles (arc fill)
    lastFill = ''
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'flash') continue
      const alpha = p.life / p.maxLife
      ctx.globalAlpha = alpha * 0.8
      if (p.color !== lastFill) {
        ctx.fillStyle = p.color
        lastFill = p.color
      }
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * alpha, 0, Math.PI * 2)
      ctx.fill()
    }

    ctx.globalAlpha = 1
  }

  // ---- Score Popups ----

  renderPopups(world: World): void {
    // Popups are transient (briefly after a kill). Skip entirely on the common
    // frame where there are none — avoids forcing a `ctx.font` parse and two
    // `textAlign` state writes 60×/sec for no work.
    if (world.popups.length === 0) return
    const ctx = this.r.ctx
    ctx.font = 'bold 11px "Courier New", monospace'
    ctx.textAlign = 'center'
    const popups = world.popups
    const POPUP_FADE_MS = 500 // final third of POPUP_DURATION_MS fades out
    for (let i = 0; i < popups.length; i++) {
      const popup = popups[i]
      const alpha = Math.min(1, popup.timer / POPUP_FADE_MS)
      const offsetY = (1 - popup.timer / POPUP_DURATION_MS) * 20
      ctx.globalAlpha = alpha
      ctx.fillStyle = 'rgba(0,0,0,0.6)'
      ctx.fillText(popup.text, popup.x + TANK / 2 + 1, popup.y - 2 - offsetY + 1)
      ctx.fillStyle = world.theme.hudAccent
      ctx.fillText(popup.text, popup.x + TANK / 2, popup.y - 2 - offsetY)
    }
    ctx.globalAlpha = 1
    ctx.textAlign = 'left'
  }

  // ---- Vignette (cached offscreen canvas) ----

  drawVignette(world: World): void {
    const ctx = this.r.ctx

    // Rebuild vignette cache when theme changes
    if (this.r.vignetteDirty || this.r.cachedTheme !== world.theme) {
      const vctx = this.r.vignetteCtx
      vctx.clearRect(0, 0, FIELD, FIELD)
      const gradient = vctx.createRadialGradient(
        FIELD / 2,
        FIELD / 2,
        FIELD * 0.35,
        FIELD / 2,
        FIELD / 2,
        FIELD * 0.75,
      )
      gradient.addColorStop(0, 'rgba(0,0,0,0)')
      gradient.addColorStop(1, world.theme.vignetteColor)
      vctx.fillStyle = gradient
      vctx.fillRect(0, 0, FIELD, FIELD)
      this.r.cachedTheme = world.theme
      this.r.vignetteDirty = false
    }

    // Single full-field composite. NOTE: a sub-rect "ring" blit (skipping the
    // fully-transparent center) was prototyped and *rejected* — in the Skia
    // backend the 9-arg drawImage pays an extractSubset overhead that makes it
    // ~2-3× slower than this whole-image fast path, i.e. a regression. The
    // transparent center is a no-op source-over here, so the full blit is both
    // correct and the fastest path. See DECISIONS.md §10 (render perf).
    ctx.drawImage(this.r.vignetteCanvas, 0, 0, FIELD, FIELD)
  }
}
