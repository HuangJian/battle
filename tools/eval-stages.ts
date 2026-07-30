import { runSimulation } from './simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { writeReplayFile } from './replay-writer'

// Evaluation: seeds 1-30 on classic stages 0-4 @18000 ticks
const SEEDS = Array.from({ length: 30 }, (_, i) => i + 1)
const STAGE_INDICES = [0, 1, 2, 3, 4]
const MAX_TICKS = 18000

const replayFailures = process.argv.includes('--replay-failures')
const replayDir = 'replays'

let totalWins = 0
let totalN = 0
for (const stageIdx of STAGE_INDICES) {
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
      record: replayFailures,
    })
    // Write replay file for failures if requested
    if (replayFailures && r.replay && r.outcome !== 'stage_clear') {
      await writeReplayFile({
        result: r,
        dir: replayDir,
        stageIndex: stageIdx,
        stageName: STAGES[stageIdx].name,
      })
    }
    if (r.outcome === 'stage_clear') wins++
    if (r.finalState.baseAlive) baseAlive++
    kills += r.finalState.killCount
    if (r.outcome === 'max_ticks') {
      timeouts++
      failures.push(`s${seed}:timeout k${r.finalState.killCount}`)
    }
    if (r.outcome === 'gameover') {
      gameovers++
      failures.push(
        `s${seed}:gameover k${r.finalState.killCount} dist${r.failure?.playerDistToBase ?? '?'}`,
      )
    }
  }
  const n = SEEDS.length
  totalWins += wins
  totalN += n
  console.log(
    `Stage ${stageIdx} (${STAGES[stageIdx].name}): win=${wins}/${n} (${((wins / n) * 100).toFixed(1)}%) ` +
      `base=${baseAlive}/${n} kills=${(kills / n).toFixed(1)} timeout=${timeouts} gameover=${gameovers}`,
  )
  if (failures.length > 0) {
    console.log(`  failures: ${failures.join(', ')}`)
  }
}
console.log(`\nOverall: ${totalWins}/${totalN} = ${((totalWins / totalN) * 100).toFixed(1)}%`)
