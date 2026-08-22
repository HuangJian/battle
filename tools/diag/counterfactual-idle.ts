#!/usr/bin/env bun
/**
 * counterfactual-idle.ts — M3 causal counterfactual open test
 * (plan/God-AI-Hard-Open-Test-Protocol.md §6).
 *
 * Question: was a suspected idle / no-output stall actually the CAUSE of the
 * base loss, or was it legitimate (holding was fine) / unfixable locally
 * (the real mistake was earlier travel/turn)? Answer by REPLAYING the failed
 * run deterministically to the stall tick and branching the world there:
 *
 *   continue          — original God AI input (the factual continuation)
 *   turn-and-fire     — stand, legally turn toward the current effective
 *                       threat (min enemyDamageDeadline among csb/cbr), fire
 *   move-to-intercept — close distance toward the threat cell, firing only
 *                       when already ray-aligned
 *   clear-or-advance  — if a brick blocks the dominant axis to the threat,
 *                       stop and blast it; otherwise advance like intercept
 *
 * Each branch runs a fixed short window (default 240 ticks with 60/120/240
 * checkpoints) from a cloneWorld snapshot; RNG, bullets, terrain, cooldowns
 * and the enemy queue all travel in the snapshot (WorldSerializer). The
 * branch inputs are scripted InputLike adapters — the God AI never runs in
 * them. Counterfactuals live HERE ONLY (protocol §6.2): no src/ behavior.
 *
 * Idle-event detection (protocol §6.1): a no-op tick = God AI committed a
 * defense/hunt branch (input._lastBranch) with moveDir=null && fire=false
 * while the player is alive and spawned. A stationary SEGMENT = consecutive
 * no-op ticks (≥ NO_OUTPUT_MIN_TICKS); only the segment's FIRST tick is an
 * event. Events must lie within --pre-window ticks before the first base HP
 * drop. By default only the LAST qualifying segment of a run (closest to the
 * damage) is counterfactual (--events-per-run N for more; each event gets
 * its own deterministic 0..T replay so the God AI state at T is factual).
 *
 * Classification (protocol §6.3):
 *   idle_causal          — continue loses base HP in the window AND ≥1
 *                          alternative keeps it
 *   idle_legitimate      — continue keeps base HP too (the stall cost
 *                          nothing locally), or all branches identical
 *   travel_or_turn_causal— continue loses base HP and NO alternative saves
 *                          it (the local window cannot fix it; root cause is
 *                          earlier target/route/turn decisions)
 *   unresolved           — snapshot/restore failure, degenerate scripts (no
 *                          alt branch moved or fired), or missing threat
 *
 * Usage:
 *   bun tools/diag/counterfactual-idle.ts --from-json tmp/open-test-forensics-baseline.json \
 *       [--kinds base_destroyed] [--limit 40] [--events-per-run 1] \
 *       [--pre-window 600] [--window 240] [--json tmp/cf-idle.json] \
 *       [--dump S34s12]        # per-tick trace for manual spot-checks
 *
 * Input corpus: a run-forensics --json sweep at the same caliber (default
 * params). The re-plays assert the corpus outcome — any divergence aborts
 * (determinism is the license for replay-based branching).
 */
import { STAGES } from '../../src/config/stages'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import type { InputLike } from '../../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { RNG } from '../../src/utils/RNG'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import {
  enemyDeadline,
  killAssessment,
  tankCenterCell,
  aimDirTo,
} from '../../src/ai/god/ThreatBudget'
import { type Direction } from '../../src/constants'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { isDefenseBranch } from './failure-classifier'

// ---------------------------------------------------------------- CLI args

function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : def
}

const fromJson = arg('from-json')
const kinds = new Set((arg('kinds') ?? 'base_destroyed').split(',').map((s) => s.trim()))
const limit = Number(arg('limit') ?? '40')
const eventsPerRun = Number(arg('events-per-run') ?? '1')
const preWindow = Number(arg('pre-window') ?? '600')
const windowTicks = Number(arg('window') ?? '240')
const jsonOut = arg('json')
const dumpKey = arg('dump')

/** Checkpoint offsets inside the branch window (protocol suggests 60/120/240). */
const CHECKPOINTS = [60, 120, 240].filter((c) => c <= windowTicks)
if (CHECKPOINTS.length === 0) CHECKPOINTS.push(windowTicks)

