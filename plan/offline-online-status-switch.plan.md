# Plan: offline-online-status-switch — 课程训练状态切换重设计（权威三态 + 租约围栏 + 失联回收 + 训练侧复原）

> **交付物**：本 plan（现状解剖 / 根因 / 设计 / 分阶段实施 / 集成测试清单）。实现按 §5 的 P0→P2 执行。
> **状态**：待实施（2026-10-04 根因定案，来自用户报障两条；同日**二轮补审**增补 §2.4 八条（R3-a…R3-h）、
> **三轮补审**增补 §2.5 七条（R4-a…R4-g：首页课程区「经常不对、频繁变化」的读面机制）与相应设计/实施/用例）。
> **四轮评审**（`off.review-ds.md`，同日）逐行核对后已并入：**3 处 P0 硬伤**（lease_verdict 六态不可实现 ·
> 读面缺 authority 通道 · 续跑缺启动前置）+ **4 处事实收窄**（R1-e 假收官是条件级 · R2-e 文案 ·
> 「墓碑通知旧云机」不成立 · pin 存量迁移未评估）。修订点散见 §3.0/§3.3/§3.4/§3.5/§3.6/§3.9/§5/§6/§7/§9，
> 关键行以 **★评审修订** 标出。
> **五轮评审**（`off.review-hy.md`，同日）复验四轮全部成立后，报出**设计判据的消费方没跟上**这一共同形状：
> **2 处硬伤**（P0-A `auto_claimable` 无 stale/revoked ⇒ T3/T7 必红 · P0-B 导包触发账本 `give_up` 上界没在换主时重置
> ⇒ 报障二在无包分支照样复现）+ 6 处收窄（P1-C 冷课 `auto` 能力扩张 · P1-D 日志抑制点 · P1-E `resolve_courses` 签名 ·
> P1-F 四键清理 · P1-G 文件归属 · P1-H `bc_ledger.inflight_jobs`）。**§3.0 新增第 7 条设计原则**（新判据必须同批点名全部消费方）。
> 其上 §4-1（竞态改回 409）与 P1-G 的 §1.3 半条经评估**未采纳**（见 §8 与被否决说明）。
> **六轮评审**（`off.review-bf.md`，同日）把每条判据 / 行号 / 消费链拿到工作树真代码逐条对账（纯静态，未跑门禁），报出：
> **1 处 P0 修法落点错误**（F1：「再发布=复活」修在**零生产调用**的 `store_ledger.publish` 上；生产 republish 走
> `remote/hub_client.publish_job`（磁盘 IPC，无条件追加 `job_pending`），真阻塞是 hub 内存 `_cancelled` 无清理点）
> + **2 处判据未点名**（F2 `auto_eligible` 重定义后 busy 闸两消费点 · F3「停课⇒claim 拒」不是现状——生产 stop
> 推 `mode=offline` 故今天停课+有包仍可被领走）+ **3 处需定死的二义**（F4 同 worker_id 回来 · F5 墓碑 holder 形状 ·
> F6 冷课 `!pinned` 与停课的 authority 全组合）+ 一批守卫/文案小项（§3 小项 1–7）。修订已并入
> §3.1/§3.2/§3.3/§3.4/§3.6/§3.7/§3.9/§5/§6/§7/§9 对应行，关键处标 **★六轮**。
> **触发（用户报障，逐字）**：「现在的课程离线/在线状态切换很不稳定。如 offline worker 抢占过的课程，
> 手动切为在线却不能被在线 worker 领取 job；又如一个 offline worker 抢占过课程却停机（配额用尽）后，
> 另一个新上线的 offline worker 迟迟领不到 job 等。」
> **关联决策**：`§2026-09-25-goalnn-role-routing`（job 归属）· `§2026-10-03-goalnn-auto-offline-handoff`（claim 即接管）·
> `§2026-10-03-goalnn-offline-seize`（pin 语义坍缩——**本 plan 部分回摆**，见 §4.2）·
> `§2026-10-03-goalnn-switch-mode-drops-jobs`（切模式撤单——**本 plan 半球修正**，见 §4.3）·
> `§2026-10-01-goalnn-lease-orphan-reap`（job 租约早收；本 plan 是**离线租约**的同族，之前明文排除在外）。
> **姊妹 plan**：`plan/online-offline-role-routing.plan.md`（归属）· `plan/lease-orphan-reap.plan.md`（job 租约；
> 本 plan 沿其「惰性回收、无后台 tick」形状）· 自动交接的现状全文见 `docs/nn/remote-transport.md` §56/§57/§59。
> **路径基准**：除注明外相对 `nn-training/`（例：`hub/queue_offline.py` = `nn-training/hub/queue_offline.py`；控制台 = `dashboard/`）。
> **阅读顺序**：§1 事实链 → §2 根因 → §3 设计 → §4 行为变更 → §5 实施 → §6 集成测试 → §7 DoD → §8 否决 → §9 灰区。
> **行号只作定位加速，判据看函数名。**

---

## 0. 一句话目标

**让「这门课现在由谁跑」只有一个可判定的答案**（权威三态 + 租约围栏 + 失联回收）：
人把课切回在线 = 当拍生效且稳定（离线盘不再抢、在线 job 可领、本机训练侧自动复原）；
离线盘停机 = 新盘在心跳宽限内自动接管，且死租约不阻塞任何别的课。

---

## 1. 现状机制解剖（四个角色 × 八个状态载体）

### 1.1 角色与状态载体

| # | 载体 | 住哪 | 谁写 | 谁读 | 语义 |
|---|---|---|---|---|---|
| S1 | `courseModes`（三态意图） | 控制台 `console-state`（盘） | `setCourseMode` / `unsetCourseMode`（人）/ 自动交接**不写** | 回灌、面板漂移徽标 | `unset`/`online`/`offline` = **人的意图** |
| S2 | `hub mode`（派发闸） | hub `_modes` + `offline-dispatch.json`（`mode`） | `set_mode_pinned`（人/回灌）、`note_claim`/`begin_auto_handoff`（claim 翻） | `_sync_parked`、取包端点、清单 | 在线/离线派发 |
| S3 | `pinned` | 同上（`dispatch.json`） | 人（`pin=1/0`）；claim 不写 | `pinned_of`（仅停摆告警与 legacy 拒绝用） | 「人管过」——2026-10-03 后**不拦离线盘** |
| S4 | `claimed_offline` / `claimed_by` / `flipped_at` | 同上 | claim 两条腿；人的切在线清 | 清单/停滞告警/busy 窗口 | 「谁在自动交接/接管」的记账 |
| S5 | `completed_pack_sha` | 同上 | `note_offline_completed`（段末跑满） | `completion_blocked` | 完成锚（重导包 sha 变解封） |
| S6 | rl-config `courses.<课>.rollout_src/run_iters` | 控制台写、python 每轮读 | `applyTrainModeToConfig`（人的开关、自动交接反向调用） | `_rollout_source` / `_run_segment_iters` | 本机腿怎么跑（`run` = 本机不跑） |
| S7 | `training-enabled.txt` | `<traj>/<课>/` | 开课/停课 | `auto_eligible`、`enabled_courses`、`reopened_parked` | 在训标记（open_time = mtime） |
| S8 | 离线租约 `_leases[course]` | hub 进程内（volatile） | `claim_offline`/`heartbeat_offline`/`release_offline` | 清单 holder、claim 判据、busy 闸 | 「谁在跑这门课」的**排他**记账（TTL 900s） |

### 1.2 四条链路（今天真实跑法）

1. **在线训练**：共享 trainer（`trainer/run_rl_cluster.py --serve`）逐轮发 `iter`/`ppo` job（`manifest.role=online`）
   → 在线 worker peek/claim（两道闸：job 级 role + 课程级 `parked`）→ 回传 → 下一轮。
2. **切离线 / 自动接管**：离线盘 `GET /offline/tasks` → `claim`（有包走 `claim_offline` 建租约；无包走
   `begin_auto_handoff` 翻 mode + hub→控制台 `/api/autoOfflineHandoff` 写 rl-config + 导包 + 409）→ 取包 →
   整段自主跑（心跳 60s）→ 逐轮补传 / 段末摘要。**claim 成功即翻 `mode=offline`（两条腿都翻）**，
   并 `_drop_unsettled` 撤掉该课未认领的在线 job。
3. **本机腿对离线的反应**：`step_course_iter` 见到 `rollout_src=run` ⇒ 一行指路 + `ROUND_OFFLINE_EXIT`；
   共享 trainer 把它当**终局**（`_map_outcome` → `done(final=True)`）⇒ `_settle_rounds` 调 `finish_course`
   （写 `run_complete` + 云机 PAUSE），课程队列置 done。
4. **切回在线（今天）**：`setCourseMode('online')` = 写 rl-config + 推 hub `mode=online&pin=1&drop_jobs=1`。
   没有第四步——**没有东西把 trainer 重新入队，也没有东西撤销离线租约**。

### 1.3 两套租约——为什么离线租约不能照抄 job 租约

| 维度 | job 租约（`_JobStore._leases`） | 离线租约（`_HubQueue._leases`） |
|---|---|---|
| TTL | 300s（`CLAIM_TTL_SEC`） | 900s（`OFFLINE_LEASE_TTL_SEC`） |
| 续租 | worker 心跳（下载前就起） | 云机心跳线程（60s） |
| 失联早收 | **有**：§52 孤儿租约（claim 后**零心跳**超 `ORPHAN_GRACE_SEC=180` 提前回池） | **无**（`plan/lease-orphan-reap` §2 明文排除「离线段不适用」） |
| 回收形状 | `_lease_state` / `_lease_held` 四处同源（池过滤、claim、`lease_worker`、`inflight`） | 只有 `lease_verdict` 四态（free/mine/foreign/expired） |
| 越权接管 | `takeover` 语义在备份授权/避让链上 | `takeover=1` 仅人工（控制台/搬机） |
| 存储 | 进程内（hub 重启即丢，由首写锁定兜） | 进程内（同左） |

### 1.4 控制台意图与 hub 实际态的三态分工（今天）

| 意图（S1） | hub（S2/S3） | 含义 |
|---|---|---|
| `unset`（键不存在） | `pinned=false`，mode 由运行态决定 | **自动池**：离线盘可 claim/seize |
| `online` | `pinned=true, mode=online` | 「人管过」——但 2026-10-03 起**离线盘照样可抢** |
| `offline` | `pinned=true, mode=offline` | 人指定离线；云机 claim 许可 |

**注意**：S1 是意图、S2 是派发闸、S3 是记账——三者的语义在 2026-10-03 被拆开过一次（pin 坍缩），
但**没有任何一处把三者合成一个「这门课现在归谁」的派生函数**。这就是不稳定的土壤。

---

## 2. 根因（两条报障逐条落到代码）

### 2.1 报障一：offline 抢过的课手动切在线后，在线 worker 领不到 job

链条不是单点，是五个缺口串起来的：

| # | 根因 | 证据（函数/行为） |
|---|---|---|
| R1-a | **pin 不拦自动回抢**：claim（`note_claim`）/无包接管（`begin_auto_handoff`）对 `pinned` 的课照样翻 offline；`seize` 也含 pinned-online 课 | `auto_eligible` = 在表 ∧ 开课标记（**不含 pin**）；`offline_tasks` 的 `seize = real and not offline and auto and not completed`；`test_seize_claim_flips_pinned_online_and_drops_unclaimed_jobs` 钉的正是这个行为 |
| R1-b | **每一次回抢都撤在线 job**：claim 翻 offline 时 `_drop_unsettled(reason="auto-handoff")` 把该课未认领的 job 写 `job_cancelled` | `note_claim` / `begin_auto_handoff`；这是 R1-a 的放大器 |
| R1-c | **切在线不撤销离线租约**：`set_mode_pinned` 只改 `_modes`/dispatch 记录，`_leases` 一字不动 ⇒ 旧云机继续心跳（200）、继续跑完（双腿并发），下次 drain 循环还能再 claim | `claim_offline`/`heartbeat_offline` 全文；`set_mode_pinned` 不碰 `_leases` |
| R1-d | **训练侧不复原**：`ROUND_OFFLINE_EXIT` 是终局；共享 trainer 的复活判据 `reopened_parked` 只看 `training-enabled.txt` 的 mtime（只有停→开才变），切模式不碰它 ⇒ 课程永远不再入队 | `loop_runner._map_outcome`（`done(final=True)`）；`loop_serve.reopened_parked`；`setCourseMode` 不 touch 标记 |
| R1-e | **假收官**（★评审修订：**条件级**，不是无条件）：共享 trainer 对 offline 退出的课也走 `_settle_rounds` → `finish_course` ⇒ 写 `run_complete`（「已完成」横幅）+ 云机 PAUSE——把「被云机接走」记成「跑满了」 | `_settle_rounds` 遍历 `QUEUE_DONE` 课，但**有跳过分支** `rt is None or rt.engine is None ⇒ continue`（`loop_serve.py:936-938`）⇒ 只在「本地至少跑过一轮（存在 engine）」时成立；`finish_course` 的 `write_run_complete` 调用即达（无内部条件），`_sync_cloud_halt` 对「无 hub/已停机/notify/软判决」短路（`loop_guards.py:155-183`）⇒ PAUSE 是**效果层有条件** |
| R1-f | **再发布也领不到（潜在主因；二轮深化见 R3-b）**：撤单有**两层**——① `_cancelled` 即时闸（`publish` 不 discard）；② 账本折叠（`job_cancelled` 晚于旧 `job_pending`，且 `publish` 按历史 `pending_ids` 去重、**不会再追加** `job_pending`）⇒ 重发同 job_id 后两个层面都仍是撤单：池里看不见、claim 永远拒 | `store_ledger.cancel_unsettled_jobs`（`_cancelled.add`）/ `publish`（去重逻辑）/ `store_leases._claim_locked`；**探针实测**：cancel→republish 后 `claimable=[]`、`claim=None`、`_cancelled` 仍含该 jid |
| R1-g | **多源状态无派生**：面板「在训」读 S7、hub 读 S2、本机读 S6、调度器读队列 done——四处可以各说各话；操作员看到「在线在训」而实际没有任何在线腿（**首页课程区的逐条机制见 §2.5**，R4-a…R4-g） | `docs/nn/console.md` §22 的 modeDrift 教训（三个意图三个源，今天只补了徽标） |

**症状合成**：人切在线 → 可能过 15 分钟就被 R1-a 翻回去（R1-b 顺手撤光 job）；即使不翻回去，
本机腿已 done（R1-d/e），没有任何在线 job 产出；就算有人停→开课把 trainer 复活（bump 标记 mtime），
重发的同 job_id 又撞 R1-f 的 `_cancelled` ⇒ 「offline worker 抢过的课，切在线后在线 worker 领不到 job」。

### 2.2 报障二：offline worker 停机（配额用尽）后新盘迟迟领不到

