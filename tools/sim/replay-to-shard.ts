/**
 * replay-to-shard.ts — replay.json → obs/scalars/extra 重建器（worker pre-pass 的唯一实现）。
 *
 * 契约（plan/replay-corpus.plan.md D3/D4）：
 * - **只** import World/Simulation/ObsEncoder/ReplayInput/parseReplayFile/restoreWorld/npy；
 *   不得 import export-rl-rollout（会拖入模型/native 依赖）。
 * - 世界只由 replay frames 驱动；**不重跑 OpeningController/A***，也不用 `a_*` 标签驱动。
 * - 决策 tick 由 `replay_meta.decisionTicksList` 给（缺省 = 均匀 `t % k === 0`）；
 *   编码时点 = 决策 tick 的 **pre-tick** 世界状态。
 * - 确定性校验（零容差）：nSamples / decisionTicks / obsHash / tickHash 链 / 终局状态。
 *
 * 用法：
 *   bun tools/sim/replay-to-shard.ts --shard-dir <dir> [--out <dir>] [--verify-only]
 *
 * 退出码 0 = 重建且全部校验通过；1 = 任一确定性校验失败（不写半成品）。
 */

import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync } from 'fs'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { STAGES } from '../../src/config/stages'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { ObsEncoder, OBS_CHANNELS, BOARD, SCALAR_DIM } from '../../src/nn/obs-encoder'
import { POLICY_EXTRA_DIM } from '../../src/nn/policy-extra'
import { writeNpyFile } from '../../src/nn/npy'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { parseReplayFile } from '../../src/replay/file'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { runServe } from './serve-loop'
import { worldTickHash } from '../../src/replay/tickHash'
import type { GameState } from '../../src/types'

/** `replay_meta.json` 的消费面（只列转换器用到的字段；其余原样忽略）。 */
export interface ReplayMeta {
  version: number
  corpus_fmt?: string
  stage: number
  seed: number
  difficulty: string
  k: number
  ticks: number
  decisionTicks: number
  decisionTicksList?: number[]
  nSamples: number
  obsHash: string | null
  tickHashes?: number
  recordMs?: number
  bytes?: number
  [key: string]: unknown
}

export interface ConvertOptions {
  shardDir: string
  /** 输出目录；缺省 = 原地（worker pre-pass 的形态）。 */
  outDir?: string
  /** 只重建 + 校验，不写文件。 */
  verifyOnly?: boolean
}

export interface ConvertResult {
  nSamples: number
  ticks: number
  obsHash: string
  /** 重建 + 校验的墙钟毫秒。 */
  convertMs: number
  /** 写了哪些文件（verifyOnly 时为空）。 */
  written: string[]
}

/** 终局状态 → replay 的 `sim.status` 口径（与 export-rl-rollout 的 replayStatus 同源）。 */
function statusOf(world: World): 'clear' | 'base' | 'died' | 'timeout' {
  const state: GameState = world.state
  if (state === 'stageclear' || state === 'victory') return 'clear'
  if (state === 'gameover') return world.tileMap.isBaseDestroyed() ? 'base' : 'died'
  return 'timeout'
}

function isTerminal(state: GameState): boolean {
  return state === 'stageclear' || state === 'victory' || state === 'gameover'
}

