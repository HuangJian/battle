#!/usr/bin/env bun
/**
 * export-replay-labels.ts — 人类 probe replay → BC 语料 shards（与 export-godai-labels 同构）。
 *
 * God exporter 是"边跑边采"（GodAI 驱动）；本工具是"事后重放"（replay 输入驱动同一仿真），
 * 其它一字同规：同一 ObsEncoder、同一 decisionTick 事件谓词（turn/fire-edge/item/subsample K=10）、
 * 同一 masks 口径、同一 npy 落盘格式（obs/scalars/actions/masks/conditions + manifest），
 * `train/bc.py`（data.dataset）可直接消费。
 *
 * 有意不同的三处（都是降级为"无"，不发明新语义）：
 *  1. near-miss 只打标不复制（godai 默认 2× 超采样；人类语料加权以后在训练侧做，这里保持原样）。
 *  2. 不算 returns/phi（train/bc.py returns.npy 可选；纯 BC 双头不需要）。
 *  3. 只收通关局（wins-only 恒开：verdict band==solvable，或无 verdicts 文件时 metadata killCount>=20）。
 *
 * 保真：每局重放与 replay 内 tickHashes 全对才落盘（verify-replay 同契约），外加 kills/元数据对账；
 * 任一不符即响亮跳过（'fidelity-skip'），绝不静默入库。
 *
 * Usage:
 *   bun tools/sim/export-replay-labels.ts --replays <file|dir> [--replays ...]
 *       --out tmp/human-shards [--verdicts <verdicts.jsonl>] [--teacher-label human]
 *       [--session <name>] [--min-kills 20]
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { worldTickHash } from '../../src/replay/tickHash'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { BASE_POS, CELL } from '../../src/constants'
import {
  ObsEncoder,
  decisionTick,
  actionFromFrame,
  computeMasks,
  OBS_SCHEMA_MAJOR,
  OBS_CHANNELS,
  BOARD,
  SCALAR_DIM,
  SCHEMA_FINGERPRINT,
} from '../../src/nn/obs-encoder'
import { writeNpy } from '../../src/nn/npy'
import { BASE_PRESSURE_RADIUS } from './rl-reward'
import { mkdirSync, writeFileSync, readdirSync, readFileSync, statSync } from 'fs'
import { join, basename } from 'path'

const EXPORTER_VERSION = '1.0.0-replay'
const OBS_N = OBS_CHANNELS * BOARD * BOARD
const MASK_DIM = 7
const K = 10

interface Sample {
  obs: Uint8Array
  scalars: Float32Array
  move: number
  fire: number
  masks: number[]
  nearMiss: boolean
  cond: number
}

function fail(msg: string): never {
  console.error(`[export-replay-labels] ${msg}`)
  process.exit(2)
}

function collectReplayFiles(inputs: string[]): string[] {
  const out: string[] = []
  for (const p of inputs) {
    const st = statSync(p, { throwIfNoEntry: false })
    if (!st) fail(`replays 路径不存在: ${p}`)
    if (st!.isDirectory()) {
      for (const f of readdirSync(p).sort()) {
        if (f.endsWith('.replay')) out.push(join(p, f))
      }
    } else if (p.endsWith('.replay')) {
      out.push(p)
    } else fail(`非 .replay 文件: ${p}（目录或 .replay 二选一）`)
  }
  return out
}

function loadVerdicts(path: string): Map<string, any> {
  const m = new Map<string, any>()
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const l = line.trim()
    if (!l) continue
    const v = JSON.parse(l)
    m.set(`${v.stage}/${v.seed}`, v)
  }
  return m
}

function convertOne(
  file: string,
  verdicts: Map<string, any> | null,
  minKills: number,
): { ok: boolean; detail: string; samples?: Sample[]; meta?: any } {
  const parsed: any = parseReplayFile(readFileSync(file, 'utf8'))
  if (parsed.error || !parsed.replay) return { ok: false, detail: `parse 失败` }
  const rp = parsed.replay
  const md = rp.metadata ?? {}
  const rpSeed: number = rp.seed
  const total: number = rp.totalTicks ?? 0
  if (!total || !Number.isInteger(rpSeed)) return { ok: false, detail: 'totalTicks/seed 缺失' }
  // 通关门（wins-only 恒开）：verdict 有则认 band，无则认 metadata kills。
  // seed 取顶层 replay.seed（metadata 里没有 seed）。
  if (verdicts) {
    const v = verdicts.get(`${md.stage}/${rpSeed}`)
    if (!v || v.best?.band !== 'solvable' || (v.kills ?? 0) < minKills) {
      return { ok: false, detail: `verdict 未达标（band/kills），跳过` }
    }
  } else if ((md.killCount ?? 0) < minKills) {
    return { ok: false, detail: `metadata kills=${md.killCount} < ${minKills}，跳过` }
  }
  // 布关 = diverge-resim 已验证路径（14/14 保真）：预置难度/规则/关卡 → restore → 输入替换。
  const world = new World()
  world.rng.reseed(rp.seed)
  const dkey = md.difficulty || 'classic'
  world.difficultyKey = dkey
  world.difficulty = (DIFFICULTIES as any)[dkey] ?? (DIFFICULTIES as any)['classic']
  world.rules = (RULES as any)[dkey] ?? DEFAULT_RULES
  world.loadStageData((STAGES as any[])[md.stage] ?? (STAGES as any[])[0], 0)
  restoreWorld(world, rp.initialSnapshot)
  const input = new ReplayInput(rp.frames)
  const sim = new Simulation(world, input as any)
  sim.input = input as any
  sim.input2 = (input as any).input2 ?? null
  world.state = 'playing'

  const encoder = new ObsEncoder()
  const samples: Sample[] = []
  let prevDir: any = null
  let prevGuard = false
  let prevFrenzy = false
  // tickHashes 全对账（verify-replay 同契约）：phase = 完成 tick 数。
  const recorded: string[] = rp.tickHashes ?? []
  const interval: number = rp.hashInterval ?? 100
  let hashIdx = 0
  let tick = 0
  while (tick < total) {
    // obs(t)：本 tick 输入消费**之前**的世界状态（与 godai 同采样点，plan §1.3）。
    encoder.encode(world)
    const dir = (input as any).getMoveDirection ? (input as any).getMoveDirection() : null
    const firing = (input as any).isFiring ? !!(input as any).isFiring() : false
    const guard = (input as any).wasItemPressed ? !!(input as any).wasItemPressed('guard') : false
    const frenzy = (input as any).wasItemPressed ? !!(input as any).wasItemPressed('frenzy') : false
    const { isDecision, condition } = decisionTick(
      tick,
      world,
      prevDir,
      dir,
      prevGuard,
      guard,
      prevFrenzy,
      frenzy,
      K,
    )
    prevDir = dir
    prevGuard = guard
    prevFrenzy = frenzy
    if (isDecision) {
      const label = actionFromFrame({ direction: dir, firing })
      const masks = computeMasks(world)
      const ringFrac = encoder.scalars[6]
      const basePressed =
        !!world.tileMap.getBasePos() &&
        (ringFrac < 1 ||
          world.tanks.some(
            (e: any) =>
              e.alive &&
              e.spawnTimer <= 0 &&
              e.allegiance === 'enemy' &&
              Math.abs(Math.floor((e.x + 16) / CELL) - BASE_POS.col) +
                Math.abs(Math.floor((e.y + 16) / CELL) - BASE_POS.row) <=
                BASE_PRESSURE_RADIUS,
          ))
      samples.push({
        obs: encoder.obs.slice(),
        scalars: encoder.scalars.slice(),
        move: label.move,
        fire: label.fire,
        masks: [...masks.move, ...masks.fire],
        nearMiss: basePressed,
        cond: condition,
      })
    }
    sim.tick()
    if (typeof (input as any).advance === 'function') (input as any).advance()
    tick++
    if (typeof (world as any).consumeEvents === 'function') (world as any).consumeEvents()
    if (hashIdx < recorded.length && tick % interval === 0) {
      if (worldTickHash(world) !== recorded[hashIdx]) {
        return { ok: false, detail: `fidelity-skip：tick ${tick} hash 失配` }
      }
      hashIdx++
    }
  }
  if ((world.killCount ?? -1) !== (md.killCount ?? -2)) {
    return {
      ok: false,
      detail: `fidelity-skip：kills ${world.killCount} vs 元数据 ${md.killCount}`,
    }
  }
  return {
    ok: true,
    detail: `${samples.length} samples`,
    samples,
    meta: { ...md, seed: rpSeed, totalTicks: total },
  }
}

function main(): void {
  const replayInputs: string[] = []
  let outDir = 'tmp/human-shards'
  let verdictsPath = ''
  let teacher = 'human'
  let session = ''
  let minKills = 20
  const argv = process.argv.slice(2)
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--replays') replayInputs.push(argv[++i])
    else if (argv[i] === '--out') outDir = argv[++i]
    else if (argv[i] === '--verdicts') verdictsPath = argv[++i]
    else if (argv[i] === '--teacher-label') teacher = argv[++i]
    else if (argv[i] === '--session') session = argv[++i]
    else if (argv[i] === '--min-kills') minKills = parseInt(argv[++i], 10)
    else
      fail(
        `未知参数 ${argv[i]}（允许：--replays/--out/--verdicts/--teacher-label/--session/--min-kills）`,
      )
  }
  if (!replayInputs.length) fail('缺少 --replays')
  const files = collectReplayFiles(replayInputs)
  if (!files.length) fail('没有 .replay 文件')
  const verdicts = verdictsPath ? loadVerdicts(verdictsPath) : null
  mkdirSync(outDir, { recursive: true })
  let kept = 0
  let skipped = 0
  let totalSamples = 0
  const usedNames = new Set<string>()
  for (const f of files) {
    const r = convertOne(f, verdicts, minKills)
    if (!r.ok || !r.samples || !r.meta) {
      console.log(`[SKIP] ${basename(f)}: ${r.detail}`)
      skipped++
      continue
    }
    const md = r.meta
    let name = `shard_h_s${md.stage}_seed${md.seed}`
    if (usedNames.has(name)) {
      let k = 2
      while (usedNames.has(`${name}_${k}`)) k++
      name = `${name}_${k}`
    }
    usedNames.add(name)
    const dir = join(outDir, name)
    mkdirSync(dir, { recursive: true })
    const N = r.samples.length
    const obs = new Uint8Array(N * OBS_N)
    const scalars = new Float32Array(N * SCALAR_DIM)
    const actions = new Uint8Array(N * 2)
    const masks = new Uint8Array(N * MASK_DIM)
    const conditions = new Uint8Array(N)
    r.samples.forEach((s, i) => {
      obs.set(s.obs, i * OBS_N)
      scalars.set(s.scalars, i * SCALAR_DIM)
      actions[i * 2] = s.move
      actions[i * 2 + 1] = s.fire
      for (let j = 0; j < MASK_DIM; j++) masks[i * MASK_DIM + j] = s.masks[j]
      conditions[i] = s.cond
    })
    writeNpy(`${dir}/obs.npy`, obs, [N, OBS_CHANNELS, BOARD, BOARD], 'u1')
    writeNpy(`${dir}/scalars.npy`, scalars, [N, SCALAR_DIM], 'f4')
    writeNpy(`${dir}/actions.npy`, actions, [N, 2], 'u1')
    writeNpy(`${dir}/masks.npy`, masks, [N, MASK_DIM], 'u1')
    writeNpy(`${dir}/conditions.npy`, conditions, [N], 'u1')
    const manifest = {
      schemaMajor: OBS_SCHEMA_MAJOR,
      obsSchemaMajor: OBS_SCHEMA_MAJOR,
      schemaFingerprint: SCHEMA_FINGERPRINT,
      exporterVersion: EXPORTER_VERSION,
      shard: name,
      teacher,
      nSamples: N,
      stage: md.stage,
      seed: md.seed,
      difficulty: md.difficulty ?? null,
      lives: md.lives ?? null,
      kills: md.killCount ?? null,
      ticks: md.totalTicks ?? r.meta.totalTicks ?? null,
      session: session || null,
      nearMissFrames: r.samples.filter((s) => s.nearMiss).length,
    }
    writeFileSync(`${dir}/manifest.json`, JSON.stringify(manifest, null, 2))
    kept++
    totalSamples += N
    console.log(`[OK] ${basename(f)} -> ${name}: ${N} samples`)
  }
  console.log(`\n=== export summary ===`)
  console.log(
    `files=${files.length} kept=${kept} skipped=${skipped} samples=${totalSamples} out=${outDir}`,
  )
  writeFileSync(
    `${outDir}/_export_report.json`,
    JSON.stringify({ files: files.length, kept, skipped, totalSamples, teacher, session }, null, 2),
  )
}

main()
