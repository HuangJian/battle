/**
 * tools/perf/kernel-phases.ts —— **内核逐阶段**计时台架：那 6.4 ms（或 1.5 ms）到底花在哪。
 *
 * ## 为什么需要它
 *
 * `conv-ab.ts` 只回答「整批优化值多少」，`kernel-variants.ts` 回答「**某一项改动**值多少」，
 * 但**想知道时间花在哪一段**（pad3 / conv3 / pad5 / dw / pw / GAP）只能直接量。
 * 2026-09-23 的触发问题：四项优化在 arm64 上只值 ≈5%（x64 是 +44%）——是某项拖后腿，
 * 还是**这些机制在 NEON 上本来就不值钱**（例如瓶颈是 FP 端口而不是取数端口）？逐阶段占比能直接分辨。
 *
 * ## 做法（**不改生产源码**）
 *
 * 读 `src/nn/conv/conv.c`，用**断言过的文本替换**把计时宏插到各阶段调用两侧，生成一份副本
 * （留在 `tmp/kernel-phases/conv.c`，可 `diff -u` 复核）；再追加两个导出：
 *   · `cf_phase_profile(wblob, in16, pooled, bufA, out[8], reset)` —— 跑一次 `cf_student_features`
 *     并把累计 ticks 抄进 `out`（0=总计 1=pad3 2=conv3 3=pad5 4=dw 5=pw 6=GAP）
 *   · `cf_phase_timer_hz()` —— aarch64 直接给 `cntfrq_el0`；x86 的 TSC 频率由驱动用墙钟标定
 * 计时用 aarch64 的 `cntvct_el0` / x86 的 `rdtsc`（**只读计数器，无副作用**）⇒ 数值与生产库逐字节相同
 * （驱动默认自带一致性校验：同一份输入下与生产库 memcmp 对拍）。
 *
 * 插桩开销 ≈ 7 个边界 × ~20–40 cycle/次调用，相对一次 6.4 ms 的调用可忽略；两侧共享同一份
 * 机器状态 ⇒ 占比可信（绝对值仍受频率漂移影响，见 §11.4 的钉核教训）。
 *
 * ## 用法
 *
 *   bun tools/perf/kernel-phases.ts                     # 本机构建 + 测量（x64 校验：占比应与 §1.3 一致）
 *   bun tools/perf/kernel-phases.ts --target linux-arm64 --iters 300
 *   bun tools/perf/kernel-phases.ts --lib /tmp/kernel-phases.so --iters 300   # 无编译器的机器：只测已有库
 *   taskset -c 7 bun tools/perf/kernel-phases.ts --lib /tmp/kernel-phases.so 300   # 手机上必须钉核
 *
 * 已知 MAC（用于算 GMAC/s，见 plan §1.3）：conv3 7.0088M · dw 8.6528M · pw 22.1512M（合计 37.8128M）。
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { NATIVE_TARGETS, nativeFlags, nativeLinkArgs } from '../../src/nn/conv/native-prebuilt'
import { hostTarget, resolveNativeLib } from '../agent/native-build'

const ROOT = path.resolve(import.meta.dir, '..', '..')
const WORK = path.join(ROOT, 'tmp', 'kernel-phases')
const SRC = path.join(ROOT, 'src', 'nn', 'conv', 'conv.c')

const BOARD = 26
const SP = BOARD * BOARD
const H = 64
const D = 8
const IN_CH = 18
const STEM_W = IN_CH * 576
const DW_W = D * H * 25
const PW_W = D * H * H
const BLOB = STEM_W + H + DW_W + D * H + PW_W + D * H

const MACS = [0, 0, 64 * SP * IN_CH * 9, 0, D * H * SP * 25, D * H * SP * H, 0]
const NAMES = ['total', 'pad3', 'conv3', 'pad5×8', 'dw×8', 'pw×8', 'GAP']

/* ── 参数 ─────────────────────────────────────────────────────────────────── */
const argv = process.argv.slice(2)
function opt(name: string): string | null {
  const i = argv.indexOf(name)
  return i >= 0 ? (argv[i + 1] ?? null) : null
}
const ITERS = Number(argv.find((a) => /^\d+$/.test(a)) ?? 200)
const TARGET_ID = opt('--target')
const LIB_ARG = opt('--lib')
/** 额外编译 flags（实验用，例：`KP_EXTRA_FLAGS=-ffp-contract=fast` 量 FMA 潜力）。
 *  给了它就**必然破坏逐位一致** ⇒ 自动关掉下面的一致性对拍（并在开头响亮标注）。 */
