/**
 * verify-replay-corpus.ts — P0 identity harness（plan/replay-corpus.plan.md §8 P0）。
 *
 * 逐局跑 full 与 replay 两条导出路径，然后：
 *   ① Bun 进程内转换 replay → obs/scalars/extra，与 full 逐字节对账；
 *   ② 可选：同一批 shard 交给 Node bundle 重转（Bun 录制 → Node 重建）；
 *   ③ 可选：交给 WSL Linux 的 Node 重转（Windows 录制 → Linux 重建）。
 *
 * 用法：
 *   bun tools/sim/verify-replay-corpus.ts --games 1015 --max-ticks 1200
 *   bun tools/sim/verify-replay-corpus.ts --games 50 --runtimes node,wsl --keep
 *
 * 说明：
 *   - 默认 35 关 × ceil(games/35) seeds，seed 从 2000000001 起；`hard`。
 *   - 只写 replay shard（标签 + replay 两成员），full 的 obs 三张只在内存里算 sha 后丢弃，
 *     因此 1000 局不会留下 GB 级临时文件。
 *   - WSL 侧要求 `$HOME/.local/node/bin/node`（可用 `--wsl-node` 覆盖）。
 */

import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { join, resolve } from 'path'
import { STAGES } from '../../src/config/stages'
import { runOneBench, shardNpyEntries, writeRlShard } from './export-rl-rollout'
import { convertReplayShard } from './replay-to-shard'

const OBS_FILES = ['obs.npy', 'scalars.npy', 'extra.npy'] as const

interface RefRow {
  index: number
  stage: number
  seed: number
  shardDir: string
  nSamples: number
  obs: string
  scalars: string
  extra: string
}

function arg(name: string, fallback: string): string {
  const i = process.argv.indexOf(name)
  return i >= 0 ? (process.argv[i + 1] ?? fallback) : fallback
}

function sha(data: Uint8Array): string {
  return createHash('sha256').update(data).digest('hex')
}

function entriesMap(entries: Array<{ name: string; data: Uint8Array }>): Map<string, Uint8Array> {
  return new Map(entries.map((e) => [e.name, e.data]))
}

function toWslPath(p: string): string {
  const abs = resolve(p)
  return '/mnt/' + abs[0].toLowerCase() + abs.slice(2).replace(/\\/g, '/')
}

const games = Math.max(1, Number(arg('--games', '1015')))
const maxTicks = Math.max(50, Number(arg('--max-ticks', '1200')))
const weightsPath = resolve(
  arg('--weights', 'nn-training/weights/g4-mobility/g4-mobility.it380.20261114-194719.json'),
)
const work = resolve(arg('--work', 'tmp/replay-identity'))
const runtimes = new Set(
  arg('--runtimes', 'node,wsl')
    .split(',')
    .map((s) => s.trim()),
)
const keep = process.argv.includes('--keep')
const wslNode = arg('--wsl-node', '$HOME/.local/node/bin/node')

if (!existsSync(weightsPath)) throw new Error(`weights not found: ${weightsPath}`)
const weightsText = readFileSync(weightsPath, 'utf8')

if (existsSync(work)) rmSync(work, { recursive: true, force: true })
const shardsRoot = join(work, 'shards')
mkdirSync(shardsRoot, { recursive: true })
const refPath = join(work, 'ref.jsonl')
const refs: RefRow[] = []

const t0 = Date.now()
let fullMs = 0
let replayMs = 0
let convMs = 0
let recordMsTotal = 0
let sampleTotal = 0
console.log(
  `[verify] games=${games} maxTicks=${maxTicks} stages=${STAGES.length} work=${work} runtimes=${[...runtimes].join(',')}`,
)

