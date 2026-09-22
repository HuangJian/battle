# NN 远程链路与传输 — 技术档案

> hub / worker / 云机 / 离线任务包 / 回传 / 优先级调度。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---
## §31 异步结果回传：把 `out` 从关键路径上摘下来（plan/transfer-scheduling P2.5，2026-09-22）

**为什么做**：双课程单 worker（最典型场景）下 A 的 rollout 与 B 的 PPO 交错填空 ⇒ **算力已满**，
再快只能把传输从关键路径上摘掉。现场账 `rollout/in/ppo/out = 45/15/50/25 s`（用户口径）：
云侧只感知 `in/ppo/out`，而 **`out` 25s 比 `in` 15s 还大**、改造前又全程压在关键路径上——
P2 预取只治 `in`，`out` 无人管。两者正交：命中让 `in`→0，`out` 不动。

**做了什么**

| 面 | 落地 |
|---|---|
| 新模块 | `remote/result_upload.py`：`ResultUploader`（有界队列 `depth=2` + 专用线程 + `drain`/`close` + 落定回调）；`sync` 模式逐字回旧行为 |
| worker 接线 | `submit` 入队即返回 → 主循环立刻领下一份；`_result_settled` 在**落定那一刻**才 `_wire_flush(wall_end=…)`；job 级 `uploaded` 标志决定 finally 收不收；整段主循环包 `try/finally` 收尾 drain；`--result-upload {async,sync}`（缺省 async） |
| 记账 | `_wire_flush(wall_end=)`：`wall` = **关键路径**（claim → 结果就绪），新增 `overlap=` 字段；`out` 秒数照报不抹 | 
| 读方 | `tools/wire_report.py`：`overlap=` 可选组（旧日志当 0）+ `out_overlap_sec` + 渲染「不占关键路径 / 仍压在关键路径上」 |
| 安全网 | 队列满 / 入队超时 / 上传器已收尾 ⇒ **退回同步**（绝不丢）；失败落带 jid 的 `★` 行 + 收尾汇总；`--once` 先 drain 再判成败（H8 不退让） |

**验证**：`tests/test_async_result_upload.py`（18 例）——核心是 **A/B 次序**（async 下第二份 job
开算早于第一份回传结束；sync 基线必须晚于，否则用例是空转），另有「退出必 drain」（用事件闸住上传，
确定性可判，不赌调度）「队列满退同步不丢」「失败响亮」「每 job 恰好一行阶段账」「取消路径照旧收账」。
**8 刀改坏必红全红**（submit 恒同步 / close 不等落定 / 队列满丢结果 / 失败不响亮 / 忘传 wall_end /
finally 无条件收账 / --once 不等落定 / 读方不认 overlap）。nn 门禁 ruff+mypy+pytest
**2051 passed / 3 skipped**；根 `bun run check` 2122 例 0 fail。

**未做（明确入口）**：`out` 的**减重**（minimize-payload：让那 25s 本身变小）——与 P2.5 正交，
两者都做才是全量收益。**P0.5 的实机数字**仍待现场（见 §29/§30）。

---

---

## §30 竞速退役 + 控制台同批 + push 腿：P3 落地（plan/transfer-scheduling，2026-09-22）

**起因**：§28/§29 把取活换到 `peek → priority → claim`、把传输拆成 bulk 单通道之后，**旧**的
竞速广播判定（§2026-09-17）仍与新的优先级表并存——两套调度语义同时在跑。本批一次性删干净
（plan §2.8「不留兼容短语」），并把 push 腿拉到同一张表上。

**改了什么（判据看函数名）**
- `remote/protocol.py`：删 `RACE_MODE_*` / `race_decision` / `parse_hub_scope` / `HUB_SCOPE_HEADER`；
  `RACE_WORKER_WINDOW_SEC` → **`WORKER_SEEN_WINDOW_SEC`**（窗口本身还有用：登记表）。
- `remote/hub_server.py`：删 `race_mode/race_active/set_race_mode/race_state/clear_workers`、
  `GET|POST /admin/race`、`--race`、`/jobs/next`；`claim_next`/`claim(race=)` 的 race 分支删除。
  **保留** `claim(mode="backup")` 机制与 `_backup_authorized`（删判定不删机制，R1-1）。
- `remote/worker.py`：`poll_job` 整函数删除（取活只剩 `acquire_job`）；`--poll` 多值退化；
  `/jobs/next` 的发送点删除。
- `remote/push_dispatch.py`（R1-7）：同一张优先级表（`_priority_of_course`）、**1 主 + N 备份**
  （`backups_per_course` 缺省 1；备份无租约、槽位键 `f"{jid}#b{n}"`）、派发后 `hub.start_job`
  打 `computing_at`（否则掉队救援在 push 腿永久沉默）、landed 时 `_cancel_others` 推取消帧
  （推不到 ⇒ 让它跑完 + 409 丢弃，**不算失败**）。
- `dashboard/src/**`（R2-1，**同批**否则 hub 起不来）：去 `--race` 透传 / `RaceMode` / `raceActive`
  渲染，删 `tests/hub-server-race-arg.test.ts`；连注释里也不再出现退役面名。
- 测试侧：`tests/helpers/hub_poll.py`（新面拼回**与旧面逐字段同形**的返回值，10 个登录点机械替换，
  不各写十份会各自漂的实现）；`tests/helpers/push_worker.py` + `tests/conftest.py::worker_factory`
  （假 push worker 搬进 helper/conftest——跨测试文件 `from tests.test_a import fixture` 会撞
  ruff `F811`，因为夹具名遮蔽模块级 import）。

**门禁（常驻）**：`test_priority_schedule.py::test_race_judgment_has_no_production_path`（判定零命中）·
`test_jobs_next_retired.py`（真 HTTP 404 + 生产零命中）· `test_scope_unrelated_race.py`（R1-10 负向：
eval 侧长尾竞速**没有**被误删）· `test_push_priority_dispatch.py`（6）· `test_pause_budget.py`（4）。

**改坏必红自查（五刀）**：① 生产里加回 `/jobs/next` 兼容别名；② 竞速判定改名加回；③ 备份副本改走
租约；④ push 备份上限改 2；⑤ `peek` 不再登记 worker（避让链端到端用例）——五刀全红。

**踩到的坑**
- **跨测试文件 import 夹具 = ruff F811**：pytest 允许 `from tests.test_a import worker_factory`，
  但参数名遮蔽模块级 import，ruff 判「redefinition of unused」。正解是把夹具放进 `conftest.py`
  （无需 import 即全局可见）+ 实现搬 `tests/helpers/`，而不是加 `noqa`。
- **`_POLL_WARN_AT` 随 `poll_job` 一起被删**：它是 `_warn_non_200` 的节流表，与取活面无关——
  「整函数删除」时要清点的是**函数体引用的模块级名字**，不是函数名本身。
- **注释也算「删除清单命中」**：控制台 7 处 prose 注释还在写 `/jobs/next`（讲「取活面不看课程」的
  历史理由）。删除清单的 grep 若把控制台算进范围，注释必须一起改词——否则「零命中」是自欺。

**一处改名（R2-10d 对齐）**：让路预算 `BULK_YIELD_BUDGET_SEC` → **`PAUSE_BUDGET_SEC`**（与 plan
`docs/nn/legacy.md` §6 #5 同名；§29 里写的是旧名），安全裕度断言从 `test_bulk_sched.py` 拆到 plan 点名的
`tests/test_pause_budget.py`，并改为引用 hub 的 `SEND_TIMEOUT_SEC` 常量（原先写死 60.0）。

**未完成（不是已交付）**：P0.5 的**实机数字**（阶段账 `in/out/ppo/wall` 占比、`p90` 取消延迟）
——需一次云-hub-LAN 会话跑 `tools/wire_report.py`；在那之前 P2 预取的收益结论不成立，可用
`--prefetch-depth 0` 关闭。

---

---

## §29 传输 QoS + 阶段账 + 软持有预取：P0 / P0.5（仪器）/ P2 落地（plan/transfer-scheduling，2026-09-22）

**起因**：§28 只交付了 pull 线的取活换面；本批补上「传输那半场」——bulk 单通道、控制面旁路、
预取把下载叠到上一个 job 的 PPO 上，以及判 P2 盈亏所必需的阶段账。

**改了什么（判据看函数名）**
- `remote/bulk_sched.py`（新）：`BulkScheduler.slot`（**唯一** bulk 入口，任意并发下 `inflight≤1`）、
  `pace`（分片间隙让路 + P2 抢占检查）、`pause_if_needed`（单次预算 `BULK_YIELD_BUDGET_SEC=5s`，
  上界 = min(worker `BODY_IDLE_TIMEOUT_SEC`=45s, hub `SEND_TIMEOUT_SEC`=60s)——**两个都写进注释**）、
  `control_path`（按**路径**分流：`/payload` `/result` `/code` `/ts_code` `/blob` 才是 bulk）、
  `BulkPreemptError`（P2 被挤走 = 丢半截重下，**不**算失败）。
- `remote/worker.py`：`_request` 按 `control_path` 标记控制面（控制面**不进**队列；`urllib` 每请求
  新连接 = 独立 socket）；`_read_body(pace=…)` 每分片查一次让路；`post_result` 占 **P1** 槽
  （不被抢断、退避睡眠在槽外）；`download_*(bulk_prio=…)` 缺省 P1、预取传 P2。
- **阶段账（P0.5 仪器）**：`_wire_start`（claim 起算 wall）+ `_wire_time(jid,"ppo",sec)` +
  `_wire_flush` 尾部 `phases in=…s out=…s ppo=…s other=…s wall=…s`；每 job 行另加
  `wait=<queue_wait_sec>/yield=<yield_count>/p0_p95=<p0_rt_ms>`。
- `remote/prefetch.py`（新）：`PrefetchStore`（无租约软持有；`work_dir/prefetch/<jid>/` 只存引用与
  摘要，字节同时落内容寻址 `blob_cache/<sha>`；预算 64MB 超限按 `updated_at` 丢最旧；存取双向 sha
  校验）+ `pick_candidates`（纯函数）。`worker.py::_prefetch_fill` 后台填充（候选 = `peek_jobs`，
  下载 = **P2**，与 `run_job` 重叠），命中经**现成的** `run_job(preloaded=…)` 零下载开算；
  `--prefetch-depth`（0 = 关）；`prefetch/` 进 `prune_job_dirs` 豁免名单。
- `tools/wire_report.py`：阶段占比表（`parse_phases` / `summarize_phases` / `render_phases`：
  总量占比 + 每 job p50/p90；`--json` 同步）。

**关键取舍（DECISIONS §2026-09-22-goalnn-bulk-single-channel）**：让路**预算有界**（超 5s 会被 worker
自己的 45s 空闲判停/hub 60s 分片写超时判成停滞，而两个上界必须同时看）；P1 **不可抢断**（POST 大 body
没有安全 Range，抢断 = 整份白传）而 P2 **必须可弃**；预取**只走** `download_payload` 一条路（omit 协商
落在那里，另写整包 GET 会把 minimize-payload 的瘦身吹回去）；预取失败**永不**转
`ProtocolError`/`report_job_failure`。

**门禁**：`test_bulk_sched.py`（11）· `test_control_plane_bypass.py`（2，真 HTTP：bulk 在途时控制面
往返 <1s **且**量到并行）· `test_soft_hold_prefetch.py`（12）· `test_wire_report.py` 阶段账段（+5，
含**写方/读方格式一致性**用例——防「阶段表静默变空」）。七刀改坏必红已自查（门禁有效性）。
全量门禁：ruff + mypy + pytest `2050 passed / 3 skipped`（27s）。

**未做（下一步入口）**
1. **P0.5 的实机数字**：仪器与聚合口就绪，但本机没有真机 `wire` 日志可算 ⇒ 下一次云-hub-LAN 会话跑
   `python nn-training/tools/wire_report.py <worker 日志>`，把阶段表贴到 plan §1.1 下方。
   **在那之前 P2 的收益结论不成立**（机制可关：`--prefetch-depth 0`）。
2. **P3 竞速退役**（`/jobs/next` / race 判定 / `poll_job` / `hub_scope` / `--race` / `/admin/race`
   + 控制台同批改造 + push 腿 R1-7）：**本批未动**，两套仍并存且全绿。爆炸半径已清点（生产
   `hub_server.py` 83 处、`protocol.py` 15、`worker.py` 9；测试 `test_race_broadcast.py` 57 整文件删、
   `test_multi_course_hub.py` 50、`test_remote_ppo.py` 10、`test_poison_freeze.py` 9 + e2e 4 文件；
   控制台 `--race` argv 必须同批，否则 hub argparse 未识别即退出）⇒ 建议单开一批。

---

## §28 传输∥PPO 优先级调度面（pull 线取活换面）：P1/P1.5/P2 落地（plan/transfer-scheduling，2026-09-22）

**起因（用户指令）**：处理 `transfer-scheduling.review-R2.md` 的评审意见 → 自主开发/测试/审查/提交。
plan 的完整语义是「软持有预取 + 优先级调度 + landed 取消 + 传输 QoS + 取代 race broadcast」，
本次落地的是**其中依赖顺序最先可自洽交付的一段**：pull 线的**取活换面**。

**改了什么（判据看函数名）**
- `remote/worker.py`：`acquire_job`（取活三件套 = `peek_jobs` → `request_priority` → `claim_job`）、
  `job_started` / `job_ready` / `abandon_job` / `job_status` / `start_cancel_watcher`；`worker_loop`
  改走 `acquire_job`；`post_result(mode=…)` 把 backup 副本的 403 单列为**丢弃**；`run_job` 接
  `should_cancel` / `on_ppo_start`，取消点 = `ppo_update(on_epoch_done=…)` 的 epoch 边界 + 开算前预检。
- `remote/hub_server.py`：`GET /jobs/peek` 与 `POST /jobs/{priority,claim,start,ready,abandon}`；
  `_JobStore.claim_outcome/_claim_locked`（唯一临界区）；`_claimed` / `_computing` / `_ready` /
  `_epoch` / `_backup_authorized`；`scheduling_facts` / `priority_for` / `start_job` / `set_ready` /
  `abandon_job`；`note_worker` 换源到 peek/priority 且 `hub_scope` 照旧上报（避让链输入不丢）。
- `remote/protocol.py`：`job_priority`（§1.4 表纯函数：landed→none、ready→low、computing 超阈值→high、
  未超→medium、claim→medium、无人→highest）、`JobCancelledError`、`CLAIM_MODE_*` / `PRIORITY_*` /
  `STRAGGLER_SEC=180` / `JOB_CANCEL_POLL_SEC=1.5`。

**关键取舍（详见 DECISIONS §2026-09-22-goalnn-transfer-scheduling-pull）**：备份副本**不 pop 原租约**
（只加 `_backup_authorized` 放行回传）——pop 会让原 worker 硬死后无租约可过期 ⇒ 毒包熔断失明；
`highest` 的唯一性闸与 claim 同临界区（epoch 只在 claim 一处校验）；掉队阈值只认 `computing_at`；
取消只认 `landed` 且必须是独立异常（不可落进 `ProtocolError`/`RetryableError`）。

**门禁**：`nn-training/tests/test_priority_schedule.py` 17 例（纯函数五分支 / 两把时钟 / 备份租约 /
abandon 零 reclaim / highest 唯一性闸 / peek 无副作用 / 真实 HTTP 端到端 / 403 丢弃 / 取活三件套 /
取消环 / 源码级接线断言）；迁移 3 个既有文件到 `acquire_job`（`test_remote_hotswap.py` /
`test_worker_offline_cap.py` / `e2e/test_worker_queue.py`）。四刀改坏必红已自查（门禁有效性）。

**未做（下一步入口）**：P0 传输 QoS（控制面旁路 + bulk 单通道）、P0.5 基线（`T_in/T_out/T_ppo` 与
GPU 空转占比）、P2 的**预取半场**（`PrefetchQueue` + omit 协商）、P3 竞速退役（`/jobs/next` /
race 判定 / `poll_job` + 控制台同批改造 + push 腿 R1-7）。**本批不动** `/jobs/next`、`poll_job`
与 race 判定（两套并存，`test_race_broadcast.py` 仍绿），故 2026-09-17 的 race 条目尚未 supersede。

**门禁口径**：nn 侧 `bash tools/githook/nn-py-safe.sh -m pytest -q tests/ e2e/` —— 全绿，唯一红是
`tests/test_serve_wiring.py::test_course_args_match_run_rl_echo_config`（**既有环境失败**：本机
`nn-training/rl-config.json` 是未入库的机器本地配置，`course_args` 吃到机器侧覆盖而 oracle 不会；
已用「把三个文件还原成 HEAD 版本」在同一工作树上复现同一红，证明与本批无关）。

---

---

## §27 补传 400 的真因 / 导入后指标表看得见 / 引导模块每次刷新（2026-09-22）

用户 2026-09-22 问的四件事里剩下三件的收口（第一件＝单局看门狗，`docs/nn/runtime-opt.md` §8）。

### ① 「it1 产物回传失败，且失败后再也不回传」——真因在**取错了账本行**

现场：`补传 it1 体被拒（HTTP 400: 账本行与权重不符：行记 bff93df1… 实得 5f099e55…）——
本会话停用补传`。两个独立缺陷叠在一起：

* **取错行**：`OfflineDeliverer._row_for(it)` 从 `metrics.jsonl` 里返回**第一条**同 `it` 的行。
  而 `ArtifactStore.checkpoint` 是**追写**的：续跑/重开同一目录时同一个 `it` 会再落一行 ⇒
  拿上一会话的行去配这一会话的权重 ⇒ 指纹必然不符。现在：**新→旧扫描 + 只认 `weights_fp`
  与本轮字节相符的行**（与控制台「同 iter 取最后一条 = 最新对账结果」同一口径）；
  配不上就**宁可不发 row**（一个指错轮次的行比缺行危险得多），并记一行 WARN 把两个前缀并排。
* **一次内容拒收停用整条腿**：旧口径 `400/413 → 本会话停用补传` 是照抄 401/403 的反应。
  但两者代价不同：鉴权错每轮重试会把本 IP 封掉（hub D9 闭锁），内容错只毁**那一轮**。
  现在：400/413/422 → 记进会话内 `_rejected`（每个 it 只跳一次）→ **其余轮次照推**；
  401/403 仍然停用整条腿。`_post_artifact` 的返回值从 bool 改成 `"ok"/"skip"/"stop"`，
  三态各自对应「记已投递 / 跳过这轮继续 / 这一拍到此为止」——把「一轮的代价」与
  「整条腿的代价」在类型上分开。

### ② 导入产物后控制台「各轮指标表」一行不显——账本没并

那张表只读 `tmp/<课程>/training_log.jsonl` 的 `iteration` 事件（
`dashboard/src/server/api/state-view.ts` → `iters.readIterMetrics`）。导入只落了产物目录
（权重/优化器/`metrics.jsonl`）⇒ 权重可评估、末轮能评，但**表是空的**（用户实测）。
现在导入器把包内 `metrics.jsonl` 的逐轮行搬进课程账本（`deliver_zip._merge_carried_metric_rows`）：
只搬控制台真读的字段（`winRate/outcomes/samples/ticks/expectedGames/rollout_sec/ppo_sec/
steps/chunks/kl/policy/value/entropy/mean_ret`），带 `import_run_id` 来源标记，按 `it` 幂等
（账本已有一行就不写第二遍），失败只记一笔（导入的主价值是权重可评估）。
`expectedGames ← report.games` 是必要的：控制台用 `ticks / expectedGames` 算平均每局时长，
缺了它那一列恒为 0（看起来像「跑了零 tick」）。`dim_means`/`score_mean` 产物行里没有 ⇒
那些列**留空，不编数字**。控制台回执也加了一句「逐轮指标 +N 行」——最贵的失败是
「导进去了但表是空的」，它必须在上传后的第一眼就被看见。

### ③ 多课程写法没生效——是**引导模块缓存**在跑旧版

现场：`CFG.course` 给了三个课名，日志里却拼出 `['x20-demo-mix', ...]` 这种目录名，
且去找一个不存在的任务包。多课程解析本身早已随 `05691e72` 入库（`offline_boot.courses_of`
认字符串/列表/逗号分隔，串行跑完，`course_work_dir(multi=True)` 给每门课再套一层目录）；
真因是 notebook 的 `_load_boot` 旧策略「**有缓存先用缓存**」：同一个 kernel 里跑过一次旧
代码之后，之后每次 Run 都在跑那份旧的，而日志只打 branch（同分支看不出新旧）。
现在：每次会话先拉最新、`Path.replace` **原子替换**，拉不到才回落到缓存（并响亮说明用的是
上一份），并把**实际加载那份**的 `sha12` 打进日志。`tests/test_offline_notebook.py` 钉住这四点
（旧策略的 `_branch.txt` 不得回潮）。

#### 复审（2026-09-22 晚）：为什么用户导入后还是空的 + 补上两列

用户报「导进去了但表还是空的」。实测现场（`tmp/x20-demo-mix/`）：

* 那次导入跑在 **19:28**（`deliver-import.log` 的时间戳），而本节的合并代码写在 **20:54**
  ——那次 `DELIVER_IMPORT_JSON` 里**根本没有 `metric_rows` 键**（旧代码的指纹）；
