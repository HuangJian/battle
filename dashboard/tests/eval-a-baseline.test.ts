/**
 * eval-a-baseline.test.ts — 离线开课的 **it0 基线补评**（2026-09-24 用户口径）。
 *
 *  缺口（不是猜测）：离线档 = `courses.<课>.rollout_src:'run'`（**本机不跑训练**）⇒ it0 读数
 *  两条产路都不通——云机侧 `remote/offline_eval.CloudEvalPlan.due()` 对 `it < 1` 恒 False，
 *  本机主循环的基线派发（`loop_core._maybe_dispatch_baseline_eval`）压根不在场上。缺了它
 *  控制台的配对基线退化成「第一条 eval 轮」（`iters.ts`：`evalIters.includes(0) ? 0 : evalIters[0]`）
 *  ⇒ 随 run 起点漂移，跨腿（demo-mix vs firstkill）失去共同锚。
 *
 *  本文件钉**控制台这一侧**的一件事（python 一侧由 `nn-training/tests/trainer/test_eval_a_once.py` 钉）：
 *   ① `evalAArgs` 的 argv 形状：baseline ⇒ 带 `--baseline`；`ckpt` 空 ⇒ **不传** `--ckpt`
 *      （空串到 python 手里 `Path("")` 是 `.` = 存在的目录，会被当权重算指纹）。
 *  一律不真起 python（那会真跑几百局游戏）。
 *
 *  ★M4（plan/worker-type-dispatch-model §3-M4）：`shouldAutoBaseline` 三态那一组已随它一起删
 *  ——它存在的唯一理由是「离线开课 = 本机不跑训练 ⇒ it0 读数两条产路都不通」。今天开课不再指定
 *  模式（本机恒跑、主循环 `loop_baseline` 就在场上；整段被云机领走时基线由云腿产出），
 *  那层替代产路被设计性地拆掉了（见 `course-lifecycle.ts` 的对应注释）。人工入口仍在：
 *  面板的 evalA 按钮与「导入训练结果后自动评估」照旧走 `evalAArgs`（本文件两条用例）。
 */

import { describe, expect, it } from 'bun:test'
import { evalAArgs } from '../src/server/eval-a-run'

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
