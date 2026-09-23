/**
 * rollout-runner.ts —— rollout 子进程的运行时选择（DECISIONS §353）
 *
 * 背景（本机实测 2026-09-08，bun 1.4.2 / node 26.8.1，CPU 空闲）：
 *   同一个 wasm 内核模块（37M MACs/forward；今 src/nn/conv/prebuilt/wasm/conv.wasm）
 *     bun  (JSC) 7.55 ms/次
 *     node (V8)  4.62 ms/次      → V8 快 ~1.63×
 *   端到端单局（1200 tick）1904 ms → 1546 ms（扣进程启动后 ~1.4×）。
 *   同 seed/权重下两引擎产出的 npy/manifest **逐字节相同**（wasm 字节码 + IEEE754），
 *   故换引擎不破坏跨节点确定性（M4 红线）。
 *
 * 策略：**agent 自身仍跑在 bun**（Bun.serve / 版本门 / codeHash 口径不变），只把
 * rollout 采样子进程的引擎交给**本机微基准自动选择**（§374/§378）：五平台实测 V8
 * 只在 win/wsl x64 赢 14-30%，mac/arm64 是 bun 赢 3-6% —— 版本门槛修不出平台差异，
 * 故启动时对同一 wasm 内核实测两引擎稳态 forward，选快者（3% 迟滞；结果按
 * bun/node 版本 + wasm sha 缓存，日常重启零开销）。用 node 时执行 `bun build
 * --target=node` 预打包 exporter。
 *
 * ⚠️ 打包产物必须自带两个资产：内核 wasm 与 native 共享库 —— 两者都是用
 * `new URL(<相对路径>, import.meta.url)` 定位的，打包后变成相对**产物**解析。缺文件
 * 不会报错，而是**静默回退**（wasm 缺 → TS 特征路径 4.4ms → 62.7ms，14× 慢；
 * native 缺 → 静默回落 wasm）—— 因此 ensureNodeBundle/ensureNativeAssets 会把它们
 * 按**同一相对路径**（`prebuilt/wasm/conv.wasm`、`prebuilt/<平台>/<库>`）复制到产物旁
 * （本坑实测代价：一整天的数据）。
 *
 * 降级链：node 不存在 / major < MIN_NODE_MAJOR / 打包失败 / node 子进程连续失败
 * ≥ NODE_FAIL_LIMIT 次 → 永久退回 bun（`--no-node` 可强制）。
 */
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { buildNative, nativeStaleReason, resolveNativeLib } from './native-build'

/** 内核 wasm 资产：仓根相对（源）与产物相对（bundle 里的镜像路径）。
 *  后者必须 == conv_wasm_adapter.ts 里 `new URL('./prebuilt/wasm/conv.wasm', import.meta.url)`
 *  的相对部分 —— 打包后 import.meta.url 就是产物自身。 */
const WASM_ASSET_REL = 'src/nn/conv/prebuilt/wasm/conv.wasm'
const WASM_BUNDLE_REL = 'prebuilt/wasm/conv.wasm'

/** node 最低可接受主版本（22.x LTS 起；实测 22.22 = 5.40ms、26.8 = 4.62ms）。 */
export const MIN_NODE_MAJOR = 22
/** 同一引擎连续失败多少次后永久回退（区分"任务本身失败"与"引擎不可用"）。 */
export const NODE_FAIL_LIMIT = 2

/** 允许交给 node 的 exporter 白名单（其余一律走 bun 原路径）。 */
const NODE_BUNDLE_ENTRIES: Record<string, string> = {
  'tools/sim/export-rl-rollout.ts': 'export-rl-rollout',
  'tools/sim/export-eval-game.ts': 'export-eval-game',
  'tools/sim/export-intent-rollout.ts': 'export-intent-rollout',
  'tools/sim/export-goal-rollout.ts': 'export-goal-rollout',
}

export type RolloutEngine = 'node' | 'bun'

