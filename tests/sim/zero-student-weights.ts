/**
 * 零填充 student 权重（`nn-weights-json`）——spawn 型 sim 测试的**唯一**权重夹具。
 *
 * 为什么不读真权重：`nn-training/weights/**` 是 gitignore（本机训练产物），干净 clone /
 * CI 上不存在。拿它当夹具，门禁就变成「这台机器上有没有某个 checkpoint」——2026-11-20
 * 四个 spawn 用例因此在本机全红（`ENOENT g4-mobility.it380…`），而代码一行没错。
 *
 * 形状 = **当前生产架构**（h=64 / d=8 / 三头 + value），所以 `buildModelFromText` 与
 * `assertRolloutWeightsSchema` 照旧按生产 schema 校验、跑的是生产特征路径（别缩成
 * h=16/d=2 的瘦身规格：那会把 schema 护栏和原生卷积准入一起绕掉）。
 *
 * 输入/标量宽度跟住 `src/nn/obs-encoder.ts`：写成旧宽度会静默回退 TS 特征路径，
 * 零权重下轨迹仍然相同（logits 全 0），但同一局会慢十几倍。
 *
 * 这是**唯一**实现（2026-11-20 收敛）：spawn 型用例用 `writeZeroStudentWeights()` 落盘，
 * 进程内 `runOneBench` 型用例用 `zeroStudentWeightsText()`；原先 `state-init` /
 * `aim-dodge-buckets` 各有一份内联副本，改形状要改三处，漏一处就是“某些用例走旧宽度”。
 *
 * 别把它和 `tests/fixtures/student-golden*.json` 混用：那些是**带具体权重**的金标夹具，
 * 断言值依赖那组数；需要零权重只是为了把仿真跑起来的用例才用本模块。
 */
import { writeFileSync } from 'fs'
import { join } from 'path'
import { OBS_CHANNELS, SCALAR_DIM } from '../../src/nn/obs-encoder'

/** 零填充 student 权重的 JSON 文本（确定性；无随机、无墙钟）。 */
export function zeroStudentWeightsText(): string {
  const h = 64
  const d = 8
  const inCh = OBS_CHANNELS
  const scalarDim = SCALAR_DIM
  const shapes: Record<string, number[]> = {
    'stem.weight': [h, inCh + 2, 3, 3],
    'stem.bias': [h],
    'fc.weight': [128, h + scalarDim],
    'fc.bias': [128],
    'move_head.weight': [5, 128],
    'move_head.bias': [5],
    'fire_head.weight': [2, 128],
    'fire_head.bias': [2],
    'value_head.weight': [1, 128],
    'value_head.bias': [1],
  }
  for (let i = 0; i < d; i++) {
    shapes[`blocks.${i}.dw.weight`] = [h, 1, 5, 5]
    shapes[`blocks.${i}.dw.bias`] = [h]
    shapes[`blocks.${i}.pw.weight`] = [h, h, 1, 1]
    shapes[`blocks.${i}.pw.bias`] = [h]
  }
  const params: Record<string, { shape: number[]; data: string }> = {}
  for (const [name, shape] of Object.entries(shapes)) {
    const n = shape.reduce((a, b) => a * b, 1)
    const f32 = new Float32Array(n)
    params[name] = { shape, data: Buffer.from(f32.buffer).toString('base64') }
  }
  return JSON.stringify({
    format: 'nn-weights-json',
    version: 1,
    schema_major: 3,
    arch: {
      kind: 'student',
      in_ch: inCh,
      board: 26,
      scalar_dim: scalarDim,
      h,
      d,
      head_hidden: 128,
    },
    params,
  })
}

/** 写一份零权重到 `dir`，返回**绝对**路径（子进程 cwd 可能与测试进程不同）。 */
export function writeZeroStudentWeights(dir: string): string {
  const p = join(dir, 'zero-student-weights.json')
  writeFileSync(p, zeroStudentWeightsText())
  return p
}
