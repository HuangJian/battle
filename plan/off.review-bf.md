# off.review-bf.md — 《offline-online-status-switch》第六轮评审

> 评审人：Buffy · 2026-10-05 · 基准 HEAD `3ca9212b`（goal-nn）· 方法：把 plan 的每一条判据 / 行号 /
> 消费链拿到工作树真代码上逐条对账（只读 grep + 静态追踪；未跑门禁、未改代码）。
> **范围**：不重复复验前五轮已并入的修订，只报**仍未闭合或新发现**的问题；前五轮项抽查清单见 §4。
> **结论**：设计方向与四/五轮修订整体成立、可实施；但仍有 **1 处 P0 级修复落点错误（F1）** +
> **2 处判据/行为未点名（F2/F3）** + **3 处需定死的二义（F4/F5/F6）** + 一批守卫/文案小项（§4）。
> 建议 F1–F3 并入 §5 P0 清单、F4–F6 并入对应 P0/P1 行与 §6 用例后再开工。

| # | 级别 | 一句话 |
|---|---|---|
| F1 | P0 | 「再发布=复活」的修法落在**生产不走的写者**（`store_ledger.publish`）；真实 republish 走 `remote.hub_client.publish_job`（磁盘 IPC），`_cancelled` 内存否决**没有任何清理点** |
| F2 | P0 | `auto_eligible` 重定义后，busy 闸的两处消费点（`claim_offline` 的 `if auto:` / 清单的 `busy` 字段）未点名 ⇒ pinned_offline 静默丢「一拖一」 |
| F3 | P0 | 「停课 ⇒ claim 拒」被写成现状：生产 `stopCourse` 推 `mode=offline`，而 claim 门只在 `mode != offline` 时拒 ⇒ 停课+有包今天仍可被领走 |
| F4 | 中 | 触发账本「换主即重置」漏了**同 worker_id 回来**（Colab/Kaggle 重开会话常沿用同一 `.worker-id`） |
| F5 | 低中 | 墓碑 holder 的形状没定死：`holder_info`/`offline_leases` 谁滤墓碑、`offline_tasks` reason 渲染哪档、`holder_revoked` 有无消费面 |
| F6 | 中 | authority 派生对「冷课 × `!pinned` 记录」与「停课」无定义；P1-C 用例矩阵缺 `online+pin=false` |

---

## 1. P0 级

### F1（P0）「再发布 = 复活」修在**生产不走的写者**上：`store_ledger.publish` 全仓零生产调用；真实 republish 走 `remote/hub_client.publish_job`（磁盘 IPC），`_cancelled` 内存否决**没有清理点**

**plan 说**（§3.6 / P0-6 / R1-f / R3-b）：「两层一起修」——① `store_ledger.publish` 清 `_cancelled`；
② `publish` 按账本净态追加复活 `job_pending`（探针实测 cancel→republish 后 `claimable=[]`）。

**事实链**（逐条 grep / 读码坐实）：

1. **`hub/store_ledger.py:182 publish` 在 `hub/` 内没有任何调用方**。全仓（除 `.venv` / `tests` / `tmp`）
   唯一的 `.publish(` 调用点只有两个 e2e 测试（`e2e/test_auto_handoff_e2e.py:150`、
   `e2e/test_worker_name_ledger_e2e.py:68`）；`hub/server.py` / `http_face.py` / `store_wire.py` 都没有
   发布路由。生产的发布/重发是 `remote/hub_client.publish_job`（`trainer/loop_remote_job.py:301`、
   `trainer/bc_loop.py:352`），**磁盘 IPC 直写 jsonl + job 目录，不经过 hub 进程的方法**。
2. `remote/hub_client.py:1058-1075`：`register=True` 时**无条件** `_append_ledger(job_pending)`
   （注释写「幂等去重」，代码里没有去重守卫）。⇒ 对生产路径，republish 本来就会追加新的
   `job_pending`，**R3-b 的「账本折叠仍判撤单」不成立**；探针观察到的 `claimable=[]` 是 hub 侧
   `publish` 自己的去重（`store_ledger.py:200-206`）造成的——那是**测试写者的形状**。
