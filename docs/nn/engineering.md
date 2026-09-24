# NN 工程与门禁 — 技术档案

> 测试纪律 / 编码契约 / 门禁耗时 / 账本与指标 schema。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。
>
> **编号说明**：`§20` 是本文件的「决策正文归档」节（搬自 `DECISIONS.md`），**进度节从 §21 起**
> （新条目置顶、号大）。

---

## §23 神模块拆分第一步：`loop_steps` 的传输/发布簇搬进 `loop_transport`（2026-09-23，用户指令「重构 nn-training：降低耦合 / 复用代码 / 提可维护性」）

### 一句话

`rl/loop_steps.py`（2328 行）里混着**两种东西**：`TrainingSteps` mixin（单轮结算与梯度步）
与**19 个模块级自由函数 + 2 个异常类 + 4 个常量**（课程/rollout 源解析、transport 选择、
hub 推送、节点 failover、kickstart 系数、远端可重试异常集合）——后者**没有一个是方法**，
只是历史上「从 `loop_core.py` 拆出」时按大小切、没按职责切。S4 第一步把整簇**零逻辑改动**
搬到 `rl/loop_transport.py`，`loop_steps` 只留门面 re-export（2328 → **1812** 行）。
门禁 **2255 → 2262**（+7 守卫用例）全绿；同日第二步再拆远端 PPO 腿，第三步拆 `hub_server` 的
admin 控制面，并为第四步（`worker.py`）先铺好**模块级状态契约**安全网（+13 例），第四步把
wire 簇搬进 `remote/wire.py`（+8 例）、HTTP 传输核心搬进 `remote/http.py`（keystone，+7 例）、
作业工作区/TAR/git 物化搬进 `remote/job_fs.py`（+6 例）、BC 作业搬进 `remote/bc_job.py`（+6 例，
底座拆完后业务簇可整块搬）、下载簇搬进 `remote/download.py`（+7 例）、作业取活/生命周期/
回传面搬进 `remote/job_lifecycle.py`（+8 例，seam 最密的一刀）、`remote/` 依赖账本收口（+18 例）、
拆掉账本里唯一那个延迟环（+11 例）、拆宿主（+14 例）——终值 **2374 passed / 3 skipped**。
`remote/worker.py` 3450 → **1281**（拆各簇；`wire` 312 / `http` 366 / `job_fs` 184 / `bc_job` 422 /
`download` 313 / `job_lifecycle` 682 / **`train_core` 516** / **`worker_proc` 153**）。
`remote/run_loop.py` **1451 → 491**：半离线执行引擎（963 行）下沉成 `remote/plan_run.py`
（**1035 行**，L2）——见下「拆环」节；worker 的宿主见「第九刀」节。

### 先量结构，再选刀口（拆前侦察，都是实测）

| 神模块 | 行数 | 形状 | 模块级可变状态 |
|---|---|---|---|
| `remote/hub_server.py` | 3972 | 3 个千行状态类 | 有 ⇒ 风险高，**不在首刀** |
| `remote/worker.py` | 3475 | 68 个顶层函数，有水平缝 | 只有 1 个懒建 opener |
| `rl/loop_steps.py` | 2328 | 1 个 **1715 行**类 + 19 个顶层函数 | **零** ⇒ 最安全起手 |

刀口选在 `loop_steps` 的**模块级函数簇**（43–611 行），不是类内部：① 零模块级可变状态；
② 整簇逐字可搬（不碰任何 `self` 语义）；③ 类只**调用**它们，搬走后由门面接管名字。

### 最大的坑：DI seam 是**模块全局**，且同名 seam 有**两份**

本模块的依赖注入靠**模块全局**（测试 patch 它们），于是「搬函数」= 「换命名空间」：

```
dist_common · _push_submit · _push_wait_result      ← 被测试以 rl.loop_steps.X 注入
```

函数一搬走，它就去 `loop_transport` 里找这些全局 ⇒ **旧 patch 目标静默失效**：测试会绿，
而注入根本没生效。更微妙的是**同一个全局名在两处都是注入点**：

| 调用者 | 读哪个命名空间的 `_push_submit` | patch 目标 |
|---|---|---|
| `_push_job_round`（自由函数，**已搬**） | `rl.loop_transport` | `rl.loop_transport._push_submit` |
| `TrainingSteps._push_submit_first` 的**闭包** / `_push_fetch` 直读（类方法，**未搬**） | `rl.loop_steps` | `rl.loop_steps._push_submit`（**不变**） |

所以 e2e `test_push_mode_integration.py` 里：测 `_push_job_round` 的三个 patch 目标迁到
`rl.loop_transport.*`，而测 `TrainingSteps` 方法的 `test_push_publish_phase_…`（`st._push_submit_first`）
**留在** `rl.loop_steps.*`。**这不是重复定义，是两个各自真实的注入点**——由守卫钉住两边都存在。

**漏网教训**：`import rl.loop_steps as ls; ls._push_submit = …`（`tests/test_job_fail_report.py`）
**不含字面量** `rl.loop_steps.`（少了那个点），按「patch 字符串」grep 的清单抓不到它——
是**全量门禁**点名的（`test_push_round_promotes_node_failure_over_retryable` 红）。
⇒ seam 清点必须比「文本 grep patch 目标」多想一层：**模块对象别名也算一个注入点**。

另：`loop_steps` 里 `log`（被 `tests/test_remote_iter.py` patch）与已退休名 `_course_push_url`
**不在**迁走的簇内 ⇒ 保持不动（搬走才会误伤）。

### 门面（`rl/loop_steps.py`）

显式 `from rl.loop_transport import (…25 个名字…)` + **列全的 `__all__`**——没有 `__all__` 时
ruff 的 F401 会把「有意的 re-export」判成「漏删的导入」。`__all__` 里**也含私有名**
（`_gpu_push_nodes` 等）：`from X import Y` 不看 `__all__`，而全仓无人 `import *`，故这纯粹是
给 linter 的意图声明。先例同 §22（`game_watch` 门面用显式清单、不用 `import *`）。

### 被否决的备选

| 备选 | 否决理由 |
|---|---|
| 把 `dist_common` / `_push_*` 改成**参数注入**（一次做完） | 牵动 20+ 调用点与类方法；而下一步「拆那个 1715 行类」还要动同一批函数 ⇒ 两个高风险重构叠加。先拿到「零逻辑改动的搬迁」增量 |
| 把两份同名 seam **规范化成一份** | 等于同时改注入语义 + 搬文件；且类方法确实需要自己的注入点。改为**显式记录成契约** |
| 删掉门面、全仓 `import` 改指 `loop_transport` | 20+ 调用点 + 4 个测试文件的 patch 目标一起改，diff 大而无行为收益；门面 = 零成本 |
| 一次拆三个神模块 | 违反「每次只动一件事」；`hub_server` 还有模块级可变状态 |
| 顺手把类的**方法组**也切出去 | 方法体内的 `self.X` 与模块全局混合，切法不机械；本轮只做「模块级函数整簇搬迁」 |

### 验证与回归防线

- `tests/test_loop_transport_split.py`（**7 例**）：定义只在 `loop_transport`（`loop_steps` 里
  **不得**再有同名顶层定义，防「就地补一个」）· `TrainingSteps` 仍在 `loop_steps` · 门面 re-export
  是**同一对象**（`is`）· 门面名在 `__all__` 里 · `loop_transport` 不反向 import `loop_steps`（门面不得成环）·
  **seam 功能性断言**（把 `loop_steps` 的 seam 换成「一读就炸」，`_push_job_round` 仍应跑完）·
  类方法的 seam 仍在 `loop_steps`。
- **seam 反向探针**（先于测试跑过）：patch `loop_transport._push_submit` ⇒ 生效；
  patch `loop_steps._push_submit` ⇒ 对「已搬的自由函数」**无影响**（证明迁移到位）。
- 落盘验证：ruff + mypy 绿（362 源文件）；`import rl.loop_steps, rl.loop_transport` 与门面 `is` 同一性手工确认；
  迁走块内的陈旧路径引用一并同步（如 docstring 里的 `loop_steps kick_live` → `loop_transport`）。
- 门禁：**2262 passed / 3 skipped / 0 failed**，26s。

### 违反后果

- 有人把传输/发布函数**搬回** `loop_steps`（或就地再定义一个）⇒ 守卫红（这正是它存在的理由）。
- 新写 `rl/*.py` 偷偷 import `remote.push_client` 而不登记 ⇒ `tests/test_layering.py` 的
  声明式快照 `RL_ORCHESTRATION` 红（`loop_transport` 已是其中一员）。
- 若把 seam 只留在一边、却让两边共用一个函数体 ⇒ 注入静默失效（绿而无效），本节的守卫会点名。

### 第二步（同日完成）：远端 PPO 腿 13 方法搬进 `rl/loop_remote.py`

首簇搬完后接着拆那个 **1715 行 / 33 方法**的 `TrainingSteps`。先量三件事（AST）：

| 事实 | 值 | 含义 |
|---|---|---|
| 类规模 | 33 方法 / 1715 行 / **67 个声明实例属性** | 真正的耦合是**共用 `self.*`**，不是模块全局；混入切法**不消除**它（它本就是同一个 `TrainingLoop` 的状态） |
| 模块全局共读 | `log` **22 个方法** · `time` 9 · `RemotePpoJob` 7 · `Path` 6 | ⇒ 不能按「谁用 `log` 谁搬」切 |
| 测试真注入点 ∩ 方法读取 | 仅 **4 个**（`_push_submit` / `_push_wait_result` / `kickstart_coef` / `resolve_transport`） | seam 面比首簇小得多 |

**切法与方向（与初版设计不同，理由在下面）**：远端 PPO 腿 **13 方法 / 862 行（整类的 50%）**
—— 发布 → 领取 → 三重校验落位 → failover → 事件落账，覆盖 `kind=run` 半离线段与 `kind=iter`
整轮上云 —— 搬到新 `rl/loop_remote.py::TrainingRemote`。

```
_remote_ppo_publish(302) · _remote_run_segment(127) · _remote_ppo_land(116) · _remote_iter(74)
_remote_ppo_step(46) · _push_submit_node(34) · _push_fetch(25) · _abort_node_failure(21)
_remote_ppo_fetch(13) · _remote_ppo_probe(12) · _push_submit_first(10) · _remote_ppo · _handle_remote_failure
```

切法的三个根据都是量出来的：① **入口单一**——外界→簇只有 `self._remote_ppo` 一条
（`run_training` 调用），簇→外界只有 5 个小助手；② **内聚理由真实**（一条链 vs 评估/报告/日志）；
③ 新模块可直接 `from rl.loop_transport import ...`（首簇的收益开始兑现）。

**方向修正（相对最初设计）**：最初写的是「给组合类加一个基类
`TrainingLoop(RoundSteps, TrainingRemote, TrainingSteps, TrainingGuards)`」。看实际调用方向后改成
**`class TrainingSteps(TrainingRemote)`** —— 簇的唯一入口 `_remote_ppo` 是**被 `TrainingSteps`
其余方法调用的**，所以「调用者依赖被调用者」就是这个方向。收益是爆炸半径小一个量级：
**组合类不变 · 零 MRO 变化 · 4 个「继承真混入」的测试宿主（`_Stub(TrainingSteps)` 等）一行不改**。
（反向回调 5 个助手靠 `self.*` 在组合实例上动态解析——混入的常态。）

**mypy 的两个坑（都是「混入状态契约必须逐文件可见」）**：

1. 9 处 `attr-defined`：那 5 个留在 `TrainingSteps` 的助手（`_commit_journal` / `_ensure_ts_code` /
   `_forensics` / `_per_stage_quota` / `_volume_plan_block`）必须在新文件里声明——按本仓既有先例
   （`TrainingSteps._ledger_apply: Any`）声明为 `Any`。
2. 7 个属性**从未被显式声明过**（`_code_zip_path` / `_ts_code_zip_path` / `_demo_raw` / `_wire` /
   `_bundle_index` / `_code_sha256` / `_collect_child`），只在方法体里自赋值。其中
   `_ts_code_zip_path` 是「**非簇**方法 `_ensure_ts_code` 赋值、**簇**方法 `_push_submit_node` 读」
   —— 跨类读到时 mypy 会判「TrainingRemote 无此属性」⇒ 必须在 `TrainingRemote` 里补声明
   （一律 `Any`）。同一判据也筛掉了 `_evalboard_idle`：它是 `TrainingLoop` 的**方法**，不是属性。

**seam 收敛了**（首簇时是「两份同名 seam」，这次只剩一份）：`_push_submit` / `_push_wait_result`
在本簇搬走后，`rl/loop_steps.py` **连 import 都没有了**（ruff F401 亲手证实）⇒ e2e 那两处
patch 目标从 `rl.loop_steps.*` 迁到 `rl.loop_remote.*`（否则 `AttributeError`）。守卫因此新增
一条机械形式：「`loop_steps` 命名空间里不得再有这两个名字」。

- 守卫：`tests/test_loop_transport_split.py` 新增 5 例（13 方法只在 `TrainingRemote` /
  `TrainingSteps(TrainingRemote)` 且 MRO 第 2 位 / `loop_remote` 不 import `loop_steps` /
  它直接用 `rl.loop_transport` / 方法体真的读本模块 seam），并把过时的一条换成上述机械断言。
- 分层快照同步：`loop_remote` 加进 `RL_ORCHESTRATION`（它直接 import `remote.push_client`）。
- 规模：`loop_steps.py` 1812 → **952** 行（连首簇共 2328 → 952）；`loop_remote.py` 984 行。
- 门禁：**2266 passed / 3 skipped / 0 failed**，26s；mypy 364 源文件绿。

### 第三步（同日完成）：`hub_server` 的 admin 控制面9 方法 → `remote/hub/admin.py`

先量后动，**实测否掉了原计划的初判**：

| 顶层节点 | 行数 | 占比 |
|---|---|---|
| `_JobStore`(1002) · `_HubQueue`(1033) · `HubHandler`(1343) | 3378 / 3974 | **85%** |
| 5 个顶层纯函数（`_is_ip_literal` / `attributed_source` / `_is_loopback` / `_write_bytes` / `_deterministic_fill`） | **58** | 1.5% —— 原计划想先撇它们，收益太小，不单开一轮 |

推荐首刀改为 `HubHandler`（49 方法 / 1343 行）里的 **admin 控制面 9 方法 / 218 行**（停机恢复 ·
课程热切 · 队列与状态 · push-worker 清单 · net-probe）。三条依据都是量出来的：① `HubHandler`
**只有 3 个类属性**（`hub` / `push` / `_blocked_logged`）⇒ 本组方法近乎无状态，搬迁不改语义；
② 本组只往外调 4 个通用助手（`_auth_ok` / `_bytes` / `_json` / `_query_course`），反向只有
`do_GET` / `do_POST` 的 `self._admin_*` 派发；③ ✭ **测试接缝为零**——全仓对 `remote.hub_server`
的 patch 只有一处（`SEND_TIMEOUT_SEC`，不在本组），`tests/` 从它取的名字全是
`_JobStore` / `_HubQueue` / `as_hub` / `make_server`，本组一个都没被外部 import。

方向：`class HubHandler(AdminRoutes, BaseHTTPRequestHandler)`（组合类依赖混入，派发表调用它）。

**两种环的坑（都已核实并避开）**：

1. **名字成环**：admin 组读 `NET_PROBE_MAX`（顶层常量）与 `_deterministic_fill`（4 行函数），
   二者都在 `hub_server` 顶层。若留下而由新模块 import ⇒ 与「`hub_server` import `admin` 拿混入」
   成双向环 ⇒ **必须随迁**。已 grep 证实两者**全仓无其它读者** ⇒ 随迁后**不需要门面**。
   同理 `import random` 只为 `_PROBE_BLOCK` 存在，一并迁走（否则恰下 F401）。
   （顺带摸清：`_write_bytes` / `_is_ip_literal` 也无外部读者；但 `_is_loopback` / `attributed_source`
   被 `tests/test_hub_auth_d9_order.py` 直接 import ⇒ 它们若搬必须留门面——两者都不在本组。）
2. **类型遮蔽（新坑，其实比名字成环更险）**：混入里为 `headers` / `rfile` 声明类型时写了 `Any`，
   而 `AdminRoutes` 在 MRO 里**早于** `BaseHTTPRequestHandler` ⇒ `Any` 会**盖掉**类型库的精确类型，
   使组合类里 `self.headers.get(...)` / `self.rfile.read(n)` 的推断拓成 `Any`，进而让 `hub_server`
   里做 `-> str` / `-> bytes | None` 的两个方法报 **`no-any-return`**（症状在 hub_server，病因在混入）。
   ⇒ 混入里必须**逐字照抄类型库**：`headers: email.message.Message` · `rfile: BufferedIOBase` · `path: str`。
   （同族的教训：前两刀里 `self.*` 声明用 `Any` 是安全的，因为那边没有「基类已提供同名精确类型」这层。）

**测试侧的意外收获**：新守卫文件里写了带引号的 `\"remote.hub_server\"`（用做 import 边对账）——
恰好命中 `tests/test_subproc_util.py` 的「起服务必须借端口」**源码守卫的标记**（那个标记就是
带引号的点分路径，代表 patch 目标）。我的文件确实不起服务（用进程内 stub），所以改为**叶子名**
判据（语义等价）而不是去假装借端口；同时确认 admin 路由的**端到端**已被既有
`tests/test_multi_course_hub.py` 覆盖（`/admin/queue` · `/admin/courses` · `/admin/workers/halt|resume|status`），
无需重复造 HTTP 用例。另：组合类断言改用 **AST 看基类顺序**（与运行期 `__mro__` 等价），
既更贴合本仓源码守卫的风格，也避免了那个标记。

- 守卫：`tests/test_hub_admin_split.py`（**8 例**）：9 方法只在 `AdminRoutes` · 基类顺序
  `(AdminRoutes, BaseHTTPRequestHandler)` · 两个 net-probe 支撑名已随迁（且 `random.` 不再出现在
  hub_server）· `remote/hub/` 不 import `hub_server` · 确定性填充的不变量（固定种子 / 64KiB 块重复 / 长度）·
  下行越界 400 与合法回 N 字节 · 上行越界 **413** 且分块读尽 · 新模块无模块级可变状态。
- 规模：`hub_server.py` 3974 → **3728** 行；新 `remote/hub/{__init__,admin}.py` 302 行。
- 门禁：**2274 passed / 3 skipped / 0 failed**，26s；mypy 366 源文件绿。

### 第四步前置（同日）：`remote/worker.py` 的**模块级状态契约**（拆之前先把静默故障变响）

前三刀拆的都是**类**（`TrainingSteps` / `HubHandler`），刀口能靠「哪些方法互相调用」量出来。
`worker.py` 不同：它是一整片**顶层函数**（68 个）——顶层函数与「模块级可变状态」**同生共死**。
把 `_wire_*` 搬到 `remote/worker/wire.py`，它们读的就是**新模块的**全局 ⇒ `_WIRE` / `_BULK`
变成两份互不相干的账，而测试里那些 `W._WIRE.clear()` / `W._BULK.reset()` **照旧绿**。
这就是前三刀反复撞上的同一类故障，只不过这次会**一次撞上 6 个**。

实测清点（AST）后补的安全网：`tests/test_worker_state_contract.py`（**13 例**）—— ① **清点不许
漂移**（顶部可变容器 / `global` 重绑 / 顶层有状态实例各一张手工清单，且每个名字必须真被读到，
防清单变僵尸）· ② **别处不许有自己的副本**（扫 `remote/**/*.py`，状态名不得在第二个模块再绑
一次；`tailscale_boot._BEST_RATE` 走显式豁免表——那是独立引导模块的同名不同物）· ③ **行为可
复现**（`_wire_flush` 的账行同序列跑两遍**逐字节相同** · `_wire_bucket` 写进模块那份 `_WIRE` 且
封顶 `WIRE_MAX_JOBS` · 新建账的 `sched0` 取自模块那份 `_BULK`——即「子模块各拿一个调度器」的
探测器 · `_note_rate` 会话语义 · `_warn_non_200` 的 60s 节流与 `log=None` 不占窗，**后者此前零覆盖**）。

**两条可复用的手法**：① 「同序列跑两遍、逐字节相同」这类断言，不要重写一份被测逻辑到测试里
（那只是在测自己的副本）——要挑**真实结算路径**（这里是 `_wire_flush` 打出的那行账）做对账；
② 反向探针不是可选项：往 `remote/` 放一个 `_WIRE = {}` 的探针文件，守卫应**立刻点名**
`remote/_probe_state.py::_WIRE`，删即回绿——否则这条守卫可能根本是瞎的。

门禁 **2287 passed / 3 skipped**。6 处状态与刀口建议见 `plan/nn-training-refactor.md` §5.3.3。

### 第四步（同日）：wire / 低速重抽 / bulk 节流簇 → `remote/wire.py`（**状态随簇搬迁**）

刀口 = `worker.py` 里那一整簇「传输账 + 低速重抽 + bulk 节流」：`WIRE_*` 阈值 7 个 ·
状态 3 份（`_WIRE` / `_BEST_RATE` / `_BULK`）· 15 个自由函数（`set_bulk_log` / `_bulk_pace` /
`_note_rate` / `_min_rate` / `_reroll_decision` / `WireSlowError` / `_wire_bucket` … `_wire_flush`）。
新 `remote/wire.py` **289 行**；`worker.py` **3450 → 3245**。

**唯一的新决策：状态随簇搬迁，宿主做显式转发。** 前置侦察里留的那个问题（「留宿主让子模块
import」还是「随簇搬迁」）答案很硬——留宿主就只能靠**延迟 import**（`wire` 读 `worker` 的全局 =
反向边，`worker` 又 import `wire` 拿函数 = 环），而且顶层函数读的是**导入时绑定的那个名字**：
`_note_rate` 用 `global` **重绑** `_BEST_RATE`，一旦跨模块就只改自己那份。所以 `wire.py` 是这三份
状态的**唯一所有者**，`worker.py` 只留 `from remote.wire import … as…` 的转发。

**收益：注入点的分裂只有一处，而且分得很干净**

| 状态 | 可变方式 | 注入点 | 为什么 |
|---|---|---|---|
| `_WIRE` / `_BULK` | **原地**（`.clear()` / `.reset()`） | 任意入口皆是同一对象 | 转发名指向同一个 dict / 调度器 ⇒ `worker._WIRE.clear()` 照旧有效 |
| `_BEST_RATE` | **重绑**（`global`） | **只能** `remote.wire._BEST_RATE` | `worker._BEST_RATE = 0.0` 只换转发名，`_min_rate` 读不到（**静默**） |

这比前三刀都干净：只动**一个**测试文件的一处 seam（`tests/test_wire_reroll.py` 的 autouse
fixture 改重绑 `remote.wire._BEST_RATE`），另外 4 个直接 `.clear()` / `.reset()` 的测试文件
**一行不改**。

