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

#### 5.3.8 拆环（2026-09-23，**已完成**）—— 半离线执行引擎下沉 `plan_run`

§5.3.7 末尾留了两条拆除路线；实测后**两条都没照原样走**（量完才知道它们都不够）：

| 原路线 | 为什么不照做 |
|---|---|
| `verify_plan_file` / `run_plan_job` 沉到 L1（如 `remote/job_fs.py`）让 `worker` 直接拿 | 这两函数的**传递闭包 = 整个执行引擎**（`RunContext` + 单轮 + 主循环 + 云机评估装配 ≈ **963 行**）⇒ 塞进 184 行的作业 I/O 模块 = 造第二个神模块 |
| `worker.run_job` 从调用方收这两个函数 | 方向对（注入）但**对象错**：这两个只是**门面**，要注入的是「**一轮怎么跑**」（`run_job` 自己） |

**实际刀口**：引擎整块 → 新模块 `remote/plan_run.py`（**1035 行**，**L2**）；`run_loop.py` 只留
CLI / 独立续跑 / 门面（**1451 → 491**）；「一轮怎么跑」由调用方注入（`worker` 传 `run_job_fn=run_job`，
CLI 侧传 `_real_run_job`）。引擎里那个 `_real_run_job` **兜底删掉**（它就是环的成因）⇒ 依赖变成
`worker → plan_run`、`run_loop → plan_run`（纯向下）⇒ **环消失，`DEFERRED_CYCLES` 清空**。

**★ 层号落成 L2 而不是用户口径的 L1（带理由的偏离）**：账本把 `LAYERS` 定义为**拓扑秩**
（`LAYERS[m] = 1 + max(依赖层)`），而 `plan_run` 依赖最深到 L1（`offline_deliver` 顶层 /
`offline_eval` 延迟）⇒ **只能是** L2；标 L1 会与自身依赖同层（分层断言当场红）。本步把这条加成
守卫 `test_every_layer_number_equals_its_topological_rank`（全部 36 个模块逐条验算 `1+max(依赖)`，
今天全绿）——「沉到 L1」若真要按字面执行，该做的是**再把共同依赖往下拉一层**，而不是改标签。

**seam**：引擎读的模块全局（`iter_spec` / `pairs_for` / `time` / `DRAIN_FLUSH_SEC` …）调用点在
`plan_run` ⇒ 迁 `remote.plan_run`；且 `run_loop` **不再转发** `iter_spec`（打错模块 = `AttributeError`，
**响亮**而非静默失效，守卫钉住）。引擎公开名由 `run_loop` 做 `X as X` 门面（取名字可以、patch 无效）。
实迁 **1 处** `setattr`（`tests/test_run_loop.py`），其余测试与 `e2e/` **一行不改**。

**守卫**：新 `tests/test_plan_run_split.py`（**10 例**）：定义唯一 · 入口名不倒灌 · **`plan_run`
不得 import `worker` / `run_loop`（含延迟）** · **兜底 `_real_run_job` 必须不存在**（AST 三层）·
`worker` 尾巴延迟 import 引擎且注入自己 · 门面同一对象 + **不许漏**（从入口源码动态取读到的引擎名）·
`iter_spec` seam 在 `plan_run` 且测试跟着迁了 · 账本空且全图零环 · 分层 `plan_run < worker < run_loop`。
**反探针七处全命中**（含 `plan_run` 标成 L1 → 3 处红）。

> 决策 → `DECISIONS.md` §2026-09-23-goalnn-remote-ring-split；全文 → `engineering.md` §23「拆环」。
> 门禁 **2349 → 2359 passed / 3 skipped**；mypy **383** 源文件绿；根 `bun run check` 2120 pass / 0 fail。

**下一步（供将来对照）**：`remote/` 内部已零环，S4 余下是纯结构工作 —— `worker.py` 的宿主
（`run_job` 743 / `worker_loop` 364 / `main`）按**一条真实调用链**切（不按行数等分）→ `hub_server`
其余路由组（`_get_*` 12 / `_post_*` 11 → 通用助手）→ 两个千行状态类（拆 = 拆状态）。还挂着一项清理：
`remote/job_fs._ensure_commit` 是既存死代码（全仓零调用，只搬未删，删要单开）。

#### 5.3.9 第九刀（2026-09-24，**已完成**）—— 拆宿主：训练核下沉 + 进程链分离

`worker.py` 余下 1805 行全是宿主（`run_job` 752 / `worker_loop` 364 / `main` 127 /
`_prefetch_fill` 69）。按**一条真实调用链**（不是按行数）切三块，`worker.py` **1814 → 1281**：

| 块 | 行数 | 去向 | 秩 |
|---|---|---|---|
| **训练核**（`run_job` 650-1076：课程→模型→opt→多卡→ref→demo→PPO→产物） | 427 | 新 `remote/train_core.py::run_training_core` | **L4** |
| **进程生命周期**（`HOT_RELOAD_EXIT`/`_request_reload`/`supervise_worker`/`_release_cloud_machine`） | 110 | 新 `remote/worker_proc.py`（纯 stdlib） | **L0** |
| `_wire_block`（两个调用方都要） | 19 | `remote/wire.py` | L1 |

**先量再下刀**（AST）：核的自由变量 = 11 个块外局部 + 9 个形参；输出只有 **2** 个
（`result`/`course`）；`global`/`nonlocal` 0；测试 patch 过这一族名字 **0** 个 ⇒ **seam-free**。
唯一语义改动：核只收 `payload_bytes`（不收 `raw`）。漏传的检查交给 ruff `F821`（实测报出）。

**选层级联**（账本 `LAYERS` = 拓扑秩）：核依赖最深到 L3 ⇒ 秩只能是 4，`worker` 4→5；
`run_loop`/`notebook_runtime`/`worker_server` 5→6；`offline_boot`/`push_bootstrap` 6→7；
`notebook_boot` 7→8。全部由 `test_remote_dag.py` 的**秩断言 + 分层断言**双向护住。

**源文本守卫迁移**（四个老守卫按行文字面找调用点，搬家后它们「找不到就红」——正是我们要的响亮）：
`test_job_body_crash`（4 处 `job_body_error`）· `test_tpu_backend_guard.TestWorkerWiring`（xla 接线）·
`test_worker_device`（`torch.device(...)` 实参：分叉前归一化仍查壳、两个调用点查核）·
`test_priority_schedule`（取消回调接线 → 核；壳里那份同名 `except` 是另一件事）。

新守卫 `tests/test_train_core_split.py`（**14 例**）；反探针**七处全命中**。门禁 **2359 → 2374**。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-godmodule-traincore；全文 → `engineering.md` §23「第九刀」。

#### 5.3.10 第十刀（2026-09-24，**已完成**）—— 拆宿主之二：`worker_loop` 的「每 job 一轮」下沉 `job_round`

按用户指令「先定档 seam」执行。块 = `worker.py` L943-1119（**177 行**，取活已定 → 三个旁路线程 →
`run_job` → 交回传 → finally 全收）+ `_prefetch_fill`（69 行）+ `_result_settled` 闭包（21 行）。
`worker.py` **1281 → 1042**；新 `remote/job_round.py` **418 行**（L4，与 `train_core` 同层同理由）。

**seam 定档（唯一判据：调用点解析在哪个命名空间；先量后下刀）**：

| 名字 | 处置 | 实测 |
|---|---|---|
| `run_job` | **注入**（`run_job_fn=run_job`） | 住 L5 ⇒ 不能反向 import；宿主按**裸名字**读值 ⇒ 10 处 `W.run_job` patch 不改 |
| `job_ready`·`abandon_job`·`release_job`·`report_job_failure`·`start_cancel_watcher` | **迁移** → `JR` | 12 处 setattr / 4 文件 |
| `peek_jobs`·`download_payload`·`PREFETCH_ROUND_SEC` | **迁移** → `JR` | 8 处 setattr + 2 处 `W._prefetch_fill` 直接调用 |
| `uploader`·`post_result` | **留宿主并传入** | 队列跨 job 存活（`if once:` drain / 最外层 `finally` close） |
| `_prefetch_fill`·`PREFETCH_WIRE_ID`·`settle_result` | **不留门面**（断言禁） | 内部结构；留 `X as X` = 静默空操作的 patch 目标 |

`multi` 是宿主概念 ⇒ 由宿主算好 `code_cache_dir` 传；宿主账目改为回读
`RoundOutcome{jid, ok, uploaded, stop}`（搬前它们直接改宿主局部）。

新守卫 `tests/test_job_round_split.py`（**13 例**）；反探针**七处全命中**。门禁 **2374 → 2387**。
顺手修：① 上游集合从**账本推**而非写死（写死的带引号字面量会被 `test_subproc_util` 的 spawn marker
误判）；②两处既有守卫的前缀匹配会误伤 `remote.worker_proc`（L0）⇒ 改成按模块名精确比。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-godmodule-jobround；全文 → `engineering.md` §23「第十刀」。

#### 5.3.11 第十一刀（2026-09-24，**已完成**）—— `hub_server` 的 25 个路由方法按域分四组 + 四类形状收成 5 个助手

