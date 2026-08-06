/**
 * verify-guard-replay.ts — 守卫 AI 回放逐字节一致性验证（DECISIONS §159/§160）
 *
 * Records headless sessions involving 天降神兵 guards, then proves each
 * .replay file reproduces its run tick-for-tick:
 *
 *   1. YIELD scenario — a pre-planted guard sits in the player's forward cell
 *      and yields (§159 避让) while the player advances; flank + lane enemies
 *      give the §160 sweep/lane suppression fire targets.
 *   2. SUMMON scenario — the F5 天降神兵 summon path itself: the recorded
 *      input frames carry the guard bit, `guardStock` is spent through
 *      `activateGuard`, which draws from **world.rng** for the guard-kind roll
 *      and spawns accompanying "balance" enemies — every one of those rng
 *      draws must reproduce identically on playback. The window is long
 *      enough for the summoned guards to wake (spawnTimer 1000) and be driven
 *      by their God-AI brains.
 *
 * Each scenario: records TICKS of input (InputRecorder) capturing the FULL
 * World snapshot (cloneWorld) after every tick, writes a .replay file (tmp/,
 * gitignored), replays it through the SAME wiring as PlaybackController /
 * verify-replay (restoreWorld + ReplayInput + fresh Simulation — guard brains
 * are RE-CREATED from the restored World, never carried over), and compares
 * per-tick canonical hashes. All per-tick hashes identical ⇒ byte-identical.
 * Also runs the real tools/replay/verify-replay.ts gate on each file.
 *
 * Usage:
 *   bun tools/replay/verify-guard-replay.ts [--mode all|yield|summon]
 *       [--ticks N] [--seed N] [--out DIR]
 *
 * Exit code 0 = all selected scenarios byte-identical, 1 = divergence found.
 */

import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { RNG } from '../../src/utils/RNG'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { InputRecorder } from '../../src/replay/InputRecorder'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { serializeReplayFile, parseReplayFile, buildReplayFilename } from '../../src/replay/file'
import { restoreWorld, cloneWorld } from '../../src/snapshot/WorldSerializer'
import type { InputLike } from '../../src/game/Input'
import { verifyReplayText } from './verify-replay'

// ================================================================
// Scenario construction
// ================================================================

/** Mutable per-run tick clock so a scripted input can be frame-accurate. */
interface Clock {
  tick: number
}

/** Deterministic scripted input: the player advances up the lane and holds fire. */
function makeAdvanceInput(clock: Clock, guardFrames: ReadonlySet<number> | null): InputLike {
  return {
    getMoveDirection: () => 'up',
    isFiring: () => true,
    wasItemPressed: (kind) =>
      kind === 'guard' && guardFrames !== null && guardFrames.has(clock.tick),
    endFrame: () => {},
    reset: () => {},
  }
}

/**
 * Stage-0 arena with the yield geometry already in place:
 *   player (192,224) facing up — the guard (192,192) occupies the player's
 *   forward cell, so every tick the player moves, §159 避让 fires.
 * Arena rows 6-18 / cols 4-20 are cleared (the base ring at rows 22-25 stays
 * untouched); the player is shield-invulnerable, the guard is tanky, the base
 * HP is huge — the window can never hit a terminal state early.
 */
function buildGuardYieldWorld(seed: number): World {
  const world = new World()
  world.seed = seed
  world.rng = new RNG(seed)
  world.startGame('classic', 'modern', 0)

  for (let r = 6; r <= 18; r++) {
    for (let c = 4; c <= 20; c++) world.tileMap.grid[r][c] = 'empty'
  }

  const p = world.player!
  p.x = 192
  p.y = 224
  p.dir = 'up'
  p.prevMoveDir = 'up'
  p.spawnTimer = 0
  p.shieldTimer = 999999 // invulnerable — no respawn, no terminal state

  // Guard directly in the player's forward cell (rows 12-13).
  const g = world.createTank('basic', 192, 192, 'up')
  g.allegiance = 'ally'
  g.isPlayer = false
  g.spawnTimer = 0
  g.hp = 1000
  g.lastFire = -99999
  g.guardExpireFrame = 999999

  // §160 targets: an enemy up the lane and one on the flank the guard will
  // sweep across while yielding.
  const lane = world.createTank('basic', 192, 48, 'down')
  lane.spawnTimer = 0
  lane.hp = 1
  const flank = world.createTank('basic', 80, 192, 'right')
  flank.spawnTimer = 0
  flank.hp = 1

  world.tanks = [lane, flank]
  world.allies = [g]
  world.baseHp = 9999
  world.baseMaxHp = 9999
  return world
}

