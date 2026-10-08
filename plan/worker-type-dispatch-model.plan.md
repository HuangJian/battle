# plan：训练派发模型重构 —— 课程去模式化 + 自主/协作 Worker 两型

> **v3 修订版（2026-10-07）**：已并入 `plan/worker-type-dispatch-model.review-bf.md` 的
> F1–F15 逐条处置（见 **§1.6**；三条 P0 = 造包链 / 三钟收敛 / 打点归属层），并落地 **M1a**（§1.6 末）。
> **v2 评审修订版（2026-10-06）**：已并入 `plan/dispatch.review-hy.md` 的 Q1–Q5 用户裁决与 P0–P2 逐条处置
> （处置明细与两处事实性修订见 **§1.5**）。v1 曾位于 `.trae/documents/worker-type-dispatch-model.plan.md`。
> 关联：`docs/nn/remote-transport.md` §56–§68（现状）· `DECISIONS.md` §2026-10-03-goalnn-auto-offline-handoff /
> §2026-10-05-goalnn-offline-online-status-switch / §2026-10-06-goalnn-pull-replica-any-unfinished ·
> 评审 `plan/dispatch.review-hy.md`。

---

## 0. Context（为什么做）

今天的「在线/离线」是一等概念，横跨四层：

| 层 | 载体 | 文件 |
|---|---|---|
| hub | 课程模式 `_modes` + `offline-dispatch.json`（mode/pinned/claimed_offline/flipped_at）+ `authority_of` 五档 + `parked` 派发闸 + 离线租约 | `hub/queue_offline.py` `hub/task_pack.py` `hub/store_leases.py` `hub/admin.py` `hub/offline.py` |
| console | 每课模式意图表 + pin/交还自动 + 回灌 + 开课弹窗选模式 + 离线徽标/列 | `dashboard/src/server/actions/course-mode.ts` `train-mode.ts` · `web/view/course-{status,matrix}.ts` |
| trainer | rl-config `courses.<课>.rollout_src=run` + `run_iters` ⇒ `COLLECT_OFFLINE` ⇒ 本机等待 | `trainer/loop_plan.py` `loop_round_steps.py` `loop_transport.py` |
| remote | 自主盘领任务包（/offline/tasks → claim → 整段跑 → 回传） | `remote/offline_boot.py` |

痛点（本次重构一次清算）：

1. **模式语义过载**：同一状态被 mode/pin/authority 三套词汇描述（用户已裁决「课程不再区分在线/离线，更没有 auto」）；
2. **多机不能并行**：`_busy_locked`（`hub/queue_offline.py:948-980`）是**全局**一拖一——任何一门课被接管，其余课对所有云机都 busy；
3. **云机掉线 = 课程永久停摆**（§67 报障二）：租约挂着、模式停在 offline、本机等待永不恢复；
4. **掉线后没有自动回落**：人不在场时课程不回到「协作 PPO + 本机 rollout」这条腿。

新模型把三件事拆正交：**谁在跑**（worker 类型）、**归谁独占**（课程接管 hold）、**还活着吗**（进度活性）。
评审结论（已采纳）：方向正确；五个硬缺口（无包 claim、打点粒度、读/写同源、旧端兼容、本机恢复写者）在 v2 已逐条闭合。

---

## 1. 新模型定义

### 1.1 术语

| 术语 | 定义 | 现状对应 |
|---|---|---|
| **自主 worker** | 跑 `battle.offline.ipynb`（`remote/offline_boot.py`）：按课领取整段，云端自主跑 rollout+PPO，逐轮回传 | 旧「离线盘」（role=offline） |
| **协作 worker** | 跑 `battle.cloudflared/tailscale.ipynb`（`remote/worker.py`）：只领 PPO / BC 作业 | 旧「在线盘」（role=online） |
| **接管（hold）** | 自主 worker **领取成功且任务包在盘**后的**课程级独占**；只认一个持有者 | 旧「离线租约 + 翻 mode」 |
| **进度信号** | `/offline/artifact`、`/offline/result`、**轮内完成事件打点** `/offline/progress`、BC `/jobs/{id}/epoch` | 新增（心跳不算） |
| **掉线** | 接管后 **900s 无任何进度信号** ⇒ 判掉线（惰性判据，谁读谁算） | 旧 180s 心跳静默 + 900s TTL |
| **pending_export** | 「有人正在给这门课导包」的**软态**：不建 hold、不压下、不停本机、不撤单 | 旧 `begin_auto_handoff` 的翻 mode 半边 |

**wire 命名不变**（兼容窗口与已刷新客户端）：`/offline/*` 端点名、`role=offline|online`、`--offline`、`X-Battle-Offline`、CFG 键全不动；只改文档/UI/注释口径为「自主/协作」。代码常量名保持 `ROLE_OFFLINE/ROLE_ONLINE`（`common/manifest.py` 加映射注释）。

### 1.2 不变量（六条）

1. **课程无模式**：hub 删 `_modes`/pin/authority；console 删意图表与回灌；trainer 的等待不再读 `rollout_src=run`/`run_iters`。
2. **接管只在「包到手」后建立**（Q1）：claim 时包不在盘 ⇒ 不建 hold，走 409 `pending_export`（软态，见 §1.3）。
3. **接管的解除**：`release`（正常交还）/ 进度掉线（惰性，900s）/ 人工 `revoke`。★`takeover=1` 只能覆盖 `stale/revoked/expired`，**不能覆盖 live**（覆盖 live 必须先 revoke）——否则本条是空话（P2-4）。
4. **活性只认进度信号**；心跳（60s，独立线程，`offline_boot.py:1431`）只续租约 TTL，**不作活性**（§68 假活教训）。
5. **掉线 = 惰性判据**：hub 派发闸 / `/offline/tasks` 读面 / 本机 held 派生，三处同源。
6. **压下只在进度新鲜时**：hold live ⇒ 该课对协作 worker 不可派 + 本机停跑；过期 ⇒ 自动恢复（新自主盘可 stale 接管）。

### 1.3 状态转换表（课程视角）

| 状态 | 进入 | hub 派发 | 本机 trainer | 备注 |
|---|---|---|---|---|
| 在训·无接管 | 开课（无模式参数） | 正常派 PPO 给协作盘 | 跑 rollout + 发布 | — |
| **导包中**（pending_export，**软态**） | claim 遇缺包（Q1） | **照常派**（不占任何闸） | **照常跑** | 只服务：①别的盘知道「有人在导包」（排序靠后，不是闸）②`offline_stalled` 的 `pending-export` 锚点 |
| 接管中 | claim 成功 ∧ 包在盘 | 该课**压下不派**（含 §68 备份副本腿） | **held 等待**（轮边界生效） | 导包完成前不会到这一档 |
| 接管掉线 | 进度静默 ≥900s（惰性） | **自动恢复**派发（在队任务照常可领） | 自动恢复 | 见 §1.5-P0-5（双通道） |
| 正常交还 | 段末跑满 / release | 恢复 | 恢复 | 段末摘要链不变 |
| 停课 | 删开课标记 | 不可领（唯一 opt-out） | 按停课既有语义 | 在跑 hold 不杀 |
| 强制解除 | 人工 revoke | 同掉线 | 同掉线 | 控制台钮 |

### 1.4 取舍 D1–D4（v2，评审后定稿）

- **D1 活性 = 结果 + 轮内完成打点；心跳不算**。打点必须**下沉到轮内**（Q2 硬要求）：由 rollout 内的**完成事件**驱动（每 N 局完 / 每 M 秒检查点，M ≤ 300s），而非轮边界。慢轮保活、卡死轮判死（见 §1.5-P0-2 修订）。
- **D2 接管期间队列任务「压下不撤单」**：掉线后即可被协作盘领取（贴合「重新派发」字面；与 §68 同族）。撤销 `_drop_unsettled` 的自动调用；`pending_export` 期间同样不撤单。
- **D3 一拖一放 hub、按 worker**（Q3）：判据 = 「本 worker 无别的 live hold ∨ live BC 作业」；**清单必须带 `?worker=`**，缺省时 `claimable` 是**上界**（不含一拖一），语义写进 §69。
- **D4 BC = kind=bc 队列作业**（Q5）：两种 worker 均可领（仅角色闸豁免）；**领取后独占**（不发备份副本）；900s 无 epoch ⇒ 让出；**领 BC 算 drain**——一台自主 worker 同一时刻至多占一样（hold 或一个 BC 作业）。

### 1.5 裁决与评审处置（2026-10-06）★

#### 1.5.1 用户裁决 Q1–Q5（作为落地指令，全部采纳）