按用户指令「拆 hub_server 的其余路由组：把 `_get_*` 12 / `_post_*` 11 收成通用助手」执行。
第三步已有 `hub/admin.py` 先例（路由按域进混入），本刀把其余切完。

| 新模块 | 方法 | 行数（本体） | 层 |
|---|---|---|---|
| `remote/hub/schedule.py` | peek·priority·claim·start·ready·abandon·heartbeat·release | 153 | L0 |
| `remote/hub/result.py` | result(POST)·fail·status·result(GET)·bc-epoch·bc-resume·bc-metrics | 240 | **L4** |
| `remote/hub/blob.py` | payload·code·ts_code·blob·shared_code | 46 | L0 |
| `remote/hub/offline.py` | task-pack·resume·resume_blob·artifact·result | 205 | L0 |

`remote/hub_server.py` **3728 → 3017**。`result` 住 L4 是因为 `_post_result` 走
`push_dispatch.accept_result`（推/拉必须共用同一个校验函数）⇒ 宿主 `hub_server` **4 → 5**，
`smoke_loopback` / `tunnel_ab_probe` 5 → 6。

**Phase A（纯搬，逐字节等价）→ Phase B（去重）**，刻意分两步：混做会让「某条端点变味」分不清是搬错还是收错。
五助手（实现只住 `hub_server`）：`_job_or_404(known=)` 14 处 · `_job_body(cap, known=)` 4 ·
`_lease_token()` 5 · `_serve_path(p, missing=)` 5 · `_read_raw_body()` 3。
`PRIORITY_BODY_MAX` / `PEEK_MAX` 随迁 `common/protocol.py`（后者有两个读者，谁也 import 不了谁）。

**语义保留点**：`known=True` 只给 `/start` `/fail` `/result`（`/ready` **故意** `known=False`）；
`_job_body` 先闸后体；`_serve_path` 的 404 带**具体**原因；鉴权仍在没有 job 的端点内联（计数钉住）。

新守卫 `tests/test_hub_routes_split.py`（**15 例**，含 `_Probe(hs.HubHandler)` 无 socket 功能性）；
反探针**十一处全命中**。门禁 **2387 → 2402**。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-hub-routes-split；全文 → `engineering.md` §23「第十一刀」。

**下一刀**：`hub_server` 只剩引导链与最后两个千行状态类（`_JobStore` / `_HubQueue`：拆 = 拆状态，
与拆路由是两类工作）；`worker.py` 余 1042 行同理。

#### 5.3.12 第十三刀（2026-09-24，**已完成**）—— 物料落地三兄弟下沉 `download` +「不再拆」的明文理由

第十二刀（清理刀）删掉 `remote/job_fs._ensure_commit` 死代码（全仓零调用，只搬未删）之后，
按用户指令「重构 worker.py 余下的 `run_job` / `worker_loop` / `main`」侦察。结论是：**只有
`run_job` 里还有一整段可搬**（69 行物料落地），另两个是**宿主本体**。

| 模块 | 变化 | 新模块 |
|---|---|---|
| `remote/worker.py` | 1039 → **1033**（`run_job` 350 → **300**） | — |
| `remote/download.py` | 313 → **519**（`_ensure_payload` / `_ensure_code` + 2 个 `NamedTuple`） | 复用已有模块 |

刀口判据不是「哪段长」而是「哪段的判据同源」：payload / code / ts_code 三者失败语义同规
（sha 不匹配 ⇒ `RetryableError`；解包失败 ⇒ `ProtocolError`；sha 内容寻址 + tmp 原子改名）
⇒ 必须住同一个模块。层号**一处没动**（`download` L3 → 新增边 `job_fs` L1，本就向下，无级联）。

顺手的第二处：`worker_loop` 两条分支各写一份的存活日志收成 `_alive_log` + `ALIVE_LOG_SEC`
（2026-09-11 现场：漏了第二处 ⇒ 停机期日志静默被误读成「worker 罢工」）。

**★ 本刀登记的「不做」**：`run_job` / `worker_loop` / `main` **不再往下切**——理由写进
`worker.py` 头部（跨 job 生命周期 / CLI 是数据 / 每分支只做一件事），不靠口口相传。
下一次若还要动 `remote/`，目标应是 `hub_server` 的两个千行状态类。

守卫：`test_download_split.py` +3 · `test_job_round_split.py` +1（零下载调用点 / 13 形参双向一致 /
功能性「patch 打偏就红」/ 存活日志恰好两处调用）；反探针 **11/11**。门禁 **2402 → 2406**。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-worker-landing-trio；全文 → `engineering.md` §23「第十三刀」。

#### 5.3.13 第十四刀（2026-09-24，**已完成**）—— 拆状态：`_JobStore` 按**域**拆成六个混入

按用户指令「拆 hub_server 的 `_JobStore` 状态类（先定档接缝与拆法）」执行。

| 新模块 | 类 | 方法 | 层 |
|---|---|---|---|
| `remote/hub/store_ledger.py` | `LedgerMixin` | 7 | 0 |
| `remote/hub/store_wire.py` | `WireMeterMixin` | 4 | 0 |
| `remote/hub/store_scheduling.py` | `SchedulingMixin` | 9 | 0 |
| `remote/hub/store_leases.py` | `LeaseMixin` | 17 | 0 |
| `remote/hub/store_results.py` | `ResultsMixin` | 5 | 0 |
| `remote/hub/store_offline.py` | `OfflineRoundsMixin` | 5 | **1**（另需 `remote.artifacts`） |

`hub_server.py` **3017 → 2072**（−31%）；新六块共 **1273 行**；47 个方法与本体 895 行搬走。
组合类只留 `__init__` / `note_worker` 与进程级状态（`halt_workers` / `_workers`）。

**定档的结论：拆法 = 混入，不是协作对象。** 三条实测判据：① 49 个方法里 **30 个**在同一把
`_lock` 下（各持一把锁 = 换并发语义）；② 跨域互调 **37/49**（`_claim_locked` →
`_job_priority_locked` / `_collect_expired_locked` → `_drop_commitment_locked` →
`_bump_epoch_locked`）⇒ 混入留在 `self.X` 上 = **零 seam**；③ tests 直读 `store._leases` 一类
私有属性（20+ 处断言）⇒ 协作对象会全部改路。**混入 = 同一个对象、同一把锁、零行为变化。**

代价是状态声明分散 ⇒ 四个有状态的域各一个 `_init_<域>(self)`，组合类 `__init__` 逐个**显式**调用
（不用 `super()` 链）；`store_results` / `store_offline` 真无常驻状态，**不造 `pass` 空钩**
（守卫正面断言「它俩方法体里零赋值」）。顺手删掉旧 `__init__` 里那把**被 `_AuthGuard` 覆盖掉**的
`Lock()`（一个被丢弃的锁对象是下次读的人的陷阱）。

**纯搬对账**（本刀的尺子）：AST 逐成员比对 HEAD vs 新家 ⇒ **58 个成员逐字节等价、零差异**；
旧 `__init__` 的 **21 条赋值 + 43 行字段注释**全部逐字在新家。

守卫 `tests/test_hub_job_store_split.py`（**17 例**，含两条功能性：跨域链路落在同一个对象上 ·
持有 `_lock` 时最独立的计量簇也阻塞）；反探针 **11/11 命中**。门禁 **2406 → 2423**。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-hub-jobstore-mixins；全文 → `engineering.md` §23「第十四刀」。

#### 5.3.14 第十五刀（2026-09-24，**已完成**）—— `_HubQueue` 拆七混入（先办两相：先把两个类搬出宿主）

按用户指令「拆 `_HubQueue` 多课程调度面（**先侦察那 28 个同名委托方法**）」执行。侦察结论改变了切法。

**A 相（使能）**：`queue_resume` 要按课程构造/注解 `_JobStore`，而 `remote/hub/*` 不得 import
`hub_server`（成环）⇒ 先搬两个类：`_AuthGuard` + `_is_loopback` → `hub/auth.py`（101 行）、
`_JobStore` 组合类 → `hub/store.py`（108 行）。两处都**自别名 re-export**（`hs._AuthGuard` /
`hs._JobStore` / `patch remote.hub_server.*` 照旧）。

**B 相**：`hub_server.py` **2072 → 887 行**（累计 **3017 → 887，−71%**）。新八块共 **1684 行**：

| 新模块 | 类 | 成员 | 行 | 层 |
|---|---|---|---|---|
| `hub/queue_scope.py` | `QueueScopeMixin` | 15 | 222 | 2 |
| `hub/queue_discover.py` | `QueueDiscoverMixin` | 3 | 164 | 2 |
| `hub/queue_auth.py` | `QueueAuthMixin` | 5 | 68 | 1 |
| `hub/queue_claims.py` | `QueueClaimsMixin` | 9 | 294 | 2 |
| `hub/queue_resume.py` | `QueueResumeMixin` | 10 | 258 | 1 |
| `hub/queue_observe.py` | `QueueObserveMixin` | 14 | 199 | 2 |
| `hub/queue_store_face.py` | `QueueStoreFaceMixin` | 18 | 137 | 1 |
| `hub/queue_peer.py` | `QueuePeer`（**纯声明**） | 74 | 178 | 1 |
| `hub/queue.py` | `_HubQueue`（组合类） | `__init__` + 2 常量 | 164 | — |