3. 真正的生产杀手是①的**内存否决**：`hub/store_leases.py:341`（`_claim_locked`）`job_id in self._cancelled`
   即拒；该 set 只由 `cancel_unsettled_jobs` 添加（`store_ledger.py:178`），**只在 hub 进程重启时清空**。
   trainer 换进程直接写盘，hub 永远收不到「这份 jid 复活了」的信号 ⇒ 「账本净态 pending（peek 可见）
   ∧ claim 永远 `cancelled`」的撒谎形状原样存在。plan 的修法（`publish` 里 discard）因为**调用不到**，
   等于没修。`store_leases.py:344` 的注释「重启后本 set 为空也不漏：那时池子里已经没有这份 job」，
   正是被 republish 打破的假设。
4. 连带：plan 在 T1③ 里点了「`publish_job` 路径」——**按它写 T1③ 必红**（实现时才会暴露）；
   若图省事按 hub 侧 `publish` 写，则绿着骗人（只覆盖测试写者）。

**影响**：报障一的后半段（切回在线后在线 worker 领不到 job）在生产路径上仍复现，而这正是 P0-6 被立项的
目标。

**建议修法**（推荐 A）：
- **A（hub 侧对账，与「事实源是账本」一致）**：把 `_cancelled` 从「一旦作废永不作废」改成**读账本净态**。
  最小落点：`claimable_job_ids` 折叠循环后，对入选 jid `self._cancelled.discard(jid)`（同一把 `_lock` 下），
  或 `_claim_locked` 先查该 jid 最新事件、为 `job_pending` 则放行并清 set。「拿着作废前 peek 的 jid 硬领」
  仍被挡（净态仍是 cancelled），「账本已复活」的 jid 不再被误杀。
- B（不推荐）：另给生产写者一条 hub 可观察的复活信号 ⇒ 引入第二事实源。
- **用例**：`test_republish_after_cancel_revives_job` 与 T1③ **必须用生产写者构造**（`remote.hub_client.publish_job`，
  或「写 pending 行 + 预置 `_cancelled`」），配反探针「去掉对账 ⇒ 红」。

### F1b（P1-H 的裁决更正）`bc_ledger` 的「取支路」判断依据不成立

plan P1-H 说「要么改净态折叠，要么明确『BC 腿的 job 不走 `publish` 复活』并写进注释 + 用例（实施前先 grep
坐实，不许默认）」。已坐实：**RL 与 BC 都不走 hub `publish`**（F1-1），但生产 republish（`publish_job`）
当下就会产生「`job_pending` 晚于旧 `job_cancelled`」的账本行（无条件追加）。所以「BC 不走 publish 复活」
这条假设即使为真，也**不足以**排除 `inflight_jobs` 撒谎：`worker/bc_ledger.py:89-104` 的
`pending ∖ terminal` 对「同 jid 后补 pending」不敏感，读者正是 `trainer/loop_plan.py:93`。
⇒ 直接选「同口径净态折叠」这一支，不要留「写死假设」支路。

### F2（P0，静默回归）`auto_eligible` 重定义后，busy 闸的两个消费点没被点名

**plan 说**（§3.1）：`auto_eligible` 拆成 `auto_handoff_allowed` 与 `is_runnable_offline`，
「**所有**调用点（清单、claim、busy、seize、T2 翻模式）改用这两个之一」。

**事实**：两处 busy 消费点今天按 `auto_eligible` 门控：
- `hub/queue_offline.py:377-383`（`claim_offline`）：`auto = self.auto_eligible(course)` … `if auto:
  busy = self._busy_locked(course)`；
- 同文件清单段：`busy = self.busy_reason(course) if auto else ""`。

按新别名（= `auto_handoff_allowed`，`pinned_offline` 为 False），这两处会对 **pinned_offline 的 claim
跳过「一拖一」闸**、清单行也不再显示 busy——与 §3.4-4「authority 限制只作用于**被检查方**（other）；
发起方不受限」相矛盾。今天 pin 不参与 busy（旧 `auto_eligible` 只看标记），所以这是**静默回归**，
且 plan 的用例清单里没有 pinned_offline 的 busy 例。

**建议**：在 §3.1 补一张**逐调用点映射表**（写进 P0-2/P0-3 验收）：清单 `auto_handoff` 字段 →
`auto_handoff_allowed`；claim 门/`offline` 语义 → `is_runnable_offline`（含开课标记）；**busy（上述两处）→
`is_runnable_offline`**；`seize`/T2 翻模式 → `auto_handoff_allowed`。补例：A 在跑 X，B 领 pinned_offline 的
Y ⇒ 409 `busy`。

