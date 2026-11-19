/**
 * replay-to-shard.bench.ts — replay → obs/scalars/extra 重建的**性能基准**（plan/replay-corpus.plan.md §7.3）。
 *
 * 只做两件事，都为了回答「训练墙钟花在 rebuild 的哪一段」：
 *   1. `--gen <n>`：用真实导出器造 n 个 replay shard（标签 + replay 两成员，无 obs 三张）。
 *      一局只造一次，之后反复 `--bench` 复用（profile 的输入必须稳定）。
 *   2. 缺省：对 `--work` 里的 shard 逐个跑 `convertReplayShard`，报
 *      samples / ticks / convertMs / µs-per-tick / µs-per-sample。
 *
 * 归因用 Bun 自带 profiler（函数级采样），**不改生产代码**：
 *   bun --cpu-prof --cpu-prof-md --cpu-prof-dir tmp/prof \
 *     tools/sim/replay-to-shard.ts --shard-list tmp/replay-bench/shard-list.txt
 *
 * 用法：
 *   bun tools/sim/replay-to-shard.bench.ts --gen 6 --max-ticks 3000
 *   bun tools/sim/replay-to-shard.bench.ts --rounds 3
 */

import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { STAGES } from '../../src/config/stages'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { ObsEncoder, OBS_CHANNELS, BOARD, SCALAR_DIM } from '../../src/nn/obs-encoder'
import { POLICY_EXTRA_DIM } from '../../src/nn/policy-extra'
import { npyBytes } from '../../src/nn/npy'
import { parseReplayFile } from '../../src/replay/file'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { worldTickHash } from '../../src/replay/tickHash'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { runOneBench, writeRlShard } from './export-rl-rollout'
import { convertReplayShard } from './replay-to-shard'

function arg(name: string, fallback: string): string {
  const i = process.argv.indexOf(name)
  return i >= 0 ? (process.argv[i + 1] ?? fallback) : fallback
}

const work = resolve(arg('--work', 'tmp/replay-bench'))
const shardsRoot = join(work, 'shards')
const maxTicks = Math.max(50, Number(arg('--max-ticks', '3000')))
const weightsPath = resolve(
  arg('--weights', 'nn-training/weights/g4-mobility/g4-mobility.it380.20261114-194719.json'),
)

