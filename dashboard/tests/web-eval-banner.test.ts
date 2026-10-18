/**
 * web-eval-banner.test.ts — evalA 提示**横幅**（用户 2026-10-18；两个面同一形状）。
 *
 * 用户口径原文：「dashboard 首页点击 evalA 按键后，在趋势图上方显示「evalA 已启动 ……」 提示，
 * 应改为横幅提示，读数回填后自动隐藏横幅」；随后追加「把抽屉里 MetricsTable 的 evalA 反馈也
 * 改成同样的横幅 + 读数回填自动隐藏」。
 *
 * 本文件钉三件事：
 *   ① **长相**（SSR）：`EvalBanner` 是 `tc-banner` 横条（不是原来那行 `tc-notice` 灰字），
 *      成功档 info / 失败档 err，带 `role="status"` 与可关的 ✕；
 *   ② **自动隐藏的判据**（纯函数）：该轮**读数回填**（`evalData` 出现）才消失——别的轮的
 *      历史读数、服务端文案里提到的「正在跑的那轮」都不算；切课即作废；
 *   ③ **接线**：Hero 与 MetricsTable **各自**真的把判据接进 effect、真的渲染这个组件
 *      （任一处的两行删掉，① ② 仍全绿——那正是「组件很漂亮但没人用」的盲区）。
 *
 * 为什么自动隐藏只能测纯函数：两个面都是客户端状态机（preact hooks），SSR 首帧**恒无横幅**
 * （状态只能由点击产生）——把判据埋在 effect 里，就只能靠人眼验「跑完它自己没了」。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { evalBannerDone, type EvalABanner, type IterRow } from '../src/web/view'
import { EvalBanner } from '../src/web/components/EvalBanner'

const REPO = join(import.meta.dir, '..')

/** 只填本文件关心的事实（iter + evalData），其余走真实缺省形状。 */
function row(iter: number, withEval = false): IterRow {
  return {
    iter,
    time: '2026-10-18 09:00:00',
    winRate: 0.1,
    scoreMean: 1,
    scoreStd: 0.1,
    samples: 100,
    rolloutSec: 60,
    ppoSec: 120,
    pureCollectSec: null,
    ppoCloudSec: null,
    distPhaseSec: null,
    kl: 0.02,
    entropy: 0.3,
    policyLoss: 0.1,
    valueLoss: 0.2,
    meanRet: 0.1,
    lr: 0.0001,
    expectedGames: 100,
    halted: false,
    topDims: '',
    avgTicks: 900,
    accuracy: 0.5,
    loot: 0.1,
    kills: 5,
    actuals: null,
    evalData: withEval
      ? ({
          iter,
          time: '2026-10-18 09:10:00',
          winRate: 0.7,
          games: 100,
          wins: 70,
          clears: 0,
          sec: 120,
          wver: 'abcdef1234',
          dropped: 0,
          closeReason: null,
        } as unknown as NonNullable<IterRow['evalData']>)
      : null,
  }
}

const banner: EvalABanner = {
  course: 'y22-null',
  iter: 120,
  message: 'evalA 已启动 it120（课程干净评估 → eval_log，完成后读数自动回填）',
  ok: true,
}

describe('evalBannerDone：读数回填才收横幅', () => {
  it('刚点完（还没有该轮读数）⇒ 横幅留着', () => {
    expect(evalBannerDone(banner, [row(119, true), row(120)], 'y22-null')).toBe(false)
  })

  it('★ 点的那一轮出现 evalData ⇒ 自动隐藏', () => {
    expect(evalBannerDone(banner, [row(119), row(120, true)], 'y22-null')).toBe(true)
  })

  it('别的轮有 evalData 不算完成（历史读数不是这次动作的结果）', () => {
    expect(evalBannerDone(banner, [row(120), row(121, true)], 'y22-null')).toBe(false)
  })

  it('切到别的课程 ⇒ 立刻作废（横幅回答的是「这门课刚做了什么」）', () => {
    expect(evalBannerDone(banner, [row(120, true)], 'y22-lr')).toBe(true)
  })

  it('失败横幅同样按事实收：读数若照样到了也藏（失败文案不享受额外豁免）', () => {
    const failed = { ...banner, ok: false, message: 'evalA 启动失败：x' }
    expect(evalBannerDone(failed, [row(120, true)], 'y22-null')).toBe(true)
    // 失败且没有读数 ⇒ 留着给用户看（它自己不会消失）
    expect(evalBannerDone(failed, [row(120)], 'y22-null')).toBe(false)
  })
})

describe('EvalBanner：横幅形状（SSR）', () => {
  it('成功档 = tc-banner--info 横条 + role=status + 可关的 ✕', () => {
    const html = renderToString(h(EvalBanner, { banner, onDismiss: () => {} }))
    expect(html).toContain('class="tc-banner tc-banner--info"')
    expect(html).toContain('role="status"')
    expect(html).toContain(banner.message)
    expect(html).toContain('aria-label="关闭 evalA 提示"')
    // 旧实现那行灰字必须消失（换成横幅，不是两条并存）
    expect(html).not.toContain('tc-notice')
  })

  it('失败档 = tc-banner--err（红），文案原样上屏', () => {
    const html = renderToString(
      h(EvalBanner, {
        banner: { ...banner, ok: false, message: 'evalA 启动失败：boom' },
        onDismiss: () => {},
      }),
    )
    expect(html).toContain('class="tc-banner tc-banner--err"')
    expect(html).toContain('evalA 启动失败：boom')
  })
})

describe('接线：两个面都真的用它（不然纯函数与组件都只是摆设）', () => {
  // 两个面各钉两行：渲染横幅 + effect 调判据。源码级钉子（与 result-stamp.test.ts 同手法）——
  // 面板级状态机在 SSR 里跑不到，删掉任何一行都不会有别的用例变红。
  for (const file of ['Hero.tsx', 'MetricsTable.tsx']) {
    it(`${file}：渲染 EvalBanner + effect 调 evalBannerDone`, () => {
      const src = readFileSync(join(REPO, 'src', 'web', 'app', 'panels', file), 'utf8')
      expect(src).toContain('<EvalBanner banner={evalBanner}')
      expect(src).toContain('evalBannerDone(b, iters, course)')
    })
  }
})
