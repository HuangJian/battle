/**
 * tools/perf/conv-ab.ts —— 卷积内核 **旧 vs 新** 的 A/B 探针（逐位等价 + 稳态计时）。
 *
 * 为什么要有它（plan `plan/conv-optimize.plan.md` §6.4 第 1 项 + §2.5；该计划 2026-09-23 从
 * `src/nn/conv/` 移出，理由见 `docs/nn/runtime-opt.md` §17）：
 *   优化内核的最低证据是两条：① **逐位等价**（新内核与原内核在同一份权重/输入下输出
 *   逐字节相同）② **实测加速**。两者都必须是「跑一次就能复现」的动作，而不是提交信息里的
 *   一行数字 —— 尤其 arm64 的计时**只能在 arm64 机器上测**（训练机是 x64，交叉编译出来的
 *   库没法在这里跑），必须留一个能带走、在 a95/mac 上直接跑的工具。
 *
 * 对比的两侧：
 *   · 旧内核 = **git 历史里的那份**（`src/nn/native/conv_feats_native.c` + `src/nn/wasm/conv_feats.wasm`，
 *     2026-09-23 重组前的路径）—— 从对象库里取出来现编/现装，不需要旧工作区。
 *   · 新内核 = 盘上现役的 `src/nn/conv/conv.c` 编出的产物（native 走 prebuilt/本机构建，
 *     wasm 走 `src/nn/conv/prebuilt/wasm/conv.wasm`）。
 *
 * 用法（仓根）：
 *   bun tools/perf/conv-ab.ts                # 默认 40 iters × 3 rounds（每侧取最优）
 *   bun tools/perf/conv-ab.ts 100 5          # 更长的稳态测量
 *   CONV_AB_OLD_REF=<ref> bun tools/perf/conv-ab.ts   # 换旧内核所在的提交（默认自动定位）
 *   CONV_AB_OLD_LIB=<path> bun tools/perf/conv-ab.ts  # 直接用现成的旧库（无编译器时，或要与
 *                                                   # 历史 prebuilt 产物对齐时 —— 跳过现编）
 *
 * 旧侧定位：默认用 `git log --diff-filter=AM -1 -- <旧路径>`（= 最后一次**增改**该路径的提交，
 * 也就是它被删除前的那一版）—— 这样**重组之后仍然能跑**，不会因为 HEAD 上已无旧文件而失效。
 * ⚠ 不能用 `git rev-list -1 HEAD -- <路径>`：它把**删除该路径的提交**也算作「改动」，
 * 于是重组提交一旦进历史就会命中删除提交（其上文件已不存在）。2026-09-23 在 arm64 上就是这样
 * 静默跳过了 wasm 对比（`[wasm-old] … 上无 src/nn/wasm/conv_feats.wasm ⇒ 跳过`）。
 * 另：native 源码与 wasm 产物**最后一次增改未必同一次提交**，故各自定位。
 *
 * 输出：每侧的 ms/forward + `old/new` 加速比 + 四方（native old/new × wasm old/new）的
 * 逐位等价矩阵。全绿时最后一行 `[verdict]` 为 OK。
 */
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { nativeFlags, nativeLinkArgs } from '../../src/nn/conv/native-prebuilt'
import { hostTarget, resolveNativeLib } from '../agent/native-build'

const ROOT = path.resolve(import.meta.dir, '..', '..')
const WORK = path.join(ROOT, 'tmp', 'conv-ab')
fs.mkdirSync(WORK, { recursive: true })

/** 重组前的路径（旧内核的取址）。 */
const OLD_NATIVE_SRC = 'src/nn/native/conv_feats_native.c'
const OLD_NATIVE_HDR = 'src/nn/native/conv_feats_native.h'
const OLD_WASM = 'src/nn/wasm/conv_feats.wasm'
/** 重组后的新址。 */
const NEW_WASM = path.join(ROOT, 'src', 'nn', 'conv', 'prebuilt', 'wasm', 'conv.wasm')

