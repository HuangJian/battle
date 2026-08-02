/**
 * analyze-p2.ts — forensic trace of the God-AI player 2 across a coop replay.
 *
 * Replays a `.replay` file through the real Simulation (same wiring as
 * verify-replay.ts) and reports:
 *   - P2 death / respawn events
 *   - per-second P2 position + issued input frame
 *   - "stuck windows": spans where P2 barely moves
 *   - base-wall brick losses, with the bullet owner that caused each one
 *
 * Usage:
 *   bun tools/replay/analyze-p2.ts <file.replay> [--from=SEC] [--to=SEC]
 */

import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { unpackFrames } from '../../src/replay/pack'
import type { GameState } from '../../src/types'
import { CELL } from '../../src/constants'

/** Read world.state without letting TS narrow it to the last assigned literal. */
const stateOf = (w: World): GameState => w.state

const TPS = 60

/** Brick ring positions around the base (cols 11..14, rows 23..25). */
function wallCells(): Array<{ c: number; r: number }> {
  const out: Array<{ c: number; r: number }> = []
  for (let r = 23; r <= 25; r++) {
    for (let c = 11; c <= 14; c++) {
      if (r >= 24 && c >= 12 && c <= 13) continue // the base itself
      out.push({ c, r })
    }
  }
  return out
}

const mmss = (t: number) => {
  const s = Math.floor(t / TPS)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

const file = process.argv.slice(2).find((a) => a.endsWith('.replay'))
if (!file) {
  console.error('usage: bun tools/replay/analyze-p2.ts <file.replay>')
  process.exit(2)
}
const argFrom = process.argv.find((a) => a.startsWith('--from='))
const argTo = process.argv.find((a) => a.startsWith('--to='))
const fromTick = argFrom ? Number(argFrom.split('=')[1]) * TPS : -1
const toTick = argTo ? Number(argTo.split('=')[1]) * TPS : -1

const text = await Bun.file(file).text()
const parsed = parseReplayFile(text)
if ('error' in parsed) throw new Error(parsed.error)
const replay = parsed.replay
const meta = replay.metadata

const world = new World()
world.rng.reseed(replay.seed)
const dkey = meta.difficulty || 'classic'
world.difficultyKey = dkey
world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
world.rules = RULES[dkey] ?? DEFAULT_RULES
world.loadStageData(STAGES[meta.stage] ?? STAGES[0], 0)
restoreWorld(world, replay.initialSnapshot)

const input = new ReplayInput(replay.frames)
const sim = new Simulation(world, input)
sim.input = input
sim.input2 = input.input2 ?? null
world.state = 'playing'

const un = unpackFrames(replay.frames)
const p2frames = un?.p2 ?? []

console.log(`file=${file} stage=${meta.stage} seed=${replay.seed} totalTicks=${replay.totalTicks}`)
console.log(`p2 frames: ${p2frames.length}`)

const walls = wallCells()
const wallWas = new Map<string, string>()
for (const w of walls) wallWas.set(`${w.c},${w.r}`, world.tileMap.get(w.c, w.r))

let prevAlive = Boolean(world.player2?.alive)
let prevId = world.player2?.id ?? -1
let segStart = 0
let minX = Infinity,
  maxX = -Infinity,
  minY = Infinity,
  maxY = -Infinity
const segments: Array<{
  from: number
  to: number
  spanX: number
  spanY: number
  x: number
  y: number
}> = []

function closeSeg(t: number) {
  if (minX !== Infinity) {
    segments.push({
      from: segStart,
      to: t,
      spanX: maxX - minX,
      spanY: maxY - minY,
      x: minX,
      y: minY,
    })
  }
  minX = Infinity
  maxX = -Infinity
  minY = Infinity
  maxY = -Infinity
}

let tick = 0
while (!input.isFinished && tick < replay.totalTicks + 10) {
  // snapshot bullets before tick so we can attribute wall damage
  const bulletsBefore = world.bullets.map((b) => ({
    id: b.id,
    x: b.x,
    y: b.y,
    owner: `${b.allegiance}/${b.ownerKind}`,
    ownerId: b.ownerId,
  }))

  sim.tick()
  input.advance()
  world.consumeEvents?.()
  tick++

  const p2 = world.player2
  const alive = Boolean(p2?.alive)
  const id = p2?.id ?? -1
  if (alive !== prevAlive || id !== prevId) {
    closeSeg(tick)
    console.log(
      `[${mmss(tick)} t${tick}] P2 ${alive ? 'SPAWN' : 'DEATH'} id=${id} lives=${world.lives2 ?? '?'} pos=${p2 ? `${p2.x},${p2.y}` : '-'}`,
    )
    segStart = tick
    prevAlive = alive
    prevId = id
  }
  if (p2 && alive) {
    minX = Math.min(minX, p2.x)
    maxX = Math.max(maxX, p2.x)
    minY = Math.min(minY, p2.y)
    maxY = Math.max(maxY, p2.y)
  }

  // wall damage attribution
  for (const w of walls) {
    const key = `${w.c},${w.r}`
    const now = world.tileMap.get(w.c, w.r)
    if (now !== wallWas.get(key)) {
      const near = bulletsBefore
        .filter((b) => Math.abs(b.x - w.c * CELL) < 40 && Math.abs(b.y - w.r * CELL) < 40)
        .map((b) => {
          const p1 = world.player
          const p2t = world.player2
          const who =
            b.ownerId === p1?.id ? 'P1' : b.ownerId === p2t?.id ? 'P2(GodAI)' : `#${b.ownerId}`
          return `${who}:${b.owner}@(${b.x},${b.y})`
        })
      console.log(
        `[${mmss(tick)} t${tick}] WALL (${w.c},${w.r}) ${wallWas.get(key)} -> ${now}  culprits=[${near.join(',')}]`,
      )
      wallWas.set(key, now)
    }
  }

  if (fromTick >= 0 && tick >= fromTick && tick <= toTick && tick % 30 === 0) {
    const f = p2frames[tick - 1]
    console.log(
      `  [${mmss(tick)} t${tick}] p2 pos=(${p2?.x},${p2?.y}) dir=${p2?.dir} moving=${p2?.moving} alive=${alive} frame=${f ? `${f.direction ?? '-'}${f.firing ? '/F' : ''}` : 'n/a'}`,
    )
  }

  const s = stateOf(world)
  if (s === 'stageclear' || s === 'gameover' || s === 'victory') break
}
closeSeg(tick)

console.log(
  `\nfinal=${world.state} @${tick} kills=${world.killCount} baseAlive=${!world.tileMap.isBaseDestroyed()}`,
)
console.log('\n--- P2 life segments (span in px, CELL=16) ---')
for (const s of segments) {
  const stuck = s.spanX < CELL && s.spanY < CELL
  console.log(
    `${mmss(s.from)}-${mmss(s.to)} (t${s.from}-${s.to}, ${((s.to - s.from) / TPS).toFixed(1)}s) span=(${s.spanX},${s.spanY}) origin=(${s.x},${s.y})${stuck ? '   <== STUCK' : ''}`,
  )
}

// raw frame histogram over the reported window
const win = { from: 56 * TPS, to: 101 * TPS }
let none = 0
let fire = 0
for (let i = win.from; i < Math.min(win.to, p2frames.length); i++) {
  if (p2frames[i].direction === null) none++
  if (p2frames[i].firing) fire++
}
console.log(
  `\nP2 recorded frames 00:56-01:41: ${win.to - win.from} ticks, direction=null on ${none}, firing on ${fire}`,
)