function generate(n: number): void {
  if (existsSync(shardsRoot)) rmSync(shardsRoot, { recursive: true, force: true })
  mkdirSync(shardsRoot, { recursive: true })
  const weightsText = readFileSync(weightsPath, 'utf8')
  const dirs: string[] = []
  for (let i = 0; i < n; i++) {
    const stage = i % STAGES.length
    const seed = 2000000001 + Math.floor(i / STAGES.length)
    const t0 = performance.now()
    const res = runOneBench(
      stage,
      STAGES[stage],
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
    if (!res.replay) throw new Error(`replay artifact missing for stage ${stage}`)
    const dir = join(shardsRoot, `s${stage}_seed${seed}`)
    writeRlShard(dir, res.shard, { stage, seed }, res.replay, 'replay')
    dirs.push(dir)
    console.log(
      `[gen] s${stage}/seed${seed} samples=${res.shard.n} ticks=${res.replay.meta.ticks} ` +
        `wall=${(performance.now() - t0).toFixed(0)}ms`,
    )
  }
  writeFileSync(join(work, 'shard-list.txt'), dirs.join('\n') + '\n')
}

/**
 * `--write`：走**生产形态**（写 obs/scalars/extra，含原子落盘）。不加时只重建+校验。
 * 落盘那条腿的值只在写模式下看得到（35×20 的 3.97 GB 全在这）。
 */
function bench(rounds: number, withWrite = false): void {
  const list = readFileSync(join(work, 'shard-list.txt'), 'utf8')
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (list.length === 0) throw new Error(`no shards under ${work} — run --gen first`)
  const outRoot = join(work, 'bench-out')
  for (let r = 0; r < rounds; r++) {
    let samples = 0
    let ticks = 0
    let ms = 0
    for (let k = 0; k < list.length; k++) {
      const got = convertReplayShard({
        shardDir: list[k],
        verifyOnly: !withWrite,
        outDir: withWrite ? join(outRoot, `s${k}`) : undefined,
      })
      samples += got.nSamples
      ticks += got.ticks
      ms += got.convertMs
    }
    console.log(
      `[bench${withWrite ? '/write' : ''}] round=${r} shards=${list.length} samples=${samples} ` +
        `ticks=${ticks} ms=${ms.toFixed(0)} µs/tick=${((ms * 1000) / ticks).toFixed(2)} ` +
        `µs/sample=${((ms * 1000) / samples).toFixed(1)}`,
    )
  }
}

/**
 * 消融归因（ablation）：把 `convertReplayShard` 的循环按段拆开，**同一批 shard、同一顺序**，
 * 每段单独计时。不依赖 profiler 采样（Bun 的采样在本负载上密度不足，49 samples/339ms）。
 *
 * 段：parse → setup → tick → +encode → +tickHash → +npy → +write。
 * 每段的测法都是「把上一段的结果再做多一件事」，差值即该段成本。
 * `--ablate N` = 跑 N 轮取每段最小值（首次跑有 JIT/OS 噪声）。
 */
function ablate(rounds: number): void {
  const list = readFileSync(join(work, 'shard-list.txt'), 'utf8')
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean)
  const outDir = join(work, 'ablate-out')
  const phases = ['parse', 'setup', 'tick', 'encode', 'hash', 'npy', 'write'] as const
  const best: Record<string, number> = {}
  let ticksTotal = 0
  let samplesTotal = 0
  for (let r = 0; r < rounds; r++) {
    const roundTotals: Record<string, number> = {
      parse: 0,
      setup: 0,
      tick: 0,
      encode: 0,
      hash: 0,
      npy: 0,
      write: 0,
    }
    for (const dir of list) {
      const T: Record<string, number> = {
        parse: 0,
        setup: 0,
        tick: 0,
        encode: 0,
        hash: 0,
        npy: 0,
        write: 0,
      }
      let mark = performance.now()
      const markAt = (k: string): void => {
        const now = performance.now()
        T[k] += now - mark
        mark = now
      }
      const parsed = parseReplayFile(readFileSync(`${dir}/replay.json`, 'utf8'))
      if ('error' in parsed) throw new Error(parsed.error)
      const replay = parsed.replay
      const meta = JSON.parse(readFileSync(`${dir}/replay_meta.json`, 'utf8')) as {
        stage: number
        difficulty: string
        nSamples: number
        decisionTicksList?: number[]
      }
      markAt('parse')

      const world = new World()
      world.rng.reseed(replay.seed)
      const dkey = meta.difficulty || 'hard'
      world.difficultyKey = dkey
      world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
      world.rules = RULES[dkey] ?? DEFAULT_RULES
      world.loadStageData(STAGES[meta.stage] ?? STAGES[0], 0)
      restoreWorld(world, replay.initialSnapshot)
      world.state = 'playing'
      const input = new ReplayInput(replay.frames)
      const sim = new Simulation(world, input)
      const encoder = new ObsEncoder()
      const N = meta.nSamples
      const ticks = meta.decisionTicksList ?? []
      const obsFlat = new Uint8Array(N * OBS_CHANNELS * BOARD * BOARD)
      const scalarsFlat = new Float32Array(N * SCALAR_DIM)
      const extraFlat = new Float32Array(POLICY_EXTRA_DIM * N)
      markAt('setup')

      // tick only
      let i = 0
      const total = replay.totalTicks
      for (let t = 0; t < total; t++) {
        sim.tick()
        input.advance()
        world.consumeEvents()
      }
      markAt('tick')

      // + encode（用第二份 World，避免把上面已跑完的世界当成输入）
      const w2 = new World()
      w2.rng.reseed(replay.seed)
      w2.difficultyKey = dkey
      w2.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
      w2.rules = RULES[dkey] ?? DEFAULT_RULES
      w2.loadStageData(STAGES[meta.stage] ?? STAGES[0], 0)
      restoreWorld(w2, replay.initialSnapshot)
      w2.state = 'playing'
      const in2 = new ReplayInput(replay.frames)
      const sim2 = new Simulation(w2, in2)
      const obsStride = OBS_CHANNELS * BOARD * BOARD
      let di = 0
      let dt = 0
      for (let t = 0; t < total; t++) {
        if (dt < ticks.length && ticks[dt] === t) {
          encoder.encode(w2)
          obsFlat.set(encoder.obs, di * obsStride)
          scalarsFlat.set(encoder.scalars, di * SCALAR_DIM)
          extraFlat.set(encoder.extra, di * POLICY_EXTRA_DIM)
          di++
          dt++
        }
        sim2.tick()
        in2.advance()
        w2.consumeEvents()
      }
      markAt('encode')

      // + tickHash（每 100 tick；复用已跑完的 w2 不可行 —— 用第三份世界）
      const w3 = new World()
      w3.rng.reseed(replay.seed)
      w3.difficultyKey = dkey
      w3.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
      w3.rules = RULES[dkey] ?? DEFAULT_RULES
      w3.loadStageData(STAGES[meta.stage] ?? STAGES[0], 0)
      restoreWorld(w3, replay.initialSnapshot)
      w3.state = 'playing'
      const in3 = new ReplayInput(replay.frames)
      const sim3 = new Simulation(w3, in3)
      const hashes: string[] = []
      let di3 = 0
      let dt3 = 0
      for (let t = 0; t < total; t++) {
        if (dt3 < ticks.length && ticks[dt3] === t) {
          encoder.encode(w3)
          obsFlat.set(encoder.obs, di3 * obsStride)
          scalarsFlat.set(encoder.scalars, di3 * SCALAR_DIM)
          extraFlat.set(encoder.extra, di3 * POLICY_EXTRA_DIM)
          di3++
          dt3++
        }
        sim3.tick()
        in3.advance()
        w3.consumeEvents()
        if ((t + 1) % 100 === 0) hashes.push(worldTickHash(w3))
      }
      markAt('hash')

      const hasher = createHash('sha256')
      for (let k = 0; k < N; k++)
        hasher.update(obsFlat.subarray(k * obsStride, (k + 1) * obsStride))
      hasher.digest('hex')
      markAt('npy')
      const blobs = [
        npyBytes(obsFlat, [N, OBS_CHANNELS, BOARD, BOARD], 'u1'),
        npyBytes(scalarsFlat, [N, SCALAR_DIM], 'f4'),
        npyBytes(extraFlat, [N, POLICY_EXTRA_DIM], 'f4'),
      ]
      mkdirSync(outDir, { recursive: true })
      writeFileSync(`${outDir}/obs.npy`, blobs[0])
      writeFileSync(`${outDir}/scalars.npy`, blobs[1])
      writeFileSync(`${outDir}/extra.npy`, blobs[2])
      markAt('write')

      ticksTotal += total
      samplesTotal += N
      for (const p of phases) roundTotals[p] += T[p]
      void i
    }
    for (const p of phases) best[p] = Math.min(best[p] ?? Infinity, roundTotals[p])
  }
  const ticksPerRound = ticksTotal / rounds
  const samplesPerRound = samplesTotal / rounds
  console.log(
    `[ablate] shards=${list.length} ticks=${ticksPerRound} samples=${samplesPerRound} ` +
      `×${rounds} rounds（每轮全量取最小）`,
  )
  // 累计量（tick→encode→hash 都是「再跑一遍完整循环 + 多一件事」），差值才是该段成本。
  const cum: Record<string, number> = {
    parse: best.parse,
    setup: best.parse + best.setup,
    tick: best.parse + best.setup + best.tick,
    encode: best.parse + best.setup + best.encode,
    hash: best.parse + best.setup + best.hash,
  }
  const row = (label: string, ms: number, prev?: number): void => {
    const d = prev === undefined ? ms : ms - prev
    console.log(
      `[ablate] ${label.padEnd(18)} ${d.toFixed(2).padStart(8)}ms ` +
        `${((d * 1000) / ticksPerRound).toFixed(2).padStart(6)} µs/tick ` +
        `${((d * 1000) / samplesPerRound).toFixed(1).padStart(7)} µs/sample`,
    )
  }
  row('parse', best.parse)
  row('setup', cum.setup, cum.parse)
  row('tick loop', best.tick)
  row('+ encode', best.encode, best.tick)
  row('+ tickHash(100)', best.hash, best.encode)
  row('sha256 obsHash', best.npy)
  row('npyBytes+write', best.write)
  const heavy = convertReplayShard({
    shardDir: list[0],
    verifyOnly: false,
    outDir: join(outDir, 'one'),
  })
  console.log(
    `[ablate] full convert (含校验+写盘) shard0: ${heavy.convertMs}ms ` +
      `(${((heavy.convertMs * 1000) / heavy.ticks).toFixed(2)} µs/tick)`,
  )
}