### F3（P0，事实错误 + 未调度）「停课 ⇒ claim 拒」被写成现状；生产 `stopCourse` 推 `mode=offline`，claim 门只在 `mode != offline` 时拒 ⇒ 停课 + 有包今天仍可被领走

**plan 说**（§3.1 `stopped` 行 / §3.2 停课行）：claim 拒（409 `not_offline`，**现状**）/「不新发租约」。

**事实链**：
- `dashboard/src/server/actions/course-lifecycle.ts:487 stopCourse` → `pushHubMode(c, 'offline')`
  （`:227-243`，内部 `pushCourseMode(course, mode)`，**不带 pin**）⇒ 停课后 hub 记录是 `mode=offline`；
- `hub/offline.py` 的 claim 门（`auto_eligible` 引用在 `:399` 那段）判据 = `course in courses ∧
  mode != offline ∧ ¬auto_eligible` 才 409——`mode=offline` 直接放行；
- `hub/queue_offline.py` 清单：`offline = mode==OFFLINE`（真）∧ `auto=False` ⇒
  `claimable = pack_exists and offline`（有包即 True）；`claim_offline` 本身不看开课标记 ⇒
  **租约照发、整段照跑**（note_claim 因 auto=False 不改 mode，但租约已成立）；
- 既有用例只覆盖「停课后 mode=**online**」的形状（`tests/hub/test_auto_handoff.py:227`
  `test_stopped_online_course_is_hidden_from_the_offline_disk`；「停课 + pinned offline + 无包 ⇒ 404」是另一半）——
  **生产形状（停课推 offline + 有包）没有用例**。

**影响**：「唯一 opt-out = 停课」在最常见的路径上是破的（先被切过离线/自动接管过的课，停课后云机仍能领并
回传）；plan 把它标成「现状」不会促使任何人去改它。

**建议**：claim 门与 `claimable` 对「停课（开课标记不在）」一律 409 `not_offline`，**不依赖 mode 记录**
（即用 §3.1 的 `is_runnable_offline` 收口，并把它标注为**行为变更（修洞）**）；补用例「stop 后（记录 offline +
包在）claim 409 ∧ 默认清单不列」；§3.2 表格文案同步。此修法与 F6 的 `stopped` 派生一起定（见下）。

---

## 2. 二义需定死

### F4（P0-5 措辞）触发账本「换主即重置」漏了**同 worker_id 回来**这一档

**plan 说**（P0-5 / §4.1-19）：「`begin_auto_handoff` 发现新 `worker_id` ≠ 记录的 `claimed_by`
（或本拍刚判 stale 接管）⇒ `reset_auto_handoff_triggers(course)`」。

**事实**：`hub/task_pack.py:111-115/304-317`：`give_up` 只看 `count >= 3`，**无时间衰减**；触发账本进程内
（hub 重启才清）。`.worker-id` 持久在 `<work>/.worker-id`（本轮改动列表里就有 `battle-offline/.worker-id`），
同一台机重开会话沿用同一 id ⇒ 换主判据不命中，A 回来照吃 `give_up`
（`remote/offline_boot.py:1351-1356` → `gave_up` skip）——正是 plan 自己写的根因形状，只是换了主角。
「或本拍刚判 stale 接管」这句没写死是否覆盖「同主、窗口已过」的情形。

**建议**：把「新一轮交接」判据写死为 **换主 ∨ 距上次 claim 超 `AUTO_HANDOFF_PENDING_SEC`**（或给触发账本加
时间衰减），T5b 旁补「同主重试」用例；若有意保留同主不回血，也在 §3.2/§9 写明理由。

### F5（P0-11 措辞）墓碑 holder 的形状没定死

**plan 说**：P0-11「`offline_leases()`/`holder_info()` 加 `beat_at/silent_sec/stale/revoked` **并滤掉墓碑**」；
P0-1 加 `holder_stale`/`holder_revoked` 两个形参；P0-3「stale/墓碑 holder 标 `claimable=true`
（把两 flag 递进 `auto_claimable`）」；§3.7「`reason` 文案新增 `held-stale: ...`」；§3.7 又要求
`/admin/offline` 报 `revoked`。

