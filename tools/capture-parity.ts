import { runSimulation } from './simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'

// Capture new parity baseline for 8 seeds on Stage 0 classic @36000 ticks
const SEEDS = [1, 2, 7, 42, 100, 999, 12345, 55555]
const results: string[] = []

for (const seed of SEEDS) {
  const r = runSimulation({
    seed,
    stage: STAGES[0],
    difficulty: 'classic',
    maxTicks: 36000,
    sampleInterval: 36000,
    godAIParams: DEFAULT_GOD_AI_PARAMS,
  })
  const line = `  ${seed}: { outcome: '${r.outcome}', ticks: ${r.ticks}, score: ${r.finalState.score}, lives: ${r.finalState.lives}, killCount: ${r.finalState.killCount}, baseAlive: ${r.finalState.baseAlive}, playerLevel: ${r.finalState.playerLevel} },`
  results.push(line)
  console.log(line)
}

// Also capture Stage 0 + Stage 1 regression gate numbers (30 seeds @18000)
for (const stageIdx of [0, 1]) {
  let wins = 0
  let baseAlive = 0
  let kills = 0
  for (let seed = 1; seed <= 30; seed++) {
    const r = runSimulation({
      seed,
      stage: STAGES[stageIdx],
      difficulty: 'classic',
      maxTicks: 18000,
      sampleInterval: 18000,
      godAIParams: DEFAULT_GOD_AI_PARAMS,
    })
    if (r.outcome === 'stage_clear') wins++
    if (r.finalState.baseAlive) baseAlive++
    kills += r.finalState.killCount
  }
  const n = 30
  console.log(
    `Stage ${stageIdx}: wins=${wins}/${n} (${((wins / n) * 100).toFixed(1)}%) baseAlive=${baseAlive}/${n} avgKills=${(kills / n).toFixed(1)}`,
  )
}