| # | 根因 | 证据 |
|---|---|---|
| R2-a | **离线租约只有 TTL 没有活性**：死租约要等满 900s（心跳停后）才消失；job 侧早有 §52 孤儿早收，离线租约明文排除 | `lease_verdict` 只判 free/mine/foreign/expired；`OFFLINE_LEASE_TTL_SEC=900`；`plan/lease-orphan-reap` §2 边界② |
| R2-b | **死租约冻结全池**：`_busy_locked` 把**任何**别的课的活租约当「机器在忙」⇒ 一个死租约让新盘连**其它课**也抢不了（一拖一闸被死盘占住 15 分钟） | `_busy_locked` 腿①（活租约）；`auto_claimable(busy=…)` 把 busy 行标成不可领 |
| R2-c | **新 worker 无法自动接管**：清单行的 `claimable` 把任何持有者（含 stale）排除；`mine` 只认同 id（2026-10-04 commit 48ea30a7 修的正是「自己」那一档）；`takeover=1` 只有人工路径 | `_holder_id` / `resolve_courses` 的 `picked`/`mine` 两层；`queue_offline.claim_offline` 的 `foreign and not takeover` |
| R2-d | **导包窗口的记账人没有活性**：无包 claim 走 `begin_auto_handoff`（**不建租约**），死掉时只剩 `claimed_by/flipped_at`；窗口 900s 内别的课照 busy | `AUTO_HANDOFF_PENDING_SEC=900`；`begin_auto_handoff` 不刷新 `claimed_at` |
| R2-e | **会话先收工**：新盘在 TTL 内拿到的都是 409/held，`idle_wait_sec` 用尽就 SystemExit——「迟迟领不到」= 这一整段会话白等 | `_run_auto` 的 idle 预算（held 行不重置 `idle_since` ⇒ 消耗预算）；`_blocked_note` **已报**租约 TTL 与**包**过期（`offline_boot.py:1567-1576`，★评审修订）——缺的是**租约静默（stale holder）**那一档，不是「只报 TTL」 |

### 2.3 一句话根因

**模式（mode/pin）被当成唯一权威却被记为易变记账；租约被当成排他却只有 TTL 没有活性；
「离线」被训练侧当成「结束」。** 三者叠加就得到两条报障的形状。

### 2.4 其它问题（二轮补审，同日；与两条报障同族）

二轮只挖 §2.1/§2.2 未覆盖的面：**发现/登记路径、账本复活、冷课读面、回灌语义、告警静音、
回传落位、清单预算、暂停意图**。标「探针」的两条在本机真跑过（`nn-py-safe.sh`，只读/临时目录）。

| # | 严重度 | 问题 | 证据 | 设计指向 |
|---|---|---|---|---|
| R3-a | 高 | **发现登记不同步 `parked`**：`add_course` 只写 `_modes`，不写 `st.parked`。hub 以 `--discover` 起（无 `--course`）时**所有**课都走这条路 ⇒ 盘上 `mode=offline` 只在**读面**（清单/取包）生效，**派发闸不设**：重启后残留的在线 job 可被在线盘领走（模式权威与停摆闸分叉） | `hub/queue_scope.py::add_course`（无 `_sync_parked`）；`hub/queue.py::__init__` 的两处同步只覆盖初始 `_order`；`store_leases.role_blocked` 只认 `st.parked`。**探针实测**：discover 带 offline 记录的课后 `mode=offline, parked=False, role_blocked(online)=''` | §3.1 末尾（登记同步）· P0-8 |
| R3-b | 高 | **「再发布 = 复活」不只差 `_cancelled`**：清即时闸**不够**——账本折叠仍判撤单，且 `publish` 的去重按历史 `pending_ids` 判定（旧 `job_pending` 还在），**不会再追加** `job_pending` ⇒ 即便清了 set 也永不可领。这是 R1-f 的完整形状 | `store_ledger.publish` 的去重；`claimable_job_ids` 的折叠。**探针实测**：cancel→republish 后 `claimable=[]`、`claim=False`、`_cancelled` 仍含 jid | §3.6（净态复活）· P0-6 扩写 |
| R3-c | 中高 | **冷课被硬编码当离线**：不在课程表的在训课，清单把 `offline=True` 写死、取包/claim 门只看表 ⇒ **盘上的派发记录（含 pin/在线意图）在这条面上不生效**：「切在线」的课在 hub 冷掉/重启窗口里仍可能被离线盘领走旧包 | `queue_offline.offline_tasks`（`offline = … if real else True`）；`hub/offline.py::_get_task_pack` 的 mode 门（`course in courses()` 才拦）；`_post_offline_lease` 的归属门同理 | §3.1 末尾 + §3.9（冷课 authority）· P0-9 |
| R3-d | 中 | **回灌推不动被 claim 过的 'online' 意图**：`pushCourseMode` 不带 pin ⇒ hub 按既有契约 400（「要切回在线请带 pin 参数」）⇒ 控制台意图与 hub 永久分叉，且每次起 hub 都刷失败（`restoreCourseModes` 走同一条 legacy 路） | `course-mode.ts::pushCourseMode`（pin 只由 `setCourseMode` 传）；`queue_offline.set_mode_pinned` 的 legacy 拒绝；既有用例 `test_admin_courses_get_reports_pin_and_claim_state`（st3==400）钉住 | §3.9（意图 v2 + 回灌带 pin）· P1-8 |
| R3-e | 中 | **停滞告警对 pinned 课整体静音**：`stall_verdict` 一见 `pinned` 就返回空 ⇒ **pinned_offline（人指定离线）**的课云机停机也不告警——报障二场景里最该响的那一声被掐掉 | `task_pack.stall_verdict`（`if not treat_offline or pinned or completed: return ""`）；`queue_offline.offline_stalled` 传 `pinned_of(course)` | §3.7（只静音 pinned_online）· P0-10 |
| R3-f | 中高 | **回传落位无视模式与撤销**：`/offline/artifact` 不认租约/模式，`_land_offline_round_extras` ② 会把**活动权重**（`<traj>/weights.json`）推进到已作废线段（当账本里没有更大 it）；③ 照常归档；`/offline/result` 的 `end_it_reached` 给**当前包**盖章（body **已带** `run_id`/`plan_sha256`，但 hub 不校验它）⇒ 旧 worker 的迟到摘要可以把**人刚重导的新包**标成「已跑满」，云机从此拒领 | `store_offline._land_offline_round_extras`（`_ledger_has_newer_iter` 只比 it）/ `store_offline_result` + `hub/offline.py::_post_offline_result`；`remote/offline_deliver.deliver_result` 的 body（有 `run_id`/`plan_sha256`，`bundle.py` 索引也有） | §3.3 末尾（撤销后回传）· P1-7 |
| R3-g | 低中 | **清单解析分不清「空队列」与「有活但被持有」**：被外地租约挡住的课让 `resolve_courses` 返回空表 ⇒ `_run_auto` 消耗 `idle_wait_sec` 预算、可能提前收工（报障二里新盘的「迟迟」有一半来自这里） | `remote/offline_boot.resolve_courses`（held 行不进任何一批 ⇒ 返回 []）；`_run_auto` 的 `idle_since`/预算分支 | §3.5 末尾（blocker 预算）· P1-9 |
| R3-h | 灰 | **暂停的课仍可被离线盘抢**：`auto_eligible` 只看开课标记，不看控制台暂停意图 ⇒ 「暂停」不构成自动接管的否决（与「暂停 = 保留队列，恢复后接着跑」的直觉冲突） | `queue_offline.auto_eligible`；`dashboard` 的暂停写 `loop-control.json`（hub 不读）；停课（删标记）才是唯一 opt-out | §9 灰区（二选一并记 DECISIONS） |

**二轮小结**：R3-a/b/c/f 都是「权威在某条面上不生效」的同一形状（发现面、账本面、冷课面、回传面），
与 §2.3 的根因同族；R3-d/e 是「意图没有完整载体」与「告警判据过粗」；R3-g 是报障二的体验补丁；R3-h 是需要裁决的边界。修法全部落进 §3 对应小节与 §5 的 P0-8…P0-10 / P1-7…P1-9。

### 2.5 首页课程区为什么「经常不对、频繁变化」（三轮补审，同日）

用户点名的是 dashboard 首页的「课程」区：顶部 pill 行（`TrainingPills`）+ 课程矩阵（`CourseMatrix`）。
R1-g 只记了总纲（「多源状态无派生」）；本节把读面**逐条拆开**——同一门课的状态词由 6 个事实源拼出、
各自时钟与失败语义；再叠加两套词表（pill / 矩阵）与三类持久错误态。这些是**读面自己的缺陷**
（不是 hub/训练侧行为），修法落 §3.10 + P1-10/P1-11。

| # | 严重度 | 机制 | 证据 | 设计指向 |
|---|---|---|---|---|
| R4-a | 高 | **多源多时钟 + 两套词表**：同一课的状态词由 6 个源拼——①开课标记（动作写、请求现读）②共享 trainer 存活（registry pid、现读）③调度器队列（python 子进程、10s TTL + SWR、失败=空行）④hub 观测（逐候选 1.2s + 5s TTL + SWR）⑤控制台意图（文件、现读）⑥rl-config `rollout_src`（现读）。pill 走 `coursePills` 优先级（暂停 > 收官/中止 > 待进程 > 意图未生效 > 离线词 > hub 派发词 > waiting 兜底），矩阵状态列走 `matrixStatus` 的**另一条**优先级 ⇒ **同屏两个 widget 可互相矛盾**：离线接管的课 pill「已收官」（R1-e 的假收官排在 hub 词之前）而矩阵「在训」；暂停课 pill「已暂停」而矩阵状态列仍「在训」（暂停只进旁挂徽标 `pauseOp`） | `web/view/loop-queue.ts::coursePills` · `web/view/course-matrix.ts::matrixStatus/pauseOp` · `app/app.tsx` 的 L490/L585 两处接线 | §3.10-1 |
| R4-b | 高 | **探测超时退化换词（词跳主发动机）**：`liveHub` 逐候选 1.2s（`HUB_PROBE_TIMEOUT_MS`），任一拍 hub 不快/候选变多 ⇒ `queue=null` ⇒ 全课 `hubSeen=false/offline=false`、离线进度 null ⇒ pill 从 hub 词（回传中/等云机/等回传/卡住/排队·无人取/预取中）**跌回训练侧词**（已收官/采集中/空闲/视图不可用），下一拍探测成功又跳回；5s TTL 的 SWR 让这种翻转按「每次刷新换一个说法」的节奏出现 | `stack/hub-admin.ts::liveHub` · `server/api/overview.ts::hubCache` · `web/view/loop-queue.ts` 的 `hub?.offline` 分支与 `hubLive` 门 | §3.10-2/4 |
| R4-c | 中高 | **hub 重启/发现窗口 + 回灌竞态**：课程表由扫盘发现来填（启动 force 扫一次，但「端口就绪」与「课程表就位」不保证同一帧——e2e 的就绪断言专门等它；平时最小间隔 `DISCOVER_SCAN_MIN_SEC=2s`），`hubSeen` false→true 翻转；`restoreCourseModes` 在发现之前推 ⇒ 400（R3-d）⇒「意图未生效」常驻；hub 启动默认 mode=online ⇒ 面板先在线词、随后黄漂移、手动再推才变——每一步都是真实状态，读面把它们播成「忽上忽下」 | R3-d 证据行 · `hub/boot.py`（启动 force 扫）· `hub/queue_discover.py`（2s 闸）· `course-overview.ts::buildCourseRows`（`hubSeen = q !== undefined`） | §3.9（已含）· §3.10-4 |
| R4-d | 中 | **动作后的冷热混用**：`invalidateAfterAction` 对 hub 观测/调度器视图是**软作废**（旧值先给、重算后台），pause 意图/回执与组件状态却**每拍现读** ⇒ 动作后几秒内一屏里「即时事实」与「陈旧事实」并排（刚切离线：意图栏已离线，hub 词仍是在线词）；不同 widget 读到的还不是同一个新旧组合 | `snapshot-refresher.ts::invalidateAfterAction` · `server/api/loop-queue.ts::withPausedFacts`（现读与 SWR 混用） | §3.10-3/4 |
| R4-e | 中 | **读失败以空行替换上一拍**：`getLoopQueueView` 失败返回**空行 + error** 视图并存入 SWR（先给旧行、重算落地即替换）⇒ 此后 ≥TTL 内整列「视图不可用」；而 `matrixMeta` 写「上一拍读失败（显示缓存）」——实际没显示缓存（行已空），文案与行为不符。hub 侧同理（失败结果带 `url:null` 落 5s 缓存） | `server/api/loop-queue.ts::viewFromRunResult/getLoopQueueView` · `core/swr-cache.ts`（失败值也算新值） · `web/view/course-matrix.ts::matrixMeta` | §3.10-3 |
| R4-f | 中高 | **三类持久错误态在首页的读数**（不是闪变，是持续说错）：①假收官（R1-e）——离线接管的课被本机 trainer 标 done ⇒ pill「已收官」+ 收官横幅（声量最大），而云机正在跑；②意图未生效（R3-d）——每次 hub 重启后常驻黄词，要手动再推；③冷课分叉（R3-c）——面板显示离线/人固定在线，hub 那条面仍可能派活。三者叠加闪变 = 用户体感「经常不对 + 频繁变化」 | R1-e / R3-d / R3-c 证据行 | §3.5 · §3.9 · §3.10-5 |
| R4-g | 中 | **没有来源/新鲜度标注、没有滞回**：读面不区分「确定」与「不知道」（`hubSeen=false`、`offline=false`、空行都是「未知」，却被渲染成否定/默认词）；词一切换立刻换（无稳定窗口），合法窗口（claim 翻转、导包、hub 发现）读起来就是跳字。`matrixStatus` 的 `hubOnline` 门只挡了半个假诊断——hub 无应答且未在训时仍渲染「未在训」 | `coursePills`（缺行 ⇒「视图不可用」；hub 降级 ⇒ 回退词） · `matrixStatus`（`hubOnline=false` 分支） | §3.10-2/3/4 |

**三轮小结**：R4-a/e/g 是读面自身的缺陷（拼装、退化、语义）；R4-b/c/d 是「多源各自失败与刷新」在单屏上的
投影；R4-f 是既有根因的首页读数（修 R1-e/R3-c/d 后自然消失）。共同修法不是给每个源加特判，而是把
「这门课现在归谁/在干什么」收成**一个派生函数**，来源可用性只影响**标注**、不影响**词**（§3.10 + P1-10/P1-11）。

---

## 3. 设计：权威三态 + 租约围栏 + 失联回收 + 训练侧复原

### 3.0 设计原则

1. **人 > 自动**：人的一次「切换成在线」是硬意图，自动交接不得覆盖；「交还自动」才回到自动池。
2. **一个答案（每侧同规则、各自实现）**：claim / seize / 清单 / busy / 训练侧读的是**同一个派生函数**
   （§3.1 `authority_of`），不许各判各的。★评审修订：hub（Python）与 dashboard（TS）**跨进程无法共享代码**
   ⇒ 实际是「一规则两实现」——`hub/queue_offline.py::authority_of` 与 `dashboard/.../course-status.ts::courseStatus`
   必须逐条同构，靠 §6.4 的一致性用例钉住（不是靠「只维护一处」的错觉）。
3. **失联可回收**：任何「有人在跑」的记账都必须有活性判据；没有活性的记账只能算历史。
4. **切回在线 = 可复原**：训练侧把「离线」当等待（不是收官），模式变了自动续跑，无需停开课。
   ★评审修订：前提是 trainer 以 `--rollout-src auto`（或缺省）启动——见 §3.5。
