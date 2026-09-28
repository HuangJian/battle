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
  eval_local.py    干净评估执行面（run_local_eval_game / 看门狗 / 行账本衔接；S5 ⑫ 后）
  eval_rows.py     逐局 eval 行 schema + eval_log 账本 I/O（eval_row / merge_eval_rows；S5 第一刀，2026-09-27）
  eval_track.py    双轨日常评估纯逻辑（种子锚点/轮转分类 + 过拟合报警 + settle_eval_summary；S5 第三刀，2026-09-27）
  eval_yield.py    评估让位/份额（尾巴）策略（份额预留 / 让位与放行档 / 越窗与收拢判据；S5 第十二刀，2026-09-27）
  gate_inputs.py   课程门求值器输入读数面（行模型 EvalRow/BudgetInfo + trend/override/事件扫描；S5 第十五刀，2026-09-27）
  gate_judges.py   课程门求值器判决项面（11 种 kind + 统计助手 + 注册表 + `_eval_one` 接口；S5 第十五刀，2026-09-27）
  gate_check.py    课程门求值器引擎（两趟调度 + ADVANCE 附加条件 + CLI；S5 第十五刀后 = 引擎 + 门面）
  config_file.py   rl-config.json 文件面（路径唯一来源 + 安全读取；S5 第十刀，2026-09-27）
  course_spec.py   课程配置类面（CourseConfig/GatesSpec 及嵌套模型 + gates 常量；S5 第十刀，2026-09-27）
  course_resolve.py 课程解析面（curricula/levels 查找 · level 注入 + 冲突拒收 · 字节冻结 · 跨课门校验；S5 第十刀，2026-09-27）
  eval_m1.py       m1-eval 干净评估管线（intent/goal 模式；整批 + Δ 止损，DECISIONS §307）
  stream.py        流式迭代（采集与 PPO 波次重叠）
  loop.py          run_training 入口（薄包装）
  loop_core.py     TrainingLoop **组合根**（纯组合类：只剩 __init__ 槽位 + _run_inspect）
  loop_lifecycle.py TrainingLifecycle mixin（主循环骨架：setup / run 编排 / 轮派发 / 收官 / 停车；S4 第十九刀）
  loop_baseline.py TrainingBaseline mixin（it0 基线评估：指纹 + 落地摘 → 派/不派；S4 第二十刀）
  loop_iter_dir.py TrainingIterDir mixin（本轮目录续跑保留/重建 + 零 shard 配额告警；S4 第二十刀）
  loop_dispatch.py TrainingDispatch mixin（本轮派发与让位：采集三路 / A-eval 稀疏化 / EvalBoard 关窗；S4 第二十刀）
  loop_export.py   TrainingExport mixin（产物出包：TS 码 zip / 计划采集块 / 全离线包 / 权重归档；S4 第二十一刀）
  loop_remote.py   TrainingRemote **组合根**（远端 PPO 腿；零方法，S4 第二十二刀收口）
  loop_remote_push.py  TrainingRemotePush mixin（直推腿：把 job 送到节点；S4 第二十二刀）
  loop_remote_job.py   TrainingRemoteJob mixin（一个远端 PPO job 的四步 + 组合入口；S4 第二十二刀）
  loop_remote_fail.py  TrainingRemoteFail mixin（远端失败策略：确定性停腿 / 连败配额；S4 第二十二刀）
  loop_remote_drive.py TrainingRemoteDrive mixin（驱动入口：轮内三相 / 整轮 / 半离线整段；S4 第二十二刀）
  loop_steps.py    TrainingSteps mixin（课程 / 落账 / 取证；基类 TrainingRemote + TrainingEval + TrainingExport）
  loop_eval.py     TrainingEval mixin（in-loop 评估链：派发 / 尾巴收拢 / join / 收官 drain；S4 第十七刀）
  loop_volume.py   TrainingVolume mixin（动态采集编排：初波/补波/连续配额 → 派发 → 报告合并；S4 第十八刀）
  loop_guards.py   TrainingGuards **组合根**（共享 sink：账本视图增量 / 判决→云机达令；S4 第二十三刀）
  loop_guards_trip.py  TrainingGuardsTrip mixin（过程面硬边界：F4 熔断 / 止损；S4 第二十三刀）
  loop_guards_leg.py   TrainingGuardsLeg mixin（结果面停腿：干烧回锚 / 配对杀臂；S4 第二十三刀）
  loop_guards_gate.py  TrainingGuardsGate mixin（课程结束门：求值 / 判决落地 / 预算硬断；S4 第二十三刀）
  loop_guards_sweep.py TrainingGuardsSweep mixin（轮级磁盘回收：keepIters 轮转 / job 目录；S4 第二十三刀）
  rollout_phase.py 单轮采集派发三路 + 双缓冲预采句柄
  events.py        training_log.jsonl 事件写入（run_start/iteration/circuit_break/iter_error）

入口约定：run_rl.py 必须留在 nn-training/ 顶层 —— 统一启动器只接受裸文件名。
"""