* 包内**有** `metrics.jsonl`（49 行）：不是打包漏了；
* 课程账本里当时 `iteration` 行 = **0** ⇒ 表当然一行不显。

用当前代码重跑同一条导入命令：`metric_rows: 49`，账本 49 行，并用**控制台自己的读方**
（`dashboard/src/server/iters.ts::readIterMetrics`）验证：49 行、winRate/kl/samples/
rolloutSec/ppoSec 均真实。⇒ 「重复导入」是支持的（同 run_id ⇒ `rmtree` + 原子改名重解，
新包胜；账本按 it 幂等合并），且不需要清目录重来。

同时把两列补齐：

* `expectedGames ← report.games`（否则控制台 `ticks/expectedGames` 恒 0）；
* **产物行现在带 `dimMeans`/`scoreStats`**（`remote/artifacts.metrics_row`，additive，~17 个
  float/轮）：本机账本行一直是带的（`rl/events` 读 `report.dimMeans/scoreStats`），而产物行
  只留摘要 ⇒ 导入的那条腿在 kills/accuracy/loot 与 score 列上恒空，同一张表两条腿列不可比。
  现在产物行带上、导入器按分层路径搬进账本。**旧包没有这两块** ⇒ 那几列仍留空（不写 0：
  「缺数据」与「真的零击杀」不是一回事）。

顺带坐实了 §27① 的根因：那个包的 `metrics.jsonl` 里**有两条 `it:1`**（`bff93df1…` ts=…2424
与 `5f099e55…` ts=…3323）—— 与 hub 400 里那句「行记 `bff93df1`… 实得 `5f099e55`…」逐字对应：
旧 `_row_for` 取的就是**第一条**（上一会话的）行。

### 验证

`nn-python-gate` 2128 passed（连跑绿）、根 `bun run check` 绿、`dashboard` typecheck+test
1103 passed 绿。测试新增：`tests/test_deliver_zip.py` 三条（导入 ⇒ 账本 / 字段搬运（含
`expectedGames`）/ it0 与坏行不进账本）；`tests/test_offline_deliver.py` 三条
（`test_rejected_round_is_skipped_but_the_rest_keep_flowing` /
`test_row_is_picked_by_matching_bytes_not_by_first_same_iter` /
`test_row_with_mismatching_fingerprint_is_dropped_not_sent`）；`tests/test_offline_notebook.py`
一条（引导模块每次刷新 + 实际加载那份的 sha12）。

---

---

## §26 离线课「点训练 = 重打包」：旧包先作废，导出失败再恢复（2026-09-22）

用户口径：**「重启离线课程时，需要把最新代码重新打进任务包，因为代码可能已经发生了变化」**，
并且明确**时机 = 用户点击「训练」那一刻**（`openCourse`），**不是** worker 取任务时实时打包
——hub 的 `/offline/task-pack` 永远只递盘上那一刻的文件，不现场构造（无隐藏的第二份真相）。

* **触发点**：`course-lifecycle.openCourse`（离线模式）→ `launchTaskBundleExport(c)`。停课 →
  重新开课即重打一份；课已开着再点「训练」也会重打（开课无「已开课即拒」门）。
* **为什么必须先把旧包作废**：包是**代码快照**（`code.zip`/`ts_code.zip`/`commit` + `code_sha256`），
  而导出要跑几分钟。若只在后台重导、旧包仍躺在 `tmp/<课>/task-<课>.zip`，云机会在导出窗口里
  探到**旧代码的包**并拿它跑完整段——且看起来完全正常。
* **作废 = 挪走不是删除**：`tmp/<课>/stale-packs/task-<课>.zip.stale-<时间戳>`；此后
  `/offline/task-pack` 404，而云机 `obtain_pack` 的等包循环本就按「404 → 稍后重试」处理
  （默认 30 分钟）⇒ 它等新包，不退回旧代码。作废排在 `exportGuard` **之后**（拒启 = 零副作用，
  与课程生命周期的既有约定一致）。
* **作废的反面必须有人管**：导出启动失败 / 子进程中途死掉 → 盘上就没包了，而旧包是此刻唯一
  还能跑的东西。两道恢复：启动 throw 时立刻 `restoreTaskBundle`；进程退出后 `watchExportExit`
  兜底 `restorePackIfMissing`（线上路径没新包 ⇒ 把**最新**归档搬回来，并往 `export-bundle.log`
  追一行 `[作废兜底]`）。
* **回执必须说出来**：开课/导出的 detail 里明写「旧包已作废 → 导出完成前云机取不到包（404），
  它会等新包——这是预期行为，不是故障」，否则「云机拉不到包」会被当成事故排查。
* 测试：`dashboard/tests/server-api-task-bundle.test.ts`（作废挪走不丢件 / 幂等 / 无包不报错 /
  失败恢复不覆盖新包）。

---

---

## §25 离线整段五件套：云机按计划采够样本 / 回传与 PPO 并行 / 云上 A 层 eval（与 PPO 并行）/ 多课程串行 / 断点续跑锚点（2026-09-22）

用户指令五条（原文见下），一次做完并把「同口径」做成**构造性质**（共用同一份函数）
而不是靠人比对。

| # | 指令 | 落地 |
|---|---|---|
| 1 | 云端按 `target_transitions` 采够样本，与本地集群 rollout 一致 | `rl/volume_waves.volume_block/initial_wave_pairs` 抽成共享纯函数；计划带 `volume` 块，`pairs_for` 走它重放；训练侧 `per_stage_quota` 随逐轮 manifest 传递 |
| 2 | 回传权重/opt/指标与 PPO **并行**，失败不打断 PPO | `offline_deliver` 后台单写者线程（`submit_round/submit_final/close`）+ `run_loop` 三出口有界 flush；同步模式保留（`background=False`） |
| 3 | notebook 增加「是否在云机跑 eval」 | `CFG.eval_on_cloud`（+`eval_slots`/`eval_game_timeout_sec`）→ `--eval-on-cloud`；语料/行/结算全部取自 in-loop 同一份实现 |
| 3b | 云机 eval 与下一轮 PPO **并行**（eval 吃 CPU，不该阻塞 PPO） | `remote/offline_eval.CloudEvalRunner`（后台线程 + 单飞 + 有界交接 + 段末有界 `drain`） |
| 4 | `CFG.course` 支持多个离线课程名，云机串行逐个完成 | `offline_boot.courses_of`（字符串/列表/逗号通吃，按序串行，中途失败停在那里并列出剩余）；`run_one_course` 逐课一层工作目录 |
| 5 | 断点续跑：回传或人工导入的权重/opt/指标，再领任务时交给云机续跑 | hub `GET /offline/resume`（+`/offline/resume/blob`）选**最新同轮齐全**的轮次；云机 `fetch_resume` → `run_loop --resume-dir` → `apply_resume_overlay` 采纳 |

### 「同口径」在哪几处是**同一份代码**

* 语料：`rl/eval_local.a_eval_seed_list`（双轨锚点 50 + 当轮轮转 50 也在内）——`remote/offline_eval.eval_pairs` 只是 `[ (s, sd) for s in stages for sd in a_eval_seed_list(it, n) ]`；
* 行 schema：`rl/eval_local.eval_row` 是**唯一**的行构造点（in-loop 派发器 `record()` 里的 60 行字典已删、改调它）；
* summary：`settle_eval_summary`（含双轨 `anchor_wr/rotor_wr`、技能子指标、掉落三列、`nodes` 分布）；
* `wver`：权重**文件字节的 sha256**（`ArtifactStore.sha256_file` ≡ `dist_common.weights_fingerprint`）⇒ 云机评的 W(it) 与本地/节点评的同一 W(it) 在账本里同 wver，可直接配对。

云机局的 `node` 字段写 `"cloud"`（与 `local`/节点 id 分开，summary 的 `nodes` 里一眼可辨）。

### 并行语义（本次最容易被后人改回去的一处）

`CloudEvalRunner`：`submit(it)` **立刻返回**（后台线程跑局）、同一时刻只评一轮（上一轮还在飞时
有界等 120s 交接，仍不空闲就**跳过本轮**而不是排队——排队的 eval 只会越落越远）；段末 `drain`
给在飞的局 600s 有界时间落账（超时只记 WARN：已落的逐局行有效）。三个出口（complete/noop/failed）
都调 `_close_eval`，且**先收评估再 finalize**——否则 artifacts.zip 里少掉刚评的那一段。
回归钉在 `tests/test_offline_eval_cloud.py::test_submit_does_not_block_ppo`（量的是提交耗时 < 0.5s，
谁把它改回同步这个用例立刻红）。

### 读数回程（三条，都必要）

1. **artifacts zip**：`ArtifactStore.finalize` 把 `eval_log.jsonl` 打进包（原来只打 plan/manifest/
   readme/state/metrics/it-*——云上白评一轮的那种漏）；
2. **人工导入**：`remote.deliver_zip` 导入时把包里的 `eval_log.jsonl` 并进课程账本
   （`<traj>/<课>/eval_log.jsonl`，按 `(iter,wver,stage,seed)` 去重；summary 不并——按合并后的
   台账重算更可信）；
3. **实时补传**：`_post_artifact` 的体里带 `eval_rows`（只本轮的、无 `source` 的逐局行，上界 400 条），
   hub 侧 `_HubQueue.merge_eval_rows` 并进课程账本 ⇒ 段内就能在板子上看到读数。
   ⚠ **时序陷阱**（并行带来的顺序后果）：产物 POST 发生在落盘之后而评估还在飞 ⇒ 第一次投递
   时这一轮的 `eval_rows` 还不存在（若只做这一条，实时路径会**永不**带上读数——看着实现了、
   实际零命中）。处置：`CloudEvalRunner` 的 `on_round_done` 钩子在评估落账后调
   `OfflineDeliverer.submit_eval_round(it)` 把这**已投递的轮次重投一次**（hub 对重复投递幂等
   但仍并账 eval 行）——重投排在真积压之后，探活失败则退回队里下次再试。

### 断点续跑的锚点选法（用户口径：必须同轮齐全，否则退到更早轮）

* hub 的两个来源：`<job_root>/offline/<run>/it-NNN/`（自回传）与 `<traj>/<课>/deliver/<run>/it-NNN/`
  （控制台「导入产物」）；三件（`weights.json`/`opt.tar`/`row.json`）缺一就**不认**这一轮，从更大的
  it 往下退；同一 it 两个来源取目录 mtime 更新的那份。缺 opt 只是 Adam 归零、缺 row 只是曲线少一点
  ——两者都「看起来能跑」，所以判据卡在选轮这一步（`tests/test_offline_resume_anchor.py`）。
* 云机侧：`fetch_resume` 三件拿不齐就**整个锚点作废**（半套锚点比没有更危险）；`apply_resume_overlay`
  只采纳比产物当前 `last_it` 更新的锚点，指纹不符即拒（传输损坏不得进产物目录），同轮同名幂等。
* 端点：`GET /offline/resume?course=X` 的 `resume: null` 是**正常应答**（云机要能区分「hub 说没有」
  与「端点不可用」）；blob 端点只服务当前锚点 + 文件名白名单（不是通用文件服务）。

### 别的

* 一个真坑（ruff 抓的）：多课程重构后 `build_run_argv` 里的 `course` 变量已删但引用还在
  （F821）——`--hub-course` 会带空串/直接炸；同处还修了 `RUF034` 的恒假三元。
* `remote/offline_eval` 参与 `sys.path` 时的 stdlib `queue` 遮蔽风险：本模块**不用** `queue`，
  用 `deque`+`Event` 手写单飞（`rl/queue.py` 会遮蔽 stdlib，历史上已踩过一次）。
* 测试：`tests/test_offline_eval_cloud.py`（口径/执行/并行/采纳）、`tests/test_offline_resume_anchor.py`
  （hub 选轮 + 端点 + 补传并账）、`tests/test_offline_eval_wiring.py`（装配/收线/argv/拉取/导入合并/多课程）。
  门禁：`nn-python-gate`（ruff+mypy+pytest 2065 用例）39s 绿、`bun run check` 38s 绿。
* **未做**（下一手可接）：云机 eval 的读数不进 `dist-agent-meta.jsonl`（采样机健康表不含云机腿）；
  云机 eval 用的是云机自己的 CPU（不是节点池）——「云上的 eval 与 in-loop 的节点集群版差多少」
  还没有同权重的对照实测（口径相同，算力不同）。

---

---

## §24 教训：毒包熔断 + worker 崩溃响亮回传 + claim 可观测 + 发布端自检（accident.plan §4.1/4.2/4.3/4.5/4.6，2026-09-21）

**背景**：C-0 it58（job `43a4eb01cf9fe35c`）的 payload 被 hub served **约 40 次**（每 5 分钟
一次，对齐认领 TTL 300s），每次 worker 都在同一处 `BadZipFile` 炸掉、零回传，训练侧只有
3×1800s 超时——**三方合谋烧 3.5 小时，零告警**。§4.0/4.4 已修「炸的那一处」（解包判别先
tar 后 zip）；本轮补的是「**炸了要有人知道**」那一层。

| 项 | 落地 |
|---|---|
| §4.1 熔断 | hub `_JobStore` 新增 `_reclaims`（认领后**零回传**计数）与 `_frozen`（冻结记录），阈值 `FREEZE_AFTER_RECLAIMS = 3`；冻结即移出可领取池 + 一行响亮告警（job/课程/最后认领者/次数）；`/status` 报 `frozen`、`/result` 报 410 + `fail_kind=PoisonFrozen` ⇒ 训练侧**立刻**带原因停腿；人工解冻 `POST /admin/unfreeze?job_id=` |
| §4.2 崩溃响亮 | `remote/worker.py::job_body_error(phase, e)`：restore（model/opt、kickstart ref）与 grad（load_episodes/chunk/ppo_update）段的崩溃按**内容决定性**归 `ProtocolError` ⇒ 走**既有** `report_job_failure → JobFailedError` 链（带 traceback 摘要）；OOM / `OSError` / `MemoryError` 仍按瞬态 `raise` 回去重领 |
| §4.3 claim 日志 | 每次 claim 一行（job/课程/worker/lease/`reclaims=` 非零才带），本次事故现场靠 `payload served ×40` 的 cadence 反推，太贵 |
| 观测面 | `/admin/queue` 每课加 `frozen` 块（谁被冻、冻在几次）；冻结告警一次性（`consume_freeze_announcement`，不会每次轮询重喊） |

**为什么熔断是「独立第二状态」而不是复用失败标记**：`hub_client.py` 规定「重发同 job_id
清失败标记」（重发即重试）。冻结若住失败标记字段，**重发当场解冻**，本次事故照烧 3.5 小时。
两者正交：重发不清冻结（`publish` 不碰 `_frozen`），解冻只走人工入口。

**为什么计数点必须收在一个入口（本轮第二个坑）**：「租约过期」有三个观测入口——`claim()`、
`lease_worker()`、`claimable_job_ids()` 的资格判定。只在 `claim()` 里贴计数会被控制台每秒轮询
的 `/admin/queue`（走 `lease_worker`）抢在前面回收，计数**恒为 0、熔断永不触发**。处置：三者统一
走 `_collect_expired_locked()`（持锁调用），并把这条写成用例（`test_admin_queue_observation_path_also_counts`）。
这是本仓「判据要有唯一入口」的第三次同一教训。

**为什么阈值是 3 而不是 1**：合法重试真实存在（worker 挂一次、换台机器接着跑），3 给了一轮
自然愈合窗口（≈15 分钟），又不放过 3.5 小时的静默空转；拿不准的崩溃归瞬态——**熔断覆盖
「未知死法」，判错方向有兜底，而错钉终局没有**。

**§4.5/4.6 同轮落地（发布端把毒包拦在上传前）**：自检与重打循环长在 `hub_client.pack_payload_zip`
（唯一发布侧打包入口 ⇒ 不存在“某个调用者忘了自检”；重发走同一函数，“重发复用 payload 同样先过此链”
自动成立）。① **reader 全链自检**：调 `protocol.unpack_payload` **本体**（内部调 `_extract_archive`）
再断言解包出的 shard 目录与打包输入**逐名相等**——“能打开”会被本次毒包穿透（它 tar 打开完全正常）；
② **旧判别反向探测**：`zipfile.is_zipfile`（故意用旧启发式，旧 worker 发版前仍在跑），抽成
`_old_heuristic_misjudges` 并写死注释“它存在的意义就是模拟旧 worker”；③ 不通过 ⇒ 重打，但重打**必须
显式扰动**（写 `payload.perturb` = `repack-{n}`）：打包确定性 ⇒ 不扰动就逐字节相同、旧判在同一份
字节上永远为真、闭环永不收敛（这是评审判死原“换 job_id 重打”方案的根据）；④ 上限
`PAYLOAD_SELFCHECK_ATTEMPTS=3`，触顶 `ProtocolError` **响亮放弃、不发布**。

**新发现（夹具是真字节）**：把一整个真 zip 接在真 tar.xz 之后 ⇒ `is_zipfile` 为 **True**
（`_EndRecData` 只验 EOCD 的注释长度自洽，**不**验中央目录），而 `tarfile` 照旧完整打开——
这就是本次事故字节形状的最小重现，也解释了“完好的 tar.xz 为什么会被判成 zip”。
**成本实测（不是推算）**：16 核、240 shard / 480 文件 / 1.77MB ⇒ pack 0.67s、自检 **1.18s**
（+1.2s/轮发布，几乎全在“建 480 个文件”）、探测 ≈0.000s；事故代价 3.5h。
**坑**：试解目录**不能**用 `tempfile.TemporaryDirectory`（回收走 `shutil.rmtree`，本沙箱删除 shim
会 `raise SystemExit` ⇒ `ignore_errors` 拦不住、当场打死线程）——改 job 目录下 `.selfcheck/` +
`rmtree_best_effort`。

**验收（盘上）**：`tests/test_poison_freeze.py`（9：达阈冻结 / 主动 release 不算 / 已结算不算 /
`/admin/queue` 路径也计数 / 告警只喊一次 / **重发不清冻结 + 解冻回池** / 训练侧立刻 `JobFailedError` /
解冻端点 400·404·409 / claim 与冻结各一行日志）；`tests/test_payload_selfcheck.py`（7：真字节分叉 /
探测确实用旧启发式 / 好包一次通过且无扰动标记 / 产物不符·垃圾·缺额外文件三种不通过 / 误判 ⇒ 扰动重打 /
自检失败也重打 / 触顶响亮放弃 + 三次字节各不相同）；`tests/test_job_body_crash.py`（5：内容决定性 →
`ProtocolError`、瞬态原样放回、restore/grad 两段确实被包、已判定 `ProtocolError` 原样上抛）；
顺带修掉一个**既有 flake**：`test_hub_push_dispatch.py` 用例依赖 mtime 判定，Windows 系统时钟
节拍（~15.6ms）内两次写会拿到相同 mtime ⇒ 全量套件下偶发假红；改为显式 `os.utime` 递增。
门禁：`bash tools/githook/nn-python-gate.sh` 全绿（ruff+mypy+**1940 passed**）。

---

---

## §23 引导期大 body 护栏 + wire 账聚合：M1（低速重抽）的收尾（2026-09-20）

**一句话**：M1 的坏签重抽只盖了 worker 侧四段 GET（payload/code/ts_code/blob），而**最贵的两段
跑在 code.zip 之前**（引导 `GET /code` 的 code.zip、离线盘 `task-pack`）——那里连一行进度行都没有；
同时账只有「逐 job 原文」，没有 §7.2 要求的分位数。收尾 = 把同一套判据接到那两个段，并让账可复算。

### 三个口子

| 口子 | 为什么漏 | 落法 |
|------|---------|------|
| 引导期 code.zip / task-pack | 跑在 **code.zip 之前**时 `remote.worker` 还不存在（鸡生蛋） | `remote/tailscale_boot.py::fetch_guarded`；`notebook_boot._pull` · `offline_boot.fetch_task_pack` 改走它 |
| `wire` 只有原文、没有聚合 | 验收口径是 p50/p90 + 每组 ≥10 job，人眼扫不出来 | `nn-training/tools/wire_report.py`（三类行 → 逐段 p50/p90 秒 · p50/worst 速率 · bad% · reroll · 命中） |
| ts_code 缓存命中不进账 | 只漏这一处（code/blob 都记了） | `_ensure_ts_code` 命中即 `_wire_hit(jid, "ts_code")` |

### 护栏（与 worker 侧孪生）

分块读 256 KB + 进度行 + 停滞 45 s（`BOOT_IDLE_TIMEOUT_SEC`）+ 调用方的墙钟预算
（code.zip 300 s、task-pack = 既有 `PACK_TIMEOUT`）+ **首块判一次**的低速重抽
（`max(80KB/s, 本会话最好速率/4)`、预计剩余 >20 s、≤3 次、**末次必硬传**）；`HTTPError` 原样上抛
（401/404 是确定性的答案，不是抖动，由调用方按状态码处置）。
**住 `tailscale_boot` 的理由**：三个 notebook（训练 cell / 连接体检 / 离线盘）都拉它——与
`resolve_hub_url` 同一条「单源一份」；新开文件要改三份 notebook 的 raw 清单（notebook 是要重发的
交付物），而 `net_http.py` 在 code.zip 里、引导期够不到。
**孪生而非共享**：引导期只能 import 它自己与 `tailscale_boot`；两边各自单测钉同一组数字
（`test_wire_reroll.py` · `test_boot_wire_guard.py`），**改一边要同步另一边**。

