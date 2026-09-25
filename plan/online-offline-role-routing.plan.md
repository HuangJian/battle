# Plan: online-offline-role-routing — 让「该由哪个盘执行」成为 job 的不变式

> **交付物**：本 plan + 代码（2026-09-25 已实施，见 §8 实施修订表）。
> **状态**：**已实施**（P1/P2/P3 落地；P4 未做，见 §3）。评审吸收记录见 §8。
> **背景**：`reports/online-offline-hot-switch-audit-2026-09-25.md`（六条不足 L1–L6 / 六条不变式 I1–I6）。
> **姊妹 plan**：`plan/switch-mode-drops-jobs.plan.md`（管"切换即撤单"）。本 plan 管"归属"。
> **两者关系**：必须同批或紧接落地 —— 只做归属会出现"旧 job 一直不可领"，只做撤单会出现"撤了单但仍派给错盘"。
> **阅读顺序**：§1 事实链 → §2 设计 → §3 分阶段 → §4 e2e → §5 DoD → §6 边界 → §7 待裁决 → §8 实施修订。
> **行号只作定位加速，判据看函数名。**

---

## 0. 一句话目标

**job 在发布时带上 `role`（该由哪个盘执行）；认领咽喉点按 `role` + 课程停摆两道闸过滤，不再看"课程当前 mode"来判断归属；
offline 盘的取包端点补 mode 闸。** ⇒ 无论模式切换多少次、无论 job 何时入队，都只派给**角色匹配**的 worker。

---

## 1. 事实链（摘自审计报告 §4，均已在代码里核实）

| # | 事实 | 出处 |
|---|---|---|
| F1 | job 形状**发布时**定死：有 `plan_bytes`⇒`run`，有 `rollout_spec`⇒`iter`，都没有⇒`ppo` | `rl/loop_steps.py`（`publish_job(...)` 调用点） |
| F2 | **没有"job 归属"字段**（manifest 有形状、有 `dispatch` 传输意图，没有"该谁来执行"） | 字段遍历 |
| F3 | `claim_next` 用**课程当前 mode** 判归属 | `hub_server.py claim_next` |
| F4 | `peek_jobs` 同样用 mode | `hub_server.py peek_jobs` |
| F5 | **`claim_job`（按 id 直领）不校验任何能力/角色**；且**按 id 领活的路不止它**——push 腿直接调 `Hub.claim` | `hub_server.py claim_job` / `push_dispatch._dispatch` |
| F6 | `X-Battle-Offline` 只在 `/jobs/peek` 被读；`claim` **从来不发**这条头 | `protocol.py` / `worker._sched_headers` |
| F7 | `_get_task_pack` **不查 mode**：鉴权→课名→文件存在→新鲜度门 | `hub_server.py _get_task_pack` |
| F8 | `CFG.course` 点名时走老路，**连 claim 都不发生**（`leases=None`）⇒ 三重闸全绕过 | `remote/offline_boot.py` |
| F9 | 切离线会自动导出 `task-<课>.zip`，且"已有包不动" ⇒ **切回在线后包还在盘上** | `dashboard/src/server/actions/course-mode.ts` |

**结论**：归属不是 job 的属性 ⇒ 切断 mode 就漂移。修法 = 把归属**钉进 job**，并让**认领咽喉点唯一**。

---

## 2. 设计（= 实施后的形状）

### 2.1 `role` 的定义、来源与兼容

- 字段：`manifest["role"] ∈ {"offline", "online"}`（键名常量 `protocol.ROLE_FIELD`）。
- **来源 = `kind` 的同一快照**（不引入第二个读盘点），映射表 `protocol.KIND_ROLES` 是**唯一**一份：

  | kind | role | 为什么 |
  |---|---|---|
  | `run` | `offline` | 整段自主（计划随 job 走、节点自己跑 rollout + PPO）⇒ 离线盘 |
  | `iter` | `online` | 一整轮上云，仍是"在线盘的活" |
  | `ppo` | `online` | 逐轮 |
  | `bc` | `online` | BC job 由云机跑 `train/bc.py`（不是"整段自主"） |

  `MANIFEST_KINDS` 与 `KIND_ROLES` 共用一份 kind 全集；`tests/test_role_routing.py::test_kind_role_map_covers_every_manifest_kind`
  穷举钉住 —— 加第 5 个 kind 而忘了给归属 ⇒ 当场红（否则它会静默回落 online，
  表现只是"这份活一直在队列里等人"）。
