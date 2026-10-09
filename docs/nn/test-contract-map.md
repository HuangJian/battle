# nn-training 测试 → 契约归属表（test-contract map）

> 建立：2026-10-09 · 来源 plan：`plan/nn-training-test-debt-cleanup.plan.md`（§3-S1 产物）
> 评审：`plan/nn-training-test-debt-cleanup.review-bf.md`（F1–F8，本表按它改判）
> 维护规则（只在两条）：**新删一条用例 ⇒ 在这里把它的契约改指向接手的那条**；**新增一条「只能看源码/文本」的用例 ⇒ 在这里登记理由**。
> 本表不是「哪些测试可以删」的清单——它是「删之前先看这里：还有谁守着它」。

## 1. 口径与基线（可执行，不是手感）

| 项 | 值 | 凭什么 |
|---|---|---|
| 计数口径（唯一） | `nn-training/tests/helpers/text_asserts.py` | 代码即定义（口径 A 形态 / 口径 B 生产源码面） |
| 口径 A（形态，含 capsys 子串断言） | **1435** 条 / 228 文件 | 2026-10-09 实测（清理前 1488） |
| 口径 B（生产源码面，T5 删除面） | **1082** 条 / 106 文件 | 同上（清理前 1084；**是下界**，见模块头口径边界） |
| 护栏 | `nn-training/tests/test_source_text_assert_budget.py` | 只许降不许升 + 计数下界自检 |
| 重跑 | `bash tools/githook/nn-py-safe.sh -m tests.helpers.text_asserts --scope source` | 逐文件行号清单 |
| 全量基线（`-n 6` 本机） | 3963 passed / 15 skipped / 76.3s | `tmp/pytest-tmp/step0-pytest.log` |

口径 B 的**已知假阳**（别按条数批量砍）：`s0/d0` 这类 shard 目录名、用例当场写进 `tmp_path` 的夹具名
（`_marker.py`/`stub_serve.py`）、以及仓库根（不在 `nn-training/` 面）的脚本名。批次 1 实测 23 条候选
**全部是这一族**。

## 2. 已填契约（本次动过的文件）

| 文件 | 口径 B | 守的契约 | 本次处置 |
|---|---|---|---|
| `tests/tools/test_lever_scan.py` | 17 | 分析工具的**池化口径**：税基 `Σnum/Σticks`、相位密度 pooled（短局不截尾）、共用算术只有一份（`LS.PP is LS.WJ.PP`）、常量 | 删 2 条账本门端到端（T1）；留 12 条逻辑守卫 |
| `tests/tools/test_wave2_judge.py` | 15 | 判语四方向（`rel_down/up`、`abs_down/up`）、配对键首元 = 判据点、Z 常量同源 | 删 2 条账本门（T1）；留 7 条 |
| `tests/tools/test_paired_power.py` | 26 | 功效算术独立可对（均值/sd/SE/corr/正态尾/MDE/N 反解/Cohen Q/dose 倍数）、行存储 vs rA2 视图 | 删 2 条账本门（T1）；留 22 条 |
| `tests/trainer/test_offline_leg_retired.py` | 17 | 退役腿的**行为面**：发布点唯一且带 `export_path`、咽喉点当场拒、worker 拒收 `kind=run`、被接管课干净收官 | 扫描用例迁入单点（T2a）；行为面全留 |
| `tests/retired_contracts.py` + `tests/test_retired_contracts.py` | — (新) | **退役契约单点清单**（`_remote_run_segment` / `run_wait_sec` / `RUN_WAIT_DEFAULT_SEC` / `_run_wait_sec` 零回流）+ 扫描面基线 90 文件 | 新增（T2a/S3-1） |
| `tests/common/test_payload_archive_detect.py` | 6 | tar.xz vs zip 的**判别顺序**（先 tar 后 zip，2026-09-21 EOCD 误判事故）+ 失败语义 | 删 legacy-zip 重复点（T3） |
| `tests/common/test_payload_split.py` | 8 | tar.xz 往返 + 判别顺序 + 失败语义 | 同上 |
| `tests/remote/test_remote_ppo.py` | 12 | hub↔worker 双向互通；**legacy `payload.zip` 双读的唯一守卫**（含 zip 里根级 manifest 读取） | 收下 T3 的两个重复点；补注释 |
| `tests/common/test_protocol_split.py` | 5 | v1 往返 + 压缩是真的 + 常量一致（`decode_weights_json` legacy） | **不删**（与 `remote_ppo` 的 legacy 断言不同面） |
| `nn-training/conftest.py` | 0 | 耗时预算（>5s 警告 / >30s 报错）、`check()` 静默失败变红、BLAS 线程封顶、scratch 回退档 | 新增 skip 率上报表（S3-3） |