### 账聚合的一处口径纠偏（重要）

工具有意**不报「速率 p90」**：坏签是**低尾**（13 KB/s 排在排序最前）⇒ 报速率 p90 量到的是好签
那一端，会把 321.8 s 的坏签读成「一切正常」。所以速率只报 **p50 + worst**，尾部看 **bad%** 与
**p90 秒数**（秒数那边坏签就是高尾）。这是 §7.2「报 p50/p90，不报均值」的落地口径。

### 备选与否决

- **往 cell 的内联回退里复刻护栏** —— 不做：那是「GitHub raw 拉不到时的最小 pull 路径」，80 行进
  cell 与「cell 只留 CFG / 凭据 / 保活」正面冲突；代价写在 plan §4.0.1。
- **只加进度行、不做重抽** —— 否：进度行治「看不见」，106 s 的坏签仍要付；重抽成本 ≈ 建连。
- **判据单源化（worker 反过来 import tailscale_boot）** —— 否：方向反了（worker 是产品），且把两个
  已发布、各有测试的实现绑成一个发布单元。
- **`tools/remote_wire_scan.py` 当作已有的复核手段** —— 事实纠正：**该文件不在仓里**
  （`git ls-files` 只有 `test_wire_cf_tunnel.py` / `test_wire_reroll.py`）⇒ plan §1.2 的体积表
  无法就地复现；plan §6/§7.3/§9 已标注，替代口径 = worker 的 `result=` 字节数 + 协议层用例。

### 验证

- 新增 `tests/test_boot_wire_guard.py` **17 例**（判据/停滞正文/超预算/有界重抽/末次硬传/两处接线/
  护栏不可用的兜底）+ `tests/test_wire_report.py` **11 例**（现场原文解析/最近秩/命中不采样/
  重抽不重复计数/CLI）+ `test_wire_reroll.py` 补 1 例（ts_code 命中进账）。
- `tools/wire_report.py` 对现场原文**实跑**：`job:payload` p50 3.9 s / p90 **321.8 s**、worst 11 KB/s、
  bad% 50%、reroll 1（正是 §22 那两次坏签的形状）。
- 门禁：nn python 全量绿 —— ruff · mypy **303 files** · pytest `tests/` + `e2e/`
  （单跑一次记数：**1896 passed in 30.98s**）；完整门禁（含 ruff/mypy 并行）**37s**。

### 遗留（未做，已写进 plan）

- plan §M0（引导↔worker 的 code 交接：同一 sha 一次会话不传两次，现场白传 1.43 MB / 106 s）——
  仍是最优先的下一件（零协议）。
- 实机采一组 ≥10 job 的 `wire:` 样本，用 `wire_report` 的 bad% / p90s 校准 §8-4 的阀值。
- `tools/remote_wire_scan.py` 要重写回仓，否则 §1.2 的体积表不可复现。

---

---

## §22 大 body 低速重抽 + 每 job 传输账：痛在连接抽签的尾部，不在字节均值（2026-09-20）

**一句话**：同机同 hub 的传输速率是 **44× 双峰**（13.5 KB/s ↔ 990 KB/s），所以「瘦身」不是
第一杠杆——**换一条连接**才是。

### 现场（同一台 V100 / 同一 hub / 同一隧道）

| 时刻 | 端点 | 字节 | 耗时 | 速率 |
|------|------|------|------|------|
| 10:41:35→10:43:04 | payload | 2,012,020 | 88.6 s | **22.7 KB/s** |
| 11:25:01→11:25:06 | payload | 4,848,536 | 4.9 s | 990 KB/s |
| 12:49:28→12:49:32 | payload | 3,454,884 | 3.9 s | 885 KB/s |
| 12:49:58→12:51:30 | code | 1,433,893 | 106 s | **13.5 KB/s** |
| 12:51:30→12:51:42 | opt_init blob | —（无进度行 ⇒ <5 s） | — | ≥120 KB/s |
| **12:59:29→13:04:51** | **payload** | **3,486,200** | **321.8 s** | 首连接 **6–9 KB/s** 烧完 300 s 预算；**重试换连接后 354 KB/s**（余下 0.6 MB 只用 5 s） |

12:49 那两行是**相邻 3 秒的同机请求**；12:59 那次「重试」其实就是一次**意外重抽**——只是由
`BODY_TOTAL_TIMEOUT_SEC` 在 **300 s 之后**触发（而不是首块的 ~39 s），321.8 s 里约 280 s 白等。
`urllib` 每个请求新建连接（`net_http.urlopen` → `OpenerDirector.open`，无 keep-alive 池）
⇒ 云机侧 ha-connection / 边缘节点决定这条连接的质量。

### 修法（`nn-training/remote/worker.py`）

- **判据**（纯函数 `_reroll_decision`，阈值只有一处实现）：首块速率 < `max(WIRE_MIN_RATE=80KB/s,
  本会话最好速率/4)` **且** 按此速率预计剩余 > `WIRE_REROLL_BUDGET_SEC=20s` ⇒ 抛
  `WireSlowError`（带已收字节数/速率/预计剩余，**有正文**：同裸 `TimeoutError()` 的教训）。
- **只在首块判一次**（`probed` 一次性）⇒ 每次重抽浪费 ≤ 一块（256 KB），**不可能退化成
  「再整份重传一遍」**。
- **`_get_with_retry` 不退避重抽**（重抽的价值就在快）；上限 `WIRE_REROLL_MAX=3`，且
  **最后一次尝试永不可重抽**——慢链路不会变成「永远下不完」。只对幂等 GET 开放。
- **POST result 永不重抽**（产物上行，走既有 5 次退避）+ 只记账。
- **每 job 一行传输账**：`job X: wire payload=3.32MB/321.8s(10KB/s) code=cache-hit
  result=0.89MB/3.7s(245KB/s) reroll=1(wasted 0.25MB) 合计=…`；零字节命中（code/blob 缓存、
  preloaded）也进账（否则读数失真）；push 模式（`worker_server`）同样 flush；未 flush 的 job
  封顶 4 个（防无界增长）。

### 验收基准与教训

- 验收：12:59 那次若带本改动 ⇒ 首块（39 s 处）即判坏签 ⇒ 预期 ~50 s（而非 321.8 s）。
- 教训：**可观测性先于优化**——「13.5 KB/s 与 3 秒后 ≥120 KB/s」这种对比，只有在有了进度行
  之后才看得见；上一节（§21）加的停滞/进度判据是本节判据能出台的前提。
- 门禁：nn python 全量 **1859 passed**（ruff + mypy 绿），新增 `tests/test_wire_reroll.py`
  17 例（判据/相对阈值/上限/最后一次不重抽/浪费有界/账行形状/产物不重抽）。

---

---

## §21 大 body 传输停滞：两侧同时沉默（2026-09-20，用户报障：云机 claim 第二个 job 后几分钟无动静、无日志）

**一句话**：云机领到 `it2` 的 PPO job 后卡在 payload 下载里 **5 分钟一行日志都没有**——
不是没做事，是**两侧都没有停滞判据**：worker 端 `download_payload` 只有一把 `timeout=300`
的整读（socket 超时抛出的是**没有正文**的 `TimeoutError()`，`_get_with_retry` 把它 `repr`
进日志 = `TimeoutError()`，等于没写；过程零进度输出），hub 端 `wfile.write()` 压根没有发送
超时（对端半开 = 永久阻塞）。而 `/payload` 的访问行属于高频静默规则 ⇒ **两端日志同时一个字
没有**，现场只剩「卡住」。

诱因（同机取证）：`tmp/cloudflared-*.log` 同期每 5 分钟一条
`lookup region1.v2.argotunnel.com: i/o timeout`（DNS 劣化窗口；隧道只有 1 条 ha-connection）——
**小 POST（心跳/轮询）照常、大 body 卡死**是这类劣化的典型指纹。

### 修法（卡住必须有名字、有进度、有界）

| 位置 | 判据 | 行为 |
|---|---|---|
| `remote/worker.py::_read_body` | 空闲 45s（`BODY_IDLE_TIMEOUT_SEC`）无新字节 | 抛**有正文**的 `TimeoutError`：`body 停滞：45s 内没有新字节（已收 N bytes / 共 M）` |
| 同上 | 总预算 300s（`BODY_TOTAL_TIMEOUT_SEC`） | 治「永远在滴水」 |
| `download_payload / code / ts_code / blob` | 进度回调（≥5s 一行） | `job X: payload 下载中 3.20 MB / 4.85 MB (66%) 用时 12s（270 KB/s）` |
| `remote/hub_server.py::_bytes` | 发送超时 60s（`SEND_TIMEOUT_SEC`）+ 256KB 分片 | 停滞即断并打印 `已发 N/M bytes`；≥256KB 的 body 完成时打一行（可对账速率） |

缺省路径未变：`_request` 只在给了 `idle_timeout`/`progress` 的下载路径上改走分块读，
`poll_job`/`post_result` 等小请求行为逐字节不变。

回归：`tests/test_body_transfer_guard.py`（8 例；含真 TCP socket 的「读端不读 ⇒ hub
≤发送超时断开并打印已发字节数」，以及「停滞 ⇒ 一条带原因的日志 + 退避重试」）。

**同窗口的 hub 停服 = 人工操作（用户确认手动关的）**：`stopComponent` 杀进程后正是
`clearAnyComponent`（条目没了 ⇒ exit-watchdog 没有可标记的对象），不是崩溃。
教训：组件级决策只打控制台 stdout、不落文件 ⇒ 从盘上证据无法区分「人工停的」与
「自己死的」——同类报障**先问是不是手动停的**；已修：控制台现在把启/停/重启/判死/开课/停课
全部写到 `tmp/training-start/console.log`（稳定文件名 + 会话头，DECISIONS
§2026-09-20-console-decision-log）。

---

---

## §20 离线训练模式：启动选在线/离线 + 云端整段执行 + 补传归位（2026-09-19）

**用户口径**：启动课程训练时指定在线/离线（缺省在线）；离线 = 下载任务包上云自跑，或由
带特别标识的云端 worker 在线领取；`battle.offline.ipynb` 带「是否实时回传 hub」开关（不实时
回传则跑完统一打包手动导入）；notebook 先试连 hub 取包，不通则等人手动上传。

**四个已拍板**（讨论后）：① 离线启动后本机**发一个整段 job + 保留导包按钮**；② 「特别标识」=
worker **自报能力**（`X-Battle-Offline: 1` / `--offline`），不是课程绑定；③ notebook 实时回传**默认开**；
④ 段内逐轮进度**要**回传控制台。

**模式 → 键的唯一换算**（`stack/specs.ts::trainModeKnobs`）：离线 ⇒ `courses.<课>.{rollout_src:'run',
run_iters:-1}`（声明 + 段长，缺一不可）；在线 ⇒ **撤标记**（删段长；课程级 `run` 也删；别的覆盖
不动）。离线档的 `run` **绝不**写全局 `rl.rollout_src`（那会把所有课一起拖进离线）。

**本轮的另一个收获 —— 一条存量真 bug**（e2e 把它逼了出来）：补传体里原来没有**可用的课程身份**
（`manifest.course_name` 是课程文件的 `name` 字段，与 hub 的课程键 `<traj>/<课>/` 不是一回事）
⇒ **多课程 hub 下每条 `/offline/artifact` 都被 400「无法归属课程」拒掉**，节点侧补传**整体停用**
（体是自己造的，重试不会变对）。训练照常，唯一症状是控制台上段内进度永远空着。

**修法**：归位键由**生产者**带（不是 hub 猜）：领活路径 = hub 在 `/jobs/next` 里下发的 `course`
（本来就发了，只是没人往下透传）→ `run_plan_job(hub_course=)` → `make_deliverer(course=)` → 逐轮体
与段末摘要都带；全离线包路径 = `--hub-course <CFG.course>`（offline_boot 写进 argv）。空 = 单课程
hub（键就是空串）⇒ **不带**这个键，逐字回到旧形状。

**e2e**（`e2e/test_offline_training_e2e.py`，真 hub 进程 / 不跑真 rollout·PPO·eval）三条：
① 离线整段 job —— 普通 poller 先领到**在线课**那份、再轮仍是 None，带标 poller 领到 `kind="run"`
（并自报 `plan_sha256`）；hub 日志有「离线课整段交领」；② 真 `OfflineDeliverer` 逐轮补传 → 落
`<traj>/<课>/remote-jobs/offline/<run>/it-NNN/` → `GET /admin/offline` 报出 `{its,count,last_mtime}`
（控制台 `parseOfflineProgress` 吃的就是这个形状）+ 重传幂等 + 不串到另一门课；③ `GET /offline/task-pack`
200（字节相等）/ 404（人读下一步）/ 401 / 400（路径越界）。

**控制台**：启动弹窗新增「训练模式 在线/离线」（缺省在线；服务端生效值 `run` ⇒ 打开就已选中离线，
否则重新开弹窗再点启动 = 静默拉回在线）；离线启动同时把该课 hub 模式置 offline（整段只给带标 worker）。
总览行新增离线段内进度（`段内 N 轮 · 最近 …`，超 1h 无新产物变醒目 —— 离线课唯一的「死」信号）。

**门禁**：nn python gate ✔ 31s · dashboard typecheck/test（675 pass）/lint ✔ · 三份 bundle ✔ · 根 `bun run check` ✔ 33s。

---

---

## §19 启动训练不选 pull/push 模式：传输成为部署事实，课程与 worker 节点正交（2026-09-19）

**一句话**：控制台启动一门课不再问 pull/push/local——**pull 零配置**（hub 在线 + 可选隧道，
谁在轮询谁领活），**push 只看 `nodes[].gpu_push` 是否登记**（配了就默认走 hub 中介派发）：
`courses.<课>.{remote_transport,push_node_url,remote_hub_url,hub_push}` 四个键从类型表、
python 读面与写面上全部删除，残留值由 `pruneLegacyCourseKnobs` 在启动时清掉。

用户口径：「启动课程训练时，trainloop 不需要指定 pull/push 模式。pull 模式是由远端 worker
自己请求，本机只需要保证 hub 在线，配以 tailscale/cloudflared tunnel。push 模式只看系统是否
已经配置了 push worker 节点，界面留配置入口，节点数据存 rl-config.json」；并再次强调
「课程任务与 worker 节点互相正交！所有 worker 都可能接到在训的课程任务！本地 worker 与
云端 worker 完全一致」。

**传达推（`stack/push-config.ts::remoteExecutionFace`，纯函数）**：登记节点数 = 0 ⇒ `pull`；
有节点 + `rl.hub_push`（**缺省 true**）+ hub 地址/token ⇒ `hub-dispatch`；否则 `direct-push`
（detail 里说清缺哪一项）。卡面徽章、启动详情、总览三处**共用这一份**，不再有「本课指向谁」
的按课认领。

**为什么必须删键而不能只改默认值**：课程定义**任务**，worker 节点提供**算力**。按课程的
`push_node_url` 意味着「同一门课换个机器跑就得改课程配置」，而 `local_push` 那种残留条目
**仍有读者**（python 的 auto 全取登记节点）⇒ 会把训练指向一条没人服务的本机地址，表面
「训练正常」。两个方向都是静默失败，故从写面上彻底移除、从读面上清掉。

**python 侧**：`_course_push_url` / `_course_hub_push` 删除；`_gpu_push_nodes(token)` 不再
按课过滤（登记即全部候选）；`_hub_push_opt_in()` 只读全局 `rl.hub_push` 且缺省 True；
`--remote-transport` 保留为「运维钉死一条路」的最后手段，但控制台不再代写。**冒烟预演的
`REMOTE_PUSH_NODE` 改为独占**——设了它就只它一个，否则伪节点失败时 failover 会把预演的 job
送上真 GPU（「冒烟不该碰真训练」）。

**控制台侧**：启动弹窗删掉模式开关与 push 凭据输入（只剩隧道/瘦身/rollout + 降级 + 预演）；
`console-state.trainerPpo` 与 localStorage `tc.train.mode` 退役，`setMode('trainer.ppo')` 响亮
拒绝；唯一配置入口 = 「push worker 登记」面板（`nodes[]` 增删改 + `rl.hub_push` 开关 + hub/面板
两列探活）；移除 worker 时不再改写「指向它的课程指针」（那个键不存在了）。

**门禁**：nn python gate 绿（ruff + mypy + pytest xdist，含重写的 `test_course_push.py`）·
dashboard `typecheck`/`test` **658 pass**/`lint` 0 · 三份 bundle 绿 · 根 `bun run check` 绿。
决策记录：`DECISIONS.md §2026-09-19-goalnn-course-worker-orthogonal`。

---

---

## §18 多课程单 hub 端到端 + 回环 HTTP 绕开代理（P1 余下 ②）（2026-09-18）

P1 收口：`e2e/test_multi_course_single_hub_e2e.py` 把「一个 hub 进程服务所有并行课程」钉在
**真跨进程**链路上——真 hub 子进程（`--discover --push`，命令行不点课程名）+ 真
`worker_server`（starter 换成写结果的假执行器 ⇒ 不跑 rollout/PPO/torch）+ 训练侧真发布
（`publish_job(dispatch="push")`）与真等待（`wait_job` HTTP 轮询）。

```
训练侧 publish_job(dispatch="push")  →  <traj>/<课>/remote-jobs/<jid>/
hub（真进程，发现课程表）            →  push_client.submit_job（code.zip 随体）
worker_server（真 HTTP 契约）        →  GET /job/<jid>/result
hub accept_result（对账→租约→首写）  →  本课 result/result.json
训练侧 wait_job（控制台读同一份账本）
```

两个用例：① 两门课各发一份 job → 都完成、结果**各回本科目录**、账本不串课、worker
各收一次、派发器 `pushed=2/requeued=0`；② 离线课（`POST /admin/courses?mode=offline` 真热切）
不实时派发（job 停在队首、worker 没碰），同 hub 的在线课照常走完，切回在线后**同一份 job**
（不重发）立刻被推走。

### 实测踩到的四个口径（写进用例注释）

1. **就绪 ≠ 端口能答**：课程表是后台扫描登记的（`DISCOVER_SCAN_MIN_SEC=2s`），端口刚答
   200 时课程表还是空的——只等端口就会假红（首版就这么红的）；等的是「`/admin/queue`
   能答 **且**课程表就位」。
2. **结果落的是 `result/result.json`**（不是 job 根下的 `result.json`），且可领池看的是
   **盘上有没有结果**，不是租约——成功后租约要到 TTL 才消失，所以断言是「无可领的活 +
   派发器自己手上没有在飞」，而不是 `inflight == []`。
3. **`job_completed` 是训练侧写的**（验收落位后 `mark_job_completed`），hub 只写
   `job_pending` + 落结果；用例显式走一次 `mark_job_completed` 并断言「只进本科账本」。
4. `--race off`：竞速广播会把同一份活推给两台，本用例要的是 1:1 派发。

### 顺带修掉的门禁真缺陷：**回环 HTTP 被环境代理截走**

`test_offline_deliver.py::test_offline_endpoints_require_auth` 在门禁里红过一次：hub 日志
明明两次 401，测试侧读到的却是 **502**。根因不在测试——本机用户级环境带
`HTTP_PROXY`/`HTTPS_PROXY`，而 `no_proxy` 写的是 `127.*` 这种通配，Python 的
`proxy_bypass()` **不认**（只认 `host == entry` / `*.suffix` / `.suffix`），实测
`proxy_bypass("127.0.0.1") is False` ⇒ **每一发去 127.0.0.1 的请求都被送到外部代理再转
回来**（代理抖动/回错误页 = 502），本机训练也凭空多一跳。

修法：新增 `remote/net_http.py`（`is_loopback` / `no_proxy_opener` / `urlopen` 替身），
把四条本机 HTTP 出口接上——`hub_client._request`（训练侧↔hub）、`push_dispatch._http`
（hub↔GPU worker）、`worker._request`（worker↔hub，非回环仍用它的显式 ProxyHandler，
Colab 需求不受影响）、`offline_deliver._urllib_opener`；非回环分支刻意仍调
`urllib.request.urlopen`（保住测试里那条 monkeypatch 缝）。

复现→修复（§7）：`tests/test_loopback_http_no_proxy.py`（5 例）——把环境代理指到**死端口**
再打本机真服务，修复前三条出口全部 `ConnectionRefused`（我用临时脚本实测过：raw urllib
URLError、`net_http.urlopen` 200），修复后全绿；另有一例断言非回环仍走 urllib 默认。

只改生产侧还不够：**测试侧另加一层兜底**——`tests/conftest.py` 把精确回环主名
（`127.0.0.1` / `localhost` / `::1`）补进 `no_proxy`（`proxy_bypass()` 认精确匹配），8 个仍用
**裸 `urlopen`** 打本机临时端口的既有用例因此一并脱离代理（实测证据：只改生产侧时
`test_multi_course_hub.py::test_main_discover_picks_up_course_from_disk` 在满载下仍会
在 `/jobs/next` 那一步吃到代理的 `Errno 111`，而 hub 自己毫发无损）。生产靠代码、测试靠环境。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate | ✔ **1320 passed / 3 skipped**（+2 e2e +6 回环用例；ruff + mypy 干净）——连跑两次均绿 |
| 根 `bun run check` | ✔ 19s 绿 |
| dashboard typecheck / test | ✔ 15s 绿（本轮未动 dashboard） |

