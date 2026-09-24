# rollout / eval 运行时优化 — 技术档案

> native 内核 / 并发口径 / 派发 / 单局看门狗。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---
## §22 A 方案落地：节点侧 rollout **与**云机离线 eval 接入长驻池（`remote/serve_pool.py`）—— 1.45–1.47× / 1.19–1.39×，产物逐位不变（2026-09-23）

> 起因：§21 追证出「离线（云机自主段）三条腿全是逐局 spawn」；用户拍板走 **A 方案**（把 `--serve`
> 长驻协议接进节点侧执行器），随后点名「云机离线 eval 也要池化」。本条 = 落地记录：改了什么、
> 实测多少、踩到哪几个坑。
>
> **两条腿都吃到了**：rollout（kind=iter + kind=run）**1.45–1.47×**（§22.2）、云机离线 eval
> **1.19–1.39×**（随每 lane 局数，§22.5）；两边的产物对拍都是**逐位/逐字段相同**。

### 22.1 改了什么

| 位置 | 内容 |
|---|---|
| **新增 `nn-training/remote/serve_pool.py`** | 节点侧池：协议与 `tools/sim/serve-loop.ts` **逐字对齐**（`__SERVE_READY__`/`__SERVE_OK__`/`__SERVE_ERR__`，stdin 一行 = 一局 argv 的 JSON **数组且不含入口路径**） |
| `remote/iter_rollout.py` | **rollout 腿**：`run_iter_rollout` 建池（每 job 一个）→ `_run_one_game_with_retries` **先试池**、任何不确定立刻回退原本的逐局 `Popen`；轮末打一行 `serve_pool:` 汇总 |
| `remote/offline_eval.py` + `rl/eval_local.py` | **云机离线 eval 腿**（同一份池，另一种消费方式）：`run_cloud_eval` 每轮建池 → 交给每局的 `run_local_eval_game` → 池里跑成就用它的输出行拼一个 `CompletedProcess`（该腿只把 stdout 当**失败尾巴**用，不需要逐局日志文件）；轮末 `finally` 关池 |
| 开关 / 白名单 | `NN_SERVE_POOL=0` 整轮退回旧行为；`SERVE_CAPABLE_SCRIPTS`（= `sampler-agent.ts::PERSIST_SERVE_ENTRIES` 的节点侧镜像）外的脚本**连池都不建** |
| wire 形状 | **零改动**：池的计数只进节点本地返回字典（`out["serve_pool"]`），不进 `report` ⇒ hub 侧不需要任何改动、不可能因此拒收 job |
| 复用粒度 | rollout = **一个 job = 一个权重版本**；eval = **一轮评估**（两个 `finally` 都关池）。权重在池里**不需要作 key**：每局的 `--weights` 在 `main(argv)` 里逐局重读 ⇒ agent 侧那套 key（含 wver）匹配在这里天然不需要，也没有「槽位重建」 |
| 诊断口径 | worker 的 stdout 是混流（所有局的日志挤一条），池**按任务收集、落回该局自己的 `w{i}/rollout.log`** —— 看门狗的超时行与 `_first_rollout_log_tail` 的诊断口径一字不变 |

回退路径共 6 种（`no-slot` / `write-failed` / `no-stdin` / `err` / `dead` / `timeout`），**两条腿全部照此退回自己的
一次性 `Popen`**：池只可能让这一局更快，**不可能把它变失败**（「只慢不错、绝不丢局」）。协议只有一个入口
`_submit`，rollout 用 `try_pool`（顺手把该局 stdout 落回 `w{i}/rollout.log`）、eval 用 `try_capture`（行交回调用方）。

### 22.2 实测（win32-x64 · Ryzen 7 5800H 16 线程 · 真 bun + 真导出器 + 真权重）

口径：`stage 0`、`seeds 0..N-1`、`max-ticks 12900`、`hard`、`lives=1`、**workers=8**、
`ts_dir` = 仓根、权重 = `bc-c4-v3.it1`；每档 **spawn / pool 交替跑两轮**（`bun` 臂 = `NN_SERVE_POOL=0`）。

| 局数 | 每 lane 局数 | spawn（两轮） | pool（两轮） | 倍率 |
|---|---|---|---|---|
| 8 | 1 | 1.054 / 1.043 s | 0.923 / 0.951 s | 1.10–1.14× |
| 16 | 2 | 2.111 / 2.119 s | 1.734 / 1.762 s | 1.20–1.22× |
| 32 | 4 | 4.174 / 4.046 s | 2.839 / 2.845 s | 1.42–1.47× |
| **328（生产形态）** | **41** | **37.791 / 38.209 s** | **25.644 / 26.298 s** | **1.45–1.47×** |

* **复跑离散度（要引用就得带这个）**：同一份入库台架（`tools/perf/bench-iter-pool.py`）
  在门禁刚跑完（机器有背景负载）时全套重跑一遍：8 局 **1.02–1.03×** · 16 局 **1.19–1.25×** ·
  32 局 **1.36–1.37×** ⇒ 4 局/lane 的比值实际在 **1.36–1.47×** 之间漂，1 局/lane 的“+10%”落回 ≈1.0 ——
  即**无复用时的差值是噪声**，有复用后的增益真实且稳定。
* **逐位中性**：四个档的两臂 `data_fp` 完全相同（`f777d15b…` / `44e88f13…` / `624bafc2…` / `594331a0…`），
  `totalTicks` 也全等（12686 / 27153 / 58328 / 553437）⇒ 池不改语义，纯提速。
* pool 臂的 `spawned` 恒为 **8**（=workers）、`served` = 局数、`fallback=0`、`killed=0`。
* **规律**：收益 ∝ 每 lane 局数 —— 每 lane 只省一次「冷启动 + 每局重启」，所以 1 局/lane 时两臂工作量相同
  （≈1.0–1.14×，噪声）、2 局/lane 起单调拉开（1.19–1.25×）、4 局/lane 约 1.4×。生产一轮是 41 局/lane ⇒ 吃满。
* 与 §20 的 1.59× 不矛盾：那条是**单局更长**（满 12900 tick）的训练机口径；本条这些局平均只 ~1700 tick，
  单局越短、冷启动占比越高，池的**绝对**收益越大但两臂比值反而被「spawn 臂的固定成本本来就少」压住。

### 22.3 实测抓到的两个坑（都已修，都写进代码注释）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| ① | 真 bun 端到端跑通、但 `served=0 / fallback=1(err)` —— **池白建**，全靠回退撑住 | worker 的 cwd 是 **TS 代码根**，而 `--out`/`--weights` 是**相对 job 目录**的；池直接送原始 argv ⇒ worker 拿着错的权重路径报错 | 池送 `_exec_argv(argv, job_dir)`（与一次性路径**同一条**绝化规则）。抓到它的是 `test_remote_iter_real_bun` 新加的 `served==1` 断言 —— **协议漂移的哨兵**（池失败只回落不报错，没这条断言就永远发现不了） |
| ② | 8 局的轮上池**反而慢**（0.79–0.86×） | `start()` 串行等 8 个 worker 就绪 ⇒ 8 次冷启动排成一行；而逐局 spawn 臂的冷启动是彼此并发的 | `start()` 改成**并发起**（先全部 `Popen`、再统一等就绪，就绪上限整批共用）⇒ 8 局轮变 1.10× |

**教训**：冷启动是可并行的资源，**永远不要串行化它**；以及「池接上了」本身需要一条断言，
否则性能特性会静默消失（回落路径太快、太安静）。

### 22.4 测试

* 新增 `nn-training/tests/test_remote_serve_pool.py`（**23 用例**，池本体）：复用（`spawned` 不随局数涨 + 日志 pid 一致）/ 日志隔离（每局日志只含自己）/ `try_capture`（行交回、**不落盘**）/ 三类回退（ERR、进程死掉、硬顶超时）/ 白名单与开关（两条腿共用 `make_pool`）/ 同名脚本拒绝 / 端到端接线（同一批 shard + 报告 + 计数诚实）/ 开关退回旧行为 / 混脚本不建池。
* 新增 `nn-training/tests/test_offline_eval_pool.py`（**8 用例**，eval 腿）：接线（一轮一池、每局拿到同一个池、`min(slots, 局数)` 上限、轮末关池且异常路径不漏关、池起不来则整轮一次性）+ 执行面（池优先时**绝不** spawn、池拒绝则回退且返回值形状不变、不传池时行为与加池前相同、`wver` 口径不变）。
* `tests/test_remote_iter_real_bun.py` 补 `served == 1`（全仓唯一「真 bun + 真导出器」走池的路径）、`tests/test_eval_local_capture.py` 补「池优先于一次性」的源码顺序断言。
* `tests/test_offline_eval_cloud.py` 加 autouse fixture 关池（那一层的假执行器让池无意义；接线由上面那个新文件验）。
* 门禁：`bash tools/githook/nn-python-gate.sh` 绿（ruff + mypy + **2217 passed**）。

### 22.5 云机离线 eval 腿（同日落地，用户点名）

**接法**：§21 列出的三个差异点各自有了归属，没有一个是「照抄」能过的：

| 差异点 | 处理 |
|---|---|
| ① 那条路走 `run_eval_runner_capture`（**捕获** stdout/stderr） | 池新增 `try_capture`：成功把行**交回调用方**（不落盘），`run_local_eval_game` 用它拼一个 `CompletedProcess(cmd, 0, "\n".join(lines), "")` ⇒ 下游（`rc != 0` 判定、失败尾巴）一字不改 |
| ② 权重快照每轮变 | **不需要 key**：`--weights` 在导出器的 `main(argv)` 里逐局重读（池本就是「一个任务一局」）。池的生命周期 = **一轮评估**（`run_cloud_eval` 建/关），换轮自然换权重 |
| ③ 重试在外层（`offline_eval` 的 attempt 循环） | 池只保证「**要么服务、要么立刻回退**」，失败语义全归调用方：池回退后 `run_local_eval_game` 当场走一次性 `run_eval_runner_capture`（硬顶/告警口径同一份 `game_watch`，`kind="eval"`）⇒ 与池不存在时逐字相同 |

**实测**（win32-x64 / 真 bun + 真导出器 + 真权重 / workers=12 / max-ticks 12900 / 3 关 × seeds）：

| 局数 | 每 lane | spawn（两轮） | pool（两轮） | 倍率 | 产物 |
|---|---|---|---|---|---|
| 24 | 2 | 4.115 / 4.199 s | 3.459 / 3.300 s | **1.19–1.27×** | 逐字段相同 |
| 48 | 4 | 8.325 / 8.462 s | 6.061 / 6.098 s | **1.37–1.39×** | 逐字段相同 |

「逐字段相同」= 逐局 `_eval_report.json` 全部字段（`elapsedSec` 除外）+ `ticks` 两臂完全一致
（每臂 `served` = 局数、`spawned` = workers、`fallback=0`）。**台架**：`tools/perf/bench-eval-pool.py`。

**密度决定收益**（与 rollout 同律）：云机这一腿的并发面很宽（`cpu_worker_slots()`：8 核→6、
16 核→12、96 vCPU 的 Kaggle TPU 会话→92），而一轮 A 层语料是 `关数 × eval_games_per_stage`
（典型 100 局）⇒ 96 vCPU 会话上约 1 局/lane，**基本持平（≈1.0）**；本地/中等节点 8 局/lane 则 1.19–1.39×。
两个因此必须做的保守化：

* **池上限取 `min(slots, 本轮局数)`**（否则 92 slots + 8 局的轮会为 8 局预热 92 个进程）；
* 池**每轮建、轮末关**（不把常驻进程留到段末）——下一轮冷启动一轮只付一次，且 `close()` 幂等（
  内层 `finally` 已关过就是空操作），`run_cloud_eval` 的多条 `return out` 出口都不会漏。

**第三个坑（真机实测才暴露）**：`run_local_eval_game` 的 `cmd[0]` 是 **bun**，而池的任务口径与
iter 腿一致（argv 以**导出器脚本**开头）⇒ 送 `cmd[1:]`。送错的症状是最难查的那种：
池静默拒绝（`not-ours`）、一局都没服务、两臂看起来一样快（首轮实测 1.000×，`served=0`）。
⇒ 再一次印证：**「池真接上了」需要断言**（`tests/test_offline_eval_pool.py` 钉 `try_capture` 的入参，
`tests/test_remote_iter_real_bun.py` 钉真 bun 路径的 `served==1`）。

### 22.6 复跑

```bash
# rollout 腿 A/B（真 bun + 真导出器；交替两轮，逐档给倍率 + data_fp 对拍）
bash tools/githook/nn-py-safe.sh tools/perf/bench-iter-pool.py        # 默认 8,16,32
NN_BENCH_GAMES=328 bash tools/githook/nn-py-safe.sh tools/perf/bench-iter-pool.py
# eval 腿 A/B（同一台机器同一套纪律；EV_BENCH_* 调口径）
bash tools/githook/nn-py-safe.sh tools/perf/bench-eval-pool.py        # 3 关 × 8 种子
EV_BENCH_SEEDS=0,1,...,15 bash tools/githook/nn-py-safe.sh tools/perf/bench-eval-pool.py
# 关池（两条腿一起回到旧行为，随时可回退）
NN_SERVE_POOL=0 <原来那条命令>
# 单测（池本体 + 两条腿的接线）
bash tools/githook/nn-py-safe.sh -m pytest nn-training/tests/test_remote_serve_pool.py \
  nn-training/tests/test_offline_eval_pool.py -q
```

---
## §21 离线模式（云机自主段）**没有**长驻池：rollout/eval 都是逐局新建进程（2026-09-23 代码追证）

> **后续（§22）**：本条的 **(A) 方案已落地，且 rollout 与 eval 两条腿都入池** ——
> rollout（kind=iter + kind=run）实测 **1.45–1.47×**（§22.2）、云机离线 eval **1.19–1.39×**（§22.5）；
> 本表三行的「❌」自本条起作废。

> 起因：§20 量到「池 vs 逐局直开 = 1.59×」，那**离线（云机）模式**吃到这笔了吗？——**没有**。
> 三条腿全部逐局 spawn，长驻池只存在于 sampler-agent 的 `/v1/task` 路径上。

**证据链**（每个入口都读过实现）：

| 腿 | 入口 | 执行方式 | 池？ |
|---|---|---|---|
| 逐轮上云（kind=iter） | `remote/worker.py:2236` | `remote/iter_rollout.py::run_iter_rollout` → **逐局 `subprocess.Popen([bun, export-rl-rollout.ts, …])`**（线程池控并发，:202） | ❌ |
| 半离线整段 / 全离线包（kind=run） | `remote/plan_run.py`（模块 docstring：每轮合一个与 kind=iter **逐字段同构**的 job 再喂回 `run_job`；CLI/入口在 `remote/run_loop.py`） | 同上（走的就是同一条 iter 路径） | ❌ |
| 云机离线 eval | `remote/offline_eval.py` → `rl/eval_local.py::run_local_eval_game` | 逐局 Popen `bun export-eval-game.ts`（:596–630 组 cmd + `game_watch`） | ❌ |

* **池只存在于 `tools/agent/sampler-agent.ts`**（`persistPool` + `PERSIST_SERVE_ENTRIES` 里的 `--serve` 常驻进程），
  而它只在 **hub/agent 的 `/v1/task` 认领路径**（`dist_common.fetch_task`）上被用到。离线包/半离线段
  **不启动 agent**（`nn-training/**` 里 `sampler-agent` 只出现在注释里）⇒ 那条路永远拿不到池。
* 池的**复用粒度 = 一次权重版本内**：`persistPool` 按 `key`（含 wver/argv）匹配，换 wver 后旧槽位被丢弃
  （`sampler-agent.ts:828–831`）⇒ 每轮（新 wver）每槽重起一次、轮内所有局复用 —— §20 的 1.59× 正是这个口径。
* **代价（实测口径）**：rollout 腿 = §20 的 **1.59×**（PC 19.60 → 12.12 s / 100 局 8 并发；手机 2165 → 3435 局/h 同值）。
  对**整轮**的影响 = 由 rollout 占轮时比例决定，**不是** 1.59× 全量。
* **要吃到这条路只有两种改法（都要立项，不是顺手改）**：
  - **(A) 把 `--serve` 长驻协议接进 `iter_rollout.py`**：TS 侧已就绪（`tools/sim/serve-loop.ts`；rl/eval/goal/intent
    四个导出器都支持，agent 池用的就是它），改动集中在 Python 侧的进程管理与**权重切换时的槽位重建**；
  - **(B) 离线包里起本地 sampler-agent**，让 rollout/eval 走同一 `/v1/task` 协议（复用面最广，但要在 plan 里
    重新论证离线包的「自包含」边界：agent 是本地进程，协议不出网）。

---
## §20 本机（self）100 局三口径对照：**长驻池 vs 每局直开 = 1.59×**；池的价值 = 免去每局 spawn（2026-09-23）

> 问题：同一台机器、同样 8 并发、同样 100 局，**经过 sampler-agent（长驻池）** 与 **直接开导出器** 各是多少？
> 结论：**agent 11.88–12.35 s vs 每局直开 19.60 s ⇒ 1.59×**，而 vs 「批化直开」只差 ~5%
> ⇒ **池的收益全部来自「不再每局新建进程」**。

**口径**（三者完全相同：同权重、`stage 0`、`seeds 0-99`、`max-ticks 12900`、`hard`、`lives=1`、并发 8、PC = Ryzen 7 5800H 16 线程；
每个口径都校验了 `_rl_report.json` 里 `games` 求和 = 100、`totalTicks` 合计 = **272675**）：

