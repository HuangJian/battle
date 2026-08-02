import type { World } from '../../game/World'
import { FIELD, TANK } from '../../constants'
import type { GameRendererCore } from './GameRendererCore'

type Ctor<T = object> = new (...args: any[]) => T

export function GameRendererEffectsMixin<TBase extends Ctor<GameRendererCore>>(Base: TBase) {
  return class GameRendererEffects extends Base {
    // ---- Explosions ----

    protected renderExplosions(world: World): void {
      const artist = this.artist
      const exps = world.explosions
      for (let i = 0; i < exps.length; i++) {
        const exp = exps[i]
        const progress = 1 - exp.timer / exp.maxTimer
        artist.drawExplosion(exp.x, exp.y, exp.size, progress, exp.kind)
      }
    }

    // ---- Particles (batched by type to minimize state changes) ----

    protected renderParticles(): void {
      const ctx = this.ctx
      const pool = this.particles.pool
      const count = this.particles.activeCount
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
      if (this.lowQuality) {
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
          this._baseDpr,
          0,
          0,
          this._baseDpr,
          (p.x + this._baseCamX) * this._baseDpr,
          (p.y + this._baseCamY) * this._baseDpr,
        )
        ctx.rotate(p.rotation)
        ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size)
      }
      // Restore base transform for the remaining passes (smoke/ring/flash/popups).
      // Only needed if pass 2 actually moved the transform — a `setTransform` is a
      // real napi/Skia call (~300ns), and on the common frame there is no debris.
      if (drewDebris) {
        ctx.setTransform(
          this._baseDpr,
          0,
          0,
          this._baseDpr,
          this._baseCamX * this._baseDpr,
          this._baseCamY * this._baseDpr,
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

    protected renderPopups(world: World): void {
      // Popups are transient (briefly after a kill). Skip entirely on the common
      // frame where there are none — avoids forcing a `ctx.font` parse and two
      // `textAlign` state writes 60×/sec for no work.
      if (world.popups.length === 0) return
      const ctx = this.ctx
      ctx.font = 'bold 11px "Courier New", monospace'
      ctx.textAlign = 'center'
      const popups = world.popups
      for (let i = 0; i < popups.length; i++) {
        const popup = popups[i]
        const alpha = Math.min(1, popup.timer / 500)
        const offsetY = (1 - popup.timer / 1500) * 20
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

    protected drawVignette(world: World): void {
      const ctx = this.ctx

      // Rebuild vignette cache when theme changes
      if (this.vignetteDirty || this.cachedTheme !== world.theme) {
        const vctx = this.vignetteCtx
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
        this.cachedTheme = world.theme
        this.vignetteDirty = false
      }

      // Single full-field composite. NOTE: a sub-rect "ring" blit (skipping the
      // fully-transparent center) was prototyped and *rejected* — in the Skia
      // backend the 9-arg drawImage pays an extractSubset overhead that makes it
      // ~2-3× slower than this whole-image fast path, i.e. a regression. The
      // transparent center is a no-op source-over here, so the full blit is both
      // correct and the fastest path. See DECISIONS.md §10 (render perf).
      ctx.drawImage(this.vignetteCanvas, 0, 0, FIELD, FIELD)
    }
  }
}
