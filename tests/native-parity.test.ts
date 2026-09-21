/**
 * native-parity.test.ts —— native features 内核 vs wasm 的**逐字节**等价门（评审 B2/B3）。
 *
 * 为什么这个测试必须存在（rollout-eval-opt.plan.md §4 T0′）：
 *  原先「native 与 wasm 8/8 逐位一致」只存在于一个手工脚本里（硬编码 tmp/conv_features_cli.exe、
 *  脚本自己不构建），tmp 一清就再也验不了；而 native 侧内核与 wasm 侧内核是**两份独立实现**
 *  （wasm 用 wasm_simd128 intrinsics，动它就是动产品字节，不能共享源码）。所以两侧的一致性
 *  只能靠机械对拍钉住，而不是靠"同序"的口头约定。
 *
 * 覆盖三件：
 *  ① 共享库（bun:ffi，生产路径）对 wasm：8 次随机输入，pooled 与 bufA **逐字节**相等；
 *  ② 参考 CLI（tmp/native/conv_features_cli.exe，独立进程）对 wasm：同一断言 —— T0 可复现；
 *  ③ attestation 守卫：库里核算是错的（注入 poison 库）⇒ 生产入口必须**回落 wasm** 且响亮。
 *
 * 缺编译器（>无 clang）时 ①② 优雅跳过并打一行说明——跳过是**响亮**的，不静默变绿。
 */
import { describe, expect, it } from 'bun:test'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
process.chdir(ROOT)

const { runStudentConvWasm, runStudentFeatures } = await import('../src/nn/conv-wasm.ts')
const {
  featuresEngine,
  nativeLibCandidates,
  nativeStatus,
  resetNativeConvForTest,
  runStudentConvNative,
  setNativeConvEnabled,
} = await import('../src/nn/native-conv.ts')
const { buildNative, nativeArtifactNames, nativeStaleReason } =
  await import('../tools/agent/native-build.ts')

const BOARD = 26
const SP = BOARD * BOARD
const H = 64
const D = 8
const IN_CH = 18
const DW_OC = H * 25
const PW_OC = H * H

