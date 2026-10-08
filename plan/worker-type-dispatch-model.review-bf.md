# worker-type-dispatch-model.review-bf.md — 《训练派发模型重构》v2 之后评审

> 评审人：Buffy · 2026-10-07 · 基准 HEAD `b6211dea`（goal-nn）· 方法：把 v2 的每一条判据 / 行号 /
> 消费链拿到工作树真代码上逐条对账（只读 grep + 静态追踪；未跑门禁、未改代码）。
> **范围**：不重复复验 v1→v2 已并入的 Q1–Q5 / P0–P2；只报**仍未闭合或本轮新发现**；v2 自报项抽查见 §4。
> **结论**：Q1（hold 推迟到「包到手」）与 Q5（`role_blocked` 分支顺序）方向成立，v2 的行号复核
> **大面积命中**（§4 逐条）；但仍有 **3 处 P0（F1–F3）** + **8 处 P1（F4–F11）** + 落点/口径小项（F12–F15）。
> 三条 P0 是**同一种形状**：v2 把语义搬到了「新代码」这一侧，可**造包、活性、打点这三条链各有一半
> 住在旧代码或包内代码里**——删符号删不掉它们，且失败时是静默的（新 hub 自洽、现场照旧）。
> 建议 F1–F3 并入 §5 P0 清单、F4–F11 折进对应行，**在 M1a 动工前定稿**。

| # | 级别 | 一句话 |
|---|---|---|
| F1 | P0 | 删 `course-mode.ts` 全模块，但 `pending_export` 仍靠它的 `/api/autoOfflineHandoff` 造包——而该 action 今天兼写 `rollout_src=run`（= Q1 花半页否掉的「停本机通道」） |
| F2 | P0 | 三个钟没收敛：`beat_at` + `OFFLINE_LEASE_STALE_SEC=180` 的去向**一字未提**；照旧则 §68 假活永不掉线，改判据则 180s 常量/环境变量无归宿 |
| F3 | P0 | 打点若落在 `remote/plan_run.py`，它来自**包里的 `code.zip`**（不是每会话刷新的 raw）⇒ 旧包会话没有打点，却被新 hub 按 900s 判掉线 |
| F4 | P1 | `TASK_STATE_RANK` 现值引错（漏 `not_offline`，`completed` 写成 4 而非 5）——P1-7 的卖点正是「保留现值序」 |
| F5 | P1 | dashboard 侧迁移面没普查：实测 **435 处 / 26+ 文件**（含 6 个测试 178 处），M4 只写「相关用例改词」 |
| F6 | P1 | D4/Q5 的 drain 互斥只有**客户端自律**（`role_blocked(job_id, role)` 无 worker 形参）⇒ 守卫 :225「形状不变」与 Q5 落点自相矛盾 |
| F7 | P1 | `not_offline` 的三重身份（state / 409 reason / authority 常量）在删 `AUTHORITY_*` 后**没有新判据** |
| F8 | P1 | `pending_export` 无过期/清理：崩溃的导包者会留下永久「有人在导包」，`offline_stalled` 锚点缺窗即假告警 |
| F9 | P1 | M0 读数缺一半：只读轮级 `wall_sec`，没读「相邻完成事件间隔」——PPO 段没有完成事件，轮级合格会掩盖打点缺口 |
| F10 | P1 | Q4「显式拒旧客户端」只落在 claim 一面；`CFG.course` 取包腿与 `CFG.task_zip` 腿不受管，且**刷 notebook ≠ 换 worker 代码** |
| F11 | P1 | `_busy_locked` 两条腿没逐条处置（腿② 「别课正在交接」正是 Q1 要废的占闸腿；两腿的 `authority_of` skip 随删） |
| F12 | P2 | 新端点落点缺三处：`common/protocol.py` 路径常量 · `hub/http_face.py` 路由 · 分域守卫（`DOMAINS/STATE_WRITERS/ALLOWED_IMPORTS`） |
| F13 | P2 | `loop-control.json` 被塞进「hub 事实」：版本/回执/同机时钟前提要写死（它今天的契约方向与 held 相反） |
| F14 | P2 | DoD#2 措辞：清单→claim 有 TOCTOU，「claimable=true ⇒ 必成功」只能在无并发下成立 |
| F15 | P2 | 小项：普查数字对齐 · `plan_run.py` 在 `remote/` · `authority_of` 不在 `queue_scope` · `advance_active` 现状 · 生产隔离守卫 |