| # | 裁决 | 落点 |
|---|---|---|
| Q1 | **无包 claim 不建 hold**，导包走独立软态 `pending_export` | §1.3 + M1b（claim 门 / `offline-dispatch.json` v2 加 `pending_export:{by,at}` / 清单行字段，`state` 仍为 `no_pack` 不新造状态码） |
| Q2 | **掉线阈值维持 900s**；打点下沉轮内 / 落盘节流 / 误杀回归用例 三条升级为**硬要求** | M1a（`note_progress` 节流）+ M3（轮内打点）+ §4 守门用例 1 |
| Q3 | **一拖一放 hub**（清单带 `?worker=`，缺省上界语义写文档） | M1b（claim 闸 / 清单）+ M3（`fetch_task_list`/`resolve_courses` 带 worker） |
| Q4 | **旧云机不兼容，先刷新 notebook**；hub 必须**显式拒**旧客户端 | M1b（claim 加 `?proto=2`）+ M6（兼容矩阵改写） |
| Q5 | **领 BC 算 drain（互斥）**；`role_blocked` 分支顺序定死 | M5（顺序：①bc 只豁免角色闸 → ②`job_role != role` 拒 → ③课程 hold 闸**所有 kind 都吃**） |

**Q1 的精确理由**（评审"冻结 900s"的说法略重，真问题是**过早 hold**）：若 claim 即建 hold，则在导包的数分钟里
「无人能跑却已压下协作派发 + 已停本机 + 900s 进度钟已起走」——hold 的语义（"有人在跑"）被透支，
导出失败还要再等 900s 才自愈。⇒ hold 推迟到「包到手」建立；导包期本机照跑、协作盘照领（**这是必付代价，写进 §69**，
不要为它另开"停本机"通道——那会破坏"接管唯一来源 = claim"）。

#### 1.5.2 P0 五项（含两处对评审的事实性修订）

| # | 评审结论 | 处置 |
|---|---|---|
| P0-1 无包 hold | 语义空档成立 | **采纳 = Q1**；`AUTO_HANDOFF_PENDING_SEC(900s)` 与 `HOLD_PROGRESS_STALE_SEC(900s)` **巧合同值、语义不同，禁止合并常量**（调一个误伤另一个） |
| P0-2 打点粒度 | 缺口成立，**但两处措辞需修订** | **采纳并修订**（见下） |
| P0-3 读/写同源 | 成立 | **采纳 = Q3**（hub 闸 + `?worker=`；不采纳云机侧自律） |
| P0-4 旧端兼容 | 成立（旧码兜底 `held` ⇒ 照旧跑，`remote/offline_boot.py:1373-1379` 已复核） | **采纳并修订**键选择（见下） |
| P0-5 本机恢复写者 | 成立：控制台不在场 ⇒ held 无人清除 ⇒ 停摆原样 | **采纳并加固**（见下） |

**P0-2 修订一（事实）**：评审称「单轮 = 数千局 + PPO，几十分钟量级」——与实测不符：
`docs/nn/remote-transport.md` §60 记录 Colab TPU 单轮 176 局 ≈ 34.9s（p50 0.52s/局）。现有证据下**单轮是秒~分钟级**。
**修订二（判据）**：评审的误杀回归用例「单轮 wall_sec > 900s ⇒ 仍判 live」若按字面实现，会把
「整轮零完成事件挂死十几分钟」（2026-10-06 两条事故的**原形**，`DECISIONS §rollout-io-ceiling`）也保活——与 §68/用户口径相悖。
⇒ 回归用例定为：**单轮 wall_sec > 900s ∧ 轮内每 ≤300s 有完成事件 ⇒ live；整轮零完成事件 > 900s ⇒ 判掉线**。
采纳的其余两条：`note_progress` 落盘节流（距上次写盘 <60s 只改内存，hub 重启最多丢 60s 龄，P2-2 宽限吸收）；
M0 前置读数（单轮 P50/P95 `wall_sec` 从既有回传的 `iters[].wall_sec` 统计，写进 §69；**若 P95 > 600s 必须重开 Q2 裁决**——不许默默改常量）。

**P0-4 修订（旧端拒收的兼容键）**：评审候选是 `completed:true`（旧码会「本拍不跑」但日志误导且静默）。
改为 **`busy:true` + `error` 全文**：旧码的 `busy` 分支会把 `error` 原样打进旧会话日志
（`offline_boot.py:1360`「别的课正在跑（一拖一）：{error}」）⇒「请刷新 `battle.offline.ipynb`（派发模型已改为 hold v2）」
**能真正到达现场**；且旧码走 blocker 路径（不跑、不耗 idle 预算），绝不静默双跑。响应体同时带 `proto_required:2` 供新码判读。

**P0-5 处置（采纳并加固）**：本机解锁改为**双通道**——
① **trainer 惰性问 hub**：新只读端点 `GET /offline/hold?course=`（鉴权同现有；trainer 侧用现成的
`runtime.hub_url`/`token` + `remote/hub_http.py` 的 HTTP 助手，`trainer/loop_serve.py:240-296` 已在用同一套凭据），
**每轮边界一次、短超时（≤3s）**；
② **控制台文件通道降级为兜底**：`tmp/loop-control.json` 的 `held` 条目**带 `last_progress_at` 时间戳**，
trainer 用**同一条 900s 规则**就地判定是否仍 live（**加固点**：控制台进程死掉时，冻结的文件会**自行过期**，
不会把本机永久钉在 held——纯并集做不到这一点）。
生效 = `hub_live ∨ file_live`（并集，任一持有 ⇒ 停）；hub 不可达 ⇒ 退文件通道；都不可达 ⇒ 保守方向与
`loop_control.py:12-22` 既有契约一致（读不到按无意图=继续跑），半状态由停滞告警兜。

#### 1.5.3 P1 八项

| # | 评审结论 | 处置 |
|---|---|---|
| P1-1 `end_it` 盖章不该被活性门控 | 成立（盖章是**事实**；跨过 900s 不盖章 ⇒ 从已完成起点重跑，代价大两个数量级） | **采纳**：盖章**无条件**（身份校验 `_end_seal_ok` 照旧）；活性只控制「是否 advance 活动权重 / 是否判 duplicate」 |
| P1-2 取包三门的替代判据 | 成立（`hub/offline.py:127-172` 三个 mode 门随模式一起删后，包会「在盘就发任何人」） | **采纳**：取包门 = 「**无 live hold ∨ 持 lease 且 token 相符**」（live hold 时非持有者 409；旧客户端在别人持 hold 时也拿不到包 = 旧端兜底第二道闸）。显式点名（`CFG.course`）的客户端**统一先 claim 再取包**（M3）；`/offline/resume*` 不加此门（只读锚点），§69 写明 |
| P1-3 启动参数 mode | 成立（`hub/boot.py:219` `--course a:offline` 形态） | **采纳**：保留解析、**WARN + 忽略**（不 400，别 brick 老启动脚本）；`/admin/courses` 行给 `mode_ignored:true` |
| P1-4 守卫用例会红 | 成立（已复核 `tests/hub/test_role_routing.py:225/230/231`；`test_hub_queue_split.py:149/288` 的方法名清单） | **采纳**：M1c 单列「受影响的守卫用例清单」，逐条标【改断言/删守卫/换形状】，不许顺手改掉语义（清单见 §3-M1c） |
| P1-5 迁移面低估 | 成立（本仓实测：模式/权威符号 **499 处 / 36 文件**，rg 见 §3-M1a） | **采纳**：M1a 第一步跑符号普查、按文件标【删/改名/改判据】，作为 M1b/M1c 的施工单 |
| P1-6 D3×M5 交叉 | 成立 | **采纳 = Q5**；补一句：同课程同时有 hold 与 BC 作业在现实中不会发生（课程 kind 唯一），该规则是**防御性**的，但必须写死防将来 |
| P1-7 rank 反转 + `not_offline` 消失 | 成立（v1 把 `claimed(2)` 与 `no_pack(3)` 顺序无声对调了） | **采纳**：**保留现值序**（`ready 0 / stale 1 / claimed 2 / no_pack 3 / not_offline 4 / completed 5`；★v3-F4 修正——v2 引漏了 `not_offline` 并把 `completed` 写成 4，实际值见 `hub/task_pack.py:121-126`，理由注释在 `:116-119`） |
| P1-8 文档编号 | 成立（`docs/nn/console.md` §32 已被 2026-10-06 回放导出选择器占用） | **采纳**：console.md 新节 = **§33**；remote-transport.md §69 空号可用。★2026-10-08 rebase 后两侧撞号（上游同日也写到 §69/§70/§71）⇒ 本 plan 的两节实际落为 **§72** / **§35** |

#### 1.5.4 P2 八项

