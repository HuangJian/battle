#!/usr/bin/env bun
/**
 * dodge-cf.ts — §223 dodge-death counterfactual (protocol §6 discipline:
 * confirm an actionable window BEFORE designing a candidate).
 *
 * Question: in runs where the player DIED while in the dodge branch, was the
 * death avoidable locally? Replay 0..(T-60) with the God AI (factual prefix),
 * clone the world, then run 4 scripted branches for ≤240 ticks:
 *
 *   factual        — God AI continues (must reproduce the death)
 *   turn-and-fire  — hold position, face the nearest threat, fire (M3 gunfight)
 *   advance        — close distance to the nearest threat (kill the source)
 *   hard-away      — run away from the centroid of oncoming bullets (dodge+)
 *
 * Surviving branch (player alive + base undamaged at window end) = the death
 * had an actionable local window → candidate space exists. All-dead = the
 * dodge-death family is not locally fixable (confirms §215 M3 idle verdict
 * extended to moving dodge).
 *
 * Usage:
 *   bun tmp/dodge-cf.ts --from-json tmp/open-test-forensics-baseline.json \
 *       [--limit 20] [--window 60] [--json tmp/dodge-cf.json] [--dump S5s5]
 */
import { STAGES } from '../../src/config/stages'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import type { InputLike } from '../../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { RNG } from '../../src/utils/RNG'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import { tankCenterCell } from '../../src/ai/god/ThreatBudget'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { DIFFICULTIES } from '../../src/config/difficulty'
import type { Direction } from '../../src/constants'
import type { Bullet, Tank } from '../../src/types'

import { arg } from '../lib/cli'
const fromJson = arg('from-json') ?? 'tmp/open-test-forensics-baseline.json'
const limit = Number(arg('limit') ?? '999')
const windowTicks = Number(arg('window') ?? '60')
const jsonOut = arg('json')
const dumpKey = arg('dump')

const j = JSON.parse(await Bun.file(fromJson).text())
const failures = (
  j.perDifficulty.hard.failures as Array<{
    stageIdx: number
    seed: number
    forensics: { events: Array<{ tick: number; type: string; detail: string }> }
  }>
).filter((f) => {
  const deaths = f.forensics.events.filter((e) => e.type === 'death')
  if (deaths.length === 0) return false
  // last action at terminal tick was dodge (death runs end at the death tick)
  return true
})

interface BranchOut {
  playerDied: boolean
  diedAt: number // offset; -1 none
  baseDamaged: boolean
  baseFirstDamageAt: number
  endState: string
  movedTicks: number
  fires: number
}

function runBranch(
  world: World,
  sim: Simulation,
  input: InputLike,
  baseHpAtStart: number,
): BranchOut {
  let fires = 0
  let moved = 0
  let playerDied = false
  let diedAt = -1
  let baseDamaged = false
  let baseFirst = -1
  const lives0 = world.lives
  const px0 = world.player?.x ?? 0
  const py0 = world.player?.y ?? 0
  for (let off = 1; off <= windowTicks; off++) {
    sim.tick()
    input.endFrame()
    const p = world.player
    if (p) {
      const disp = Math.abs(p.x - px0) + Math.abs(p.y - py0)
      if (disp >= 8 && moved === 0) moved = off
      if (!p.alive || world.lives < lives0) {
        if (!playerDied) {
          playerDied = true
          diedAt = off
        }
      }
    } else if (!playerDied) {
      playerDied = true
      diedAt = off
    }
    if (world.baseHp < baseHpAtStart && !baseDamaged) {
      baseDamaged = true
      baseFirst = off
    }
    const evs = world.consumeEvents()
    for (const e of evs) if (e.type === 'bullet_fired') fires++
    if (world.state !== 'playing') break
    if (playerDied && baseDamaged) break
  }
  return {
    playerDied,
    diedAt,
    baseDamaged,
    baseFirstDamageAt: baseFirst,
    endState: world.state,
    movedTicks: moved,
    fires,
  }
}

/** Nearest live enemy (fallback threat id) — the one with min center distance. */
function nearestEnemyId(world: World): number | null {
  const p = world.player
  if (!p) return null
  let best: number | null = null
  let bestD = Infinity
  for (const e of world.tanks) {
    if (!e.alive || e.kind === 'player') continue
    const d = Math.abs(e.x - p.x) + Math.abs(e.y - p.y)
    if (d < bestD) {
      bestD = d
      best = e.id
    }
  }
  return best
}

