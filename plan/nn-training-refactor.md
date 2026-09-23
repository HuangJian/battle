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
> **S3 = 包循环断开（本节，已完成）** · S4 = 拆神模块（§5.3，**首簇已完成**：
> `loop_steps` 的传输/发布簇 → `rl/loop_transport.py`）。

**结论（已落地）**：`protocol`（92 处 / 65 文件）与 `game_watch`（14 处 / 12 文件）
已下沉为 `common/` 成员；`ppo/` `train/` `models/` `data/` `scripts/` 对 `remote` 引用**归零**；
新增 `tests/test_layering.py`（8 例）把单一直向钉住：编排层声明快照双向对账（多/少都红）、
纯逻辑切线、环已断、上层包不得引编排。

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
| ④ | ~~`rl → remote.{hub_client,push_client,serve_pool}` 三条边：把「取活/回传/评估脚本名」抽成**注入式接口**~~ —— **当天否决**（见下方「落地实测」） | 原判据：这三条是「策略层直接调传输层」；实际发现它们属于应用层，且正被 S4 拆 |
| ⑤ | `remote → rl` 的 8 条边**保留并写进守卫白名单**（方向合法） | 单向即可，不必追求「谁也不依赖谁」 |

**验收**：守卫测试绿 + `bun run check` 绿 + 门禁全绿；`rl/` 可在**不 import `remote` 任何模块**
的前提下通过全部单测。

**落地实测**：①②③已完成；⑤ 改由 `tests/test_layering.py` 以「纯逻辑不得 import 编排」+「remote
不得传递触及编排（环已断）」两条性质断言承担。

**④（注入式接口）当天被否决**（理由与替代方案 → `docs/nn/engineering.md` §22「同日修订」）：
`loop_steps`/`loop_guards` 外部使用者为零且正要被 S4 拆、`bc_loop` 的测试直接 monkeypatch
`remote.hub_client._request`。改做「导出器路径单源化（`EVAL_SCRIPT` 进 `common/protocol.py`）」
⇒ `rl/eval_local` 回纯逻辑，**编排层 17 → 11 模块**；白名单→声明式快照 `RL_ORCHESTRATION`（双向对账）。

**剩余（下一轮）**：把 11 个编排模块**物理搬出** `rl/`（成独立应用层包）—— 守卫已用断言代替
这一步，搬家的收益主要是「import 路径说实话」；另有两个包内环：`rl.loop_*` 编排簇（含 `run_rl`）
与 `remote.run_loop ↔ remote.worker`（后者随 S4 拆）。

</details>

### 5.3 S4：拆神模块（收益最大、风险也最大，**独立一轮**）

> **进度（2026-09-23）**：第一步已完成 —— `rl/loop_steps.py` 的模块级**传输/发布簇**
> （19 函数 + 2 异常 + 4 常量，43–611 行）**零逻辑改动**搬到 `rl/loop_transport.py`，
> `loop_steps` 只留显式清单门面（2328 → 1812 行）。
> 决策 → `DECISIONS.md` §2026-09-23-goalnn-godmodule-loop-transport；全文与教训 →
> `docs/nn/engineering.md` §23；守卫 → `tests/test_loop_transport_split.py`（7 例）。
> **门禁：2262 passed / 3 skipped / 0 failed**。
> **最大教训**：DI seam 是**模块全局**（`dist_common` / `_push_submit` / `_push_wait_result`），
> patch 目标**随实现走**——同名 seam 在两处并存是两个真实注入点，不是重复；且
> `import rl.loop_steps as ls; ls._push_submit = …` 这种**别名形态**是文本 grep 抓不到的注入点
> （被全量门禁点名）。
>
> **第二步已完成（同日）**：远端 PPO 腿 **13 方法 / 862 行（整类 50%）** → `rl/loop_remote.py::
> TrainingRemote`。**方向修正**（与下方 §5.3.1 的建议不同）：不是「给组合类加基类」，而是
> **`class TrainingSteps(TrainingRemote)`**（调用者依赖被调用者）⇒ 组合类不变 · 零 MRO 变化 ·
> 4 个「继承真混入」的测试宿主一行不改。`loop_steps.py` 1812 → **952** 行（含首簇共 2328 → 952）。
> mypy 的两个坑：5 个跨混入助手 + 7 个从未声明、只在方法体自赋值的属性都要在新文件里补声明。
> seam 由两份**收敛为一份**（`loop_steps` 连 import 都没了 ⇒ e2e 两处 patch 迁 `rl.loop_remote.*`）。
> 门禁 **2266 passed / 3 skipped**；mypy 364 源文件绿。
> **下一步**：`remote/worker.py`（一簇一簇搬）→ `remote/hub_server.py`（状态类，最后）。
> `TrainingSteps` 本体还剩 952 行 / 20 方法（切法是「按一条真实调用链切」，不是按行数等分）。

| 文件 | 行数 | 建议切法（按关注点，不是按行数等分） |
|---|---|---|
| `remote/hub_server.py` | 3972 | `remote/hub/`：`pack_exchange`（任务包导出/导入）· `offline`（离线产物面）· `results`（结果/指标摄取）· `registry`（节点登记与状态）· `admin`（net-probe / 健康）· `httpd`（Handler + 启动）；**保留 `remote/hub_server.py` 作门面 re-export**（测试与 dashboard 都从它取名字） |
| `remote/worker.py` | 3475 | `remote/worker/`：`restore`（payload 还原）· `caches`（code.zip / ts_code 内容寻址缓存）· `procs`（子进程与监督）· `runjob`（run_job 主链） |
| `rl/loop_steps.py` | 2328 → **1812** | ~~按循环阶段切~~：顶层函数簇已搬（见上方进度）；**余下** = 拆 `TrainingSteps`（1715 行 / 33 方法）的方法组（评估步 / volume 步 / 提交步），或收成 `rl/loop_steps/` 包 |

