/**
 * native-build.ts —— native features 内核（共享库 + 对拍 CLI + **交叉编译 prebuilt**）的
 * **唯一**构建入口。
 *
 * 为什么必须有这个脚本（2026-09-21 评审 B2/B3）：
 *  ① **flags 钉死**：`-march=native` 会打开 FMA/AVX-512，而 clang 默认
 *     `-ffp-contract=fast` ⇒ 乘加融合 ⇒ 与 wasm 路径不再逐位（累加顺序没变也救不了
 *     收缩）。异质节点（win/mac/a95/a97 混跑）各自编译 ⇒ 同一 job 的 shard 字节抖动
 *     ⇒ data_fp 漂移、历史语料被判异血缘。故 flags/link 参数只在
 *     `src/nn/conv/native-prebuilt.ts` 里定一次，本脚本照用（`-ffp-contract=off`、
 *     `-fno-fast-math`、x64 只到 AVX1）。
 *  ② **可复现**：T0 的「8/8 逐位一致」原先只剩一个手工脚本 + tmp 里的 .exe（工具还不
 *     负责构建），tmp 一清就再验不了。本脚本产出 `native-build.json` 指纹（源码 sha +
 *     flags + cc 版本 + 产物 sha），`--check` 可判「盘上产物是否仍是这份源码编的」。
 *  ③ **单源**：共享库、wasm 与 CLI 都是 `src/nn/conv/conv.c` —— native 与 wasm32 只是同一
 *     份算术的两个编译目标（差异仅 `CF_PW_PX`：wasm 8 / native 16，见 conv_native.h）。
 *  ④ **prebuilt（2026-09-21 新增）**：节点机器多数没有 clang，`--cross` 在训练机上把
 *     6 个目标平台（win/linux/darwin × x64/arm64，Termux 用 linux-arm64 那一份）的库
 *     交叉编译进 `src/nn/conv/prebuilt/`（含 wasm32 产物），随 git pull 分发 —— 详见
 *     `src/nn/conv/native-prebuilt.ts` 头注与 `--check-prebuilt`（仓库侧新鲜度门禁）。
 *
 * 用法（仓根）:
 *   bun tools/agent/native-build.ts               # 本机共享库 + CLI → tmp/native（默认）
 *   bun tools/agent/native-build.ts --check       # 校验本机指纹/产物是否过期（exit 1 = 过期）
 *   bun tools/agent/native-build.ts --cross       # 全量交叉编译 prebuilt → src/nn/conv/prebuilt
 *   bun tools/agent/native-build.ts --wasm        # 只重建 wasm32 产物 → prebuilt/wasm/conv.wasm
 *   bun tools/agent/native-build.ts --check-prebuilt   # prebuilt 与源码/产物 sha 是否同源
 *   bun tools/agent/native-build.ts --json        # 构建后打印 manifest（供 runner/测试读）
 *   bun tools/agent/native-build.ts --out-dir tmp/native --cc clang
 *
 * 产物（默认 tmp/native/，gitignored）:
 *   conv_native.{dll,so,dylib}   生产共享库（bun:ffi 同步调用）
 *   conv_cli[.exe]               独立参考 CLI（字节对拍/离线基准；需系统 libc，仅本机构建）
 *   native-build.json            指纹
 */
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  NATIVE_ABI,
  NATIVE_TARGETS,
  PREBUILT_DIR,
  PREBUILT_MANIFEST_NAME,
  WASM_TARGET,
  nativeFlags,
  nativeLibBasename,
  nativeLinkArgs,
  nativeTargetFor,
  wasmBuildFlags,
  type NativeTarget,
  type PrebuiltEntry,
  type PrebuiltManifest,
  type PrebuiltWasmEntry,
} from '../../src/nn/conv/native-prebuilt'

export { NATIVE_ABI, PREBUILT_DIR, PREBUILT_MANIFEST_NAME }
export type { PrebuiltEntry, PrebuiltManifest }

export const MANIFEST_NAME = 'native-build.json'
/** 本机构建/CLI 的默认目录（相对仓根；tmp/ 已 gitignore）。 */
export const DEFAULT_OUT_DIR = 'tmp/native'

/**
 * 本机目标。矩阵里没有的组合（freebsd 等）合成一个「尽量能编」的同形目标：
 * 本机构建只是给开发的便利路径，真正的分发物是 `--cross` 的 prebuilt。
 */