**守卫跟着换宿主（不是删掉）**：`tests/test_worker_state_contract.py` 改成 **owner-aware**
（13 → **15 例**）：清单按宿主分成 `WORKER_*` / `WIRE_*` 两组 · 「别处副本」扫描跳过两个宿主 ·
新增 `test_worker_reexports_are_the_same_objects`（**必须 `is` 同一对象**——这才是「两份账」
的正面判据）与 wire 侧容器清点；`test_wire_reroll` 的 seam 语义写进 fixture docstring。
另加 `tests/test_wire_split.py`（**6 例**）：定义唯一（搬走的名字不许在 `worker.py` 里再实现）·
`wire` 不得反向 import `worker`（环）· 每个名字都是**同一对象**的转发 · `_WIRE` / `_BULK`
跨两个入口仍是**一份账** · `wire.py` 顶层可变容器只许 `_WIRE` · **注入点口径**（重绑
`worker._BEST_RATE` 不改判据、重绑 `wire._BEST_RATE` 才改）。

**两个小坑**：① `wire.py` 有 `__all__`（一个顶层 `ast.List`），状态清点的「可变容器」判据差点
把它记成新共享状态 ⇒ 判据跳过 `__` 前缀；② 转发导入必须写成 `from remote.wire import x as x`
（ruff 只看自别名才认这是**有意 re-export**，否则 F401 报「未使用导入」）。

**反向探针（判据是活的）**：往 `remote/` 放 `_WIRE: dict = {}` 探针 ⇒ 状态守卫点名
`remote/_probe_wire.py::_WIRE`；往 `worker.py` 追加一个 `def _wire_flush` ⇒ 拆分守卫报
`{'_wire_flush'}`；两处复原即回绿。门禁 **2287 → 2295 passed / 3 skipped**，mypy 369 源文件绿。

### 第五步（同日）：`remote/worker.py` 的 HTTP 传输核心 → `remote/http.py`（keystone）

刀口 = worker 里**所有**业务功能的公共底座：`_opener` + `_get_opener` · `BODY_*` 阈值 ·
`_read_body` · `_POLL_WARN_AT` + `_warn_non_200` · `_request` · `_sched_headers` ·
`_get_with_retry`（**281 行**，含 2 处状态）。`worker.py` **3290 → 3030**；新 `remote/http.py` 366 行。

**为什么先拆它而不是先拆业务（这一刀选得比前四刀更关键）**：实测 BC 簇（`_bc_*` ×7 +
`normalize_ppo_device` + `resolve_bc_seed` + `_run_bc_job`）**零模块级状态**，本可以直接搬——
但它整簇站在 `_request` 上，而宿主里另外 18 个函数（作业生命周期 / 下载 / 结果回传）也站在
同一原语上。先搬业务 = `worker ⇄ bc_job` 成环，延迟 import 只是「把环藏起来」（本仓
`test_layering.py` 头部就记着这种旧账）。先下沉底座后，依赖变成 `http ← wire ← worker`（DAG），
BC / 下载 / 作业生命周期三组从此都可选送。

**依赖方向核实过**：`http.py` 只依赖 `remote.wire`（bulk 让路 / 重抽判据 / 传输账）与
`remote.bulk_sched` / `common.protocol`，**不** import `remote.worker`（有守卫钉住）。

**这一刀最有价值的发现：seam 要按「**调用点解析在哪个命名空间**」分档**。测试里
patch `worker._request` 的有 20+ 处，但它们分两类，而且**两类都得留**：

| 调用点 | 解析在 | patch 目标 | 例子 |
|---|---|---|---|
| `_get_with_retry` / `_read_body`（已搬） | `remote.http` | **`http`** | 下载族（`download_*` 走 `_get_with_retry`） |
| 直调 `_request` 的宿主函数 | `remote.worker` | **`worker`**（**不动**） | `post_result` / `peek_jobs` / `claim_job` / `heartbeat` / `_bc_fetch_resume` |

只迁了 3 个文件的 7 处（`test_wire_reroll` 4 · `test_body_transfer_guard` 3 · `test_remote_ppo` 3，其中
`download_*` 那几处都是同一原因）。**教训**：上一刀我按「patch 后调宿主函数就不动」粗分，结果
漏了「宿主函数**内部**走 `_get_with_retry`」这一类——它们是宿主的外形、传输层的里子，
只有跑门禁才点得出来（就是 `test_download_payload_records_its_segment`）。

**守卫**：`tests/test_worker_state_contract.py` 改成**三宿主**（`worker` / `wire` / `http` 各一张
清单，`_POLL_WARN_AT` / `_opener` 归 `http`）· 新 `tests/test_http_split.py`（**8 例**：定义唯一 /
不得反向 import / 转发同一对象 / 状态一份 / 顶层可变容器只许 `_POLL_WARN_AT` / **两个方向的
注入点口径各一条**——「patch http 成功而 worker 是炸弹」与「patch worker 成功而 http 是炸弹」/）。
反向探针两处都命中。门禁 **2295 → 2302 passed / 3 skipped**；mypy **371** 源文件绿。

### 第六步之一（同日）：作业工作区 / TAR / git 物化 → `remote/job_fs.py`

刀口 = `REPO_ROOT` · `JOB_DIR_KEEP` · `_persist_result` · `prune_job_dirs` · `unpack_opt_tar` ·
`pack_opt_tar` · `unpack_payload_or_fail` · `_git_head` · `_ensure_commit`（**128 行**）。
`worker.py` **3030 → 2915**；新 `remote/job_fs.py` 184 行。依赖 `job_fs ← worker`（与 `http`/`wire`
同形；守卫钉住不得反向 import）。

**为什么它是 BC 的前置**：`_run_bc_job` 除 BC 簇外只依赖两个非 BC 的宿主名——`d14_corpus_match`
（本可自 import）与 `_persist_result`（**`run_job` 也在用**）。把「作业 I/O 」这类**非 BC 专属**
的东西先放到下面，BC 才能干净搬（否则 `bc_job ⇄ worker` 成环）。

**这一刀是五刀里唯一 seam-free 的**：全仓对 `prune_job_dirs` / `unpack_payload_or_fail` /
`_persist_result` 的引用都是**直接调用**（无 `setattr`）⇒ `worker` 的显式转发就够，测试一行不改。
代价是它小（128 行），但换来的是下一刀的干净。

**两个值得记的点**：

* `REPO_ROOT` 是**由 `__file__` 推导**的常量（`Path(__file__).resolve().parent.parent`），换目录后
  必须推导出**同一个** nn-training 根——这是搬这类常量唯一会出的错，而且静默。守卫用
  `(REPO_ROOT / "remote" / "worker.py").exists()` + `== ROOT` 两面钉住。
* 顺手发现：`_ensure_commit` **全仓零调用**（只有 `_git_head` 被它自己调）——是既有的死代码。
  本刀**只搬不删**（删代码要单开一次并有决策），已登记为后续候选。

**守卫**：`tests/test_job_fs_split.py`（**6 例**：定义唯一 / 不得反向 import / 转发同一对象 /
`REPO_ROOT` 仍指向 nn-training 根 / 顶层无新增可变容器 / `prune_job_dirs` 的 `keep` 缺省仍绑
`JOB_DIR_KEEP` 且 `run_job` 仍以常量显式调用）。反向探针：往 `worker.py` 追加 `def prune_job_dirs`
⇒ 立刻点名。门禁 **2302 → 2308 passed / 3 skipped**；mypy **373** 源文件绿。

### 第六步之二（同日）：BC 作业 → `remote/bc_job.py`

刀口 = **一整块连续 363 行**：`_bc_fetch_resume` · `_bc_local_resume_dir` · `_bc_store_local_resume` ·
`_bc_load_local_resume` · `_bc_post_epoch` · `_bc_device` · `normalize_ppo_device` ·
`resolve_bc_seed` · `_run_bc_job`。`worker.py` **2915 → 2579**；新 `remote/bc_job.py` 422 行。

**为什么能一刀切完**：BC 簇在文件里**本来就是连续的**，且它对外只经两个向下依赖——
`remote.http._request`（第五步已下沉）与 `remote.job_fs._persist_result`（第六步之一已下沉）。
这正是前两刀的意义：底座拆完，业务簇就变成一块可以整块搬的代码。依赖 `bc_job → {http, job_fs}`（DAG）。

**本刀也是 seam-free 的**（全仓对 `_bc_*` / `normalize_ppo_device` / `resolve_bc_seed` / `_run_bc_job`
只有直接调用与 `from remote.worker import …`，无 `setattr`）⇒ `worker.py` 的显式转发就够，
`e2e/test_bc_epoch_e2e.py`（取 `_bc_post_epoch` / `_bc_fetch_resume` / `_run_bc_job`）与
`tests/`（取 `normalize_ppo_device` / `resolve_bc_seed` / `_bc_device`）**一行不改**。

**新守卫把一条老规矩第一次机械钉住了**：`tests/test_bc_job_split.py`（**6 例**）中的
`test_torch_stays_a_deferred_import_inside_run_bc_job` —— **顶层零 torch**（hub 侧与协议单测
不得拉 torch）必须是 `_run_bc_job` **函数内**的延迟 import；它同时断言「顶层确实没有」与
「函数体内确实有」（后者防「漏搬」）。其余五例：定义唯一 · 不得反向 import（且模块级只许
`common.protocol` / `remote.http` / `remote.job_fs`，仓内依赖白名单）· 转发同一对象 ·
顶层无新增可变容器 · `d14_corpus_match` 两边是同一个函数对象。

反向探针两处：往 `worker.py` 追加 `def normalize_ppo_device` 被点名；往 `bc_job.py` 顶层加
`import torch` 被点名。门禁 **2308 → 2314 passed / 3 skipped**；mypy **375** 源文件绿。

### 第七刀（同日）：下载簇 → `remote/download.py`

刀口 = `_progress_logger` · `download_payload` / `download_code` / `download_ts_code` /
`download_blob` · `_cache_blob` / `_resolve_blob` · `_ensure_ts_code`（**252 行**）。
`worker.py` **2579 → 2348**；新 `remote/download.py` 313 行。依赖 `download → {http, wire,
bulk_sched}`（全向下；`BODY_*` 从 `remote.http` 取单一定义）——实测本组**零跨组函数依赖**。

**这一刀的核心是「注入点分档」的第一次真正双向验证**：

| 调用点 | 解析在 | patch 目标 | 现有测试 |
|---|---|---|---|
| 组内互调：`_resolve_blob` → `download_blob`（`_ensure_ts_code` → `download_ts_code`） | **`remote.download`** | **`download`** | `tests/test_remote_ppo.py` 的 2 处（已迁） |
| 宿主：`run_job` / `_prefetch_fill` → `download_*` | `remote.worker` | **`worker`**（**不动**） | `test_soft_hold_prefetch` / `test_remote_ppo` 缓存命中用例 |

前者是「搬函数的刀里第一次出现**组内互调**」——前几刀的子模块都是一片叶子（只被宿主调），
所以「转发就够」；本组里 `_resolve_blob` 是 `download_blob` 的调用者，两者一起搬后就换了命名空间。
守卫把**两个方向**都钉住：一条证明「patch `download.download_blob` 生效而 `worker` 是炸弹」，
另一条用 AST 证明「宿主把 `download_*` 当**裸名字**用」——后者是警报：哪天宿主改成
`download.download_payload(...)`，现有 patch 会静默失效。

**另一处附带修正**：`tests/test_common_layer.py` 有一条钉「`_progress_logger` 全仓恰好两份」
的守卫（它是**有意的孪生**，tailscale_boot 要独立拉取）——它写死了 `remote/worker.py`，
随本刀改为 `remote/download.py`；新守卫里也自包一份同样的断言。

反向探针：往 `worker.py` 追加 `def download_payload` ⇒ 定义唯一被点名；往 `remote/` 放第三份
`_progress_logger` ⇒ 孪生计数被点名。门禁 **2314 → 2321 passed / 3 skipped**；mypy **377** 源文件绿。

### 第八刀（同日）：作业取活 / 生命周期 / 回传面 → `remote/job_lifecycle.py`

刀口 = **一整块连续 543 行 / 17 个顶层名**：`peek_jobs` · `request_priority` · `claim_job` ·
`_priority_rank` · `acquire_job` · `job_started` · `job_ready` · `abandon_job` · `job_status` ·
`start_cancel_watcher` · `post_result` · `release_job` · `worker_tag` · `_failure_detail` ·
`job_body_error` · `report_job_failure` · `heartbeat`。`worker.py` **2348 → 1805**；新模块 682 行。
依赖 `job_lifecycle → {common.protocol, common.text, http, wire, bulk_sched}`（全向下，零 `worker`）。

**这是 S4 里 seam 最密的一刀**（全仓对这簇有 60+ 处 `monkeypatch.setattr`）。切前先把 seam
**逐点定档**成一张表，判据只有一条——**看调用点解析在哪个命名空间**：

| 调用点 | 解析在 | patch 目标 | 结果 |
|---|---|---|---|
| 宿主（`worker_loop` / `run_job` / `_prefetch_fill`）→ `acquire_job` / `job_ready` / … | `remote.worker` | **`worker`** | 不动 |
| 簇内互调：`acquire_job` → `peek_jobs` / `request_priority` / `claim_job` / `_priority_rank`；`start_cancel_watcher` → `job_status`；`report_job_failure` → `worker_tag` | **`remote.job_lifecycle`** | **`job_lifecycle`** | 迁 14 处 |
| 本簇直调 `_request` / `_wire_add` / `_bulk_pace` / `_sched_headers` / `_warn_non_200` | **`remote.job_lifecycle`** | **`job_lifecycle`** | 迁 8 处 |

**★ 本刀新增的判据：「引用即接缝」**。审计初版只把「裸名字**调用**」当成调用点，于是把
`worker_loop` 里的 `ResultUploader(upload=post_result, …)` 判成「worker 里已无 `post_result`
调用点 ⇒ 那 9 处 `patch remote.worker.post_result` 全失效」。**错了**：它是把 `post_result` 当
**值**读一次 `worker` 的模块全局，patch 照旧生效。审计脚本改成看 AST 的 **`Load`**（任何引用）
之后，全仓真正的空操作注入点只剩 **1 处**——而且正是 `test_http_split.py` 里那条**故意**的
反例（「patch `worker.BODY_PROGRESS_MIN_SEC` 是打偏的」）。这条判据已写进新守卫。

**★ 顺带修掉一条既存的顺序敏感守卫**（本刀实测到，HEAD 上同样可复现）：
`tests/test_http_split.py::test_worker_forwards_every_moved_name` 原来对**所有**搬走的名字断言
`is` 恒等，包括 `_opener`——而 `_opener` 是 `http._get_opener` 里 `global _opener` **重绑**的
懒建单例，`worker._opener` 注定停在 import 时的快照。于是红绿取决于**文件顺序**：
`pytest tests/test_priority_schedule.py tests/test_http_split.py` 红、单跑该文件绿（全量 xdist 下
恰好绿，所以此前没被发现）。改为「重绑式标量只查名字在」+ 一条**与顺序无关的语义断言**
（重绑只发生在 `http`、`worker` 侧无 `global`）—— 语义用例同时把「`_opener` 的注入点只能是
`remote.http`」钉住。

新守卫 `tests/test_job_lifecycle_split.py`（**8 例**）：定义唯一 · 不得反向 import · 转发同一
对象 · 顶层零可变状态与零 `global` · **档位二功能性**（`acquire_job` 只 patch `job_lifecycle`
时成功、worker 侧全放炸弹）· **档位一功能性**（`_prefetch_fill` 必须走 `worker.peek_jobs`，
有界线程 + 记数）· `_request` 一族在 worker 已无调用点（警报）· 「引用即接缝」保住
`post_result` 的 9 处 patch。反向探针四处全部命中（重复定义 / 反向 import / worker 里冒出
`_request` 调用点 / 顶层可变容器）；复原回绿。门禁 **2321 → 2331 passed / 3 skipped**；
mypy **380** 源文件绿；根 `bun run check` 2120 pass / 0 fail。

### 收口：`remote/` 内部依赖账本（同日，用户指令「把散在各子模块的『不得反向 import』断言收成一张 DAG 对账」）

前八刀把「新模块不得反向 import `remote.worker`」这句话在**六个**拆分守卫里各写了一遍（其中三个
还各带一份 `ALLOWED_IMPORTS` / `PROJECT_ROOTS`）。同一件事写六遍的坏处不是啰嗦而是**漂移**——
§22 里首版 `test_layering` 就是「只给 `remote` 展开子模块、没给 `rl` 展开」而**静默变瞎**。

现在收到 `tests/helpers/remote_dag.py`（账本 + 判据实现）与 `tests/test_remote_dag.py`（整图对账）：

```
LAYERS           remote/ 全部 35 个生产模块的层号（8 层，数字越小越底层）
DEFERRED_CYCLES  唯一允许的环（仅限延迟 import，必须写明理由）
```

层号是**拓扑秩**（从叶子往上的最长路径），所以它读起来就是架构：L0 原语/叶子 → L1 单层传输/落盘
→ L2 `http`/`push_client` → L3 业务簇（`bc_job`/`download`/`job_lifecycle`/`push_dispatch`）→
L4 组装宿主（`worker`/`hub_server`）→ L5 入口编排（`run_loop`/`notebook_runtime`/…）→ L6 引导 →
L7 `notebook_boot`。

钉住的八条：账本**恰好**覆盖（增模块不给层号红 / 删了还留着也红）· **顶层**边严格向下（顶层环
= 启动即 `ImportError`，无豁免）· **延迟**边同样严格向下（延迟 import 不是「可以往回指」的许可）·
全图的环**恰好**等于 `DEFERRED_CYCLES`（多一个红、少一个也红）· **环里不许出现顶层边** ·
三个自包含引导模块**顶层零 `remote.*`**（这条以前只是 `__init__.py` 里的注释）· 解析不出的
`remote.*` 目标必须为 0（堵住「判据瞎了但全绿」）· 用**合成源码**自证检测器活性。

**实测发现一件事**（不是新造的，是既有事实第一次被看见）：`remote/` 内部**真的有一个环**——
`remote.run_loop ⇄ remote.worker`。两边都是**函数内延迟 import**：`run_loop._real_run_job` 为了
顶层保持 torch-light（`worker` 拖整条 torch 链），`worker.run_job` 则是「编排入口不是宿主的依赖」。
顶层图仍然是无环 DAG，所以它**不构成故障**；本轮的处置是把它**登记**成唯一被批准的延迟环
（`DEFERRED_CYCLES`，带理由）+ 一条警报「环里不许出现顶层边」，而**不是**顺手改代码拆它
（改哪边都得把 `verify_plan_file` / **`run_job` 的调用方式**搬动，属单开的决策）。
**（同日该决策已单开并执行：环已拆掉，`DEFERRED_CYCLES` 现为空 —— 见下「拆环」节。）**

**反探针六处全命中**（每一处都指名到唯一的用例）：顶层反向 import → 顶层分层红（整图 + 单模块
两处）· 同层延迟边 → 延迟分层红 · 环里出现顶层边 → 分层 + 「环里不许有顶层边」两处红 ·
新模块不登记 → 账本覆盖红 · `http` 顶层/延迟碰 `rl` → 单模块守卫红 · 摘掉声明环的两条边 →
`stale` 红。门禁 **2331 → 2349 passed / 3 skipped**（+18）；mypy **382** 源文件绿；
根 `bun run check` 2120 pass / 0 fail。

### 拆环：半离线执行引擎下沉 `plan_run`，`worker` / `run_loop` 都从上面拿（同日，用户指令
「拆掉 `remote/` 里唯一那个被登记的延迟环」）

上节把 `run_loop ⇄ worker` **登记**成唯一被批准的延迟环，并写下两条拆除路线。本步执行它，
并且**两条路线都没照原样走**——量完之后发现它们都不够：

| 原路线 | 为什么不照做 |
|---|---|
| 把 `verify_plan_file` / `run_plan_job` 沉到 L1（如塞进 `remote/job_fs.py`）让 `worker` 直接拿 | 这两个函数的**传递闭包就是整个执行引擎**（`RunContext` + 单轮执行 + 主循环 + 云机评估装配 ≈ **963 行**）。塞进 184 行的作业 I/O 模块 = 把一个 L1 模块变成第二个神模块 |
| 让 `worker.run_job` 从调用方收这两个函数 | 方向对了（**注入**）但对象错了：这两函数只是引擎的**门面**，真正要注入的是「**一轮怎么跑**」（`run_job` 自己） |

**实际刀口**：引擎**整块**搬到新模块 `remote/plan_run.py`（**1035 行**，L2），`run_loop.py`
只留 CLI / 独立续跑 / 门面（1451 → **491** 行）；「一轮怎么跑」由调用方**注入**：

```
plan_run（L2；不 import worker / run_loop）      ← 引擎：计划交接 + 运行上下文 + 单轮 + 主循环
   ↑                          ↑
worker（L4，kind=run 尾巴）  run_loop（L5，CLI / 独立续跑 / 门面）
   └─ run_job_fn=run_job ─┘ └─ run_job_fn=_real_run_job ─┘
```

**引擎里那个 `_real_run_job` 兜底被删掉**（它就是环的成因：引擎替调用方决定用谁的 job 执行器）。
漏注入时**响亮** `RuntimeError`，而不是静默跑错执行器——三个直接调用点（`worker.run_job`、
`run_standalone`、以及测试里那批替身）**全都显式注入**，所以删兜底不影响任何人。

#### ★ 选层：层号是**算**出来的，不是贴上去的（用户口径「沉到 L1」为何落成 L2）

用户口径是「下沉到 L1」。仲裁依据不是口味而是账本的**拓扑秩**定义（`LAYERS[m] = 1 + max(依赖层)`）：
`plan_run` 顶层依赖 `offline_deliver`(L1)、延迟依赖 `offline_eval`(L1) ⇒ 它**只能是** L2；标 L1 就是标错，
而且会让 `worker → plan_run`(L4→L1) 与「`http`(L2) → `wire`(L1)」这类的秩关系自相矛盾。
本步顺手把这条**加成了守卫**：`test_every_layer_number_equals_its_topological_rank` 对全部 36 个模块
逐条验算 `1 + max(依赖层)`——今天全绿（即：不只是 `plan_run`，整张 `LAYERS` 表都真的是秩）。
反探针：把 `plan_run` 手改成 L1 ⇒ 该用例 + 两条分层用例同时红（顺带证明「沉到 L1」若按字面执行，
真正该做的是**再把共同依赖往下拉一层**）。

#### seam（本刀是「拆环」而不是「拆文件」，所以分档第一次是**跨模块**的）

| 名字 | 调用点解析在 | patch 目标 |
|---|---|---|
| `iter_spec` / `pairs_for` / `time` / `DRAIN_FLUSH_SEC` …（引擎读的模块全局） | `remote.plan_run` | **`plan_run`**（`run_loop` **不再转发** `iter_spec` ⇒ 打错模块是 `AttributeError`，**响亮**而非静默失效） |
| 引擎公开名（`run_plan_job` / `verify_plan_file` / `RunContext` / `_drive` …） | `run_loop` 只做 `X as X` 门面 | 取名字可以，**patch 无效**（同一对象） |

实迁 **1 处** `monkeypatch.setattr`（`tests/test_run_loop.py`：`run_loop_mod.iter_spec` →
`plan_run_mod.iter_spec`）。其余全部**一行不改**：测试都走 `from remote.run_loop import …` 门面，
`e2e/` 与 `tests/test_volume_plan_block.py` 亦然。

#### 守卫（新 `tests/test_plan_run_split.py`，10 例）+ 反探针