**实测侦察（2026-09-23，动手前先量；原计划只有「按关注点切」的猜测）**：

| 文件 | 形态 | 可见缝 | 模块级可变状态 | 风险 |
|---|---|---|---|---|
| `rl/loop_steps.py` | 1 个类 `TrainingSteps`（1715 行 / 33 方法）+ 22 个顶层定义（~500 行） | 顶层函数已按关注点分簇：传输解析（`resolve_hub_push`/`resolve_transport`/`require_remote_transport`）· push（`_push_over_nodes`/`_push_job_round`）· kickstart（`kickstart_*`/`_kickstart_ref_payload`）· 阈值（`_course_cf_tunnel`/`_rollout_source`/`_run_segment_iters`/`_run_wait_sec`/`_gpu_push_nodes`）· 门/分片（`_gate_round_shards`）· 异常（`remote_retryable_exceptions`/`fatal_remote_http`） | **无** | **低**——最适合先拆（无状态、同包搬家、门面 re-export） |
| `remote/worker.py` | 68 个顶层函数（无大类） | 水平缝清楚：wire 客户端（`_request`/`peek_jobs`/`claim_job`/`job_started`/`job_ready`/`post_result`/`heartbeat`/`release_job`/`download_*`，~L445–1462）· wire慢速遥测（`_wire_*`/`_reroll_decision`/`WireSlowError`，L182–369）· BC 助手（`_bc_*`/`normalize_ppo_device`/`resolve_bc_seed`，L1583–1753）· 缓存（`_cache_blob`/`_resolve_blob`/`_ensure_ts_code`/`prune_job_dirs`）· 作业执行（`run_job` 743 行 + `_run_bc_job` 190）· 监督循环（`supervise_worker`/`worker_loop`/`main`） | `_opener`（懒建 opener）、`_BEST_RATE`/`_ACTIVE_CODE_SHA`/`_bulk_log`/wire 桶（**随宿主走**，不得拆散） | 中——**遥测/节流状态模块级共享**，拆前先补「同 seed 两遍逐字段相同」类用例 |
| `remote/hub_server.py` | 3 个千行状态类：`_JobStore`(1002) · `_HubQueue`(1033) · `HubHandler`(1343)；顶层只有 5 个小纯函数 | 顶层小纯函数（`attributed_source`/`_is_loopback`/`_is_ip_literal`/`_write_bytes`/`_deterministic_fill`，~58 行）可先撇到 `remote/hub/http.py` | 基本无 | **高**——主体是状态类，拆 = 拆状态；建议**最后做** |

**建议顺序**：`rl/loop_steps.py`（无状态，先验证「搬家 + 门面」的套路）→ `remote/worker.py`（一簇一簇搬，每簇跑门禁）→
`remote/hub_server.py`（状态类，最后；先把 `HubHandler` 的 `do_*` 按路由分组到 mixin 是可行起手）。

**方法（防翻车，逐条都是本仓踩过的）**：

1. **先抽无 `self` 的纯函数簇**（低耦合、零语义风险），再考虑带状态的类；
2. 每次只搬一个簇 ⇒ **跑门禁**（~30s，比事后定位便宜得多）；
3. 搬家时保留原模块的 **re-export**，测试与外部调用点**一行不改**——名字是契约，位置不是；
4. 模块级可变状态（如 `rl/log.py` 的前缀路由）**随宿主走**，不拆散；
5. 同步更新 `README.md` 模块地图 + `docs/nn/*.md` 的引用路径（本仓有试读 README 的用例，
   `tests/test_notebook_runtime.py` 之类会盯着路径）。

#### 5.3.1 第二步侦察（2026-09-23，AST 实测）—— 拆 `TrainingSteps`（1715 行 / 33 方法）

量出来三件事决定切法：

| 事实 | 值 | 含义 |
|---|---|---|
| 类规模 | 33 方法 / 1715 行 | 比首簇（569 行）大 3 倍 |
| **声明的实例属性** | **67 个** | 真正的耦合不是模块全局，而是**共用的 `self.*`**；混入切法不消除它（这是**有意保留**的：它们本就是同一个 `TrainingLoop` 的状态） |
| 模块全局共读 | `log` **22 个方法** · `time` 9 · `RemotePpoJob` 7 · `Path` 6 · `write_gate_verdict` 4 · `fatal_remote_http`/`JobFailedError`/`remote_retryable_exceptions`/`dist_common` 各 3 | 搬走的方法要在新模块重导这些名；`log` 22 处意味着**不能**按「谁用 log 谁搬」切 |

**测试真注入点 ∩ 方法读取 = 4 个**：`_push_submit` · `_push_wait_result` · `kickstart_coef` · `resolve_transport`。
（`e2e/test_push_mode_integration.py` 的 `test_push_publish_phase_…` patch 前两个以驱动 `st._push_submit_first` /
`st._push_fetch` ⇒ **这两个方法一旦搬走，那两处 patch 目标必须同步迁**；同 S4 首簇的教训 ——
patch 目标随实现走，别名形态（`import rl.loop_steps as ls; ls.X = …`）也是注入点。）

**首刀（已完成，见上方进度）：远端 PPO 腿（13 方法 / 862 行 = 整类 50%）→ `rl/loop_remote.py` 混入**

> ⚠ **落地时改了方向**：下面「第二步的已知代价」一条里写的「组合类 `TrainingLoop` 要加一个基类」
> **没有采用**——实际是 `class TrainingSteps(TrainingRemote)`（调用者依赖被调用者）。理由与收益见
> `docs/nn/engineering.md` §23「第二步」一节。下文保留原始设计供追溯。

