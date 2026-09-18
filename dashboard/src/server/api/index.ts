/** api/ — 控制台 API 层：状态快照（GET /api/state、/api/pool、/api/log/*）+ 动作路由（POST /api/*）。
 *
 *  快照即页面数据源：组件表（进程/健康/日志尾）、节点表（rl-config + 实时 ping）、
 *  模式开关（rl-config rl.* 键 + console-state）、课程数据与训练指标（../iters）。
 *  所有动作经 ../actions（busy 互斥在那一层）；本层只做解析/分发/HTTP 语义映射：
 *  ActionError → 409，参数错误 → 400，动作失败（业务）→ 200 + ok:false。
 *
 *  模块地图（server.ts 只依赖本目录的公共出口）：
 *    route               唯一动作入口（POST 分发）
 *    state-view          主快照组装      |  snapshot-cache / snapshot-refresher  慢部件缓存
 *    views               组件与节点视图  |  component-meta  组件元数据表（日志路径/端口）
 *    logs                日志读取与解码  |  ledger / loop-complete  账本尾派生
 *    courses             课程发现与上下文|
 *    overview            多课程并行总览 + push worker 登记（hub 观测面）
 *    loop-queue          训练调度器每课队列（只读 CLI + 在训事实合并）
 *    pool / eval-games   端点专属视图    |  ppo-queue / curriculum  专项派生
 *    config              配置读取兜底    |  view-types  视图类型透传
 *
 *  迁移纪律：调用方一律 `import ... from '../api'`，**不得直连内部文件**——
 *  内部拆分可自由演进，对外面只由本文件兜底。
 */

// ── facade 再导出（原模块对外转发，保持调用方零改动）──
export { buildEvalBoardView, buildEvalCkptsView, ladderTickAll } from '../eval-board'

// ── 内部模块（唯一对外出口；调用方一律 import 本目录，不直连内部文件）──
export * from './view-types'
export * from './config'
export * from './courses'
export * from './component-meta'
export * from './logs'
export * from './views'
export * from './snapshot-cache'
export * from './ledger'
export * from './loop-complete'
export * from './snapshot-refresher'
export * from './overview'
export * from './loop-queue'
export * from './ppo-queue'
export * from './state-view'
export * from './pool'
export * from './eval-games'
export * from './route'
export * from './curriculum'
