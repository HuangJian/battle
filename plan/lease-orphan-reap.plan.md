# Plan: lease-orphan-reap — 孤儿租约早收（hub-only，不动 worker）

> **交付物**：本 plan。实现交给后续 agent；未写进本文件的细节以代码与 `DECISIONS.md` 为准。
> **路径基准**：全文除注明外，相对 `nn-training/`（例：`remote/job_lifecycle.py` = `nn-training/remote/job_lifecycle.py`）。
> **状态**：待实施（2026-09-28 根因定案，用户指令“从根源修掉”；2026-10-01 评审修订，见 §7）。
> **触发**：bc-human-retrial job `460b637b` 实测——worker 首次 acquire 读超时，
> hub 侧租约已设、worker 侧 token 随响应一起丢，还不了租约，job 在 peek 里隐身整整
> 一个 `CLAIM_TTL_SEC=300`（16:09:39 → 16:14:12），V100 干烧 5 分钟。

## 0. 一句话目标与诚实账

认领是单程：租约设立（hub 侧，claim POST 到达即设）与 token 送达（响应读回）不在一个
原子里，中间读超时必产**孤儿租约**（hub 有主、世上无人持有 token）。本次修掉它：
claim 后无声一段即提前回池，不等满 300s TTL。

**诚实账**：触发条件苛刻（慢 tunnel ＋ 超时恰好落在认领读上，一年几次）；单次收益
约 2~8 分钟 GPU 空闲 ＋ 消灭一种误导性日志形状（“no job yet”而队列非空）。代价是
hub 调度语义的一次加法（纯 server 侧，worker 零改动——云机旧码照跑）。立项理由不是
省机时，是**队列诚实**：peek 看不见的 job 不该存在。

**非目标**：不改认领协议（无两阶段 claim，旧 worker 兼容）；不放宽 H2（无 blind-abandon，
开放 hub 上等于允许别人 abandon 你的 job）；不碰 tunnel/传输调参（让路/重抽已覆盖慢读本身）。

## 1. 根因（定案，不重证）

- `remote/job_lifecycle.py::claim_job`：`lease_token` 装在 claim 响应 body 里（客户端
  `timeout=30s`）；hub 侧 `hub/schedule.py::_post_claim` → `hub/store_leases.py::
  _JobStore._claim_locked` POST 到达即设 owner+expiry。
  读超时 ⇒ hub 有租约、worker 无 token（`acquire failed: The read operation timed out`）。
- 还租两条路（`abandon`/`release`）都要出示 token（H2，`hub/schedule.py` 注释行“须与原租者一致”）；
  心跳也要 token。孤儿租约在 hub 看来与“正常下载中的租约”**不可区分**——直到 TTL 跑完。
- 反证已排除：payload 早已静态备好（publish 时确定性构建，tar.xz 61 shards→1.72MB），
  code.sha 对得上（worker 自检通过），worker 版本门放行（第二次认领即成功开训）。
  慢的只是 tunnel（~240KB/s＋让路），放大的全是租约。

## 2. 语义规格（先定死再写码）

- **新规则（唯一一条）**：租约若满足「**自 claim 起一次成功心跳都没有**」且
  `now - claimed_at > ORPHAN_GRACE_SEC` ⇒ hub 判定为孤儿：自动释放回池，并记账本事件
  `lease-orphan-reaped {job_id, worker, silent_sec, reclaims, ts}`（可审计；控制台读账本
  `tmp/<课>/training_log.jsonl` 尾窗口即见，不另开端点）。
- **判据只能长在既有事实上**：store 里唯一会写租约活动的信号是 `heartbeat()`
  （同时刷 `_leases` 与 `_last_heartbeat`；`hub/store_leases.py::heartbeat`）。
  `/start` 只打 `computing_at`、`/ready` 只 `set_ready`、`/result` 只做 token 校验，
  **hub 看不到下载进度**（blob GET 不记任何租约活动，POST 面就是那 10 个）。
  故**不要把**「零 /start / 零下载进度」写进判据——那会逼实现者给 4 个端点加新写点。
  「零心跳」= `_last_heartbeat.get(jid)` 不晚于 claim 时刻（`_claimed[jid]["at"]`），
  领取时两者同值、心跳时后者刷新 ⇒ **零新状态**，且正是控制台 `last_heartbeat_ago` 读的字段。
