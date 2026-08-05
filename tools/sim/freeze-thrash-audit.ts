#!/usr/bin/env bun
/**
 * freeze-thrash-audit.ts — quantify the §80 "aim/navigate turn-thrash".
 *
 * The bug (see DECISIONS §80): during a freeze window the God AI runs the
 * `aggressive` branch on every tick. That branch has two sub-decisions whose
 * preconditions each RECREATE the other's:
 *
 *   - stop-and-aim  : `scanAhead(aimDir).enemy` → `_moveDir = aimDir` (turn)
 *   - navigate      : `followPath()`            → `_moveDir = path[0]`
 *
 * Turning is not free: `Simulation.updateMovement` snaps the perpendicular
 * axis (`tank.y = snap(tank.y, CELL)`) whenever the travel axis changes. That
 * snap moves the tank's CENTRE up to CELL/2 px off the line it wanted to fire
 * along, flipping `scanAhead`'s answer — so the tank ping-pongs between two
 * sub-cell positions with zero net displacement, for the whole freeze window.
 *
 * This tool measures, per run:
 *   - freezeTicks   : ticks with world.freezeTimer > 0 and the AI tank alive
 *   - thrashTicks   : ticks where pos(t) == pos(t-2) != pos(t-1)  (period-2
 *                     oscillation, zero net displacement)
 *   - worstRun      : longest consecutive thrash streak, in ticks
 *   - freezeKills   : kills scored while frozen (the window's whole point)
 *
 * It mirrors `runSimulation`'s wiring exactly so numbers are comparable with
 * regression-check.ts.
 *
 * Usage:
 *   bun tools/sim/freeze-thrash-audit.ts --stages 1-35 --seeds 1-10 [--mode coop|single]
 *   bun tools/sim/freeze-thrash-audit.ts --stages 1-35 --seeds 1-10 --set aimTurnSnapGuard=0
 *
 * Param override: --set <key>=<value> (repeatable) overrides any numeric
 * GodAIParams key on top of the defaults — same convention as
 * tools/diag/per-seed-diff.ts (§0.C rule 2: generic, no tool changes per
 * diagnostic). Used for the §80 A/B: default run = guard ON (the fix),
 * `--set aimTurnSnapGuard=0` = pre-§80 baseline.
 */

import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { RNG } from '../../src/utils/RNG'
import type { GameState, Tank } from '../../src/types'

const stateOf = (w: World): GameState => w.state

function parseRange(spec: string): number[] {
  if (spec.includes('-')) {
    const [s, e] = spec.split('-').map(Number)
    return Array.from({ length: e - s + 1 }, (_, i) => s + i)
  }
  return [parseInt(spec, 10)]
}

interface RunAudit {
  stage: number
  seed: number
  outcome: string
  freezeTicks: number
  thrashTicks: number
  worstRun: number
  freezeKills: number
  totalKills: number
}

function auditRun(
  stageIdx: number,
  seed: number,
  difficulty: string,
  coop: boolean,
  overrides: Record<string, number>,
): RunAudit {
  const stage = STAGES[stageIdx]
  // Fresh copy — --set mutates params below, never touch the shared default.
  const godAIParams = { ...DEFAULT_GOD_AI_PARAMS }
  // Generic param override (see header) — applied on top of the defaults so
  // --set wins, matching per-seed-diff.ts.
  for (const [key, val] of Object.entries(overrides)) {
    ;(godAIParams as unknown as Record<string, number>)[key] = val
  }

  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES

  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, godAIParams, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(stage, stageIdx)
  input.reset()

  let coopInput: GodAIInput | null = null
  if (coop) {
    world.coop = true
    const d = world.difficulty
    world.lives2 = d?.startLives ?? 3
    world.playerLevel2 = d?.playerStartLevel ?? 0
    const p1Col = world.playerSpawnPoint?.col ?? 8
    world.player2SpawnPoint = { col: 24 - p1Col, row: 24 }
    world.spawnPlayer2()
    const coopRng = new RNG((seed ^ 0x9e3779b9 ^ 0xdeadbeef) >>> 0)
    coopInput = new GodAIInput(world, godAIParams, coopRng, (w) => w.player2)
    coopInput.reset()
    sim.input2 = coopInput
  }

  // The tank whose stalling we measure: in coop the God AI drives player2.
  const watched = (): Tank | null => (coop ? world.player2 : world.player)

  let freezeTicks = 0
  let thrashTicks = 0
  let worstRun = 0
  let curRun = 0
  let freezeKills = 0
  let prevKills = 0
  let x1 = NaN
  let y1 = NaN
  let x2 = NaN
  let y2 = NaN

  let tick = 0
  const maxTicks = 36000
  let outcome = 'max_ticks'
  while (tick < maxTicks) {
    const frozenBefore = world.freezeTimer > 0
    sim.tick()
    input.endFrame()
    coopInput?.endFrame()
    world.consumeEvents()
    tick++

    const t = watched()
    if (frozenBefore && t?.alive) {
      freezeTicks++
      // Period-2 zero-net-displacement detector.
      if (t.x === x2 && t.y === y2 && (t.x !== x1 || t.y !== y1)) {
        thrashTicks++
        curRun++
        if (curRun > worstRun) worstRun = curRun
      } else {
        curRun = 0
      }
      freezeKills += world.killCount - prevKills
    } else {
      curRun = 0
    }
    prevKills = world.killCount
    x2 = x1
    y2 = y1
    x1 = t?.x ?? NaN
    y1 = t?.y ?? NaN

    const s = stateOf(world)
    if (s === 'stageclear' || s === 'victory') {
      outcome = 'stage_clear'
      break
    }
    if (s === 'gameover') {
      outcome = 'gameover'
      break
    }
  }

  return {
    stage: stageIdx,
    seed,
    outcome,
    freezeTicks,
    thrashTicks,
    worstRun,
    freezeKills,
    totalKills: world.killCount,
  }
}