```
_remote_ppo_publish(302) · _remote_ppo_land(116) · _remote_run_segment(127) · _remote_iter(74)
_remote_ppo_step(46) · _push_submit_node(34) · _push_fetch(25) · _abort_node_failure(21)
_remote_ppo_fetch(13) · _remote_ppo_probe(12) · _push_submit_first(10) · _remote_ppo · _handle_remote_failure
```

切法可执行的三个根据（都是量出来的，不是估计）：

1. **入口单一**：外界→簇只有 `self._remote_ppo` 一条（`run_training` 调用）；簇→外界只有 5 个小助手
   （`_commit_journal` / `_ensure_ts_code` / `_forensics` / `_per_stage_quota` / `_volume_plan_block`）。
   混入切法下两者的 `self.*` 互调**天然可用**（同在 `TrainingLoop` 组合类上）⇒ 不需改一行调用。
2. **内聚理由真实**：这 13 个方法共享「发布 → 领取 → 三重校验落位 → failover → 事件落账」一条链，
   与评估/报告/日志那几个方法（`_drain_pending_eval` / `_log_report` / `_write_iter_stats` …）职责分明。
3. **satisfies S4 首簇**：新模块可直接 `from rl.loop_transport import ...` 拿传输原语（门面只是兼容层，
   不是唯一入口）——这是首簇搬迁的预期收益开始兑现。

**第二步的已知代价（必须一并做）**：① 组合类 `TrainingLoop(TrainingSteps, TrainingGuards)` 要加一个基类；
② 新模块需重导 36 个模块全局（含 `log`/`time`/`Path` 这些高频项）；③ 迁 2 处 e2e patch 目标；
④ 守卫扩展：在 `tests/test_loop_transport_split.py` 同口径加「定义只在 `loop_remote`」+「`_remote_ppo` 仍在组合类上」。

#### 5.3.2 第三步侦察（2026-09-23，AST 实测）—— 拆 `remote/hub_server.py`（3974 行）

实测形状（与其按「先撇 5 个顶层纯函数」的初判不同——那 5 个加起来只 **58 行 / 1.5%**，不值一轮）：

| 顶层节点 | 行数 | 结论 |
|---|---|---|
| `_JobStore`（1002）· `_HubQueue`（1033）· `HubHandler`（1343） | 3378 / 3974 = **85%** | 三个大状态类才是本体 |
| 5 个顶层纯函数（`_is_ip_literal` / `attributed_source` / `_is_loopback` / `_write_bytes` / `_deterministic_fill`） | 58 | 收益太小，**不单开一轮**（可随下面的刀顺手带走） |
| `_AuthGuard`（57）· `as_hub` / `make_server` / `main`（240） | ~300 | 与 `HubHandler` 同生命周期，暂不动 |

**首刀（已完成，同日）**：`HubHandler` 的 admin 控制面（9 方法 / 218 行）→ `remote/hub/admin.py::AdminRoutes`

> 落地结果：`hub_server.py` 3974 → **3728** 行；守卫 `tests/test_hub_admin_split.py`（8 例）；门禁 **2274 passed**。
> 决策 → `DECISIONS.md` §2026-09-23-goalnn-godmodule-hub-admin；全文 → `engineering.md` §23「第三步」。
> 落地时额外摸到**两个坑**（均已避开）：
> ① **名字成环**：admin 组读的 `NET_PROBE_MAX` / `_deterministic_fill` 必须**随迁**（留下会被
>    「hub_server import admin 拿混入」反过来要 import ⇒ 双向环；已 grep 证实全仓无其它读者 ⇒ 零门面）；
> ② **类型遮蔽（比成环更险）**：混入里把 `headers` / `rfile` 宽成 `Any` 会**盖掉类型库的精确类型**
>    （`AdminRoutes` 在 MRO 里早于 `BaseHTTPRequestHandler`）⇒ 组合类里 `self.headers.get(...)` /
>    `self.rfile.read(n)` 的推断拓成 `Any`，hub_server 里两个 `-> str` / `-> bytes | None` 的方法报
>    `no-any-return`（**症状在组合类，病因在混入**）⇒ 混入里必须逐字照抄类型库。

```
_admin_push_workers(57) · _admin_courses(36) · _admin_unfreeze(32) · _admin_net_probe_upload(27)
_admin_net_probe(20) · _admin_halt(18) · _admin_queue(10) · _admin_status(9) · _admin_offline(9)
```

为什么它是 `HubHandler`（49 方法 / 1343 行）里最安全的一组——三条都是量出来的：

1. **类只有 3 个属性**（`hub: _HubQueue` / `push: PushDispatcher | None` / `_blocked_logged`），admin 组
   只经 `self.hub` / `self.push` 拿状态 ⇒ 方法近乎无状态，搬迁不改语义；
2. **admin 组只往外调 4 个通用助手**（`_auth_ok` / `_bytes` / `_json` / `_query_course`）；
   反向只有「`do_GET` / `do_POST` 派发到它们」——派发靠 `table[path](self, ...)` 或 `self._admin_*`，
   混入下天然可用；
3. ✭ **测试接缝为零**：全仓对 `remote.hub_server` 的 patch 只有一处（`SEND_TIMEOUT_SEC`，不在本组），
   而 `tests/` 从 `hub_server` 取的名字全是 `_JobStore` / `_HubQueue` / `as_hub` / `make_server`——
   本组一个都没被外部 import。对比 S4 前两步的 seam 弯弯绕，这组几乎免费。