const BOARD = 26
const SP = BOARD * BOARD
const H = 64
const D = 8
const IN_CH = 18
const STEM_W = IN_CH * 576
const DW_W = D * H * 25
const PW_W = D * H * H
const BLOB = STEM_W + H + DW_W + D * H + PW_W + D * H

const req = createRequire(import.meta.url)
const ffi = req('bun:ffi') as {
  dlopen: (
    p: string,
    d: Record<string, unknown>,
  ) => { symbols: Record<string, (...a: number[]) => number> }
  ptr: (a: ArrayBufferView) => number
  FFIType: { ptr: unknown; i32: unknown; u32: unknown }
}

const ITERS = Number(process.argv[2] ?? 40)
const ROUNDS = Number(process.argv[3] ?? 3)

function say(m: string): void {
  console.log(m)
}

function git(ref: string, args: string[]): Buffer {
  const r = spawnSync('git', [ref, ...args], { cwd: ROOT, maxBuffer: 64 * 1024 * 1024 })
  if (r.status !== 0) {
    console.error(`git ${ref} ${args.join(' ')} failed: ${String(r.stderr)}`)
    process.exit(2)
  }
  return r.stdout
}

/** 旧产物所在提交：最后一次**增改**该路径的那一版（删除提交被 `--diff-filter=AM` 排除）。
 *  `CONV_AB_OLD_REF` 可整体覆盖（两侧同用一格提交）。 */
function refFor(p: string, what: string): string {
  const env = process.env.CONV_AB_OLD_REF
  if (env) return env
  const r = spawnSync('git', ['log', '--diff-filter=AM', '-1', '--format=%H', '--', p], {
    cwd: ROOT,
    encoding: 'utf8',
  })
  const ref = (r.stdout ?? '').trim()
  if (r.status !== 0 || !ref) {
    console.error(`无法定位${what}（git log --diff-filter=AM -- ${p}）`)
    process.exit(2)
  }
  return ref
}