export function hostTarget(
  platform: string = process.platform,
  arch: string = process.arch,
): NativeTarget {
  const known = nativeTargetFor(platform, arch)
  if (known) return known
  return {
    id: `${platform}-${arch}`,
    triple: 'host',
    kind: platform === 'win32' ? 'coff' : platform === 'darwin' ? 'macho' : 'elf',
    lib: nativeLibBasename(platform),
    vector: arch === 'x64' ? ['-mavx', '-msse4.2'] : [],
  }
}

export function nativeArtifactNames(platform: string = process.platform): {
  lib: string
  exe: string
} {
  return {
    lib: nativeLibBasename(platform),
    exe: platform === 'win32' ? 'conv_cli.exe' : 'conv_cli',
  }
}

/** 卷积内核目录（现址；相对仓根的路径只在下面几个函数里出现）。 */
export const CONV_SRC_DIR = 'src/nn/conv'

/**
 * 内核真正依赖的源码（算法核心 + 两个目标的 ABI 头）。
 *
 * 三个文件放同一份：`conv_wasm.h` 只影响 wasm 目标、`conv_native.h` 只影响 native 目标，
 * 但预编产物（6 native + 1 wasm）是**一起发**的 ⇒ 用同一份源码清单才能一次抓全
 * 「改了 conv.c/头文件却只重编了一部分」这种缺口（`--check-prebuilt` 是唯一闸门）。
 */
export function nativeLibSourceFiles(repoRoot: string): string[] {
  return [
    path.join(repoRoot, 'src', 'nn', 'conv', 'conv_native.h'),
    path.join(repoRoot, 'src', 'nn', 'conv', 'conv_wasm.h'),
    path.join(repoRoot, 'src', 'nn', 'conv', 'conv.c'),
  ]
}

/**
 * 本机构建指纹涉及的源码（= 内核 + CLI 编解码）。
 *
 * prebuilt 用 `nativeLibSourceFiles`（只算内核）：本机构建会多编一个 CLI，把 CLI 的 sha
 * 也算进 prebuilt 新鲜度只会得到「改了一行 CLI → 强制重跑一次无意义的 --cross」的假警报。
 */
export function nativeSourceFiles(repoRoot: string): string[] {
  return [...nativeLibSourceFiles(repoRoot), path.join(repoRoot, 'src', 'nn', 'conv', 'conv_cli.c')]
}

export interface NativeArtifact {
  name: string
  sha256: string
  bytes: number
}

export interface NativeManifest {
  abi: number
  cc: string
  ccVersion: string
  target: string
  flags: string[]
  sources: Array<{ path: string; sha256: string }>
  artifacts: { lib: NativeArtifact; exe: NativeArtifact }
  builtAt: string
}

export function sha256File(p: string): string {
  return createHash('sha256').update(fs.readFileSync(p)).digest('hex')
}

export function nativeOutDir(repoRoot: string, outDir?: string): string {
  return path.isAbsolute(outDir ?? '')
    ? (outDir as string)
    : path.join(repoRoot, outDir ?? DEFAULT_OUT_DIR)
}

export function nativeManifestPath(dir: string): string {
  return path.join(dir, MANIFEST_NAME)
}

export interface BuildResult {
  ok: boolean
  reason?: string
  dir: string
  lib?: string
  exe?: string
  manifest?: NativeManifest
  log: string[]
}

export interface BuildOptions {
  outDir?: string
  cc?: string
  arch?: string
  platform?: string
  /** 注入执行器（单测用）。返回 status/stderr/stdout。 */
  run?: (cc: string, args: string[]) => { status: number; stderr: string; stdout: string }
  /** 已存在的产物直接复用（幂等；runner 每次启动都会调）。 */
  reuse?: boolean
}

export type CcRunner = (
  cc: string,
  args: string[],
) => { status: number; stderr: string; stdout: string }

function defaultRun(
  cc: string,
  args: string[],
): { status: number; stderr: string; stdout: string } {
  const r = spawnSync(cc, args, { encoding: 'utf8', windowsHide: true })
  return { status: r.status ?? -1, stderr: String(r.stderr ?? ''), stdout: String(r.stdout ?? '') }
}

/**
 * 构建本机共享库 + CLI，并写下指纹。幂等：指纹与盘上产物都新鲜时直接返回（reuse 默认 true）。
 *
 * 共享库走 **freestanding + -nostdlib**（与 prebuilt 同一配方，见 native-prebuilt.ts）：
 * 这样「本机构建」与「入库 prebuilt」在同一平台上是同一份配方 → 可字节比对（tests）。
 * CLI 仍链系统 libc（stdio/malloc），故不定义 CF_FREESTANDING。
 */