- **为什么不用"课程当前 mode"**：mode 是易变的（hub 内存表、控制台可热切）
  ⇒ 用它就等于把 F3 的缺陷搬进 manifest。
- **写在哪**：`publish_job`（`remote/hub_client.py`）——**唯一**落点，`job_id` 注入之后
  （归属不是身份成分：重发同一轮不会因为归属变化而变成两个 job）。`bc_loop.py` 也走
  `publish_job(kind="bc")` ⇒ 无需第二个发布点。
  显式 `role=` 可**覆盖** kind 推导（离线盘手工排活 / 将来加 kind 的逃生口）；非法覆盖值
  回落 kind 快照（不把垃圾写进字段）。
- **兼容旧 job**（无 `role`）：`role_of(manifest)` 兜底 `run→offline`、其余 `→online`，
  **不拒单**（旧 job 不该因为没字段而变孤儿）。字段**可选但一旦存在必须合法**
  （`normalize_manifest`）：拼错的 role 静默变 online 正是那种"看不见"的失败。
  ⚠ **不**把 `role` 放进 `MANIFEST_OPTIONAL_DEFAULTS`（它会注入静态默认值 ⇒ 变成第二个事实源）。

### 2.2 认领：**唯一咽喉点** + **两道正交的闸**（I2）

> 原稿写"三处同源"，评审 A 指出**漏了整整一条腿**：`_JobStore.claim` 才是唯一咽喉，
> 而 push 腿（`push_dispatch` → `Hub.claim`）**不经过** `claim_job`。实施改为**闸下沉**。

- **闸住在 `_JobStore._claim_locked`**（租约写入的唯一临界区，B3 的入口唯一性契约）。
  上层任何认领面（`claim_next` / `peek`+`claim` / 按 id 直领 / push 派发）都天然同源。
- 判据函数只有一份：`_JobStore.role_blocked(job_id, role) -> "" | "parked" | "role"`

  | 闸 | 粒度 | 判据 | 作用 |
  |---|---|---|---|
  | 归属闸 | **job** | `role_of(manifest) != 请求方 role` | 整段 job 只给离线盘（事故本体） |
  | 停摆闸 | **课程** | `parked（mode=offline）且请求方 role != offline` | 离线课不向在线盘派发（2026-09-20 的既有语义，**保留**） |

  两道闸**正交**：同一个函数两个方向都用它——派发面（`claim_next` / `peek_jobs` / push）
  **过滤候选**，临界区**拒绝**。⇒ 不会出现"peek 说能领、claim 说不能"这类两套尺子。
  停摆位由 `_HubQueue._sync_parked`（`set_mode` 热切 + 构造两条路）推到 store；唯一知道 mode 的层是 hub。
- **请求方 role**：来自请求头（见 2.3）。缺头 ⇒ `online`（旧 worker 行为逐字不变）。
  **跨角色领不到**；旧口径"带标 worker 仍可领在线课"**已作废**（一个盘一种任务，用户 2026-09-25 裁决）
  —— 这是**行为变更**，见 `DECISIONS.md §2026-09-25-goalnn-role-routing`。
- **枚举式回归防线**：`tests/test_role_routing.py::test_claim_paths_are_enumerated_and_all_carry_role`
  把生产代码里所有进 `_JobStore` 的认领调用点（4 处）与"每处都传 `role=`"钉成表
  ⇒ 出现第 5 条绕过路径（新腿、新调用点）当场红。
