/** view/ — 训练控制台共享视图层（客户端安全：禁 import node: / fs / Bun 与服务端 api、actions 层）。
 *
 *  单一事实源：视图类型（ConsoleStateView / PoolView / IterRow …）与全部纯函数
 *  （sparkPoints / fmtTs / fmtPct / sortRows / filterGroups / shouldFollow …）。
 *  服务端（server 下的 api、iters、pool-history）与浏览器组件都从本目录 import。
 *
 *  分层纪律：
 *    - 本目录内**只允许**类型与纯函数：无 IO、无 node: 模块、无 Bun 全局（有禁词断言兜底）；
 *    - 调用方一律 import 本目录（`../web/view` 或 `../../view`），**不得直连内部文件**——
 *      内部拆分可自由演进，只由 index.ts 的再导出面兜底；
 *    - 依赖方向单向：web/app/** 与 server/** 可 import 本目录，反向禁止。
 *
 *  模块地图（按数据来源分组）：
 *    console-types  控制台整页视图  |  metric-types / bc-types  训练指标与 BC 账本行
 *    component-groups 组件卡分组（服务面单例 vs 课程面按课程）
 *    pool-types     节点池视图      |  eval-types / eval-export  评估板
 *    log-view       日志页          |  cards  卡片注册表
 *    loop-queue     调度器每课队列视图（单例卡片的读面）
 *    format / spark / series / phase / rows / interaction  纯函数工具
 *    legacy-keys    浏览器本地键迁移
 *    course-overview 多课程并行总览（hub 侧）/ push worker 登记
 *    course-matrix  课程矩阵：并行总览 × 训练调度器的 outer join（问题 C5）
 *    routes         控制台路由（路径 ↔ 页面键、导航项、激活判定）
 */

// ── 内部模块（唯一对外出口；调用方一律 import 本目录，不直连内部文件）──
export * from './console-types'
export * from './component-groups'
export * from './bc-types'
export * from './log-view'
export * from './metric-types'
export * from './pool-types'
export * from './eval-types'
export * from './eval-export'
export * from './format'
export * from './spark'
export * from './series'
export * from './phase'
export * from './rows'
export * from './interaction'
export * from './legacy-keys'
export * from './cards'
export * from './course-overview'
export * from './loop-queue'
export * from './course-matrix'
export * from './routes'
