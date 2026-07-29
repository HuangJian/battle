import { runSimulation } from './simulation-runner'
import { STAGES } from '../src/config/stages'

const SEEDS = [1, 2, 7, 42, 100, 999, 12345, 55555]

console.log('const BASELINE: Record<number, Expected> = {')
for (const seed of SEEDS) {
  const r = runSimulation({
    seed,
    stage: STAGES[0],
    difficulty: 'classic',
    maxTicks: 36000,
    sampleInterval: 36000,
  })
  const outcome =
    r.outcome === 'stage_clear'
      ? 'stage_clear'
      : r.outcome === 'gameover'
        ? 'gameover'
        : 'max_ticks'
  console.log(`  ${seed}: {
    outcome: '${outcome}',
    ticks: ${r.ticks},
    score: ${r.finalState.score},
    lives: ${r.finalState.lives},
    killCount: ${r.finalState.killCount},
    baseAlive: ${r.finalState.baseAlive},
    playerLevel: ${r.finalState.playerLevel},
  },`)
}
console.log('}')
