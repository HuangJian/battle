"""biz —— **领域纯逻辑**（2026-09-30 刀 4；L1，与 `models/` `ppo/` `data/` `train/` `scripts/` 并列）。

本包装的是「**怎么算**」：课程怎么写、奖励怎么算、门/停腿怎么判、评估读数怎么落账、
配额与轮怎么排 —— 全都是**不需要传输层**就能跑的判据与账本。

```text
L0  common/                                    （stdlib-only 原语）
L1  biz/ · models/ · ppo/ · data/ · train/ · scripts/
L2  worker/                                    （节点侧执行体：iter_rollout · serve_pool）
L3  remote/                                    （跨端线路 + 云引导 + 云 worker）
L4  hub/ · trainer/（刀 5 前的 rl/）           （服务端 / 本机训练编排）
```

## 本包是怎么切出来的（机械定义，不是手感）

```text
biz/<mod>.py  ⇔  <mod> ∈ rl/*.py − RL_ORCHESTRATION
                 （刀 4 当天写作；旧名 RL_ORCHESTRATION = 今天的 TRAINER_ORCHESTRATION
                   = tests/test_layering.py 的声明式快照
                   = 「trainer 中直接或经包内传递可达 remote|worker 的模块」）
```

刀 4 之前 `rl/` 一个包里住着两种东西：**纯逻辑**（本包）与**编排**（驱动 rollout / eval /
远端腿的应用层，本质是 L4）。同一个包名让依赖方向读不出来——编排的 `queue.py`（碰 `remote`）
与纯逻辑的 `course.py`（只碰 stdlib）看上去是同一层。刀 3 把节点侧执行体
`remote/{serve_pool,iter_rollout}` 出包时已经暴露过一次这个问题（`biz.reports` 是**向下**边、
`trainer.queue_local` 是**向上**边，同名前缀读不出来）；刀 4 把纯逻辑整族搬出去，剩下的编排
则由刀 5 整包改名 `rl/` → `trainer/`（成员一个没变）。

## 契约（`tests/test_layering.py` 三条断言咬着）

1. **不得 import `remote` / `hub` / `worker` / `trainer`**，也不得 import 编排态 `trainer.*`
   （`test_l1_packages_never_import_the_upper_face` ·
   `test_l1_packages_never_import_trainer_orchestration` ·
   `test_biz_never_imports_trainer_orchestration`）。
   ⇒ 本包**可达**里没有传输层，因此 `remote/` `hub/` `worker/` 引用本包永远是**向下**边。
2. 但「可达」不等于「允许随手加边」：传输/落盘/作业面（`remote/` `hub/` `worker/`）引用本包
   要在各自的 `assert_remote_module(..., allowed_rl=…)` 表里**点名登记**
   （`tests/helpers/remote_dag.py` 的 L2-pure 口径）——多一条边就红。
3. 本包**是 L1 不是 L0**：`common/` 不许反过来 import 本包（`test_common_is_a_leaf_package`）。
   依赖方向永远 `biz → common`，绝不反向。

> ⚠ 加模块到本包之前先问：**它需要传输层吗？** 需要就不属于本包（那是编排，住 `trainer/`）；
> 不需要但只服务训练主循环的调度实现，也先看它是否已有归属（`loop_*` 编排族住 `trainer/`）。

## 模块（64）

| 域 | 模块 | 内容 |
|----|------|------|
| **课程 / 配置** | `course` | 每轮采哪些 `(stage, seed)`（`build_pairs` / `parse_range`） |
| | `course_spec` | 课程配置类面（`CourseConfig` / `GatesSpec` + gates 常量；`curricula/*.jsonc`） |
| | `course_resolve` | 课程/关卡查找与加载（level 注入 + 冲突拒收 + 字节冻结） |
| | `course_archive` | 课程封存：已停课程搬成只读档案 |
| | `config` | `RLConfig` 启动参数校验层（pydantic） |
| | `config_file` | `rl-config.json` 文件面（路径唯一来源 + 安全读取） |
| | `cli` | `build_argparser`（run_rl CLI 参数声明，默认值取自 rl-config.json） |
| | `modes` | 三模式（per-tick / intent / goal）注册表 + 启动参数合并 |
| | `hot_reload` | 课程热加载：每 iter 重读课程文件、按语料身份分流 |
| | `bc_config` | BC 课程配置（`curricula/<name>.bc.jsonc`，独立于 RL 的 `CourseConfig`） |
| | `plan` | 「半离线整段训练」的计划文件（kind=run 的 `plan.json`） |
| **奖励** | `reward_library` | 奖励公式引擎（唯一定义源在 Python；TS 只落 41 维指标向量） |
| | `reward_builtin` | 内置势函数（降级卡回退目标 + 公式编码对账基准） |
| | `reward_context` | 加载期 holder（`update_kwargs` 到不了 `compute_gae` 的那个缝） |
| | `reward_validation` | 公式静态校验 + 数值包络（三层归一化） |
| | `schedule` | 超参分段表查表（`ppo_schedule`） |
| **评估读数 / 账本** | `eval_rows` | 逐局 eval 行 schema + `eval_log.jsonl` 账本 I/O |
| | `eval_ingest` | EvalBench 入账（per-tick 链路与 m1 链路共用） |
| | `eval_track` | 双轨日常评估（锚点/轮转分类 + 过拟合报警 + summary 结算） |
| | `eval_yield` | 评估让位/份额（尾巴）策略判决面 |
| | `eval_local` | 干净评估的纯函数/无状态工具（看门狗 + 行账本衔接） |
| | `eval_replays_once` | 从指定 iter 的 in-loop eval 导出 replay（控制台「导出 replay」后端） |
| | `eval_heartbeat` | EvalBoard runner 心跳（单文件原子覆盖写） |
| | `reports` | 跨 worker 报告聚合（本地 rollout 与远端单局摘要共用） |
| | `resume` | 断点续跑：shard 对账 + `training_log.jsonl` 锚点回读 |
| | `train_ledger` | `training_log.jsonl` 的单一只读视图（R2a） |
| | `bc_ledger` | BC 轮账本最小读/写面（BC 编排器与只读计划视图共用） |
| | `agent_meta` | 节点采样元数据账本（`dist-agent-meta.jsonl`）唯一写面 |
| | `commit_journal` | PPO 提交序列 WAL（started/done） |
| | `metrics_stats` | 每 iter 指标统计量落盘（`metrics_stats.jsonl`） |
| | `terminal_stats` | 每 iter 终局指标聚合（只读 `manifest.json`，O(#games)） |
| | `ladder_ledger` | 阶梯统一 identity 台账（20 级阶梯 + 经典 35 关一张表） |
| **门 / 停腿判据** | `gate_check` | 课程结束门求值器（两趟调度 + ADVANCE 附加条件 + CLI） |
| | `gate_inputs` | 门求值器输入读数面（`EvalRow` / `BudgetInfo` + trend/override/事件扫描） |
| | `gate_judges` | 门判决项面（11 种 kind + 统计助手 + 注册表） |
| | `breaker` | F4 熔断（KL/entropy 连击停车） |
| | `stop_loss` | 止损判门（Δ 显著 + σ 估计，P1-9） |
| | `kickstart_burn` | kickstart 干烧熔断（结果面） |
| | `paired` | 配对 rotateSeed 核对 |
| | `paired_kill` | 配对中点杀臂（连续 2 点 <−3pp） |
| | `loop_guards_trip` | `TrainingGuardsTrip` mixin：过程面硬边界（熔断/止损） |
| | `loop_guards_leg` | `TrainingGuardsLeg` mixin：结果面停腿（干烧回锚 / 配对杀臂） |
| | `loop_guards_gate` | `TrainingGuardsGate` mixin：课程结束门（求值/判决落地/预算硬断） |
| | `loop_guards_sweep` | `TrainingGuardsSweep` mixin：轮级磁盘回收（keepIters 轮转） |
| **采集配额 / 轮模型** | `volume_quota` | 配额感知连续采集（替代离散补波） |
| | `volume_waves` | 按样本量动态采集的纯逻辑（初波反解 + 补波） |
| | `loop_tasks` | 训练循环任务模型（一轮 = 有依赖的任务 DAG） |
| | `loop_scheduler` | 单进程多课程任务调度器（R2c-1） |
| | `loop_round` | 轮内上下文（`RoundContext`）与 13 步表（R2c-3） |
| | `iter_job` | `kind=iter` job 的 hub 侧规格构造（只换路径，不改命令） |
| | `cmd` | `build_rollout_cmd` rollout 子进程命令模板（统一三 exporter） |
| | `backend` | `RolloutBackend` 契约（Protocol）——把 `stream.py` 的 duck typing 变可执行断言 |
| **BC** | `bc_dispatch` | BC 语料 LAN 集群派发（`/v1/task ?mode=bc`） |
| | `bc_eval` | BC 每 N epoch 的多地图干净评估派发 |
| **运行时 / 运维** | `log` | 统一日志基础设施（带时间戳 stdout 行 + 落盘） |
| | `events` | `training_log.jsonl` 事件写入（run_start / iteration / circuit_break / iter_error） |
| | `forensics` | OOM/磁盘取证插桩（I1 第 0 步） |
| | `workdir_sweep` | 本地采样路径的临时目录收敛（§374） |
| | `state_init` | rollout 起始分布（人类中段快照）的派生与护栏 |
| | `archive` | RL 权重归档轮转 + 分支 push |
| | `node_identity` | 「同 node 的 bootId 与本轮账本不一致 ⇒ 本轮排除」的训练侧判据 |
| | `engine_pool` | 单进程多课程共享的 torch 引擎持有者（R2c-3 前置） |
| | `model_build` | `build_model`：BC warm-start / RL resume / A4 归一校准 |
| | `ladder_factory` | 阶梯工厂（c01..c20 关卡 + 课程 + 计划台账，数据不是代码） |
"""

from __future__ import annotations

__all__: list[str] = []
