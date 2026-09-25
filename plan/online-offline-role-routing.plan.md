# Plan: online-offline-role-routing — 让「该由哪个盘执行」成为 job 的不变式

> **交付物**：本 plan + 代码（2026-09-25 已实施，见 §8 实施修订表）。
> **状态**：**已实施**（P1/P2/P3 落地；**P5 = §7 的收尾：已实施**（2026-09-25）；P4 **重裁**（不退役，见 §9，实现待做）。
> 评审吸收记录见 §8；P5 的记录 = `DECISIONS §2026-09-25-goalnn-offline-leg-retired` + `docs/nn/remote-transport.md` §48。
> P4 的误判与撤回见 §9.0。
> **背景**：`reports/online-offline-hot-switch-audit-2026-09-25.md`（六条不足 L1–L6 / 六条不变式 I1–I6）。
> **姊妹 plan**：`plan/switch-mode-drops-jobs.plan.md`（管"切换即撤单"）。本 plan 管"归属"。
> **两者关系**：必须同批或紧接落地 —— 只做归属会出现"旧 job 一直不可领"，只做撤单会出现"撤了单但仍派给错盘"。
> **阅读顺序**：§1 事实链 → §2 设计 → §3 分阶段 → §4 e2e → §5 DoD → §6 边界 → §7 裁决（`kind=run` 归谁）→ §8 实施修订 → §9 P4 重裁（两块盘都留、只去 bun 依赖；含误判留档）。
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
| **P4** | I6 第三块盘：`battle.cloudflared.ipynb` —— **不退役**（它走 cloudflared 公网隧道，与 tailscale 盘是两条不同的接入方式）；要处理的是**两块盘的 bun 依赖**（见 §9，取向待裁） | 待定（§9.3） | ⬜ 未做 —— 初版的「退役」已于当日撤回 |
| **P5** | **§7 的收尾**：`kind=run` 的队列项退役（砍在发布点）+ 无消费者时的响亮拒 + 离线盘报名与读数 | `rl/loop_steps.py` / `rl/loop_round_steps.py` / `rl/loop_round.py` / `rl/loop_core.py` / `rl/loop_runner.py` / `rl/cli.py` / `remote/worker.py` / `remote/run_loop.py` / `remote/offline_boot.py` / `remote/hub_server.py` | ✅ 已实施 |

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

## 7. 裁决：`kind=run` 的 job 归谁 —— **已裁决（2026-09-25，用户委托 agent 裁决）**

**结论：取向 1（源头砍掉）为主 + 取向 3 的机制作尾巴（熔断收尾）；取向 2 否决。**
**口径（用户 2026-09-25）：离线场景里没有「一整段」这个中间概念。**

### 7.0 离线场景的口径（先定死，别拿代码里的残留当需求）

**云机接手一门课 ⇒ 就一直跑**，直到三种收尾之一：① 跑完整个课程；② 云机配额/预算用尽；
③ 人工停机/停课（云机收到指示，轮边界收尾）。**能传回来多少是多少** —— 已跑完的轮尽力补传，
不追求完整、不阻塞在飞的那一轮。

⇒ 所以「一整段 job」不是离线场景的载体，它只是 `kind=run` 这条**残留路径**的形状（本机发一份带段长的
队列项、随后等 8h）。离线场景的载体是**任务包**（`battle.offline.ipynb` 取包接手一直跑）。
（用户 2026-09-25 原话：「offline notebook 接过去就一直跑，直到跑完整个课程，或者云机配额用尽，
或者人工停机/停课，能传回来多少是多少」——所以「段长」这个词在离线场景里没有位置。）

### 7.0.1 本裁决**不许碰**的两条既有能力（用户 2026-09-25 点名保留）

1. **离线云机串行跑多门课**：`offline_boot.run` 的 `/offline/tasks` 清单 + `queue_mode=drain`（缺省：跑完一批
   继续驻守）+ claim/heartbeat/release 租约 + `session_budget_sec`/`idle_wait_sec`；**空队列 = 正常收工 rc=0**。
   ⇒ P5 一个字节都不动 `/offline/*`。
