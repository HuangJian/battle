/**
 * rollout-runner.ts —— rollout 子进程的运行时选择（DECISIONS §353）
 *
 * 背景（本机实测 2026-09-08，bun 1.4.2 / node 26.8.1，CPU 空闲）：
 *   同一个 conv_feats.wasm 模块（37M MACs/forward）
 *     bun  (JSC) 7.55 ms/次
 *     node (V8)  4.62 ms/次      → V8 快 ~1.63×
 *   端到端单局（1200 tick）1904 ms → 1546 ms（扣进程启动后 ~1.4×）。
 *   同 seed/权重下两引擎产出的 npy/manifest **逐字节相同**（wasm 字节码 + IEEE754），
 *   故换引擎不破坏跨节点确定性（M4 红线）。
 *
 * 策略：**agent 自身仍跑在 bun**（Bun.serve / 版本门 / codeHash 口径不变），只把
 * rollout 采样子进程交给 node —— 用 `bun build --target=node` 预打包 exporter，
 * 由 node 执行打包产物。
 *
 * ⚠️ 打包产物必须自带 wasm：`conv-wasm.ts` 用 `new URL('./wasm/conv_feats.wasm',
 * import.meta.url)` 定位，打包后是相对**产物**解析的。缺文件不会报错，而是**静默
 * 回退 TS 特征路径（4.4ms → 62.7ms，14× 慢）** —— 因此 ensureBundle 一定会把
 * conv_feats.wasm 复制到产物同级 `wasm/` 下（本坑实测代价：一整天的数据）。
 *
 * 降级链：node 不存在 / major < MIN_NODE_MAJOR / 打包失败 / node 子进程连续失败
 * ≥ NODE_FAIL_LIMIT 次 → 永久退回 bun（`--no-node` 可强制）。
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

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
  // ⚠️ wasm 资产必须与产物同级：否则 conv-wasm 静默回退 TS 路径（14× 慢）。
  try {
    const src = path.join(opts.repoRoot, 'src', 'nn', 'wasm', 'conv_feats.wasm')
    const dstDir = path.join(opts.bundleDir, 'wasm')
    fs.mkdirSync(dstDir, { recursive: true })
    if (fs.existsSync(src)) fs.copyFileSync(src, path.join(dstDir, 'conv_feats.wasm'))
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

export interface RunnerOptions {
  repoRoot: string
  bundleDir?: string
  codeHash?: string
  /** 强制 bun（A/B 对照与回滚用；CLI `--no-node`）。 */
  forceBun?: boolean
  nodeBin?: string
  log?: (msg: string) => void
  detect?: (opts: DetectOptions) => NodeRuntime | null
  build?: BuildOptions['build']
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

/**
 * 创建 runner：启动时探测一次 node + 预打包全部白名单 exporter。
 * 之后 launch() 只查缓存，不再做 IO。
 */
export function createRolloutRunner(opts: RunnerOptions): RolloutRunner {
  const bundleDir = opts.bundleDir ?? path.join(opts.repoRoot, 'tmp', 'dist-agent', 'node-bundle')
  const log = opts.log ?? (() => {})
  let engine: RolloutEngine = 'bun'
  let node: NodeRuntime | null = null
  let reason: string

  if (opts.forceBun) {
    reason = 'forceBun（--no-node / SAMPLER_NODE_BIN 未启用）'
  } else {
    node = (opts.detect ?? detectBestNode)({ bin: opts.nodeBin })
    if (!node) {
      reason = `node ≥ v${MIN_NODE_MAJOR} 不可用 → bun`
    } else {
      const t0 = Date.now()
      prepareBundleDir(bundleDir, opts.codeHash ?? '')
      let ok = 0
      for (const entryTs of Object.keys(NODE_BUNDLE_ENTRIES)) {
        if (
          ensureNodeBundle(entryTs, { repoRoot: opts.repoRoot, bundleDir, build: opts.build, log })
        )
          ok++
      }
      if (ok === 0) {
        reason = `node ${node.version} 可用但打包全失败 → bun`
        node = null
      } else {
        engine = 'node'
        reason = `node ${node.version}（打包 ${ok}/${Object.keys(NODE_BUNDLE_ENTRIES).length}，${Date.now() - t0}ms）`
      }
    }
  }
  log(`[rollout-runner] engine=${engine} — ${reason}`)

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