---

## 1. P0 级

### F1（P0）删 `course-mode.ts` 全模块 ↔ `pending_export` 仍靠它的 `/api/autoOfflineHandoff` 造包；且该 action 今天**兼写 `rollout_src=run`**

**plan 说**：M4「删：`course-mode.ts` 全模块」·「『导出任务包』保留」；§1.3/§1.5.1 说 `pending_export` 期间
**本机照常跑**（并专门论证「不要为它另开停本机通道——那会破坏『接管唯一来源 = claim』」）；M1b 说缺包走
`pending_export` + `offline_stalled` 锚 `pending_export.at`（窗口 `AUTO_HANDOFF_PENDING_SEC`）。

**事实链**（全部坐实）：

1. **造包的一端在 dashboard，不在 hub**：hub 的缺包腿只发一个触发（`hub/task_pack.py:69`
   `AUTO_HANDOFF_CONSOLE_PATH="/api/autoOfflineHandoff"`，`:402-416` POST 出去，404/超时只记一行），
   **真正造包的是控制台**：`dashboard/src/server/api/route.ts:378` → `course-mode.ts:503
   autoOfflineHandoff(course)`。
2. 该 action **同时改生产配置**：`:514` `applyTrainModeToConfig(c, 'offline', { remember: true })`，
   文案自证 `:536`「已自动切离线（**rollout_src=run：本机不跑这门课**）」。⇒ 今天「有盘来 claim 缺包的课」
   这条链的**副作用就是停本机**，正是 Q1 用半页论证要拆掉的东西（「hold 的语义被透支」）。
3. 按 M4 字面删掉 `course-mode.ts` ⇒ 端点消失 ⇒ hub 触发 404 ⇒ **没有任何人再造包** ⇒
   `state=no_pack ∧ pending_export` 成为**永久态**：M1b 的 `offline_stalled` 锚点、`auto_handoff_decision`
   的节流/上界、M0 之外的「自动导包」能力全部落空，只剩人手点「导出任务包」。
4. 反过来保留端点但不管它 ⇒ `pending_export` 期间本机被 `rl-config` 停跑（配置写面绕过了 hold 的全部闸），
   且 M2 的「`rollout_src=run` 容忍读 ⇒ 映射 `local` + WARN」会把这份残留读成**继续跑**——
   两侧判据打架，现场是「日志说 WARN、训练确实停了/没停」取决于谁后写。

**建议**（三选一，写进 M1b/M4 并点名落点）：① 把 action 迁到新模块，语义改成**只导包、不写配置**
（`applyTrainModeToConfig` 随 `train-mode.ts` 一起退役；Q1 的唯一合法实现）；② 整条自动导包链退役，
`pending_export` 降级为「纯提示，等人手工导出」——那 `offline_stalled` 锚点与触发账本要一并重写（别留
只在文档里活着的机制）；③（不推荐）保留今天行为 ⇒ 必须回滚 Q1 的「本机照常跑」裁决，两者不能并存。
无论选哪条，`autoOfflineHandoff`/`route.ts:378`/`exportGuard`/`autoBundleDecision(mode:'offline')`
都要在 M1a 的普查施工单里有明确行。

### F2（P0）三个钟没收敛：`beat_at` + `OFFLINE_LEASE_STALE_SEC=180` 的去向一字未提

**plan 说**：§1.1「掉线 = 接管后 900s 无任何进度信号」；§1.2-4「心跳只续租约 TTL，**不作活性**」；
P2-1「任何合法接触（心跳 ∨ 进度）刷 TTL；只有进度刷 `last_progress_at`；`lease_verdict` 顺序
`expired → revoked → mine → stale → foreign`」+ 四象限表（心跳活×进度老 = stale）；
P0-1 只声明两个 900 常量「巧合同值、语义不同、禁止合并」。

**事实**（判据与写入两侧）：