2. **notebook 里不写课程名**：`requested_courses` 空 ⇒ 走清单发现（只对没有清单端点的老 hub 降级回「必须填 course」）。
   ⇒ P5 的「离线盘报名」只能是**这块盘的身份**（离线 notebook 天生就是离线盘，自报即可），
   **不许**做成「每门课一个开关」，也不许因此要求 ipynb 填课程名。

起因：P1 之后 `role=offline` 的 job 只给 `role=offline` 的 worker，若没有任何 worker 声明离线角色，
这类 job 无人接，本机训练侧白等（`rl/loop_steps.py RUN_WAIT_DEFAULT_SEC=8h`）。

### 7.1 依据（R1–R4 均已在代码里核实）

| # | 事实 | 出处 |
|---|---|---|
| R1 | 队列里的 `kind=run`（残留路径）**只有一个生产发布者**：离线档本机循环的那次派发（`register=True`，本机随后等 8h） | `rl/loop_steps.py _remote_run_segment` → `_remote_ppo(plan_bytes=…, register=export_path is None)` |
| R2 | `--export-bundle`（取包链的入口；控制台「切离线」自动跑的就是它）**也**造 `kind=run` 的 manifest，但 `register=False`（只建 job 目录当打包源，**不进待领池**、不记账本） | `rl/loop_steps.py`（`register=export_path is None` + 导出分支）· `dashboard/src/server/bundles/export.ts taskBundleArgs` |
| R3 | **没有任何盘是离线角色**：`CFG["offline_worker"]` 全仓只有测试设过；`offline_boot`（取包链）全文碰不到 `/jobs`，它自报身份只有 `Authorization` | 审计 §4-L3 · `remote/offline_boot.py` |
| R4 | 因此 P1 之后 `role=offline` 的**队列项无人能领**（旧 job 按 `kind` 兜底也是 offline），本机在每个段上白等 8h | `protocol.KIND_ROLES`（`run ⇒ offline`）· `hub_server._JobStore.role_blocked` |

⇒ **取向 2（保留但只给离线 worker）** 要成立，必须**新造并部署**一个消费者；而它的功能已被
**取包链**完整覆盖且更完整（`battle.offline.ipynb`：`/offline/tasks` 清单 + 租约 + 取包 + 产物回传 +
it0 基线 + 本机产物优先；四份 plan 在建/已实施）⇒ 保留它 = **一个任务两个执行者**，
正是本次事故的结构（审计 §2：两条链结构上分离，问题在闸门语义）。
⇒ **取向 3 单独用**（只熔断）＝ 每次切离线都先白等一轮，且不解决「两个执行者」的结构问题。

### 7.2 实施形状（P5：砍在**发布点**，不是配置字段）

1. **配置不动**：`setCourseMode('offline')` 继续写 `rollout_src=run` + `run_iters` —— 它们是「这门课由云机
   接手」的既有声明（`run_iters=-1` = 跑到课程末，与 §7.0 的离线场景同义），且 `--export-bundle` 的终点口径
   与它同源。**不**按本节的原始稿（取向 1 的字面写法）
   在这里删字段：`resolve_collect_mode(seg=0, source≠node)` 落到 `COLLECT_LOCAL` ⇒ **本机偷偷退回自己
   采样训练**，与云机取包链**双跑**（这正是原稿那句「代价：本机不再发活」没写出来的坑）。
2. **本机循环遇 `run` 不再派发**：不发布队列项、不采样、不进账本、不等 `run_wait_sec`；
   收工形状复用既有 `ROUND_BUNDLE_EXIT` 的「干净收官」（`loop_runner` 置 `finished=True` +
   一行 `finish_reason`），日志**指路取包链**（该用哪个 ipynb）。§6 的「不碰 `stopCourse`」不变。
   该课的权重/读数由**产物导入**（取包链的 `POST /offline/artifact` → 控制台导入）推进本机账本。