export function buildNative(repoRoot: string, opts: BuildOptions = {}): BuildResult {
  const log: string[] = []
  const dir = nativeOutDir(repoRoot, opts.outDir)
  const platform = opts.platform ?? process.platform
  const arch = opts.arch ?? process.arch
  const cc = opts.cc ?? process.env.NN_CC ?? 'clang'
  const names = nativeArtifactNames(platform)
  const lib = path.join(dir, names.lib)
  const exe = path.join(dir, names.exe)
  const run = opts.run ?? defaultRun

  if ((opts.reuse ?? true) && nativeReady(repoRoot, { outDir: opts.outDir, platform })) {
    let manifest: NativeManifest | undefined
    try {
      manifest = JSON.parse(fs.readFileSync(nativeManifestPath(dir), 'utf8')) as NativeManifest
    } catch {
      manifest = undefined
    }
    return { ok: true, reason: 'reuse（指纹新鲜）', dir, lib, exe, manifest, log }
  }

  const target = hostTarget(platform, arch)
  const flags = nativeFlags(target)
  const srcCore = path.join(repoRoot, 'src', 'nn', 'conv', 'conv.c')
  const srcCli = path.join(repoRoot, 'src', 'nn', 'conv', 'conv_cli.c')
  // 先查源文件再问编译器：没有源码的仓（测试桩）不该白跑一次 clang
  for (const f of [srcCore, srcCli]) {
    if (!fs.existsSync(f)) return { ok: false, reason: `缺源文件: ${f}`, dir, log }
  }
  const ver = run(cc, ['--version'])
  if (ver.status !== 0) {
    return { ok: false, reason: `编译器不可用: ${cc}`, dir, log }
  }
  const ccVersion = ver.stdout.split(/\r?\n/)[0]?.trim() ?? 'unknown'
  fs.mkdirSync(dir, { recursive: true })

  // Windows/clang 会顺手吐 .exp/.lib 导入库（tmp/native 里全是噪音）——CLI 链接时关掉
  const implib = target.kind === 'coff' ? ['-Wl,/noimplib'] : []

  // ---- 共享库（原子落盘：先写临时名再 rename，避免并发读到半截 .dll） ----
  const libTmp = `${lib}.tmp-${process.pid}`
  const libBuild = run(cc, [...flags, ...nativeLinkArgs(target), ...implib, '-o', libTmp, srcCore])
  if (libBuild.status !== 0 || !fs.existsSync(libTmp)) {
    try {
      fs.rmSync(libTmp, { force: true })
    } catch {
      /* ignore */
    }
    return {
      ok: false,
      reason: `共享库编译失败 rc=${libBuild.status}`,
      dir,
      log: [...log, libBuild.stderr.slice(0, 400)],
    }
  }
  fs.renameSync(libTmp, lib)
  log.push(`lib ok: ${path.relative(repoRoot, lib)}`)

  // ---- 参考 CLI（链系统 libc：stdio/malloc；不走 -nostdlib） ----
  const exeTmp = `${exe}.tmp-${process.pid}`
  const exeBuild = run(cc, [
    ...nativeFlags(target, false),
    ...implib,
    '-o',
    exeTmp,
    srcCli,
    srcCore,
  ])
  if (exeBuild.status !== 0 || !fs.existsSync(exeTmp)) {
    try {
      fs.rmSync(exeTmp, { force: true })
    } catch {
      /* ignore */
    }
    return {
      ok: false,
      reason: `CLI 编译失败 rc=${exeBuild.status}`,
      dir,
      lib,
      log: [...log, exeBuild.stderr.slice(0, 400)],
    }
  }
  fs.renameSync(exeTmp, exe)
  log.push(`cli ok: ${path.relative(repoRoot, exe)}`)

  // clang 在 Windows 上还会留下 .exp/.lib 导出文件（`/noimplib` 只挡 .lib）——手动清掉，
  // 让 tmp/native 里只有「库 + CLI + 指纹」三件（文档就是这么写的）。
  for (const stray of [`${lib}.exp`, `${lib}.lib`, `${exe}.exp`, `${exe}.lib`]) {
    try {
      fs.rmSync(stray, { force: true })
    } catch {
      /* ignore */
    }
  }

  const manifest: NativeManifest = {
    abi: NATIVE_ABI,
    cc,
    ccVersion,
    target: `${platform}-${arch}`,
    flags,
    sources: nativeSourceFiles(repoRoot).map((p) => ({
      path: path.relative(repoRoot, p).replace(/\\/g, '/'),
      sha256: sha256File(p),
    })),
    artifacts: {
      lib: { name: names.lib, sha256: sha256File(lib), bytes: fs.statSync(lib).size },
      exe: { name: names.exe, sha256: sha256File(exe), bytes: fs.statSync(exe).size },
    },
    builtAt: new Date().toISOString(),
  }
  fs.writeFileSync(nativeManifestPath(dir), JSON.stringify(manifest, null, 2), { encoding: 'utf8' })
  log.push(`manifest ok: ${MANIFEST_NAME}`)
  return { ok: true, dir, lib, exe, manifest, log }
}