5. **不新增第二个事实源**（★评审修订，原写「零新状态载体」）：全部落在**既有载体上加字段**
   （`dispatch.json` 字段 + 租约字段 + 意图表 v2），不发明新的状态机；新增字段必须进
   `dispatch_record_default` / `dispatch_record_merge` 才落得下盘（见 §3.3 末尾）。
6. **惰性回收**：没有后台线程——谁先看谁回收（沿 `plan/lease-orphan-reap` 形状）。
7. **★评审修订（五轮总评，新增）**：**每新增一档判据，必须在同一批里点名它的全部消费方**——
   纯函数形参（如 `auto_claimable` 的 `holder_stale`）/ HTTP 行字段（`offline_tasks`、`queue_state`）/
   worker 选课层（`resolve_courses` 的 `picked/mine/seize`）/ 控制台读面（`courseStatus`）/
   触发账本（`auto_handoff_decision`）。四轮与五轮各发现「hub 内部判对了、但判决没递到能行动的人手里」
   （authority 没递到 dashboard · stale 没递到 `auto_claimable` · 触发账本没在换主时重置）——同一类错误的三个实例。
   实施时每个 P0 行**必须列出消费方清单**，缺一即视为未完成。

### 3.1 权威三态（`course_authority(course) -> str`，唯一派生函数）

派生自既有事实（`dispatch_record.pinned` + `mode_of` + 开课标记），不新增字段：

| authority | 条件 | claim | seize | claim 时可翻 offline | 备注 |
|---|---|---|---|---|---|
| `pinned_online` | `pinned ∧ mode=online` → 含临时漂移 | **拒**（409 `pinned_online`） | **不列**（`seize=false`） | **不翻** | 人固定在线；唯一解锁 = `unsetCourseMode`（pin=0） |
| `pinned_offline` | `pinned ∧ mode=offline`，**或冷课无记录**（见 §3.1-冷课） | **许** | —（已是离线） | 不翻（已 offline） | 人指定云机跑 / 从未登记但仍按离线 |
| `auto` | `!pinned` **∧ `course in _stores`**（★评审修订，见下） | 许（有包建租约/无包走导包链） | 许（在训在线课，U1 默认） | **翻**（自动交接） | 开课弹窗未选模式 = 这一档 |
| `stopped`（正交维） | 开课标记不在 | 拒（409 `not_offline`，现状） | — | — | 唯一 opt-out 不变 |

- 实现：`hub/queue_offline.py` 新增 `authority_of(course)`（读一条 dispatch 记录 + mode + 标记），
  `auto_eligible` 拆成两个问题：`auto_handoff_allowed(course)`（= `authority == auto` ∧ **`course in self._stores`** ∧ 标记在）
  与 `is_runnable_offline(course)`（= 标记在 ∧ ¬pinned_online）。**所有**调用点（清单、claim、busy、seize、T2 翻模式）
  改用这两个之一，禁止再直接读 `auto_eligible`（保留为兼容别名 = `auto_handoff_allowed`，测试守卫其唯一性）。- **★评审修订（五轮 P1-C，`auto` 档必须带 `course in _stores`）**：`mode_of` 对未登记课返回 `ONLINE`
  （`queue_scope.py:210-211`）、`_dispatch_load` 缺省回 `ONLINE`（`queue_offline.py:475`）、`dispatch_record_default`
 的 `pinned=False`（`:84`）⇒ **只按 `!pinned` 判会把冷课派生成 `auto`**；而 `auto` 档下
`auto_claimable(auto=True, pack_exists=False)=True`（`queue_offline.py:146`）⇒ **无包冷课能被 claim 并触发导包**
——今天不可能（`auto_eligible` 第一条 `if course not in self._stores: return False`，`offline` 恒 True ⇒ 无包不可领）。
这是**未声明的能力扩张**，且与 §9「无记录冷课维持『按离线』」自相矛盾。**定死 = 两条同时成立**：
① 冷课（∉ `_stores`）**无记录** ⇒ 派生 `pinned_offline`；② `auto_handoff_allowed` **额外要求 `course in _stores`**
（把今天 `auto_eligible` 的硬判据保下来）。冷课**有记录**（`mode=online,pinned`）仍按记录派生（人在磁盘上表达过意图）。
- **★六轮 F6（authority 全组合定死；冷课 `!pinned` 与停课都进表）**：`pinned_offline` 要求 `pinned` **或**无记录；
  `auto` 要求 `course in _stores` ⇒ 冷课记录为 `{mode:online, pin:false}` / `{mode:offline, pin:false}`（今天开课在在线的常态）
  时按旧三分档**无档可落**；停课课（标记不在、记录 `mode=offline`）按三分档会落 `auto`，直接进 `/admin/queue` 读面会给出误导标签。
  **定死如下**（claim = 允许领租约；claimable = 清单行可领；handoff = `auto_handoff_allowed`）：

  | 场景 | authority | claim | claimable（有包 / 无包） | handoff |
  |---|---|---|---|---|
  | 在表 · 无记录 · 标记在 · mode=online | `auto` | 许 | true / true | true |
  | 在表 · `{online, pin:true}` | `pinned_online` | 409 `pinned_online` | false / false | false |
  | 在表 · `{online, pin:false}`（claim 翻过又交还自动） | `auto` | 许 | true / true | true |
  | 在表 · `{offline, pin:true}` | `pinned_offline` | 许 | true / false | false |
  | 在表 · `{offline, pin:false}`（claim 翻的，自动池） | `auto`（mode=offline） | 许 | true / true | true |
  | 冷课 · 无记录 | `pinned_offline` | 许（仅包在） | true / false | false |
  | 冷课 · `{online, pin:false}` | 按 mode=online ⇒ 不可领 | 409 `not_offline` | false / false | false |
  | 冷课 · `{online, pin:true}` | `pinned_online` | 409 `pinned_online` | false / false | false |
  | 冷课 · `{offline, *}` | `pinned_offline` | 许（仅包在） | true / false | false |
  | 停课（标记不在，任意记录） | `stopped`（正交维） | 409 `not_offline` | false / false | false |

  冷课（∉ `_stores`）`!pinned` 一律**按记录 mode**派生：online ⇒ 不可领、offline ⇒ 可领（均无导包能力）——
  与「无记录才退回按离线」合起来闭合 F6。**冷课 `authority_of` 的缺省短路（★六轮小项 7）**：冷课 `_dispatch_path`
  需要 `traj_root()`，单课程 `--job-root`（无 discover root）会 `ProtocolError` ⇒ 读盘失败一律退回缺省（无记录 ⇒
  `pinned_offline`），**不把 500 带进清单**。用例矩阵（§6.3）同步补齐 `online+pin=false` 两行。
- **★六轮 F2（`auto_eligible` 重定义后，busy 闸两消费点必须点名）**：`queue_offline.py:377-383`（`claim_offline` 里
  `auto = self.auto_eligible(course)` … `if auto: busy = self._busy_locked(course)`）与同文件清单段
  `busy = self.busy_reason(course) if auto else ""` 今天按 `auto_eligible` 门控——若照别名语义（= `auto_handoff_allowed`，
  `pinned_offline=False`）改，这两处会对 **pinned_offline 的 claim 静默跳过「一拖一」闸**、清单行也不再显示 busy，
  与 §3.4-4「authority 限制只作用于被检查方」矛盾（今天 pin 不参与 busy ⇒ 这是**静默回归**）。
  **逐调用点映射表定死**（实施按表改，禁止保留「随手读一个」的调用点）：

  | 调用点 | 新判据 |
  |---|---|
  | 清单 `auto_handoff` 字段 | `auto_handoff_allowed` |
  | 清单 `busy` 字段 / `claim_offline` 的 busy 门 | `is_runnable_offline` |
  | claim 归属门（`hub/offline.py`，含停课与冷课） | `is_runnable_offline`（+ `authority` 分流 `pinned_online`） |
  | `seize` / T2 翻模式 / `_claim_without_pack` 入口 / `_ask_console_freshness` | `auto_handoff_allowed` |
  | `offline_task_courses` 候选面 | 标记在（含 pinned_online 行照发；停课不列） |
  | `stall_verdict` 静音 | `authority == pinned_online` |

  补用例：A 在跑 X，B 领 **pinned_offline** 的 Y ⇒ 409 `busy`（今天会放行）。
- **★六轮 F3（停课行为变更，修洞）**：「停课 ⇒ claim 拒」**不是现状**：生产 `stopCourse` 推 `mode=offline`
  （`course-lifecycle.ts:487 → pushHubMode:227-243`，不带 pin），claim 门只在 `course in courses ∧ mode != offline
  ∧ ¬auto_eligible` 时 409 ⇒ `mode=offline` 直接放行，且清单 `claimable = pack_exists and offline`（有包即 true）、
  `claim_offline` 不看开课标记 ⇒ **停课 + 有包今天仍能被领走并整段跑**（既有用例只覆盖「停课后 mode=online」的形状）。
  **定死**：claim 门与 `claimable` 对「停课（开课标记不在）」一律 409 `not_offline` / `false`（`is_runnable_offline`
  收口，`offline_task_courses` 默认不列），标为**行为变更（修洞）**；补用例「stop（offline 记录 + 包在）⇒ claim 409
  ∧ 默认清单不列」+ 反探针。§3.2 表格「停课」行的「现状」字样已按此更正。
- **★六轮小项 5（pinned_online 与默认清单）**：`offline_tasks` **行照发**（默认清单里带 `claimable=false`、
  `seize=false`、`reason=pinned: 人固定在在线（交还自动后可领）`）——云机「空队列自解释」不该少这一档；
  T2 的 `resolve_courses` 空表断言不受影响（不抢与列不列是两件事）。
- `offline_tasks` 行新增 `authority`（读面；控制台与云机都可据它排障）；`seize` 用 `authority == auto`。
- **登记即同步**（R3-a）：`queue_scope.add_course` 末尾调 `_sync_parked(c)`（一行）——发现/重启路径与
  构造路径同口径；守卫用例钉「discover 一门带 offline 记录的课 ⇒ `st.parked is True`」。
- **冷课也读记录**（R3-c）：不在课程表的课，authority 从盘上 `offline-dispatch.json` 派生（有记录按记录；
  无记录才退回旧默认「离线」）——`offline_tasks` / `_get_task_pack` / `_post_offline_lease` 三处统一走它。
- ★评审修订（P0-2）：authority 必须有**读面出口**，否则 §3.10 的 dashboard 拿不到输入。既有调用点至少三处
  仍直读 `auto_eligible`（`queue_offline.offline_task_courses:206` · `hub/offline.py:399` · `hub/offline.py:530`）
  ⇒ `authority` 除进 `offline_tasks` 行外，**还要进 `/admin/queue` 的每课行**（`queue_observe.queue_state`，
  dashboard 的 `course-overview.ts` 正是从它建行）——见 §5 P0-11。
- ★评审修订（P0-2）：`authority_of` 是 `queue_offline` 混入的方法，`queue_state` 在 `queue_observe` 混入——
  两者是同一组合类的兄弟簇（`self.authority_of(...)` 可用），但**形参面必须由 `QueuePeer` 声明**（P0-7）。

### 3.2 状态转换表（事件 → 动作，全部落在 P0/P1）

| 事件 | 权威变化 | hub 动作 | 训练侧动作 |
|---|---|---|---|
| 开课（未选模式） | `auto, mode=online` | 无（就绪） | 正常入队 |
| 自动 claim（有包） | `auto → auto(offline)` | 建租约；翻 mode；撤未认领 job；记 `claimed_offline/by/at` | 下一轮边界见 `run` ⇒ 等待（§3.5） |
| 自动 claim（无包） | 同上 | 翻 mode + 触发控制台导包（409 pending_export）；**刷新 `claimed_at` 且写 `claimed_by`**（R2-d）；★五轮 P0-B + ★六轮 F4：**新一轮交接 ⇒ 重置导包触发账本**——「新一轮」= 换 `worker_id` **∨ 距上次 claim 超 `AUTO_HANDOFF_PENDING_SEC`**（`.worker-id` 持久在 `<work>/.worker-id`，同机重开会话沿用同一 id ⇒ 只判换主会漏「同主回来」，照吃前任烧满的 `give_up`，`offline_boot.py:1351-1356`） | 同上 |
| 人切离线（pin=1） | `pinned_offline` | `mode=offline`；`claimed_offline=false`；`drop_jobs` 生效；**不撤租约** | 同上 |
| 人切在线（pin=1） | `pinned_online` | `mode=online`；**撤销租约**（§3.3）；清 `claimed_offline/by/at/flipped_at`；**不撤单**（§3.6） | 等待中的课自动续跑（§3.5） |
| 交还自动（pin=0） | `auto` | `mode=online`；同撤销与清理；不撤单 | 同上 |
| 停课（删标记） | `stopped` | 不新发租约（409 `not_offline`；★六轮 F3：**行为变更**——今天 `mode=offline` 的停课仍可被领走）；在跑租约**不杀**（训练不因控制面停摆） | 该课在下次空闲拍退出队列（`enabled_courses` 不再含它） |
| 租约失联（> `OFFLINE_LEASE_STALE_SEC`） | 不变 | 清单标 stale + claimable；claim 自动接管（记日志）；busy 不再算它 | —（新盘照常 claim） |
| 租约过期（TTL） | 不变（mode 留 offline = U3 waiting） | 惰性清；可再领 | — |
| 段末跑满（`end_it_reached`） | 不变 | `completed_pack_sha` 落盘；不可再领（重导包 sha 变解封） | — |
| 心跳收到 `revoked` | 不变 | — | **停止再 claim；当前段在下一个轮边界收尾**（P1；旧 worker 照旧跑完，兼容降级） |

### 3.3 离线租约围栏（`beat_at` + `revoked` tombstone）

租约记录（`_leases[course]`）加两个字段：

```python
{ token, worker_id, at, expires_at,
  beat_at: float,        # 最近一次心跳（claim 时 = at）
  revoked: bool }        # 被人切在线/交还自动撤销的墓碑
```

**`lease_verdict` 扩展为六态**（`hub/task_pack.py`，**叶子纯函数，签名不变** `(now, rec, worker_id)`）：

| 顺序 | 条件 | verdict | claim | heartbeat | release |
|---|---|---|---|---|---|
| 1 | 无记录 / `expires_at<=now` | `expired` | 允许 | 409 `expired` | 幂等成功 |
| 2 | `rec["revoked"]` 为真 | `revoked` | 允许（新主） | 409 `revoked` | 200（清墓碑） |
| 3 | `worker_id` 相同 | `mine` | 续上 | 200 | 200 |
| 4 | `now - beat_at > OFFLINE_LEASE_STALE_SEC` | `stale` | **自动接管**（不需要 `takeover=1`；日志一行 `offline-lease-reclaim`） | 409 `taken`（旧主） | 409 foreign |
| 5 | 其余 | `foreign` | 409 `held` | 200 | 409 |

