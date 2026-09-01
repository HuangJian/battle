# nn-training 工程化改造计划

> **目标**：在**零 runtime 行为变化**的前提下，把 `nn-training/` 从「能跑」提升到「工程级可维护、可复现、可多人协作」。
> **红线**：本重构**绝不**改动任何训练算法逻辑（PPO/BC/课程/熔断）、不换依赖版本、不动数据 schema。纯工程包装。

---

## 1. 现状诊断

### 1.1 规模

- ~50 个 Python 文件
- ~7000 行非测试生产代码 + ~1800 行测试代码
- 9 个测试文件、约 65 个测试函数
- 4 个关键目录：`rl/`、`weights/`、`tmp/`、`.venv/`

### 1.2 已有优势（保持）

| 优势 | 位置 |
|------|------|
| 三层架构分离（编排入口 → rl/ 包 → 算法后端 ppo*.py） | `run_rl.py` + `rl/` |
| 双平台统一启动器（venv bootstrap + 线程环境 + 锁） | `start-training.sh` / `.ps1` |
| 断点续跑 / 流式 PPO / F4 熔断 / 语料轮转 | `rl/resume.py` / `stream.py` / `breaker.py` / `course.py` |
| `(rotateSeed, it)` 纯函数键控种子流（防记忆化） | `rl/course.py` |

### 1.3 核心工程化欠账

| # | 问题 | 影响 |
|---|------|------|
| 1 | 无 `pyproject.toml`，不可 `pip install -e .`，工具链无统一入口 | 新人 onboarding 靠口口相传 |
| 2 | 7000 行代码几乎无 type hints | 改接口靠 runtime 撞 bug |
| 3 | `test_run_rl.py` 813 行含 17 个函数，快速层/集成层堆叠 | 跑一次单层测试需加载整包 |
| 4 | `run_rl.py` 1248 行塞进 CLI + 三模式 + 归档 + 熔断 + 主循环 | 任一改动都需读懂全文件 |
| 5 | `tmp/` 1.82MB 残片、54 个 `__pycache__`、无轮转日志 | 磁盘膨胀 + 复盘找不到日志 |
| 6 | `rl-config.json` 无 schema 验证 | 配错 key 要到 PPO 第三轮才崩 |
| 7 | `weights/` 41 个 json 靠 WEIGHTS.md 人肉同步 | 回溯最佳权重易失效 |
| 8 | Python 侧无 lint / format / CI | 风格不一致靠肉眼 |

---

## 2. 阶段划分

每个阶段独立可验证，**按顺序执行**。阶段间不强依赖但前置阶段的稳定性让后续改动更安全。

### P0 —— 可复现基座（纯包装，零 runtime 改动）

- [ ] 添加 `pyproject.toml`：包元数据 + 依赖区（torch/numpy pin）+ ruff/mypy/pytest 配置
- [ ] 收紧 `.gitignore`（`.python-version`、`dist-agent-meta.jsonl`、`*.out.log`、`*.err.log`）
- [ ] 清理仓库内 `__pycache__` 目录
- [ ] 添加 `Makefile`：`check` / `test-fast` / `test-itest` / `smoke` / `clean` / `format` / `lint`
- [ ] 仓库内 `nn-training/README.md`（入口图 + 命令拓扑 + 改动红线）

**验收**：`make check` 绿灯；仓库内无 `__pycache__`；`make clean` 只清轮转外临时文件。

### P1 —— 类型注解 + 静态检查

- [ ] 全量 type hints：从 `rl/` 包开始 → `schema.py` / `dist_common.py` → `run_rl.py`
- [ ] `rl/args.py`：显式 `@dataclass` 替代测试中 `SimpleNamespace`
- [ ] `mypy --config-file nn-training/pyproject.toml` 接入 `make typecheck`
- [ ] `ruff` 接入 `make lint`，`ruff format` 接入 `make format`

**验收**：`make lint` + `make typecheck` 绿灯。

### P2 —— 测试架构重组

- [ ] 拆分 `test_run_rl.py` 为：
  - `tests/test_rl_course.py`
  - `tests/test_rl_resume.py`
  - `tests/test_rl_stream.py`
  - `tests/test_rl_breaker.py`
  - `tests/test_rl_queue_itest.py`（集成层，需 `RUN_RL_ITEST=1`）
- [ ] `tests/conftest.py`：共享 fixture（临时 traj_dir / 假权重 / 假 shard）
- [ ] 测试 runners：`make test-fast`（无 torch、无 bun）/ `make test`（全部）

**验收**：快速层 < 5s；文件命名镜像模块。

### P3 —— 入口瘦身 + 配置治理

- [ ] `run_rl.py` 拆分为：`rl/modes.py`（三模式注册表）、`rl/archive.py`（权重归档轮转）、`rl/checkpoint.py`（warm-start 幂等）
- [ ] `rl/config.py`：dataclass schema 校验，缺字段给默认值 + 警告，未知字段 yellow flag
- [ ] 结构化日志统一替换散落 `_emit` / 裸 `print`，按 run 切分文件

**验收**：`run_rl.py` < 600 行；配置缺失项启动首屏即报。

### P4 —— CI + 产物生命周期

- [ ] GitHub Actions：ubuntu/windows 双 runner；`ruff check` + `mypy` + `test-fast` + `smoke`
- [ ] `make weights-prune` / `make weights-update-md` 自动化 WEIGHTS.md
- [ ] `tmp/‘ 治理：训练器统一写 `tmp/run-<RUN_ID>/`，`make clean` 清轮转之外全部
- [ ] `tools/plot_training.py`：胜率曲线 / KL-熵曲线 forensics

**验收**：CI 绿灯方才可合并；WEIGHTS.md 可自动生成。

---

## 3. 不动的东西

以下**不**在本次重构范围内，避免触碰风险：

- torch / numpy 版本 pin（已在 `requirements.txt` 工程化锁定）
- PPO / BC / 课程采样 / 熔断等算法逻辑
- `schema.py` 与 TS 端的字节级协议（`OBS_SCHEMA_MAJOR`）
- `start-training.sh` / `.ps1` 的 venv bootstrap 行为（运行期不变）

---

## 4. 执行顺序建议

```
P0 ──→ P1 ──→ P3 ──→ P4
        └──→ P2 ──┘  (P2 可与 P1/P3 并行)
```

每完成一个阶段即打一个 commit，独立可回滚。
