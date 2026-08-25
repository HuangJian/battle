/**
 * export-observations.ts — replay corpus -> NN training shards (plan §2.2, NN-M0b).
 *
 * NDJSON (one replay per line) -> parseReplayFile -> restoreWorld ->
 * ReplayInput -> sim.tick loop. At each decision tick (plan §1.3) we capture
 *   (obs-encoder(world), human frame, invalid-action mask, decision condition)
 * and write one npy shard per replay plus a manifest.json.
 *
 * v2 (plan/AI-No-Items-Warmstart.md M2, OBS_SCHEMA_MAJOR=2)：
 *   · item 头删除 —— actions→(N,2) [move,fire]，masks→(N,7)；
 *   · 道具动作帧过滤（a_item≠none 剔除，与新政策一致；实测 ~0.07% 决策帧）；
 *   · returns：按 RL reward 定义（tools/sim/rl-reward.ts）逐决策帧结算、
 *     γ=0.995 折现为 return，落盘 returns.npy（M3 value 头 MC 预置）。
 *
 * Acceptance gate (plan §2.1 / §0): every replay is first re-verified with
 * verifyReplayText (terminal-state + tick-hash chain). Desynced replays are
 * skipped and logged — they never poison the training set.
 *
 * Modes / flags:
 *   --out <dir>            output root (default tmp/nn-export)
 *   --skip-verify          skip the re-verify gate (use only on verified corpora)
 *   --verify-determinism   export each replay TWICE to two temp dirs and
 *                          byte-compare the .npy shards (gate ②: determinism).
 *                          No training shards are written in this mode.
 *
 * Usage:
 *   bun tools/replay/export-observations.ts nn-demo/*.ndjson --out tmp/nn-export
 *   bun tools/replay/export-observations.ts nn-demo/*.ndjson --out tmp/nn-export --skip-verify
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, GRID, CELL, ENEMIES_PER_STAGE } from '../../src/constants'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { unpackFrames } from '../../src/replay/pack'
import { verifyReplayText } from './verify-replay'
import type { InputFrame } from '../../src/replay/types'
import type { Direction } from '../../src/constants'
import {
  ObsEncoder,
  decisionTick,
  actionFromFrame,
  computeMasks,
  OBS_SCHEMA_MAJOR,
  SCALAR_DIM,
} from '../../src/nn/obs-encoder'
import { writeNpy } from '../../src/nn/npy'
import { mkdirSync, writeFileSync, readdirSync, readFileSync } from 'fs'
import { basename } from 'path'
import { platform, arch, cpus } from 'os'
import {
  REWARD_SCALE,
  discountReturns,
  phiNow,
  countBaseWall,
  sampleBasePressure,
  TELEMETRY_SAMPLE_TICKS,
  type PhiCounters,
} from '../sim/rl-reward'
import { scoreRun, V7_SCORE_CONFIG, type Weights } from '../eval/godai-score'
import type { RunTelemetry } from '../sim/simulation-runner'

const EXPORTER_VERSION = '2.0.0'
const K = 10 // subsample period (plan §1.3)
const MASK_DIM = 7

const RL_SCORE_CONFIG = {
  ...V7_SCORE_CONFIG,
  lossWeights: {
    progress: 0.3,
    baseIntegrity: 0.25,
    baseSafety: 0.25,
    tempo: 0.08,
    accuracy: 0.06,
    openingTempo: 0.03,
    loot: 0.03,
  } as Weights,
}

interface Accumulator {
  obs: Uint8Array[]
  scalars: Float32Array[]
  actions: number[] // [move, fire] pairs, flattened
  masks: number[]
  conditions: number[]
  rewards: number[] // v2: RL reward per sample（terminal-anchored, Σ ≡ SCALE×gatedScore）
  nTurn: number
  nFire: number
  nItem: number
  nItemEvents: number // raw guard/frenzy bit-changes (gate ⑤ cross-check)
  nSub: number
  nFilteredItem: number // v2: 剔除的道具动作帧数
  nSamples: number
}

function newAcc(): Accumulator {
  return {
    obs: [],
    scalars: [],
    actions: [],
    masks: [],
    conditions: [],
    rewards: [],
    nTurn: 0,
    nFire: 0,
    nItem: 0,
    nItemEvents: 0,
    nFilteredItem: 0,
    nSub: 0,
    nSamples: 0,
  }
}

interface ExportResult {
  acc: Accumulator
  meta: Record<string, unknown>
  ok: boolean
  reason: string
}

interface Tel {
  enemyTotal: number
  startLives: number
  playerDeaths: number
  playerShots: number
  powerUpsSpawned: number
  powerUpsCollected: number
  starsCollected: number
  baseWallTotal: number
  baseWallIntact: number
  basePressureSum: number
  basePressureSamples: number
  cellsVisited: Set<number>
  firstKillTick: number | undefined
}

function newTel(stage: unknown, world: World): Tel {
  return {
    enemyTotal: (stage as { enemyCount?: number })?.enemyCount ?? ENEMIES_PER_STAGE,
    startLives: world.difficulty?.startLives ?? START_LIVES,
    playerDeaths: 0,
    playerShots: 0,
    powerUpsSpawned: 0,
    powerUpsCollected: 0,
    starsCollected: 0,
    baseWallTotal: countBaseWall(world),
    baseWallIntact: countBaseWall(world),
    basePressureSum: 0,
    basePressureSamples: 0,
    cellsVisited: new Set<number>(),
    firstKillTick: undefined,
  }
}

function makeCounters(tel: Tel, world: World, ticks: number, baseAlive: boolean): PhiCounters {
  return {
    enemyTotal: tel.enemyTotal,
    startLives: tel.startLives,
    kills: world.killCount,
    lives: world.lives,
    ticks,
    baseAlive,
    baseWallTotal: tel.baseWallTotal,
    baseWallIntact: tel.baseWallIntact,
    playerShots: tel.playerShots,
    powerUpsCollected: tel.powerUpsCollected,
    powerUpsSpawned: tel.powerUpsSpawned,
    basePressureMean:
      tel.basePressureSamples > 0 ? tel.basePressureSum / tel.basePressureSamples : 0,
    basePressureSamples: tel.basePressureSamples,
    firstKillTick: tel.firstKillTick,
  }
}

export function exportReplay(text: string, fileLabel: string, skipVerify: boolean): ExportResult {
  const parsed = parseReplayFile(text)
  if ('error' in parsed)
    return { acc: newAcc(), meta: {}, ok: false, reason: `parse: ${parsed.error}` }

  const replay = parsed.replay
  const meta = replay.metadata

  // ---- Acceptance gate: re-verify the recording (plan §0) ----
  if (!skipVerify) {
    const v = verifyReplayText(text, fileLabel)
    if (v.verdict !== 'OK') {
      return {
        acc: newAcc(),
        meta: { stage: meta.stage, type: replay.type },
        ok: false,
        reason: `desync: ${v.reason}`,
      }
    }
  }

  // ---- Rebuild world exactly like PlaybackController.start() ----
  const world = new World()
  world.rng.reseed(replay.seed)
  const dkey = meta.difficulty || 'classic'
  world.difficultyKey = dkey
  world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
  world.rules = RULES[dkey] ?? DEFAULT_RULES
  const stage = STAGES[meta.stage] ?? STAGES[0]
  world.loadStageData(stage, 0)
  restoreWorld(world, replay.initialSnapshot)
  const input = new ReplayInput(replay.frames)
  const sim = new Simulation(world, input)
  sim.input = input
  sim.input2 = input.input2 ?? null
  world.state = 'playing'

  const frames1: InputFrame[] = unpackFrames(replay.frames)?.p1 ?? []
  const encoder = new ObsEncoder()
  const acc = newAcc()
  const tel = newTel(stage, world)
  const seenPuIds = new Set<number>()
  let prevLivePuIds = new Set<number>()

  const phis: number[] = [] // Φ(捕获样本状态)，长度 == acc.nSamples
  let prevDir: Direction | null = null
  let prevGuard = false
  let prevFrenzy = false
  let t = 0
  const startState = world.state
  const encStart = performance.now()
  let outcome: 'stage_clear' | 'base_destroyed' | 'lives_exhausted' | 'timeout' = 'timeout'

  while (!input.isFinished && t < replay.totalTicks + 10) {
    // obs(t): world state BEFORE tick t's human input is consumed (plan §1.3 phase).
    encoder.encode(world)
    const cur = frames1[t]
    const curDir = cur?.direction ?? null
    const curGuard = cur?.guard ?? false
    const curFrenzy = cur?.frenzy ?? false
    if (prevGuard !== curGuard || prevFrenzy !== curFrenzy) acc.nItemEvents++

    const { isDecision, condition } = decisionTick(
      t,
      world,
      prevDir,
      curDir,
      prevGuard,
      curGuard,
      prevFrenzy,
      curFrenzy,
      K,
    )
    if (isDecision) {
      // v2: 道具动作帧剔除（guard/frenzy 任一激活 → 不入训练样本）
      if (curGuard || curFrenzy) {
        acc.nFilteredItem++
      } else {
        const label = actionFromFrame({ direction: curDir, firing: cur?.firing ?? false })
        const masks = computeMasks(world)
        acc.obs.push(encoder.obs.slice())
        acc.scalars.push(encoder.scalars.slice())
        acc.actions.push(label.move, label.fire)
        acc.masks.push(...masks.move, ...masks.fire)
        acc.conditions.push(condition)
        const baseAlive = !world.tileMap.isBaseDestroyed()
        tel.baseWallIntact = countBaseWall(world)
        phis.push(phiNow(makeCounters(tel, world, t, baseAlive)))
        if (condition === 0) acc.nTurn++
        else if (condition === 1) acc.nFire++
        else if (condition === 2) acc.nItem++
        else acc.nSub++
        acc.nSamples++
      }
    }

    sim.tick()
    input.advance()
    // ---- telemetry（语义对齐 simulation-runner）----
    let collectedThisTick = 0
    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed') {
        if ((e as any).by === 'player' && tel.firstKillTick === undefined) tel.firstKillTick = t
        if ((e as any).tank?.isPlayer) tel.playerDeaths++
      } else if (e.type === 'bullet_fired' && (e as any).bullet?.isPlayer) {
        tel.playerShots++
      } else if (e.type === 'powerup_collected') {
        collectedThisTick++
        tel.powerUpsCollected++
        if ((e as any).powerUp === 'star') tel.starsCollected++
      }
    }
    {
      const live = new Set<number>()
      for (const pu of world.powerUps) {
        live.add(pu.id)
        if (!seenPuIds.has(pu.id)) {
          seenPuIds.add(pu.id)
          tel.powerUpsSpawned++
        }
      }
      let vanished = 0
      for (const id of prevLivePuIds) if (!live.has(id)) vanished++
      tel.powerUpsSpawned += Math.max(0, collectedThisTick - vanished)
      prevLivePuIds = live
    }
    if (t % TELEMETRY_SAMPLE_TICKS === 0) {
      tel.basePressureSum += sampleBasePressure(world)
      tel.basePressureSamples++
      if (world.player?.alive) {
        const col = Math.floor((world.player.x + world.player.w / 2) / CELL)
        const row = Math.floor((world.player.y + world.player.h / 2) / CELL)
        tel.cellsVisited.add(row * GRID + col)
      }
    }
    prevDir = curDir
    prevGuard = curGuard
    prevFrenzy = curFrenzy
    t++
    const st: string = world.state
    if (st === 'stageclear' || st === 'victory' || st === 'gameover') {
      outcome =
        st === 'gameover'
          ? world.tileMap.isBaseDestroyed()
            ? 'base_destroyed'
            : 'lives_exhausted'
          : 'stage_clear'
      break
    }
  }
  // 超时（replay 截断但未清关）→ 按 replay.type 兜底
  if (outcome === 'timeout' && replay.type === 'clear') outcome = 'stage_clear'

  // ---- 终局打分 + reward（telescoping）----
  const baseAliveFinal = !world.tileMap.isBaseDestroyed()
  tel.baseWallIntact = countBaseWall(world)
  const scorable = {
    outcome,
    ticks: t,
    finalState: {
      killCount: world.killCount,
      lives: world.lives,
      baseAlive: baseAliveFinal,
    },
    firstKillTick: tel.firstKillTick,
    telemetry: {
      enemyTotal: tel.enemyTotal,
      startLives: tel.startLives,
      playerDeaths: tel.playerDeaths,
      playerShots: tel.playerShots,
      powerUpsSpawned: tel.powerUpsSpawned,
      powerUpsCollected: tel.powerUpsCollected,
      starsCollected: tel.starsCollected,
      finalPlayerLevel: world.playerLevel,
      baseWallIntact: tel.baseWallIntact,
      baseWallTotal: tel.baseWallTotal,
      basePressureMean:
        tel.basePressureSamples > 0 ? tel.basePressureSum / tel.basePressureSamples : 0,
      basePressureSamples: tel.basePressureSamples,
      cellsVisited: tel.cellsVisited.size,
      deaths: [],
    } satisfies Omit<RunTelemetry, 'deaths'> & { deaths: never[] },
  } as any
  const scored = scoreRun(scorable, RL_SCORE_CONFIG as any)
  const gatedScore = outcome === 'base_destroyed' ? scored.score * 0.1 : scored.score
  const phiFinal = phiNow(makeCounters(tel, world, t, baseAliveFinal))
  const n = phis.length
  for (let i = 0; i < n; i++) {
    const next = i + 1 < n ? phis[i + 1] : phiFinal
    acc.rewards.push(next - phis[i])
  }
  if (n > 0) {
    acc.rewards[n - 1] += REWARD_SCALE * gatedScore - (phiFinal - phis[0])
  }

  const encEnd = performance.now()
  const out: Record<string, unknown> = {
    stage: meta.stage,
    outcome: replay.type,
    startState,
    endState: world.state,
    seed: replay.seed,
    totalTicks: replay.totalTicks,
    nSamples: acc.nSamples,
    conditionBreakdown: { turn: acc.nTurn, fire: acc.nFire, item: acc.nItem, subsample: acc.nSub },
    filteredItemFrames: acc.nFilteredItem,
    gatedScore,
    score: scored.score,
    encodeMs: encEnd - encStart,
    ticks: t,
    usPerTick: ((encEnd - encStart) * 1000) / Math.max(1, t),
  }
  return { acc, meta: out, ok: true, reason: 'exported' }
}

function flushShard(
  acc: Accumulator,
  dir: string,
  name: string,
  baseManifest: Record<string, unknown>,
): void {
  const N = acc.nSamples
  if (N === 0) return
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * SCALAR_DIM)
  const actions = new Uint8Array(N * 2)
  const masks = new Uint8Array(N * MASK_DIM)
  const conditions = new Uint8Array(N)
  const returns = new Float32Array(N)
  const ret = discountReturns(acc.rewards)
  for (let i = 0; i < N; i++) {
    obs.set(acc.obs[i], i * 14 * 26 * 26)
    scalars.set(acc.scalars[i], i * SCALAR_DIM)
    actions[i * 2] = acc.actions[i * 2]
    actions[i * 2 + 1] = acc.actions[i * 2 + 1]
    for (let j = 0; j < MASK_DIM; j++) masks[i * MASK_DIM + j] = acc.masks[i * MASK_DIM + j]
    conditions[i] = acc.conditions[i]
    returns[i] = ret[i]
  }
  const manifest = {
    schemaMajor: OBS_SCHEMA_MAJOR,
    obsSchemaMajor: OBS_SCHEMA_MAJOR,
    exporterVersion: EXPORTER_VERSION,
    shard: name,
    nSamples: N,
    conditionBreakdown: baseManifest.conditionBreakdown,
    ...baseManifest,
  }
  mkdirSync(dir, { recursive: true })
  writeNpy(`${dir}/obs.npy`, obs, [N, 14, 26, 26], 'u1')
  writeNpy(`${dir}/scalars.npy`, scalars, [N, SCALAR_DIM], 'f4')
  writeNpy(`${dir}/actions.npy`, actions, [N, 2], 'u1')
  writeNpy(`${dir}/masks.npy`, masks, [N, MASK_DIM], 'u1')
  writeNpy(`${dir}/conditions.npy`, conditions, [N], 'u1')
  writeNpy(`${dir}/returns.npy`, returns, [N], 'f4')
  writeFileSync(`${dir}/manifest.json`, JSON.stringify(manifest, null, 2))
  // also drop a tiny npy summary for quick inspection
  writeFileSync(`${dir}/_summary.json`, JSON.stringify(manifest, null, 2))
}

/** Export one replay text to <outDir>/<shardName> (gate-skipping optional). */
function exportAndWrite(
  text: string,
  fileLabel: string,
  outDir: string,
  shardName: string,
  skipVerify: boolean,
): ExportResult {
  const res = exportReplay(text, fileLabel, skipVerify)
  if (res.ok)
    flushShard(res.acc, `${outDir}/${shardName}`, shardName, res.meta as Record<string, unknown>)
  return res
}