★评审修订（P0-1，**原表第 2 行「`revoked` 且 token 匹配」不可实现**）：`lease_verdict` 拿不到 token
（签名只有 `worker_id`，`hub/task_pack.py:234`），而墓碑 `worker_id` **就是老主** ⇒ 若按「token/身份匹配」判，
**新主永远命中不了 `revoked`**，会落到第 4/5 行（`stale`/`foreign`），再被 `claim_offline` 的
`foreign and not takeover → return foreign`（`queue_offline.py:385`）拒掉 ⇒「新主可领」当场落空。
**定案：判据 = `rec["revoked"]` 为真（不看任何身份）**——墓碑是**全局否决**，谁看到都算 `revoked`；
token 只在 `claim_offline`/`heartbeat_offline` 内部用于**决定建新租约还是回 409 文案**（那两处本来就有 token）。
- **顺序理由（必须写进代码注释）**：`revoked` 排在 `mine` **之前** ⇒ 老主心跳拿 `revoked`（而不是续上）；
  `mine` 排在 `stale` **之前** ⇒ 同一个 worker 回来续领自己的（可能静默过的）租约时判 `mine` 续上，
  **不被判成 stale 而被自己 409**（否则 cell 中断重跑会被自己的墓碑/静默挡在门外）。
- `heartbeat_offline` 的实现顺序：先算 verdict，`revoked` ⇒ 409 `revoked`（**不论 token**）；
  否则维持既有 token 比对（`queue_offline.py:423` 的 `taken`/`expired`）——token 仍是「证明你是持有人」的凭据。
  ★六轮小项 4：心跳**必须同时刷新 `beat_at`**（不只 `expires_at`）——只续到期会把持续心跳的活主
  判成 `stale`（stale 腿读 `beat_at`）。P0-1/P0-2 验收两侧都钉：写入（心跳后 `beat_at≈now`）与判据
  （持续心跳的主永不被判 stale）。
- ★六轮小项 6（**墓碑下的 release token 语义**）：release **仍需 token**（与既有 `release_offline` 同规）——
  老主持 token ⇒ 200 并清墓碑；token 不符/缺失 ⇒ 409 `foreign`（`revoked` 不豁免；新主走 claim
  覆盖墓碑，不需要 release）。用例补「墓碑 + 错误 token ⇒ 409」「墓碑 + 原 token ⇒ 200 ∧ 记录清」。

- 新常量：`OFFLINE_LEASE_STALE_SEC = 180.0`（= 3×心跳 60s，与 job 侧 `ORPHAN_GRACE_SEC` 同值同理由），
  住 `hub/queue_offline.py`（**纯 hub 侧，不进 `common/protocol.py`**）；env 覆盖 `BCITY_OFFLINE_LEASE_STALE_SEC`（e2e 调秒级）。
  迁就旧记录：无 `beat_at` ⇒ 用 `at`（语义不变）。
  ★评审修订（五轮 §4-5）：它与 job 侧 `ORPHAN_GRACE_SEC` **同值不同义**（job 侧 = claim 后**零心跳**；
  离线段 = **连续静默**）——必须把 §8 那条「照抄 job 侧实现被否决」的理由**抄进常量旁的注释**，
  否则下一个人会照抄 `_lease_state` 的实现（那正是 §8 想避免的）。
- `revoke_offline_lease(course, reason)` 新助手：置 `revoked=True`（保留 token/beat_at），
  `set_mode_pinned(→online)`（pin=1 或 0）与 `unsetCourseMode` 路径都调它；下一次成功 claim 覆盖墓碑。
  ★评审修订（P1-6，**收回「唯一通知通道」的表述**）：墓碑**只对新 worker 有效**——`remote/` 全目录
  `revoked` 零匹配，`_beat` 对 409 只按 `expired` 二分且一律 `return`（`offline_boot.py:1402-1412`，停心跳不停训练）
  ⇒ 旧云机连字段都不读。保留记录而不是删除的真正理由 = **新 worker 下一次心跳拿到 409 `revoked` 并在轮边界收尾**（§3.8 同款）；
  旧 worker 的行为由 §3.8/§9 如实记录，不夸口。
- ★评审修订（P0-1 配套，**字段必须落得下盘**）：`beat_at`/`revoked` 是 `_leases`（进程内）字段，
  与 `<课>/offline-dispatch.json` 无关——**只需**改 `queue_offline` 的租约读写；`dispatch_record_default`/`dispatch_record_merge`
  的宽容归一（`queue_offline.py:94-115`，未知字段被丢弃）**不动**，`DISPATCH_VERSION` **不 bump**（§3.0-5 的「不新增事实源」）。
  若实施中有人把 revoked 落进 dispatch，那是**第二个事实源**，禁止。
- `heartbeat_offline` 按表返回 `revoked`；`claim_offline` 按表允许自动接管 stale/revoked（`takeover` 参数保留，降级为人工兜底语义）。
- 竞态收尾（必须做）：`claim_offline` 在持锁时读一次 authority、`note_claim` 再在 dispatch 锁下判一次——
  若 claim 在「读 authority 之后、切换之前」赢锁，`note_claim` 发现 `pinned_online` ⇒ **不翻模式**并立刻
  `revoke_offline_lease`（把刚建的租约标记撤销）。锁序照**既有**嵌套方向（`_lease_lock → _dispatch_lock`，
  `_busy_locked` 就是这么读记录的）；`revoke_offline_lease` 只在**不持任何锁**处调用，禁止反向嵌套。
  ★评审修订（五轮 §4-1，**契约写死；不同意「改回 409」的备选**）：两种到达顺序（人在 `note_claim` **前/后**切换）
  的**终态完全一致** = `mode=online ∧ pinned=online ∧ 租约成墓碑`（人先切 ⇒ `claim_offline` 持锁读到 `pinned_online`
  直接 409；人后切 ⇒ `note_claim` 不翻 + 撤租约）。故**契约 = 「竞态输掉的 claim 返回 200 + 一个已是墓碑的 token；
  新 worker 下一次心跳拿 409 `revoked` 并在轮边界收尾（§3.8）；旧 worker 照旧跑完」**——需要**撤回 `note_claim`
  已写的 dispatch 字段**才能改回 409，而那个撤回是**新增回滚路径**（比现状更易出错），收益只是「省掉一次白 claim」。
  被否决备选记入 §8。
- **撤销后回传的处置**（R3-f）：`pinned_online` 期间到达的 `/offline/artifact` 只落 `offline/<run_id>/`
  与离线账（供导入参考），**不推进活动权重**（`_land_offline_round_extras` ② 加 authority 门）；
  `/offline/result` 的 `end_it_reached` 必须用 **body 已带的 `run_id`/`plan_sha256`** 与**当前包**（索引里同字段）
  比对相符才置 `completed_pack_sha`——否则只记一行「旧包段末到岸（不盖章）」（字段已具，加的是 hub 侧校验）。

### 3.4 busy 闸（一拖一的活性口径）

★六轮 F2：本节的 busy 判据在两处消费（`claim_offline` 的 busy 门 + 清单行的 `busy` 字段），都改用
**`is_runnable_offline`**（不是 `auto_handoff_allowed`）——pinned_offline 必须照样吃「一拖一」；
只按「自动可达」门控会把 pin 离线课静默漏掉。

`_busy_locked(course)` 的两条腿改为「只算有活性的东西」：

1. **别的课的租约**：仅当 verdict ∈ `{mine, foreign}`（即未过期 ∧ 未 stale ∧ 未 revoked）才算忙；
   stale/revoked/expired 一律不算（死盘不再冻结全池——R2-b 的修法）。
2. **别的课在交接窗口**：锚点从 `flipped_at` 改为 **`claimed_at`**（每次 pending_export claim 由 `begin_auto_handoff`
   刷新，R2-d——它现在**不写** `claimed_at`，本 plan 新增这一步），且 `claimed_by` 非空、
   `now - claimed_at <= AUTO_HANDOFF_PENDING_SEC`；锚点超窗 ⇒ 不算忙
   （course 行列 `claimable=true`，新盘可重新触发导包；停滞告警面继续兜底）。
   ★评审修订（P2-2）：`queue_offline.py:624-626` 有一条**刻意的反面注释**（「重复 claim **不**刷新 `flipped_at`：
   它是停滞告警与 busy 窗口的锚点」）——改锚点后**两个判据时间轴分叉**：busy 用 `claimed_at`（会被刷新），
   `stall_verdict` 的 `pending-export` **仍锚 `flipped_at`**（`queue_offline.py:750`，不刷新）。这是**有意**的
   （活跃重试不触发停滞告警、但也不该把死盘一直算作忙）——必须把那条注释同步改写成「`flipped_at` 服务停滞告警、
   `claimed_at` 服务 busy 窗口」，否则下一个人会把两处「对齐」回一个锚点。
3. 两条腿都只在 `authority != pinned_online` 的课上生效（pinned-online 不占闸）。
4. ★评审修订（灰区①，**补一个此前没写的判据**）：上面的 `authority` 限制作用在**被检查方**（`other`）还是
   **发起方**（`course`）——**定义：只作用于被检查方**。即：`pinned_online` 的课**不占别人的闸**（不进 busy 的
   候选集），但它**仍然会被别人的交接窗口挡住**（发起方不受 authority 限制——`pinned_online` 的课压根不该来 claim，
   它在 §3.1 就被 409 了，所以「被挡」这条实际到不了）。**实现约束**：`_busy_locked` 的两条腿遍历 `other` 时先判
   `authority_of(other) != pinned_online`，对自己的 `course` 参数不做 authority 判断。

### 3.5 训练侧复原（离线 = 等待，不是收官）

- **★评审修订（P0-3，前置条件，必须写进实施清单）**：「模式写回后自动续跑」成立的前提是
  **trainer 以 `--rollout-src auto`（或缺省，缺省即 `auto`）启动**——`_rollout_source(args)` 的第一段
  `if mode and mode != "auto": return mode`（`trainer/loop_transport.py:108-114`）会让 CLI 显式值**短路掉 rl-config**。
  已核实：`--rollout-src` 的 argparse 缺省 = `"auto"`（`worker/cli.py:537`），且启动器（`dashboard/src/launch/cli.ts`
  → `trainer/run_rl_cluster.py`）**不传**该参数 ⇒ 当前成立，但这是**隐式依赖**。实施时给一条守卫：
  `run_rl`/`loop_serve` 启动期断言（或一行 `log`）「offline 复原要求 `rollout_src == auto`，实测 <值>」；
  验收加「显式 `--rollout-src run` 启动时，plan 的自动复原**声明为不支持**（明说，不静默失败）」。
  ★评审修订（五轮 §4-4，两处细化）：① 守卫**落 `_rollout_source` 自己**（`loop_transport.py:108-114`
  已有「非法值 `raise SystemExit`」的先例），不放 run_rl/loop_serve 启动期——后者离判据太远；
  ② **续跑还同时依赖 `courses.<课>.run_iters` 被删**：`resolve_collect_mode` 是 `if source == "run" or seg:`
  （`worker/loop_round.py:107`）⇒ **`run_iters` 留着也判离线**（`source` 写回 `local` 也没用）。
  `train-mode.ts:88` 的 `delete row.run_iters` 就是这一步；计划没写这条依赖 ⇒ P1-1 验收加一条
  「`courses.<课>` 里 `run_iters` 仍在 ⇒ 声明为不支持（离线词不撤）」。
- `loop_runner._map_outcome`：`ROUND_OFFLINE_EXIT` → **`waiting(self.now() + self.poll_interval, detail, hold=False, jid=..., round=str(out.it))`**
  （与既有 `ROUND_WAIT` 分支同形，`trainer/loop_runner.py:291-296`：`waiting` 已带 `hold`/`jid`/`round` 形参，**不需要新构造器**），
  reason = 「离线课由云机取任务包接手；切回在线自动恢复」，不再 `done(final=True)`。
  队列保留该轮任务，调度器每拍重问；`ctx.collect_mode` 每轮由 `resolve_collect_mode(src, ctx.seg)` 重算
  （`trainer/loop_round_steps.py:261`）⇒ rl-config 写回后下一拍即离开 `COLLECT_OFFLINE`，**自动续跑**（15s 量级，无需停开课）。
- `loop_lifecycle.run`（单课程入口）：`ROUND_OFFLINE_EXIT` 从 `return` 改为「退避后重问同一轮」（`it -= 1` 语义同 `ROUND_WAIT`）。
- `_settle_rounds`：**不再**把离线退出的课当 `QUEUE_DONE` 收官 ⇒ 不写假 `run_complete`、不发云机 PAUSE（R1-e 消失）；
  真跑满（budget/门）路径逐字节不变。
- 日志：离线等待**每个模式状态只喊一次**（记 `last_verdict`，避免 15s 一次刷屏）。
  ★评审修订（五轮 P1-D）：**抑制点不在 `loop_runner.py`**——每拍那行四句日志在
  `trainer/loop_round_steps.py:272-284`（`step_course_iter` 的 `COLLECT_OFFLINE` 分支），改成 waiting 后
  它每 `poll_interval` 重放一次 ⇒ **比今天更吵**。抑制要么做在那里（`CourseRuntime` 上记 `last_offline_note`），
  要么把这段日志整体搬进 `_map_outcome` 的 waiting 分支。P1-1 的文件列**必须加 `trainer/loop_round_steps.py`**。
- 控制台读面：等待中的课显示「离线（云机接手）」，不是「已收官」；`waiting_state` 文案补一档（`loop_plan.py`）。
- **清单预算**（R3-g）：`resolve_courses` 把「有活、但当前被**非自己**的有效租约持有」的行一并带回
  （新增 `blocked: [{course, holder, expires_in}]`），`_run_auto` 视其为**中间态**（不占 `idle_wait_sec`、
  退避再问）——「空队列」与「还得等一会儿」从此在预算上分开。
  ★评审修订（五轮 P1-E，**签名与调用点必须写死**）：`resolve_courses` 现在的返回类型是 `list[dict]`
  （每个元素 = 一门要跑的课），**加一个并列通道必须改签名**（`(picks, blocked)` 元组或 out-param）；
  调用点 = `_run_auto:1744` 与 `run_one_course`。且 `_run_auto` 现有那条「不占 idle 预算」的分支
  （`offline_boot.py:1770-1778`）在 **`if tasks:` 内部**，而 R3-g 的场景是 `tasks==[]`
  （`resolve_courses:1529-1532` 对 held 行 `return []`）⇒ **必须在 `if tasks:` 之外新增分支**
  「`tasks` 空但 `blocked` 非空 ⇒ 退避再问、不累 `idle_since`」。**注意既有 `_run_batch` 的 `blockers`
  是另一件事**（`leases["blockers"]`，只服务 tasks 非空时的交接中间态）——同名不同物，别接错线。

### 3.6 队列完整性（撤单与再发布）

- **撤单只在「进入离线」**：`set_mode_pinned` 的 `drop_jobs` 收敛为 `m == offline` 才生效；
  切在线/交还自动**不撤**（停在队首的 iter/ppo 正是回来后要领的活——这也让
  `e2e/test_multi_course_single_hub_e2e.py::test_offline_course_is_parked_and_resumes_on_going_online`
  的语义从「同一份活立刻被推走」升级为「不必重发」）。自动交接两条腿（claim 翻 offline）保持撤。