/**
 * Stage-0 arena for the F5 天降神兵 SUMMON path: NO pre-planted guard. The
 * player holds the guard key at frames 30/90/150 (recorded into the input
 * stream), spending guardStock 3→0 through `activateGuard` — which rolls the
 * guard kind from world.rng and spawns accompanying balance enemies. The
 * guards wake at tick 1000+ (spawnTimer) and are then God-AI driven.
 */
const GUARD_SUMMON_FRAMES: ReadonlySet<number> = new Set([30, 90, 150])

function buildGuardSummonWorld(seed: number): World {
  const world = new World()
  world.seed = seed
  world.rng = new RNG(seed)
  world.startGame('classic', 'modern', 0)

  for (let r = 6; r <= 18; r++) {
    for (let c = 4; c <= 20; c++) world.tileMap.grid[r][c] = 'empty'
  }

  const p = world.player!
  p.x = 192
  p.y = 224
  p.dir = 'up'
  p.prevMoveDir = 'up'
  p.spawnTimer = 0
  p.shieldTimer = 999999

  world.tanks = []
  world.allies = []
  world.guardStock = 3 // the summon path spends this
  world.baseHp = 9999
  world.baseMaxHp = 9999
  return world
}

// ================================================================
// Hashing + shared verification pipeline
// ================================================================

/**
 * Canonical world hash — the byte-identity key.
 *
 * Two runs of the same scenario must produce IDENTICAL hashes iff their
 * gameplay state matches. The one wrinkle: entity `id`s come from `genId()`
 * — a process-global counter that is NOT reset between Worlds (documented in
 * World.ts). Entities created DURING playback therefore get different
 * absolute ids than the original run — but the id REFERENCES stay internally
 * consistent (bullet.ownerId / world.activeCommanderId always point at the
 * right tank within a world). Canonicalization remaps every id by first
 * occurrence in the snapshot tree, so the hash compares WORLD state (which
 * must be byte-identical) and not process-global counter state.
 */
function hashWorld(world: World): string {
  const snap = JSON.parse(JSON.stringify(cloneWorld(world))) as Record<string, unknown>
  const remap = new Map<number, number>()
  let next = 1
  const canon = (id: number): number => {
    if (id <= 0) return id
    let k = remap.get(id)
    if (k === undefined) {
      k = next++
      remap.set(id, k)
    }
    return k
  }
  const rewrite = (node: unknown): void => {
    if (node === null || typeof node !== 'object') return
    if (Array.isArray(node)) {
      for (let i = 0; i < node.length; i++) rewrite(node[i])
      return
    }
    const obj = node as Record<string, unknown>
    for (const key of Object.keys(obj)) {
      const v = obj[key]
      if (
        typeof v === 'number' &&
        (key === 'id' || key === 'ownerId' || key === 'activeCommanderId')
      ) {
        obj[key] = canon(v)
      } else if (v !== null && typeof v === 'object') {
        rewrite(v)
      }
    }
  }
  rewrite(snap)
  return JSON.stringify(snap)
}

interface ScenarioStats {
  lateralSteps: number
  maxAllies: number
  guardFire: number
  guardStock: number
}

interface Scenario {
  name: string
  ticks: number
  seed: number
  build: (seed: number) => World
  guardFrames: ReadonlySet<number> | null
  /** Scenario-specific PASS criteria (on top of byte-identity). */
  criteria: (s: ScenarioStats) => { ok: boolean; lines: string[] }
}