| # | 处置 |
|---|---|
| P2-1 两个钟 | **采纳**：统一为「**任何合法接触（心跳 ∨ 进度）都刷 TTL**；只有进度刷 `last_progress_at`」；`lease_verdict` 顺序仍是 `expired → revoked → mine → stale → foreign`（expired 是 stale 的子集）。§69 画四象限真值表：心跳活×进度新=live；心跳活×进度老=**stale（§68 防线）**；心跳死×进度新=不可能（进度刷 TTL），防御判 live；心跳死×进度老=expired |
| P2-2 重启宽限 | **采纳**：恢复时 `last_progress_at = max(盘上值, now − GRACE)`（GRACE=300s）+ 一行 `hold-restored` 日志；防「刚重启就把活着的 worker 判掉线」 |
| P2-3 `expires_in` | **采纳**：`hold_info` **保留 `expires_in`**（值改「距判掉线的剩余秒」= min(进度余量, TTL 余量)），形状不变——`hub/offline.py:547` 与 `offline_boot.py:1374` 现成读方一行不改 |
| P2-4 `takeover=1` | **采纳**：见 §1.2-③ 不变量 3 |
| P2-5 worker 类型展示 | **采纳**：优先级 = 持 hold ⇒ 自主；否则按最近一次露面面（`offline_disk` 报名 ∪ peek/claim 登记） |
| P2-6 文件名口径 | **采纳**：全文用真实名 `offline-dispatch.json`（`queue_offline.py:74` `DISPATCH_NAME`） |
| P2-7 `active_courses` 抖动 | **采纳**：保留为观测口径「在派发课程数 = 非 live-hold 且有活」；§69 写明被 hold⇒0、恢复⇒1 的抖动范围 |
| P2-8 M1 太大 | **采纳**：M1 切 **M1a（判据+双写，旧语义不动）→ M1b（切消费点）→ M1c（删旧符号）**，每步独立绿、独立提交 |

#### 1.5.5 保留项（重写时别丢，来自评审 A1–A5）

活性不认心跳 · 惰性判据「谁读谁算」（不引入清理线程）· 多机并行是真痛点 · wire 名一字不动 ·
`role_blocked` 仍住唯一咽喉点 `_claim_locked`（`store_leases.py:354`；四条认领面天然同源）。

#### 1.5.6 本次对评审的复核记录（行号已逐条验证）
`offline_boot.py:1349-1383`（旧码 409 兜底 `held`⇒照旧跑）· `offline_boot.py:1431`（心跳独立线程）·
`offline.py:127-172`（取包三门）· `offline.py:530/547`（takeover / expires_in）· `boot.py:219`（parse_course_arg）·
`test_role_routing.py:225/230/231` 与 `test_hub_queue_split.py:149/288`（守卫）· `console.md:10`（§32 占用）·
`plan_run.py:514/524/596-605`（轮循环 + wall_sec）· `loop_serve.py:240-296` + `remote/hub_http.py:190`（trainer 已有 hub HTTP 凭据通道）·
本仓符号普查 499 处/36 文件（v1 的迁移面确实低估）。

### 1.6 v3 修订：`review-bf` 的 F1–F15 逐条处置（2026-10-07）★

> 评审：`plan/worker-type-dispatch-model.review-bf.md`（基准 `b6211dea`，只读对账）。
> **三条 P0 是同一种形状**：造包 / 活性 / 打点三条链各有一半住在**旧代码或包内代码**里 ——
> 删 hub 符号删不掉它们，失败时还是静默的（新 hub 自洽、现场照旧）。

| # | 级别 | 定案 | 落点 |
|---|---|---|---|
| F1 | P0 | **保留 `/api/autoOfflineHandoff`，语义改成「只导包」**：`applyTrainModeToConfig(c,'offline')` 随 `train-mode.ts` 一起退役 —— 该 action 今天是「导包 + 停本机」两条腿，后者正是 Q1 花半页要拆掉的。另两条路**明文否决**：整条退役 ⇒ `pending_export` 永远无包（`offline_stalled` 锚点与触发账本全部落空）；保留停本机 ⇒ 与 Q1 并存即自相矛盾 | M4（`course-mode.ts::autoOfflineHandoff` / `server/api/route.ts:378` / `autoBundleDecision`）+ M2（写面退役）；M6 补「hub 触发链仍通」的判据 |
| F2 | P0 | **三钟收敛成一条**：`lease_verdict` 的 stale 腿在 M1b 改读 `last_progress_at`（阈值 `HOLD_PROGRESS_STALE_SEC`）；`OFFLINE_LEASE_STALE_SEC`（180s）/ `offline_lease_stale_sec()` / `BCITY_OFFLINE_LEASE_STALE_SEC` 在 M1c **删**（e2e 改指 `BCITY_HOLD_PROGRESS_STALE_SEC`）。补用例「心跳活 ∧ 进度静默 200s ⇒ 不 stale ∧ 不可接管 ∧ 本机仍 held」 | M1a 已立新判据（只认进度）；M1b 切判据；M1c 删旧常量 |
| F3 | P0 | **打点落 `remote/offline_boot.py`**（每会话从 GitHub raw 刷新、与 `proto=2` 同源；它已持有 lease token + hub 地址），`remote/plan_run.py` 只把轮内完成事件上报到这一层。**不用** `proto=2` 去绑包内代码 —— 它认不出旧包（`code.zip` 优先级：产物目录 → 包 → hub `/code`） | M3 改写（打点层）；M6 §69 补「刷 notebook ≠ 换 worker 代码」 |
| F4 | P1 | `TASK_STATE_RANK` **照抄六项**（`not_offline:4` / `completed:5`） | §1.5.3-P1-7 已就地修正 |
| F5 | P1 | **dashboard 侧也做普查**（实测 435 处 / 26+ 文件，含 6 个测试 178 处）+ 逐文件处置表；`stack/specs.ts::trainModeKnobs` / `resolveRolloutSrc` 明确**删**（`rollout_src` 域随模式一起退役） | M4 前置步骤（与 M1a 的 hub 普查并列） |
| F6 | P1 | **走 hub 侧闸**（不采纳客户端自律，与 P0-3 同口径）：drain 判据进 `_claim_locked`（它有 `worker_id`），`role_blocked` 扩成 `(job_id, role, worker_id)`；M1c 的守卫处置从「形状不变」改「改断言」 | M1b（`hold_meta` 同步）+ M5；§3-M1c:225 行同步改 |
| F7 | P1 | `not_offline` **与 `stopped` 收口**：字面量保留（云机 `blockers` 名单 / `?include=all` 读面 / 409 文案不动 wire），派生判据只留「停课（标记不在）」——「冷课带 online 记录」随 `_modes` 一起消失 | M1b（派生）+ M1c（删 `AUTHORITY_NOT_OFFLINE`，理由写进 §69） |
| F8 | P1 | `pending_export` **读面惰性过期**（窗 = `AUTO_HANDOFF_PENDING_SEC` = 导包窗口），不写盘清理 | **M1a 已落地**（`pending_export_of`）+ 用例 |
| F9 | P1 | M0 增读：`ppo_sec` 占比 + 轮内**相邻完成事件间隔** P50/P95；样本口径排 anchor（`plan_run.py:453` 的 `wall_sec=0.0`）/ smoke / 中断轮 | M0 |
| F10 | P1 | 兼容矩阵收窄四段：claim 面被拒 / 取包面在 live hold 下被拒 / `CFG.course` 与 `CFG.task_zip` 两腿降级为现状 / 升级 = 刷 notebook **且**换新包或新产物目录 | M6 |
| F11 | P1 | `_busy_locked` 逐条处置：腿① 改按 worker 排除自己；腿②（别课「交接中」）**整条删**（它正是 Q1 要废的占闸腿，常数只留给 `offline_stalled`）；两腿的 `authority_of` skip 随 authority 删除 | M1b |
| F12 | P2 | 新端点落点补齐三处：`common/protocol.py` 路径常量 · `hub/http_face.py` 路由 · 分域守卫（`DOMAINS` / `STATE_WRITERS` / `ALLOWED_IMPORTS`）；`hold_meta` 是新的跨域写者，必须登记 | M1b |
| F13 | P2 | `loop-control.json` 承载 hub 事实的三个前提写死：版本/回执形状 · 旧 trainer × 新文件的宽容 · **同机时钟前提**（`tmp/` 共享）；注明「`held` 是 hub 事实的缓存，不是第二事实源」 | M2 + §69 |
| F14 | P2 | DoD#2 保留现值措辞，但 §69 写明**清单是上界**（含并发窗口），守卫只在无并发下跑 | M1b + §69 |
| F15 | P2 | 小项：普查数字按当日重跑并记差值 · `plan_run.py` 补路径（住 `remote/`）· `authority_of` 住 `queue_offline.py` · `advance_active` 现状（`hub/offline.py:315`）· `tests/test_production_isolation.py` 进 M1c 守卫清单 · `_ask_console_freshness` 复核成立 | M0 / M1a / M1c |

