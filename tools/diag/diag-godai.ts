#!/usr/bin/env bun
/**
 * diag-godai.ts — Diagnostic tool for God AI behavior.
 * Runs a simulation and reports branch counts, player trajectory, and failure analysis.
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { STAGES } from '../../src/config/stages'
import { CELL, BASE_POS } from '../../src/constants'

const seed = parseInt(process.argv[2] ?? '1', 10)
const difficulty = process.argv[3] ?? 'classic'

const world = new World()
world.rng.reseed(seed)
world.difficultyKey = difficulty
world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
const input = new GodAIInput(world)
const sim = new Simulation(world, input)
world.loadStageData(STAGES[0], 0)
input.reset()

let tick = 0
let firstKillTick: number | null = null
const playerPos: Array<{ tick: number; x: number; y: number; distBase: number }> = []

while (tick < 18000) {
  sim.tick()
  input.endFrame()
  tick++

  const events = world.consumeEvents()
  for (const e of events) {
    if (firstKillTick === null && e.type === 'tank_destroyed' && e.by === 'player') {
      firstKillTick = tick
    }
    if (e.type === 'base_destroyed') {
      const p = world.player
      const pcx = p ? p.x + p.w / 2 : 0
      const pcy = p ? p.y + p.h / 2 : 0
      const bcx = BASE_POS.col * CELL + CELL
      const bcy = BASE_POS.row * CELL + CELL
      console.log('=== BASE DESTROYED at tick', tick, '===')
      console.log('Player pos (cell):', Math.floor(pcx / CELL), Math.floor(pcy / CELL))
      console.log(
        'Player dist to base (cells):',
        Math.round((Math.abs(pcx - bcx) + Math.abs(pcy - bcy)) / CELL),
      )
      console.log('Kills:', world.killCount, 'Lives:', world.lives)
      console.log('First kill at tick:', firstKillTick)
      console.log('Branch counts:', JSON.stringify(input.branchCounts))
      const enemies = world.tanks.filter((t) => t.alive)
      console.log('Enemies alive at base death:', enemies.length)
      for (const t of enemies) {
        console.log('  Enemy:', t.kind, 'at cell', Math.floor(t.x / CELL), Math.floor(t.y / CELL))
      }
      console.log('Player trajectory:')
      for (const p of playerPos) {
        console.log('  tick', p.tick, ': cell', p.x, p.y, 'distBase', p.distBase)
      }
      process.exit(0)
    }
  }

  if (world.state === 'stageclear') {
    console.log('=== STAGE CLEAR at tick', tick, '===')
    console.log('Kills:', world.killCount, 'Lives:', world.lives)
    console.log('First kill at tick:', firstKillTick)
    console.log('Branch counts:', JSON.stringify(input.branchCounts))
    process.exit(0)
  }

  if (world.state === 'gameover') {
    console.log('=== GAME OVER at tick', tick, '===')
    console.log('Kills:', world.killCount, 'Lives:', world.lives)
    console.log('Branch counts:', JSON.stringify(input.branchCounts))
    process.exit(0)
  }

  if (tick % 500 === 0) {
    const p = world.player
    if (p) {
      const pcx = p.x + p.w / 2
      const pcy = p.y + p.h / 2
      const bcx = BASE_POS.col * CELL + CELL
      const bcy = BASE_POS.row * CELL + CELL
      playerPos.push({
        tick,
        x: Math.floor(pcx / CELL),
        y: Math.floor(pcy / CELL),
        distBase: Math.round((Math.abs(pcx - bcx) + Math.abs(pcy - bcy)) / CELL),
      })
    }
  }
}

console.log('=== TIMEOUT at tick', tick, '===')
console.log('Branch counts:', JSON.stringify(input.branchCounts))
console.log('Player positions over time:')
for (const p of playerPos) {
  console.log('  tick', p.tick, ': cell', p.x, p.y, 'distBase', p.distBase)
}