| 口径 | 100 局墙钟 | 吞吐 | 每局 ms（mean / p50 / p90 / max） |
|---|---|---|---|
| **A 经 agent（长驻池，`tools/perf/agent-bench.ts`）** | **11.88 / 12.35 s**（两轮） | **30306 / 29139 局/h** | 913 / **866** / 1562 / 1871（rep2） |
| B2 直开 · 每进程连续 12-13 局（批化，**仅诊断口径**） | 12.60 s | 28508 局/h | 846 / 856 / 925 / 925 |
| B1 直开 · **每局一个进程**（池前训练栈口径） | **19.60 s** | 18343 局/h | 1304 / 1277 / 1938 / 2193 |

* **A ÷ B1 = 1.59×**（19.60 / 12.12 平均）；**A ÷ B2 = 1.05×**。
* 分解：B1 每局比 A 多花 **~0.41 s**（p50 1277 → 866 ms）= bun 启动 + 模块加载 + 首用 attestation×3 + 权重加载
  —— 在 8 条 lane 上就是 100 局 × 0.41 s ÷ 8 = **5.1 s**，正好是 19.60 − 12.12 的差。
* ⚠ **B2 不是可部署选项**：hub 用 `rl/cmd.build_rollout_cmd` **逐局一条 argv**（`remote/iter_rollout.py` 也是），
  批化只是用来把「池」与「批化」两个效应分开。两者都能干掉 per-game spawn ⇒ **池的独立价值很小（~5%）**，
  真正值钱的是「别每局新起进程」（~1.6×）。
* 手机侧同口径（均为 8 并发、100 局）：**逐局直开 13.30 s / 8 局 = 2165 局/h**（§18 并发实验）
  对**池 104.80 s / 100 局 = 3435 局/h** ⇒ **1.59×**，与 PC 同值。
  ⇒ **arm64/小设备节点不要跑「每局 spawn」的路径**（如 `remote/iter_rollout.py` 那种逐局 spawn）：
  它把启动成本按局重付，而 §19 的 hub/agent 路径没这一项。
* ⚠ **本节曾写过一条错的手机推论**（「池在手机上 ≈2.1–2.3×」）：那是拿「8 进程各跑 1 局」的单局延迟
  （12.2–13.1 s，含 8 个启动同时抢 CPU）去比池的**稳态** p50（5.77 s）—— 两种结构不可比。
  正确口径按时长除局数（已改成上面的 2165 → 3435 局/h）。
* 新工具：`tools/perf/direct-bench.sh <spawn|batch> <并发> <局数> <标签>`（直开口径，自带产物校验）。
* **踩过的坑（写进工具纪律）**：第一版脚本 `cd "$(dirname "$0")/.."` 少退一层（脚本在 `tools/perf/` 下）
  ⇒ cwd 变成 `tools/`，100 次启动全部因找不到入口而秒退，却打出了「**8.6 s / 41643 局/h**」的漂亮数字。
  ⇒ 长任务的度量脚本**必须校验产物**（本脚本：report 里 `games` 求和 ≠ 期望值 或 有非零退出 ⇒ 响亮失败退出），
  否则「快速失败」会伪装成「性能突破」。

---
## §19 真实训练路径下的节点吞吐实测（sampler-agent 100 局 · PC 驱动）：**8.2–8.9×**，修正 §18 的「4–5×」（2026-09-23）

> §18 量的是**单进程直跑**（4.2×）—— 只覆盖了 ISA 那一层；本节把**机器宽度与并发**也加进来
> （真实 HTTP 协议 + 常驻池 + 各自 worker 数），得到训练栈真正会看到的数字：**8.5×**。
> 两节都对，量的不是同一件事 —— **引用节点产能请用本节**。

**方法**：手机上拉真 agent（`bun tools/agent/sampler-agent.ts --port 8443 --workers N`，proot 会话内常驻）；
PC 侧新驱动 **`tools/perf/agent-bench.ts`** 走训练栈同一协议：
`POST /v1/weights`（gzip 体 + `x-weights-sha256` = 未压缩 sha）→ 并发
`GET /v1/task?iterId&wver&stage&seed&maxTicks&difficulty&livesOverride`（sync 直回整包；自动 strip 保活空格 + `unpackContainer` 校验）。
同一份权重（wver `46e6285876a2…`）、同一批 `(stage 0, seed 0-99)`、同 `max-ticks 12900`、同 `difficulty hard`。

| 机器 | workers | 100 局墙钟 | 吞吐 | 单局 mean / p50 / p90 / max |
|---|---|---|---|---|
| PC（Ryzen 7 5800H · 16 线程） | 8 | **12.35 s** | **29139 局/h** | 0.95 / 0.91 / 1.64 / 2.00 s |
| 手机（SD865 · 8 线程 big.LITTLE） | 8 | 104.80 s | 3435 局/h | 8.23 / 5.77 / 12.14 / 40.92 s |
| 手机 | **6** | **101.26 s** | **3555 局/h** ← 吞吐最优 | 5.93 / 4.34 / 9.67 / 30.13 s |
| 手机 | 4 | 109.96 s | 3274 局/h | 4.31 / 3.77 / 6.81 / 24.65 s |

* **吞吐比 8.2×（手机最优 6w 对 PC 8w）～ 8.5×（8w 对 8w）**；8w 对 8w 的单局延迟比 **8.7×**。
  ⇒ **用户的「一个数量级」在真实路径上成立**（§18 的 4.2× 只是 ISA 那一半）。
* **分解 = ISA 4×（§14/§15）× 机器宽度 ~2.1×**（16 线程全大核 vs 8 线程里只 1 个大核）。
* **并发档位（每档两轮 100 局，交替跑）**：

  | 手机 workers | 墙钟 rep1 / rep2 | 吞吐 rep1 / rep2 | p50 | p90 | max |
  |---|---|---|---|---|---|
  | 4 | 109.96 / 112.67 s | 3274 / **3195** 局/h | 3.77 / 3.79 s | 6.81 / 6.67 s | 24.7 / 26.1 s |
  | **6** | **101.26 / 103.30 s** | **3555 / 3485** 局/h | 4.34 / 4.45 s | 9.67 / 9.05 s | 30.1 / 38.0 s |
  | 8 | 104.80 / 113.87 s | 3435 / **3162** 局/h | 5.77 / 6.31 s | 12.14 / 13.37 s | 40.9 / 44.3 s |

  ⇒ 重复后**吞吐排序稳定：6 > 8 ≈ 4**（6 对 4 两轮都 +9%；6 对 8 为 +3.5% / +10%），
  而**延迟排序单调：4 < 6 < 8**（p90 6.7 / 9.1 / 13.4 s）；`rep` 内部离散：4 = 2.5% · 6 = 2.0% · **8 = 8.7%**
  （8 worker 的离散度也最大——小核被抢得最凶）。
* **结论（手机上该开几个 worker）**：**6** —— 吞吐最优（两轮一致）、且正好等于 python 侧
  `cpu_worker_slots(8) = 6`（`platform_utils.py` 无需改）；**8 是双输**（吞吐无优势、延迟与离散度最差）。
  只有**在乎单局延迟**（eval、以及 `remote/game_watch.py` 那个 5s 首轮硬顶，见下）时才降到 **4**
  （p90 6.7 s vs 9.1 s，代价 ≈9% 吞吐）。
* ⚠ **要核对的旋钮（本次未直接复现，但数摆着）**：`remote/game_watch.py` 的单局看门狗
  首次尝试硬顶 **5s**（重试放宽 ×4 = 20s，最多 3 次）——手机上 8w 的 **p50 已经是 5.77 s**
  ⇒ 走**节点侧逐局 spawn** 的路径（`remote/iter_rollout.py`，以及 eval 的 `--eval-game-timeout-sec`）时
  会大量「首轮被砍→重跑」；而 hub/agent 路径（本节测的）trainer 只是等 HTTP，不受该硬顶影响。
  建议：arm64/手机节点跑 iter/eval 时显式给 `--remote-iter-game-timeout`（给 ≥ 60s）并同步 eval 那一侧。
* **跨平台确定性**：四种配置（含两端）`总 tick` 全部 **272675**，`unpack 失败=0`、`503 重试=0`
  ⇒ HTTP 通道 + 常驻池不改语义，节点 shard 可被 PC 侧正常 unpack。
* **常驻池确实生效**：agent 日志 `[sampler-agent] persist worker spawned (export-rl-rollout.ts)` ×N
  （每槽惰性拉起、之后复用）⇒ §4.6 的固定成本收益在这条路径上真的拿到了。
* **顺带实证 §17**：手机 codeHash `def22c1d…`（提交 `3c16255`）vs PC `7d3704…`（同一提交 + 本批把
  `conv-optimize.plan.md` 移出 `src/nn/`）⇒ 「`src/nn/` 下**增删任何文件**（含 `.md`）都改 codeHash」。

**复跑**：`tools/perf/phone-agent.sh`（手机侧起停，需在长驻 proot 会话内——`proot --kill-on-exit` ⇒ 会话结束即杀）
+ `tools/perf/agent-bench.ts`（PC 侧驱动，参数见文件头）；手机侧记得
`bash /tmp/phone-agent.sh start 6 8443`，PC 侧 `--url http://<手机IP>:8443 --key $(cat agent.auth)`。

---
## §18 Android(arm64 · big.LITTLE) 上的 rollout 究竟慢多少：**实测量 4–5×**，不是 10×（adb 直驱分解，2026-09-23）

> 起因：用户报告「Android 上 rollout 一局比 self/mac 慢一个数量级」。用 **adb 直驱**（Redmi K30 Pro ·
> SD865 = 4×1.8G 小核 + 1×2.4G 大核 · Android + proot/Ubuntu · bun 1.4.2）复现并逐层分解。
> **结论先行**：同一命令 / 同一权重 / 同一 (stage, seed) 下，手机只是 PC（Ryzen 7 5800H · 16 线程）的
> **4.2×**（单局）与 **4.8×**（8 并发摊薄吞吐）—— **native 路径上不存在 10× 异常**，
> 且无热降频、无钉核收益、无 proot 现象级开销。能凑出「一个数量级」的只有两种**不同口径的比法**（见下）。
>
> ⚠ **口径修正（同日紧接补测）**：本节是**单进程直跑**口径，只含 **ISA** 那一层。真实训练路径
> （HTTP + 常驻池 + **各自 worker 数**）实测是 **8.2–8.9×** ⇒ 见 **§19**（含 4/6/8 worker 的吞吐与延迟表）。
> **引用节点产能用 §19**；本节用来回答「慢在哪一段」。

### 复现（与 PC 逐字相同的命令；两边的 `[OK]` 行逐字段相同 ⇒ 工作量相同）

```bash
bun tools/sim/export-rl-rollout.ts --weights <x20-noexplore.it181> --out /tmp/ro-ph \
  --stages 0 --seeds 0-3 --difficulty hard --lives-override 1 --max-ticks 12900
```

| | 4 局批（14318 ticks / 1433 决策） | 扣启动后每决策 |
|---|---|---|
| PC（win32-x64 · 16 线程） | **2.615 s** | **1.68 ms** |
| 手机（8 线程 big.LITTLE） | **10.83–11.30 s** | **7.06 ms** |
| 比值 | **4.2×** | **4.2×** |

* 进程启动（`--max-ticks 1`）：PC **0.203 s** / 手机 **0.715 s**（bun 启动 + 首用 attestation×3 + proot）。
* 每决策 **7.06 ms（手机） vs 内核单独 6.4 ms（§14）** ⇒ 内核占 91%，与 x64 侧的 95% 同构
  ⇒ **慢的全部是内核**，不是 JS/编码/仿真/GC。
* 反证三条：**热降频**（3 连跑 10.96 / 10.82 / 11.01 s，平的）· **钉大核**（`taskset -c 7` 11.10 s，无改善）·
  **调度迁移**（10.8–11.3 s 区间稳定）。

### 三条后端链在两侧的真实代价（1 局 · max-ticks 2000 · 200 决策 · 含启动）

| 后端 | PC | 手机 | 手机/PC | 每决策（扣启动）PC → 手机 |
|---|---|---|---|---|
| **native** | 0.614 s | 2.350 s | **3.8×** | 2.06 → 8.18 ms |
| wasm | 0.919 s | 2.438 s | 2.7× | 3.58 → 8.62 ms |
| **ts（兼底）** | 9.557 s | **23.795 s** | 2.5× | 46.8 → **115.4 ms** |

* ⚠ **手机上的 native 只比 wasm 快 ~5%**（1.64 s vs 1.72 s 工作量），而 **TS 兼底比 native 慢 10.1×**。
  ⇒ 「手机慢一个数量级」这个读数**只可能来自 TS 兼底**（或下面说的口径混比），**不可能是 native 路径**。
* **第一诊断动作**：看 shard manifest 的 `"feat"`（或日志里 `[conv-wasm] 加载失败，回退 TS 特征路径`）。
  `ts` ⇒ 10× 悬崖（产物没到位）· `wasm` ⇒ 只 +5%，可接受 · `native` ⇒ **无异常，慢的是 ISA**。

### 并发：big.LITTLE 的代价在「单局延迟」，不在吞吐（8 线程手机 vs 16 线程 PC）

| 并发 N（各 1 局 · seed0） | PC 每 worker 墙钟 | 手机 每 worker 墙钟 | PC 摊薄 ms/局 | 手机 摊薄 ms/局 |
|---|---|---|---|---|
| 1 | 1.08 s | 4.31 s | 1289 | 4548 |
| 4 | 1.38–1.46 s | 6.05–7.57 s | **435** | 1938 |
| 8 | 2.19–2.42 s | **12.2–13.1 s** | **348** | **1662** |

* 手机 8 并发时**单局延迟 3.0×**（4.31 → 12.9 s）——8 个进程里有 4 个落在 1.8 GHz 小核上；
  但**吞吐仍在涨**（1662 ms/局 @8 vs 4548 @1）⇒ 现行口径 `max(cores−4, cores×0.8)`（8 核 → 6 worker）
  对**吞吐**是对的，只是把**单局延迟**放大了。
* ⇒ **「一局慢一个数量级」极可能是两种口径混比**：手机在**自有 worker 数**下的单局延迟（~12.9 s）
  对比 mac/PC **闲置时单局**（~1.1 s）= **11.9×**。**同口径比是 4–5×**；吞吐口径（同并发摊薄）**4.8×**。

### 结论与可行动项

1. **arm64 节点的慢是 ISA 决定的**（§14 / §15：FP-op 受限 · 内核 3.9×），**不是 Android / proot / bun 的锅**。
2. **排课按 4–5× 折算**（吞吐），不按 10×；也**不要**把 x64 的内核提速算到 arm64 头上（§14）。
3. **部署体检先看 `feat`**：若是 `ts`，问题不是「手机慢」而是**产物没到位**（10× 悬崖）
   —— 先修分发再加机器；`wasm` 只 +5%。
4. **arm64 上 native 只比 wasm 快 ~5%** ⇒ 想简化 arm64 产物分发（省掉 `.so` + attestation）时，
   纯 wasm 是可接受的降级；**x64 则不行**（内核口径 native 比 wasm 快 2.2×，实测口径 1.7×）。

**adb 直驱的三个招式**（本次用到的，可复用）：`export MSYS_NO_PATHCONV=1` → 载荷推 `/data/local/tmp`
（Termux uid 读不到 `/sdcard`）→ `adb shell "run-as com.termux sh -c 'cp … <rootfs>/tmp/'"` 到 rootfs →
`run-as com.termux sh <run.sh>`，run.sh 里 `PATH=$PREFIX/bin:/system/bin proot-distro login ubuntu -- bash -lc "…"`
（`proot-distro` 需要显式 PATH，否则谎报「proot utility does not exist」）。这四个封装好的脚本已入
`tools/perf/phone-{run,roll2,par,eng}.sh`（`tools/perf/**` 不在 codehash 集里，不会惊动节点）：
`phone-run.sh "<容器内命令>"` · `phone-roll2.sh <标签> <max-ticks> <taskset 前缀>` ·
`phone-par.sh <N> <每进程局数> <标签>` · `phone-eng.sh <标签> <native|wasm|ts>`。

---
## §17 计划文档移出 codehash 集：`src/nn/conv/conv-optimize.plan.md` → `plan/conv-optimize.plan.md`（2026-09-23）

> **教训（值得下个 agent 记住）**：`tools/agent/codehash-files.txt` 收了**目录** `src/nn/`，而 F3 噪声过滤
> 只排除隐藏项 / `__pycache__` / `node_modules` / `.pyc .orig .rej .bak .tmp .log .swp` / `~` 结尾
> —— **`.md` 不在排除之列** ⇒ 放进 `src/nn/**` 的**任何文档改动都会改 codeHash ⇒ 触发一次节点升级波**
>（`docs/nn.progress.md` 记的「reorg 那次有意不碰它」就是这个原因）。实施计划 / 调研 / 台架属于
> `plan/**` · `docs/**` · `tools/perf/**`（都不在 codehash 集里）。

* 动作：文件系统 `mv`（**不是** `git mv`，`AGENTS §0.1` #2）⇒ `plan/conv-optimize.plan.md`；
  正文一字未动，只改了它自述的现址与顶部状态块里那条「旧号 §140–§142」的指向。
* 顺带解掉一处旧号例外：该文档内部的 `docs/nn.progress.md §140–§142` 现重写为 `docs/nn/runtime-opt.md` §9–§11
  ⇒ `docs/nn.progress.md`「旧号引用清理范围」第 3 条已标为**已解决**。