**⚠ 两个必须随迁的名字（否则成环）**：admin 组读两个**定义在 `hub_server` 顶层**的名字——

* `_deterministic_fill`（顶层函数，仅 4 行）—— 若留在 `hub_server` 而 `admin.py` 去 import 它，
  就与「`hub_server` import `admin` 拿混入」形成**双向环**；
* `NET_PROBE_MAX`（顶层常量，= 16MB）—— 同理。

⇒ 两者**随 admin 组一起搬**到新模块。**已查明：两者全仓无其它读者**（grep `remote/ rl/ tests/ e2e/` 除
`hub_server.py` 外零命中）⇒ **不需要门面 re-export**（比预想更干净）。
本组另读的 `COURSE_MODES` / `ProtocolError` / `json` / `time` / `urllib` 均为正常 import。

> 顺带查明（供后续刀参考）：其余 3 个顶层纯函数 `_write_bytes` / `_is_ip_literal` 也**无外部读者**；
> 但 `_is_loopback` / `attributed_source` **被 `tests/test_hub_auth_d9_order.py` 直接 import** ⇒
> 它们一旦搬迁必须留门面 re-export（好消息：两者都不在 admin 组里）。

**后续顺序**（仍在 `remote/hub_server.py`）：`HubHandler` 的其余路由组（`_get_*` 12 个 / `_post_*` 11 个）
→ 通用助手（`_auth_ok` / `_bytes` / `_json` / `_read_*_body` / `log_message`）→ 最后才动两个千行状态类
（`_JobStore` / `_HubQueue`：拆 = 拆状态）。

#### 5.3.3 第四步前置（2026-09-23，已完成）：`remote/worker.py` 的状态契约

`worker.py` 要拆的不是类，而是**一整片顶层函数**（68 个）——而顶层函数与「模块级可变状态」
同生共死：搬到 `remote/worker/wire.py` 后，它们读的就是**新模块的**全局 ⇒ `_WIRE` / `_BULK`
变成两份互不相干的账，而测试里那些 `W._WIRE.clear()` / `W._BULK.reset()` **照旧绿**。
这正是 S4 前两步反复撞上的那类静默故障，只不过这次会一次撞上 6 个。

**实测清点（AST）——真·模块级可变状态共 6 处**：

| 名字 | 形态 | 谁改 | 测试可见 |
|---|---|---|---|
| `_opener` | 懒建单例（`global`） | `_get_opener` | 否 |
| `_BEST_RATE` | float（`global`） | `_note_rate` | **是**（`test_wire_reroll` / `test_boot_wire_guard` 重置它） |
| `_WIRE` | `dict[str, dict]`（每 job 传输账） | `_wire_bucket` / `_wire_flush` | **是**（多文件 `.clear()` + 断言封顶） |
| `_BULK` | `BulkScheduler()` **实例** | `set_bulk_log` / `pace` / `slot` / `control` / `reset` | **是**（直接 `.reset()` / `.inflight()`） |
| `_POLL_WARN_AT` | `dict[str, float]`（告警节流 60s） | `_warn_non_200` | 否（**此前零覆盖**，本轮补上） |
| `_ACTIVE_CODE_SHA` | `str \| None`（会话代码指纹） | `run_job`（`global`；**定义在 2757 行、用在 2141 行**） | 间接（`test_remote_hotswap` 测异常语义） |

（`remote/tailscale_boot.py` 的同名 `_BEST_RATE` 是**结构性豁免**：独立引导模块，各有各的
会话，不是同一个变量——别顺手合并。）

**已铺好的安全网**：`tests/test_worker_state_contract.py`（**13 例**）三类断言：

1. **清点不许漂移**：顶部可变容器 = `_WIRE` / `_POLL_WARN_AT` · `global` 重绑 = `_opener` /
   `_BEST_RATE` / `_ACTIVE_CODE_SHA` · `_BULK` 是顶层 `BulkScheduler()` 调用 · 且每个名字都
   真的被读到（防清单变僵尸）；
2. **别处不许有自己的副本**：扫 `remote/**/*.py`，状态名不得在第二个模块里再绑一次
   （`tailscale_boot._BEST_RATE` 走显式豁免表）；
3. **行为可复现**：`_wire_flush` 的账行**同序列跑两遍逐字节相同** · `_wire_bucket` 写进模块那份
   `_WIRE` 且封顶 `WIRE_MAX_JOBS` · `sched0` 取自模块那份 `_BULK`（跨模块各拿一个调度器的探测器）·
   `_note_rate` 的会话语义 · `_warn_non_200` 的 60s 节流与 `log=None` 不占窗（此前零覆盖）。

**反向探针已验证判据是活的**：在 `remote/` 放一个 `_WIRE = {}` 的探针文件 ⇒ 守卫立刻点名
`remote/_probe_state.py::_WIRE`；删除即回绿。门禁 **2287 passed / 3 skipped**。

**拆 worker 的建议刀口（下一步）**：wire 簇（`_wire_*` / `_bulk_pace` / `_reroll_decision` / 速率
阈值常量，~L132‑340）与 BC 助手簇（`_bc_*` / `normalize_ppo_device` / `resolve_bc_seed`，~L1583‑1753）
——**但首先**要决定 6 处状态是「留在宿主并让子模块 import」还是「随簇搬迁」（后者必须同步改
`tests/` 的注入点；前两刀的 seam 迁移分录可直接照抄）。`supervise_worker` / `worker_loop` / `main`
是宿主，**不动**。

#### 5.3.4 第四步落地（2026-09-23，已完成）—— wire / 低速重抽 / bulk 节流簇 → `remote/wire.py`

