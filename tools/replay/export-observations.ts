/**
 * export-observations.ts — replay corpus -> NN training shards (plan §2.2, NN-M0b).
 *
 * NDJSON (one replay per line) -> parseReplayFile -> restoreWorld ->
 * ReplayInput -> sim.tick loop. At each decision tick (plan §1.3) we capture
 *   (obs-encoder(world), human frame, invalid-action mask, decision condition)
 * and write one npy shard per replay plus a manifest.json.
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
 *   bun tools/replay/export-observations.ts nn-demo/bc-replays-s1-s2.ndjson --out tmp/nn-det --verify-determinism
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
import { verifyReplayText } from './verify-replay'
import type { InputFrame } from '../../src/replay/types'
import type { Direction } from '../../src/constants'
import {
  ObsEncoder,
  decisionTick,
  actionFromFrame,
  computeMasks,
  OBS_SCHEMA_MAJOR,
} from '../../src/nn/obs-encoder'
import { writeShard } from '../../src/nn/npy'
import { mkdirSync, writeFileSync, readdirSync, readFileSync } from 'fs'
import { basename } from 'path'
import { platform, arch, cpus } from 'os'

const EXPORTER_VERSION = '0.2.0'
const K = 10 // subsample period (plan §1.3)

interface Accumulator {
  obs: Uint8Array[]
  scalars: Float32Array[]
  actions: number[]
  masks: number[]
  conditions: number[]
  nTurn: number
  nFire: number
  nItem: number
  nItemEvents: number // raw guard/frenzy bit-changes (gate ⑤ cross-check)
  nSub: number
  nSamples: number
}

function newAcc(): Accumulator {
  return { obs: [], scalars: [], actions: [], masks: [], conditions: [], nTurn: 0, nFire: 0, nItem: 0, nItemEvents: 0, nSub: 0, nSamples: 0 }
}

interface ExportResult {
  acc: Accumulator
  meta: Record<string, unknown>
  ok: boolean
  reason: string
}

export function exportReplay(text: string, fileLabel: string, skipVerify: boolean): ExportResult {
  const parsed = parseReplayFile(text)
  if ('error' in parsed) return { acc: newAcc(), meta: {}, ok: false, reason: `parse: ${parsed.error}` }

  const replay = parsed.replay
  const meta = replay.metadata

  // ---- Acceptance gate: re-verify the recording (plan §0) ----
  if (!skipVerify) {
    const v = verifyReplayText(text, fileLabel)
    if (v.verdict !== 'OK') {
      return { acc: newAcc(), meta: { stage: meta.stage, type: replay.type }, ok: false, reason: `desync: ${v.reason}` }
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

  let prevDir: Direction | null = null
  let prevGuard = false
  let prevFrenzy = false
  let t = 0
  const startState = world.state
  const encStart = performance.now()

  while (!input.isFinished && t < replay.totalTicks + 10) {
    // obs(t): world state BEFORE tick t's human input is consumed (plan §1.3 phase).
    encoder.encode(world)
    const cur = frames1[t]
    const curDir = cur?.direction ?? null
    const curGuard = cur?.guard ?? false
    const curFrenzy = cur?.frenzy ?? false
    if (prevGuard !== curGuard || prevFrenzy !== curFrenzy) acc.nItemEvents++

    const { isDecision, condition } = decisionTick(
      t, world, prevDir, curDir, prevGuard, curGuard, prevFrenzy, curFrenzy, K,
    )
    if (isDecision) {
      const label = actionFromFrame(cur ?? { direction: null, firing: false, guard: false, frenzy: false })
      const masks = computeMasks(world)
      acc.obs.push(encoder.obs.slice())
      acc.scalars.push(encoder.scalars.slice())
      acc.actions.push(label.move, label.fire, label.item)
      acc.masks.push(...masks.move, ...masks.fire, ...masks.item)
      acc.conditions.push(condition)
      if (condition === 0) acc.nTurn++
      else if (condition === 1) acc.nFire++
      else if (condition === 2) acc.nItem++
      else acc.nSub++
      acc.nSamples++
    }

    sim.tick()
    input.advance()
    world.consumeEvents?.()
    prevDir = curDir
    prevGuard = curGuard
    prevFrenzy = curFrenzy
    t++
    const st: string = world.state
    if (st === 'stageclear' || st === 'gameover' || st === 'victory') break
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
    encodeMs: encEnd - encStart,
    ticks: t,
    usPerTick: (encEnd - encStart) * 1000 / Math.max(1, t),
  }
  return { acc, meta: out, ok: true, reason: 'exported' }
}

function flushShard(acc: Accumulator, dir: string, name: string, baseManifest: Record<string, unknown>): void {
  const N = acc.nSamples
  if (N === 0) return
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * 24)
  const actions = new Uint8Array(N * 3)
  const masks = new Uint8Array(N * 10)
  const conditions = new Uint8Array(N)
  for (let i = 0; i < N; i++) {
    obs.set(acc.obs[i], i * 14 * 26 * 26)
    scalars.set(acc.scalars[i], i * 24)
    actions[i * 3] = acc.actions[i * 3]
    actions[i * 3 + 1] = acc.actions[i * 3 + 1]
    actions[i * 3 + 2] = acc.actions[i * 3 + 2]
    for (let j = 0; j < 10; j++) masks[i * 10 + j] = acc.masks[i * 10 + j]
    conditions[i] = acc.conditions[i]
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
  writeShard(dir, { obs, scalars, actions, masks, conditions }, manifest)
  // also drop a tiny npy summary for quick inspection
  writeFileSync(`${dir}/_summary.json`, JSON.stringify(manifest, null, 2))
}

/** Export one replay text to <outDir>/<shardName> (gate-skipping optional). */
function exportAndWrite(text: string, fileLabel: string, outDir: string, shardName: string, skipVerify: boolean): ExportResult {
  const res = exportReplay(text, fileLabel, skipVerify)
  if (res.ok) flushShard(res.acc, `${outDir}/${shardName}`, shardName, res.meta as Record<string, unknown>)
  return res
}