export interface NodeRuntime {
  bin: string
  version: string
  major: number
  minor: number
  patch: number
  /** 版本排序键（major*1e6 + minor*1e3 + patch）。 */
  rank: number
}

/** 版本排序键。 */
function versionRank(v: VersionTriple): number {
  return v.major * 1_000_000 + v.minor * 1_000 + v.patch
}

export interface LaunchPlan {
  cmd: string
  argv: string[]
  engine: RolloutEngine
}

export interface VersionTriple {
  major: number
  minor: number
  patch: number
}

/** 解析 `node --version` 输出（"v26.8.1" 或 "26.8.1"）。 */
export function parseNodeVersion(raw: string): VersionTriple | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec(String(raw).trim())
  if (!m) return null
  return { major: Number(m[1]), minor: Number(m[2]), patch: Number(m[3]) }
}

export interface DetectOptions {
  /** 覆盖 node 可执行文件名/路径（默认 $SAMPLER_NODE_BIN || 'node'）。 */
  bin?: string
  /** 注入执行器（单测用）；缺省 spawnSync(bin, ['--version'])。 */
  run?: (bin: string) => { status: number; stdout: string }
}

/** 探测可用 node（≥ MIN_NODE_MAJOR）。不可用返回 null。 */
export function detectNode(opts: DetectOptions = {}): NodeRuntime | null {
  const bin = opts.bin ?? process.env.SAMPLER_NODE_BIN ?? 'node'
  const run =
    opts.run ??
    ((b: string) => {
      const r = spawnSync(b, ['--version'], { encoding: 'utf8', windowsHide: true })
      return { status: r.status ?? -1, stdout: String(r.stdout ?? '') }
    })
  try {
    const r = run(bin)
    if (r.status !== 0) return null
    const v = parseNodeVersion(r.stdout)
    if (!v || v.major < MIN_NODE_MAJOR) return null
    return {
      bin,
      version: `v${v.major}.${v.minor}.${v.patch}`,
      major: v.major,
      minor: v.minor,
      patch: v.patch,
      rank: versionRank(v),
    }
  } catch {
    return null
  }
}

/** exporter 源码 → 打包产物名（不在白名单返回 null = 不支持 node）。 */
export function bundleNameFor(entryTs: string): string | null {
  return NODE_BUNDLE_ENTRIES[entryTs] ?? null
}

export function bundlePathFor(entryTs: string, bundleDir: string): string | null {
  const base = bundleNameFor(entryTs)
  return base ? path.join(bundleDir, `${base}.mjs`) : null
}

export interface BuildOptions {
  repoRoot: string
  bundleDir: string
  /** 注入打包执行器（单测用）；缺省用当前 bun（process.execPath）执行 bun build。 */
  build?: (entry: string, out: string) => { status: number; stderr?: string }
  log?: (msg: string) => void
}

/**
 * 确保 `entryTs` 的 node 打包产物就位（幂等；产物 + wasm 资产）。
 * 返回产物绝对路径，失败返回 null（调用方须回退 bun）。
 */