/** Minimum consecutive no-op ticks for a stationary segment (mirrors
 * NO_OUTPUT_MIN_SAMPLES in failure-classifier.ts, at tick resolution). */
const NO_OUTPUT_MIN_TICKS = 3

/** Hunt-family branches that also count as "committed" for idle detection
 * (protocol §6.1: 防守或 hunt 分支提交) — defense set via isDefenseBranch. */
const HUNT_BRANCHES = new Set(['aggressive', 'engage', 'hunt'])

// ------------------------------------------------------------- report types

type IdleClass = 'idle_causal' | 'idle_legitimate' | 'travel_or_turn_causal' | 'unresolved'

export interface BranchRecord {
  /** Metrics at each checkpoint offset: base HP, threat dead, cumulative
   * fires, net displacement (px — immune to the 1px snap wobble), player
   * dead. */
  checkpoints: Array<{
    at: number
    baseHp: number
    targetDead: boolean
    fires: number
    displacement: number
    playerDead: boolean
  }>
  firstBaseDamageOffset: number // -1 = none in window
  firstFireOffset: number
  /** First offset with ≥8px net displacement (a real step; 1px snap
   * corrections around grid alignment do not count). */
  firstMoveOffset: number
  targetDeathOffset: number
  playerDeathOffset: number
  endState: string // world.state at window end / early stop
  rngStateEnd: number
}

interface EventRecord {
  key: string
  stageIdx: number
  seed: number
  failureCause: string
  corpusTicks: number
  firstBaseDamageTick: number
  eventTick: number
  segmentLen: number
  branch: string
  onCooldown: boolean
  threatCount: number
  threatId: number
  threatFallback: boolean // no csb/cbr enemy — nearest used instead
  threatDeadline: number
  threatKillEta: number
  threatSlack: number
  classification: IdleClass
  /** True when every damage-avoiding alternative ALSO produced no fire and
   * no ≥8px displacement in the window — the avoid came from standing
   * (e.g. body-blocking the lane), not from an active intervention. */
  avoidedByInaction: boolean
  branches: {
    cont: BranchRecord
    turnFire: BranchRecord
    intercept: BranchRecord
    clearAdvance: BranchRecord
  }
}

// Deterministic init mirroring runSimulation() (tools/sim/simulation-runner.ts)
// exactly — same difficulty wiring, same godRng seed derivation, same reset
// order. `stageIndex` must match the CORPUS caliber (run-forensics uses 0):
// it feeds loadStageData → score-milestone drops consume RNG, so a mismatch
// silently diverges the whole run. Determinism (MANIFEST §2.3) is what
// licenses replay-to-T branching.
const replayStageIndex = Number(arg('stage-index') ?? '0')

function setupRun(
  stageIdx: number,
  seed: number,
  difficulty: string,
): { world: World; sim: Simulation; input: GodAIInput } {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? 3
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], replayStageIndex)
  input.reset()
  return { world, sim, input }
}

/** One factual tick: sim → probe (this tick's decision state) → endFrame →
 * consume. Returns the probe or null when the world left 'playing'. */
interface TickProbe {
  branch: string
  moveDir: Direction | null
  fire: boolean
  onCooldown: boolean
  baseHp: number
  px: number
  py: number
  playerAlive: boolean
}

function probeTick(world: World, input: GodAIInput): TickProbe | null {
  if (world.state !== 'playing') return null
  const p = world.player
  const onCooldown = !!p && world.frame * (1000 / 60) - p.lastFire < p.nextFireInterval
  return {
    branch: input._lastBranch,
    moveDir: input._moveDir,
    fire: input._fire,
    onCooldown,
    baseHp: world.baseHp,
    px: p ? p.x : -1,
    py: p ? p.y : -1,
    playerAlive: !!p && p.alive && p.spawnTimer <= 0,
  }
}

/** Mirror of computeLedgerSample's fire-cooldown gate — read-only. */
function isNoOpTick(pr: TickProbe): boolean {
  return pr.moveDir === null && !pr.fire
}
function isCommittedBranch(branch: string): boolean {
  return isDefenseBranch(branch) || HUNT_BRANCHES.has(branch)
}

interface Segment {
  start: number
  len: number
  branch: string
}

/** Pass 1: replay the whole run, collecting stationary segments (first tick
 * only) and the first base-damage tick. Asserts the corpus outcome. */