**M1a 落地记录（2026-10-07，本仓）**：

- `hub/task_pack.py`：`HOLD_PROGRESS_STALE_SEC=900`（env `BCITY_HOLD_PROGRESS_STALE_SEC`）·
  `HOLD_RESTORE_GRACE_SEC=300` · 纯判据 `hold_progress_at` / `hold_touch_at` / `hold_state`
  （**只认进度，零进度 = stale**）/ `hold_restore_grace` / `hold_expires_in`（= min(进度余量, TTL 余量)）。
- `hub/queue_offline.py`：`DISPATCH_VERSION=2`（`hold` / `pending_export` **与旧字段双写**）·
  `hold_record_merge` / `pending_export_record_merge`（宽容归一，全空 ⇒ 无）·
  `note_hold`（清软态、claim 记第一个进度锚）· `note_progress(course, token=…)`（内存每拍
  更新、落盘节流 `HOLD_PROGRESS_PERSIST_SEC=60`、无 hold 时无操作、**换主门**：旧主的 ping
  不复活旧 hold —— 自查时发现的并发面，`_dispatch_update(guard_hold_token=…)` 上锁内重查）·
  `note_pending_export` · `hold_of` · `pending_export_of`（超窗惰性过期）· `_dispatch_load`
  的**恢复宽限 + `hold-restored` 日志**。
- `hub/queue_peer.py` 声明面 +5；守卫 `tests/hub/test_hub_queue_split.py` 同步
  （域成员 121 / 实现名 123 / 声明 85）；用例 `tests/hub/test_hold.py`（11 例，先红后绿）。
- 门禁：`bash tools/githook/nn-python-gate.sh` 全绿（ruff + mypy + 3752 passed / 9 skipped）。

**v2 → v3 对 hold 字段集的一处细化**：v2 的 `{worker_id,token,at,last_progress_at}` 不够算 P2-3 的
「TTL 余量」（心跳会把它推着走）⇒ 追加 **`touch_at`**（最近一次合法接触：心跳 ∨ 进度）。
读数口径定死：**进度只进 `last_progress_at`，任何接触都进 `touch_at`** —— P2-1 的四象限真值表
按这两个字段读。

---

## 2. 需求逐条映射（用户 0–8）

| # | 需求 | 落点 |
|---|---|---|
| 0 | 课程不区分在线/离线、无 auto | M1（hub 删模式/authority）+ M2（trainer 换 held 通道）+ M4（console 删意图/徽标/开关） |
| 1 | worker 两型（notebook 决定） | 已是事实（role 由 notebook 决定）；M4 加类型展示；M5 补齐自主腿 BC 能力 |
| 2 | 开课不再指定模式 | M4（`openCourse` 去 `trainMode`）；M1b（hub `/admin/courses` 模式 POST 400 退役；启动参数 WARN+忽略） |
| 3 | 课程可整课领；协作领最新 it PPO | M1b（清单/claim 去 gate：所有在训课可领，包到手才建 hold） |
| 4 | 接管后不派该课 PPO 给协作（含备份副本） | M1b（`role_blocked` 课程 hold 闸，住 `_claim_locked` 咽喉点） |
| 5 | 15 分钟无回传 ⇒ 掉线 ⇒ 重新派发 | M1（惰性判据 + 派发自动恢复）+ M2（本机双通道恢复）+ M3（轮内打点） |
| 6 | 自主 worker 重新上线可再抢任何在训课 | M1b（claim 去 authority/pin；stale 自动接管；`takeover` 不得覆盖 live）+ M3（清单简化 + `?worker=`） |
| 7 | BC 两型均可领、领取后独占、15 分钟让出 | M5（含 drain 互斥与 `role_blocked` 顺序） |
| 8 | dashboard UI/UX 调整 | M4 + M6 |

---

## 3. 实现分解

### M0 — 前置读数（开工前，一次性）

- 从既有离线段回传结果（`iters[].wall_sec`，落点 `<job_root>/offline/<run_id>/…`）统计各课**单轮 P50/P95**，
  写进 `docs/nn/remote-transport.md §72`（原计划 §69，rebase 撞号后上移）；**P95 > 600s ⇒ 重开 Q2 阈值裁决**（记录是唯一凭据）。

### M1a — hub 判据 + `offline-dispatch.json` v2 双写（旧语义不动）★ **已落地 2026-10-07（见 §1.6 末）**

- 新增纯判据：`hold_state` / `hold_progress_at` / `hold_touch_at` / `hold_restore_grace` / `hold_expires_in`
  （`HOLD_PROGRESS_STALE_SEC=900`，env `BCITY_HOLD_PROGRESS_STALE_SEC`；★F2：**只认进度**，心跳不参与）；
  `note_progress`（**落盘节流 ≥60s**，内存每拍更新）；v2 schema `{v:2, …v1 字段并存…,
  hold:{worker_id,token,at,last_progress_at,touch_at}, pending_export:{by,at}}`
  （兼容读 v1：新键缺 ⇒ 空）；恢复宽限（P2-2）；`pending_export_of` 惰性过期（★F8）。
- **双写**：hold 与 `pending_export` 与旧 mode/pinned 字段并存；旧读方零变化。
- 第一步：跑符号普查（施工单）：`rg -n 'AUTHORITY_|authority_of|auto_handoff|pinned_of|_modes\b|mode_of|parked|COURSE_MODE|set_mode_pinned' nn-training`
  （v1 实测 499 处/36 文件），按文件标【删/改名/改判据】。

### M1b — hub 切消费点 ★ **已落地 2026-10-07（两笔：`d8a5a0e2` 消费点切换 + `427d8e21` 收尾）**

- `queue_offline.py`：`claim_offline` 门 = 开课标记 ∧ ¬completed ∧（无 live hold ∨ mine ∨ stale）∧（**本 worker 无别的 live hold ∨ live BC 作业**，Q3/Q5）∧ **包在盘**（Q1；缺包 ⇒ `pending_export` 路径，沿用 `_claim_without_pack` 形状 `hub/offline.py:649-707`）；
  `heartbeat` 续 TTL、`stale ⇒ 409`（新文案）；`release/revoke` 保留；`busy` 按 worker；`offline_tasks` 新行字段（含 `pending_export`、`holder`、`?worker=` 语义）；rank 保留现值序（P1-7）；
  `offline_stalled` 锚 `pending_export.at`（窗口 `AUTO_HANDOFF_PENDING_SEC`，独立常量）；`_sync_parked` 全链删。
- `store_leases.py`：`role_blocked` **分支顺序定死**（Q5）①bc ⇒ 只跳过角色闸；②`job_role != role` ⇒ `"role"`；③**课程 hold 闸所有 kind 都吃** ⇒ `"held:<worker>"`（`hold_meta` 由 `_HubQueue` 同步，时间戳自判活）；
  新增 `job_kind` 缓存（M5 的 BC 进度租约）；`_lease_state` 的 bc 进度孤儿态。
- `queue_claims.py`：备份腿排除 held 课（随 role_blocked 自动生效）与 bc。
- `offline.py`：claim 加 `?proto=2`（缺失 ⇒ **`busy:true` + `error` 全文 + `proto_required:2`**，见 §1.5.2-P0-4）；
  新增 `GET /offline/hold`（P0-5 只读面）与 `POST /offline/progress`；取包门换 lease 门（P1-2）；
  `artifact/result` 的 advance/盖章按 P1-1 分家（盖章无条件、advance 要 live+token）。
- `admin.py`：`/admin/courses` 行 = `{course, training, held, mode_ignored}`；模式 POST 400 退役文案；`release_hold=1` = revoke；
  `queue_observe.py` 行换 `held`；`boot.py`/`queue.py` 启动恢复 hold（`hold-restored` 日志）。
- **测试**（先红后绿）：`test_auto_handoff.py → test_hold.py`（判据全组合 / claim 三态 / `?worker=` 同源 / 无包不建 hold / 掉线恢复 / takeover 不覆盖 live / 重启恢复）·
  `test_priority_schedule.py`（备份腿不发 held 课与 bc）· `test_offline_task_pack.py`（行字段 + 取包 lease 门）·
  **守门用例**：`test_offline_task_queue.py` 加「带 `?worker=` 清单 claimable=true ⇒ 同参 claim 必成功」（同源契约，`queue_offline.py:12-14`）。

**★ 落地对账（2026-10-07）**：

- 上面每条的落点见两笔提交信息；**未按原文落地的只有一条**（有意）：`/admin/courses` 的
  mode POST **不改成 400 退役**（文案也留到 M4）——M1b..M3 期间控制台还在用那颗开关
  （`course-mode.ts` 到 M4 才删），提前 400 会把在线/离线切换打成哑的。退役文案随 M4 的
  UI 一起落。