export function ensureNodeBundle(entryTs: string, opts: BuildOptions): string | null {
  const out = bundlePathFor(entryTs, opts.bundleDir)
  if (!out) return null
  if (fs.existsSync(out)) return out
  const entry = path.join(opts.repoRoot, entryTs)
  if (!fs.existsSync(entry)) return null
  fs.mkdirSync(opts.bundleDir, { recursive: true })
  const tmp = `${out}.tmp-${process.pid}`
  // 原子落盘：同机多 agent 并发打包不会读到半截文件。
  try {
    const build =
      opts.build ??
      ((e: string, o: string) => {
        const r = spawnSync(process.execPath, ['build', e, '--target=node', `--outfile=${o}`], {
          cwd: opts.repoRoot,
          encoding: 'utf8',
          windowsHide: true,
        })
        return { status: r.status ?? -1, stderr: String(r.stderr ?? '') }
      })
    const r = build(entry, tmp)
    if (r.status !== 0 || !fs.existsSync(tmp)) {
      opts.log?.(
        `[rollout-runner] bun build 失败 ${entryTs} (status=${r.status}) ${r.stderr ?? ''}`,
      )
      try {
        fs.rmSync(tmp, { force: true })
      } catch {
        /* ignore */
      }
      return null
    }
    fs.renameSync(tmp, out)
  } catch (e) {
    opts.log?.(
      `[rollout-runner] 打包异常 ${entryTs}: ${e instanceof Error ? e.message : String(e)}`,
    )
    return null
  }
  // ⚠️ wasm 资产必须与产物同级（**同一相对路径**）：conv_wasm_adapter 用
  // `new URL('./prebuilt/wasm/conv.wasm', import.meta.url)` 定位 ⇒ 打包后相对产物解析。
  // 缺文件不报错，而是静默回退 TS 路径（14× 慢）。
  try {
    const src = path.join(opts.repoRoot, WASM_ASSET_REL)
    const dst = path.join(opts.bundleDir, WASM_BUNDLE_REL)
    fs.mkdirSync(path.dirname(dst), { recursive: true })
    if (fs.existsSync(src)) fs.copyFileSync(src, dst)
  } catch (e) {
    opts.log?.(
      `[rollout-runner] wasm 资产复制失败（将回退 TS 路径）: ${
        e instanceof Error ? e.message : String(e)
      }`,
    )
    return null
  }
  return out
}

/** codeHash 变化的旧产物清理（stamp 不符 → 整个目录重建）。 */
export function prepareBundleDir(bundleDir: string, codeHash: string): void {
  const stamp = path.join(bundleDir, '.codehash')
  try {
    if (fs.existsSync(stamp) && fs.readFileSync(stamp, 'utf8') === codeHash) return
    fs.rmSync(bundleDir, { recursive: true, force: true })
  } catch {
    /* ignore */
  }
  try {
    fs.mkdirSync(bundleDir, { recursive: true })
    fs.writeFileSync(stamp, codeHash)
  } catch {
    /* ignore */
  }
}

/**
 * 枚举机器上所有 node 可执行文件（$SAMPLER_NODE_BIN 优先，其次 `where.exe node` /
 * `which -a node`）。用于"挑版本最高的那个"——本机实测 node 26.8.1 (4.62ms) 比
 * node 22.22.2 (5.40ms) 再快 ~15%。
 */
export function listNodeCandidates(): string[] {
  const out: string[] = []
  const env = process.env.SAMPLER_NODE_BIN
  if (env) out.push(env)
  if (!env || process.env.SAMPLER_NODE_SCAN === '1') {
    try {
      const isWin = process.platform === 'win32'
      const r = spawnSync(isWin ? 'where.exe' : 'which', isWin ? ['node'] : ['-a', 'node'], {
        encoding: 'utf8',
        windowsHide: true,
      })
      if (r.status === 0) {
        for (const line of String(r.stdout ?? '').split(/\r?\n/)) {
          const t = line.trim()
          if (t && /node(\.exe)?$/i.test(t)) out.push(t)
        }
      }
    } catch {
      /* ignore */
    }
  }
  if (!out.length) out.push('node')
  return [...new Set(out)]
}

export interface BestNodeOptions extends DetectOptions {
  /** 注入候选枚举（单测用）。 */
  list?: () => string[]
}

/** 在候选里挑**版本最高**的可用 node；全部不可用 → null。 */
export function detectBestNode(opts: BestNodeOptions = {}): NodeRuntime | null {
  if (opts.bin) return detectNode(opts) // 显式指定：不扫描
  let best: NodeRuntime | null = null
  for (const bin of (opts.list ?? listNodeCandidates)()) {
    const n = detectNode({ bin, run: opts.run })
    if (n && (!best || n.rank > best.rank)) best = n
  }
  return best
}