const EXTRA = (process.env.KP_EXTRA_FLAGS ?? '').split(/\s+/).filter(Boolean)
/** 跳过「插桩库 == 生产库」对拍：只该在**故意破契约**的实验库上（`--lib` 跑别处编好的实验库时用）。 */
const NO_EQ = argv.includes('--no-eq')

/* ── 注入插桩（断言过的文本替换；命中数 ≠1 即中止，不写盘） ───────────────── */
const HDR = `/* ⚠ 由 tools/perf/kernel-phases.ts 注入的计时插桩；生产 conv.c 不含这些。 */
static unsigned long long cf_phase_ticks[8];
static inline unsigned long long cf_ticks_now(void) {
#if defined(__aarch64__) || defined(__arm64__)
  unsigned long long v;
  __asm__ volatile("mrs %0, cntvct_el0" : "=r"(v));
  return v;
#elif defined(__x86_64__) || defined(__i386__)
  return (unsigned long long)__builtin_ia32_rdtsc();
#else
  return 0ull;
#endif
}
static inline unsigned long long cf_timer_hz(void) {
#if defined(__aarch64__) || defined(__arm64__)
  unsigned long long v;
  __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v));
  return v;
#else
  return 0ull; /* x86：TSC 频率由驱动用墙钟标定 */
#endif
}
#define CF_PH_BEG(i) cf_phase_ticks[(i)] -= cf_ticks_now()
#define CF_PH_END(i) cf_phase_ticks[(i)] += cf_ticks_now()
`

const HUNKS: Array<[string, string]> = [
  [
    `  f32* bufB = bufB_store;

  pad3(in16, CF_IN_CH, pad3_);
  conv3(pad3_, stemW, stemB, bufA, CF_IN_CH, CF_H);
  for (int i = 0; i < CF_D; i++) {
    pad5(bufA, pad5_);
    conv5dw(pad5_, dwW + i * CF_H * 25, dwB + i * CF_H, bufB);
    conv1x1_res(bufB, pwW + i * CF_H * CF_H, pwB + i * CF_H, bufA);
  }
  for (int c = 0; c < CF_H; c++) {`,
    `  f32* bufB = bufB_store;

  CF_PH_BEG(0);
  CF_PH_BEG(1); pad3(in16, CF_IN_CH, pad3_); CF_PH_END(1);
  CF_PH_BEG(2); conv3(pad3_, stemW, stemB, bufA, CF_IN_CH, CF_H); CF_PH_END(2);
  for (int i = 0; i < CF_D; i++) {
    CF_PH_BEG(3); pad5(bufA, pad5_); CF_PH_END(3);
    CF_PH_BEG(4); conv5dw(pad5_, dwW + i * CF_H * 25, dwB + i * CF_H, bufB); CF_PH_END(4);
    CF_PH_BEG(5); conv1x1_res(bufB, pwW + i * CF_H * CF_H, pwB + i * CF_H, bufA); CF_PH_END(5);
  }
  CF_PH_BEG(6);
  for (int c = 0; c < CF_H; c++) {`,
  ],
  [
    `    pooled[c] = sum / (f32)CF_SP;
  }
  return 0;
}`,
    `    pooled[c] = sum / (f32)CF_SP;
  }
  CF_PH_END(6);
  CF_PH_END(0);
  return 0;
}`,
  ],
]

const TAIL = `
/* 台架入口（由 kernel-phases.ts 追加）：跑一次并把累计 ticks 抄出去。 */
CF_EXPORT int cf_phase_profile(const float* wblob, const float* in16, float* pooled, float* bufA,
                               unsigned long long* out, int reset) {
  if (reset)
    for (int i = 0; i < 8; i++) cf_phase_ticks[i] = 0;
  const int rc = cf_student_features(wblob, in16, pooled, bufA);
  for (int i = 0; i < 8; i++) out[i] = cf_phase_ticks[i];
  return rc;
}
CF_EXPORT unsigned long long cf_phase_timer_hz(void) { return cf_timer_hz(); }
`