- **观测面**：`/admin/queue` 每行加 `roles: {jid: role}`（"谁在等谁"可见）。
  ⚠ **不**报 `claimable` 布尔——"可不可领"是**相对请求方角色**的属性，观察者不带角色，
  任何布尔都会误导。

### 2.3 worker 角色上报 —— **复用既有载体，不新增第 4 个模式载体**（评审 D 修订）

原稿要新增 `--role` + `X-Battle-Role` + `CFG.role`，而现成链路已经存在：

```
CFG["offline_worker"] → supervisor argv `--offline` → worker_loop(role=…) → _sched_headers
                     → `X-Battle-Offline: 1`（peek + claim 两跳）
```

实施的取舍：

- **头名与取值零改动**（`X-Battle-Offline: 1`）：混合部署里旧 hub/旧 worker 用同一份字面量，
  改名只会让带标 worker 在旧 hub 上**静默掉线**；而"归属判断是否正确"与名字无关。
  语义从**能力**（"我能自己跑完整段"）升级为**归属**（"本会话属于哪块盘"），
  名字保持 —— 这条由 `tests/test_offline_task_pack.py::test_role_header_name_is_shared_with_workers` 守住（防顺手改名）。
- **CLI 名不变**（`--offline`）：`notebook_runtime.restart_argv` / `offline_boot` 的既有链路照旧，
  只把解析结果从 `offline_ok=True` 换成 `role="offline"`。
- **claim 也必须带头**（F6 的真正修法）：`worker._sched_headers` 现在同时服务 peek 与 claim。
  闸下沉之后，"只在 peek 上带头"= 带标 worker **自锁**（peek 绿、claim 红）——
  `tests/test_worker_offline_cap.py::test_claim_sends_role_header_too` 守着这一跳，
  `acquire_job` 内部两跳（peek → claim）都透传 `role`。
- **能力闸 → 归属闸**的依据：`kind=run` 整段**确实**由 tailscale 盘跑得动（审计 §3：它有能力）——
  事故正是"有能力的盘接走了不属于它的整段 job"。能力拦不住"接错盘"，
  所以判据必须是角色（**capability → policy**）。这条 supersede 了 `DECISIONS.md:1634` ③
  与三处同义 docstring（`protocol.py` / `worker.py` / `notebook_runtime.py`，均已在实施中改写）。

### 2.4 offline 取包端点补 mode 闸（I5）—— ✅ 已实施

- `_get_task_pack`：在"包存在"之后、新鲜度门之前，
  `course in hub.courses() and mode_of(course) != OFFLINE` ⇒ **409** + 文案
  「该课现在是 online —— 离线课请先在控制台切离线（切换会自动导出任务包）」。
- **为什么是 409 不是 404**：包**确实存在**（F9），404 会把人引向"去导出"，而正确动作是"切模式"。
- **只在"表里有它且明确 online"时拦**：冷课/未扫到的课必须照旧放行 —— 课程表是"1 小时新鲜度
  扫描"的产物，而离线课本机不训练 ⇒ 从表里掉出去是常态，拿"不在表里"当 online 会把正常取包锁死
  （与 `_task_pack_miss_candidate` ① 同一条规则）。
- 用例：`tests/test_offline_task_pack.py::test_task_pack_online_course_is_409_even_when_the_pack_lives_on_disk`
  + `test_task_pack_cold_course_still_served`；跨进程那条在 `e2e/test_offline_training_e2e.py`。

### 2.5 配置短路修复（I3）—— ✅ 已实施

- `rl/cli.py`：`--rollout-src` 的默认从 `_d("rollout_src", "auto")` 改为**字面量 `"auto"`**。
  旧写法把 rl-config 顶层的 `rl.rollout_src` 读成 argparse 默认值 ⇒ `_rollout_source` 第一行
  "非 auto 就早返回"直接命中 ⇒ **课程级 `courses.<课>.rollout_src` 被整个忽略**
  （控制台写着 run、实际跑 local，而两者的日志形状一样）。