// ================= 引擎微基准自动选择（DECISIONS §374） =================
/** node 需比 bun 快 ≥ 此比例才入选（迟滞，防噪声横跳）。 */
export const ENGINE_BENCH_MARGIN = 0.97
const BENCH_ENTRY_TS = 'tools/agent/engine-bench.ts'
const BENCH_MJS = 'engine-bench.mjs'
const CHOICE_FILE = 'engine-choice.json'
const BENCH_ROUNDS = 2

export interface EngineChoice {
  winner: RolloutEngine
  bunMs: number
  nodeMs: number | null
  bunVer: string
  nodeVer: string
  wasmSha: string
  ts: number
  /** bun 臂实测走的后端（native 臂 = 共享库已装且 attestation 过了）；旧缓存缺此字段即作废。 */
  bunArm?: BenchArm
  /** node 臂实测走的后端（正常就是 wasm）。 */
  nodeArm?: BenchArm
  /** native 共享库指纹（重建过就要重测）。 */
  nativeSha?: string
}

/** features 实际用到的后端（与 src/nn/conv/conv_native_adapter.ts 的 featuresEngine 对齐）。 */
export type BenchArm = 'native' | 'wasm' | 'ts' | 'unknown'

function sha256File(p: string): string {
  return createHash('sha256').update(fs.readFileSync(p)).digest('hex').slice(0, 16)
}

/** 缩短展示用指纹（native 库）。 */
export function shortSha(p: string | null): string {
  if (!p) return ''
  try {
    return sha256File(p)
  } catch {
    return ''
  }
}

export function engineChoiceFile(bundleDir: string): string {
  return path.join(bundleDir, CHOICE_FILE)
}

/**
 * 缓存是否仍有效：bun/node 版本、wasm sha、**native 库指纹**都没变才算数。
 * nativeSha 是 2026-09-21 新加的口径（不带上它 ⇒ 换了 native 库还吃旧基准）。
 */
export function choiceValid(
  c: EngineChoice | null,
  bunVer: string,
  nodeVer: string,
  wasmSha: string,
  nativeSha = '',
): boolean {
  return (
    !!c &&
    c.bunVer === bunVer &&
    c.nodeVer === nodeVer &&
    c.wasmSha === wasmSha &&
    (c.nativeSha ?? '') === nativeSha
  )
}

/** 迟滞判据：node 明显更快才选 node。 */
export function chooseByBench(
  bunMs: number,
  nodeMs: number | null,
  margin = ENGINE_BENCH_MARGIN,
): RolloutEngine {
  if (nodeMs === null || !Number.isFinite(nodeMs) || nodeMs <= 0) return 'bun'
  return nodeMs <= bunMs * margin ? 'node' : 'bun'
}

export interface BenchResult {
  bunMs: number
  nodeMs: number
  bunArm: BenchArm
  nodeArm: BenchArm
}

/**
 * native 共享库资产就位（T2，2026-09-21）：
 *  1. `NN_NATIVE_LIB` 指路 / **入库 prebuilt**（节点默认走这条，**不需要 clang**）/ 本机
 *     `tmp/native` 构建 ⇒ 直接用；
 *  2. 都没有（prebuilt 矩阵没这个平台）⇒ 有编译器就建一次（`NN_NATIVE_BUILD=0` 可禁）；
 *  3. 复制到 bundle 目录 ⇒ 打包后的 exporter 用模块相对路径也能找到（node 引擎用不上，
 *     但 bun 引擎跑 bundle 时会用）。
 * 失败永远只降级、不抛：native 是**可选加速**，掉了就回落 wasm（正确性另由首用 attestation 把关）。
 */
