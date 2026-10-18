/** EvalBanner.tsx — evalA 提示**横幅**（首页走势图上方 / 指标抽屉顶部，两处同一形状）。
 *
 *  用户口径（2026-10-18）：「点击 evalA 按键后，在趋势图上方显示『evalA 已启动 …』提示，应改为
 *  横幅提示，读数回填后自动隐藏横幅」；随后追加「抽屉里 MetricsTable 的 evalA 反馈也改成同样的
 *  横幅 + 读数回填自动隐藏」。两个面因此共用这一个组件（形状一处、判据一处）。
 *
 *  为什么不是 `InlineNotice` 一行灰字：那是面板内「刚做了什么」的就地反馈样式，混在走势图/表格
 *  上方像页脚——evalA 是**长任务**（几十秒到几分钟），用户要的是一条「它还在跑、跑完读数会自动
 *  回填」的持续提示。所以给它 `tc-banner` 横条（`role="status"`、带边框），失败转红档，可手动关。
 *
 *  自己消失的条件**不在这里**：`view/eval-a-banner.ts::evalBannerDone()`（纯函数，可单测）——
 *  组件只负责画。导出供 SSR 用例钉形状（两个面首帧都恒无横幅：状态只能由点击产生）。
 */

import type { EvalABanner } from '../view'

export function EvalBanner({ banner, onDismiss }: { banner: EvalABanner; onDismiss: () => void }) {
  return (
    <div
      className={`tc-banner tc-banner--${banner.ok ? 'info' : 'err'}`}
      role="status"
      aria-label="evalA 提示"
    >
      <span>{banner.message}</span>
      <button
        type="button"
        className="tc-iconbtn"
        aria-label="关闭 evalA 提示"
        title="关闭（读数回填后它会自己消失）"
        onClick={onDismiss}
      >
        ✕
      </button>
    </div>
  )
}
