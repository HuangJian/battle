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

**推荐首刀：`HubHandler` 的 admin 控制面（9 方法 / 218 行）→ `remote/hub/admin.py::AdminRoutes` 混入**

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