export function ensureNativeAssets(
  repoRoot: string,
  bundleDir: string,
  log: (m: string) => void,
): { ok: boolean; libPath: string | null; sha: string; reason: string } {
  if (process.env.NN_NATIVE === '0')
    return { ok: false, libPath: null, sha: '', reason: 'NN_NATIVE=0' }
  const resolved = resolveNativeLib(repoRoot)
  let lib: string | null = resolved.path
  if (!lib && process.env.NN_NATIVE_BUILD !== '0') {
    const r = buildNative(repoRoot, {})
    log(
      `[rollout-runner] native 构建${r.ok ? '成功' : `失败（${r.reason}）`}${r.log.length ? ` — ${r.log.join('; ')}` : ''}`,
    )
    lib = r.ok ? (r.lib ?? null) : null
  }
  if (!lib) {
    const why =
      process.env.NN_NATIVE_BUILD === '0' ? 'NN_NATIVE_BUILD=0' : nativeStaleReason(repoRoot)
    return {
      ok: false,
      libPath: null,
      sha: '',
      reason: `${resolved.reason}${why ? `；本机指纹: ${why}` : ''}`,
    }
  }
  log(`[rollout-runner] native 资产就绪 source=${resolved.kind ?? 'build'} lib=${lib}`)
  const sha = shortSha(lib)
  try {
    // 按**同一相对路径**镜像进 bundle：native 适配器的候选里有
    // `new URL(\`prebuilt/<平台>/<库>\`, import.meta.url)`，于是打包产物与源码两种形态
    // 走同一条解析路径（不再依赖“库与产物同级”的旧约定）。
    const dst = path.join(
      bundleDir,
      'prebuilt',
      `${process.platform}-${process.arch}`,
      path.basename(lib),
    )
    fs.mkdirSync(path.dirname(dst), { recursive: true })
    if (!fs.existsSync(dst) || shortSha(dst) !== sha) fs.copyFileSync(lib, dst)
  } catch (e) {
    log(
      `[rollout-runner] native 库复制到 bundle 失败（不影响 cwd/源路径解析）: ${
        e instanceof Error ? e.message : String(e)
      }`,
    )
  }
  return { ok: true, libPath: lib, sha, reason: 'ok' }
}

/** 解析基准输出（BENCH <ms> / BENCH-ARM <arm>）。 */
export function parseBench(stdout: string): { ms: number | null; arm: BenchArm } {
  let ms: number | null = null
  let arm: BenchArm = 'unknown'
  for (const line of stdout.split(/\r?\n/)) {
    const t = line.trim()
    const m = /^BENCH\s+([\d.]+)$/.exec(t)
    if (m) {
      const v = Number(m[1])
      if (Number.isFinite(v) && (ms === null || v < ms)) ms = v
    }
    const a = /^BENCH-ARM\s+(\S+)$/.exec(t)
    if (a) arm = a[1] as BenchArm
  }
  return { ms, arm }
}

/**
 * 真跑微基准（bun 跑 TS 源 / node 跑打包 mjs，各自进程内计时，spawn 税不进数字）。
 * 两侧都调用**生产入口**（native → wasm → TS），所以 bun 臂装上 native 时这里测到的
 * 就是 native 的数字（BENCH-ARM native）—— 引擎选择因此能拿 native 与 node+wasm 比。
 */
