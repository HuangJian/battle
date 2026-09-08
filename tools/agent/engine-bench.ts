/**
 * engine-bench.ts —— features 稳态 forward 微基准（rollout 引擎自动选择用，§374）
 *
 * 背景：同一 conv_feats.wasm（37M MACs/forward）在不同平台 JSC(bun)/V8(node) 谁快
 * 不一（五平台实测：V8 在 win/wsl x64 快 14-30%，mac/arm64 bun 快 3-6%）→ 启动时
 * 用本基准在**本机**实测两引擎稳态 forward，选快者（3% 迟滞，防噪声横跳）。
 *
 * 数值无关性：forward 是定长计算（MAC 数与数据值无关），故用 dummy 权重/输入即可，
 * 无需真实权重。镜像 conv-wasm 的 runner 语义（每调用拷 in16 + 读回 pooled），
 * 保证与真实采样路径同构。
 *
 * 用法（bun 跑 TS / node 跑打包后的 .mjs，接口一致）：
 *   <engine> engine-bench(.ts|.mjs) <conv_feats.wasm 绝对路径> [rounds]
 * 每轮先 warmup W 次再计时 T 次，输出一行 `BENCH <avg_ms>`；退出码 0=成功。
 *   rounds 缺省 2；内部 W=15、T=30（≈ 0.3-0.5s/轮，足够压噪声）。
 */
import { readFileSync } from 'fs'

const BOARD = 26
const SP = BOARD * BOARD
const H = 64
const D = 8

function main(): void {
  const wasmPath = process.argv[2]
  const rounds = Math.max(1, parseInt(process.argv[3] ?? '2', 10))
  if (!wasmPath) {
    console.error('usage: engine-bench <conv_feats.wasm> [rounds]')
    process.exit(2)
  }
  const bytes = readFileSync(wasmPath)
  const mod = new WebAssembly.Module(bytes)
  const inst = new WebAssembly.Instance(mod)
  const mem = inst.exports.memory as WebAssembly.Memory
  // 与 conv-wasm.ts 相同的布局/生长（权重 + 输入 + 3 中间 + pooled）
  const need =
    (1 << 20) + (9216 + 8 * (1600 + 64 + 4096 + 64)) * 4 + 16 * SP * 4 + 3 * H * SP * 4 + H * 4
  const grow = Math.ceil((need - mem.buffer.byteLength) / 65536)
  if (grow > 0) mem.grow(grow)
  const base = 1 << 20
  const feats = inst.exports['features'] as (...n: number[]) => void
  const offStemW = base
  const offStemB = offStemW + 9216 * 4
  const offDwW = offStemB + 64 * 4
  const offDwB = offDwW + D * H * 25 * 4
  const offPwW = offDwB + D * H * 4
  const offPwB = offPwW + D * H * H * 4
  const offIn = offPwB + D * H * 4
  const offBufA = offIn + 16 * SP * 4
  const offBufB = offBufA + H * SP * 4
  const offBufC = offBufB + H * SP * 4
  const offPooled = offBufC + H * SP * 4
  const f32At = (o: number, n: number): Float32Array => new Float32Array(mem.buffer, o, n)
  // dummy 权重/输入（定值；forward 计算量与值无关）
  f32At(offStemW, 9216).fill(0.01)
  f32At(offStemB, 64).fill(0.01)
  for (let i = 0; i < D; i++) {
    f32At(offDwW + i * H * 25 * 4, H * 25).fill(0.01)
    f32At(offDwB + i * H * 4, H).fill(0.01)
    f32At(offPwW + i * H * H * 4, H * H).fill(0.01)
    f32At(offPwB + i * H * 4, H).fill(0.01)
  }
  const in16 = f32At(offIn, 16 * SP)
  in16.fill(0.5)
  const pooled = f32At(offPooled, H)

  const once = (): void => {
    // 与 conv-wasm runner 同构：每调用拷 in16 + 读回 pooled
    const v = f32At(offIn, 16 * SP)
    v.set(in16)
    feats(
      offIn,
      offStemW,
      offStemB,
      offDwW,
      offDwB,
      offPwW,
      offPwB,
      offBufA,
      offBufB,
      offBufC,
      offPooled,
    )
    pooled.set(f32At(offPooled, H).subarray(0, H))
  }
  const WARM = 15
  const T = 30
  for (let r = 0; r < rounds; r++) {
    for (let i = 0; i < WARM; i++) once()
    const t0 = performance.now()
    for (let i = 0; i < T; i++) once()
    const avg = (performance.now() - t0) / T
    console.log(`BENCH ${avg.toFixed(3)}`)
  }
}
main()