export interface CheckResult {
  ok: boolean
  reason: string
}

/** 指纹校验：源码 sha / flags / 产物 sha 全对才算新鲜。 */
export function nativeReady(
  repoRoot: string,
  opts: { outDir?: string; platform?: string } = {},
): boolean {
  return !nativeStaleReason(repoRoot, opts)
}

/** 返回「为什么不可用」；空串 = 新鲜可用。 */
export function nativeStaleReason(
  repoRoot: string,
  opts: { outDir?: string; platform?: string } = {},
): string {
  const dir = nativeOutDir(repoRoot, opts.outDir)
  const mp = nativeManifestPath(dir)
  if (!fs.existsSync(mp)) return `无指纹 ${mp}`
  let m: NativeManifest
  try {
    m = JSON.parse(fs.readFileSync(mp, 'utf8')) as NativeManifest
  } catch (e) {
    return `指纹不可读: ${e instanceof Error ? e.message : String(e)}`
  }
  if (m.abi !== NATIVE_ABI) return `ABI 不符（盘上 ${m.abi}，期望 ${NATIVE_ABI}）`
  if (m.target !== `${opts.platform ?? process.platform}-${process.arch}`)
    return `目标不符（盘上 ${m.target}）`
  const srcProblem = sourcesStaleReason(repoRoot, m.sources)
  if (srcProblem) return srcProblem
  for (const a of [m.artifacts.lib, m.artifacts.exe]) {
    const p = path.join(dir, a.name)
    if (!fs.existsSync(p)) return `缺产物 ${a.name}`
    if (sha256File(p) !== a.sha256) return `产物已变 ${a.name}（需重建）`
  }
  return ''
}

function sourcesStaleReason(
  repoRoot: string,
  sources: Array<{ path: string; sha256: string }>,
): string {
  for (const s of sources) {
    const p = path.join(repoRoot, s.path)
    if (!fs.existsSync(p)) return `缺源文件 ${s.path}`
    if (sha256File(p) !== s.sha256) return `源码已变 ${s.path}（需重建）`
  }
  return ''
}

/** 本机构建的共享库绝对路径（缺失返回 null）。 */
export function nativeLibPath(
  repoRoot: string,
  opts: { outDir?: string; platform?: string } = {},
): string | null {
  const dir = nativeOutDir(repoRoot, opts.outDir)
  const p = path.join(dir, nativeArtifactNames(opts.platform ?? process.platform).lib)
  return fs.existsSync(p) ? p : null
}

/* ─────────────────────────── prebuilt（交叉编译分发物） ─────────────────────────── */

export function prebuiltOutDir(repoRoot: string, outDir?: string): string {
  return path.isAbsolute(outDir ?? '')
    ? (outDir as string)
    : path.join(repoRoot, outDir ?? PREBUILT_DIR)
}

export function prebuiltManifestPath(dir: string): string {
  return path.join(dir, PREBUILT_MANIFEST_NAME)
}

function readPrebuiltManifest(dir: string): PrebuiltManifest | null {
  try {
    return JSON.parse(fs.readFileSync(prebuiltManifestPath(dir), 'utf8')) as PrebuiltManifest
  } catch {
    return null
  }
}

export interface PrebuiltTargetResult {
  id: string
  ok: boolean
  sha256?: string
  bytes?: number
  reason?: string
}

export interface PrebuiltBuildResult {
  ok: boolean
  dir: string
  targets: PrebuiltTargetResult[]
  manifest?: PrebuiltManifest
  log: string[]
}

/** wasm32 产物路径（`prebuilt/wasm/conv.wasm`）。 */
export function prebuiltWasmPath(dir: string): string {
  return path.join(dir, WASM_TARGET.dir, WASM_TARGET.lib)
}

export interface WasmBuildResult {
  ok: boolean
  path: string
  sha256?: string
  bytes?: number
  reason?: string
}

/**
 * 编译 wasm32 产物（与 6 个 native 目标**同一份** `conv.c`）。
 *
 * 与 buildPrebuilt 一样**不做 reuse**：漏编 wasm 是仓库记录过的最危险失败模式
 * （`run()` 静静回落 TS 路径 = 41 ms/forward > 16.7 ms 帧预算，plan/obs-schema-v3.plan.md
 * §3.5），宁可多花一秒重编。
 */
