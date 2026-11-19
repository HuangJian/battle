/**
 * native-variant.ts —— 运行时决定「用 x64 的哪一条产物」（基线 vs `-fma` 变体）。
 *
 * ## 为什么必须**试跑**而不是查 CPU 型号（2026-11-16 用户提问「不支持 FMA 的能不能用无 FMA 的」）
 *
 * `-mfma` 编出来的内核在 Haswell(2013) 之前的 x64 上一执行就 SIGILL，**进程级 try/catch 拦不住**
 * （见 conv_native_adapter.ts 头注 ①b）。所以选错的代价不是「慢一点」而是「节点整局挂掉」，
 * 判定必须保守且可机器复核：
 *
 *   ① **不解析 `/proc/cpuinfo` / `sysctl` / 注册表**：三套平台三套解析、各自会漂（WSL 的
 *      cpuinfo 是宿主透传，Android 的 flags 字段命名又不统一），而且字符串里有 `fma` 也不代表
 *      OS 允许用（虚拟化屏蔽）。它们能给的只是一句「可能行」。
 *   ② **在子进程里真跑一次**：`bun -e` 里 dlopen 该产物 + 调一次 `cf_student_features`，
 *      退出码 0 才算「本机可用」。SIGILL 死在子进程里，父进程只看到非 0 退出码 —— 这正是
 *      我们要的语义（「会不会炸」而不是「CPU 有没有这个功能」）。代价是一次进程启动（本机实测
 *      ~40–120 ms），因此结果**落盘缓存**（键=平台/架构+产物 sha），一台机器一辈子一次。
 *
 * 缓存文件用 `tmp/nn-native-variant.json`（仓根，已 gitignore；**绝不写进 `src/`**——
 * 那里进 codeHash，写一个文件就会让所有节点变 stale）。写不进去（只读盘/别的 cwd）不影响
 * 正确性：本进程内仍然缓存，只是下次进程要再探一次。
 *
 * 缺产物、子进程超时、探针自身崩 ⇒ 一律判 `false`（= 用基线）。**失败方向永远是保守的**：
 * 拿不到证据就少 8.4%，而不是赌一把让节点 SIGILL。
 */
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { D, H, IN_CH, SP, STEM_W } from './conv-shape'
import type { NativeVariant } from './native-prebuilt'

/** 权重 blob 的 float 数（与 conv_native.h::CF_BLOB_FLOATS / adapter 的 ensureBlob 同序同长）。 */
const BLOB_FLOATS = STEM_W + H + D * H * 25 + D * H + D * H * H + D * H

/** 探针逻辑版本：改了探针（参数/断言）就 +1，让旧缓存失效。 */
const PROBE_VERSION = 1

/** 缓存文件（相对 cwd，已 gitignore）。 */
export const VARIANT_CACHE_REL = 'tmp/nn-native-variant.json'

/**
 * 探针超时：正常 <1 s（本机实测 ~45–55 ms）。卡住 = 进程/目录有问题 ⇒ 直接判不可用并缓存
 * false（永久基线）。不设更长的值：这是**首次 forward 的同步等待**，宁可少 8.4% 也别卡住采样。
 */
const PROBE_TIMEOUT_MS = 10_000

interface CacheFile {
  v?: number
  entries?: Record<string, { ok: boolean; at: string }>
}

function cachePath(): string {
  return path.join(process.cwd(), VARIANT_CACHE_REL)
}

function readCache(): CacheFile {
  try {
    const raw = fs.readFileSync(cachePath(), 'utf8')
    const m = JSON.parse(raw) as CacheFile
    if (m && typeof m === 'object' && m.v === PROBE_VERSION && m.entries) return m
  } catch {
    /* 缺文件/坏 JSON/版本不符 ⇒ 当空缓存 */
  }
  return { entries: {} }
}

function writeCache(c: CacheFile): void {
  try {
    fs.mkdirSync(path.dirname(cachePath()), { recursive: true })
    fs.writeFileSync(cachePath(), JSON.stringify({ ...c, v: PROBE_VERSION }, null, 2), 'utf8')
  } catch {
    /* 只读盘/无权限 ⇒ 不缓存（正确性不受影响） */
  }
}

