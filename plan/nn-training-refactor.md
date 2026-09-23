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
| 双平台统一启动器（venv bootstrap + 线程环境 + 锁） | `tools/training/train.ts`（原 `start-training.sh` / `.ps1` 与 `start.ts train`，已删） |
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
- 统一启动器（`tools/training/train.ts`，原 `start-training.sh` / `.ps1`）的 venv bootstrap 行为（运行期不变）

---

## 4. 执行顺序建议

```
P0 ──→ P1 ──→ P3 ──→ P4
        └──→ P2 ──┘  (P2 可与 P1/P3 并行)
```

每完成一个阶段即打一个 commit，独立可回滚。

---

## 5. 第三轮（2026-09-23）—— 现状已大变，本文件 §1.1 规模数早已过期

> ⚠ §1.1 写的「~50 个 Python 文件 / ~7000 行」是 2026-09-02 的读数。**现在**：
> 生产代码 ~118K 行 / 200+ 模块；单文件最大 `remote/hub_server.py` **3972 行**、
> `remote/worker.py` 3475、`rl/loop_steps.py` 2328。门禁 = `ruff + mypy + pytest`
> （tests/ + e2e/ 同一次 xdist），基线 **2230 passed / 3 skipped / ~27s**。
> 用户指令（2026-09-23）：降低模块耦合 + 尽量复用代码 + 提可维护性与可扩展性。

### 5.1 已完成 —— S1：`common/` 共享原语层（去重，零行为变化）

新增 stdlib-only 包 **`nn-training/common/`**（`hashing` / `proc` / `fs` / `text` / `logutil`），
把 12 组「同名各写 2~4 份」的原语收敛成唯一实现；上层只留 re-export / 薄包装
（历史名字与 monkeypatch 接缝**一个没动**）。同时把 **13 处裸 `text=True` 捕获**统一到
`common.proc.run_capture`（显式 UTF-8）——那正是 `docs/nn/engineering.md` §19/§30 记的
「响亮错误变哑巴」坑。

- 决策：`DECISIONS.md` §2026-09-23-goalnn-common-primitives-layer
- 全文与教训：`docs/nn/engineering.md` §21
- 回归防线：`tests/test_common_layer.py`（17 例，含 AST 源码守卫与层契约守卫）
- **门禁：2247 passed / 3 skipped / 0 failed**（ruff + mypy 绿）。
- 层契约（**动 `common/` 前必读**）：只依赖 stdlib、不反向 import 上层、无副作用、
  无模块级可变状态；**三个从 GitHub raw 单独拉取的引导模块**
  （`remote/tailscale_boot.py` / `notebook_boot.py` / `offline_boot.py`）**禁用 `common/`**
  ——它们要在拿到 `code.zip` 之前被 import，其重复是结构性豁免。

### 5.2 已完成 —— S3：断开 `rl` ↔ `remote` 包循环（2026-09-23）

> **编号口径**：本节曾写作「S2」，与 `docs/nn/engineering.md` §22 / `DECISIONS.md`
> §2026-09-23-goalnn-layering-common-sink 的「S3」不一致 ⇒ 现统一为文档口径：
> S1 = `common/` 原语层（§5.1，已完成）· S2 = `text=True` 编码隐患（附于 S1，已完成）·
> **S3 = 包循环断开（本节，已完成）** · S4 = 拆神模块（§5.3，待办）。

**结论（已落地）**：`protocol`（92 处 / 65 文件）与 `game_watch`（14 处 / 12 文件）
已下沉为 `common/` 成员；`ppo/` `train/` `models/` `data/` `scripts/` 对 `remote` 引用**归零**；
新增 `tests/test_layering.py`（5 例）把单一直向钉住（含「白名单未用即红」）。
`rl/` 遗留 **4 项过渡白名单**（§5.2 步骤 ④ 的待办）。

- 决策：`DECISIONS.md` §2026-09-23-goalnn-layering-common-sink
- 全文与教训：`docs/nn/engineering.md` §22
- **门禁：2252 passed / 3 skipped / 0 failed**

<details><summary>原诊断与设计（保留供追溯）</summary>

**现状（实测，不是印象）**：依赖是**双向**的——

```
rl  → remote ：10 个文件（protocol / game_watch / serve_pool / hub_client / push_client）
remote → rl  ： 8 个文件（resume.walk_shard_dirs / eval_local.* / plan.* / config.load_course /
                            reward_library.* / reward_context.update / course.parse_range / log.log）
```

一个双向的包依赖 = 谁都可能拿到半初始化的对方；`rl/queue.py` 已经在用**函数内延迟 import**
（`from rl.dispatch import RolloutDispatcher`）绕，而 `rl/dispatch.py` 又想 import `rl/queue`
——这类「靠延迟 import 换来的平静」是下一个事故的温床。

**目标分层（单一直向）**：

