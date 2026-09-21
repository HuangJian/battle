/** actions/ — 控制台动作层：组件 启/停/冒烟 · 模式开关 · 节点编辑（回写 rl-config）。
 *
 *  与 CLI 启动器的关系：复用同一套 spawn/账本/冒烟/哨兵原语（core/*、stack/*），
 *  但动作是**面向单组件**的（页面上点某个组件的启动/停止），而 launch/cli 的
 *  hub/push 模式是「整条流水线一次拉起」。组件间依赖由 preset 的顺序声明
 *  （hub-server 先于 cloudflared/trainer；self-node 是采集底线）。
 *
 *  并发纪律：每个动作是短命异步流程；同一 key 的并发点击被 busy 互斥集合挡住
 *  （第二次调用直接抛 ActionError，由 api 层转 409），不做排队。
 *
 *  模式开关落点：stream / double_buffer / precollect_early 是 run_rl 的真实
 *  rl-config 键 → 直接回写 rl-config.json（run_rl 下次启动即生效）；trainer 的
 *  pull/push/local 是控制台的基建编排选择（pull=remote+隧道，push=remote 无本地
 *  隧道，local=本机 PPO）→ 存 console-state.json，trainer 启动时翻译成
 *  --ppo/--env 与基建组件组合。
 *
 *  模块地图（api/route.ts 只依赖本目录的公共出口）：
 *    result          公共契约（ActionError / busy / ActionResult）
 *    start / stop / smoke   组件生命周期      |  preset  模式预设与开关
 *    console-state   控制台状态读写            |  nodes  节点配置回写
 *    cloud-halt      云端停机与恢复            |  labels  标签与锁查询
 *    train-smoke     推送链路预演              |  restart  变更检测重启
 *    workers         GPU push worker 登记（回写 rl-config）
 *    course-lifecycle 开课/停课（**独立于进程启停**：进程启动不再顺带开课）
 */

// ── facade 再导出（原 actions.ts 第 22 行的转发；调用方零改动）──
export { resolveCourseBc } from '../../stack/courses'

// ── 内部模块（唯一对外出口；调用方一律 import 本目录，不直连内部文件）──
export * from './result'
export * from './console-state'
export * from './cloud-halt'
export * from './course-mode'
export * from './poison'
export * from './course-lifecycle'
export * from './loop-control'
export * from './labels'
export * from './start'
export * from './stop'
export * from './smoke'
export * from './preset'
export * from './nodes'
export * from './workers'
export * from './train-smoke'
export * from './restart'