/** 确定性 LCG（与 tests/native-parity 同口径），保证两侧喂同一份数据。 */
function makeRng(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
function fill(rng: () => number, n: number, scale = 0.4): Float32Array {
  const a = new Float32Array(n)
  for (let i = 0; i < n; i++) a[i] = (rng() - 0.5) * scale
  return a
}
const bytesOf = (a: Float32Array): Buffer => Buffer.from(a.buffer, a.byteOffset, a.byteLength)

const rng = makeRng(20260922)
const blob = new Float32Array(BLOB)
{
  // 按 CF_BLOB_FLOATS 的顺序填（顺序对两侧都一样，逐位对拍才有意义）
  let o = 0
  for (const n of [
    STEM_W,
    H,
    ...Array.from({ length: D }, () => H * 25),
    ...Array.from({ length: D }, () => H),
    ...Array.from({ length: D }, () => H * H),
    ...Array.from({ length: D }, () => H),
  ]) {
    blob.set(fill(rng, n), o)
    o += n
  }
}
const in16 = fill(rng, IN_CH * SP, 2)

/** 稳态计时：两侧**按轮交错**（同一轮先 A 后 B，逐轮交替），每侧取各轮最优。
 *
 * 为什么必须交错（2026-09-23 实测）：原先「先跑完 A 的所有轮次、再跑 B」时，同一份二进制
 * 跨相位能差 ±4%（本机同时跑着其它东西 + 手机/节点的频率漂移），而这个量级**与要读的差异
 * 同阶** ⇒ 会把「变体更慢 2%」读成「更快 2%」。交错后两侧共享同一段机器状态，噪声主要落进
 * 比值以外的绝对值里。 */
function benchPair(a: () => void, b: () => void): [number, number] {
  let bestA = Number.POSITIVE_INFINITY
  let bestB = Number.POSITIVE_INFINITY
  for (let r = 0; r < ROUNDS; r++) {
    for (let i = 0; i < 8; i++) {
      a()
      b()
    }
    let t = performance.now()
    for (let i = 0; i < ITERS; i++) a()
    bestA = Math.min(bestA, (performance.now() - t) / ITERS)
    t = performance.now()
    for (let i = 0; i < ITERS; i++) b()
    bestB = Math.min(bestB, (performance.now() - t) / ITERS)
  }
  return [bestA, bestB]
}

/** 旧库直接指定：没有本地编译器（如 WSL 只有 gcc、节点机器没工具链）或要与**历史 prebuilt 产物**
 *  对齐时用。取法示例：`git show <ref>:src/nn/native/prebuilt/linux-x64/conv_feats_native.so > old.so`。 */
const OLD_LIB_ENV = process.env.CONV_AB_OLD_LIB ?? ''

const REF = refFor(OLD_NATIVE_SRC, '旧 native 内核')
const REF_WASM = refFor(OLD_WASM, '旧 wasm 产物')
say(
  `[env] ${process.platform}-${process.arch} · bun ${process.versions.bun} · 旧 native @ ${REF}` +
    (REF_WASM === REF ? '' : ` · 旧 wasm @ ${REF_WASM}`) +
    (OLD_LIB_ENV ? ` · 旧库=env ${OLD_LIB_ENV}` : ''),
)

/* ─────────────────── native：旧源码从 git 取出来，用**同一套 flags** 现编 ─────────────────── */

const OLD_C = path.join(WORK, 'old_conv.c')
const OLD_H = path.join(WORK, 'conv_feats_native.h')

const target = hostTarget()
const OLD_LIB = OLD_LIB_ENV ? path.resolve(OLD_LIB_ENV) : path.join(WORK, `old_${target.lib}`)

function prepareOldLib(): void {
  if (OLD_LIB_ENV) {
    if (!fs.existsSync(OLD_LIB)) {
      console.error(`CONV_AB_OLD_LIB 指向的文件不存在：${OLD_LIB}`)
      process.exit(2)
    }
    return
  }
  fs.writeFileSync(OLD_C, git('show', [`${REF}:${OLD_NATIVE_SRC}`]))
  fs.writeFileSync(OLD_H, git('show', [`${REF}:${OLD_NATIVE_HDR}`]))
  if (fs.existsSync(OLD_LIB)) return
  const cc = process.env.NN_CC ?? 'clang'
  const args = [
    ...nativeFlags(target),
    // 交叉/本机两种情形都不给 --target：这里要的是**本机可 dlopen** 的库
    ...nativeLinkArgs(target),
    '-I',
    WORK,
    '-o',
    OLD_LIB,
    OLD_C,
  ]
  const r = spawnSync(cc, args, { encoding: 'utf8', windowsHide: true })
  if (r.status !== 0 || !fs.existsSync(OLD_LIB)) {
    console.error(
      `旧内核编译失败 rc=${r.status}：${String(r.stderr).split(/\r?\n/)[0] ?? ''}\n` +
        `（需要 clang + lld；命令：${cc} ${args.join(' ')}）`,
    )
    process.exit(2)
  }
}
prepareOldLib()

const resolved = resolveNativeLib(ROOT)
if (!resolved.path) {
  console.error(`[native] 无现役库可用：${resolved.reason}`)
  process.exit(2)
}
say(`[native] 新库来源=${resolved.kind} ${path.relative(ROOT, resolved.path).replace(/\\/g, '/')}`)

const symDef = {
  cf_abi: { args: [], returns: ffi.FFIType.u32 },
  cf_student_features: {
    args: [ffi.FFIType.ptr, ffi.FFIType.ptr, ffi.FFIType.ptr, ffi.FFIType.ptr],
    returns: ffi.FFIType.i32,
  },
}
const libOld = ffi.dlopen(OLD_LIB, symDef)
const libNew = ffi.dlopen(resolved.path, symDef)

const wb = ffi.ptr(blob)
const ip = ffi.ptr(in16)
const pOld = new Float32Array(H)
const aOld = new Float32Array(H * SP)
const pNew = new Float32Array(H)
const aNew = new Float32Array(H * SP)

const rcO = libOld.symbols.cf_student_features(wb, ip, ffi.ptr(pOld), ffi.ptr(aOld))
const rcN = libNew.symbols.cf_student_features(wb, ip, ffi.ptr(pNew), ffi.ptr(aNew))
if (rcO !== 0 || rcN !== 0) {
  console.error(`kernel rc old=${rcO} new=${rcN}`)
  process.exit(2)
}
const eqPooled = bytesOf(pOld).equals(bytesOf(pNew))
const eqBufA = bytesOf(aOld).equals(bytesOf(aNew))
say(`[native] abi old=${libOld.symbols.cf_abi()} new=${libNew.symbols.cf_abi()}`)
say(`[native] bitexact pooled=${eqPooled} bufA=${eqBufA}`)
const [tOldN, tNewN] = benchPair(
  () => void libOld.symbols.cf_student_features(wb, ip, ffi.ptr(pOld), ffi.ptr(aOld)),
  () => void libNew.symbols.cf_student_features(wb, ip, ffi.ptr(pNew), ffi.ptr(aNew)),
)
say(
  `[native] ms/forward old=${tOldN.toFixed(4)} new=${tNewN.toFixed(4)} speedup=${(tOldN / tNewN).toFixed(3)}x`,
)

/* ─────────────────── wasm：旧产物从 git blob 取，新产物现址读 ─────────────────── */

interface WasmSide {
  feats: (...a: number[]) => void
  buf: () => Buffer
}

function loadNewWasm(): WasmSide {
  const bytes = fs.readFileSync(NEW_WASM)
  const inst = new WebAssembly.Instance(new WebAssembly.Module(bytes))
  const mem = inst.exports.memory as WebAssembly.Memory
  say(`[wasm-new] initial memory = ${mem.buffer.byteLength} bytes`)
  const base = Math.max(1 << 20, mem.buffer.byteLength)
  const need = base + BLOB * 4 + IN_CH * SP * 4 + H * 4 + H * SP * 4
  const grow = Math.ceil((need - mem.buffer.byteLength) / 65536)
  if (grow > 0) mem.grow(grow)
  const f32At = (o: number): Float32Array => new Float32Array(mem.buffer, o)
  const oBlob = base
  const oIn = base + BLOB * 4
  const oPooled = oIn + IN_CH * SP * 4
  const oBufA = oPooled + H * 4
  f32At(oBlob).set(blob)
  f32At(oIn).set(in16)
  const feats = inst.exports['cf_student_features'] as (...a: number[]) => void
  return {
    feats: (...a) => void feats(oBlob, oIn, oPooled, oBufA, ...a),
    buf: () =>
      Buffer.concat([
        bytesOf(f32At(oPooled).subarray(0, H)),
        bytesOf(f32At(oBufA).subarray(0, H * SP)),
      ]),
  }
}

function loadOldWasm(): WasmSide | null {
  const r = spawnSync('git', ['show', `${REF_WASM}:${OLD_WASM}`], {
    cwd: ROOT,
    maxBuffer: 64 * 1024 * 1024,
  })
  if (r.status !== 0) {
    console.warn(`[wasm-old] ${REF_WASM} 上无 ${OLD_WASM} ⇒ 跳过 wasm 对比`)
    return null
  }
  const inst = new WebAssembly.Instance(new WebAssembly.Module(r.stdout))
  const mem = inst.exports.memory as WebAssembly.Memory
  say(`[wasm-old] initial memory = ${mem.buffer.byteLength} bytes`)
  const base = 1 << 20
  const need = base + BLOB * 4 + IN_CH * SP * 4 + 3 * H * SP * 4 + H * 4
  const grow = Math.ceil((need - mem.buffer.byteLength) / 65536)
  if (grow > 0) mem.grow(grow)
  const f32At = (o: number): Float32Array => new Float32Array(mem.buffer, o)
  const oStemW = base
  const oStemB = oStemW + STEM_W * 4
  const oDwW = oStemB + H * 4
  const oDwB = oDwW + DW_W * 4
  const oPwW = oDwB + D * H * 4
  const oPwB = oPwW + PW_W * 4
  const oIn = oPwB + D * H * 4
  const oBufA = oIn + IN_CH * SP * 4
  const oBufB = oBufA + H * SP * 4
  const oBufC = oBufB + H * SP * 4
  const oPooled = oBufC + H * SP * 4
  // 注意：oXxx 是**字节**偏移，blob.subarray 收**元素**下标 ⇒ 单独算一份元素偏移
  const eStemW = 0
  const eStemB = eStemW + STEM_W
  const eDwW = eStemB + H
  const eDwB = eDwW + DW_W
  const ePwW = eDwB + D * H
  const ePwB = ePwW + PW_W
  f32At(oStemW).set(blob.subarray(eStemW, eStemW + STEM_W))
  f32At(oStemB).set(blob.subarray(eStemB, eStemB + H))
  f32At(oDwW).set(blob.subarray(eDwW, eDwW + DW_W))
  f32At(oDwB).set(blob.subarray(eDwB, eDwB + D * H))
  f32At(oPwW).set(blob.subarray(ePwW, ePwW + PW_W))
  f32At(oPwB).set(blob.subarray(ePwB, ePwB + D * H))
  f32At(oIn).set(in16)
  const feats = inst.exports['features'] as (...a: number[]) => void
  return {
    feats: () =>
      void feats(oIn, oStemW, oStemB, oDwW, oDwB, oPwW, oPwB, oBufA, oBufB, oBufC, oPooled),
    buf: () =>
      Buffer.concat([
        bytesOf(f32At(oPooled).subarray(0, H)),
        bytesOf(f32At(oBufA).subarray(0, H * SP)),
      ]),
  }
}

const wasmNew = loadNewWasm()
const wasmOld = loadOldWasm()
const natOldBuf = Buffer.concat([bytesOf(pOld), bytesOf(aOld)])
const natNewBuf = Buffer.concat([bytesOf(pNew), bytesOf(aNew)])
const sides: Array<[string, Buffer]> = [
  ['nat-old', natOldBuf],
  ['nat-new', natNewBuf],
]
if (wasmOld) {
  wasmOld.feats()
  wasmNew.feats()
  sides.push(['wasm-old', wasmOld.buf()], ['wasm-new', wasmNew.buf()])
  // 诊断：差异量级（小 = 数值累加差异；巨大 = 布局/越界）
  const maxDiff = (x: Buffer, y: Buffer): number => {
    let m = 0
    for (let i = 0; i < Math.min(x.length, y.length) / 4; i++) {
      m = Math.max(m, Math.abs(x.readFloatLE(i * 4) - y.readFloatLE(i * 4)))
    }
    return m
  }
  const nH = H * 4
  const wOld = wasmOld.buf()
  const wNew = wasmNew.buf()
  say(`[wasm] maxdiff pooled=${maxDiff(wOld.subarray(0, nH), wNew.subarray(0, nH))}`)
  say(`[wasm] maxdiff bufA=${maxDiff(wOld.subarray(nH), wNew.subarray(nH))}`)
  // 幂等性：同一边连跑两次，输出必须逐字节相同（否则动了自己的 scratch/输出区）
  wasmOld.feats()
  say(`[wasm-old] rerun bitexact=${wasmOld.buf().equals(wOld)}`)
  wasmNew.feats()
  say(`[wasm-new] rerun bitexact=${wasmNew.buf().equals(wNew)}`)
}

let allEq = true
for (const [an, ab] of sides) {
  for (const [bn, bb] of sides) {
    if (an >= bn) continue
    const eq = ab.equals(bb)
    if (!eq) allEq = false
    say(`[eq] ${an} == ${bn}: ${eq}`)
  }
}

if (wasmOld) {
  const [tOldW, tNewW] = benchPair(
    () => wasmOld.feats(),
    () => wasmNew.feats(),
  )
  say(
    `[wasm] ms/forward old=${tOldW.toFixed(4)} new=${tNewW.toFixed(4)} speedup=${(tOldW / tNewW).toFixed(3)}x`,
  )
  say(`[wasm-vs-native] new wasm/native = ${(tNewW / tNewN).toFixed(3)}x（>1 表示 native 更快）`)
}
say(`[verdict] bitexact=${allEq ? 'OK' : 'MISMATCH'}（${sides.length} 方互比）`)
if (!allEq) process.exit(1)