```
L0  common/ · platform_utils · pid_probe · dist_common · schema     （stdlib-only，无 torch）
L1  rl/ · ppo/ · models/ · data/ · train/                            （纯逻辑，可脱离 torch 单测）
L2  remote/                                                         （传输；可依赖 L0/L1，反之禁止）
```

**落地顺序（每步独立可回滚、门禁绿才走下一步）**：

| 步 | 动作 | 依据 |
|---|---|---|
| ① | 先写**分层守卫测试**（AST 扫模块级 import，断言不存在 `rl → remote` 边；现状红/白名单先行） | 没有守卫的分层重构 = 下一次提交就退回双向 |
| ② | `remote/protocol.py` → `common/protocol.py`（**stdlib-only 纯编解码器**，无 torch、无网络）。全仓调用点改 import，删旧文件（**不留 shim**——shim 是「聪明」，本仓偏好直白） | 这是 `rl → remote` 的**主边**（5 文件） |
| ③ | `remote/game_watch.py` 同样下沉（先核 stdlib-only） | `rl/queue_local` / `rl/eval_local` 的第二条边 |
| ④ | `rl → remote.{hub_client,push_client,serve_pool}` 三条边：把「取活/回传/评估脚本名」抽成**注入式接口**（rl 侧收一个 callable / 协议对象），而不是让 rl 直接 import 传输实现 | 这三条是「策略层直接调传输层」，耦合最贵 |
| ⑤ | `remote → rl` 的 8 条边**保留并写进守卫白名单**（方向合法） | 单向即可，不必追求「谁也不依赖谁」 |

**验收**：守卫测试绿 + `bun run check` 绿 + 门禁全绿；`rl/` 可在**不 import `remote` 任何模块**
的前提下通过全部单测。

**落地实测**：①②③已完成，⑤已由 `tests/test_layering.py` 承担（`rl/` 白名单 + 白名单不腐烂）。
**④ 仍待办**——即 `tests/test_layering.py::RL_TO_REMOTE_WHITELIST` 里的 4 项
（`remote.bundle` / `hub_client` / `push_client` / `serve_pool`）；做完 ④ 则把它们从白名单删掉
（**不删即红**，这是守卫设计的自清机制）。

</details>

### 5.3 待办 —— S4：拆神模块（收益最大、风险也最大，**独立一轮**）

| 文件 | 行数 | 建议切法（按关注点，不是按行数等分） |
|---|---|---|
| `remote/hub_server.py` | 3972 | `remote/hub/`：`pack_exchange`（任务包导出/导入）· `offline`（离线产物面）· `results`（结果/指标摄取）· `registry`（节点登记与状态）· `admin`（net-probe / 健康）· `httpd`（Handler + 启动）；**保留 `remote/hub_server.py` 作门面 re-export**（测试与 dashboard 都从它取名字） |
| `remote/worker.py` | 3475 | `remote/worker/`：`restore`（payload 还原）· `caches`（code.zip / ts_code 内容寻址缓存）· `procs`（子进程与监督）· `runjob`（run_job 主链） |
| `rl/loop_steps.py` | 2328 | 按循环阶段切（评估步 / volume 步 / 提交步），或收进 `rl/loop_steps/` 包 |

**方法（防翻车，逐条都是本仓踩过的）**：

1. **先抽无 `self` 的纯函数簇**（低耦合、零语义风险），再考虑带状态的类；
2. 每次只搬一个簇 ⇒ **跑门禁**（~30s，比事后定位便宜得多）；
3. 搬家时保留原模块的 **re-export**，测试与外部调用点**一行不改**——名字是契约，位置不是；
4. 模块级可变状态（如 `rl/log.py` 的前缀路由）**随宿主走**，不拆散；
5. 同步更新 `README.md` 模块地图 + `docs/nn/*.md` 的引用路径（本仓有试读 README 的用例，
   `tests/test_notebook_runtime.py` 之类会盯着路径）。

### 5.4 本轮**不做**（已核，刻意保留）

- `remote/notebook_boot.py` ↔ `remote/offline_boot.py` 的孪生助手（`_build_opener` /
  `_load_tailscale_boot` / `_course_dirs`）：**结构性豁免**，共享即断链（§5.1 层契约）。
- `remote/tailscale_boot.py::_progress_logger` ↔ `remote/worker.py::_progress_logger`：
  同理只允许**两份**（`tests/test_common_layer.py` 钉住了这个数）。
- `hub_server._json` / `worker_server._json`：是 **HTTP handler 的方法**，合需要 mixin；
  收益（7 行）远小于「给两个 handler 引入共同基类」的耦合成本。
- `scripts/eval_intent_m5.py` ↔ `train/intent_probe.py` 的 `seq_features` / `build_injection`、
  两个 `build_model`：属**模型构造/特征口径**，正确落点是 `models/intent_net.py` 的类方法，
  属「改模型面」——单开一轮，别混进纯工程重构。