**★ 侦察推翻的第一个直觉：那 28 个同名委托方法不能收成 `__getattr__`**。三条判据：① 签名分
三档 —— 28 条逐参数一致、3 条多一个**前置 `course`**（`claimable_job_ids` / `store_offline_artifact` /
`store_offline_result`：store 每课程一份、队列要跨课程寻址）、1 条**改名**（`abandon` →
`abandon_job`）；② 缺归属时的返回值**逐方法不同**（`False`/`None`/`{}`/`[]`/`0`）⇒ 得把一张
31 行「空值表」藏进字符串；③ 它是类 docstring 写下的对外承诺，`__getattr__` 会让 mypy 看不见。
⇒ 三张**写死闭集表** + 可执行断言（同名面闭集 · 28 条签名逐参数 · 3 条只多前置 `course`）。

**★ 侦察带来的第二个结构决定：`QueuePeer` 共同声明面**。七混入必须互不 import（本刀要消灭耦合），
但各自要调兄弟方法 ⇒ mypy 一片 `attr-defined`。三条路里选第三条：74 个 `def X(...) -> T: ...`
（**零实现**），混入继承它 ⇒ mypy 看声明、运行时由后续混入的活动实现覆盖。`_store_of` **刻意不进**
（那要 import `hub.store` ⇒ L3 ⇒ 混入 ≥L4 ⇒ `hub.queue` L5 ⇒ `hub_server` L6 = 与
`smoke_loopback`(L6) 同层）。守卫两条：纯声明（体里只有 `...`）+ 与真实现逐参数一致。

**★ 门面的活性也要量**：签名一致抓不住「方法还在、活不干了」。判据**不看名字看结构**：体里
**恰好一处**「拿到 store 的取用」且转发目标名等于声明值。取用**四种写法都认**（`_store_of(job_id)` /
`_stores[course]` / `_stores.get(course)` / `_solo`）—— 这是跑出来的：第一版只认前两种，
`note_worker` 与 `claimable_job_ids` 立刻顶出来；`note_worker` 因此被认定为**唯一一条非转发的
同名方法**（队列自己就是登记表的拥有者），写成例外表 `FACADE_OWN` + 一条**反面**断言。

**★ 与第十四刀刻意相反：`__init__` 不拆钩子。** `_JobStore` 那时拆四个 `_init_*`（状态散在 900 行、
四域独立）；这里 44 行且几处**咬合**（`_solo` 决定 `_now`、`_discover_root` 决定 `_discover_last`
初值、`_adopt_solo` 运行期把 `_halt_default` / `_workers` / `_auth_fail` 从 store 搬到 `self`）
⇒ 按域切只会把直线扯成跳转。

**纯搬对账**：AST 逐成员比对 ⇒ **76/76 逐字节等价**；旧 `__init__` 的 16 条字段声明 + 43 行字段注释
逐字在新家；七混入**零重名**。

守卫 `tests/test_hub_queue_split.py`（**25 例**，含三条功能性：跨域链路落在同一个对象上 ·
持有 `_lock` 时最外层门面也阻塞 · `_adopt_solo` 跨域搬进程状态）；反探针 **14/14 命中**。
门禁 **2423 → 2449**；mypy **401 → 413**。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-hub-hubqueue-mixins；全文 → `engineering.md` §23「第十五刀」。

**下一刀**：`hub_server` 余 **887 行** —— 迭代器与两个状态类都已不在里面，只剩**引导链与 HTTP 面**
（handler / 派发表 / `main` / 启动参数）。这是 S4 的最后一块结构面。

#### 5.3.15 第十六刀（2026-09-24，**已完成**）—— `hub_server` 收口：HTTP 面 / 引导链 / 薄入口

按用户指令「拆 hub_server 剩下的引导链与 HTTP 面，收口 S4」执行。**`remote/hub_server.py`
887 → 100 行**（累计 3017 → 100，**−97%**），且剩余部分只有 20 行代码。

| 新模块 | 层 | 行 | 内容 |
|---|---|---|---|
| `remote/hub/http_face.py` | **L5** | 575 | 来源判定（`CF_SOURCE_HEADER` / `SEND_*` / `_is_ip_literal` / `attributed_source`）+ `HubHandler`（五组路由混入的组装 + 5 个通用助手） |
| `remote/hub/boot.py` | **L6** | 310 | 引导链：`DISCOVER_SCAN_SEC` · `as_hub` · `make_server` · `main` |
| `remote/hub_server.py` | **L7** | 100 | 入口（`python -m remote.hub_server`）+ 17 条自别名 re-export + `__all__` 契约 |

**为什么两处而不是一处**：HTTP 面对每个请求负责，引导链对一次进程启动负责 —— 读者、生命周期、
失败模式（请求级 500 vs 启动即 `exit(1)`）都不同。合成一个模块就得让 argparse 与
`BaseHTTPRequestHandler` 住同一文件。

**层号先算后切**：`LAYERS` 是拓扑秩 ⇒ 中间插一层会顺反向边涨上去。先模拟后实测 ⇒ **级联只有 3 个**：
`hub_server` 5→7、`smoke_loopback` / `tunnel_ab_probe` 6→8。顺带修掉账本顶部那段把 `hub_server`
列在「L4 组装」的**过时散文**（数字有守卫管，散文没人管）。

**门面 = 契约**：入口现在**零定义**（守卫正面断言：无 `def`/`class`，唯一赋值是 `__all__`）——
比「11 个搬走的成员不在」更硬，它挡的是「顺手补个小函数」。配套对象恒等 + 17 名闭集 +
不得挂实现 import。

**★ 踩到的坑**：`SEND_TIMEOUT_SEC` 的唯一读者 `HubHandler._bytes` 读的是**所在模块的全局** ⇒
搬走后对入口 `setattr` 变成**静默空操作**（第十三刀「名字 ≠ 注入点」第二次现身）。守卫机械化：
裸 `Name` 读 + 全仓恰好一处 patch 且写在实现模块上。

**⚠ 搬走代码会静默废掉四条读源码的守卫**（`_class_methods(HUB_SERVER, …)` → `StopIteration`；
`test_hub_admin_split` 的「不在 hub_server 里」变成恒真空话；`test_jobs_next_retired::_PROD_FILES`
扫空壳 ⇒ 退役端点回流不会被发现）。⇒ **搬文件时必须一条条问「谁会按路径读它」。**

**★ 顺手修掉一个跨项目静默回归（第十四刀留下、HEAD 上已经红）**：dashboard 的镜像常量守卫
`poison-unfreeze.test.ts` 按写死路径读 `hub_server.py` 找 `FREEZE_AFTER_RECLAIMS`，而该常量第十四刀
已搬到 `hub/store_leases.py` ⇒ 用例失败但**无任何门禁会发现**（nn 侧不跑 dashboard 测试）。改成
**在 python 源码树里搜定义**。同族盲区：dashboard 监督器哨兵只盯入口文件 ⇒ 改 `hub/http_face.py`
不会触发重启（第十一刀起如此）；修成 `hubImplementationFiles()` 枚举 `remote/hub/*.py` + 一条新守卫。

守卫 `tests/test_hub_entry_split.py`（**13 例**，含两条功能性：从门面拿 `make_server` 真起服务打通
`/ping` 且错 token 401 · `as_hub` 幂等）；纯搬对账 **11/11 逐字节等价**、旧 body 零残余；
反探针 **12/12 命中**。nn 门禁 **2449 → 2462**；mypy **413 → 415**；根 `bun run check` 2120 pass；
dashboard **1105 pass / 0 fail** + typecheck 绿。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-hub-entry-split；全文 → `engineering.md` §23「第十六刀」。

**S4 到此收口**：`remote/` 的四个神模块（`worker` / `hub_server` / `loop_steps` / `TrainingSteps`）
里前两个已拆完并收口（第十三·十六刀），第四个见 §5.2。余下可做的是**同一套手法**在
`rl/` 侧继续（`TrainingSteps` 本体 952 行 / 20 方法，切法 = 按一条真实调用链切）。

#### 5.3.16 第十七刀（2026-09-24，**已完成**）—— `TrainingSteps` 的 in-loop 评估链 → `rl/loop_eval.py`

按用户指令「按同一条『真实调用链』手法拆 `rl/loop_steps.py` 的 `TrainingSteps` 本体（952 行 /
20 方法）」执行。**`rl/loop_steps.py` 940 → 666 行**（累计 2328 → 666）；新模块
`rl/loop_eval.py`（375 行）承载 `TrainingEval`（8 成员）——**混入**，同一对象、零行为变化。

| 判据 | 实测 | 用在哪 |
|---|---|---|
| 20 个方法里互相调用的 | **7 个**，连成**一条链** | 簇 = 这条链（+ `_eval_on_round` 占位） |
| 链的外部入口 | 3（轮内 2 + 收官 1） | 方向 = 调用者依赖被调用者 ⇒ 本簇当基类 |
| 状态槽 | 5 个，只被这簇读写 | 随簇搬；两处**跨模块手**经继承（闭集表钉住） |
| 模块级名字 | **零** | ⇒ **零 patch 点迁移**（前两刀最贵的那步这次是空的） |

