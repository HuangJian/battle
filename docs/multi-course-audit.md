# 多课程并行训练 — 现状审计与目标态

> 对应 plan：`plan/multi-course-parallel-training.md`（P0-W6）。
> 基线：2026-09-11 merge 后代码（分支 `nn2`）。本文只记「现状 → 目标」的差距与
> 证伪手段，不含决议（决议在 plan 与 `DECISIONS.md`）。

## 1. 现状：单例假设住在哪

多课并行的梗阻不是一处，而是一组**同构的单例假设**——每处都假定「一台机器只有一门
课程」。按 plan §2 的编号：

| 事实 | 现状落点（符号为准） | 后果 |
|---|---|---|
| 训练进程单实例 | `run_rl.py::_acquire_run_rl_lock` → `nn-training/.run_rl.lock`；`train_loop.py` → `.train_loop.lock` | 第二课直接拒启 |
| 账本条目 | `types.ts::Registry` 的 5 个扁平单键（`hubServer`/`trainingLoop`/…） | 第二课覆盖登记 → 停错/杀错 |
| 端口 | `rl.hub_port` 一处 base，`+1`（cloudflared metrics）/`+2`（本机伪 push）散在 `specs.ts`/`push.ts`/`proc.ts`/`hub.ts`/`console/actions.ts`/`console/api.ts`/`smoke.ts` 七处手写 | 第二个 hub 端口冲突 |
| hub 进程 | `hub.ts::stepHubServer` 单 jobRoot/jsonl（课程由 jobRoot 推导） | 两个 hub 抢占同一 `hub_port` |
| 隧道 | 账本单 `cloudflared` + `rl.remote_hub_url` 单键（`config.ts::writeRemoteHubUrl`） | 第二课覆盖隧道登记 → worker 连错 hub |
| 监督器重建 | `console/actions.ts::restartSpecFor(key)` 用 `entry.course \|\| console-state.course` 猜课程 | A 课进程被 B 课配置拉起 |
| halt | `console/actions.ts::hubAdminOk` 恒读 `rl.hub_port` + 单 `cloudHalt` | A 课死停错 hub |
| kill-previous | `train.ts::killPreviousTrainers(script)` 只按脚本名全杀 | 重启 A 课杀掉 B 课 |
| 本机并发 | `rl.workers: 8` / `rl.local_slots: 10` 单份配额，无总量校验 | 双课 = 20 对局进程抢核 |

已天然隔离、无需改动：`tmp/<course>/`、`course_fp`（D14 血缘）、`(rotateSeed, it)`
种子流、eval 日志与 evalboard 行的 course 键控。
明确**非**隔离、另有归宿：EvalBoard 批队列 `batches.jsonl`（跨进程无锁改写，P4 加锁）。

## 2. 目标态：课程 = 并行单元

- 一切单例按课程实例化：锁 `.run_rl.<course>.lock`、账本 `Record<course, Entry>`、
  端口 `hub_port(slot) = base + slot*10`、隧道 `rl.remote_hubs[course]`、halt
  `cloudHalts[course]`。槽位 0–3（常态 2 课，4 槽留余量）。
- 端口算术**只有一个归宿**：`tools/training/slots.ts`（`portForSlot` / `slotPort` /
  `checkCapacity` / `allocateSlot` / `allSlotPorts`）。调用方永远不给偏移量。
- 本机并发按加法校验：`eff(course) = max(workers_c, local_slots_c)`，
  `Σ eff ≤ max(rl.workers, rl.local_slots)`（本分支 8/10 → 容量 10），超量 fail-fast
  并点名超量课程；拆分配额只住 `rl-config.json` 的 `courses` 块，**永不写进
  `curricula/*.jsonc`**（一改课程文件 `course_fp` 就变，触发 D14 熔断误判）。
- 归属只做审计、不参与调度：manifest `course_name`（短名）+ 既有 `course`（全文快照）
  + `course_fp`（血缘），dispatch 报告带 course。

## 3. P0 证伪测试清单（红 → 打开的阶段）

| 断言 | 文件 | P0 状态 | 打开于 |
|---|---|---|---|
| 双课程 hub spec 端口/日志不同 | `tests/training-multi-course.test.ts`（W1） | 红（skip） | P1a |
| 双课程 worker_server 端口/work 目录不同 | 同上（W1） | 红（skip） | P1a |
| 双课程锁名不同 + 无课程沿用旧名 | 同上（W1）+ pytest（W2） | 红（skip） | P1a / P1c |
| `checkCapacity` 加法校验（值从 rl-config 读） | 同上（W3） | 红（skip） | P1a |
| spec 重建逐字段一致（Q4 快照） | 同上（W4） | 占位 | P1b |
| 门禁① `hub_port` 唯一归宿 | 同上（W5①） | 红（skip） | P1a |
| 门禁② `Object.keys(reg)` 唯一归宿 | 同上（W5②） | 红（skip） | P1b |
| 锁原语：双课双持 / 同课拒绝 / `--force` 不跨课 | `nn-training/tests/test_multi_course_locks.py` | **已绿** | — |

红态证据（P0 落盘时实测）：
- bun 侧 `7 fail / 2 pass`（去 skip 跑：端口与日志全同、slots.ts 未落盘、两门禁红）；
- pytest 侧首条即 `ImportError: cannot import name 'course_lock_path'`，
  锁原语三条用例绿（`acquire_lock` 是纯路径参数，per-course 实例化即可用）。

P0 未改动任何生产代码：全部红断言以 `skip` 暂存，`bun run check` 与
`make -C nn-training python-gate` 保持绿。P1 起逐条去掉 skip。
