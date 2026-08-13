#!/usr/bin/env bun
/**
 * idle-trace.ts — Detailed per-tick trace for a specific stage@seed idle alert.
 *
 * Uses the same "within 1 cell" displacement logic as idle-analysis.ts:
 * an idle period continues as long as the player's displacement from the
 * idle-start position does not exceed 1 cell (16px) in either axis.
 *
 * Usage:
 *   bun tools/diag/idle-trace.ts --stage 1 --seed 1 --difficulty hard \
 *       --from 5880 --to 6120
 *   bun tools/diag/idle-trace.ts --stage 1 --seed 1 --summary --from 5880 --to 6120
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { CELL, BASE_POS, START_LIVES } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'

const IDLE_THRESHOLD_TICKS = 600 // 10s at 60fps
const CELL_THRESHOLD = CELL // 16px — 1 cell displacement threshold

function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : def
}

const stageIdx = Number(arg('stage', '1')) - 1
const seed = Number(arg('seed', '1'))
const difficulty = arg('difficulty', 'hard')!
const fromTick = Number(arg('from', '0'))
const toTick = Number(arg('to', '999999'))
const summary = process.argv.includes('--summary')

const world = new World()
world.rng.reseed(seed)
world.difficultyKey = difficulty
world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
world.rules = RULES[difficulty] ?? DEFAULT_RULES
world.playerLevel = world.difficulty?.playerStartLevel ?? 0
world.lives = world.difficulty?.startLives ?? START_LIVES
// 督战 (supervise) mode: God AI drives player1, no human input.
world.spectate = true

const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
const input = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, godRng)
const sim = new Simulation(world, input)

world.loadStageData(STAGES[stageIdx], stageIdx)
input.reset()

const bcx = BASE_POS.col * CELL + CELL
const bcy = BASE_POS.row * CELL + CELL

let tick = 0
let outcome = 'max_ticks'

// Idle tracking: displacement-from-start logic (matches idle-analysis.ts)
let idleStart = -1
let idleStartX = 0
let idleStartY = 0
let idleFireTicks = 0
let idleKillsAtStart = 0
let idleRevisionAtStart = 0

const branchCounts = new Map<string, number>()
let totalFireTicks = 0
let totalMoveNullTicks = 0
let totalTraceTicks = 0
let alertCount = 0

while (tick < 36000) {
  sim.tick()

  const p = world.player
  const playerAlive = !!p && p.alive && p.spawnTimer <= 0
  const px = p ? Math.round(p.x + p.w / 2) : -1
  const py = p ? Math.round(p.y + p.h / 2) : -1
  const branch = input._lastBranch
  const moveDir = input._moveDir
  const fire = input._fire
  const aggSuppress = input._aggCampSuppress
  const aggCampTicks = input._aggCampTicks
  const aggNavStuckTicks = input._aggNavStuckTicks
  const aggNavSuppress = input._aggNavSuppress
  const navStuckTicks = input._navStuckTicks
  const navStuckSuppress = input._navStuckSuppress
  const isAggressive = input.aggressive
  const pathLen = input.path.length
  const replanTimer = input.replanTimer
  const freezeTimer = world.freezeTimer
  const puStr = world.powerUps
    .filter((pu) => pu.alive)
    .map(
      (pu) => `(${Math.floor((pu.x + pu.w / 2) / CELL)},${Math.floor((pu.y + pu.h / 2) / CELL)})`,
    )
    .join(',')

  // ---- Idle detection (displacement-from-start, matches idle-analysis.ts) ----
  if (playerAlive) {
    if (idleStart < 0) {
      idleStart = tick
      idleStartX = px
      idleStartY = py
      idleFireTicks = fire ? 1 : 0
      idleKillsAtStart = world.killCount
      idleRevisionAtStart = world.tileMap.revision
    } else if (
      Math.abs(px - idleStartX) <= CELL_THRESHOLD &&
      Math.abs(py - idleStartY) <= CELL_THRESHOLD
    ) {
      // Still within 1 cell — extend idle
      if (fire) idleFireTicks++
    } else {
      // Moved more than 1 cell — check if idle period was an alert
      const duration = tick - idleStart
      if (duration >= IDLE_THRESHOLD_TICKS) {
        const killsDuringIdle = world.killCount - idleKillsAtStart
        const terrainChanged = world.tileMap.revision !== idleRevisionAtStart
        const combatExempt = idleFireTicks > 0 && (killsDuringIdle > 0 || terrainChanged)
        if (!combatExempt) {
          alertCount++
          const dur = ((duration * (1000 / 60)) / 1000).toFixed(1)
          console.log(
            `\n  ⚠ IDLE ALERT #${alertCount} tick ${idleStart}-${tick - 1} (${dur}s) ` +
              `pos=(${Math.floor(idleStartX / CELL)},${Math.floor(idleStartY / CELL)}) ` +
              `fire=${idleFireTicks} kills=${killsDuringIdle} terrain=${terrainChanged}`,
          )
        }
      }
      // Start new idle period
      idleStart = tick
      idleStartX = px
      idleStartY = py
      idleFireTicks = fire ? 1 : 0
      idleKillsAtStart = world.killCount
      idleRevisionAtStart = world.tileMap.revision
    }
  } else {
    idleStart = -1
  }

  if (tick >= fromTick && tick <= toTick && playerAlive) {
    const col = Math.floor(px / CELL)
    const row = Math.floor(py / CELL)

    const terrainUp = row > 0 ? world.tileMap.get(col, row - 1) : 'edge'
    const terrainDown = row < 25 ? world.tileMap.get(col, row + 1) : 'edge'
    const terrainLeft = col > 0 ? world.tileMap.get(col - 1, row) : 'edge'
    const terrainRight = col < 25 ? world.tileMap.get(col + 1, row) : 'edge'

    let nearestEnemyDist = 999
    let enemyCount = 0
    for (const t of world.tanks) {
      if (t.isPlayer || !t.alive || t.spawnTimer > 0) continue
      enemyCount++
      const d = Math.abs(t.x - p!.x) + Math.abs(t.y - p!.y)
      if (d < nearestEnemyDist) nearestEnemyDist = Math.round(d / CELL)
    }

    const distToBase = Math.round((Math.abs(px - bcx) + Math.abs(py - bcy)) / CELL)
    const idleDuration = idleStart >= 0 ? tick - idleStart : 0
    const dx = idleStart >= 0 ? px - idleStartX : 0
    const dy = idleStart >= 0 ? py - idleStartY : 0
    const displacement = Math.max(Math.abs(dx), Math.abs(dy))

    if (summary) {
      branchCounts.set(branch, (branchCounts.get(branch) ?? 0) + 1)
      if (fire) totalFireTicks++
      if (moveDir === null) totalMoveNullTicks++
      totalTraceTicks++

      if (tick === fromTick || (tick > fromTick && (tick - fromTick) % 60 === 0)) {
        console.log(
          `  tick ${tick} pos=(${col},${row}) dir=${p!.dir} branch=${branch} ` +
            `move=${moveDir ?? 'null'} fire=${fire} enemies=${enemyCount} ` +
            `nearest=${nearestEnemyDist} base=${distToBase} ` +
            `idle=${idleDuration}t disp=${displacement}px ` +
            `aggSup=${aggSuppress} aggCamp=${aggCampTicks} ` +
            `aggNavStuck=${aggNavStuckTicks} aggNavSup=${aggNavSuppress} ` +
            `navStuck=${navStuckTicks} navSup=${navStuckSuppress} ` +
            `pathLen=${pathLen} replan=${replanTimer} ` +
            `freeze=${Math.round(freezeTimer)} pu=[${puStr}]`,
        )
      }
    } else {
      const terrainStr = `U=${terrainUp} D=${terrainDown} L=${terrainLeft} R=${terrainRight}`
      const idleMark = idleDuration >= IDLE_THRESHOLD_TICKS ? ' ⚠IDLE' : ''
      console.log(
        `t${tick} (${col},${row}) dir=${p!.dir} moving=${p!.moving} ` +
          `br=${branch} mv=${moveDir ?? 'null'} fire=${fire} ` +
          `enemies=${enemyCount} near=${nearestEnemyDist} base=${distToBase} ` +
          `idle=${idleDuration}t disp=${displacement}px${idleMark} ${terrainStr} ` +
          `agg=${isAggressive} aggSup=${aggSuppress} aggCamp=${aggCampTicks} ` +
          `aggNavStuck=${aggNavStuckTicks} aggNavSup=${aggNavSuppress} ` +
          `navStuck=${navStuckTicks} navSup=${navStuckSuppress} ` +
          `pathLen=${pathLen} replan=${replanTimer} ` +
          `freeze=${Math.round(freezeTimer)} pu=[${puStr}]`,
      )
    }
  }

  input.endFrame()
  tick++

  if (world.state === 'stageclear' || world.state === 'victory') {
    outcome = 'stage_clear'
    break
  }
  if (world.state === 'gameover') {
    outcome = 'gameover'
    break
  }
}

// Flush final idle period
if (idleStart >= 0) {
  const duration = tick - idleStart
  if (duration >= IDLE_THRESHOLD_TICKS) {
    const killsDuringIdle = world.killCount - idleKillsAtStart
    const terrainChanged = world.tileMap.revision !== idleRevisionAtStart
    const combatExempt = idleFireTicks > 0 && (killsDuringIdle > 0 || terrainChanged)
    if (!combatExempt) {
      alertCount++
      const dur = ((duration * (1000 / 60)) / 1000).toFixed(1)
      console.log(
        `\n  ⚠ IDLE ALERT #${alertCount} tick ${idleStart}-${tick - 1} (${dur}s) ` +
          `pos=(${Math.floor(idleStartX / CELL)},${Math.floor(idleStartY / CELL)}) ` +
          `fire=${idleFireTicks} kills=${killsDuringIdle} terrain=${terrainChanged}`,
      )
    }
  }
}

if (summary) {
  console.log(`\n--- Summary tick ${fromTick}-${toTick} (${totalTraceTicks} ticks) ---`)
  console.log(`Outcome: ${outcome} at tick ${tick}`)
  console.log(`Idle alerts: ${alertCount}`)
  console.log(`\nBranch distribution:`)
  const sorted = [...branchCounts.entries()].sort((a, b) => b[1] - a[1])
  for (const [br, count] of sorted) {
    const pct = ((count / totalTraceTicks) * 100).toFixed(1)
    console.log(`  ${br}: ${count} (${pct}%)`)
  }
  console.log(`\nFire ticks: ${totalFireTicks} / ${totalTraceTicks}`)
  console.log(`Move=null ticks: ${totalMoveNullTicks} / ${totalTraceTicks}`)
}