- **再发布 = 新纪元（★六轮 F1 更正修法落点）**：生产 republish **不走 hub 的 `publish`**——`store_ledger.publish`
  全仓零生产调用（仅 `e2e/test_auto_handoff_e2e.py:150` / `e2e/test_worker_name_ledger_e2e.py:68` 两个测试写者）；
  产线是 `remote/hub_client.publish_job`（磁盘 IPC；调用点 `trainer/loop_remote_job.py:301`、`trainer/bc_loop.py:352`），
  它在 `register=True` 时**无条件追加** `job_pending`（`hub_client.py:1058-1075`；注释说幂等去重，代码没有守卫）
  ⇒ 对生产路径，账本净态本来就是 pending（R3-b 的「账本折叠仍判撤单」在生产路径上**不成立**）。
  真正的生产阻塞 = hub 内存 `_cancelled`（`store_leases.py:341` 的 `_claim_locked` 即拒；只由
  `cancel_unsettled_jobs` 添加、**只在 hub 重启时清**）：trainer 换进程直写盘，hub 收不到复活信号
  ⇒「账本 pending（peek 可见）∧ claim 永远 `cancelled`」的撒谎形状原样存在。
  **修法 A（定案）= hub 侧按账本净态对账**：
  ① `_claim_locked` 命中 `_cancelled` 时折叠该 jid 的**最后一次**事件——为 `job_pending` ⇒ `discard` 并放行
     （同一把 `_lock` 下；真相源仍是账本），为 `job_cancelled`/`job_completed` ⇒ 照旧拒。
  ② `store_ledger.publish` 按净态去重（`pending_ids` 集合差的口径改成「最后一次事件」）+ `discard` 即时闸——
     防 e2e/测试写者路径与 ① 漂开；幂等语义保持（已有净态 pending/completed 不追加）。
  ③ **用例必须用生产写者构造**：T1③ / `test_republish_after_cancel_revives_job` 走
     `remote.hub_client.publish_job`（磁盘 IPC；或「写 pending 行 + 预置 hub `_cancelled`」），配反探针
     「去掉对账 ⇒ 红」。只按 hub `publish` 写会绿着骗人（只覆盖测试写者）。
  ④ 读面代价收窄（★六轮 F1b）：`worker/bc_ledger.inflight_jobs`（`:112-113`）的**集合差**对「同 jid 后补
     pending」不敏感（读取者正是 `trainer/loop_plan.py:93`）⇒ **改净态折叠**这一支；不透「BC 腿不走
     `publish` 复活」的写死假设（生产 republish 当下就会产生晚于旧 `job_cancelled` 的 `job_pending`）。
  ⑤ P2-3 的旧修法（`publish` 内部 `pending_ids` 永远为假）仍然对：那一行的去重口径换成净态折叠。
- **复活事件的读面代价（★评审修订，五轮 P1-H：点名，不再写「逐个清查」）**：账本里同一 jid 会出现两条
  `job_pending`（净态语义）——凡按事件计数的读方一律以**净态/去重**为准。已核对 `_read_ledger` 全部读方：

  | 读方 | 折叠方式 | 复活后 |
  |---|---|---|
  | `store_ledger.claimable_job_ids`（`:117-125`） | 文件序、净态 | ✔ 无需改 |
  | `store_ledger.cancel_unsettled_jobs`（`:167-169`） | 走 `claimable_job_ids` | ✔ |
  | `hub/queue_observe.queue_state` 的 `pending_n`（`:113-114`） | 走 `claimable_job_ids` | ✔ |
  | `hub/store_offline._ledger_has_newer_iter`（`:306`） | 只认 `iteration`/`offline_artifact` | ✔ |
  | `remote/hub_client.cancel_stale_jobs`（`:1164-1204`） | 按 jid 折叠 terminal | ✔ |
  | **`worker/bc_ledger.inflight_jobs`（`:112-113`）** | **集合差** `pending ∖ (done ∪ terminal)`（`scan_jobs:89-104`） | ⚠ **会撒谎**：复活的 jid 同时落在 `pending` 与 `terminal`（旧 `job_cancelled`）⇒ 被判「已收口」⇒ BC 课的在飞面漏报 |

  ⇒ **唯一风险点 = `bc_ledger.inflight_jobs`**（以及共用 `scan_jobs` 的 `completed_jobs`）。实施时**必须**：
  要么把它改成与 `claimable_job_ids` 同口径的**文件序净态折叠**，要么明确「BC 腿的 job 不走 `publish` 复活」
  并把这条假设**写进注释 + 一条用例**（`store_ledger.publish` 与 BC 的 `publish_job` 是否同一路径，
  实施前先 grep 坐实，**不许默认**）。
- 账本侧不动（`cancel_stale_jobs` 的既有语义不变）。

### 3.7 观测面与控制台

- `GET /offline/tasks` 行增：`authority`；`holder.silent_sec` / `holder.stale`；
  `reason` 文案新增 `pinned: 人固定在在线（交还自动后可领）`、`held-stale: <id>（静默 Ns，可直接接管）`、
  `held-revoked: <id>（已被撤销，可直接接管）`。
- ★六轮 F5（**墓碑 holder 形状定死**）：`holder_info` **返回**墓碑（带 `revoked=True` + `silent_sec`/`stale`）
  ——清单才能渲染 `held-revoked`、`holder_revoked` 形参才是真消费；`offline_leases()` 面向 `/admin/offline`
  **保留 revoked 条目**（带全字段，便于排障），不做静默删除。补「墓碑行在清单里的 reason/claimable 形状」用例
  （claimable=true；墓碑下新 worker 可领）。
- ★六轮小项 5：pinned_online 行**照发**默认清单（`claimable=false`、`seize=false`、`reason=pinned: ...`）——
  云机「空队列自解释」不该少这一档；`resolve_courses` 不抢即可（T2 不受影响）。
- `GET /admin/offline`：每课 `authority` + 租约 `beat_at/age/stale/revoked`（统一从 `offline_leases()` 读）。
- hub 日志新增三行（各只喊一次）：`offline-claim`（已有，加 authority）、`offline-revoke`、`offline-lease-reclaim`。
- 控制台：`setCourseMode('online')` 文案改为「已固定在线：离线盘不再抢它（要交回自动池点『交还自动』）」；
  `unsetCourseMode` 文案改为「已交还自动：离线盘可再次接管」；离线行显示 stale-holder 徽标。注释里三处
  「pin 不再拦」按 §4.2 更正。
- 停滞告警（R3-e）：`stall_verdict` 的 `pinned` 静音**只保留 `pinned_online`**；`pinned_offline` 与人管过的离线课
  正常告警（传入 `authority` 替代裸 `pinned`）。

### 3.8 兼容矩阵

| 组合 | 行为 |
|---|---|
| 新 hub × 旧 worker | 旧 worker `held` 时照旧跑（不改旧码）；stale 接管发生在 hub 侧，旧 worker 心跳拿 409 `taken` 后续跑完（无 corruption，重复由首写幂等丢弃） |
| 旧 hub × 新 worker | 新 reason（`pinned_online`/`revoked`/stale）缺席 ⇒ `claim_course` 落到旧映射（held → 照旧跑）；`resolve_courses` 对无 `authority`/`stale` 字段的清单退回旧口径（逐字段缺省） |
| 旧 `dispatch.json`（v1） | `authority_of` 从既有 `pinned`+`mode` 派生，零迁移 |
| 旧 e2e/用例 | 见 §6.5 的语义改写清单 |

### 3.9 意图与冷课的补齐（R3-c/R3-d/R3-h）

- **意图表 v2**：`console-state.courseModes` 值由裸字符串升级为 `{mode, pinned}`（旧值读取时按
  `{mode, pinned:false}` 归一，写入一律 v2）。那颗开关（`setCourseMode`）写 `pinned:true`；
  开课弹窗的显式选择与停课维持 `pinned:false`（F9 语义不动）。
- **回灌带 pin**：`restoreCourseModes` 对 `pinned:true` 的条目用 `pin=1` 推（这才推得动被 claim 过的在线意图，
  R3-d）；`pinned:false` 保持 legacy 推送。旧条目不会被静默升级——F9 之前「每门开过的课都被写过 online」，
  一次性把噪声 pin 上会关掉整套自动交接；要固定请再点一次那颗开关（回执里写明）。
- **冷课 authority**（R3-c）：见 §3.1 第二条 + ★五轮 P1-C 的定死口径（**冷课无记录 ⇒ `pinned_offline`；
  `auto` 只对 `course in _stores` 成立**）；清单行照发 `authority`，未登记课的行由记录/缺省派生。
  守卫用例：冷课（有标记、无记录、无包）**不可领**（今天逐字相同），且 `auto_handoff_allowed=False`。
- **★评审修订（P1-7，迁移爆炸半径——必须在 P0 做完盘点）**：authority 派生自 **hub 盘上**
  `dispatch_record.pinned`（`queue_offline.py:512-514`），**不是**控制台意图表。而 2026-10-03 语义下
  `setCourseMode('online')` 写的就是 `pin=1`（`dashboard/src/server/actions/course-mode.ts:260`
  `pushCourseMode(c, m, { pin: true, dropJobs: true })`）⇒ 本 plan 上线当天，**所有被人手动切过在线的课**
  都会从「可被抢」变成 `pinned_online`（离线盘永久不可抢）。这正是 §4.2 的目标，但必须**先量化**：
  P0 实施前跑一次 `grep -l '"pinned": true' tmp/*/offline-dispatch.json` 给出受影响课表，写进 DECISIONS 新条
  （「升级即生效：这批课本就应锁定；若属误 pin，操作员用『交还自动』解锁」）。**不引入自动迁移**（会把
  `pinned` 的语义偷偷改掉）。
- **★六轮 F4（「新一轮交接」定死，P0-5）**：触发账本的重置判据 = **换 `worker_id`（≠ 记录的 `claimed_by`）∨ 距上次
  claim 超 `AUTO_HANDOFF_PENDING_SEC`**（或给账本加时间衰减）——`.worker-id` 持久在 `<work>/.worker-id`，同一台机
  重开会话沿用同一 id，只判换主会漏「同主回来」：A 在导包窗口重试满 3 次 → 重启会话 → 照吃 `give_up`
  （`remote/offline_boot.py:1351-1356` → skip）。T5b 旁补「同主重试」用例；若有意保留同主不回血，在 §9 写明理由。
- **★六轮小项 7（冷课 `_dispatch_path` 兜底，P0-9）**：冷课 `authority_of` 读盘失败（单课程 `--job-root` 无
  `traj_root()` ⇒ `ProtocolError`）⇒ 退回缺省（无记录 ⇒ `pinned_offline`），不把 500 带进清单。
- **暂停与自动接管**（R3-h）：二选一（推荐 A，落 DECISIONS）——
  A. **暂停不拦接管**（hub 不读控制台文件，保持单向依赖）：接管回执与面板写明「该课处于暂停，离线盘已接手；
     恢复本地训练请先切回在线或停课」；
  B. 暂停也拦（`auto_eligible` 增读 `loop-control.json`）：语义更直觉，但引入 hub→控制台文件耦合与旧版本兼容问题。
  推荐 A 的理由：停课已是唯一 opt-out（用户口径），暂停是本地调度旋钮。

### 3.10 首页课程区读面（单一派生 + 来源/新鲜度 + 不消失，R4-a…R4-g）

1. **单一派生 `courseStatus(course)`**（`dashboard/src/web/view/course-status.ts` 新纯函数，注册进 `web/view` 桶）：
   输入 = §3.1 的 authority + 租约/stale（hub 行）+ waiting（训练侧行）+ trainer 存活 + 开课标记 + 意图/rollout_src；
   输出 = `{state, source, stale, title}`。**pill 与矩阵行读同一个输出**（渲染器只决定「词怎么说」，不决定「是什么」）
   ——`coursePills`/`matrixStatus` 的优先级表收编进它，禁止再各自拼源；守卫用例：同屏两侧词不得互相矛盾（R4-a）。
   ★评审修订（P0-2，**authority 的入口**）：dashboard 每课程 hub 事实的唯一来源是 `/admin/queue`
   （`server/api/overview.ts:117` → `probeHubAdmin`；行由 `course-overview.ts:421` 的
   `input.queue?.courses[course]` 建），而 `queue_state` 的每课行**现在只有 `mode`**（`hub/queue_observe.py::queue_state`）。
   ⇒ 本 plan 必须让 `queue_state` 的每课行**带上 `authority`（与 `pinned`）**（P0-11），`courseStatus` 从
   `CourseRow` 读它；`/admin/offline` 的租约字段（beat_at/stale/revoked）走既有 `offline` 通道（P0-11 同批）。
   **不得**让 `courseStatus` 去猜 authority（那是第二事实源）。
2. **来源与新鲜度显式**：每个事实带 `source`（marker/registry/loop/hub/intent/config）与 `stale:{since,reason}`；
   规则：**「未知」不得渲染成否定**——hub 探针失败 ⇒ `hubSeen/offline` 是 `unknown` 而不是 false（`hubOnline`
   门扩到全部判据），训练侧空行 ⇒「未知」而不是「视图不可用/空闲」二选一（R4-b/g）。
3. **不消失语义（last-known-good）**：`hubCache`/`loopQueue` 的**失败结果不替换旧值**——成功才回填，失败保旧值 +
   打 `stale` 标；`matrixMeta` 的「显示缓存」文案改为真实（旧行确实在）。陈旧窗口上限 = 一个刷新周期，
   超过则显示「未知（读面失败 Ns）」，不无限保旧（R4-e）。
4. **滞回**：词的切换只在**派生值变化**时发生；来源可用性变化（hub 探测失败/恢复）只改 `stale` 标注、不改词
   ——「回传中 → 已收官」这类跨语义跳字彻底消失；动作后的即时意图/回执仍先上屏（现读），其余事实保留上一拍
   并标注「重算中」（≤TTL）（R4-b/c/d）。
5. **持久错误态随根因修复**：假收官（§3.5 后离线课走 waiting 词「离线（云机接手）」）、意图未生效（§3.9 回灌带 pin）、
   冷课分叉（§3.1 冷课 authority）——读面不再为它们加特判（R4-f）。
6. 读面口径进 `docs/nn/console.md`（词表来源、未知语义、陈旧标注规则），与 §22 的教训同文档留存。

---

## 4. 行为变更与既有决策的关系

### 4.1 变更清单（写 DECISIONS 时逐条落）

1. `pinned ∧ mode=online` ⇒ **离线盘不可 claim/seize/翻模式**（pin 重新获得一处阻止力）。
2. 切在线/交还自动 ⇒ **撤销离线租约**（墓碑 + 心跳 409 `revoked`）。
3. 离线租约新增**失联早收**（静默 180s 自动可接管）与 **busy 活性口径**。
4. 切在线 **不撤单**；`_cancelled` 即时闸按**账本净态**对账（★六轮 F1：生产 republish 走
   `remote/hub_client.publish_job`，hub 侧在 `_claim_locked` 折叠净态——不再靠零调用的 `publish`）。