/** Byte-for-byte compare every .npy file between two shard dirs. */
function compareShards(a: string, b: string): { ok: boolean; detail: string } {
  const files = readdirSync(a).filter((f) => f.endsWith('.npy'))
  for (const f of files) {
    const ba = readFileSync(`${a}/${f}`)
    const bb = readFileSync(`${b}/${f}`)
    if (ba.length !== bb.length) return { ok: false, detail: `${f} size ${ba.length} vs ${bb.length}` }
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
    console.error('usage: bun tools/replay/export-observations.ts <demos.ndjson...> --out <dir> [--skip-verify] [--verify-determinism]')
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
      const ls = String(content).split('\n').filter((l: string) => l.trim().length > 0)
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
        results.push(`[DET SKIP] ${label}: ${ra.ok ? '' : 'A ' + ra.reason} ${rb.ok ? '' : 'B ' + rb.reason}`)
        continue
      }
      const cmp = compareShards(`${detA}/${shard}`, `${detB}/${shard}`)
      if (!cmp.ok) fails++
      results.push(`[${cmp.ok ? 'DET OK' : 'DET FAIL'}] ${label}: ${cmp.detail}`)
    }
    console.log(results.join('\n'))
    console.log(`\n=== determinism check ===`)
    console.log(`replays=${lines.length} byteIdentical=${lines.length - fails} fails=${fails}`)
    writeFileSync(`${outDir}/_determinism_report.json`, JSON.stringify({ replays: lines.length, fails, env: ENV, results }, null, 2))
    process.exit(fails > 0 ? 1 : 0)
  }

  let total = 0
  let kept = 0
  let skipped = 0
  let totalSamples = 0
  let totalEncodeMs = 0
  let totalTicks = 0
  const perFile: string[] = []

  for (const f of files) {
    const content = await (Bun.file(f) as any).text()
    const lines = String(content).split('\n').filter((l: string) => l.trim().length > 0)
    const base = basename(f)
    for (let i = 0; i < lines.length; i++) {
      total++
      const label = `${base}#${i}`
      const res = exportAndWrite(lines[i], label, outDir, `shard_${base.replace(/[^a-zA-Z0-9]/g, '_')}_${i.toString().padStart(3, '0')}`, skipVerify)
      if (!res.ok) {
        skipped++
        perFile.push(`[SKIP] ${label} reason=${res.reason}`)
        continue
      }
      kept++
      const m = res.meta as any
      totalSamples += m.nSamples ?? 0
      totalEncodeMs += m.encodeMs ?? 0
      totalTicks += m.ticks ?? 0
      perFile.push(`[OK]   ${label} samples=${m.nSamples} stage=${m.stage} outcome=${m.outcome} encMs=${m.encodeMs?.toFixed?.(1)}`)
    }
  }

  console.log(perFile.join('\n'))
  console.log(`\n=== export summary ===`)
  console.log(`replays total=${total} kept=${kept} skipped=${skipped} samples=${totalSamples}`)
  const usPerTick = totalEncodeMs * 1000 / Math.max(1, totalTicks)
  console.log(`encode time: ${totalEncodeMs.toFixed(1)}ms over ${totalTicks} ticks = ${usPerTick.toFixed(3)} us/tick`)
  console.log(`shards written under: ${outDir}`)
  writeFileSync(`${outDir}/_export_report.json`, JSON.stringify({
    total, kept, skipped, totalSamples, outDir, skipVerify,
    perf: {
      ms: Math.round(totalEncodeMs),
      ticks: totalTicks,
      usPerTick: +usPerTick.toFixed(3),
    },
    env: ENV,
  }, null, 2))
}

if (import.meta.main) {
  main()
}