**P1 到此收口**；余下的「组件卡片按『单例角色 / 按课程』分组」仍按 §17 的说明推迟（属形状
整理，不阻塞任何链路）。

---

## §17 单 hub + 单隧道（P1 余下 ①）：hub/cloudflared 收敛为单实例 + 课程表从盘上发现（2026-09-18）

用户口径（原话）：`hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程，就能同时支持所有并行训练课程`。
本轮做 **hub + 隧道**（trainingLoop 是有状态会话，收敛要等 P2 的任务队列改造，见 DECISIONS 条目的「备选与否决」）。

### 形状：一个进程，一份地址，课程表来自磁盘

```
训练侧（每课一个会话）        共享实例（一个进程）
tmp/<课A>/remote-jobs  ─┐
tmp/<课B>/remote-jobs  ─┼─→ hub-server :<rl.hub_port>  --discover --traj-root <repo>/tmp
tmp/<课C>/remote-jobs  ─┘        ├─ 扫 <traj>/<课>/{remote-jobs,offline}（新鲜 1h 窗口）自动登记
                                 ├─ 每课一条 FIFO + 跨课程轮转（既有）
                                 └─ cloudflared（单隧道 → 同一个 hub 端口）
```

- **课程表不做注册**：训练侧把 job 发布到盘上就是「这门课在跑」的事实。扫描两处触发——`claim_next()` 前置（2s 最小间隔闸 ⇒ 新课程**下一次轮询**就能被领到）+ 后台 5s 节拍（push 模式/无 worker 时兜底）。登记后不撤销（课程暂停时在飞 job 的结果回传不能 404）。
- **新鲜窗口 1h**：陈旧实验目录的磁盘形状与残留 pending job 完全一样，误登记 = 把死课程的活派给真 GPU worker。
- **统计面唯一**：`scopeOf(key, course)` 是共享槽（`''`）的唯一归一入口；`sharedHubPort/sharedHubUrl/sharedTunnelMetricsPort` 是唯一地址来源（grep 门禁守 `slotPort(…, 'hub')` 零调用面）。
- **换代接管**：共享实例启动前把旧形状（per-course）条目活则杀、死则清账；per-course 条目**拒重建**（fail-closed），否则等于凭空再造一个 hub 读同一棵树。
- **单实例切换要搬状态**：`_adopt_solo()` 把 halt / race / worker 登记 / 鉴权计数从那份 `_JobStore` 搬到队列自己身上。
- **停机达令按课程下发**（连带必修）：单 hub 之前「一个 hub 一份 halt 布尔」≈ 按课程；单进程后那个布尔升格为**进程级**，A 课门禁 ABORT 会连坐停掉 B 课云机（表现为「云机莫名停机」，属最贵的静默故障）。故 halt/resume/status 一律支持 `?course=`（空 = 全课程），训练侧（门禁 / 启动清停机态）与控制台各自带课名。

### 验收

| 面 | 结果 |
|---|---|
| nn python gate | ✔ 26s 绿（ruff / mypy / 全量 pytest **1312 passed / 3 skipped**，含 `test_multi_course_hub.py` 24 例，其中 1 例是**真进程** `--discover` 主流程） |
| dashboard typecheck / test | ✔ 542 pass / 0 fail（`single-hub-tunnel.test.ts` 新 7 例；`cloud-halt.test.ts` +2 例按课程达令） |
| dashboard build:ui | ✔ 三份 bundle（app 61.8KB gzip / log / eval） |
| 根 `bun run check` | ✔ 32s 绿 |

### 踩到的坑（都留了回归）

1. **`tmp/*/remote-jobs` 写进了 TS 块注释** ⇒ `*/` 提前闭合注释，tsc 在下一行开始报 `Module declaration names…` 一类怪错。TS 注释里写通配路径要避开 `*/`。
2. **`withScratch(async () => …)` 不 await 回调** ⇒ `finally` 在第一个 await 处就恢复环境变量，异步体后半段跑去读**线上** `registry.json`，而那份里正好躺着历史遗留的 `hubServers[''] = {pid: 1}`（exit-watchdog 事故污染）——测试看起来「读到 pid 1」其实是读错文件。修法：测试夹具必须 `await fn(...)`。
3. **`_pump` 的 8s 上限在满载下会偶发红**：门禁拿 xdist -n 12 跑，假 worker 的 0.5s 探测超时在满载时会把**健康** worker 误判为「没答」⇒ 派发器把它当忙 ⇒ 没有机器能接活（`test_worker_refusing_job_requeues_to_another` 实测红过一次）。修法：假 HTTP 探测超时提到 2s + `_pump` 上限提到 20s（纯测试夹具余量）。
4. **子进程还活着时读它的 stdout 会阻塞**：`assert st == 200, f"{proc.stdout.read()}"` 一旦被改成**先读后断言**（不在失败分支里惰性求值），就变成等 EOF → 整个用例挂到 60s 超时（mypy 还顺手报 `IO[Any] | None`）。修法：`_startup_output(proc)` 只在失败时调用、且进程未退出时直接返回空串。
5. **`# noqa: BLE001` 在这个 ruff 配置下是 RUF100（未启用该码）**——写了反而红，异常兜底的注释直接当普通注释写。
6. **`toEqual` 比 ProcSpec 会因闭包函数不等而假红**（`healthy`/`ownsResource` 每次构造都是新引用）⇒ 逐可比字段断言（沿用 W4 的写法）。

---

## §16 hub 中介 push 派发（P1 余下）：队列顺序推空闲 worker + 周期探活 + 超时回落换人（2026-09-18）

用户指令：hub 中介的 push 派发 + worker 登记入口 + 周期 ping 探活，训练侧 push 改走 hub。
抉择与代价全文见 `DECISIONS.md §2026-09-18-goalnn-hub-push-dispatch`，这里只记落地与实测。

**形状：派发权搬到 hub，训练侧只留一句话。** 训练侧发布时写 `manifest.dispatch="push"`
（= `--remote-transport hubpush`，或 auto + `courses.<课>.hub_push` / `rl.hub_push`），
之后回 `wait_job` —— 与 pull 完全同一条收尾链（三重校验 → 落位 → 记账），**不新增第二条
客户端**。hub 侧新模块 `remote/push_dispatch.py` 认领这份活自己推：

- `PushWorkers`（登记表）：读 rl-config `nodes[]` 里 `gpu_push` 的条目（控制台的 worker
  登记入口回写它们），**mtime 热重载**（写完配置不必重启 hub）；周期 `GET /ping` 更新
  在线/忙闲/排队；运行时增删走 `POST /admin/push-workers`（volatile，**且重载后仍保留**）。
- `PushDispatcher`（派发拍）：跨课程轮转 + **每课程至多一份在途**（课程内轮次有硬序）+
  只推**队首**且队首必须是 push job（不越过它推后面的）+ 四道闸（在线/不忙/在飞<并发/
  不在本次避让名单）；上传与等待在**每份 job 一条线程**里做（几十 MB 的 POST 不阻塞拍）。
- 回落：超时（缺省 45min 兜底）/ 连续探活失败（3 次）/ 拒收（409/428）/ 结果入账被拒
  ⇒ 放租约 + 避开那台 + 立刻换人重推（job 停在该课队首）。
- 结果入账与云机 POST **共用** `accept_result`（对账 → 租约 → 首写锁定）；`_post_result`
  改为调用它 —— 两条腿不可能一条校验一条不校验。

**探活的两条硬判定**（都会在回归里咬）：

1. **从没答过 = 不在线**：宁可这一拍不推，也不往一台可能是死的机器上推几十 MB。
2. **失败但未达阈值 ⇒ 当局忙**：状态未知必须当忙，否则一次隧道抖动就够调度器认为它空闲
   并二次推送同一门课（两份 PPO 抢同一轮）。

**缺省关**：`--push` 才启用（不打开连探活线程都不起），既有单课程用例与线上行为逐字节不变；
控制台经 `rl.hub_push` 透传，`--push-config` 显式指向仓库那份 rl-config（指到 per-course
目录 = 登记表恒空，是最难查的一种配错）。

**回归**（14 例，全用假 GPU worker server 的三件套 `/ping` `/job` `/job/{id}/result`，
不跑任何真实运算、不 spawn bun）：

- 纯函数：登记判据与训练侧 `_gpu_push_nodes` 同尺子（`gpu_push` + enabled 缺省 true + url）；
  四道闸 + 注册序稳定挑选。
- 登记表：mtime 热重载、非 push 节点不进表、探活写回在线/忙闲/计数、运行时增删。
- 全链路：happy path（推 → 结果落盘 → **真 hub 端点** `/jobs/{id}/status|result` 读得到 →
  wire 账 `payload_bytes/body_bytes` 实测）；忙的 worker 被跳过；**离线课一份都不推**；
  失联 → 回落队首换另一台（并断言跑死它的那台**不得**再拿同一份）；超时兜底；worker 拒收
  （409）→ 换台；**被篡改的结果**（data_fp 漂）→ 拒收 + 不落盘 + 换台重跑；队首是 pull 活
  → 一份都不推；没有空闲 worker → job 留队首、无租约残留；**训练侧真发布**
  （`publish_job(dispatch="push")`）走完同一条链；`/admin/push-workers` 读/增/删 + 未启用 409。
- 传输裁决（`test_remote_transport.py` +5 例）：`hubpush` 无条件走（缺 hub/token 响亮拒），
  `push`/`pull` 压过配置，auto 只在 opt-in + hub 齐备时切（旧部署一行不改）。

**门禁**：nn python gate **1301 passed / 3 skipped**（ruff + mypy 干净）、根 `bun run check`
（1851+ pass）、dashboard typecheck / test（309 例）/ `build:ui` 三份 bundle 全绿。

**本轮未做（P1 余下）**：控制台「worker 登记入口」UI 与面板重组（课程 select 自由可切 /
在训课程高亮 / 队列与 push 总览）、单隧道（hub/cloudflared 收敛为单例）、多课程单 hub 的
端到端 e2e（训练侧 hubpush → hub → 真 worker_server + 假 PPO）。

---

## §15 多课程单 hub（P1）：一个进程托管 N 份账本 + 跨课程轮转 + 离线课 + 分课程权重桶（2026-09-18）

用户指令：多课程并行训练流程与操作重组（上限先设 5；hubserver/trainingloop/selfNode/
cloudflared 各只需一个进程；hub 设 PPO 任务队列；离线课不实时派发但收回传；rollout 集群
按课程缓存最近权重；课程数 < worker 数时用单课程竞速；面板重组；e2e）。抉择与代价全文
见 `DECISIONS.md §2026-09-18-goalnn-multi-course-single-hub`，这里只记落地与实测。

**形状：一个进程托管 N 份 `_JobStore`，磁盘契约逐字节不变。** 多课程不是换一套目录约定，
而是「同一进程里多挂几份账本」（仍是 `tmp/<course>/remote-jobs` + `training_log.jsonl`）。
这条取舍换来的是：既有 100 个单课程 hub 用例、`tmp/<course>` 约定、诊断工具全部照旧，
回滚只需改启动参数。

- `_HubQueue`（调度面）：路由（job_id → 课程，扫 job 目录一次并缓存）+ 每课程 FIFO +
  **跨课程轮转**（`rotation_order` 从上次派发的下一门开始）+ 离线课不参与 + 观测面。
- `claim_next(worker_id, race)`：挑活的唯一入口。超时回收时记下「谁跑死的」，在**还有
  别的活跃 worker** 时避开那位前持有人（用户口径「回落队首并改为推送其它 worker」）；
  独苗时允许自领（否则那台 worker 永远空转）。
- 竞速口径：`race_decision(..., active_courses=N)` = 「窗口内不同 worker 数 > 在派发课程数」。
  **缺省 `active_courses=1` 时与旧口径逐字节等价**（`< 2` ⟺ `<= 1`）⇒ 旧用例一行未改。
- 鉴权面提取 `_AuthGuard`（进程级一份，不按课程各算 ⇒ 封禁阈值不会变成 5×N）；单课程队列
  **借**那一份 store 的鉴权/竞速/停机状态。
- 权重桶按 `(course, kind)`（`tools/agent/weight-buckets.ts`，纯逻辑出列）+ 上传前预检
  `GET /v1/weights?sha=&kind=&course=`（命中连体都不传）。课程身份走进程级 `RL_COURSE_NAME`
  （在 `apply_course` 挂——训练进程唯一知道课程名的地方）。

**实测踩到的两个真缺陷（都有回归）**：

1. **「找不到归属」不能写成空串**：单课程队列（与旧单课程 hub）的课程名**就是空串**
   （`tmp/nocourse` 那套约定）。`course_of` 用空串兼作缺失值 ⇒ 单课程下 `/jobs/next` 刚派
   出的 job 立刻解析不到归属、handler 打到哨兵路径上 **500**（每一次拉活都失败）；
   补传路径同款 ⇒ 单课程每一次补传 **400**。两处都改成 `None` 表缺失。
2. **避让的判定时序**：在队列层「先读 stale 记录再比身份」恒为空——那一刻过期租约还没
   被回收，stale 记录还没写。修正为「闸在队列层（`may_avoid_stale_holder`：有身份 +
   还有替班），身份比对在 store 内」（那里才是回收之后的最新状态）。第一版就是这么写的，
   回归测试当场抓出来。

**另一条实测口径**：避让的闸看的是「窗口内活跃 worker 数」（180s），而租约 TTL 是 300s
——一个 worker 停 poll 超窗口即判离场。测试里必须按真实节奏刷 `note_worker`（活着的
worker 每几秒就打一次 `/jobs/next`），否则两台都被当离场 ⇒ 活跃数 0 ⇒ 避让不开（这是
**对**的行为：连一台活的都没有时，避让只会让这活没人干）。

**门禁**：nn python gate **1270 passed / 3 skipped**（+29 例：`test_multi_course_hub.py` 17 +
`test_weight_course_buckets.py` 12）；根套件（含 `tests/agent/weight-buckets.test.ts` 13 例）
全绿；ruff + mypy 干净。

**本轮未做（P1 余下）**：hub 中介的 push 派发（hub 主动推给空闲 worker + worker 登记入口 +
周期 ping 探活）与训练侧 push 改走 hub；单隧道（随单 hub 自然成立）；dashboard 重组；多课程
单 hub 的 e2e。**P2（用户已拍板）**：训练循环**迭代任务化**（一轮 = 一个任务、执行器不跨轮
持状态、状态全在磁盘），验收 = 「断开续跑 == 连续跑，逐字节等价」；允许牺牲 T4 预采、允许
本机降级改每轮载入、eval 尾巴改盘上轮询。

---

## §14 本机 hub 地址走凭据（`HUB_IP`）：两个 notebook 同键同口径 + 一条对账守卫（2026-09-17）

**用户指令**：`battle.tailscale.ipynb`，把本机 ip 也设置为 secret，避免每次都要手动输入。

**动机**：`CFG["hub_url"]` 是「**每个会话都要手填、值却长期不变**」的值——hub 跑在操作者本机，
Tailscale IP 在设备重注册前是稳定的。这正是 secret 的用途，而它此前是 CFG 里唯一还需要人肉
改的条目；模板值 `http://<本地TS_IP>:8787` 忘了改就会拿一个带尖括号的主机名去连
（错在 DNS 层，比当场点名难查得多）。

**一个键、两种取值**（不必记两套约定）：

```
HUB_IP = 100.64.0.5                     → http://100.64.0.5:8787（配 CFG hub_port，缺省 8787）
HUB_IP = 100.64.0.5:9999                → http://100.64.0.5:9999
HUB_IP = http://hub.tailnet.ts.net:8787 → 原样（换域名/协议/端口都行，同机 127.0.0.1 同）
CFG hub_url 含 "<" → 视为未填（返空串，由调用方响亮失败并点名该填哪个键）
```

`HUB_IP` 有值时**压过** CFG `hub_url`；未设时按 CFG 手填——老会话（没建这个 Secret）行为不变。

**单源一份，两个 notebook 共用**：解析逻辑住在 `remote/tailscale_boot.py::resolve_hub_url`，
消费点是 `notebook_boot.run()`（真跑）与 `diagnose()`（体检）——两个 notebook 都从 GitHub raw
拉这**同一个文件**（体检那边只拉它一个），所以口径不会两边漂。

**与 §2026-09-17-kaggle-cred-before-proxy 同一条时序约束**：`HUB_IP` 与另三个凭据**同批在
引导之前**读。它同样只能公网取（平台 Secrets），引导后 userspace 代理只转发 Tailscale IP ⇒
读出来会是空串；而若它被读成空，症状是「连不上 hub」——与真正的原因（地址没读到）看起来
完全不像，是最费排障时间的一类假象。

**notebook 侧改动**：

| 位置 | 改动 |
|---|---|
| 训练 cell CFG | 新增 `hub_ip` / `hub_port` 两条；`hub_url` 降为「手填兜底」 |
| 训练 cell 内联回退（远端模块拉不到时唯一的活路） | 复刻同一段解析 + **打日志记来源**（`hub = … （来源：HUB_IP / CFG hub_url）`），失败信息同时点名 `HUB_IP` 与 `hub_url` |
| 体检 cell | 自己读 `HUB_IP` 并把 `hub_ip`/`hub_port` 喂进 `diagnose()` |
| 两 cell 的说明（markdown） | 凭据清单补 `HUB_IP`；pull 行不再指向一个要手改的 CFG 值 |

体检 cell 必须同口径，否则会出现「**体检说连不上、真跑却连得上**」这种最难信的一种诊断结论；
`diagnose` 里那句「未配 hub_url，跳过」也改成「未配地址（HUB_IP 凭据 / CFG hub_url）」——
体检报告里的跳过一次不该让人再去猜该填哪个键。

**回归守卫**：新增 `nn-training/tests/test_notebook_hub_ip.py`（**14 例**）。notebook 不 import
仓库代码，所以按 `test_tpu_probe_notebook.py` 的做法把 cell 文本抠出来**独立执行**——
但不止「有这几行」，而是把内联回退算出的 `_hub` 与 `resolve_hub_url` 的返回值**逐例对账**
（8 例取值 + 2 例未填→`SystemExit` 且点名两个键 + 缺省端口跟随 `HUB_DEFAULT_PORT`），
再钉两个 cell 的读取位置早于代理引导、体检 cell 把 `hub_ip`/`hub_port` 喂进 `diagnose`。
先红后绿验过：把 cell 里的缺省端口字面量 `8787` 改成 `9999`，对账断言当场红。

**存量测试立即咬到新凭据**（这是好信号）：`test_bootstrap_proxy.py` 的「凭据前置」用例
（假 secret 只认三个旧键）在新凭据加入后立刻 `KeyError: 'HUB_IP'` —— 它断言的正是
「任何凭据在 tailnet 代理生效后被读即红」，说明这道闸门对**新增凭据**也是自动生效的。
已扩为四键并顺手加固两处：`calls` 断言四个键的读取顺序；`captured["hub"]` 断言
HUB_IP（100.64.0.5）**压过** CFG hub_url（故意留成不同的 100.64.0.9，好让「压过」可观测）；
另加一例「HUB_IP 未设 ⇒ 回落 CFG hub_url」。

**门禁**：nn python gate **1238 passed / 3 skipped**（+15 例；ruff + mypy 干净）；
根 `bun run check` **1851 pass / 0 fail**。

---

---

## §13 「云端同机 rollout + PPO」全链路集成测试：一条真链路，两个面板读法（2026-09-17）

**用户指令**：写一个集成测试，确保「云端同机 rollout + PPO」全流程畅通，本地 dashboard
能正常读出云端回传的**每 it 权重与指标**；rollout/PPO 用假节点，不跑实际运算。

新增 `nn-training/e2e/test_cloud_iter_e2e.py`（单用例，**~2.0s**，进门禁 e2e 层；见文末"墙钟"）：

```
训练侧 TrainingLoop._remote_iter(it)     真 build_iter_spec + 真 publish_job（磁盘 IPC）
  → 真 hub-server（127.0.0.1 临时端口，真租约/真账本；单 worker ⇒ race=false 独占）
  → 假云机（本测试线程）：真 /jobs/next 领取 → 真下载 payload/code/ts_code（逐 sha 对账）
      假 rollout（按 argv 逐局写 w{i}/rl_sX_seedY + _rl_report.json，**不 spawn bun**）
      → 真 verify_shards（实产集 == 声明集）+ 真 combine_reports（聚合口径不假）
      假 PPO（换一份权重 + opt tar + agg，**不碰 torch**）
      → 真 validate_result + 真 pack_result_v2（v2 线格式）→ POST result
  → 训练侧 真 wait_job → 真 verify_and_land（三重校验 + 权重原子落位 + opt 解包）
  → 真 _export_weights（归档）→ 真 _record_iteration（iteration 账本）
```