interface DetectResult {
  segments: Segment[]
  firstBaseDamageTick: number
  outcomeOk: boolean
  trace: TickProbe[] // full per-tick probe trace (dump only)
}

function detectSegments(
  stageIdx: number,
  seed: number,
  difficulty: string,
  maxTicks: number,
  corpus: { ticks: number; outcome: string },
): DetectResult {
  const { world, sim, input } = setupRun(stageIdx, seed, difficulty)
  const segments: Segment[] = []
  const trace: TickProbe[] = []
  let segStart = -1
  let segBranch = ''
  let firstBaseDamageTick = -1
  let prevBaseHp = world.baseHp
  let tick = 0
  let outcome = 'max_ticks'
  while (tick < maxTicks) {
    sim.tick()
    const pr = probeTick(world, input)
    input.endFrame()
    world.consumeEvents()
    tick++
    if (world.baseHp < prevBaseHp && firstBaseDamageTick < 0) firstBaseDamageTick = tick - 1
    prevBaseHp = world.baseHp
    if (pr) trace.push(pr)
    // Segment bookkeeping: only alive+spawned ticks count; anything else
    // (death, respawn, terminal) closes the segment.
    if (pr && pr.playerAlive && isNoOpTick(pr) && isCommittedBranch(pr.branch)) {
      if (segStart < 0) {
        segStart = tick - 1
        segBranch = pr.branch
      }
    } else {
      if (segStart >= 0 && tick - 1 - segStart >= NO_OUTPUT_MIN_TICKS) {
        segments.push({ start: segStart, len: tick - 1 - segStart, branch: segBranch })
      }
      segStart = -1
    }
    if (world.state === 'stageclear' || world.state === 'victory') {
      outcome = 'stage_clear'
      break
    }
    if (world.state === 'gameover') {
      outcome = 'gameover'
      break
    }
  }
  if (segStart >= 0 && tick - segStart >= NO_OUTPUT_MIN_TICKS) {
    segments.push({ start: segStart, len: tick - segStart, branch: segBranch })
  }
  const outcomeOk =
    outcome === corpus.outcome ||
    (corpus.outcome === 'gameover' && outcome === 'gameover') ||
    (outcome === 'max_ticks' && corpus.outcome === 'max_ticks')
  return { segments, firstBaseDamageTick, outcomeOk, trace }
}

/** The current effective threat: min enemyDamageDeadline among direct
 * (csb/cbr) threats; nearest-by-Manhattan fallback when none exists. */
interface ThreatPick {
  id: number
  deadline: number
  killEta: number
  slack: number
  fallback: boolean
  count: number
}

function pickThreat(world: World): ThreatPick | null {
  const p = world.player
  if (!p) return null
  const pc = tankCenterCell(p)
  let best: import('../../src/types').Tank | null = null
  let bestDl = Infinity
  let fallback: import('../../src/types').Tank | null = null
  let fallbackDist = Infinity
  let count = 0
  for (const t of world.tanks) {
    if (t.isPlayer || !t.alive || t.spawnTimer > 0) continue
    const dl = enemyDeadline(world, t)
    if (dl.directThreat) {
      count++
      if (dl.enemyDamageDeadline < bestDl) {
        bestDl = dl.enemyDamageDeadline
        best = t
      }
    }
    const tc = tankCenterCell(t)
    const d = Math.abs(tc.col - pc.col) + Math.abs(tc.row - pc.row)
    if (d < fallbackDist) {
      fallbackDist = d
      fallback = t
    }
  }
  const chosen = best ?? fallback
  if (!chosen) return null
  const dl = enemyDeadline(world, chosen)
  const ka = killAssessment(world, p, chosen)
  return {
    id: chosen.id,
    deadline: dl.enemyDamageDeadline,
    killEta: ka.playerKillEta,
    slack: ka.killSlack,
    fallback: best === null,
    count,
  }
}

/** Scripted branch input — deterministic function of the current world
 * state. Implements InputLike so `sim.input` can be swapped (diag-only). */
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

function threatById(world: World, id: number): import('../../src/types').Tank | null {
  for (const t of world.tanks) if (t.id === id) return t
  return null
}

/** First blocking terrain 1..maxCells ahead of (pc) along dir:
 * 'brick' | 'steel' | 'water' | 'none'. Tanks are ignored — a tank in the
 * way is transient, terrain is not. */
