# nn-training — Battle City Web 玩家 AI 训练工坊

> 确定性、可复现、零资产依赖。浏览器里的坦克由 SVG 绘制、声音由 Web Audio 合成，
> 这里的权重也由纯 stdlib + numpy + torch 在 CPU 上训出——没有云服务、没有外部数据集。

---

## 模块地图

```
nn-training/
├── pyproject.toml           # 包元数据 + ruff / mypy / pytest 配置
├── task.py                  # 跨平台 task runner  ("make check" 等价)
├── Makefile                 # 同上的 make 封装（Git Bash 用户）
├── .python-version          # CPython 3.13
├── rl-config.json           # RL 训练配置（rl-mode 默认值、workers、eval-seeds 等）
├── requirements.txt         # pip 依赖（torch / numpy / dev-tools）
├── start-training.sh / .ps1 # 统一启动器（venv bootstrap、线程环境、锁）
│
├── run_rl.py                # 【RL 编排入口】三模式 CLI + 迭代主循环 + 权重归档/熔断
│                              #   --mode {per-tick, intent, goal}（DECISIONS §307 整合）
├── train_loop.py            # 【BC 训练入口】连续行为克隆（与 run_rl 并列）
│
├── schema.py                # 张量布局常量（OBS_CHANNELS / MOVE_DIM / ...）— TS 侧共享
├── dist_common.py           # 分布式采样器协议（std-lib-only，可脱离 torch 单测）
├── platform_utils.py        # 跨平台子进程（Windows CREATE_NO_WINDOW 等）
├── weights_prune.py         # 权重文件归档轮转管理器（保留最近 K 份，自动更新 WEIGHTS.md）
│
├── models/                  # 【模型包】神经网络定义 + 权重导入导出
│   ├── core.py              #    NNPolicy：Conv(14→32→48→64) + GAP + FC + 双头（BC 基座）
│   ├── student.py           #    StudentNet / PPOStudent：CoordConv-ConvMixer-Lite（P1.5 蒸馏）
│   ├── rl_model.py          #    RLNet：ResNet-11 教师网（P1 参考实现，~999K 参数）
│   ├── intent_net.py        #    IntentNet：意图策略网（8 类意图 + 注入 + 敌人/锚点三头）
│   └── goal_net.py          #    GoalNet：目标值网络（676/169 路目标格 + 心跳承诺期）
│
├── data/                    # 【数据包】数据集加载 + npy/JSON 权重序列化
│   ├── dataset.py           #    Dataset + mirrorX 在线增强（BC）
│   ├── npyio.py             #    npy 打包工具（shard 写入/读取）
│   └── weights_io.py        #    权重 JSON（反）序列化（save/load_state_into）
│
├── train/                   # 【训练包】训练器脚本（各自含 argparse CLI）
│   ├── bc.py                #    BC 行为克隆训练器（masked CE + value MC 预置）
│   ├── goal_bc.py           #    Goal-Space BC 蒸馏训练器（反事实软目标 CE）
│   └── intent_probe.py      #    M0b 意图可学习性前置探针（divergence-probe 三桶评估）
│
├── ppo/                     # 【PPO 包】on-policy policy gradient 后端
│   ├── engine.py            #    ppo_update / build_ppo / load_episodes / discover_rl_shards
│   ├── common.py            #    masked_logsoftmax / compute_gae / discover_shards / chunk_episodes
│   ├── intent.py            #    IntentNet PPO 适配（意图步 semi-MDP，变步长 GAE γ^Δt）
│   ├── goal.py              #    GoalNet PPO 适配（goal 承诺步，心跳承诺期）
│   └── bench.py             #    PPO 吞吐基准（T1/T2 优化测量）
│
├── rl/                      # 【核心包】纯逻辑、无副作用、可脱离 run_rl.py 单测
│   ├── modes.py             #    三模式注册表 + 启动参数合并（per-tick/intent/goal）
│   ├── course.py            #    语料 (stage,seed) 配对与课程（build_pairs / parse_range）
│   ├── breaker.py           #    F4 熔断状态机（KL/entropy 连击停车）
│   ├── resume.py            #    断点续跑：shard 扫描 + 已完成对恢复
│   ├── reports.py           #    多 shard 聚合（winRate / kl / scoreStats）
│   ├── queue.py             #    派发队列：race-tier / dup / pick（远端节点 + 本地槽位）
│   ├── stream.py            #    流式迭代：wave_params + 软降档（采集与 PPO 波次重叠）
│   ├── eval_dispatch.py     #    评估轮派发（per-tick 贪心局 + eval_log 对账）
│   ├── eval_m1.py           #    M1 评估协议（intent/goal 干净评估 + Δ 止损）
│   ├── archive.py           #    RL 权重归档轮转 + 分支 push
│   ├── log.py               #    log() 落盘（带时间戳统一格式）
│   └── __init__.py          #    包入口文档
│
├── scripts/                 # 【辅助脚本】一次性/诊断工具
│   ├── eval_bridge.py       #    评估桥接（TS ↔ Python 互评）
│   ├── eval_intent_m5.py    #    M5 意图模型端到端评估
│   ├── gen_self_inj.py      #    离线 self-feed 注入生成（scheduled sampling 数据源）
│   ├── init_scratch_weights.py  #  纯从零 RL 的合理随机初始化（A2/A5）
│   └── validate_export.py   #    导出权重验证（shape/格式断言）
│
├── tests/                   # 【测试包】纯逻辑回归 + torch 常驻回归
│   ├── conftest.py          #    共享 fixture（bp_args 等）
│   ├── test_rl_course.py    #    确定性配对 + 课程扩展
│   ├── test_rl_breaker.py   #    熔断状态机
│   ├── test_rl_reports.py   #    聚合不变式
│   ├── test_rl_stream.py    #    wave-params + 残局 clamp
│   ├── test_run_rl.py       #    run_rl.py 编排层回归（含镜像、断点、JSONL 锚点、race-tier）
│   ├── test_run_rl_m1.py    #    三模式整合 + m1-eval 评估管线回归
│   ├── test_ppo_common.py   #    PPO 共享基础设施回归
│   ├── test_ppo_goal.py     #    ppo_goal.py 常驻回归
│   ├── test_ppo_intent.py   #    ppo_intent.py 常驻回归
│   ├── test_rl_model.py     #    rl_model.py（ResNet 教师网）回归
│   ├── test_student_model.py #   student_model.py（CoordConv-ConvMixer-Lite）回归
│   ├── test_train_loop_pure.py  # train_loop.py 纯函数回归
│   └── test_upgrade.py      #    dist_common 主动升级机制回归
│
├── tools/                   # 可视化工具
│   └── plot_training.py     #    train.log 快速取证（无外部依赖）
│
├── tmp/                     # 训练产物（已 .gitignore）
│   ├── rl-weights/weights.json      # 集成层 fixture
│   ├── itest-*.log                  # 集成层日志（RUN_RL_ITEST=1）
│   └── goal-bc/  goal-bc-smoke/  goal-rl*/  intent-probe-hard/
│
└── weights/                 # 快照归档（按模式分组，已 .gitignore）
    ├── intent-rl-weights.it*.json
    └── rl-weights.it*.json
```