/** Record → write .replay → replay → compare. Returns PASS/FAIL. */
async function verifyScenario(scenario: Scenario, outDir: string): Promise<boolean> {
  const { name, ticks, seed } = scenario
  console.log(`[verify-guard-replay] ${name}: seed=${seed} ticks=${ticks}`)

  // ---------------- 1. Original run (recording pass) ----------------
  const world = scenario.build(seed)
  const clock: Clock = { tick: 0 }
  const input = makeAdvanceInput(clock, scenario.guardFrames)
  const sim = new Simulation(world, input)
  const recorder = new InputRecorder()
  recorder.startNew(world)

  const origHashes: string[] = []
  let lateralSteps = 0
  let maxAllies = world.allies.length
  let prevGuardX = world.allies[0]?.x ?? -1
  for (let t = 0; t < ticks; t++) {
    clock.tick = t
    sim.tick()
    recorder.recordFrame(input)
    input.endFrame()
    world.consumeEvents()
    origHashes.push(hashWorld(world))
    // Lateral (perpendicular) guard motion while the player advances = §159
    // yield steps (the player moves vertically, so yielding is horizontal).
    const g = world.allies[0]
    if (g && g.alive && g.x !== prevGuardX && world.player?.moving) lateralSteps++
    prevGuardX = g?.x ?? -1
    if (world.allies.length > maxAllies) maxAllies = world.allies.length
  }
  const rec = recorder.finalize()!
  const g0 = world.allies[0]
  const stats: ScenarioStats = {
    lateralSteps,
    maxAllies,
    guardFire: g0?.fireCount ?? 0,
    guardStock: world.guardStock,
  }

  // ---------------- 2. Write the .replay file ----------------
  const filename = buildReplayFilename({
    difficulty: 'classic',
    stageIndex: 0,
    status: 'timeout',
    lives: world.lives,
    totalTicks: rec.tickCount,
    seed,
  })
  const text = serializeReplayFile({
    source: 'sim',
    seed,
    sim: {
      seed,
      difficulty: 'classic',
      stageIndex: 0,
      stageName: STAGES[0].name,
      outcome: 'timeout',
      status: 'timeout',
      maxTicks: 36000,
    },
    initialSnapshot: rec.snapshot,
    frames: rec.frames,
    totalTicks: rec.tickCount,
    metadata: {
      stage: 0,
      stageName: STAGES[0].name,
      difficulty: 'classic',
      lives: world.lives,
      playerLevel: world.playerLevel,
      score: world.score,
      killCount: world.killCount,
      enemiesTotal: 20,
      playTimeMs: world.playTimeMs,
    },
  })
  const path = `${outDir}/${filename}`
  await Bun.write(path, text)
  console.log(`  wrote ${path}`)

  // ---------------- 3. Playback pass (exact verify-replay wiring) ----------------
  const parsed = parseReplayFile(text)
  if ('error' in parsed) throw new Error(`Parse failed: ${parsed.error}`)
  const replay = parsed.replay

  const world2 = new World()
  world2.rng.reseed(replay.seed)
  world2.difficultyKey = 'classic'
  world2.difficulty = DIFFICULTIES['classic']
  world2.rules = RULES['classic'] ?? DEFAULT_RULES
  world2.loadStageData(STAGES[replay.metadata.stage] ?? STAGES[0], 0)
  restoreWorld(world2, replay.initialSnapshot)
  const input2 = new ReplayInput(replay.frames)
  const sim2 = new Simulation(world2, input2)
  sim2.input = input2
  sim2.input2 = input2.input2 ?? null
  world2.state = 'playing'

  const playHashes: string[] = []
  for (let t = 0; t < ticks; t++) {
    sim2.tick()
    input2.advance()
    world2.consumeEvents()
    playHashes.push(hashWorld(world2))
  }

  // ---------------- 4. Byte-identical comparison ----------------
  let firstMismatch = -1
  for (let t = 0; t < ticks; t++) {
    if (origHashes[t] !== playHashes[t]) {
      firstMismatch = t
      break
    }
  }
  const guardPosMatch =
    (world.allies[0]?.x ?? -1) === (world2.allies[0]?.x ?? -1) &&
    (world.allies[0]?.y ?? -1) === (world2.allies[0]?.y ?? -1) &&
    (world.allies[0]?.dir ?? null) === (world2.allies[0]?.dir ?? null)

  // ---------------- 5. verify-replay.ts gate ----------------
  const vr = verifyReplayText(text, path)

  console.log('  per-tick world hashes:')
  if (firstMismatch === -1) {
    console.log(`    ${ticks}/${ticks} identical — BYTE-IDENTICAL across original run and replay`)
  } else {
    console.log(
      `    DIVERGED at tick ${firstMismatch} (${ticks - firstMismatch} ticks match before)`,
    )
  }
  console.log(
    `  guard final (x,y,dir): orig=(${g0?.x ?? '-'},${g0?.y ?? '-'},${g0?.dir ?? '-'}) replay=(${world2.allies[0]?.x ?? '-'},${world2.allies[0]?.y ?? '-'},${world2.allies[0]?.dir ?? '-'}) ${guardPosMatch ? 'MATCH' : 'MISMATCH'}`,
  )
  console.log(
    `  [verify-replay.ts] ${vr.verdict === 'OK' ? 'OK' : 'DESYNC'} — ${vr.reason} (final='${vr.finalState}' @tick ${vr.endedAtTick}/${vr.totalTicks})`,
  )

  const crit = scenario.criteria(stats)
  for (const line of crit.lines) console.log(`  ${line}`)
  const ok = firstMismatch === -1 && guardPosMatch && vr.verdict === 'OK' && crit.ok
  console.log(`[verify-guard-replay] ${name}: ${ok ? 'PASS' : 'FAIL'}`)
  return ok
}