**矛盾点**：若 `holder_info` 对墓碑返回 `None`（「滤掉」的最自然读法），`offline_tasks` 里
`holder_present=False` ⇒ `holder_revoked` 形参**永远无人传 True**（P0-1 的新档形同虚设）；
若不滤，`/admin/offline` 的 holders 又会把墓碑当持有人列出。二者只能选一（同一个函数两个方向）。

**建议**：定死为「`holder_info` **返回**墓碑（带 `revoked=True`）⇒ 清单能渲染 `held-revoked`、两个 flag 是真
消费；`offline_leases()` 面向 `/admin/offline` 保留 revoked 条目（带字段，便于排障），不做静默删除」。
补一条「墓碑行在清单里的 reason/claimable 形状」用例。

### F6（§3.1 / P1-C）authority 派生对「冷课 × `!pinned` 记录」与「停课」无定义；用例矩阵缺 `online+pin=false`

**plan 说**：authority 派生自 `pinned + mode + 开课标记`；三分档（pinned_online / pinned_offline / auto）+
`stopped` 正交；「冷课**有记录**仍按记录派生」。

**事实**：`pinned_offline` 要求 `pinned` **或**无记录；`auto` 要求 `course in _stores` ⇒ 冷课记录为
`{mode: online, pin: false}`（开课在在线的常态）或 `{mode: offline, pin: false}` 时**无档可落**；
停课课（标记不在、记录 `mode=offline, pin=false`）按三分档落 `auto`，若该值直接进 `/admin/queue` 读面会
给出误导标签（除非标记真的参与派生——plan 的两种说法并存）。P1-C / §6.3 的矩阵只覆盖
`{无记录, online+pin, offline}`，`online+pin=false` 缺。

**建议**：补一张**全组合小表**（cold × {无记录, online+pin, online+!pin, offline+pin, offline+!pin}，
in-table stopped × pin/mode），逐档给出 `authority / claim / claimable / auto_handoff_allowed`；
冷课 `!pinned` 建议按 **mode** 派生（online ⇒ 不可领、offline ⇒ 可领，均无导包能力），或明确「维持今天按离线」
并在 §3.9/§9 写死；用例矩阵同步补齐。

---

## 3. 守卫与小项（不阻塞，但别漏）

1. **P0-6 文件归属**：②（`set_mode_pinned` 的 `drop_jobs` 收敛）住 `hub/queue_offline.py`，文件列只写
   `hub/store_ledger.py` 会漏改点（①的「文件列 = 只改 store_ledger」是对的）。
2. **P0-7 守卫批量**：除 `STATE_WRITERS['_leases']` 外，`tests/hub/test_hub_queue_split.py` 的 `DOMAINS`
   成员表（queue_offline 新方法要登记）、`QueuePeer` 纯声明数与 `len(funcs)` 断言、以及新增 import 边
   （`ALLOWED_IMPORTS`）都要同批更新——守卫是「多一个成员即红」，漏了会卡在实施中间态。
3. **P2-5 预期**：`test_admin_courses_get_reports_pin_and_claim_state` 的 `st3==400` 依据是「legacy
   `pin=None` 拒覆盖 claim 翻的 offline」，本 plan 未改这条 ⇒ 大概率**保持 400**；别按「会变」直接改期望，
   先复核（新行为只动 `pinned`/清理字段面）。
4. **心跳要刷新 `beat_at`**（P0-2 未逐字写明）：只续 `expires_at` 不更新 `beat_at`，会把持续心跳的活主
   判成 `stale` 而被接管。`lease_verdict` 的 stale 腿与 `heartbeat_offline` 的写入两侧都要进 P0-1/P0-2 验收。
5. **pinned_online 不在默认清单**（`offline_task_courses` 收口后）：云机「空队列自解释」日志会少这一档
   解释；要么行照发（`claimable=false`、`reason=pinned`，与 §3.7 文案呼应），要么在 §3.7 写明「有意隐藏」。
   T2 的 `resolve_courses` 空表断言两种口径都成立，不受影响。
