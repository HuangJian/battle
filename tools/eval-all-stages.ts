import { runSimulation } from './simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'

// Full 35-stage classic baseline scan
const SEEDS = Array.from({ length: 20 }, (_, i) => i + 1)
const MAX_TICKS = 18000

let totalWins = 0
let totalN = 0
const stageResults: Array<{
  idx: number
  name: string
  winRate: number
  avgKills: number
  gameovers: number
}> = []

for (let stageIdx = 0; stageIdx < STAGES.length; stageIdx++) {
  let wins = 0
  let baseAlive = 0
  let kills = 0
  let timeouts = 0
  let gameovers = 0
  const failures: string[] = []
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
    if (r.finalState.baseAlive) baseAlive++
    kills += r.finalState.killCount
    if (r.outcome === 'max_ticks') {
      timeouts++
      if (failures.length < 10) failures.push(`s${seed}:timeout k${r.finalState.killCount}`)
    }
    if (r.outcome === 'gameover') {
      gameovers++
      if (failures.length < 10) failures.push(`s${seed}:gameover k${r.finalState.killCount}`)
    }
  }
  const n = SEEDS.length
  totalWins += wins
  totalN += n
  const winRate = (wins / n) * 100
  const avgKills = kills / n
  stageResults.push({ idx: stageIdx, name: STAGES[stageIdx].name, winRate, avgKills, gameovers })
  console.log(
    `Stage ${stageIdx} (${STAGES[stageIdx].name}): win=${wins}/${n} (${winRate.toFixed(1)}%) ` +
      `base=${baseAlive}/${n} kills=${avgKills.toFixed(1)} timeout=${timeouts} gameover=${gameovers}`,
  )
  if (failures.length > 0) {
    console.log(`  failures: ${failures.join(', ')}`)
  }
}

console.log(`\nOverall: ${totalWins}/${totalN} = ${((totalWins / totalN) * 100).toFixed(1)}%`)
console.log(`\nSub-80 stages:`)
for (const s of stageResults) {
  if (s.winRate < 80) {
    console.log(
      `  S${s.idx} (${s.name}): ${s.winRate.toFixed(1)}% kills=${s.avgKills.toFixed(1)} GO=${s.gameovers}`,
    )
  }
}
