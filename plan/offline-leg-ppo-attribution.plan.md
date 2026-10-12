# Plan: offline-leg-ppo-attribution — 自主盘（offline leg）完成的 PPO 轮计入节点贡献统计

> **交付物**：本 plan + 落地代码（hub / worker / dashboard 三处）。
> **状态**：**已批准**（2026-10-12 用户裁决：① 可读名用 `kaggle-o`；② 历史 602 行**不修**）——首轮评审
> 的 F1–F6 已全部并入本版（见 §0 的修订表）。
> **路径基准**：全文路径相对**仓库根**（`D:/github/battle2`），例：`dashboard/src/server/contribution.ts`、
> `nn-training/hub/store_offline.py`。规矩见 `AGENTS.md`，本文不重复。
> **触发**（用户 2026-10-12 报障）：「kaggle 自主 worker 今天完成了 600 轮 ppo 任务，
> 却未显示在 dashboard 页面的节点贡献统计数据里。请排查。」
> **定位**：补 `DECISIONS §2026-10-06-goalnn-offline-leg-sampling-exclusion` 那条例外的
> **前半句欠账**，不是新 bug、不是独立重构。**不**实现 `plan/dashboard-ppo-live-rows.plan.md`（R1/R2/R3，待批准）。

## 0. 一句话目标

自主盘（云机领**整段任务包**、跑 `it0..itN`）完成的每一轮，在 dashboard **节点贡献统计 → PPO 组**里
按 **`kaggle-o`** 计入 **1 个 job 等价单位**；采样组不受影响（一加一减正是 §2026-10-06 口径的原意）。

**★ 名字口径（2026-10-12 用户裁决，取代本 plan 初稿的 `kaggle-c`）**：可读名 = 
`worker_tag(offline=True, hub_url=…)` = `<env>-o`（本机 = `local`）——自主腿走的**就是** offline 链路，
名字照链路写。`kaggle-c` 是**同一台盘队列腿**的名字（cloudflared 链路），两条腿链路不同 ⇒ 不并成
一个名字：宁可多一行 `kaggle-o`，也不动链路语义（撞名与 `.worker-id` 持久语义的理由见 §3-B）。

**诚实账（先说清，否则交付会被误读成「数据修好了」）**：

- **历史 602 行拿不回身份（用户裁决：不修）。** `release` 之后 hold 镜像被清空（`"hold": {}`），
  而账本行**从未**写过 worker ⇒ 这 602 行只能以 `(未登记)` 呈现（hub 侧 `run.json.source_dir` 只能
  证明 env=kaggle，机器身份无从复原；**不从 hub 日志人工回填**）。
- **收益从今天之后的第一轮起生效。** 本 plan 要的是「下次看得到」，不是「补上昨天」。

**首轮评审修订（2026-10-12，评审 F1–F6 全部已并入下文）**：

| # | 评审发现 | 处置 |
|---|---|---|
| F1 | §3-B 的 `worker_tag(offline=True, …)` 产 `kaggle-o`，与初稿目标/验收写的 `kaggle-c` 冲突 | 用户裁决：**采用 `kaggle-o`**；目标/验收/C/夹具全部改口径（§0/§3-C/§5/§8） |
| F2 | 漏了 `process` 模式（缺省）的子进程一跳：身份进不了真正 POST 的那个进程 | §3-B 补全链路（`_spawn` env + `deliver_worker`），并明确「最小版不再可接受」 |
| F3 | A 的 hold 兜底来源写错层（`holder_info/hold_of` 是队列层，store 拿不到） | §3-A 改为 store 自己的 `hold_meta` 镜像；并钉住新键要排在 `**row` **之后** |
| F4 | C 的行号（`:170/214`）指的不是 PPO 行的 `(未登记)` 约定；「别 import `web/view`」与既有 import 冲突 | §3-C 改正落点（`WorkerContribution.tsx:213`）与 import 事实 |
| F5 | 单位口径数字（600 vs 602）、`iteration` 行是否同步带身份 | §0/§1/§3-A 写明；`iteration` 行**不动**（附理由） |
| F6 | 文档落点（AGENTS §5 要求新号置顶）与「先钉生产值的用例」 | §4 改为新开 `console.md` §37 + §23 留指针；§5 补生产值用例 |