## 3. T2b 登记：**行为拒绝**（原稿想并进源码扫描器的六条，F2 复核后原地保留）

| 文件:行 | 守的行为 | 为什么不进 `RETIRED_CONTRACTS` |
|---|---|---|
| `tests/remote/test_plan_run_split.py:228` | worker 在零指令零下载之前拒收 `kind=run` | 断言的是**现行代码的拒绝路径**（源码里没这个字 ≠ 这条腿接得回来） |
| `tests/hub/test_auto_handoff.py:167` | retired mode keys 被忽略 | 行为（返回值/状态），非源码文本 |
| `tests/hub/test_auto_handoff.py:815` | mode actions 回 400 | HTTP 行为 |
| `tests/hub/test_multi_course_hub.py:556` | mode POST 已退役（400） | HTTP 行为 |
| `tests/common/test_offline_task_queue.py:1087` | `resolve` 忽略 retired mode keys（该文件 `read_text(` = 0） | 纯行为 |
| `tests/remote/test_job_identity_collision.py:293` / `tests/hub/test_jobs_next_retired.py:87` / `tests/biz/test_ladder_factory.py:201` | single-course legacy 语义 / `legacy_shape` 不再产出 / arena2-3 已删的单源锁 | 行为/结构断言 |

**区域性墓碑**（保留在原地的第三类，进不了清单）：`tests/hub/test_role_routing.py:243`
`assert "self.parked" not in gate` —— `parked` 在别处仍是好名字，它只能在 `role_blocked` 那段源码里消失；
清单只收 **repo-wide 标识级**扫描。
`tests/remote/test_bulk_sched.py:555` / `test_soft_hold_prefetch.py:447` 两处「已退役（2026-10-06）」是
**注释块**（不是代码）：记录删了哪条用例、判据没丢在哪里 ⇒ 保留（已核对）。

## 4. T4 登记：本机（Windows）恒 skip —— 保留，但要有数

守的是 POSIX/CI 侧契约，本机 skip 是设计。**从 2026-10-09 起不必手工数**：
每次跑的末尾有 `[conftest] skip 率：N skipped / M 收下` + 按文件分组（`conftest.pytest_terminal_summary`）。

| 文件 | 跳过条件 |
|---|---|
| `tests/tools/test_forkdist.py` | `:126/:149/:162` Windows · `:78` 无 `fork` · `:65/:68` intentional skip/xfail |
| `tests/common/test_platform_utils_proc.py:75` | POSIX 进程组语义 |
| `tests/test_githook_scripts.py:452` / `:524` | 非 Linux（POSIX python 走 xdist / 假 python 是 sh 脚本） |
| `tests/remote/test_rollout_scratch.py` | **7** 处 `_SKIP_BLOCKED`（沙箱删除配额）：环境敏感而非过时 |

⚠ **移出本表**：`tests/common/test_instance_lock.py:117-119`（F8）——那是 Windows **回落分支**，
本机反而会跑，属正常覆盖。

## 5. 未填（批次 2，已预注册）

按口径 B 从大到小：`tests/remote/test_remote_iter.py`(118) · `tests/worker/test_remote_serve_pool.py`(99) ·
`tests/hub/test_hub_queue_split.py`(49) · `tests/hub/test_hub_job_store_split.py`(30) ·
`tests/remote/test_offline_local_first.py`(27) · `tests/trainer/test_loop_guards_split.py`(27) ·
`tests/remote/test_plan_run_split.py`(23) · `tests/trainer/test_loop_lifecycle_split.py`(23) ·
`tests/tools/test_forkdist.py`(21) · `tests/trainer/test_batch_runner_phases.py`(16) ·
`tests/common/test_dist_common_poll.py`(15) · `tests/test_githook_scripts.py`(16) · `tests/test_layering.py`(13)。

对这 13 个文件按 plan §2-T5 的三条判据逐条判读（判据 3 = 保留 + 在此登记理由）。**已知形状**（抽样读过的）：

* `test_*_split.py` 族（≈150 条）：守**分层/搬家后的单一来源**——「这个名字只能在一处定义/一处使用」
  「import 面闭集」。行为测试无法表达「源码里没有第 2 个调用点」⇒ 归判据 3。
* `test_batch_runner_phases.py`(16)：AST 结构闭合（`_UnitPlan` 字段集、`self.X = plan.X` 恒等映射、
  三相位接线）。字段错位会**静默换值**，行为测试只在取值不同的时候才抓到 ⇒ 归判据 3。
* `test_remote_iter.py`(118)：多数命中是 `.py` 路径串（假阳），少数是「不许再引入第 2 个 spawn 点」
  这类闭集守卫 ⇒ 判读时先剔假阳。