* 代价：从 codeHash 集里**移走**一个文件同样改一次哈希 ⇒ 本批仍会有**一次**升级波；此后改这份计划不再触发。
* 适用边界：`src/**` 只放**运行时语义**的东西（内核源码、适配器、分发矩阵）；文档与实验台架一律在外。
* 残留：`tools/agent/sampler-agent.ts:773` 的注释仍写旧路径 —— **故意不动**（该文件在 codehash 集内，
  改注释同样触发升级波；同 `docs/nn.progress.md` 里 `§126` 先例）。`tools/perf/conv-ab.ts` 不在集内，已改。

---
## §16 darwin-x64 实机计时：native **1.408×**（平台表补最后一格）（2026-09-23）

> 补齐 §9 / `conv-optimize.plan.md` §11.2 平台表的最后一个 x64 组合。**用户实机读数（macOS x64）**：
> `native speedup = 1.408×`（即 `tools/perf/conv-ab.ts` 的 `[native] … speedup=` 行）。

| 平台 | native（内核单函数） | wasm（同源 wasm32 目标） |
|---|---|---|
| win32-x64 | 1.38–1.46× | 1.67–1.71× |
| **darwin-x64** | **1.408×**（本次） | 未测 |
| linux-x64（WSL2） | 1.55–1.59× | 1.67–1.71× |
| linux-arm64（K30 Pro · proot） | **≈1.05×** | 1.28–1.30× |

* **读法：决定倍率的是 ISA 家族，不是操作系统 / 工具链。** 三个 **x64** 平台落在同一带
  （1.408 / 1.55 / 1.41），唯一掉到 ≈1.05 的是 **arm64（NEON）** —— 与 §15 的机制结论一致
  （x64 是**取数端口**受限 ⇒ 少取数的平铺重排值 40–60%；arm64 是 **FP-op** 受限 ⇒ 同样重排只值 ~5%）。
  ⇒ 后续评估这批内核优化**只需按 ISA 分（x64 / arm64）**，不必按 OS 分（darwin/win32/linux 同族同结论）。
* 口径：这是**内核单函数**（一次 `cf_student_features`）倍率，不是端到端 —— 端到端见 §12（win32 生产形态 1.48×）。
* 两个待澄清点（不影响上面读法，只影响可引用性）：① 若这台是 **Apple Silicon 上跑 x64 bun（Rosetta）**，
  那就是模拟层口径，不能代表真 Intel Mac；② 本次只记了倍率，没记 `old= / new= ms`。
  复跑一条命令即可补齐：`bun tools/perf/conv-ab.ts 80 5`
  （要与**历史 prebuilt（入库产物）**而非现编旧源对比：`CONV_AB_OLD_LIB=<旧 darwin-x64 dylib> bun tools/perf/conv-ab.ts 80 5`）。

---
## §15 arm64 逐阶段归因：那 6.4 ms 花在哪、以及**为什么四项不迁移**（`tools/perf/kernel-phases.ts`，conv-optimize.plan.md §11.6，2026-09-23）

> 承接 §14：既然四项在 arm64 上只值 ≈5%，就要回答「时间花在哪」与「为什么 x64 的 +44% 不迁移」。
> 一句话：**arm64 已贴着自己机器的 FP 吞吐上限（61–69%）**，x64 的短板是**取数端口** ⇒
> 「少取数」的重排在 arm64 上**没有瓶颈可治**。

**方法（新台架，已入库）**：`tools/perf/kernel-phases.ts` 把计时插桩（aarch64 `cntvct_el0` 19.2 MHz / x86 `rdtsc`）
**注入 `conv.c` 的一份副本**（断言过的文本替换；**生产源码一字未改**），导出 `cf_phase_profile` 抄回各阶段累计 ticks；
插桩库与生产库**逐字节同输出**（台架默认对拍，防拼错）。旋钮：`--target <id>`（只交叉构建）· `--lib <库>`（在目标机上跑，无需编译器）·
`KP_EXTRA_FLAGS=…` + `--no-eq`（破契约的上限实验）。

**相位表**（两侧都是**优化后**内核；x64 = win32-x64 AVX1 本机，arm64 = K30 Pro/proot `taskset -c 7`）

| 阶段 | x64 µs | x64 占比 | x64 GMAC/s | arm64 µs | arm64 占比 | arm64 GMAC/s | 倍差 |
|---|---|---|---|---|---|---|---|
| total | 1605 | 100% | 23.6 | **6463** | 100% | 5.85 | 4.0× |
| pad3 | 2.1 | 0.1% | — | 6.1 | 0.1% | — | 2.9× |
| conv3 | 343 | 21.4% | 20.4 | 1378 | 21.3% | **5.1** | 4.0× |
| pad5×8 | 36 | 2.2% | — | 146 | 2.3% | — | 4.0× |
| dw×8 | 594 | 37.0% | 14.6 | 1516 | 23.5% | **5.7** | 2.6× |
| pw×8 | 599 | 37.3% | **37.0** | 3377 | **52.3%** | **6.6** | **5.6×** |
| GAP | 30 | 1.9% | — | 36 | 0.6% | — | 1.2× |

（对照 §9 里引的**优化前** x64 口径：pw 54.2% / 17.5–19.2 GMAC/s · conv3 26.1% / 18.3 · dw 21.1% / 16.9 ——
pw 的吞吐翻倍正是 x64 +44%（§14）的来源。）

**① arm64 已经贴近自己的 FP 上限**：A77 只有 2 条 128-bit FP 管线（4 lane/条）且 mul 与 add **共用**它们；
`-ffp-contract=off` 下 1 MAC = 2 个 FP op ⇒ 上限 ≈ **9.6 GMAC/s**（4 MAC/cycle × 2.4 GHz）；
实测整体 **5.85**、pw **6.6** ⇒ **61–69% 上限**。x64（Zen3）有 4 条 FP 管线且 mul/add 分开（实测上限 52.4 GMAC/s），
它的短板是 2 个 **load 端口** ⇒ 同一批「少取数」的平铺重排：x64 **+44%**、arm64 只剩 **+5%**。
**不是某一项拖后腿，是这台机器没有那个瓶颈可治。**

**② FMA 上限实验**（`KP_EXTRA_FLAGS=-ffp-contract=fast`；**故意破契约、只量上限、不是可用配置**）

| | total | conv3 | dw | pw |
|---|---|---|---|---|
| arm64 无 FMA | 6463 µs | 5.1 | 5.7 | 6.6 |
| **arm64 +FMA** | **4893 µs（+32%）** | 6.8 | 7.0 | **9.1** |
| x64 无 FMA | 1605 µs | 20.4 | 14.6 | 37.0 |
| x64 +AVX2/FMA | **1414 µs（+14%）** | 20.5 | 17.7 | 43.0 |

⇒ 同一个杠杆在 arm64 值 **+32%**、在 x64 只值 **+14%**（plan §3.4 当年记的「~5%」是**优化前**代码口径）。
开 FMA = 改数值 = **new era**（权重/语料/基线全重做，`conv-optimize.plan.md` §0.5 红线 ①）⇒ **本批不动**，
只把价签挂出来；「要不要为此开新纪」列进未决事项（`docs/nn.progress.md` §3.3）。

**可行动结论**：**arm64 节点的内核已无微优化空间**（61–69% 贴顶），只剩「**减少 MAC 数**」或
「**接受 FMA 新纪**」两条路；反过来对 x64，取数侧的重排仍是有效手段（dw 现在反而是最大头 37%）。

**复跑**：`bun tools/perf/kernel-phases.ts 300`（本机 x64，应与上表一致）·
`bun tools/perf/kernel-phases.ts --target linux-arm64`（交叉构建）→ 拷到机器上
`taskset -c 7 bun tools/perf/kernel-phases.ts --lib /tmp/kernel-phases.so 300`（**必须钉核**）。

---
## §14 arm64 实机计时 + 逐项归因：**无负项、零回落**，但 x64 的 +44% 不迁移（`conv-optimize.plan.md` §11.4，2026-09-23）

> **关闭 §9 的「⚠ 待办：arm64 实机计时」**（该待办的判据「证明不慢」已达成并超额：连「每一项各值多少」也量了）。
> **编号来历**：这两条（§14/§15）在原 `docs/nn.progress.md` 里编 §144/§145，2026-09-23 拆分时尚未归档
> ⇒ 按本文件局部编号补入（接在 §12 之后；`§13` 已被「决策正文归档」占用）。
> 环境：Redmi K30 Pro（M2002J9E · SD865：4×1.8G + 1×2.4G）· Android + **proot/Ubuntu** · bun 1.4.2，
> 经 **adb 驱动**（`run-as com.termux` + `/data/local/tmp` 中转 + `proot-distro login ubuntu -- bash -lc`；
> 两个坑：Termux uid 读不到 `/sdcard`、`proot-distro` 要显式给 PATH）。

**方法**：old = git 里的历史 prebuilt（`/tmp/old.so`，sha256 `d3f18b2e…`，7664B，**当场核对**）；new = 仓库里的
`src/nn/conv/prebuilt/linux-arm64/conv_native.so`。**钉单个大核**（`taskset -c 7`，内核本来就单线程）+ best-of-N。

| 对比（100×6，注明除外） | 旧 | 新 | 比值 | n |
|---|---|---|---|---|
| **整批**（历史 prebuilt vs 现役） | 6.70–6.87 ms | 6.38–6.59 ms | **1.017 / 1.054 / 1.057 / 1.061** ⇒ **≈1.05×** | 4 |
| `pw8` 变体（`CF_PW_PX` 16→8） | 6.43–6.48 | 6.28–6.48 | **1.003 / 0.993 / 1.008** ⇒ **≈1.00** | 3 |
| `nog3` 变体（conv3 4oc 分组→展开） | 6.48–6.53 | 6.39–6.64 | **1.014 / 0.983 / 1.045** ⇒ **≈1.01** | 3 |
| `ctl`（**与现役逐字节相同**的库） | 6.38 | 6.27 | **1.017** = 噪声底线 | 1 |
| **wasm**（同一份源码的 wasm32 目标） | 9.44–9.53 | **7.29–7.42** | **1.275–1.295**（±0.5%） | 13 |

* **结论：四项没有一项在 arm64 上是负的 ⇒ 不回退、也不拆 `CF_PW_PX` 三档**（`pw8`≈1.00 ⇒ 16px 在 NEON 既不亏也不赚；
  `nog3`≈1.01 而 x64 是 1.14× ⇒ **保留**分组）。整批 arm64 **≈+5%**（x64 +44%）、wasm **+28%**（x64 +69%）
  ⇒ **给 arm64 节点排课程吞吐时，不能按 x64 的提速算产能**。
* **测量纪律（引用 arm64 / 手机数字必须带）**：同一台机器**不钉核**时逐次比值从 **0.988 到 1.096**（±5%，与要读的效应同阶）；
  钉单核 + best-of-N 后收敛到 ±2%。早先一轮报的 1.078/1.106 就落在这条噪声带内 —— **方向对、数值不可引用**。
* **顺带修掉探针的两个读数缺陷**：① 旧侧定位用 `git rev-list -1 HEAD -- <路径>`，而**路径限制的历史把「删除该路径的提交」也算一次改动**
  ⇒ 重组提交一进历史，命中的正是删除提交 ⇒ wasm 对比被**静默跳过**（改 `git log --diff-filter=AM -1 -- <路径>`；
  且 native 源码与 wasm 产物**各自定位**，实测最后增改不是同一提交：`6ed7f11f` vs `7664673e`）；
  ② 原计时「先跑完 A 的所有轮次、再跑 B」⇒ 频率漂移整段偏到一侧，同一份二进制跨相位 **±4%**
  （改**逐轮交错** `benchPair`，修后 x64 重复性 ≤1.5%）。
* 工具：`tools/perf/kernel-variants.ts`（从**现役单源**派生 `ctl`/`pw8`/`nog3` 变体库，断言过的文本替换；
  conv3 的优化前实现从 git 取，不手抄）+ `tools/perf/conv-ab.ts`（`CONV_AB_OLD_LIB` 可指任意库 ⇒ 变体库**不需要探针加接口**）。
  变体的 x64 先验：`ctl`≈1.00 / `pw8`≈1.07 / `nog3`≈**1.14**。
* 「6.4 ms 到底花在哪 + 为什么这四项不迁移」⇒ **§15**（逐阶段归因 + FMA 上限实验）。
* 平台表最后一格 **darwin-x64 = 1.408×**（与 x64 同族同带）⇒ **§16**。
* **arm64 在真实 rollout 里的端到端代价**（含并发放大 / 启动 / TS 兼底悬崖）⇒ **§18**。

---
## §12 端到端实测：内核优化在一局 rollout 里的确切倍率（`conv-optimize.plan.md` §11.2 补测，2026-09-23）

> 补 §9–§11 缺的那一格：之前只量了**内核**（win32 native 1.40–1.46× / linux-x64 1.55–1.59×、wasm 1.67–1.71×），
> plan §11.3 ③ 当时明写「端到端没有单独量，按占比估 1.3–1.45×，要引用确切数字请自己量一次」。本次用**真导出器**量了。

**方法（唯一变量 = 内核二进制，其余全部相同）**

* 同一份代码 + 同一权重（`nn-training/weights/x20-noexplore/x20-noexplore.it181.20260922-083734.json`）
  + 同 `--stages/--seeds/--difficulty/--lives-override`；走的正是训练路径 `export-rl-rollout.ts`。
* **旧内核臂** = 把重组前的入库产物取出来再用 env 指进去：
  `git show <重组前提交>:src/nn/native/prebuilt/win32-x64/conv_feats_native.dll > tmp/old.dll`，
  然后 `NN_NATIVE_LIB=tmp/old.dll`（适配器按 env → prebuilt 顺序解析；日志里 `lib=` 行可确认实际加载的是哪一个，
  两臂的 `attest=3/3 逐字节 vs wasm` 都通过）。⇒ 不需要旧工作区、不需要重编，同一进程结构对拍。
* 交错 **A/B/A/B**（各 3 轮取最优）；启动基线 = 同命令 `--max-ticks 1`（同一 env，两臂各测）。

**结果（win32-x64 · bun 1.4.2 · 真 FFI + 真权重）**

| 配置 | 决策 / tick | 旧内核 | 新内核 | 提速（raw 墙钟） | 扣启动 |
|---|---|---|---|---|---|
| `--max-ticks 12900 --seeds 0-3`（生产形态，= plan §7.3 的命令） | 1433 / 14318 | 4275 ms | **2897 ms** | **1.48×** | 1.49–1.52× |
| `--max-ticks 2500 --seeds 0-3`（定长局） | 1000 / 10000 | 2914 ms | **2085 ms** | **1.40×** | 1.46× |
| `--max-ticks 12000 --seeds 0`（4940 tick 后 gameover） | 494 / 4940 | 1527 ms | **1178 ms** | **1.30×** | 1.34× |

* **每决策边际成本**（生产形态，扣启动）：旧 2.76–2.80 ms → 新 **1.84–1.85 ms**（1.49–1.52×）；4 局批 = **1069 → 724 ms/局**。
* 三轮的离散度很小（旧 4275/4284/4340 · 新 2897/2898/2946）⇒ 比值稳在 **1.45–1.50×**。
* 三档 raw 比不同不是噪声，是**每进程固定成本**（plan §1.4：89–107 ms + bun 启动）摊薄比例不同：
  局越短 / 进程数越多，raw 越低。**引用时必须带配置**；对外一律用生产形态的 **1.48×**。
* **等价性**：三个配置、两臂的 npy shard sha256 **全等**（含 `--max-ticks 1` 的 `18201299dc637d68`）
  ⇒ 提速是白拿的，语料血缘不变（与 §9 的四重验证同结论，这次是**端到端**通道）。

**一处偏离预估（记为观察，未深究）**：按内核占比 86.5–95.2%（plan §1.2 / §4.7.2）+ 内核 1.40–1.46× 反推，
端到端应 ≈1.35–1.46×；实测生产形态 1.48× 略超上沿，且**每决策省 0.92–0.96 ms 大于内核单独口径的 0.67–0.75 ms**
⇒ 真训练进程里内核的实际时间占比高于 profiler 抽样给的那一份（采样器对 JS/native 混合栈的归属偏差所致，未进一步归因）。

**复跑**：见 `conv-optimize.plan.md` §11.2 的「端到端」块（Windows/WSL 同一套 `NN_NATIVE_LIB` 覆盖；
把 `<重组前提交>` 换成 `git log --format=%H -1 <重组前>`，即本批提交的父提交）。

---

## §11 卷积代码全部收进 `src/nn/conv/**` + wasm 产物可重现（2026-09-23）

> 接 §9/§10：用户要求「infer.ts 里的纯 TS 卷积实现也拆出来、native-prebuilt.ts 也移进去」。
> 于是**内核（C）/ 两个加速后端 / TS 孪生 / 分发矩阵**现在都在一个目录里。

### 做了什么

* **TS 孪生实现独立成模块**：`StudentModel` 的 `conv3x3` / `conv5x5dw` / `conv1x1` / `reluInPlace`
  与 features() 里的 GAP 循环 ⇒ **`src/nn/conv/conv_ts.ts::runStudentConvTs(view)`**（含 `ConvTsView`）。
  infer.ts 的兜底变成一行调用；视图在**构造期建一次**（`tsConvView`）⇒ 每 tick 零分配（AGENTS §14 口径不变）。
  为什么值得：三个后端并排可见（改 conv.c 时能一眼看到 TS 侧对应实现），且 conv_ts 不 import 任何模型
  类型（入参只是 buffer 视图）⇒ 可单独当作参考实现对拍。