- 补的三块（原文只写了判据、没写送信端）：`offline_advance_ok` 的四态（含保留的
  `pinned_online` 腿——权威退役时才删）；`pending_export.at` **首写为准**（否则云机轮询
  会把停滞锚点推着走 ⇒ T8 告警脑死）；导包窗 `auto_handoff_pending_sec()` 加 env 旋钮
  （e2e 不想真等 900s，与 `hold_progress_stale_sec()` 同一套做法）。
- 送信端（P1-1 的 `lease_token`）已通：`write_lease_file`（0600）→ `--hub-lease-file` →
  `run_loop` CLI → `open_run_context` → `OfflineDeliverer` 体。

### M1c — 删旧符号 + 守卫用例清单

- 删：`_modes`/`mode_of`/`set_mode*`/`authority_of`/`AUTHORITY_*`/`pinned_of`/`auto_*`/`parked`/`_sync_parked`/旧 `busy` 第二腿/`offline_tasks` 旧字段；
  `offline-dispatch.json` 落 v2（读侧兼容保留）。
**★ 落地对账（2026-10-07）——范围重划（有意，理由如下）**：

- **这一刀只删「已经没有任何消费者」的旧符号**：`parked` + `_sync_parked`（旧停摆闸；
  派发闸的第三层已是 hold）+ 它们的守卫用例。
- **`_modes` / `mode_of` / `set_mode*` / `authority_of` / `AUTHORITY_*` / `pinned_of` /
  `auto_*` 的实际删除并入 M4 那一笔**。为什么不能在这里删：它们的消费者包含**控制台的
  三颗钮与 `/admin/courses?mode=`**（`course-mode.ts` 到 M4 才删）——先删端点再删按钮，
  中间就是一段「按钮打了 404 / 意图静默失配」的窗口，而那正是 F5 类事故的土壤。
  判据：**一个语义的写入面与它的最后一个读方必须同一天消失**。
- 因此 M1c 之后、M4 之前的稳定态是：**派发路径零 mode/authority 读取**（闸只看 hold），
  而 mode/pin 仍作为「控制台意图」的存储与读面存在（它不再是派发判据）。
- `offline-dispatch.json` 已在 M1a 落 v2（读侧兼容保留）✓。

- **守卫用例清单**（逐条处置）：
  - `tests/hub/test_role_routing.py:225`（`blocked = self.role_blocked(...)` 必须在 `_claim_locked`）→ **改断言** ✓（M1b 收尾已做）；
  - `:230-231`（`parked` / `job_role != role` 两行）→ **改断言** ✓（M1b 钉了三分支顺序，M1c 删 `parked` 那一条）；
  - `test_role_routing.py:234-249`（`test_parking_flag_is_synced_from_course_mode`）→ **删** ✓，新用例
    `test_hold_mirror_is_synced_from_the_dispatch_record_on_startup` 代替（hold_meta 同步）；
  - `tests/hub/test_hub_queue_split.py:149/288`（方法名清单）→ **改清单** ✓（M1b 加 `job_kind`/`hold_blocked`/
    `_hold_stale_sec`/`_sync_hold`，M1c 删 `_sync_parked`：成员 126→125、实现名 128→127、声明面 86→85）；
  - **清单外但同源的四处**（普查施工单的收获）：`test_priority_schedule.py`（离线课对在线盘隐身
    → 改钉「接管中的课隐身」）· `test_auto_handoff.py::test_discovered_course_inherits_parked…`
    （→ 改钉「发现/重启时从盘上恢复 hold」）· `e2e/test_multi_course_single_hub_e2e.py::
    test_offline_course_is_parked…`（→ 真端点 claim 取 hold + release 恢复）·
    `e2e/test_auto_handoff_e2e.py::test_hub_restart_keeps_discovered_offline_course_parked`
    （→ 重启后 hold 仍拦 peek + `/offline/hold` 读得到）。
  - `tests/test_production_isolation.py`（六轮 F15 点名）：不依赖 `_modes`/`parked`（它钉的是
    `autoOfflineHandoff` 的生产隔离）⇒ **不动**；M4 改 `autoOfflineHandoff` 语义时再看。

### M2 — 训练侧 held 通道（去 rl-config 模式）★ **已落地 2026-10-07（`701846cb`）**

- 控制文件 schema：`held: [{course, last_progress_at}]`（console 写、trainer 只读 + **就地 900s 自判活**）；
  `applied` 回执同步加 `held`。
- **trainer 直问 hub**（P0-5）：`GET /offline/hold?course=`，每轮边界一次、≤3s 超时、失败退文件通道；
  生效 = `hub_live ∨ file_live`。
- `loop_plan.py`：`course_is_offline` → `course_is_held`；`WAIT_OFFLINE` → `WAIT_HELD="held"`（文案「云机接管中：自主 worker 正在跑这段（15 分钟无回传自动恢复协作派发）」）；
  `worker/loop_round.py`：`COLLECT_OFFLINE/ROUND_OFFLINE_EXIT` → `COLLECT_HELD/ROUND_HELD_EXIT`；
  `loop_transport.py`：`ROLLOUT_SRCS` 退役 `run`——**容忍读**（残留映射为 `local` + 一行 WARN，不 brick 课程）；
  `loop_{round_steps,runner,lifecycle,serve,run_rl_cluster}.py` 逐字换词换源。
- 删 `dashboard/src/server/actions/train-mode.ts` 及调用点；`pruneStoppedCourseConfig` 保留。

**★ 落地对账（2026-10-07，`701846cb`）**：

- `trainer/loop_hold.py`（新）：`hub_held`（三态 `True/False/None`，只认 `held is True`；非 JSON/
  404/超时全消化）+ `file_held`（走 `read_control` 的 `held`）+ `course_held`（**并集**，缺省
  False = 本机照跑）；`course_key_of` 过 `course_key_from_path`（`--course` 收「名或路径」⇒
  不归一就会给本课写成 `x.jsonc`，**静默双跑**）；`HOLD_QUERY_TIMEOUT_SEC=3.0`。
- `trainer/loop_control.py`：`Control.held` + `parse_held`（窗常量现读 `hub.task_pack.
  hold_progress_stale_sec()`——**不在第二处抄一个**）+ `parse_control(raw, now=)`/`read_control(now=)`；
  `write_applied(..., held=)`；F13 三前提写进模块 docstring（版本/回执形状 · 双向宽容 · 同机时钟）。
- `worker/loop_round.py`：`resolve_collect_mode(source, seg, *, held=False)`（顺序 held > node >
  local；`run`/段长**不再**是「归云机」的同义词）；`COLLECT_HELD`/`ROUND_HELD_EXIT`。
- 换词：`loop_round_steps`（`HELD_LEG_HINT`/`HELD_WAIT_HINT`/`_held_note`）· `loop_lifecycle` ·
  `loop_runner`（`_map_outcome` → WAIT）· `loop_plan`（`WAIT_HELD`/`course_is_held`）· `loop_serve`
  （`_held_waiting`）· `run_rl_cluster`（只读视图：合成 args 带 `course=<课>`，走**文件通道**）。
- `loop_transport.py`：`ROLLOUT_SRCS=("auto","local","node")` + `ROLLOUT_SRCS_RETIRED=("run",)`
  + `_note_retired_source`（按课去重，CLI 与 rl-config 两条入口都容忍）；argparse 仍收 `run`
  （拒启 = brick 课程），help/注释标明已退役；`_warn_explicit_source_blocks_restore` 收窄到 `node`。
- 控制台：删 `actions/train-mode.ts` + `stack/specs.ts::trainModeKnobs`（唯一调用方即写面本身）；
  `course-mode.ts` 三条腿（`setCourseMode`/`unsetCourseMode`/`autoOfflineHandoff`）**不再写 rl-config**
  ——后者按 F1 收窄成**只导包**（hub 触发链仍能出包 ⇒ `pending_export` 有包可取）；
  `course-lifecycle::writeCourseConfigForOpen` 离线档不写盘、在线档只落显式 `rollout_src` 并
  **就地清退役键**（`run_iters` + 残留 `run`；清空则整条节点删）；
  `writeLoopControl` 只替补 `paused`（**不许抹掉 `held`**）+ `parseLoopControl` 宽容解析 `held`；
  `LoopWaitKind` 增 `held`（与旧 `offline` 同席——旧 python × 新 console 的共存读；词表重写属 M4）。
- **范围重划（有意）**：`held` 的**写方**（console 每次读 hub holds 的同一处写缓存）落在 M4——
  与 `overview.ts` 透出 `holds` 同席（同一份事实两次读 = 两个时刻的真相）。M2 只立住读侧 +
  「不覆盖」纪律 + 三前提；F13 因此标 **M2 部分 / M4 收尾**。