3. **尾巴 = 熔断收尾（取向 3 的机制，不是它的取向）**：任何仍存在的 offline-role 队列项
   （盘上遗留 / 手写 `--run-iters` / 混部期旧 hub）**不许白等 8h**：hub 侧补一条
   **「本环境有没有离线 worker」**的读数（`/admin/queue` 已报 `roles`，worker 侧一并报 role），
   训练侧发布前探一次 —— 无消费者 ⇒ **立即响亮拒跑 + 一行指路**（探不到读数才落到既有的有界等待）。
4. **取包链零改动**：R2 那条路径（`register=False`）逐字节不动；`/offline/*` 与任务包格式不变，
   `--export-bundle` 仍是「只读快照、不推进账本」。

### 7.3 与姊妹 plan 的接口

- 撤单腿（`plan/switch-mode-drops-jobs.plan.md`）在 **run 腿上结构性变成不可达**（没有 job 可撤）——
  但**仍然要落**：切在线→离线时在飞的 **`iter`/`ppo`** job 仍靠它撤。
- 两者因此**不再硬耦合**（本 plan 开头的「必须同批或紧接」对本条收窄为「`iter`/`ppo` 腿上同批」）：
  先做哪条都不会留下「旧 job 不可领」或「撤了单但派给错盘」的半状态。

### 7.4 DoD（P5 落地时逐条判）

- [x] `/admin/queue` 上不再出现 offline-role 的**待领**项 —— 生产端不再发它
      （枚举式：`tests/test_offline_leg_retired.py::test_run_manifest_is_published_from_exactly_one_place_with_export_path`；
      残留项另有 `offline_disk.stale_jobs` 点名）。
- [x] 离线课本机循环：一行指路 + 不采样 + 不派发 + 不进账本 + 不等待
      （`test_offline_course_stops_the_round_cleanly_with_a_pointing_line` × 3 档 `run_iters`
      + `test_offline_course_never_reaches_the_collect_step` + `resolve_collect_mode` 的
      「绝不回落 `COLLECT_LOCAL`」断言）。
- [x] 无消费者时**响亮拒**且**有界**（不是 8h）：发布咽喉点 `SystemExit`（
      `test_publish_choke_point_refuses_a_run_queue_job_loudly`，消息含 `battle.offline.ipynb`
      与 `--export-bundle`）+ worker 侧零下载拒收（`test_worker_refuses_a_run_kind_job_before_any_download`）
      + `RUN_WAIT_DEFAULT_SEC`/`_run_wait_sec`/`--run-wait-sec`/`rl.run_wait_sec` 全部退役
      （`test_production_code_has_no_trace_of_the_retired_leg`）。
- [x] `--export-bundle` / `/offline/*` 逐字节不变（取包链 e2e 全绿；`e2e/test_offline_training_e2e.py`
      的两条段用例改成钉「**遗留**离线项仍按归属派发 + 补传读面」）；§7.0 的三种收尾与「能传回来多少
      是多少」不被本裁决改动。
- [x] `nn-python-gate` 绿（2484 passed，含 ruff + mypy + tests/+e2e）+ dashboard typecheck/1158 测试绿
      + 一条 `DECISIONS`（含被否决项：取向 2 / 在配置里删字段 / 只熔断 / 删 `run_plan_job` / 加回退开关）。

### 7.5 取向 1 / 2 / 3 的对照（留档，免得再问一次）

| 取向 | 本裁决 | 理由 |
|---|---|---|
| 1 源头砍掉 | ✅ **采用（砍在发布点）** | R1/R2：队列项只有一个发布者，且取包链是它的完整替代 |
| 2 只给离线盘 | ❌ 否决 | R3：消费者**不存在**，要成立就得新造并部署一个（= 两套执行者） |
| 3 熔断收尾 | ⚠️ **只作尾巴** | 单独用＝每次切离线先白等一轮，且留着「两个执行者」的结构 |

### 7.6 残留清单：仓里还有哪些地方当成「离线 = 发一份队列 job」（2026-09-25 排查）