定义唯一（引擎名只在 `plan_run`，`run_loop` 不得再实现）· 入口名不得倒灌进引擎 · **`plan_run`
不得 import `remote.worker` / `remote.run_loop`（含延迟）** · **兜底 `_real_run_job` 必须不存在**
（AST 三层：名字零 `Load`、`_run_iteration` 里 `run_job` 赋值只能有一处且值恰为 `ctx.run_job_fn`、
漏注入的 `RuntimeError` 文本在）· `worker` 尾巴延迟 import **引擎**且 `run_job_fn=run_job` ·
门面同一对象 · **门面不许漏**（从入口源码动态取读到的引擎名）· `iter_spec` seam 在 `plan_run`
且测试跟着迁了 · 账本 `DEFERRED_CYCLES == {}` 且全图零环 · 分层关系 `plan_run < worker < run_loop`。

**反探针七处全命中**（每处指名到唯一用例）：`plan_run` 延迟 import `worker` → 4 处红（含零环）·
`worker` 尾巴改回 import `run_loop` → 4 处红 · 拿掉 `run_job_fn=run_job` 注入 → 1 处红 ·
引擎里恢复 `or _real_run_job` 回落 → 1 处红 · 门面漏 `_drive` → 1 处红 · `run_loop` 又转发
`iter_spec` → 1 处红 · `plan_run` 标成 L1 → 3 处红（秩 + 两条分层）。

#### 账本变更

`DEFERRED_CYCLES` 从「一个带理由的环」变成**空字典**，且新增两条硬断言：`undeclared == []`
（= 全图零环，比原来「恰好等于声明值」更严）+ `not dag.DEFERRED_CYCLES`（**要网开一面必须先改守卫**）。
机制（`cycles` / `undeclared_cycles` / `stale`）保留：真要再引入环，它会**归入「已声明」而不是静静绿着**。

门禁 **2349 → 2359 passed / 3 skipped**；mypy **383** 源文件绿；根 `bun run check` 2120 pass / 0 fail。

### 第九刀（2026-09-24）：拆宿主 —— `run_job` 的**训练核**下沉 `train_core`，进程生命周期 → `worker_proc`

上两节把 `remote/` 的依赖账本收口后，`worker.py` 余下的 **1805 行全是宿主**（`run_job` 752 /
`worker_loop` 364 / `main` 127 / `_prefetch_fill` 69）。本刀按**一条真实调用链**切（不是按行数等分）：

```
run_job（作业壳）      网络 / 三道校验 / kind 分叉 / 冒烟回显 / 落盘 / 上报 / 半离线尾巴
   └─ run_training_core() ──► train_core.py（L4）   课程→模型→opt→多卡→ref→demo→PPO→产物（427 行）
worker 的进程生命周期 ─────► worker_proc.py（L0）  监督 / 热替换（exit 86）/ 云机停机（110 行）
_wire_block（两个调用方） ─► wire.py（L1）          M0 wire 子字典（19 行）
```

`worker.py` **1805 → 1281**；`run_job` **752 → 325**（余下全是网络/校验/上报/尾巴）。

#### ★ 先量接口，再下刀（AST 自由变量清点）

搬 427 行最怕的是「漏传一个自由变量」——那些分支（TPU/XLA、cuda-dp、kickstart、demo）在
测试里**跑不到**，漏了就是云机上炸。所以先把块内/块外的**每一个名字**算清楚（AST）：

| 量到的量 | 值 |
|---|---|
| 块内自由变量（必须当参数） | **11** 个块外局部（`jid`/`job_dir`/`manifest`/`blob_root`/`iter_info`/`payload_*`/`unpack_sec`/`ts_code_*`…）+ **9** 个 `run_job` 形参 |
| 逃出去的输出 | **2** 个（`result`、`course`）—— 其余 40+ 个赋值全部块内自用 |
| `global` / `nonlocal` | 0（`_ACTIVE_CODE_SHA` 的重绑在壳里，不随簇走） |
| 测试 patch 过这一族名字 | **0**（`_resolve_blob` / `time` / `pack_opt_tar` / `job_body_error` / `_wire_block` …）⇒ **seam-free** |

**唯一一处语义改动**：核不再拿 payload 字节本身，只拿 `payload_bytes`（`raw` 留在壳里）——
这正是「壳测好的事实下传、训练的活下沉」那条界。**漏传的检查交给了编译器**：ruff `F821`
在生成后当场报出 `payload_bytes=len(raw)` 里那个不存在的 `raw`（实测命中，已修）。

#### 选层：训练核是 **L4**，`worker` 因此升到 **L5**

它依赖最深到 L3（`download`/`job_lifecycle`）⇒ 秩只能是 4（账本 `LAYERS` = **拓扑秩**）。
插入它之后重算全表：`worker` 4→5、`run_loop`/`notebook_runtime`/`worker_server` 5→6、
`offline_boot`/`push_bootstrap` 6→7、`notebook_boot` 7→8（**7 个数字 + 1 个新模块**，机械且被
秩断言/分层断言双向护住）。`worker_proc` 纯 stdlib ⇒ 秩 0。

#### seam / 源文本守卫

本刀**零 `setattr` 迁移**（那一族名字没人 patch）。真正要改的是**读源码文本**的守卫——
它们按行文字面找调用点，代码一搬家就「找不到」（这是好事：**响亮**而不是静默绿）：

| 守卫 | 原来读 | 现在读 |
|---|---|---|
| `test_job_body_crash`（4 处 `job_body_error` 包装） | `worker.run_job` | `train_core.run_training_core` |
| `test_tpu_backend_guard.TestWorkerWiring`（xla 接线 3 条） | `worker.py` | `train_core.py` |
| `test_worker_device`（`torch.device(...)` 实参） | `worker.run_job` | 分叉前归一化仍查壳，两个 `torch.device` 调用点查核 |
| `test_priority_schedule`（取消回调接线） | `worker.py` | `train_core.py`（壳里那份同名 `except` 是**另一件事**） |

#### 新守卫（`tests/test_train_core_split.py`，14 例）+ 反探针

定义唯一（壳里不许再有训练核的调用点，AST 判 `Call`）· **★ 接口双向一致**（调用点的位置实参
个数与关键字集合 == 核的形参集合；漏一个就红——本刀最可能的失误形态）· 核里不得有 `**kwargs`
吞接口 · 核不得 import `worker`/`run_loop`（含延迟）· 顶层零 torch/numpy/ppo **且**函数体内确实有 ·
零模块级可变容器 · `worker_proc` 纯 stdlib + 四个转发名同一对象 + `_request_reload` 抛的正是
`supervise_worker` 认的那个码 · `_wire_block` 定义唯一且在 `wire` · 账本层关系。
**反探针七处全命中**（壳里冒出 `_resolve_blob` 调用 → 1 处红 · 调用点漏传 `log=log` → 1 处 ·
核延迟 import `worker` → 核守卫 + 账本 3 处 · 核顶层 `import torch` → 1 处 · `worker` 重复实现
`_wire_block` → 1 处 · `worker_proc` import `remote.download` → 1 处 · `train_core` 标成 L3 → 秩 +
分层 2 处）。

门禁 **2359 → 2374 passed / 3 skipped**；mypy **387** 源文件绿；根 `bun run check` 2120 pass / 0 fail。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-godmodule-traincore；全文 → 本节。

### 第十刀（2026-09-24）：拆宿主之二 —— `worker_loop` 的「每 job 一轮」下沉 `job_round`（**seam 最密的一刀**）

第九刀留下的宿主里，`worker_loop`（364 行）是一个把**两种东西**织在一起的 while：轮询壳
（多 hub round-robin / 停机感知 / 空闲退出）与**一轮**（177 行：取活已定 → 起三个旁路线程 →
`run_job` → 交回传 → finally 全收）。本刀拿走后者：

```
worker_loop（留 worker.py）  轮询 / claim / halt / idle / --once / 回传收尾（uploader.close）
    └─ run_one_round() ──► job_round.py（L4）  旁路线程组 + run_job_fn + 交回传 + RoundOutcome
配套下沉（原本都只服务这一轮，且依赖最深到 L3）：
    _prefetch_fill（69 行）· settle_result（原 `_result_settled` 闭包）· PREFETCH_WIRE_ID/ROUND_SEC
```

`worker.py` **1281 → 1042**；新 `remote/job_round.py` **418 行**（块 177 + 填充器 69 + 落定 21 +
数据/文档）。**这一刀第一次出现「20 个入参」，也第一次把「注入 vs 迁移」按同一个判据一次分完。**

#### ★ seam 分档：只按「调用点解析在哪个命名空间」定，不按「谁定义的」

先量（AST + 全仓扫 patch），再决定往哪搬：

| 名字 | 宿主里的调用点 | 解析在 | 处置 |
|---|---|---|---|
| `run_job` | 1（`run_job_fn=run_job`） | **`remote.worker`**（读**值**） | **注入**：`run_job` 住 L5，本模块不能 import 它；宿主在调用点读裸名字 ⇒ 那 **10 处** `W.run_job` patch **一行不改** |
| `job_ready` · `abandon_job` · `release_job` · `report_job_failure` · `start_cancel_watcher` | 各 1–2，**100% 在块内** | `remote.job_round` | **迁移**：12 处 `setattr`（4 个文件） |
| `peek_jobs` · `download_payload` · `PREFETCH_ROUND_SEC` | 各 1–3（全在 `_prefetch_fill`） | `remote.job_round` | **迁移**：8 处 `setattr` + 2 处 `W._prefetch_fill` 直接调用 |
| `uploader` · `post_result` | — | `remote.worker` | `uploader` 跨 job 存活（`if once:` 的 drain、最外层 `finally` 的 close/stats）⇒ **宿主建制并传入** |

#### ★ 「不留假门面」第一次成为**断言**

`_prefetch_fill` / `PREFETCH_ROUND_SEC` / `PREFETCH_WIRE_ID` / `settle_result` 是这一轮的**内部结构**，
不是 e2e 直接 import 的门面（`job_ready` 那种才是）。若照惯例在 `worker.py` 留一份 `X as X` 转发，
`setattr(W, "PREFETCH_ROUND_SEC", …)` 就变成**没人读的变量**——测试全绿、注入为零。前几刀把这
类比作「patch 打偏而测试全绿」，本刀把它钉成 **`not hasattr(worker, …)`**（不许留），并在宿主
文档里写明「要拦请 patch `remote.job_round.*`」。

#### 宿主收回的账：`RoundOutcome`

搬前块内直接改宿主局部（`done += 1` / `_polls_since_accept = 0` / CodeChangedError 分支的
`return done`）。搬后这些**只能回读**：`RoundOutcome{jid, ok, uploaded, stop}`——
`uploaded ⇒ done += 1`（原 `_polls_since_accept = 0` 同一条口径），`stop ⇒ 整条退出`。
`multi` 是宿主概念（「有几个 hub」），它唯一的用处是 `code_cache_dir=shared_code_cache if multi else None`
⇒ 由宿主算好传 `code_cache_dir`，本模块**不知道**有几个 hub。

#### 新守卫（`tests/test_job_round_split.py`，13 例）+ 反探针七处

定义唯一 **且宿主不留假门面** · 轮询壳里不许再有「一轮」的调用点（AST 判 `Call`）·
**★ 接口双向一致**（调用点位置实参 + 关键字集合 == 形参集合；20 个入参**漏传一个就红**）·
不得有 `**kwargs` 吞接口 · 不得 import 同层/上层（**含延迟**，且上游集合**从账本推**而非写死）·
顶层零可变状态与零 `global` · **★ 档位一**（`run_job_fn` 必须是裸名字 `ast.Name`）·
**★ 档位二功能性**（炸弹放 `worker.peek_jobs`、记数放 `job_round.peek_jobs`，填充器必须走后者）·
宿主不得属性式访问 `job_round.`/`JR.` · **★ 宿主回读两分支功能性**（假 round：`uploaded ⇒ done==1`、
`stop ⇒ 整条退出`）· 层号关系。

**反探针七处全命中**：留假门面 · `run_job_fn` 改成 lambda · 调用点漏传 `log=log` · 改成属性式访问 ·
延迟 import `worker` · 顶层可变容器 · 账本标成 L3。

#### 顺手修掉的两个**守卫自身的**坑（都是本刀踩出来的）

1. **读源码文本的守卫会与「字面量」互相打架**：`tests/test_subproc_util.py` 的「起真服务进程
   必须借端口」按带引号的字面量扫（`"remote.worker_server"` 是它的 marker 之一）。本守卫原先把
   上层模块列成字面量元组 ⇒ 被误判成「起了 worker_server 真进程」。改成**从账本推**
   （`lv >= mine`）既解了假阳性，又比写死名单更强（新模块自动入列）。
2. **既有守卫里的前缀匹配误伤**：`m.startswith("remote.worker")` 会把合法的 `remote.worker_proc`(L0)
   判成反向依赖 ⇒ 两处改成「按模块名精确比」（`m == u or m.startswith(u + ".")`）。

门禁 **2374 → 2387 passed / 3 skipped**；mypy **389** 源文件绿；根 `bun run check` 2120 pass / 0 fail。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-godmodule-jobround；全文 → 本节。
> ⚠ 踩坑（记进 memory）：反探针**回滚同长度的编辑**时，pyc 的失效判据是 (mtime, size)——
> 秒级 mtime 相同 + size 相同 ⇒ Python 继续用**旧** pyc，于是「已复原」的源码仍报旧结论。
> 探针脚本回滚后必须 `touch`（本刀就因此误判了一下）。

### 第十一刀（2026-09-24）：拆宿主之三 —— `hub_server` 的 25 个路由方法按**域**分四组，再把四组重复的形状收成 5 个助手

第三步已把 admin 控制面（9 方法）拆成 `hub/admin.py` 定下了先例；本刀把**其余全部**路由按域切完，
`HubHandler` 从此只剩「大学用：组合 + 共享助手 + 线程/共享状态」。`remote/hub_server.py`
**3728 → 3017 行**（方法本体 644 行搬走，余下是 `_JobStore` / `_HubQueue` / 引导 / 引导链）。

```
HubHandler(AdminRoutes, ScheduleRoutes, ResultRoutes, BlobRoutes, OfflineRoutes, BaseHTTPRequestHandler)
  schedule（153 行）取活/租约/打点：peek · priority · claim · start · ready · abandon · heartbeat · release
  result  （240 行）回传与终局：result(POST) · fail · status · result(GET) · bc-epoch · bc-resume · bc-metrics
  blob    （ 46 行）字节服务：payload · code · ts_code · blob · shared_code
  offline （205 行）离线段：task-pack · resume · resume_blob · artifact · result
```

#### ★ `result` 那组让宿主升到 **L5**（层号是算出来的，第三次兑现）

`_post_result` 走 `remote.push_dispatch.accept_result`——「推模式与拉模式必须用**同一个**校验函数」
这条纪律（一份对不上账的结果被静默落盘成一轮看起来正常的训练，正是它要挡的事）。于是
`hub.result` 的拓扑秩只能是 **4**，宿主 `hub_server` 被迫 **4 → 5**，`smoke_loopback` /
`tunnel_ab_probe` 随之 5 → 6。`result` 单独住 L4 而其余三组住 L0（与 `hub.admin` 同层），是
**依赖深度**决定的，不是口味：账本的秩断言把标错的当场报出来。

#### Phase B：把重复了 3–5 遍的形状收成 5 个助手（实现只住 `hub_server`）

| 助手 | 收掉的形状 | 原处数 |
|---|---|---|
| `_job_or_404(known=)` | 鉴权 + 取 `job_id` + 404 | 14 |
| `_job_body(cap, known=)` | 上者 + 读小 JSON 体 | 4 |
| `_lease_token()` | `X-Lease-Token` / `lease-token` 双写法 | 5 |
| `_serve_path(p, missing=)` | 定位 → 不存在就 404 说清原因 → `_bytes` | 5 |
| `_read_raw_body()` | 按 `Content-Length` 读满（上限留给调用方） | 3 |

分两 Phase 做是有意的：先**纯搬**（逐字节等价，只改 `self.` 的解析命名空间），再**去重**——
混在一起，一旦哪条端点变味就分不清是「搬错」还是「收错」。

#### 两个语义保留点（`count` 不出来的那类）

- **`known=True` 那一档**：`/start` `/fail` `/result` 要的是「已知 job」——没有它，会往一个
  不存在的 job 打点/落账。`/peek` `/priority` `/claim` `/ready` `/abandon` `/heartbeat` `/release`
  用 `known=False`（`/ready` 更是**故意**：回传就要开始的那份 job 可能还没落 manifest）。
- **顺序**：`_job_body` 先闸后体（写错的 URL 不该让服务端白读一段远端体）；`_serve_path` 的 404
  必须带**具体**原因（`no payload` vs `no ts_code zip` 是两条不同的下一步）。两者都写了功能性断言。
- 鉴权仍然在**没有 job** 的端点内联（`/peek` `/priority` `/shared_code` `/offline/*`）——这批
  端点的共同点是「没有 job_id 可查」，助手帮不上；计数被守卫钉住（多一处 = 又抄了一遍 404 边界）。

#### 新守卫（`tests/test_hub_routes_split.py`，15 例）+ 反探针**十一**处全命中

定义唯一（25 个方法住混入、`HubHandler` 不得再定义）· `HubHandler.X is Mixin.X`（**对象级**接线）·
助手唯一实现 **且各自活着**（`≥N` 调用点，防死助手）· **漂移警报**：混入里不许再出现那四种内联形状 ·
内联鉴权计数与位置 · 混入零上向依赖（`dag.assert_remote_module` 白名单）+ `hub/` 包不反向 import 组装模块 ·
账本层号关系（含 `result`==4 与「宿主 == worker == 5」的对称性）·
**★ 功能性**：`_Probe(hs.HubHandler)`（`object.__new__`，无 socket）走完 `_set`/`_job_or_404`/
`_job_body`/`_read_raw_body` 的两侧契约。

**反探针十一处**：方法搬回宿主 · 混入没接进 MRO · 助手在混入里又实现一份 · 抄回内联租约头 ·
多一处内联鉴权 · 混入向上一层 import · 账本把 `result` 标成 L3 · 助手漏掉 `known` 闸 ·
先读体再判 job · 404 退回通用文案 · 助手不再被调用。

#### 测试侧：**真子类**而不是往实例上挂属性

功能性断言要一个「能跑路由但写不出去」的 `HubHandler`。原方案 `object.__new__` + 实例上挂
`_json`/`_bytes`/`_auth_ok` ⇒ `mypy` 的 `method-assign`（不许给方法赋值）与 `ruff` 的 `B010`
（不许用常量 `setattr`）**互相打架**。改成 `class _Probe(hs.HubHandler)` 重写这三个方法（外加空
`__init__` 绕开 `BaseHTTPRequestHandler.__init__` 的请求处理）：两个 linter 都认，且顺带把
「哪些实例字段是绕过 `__init__` 直接塞的」写在了类注释里。

#### 又一次撞上「读源码文本的守卫」

`tests/test_subproc_util.py` 按**带引号的字面量**扫「起真服务进程」（`"remote.hub_server"` 等）。
本守卫第一版写 `layers["remote.hub_server"]` ⇒ 被误判成起了真进程（第十刀同款坑，第二次）。
修法：按键的**叶子名**从账本取（`_ledger_key("hub_server")`），**连解释这件事的 docstring 里也
不许出现那个带引号的字面量**（`_code_of` 只剥 `#` 注释、保留 docstring）。

门禁 **2387 → 2402 passed / 3 skipped**；mypy **394** 源文件绿；根 `bun run check` 2120 pass / 0 fail。

> 决策 → `DECISIONS.md` §2026-09-24-goalnn-hub-routes-split；全文 → 本节。
> 协议常量 `PRIORITY_BODY_MAX` / `PEEK_MAX` 随迁到 `common/protocol.py`：前者是同一族请求体上限
> （`FAIL_BODY_MAX` / `OFFLINE_*_BODY_MAX` 都住那里），后者有**两个读者**（`hub.schedule` 的端点与
> `hub_server._HubQueue.peek_jobs` 的形参默认值），谁也 import 不了谁 ⇒ 必须住两边都能 import 的协议层。

### 未做完（S4 余下）

`remote/` 内部**已零环**（见「拆环」节），S4 余下是纯结构工作：
`remote/worker.py` 余 **1042 行**（`worker_loop` 已缩到「轮询壳 + 回传收尾」，`run_job` 325 /
`main` 127 是宿主本体）；`remote/hub_server.py` 余 **3017 行**，路由面已全部切完，只剩两个千行状态类
`_JobStore` / `_HubQueue`（拆 = 拆状态，风险与切法都不同）与引导链。
设计见 `plan/nn-training-refactor.md` §5.3。
`TrainingSteps` 本体还剩 952 行 / 20 方法（切法是「按一条真实调用链切」，不是按行数等分）。

**✅ 清理已做（2026-09-24，第十二刀）：`remote/job_fs._ensure_commit` 已删**——第六步之一登记的
既存死代码（全仓零调用，只搬未删）。同时删 `job_fs.__all__` 条目、`worker.py` 的门面转发、
`tests/test_job_fs_split.py` 的清单与 docstring；`REPO_ROOT` 从「`_git_head` / `_ensure_commit` 共用」
缩成「只被 `_git_head` 用」。

---

## §22 断开 `rl` ↔ `remote` 包循环：把纯逻辑叶子下沉到 L0，用测试钉住依赖方向（2026-09-23，用户指令「重构 nn-training：降低耦合 / 复用代码 / 提可维护性」）

### 一句话

`remote/protocol.py`（采样协议）与 `remote/game_watch.py`（对局转播纯逻辑）**模块级零上层依赖**
⇒ 下沉为 `common/protocol.py` / `common/game_watch.py`；新增 `tests/test_layering.py` 把
「`L2 remote → L1 纯逻辑 → L0 原语`」的**单一依赖方向**变成断言，并配一张**会腐烂就会红**的
过渡白名单。门禁 **2247 → 2252**（+5 守卫用例）全绿。

> **同日修订（见文末「同日修订」节）**：第 ④ 步的**注入式接口方案当天被否决**，改为
> 「结构性编排层 + 导出器路径单源化」；白名单换成声明式快照，另补两条性质断言（真切线 /
> 环已断）。**包级环已断**由 SCC 分析证实；门禁终值 **2255 passed / 3 skipped**。

### 循环的现场

S1 结束时测到：`rl/` 有 **10 个文件** import `remote/*`，`remote/` 有 **8 个文件** import `rl/*`
—— 双向。当年靠 `rl/queue.py` 里一处**函数内延迟 import** 维持「能跑」：延迟 import 把失败
从 import 期推到调用期，于是环在启动时看不出来。这就是本仓记过的「延迟 import 掩盖循环」先例。

先做**判据**再动手（否则只是把环换个地方）：AST 扫全部 import 节点（含函数内），量出两件事——

| 事实 | 值 |
|---|---|
| `common.protocol` 的引用面 | 92 处 / **65 个文件** |
| `common.game_watch` 的引用面 | 14 处 / **12 个文件** |
| 三个独立引导模块是否引用它们 | **否**（安全：不触 §21 契约 4） |
| `protocol.py` 模块级可变状态 | **无**（只有常量） |

两条最容易踩的坑，提前钉住：

* **门面不能用 `import *`**：`game_watch.__all__` **漏了** `PROGRESS_LOG_SEC` / `progress_due`，
  而 `rl/queue_local` 正在用 ⇒ `import *` 会静默少导出。故 `remote/game_watch.py` 保留显式
  `from common.game_watch import (...)` 完整清单的门面，`protocol` 同理（别名 re-export 保住
  全部历史名字，30+ 调用点与 `monkeypatch.setattr(mod, "bun_version", …)` 类接缝**零改动**）。
