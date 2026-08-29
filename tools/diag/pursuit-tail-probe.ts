#!/usr/bin/env bun
/**
 * pursuit-tail-probe.ts — §302 trigger-rate + aim-quality probe.
 *
 * ab-param answers "did the suite win rate move?" but not "did the mechanism
 * actually fire, and did it change the shot geometry?". A net-0 A/B on a
 * mechanism that fired 0.3% of ticks is vacuous; a net-0 A/B on one that fired
 * 12% of ticks is a real negative. This probe measures both arms in-process
 * (same loop shape as tools/diag/ab-diff.ts) and reports, per arm:
 *
 *   overTicks / overPct .......... §302 lane-merge overrides (ticks, % ticks)
 *   fireTicks .................... ticks the AI decided to fire
 *   alignedFire .................. fired AND an enemy sat on the bullet's line
 *                                  (the shot geometry §302 is trying to buy)
 *   perpFire ..................... fired AND the nearest enemy was on the
 *                                  CROSS axis (parallel-lane / sideways shot —
 *                                  the defect from §12.1 #3)
 *   laneTicks .................... ticks the player shares the nearest enemy's
 *                                  travel lane (its column when it travels
 *                                  vertically, its row when horizontal)
 *   kills / shots / ticks ........ outcome
 *
 * Reads only World state + the GodAIInput observation counters (AGENTS §2.1);
 * never mutates a World outside the normal simulation tick.
 *
 * Usage:
 *   bun tools/diag/pursuit-tail-probe.ts --stages all --seeds 1-60 [--mode 2]
 *   bun tools/diag/pursuit-tail-probe.ts --stages 21,26 --seeds 1-20 --mode 2
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { RNG } from '../../src/utils/RNG'
import { START_LIVES, CELL } from '../../src/constants'
import { arg, parseSeeds } from '../lib/cli'
import { parseStageSpec, StageSpecError } from '../lib/stage-spec'

const difficulty = arg('difficulty') ?? 'hard'
const seeds = parseSeeds(arg('seeds'), 60)
const stageSpec = arg('stages') ?? 'all'
let stageIdxs: number[]
try {
  stageIdxs = parseStageSpec(stageSpec, STAGES.length)
} catch (e) {
  console.error(e instanceof StageSpecError ? e.message : `invalid --stages: ${stageSpec}`)
  process.exit(1)
}
const candMode = Number(arg('mode', '2'))
/** Perpendicular (lane) gap budget — sweepable without touching src/. */
const laneGap = Number(arg('lane-gap', '4'))
const maxCells = Number(arg('max-cells', '9'))
const minCells = Number(arg('min-cells', '3'))
/** §302 mode-7 along-mode split: 0 both / 1 wake / 2 level-ahead / 3 yield. */
const alongMode = Number(arg('along-mode', '0'))

interface Agg {
  ticks: number
  overTicks: number
  holdTicks: number
  fireTicks: number
  alignedFire: number
  perpFire: number
  laneTicks: number
  iceTicks: number
  eIceTicks: number
  laneFire: number
  laneAlignedFire: number
  distSum: number
  axisFlips: number
  kills: number
  shots: number
  wins: number
  runs: number
}

const blank = (): Agg => ({
  ticks: 0,
  overTicks: 0,
  holdTicks: 0,
  fireTicks: 0,
  alignedFire: 0,
  perpFire: 0,
  laneTicks: 0,
  iceTicks: 0,
  eIceTicks: 0,
  laneFire: 0,
  laneAlignedFire: 0,
  distSum: 0,
  axisFlips: 0,
  kills: 0,
  shots: 0,
  wins: 0,
  runs: 0,
})

function runOnce(stageIdx: number, seed: number, mode: number, agg: Agg): void {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const params =
    mode === 0
      ? { ...DEFAULT_GOD_AI_PARAMS }
      : {
          ...DEFAULT_GOD_AI_PARAMS,
          pursuitTailMode: mode,
          pursuitTailMaxLaneGap: laneGap,
          pursuitTailMaxCells: maxCells,
          pursuitTailMinCells: minCells,
          pursuitTailAlongMode: alongMode,
        }
  const input = new GodAIInput(world, params, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], 0)
  input.reset()

  let tick = 0
  let lastOver = 0
  let lastHold = 0
  let lastAxis = -1
  while (tick < 36000) {
    sim.tick()
    // --- observation (read-only, after the tick) ---
    agg.ticks++
    // _pursuitTailOverrides is cumulative — take the per-tick delta.
    agg.overTicks += input._pursuitTailOverrides - lastOver
    lastOver = input._pursuitTailOverrides
    agg.holdTicks += input._pursuitTailHolds - lastHold
    lastHold = input._pursuitTailHolds
    const p = world.player
    if (p && p.alive) {
      if (input._fire) agg.fireTicks++
      // nearest live enemy + its travel axis
      let best: (typeof world.tanks)[number] | null = null
      let bestD = Infinity
      for (const t of world.tanks) {
        if (!t.alive || t.spawnTimer > 0) continue
        const d = Math.abs(t.x - p.x) + Math.abs(t.y - p.y)
        if (d < bestD) {
          bestD = d
          best = t
        }
      }
      if (best) {
        const eCol = Math.round(best.x / CELL)
        const eRow = Math.round(best.y / CELL)
        const pcCol = Math.round(p.x / CELL)
        const pcRow = Math.round(p.y / CELL)
        const vertical = Math.abs(best.vy) > Math.abs(best.vx)
        const lane = vertical ? eCol === pcCol : eRow === pcRow
        if (lane) agg.laneTicks++
        if (world.isTankOnIce(p)) agg.iceTicks++
        if (best && world.isTankOnIce(best)) agg.eIceTicks++
        agg.distSum += Math.abs(eCol - pcCol) + Math.abs(eRow - pcRow)
        // Axis stability: how often the nearest enemy's travel axis flips.
        // A lane that changes every ~1s cannot be merged into — this is the
        // number that decides whether §302 is a viable mechanism at all.
        const axisNow = Math.abs(best.vy) > Math.abs(best.vx) ? 1 : 0
        if (lastAxis >= 0 && axisNow !== lastAxis) agg.axisFlips++
        lastAxis = axisNow
        if (input._fire) {
          const horizontalShot = p.dir === 'left' || p.dir === 'right'
          const onLine = horizontalShot ? eRow === pcRow : eCol === pcCol
          if (onLine) agg.alignedFire++
          else if (vertical !== horizontalShot) agg.perpFire++
          // Conditional quality: of the ticks spent ON the lane, how many
          // produced an aligned shot? (base vs cand — this is the payoff.)
          if (lane) {
            agg.laneFire++
            if (onLine) agg.laneAlignedFire++
          }
        }
      }
    }
    input.endFrame()
    tick++
    if (world.state === 'stageclear' || world.state === 'gameover') break
  }
  agg.runs++
  agg.kills += world.killCount
  // Tank.fireCount is the monotonic per-World shot counter — the shot total
  // survives player death/respawn (killCount does not reset, so both are
  // end-of-run totals and need no per-tick bookkeeping).
  agg.shots += world.player?.fireCount ?? 0
  if (world.state === 'stageclear') agg.wins++
}

