// per-seed-diff.ts — Reusable per-seed tick-diff diagnostic for God AI regressions.
//
// USAGE (3-step workflow):
//   1. bun tools/diag/per-seed-diff.ts dump <stageIdx> <seed> > /tmp/before.txt
//   2. git stash   (or git checkout the files you changed)
//   3. bun tools/diag/per-seed-diff.ts dump <stageIdx> <seed> > /tmp/after.txt
//   4. git stash pop  (restore your changes)
//   5. bun tools/diag/per-seed-diff.ts diff /tmp/before.txt /tmp/after.txt
//
// The "dump" mode runs one simulation and writes a compact per-tick signature.
// The "diff" mode compares two dumps and reports the first divergence tick +
// surrounding context, so you can see exactly which AI decision changed.
//
// WHY THIS EXISTS (§70 lesson): aggregate win-rate comparison can mask
// per-seed regressions caused by V8 JIT sensitivity to hot-loop code changes.
// By comparing tick-by-tick signatures, you can pinpoint the exact tick and
// decision (fire/moveDir/position) where the AI diverges, then trace that
// back to the code change responsible. See DECISIONS.md §70 for the full
// case study; method write-up: docs/god-ai-tuning.progress.md §0.B.
//
// NOTE: Requires the GodAIInput to be the player's input (solo mode, not coop).

import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { applyStageOverrides } from '../../src/ai/godai-stage-overrides'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { CELL } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { STAGES } from '../../src/config/stages'
import { readFileSync } from 'fs'

const USAGE = `
per-seed-diff.ts — per-seed tick-diff diagnostic for God AI regressions.

Usage:
  bun tools/diag/per-seed-diff.ts dump <stageIdx> <seed>   Dump tick signatures
  bun tools/diag/per-seed-diff.ts diff <fileA> <fileB>     Compare two dumps

Diagnostic flags (dump mode only):
  --set <key>=<value>   Override any numeric GodAIParams key (repeatable).
                        Generic — reuse for any future parameter diagnostic
                        without tool changes (progress doc §0.C rule 2).
                        e.g. --set evasionSteelOcclusion=1 / --set counterFire=0

Workflow:
  1. bun tools/diag/per-seed-diff.ts dump 32 5 > /tmp/before.txt
  2. bun tools/diag/per-seed-diff.ts dump 32 5 --set evasionSteelOcclusion=1 > /tmp/after.txt
  3. bun tools/diag/per-seed-diff.ts diff /tmp/before.txt /tmp/after.txt
`

function dump(stageIdx: number, seed: number): void {
  const stage = STAGES[stageIdx]
  const godAIParams = applyStageOverrides(stage.name, DEFAULT_GOD_AI_PARAMS)
  // Generic param override (dump mode only): --set <key>=<value>, repeatable.
  // Any numeric GodAIParams key — future diagnostics need no tool changes.
  // Param-specific hardcoded flags (--steelOcclusion / --noCounterFire /
  // --brickGate) are deliberately unsupported; see progress doc §0.C rule 2.
  for (let ai = 0; ai < process.argv.length; ai++) {
    if (process.argv[ai] !== '--set') continue
    const kv = process.argv[ai + 1]
    if (!kv || !kv.includes('=')) {
      console.error('--set expects key=value (e.g. --set counterFire=0)')
      process.exit(1)
    }
    const eq = kv.indexOf('=')
    const key = kv.slice(0, eq)
    const val = Number(kv.slice(eq + 1))
    if (isNaN(val) || !(key in godAIParams)) {
      console.error(`--set: unknown or non-numeric param '${key}'`)
      process.exit(1)
    }
    ;(godAIParams as unknown as Record<string, number>)[key] = val
  }
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, godAIParams, godRng)
  const sim = new Simulation(world, input)
  // NOTE: stageIndex=0 matches runSimulation's default — the index only affects
  // scoring formulas, not gameplay, but using 0 ensures byte-identical parity
  // with the eval suite and regression gate.
  world.loadStageData(stage, 0)
  input.reset()

  const maxTicks = 18000
  for (let tick = 0; tick < maxTicks; tick++) {
    sim.tick()
    input.endFrame()
    // Consume events to match runSimulation's behavior (events accumulate
    // in world and consumeEvents clears them each tick).
    world.consumeEvents()

    const p = world.player
    const px = p ? Math.round((p.x / CELL) * 10) / 10 : -1
    const py = p ? Math.round((p.y / CELL) * 10) / 10 : -1
    const enemies = world.tanks.filter((t) => t.alive && !t.isPlayer).length
    const ebullets = world.bullets.filter((b) => b.alive && !b.isPlayer).length
    const pbullets = world.bullets.filter((b) => b.alive && b.isPlayer).length
    const state = (world as any).state

    // Compact signature: tick|px,py|dir|fire|moveDir|enemies|ebullets|pbullets|state
    console.log(
      `${tick}|${px},${py}|${p?.dir ?? '?'}|${input._fire ? 'F' : '.'}|${input._moveDir ?? '-'}|e${enemies}|eb${ebullets}|pb${pbullets}|${state}`,
    )

    if (state === 'gameover' || state === 'stageclear') break
  }
}

