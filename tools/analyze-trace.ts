#!/usr/bin/env bun
/**
 * analyze-trace.ts — Quick trace analysis tool.
 * Usage: bun tools/analyze-trace.ts <trace-file.json>
 */
import { readFileSync } from 'fs'

const file = process.argv[2]
if (!file) {
  console.error('Usage: bun tools/analyze-trace.ts <trace-file.json>')
  process.exit(1)
}

const trace = JSON.parse(readFileSync(file, 'utf8'))

// Find idle ticks where player is stopped with enemies but not firing.
const idle = trace.ticks.filter((t: any) => t.mv === null && !t.fr && t.enemies.length > 0)
console.log('=== Idle ticks with enemies present:', idle.length, '===\n')

// Show first 10 with detail.
for (const t of idle.slice(0, 10)) {
  console.log(
    `tick ${t.tick}: pos=(${t.pc.col},${t.pc.row}) dir=${t.pdir} enemies=${JSON.stringify(t.enemies.map((e: any) => `(${e.k},${e.c},${e.r})`))} bullets=${t.bullets} threats=${t.threats}`,
  )
}

// Show context around first idle.
if (idle.length > 0) {
  const firstIdle = idle[0]
  console.log(`\n--- Context around first idle tick ${firstIdle.tick} ---`)
  const around = trace.ticks.filter((t: any) => Math.abs(t.tick - firstIdle.tick) <= 36)
  for (const t of around) {
    const evStr = t.ev.length > 0 ? ` EV: ${t.ev.join(',')}` : ''
    console.log(
      `tick ${t.tick}: pos=(${t.pc.col},${t.pc.row}) dir=${t.pdir} mv=${t.mv} fr=${t.fr} branch=${t.branch} enemies=${t.enemies.length} threats=${t.threats} baseT=${t.baseThreats}${evStr}`,
    )
  }
}

// Analyze firing patterns.
const firing = trace.ticks.filter((t: any) => t.fr)
const firingWhileMoving = firing.filter((t: any) => t.mv !== null)
const firingWhileStopped = firing.filter((t: any) => t.mv === null)
console.log(`\n=== Firing analysis ===`)
console.log(`Total firing ticks: ${firing.length}`)
console.log(`Firing while stopped: ${firingWhileStopped.length}`)
console.log(`Firing while moving: ${firingWhileMoving.length}`)

// Check for cooldown patterns - long gaps between fires.
const fireTicks = firing.map((t: any) => t.tick)
if (fireTicks.length > 1) {
  const gaps: number[] = []
  for (let i = 1; i < fireTicks.length; i++) {
    gaps.push(fireTicks[i] - fireTicks[i - 1])
  }
  gaps.sort((a, b) => b - a)
  console.log(`Largest fire gaps (ticks): ${gaps.slice(0, 10).join(', ')}`)
  console.log(`Avg fire gap: ${(gaps.reduce((a, b) => a + b, 0) / gaps.length).toFixed(1)} ticks`)
}

// Analyze movement patterns.
const moving = trace.ticks.filter((t: any) => t.mv !== null)
const stopped = trace.ticks.filter((t: any) => t.mv === null)
console.log(`\n=== Movement analysis ===`)
console.log(
  `Moving: ${moving.length} ticks (${((moving.length / trace.ticks.length) * 100).toFixed(0)}%)`,
)
console.log(
  `Stopped: ${stopped.length} ticks (${((stopped.length / trace.ticks.length) * 100).toFixed(0)}%)`,
)

// Direction distribution.
const dirCounts: Record<string, number> = {}
for (const t of moving) {
  const key = t.mv as string
  dirCounts[key] = (dirCounts[key] ?? 0) + 1
}
console.log(`Move directions: ${JSON.stringify(dirCounts)}`)

// Analyze base defense.
const baseThreatTicks = trace.ticks.filter((t: any) => t.baseThreats > 0)
console.log(`\n=== Base defense analysis ===`)
console.log(`Ticks with base threats: ${baseThreatTicks.length}`)
if (baseThreatTicks.length > 0) {
  console.log(`First base threat at tick ${baseThreatTicks[0].tick}`)
  console.log(`Last base threat at tick ${baseThreatTicks[baseThreatTicks.length - 1].tick}`)
  // What was the player doing during base threats?
  const branchDuringThreat: Record<string, number> = {}
  for (const t of baseThreatTicks) {
    branchDuringThreat[t.branch] = (branchDuringThreat[t.branch] ?? 0) + 1
  }
  console.log(`Branches during base threats: ${JSON.stringify(branchDuringThreat)}`)
}

// Kill events.
const killEvents = trace.ticks.filter((t: any) => t.ev.includes('tank_destroyed'))
console.log(`\n=== Kill events ===`)
for (const t of killEvents) {
  console.log(
    `tick ${t.tick}: pos=(${t.pc.col},${t.pc.row}) branch=${t.branch} enemies remaining=${t.enemies.length}`,
  )
}

console.log(`\n=== Outcome: ${trace.outcome} at tick ${trace.totalTicks} ===`)
if (trace.failure) {
  console.log(`Failure: ${trace.failure.cause} at tick ${trace.failure.tick}`)
  console.log(`Player dist to base: ${trace.failure.playerDistToBase}`)
  console.log(`First kill at tick: ${trace.failure.firstKillTick}`)
}