function aheadTerrain(
  world: World,
  pc: { col: number; row: number },
  dir: Direction,
  maxCells: number,
): 'brick' | 'steel' | 'water' | 'none' {
  const stepX = dir === 'left' ? -1 : dir === 'right' ? 1 : 0
  const stepY = dir === 'up' ? -1 : dir === 'down' ? 1 : 0
  for (let k = 1; k <= maxCells; k++) {
    const c = pc.col + stepX * k
    const r = pc.row + stepY * k
    if (c < 0 || c > 25 || r < 0 || r > 25) return 'none'
    const t = world.tileMap.get(c, r)
    if (t === 'brick') return 'brick'
    if (t === 'steel') return 'steel'
    if (t === 'water') return 'water'
  }
  return 'none'
}

/** Face `dir` (issue it as movement while not facing it), then hold and
 * fire. Used both for aiming at threats and at bricks to clear. */
function faceAndFire(
  p: import('../../src/types').Tank,
  dir: Direction,
): { move: Direction | null; fire: boolean } {
  if (p.dir !== dir) return { move: dir, fire: false }
  return { move: null, fire: true }
}

/** Shared steering core: close the distance to the threat cell.
 * - adjacent → hold & fire
 * - brick in the way (≤ blastDist cells) → face it and blast
 * - steel/water in the way → slide along the secondary axis
 * - otherwise advance along the dominant axis; fire when already
 *   ray-aligned, facing the threat, and within 8 cells. */
function planAdvance(
  world: World,
  p: import('../../src/types').Tank,
  t: import('../../src/types').Tank,
  blastDist: number,
): { move: Direction | null; fire: boolean } {
  const pc = tankCenterCell(p)
  const tc = tankCenterCell(t)
  const dx = tc.col - pc.col
  const dy = tc.row - pc.row
  const dist = Math.abs(dx) + Math.abs(dy)
  if (dist <= 1) return faceAndFire(p, aimDirTo(tc.col, tc.row, pc.col, pc.row) ?? p.dir)
  const primary: Direction =
    Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up'
  const ahead = aheadTerrain(world, pc, primary, blastDist)
  if (ahead === 'brick') return faceAndFire(p, primary)
  if (ahead === 'steel' || ahead === 'water') {
    // Slide along the secondary axis if it still closes the distance.
    const secondary: Direction | null =
      Math.abs(dx) >= Math.abs(dy)
        ? dy !== 0
          ? dy > 0
            ? 'down'
            : 'up'
          : null
        : dx !== 0
          ? dx > 0
            ? 'right'
            : 'left'
          : null
    if (secondary) {
      const secAhead = aheadTerrain(world, pc, secondary, 1)
      if (secAhead !== 'steel' && secAhead !== 'water') return { move: secondary, fire: false }
    }
    return { move: primary, fire: false } // bump — nothing better to do geometrically
  }
  const aim = aimDirTo(tc.col, tc.row, pc.col, pc.row) ?? p.dir
  const aligned = tc.col === pc.col || tc.row === pc.row
  const fire = aligned && dist <= 8 && p.dir === aim
  return { move: primary, fire }
}

/** Turn-and-fire (hold semantics): a tank turns by issuing a movement
 * direction (Input semantics), so while NOT yet facing the threat issue the
 * facing direction (the fairness rule enforces turn legality and cooldown);
 * once facing the threat, hold position and fire. */
function planTurnAndFireHold(
  world: World,
  threatId: number,
): { move: Direction | null; fire: boolean } {
  const p = world.player
  const t = threatById(world, threatId)
  if (!p || !t || !t.alive) return { move: null, fire: false }
  const pc = tankCenterCell(p)
  const tc = tankCenterCell(t)
  const want = aimDirTo(tc.col, tc.row, pc.col, pc.row) ?? p.dir
  return faceAndFire(p, want)
}

/** Move-to-intercept: steer toward the threat, blasting only bricks that
 * block the immediate path (≤2 cells). */
function planIntercept(world: World, threatId: number): { move: Direction | null; fire: boolean } {
  const p = world.player
  const t = threatById(world, threatId)
  if (!p || !t || !t.alive) return { move: null, fire: false }
  return planAdvance(world, p, t, 2)
}

/** Clear-or-advance: proactively clear the line — blast any brick up to 6
 * cells ahead on the dominant axis before advancing. */