**断言面 = 面板的三个真读者**（python 侧独立重实现其读法，并把口径钉在面板源码的字面量
上——双端锚，同 `tests/nn/schema-fingerprint.test.ts` 的思路）：

| 面板面 | 源码 | 本测试断言 |
|---|---|---|
| 每 it 权重 | `eval-board/ckpts.ts::iterFromCkpt`（`/\.it(\d+)\./`）+ `buildEvalCkptsView` | 归档名解出 iter==7、归档/活动指针字节 == **云端回传**的那份（≠ 本地 init） |
| 每 it 指标 | `server/iters.ts::readIterMetrics` | iteration 行字段**存在且非空** + 逐值对账（winRate 0.5 / score_mean 0.1 / dim_means.kills 0.5 / rollout_sec=节点自报 / ppo_cloud_sec=云真训练秒 / wire.rollout_src==node；M0 计量：hub 实测 `up_bytes`==节点 payload 字节、`down_bytes`==result 体字节、`wire.worker.payload_bytes` 同值） |
| job 账本 | `api/ppo-queue.ts::detectPpoQueueStall` | `job_pending` → `job_completed` 有序、`result/result.json` 与 `claimed` 标记齐、无 `fail.json` |

**为什么单独断言「字段存在」**：`readIterMetrics` 用 `Number(r.x ?? 0)`——写入侧漏一个字段
会被**静默读成 0**（曲线掉零、表格 0% 而不报错）。这是读者**不会**替我们发现的回归，
只有存在性断言能抓。

**失败卫生**：假云机任何异常都 (a) 记进 `node.errors`（主线程先断言它，根因不被超时掩埋）、
(b) `POST /jobs/{id}/fail` —— 真链路上这条让训练侧 `wait_job` 立即带原因收兵，测试红了也
是秒级；训练侧另跑在独立线程 + 45s 限时监督（不会陪 `wait_job` 等满 30min）。

**边界（其余面由既有测试承担）**：push 直推云机 → `e2e/test_push_mode_integration.py`；
真 bun rollout → `tests/test_remote_iter_real_bun.py`（本机无 bun/权重时 skip）；
竞速输家叫停仍是后续项（旧的竞速广播条，§67）。
（**2026-09-23 注**：竞速随下面 P3 整体退役（见 §30），此后续项已消失；§67 本体亦随 2026-09-23 拆分删除。）

**墙钟（首版 6.8s → 现 ~2.0s）**：掐表后最大的那块不是算，是**等**——`wait_job` 的
`poll_sec` 生产默认 **5s**（为隧道抖动设计的节奏），而假云机在 publish 后几百 ms 就把结果
POST 上来了 ⇒ 训练侧仍要等满下一个轮询窗。实测分解：**1.0s** 准备/打包/发布（code.zip 238
文件 + ts_code.zip 340 文件 ~1.4MB + tar.xz payload）+ **5.0s** 轮询窗 + 收尾。
⇒ 加 `fast_wait_poll` fixture：**只**把 `poll_sec` 调成 0.05（`wait_job` 本体全真跑：404 续等、
410 带原因收兵、5xx 退避、超时前 `/status` 二次确认）；真 CLI 解析器 import 提到模块级。
现测 1.99 / 2.00 / 2.08s，占空降到 ~2s（剩下的都是真工作：两包打包 + 真落位）。

**门禁**：nn python gate **1223 passed / 3 skipped**（+1 例）；根 `bun run check` 1851 pass / 0 fail。

---

## §12 产物补传：中途能连上 hub 就自动恢复在线回传（2026-09-17）

**为什么记这一笔**：这是全离线（§11）需求的后半句，也是**第一条「在线能力可选」的链路**
——它之前的每条腿都以「hub 可达」为前提，而这条腿必须**既不影响训练、又能在断网后自愈**。
决策与备选（含六条否决）见 `DECISIONS.md §2026-09-17-goalnn-offline-reconnect-delivery`。

```
节点每轮：checkpoint 落盘（自洽）→ sync()：探 GET /ping（带 token）
  ├ 不通 / 5xx     → 静默跳过（节流记一行），下轮再试           ← 训练继续
  ├ 401/403        → **本会话停用**补传（D9 会把每轮重试的自己封掉；配置错重试无用）
  └ 通 → POST /offline/artifact（权重+opt+账本行，(run_id, it) 幂等、首写锁定）
            └ 第一次连上时把之前攒的**积压一次补齐**（pending = 磁盘上有、delivered.json 里没有）
hub：<job_root>/offline/<run_id>/it-NNN/{weights.json,opt.tar,row.json} + metrics.jsonl
     + result.json（段末摘要，覆盖写）+ 账本审计事件 offline_artifact / offline_result
```

### 三个必须先说清的判定

1. **探活走带 token 的 `/ping`，不新增无鉴权 `/health`**：探针要回答「我能不能用这条链」，
   只证可达会让「token 配错」在第一次上传 ~1.9MB 之后才暴露，还多一个向公网泄露「hub 在线」
   的端点。
2. **401/403/400/413 = 本会话停用**，不是重试：D9 的闭锁是「同 IP 五次无效鉴权封 3600s」
   （每轮重试 = 自己把自己封掉），且体被拒说明形状不对（版本不匹配），重试一百次也不会对。
   5xx / 网络异常才走「下轮再试」。
3. **补传不是 job**：没有 pending、没有租约、不进待领池（`claimable_job_ids() == []`，测试钉住）
   ——记一条 `job_pending` 会让控制台显示一条永远等不到工人的任务，还可能被别的节点领走。

### 写测试时按以往教训卡的两个真缺陷（都有回归）

| 缺陷 | 不修的后果 |
|---|---|
| 传输异常从 `_post` 冒到 `sync()` 外层 | 外层兜住了异常但**跳过了 `_save_ledger`** ⇒ 本轮已投递的几轮只在内存里、下次会话整批重传（幂等但白焚流量）。修法：异常收在 `_post`，返回状态 0 = 没送达 |
| 账本行自称的权重指纹没与实收字节对账 | 产物目录**内部不一致**（人改过 / 半截写入）的权重会以「hub 上的产物」身份进入 eval/续跑，而真因在几千行日志之外。修法：hub 侧 400 拒收 |

另外两处防护按「不可信输入」对待（hub 端）：`run_id` 是目录名 ⇒ `sanitize_run_id`
（`[A-Za-z0-9][A-Za-z0-9._-]*`、≤64、拒 `..`、**非字符串即非法**，不做 str() 兑底——宽容只会
把「上游传错类型」变成看似正常的目录名）；声明长度超限直接 413（不读进内存）。

### 验证面（本机可跑的）