* **`src/nn/native-prebuilt.ts` → `src/nn/conv/native-prebuilt.ts`**（分发矩阵 / 构建 flags / ABI 与内核同目录；
  `tools/agent/native-build.ts` 仍在 tools/ —— 它是构建器不是卷积代码）。引用同步：两个适配器 ·
  native-build · tests/native-prebuilt · tools/perf/conv-ab · `nn-training/tests/test_ts_code_pack.py` 的
  打包清单。`src/nn/` 本就在 codeHash 目录集内 ⇒ 无需改 `codehash-files.txt`。
* **wasm 产物可重现（实测发现并修复）**：`conv.wasm` **每次重建 sha 都不同**（连编 3 次 3 个 sha）。
  定位：wasm-ld 把 `name` 自定义段的 **module name 写成输出文件名**，而构建为原子落盘写的是
  `${out}.tmp-<pid>` ⇒ 每次不同（直接 clang 对比确认差异就在文件尾 `name` 段，内容就是 `w-a.wasm` /
  `w-b.wasm`）。`-Wl,--name=` 参数 wasm-ld 不认 ⇒ 用 **`-Wl,--strip-all`** 去掉 `name` 段：实测
  「换输出名 → 字节相同」，连编 3 次同 sha，产物小 270B（7620→7350B），导出表
  （`cf_abi` / `cf_student_features` / `memory`）不受影响。代价：wasm 栈追踪少函数名。

### WSL / linux-x64 实测（2026-09-23 追加，`opencode` 发行版 · Ryzen 7 5800H · WSL2 6.18.33.2 · bun 1.4.2）

同一个探针在 Linux 上跑。WSL 里只有 gcc 没有 clang（现编旧内核那一步做不了），所以给探针加了
**`CONV_AB_OLD_LIB`** 覆盖：直接指向 git 里的旧 prebuilt 产物 —— 顺带让「没本地工具链的机器（含
arm64 节点）」也能测。取旧库：`git show <ref>:src/nn/native/prebuilt/linux-x64/conv_feats_native.so > old.so`。

| 后端（linux-x64） | 旧 | 新 | 提速 |
|---|---|---|---|
| native（入库 prebuilt，AVX1） | 2.37–2.47 ms | **1.53–1.55 ms** | **1.55–1.59×** |
| wasm（bun/JSC on Linux） | 4.34–4.50 ms | **2.59–2.63 ms** | **1.67–1.71×** |

* 四方（native 旧/新 × wasm 旧/新）**memcmp 全等**，`[verdict] bitexact=OK`；被计时的 native 新库
  sha256 就是 manifest 里 linux-x64 那一条（`35f32107…`）。
* **独立第二通道**（纯 python3 ctypes，无 bun-ffi 调用开销）：native 旧 2.31–2.40 → 新 **1.63** ms =
  **1.42–1.46×** —— 比值更低是因为去掉 ffi 开销后「内核占比」变大；两条通道结论一致（native **≥1.4×**）。
* 跨平台对照：**wasm 的比值在 Windows 与 Linux 上相同（1.67–1.71×）**；native 比值 Linux 略高（1.55–1.59×
  vs win32 1.40–1.46×）。绝对耗时 Linux 两侧都更快（wasm 3.59→2.60、native 1.64→1.53）—— 纯执行侧差异，
  与包大小/磁盘无关（内核数据全在几百 KB，计时循环内无 I/O）。
* 旧库口径说明：win32 的探针在同样口径下用**旧 prebuilt dll** 作基线是 1.378×（vs 现编旧源码 1.40–1.46×），
  即基线取「历史产物」还是「现编同一份源码」本身就有 ~3% 差；两个数都应记为 native **≈1.4×**。

### 验证

* `bun run check` 绿（2126 pass / 0 fail）· `bun run build` 绿 · `--check-prebuilt` 通过（6 native + wasm32 同源）。
* 逐位：`tests/native-parity.test.ts` + `tests/native-prebuilt.test.ts` 29 例全绿（含 WSL 真 dlopen 新
  linux-x64 `.so` ↔ 新 wasm 逐字节）；`bun tools/perf/conv-ab.ts 30 3` 四方（native 旧/新 × wasm 旧/新）
  **memcmp 全等**（native 1.46× / wasm 1.77× vs 重组前内核）。
* TS 兜底路径由 `tests/nn/student-infer.test.ts` 等 golden 锚定（h16/d2 瘦身 fixture **必走 TS**）——
  4 个文件 25 例全绿 ⇒ 抽取没有动算术。
* 顺带把「重建可重现」台账做实：只改注释后重建，**linux/darwin 四个目标 sha 逐字节不变**，只有 win32
  两个变了（lld-link `/Brepro` 把输入临时路径哈希进 TimeDateStamp，头注已录）。
* **文档**：计划 `src/nn/conv/conv-optimize.plan.md` 随本批入库并更新到「已落地」状态（顶部状态块 +
  §0.3 DoD 勾选 + §9 现址表 + 新增 **§11 落地记录与偏差**：四个 Stage 的实际做法、两平台 × 两后端
  实测表、八条与计划的偏差、arm64 复跑命令、最终文件地图）。

---

## §10 goal/intent 导出器入池 + `--serve` 协议收敛（`tools/sim/serve-loop.ts`，conv-optimize.plan.md §4.6 Stage 4，2026-09-23）

### 做了什么

* **协议收敛为单一实现**：新增 `tools/sim/serve-loop.ts::runServe(main)` —— rl / eval / goal / intent
  四个导出器共用一份 `--serve` 长驻协议（`__SERVE_READY__` / `__SERVE_OK__` / `__SERVE_ERR__ <msg>`）。
  原先 rl 与 eval 各抄一份（两份逐行相同）—— 进池的导出器将来只会更多，协议留在四处就是下次漂移的温床。
  顺手硬化：**合法但非数组**的 JSON（`123` / `{}`）也判 `bad-json` —— 放进去 `main` 拿不到 argv，会静默
  按默认网格跑 16 局（报错比跑错便宜）。
* **goal/intent 入池**：`export-goal-rollout.ts` / `export-intent-rollout.ts` 加 `--serve`
  （`main(argv)` 可注入，`GOAL_SHARD_FILES` / `INTENT_SHARD_FILES` 导出供测试对拍），
  `sampler-agent.ts::PERSIST_SERVE_ENTRIES` 加这两条 —— 这两个模式**局数多、单局短**，是 spawn 成本
  占比最高的地方（计划预估该模式 +15–19%）。
* **静态同规门禁**：`tests/export-goal-intent-serve.test.ts` 读 `PERSIST_SERVE_ENTRIES` 字面量，断言每个
  条目文件都存在且都走 `runServe(main)` —— 堵「加了池条目但导出器没实现 `--serve`」（一进池就 spawn 一个
  跑默认网格的进程再退出，只在 agent 的熔断计数上留痕）。

### 实测（本机 x64，120 tick 瘦身局，真子进程）

| 导出器 | 一次性 spawn | persist worker | 每局固定开销差 |
|---|---|---|---|
| goal | 176 ms/局 | 22 ms/局 | **8.15×**（省 154 ms） |
| intent | 172 ms/局 | 19 ms/局 | **8.93×**（省 152 ms） |

口径提醒（AGENTS §16.1）：这张表量的是**每局固定开销**（瘦身局 120 tick，游戏本体只占很小一块）；生产局
~1200 tick 时长得多，收益占比落在计划预估的 **15–19%** 量级 —— 而 a95/Termux 上 spawn 那一跳 ~2.5s
（本文件 §5 实测），占比比本机更高。

### 等价性（`tests/export-goal-intent-serve.test.ts`）

每个导出器只起**一个**子进程，基线在进程内跑：① READY/OK 握手；② serve 与一次性调用的 **shard 树逐字节
相同** + 容器内 shard 条目逐字节相同（manifest 只除墙钟 `elapsedSec`）；③ 坏行（非法 JSON / 非数组 JSON）
响亮报错、worker 不倒；④ 同 worker 换 seed 的第二局按本局成包（不串局、worker 仍挺）。
rl/eval 改用共享实现后 `tests/export-eval-game-serve.test.ts` 仍绿（协议未变）。

### 门禁

`bun run check` 绿（2126 pass / 0 fail，+3 用例）· `bun run build` 绿。

---

## §9 卷积内核单源化 + 四项循环重排（`src/nn/conv/**`，conv-optimize.plan.md 落地，2026-09-23）

> 编号来历：本批四条在原 `docs/nn.progress.md` 里编 §140–§143（刻意从 140 起：2026-09-22 的 memory
> 把「TPU ragged tail 收尾」记作 §139，而那一条在文件里编 §131，当日的编号出现过漂移）；
> 2026-09-23 拆分后按本文件局部编号排为 §9–§12。

### 做了什么

* **目录重组**：`src/nn/native/**` + `src/nn/wasm/**` ⇒ **`src/nn/conv/**`** ——
  `conv.c`（唯一算法源）· `conv_native.h`（共享常量 / blob 顺序 / `CF_ABI`）· `conv_wasm.h`（wasm 导出层）·
  `conv_cli.c`（对拍 CLI）· `conv.ts`（生产咽喉 native→wasm→TS）· `conv_native_adapter.ts`（bun:ffi + 首用 attestation）·
  `conv_wasm_adapter.ts`（wasm 后端）· `prebuilt/{win32,linux,darwin}-{x64,arm64}/conv_native.{dll,so,dylib}` +
  `prebuilt/wasm/conv.wasm` + `manifest.json`。**`tools/agent/native-build.ts` 留在原址**
  （它是构建器，不是卷积计算；`native-prebuilt.ts` 于 §11 一并移入本目录）。
* **四项循环重排**（conv-optimize.plan.md §2，全部**不动每元素累加次序** ⇒ 逐位不变）：
  ① `pad5` 只清边框（原实现先清整张 30×30 再覆盖内部）② `conv3` 4oc 分组 + 权标量预取
  ③ `conv5dw` 权预取 `kw[25]` ④ `conv1x1_res` 像素块 4 → `CF_PW_PX`（**native 16 / wasm 8**）。
* **单源双目标**：wasm 侧不再手写 `wasm_simd128` intrinsics（DECISIONS §368 那一版 4oc×4px）——
  **同一份纯 C** 编两个目标，唯一差异是目标条件常量 `CF_PW_PX`（它只决定「哪些像素进同一条向量寄存器」，
  不改累加序 ⇒ 两侧逐位相同）；ABI 统一为 native 的 **单 blob + 4 参**
  `cf_student_features(wblob,in16,pooled,bufA)`（wasm 侧原来的 bufB/bufC 由内核静态区承担）。
* **wasm 进门禁**：`conv.wasm` 进 `prebuilt/manifest.json`（`--wasm` 重建 + `--check-prebuilt` 核对），
  连同 6 个 native 目标**一次抓全**「改了 conv.c 却漏重编某个目标」——此前 wasm 漏编只能靠人记得，
  是仓库记录过的最危险失败模式（静默回落 TS = 41ms/forward > 帧预算）。
* **eval 每 tick 白编码**（conv-optimize.plan.md §4.7）：`export-eval-game.ts` 的 `encoder.encode(world)`
  移进 `t % K === 0` 守卫（与 `export-rl-rollout.ts` 里那条 DECISIONS §368 同式）。

### 实测（本机 x64 / bun，真 FFI + 真权重；探针已入库 = `bun tools/perf/conv-ab.ts 30 3`）

| 口径 | 旧 | 新 | 比 |
|---|---|---|---|
| native ms/forward（x64 AVX1，16px） | 2.38 | **1.63** | **1.47×** |
| wasm ms/forward（bun/JSC，8px 纯 C vs 手写 intrinsics 4px） | 6.13 | **3.64** | **1.69×** |
| eval 单局墙钟（stage7/seed860001，1294 tick，8 轮交错 min/median） | unfixed 494/504 | fixed 470/485 | **−19 ms/局** |

**逐位等价（四重）**：① 四方等价矩阵（native 旧/新 × wasm 旧/新）同一权重+输入下 pooled+bufA **memcmp 全等**；
② `tests/native-parity.test.ts` native(16px) ↔ wasm(8px) 逐字节（8 次随机输入 + 参考 CLI）；
③ 一局 rollout（`export-rl-rollout --stages 0 --seeds 0`）native 路径 vs `NN_NATIVE=0`（wasm 路径）
`obs/scalars/a_move/a_fire/lp_*/mask/done/metrics/value.npy` **逐字节相同**，仅 manifest 的 `feat` 字段不同；
④ eval 报告逐字段相同（outcome/ticks/win/score/kills/hitRate/pickups）。
回滚粒度：`NN_NATIVE=0`（真 wasm）或按 commit revert；内核无运行期开关。

### 栅栏（跨平台）

* 6 目标 prebuilt 全量重出（`--cross`，clang 20.1.0 + lld）；`prebuilt/linux-x64` 在 WSL+ctypes 真加载，
  与 wasm 逐字节一致（`tests/native-prebuilt.test.ts` ④）。
* **⚠ 待办：arm64 实机计时**（a95/a96 Termux + mac）——本机只有 x64，静态普查只能证「结构没坏」。
  复测命令：`bun tools/perf/conv-ab.ts 30 3`（**在 arm64 机器上跑**；探针自带旧内核定位
  `git rev-list -1 HEAD -- src/nn/native/conv_feats_native.c`，故重组后仍能取到旧源码现编对拍，
  最后一行 `[verdict] bitexact=OK` 是判据）。
  风险集中在 **conv3 4oc 分组**（arm64 栈引用 18→102、mem +14%）：若 arm64 变慢，**只回退这一项**，
  其余三项保留（plan §2.2 / §5 Stage 2）。
* `--check-prebuilt` 同时守 6 native + wasm32；`bun run check` / `bun run build` 绿；
  nn-training `test_ts_code_pack.py` 绿（白名单已跟着换路径）。

### 决策

`DECISIONS.md §2026-09-23-goalnn-conv-single-source`（单源 + `CF_PW_PX`；被否决的「全局 8px」实测白吐 ~5pp）。

---
## §8 云机 rollout/eval 的单局看门狗：**>5s 即杀、原地重跑同一 argv**（2026-09-22）

用户口径（原话）：「或者超时重试！单局 >5s 肯定不正常。」起因是 it34 的一次现场：

```
[11:03:39] kind=iter rollout: 310/328 games settled (4s)
[11:14:25] kind=iter rollout: 320/328 games settled (651s)   ← 剩下几局卡住，日志只有计数
```

10 局卡死、日志里既没有「哪一局」也没有「卡多久」——旧口径 `timeout=off`（0 = 不限）是
**本机历史行为**，搬到云机上等于「一个卡住的 bun 子进程永远等下去」。

### 口径（唯一定义点 `remote/game_watch.py`，rollout 与 eval 共用一份）

| 常量 | 值 | 含义 |
|---|---|---|
| `SLOW_GAME_WARN_SEC` | 5.0 | 点名线：超过它打一行带局身份（`s3/d7`）的 WARN |
| `DEFAULT_GAME_TIMEOUT_SEC` | 5.0 | **首次尝试**硬顶（= 点名线）：超时 ⇒ kill + 原地重跑 |
| `RETRY_TIMEOUT_FACTOR` | 4.0 | **重试**尝试上限倍数（5s → 20s）；调用方显式配了就一字不改 |
| `GAME_MAX_ATTEMPTS` | 3 | 一局最多跑几次（种子在 argv 里 ⇒ 重跑是确定性的） |
| `GAME_POLL_SEC` | 0.5 | 轮询粒度（软告警与硬顶都靠它发现） |

为什么重试反而**放宽**上限：单局墙钟是重尾的（本机强并发实测 rollout `perGameSecs`
p50 1.8s / p90 3.4s / p99 16.8s；eval `wallSec` p50 1.2~1.6s / p90 3.2~4.2s / p99 7~8s，
`tmp/x20-*/eval_log.jsonl`）。首次 >5s 已属异常（用户口径）值得杀；但重试是**兜底**——
一次主机抖动把同一局连杀三次，代价是「整轮作废重发」或「评估少一局」（读数有偏），
比多等十几秒贵得多。显式配置（`--remote-iter-game-timeout` / `--eval-game-timeout-sec`）
对每次尝试一视同仁：配置说了算。

### 为什么重试而不是「竞速副本」（用户提的另一条路）

argv 不变 ⇒ out 目录不变 ⇒ 声明的 shard 集（`data_fp`）逐字节不变；副本会多产一个同
(stage,seed) 的 shard 目录，直接撞上「实产集 == 声明集」那道门。竞速在**多节点**在线路径上
成立是因为那里有 hub 侧候选表（`rl/queue_local.py::pick_race_target`）；云机离线只有一个节点，
重试是同一效果的最小实现。

### 落地

* `remote/game_watch.py`（新）：五个常量 + `attempt_timeout_sec` / `warn_is_redundant` /
  四行日志（`slow_warn_line` / `hard_cap_line` / `retry_line` / `game_time_summary`）。
  **调用点一律模块属性读**（`game_watch.X`）——`from ... import X` 会抄出第二份绑定，
  patch 了 game_watch 那份而调用点还在读旧绑定就是静默的错口径。
* `remote/iter_rollout.py`：`Popen` + 轮询代替一次 `wait(timeout)`（卡住期间就有告警）；
  `_run_one_game_with_retries` 逐尝试算上限、清理上一次的半截 shard 后重跑；整轮收尾打
  **单局耗时分布**（p50/p90/p99/max + ≥5s 计数 + 重试次数 + 最慢 3 局点名）。