/** 产物指纹（缓存键的一部分）：8KB 文件，sha256 成本可忽略。 */
function fileKey(p: string): string {
  try {
    return createHash('sha256').update(fs.readFileSync(p)).digest('hex').slice(0, 16)
  } catch {
    return ''
  }
}

/**
 * 子进程探针脚本：dlopen 目标产物、问 ABI、用 dummy 权重跑一次内核。
 *
 * 只用 `bun:ffi` 的基础 API；尺寸/ABI 从 `conv-shape` 与 `NATIVE_ABI` 注入，避免跑着跑着
 * 两边常量漂了（探针若用旧尺寸，会把「尺寸不符」误判成「内核不可用」——失败方向倒是保守的）。
 */
function probeScript(abi: number): string {
  return [
    "const { dlopen, FFIType, ptr } = require('bun:ffi')",
    'const lib = dlopen(process.env.NN_PROBE_LIB, {',
    '  cf_abi: { args: [], returns: FFIType.u32 },',
    '  cf_student_features: {',
    '    args: [FFIType.ptr, FFIType.ptr, FFIType.ptr, FFIType.ptr],',
    '    returns: FFIType.i32,',
    '  },',
    '})',
    `if (lib.symbols.cf_abi() !== ${abi}) process.exit(3)`,
    `const blob = new Float32Array(${BLOB_FLOATS}).fill(0.01)`,
    `const in16 = new Float32Array(${IN_CH * SP}).fill(0.5)`,
    'const pooled = new Float32Array(64)',
    `const bufA = new Float32Array(64 * ${SP})`,
    'const rc = lib.symbols.cf_student_features(ptr(blob), ptr(in16), ptr(pooled), ptr(bufA))',
    'process.exit(rc === 0 ? 0 : 4)',
  ].join('\n')
}

/**
 * 该产物在本机能不能**真的跑起来**（缓存优先）。
 *
 * `libPath` 为空（矩阵里没有这个变体 / 文件缺失）⇒ false，不探测。
 */
export function variantKernelRuns(
  libPath: string,
  opts: { abi: number; noCache?: boolean },
): boolean {
  if (!libPath) return false
  const key = `${process.platform}-${process.arch}|${PROBE_VERSION}|${fileKey(libPath)}`
  const cache = opts.noCache ? { entries: {} } : readCache()
  const hit = cache.entries?.[key]
  if (hit) return hit.ok

  const r = spawnSync(process.execPath, ['-e', probeScript(opts.abi)], {
    encoding: 'utf8',
    timeout: PROBE_TIMEOUT_MS,
    windowsHide: true,
    env: { ...process.env, NN_PROBE_LIB: libPath, NN_NATIVE: '1' },
  })
  const ok = r.status === 0
  if (!opts.noCache) {
    const entries = cache.entries ?? {}
    entries[key] = { ok, at: new Date().toISOString() }
    cache.entries = entries
    writeCache(cache)
  }
  return ok
}

/**
 * 本机该优先用哪个变体。返回值只用来**排序候选路径**（`nativeLibCandidates`）：
 * 探测不过 ⇒ 只用基线产物，连候选里都不放变体（避免任何路径把它加载起来）。
 */
export function preferredVariants(
  variants: { variant?: 'fma'; libPath: string }[],
  opts: { abi: number; noCache?: boolean },
): NativeVariant[] {
  const base: NativeVariant[] = variants.some((v) => !v.variant) ? ['baseline'] : []
  const fma = variants.find((v) => v.variant === 'fma')
  if (fma && variantKernelRuns(fma.libPath, opts)) return ['fma', ...base]
  return base
}

/** 测试用：清掉落盘缓存（不影响本进程已有的选择结果）。 */
export function resetVariantCacheForTest(): void {
  try {
    fs.rmSync(cachePath(), { force: true })
  } catch {
    /* ignore */
  }
}