* **多模块混合导入**：原文有 `from remote import game_watch, serve_pool` 这种一行导两个模块的写法，
  机械替换只改了其中一个 ⇒ mypy 才炸。逐个复核修为「`game_watch` 走 `common`、`serve_pool` 留 `remote`」。

### 分层契约（`tests/test_layering.py` 是权威）

```
L0  common/ · platform_utils · pid_probe · dist_common · schema     （stdlib-only，无 torch）
L1  models/ · ppo/ · data/ · train/ · rl/ · scripts/                （纯逻辑）
L2  remote/ · 根入口（run_rl.py / run_bc.py …）                      （传输 / 应用）
```

下沉之后的实测收益（不是设计意图，是量出来的）：

* `ppo/` `train/` `models/` `data/` `scripts/` 对 `remote` 的引用 **归零**（测试断言 `== 0`）；
* `remote/` 依赖 `rl` / `ppo` / `train` / `data` 属**合法方向**（传输层在最上），不动；
* `rl/` 只剩 **4 项过渡白名单**：`remote.bundle`（打离线任务包）/ `remote.hub_client`
  （poll/wait/verify_and_land）/ `remote.push_client`（hub-push 派发腿）/ `remote.serve_pool`
  （`rl/eval_local.py` 模块级 `EVAL_SCRIPT` 常量）——即 `plan/nn-training-refactor.md` §5.2
  第 ④ 步「改注入式接口」的待办。
  > ⚠ **该口径已在同日修订中作废**：`EVAL_SCRIPT` 收进 `common/protocol.py` 后 `eval_local`
  > 回纯逻辑，白名单改为声明式快照 `RL_ORCHESTRATION`（11 个模块），第 ④ 步的注入方案被否决。
  > 以「同日修订」节为准。

守卫做**两件**事，第二件才是关键：① 任何未列入白名单的新边即红；
**② 白名单里某项已无引用 ⇒ 红**（提示删掉）。否则这类清单必然腐烂成「合法的历史遗留」——
那正是白名单最坏的结局。另附机械事实守卫：`protocol.py` / `game_watch.py` 不得再出现在 `remote/` 下。

守卫的两条自纠（都是判据写过头，不是实现错）：① 首版把 `from remote.hub_client import` 误记成
裸 `remote`（违反「只看具体子模块」）⇒ 修；② 修过头，把 `remote.hub_client` **展开**成
`remote.hub_client.x` 兄弟别名 ⇒ 精确为「只有 `from remote import x` 才展开」。

### 被否决的备选

| 备选 | 否决理由 |
|---|---|
| 让 `remote` 反过来 import `rl` 时全部改注入（一次做完） | 涉及 8 个 remote 文件的重签；先拿到**单向可达**的增量并钉住，再逐项改注入（白名单就是为了让第二步可增量） |
| `remote/protocol.py` 保留为薄门面（不删） | 多一层空壳、多一处「到底哪份是真的」；本仓先例是 `pid_probe` 式的**真下沉**，不留壳 |
| 用 `import *` 做门面省事 | `game_watch.__all__` 已漏两项且正被使用 ⇒ 静默少导出 |
| 把分层守卫写成 grep/lint 规则 | 需要**跨文件**判断 + 白名单双向对账，lint 规则表达不了「未使用即红」；且 AST 才看得见函数内延迟 import |

### 验证与回归防线

- `tests/test_layering.py`（5 例）：L0 不得依赖 L1/L2 · L1 不得依赖 `remote`（`rl/` 白名单除外）·
  白名单不得腐烂 · `common/` 是叶子包 · 搬迁模块已离开 `remote/`。
- **断言是活的**：临时写 `rl/_probe_tmp.py` 故意 `import remote.worker` ⇒ 守卫立刻点名
  `rl/_probe_tmp.py -> remote.worker`（验证后已删）。
- 落盘验证：ruff + mypy 绿；grep 残留 `remote.protocol` / `remote.game_watch` = 0（注释里的路径引用
  也一并同步，避免文档说谎）；`tests/test_jobs_next_retired.py` 里硬编码的 `remote/protocol.py` 源码
  路径守卫同步为 `common/protocol.py`。
- 门禁：**2252 passed / 3 skipped / 0 failed**，23s；根项目 `bun run check` 2120 pass / 0 fail。

### 违反后果

- 重新从 `rl/` 里 `import remote.*`（白名单外）⇒ `code.zip` 侧「纯逻辑」包被迫拖入传输依赖，
  云机/无 bun 环境首包即断。
- 把 `protocol.py` / `game_watch.py` 搬回 `remote/` ⇒ 包循环重新出现，测试红（这正是守卫存在的理由）。

### 同日修订（2026-09-23）：第 ④ 步不做注入，改「结构性编排层 + 导出器路径单源化」

原计划第 ④ 步是「把 `rl → remote.{bundle,hub_client,push_client,serve_pool}` 抽成**注入式
接口**」。看清楚依赖形状后**否决了注入**，理由都是量出来的：

| 发现 | 含义 |
|---|---|
| `rl/loop_steps.py` / `rl/loop_guards.py` **外部使用者为零** | 它们不是「被复用的库」，而是 `rl/` 内部的**应用层**；给它们注入一个 client 对象只是把 import 换成字段，换不来解耦 |
| `rl/loop_steps.py` **2328 行**，正是 S4 要拆的神模块 | 在它身上同时做「注入改造 + 拆分」= 两个高风险重构叠加，违反「每次只动一件事」 |
| `rl/bc_loop.py` 的测试 monkeypatch 的是 `remote.hub_client._request` | 改注入要连测试接缝一起改，而**接缝本身就是被验证的契约** |
| `rl/eval_local.py` 对 remote 的**唯一**依赖是 `EVAL_SCRIPT`（一个字符串） | 环的真正入口是「一个 TS 文件路径被抄在传输层」，不是一个需要注入的能力 |

**于是先做了一件小得多、收益却大的事**：把 TS 导出器路径收进 `common/protocol.py`
（`ROLLOUT_SCRIPT` / `EVAL_SCRIPT`，`ROLLOUT_SCRIPTS` 由前者派生）；`remote/serve_pool.py`
只做 re-export，`rl/eval_local.py` 改从 `common.protocol` 取。**一条边消失 ⇒ 编排层从 17 个
模块塌到 11 个**：`eval_local` / `eval_dispatch` / `gate_check` / `batch_eval` / `eval_a_once` /
`eval_replays_once` 原来只是**经由 `eval_local` 间接**碰到传输层，环一断就回了纯逻辑。

**验证环真的断了**（不是靠“看着像”）：跑全仓生产模块图的 **Tarjan 强连通分量**分析——
现在零个 `rl` ↔ `remote` 环（剩下的三个环是 `rl.reward_builtin↔reward_library`（注册表惯用法）、
`remote.run_loop↔remote.worker`（两个神模块，S4 目标）、以及 `rl.loop_*` 编排簇 + `run_rl`
内部互相可达——都不跨包，是包内的下一批目标）。

**守卫改造**：白名单（列举「允许 import 哪个 remote 子模块」）换成**声明式快照**
`RL_ORCHESTRATION`（列举 rl 里哪些模块属于应用层），并把测试从 5 条加到 8 条：

| 断言 | 性质 |
|---|---|
| 快照 vs 「rl 中可达 remote 的集合」**双向对账** | 多一个红、**少一个也红** —— 清单不会腐烂 |
| 纯逻辑 rl 不得 import 编排 rl | **真切线**（否则「纯」名不副实） |
| `remote` 不得（传递地）触及任何编排模块 | **环已断**的机械形式 |
| `ppo/train/models/data/scripts` 不得 import 编排 rl | 采样器/训练器不被间接拖入传输层 |

**⚠ 探针揪出的判据盲点（值得单独记）**：改完先跑反向探针，发现 `P2`（纯逻辑 `from rl import
loop_steps`）与 `P3`（`remote` 里 `from rl import loop_steps`）**都没报错**——因为 `from <pkg>
import <mod>` 在 AST 里只给裸包名，而首版的展开只给 `remote` 开了小灶、没给 `rl`/`models` 展开。
后果：**测试全绿但守卫是瞎的**，而且是那种「下一轮重构时看着有护栏、实际没有」的瞎。
修法：`_subpackages()` 按实存的包子目录统一展开，**不再逐个包开小灶**；四条探针（纯逻辑碰
remote / 纯逻辑碰编排 / remote 碰编排 / 上层包碰 remote）全部命中后才算完。

**仍未做**：把 11 个编排模块**物理搬出** `rl/`（成独立应用层包）——守卫已经用断言代替了这一步，
搬家的收益主要是「import 路径说实话」；它触碰 ~30 个调用点与测试，值得独立一轮。

---

## §21 `common/` 共享原语层：同口径写进注释不算单一实现（2026-09-23，用户指令「重构 nn-training：降低耦合 / 复用代码 / 提可维护性」）

### 一句话

新增 stdlib-only 的 `common/` 包（hashing / proc / fs / text / logutil），把**同名各写 2~4 份**的
原语收敛成唯一实现；上层只保留 re-export 与薄包装，**零行为变化**（门禁 2230 → 2247 全绿）。

### 现场：十二份「同口径」实现

用 AST 扫全仓（按「去掉 docstring 后函数体逐字节相同」聚合），生产代码面命中 12 组重复；
更要紧的是**语义已经漂移**的那几组——漂移是无声的，因为两侧各自自洽、测试各自绿：

| 原语 | 份数 | 漂移形态 |
|---|---|---|
| `sha256_file` / `sha256_bytes` | 3 份 + 1 处 inline | `dist_common.weights_fingerprint` / `remote.artifacts` / `remote.hub_client._sha256_file` / `remote.bundle`（这份还在函数里 `import hashlib`） |
| `bun_version` | 3 份 | 训练机侧失败返 `"?"`（timeout 10）；节点侧失败返 `""`（timeout 30、且要求 `rc==0`） |
| `exc_tail` | 2 份 | `remote/worker.py::_failure_detail` ↔ `rl/stream.py::_exc_tail`，后者 docstring 写着「与前者同口径…**故就地保留同款小助手**」 |
| 原子写 | 2 份 | `remote/artifacts.atomic_write_bytes` ↔ `remote/hub_server._write_bytes`（两份注释都在解释「半截文件比没有文件更危险」） |
| 追加 JSONL | 2 份 | `rl/bc_ledger.append_ledger` ↔ `remote/hub_client._append_ledger` |
| tar 解包 | 2 份 | `remote/worker.unpack_opt_tar` ↔ `remote/hub_client._extract_tar`（都要兼容 Py<3.12 的 `filter=`） |
| `_log_default`（tag 化日志） | 4 份 | 差别只有 tag（`[run]` / `[hub-push]` / `[deliver]` / `[battle-rl]`），格式字面量被抄 4 遍 |
| 子进程捕获 | **13 处裸 `text=True`** | 见 §19——这条正是「封装各写各的」的直接代价 |

「同口径就地保留」那句话就是本节的判决：**口径写进注释不算单一实现**。先例是
`pid_probe.pid_alive`（其 docstring 已有「唯一实现」论证），本层沿用同一模式并把它扩成一条**层契约**。

### 层契约（`common/__init__.py` 是权威，测试守着）

1. **只依赖 stdlib**（外加 `platform_utils`）——本包要随 `code.zip` 解到**没有 torch/numpy** 的云机上；
2. **不得反向 import 上层**（依赖方向永远 `上层 → common`）；
3. **无副作用、无模块级可变状态**；
4. **三个引导模块不得用本包**：`remote/tailscale_boot.py` / `notebook_boot.py` / `offline_boot.py`
   —— 它们从 GitHub raw **单独拉取**，cell 侧在拿到 `code.zip` **之前**就要 `import` 它们。
   它们的重复是**结构性豁免**，不是漏网；用测试把这个事实钉住（谁再「顺手合并」即红）。

### 被否决的备选

| 备选 | 否决理由 |
|---|---|
| 分散合并（`remote/` 内部一份 sha256、`rl/` 内部一份…） | 跨包重复（`rl`↔`remote`、`ppo`↔`rl`）正是漂移发生的地方；包内合并治不了 §19 这类「捕获封装」问题 |
| 塞进 `dist_common` | 它是**采样协议**模块（含网络/threading）；`remote/artifacts` 明确要「纯 stdlib、可单独搬运」的落点，协议模块不是那个落点 |
| 把 `bun_version` 的分歧「统一」掉 | 那是**静默行为变更**（改动某一侧在非零退出码上的返回值）。改为显式形参 `require_zero`，两侧口径各留一行文档 |
| 新文件承载 `record_agent_meta` | 改用 `rl/agent_meta.py`——与 `bc_ledger` / `train_ledger` / `ladder_ledger` 的「一账本一模块」惯例一致 |
| 顺手合并 notebook_boot / offline_boot 的孪生助手 | 违反契约 4（独立拉取 ⇒ 拿不到 `common`） |

### 验证与回归防线

- `tests/test_common_layer.py`（17 例）：单一实现（`_defs_of` 源码扫描 + `is` 同一对象）、
  独立重实现对账（不调被测函数）、§19 编码回归（真子进程写不可解码字节 ⇒ 断言 `stdout` 不为 `None`
  + 中文原样 + 替换符命中）、**AST 源码守卫**（生产代码不得再有裸 `text=True` 捕获）、`code.zip`
  必须含 `common/`、以及「豁免是结构性的」两条（引导模块零依赖 / `_progress_logger` 恰好两份）。
  > 守卫两条注意：① 源码守卫必须走 **AST**——行文本会把 docstring 里引用的 `text=True` 判成缺陷；
  > ② 「恰好两份」而不是「唯一一份」：`tailscale_boot` 不能共享 ⇒ 两份是**正确**的终态。
- 门禁：**2247 passed / 3 skipped / 0 failed**，ruff + mypy 绿，27s。
- 曾观察到一次 `tests/test_bulk_sched.py::test_yield_stops_at_budget_even_if_control_stays`
  在 `-n 12` 下红（约束 `elapsed <= 0.39s`，实测 2.08s）：单跑 3/3 绿、下一次全量绿 ⇒
  **既存的墙钟容差 flake**（与本轮改动无关），本轮未动它。

### 违反后果

- 在 `common/` 里 import `torch` / `rl.*` / `remote.*` ⇒ 云机解开 `code.zip` 即 `ImportError`
  （本机全绿、只有云机炸）。
- 给「顺手把 notebook_boot / offline_boot 合并掉」开口子 ⇒ cell 引导链断在首包之前。
- 重新抄一份 `sha256_*` / `bun_version` ⇒ 账本「同字节同哈希」契约与版本对账重新变成两份真相。

---

## §19 本机评估的子进程捕获：gbk 解码把 stdout/stderr 丢成 None（顺带刷屏 65 行/100 局）（2026-09-22）

`rl/eval_local.py::run_local_eval_game` 的 `subprocess.run(capture_output=True, text=True)` 没给
`encoding` —— win32 中文机 locale = gbk（cp936），而 bun 的输出带 UTF-8 字节 ⇒ 解码在 subprocess
的 **reader 线程**里抛 UnicodeDecodeError。两个后果（不只是日志脏）：

1. 父进程日志被 `Exception in thread ... _readerthread` traceback 刷屏（evalA 实测 **65 次/100 局**
   = 每个本机局一次，与 `nodes:local` 局数逐一对应）；
2. **captured stdout/stderr 直接丢成 `None`**（异常死在读线程、`communicate` 不重抛）⇒ 失败路径的
   `RuntimeError(f"rc={rc} ({stderr[-160:]})")` 只剩 rc，诊断信息全没 —— 响亮错误变哑巴。

修复：抽出 `run_eval_runner_capture(cmd, timeout)`，显式 `encoding="utf-8", errors="replace"`
（`rl/queue.bun_version` 早就这么写，本处是漏网的一个）。复现/回归：
`tests/test_eval_local_capture.py`（真子进程写不可解码字节，断言输出还在 + 中文正常 + 替换符命中；
旧实现在同一命令下 `stdout`/`stderr` 都是 `None`）。实测同一 scratch 课程 100 局：traceback
**65 → 0**，本机局输出全部可读。

⚠ 同类隐患仍在别处（全量 grep `text=True` 尚有 13 处）：`dist_common._git_index_blobs`
（`git ls-files`，默认 `core.quotepath=true` 会把非 ASCII 路径转义 ⇒ 现状安全，但哪天加
`-c core.quotepath=false` 就中招）、`rl/archive.py` 的两处 git 调用同理。**新写 subprocess 捕获
一律带上 `encoding="utf-8", errors="replace"`。**

---

---

## §18 修复：本地 resume 的 D14 判据同源化（accident.plan §2/A，2026-09-21）

**问题**：D14（跨课程语料不混训）有两把尺子——`course_fp` = 课程**文件字节** sha256，
`corpus_fp` = 语料**语义**身份（env+reward 解析值哈希）。远端链路 2026-09-13 就改成语义优先
（`remote.protocol.d14_corpus_match`，hub 打包 + worker 装载共用），但**本地对账**一直只比
文件字节 ⇒ 改一下课程里的预算/路径/注释（字节变、语义不变）就把自己历史的 shard 全判成
异血缘 ⇒ **全量重采**，而云端其实照收（同一个 D14 两份实现的代价）。

**改动**：`_scan_shards` / `completed_pairs` / `settled_stage_totals` / `resumed_manifests`
新增 `corpus_fp`，比较换调 `d14_corpus_match`；`loop_core` 加 `self._corpus_fp`
（`rl.cmd.corpus_fp_for_args`，与远端发布同源）并沿 `rollout_phase` → `stream` / `queue` /
`dispatch` 透传；扫描缓存键加上 `corpus_fp`（否则一份身份的缓存会冒充另一份的答案）。
措辞同步：`_course_file_fp` / `cmd.course_fp_for_args` 改称「D14 **文件**血缘」并把
“语料身份 = corpus_fp”写进同一段 docstring——误诊的源头就是那几句命名。

**验收**：`tests/test_local_resume_lineage.py`（6：语义相同/字节不同 ⇒ 认（不再全量重采）；
语义不同 ⇒ 不认；legacy manifest 无 corpus_fp → 回退字节（旧行为逐字节不变）；缓存键含
corpus_fp；另两个消费点同源；`corpus_fp_for_args == corpus_identity_fp`）；门禁 **1961 passed**。

---

---

## §17 e2e 权重下发账本跨用例撞键：门禁随机 flake 的**残留根**（2026-09-20）

**一句话**：§13 的 `weights_push_cache_reset()` 只治了「同进程顺序跑」，**没治在途线程**——
前一个用例的收尾 `weights-push` 线程可能在下一个用例 reset **之后**才
`note_weights_pushed(...)`；而全仓库的哑权重内容都是 `{"stub": true}` ⇒ `wver`（= **文件
指纹**）完全相同、节点名也同为 `fake` ⇒ 下一个用例的权重下发被判 “kept / skip POST” ⇒
FakeServer 收不到 weights 事件 ⇒ `test_it_stream_smoke` 的 I3 断言
（`bool(wts3) and fired[0] > wts3[0]`）假红。xdist 下取决于「同 worker 的前置用例是哪个」，
这就是门禁里那条**随机 flake**（本轮实测：`e2e/` 单跑 3 次 2 次红，日志指纹
`weights[rollout] -> fake (kept, 0.0s)`）。

### 修法（从根上不撞键，而不是再加一次 reset）

哑权重内容**每个用例唯一**：`{"stub": true, "case": <tmp 目录名>}`。

| 位置 | 改动 |
|------|------|
| `e2e/test_run_rl.py::_itest_env` | 每次写盘的哑权重带 `case` 字段（`tmp_path.name`） |
| `e2e/test_volume_e2e.py`（weights 落盘处） | 同上（`tmp.name`） |

**为什么改内容而不是再补一次 reset**：reset 管不了「reset 之后才落地的在途线程」，只有**键
不同**才与线程时序无关；`weights_push_cache_reset()` 保留作双保险。顺带把「断言不该依赖
进程内跨用例缓存」这条写进注释，避免下一个人再把哑权重改成全同内容。

---

---

## §16 Windows 门禁耗时：TCP 空等 + NTFS 大量小文件（2026-09-20）

**一句话**：同一批用例在同机 WSL <5s、Windows 原生 python 5.5–9.1s 触发 >5s 警告。
根因不是训练变慢，是两类 **Windows 税**：① `127.0.0.1:<closed>` 的 connect 空等
（hub_client 默认 timeout=10/15，不可达路径 ×3 次调用 ≈ 6.2s）；② volume 桩按局数
写 manifest，600000 配额 ×3 个 stub ≈ 3k 次 mkdir+write（NTFS 慢一个数量级）。

### 修法（§14 同口径：把测试侧超时/体量拧小，生产缺省不变）

| 用例 | 病因 | 修法 | 修后（xdist -n 8） |
|---|---|---|---|
| `test_clear_halt_on_startup` | 不可达 hub ×3 次默认 timeout | `clear_halt_on_startup(timeout=)` 透传；测试 0.05s | 0.58s |
| `test_volume_topup_partial_ledger_*` | 每局一个 manifest，文件数爆炸 | 配额 600000→60000（g0=16/w2=8/w3=4），断言同比例更新 | 1.14s |
| `test_bc_train_on_epoch_called_per_epoch` | 真 torch 2 epoch + xdist 抢 CPU | 语料 60→16、batch 32→8（钩子语义不测收敛） | 3.48s |
| `test_bc_train_resume_continues_epoch_numbering` | 同上（两段训练） | 同上 | 0.79s |

`clear_halt_on_startup` 新增 `timeout: float = 10.0` 透传给 `hub_halted`/`set_cloud_halt`
（生产行为不变；测试才有办法在不可达地址上秒败）。

---

---

## §15 pathlib 钩子自证随 Py3.12 改写：accessor 已删、rglob 已并发容忍（2026-09-20）

**一句话**：`tests/test_rl_resume.py` 的竞态钩子在 Python 3.12.13 上红——不是生产回归，
是 pathlib 内部结构变了：① `_NormalAccessor`/`_Accessor` 已删；② `_WildcardSelector._select_from`
对 scandir 的 `except OSError: pass`（pathlib.py:206）⇒ **rglob 本身已并发容忍**，
旧自证「Path.rglob 必抛 FileNotFoundError」变成假红。

### 改写口径

- 钩子绑定点：`os.scandir` +（若存在）accessor 类 + `Path._scandir`（3.12 glob 真正拿走的绑定）。
- `rmtree` 与钩子同走 `os.scandir` ⇒ 加 **re-entrancy 旗标**，否则 `RecursionError`（实测）。
- 自证测试改名为 `test_hook_intercepts_scandir_and_deletes_victim`：断言
  ① hook 拦到 `scandir(victim)`；② 拦截瞬间目录已删；③ 后续真实 scandir 抛 ENOENT。
  —— 下方 walk/resume 绿色仍不可能是「钩子没生效」的假绿。