* `remote/offline_eval.py` + `rl/eval_local.py`：同款看门狗（`run_eval_runner_capture` 的
  Popen 轮询版保留 `TimeoutExpired` 的 captured output —— 诊断不被超时吃掉）；单局失败
  原地重跑，且 `wall_sec` 与 in-loop 腿同字段（两腿逐条可比）。
* `remote/protocol.py`：`game_timeout_sec` 的注释口径改写（0 = 节点兜底，不是「不限」）；
  `remote/run_loop.py` 的 eval 侧**原样传 0**（不提前解析——「显式 vs 兜底」决定重试要不要
  放宽，解析一次就丢了这个信息）。
* 测试：`tests/test_game_watch.py`（7 条纯函数：默认值即用户口径 / 首试 vs 重试 / 显式优先 /
  告警冗余判定 / 四行日志带局身份 / 分布行 / 零局不崩）；`tests/test_remote_iter.py` 加
  「重试上限只在 plan 没给时放宽」与「轮末分布行」；`tests/test_offline_eval_cloud.py` 加
  「首次 5s、重试 20s；显式 120 ⇒ 两次都 120」。

### 未做（明确留白）

* **在线多节点腿**（`rl/dispatch.py`）不变：它有 `taskTimeoutSec`（缺省 900s）+ 竞速候选表 +
  超时冷却黑名单，是另一套成熟机制；本节只治云机离线这条腿。
* **本机 in-loop eval**（`rl/eval_dispatch` / `rl/batch_eval`）的上限仍由调用方显式给：本机实测
  eval p90 已 3~8s（机器慢、并发高），拿 5s 当硬顶会频繁误杀；云机 96 核上的 p50 亚秒，
  两者不是同一档。

---

---

## §7 并发口径统一：rollout 与 eval 共用 `max(cores−4, cores×0.8)`（2026-09-22）

用户口径：**「rollout 和 eval 是交替进行的，所以不应该为 eval 保留 CPU 核数，两者都使用
`max(cores − 4, cores × 0.8)`；只要留两三个核给数据回传任务就够了。」**

* **唯一口径落地**：`platform_utils.cpu_worker_slots(cores=None)`（新）= `max(1, min(n, max(n−4,
  floor(n×0.8))))`。96 核 → 92；40 → 36；16 → 12；8 → 6（留 2）；1 → 1。
* **eval 侧**：`remote/offline_eval.default_slots()` 直接跟着它（**删掉**「扣 `plan.workers` 再卡
  64」的老口径——96 核上那是白掉整三成；也删掉 `DEFAULT_SLOTS_CAP/RESERVE` 两个常量）。
* **rollout 侧（云机）**：`remote/run_loop` 新增 `--rollout-workers`（0 = 同口径自动），装配时解析一次
  写进 `RunContext.rollout_workers`，每轮用 `with_rollout_workers(spec, n)` 覆盖计划里钉着的
  `plan.workers`——**那是导出机的规模**（常在 8~16 核的本机导出，却要在 96 vCPU 云机整段跑）。
  `workers` 不进 `data_fp`（`iter_declared_entries` 只取 argv 的 stage/seed）⇒ 换并行度不动摇
  任何指纹；每段开跑时把「本机值 / 计划值」一并日志报出（两者不同时能当场归因「为什么不见快」）。
* **notebook**：`CFG.rollout_workers`（0 = 自动）+ 启动回显；配置表两行的口径文案同步（说明与代码
  说的必须是同一个公式）。

为什么不再互相预留：云机离线段是 rollout → PPO → eval **交替**推进的，给 eval 扣掉 rollout 的
并行度等于两笔账扣同一份钱；真正需要一直活着的只有补传/日志/守护线程，几个核足够。

测试：`test_offline_eval_cloud`（口径=同一公式，逐核数对账）、`test_offline_eval_wiring`
（缺省不吃 `plan.workers`；`with_rollout_workers` 只换 workers、`data_fp` 不变、0/同值不动原对象）、
`test_offline_notebook`（说明面写了公式与旋钮）。

---

---

## §6 手动 evalA 慢 6.7 倍的根因：绕开了节点池（339s → ~18s，改走 in-loop 同一派发器）（2026-09-22）

现象：控制台指标表里手动点的 evalA 耗时 **339s**（x20-noexplore it177），而 in-loop eval ~60s。
读数（同一份 `tmp/<课>/eval_log.jsonl` 的 `eval_summary`）：

| 轮 | sec | nodes |
|---|---|---|
| it165/170/175/180（in-loop） | 50.8 / 57.8 / 50.8 / 63.7 | local 100–118 · mac 64–133 · self 167–218 |
| **it177（手动 evalA）** | **339.2** | **`{"local-evalA": 400}`** |

根因：**不是评估变慢，是手动触发绕开了节点池**。in-loop 的 ~60s 靠三台节点分摊 400 局；
`rl/eval_a_once.py`（控制台 evalA 按钮 + 导入后自动评估的**唯一**启动点）自带一个
`for stage, seed in todo:` **本机串行**循环 —— 339.2 / 400 = 0.848 s/局，与串行假设逐位吻合
（`local-evalA` 这个 node 标签本身就是「自己跑的」指纹；同时刻并跑的 judge-414000 只贡献次要噪声）。

修复（**用户 2026-09-22 指令：evalA 不要只在 local 跑，和 in-loop 等同对待**）：删掉自带的串行
循环，改为**薄包装 `rl/eval_dispatch.py::dispatch_eval_round`**（= in-loop 的同一个
`EvalDispatcher`），于是语料（`a_eval_seed_list` 双轨）、去重（`eval_done_keys(min_iter=1)`）、
**节点门**（evalSupport / stageJsonSupport / bun 版本 / codeHash）、并行 ping + 并行权重下发、
本机份额（`policy.evalLocalSlots`，缺省 4）、账本 schema（`node=<节点 id>` / `wallSec`）、
summary 落账全部与 in-loop 同一份实现 —— 手动行与 in-loop 行从此可比。

- 脚本自己要管的只剩一件：`EvalDispatcher` 在「同 wver 语料已评完」时**早退不写 summary**
  （in-loop 有 `_drain` 覆盖判定兜底，手动触发没有）⇒ 保留按同 wver 回填本 iter 读数的
  `_write_summary_for_wver`（控制台按 iter 挂 evalData，缺本 iter 的 summary 表上永远空）。
- 布局与 in-loop 对齐：冻结权重快照 + 本机局目录都落 `traj/it<N>/`（`eval_replays_once` 的
  权重解析按 `traj/it*/_eval_frozen_weights*.json` 找，同址才有得找）；本机局目录收工即清。
- 本机 gate **立即置位**：手动评估不在训练的 PPO 窗口里，没有要让位的对象
  （不置位 = 本机槽位一路空等到 deadline）。
- ⚠ 一个必须先摘掉的坑：`rl/queue.py` 遮蔽 stdlib `queue`（脚本目录被插进 `sys.path[0]`），
  而节点派发要走 `dist_common.ping_nodes_parallel` / `post_weights_parallel` 的
  `concurrent.futures`（内部 `import queue`）⇒ **导入派发器之前必须先 scrub 脚本目录**，
  否则一 ping 就炸（旧实现从不派发，所以这个坑从没暴露过；同 `eval_replays_once.py`）。

验证（scratch 课程 `tmp/evala-smoke/smoke.jsonc`，1 关 × 双轨 100 局，**不碰任何真实账本**）：

```
[evalA] it105 … → 派发（与 in-loop 同路：节点池 ['self','mac','a97','a96'] + 本机份额）
[eval] it105: dispatch 100 greedy games (corpus=100, done=0) [dual-track anchor+rotor]
       -> [('self',8), ('mac',5), ('a97',7), ('a96',7)] [local tail-reserved ×4]
[evalA] DONE it105 sec=17.8 games=100 winRate=0.11 nodes={'local': 63, 'mac': 37} elapsed=18s
```

节点确实参与（mac 37 局；self 背压停派、a97/a96 未结算 = 派发器既有的节点门/软失败熔断行为，
in-loop 同样如此），100 局 18s 完成；it177 折算 339s → **~50s**（400 局，与 in-loop 50–64s 同量级）。
回归测试 `tests/test_eval_a_once.py`（派发器接线 + summary 读回契约）。

半路被否的方案（留档）：先只做了「本机并发」——`--workers`（min(8,CPU)）+ ThreadPoolExecutor，
实测 7.3×（scratch 100 局 144.3s → 19.9s）。用户口径：手动 evalA 不是「另一套本机评估」，
而是**同一次评估的手动触发**，必须与 in-loop 等同（节点池也不例外）；本机并发只是把单机吃满，
仍然拿不到集群那 5–8 倍并发。**别再往「本机并行度」方向调**——那不是这条路径的杠杆。

---

---

## §5 真机验收：mac(darwin-arm64) / a95(linux-arm64·Termux) 上 native 全绿（plan/rollout-eval-opt.plan.md T2/T3/T4，2026-09-21）

**起因（用户指令）**：「mac 和 a95 节点都已经更新到最新代码，请做一轮真机 rollout/eval 测试」——
补上 T2 DoD 唯一剩项（本机无法代跑的那一条）。

**方法**（生产同源，不绕协议）：读 `rl-config.json` 的 mac/a95 → `dist_common.node_ping`（含
`check_code_hash`）→ `post_weights`（rollout/eval 两个 kind）→ `fetch_task` 真派任务（
per-tick rollout `kind=rollout` + 干净评估 `mode=eval,kind=eval`）→ 解容器读 manifest。
环境与课程逐字同源：`x20-clutch`（stage 2000、stageJson 577B、lives 1、level 0、hard、12900 ticks），
权重 = `it176`（wver `b414e7d7…`），3 个 seed（4242/4243/4244）。临时探针在 `tmp/probe-realnode-native.py`。

**结果**：

| 项 | mac (192.168.0.88) | a95 (192.168.0.95) |
|---|---|---|
| codeHash | `e96d12b8…` == 训练机 | 同 |
| agent / 引擎 | `6ed7f11` / bun | 同 |
| rollout ×3 · eval ×3 | **`feat=native` 6/6** | **`feat=native` 6/6** |
| `validate_result` / `validate_eval_result` | 全 ok | 全 ok（无 409/超时/校验拒收） |
| 服务时长（manifest `elapsedSec`） | rollout 0.3–0.6s · eval 0.3–1.1s | rollout 0.5–1.2s · eval 0.9–2.4s（手机 CPU，量级合理） |

- **跨平台读数一致（最硬的一条）**：以本机 win32-x64 native 为基准，mac(darwin-arm64) 与
a95(linux-arm64) 在 3 seed ×（rollout, eval）= **12 组上 outcome/ticks/score/kills/enemyHits 逐值相同**
（例 s4242 rollout：`lives_exhausted` / 1062 ticks / score `0.23758612136100288` / kills 1 / enemyHits 6，三平台同）。
`codeHash` 相等已保证三边拿的是**同一份 prebuilt 库字节**（`src/nn/**` 在 codeHash 集内）⇒ 这是
「跨平台逐位一致」在真机上的证明，而不是单机声明。
- **本机 A/B 对照臂**（`tmp/probe-local-ab.py`，win32-x64，同 stage/seed/权重，`NN_NATIVE=0` 关 native）：
native 与 wasm **除 `feat` 外逐字段相同**，native 快 **1.38–1.75×（rollout）/ 1.54–1.67×（eval）**。

**顺带发现（已查清，与 native 正交）**：a95 上 eval 局的客户端往返明显大于 manifest 服务时长
（3.4–7.0s vs 0.8–1.1s）。两个实验定位到**两个独立问题**（探针 `tmp/probe-a95-eval-root-cause.py`、
`tmp/probe-a95-spawn-timeline.py`）：

**① 真因 = 每局一个新子进程的手机冷启动（~2.5s），且它会拖慢同节点其它请求。** 证据链：
· eval / bc（都不在 `PERSIST_SERVE_ENTRIES` ⇒ 每局 `spawn` 一个新 bun）的 `提交→202` = 2.5s / 3.0s；
  同节点 rollout（走长驻 persist worker）= 0.07s；mac 的同一个 eval = 0.03s（同一段代码 ⇒ 差异只可能在
  `spawn()` 这一处）。
· 该 2.5s **不是异步的**：任务进行中每 0.1s 打一次 `/v1/status`，第一次轮询的**应答被拖到 2.59s**，
  随后的轮询都在 0.02–0.45s 内回 ⇒ 事件循环在那段被占满（子进程冷启动把手机 CPU 吃满/占着调度）。
  实践含义：**手机上每局 eval 都会把 agent 卡死 ~2.5s**，并发吞吐被吃。
· 量级对得上：同局生命周期 3.2s − 子进程内口径 0.7s = 2.5s（见②的两份读数）。

**② 为什么它看起来像「接单前」且一直看不到真相：同步路径回给客户端的是**未盖章**的容器（真 bug）。**
`tools/agent/sampler-agent.ts:1575-1582`：`lruPut(key, stampServiceSec(key, buf))` 把**盖章副本**放进缓存，
但紧接着 `controller.enqueue(new Uint8Array(buf))` 发的是**原 buf**（子进程内口径）。⇒ 客户端第一次拿到
0.7s，同一局再请求一次（结果缓存命中，走 `cached.buf`=已盖章）拿到 **3.2s**：同一局两个 elapsedSec。
影响：生产 `fetch_task` 默认走同步路径 ⇒ **节点上报的「服务耗时」系统性漏掉子进程冷启动**（手机上漏 2.5s/局），
控制台的节点耗时会被低估；异步路径（1191 行）无此问题。

**两个修法已落地并真机验证（2026-09-21，commit `4a14987`，已 push）**：
· ② 结构上修：新增唯一出口 `serveResult(key, buf)`（盖章一次），同步/异步两处都只发它的返回值
  —— 「盖章值 == 对外值」由代码结构保证。判例 `tests/agent/result-stamp.test.ts`（纯函数，~2ms；含一条
  结构钉子：源码里不得再出现 `enqueue(new Uint8Array(buf))`）。
· ① `export-eval-game.ts` 加 `--serve`（协议与 `export-rl-rollout` 一字不差）+ 入 `PERSIST_SERVE_ENTRIES`
  ⇒ eval 走长驻 worker，手机上每局省下 ~2.5s 与那次事件循环卡顿。判例
  `tests/export-eval-game-serve.test.ts`（握手 / 与一次性调用等价（除 `elapsedSec`）/ 坏行不致命 / 不串局；
  自带 ~0.3s，另一次 agent over-HTTP 的端到端判例实测在 `bun test` 里起不来（30s 超时）已弃用）。

**真机复验（mac/a95 拉到 `4a14987` 后，同 stage/seed/权重）**：

| 指标（a95） | 修前 | 修后 |
|---|---|---|
| 同步 eval 一局（含首次冷启动那轮） | wall 3.43s，manifest `elapsedSec` 0.7（真值 3.2） | wall **0.45s**，`elapsedSec` **0.4** |
| 同局第二次（缓存命中） | 0.83s / 3.2（与第一次不等） | 0.06s / **0.4（与第一次相同）** |
| 任务期间 `/v1/status` 首轮应答 | 被拖 **2.59s**（事件循环卡住） | **0.04s**（无卡顿） |
| eval `feat` / 读数 | native / 同 | native / 同（`lives_exhausted` 与 ticks/score 逐值不变） |

全节点复验：mac + a95 各 3 seed ×（rollout, eval）仍 **`feat=native` 12/12**，且与本机 win32-x64 native
在 12 组上逐值一致（跨平台逐位一致未受影响）。仍存的一次性成本：**节点重启后的第一局**要付一次长驻
worker 冷启动（a95 实测服务侧 6.6s/首局，之后回到 0.6–1.2s）——这是新口径（真实生命周期）下应看到的数。

**未做**：云机（`ts_code.zip` 通道）仍只有 real-bun 哨兵 + 打包门禁的间接证据，没在真云机上确认
一行 `feat=native`——那条通道的验收要等下一次离线训练任务时看 shard 的 `feat`。

---

---

## §4 prebuilt 交叉编译分发：节点不再需要 clang（plan/rollout-eval-opt.plan.md §2.5/T2，2026-09-21）

**起因（用户提问）**：「rollout 节点机器上可能没有 clang，能本机编译出所有平台 windows/macos/linux/
android termux 的 native 库直接给它们使用吗？」——查节点池：self(win x64) / mac / a95·a96·a97·a98
(Android-Termux arm64) / lite / gcs，**异质且多数没有 clang**。原 T2「节点上自己编一次」等于 native 臂
在远端永远开不起来（只会打一行「编译器不可用」）。答：能，且这正是 T2 的正解。

**做了什么**：

1. 训练机交叉编译 6 目标 → `src/nn/native/prebuilt/<id>/conv_feats_native.{dll,so,dylib}` + `manifest.json`
   （源码 sha + flags + cc 版本 + 每目标 sha；共 ~78 KB），随既有 `git pull` 升级通道分发。
   目标 = win32/linux/darwin × x64/arm64；**Termux 用 linux-arm64 那份**（platform 报 linux、arch 报 arm64）。