function planClearOrAdvance(
  world: World,
  threatId: number,
): { move: Direction | null; fire: boolean } {
  const p = world.player
  const t = threatById(world, threatId)
  if (!p || !t || !t.alive) return { move: null, fire: false }
  return planAdvance(world, p, t, 6)
}

/** Run one branch for `window` ticks from the CURRENT world state and
 * collect checkpoint metrics. Returns null on catastrophic failure. */
function runBranch(
  world: World,
  sim: Simulation,
  input: InputLike,
  threatId: number,
  baseHpAtBranch: number,
): BranchRecord | null {
  const checkpoints: BranchRecord['checkpoints'] = []
  let fires = 0
  let firstBaseDamageOffset = -1
  let firstFireOffset = -1
  let firstMoveOffset = -1
  let targetDeathOffset = -1
  let playerDeathOffset = -1
  const livesAtStart = world.lives
  const sx = world.player ? world.player.x : 0
  const sy = world.player ? world.player.y : 0
  for (let off = 1; off <= windowTicks; off++) {
    sim.tick()
    input.endFrame()
    const events = world.consumeEvents()
    for (const e of events) {
      if (
        e.type === 'bullet_fired' &&
        (e as { bullet?: { isPlayer?: boolean } }).bullet?.isPlayer
      ) {
        fires++
        if (firstFireOffset < 0) firstFireOffset = off
      }
    }
    const p = world.player
    if (p) {
      const disp = Math.abs(p.x - sx) + Math.abs(p.y - sy)
      if (firstMoveOffset < 0 && disp >= 8) firstMoveOffset = off
      if (CHECKPOINTS.includes(off)) {
        checkpoints.push({
          at: off,
          baseHp: world.baseHp,
          targetDead: targetDeathOffset >= 0,
          fires,
          displacement: disp,
          playerDead: playerDeathOffset >= 0,
        })
      }
    } else if (CHECKPOINTS.includes(off)) {
      checkpoints.push({
        at: off,
        baseHp: world.baseHp,
        targetDead: targetDeathOffset >= 0,
        fires,
        displacement: 0,
        playerDead: true,
      })
    }
    if (world.baseHp < baseHpAtBranch && firstBaseDamageOffset < 0) firstBaseDamageOffset = off
    const t = threatById(world, threatId)
    if ((!t || !t.alive) && targetDeathOffset < 0) targetDeathOffset = off
    const playerDead = !p || !p.alive || world.lives < livesAtStart
    if (playerDead && playerDeathOffset < 0) playerDeathOffset = off
    if (world.state !== 'playing') break
  }
  // Review P1: the RNG end state must be read AFTER the branch simulation —
  // reading it before the loop captured the START state, mislabeling it as
  // the branch's end state.
  const rngStateEnd = world.rng.getState()
  return {
    checkpoints,
    firstBaseDamageOffset,
    firstFireOffset,
    firstMoveOffset,
    targetDeathOffset,
    playerDeathOffset,
    endState: world.state,
    rngStateEnd,
  }
}

/** Protocol §6.3 classification over the four branch records. */
export function classifyIdleEvent(cont: BranchRecord, alts: BranchRecord[]): IdleClass {
  const contLost = cont.firstBaseDamageOffset >= 0
  const altAvoids = alts.some((a) => a.firstBaseDamageOffset < 0)
  const altActed = alts.some((a) => a.firstFireOffset >= 0 || a.firstMoveOffset >= 0)
  if (!altActed && contLost) return 'unresolved' // scripted branches never acted — tool defect, not a verdict
  if (!contLost) return 'idle_legitimate' // the stall cost no base HP in the window
  if (altAvoids) return 'idle_causal'
  return 'travel_or_turn_causal'
}

// ------------------------------------------------------------------ main

interface CorpusFailure {
  stageIdx: number
  seed: number
  outcome: string
  failureCause?: string
  ticks: number
}