- **适用范围（三条边界，必须写进实现注释）**：
  1. **push 腿不适用**：push 派发的租约由 hub 进程内的 `PushDispatcher` 代持，它只在
     `_await_result` 里续租（`remote/push_dispatch.py::_await_result`）——**上传段
     （`state="upload"`，payload+code+ts+blobs，带 3 次重试）一次心跳都不发**。
     若按“exclusive 模式一律适用”实现，慢上传 >180s 的 push 腿会被自己的规则误杀
     （同文件注释自己写着“不续租就会被 pull worker 领走（同一份活两处跑）”）。
     判别据 = 持有人身份前缀：`common/protocol.py::PUSH_WORKER_PREFIX` +
     `push_worker_id_of()`（两函数**同住协议层**，hub 侧 import 零成本）；
     `worker_id` 带该前缀的租约**永不早收**——push 腿的治理交回派发器自己的
     `--push-timeout-sec`。
  2. **离线段不适用**：`/offline/lease` 是另一套（小时级 TTL，`common/protocol.py` 的
     离线段常量），不经过 `_JobStore._leases` ⇒ 本规则天然不碰它，但要在文档里写明。
  3. **手工认领适用**：无 `worker_id` 的 curl 式认领同样适用（它同样不可能有心跳）；
     判别据只看“有没有心跳”，不看身份有没有填。
- `ORPHAN_GRACE_SEC = 180`（定值，不做旋钮；住 `hub/store_leases.py` **顶部**——
  不放 `common/protocol.py`：那是两端共享的协议面，本规则纯 hub 侧）。
  180 = 心跳周期 `HEARTBEAT_SEC=60` × 3：pull 腿的心跳线程在**下载之前**就起
  （`remote/job_round.py::run_round`），到 180s 至少心跳过 2 次；再留一拍余量。
  backup 模式（无租约）永不触发；hub 重启丢租约既有语义不变。
- **实现形状（★ 判据必须唯一，否则会做出比现在更坏的形状）**：
  1. 抽**一个**纯判据 `_lease_state(job_id, now)`（返回四态 `LEASE_NONE`（无租约）/ `LEASE_ALIVE` /
     `LEASE_EXPIRED` / `LEASE_ORPHAN`）在**四处同源**使用：`store_ledger.py::claimable_job_ids`
     的池过滤、`_claim_locked` 的 `recovering`、`lease_worker`、`inflight`。
     兄弟混入拿不到那边的常量（混入之间不许 import）⇒ 再给一个布尔视图
     `_lease_held(job_id, now)`（`== LEASE_ALIVE`）供池过滤用，判据仍只有一处。
     ⚠ 只在池过滤里加“孤儿⇒可见”是不够的：`_claim_locked` 会因为 `job_id in self._claimed`
     （`recovering=False`）回 `held` ⇒ **队列里看得见、谁都领不到**（比现状更难查）。
  2. 回收仍走**唯一**的 `_collect_expired_locked`（stale 记录 + 毒包计数 +
     `_drop_commitment_locked`），不新增回收点；给它加 `orphan=` 形参决定是否写事件。
     ⚠ **不许用 `release()`**：它不动 `_claimed`，那份 job 会被 highest 唯一性闸永久
     挡在门外（`hub/store_scheduling.py::_drop_commitment_locked` 的注释把这坑写清了）。
  3. **无后台 tick**：现有形状是“谁先看谁回收”（`claimable_job_ids` 是纯过滤；
     回收只在 `claim` / `lease_worker` 两处）。本规则沿用同一形状 ⇒ 事件时刻 =
     **第一次有人看的时刻**（工人空转轮询、控制台 `/admin/queue` 秒级触发），
     `silent_sec` 记的是“被看见时的静默秒数”，可能 ≫ 180。§6 的验收按这个口径写。