## 1. 现场证据（已核实，可直接引用）

hub 日志 `tmp/hub-server.out`：

```
[07:22:57] offline-claim x24-stack7-dist worker=3908d414c3ca-217c4984 takeover=0
[07:22:58] task-pack -> x24-stack7-dist ... GET /offline/task-pack?lease=...
[07:23:24] resume-anchor -> x24-stack7-dist it300
[07:23:45] POST /offline/release?lease=80c6039cce61783c 200
[02:20:17] offline-claim x24-stack7-burst worker=3908d414c3ca-217c4984
```

两门课账本事件直方图（`tmp/<课>/training_log.jsonl`）：

| 课 | iteration | offline_artifact | offline_result | job_pending | job_* 其它 |
|---|---|---|---|---|---|
| x24-stack7-dist | 299 | 301 | 3 | 1 | **0** |
| x24-stack7-burst | 299 | 301 | 3 | 1 | **0** |

`offline_artifact` 的 `it` 范围 = 0..300（每课 **301** 条、无重号）⇒ **301 × 2 = 602 条**账本行
（用户口径的「600 轮」= 去掉各自 it0 基线那一条）。首轮评审独立复算了这两课的全部事件计数
（`run_start 4/3 · forensics 6 · job_pending 1 · offline_artifact 301 · iteration 299 · offline_result 3`），与上表一致。

部署里**实际存在的**可读名（`tmp/hub-server.out` 计数）：`kaggle-c` 2673 · `colab-t` 1350 ·
`aistudio-c` 3459 —— **没有任何 `-o` 行**。⇒ 本 plan 落地后 `kaggle-o` 是**新出现的行**（不是既有行的别名），
这正是「必须自报可读名」的理由：hub 侧只有 claim 身份（`{HOSTNAME}-{uuid8}`），**变不出** env/link。

历史两课 hub 侧存档 `run.json` 的 `source_dir = /kaggle/working/battle-offline/<课>/run` ⇒ 只能证明
**env = kaggle**，机器身份无从复原（历史不补的佐证）。

一行 `offline_artifact` 的实际内容（**没有任何 worker 字段**）：

```json
{"event":"offline_artifact","run_id":"6b2ce0e3312db6d5","it":300,"weights_fp":"a97c96...","ts":1791760957.9}
```

## 2. 三处断链（缺一不可）

| # | 断在哪 | 证据 |
|---|---|---|
| **A** | hub 落 `offline_artifact` 账本行时**不写 worker** | `nn-training/hub/store_offline.py:266-289`：写 `offline-metrics.jsonl` 与 `_append_ledger` **两处**都不带身份 |
| **B** | 自主盘身份与队列腿**两套命名**，不并 | 自主盘 `nn-training/remote/offline_boot.py:1361` = `{HOSTNAME}-{uuid8}`（**无 env/link**）；队列腿 `nn-training/remote/job_lifecycle.py:656 worker_tag()`（`worker.py:920` 用 `offline=(role==ROLE_OFFLINE)`）→ `nn-training/common/env_probe.py:127 worker_name()` = `<env>-{c,t,o}`（本部署实际：`kaggle-c` / `colab-t` / `aistudio-c`） |
| **C** | dashboard PPO 读面**只认 `job_*` 三类事件** | `dashboard/src/server/contribution.ts:98-151 readPpoAttribution` 只认 `job_result_accepted` / `job_completed` / `job_rejected`，且 `:122` `if (typeof jid !== 'string') continue` 把无 `job_id` 的行整行丢掉（offline 行根本没有 `job_id` ⇒ 必被丢） |