2. **内核改免 libc**：交叉编译没有目标平台 sysroot，碰 `string.h`/`memset`/`memcpy` 就链不出来。
   内核的零填与整行拷贝换成 `cf_zero/cf_copy`（float 按值赋值，结果逐字节不变 ⇒ 本机 parity 仍 8/8 绿）。
   收益 = 产物**零动态依赖**（无 DT_NEEDED / LC_LOAD_DYLIB / PE 导入表）⇒ glibc / musl /
   **bionic(Termux)** 都能 dlopen；代价 = padding 从 libc 向量化实现降级为自己写，实测 ~0–2%（2.52 → 2.53ms）。
3. **x64 只到 AVX1**（`-mavx -msse4.2`）而不是 `-mavx2`：实测 SSE2/SSE4.2 = 3.46–3.53ms、
   AVX1 = 2.60–2.64ms、AVX2 = 2.51–2.59ms ⇒ AVX1 已拿到全部收益（~27%），AVX2 只多 ~2%，
   而 prebuilt 是发给别人用的，`-mavx2` 的代价是「2013 年前的 x64 CPU SIGILL」。依旧禁 `-march=native`。
4. **解析顺序**：`NN_NATIVE_LIB` → **prebuilt** → `tmp/native` 本机构建 →（有编译器时）编一次。
   节点走第 2 条 ⇒ **零工具链**。两处实现同序（`src/nn/native-conv.ts` 与 tools 侧 `resolveNativeLib`，
   因 src/ 不许依赖 tools/）。
5. **新鲜度只在仓库侧**（`--check-prebuilt` + `tests/native-prebuilt.test.ts`，跑在常规套件里 ⇒ 提交必然过闸）；
   **不做运行期源码 sha 校验**——运行期真闸门是首用 attestation（native↔wasm 逐字节，不过即关 native）。

**证据（本机可复跑）**：

| 项 | 结果 |
|---|---|
| 6 目标产物 | linux/darwin 两份**逐字节可重现**；win32 差 9–13 字节（见坑 ① ） |
| 零依赖（机械核对） | 逐目标断言 ELF ET_DYN+EM_X86_64/AARCH64、Mach-O MH_DYLIB+cputype、PE machine+`IMAGE_FILE_DLL`；且产物里不出现 `libc.so`/`ld-linux`/`libSystem`/`KERNEL32`/`ucrtbase`/`VCRUNTIME`；`llvm-nm -u` 空、ELF `NeededLibraries []` |
| **真执行** | **WSL + python3 ctypes 加载入库 linux-x64 `.so`**：pooled+bufA 与 wasm **逐字节相同**（本机唯一能真跑非本平台产物的通道） |
| win32-x64 | 生产入口 bun:ffi 真加载入库 DLL，attestation **3/3 逐字节 vs wasm**（`tests/native-parity.test.ts`，现加载的就是 prebuilt） |
| 门禁 | `tests/native-prebuilt.test.ts` 16 例 + `bun run check` + `bun run build` 绿 |

**踩到的三个坑（都写进注释/DECISIONS 免得重踩）**：

① **Windows 免 CRT 的两件事**：任何浮点使用都会引用 `_fltused`（否则 `lld-link: undefined symbol: _fltused`）
⇒ 内核在 `CF_FREESTANDING` 下自带该符号；免 CRT 的 DLL 没有 `_DllMainCRTStartup` ⇒ 链接期 `-Wl,-noentry`
（PE 规范允许入口点 0，加载器跳过 init）。实测 bun:ffi 能正常加载。
② **Mach-O 的可重现性**：不显式给 `-no_uuid` + `-install_name` 时，lld 会把**临时输出文件名**（带 pid）
写进 LC_ID_DYLIB/UUID ⇒ 两次构建差 6 字节。给了这两个 flag 后 linux/darwin 均字节相同。
③ **COFF 不追可重现**：lld-link 的 `/Brepro` 把 TimeDateStamp 换成含临时 .o 路径的哈希，`-fno-temp-file`
也压不住（实测仍差 9–13 字节）。正确性靠 attestation、新鲜度靠 manifest sha256，都不依赖「重建字节相同」。

**仍未做**：真节点上确认一行日志（`native 资产就绪 source=prebuilt …`）与一局 shard 的 `feat=native`
—— 本机无法代跑；其余（格式/零依赖/本机加载/linux-x64 真执行）已全部进门禁。

**补（同日，用户点名）：云机通道也得带库。** 离线训练任务的 rollout 是在 **PPO 云机**上执行的
（`kind=iter`：训练侧把 TS 运行时打成 `ts_code.zip` 下发，worker 解到 `ts_cache/<sha>` 再
`bun tools/sim/export-rl-rollout.ts`）。云机既没 clang 也不持仓库，那条白名单原本只收
`.ts/.jsonc/.wasm` ⇒ 库根本不进包 ⇒ `native-conv` 找不到库 ⇒ 首用 attestation 直接跳过 ⇒
**静默回落 wasm**（不报错，只是每局回到 1338ms）—— 正是最难查的那种。修法：新增
`TS_CODE_BINARY_DIRS=("src/nn/native/prebuilt",)` + `TS_CODE_BINARY_SUFFIXES=(".dll",".so",".dylib")`
（只对该目录生效，不给 `.so` 开全局口子），6 目标全带、写 0755，目录缺失就 `HubClientError`
+ 提示重建命令。门禁两道：`tests/test_ts_code_pack.py`（4 例）+ real-bun 哨兵里「把 zip 解到临时树、
以它为 cwd 跑 rollout，断言 shard manifest `feat == native`」（探针验过断言是活的）。

---

---

## §3 rollout/eval 优化落地：native features 内核进生产（plan/rollout-eval-opt.plan.md，2026-09-21）

**做了什么**：Student `features`（rollout/eval 的绝对瓶颈，本机一局 1338ms 里九成）接入 native 内核，
与 wasm 逐位一致、且**门禁化**；选择的进程模型是**共享库 + `bun:ffi`**，不是起进程。

**为什么不是「CLI + 常驻子进程」**（这一条是本轮最贵的判断）：features 是**逐决策顺序依赖**的
（动作 → 下一状态），跨决策无法批量；每局 ~236 次调用，而本机进程启动实测 **42–54ms/次**
（`cmd.exe` 42.1 / `hostname` 54.4，n=30）—— 起进程比整局 sim 还贵，等于负优化。
FFI 每次 ~0.5µs，且 `in16/pooled/bufA` 直接传 JS 数组地址，连 wasm 的「拷进 48KB + 回拷 169KB」都省了。
代价是 native 只在 bun 引擎可用（node 无 FFI）⇒ 引擎选择必须把 bun+native 当独立臂与 node+wasm 比。

**实测（本机，可复跑）**：

| 臂 | ms/次 forward | 出处 |
|---|---|---|
| TS（兜底） | 52.38 | `bun tools/sim/perf-conv-wasm.ts --iters 400` |
| wasm | 6.04（对 TS 8.68×） | 同上 |
| **native** | **2.62（对 wasm 2.31×、对 TS 20.0×）** | 同上 |

单局端到端（同 seed 同权重，x20-clutch.it99，2400 ticks）：**1447ms → 853ms（1.70×）**，
且 outcome/score/kills/ticks 逐字段一致。引擎选择实测：
`bun 2.66ms[native] vs node 3.52ms[wasm] → bun` —— 在 native 之前这里会选 node（3.5 vs bun-wasm 6.2）。

**四条纪律**（都写进了 `DECISIONS.md §2026-09-21-goalnn-native-features-engine · 全文 → 本文件 §9`）：

1. **flags 钉死 + 禁 `-march=native`**：它会开 FMA/AVX-512，而 clang 默认 `-ffp-contract=fast`
   ⇒ 乘加融合 ⇒ 与 wasm 不再逐位 ⇒ 异质节点 shard 字节抖动。
2. **首用 attestation**（本机真实权重 + 3 次 native↔wasm 逐字节）：节点是异质的，
   「本机 8/8 逐位」不能外推，只能在**本机**验；不过就关 native + 一行 warning。
   单测直接用「编一个输 0 的 poison 库」钉住这条守卫。
3. **算法单源**：共享库与对拍 CLI 都链接 `src/nn/native/conv_feats_native.c`（CLI 只剩编解码）。
   wasm 侧那份动不得（`wasm_simd128` intrinsics = 产品字节），所以两侧一致性交给
   `tests/native-parity.test.ts` 逐字节断言，而不是「同序」的口头约定。
4. **记账**：shard manifest 加 `feat`（native/wasm/ts），但**不进 `data_fp`**
   （protocol.py 只对 dir/wver/stage/seed 求 sha）——「以为开了 native 其实回落了」事后能查。

**顺手修掉的两个历史坑**：

* `tools/agent/engine-bench.ts` 旧版**自绘 wasm 内存布局**，按 v2 的 16 通道算而 v3 已是 18 通道 ⇒
  `offIn` 只留 16×SP，越界写进 bufA 区（dummy 数据所以没炸，但基准与生产不同构）。现已改为
  调用生产入口，顺便让基准能测到 native 臂。
* 旧的「8/8 逐位一致」只活在一个手工脚本里（硬编码 `tmp/conv_features_cli.exe`、脚本还不负责构建），
  tmp 一清即失效。现由 `tools/agent/native-build.ts`（钉死 flags + 指纹 `native-build.json`）
  + 门禁用例取代。

**eval 侧端到端验收（T4，2026-09-21）**：`export-eval-game.ts` 与 rollout 共用 `infer.features`，
它的 features 调用次数与 rollout 同量级（贪心逐步 argmax），所以收益同理：
同权重（x20-clutch.it99）/同关（s0）/同 max-ticks（2400），**5 个 seed 逐个跑两臂**——
单局 **1846ms → 1013ms（1.82×）**（5 局总计 9228ms → 5064ms）；
报告 `_eval_report.json` 的**全部字段逐字段一致（唯一不同的字段是 `feat`：native vs wasm）**，
即「换后端不改评估读数」（也再次印证逐位一致的工程价值）。
同时给 eval 报告补上 `feat` 记账（与 rollout 的 shard manifest 同口径）——
greedy eval 是节点侧跑的量最大的一类任务，这里看不见后端就等于「以为开了 native 其实回落」。
注：`export-eval-game.ts` 在 dist 哈希集内 ⇒ 改它意味着节点需随新代码同步（既有惯例）。

**补上的门禁（§3 遗留项）**：`tests/pack-memory-parity.test.ts`（3 例，~2.3s）把 `--pack-memory`
与写盘路径的字节等价从「一次手工冒烟」变成门禁：① `shardNpyEntries` ≡ `writeRlShard` 落盘文件
（逐字节 + 名字集合/顺序 == 导出的 `RL_SHARD_FILES`）；② `buildPack` 两份 entries 字节相同；
③ 真跑两次 exporter：内存模式不写 npy 目录，且用消费端读者 `unpackContainer` 解回后
10 个 npy **逐字节同一**、manifest 除 `elapsedSec` 外逐字段同一。

**③ 这个用例本身抓到一个真事实**（第一次进全量套件时红了）：**pack 不是字节确定的** ——
`packManifest` 带墙钟 `elapsedSec`（0.1s 粒度），并行负载下两条路径分别 0.3s / 0.2s ⇒ 包字节不等，
而失败现场的 10 个 npy 与 manifest 其余字段**逐字节相同**（已从失败产物直接验证）。
所以原稿「同 seed 冒烟两包字节相等（5561B）」是**舍入巧合**，已改成上面那条准确边界。
教训：把「手工冒烟偶然成立」写成结论，会在门禁里以「看起来是 flaky」的形式还回来。

**未做（诚实标注）**：真节点上的 T2（预编译库上传 / 本机编译）未验——代码路径在
（`ensureNativeAssets` 三态 + bundle 复制 + `NN_NATIVE_LIB`），需在节点上跑一次
`bun tools/agent/native-build.ts` 看启动日志与 shard 的 `feat=native`；
`--pack-memory` 的两路径字节等价仍只有手工冒烟、未进门禁；P1 实验内核（`tools/agent/native/conv_feats_opt.c`）不接生产。

**门禁**：`bun run check` 绿（tsc + 全量根套件，27s）；`bun run build` 绿；
新增 `tests/native-parity.test.ts`（8，含 eval 侧 `feat` 记账用例）与 `tests/pack-memory-parity.test.ts`（3），
`tests/agent/rollout-runner.test.ts` 扩到 32（含 native 臂反超 node、`nativeSha` 缓存失效、`parseBench`）。

---

## §2 x20-rebirth it19 rollout 208s 复盘：权重并行下发 + kept 短路径 + tail-join grace 默认 0（2026-09-19）


**一句话**：it19 `rollout_sec=208.4s` 不是仿真慢（真采集 ~26s / 12%），而是 volume 补波
每波重跑「串行权重 POST + tail-join grace 30s」叠出来的墙钟。三处收紧 + GBK 门禁修复。

### 根因（training-loop.log it19 时间线）

| 阶段 | 耗时 | 说明 |
|------|------|------|
| 权重 POST w0 | 50s | 串行 6 节点，单节点 7–12s（purged 整包上传） |
| 真仿真 w0 | 21s | 124/124 局 |
| tail-join grace | 30s | 结果已齐仍等僵尸 worker |
| 补波 w1/w2 同构 | ×2 | kept 仍整包 POST + 又各 30s grace |
| **合计** | **~209s** | **仿真仅 ~26s** |

- 权重 POST 原为裸 `for nd in nodes` 串行；ping 早已 ThreadPool 并行（v4.0）。
- 补波时 agent 返回 `kept`（同 sha 幂等），但 HTTP body 仍整包上传。
- `tailGraceJoinSec` 默认 30s：all_settled 后在飞副本只剩竞速输家，结果注定被 dedup
  丢弃——等待无数据价值；旧实现的问题是「无上界」（join window+taskTimeout≈2700s），
  不是「必须等 30s」。

### 改动

1. **`dist_common.post_weights_parallel`** + dispatch/eval_dispatch/batch_eval 接线：
   ThreadPool 并行 POST，日志按配置顺序回放。`pure_collect_sec` 锚点**不变**
   （仍 = 全部节点权重就绪时刻）。
2. **kept 短路径**：`GET /v1/weights/cached`（头 X-Weights-Sha256 / X-Kind）→
   命中则不传 body 直接 kept。旧 agent 404 → 回退完整 POST。trainer
   `post_weights` 内先探针；agent `sampler-agent.ts` 新增该 GET + 纯函数
   `weightsCachedInBucket`。
3. **`resolve_tail_join_sec`**（`rl/dispatch.py`）：all_settled/halt 默认 **0**；
   窗口到期未齐默认 5s（`tailGraceJoinSecDeadline`）。policy 可覆写（e2e 用 2s）。
4. **同 it 波次权重复用**（本条追加）：`dist_common` 进程内 `_WEIGHTS_PUSHED[wver]→nodes`。
   `partition_weights_nodes` 拆 reuse/need；补波只对 need POST。ping/codeHash/bun
   exclude 时 `forget_weights_node`。`post_weights_parallel` 成功后自动 note。
5. **边分发边开采 + rollout 口径**（用户 2026-09-19，DECISIONS §2026-09-19-rollout-pipeline-metric · 全文 → 本文件 §9）：
   `pure_collect_sec` = **权重开始分发 → 样本齐可交 PPO**（`last_settle − t_dist_start`，
   含与采集重叠的分发墙钟）。`post_weights_parallel(..., on_alive=)` 每节点成功即 spawn
   采样线程；local/reuse 先开采。旧口径「末局 − 全节点 ready」作废。
   **多波聚合**：`rl/reports.aggregate_rollout_collect`——volume 各波带
   `weights_dist_start_ts`/`collect_end_ts`，`combine_reports` 压成 it 级
   `min(start)→max(end)`（首波分发→全部样本齐）；iteration 事件写
   `rollout_collect_aggregated` / `rollout_collect_waves`。
6. **连续配额采集 VOLUME_RULE_V2**（用户 2026-09-19，DECISIONS
   §2026-09-19-volume-continuous-quota）：**退役离散补波**——串行 volume 路径改为
   `loop_core._volume_collect_continuous`（S4 第十八刀后搬到 `rl/loop_volume.py::TrainingVolume`，
下文方法名与路径不变）：读账本 → 按分关差额+软停
   （`collected+inflight*est_s≥quota` 不再派）→ 小批派发 → 直到达标/game_cap/
   batch 安全阀。种子 `(it,stage,k)`；`resume.trailing_stage_samples_per_game`
   提供 est_s。wave 纯函数保留（旧 e2e）；生产不再走 `_volume_topup`。
7. **GBK 门禁**（`docs/nn/engineering.md` §3 同源）：`test_remote_iter_real_bun` / `test_tpu_probe_notebook`
   改 `tests.subproc_util.run_utf8`；`bun_version` 三处显式 `encoding=utf-8`。
   real_bun 另补 `lives_override=1`（exporter 2026-09-19 起无 flag 即响亮失败）。

### 未做（有意）

- **边分发边开采**：会改 `pure_collect_sec` 用户口径（起点 ≠ 全部权重就绪）——属决策项，
  不在本次执行范围。
- volume 多波跨波聚合 `pure_collect_sec`：dashboard 在无 dist 字段时仍回退
  `rollout_sec`（整段墙钟）；观测口径问题，非调度路径。

### 回归

- `nn-training/tests/test_dist_weights.py`（探针命中/404 回退/并行顺序）
- `nn-training/tests/test_tail_grace.py`（grace 默认 0 / deadline 5s）
- `e2e/test_run_rl.py::test_it_tail_join_grace_v317`（policy 覆写仍有界）
- `tests/dist-agent.test.ts` weightsCachedInBucket
- `bash tools/githook/nn-python-gate.sh` 绿（1243+ 用例）
- 根 `bun run test` 绿

