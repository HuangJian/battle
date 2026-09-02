"""rl — RL 训练核心逻辑包（自 run_rl.py 工程化抽取，2026-08-25）。

分层：
  log.py           带时间戳日志（全链路统一格式）
  course.py        课程：parse_range / build_pairs（(rotateSeed,it) 纯函数）
  reports.py       跨 worker 报告聚合
  resume.py        断点续跑：completed_pairs / resumed_manifests / jsonl 锚点
  breaker.py       F4 熔断纯逻辑（阈值 + 连击判定）
  stop_loss.py     止损判门纯逻辑（Δ 显著 + σ 估计，P1-9）
  queue.py         中央队列调度薄包装（RolloutDispatcher 见 dispatch.py）
  dispatch.py      RolloutDispatcher OO 实现（远端节点 + 本地槽位 + 竞速派档）
  queue_local.py   纯本地 rollout / rescan / 竞速选择纯函数
  cmd.py           bun spawn 命令模板（build_rollout_cmd 统一三 exporter）
  eval_dispatch.py 干净评估分发（EvalDispatcher OO；薄包装 + 线程入口）
  eval_local.py    干净评估纯函数/台账结算（run_local_eval_game 等）
  eval_m1.py       m1-eval 干净评估管线（intent/goal 模式；整批 + Δ 止损，DECISIONS §307）
  stream.py        流式迭代（采集与 PPO 波次重叠）
  loop.py          run_training 入口（薄包装）
  loop_core.py     TrainingLoop 主循环（setup / 迭代编排 / 目录 / 采集派发）
  loop_steps.py    TrainingSteps mixin（结算 / 串行 PPO / 导出 / eval join / 落账）
  loop_guards.py   TrainingGuards mixin（F4 熔断 / 止损 / keepIters 轮转）
  rollout_phase.py 单轮采集派发三路 + 双缓冲预采句柄
  events.py        training_log.jsonl 事件写入（run_start/iteration/circuit_break/iter_error）

入口约定：run_rl.py 必须留在 nn-training/ 顶层 —— 统一启动器只接受裸文件名。
"""