**为什么这是已登记欠账、不是新 bug**：

- `DECISIONS.md §2026-10-06-goalnn-offline-leg-sampling-exclusion` 原文口径：
  **「offline worker 完成的轮算节点 PPO 贡献、不算 rollout/eval 贡献」**。当时只实现了后半句
  （`dashboard/src/server/pool-history.ts:819 readOfflineLegs` 从**采样**侧剔除），
  **前半句从未落地** —— 因为 `job_*` 事件压根不存在。本次报障正是那条决策的欠账。
- `plan/dashboard-ppo-live-rows.plan.md:3` 状态 = **待批准（只写计划，未改任何代码）**；
  `:45` 明写「自主盘领整段任务包，不走 job 队列 ⇒ 在 PPO 归属账本里**根本不出现**」，`:53` 列为缺口 R3。

## 3. 落点与改法

### A. hub：`offline_artifact` 行带上 worker

`nn-training/hub/store_offline.py:265-289`（`store_offline_artifact` 里**两处都要写**）：

| # | 落点 | 读方 |
|---|---|---|
| ① | `offline/<run_id>/metrics.jsonl` 那条（`OFFLINE_METRICS_NAME`，**每 run 一份**） | 人工复盘 |
| ② | `self._append_ledger(...)` 那条（= `self.jsonl_path` = 课程账本 `training_log.jsonl`，`store_ledger.py:115`） | **dashboard 读的就是它** |

只改一处 = 面板照样空（②）/ 复盘对不上（①）。

落两个键（字段名与 B/C 严格对齐，别各起一套）：

| 键 | 值 | 来源（优先级） |
|---|---|---|
| `worker` | claim 身份（`3908d414c3ca-217c4984`） | ① `body["worker"]` → ② **本 store 的 `self.hold_meta.get("worker_id")`** |
| `worker_name` | 可读名（`kaggle-o`） | **只有** `body["worker_name"]` ——别拿 claim 身份去填 |

兜底为什么是 `hold_meta`（**不是** `holder_info(course)` / `hold_of(course)`，初稿写错层）：那两个是
**队列层**（`_HubQueue`）的界面，`store_offline` 这个混入只见 `self`（`hub/queue_peer.py` 的声明面里
也没有它们）；而 store 是**每课程一份**（`hub/queue_resume.py:301` 按 `course` 取 `self._stores[course]`
再转发，混入签名本来就没有 `course`）⇒ store 上的 `hold_meta` 天然只代表本课程那门 hold，形状 =
`{"worker_id": w, "last_progress_at": t}`（`hub/store_leases.py:190`，由 `_HubQueue._sync_hold` 推、
`hub/queue_scope.py:247` 写）。补传天然发生在 claim→release 之间 ⇒ 正常路径上它有值。
**这里不判活性**：不回答「hold 还活着吗」（那是派发闸 `hold_blocked` 的活），只回答「这轮是谁推的」。

**★ B 不穿 claim 身份的参**（2026-10-12 裁决，取代初稿的「加一个可空构造参数」）：`worker` 由 ② 兜底，
B 只自报 `worker_name`。理由不是省事，是**版本偏斜**：`offline_boot.py` 每次会话从 GitHub raw 刷新，
而 `run_loop` / `plan_handoff` / `deliver_proc` 来自**代码快照**（`code.zip`，可能很旧）——给那条链加一个
新 argv/env 参数，就是让新引导器去撞旧快照（`argparse` 崩或静默退化；`offline_boot.py::run_one_course`
里 `local_first` 那段注释说的正是这件事）。而 `worker_name` 的计算点（§3-B）与 `run_loop` **同住一份
快照** ⇒ **零偏斜**。

**两者都没有 ⇒ 该键缺席**（不写空串、不写 `null`、不编）—— C 侧按既有 `(未登记)` 口径渲染。