function diff(fileA: string, fileB: string): void {
  const linesA = readFileSync(fileA, 'utf-8').trim().split('\n')
  const linesB = readFileSync(fileB, 'utf-8').trim().split('\n')

  const maxLen = Math.max(linesA.length, linesB.length)
  let firstDiverge = -1

  for (let i = 0; i < maxLen; i++) {
    if (i >= linesA.length || i >= linesB.length) {
      firstDiverge = i
      break
    }
    if (linesA[i] === linesB[i]) {
      // still matching
    } else {
      firstDiverge = i
      break
    }
  }

  if (firstDiverge === -1) {
    console.log('IDENTICAL — no divergence found.')
    return
  }

  console.log(
    `First divergence at tick ${firstDiverge < linesA.length ? linesA[firstDiverge].split('|')[0] : '?'} (line ${firstDiverge + 1})`,
  )
  console.log()

  // Show context: 3 ticks before, the divergence, and 5 ticks after
  const ctxStart = Math.max(0, firstDiverge - 3)
  const ctxEnd = Math.min(maxLen, firstDiverge + 6)

  for (let i = ctxStart; i < ctxEnd; i++) {
    const a = i < linesA.length ? linesA[i] : '(missing)'
    const b = i < linesB.length ? linesB[i] : '(missing)'
    const marker = a === b ? '  ' : '>>'
    console.log(`${marker} A: ${a}`)
    if (a !== b) {
      console.log(`${marker} B: ${b}`)
    }
  }

  // Parse the divergence fields
  if (firstDiverge < linesA.length && firstDiverge < linesB.length) {
    const fieldsA = linesA[firstDiverge].split('|')
    const fieldsB = linesB[firstDiverge].split('|')
    const labels = [
      'tick',
      'pos',
      'dir',
      'fire',
      'moveDir',
      'enemies',
      'ebullets',
      'pbullets',
      'state',
    ]
    const changed: string[] = []
    for (let f = 0; f < labels.length; f++) {
      if (fieldsA[f] !== fieldsB[f]) {
        changed.push(`${labels[f]}: ${fieldsA[f] ?? '?'} → ${fieldsB[f] ?? '?'}`)
      }
    }
    console.log()
    console.log(`Changed fields: ${changed.join(', ')}`)
  }

  // Show final outcomes
  const lastA = linesA[linesA.length - 1] ?? ''
  const lastB = linesB[linesB.length - 1] ?? ''
  const outcomeA = lastA.split('|').pop() ?? '?'
  const outcomeB = lastB.split('|').pop() ?? '?'
  console.log()
  console.log(`Outcome A: ${outcomeA} (${linesA.length} ticks)`)
  console.log(`Outcome B: ${outcomeB} (${linesB.length} ticks)`)
  if (outcomeA !== outcomeB) {
    console.log(`*** OUTCOME FLIPPED ***`)
  }
}

// CLI
const mode = process.argv[2]
if (mode === 'dump') {
  const stageIdx = parseInt(process.argv[3] ?? '')
  const seed = parseInt(process.argv[4] ?? '')
  if (isNaN(stageIdx) || isNaN(seed)) {
    console.error(USAGE)
    process.exit(1)
  }
  dump(stageIdx, seed)
} else if (mode === 'diff') {
  const fileA = process.argv[3]
  const fileB = process.argv[4]
  if (!fileA || !fileB) {
    console.error(USAGE)
    process.exit(1)
  }
  diff(fileA, fileB)
} else {
  console.error(USAGE)
  process.exit(1)
}
