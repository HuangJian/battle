import type { World } from '../game/World'
import type { ThemeColors } from '../types'
import { CELL, GRID, FIELD, CANVAS_WIDTH, CANVAS_HEIGHT, HUD_WIDTH, TANK } from '../constants'
import { SpriteFactory } from './Sprites'

/**
 * Renderer — reads the World and draws everything to canvas.
 * Never modifies the World.
 */
export class Renderer {
  ctx: CanvasRenderingContext2D
  canvas: HTMLCanvasElement
  sprites: SpriteFactory

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('Canvas 2D context not available')
    this.ctx = ctx
    this.ctx.imageSmoothingEnabled = false
    this.sprites = new SpriteFactory(ctx, {} as ThemeColors)
  }

  setTheme(theme: ThemeColors): void {
    this.sprites.setTheme(theme)
  }

  render(world: World): void {
    this.setTheme(world.theme)
    const ctx = this.ctx

    // Clear
    ctx.fillStyle = world.theme.bg
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)

    if (world.state === 'menu') {
      this.renderMenu(world)
      return
    }

    // Playfield background
    ctx.fillStyle = world.theme.bg
    ctx.fillRect(0, 0, FIELD, FIELD)

    // 1. Terrain (except forest)
    this.renderTerrain(world, false)

    // 2. Tanks
    this.renderTanks(world)

    // 3. Bullets
    this.renderBullets(world)

    // 4. Power-ups
    this.renderPowerUps(world)

    // 5. Forest (on top of tanks for hiding)
    this.renderTerrain(world, true)

    // 6. Explosions
    this.renderExplosions(world)

    // 7. Score popups
    this.renderPopups(world)

    // 8. HUD
    this.renderHUD(world)

    // 9. Overlays (pause, game over, stage clear)
    this.renderOverlays(world)
  }

  // ---- Terrain ----

  private renderTerrain(world: World, forestOnly: boolean): void {
    const tm = world.tileMap
    const frame = world.frame

    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        const type = tm.get(c, r)
        if (type === 'empty') continue
        if (type === 'forest' && !forestOnly) continue
        if (type !== 'forest' && forestOnly) continue

        const x = c * CELL
        const y = r * CELL

        switch (type) {
          case 'brick':
            this.sprites.drawBrick(x, y, CELL)
            break
          case 'steel':
            this.sprites.drawSteel(x, y, CELL)
            break
          case 'water':
            this.sprites.drawWater(x, y, CELL, frame)
            break
          case 'forest':
            this.sprites.drawForest(x, y, CELL)
            break
          case 'ice':
            this.sprites.drawIce(x, y, CELL)
            break
          case 'base':
            this.sprites.drawBase(x, y, CELL, false)
            break
        }
      }
    }

    // Check if base is destroyed and draw destroyed base
    if (!forestOnly && world.tileMap.isBaseDestroyed()) {
      // Find where base was (we know it's been destroyed, draw ruins)
      // Base was at tile (6, 12) = sub-blocks (12-13, 24-25)
      for (let r = 24; r <= 25; r++) {
        for (let c = 12; c <= 13; c++) {
          if (r < GRID && c < GRID) {
            this.sprites.drawBase(c * CELL, r * CELL, CELL, true)
          }
        }
      }
    }
  }

  // ---- Tanks ----

  private renderTanks(world: World): void {
    const frame = world.frame
    for (const tank of world.allTanks) {
      if (!tank.alive) continue

      // Spawn animation
      if (tank.spawnTimer > 0) {
        this.sprites.drawSpawn(tank.x, tank.y, tank.w, frame)
        continue
      }

      // Draw tank
      if (tank.isPlayer) {
        this.sprites.drawPlayerTank(tank.x, tank.y, tank.w, tank.dir, tank.level ?? 0, frame)
      } else {
        this.sprites.drawEnemyTank(
          tank.x,
          tank.y,
          tank.w,
          tank.dir,
          tank.kind,
          frame,
          (tank.flashTimer ?? 0) > 0,
          tank.hp,
          tank.maxHp,
        )
      }

      // Bonus enemy indicator (blinking red outline)
      if (tank.bonus) {
        const blink = Math.floor(frame / 8) % 2 === 0
        if (blink) {
          this.ctx.strokeStyle = '#ff4040'
          this.ctx.lineWidth = 1
          this.ctx.strokeRect(tank.x - 1, tank.y - 1, tank.w + 2, tank.h + 2)
        }
      }

      // Shield effect
      if (tank.shieldTimer && tank.shieldTimer > 0) {
        this.sprites.drawShield(tank.x, tank.y, tank.w, frame)
      }
    }
  }

  // ---- Bullets ----

  private renderBullets(world: World): void {
    for (const bullet of world.bullets) {
      if (!bullet.alive) continue
      this.sprites.drawBullet(bullet.x, bullet.y, bullet.w, bullet.dir)
    }
  }

  // ---- Power-ups ----

  private renderPowerUps(world: World): void {
    const frame = world.frame
    for (const pu of world.powerUps) {
      if (!pu.alive) continue
      this.sprites.drawPowerUp(pu.x, pu.y, pu.w, pu.type, frame)
    }
  }

  // ---- Explosions ----

  private renderExplosions(world: World): void {
    for (const exp of world.explosions) {
      const progress = 1 - exp.timer / exp.maxTimer
      this.sprites.drawExplosion(exp.x, exp.y, exp.size, progress, exp.kind)
    }
  }

  // ---- Score Popups ----

  private renderPopups(world: World): void {
    const ctx = this.ctx
    ctx.font = 'bold 10px monospace'
    ctx.textAlign = 'center'
    for (const popup of world.popups) {
      const alpha = Math.min(1, popup.timer / 500)
      ctx.globalAlpha = alpha
      ctx.fillStyle = '#ffe040'
      ctx.fillText(popup.text, popup.x + TANK / 2, popup.y - 2)
    }
    ctx.globalAlpha = 1
    ctx.textAlign = 'left'
  }

  // ---- HUD ----

  private renderHUD(world: World): void {
    const ctx = this.ctx
    const t = world.theme
    const hx = FIELD

    // Background
    ctx.fillStyle = t.hudBg
    ctx.fillRect(hx, 0, HUD_WIDTH, CANVAS_HEIGHT)

    // Enemies remaining
    ctx.fillStyle = t.hudText
    ctx.font = 'bold 8px monospace'
    ctx.textAlign = 'left'
    ctx.fillText('ENEMY', hx + 8, 16)

    // Draw enemy icons (grid of small tanks)
    const remaining = world.enemiesRemaining
    for (let i = 0; i < remaining && i < 20; i++) {
      const ex = hx + 8 + (i % 2) * 16
      const ey = 24 + Math.floor(i / 2) * 16
      ctx.fillStyle = t.enemyBasic
      ctx.fillRect(ex, ey, 10, 10)
      ctx.fillStyle = '#606060'
      ctx.fillRect(ex + 1, ey, 2, 10)
      ctx.fillRect(ex + 7, ey, 2, 10)
    }

    // Divider
    ctx.fillStyle = t.hudAccent
    ctx.fillRect(hx + 4, 200, HUD_WIDTH - 8, 1)

    // Player lives
    ctx.fillStyle = t.hudText
    ctx.fillText('LIVES', hx + 8, 216)
    ctx.fillStyle = t.playerBody
    ctx.font = 'bold 16px monospace'
    ctx.fillText(`× ${world.lives}`, hx + 8, 236)

    // Divider
    ctx.fillStyle = t.hudAccent
    ctx.fillRect(hx + 4, 250, HUD_WIDTH - 8, 1)

    // Player level (stars)
    ctx.fillStyle = t.hudText
    ctx.font = 'bold 8px monospace'
    ctx.fillText('LEVEL', hx + 8, 266)
    for (let i = 0; i < world.playerLevel + 1; i++) {
      const sx = hx + 8 + i * 12
      ctx.fillStyle = t.powerUpGlow
      ctx.font = '12px monospace'
      ctx.fillText('★', sx, 282)
    }

    // Divider
    ctx.fillStyle = t.hudAccent
    ctx.fillRect(hx + 4, 296, HUD_WIDTH - 8, 1)

    // Score
    ctx.fillStyle = t.hudText
    ctx.font = 'bold 8px monospace'
    ctx.fillText('SCORE', hx + 8, 312)
    ctx.fillStyle = t.hudAccent
    ctx.font = 'bold 14px monospace'
    ctx.fillText(String(world.score).padStart(6, '0'), hx + 8, 330)

    // High score
    ctx.fillStyle = t.hudText
    ctx.font = 'bold 8px monospace'
    ctx.fillText('HI', hx + 8, 348)
    ctx.fillStyle = t.hudAccent
    ctx.font = 'bold 12px monospace'
    ctx.fillText(String(world.highScore).padStart(6, '0'), hx + 8, 364)

    // Stage
    ctx.fillStyle = t.hudText
    ctx.font = 'bold 8px monospace'
    ctx.fillText('STAGE', hx + 8, 388)
    ctx.fillStyle = t.hudAccent
    ctx.font = 'bold 16px monospace'
    ctx.fillText(`${world.stageIndex + 1}`, hx + 8, 406)
  }

  // ---- Overlays ----

  private renderOverlays(world: World): void {
    const ctx = this.ctx

    if (world.state === 'paused') {
      ctx.fillStyle = 'rgba(0,0,0,0.6)'
      ctx.fillRect(0, 0, FIELD, FIELD)
      ctx.fillStyle = world.theme.hudAccent
      ctx.font = 'bold 24px monospace'
      ctx.textAlign = 'center'
      ctx.fillText('PAUSED', FIELD / 2, FIELD / 2)
      ctx.font = '10px monospace'
      ctx.fillStyle = world.theme.hudText
      ctx.fillText('Press P to resume', FIELD / 2, FIELD / 2 + 24)
      ctx.textAlign = 'left'
    }

    if (world.state === 'stageclear') {
      ctx.fillStyle = 'rgba(0,0,0,0.5)'
      ctx.fillRect(0, 0, FIELD, FIELD)
      ctx.fillStyle = world.theme.hudAccent
      ctx.font = 'bold 20px monospace'
      ctx.textAlign = 'center'
      ctx.fillText('STAGE CLEAR', FIELD / 2, FIELD / 2)
      ctx.font = '12px monospace'
      ctx.fillStyle = world.theme.hudText
      ctx.fillText(`Stage ${world.stageIndex + 1} Complete`, FIELD / 2, FIELD / 2 + 24)
      ctx.textAlign = 'left'
    }

    if (world.state === 'gameover') {
      ctx.fillStyle = 'rgba(0,0,0,0.7)'
      ctx.fillRect(0, 0, FIELD, FIELD)
      ctx.fillStyle = '#ff4040'
      ctx.font = 'bold 24px monospace'
      ctx.textAlign = 'center'
      ctx.fillText('GAME OVER', FIELD / 2, FIELD / 2)
      ctx.font = '10px monospace'
      ctx.fillStyle = world.theme.hudText
      ctx.fillText('Press R to restart', FIELD / 2, FIELD / 2 + 24)
      ctx.textAlign = 'left'
    }

    if (world.state === 'victory') {
      ctx.fillStyle = 'rgba(0,0,0,0.7)'
      ctx.fillRect(0, 0, FIELD, FIELD)
      ctx.fillStyle = world.theme.hudAccent
      ctx.font = 'bold 20px monospace'
      ctx.textAlign = 'center'
      ctx.fillText('VICTORY!', FIELD / 2, FIELD / 2 - 10)
      ctx.font = '12px monospace'
      ctx.fillStyle = world.theme.hudText
      ctx.fillText(`Final Score: ${world.score}`, FIELD / 2, FIELD / 2 + 16)
      ctx.fillText('Press R to play again', FIELD / 2, FIELD / 2 + 36)
      ctx.textAlign = 'left'
    }
  }

  // ---- Menu ----

  private renderMenu(world: World): void {
    const ctx = this.ctx
    const t = world.theme

    // Background
    ctx.fillStyle = t.bg
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)

    // Title
    ctx.fillStyle = t.hudAccent
    ctx.font = 'bold 28px monospace'
    ctx.textAlign = 'center'
    ctx.fillText('BATTLE CITY', CANVAS_WIDTH / 2, 100)

    ctx.fillStyle = t.hudText
    ctx.font = '10px monospace'
    ctx.fillText('Faithful to the classic. Designed for the future.', CANVAS_WIDTH / 2, 120)

    // Instructions
    ctx.font = 'bold 12px monospace'
    ctx.fillStyle = t.hudAccent
    ctx.fillText('PRESS ENTER TO START', CANVAS_WIDTH / 2, 200)

    ctx.font = '9px monospace'
    ctx.fillStyle = t.hudText
    ctx.fillText('WASD / Arrows — Move', CANVAS_WIDTH / 2, 240)
    ctx.fillText('Space — Fire', CANVAS_WIDTH / 2, 256)
    ctx.fillText('P — Pause   R — Reset', CANVAS_WIDTH / 2, 272)

    // Difficulty
    ctx.font = 'bold 10px monospace'
    ctx.fillStyle = t.hudAccent
    ctx.fillText(`Difficulty: ${world.difficulty.name}`, CANVAS_WIDTH / 2, 320)
    ctx.font = '8px monospace'
    ctx.fillStyle = t.hudText
    ctx.fillText('← → to change', CANVAS_WIDTH / 2, 336)

    // High score
    ctx.font = '9px monospace'
    ctx.fillStyle = t.hudText
    ctx.fillText(`High Score: ${world.highScore}`, CANVAS_WIDTH / 2, 380)

    ctx.textAlign = 'left'
  }
}