> 判据：凡把 `rollout_src=run` / `run_iters` / `kind=run` 理解成「本机发一份 job 到队列、等人领」的地方。
> 「保留」= P5 不动它（或只改措辞）；「退役」= P5 要拆掉的；「改词」= 行为不动、文案要跟上。

**一、真会发出队列项的（退对象）**

| 位置 | 现在的假设 | 归属 |
|---|---|---|
| `rl/loop_steps.py _remote_run_segment` | 本机发布一份 `kind=run` 队列项，随后等 `run_wait_sec` | **退役**（改为不派发 / 不采样 / 不进账本 / 不等） |
| `rl/loop_round_steps.py` 的 `COLLECT_SEGMENT` 分支 | 采集模式分流里有一条「整段」腿 | **退役**；同文件的 `--export-bundle` 早退**保留**（取包链入口） |
| `rl/loop_round.py resolve_collect_mode` / `COLLECT_SEGMENT` / `ctx.seg` | 段长决定「本机不采样」 | **半退役**：字段留（`--export-bundle` 的到哪停），`COLLECT_SEGMENT` 分支退役 |
| `rl/loop_steps.py RUN_WAIT_DEFAULT_SEC` / `_run_wait_sec` / `--run-wait-sec` / `rl.run_wait_sec` | 等一个没人领的 job，硬顶 8h | **退役**（尾巴改成有界 + 响亮拒跑） |
| `remote/worker.py`「半离线尾巴」（`kind == "run"` → `run_plan_job`） | worker 领到 run job 后把剩余轮次自己跑完 | **退役**（队列腿的消费端；取包链走 `run_loop.main --bundle`，与它无关） |
| `remote/run_loop.py run_plan_job` | 同上 | **退役**（随发布端一起） |
| `remote/hub_client.py publish_job` 的 `kind='run'` 必填校验 | `kind=run` 必须带 `plan_bytes` + `rollout_spec` | **保留**（`--export-bundle` 仍造这种 manifest），但注释要写明「它的消费者是取包链，不是队列」 |

**二、配置/语义层（保留，改措辞）**

| 位置 | 现在的说法 | 归属 |
|---|---|---|
| `dashboard/src/stack/specs.ts trainModeKnobs` | `offline ⇒ rollout_src=run + run_iters=-1`「整段上云」 | **保留**（= 「这门课由云机接手」的声明）；措辞改「整段/段长」→「由云机接手 / 到课程末」 |
| `dashboard/src/server/actions/train-mode.ts` | 写配置 + 回执「整段上云」 | **保留写入**；回执改词（「本机不再采样」仍然对） |
| `dashboard/src/server/actions/course-mode.ts` | 切离线回执「整段上云（云机自己跑 rollout）」 + 「一段 job 覆盖到课程末，trainer 阻塞在段等待里」 | **保留**（这颗开关就是把手交给取包链）；**两句文案 P5 后都不再成立**，要改 |
| `dashboard/src/server/actions/course-lifecycle.ts` 停课回执 | 「带标 worker 仍可领已入队的整段 job」 | **改词**（P5 后这句话是假的） |
| `dashboard/src/server/actions/course-lifecycle.ts` 开课预校验 | 「整段上云要求课程声明 iters>0」 | **判据保留**（包同样要有终点）、措辞改 |
| `dashboard/src/server/api/state-view.ts` | 「离线课的 PPO job 不经 hub 队列认领」→ 关掉排队暂停红条 | **判据保留**；这条注释在 P5 之前**与代码不符**（本机循环确实会发一份队列项），P5 之后才为真 → **P5 的验收点之一** |
| `dashboard/src/server/exit-watchdog.ts` | 认得 `--run-iters<0 …` 那条 SystemExit | **随 P5**：`--run-iters` 只剩导出用途就删掉这条匹配 |
| `dashboard/src/server/bundles/export.ts` | `--run-iters -1` + `--export-bundle` | **保留**（取包链的入口，就是「云机接手」的交付物） |
| `dashboard/src/core/types.ts` / `api/route.ts` 的域校验 / `OpenCourseModal.tsx` | `run` 是合法 rollout 源；离线档忽略 rollout 选择 | **保留**，注释改词 |
| `dashboard/src/server/iters.ts`（事件记 `rollout_src`） | 观测字段 | **保留** |