6. **`release` 在 `revoked` 下的 token 语义**：§3.3 表写 release⇒200（清墓碑），但既有 `release_offline`
   是 token 比对（不符 ⇒ foreign 409）；需明确墓碑下是否免 token、谁可清。
7. **冷课 `_dispatch_path` 的兜底**：冷课（∉ stores）需要 `traj_root()`，单课程 `--job-root` 模式（无 discover
   root）会 `ProtocolError`——`authority_of` 对冷课要有「不知道就退回缺省」的短路，别把 500 带进清单。

---

## 4. 复验成立（前五轮项抽查，全部落回真代码）

- 四轮 P0-1：`lease_verdict` 签名 `(now, rec, worker_id)`、住叶子 `task_pack.py:234`、六态顺序可实施；
  token 只在 claim/heartbeat 内分流——成立。
- 四轮 P0-2 / 三轮 R4：`queue_state` 每课行现无 authority、dashboard 经 `course-overview.ts:421`
  `input.queue?.courses[course]` 建行、`hubCache` 5s SWR、`HUB_PROBE_TIMEOUT_MS=1200`
  （`stack/hub-admin.ts:71`）、两套词表（`coursePills` / `matrixStatus`）——成立。
- 四轮 P0-3：`_rollout_source` 显式值短路 rl-config（`trainer/loop_transport.py:106-114`）、
  `resolve_collect_mode` 的 `source=='run' or seg`（`worker/loop_round.py:107`）、`train-mode.ts` 的
  `delete row.run_iters`——成立。
- 五轮 P0-A：`auto_claimable` 现有调用为关键字形参（`tests/hub/test_auto_handoff.py:152-163`），
  加带默认值形参不破既有用例——成立。
- 五轮 P0-B 根因：触发账本只在 `hub/offline.py:468` / `queue_offline.py:401`（有包腿）重置；
  `_claim_without_pack` 不传 worker、`begin_auto_handoff` 不写 `claimed_at`——成立（修法见 F4）。
- 五轮 P1-C：`mode_of` 未登记课回 ONLINE（`queue_scope.py:210-211`）、`_dispatch_load` 缺省 ONLINE
  （`queue_offline.py:473-480`）、`dispatch_record_default.pinned=False`——成立（组合表见 F6）。
- 五轮 P1-E：`resolve_courses` 返回 `list[dict]`、held 行 `return []`（`offline_boot.py:1529-1532`）、
  `_run_auto` 的「不占预算」分支在 `if tasks:` 内（`:1770-1778`）——成立。
- 五轮 P1-F：`flipped_at` 无清理入口（`set_mode_pinned` 只清 `claimed_offline`、`note_release` 只清
  `claimed_by/at`）——成立；**建议顺手把「人切离线」分支的 `flipped_at` 一起定**（P0-10 让 pinned_offline 可闻
  之后：旧锚点要么置新、要么明确沿用，否则老课一 pin 就假停摆）。
- 五轮 P1-G：`stall_verdict` / `auto_claimable` 都住 `queue_offline.py`（`:149` / `:127`），不在
  `task_pack.py`——成立。
- 五轮 P1-H：`bc_ledger.inflight_jobs` 集合差口径确认（裁决见 F1b）。
- 二轮 R3-a：`add_course` 无 `_sync_parked`（`queue_scope.py:146-165`）——成立。
- P2-2/P2-4 节号：`docs/nn/remote-transport.md` 现最大 §66、`docs/nn/console.md` 现最大 §28——成立
  （新节 §67 / §29 正确）。
- 行号抽查：`queue_offline.py:127/149/385/401/512/563/650`、`offline.py:399/468`、`store_ledger.py:200-206`、
  `loop_serve.py:937`、`test_auto_handoff.py:152/604/714`、`e2e/test_multi_course_single_hub_e2e.py:452`
  等与 plan 引用一致（±3 行内），未发现引错函数的情况。

## 5. 未验证项（评审边界）

- 未跑 pytest / 门禁（本轮是文档评审，无代码改动）；F1/F3 的行为链是静态追踪 + grep 坐实，建议实施时
  各先钉一条最小用例（F1：生产写者 republish；F3：停课 + offline 记录 + 包在）。
- R4-c 的「e2e 就绪断言 / `DISCOVER_SCAN_MIN_SEC=2s`」只做了结构核对，未逐帧复现面板抖动序列。