- `lease_verdict` 的 stale 腿读的是 **`beat_at`**：`hub/task_pack.py:297-302`，阈值
  `offline_lease_stale_sec()` = `OFFLINE_LEASE_STALE_SEC = 180.0`（`:97`、`:102-109`，env
  `BCITY_OFFLINE_LEASE_STALE_SEC`）。
- `hub/queue_offline.py:513-527` `heartbeat_offline` **续 `expires_at` 的同时刷 `beat_at`**（六轮小项 4）；
  `holder_info` 的 `stale`（`:435`）也读同一个 `beat_at`。
- `claim_offline` 对 `stale` **自动接管、不需要 `takeover=1`**（`:463-487`：`verdict=="stale"` ⇒ 直接发新租约 +
  `reclaimed` 日志）。
- ⇒ 三把尺子并存：TTL 900s（`common/protocol.py:636`）、`beat_at` 静默 180s、以及 plan 新引入的**进度** 900s。

**两条歧路都会静默出错**：

- **照旧（stale 读 `beat_at` 180s）**：心跳线程整段在跑（§68 的「假活」）⇒ 进度死了的 worker
  **永判 live**，Q2 的 900s 掉线裁决落空，`_busy_locked`/派发恢复全按假活冻结。
- **改读 `last_progress_at`**：语义对了，但 180s 这个常量 + env 名（e2e 靠它调秒级）**没有归宿**，
  且 plan 的四象限表没有给出阈值数字——实施者会随手「保留 180」⇒ 200s 没进度的活主被自动接管，
  等于把 Q2 裁决改成 180s。

**建议**：M1a 点名三件事——① `lease_verdict` 的 stale 腿改读 `last_progress_at`，阈值 =
`HOLD_PROGRESS_STALE_SEC`；② `OFFLINE_LEASE_STALE_SEC`/`offline_lease_stale_sec()`/env 显式删除或
改名（别留同名不同义）；③ 补用例「心跳活 ∧ 进度静默 200s ⇒ 不 stale ∧ 不可接管 ∧ 本机仍 held」，
与 F2 一起进 §4 守门用例 1 的邻位（今天它只测「有完成事件 ⇒ live」与「零事件 >900s ⇒ 掉线」两档）。

### F3（P0）打点若落在包内代码（`remote/plan_run.py`），旧包会话没有打点却被按 900s 判掉线

**plan 说**：M3「**轮内打点**：`plan_run`/`iter_rollout` 的**完成事件** → 注入 `POST /offline/progress`」；
§1.1 进度信号 = `artifact`/`result`/`progress`/BC epoch；兼容矩阵「旧云机 × 新 hub ⇒ claim 缺 `?proto=2` ⇒ 409」。

**事实**：

1. **`?proto=2` 是「会话代码」的标，不是「包代码」的标**：`ipynb/battle.offline.ipynb` 的 bootstrap
   每次会话都从 GitHub raw 重取 `remote/{offline_boot,tailscale_boot,offline_deliverable}.py`
   （cell 文案「**每次会话都刷新**」）⇒ 新会话一定带 `proto=2`。
2. **但 worker 的运行代码来自包**：`remote/offline_boot.py:893-932` 把 `code.zip` 解到 `CODE_DIR` 并插进
   `sys.path`，优先级 = **`<dest>/code.zip`（产物目录）→ 任务包的 `code.zip` → hub 的 `GET /code`**；
   `remote/plan_run.py`、`worker/*` 都在其中。⇒ 一个**续跑旧产物目录 / 跑旧包**的会话，其
   `plan_run` 是旧 commit 的**没有打点**的版本。
3. ⇒ 该会话接管后：轮内零进度信号、只有轮边界 `artifact/result`。若单轮 >900s（M0 还没测过 P95，
   plan 自己把「P95 > 600s ⇒ 重开 Q2」设为门槛）⇒ **被判掉线**：hub 恢复协作派发 + 本机恢复 rollout +
   别的盘可 stale 接管——接管的是一台**正在正常跑**的盘。
4. 代价与 D2 同族（冗余续跑、有界），但方向反了：Q2 的「慢轮保活」被自己的代码来源打破，且
   `?proto=2` 挡不住它（它本来就是新会话）。

