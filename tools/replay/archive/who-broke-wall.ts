/**
 * who-broke-wall.ts — attribute base-wall brick losses in a replay to the
 * exact bullet (and owner tank) that caused them.
 *
 * Tracks every bullet from birth, so when a wall cell disappears we can name
 * the bullet that was heading for it rather than guessing by proximity.
 *
 * Usage: bun tools/replay/who-broke-wall.ts <file.replay>
 */

import { World } from '../../../src/game/World'
import { Simulation } from '../../../src/game/Simulation'
import { DIFFICULTIES } from '../../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../../src/config/rules'
import { STAGES } from '../../../src/config/stages'
import { ReplayInput } from '../../../src/replay/ReplayInput'
import { parseReplayFile } from '../../../src/replay/file'
import { restoreWorld } from '../../../src/snapshot/WorldSerializer'
import { CELL } from '../../../src/constants'
import type { GameState } from '../../../src/types'

const stateOf = (w: World): GameState => w.state

const file = process.argv.slice(2).find((a) => a.endsWith('.replay'))!
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

const walls: Array<{ c: number; r: number }> = []
for (let r = 23; r <= 25; r++) {
  for (let c = 11; c <= 14; c++) {
    if (r >= 24 && c >= 12 && c <= 13) continue
    walls.push({ c, r })
  }
}
const was = new Map<string, string>()
for (const w of walls) was.set(`${w.c},${w.r}`, world.tileMap.get(w.c, w.r))

/** id -> description of the bullet's origin, captured at birth. */
const born = new Map<number, string>()
const mmss = (t: number) =>
  `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor(t / 60) % 60).padStart(2, '0')}`

let tick = 0
while (!input.isFinished && tick < replay.totalTicks + 10) {
  // Register new bullets with their owner BEFORE they move.
  for (const b of world.bullets) {
    if (!b.alive || born.has(b.id)) continue
    const who =
      b.ownerId === world.player?.id
        ? 'P1(human)'
        : b.ownerId === world.player2?.id
          ? 'P2(GodAI)'
          : `enemy#${b.ownerId}`
    born.set(b.id, `${who} dir=${b.dir} from(${Math.round(b.x)},${Math.round(b.y)})`)
  }
  // Snapshot live bullets before the tick resolves collisions.
  const live = world.bullets.filter((b) => b.alive).map((b) => ({ ...b }))

  sim.tick()
  input.advance()
  world.consumeEvents?.()
  tick++

  for (const w of walls) {
    const key = `${w.c},${w.r}`
    const now = world.tileMap.get(w.c, w.r)
    if (now === was.get(key)) continue
    // The bullet that vanished this tick AND was travelling toward this cell.
    const stillAlive = new Set(world.bullets.filter((b) => b.alive).map((b) => b.id))
    const gone = live.filter((b) => !stillAlive.has(b.id))
    const suspects = gone
      .filter(
        (b) =>
          Math.abs(b.x + b.w / 2 - (w.c * CELL + CELL / 2)) < CELL * 2 &&
          Math.abs(b.y + b.h / 2 - (w.r * CELL + CELL / 2)) < CELL * 2,
      )
      .map((b) => born.get(b.id) ?? `#${b.id}`)
    console.log(
      `[${mmss(tick)} t${tick}] (${w.c},${w.r}) ${was.get(key)} -> ${now}\n     by: ${suspects.length ? suspects.join(' | ') : `(no bullet resolved nearby; ${gone.length} bullets died this tick)`}`,
    )
    was.set(key, now)
  }

  const s = stateOf(world)
  if (s === 'stageclear' || s === 'gameover' || s === 'victory') break
}
console.log(`\nfinal=${stateOf(world)} @${tick}`)