- `_dir_signature` 断言侧 `\`→`/` 归一（Windows `relative_to` 反斜杠；缓存 key 本机自洽）。

生产修法（`walk_shard_dirs` = `os.walk`）**不变**；本条只是 sentinel 随解释器演进。

---

---

## §14 门禁墙钟 157s → 24s：五个「测试替生产超时白等」的坑 + per-test 耗时预算护栏（2026-09-20）

**一句话**：单测跑成 157~185s（文档基线 ~25s，用户口径「昨天还 <30s」）不是因为工作量变大，
而是因为**多处测试在空等生产超时**——CPU 占用低、墙钟长。逐个定位（`--durations` +
`pytest-timeout --timeout-method=thread` 线程栈）后改回去，并加护栏让这类退化不能再静默回来。

### 定位手法（可复用）

1. `--durations=30` 排序：四个参数化用例各 26.5s、三个相邻用例 15/20s——**整数秒**是 sleep 指纹；
2. 单个用例 `--timeout=8 --timeout-method=thread`：栈里若全是
   `all_settled.wait(...)` / `time.sleep(backoff)`，就是**纯空闲等**（不占 CPU 的那类）；
3. 按「谁配的小节奏没生效」顺藤摸瓜，而不是按「哪个用例慢」逐个改。

### 五个坑（均已修）

| # | 位置 | 病因 | 修法 |
|---|---|---|---|
| 1 | `rl/dispatch.py` 瞬断背压退避 | 上限**硬编码 5s**：每个 502/503/504/超时 白等 5s ⇒ 3~5 次即 15~26.5s/用例 | 提为 policy `transientBackoffSec`（生产缺省 5s 不变），测试 0.02s |
| 2 | `rl/queue_local.py` rescan 节奏 | `next_probe_at` 地板**写死 1.0s**，`recoverPingSec=0.05` 形同虚设 ⇒ 4 轮回场 ≥3s | 地板改为与 wait 同源 0.05s（生产缺省 20s/5s 远大于地板，不受影响） |
| 3 | `remote/push_client.py` 409 重试梯子 | `backoff=min(2**attempt,8)`（2s+4s）被 `test_worker_refusing_job_requeues_to_another` **顺带**跑满 6s | 梯子提为 `PushDispatcher(push_attempts=)`，该用例 `=1`；梯子另用零墙钟用例逐个钉住（sleep 换记录，试满抛 `RetryableError`） |
| 4 | `tests/test_rollout_dispatch_resilience.py` | harness `queueWindowSec=30`：回场上界用尽后无人能结算，三处线程全停在 `all_settled.wait` 直到 deadline（实测 26.5s） | 该用例窗 1.5s（配速旋钮，同 `nodeRecoverFirstSec` 用法）+ 断言 4 局全进 `missing`（喂 volume 补波/resume） |
| 5 | 同文件 `test_halt_stops_round_without_waiting_window` | 用 0.3s 定时器置 halt，隐含「0.3s 时轮还在跑」——坑 2 修好后轮在 <0.3s 跑完，halt 落空 | 改为**首局取活即置位**（确定性，不依赖墙钟） |

### 第 6 个坑：一个「先当真工作放过、实为死测试」的对拍（`test_serve_wiring`）

`test_course_args_match_run_rl_echo_config` 实测 5.02s。我第一反应判它是**真工作**（起真 Python
子进程 + 导入含 torch 的 `run_rl`），打算用 `@pytest.mark.time_budget(10)` 显式放宽——**判错了**，
用户一句「为什么要依赖 torch，CI 又不真跑训练」点醒：

- oracle 的 argv 是 `["run_rl.py", "--course", stem]`，**没传 `--echo-config`**；
- 而 `run_rl.main()` 的 echo 调用点在 `if getattr(args, "echo_config", False):`
  （run_rl.py:212）之后 ⇒ 那次 dump **从未被调用**；
- 于是 main() 一路往下：`validate_args` → loop 启动 → `build_model` → **导入 torch** + 读
  `weights/<course>/*.json` ⇒ 5s 全耗在这条与判据无关的链上；
- 后果更重：本机缺权重 ⇒ 永远 skip（实测如此）；真机有权重 ⇒ 没 PARITY ⇒ `pytest.fail`。
  即**这条对拍一直是死测试**（既没对拍上，又在有权重的环境必红）。

修法：oracle 传 `--echo-config`，走**文档化短路**（echo → log → return，在 validate_args
与任何权重/torch 之前）⇒ 0.24s、不依赖权重与 torch、任何环境都真跑。顺手去掉
`echo_config` 键（调用开关，不算解析快照，否则就是唯一的假分叉——实测 98 项全同、仅此一项差）。
`time_budget` 标记与 env-blocked 跳过一并删除（拿不到 PARITY 现在一律算真回归）。
跳过数因此 4 → 3。

### 新护栏：`nn-training/conftest.py`（根 conftest，用户口径）

**单测 >5s 警告、>10s 报错**。放在**根** conftest 是因为门禁跑 `pytest tests/ e2e/`，两层要同规。
实现用 `pytest_runtest_makereport` 改写 outcome（超预算即判 **failed**，不是 teardown 报错）⇒
`-x`/xdist/summary 全是标准语义；只计 call 阶段（fixture 建拆不算）。阈值可用
`NN_TEST_WARN_S` / `NN_TEST_FAIL_S` / `--test-warn-s` / `--test-fail-s` 覆盖；个别确需更长的用例用
`@pytest.mark.time_budget(N)`（必须写明理由）。它抓的正是本节的退化形态：**不占 CPU 的等待、
真实 sleep、被当成配速用的生产超时**，都会在耗时上现形。

### 效果（同一 16 核容器）

| 指标 | 修前 | 修后 |
|---|---|---|
| nn python gate 全量 | 157 / 182 / 185s | **24s** |
| `pytest tests/ e2e/ -n 12` | 142.7s | **20.1s** |
| 最慢单测 | 30.04s（4 个用例 26.5s） | **0.24s**（原 5.02s 的对拍；见上栏第 6 个坑） |
| >5s 警告 | — | **0** |
| 跳过 | 4 | **3**（serve_wiring 那条由「永远 skip」变为真跑） |

结果：1822 passed / 3 skipped / 0 failed，ruff + mypy 全绿。

### 遗留（未擅自改生产终止语义）

「全员停派（软停/熔断）且 `nodeRearmLimit` 用尽」时，若只剩 pending 而无人能服务，整轮会
**空等到 `queueWindowSec`**（生产缺省 1800s）才收官。测试里配小窗即可，但生产侧这条路径
值得独立评估（早退 vs 等窗，与 volume 补波/resume 的交互）——属终止语义变更，不在本次范围。

---

---

## §13 e2e `check()` 静默失败：autouse fixture `_fail_loudly` + 三条被它揭出的既存红（2026-09-20）

**一句话**：`e2e/test_run_rl.py` 的 `check()` 只把失败追加进模块级 `FAILS`、**从不抛错**，
只有手写了 `f0 = len(FAILS) … raise` 的用例才变红 —— `test_it_stream_smoke` 漏了那一段，
它那条「I3 eval after weight distribution」断言于是**静默 FAIL 了很久**，直到 xdist `-n 12`
下落到不同 worker 顺序才偶发红（门禁里被当作“随机 flake”）。

### 根因（I3 那条为何是顺序相关的）

`dist_common._WEIGHTS_PUSHED` 是**进程内账本**，键 `(kind, wver)`：
`_itest_env` 给每个 e2e 用例写的哑权重内容都是 `{"stub": true}` ⇒
`weights_fingerprint` 相同 ⇒ 同一 worker 里第二个用例的权重下发走 “kept / reuse
… skip POST”，**根本不经 HTTP** ⇒ FakeServer 没有 `weights` 事件 ⇒ `bool(wts3)` 假。
实测（`-p no:randomly`，`queue_normal → stream_smoke`）**必现**；单跑 smoke 则必过。

### 修法

| 位置 | 改动 |
|---|---|
| `e2e/test_run_rl.py` 模块级 | 新增 `@pytest.fixture(autouse=True) _fail_loudly`：测前快照 `len(FAILS)`，测后新增即 `raise AssertionError`——义务交给框架，新用例不可能再忘；三处手写 `f0/raise` 随之删除 |
| `_itest_env()` | 每个用例开头 `dist_common.weights_push_cache_reset()`（进程内账本清零）⇒「本轮真的下发过权重」重新成为**结构性**前提而非对用例顺序的断言。排序那一半本就有结构保证：stream 模式 `local_slots=2 < 4` 对局数 ⇒ 队列只能靠 POST 成功后孵化的节点线程清空 |

### 揭出的三条既存静默红（全在 `-n 12` 下）

1. **`test_mirror_scalar_lockstep`**：`SCALAR_X_INDICES == [15,18]` 已过期——`schema.py:94`
   追加了 `29=iceVx`（`SCALAR_DIM=30`）。改为 `[15,18,29]` 并补 29 的翻转断言（真语境：
   iceVx 是 x 分量，必须镜像）。
2. **`test_eval_local_gate` (phase B)**：原断言「gate 关闭 ⇒ 本地 runner 零调用」被
   2026-09-15 的 `release_local_gate_if_starved`（无节点时轮末主动开闸，防 600s 空等）
   **废除**；本地 worker 与那次开闸是竞态，单跑时 worker 已退（开闸是空操作）、`-n 12`
   下它仍在等门而被放行。断言换成当下真值：无节点轮必被主动开闸 + 不挂死 + 0 结算
   （“关门则让位”由 `hold_for_local` 单函数断言 + I7 慢 eval 用例覆盖）。
3. **`test_it_longtail_race` (I6)**：**单节点**配置下 `pick_race_target` 的「本节点已持有
   则不派回」（`queue_local.py:411`）恒假 ⇒ race lane **永不触发**（dispatches 恒=1），
   与 v3.14 竞速用例同源——那例当时靠加第二个节点修好，本例漏了。补同款第二节点后
   `dispatches=2`、单轮 0.2s（`race lane` 行重现）。曾误判为「负载把另一条任务也拖过慢窗」，
   把 3.0s 慢窗调到 15s 后仍 `dispatches=1` ⇒ 反证竞速条件本身不成立，窗口长度不是变量。

### 教训

- **「静默聚合」型断言 = 事实上的注释**：绿只在有人记得写 raise 时成立。用 autouse fixture
  把义务交给框架，而不是靠每个用例自觉（本例正是自觉漏掉的那一个）。
- **单节点不会竞速**：任何基于 race lane 的用例，配置必须是 ≥2 个节点（`nd_id not in
  inflight_nodes[task]` 是其唯一入口）。
- **进程内账本/缓存必须在用例入口清零**，否则同一 worker 的用例顺序会改变被测行为
  （`weights_push_cache_reset` 已是 `tests/test_dist_common_poll.py` 的既有惯例）。
- **A/B 定位法**：把「只加 fixture、不加隔离」的副本跑一遍（`git show`+注入，不 stash），
  就能把「fixture 揭出的既存红」与「本改动引入的红」分开——本例三条都在两版里同现。

### 验证

nn python gate 全绿 182s（含 `-n 12` e2e 12 用例）· `e2e/test_run_rl.py -n 12` **3/3 绿**
（改前 3/3 红）· 单进程整文件绿 · standalone 入口 `RESULT: ALL PASS` · 根 `bun run check`
1934 pass / 0 fail。

### 2026-09-20 后续更正：I3 那条断言本身立不住（§13 的一句话被实测推翻）

上文表格里「排序那一半本就有结构保证：stream 模式 `local_slots=2 < 4` 对局数 ⇒ 队列只能靠
POST 成功后孵化的节点线程清空」 **是错的**——本地槽是**复用**的：2 条本地腿 0.01s/局，
4 局全在本地跑完（`byNode={"local": 4}`），队列在后台 `weights-push` 仍**在途**时就清空。
门禁复现（`-n 12`，3 次里红 1 次）拿到的原始读数：

```
[stream] clean-eval dispatched (dispatch queue drained)   ← eval 已派发
eval_fired=1789903983.124095  weights POST 落地=1789903983.1436107   （push 落后 19.5ms）
[dist] tail-join grace 0s 到期：7 个 worker 仍在收尾: ['weights-push', ...]
```

**结论**：I3 原断言的「eval 晚于节点权重 POST」**不是本代码的性质**——契约（`rl/stream.py`
文档串）= 清空即「采集任务已全部交出（节点/本地）」，而干净评估另走自己的 `kind='eval'`
权重握手（`rl/eval_dispatch.py`），从不依赖 rollout 那条 POST 的完成时刻。

**改法（断言换成真性质 + 诊断带读数）**：

| 位置 | 改动 |
|---|---|
| `e2e/test_run_rl.py` I3 | 改为 **`not node_disp or wts3[0] < node_disp[0]`**：有结构保证的是「**节点采样派发**晚于其权重落地」（节点 worker 在 POST 成功后才孵化，`dispatch.py::_push_need_and_spawn`）；本地腿跑完全部对局时该断言不适用（and 不该乱红）。消息里带 `weights_events/first/first_node_dispatch/eval_fired` 四个读数 |
| `rl/stream.py` | 那行日志原写 “frozen weights on nodes” —— 同一误读，改成「采集任务已全部交出（节点/本地在途），评估与其并行」 |

**教训**：断言「A 晚于 B」时必须确认 A 与 B 的因果关系**在每一条执行路径上**都成立；
「上限（`local_slots<对局数`）」不等于「排他」（本地腿复用 ⇒ 上限不封吞吐）。

### 2026-09-21 后续：I1 是同一个坑的另一半（已修）

`test_it_queue_normal`（I1）留着同族的旧断言 `drained_ts[0] > wts[0]`
（「队列清空晚于权重 POST」）——同一机制下必发：`local_slots_max=0` 仍会孵化
`cap_full = max(1, min(workers=4, n_tasks=2)) = 2` 条**本地腿**，2 局 × 0.01s 能在后台
`weights-push` 落地前清空队列。

**实测（本机，standalone 单跑 6 次，同一份未改动代码）**：**红 2 / 绿 4**（≈1/3），
与任何改动无关——`nn-python-gate.sh` 因此约 1/3 概率红。原始读数（反向探针抓到的）：

```
weights_events=1  first=1789982346.3306324
first_node_dispatch=1789982346.4449425      （权重确实先到节点，真契约成立）
drained=1789982346.3306324                   （drained 与权重 POST 同一毫秒 ⇒ 旧断言恒输）
```

**改法**：与 I3（2026-09-20）同一条 —— 断言换成有结构保证的真性质
**「节点采样派发晚于其权重落地」**（节点 worker 在 POST 成功后才孵化）；本地腿跑完对局时
该断言不适用（and 不该乱红）。修后 standalone **10/10 绿**；反向探针（把 `<` 改成 `>`）
**立刻红**且打印出上面四个读数 ⇒ 断言是活的、非空。

**教训**：同一个错误断言会在**多个用例里各写一遍**；改一条时要 `grep` 同族表达式
（`drained.*>.*wts` / `wts.*<.*node`），别只修红的那一个。

---

---

## §12 并发退场 × 磁盘遍历：`walk_shard_dirs`（os.walk）取代 shard 树上的 `Path.rglob`（2026-09-20）

**一句话**：dup-settle 输家退场（结算线程 `rmtree` 它自己的 shard 目录）与主线程的
`resumed_manifests` 磁盘对账**同轮并发**，`Path.rglob` 的 `scandir` 撞上删除即抛
ENOENT——而该异常在 **for 语句的迭代**里（不在循环体内），调用方的 `try/except OSError`
只裹了 `read_text`，挡不住 ⇒ `stream collector failed` / 整轮红。

### 现场（门禁 e2e `-n 12`，栈完整）

```
rl/dispatch.py:1121 in run → resumed_manifests(...)
rl/resume.py:248 in resumed_manifests → traj_dir.rglob("rl_s*_seed*/manifest.json")
pathlib.py:440 in _select_from → with scandir(parent_path) as scandir_it:
FileNotFoundError: [Errno 2] No such file or directory: '…/i9/dist/fake/rl_s0_seed111'
⇒ RuntimeError: stream collector failed: …
```

**症状特征**（判据）：`No such file or directory` 后面跟的是**目录**路径（抛点在 scandir
而非文件读）——这与「文件缺失」类 ENOENT 一眼可分。

### 修法

`rl/resume.py::walk_shard_dirs()`（新，单一口径）：`os.walk(root, followlinks=True)` +
`fnmatch` 目录名，契约是「scandir 失败按 onerror=None 静默跳过该层」⇒ 遍历期被删的
目录自然消失，其余 shard 照常对账（少一份已退役副本正是期望语义）。四处调用点换用它：
`_dir_signature` / `_scan_shards`（`completed_pairs`）/ `resumed_manifests` /
`trailing_stage_samples_per_game`（连带 `it_dirs` 的 `stat` 竞态）；`remote/hub_client.
iter_shard_dirs`（发布端同一条竞态，同一 helper + `_shard_mtime` 兜底）。

### 证据链

- **先复现**：门禁实测栈（上表）＋ `tests/test_rl_resume.py` 的确定性钩子自证
  （原名 `test_hook_makes_old_rglob_raise`——「旧 rglob 必抛」；Py3.12 后 pathlib 已
  并发容忍，自证改为 `test_hook_intercepts_scandir_and_deletes_victim`，见 §15）。
- **确定性竞态钩子**：在 `scandir` 的实参 = 受害目录那一刻 rmtree（= 事故时序），
  绑定点随 Python 版本：≤3.11 pathlib 锁存 `os.scandir` 成 accessor 类属性（cpython 3.10
  `pathlib.py:290`）；≥3.12 改 `Path._scandir` + `os.scandir`（见 §15）。
- **反面证据**：「retire 竞态」曾被怀疑是 collector 读 shard（`_shard_dir`）出错——实测
  不成立：把本地竞速副本拖慢以**确定性**制造 `dup settle … node=local (+retired …)`
  （`e2e/test_run_rl.py::test_it_stream_local_loser_retire`），collector 照常收官 4/4。

### 教训

1. 对**活的** shard 树做遍历的每一处都必须并发删除安全（退场是设计内的并发写者）；
   新加 walker 时先问「谁会在我遍历时删这个目录」。
2. `pathlib.rglob/glob` 的 ENOENT 抛在**迭代**里 —— 在循环体内 try 是无效防护，
   必须换成遍历期本身安全的原语（os.walk）或自己 scandir + try。
3. 竞态复现钩子要**自证有效性**（同一钩子下旧实现必须红），否则「钩子没生效」会被
   当成「测试通过」。

---

---

---

## §11 测试侧端口竞态：`spawn_bound_port()` 把「探端口 → 子进程 bind」收成一个出口（2026-09-19）

**现象**：R4 提交被 pre-commit 的 nn python gate 拦下，红的是一条与本轮改动**无关**的 e2e ——
`hub-server 未就绪或课程表不对（rc=1）`；真因（`端口 127.0.0.1:53637 已被占用——拒绝启动`）
只埋在子进程输出里（gate 的报错摘要里看得见，本地单跑该文件却 2 passed）。

**根因**：`_free_port()` 是「`bind(0)` → `close()` → **交给子进程** bind」的 TOCTOU。串行跑窗口只
微秒级，但 gate 用 pytest **xdist**：另一个 worker 的探测会拿到刚被释放的同一端口并先绑上
（窗口 = 另一端 Python 冷启动 ~1s）⇒ 先绑者赢、后绑者被 `remote/_port_guard.py` 拒启。

**修法**（`tests/subproc_util.py::spawn_bound_port()`）：

- 成功判据 = **这个子进程自报** `listening on <host>:<port>`（不是「端口上有人监听」——那可能是
  别人的服务，最坏会让用例对着陌生 hub 跑完并且通过）；
- 撞端口的子进程带 `PORT_TAKEN_MARKER` 退出 ⇒ **换端口重试**（默认 5 次）＋ print 一行（重试要可见）；
- 非端口原因的死法**立刻**抛（重试只该救端口，不该把真 bug 藏成「偶尔红一次」）；
- 超时既没自报也没退出 ⇒ 当成功（日志文案变了不该变硬失败），就绪判定交回调用方。

**证据**：`tests/test_subproc_util.py` 6 例——真 `remote._port_guard` 子进程驱动「撞端口 → 重试」、
非端口死法不重试、上限到顶、超时兜底 + `tail()` 可读、守卫文案对齐、**源码守卫**（两个调用点
不得再出现裸取端口）。全量 gate `1579 passed / 4 skipped`（+6）；`-n 6` 并发复跑相关 5 文件 83 passed × 3
（**未见重试**——竞态本身稀有，所以靠确定性用例而不是压力测试来证明）。

**顺带**：两个调用点各自那份 `_startup_output()`（「进程活着时 read() 会阻塞到 EOF」的绕行）删除，
改由 helper 的 reader 线程实时收行 + `BoundServer.tail()` 取尾部——诊断面从「只能在进程死后读」
变成「随时能读」。

### 续（同日）：全部取端口点收编——四份私有 `_free_port` 清零，守卫改扫全测试面

仓库里还剩四份私有副本，语义分两档，故出口也定成三个（全在 `tests/subproc_util.py`）：

| 出口 | 适用 | 机制 |
|---|---|---|
| `free_port()` | **进程内**探端口（`test_port_guard` / `test_push_bootstrap_teardown`） | bind 紧随探测，窗口微秒级 |
| `spawn_bound_port()` | **单个**服务子进程（`test_multi_course_hub` / `test_instance_lock` 的顺序双启 / `test_worker_server_lock` / e2e 的 `_Hub`） | 等**这个子进程**自报 listening；撞端口换端口重试 |
| `retry_on_port_stolen(scenario)` | **多个**进程抢同一端口（两处「三启 ⇒ 恰好一个」） | `scenario(port)` 抛 `PortStolenError` ⇒ 换端口重跑；其余异常原样上抛 |

第三个出口的理由：「三启同时启动」必须让 N 个进程抢**同一个**端口，单进程版用不了；端口若被外人
抢走则 N 个全灭且输出带占用文案——那是**场景作废**（换端口重跑），不是被测行为不对，既不该报红
也不该把断言放宽。

**另一个真坑（这次顺手拆掉）**：helper 的子进程 stdout 一开始写 `text=True`——那按**控制台代码页**
解码（zh-CN Windows = gbk），而服务侧按 `force_utf8_stdio()` 写 UTF-8 ⇒ 读线程撞 UnicodeDecodeError
静默死掉，`lines` 永远为空 ⇒ 自报监听看不见、端口竞争也识别不出。改成显式
`encoding="utf-8", errors="replace"`，并加一条源码守卫钉住（`test_child_output_is_decoded_as_utf8_not_console_codepage`）。
同一个根因 2026-09-17 在本仓吃过一次（`test_instance_lock` / `test_worker_server_lock` 当时被迫用
`capture_output=True` + bytes + 手动 utf-8 解码）。

**守卫扩面 + 剥注释**：从「2 个文件的点名清单」改成扫全部 `tests/**` + `e2e/**`（剥掉注释后扫，R4 那条
教训——「已退役」的记录恰恰写在注释里）：① 不许再有私有 `_free_port`；② 凡 argv 里出现
`"-m"` + `remote.hub_server|remote_worker_serve|remote.worker_server` 的文件必须借端口（用**带引号的
argv 元素**判定，避免把 `from remote.worker_server import` 这种进程内用法误扫进来）。

**证据**：`tests/test_subproc_util.py` **9 例**；全量 gate `1582 passed / 4 skipped`（+3）；根 `bun run check`
1868 绿。记录：`DECISIONS.md` §2026-09-19-goalnn-test-port-convergence。

---

## §10 节点门统一：rollout 与 eval 同用 codehash-files.txt（eval 门改比 codeHash，ping 不再报 engineEpoch）（2026-09-17）

**为什么记这一笔**：eval 节点门比的是 `engine_epoch = sha256(git_full_commit + GAMEPLAY_SPECS
指纹)[0:16]`——**掺了 git commit**，而 gameplay 集还是 TS（codehash-files.ts）/Python
（dist_common.py）两侧手工镜像的第二份清单。于是任何与 rollout/eval 无关的提交（dashboard /
nn-training / docs）都把全节点判 stale，运维只能同步 + 重启 sampler-agent（2026-09-15
x3-power it30：epoch 全员 mismatch → nodes_ok=[] → 600s 零局；2026-09-03 x3-chip-k10
it1–it4 四节点同因全 skip）。用户指令：两者统一用 `tools/agent/codehash-files.txt` 作为
「节点是否可用」的事实来源。

```
唯一事实来源（改这里 = 同时决定 rollout 门与 eval 门）
  tools/agent/codehash-files.txt
    ├ src/nn/**（策略）· tools/sim/export-*（导出器）· tools/agent/*.ts（agent 协议）
    └ 2026-09-17 并入：src/game/** · src/config/** · src/utils/**（RNG）· src/ai/**（God）
                        · tools/det-golden.v1.sha256   ← 原 GAMEPLAY_SPECS 双份清单已删
  排除原则（同处写明）：dashboard/** · nn-training/** · docs/** · plan/** · tests/**
                        —— 它们的提交不得让任何节点变 stale

  /v1/ping 只报 codeHash = hash(清单展开) —— 唯一的节点门字段
  rollout 门（dispatch / rescan）与 eval 门（eval_dispatch §6.6 / batch_eval 严格）同用
    dist_common.check_code_hash(ping, 本机 codeHash)
  engine_epoch = sha256(codeHash)[0:16]（训练机侧算）= **账本记录值**：进
    EvalGameRow.engine / 心跳 / S10 哨兵；**不再是门**，故不进 ping（原 check_engine_epoch 删）
```

**语义变化（有意为之）**：S10「引擎漂移」的范围从 git commit 收窄为清单代码（含 src/nn 策略），
**比旧式更不容易误报**（以前连 docs 提交都算漂移）；而引擎文件入清单后，「改引擎不改名 codeHash」
的漏网口同时关闭。节点门也只剩**一个字段（codeHash）与一个判据**：旧 agent「无 engineEpoch →
过渡期放行」的宽松分支随之取消——codeHash 是 rollout 门一直都在用的字段，比它只严不宽。**代价**：本次自身即清单变更 ⇒ 全节点一次性
判 stale ⇒ 一轮预期内的升级波（同步代码 + agent 重启后自动纳管）。

**门禁实测（2026-09-17 收尾全绿）**：根 `bun run check` **1851 pass / 0 fail**；dashboard
typecheck + **485 pass / 0 fail**（顺带修掉进入本次任务时那条**存量红**
`server-actions-resolve-course-bc`：用例引用的是从未存在过的课程名 `x1-rebirth-a2`，仓内课程是
`x1-rebirth.jsonc`，已改成真课程名）；nn python：ruff + mypy（225 文件）干净、
**1183 passed / 3 skipped**。当时剩下一条未跑
`test_remote_iter::test_spec_argv_weights_must_be_relative` 是**平台性存量红**：
`_iter_rel_path` 只挡 `..` / `~` / POSIX 绝对路径，`C:/weights.json` 在 Linux 上既非
`os.path.isabs` 也非 `Path.isabs`（Windows 上才拦得住）——**已于同日修掉，见 `docs/nn/runtime-opt.md` §1.1**。
决策见 `DECISIONS.md §2026-09-17-goalnn-unified-node-gate`。

---

## §9 python 全量门禁提速：worker 数 × CPU 内线程数必须成对调（2026-09-17）

**背景**：用户问「检查 python 全量门禁，提高可维护性，减少耗时」。门禁墙钟实测 39~50s，而
ruff(~1s) / mypy(~4s) 完全藏在 pytest 后面 ⇒ 只有一个瓶颈：pytest。

**根因（推翻 `docs/goal-nn.progress.md §25` 的旧结论）**：旧默认 `-n 4` + torch 默认内线程
（= 物理核 16）⇒ 4 worker × 16 线程 = 64 线程抢 16 核，**严重超订**。§25 那条「n=4 最优、
auto=16 反更慢」正是这个假象的读数（worker 越多越慢本身就是超订证据），不是「torch import 开销」。

**实测**（16 核，`python -m pytest tests/ e2e/` 单独计时，同机交错 3 次/项）：

| 配置 | 均值墙钟 | 备注 |
|---|---|---|
| `-n 4` 默认线程 | 36.3s | 旧默认 |
| `-n 12` 默认线程 | 44.7s | worker 越多越慢（超订证据） |
| `-n 4` 线程=1 | 39.7s | 只封线程不救小并发 |
| **`-n 12` 线程=1** | **24.7s** | ← 新默认 |
| `-n 8` / `-n 16` / `auto` 线程=1 | 25.5 / 23.5 / 24.0s | 8~16 平坦 |

**改动**：① 门禁 export `OMP/MKL/OPENBLAS_NUM_THREADS`（`NN_GATE_THREADS`，默认 1，0 = 不设）
+ worker = `min(核数, 12)`（`NN_GATE_NPROC`；取 12 同时给内存封顶：峰值 pytest 进程树
RSS ≈ 3.9GB ≈ `-n 4` 的 3 倍）；② 三路工具启动去重成 `run_tool`（原先 LIVE/detach 二选一
复制了三遍，加一个工具就要再抄一遍，漏掉 detach 分支会让 Windows commit 卡死）；③ 去掉 pytest 的
重复 `-q`（addopts 已有 `-q` ⇒ 原本是 `-qq`，把结尾的「N passed in Xs」吞了，hook 日志里看不到
用例数与耗时）——现在日志里是 `1010 passed in 21.2s`；④ `t0` 提到启动工具之前，报告的秒数 = 门禁
真实墙钟（旧版只算「等最慢那个」）。

**同策推广**（防「门禁快、日常入口慢」的漂移）：`task.py` 四个 target 共用 `clean_env()`，线程封顶
只改一处即全生效，worker 一律 `-n auto`（**2026-09-15 把 `-n auto` 判为「沙箱 ~34% 停滞」头号嫌疑
是误判，本次回退**）；`nn-training/Makefile` 加 `NPROC ?= auto` / `THREADS ?= 1` + export；
CI `nn-training.yml` 加 job 级线程封顶，单测层从**无 `-n`**（job 里最长的 pytest 步）改为 `-n 2`
（与 e2e 同口径）。

**验证**：门禁连跑两次 20s / 23s rc=0（改前 39 / 50s）；日志含 `1010 passed in 21.2s`；ruff +
mypy 绿；`test_githook_scripts.py` 新增两条静态护栏（封线程 export + 核数派生 worker 数），并用
**变异测试证明非空转**（删 export / 退回 `-n 4` / 去掉上界 → 3/3 被抓住）；CI 侧封顶用
`taskset -c 0,1` 压成本机 2 vCPU 模拟（`-n 2`：默认 57/73s → 封顶 55/53s）。

**教训（通用形态）**：把「并行度不够」当结论之前，先看**每个进程内部**开了多少线程——
`-n`（进程数）× 库默认线程数（= 核数）是一对乘积，只调一半得到的结论会**反号**（本来该加 worker，
却得出「workers 越少越好」）。

---

## §8 metrics v6 落地：分敌种命中/击杀列（idx31–38，TS+Python 全链，2026-09-16）

**为什么记这一笔**：T5（分敌杀信用）的工程前提（x3-credit-p6 课程文件头「工程前提」
节明示）。`docs/nn/experiments.md` §8 判 T6 全面阴性后，T5 主路径依赖本条落地；观测/模型/encoder 未动
（x3-power-followup §52 禁令维持）——本条是**采集通道加宽**，非奖励语义变更。

**落地内容**（plan/t5-metrics-v6.plan.md，全部尾部追加、0–30 列号永久不动）：
- `METRICS_DIM` 31→39，`METRICS_VERSION` 5→6（TS/Python 双侧同源，manifest 与
  summary 均改由常量导出，消除硬编码副本）；idx31–34=kills{Basic,Fast,Power,Armor}
  （只记 `tank_destroyed by=player` 打敌车），idx35–38=hits{同序}（`enemy_hit.targetKind`，
  含致死命中）；列序 = `ENEMY_KIND_ORDER = [basic, fast, power, armor]`。
- 验证三层：跨语言 SSOT 断言（两侧列名/idx 互锁）＋新 `tests/sim/metrics-v6-census.test.ts`
  （独立重实现对账＋守恒 sum(killsByKind)==玩家击杀敌车＋同 seed 双跑确定性）＋
  golden 复用零填充（v6 尾列对 v7 公式零贡献，不重铸 oracle）。
- 新课程 `x3-credit-p6` / `x3-credit-p6-r2`（power 杀信用 2× 主臂 ×2 独立 run，
  `docs/nn/experiments.md` §8 新规下限；单变量 vs x3-start；verdict 规格已冻结在课程文件头，
  **待用户手工开训**）。首次开训触发采样节点 codeHash 重编译，属预期。
- ★ 残差桶（评审 P0，方案 A）：bomb 清屏（`SimulationPowerUps.ts:391`）不推
  `tank_destroyed` ⇒ 四桶之和恒 ≤ 标量 `kills` 列（God AI 长局缺 11.6%）。
  公式末尾带 `wKillBasic*(kills − ΣkillsByKind)`——全 3.0 剂量下与 x3-start
  标量公式逐字等价（golden 6 局对账 |Δ|=0），单变量纯度保住；已知残留
  （桶按 basic 计价，bomb 杀 power 无溢价）记在课程文件与结算模板。
- 教训（本条勘误位）：kills 包络上界 40 是单关 2× 余量——多关合一 episode 的
  课程形态须重估（caveat 注释已落在 `reward_validation.py` DEFAULT_RANGES）。

---

---

## §7 ipynb 清场 + tpu-probe 单源化（2026-09-13，用户指令「重构 ipynb：删无用 notebook，重新整理 tpu-probe 使其更模块化并保持独立性」）

- **删三个被取代的旧 notebook**（均无活引用，文档中的历史提及保留为史实）：`p4-onset.ipynb`（课程专用 BC → 通用 `battle-bc.ipynb`）、`m2_colab_worker.ipynb` + `p4-onset-rl.ipynb`（旧式内联 worker → 单 cell `battle-rl.ipynb`，运行时已迁 code.zip）。现存三个：battle-rl（云端 worker）、battle-bc（通用 BC 蒸馏）、tpu-probe（吞吐探针）。
- **tpu-probe.ipynb 重构为 §0-§6 分区 + Cell 地图**：§1 vfio 诊断/释放两个 cell 原各自内嵌一份 `find_vfio_holders` 拷贝 → 合并为单一「工具函数」cell（无副作用）+ 两个 thin driver（NameError 时提示先跑工具 cell）；§3 脚本 cell 从"手工保持逐字节一致"改为**单源生成**；测量 cells（TPU 三遍/GPU 单卡/多卡/CPU）原样保留为 §4 thin driver。
- **单源机制**：正本 `nn-training/tools/tpu-probe.py` → 新工具 `tools/sync_tpu_probe_nb.py`（写回 / `--check`）→ notebook 的 `%%writefile tpu_probe.py` cell；新测试 `tests/test_tpu_probe_notebook.py` 双守卫（内嵌==磁盘逐字节 + 工具 `--check` 通过），drift 在 python-gate 常驻拦截。**运行时独立性不变**：notebook 在 Colab/Kaggle 仍自包含（脚本随 cell 写盘，无需克隆仓库）。
- **顺手修三处 HEAD 上已红的旧账**（7aeafb2 / 26f9171 / 99c9367 落地时未跑全门禁）：
  ① `rl/forensics.py::_rss_mb_posix` 同源化——`getrusage ru_maxrss` 在 WSL2 内核**持续滞后**于实际常驻集（实测滞后 ~16kB），与 `/proc/self/statm` 混用导致 `test_rss_mb_sane` **确定性红**（3/3）；改读 `/proc/self/status` 的 VmRSS/VmHWM 同源快照（20 万次采样 0 违例，peak ≥ cur 由内核保证），getrusage 降级路径保留给非 Linux POSIX。
  ② `rl/eval_replays_once.py` 两处 RUF100 unused noqa（`# noqa: E402/BLE001` 指向未启用规则；意图注释保留）。
  ③ `tools/training/console/api.ts:782` `CURRICULA_DIR` → `curriculaDir()`（TS2552，7aeafb2 引入的未定义标识符）。
- **环境记录**：满编 `-n 4` 下 `test_bc_epoch_e2e` 两例曾红（fake_worker POST 400 manifest 校验拒绝），单跑与 `-n 2` 均绿——负载型 flake 非回归，e2e 并发时序对 CPU 争用敏感（§0.1#4 单文件绿 + 全组红 = 环境的又一实例）。

---

---

## §6 自主审查轮：battle-rl.ipynb 单 cell 化 + 真跑暴露四缺陷修复 + bc-c4 it1 真实产物（2026-09-13）

**BC 云端 notebook 重构（用户指令：网页只执行一个 cell 建连，其余完全自主）**——
`nn-training/ipynb/battle-rl.ipynb` 从 8 代码 cell 顺序流程改为 **markdown + 单代码 cell**：

- 参数置顶（MODE/HUB_URL/HUB_TOKEN + 行为参数）；自动 = 平台/设备探测（CUDA/TPU/CPU，
  TPU 独占设备约束全保留：内核绝不 import torch_xla，占用者诊断在）、保活、
  `/code` 代码引导（**404 = 每 30s 等待重试**——旧版硬失败，顺序敏感；现在先起 worker
  后起 trainer 完全合法）、worker 监督（热替换 86 自动重启 + **崩溃指数退避重启**
  ≤MAX_WORKER_RESTARTS；rc=0 干净退出/-2 配置致命不重启）、push 模式 cloudflared
  自动安装 + bootstrap 优先 hub /code（GitHub tarball 兜底）。中断 = 干净停机。
- **E2E 实证**：本地 hub + 抽取 cell 源码 headless 执行——worker 先起等 /code 重试
  → trainer 后起发布 → cell 引导代码、领 kind=bc 任务、真训练 5.5s、回传 accepted。
- **二次精简（同日）**：运行时逻辑再搬进 code.zip——新模块 `remote/notebook_runtime.py`
  （设备探测/pull/push/崩溃退避重启，入库受 ruff+mypy 门禁），cell 降到 **134 行** =
  参数 + 保活 + /code 引导 + `run_notebook(CFG)` 委托。修运行时不再重发 notebook
  （重跑 cell 即拉新版）；push 的 GitHub tarball 兜底随之删除（/code 等待重试已覆盖）。
  headless E2E 复验 PASS（code.zip 100 个 .py 含本模块）。
- 新夹具课程 `curricula/bc-e2e.bc.jsonc`（BC 管线流校验，对齐 tiny-a 定位；wins_only=false
  适配 300-tick 冒烟）。

**真跑暴露四缺陷（全部修复）**：

1. **smoke 语料污染真轮**：smoke shard 落 `bc-data/it1` 被真跑断点续跑复用（40 局里混进
   1 局 300-tick timeout 局）→ smoke 落 `bc-data/smoke` + 全量重采不复用；`round_name`
   贯通采集/发布/落位三处（iter_bc_shard_dirs/verify_and_land_bc/publish_bc_job）。
2. **本地训练路径根错位**：run_bc chdir 仓库根但子进程 cwd=nn-training，相对路径
   FileNotFoundError → 全 resolve 绝对路径；子进程输出 capture 缓冲到结束（§16.3 违例）
   → Popen 逐行流式中继。
3. **本地 ckpt_every 死键**：train_local_bc 漏传 --ckpt-every（云端 manifest 有）→ 接上；
   真跑 ckpt.10..60 全落盘。
4. **本地 WEIGHTS.md 行全 0**：extra_meta 是 weights JSON **顶层合并**（sizes/best_val_loss/
   history），误读 `meta` 子键 → 修读法 + it1 注册行已人工订正。

**真实产物（bc-c4 it1，全链本地实证）**：arena4 语料 40 局（29 胜保留/11 败过滤，
6442 帧，18.6s 采集）→ CPU 6 线程 60 epoch 37 分钟（val_loss 2.24→1.6260，move acc
0.236→0.566，fire acc 0.761→0.764）→ 归档 `nn-training/weights/bc-c4/bc-c4.it1.20260913-161311.json`
+ 中央 WEIGHTS.md 行 + 活跃指针 `tmp/bc-c4/weights.json`。该权重可直接作 RL 课程
`bc` 种子 / `kickstart_ref` 快照。

---

## §5 python 门禁 flake 全面审计：静态扫雷 + 负载轰炸（2026-09-13，§4 后续）

**方法**：① 静态扫雷——全测试目录 grep 紧墙钟（sleep/wait/timeout 断言）、被采样日志行
依赖（RACE_LOG_SAMPLE 类）、固定端口、mtime 排序、xdist 共享 tmp 撞路径；② 历史证据——
git log 的历次 flake 修复（`82cc6d6` I9 假红、`b0317ad` 沙箱删除配额、§4）+ tmp 红跑
日志；③ 实证——全量 541 项 × 5 轮禁用 `-x`（首败不停、收全部失败）轰炸，其中 2 轮带
4 spinner、叠加真机 c4-chip03 训练负载；I7 定向 10 连跑（6 spinner）。

**发现与处置**：
- **I7（`test_it_eval_deferred`）set-then-clear 竞态 + 3s 紧等待 → 已修**：
  `eval_th.start()` 之后才 `eval_dispatched.clear()`——负载下主线程若在 start→clear
  之间被调度延迟数秒，eval 线程先置位再被清掉 → 必假红（与 I9 §4 同族）。修法：
  clear 提前到 start 之前（置位必属真实派发）+ 等待 3s→30s（正常 ~10-100ms，覆盖
  ping→POST 权重→worker 孵化→首局 fetch 全链路的负载放大）。
- **I10（tail join grace）`took10 < 15s`**：对设计的 2s grace 有 ~4× 余量，观察保留。
- **其余全部干净**：端口全 bind 0（ephemeral）；mtime 仅等值断言；tmp_path 已由
  conftest 唯一化（pid 参与命名）；`test_dist_common_poll` 的 `dt < 5s` 有 4-5× 余量；
  test_upgrade 的短超时是故意触发降级路径的合法输入；I1/I3 的排序断言走真实回调与
  服务器事件、不经过可采样日志；P0 新增 test_loop_gate_soft_remediate 纯逻辑零时序。

**实证结果**：5 轮全量 2705 次执行零失败（19.4s / 21.3s / 19.8s / 24.8s / 54.4s——
末两轮带 spinner + 真机训练负载）；I7 定向 10/10 绿。结合 §4 修复前的历史红跑，
目前门禁内已知 flake 清零；`-x` 首败即停是门禁的有意设计（快速反馈），审计口径
须用 `-o addopts=` 覆盖。

---

---

## §4 I9 长尾竞速测试 flake：两条失败路径 + 双通道断言修（2026-09-13 复核 `python-flaky.issue.md`）

**问题（复核确认真实并复现）**：`test_run_rl.py::test_it_early_race_v314` 在 xdist -n 4
门禁下偶发红（他机 2/3；本机 8 CPU spinner 负载下复现，`tmp/pygate-flaky-repro2.log`；
4 spinner 与单跑 ×8 均绿）。与 P0 提交 `d17e9f0` 无关（stat 确认未触及调度路径）。

**根因（比原 issue 的分析多一条路径）**：断言的证据链有两层隐性依赖，负载下各自翻车——

- **路径 1（原 issue 发现）**：竞速检查的墙钟时刻。空闲槽无任务可派时按
  `all_settled.wait(0.5)` 空转——**轮询粒度 0.5s 本身 > 0.4s 慢窗**；xdist/沙箱负载下
  首个竞速检查实测晚至 +1s，seed111 已结算离场，race lane 只能命中剩余任务（红跑日志
  命中 `(3,9006)`）。
- **路径 2（本次复核新发现）**：证据通道被采样。`741c395` 起 race drops 每类只打前
  `RACE_LOG_SAMPLE=2` 条；而 v3.7 突发 fanout **不打派发日志**，会在派发突发期抢占
  慢任务的 dup 槽——快副本 0.01s 获胜即把任务弹出 inflight，race lane 从此**结构性
  选不中它**，慢主副本的证据行（`main_by_fanout` 类）落在第 3 条后被采样挤掉。
  复现红跑（repro2）正是此形态：race 命中 `(2,9005)`，`main_by_fanout=3` 只打 2 条。

**修（test_run_rl.py，三处）**：
1. **慢窗 0.4→3.0s 上限（FakeAgent）+ 竞速副本到达即提前放行**：远大于轮询粒度 +
   观测抖动（+1s）；慢 handler 每 50ms 轮询同键并发 fetch 计数，≥2（= 竞速副本已
   派出、被测性质已成立）即提前返回——窗口只在回归（无副本）时才睡满，正常路径单轮
   回到**亚秒级**（实测 I9 0.38s / I6 0.4s，比 0.4s 窗时代还快，门禁墙钟无净增）。
   修掉「v3.15 判据不依赖窗长」的错误注释。round 仍远低于 30s 死锁兜底。
2. **I9 测试 cfg 设 `tailFanoutN: 0`**：突发 fanout 是唯一不打日志的复制通道，关掉它
   让 race lane 成为唯一复制路径（fanout 语义由 I6/longtail 测试覆盖）——证据不再
   依赖「seed111 未被突发复制」的时序运气。
3. **断言改双通道 OR**：日志行（tail-race 等，不采样）**或** FakeAgent 派发计数
   `(0,111) ≥2`（对采样免疫，与 I6/longtail 既有断言同款式）。2026-09-06 曾硬断言
   计数而"HTTP 事件偶发缺席"——OR 化吸收该教训；回归（`pick_race_target` 断）时两
   通道同时缺席必红（已反验：patch `return None` → 红 → 还原；提前放行下仍成立，
   因无副本时慢窗照旧睡满）。

**验证**：单跑绿（`log evidence=True, dispatches=2`；提前放行后 I9 0.38s / I6 0.4s，
较 0.4s 窗时代还快）；反验红（两种窗口形态下各验一次）；门禁 5 连（第 2/4 次带
6 spinner 负载）+ 优化后 3 连全绿。教训：**「结构确定性」判据仍可能依赖墙钟
时序**——写竞速/超时类测试时，慢窗必须按「轮询粒度 × 安全系数」取值；改日志采样
（RACE_LOG_SAMPLE）前必须 grep 测试对被采样行的依赖。

---

---

## §3 CLI 子进程编码契约：环境无关的三层修（2026-09-13 复核 `python-cli.issue.md`）

**问题（复核确认真实）**：`test_gate_check.py::test_cli_dry_run_exit_code` 用裸
`subprocess.run(..., text=True)` 捕获 `rl.gate_check --json`——父侧解码编码 =
`locale.getpreferredencoding(False)`（**解释器启动期决定，运行时改不了**；zh-CN
Windows = cp936），子侧却由启动环境任意决定（`ensure_ascii=False` 把中文直排进
stdout）。子进程 UTF-8（agent 沙箱常设 `PYTHONIOENCODING=utf-8` 且无 `PYTHONUTF8`）
× 父进程 cp936 → 读线程在 `subprocess._readerthread` 死亡 → `stdout=None` →
`json.loads(None)` TypeError。本机复现矩阵证实：仅 `PYTHONIOENCODING=utf-8` 必红；
`PYTHONUTF8=1` 两侧都 UTF-8 则绿（**它掩蔽而非修复**）——不同 agent 沙箱 env/代码页/
Python 版本（3.15 起 PEP 686 默认 UTF-8）各异，环境解不可能通用。

**修（契约从环境移进代码，三层）**：
1. **`--json` 机器通道改 `ensure_ascii=True`**（`rl/gate_check.py`）——纯 ASCII 字节对
   任何解码器免疫（含我们控制的裸 text=True 父进程与不控制的第三方 agent）；中文经
   `\uXXXX` 传输，`json.loads` 还原无损。人类可读走非 `--json` 分支。
2. **子侧入口钉死**：`platform_utils.force_utf8_stdio()`（运行时 `reconfigure`
   stdout/stderr 为 UTF-8，实测压过强设的 `PYTHONIOENCODING=gbk`），`gate_check.main`
   与 `run_rl.main` 接入——被测 CLI 的字节流恒 UTF-8，与环境解耦。
3. **父侧测试统一出口**：`tests/subproc_util.run_utf8()`（强制 `encoding="utf-8"` +
   `stdout is None` 就地断言），扫掉全部 7 处裸 `text=True`（test_gate_check ×2、
   test_run_rl、test_upgrade ×2——后两处 spawn 的 **bun 管道恒 UTF-8**，本就是同型
   雷点、test_no_torch_on_import ×2）。

**验证**：四场景矩阵（无强制/仅 PYTHONIOENCODING/PYTHONUTF8/沙箱原样）全绿；对抗
探针（子进程强设 GBK env）下 `--json` 输出纯 ASCII、verdict/report 无损；nn-python-gate
全绿。生产侧同模式捕获点（`dist_common` ×2 / `run_rl` 的 git 调用 = ASCII 输出、
`bootstrap` 已 `errors="replace"`）不急；新 subprocess 测试一律用 `run_utf8`。

---

---

## §2 metrics v5（`clearTick`）+ outcome 虚拟符号：修复加列只改了一半（2026-09-12）

**背景**：c6-bonus 修「清场后 BONUS TIME 窗口被 max_ticks 截断 ⇒ 歼灭局吃 `terminal.timeout=−2`」需要两个新能力：指标列 `clearTick`（idx30，哨兵 −1）与公式可访问 outcome（虚拟符号 `is_timeout` 等，不占列）。设计侧验证通过：`wClear=4.0` + `wBonusTicks=600` 让「清场+超时」与 `stage_clear` 总回报相等（实测两边均 −14.0）。

- **P0（已修）**：`tools/sim/export-rl-rollout.ts` 只改 `metricsRow()` 的行、`METRICS_DIM` 仍为 30 ⇒ `writeRlShard` 的 `metrics.set(row, i*30)` 在**每局终局行**越界 `RangeError`，整条 RL 采集腿零产出；`tsc`/TS 测试全看不见（`number[]` 无长度类型、无行宽断言）。修：常量改 31 + 行构造提为 `export function buildMetricsRow` + 新增 `tests/export-rl-rollout-metrics.test.ts`（行宽 / 两种哨兵 / 与 Python `METRICS` 条目数跨语言对账）。e2e 复核：1 局 → `metrics.npy (41,31)`、`metrics_version=5`、未清场列 −1。
- **Python 门（已修）**：① `reward_validation.DEFAULT_RANGES` 缺 `clearTick` ⇒ `validate_reward(course)` 抛未捕获 `KeyError`；现改为带列名的 `FormulaError`（由 `validate_reward` 归入 errors），并新增 `test_all_metrics_have_envelope_range` 锁「加列必须登记域」。② `symbolic_envelope` 对引用虚拟符号的加性项不带 outcome 调 `phi` ⇒ FormulaError 被当成数值爆炸记 `inf/超限`（假临界）；现按**每个真实 outcome** 各求一遍取峰值（比「全 0 虚拟」忠实），角点回映加取模。③ `tests/golden/v7_phi_ts_oracle.json` 仍是 30 列 ⇒ 重生成；**`phi` 逐位不变**（v7 不读 clearTick），纯宽度同步，非重新标定。④ `test_item_metrics_layout_locked` 尾部清单补 `clearTick`(idx30)。
- **门禁**：nn-python-gate（ruff + mypy 144 文件 + pytest 全量）**绿**；`bun run check` **2022 pass / 3 skip / 0 fail**；`validate_reward(c6-bonus)` = ok、errors/warnings 均空，`wClear` 项 max_abs=4.0。
- **reward golden 补真实覆盖（已做）**：原先 60 个 case 的 `clearTick` 全是 `0.0` ⇒ `wClear` 是常数项、diff 恒 0，补偿/豁免**零覆盖**；且 `0.0` 是合法值（「第 0 tick 已清场」）而非「未清场」。改：① `_v7_corpus` 显式写哨兵 `-1.0`（对不读该列的 5 门课 reward **逐位无影响**，已验证 60/60 不变）；② 新增 `_clear_metrics()` + c6-bonus 专属单调行序列（tick 0→3000、清场于 tick=600 ⇒ 封顶上界 1200）共 4 case（cleared × {timeout, stage_clear}、uncleared × {timeout, lives_exhausted}）；③ 差分验证：`timeout/cleared` 去掉 `wClear` 项 Δ=**+4.0**、去掉 tick 豁免 Δ=**+18.0**，其余三种 Δ=0 ⇒ 两个新机制都真正参与；④ `cleared-timeout` 总额 == `stage_clear` 总额（均 −10.0），设计意图入 golden。golden 60→64 case，原有 60 个 reward 逐位不变。
- **c6-bonus 起点/教师在新口径下重测（已做）**：新 `win = stage_clear ∪ cleared` 抬高同一权重的读数，而 `gate_check.py` 直接吃 `teacher.wins/games` + eval 行 win_rate ⇒ 两个基准必须同口径重测，否则"学生被抬高、基准仍旧"会白过门。实测（`eval-course-ckpt.ts --course tmp/c6-pickup-strict.jsonc --seed0 860001 --games 100`；旧记录的 kills 3.54 / phits 0.57 / zero_kill_frac 0.14 **逐位复现** ⇒ 只口径变）：bc `c6-pickup.it35` 31/100→**32/100**（`baseline_win_rate 0.31→0.32`）、教师 42/100→**43/100**（`teacher.wins 42→43`）、教师 `timeout_frac 0.03→0.02`（剔除"已清场被截断"，同 `evalboard/stats.ts`）。G1 有效门槛随之 0.36→**0.37**。逐局证据 `tmp/c6bonus-{teacher,bc}.jsonl`。同时订正 c6-bonus 头注释 4 处与磁盘不符的说法（"只改 2 处 / gates 全锁死"、"gates 原样继承"、"报数并列 win/cleared 两口径"、params 名 `wTickFree`→`wBonusTicks`）。
- **仍欠**：① metrics v5 + `win` 口径变更的 `DECISIONS.md` 条目（含 golden 覆盖扩充与 `_v7_corpus` 哨兵改写的理由）。② **其余课程的基准仍是旧口径**：`c6-pickup2`/`c6-pickup3`（0.31 / 42）、`c4-dodge`（0.72 / 68）、`c6b-margin`（0.22 / 42）——凡还有在跑的腿，需按各自 stage 重测；`gate_check.py:293-298` 的 timeout_frac 回退分支仍按**原始 outcome** 算（与 TS 侧新口径不一致，旧行才走到该分支）。

---

---

## §1 外围组件巡检：goal 热图静默常量（生产档目标策略失效）+ eval 墙损失测 + 我引入的 payload 回归（2026-09-10）

用户指令："检查一下其它组件（sampler-agent, cloudflared, src/nn, export-rl-rollout,
export-eval-game, ...）有没有 bug 或者可以优化的空间"。方法：3 个只读子代理并行 + **逐条回验**
（子代理给的路径与行号一律自核——本轮 5 条外部结论里 3 条是假报，见 §1.5）。改动前先做决定性复现。

### 1.1 ★`goalForward` 目标热图在生产架构下恒为常量（HIGH）

**病根**：wasm 卷积段跑完只回拷 `pooled`（256 B），`offBufA` 写进 wasm 线性内存后**从不搬回 JS**。

    conv-wasm.ts   feats(..., offBufA, offBufB, offBufC, offPooled)
                   pooled.set(f32At(offPooled).subarray(0, pooled.length))  ← 只有这一行回拷
    infer.ts       goalHeatmap[p] += w * this.bufA[...]                    ← bufA 只在非 wasm 分支被填

**决定性复现**（合成 h16/d2 与 h64/d8 合法权重，同一份代码）：

| 架构 | 主干路径 | 换输入后热图 max&#124;Δ&#124; | 热图不同取值 |
|---|---|---|---|
| h16/d2 | TS 手写循环 | 3.889e+2 | 676/676 |
| h64/d8 | **conv-wasm** | **0.000e+0** | **1/676（常量）** |

用真实生产 golden（`goal_net.py --golden --h 64 --d 8`，本次新增 fixture `goal-golden-wasm.json`）：
热图对 py 期望 max&#124;Δ&#124; = **1.245e+1**，而 engage 头 7.6e-6 正常。

**影响面**（生产必踩——`goal_net.py` 默认 `--h 64 --d 8`）：`export-eval-game.ts`（policy=goal 的
评估）、`export-goal-rollout.ts`、`export-counterfactual-goals.ts`、`src/nn/goal-executor.ts`。

**为何一直没被发现（结构性教训）**：TS 侧有**两套等价卷积实现**（TS 手写 / wasm），而 golden 是
**按档位分工**覆盖的——`goal-golden.json` 是 `h=16/d=2` 瘦身档（注释自陈"主干不触发 wasm"），
`student-golden-wasm.json` 是 `h=64/d=8` 生产档但**只覆盖 student 三头**（消费 pooled，回拷正常）。
⇒ 测试跑 TS 路径、生产跑 wasm 路径，两条永不相交，wasm 专属缺陷零覆盖。**新增头/新架构时，
必须同时补一条生产档 golden，别只补瘦身档。**

**修法**：`run()` 补 `bufA.set(f32At(offBufA).subarray(0, bufA.length))`；`runStudentConvWasm` 以
`bufA.length !== H*SP` 作为"架构不符"守卫（不符即退 TS 原路径，正确性优先）。回拷成本实测
**2.9 µs/次 ≈ features(6.9 ms) 的 0.04%** ⇒ 无条件拷贝，换掉"某个头悄悄读陈旧空间特征"这类静默 bug。

**回归**：新增 `tests/fixtures/goal-golden-wasm.json`（h=64/d=8，422 KB，同 student-wasm 量级）+
`tests/nn/goal-infer.test.ts` 一组 5 例：三头对 py golden（热图 ≤1e-3，修复后实测 1.1e-5，修复前
1.245e+1）+ **"热图随 obs 变化"**（通道 0/1 对调；修复前恒 0、676 格只剩 1 个取值）。

### 1.2 eval `baseWallIntact` 恒等于 `baseWallTotal`，baseIntegrity 被钉死在 1.0（MED）

`export-eval-game.ts` 只在 telemetry 初始化时赋值一次，循环内从不更新（对照：`export-rl-rollout`
每决策步重算、`export-observations` 每 tick 重算）。下游 `godai-score.ts`：

    baseIntegrity = 0.55 + 0.45·clamp01(baseWallIntact / baseWallTotal)   // 分子恒等分母 ⇒ 恒 1.0

**实锤（两份独立证据）**：

- **在野扫描**：`tmp/**/_eval_report.json` **173 份，100% `intact == total`**（含 100 个 gameover、
  61 个 stage_clear）——真实对局里墙被打掉却从不记录。
- **A/B（修复后重跑同一 stage/seed）**：s5/seed1 随机策略 → 终局 `5/8`（修复前必为 `8/8`）；
  s5/seed5 基地存活、墙被打到 `1/8` ⇒ `baseIntegrity = 0.606`（**修复前 1.0**）——即注释里
  "墙被打秃但基地还活着"这个领先指标此前完全丢失。

**修法**：终局（循环退出后、构造 `scorable` 前）取一次 `tel.baseWallIntact = countBaseWall(world)`。

### 1.3 我上一提交（`d183997`）引入的回归：陈旧 pending job 永不下架（MED，我的责任面）

`tools/training/hub.ts` 的 `drainStaleJobs()` 仍硬编码 `unlinkSync('payload.zip')`，而同一提交把
payload 容器改名 `payload.tar.xz`（`protocol.PAYLOAD_NAME`）⇒ `unlinkSync` 恒抛 → 被
`catch { /* already gone */ }` 吞 → `n` 恒 0 → **账本里所有陈旧 pending job 保持可领**
（`hub_server.claimable_job_ids` 以 `find_payload` 存在性判定），真 worker 会白烧 GPU 租约去跑
已死运行的局——恰是该函数存在的唯一理由。

**修法**：改为按前缀扫描 job 目录删 `payload.*`（而非硬编码全名），从根上免疫再次改名；`hub.ts`
该处注释同步更正。**回归**：新增 `tests/training-drain-stale.test.ts` 5 例（新名/旧名/两名并存
都被下架；非 payload 文件与已 completed job 不动；坏账本、缺目录不抛）。

### 1.4 `cacheHits` 少计（LOW）

`tools/agent/sampler-agent.ts` 的 `/v1/result` 轮询端命中 `resultCache` 时未累加 `cacheHits`
（提交端有）。trainer 轮询是主要取包路径 ⇒ 缓存命中率被系统性低估。已补。注：该字段目前**只产出、
仓内无消费方**（经 `/v1/status` 暴露），影响限于对外状态接口的口径正确性。

### 1.5 被证伪的假报（记录以免重走）

| 假报 | 证伪 |
|---|---|
| 子代理给的路径 `tools/dist/sampler-agent.ts` | **文件不存在**（只有 `tools/agent/`）——其行号与结论整体不可信 |
| "异步提交路径缺 resultCache 短路" | 两条路径都有（提交端 / 轮询端各一处） |
| "export-rl-rollout 每局重建模型，是可优化项" | `runOne` 确实逐局 `buildModelFromText`，但 `--pack`（生产路径）强制**一进程一局** ⇒ 只跑一次；实测 1.228 ms/次（90% 为 base64→f32），不值得改 |
| "`npy.ts` 解析校验不严" | `npy.ts` 只有 `writeNpy/writeShard`，**无 reader** |
| "cloudflared 日志泄漏" | `tmp/cloudflared-*.log` 归 `tools/tmp-clean.py`（`.log` 在后缀表内，保留 N 天） |

### 1.6 巡检确认为健康（勿动）

- `stepCloudflared`：隧道复用判定走**本地 cloudflared `/ready`**（而非穿隧道 ping）——避免 hub
  出网劣化造成假阴性，是对的；3 次重试 + edge 20 s 确认 + "失败保留基础设施"均为刻意设计。
- `src/nn/obs-encoder.ts`：`obs`/`scalars` 预分配 + `fill(0)`，无每 tick 分配。
- `conv-wasm.ts`：权重上传用 `uploaded !== stemW` 实例指纹守卫，越界有显式 `check()` 抛错；
  wasm 加载/运行异常**有** `console.error`——§1.1 之所以静默，是因为它发生在 wasm **成功**时。

### 1.7 门禁

`bun run check` 绿（tsc + check-decisions + `bun test --parallel`）；oxlint **0 error**（20 条既有
warning，非本次引入）；oxfmt 对本次改动的 6 个源码/测试文件 clean。

### 1.8 连带影响：`docs/goal-nn.progress.md` §3 的 goal 结论需重新解读

§1.1 的缺陷**必须在历史结论里对账**。`docs/goal-nn.progress.md` §1「性能实测」自证 goal 前向
探针跑的是 **h=64/d=8** ⇒ T9a 金丝雀那批 goal 策略实验**全部在 wasm 路径上**，即热图恒为常量、
argmax 恒选同一格。

- **`goal 0.05%`（canary ②）**：除已记录的 executor 短板，还叠加本缺陷 —— "目标选择轴从未生效"。
  该节"执行层短板与**目标选择**、学习无关"的归因**需修正一半**。
- **`goal-god 0.0%`**：零网络，**不受本缺陷影响** ⇒ "执行层无生存能力"这一结论**仍成立**。

已在 `docs/goal-nn.progress.md` §3 追加该注记。**卡 A0 的 goal 重测必须在修复后的代码上做**，
否则重测仍在测常量热图（原计划的重测因此不作数）。

---

---


## §20 决策正文归档（搬自 `DECISIONS.md`，2026-09-23）

> 2026-09-23 把 `DECISIONS.md` 里这些条目的**正文全文**搬到这里（索引行与编号仍留在
> `DECISIONS.md` —— 编号永不重排）。锚点 = `### §<旧编号>`。

