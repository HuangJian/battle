#!/usr/bin/env bun
/**
 * pursuit-tail-flip.ts — §302 per-tick forensics for one stage@seed.
 *
 * ab-param answers "how many seeds flipped"; this answers "WHY did this one
 * flip". For a single (stage, seed) it runs the baseline and the candidate
 * arm through the same in-process loop (identical setup to ab-param's
 * stageIndex=0 / hard / 36000-tick caliber, verified byte-identical on
 * s21+s26+s1 @ seeds 1-10) and prints a side-by-side timeline:
 *
 *   tick | base: pos dir kills lives baseHp | cand: pos dir kills lives baseHp
 *         | over = §302 override ticks in this window
 *
 * plus the first divergence tick with a ±12-tick context window, and the two
 * outcomes. The timeline is what separates "the candidate lost the base at
 * t=1200" (a defense/positioning failure) from "the candidate ran out of
 * lives" (a combat-exposure failure) from "timed out with 17/19 kills" (a
 * tempo failure) — three very different root causes that an aggregate win
 * count cannot distinguish.
 *
 * Usage:
 *   bun tools/diag/pursuit-tail-flip.ts --stage 26 --seed 9 --mode 3
 *   bun tools/diag/pursuit-tail-flip.ts --stage 21 --seed 26 --mode 3 --step 100
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { RNG } from '../../src/utils/RNG'
import { START_LIVES, CELL } from '../../src/constants'
import { arg } from '../lib/cli'

const difficulty = arg('difficulty') ?? 'hard'
const stageNo = Number(arg('stage', '26'))
const seed = Number(arg('seed', '9'))
const candMode = Number(arg('mode', '3'))
const step = Number(arg('step', '300'))
const maxTicks = Number(arg('max-ticks', '36000'))
const stageIdx = stageNo - 1

interface Snap {
  t: number
  col: number
  row: number
  dir: string
  kills: number
  lives: number
  hp: number
  over: number
  fire: number
}

interface Arm {
  snaps: Snap[]
  trace: string[]
  outcome: string
  ticks: number
  kills: number
  over: number
}

function run(mode: number, wantTrace: boolean): Arm {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const params =
    mode === 0 ? { ...DEFAULT_GOD_AI_PARAMS } : { ...DEFAULT_GOD_AI_PARAMS, pursuitTailMode: mode }
  const input = new GodAIInput(world, params, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], 0)
  input.reset()

  const snaps: Snap[] = []
  const trace: string[] = []
  let over = 0
  let lastOver = 0
  let tick = 0
  while (tick < maxTicks) {
    sim.tick()
    over += input._pursuitTailOverrides - lastOver
    lastOver = input._pursuitTailOverrides
    const p = world.player
    if (tick % step === 0 && p) {
      snaps.push({
        t: tick,
        col: Math.round(p.x / CELL),
        row: Math.round(p.y / CELL),
        dir: p.dir,
        kills: world.killCount,
        lives: world.lives,
        hp: world.baseHp ?? -1,
        over,
        fire: p.fireCount ?? 0,
      })
      over = 0
    }
    if (wantTrace && p) {
      trace.push(
        `${tick}|${(p.x / CELL).toFixed(1)},${(p.y / CELL).toFixed(1)}|${p.dir}|` +
          `${input._fire ? 'F' : '.'}|${input._moveDir ?? '-'}|k${world.killCount}|` +
          `hp${world.baseHp ?? -1}|${input._pursuitTailOverrides - (lastOver - (input._pursuitTailOverrides - lastOver)) === 0 ? '' : ''}`,
      )
    }
    input.endFrame()
    tick++
    if (world.state === 'stageclear' || world.state === 'gameover') break
  }
  return {
    snaps,
    trace,
    outcome: world.state,
    ticks: tick,
    kills: world.killCount,
    over: input._pursuitTailOverrides,
  }
}

const A = run(0, true)
const B = run(candMode, true)

console.log(
  `§302 flip forensics  ${difficulty} s${stageNo} (${STAGES[stageIdx].name}) @seed ${seed}  ` +
    `candMode=${candMode}  step=${step}`,
)
console.log(
  `base: ${A.outcome} @${A.ticks}t  kills ${A.kills}  overrides ${A.over}\n` +
    `cand: ${B.outcome} @${B.ticks}t  kills ${B.kills}  overrides ${B.over}`,
)

// First divergence in the raw trace.
let firstDiff = -1
for (let i = 0; i < Math.max(A.trace.length, B.trace.length); i++) {
  if (A.trace[i] !== B.trace[i]) {
    firstDiff = i
    break
  }
}
if (firstDiff >= 0) {
  console.log(`\n--- first divergence at tick ${firstDiff} (context ±12)`)
  const lo = Math.max(0, firstDiff - 12)
  const hi = Math.min(Math.max(A.trace.length, B.trace.length) - 1, firstDiff + 12)
  for (let i = lo; i <= hi; i++) {
    const mark = i === firstDiff ? '>>' : '  '
    console.log(`${mark} A: ${A.trace[i] ?? '-'}`)
    console.log(`${mark} B: ${B.trace[i] ?? '-'}`)
  }
}

// Side-by-side timeline.
console.log(`\n--- timeline (every ${step} ticks; over = §302 override ticks in the window)`)
console.log(
  '  tick | base  pos    dir    k  lv  hp  over shots | cand  pos    dir    k  lv  hp  over shots',
)
const n = Math.max(A.snaps.length, B.snaps.length)
for (let i = 0; i < n; i++) {
  const a = A.snaps[i]
  const b = B.snaps[i]
  const f = (s?: Snap) =>
    s
      ? `${String(s.col).padStart(2)},${String(s.row).padStart(2)} ${s.dir.padEnd(5)} ` +
        `${String(s.kills).padStart(2)} ${String(s.lives).padStart(2)} ${String(s.hp).padStart(3)} ` +
        `${String(s.over).padStart(4)} ${String(s.fire).padStart(4)}`
      : ' '.repeat(34)
  console.log(`${String(i * step).padStart(6)} | ${f(a)} | ${f(b)}`)
}