- 用例：新 `tests/trainer/test_loop_hold.py`（双通道/三态/超时形状/课程键归一）· `test_loop_control.py`
  （`held` 宽容与就地自判活、回执随 `held` 刷新）· `test_loop_round.py`/`test_offline_leg_retired.py`
  （held 分流、退役容忍读、WAIT 映射）· `test_serve_wiring.py`（控制文件通道端到端）·
  `e2e/test_loop_supervisor_integration.py`（接管等待不阻塞别课 + 解除即续跑）· dashboard 侧
  `train-mode-offline`/`course-mode`/`course-lifecycle`/`loop-control`/`rollout-src-launch-option`。
- 门禁：nn python gate **3787 passed / 9 skipped** · `bun run check` **2399 pass / 12 skip** ·
  `cd dashboard && bun run typecheck && bun run test` **1471 pass** · 三份 bundle 重建 ok。

### M3 — 自主腿（remote/offline_boot.py）★ **已落地 2026-10-07（`cfd9a4b7`）**

- 清单/领取：`fetch_task_list`/`resolve_courses` 带 `?worker=`；消费 `pending_export`（软提示：排序靠后，本拍不跑换下一门）；
  **claim 409 分流**：`held`(live) ⇒ **本拍不跑换下一门**（P0-4 后半）；`stale/revoked/expired` ⇒ 跑完当前段打包退出；
  显式点名（`CFG.course`）**统一先 claim 再取包**（P1-2 配套）。
- **轮内打点**（Q2）：`plan_run`/`iter_rollout` 的**完成事件**（每 N 局完 / 每 M 秒检查点，M ≤ 300s）→ 注入 `POST /offline/progress`；
  轮边界（开工/重试/上传前）为第二层；**绝不用独立线程打点**（那是心跳的教训）。
- 取包带 `?lease=`（P1-2）；notebook 说明书改「自主/接管」口径；`proto=2` 由新引导代填。

**★ 落地对账（2026-10-07，`cfd9a4b7`）**：

- **打点层（F3 定案：住 `offline_boot`）**：新 `remote/offline_boot.py::install_progress_pinger`
  （挂进 `sys.modules[PROGRESS_HOOK_NAME]` → 返回 `detach()`，其返回值 = 本段租约结局）
  + `progress_url` + `_progress_label`；常量 `PROGRESS_MIN_INTERVAL_SEC=240`（M ≤ 300s，且
  `3M ≤ hold_progress_stale_sec()`，用例钉影响力）/ `PROGRESS_TIMEOUT_SEC=5` / `PROGRESS_FAIL_LIMIT=3`；
  **绝不另起线程**（用例钉「装层前后线程数不变」+「没有完成事件就一枪不发」）。
- **上报口（快照侧）**：新 `common/progress_hook.py`（L0，只有 `HOOK_NAME="bcity_offline_progress"`
  + `hook()` + `report(kind, *, it, done, total, force)`；没注册者 / 注册者抛异常 ⇒ False，**绝不外抛**）。
  事件源两层：`worker/iter_rollout.py` **每结算一局**报一次（轮内那一层，长轮次的命根子）+ `remote/plan_run.py`
  的段开工 / 每轮开工 / 落盘+上传前 / 重试（轮边界那一层，`force=True` 顶开节流）。
  两边各一份常量（boot 不许 import 本仓代码）⇒ 用例双向钉等值。
- **409 分流**：`revoked` ⇒ 本会话**不再领这一课**（`run_one_course(progress=…)` 回写 →
  `_run_batch` 收进 `leases["revoked"]` → `_run_auto` 并入 `gave_up`，下一拍的清单跳过）；
  `expired`/`taken`/`unreachable` ⇒ 停打点、段照跑并打包（回传可能被判 duplicate）。
  装/卸时机：`run_one_course` 里**只在该段**（hub 可达 ∧ 领到租约）装，`finally` 卸
  （串行逐课，留着一个绑上一课的 URL 就会把下一段的进度报进别人账上）。
- **领取面**：`resolve_courses` 把 `pending_export` 行**排到最后**（软态不占闸 ⇒ 照领，但先挑能跑的；
  claim 必 409，走到它时记一笔 blocker 就换下一门）；claim 409 分流沿用 M1b 的 blockers
  （`held`(live)/`busy`/`completed`/`not_offline`/`pinned_online`/`proto` ⇒ 本拍不跑）。
- **行为变更（记一笔）**：`CFG.course` **点名腿也先 claim 再取包**（新 `_explicit_leases`）——
  该腿从此也吃 hub 的 409 门（停课 / 人固定在在线 / 被别人 live hold / 本段跑满 / 导包中 ⇒ 本拍不跑，
  日志逐档说明）；没有可用 hub 地址 ⇒ 空 ctx（纯离线 + 手动包，逐字照旧）。理由三条：P1-2 取包门看
  hold（不 claim 时**自己**上一段留下的 live hold 会把包锁住）、hub 侧「谁在跑这门课」只剩 hold 一个
  真源（不 claim = 控制台看到「没人接手」）、AGENTS §5「训练操作唯一入口 = 控制台」。
- **取包带 `?lease=` / `proto=2`**：M1b 已落（`obtain_pack`→`fetch_task_pack`→`_try_hubs`，claim URL 带
  `?proto=2`），本刀只补点名腿的 claim 前置；`BOOT_SELF=boot-2026-10-07a`；notebook 说明书加一节
  「谁在跑这门课：接管（hold）与轮内打点」（进度才算活性 / 900s 无进度可被接管 / 409 三态行为 /
  proto=2 旧端指引），`CFG.course` 那行写明点名腿也先领租约。
- 用例：新 `tests/common/test_progress_hook.py`（契约四态：无注册者 / 转发形状 / 层抛异常被吞 /
  层被同名键占住）+ `tests/common/test_offline_task_queue.py` 的 M3 段 14 条（常量影响力 · URL/参数
  逐字与节流 · 时间窗重开 · 409 三态 · 失败上限 · detach 还原上层注册 · 无线程 · **真 hub 端到端**
  （造「心跳活、进度死」后打点把 hold 刷回 live；错租约 → 409 taken）· pending_export 排序 ·
  revoked 回收 · 点名腿 claim→run 次序与接线 · 无 hub 空 ctx · 装层/卸层顺序）+ `tests/remote/test_run_loop.py`
  （轮边界四类事件 + force）+ `tests/remote/test_remote_iter.py`（每结算一局 / 无注册者零副作用）。
- 门禁：nn python gate **3811 passed / 9 skipped**（ruff + mypy + pytest 全量，29s）· `bun run check` 绿
  （本刀未动 TS，故 dashboard 两份与 bundle 重建不在本次范围）。

### M4 — 控制台 UI/UX（需求 8）★ **已落地 2026-10-08（`beee107f`，见下对账）**

- 删：`course-mode.ts` 全模块 · `console-state.courseModes` · 启动回灌 · 开课弹窗 `trainMode` · 权威徽标/modeDrift · 切离线/切换成在线/交还自动三颗钮 · `intent-drift`。
- 改：`overview.ts` 透出 `holds`（holder/最近回传龄/段内轮数/it；含 `pending_export` 行）；
  `course-status.ts` 词表：`autonomous-running`（云机执行中）· `autonomous-waiting`（等云机接管）· `autonomous-stale`（接管掉线·已恢复协作）· loop 侧 `held-wait`；
  `course-matrix.ts`：段内列 → 接管列；`alerts.ts`：接管无进度告警（15 分钟自动恢复说明）；
  worker 行加类型（P2-5 优先级）；加「强制解除接管」（`release_hold=1`）；「导出任务包」保留；停课不再推 hub。
- 测试：dashboard 相关用例改词 + 新增 hold 派生/双通道用例。

**★ 落地对账（本笔）**：

- **F5 前置普查（dashboard 侧）**：逐文件处置落进本笔 diff（删 542 行的 `course-mode.ts`、
  `TrainMode` 类型、`console-state` 两键、`start.ts` 回灌、`trainModeKnobs`；`resolveRolloutSrc`
  域收 `['auto','local','node']`（`'run'` 退役）+ 容忍读）。
- **F1 定案落地**：新 `server/actions/auto-offline-handoff.ts` —— `autoOfflineHandoff` 收窄成**只导包**
  （`autoBundleDecision` 规则表原样搬入；「停本机」腿随 `course-mode.ts` 整模块退役）。