### §2026-09-15-gate-trigger-scope（2026-09-15，用户提问「tools 改动有必要跑 src 的测试吗 / src 改动能不触发 dashboard 吗」⇒ 三处触发面重排）

- **背景（全部实测）**：
  1. 根 `bun run test`（`tools/test-silent.ts`）按 basename 把改动映射到同名测试，**命中就只跑那几个**：
     `src/config/stages.ts` → 1 个（而 **50** 个测试直接 import 它）、`difficulty.ts` → 1（39 个）、
     `combat.ts` → 3（13 个）⇒ 改配置类文件能绿着过去而 49 个依赖测试一个没跑；
     **映射为空才 fallback 全量** ⇒ 反倒比命中更安全。非 heavy 全量实测 **6s**、tools 子集 5s。
  2. freeze 触发按扩展名判（`\.(ts|js|mjs|cjs|tsx|jsx)$|^src/`）⇒ `tests/**` 与 `src/assets/**` 也各付
     ~100s。**（2026-09-15 当日实测更正：实为 ~3.7s／21 组合全网格（16 核 Linux），旧数字错 ~27×。
     成本现由 `tools/probe-det-baseline.sh` 与 pre-commit 自报 elapsed；豁免的**安全论据（签名闭包）
     不受影响**，但收益从「百秒级」降为「秒级」——是否还值得留这套豁免（及其 freeze-scope 断言）
     属独立待决项。）**
  3. `dashboard/` 只读消费仓根 10 个模块，却**三条防线同时失效**：hook 只在 staged 含 `dashboard/**` 时
     跑它、根套件 `SKIP_RE` 排除它、CI 里没有它的 workflow ⇒ src/tools 改断契约会静默落地。