class ScriptedInput implements InputLike {
  constructor(private plan: () => { move: Direction | null; fire: boolean }) {}
  getMoveDirection(): Direction | null {
    return this.plan().move
  }
  isFiring(): boolean {
    return this.plan().fire
  }
  wasItemPressed(): boolean {
    return false
  }
  endFrame(): void {}
  reset(): void {}
}

function tankById(world: World, id: number | null): Tank | null {
  if (id == null) return null
  for (const t of world.tanks) if (t.id === id) return t
  return null
}

/** Stand: turn toward the threat (Input turn semantics), fire when facing. */
function planTurnAndFire(
  world: World,
  threatId: () => number | null,
): { move: Direction | null; fire: boolean } {
  const p = world.player
  const t = threatId()
  if (!p || t == null) return { move: null, fire: false }
  const tTank = tankById(world, t)
  const tc = tTank ? tankCenterCell(tTank) : null
  const pc = tankCenterCell(p)
  if (!tc || !pc) return { move: null, fire: false }
  if (tc.col === pc.col && tc.row === pc.row) return { move: null, fire: false }
  const dx = tc.col - pc.col
  const dy = tc.row - pc.row
  const aim: Direction =
    Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up'
  if (aim !== p.dir) return { move: aim, fire: false }
  return { move: null, fire: true }
}

/** Advance toward the nearest enemy (close the kill). */
function planAdvance2(
  world: World,
  threatId: () => number | null,
): { move: Direction | null; fire: boolean } {
  const p = world.player
  const t = threatId()
  if (!p || t == null) return { move: null, fire: false }
  const tTank = tankById(world, t)
  const tc = tTank ? tankCenterCell(tTank) : null
  const pc = tankCenterCell(p)
  if (!tc || !pc) return { move: null, fire: false }
  const dx = tc.col - pc.col
  const dy = tc.row - pc.row
  const aim: Direction =
    Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up'
  if (aim !== p.dir) return { move: aim, fire: false }
  const aligned = tc.col === pc.col || tc.row === pc.row
  const dist = Math.abs(dx) + Math.abs(dy)
  return { move: aim, fire: aligned && dist <= 8 }
}

/** Hard-away: run away from the centroid of bullets within 6 cells. */
function planHardAway(world: World): { move: Direction | null; fire: boolean } {
  const p = world.player
  if (!p) return { move: null, fire: false }
  let cx = 0
  let cy = 0
  let n = 0
  for (const b of world.bullets) {
    if (!b.alive || b.isPlayer) continue
    const d = Math.abs(b.x - p.x) + Math.abs(b.y - p.y)
    if (d <= 96) {
      cx += b.x
      cy += b.y
      n++
    }
  }
  if (n === 0) return { move: null, fire: false }
  cx /= n
  cy /= n
  const dx = p.x - cx
  const dy = p.y - cy
  const dir: Direction =
    Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up'
  return { move: dir, fire: false }
}

// ---------------------------------------------------------------- main

interface RunRes {
  key: string
  deathTick: number
  factual: BranchOut
  turnAndFire: BranchOut
  advance: BranchOut
  hardAway: BranchOut
}