**三、文档与测试（随 P5 改）**

| 位置 | 归属 |
|---|---|
| `docs/nn/remote-transport.md` / `console.md` / `training-stack.md` / `runtime-opt.md` 的「半离线/整段」段 | 改词：离线 = 取包接手；队列 run 项退役（P5 落一条进度记录） |
| `tests/test_run_segment.py`（8 处）/ `tests/test_loop_round.py` / `tests/test_serve_wiring.py` | 跟 P5 一起改：整段腿的主用例退役或改成「不派发」的断言 |
| `tests/test_role_routing.py`（`run ⇒ offline` 映射） | **保留**（形状仍在；旧 job 兜底仍需要） |
| `e2e/test_offline_training_e2e.py`（取包链）/ `e2e/test_loop_supervisor_integration.py` | 前者**保留**（要加一条：本机对离线课不发队列项）；后者按 P5 调整 |
| `dashboard/tests/{train-mode-offline,course-mode,course-lifecycle,exit-watchdog,server-api-state-view}.test.ts` | 按上表逐条改断言/文案 |

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

---

## 9. P4 —— **重裁：不退役**；要处理的是**两块盘的 bun 依赖**（2026-09-25）

> ⚠️ **本节初版（2026-09-25 16:39）判的是「`battle.cloudflared.ipynb` 退役成指路牌」，那个裁决是错的**
> —— 已实施并提交（`da3c8a68`）后**当日全量撤回**：该 notebook 已恢复原状（cell / docstring / 守卫用例
> 全撤），本节初版内容仅作**误判留档**（§9.1–§9.4 标了「初版」的就是它）。

### 9.0 为什么初版是错的（根因 —— 这条必须记住）

**两块 notebook 不是同一件事的两个副本，而是两条不同的隧道**：`battle.tailscale.ipynb` 走 **tailnet**、
`battle.cloudflared.ipynb` 走 **cloudflared 公网隧道**，服务**不同的云机网络环境**
（进不了 tailnet 的机器只能走公网隧道）。所以它不是「多余的第二份实现」，而是**唯一的那条接入方式**，
「退役」等于砍掉一整类云机。

初版错在把「**能力归属**」（谁跑 rollout）当成「**接入方式**」（怎么连到 hub）来判：能力重叠 ≠ 入口冗余。
两条旁证被我误读 —— ① `rollout.cloudflared.ipynb` 服务的是**采样节点**（另一件事，不是这块盘的替代）；
② `rl-config.nodes[]` 里的 CF URL 节点恰恰证明**公网隧道是活路**，却被我当成「已有人承担」。

### 9.1 重裁：真正的要求（用户口径，2026-09-25）

> 「两块盘分别用于建立**不同的网络隧道**和 hub 通信，用于**不同的云机网络环境**。
> 只是让你把**它们的 bun 依赖去掉**，不是把整个 notebook 退役。」

⇒ **两块盘都保留、两条隧道都保留**；P4 的范围只是**去掉它们对 bun 的依赖**。

### 9.2 现在的 bun 依赖长什么样（事实，尚未改）

| # | 事实 | 出处 |
|---|---|---|
| B1 | 节点侧 rollout（`kind=iter`）要**节点自己**有 bun 跑 TS：能力自检 `remote/iter_rollout.resolve_bun` 找不到就 **零下载拒单**（`REJECTED: 节点上找不到 'bun'`） | `remote/iter_rollout.py` · `remote/worker.py` |
| B2 | bun 的获取方式是**从公网现装**（`curl https://bun.sh/install \| bash`），且必须在改代理之前（§46：userspace tailscaled 的代理只转 Tailscale IP） | `remote/tailscale_boot.py::ensure_bun` |
| B3 | 只有 **tailscale 这条链**会装（`ensure()` 里调 `ensure_bun`）；`battle.cloudflared.ipynb` 的 cell 与 `notebook_boot` 都不装 ⇒ 它每单被拒（审计 §I6 说的就是这条） | 同上 · `ipynb/battle.cloudflared.ipynb` |
| B4 | `battle.offline.ipynb` 的 cell 里也有同一段安装（离线腿不受影响） | `ipynb/battle.offline.ipynb` |

