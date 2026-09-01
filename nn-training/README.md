# nn-training — Battle City Web 玩家 AI 训练工坊

> 确定性、可复现、零资产依赖。浏览器里的坦克由 SVG 绘制、声音由 Web Audio 合成，
> 这里的权重也由纯 stdlib + numpy + torch 在 CPU 上训出——没有云服务、没有外部数据集。

---

## 模块地图

```
nn-training/
├── pyproject.toml           # 包元数据 + ruff / mypy / pytest 配置   ← P0 新增
├── task.py                  # 跨平台 task runner  ("make check" 等价)  ← P0 新增
├── Makefile                 # 同上的 make 封装（Git Bash 用户）       ← P0 新增
├── .python-version          # CPython 3.13
├── start-training.sh / .ps1 # 统一启动器（venv bootstrap、线程环境、锁）
│
├── run_rl.py                # 【编排入口】CLI 解析 + 三模式调度 + 停车
├── train_loop.py            # 【BC 训练入口】连续行为克隆（与 run_rl 并列）
│
├── schema.py                # 张量布局常量（OBS_CHANNELS / MOVE_DIM / ...）
├── dist_common.py           # 分布式采样器协议（std-lib-only，可脱离 torch 单测）
├── ppo.py / ppo_common.py   # PPO 后端（text-book PPO + GAE）
├── ppo_goal.py              # 目标网 PPO 适配
├── ppo_intent.py            # 意图网 PPO 适配
├── dataset.py               # Dataset + mirrorX 在线增强（BC）
├── student_model.py         # 学生网基类（BC 教师-学生蒸馏）
├── intent_net.py            # 意图策略网
├── goal_net.py              # 目标值网络
├── weights_io.py            # 权重 JSON（反）序列化
├── npyio.py                 # npy 打包工具
├── platform_utils.py        # 跨平台子进程（Windows CREATE_NO_WINDOW 等）
│
├── rl/                      # 【核心包】纯逻辑、无副作用、可脱离 run_rl.py 单测
│   ├── course.py            #    语料 (stage,seed) 配对与课程
│   ├── breaker.py           #    F4 熔断状态机（KL/entropy 连击停车）
│   ├── resume.py            #    断点续跑：shard 扫描 + 已完成对恢复
│   ├── reports.py           #    多 shard 聚合（winRate / kl / scoreStats）
│   ├── queue.py             #    派发队列：race-tier / dup / pick
│   ├── stream.py            #    流式迭代：wave_params + 软降档
│   ├── eval_dispatch.py     #    评估轮派发（stream 与 queue 双模）
│   ├── eval_m1.py           #    M1 评估协议
│   ├── log.py               #    log() 落盘
│   └── __init__.py
│
├── tests/                   # 【独立 pytest 包】纯逻辑回归、无 torch/bun   ← P0 新增
│   ├── conftest.py          #    共享 fixture（bp_args 等）
│   ├── test_rl_course.py    #    确定性配对 + 课程扩展
│   ├── test_rl_breaker.py   #    熔断状态机
│   ├── test_rl_reports.py   #    聚合不变式
│   └── test_rl_stream.py    #    wave-params + 残局 clamp
│
├── test_run_rl.py           # 常驻回归：集成层 + 无法拆出的 fixture 测试
│   ├── test_mirror_scalar_lockstep   # BC mirrorX 索引锁步
│   ├── test_resume_scope              # completed_pairs 口径
│   ├── test_jsonl_anchors            # resume manifest 一致性
│   ├── test_compute_gae / chunk_episodes / backup_weights / eval_local_gate / race-tier
│   └── test_integration               # RUN_RL_ITEST=1 时启用（需 bun + weights fixture）
│
├── test_run_rl_m1.py        # M1 集成层回归
├── test_ppo_common.py / test_ppo_goal.py / test_ppo_intent.py
├── test_rl_model.py / test_student_model.py / test_train_loop_pure.py / test_upgrade.py
│
├── tmp/                     # 训练产物（已 .gitignore）
│   ├── rl-weights/weights.json      # 集成层 fixture
│   ├── itest-*.log                  # 集成层日志（RUN_RL_ITEST=1）
│   └── goal-bc/  goal-bc-smoke/  goal-rl*/  
└── weights/                 # 快照归档（按模式分组）
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
make test                # 全量 pytest
python test_run_rl.py    # 常驻回归（集成层带 --itest 环境变量）
RUN_RL_ITEST=1 python test_run_rl.py   # 启用假 HTTP agent 集成层

# ── 清理 ──
make clean               # 删除 __pycache__ / *.log / 临时产物，保留 weights/

# ── 训练启动器（venv 由它 bootstrap）──
bash start-training.sh --script run_rl.py --mode intent-rl --stream 1
powershell -File start-training.ps1 -Script run_rl.py -Mode intent-rl -Stream 1
```

---

## 改动红线（本目录内有效，不得突破）

1. **不碰生产算法**：PPO / BC / 课程 / 熔断 / 镜像增强的逻辑行不在此工程化范围内。
2. **不新增运行时行为**：commit 不改变 `python test_run_rl.py` 的输出（ALL PASS 集合）。
3. **不删测试**：搬迁后的原函数体必须同步从 test_run_rl.py 中删除以避免重复定义。
4. **不引入新依赖**：Python 侧仅用 torch / numpy + stdlib；工具链（ruff/mypy/pytest）是 dev-only。
5. **不扩大 scope**：Mermaid 图入口（`run_rl.py` 的 CLI 解析块）暂不拆——改动影响三模式调度。

---

## 分层测试策略

| 文件 | 触发 | 速度 | 依赖 |
|------|------|------|------|
| `tests/test_rl_*.py` | `make test-fast` / pytest | < 2 s | 仅 stdlib |
| `test_run_rl.py` | `python test_run_rl.py` | ≈ 5 s | numpy + run_rl 编排 |
| `test_run_rl.py --itest` | `RUN_RL_ITEST=1` | ≈ 30 s | bun + tmp fixture |
| `test_ppo_*.py` | pytest | ≈ 5 s | torch |

先跑 make test-fast，再跑 python test_run_rl.py，最后集成层。

---

## 状态

- [x] P0 可复现基座（pyproject.toml / Makefile / task.py）
- [x] P1 type hints（rl/course.py, rl/resume.py）+ ruff/mypy 配置
- [x] P2 测试架构（新增 tests/ + 瘦身 test_run_rl.py + 20 项 pytest 通过）
- [ ] P3 配置治理（config schema 校验）
- [ ] P4 CI（GitHub Actions + 权重归档自动化）

> 注：本 README 是 P0 起逐步增补的，与 `plan/nn-training-refactor.md` 路线图对齐。
