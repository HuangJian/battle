/**
 * native-conv.ts —— StudentModel.features 的 native 后端（共享库 + bun:ffi，**同步零 IPC**）。
 *
 * 为什么是 FFI 而不是「CLI + 常驻子进程」（2026-09-21 评审 B1）：
 * features 是**逐决策顺序依赖**的（动作 → 下一状态），跨决策无法批量；而每局有 ~236 次
 * 调用（tmp/perf-sampler-pack.log: samples/game mean=236.2）。本机实测进程启动成本
 * （n=30：hostname 54.4ms / cmd.exe 42.1ms）⇒ 一次调用起一个进程比整局 sim（1338ms）还贵。
 * 共享库 + FFI 每次调用 ~0.5µs（tmp/ffi-probe 实测），且 in16/pooled/bufA 直接以 JS
 * 数组的内存地址传入 —— 连 wasm 路径的「拷进 48KB + 拷回 169KB」都省掉。
 *
 * 只跑在 bun 上（bun:ffi 是 bun 内建；node 无 FFI）。所以 rollout 引擎选择里
 * 「bun+native」是一条独立臂，与 node+wasm 比完再定（见 tools/agent/rollout-runner.ts）。
 * node 下本模块静默返回 null ⇒ 回落 wasm ⇒ 与今天行为逐字节一致。
 *
 * 共享库从哪来（三条路，按优先级；节点上通常只有第 ② 条可用）：
 *   ① `NN_NATIVE_LIB` 显式指路；
 *   ② **入库 prebuilt**（交叉编译随仓库分发，节点无需 clang —— 见 src/nn/native-prebuilt.ts）；
 *   ③ 本机 `bun tools/agent/native-build.ts` 的 tmp/native 产物。
 *
 * 三条铁律（评审 B2/B4）：
 *  ① **首用 attestation**：加载后先拿真实权重跑几次 native 与 wasm 的逐字节对拍，
 *     过了才投产。异质节点各自编译 ⇒ 「本机 8/8 逐位一致」不能外推，只能在**本机**验。
 *     对拍不过 / 库缺失 / ABI 不符 / wasm 参考不可用 ⇒ 立刻关 native 并**响亮**记一行。
 *  ② **不算数就回落**：任何时候 run 抛错 ⇒ 关 native（进程内永久）+ 一行日志，
 *     调用方（infer.features）自动走 wasm/TS —— 绝不静默用可疑内核。
 *  ③ **来源可查**：`featuresEngine()` 暴露本进程实际用过的后端（进 shard manifest 与日志），
 *     避免「以为开了 native 其实一直在 wasm 上跑」。
 *
 * 关闭开关：`NN_NATIVE=0`（强制 off）；库路径覆盖 `NN_NATIVE_LIB=<abs path>`。
 */
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { NATIVE_ABI, nativeLibBasename, nativeTargetFor, PREBUILT_DIR } from './native-prebuilt'

const BOARD = 26
const SP = BOARD * BOARD
const H = 64
const D = 8
const IN_CH = 18
const STEM_W = IN_CH * 576
const DW_W = D * H * 25
const PW_W = D * H * H
/** 权重 blob 的 float 数（顺序必须与 src/nn/native/conv_feats_native.h 一致）。 */
const BLOB_FLOATS = STEM_W + H + DW_W + D * H + PW_W + D * H
const ABI = NATIVE_ABI

export type FeaturesEngine = 'native' | 'wasm' | 'ts'

/**
 * native 路径状态（供 bench / CLI / pipeline 查）：<br>
 *   enabled=false 表示 native 用不了（原因在 reason）；enabled=true 表示本进程已 attest 通过。
 */
export interface NativeStatus {
  available: boolean
  tested: boolean
  reason: string
  libPath: string | null
}

