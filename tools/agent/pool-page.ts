/** pool-page.ts — /pool 节点池监控页（兼容壳，2026-09-06 迁移）。

 *  实现已整体移植到 tools/training/monitor/（page.ts = 视图组装，history.ts /
 *  iters.ts = 数据聚合，theme.ts = 样式）。本文件保留原导出签名，让已部署节点上
 *  的 sampler-agent.ts（GET /pool 的 mtime 键控动态 import，DECISIONS §341）在
 *  git pull 升级后无缝指向新实现——旧路径不再维护，后续只改 monitor/。
 */

export {
  renderMonitorPage as renderPoolPage,
  type MonitorCtx as PoolPageCtx,
} from '../training/monitor/index'
