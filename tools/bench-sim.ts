/**
 * Headless performance benchmark & verification for Battle City Web.
 *
 * Runs the real Simulation + World (no canvas/DOM needed) to:
 *   1. Measure simulation tick cost over a long run (baseline for "is the
 *      bottleneck in the sim or the renderer?").
 *   2. Prove `consumeEvents()` no longer allocates a new array every frame
 *      (double-buffer → at most 2 distinct buffer identities over many calls).
 *   3. Prove brick destruction now triggers an INCREMENTAL terrain redraw
 *      (dirtyCells populated, full `dirty` flag stays false) instead of a
 *      full 26×26 cache rebuild.
 *
 * Run with:  bun tools/bench-sim.ts
 */
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { TileMap } from '../src/game/TileMap'
import { GRID } from '../src/constants'

function fmt(ms: number): string {
  return ms < 1 ? `${(ms * 1000).toFixed(1)}µs` : `${ms.toFixed(3)}ms`
}

// ---------------------------------------------------------------------------
// 1. Simulation tick cost
// ---------------------------------------------------------------------------
const world = new World()
world.startGame('classic', 'default', 0)
const input = new Input()
const sim = new Simulation(world, input)

// Warm up JIT
for (let i = 0; i < 120; i++) sim.tick()

const N = 60 * 60 * 3 // ~3 minutes of gameplay ticks
const t0 = performance.now()
for (let i = 0; i < N; i++) {
  sim.tick()
  world.consumeEvents() // mimic the game loop consuming events every frame
}
const t1 = performance.now()
const total = t1 - t0
console.log(`[sim] ${N} ticks in ${total.toFixed(1)}ms  →  ${fmt(total / N)}/tick`)

// ---------------------------------------------------------------------------
// 2. consumeEvents must NOT allocate per frame (double-buffer invariant)
// ---------------------------------------------------------------------------
const identities = new Set<object>()
for (let i = 0; i < 2000; i++) {
  const e = world.consumeEvents() as unknown as object
  identities.add(e)
}
const distinct = identities.size
console.log(
  `[alloc] distinct consumeEvents() buffers over 2000 frames (expect <=2): ${distinct}  ${
    distinct <= 2 ? 'PASS' : 'FAIL'
  }`,
)

// ---------------------------------------------------------------------------
// 3. Brick destruction → incremental (dirtyCells) not full rebuild
// ---------------------------------------------------------------------------
const tm = new TileMap()
// Build a tiny grid with a brick at (3,3)
for (let r = 0; r < GRID; r++) for (let c = 0; c < GRID; c++) tm.grid[r][c] = 'empty'
tm.grid[3][3] = 'brick'
tm.dirty = false
tm.dirtyCells.length = 0

tm.destroy(3, 3)
const incrementalOk =
  tm.dirty === false && tm.dirtyCells.length === 1 && tm.dirtyCells[0] === 3 * GRID + 3
console.log(
  `[terrain] brick destroy → dirty=${tm.dirty} dirtyCells=${tm.dirtyCells.length} ` +
    `(expect dirty=false, cells=1): ${incrementalOk ? 'PASS' : 'FAIL'}`,
)

// Destroying the base must still force a full rebuild (ruins rendering)
tm.grid[24][12] = 'base'
tm.dirty = false
tm.dirtyCells.length = 0
tm.destroyAllBaseCells()
const baseOk = (tm.dirty as boolean) === true
console.log(`[terrain] base destroy → dirty=${tm.dirty} (expect true): ${baseOk ? 'PASS' : 'FAIL'}`)

const allPass = distinct <= 2 && incrementalOk && baseOk
console.log(allPass ? '\nALL CHECKS PASS' : '\nSOME CHECKS FAILED')
process.exit(allPass ? 0 : 1)
