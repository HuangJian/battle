#!/usr/bin/env bun
/**
 * diag-suicide-events.ts — log every §116 suicide commit with surrounding
 * context, to diagnose whether trades are wasteful (per-seed tick-diff).
 * Usage: bun tools/diag/diag-suicide-events.ts <difficulty> <stageIdx> <seed> <on|off>
 */
import { World } from '../../../src/game/World'
import { Simulation } from '../../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../../src/config/rules'
import { STAGES } from '../../../src/config/stages'
import { RNG } from '../../../src/utils/RNG'
import { START_LIVES, BASE_POS, CELL } from '../../../src/constants'

const difficulty = process.argv[2] ?? 'hard'
// CLI stage is 1-based (1..35); internal index is 0-based.
const stageIdx = parseInt(process.argv[3] ?? '24', 10) - 1
const seed = parseInt(process.argv[4] ?? '14', 10)
const on = (process.argv[5] ?? 'on') === 'on'

const world = new World()
world.rng.reseed(seed)
world.difficultyKey = difficulty
world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
world.rules = RULES[difficulty] ?? DEFAULT_RULES
world.playerLevel = world.difficulty?.playerStartLevel ?? 0
world.lives = world.difficulty?.startLives ?? START_LIVES
const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
const params = { ...DEFAULT_GOD_AI_PARAMS, suicideReturnMode: on ? 1 : 0 }
const input = new GodAIInput(world, params, godRng)
const sim = new Simulation(world, input)
world.loadStageData(STAGES[stageIdx], stageIdx)
input.reset()

let tick = 0
let lastSuicideTick = -1
const bcx = BASE_POS.col * CELL + CELL
const bcy = BASE_POS.row * CELL + CELL
let suicides = 0
let eatenLives = 0
let lastLives = world.lives

while (tick < 18000) {
  sim.tick()
  // Detect a suicide born from a NEW commit (standing start), not the
  // mid-suicide continuation ticks, by tracking when _suicideStanding flips on.
  if (on && input._suicideStanding && lastSuicideTick < 0) {
    lastSuicideTick = tick
    const p = world.player
    const px = p ? Math.round(p.x / CELL) : -1
    const py = p ? Math.round(p.y / CELL) : -1
    const distBase = p
      ? Math.round((Math.abs(p.x + p.w / 2 - bcx) + Math.abs(p.y + p.h / 2 - bcy)) / CELL)
      : -1
    suicides++
    console.log(
      `[suicide #${suicides}] tick=${tick} lives=${world.lives} player=(${px},${py}) distBase=${distBase} ` +
        `baseAlive=${!world.tileMap.isBaseDestroyed()} spawn=(${world.playerSpawnPoint.col},${world.playerSpawnPoint.row})`,
    )
  }
  if (on && !input._suicideStanding) lastSuicideTick = -1
  // Track eaten lives (life lost that can't be attributed to an enemy kill).
  if (world.lives < lastLives) {
    eatenLives += lastLives - world.lives
  }
  lastLives = world.lives
  input.endFrame()
  tick++
  if (world.state === 'stageclear' || world.state === 'gameover') break
}

const p = world.player
const px = p ? Math.round(p.x / CELL) : -1
const py = p ? Math.round(p.y / CELL) : -1
console.log(
  `S${stageIdx + 1} ${STAGES[stageIdx].name} seed ${seed} (${difficulty}, suicide=${on ? 'ON' : 'OFF'}) ` +
    `outcome=${world.state} T=${tick} kills=${world.killCount} lives=${world.lives} player=(${px},${py}) ` +
    `baseAlive=${!world.tileMap.isBaseDestroyed()} suicides=${suicides} branchSus=${input.branchCounts.suicideReturn}`,
)