**刀口**：`WIRE_*` 阈值 7 个 + 状态 3 份（`_WIRE` / `_BEST_RATE` / `_BULK`）+ 15 个自由函数
（`set_bulk_log` · `_bulk_pace` · `_note_rate` · `_min_rate` · `_reroll_decision` · `WireSlowError` ·
`_wire_bucket` · `_wire_start` · `_wire_add` · `_wire_time` · `_wire_hit` · `_wire_note_reroll` ·
`_wire_flush`）。`worker.py` **3450 → 3245** 行；`remote/wire.py` 289 行。

**§5.3.3 留的那个问题已定：状态随簇搬迁。** 留宿主只能靠延迟 import（`wire` 读 `worker` 的全局
= 反向边，`worker` 又 import `wire` 拿函数 = 环），而且 `_note_rate` 用 `global` **重绑**
`_BEST_RATE`——跨模块只改自己那份。⇒ `wire.py` 是这三份状态的**唯一所有者**，`worker.py` 只做
`from remote.wire import … as …` 的**显式转发**（自别名是 ruff 认可的 re-export 写法）。

**注入点分裂只有一处**：`_WIRE` / `_BULK` 原地可变（`.clear()` / `.reset()`）⇒ 任意入口都是同一对象
（`test_wire_report` / `test_bulk_sched` / `test_async_result_upload` / `test_control_plane_bypass`
**一行不改**）；`_BEST_RATE` 重绑 ⇒ 注入点**必须** `remote.wire._BEST_RATE`（只改了
`tests/test_wire_reroll.py` 的 autouse fixture）。

**守卫**：`tests/test_worker_state_contract.py` 改 **owner-aware**（13 → 15 例：清单按宿主分两组 ·
扫副本跳过两个宿主 · 新增「转发必须 `is` 同一对象」）· 新 `tests/test_wire_split.py`（**6 例**：
定义唯一 · 不得反向 import · 同一对象 · 一份账 · 顶层可变容器只许 `_WIRE` · 注入点口径）。
反向探针两处都命中（`remote/_probe_wire.py::_WIRE` / `worker.py` 里重复实现 `_wire_flush`）。
门禁 **2295 passed / 3 skipped**。

> 决策 → `DECISIONS.md` §2026-09-23-goalnn-godmodule-wire；全文 → `engineering.md` §23「第四步」。

#### 5.3.5 第五步（2026-09-23，**已完成**）—— HTTP 传输核心 → `remote/http.py`，BC 留待第六步

侦察（AST 实测）—— BC 簇与 HTTP 核心的依赖形状：

先把「谁依赖谁」量清——这决定第五步能不能直接拆 BC：

| 函数 | 行数 | 调用的顶层函数 | 读的模块级名 |
|---|---|---|---|
| `_bc_local_resume_dir` / `_bc_store_local_resume` / `_bc_load_local_resume` | 6 / 18 / 10 | 仅簇内 | 无 |
| `_bc_device` / `normalize_ppo_device` / `resolve_bc_seed` | 9 / 25 / 18 | 无 | 无 |
| `_bc_fetch_resume` / `_bc_post_epoch` | 30 / 41 | **`_request`**（宿主） | 无 |
| `_run_bc_job` | 190 | `_request` 系 + `_persist_result` + 上面的助手 | `d14_corpus_match` |

**关键发现**：BC 簇**零模块级状态**（不像 wire 簇），但它整簇站在 **HTTP 传输原语**（`_request` 系）
上；宿主里其余 18 个函数也全站在同一原语上。⇒ **直接拆 BC 会造成环**（`bc_job` 要 `_request`、
而 `worker` 又要 import `bc_job`），用延迟 import 换来的只是「把环藏起来」（本仓
`tests/test_layering.py` 头部就记着这种「延迟 import 掩盖循环」的旧账）。

**所以正确的下一刀是先把传输核心下沉**（它才是 keystone），再拆 BC：

* **刀口 = `remote/http.py`**：`_opener` / `_get_opener` / `_POLL_WARN_AT` / `_warn_non_200` /
  `_read_body` / `_request` / `_sched_headers` / `_get_with_retry`（≈ **253 行**，含 2 处状态）。
  实测**自含**：组内只调自己 + `remote.wire`（`_BULK` / `_bulk_pace` / `_min_rate` /
  `_note_rate` / `_reroll_decision` / `_wire_add` / `_wire_note_reroll` / `WireSlowError`），
  零宿主依赖 ⇒ 依赖方向 `http ← worker`（与 `wire` 同形，无环）。搬后 `worker.py`
  ≈ 3290 → 3040 行，而且 BC / 下载 / 作业生命周期三组从此可选送。
* **seam 面比前几刀大，但同样是「按调用点定档」**：patch `worker._request` 的测试分两类——
  ① 之后调**宿主**函数（`claim_job` / `heartbeat` / `post_result` / `download_*` / 离线配额 /
  优先级调度）⇒ 解析在 `worker` 命名空间，**一行不改**；② 之后调**已搬走**的
  `_get_with_retry` / `_read_body`（`tests/test_wire_reroll.py` 的 3 处 `_request` +
  `_reroll_decision` 1 处，可能还有 `test_body_transfer_guard`）⇒ 必须改指 `remote.http`。
  范本：第四步只动了一个文件的 autouse fixture（最后合计 2 行）。
* **守卫**：`test_worker_state_contract` 再改一次宿主（`_opener` / `_POLL_WARN_AT` → `http`，
  与 `wire` 同构：新增第三组清单）；新增 `tests/test_http_split.py`（照 `test_wire_split.py`
  六条：定义唯一 / 不得反向 import / 同一对象 / 一份账 / 顶层可变容器清单 / 注入点口径）。