**键的位置：排在 `**(row if isinstance(row, dict) else {})` 之后**（① 那一处，即 `:274` 与 `:275` 之间）。
`row` 是**客户端给的东西**（`offline_deliver._row_for` 把账本行原样转发），排在它后面才是「hub 认定的
身份压过客户端自称」；写在前面的后果是客户端能在 `row` 里塞一个 `worker` 冒名顶替。

老行（无这两个字段）不许补写、不许猜；hold 已清 ⇒ 历史轮也拿不回身份 ⇒ **A 只对未来的轮生效**
（用户裁决：历史 602 行按原样留在 `(未登记)`，不回溯、不从 hub 日志回填）。

`iteration` 行（`_land_round_metrics`，同文件 `:301` → `remote/artifacts.ledger_row_from_metrics`）
**不动**：它是课程侧的**指标**行（曲线的数据点，读方是控制台指标表），而归属是 `offline_artifact` 这条
腿的事；两处都写 = 同一事实两个落点，改天必然漂。

`body` 里两个字段的取值都要**只接受非空 `str`**（其它类型当没给）：体是远端来的字节，一个 dict 落进
账本就会把 dashboard 的读面打红，而那不是本任务要修的东西。

### B. 补传体自报可读名（self-report）

**落点是 `nn-training/remote/offline_deliver.py:852-876` 的 `body` 构造**（`_post_artifact` 里）——
**不是** `offline_boot.py`。已核实：`offline_deliver.py:59-71` 本就 `from common import net_http` /
`from common.protocol import ...` ⇒ **它不在 standalone 名单里，可以自由 import `common`**。
（standalone 名单 = `tests/common/test_common_layer.py:49 STANDALONE_BOOT_MODULES`，成员是
`offline_boot.py` + 兄弟 `offline_deliverable.py` —— 它们在 `code.zip` 落地前从 GitHub raw 拉取，
守卫 `:113 test_standalone_boot_modules_stay_dependency_free`。别往那两个文件里加 `common` import。）

在 `body` 里加一个键（**只 `worker_name`**，claim 身份的来源见 §3-A 的裁决）：

```python
if self.worker_name:
    body["worker_name"] = self.worker_name
```

（**缺省不写**：与 `course` / `lease_token` 同一条纪律——读方把「带没带」当判据，空串会把「没身份」
伪装成「有身份」。）

**名字在构造期算一次、`offline_deliver` 只存字符串**（不在发体时算）：`worker_tag` 住在
`remote/job_lifecycle.py:656` = **L3**，而 `offline_deliver`(L1) / `deliver_proc`(L2) /
`deliver_worker`(L2) 全在它**下面**（层号账本 `tests/helpers/remote_dag.py`）⇒ 那里 import 它是**向上依赖**，
DAG 守卫当场红。计算点定在 `remote/plan_handoff.py::open_run_context`（**L5**，向下 import 合法）：

```python
worker_name=worker_tag(offline=True, hub_url=hub_url),   # 传给 make_deliverer
```

`offline=True` 走 LINK_OFFLINE ⇒ `<env>-o`（本机 = `local`），就是自主腿的实际链路（用户裁决 F1）。
这也让「名字只在**真要建补传器**时才算」（`deliver=False` 或没 hub 时 `make_deliverer` 直接返回
`None`，那次 env 探测根本不该发生）。

**★ F2：必须打通整条构造链。** `OfflineDeliverer` 现在没有这个字段 ⇒ 加一个构造参数 `worker_name: str = ""`。
链有五跳，**漏一处 = 该模式静默不带身份**（正是本次 bug 的形状）：