### 9.3 待裁：去掉「盘上装 bun」之后，bun 从哪来？

把「盘自己 curl 公网装」去掉有四条路（实施前必须定，别猜）：

| 取向 | 含义 | 代价 / 风险 |
|---|---|---|
| **A 由 hub 侧下发** | bun 随 `ts_code` / `code.zip` 下发（内容寻址缓存已有，装包代价只付一次）；盘上零安装、零公网依赖 | payload 变大（bun 未压缩约 90MB）· 要按平台架构（x64/arm64）选包 |
| **B 盘上装，但不再依赖公网** | 保留「装」，来源改成 hub 供包；安装动作挪到与隧道无关的位置（两块盘都只调一次） | 仍是「在盘上装」，且要新增一条供包通道 |
| **C 发布可执行产物** | rollout 入口用 `bun build --compile` 出单文件二进制随包下发 ⇒ 盘上**根本不需要 bun** | 仓库目前**没有**这条流水线（新的构建+入库产物+签名）· 原生扩展（`conv_native.so`）要一起考虑 |
| 补装 bun（初版被否的那条） | 只在 cloudflared 线也接 `ensure_bun` | ⚠️ 与用户口径不符（那是「补装」，正是被否的方向） |

### 9.4 初版依据（**留档：R4 是错的**）

#### 初版 R1–R5（2026-09-25 16:39，已被 §9.0 推翻；R4 错在把 tailnet 当唯一接入）

| # | 事实 | 出处 |
|---|---|---|
| R1 | 它=「GPU worker 经 cloudflared 公网隧道接入」（push-first 无 hub / pull）。而 ① 节点侧 rollout/eval 的 cloudflared 通道现在由 `rollout.cloudflared.ipynb` 承担（bun + `sampler-agent`，且 `rl-config.nodes[]` 里已有 CF URL 节点，如 `gcs`）；② GPU PPO 算力由 `battle.tailscale.ipynb` 承担（push/pull 都支持） | `nn-training/rl-config.json` 的 `nodes[]`（含 `https://….trycloudflare.com`）· `ipynb/rollout.cloudflared.ipynb`（装 bun 那段） |
| R2 | **它现在什么都跑不了**：cell 与它的两条模块链（`notebook_runtime.run_notebook` pull / 内联 push bootstrap）都不装 bun，也都不经过 `tailscale_boot.ensure()`（§46 的 bun 修复只覆盖在线 tailscale 腿与 Colab 离线腿）⇒ 任何 `kind=iter` job 被 worker 的能力自检**零下载拒单**；它顶多能跑 `kind=ppo`（hub 采样本机 + 云机只算 PPO），而那条路 tailscale 盘已覆盖 | `remote/notebook_runtime.py` · `remote/push_bootstrap.py` · `remote/tailscale_boot.py::ensure_bun`（§46） |
| R3 | 它的 230 行 cell 是 `notebook_boot._push` 的**第二份实现**（HTTP 升级服务 + 起 cloudflared + 释放端口 + spawn 完整 worker）——两份必然漂；审计 §I6 的后半句「没被'每次会话刷新引导模块'覆盖」说的就是它：它**不拉远端引导模块**，仓库里的修复到不了它 | `ipynb/battle.cloudflared.ipynb` cell vs `remote/notebook_boot.py::_push` |
| R4 | **训练侧本来就在 tailnet 上**：push 的目的地址就是节点的 TS IP（`battle.tailscale.ipynb` 的 `rl_mode: push` 那行），用户真机日志（§46）也是这条 ⇒「训练机没有 tailnet」这个场景在系统里不存在 | `ipynb/battle.tailscale.ipynb` markdown 表 · `docs/nn/remote-transport.md` §46 |
| R5 | 它正是审计点名的**下一个同款坑**：也能起 worker、也会被 UI 派活、却静默拒单（与 `9d…`/`ed9d3514` 同类：能力与 UI 的承诺不一致） | `reports/online-offline-hot-switch-audit-2026-09-25.md` §I6 |