const pct = (a: number, b: number) => (b > 0 ? ((100 * a) / b).toFixed(2) : '0.00')

function report(label: string, agg: Agg): void {
  console.log(
    `${label.padEnd(6)} win ${agg.wins}/${agg.runs} (${pct(agg.wins, agg.runs)}%)  ` +
      `ticks ${agg.ticks}  over ${agg.overTicks} (${pct(agg.overTicks, agg.ticks)}%)  ` +
      `hold ${agg.holdTicks} (${pct(agg.holdTicks, agg.ticks)}%)  ` +
      `fire ${agg.fireTicks} (${pct(agg.fireTicks, agg.ticks)}%)  ` +
      `aligned ${agg.alignedFire} (${pct(agg.alignedFire, Math.max(1, agg.fireTicks))}% of fire)  ` +
      `perp ${agg.perpFire} (${pct(agg.perpFire, Math.max(1, agg.fireTicks))}% of fire)  ` +
      `lane ${agg.laneTicks} (${pct(agg.laneTicks, agg.ticks)}%)  ` +
      `laneFire ${agg.laneFire} (aligned ${pct(agg.laneAlignedFire, Math.max(1, agg.laneFire))}%)  ` +
      `ice ${pct(agg.iceTicks, agg.ticks)}%/eIce ${pct(agg.eIceTicks, agg.ticks)}%  ` +
      `meanDist ${(agg.distSum / Math.max(1, agg.ticks)).toFixed(2)}c  ` +
      `axisFlip/${Math.max(1, Math.round(agg.ticks / 60))}s ${agg.axisFlips}  ` +
      `kills ${agg.kills}  kills/shot ${(agg.kills / Math.max(1, agg.shots)).toFixed(3)}`,
  )
}

console.log(
  `§302 probe  difficulty=${difficulty} stages=${stageIdxs.length} seeds=${seeds.length} ` +
    `candMode=${candMode} laneGap=${laneGap} range=[${minCells},${maxCells}] alongMode=${alongMode}`,
)
const A = blank()
const B = blank()
for (const si of stageIdxs) {
  const a = blank()
  const b = blank()
  for (const seed of seeds) {
    runOnce(si, seed, 0, a)
    runOnce(si, seed, candMode, b)
  }
  for (const k of Object.keys(A) as Array<keyof Agg>) {
    A[k] += a[k]
    B[k] += b[k]
  }
  console.log(`--- s${si + 1} ${STAGES[si].name}`)
  report('base', a)
  report(`m${candMode}`, b)
  console.log(
    `       Δ win ${b.wins - a.wins >= 0 ? '+' : ''}${b.wins - a.wins}  ` +
      `Δ aligned ${b.alignedFire - a.alignedFire >= 0 ? '+' : ''}${b.alignedFire - a.alignedFire}  ` +
      `Δ perp ${b.perpFire - a.perpFire >= 0 ? '+' : ''}${b.perpFire - a.perpFire}  ` +
      `Δ lane ${b.laneTicks - a.laneTicks >= 0 ? '+' : ''}${b.laneTicks - a.laneTicks}`,
  )
}
console.log('=== SUITE')
report('base', A)
report(`m${candMode}`, B)
console.log(
  `Δ win ${B.wins - A.wins >= 0 ? '+' : ''}${B.wins - A.wins}  ` +
    `Δ aligned ${B.alignedFire - A.alignedFire >= 0 ? '+' : ''}${B.alignedFire - A.alignedFire}  ` +
    `Δ perp ${B.perpFire - A.perpFire >= 0 ? '+' : ''}${B.perpFire - A.perpFire}  ` +
    `Δ lane ${B.laneTicks - A.laneTicks >= 0 ? '+' : ''}${B.laneTicks - A.laneTicks}`,
)
