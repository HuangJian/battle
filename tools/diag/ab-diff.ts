#!/usr/bin/env bun
/**
 * ab-diff.ts — cross-difficulty A/B flip scanner for the suicide strategy (§116).
 * flip-scan.ts hardcodes classic; this extends it to hard/chaos. Runs both
 * arms (A = baseline, B = baseline + suicideReturnMode=1) across stages/seeds
 * and reports FLIP-TO-WIN / FLIP-TO-LOSE / TIED per stage + a suite summary.
 *
 * Usage:
 *   bun tools/diag/ab-diff.ts --difficulty hard --stages all --seeds 1-60
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { RNG } from '../../src/utils/RNG'
import { START_LIVES } from '../../src/constants'
import { arg, parseSeeds, parseStages } from '../lib/cli'

const difficulty = arg('difficulty') ?? 'hard'
const seeds = parseSeeds(arg('seeds'), 60)
const stageIdxs = parseStages(arg('stages'))

function runOnce(stageIdx: number, seed: number, suicideOn: boolean): boolean {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const params = { ...DEFAULT_GOD_AI_PARAMS, suicideReturnMode: suicideOn ? 1 : 0 }
  const input = new GodAIInput(world, params, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], stageIdx)
  input.reset()
  let tick = 0
  while (tick < 18000) {
    sim.tick()
    input.endFrame()
    tick++
    if (world.state === 'stageclear') return true
    if (world.state === 'gameover') return false
  }
  return false
}

interface StageAgg {
  winA: number
  winB: number
  toWin: number[]
  toLose: number[]
  tied: number
}

const byStage = new Map<number, StageAgg>()
for (const si of stageIdxs) byStage.set(si, { winA: 0, winB: 0, toWin: [], toLose: [], tied: 0 })

let totalToWin = 0
let totalToLose = 0
for (const si of stageIdxs) {
  const agg = byStage.get(si)!
  for (const seed of seeds) {
    const a = runOnce(si, seed, false)
    const b = runOnce(si, seed, true)
    if (a) agg.winA++
    if (b) agg.winB++
    if (a === b) agg.tied++
    else if (b && !a) agg.toWin.push(seed)
    else agg.toLose.push(seed)
  }
  totalToWin += agg.toWin.length
  totalToLose += agg.toLose.length
  const name = STAGES[si].name
  console.log(
    `S${si + 1} ${name.padEnd(16)} A ${agg.winA}/${seeds.length} → B ${agg.winB}/${seeds.length}  ` +
      `win:${agg.toWin.length} lose:${agg.toLose.length} tied:${agg.tied}`,
  )
  if (agg.toWin.length > 0) console.log(`    FLIP-TO-WIN  seeds: ${agg.toWin.join(', ')}`)
  if (agg.toLose.length > 0) console.log(`    FLIP-TO-LOSE seeds: ${agg.toLose.join(', ')}`)
}
console.log(
  `\n[${difficulty}] SUITE: net ${totalToWin - totalToLose >= 0 ? '+' : ''}${totalToWin - totalToLose} flips ` +
    `(to-win ${totalToWin}, to-lose ${totalToLose})`,
)