if (import.meta.main) {
  const arg = (n: string, d: string) => {
    const i = process.argv.indexOf(`--${n}`)
    return i >= 0 ? process.argv[i + 1] : d
  }
  const difficulty = arg('difficulty', 'classic')
  const mode = arg('mode', 'coop')
  const seeds = parseRange(arg('seeds', '1-10'))
  const stages = parseRange(arg('stages', '1-35'))
    .map((n) => n - 1) // CLI is 1-based (1..35); internal index is 0-based
    .filter((i) => i >= 0 && i < STAGES.length)
  const coop = mode === 'coop'

  // --set <key>=<value>, repeatable (same convention as per-seed-diff.ts).
  const overrides: Record<string, number> = {}
  for (let ai = 0; ai < process.argv.length; ai++) {
    if (process.argv[ai] !== '--set') continue
    const kv = process.argv[ai + 1]
    if (!kv || !kv.includes('=')) {
      console.error('--set expects key=value (e.g. --set aimTurnSnapGuard=0)')
      process.exit(1)
    }
    const eq = kv.indexOf('=')
    const key = kv.slice(0, eq)
    const val = Number(kv.slice(eq + 1))
    if (isNaN(val) || !(key in DEFAULT_GOD_AI_PARAMS)) {
      console.error(`--set: unknown or non-numeric param '${key}'`)
      process.exit(1)
    }
    overrides[key] = val
  }

  const runs: RunAudit[] = []
  let done = 0
  const grand = stages.length * seeds.length
  for (const s of stages) {
    for (const seed of seeds) {
      runs.push(auditRun(s, seed, difficulty, coop, overrides))
      done++
      if (done % 20 === 0 || done === grand) {
        process.stderr.write(`\r  [freeze-thrash-audit:${mode}] ${done}/${grand}...`)
      }
    }
  }
  process.stderr.write('\n')

  const sum = (f: (r: RunAudit) => number) => runs.reduce((a, r) => a + f(r), 0)
  const freezeTicks = sum((r) => r.freezeTicks)
  const thrashTicks = sum((r) => r.thrashTicks)
  const worst = Math.max(...runs.map((r) => r.worstRun))
  const stalled = runs.filter((r) => r.worstRun >= 60) // >=1s frozen-in-place

  console.log(
    JSON.stringify(
      {
        config: {
          difficulty,
          mode,
          stages: stages.length,
          seeds: seeds.length,
          runs: runs.length,
          overrides,
        },
        perStage: STAGES.map((s, i) => {
          const rs = runs.filter((r) => r.stage === i)
          if (rs.length === 0) return undefined
          const clears = rs.filter((r) => r.outcome === 'stage_clear').length
          return {
            stage: s.name,
            clears,
            runs: rs.length,
            winRate: +((clears / rs.length) * 100).toFixed(1),
            freezeTicks: rs.reduce((a, r) => a + r.freezeTicks, 0),
            thrashTicks: rs.reduce((a, r) => a + r.thrashTicks, 0),
            freezeKills: rs.reduce((a, r) => a + r.freezeKills, 0),
            totalKills: rs.reduce((a, r) => a + r.totalKills, 0),
            worstStreakTicks: Math.max(...rs.map((r) => r.worstRun)),
          }
        }),
        freezeTicks,
        thrashTicks,
        thrashPct: freezeTicks ? +((thrashTicks / freezeTicks) * 100).toFixed(2) : 0,
        worstStreakTicks: worst,
        runsWithStall1s: stalled.length,
        runsWithStall1sPct: +((stalled.length / runs.length) * 100).toFixed(2),
        freezeKills: sum((r) => r.freezeKills),
        totalKills: sum((r) => r.totalKills),
        worstOffenders: [...runs]
          .sort((a, b) => b.worstRun - a.worstRun)
          .slice(0, 10)
          .map(
            (r) =>
              `s${r.stage + 1}/seed${r.seed}: streak=${r.worstRun}t thrash=${r.thrashTicks}/${r.freezeTicks} ${r.outcome}`,
          ),
      },
      null,
      2,
    ),
  )
}