const base = fs.readFileSync(SRC, 'utf8')
let instrumented = base
for (const [oldStr, newStr] of HUNKS) {
  const n = instrumented.split(oldStr).length - 1
  if (n !== 1) {
    console.error(
      `插桩锚点命中 ${n} 次（预期 1）—— conv.c 变了，请更新锚点：\n${oldStr.slice(0, 80)}…`,
    )
    process.exit(2)
  }
  instrumented = instrumented.replace(oldStr, newStr)
}
const outSrc = `${HDR}\n${instrumented}\n${TAIL}`
fs.mkdirSync(WORK, { recursive: true })
const SRC_OUT = path.join(WORK, 'conv.c')
fs.writeFileSync(SRC_OUT, outSrc)

/* ── 构建（--lib 时跳过） ─────────────────────────────────────────────────── */
const target = TARGET_ID ? NATIVE_TARGETS.find((t) => t.id === TARGET_ID) : hostTarget()
if (!target) {
  console.error(`未知目标：${TARGET_ID}（可用：${NATIVE_TARGETS.map((t) => t.id).join(' / ')}）`)
  process.exit(2)
}
const ext = target.lib.split('.').pop()!
const OUT_LIB = path.join(WORK, `kernel-phases-${target.id}.${ext}`)

if (!LIB_ARG) {
  const cc = process.env.NN_CC ?? 'clang'
  const args = [
    ...(target.triple === 'host' ? [] : [`--target=${target.triple}`]),
    ...nativeFlags(target),
    ...EXTRA,
    ...nativeLinkArgs(target),
    '-I',
    path.join(ROOT, 'src', 'nn', 'conv'),
    '-o',
    OUT_LIB,
    SRC_OUT,
  ]
  const r = spawnSync(cc, args, { encoding: 'utf8', windowsHide: true })
  if (r.status !== 0 || !fs.existsSync(OUT_LIB)) {
    console.error(`构建失败 rc=${r.status}\n${r.stderr}\n（命令：${cc} ${args.join(' ')}）`)
    process.exit(2)
  }
}
if (!LIB_ARG && target.id !== hostTarget().id) {
  // 交叉目标只负责**构建**：本机（x64）载入不了 arm64 的 .so，得拷到目标机器上用 --lib 跑。
  console.log(
    `[build] 交叉目标 ${target.id} 已产出 ${path.relative(ROOT, OUT_LIB).replace(/\\/g, '/')}` +
      `（${fs.statSync(OUT_LIB).size}B）—— 拷到目标机器后用 \`--lib <那个库>\` 跑。`,
  )
  process.exit(0)
}

const LIB = path.resolve(LIB_ARG ?? OUT_LIB)

/* ── 驱动 ─────────────────────────────────────────────────────────────────── */
const req = createRequire(import.meta.url)
const ffi = req('bun:ffi') as Record<string, never> & {
  dlopen: (
    p: string,
    d: Record<string, unknown>,
  ) => { symbols: Record<string, (...a: number[]) => number> }
  ptr: (a: ArrayBufferView) => number
  FFIType: Record<string, unknown>
}