`tests/test_offline_deliver.py`(16)：接通后落位/幂等/重启续投/**hub 关机期间零影响 + 重连一次补齐**
（核心需求）/坏 token 只试一次不多出网/体被拒即停用/`SYNC_CAP` 分次/传输异常不破记账/
越界 `run_id` 拒收且不落盘/指纹不符拒收/重复投递首写锁定/摘要覆盖写 + 审计/未鉴权 401/超限 413/
`sanitize_run_id` 纯函数/**真 hub + 替身 run_job 的整段集成**（逐轮在跑的过程中到达 hub）/hub 无人
听时整段照常跑完。

门禁：nn python gate **1171 passed / exit 0**；根 `bun run check` **1853 pass / 0 fail**；
dashboard typecheck 干净 + **468 pass / 0 fail**。

### 未做（不写成已做）

① 真云端（Kaggle/Colab）端到端一次——本机没有节点，整条链只在真 HTTP 端点 + 替身 `run_job`
上跑过；② 控制台里显示补传进度（账本 `offline_artifact`/`offline_result` 已在写，读方未接）；
③ push 形态的节点没有 hub 地址 ⇒ 默认不开补传（需要显式配 URL）。

---

## §11 全离线任务包落地：hub 导出 → Kaggle/Colab 上传 → 云机自主跑完（2026-09-17）

**为什么记这一笔**：新增一条「任务可以离线搬运」的交付面（协议外的文件格式 + 两处入口 + 一个
不可信输入的边界）；决策与理由（含四条被否决的备选）见 `DECISIONS.md`
§2026-09-17-goalnn-offline-task-bundle。

### 与半离线（§10）的关系

```
半离线（kind="run"）：hub **在线**发一次 job（计划随 payload）→ 云机自主跑完 → 回传尽力而为
全离线（task bundle）：hub **导出 zip**（计划+课程+权重+动量+代码+TS）→ 人搬上云（dataset/Drive）
                       → 云机 import 后自主跑完 → 产物落 Kaggle output / Drive
                       → **若中途能连上 hub**，自动补传（下一笔；本 § 未做）
```

半离线解决「云机不依赖 hub 也能跑完」，全离线解决「**hub 关机也能开工**」——任务本身不再走网络。

### 形状

```
hub:  run_rl --ppo remote --run-iters N --export-bundle tmp/task.zip
        └ 不训练、不等待：拼 plan + 复用发布链造 manifest（register=False：不记账本/不进待领池）
        └ remote/bundle.export_bundle：task.json 索引（逐件 sha256+字节数）+ README + 六件套
云机: python -m remote.bundle import task.zip --dest /kaggle/working/battle2-<run>
        └ 逐件对账（不符即拒收，一局不跑）→ 铺成可直接续跑的产物目录（it-{it}/ 起点 + ts_code/）
      python -m remote.run_loop --artifacts <dir> --device cuda      # 或 --bundle <zip> 一步到位
        └ code.zip / ts_code.zip 字节走 run_job 的 preloaded（节点无仓、不联网）
产物: it-NNN/weights.json + opt.tar + metrics.jsonl + state.json + LATEST.zip/artifacts.zip
      Kaggle = /kaggle/working（Save Version 即官方打包下载）；Colab = Drive / files.download
```

轮次对齐（整条最容易错的一处）：loop 的 `it` = 包里要跑的**第一轮** ⇒ `plan.start_it = it-1`、
`max_iters = n`（不是 n-1——这里没有「job 自己那一轮」要扣，`args.out` 就是包的起点）。

### 两处按教训卡出来的硬门

| 门 | 不卡的后果 |
|---|---|
| 导入逐件 sha/字节数对账 + zip-slip 拒绝 + magic 校验 | 一次截断的搬运会变成一堆无法归因的怪结果；恶意/损坏成员可在云机上写任意文件（包是人搬来的，最不可信） |
| standalone 入口「代码可用」硬门（`code.zip` 或 `code_cache/<sha>/` 命中） | `run_job` 拿空 base_url 去下载代码，报一个跟真因无关的错（重试/联网都治不了）——改前 `test_run_loop` 的两个用例正是这么红的 |

注意这个门**只卡 standalone 入口**，不卡 `_run_iteration`：半离线轮（hub 发的 kind=run）的代码是
worker 自己那一轮从 hub 下好、已落进内容寻址缓存的——那里没有 `code.zip` 字节也完全正常。

### 验证面（本机可跑的）

`tests/test_bundle.py`(7)：导出→导入的目录形状（起点 checkpoint + opt + TS 树）；改一个字节就
逐件对账拒收；zip-slip 成员拒收且不落盘；拿错包/坏 zip 在读索引这一步停；导出侧三道自检（计划
sha 不符 / 缺 code.zip / 缺 ts_code.zip）；**导入后 `run_standalone` 无网无仓跑到 end_it**
（替身断言 payload 里带了 code.zip 与 ts_code.zip 字节、`base_url/token` 必须为空），再跑一次是
noop（续跑判定认得出同一段，不从 start_it 重来）。

门禁：nn python gate **1154 passed / exit 0**；根 `bun run check` **1853 pass / 0 fail**。

### 未做（不写成已做）

① **自动补传**（用户同一条需求的后半句「中途能连上 hub 就自动恢复产物在线回传」）——设计已定：
每轮 best-effort `POST /offline/artifact`（按 `(run_id, it)` 幂等、首写锁定）+ 轮末
`POST /offline/result`；`GET /health` 轻量探活；连不上静默跳过、下轮再试；产物目录 `delivered.json`
记已投递项（重启后续投）；hub 侧落 `<job_root>/offline/<run_id>/` 并记 `offline_artifact` /
`offline_result` 账本事件；token 走 secret 不进包。② 真云端（Kaggle/Colab）端到端一次——本机无
GPU 节点。③ 控制台入口（导出按钮 / 离线进度显示）。

---

## §10 半离线整段落地：kind="run"（一次领走整段，节点自主跑完 + 产物可打包下载）（2026-09-17）

**为什么记这一笔**：新增一种 job 语义（跨层协议 + 新的执行器 + 新的产物面），并抓到两个真缺陷；
决策与理由（含四条被否决的备选）见 `DECISIONS.md` §2026-09-17-goalnn-halfoffline-run。

### 需求与判据（用户 2026-09-17）

> 「云机从 hub 领到训练任务（课程、初始权重、代码）后，即使本机 hub 一直失联，它也能全程自主
> 完成训练，并以 kaggle/colab 官方支持方式提供产物（每轮权重和指标）打包下载。」

三问已答：**一次领 = 整段**；**逐轮权重 + 指标 + 可续跑**；**不在云上跑评估**（只记训练指标）。

### 形状：kind="run" 是 kind="iter" 的延长，不是 fork

```
hub（哨兵）                                  云机（自主段执行器）
  build_plan → plan.json（对集入参 + argv 模板 + end_it）
  publish_job(kind="run", plan_bytes=…)  ───► payload: init_weights.json + plan.json + ts_code.zip
                                              ① verify_plan_file（sha / 形状 / 全段对集指纹）→ 一局不跑就拒收
                                              ② run_job(it)（与 kind=iter **同一**执行链）
                                              ③ run_plan_job 尾巴：逐轮合成同构 iter job → run_job
                                                 └ 每轮回传的权重 = 下一轮 payload 的 init_weights.json
                                                 └ opt 走 blob_cache（Adam 动量不丢）
                                              ④ 逐轮落产物：it-NNN/{weights.json,opt.tar} + metrics.jsonl
                                                 + state.json + LATEST.zip；收尾 artifacts.zip
  wait_job(timeout=8h)  ◄───── 合并结果（末轮形状 + iters 明细 + it_end + artifacts 元信息）
  verify_and_land（零新代码：三个指纹逐字段对的是本 job 自己）
  run_segment 事件（逐轮指标进控制台）+ it 跳到段尾，照常走本机结算/归档
```

轮次语义的一条硬约束：**段中间那些轮不派发本机 eval**——它们的权重不在本机归档里，用活指针充
`W(it-1)` 就是刚修过的「eval 标签超前一轮」。段尾权重由下一轮（或收官 drain）正常派发。

### 两个被测试抓出来的真缺陷（都已进回归）

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| 1 | `ArtifactStore.start` 用**调用方传入**的计划 sha 做续跑判据，而目录写盘的是另一份格式（hub 走 `dump_plan` 的规范形 `sort_keys=True`） | 新会话拿同一目录永远算不出相等值 ⇒ 每次都被当成**新段**从 `start_it` 重跑；「关掉会话明天接着跑」静默退化成重跑，只有对着日志才看得出来 | 判据改为**写盘之后**对磁盘上的 `plan.json` 算 sha（自描述）；传入值另存 `plan_sha256_declared` 供审计 |
| 2 | 起点快照（`_seed_start_checkpoint`）往 `metrics.jsonl` 写一行（无 agg/report） | 「账本一行 = 一轮、it 唯一」失效，并逼下游用「过滤掉没有 report 的行」绕开——用过滤器掩盖一条本不该写的行 | `checkpoint(..., row=None)` = 只落 checkpoint 不记账；`_combined` 的 report 过滤器随之删掉 |

两个都是**先写测试、后改代码**；`test_standalone_resume_continues_without_duplicate_rows` 就是缺陷 1 的回归。

### 验证面（本机可跑的）

- `tests/test_plan.py`(8)：对集重放逐位一致（rotate / curriculum / seed-rotate 三模式）、
  argv 模板重定向恒等、`PAIR_ARG_FIELDS` 漏字段在**发布期**就红、`pairs_fp` 顺序敏感。
- `tests/test_run_loop.py`(11)：**注入 run_job 替身**跑整条链——替身自己开 payload 验
  `init_weights.json` 的字节（所以「权重逐轮传下去了」不是自报），逐轮 job 与 kind=iter 逐字段同构
  （逐局 stage/seed、`--wver` = 该轮 init 的 sha），合并结果**过 `validate_result`**；预算/上限/重领/
  失败四种停机点都能续跑且账本 `it` 唯一。
- `tests/test_run_segment.py`(12)：段长与等待上限的解析优先级（CLI > courses > rl > 缺省关）、
  `max_iters=n-1` 的区间语义、`publish_job(kind="run")` 把 `plan.json` 放进 payload 且 manifest 记 sha、
  缺计划/缺规格/带本地 shard/缺 ts_code sha 一律拒发、同一计划重发同 job_id、发布结果过协议层校验。
- 门禁：nn python gate（ruff + mypy + pytest tests/ + e2e）**1147 passed / exit 0**；
  根 `bun run check` **1853 pass / 0 fail**。

### 未做（不写成已做）

① 真云端（Kaggle/Colab）端到端跑一次——本机无 GPU 节点，`run_remote` 这条路没跑过；
② 段内**进度上报到控制台**（长段期间 hub 只有等待，逐轮指标要等段尾才可见）；
③ 控制台启动弹窗的 `run_iters` 选项（现只有 rl-config / CLI）；
④ push 传输的等待预算仍 1800s（半离线的自然形态是 pull：Kaggle worker 在会话内长期驻留）。

---

## §9 节点确定性失败带原因回传控制面：`POST /jobs/{id}/fail` + `/result` 410（2026-09-17）

**为什么记这一笔**：这是**跨层协议**新增（新端点、新终局状态、新的失败分类语义），且改了
「失败即终局 vs 逐轮重试」两条既有语义的交界，按 §5 硬规则入账。决策与理由（含四条被否决的备选）
见 `DECISIONS.md` §2026-09-17-job-fail-report。

### 缺口（用户提问暴露，2026-09-17）

云机**确定性**失败（bun 装不上 / TS 运行时取不到 / argv 非法）此前只落在**云机日志**里：

| 链路 | 旧行为 | 训练侧看到 |
|---|---|---|
| pull（worker 轮询 hub） | `worker_loop` 的 `except ProtocolError` 只打一行 `REJECTED … skip (not retried)`——不回传、不还租约 | `wait_job` 等满 **25 分钟**超时 ⇒ 一行「超时」（把能力缺失写成了网络/排队问题） |
| push（hub 推节点） | 节点 `worker_server` 对 failed 返回 **500** | `push_client.wait_result` 把 500 当**瞬时错误**重试到 **1800s** 预算耗尽 |

两条路都白烧一个超时窗口，且重试后通常再撞同一堵墙。

### 改后契约

```
节点判定「这台机器跑不成」
   └─ POST /jobs/{id}/fail  {reason(必填), kind, detail, worker}      # bearer + 活租约须持有人
        └─ fail.json  首写锁定：已有结果不收失败 / 首个原因胜出（原子 tmp+replace）
             ├─ GET /jobs/{id}/result  → 410 + 原因 + fail_kind + fail_detail
             ├─ GET /jobs/{id}/status  → state="failed" + 原因
             ├─ claimable_job_ids()    → 排除（换节点只是重演同一失败）
             └─ 账本                   → job_failed{job_id, reason, kind, worker}
训练侧：wait_job/ wait_result  → 立刻抛 JobFailedError(reason, kind, detail)
        └─ _abort_node_failure    → 第一次就写 gate_verdict: ABORT（真原因入判决）+ 停腿
逐轮重试：publish_job 重发同 job（同幂等键 → 同 job_id）→ 清掉 fail.json（重发即重试）
```

用途边界（写进代码与决策）：**只有确定性失败走这条路**——瞬时失败（网络/5xx）仍走 `release`
回池、不报 `fail`；`CodeChangedError` 刻意不报（它靠重启进程 + 租约回池自愈，报了会把可恢复的
job 钉死）。

### 改动清单（file → 改后行为）

| 位置 | 改后行为 |
|---|---|
| `remote/protocol.py` | `FAIL_NAME="fail.json"` / `FAIL_BODY_MAX=64KB` / `JobFailedError(RuntimeError)`（携 `kind`/`detail`，文档写清与 Retryable/ProtocolError 的分界） |
| `remote/hub_server.py` | `store_job_failure`（有结果不收 / 首写胜 / 原子写）+ `job_failure` + `POST /jobs/{id}/fail`（`reason` 必填、体有界、租约同 result）+ `_get_result` 410 + `_get_status` failed + `claimable_job_ids` 排除 + 账本 `job_failed` |
| `remote/hub_client.py` | `report_job_failure`（尽力而为，绝不炸 worker 主循环）；`wait_job` 对 410 与收尾二次确认的 `state=failed` 立即抛；`publish_job` 重发时清 `fail.json` |
| `remote/worker.py` | `except ProtocolError` 分支补回报（带 `worker_tag()`=`host:pid`，多机共用 token 时定位现场的唯一线索）+ `_failure_detail`（traceback **尾段**：原因是最后一帧） |
| `remote/worker_server.py` | failed 响应 **500 → 410**（500 在 `wait_result` 里是「瞬时错误」语义）；`set_error` 记 `kind` |
| `remote/push_client.py` | `wait_result` 见 410 → 立即抛 `JobFailedError` |
| `rl/loop_steps.py` | `remote_retryable_exceptions()` 收 `JobFailedError`（不进来就会冒泡到 loop_core 通用兜底杀进程）；`_abort_node_failure` 写 ABORT（真原因）+ 停腿，不耗连败配额、不降级；`_push_job_round` 全节点失败时**原样抛**节点失败而非包成 `RetryableError` |
| `dashboard/src/server/api/ppo-queue.ts` | `job_failed`/`fail.json` 不算「排队超时」；账本读法改 **last-write-wins**（旧口径「出现过终局即关闭」会把重发的同一 job 永久当已关闭，真卡住时不告警） |

### 回归与实测

- `nn-training/tests/test_job_fail_report.py`（**9 例**）：端点/校验/租约/首写/410/status/账本、`wait_job`
  **秒级**失败（断言 `<10s`，旧路 1500s）、收尾二次确认也认失败、worker 回报到位、push 410、
  push round 不被包成 RetryableError、**重发清标记**（不清 = 重试永久钉死，故必须钉住）。
- `nn-training/tests/test_remote_degrade.py`（+2 例）：`JobFailedError` 首败即 ABORT 且 `_remote_fail==0`、
  不降级、`remote_calls==1`（不重试）；在捕获集合里。
- `dashboard/tests/server-api-ppo-queue.test.ts`（+2 例）：`job_failed`/`fail.json` 不误报；失败后重发又被盯排队。
- 实测（2026-09-17 本机）：整个 `wait_job` 快速失败用例含起服务仅 **0.52s**（预算 25min）。
- 门禁：nn python gate（ruff + mypy + pytest tests/+e2e/）**exit 0**（1112 例）· root `bun run check`
  **1853 pass / 0 fail** · dashboard typecheck + **457 pass / 0 fail** + 三份 bundle ok。

### 未做（不写成已做）

- 真远程轮次的端到端验证（本机无节点可跑）。
- 节点侧 preflight **硬门**（计划 §5.3：bun 版本对账不匹配直接拒单）仍只有记录与日志。

---

---

## §8 M3 rollout 上云落地：新 job kind「一整轮」（kind=iter）＋ TS 运行时打包（2026-09-17）


**为什么记这一笔**：这是本仓**训练架构**层面的新增（新的 job kind、新的传输实体、新的执行位置开关），
按 §5 硬规则入账。方案见 `plan/remote-wire-remediation.plan.md` §5；决策与理由见
`DECISIONS.md` §2026-09-17-goalnn-rollout-on-cloud。

### 开工依据（先把话说清楚：这是**人拍板**，不是门开了）

计划 §6 的原文是「门 1 命中 ⇒ M3 直接留档不做」，而门 1 在 M1 实测里已经命中（`http2` 把
2MiB 上行压到 p50 4.8s，见 §6）。本次开工依据是**用户指令**：目标腿是 TPU 实例
（v3-8 = 96 vCPU），rollout 上云在那里收益极高。即：**门的结论没变，是决策权变了**。
收益前提也照旧：≥16 vCPU 的腿才成立（GPU T4×2 = 4 vCPU 直接否，见可行性报告 §3.3）。

### 改动清单（file:line → 改后行为）

| 位置 | 改后行为 |
|---|---|
| `remote/protocol.py` | 新 `kind="iter"`（走 BC 开过的 kind 通道）：追加必填 `ts_code_sha256` + `rollout`；`mode` 红线互斥照旧；`validate_rollout_spec`（argv 白名单 = 只放行 `tools/sim/export-rl-rollout.ts`；`--out`/`--weights` 必须 job 内相对路径；逐局 stage/seed 不重复）；`iter_declared_entries`/`iter_expected_data_fp`（**声明集**的 data_fp，与实产集同一函数两侧各算一次）；result 增 `report` 必填校验（同 BC 分支先例） |
| `remote/hub_client.py` | `pack_ts_code_zip`（`src/**`+`tools/**` 的 `.ts/.jsonc/.wasm`，固定时间戳 ⇒ 内容寻址；**整棵 tools/** 不是只 tools/sim——实测依赖闭包跨到 `../eval/godai-score`）；`publish_job(kind="iter", rollout_spec=…)`：payload **不含 shard**、`data_fp` 用声明集、ts_code.zip 拷进 job 目录、有 opt blob 也仍带 `init_weights.json`（节点要用它跑 rollout）；`verify_and_land` 对 iter 轮按声明集校验（hub 侧无本地 shard 可重算） |
| `remote/iter_rollout.py`（新） | 节点侧执行器：线程池 `bun <argv...>`（cwd = TS 代码根、job 侧路径绝化）→ 逐位校验实产 shard 集 == 声明集 → `combine_reports` 聚合（与本机 rollout 同一个聚合函数）→ 返回 report/shard_dirs/逐局秒数/bun 版本 |
| `rl/iter_job.py`（新） | hub 侧规格构造：**复用** `rl/cmd.build_rollout_cmd`（三导出器 + 课程覆盖 + D14 血缘的唯一拼装点）只换路径，丢掉 argv[0]（本机 bun 绝对路径在云机上无意义） |
| `remote/worker.py` / `worker_server.py` / `hub_server.py` / `push_client.py` | TS 运行时四件套：内容寻址缓存 `ts_code_cache/<sha>`（**已加进 `prune_job_dirs` 豁免名单**，M2 的 `blob_cache` 就是漏了这个）、`/ts-code-sha` 探测、`/jobs/{id}/ts_code` 下载、`428 ts-code-missing` 门、wire 记 `ts_code_bytes/hit` |
| `rl/loop_steps.py` / `loop_core.py` / `cli.py` | `--rollout-src auto|local|node`（缺省 auto→local）；`_remote_iter` = 整轮上云；node 轮本机**完全不采样**（跳过 `_rollout_phase`，也不预采/不补波）；`_rollout_source()` 读 rl-config（CLI > `courses.<课>.rollout_src` > `rl.rollout_src` > local，D14 血缘：选项永不进 curricula） |
| `dashboard/src/{core,stack,server,web}` | 启动弹窗新增「rollout」选项（本机/上云(节点)/auto）+ 当前生效值；route 白名单（非法 400）；preset 落 `rl.rollout_src`（**字符串域，原样落，不过任何换算**——与 `slim` 的双域相反）；console-state additive |

**回退开关**：`--rollout-src local`（= 缺省；逐字节回到旧行为：本机采样 + 只把 PPO 送云）。

### 安全阀与互斥（配错要响亮，不许静默降级）

1. `rollout_src=node` **与 `--target-transitions` 互斥**（补波要求训练侧反复读本地 shard，而上云轮的
   shard 在节点上、跑完即毁）⇒ 配错即 `SystemExit`；
2. 发布 iter job 时 traj 目录**已有本地 shard** ⇒ 拒发（否则 = 双份采集，且下一轮又会被自己拒绝）；
3. 节点侧实产 shard 集 ≠ 声明集 ⇒ `ProtocolError` 拒收（漏局/多局/`wver` 漂都不会因为重试而变好）；
4. `opt_sha` 有值而 blob 取不到 ⇒ 响亮失败（M2 既有规矩，上云轮同样适用）。

### 控制面迁移清点（计划 §5.4 说的「最容易漏一半的地方」）

| 读取点 | 上云轮处理 |
|---|---|
| 本地采样 / 预采 | loop_core 直接跳过 `_rollout_phase` + `spawn_next_collect`（不是「配成 0」，是**结构上不可能**双采） |
| `rollout_sec` | 用节点回传 `report.elapsedSec`（t_rollout 含「等节点跑完 rollout + PPO」的整段墙钟，拿它当采集时间 = 假指标） |
| `metrics_stats` | **跳过**（shard 不在本地，硬跑只会写一份 `shards=0` 的假精度统计），逐维度口径改由 report 的 `dimMeans/scoreStats` 承担 |
| `_check_quota_incident` | **跳过**（上云轮本地零 shard 是预期，否则每轮喊「检查配额」把真事故淹掉） |
| eval | 零改动（仍在本地 hub 用 bun 跑，权重照旧下行归档） |
| 账本/控制台 | `wire.rollout_src` 落每轮取值；「传输」页已有该列（M0 铺的） |

### 实施中踩到 / 修掉的四个坑（都有回归测试）

1. **`_remote_iter` 绕过了 4xx 立即停腿判据**：上云轮不走 `_remote_ppo_or_degrade`（loop_core 跳过
   `_serial_ppo`），于是 x3-step 事故那种「403 白烧 5×30s 重发同一 job 才死」会重演 ⇒ 在
   `_remote_iter` 里补上同一分类（`fatal_remote_http` → 写 ABORT 判决 + `_leg_abort`）。
2. **`push_client` 的 `428 ts-code-missing` 没置 `need_ts`**：典型场景是「探针说缓存命中、真 POST 时
   缓存已不在」（并发清理 / 两课共享节点），此时 `need_ts` 本是 False ⇒ 一直重发不带 ts 的体、
   白烧满重试预算后整轮失败。与 `code-missing` 的 `need_code = True` 同规。**先复现再修**（§7.1）。
3. **`ts_code_cache` 必须进 `prune_job_dirs` 豁免名单**——否则每轮必 miss，而日志上一切正常。
   这正是 M2 `blob_cache` 的同一个坑，所以顺手把热替换测试的期望集也扩成三棵缓存树。
4. **`pack_ts_code_zip` 的白名单不能只放 `tools/sim`**：`export-rl-rollout.ts` 的依赖闭包跨到
   `../eval/godai-score`（v7 评分口径）。手写「该打哪几个子目录」就是在猜依赖图 ⇒ 打整棵
   `tools/**`，并靠 `test_remote_iter_real_bun.py`（真 bun 真跑）当哨兵。

### 本机验收（能测的都测了）

| 计划 §5.5 条目 | 状态 |
|---|---|
| ① 逐位对拍（节点 shard == 本机 rollout，逐字节 diff） | **已验**：`tests/test_remote_iter_real_bun.py`（真 bun + 真权重 + 真 `pack_ts_code_zip` 解包），同 argv 跑两遍逐文件比对，含 `_rl_report.json` 全字段（除 `elapsedSec`） |
| ② report 等价（winRate/outcomes/ticks 同种子） | **已验**：同上，聚合报告 `totalTicks/totalSamples/outcomes/winRate` 落在单局口径上 |
| ③ 电量账 `up_bytes ≈ 0` | **部分**：payload 确实只剩 `init_weights.json`（单测断言「不含 shard」），但**真远程轮次的绝对值未测**（本机无节点可跑） |
| ④ 可回退 `rollout_src=local` | **已验（构造性）**：非 iter 轮 manifest 不含新键、`shard_dirs` 走原路 ⇒ 逐字节旧行为 |
| ⑤ 协议用例（缺字段拒收 / mode 互斥 / report 校验） | **已验**：`tests/test_remote_iter.py`（51 例）+ `test_remote_ppo.py` 扩 1 例 |

**未做（不写成已做）**：真云机/真远程轮次的绝对值（`wire.up_sec`、每轮墙钟、TPU 腿上的 target ~10s 量级）；
本机也没有跑过「hub + localWorker + trainer `--rollout-src node`」的整条本机闭环（那需要一次真 PPO 轮）。
⇒ 下一步的开门条件：拿 TPU 实例跑 ≥2 个独立 run 各 ≥8 轮，比 `rollout_sec`/`ppo_sec`/`wire.up_bytes` 中位。

### 测试与门禁

新增/扩写：`tests/test_remote_iter.py`（协议 + 规格 + 节点执行器 + ts_code 缓存 + 失败语义 + 控制面两处跳过）、
`tests/test_remote_iter_real_bun.py`（逐位对拍）、`tests/test_remote_ppo.py`（`/ts_code` × `/blob` 两条 GET 端点）、
`e2e/test_push_mode_integration.py`（+5：ts 缓存命中/未命中/428 补传/缺字节响亮失败/真 worker_server 闭环）、
`tests/test_remote_hotswap.py`（豁免名单扩到三棵树）、`dashboard/tests/rollout-src-launch-option.test.ts`（+8）。
门禁：nn pytest+e2e **exit 0**（ruff/mypy 干净）、root `bun run check` **1853 pass / 0 fail**、
dashboard **427 pass / 0 fail** + 三份 bundle ok。

---

---

## §7 远程传输三改 M0–M2 落地：统一计量 + 隧道协议开关 + 协议瘦身（2026-09-17）


**为什么记这一笔**：本笔是**训练架构变更**（云腿线协议 + 校验语义 + 新的运行期开关），按 §5 硬规则入账。
方案与十项验收口径见 `plan/remote-wire-remediation.plan.md`；决策见 `DECISIONS.md` §2026-09-17-goalnn-remotewire-m0m2。
切片提交：**M0 `0bc7a69`**（计量）/ **M1 `fa6a34f`**（隧道协议）/ **M2 `a59b1ef`**（瘦身）。

### 改动清单（file → 改后行为）

| 位置 | 改后行为 |
|---|---|
| `rl/loop_steps.py`（`_remote_ppo`/`_push_job_round`）+ `rl/events.py` | iteration 事件增 additive `wire` 子字典（up/down_bytes、up/pack_sec、blobs_miss、protocol、edge_ip、slim、rollout_src）；`_wire_from_result` 把 worker 半与 hub 半合成一份账 |
| `remote/worker.py` | result 增 `wire`（payload_bytes/dl_sec/unpack/opt_restore/result_bytes/blob_hits/blob_miss_bytes）；`blob_cache/<sha>` 落盘自身产出的 opt；`prune_job_dirs` **豁免** `blob_cache`（否则下一轮必 miss——实施中踩到的真坑） |
| `remote/hub_server.py` | `/jobs/{id}/blob?name=opt\|ref`、serve payload/收 result 时记 `sent_bytes`/`recv_bytes`、`/admin/net-probe?bytes=N`（鉴权 + 固定种子填充，同 bytes 逐字节相同） |
| `remote/push_client.py` / `remote/worker_server.py` | `/blob-sha?sha=` 探测；`/job` 体默认 v2（BRJ2）+ 4xx 一次 JSON 退路 |
| `remote/{protocol,hub_client}.py` | B1 不打占位 `manifest.json`、B2 不打 `opt_init.tar.b64`、B4 有 opt 时不带 `init_weights.json`；B3 `opt_sha`/`ref_sha` 内容寻址（可选键，`slim=false` 逐字节回旧行为） |
| `dashboard/src/stack/{specs,hub,core/types}.ts` + `server/{actions,api}` + `TrainLaunchModal.tsx` | `cf_protocol`(`http2`|`quic`|`auto`，缺省 http2) / `cf_edge_ip`(4|6|auto) 一路到 cloudflared 命令行；抽出 `cfTunnelArgs` 供两处 spawn 共用；复用旧隧道时**比对登记的协议**，不一致则杀旧起新 |

**回退开关**：`slim`（瘦身，缺省关＝旧字节行为）、`cf_protocol`/`cf_edge_ip`（隧道，缺省 `http2`/`4`）。

### 实测数字（本机闭环，`tmp/m2-smoke.log` / `tmp/m2b-e2e.log`）

| 量 | it1（冷） | it2（同会话） |
|---|---:|---:|
| published = hub.sent = worker.payload | 1,683,776 | 1,403,724 |
| blob_hits / blob_miss_bytes | 0 / 0（首轮无 opt_sha） | **1 / 0** |
| worker 结果体（v2 vs 同内容 JSON） | 1,039,382 vs 1,385,656 | 1,041,907 vs 1,389,026 |

- it2 日志出现 `model/opt 从 opt_init（cache）恢复（Adam 动量延续，D5）` ⇒ **1.19MB 的 opt 整轮没过线**（B3 的设计意图）。
- push 侧 B5：真 `worker_server` × 真 `push_client` e2e 实测 v2 体比同内容 JSON 体小 **>20%**（断言在 `e2e/test_push_mode_integration.py`）。
- 门禁：`bun run check` 1853 pass / 0 fail；`bash tools/githook/nn-py-safe.sh -m pytest nn-training/tests nn-training/e2e -q` 310 pass / 0 fail；`bun dashboard/src/server/build.ts` 三份 bundle 通过。

> 📺 **在哪看**：控制台详情抽屉新增「**传输**」页（`dashboard/src/web/app/panels/WirePanel.tsx`）
> —— 上半是每轮 `wire` 账（上行/下行/打包/blob 未命中/协议/瘦身 + 最近 12 轮走势表，数据源
> `metrics.iters[].wire`），下半是 `tmp/tunnel-ab-*.json` 的 A/B 表（p50/p90/max + 倍率徽章，
> 新→旧）。从此不必翻日志/JSON 才能读到这两组数。

### M1 隧道 A/B 实测（已跑：`remote/tunnel_ab_probe.py`，2026-09-17 本机）

探针自建环境（真 hub-server + `cloudflared tunnel --protocol <p> --edge-ip-version 4` × 每腿一条
quick tunnel），每腿每方向 2MiB × N 发；`loopback` 腿作基线。**每臂 2 个独立 run**（run1/run2 各 5 发，
run3 两臂各 8 发）。原始 JSON：`tmp/tunnel-ab-{1,2,3}.json`。

| 腿 | 方向 | run | n | p50 | p90 | max | p50 吞吐 |
|---|---|---|---:|---:|---:|---:|---:|
| loopback | up | 1 | 5 | 0.02s | 0.02s | 0.03s | 890 Mbps |
| loopback | down | 1 | 5 | 0.03s | 0.03s | 0.03s | 576 Mbps |
| **http2** | **up** | 1 / 2 / 3 | 5/5/8 | **4.84 / 4.66 / 4.85s** | 4.84 / 5.90 / 5.39s | 5.26 / 6.49 / 9.41s | ~3.5 Mbps |
| http2 | down | 1 / 2 / 3 | 5/5/8 | 4.80 / 5.09 / 5.30s | 5.03 / 6.61 / 9.26s | 8.72 / 8.42 / 9.44s | ~3.3 Mbps |
| **quic** | **up** | 2 / 3 | 5/8 | **23.42 / 33.04s** | 25.40 / 36.34s | 43.54 / 49.88s | 0.5–0.7 Mbps |
| quic | down | 2 / 3 | 5/8 | 8.50 / 6.20s | 8.90 / 7.49s | 9.77 / 9.07s | 2.0–2.7 Mbps |

**判定（§3.4 判据）**：`http2` 腿 **p50 明显更快**（上行 4.7–4.9s vs 23–33s，**5–7×**；下行 1.2–1.8×），
且 **8 连发无退化趋势**（run3 上行 3.3–9.4s 抖动，p50 仍 4.85s）；`quic` 腿**复现退化**且抖动极大
（p50 23s 而 max 43.5s）—— 与「ISP 对 QUIC(UDP/443) QoS 降质」的病灶签名一致。
⇒ **决策门 1：命中。** 默认取 `http2`/`edge-ip-version 4` 是对的（即 M1 缺省值）。

⚠ **绝对数不可直接换算每轮耗时**：客户端在本机，请求出一遍家宽、响应回一遍（§2.3 已声明）。
真轮次的传输量约 1.2MB 上 + 0.86MB 下 ⇒ 按本表 p50 约 6–8s（远低于门 1 的 20s）。
真实 payload 形状 + 宿主路径下的绝对值仍需云机确认。

### 实施中发现的坑（同批）

- **本机环境代理会杀死隧道探测**：本机设了 `HTTPS_PROXY=http://127.0.0.1:7890`，而 `NO_PROXY` 只含
  localhost/127.0.0.1/内网网段 ⇒ 打 `https://*.trycloudflare.com` 被丢进本地代理，拿回
  `SSL: UNEXPECTED_EOF_WHILE_READING`（而 cloudflared 日志里 `Registered tunnel connection` 一切正常，
  极易误判成「隧道坏了」）。探针客户端已固定 `ProxyHandler({})` 绕过；**云端 worker 没有这种代理**，
  所以按直连量才是对的。
- quick tunnel 的 URL 在 edge **注册完成前**就写进日志 ⇒ 必须先用 `/ping` 就绪门等它真开始服务，
  否则「连接没建好」会被记成「协议慢」，恰好污染本探针唯一要量的东西。

### B6（xz preset 3→6）已量、**不采用**（2026-09-17，本机，真 payload）

测量口径：`tmp/measure-xz-preset.py` —— 取 `tmp/x3-power/remote-jobs/*/payload.tar.xz`（2026-09-15 那次真
self-node push 训练留下的真 job），解包出真 shard 目录，再调**真** `pack_payload`（`PAYLOAD_XZ_PRESET`
按档位改）。3 份独立真 payload，每份 240 个 shard / 裸 ~270 MB。

| job | preset 3 体积 / 打包 | preset 6 体积 / 打包 | 体积 | 打包耗时 |
|---|---|---:|---:|---:|
| `05cb2d44acd0c2d0` | 1,198,096 B / 3.24s | 1,162,552 B / 9.44s | **−3.0%** | **+191%** (+6.20s) |
| `0cb44569e07c7890` | 1,187,392 B / 3.13s | 1,156,264 B / 9.35s | **−2.6%** | **+199%** (+6.22s) |
| `132f5e1cea7c4e16` | 1,227,432 B / 3.47s | 1,192,388 B / 10.00s | **−2.9%** | **+188%** (+6.53s) |

**判定：不动档位**（实测写进 `remote/protocol.py` 常量旁的注释，防止将来重测）。理由：省 ~34 KB，
而按隧道实测 ~3.5 Mbps 那 34 KB 只值 ~0.08s 传输，代价是**每轮关键路径 +6.3s CPU** —— 净亏，
且远低于计划 §4.2 的「收益 <10% 就别做」门槛。解包侧未量到收益（3 份均落在噪声内）。

**副产物：B1/B2/B4 在真数据上的独立验证**（不是合成 shard）：同一批 job 的原盘 payload
（含占位 `manifest.json` + `opt_init.tar.b64` + `init_weights.json`）为 2,045,276 / 2,034,536 / 2,074,996 B，
只打 shard 后为 1,198,096 / 1,187,392 / 1,227,432 B ⇒ **−41.4% / −41.6% / −40.8%**。

### 补记：瘦身开关进启动选项 + 一个真实指标缺口修复（2026-09-17）

**① M2 的 `slim` 现在能在启动训练时选**（计划 §1.4 要求「每个改动都有运行期开关」＋「启动时
提供选项」）。M1 的隧道选项走齐了一条链（config → console-state → route 白名单 → preset → UI →
显示当前生效值），而 `slim` 之前只落了 config 键 + 指标 ⇒ 想 A/B 只能手改 `rl-config.json`，
等于没有「启动时提供选项」这条路。现已补齐同一条链：

| 环节 | 位置 | 行为 |
|---|---|---|
| 类型/双域 | `core/types.ts` `SlimMode` + `CourseConf.slim` + `rl.slim` | UI/console-state 用 `'on'\|'off'`；rl-config **必须** `1\|0` |
| 解析 | `stack/specs.ts` `resolveSlim` / `slimToCfg` | per-course > `rl.*` > 缺省 **on**（与 python `_d("slim",1)` 同口径）；换算只此一个入口 |
| 生效值 | `api/state-view.ts` `modes.slim` | UI 显示「当前生效」，避免「以为改了其实没改」 |
| 契约 | `api/route.ts` preset 白名单 `on\|off`（非法 400） | 与 `mode`/`cfProtocol` 同写法 |
| 落库 | `actions/preset.ts` | `rl.slim = slimToCfg(opts.slim)`（数值域）+ console-state 存 UI 域 |
| UI | `TrainLaunchModal.tsx` 「瘦身」分段控件 | 开 / 关（A/B 对照）；上次选择进 localStorage；当前生效值上屏 |

**② 修的缺口：`wire.protocol` / `wire.edge_ip` 原本**恒为 null***。`_wire_from_result` 读的是
`getattr(args, "remote_cf_protocol", None)`，但 CLI **从未声明这两个参数**（`cf_protocol` 在整个
python 侧只出现在那两行读取处）⇒ M1 §1.4「开关取值必须写进 iteration 事件」实际没被满足：
控制台能显示「此刻生效值」，却回答不了「改用 http2 之后那几轮 vs 之前那几轮」。修法：
`rl/cli.py` 加 `--remote-cf-protocol` / `--remote-cf-edge-ip`（缺省取 rl-config，即控制台回写的键），
`rl/loop_steps.py` 新增 `_course_cf_tunnel(args)`（与 `_course_push_url` 同口径：CLI >
`courses.<stem>.cf_*` > `rl.cf_*` > None；选项住 rl-config，**永不进 curricula**，D14）。
测试 `nn-training/tests/test_wire_cf_tunnel.py`（12 例：CLI 声明与缺省、四级优先级、不串课、
旧 args/坏 config 不炸训练、端到端进 wire）。

> ⚠ 修正一条早前的说法：前文表格里「push 侧 B5」的实测与本次均为本机闭环；
> `wire.protocol` 现在才会真的非 null —— 在此之前「按协议分组统计」是做不到的。

### 未做（不写成已做）

- **M2 云机绝对值确认**（≥8 轮中位 `wire.up_sec`）**未跑**。
- **M3（rollout 上云）按 §5.1 门留档不做**：门 1 已命中（`http2` 把传输打到 ≪20s），门 2 要等云机绝对值，
  且 M2 之后上行只剩 ~1.2MB —— 按 `plan/kaggle-rollout-feasibility.md` 的算术已经是负交易。

### 教训

- **验收 harness 是「待修资产」时先修再量**：`smoke_loopback` 盘上没有近期运行痕迹，实际跑起来三处已腐坏（v2 schema 权重文件、21 列 vs 39 列的 METRICS_DIM、`blob_cache` 被 prune）。**它碰的正好是要改的函数** ⇒ 修它本身就是交付物，而不是绕开它。
- **新缓存目录必须同时改 prune 名单**：`blob_cache` 第一版每轮必 miss，就是因为 `prune_job_dirs` 只豁免 `code_cache`——「缓存命中率」类 bug 会伪装成「协议没生效」。

---

## §6 hub-server 重启死锁收口：D9 只当「无效鉴权」的守门人 + 回环永不封禁 + 端口级实例锁（2026-09-17）

**为什么记这一笔**：本笔含**训练基础设施架构变更**（D9 鉴权/闭锁语义、hub 启动串行化），
按 §5 硬规则必须入账。事故现场：hub-server「自动崩溃后手动重启失败」，控制台只报「意外退出」。

### 一、事故链（三层同族问题，一次收口）

1. **封禁连坐**：旧 `_auth_ok` **先查 `is_blocked` 再验 token** ⇒ 一次误封（本机组件用陈旧 token
   连打 5 次 `/ping`）把该来源 IP 的**全部**流量（console 健康检查、训练循环、worker 拉活）403
   一小时；而封禁只住**进程内存**、只能靠重启清除。
2. **回环当替罪羊**：cloudflared 回源把**隧道流量也全归成 127.0.0.1** ⇒ 回环上的失败里混着隧道
   里的陌生来源，对回环封禁 = 整台机器的服务面连坐（用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）。
3. **重启被自己的守卫挡死**：端口守卫（`_port_guard.ensure_port_free`）是「探测 → bind」的 TOCTOU，
   且 Windows `SO_REUSEADDR` 允许双绑（后启动者静默变僵尸）；旧实例活着占着 8787 ⇒ 新实例被拒 ⇒
   **必须重启才能解封、重启却被自己占的端口挡死**，只能人工杀进程。

### 二、修复（四处）

- **D9 改序**（`remote/hub_server.py`）：先验 token；**合法 token 永远放行**，封禁只拒无效鉴权尝试
  （封禁期内的无效尝试 403，且不再计数/不延长）。
- **回环豁免**（`remote/hub_server.py::_is_loopback`）：`127.0.0.0/8` / `::1` / `::ffff:127.0.0.1`
  上的失败**不计数、不封禁**（`is_blocked` 防御性恒 False）；鉴权边界与 `AUTH FAIL` 审计行不变。
- **原子实例锁**（新 `remote/_instance_lock.py`，`nn-training/.<kind>.<port>.lock`，按端口键控）：
  拿锁 → 端口探测 → bind；陈旧锁按「持有者已死 / 命令行缺本服务指纹（PID 复用）」接管（指纹可
  给多个），身份读不到则 fail-closed 拒启；锁文件**写不下**（只读 FS）则 fail-open + 响亮告警。
  覆盖 `hub_server` 与 **`worker_server`**（push 端口，经 `remote_worker_serve`，2026-09-17 补）——
  worker 僵尸更贵：HUB 会把 job POST 进一个没人应答的监听端口，表现为推送静默卡死。
  自带安全存活探测（Windows `GetExitCodeProcess`——**不复用** `train/loop_util._pid_alive`，后者
  在 Windows 走 `os.kill(pid,0)`＝`TerminateProcess` 会杀持有者；该隐患已于同日单独修掉）。
- **控制台侧**：启动前 `reclaimPort` 回收端口幸存者（`stack/hub.ts`，接在 hub/selfNode）；
  「停止 trainer」释放本课 run_rl/run_bc 锁且**先核验进程身份**再停存活持有者
  （`launch/cli.ts::releaseTrainerLock`）。**`workerServe` 路径故意不接 reclaimPort**：
  worker_server 可能在跑数小时的 PPO job，`/ping` 失败就回收它 = 炮掉在途 job；该路径
  会先复用健康的幸存者，不通时锁会响亮拒启并指向日志（含持有者 PID），交人工处置。

### 二补、隧道来源还原（B，用户点名「确认隧道来源到底该不该计数/封禁」）

**背景**：回环豁免（2 号修复）把隧道入口一并豁免了 —— cloudflared 回源把隧道流量全归成
`127.0.0.1`，于是隧道侧**只 401、不计数、不封禁**，等于对公网暴露面零封禁（D9 只对 tailnet 直连 IP 有效）。

**决定 = B**（方案对比与 C+D/E 被否的理由见 DECISIONS 同条）：`remote/hub_server.py::attributed_source(peer, cf)`
—— **只在「TCP 对端是回环」时**采信 `CF-Connecting-IP`（须是合法 IP 字面量且非回环值）⇒ 按**归因 IP**
计数/封禁；其余（无头 / 头非 IP / 头写回环值 / 对端非回环）⇒ 归因 TCP 对端。直连（tailnet）对端
**只认对端 IP**：那台机器能自己写任何头。审计行带上 `peer=` / `src=` / `via=cf|peer`。

**为什么不需要再加「全局退避闸」**（C+D，曾被列为首选）：退避的收益完全建立在「封禁不可用」之上，
B 一生效就重复了；而 B 若失效（头可伪造），最坏后果**两条都良性** —— ① 轮换头值 ⇒ 拿不到封禁，
退化为回环豁免（不会更差）；② 伪造 tailnet 某 worker 的 IP ⇒ 那只拒它的**无效鉴权尝试**，它带
正确 token 的请求照常放行（改序使然）⇒ 不是对合法对端的 DoS。零收益增量 + 要给每条合法路径加一条
延迟分支 ⇒ 不做。

**未实测假设（登记待验）**：CF 边缘**会覆写** `CF-Connecting-IP`。仓里无 CF 头读取先例、quick tunnel
无 ingress 配置、沙箱无网络 ⇒ 无法离线验证。**实测法（2 分钟）**：向隧道发一次带伪造头的无效鉴权
（`curl -H 'Authorization: Bearer wrong' -H 'CF-Connecting-IP: 203.0.113.7' https://<隧道>/ping`），看
`hub-server.out` 的 `AUTH FAIL` 行 `src=` 是伪造值（⇒ 可伪造，回退 = 删 `attributed_source` 的 cf 分支）
还是真实公网出口 IP（⇒ 假设成立）。**预登记的失效后果**见 DECISIONS 同条，两条都良性。