5. 训练侧：离线 = 等待（可自动复原），不写假 `run_complete`。
6. `add_course`/发现路径同步 `parked`（R3-a；纯 bugfix，无需决策）。
7. 再发布 = 复活**落账本**（按净态追加 `job_pending`，不只是一张内存 set；R3-b）。
8. 冷课读盘上派发记录派生 authority（在线/pin 意图在冷课面也生效；R3-c）。
9. `pinned_online` 期间的回传不推进活动权重；`completed_pack_sha` 需包身份相符才盖章（R3-f）。
10. 意图表 v2（`{mode, pinned}`）+ 回灌带 pin；旧条目按 legacy 处理（R3-d）。
11. 停滞告警只静音 `pinned_online`（R3-e）；清单 held 行进 blocker 预算（R3-g）。
12. 首页课程区状态**单一派生**（`courseStatus`），pill 与矩阵同源；来源/新鲜度显式化（「未知 ≠ 否定」，R4-a/g）。
13. 读面失败**保留上一拍值**并标注 stale（不再以空行替换），陈旧窗口 ≤ 一个刷新周期（R4-e）。
14. 词切换以**派生值变化**为准（来源可用性变化只降级标注、不换词；R4-b/c/d）。
15. ★评审 P0-1：`lease_verdict` 的 `revoked` **按 `rec["revoked"]` 判、不看身份**（签名不变）；顺序
    expired→revoked→mine→stale→foreign；token 只在 `claim_offline`/`heartbeat_offline` 内部用于分流。
16. ★评审 P0-2：`authority`（+`pinned`）必须进 `/admin/queue` 每课行、租约 `stale/revoked` 进 `/admin/offline`
    （P0-11）——dashboard 读面（§3.10）**不猜** authority。
17. ★评审 P0-3/P1-7：训练侧复原的前提 = trainer 以 `--rollout-src auto` 启动（加守卫/日志）
    ∧ `courses.<课>.run_iters` 被删；`pinned=true` 的存量课在上线当天转入 `pinned_online`，须先盘点并记 DECISIONS。
18. ★五轮 P0-A：`auto_claimable` 加 `holder_stale`/`holder_revoked`（带默认值）⇒ stale/墓碑行**可领**。
19. ★五轮 P0-B：导包触发账本**换主即重置**（`begin_auto_handoff` 见新 `worker_id` ⇒ `reset_auto_handoff_triggers`）
    ⇒ 新盘不再吃到前任烧满的 `give_up`。
20. ★五轮 P1-C：`auto` 档额外要求 `course in _stores`；冷课无记录 = `pinned_offline`（不扩张无包触发导包的能力）。
21. ★五轮 P1-H + ★六轮 F1b：`worker/bc_ledger.inflight_jobs`（集合差口径）是「净态复活」的唯一风险读方——**定死改净态折叠**（不留「BC 腿不走 `publish` 复活」的写死假设）。

### 4.2 为什么这次要回摆 pin（不是翻烧饼）

2026-10-03 §59 的裁决是「pin 不再拦离线盘」，背景是**当时**云机上线后 `GET /offline/tasks` 持续 0 条
（用户手动设 online pin=1 是当时唯一的「让课继续训」的状态表达）。今天环境已经变了：

- 控制台已有**三态意图表**（`unset`/`online`/`offline`）与「交还自动池」按钮——「我不想被抢」有专用表达，
  不必再借用 pin online；
- 用户现在报障的正是**手动切在线不稳定**——pin online 必须重新成为硬意图，否则「切换成在线」这个动作
  在语义上不存在（切了也随时被抢回去）。

因此本 plan 的回摆是**精准的一半**：pin online 拦自动接管；pin offline 与 U1（auto 抢占）语义不动。
`§2026-10-03-goalnn-offline-seize` 的用户口径「不管什么时候上线接活优先取就绪离线课，没有就抢第一个在训
在线课」**在 `auto` 档完整保留**——开课未选模式（unset）就是在自动池里，离线盘照抢。DECISIONS 新条要
带这段理由与被否决备选（否则三个月后又被当成「随手改回去」）。

### 4.3 与 `switch-mode-drops-jobs` 的「半球修正」

§57 的原意是「切模式 = 上一段整体作废」——触发场景是 `kind=run` 整段 job 躺在队列里被离线盘补做，
而那条腿 2026-09-25 已双端退役（`run_plan` 无生产调用者），今天队列里没有「上一模式的专属 job」：
停在队首的 iter/ppo 就是回来后要领的活。**进入离线时撤单保留**（清了更干净），**切回在线时撤单反成 bug**
（撤完本机腿又不产出，正是报障一）。半球修正 + `_cancelled` 清理一起落地，才闭合「在线 job 可领」。

---

## 5. 分阶段实施

### P0 — hub 侧闭环（两条报障的阻塞解除，全部可单测/进程内 HTTP 验）

| # | 文件 | 改动 | 验收 |
|---|---|---|---|
| P0-1 | `hub/task_pack.py`（`lease_verdict`）/ `hub/queue_offline.py`（`auto_claimable`） | `lease_verdict` 六态 + `OFFLINE_LEASE_STALE_SEC=180.0` 纯判据（★六轮：常量 + env `BCITY_OFFLINE_LEASE_STALE_SEC` 读点住 **`task_pack.py`**——判据叶子自己要用它，「住 `queue_offline`」会让叶子向上 import；**签名不变** `(now, rec, worker_id)`；`revoked` **不看身份**，读 `rec["revoked"]`/`rec["beat_at"]`；顺序 expired→revoked→mine→stale→foreign）；★五轮 P0-A：**`auto_claimable` 加 `holder_stale: bool = False` / `holder_revoked: bool = False` 两个带默认值的形参**（`holder_present and not (holder_stale or holder_revoked)` 才算挡领）——否则 stale 行仍 `claimable=false`，T3/T7 必红 | 纯函数表驱动用例（含「墓碑下**新** worker 也判 `revoked`」「老主心跳判 `revoked` 而非 `mine`」「自己静默后回来判 `mine` 而非 `stale`」三例）+ **`auto_claimable` stale/revoked 可领三例**；★既有 `test_auto_claimable_table`（`tests/hub/test_auto_handoff.py:152-163`，用 `**ok` 关键字调用）**不得红** ⇒ 新形参必须带默认值 |
| P0-2 | `hub/queue_offline.py` | 租约记录加 `beat_at/revoked`（★六轮小项 4：心跳**同时刷新 `beat_at`**，不只 `expires_at`）；`authority_of` / `auto_handoff_allowed` / `is_runnable_offline`（★六轮 F2 映射表：busy 两处 + claim 门 = `is_runnable_offline`，清单 `auto_handoff`/seize/翻模式 = `auto_handoff_allowed`）；`claim_offline` 自动接管 stale/revoked；`heartbeat` 返回 `revoked`；`release` 清墓碑（★六轮小项 6：墓碑下**仍需 token**，token 不符 ⇒ 409 `foreign`）；`revoke_offline_lease`；`note_claim`/`begin_auto_handoff` 的 authority 双判 + 竞态收尾（`begin_auto_handoff` 加 `worker_id` 形参）；★五轮 P1-F：**切在线/交还自动的在线分支一并清 `claimed_offline/by/at/flipped_at` 四键**（现在 `set_mode_pinned:560-563` 只清 `claimed_offline`、`note_release:650-652` 只清 `by/at`，`flipped_at` **无清理入口** ⇒ 交还自动后 `stall_verdict` 会拿旧 `flipped_at` 立刻判 `pending-export` **假停滞**） | `tests/hub/test_auto_handoff.py` 新用例（+「切在线 → `/admin/offline` 无 holder、`flipped_at` 归零」） |
| P0-3 | `hub/queue_offline.py` | `offline_tasks`：`authority` 字段、pinned-online 行 `claimable=false & seize=false`、**stale/墓碑 holder 标 `claimable=true`（把 `holder_stale`/`holder_revoked` 递进 `auto_claimable`，见 P0-1）**、`seize` 用 auto | 同上 + 清单矩阵 |
| P0-4 | `hub/queue_offline.py` | `_busy_locked` 活性口径（stale/revoked 不算忙；窗口锚 `claimed_at` + 刷新） | 同上 |
| P0-5 | `hub/offline.py` | claim 409 新 reason `pinned_online`；★六轮 F3：停课（标记不在）claim 一律 409 `not_offline`（含 `mode=offline` 的停课残留——**行为变更**）；`_claim_without_pack` 传 worker ⇒ **刷新 `claimed_at` 且写 `claimed_by`**；held 文案带 stale/revoked；★五轮 P0-B + ★六轮 F4：**新一轮交接即重置导包触发账本**——`begin_auto_handoff` 发现（新 `worker_id` ≠ 记录的 `claimed_by`）**∨（距上次 claim 超 `AUTO_HANDOFF_PENDING_SEC`，同主重开会话也命中）** ⇒ `reset_auto_handoff_triggers(course)`。**根因**：触发账本只在「claim 成功**且有包**」时重置（`offline.py:468` / `queue_offline.py:401`），无包腿无重置点 ⇒ A 在导包窗口重试满 3 次后死掉，B 拿到的是 `give_up`（`task_pack.py:311`）而非 `pending_export` ⇒ worker 把它加进 `skip`（`offline_boot.py:1634-1635→1695→1767`）⇒ **本会话永久放弃这门课**（只有重启 hub 才清）；报障二在无包分支照样复现 | 进程内 HTTP 用例 + T5 拆两例（见 §6.2） |
| P0-6 | ★六轮 F1 更正：`hub/store_ledger.py` + **`hub/store_leases.py`**（`_cancelled` 即时闸在 `_claim_locked`；生产写者是 `remote/hub_client.publish_job`，hub 侧必须按**账本净态**对账）；② `set_mode_pinned` 的 `drop_jobs` 收敛住 **`hub/queue_offline.py`**（★六轮小项 1：文件列不能只写 store_ledger） | ① `_claim_locked` 命中 `_cancelled` ⇒ 折叠该 jid 最后事件：`job_pending` ⇒ `discard` + 放行；`job_cancelled`/`job_completed` ⇒ 拒（R3-b/R1-f）；② `publish` 净态去重 + `discard`（防测试写者路径漂开）；③ `drop_jobs` 仅 `m==offline` 生效 | 用例**用生产写者构造**：`test_republish_after_cancel_revives_job`（`remote.hub_client.publish_job` 写盘 + 预置 `_cancelled` ⇒ peek 可见 ∧ claim 200；未重发仍拒）+ `test_switch_to_online_does_not_drop_unclaimed_jobs`（撤单用例改写） |
| P0-7 | `hub/queue_peer.py` / `queue.py` | 声明面（`authority_of`/`revoke_offline_lease`/`auto_handoff_allowed`/`is_runnable_offline` 等）与门面转发；★实施注意：新增**写 `_leases` 的方法**（`revoke_offline_lease` 置墓碑）⇒ 必须同步 `tests/hub/test_hub_queue_split.py::STATE_WRITERS['_leases']`（现为 `{__init__, _lease_rec, claim_offline, heartbeat_offline, release_offline}`）；★六轮小项 2：**同批**更新 `DOMAINS` 成员表（新方法要登记）、`QueuePeer` 纯声明数与 `len(funcs)` 断言（现 `:823` = 78）、`ALLOWED_IMPORTS`（`:349+`，新 import 边）——守卫是「多一个成员即红」，漏了会卡在实施中间态 | 导入/类型门禁 + `test_hub_queue_split.py` 绿 |
| P0-8 | `hub/queue_scope.py` | `add_course` 末尾补 `_sync_parked(c)`（R3-a） | 新用例：discover 带 offline 记录 ⇒ `parked` |
| P0-9 | `hub/queue_offline.py` / `hub/offline.py` | 冷课 authority：`offline_tasks` / `_get_task_pack` / claim 门改读盘上记录（R3-c） | 冷课三面矩阵用例 |
| P0-10 | ★五轮 P1-G 更正文件列：**只 `hub/queue_offline.py`**（`stall_verdict` 住 `:149`，**不在** `task_pack.py`——后者的头部把「严格向下、不 import 兄弟」写死了，而 `stall_verdict` 要吃 `authority`（`queue_offline` 的东西）；`auto_claimable` 同样住 `queue_offline.py:127`。**别把它们搬进 `task_pack.py`**） | `stall_verdict` 接受 authority（只静音 pinned_online，R3-e） | 停滞矩阵用例（pinned_online 静音 / pinned_offline 告警 / auto 告警） |
| P0-11 | `hub/queue_observe.py` / `hub/queue_offline.py` / `hub/queue_peer.py` | ★评审修订（P0-2）：**authority 读面出口**——`queue_state` 每课行加 `authority` + `pinned`；`offline_leases()`/`holder_info()` 加 `beat_at/silent_sec/stale/revoked`；★六轮 F5 **定死墓碑形状**：`holder_info` **返回**墓碑（`revoked=True`；清单可渲染 `held-revoked`、两个 flag 是真消费），`offline_leases()` 面向 `/admin/offline` **保留** revoked 条目（带全字段，不静默删）；`QueuePeer` 声明新增面（P0-7 同批） | 进程内 HTTP：`/admin/queue` 每课带 authority；`/admin/offline` 的租约字段含 stale/revoked ∧ 墓碑在 holders 里带 `revoked=true`；dashboard 侧 `course-overview.ts` 能读到（接线用例） |

### P1 — 训练/worker 侧闭环（报障一的充分条件、报障二的体验）

| # | 文件 | 改动 | 验收 |
|---|---|---|---|
| P1-1 | `trainer/loop_runner.py` + ★`trainer/loop_round_steps.py` | `ROUND_OFFLINE_EXIT` → `waiting(...)`；★五轮 P1-D：**日志抑制做在 `loop_round_steps.py:272-284`**（`CourseRuntime` 记 `last_offline_note`，或把那段日志搬进 `_map_outcome` 的 waiting 分支）；★P0-3：守卫落 **`_rollout_source` 自己**（`loop_transport.py:108-114`，非法值即有 `SystemExit` 先例） | `tests/trainer/test_serve_wiring.py` 新用例（含「显式 `--rollout-src run` 声明不支持」「`run_iters` 仍在 ⇒ 离线词不撤」） |
| P1-2 | `trainer/loop_lifecycle.py` | 单课程入口 offline 退避重问；`finish_course` 不被离线路径调到 | `tests/trainer/test_offline_leg_retired.py` 增消费面用例 |
| P1-3 | `trainer/loop_serve.py` | 离线等待课不再进 `_settle_rounds` 收官；日志一次；`waiting_state` 文案（`loop_plan.py` 现只有 WAIT_INFLIGHT/COLLECT/IDLE/READY 四档，需补一档）；★评审修订（灰区③）：验收加「**全课离线**时 supervisor 不退出、不吃满 CPU」（waiting=hold=False 不占票） | `e2e/test_loop_supervisor_integration.py` |
| P1-4 | `remote/offline_boot.py` | `heartbeat_loop` 收 `revoked` ⇒ 置停止事件；`run_one_course` 在第 N 轮边界收尾并打包；`claim_course` 映射 `pinned_online`/`revoked`；`resolve_courses` 认 stale 行（claimable 已含）；`_blocked_note` 报 stale | `tests/common/test_offline_task_queue.py` |
| P1-5 | `dashboard/src/server/actions/course-mode.ts` | 文案/注释（固定在线 vs 交还自动）；`pushCourseMode` 注释同步 | `dashboard/tests/course-mode.test.ts` |
| P1-6 | `dashboard/src/web/**`（矩阵/课程管理） | pinned 徽标三态、stale holder 徽标、离线等待文案 | `dashboard/tests/web-*.test.ts` |
| P1-7 | `hub/store_offline.py` / `hub/offline.py` | 撤销后回传处置：`pinned_online` 不推进活动权重；`end_it_reached` 校验 body 的 run_id/plan_sha256 与当前包相符才盖章（R3-f；字段已具，hub 侧加校验） | `tests/hub/test_offline_backfeed.py`（新文件） |
| P1-8 | `dashboard/src/server/actions/{course-mode,console-state}.ts` | 意图表 v2 `{mode,pinned}` + `restoreCourseModes` 带 pin（R3-d） | `dashboard/tests/course-mode.test.ts` |
| P1-9 | `remote/offline_boot.py` | ★五轮 P1-E：`resolve_courses` **改签名**（返回 `(picks, blocked)` 或 out-param；现为 `list[dict]`），带出 held blockers；`_run_auto` **在 `if tasks:` 之外新增分支**「`tasks` 空但 `blocked` 非空 ⇒ 退避、不累 `idle_since`」（R3-g；与既有 `_run_batch` 的 `leases["blockers"]` **同名不同物**，别接错） | `tests/common/test_offline_task_queue.py` |
| P1-10 | `dashboard/src/web/view/course-status.ts`（新；注册进 `web/view` 桶）/ `{loop-queue,course-matrix}.ts` / `app/panels/{TrainingPills,CourseMatrix}.tsx` | `courseStatus` 单一派生（pill 与矩阵同源）；来源/未知/stale 语义；词切换滞回（R4-a/b/g）；★评审修订（P0-2）：authority 从 P0-11 的 `/admin/queue` 行读（不猜） | `dashboard/tests/web-course-status.test.ts`（新）+ 既有矩阵/pill 用例改写 |
| P1-11 | `dashboard/src/server/api/{loop-queue,overview}.ts` | 读面失败不替换旧值（成功才回填）+ `stale` 标；`matrixMeta`「显示缓存」文案与行为一致（R4-e）；★评审修订（P0-2）：`overview.ts` 把 P0-11 的 `authority` 透传进 `CourseRow`（`course-overview.ts` 建行处） | `dashboard/tests/state-read-freshness.test.ts`（新）+ `server-api-courses-facts.test.ts` 补 authority 透传断言 |