export function buildWasm(
  repoRoot: string,
  opts: { outDir?: string; cc?: string; run?: CcRunner } = {},
): WasmBuildResult {
  const dir = prebuiltOutDir(repoRoot, opts.outDir)
  const cc = opts.cc ?? process.env.NN_CC ?? 'clang'
  const run = opts.run ?? defaultRun
  const srcCore = path.join(repoRoot, 'src', 'nn', 'conv', 'conv.c')
  if (!fs.existsSync(srcCore)) return { ok: false, path: '', reason: `缺源文件 ${srcCore}` }
  const ver = run(cc, ['--version'])
  if (ver.status !== 0) return { ok: false, path: '', reason: `编译器不可用: ${cc}` }
  const out = prebuiltWasmPath(dir)
  fs.mkdirSync(path.dirname(out), { recursive: true })
  const tmp = `${out}.tmp-${process.pid}`
  const r = run(cc, [...wasmBuildFlags(), '-o', tmp, srcCore])
  if (r.status !== 0 || !fs.existsSync(tmp)) {
    try {
      fs.rmSync(tmp, { force: true })
    } catch {
      /* ignore */
    }
    const why = `rc=${r.status}: ${r.stderr.split(/\r?\n/).find((l) => l.trim()) ?? ''}`.slice(
      0,
      300,
    )
    return { ok: false, path: out, reason: why }
  }
  fs.renameSync(tmp, out)
  return { ok: true, path: out, sha256: sha256File(out), bytes: fs.statSync(out).size }
}

/** 盘上 wasm 产物的 manifest 条目（不存在返回 null）。 */
export function wasmEntryOf(dir: string): PrebuiltWasmEntry | null {
  const p = prebuiltWasmPath(dir)
  if (!fs.existsSync(p)) return null
  return {
    id: WASM_TARGET.id,
    lib: WASM_TARGET.lib,
    flags: wasmBuildFlags(),
    sha256: sha256File(p),
    bytes: fs.statSync(p).size,
  }
}

export interface PrebuiltBuildOptions {
  outDir?: string
  cc?: string
  run?: CcRunner
  /** 只构建这些目标 id（调试用；manifest 里其它目标的条目会保留，文件缺失则记为失败）。 */
  only?: string[]
}

/**
 * 交叉编译全部目标 → `<outDir>/<id>/<lib>` + `manifest.json`。
 *
 * 幂等性：本函数**不做 reuse**（6 个目标全量重建约数秒），因为这是「跟着源码走」的
 * 发布动作 —— 少了它就会出现「改了内核忘了重建某个目标」的静默缺口（`--check-prebuilt`
 * 会在仓库侧抓住这种缺口，那才是提交时的最后一道闸）。
 */