**访问日志也补上来源**（`log_message`）：回源流量原本全写成 `[hub-server 127.0.0.1]`，正是本次事故
排查的最大阻雾（分不清「本机组件」与「隧道里的陌生人」）；现在归因到 `via=cf` 时写成
`[hub-server 127.0.0.1 src=<真实 IP> via=cf]`，本机组件与直连对端逐字保持旧格式（不加噪）。
`log_message` 只打非常规事件，不刷屏；`self.headers is None` 的早期错误路径有护栏。

**回归**：`nn-training/tests/test_hub_auth_d9_order.py` 11 → **19 例**（归因矩阵 / 隧道源 5 次封禁第 6 次
403 / 被封归因 IP 持合法 token 仍放行 / 本机无头组件仍豁免 / 直连对端自带头不算数 / 伪造头无害 /
访问日志带 `src=` 及两个负例 / `headers is None` 不抛）。
A/B 行为取证（`tmp/cf-red-behavior.log`，detached worktree 跑修复前代码）：
`PRE-FIX: 5 次回环+CF头 无效鉴权返回码 = [401,401,401,401,401]`、`is_blocked(203.0.113.7) = False`、
`_auth_fail = {}` ⇒ 隧道入口确实零计数。

### 三、教训（可迁移）

- **「活着但不健康」的进程是重启链路的头号敌人**：所有自我守卫（端口/单实例锁）都必须能区分
  「真双开」与「上一代残骸」，否则守卫本身变成死锁的一环。判据顺序一律：**存活 → 身份核验 →
  接管/拒启**，身份读不到就 fail-closed 且**响亮打印**（不静默共存）。
- **惩罚性状态机只能惩罚「确定恶意」的那一类**：把合法流量与可疑流量放在同一个计数器里，
  在共享来源 IP（回源/代理/NAT）下必然误伤整机；封禁的作用面必须比鉴权边界**更窄**，不能更宽。
- **取证纪律**：401/403 永不静默（2026-09-16 已立），且审计行必须写「不计数/不封禁」这类**语义**
  说明——否则下一次排障会把「回环不封禁」误读成「封禁失灵」；同理，一旦「对端」不再是封禁对象，
  审计行必须同时打印 `peer=` 与 `src=`——只打一个会让「封的是谁」永远无法事后重建。
- **一次收紧会开出新的口子，必须回头补上**：改序（A）与回环豁免（B）都是「减误伤」，但 A 把封禁
  面从「来源 IP」缩到「无效尝试」、B 又豁免了隧道整整一侧 —— 每一步都对，合起来却让公网入口
  零封禁。这就是 B（2026-09-17 追加）存在的原因：**每次放宽都要重新问一遍「那么谁在守门」**。

### 三补、存活探测的 Windows 隐患收口（同日补修，用户点名）

- **隐患**：`train/loop_util.py::_pid_alive` 在 Windows 侧一直是裸 `os.kill(pid, 0)` —— Windows 上
  那不是探测而是 `TerminateProcess(handle, 0)`：`acquire_lock` 判「锁持有者还活着吗」的**只读查询**
  会直接把持有者杀掉（最坏：打死正在训练的 trainer），而 `except Exception → False` 还会把
  「我杀了它」记成「它本来就是死的」，双开护栏静默失效。`run_rl._runrl_pid_alive` 早期就因这条
  隐患不复用它（自己写了安全分支），loop_util 侧因此露了很久。
- **修**：两处同口径 —— Windows 分支走 `GetExitCodeProcess == STILL_ACTIVE`，并补 `pid <= 0 → 不活`
  护栏（POSIX 上 `os.kill(0, 0)` / `os.kill(-1, 0)` 命中**进程组**、实测成功，会把残缺锁文件里的
  0/-1 当成活人持有 ⇒ 同名课永久拒启）。三处同源：`loop_util` / `run_rl` / `remote._instance_lock`。
- **回归**：`nn-training/tests/test_pid_probe_windows_safe.py`（6 例）——注入假 kernel32 + 监视
  `os.kill`，断言 Windows 分支**零 os.kill**、退出码语义、句柄不泄漏、POSIX 分支不变、残缺锁可清理，
  外加一条行程门禁（三处探测必须保留 `os.name == "nt"` 分支）。
  A/B：修复前红 —— `AssertionError: train.loop_util._pid_alive: Windows 分支不得调用 os.kill，实际: [(pid, 0)]`。
  ⚠️ 坑：伪装 Windows 时 `os.name="nt"` 必须**只在探针调用期间**生效并先于异常还原，否则
  `pathlib` 会把路径解析成 `WindowsPath`，pytest 在报错/cache 阶段直接 INTERNALERROR。
- **同类待收口**：`remote/notebook_runtime.py::_pid_alive`、`tools/tmp-clean.py`（同名写法，未动）。

### 三补二、存活探测唯一化 + 隧道 metrics 端口闸（2026-09-17 第三批，用户点名的两个收口）

**存活探测唯一化**（`nn-training/pid_probe.py`，新增）：18 小时内同类隐患在 3 个不同文件各自
踩过一次——`loop_util` 裸 `os.kill`（会杀锁持有者）、`notebook_runtime` **嵌套闭包**+裸 `os.kill`
（不可测，且用来判断 bootstrap 已起的 `serve_pid` 是否还活 ⇒ 在 Windows 上会把 worker_server
直接杀掉）、`tmp-clean` 缺 `pid<=0`（残锁里的 0/-1 命中**进程组**⇒ `training_running()` 恒真 ⇒
运行目录永远不再收敛，实测 `_pid_alive(0) = True`）。根因面 = 「每加一个调用点就多一份可漂移的
实现」，故收敛为**唯一实现** `pid_probe.pid_alive`（stdlib-only 顶层模块，与 `platform_utils` 同层，
`remote/` 与 `train/` 都直接 import 它——两方向都不能反向依赖对方的包）；四份具名薄壳全部委托；
**唯一保留副本** = 仓根 `tools/tmp-clean.py`（根级开发工具不依赖 nn-training 布局），契约由源码门禁守。
门禁升级：`test_pid_probe_windows_safe.py` 现在把**六处入口**放进同一组断言，并用 AST 断言
`nn-training/` 里真调用 `os.kill(pid, 0)` 的文件**只有 `pid_probe.py`**（AST 而非字符串：新写的
docstring 到处在讨论这个坑，且 `os.kill(pid, 15)` 是**故意发的信号**、不属本不变量）。

**隧道 metrics 端口闸**（`dashboard/src/stack/hub.ts`）：cloudflared 是**第三方二进制**，没法在
它内部装实例锁（hub/worker 那层是 python 自己拿 `O_CREAT|O_EXCL`）⇒ 控制台侧回收是它**唯一**的
一道闸。`stepCloudflared` 在 spawn 前 `reclaimPort(metricsPort)`（与 hub/selfNode 同族），
堵住 `supersedeSlotTunnels` 看不见的幸存者（孤儿/登记丢失/控制台重启竞态）。metrics 端口既是
`--metrics` 的 bind 目标、又是 `/ready` 的探测目标，被占着会**同时**造成「新隧道 bind 失败」与
「就绪读数读自旧僵尸」；后者另加**就绪归属**：`core/proc.ts::portOwnedBy(pid, port)`
（唯一实现，早期叫 `tunnelOwnsMetrics`，因 hub/worker 也要用而改成中性名），就绪 = 「本进程持有
该端口 ∧ /ready 200」。

### 三补三、就绪归属推广到监督器（2026-09-17 第四批，用户点名「监督器那条路径也要」）

`ProcSpec` 新增可选字段 **`ownsResource?: (pid) => Promise<boolean>`**，三处声明：
`hubServerSpec`（hub 端口）、`cloudflaredSpec`（metrics 端口）、`workerServeSpec`（push 端口）；
**监督器**（`server.ts::restart`，变更检测重启）与**启动步骤**的就绪判定都变成
「`ownsResource`（未声明 = 不阻塞）∧ `healthy()`」，未就绪时日志点出归因。

为什么监督器同样需要：它杀旧 pid 后紧接着拉起同一条 spec，若新进程 bind 失败
（EADDRINUSE / python 侧双监听守卫拒绝）**早已退出**，而端口上的旧实例照样答 `/ping` / `/ready`
⇒ 监督器把**僵尸的 200 记成「重启成功」**：账本写新 pid、实际服务的是旧进程 —— 这正是
hub-server 重启事故的相位（账本 pid ≠ 真在服务的那一个）。

**实现时发现并修掉的语义漏洞**（记一笔，避免以后重蹈）：`portOwnedBy` 最初把「占用者清单为空」
一律当成「探测不可用 ⇒ 不判死」——但**没人监听**时 lsof/netstat 也返回空，于是「新进程已死」
会被判成「归属 OK」。现用 TCP 探测二次区分：没人监听 ⇒ **false**（要抓的就是这个）；
有人在监听但列不出归属 ⇒ **true**（工具缺失不该让所有组件启动失败）。测试里就有一个真监听
用例把它担住了（hub 端口无人监听 ⇒ `hub.ownsResource!(child.pid) === false`）。

**保留的刻意选择**：监督器重启路径**不接** `reclaimPort`（它杀的是账本里确切 pid、紧接着拉起
同一 spec，无孤儿窗口；kill 失败时回收也一样杀不掉）——只核归属。

### 四、验收

- `nn-training/tests/test_hub_auth_d9_order.py`（11 例）、`nn-training/tests/test_instance_lock.py`（9 例，
  含真进程顺序双启被拒 / 同时三启恰好存活一个）、`dashboard/tests/trainer-lock-release.test.ts`（13 例）。
- A/B 取证：detached worktree 对 HEAD 跑新测试 → 红（`assert 403 == 200`；日志里 127.0.0.1 被 BLOCKED）。
- 第三/四批（存活探测/隧道闸/就绪归属）：`test_pid_probe_windows_safe.py` 6 → **8 例**、
  `dashboard/tests/training-port-reclaim.test.ts` 6 → **15 例**（含 `ownsResource` 声明门禁、
  监督器接线门禁、`portOwnedBy` 空清单两义、真监听下 `hub.ownsResource` 判 false）；A/B 红：worktree 对 HEAD 跑新测
  6 红（含行为级 `tmp-clean._pid_alive(0) = True`），cloudflared 侧 HEAD 上既无 `reclaimPort`
  也无 `tunnelOwnsMetrics`、就绪判定是裸 `tunnelEdgeReady`。门禁：nn python gate（205 源文件）✓、
  `cd dashboard && bun run typecheck && bun run test`（406 例）✓、`bun run check`（1849 例）✓。
  ⚠️ 本批 gate **首跑红过一次**：`tests/test_remote_ppo.py::test_hub_server_auth_and_job_lifecycle`
  报 `Con…`（连接错误）——单跑该文件绿、`--maxfail=99` 单跑绿、重跑全量 gate 也绿 ⇒ 满编 `-n 4`
  下的**负载型 flake**（同 §313 已归档的那一类），与本次改动无关（日志已删，仅存档此判定）。