- **备选与否决**：保留 basename 窄跑并「修正确性」（补 import 图 / 传递闭包）—— 否，为省 1s 引入一个
  失败模式是**漏跑（假绿）**的启发式不划算；把 `tools/**` 一律豁免 freeze —— 否，签名由
  `tools/diag/per-seed-diff.ts` 生产，探针口径改动**真会**移动签名；把 dashboard 门禁也挂进 hook
  （按消费清单触发）—— 否，那会把「每人都必付」的路径变成依赖清单维护点，CI 才是它该有的节拍。
- **决定**：① `test-silent` 删除 basename 映射与 `--strict`：**代码改动一律跑全量**（仍保留 heavy 排除、
  静默输出、失败单跑取详情，以及「跳过无关改动」= 纯文档/课程配置/dashboard-only）。② `FREEZE_STAGED`
  豁免 `^tests/` 与 `^src/assets/`，理由由 `tests/freeze-scope.test.ts` 钉住（算 `per-seed-diff.ts` 的
  import 传递闭包，断言与这两目录零交集、且非空防假通过）。③ 新增 CI `.github/workflows/dashboard.yml`：
  触发 = `dashboard/**` ∪ 它消费的 10 个仓根模块，清单不得落后于真实 import
  （`dashboard/tests/ci-scope.test.ts` 自动核对，拼错的路径也拦）。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯——「只跑受影响的测试」是每个 agent 都会想做的优化
  （本仓历史上正是这么写的），而「tools 改动要不要跑 src 测试」的直觉也会反复出现；
  ③ 无法就近表达——跨「根 runner 语义 / hook 触发 / CI 范围」三个面，代码注释各说一半。
- **违反后果**：恢复 basename 窄跑 ⇒ 改配置类文件只跑 1 个测试即绿灯（静默漏测）；放宽 freeze 到
  `tools/**` ⇒ 改探针即可绕签名门禁；dashboard CI 触发清单不跟 import ⇒ 契约破坏重新回到静默落地。

---

### §2026-09-15-heavy-tests-criterion（2026-09-15，用户提问「根套件实测墙钟 ~6s 下，HEAVY_TESTS 名单还有意义吗」⇒ 判据改为实测 + 名单纠错）

- **背景（全部实测，16 vCPU；非 heavy 全量 179 files = 6.1s）**：`HEAVY_TESTS` 的注释一直写着「keep in sync
  with measured wall-time (see per-file profiling)」，但**那份 profiling 根本不存在**，于是两个条目都腐烂了：
  `godai-score-gate` 记 ~19.5s（实测 11.7–13.1s）、`calibration` 记 ~2.5s（实测 **0.66–0.71s**，5/5 稳定——
  它的 full sweep 早已移交 CLI `tools/eval/calibrate.ts`，测试自己写着「小 stage 子集，完整版走 CLI」）。
  逐项代价：排除 `godai-score-gate` 使套件 6.1s → 18.6s（**3.0×**）；排除 `calibration` 只值 ≤0.7s（实测落在
  噪声带内）；次慢的 `nn/intent-rl-rollout`（3.8s/1.7s）剔除也只省 ~0.6s。
- **判据（改为可复核）**：**只排除「单跑墙钟 ≥ 整份非 heavy 套件」的文件**——它一个就抵得上整份套件。
  低于这条线时 `--parallel` 会把它填进既有尾巴，剔除收益 ≤ 它自己的运行时间，却白送一个静默盲区
  （CI **无**根套件 workflow ⇒ 本地 hook 是它唯一的自动化通道）。旧口径「exceeds a few seconds」是错的：
  「几秒」正是排除不产生收益的区间（`calibration` 0.11×、`intent-rl-rollout` 0.3–0.6× 全落在里面）。
- **备选与否决**：① 保留 `calibration` 在名单里——否，省不到 0.7s 而丢掉它的本地覆盖；② 把判据写成一个
  写死的秒数阈值——否，那正是腐烂源（两个数字全被测错）；改为「用 `bun tools/measure-suite.ts` 现测现判」，
  该工具把「每文件墙钟 + 条目达标判定」变成一条命令（原注释引用的 profiling 从未存在）；③ 维持 §1.4 的
  ⚠ 软提示——否，它挂在绿行摘要上没人会读，拦不住名单腐烂（先例：`god-ai-gate` 重命名后残留，§234）。
- **决定**：① 名单只剩 `godai-score-gate`；② 判据与实测数字写进 `tools/test-silent.ts` 的 `HEAVY_TESTS`
  注释与 `docs/agents.details.md` §5.3；③ 新增 `tools/measure-suite.ts`（只读剖面 + 达标判定）；
  ④ `tests/test-silent-scope.test.ts` 增硬断言：条目必须命中真实测试文件、名单非空、名字口径与执行器过滤
  一致且在根套件枚举内（实测：伪造死条目 → 2 fail；复原 → 6 pass）。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯——「CMA-ES calibration 听着就像重负载」会让下一个 agent
  把它加回名单，「多排除几秒无所谓」也会持续压低名单的可信度；③ 无法就近表达——判据跨「runner 注释 /
  测量工具 / 硬断言」三处，且必须推翻 §1.4 记录的软提示口径。