for (let i = 0; i < games; i++) {
  const stage = i % STAGES.length
  const seed = 2000000001 + Math.floor(i / STAGES.length)
  const stageObj = STAGES[stage]
  const tf = performance.now()
  const full = runOneBench(stage, stageObj, seed, 'hard', maxTicks, weightsText)
  fullMs += performance.now() - tf
  const tr = performance.now()
  const replay = runOneBench(
    stage,
    stageObj,
    seed,
    'hard',
    maxTicks,
    weightsText,
    'off',
    false,
    null,
    null,
    null,
    false,
    'replay',
  )
  replayMs += performance.now() - tr
  if (!replay.replay) throw new Error(`stage ${stage} seed ${seed}: replay artifact missing`)
  recordMsTotal += Number(replay.replay.meta.recordMs ?? 0)
  sampleTotal += full.shard.n

  const fullEntries = entriesMap(shardNpyEntries(full.shard, 'full'))
  const replayEntries = entriesMap(shardNpyEntries(replay.shard, 'replay'))
  // 标签必须与 full 逐字节相同（replay 只改传输形态，不改语料语义）。
  for (const [name, data] of replayEntries) {
    const ref = fullEntries.get(name)
    if (!ref) throw new Error(`${stage}/${seed}: full missing label ${name}`)
    if (Buffer.compare(Buffer.from(ref), Buffer.from(data)) !== 0) {
      throw new Error(`${stage}/${seed}: label ${name} differs between full and replay`)
    }
  }

  const shardDir = join(shardsRoot, `s${stage}_seed${seed}`)
  mkdirSync(shardDir, { recursive: true })
  writeRlShard(shardDir, replay.shard, { stage, seed }, replay.replay, 'replay')

  const expected = {
    obs: sha(fullEntries.get('obs.npy')!),
    scalars: sha(fullEntries.get('scalars.npy')!),
    extra: sha(fullEntries.get('extra.npy')!),
  }
  // Bun 进程内转换 = 同运行时 identity 主判。
  const conv = join(work, 'conv', `s${stage}_seed${seed}`)
  const tc = performance.now()
  convertReplayShard({ shardDir, outDir: conv })
  convMs += performance.now() - tc
  for (const name of OBS_FILES) {
    const got = sha(readFileSync(join(conv, name)))
    const want = expected[name.replace('.npy', '') as 'obs' | 'scalars' | 'extra']
    if (got !== want) throw new Error(`${stage}/${seed}: Bun conversion ${name} mismatch`)
  }
  rmSync(conv, { recursive: true, force: true })

  const row: RefRow = {
    index: i,
    stage,
    seed,
    shardDir,
    nSamples: full.shard.n,
    ...expected,
  }
  refs.push(row)
  writeFileSync(refPath, JSON.stringify(row) + '\n', { flag: 'a' })
  if ((i + 1) % 25 === 0 || i + 1 === games) {
    const sec = (Date.now() - t0) / 1000
    const rate = (i + 1) / sec
    const eta = (games - i - 1) / Math.max(0.001, rate)
    console.log(
      `[verify] ${i + 1}/${games} samples=${row.nSamples} elapsed=${sec.toFixed(1)}s ` +
        `rate=${rate.toFixed(2)}/s eta=${eta.toFixed(0)}s`,
    )
  }
}

writeFileSync(join(work, 'shard-list.txt'), refs.map((r) => r.shardDir).join('\n') + '\n')

function checkRuntime(label: string): number {
  let bad = 0
  for (const r of refs) {
    for (const name of OBS_FILES) {
      const got = sha(readFileSync(join(r.shardDir, name)))
      const want = r[name.replace('.npy', '') as 'obs' | 'scalars' | 'extra']
      if (got !== want) {
        bad++
        console.error(`[${label}] MISMATCH s${r.stage}/seed${r.seed} ${name}`)
        break
      }
    }
  }
  console.log(`[${label}] shards=${refs.length} mismatches=${bad}`)
  return bad
}

function buildBundle(): string {
  const out = join(work, 'replay-to-shard.node.js')
  const p = Bun.spawnSync(
    [process.execPath, 'build', 'tools/sim/replay-to-shard.ts', '--target=node', '--outfile', out],
    { cwd: resolve('.'), stdout: 'pipe', stderr: 'pipe', windowsHide: true },
  )
  if (p.exitCode !== 0) throw new Error(`bun build failed: ${p.stderr?.toString() ?? ''}`)
  return out
}

let bad = 0
const list = join(work, 'shard-list.txt')
if (runtimes.has('node')) {
  const bundle = buildBundle()
  console.log('[verify] Node conversion start')
  const p = Bun.spawnSync(['node', bundle, '--shard-list', list], {
    cwd: resolve('.'),
    stdout: 'pipe',
    stderr: 'pipe',
    windowsHide: true,
  })
  if (p.exitCode !== 0) {
    console.error(p.stderr?.toString() ?? '')
    throw new Error(`Node conversion failed rc=${p.exitCode}`)
  }
  console.log(p.stdout.toString().trim())
  bad += checkRuntime('node')
}
if (runtimes.has('wsl')) {
  const bundle = buildBundle()
  const wslList = join(work, 'shard-list.wsl.txt')
  writeFileSync(wslList, refs.map((r) => toWslPath(r.shardDir)).join('\n') + '\n')
  const cmd = `${wslNode} ${toWslPath(bundle)} --shard-list ${toWslPath(wslList)}`
  console.log('[verify] WSL conversion start')
  const p = Bun.spawnSync(['wsl.exe', '-e', 'bash', '-lc', cmd], {
    stdout: 'pipe',
    stderr: 'pipe',
    windowsHide: true,
  })
  if (p.exitCode !== 0) {
    console.error(p.stderr?.toString() ?? '')
    console.error(p.stdout?.toString() ?? '')
    throw new Error(`WSL conversion failed rc=${p.exitCode}（WSL 需 $HOME/.local/node/bin/node）`)
  }
  console.log(p.stdout.toString().trim())
  bad += checkRuntime('wsl')
}

console.log(
  `[verify] DONE games=${games} samples=${sampleTotal} total=${((Date.now() - t0) / 1000).toFixed(1)}s ` +
    `fullMs=${fullMs.toFixed(0)} replayMs=${replayMs.toFixed(0)} convMs=${convMs.toFixed(0)} ` +
    `recordMs=${recordMsTotal.toFixed(0)} mismatches=${bad} work=${work}`,
)
if (bad > 0) process.exitCode = 1
else if (!keep) rmSync(work, { recursive: true, force: true })