- 顶层 `rl.rollout_src` **仍在** `_rollout_source` 的兜底链里（课程级为空 ⇒ 照旧生效）
  ⇒ 顶层配置语义一字未变。用例：`tests/test_run_segment.py::test_cli_default_rollout_src_never_shadows_the_course_level_key`。

---

## 3. 分阶段（每阶段独立可交付、可测、可回退）

| 阶段 | 内容 | 文件 | 状态 |
|---|---|---|---|
| **P1（核心）** | §2.1 role 进 manifest + §2.2 咽喉点两道闸 + §2.3 角色上报（复用载体） | `protocol.py` / `hub_client.py` / `hub_server.py` / `worker.py` / `push_dispatch.py` / `notebook_runtime.py` | ✅ 已实施 |
| **P2** | §2.4 取包端点补 mode 闸 | `hub_server.py` | ✅ 已实施 |
| **P3** | §2.5 配置短路 | `rl/cli.py` | ✅ 已实施 |
| **P4** | I6 第三块盘：`battle.cloudflared.ipynb` 装 bun + 接"每次会话刷新"（或明确退役它） | 该 ipynb | ⬜ 未做（与本 plan 正交） |

**实施顺序（已按此落）**：`protocol.py` 常量+映射 → `hub_client.py` 写字段 → `hub_server.py`
闸下沉 + 三个面透传 role → `worker.py` 两跳带头 → push 腿同源 → notebook 文案同步。

---

## 4. e2e

### 4.1 装置（复用，别另造）

| 装置 | 位置 | 用途 |
|---|---|---|
| `_Hub` / `_HubQueue` + `make_server` | `e2e/test_offline_training_e2e.py` / `remote/hub_server.py` | 真 hub 进程（`--discover`）+ `ready()/set_mode()/lines/close()` |
| `hub_poll` | `tests/helpers/hub_poll.py` | 旧 `/jobs/next` 同形替代（peek + claim，**peek 与 claim 都带角色头**） |
| `_hub` / `_publish` / `_workers_with` / `_pump` | `tests/test_hub_push_dispatch.py` | push 腿派发装置 |
| `spawn_bound_port` | `tests/subproc_util.py` | 起进程 + 等端口（端口竞态由它消化） |

**纪律（抄同文件头部声明）**：不 spawn bun/node、不加载 torch、HTTP 全在本机临时端口。

### 4.2 用例 → 落点对照（T1–T6）

| 原计划用例 | 落点（实施后） |
|---|---|
| T1 `role=offline` 的 job 在线盘领不到 | `e2e/test_offline_training_e2e.py::test_offline_segment_is_claimable_only_by_a_marked_worker`（跨进程）+ `tests/test_role_routing.py`（进程内 HTTP/直调） |
| T2 归属在 N 次模式切换后**逐次**不变 | `tests/test_multi_course_hub.py`（5 次切换逐次断言 `job_role` 恒为 offline、在线盘逐次领不到） |
| T3 按 id 直领也受闸（钉 F5） | `tests/test_priority_schedule.py::test_peek_carries_halt_and_role_gate` + `tests/test_role_routing.py::test_store_gate_rejects_role_mismatch_without_touching_the_lease` |
| T4 旧 job（无 role）不孤儿 | `tests/test_role_routing.py::test_role_of_reads_field_then_kind` + `test_claim_next_partitions_courses_by_job_role` |
| T5 在线 job 离线盘领不到（★行为变更） | `tests/test_role_routing.py`（`role_blocked` 两个方向）+ `test_priority_schedule` 的归属闸断言 |
| T6 取包端点的 mode 闸 409→200 | `tests/test_offline_task_pack.py`（进程内）+ `e2e/test_offline_training_e2e.py`（跨进程） |
| **pull 腿**（§4.4 必查项） | 既有 pull 腿 e2e 全绿（`e2e/test_multi_course_single_hub_e2e.py` / `test_offline_training_e2e.py` 都是真 worker 拉活形态）+ 新增 `test_push_leg_never_pushes_an_offline_role_job`（push 腿对照组同用例内） |