- **违反后果**：往名单里塞低于地板的文件 ⇒ 名单变装饰、覆盖率静默流失；名字腐烂不管 ⇒ 12s 的 score gate
  静默塞回每次提交（提交测试步 6s→19s）；恢复「写死秒数」的判据 ⇒ 数字再次腐烂且无人能发现。

---

### §2026-09-17-goalnn-python-gate-parallel-policy（2026-09-17，python 门禁并行策略：worker 数 × CPU 内线程数必须成对调；"-n 4 最优" 作废）

- **背景**：用户「检查 python 全量门禁，提高可维护性，减少耗时」。门禁 39~50s，而 ruff(~1s)/
  mypy(~4s) 全藏在 pytest 后面 ⇒ 唯一瓶颈是 pytest 步。
- **根因**：旧默认 `-n 4` × torch 默认内线程（= 物理核）⇒ 4×16 = 64 线程抢 16 核（**超订**）。
  `docs/goal-nn.progress.md §25`「n=4 最优、auto=16 反更慢」是超订假象的读数（worker 越多越慢
  本身就是证据），不是「torch import 开销」。16 核实测均值（同机交错 3 次/项）：`-n 4` 默认 36.3s /
  `-n 12` 默认 44.7s / `-n 4` 线程=1 39.7s / **`-n 12` 线程=1 = 24.7s**（`-n 8/16/auto` 线程=1 同水平）。
- **备选与否决**：① 只加 worker——否（44.7s，更慢）；② 只封线程——否（39.7s，无收益：小并发盖不住
  尾部长用例）；③ `-n auto` 且不封顶——否（worker 数 = 逻辑核，无法给内存封顶；本机 auto ≈ n16 同水平
  但峰值 RSS 随核数线性涨）；④ 把新数字写死（如 `-n 12`）——否（换机器就错），改「核数派生 + 上界」。
- **决定**：门禁 worker = `min(核数, 12)`（`NN_GATE_NPROC` 覆盖）、CPU 内线程 = 1
  （`NN_GATE_THREADS` 覆盖，0 = 不设；须在 python 启动前 export OMP/MKL/OPENBLAS）。
  **同策推广到所有本地入口**（防「门禁快、日常慢」漂移）：`task.py`（线程封顶收在共用的
  `clean_env()`，四个 target 一律 `-n auto`——**同时回退 2026-09-15 把 `-n auto` 判为
  「沙箱 ~34% 停滞头号嫌疑」的误判**）、`nn-training/Makefile`（`NPROC ?= auto` / `THREADS ?= 1`）、
  CI `nn-training.yml`（job 级封顶 + 单测层由串行改 `-n 2`）。**`-n 4` 不再是任何入口的默认值。**
- **违反后果**：任一入口把封顶 export 删掉、或把 worker 写死回 4，全量立刻退回 ~36s——而**用例仍全绿**，
  只有人肉计时才看得出来（静默退化）。由 `nn-training/tests/test_githook_scripts.py` 两条静态护栏钉住
  （已做变异验证：删 export / 退回 `-n 4` / 去上界 3/3 被抓住）。
- **证据与完整表格**：`docs/nn/engineering.md` §9。另：门禁不再重复加 `-q`（addopts 已有 ⇒ 原本
  `-qq` 吞掉了「N passed in Xs」，hook 日志里看不到用例数与耗时）。

---

### §2026-09-19-eval-tools-node-upgrade（2026-09-19，一次性评估工具复用训练循环的节点升级守卫 + m1-eval 补节点门）

- **背景**：`eval-course-ckpt.ts` 遇到 stale 节点只打一行 `codeHash mismatch … — skipped`，
  既不升级也不汇总告警；全灭时 `hybrid failed — falling back to local` 后**照跑本地**，
  汇总行不区分节点/本地（2026-09-19 实测：x20 it96 跑 ladder-c20-lives1，5 台远端全 stale，
  唯一的局其实是 self 节点跑的）。`m1-eval.ts` 更旧：`tryActivate` 只看 HTTP 200，
  **根本没有 codeHash/bun/能力位门** ⇒ 陈旧节点会被当可用算力，不同 era 的结果混进同一份读数。
- **备选与否决**：① TS 重写护栏/dirty 判据 —— 否，dirty 是字节级判据（`git ls-files -s` +
  `git cat-file --batch`，不能用 `git status`：autocrlf 会把 CRLF 污染藏起来，2026-09-09 mac
  事故），重写就是双语漂移 + 重演事故；② 只告警不升级 —— 否，用户要「能推升级」；③ 在 TS 里
  内联 `python -c` 拼脚本 —— 否（argv 引号/路径脆弱，且仓库规定 nn python 须经
  `tools/githook/nn-py-safe.sh`）；④ 升级默认开 —— 否，一次性判读工具不该默默重启别人的机器。
- **决定**：① 新增 `nn-training/dist_upgrade_cli.py`（stdin JSON spec → 逐节点调
  `dist_common.request_upgrade_guarded`，**单源**：护栏与 dirty 判据仍只在 Python 一处）；
  ② 新增 `tools/lib/node-upgrade.ts`（经 `nn-py-safe.sh` 拉起该 CLI；memo 文件
  `tmp/node-upgrade-memo.json` 跨调用去重，语义同 `_RESTART_SEEN`；**永不抛**——失败以
  `{ok:false,error}` 返回供调用方响亮告警）；③ 新增 `tools/lib/dist-node-gate.ts`（门 +
  聚合 WARN + provenance，两个工具共享；`eval-course-ckpt` 保留同名再导出，既有测试
  导入面不变）；④ 两工具接上：门逐条日志 + 收尾聚合 WARN（可用数/原因/两侧 codeHash）+
  provenance（`node:<id>`/`local` 逐局计数）+ **`--upgrade-nodes` 显式开关**才下发 pull+restart；
  ⑤ m1-eval 补上原本缺失的 codeHash/bun/能力位门（与 rollout/eval 同源）。
- **违反后果**：TS 重写 dirty 判据 ⇒ 重演 autocrlf 掩盖 CRLF（mac 卡 40 分钟）；升级默认开 ⇒
  判读脚本随处重启节点；不回填 memo ⇒ 每次跑 CLI 都再捶一遍同一 stale 节点；m1-eval 无门 ⇒
  陈旧节点的局混进 gate/scoreV7 读数且**看不出来**（本条的起点）。
- **落地**：`nn-training/dist_upgrade_cli.py` + `tests/test_dist_upgrade_cli.py`（8 例：spec 校验 /
  current 短路 / 映射到共享守卫 / dirty=null 真探测 / self 不探 dirty / dry-run 零 POST / 端到端）；
  `tools/lib/{dist-node-gate,node-upgrade}.ts` + `tests/{dist-node-gate,node-upgrade}.test.ts`
  （+21 例，含真子进程 dry-run）；`tools/sim/{eval-course-ckpt,m1-eval}.ts` 接线。
- **证据**：mock 节点全链实测——TS → `nn-py-safe.sh` → CLI → 守卫 → `POST /v1/restart` → 202，
  mock 端看到 body `{"pullBranch":""}`（self/回环语义强制禁 pull，护栏③ 生效）→ 回传
  `restart-requested` 并落 memo；真节点告警实测：`WARN dist nodes: 1/6 usable (self) ·
  5 stale (mac,a95,a97,a96,gcs)` + `provenance: node:self=1（共 1 局）`；m1-eval 同样输出 6/6 门
  结果。门禁：根 `bun run check` 1896 绿 · nn-python-gate 1285 绿。
- **未做**：控制台里的节点升级按钮（仍只有 CLI/训练循环）；`--require-nodes` 这类「无可用节点即
  非零退出」的判定开关（本轮只做到「响亮说出来」）。
- **追记二（同日，本机槽位必读 rl-config——用户 2026-09-19 实测报障）**：一次 1600 局的
  `eval-course-ckpt`（`--dist-local` 未给）跑出 `local=730 / 远端 870`，而 `nn-training/rl-config.json`
  写的是 `rl.local_slots: 0`（= 本机不参与、全交集群）。根因：两个工具的 `--dist-local` 缺省写死
  `workers`（物理核数 15），**从不看配置** ⇒ 机器口径被静默覆盖。
  - **决定**：本机槽位取值序 = 显式 `--dist-local` > `policy.evalLocalSlots`（评测专用旋钮，
    与 `rl/eval_local.py` 的 `EVAL_LOCAL_SLOTS_DEFAULT` 同序）> `rl.local_slots`（机器级，
    `dashboard/src/core/slots.ts` 同源）> 物理核数（配置未约定时的兜底）。实现为共享纯函数
    `configLocalSlots()`，两工具共用；启动日志固定打印生效值与**来源**（`--dist-local` /
    `配置 rl.local_slots` / `物理核数`）——缺省值从哪来决定了「本地 N 局」是配置意图还是意外。
  - **行为变化（重要）**：本机 **0 槽位现在真的意味着 0**。节点忙/不可达（503、ping 超时）时
    不再静默用本机补上，而是响亮失败（`incomplete hybrid results N/M — exit 1`）；想留本机兜底
    就显式 `--dist-local N`。节点被别的作业占满时这是预期行为，不是回归。
  - **同时修掉我上一版告警文案的误报**：`local>0` 曾被一律写成「节点部分失败或本地兜底」，
    把健康混跑报成故障；现只对**远端零参与**喊 WARN，混跑只报份额并点明由 `--dist-local` 决定。
  - **验证**：真配置下 16 局——`distLocal=0（来源：配置 rl.local_slots）` → `远端 8 / 本地 0`；
    m1-eval 同配置（单节点假配置）4 局全走节点；新增 `configLocalSlots` 3 例 + provenance 重写用例。
- **追记（同日，真节点收敛 + 两个判读修正）**：
  1. **升级真的收敛了**：`--upgrade-nodes` 后轮询 `rl-config.json` 全部 6 节点，`codeHash` 均为
     `ba6f7b4eda13…`（= 本机）、`agent=52cf887`（= HEAD）。**但收敛有尾巴**：mac/a95/a97 秒级收敛，
     a96 与 gcs 在 push 后的下一分钟里才翻过来（a96 曾返 502、gcs 曾保持 stale）——所以「push 成功」
     与「节点已可用于本批」不是同一时刻，判读时以**再 ping 一次**为准，不要拿 push 当次结果下结论。
     节点环境不支持远控升级属正常（本机隧道/反代差异），不必追求 6/6，一两台成功即达到目的。
  2. **修：小批量下排头的节点会秒光整批**（`fanOutOrder`）。链体在首次 await 前**同步** claim，旧代码
     按配置顺序把每个节点的并发链一次性起完 ⇒ 第一个节点（常是 self）把整批任务吃掉：实测 5 节点可用、
     8 局的批量里 `provenance: node:self=8`（远端一条没分到）。现改为轮转 spawn（每轮 1 条本地链 +
     每节点各 1 条），只改启动顺序，共享游标 + 尾部竞速语义不变；纯函数 + 单测 `fanOutOrder`。
- **追记三（同日，节点探测/判 stale 也回归 Python 单源——用户裁定）**：用户指出「Python 侧早就有这套
  节点通信与重试且经长期实战检验，别再在 TS 里重建」。复核属实：上一条的 `--upgrade-nodes` 虽然把
  **护栏**（dirty 判据/去重/self 禁 pull）交给了 `dist_upgrade_cli.py`，但**「谁是 stale」这一步仍在 TS 里
  自己 ping + 比 codeHash**——而 `dist_common.upgrade_stale_nodes(cfg, expected, branch, ...)` 早就是
  训练循环里那个「ping 每个 enabled 节点 → hash ≠ expected → request_upgrade_guarded」的完整实现。
  - **决定**：探测与判门也**只能有一处实现**。`dist_upgrade_cli.py` 增扫描模式（spec 给 `cfg_path`，
    由它自己 ping）；新增 `dist_common.seed_restart_state(entries)` 让一次性进程把调用方持久化的
    跨调用 memo 预置回 `_RESTART_SEEN`（判据仍是同一函数，调用方只存状态不写规则）；
    `upgrade_stale_nodes` 的结果补 `pingHash`（调用方写 memo 用的键）。`tools/lib/node-upgrade.ts`
    随之改为**扫描客户端**：spec = `{cfg_path, expected_hash, branch, seen, dry_run}`，TS **不再 ping、
    不再比 hash**；memo 键改为全量 hex（`nid|pingHash|expectedHash`，旧截断键自然失效、无害）。
    `eval-course-ckpt.ts` 因此不再 import `pingNode`/`nodeGateReason`；`m1-eval.ts` 的 `--upgrade-nodes`
    同样只传 cfg 路径。
  - **证据**：真集群 `--upgrade-nodes` 实跑（2 局 × x20 it96 × ladder-c20-lives1）——
    `node self/mac/a95/a97/gcs: 已是期望 codeHash` · `node a96: ping 不通`，随后 dispatch 照常
    `5 nodes → settled=1/1`（探测由 Python 做，TS 侧零 ping）。门禁：根 `bun run check` 绿 ·
    `nn-python-gate` 绿（ruff/mypy + 1296 例）。新增 Python 测试 6 例（扫描/stale·current 分流/dry-run
    零 POST/seen→dedup 真闸门/结构错误），重写 `tests/node-upgrade.test.ts` 为 spec 契约 + memo 往返。
  - **仍留的重复（明确不做假动作）**：`m1-eval.ts` 自己的**分派链**（判门/rescan/尾竞速/权重下发）
    还是 TS 实现——它的产物（`[m1-eval] WIN RATE` + 顶层 JSON report）被 `rl/eval_m1.py` 解析，
    整段改走 Python 得新写一个跑**内置关**的入口并接回训练循环契约，不是本轮的范围；本轮只把
    「升级探测」这一个已确认的重建点收敛掉。
  3. **加：`claims` 注脚（分派口径）与 `provenance`（结算口径）配对读**——实测一批 8 局出现 11 次分派：
     a95/a96/a97 各领到 1 局但结果被尾部竞速的重复副本抢走（`fanoutDup=2` 的设计使然，同一 (stage,seed)
     重跑结果逐字相同，故不影响读数），只看 provenance 会误读成「远端没拿到活」。
     `m1-eval` 本轮仍只打 provenance（其本地链先入队，分配口径的同样问题未动）。

- **追记四（同日，`eval-course-ckpt` 的 10054 真因：权重桶撞车；含一处我自己引入的回归）**：
  用户指出「`eval-course-ckpt` 还是有问题」，证据是另一 agent 的课程记录
  （`nn-training/curricula/x20-powered.jsonc` 第 5 行：「它占着 dist 集群，本腿 it0 探针会被 10054
  挤死」）。逐层排查（不是猜）得到四个独立缺陷，前两个是真因。
  1. **本机槽位链被我上一轮的重构悄悄弄回归了**（用户上一轮报障原样复活）：`configLocalSlots()`
     是上一轮为「`rl.local_slots: 0` 必须真的 0」建的共享纯函数，m1-eval 还在用，但
     **`eval-course-ckpt` 改成调 Python 后把它丢了** —— 缺省 `localSlots` 不写进 spec ⇒ Python 侧
     取 `policy.evalLocalSlots` 缺省 **4** ⇒ 整条配置链被跳过（`eval_course_once.py` 的注释甚至
     声称读了 `rl.local_slots`，而 Python 侧从来没读过）。
     - **修**：求解器收敛成 `dist-node-gate.pickDistLocal(explicit, cfg, fallback)`（纯函数，
       **两个工具共用**，m1-eval 的内联三元也换掉）；`eval-course-ckpt` 启动日志打印生效值 + 来源，
       并**总是**把解析结果写进 spec。实测 `distLocal=0（来源：配置 rl.local_slots）`。
  2. **10054 的真因 = 一次性评估的权重文件被训练作业扫掉**（不是「节点忙」）：节点侧按 kind 收敛
     权重文件（`workdir-cleanup.WEIGHT_FILES_KEEP = 4`），而训练作业**每轮**往 `rollout` 桶 POST 新
     权重 ⇒ 我们那份固定权重（`weights-rollout-d66378e…`）在几秒内被扫掉；但另一支 agent 进程
     （同机还有 8789 那支，共享 `tmp/dist-agent`）的内存桶仍答 "kept" ⇒ 任务子进程 `ENOENT` 退出 ⇒
     agent 直接断连 ⇒ client 只见 `WinError 10054`，与「节点满负荷」在传输层**不可区分**。
     重试耗尽 ⇒ 单元 0/50 settled；`local_slots: 0` 时整批 0 行、exit 1（这正是它「被挤死」的表象）。
     - **修**：新增 `ONESHOT_EVAL_KIND = "eval"`，两个一次性入口（`eval_course_once` /
       `eval_m1_once`）把权重 POST 进专用桶（agent 的 `x-kind` 本就能任意分桶），训练作业的
       `rollout` churn 扫不到它；**按关键字传** —— 位置写错会静默回落 `rollout`（我第一版真踩了：
       写到 14 号位置 = `init_sha16`，行为与修复前一模一样，靠单测才发现）。
     - **证据（训练作业**在跑**时实测）**：修复前 —— `weights[rollout] -> self (kept)` 后 8 局全
       `背压 6/6 耗尽 + 真失败` ⇒ `provenance: none`、0 行、exit 1；修复后 ——
       `weights[eval] -> … (kept)`、`weights-eval-d66378e3….json` 存活（同时 4 份 rollout 仍被
       KEEP 轮换）、**16/16 全远端、local 0、exit 0**。
  3. **10054 不再被当成节点故障**（既有实现缺陷，独立于上条）：`dist_common.fetch_task` 把
     连接被重置/超时/408·429·5xx 标为 `transient`（`DistError.transient`），`batch_eval` 对它**背压
     重排 + 指数退避**（上限 8s）而**不计** `nodeFailStreak`，并把「背压次数/真失败次数」与
     `provenance` 一起入账；单元 0 局且本机槽位 0 时打一行**响亮提示**指向权重文件缺失这一真因。
     旧行为：一瞬 10 次 10054 把 6 个节点在 1 秒内全部熔断（与 rl/bc_dispatch 的 busy 背压同源问题）。
  4. **runDir 复用会污染 provenance 判读**：`eval_log.jsonl` 每次运行都重建，而
     `dist-agent-meta.jsonl` 只追加 ⇒ 200 局的重跑里 meta 积 343 条（含上一轮 114 条远端条目），
     照它判「是否降级本地」会得出**反的**结论。新增 `_reset_run_ledgers()` 开跑前两个都清。
     （顺带纠正上一轮的一处判读：那份「200 局全 local」的产物来自**第二次运行**、且它的
     `spec.json` 写着 `noNodes: true` —— 是调用方显式 `--no-dist`，不是节点被熔断。）
  - **验证**：真集群 + **训练作业在跑**下三种模式实测（默认 `distLocal=0` 16/16 远端 ·
    `--dist-local 2` 打印来源且 `[local ×2]` 生效 · `--no-dist` 全本机）；`m1-eval`（god，2 局）
    `localSlots=0 kind=eval` 全远端。门禁：根 `bun run check` 绿 · `bun run build` 绿 ·
    `nn-python-gate` 绿（ruff/mypy + 1312 例）。新增测试：TS `pickDistLocal` 4 例 + 槽位链 2 例；
    Python `_reset_run_ledgers` 1 例 + **专用 kind 契约 1 例（源码级守卫，已实测对「位置传参」变体变红）**。

- **追记五（同日，800 局探针暴露两个真缺陷：起跑 7s 的串行 ping + 慢节点拖尾巴；事件级日志落地）**：
  用户实测「等了十几秒 CPU 才满」「CPU 满一阵又掉档一阵子（几十秒）」⇒ 要求把**权重传输完毕**与
  **评测结果返回**打到日志里排查（命令：`x20-rebirth` it96 × `ladder-c20-lives1` × 800 局
  `--seed0 418000`，非判决段、仅诊断）。
  - **落地的事件日志**（新增，已真集群验证）：① `dist_common.post_weights_parallel` 逐节点
    `weights[kind] -> <节点> (<mode>, X.XXs)` + **阶段总计**
    `weights[kind] ready on N/M nodes in X.XXs (sha …)`（分发起点即此）；② `batch_eval` 逐单元
    `阶段 gate X.XXs alive=N/M` / `阶段 weights X.XXs ok=N/M`；③ 逐局 `→ <节点> s/seed` 与
    `← <节点> s/seed X.Xs ticks=… outcome=…`（配对即得**在飞曲线**）；④ 每 2s 在飞采样
    `⏱ pending=… inflight=… settled=…`；⑤ **被 ping 丢掉的节点不再静默**（原先只是 `continue`，
    实测 `alive=4/6` 时看不出丢的是谁、为什么）。开关 `EVAL_TRACE_EVENTS`（一次性工具缺省开，
    训练循环路径缺省关 ⇒ A/B/C 层日志逐字不变）。
  - **发现①（起跑慢）**：`阶段 gate 6.83s alive=4/6` —— 节点门是**串行** ping，每台预算 3s，
    两台负载高的节点直接吃掉 ~7s；而门**每单元重跑一次**。修：新增
    `dist_common.ping_nodes_parallel`（保序、并行）⇒ 阶段墙钟 == 最慢一台，实测 6.83s → **3.01s**；
    并把「谁掉了、为什么」写进日志（实测 `node a96: ping 失败/超时`）。
  - **发现②（CPU 掉档的真因：慢节点拖尾巴）**：单元内前 ~30s 快节点（self/mac/gcs 平均 2.9/3.7/3.8s
    每局）就干完 ~170 局，之后 **pending=0**，只剩配置固定并发（a95/a97/a96 各 7）的慢节点在跑：
    **a96 平均 124s/局（最大 180s）、a97 42.3s、a95 20.3s**（同一台机器上训练作业抢 CPU）⇒ 每单元尾巴
    1–3 分钟、本机 8 个槽位与其余节点全部空转（实测 u3：`pending=0 inflight=7` 卡了 ~170s，7 局全在
    a96）。**这 3 台只贡献 71/800 局（8.9%）却吃掉 63% 的节点秒**。首轮（297s）与次轮（653s）的差距
    就来自这里，不是网络。**调度策略怎么改尚未定**（见下），本轮只做到「看得见 + 起跑不再白等」。
  - **实测读数（非判决段，仅吞吐参考）**：800/800 全远端 · **local=0** · 653.2s / 1.2 games/s；
    来源分布 self=356 · mac=251 · gcs=122 · a96=32 · a95=25 · a97=14；pass 82/800=10.3% ·
    kills 6.27（与首轮逐值相同：同种子确定性 ✓）。**不要拿它当 it96 capability 读数**（段未预注册）。

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §22→§1 · 旧 §27→§2 · 旧 §30→§3 · 旧 §31→§4 · 旧 §33→§5 · 旧 §40→§6 · 旧 §42→§7 · 旧 §53→§8 · 旧 §57→§9 · 旧 §65→§10 · 旧 §93→§11 · 旧 §96→§12 · 旧 §97→§13 · 旧 §98→§14 · 旧 §102→§15 · 旧 §103→§16 · 旧 §106→§17 · 旧 §121→§18 · 旧 §128→§19