| 跳 | 位置 | 要做什么 |
|---|---|---|
| ① 装配点 | `remote/plan_handoff.py:387`（`make_deliverer(...)` 调用） | 算 + 传 `worker_name`（**唯一生产来源**） |
| ② 工厂 | `remote/deliver_proc.py:578 make_deliverer` | 加 kwarg；`process` 分支与 `thread`/`sync` 分支**都要**透传（错过工厂 = 参数在链上静默丢掉，与没实现同效） |
| ③ 进程代理 | `remote/deliver_proc.py:104 DelivererProcess.__init__` | 存字段 + `_degrade()`（`:365`）降级线程模式时透传给 `OfflineDeliverer` |
| ④ **子进程一跳（缺省模式！）** | `remote/deliver_proc.py:240 _spawn` 的 `env` + `remote/deliver_worker.py:195` | 照 `HUB_LEASE_ENV` 现成样子：`env[WORKER_NAME_ENV] = self.worker_name`（**空值不塞**）；子进程侧仿 `lease = os.environ.get(HUB_LEASE_ENV)` 读它、传给 `OfflineDeliverer`。**缺这一跳，POST 的根本不是带身份的那个进程** |
| ⑤ 本体 | `remote/offline_deliver.py:132 __init__` + `:873` body | 存字段 + 上面那段 `if` |

`WORKER_NAME_ENV` 常量与 `HUB_LEASE_ENV`（`offline_deliver.py:1066`）同址（**只走 env 不进 argv**：
argv 在 `/proc/<pid>/cmdline` 上全机可读 —— 与 token/租约同一条纪律）。
`_mirror()`（`:475`，**只调 `pending()`/`status()` 的只读镜像**）**不必**带：它从不发体，带一个
假身份反而会让「谁在投递」出现两个答案（测试里显式断言它不发体，见 §5）。

C 的优先级：`worker_name`（可读名）> `worker`（claim 身份）> `(未登记)`。

**为什么不动 claim 身份**（三条，写进注释）：`worker_id_of()`（`offline_boot.py:1347`，产
`{HOSTNAME}-{uuid8}`）喂 `/offline/claim` 与 `.worker-id`，是**进程身份**，必须保持：① claim 身份要
进程级唯一（同机重开会话也撞不上），可读名要**跨会话稳定**（`kaggle-c` 正是靠它当面板列名）；
② `{env}-{link}` 在「两台同 env 的盘」下会撞名 —— §66 的前提是「同一环境不会同时有两台云机」，
而 stack7 两腿**确实**同机并行，撞了会并错行；③ 直接换会破 `.worker-id` 的持久语义
（离线盘跨会话续领靠它认 `mine`）。

**A 的 hold fallback 因此只对「没带 `worker_name` 的老客户端/手动补传」兜底**，正常路径走 self-report。

**别碰的护栏**：`tests/remote/test_offline_deliver.py`（补传体形状，`assert len(seen) == 3 and
all(b.get("lease_token") == ...)` 那类是**单字段**断言，加字段不会红，但顺手看一眼有没有
「整份 body 精确相等」的断言）+ `tests/hub/test_offline_backfeed.py`（hub 承接面）。

### C. dashboard 读面接 `offline_artifact`

`dashboard/src/server/contribution.ts:98-151 readPpoAttribution` 现只认 `job_result_accepted` /
`job_completed` / `job_rejected`。加第三种事件：

- **单开一条分支**读 `event === 'offline_artifact'`（`ts` + 身份），**别塞进现有 `jid` 流程** ——
  `:122` 那个 `if (typeof jid !== 'string') continue` 是 job 腿的语义，offline 行根本没有 `job_id`，
  改它会让两条腿纠缠。新分支写在那个 `if` 的**真分支里**（`jid` 不是字符串 ⇒ 先看它是不是 offline 行，
  是就收下、然后 `continue`）——`ts` 是两腿共用的一段代码，独立循环会把它抄第二份。
- 身份取 `worker_name` → `worker` → `''` ⇒ 由既有 `(未登记)` 口径渲染。**约定住在两个地方，照抄不改**：
  view 层 `WorkerContribution.tsx:73/213`（`{r.worker || '(未登记)'}`）与 fs 层的空 worker 习惯
  （`contribution.ts:170`）；`contribution.ts` 本来就 `import { machineOf, ... } from '../web/view'`（:20-30）
  ⇒ 沿用既有 import，**别**在 fs 层再造一个读面/新占位符。