#### 初版实施形状（已全量撤回）

1. **`nn-training/ipynb/battle.cloudflared.ipynb` 变成零逻辑指路牌**：markdown 说清「已退役 + 为什么 +
   该用哪个」，code cell 打印同一份指路后 `SystemExit` ⇒ 审计 §I6 的两个洞（**不装 bun / 不被刷新覆盖**）
   **由构造消失**（没有逻辑可漂、没有被派活的可能）。
2. **两处点它名字的 docstring 改掉**：`remote/notebook_runtime.py` 的抬头（原来写「（`battle.cloudflared.ipynb` /
   `battle.tailscale.ipynb`）的运行时逻辑」）与 `remote/push_bootstrap.py` 的「Kaggle push-first：notebook
   内联一份压缩 bootstrap」（现在只有 tailscale 盘的内联回退用它）。
3. **能力清单成为唯一口径**（本表即「该用哪个 notebook」的答案）：

| 你要的 | 用哪个 |
|---|---|
| 在线 PPO 算力（hub 采样本机 + 云机算 PPO，`kind=ppo`） | `battle.tailscale.ipynb`（pull） |
| 节点侧 rollout/eval（`kind=iter`，需要 bun） | `battle.tailscale.ipynb`（pull/push；§46 起自装 bun）**或** `rollout.cloudflared.ipynb`（不需要 tailnet） |
| 云机自主跑整课（离线） | `battle.offline.ipynb`（取任务包；**不经 hub 队列**，P5 之后） |
| BC 蒸馏 | `battle-bc.ipynb` |

4. **守卫用例**（`nn-training/tests/test_notebook_retired.py`）：该 ipynb 里不得再出现任何 worker 引导标识
   （`notebook_runtime` / `push_bootstrap` / `worker_loop` / `run_pull_worker` / `run_push_worker` /
   cloudflared 二进制安装），且必须含 `SystemExit` 与三份指路 —— 防止有人「顺手把它改回可用」而没读这段裁决。

#### 初版 DoD（已作废）

- [x] 该 ipynb **零逻辑**（无 worker 引导标识），打开即指路（`SystemExit` + 该用哪个）。
- [x] `notebook_runtime.py` / `push_bootstrap.py` 不再点它的名字（§46 的历史注记不算）。
- [x] 能力清单与 `rl-config.nodes[]`（CF URL 节点）+ §46 的 bun 口径一致。
- [x] `nn-python-gate` 绿（含新守卫用例）+ 一条 `DECISIONS`（含被否决项）。

#### 初版取向对照（已作废）

| 取向 | 初版判定 | 实情 |
|---|---|---|
| 退役（指路牌） | ✅ 「采用」 | **错**：它服务的不是「同一条活的第二个执行者」，而是一条**独立的接入方式**（§9.0） |
| 装 bun + 接刷新 | ❌ 否决 | ⚠️ 也错在方向上：用户要的是**去掉 bun 依赖**，不是「装不装」 |
| 直接删文件 | ❌ 否决 | 结论对（不该删），但理由（「不产生指路」）建立在错的退役前提上 |
| 保留可用、只在文档标注退役 | ❌ 否决 | 同上：前提错 |

**与 §7 的关系（修正后）**：§7 治的是「**一个任务两个执行者**」（队列里的整段 vs 云机取包）——
那条裁决成立。§9 **不是**同一件事：两块盘是**两条隧道**，各自服务一类网络环境，可以共存。
初版把「§7 的形状」硬套到「接入方式」上，才是 P4 误判的最后一层原因。