**新增的枚举式防线**（评审 A 要求）：`tests/test_role_routing.py::test_claim_paths_are_enumerated_and_all_carry_role`
—— 认领路径**只有** 4 条（`claim_next` / `Hub.claim` / `Hub.claim_job` / push `hub.claim`），
且每条都把 `role` 送进临界区。

### 4.3 既有用例的语义改动（**必须点名**）

原稿预测 `e2e/test_multi_course_single_hub_e2e.py::test_offline_course_is_parked_and_resumes_on_going_online`
的旧断言会失效（"切回在线后同一份 job 只对离线盘可领"）。**实施后它仍然成立**，因为停摆闸被
**保留为课程级第二道闸**（见 §2.2 表）：离线课的活对在线盘隐身（`pending_n == 1` 不变），
切回在线后照旧可领。⇒ 该用例**只改文件头说明**，断言不动。
行为变更的落点是 `e2e/test_offline_training_e2e.py::test_offline_segment_is_claimable_only_by_a_marked_worker`
（旧口径"带标仍可领在线课"的断言已删除）。

---

## 5. DoD

- [x] 判据一：派发/认领路径上不再有 `mode_of(course)` 当**归属**判据；剩下的 `mode_of`
      只有在 `/admin/courses`、`/offline/tasks`、`active_courses()`（活跃度近似）、停摆同步（`_sync_parked`）、
      观测面 —— 逐处都在代码注释里写明了理由。
- [x] T1–T6 全绿（落点见 §4.2）；T2 的 N 次切换**逐次**断言。
- [x] pull 腿覆盖（既有 e2e 全绿）+ push 腿对照组用例。
- [x] 旧 job（无 `role`）不孤儿（§4.2 T4）。
- [x] `nn-python-gate` 绿（ruff + mypy + pytest 全量 2474 条）。
- [x] `docs/nn/remote-transport.md` §47 写清 role 语义、咽喉点两道闸、"带标不再兼领在线课"的行为变更。
- [x] `DECISIONS.md` 一条（§2026-09-25-goalnn-role-routing；含被否决备选：独立 `X-Battle-Role` 头、`NN_ROLE_ROUTING` 回退开关）。
- [x] 三处错误的「交替跑（其实交叠）」式旧注释/文案清理（worker / protocol / notebook_runtime，且已 supersede §1634 ③）。

---

## 6. 边界（不做）

- **不改** `stopCourse` 的"队列不动"契约 —— 撤单是姊妹 plan 的事。
- **不**在 worker 侧强杀在飞 job（取消点仍是轮边界）。
- **不**把两条链合并（offline 盘保持"取包自主跑"）。
- **不**动 `run_iters` 的既有语义（`rollout_src` 只改 §2.5 那一条默认值）。
- **不**新增第 4 个"模式载体"（复用 `--offline` / `X-Battle-Offline`；`CFG.offline_worker` 链路不动）。
- **不**做回退开关（`NN_ROLE_ROUTING=0`）：它会把"一个盘一种任务"变成**可选**，正好在最需要它的混部期
  复现事故；回滚面已在笔记本/CLI（`offline_worker` 开关）+ 本提交（hub/worker 同批），见 §8-K。

---

## 7. 待裁决（仍未裁决，**阻塞的不是 P1 而是它的收尾**）

**`kind=run` 的 job 在 P1 之后归谁？**

P1 之后 `role=offline` 的 job 只给 `role=offline` 的 worker。若没有任何 worker 声明离线角色，
这类 job 无人接，本机训练侧会白等（`rl/loop_steps.py RUN_WAIT_DEFAULT_SEC=8h`）。

三个取向（**用户未裁决，agent 落地到这里必须停下来问**）：

