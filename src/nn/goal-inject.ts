/**
 * goal-inject.ts — goal 网络的 9 维运行时注入（plan/Goal-Space-Policy-Rebuild.md §8.1.1）。
 *
 * 宽度保持 9（零形状 churn：hiddenInject 137 不变），语义从 intent 的
 * "prev-intent one-hot(8) + duration" 重定义为 goal-space 时序态：
 *
 * | 维 | 语义                          | 范围      |
 * | 0  | prevGoalRow / 26              | [0,1]     |
 * | 1  | prevGoalCol / 26              | [0,1]     |
 * | 2  | min(duration, 300) / 300      | [0,1]     |
 * | 3  | min(switches, 10) / 10        | [0,1]     |
 * | 4  | arrived ? 1 : 0               | {0,1}     |
 * | 5–8| 保留恒 0（前向兼容，防 schema bump） | —    |
 *
 * ⚠️ duration 分布漂移（评审 a1，§8.1.1）：标注期 replan=30 ⇒ 维 2 ∈ [0, 0.1]，
 * 部署承诺期 150–240 ⇒ ∈ [0.5, 0.8] —— 语料必须混入长承诺样本（T6 验收项）。
 *
 * 本模块是 TS/Py 两端共享语义的 TS 常量源：goal_net.py 的 golden 注入与
 * GoalExecutor / export-goal-rollout 的注入构造都走 writeGoalInject()。
 */

export const GOAL_INJECT_DIM = 9

/** 保留维（5–8）起点；这些维恒 0，启用新维 = 新 DECISIONS 条目 + golden 更新。 */
export const GOAL_INJECT_RESERVED_FROM = 5

/** duration / switches 的归一化上限（与 §8.1.1 表一致）。 */
export const GOAL_INJECT_DURATION_CAP = 300
export const GOAL_INJECT_SWITCHES_CAP = 10

/**
 * 写入 9 维注入（复用 dst 缓冲，零分配；调用方持有常驻 buffer）。
 * prevGoalRow/Col < 0 表示"尚无上一目标"（局首帧）⇒ 维 0/1 置 0。
 */
export function writeGoalInject(
  dst: Float32Array,
  prevGoalRow: number,
  prevGoalCol: number,
  durationTicks: number,
  switches: number,
  arrived: boolean,
): Float32Array {
  dst[0] = prevGoalRow >= 0 ? prevGoalRow / 26 : 0
  dst[1] = prevGoalCol >= 0 ? prevGoalCol / 26 : 0
  dst[2] = Math.min(durationTicks, GOAL_INJECT_DURATION_CAP) / GOAL_INJECT_DURATION_CAP
  dst[3] = Math.min(switches, GOAL_INJECT_SWITCHES_CAP) / GOAL_INJECT_SWITCHES_CAP
  dst[4] = arrived ? 1 : 0
  for (let i = GOAL_INJECT_RESERVED_FROM; i < GOAL_INJECT_DIM; i++) dst[i] = 0
  return dst
}