* **再下一刀（第六步）才是 BC**：`_bc_*` ×7 + `normalize_ppo_device` + `resolve_bc_seed` +
  `_run_bc_job` → `remote/bc_job.py`（依赖 `remote.http`，仍无环）；`_run_bc_job` /
  `_bc_fetch_resume` / `_bc_post_epoch` 被 `e2e/test_bc_epoch_e2e.py` 直接 import ⇒ 留门面；
  `normalize_ppo_device` / `resolve_bc_seed` / `_bc_device` 被 `tests/` 直接 import ⇒ 同上。
  **注意** `tests/test_worker_device.py` 有一条读 `worker.py` **源码文本**的守卫
  （`flat.count("normalize_ppo_device(device)") == 1`，且顺序在 `"kind"]) == "bc"` 之前）——
  它看的是 `run_job` 里的调用点（宿主，不动）⇒ 应继续绿，但必须跑一遍确认。* 纯助手单独拆（只搬 6 个无依赖函数，≈ 86 行）**否决**：收益 2.6%，且把一个关注点劈成
  两个模块——与「按职责切」相悖。

> **落地结果（同日）**：`remote/http.py` 已建成（366 行，281 行搬自 worker）；`worker.py`
> **3290 → 3030** 行；依赖方向 `http ← wire ← worker`（DAG，守卫钉住 `http` 不 import `worker`）。
> seam 实际只迁了 3 个文件的 7 处（`test_wire_reroll` 4 · `test_body_transfer_guard` 3 ·
> `test_remote_ppo` 3）。守卫：状态契约改**三宿主**（`worker` / `wire` / `http`）＋新增
> `tests/test_http_split.py`（**8 例**，含两个方向的注入点口径各一条）。
> 反向探针两处命中。门禁 **2295 → 2302 passed / 3 skipped**；mypy 371 源文件绿。
> 决策 → `DECISIONS.md` §2026-09-23-goalnn-godmodule-http；全文 → `engineering.md` §23「第五步」。
>
> ⚠ **差点漏掉的 seam 类**：`download_payload` 这样的**宿主外形、传输层里子**（在 `worker` 里，
> 但走 `_get_with_retry`）——注入点在 `http`，是跑门禁才点出来的。第六步拆 BC 时同理：
> `_bc_fetch_resume` / `_bc_post_epoch` / `_run_bc_job` 直调 `_request`，搬走后注入点随它们走。

**第六步的最后一个待决点（先想清再动手）**：`_run_bc_job` 除 BC 簇外还调两个**非 BC** 的
宿主名——`_persist_result`（结果落盘，仅 5 行，**宿主 `run_job` 也在用**）与 `d14_corpus_match`
（`common.protocol` 的导入名）。若把 `_run_bc_job` 搬进 `bc_job`：

* `d14_corpus_match` 不是问题（`bc_job` 自己从 `common.protocol` import 即可）；
* `_persist_result` **必须决定归属**：(a) 随 BC 搬（`worker` 改从 `bc_job` 导入它——无环但不合语义：
  结果落盘不是 BC 专属）；(b) 与 `unpack_opt_tar` / `pack_opt_tar` / `prune_job_dirs` / `_git_head`
  / `_ensure_commit` / `_ensure_ts_code` / `_cache_blob` / `_resolve_blob` 一起先下沉一个
  `remote/job_fs.py`（作业工作区/缓存/TAR 组，≈ 250 行）——**我倾向 (b)**：它和 `http` 一样是
  「业务簇的公共底座」，先下沉后 BC 与下载簇都能干净选送。

⇒ **建议第六步再拆两刀**：先 `remote/job_fs.py`（底座），再 `remote/bc_job.py`（业务）。

> **第一刀已落地（同日）**：`remote/job_fs.py` 建成（184 行，128 行搬自 worker）：`REPO_ROOT` ·
> `JOB_DIR_KEEP` · `_persist_result` · `prune_job_dirs` · `unpack_opt_tar` / `pack_opt_tar` ·
> `unpack_payload_or_fail` · `_git_head` / `_ensure_commit`。`worker.py` **3030 → 2915**。
> 依赖 `job_fs ← worker`（守卫钉住不得反向 import）。**五刀里唯一 seam-free 的一刀**：
> 全仓对本组都是直接调用（无 `setattr`）⇒ 显式转发就够，测试一行不改。
> 守卫 `tests/test_job_fs_split.py`（6 例）；反向探针往 worker 追加 `def prune_job_dirs` 即被点名。
> 门禁 **2302 → 2308 passed / 3 skipped**；mypy 373 源文件绿。
> 决策 → `DECISIONS.md` §2026-09-23-goalnn-godmodule-jobfs；全文 → `engineering.md` §23「第六步之一」。
>
> **顺带发现（已登记，未处理）**：`_ensure_commit` **全仓零调用**（只有 `_git_head` 被它自己调）
> ——既存死代码；删代码要单开一次并有决策。

> **第二刀已落地（同日）**：BC 簇（**363 行**，本来就是连续一块）整块搬进 `remote/bc_job.py`
> （422 行）：`_bc_fetch_resume` / `_bc_local_resume_dir` / `_bc_store_local_resume` /
> `_bc_load_local_resume` / `_bc_post_epoch` / `_bc_device` / `normalize_ppo_device` /
> `resolve_bc_seed` / `_run_bc_job`。`worker.py` **2915 → 2579**。依赖
> `bc_job → {http, job_fs}`（向下，无环）。**同样 seam-free**：全仓对 BC 名字只有直接调用与
> `from remote.worker import …` ⇒ 显式转发就够，e2e / tests **一行不改**。
> 新守卫 `tests/test_bc_job_split.py`（6 例），其中
> `test_torch_stays_a_deferred_import_inside_run_bc_job` 是**「顶层零 torch」这条老规矩第一次被
> 机械钉住**（同时断言顶层没 torch 与函数体内有）。反向探针两处均命中。
> 门禁 **2308 → 2314 passed / 3 skipped**；mypy 375 源文件绿。
> 决策 → `DECISIONS.md` §2026-09-23-goalnn-godmodule-bcjob；全文 → `engineering.md` §23「第六步之二」。