- **单位口径（先定死再写码）**：文档说 PPO 组「单位：job」，而队列腿 1 job = 1 轮 it ⇒
  **1 条 `offline_artifact` = 1 个 job 等价单位**。每课 301 条 ⇒ 两课 602 与用户口径对得上。
  这是语义决定 ⇒ 必须写进 DECISIONS（§4）。
- 归并仍走既有 `machineOf`（`web/view/contribution.ts:145`，剥 `:pid`；server 侧 `:206/211`
  已在 PPO 聚合里用它 —— `kaggle-o` 无冒号 ⇒ 恒等）。
- **不进采样组** —— role 隔离是用户裁决的硬口径（WC-plan §4.1b）。
- **注意一加一减不是重复计数**：`pool-history.ts:819 readOfflineLegs` 已把这批 it 从**采样**侧剔除，
  现在 PPO 侧把同一批轮次计进来 ⇒ 两条腿一加一减，正是 `§2026-10-06` 口径的原意。
  在 C 的注释里写清，否则下一个人会以为重复计数。

## 4. 记录（§6）

**记一条 DECISIONS**：`## §<日期>-goalnn-offline-leg-ppo-attribution`，锚 `### <同号>`。
三问里至少覆盖：

- **被否决备选**：① 让 dashboard 去读 hub 的 `/admin/offline` progress 而不是加账本字段 —— 否，progress 无身份，且是第二事实源；
  ② 把 claim 身份直接换成 `worker_tag()` —— 否，见 §3-B 的三条理由（撞名 + 破 `.worker-id` 持久语义）；
  ③ 只改 C 让 602 行显示成「(未登记)」 —— 否，用户要的是**归到 Kaggle 这台**，不记身份的份额对定位「哪台在干活」没用；
  ④ 让引导侧把 claim 身份经 argv/env 下传 —— 否，见 §3-A 的版本偏斜（新引导器 × 旧快照）。
- **未来会重犯**：单位口径（1 轮 = 1 job 等价）；老行无身份 ⇒ `(未登记)` 不回溯；历史 602 行不可补（hold 已清）；
  **可读名与 claim 身份永远是两个字段**（想「合成一个」前先读 §3-B）。
- **就近表达不了**：两套命名必须分离的完整理由（进程身份 vs 面板列名）+ 可读名为什么在**构造期**算
  （层号：L3 的 `worker_tag` 不能在 L1/L2 里 import）+ 为什么不从离线引导侧穿参（版本偏斜）。

**文档落点**（AGENTS §5：新条目**置顶**、号 = 当前最大 + 1）：

- `docs/nn/console.md` **新开 `## §37`**（现文件最大 = §36 ⇒ 插在文件顶部、紧踉头部那段指针）：交付面 =
  `offline_artifact` 计入 PPO 组（1 条 = 1 job 等价）+ 两个身份字段的分工 + `kaggle-o` 的来历 +
  历史 602 行为什么留在 `(未登记)` + 两处落点（`metrics.jsonl` / 课程账本）。
- `docs/nn/console.md §23`（PPO 侧「哪个云端 worker 完成几个 job」那段）**加一行指针**到 §37 ——
  **不就地重写**（§23 是 2026-10-02 的现场记录，§5「新号置顶」的本意就是别去改写历史节）。
- `docs/nn.progress.md` + `.workbuddy/memory/YYYY-MM-DD.md` 各一行（§11）。

## 5. 测试（§7：先写红，再改）

