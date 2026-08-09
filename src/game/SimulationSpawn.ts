import { MAX_ENEMIES_ALIVE, COOP_MAX_ENEMIES_ALIVE, TANK } from '../constants'
import { applyEliteModifier, resolveProfile, profileToStats } from '../config/combat'
import { rollTier, COMMANDER_ALIVE_CAP } from '../ai/config'
import type { IntelligenceLevel } from '../types'
import { aabb } from '../utils/helpers'
import type { SimulationConstructor, SimulationCore } from './SimulationCore'

// Spawn points: the defaults are derived from ENEMY_SPAWNS in constants.ts and
// cached on `world.enemySpawnPoints` at loadStageData. The third authentic
// point is col 6 (x = 96), NOT the old hardcoded col 24 (x = 384) which jammed
// a tank against the right wall (FIELD = 416, tank = 32 ⇒ a tank at x = 384
// occupies x = 384..416 and can only move down/left; two such tanks meeting at
// the edge deadlock with zero free directions). See fix in updateSpawning().
// NOTE: per-stage overrides now live on `world.enemySpawnPoints`
// (plan/God-AI-Curriculum §3.5 影响 1). Simulation reads from the World, not
// from any module-level constant.

/**
 * SimulationSpawnMixin — the enemy Spawn System (spawn timers + queue-driven
 * spawning with tier rolls, commander caps and elite boosts).
 *
 * Composes onto {@link SimulationCore}. See `Simulation.ts` for the final
 * mixin order. Cross-mixin calls (player updates, bullets, conditions) resolve
 * to the stubs declared on `SimulationCore`.
 */