export function runEngineBench(
  repoRoot: string,
  bundleDir: string,
  bunPath: string,
  node: NodeRuntime,
  log: (m: string) => void,
  build?: BuildOptions['build'],
  nativeLib: string | null = null,
): BenchResult | null {
  try {
    fs.mkdirSync(bundleDir, { recursive: true })
    const mjs = path.join(bundleDir, BENCH_MJS)
    if (!fs.existsSync(mjs)) {
      const b =
        build ??
        ((entry: string, out: string) => {
          const r = spawnSync(bunPath, ['build', entry, '--target=node', `--outfile=${out}`], {
            cwd: repoRoot,
            encoding: 'utf8',
            windowsHide: true,
          })
          return { status: r.status ?? -1, stderr: String(r.stderr ?? '') }
        })
      const r = b(path.join(repoRoot, BENCH_ENTRY_TS), mjs)
      if (r.status !== 0 || !fs.existsSync(mjs)) {
        log(`[rollout-runner] engine-bench 打包失败 rc=${r.status} → 略过微基准`)
        return null
      }
    }
    // 打包臂要自带 wasm 资产（wasm 适配器按模块相对路径解析；缺了会静默回退 TS 路径）
    const wasm = path.join(repoRoot, WASM_ASSET_REL)
    if (!fs.existsSync(wasm)) return null
    const wasmDst = path.join(bundleDir, WASM_BUNDLE_REL)
    try {
      fs.mkdirSync(path.dirname(wasmDst), { recursive: true })
      if (!fs.existsSync(wasmDst)) fs.copyFileSync(wasm, wasmDst)
    } catch {
      /* 复制失败 → node 臂会回退 TS，基准数字自会露出来 */
    }
    // native 库经 env 明确告知两侧（bun 臂能用；node 臂即使拿到也用不了 bun:ffi）
    const env = { ...process.env, ...(nativeLib ? { NN_NATIVE_LIB: nativeLib } : {}) }
    const run = (
      cmd: string,
      entry: string,
    ): { ms: number | null; arm: BenchArm; note: string } | null => {
      const r = spawnSync(cmd, [entry, String(BENCH_ROUNDS)], {
        cwd: repoRoot,
        env,
        encoding: 'utf8',
        timeout: 60_000,
        windowsHide: true,
      })
      if (r.status !== 0) return null
      const p = parseBench(String(r.stdout ?? ''))
      return {
        ms: p.ms,
        arm: p.arm,
        note: String(r.stderr ?? '')
          .trim()
          .slice(0, 200),
      }
    }
    const bunR = run(bunPath, path.join(repoRoot, BENCH_ENTRY_TS))
    const nodeR = run(node.bin, mjs)
    if (!bunR || !nodeR || bunR.ms === null || nodeR.ms === null) {
      log(
        `[rollout-runner] 微基准失败 bun=${bunR?.ms ?? null}(${bunR?.arm ?? '?'}) node=${nodeR?.ms ?? null}(${nodeR?.arm ?? '?'}) → 略过`,
      )
      return null
    }
    // bun 臂若掉了 native，把原因记一行（否则「以为开了 native」无人知）
    if (nativeLib && bunR.arm !== 'native') {
      log(
        `[rollout-runner] 注意：bun 臂未走 native（arm=${bunR.arm}）${bunR.note ? ` — ${bunR.note}` : ''}`,
      )
    }
    return { bunMs: bunR.ms, nodeMs: nodeR.ms, bunArm: bunR.arm, nodeArm: nodeR.arm }
  } catch (e) {
    log(`[rollout-runner] 微基准异常: ${e instanceof Error ? e.message : String(e)} → 略过`)
    return null
  }
}

export function engineVersion(): string {
  const r = spawnSync(process.execPath, ['--version'], { encoding: 'utf8', windowsHide: true })
  return (r.stdout ?? '').trim() || 'unknown'
}

export interface RunnerOptions {
  repoRoot: string
  bundleDir?: string
  codeHash?: string
  /** 强制 bun（A/B 对照与回滚用；CLI `--no-node` / env SAMPLER_ENGINE=bun）。 */
  forceBun?: boolean
  /** 强制 node（env SAMPLER_ENGINE=node；需 node≥22）。 */
  forceNode?: boolean
  nodeBin?: string
  log?: (msg: string) => void
  detect?: (opts: DetectOptions) => NodeRuntime | null
  build?: BuildOptions['build']
  /** 注入微基准（单测用）：返回 bun/node 稳态 forward ms；null=基准失败。缺省真跑。 */
  bench?: BenchFn
  /** 注入 native 资产解析（单测用）。缺省 = ensureNativeAssets（可能触发一次编译）。 */
  native?: () => { ok: boolean; libPath: string | null; sha: string; reason: string }
}