- **写面**：`route.ts` 删 `setCourseMode`/`unsetCourseMode` case、加 `releaseCourseHold`
  （`hub-admin.ts::hubReleaseCourseHold`，**409 ⇒ ok:true「本来就没接管」**= 目标态已达）；`openCourse`
  对 `body.trainMode !== undefined` **响亮 400**（「trainMode 已退役…」）；开课 body 只余 `rolloutSrc`/`seedFrom`；
  `stopCourse(course)` 不再推 hub。
- **读面**：`overview.ts` = `HubAdmin.{holds,pendingExports,offlineDisks}` + 与面板同拍的 `holdFacts(queue)`
  （`workerKindOf` 派生 worker 类型，P2-5）；`held` 缓存写侧落 `overview.getHubAdmin` →
  `loop-control.writeHeldCache`（校验课名 / 去重 / 原子写 / 只替补 `held` / 空数组也写 / 永不抛）；
  `course-overview.ts` 行去 `mode`/`authority`/`pinned`/`offline` 键。
- **行为变更（逐条记）**：
  1. **接管列的 `iter` 只认云机回传**（`iter = ov.offlineLastIter ?? null`）——拿不到就是**未知**
     （`iterSource=null`，不拿本地队列/账本指针充数；「本机下一轮指针」在接管期不适用）；`iterSource='hold'` = 有回传。
  2. `state-view` 顶层 `offline` 键删除；`ppoQueueStall` 判据 = `holds[course].state === 'live'`（停滞告警与派发闸同源）。
  3. `release_hold` = **唯一人工解除出口**（旧三颗钮全删）。
  4. rollout 位置（`rolloutSrc`: auto/local/node）在开课弹窗**保留**（「云机接管不在这里选」注释）——
     它是执行位置不是在线/离线，与「课程无模式」不冲突。
- **UI 细节**：`course-status.ts` 词表 `autonomous-running/-waiting/-stale` + `held-wait`；
  接管列 +「强制解除接管」钮；`alerts.ts` 唯一人工动作 `releaseCourseHold`（silent 档只剩 `ack`）+ pending-export
  详情三条出路（重连 / 手工导入 / 本机不动）；`WorkerRegistry` 类型徽标（自主/协作，null 不画）；
  `BundleRowActions`「未导出 · 云机认领时自动导」+「重打包把旧包挪进 stale-packs/ 作废」。
- **测试**：删 `course-mode.test.ts` · `course-mode-bundle.test.ts` **改名** `auto-offline-handoff.test.ts`
  （保 `autoBundleDecision` 规则表 + 接线）· `train-mode-offline.test.ts` **改名** `course-mode-retired.test.ts`
  （退役断言四段）· 改 16 份。
- **门禁**：`bun run check` **2399 pass / 12 skip** · `cd dashboard && typecheck+tests` **1422 pass / 0 fail**
  （差 = 删/改名）· 三份 bundle 重建 ok · `bunx oxlint` 0 warnings 0 errors。
- `dashboard/src/web/theme.css` 的 oxfmt 历史未格式化**非本笔引入**，不随本刀修（M6 或之后单独清）。
- **M4b（hub 侧，紧接本刀的独立一笔）**：`_modes`/`mode_of`/`set_mode*`/`authority_of`/`AUTHORITY_*`/
  `pinned_of`/`auto_*`/`parked` 残留删除——M1c 时点因控制台三颗钮未删而推迟到此处；
  现在写面已消失（本刀），旧符号可安全清（与 M1c 同判据：写面与最后一个读方同日消失）。

**★ 落地对账（M4b，2026-10-08）**：

- 删除面（生产）：`common/protocol.py` 三常量（`COURSE_MODE_*`/`COURSE_MODES`）；
  `hub/queue_scope.py` 的 `_modes`/`mode_of`/`offline_courses`/`set_mode*`/`dispatch_effective_mode`；
  `hub/task_pack.py` 的 `AUTHORITY_*` 五档 + `OFFLINE_LEASE_STALE_SEC(180s)` +
  `offline_lease_stale_sec()` + `lease_verdict` 的 beat_at 退回腿（★F2：一条租约没有进度字段
  即不可证明活着）；`hub/admin.py` 的 mode/pin/drop_jobs 三参数（带任一 ⇒ 400 退役文案）；
  `/admin/queue` 行的 `mode/authority/pinned` 与顶层 `offline`；`offline_tasks` 的
  `authority/auto_handoff/seize`；`hub/offline.py` 取包三门（换成 live hold 的 lease 门）。
- 兼容标记（P1-3）：`--course a=offline` 保留解析、WARN + 忽略，`/admin/courses` 行给
  `mode_ignored`；`parse_course_arg` 第二项改为**段原文**（不再归一化/校验值域）。
- 消费点改判据：`active_courses` 的「非离线」→「非 live-hold」（P2-7）；`offline_task_courses`
  的候选面 = 开课标记（冷课也一样——停课是唯一 opt-out）；`offline_stalled` ② 腿锚读**盘上原文**
  `pending_export.at`（WIP 里误用会惰性过期的读面 `pending_export_of` ⇒ 窗一过锚就消失、
  告警永远不响；本刀修正）；`role_blocked` 的 hold 闸不变（M1b 已落）。
- 测试面：`test_auto_handoff.py`（去模式重写 + v1/v2 旧键忽略）/ `test_multi_course_hub.py`
  （role 循环量换 hold、admin 行、active_courses）/ `test_offline_task_queue.py`（清单新字段集 /
  resolve 一层选择 / `pinned_online` 客户端腿删）/ `test_hub_push_dispatch.py`（「离线课不推」
  → 「被接管的课不推」）/ `e2e/test_auto_handoff_e2e.py`（14 例全部改到 hold 面；`drop_jobs`
  与 pin 两个用例退役，补旧端拒收）/ `e2e/test_offline_training_e2e.py`（取包门换成 lease 门）/
  `e2e/test_multi_course_single_hub_e2e.py`（死 `set_mode` 删除）。
- 门禁：nn python gate **3799 passed / 9 skipped**（ruff + mypy + pytest tests/+e2e/）。

### M5 — BC 独占（需求 7 + Q5）

- `common/manifest.py`：`KIND_ROLES` 不动；新增双角色 kind 集（bc）；
  `store_leases.py`：`role_blocked` 顺序按 Q5；bc 进度租约（`_last_progress` ← `POST /jobs/{id}/epoch`，900s 孤儿）；
  `queue_claims.py`：bc 无备份副本。
- 自主腿（M3 追加）：`try_take_bc_job()` 前置闸 = 本 worker 无 live hold（**复用 Q3 同一判据**）；
  执行面复用 `remote/job_round.run_one_round` 单 job 生命周期；领 BC 后不得再 claim 课程，直到跑完/让出。
- 文档写死副作用：BC 租约被 900s 回收后原 holder **仍在 drain**（Q5）⇒ 最坏这份 BC 被别人重跑一遍，有界。

**★ 落地对账（M5，2026-10-08）**：

- `common/manifest.py`：新增 `KIND_BC` + `BOTH_ROLE_KINDS = frozenset({bc})`（`KIND_ROLES` **不动**）；
  `common/protocol.py` 两条 `as` 转发。`store_leases.role_blocked` 的 ① 改读 `BOTH_ROLE_KINDS`
  （顺序保持：双角色豁免 → 角色 → hold，守卫连位置一起钉）。
- `store_leases.py`：`_last_progress` + `_progress_lock` + `note_job_progress`（`POST /jobs/{id}/epoch`
  的**唯一**写入点，落点在 `store_results.store_bc_epoch`）/ `job_progress_at` / `_bc_progress_stale_sec`
  （env `BCITY_BC_PROGRESS_STALE_SEC`）/ `bc_drain_of`；`_lease_state` 增 bc 分支（**只认进度**，
  排在 push 豁免**之后**——push 腿代持的租约无 epoch，排前面会把正在收包的推送误杀）；
  备份面拒 bc（`no_backup`）；领取写第一个进度锚；`abandon/release/mark_completed/_collect_expired/
  publish` 五处清锚。
- `queue_claims.py`：`worker_holds`（跨课程「谁在带课」全视图）/ `worker_bc_drain` / `_drain_blocked`
  （接在 `claim_next` / `peek_jobs` / `claim_job` 三面）+ `job_kind` 的 hub 转发（push 腿用）。
  **与 plan 字面的一处偏离（有意）**：F6 说 `role_blocked` 扩成 `(job_id, role, worker_id)`——
  实际按**门面契约守卫**（同名 ⇒ 逐字同签名，闭集 34 条）改成兄弟函数：判据仍全在 hub 侧、
  三面共用，而 store 侧不被迫长一个用不上的形参（详记 `DECISIONS §2026-10-08-goalnn-dispatch-model`）。