**建议**：定死打点的**归属层**——① 落在 `remote/offline_boot.py`（每会话 raw 刷新、与 `proto=2` 同源，
持有 lease token + hub 地址，`plan_run` 通过既有回调/环境把「轮内完成」事件递上来）；或
② hub 侧按**包的 commit/能力**降级活性判据（claim 时记录 `pack.commit`，旧包不参与进度腿、
退回轮边界活性），并把这条写进 §69 与兼容矩阵（它现在是第三条「旧端」）。顺便：`code.zip` 的
优先级意味着**「刷新 notebook」不等于换 worker 代码**（旧 work dir 会把旧码钉住）——这条要进 §69 的
现场判据（云机日志已有 `BOOT_SELF`/sha12 可读）。

---

## 2. P1 级

### F4（P1）`TASK_STATE_RANK` 现值引错

plan P1-7：「**保留现值序**（`ready 0 / stale 1 / claimed 2 / no_pack 3 / completed 4`，理由沿用
`hub/task_pack.py:116-119`）」。实际 `hub/task_pack.py:121-126` 是
**`ready 0 / stale 1 / claimed 2 / no_pack 3 / not_offline 4 / completed 5`**（引用的理由注释确实在
`:116-119` ✓）。P1-7 的整个卖点是「别无声对调顺序」，而它自己漏掉了 `not_offline` 并把 `completed`
提前一档——照抄就得到一个**与现状不同**的表。建议把六项照抄，并在 §69 写明 `not_offline` 仍占位。

### F5（P1）dashboard 侧迁移面没有普查（实测 435 处 / 26+ 文件）

plan 的 P1-5 只对 `nn-training` 做了符号普查（记 499 处 / 36 文件，我复现 491/35，见 F15），
M4 对 dashboard 只写「相关用例改词 + 新增 hold 派生/双通道用例」。实测（`dashboard/src`+`tests`，
`courseModes|trainModeKnobs|modeDrift|setCourseMode|pushCourseMode|pushHubMode|applyTrainModeToConfig|trainMode|rolloutSrc|runIters|run_iters`）：

| 落点 | 处数 | 备注 |
|---|---|---|
| `tests/course-mode.test.ts` | 67 | 整模块的目标测试 |
| `tests/train-mode-offline.test.ts` | 44 | 域换算 + 开课落键的守卫 |
| `src/server/actions/course-lifecycle.ts` | 34 | 开课/停课都推 hub（`pushCourseMode` / `applyTrainModeToConfig`） |
| `tests/course-lifecycle.test.ts` | 31 | |
| `src/server/actions/course-mode.ts` | 25 | 含 F1 的 `autoOfflineHandoff` |
| `src/server/actions/train-mode.ts` | 22 | M2 已点名要删 ✓ |
| `src/web/app/panels/OpenCourseModal.tsx` | 18 | M4 已点名 ✓ |
| `src/server/api/route.ts` | 18 | action 路由表（含 `autoOfflineHandoff`/`setCourseMode`/`unsetCourseMode`） |
| `tests/rollout-src-launch-option.test.ts` | 17 | 断言「写面住在 `train-mode.ts`」（:87-89） |
| `src/web/view/course-matrix.ts` | 10 | `modeDrift` |
| `src/stack/specs.ts` | 9 | **`trainModeKnobs`/`resolveRolloutSrc` = 域换算唯一推导点**（未点名） |
| `tests/web-course-matrix.test.ts` | 9 | |
| `console-state.ts` / `state-view.ts` / `core/types.ts` / `CourseAdmin.tsx` … | 各 2–6 | 意图键的读面/类型 |
| `tests/web-train-launch-wiring.test.ts` | 3 | 断言启动体含 `trainMode`（:59/:152） |

⇒ 建议 M4 像 M1c 那样出一张**逐文件处置表**（删/改名/改断言），并明确 `stack/specs.ts::trainModeKnobs`
是**删除**还是**保留但只剩在线分支**（它也是 `rl-config` 里 `run_iters` 的唯一写入推导点）。

### F6（P1）drain 互斥只有客户端自律；`role_blocked` 没有 worker 形参 ⇒ 守卫「形状不变」不成立

