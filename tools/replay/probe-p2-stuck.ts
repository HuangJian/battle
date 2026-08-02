/**
 * probe-p2-stuck.ts — at chosen ticks of a coop replay, dump the world facts
 * that the God AI's Navigator reads, for BOTH `world.player` (P1) and
 * `world.player2` (the tank the God AI actually controls).
 *
 * If the Navigator is reading the wrong tank, the two columns disagree.
 *
 * Usage: bun tools/replay/probe-p2-stuck.ts <file.replay> t1 t2 t3 ...
 */

import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { GodAIInput } from '../../src/ai/GodAIInput'
import { RNG } from '../../src/utils/RNG'
import type { GameState } from '../../src/types'
import { CELL, TANK, DIR_VECTORS, type Direction } from '../../src/constants'
import { snap } from '../../src/utils/helpers'
import type { Tank } from '../../src/types'

/** Read world.state without letting TS narrow it to the last assigned literal. */
const stateOf = (w: World): GameState => w.state

const args = process.argv.slice(2)
const file = args.find((a) => a.endsWith('.replay'))!
const probes = args.filter((a) => /^\d+$/.test(a)).map(Number)

const text = await Bun.file(file).text()
const parsed = parseReplayFile(text)
if ('error' in parsed) throw new Error(parsed.error)
const replay = parsed.replay

const world = new World()
world.rng.reseed(replay.seed)
const dkey = replay.metadata.difficulty || 'classic'
world.difficultyKey = dkey
world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
world.rules = RULES[dkey] ?? DEFAULT_RULES
world.loadStageData(STAGES[replay.metadata.stage] ?? STAGES[0], 0)
restoreWorld(world, replay.initialSnapshot)

const input = new ReplayInput(replay.frames)
const sim = new Simulation(world, input)
sim.input = input
sim.input2 = input.input2 ?? null
world.state = 'playing'

// A probe AI wired the same way Game.ts wires coop God AI.
const godP2 = new GodAIInput(world, undefined, new RNG(1), (w) => w.player2)

function rawCanMove(t: Tank, dir: Direction): boolean {
  const v = DIR_VECTORS[dir]
  const nx = snap(t.x, CELL) + v.dx * CELL
  const ny = snap(t.y, CELL) + v.dy * CELL
  if (!world.isInBounds(nx, ny, TANK, TANK)) return false
  if (world.rectHitsTerrain(nx, ny, TANK, TANK)) return false
  for (const o of world.allTanks) {
    if (o === t || !o.alive) continue
    if (nx < o.x + o.w && o.x < nx + TANK && ny < o.y + o.h && o.y < ny + TANK) return false
  }
  return true
}

const DIRS: Direction[] = ['up', 'down', 'left', 'right']
const fmtDirs = (t: Tank | null) =>
  t ? DIRS.map((d) => `${d[0]}=${rawCanMove(t, d) ? 'Y' : 'n'}`).join(' ') : 'n/a'

let tick = 0
const set = new Set(probes)
while (!input.isFinished && tick < replay.totalTicks + 10) {
  sim.tick()
  input.advance()
  world.consumeEvents?.()
  tick++
  if (set.has(tick)) {
    const p1 = world.player
    const p2 = world.player2
    console.log(`\n=== tick ${tick} (${(tick / 60).toFixed(1)}s) ===`)
    console.log(
      `  P1 (world.player)  pos=(${p1?.x},${p1?.y}) cell=(${Math.round((p1?.x ?? 0) / CELL)},${Math.round((p1?.y ?? 0) / CELL)}) lvl=${p1?.level}  ${fmtDirs(p1 ?? null)}`,
    )
    console.log(
      `  P2 (world.player2) pos=(${p2?.x},${p2?.y}) cell=(${Math.round((p2?.x ?? 0) / CELL)},${Math.round((p2?.y ?? 0) / CELL)}) lvl=${p2?.level}  ${fmtDirs(p2 ?? null)}`,
    )
    // What the Navigator's playerCell() says vs what its `p` variable is
    godP2.endFrame()
    const cell = godP2.playerCell()
    console.log(`  godP2.playerCell() = (${cell.col},${cell.row})  <- from controlledTank (P2)`)
    console.log(
      `  Navigator's \`const p = w.player\` => P1 at (${p1?.x},${p1?.y})  <== MISMATCH if != P2`,
    )
    for (const d of DIRS) {
      const viaP1 = godP2.canMoveDir(p1!, d)
      const viaP2 = godP2.canMoveDir(p2!, d)
      if (viaP1 !== viaP2) {
        console.log(`    canMoveDir('${d}'): P1=${viaP1} but P2=${viaP2}  <-- wrong answer used`)
      }
    }
    console.log(
      `  canMoveOrBreak(P1,left)=${godP2.canMoveOrBreak(world.player!, 'left')} canMoveOrBreak(P2,left)=${godP2.canMoveOrBreak(world.player2!, 'left')}`,
    )
  }
  const s = stateOf(world)
  if (s === 'stageclear' || s === 'gameover' || s === 'victory') break
}
