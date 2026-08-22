#!/usr/bin/env bun
/**
 * diag-ice-deaths.ts — Was the player on ice when it died? (S19 family)
 *
 * Runs a stage/seed and, on every player death, records whether the player
 * was standing on ice and whether it had residual glide velocity.
 *
 * Usage: bun tools/diag/diag-ice-deaths.ts <stage 1-35> <seedList> [--params <file>]
 */
import { World } from '../../../src/game/World'
import { Simulation } from '../../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, GodAIParams } from '../../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../../src/config/rules'
import { STAGES } from '../../../src/config/stages'
import { readFileSync } from 'fs'

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

// CLI stage is 1-based (1..35); internal index is 0-based.
const stageIdx = parseInt(process.argv[2] ?? '19', 10) - 1
const seeds = (process.argv[3] ?? '1,4,5,7,9').split(',').map((s) => parseInt(s, 10))
const paramsFile = arg('params', '')
let params: GodAIParams = DEFAULT_GOD_AI_PARAMS
if (paramsFile) {
  const raw = JSON.parse(readFileSync(paramsFile, 'utf8'))
  params = { ...DEFAULT_GOD_AI_PARAMS, ...(raw.bestParams ?? raw) }
}

let totalDeaths = 0
let deathsOnIce = 0
let deathsWithGlide = 0

for (const seed of seeds) {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  const input = new GodAIInput(world, params)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], 0)
  input.reset()

  let tick = 0
  while (tick < 18000 && world.state === 'playing') {
    // Sample BEFORE the tick that kills: track ice state continuously.
    const p = world.player
    const wasOnIce = p && p.alive ? world.isTankOnIce(p) : false

    sim.tick()
    input.endFrame()
    tick++

    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed' && e.tank.isPlayer) {
        totalDeaths++
        if (wasOnIce) deathsOnIce++
        // Residual glide: destroyed tank still carries velocity from ice.
        const glide = Math.abs(e.tank.vx) + Math.abs(e.tank.vy) > 0.1
        if (wasOnIce && glide) deathsWithGlide++
        console.log(
          `S${stageIdx + 1} seed ${seed} death at t${tick}: onIce=${wasOnIce} glide=${glide} cell=(${Math.floor((e.tank.x + e.tank.w / 2) / 16)},${Math.floor((e.tank.y + e.tank.h / 2) / 16)}) lives=${world.lives}`,
        )
      }
    }
  }
  console.log(
    `S${stageIdx + 1} seed ${seed}: end=${world.state} kills=${world.killCount} t=${tick}`,
  )
}

console.log(
  `\nTotal deaths=${totalDeaths} onIce=${deathsOnIce} (${((deathsOnIce / Math.max(1, totalDeaths)) * 100).toFixed(0)}%) withGlide=${deathsWithGlide}`,
)