export function buildPrebuilt(
  repoRoot: string,
  opts: PrebuiltBuildOptions = {},
): PrebuiltBuildResult {
  const log: string[] = []
  const dir = prebuiltOutDir(repoRoot, opts.outDir)
  const cc = opts.cc ?? process.env.NN_CC ?? 'clang'
  const run = opts.run ?? defaultRun
  const srcCore = path.join(repoRoot, 'src', 'nn', 'conv', 'conv.c')
  if (!fs.existsSync(srcCore)) {
    return { ok: false, dir, targets: [], log: [`缺源文件 ${srcCore}`] }
  }
  const ver = run(cc, ['--version'])
  // wasm 与 native 目标同一份 conv.c：源码不在就一起失败（不用先跑一遍再发现）
  if (ver.status !== 0) {
    return {
      ok: false,
      dir,
      targets: NATIVE_TARGETS.map((t) => ({ id: t.id, ok: false, reason: `编译器不可用: ${cc}` })),
      log: [`交叉编译需要 clang（+ lld）：${cc} --version 失败`],
    }
  }
  const ccVersion = ver.stdout.split(/\r?\n/)[0]?.trim() ?? 'unknown'
  const only = opts.only && opts.only.length > 0 ? new Set(opts.only) : null
  const results: PrebuiltTargetResult[] = []
  const entries: PrebuiltEntry[] = []

  for (const t of NATIVE_TARGETS) {
    if (only && !only.has(t.id)) continue
    const tdir = path.join(dir, t.id)
    fs.mkdirSync(tdir, { recursive: true })
    const out = path.join(tdir, t.lib)
    const tmp = `${out}.tmp-${process.pid}`
    const flags = nativeFlags(t)
    const args = ['--target=' + t.triple, ...flags, ...nativeLinkArgs(t), '-o', tmp, srcCore]
    const r = run(cc, args)
    if (r.status !== 0 || !fs.existsSync(tmp)) {
      try {
        fs.rmSync(tmp, { force: true })
      } catch {
        /* ignore */
      }
      const why = `rc=${r.status}: ${r.stderr.split(/\r?\n/).find((l) => l.trim()) ?? ''}`.slice(
        0,
        300,
      )
      results.push({ id: t.id, ok: false, reason: why })
      log.push(`FAIL ${t.id}: ${why}`)
      continue
    }
    fs.renameSync(tmp, out)
    // Windows 会给 DLL 顺手吐 .exp/.lib（`/noimplib` 只挡 .lib）
    for (const stray of [`${out}.exp`, `${out}.lib`]) {
      try {
        fs.rmSync(stray, { force: true })
      } catch {
        /* ignore */
      }
    }
    const sha = sha256File(out)
    const bytes = fs.statSync(out).size
    entries.push({
      id: t.id,
      triple: t.triple,
      kind: t.kind,
      lib: t.lib,
      flags,
      sha256: sha,
      bytes,
    })
    results.push({ id: t.id, ok: true, sha256: sha, bytes })
    log.push(`ok   ${t.id}: ${path.relative(repoRoot, out)} ${bytes}B ${sha.slice(0, 12)}…`)
  }

  // ---- wasm32 目标（与 6 个 native 目标同门禁：改 conv.c 就必须重编） ----
  // `--only` 未点名 wasm32 时沿用旧条目；否则重建（失败则不写条目 ⇒ --check-prebuilt 报「缺」）
  const prev = readPrebuiltManifest(dir)
  const wantWasm = !only || only.has(WASM_TARGET.id)
  let wasmEntry: PrebuiltWasmEntry | undefined
  let wasmOk = true
  if (wantWasm) {
    const w = buildWasm(repoRoot, { outDir: opts.outDir, cc, run })
    if (!w.ok) {
      wasmOk = false
      log.push(`FAIL ${WASM_TARGET.id}: ${w.reason}`)
    } else {
      wasmEntry = wasmEntryOf(dir) ?? undefined
      log.push(
        `ok   ${WASM_TARGET.id}: ${path.relative(repoRoot, w.path)} ${w.bytes}B ${(w.sha256 ?? '').slice(0, 12)}…`,
      )
    }
  } else {
    wasmEntry = prev?.wasm ?? wasmEntryOf(dir) ?? undefined
  }

  // `--only` 时保留未重建目标的旧条目（它们仍在盘上、仍是这份源码编的）
  if (only && prev) {
    for (const e of prev.targets) if (!only.has(e.id)) entries.push(e)
    for (const e of prev.targets) {
      if (only.has(e.id) || results.some((r) => r.id === e.id)) continue
      const p = path.join(dir, e.id, e.lib)
      results.push(
        fs.existsSync(p)
          ? { id: e.id, ok: true, sha256: e.sha256, bytes: e.bytes }
          : { id: e.id, ok: false, reason: `缺 ${path.relative(repoRoot, p)}（--only 未重建）` },
      )
    }
  }

  entries.sort(
    (a, b) =>
      NATIVE_TARGETS.findIndex((t) => t.id === a.id) -
      NATIVE_TARGETS.findIndex((t) => t.id === b.id),
  )
  const manifest: PrebuiltManifest = {
    abi: NATIVE_ABI,
    cc,
    ccVersion,
    sources: nativeLibSourceFiles(repoRoot).map((p) => ({
      path: path.relative(repoRoot, p).replace(/\\/g, '/'),
      sha256: sha256File(p),
    })),
    targets: entries,
    wasm: wasmEntry,
    builtAt: new Date().toISOString(),
  }
  fs.writeFileSync(prebuiltManifestPath(dir), `${JSON.stringify(manifest, null, 2)}\n`, {
    encoding: 'utf8',
  })
  log.push(
    `manifest ok: ${PREBUILT_MANIFEST_NAME}（${entries.length} native 目标` +
      `${wasmEntry ? ' + wasm32' : '（缺 wasm32）'}）`,
  )
  return {
    ok:
      wasmOk &&
      results.every((r) => r.ok) &&
      entries.length === NATIVE_TARGETS.length &&
      (wantWasm ? wasmEntry !== undefined : true),
    dir,
    targets: results,
    manifest,
    log,
  }
}

/**
 * prebuilt 是不是「当前这份源码 / 当前这些文件」编的。空串 = 新鲜。
 *
 * 这就是 T2 的仓库侧门禁（tests/native-prebuilt.test.ts 与 `--check-prebuilt` 共用）：
 * 节点上不做这份判断（见 native-prebuilt.ts 头注 ③），所以**提交时**必须过这一关，
 * 否则节点会拿到一份「源码已改、库还是老的」的 prebuilt（attestation 会拦，但白跑一趟）。
 */