### 预期效果（量级，非承诺）

- w0 权重：串行 ~50s → 最慢节点 ~10s（并行）+ 探针。
- 补波 kept：~16–20s/波 → ~2s/波（只探针）。
- tail grace：30s×3 波 → 0s（all_settled）。
- it19 同类轮次 rollout 墙钟有望从 ~200s 压到 ~40–60s（仿真 + 少量调度）。

---

---

## §1 in-loop eval 墙钟：软等可配（180s→30s）+ 本机份额提前放行（2026-09-17）

**为什么记这一笔**：用户检查训练流程后确认「eval 已藏进下一轮 PPO」——`_dispatch_delayed_eval(it)`
排在 `_serial_ppo(it)` **之前**、读归档 W(it-1)、事后只软等（`select_delayed_eval_it(6)==5`）。
但仍有**两段墙钟暴露在 PPO 之后**（写这段时都在 `rl/loop_steps.py` —— S4 第十七刀（2026-09-24）后这一簇（`_join_eval` /
`_sweep_eval_tail` / `_dispatch_delayed_eval` 等 8 个成员）搬到了 `rl/loop_eval.py::TrainingEval`；
下文方法名不变）：

```
改前： it6 collect(W5) ─┬─ 派发 eval(W5) ─┬─ PPO(6)  ◄── 节点侧 eval 藏在这里 ✅
                        │                 └─ _join_eval：软等 ≤180s（硬编码）❌①
                        └─ 本机份额 gate 只在 _join_eval 置位 ⇒ 本机 eval 局
                           在 PPO 收尾后才开跑（local_slots ≈ 5% 语料）❌②

改后： ① **不站等固定秒数**：尾巴交下一轮 rollout 收官这个自然边界收拢（_sweep_eval_tail，非阻塞）
          应急旋钮 policy.evalJoinSoftSec **缺省 0**（>0 = 回到 “PPO 后最多站等 N 秒”）
       ② 本机份额按「本轮 PPO 是否占本机核心」分档放行（policy.evalLocalEarlyEpochs，默认 1）
          · 远端 PPO（--ppo remote）/ 整轮上云（rollout=node）/ stream 轮
              → immediate：本机核心此刻空闲 ⇒ 派发即开闸（节点 hold_for_local 预留同步解除）
          · 本机 PPO → last_epoch：末 early 个 epoch 完成即开闸（复用 ppo_update 的
              on_epoch_done 钩子，判据 `ep_done >= epochs - early`，与吞吐 T4 预采同口径）
          · early=0 → on_join：维持 R6 原语义（纯让位）
          · 远端降级本机（_serial_ppo 落到本地）⇒ _regate_local_eval 收回我们提前放的 gate
```

**为什么是“边界收拢”而不是“站等 N 秒”（2026-09-17 同日修正）**：固定秒数两个方向都错——
尾巴早落地就白站（最常见），尾巴更晚就照样丢。尾巴在**下一轮整段采集**（分钟级）里自己能跑完、
自己写 summary（wver 键控、续跑幂等），所以到下一个自然同步点（下一轮 rollout 收官，即
`_dispatch_delayed_eval` 入口；另加收官 `_drain_pending_eval`）只需零成本观测/清账：已收官的
打一行 `tail settled during rollout`，还在跑的（异常：节点慢/挂）打 WARN 后交后台——时间基准用它
**自己的** `eval_window_sec`（不引入新魔数），它自己的 deadline 会结束它。intent/goal 模式**不动**：
止损判门要吃同轮 summary，仍走全预算 join（`window+60`）。

**验证**：`nn-training/tests/test_eval_timing.py` 新增/更新为 16 例（含「缺省零 join」「边界收拢」）——旋钮读数与坏值回落、放行档三分支、
`early_epoch_reached` 边界（early=0 / early>epochs）、派发即放行 + 降级收回、本机 PPO 未放行 /
epoch 钩子到点放行 / `evalLocalEarlyEpochs=0` 不加钩子、缺省零 join + 边界收拢（已收官/在跑/应急旋钮>0/无尾巴）、`evalJoinSoftSec` 应急值超预算夹回。
门禁：`e2e/test_run_rl.py -k "eval_deferred|eval_post_ppo_weights|eval_local_gate|tail_join_grace|early_race"
5 passed`；nn python 全量 **绿**（**1200 passed / 3 skipped，第一次无 any deselect/跳过**——
`docs/nn/engineering.md` §10 记的那条平台性存量红已在 §1.1 修掉）；ruff + mypy 干净。

### §1.1 顺带清掉存量红：盘符/UNC 路径在任何平台都被拒（2026-09-17）

**红点**：`test_remote_iter::test_spec_argv_weights_must_be_relative` 在 Linux 上恒失败——
`_iter_rel_path`（`remote/protocol.py`）只挡 `..` / `~` / POSIX 绝对路径，`os.path.isabs` 与
`Path.is_absolute` 都只看**当前内核**规则 ⇒ `C:/weights.json` 在 Linux 上静默过门。
这不只是“测试红”：Windows 盘符路径真能过 hub 的门，到节点上却指向**宿主盘**（或直接跑挂）——
节点的 cwd 契约不随 hub 内核变。

**修**：`_iter_rel_path` 增两条跨平台判定（只多拒、不放松）：盘符前缀 `^[A-Za-z]:`（绝对值与
**drive-relative `c:x`** 都算）与 UNC（`\\host\share`）。回归用例扩为参数化 5 例（`C:/`、`C:\`、
`c:weights.json`、两种 UNC）+ 一条反向断言（`weights.json`、`sub/dir/w.json`、`./w.json`、
`w-1.2.json`、`a_b/c.json` 照旧放行，防收紧误伤正常相对路径）。修前 4/5 参数化用例红（`//host/…`
本就命中 `os.path.isabs`），修后全绿。

**门禁**：`nn-python-gate.sh` **1200 passed / 3 skipped，rc=0**（首个无 deselect、无跳过的全绿）；
ruff + mypy 干净。

**仍暴露的墙钟（有意保留）**：① 收官 drain（`_drain_pending_eval`，无 PPO 可藏，
≤min(window+60,600)s）；② intent/goal 全预算 join。per-tick 主链现在**不为 eval 站等一秒**。
另：stream 路径「标签超前一轮 + 同 iter 双点」的缺陷**未动**（`--ppo remote` 下不可达，本地
stream 腿才可见），已在上一轮的流检查中记录，待单独处置。

---


## §13 决策正文归档（搬自 `DECISIONS.md`，2026-09-23）

> 2026-09-23 把 `DECISIONS.md` 里这些条目的**正文全文**搬到这里（索引行与编号仍留在
> `DECISIONS.md` —— 编号永不重排）。锚点 = `### §<旧编号>`。

### §2026-09-12-rollout-flag-bug（2026-09-12，local rollout 3命1星污染事件；已修、已记录、暂不重训）

- **背景**：`nn-training/rl/cmd.py` `build_rollout_cmd` 用 `f"--{k}"` 拼 override 键
  （`lives_override`/`player_level` 下划线），而 `tools/sim/export-rl-rollout.ts` 只认连字符
  `--lives-override`/`--player-level`（未知 flag 静默忽略）⇒ **local 直跑全程以 hard 缺省
  （3命1星）执行，远端节点以课程覆盖（1命0星）执行**。commit `1ee8955`（2026-09-12 16:55）
  修复（下划线→连字符，`tests/test_rl_cmd.py` 锁死口径）。bug 自 `e828331`（2026-09-02 22:55，
  M1 配置化）引入。
- **污染范围**：e828331 → 1ee8955 之间所有课程的 **local 直跑 rollout 轨迹**（PPO 吃进
  3命1星环境的样本）。各课程 local 局占比实测（`tmp/<course>/dist-agent-meta.jsonl`）：
  c4-kb1 **39.1%**、c4-margin 28.3%、c6-gae 21.1%、c5-gae/c5-margin/c5-ent ~14-15%、
  c6-margin/c6b-margin 11-13.6%、c5-tick/c6-pickup 13.3-13.8%。**样本量权重更高**：it160
  local 45 局 7204 样本（avgTicks 1595）vs remote 105 局 11631 样本（avgTicks 1103）⇒
  c6-gae 污染在 PPO 中的实际权重 ≈ **38%**（> 局数占比 21%，3命局活更久）。
- **关键事实（判定可信的依据）**：eval 链路（`export-eval-game.ts` + `run_local_eval_game` +
  sampler-agent）与 rollout 命令模板（cmd.py）是**两套独立代码**，eval 一直用正确连字符
  flag ⇒ **所有 eval 口径结论（c5-gae 55% 爬升、c6-gae +7pp 等）未被污染**。修复后实测
  it160 归档权重 1命0星采样 rollout = 27% ≈ eval 29% ≈ 配对 30%，三口径回归一致。
- **c6-pickup 探针（污染窗口内训的 it35，修复后评估）**：c6 关 seeds 0-99 配对
  **38% vs 起点 23% = +15pp，McNemar p=0.025 显著**；且高于 c6-gae 160 轮的 30%（in-loop
  eval it5=38%/it35=34% 同步确认，非单点假象）。污染排除：污染更重的 c6-gae 反而不如它 ⇒
  **38% 落在 wPickup 1.5→3.0 杠杆上（唯一变量）**，道具杠杆真效初证。
- **处置（用户拍板）**：① 已收官课程（c5-gae/c6-gae/c5-tick 等）**不重训**——判定全走 eval
  （干净），权重保留作 bc/参考，但出身含 X% 多命样本需知情；② c6-pickup **暂不重启**
  （it36 权重 PPO 未完成即停，归档停在 it35；训练进程内存旧 cmd.py，修复不会热更新）；
  重启时须用修复后代码、以 c6-pickup.it35 为 bc 续跑 it36+。
- **违反后果**：任何人拿 training_log 的 rollout winRate 当能力口径（虚高 17pp 量级）；
  任何人把污染窗口课程的权重当作"纯 1命0星数据"训出的（引用前必须查本条目占比表）；
  任何人未经"修复后代码 + 冻结快照"就用本地直跑出教训性结论。

---

### §2026-09-19-rollout-pipeline-metric（2026-09-19，边分发边开采 + rollout 耗时口径，用户定义）

- **口径（用户拍板）**：rollout 耗时 = **权重就绪开始分发 → 所有样本采集完毕可交 PPO**。
  `pure_collect_sec` = `last_settle − t_dist_start`（**含**与采集重叠的分发墙钟，端到端）。
  旧口径（2026-08-24：末局结算 − **全部**权重分发完毕）作废——在「先等全节点再开采」下
  把分发墙钟藏进 net/dist_phase，volume 多波时 dashboard 显示的 rollout 与真实采集周期脱节。
- **实现**：`dist_common.post_weights_parallel(..., on_alive=)` 每节点 POST 成功即回调；
  `rl/dispatch.py` 先起 local/reuse 采样线程，need 节点后台 POST 成功立刻 spawn（边分发边开采）。
  `weights_dist_start_at` / `weights_dist_done_at` 作诊断锚点；`dist_phase_sec` 仍为
  ping→权重分发完成（与采集重叠部分不再从 rollout 里抠掉）。
- **备选与否决**：保持「等全节点 ready 再开采 + pure_collect=末局−全 ready」——否，与用户
  端到端口径冲突，且 it19 实测串行分发 50s×多波白白空转；rollout 只记纯仿真（末局−各节点
  自 ready）——否，用户明确要「开始分发→样本齐」一体读数。
- **落地**：`dist_common.rollout_collect_sec` / `partition_weights_nodes` / `_WEIGHTS_PUSHED`
  同 it 补波复用；dashboard `phaseSecs` 注释同步。DECISIONS 本条 = 口径变更备案（防再
  「优化」回旧锚点）。
- **多波聚合（同日补充）**：`rl/reports.aggregate_rollout_collect`——volume 各波报告带
  `weights_dist_start_ts` / `collect_end_ts`，`combine_reports` 压成 it 级
  `pure_collect_sec = min(start)→max(end)`（首波分发→全部样本齐，含波间空隙）。无 ts
  时回退 max(per-wave) 并标 `rollout_collect_aggregated=False`。iteration 事件附加
  `rollout_collect_aggregated` / `rollout_collect_waves`。

---

### §2026-09-19-volume-purecollect-stale-merge（2026-09-19，bugfix：指标表 rollout 时间轮轮累加）

- **背景**：x20-powered/x20-snowball 的 `pure_collect_sec`（指标表 rollout 列）it1≈30s
  起每轮 +70~100s，it6 达 425s；同轮真采集 `rollout_sec` 始终 ~25–36s。
- **根因**：`_volume_collect_continuous` 收官时 `combine_reports([self._report, combined])`，
  而 `self._report` **未在轮初清空**——仍带上一轮 `weights_dist_start_ts`；
  `aggregate_rollout_collect` 的 min(start) 被钉在 run 起点，pure_collect ≈ 累计墙钟。
  同路径 `totalSamples` 也被跨轮相加（jsonl `samples` 虚高；`transitions_collected` 正常）。
- **备选与否决**：在 combine 前手工剥掉 prior 的 ts —— 否，prior 整份都不该进本轮报告
  （games/score 会双计）；dashboard 改读 `rolloutSec` 回避 —— 否，掩盖错误账本；
  保留跨轮 combine「凑完整 run 窗口」—— 否，与用户定义的「本轮采集」口径冲突。
- **决定**：轮初 `self._report = {}`；continuous 收官只采纳本轮
  `adopt_volume_report(combined)`（无 batch 时返回**合法空 shape** `combine_reports([])`，
  绝不返回 `{}` —— 否则 `_log_report`/events 读 `games` KeyError，2026-09-19 同日回归已修：
  combine 跳过空 dict、日志/events 用 `.get`）。历史 jsonl 的
  pure_collect/samples 累加行**作废对照**，请改看同轮 `rollout_sec`/`transitions_collected`。
- **违反后果**：任何把上一轮 `_report` 再 combine 进本轮采集的改动，都会让指标表
  rollout 列再次单调暴涨；任何让 volume 收官后 `_report` 停在 `{}` 的改动都会
  在 `_log_report` 打 KeyError 打死 trainer。
- **落地**：`nn-training/rl/reports.py`（`adopt_volume_report`/`empty_collect_report`/combine 跳空）、
  `loop_core.py`（轮初复位 + continuous 恒 adopt）、`loop_steps.py`/`events.py`（.get）；
  回归 `tests/test_rl_reports.py::test_adopt_volume_report_*`。

---

### §2026-09-19-m1-eval-python-dispatch（2026-09-19，m1-eval 分派链回归 Python：TS 只写 spec/读行/打分）

- **决定**：上一轮的「仍留的重复」点名的就是 `m1-eval.ts` 自己的分派链（判门/rescan/尾竞速/权重下发
  ≈300 行）。用户裁定同一口径——**Python 侧已有实战版，别再在 TS 重建**。新增
  `nn-training/eval_m1_once.py`（spec → `rl/batch_eval.BatchEvalRunner` → 逐局行），TS 只做
  「写 spec → 经 nn-py-safe.sh 调 Python → 读回逐局行 → scoreV7/报告/HTML/banner」。
- **边界（明确划出，不是半途而废）**：只有**分派**回归 Python。`--no-dist` 仍走本机 in-process
  worker 池——那是游戏引擎本身、不涉节点通信，且 `tools/perf/scan-intent-concurrency.ts` 正是量它的
  并发度；非分派 policy（`nn` 走 `--weights-dir` 自动发现、无文件可上传，`intent`/`intent-oracle`
  与 cadence 探针）也留在本机池。`goal-god` **不再分派**：远端 goal 执行器需要 goal 权重桶，
  而它按 kind='none' 分派时远端必然缺权重（旧实现看似分派、实则不可用）⇒ 要跑用 `--no-dist`。
- **Python 侧三处扩展（都是加法；既有调用方行为逐字节不变）**：
  1. `rl/batch_eval.py`：`kind` 由 policy 推（`KIND_FOR_POLICY`：intent-exec→'intent'、goal→'goal'、
     nn/god→'rollout'），**上传与查询同 kind**（此前写死 'rollout' ⇒ intent/goal 一律 409）；
     `include_scorable`（默认关；True 时逐局行多带 agent 报告的原始 `scorable` = scoreV7 的完整输入，
     原样回传、不做字段级搬运 ⇒ 不可能两端漂移）；unit 的 `lives`/`level` 缺省 = **不覆盖**
     （difficulty/关卡默认说了算；写死 3 会把「难度默认」硬编码成常数，改难度即错）。
  2. `rl/eval_m1.py`：`subprocess.run(text=True)` 补 `encoding="utf-8", errors="replace"`——父进程不传
     encoding 时按 locale 解码（zh-CN Windows = cp936），而 m1-eval 的 stderr 带中文 ⇒
     UnicodeDecodeError 被 `dispatch_eval_bg_m1` 的 except 吞成「clean eval failed (ignored)」，
     **干净评估静默消失**（2026-09-19 实测：本地/分布式两种调用都复现；与 gate_check §30 同类坑，
     那边靠 ensure_ascii 免疫）。
  3. 两个一次性入口（m1 / course）把 `sys.stdout` 改道 stderr：训练栈 `rl.log.log()` 按设计写 stdout，
     而这两个入口的 stdout 是调用方的**产物通道**（m1 的 JSON 报告 / 课程行）——实测 `[dist] weights[…]`
     行混进 stdout 后 `json.loads(stdout)` 取 perGame 会**静默失败**（D5(a) 入账缺口）。