**基类元组是追加**：`class TrainingSteps(TrainingRemote, TrainingEval)` —— 2026-09-23 写下的
`__mro__[1] is TrainingRemote` 与四个「继承真混入」的测试宿主逐字仍成立（追加而非插队）。

**守卫** `tests/test_loop_eval_split.py`（11 例）：成员闭集 · 对象恒等 · 基类元组 + 组合类不变 ·
五槽位单处声明 · 跨模块手闭集 · 顶层 import 闭集 + DI 只许延迟 · 不得反向 import · **两条功能性**
（跨模块交棒 · 占位响亮失败）。反探针 **14/14**。纯搬对账 **8/8**（1 处申报差异）。

**⚠ 坑**：按路径读 `loop_steps.py` 的守卫（`tests/test_eval_a_once.py`）随搬家红——修法升级成
「在 `rl/` 树里找谁持有这个名字」+「拿到它的模块里住着 `_dispatch_delayed_eval`」；
`ruff format --check` 不是门禁（HEAD 上本来就不格式），别顺手 format。

**下一刀（如果要继续）**：`loop_steps.py` 余 **666 行**，剩下的 12 个方法**都是叶子**（无方法间
调用链）——`_log_report` / `_write_iter_stats` / `_record_iteration` / `_commit_journal` /
`_forensics`（报告与落账）· `_volume_plan_block` / `_export_offline_bundle` / `_export_weights` /
`_ensure_ts_code` / `_per_stage_quota`（导出与配额）· `_course_iter` / `_hot_reload_course`（课程）。
⇒ 下一刀若仍要**按链**切，可考虑「报告与落账」那条（`_write_iter_stats → _log_report →
_record_iteration` 的**数据流**，不是调用流）或「导出与配额」（`_volume_plan_block` 被
`_export_offline_bundle` 调用，其余靠 `self`）；再往下就是**按职责**切而不是按链切，收益递减——
判据要先量（叶子之间有没有共享状态、有没有数据流边），别按行数等分。

#### 5.3.17 第十八刀（2026-09-25，**已完成**）—— `TrainingLoop` 的动态采集链 → `rl/loop_volume.py`

按用户指令「全程自主，继续按同一手法拆 `rl/` 侧神模块」执行。**`rl/loop_core.py` 1386 → 931 行**
（−33%；搬走 462 行 = 445 方法 + 9 行分节注释，留下 8 行指路注释）；新模块
`rl/loop_volume.py::TrainingVolume`（**573 行**）。

| 判据 | 实测 | 用在哪 |
|---|---|---|
| `TrainingLoop`（25 方法 / 1089 行）的连通分量 | **3 条链**：volume **9** · 生命周期 7 · 基线 2 | 取最大且最内聚的 volume |
| 9 成员是否同一条链 | 是（一个连通分量，5 个共享槽） | 簇 = 这条链；且恰是旧类的**尾块**（连续 462 行） |
| 生产入口在哪 | **全部在 `RoundSteps`**（`step_course_iter` → `_iteration_pairs`；`step_rollout` → `_volume_active` / `_volume_collect_continuous`） | 方向 = 调用者依赖被调用者 ⇒ **`class RoundSteps(TrainingVolume)`** |
| `_volume_topup` 的调用点 | **生产零调用点**（VOLUME_RULE_V2 后 `step_volume_topup` 是空步）；只由 e2e/单测以 unbound 形式驱动 | 写进散文与守卫（不然散文会撒谎） |
| 模块级名字（patch 点） | **1 个**：`log` | 迁 `1` 处 `setattr`（`e2e/test_volume_e2e.py`） |
| 跨模块手 | `__init__` Store×7 + `_record_iteration` Load×3（量出来的） | 写成闭集表 |

**为什么不「给 `TrainingLoop` 加基类」**：那要改组合类 + 四个「继承真混入」的测试宿主，并让第十七刀
守卫里「组合类三件套不变」那句失守；走调用者一侧 ⇒ `TrainingLoop.__bases__` 与**全部既有守卫一行不改**。

**守卫** `tests/test_loop_volume_split.py`（11 例）：成员闭集 · 对象恒等（既有用例的 unbound 绑定形态）·
`RoundSteps.__bases__` + 组合类三件套 + 判定 MRO 逐项 · 七槽位声明在新家且 `__init__` 仍全部赋值 ·
跨模块手闭集 · 顶层 import 闭集 · 延迟 import 白名单 · 不得反向 import · **两条功能性**（`log` seam
在本模块 · unbound 绑定经 MRO 取真实现）。反探针 **14/14**；纯搬对账 **9/9 逐字节、零申报差异**。

**⚠ 两个坑**：① 散文宣称「`step_volume_topup` 调 `_volume_topup`」而它其实是**退役空步** ⇒ 被自己的
守卫顶出来（四处散文改对）；② 分层快照按设计**先红再登记**（`loop_volume → rl.rollout_phase` ⇒
登记进 `RL_ORCHESTRATION`）。

**下一刀候选（已量）**：`rl/batch_eval.py`（1785 行，全仓最大）的 35 个顶层函数是**一个 28 节点巨团**
⇒ 按链切不动，要拆得先设计「批存储」接口（真设计改动）——**✅ 该设计已于 2026-09-25 交付：见 §5.5**；
`loop_core.py` 余下的生命周期链（7 成员）
就是「主循环骨架」本身，切开前需先答「拆出去后谁是宿主」。**⇒ 第十九刀已答完并落地（见下）。**

#### 5.3.18 第十九刀（2026-09-25，**已完成**）—— 主循环骨架 → `rl/loop_lifecycle.py`

用户指令：「拆 `loop_core` 余下的生命周期链（7 成员 = 主循环骨架），**先答清「拆出去后谁是宿主」**」。
**`rl/loop_core.py` 931 → 446 行**；新模块 `rl/loop_lifecycle.py::TrainingLifecycle` **673 行**
（7 方法 351 行 + 7 个只能随它走的模块级定义 150 行 = 闭包实测）。

| 判据 | 实测 | 用在哪 |
|---|---|---|
| 入边（谁以 `self.` 调本簇） | `loop_remote`（TrainingRemote）×1 · `loop_round_steps`（RoundSteps）×2，都打 `_evalboard_idle` | 宿主必须同时是两个 caller 的祖先 |
| 两个 caller 的祖先集交集 | **`{object}`**（空） | **sibling 宿主不存在** ⇒ 只能挂组合根 |
| 出边（谁在 concrete 上调它） | `.run`（`rl/loop.py`）· `.run_one_round`（`loop_runner`）· `._setup` / `.finish_course`（`loop_serve` 的多态 `engine`） | 同指 `TrainingLoop`（`BcLoop` 自带同名方法，无关） |
| 7 个成员名在既有混入里的同名 `def` | **0** | 追加末位不会被 MRO 遮罩 |
| 7 个模块级名字的 monkeypatch 点 | **0**（只有 4 处普通 import） | 不留别名，同步 4 处调用点 |

**组装**：`TrainingLoop.__bases__` 三件套 → **末位追加**四件套；两处 S17/S18 写的「组合类三件套逐字不变」
断言**演进登记**为四件套（两处**把心**不动：本簇不是组合类的直接基类，`TrainingSteps.__bases__` /
`RoundSteps.__bases__` / 各 `__mro__[1]` 逐字不变）。

**守卫** `tests/test_loop_lifecycle_split.py`（13 例）：成员与模块级名字只在新家 · 对象恒等 · 旧家零别名 ·
组合类四件套 + 判定 MRO · **宿主判据的机器形式**（入边计数 + 祖先集交集为空 + `not hasattr(RoundSteps,
"_evalboard_idle")`）· 借用声明 == 派生集 · 三张跨模块手表（入边/出边/槽位写-读）· 顶层 import 闭集 +
禁反向边 · **★ 三条功能性**（出边手归属 · `run_one_round` 真跑通含异常分类与让位响亮报错 · 旧家不再吸收
patch）。反探针 **18/18**；纯搬对账 **14/14 逐字节**（留下的成员 **10/10** 同）。

**⚠ 三个坑（全是「搬家后按路径读源码的守卫失效」家族）**：① `tests/test_batch_eval.py` 写死路径读
`loop_core.py` 找 `maybe_dispatch_batch` ⇒ 改读持有者 + 钉「恰好一处定义」；② `e2e/test_loop_supervisor_
integration.py` 经中间名字 `rl.loop_core.time` 补 `time.sleep` ⇒ 旧家不再 import `time` 后响亮
`AttributeError` ⇒ 改为直接补 `time` 模块对象；③ **dashboard 跨项目盲区**（第十六刀同族）：`kickstart-
receipt.test.ts` 写死路径读 `loop_core.py` 找 `KICKSTART_DEFAULT_WARN` ⇒ 改成源码树搜定义
（`rlSourceDefining`）+ 同步四处注释路径。**锚点教训（延续第十五刀）**：⑧ 原选字面量在
`loop_round_steps.py` 里有两处，`assert count == 1` 当场拦下——锚点写错 ≠ 守卫空档。

**门禁**：nn **2484 → 2497 passed / 3 skipped**；ruff / mypy 绿；根 `bun run check` 2120 pass / 0 fail；
dashboard typecheck + **1105 pass / 0 fail**。