### P2 — 观测/文档/收尾

| # | 内容 |
|---|---|
| P2-1 | ★评审修订：`/admin/offline` 的 authority/租约字段暴露**已提前到 P0-11**（P1-10 依赖它）；本行只剩**告警坞文案**补 `revoked`/`stale-holder` 两态 |
| P2-2 | 文档三件：`docs/nn/remote-transport.md` 新节（★评审修订：**当前最大已是 §66**——§63 包新鲜度/§64 mine 续领/§65 上传框顺序/§66 worker 身份均 2026-10-04 落地 ⇒ 本节取 **§67**，**不要**照抄原「§63」；含状态转换表全文）· `docs/nn.progress.md` 索引一行 · `DECISIONS.md` 新条（§4.1 **1–5 条** + §4.2 回摆理由 + ★P1-7 的 pinned 存量课表） |
| P2-3 | `.workbuddy/memory/` 一行；`plan/online-offline-role-routing.plan.md` 顶部加「后续：本 plan」指针 |
| P2-4 | 文档：`docs/nn/console.md` 意图表 v2 与「固定/自动」语义 + §3.10 读面口径（单一派生/未知语义/stale 标注）——★五轮 §4-3：**当前最大已 §28**（2026-10-04）⇒ 新节取 **§29**，不要照抄 §22；`DECISIONS.md` 补 §4.1 的 **6–21 条**（含 R3-c/f、三轮读面 R4、★评审 P0-1/P0-2/P0-3、**五轮 P0-A/P0-B/P1-C/P1-F/P1-G/P1-H** 的行为变更） |

---

## 6. 集成测试（用户点名，逐条对报障）

### 6.1 分层与纪律

- **e2e**（`nn-training/e2e/`）：真 hub 子进程 + 真 HTTP + 真 `offline_boot` 客户端 + 假控制台；不 spawn bun/node、不加载 torch。
- **hub 集成**（`tests/hub/test_auto_handoff.py`）：进程内真 HTTP（`_boot` 夹具）+ 假时钟注入。
- **worker/trainer**：`tests/common/test_offline_task_queue.py`、`tests/trainer/test_serve_wiring.py`、`e2e/test_loop_supervisor_integration.py`。
- **不许真等 900s**：stale 阈值走 `BCITY_OFFLINE_LEASE_STALE_SEC`（e2e 子进程 env）；hub 用例用 `now_fn` 假钟推进；
  TTL 用例保持既有「推进假钟跨 TTL」形状。
- 每个 e2e 用例落盘前后断言盘上事实（`offline-dispatch.json`、`/admin/offline`），不只信内存。

### 6.2 e2e 用例（`e2e/test_auto_handoff_e2e.py` 增 T1–T8，T5 拆 T5a/T5b）

| ID | 用例名 | 复现报障 | 断言（真 hub + 真 HTTP） |
|---|---|---|---|
| T1 | `test_manual_online_switch_revokes_lease_and_republished_job_is_claimable` | ① | A 盘 claim 在训课（翻 offline）→ 发一份 role=online 的 job 并让它被 `_drop_unsettled` 撤掉 → 人切 `mode=online&pin=1&drop_jobs=1` → ①租约墓碑存在、A 心跳得 409 `revoked`；②清单 `seize=false & claimable=false & authority=pinned_online`；③**重发布同一 job_id** → peek 可见 ∧ 在线 role claim 得 200。★五轮 §4-6：T1③ **必须显式构造同一 job_id**（幂等键 = `课程+it+runId`，`publish_job` 路径）并在注释写明组成——**别靠「同 it 必然同 id」的假设**（假设错了整个 R3-b 是空转）。★六轮 F1：③ 必须经**生产写者** `remote.hub_client.publish_job`（磁盘 IPC）构造（hub 的 `store_ledger.publish` 在生产零调用），并先经生产路径把 jid 写进 `_cancelled`（撤单/`cancel_stale_jobs`），配反探针 |
| T2 | `test_pinned_online_course_is_not_seized_nor_claimed_by_offline_disk` | ① | pin online 的课：`claim` 409 `pinned_online`；真 `resolve_courses` 在没有别的课时返回空列表（不抢）；`unsetCourseMode` 后同一份清单里 `seize=true` 且 claim 200 翻 offline |
| T3 | `test_dead_offline_holder_is_reclaimed_after_silence` | ② | A claim（正常心跳）→ A 停跳、假钟/阈值推进过 stale → B 清单该行 `claimable=true`、`holder.stale=true` → B claim 200（无需 `takeover=1`）→ A 心跳 409 `taken` |
| T4 | `test_dead_holder_does_not_block_seizing_another_course` | ② | A 的租约 stale 后，B claim 另一门在训在线课（seize 路径）⇒ 200（不吃 `busy`）；对照组：A 活着（心跳新鲜）⇒ 409 `busy` |
| T5a | `test_export_window_owner_death_reopens_the_course` | ② | A claim 无包课 → 翻 offline + pending_export（无租约）→ A 死 → 窗口超时后 B claim 同一课 ⇒ 409 `pending_export`（可重触发、非 busy），且 B 能同时抢别的课（窗口不占闸） |
| T5b | `test_export_window_new_owner_resets_the_trigger_budget` | ② | ★五轮 P0-B：A 把导包触发**烧到上界 3 次**后死 → B（新 `worker_id`）claim 同一课 ⇒ **`reset_auto_handoff_triggers` 生效** ⇒ 回 **409 `pending_export`**（**不是 `give_up`**）∧ worker **不把它加进 skip**。**这是报障二在无包分支的真回归锚**（修 P0-5 前必红） |
| T6 | `test_offline_course_waits_and_resumes_without_reopen`（★实施落点：`e2e/test_loop_supervisor_integration.py`——真 `Supervisor` + 真引擎 + 假重活；§6.4 的同义名并入此例） | ① | 本机腿（假 runner 或真 `_map_outcome`）在 offline 时停在等待态、**不**写 `run_complete`；控制台反向写回在线配置后 ⇒ 同一课在下一拍继续推进（引擎热复用，无需停开课）。实施时增补反探针：把 `_map_outcome` 离线分支改回 `done(final=True)` ⇒ 本用例以 `done != waiting` 红 |
| T7 | `test_full_cycle_offline_then_online_then_offline`（已落地，同文件；★实施增补：切在线后先断言 claim 409 `pinned_online` 再交还自动） | ①+② | 在线 → 离线接管（A）→ A 死 → B 接管（T3 链）→ 人切在线 → 回自动 → 再被 C 抢走；全程 `mode/authority/lease/holder` 逐次断言，末尾断言账本无假 `run_complete` |
| T8 | `test_hub_restart_keeps_discovered_offline_course_parked` | 二轮 R3-a | hub 以 discover-only 起、盘上已有 `offline` 记录 → 关掉再起第二个实例 → 该课**仍然停摆**（真发一份 role=online job 不被派走）；修 R3-a 前必红 |

### 6.3 hub 用例（`tests/hub/test_auto_handoff.py` 增/改）

- `test_lease_verdict_six_states`（表驱动：free/mine/foreign/expired/stale/revoked × claim/heartbeat/release 结果）
  ★评审 P0-1 追加三例：①**墓碑下新 worker 也判 `revoked`**（不是 `foreign`）②老主心跳判 `revoked`（不是 `mine`）
  ③自己静默后回来判 `mine`（不是 `stale`）。
- ★评审 P2-4：`test_auto_eligible_alias_is_the_only_reader`（机械守卫：grep/用例断言 `auto_eligible` 的调用点
  == 白名单，新增调用点即红）。
- ★五轮 P0-A：`test_auto_claimable_stale_and_revoked_are_claimable`（`holder_stale=True`/`holder_revoked=True`
  ⇒ `claimable=True`；照旧 `holder_present=True` 且都不置 ⇒ `False`）。
- ★五轮 P1-C：`test_cold_course_without_record_is_not_auto_claimable`（盘上有标记、∉ `_stores`、无记录、无包
  ⇒ `authority=pinned_offline ∧ auto_handoff_allowed=False ∧ claimable=False`，与今天逐字相同）。
- ★五轮 P1-F：`test_switching_online_clears_all_four_handoff_fields`（切在线/交还自动后
  `claimed_offline/by/at/flipped_at` **全归零** ∧ `/admin/offline` 无 holder ∧ 不产生 `pending-export` 假停滞）。
- ★五轮 P0-B：`test_handoff_trigger_budget_resets_when_owner_changes`（烧满 3 次 + 换 `worker_id`
  ⇒ 触发账本清零 ⇒ 下一次回 `trigger`/`pending_export` 而非 `give_up`）。
- ★评审 P0-11：`test_queue_state_and_admin_offline_expose_authority`（`/admin/queue` 每课带 `authority`+`pinned`；
  `/admin/offline` 租约含 `stale/revoked` 且**墓碑不出现在 holders**）。
- `test_stale_lease_takeover_logs_and_clears_holder`（自动接管 + 日志 + 无 `takeover` 参数）。
- `test_pinned_online_blocks_claim_and_seize_matrix`（pin/mode/marker 组合矩阵，含 `unset` 回自动）。
- `test_human_online_switch_revokes_lease_and_clears_handoff_record`（墓碑 + 清 `claimed_offline/by/at/flipped_at`）。
- `test_busy_gate_ignores_stale_and_revoked_leases`（腿① + 腿②超窗）。
- `test_handoff_window_refreshed_by_repeated_pending_export_claims`（死盘不再占窗口）。
- `test_republished_job_is_claimable_after_cancel`（R1-f：peek 可见 ∧ claim 200；配反向断言：未重发前 claim 仍拒）。
- `test_switch_to_online_does_not_drop_unclaimed_jobs`（R1-b：切在线后池里那份仍在且可领；进入离线仍撤）。
- `test_discovered_course_inherits_parked_from_dispatch_record`（R3-a：discover-only + offline 记录 ⇒ `mode=offline ∧ st.parked`）。
- `test_cold_course_authority_follows_dispatch_record`（R3-c：未登记课 × {无记录, online+pin, offline} × 清单/取包/claim 三面）。
- `test_republish_after_cancel_revives_job`（R3-b：净态复活事件 + 池可见 + claim 200；未重发仍拒 `cancelled`）。
- `test_pinned_offline_stall_is_reported`（R3-e：`stall_verdict` 矩阵——pinned_online 静音 / pinned_offline 告警 / auto 告警）。
- `test_revoked_backfeed_does_not_advance_active_weights`（R3-f：切在线后旧 worker 回传 ⇒ `weights.json` 不变；
  `end_it_reached` 带旧包身份 ⇒ 不写 `completed_pack_sha`；带当前包身份 ⇒ 写）。
- ★六轮 F2：`test_busy_gate_blocks_pinned_offline_claim`（A 在跑 X；B claim pinned_offline 的 Y ⇒ 409
  `busy`；清单 Y 行带 busy）· ★六轮 F3：`test_stopped_course_with_pack_and_offline_record_is_not_claimable`
  （停课 + offline 记录 + 包在 ⇒ claim 409 `not_offline` ∧ 默认清单不含它）· ★六轮 F4：
  `test_handoff_trigger_budget_resets_when_same_owner_returns_after_window`（同 `worker_id`、距上次 claim
  超窗 ⇒ 重置）· ★六轮 F5：`test_tombstone_holder_shape_in_tasks_and_admin_offline`（墓碑行
  `claimable=true`、`holder.revoked=true`、reason `held-revoked`；`/admin/offline` 保留条目）· ★六轮 F6：
  `test_authority_combination_matrix`（全组合表逐档：authority / claim / claimable / auto_handoff_allowed，
  含冷课 `{online,pin:false}` 与停课）。

### 6.4 worker / trainer / dashboard 用例

- `tests/common/test_offline_task_queue.py`（★实施落点，名字已对账）：
  `test_resolve_skips_pinned_online_rows`（不抢人固定的课）· `test_resolve_picks_stale_holder_row`
  （stale 行可领，不必等 TTL）· `test_claim_course_maps_auto_handoff_states`（§6.4 原名的
  `pinned_online` 档并入此例）+ `test_heartbeat_loop_stops_on_revoked`（§6.4 原名的 `revoked` 面 = 心跳
  置停止事件）· `test_blocked_note_reports_stale_holder`。
- `tests/trainer/test_serve_wiring.py` / `e2e/test_loop_supervisor_integration.py`（★实施落点）：
  `test_offline_course_waits_and_resumes_without_reopen`（T6；offline 退出 = waiting；配置写回后同一课推进；
  无 `run_complete`——两条反探针已实测红）· `test_offline_wait_does_not_block_other_courses`（hold=False）·
  `tests/trainer/test_serve_wiring.py::test_offline_course_reaching_done_is_not_settled`（P1-3 第二道闸：
  即使走到 QUEUE_DONE 也不调 `finish_course`；已实测反探针红）·
  `tests/trainer/test_offline_leg_retired.py::test_explicit_rollout_src_declares_restore_unsupported`
  （P1-1 的「显式 --rollout-src run 声明不支持」档：喊且去重；`local` 不喊）。
- `dashboard/tests/course-mode.test.ts`：切在线回执含「固定在线 + 不再被抢」；
  `unsetCourseMode` 回执含「交还自动」；`pushCourseMode` 的 `drop_jobs` 传参断言更新为先落本机再推 hub。