plan Q5/D4：「一拖一放 hub、按 worker」+「领 BC 算 drain（互斥）」；P1-7/M1c 又说
`test_role_routing.py:225` 的处理是「**改断言**（形状不变，字符串仍在）」。

事实：`hub/store_leases.py:164` `role_blocked(self, job_id, role)` **只有 job 与 role**（plan 的
「课程 hold 闸所有 kind 都吃」可以只靠 job 的课程名，成立 ✓）；但「**本 worker** 无别的 live hold ∨
live BC 作业」需要 worker 身份——`_claim_locked` 有 `worker_id`（`:316-326`），`role_blocked` 没有。
而 M5 的自主要腿只能自己自律（「`try_take_bc_job()` 前置闸 = 本 worker 无 live hold」）——这与
P0-3 定下的口径（「**不采纳云机侧自律**」）相反，且自律破口的最坏结果是**一份 BC 与一段 rollout 并行**
（浪费算力，产物不坏，因为有独占租约）。两条路必须选一条并写清：

- **A（hub 侧）**：把 drain 判据放进 `_claim_locked`/`role_blocked`（需要扩签名 ⇒
  `test_role_routing.py:225` 的 `blocked = self.role_blocked(job_id, role)` 断言**会变**，
  M1c 里那句「形状不变」要改）；
- **B（客户端自律）**：§69 写死「破口 = 冗余并行，有界」，并补一条「hub 侧不查」的理由
  （与 P0-3 的取舍分开记）。

### F7（P1）`not_offline` 的三重身份在删 `authority_of`/`_modes` 后没有新判据