- 门禁：`bash tools/githook/nn-python-gate.sh` ✓；`cd dashboard && bun run typecheck && bun run test` ✓；
  `bun run check` ✓。决策记录：DECISIONS §2026-09-17-hub-restart-deadlock-hardening。

---

## §5 BC 训练整合进 云-HUB-LAN：kind=bc 第二任务类型全链（2026-09-13，用户指令五步）

**目标**：BC 从本地手工流程（export-godai-labels + train/bc.py 手工串）升级为与 PPO 同构的
分布式管线：控制台启动 → LAN 集群生成语料 → 云机 poll/接收任务训练 → 权重回传归档。
设计：`plan/bc-cloud-integration.plan.md`；**全部复用既有管线，只加任务类型，不建第二套体系**。

- **协议（remote/protocol.py）**：manifest 可选 `kind`（缺省 "ppo" wire 兼容）；bc 免必填
  reward/γ/λ、追加必填 `arch`、mode 红线 "bc"（串型互斥）；bc result 校验走 `metrics`
  （无 agg）。幂等键/job_id/data_fp 公式不变（bc `init_weights_fp` 恒 "bc"）。
- **云端（remote/worker.py::_run_bc_job）**：run_job 在 code 守卫后按 kind 分叉——D14 血缘
  校验共用 → import `train/bc.py::train`（code.zip 已含 nn-training 全部 .py）→ BC 权重
  （版本化归档字节）+ metrics 回传；cuda-dp→cuda、tpu 拒收；`--echo` 占位回传。
- **HUB 客户端**：publish_job 增 `kind/extra`，init_weights_path 可选（bc 不拷 init 文件）；
  `iter_bc_shard_dirs` + `verify_and_land_bc`（data_fp 重算/commit 对账，无 opt tar）。
  push 模式零改动（worker_server 同 normalize/run_job）。
- **LAN 语料（mode=bc）**：新导出器 `tools/sim/export-godai-bc.ts`（单局复用
  export-godai-labels.exportGame 纯函数 → npyBytes 内存序列化 → BCV2 容器；wins-only 败局
  = `kept:false` 空容器合法结果）；agent `/v1/task` 收 mode=bc（免权重桶）、ping 加
  `bcSupport` 能力位（fail-closed，旧 agent 不派）；入 codehash-files.txt（升级波）。
  `dist_common`：BC_SHARD_FILES/validate 分支/fetch_task wins+nearMissTimes 透传。
- **派发（rl/bc_dispatch.py）**：紧凑调度器——bcSupport 探活门 → (stage,seed) 队列 →
  节点槽位线程 → fetch_task(mode=bc) → 落盘 `<traj>/bc-data/it{r}/bc_s{stage}_seed{seed}/`
  （manifest 补 course_fp/corpus_fp 血缘）。无竞速（语料不需要最快者胜）；局失败重试一次。
- **BC 课程（rl/bc_config.py + curricula/*.bc.jsonc）**：独立文件种类（RL CourseConfig
  extra=forbid 不兼容、mode/gates/schedule 全不适用）；level 引用复用关卡抽离语义；
  `bc_corpus_identity_fp`（env+corpus 参数；train 超参刻意排除）；轮 r 种子
  `[1+(r-1)*seed_rotate, …]`（§15.1 轮转）。首课 **bc-c4.bc.jsonc**（level=arena4，
  student/60ep/wins-only/near-miss 3×，value_coef=0——`docs/nn/legacy.md` §20 M3 教训）。
- **编排器（run_bc.py，torch-free）**：per-course run_bc 锁；账本 `bc_round_completed`
  断点续跑；语料补采 → publish(kind=bc) → push/hub 等待 → verify_and_land_bc →
  backup_weights 归档（`nn-training/weights/<prefix>/<prefix>.it<N>.<ts>.json`）+
  WEIGHTS.md 行（torch-free 复刻）；bc-data 旧轮收敛。--smoke：1 局/max_ticks≤300/
  epochs=1 真一轮，落位即作废（`weights.smoke.json` scratch、不归档、账本零污染）。
- **控制台**：`.bc.jsonc` 全链认课（validateCourseArg/discoverCourses/setCourse/
  sanitizeViewCourse）；BC 课程复用 trainingLoop 组件键 → `bcLoopSpec`（run_bc.py，
  哨兵含 remote 四件套）——启停/监督/退出看门狗零改动；startBcLoop（无 BC 种子播种）；
  smokeTrainBc 三里程碑预演（published job → weights landed → BC SMOKE PASS，伪节点
  **真 BC 训练**非 echo）。
- **E2E 冒烟 PASS**（本机全链实测）：self 节点采语料（arena4 单局）→ push 发布 → 伪 GPU
  节点真 BC 训练（student 67.5K params，1 epoch，2.8s）→ 回传 → verify_and_land_bc 落位 →
  作废退出。回归：nn-python-gate 相关子集 162 绿 + `bun run check` 绿。
- **踩坑三则**：① 单 shard 语料 shard 级切分把全部样本划进 val（train=0 → DataLoader
  num_samples=0 崩）——`make_loaders` 回退样本级切分（test_shard_split 回归锁）；② smoke
  300 tick 内不可能 wins → smoke 关 wins_only；③ agent 结果缓存键不含 wins/nearMiss——
  smoke 轮用独立 iterId 命名空间（`-smoke` 后缀）防缓存回放假结果。
- **运维注意**：LAN 远端节点需 git pull 升级后才有 bcSupport（push 合并后人升）；升级前
  bc 语料只由 self/已升级节点承担（fail-closed 不污染）。

---

## §4 云端停机机制（2026-09-11，用户指令：停机 = 停云端省 GPU 配额，本地进程都不停）

承接 `docs/nn/training-stack.md` §1 的"未决"：§385 只修了 G13 口径，停车仍不省云配额。用户确认语义后落地（DECISIONS §2026-09-11-nntrain-cloud-halt）：

- **hub**：`/admin/workers/{halt,resume,status}`（Bearer 鉴权，volatile）→ 置位后 `/jobs/next` 下发 `{"halt":true}`。
- **worker**：收到达令即干净退出（keepalive 停、cell 走完）——省配额的有效动作止于"worker 停 + 不再轮询空转"；
  Kaggle/Colab 无 release API，session 释放需手工断连或到 9h 上限（tpu-probe 调研，横幅文案如实写明）。
- **console**：exit-watchdog 在 TrainingLoop 死亡（含设计内停车与崩溃）时自动发 halt（幂等），
  console-state `cloudHalt{at,reason}` 持久化 → 顶栏红色横幅 +「恢复云端」按钮（另提供手动 `cloud-halt`/`cloud-resume` 动作）。
- **验证**：test_hub_workers_halt_flow（hub 侧 401/halt/resume）/ worker_loop halts（worker 退出）/
  poll_job 上浮 halt / cloud-halt.test.ts（幂等跳过、失败不写标、恢复清标）。
- 恢复路径：删停机标记 + hub resume 后，**必须重启云端 worker 会话**才重新入队（横幅明示）。
- **修订（2026-09-11 用户确认"停机≠省配额"）**：worker 退出≠云机释放——Kaggle/Colab 宿主会话不因此停止计费
  （Kaggle 无释放 API、Colab 空闲约 90min 才回收）。真释放只发生在：① Colab 部署下 halt 达令触发
  `google.colab.runtime.unassign()`；② 人工断开。横幅/动作文案改为**诚实警告**（配额照烧→必须人工断开），
  不再声称"省 GPU 配额"。测试断言同步（非 Colab 路径必须出现"手工断开"提示）。
- **最终语义（2026-09-11 用户五条，DECISIONS §2026-09-11-nntrain-cloud-halt-final）**：停机=发布"停机命令"
  而非杀 worker——{halt} 随 /jobs/next 同发（有任务同批、空闲单独）；worker 停机过渡**尝试一次**真释放
  （Colab unassign / 其余提示人工断开），**不退出、照常执行任务**（云机不闲置）；halt 清除复位。
  console-state cloudHalt 双态 halted/recovered：红横幅=停机中（含"停不掉继续干活"状态）；灰横幅=曾停机已恢复
  （保留历史，TrainingLoop 重启或手动恢复触发）；本地组件全程不动。

---

---

## §3 四项监控修复落地 + PPO job 竞速模型（§343）+ it24 孤儿租约事故复盘（2026-09-06）

§2 的三处发现（+ backup_prefix 第④项）经用户拍板「修全部问题」后全部落地，
回归测试 nn-training/tests/test_rl_remote_fixes.py（6 项）+ 全量 pytest 312 绿：

### 3.1 修复清单（DECISIONS §342）
1. **lr 折算**：`rl/loop_steps.py _course_iter` 把 `sch['lr']` 同步折进 `args.lr`
   （remote 模式经 publish_job → manifest → worker Adam 生效）。实战影响：it24–35
   本就该 1.5e-4（绝对 iter 查表），修复的实战价值是 **it36+ 精调段正确降到
   5e-5**（消除 3 倍超速 + 无 KL 惩罚叠加风险）。
2. **dup shard 退场**：`rl/dispatch.py` dup-settle 分支退役输家副本目录
   （`_dir` 目录名归一化兼容 local wave/远程 shard 两形态）；`remote/hub_client
   .iter_shard_dirs` 同名去重兜底（manifest mtime 最早者胜）。it24 实测：发布
   retire 2 份残留，`shards=150` 与 expectedGames 平，UserWarning 消失。
3. **EVAL_SEEDS 扩 100**：`rl/eval_local.py`。it25 起评估 100 局（CI ±13pp →
   ±10pp 内），旧 20 seed 前缀不变保持可比。
4. **backup_prefix/backup_dir 按课程生效**：归档落 `nn-training/weights/p4-onset/`。

### 3.2 §343 竞速模型（用户指令，详细权衡见 DECISIONS §343）
PPO job 分发从「租约独占」改为「竞速广播」：`/jobs/next` 对所有轮询者广播同一
open job，先回传结果者胜（store_result 首写锁定，迟到 409 丢弃），结果落盘未
验收的 job 从池中剔除。租约/心跳降级为兼容路径不再参与调度。动机 = it24 事故：

### 3.3 it24 孤儿租约事故复盘（08:45–09:20）
- 时间线：08:45:40 发布 it24 → 3s 后旧 Kaggle 会话领取 → 用户家庭网络断 2–3min
  → Kaggle 页面停更，用户重载页面 → 旧 worker 进程死亡，**租约独占下 job 被锁
  满 30min**（孤儿租约）；trainer `wait_job` 同为 30min 超时 → 09:15:45 超时 →
  iter_error → 幂等重发同一 job → 继续等待。断网期间 trainer 侧 502/URLError
  均被 §1 加固的重试吞掉（设计生效），零数据损失。
- 应急处置（已做）：hub_server 仅重启（租约纯内存态即清零，隧道/TrainingLoop
  不动，Kaggle 的 HUB_URL 不变）→ 自愈从 30min 压到即时。
- 根治（§343 已落地）：竞速模型下「断连重连」零等待——重连的 worker 立即重领
  同一 job 重算，不存在孤儿租约概念。
- **Kaggle 侧操作指引**：页面停更 ≠ worker 死了，先看 cell 状态；重载/重跑
  worker cell 安全（幂等键 + 首写锁定兜底）；worker 代码经 code.zip 每任务下发
  （GET /jobs/{id}/code），notebook 只需保持轮询 cell 运行，无需改代码。
- 监控教训：hub-server.out 的请求时序（payload 下载 / heartbeat / result POST）
  是判断「worker 活没活」的最直接证据——heartbeat 绝迹 + result 恒 404 = 租约
  孤儿，而不是 PPO 慢。

---

## §2 p4-onset 首日监控：lr 调度在 remote 模式未生效 + 重复 shard + 评估截断（2026-09-06，监控发现，待处置）

监控 06:21–08:11 远程推架构首跑（it1–10 完成，it5/it10 两次评估）。训练本身健康
（KL 0.0905→0.0077 单调收敛、熵 0.36 带稳定、value 6.2→2.6），it8 误熔断已由
F4/DECISIONS §339 修复（ENT 改相对崩塌语义 + ent_peak 基线继承，热启动课程自动
豁免，不会复发）。以下三处为监控新发现：

### 2.1 ppo_schedule 的 lr 三段表在 remote 模式下全程未生效（重要，待用户拍板）
- 证据链：`rl/loop_steps.py _course_iter` 只把 `sch['lr']` 写进 `self._opt.param_groups`
  （remote 模式 hub 侧无 optimizer，静默跳过）；`publish_job(..., lr=float(args.lr), ...)`
  传**静态** `args.lr`，全库无 `args.lr =` 赋值点；worker 侧 `remote/worker.py:531/538`
  以 `Adam(lr=manifest["lr"])` 建 optimizer。日志双印证：每轮打
  `[course] ppo_schedule@itN: lr=0.0003`，而 iteration 事件 lr 恒 0.00015
  （`rl/events.py` 写 `args.lr`）。
- 影响：it1–35 实际恒定 lr=1.5e-4——warmup 段（≤15，设计 3e-4）只有一半学习率；
  **it36+ 精调段（设计 5e-5）将 3 倍超速**，phase3 又 kl_coef=0 无 KL 惩罚，风险最大。
  kl_coef 经 `args._kl_coef` + manifest **生效**；`kl_cap` 仅 stream 路径消费
  （remote 强制 stream=0 ⇒ **本模式不接线**，打包≠生效；见
  DECISIONS §2026-09-15-goalnn-kl-cap-unwired）。
- 按 ~2.2 min/iter，it36 约 1 小时内到达。选项：(a) `_course_iter` 把 `sch['lr']`
  同步折进 `args.lr`（一行改动）+ 重启 resume 续跑，it12 起与课程表对齐，
  it1–11 半速段记为既成事实；(b) 维持恒定 1.5e-4 跑完并改课程注释。属实验语义
  变更（AGENTS §15.5），由用户决定。

### 2.2 同 seed shard 双份落盘（dist/<node> 与 wNN 各一份，内容不同）
- 每轮 0–7 个 seed 在 `it{N}/dist/<node>/` 与 `it{N}/wNN/` 各有一份完整 shard
  （it3=7、it4=3、it5=4、it6=4、it7=3、it8=2、it10=1、it11=1；it9 本地-only=0），
  两份 obs sha **不同**（同 seed 同 wver 本应逐字节同——指向跨节点 agent 版本差，
  参数 AGENTS §16.6）。机制高度疑似 tail fan-out 竞速的迟到副本：败者已被拒绝
  结算却仍写盘共存（`rl/queue.py` `docs/nn/legacy.md` §21 修的是误回队，未管盘上残留）。
- 发布端 `remote/hub_client.pack_payload` 对重复 arcname 写 zip → zipfile
  UserWarning 刷屏；worker `unpack_payload` extractall 同名后者胜（排序保证 wNN
  在后）→ 实训只吃 150 个唯一 dir 的一份拷贝，**无双计**，但 payload 虚胖、
  账面 shards=151–157 与 expectedGames=150 不平。修复方向：结算后 retire 输家
  副本的盘上目录。

### 2.3 贪心评估被 EVAL_SEEDS 常量截断为 20 局（配置 100 局不生效）
- `rl/eval_local.py EVAL_SEEDS = (860001, 860002, *range(860003, 860021))` = 20 个
  种子；`rl/eval_dispatch.py:107` `EVAL_SEEDS[:n_seeds]` 把课程
  `eval_games_per_stage: 100` 静默截成 20。20 局胜率 95% CI ±13pp——it5/it10 的
  10%（2/20）与起点 p1-ep60 的 14%（100 局）统计上不可区分，阶段判断目前只能靠
  20 局粗评 + rollout 采样胜率双低通道。

### 2.4 其它观察（备忘）
- 备份正常但课程 `backup_prefix/backup_dir` 未被采用：`rl/loop_steps.py:458` 统一
  用模式前缀 `rl-weights` 落 `nn-training/weights/rl-weights.itN.*.json`
  （it1–11 全部已归档，权重安全）。
- it9 低谷（winRate 2.7%、128/150 超时、熵 0.246）归因：重启窗口内 mac 升级重启、
  a95/a97/a98 codeHash mismatch 被排除 → 150 局全本机采集（rollout 132s vs 常态
  40–66s）。it10 恢复 6.7%，it11 起全舰队回归。若 it11+ 超时占比仍 >50% 则需另行归因。
- it1 的 samples=68002 / mean_ret 8.0 为陈旧 shard 混入（resume=150，昨日多次失败
  尝试的同 init 残留）——趋势判断从 it2 起。
- 超时占比 ~45% 印证课程注释预判（max_ticks=2400 对 4 敌偏紧，后续重标定项）。
  走势数据：tmp/p4-onset/training_log.jsonl 与 eval_log.jsonl。

---

## §1 远程链路冒烟预演：worker --echo + TrainingLoop 作废轮（2026-09-05，DECISIONS §340）

用户拍板：不建虚拟课程——TrainingLoop 跑**真课程**，伪 Kaggle（echo worker）回传 init
权重并带 smoke 标记，TrainingLoop 走完正常落位后识别标记**作废本轮**（it 不前进）。
冒烟保真度 = 与真训练逐字节同路径；作废标记使任何消费方都不会把回显权重当真 PPO
吃进去（自保护）。

落地（`bun run check` / pytest test_remote_ppo 32 绿）：
- `remote/worker.py --echo`：下载/校验/commit/course_fp 全走，跳过 torch 与 PPO，
  result = init 权重回显 + manifest 指纹回显 + `smoke: true`（validate_result 不拒额外键）。
- `rl/loop_steps._remote_ppo`：`verify_and_land` + `mark_job_completed` 后查
  `result["smoke"]` → 抛 `SmokeVoidRoundError`；落位权重与发布时逐字节相同（init 回显），
  无需回滚。
- `rl/loop_core.run`：捕获 SmokeVoidRoundError——`--smoke` 干净退出，真训练 `it -= 1`
  原地重试（不计失败连击、不 sleep）。**作废轮不写 iteration 事件** →
  `last_completed_iter` 续跑锚点零污染（实测账本 0 iteration 事件）。
- `rl/cli.py --smoke`；`tools/hub-start.ts --smoke-only` 新增 Kaggle 交互预演阶段
  （真 job 发布 → echo worker 穿隧道 claim→payload→code→result → 落位 → 作废退出）。
- `remote/hub_client.wait_job` 加固：轮询容忍瞬时网络错误与 5xx（快速隧道抖动曾
  一击废掉整轮迭代 → 连击重试堆积陈旧 pending job）——24/7 隧道运营可靠性修复。
- 闪窗收尾：`archive.py`（git push）/`run_rl.py`（rev-parse）补接
  `platform_utils.POPEN_NO_WINDOW`（console-less 父进程拉 git 会新建可见控制台；
  其余文件的接线早已完成）。
- 闪窗第二三轮排查（看门狗实证法：EnumWindows 扫可见 ConsoleWindowClass 窗口记
  PID）：漏网点共两处——`remote/hub_client.git_head`（每次发布）与
  `dist_common.dirty_hash_files`（每轮派发的 dirty-tree 检查）——均已补接。dirty 检查
  本身保留：它只管**远端** pull 节点的 restart 循环护栏（self 节点经 is_self_node
  豁免、纯重启零 git 操作，2026-08-30 修订），与 code.zip 直接打包无关。
- `tools/hub-start.ts drainStaleJobs`：TrainingLoop 新启动前下架陈旧 pending job
  （删 payload.zip → hub claimable 跳过；账本保留 job_pending 真实历史），避免真
  worker 空烧 GPU 租约。

实测（20:56 冒烟）：发布 8s（断点续跑）→ echo worker 全趟 54s → 落位 → 作废退出；
账本 0 iteration 事件。教训：Bun Windows `detached` = DETACHED_PROCESS（子进程完全
无控制台，hwnd=0 实测），孙进程（git 等）会新建可见控制台闪窗——python 侧子进程
一律挂 POPEN_NO_WINDOW。

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §18→§1 · 旧 §19→§2 · 旧 §20→§3 · 旧 §24→§4 · 旧 §39→§5 · 旧 §56→§6 · 旧 §58→§7 · 旧 §59→§8 · 旧 §60→§9 · 旧 §61→§10 · 旧 §62→§11 · 旧 §63→§12 · 旧 §70→§13 · 旧 §69→§14 · 旧 §71→§15 · 旧 §72→§16 · 旧 §74→§17 · 旧 §75→§18 · 旧 §92→§19 · 旧 §94→§20 · 旧 §104→§21 · 旧 §105→§22 · 旧 §109→§23 · 旧 §119→§24 · 旧 §129→§25 · 旧 §130→§26 · 旧 §137→§27 · 旧 §127→§28 · 旧 §128→§29 · 旧 §129→§30 · 旧 §130→§31