- **hub 侧**（`bash tools/githook/nn-py-safe.sh`，**严禁裸 `python -m pytest`**）：
  补在 `nn-training/tests/hub/test_offline_backfeed.py`（真 HTTP；落盘事实与读面各断言一次，照它既有风格）；
  夹具 = 现成的 `_boot(tmp_path)` + `_post(base, OFFLINE_ARTIFACT_PATH, _round_body(...) | {...})`：
  ① 体带 `worker_name=kaggle-o` ⇒ **两处落点**（`<课>/remote-jobs/offline/<run>/metrics.jsonl` 与课程账本
     `<课>/training_log.jsonl`）的 `offline_artifact` 行都逐字带它 —— 只测一个会漏（§3-A 的①②是两处）；
  ② 体带 `worker=<claim 身份>` ⇒ 逐字带上；且 **hold 存在时也不被覆盖**（自报优先）；
  ③ 体不带 `worker` 但**有 hold** ⇒ 落 hold 的 `worker_id`（夹具 `hub.note_hold(COURSE, worker_id="cloud-1",
     token="tok-1")`，`:226` / `:262` 已有两处同形）；
  ④ 体不带 `worker` 且**无 hold** ⇒ `worker` 键**缺席**（不是空串、不是 `null`）；
  ⑤ `worker_name` 只有自报这一条来源 ⇒ 体不带它时**永远**缺席，**不要**拿 hold 的 `worker_id` 去填
     （那是 claim 身份不是可读名，填了面板就多一个 `3908d414c3ca-…` 假列名）；
  ⑥ **`row` 不得冒名**：体里 `row={"worker": "evil", "worker_name": "evil"}` 而 hold 在 ⇒
     账本里仍是 hub 的认定值（钉 §3-A 的键位置：新键排在 `**row` 之后）；
  ⑦ 两种类型噪声不写入：`worker_name` 给 `123` / `{}` ⇒ 当没给（键缺席），不是 `"123"`、不是 `null`。
  顺手确认既有断言（`weights_fp` / `opt_bytes` 形状）没被加字段打红。
- **worker 侧快层**（`nn-training/tests/remote/test_offline_deliver.py`）——**照抄 `:762` 那条的形状**：
  `test_deliverer_carries_course_so_a_multi_course_hub_can_route`（`seen: list[dict]` + `opener` 收
  `/offline/artifact` 的体 + `_deliverer(...)` 夹具）**就是**抓 body 形状的样板，新用例与它并列：
  ① 给了 `worker_name` ⇒ 每一轮的体都带该值；② **没给 ⇒ 键整个缺席**（不是空串：`:743`
  `test_deliverer_omits_course_key_when_unset` 把「hub 拿带没带当判据」说透了，照它的道理办）；
  ③ 工厂也透传：仿 `:783 test_make_deliverer_passes_the_course_through` 补 `worker_name` 版
  （错过工厂 = 参数在构造链上静默丢掉，与没实现同效）——**两种模式都要**（`thread`/`sync` 分支也得给）。
- **worker 侧拉起面**（`nn-training/tests/remote/test_offline_deliver_async.py:375`，真 `Popen` 被猴子换掉、
  只看 `env`/`argv`）：① `env[WORKER_NAME_ENV]` = 给的名；② 没给 ⇒ 该 env **不存在**；
  ③ 名**不进 argv**（同 token 那条纪律）。
- **★ 端到端（真实子进程，F2 的正身）**（`nn-training/tests/remote/test_offline_deliver_proc.py`，仿
  `test_process_mode_pushes_the_backlog_and_exits_cleanly` 的 `_State(tmp_path, hub, worker_name="kaggle-o")`）：
  真起子进程打真 HTTP stub ⇒ `hub.artifact_bodies()` **每一份**都带 `worker_name == "kaggle-o"`；
  不给名时**每一份都不带键**。只算在父侧（只快层绿）= 这个 bug 换个形状重现，所以这条**不可省**。
