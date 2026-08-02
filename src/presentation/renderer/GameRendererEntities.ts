import type { World } from '../../game/World'
import type { Tank } from '../../types'
import { POWERUP_TIMEOUT_MS } from '../../constants'
import { getHpLevel } from '../../config/hp-level'
import type { GameRendererCore } from './GameRendererCore'

type Ctor<T = object> = new (...args: any[]) => T

export function GameRendererEntitiesMixin<TBase extends Ctor<GameRendererCore>>(Base: TBase) {
  return class GameRendererEntities extends Base {
    // ---- Tanks ----

    protected renderTanks(world: World, tanks: Tank[]): void {
      const ctx = this.ctx
      const frame = world.frame
      const artist = this.artist
      for (let ti = 0; ti < tanks.length; ti++) {
        const tank = tanks[ti]
        if (!tank.alive) continue

        if (tank.spawnTimer > 0) {
          artist.drawSpawn(tank.x, tank.y, tank.w, frame)
          continue
        }

        const vc = this.animations.get(tank.id)
        const animFrame = vc ? this.animations.getFrame(vc) : (frame >> 2) & 1

        // Draw HP level visual decoration aura (Level 2~6)
        const hpLevel = getHpLevel(tank.hp)
        if (hpLevel > 1) {
          artist.drawHpLevelAura(tank.x, tank.y, tank.w, hpLevel, frame)
        }

        if (tank.allegiance === 'ally') {
          // 天降神兵 allied guard — distinct purple unit (no enemy crown/insignia).
          artist.drawAllyTank(tank.x, tank.y, tank.w, tank.dir, animFrame)
        } else if (tank.isPlayer) {
          // Lie-Back-Win-Mode: use player2 sprite for God AI tank.
          if (tank === world.player2) {
            artist.drawPlayer2Tank(tank.x, tank.y, tank.w, tank.dir, tank.level ?? 0, animFrame)
          } else {
            artist.drawPlayerTank(tank.x, tank.y, tank.w, tank.dir, tank.level ?? 0, animFrame)
          }
        } else {
          const isCommander = tank.aiState?.isCommander === true
          artist.drawEnemyTank(
            tank.x,
            tank.y,
            tank.w,
            tank.dir,
            tank.kind,
            animFrame,
            (tank.flashTimer ?? 0) > 0,
            tank.hp,
            Math.min(tank.hitCount ?? 0, 4),
            isCommander,
          )
        }

        if (tank.bonus) {
          const blink = Math.floor(frame / 10) % 2 === 0
          if (blink) {
            ctx.strokeStyle = '#ff4040'
            ctx.lineWidth = 1
            ctx.strokeRect(tank.x - 1, tank.y - 1, tank.w + 2, tank.h + 2)
          }
        }

        if (tank.shieldTimer && tank.shieldTimer > 0) {
          artist.drawShield(tank.x, tank.y, tank.w, frame)
        }

        // Allies get a distinct purple friendly aura (not the enemy rank
        // insignia / commander crown); everyone else draws the insignia LAST so
        // it sits above the HP level border, bonus frame, and shield.
        if (tank.allegiance === 'ally') {
          artist.drawAllyAura(tank.x, tank.y, tank.w, frame)
        } else {
          artist.drawInsignia(
            tank.x,
            tank.y,
            tank.w,
            tank.aiState?.level ?? 'none',
            tank.aiState?.isCommander === true,
          )
        }
      }
    }

    // ---- Bullets ----

    protected renderBullets(world: World): void {
      const artist = this.artist
      const bullets = world.bullets
      // P6: index loop instead of `for...of` — dense arrays optimize identically
      // in V8, but `for...of` may allocate an iterator object on holey arrays
      // (post-compaction). The cost is zero on dev hardware and a real win on
      // older JS engines.
      for (let i = 0; i < bullets.length; i++) {
        const bullet = bullets[i]
        if (!bullet.alive) continue
        artist.drawBullet(bullet.x, bullet.y, bullet.w, bullet.dir)
      }
    }

    // ---- Power-ups ----

    protected renderPowerUps(world: World): void {
      const frame = world.frame
      const artist = this.artist
      const pus = world.powerUps
      for (let i = 0; i < pus.length; i++) {
        const pu = pus[i]
        if (!pu.alive) continue
        artist.drawPowerUp(pu.x, pu.y, pu.w, pu.type, frame, pu.lifeTimer, POWERUP_TIMEOUT_MS)
      }
    }
  }
}