function makeRng(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const rng = makeRng(20260922)
const fill = (n: number, scale = 0.4): Float32Array => {
  const a = new Float32Array(n)
  for (let i = 0; i < n; i++) a[i] = (rng() - 0.5) * scale
  return a
}
const blob = new Float32Array(BLOB)
{
  let o = 0
  for (const n of [
    STEM_W,
    H,
    ...Array.from({ length: D }, () => H * 25),
    ...Array.from({ length: D }, () => H),
    ...Array.from({ length: D }, () => H * H),
    ...Array.from({ length: D }, () => H),
  ]) {
    blob.set(fill(n), o)
    o += n
  }
}
const in16 = fill(IN_CH * SP, 2)

const u64 = ffi.FFIType.u64 ?? ffi.FFIType.u32
const symDef = {
  cf_phase_profile: {
    args: [
      ffi.FFIType.ptr,
      ffi.FFIType.ptr,
      ffi.FFIType.ptr,
      ffi.FFIType.ptr,
      ffi.FFIType.ptr,
      ffi.FFIType.i32,
    ],
    returns: ffi.FFIType.i32,
  },
  cf_phase_timer_hz: { args: [], returns: u64 },
  cf_student_features: {
    args: [ffi.FFIType.ptr, ffi.FFIType.ptr, ffi.FFIType.ptr, ffi.FFIType.ptr],
    returns: ffi.FFIType.i32,
  },
} as Record<string, unknown>
const lib = ffi.dlopen(LIB, symDef)
const wb = ffi.ptr(blob)
const ip = ffi.ptr(in16)
const pooled = new Float32Array(H)
const bufA = new Float32Array(H * SP)
// ⚠ 必须用 BigUint64Array（8B/元素）：C 侧写的是 u64，用 Float64Array 存会把位型当 double 解读
// （小整数变成 ~1e-318 的次正规数 ⇒ 比值侥幸还对、绝对值全错，2026-09-23 踩过）。
const ticks = new BigUint64Array(8)

/* 一致性：插桩库与生产库必须逐字节同输出（只读计数器不可能改数值） */
if (EXTRA.length || NO_EQ) {
  console.log(
    '\n⚠ 本次是**实验库**（破了逐位契约，只量上限、不是可用配置）⇒ 不做一致性对拍。' +
      (EXTRA.length ? ` KP_EXTRA_FLAGS=${EXTRA.join(' ')}` : ''),
  )
}
const prod = EXTRA.length || NO_EQ ? { path: '' } : resolveNativeLib(ROOT)
if (prod.path) {
  try {
    const plib = ffi.dlopen(prod.path, {
      cf_student_features: symDef.cf_student_features,
    } as Record<string, unknown>)
    const p2 = new Float32Array(H)
    const a2 = new Float32Array(H * SP)
    lib.symbols.cf_phase_profile(
      wb,
      ip,
      ffi.ptr(p2),
      ffi.ptr(a2),
      ffi.ptr(new BigUint64Array(8)),
      1,
    )
    plib.symbols.cf_student_features(wb, ip, ffi.ptr(pooled), ffi.ptr(bufA))
    const eq =
      Buffer.from(p2.buffer).equals(Buffer.from(pooled.buffer)) &&
      Buffer.from(a2.buffer).equals(Buffer.from(bufA.buffer))
    console.log(
      `[eq] 插桩库 == 生产库（${path.relative(ROOT, prod.path).replace(/\\/g, '/')}）: ${eq}`,
    )
    if (!eq) process.exit(1)
  } catch (e) {
    console.log(`[eq] 跳过（生产库不可用：${String(e).slice(0, 60)}）`)
  }
}

for (let i = 0; i < 5; i++)
  lib.symbols.cf_phase_profile(wb, ip, ffi.ptr(pooled), ffi.ptr(bufA), ffi.ptr(ticks), 1)
lib.symbols.cf_phase_profile(wb, ip, ffi.ptr(pooled), ffi.ptr(bufA), ffi.ptr(ticks), 1)
const t0 = performance.now()
for (let i = 0; i < ITERS; i++)
  lib.symbols.cf_phase_profile(wb, ip, ffi.ptr(pooled), ffi.ptr(bufA), ffi.ptr(ticks), 0)
const wallMs = performance.now() - t0

const tick = (i: number): number => Number(ticks[i])
const hzFromC = Number(lib.symbols.cf_phase_timer_hz())
const hz = hzFromC > 0 ? hzFromC : Math.round(tick(0) / (wallMs / 1000))
const usPer = (t: number): number => ((t / hz) * 1e6) / ITERS

console.log(
  `\n[env] ${process.platform}-${process.arch} · ${ITERS} iters · lib=${LIB.replace(/\\/g, '/')}` +
    `\n[timer] ${hzFromC > 0 ? 'cntfrq_el0' : 'rdtsc(墙钟标定)'} = ${(hz / 1e6).toFixed(3)} MHz` +
    ` · 墙钟 ${(wallMs / ITERS).toFixed(3)} ms/forward`,
)
console.log('\n| 阶段 | ticks/次 | µs/forward | 占比 | GMAC/s |')
console.log('|---|---|---|---|---|')
const total = tick(0) || 1
for (let i = 0; i < 7; i++) {
  const us = usPer(tick(i))
  const share = (tick(i) / total) * 100
  const gmac = MACS[i] ? MACS[i] / (us * 1000) : 0
  console.log(
    `| ${NAMES[i]} | ${(tick(i) / ITERS).toFixed(0)} | ${us.toFixed(3)} | ${share.toFixed(1)}% | ${gmac ? gmac.toFixed(1) : '—'} |`,
  )
}
const sum = Array.from({ length: 6 }, (_, i) => tick(i + 1)).reduce((a, b) => a + b, 0)
console.log(
  `\n[check] 各阶段之和 / 总计 = ${((sum / total) * 100).toFixed(1)}%（应 ≈100%，差额 = 函数入口/出口与循环开销）`,
)