interface TestModel {
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

/** 确定性伪随机（LCG）——不用 Math.random，失败可复现。 */
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

/** 一次性构造 shape 合法的假权重模型（对拍与数值无关，但要覆盖真实形状）。 */
function makeModel(seed = 20260921): TestModel {
  const rng = makeRng(seed)
  return {
    in16: new Float32Array(IN_CH * SP),
    stemW: fill(rng, IN_CH * H * 9),
    stemB: fill(rng, H),
    dwW: Array.from({ length: D }, () => fill(rng, DW_OC)),
    dwB: Array.from({ length: D }, () => fill(rng, H)),
    pwW: Array.from({ length: D }, () => fill(rng, PW_OC)),
    pwB: Array.from({ length: D }, () => fill(rng, H)),
    pooled: new Float32Array(H),
    bufA: new Float32Array(H * SP),
  }
}

function randInput(m: TestModel, rng: () => number): void {
  for (let i = 0; i < m.in16.length; i++) m.in16[i] = (rng() - 0.5) * 2
}

function bytesOf(a: Float32Array): Buffer {
  return Buffer.from(a.buffer, a.byteOffset, a.byteLength)
}

/** 参考 CLI 的入参协议：magic u32 + in16 + wblob（顺序见 conv_feats_native.h）。 */
function packCliInput(m: TestModel): Buffer {
  const parts: Float32Array[] = [m.in16, m.stemW, m.stemB, ...m.dwW, ...m.dwB, ...m.pwW, ...m.pwB]
  return Buffer.concat([
    Buffer.from(new Uint32Array([0x434f4e56]).buffer),
    ...parts.map((a) => bytesOf(a)),
  ])
}

const TRIALS = 8

const build = buildNative(ROOT, {})
const nativeOk = build.ok
if (!nativeOk) {
  console.warn(`[native-parity] native 未构建（${build.reason}）→ 对拍用例跳过（非静默变绿）`)
} else {
  console.log(
    `[native-parity] native 就绪：flags=${build.manifest!.flags.join(' ')} cc=${build.manifest!.ccVersion}`,
  )
}

describe('native 内核 vs wasm（逐字节）', () => {
  it.skipIf(!nativeOk)(`共享库 8 次随机输入 pooled+bufA 逐字节一致`, () => {
    resetNativeConvForTest()
    const m = makeModel()
    const rng = makeRng(7)
    let trials = 0
    for (let t = 0; t < TRIALS; t++) {
      randInput(m, rng)
      // 参考：wasm
      expect(runStudentConvWasm(m)).toBe(true)
      const wPooled = Float32Array.from(m.pooled)
      const wBufA = Float32Array.from(m.bufA)
      // 被测：native（生产入口；首次会先做 attestation）
      expect(runStudentConvNative(m, { runWasm: (x) => runStudentConvWasm(x) })).toBe(true)
      expect(bytesOf(m.pooled).equals(bytesOf(wPooled))).toBe(true)
      expect(bytesOf(m.bufA).equals(bytesOf(wBufA))).toBe(true)
      trials++
    }
    expect(trials).toBe(TRIALS)
    expect(featuresEngine()).toBe('native')
    expect(nativeStatus().available).toBe(true)
  })

  it.skipIf(!nativeOk)('参考 CLI（独立进程）与 wasm 逐字节一致（T0 可复现）', () => {
    const exe = path.join(build.dir, nativeArtifactNames().exe)
    expect(fs.existsSync(exe)).toBe(true)
    const m = makeModel(4242)
    const rng = makeRng(11)
    for (let t = 0; t < 3; t++) {
      randInput(m, rng)
      expect(runStudentConvWasm(m)).toBe(true)
      const wPooled = Float32Array.from(m.pooled)
      const wBufA = Float32Array.from(m.bufA)
      const r = spawnSync(exe, { input: packCliInput(m), maxBuffer: 8 * 1024 * 1024 })
      expect(r.status).toBe(0)
      const out = r.stdout as Buffer
      expect(out.length).toBe((H + H * SP) * 4)
      const gotPooled = new Float32Array(out.buffer, out.byteOffset, H)
      const gotBufA = new Float32Array(out.buffer, out.byteOffset + H * 4, H * SP)
      expect(bytesOf(gotPooled).equals(bytesOf(wPooled))).toBe(true)
      expect(bytesOf(gotBufA).equals(bytesOf(wBufA))).toBe(true)
    }
  })

  it('架构不符（d=4）时生产入口不启用任何加速后端', () => {
    resetNativeConvForTest()
    const m = makeModel()
    const bad = { ...m, dwW: m.dwW.slice(0, 4), dwB: m.dwB.slice(0, 4) }
    expect(runStudentConvNative(bad, { runWasm: (x) => runStudentConvWasm(x) })).toBe(false)
    expect(runStudentConvWasm(bad)).toBe(false)
  })
})

describe('native 选择链（T1′：native → wasm → TS）', () => {
  it('setNativeConvEnabled(false) ⇒ 生产入口回落 wasm（基准量纯 wasm 臂用的开关）', async () => {
    setNativeConvEnabled(false)
    try {
      const m = makeModel(1234)
      expect(await runStudentFeatures(m)).toBe(true)
      expect(featuresEngine()).toBe('wasm')
      expect(nativeStatus().available).toBe(false)
    } finally {
      setNativeConvEnabled(true)
      resetNativeConvForTest()
    }
  })

  it('入库 prebuilt 排在候选里、且优先于本机构建（分发物 = 生产路径）', () => {
    const rel = `${process.platform}-${process.arch}`
    const pb = path.join(ROOT, 'src', 'nn', 'native', 'prebuilt', rel)
    if (!fs.existsSync(pb)) {
      console.warn(`[native-parity] 仓里无 ${rel} 的 prebuilt → 优先级断言跳过（非静默变绿）`)
      return
    }
    const c = nativeLibCandidates()
    const iPre = c.findIndex((p) => p.includes('native-prebuilt') || /prebuilt[\\/]/.test(p))
    const iLocal = c.findIndex((p) => p.includes(path.join('tmp', 'native')))
    expect(iPre).toBeGreaterThanOrEqual(0)
    expect(iLocal).toBeGreaterThan(iPre)
  })

  it('库路径候选：env 最优先，且覆盖 bundle 同级 / bundle 的 wasm 子目录 / cwd 的 tmp/native', () => {
    const prev = process.env.NN_NATIVE_LIB
    process.env.NN_NATIVE_LIB = 'X:/probe/conv_feats_native.dll'
    try {
      const c = nativeLibCandidates()
      expect(c[0]).toBe('X:/probe/conv_feats_native.dll')
      // cwd 的 tmp/native（源路径裸跑 / 一次性 spawn，cwd=仓根）
      expect(c.some((p) => p.includes(path.join('tmp', 'native')))).toBe(true)
      // bundle 同级（打包产物用 import.meta.url 相对解析）与其 wasm/ 子目录
      expect(c.length).toBeGreaterThanOrEqual(4)
      expect(c.filter((p) => p.includes('conv_feats_native')).length).toBeGreaterThanOrEqual(4)
    } finally {
      if (prev === undefined) delete process.env.NN_NATIVE_LIB
      else process.env.NN_NATIVE_LIB = prev
    }
  })
})

describe('eval 侧记账（T4：eval 与 rollout 同咽喉 + 可查）', () => {
  it('export-eval-game 的报告带 feat，且与 featuresEngine() 同口径（瘦身规格 ⇒ TS）', () => {
    // h=16/d=2 的 fixture 不满足加速守卫（h64/d8）⇒ 走 TS，正好验证「回落也要记账」这一半。
    const g = JSON.parse(
      fs.readFileSync(path.join(ROOT, 'tests', 'fixtures', 'student-golden.json'), 'utf8'),
    ) as { h: number; d: number; params: Record<string, unknown> }
    const base = path.join(ROOT, 'tmp')
    fs.mkdirSync(base, { recursive: true })
    const work = fs.mkdtempSync(path.join(base, 'eval-acc-'))
    const weights = path.join(work, 'weights.json')
    fs.writeFileSync(
      weights,
      JSON.stringify({ arch: { kind: 'student', h: g.h, d: g.d }, params: g.params }),
      { encoding: 'utf8' },
    )
    const out = path.join(work, 's0')
    const wver = 'e'.repeat(64)
    const r = spawnSync(
      process.execPath,
      [
        'tools/sim/export-eval-game.ts',
        '--weights',
        weights,
        '--stage',
        '0',
        '--seed',
        '1',
        '--max-ticks',
        '300',
        '--wver',
        wver,
        '--node-label',
        'test',
        '--out',
        out,
      ],
      { cwd: ROOT, encoding: 'utf8', timeout: 120_000, windowsHide: true },
    )
    expect(r.status).toBe(0)
    const report = JSON.parse(
      fs.readFileSync(path.join(out, '_eval_report.json'), 'utf8'),
    ) as Record<string, unknown>
    expect(report.wver).toBe(wver)
    expect(report.feat).toBe('ts')
    expect(report.outcome).toBeTruthy()
  })
})

describe('attestation 守卫（B2/B4）', () => {
  it.skipIf(!nativeOk)('核算是错的库 ⇒ 生产入口回落 wasm 并记录原因', async () => {
    // 故意编一个「输出全 0」的库：形状/ABI 都对，数值错 ⇒ 只有 attestation 能拦住它
    const dir = path.join(ROOT, 'tmp', 'native-poison')
    fs.mkdirSync(dir, { recursive: true })
    const src = path.join(dir, 'poison.c')
    fs.writeFileSync(
      src,
      '#include <string.h>\n' +
        '#if defined(_WIN32)\n#define EXPORT __declspec(dllexport)\n#else\n#define EXPORT\n#endif\n' +
        'EXPORT unsigned cf_abi(void) { return 1u; }\n' +
        'EXPORT int cf_student_features(const float* w, const float* i, float* pooled, float* bufA) {\n' +
        '  (void)w; (void)i;\n' +
        '  memset(pooled, 0, 64 * sizeof(float));\n' +
        '  memset(bufA, 0, 64 * 676 * sizeof(float));\n' +
        '  return 0;\n' +
        '}\n',
      { encoding: 'utf8' },
    )
    const outName = nativeArtifactNames().lib
    const out = path.join(dir, outName)
    const args =
      process.platform === 'darwin'
        ? ['-O3', '-dynamiclib', '-o', out, src]
        : ['-O3', '-shared', ...(process.platform === 'linux' ? ['-fPIC'] : []), '-o', out, src]
    const cc = spawnSync(build.manifest!.cc, args, { encoding: 'utf8', windowsHide: true })
    if (cc.status !== 0 || !fs.existsSync(out)) {
      console.warn(`[native-parity] poison 库编译失败 → 守卫用例跳过: ${cc.stderr?.slice(0, 200)}`)
      return
    }
    const prev = process.env.NN_NATIVE_LIB
    process.env.NN_NATIVE_LIB = out
    resetNativeConvForTest()
    try {
      const m = makeModel(99)
      randInput(m, makeRng(3))
      // 生产入口（native 优先）必须仍然成功——但只能由 wasm 完成
      expect(await runStudentFeatures(m)).toBe(true)
      expect(featuresEngine()).toBe('wasm')
      expect(nativeStatus().available).toBe(false)
      expect(nativeStatus().reason).toMatch(/attestation 失败/)
    } finally {
      if (prev === undefined) delete process.env.NN_NATIVE_LIB
      else process.env.NN_NATIVE_LIB = prev
      resetNativeConvForTest()
    }
  })

  it('指纹过期判定：源码 sha 不符 ⇒ 报「需重建」', () => {
    const dir = path.join(ROOT, 'tmp', 'native-stale-probe')
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(
      path.join(dir, 'native-build.json'),
      JSON.stringify({
        abi: 1,
        cc: 'clang',
        ccVersion: 'probe',
        target: `${process.platform}-${process.arch}`,
        flags: [],
        sources: [{ path: 'src/nn/native/conv_feats_native.c', sha256: 'deadbeef' }],
        artifacts: {
          lib: { name: 'x', sha256: 'x', bytes: 1 },
          exe: { name: 'y', sha256: 'y', bytes: 1 },
        },
        builtAt: new Date().toISOString(),
      }),
      { encoding: 'utf8' },
    )
    const why = nativeStaleReason(ROOT, { outDir: 'tmp/native-stale-probe' })
    expect(why).toMatch(/源码已变/)
  })
})