export function SimulationSpawnMixin<TBase extends SimulationConstructor<SimulationCore>>(
  Base: TBase,
) {
  return class SimulationSpawn extends Base {
    protected updateSpawnTimers(): void {
      const w = this.world
      // Indexed loop — `for...of` allocates an iterator object per call (AGENTS
      // §14.1). This runs every tick over allTanks (which the getter rebuilds
      // into a reused buffer).
      const tanks = w.allTanks
      for (let i = 0; i < tanks.length; i++) {
        const tank = tanks[i]
        if (tank.spawnTimer > 0) {
          tank.spawnTimer -= 1000 / 60
          if (tank.spawnTimer < 0) tank.spawnTimer = 0
        }
        if (tank.shieldTimer && tank.shieldTimer > 0) {
          tank.shieldTimer -= 1000 / 60
          if (tank.shieldTimer < 0) tank.shieldTimer = 0
        }
        if (tank.boatTimer && tank.boatTimer > 0) {
          tank.boatTimer -= 1000 / 60
          if (tank.boatTimer < 0) tank.boatTimer = 0
        }
        if (tank.flashTimer !== undefined && tank.flashTimer > 0) {
          tank.flashTimer -= 1000 / 60
        }
      }
    }

    protected updateSpawning(): void {
      const w = this.world
      if (w.spawnQueue.length === 0) return
      // Co-op (躺赢 / 督战x2) raises the concurrent-enemy cap so the field keeps
      // a minimum of COOP_MAX_ENEMIES_ALIVE regular enemies alive (tasks.chat.md
      // §27: "敌人最低同时在场数设为 5，除非敌人已全部出场"). The spawner refills
      // to this cap every interval, so a higher cap == a floor. Single-player
      // keeps the tighter MAX_ENEMIES_ALIVE. Balance (isExtra) enemies from
      // 天降神兵 are excluded from enemyCount, so they stack on top of this floor.
      const maxAlive = w.coop || w.spectateDual ? COOP_MAX_ENEMIES_ALIVE : MAX_ENEMIES_ALIVE
      if (w.enemyCount >= maxAlive) return
      if (w.spawnTimer > 0) return

      const entry = w.spawnQueue[0]
      const spawnPoints = w.enemySpawnPoints
      const n = spawnPoints.length

      // Try every spawn point in rotation and use the first one that is clear of
      // tanks. Previously the code retried only the *current* point (decrementing
      // the index on failure), so a single occupied/stuck point would stall ALL
      // enemy spawns forever. Now an occupied point is simply skipped and the next
      // one is tried; if none are clear we just retry next frame and rotate the
      // start index so we don't keep re-checking the same blocked point first.
      for (let i = 0; i < n; i++) {
        const idx = (w.spawnPointIndex + i) % n
        const pt = spawnPoints[idx]

        // Skip spawn points overlapping blocking terrain (brick/steel/water/base).
        // Several authentic stages place terrain on top of a spawn cell — e.g.
        // col 6 is steel on stage 2, brick on stages 9/19/21, water on stages
        // 20/26/31, steel on stage 25. Without this check the enemy was created
        // *inside* that terrain and then jammed: every candidate move overlapped
        // the very cell it stood on, so rectHitsTerrain() rejected all four
        // directions and the tank sat at the spawn point forever. Treat a
        // terrain-blocked point exactly like an occupied one — skip it and fall
        // through to the next clear point (col 0 / col 12 are always clear on
        // every stage, so a spawn always succeeds).
        if (w.rectHitsTerrain(pt.x, pt.y, TANK, TANK)) continue

        // Check if spawn area is clear of other tanks (inline rect — no per-retry allocation)
        let canSpawn = true
        const checkTanks = w.allTanks
        for (let ci = 0; ci < checkTanks.length; ci++) {
          const tank = checkTanks[ci]
          if (aabb(pt.x, pt.y, TANK, TANK, tank.x, tank.y, tank.w, tank.h)) {
            canSpawn = false
            break
          }
        }
        if (!canSpawn) continue

        // Create the enemy tank (base profile/stats; tier & boost applied after).
        const tank = w.createTank(entry.kind, pt.x, pt.y, 'down')
        tank.bonus = entry.bonus

        // ---- Spawn-time tier roll (plan §5) ----
        // Decide the FINAL tier BEFORE finalizing stats so a cap downgrade can
        // veto the +15% boost cleanly (§5.3 [D10-fix]).
        const remainingSpawns = w.enemiesTotal - w.enemiesSpawned
        let tier: IntelligenceLevel
        if (w.commanderQuotaRemaining > 0 && remainingSpawns <= w.commanderQuotaRemaining) {
          // Floor guarantee: force a Commander attempt so the difficulty's
          // minimum commander count is always satisfiable (§5.1 [D9-fix]).
          // Forced rolls consume NO RNG draw (tier-roll gate spirit).
          tier = 'commander'
          w.commanderQuotaRemaining -= 1
        } else {
          tier = rollTier(w.difficultyKey, w.rng)
          // Count a natural commander roll against the floor only while it is
          // still outstanding. The floor is a MINIMUM guarantee, so the counter
          // clamps at 0 (never negative) once satisfied — extra natural
          // commander spawns beyond the floor are just bonus, not debt.
          if (tier === 'commander' && w.commanderQuotaRemaining > 0) w.commanderQuotaRemaining -= 1
        }

        let isCommander = false
        let finalLevel = tier
        if (tier === 'commander') {
          // Cap: at most COMMANDER_ALIVE_CAP commander-tier tanks alive on
          // screen (active + inactive both count, §5.1). A roll against a
          // full cap downgrades to ACTUAL Veteran — no boost, no crown.
          let aliveCmd = 0
          const cmdTanks = w.tanks
          for (let ci = 0; ci < cmdTanks.length; ci++) {
            const t = cmdTanks[ci]
            if (t.alive && t.aiState?.level === 'commander') aliveCmd++
          }
          if (aliveCmd >= COMMANDER_ALIVE_CAP) {
            finalLevel = 'veteran'
          } else {
            isCommander = true
          }
        }

        // Apply the +15% combat boost to EVERY commander-tier spawn (incl.
        // inactive ones), per §5.3 [D10]. A cap-downgraded Veteran gets
        // nothing — decide tier first, then boost conditionally.
        if (isCommander) {
          const eliteProfile = applyEliteModifier(
            tank.profile ?? resolveProfile(tank.kind, 0),
            tank.kind,
          )
          tank.profile = eliteProfile
          const eliteStats = profileToStats(eliteProfile, tank.kind, tank.level ?? 0, w.rules)
          tank.speed = eliteStats.speed
          tank.bulletSpeed = eliteStats.bulletSpeed
          tank.bulletPower = eliteStats.bulletPower
          tank.damage = eliteStats.damage
          tank.fireCooldown = eliteStats.fireCooldown
          tank.nextFireInterval = eliteStats.fireCooldown
          tank.maxHp = eliteStats.maxHp
          tank.hp = eliteStats.maxHp
        }

        // Stamp the rolled tier onto the brain (createTank used a placeholder).
        // commanderTimer stays at its createTank default; Simulation sets the
        // 1s office delay when this tank BECOMES the active commander.
        if (tank.aiState) {
          tank.aiState.level = finalLevel
          tank.aiState.isCommander = isCommander
        }

        w.tanks.push(tank)
        w.spawnQueue.shift()
        w.enemiesSpawned++
        w.spawnTimer = w.rules.spawnIntervalMs // classic 1.8s, others 1.5s
        w.spawnPointIndex = (idx + 1) % n
        return
      }

      // All points blocked this frame — advance the start index and retry next frame.
      w.spawnPointIndex = (w.spawnPointIndex + 1) % n
    }
  }
}