interface ConvModelView {
  in16: Float32Array
  stemW: Float32Array
  stemB: Float32Array
  dwW: Float32Array[]
  dwB: Float32Array[]
  pwW: Float32Array[]
  pwB: Float32Array[]
  pooled: Float32Array
  bufA: Float32Array
}

interface FfiTypes {
  i32: unknown
  u32: unknown
  ptr: unknown
}
interface FfiLib {
  dlopen: (
    p: string,
    def: Record<string, { args: unknown[]; returns: unknown }>,
  ) => { symbols: Record<string, (...a: unknown[]) => number> }
  ptr: (a: ArrayBufferView) => number
  FFIType: FfiTypes
}

interface NativeKernel {
  /** kernel 入口（参数为内存地址）。 */
  run: (wblob: number, in16: number, pooled: number, bufA: number) => number
  /** 一次性权重 blob（实例变化时重建）。 */
  blob: Float32Array | null
  blobOwner: unknown
}

let _status: NativeStatus | null = null
let _kernel: NativeKernel | null = null
let _ffi: FfiLib | null | undefined
let _lastEngine: FeaturesEngine | 'unknown' = 'unknown'
let _reason = ''

/** 间接 require（只查一次）：避免 `bun build --target=node` 在打包期解析 bun:ffi。 */
function requireBunFfi(): FfiLib | null {
  if (_ffi !== undefined) return _ffi
  if (typeof (globalThis as { Bun?: unknown }).Bun === 'undefined') {
    _ffi = null
    return null
  }
  try {
    const req = createRequire(import.meta.url)
    _ffi = req(['bun', 'ffi'].join(':')) as FfiLib
  } catch {
    _ffi = null
  }
  return _ffi
}

/**
 * 候选库路径，按优先级：
 *   ① `NN_NATIVE_LIB`（显式指路，含节点上手工放库的场合）
 *   ② **入库 prebuilt**（`src/nn/native/prebuilt/<platform>-<arch>/…`，模块相对 → cwd 相对）
 *      —— 节点机器多数没有 clang，这是它们在远端唯一能拿到的 native 臂（native-prebuilt.ts）。
 *   ③ 与模块同级 / bundle 同级 / bundle 的 wasm/ 子目录（rollout-runner 会把解析到的库
 *      拷到 bundle 旁，让打包产物也能用相对路径找到它）
 *   ④ cwd（仓根）的 tmp/native：本机 `bun tools/agent/native-build.ts` 的开发产物
 */
export function nativeLibCandidates(): string[] {
  const n = nativeLibBasename(process.platform)
  const out: string[] = []
  const env = process.env.NN_NATIVE_LIB
  if (env) out.push(env)
  const id = `${process.platform}-${process.arch}`
  if (nativeTargetFor(process.platform, process.arch)) {
    // 模块生在 `<repo>/src/nn/` ⇒ 相对它就落到 prebuilt 根；打包产物里这条不存在，
    // 那时靠 cwd（仓根）那条与 ③ 的同级副本（rollout-runner 放的）。
    try {
      out.push(fileURLToPath(new URL(`native/prebuilt/${id}/${n}`, import.meta.url)))
    } catch {
      /* ignore */
    }
    out.push(path.join(process.cwd(), PREBUILT_DIR, id, n))
  }
  for (const rel of [n, `wasm/${n}`]) {
    try {
      out.push(fileURLToPath(new URL(rel, import.meta.url)))
    } catch {
      /* ignore */
    }
  }
  out.push(path.join(process.cwd(), 'tmp', 'native', n))
  return out
}

function resolveLib(): string | null {
  for (const p of nativeLibCandidates()) {
    try {
      if (p && fs.existsSync(p)) return p
    } catch {
      /* ignore */
    }
  }
  return null
}