export function prebuiltStaleReason(
  repoRoot: string,
  opts: { outDir?: string; ccVersion?: string } = {},
): string {
  const dir = prebuiltOutDir(repoRoot, opts.outDir)
  const mp = prebuiltManifestPath(dir)
  if (!fs.existsSync(mp)) return `无 prebuilt manifest ${path.relative(repoRoot, mp)}`
  const m = readPrebuiltManifest(dir)
  if (!m) return `prebuilt manifest 不可读: ${mp}`
  if (m.abi !== NATIVE_ABI) return `prebuilt ABI 不符（盘上 ${m.abi}，期望 ${NATIVE_ABI}）`
  if (opts.ccVersion && m.ccVersion !== opts.ccVersion)
    return `prebuilt 是另一版编译器编的（盘上 ${m.ccVersion}）`
  const srcProblem = sourcesStaleReason(repoRoot, m.sources)
  if (srcProblem) return `prebuilt 过期：${srcProblem}`
  // wasm32（与 native 同门禁；缺条目 = 上一次 --cross 没编成，绝不能默默过）
  if (!m.wasm)
    return `prebuilt 缺 ${WASM_TARGET.id} 产物（跑 bun tools/agent/native-build.ts --wasm）`
  const wp = prebuiltWasmPath(dir)
  if (!fs.existsSync(wp)) return `prebuilt 缺 wasm 产物 ${path.relative(repoRoot, wp)}`
  if (sha256File(wp) !== m.wasm.sha256)
    return `prebuilt wasm 产物已变 ${path.relative(repoRoot, wp)}（需 --wasm 重建）`
  if (m.wasm.flags.join(' ') !== wasmBuildFlags().join(' '))
    return `prebuilt wasm flags 已变 ${WASM_TARGET.id}（需 --wasm 重建）`
  for (const t of NATIVE_TARGETS) {
    const e = m.targets.find((x) => x.id === t.id)
    if (!e) return `prebuilt 缺目标 ${t.id}`
    const p = path.join(dir, t.id, e.lib)
    if (!fs.existsSync(p)) return `prebuilt 缺产物 ${path.relative(repoRoot, p)}`
    const sha = sha256File(p)
    if (sha !== e.sha256)
      return `prebuilt 产物已变 ${path.relative(repoRoot, p)}（需 --cross 重建）`
    if (e.flags.join(' ') !== nativeFlags(t).join(' '))
      return `prebuilt flags 已变 ${t.id}（需 --cross 重建）`
  }
  return ''
}

export interface ResolvedNativeLib {
  path: string | null
  /** env 指路 / 入库 prebuilt / 本机 tmp/native 构建 / 无。 */
  kind: 'env' | 'prebuilt' | 'local' | null
  sha: string
  reason: string
}

/**
 * 运行期解析共享库（rollout-runner 与测试共用；src/ 侧的同类解析在 conv_native_adapter.ts，
 * 那是因为 src/ 不许依赖 tools/，两处的**候选顺序**保持一致：env → prebuilt → 本机构建）。
 *
 * prebuilt 排在「本机构建」之前：分发的产物与本机编的是同一配方（同一 flags/链接参数），
 * 而 prebuilt 是**所有节点都会拿到的那一份** —— 让本机也用它，暴露差异的机会最多。
 */
export function resolveNativeLib(
  repoRoot: string,
  opts: { platform?: string; arch?: string } = {},
): ResolvedNativeLib {
  const platform = opts.platform ?? process.platform
  const arch = opts.arch ?? process.arch
  const env = process.env.NN_NATIVE_LIB
  if (env && fs.existsSync(env)) {
    return { path: env, kind: 'env', sha: sha256File(env), reason: 'NN_NATIVE_LIB' }
  }
  if (nativeTargetFor(platform, arch)) {
    const p = path.join(repoRoot, PREBUILT_DIR, `${platform}-${arch}`, nativeLibBasename(platform))
    if (fs.existsSync(p)) {
      return {
        path: p,
        kind: 'prebuilt',
        sha: sha256File(p),
        reason: `prebuilt ${platform}-${arch}`,
      }
    }
  }
  const local = nativeLibPath(repoRoot, { platform })
  if (local)
    return { path: local, kind: 'local', sha: sha256File(local), reason: 'tmp/native（本机构建）' }
  const why =
    nativeTargetFor(platform, arch) === null
      ? `prebuilt 矩阵无 ${platform}-${arch}`
      : `prebuilt 缺 ${platform}-${arch}（跑 bun tools/agent/native-build.ts --cross）且无本机构建`
  return { path: null, kind: null, sha: '', reason: why }
}