- `queue_offline.py`：`_busy_locked` 增 BC 腿（在跑 bc 的盘不接课程；超窗自动恢复）。
- `push_dispatch.py`：备份过滤不含双角色 kind；`peek_jobs` 的在飞候选同样排除（客户端少一趟无功往返）。
- `remote/offline_boot.py`：`try_take_bc_job` + `_run_bc_once`（注入点；包内 `worker_loop(once=True,
  role="offline")` = 只领 bc 的执行面）+ `resolve_courses(holds=)` 回填 `holds["mine"]`（Q3 同一份事实）；
  `_run_auto` 三个空档分支接线（跑完重置空转锚）；`BOOT_SELF=boot-2026-10-08a`。
- 用例：`test_hold.py` +3（无备份 / 进度租约与让出 / 双向互斥）· 新 `test_offline_bc_leg.py`（6 例）·
  `test_hub_push_dispatch.py` +1（push 不备份 bc）· `test_offline_task_queue.py` +1（`holds["mine"]`）。
  **先红后绿实测**：备份拒（`if False:`）· 进度孤儿分支（`if False:`）· 跨课程 drain 腿（`if True:`）·
  busy 腿（`if False:`）· push 过滤（`if False:`）逐条把对应用例打红后恢复（本笔 A/B 记录）。
- 门禁：nn python gate **3810 passed / 9 skipped**（ruff + mypy + pytest `tests/ e2e/`）。

### M6 — 迁移、兼容与文档

- 一次性清理：`console-state.courseModes` 键删除；各课 rl-config 删 `courses.<课>.run_iters` 与 `rollout_src=run`；`loop-control.json` 旧形状条目删除。
- **兼容矩阵（改写后）**：

  | 组合 | 结论 |
  |---|---|
  | 旧云机 × 新 hub | **不支持**：claim 缺 `?proto=2` ⇒ 409（`busy`+error 全文送达旧日志）；升级窗口 = 刷新 notebook |
  | 新云机 × 旧 hub | 读不到 hold ⇒ 按现状降级照跑（与现状同级），UI 显示未知 |
  | 旧 console × 新 hub | 模式 POST 400 退役文案（响亮）；回灌全失败（随代码删除） |
  | 新 console × 旧 hub | 读不到 hold ⇒ `held` 按空集写；UI 显示未知 |

- 文档：`remote-transport.md` **§69**（本重构全文：状态表/判据/四象限真值表/`?worker=` 上界语义/`pending_export` 代价/`active_courses` 抖动/取包门/BC 独占/兼容矩阵）·
  `console.md` **§33**（v2 UI 词表与钮）· `docs/nn.progress.md` 索引节数同步（44 / 33）·
  `DECISIONS.md` 新条目 `§2026-10-08-goalnn-dispatch-model` · memory 一行。

**★ 落地对账（M6，2026-10-08）**：

- 一次性清理**实测无残留**：`nn-training/rl-config.json` 的 `courses` 为空（无 `run_iters` /
  `rollout_src=run`）；`tmp/console-state.json` 不存在（无 `courseModes` 键）；`tmp/loop-control.json`
  只剩 `{version, held}`（已是新形状）⇒ 三项都不用动（写进 §69.7，免得下次再来找一遍）。
- **M0 读数未测（如实记）**：本机没有真机离线回传语料（`offline/` 产物只出现在 pytest 临时目录）
  ⇒ 单轮 P50/P95 **未测**，「P95 > 600s ⇒ 重开 Q2」既未触发也未证伪；真机下一份回传落盘后补记到 §69.3。
- **e2e 守门（plan §4 的四条）落地情况**：①（误杀/卡死双面）与 ④（无包不建 hold）在 M1b/M3 的
  e2e/单测里已钉；②（清单/claim 同源，`?worker=`）与 ③（旧端拒收 `proto=2`）在 `test_hold.py`
  与 e2e 拒收用例里。
- **`test_hold_e2e.py` 已建（本笔追加）**：真 hub 子进程 + 真 HTTP + 秒级窗（**不模拟时钟**）：
  ① 全循环（开课无模式 → claim 建 hold → 协作被压 → 超窗掉线 ⇒ 协作自动恢复 → 轮内进度事件
  刷回 live → 新盘 stale 接管 → `release_hold=1` 强制解除）；② BC 独占链（`holding:<课>` /
  双角色领 BC / `no_backup` / 在跑 BC ⇒ `busy` / 超窗让出 + 账本 `lease-orphan-reaped` /
  课程接管自动恢复 / A 的 hold 与 BC 正交）。A/B：关掉 `_drain_blocked` 与 busy 的 BC 腿 ⇒
  ② 对应断言当场红；恢复后 2 passed（≈6s）。
  **仍待**：真机判据（云机日志 `hold-claim` / 轮内 `offline-progress` / 掉线后 `409 stale`；
  控制台三词与接管列；旧 notebook 会话「请刷新」且不双跑）——那需要真云机会话，本机无法自证。

---

## 4. 验证（DoD）

- **门禁**：`bun run check` · nn python gate（`bash tools/githook/nn-py-safe.sh -m pytest`，≤60s/用例）· `cd dashboard && bun run typecheck && bun run test` · 动过 `dashboard/src/web/**` ⇒ `bun dashboard/src/server/build.ts` 三份 bundle。
- **先红后绿**：新语义用例在旧形状上必须红（A/B 工作树实测记录）。
- **四条守门用例**（评审 §6，第 1 条按 §1.5.2 修订）：
  1. 误杀/卡死双面：单轮 wall_sec >900s ∧ 轮内每 ≤300s 有完成事件 ⇒ live 且协作仍被压、本机仍 held；**整轮零完成事件 >900s ⇒ 判掉线**；
  2. 清单/claim 同源：带 `?worker=` 清单说 `claimable=true` ⇒ 同参 `claim_offline` 必成功；
  3. 旧端拒收：claim 无 `?proto=2` ⇒ 409 且响应含 `busy:true`+`error`+`proto_required:2`；新码拿 live `held` ⇒ 本拍不跑换下一门；
  4. 无包不建 hold：无包 claim ⇒ 无 hold、协作盘仍可领该课 PPO、清单 `state=no_pack ∧ pending_export.by=<worker>`、本机不停跑。
- **e2e**（env 调秒级）：`test_hold_e2e.py` —— 开课无模式 → 云机 claim（先导包态再 hold）→ 协作被压 → 900s（模拟）→ 本机双通道恢复 + 任务可领 → 新盘 takeover → 强制解除；BC 独占链。
- **真机判据**：云机日志 `hold-claim` / 轮内 `offline-progress` / 掉线后 `409 stale`；控制台三词与接管列；hub `/admin/queue.held` 与 `/admin/offline` 一致；**旧 notebook 会话**出现「请刷新」日志且照旧不跑（不双跑）。
- **M0 数据**：§69 记录单轮 P50/P95（重开阈值裁决的唯一凭据）。

---

## 5. 风险与兜底

| 风险 | 兜底 |
|---|---|
| 慢轮误杀（Q2 维持 900s 的固有风险） | **轮内完成事件打点**（M ≤300s）+ 挂死轮本来就该判死（2026-10-06 事故原形）；误释放只造成冗余续跑（从最新锚点接），被释放者产物只归档 |
| `pending_export` 期间本机照跑 ⇒ 导出包追移动靶 | 必付代价（Q1）；新鲜度门（`_ask_console_freshness`，节流 600s + 上界 2，到顶照发旧包）+ resume 锚点；**禁止**给 pending_export 开"停本机"通道 |
| 掉线后旧任务被补做（D2 代价） | 有界（一条 job）；trainer 恢复时 `cancel_stale_jobs` 兜底；与 §68 竞速浪费同族 |
| hub 重启丢 hold | `offline-dispatch.json` v2 落盘（含 token）+ 300s 恢复宽限 + `hold-restored` 日志 |
| 控制台死 + 本机 held 冻结 | **双通道**：trainer 直问 hub（主）；文件条目带时间戳**自过期**（兜） |
| 旧任务/双跑竞态（在飞 job + 接管起点分叉） | 沿用「在飞的不动」+ 结果按 jid 验收；旧客户端被 `proto=2` 挡在 claim、被 lease 门挡在取包 |
| M1 太大（499 处/36 文件） | M1a/b/c 三段，每段独立绿、独立提交，可二分 |

## 6. 交付物

- 代码：M1a–M5 落点如各节（hub ~10 文件 / trainer ~8 / remote 2 / dashboard ~15 / common 1）。
- 文档：`remote-transport.md §72`（原 §69）· `console.md §35`（原 §33）· `DECISIONS.md` 新条目 · 本 plan（已落 `plan/`）· memory 一行。
- 顺序：**M0 → M1a → M1b → M1c → M2 → M3 → M4 → M5 → M6**；M1 三小步与 M2 是同一批语义切换的两半，推荐连做但不合并提交。