#### 5.3.19 第二十刀（2026-09-25，**已完成**）—— 组合根收尾：三簇叶子出组合类 → 纯组合类

用户指令：「拆 `loop_core` 剩下的基线评估链（2 成员）与叶子方法，让组合根成为纯组合类」。
**`rl/loop_core.py` 446 → 240 行**；新模块三件（合计 355 行 = 128 + 96 + 131）——`TrainingLoop` 余下**只有**
`__init__` + `_run_inspect`。

| 新家 | 成员 | 判据（谁调它 / 同源在哪） |
|---|---|---|
| `rl/loop_baseline.py::TrainingBaseline` | `_baseline_eval_weights` · `_maybe_dispatch_baseline_eval` | it0 基线（wver 指纹 + 落地摘）——`step_course_iter` |
| `rl/loop_iter_dir.py::TrainingIterDir` | `_check_quota_incident` · `_prepare_iter_dir` | **`self._traj_dir` 里有没有本轮的活**——`step_prepare_iter` / `step_course_iter` |
| `rl/loop_dispatch.py::TrainingDispatch` | `_eval_on_round` · `_evalboard_yield` · `_rollout_phase` | **本轮把活派给谁 / 让位给谁**——`step_rollout` / `step_eval_dispatch` / `step_record_iteration` |

**宿主**：mixin 级父调用者**全部**是 `RoundSteps` ⇒ 三簇挂它一侧（`class RoundSteps(TrainingVolume,
TrainingBaseline, TrainingIterDir, TrainingDispatch)`），**`TrainingLoop.__bases__` 一行不改**。
`_eval_on_round` 另有 `TrainingEval` / `TrainingGuards` 两个 sibling 调用者，但三者互不继承**不构成**必须挂
组合根的理由（`RoundSteps` 是组合根的第一个基类，挂它的基类里已在 `TrainingLoop` 的线性化上）。

**★ 唯一真约束 = `_eval_on_round` 的顺序契约**：`rl/loop_eval.py` 的同名成员是**占位**（body `raise`），
真实现必须在 MRO 里更靠前，否则占位胜出 ⇒ 静默返回 falsy 把 eval 全关掉；挂 `RoundSteps` 一侧天然满足，
改成组合根**末位**会反过来胜出（反探针 ⑤）。

**结构性例外**：`_run_inspect` + 模块级 `run_inspect` 必须同住组合根（`_run_inspect` 按模块全局解析它，
而那个模块不能 import `rl.loop_core` 当基类 ⇒ 成环）⇒ 守卫
`test_composition_root_keeps_only_the_structural_pair` 正面钉住闭集 `{__init__, _run_inspect}`。

**守卫** `tests/test_loop_core_tail_split.py`（13 例）：成员定义只在新家 · 接线对象恒等 · 组装逐字
（`RoundSteps.__bases__` + **全量 MRO 名单的唯一所有权**）· 组合根方法闭集恰好两项 · 借用声明 == 派生集
（类体零带值槽位）· 入边闭集（**AST 计真实 `Call`**）· 出边为空 · 槽位读者闭集 + **写手唯一** · 顶层 import
闭集 + 禁反向边 · **★ 四条功能性**（`_check_quota_incident` 真跑且 `log` seam 落点在本模块 · 顺序契约 ·
旧家不再吸收 patch）。反探针 **18/18**；纯搬对账 **7/7 逐字节**（留下的 **3/3** 同）。

**两条教训（都是「判据口径」而不是「代码」错）**：① 写手断言首版沿用「谁碰到这个名字」 ⇒
`run_one_round` 也**读** `_course_fp` ⇒ 改名赋值点守卫**不红**（探针首次 17/18）；改成 AST 只看赋值目标
（`_self_assigns`）+ 断言 == `{"_setup_common"}`。② 入边首版用 `src.count("self.x(")` ⇒ 新模块头注里那句
「`self._maybe_dispatch_baseline_eval(...)` …」被算成一条入边 ⇒ 对着合法文档报假红；改成 AST 计 `Call` 节点。

**坑（第九/十/十一次同族）**：`tests/test_batch_eval.py` 写死路径读 `loop_core.py` ·
`e2e/test_loop_supervisor_integration.py` 经中间名字 `rl.loop_core.time` 补 `time.sleep` ·
dashboard `kickstart-receipt.test.ts` 写死路径读 python 源码 —— 全部改成「读持有者 / 源码树搜定义」。

**记账校正**：第十九刀公布的 `loop_lifecycle.py` 行数 617 → 672 还差一（真值 **673**）⇒ 连提交消息带四处文档
一次改齐。教训：可测量值落盘后量一次，别在编辑过程中随手报。

**门禁**：nn **2497 → 2510 passed / 3 skipped**；ruff / mypy 绿；根 `bun run check` 2120 pass / 0 fail；
dashboard typecheck + **1105 pass / 0 fail**；`check-decisions` ok。

#### 5.3.20 第二十一刀（2026-09-25，**已完成**）—— `TrainingSteps` 产物出包簇 → `rl/loop_export.py`

用户指令：「继续按同一手法拆 `rl/loop_steps.py` 或 `loop_remote.py`」。取 `loop_steps`——**667 → 541 行**，
新模块 `rl/loop_export.py::TrainingExport`（**210 行 / 4 成员**）。

**刀口：先答「有没有链可牵」**。`TrainingSteps`（12 方法）的类内调用图**只有唯一一条方法间调用链**
（`_export_offline_bundle` → `_volume_plan_block`），其余 **8 个是叶子**（零互调）⇒ 无连通链可牵，改按
**判据同源**取簇：判据 = 「产物打包 / 打指纹 / 缓存 TS 码」= **出包家族**同一件事。

| 新家 | 成员 | 入边（谁调它） |
|---|---|---|
| `rl/loop_export.py::TrainingExport` | `_ensure_ts_code` · `_volume_plan_block` · `_export_offline_bundle` · `_export_weights` | `loop_round_steps.py` ×2 · `loop_remote.py` ×1 · 本模块 ×1 |

**宿主：末位追加** —— `class TrainingSteps(TrainingRemote, TrainingEval, TrainingExport)`；`__mro__[1]` 仍是
`TrainingRemote`，`TrainingLoop.__bases__` 一行不改。依据 = **实测遮罩面**（4 个成员名在既有混入里零同名 `def`）。
三处钉元组的既有断言**演进登记**，全量 MRO 名单（S20 那篇）在 `TrainingEval` 后插入 `TrainingExport`。

**patch 面 = 零迁移**（`rl.loop_steps` 只 patch 过 `log`，测的是留守的 `_write_iter_stats`），但仍钉「`log` seam
落在本模块」。**死 import 清理**：`dist_common` / `backup_weights` / `_MODE_BACKUP_PREFIX`（AST 断言零引用）。

**★ 真实发现**：`_export_offline_bundle` 的「无起点权重」分支**走不到**——`weights_fingerprint` → `sha256_file`
对不存在文件**响亮抛 `FileNotFoundError`**（不是 falsy）⇒ 功能性用例改测 `iters<=0` 门（真跑 `SystemExit`）。

**坑**：dashboard 跨项目盲区（第十六/十九/二十刀同族）—— `course-lifecycle.ts` 两处注释把 `--run-iters<0` 守卫
指到 `loop_steps.py`，而守卫在 HEAD（S20）上**早已**住 `rl/loop_remote.py` ⇒ 改成 `rl/loop_remote.py`（注释-only）。

**守卫** `tests/test_loop_export_split.py`（14 例）+ 反探针 **19/19**；纯搬对账 **4/4 逐字节**（留下的 **8/8** 同）。
分层快照先红再登记**第六次**（`loop_export` 由**函数内延迟 import** `remote.hub_client` 入选）。
**门禁**：nn **2510 → 2524 passed / 3 skipped**；ruff / mypy 绿；根 `bun run check` 2120 / 0；dashboard **1105 / 0**。

#### 5.3.21 第二十二刀（2026-09-25，**已完成**）—— `loop_remote` 的 862 行连通分量 → 四簇混入

用户指令：「把 `loop_remote.py` 那 862 行连通分量按「判据同源」拆成多个混入」。`rl/loop_remote.py`
**997 → 35 行**（−96%），13 方法 → 四簇（方法体 69 / 470 / 76 / 247 行），组合根零方法。

**刀口：一条连通分量没有「链」可牵 ⇒ 判据同源**（S19/S20 的链取法在此无解——13 个节点本就连成一个
连通分量）。四判据：推送腿 / 一个 job 的四步 / 失败策略 / 驱动入口。

| 新家 | 方法 | 判据 |
|---|---|---|
| `rl/loop_remote_push.py::TrainingRemotePush` | `_push_submit_node` · `_push_submit_first` · `_push_fetch` | 把一份 job 送到节点 |
| `rl/loop_remote_job.py::TrainingRemoteJob` | `_remote_ppo` · `_remote_ppo_publish` · `_remote_ppo_probe` · `_remote_ppo_fetch` · `_remote_ppo_land` | 一份 job 的四步 + 组合入口 |
| `rl/loop_remote_fail.py::TrainingRemoteFail` | `_abort_node_failure` · `_handle_remote_failure` | 远端失败策略 |
| `rl/loop_remote_drive.py::TrainingRemoteDrive` | `_remote_ppo_step` · `_remote_iter` · `_remote_run_segment` | 谁驱动这条腿 |

