/**
 * conv-shape.ts —— conv 内核 **TS 侧形状契约的单一来源**（native / wasm / 基准共用）。
 *
 * 事故（2026-11-12）：v7 把 stem 输入通道 18→19（conv_native.h::CF_IN_CH），两个 adapter
 * 和测试里的 dummy 模型都跟着改了，只有 tools/agent/engine-bench.ts 漏改 ⇒ 形状守卫
 * 直接 return false，基准计时空转函数、BENCH-ARM 恒 unknown、假数字进引擎选择缓存。
 * 这正是 2026-09-21「自绘一份布局」评审只修一半留下的复发口：形状必须只有一处定义。
 *
 * 与 C 头的对齐由 tests/agent/engine-bench.test.ts 机械断言（读 CF_IN_CH 文本），
 * C 侧编译期宏无法被 TS import，只能靠测试守住这条跨语言契约。
 */
/** 棋盘边长；CF_SP = BOARD²。 */
export const BOARD = 26
/** stem 输入通道：v7 = 17 obs + 2 coord（旧 18 = 16 + 2）。必须 == conv_native.h::CF_IN_CH。 */
export const IN_CH = 19
export const SP = BOARD * BOARD
/** 隐藏通道数（stem 输出 / dw-pw 宽度）。 */
export const H = 64
/** depthwise 堆叠层数。 */
export const D = 8
/** stem 权重长度 = IN_CH × 3 × 3 × H（== conv_native.h::CF_STEM_W）。 */
export const STEM_W = IN_CH * H * 9