// ================================================================
// Scenarios + main
// ================================================================

function yieldScenario(ticks: number, seed: number): Scenario {
  return {
    name: 'yield',
    ticks,
    seed,
    build: buildGuardYieldWorld,
    guardFrames: null,
    criteria: (s) => ({
      ok: s.lateralSteps > 0 && s.guardFire > 0,
      lines: [
        `yield lateral steps while player moving (§159): ${s.lateralSteps}`,
        `guard shots fired during the window (§160 suppression): ${s.guardFire}`,
      ],
    }),
  }
}

function summonScenario(ticks: number, seed: number): Scenario {
  return {
    name: 'F5 summon path',
    ticks,
    seed,
    build: buildGuardSummonWorld,
    guardFrames: GUARD_SUMMON_FRAMES,
    criteria: (s) => ({
      ok: s.maxAllies >= 3 && s.guardStock === 0,
      lines: [
        `summons fired (guard bit frames 30/90/150): max guards seen = ${s.maxAllies} (expect 3)`,
        `guardStock after spending: ${s.guardStock} (expect 0)`,
      ],
    }),
  }
}

function parseArgs(): {
  modes: string[]
  ticks: number | null
  seed: number
  outDir: string
} {
  const args = process.argv.slice(2)
  const get = (name: string): number | null => {
    const i = args.indexOf(name)
    return i >= 0 && args[i + 1] !== undefined ? Number(args[i + 1]) : null
  }
  const modeIdx = args.indexOf('--mode')
  const rawMode = modeIdx >= 0 && args[modeIdx + 1] !== undefined ? args[modeIdx + 1] : 'all'
  const outIdx = args.indexOf('--out')
  return {
    modes: rawMode === 'all' ? ['yield', 'summon'] : [rawMode],
    ticks: get('--ticks'),
    seed: get('--seed') ?? 12345,
    outDir: outIdx >= 0 && args[outIdx + 1] !== undefined ? args[outIdx + 1] : 'tmp',
  }
}

async function main(): Promise<void> {
  const { modes, ticks, seed, outDir } = parseArgs()
  const scenarios: Scenario[] = []
  // The summon scenario's guards wake at tick 1000 (spawnTimer) — give it a
  // longer default window so the God-AI-driven summoned guards are exercised.
  if (modes.includes('yield')) scenarios.push(yieldScenario(ticks ?? 360, seed))
  if (modes.includes('summon'))
    scenarios.push(summonScenario(ticks ?? 1200, Math.max(1, seed ^ 0xabc)))
  if (scenarios.length === 0) {
    console.error('usage: bun tools/replay/verify-guard-replay.ts [--mode all|yield|summon]')
    process.exit(2)
  }
  let allOk = true
  for (const s of scenarios) {
    allOk = (await verifyScenario(s, outDir)) && allOk
  }
  console.log(`[verify-guard-replay] overall: ${allOk ? 'PASS' : 'FAIL'}`)
  process.exit(allOk ? 0 : 1)
}

if (import.meta.main) {
  void main()
}