> **第三刀已落地（同日）**：下载簇（**252 行**）搬进 `remote/download.py`（313 行）：
> `_progress_logger` · `download_payload` / `download_code` / `download_ts_code` /
> `download_blob` · `_cache_blob` / `_resolve_blob` · `_ensure_ts_code`。
> `worker.py` **2579 → 2348**。依赖 `download → {http, wire, bulk_sched}`（全向下，无环）；
> `BODY_*` 从 `remote.http` 取单一定义（不复制常量）。实测本组**零跨组函数依赖**。
>
> **本刀的核心是「注入点分档」第一次真正双向验证**：本组含**组内互调**
> （`_resolve_blob` → `download_blob`、`_ensure_ts_code` → `download_ts_code`），
> 搬走后这些调用解析在 **`remote.download`** ⇒ 已迁 2 处 patch（`tests/test_remote_ppo.py`）；
> 而宿主（`run_job` / `_prefetch_fill`）仍把 `download_*` 当**裸名字**用 ⇒ 解析在 `worker`
> ⇒ `test_soft_hold_prefetch` 等的 patch **一行不改**。守卫两个方向各一条断言，
> 含「宿主不得改成属性式访问 `download.download_payload(...)`」的警报（那样现有 patch 会静默失效）。
>
> **附带修正**：`tests/test_common_layer.py` 那条钉「`_progress_logger` 全仓恰好两份」的守卫
> 写死了 `remote/worker.py` ⇒ 随本刀改为 `remote/download.py`。
> 反向探针两处均命中（worker 里重定义 `download_payload`、第三份 `_progress_logger`）。
> 门禁 **2314 → 2321 passed / 3 skipped**；mypy **377** 源文件绿。
> 决策 → `DECISIONS.md` §2026-09-23-goalnn-godmodule-download；全文 → `engineering.md` §23「第七刀」。

> **（已执行）下一批刀口（worker 余下 2348 行）**：① 作业生命周期簇（`peek_jobs` / `request_priority` /
> `claim_job` / `job_started` / `job_ready` / `abandon_job` / `job_status` / `start_cancel_watcher` /
> `_priority_rank` / `acquire_job` / `post_result` / `release_job` / `heartbeat` / `worker_tag` /
> `_failure_detail` / `job_body_error` / `report_job_failure`）——
> 注意这一簇的 **seam 很密**（测试大量 patch `worker.post_result` / `worker.run_job` /
> `worker.acquire_job` 等），必须逐点定档；② `run_job`（743 行）/ `worker_loop`
> （364）/ `main` 是宿主，**不动**。

#### 5.3.6 第八刀（2026-09-23，**已完成**）—— 作业取活 / 生命周期 / 回传面 → `remote/job_lifecycle.py`

刀口 = **连续 543 行 / 17 个顶层名**（清单见上「已执行」那节，逐字对应）。`worker.py`
**2348 → 1805**；新模块 **682 行**。依赖 `job_lifecycle → {common.protocol, common.text,
http, wire, bulk_sched}`（全向下，零 `worker`）。

**seam 定档表（逐点实测，非推断）**：宿主 `worker_loop` / `run_job` / `_prefetch_fill` 的调用点
仍解析在 `worker` ⇒ 不迁；**簇内互调**（`acquire_job` → `peek_jobs` / `request_priority` /
`claim_job` / `_priority_rank`；`start_cancel_watcher` → `job_status`；`report_job_failure` →
`worker_tag`）与本簇直调 `_request` / `_wire_add` / `_bulk_pace` / `_sched_headers` /
`_warn_non_200` ⇒ 改指 `remote.job_lifecycle`，共迁 **17 处 `setattr` 调用点 / 5 个测试文件**
（`test_priority_schedule` 10：`_request` 1 + `_wire_add` 2 + `claim_job` 2 + `peek_jobs` 3 +
`job_status` 2 · `test_remote_ppo` 3 · `test_worker_offline_cap` 2 · `test_wire_reroll` 1 ·
`test_http_split` 1）。`test_soft_hold_prefetch` / `test_async_result_upload` /
`test_remote_hotswap` 里对 `W.post_result` / `W.peek_jobs` 的 patch **一行不改**——它们是档位一
（宿主引用 / 调用），见下。

**两条可复用的结论**（已写进 `tests/test_job_lifecycle_split.py` 头部）：

1. ✭ **「引用即接缝」**——判「worker 侧 patch 是否失效」要用 AST 的 **`Load`**（任何引用）而不是
   `Call`。`worker_loop` 的 `ResultUploader(upload=post_result, …)` 是**传值引用**，那 9 处
   `patch remote.worker.post_result` 照旧有效；只按 `Call` 判会得出错误结论。按新判据重扫，
   全仓真正的空操作注入点只剩 `test_http_split.py` 里那条**故意**的反例。
2. ✭ **重绑式标量不做 `is` 恒等断言**——`_opener` 是 `http._get_opener` 里 `global` 重绑的懒建
   单例，`worker._opener` 停在 import 快照。`tests/test_http_split.py` 原先对它做 `is` 断言，
   红绿取决于**文件顺序**（`pytest tests/test_priority_schedule.py tests/test_http_split.py` 红、
   单跑绿；全量 xdist 下恰好绿所以长期未被发现；本刀在 HEAD 上复现并修掉）。新口径：重绑式
   标量只查「名字在」+ 一条与顺序无关的语义断言（重绑只发生在所有者模块）。