/** Byte-for-byte compare every .npy file between two shard dirs. */
function compareShards(a: string, b: string): { ok: boolean; detail: string } {
  const files = readdirSync(a).filter((f) => f.endsWith('.npy'))
  for (const f of files) {
    const ba = readFileSync(`${a}/${f}`)
    const bb = readFileSync(`${b}/${f}`)
    if (ba.length !== bb.length)
      return { ok: false, detail: `${f} size ${ba.length} vs ${bb.length}` }
    if (Buffer.compare(ba, bb) !== 0) return { ok: false, detail: `${f} bytes differ` }
  }
  return { ok: true, detail: `${files.length} npy files byte-identical` }
}

const ENV = {
  runtime: `bun ${Bun.version}`,
  platform: platform(),
  arch: arch(),
  cpuCores: cpus().length,
}

async function main(): Promise<void> {
  const args = process.argv.slice(2)
  let skipVerify = false
  let determinism = false
  const files: string[] = []
  for (const a of args) {
    if (a === '--skip-verify') skipVerify = true
    else if (a === '--verify-determinism') determinism = true
    else if (a === '--out') continue
    else if (args[args.indexOf(a) - 1] === '--out') continue
    else if (a.startsWith('--')) continue
    else files.push(a)
  }
  const outIdx = args.indexOf('--out')
  const outDir = outIdx >= 0 ? args[outIdx + 1] : 'tmp/nn-export'
  if (files.length === 0) {
    console.error(
      'usage: bun tools/replay/export-observations.ts <demos.ndjson...> --out <dir> [--skip-verify] [--verify-determinism]',
    )
    process.exit(2)
  }
  mkdirSync(outDir, { recursive: true })

  if (determinism) {
    // Gate ②: export each replay twice, byte-compare. No training shards kept.
    const detA = `${outDir}/__det_a`
    const detB = `${outDir}/__det_b`
    mkdirSync(detA, { recursive: true })
    mkdirSync(detB, { recursive: true })
    const lines: { text: string; label: string }[] = []
    for (const f of files) {
      const content = await (Bun.file(f) as any).text()
      const ls = String(content)
        .split('\n')
        .filter((l: string) => l.trim().length > 0)
      const base = basename(f)
      for (let i = 0; i < ls.length; i++) lines.push({ text: ls[i], label: `${base}#${i}` })
    }
    const results: string[] = []
    let fails = 0
    for (const { text, label } of lines) {
      const shard = `shard_${label.replace(/[^a-zA-Z0-9]/g, '_')}`
      const ra = exportAndWrite(text, label, detA, shard, skipVerify)
      const rb = exportAndWrite(text, label, detB, shard, skipVerify)
      if (!ra.ok || !rb.ok) {
        results.push(
          `[DET SKIP] ${label}: ${ra.ok ? '' : 'A ' + ra.reason} ${rb.ok ? '' : 'B ' + rb.reason}`,
        )
        continue
      }
      const cmp = compareShards(`${detA}/${shard}`, `${detB}/${shard}`)
      if (!cmp.ok) fails++
      results.push(`[${cmp.ok ? 'DET OK' : 'DET FAIL'}] ${label}: ${cmp.detail}`)
    }
    console.log(results.join('\n'))
    console.log(`\n=== determinism check ===`)
    console.log(`replays=${lines.length} byteIdentical=${lines.length - fails} fails=${fails}`)
    writeFileSync(
      `${outDir}/_determinism_report.json`,
      JSON.stringify({ replays: lines.length, fails, env: ENV, results }, null, 2),
    )
    process.exit(fails > 0 ? 1 : 0)
  }

  let total = 0
  let kept = 0
  let skipped = 0
  let totalSamples = 0
  let totalFilteredItem = 0
  let totalEncodeMs = 0
  let totalTicks = 0
  const perFile: string[] = []

  for (const f of files) {
    const content = await (Bun.file(f) as any).text()
    const lines = String(content)
      .split('\n')
      .filter((l: string) => l.trim().length > 0)
    const base = basename(f)
    for (let i = 0; i < lines.length; i++) {
      total++
      const label = `${base}#${i}`
      const res = exportAndWrite(
        lines[i],
        label,
        outDir,
        `shard_${base.replace(/[^a-zA-Z0-9]/g, '_')}_${i.toString().padStart(3, '0')}`,
        skipVerify,
      )
      if (!res.ok) {
        skipped++
        perFile.push(`[SKIP] ${label} reason=${res.reason}`)
        continue
      }
      kept++
      const m = res.meta as any
      totalSamples += m.nSamples ?? 0
      totalFilteredItem += m.filteredItemFrames ?? 0
      totalEncodeMs += m.encodeMs ?? 0
      totalTicks += m.ticks ?? 0
      perFile.push(
        `[OK]   ${label} samples=${m.nSamples} stage=${m.stage} outcome=${m.outcome} filteredItem=${m.filteredItemFrames} encMs=${m.encodeMs?.toFixed?.(1)}`,
      )
    }
  }

  console.log(perFile.join('\n'))
  console.log(`\n=== export summary ===`)
  console.log(
    `replays total=${total} kept=${kept} skipped=${skipped} samples=${totalSamples} filteredItemFrames=${totalFilteredItem}`,
  )
  const usPerTick = (totalEncodeMs * 1000) / Math.max(1, totalTicks)
  console.log(
    `encode time: ${totalEncodeMs.toFixed(1)}ms over ${totalTicks} ticks = ${usPerTick.toFixed(3)} us/tick`,
  )
  console.log(`shards written under: ${outDir}`)
  writeFileSync(
    `${outDir}/_export_report.json`,
    JSON.stringify(
      {
        total,
        kept,
        skipped,
        totalSamples,
        totalFilteredItem,
        outDir,
        skipVerify,
        perf: {
          ms: Math.round(totalEncodeMs),
          ticks: totalTicks,
          usPerTick: +usPerTick.toFixed(3),
        },
        env: ENV,
      },
      null,
      2,
    ),
  )
}

if (import.meta.main) {
  main()
}