- **代价（评审要求写明，别只写收益）**：误杀（活 worker 被判孤儿）= **一次 reclaim**
  （`_reclaims+1` ⇒ 三度达 `FREEZE_AFTER_RECLAIMS` 冻成毒包）+ 该 worker 进
  `_stale_holders`（避让链在 ≥2 活跃 worker 时拒它重领自己那份，`may_avoid_stale_holder`）
  + 结果回传 403 被丢（`hub/result.py` token 校验；`remote/result_upload.py::_call` 把 4xx
  吞成一行 ★ 日志）+ 同一份活两处跑（另一个 worker 领走）。
  这就是判据必须**窄到“自 claim 起零心跳”**、且 push 腿必须豁免的原因：
  窄判据下“活 worker 被判孤儿”只剩一种窗口——**已收到 token 却在跑起心跳线程之前死掉/卡住**，
  实践上等于真孤儿。
- **worker 零改动**：pull 腿旧码云机照跑（心跳线程照发；`heartbeat()` 客户端把 4xx 吞掉，
  所以即使真被误杀，worker 侧只会继续跑完本轮——这正是“误杀的代价落在 GPU 上”的由来）。

## 3. 被否决的备选（三问存档，免重犯）

| 备选 | 否决理由 |
|---|---|
| (a) 认领下载两阶段（意向锁短 TTL＋就绪转正） | 两端都动；云机旧码不认新语义就全退役一次，不值得为一年几次的事故付全网升级税 |
| (d) blind-abandon（凭 worker_id 放宽 H2） | 安全口径：开放 hub 上任何人可 abandon 别人的 job＝可 DoS；私有 tunnel 可议但本仓库按开放 hub 设计 |
| (b) 过期回池后优先重领（治标） | 治的是发现延迟，不是隐身本身；peek 在 TTL 内根本看不见它，省不掉 5 分钟 |
| (e) “静默 180s”（从最后一次活动起算）而非“自 claim 起零心跳” | 会把“心跳过两次后死掉”的租约提前判死（TTL 边界 300→180 的误杀率上升），而事故形状（零心跳）不需要它；判据越宽，代价越高 |
| (f) 用 `lease <= now` 的一半 TTL 当阈值 | 与心跳周期脱钩（改 `HEARTBEAT_SEC` 就漂），且对“心跳过但慢”的活 worker 同样误杀 |

## 4. 分阶段

- P0：`hub/store_leases.py` 加 `ORPHAN_GRACE_SEC` + `_lease_state`/`_lease_held` +
  `_collect_expired_locked(orphan=)` 的账本事件；`hub/store_ledger.py::claimable_job_ids`
  与 `lease_worker`/`inflight` 改用同一判据。
  单测进 `tests/hub/test_hub_leases.py`（与 P3b 同文件，该文件有现成 `_Clock` 假时钟）；
  拆分守卫 `tests/hub/test_hub_job_store_split.py` 的方法计数随 +2 更新（49 → 51）。
- P1：文档三件——`docs/nn/remote-transport.md` **顶部**加 `## §52`（当前最大 51）+
  `docs/nn.progress.md` 索引一行 + `DECISIONS.md` 索引入账（传输语义加法，按
  HOW-TO-ADD 三问：被否决备选 ✓ 会重犯 ✓ 不能就近表达 ✓）；
  hub-server 经控制台重启生效（本地组件，worker 不动）。

## 5. 测试 / DoD

全部用假时钟（`_JobStore(now_fn=...)`），禁睡真 180s。

- `test_claim_silence_past_grace_reaps_and_logs`：claim 后推进 179s 无声 ⇒ 池里没有它、
  `claim()` 仍`None`、无事件；**181s ⇒ 池里出现它、`claim()` 拿得到（★ 池可见 ⇒ 领得到）、
  账本一条 `lease-orphan-reaped`**（`silent_sec ≥ 180`、`worker` 是原持有人、`reclaims == 1`）。
