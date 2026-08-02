import type { Direction } from '../../constants'
import { DIR_TO_INDEX } from './SpriteCache'
import { TANK_KEY_MAP, AURA_CONFIGS, auraBucket, drawAllyAuraPaths } from './SpriteArtistCore'
import type { SpriteArtistCore } from './SpriteArtistCore'

type Ctor<T = object> = new (...args: any[]) => T

export function SpriteArtistTanksMixin<TBase extends Ctor<SpriteArtistCore>>(Base: TBase) {
  return class SpriteArtistTanks extends Base {
    // ================================================================
    // Tanks
    // ================================================================

    /**
     * Ground shadow drawn UNDER a tank, in world (non-rotated) space. Tank
     * sprites are pre-rotated per facing direction, so any shadow baked into the
     * sprite would spin with the body; this keeps the contact shadow fixed at the
     * tank's footprint regardless of direction.
     */
    private drawTankShadow(x: number, y: number, size: number): void {
      if (this.lowQuality) return // decorative — skipped in Performance Mode
      const ctx = this.ctx
      const cx = x + size / 2
      // Contact shadow sits at the BOTTOM of the footprint, low enough that a
      // clear crescent peeks out below the tank body (not hidden under it).
      const cy = y + size * 0.95
      // Only `fillStyle` is mutated here, so a cheap save/restore of that one
      // property replaces a full `save()`/`restore()` graphics-state push (R3 / P1-D).
      const prev = ctx.fillStyle
      ctx.fillStyle = 'rgba(0,0,0,0.22)'
      ctx.beginPath()
      ctx.ellipse(cx, cy, size * 0.45, size * 0.126, 0, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = prev
    }

    drawTank(
      x: number,
      y: number,
      size: number,
      dir: Direction,
      bodyColor: string,
      turretColor: string,
      animFrame: number,
      level: number = 0,
    ): void {
      const ctx = this.ctx
      const s = size / 8

      ctx.save()
      ctx.translate(x + size / 2, y + size / 2)
      const rot =
        dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
      ctx.rotate(rot)
      ctx.translate(-size / 2, -size / 2)

      // Treads (left and right)
      ctx.fillStyle = '#303030'
      ctx.fillRect(0, 0, s, size)
      ctx.fillRect(size - s, 0, s, size)

      // Tread details (animated)
      ctx.fillStyle = '#505050'
      for (let i = 0; i < 8; i++) {
        const yy = i * s + (animFrame ? 0 : s / 2)
        if (yy < size) {
          ctx.fillRect(0, yy, s, s / 2)
          ctx.fillRect(size - s, yy, s, s / 2)
        }
      }

      // Tread highlights
      ctx.fillStyle = '#606060'
      ctx.fillRect(0, 0, s, 1)
      ctx.fillRect(size - s, 0, s, 1)

      // Body
      ctx.fillStyle = bodyColor
      ctx.fillRect(s, s * 2, size - s * 2, size - s * 4)

      // Body highlight (top)
      ctx.fillStyle = 'rgba(255,255,255,0.15)'
      ctx.fillRect(s, s * 2, size - s * 2, s)

      // Level details
      if (level >= 1) {
        ctx.fillStyle = '#f0f0f0'
        ctx.fillRect(s * 2, s * 5, s, s)
      }
      if (level >= 2) {
        ctx.fillStyle = '#f0f0f0'
        ctx.fillRect(s * 5, s * 5, s, s)
        // Armor plate
        ctx.fillStyle = 'rgba(255,255,255,0.1)'
        ctx.fillRect(s * 2, s * 3, s * 4, s * 2)
      }
      if (level >= 3) {
        ctx.fillStyle = '#f0f0f0'
        ctx.fillRect(s * 3, s * 5, s * 2, s)
      }

      // Turret
      ctx.fillStyle = turretColor
      ctx.fillRect(s * 2, s * 2, s * 4, s * 4)

      // Turret highlight
      ctx.fillStyle = 'rgba(255,255,255,0.2)'
      ctx.fillRect(s * 2, s * 2, s * 4, s)

      // Turret center
      ctx.fillStyle = bodyColor
      ctx.fillRect(s * 3, s * 3, s * 2, s * 2)

      // Cannon
      ctx.fillStyle = turretColor
      ctx.fillRect(s * 3, 0, s * 2, s * 3)
      // Cannon tip
      ctx.fillStyle = 'rgba(0,0,0,0.3)'
      ctx.fillRect(s * 3, 0, s * 2, s / 2)

      ctx.restore()
    }

    drawPlayerTank(
      x: number,
      y: number,
      size: number,
      dir: Direction,
      level: number,
      animFrame: number,
    ): void {
      // Non-rotating ground shadow (drawn under the tank, before any path)
      this.drawTankShadow(x, y, size)

      // Fast path: use pre-rasterized + pre-rotated sprite (no save/translate/rotate/restore)
      // Skip when skipSvg is set (Classic/Neon themes use procedural fallback
      // with theme-aware colors instead of the Modern-Retro-tuned SVGs).
      const cache = this.spriteCache
      if (cache?.built && !this.skipSvg) {
        const dirIdx = DIR_TO_INDEX[dir] ?? 0
        const stage = Math.max(0, Math.min(level ?? 0, 3))
        const cs = cache.canvasSize
        const cx = x + size / 2
        const cy = y + size / 2
        // R5-B: if there's a starbuf overlay, use the lazy-built composite bitmap
        // (body + overlay in one) — 1 drawImage instead of 2.
        if (stage > 0) {
          const composite = cache.getCompositeTankSprite('tank.player1', dirIdx, 'starbuf', stage)
          if (composite) {
            this.ctx.drawImage(composite, cx - cs / 2, cy - cs / 2, cs, cs)
            return
          }
        }
        const sprite = cache.getTankSprite('tank.player1', dirIdx)
        if (sprite) {
          const ctx = this.ctx
          ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
          if (stage > 0) {
            const overlay = cache.getStarbufSprite(stage, dirIdx)
            if (overlay) ctx.drawImage(overlay, cx - cs / 2, cy - cs / 2, cs, cs)
          }
          return
        }
      }

      // SVG fallback (also skipped when skipSvg is set)
      const rot =
        dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
      if (!this.skipSvg && this.drawSvgCentered('tank.player1', x, y, size, rot, 1.28)) {
        const stage = Math.max(0, Math.min(level ?? 0, 3))
        if (stage > 0) this.drawSvgCentered(`fx.starbuf${stage}`, x, y, size, rot, 1.28)
        return
      }
      const t = this.theme
      const body = level >= 3 ? t.playerBody3 : level >= 2 ? t.playerBody2 : t.playerBody
      this.drawTank(x, y, size, dir, body, t.playerTurret, animFrame, level)
    }

    drawPlayer2Tank(
      x: number,
      y: number,
      size: number,
      dir: Direction,
      level: number,
      animFrame: number,
    ): void {
      // Lie-Back-Win-Mode: God AI tank uses the tank.player2 sprite.
      this.drawTankShadow(x, y, size)
      const cache = this.spriteCache
      if (cache?.built && !this.skipSvg) {
        const dirIdx = DIR_TO_INDEX[dir] ?? 0
        const stage = Math.max(0, Math.min(level ?? 0, 3))
        const cs = cache.canvasSize
        const cx = x + size / 2
        const cy = y + size / 2
        // R5-B: composite body + starbuf overlay into one blit.
        if (stage > 0) {
          const composite = cache.getCompositeTankSprite('tank.player2', dirIdx, 'starbuf', stage)
          if (composite) {
            this.ctx.drawImage(composite, cx - cs / 2, cy - cs / 2, cs, cs)
            return
          }
        }
        const sprite = cache.getTankSprite('tank.player2', dirIdx)
        if (sprite) {
          this.ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
          if (stage > 0) {
            const overlay = cache.getStarbufSprite(stage, dirIdx)
            if (overlay) this.ctx.drawImage(overlay, cx - cs / 2, cy - cs / 2, cs, cs)
          }
          return
        }
      }
      // SVG fallback: use tank.player2 asset.
      const rot =
        dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
      if (!this.skipSvg && this.drawSvgCentered('tank.player2', x, y, size, rot, 1.28)) {
        const stage = Math.max(0, Math.min(level ?? 0, 3))
        if (stage > 0) this.drawSvgCentered(`fx.starbuf${stage}`, x, y, size, rot, 1.28)
        return
      }
      // Procedural fallback — use silver/green colors for player2.
      const t = this.theme
      this.drawTank(x, y, size, dir, t.playerBody2, t.playerTurret, animFrame, level)
    }

    drawEnemyTank(
      x: number,
      y: number,
      size: number,
      dir: Direction,
      kind: string,
      animFrame: number,
      flash: boolean,
      hp: number,
      hitStage = 0,
      isCommander = false,
    ): void {
      const key = TANK_KEY_MAP[kind] ?? 'tank.basic'

      // Non-rotating ground shadow (drawn under the tank, before any path)
      this.drawTankShadow(x, y, size)

      // Fast path: use pre-rasterized + pre-rotated sprite
      // Skip when skipSvg is set (Classic/Neon themes use procedural fallback
      // with theme-aware colors instead of the Modern-Retro-tuned SVGs).
      const cache = this.spriteCache
      if (cache?.built && !this.skipSvg) {
        const dirIdx = DIR_TO_INDEX[dir] ?? 0
        const stage = Math.max(0, Math.min(hitStage, 4))
        const cs = cache.canvasSize
        const cx = x + size / 2
        const cy = y + size / 2
        const ctx = this.ctx
        // R5-B: if there's a hit overlay, use the lazy-built composite bitmap
        // (body + overlay in one) — 1 drawImage instead of 2.
        if (stage > 0) {
          const composite = cache.getCompositeTankSprite(key, dirIdx, 'hit', stage)
          if (composite) {
            ctx.drawImage(composite, cx - cs / 2, cy - cs / 2, cs, cs)
            if (isCommander) this.drawCommanderAura(x, y, size, animFrame)
            return
          }
        }
        const sprite = cache.getTankSprite(key, dirIdx)
        if (sprite) {
          ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
          if (stage > 0) {
            const overlay = cache.getHitSprite(stage, dirIdx)
            if (overlay) ctx.drawImage(overlay, cx - cs / 2, cy - cs / 2, cs, cs)
          }
          // Rank insignia (Rookie/Soldier/Veteran) is now drawn by the caller
          // LAST (see `drawInsignia`) so it sits above the HP level border,
          // bonus frame, and shield. Commanders draw the crown INSTEAD
          // (crown-xor-insignia, no stacking); None draws nothing (plan §6).
          // Commander visual decoration (prominent aura)
          if (isCommander) {
            this.drawCommanderAura(x, y, size, animFrame)
          }
          return
        }
      }

      // SVG fallback (also skipped when skipSvg is set)
      const rot =
        dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
      if (!this.skipSvg && this.drawSvgCentered(key, x, y, size, rot, 1.28)) {
        const stage = Math.max(0, Math.min(hitStage, 4))
        if (stage > 0) this.drawSvgCentered(`fx.hit${stage}`, x, y, size, rot, 1.28)
        // Insignia drawn by the caller last (see `drawInsignia`).
        if (isCommander) this.drawCommanderAura(x, y, size, animFrame)
        return
      }
      const t = this.theme
      let body: string
      let turret: string

      switch (kind) {
        case 'basic':
          body = t.enemyBasic
          turret = '#e0e0e0'
          break
        case 'fast':
          body = t.enemyFast
          turret = '#e0ffff'
          break
        case 'power':
          body = t.enemyPower
          turret = '#ffe0ff'
          break
        case 'armor':
          body = flash ? t.enemyArmorFlash : hp <= 1 ? t.enemyArmorFlash : t.enemyArmor
          turret = '#ffffe0'
          break
        default:
          body = t.enemyBasic
          turret = '#e0e0e0'
      }

      this.drawTank(x, y, size, dir, body, turret, animFrame, 0)
      if (isCommander) {
        this.drawCommanderAura(x, y, size, animFrame)
      }
    }

    /**
     * Draws the 天降神兵 allied guard — a TANK (same silhouette as every other
     * unit so it reads as a real combatant) but with a distinct PURPLE body and a
     * shield emblem on the turret in place of the player's star. Uses the
     * dedicated `tank.ally` sprite. Rotates to face direction like any tank.
     *
     * The underlying `kind` still governs HP/AI behaviour, but every ally reads as
     * the same friendly purple guard. Allies deliberately do NOT draw the enemy
     * rank insignia or commander crown — their friendly status is conveyed by the
     * purple body + shield emblem + ally aura.
     */
    drawAllyTank(x: number, y: number, size: number, dir: Direction, animFrame: number): void {
      const key = 'tank.ally'

      // Non-rotating ground shadow (same as every other tank)
      this.drawTankShadow(x, y, size)

      // Fast path: pre-rasterized + pre-rotated sprite
      // Skip when skipSvg is set (Classic/Neon themes use procedural fallback).
      const cache = this.spriteCache
      if (cache?.built && !this.skipSvg) {
        const dirIdx = DIR_TO_INDEX[dir] ?? 0
        const sprite = cache.getTankSprite(key, dirIdx)
        if (sprite) {
          const cs = cache.canvasSize
          const cx = x + size / 2
          const cy = y + size / 2
          this.ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
          return
        }
      }

      // SVG fallback (also skipped when skipSvg is set)
      const rot =
        dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
      if (!this.skipSvg && this.drawSvgCentered(key, x, y, size, rot, 1.28)) return

      // Procedural fallback — purple ally tank (hardcoded; the real path is the
      // pre-rasterized purple sprite above, so theming the fallback isn't needed).
      this.drawTank(x, y, size, dir, '#8A4FD8', '#A06BE8', animFrame, 0)
    }

    /**
     * Draw the allied-guard aura — a soft pulsing purple ring + a small upward
     * chevron "friendly beacon" so the ally stays unmistakable on a busy field
     * (deliberately distinct from the gold enemy commander crown).
     *
     * R3: fast path blits a pre-rendered bitmap (16 pulse buckets) — 1 drawImage
     * replaces 2 path ops + manual property save/restore. Fallback draws paths
     * directly when the SpriteCache is not built.
     */
    drawAllyAura(x: number, y: number, size: number, frame: number): void {
      const cfg = AURA_CONFIGS.ally
      const cache = this.spriteCache
      if (cache?.built) {
        const sprite = cache.getAuraSprite('ally', auraBucket(frame, cfg.freq))
        if (sprite) {
          this.ctx.drawImage(sprite, x - cfg.offset, y - cfg.offset, cfg.canvasSize, cfg.canvasSize)
          return
        }
      }
      const ctx = this.ctx
      const pulse = Math.sin(frame * cfg.freq) * 0.5 + 0.5
      const prevStroke = ctx.strokeStyle
      const prevLW = ctx.lineWidth
      const prevAlpha = ctx.globalAlpha
      const prevFill = ctx.fillStyle
      drawAllyAuraPaths(ctx, x, y, size, pulse)
      ctx.strokeStyle = prevStroke
      ctx.lineWidth = prevLW
      ctx.globalAlpha = prevAlpha
      ctx.fillStyle = prevFill
    }

    /**
     * Draw the rank insignia (Rookie/Soldier/Veteran) as a standalone,
     * caller-driven pass. Kept OUT of `drawEnemyTank` so the renderer can
     * invoke it LAST — guaranteeing it renders above the HP level border,
     * the bonus frame, and the shield bubble (user: z-index above HP 等级边框).
     * Commanders draw the crown instead (crown-xor-insignia); None draws nothing.
     * The badge is enlarged 1.5× then a further 1.4× over the base
     * (cs/6 → cs/4 → ~cs/2.86) and "rides on" the tank's top-right corner
     * — its center sits on the corner point, so it straddles the edge (half
     * inside, half outside the hull). It is rotated 180° about its own center.
     */
    drawInsignia(x: number, y: number, size: number, level: string, isCommander = false): void {
      if (isCommander || level === 'none') return
      const cache = this.spriteCache
      const cs = cache?.canvasSize ?? Math.ceil(size * Math.SQRT2)
      const ins = (cs / 6) * 1.5 * 1.4 // base 1/6, +1.5×, +1.4×
      const cx = x + size / 2
      const cy = y + size / 2
      // Ride on the top-right corner: badge center on the corner point.
      const ix = cx + size / 2 - ins / 2
      const iy = cy - size / 2 - ins / 2
      if (cache?.built) {
        const img = cache.getInsigniaSprite(level)
        if (img) {
          // Sprite is pre-rotated 180° at bake time (SpriteCache.renderRotated),
          // so a plain drawImage suffices — no save/translate/rotate/restore.
          this.ctx.drawImage(img, ix, iy, ins, ins)
          return
        }
      }
      // SVG fallback — drawSvgCentered rotates about the badge center.
      this.drawSvgCentered(`fx.insignia.${level}`, ix, iy, ins, Math.PI, 1)
    }
  }
}