/** 加载共享库（只做「能不能用」的判定，不 attest）。 */
function loadKernel(): NativeKernel | null {
  if (_kernel) return _kernel
  if (process.env.NN_NATIVE === '0') {
    _reason = 'NN_NATIVE=0 强制关闭'
    return null
  }
  const ffi = requireBunFfi()
  if (!ffi) {
    _reason =
      typeof (globalThis as { Bun?: unknown }).Bun === 'undefined'
        ? '非 bun 运行时（bun:ffi 不可用）'
        : 'bun:ffi 不可用'
    return null
  }
  const libPath = resolveLib()
  if (!libPath) {
    _reason = nativeTargetFor(process.platform, process.arch)
      ? '未找到共享库（prebuilt 缺失 → 跑 bun tools/agent/native-build.ts --cross，或本机构建）'
      : `未找到共享库（prebuilt 矩阵无 ${process.platform}-${process.arch}）`
    return null
  }
  try {
    const lib = ffi.dlopen(libPath, {
      cf_abi: { args: [], returns: ffi.FFIType.u32 },
      cf_student_features: {
        args: [ffi.FFIType.ptr, ffi.FFIType.ptr, ffi.FFIType.ptr, ffi.FFIType.ptr],
        returns: ffi.FFIType.i32,
      },
    })
    const abi = lib.symbols.cf_abi?.()
    if (abi !== ABI) {
      _reason = `ABI 不符（库 ${String(abi)} ≠ 期望 ${ABI}）→ 需重建`
      return null
    }
    const sym = lib.symbols.cf_student_features
    if (!sym) {
      _reason = '库缺 cf_student_features 符号'
      return null
    }
    _kernel = {
      run: (w, i, p, b) => sym(w, i, p, b),
      blob: null,
      blobOwner: null,
    }
    return _kernel
  } catch (e) {
    _reason = `加载失败: ${e instanceof Error ? e.message : String(e)}`
    return null
  }
}

/**
 * 把模型的 7 组权重拼成 kernel 期望的单一 blob（实例变化时重建一次）。
 *
 * **缓存键 = `m.stemW` 的引用身份**（与 wasm 侧 `uploaded !== stemW` 同族），它成立的
 * 前提是**权重视图不可变**：`src/` 里模型由 `buildModelFromText` 一次性构建，之后没有任何
 * 原地改权重的路径（导出器每局新建模型、权重文件按内容寻址 ⇒ 每局都是新数组）。
 * 若将来出现「同一批 Float32Array 原地换权重」（如 load_state 复用 buffer），**这里与 wasm 的
 * 上传缓存都会静默继续用旧权重**（attestation 已过、更难察觉）—— 那时的正确改法是换键
 * （wver / 内容指纹）或在那条路径里显式重建视图（`resetNativeConvForTest()` 是现成的钩子），
 * 不要只改一侧。
 */
function ensureBlob(k: NativeKernel, m: ConvModelView): Float32Array | null {
  if (k.blob && k.blobOwner === m.stemW) return k.blob
  const sizes = [
    m.stemW.length,
    m.stemB.length,
    ...m.dwW.map((a) => a.length),
    ...m.dwB.map((a) => a.length),
    ...m.pwW.map((a) => a.length),
    ...m.pwB.map((a) => a.length),
  ]
  const total = sizes.reduce((s, n) => s + n, 0)
  if (total !== BLOB_FLOATS) return null
  const blob = new Float32Array(total)
  let o = 0
  for (const a of [m.stemW, m.stemB]) {
    blob.set(a, o)
    o += a.length
  }
  for (const group of [m.dwW, m.dwB, m.pwW, m.pwB]) {
    for (const a of group) {
      blob.set(a, o)
      o += a.length
    }
  }
  k.blob = blob
  k.blobOwner = m.stemW
  return blob
}

/** 形状守卫：与 wasm 侧同一套契约（h64/d8/board26/18ch），不符直接回落。 */
function shapeOk(m: ConvModelView): boolean {
  return (
    m.dwW.length === D &&
    m.dwB.length === D &&
    m.pwW.length === D &&
    m.pwB.length === D &&
    m.stemW.length === STEM_W &&
    m.stemB.length === H &&
    !!m.bufA &&
    m.bufA.length === H * SP &&
    m.pooled.length === H &&
    m.in16.length === IN_CH * SP
  )
}