async function main() {
  if (!fromJson) {
    console.error(
      'usage: counterfactual-idle.ts --from-json <forensics-corpus.json> ' +
        '[--kinds base_destroyed] [--limit 40] [--events-per-run 1] ' +
        '[--pre-window 600] [--window 240] [--json out.json] [--dump S<stage>s<seed>]',
    )
    process.exit(1)
  }
  const corpusRaw = await Bun.file(fromJson as string).json()
  const pd = corpusRaw.perDifficulty?.hard
  if (!pd?.failures) {
    console.error(
      `counterfactual-idle: ${fromJson} has no perDifficulty.hard.failures — is this a run-forensics corpus?`,
    )
    process.exit(1)
  }
  const maxTicks = corpusRaw.maxTicks ?? 36000
  const failures: CorpusFailure[] = (pd.failures as CorpusFailure[]).filter(
    (r) => r.failureCause !== undefined && kinds.has(r.failureCause),
  )
  if (failures.length === 0) {
    console.error(`counterfactual-idle: no runs match kinds=[${[...kinds].join(',')}]`)
    process.exit(1)
  }
  // Even stride across (stage, seed) order — a representative sample, not the first N.
  const chosen: CorpusFailure[] = []
  const stride = failures.length / Math.min(limit, failures.length)
  for (let i = 0; i < Math.min(limit, failures.length); i++) {
    chosen.push(failures[Math.floor(i * stride)])
  }

  console.log(
    `counterfactual-idle: corpus=${(fromJson as string).split(/[\\/]/).pop()} ` +
      `kinds=[${[...kinds].join(',')}] matches=${failures.length} sampled=${chosen.length} ` +
      `preWindow=${preWindow} window=${windowTicks} checkpoints=[${CHECKPOINTS.join(',')}] ` +
      `eventsPerRun=${eventsPerRun}`,
  )

  const events: EventRecord[] = []
  const counts: Record<IdleClass, number> = {
    idle_causal: 0,
    idle_legitimate: 0,
    travel_or_turn_causal: 0,
    unresolved: 0,
  }
  let skipped = 0
  let diverged = 0

  for (const f of chosen) {
    const key = `S${f.stageIdx + 1}s${f.seed}`
    const det = detectSegments(f.stageIdx, f.seed, 'hard', maxTicks, {
      ticks: f.ticks,
      outcome: f.outcome,
    })
    if (!det.outcomeOk) {
      diverged++
      console.error(`  ${key}: REPLAY DIVERGED from corpus (determinism violation) — skipped`)
      skipped++
      continue
    }
    // Qualify: within preWindow before the first base damage; last N segments.
    const qualified = det.segments.filter(
      (s) =>
        det.firstBaseDamageTick >= 0 &&
        s.start <= det.firstBaseDamageTick &&
        s.start >= det.firstBaseDamageTick - preWindow,
    )
    if (qualified.length === 0) {
      skipped++
      continue
    }
    const selected = qualified.slice(-eventsPerRun)

    for (const seg of selected) {
      // Pass 2: fresh deterministic replay to T (branch point = BEFORE tick T
      // executes, so the alternative can act ON the stall tick itself).
      const { world, sim, input } = setupRun(f.stageIdx, f.seed, 'hard')
      let tick = 0
      while (tick < seg.start && world.state === 'playing' && tick < maxTicks) {
        sim.tick()
        input.endFrame()
        world.consumeEvents()
        tick++
      }
      if (tick !== seg.start || world.state !== 'playing') {
        counts.unresolved++
        continue
      }
      const threat = pickThreat(world)
      const p = world.player
      if (!threat || !p) {
        counts.unresolved++
        continue
      }
      const snap = cloneWorld(world)
      const baseHpAtBranch = world.baseHp
      const probeAt = det.trace[seg.start] ?? null
      const onCooldown = probeAt ? probeAt.onCooldown : false

      // continue: factual continuation driven by the SAME input (its state at
      // T is factual — the replay drove it identically).
      const cont = runBranch(world, sim, input, threat.id, baseHpAtBranch)

      const results: Array<BranchRecord | null> = [cont]
      const plans = [
        (w: World, id: number) => planTurnAndFireHold(w, id),
        planIntercept,
        planClearOrAdvance,
      ]
      for (const plan of plans) {
        restoreWorld(world, snap)
        const scripted = new ScriptedInput(() => plan(world, threat.id))
        sim.input = scripted
        results.push(runBranch(world, sim, scripted, threat.id, baseHpAtBranch))
        sim.input = input
      }
      const [contR, tfR, icR, caR] = results
      if (!contR || !tfR || !icR || !caR) {
        counts.unresolved++
        continue
      }
      const cls = classifyIdleEvent(contR, [tfR, icR, caR])
      counts[cls]++
      const acted = (r: BranchRecord) => r.firstFireOffset >= 0 || r.firstMoveOffset >= 0
      const avoiders = [tfR, icR, caR].filter((r) => r.firstBaseDamageOffset < 0)
      const avoidedByInaction =
        cls === 'idle_causal' && avoiders.length > 0 && avoiders.every((r) => !acted(r))
      events.push({
        key,
        stageIdx: f.stageIdx,
        seed: f.seed,
        failureCause: f.failureCause ?? '?',
        corpusTicks: f.ticks,
        firstBaseDamageTick: det.firstBaseDamageTick,
        eventTick: seg.start,
        segmentLen: seg.len,
        branch: seg.branch,
        onCooldown,
        threatCount: threat.count,
        threatId: threat.id,
        threatFallback: threat.fallback,
        threatDeadline: threat.deadline,
        threatKillEta: threat.killEta,
        threatSlack: threat.slack,
        classification: cls,
        avoidedByInaction,
        branches: { cont: contR, turnFire: tfR, intercept: icR, clearAdvance: caR },
      })
      const b = (r: BranchRecord) =>
        `dmg@${r.firstBaseDamageOffset < 0 ? '-' : r.firstBaseDamageOffset}` +
        `/kill@${r.targetDeathOffset < 0 ? '-' : r.targetDeathOffset}` +
        `/fire@${r.firstFireOffset < 0 ? '-' : r.firstFireOffset}` +
        `/step@${r.firstMoveOffset < 0 ? '-' : r.firstMoveOffset}` +
        `${avoidedByInaction && r.firstBaseDamageOffset < 0 ? '(inact)' : ''}`
      console.log(
        `  ${key} t=${seg.start} seg=${seg.len}t branch=${seg.branch} cd=${onCooldown ? 1 : 0} ` +
          `threats=${threat.count}${threat.fallback ? '(fb)' : ''} slack=${threat.slack.toFixed(0)} → ${cls} ` +
          `[cont ${b(contR)} | t&f ${b(tfR)} | int ${b(icR)} | c/a ${b(caR)}]`,
      )
      if (dumpKey === key) {
        console.log(
          `  --- dump ${key}: per-tick trace around t=${seg.start} (branch/move/fire/cd/baseHp/px,py)`,
        )
        const from = Math.max(0, seg.start - 60)
        const to = Math.min(det.trace.length, seg.start + 90)
        for (let i = from; i < to; i++) {
          const pr = det.trace[i]
          console.log(
            `    t=${i} ${pr.branch}/${pr.moveDir ?? '-'}/${pr.fire ? 'F' : '-'}/${pr.onCooldown ? 'cd' : '--'} ` +
              `hp=${pr.baseHp} p=(${Math.round(pr.px)},${Math.round(pr.py)})${i === seg.start ? '  <== EVENT' : ''}`,
          )
        }
      }
    }
  }

  const total = events.length
  const causalShare = total > 0 ? counts.idle_causal / total : 0
  console.log('==============================================================================')
  console.log(
    `events=${total} (skipped=${skipped}, diverged=${diverged}) ` +
      `idle_causal=${counts.idle_causal} idle_legitimate=${counts.idle_legitimate} ` +
      `travel_or_turn_causal=${counts.travel_or_turn_causal} unresolved=${counts.unresolved}`,
  )
  console.log(`idle_causal share = ${(causalShare * 100).toFixed(1)}%`)
  if (causalShare < 0.2) {
    console.log(
      'NOTE (protocol §6.3): idle_causal is LOW — do NOT continue treating idle-alert elimination as the main line.',
    )
  }
  if (jsonOut) {
    await Bun.write(
      jsonOut,
      JSON.stringify({
        generatedAt: new Date().toISOString(),
        fromCorpus: fromJson as string,
        filters: {
          kinds: [...kinds],
          limit,
          eventsPerRun,
          preWindow,
          windowTicks,
          checkpoints: CHECKPOINTS,
        },
        total,
        counts,
        idleCausalShare: causalShare,
        events,
      }),
    )
    console.log(`JSON → ${jsonOut}`)
  }
}

// Import guard: run the CLI only when executed directly, not when imported
// by tests (tests/counterfactual-idle.test.ts exercises the pure classifier).
if (import.meta.main) {
  await main()
}