---

## 命令拓扑

```bash
# 全部在 nn-training/ 目录下执行

# ── ruff + mypy + fast-test ──
make check               # 或:  python task.py check
make lint                # 或:  python task.py lint
make typecheck           # 或:  python task.py typecheck

# ── 测试分层 ──
make test-fast           # 快速层（纯逻辑 + 无 torch/bun） ≈ 2 s
make test                # 全量 pytest（含 torch 测试）
python tests/test_run_rl.py    # 常驻回归（集成层，可单独运行）
RUN_RL_ITEST=1 python tests/test_run_rl.py   # 启用假 HTTP agent 集成层

# ── 清理 ──
make clean               # 删除 __pycache__ / *.log / 临时产物，保留 weights/

# ── 权重管理 ──
make weights-prune       # 显示将被裁剪的权重（dry-run，保留最新 K 份）
make weights-prune-apply # 实际裁剪权重文件
make weights-update-md   # 重新生成 weights/WEIGHTS.md 目录清单

# ── 训练启动器（venv 由它 bootstrap）──
bash start-training.sh --script run_rl.py --mode intent-rl --stream 1
powershell -File start-training.ps1 -Script run_rl.py -Mode intent-rl -Stream 1
```

---

## 改动红线（本目录内有效，不得突破）

1. **不碰生产算法**：PPO / BC / 课程 / 熔断 / 镜像增强的逻辑行不在此工程化范围内。
2. **不新增运行时行为**：commit 不改变 `python tests/test_run_rl.py` 的输出（ALL PASS 集合）。
3. **不删测试**：搬迁后的原函数体必须同步从 test_run_rl.py 中删除以避免重复定义。
4. **不引入新依赖**：Python 侧仅用 torch / numpy + stdlib；工具链（ruff/mypy/pytest）是 dev-only。
5. **不扩大 scope**：Mermaid 图入口（`run_rl.py` 的 CLI 解析块）暂不拆——改动影响三模式调度。

