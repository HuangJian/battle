/**
 * eval-a-baseline.test.ts — 离线开课的 **it0 基线补评**（2026-09-24 用户口径）。
 *
 *  缺口（不是猜测）：离线档 = `courses.<课>.rollout_src:'run'`（**本机不跑训练**）⇒ it0 读数
 *  两条产路都不通——云机侧 `remote/offline_eval.CloudEvalPlan.due()` 对 `it < 1` 恒 False，
 *  本机主循环的基线派发（`loop_core._maybe_dispatch_baseline_eval`）压根不在场上。缺了它
 *  控制台的配对基线退化成「第一条 eval 轮」（`iters.ts`：`evalIters.includes(0) ? 0 : evalIters[0]`）
 *  ⇒ 随 run 起点漂移，跨腿（demo-mix vs firstkill）失去共同锚。
 *
 *  本文件钉**控制台这一侧**的两件事（python 一侧由 `nn-training/tests/test_eval_a_once.py` 钉）：
 *   ① `evalAArgs` 的 argv 形状：baseline ⇒ 带 `--baseline`；`ckpt` 空 ⇒ **不传** `--ckpt`
 *      （空串到 python 手里 `Path("")` 是 `.` = 存在的目录，会被当权重算指纹）；
 *   ② `shouldAutoBaseline` 的三态：离线补 / 在线不补 / 逃生阀置位不补。
 *  一律不真起 python（那会真跑几百局游戏）。
 */

import { describe, expect, it } from 'bun:test'
import { shouldAutoBaseline } from '../src/server/actions/course-lifecycle'
import { evalAArgs } from '../src/server/eval-a-run'

const ENV_KEY = 'BCITY_NO_AUTO_BASELINE_EVAL'

/** 临时置/清逃生阀（跑完还原——同进程里别的用例可能依赖当前值）。 */
function withEscapeHatch(value: string | undefined, fn: () => void): void {
  const saved = process.env[ENV_KEY]
  if (value === undefined) delete process.env[ENV_KEY]
  else process.env[ENV_KEY] = value
  try {
    fn()
  } finally {
    if (saved === undefined) delete process.env[ENV_KEY]
    else process.env[ENV_KEY] = saved
  }
}

describe('evalAArgs（纯函数：一次性进程 argv）', () => {
  it('手动 evalA（控制台按钮/导入后自动评估）：显式 ckpt + iter，不带 --baseline', () => {
    const a = evalAArgs('c4-dodge', 'nn-training/weights/c4-dodge.it27.json', 27)
    expect(a[a.indexOf('--ckpt') + 1]).toBe('nn-training/weights/c4-dodge.it27.json')
    expect(a[a.indexOf('--iter') + 1]).toBe('27')
    expect(a).not.toContain('--baseline')
  })

  it('it0 基线：带 --baseline、iter 恒 0；ckpt 空 ⇒ 整条 --ckpt 都不传', () => {
    const a = evalAArgs('x20-firstkill', '', 0, { baseline: true })
    expect(a).toContain('--baseline')
    expect(a).not.toContain('--ckpt')
    expect(a[a.indexOf('--iter') + 1]).toBe('0')
    // 课程名要在（python 侧靠它找 curricula/*.jsonc）
    expect(a[a.indexOf('--course') + 1]).toBe('x20-firstkill')
  })
})

describe('shouldAutoBaseline（离线才补、逃生阀可关）', () => {
  it('离线 + 无逃生阀 ⇒ 补（it0 是云腿产不出、控制台又必须要的那一格）', () => {
    withEscapeHatch(undefined, () => {
      expect(shouldAutoBaseline('offline')).toBe(true)
    })
  })

  it('在线 ⇒ 不补（本机主循环 `_maybe_dispatch_baseline_eval` 自己会派）', () => {
    withEscapeHatch(undefined, () => {
      expect(shouldAutoBaseline('online')).toBe(false)
    })
  })

  it('逃生阀置位（测试/应急）⇒ 不补', () => {
    withEscapeHatch('1', () => {
      expect(shouldAutoBaseline('offline')).toBe(false)
    })
  })
})
