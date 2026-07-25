/**
 * sim-bench.ts — Reusable headless performance stress-test for Battle City Web.
 *
 * Runs the REAL Simulation + World (no canvas/DOM) so it is fast, deterministic,
 * and CI-friendly. It answers the questions that matter for the "60 FPS with the
 * fan off" goal:
 *
 *   1. How much CPU does one simulation tick cost at normal scale?
 *   2. Does cost stay flat (O(n)) or blow up (O(n^2)) as enemies/bullets grow?
 *   3. Where is the blow-up — tanks (tank/tank, AI perception) or bullets
 *      (bullet/tank, bullet/bullet)? We isolate by holding one axis fixed.
 *   4. Does the cost stay inside the frame budget (default 6.0 ms — leaves
 *      ~10.7 ms of the 16.67 ms frame for rendering + browser overhead)?
 *   5. Allocation invariant: consumeEvents() must still be allocation-free
 *      (<= 2 distinct buffer identities) under load.
 *
 * Timing uses BATCHED macro-measurement (K batches of N ticks, per-batch mean)
 * because sub-millisecond tick costs are below the resolution of performance.now()
 * when measured one tick at a time. Stats are computed over the per-batch means,
 * which removes quantization noise and yields a trustworthy slope.
 *
 * Output: tools/perf/results/sim-bench.json  +  a Markdown summary to stdout.
 * Exit code: 0 if every scenario is within budget, 1 if any breaches (CI gate).
 *
 * Run:  bun tools/perf/sim-bench.ts
 *       bun tools/perf/sim-bench.ts --budget 6 --ticks 3600
 */
import { World, genId } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { Input } from '../../src/game/Input'
import { BULLET, CELL, FIELD } from '../../src/constants'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const BUDGET_MS = Number(process.argv.find((a) => a.startsWith('--budget'))?.split('=')[1] ?? 6.0)
const TOTAL_TICKS = Number(process.argv.find((a) => a.startsWith('--ticks'))?.split('=')[1] ?? 3600)
const BATCHES = 60
const BATCH_TICKS = Math.max(10, Math.floor(TOTAL_TICKS / BATCHES))
const WARMUP = 240
const SEED = 0x5eed1234

const ENEMY_KINDS = ['basic', 'fast', 'power', 'armor'] as const

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function fmt(ms: number): string {
  if (ms < 1) return `${(ms * 1000).toFixed(1)}µs`
  return `${ms.toFixed(3)}ms`
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1))
  return sorted[idx]
}

function stats(arr: number[]) {
  const s = [...arr].sort((a, b) => a - b)
  const sum = s.reduce((a, b) => a + b, 0)
  return {
    min: s[0],
    median: percentile(s, 50),
    p95: percentile(s, 95),
    p99: percentile(s, 99),
    max: s[s.length - 1],
    mean: sum / s.length,
  }
}

/** Deterministic stress world: `enemies` enemy tanks + `bullets` in-flight bullets. */
function buildStressWorld(enemies: number, bullets: number): { world: World; sim: Simulation } {
  const world = new World()
  world.rng.reseed(SEED)
  world.startGame('classic', 'default', 0)
  world.state = 'playing'
  world.enemiesRemaining = Math.max(enemies, world.enemiesRemaining)

  const spots: Array<[number, number]> = []
  for (let r = 0; r < 26; r += 2) {
    for (let c = 0; c < 13; c += 2) {
      spots.push([c * CELL, r * CELL])
    }
  }
  for (let i = 0; i < enemies; i++) {
    const [x, y] = spots[i % spots.length]
    const t = world.createTank(ENEMY_KINDS[i % ENEMY_KINDS.length], x, y, 'down')
    t.alive = true
    world.tanks.push(t)
  }

  for (let i = 0; i < bullets; i++) {
    const dirs = ['up', 'down', 'left', 'right'] as const
    world.addBullet({
      id: genId(),
      x: (i * 37) % (FIELD - BULLET),
      y: (i * 53) % (FIELD - BULLET),
      w: BULLET,
      h: BULLET,
      dir: dirs[i % 4],
      alive: true,
      ownerId: -1 - i,
      ownerKind: 'basic',
      isPlayer: i % 2 === 0,
      speed: 6,
      power: 1,
    })
  }
  return { world, sim: new Simulation(world, new Input()) }
}