---

## 分层测试策略

| 文件 | 触发 | 速度 | 依赖 |
|------|------|------|------|
| `tests/test_rl_*.py` | `make test-fast` / pytest | < 2 s | 仅 stdlib |
| `tests/test_run_rl.py` | `python tests/test_run_rl.py` | ≈ 5 s | numpy + run_rl 编排 |
| `tests/test_run_rl.py --itest` | `RUN_RL_ITEST=1` | ≈ 30 s | bun + tmp fixture |
| `tests/test_ppo_*.py` | pytest | ≈ 5 s | torch |
| `tests/test_rl_model.py` | pytest | ≈ 5 s | torch |
| `tests/test_student_model.py` | pytest | ≈ 5 s | torch |
| `tests/test_train_loop_pure.py` | pytest | < 2 s | stdlib |
| `tests/test_upgrade.py` | pytest | < 2 s | stdlib |

先跑 make test-fast，再跑 python tests/test_run_rl.py，最后集成层。

---

## 三模式架构（DECISIONS §307）

`run_rl.py` 是唯一的 RL 入口，通过 `--mode` 选择后端：

| 模式 | 后端 | 模型 | GAE | 说明 |
|------|------|------|-----|------|
| `per-tick` | `ppo/engine.py` | PPOStudent (ConvMixer) | 标准 γ | 逐 tick move/fire PPO |
| `intent` | `ppo/intent.py` | IntentNet (三头) | 变步长 γ^Δt | 意图步 semi-MDP，8 类意图 |
| `goal` | `ppo/goal.py` | GoalNet (目标格) | 变步长 γ^Δt | goal 承诺步，676/169 路目标格 |

`--goal` 是 `--mode goal` 的兼容别名（原 `run_rl_intent.py` 已退役并入本文件）。

---

## 依赖关系图

```
run_rl.py
  ├─→ rl/{modes,course,queue,stream,breaker,resume,reports,eval_dispatch,eval_m1,archive,log}
  ├─→ ppo/{engine,goal,intent}
  ├─→ data/weights_io
  ├─→ dist_common
  └─→ platform_utils

train_loop.py
  ├─→ train/bc
  ├─→ models/{core,student}
  ├─→ data/{dataset,weights_io}
  └─→ schema

train/goal_bc.py → models/goal_net → data/weights_io
train/intent_probe.py → models/{intent_net,student}
scripts/* → models/*, ppo/*
```

---

## 状态

- [x] P0 可复现基座（pyproject.toml / Makefile / task.py）
- [x] P1 type hints（rl/course.py, rl/resume.py）+ ruff/mypy 配置
- [x] P2 测试架构（新增 tests/ + 瘦身 test_run_rl.py + 20 项 pytest 通过）
- [x] P2.5 包化重组（models/ + data/ + train/ + ppo/ + rl/ + scripts/）
- [ ] P3 配置治理（config schema 校验）
- [ ] P4 CI（GitHub Actions + 权重归档自动化）

> 注：本 README 与 `plan/nn-training-refactor.md` 路线图对齐。
