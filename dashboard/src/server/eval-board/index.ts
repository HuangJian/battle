/** eval-board/ — 控制台 EvalBoard 数据端点（plan/rl-eval-system.md §8）。
 *
 *  模块地图：
 *    view         看板视图合成（阶梯/批次/告警/迭代行，TTL 缓存）
 *    ckpts        ckpt/iter 发现与权重清单视图
 *    ladder-data  阶梯正本/工作副本与 runner 心跳
 *    ingest       W1 read-through 入账（课程 ↔ rung 映射）
 *    probe        探针批次入队与中止
 *    auto-ladder  R4 自动爬梯 ticker 与无状态进度推导
 *
 * - `GET /api/evalboard`：独立路由（不塞 /api/state），照抄 /api/pool 模板
 *   （首屏外异步拉 + 30s TTL + 课程键控）。
 * - W1 自动入账：每次构建视图时 read-through——扫描 `tmp/<course>/eval_log.jsonl`
 *   （真实布局：`loop_core.py:189` `_traj_dir = _traj_root/it<N>`，`eval_dispatch.py:93`
 *   取 `traj_dir.parent` ⇒ 账本在 `<traj 根>/eval_log.jsonl`，**不是** `traj/` 子目录；
 *   旧写法路径错误导致恒 0 入账 —— 2026-09-10 修复，保留 legacy 候选兼容），
 *   新行经 `ingestRows` 入 EvalStore（A 层 run_id 代理 = course；
 *   同 course/iter/wver/stage/seed 即同一局，去重天然正确）。
 *   训练循环零改动、零新增跑批成本。
 * - `POST /api/evalProbeRun`：只 append 一行 `status=pending` 到 batches.jsonl
 *  （§6.7 队列文件桥）；派发期节点配置冻结经 busy 前置检查（§8）。
 */

// ── 内部模块（唯一对外出口；调用方一律 import 本目录，不直连内部文件）──
export * from './ckpts'
export * from './ladder-data'
export * from './ingest'
export * from './view'
export * from './probe'
export * from './auto-ladder'