/** Keep ~`target` bullets alive by topping up any that died (steady-state load). */
function topUpBullets(world: World, target: number): void {
  let alive = 0
  for (const b of world.bullets) if (b.alive) alive++
  let i = 0
  while (alive < target) {
    const dirs = ['up', 'down', 'left', 'right'] as const
    world.addBullet({
      id: genId(),
      x: (i * 37) % (FIELD - BULLET),
      y: (i * 53) % (FIELD - BULLET),
      w: BULLET,
      h: BULLET,
      dir: dirs[i % 4],
      alive: true,
      ownerId: -1000 - i,
      ownerKind: 'basic',
      isPlayer: i % 2 === 0,
      speed: 6,
      power: 1,
    })
    alive++
    i++
  }
}

/**
 * Batched measurement: run `BATCHES` batches of `BATCH_TICKS` ticks, return the
 * per-batch mean tick time (ms). Top-up bullets at the start of each batch so the
 * in-flight count stays near `target` (sustained collision pressure).
 */
function measureScenario(world: World, sim: Simulation, bullets: number): number[] {
  const perBatch: number[] = []
  for (let b = 0; b < BATCHES; b++) {
    if (bullets > 0) topUpBullets(world, bullets)
    const t0 = performance.now()
    for (let i = 0; i < BATCH_TICKS; i++) {
      sim.tick()
      world.consumeEvents()
    }
    const t1 = performance.now()
    perBatch.push((t1 - t0) / BATCH_TICKS)
  }
  return perBatch
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------
interface Row {
  label: string
  enemies: number
  bullets: number
  stat: ReturnType<typeof stats>
  withinBudget: boolean
}

const rows: Row[] = []

function run(label: string, enemies: number, bullets: number): Row {
  const { world, sim } = buildStressWorld(enemies, bullets)
  // warm up JIT + fill steady state
  for (let i = 0; i < WARMUP; i++) {
    if (bullets > 0) topUpBullets(world, bullets)
    sim.tick()
    world.consumeEvents()
  }
  const perBatch = measureScenario(world, sim, bullets)
  const st = stats(perBatch)
  return { label, enemies, bullets, stat: st, withinBudget: st.p95 < BUDGET_MS }
}

rows.push(run('baseline (4 enemies / 6 bullets)', 4, 6))
for (const b of [10, 30, 60, 120, 240]) rows.push(run(`bullets=${b} (4 enemies)`, 4, b))
for (const e of [8, 16, 32, 64, 128]) rows.push(run(`enemies=${e} (6 bullets)`, e, 6))
for (const [e, b] of [
  [16, 60],
  [32, 120],
  [64, 240],
] as const)
  rows.push(run(`stress (${e} enemies / ${b} bullets)`, e, b))

// ---------------------------------------------------------------------------
// Allocation invariant (must hold under load)
// ---------------------------------------------------------------------------
{
  const { world, sim } = buildStressWorld(32, 120)
  for (let i = 0; i < WARMUP; i++) {
    topUpBullets(world, 120)
    sim.tick()
  }
  const ids = new Set<object>()
  for (let i = 0; i < 2000; i++) {
    sim.tick()
    ids.add(world.consumeEvents() as unknown as object)
  }
  const allocOk = ids.size <= 2
  console.log(
    `\n[alloc] distinct consumeEvents() buffers over 2000 frames (expect <=2): ${ids.size}  ${allocOk ? 'PASS' : 'FAIL'}`,
  )
  if (!allocOk) process.exitCode = 1
}

// ---------------------------------------------------------------------------
// Slope analysis — locate the O(n^2) blow-up
// ---------------------------------------------------------------------------
const rowFor = (pred: (r: Row) => boolean): Row | undefined => rows.find(pred)
const bulletSlope = (() => {
  const pts = [10, 30, 60, 120, 240]
    .map((b) => rowFor((r) => r.enemies === 4 && r.bullets === b))
    .filter(Boolean) as Row[]
  if (pts.length < 2) return null
  const a = pts[0]
  const z = pts[pts.length - 1]
  return (z.stat.p95 - a.stat.p95) / (z.bullets - a.bullets)
})()
const tankSlope = (() => {
  const pts = [8, 16, 32, 64, 128]
    .map((e) => rowFor((r) => r.enemies === e && r.bullets === 6))
    .filter(Boolean) as Row[]
  if (pts.length < 2) return null
  const a = pts[0]
  const z = pts[pts.length - 1]
  return (z.stat.p95 - a.stat.p95) / (z.enemies - a.enemies)
})()
const baseBulletRow = rowFor((r) => r.enemies === 4 && r.bullets === 10)
const sustainBullets =
  baseBulletRow && bulletSlope && bulletSlope > 0
    ? Math.floor((BUDGET_MS - baseBulletRow.stat.p95) / bulletSlope + 10)
    : null

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------
console.log(`\n=== Battle City Web — Simulation Tick Performance (budget p95 < ${BUDGET_MS}ms) ===`)
console.log(
  `${'scenario'.padEnd(34)}${'median'.padStart(10)}${'p95'.padStart(10)}${'p99'.padStart(10)}${'max'.padStart(10)}  budget`,
)
for (const r of rows) {
  const flag = r.withinBudget ? 'OK ' : 'OVER'
  console.log(
    `${r.label.padEnd(34)}${fmt(r.stat.median).padStart(10)}${fmt(r.stat.p95).padStart(10)}${fmt(r.stat.p99).padStart(10)}${fmt(r.stat.max).padStart(10)}  ${flag}`,
  )
}
console.log('\n--- slope analysis (added cost per extra entity, p95) ---')
console.log(
  `bullet cost @4 enemies : ${bulletSlope !== null ? (bulletSlope * 1000).toFixed(3) + ' µs/bullet' : 'n/a'}`,
)
console.log(
  `tank   cost @6 bullets : ${tankSlope !== null ? (tankSlope * 1000).toFixed(3) + ' µs/tank' : 'n/a'}`,
)
if (sustainBullets !== null)
  console.log(`est. max sustainable bullets @4 enemies (p95<${BUDGET_MS}ms): ~${sustainBullets}`)
else console.log('tick cost is essentially flat in n — no O(n^2) blow-up detected at tested scales')

const anyOver = rows.some((r) => !r.withinBudget)
console.log(`\n${anyOver ? 'SOME SCENARIOS OVER BUDGET' : 'ALL SCENARIOS WITHIN BUDGET'}`)

const out = {
  budgetMs: BUDGET_MS,
  ticks: TOTAL_TICKS,
  batches: BATCHES,
  seed: SEED,
  generatedAt: new Date().toISOString(),
  scenarios: rows.map((r) => ({
    label: r.label,
    enemies: r.enemies,
    bullets: r.bullets,
    withinBudget: r.withinBudget,
    stat: {
      median: +r.stat.median.toFixed(4),
      p95: +r.stat.p95.toFixed(4),
      p99: +r.stat.p99.toFixed(4),
      max: +r.stat.max.toFixed(4),
      mean: +r.stat.mean.toFixed(4),
    },
  })),
  slopes: { msPerBullet: bulletSlope, msPerTank: tankSlope, sustainBullets },
  allocationOk: process.exitCode !== 1,
  allWithinBudget: !anyOver,
}
const outPath = 'tools/perf/results/sim-bench.json'
mkdirSync(dirname(outPath), { recursive: true })
writeFileSync(outPath, JSON.stringify(out, null, 2))
console.log(`\nwrote ${outPath}`)

if (anyOver) process.exit(1)