- `dashboard/tests/web-course-matrix.test.ts`：pinned 三态徽标 + stale-holder 徽标。
- `tests/common/test_offline_task_queue.py`（补）：`test_resolve_reports_held_rows_as_blockers`（R3-g：held 行进入
  blockers、预算不推进；held 行的原因文本进日志）。
- `dashboard/tests/course-mode.test.ts`（补）：`test_intent_v2_pinned_flag`（那颗开关写 `{mode,pinned:true}`；
  开课弹窗写 false）· `test_restore_online_intent_sends_pin`（R3-d）。
- `dashboard/tests/web-course-status.test.ts`（新，§3.10）：①同一派生下 pill 与矩阵状态词一致（假收官/暂停/pinned 三例）；
  ②hub 探针失败 ⇒ 标注「未知」，**不得**渲染成「未在训 / hub 不认」；③调度器读面空行失败 ⇒ 保留上一拍词 + stale 标；
  ④来源可用性 false→true 不改词（滞回只改标注）。
- `dashboard/tests/state-read-freshness.test.ts`（新）：`hubCache`/`loopQueue` 失败不替换旧值（成功才回填，代际计数语义不变）；
  TTL 内失败一次 ⇒ 旧行仍在 + stale；连续失败超一个刷新周期 ⇒ 显示「未知」（不是无限保旧）。

### 6.5 既有用例的语义改写清单（必须同批改，否则门禁红）

| 文件 | 用例 | 为什么 |
|---|---|---|
| `tests/hub/test_auto_handoff.py` | `test_seize_claim_flips_pinned_online_and_drops_unclaimed_jobs` | pin 不再被抢 ⇒ 改成「409 `pinned_online` + mode 仍 online」；seize 的正面用例改用 `pin=0`（auto） |
| 同上 | ★新增（评审 P2-5）：`test_admin_courses_get_reports_pin_and_claim_state`（`:604`） | **plan 自己在 §2.4 R3-d 引用了它，却漏进本清单**——★六轮小项 3：它的 `st3==400` 依据是「legacy `pin=None` 拒覆盖 claim 翻的 offline」，本 plan **未改这条** ⇒ 大概率**保持 400**；先复核再改（新行为只动 `pinned`/清理字段面），若确需改在此对账 |
| 同上 | `test_drop_jobs_cancels_unclaimed_and_keeps_inflight` | 保持（进离线仍撤）；★评审 P2-5：明确**新用例名** = `test_switch_to_online_does_not_drop_unclaimed_jobs`（§6.3 已有，此处对账） |
| `tests/common/test_offline_task_queue.py` | `test_claim_*` 系列 | 旧 `foreign` 路径补 `stale` 一档；`lease_verdict` 返回值域扩 |
| `e2e/test_multi_course_single_hub_e2e.py` | `test_offline_course_is_parked_and_resumes_on_going_online` | 语义升级：切回在线时**不重发**那份 job 也立刻可领（现在是「回落队首」）。★实施对账：现有断言已覆盖该语义（切回在线后同一 jid 被推走、`pending_n=0`），复核后**不改**；full gate 绿 |
| `tests/trainer/test_offline_leg_retired.py` | 轮级断言保持（`ROUND_OFFLINE_EXIT` 仍在）；新增消费面用例 | 改动在 `_map_outcome`/`run()`，不在 `step_course_iter` |

### 6.6 反探针（改坏必红）

- 删掉 `note_claim` 的 authority 双判 ⇒ T2 红；把 `_busy_locked` 活性口径退回「任何 live 租约」⇒ T4 红。
- 注释掉 `publish` 的 `_cancelled.discard` ⇒ T1③ 红；把切在线的 `drop_jobs` 恢复成无条件 ⇒ `test_switch_to_online_does_not_drop_unclaimed_jobs` 红。
- 把 `_map_outcome` 的 offline 分支改回 `done(final=True)` ⇒ T6 红（出现 `run_complete`）。
- `stale` 阈值硬编码 900（抄 TTL）⇒ T3 的「过 stale 即接管」在假钟上超时红。
- 去掉 `add_course` 的 `_sync_parked` ⇒ T8 与 R3-a 用例红；把 `publish` 复活分支退回「只 discard」⇒ R3-b 用例红。
- 拆掉 `_land_offline_round_extras` 的 authority 门 ⇒ R3-f 用例红。
- 把 hub 探针失败恢复成「`hubSeen=false` ⇒ 未在训」⇒ §6.4 读面「未知」用例红；让读失败清空旧行/旧值 ⇒ 保留用例红；
  让 pill 与矩阵各拼各的源 ⇒ 一致性用例红。

---

## 7. DoD

- **P0**：§6.2 T1–T5a+T5b+T8、§6.3 全绿（含 R3-a/b/c/e 新用例 + P0-1 三例 + **五轮 P0-A/P0-B/P1-C/P1-F 四例** + P0-11 暴露用例 + **六轮 F1–F6 五例**；F1 用例必须用生产写者 `remote.hub_client.publish_job` 构造）
  + `bash tools/githook/nn-python-gate.sh` 绿；hub-only（`git diff --stat` 自证 `remote/worker/trainer` 零行，除声明面）；
  ★评审 P1-7：P0 完成前**先出** `tmp/*/offline-dispatch.json` 的 `pinned=true` 存量课表（记进 DECISIONS 新条）。
- **P1**：§6.2 T6/T7、§6.4 全绿 + nn python gate 绿 + `bun run check` 绿；动过 `dashboard/**` ⇒
  `cd dashboard && bun run typecheck && bun run test` 绿（**这条是硬门禁，不是可选**——`bun run check` 只判根项目）。
- **总 DoD**：两条报障在 e2e 里各有一条**同名复现**用例（T1↔报障一、T3+T4↔报障二）；`plan/mvp.md` §10 的
  MVP DoD 不受影响（本 plan 不动游戏侧）；60 FPS/内存无涉（hub/trainer 侧）；文档（P2-2/P2-4）落地；
  首页课程区读面（P1-10/P1-11）在 dashboard 门禁下全绿（一致性/未知/不消失三类用例；★评审 P0-2：含
  「authority 经 `/admin/queue` 透传到 `courseStatus`」的接线断言）；
  DECISIONS 新条含 §4.1 的 21 条变更（6–11 二轮、12–14 三轮读面、15–17 四轮、18–21 五轮）+ §4.2 回摆理由 + 被否决备选 +
  ★P1-7 的 pinned 存量课表。

---

## 8. 被否决备选（三问留档）

| 备选 | 否决理由 |
|---|---|
| 只修租约失联（R2），不动 pin（R1） | 报障一的回抢与撤单原样存在——「切在线」仍然不稳定 |
| pin 继续坍缩，改为「切在线时把课从 seize 候选里临时摘出 N 分钟」 | 引入时间窗状态（第三个事实源）；窗口过后又被抢，问题只是延后 |
| hub 定期扫死租约（后台线程/tick） | 本仓既有形状是「谁先看谁回收」（§52 同款）；后台线程带生命周期/测试复杂度，收益零 |
| 新 worker 收到 `held` 就自动 `takeover=1` | 无活性依据的盲接管 = 双盘同跑同一段（首写幂等只兜回传，不兜算力与进度错乱）；判据必须在 hub |
| 切在线时硬杀云机会话（新控制端点/kill） | Colab/Kaggle 无反向控制通道；且违背「训练不因控制面停摆」——心跳 409 `revoked` + 轮边界自停是唯一可用通道 |
| 离线租约照抄 job 租约的「零心跳才早收」（`beat_at == at`） | 云机心跳线程在整段训练期间持续跑，慢网/挂起 >180s 的概率远大于 job 下载段；「连续静默」判据在 hub 侧成本相同、恢复更快，误杀代价也只是重复算力（回传首写幂等） |
| 训练侧复活靠 `touch training-enabled.txt`（借 `reopened_parked`） | open_time 是 T4 排序 SSOT，touch 会改动 seize 顺序与告警锚点；标记的语义是开课/停课，不是模式 |
| 训练侧复活靠重启 hub/trainer | 正是今天的操作员动作（停→开课），本 plan 要消灭的正是它 |
| ★评审 P0-1 备选：给 `lease_verdict` 加 `token` 入参，按 token 判 `revoked` | `lease_verdict` 是 `hub/task_pack.py` 的**叶子纯函数**（该文件头部把「严格向下、不 import 兄弟」写死）；加 token 会让它与 hub 租约合同耦合上升，而 revoked 本就是「全局墓碑、不看身份」的语义（见 §3.3 定案）。token 的真实用途只在 `claim_offline`/`heartbeat_offline` 的**分流**（建新租约 vs 回 409 文案），那两处本来就有 token |
| ★五轮 §4-1 备选：竞态收尾改回 409（`claim_offline` 在 `note_claim` 后重判 authority，命中 `pinned_online` ⇒ 回 409） | 需**撤回 `note_claim` 已写的 dispatch 字段**（mode/claimed_offline/claimed_by/at）——新增一条回滚路径，比现状更易出错；而两种到达顺序的**终态等价**（都是墓碑 + `pinned_online`），worker 拿 200 + 墓碑 token 已是 §3.8 明确支持的兼容降级。收益仅「省掉一次白 claim」，不值一条回滚。**契约已在 §3.3 写死** |
| 五轮 P1-C 备选 (a)：冷课一律派生 `pinned_offline` | 与定案 (b) 效果相同，但会把「人固定离线」的徽标打给**从没人管过**的冷课，读面要额外解释 ⇒ 采用 (b)（`auto` 只对 `course in _stores` 成立） |
| 「再发布只清 `_cancelled`」 | 探针证明不够：账本折叠仍是撤单 ⇒ 必须在账本层按净态复活（§3.6） |
| 冷课一律按离线放行（维持现状） | pin/在线意图在最容易分叉的冷课面失效——正是「切换不稳定」的土壤 |

---

## 9. 风险与灰区

- **误回收窗口**：云机网络分区 >180s 会被判 stale；旧主继续跑完，新主接管重跑——代价是重复算力
  （`(run_id, it)` 首写幂等保证回传不串），不是数据损坏。若现场证明 180s 太紧，调 env 即可（不写死）。
- **旧 worker 兼容**：旧 worker 不会因 `revoked` 停止（照旧跑完）；双跑窗口由「切在线不撤单 + 假收官消失」
  收窄，但彻底消灭要等 P1 的新 worker 铺开——如实记录，不夸口。★评审 P1-6：**墓碑只对新 worker 有效**
  （`remote/` 全目录零 `revoked`、`_beat` 只按 `expired` 二分）——所以「切在线」对旧云机的**只读效果 = 停止心跳续租**，
  不是停止算力。
- ★评审 P1-7 **pin 回摆的存量冲击**：authority 读 hub 盘上 `dispatch.json.pinned`（不是控制台意图表），
  而 2026-10-03 语义下「人手动切在线」写的就是 `pin=1` ⇒ 上线当天这批课**从可被抢变永久不可抢**（这正是 §4.2 想要的）。
  风险不在方向而在**数量未知**：P0 实施前必须盘点（§7 P0 行），并确认这批课没有「本该继续被自动交接」的。
- ★评审 灰区③ **全课离线时 supervisor 的行为**：`waiting(hold=False)` 不占票（`loop_runner.py:269-276`），
  但「离线课永远在队列里重问」意味着进程**常驻 + 每 `poll_interval` 醒来一次**。验收（P1-3）加一条：
  全课离线时 supervisor 不退出、日志不刷屏（每模式状态一次）、CPU 空转可忽略。
- **`_leases` volatile**：hub 重启清租约（既有语义）；重启同时清墓碑，旧云机重 claim 时 authority 判据兜住。
- **控制台意图与 hub 的漂移**：本 plan 不新建第四者；`authority` 由既有字段派生，控制台面板改读派生值即可。
- **「再发布 = 复活」的两层修复**：与「撤单 = 上一段整体作废」的直觉相反——但重发（同幂等键）本来就是
  「重试」语义；账本折叠两层必须同向，只清内存 set 的中间态就是「池可见/claim 拒」或反过来的撒谎形状（R3-b 探针）。
  ★六轮 F1：生产写者是 `remote/hub_client.publish_job`（磁盘 IPC，无条件追加 `job_pending`）而不是 hub `publish`
  ——修法落在 hub 侧按账本净态对账（`_claim_locked`），用例必须用生产写者构造，否则绿着骗人。
- **冷课 authority 的默认面**（R3-c）：★五轮 P1-C 已**定死**——无记录的冷课 = `pinned_offline`
  （有包可领、无包不可领，与今天逐字相同），且 `auto` 档额外要求 `course in _stores`
  ⇒ **无包冷课不会触发导包**（能力不扩张）。历史兼容选择，不再留二义。
- **暂停是否拦自动接管**（R3-h）：推荐 A（不拦，回执与面板写明）；若用户口径变化，改 B 并同步 `docs/nn/console.md`。
- **回传的「落盘但不推进」边界**（R3-f）：不推进活动权重/不盖章，但**不拒收**——产物仍落离线目录与账，
  导入路径照旧可用（「训练永不因控制面停摆」的延续）。
- **读面滞回 vs 即时性**（R4-d/g）：滞回只作用于「词」，不作用于动作回执与意图/回执现读；风险是真实变化晚一拍显示
  ——代价上限 = 一个刷新周期且带 stale 标注，换「不再每次刷新换一个说法」。超窗即显示「未知（读面失败 Ns）」。
- **last-known-good 的陈旧风险**（R4-e）：失败保旧值必须带 `stale.since` 与「重算中」标注，窗口 ≤ 一个刷新周期；
  不保留（现状：空行替换）同样误导且更显眼——两害相权取其轻，并靠标注把风险摆在明面上。

---

## 10. 指针

- 现状长文：`docs/nn/remote-transport.md` §48（role 归属）· §56（auto handoff）· §57（撤单）· §59（seize）·
  ★评审修订：§62（opt 种子）/ §63（包新鲜度）/ §64（mine 续领）/ §65（上传框顺序）/ §66（worker 身份）——**本节新文取 §67**。
- 实现落点：`hub/{queue_offline,task_pack,offline,store_ledger,store_leases,store_offline,queue_scope,queue_peer}.py` ·
  ★评审 P0-11 新增：`hub/queue_observe.py`（`queue_state` 加 authority）·
  `trainer/{loop_runner,loop_lifecycle,loop_serve,loop_plan}.py` · `remote/{offline_boot,offline_deliver}.py` ·
  `dashboard/src/server/actions/{course-mode,console-state,train-mode}.ts` · `dashboard/src/server/api/{loop-queue,overview}.ts` ·
  `dashboard/src/web/view/{course-status.ts（新）,course-overview.ts,loop-queue.ts,course-matrix.ts}` · `dashboard/src/stack/hub-admin.ts`。
- 测试：`e2e/test_auto_handoff_e2e.py` · `tests/hub/{test_auto_handoff,test_offline_backfeed}.py` ·
  `tests/common/test_offline_task_queue.py` · `tests/trainer/{test_serve_wiring,test_offline_leg_retired}.py` ·
  `e2e/{test_loop_supervisor_integration,test_multi_course_single_hub_e2e}.py` · `dashboard/tests/{course-mode,web-course-status,state-read-freshness}.test.ts`。
- 决策：`DECISIONS.md` 新条（§4.1/§4.2/§4.3）· `docs/nn.progress.md` 索引。