- `test_heartbeat_once_never_reaped`（★ 评审改口径——**一次心跳后按 TTL 走**）：
  60s 处一次心跳 ⇒ 181s 时池里仍没有它、`claim()` 仍 `None`、**无** `lease-orphan-reaped`；
  随后跨过 TTL（该心跳把 lease 推到 360）⇒ 走**既有过期路径**回池，**仍然没有**该事件
  （“早收不误杀”与“TTL 照旧”是两件事，分开断言）。
- `test_push_lease_is_never_orphan_reaped`：`worker_id=push_worker_id_of("gpu1")` 认领后
  零心跳推进 181s ⇒ 池里没有、`claim()` 仍 `None`、无事件（push 腿上传段的形状）；
  再跨过 TTL ⇒ 按过期回收，仍无该事件。
- `test_backup_never_reaped` ＋ H2 回归（既有矩阵全绿）。
- **既有用例需随语义改动**：`test_claim_excludes_from_pool_until_expiry` 现在在 299s 处断言
  “仍在租”——零心跳下按新规则 180s 就该早收 ⇒ 该用例改为**claim 后带一次心跳**再走 TTL 边界
  （它测的是 TTL，不是孤儿规则）。这是本次唯一一处既有断言的语义变更，写进 commit 说明。
- DoD：上列全绿 ＋ `bash tools/githook/nn-python-gate.sh`（ruff+mypy+pytest 全量）绿
  ＋ `bun run check` 绿（附加，本次无根项目 TS 改动）＋ DECISIONS 条目 ＋ docs 三件
  ＋ 无 worker 侧改动（`git diff --stat` 自证 `remote/**`、`worker/**` 零行；
  `remote/push_dispatch.py` 是**hub 进程内**的派发器，本次也**不动它**——判别据走协议层常量）。

## 6. 验收（真机，不烧训练）

- 人工复现：开任意小课 → 发布后 kill -9 worker 进程（模拟读超时孤儿）→ hub 日志
  `lease-orphan-reaped` 应在 **~180s** 后的**第一次轮询/观测**出现（而非 300s），
  `GET /jobs/next`（或 peek）恢复可见该 jid；`tmp/<课>/training_log.jsonl` 尾窗口能看到那一行。
- 负向：t<180 期间**不得**出现该事件；push 腿（`rl.hub_push=true`）慢上传期间**不得**出现；
  心跳正常的 pull worker 跨 300s TTL 期间**不得**出现。worker 侧无需任何操作。

## 7. 评审处置（2026-10-01，评审人 hy；已并入上文）

| # | 发现 | 处置 |
|---|---|---|
| O1 | push 腿租约只在 `_await_result` 续租，上传段零心跳 ⇒ “exclusive 一律适用”会误杀慢上传 | 判据加适用范围：push 前缀（`PUSH_WORKER_PREFIX`）免收；§2 边界① |
| O2 | §2 原列“零 /start / 零下载进度 / 零结果”不是 store 的事实（只有 heartbeat 写租约） | 判据收窄为“自 claim 起零心跳”；§2 判据段 |
| O3 | 只在池过滤里加判据会做出“池可见、领不到” | 抽唯一判据 `_lease_state`（+ `_lease_held` 布尔视图）四处同源；§2 实现形状①② |
| O4 | `hub/schedule.py::_claim_locked` 不存在（路由在 `hub/schedule.py::_post_claim`，临界区在 `hub/store_leases.py::_JobStore._claim_locked`）；单测路径应为 `tests/hub/test_hub_leases.py` | §1/§4 坐标订正；路径基准写进头部 |
| O5 | `test_heartbeat_once_never_reaped` 要求“一次心跳后 1000s 仍租住”，与 TTL 语义矛盾 | 改为“181s 不早收 + 跨 TTL 走既有过期路径、全程无事件”；§5 |
| O6 | P1 漏 docs 三件；DoD 的 `bun run check` 只判根项目；“remote/worker* 零行”措辞不明 | P1/§5 按 AGENTS §9 补齐；DoD 写明全量 nn gate 为准、push 派发器也零改动 |
