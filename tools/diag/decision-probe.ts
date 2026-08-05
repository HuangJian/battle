#!/usr/bin/env bun
/**
 * decision-probe.ts — Reusable God-AI decision probe at a specific tick.
 *
 * During the §88 campaign every root-cause hunt needed a bespoke
 * `tmp/probe-s88-decision.ts` / `probe-s29.ts` / `probe-compare.ts` — each
 * ~50 lines of copy-paste World/Simulation/GodAIInput wiring that printed one
 * particular decision slice at one particular tick. This tool is that probe,
 * fixed once: run the simulation to tick T with the given params, then print
 * the full decision context in one shot.
 *
 * Typical loop (§0.B):
 *   1. per-seed-diff diff shows "first divergence at tick T"
 *   2. bun tools/diag/decision-probe.ts <stage> <seed> <T> --set <k=v>   (B arm)
 *      bun tools/diag/decision-probe.ts <stage> <seed> <T>               (A arm)
 *   3. compare the two prints — the changed decision IS the divergence.
 *
 * Prints (all read-only World observation + public AI decision APIs):
 *   - world state: game state, frame, player cell/px, enemy roster
 *   - decision: selectTarget, threat state, chase target, chokepoint plan
 *     + cell, isBaseUnderThreat, getMoveDirection, _fire flag
 *   - branch counters (dodge/t8/aggressive/t2a/powerup/navigate/dead/chokepoint)
 *
 * Usage:
 *   bun tools/diag/decision-probe.ts <stage 1-35> <seed> <tick> [--set k=v ...]
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { CELL, BASE_POS, START_LIVES } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { STAGES } from '../../src/config/stages'

const USAGE = `
decision-probe.ts — God-AI decision probe at a specific tick.

Usage:
  bun tools/diag/decision-probe.ts <stageIdx> <seed> <tick> [--set k=v ...]

Prints the full decision context at tick <tick> (0-based, inclusive):
world state + selectTarget + threat/chase/chokepoint + branch counters.

Example:
  bun tools/diag/decision-probe.ts 27 12 2853 --set chokepointMode=1 --set threatPointMargin=2
`

function buildParams(): GodAIParams {
  const params: Record<string, number> = {
    ...(DEFAULT_GOD_AI_PARAMS as unknown as Record<string, number>),
  }
  for (let ai = 0; ai < process.argv.length; ai++) {
    if (process.argv[ai] !== '--set') continue
    const kv = process.argv[ai + 1]
    if (!kv || !kv.includes('=')) {
      console.error('--set expects key=value (e.g. --set chokepointMode=1)')
      process.exit(1)
    }
    const eq = kv.indexOf('=')
    const key = kv.slice(0, eq)
    const val = Number(kv.slice(eq + 1))
    if (isNaN(val) || !(key in DEFAULT_GOD_AI_PARAMS)) {
      console.error(`--set: unknown or non-numeric param '${key}'`)
      process.exit(1)
    }
    params[key] = val
  }
  return params as unknown as GodAIParams
}

// CLI stage is 1-based (1..35); internal index is 0-based.
const stageIdx = parseInt(process.argv[2] ?? '') - 1
const seed = parseInt(process.argv[3] ?? '')
const probeTick = parseInt(process.argv[4] ?? '')
if (
  isNaN(stageIdx) ||
  isNaN(seed) ||
  isNaN(probeTick) ||
  stageIdx < 0 ||
  stageIdx >= STAGES.length
) {
  console.error(USAGE)
  process.exit(1)
}

const stage = STAGES[stageIdx]
const world = new World()
world.rng.reseed(seed)
let difficulty = 'classic'
for (let ai = 0; ai < process.argv.length; ai++) {
  if (process.argv[ai] !== '--difficulty') continue
  const d = process.argv[ai + 1]
  if (d && DIFFICULTIES[d]) difficulty = d
}
world.difficultyKey = difficulty
world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
world.rules = RULES[difficulty] ?? DEFAULT_RULES
// §105 sync: mirror startGame()'s P1 init (hard/chaos ship playerStartLevel=1 /
// startLives=2 — without this the probe simulates level 0 / 3 lives).
world.playerLevel = world.difficulty?.playerStartLevel ?? 0
world.lives = world.difficulty?.startLives ?? START_LIVES
const godAIParams = buildParams()
const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
const input = new GodAIInput(world, godAIParams, godRng)
const sim = new Simulation(world, input)
world.loadStageData(stage, 0)
input.reset()

for (let tick = 0; tick <= probeTick; tick++) {
  sim.tick()
  input.endFrame()
  world.consumeEvents()
  const state = (world as unknown as { state: string }).state
  if (tick < probeTick && (state === 'gameover' || state === 'stageclear')) {
    console.log(
      `(game ended at tick ${tick} with state ${state} — probe tick ${probeTick} unreachable)`,
    )
    process.exit(0)
  }
}

// ---- read-only decision snapshot at the probe tick ----
// Capture the tick's own decision fields FIRST: getMoveDirection() below
// calls think(), which increments the branch counters — reading them after
// would include a probe think() the sim never made.
const p = world.player
const px = p ? Math.round((p.x / CELL) * 10) / 10 : -1
const py = p ? Math.round((p.y / CELL) * 10) / 10 : -1
const pc = input.playerCell()
const target = input.selectTarget(pc)
const enemies = world.tanks.filter((t) => t.alive && !t.isPlayer)
const state = (world as unknown as { state: string }).state
const branchCounts = { ...input.branchCounts }
const fireThisTick = input._fire
const moveDirThisTick = input._moveDir
// §116/§117: suicide-trade state (captured BEFORE the probe's own think below).
const suicideStanding = input._suicideStanding
const suicideStandTicks = input._suicideStandTicks
const suicideStandSuppress = input._suicideStandSuppress

console.log(
  `===== S${stageIdx + 1} ${stage.name} · seed ${seed} · tick ${probeTick} · ${state} =====`,
)
console.log(
  `player: cell (${pc.col},${pc.row}) px (${px},${py}) dir ${p?.dir ?? '?'} hp ${p?.hp}/${p?.maxHp} level ${p?.level ?? 0}`,
)
console.log(`enemies alive: ${enemies.length}`)
for (const e of enemies) {
  const ec = input.tankCell(e)
  console.log(
    `  enemy ${e.kind.padEnd(6)} cell (${ec.col},${ec.row}) px (${Math.round((e.x / CELL) * 10) / 10},${Math.round((e.y / CELL) * 10) / 10}) dir ${e.dir} hp ${e.hp}/${e.maxHp}`,
  )
}
console.log(`base: (${BASE_POS.col},${BASE_POS.row}) · HP ${world.baseHp}/${world.baseMaxHp}`)

console.log('\n--- decision (this tick) ---')
console.log(`_fire (this tick):   ${fireThisTick ? 'true' : 'false'}`)
console.log(`_moveDir (this tick): ${moveDirThisTick ?? 'null'}`)
console.log(
  `suicideStanding:     ${suicideStanding}  standTicks ${suicideStandTicks}  suppress ${suicideStandSuppress}`,
)
console.log(`selectTarget:        ${target ? `(${target.col},${target.row})` : 'null'}`)
console.log(`isBaseUnderThreat:   ${input.isBaseUnderThreat()}`)
console.log(`isThreatState(§88):  ${input.isThreatState()}`)
console.log(`threatChaseTarget:   ${JSON.stringify(input.threatChaseTarget() ?? null)}`)
const plan = input.chokepointPlan()
if (plan) {
  console.log(
    `chokepointPlan:       choke=(${plan.chokepoint?.col ?? '?'},${plan.chokepoint?.row ?? '?'}) threatPoints=${plan.threatPoints.length}`,
  )
} else {
  console.log(`chokepointPlan:       null (mode OFF or no plan)`)
}
console.log(`chokepointCell:      ${JSON.stringify(input.chokepointCell() ?? null)}`)
// NOTE: calls think() once — deterministic same-state re-derivation; the
// branch counters below are the snapshot taken BEFORE this call.
console.log(`getMoveDirection:    ${input.getMoveDirection() ?? 'null'}`)

console.log('\n--- branch counters (cumulative, excluding probe think) ---')
console.log(`  ${JSON.stringify(branchCounts)}`)
