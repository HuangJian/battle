#!/usr/bin/env bun
/**
 * diag-suicide.ts — diagnostic for the suicide quick-return (§116).
 * Runs full stage simulations across seeds and reports how many times the
 * SUICIDE_RETURN candidate commits (branchCounts.suicideReturn), per stage.
 * Helps answer "does the strategy fire at all" before A/B interpretation.
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { RNG } from '../../src/utils/RNG'
import { START_LIVES } from '../../src/constants'

const difficulty = process.argv[2] ?? 'classic'
const seedCount = parseInt(process.argv[3] ?? '60', 10)
const stageSpec = process.argv[4] ?? 'all'

const stageIdxs = stageSpec === 'all' ? STAGES.map((_, i) => i) : stageSpec.split(',').map(Number)

let totalFire = 0
let totalFireRuns = 0
let totalRuns = 0
let totalWins = 0

for (const si of stageIdxs) {
  let fires = 0
  let wins = 0
  for (let seed = 1; seed <= seedCount; seed++) {
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
    world.loadStageData(STAGES[si], si)
    input.reset()
    let tick = 0
    while (tick < 18000) {
      sim.tick()
      input.endFrame()
      tick++
      if (world.state === 'stageclear') {
        wins++
        break
      }
      if (world.state === 'gameover') break
    }
    fires += input.branchCounts.suicideReturn
    totalRuns++
    if (input.branchCounts.suicideReturn > 0) totalFireRuns++
  }
  totalFire += fires
  totalWins += wins
  const name = STAGES[si].name
  console.log(
    `S${si} ${name.padEnd(16)} win ${wins}/${seedCount}  suicide-fires ${fires} (${fires > 0 ? (fires / seedCount).toFixed(2) : 0}/run)`,
  )
}

console.log(
  `\nTOTAL: win ${totalWins}/${totalRuns}  suicide fires ${totalFire} across ${totalFireRuns} of ${totalRuns} runs (${((totalFireRuns / totalRuns) * 100).toFixed(1)}% of runs)`,
)