同一个字面量今天有三处用法：`TASK_STATE_NOT_OFFLINE`（state，注释写明 = 「冷课带 **online 记录**」，
`task_pack.py:72-79`）、`AUTHORITY_NOT_OFFLINE`（`task_pack.py:89`，派生在 `queue_offline.authority_of`）、
以及**停课**的 409 reason（`hub/queue_offline.py:322`「`not_offline: 这门课不在训练中（开课标记已删）」）。

plan P1-7 只说「`not_offline` 保留为 `include_all` 专用态」，但 M1c 把 `AUTHORITY_*`/`authority_of`/
`_modes` 全删 ⇒ 「冷课带 online 记录」这个含义**消失**，而消费方还在：云机 `blockers` 名单
（`offline_boot.py:1674`）、`?include=all` 读面、控制台词表（`course-overview.ts:63/71`）。
⇒ 必须定死：`not_offline` ≡ 停课（那它与 `stopped` 正交维合并，还是两个都要）？还是整个退役
（停课改用 `stopped` 状态码）？两条都要顺带改云机侧。

### F8（P1）`pending_export` 没有过期/清理

v2 schema 是 `pending_export:{by,at}`，但：谁清它（下一次成功 claim？包出现？）、读面（清单行/
`offline_stalled` 锚点）是否按 `AUTO_HANDOFF_PENDING_SEC` 过滤，都没写。崩溃/换机的导包者会留下
永久「有人在导包」的软提示，`offline_stalled` 若照旧读 `at` 而无窗，就会对一个早已不存在的导包
告警。建议：读面一律 `now - at > window ⇒ 视为无`（惰性判据的风格与 hold 一致），并补一条用例。

### F9（P1）M0 的读数口径缺一半

M0 只统计 `iters[].wall_sec` 的 P50/P95（数据源坐实 ✓ `remote/plan_run.py:596-604`）。但打点是
**rollout 完成事件**驱动、PPO 段没有事件，而 Q2 的判定条件是「轮内每 ≤300s 有完成事件」——
轮级 `wall_sec` 合格**完全掩盖**不了「PPO 段独占 900s」这种形状。建议 M0 增读：① `ppo_sec`
占比（`artifacts.metrics_row` 里现成 ✓）、②「相邻完成事件间隔」的 P50/P95（≈ rollout chunk 间隔，
它才是 M 的取值依据）；并写死样本口径：排除 anchor 行（`plan_run.py:453` 的 `wall_sec=0.0`）、
smoke 轮、中断轮，注明课程名单与 run 数。

### F10（P1）Q4「显式拒旧客户端」只落在 claim 一面；「刷 notebook ≠ 换 worker 代码」

兼容矩阵写「旧云机 × 新 hub = **不支持**：claim 缺 `?proto=2` ⇒ 409」。但旧端有两条腿不走 claim：

1. **`CFG.course` 显式课名腿**：today 直接取包（`P1-2` 的配套改动才让新端「先 claim 再取包」）⇒
   新 hub 的取包门只拦「有 live hold」的情形（P1-2 的第二道闸 ✓），无 hold 时照发 ⇒ 旧端照跑。
2. **`CFG.task_zip` 腿**：本机自带包，完全不经 hub（`offline_boot.py:714`）⇒ 不受任何闸管。

两条都不至于双跑（无 hold 时没有"别人在跑"），但矩阵的「不支持」应收窄成一句可复核的话：
「**claim 面被拒**；取包面在别人持 live hold 时被拒；显式课名/自带包两条腿降级为现状（不受新模型管）」。
再补 F3-② 的 `code.zip` 优先级事实：升级的完整动作 = 刷新 notebook **+** 让本课换一份新包/新产物目录，
否则 worker 代码仍是旧 commit——这条决定「旧端窗口」有多宽。

### F11（P1）`_busy_locked` 两条腿没逐条处置

`hub/queue_offline.py:936` `_busy_locked`（plan 引 `948-980`，def 实际在 `:936`，±12 行）：

- 腿①（`:951-961`）：**别的课**有活租约 ⇒ `foreign` 才算在跑（过期/静默/墓碑不算），且对
  `authority_of(other) == PINNED_ONLINE` 的课 `continue`（§3.4-3）。
- 腿②（`:962-980`）：**别的课正在交接**——锚 `claimed_offline` + `claimed_by` + `claimed_at` 在
  `AUTO_HANDOFF_PENDING_SEC` 窗内、且包还没出现。

腿②正是 Q1 说「`pending_export` 不占**任何**闸」要删的东西（它就是"导包期占闸"的现存实现），
腿①/腿②两处的 `authority_of(...) == PINNED_ONLINE` 也随 authority 一起删。M1b 现在只写
「`busy` 按 worker」，没写这两条腿的逐条处置 ⇒ 实施者很可能把腿②「换个锚点」留下，
于是 §1.3 的「照常派」在代码里不成立（而 DoD#4 只测本机不停跑、不测「别课能领」）。
建议 M1b 里逐条写：腿① 改「按 worker 排除自己」、腿② **整条删**（常数只留给 `offline_stalled`）。

---

## 3. 二义与落点（P2，不阻塞但别漏）

### F12（P2）新端点的三处落点没进清单

`GET /offline/hold` + `POST /offline/progress`（plan 说「wire 全不动」= 现有名不改 ✓，新增不算破约）需要：
① `common/protocol.py` 的 `OFFLINE_*_PATH` 常量族（`:590-629` 现九个，无重名 ✓）；
② `hub/http_face.py` 的路由分发（`elif path.startswith("/offline/...")` 一族）；
③ 分域守卫 `tests/hub/test_hub_queue_split.py` 的 `DOMAINS`（`:104`）/`STATE_WRITERS`（`:319`）/
`ALLOWED_IMPORTS`（`:363`）——`hold_meta` 是「`queue_offline` → `store_leases`」的**新跨域写者**，
`role_blocked` 读它，必须登记（off.review 小项 2 的同一类坑）。§6 只写「common 1 文件」，太粗。

### F13（P2）`loop-control.json` 承载 hub 事实的三个前提

`trainer/loop_control.py` 头部把该文件定义成**人的暂停意图**，且保守方向是「**读不到 ⇒ 继续训练**」；
dashboard 侧 `parseLoopControl`（`src/server/actions/loop-control.ts`）只认 `paused`，对未知键宽容。
P0-5 往同一份文件里加 `held`（方向相反：读到 ⇒ 停本机）⇒ 要写死：① 版本/形状（plan 已说 `applied`
加 `held` ✓）；② 旧 trainer × 新文件（未知键忽略）补一条守卫；③ **同机时钟前提**（`tmp/` 共享；
跨机部署时 `last_progress_at` 与本地墙钟的偏差会把「held 自过期」判歪）。另外这条通道要多一句
「`held` 是 hub 事实的缓存、不是第二事实源」（与 `applied` 的「进程的一面之词」口径分开）。

### F14（P2）DoD#2 的措辞

「带 `?worker=` 清单说 `claimable=true` ⇒ 同参 `claim_offline` 必成功」——单线程守卫成立，
但线上有 TOCTOU（另一台盘先领）。建议 DoD 保留现状措辞 + §69 写明「清单是**上界**（含并发窗口）」，
免得实施者把守卫写成跨并发契约（那一写就会 flaky）。

### F15（P2）小项

1. **普查数字**：我用 plan 的同一条 rg（排除 `.venv`/`tmp`）复现 = **491 处 / 35 文件**（plan 记
   v1 的 499/36）。差值是时点差还是口径差（是否含 `tests/`/`e2e/`）要记一行，别当精确值用。
2. **`plan_run.py` 住 `remote/`**（不在 `trainer/`）；M0 引的 `plan_run.py:514/524/596-605` **全部命中**
   （`:524` 轮循环 `wall_sec`、`:596-604` `iters[].wall_sec`、`:453` anchor 行 `wall_sec=0.0`）。
   正文没带路径，施工时容易找错目录。
3. **`AUTHORITY_*` 住 `hub/task_pack.py:85-91`；`authority_of` 派生住 `hub/queue_offline.py`**
   （不是 `queue_scope.py`；那 22 处是 `mode_of` 一族的读者）。M1a 施工单别找错文件。
4. **`advance_active` 现成**：`hub/offline.py:315` 今天就按 `authority_of(course) != PINNED_ONLINE`
   判（P1-1 要接的点已存在 ✓，M1b 里直接点名行号）。
5. **`tests/test_production_isolation.py` 也是守卫**（6 处 `auto_handoff`；它把「模式 → rl-config 键」
   写成生产隔离的前提）——M1c 的「受影响的守卫用例清单」应把它列进去（现在只列了
   `test_role_routing.py` 与 `test_hub_queue_split.py` 两处，其余交给「普查施工单」兜）。
6. **`_ask_console_freshness` 的节流/上界与代码一致** ✓（`TASK_PACK_STALE_THROTTLE_SEC=600`、
   `TASK_PACK_STALE_TRIGGER_LIMIT=2`，`hub/task_pack.py:58-64`）。

---

## 4. v2 自报项抽查（复核成立，逐条落回真代码）

- **旧端兜底 `399 held` 链（P0-4 修订的关键论据）**：`remote/offline_boot.py:1360` 的 `busy` 分支确实把
  `doc.get('error')` 原样打进日志；`blockers` 名单在 `:1674`
  （`busy/pending_export/completed/not_offline`，**不占 idle 预算**）⇒ 用 `busy:true`+error 全文化
  「请刷新任务包」**能到达现场且不双跑** ✓；`held` 分支 `:1371-1379` 现文案「本机照旧跑」✓
  （即 F3-③ 里的旧端行为）。
- **心跳线程独立**：`:1431` `threading.Thread(target=_beat, name=f"offline-heartbeat-{course}")` ✓。
- **`?worker=` 已被云机带着走**：claim 请求 `:1342-1344`（`course`/`worker`/`takeover`），且
  `_run_auto` 在**取清单之前**就备好 worker id（`:1791-1796`）⇒ Q3 的清单侧加 `?worker=` 是纯 hub 侧改动 ✓。
- **取包三门**：`hub/offline.py:140-175`（`PINNED_ONLINE` 409 / `NOT_OFFLINE` 409 / 旧 mode 门）✓，
  与 P1-2 的替换点一致。
- **`_claim_without_pack` 形状**：`hub/offline.py:649`（至 ~707）✓，「自动课翻 mode + 请控制台导包；其余 404」
  ——F1 的三选一里要处理「其余 404」这条腿（新模型没有「人管课」了）。
- **`expires_in` 链**：`holder_info` 在 `queue_offline.py:420-435`，读方 `offline.py:547` +
  `offline_boot.py:1374` ✓ ⇒ P2-3「形状不变、读方一行不改」成立。
- **`boot.py:219` `parse_course_arg`** ✓（`--course a:offline` 形态，P1-3 的 WARN+忽略落点对）。
- **守卫行号**：`tests/hub/test_role_routing.py:225`（`blocked = self.role_blocked(job_id, role)`）、
  `:230`（`if self.parked and role != ROLE_OFFLINE`）、`:231`（`job_role(job_id) != role`）、
  `:234-249`（`test_parking_flag_is_synced_from_course_mode`）、`tests/hub/test_hub_queue_split.py:149/288`
  （两处 `role_blocked` 成员表）**全部命中** ✓（F6 只改 :225 的处置结论）。
- **文档节号**：`docs/nn/console.md:10` = §32（回放导出，2026-10-06）✓；`remote-transport.md` 最大 §68 ✓
  ⇒ 新节 §33 / §69 正确 ✓。
- **常数**：`AUTO_HANDOFF_PENDING_SEC = 900.0`（`queue_offline.py:83`）·`OFFLINE_LEASE_STALE_SEC = 180.0`
  （`task_pack.py:97`）·`OFFLINE_LEASE_TTL_SEC = 900`（`protocol.py:636`）·`HEARTBEAT_SEC = 60` ✓
  （P0-1 的「两个 900 别合并」成立，但见 F2）。
- **BC 进度信号**：`POST /jobs/{id}/epoch`（`hub/result.py:311` → `store_bc_epoch`）✓ ⇒ D4/Q5 的
  900s 让出锚点现成。
- **`_end_seal_ok`**（`hub/offline.py:392`）与 `advance_active`（`:315`）分家落点存在 ✓ ⇒ P1-1 可实施。
- **P0-5 的凭据面**：hub 是**单 token** 鉴权（`hub/http_face.py:235-265` `Authorization: Bearer`）⇒
  trainer 用 `runtime.token` 可以问任何端点 ✓；RL 侧 `worker/loop_round.py:136 hub_url` +
  `trainer/run_rl.py:275/395`（`--remote-hub-url`/`rl.remote_hub_url`）、BC 侧
  `trainer/loop_serve.py:240-300`（`clear_halt_on_startup(runtime.hub_url, runtime.token)`）✓。
- **M0 可行**：`iters[].wall_sec` 确实在回传体里（`remote/plan_run.py:596-604`）✓。
- **痛点「多机不能并行」**：`_busy_locked` 对**请求方之外**的课生效 ⇒ 一门课被接管，其余课对所有云机
  busy ✓（plan 的现状描述准确，只有行号偏移见 F11）。
- **`?proto=2` / `/offline/hold` / `/offline/progress` 无重名**（`common/protocol.py:590-629` 九个端点）✓。

## 5. 未验证项（评审边界）

- 未跑 pytest / 门禁（文档评审，无代码改动）；F1/F3 是静态追踪 + grep 坐实，建议实施时各先钉一条
  最小用例（F1：`autoOfflineHandoff` 不写 `/offline/` 之外的东西；F3：旧包会话 + 单轮 >900s ⇒ 活性判据）。
- **`e2e/test_hold_e2e.py` 的「env 调秒级」**：现有通道是 `BCITY_OFFLINE_LEASE_STALE_SEC`（单测/e2e 在用），
  新 `HOLD_PROGRESS_STALE_SEC` 已自带 env 名（`BCITY_HOLD_PROGRESS_STALE_SEC` ✓）——但 e2e 用例里
  **成对出现**的写法要跟 F2 的收敛一起定（两个钟同时被调秒级时，谁压谁必须可读）。
- **`autoOfflineHandoff` 改造后的下游**（`exportGuard` / `autoBundleDecision(mode:'offline')` /
  `launchTaskBundleExport`）只读到入口与副作用，没逐行读它与 `train-mode` 写面的耦合；F1 定方案时
  要连这段一起评审。
- 未核 Colab/Kaggle 侧「旧包会话」的实际占比（决定 F3 的影响面），只看清了 `code.zip` 的优先级机制。