export interface RolloutRunner {
  readonly engine: RolloutEngine
  readonly node: NodeRuntime | null
  /** 为什么选它（启动时打一行日志/进 /v1/status）。 */
  readonly reason: string
  readonly bundleDir: string
  launch(entryTs: string, args: string[]): LaunchPlan
  noteSuccess(engine: RolloutEngine): void
  noteFailure(engine: RolloutEngine, detail?: string): void
}

/** 注入式基准（单测用）：arms 可省（旧断言形状）。 */
export type BenchFn = () => {
  bunMs: number
  nodeMs: number
  bunArm?: BenchArm
  nodeArm?: BenchArm
} | null

/**
 * 创建 runner：启动时探测一次 node + native 资产 + 微基准，并按需预打包全部白名单 exporter。
 * 之后 launch() 只查缓存，不再做 IO。
 */
export function createRolloutRunner(opts: RunnerOptions): RolloutRunner {
  const bundleDir = opts.bundleDir ?? path.join(opts.repoRoot, 'tmp', 'dist-agent', 'node-bundle')
  const log = opts.log ?? (() => {})
  let engine: RolloutEngine = 'bun'
  let node: NodeRuntime | null = null
  let reason: string
  const envEngine = (process.env.SAMPLER_ENGINE ?? '').toLowerCase()
  const forceNode = opts.forceNode || envEngine === 'node'

  // 引擎选择（§374：默认按本机微基准自动择优；缓存命中零开销）
  if (opts.forceBun || envEngine === 'bun') {
    reason = opts.forceBun
      ? 'forceBun（--no-node / SAMPLER_ENGINE=bun）'
      : 'SAMPLER_ENGINE=bun 强制'
  } else {
    const t0 = Date.now()
    prepareBundleDir(bundleDir, opts.codeHash ?? '')
    node = (opts.detect ?? detectBestNode)({ bin: opts.nodeBin })
    if (!node) {
      reason = `node ≥ v${MIN_NODE_MAJOR} 不可用 → bun`
    } else if (forceNode) {
      engine = 'node'
      reason = `SAMPLER_ENGINE=node 强制（${node.version}）`
    } else {
      // 微基准自动选：缓存优先（bun/node 版本 + wasm 未变即复用）
      const bunVer = engineVersion()
      const wasmPath = path.join(opts.repoRoot, WASM_ASSET_REL)
      const wasmSha = fs.existsSync(wasmPath) ? sha256File(wasmPath) : ''
      const choiceFile = engineChoiceFile(bundleDir)
      // native 资产（rollout-eval-opt T2）：bun 臂的加速来源；失败只降级、不抛。
      // 放在探测之后、基准之前 —— 基准要按「本机真实会跑的臂」计时。
      const nat = (opts.native ?? (() => ensureNativeAssets(opts.repoRoot, bundleDir, log)))()
      log(
        nat.ok
          ? `[rollout-runner] native features 资产就绪 lib=${nat.libPath} sha=${nat.sha}`
          : `[rollout-runner] native features 不可用：${nat.reason}（bun 臂回落 wasm）`,
      )
      let loaded: EngineChoice | null = null
      try {
        if (fs.existsSync(choiceFile)) {
          const j = JSON.parse(fs.readFileSync(choiceFile, 'utf8')) as EngineChoice
          if (choiceValid(j, bunVer, node.version, wasmSha, nat.sha)) loaded = j
        }
      } catch {
        /* 缓存损坏 → 重测 */
      }
      if (loaded) {
        engine = loaded.winner
        reason =
          `微基准缓存（winner=${loaded.winner}，bun ${loaded.bunMs.toFixed(2)}ms` +
          `${loaded.bunArm ? `[${loaded.bunArm}]` : ''}` +
          (loaded.nodeMs !== null
            ? ` / node ${loaded.nodeMs.toFixed(2)}ms${loaded.nodeArm ? `[${loaded.nodeArm}]` : ''}`
            : '') +
          `，${bunVer} vs ${node.version}）`
      } else {
        const benchFn: BenchFn =
          opts.bench ??
          (() =>
            runEngineBench(
              opts.repoRoot,
              bundleDir,
              process.execPath,
              node!,
              log,
              opts.build,
              nat.libPath,
            ))
        const raw = benchFn()
        const bench: BenchResult | null = raw
          ? { ...raw, bunArm: raw.bunArm ?? 'unknown', nodeArm: raw.nodeArm ?? 'unknown' }
          : null
        if (!bench) {
          reason = `node ${node.version} 可用但微基准失败 → bun`
          node = null
        } else {
          engine = chooseByBench(bench.bunMs, bench.nodeMs)
          const choice: EngineChoice = {
            winner: engine,
            bunMs: bench.bunMs,
            nodeMs: bench.nodeMs,
            bunVer,
            nodeVer: node.version,
            wasmSha,
            ts: Date.now(),
            bunArm: bench.bunArm,
            nodeArm: bench.nodeArm,
            nativeSha: nat.sha,
          }
          try {
            fs.mkdirSync(bundleDir, { recursive: true })
            fs.writeFileSync(choiceFile, JSON.stringify(choice))
          } catch {
            /* 缓存写失败不影响本次决策 */
          }
          reason =
            `微基准：bun ${bench.bunMs.toFixed(2)}ms[${bench.bunArm}] vs ` +
            `node ${bench.nodeMs.toFixed(2)}ms[${bench.nodeArm}] → ` +
            (engine === 'node' ? `node ${node.version}` : 'bun（node 未快过 3% 迟滞）') +
            `（${Date.now() - t0}ms）`
        }
      }
    }
  }
  log(`[rollout-runner] engine=${engine} — ${reason}`)

  // engine=node 时预打包 exporter（bun 引擎不需要 bundle）
  if (engine === 'node' && node) {
    const t0 = Date.now()
    let ok = 0
    for (const entryTs of Object.keys(NODE_BUNDLE_ENTRIES)) {
      if (ensureNodeBundle(entryTs, { repoRoot: opts.repoRoot, bundleDir, build: opts.build, log }))
        ok++
    }
    if (ok === 0) {
      log(`[rollout-runner] node ${node.version} 可用但打包全失败 → 退回 bun`)
      engine = 'bun'
      node = null
    } else {
      log(
        `[rollout-runner] exporter 打包 ${ok}/${Object.keys(NODE_BUNDLE_ENTRIES).length}（${Date.now() - t0}ms）`,
      )
    }
  }

  let failStreak = 0
  const bundles = new Map<string, string | null>()

  return {
    get engine() {
      return engine
    },
    get node() {
      return node
    },
    reason,
    bundleDir,
    launch(entryTs: string, args: string[]): LaunchPlan {
      if (engine === 'node' && node) {
        if (!bundles.has(entryTs)) {
          bundles.set(
            entryTs,
            ensureNodeBundle(entryTs, {
              repoRoot: opts.repoRoot,
              bundleDir,
              build: opts.build,
              log,
            }),
          )
        }
        const out = bundles.get(entryTs) ?? null
        if (out) return { cmd: node.bin, argv: [out, ...args.slice(1)], engine: 'node' }
      }
      // 非白名单 / 打包失败 / 已降级 → 原 bun 路径（args[0] 就是 .ts 源码）
      return { cmd: process.execPath, argv: args, engine: 'bun' }
    },
    noteSuccess(engineUsed: RolloutEngine): void {
      if (engineUsed === 'node') failStreak = 0
    },
    noteFailure(engineUsed: RolloutEngine, detail?: string): void {
      if (engineUsed !== 'node' || engine !== 'node') return
      failStreak++
      if (failStreak >= NODE_FAIL_LIMIT) {
        engine = 'bun'
        log(
          `[rollout-runner] node 连续失败 ${failStreak} 次 → 永久回退 bun（last: ${detail ?? 'n/a'}）`,
        )
      }
    },
  }
}