**宿主 = 把 DAG 写进类声明**：`Push ← Job ← {Fail, Job} ← Drive ← TrainingRemote`（Fail 与 Job 并列
成为 Drive 的第二个基类）。组合根仍住 `rl/loop_remote.py` 且**零方法** ⇒ `TrainingSteps.__bases__` /
`TrainingLoop.__bases__` / 测试宿主 / `from rl.loop_remote import TrainingRemote` 调用点**一行不改**。

**patch 面**：`_push_submit` / `_push_wait_result` → `rl.loop_remote_push`（e2e 两处 `setattr` 改址）；
`dist_common` → `rl.loop_remote_drive`；`log` → 每簇自己的模块。patch 旧的 `rl.loop_remote.*` 现在是**静默空操作**。

**守卫演进 6 处**（transport 定义面改读组合根 MRO / export 两条入边换落点 + 读者改元组 / lifecycle 入边
改址 / core_tail 的 `MRO_NAMES` 插四名 / volume 改读「四新家 + 组合根」/ layering 登记四模块）。
新守卫 `tests/test_loop_remote_split.py`（**15 例**，含三条功能性）+ 反探针 **24/24 全红**；
纯搬对账 **13/13 逐字节**。分层快照先红再登记**第七次**。

**★ 新形态坑**：`test_loop_volume_split` 按旧文件**类名**枚举方法——类体被切空后它**静默读到空集也算过**
（与「按路径读写源码的守卫响亮失败」不同）⇒ 搬家还要问「谁会静静地读到空」。
**dashboard 跨项目盲区（同族）又一次**：`--run-iters<0` 守卫随 `_remote_run_segment` 迁到
`loop_remote_drive.py`（S21 才刚指到 `loop_remote.py`）⇒ 同步改址。

**门禁**：nn **2524 → 2539 passed / 3 skipped**；ruff / mypy 绿（433 文件）；根 `bun run check` 2120 / 0；
dashboard typecheck + **1105 / 0**；`check-decisions` ok。

#### 5.3.22 第二十三刀（2026-09-25，**已完成**）—— `loop_guards` 的 13 成员按判据同源切四簇

用户指令：「拆 `rl/loop_guards.py` 或 `loop_steps.py` 剩下的叶子，先给侦察结论与刀口判据」。
`rl/loop_guards.py` **785 → 194 行**（−75%），13 方法 → 四簇（177 / 200 / 287 / 78 行）。

**刀口：先选文件，再选切法**——`loop_steps.py` 余下 8 叶被否决（类内零互调 + 判据彼此无关 ⇒
拆它只能按大小）；`loop_guards.py` 的调用图是**多 sink 的 DAG**，判据同源有四条轴。

| 新家 | 方法 | 判据 |
|---|---|---|
| `rl/loop_guards_trip.py::TrainingGuardsTrip` | `_breaker` · `_stop_loss` | **过程面**硬边界（连击式） |
| `rl/loop_guards_leg.py::TrainingGuardsLeg` | `_kickstart_burn` · `_paired_kill` | **结果面**停腿 |
| `rl/loop_guards_gate.py::TrainingGuardsGate` | `_gate` · `_apply_verdict` · `_warn_min_train_unreachable` · `_budget_hard_cut` | **课程结束门一整族** |
| `rl/loop_guards_sweep.py::TrainingGuardsSweep` | `_rotate_cleanup` | **轮级磁盘回收** |
| `rl/loop_guards.py::TrainingGuards`（**留根**） | `_ledger_apply` · `_sync_cloud_halt` · `_is_soft_verdict` · `_gate_halt_mode` + 3 常量 | **共享 sink**（提供者留根、调用者出包） |

**宿主 = 留宿主**，依据 S19 已记录的规则「入边来自多个 sibling ⇒ 锁进组合根」：`_ledger_apply` 被 3 簇调、
`_sync_cloud_halt` 被 2 簇 + 外部 `loop_lifecycle.finish_course` 调。四簇零互调 ⇒ 元组顺序惰性；
`TrainingLoop.__bases__` / `TrainingSteps.__bases__` / 既有 import 与测试宿主**一行不改**。

**★ patch 面零迁移**（与前四刀最大的差别）：`set_cloud_halt` / `dist_common` 被四个测试文件 5 处打桩，
而唯一真调用点 `_sync_cloud_halt` 留根 ⇒ 那些文件**一行不改**。「谁是 patch 锚点」升级为本刀宿主判据。

**mypy 逼出的设计事实**：首轮 13 个 `attr-defined` ⇒ 每簇加声明块，并把声明面写成契约
`declared == 派生集 ∪ 借用的方法`。

**守卫演进 4 处**：core_tail（`MRO_NAMES` 插四名 + `_eval_on_round` 入边改址）/ volume（覆盖面主动
扩成「四新家 + 组合根」）/ layering（**未红**——四簇不达 remote，无需登记）/ dashboard（**无需改**）。
新守卫 `tests/test_loop_guards_split.py`（**27 例**）+ 反探针 **27/27 全红**；纯搬对账 **12/12 逐字节**。

**★ 反探针的元教训**：首版两条变异「存活」= 探针锚点打偏（落在 fixture 走不到的分支上），不是守卫漏——
**「存活」先怀疑探针本身**。

**门禁**：nn **2539 → 2566 passed / 3 skipped**；ruff / mypy 绿（438 文件）；根 `bun run check` 2120 / 0；
`check-decisions` ok。

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

---

### 5.5 下一轮的设计（2026-09-25）—— 「批存储」接口（为拆 `rl/batch_eval.py` 做准备）

> 用户指令：「给 batch_eval.py 设计「批存储」接口，为拆那个 1785 行的文件做准备」。
> **本节只设计，不实施**；实施切在 §5.5.4 的 B1–B5，每步独立可验证可提交。
>
> **✅ 已先行落地（2026-09-25，独立一小刀）**：§5.5.2 里那个顺带修的**非原子落盘**已单独修完
> （§7 流程：先写确定性失败用例 → 红 → 最小改动 → 绿）——它不依赖 B2 的状态机改造，单独修更干净。
> 改动只有 `write_batches` 一个函数 + 一条用例；B2 的剩余范围见 §5.5.4。
>
> ⚠ **行数基准顺移**：下文所有 **1785** 都是**修前**的读数；该修复给 `write_batches` 加了 20 行
> docstring（说明为什么要原子发布）⇒ 修后 = **1805 行**，§5.5.4 的预计目标按同一起点顺移。
>
> **✅ B1 已落地（2026-09-25，第二十五刀）**：`rl/batch_eval.py` **1805 → 1616**，新模块
> `rl/batch_plan.py` **271 行**（把 13 函数 + 10 常量纯搬；旧家 21 条自别名再导出 ⇒ 调用点零改动）；
> 分层快照**未红**（新模块不达 remote）；唯一演进的守卫是 `test_dist_common_poll`（改成「按定义搜家」）。
> 全文 → `docs/nn/engineering.md` §23「第二十五刀」；决策 → `DECISIONS.md`
> §2026-09-25-goalnn-batch-plan-b1-split。
>
> **✅ B2 已落地（2026-09-25，第二十六刀）**：`rl/batch_eval.py` **1616 → 1190**，新模块
> `rl/batch_store.py` **680 行**（`BatchStore`：8 个具名转移 + 读面 3 + 请求面 4；`_tx` 取代
> `@_claim_locked`；`dirty` 才落盘；`_publish` 是全仓唯一台账写点）。两条★特例语义由守卫正面钉；
> **差分探针 54/54 步与旧实现等价**；反探针 22/22 全红（首轮 1 条存活 = 真空档，已补用例）。
> 全文 → `docs/nn/engineering.md` §23「第二十六刀」；决策 → `DECISIONS.md`
> §2026-09-25-goalnn-batch-store-b2-split。**下一步 = B3**（执行器纯搬 → `rl/batch_runner.py`）。

#### 5.5.1 为什么它「按链切」不动（先量后定的结论）

`rl/batch_eval.py` = **1785 行 / 35 个顶层函数 + 2 个类**。分区实测：

| 区段 | 行 | 行数 | 内容 |
|---|---|---|---|
| 头部常量 + 判据/门 | 55–150 | ~96 | `data_root` · `is_transient_error` · `node_gate_reason` · `kind_for_policy` · `utc_now_iso` · 窗口/背压常量 |
| 计划层（纯） | 168–337 | **152** | `load_ladder` · `plan_units` · `load_corpora` · `corpus_doc` · `plan_verdict_units` · `units_for_batch` · `batch_iter_id` |
| 跨进程锁 | 347–392 | 46 | `_claim_guard` / `_claim_locked`（复用 `train.loop_util`） |
| 台账读写 | 395–494 | **84** | `read_batches` / `write_batches` + 请求文件面（`REQUESTS_*` / `read_requests` / `read_done_req_ids` / `mark_requests_done` / 三个 key 函数） |
| 台账巨团 | 497–731 | **231** | `consume_requests`(185) · `claim_pending`(23) · `mark_unit_done`(23) |
| 执行器 | 734–1629 | **896** | `BatchEvalRunner`（`_run` 占 **821**） |
| 轮内接线 | 1632–1751 | 116 | `dispatch_batch_bg` · `maybe_dispatch_batch`(68) · `select_next_unit` |
| 台账尾部转移 | 1754–1785 | 31 | `_persist_of` · `_requeue` · `_reopen_for_resume` |