interface AttestRef {
  /** 参考实现（wasm runner）；attestation 只认 wasm —— 缺它就不开 native。 */
  runWasm: (m: unknown) => boolean
}

/**
 * 首用 attestation：真实权重 + 确定性伪随机输入 ×3，native 与 wasm 逐字节对拍。
 * 通过 ⇒ available；任何一处不符/抛错 ⇒ 关 native（响亮）。
 */
function attest(k: NativeKernel, m: ConvModelView, ref: AttestRef): boolean {
  const blob = ensureBlob(k, m)
  if (!blob) {
    _reason = '权重 blob 尺寸不符（架构不是 h64/d8/18ch）'
    return false
  }
  const wblobPtr = requireBunFfi()!.ptr(blob)
  const keepIn16 = Float32Array.from(m.in16)
  const keepPooled = Float32Array.from(m.pooled)
  const keepBufA = Float32Array.from(m.bufA)
  let seed = 20260921 >>> 0
  const rnd = (): number => {
    seed = (seed + 0x6d2b79f5) >>> 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  try {
    for (let t = 0; t < 3; t++) {
      for (let i = 0; i < m.in16.length; i++) m.in16[i] = (rnd() - 0.5) * 2
      // 参考：wasm（同一进程、同一权重）
      const wasmOk = ref.runWasm(m)
      if (!wasmOk) {
        _reason = 'attestation 缺参考（wasm 不可用）'
        return false
      }
      const wPooled = Float32Array.from(m.pooled)
      const wBufA = Float32Array.from(m.bufA)
      const ffi = requireBunFfi()!
      const rc = k.run(wblobPtr, ffi.ptr(m.in16), ffi.ptr(m.pooled), ffi.ptr(m.bufA))
      if (rc !== 0) {
        _reason = `kernel 返回 ${rc}`
        return false
      }
      const eqP = Buffer.from(m.pooled.buffer, m.pooled.byteOffset, m.pooled.byteLength).equals(
        Buffer.from(wPooled.buffer, wPooled.byteOffset, wPooled.byteLength),
      )
      const eqB = Buffer.from(m.bufA.buffer, m.bufA.byteOffset, m.bufA.byteLength).equals(
        Buffer.from(wBufA.buffer, wBufA.byteOffset, wBufA.byteLength),
      )
      if (!eqP || !eqB) {
        _reason = `attestation 失败 trial=${t}（pooled 逐位=${eqP} bufA 逐位=${eqB}）→ 本机 native 内核与 wasm 不一致，回落`
        return false
      }
    }
    _reason = 'ok'
    return true
  } finally {
    // 还原调用方缓冲（attestation 的随机输入不得留在现场）
    m.in16.set(keepIn16)
    m.pooled.set(keepPooled)
    m.bufA.set(keepBufA)
  }
}

/**
 * features 的 native 入口（与 runStudentConvWasm 同契约：true = pooled+bufA 已填）。
 * 任何不确定 ⇒ false，调用方走 wasm/TS。
 */
export function runStudentConvNative(model: unknown, ref: AttestRef): boolean {
  const m = model as ConvModelView
  if (!shapeOk(m)) {
    _reason = _reason || '架构不符（非 h64/d8/26/18ch）'
    return false
  }
  const k = loadKernel()
  if (!k) return false
  if (!_status?.tested) {
    const ok = attest(k, m, ref)
    _status = {
      available: ok,
      tested: true,
      reason: _reason,
      libPath: resolveLib(),
    }
    if (ok) {
      console.log(`[features] native 启用（lib=${_status.libPath}，attest=3/3 逐字节 vs wasm）`)
    } else {
      console.warn(`[features] native 关闭: ${_status.reason} → 回落 wasm/TS`)
    }
    if (!ok) return false
  }
  if (!_status.available) return false
  const blob = ensureBlob(k, m)
  if (!blob) return false
  try {
    const ffi = requireBunFfi()
    if (!ffi) return false
    const rc = k.run(ffi.ptr(blob), ffi.ptr(m.in16), ffi.ptr(m.pooled), ffi.ptr(m.bufA))
    if (rc !== 0) throw new Error(`kernel rc=${rc}`)
    _lastEngine = 'native'
    return true
  } catch (e) {
    // 运行期异常 = 内核不可信 ⇒ 本进程永久关闭（不静默重试）
    _status = {
      available: false,
      tested: true,
      reason: `运行期异常: ${e instanceof Error ? e.message : String(e)}`,
      libPath: resolveLib(),
    }
    console.warn(`[features] native 运行期异常 → 本进程关闭: ${_status.reason}`)
    return false
  }
}

/** 记录「本次走了 wasm」。 */
export function noteFeaturesEngine(engine: FeaturesEngine): void {
  _lastEngine = engine
}

/** 本进程最近一次 features 实际用的后端（账本/日志用）。 */
export function featuresEngine(): FeaturesEngine | 'unknown' {
  return _lastEngine
}

/** native 可用性（不触发 attestation；供 bench/CLI 打印）。 */
export function nativeStatus(): NativeStatus {
  if (_status) return _status
  const k = loadKernel()
  return {
    available: false,
    tested: false,
    reason: k ? '未 attest' : _reason,
    libPath: resolveLib(),
  }
}

/**
 * 基准/测试用开关：false = 本进程内强制关 native（比如要量纯 wasm/TS 的臂），
 * true = 回到「未探测」（下次调用重新 attest）。生产路径不调用。
 *
 * 为什么不复用 NN_NATIVE：env 只在首次 loadKernel 时读，已经加载过的进程改不了；
 * 而 perf 工具需要在同一进程里依次量 native / wasm / TS 三条臂。
 */
export function setNativeConvEnabled(enabled: boolean): void {
  if (enabled) {
    _status = null
    return
  }
  _status = {
    available: false,
    tested: true,
    reason: 'setNativeConvEnabled(false)（基准/测试强制关闭）',
    libPath: resolveLib(),
  }
}

/** 测试用：清空模块级缓存，回到「未探测」。 */
export function resetNativeConvForTest(): void {
  _status = null
  _kernel = null
  _lastEngine = 'unknown'
  _reason = ''
}

/**
 * dummy 权重下的 kernel 稳态微基准（rollout 引擎选择用；**不经 attestation** ——
 * 它只量速度、不产数据，故不涉及正确性主张）。返回 ms/次；native 不可用返回 null。
 */
export function benchNativeKernel(rounds = 2, warm = 15, iters = 30): number | null {
  const k = loadKernel()
  if (!k) return null
  const ffi = requireBunFfi()
  if (!ffi) return null
  const blob = new Float32Array(BLOB_FLOATS)
  blob.fill(0.01)
  const in16 = new Float32Array(IN_CH * SP)
  in16.fill(0.5)
  const pooled = new Float32Array(H)
  const bufA = new Float32Array(H * SP)
  const w = ffi.ptr(blob)
  const i = ffi.ptr(in16)
  const p = ffi.ptr(pooled)
  const b = ffi.ptr(bufA)
  let best = Number.POSITIVE_INFINITY
  try {
    for (let r = 0; r < Math.max(1, rounds); r++) {
      for (let n = 0; n < warm; n++) k.run(w, i, p, b)
      const t0 = performance.now()
      for (let n = 0; n < iters; n++) k.run(w, i, p, b)
      best = Math.min(best, (performance.now() - t0) / iters)
    }
  } catch {
    return null
  }
  return Number.isFinite(best) ? best : null
}