> 决策 → `DECISIONS.md` §2026-09-23-goalnn-godmodule-joblifecycle；全文 → `engineering.md` §23「第八刀」。
> 守卫 → `tests/test_job_lifecycle_split.py`（8 例）。

> **下一批刀口（worker 余下 1805 行）**：**已无可整块搬的叶子簇**——余下都是宿主
> （`run_job` / `worker_loop` / `main` / `_prefetch_fill` 与它们的私有助手），再拆就是**拆宿主**
> （切法参 `loop_steps` 那几刀：按一条真实调用链切，不按行数等分）。还剩一项已登记的清理：
> `remote/job_fs._ensure_commit` 是既存死代码（全仓零调用，只搬不删）。
> 之后转 `hub_server` 其余路由组（`_get_*` 12 / `_post_*` 11 → 通用助手）。

#### 5.3.7 收口（2026-09-23，**已完成**）—— `remote/` 内部依赖账本（全局无环守卫）

前八刀把「不得反向 import `remote.worker`」在**六个**拆分守卫里各写了一遍（三个还各带白名单）。
现收到一处：`tests/helpers/remote_dag.py`（**账本 + 判据实现**）+ `tests/test_remote_dag.py`
（整图对账，18 例）；六个拆分守卫改调 `assert_remote_module(module, allowed_project_imports=…)`。

```
LAYERS           remote/ 全部 35 个生产模块的层号（8 层，拓扑秩；数字越小越底层）
DEFERRED_CYCLES  唯一允许的环（仅限延迟 import，必须写明理由）
```

分层读数（与代码结构一致）：L0 原语/叶子 → L1 单层传输/落盘 → L2 `http`/`push_client` →
L3 业务簇（`bc_job`/`download`/`job_lifecycle`/`push_dispatch`）→ L4 组装宿主（`worker`/
`hub_server`）→ L5 入口编排（`run_loop`/`notebook_runtime`/`worker_server`/`smoke_loopback`/
`tunnel_ab_probe`）→ L6 引导（`offline_boot`/`push_bootstrap`）→ L7 `notebook_boot`。

**★ 实测发现**：`remote/` 内部**真有一个环**——`remote.run_loop ⇄ remote.worker`，两边都是
**函数内**延迟 import（`run_loop._real_run_job` 要顶层保持 torch-light；`worker.run_job` 不把编排
入口当宿主的依赖）。顶层图仍是无环 DAG ⇒ **不构成故障**；处置是登记为唯一被批准的延迟环 +
「环里不许出现顶层边」警报。**拆它的前提**（供将来单开）：要么把 `run_loop.verify_plan_file` /
`run_plan_job` 下沉到 L1（比如 `remote/job_fs.py`）而 `worker` 直接拿，要么让 `worker.run_job`
从调用方收这两个函数——两条都属决策，不该混进「加一条守卫」。

> 决策 → `DECISIONS.md` §2026-09-23-goalnn-remote-dag-ledger；全文 → `engineering.md` §23
> 「收口：`remote/` 内部依赖账本」。反探针六处全命中；门禁 **2349 passed / 3 skipped**。

> **（历史）第二刀的预期执行清单**（已执行，保留供对照）：`_bc_fetch_resume` / `_bc_local_resume_dir` /
> `_bc_store_local_resume` / `_bc_load_local_resume` / `_bc_post_epoch` / `_bc_device` /
> `normalize_ppo_device` / `resolve_bc_seed` / `_run_bc_job` → `remote/bc_job.py`（依赖
> `remote.http` + `remote.job_fs`，无环；`d14_corpus_match` 自 `common.protocol` import）。
> 门面（外部直接 import）：e2e 取 `_run_bc_job` / `_bc_fetch_resume` / `_bc_post_epoch`；
> tests 取 `normalize_ppo_device` / `resolve_bc_seed` / `_bc_device`。seam：`_bc_fetch_resume` /
> `_bc_post_epoch` / `_run_bc_job` 直调 `_request` ⇒ 搬走后 patch 目标随它们到 `remote.bc_job`
> （现有 patch 点见 `tests/` / `e2e/test_bc_epoch_e2e.py`，逐点定档）。注意
> `tests/test_worker_device.py` 有读 `worker.py` **源码文本**的守卫（`normalize_ppo_device(device)`
> 计数 + 顺序，看的是 `run_job` 里的调用点=宿主）⇒ 应仍绿，但必须跑一遍确认。

### 5.4 本轮**不做**（已核，刻意保留）

- `remote/notebook_boot.py` ↔ `remote/offline_boot.py` 的孪生助手（`_build_opener` /
  `_load_tailscale_boot` / `_course_dirs`）：**结构性豁免**，共享即断链（§5.1 层契约）。
- `remote/tailscale_boot.py::_progress_logger` ↔ `remote/download.py::_progress_logger`：
  同理只允许**两份**（`tests/test_common_layer.py` 与 `tests/test_download_split.py` 都钉住了这个数）。
- `hub_server._json` / `worker_server._json`：是 **HTTP handler 的方法**，合需要 mixin；
  收益（7 行）远小于「给两个 handler 引入共同基类」的耦合成本。
- `scripts/eval_intent_m5.py` ↔ `train/intent_probe.py` 的 `seq_features` / `build_injection`、
  两个 `build_model`：属**模型构造/特征口径**，正确落点是 `models/intent_net.py` 的类方法，
  属「改模型面」——单开一轮，别混进纯工程重构。