function main(): void {
  const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
  const argv = process.argv.slice(2)
  const arg = (name: string, def = ''): string => {
    const i = argv.indexOf(`--${name}`)
    return i >= 0 && argv[i + 1] ? argv[i + 1]! : def
  }
  const outDir = arg('out-dir', DEFAULT_OUT_DIR)
  const cc = arg('cc', process.env.NN_CC ?? 'clang')

  if (argv.includes('--check')) {
    const why = nativeStaleReason(repoRoot, { outDir })
    if (why) {
      console.error(`[native-build] STALE: ${why}`)
      process.exit(1)
    }
    console.log('[native-build] fresh: 指纹与产物一致')
    return
  }

  if (argv.includes('--check-prebuilt')) {
    const why = prebuiltStaleReason(repoRoot, { outDir: arg('prebuilt-dir', PREBUILT_DIR) })
    if (why) {
      console.error(`[native-build] PREBUILT STALE: ${why}`)
      process.exit(1)
    }
    console.log(
      `[native-build] prebuilt fresh: ${NATIVE_TARGETS.length} native 目标 + ${WASM_TARGET.id} 与源码同源`,
    )
    return
  }

  if (argv.includes('--wasm')) {
    const dir = arg('prebuilt-dir', PREBUILT_DIR)
    const w = buildWasm(repoRoot, { outDir: dir, cc })
    if (!w.ok) {
      console.error(`[native-build] WASM FAILED: ${w.reason}`)
      process.exit(1)
    }
    console.log(
      `[native-build] wasm ok: ${path.relative(repoRoot, w.path)} (${w.bytes}B ${(w.sha256 ?? '').slice(0, 12)}…）`,
    )
    // 同步 manifest 的 wasm 条目：不同步的话 --check-prebuilt 会把刚编好的产物判成
    // 「产物已变（需 --wasm 重建）」—— 只重编 wasm 时 native 条目与 sources 保持不变。
    const dirAbs = path.isAbsolute(dir) ? dir : path.join(repoRoot, dir)
    const prev = readPrebuiltManifest(dirAbs)
    const entry = wasmEntryOf(dirAbs)
    if (prev && entry) {
      fs.writeFileSync(
        prebuiltManifestPath(dirAbs),
        `${JSON.stringify({ ...prev, wasm: entry }, null, 2)}\n`,
        { encoding: 'utf8' },
      )
      console.log(`[native-build] manifest ok: wasm 条目已更新`)
    } else {
      console.warn(
        `[native-build] 无 ${PREBUILT_MANIFEST_NAME} ⇒ 只重编了 wasm（--check-prebuilt 仍会红：先跑 --cross）`,
      )
    }
    if (argv.includes('--json')) console.log(JSON.stringify(entry))
    return
  }

  if (argv.includes('--cross')) {
    const only = arg('only')
    const r = buildPrebuilt(repoRoot, {
      outDir: arg('prebuilt-dir', PREBUILT_DIR),
      cc,
      only: only
        ? only
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean)
        : undefined,
    })
    for (const l of r.log) console.log(`[native-build] ${l}`)
    if (!r.ok) {
      console.error('[native-build] CROSS FAILED')
      process.exit(1)
    }
    console.log(
      `[native-build] cross ok: ${path.relative(repoRoot, r.dir)}（${r.targets.length} native 目标` +
        `${r.manifest!.wasm ? ` + ${WASM_TARGET.id}` : ''}，cc=${r.manifest!.ccVersion}）`,
    )
    if (argv.includes('--json')) console.log(JSON.stringify(r.manifest))
    return
  }

  const r = buildNative(repoRoot, { outDir, cc })
  for (const l of r.log) console.log(`[native-build] ${l}`)
  if (!r.ok) {
    console.error(`[native-build] FAILED: ${r.reason}`)
    process.exit(1)
  }
  console.log(
    `[native-build] ok: ${path.relative(repoRoot, r.lib!)} (${r.manifest!.artifacts.lib.bytes}B) ` +
      `+ ${path.relative(repoRoot, r.exe!)} — flags=${r.manifest!.flags.join(' ')} cc=${r.manifest!.ccVersion}`,
  )
  if (argv.includes('--json')) console.log(JSON.stringify(r.manifest))
}

// 只认 bun 的 `import.meta.main`：本文件被 runner/测试 import 时绝不能执行 main()
// （node 打包路径下用 argv[1] 猜会误判，直接不做兜底）。
if ((import.meta as { main?: boolean }).main === true) main()