const out: RunRes[] = []
let processed = 0
for (const f of failures) {
  if (processed >= limit) break
  const key = `S${f.stageIdx + 1}s${f.seed}`
  const deaths = f.forensics.events.filter((e) => e.type === 'death')
  const T = deaths[deaths.length - 1].tick
  const start = T - windowTicks
  if (start < 0) continue

  const world = new World()
  world.rng.reseed(f.seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard'] ?? DIFFICULTIES['classic']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? 3
  const godRng = new RNG((f.seed ^ 0x9e3779b9) >>> 0)
  const god = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, godRng)
  const sim = new Simulation(world, god)
  world.loadStageData(STAGES[f.stageIdx], 0)
  god.reset()
  let reached = false
  for (let t = 0; t < start; t++) {
    sim.tick()
    god.endFrame()
    if (world.state !== 'playing') break
    if (t === start - 1) reached = true
  }
  if (!reached || world.state !== 'playing') continue

  if (dumpKey === key) {
    console.log(`=== ${key} death@${T} ===`)
    for (let t = 0; t < windowTicks; t++) {
      sim.tick()
      god.endFrame()
      const p = world.player
      const b = world.bullets
        .filter((x: Bullet): x is Bullet => x.alive)
        .map((x) => `${x.isPlayer ? 'P' : 'E'}@${Math.round(x.x)},${Math.round(x.y)}${x.dir[0]}`)
        .join(' ')
      console.log(
        `t${start + t} branch=${god._lastBranch} dir=${p?.dir} pos=${p ? Math.round(p.x) + ',' + Math.round(p.y) : 'dead'} bullets[${b}]`,
      )
      if (world.state !== 'playing') break
    }
    continue
  }

  const threatId = () => nearestEnemyId(world)
  const baseHp0 = world.baseHp
  const snap = cloneWorld(world)
  const branches: Array<[string, InputLike]> = [
    ['factual', god],
    ['turnAndFire', new ScriptedInput(() => planTurnAndFire(world, threatId))],
    ['advance', new ScriptedInput(() => planAdvance2(world, threatId))],
    ['hardAway', new ScriptedInput(() => planHardAway(world))],
  ]
  const res: {
    factual: BranchOut
    turnAndFire: BranchOut
    advance: BranchOut
    hardAway: BranchOut
  } = {
    factual: {
      playerDied: false,
      diedAt: -1,
      baseDamaged: false,
      baseFirstDamageAt: -1,
      endState: '',
      movedTicks: 0,
      fires: 0,
    },
    turnAndFire: {
      playerDied: false,
      diedAt: -1,
      baseDamaged: false,
      baseFirstDamageAt: -1,
      endState: '',
      movedTicks: 0,
      fires: 0,
    },
    advance: {
      playerDied: false,
      diedAt: -1,
      baseDamaged: false,
      baseFirstDamageAt: -1,
      endState: '',
      movedTicks: 0,
      fires: 0,
    },
    hardAway: {
      playerDied: false,
      diedAt: -1,
      baseDamaged: false,
      baseFirstDamageAt: -1,
      endState: '',
      movedTicks: 0,
      fires: 0,
    },
  }
  for (const [name, inp] of branches) {
    if (name !== 'factual') {
      restoreWorld(world, snap)
      sim.input = inp
    }
    res[name as keyof typeof res] = runBranch(world, sim, sim.input, baseHp0)
    sim.input = god
  }
  out.push({ key, deathTick: T, ...res })
  processed++
  console.error(
    `[${processed}/${Math.min(limit, failures.length)}] ${key}@${T} f=${res.factual.playerDied} taf=${res.turnAndFire.playerDied} adv=${res.advance.playerDied} away=${res.hardAway.playerDied}`,
  )
}

// ---------------------------------------------------------------- report

const N = out.length
const factualDied = out.filter((r) => r.factual.playerDied).length
const tafSurv = out.filter((r) => !r.turnAndFire.playerDied && !r.turnAndFire.baseDamaged).length
const advSurv = out.filter((r) => !r.advance.playerDied && !r.advance.baseDamaged).length
const awaySurv = out.filter((r) => !r.hardAway.playerDied && !r.hardAway.baseDamaged).length
const anySurv = out.filter(
  (r) =>
    (!r.turnAndFire.playerDied && !r.turnAndFire.baseDamaged) ||
    (!r.advance.playerDied && !r.advance.baseDamaged) ||
    (!r.hardAway.playerDied && !r.hardAway.baseDamaged),
).length
console.log(
  `\n=== dodge-death counterfactual (${N} runs, window ${windowTicks}, clone @T-${windowTicks}) ===`,
)
console.log(`factual reproduced death: ${factualDied}/${N}`)
console.log(
  `survive (alive+base): turn-and-fire ${tafSurv}/${N} · advance ${advSurv}/${N} · hard-away ${awaySurv}/${N}`,
)
console.log(
  `any branch survives: ${anySurv}/${N}${N > 0 ? ' (' + ((anySurv / N) * 100).toFixed(1) + '%)' : ''}`,
)
const survivors = out.filter(
  (r) =>
    (!r.turnAndFire.playerDied && !r.turnAndFire.baseDamaged) ||
    (!r.advance.playerDied && !r.advance.baseDamaged) ||
    (!r.hardAway.playerDied && !r.hardAway.baseDamaged),
)
console.log('survivor runs:', survivors.map((r) => r.key).join(' '))
if (jsonOut) await Bun.write(jsonOut, JSON.stringify(out, null, 2))