1. **源头砍掉（推荐）**：`setCourseMode('offline')` 不再写 `rollout_src=run` / `run_iters`
   ⇒ 离线课整体交给取包链；`kind=run` 逐步自然消亡。**代价**：切离线后本机不再发活。
2. **保留但只给离线 worker**：需要一个真实 `role=offline` worker（等于让某个盘接 hub job）。
3. **拒单 + 熔断收尾**：job 烂在队列、3 次熔断冻结，本机等超时收兵（训练停摆至人工处理）。

**P1 已落"归属"这一半**（对 `kind=ppo`/`iter` 立即生效、无副作用），但**本条仍阻塞 P1 的收尾**——
不许当成做完了。撤单腿（姊妹 plan）与它是同一件事的两半。

---

## 8. 实施修订表（评审 A–K → 处置）

| # | 评审发现 | 处置 |
|---|---|---|
| A | 认领点不是"三处"，**push 腿绕过 `claim_job`** | **闸下沉到 `_JobStore._claim_locked`**（唯一咽喉）；push 腿只保留"不值当推"的一跳过滤 + `role=ROLE_ONLINE`；加**枚举式**用例钉死 4 条路径 |
| B | 语义翻转了**已记录决策**（`DECISIONS.md:1634` ③）却没点名 | 新增 DECISIONS 条目显式 supersede；三处同义 docstring（`protocol`/`worker`/`notebook_runtime`）同步改写并说明"能力 ≠ 归属：能力拦不住接错盘" |
| C | `kind` 有四个，原稿只认三个 | 映射表补齐 `bc ⇒ online`；`MANIFEST_KINDS`/`KIND_ROLES` 共用一份全集 + 穷举用例；发布点确认 `bc_loop.py` 走 `publish_job`（无需第二个写点） |
| D | 新增 `--role`/`X-Battle-Role`/`CFG.role` = 第 4 个模式载体，与 §6 自相矛盾 | 全部删掉：复用 `--offline` + `X-Battle-Offline`（字面量不变，仅语义升级） |
| E | 头名不改的理由没写透 | 写进 `protocol.ROLE_HEADER` 注释 + `test_role_header_name_is_shared_with_workers`（守"不许改名"） |
| F | F6 只说"不读"，实际是**从来不发**；且 claim 不带头会自锁 | `_sched_headers` 服务 peek + claim；`acquire_job` 两跳透传；用例 `test_claim_sends_role_header_too` + 预取线程 kwargs 断言 |
| G | 停摆（离线课停车）语义不能被归属闸吞掉 | **保留为课程级第二道闸**（`_JobStore.parked`，由 `_HubQueue._sync_parked` 同步），与归属闸共用 `role_blocked` 一份判据 |
| H | `role` 进 `MANIFEST_OPTIONAL_DEFAULTS` 会造第二个事实源 | **不进**：字段可选、`role_of` 兜底、`normalize_manifest` 校验合法性 |
| I | 缓存/失效没定 | `_JobStore._roles` + 独立 `_role_lock`；**`publish` 覆盖 manifest 时 pop**（不靠"role 永不变"的假设）+ 用例 `test_job_role_cache_is_invalidated_by_republish` |
| J | `/admin/queue` 报 `claimable` 布尔会误导 | 改报 `roles: {jid: role}`（相对角色的属性不给布尔） |
| K | 回退开关 `NN_ROLE_ROUTING=0` | **否决**（理由见 §6 末条 + DECISIONS）；回滚面 = 笔记本开关 + 本提交（hub/worker 同批） |

**附带发现并修掉的问题**（与主设计同源）：
- `docs/tpu-perf` 无关；`rl/cli.py` 的 `--rollout-src` 短路（§2.5）属于 I3；
- `hub_server.py` 的"离线课整段交领"日志判据原读 `mode_of(course)` ⇒ 热切后会撒谎，改为读 `job_role(jid)`；
- `active_courses()` 的 `mode_of` 读法保留，但补写"它只是活跃度近似、不参与派发判断"的口径注释。