/** 最小 npy 头解析：只要行数（第一个 shape 维度）。 */
function npyRowCount(path: string): number | null {
  const buf = readFileSync(path)
  if (buf.length < 10) return null
  const major = buf[6]
  const headerLen =
    major === 1
      ? buf[8] | (buf[9] << 8)
      : buf[8] | (buf[9] << 8) | (buf[10] << 16) | (buf[11] << 24)
  const start = major === 1 ? 10 : 12
  const header = buf.toString('latin1', start, start + headerLen)
  const m = /'shape'\s*:\s*\(\s*(\d+)/.exec(header)
  return m ? parseInt(m[1], 10) : null
}

/** `.tmp` + rename 原子落盘，但**不**先 `npyBytes` 整块拷贝（obs 是 3.97 GB 量级）。 */
function writeNpyAtomic(
  path: string,
  view: ArrayBufferView,
  shape: number[],
  dtype: 'u1' | 'f4',
): void {
  const tmp = `${path}.tmp`
  writeNpyFile(tmp, view, shape, dtype)
  try {
    renameSync(tmp, path)
  } catch (e) {
    try {
      unlinkSync(tmp)
    } catch {
      /* 同上 */
    }
    throw e
  }
}

/**
 * 重建一个 shard 的 obs/scalars/extra 并做确定性校验。
 * 任一校验失败抛错（worker 侧据此把 shard 判为确定性失败，**不得**重试掩盖）。
 */
export function convertReplayShard(opts: ConvertOptions): ConvertResult {
  const t0 = performance.now()
  const shardDir = opts.shardDir
  const replayPath = `${shardDir}/replay.json`
  const metaPath = `${shardDir}/replay_meta.json`
  if (!existsSync(replayPath)) throw new Error(`[replay-to-shard] missing ${replayPath}`)
  if (!existsSync(metaPath)) throw new Error(`[replay-to-shard] missing ${metaPath}`)

  const parsed = parseReplayFile(readFileSync(replayPath, 'utf8'))
  if ('error' in parsed) throw new Error(`[replay-to-shard] replay parse failed: ${parsed.error}`)
  const replay = parsed.replay
  const meta = JSON.parse(readFileSync(metaPath, 'utf8')) as ReplayMeta

  if (meta.version !== 1) {
    throw new Error(`[replay-to-shard] unsupported replay_meta version ${String(meta.version)}`)
  }
  if (meta.corpus_fmt !== undefined && meta.corpus_fmt !== 'replay') {
    throw new Error(`[replay-to-shard] corpus_fmt=${String(meta.corpus_fmt)} != replay`)
  }
  const N = meta.nSamples
  if (!Number.isInteger(N) || N <= 0) {
    throw new Error(`[replay-to-shard] bad nSamples ${String(N)}`)
  }
  if (meta.decisionTicks !== N) {
    throw new Error(
      `[replay-to-shard] decisionTicks ${String(meta.decisionTicks)} != nSamples ${String(N)}`,
    )
  }
  if (Number.isInteger(meta.ticks) && meta.ticks !== replay.totalTicks) {
    throw new Error(
      `[replay-to-shard] meta.ticks ${String(meta.ticks)} != replay totalTicks ${replay.totalTicks}`,
    )
  }
  if (Number.isInteger(meta.seed) && meta.seed !== replay.seed) {
    throw new Error(
      `[replay-to-shard] meta.seed ${String(meta.seed)} != replay seed ${replay.seed}`,
    )
  }

  // ---- 决策 tick 契约：显式列表优先；缺省 = 均匀 `t % k === 0`（与 decisionDue 同式） ----
  const ticks: number[] = []
  if (Array.isArray(meta.decisionTicksList)) {
    const list = meta.decisionTicksList
    if (list.length !== N) {
      throw new Error(
        `[replay-to-shard] decisionTicksList length ${list.length} != nSamples ${String(N)}`,
      )
    }
    let prev = -1
    for (const t of list) {
      if (!Number.isInteger(t) || t <= prev) {
        throw new Error(
          `[replay-to-shard] decisionTicksList not strictly increasing at ${String(t)}`,
        )
      }
      if (t < 0 || t >= replay.totalTicks) {
        throw new Error(
          `[replay-to-shard] decision tick ${String(t)} outside [0,${replay.totalTicks})`,
        )
      }
      prev = t
      ticks.push(t)
    }
  } else {
    const k = meta.k
    if (!Number.isInteger(k) || k <= 0) throw new Error(`[replay-to-shard] bad k ${String(k)}`)
    for (let t = 0; t < replay.totalTicks; t++) if (t % k === 0) ticks.push(t)
    if (ticks.length !== N) {
      throw new Error(
        `[replay-to-shard] uniform decision ticks ${ticks.length} != nSamples ${String(N)} (k=${k}, ticks=${replay.totalTicks})`,
      )
    }
  }
  // ---- 标签行数必须与决策 tick 对齐（缺文件 = 确定性失败） ----
  for (const label of ['a_move.npy', 'a_fire.npy', 'done.npy', 'mask.npy']) {
    const p = `${shardDir}/${label}`
    if (!existsSync(p)) throw new Error(`[replay-to-shard] missing ${label}`)
    const rows = npyRowCount(p)
    if (rows !== null && rows !== N) {
      throw new Error(`[replay-to-shard] ${label} rows ${rows} != nSamples ${String(N)}`)
    }
  }

  // ---- 建局：与 verify-replay / export-rl-rollout 同序（顺序错 ⇒ tickHash 必分叉） ----
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
  sim.input = input
  sim.input2 = input.input2 ?? null

  const encoder = new ObsEncoder()
  const obsFlat = new Uint8Array(N * OBS_CHANNELS * BOARD * BOARD)
  const scalarsFlat = new Float32Array(N * SCALAR_DIM)
  const extraFlat = new Float32Array(N * POLICY_EXTRA_DIM)
  const tickHashes: string[] = []
  const obsStride = OBS_CHANNELS * BOARD * BOARD
  /** 决策游标（`ticks` 严格递增，故单调前移即可；旧实现每 tick 查一次 Set）。 */
  let di = 0

  for (let t = 0; t < replay.totalTicks; t++) {
    // `ticks` 已校验为严格递增 ⇒ 单调游标与 Set 查表等价（且每 tick 零查表）。
    if (di < N && ticks[di] === t) {
      // 直写：把编码器的三块缓冲绑到大张量的本样本窗口（新建、全 0）——
      // 省掉三次 `.set()`（其中 obs 是 51 KB/样本），也不用再 `fill(0)`。
      const o0 = di * obsStride
      const s0 = di * SCALAR_DIM
      const e0 = di * POLICY_EXTRA_DIM
      encoder.bind(
        obsFlat.subarray(o0, o0 + obsStride),
        scalarsFlat.subarray(s0, s0 + SCALAR_DIM),
        extraFlat.subarray(e0, e0 + POLICY_EXTRA_DIM),
        true,
      )
      encoder.encode(world)
      di++
    }
    sim.tick()
    input.advance()
    // 与 export-rl-rollout 主循环同序：每 tick 消费事件，tickHash 才与录像一致。
    world.consumeEvents()
    if ((t + 1) % 100 === 0) tickHashes.push(worldTickHash(world))
    if (isTerminal(world.state) && t + 1 < replay.totalTicks) {
      throw new Error(
        `[replay-to-shard] world reached '${world.state}' at t${t + 1} < totalTicks ${replay.totalTicks}`,
      )
    }
  }
  if (di !== N) throw new Error(`[replay-to-shard] encoded ${di} samples != nSamples ${String(N)}`)

  // ---- 确定性校验：obsHash / tickHash 链 / 终局 ----
  // obsHash 规格 = `sha256(concat(obs_i))`（D4）；obsFlat 就是那份拼接（决策序、无空隙）
  // ⇒ 一次 update 与「逐决策 update」字节等价，但省掉 N 次原生调用（profile 6.5%）。
  const obsHash = `sha256:${createHash('sha256').update(obsFlat).digest('hex')}`
  if (meta.obsHash && obsHash !== meta.obsHash) {
    throw new Error(`[replay-to-shard] obsHash mismatch: computed ${obsHash}, meta ${meta.obsHash}`)
  }
  const recordedHashes = replay.tickHashes ?? []
  if (recordedHashes.length !== tickHashes.length) {
    throw new Error(
      `[replay-to-shard] tickHash count ${tickHashes.length} != recorded ${recordedHashes.length}`,
    )
  }
  for (let h = 0; h < recordedHashes.length; h++) {
    if (recordedHashes[h] !== tickHashes[h]) {
      throw new Error(
        `[replay-to-shard] tickHash mismatch at checkpoint t${(h + 1) * 100} (window [${h * 100},${(h + 1) * 100}])`,
      )
    }
  }
  const endStatus = statusOf(world)
  if (parsed.envelope.sim && parsed.envelope.sim.status !== endStatus) {
    throw new Error(
      `[replay-to-shard] final status ${endStatus} != recorded ${parsed.envelope.sim.status}`,
    )
  }
  const recordedFinal = parsed.envelope.finalState
  if (recordedFinal) {
    if (recordedFinal.killCount !== world.killCount) {
      throw new Error(
        `[replay-to-shard] killCount ${world.killCount} != recorded ${recordedFinal.killCount}`,
      )
    }
    if (recordedFinal.lives !== world.lives) {
      throw new Error(`[replay-to-shard] lives ${world.lives} != recorded ${recordedFinal.lives}`)
    }
    // finalState.ticks 是**游戏时钟**；state_init 时 = 交棒 tick + replay 帧数。
    const initTick = Number.isInteger(meta.initTick) ? Number(meta.initTick) : 0
    if (recordedFinal.ticks !== replay.totalTicks + initTick) {
      throw new Error(
        `[replay-to-shard] finalState.ticks ${recordedFinal.ticks} != ` +
          `totalTicks ${replay.totalTicks} + initTick ${initTick}`,
      )
    }
  }

  const written: string[] = []
  if (!opts.verifyOnly) {
    const outDir = opts.outDir ?? shardDir
    mkdirSync(outDir, { recursive: true })
    // 三张张量直写目标文件（不经过 `npyBytes` 的整块拷贝）；形状/dtype 与 D4 同源。
    const files: Array<[string, ArrayBufferView, number[], 'u1' | 'f4']> = [
      ['obs.npy', obsFlat, [N, OBS_CHANNELS, BOARD, BOARD], 'u1'],
      ['scalars.npy', scalarsFlat, [N, SCALAR_DIM], 'f4'],
      ['extra.npy', extraFlat, [N, POLICY_EXTRA_DIM], 'f4'],
    ]
    for (const [name, view, shape, dtype] of files) {
      writeNpyAtomic(`${outDir}/${name}`, view, shape, dtype)
      written.push(name)
    }
  }

  return {
    nSamples: N,
    ticks: replay.totalTicks,
    obsHash,
    convertMs: +(performance.now() - t0).toFixed(1),
    written,
  }
}

/**
 * CLI 与长驻池共用的入口。**失败一律 `throw`，不在这里 `process.exit`。**
 *
 * 池化（`--serve`）的协议是「失败打一行 `__SERVE_ERR__` 后继续服务下一个 shard」
 * —— `process.exit` 会把整池 worker 连同后面几百个 shard 一起带走。一次性路径的退出码
 * 由文件末尾的 CLI 包装负责。
 */
export function main(argv: string[]): void {
  const shardDirs: string[] = []
  let listFile = ''
  let outDir = ''
  let verifyOnly = false
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--shard-dir') shardDirs.push(argv[++i] ?? '')
    else if (a === '--shard-list') listFile = argv[++i] ?? ''
    else if (a === '--out') outDir = argv[++i] ?? ''
    else if (a === '--verify-only') verifyOnly = true
    else throw new Error(`[replay-to-shard] unknown arg ${a}`)
  }
  if (listFile) {
    for (const line of readFileSync(listFile, 'utf8').split(/\r?\n/)) {
      const s = line.trim()
      if (s) shardDirs.push(s)
    }
  }
  if (shardDirs.length === 0) {
    throw new Error(
      'usage: bun tools/sim/replay-to-shard.ts --shard-dir <dir>|--shard-list <file> ' +
        '[--out <dir>] [--verify-only]',
    )
  }
  let samples = 0
  let ticks = 0
  let convertMs = 0
  for (const shardDir of shardDirs) {
    const r = convertReplayShard({ shardDir, outDir: outDir || undefined, verifyOnly })
    samples += r.nSamples
    ticks += r.ticks
    convertMs += r.convertMs
    if (shardDirs.length === 1) {
      console.log(
        `[replay-to-shard] OK samples=${r.nSamples} ticks=${r.ticks} ` +
          `convertMs=${r.convertMs} written=${r.written.join(',') || '(none)'} obsHash=${r.obsHash}`,
      )
    }
  }
  if (shardDirs.length > 1) {
    console.log(
      `[replay-to-shard] OK shards=${shardDirs.length} samples=${samples} ticks=${ticks} ` +
        `convertMs=${convertMs.toFixed(1)}`,
    )
  }
}

/**
 * `--serve` 分派：每行 = `['conv', ...该 shard 的 argv]`（与 `serve-any.dispatch` 的
 * `[mode, ...argv]` 同规）。未知 mode 抛 —— 静默跑错比响亮报错贵得多。
 *
 * 本转换器**不走** `serve-any.ts` 的同质入口：它在 worker payload 里只有预建 bundle
 * （`replay-to-shard.node.js`），没有 TS 树 ⇒ 池的入口就是 bundle 自己。协议实现仍是
 * `serve-loop.ts::runServe`（唯一实现，不许各抄一份）。
 */
export function dispatch(argv: string[]): void {
  const mode = argv[0]
  if (mode !== 'conv') {
    throw new Error(
      `replay-to-shard: unknown mode ${mode === undefined ? '(missing)' : `'${mode}'`} — 合法：conv`,
    )
  }
  main(argv.slice(1))
}

if (import.meta.main) {
  if (process.argv.includes('--serve')) {
    runServe(dispatch)
  } else {
    try {
      main(process.argv.slice(2))
    } catch (e) {
      console.error(e instanceof Error ? e.message : String(e))
      process.exit(1)
    }
  }
}
