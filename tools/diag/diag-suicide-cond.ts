#!/usr/bin/env bun
/**
 * diag-suicide-cond.ts — per-condition bottleneck analyzer for §116.
 * At every tick, evaluates each of the 5 preconditions independently on the
 * REAL world state and counts how often each is satisfied, to find which
 * condition is the binding constraint preventing the candidate from firing.
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { RNG } from '../../src/utils/RNG'
import { START_LIVES } from '../../src/constants'
import {
  controlledLives,
  bulletWouldKillPlayer,
  findSuicideTargetImpl,
} from '../../src/ai/god/SuicideReturn'
import { enemyCanShootBase } from '../../src/ai/god/SmartThreatModel'

const difficulty = process.argv[2] ?? 'classic'
// CLI stage is 1-based (1..35); internal index is 0-based.
const stageIdx = parseInt(process.argv[3] ?? '33', 10) - 1
const seed = parseInt(process.argv[4] ?? '1', 10)

const world = new World()
world.rng.reseed(seed)
world.difficultyKey = difficulty
world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
world.rules = RULES[difficulty] ?? DEFAULT_RULES
world.playerLevel = world.difficulty?.playerStartLevel ?? 0
world.lives = world.difficulty?.startLives ?? START_LIVES
const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
const params = { ...DEFAULT_GOD_AI_PARAMS, suicideReturnMode: 1 }
const input = new GodAIInput(world, params, godRng)
const sim = new Simulation(world, input)
world.loadStageData(STAGES[stageIdx], stageIdx)
input.reset()

const c = {
  ticks: 0,
  playerAlive: 0,
  livesOK: 0,
  shieldedOK: 0,
  threatExists: 0,
  bulletLethal: 0,
  bulletIn1s: 0,
  threatPointEnemy: 0,
  spawnDistanceOK: 0,
  hasTarget: 0,
}

let tick = 0
while (tick < 18000) {
  sim.tick()
  input.endFrame()
  tick++

  const p = world.player
  if (p && p.alive && p.spawnTimer <= 0) {
    c.playerAlive++
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2

    if (controlledLives(input) >= input.params.suicideReturnMinLives) c.livesOK++
    if ((p.shieldTimer ?? 0) <= 0) c.shieldedOK++

    const bullets = world.bullets
    let threat: any = null
    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (!b.alive || b.isPlayer) continue
      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      const vertical = b.dir === 'up' || b.dir === 'down'
      const aligned = vertical ? Math.abs(bcx - pcx) < 32 : Math.abs(bcy - pcy) < 32
      if (!aligned) continue
      const approaching =
        (b.dir === 'down' && bcy < pcy) ||
        (b.dir === 'up' && bcy > pcy) ||
        (b.dir === 'right' && bcx < pcx) ||
        (b.dir === 'left' && bcx > pcx)
      if (!approaching) continue
      threat = b
      break
    }
    if (threat) {
      c.threatExists++
      if (bulletWouldKillPlayer(p, threat)) c.bulletLethal++
      const bcx2 = threat.x + threat.w / 2
      const bcy2 = threat.y + threat.h / 2
      const vertical = threat.dir === 'up' || threat.dir === 'down'
      const bdist = vertical ? Math.abs(bcy2 - pcy) : Math.abs(bcx2 - pcx)
      const bt = threat.speed > 0 ? bdist / threat.speed : Infinity
      if (bt <= input.params.suicideReturnBulletTimeTicks) c.bulletIn1s++
    }
    // Count threat-point enemies (condition 1) and spawn-distance satisfaction.
    const tanks = world.tanks
    for (let ti = 0; ti < tanks.length; ti++) {
      const e = tanks[ti]
      if (!e.alive || e.spawnTimer > 0) continue
      if (enemyCanShootBase(input, e)) {
        c.threatPointEnemy++
        const ec = input.tankCell(e)
        const sd =
          Math.abs(world.playerSpawnPoint.col - ec.col) +
          Math.abs(world.playerSpawnPoint.row - ec.row)
        if (sd <= input.params.suicideReturnSpawnDistCells) c.spawnDistanceOK++
      }
    }
    if (findSuicideTargetImpl(input, pcx, pcy)) c.hasTarget++
  }

  if (world.state === 'stageclear' || world.state === 'gameover') break
}

console.log(`Stage S${stageIdx + 1} ${STAGES[stageIdx].name} seed ${seed} (${difficulty})`)
console.log(`total ticks ${tick}, playerAlive ${c.playerAlive}`)
console.log(`  livesOK         ${c.livesOK}`)
console.log(`  shieldedOK      ${c.shieldedOK}`)
console.log(
  `  threatExists    ${c.threatExists}  bulletLethal ${c.bulletLethal}  bulletIn1s ${c.bulletIn1s}`,
)
console.log(
  `  threatPointEnemy ${c.threatPointEnemy}  spawnDistanceOK ${c.spawnDistanceOK}  hasTarget ${c.hasTarget}`,
)
console.log(`spawn = (${world.playerSpawnPoint.col}, ${world.playerSpawnPoint.row})`)
console.log(`outcome: ${world.state}`)