/**
 * 单函数微基准：同一个**静止**世界（跑到 40% tick 处）上反复调 `encode` / `worldTickHash`。
 *
 * 为什么需要它：端到端 ablation 的逐段差值是垃圾 —— 每段都把同一个循环再跑一遍，
 * 第二遍永远被 JIT 加了速（实测出现 encode 段 < tick 段）。静止世界只重复测被测函数本身，
 * 且 A/B 两棵树的输入完全一致。
 */
function micro(rounds: number): void {
  const list = readFileSync(join(work, 'shard-list.txt'), 'utf8')
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean)
  const dir = list[0]
  const parsed = parseReplayFile(readFileSync(`${dir}/replay.json`, 'utf8'))
  if ('error' in parsed) throw new Error(parsed.error)
  const replay = parsed.replay
  const meta = JSON.parse(readFileSync(`${dir}/replay_meta.json`, 'utf8')) as {
    stage: number
    difficulty: string
  }
  const world = new World()
  world.rng.reseed(replay.seed)
  const dkey = meta.difficulty || 'hard'
  world.difficultyKey = dkey
  world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
  world.rules = RULES[dkey] ?? DEFAULT_RULES
  world.loadStageData(STAGES[meta.stage] ?? STAGES[0], 0)
  restoreWorld(world, replay.initialSnapshot)
  world.state = 'playing'
  const input = new ReplayInput(replay.frames)
  const sim = new Simulation(world, input)
  const stop = Math.floor(replay.totalTicks * 0.4)
  for (let t = 0; t < stop; t++) {
    sim.tick()
    input.advance()
    world.consumeEvents()
  }
  console.log(
    `[micro] shard=${dir} ticks=${replay.totalTicks} at tick ${stop} ` +
      `enemies=${world.tanks.length} bullets=${world.bullets.length}`,
  )
  const encoder = new ObsEncoder()
  const N = 3000
  for (let i = 0; i < 200; i++) encoder.encode(world)
  let best = Infinity
  for (let r = 0; r < rounds; r++) {
    const t0 = performance.now()
    for (let i = 0; i < N; i++) encoder.encode(world)
    best = Math.min(best, performance.now() - t0)
  }
  console.log(`[micro] ObsEncoder.encode      ${((best * 1000) / N).toFixed(2)} µs/op`)
  for (let i = 0; i < 200; i++) worldTickHash(world)
  best = Infinity
  for (let r = 0; r < rounds; r++) {
    const t0 = performance.now()
    for (let i = 0; i < N; i++) worldTickHash(world)
    best = Math.min(best, performance.now() - t0)
  }
  console.log(`[micro] worldTickHash           ${((best * 1000) / N).toFixed(2)} µs/op`)
}

if (process.argv.includes('--gen')) {
  generate(Math.max(1, Number(arg('--gen', '4'))))
} else if (process.argv.includes('--micro')) {
  micro(Math.max(1, Number(arg('--micro', '5'))))
} else if (process.argv.includes('--ablate')) {
  ablate(Math.max(1, Number(arg('--ablate', '1'))))
} else {
  bench(Math.max(1, Number(arg('--rounds', '3'))), process.argv.includes('--write'))
}