**巨团的真因不是调用图，是「台账没有所有者」**：有**八个**独立的 read-modify-write 点，每个自己决定四件事——

| 写点 | 改哪些字段 | 何时落盘 | 状态转移 |
|---|---|---|---|
| `consume_requests` | 建批 / `status→aborted` | `dirty` 才写 | pending→（新）/ aborted |
| `claim_pending` | `status→running` | 命中即写 | pending ∨ running-未完成 → running |
| `mark_unit_done` | `units.done` · `node_dist` · `status` | 命中即写 | → done / → pending / aborted 只回填 |
| `_persist_of` | `units.of` | **无条件**写 | 无 |
| `_requeue` | `status→pending` | **只在分支内**写 | running → pending（`aborted` 除外） |
| `_reopen_for_resume` | `status→pending` | **无条件**写 | running → pending |

这六种「何时落盘」策略与五个散落的 `status` 赋值，就是任何一条链都要横穿的东西 ⇒ **链切无解**（§5.2 已量过同一件事）。

#### 5.5.2 接口：`BatchStore`（新模块 `rl/batch_store.py`）

**契约一句话**：`<EVALBOARD_DATA>/batches.jsonl` 与两个请求文件**只有一个所有者**，
`status` / `units` / `node_dist` 的每一次变更都发生在一个**具名转移**里，且一次转移 = 一次事务 = 一次落盘。

```python
class BatchStore:
    """EvalBoard 批台账的唯一读写口（含跨进程锁与原子落盘）。"""

    def __init__(self, root: Path | None = None) -> None: ...   # 默认 data_root()

    # ── 写面：全部 = 一次事务（读 → 改 → 原子写）──
    def enqueue(self, **spec) -> dict | None: ...         # 幂等：同 key pending / 已物化 → None
    def enqueue_verdict(self, **spec) -> dict | None: ... # 键 = 语料 id + ckpt 标签序列
    def abort(self, batch_id: str) -> bool: ...
    def claim(self, *, consume: bool = True) -> dict | None: ...   # 最早可跑批 → running
    def set_units_of(self, batch_id: str, of: int) -> None: ...    # 只定型，不动 status
    def mark_unit_done(self, batch_id: str, unit_idx: int, node_dist: dict) -> None: ...
    def requeue(self, batch_id: str) -> None: ...                 # 认领后发现跑不了
    def reopen_for_resume(self, batch_id: str) -> None: ...        # yield/超时 → 保留 done

    # ── 读面（无副作用）──
    def get(self, batch_id: str) -> dict | None: ...
    def all(self) -> list[dict]: ...
    def done_units(self, batch_id: str) -> set[int]: ...

    # ── 请求面（console 单写 / runner 单消费）──
    def pending_requests(self) -> list[dict]: ...
    def mark_requests_done(self, ids: Sequence[str]) -> None: ...
    def consume_requests(self) -> dict: ...   # 请求翻译 → 调上面的具名转移
```

**状态机（唯一所有者）** —— 公开的 `status = {pending, running, done, aborted}`：

| 转移 | from | to | 判据（原文照搬，不许「统一」掉） |
|---|---|---|---|
| `enqueue*` | — | `pending` | 同 key 无 pending **且** 无 `created_ts >= req.ts` 的已物化批 |
| `claim` | `pending` ∨（`running` ∧ `len(done) < of`） | `running` | `of` 可为 0（未定型） |
| `mark_unit_done` | `running` / `pending` | `done`（`of>0 ∧ ndone>=of`）否则 `pending` | ★ `of == 0` ⇒ **不判 done** |
| `mark_unit_done` | `aborted` | `aborted` | ★ 只回填 `node_dist`，**不复活** |
| `requeue` | `running` | `pending` | ★ `aborted` **不改**（且此时不落盘） |
| `reopen_for_resume` | `running` | `pending` | `units.done` 保留 |
| `abort` | `pending` / `running` | `aborted` | 命中 `batch_id` |

两条 ★ 是本设计最容易在「集中化」时被抹掉的语义，守卫要**正面**钉（见 §5.5.5）。

**事务与锁**：`_tx()` 取代 `@_claim_locked` 装饰器 —— 跨进程仍用 `train.loop_util` 的 `claim.lock`
（**拒绝第三套锁**），进程内 `RLock` 可重入。收益有三：
① `claim()` 内部调 `consume_requests()` 不再需要 `_claim_held` 那条 thread-local 缝（同一 `with` 内嵌套）；
② 一次事务可以含多次字段修改，**只落盘一次**；③ **`dirty` 才落盘**统一六种策略（行为等价：
现在 `_persist_of` / `_reopen_for_resume` 在什么都没变时也整文件重写一遍）。

**★ 顺带修一个实测缺陷：非原子落盘。** `write_batches` 用 `Path.write_text`（**截断式**：`open('w')`
先截断再写字节），而 `batches.jsonl` 有一个**无锁的跨语言读者** —— console/TS 的
`dashboard/src/evalboard/batches.ts::loadBatches`（「runner 单写；console 只读」，**坏行静默跳过**）。
python 侧读者都在锁里，所以受害面就是这个 TS 读者；而它的 `enqueueBatch` 去重（「同 course+rung+ckpt
的 pending 批已存在则直接返回它」）**依赖读全** ⇒ 短读会导致**重复入队**。

探针 `nn-training/tmp/probe_batch_store.py`（读者逐字节照抄 `loadBatches` 的读法；写者连续重写 60 轮）：

| 台账规模 | 现状 `write_text` | 提案 `tmp + os.replace` |
|---|---|---|
| 12 批 / 2.7 KB | 短读 **126/622 = 20.3%** · 坏行 0 | **0/671** |
| 100 批 / 22 KB | 短读 **114/453 = 25.2%** · 坏行 0 | **0/513** |
| 2000 批 / 444 KB | 短读 **144/376 = 38.3%** · 坏行 12 | **0/398** |

（终次实测 log = `nn-training/tmp/probe-batch-store-final.log`。比例是探针紧循环下的量，逐轮会波动；
要读的是「窗口**存在**且在生产规模（十余批）也可见」+ 「`os.replace` 关得上」。）
**已修（2026-09-25）**：`os.replace(tmp, dst)`，临时名**固定**为 `batches.jsonl.tmp`（不随机）——
上一次崩溃残留的 `.tmp` 会被本次直接覆盖（不累积），故**不需要** `finally` 清理、也不需要改 `.gitignore`
（`dashboard/data/evalboard/*` 整目录已忽略；且 `store.ts` 的 `.jsonl` 后缀过滤不会把它当行文件）。
确定性复现用例 `tests/test_batch_eval.py::test_batch_ledger_publish_is_atomic`（不靠线程时序、不碰墙钟：
在 live 文件被以 `'w'` 打开的**那一刻**读一次台账）。
**同族未修（另开一刀）**：`dashboard/src/evalboard/batches.ts::rewriteBatches`（TS 侧 `claimPending` /
`updateBatch`）用同样的截断式 `writeFileSync` —— 它要动 `dashboard/**`、与 P1「触发端不得写台账」的守卫相邻，
值得自己一刀而不是搭在这里。

#### 5.5.3 它解锁的拆分（目标形态）

接口化之后，巨团里每个调用方只剩「说清自己要什么」，五段分区各自成立：

| 模块 | 内容 | 依据 |
|---|---|---|
| `rl/batch_plan.py` | 纯规划 + 判据/门（`plan_units` / `plan_verdict_units` / `units_for_batch` / `load_ladder` / `load_corpora` / `corpus_doc` / `corpora_path` / `select_next_unit` / `node_gate_reason` / `kind_for_policy` / `is_transient_error` + 桶常量） | **零 IO、零锁、零 root 依赖** ⇒ 最安全的一刀 |
| `rl/batch_store.py` | `BatchStore`（台账 + 请求面 + 锁 + 原子写 + `data_root` / `utc_now_iso`） | 唯一所有者 |
| `rl/batch_runner.py` | `BatchEvalRunner` + `dispatch_batch_bg` | **执行面**（网络/进程/权重），与台账所有权无关 |
| `rl/batch_eval.py`（门面） | 常量 + `maybe_dispatch_batch` + **逐个再导出**上面三家的公开名 | 保证既有 import 点一行不改 |

**为什么 `maybe_dispatch_batch` 留门面**：它的入边是轮内（`loop_lifecycle._evalboard_idle` / `loop_dispatch._evalboard_yield`
→ `self.` 调），出边是「认领 → 规划 → 定型 → 起线程」这条**编排**线；测试 `test_batch_eval.py:82` 还按**源码树**读它
（不按写死路径）⇒ 留门面是零迁移的选择。（与 S19「入边来自多个 sibling ⇒ 锁进组合根」同源的理由。）

