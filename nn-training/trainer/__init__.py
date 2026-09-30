"""trainer —— **训练编排层**（2026-09-30 刀 5 前的 `rl/`；整包改名，成员一个没变）。

## 这个包是什么

「**怎么跑**」：驱动 rollout 采集、派发评估、推远端腿、收报告、走完一轮课程 —— 会碰传输层
（`remote/` · `worker/`）的那一层。分层位置 **L4**（与 `hub/` 同层，两者互不 import）。

```text
L0  common/                                     （stdlib-only 原语）
L1  biz/                                       （游戏业务）
L2  worker/                                     （本地 torch 训练全栈）
L3  remote/                                     （跨端线路 + 云引导 + 云 worker）
L4  trainer/（刀 5 前的 rl/） · hub/ · 根入口（trainer/run_rl.py / trainer/run_bc.py / trainer/run_rl_cluster.py …）
```

## 为什么只剩编排（2026-09-30 刀 4，家名于刀 5 改成 trainer）

本包曾同时住着两种东西：**纯逻辑**（课程 / 奖励 / 门 / 账本 / 配额 —— 不碰传输层）与**编排**
（本包现存的这些）。同名同包让依赖方向读不出来：`trainer/queue.py`（编排，import `remote`）与
同一包里的 `course.py`（纯逻辑，只碰 stdlib）看上去是同一层。刀 4 按**机械判据**把它们分开：

```text
biz/<mod>.py  ⇔  <旧 rl 树里不属于 TRAINER_ORCHESTRATION 的模块>
                 （刀 4 当天写作 `rl/*.py − RL_ORCHESTRATION`；刀 5 后家名换成 trainer/）
```

于是本包现在**每个模块都可达 `remote|worker`** —— 这条由
`tests/test_layering.py::test_trainer_holds_only_orchestration_modules` 与
`test_trainer_orchestration_set_is_exactly_the_modules_reaching_remote` 双向钉住
（往这里丢一个纯逻辑模块、或让某个模块不再碰传输层，都红）。

## 模块（43）

> 2026-09-30（刀 7）：六个**入口脚本**从 `nn-training/` 顶层搬进本包（此前它们只能靠
> 「与本包并列的根文件」这一形状存在）⇒ `nn-training/` 下如今只剩 `conftest.py`。入口进包后
> 三条既有前提要重算：脚本目录 `trainer/` 会进 `sys.path[0]`（其 `queue.py` 遮蔽 stdlib
> `queue`）⇒ 六个入口各自前置一段「摘目录项 + 放回 nn-training 根」（惯用法见
> `trainer/eval_a_once.py`）；`Path(__file__)` 的上溯层数 +1；启动器 `--script` 随之写成
> `trainer/run_rl.py`（它本来就接受 nn-training/ 下的相对路径，见 `launch/cli.ts`）。

| 簇 | 模块 | 内容 |
|----|------|------|
| **入口** | `run_rl` | RL 训练入口（两阶段 argparse / `--mode` 分派；刀 7 前住 `nn-training/` 顶层） |
| | `run_bc` | BC 编排器入口薄壳（引擎在 `trainer/bc_loop.py`；刀 7 前住顶层） |
| | `run_rl_cluster` | 单进程多课程调度器入口（`--serve` / `--json`；刀 7 前住顶层） |
| | `train_loop` | 单进程 BC 训练入口（轮次驱动 + 锁；刀 7 前住顶层） |
| | `eval_course_once` · `eval_m1_once` | 一次性评估入口（课程 / m1；刀 7 前住顶层） |
| | `loop` | `run_training` 入口（薄包装） |
| | `loop_runner` | 训练主循环的启动/接管面 |
| | `loop_serve` | 常驻服务模式（`--serve`：从 hub 领任务跑课程；`kind ∈ {rl, bc}`） |
| **主循环组合** | `loop_core` | `TrainingLoop` **组合根**（只剩 `__init__` 槽位 + `_run_inspect`） |
| | `loop_lifecycle` | `TrainingLifecycle` mixin（setup / run 编排 / 轮派发 / 收官 / 停车） |
| | `loop_steps` | `TrainingSteps` mixin（课程 / 落账 / 取证；基类 Remote + Eval + Export） |
| | `loop_round_steps` | `RoundSteps`（一轮 13 步的执行体；基类 Volume/Baseline/IterDir/Dispatch） |
| | `loop_control` | 控制面（暂停/恢复/减配的轮内检查点） |
| **采集与配额** | `rollout_phase` | 单轮采集派发三路 + 双缓冲预采句柄 |
| | `loop_volume` | `TrainingVolume` mixin（初波/补波/连续配额 → 派发 → 报告合并） |
| | `collect_only` | 只采集模式（不更新权重的一轮） |
| | `stream` | 流式迭代（采集与 PPO 波次重叠） |
| | `queue` · `queue_local` | 派发队列（race-tier/dup/pick）· 纯本地 rollout/竞速（两者直连 `worker.serve_pool`） |
| | `dispatch` | `RolloutDispatcher`（远端节点 + 本地槽位 + 竞速派档） |
| **评估派发** | `eval_dispatch` | 干净评估分发（`EvalDispatcher` + 线程入口） |
| | `eval_a_once` | A 层单次干净评估（一次一批） |
| | `eval_m1` | m1-eval 管线（intent/goal 整批 + Δ 止损，DECISIONS §307） |
| | `loop_eval` | `TrainingEval` mixin（in-loop 评估链：派发 / 尾巴收拢 / join / drain） |
| | `loop_dispatch` | `TrainingDispatch` mixin（采集三路 / A-eval 稀疏化 / EvalBoard 关窗） |
| | `loop_baseline` | `TrainingBaseline` mixin（it0 基线评估：指纹 + 落地摘 → 派/不派） |
| | `loop_iter_dir` | `TrainingIterDir` mixin（本轮目录续跑保留/重建 + 零 shard 配额告警） |
| **批处理（B 层）** | `batch_eval` | 批评估**门面**（`BatchEvalRunner` 的公开面） |
| | `batch_plan` | 批语料规划 + 瞬时判据的薄转发（409 自愈的 B 层落点） |
| | `batch_runner` | `BatchEvalRunner` / `dispatch_batch_bg`（执行面） |
| | `batch_store` | 批台账（`BatchStore`；具名转移，零手写写盘） |
| **护栏组合** | `loop_guards` | `TrainingGuards` **组合根**（共享 sink：账本视图增量 + 判决→云机达令；四簇混入住 `biz/`） |
| **远端腿** | `loop_remote` | `TrainingRemote` **组合根**（远端 PPO 腿；零方法） |
| | `loop_remote_push` | 直推腿 mixin（把 job 送到节点） |
| | `loop_remote_job` | 一个远端 PPO job 的四步 mixin（发布 → 探活 → 取回 → 三重校验落位） |
| | `loop_remote_fail` | 远端失败策略 mixin（确定性失败停腿 / 连败配额） |
| | `loop_remote_drive` | 驱动入口 mixin（轮内三相 / 整轮上云 / 半离线整段） |
| **产物出包** | `loop_export` | `TrainingExport` mixin（TS 码 zip / 计划采集块 / 全离线包 / 权重归档） |
| **BC 腿** | `bc_loop` | BC 训练主循环（云端/本地每 epoch 回传的消费与推进） |
| | `bc_ingest` | BC job 回传消费（轮询会话 / 指标·eval 入账 + 节奏常量） |
| **计划与轮** | `loop_plan` | 课程种类判定与轮计划（`kind ∈ {rl, bc}`） |

> 纯逻辑（课程 / 奖励 / 门 / 账本 / 配额 / 四簇护栏混入 …）→ `biz/__init__.py`（64 个模块，含模块表）。
> 入口约定（2026-09-30 刀 7 更新）：入口**住在本包**，启动器按 `nn-training/` 下的相对路径取
> 脚本（`--script trainer/run_rl.py`；`resolveTrainScript` 只拒绝对路径/盘符/`..`，子目录一直
> 是允许的）。旧的裸文件名（`--script run_rl.py`）**不再接受**——它现在会响亮失败
> `script not found`（故意不补 `LEGACY_ALIAS`：别名森林会让「脚本搬家了」永远没人发现）。
"""

from __future__ import annotations

__all__: list[str] = []
