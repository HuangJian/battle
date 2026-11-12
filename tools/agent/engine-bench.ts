/**
 * engine-bench.ts —— features 稳态 forward 微基准（rollout 引擎自动选择用，§374 / rollout-eval-opt §4）
 *
 * 背景：同一份 features 在不同平台/运行时谁快不一（五平台实测：wasm 路径 V8 在 win/wsl x64
 * 快 14-30%，mac/arm64 是 bun 快 3-6%）→ 启动时用本基准在**本机**实测稳态 forward，
 * 选快者（3% 迟滞，防噪声横跳）。
 *
 * ★ 2026-09-21 改（评审 B1/B3）：旧版在文件里**自绘一份 wasm 内存布局**再直接调 `features`，
 *   于是①通道数在 v2→v3（16→18）时就漂了（旧布局 offIn 只留 16 通道，越界写进 bufA 区）；
 *   ②永远测不到 native 臂。现在改为调用**生产入口** `runStudentFeatures`（native → wasm →
 *   TS 同一条链，见 src/nn/conv/conv.ts），基准测的就是生产真正走的路：
 *     - bun 臂装了共享库 ⇒ 测出来是 native（且首用 attestation 已被 warmup 吸收）；
 *     - node 臂无 bun:ffi ⇒ 测出来是 wasm；
 *     - **两臂都没跑起来 ⇒ 非零退出**，绝不输出 BENCH 数字（2026-11-12 事故：形状漂移
 *       让两个后端在守卫处 return false，测出 0.00x ms 的"空转"，BENCH-ARM unknown，
 *       假数字被 rollout-runner 缓存成引擎选择依据，且无人察觉）。
 *
 * ★ 2026-11-12 修：dummy 模型的形状不再自绘，一律取 ./conv-shape（与两个 adapter 同源）。
 *   2026-09-21 只把「直接调 wasm」改成「调生产入口」，模型形状仍留了一份硬编码，
 *   v7 把 IN_CH 18→19 时这份就漂了 —— 形状常量必须只有一处定义。
 *
 * 数值无关性：forward 是定长计算（MAC 数与数据值无关），故 dummy 权重/输入即可，无需真实权重。
 *
 * 用法（bun 跑 TS / node 跑打包后的 .mjs，接口一致）:
 *   <engine> engine-bench(.ts|.mjs) [rounds]
 * 输出（最后两行；仅成功时）:
 *   BENCH <avg_ms>              本引擎稳态 features 单次耗时（取各轮最小值）
 *   BENCH-ARM <native|wasm>     实际测的是哪条后端（账本/日志用）
 * 退出码 0=成功；1=未跑进加速后端（基准无效）。
 */

// 静态 import：node 打包（bun build --target=node）能把它内联进来；
// 用动态 URL import 在产物里会指向不存在的源文件（裸跑才有）。
import { featuresEngine, runStudentFeatures } from '../../src/nn/conv/conv'
import { D, H, IN_CH, SP, STEM_W } from '../../src/nn/conv/conv-shape'

/** 确定性伪随机（LCG）；只为形状合法的假权重，不用 Math.random。 */
function makeRng(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function fill(rng: () => number, n: number): Float32Array {
  const a = new Float32Array(n)
  for (let i = 0; i < n; i++) a[i] = (rng() - 0.5) * 0.4
  return a
}

function makeModel(): Record<string, unknown> {
  const rng = makeRng(1234)
  return {
    in16: new Float32Array(IN_CH * SP),
    stemW: fill(rng, STEM_W),
    stemB: fill(rng, H),
    dwW: Array.from({ length: D }, () => fill(rng, H * 25)),
    dwB: Array.from({ length: D }, () => fill(rng, H)),
    pwW: Array.from({ length: D }, () => fill(rng, H * H)),
    pwB: Array.from({ length: D }, () => fill(rng, H)),
    pooled: new Float32Array(H),
    bufA: new Float32Array(H * SP),
  }
}

function main(): void {
  const nums = process.argv.slice(2).filter((a) => /^\d+$/.test(a))
  const rounds = Math.max(1, parseInt(nums[0] ?? '2', 10))
  const m = makeModel()
  const once = (): void => {
    runStudentFeatures(m)
  }
  const WARM = 15
  const T = 30
  let best = Number.POSITIVE_INFINITY
  for (let r = 0; r < rounds; r++) {
    for (let i = 0; i < WARM; i++) once()
    const t0 = performance.now()
    for (let i = 0; i < T; i++) once()
    best = Math.min(best, (performance.now() - t0) / T)
  }
  const arm = featuresEngine()
  // 空转守卫：形状一旦再漂，两个后端都会在形状检查处 return false，此时 featuresEngine()
  // 仍是 unknown、耗时接近 0 —— 旧版照样打印 BENCH，把假数字喂给引擎选择。宁可让基准
  // 失败（调用方回退 bun），也不产出看似合法的错误数据。
  if (arm !== 'native' && arm !== 'wasm') {
    console.error(`[engine-bench] 基准无效：未跑进加速后端（arm=${arm}，best=${best}）→ 拒绝输出`)
    process.exit(1)
  }
  if (!Number.isFinite(best) || best <= 0) {
    console.error(`[engine-bench] 基准无效：耗时非正（best=${best}，arm=${arm}）→ 拒绝输出`)
    process.exit(1)
  }
  console.log(`BENCH ${best.toFixed(3)}`)
  console.log(`BENCH-ARM ${arm}`)
}
main()
