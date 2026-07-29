import { runSimulation } from './simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'

// Quick eval on critical stages with 10 seeds
const SEEDS = Array.from({ length: 10 }, (_, i) => i + 1)
const MAX_TICKS = 18000
const CRITICAL_STAGES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 18, 25, 26, 28, 30, 32, 33, 34]

let totalWins = 0
let totalN = 0
for (const stageIdx of CRITICAL_STAGES) {
  let wins = 0
  let kills = 0
  let timeouts = 0
  let gameovers = 0
  for (const seed of SEEDS) {
    const r = runSimulation({
      seed,
      stage: STAGES[stageIdx],
      difficulty: 'classic',
      maxTicks: MAX_TICKS,
      sampleInterval: MAX_TICKS,
      godAIParams: DEFAULT_GOD_AI_PARAMS,
    })
    if (r.outcome === 'stage_clear') wins++
    kills += r.finalState.killCount
    if (r.outcome === 'max_ticks') timeouts++
    if (r.outcome === 'gameover') gameovers++
  }
  const n = SEEDS.length
  totalWins += wins
  totalN += n
  const pct = ((wins / n) * 100).toFixed(1)
  const avgK = (kills / n).toFixed(1)
  const flag = wins / n >= 0.8 ? '✅' : wins / n >= 0.5 ? '⚠️' : '❌'
  console.log(
    `${flag} S${stageIdx} (${STAGES[stageIdx].name}): ${pct}% kills=${avgK} to=${timeouts} go=${gameovers}`,
  )
}
console.log(`\nOverall: ${totalWins}/${totalN} = ${((totalWins / totalN) * 100).toFixed(1)}%`)