#### 5.5.4 迁移批次（每步独立可验证、可提交、可回滚）

| 步 | 做什么 | 行为风险 | 预计 |
|---|---|---|---|
| **B1** | 纯规划 + 判据出包到 `rl/batch_plan.py`；`batch_eval` 再导出 | **零**（纯函数，无锁无 IO，逐字节对账） | 1785 → ~1630 · **✅ 已完成：1805 → 1616（`batch_plan.py` 271 行；逐字节对账 23/23 + 36/36，反探针 21/21，nn 2586）** |
| **B2** | 建 `rl/batch_store.py`：8 个写点 → 具名转移；`consume_requests` 拆成「请求翻译 + 三个具名转移」；`_persist_of` 消失（并入 `set_units_of`）；落盘策略统一成 **`dirty` 才落盘** | **中**（状态机集中 + 落盘策略统一）| ✅ **已完成：1616 → 1190（`batch_store.py` 680 行；差分探针 54/54 等价，反探针 22/22，nn 2608）** |
| **B3** | `BatchEvalRunner` + `dispatch_batch_bg` → `rl/batch_runner.py`（896 + 36 行，**纯搬**） | 零（逐字节对账） | ✅ **已完成：1190 → 223（`batch_runner.py` 1031 行；逐字节对账 8/8，反探针 17/17，nn 2621）** |
| **B4** | 门面收尾：`batch_eval.py` = 常量 + `maybe_dispatch_batch` + 再导出 | 零（只整理 + 一条行为等价删减） | ✅ **已完成：225 → 166 行**（散落的「已搬到 X」指路注释合并成 docstring 的一张表；删掉旧实现「就地改台账再落盘」的残留 `batch.setdefault("units", {})["of"]`；新守卫 `tests/test_batch_eval_facade.py` 11 例，反探针 14/14，nn 2632）。目标形态 B3 之后就已达成，本步只把「门面是门面」变成机器可判的契约 |
| B5 | **另开一轮**：`_run` 的 821 行按阶段切（通道机器 ~500 / 收尾 ~80 / 参与度账 ~60 / 单元开头 ~60） | —— | 不在本接口范围 |

行数是**分区实测的估**（落盘后以实测为准——S19/S20/S22 三次记账教训）。
B1 与 B3 是纯搬，可按 S21–S23 的成品流程走（逐字节对账 + 契约守卫 + 反探针）。
**B2 是本设计的主体**，也是全仓第一次「按状态所有者切」而不是「按链/按判据切」。

#### 5.5.5 守卫：要演进的 + 要新写的

**已知按路径读 python 源码的（搬家即失效，逐个改址）：**

| 文件 | 现在读什么 | 哪一步要改 |
|---|---|---|
| `nn-training/tests/test_batch_eval_wver.py:18` | `SRC = rl/batch_eval.py`（AST 钉 wver 实参） | B3（随执行器）|
| `nn-training/tests/test_eval_loot_fields.py:89` | 清单里的 `("rl/batch_eval.py", "eval_loot_fields")` | B3 |
| `nn-training/tests/test_dist_common_poll.py:250/252/276` | 读源码断言 `is_transient_error` 是**纯转发** | B1 |
| `nn-training/tests/test_eval_requests.py:147` | `from rl.batch_eval import _requeue`（私有名 unbound 调用） | B2（私有 seam 改到 store 上）|
| `dashboard/tests/evalboard-corpora.test.ts:345` | 「Python 与 TS 解析同一份 corpora.json」 | B1（核它的读法是否已按定义搜）|

`test_batch_eval.py`（838 行）已按**源码树**读（`:82` glob `loop_*.py` + `loop_core.py`）——S16 修过的形态，B1–B4 不必动。
`test_kick_once_paths.py:35` 断言 `rl/batch_eval.py` 存在 ⇒ 门面保留即继续成立。

**新守卫（B2 交付物）**：`tests/test_batch_store_txn.py`，至少这五条 **★ 功能性**：
① `mark_unit_done` 在 `of==0` 时**不判 done**；② `aborted` 批的在途 unit 只回填 `node_dist`、状态不变；
③ `requeue` 不复活 `aborted`；④ **改了必须落盘**（dirty-tracking 的反向用例）+ 没改**不落盘**（写次数计数）；
⑤ 落盘是**原子**的 —— ✅ 已提前落地：`tests/test_batch_eval.py::test_batch_ledger_publish_is_atomic`
（确定性：在 live 文件被以 `'w'` 打开的那一刻读台账，而不是靠并发时序），B2 只需把它扩到 store 的方法上。
另加契约守卫：`status` 的赋值点闭集 = store 的具名转移（AST 数 `Assign` target，同 S20 的 `_self_assigns` 教训）。

**新守卫（B4 交付物）**：`tests/test_batch_eval_facade.py` —— ① **门面契约**：模块级 `def` 闭集 =
{`maybe_dispatch_batch`} · 自别名再导出表**闭集**（双向）且对象级恒等 · 刻意**不**转发的名字
（执行器五常量 + `_heartbeat` · store 文件名常量 · 三个私有 seam）在门面上**响亮** AttributeError ·
门面**不改写** store 交回的台账 dict（AST：函数体里零下标赋值 + 三次变更都经 `store.<具名转移>`）。
② **`maybe_dispatch_batch` 的直接功能性**（此前只被轮内间接覆盖，`tests/` 无一条直接调用）：
认领→规划→定型→起线程主线 · 三条 requeue 路径 · 判决批权重取 unit 而非批次级 `rl_path`。

#### 5.5.6 明确不改的

- 三个文件的**字节格式**与**写者唯一性**（`batches.jsonl` runner 单写 · `requests.jsonl` console 单写 append-only ·
  `requests.done.jsonl` runner 单写 append-only）—— `dashboard/data/evalboard/README.md` 就是这份契约。
- 锁实现仍复用 `train.loop_util`（**拒绝第三套锁实现**，plan P4-W4）。
- `consume_requests` 的「任何失败只记日志、**绝不抛出**」（训练主链零风险）。
- `EVALBOARD_DATA` / `data_root()` 口径（`rl/eval_heartbeat` 同口径依赖它）。
- python↔TS 的字面契约：`utc_now_iso` 的 UTC+毫秒+Z 格式（`enqueueCovered` 用**字符串比较**判「已物化」）、
  `batch_id` 生成式、`units.of=0=未定型`、`BATCH_STAGE_BASE`/`EVAL_SEED0`/`SEGMENT_LEN`/`REGRESSION_EVERY` 等双侧镜像值。
- **公开名全部经 `rl/batch_eval` 再导出** ⇒ `eval_m1_once.py` / `eval_course_once.py` / `dashboard/src/evalboard/kick-once.py` /
  既有测试**一行不改**（私有名 `_requeue` / `_persist_of` 不转发：它们是私有 seam，测试应当改到 store 上）。

#### 5.5.7 主要风险

1. **「统一」时抹掉 aborted 的两条特例**（§5.5.2 两条 ★）—— 这是本设计唯一会改变**行为**的地方，
   守卫必须正面钉，不能只靠「搬得对」。
2. **dirty-tracking 写错 ⇒ 漏落盘**（进度看起来正常、重启后少一批）。⇒ §5.5.5 ④。
3. **跨语言读者的新假设**：原子写让「读者永远读到完整快照」成立，但**锁不住** TS 侧 ——
   本接口只承诺**落盘原子**，不承诺 console 读到最新（那是下一次轮询的事）。

**更早已记录：同一件事的相反先例。** §5.3.22（第二十三刀）的刀口是「多 sink 的 DAG ⇒ 提供者留根」，
本设计是「共享可变状态的 DAG ⇒ 把状态收进一个所有者」。前者按**调用**分家，后者按**所有权**分家——
两种刀法都不动行为，但后者顺带能修掉一类真缺陷（非原子落盘就是第一个）。

**✅ B2 落实结果（2026-09-25，第二十六刀）——三条风险逐条对账**：

1. **两条 ★ 特例语义被抹掉** ⇒ 守卫**正面钉**（`test_mark_unit_done_does_not_finalize_when_of_is_unset` /
   `test_aborted_batch_only_backfills_node_dist` / `test_requeue_never_revives_aborted`），且差分探针
   （同一串 54 步操作打在新旧两份实现上）**逐字节等价** ⇒ 特例未被「统一」掉，是实测而非声明。
2. **dirty-tracking 写错 ⇒ 漏落盘** ⇒ `test_transitions_publish_only_when_something_changed` 两个方向都钉：
   数 `_publish` 调用次数（「改了必落盘」与「没改不落盘」各自的反向用例），并**每次都从盘上重读**核对。
3. **跨语言读者的新假设** ⇒ 未变：只承诺**落盘原子**（`_publish` = 唯一写点 = `tmp + os.replace`），
   不承诺 console 读到最新。

**★ 设计里没写、实施时才浮出的一条**（已回写 §5.5.2 的实现）：锁若只放在**单个具名转移**上，
`consume_requests` 会分不清「锁忙」与「去重跳过」两种 `None` ⇒ 把请求标成已消费却没建批（丢请求）。
因此 `consume_requests` 自己也拿一把 `_tx`（整轮一把锁、内层转移可重入）。