- **dashboard 侧**（`dashboard/tests/worker-contribution.test.ts`，③ PPO 归属那一节，照 `withPoolRoot([], …)` +
  `writeFileSync(join(root, 'k25', 'training_log.jsonl'))` 的现成夹具写）：
  ① 一条 `offline_artifact` 带 `worker_name=kaggle-o` ⇒ 计入 PPO 组、份额分母 +1；
  ② 只有 `worker`（claim 身份）⇒ 显示该身份（`machineOf` 恒等，无冒号）；
  ③ 两者都无 ⇒ `worker === ''`（渲染 `(未登记)`），**不吞掉该轮**；
  ④ 与既有 `job_completed` 混合 ⇒ 各自计数正确、`machineOf` 归并生效（同名的 job 腿与 offline 腿合成一行）；
  ⑤ 它**不**出现在采样组（role 隔离，WC-plan §4.1b 不许破）；
  ⑥ 单位口径：同课 3 条 `offline_artifact` ⇒ `totalDone` +3（1 条 = 1 job 等价）。

## 6. 门禁（§9，全绿才算交付）

```
bun run check                          # 根项目
cd dashboard && bun run typecheck && bun run test
bun run build                          # shipping gate
bash tools/githook/nn-py-safe.sh -m pytest \
  nn-training/tests/hub/test_offline_backfeed.py \
  nn-training/tests/hub/test_hub_job_store_split.py \
  nn-training/tests/remote/test_offline_deliver.py \
  nn-training/tests/remote/test_offline_deliver_async.py \
  nn-training/tests/remote/test_offline_deliver_proc.py \
  nn-training/tests/test_remote_dag.py \
  nn-training/tests/common/test_common_layer.py
```

- `test_hub_job_store_split.py`：A 改了 `store_offline`（方法集表 / 无状态断言 / `ALLOWED_IMPORTS` 那几张表）
  ⇒ **必须**跑（它才是「没把六个混入拆坏」的尺子）；
- `test_remote_dag.py`：B 新增一条边 `plan_handoff`(L5) → `job_lifecycle`(L3)，靠它证仍严格向下；
- `test_common_layer.py`：护栏回归（B 没往 standalone 名单里加 import，跑它证没破）；
- `test_offline_deliver_proc.py` 是**慢层**（真子进程 + 真 HTTP），F2 的正身住在那里。
60s/用例上限、`-m pytest` 另套 480s 外墙钟。**别加 `-n`/`--parallel`**（§5：门禁的 worker 数一律 = 物理核，
launcher 已代劳）。

## 7. Windows 编辑纪律（§17，本仓两次栽在这）

- 多 hunk 改动写临时 `.py` 脚本 + 逐 hunk `assert old.count(...) == expected`，全命中才写盘；脚本放仓库内，跑完删。
- hunk 文本不进 shell；输出 CJK 重定向到文件或显式 `encoding='utf-8'`（控制台是 gb2312）。
- 用托管 runtime 的绝对 python 路径，别写裸 `./.venv`。
- 只 `pwsh`，禁裸 `powershell`（DECISIONS §323）。
- 写完验证落盘：语法 / 构建 / grep 锚点。

## 8. 交付时必须回答（答不出的说明没做完）

1. 新的 `offline_artifact` 行里两个身份字段分别是什么值？拿哪条真实课账本或测试产物贴出来。
2. dashboard 节点贡献统计里，`kaggle-o` 的 PPO 完成数是否 +602（或在夹具里等价值证）？
3. 采样组有没有被这次改动污染（role 隔离）？
4. 历史 602 行为什么仍是「(未登记)」或为何不需要补 —— 一句话理由。
5. 今天之后新跑的一轮，能不能端到端归到 `kaggle-o`（含 `machineOf` 恒等那条 + `process` 模式那一路）？

## 9. 不要顺手做的

- 不实现 `plan/dashboard-ppo-live-rows.plan.md` 的 R1（inflight 带 course/it）、R2（prefetch 上报）—— 那是另一个**待批准** plan。
- 不改派发 / 租约 / 避让任何语义。
- 不动节点页的采样组口径。
- 不回溯改写历史账本行。
- 不 `git push`、不 `git stash`、不用 `git add -A`（AGENTS §5）。