- **验证（真集群 + 真消费方）**：
  - 三种分派 policy 实跑（`god` / `intent-exec` 用 `tools/gen-intent-weights.ts` 生成的全尺寸权重 /
    `goal` 用形状合法的合成权重）：6 节点在线、配置 `rl.local_slots: 0` ⇒ 逐局 `node:…` 全远端、本地 0；
  - **跨 runner 对拍**（同 stage/seed/权重，dist=export-eval-game vs 本机池=sim-worker）：逐字段一致，
    唯一差异是 `firstKillTick` ±1 tick 的采样口径（scoreV7 suite 完全相同）；
  - **训练循环真入口** `rl/eval_m1.py::run_clean_eval` 实跑 35 关 × 1 seed →
    `winRate=0.714 total=35 cleared=25 error=0 retries=0 perGame=35`；
  - 断点：dist 走 Python run dir 台账（二次运行 `already settled — skip`，0.0s；`--fresh` 才清），
    本机池仍走 TS ledger（`ledger resume: N/M already settled`）——两套各自完整，不叠加。
- **门禁**：根 `bun run check` 绿 · `nn-python-gate` 绿（ruff/mypy）。新增 Python 6 例
  （spec→unit 归一/行映射/kind 表与 DISPATCHABLE 对齐）+ TS 7 例（spec 构造/行映射/白名单）。

---

### §2026-09-21-goalnn-native-features-engine（2026-09-21，落地 `plan/rollout-eval-opt.plan.md` §4：native 内核进入**生产** features 路径）

**背景**：Student `features` 是 rollout/eval 的绝对大头（本机实测：TS 52.4ms / wasm 6.0ms /
native 2.6ms per forward；每局 ~236 次调用 ⇒ 单局 sim 1338ms 里九成）。native 与 wasm
**逐字节一致**（`tests/native-parity.test.ts` 现为门禁，8 次随机输入 pooled+bufA 逐位相等），
但「怎么接进生产」此前没有任何裁决：进程模型没定、跨平台字节一致没保、资产分发没写。

**决定**：

1. **单一咽喉 + 选择链**：`src/nn/conv-wasm.ts::runStudentFeatures` = `native → wasm → TS`，
   `infer.features` 只调它 ⇒ rollout 与 eval **同引擎**（T4 免费达成）。native 失败一律回落，
   调用方永不感知；TS 实际生效时 `noteFeaturesTs()` 记账（防「以为开了加速其实在 TS」）。
2. **进程模型 = 共享库 + `bun:ffi`（同步零 IPC）**，不是「CLI + 常驻子进程」。features 是
   **逐决策顺序依赖**的（动作→下一状态）⇒ 跨决策不能批量；而本机进程启动实测 42–54ms/次
   （`cmd.exe` 42.1 / `hostname` 54.4，n=30），×236 次/局比整局 sim 还贵 ⇒ 起进程 = 负优化。
   FFI 每次调用 ~0.5µs，且 `in16/pooled/bufA` 直接以 JS 数组地址传入（连 wasm 的拷进 48KB+
   回拷 169KB 都省了）。代价：**native 只在 bun 引擎可用**（node 无 FFI，不做 napi 插件）——
   故 bun 臂的基准数字就是 native 的数字，与 node+wasm 比完再定引擎（见第 5 条）。
3. **准入 = 本机首用 attestation**：加载后先拿**真实权重**做 3 次 native↔wasm 逐字节对拍，
   过了才投产；不过 / 库缺失 / ABI 不符 / 参考不可用 ⇒ 本进程关闭 + 一行 warning；
   运行期任何异常 ⇒ 本进程**永久**关闭（不静默重试）。理由：节点是异质的，
   「本机 8/8 逐位一致」不能外推，只能在**本机**验（评审 B2/B4）。
4. **构建钉死 flags + 指纹**（`tools/agent/native-build.ts`）：`-O3 -ffp-contract=off
   -fno-fast-math`（x64 另加 `-mavx2 -msse4.2`；**已被 §2026-09-21-goalnn-native-prebuilt-distribution
   第 3 条修订为 `-mavx -msse4.2`** —— 收益已由 AVX1 吃满，AVX2 只多 ~2% 而代价是老 CPU SIGILL），
   **禁 `-march=native`** —— 它会打开 FMA/
   AVX-512，而 clang 默认 `-ffp-contract=fast` ⇒ 乘加融合 ⇒ 与 wasm 不再逐位、跨节点
   shard 字节抖动。产物 + `native-build.json`（源码 sha + flags + cc 版本 + 产物 sha）
   落在 `tmp/native/`（gitignored）；`--check` 判「盘上产物是不是这份源码编的」。
   native 侧**单源**：共享库与对拍 CLI 都链接 `src/nn/native/conv_feats_native.c`。
5. **引擎选择纳入 native 臂**：`tools/agent/engine-bench.ts` 改为调用**生产入口**（不再自绘
   wasm 内存布局——旧版按 v2 的 16 通道算、v3 已是 18 通道），输出 `BENCH` + `BENCH-ARM`；
   `chooseByBench` 语义不变（node 需快过 bun 臂 3% 才入选），但 `EngineChoice` 新增
   `bunArm/nodeArm/nativeSha`，**native 库指纹进缓存键**（重建过就重测）。
   资产进 bundle（`ensureNativeAssets`）+ 构建失败/缺库只降级不抛。
6. **记账**：shard manifest 新增 `feat`（`native|wasm|ts`）；**不进 `data_fp`**
   （`protocol.py::data_fp` 只对 dir/wver/stage/seed 求 sha，已核）。

**备选与否决**：

* **常驻子进程 + 二进制分帧 IPC**——否：见第 2 条的实测（42–54ms/次 vs 236 次/局），且要自己
  写同步管道读（Node 侧 pipe 非阻塞、`fs.readSync` 要 EAGAIN 重试），复杂度全换来负收益。
* **让 wasm 与 native 共享同一份 C 源**——否：wasm 内核用 `wasm_simd128` intrinsics，**改它就是
  改产品字节**（= 新 era，浏览器路径也要重验）。改为「native 侧单源 + 两侧逐字节对拍进门禁」。
* **用 golden 哈希替代本机 wasm attestation**——暂不做：省下的只是 ~10ms/进程，却要多维护一套
  golden 版本化；本机 wasm 参考一直都在（缺 wasm 时本模块直接关 native，保守优先）。
* **`-march=native` 换一点速度**——否：见第 4 条。
* **node 侧也用 native（napi 插件 / node-gyp）**——否：要每个节点有编译工具链 + ABI 随 node
   主版本漂，收益已被「bun+native < node+wasm」覆盖（本机 2.66ms vs 3.52ms）。

**gate**：`tests/native-parity.test.ts`（7 例：共享库 8 次逐字节 / 参考 CLI 逐字节 /
架构守卫 / 选择链 / 库路径候选 / poison 库 attestation 守卫 / 指纹过期）；
`tests/agent/rollout-runner.test.ts` 32 例（含 native 臂反超 node、nativeSha 失效、parseBench）；
`bun run check` + `bun run build` 绿。实测（`bun tools/sim/perf-conv-wasm.ts`）：
TS 52.4ms / wasm 6.0ms / native 2.6ms（**对 wasm 2.3×、对 TS 20×**）；
单局端到端（同 seed 同权重、x20-clutch.it99、2400 ticks）：**1447ms → 853ms（1.70×）**，
outcome/score/kills/ticks 完全一致；引擎选择实测 `bun 2.66ms[native] vs node 3.52ms[wasm] → bun`。

---

---

### §2026-09-21-goalnn-native-prebuilt-distribution（2026-09-21，落地 `plan/rollout-eval-opt.plan.md` §2.5/T2：native 库改「训练机交叉编译 + 随仓库分发」）

**背景**：原 T2 是「节点上自己 `bun tools/agent/native-build.ts` 编一次」。用户提问（2026-09-21）：
「rollout 节点机器上可能没有 clang，能本机编译出所有平台的 native 库直接给它们使用吗？」——查节点池
（docs/goal-nn-handoff.md §4：self(win) / mac / a95·a96·a97·a98(Android-Termux) / lite / gcs）可见节点是
异质的且**多数没有 clang**，原设计等于 native 臂在远端永远开不起来（只会得到「编译器不可用」一行）。

**决定**：

1. **训练机交叉编译 6 目标入库**：`src/nn/native/prebuilt/<platform>-<arch>/conv_feats_native.{dll,so,dylib}`
   + `manifest.json`（源码 sha + flags + cc 版本 + 每目标产物 sha，共 ~78 KB）。目标 = `win32/linux/darwin ×
   x64/arm64`。分发通道就是既有的 `git pull`（节点升级本来就是同一分支 ff-only）。
   **Termux 不单列目标**：它 `process.platform=linux`、arch=arm64 ⇒ 用 linux-arm64 那一份。
2. **内核改免 libc（freestanding）**：交叉编译时只有 Windows 的 MSVC 头/库、没有目标平台 sysroot，
   一旦 `#include <string.h>` / 引用 `memset/memcpy` 就链不出来（`-nostdlib`）。内核里 padding 的零填与
   整行拷贝改为本地 `cf_zero/cf_copy`（float 按值赋值，语义与 memset/memcpy 逐字节等价 ⇒ 不改结果）。
   收益：产物**零动态依赖**（无 DT_NEEDED / LC_LOAD_DYLIB / PE 导入表）⇒ 同一份 linux-arm64 在
   glibc / musl / **bionic(Termux)** 上都能 dlopen，不必为 Termux 单独出目标。
3. **x64 只到 AVX1（`-mavx -msse4.2`），不用 `-mavx2`**：实测量化（ffi 微基准，x20-clutch.it176）
   SSE2/SSE4.2 = 3.46–3.53ms · AVX1 = 2.60–2.64ms · AVX2 = 2.51–2.59ms ⇒ AVX1 已拿到全部收益（~27%），
   AVX2 只多 ~2%；而 prebuilt 是**发给别人**用的，`-mavx2` 的代价是「2013 年前的 x64 CPU 直接 SIGILL」。
   依旧**禁 `-march=native`**（FMA 收缩 ⇒ 与 wasm 不再逐位，见 §2026-09-21-goalnn-native-features-engine 第 4 条）。
4. **新鲜度判断只在仓库侧**（`--check-prebuilt` + `tests/native-prebuilt.test.ts`），**不做运行期源码 sha 校验**：
   提交时 prebuilt 与源码必然同源（门禁钉死）；而运行期真闸门始终是**首用 attestation**（真实权重下
   native↔wasm 逐字节，不过即关 native + 响亮回落）。节点上多读 3 个源文件换不来更安全的结论，只会把
   「库在 bundle 里、源码不在」这种正常情形误判成不可用。
5. **解析顺序统一为 env → prebuilt → 本机构建**（`src/nn/native-conv.ts` 与 `tools/agent/native-build.ts::resolveNativeLib`
   两处同序，因 src/ 不许依赖 tools/ 而各写一份）。prebuilt 排在「本机构建」之前：它是所有节点都会拿到的
   那一份，让本机也用它 = 暴露差异的机会最多。**节点因此完全不需要编译器**。
6. **prebuilt 放在 `src/nn/` 下 ⇒ 进 codeHash 集**（`tools/agent/codehash-files.txt` 的 `src/nn/`）：换了库
   就触发一次正常的节点升级波（与 `src/nn/wasm/conv_feats.wasm` 同一先例：受跟踪的构建产物）。
7. **云机通道也要带库（`ts_code.zip`，2026-09-21 用户点名）**：离线训练任务的 rollout 在 PPO 云机上
   执行（`kind=iter`：训练侧把 TS 运行时打成 `ts_code.zip` 下发，worker 解到 `ts_cache/<sha>` 后跑
   `bun tools/sim/export-rl-rollout.ts`）——云机**既没 clang 也不持仓库**，所以那条白名单
   （`src/**` + `tools/**` 的 `.ts/.jsonc/.wasm`）漏了库就等于「无库可加载 ⇒ attestation 跳过 ⇒
   **静默**回落 wasm」。故新增 `TS_CODE_BINARY_DIRS = ("src/nn/native/prebuilt",)` +
   `TS_CODE_BINARY_SUFFIXES = (".dll",".so",".dylib")`（**只对该目录生效**，不给 `.so` 开全局口子），
   6 个目标全带（云机 arch 打包时未知），二进制写 `0755`，目录缺失则 `HubClientError` + 提示重建命令。

**备选与否决**：

* **让节点各带工具链**——否：异质（Windows/mac/Android）且多数装不上/不该装；也要为每台机器维护编译
  环境，收益却只是「native 在少数机器可用」。
* **hub 侧下发二进制 / LFS / 独立 CDN**——否：节点升级链路已经是 git pull；多一条分发通道就多一处
  版本错配与运维面（且 codeHash 升级波无法覆盖它）。
* **`-march=native` / `-mavx2` 换一点速度**——否：见第 3 条（AVX2 收益噪声级、代价是 SIGILL）。
* **追 COFF 字节可重现**——不追：lld-link 的 `/Brepro` 把 TimeDateStamp 换成含临时 .o 路径的哈希
  （`-fno-temp-file` 也压不住），win 两份差 9–13 字节；而正确性靠 attestation、新鲜度靠 manifest sha256，
  都不依赖「重建字节相同」。（linux/darwin 两份**已**字节相同：darwin 需显式 `-no_uuid` + `-install_name`，
  否则 LC_ID_DYLIB 里带临时文件名的 pid。）
* **node 侧也吃 prebuilt（napi）**——否：node 无 FFI（沿用前述条目的否决）。

**gate / 证据**：`tests/native-prebuilt.test.ts`（16 例）——
① 同源：`prebuiltStaleReason` 为空、6 目标 sha/尺寸/ABI 与 manifest 一致；
② 格式与依赖：逐目标断言文件头与架构（ELF ET_DYN/EM_X86_64|AARCH64、Mach-O MH_DYLIB + cputype、
PE `PE\0\0` + machine + `IMAGE_FILE_DLL`）、两个导出符号在、且**不出现** libc.so/ld-linux/libSystem/
KERNEL32/api-ms-win/ucrtbase/VCRUNTIME；有 llvm 时再断言 `llvm-nm -u` 空 + ELF `NeededLibraries []`；
③ 解析优先级（env/prebuilt/local/无，含路径映射）；
④ **真执行**：WSL + python3 ctypes 加载入库的 linux-x64 `.so`，pooled+bufA 与 wasm **逐字节相同**
   （本机唯一能真跑非本平台产物的通道；win32-x64 那份由 `tests/native-parity.test.ts` 在生产入口真加载
   + attestation 3/3 覆盖；darwin/*-arm64 只能靠节点首用 attestation）。
⑤ **云机通道**：`nn-training/tests/test_ts_code_pack.py`（4 例：manifest 每个目标都进包且字节数/可执行位对、
   `.ts/.wasm/native-conv.ts/native-prebuilt.ts` 仍在包、同内容两次打包 sha 相同、缺目录响亮报错）；
   并在 `tests/test_remote_iter_real_bun.py`（真 bun 哨兵）里把 zip 解到临时树、**以它为 cwd 跑 rollout**，
   断言 shard manifest 的 `feat == "native"`（探针验过这条断言是活的：改成期望 wasm 会红）。
   `bun run check` / `bun run build` / `nn-python-gate.sh` 绿。

### §2026-09-23-goalnn-conv-single-source

- **背景**：卷积内核占一步 rollout 的 95%（features ~2.4ms/次），原为**两份实现**：native 纯 C 4oc×4px
  / wasm32 手写 `wasm_simd128` intrinsics 4oc×4px（§368），两者的一致性只靠 `native-parity` 逐字节对拍。
- **备选与否决**：① **全局 8px 单一常量** —— 否：native 白吐 ~5pp（实测 iso 8px 31.8–33.0 vs 16px
  35.1–37.4 GMAC/s；本机端到端 1.47× vs 预计 ~1.5×）；② **wasm 也设 16px** —— 否：wasm 只有
  16×v128 = 64 float 寄存器容量，16px 需 20/16 ⇒ 溢写（实测 locals 373→455 ⇒ 局部变量爆表）；
  ③ **保留两份实现、各自优化** —— 否：同一份算术两份代码 ⇒ 同步链漏编（wasm 漏编 = 静默回落 TS
  = 41ms/forward > 帧预算，仓库记录过的最危险失败模式）。
- **决定**：单源 `src/nn/conv/conv.c`（纯 C，无 intrinsics）编 native 与 wasm32 两目标，差异只有目标
  条件常量 `CF_PW_PX`（wasm 8 / 其余 16）；它**只决定哪些像素进同一条向量寄存器，不改每元素的累加
  次序** ⇒ 两侧输出逐位相同（`native-parity` 是它的守卫）。wasm 产物与 6 个 native 目标**同入** prebuilt
  manifest，`--cross` / `--check-prebuilt` 一次抓全漏编；ABI 统一为单 blob + 4 参 `cf_student_features`。
- **违反后果**：把 `CF_PW_PX` "简化"成单一常量 ⇒ native 白丢 ~5pp 或 wasm 溢写（两者都有实测数字）；
  任何**改动累加次序**的"优化"都会让 `native-parity` 红——那是语义变更（新 era），不在本决策范围内。

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §66→§1 · 旧 §95→§2 · 旧 §124→§3 · 旧 §125→§4 · 旧 §126→§5 · 旧 §127→§6 · 旧 §131→§7 · 旧 §136→§8 · 旧 §140→§9 · 旧 §141→§10 · 旧 §142→§11 · 旧 §143→§12 · 旧 §144→§14 · 旧 §145→§15
