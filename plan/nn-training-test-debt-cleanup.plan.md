# nn-training 测试债清理（test-debt cleanup）

> 状态：**执行中**（2026-10-09；S0/S1 已落盘，T1/T2a/T3/S3 已实施，见 §7 实施记录）。
> 执行前先读 `docs/nn/engineering.md` §14 / §49 / §68 / §71。
> 拍板结果：**T1 直接删**（修订为「只删账本门用例」）· **T2 合并为单点**（拆 T2a 扫描型 / T2b 行为型）· **T5 只删不改写**（删除面收窄到有可执行口径的 `P_src`）。
> **评审**：`plan/nn-training-test-debt-cleanup.review-bf.md`（2026-10-09，F1–F8）——本文件已按 F1–F8 全部改判，逐条处置见 §2.0。
> 关联：`tools/githook/nn-python-gate.sh`（门禁）· `nn-training/conftest.py`（耗时预算）·
> `nn-training/pyproject.toml` `[tool.pytest.ini_options]`。

---

## 2.0 评审处置表（F1–F8，2026-10-09）

| # | 评审发现 | 本文件的改判 | 落地 |
|---|---|---|---|
| **F1** | T1「恒 skip 空壳」为假：三文件共 47 例、账本门只有各 2 条 ⇒ 「直接删三个文件」会删掉 ~40 条活守卫 | 删除面**收窄到 6 条账本门用例**；三个文件与工具本体都留 | §2-T1（改）、§7-①② |
| **F2** | T2 的 10 个候选里 6 条是**行为拒绝**（`test_offline_task_queue.py` 的 `read_text(` = 0）⇒ 并进源码扫描器 = 覆盖降级 | T2 拆 **T2a 扫描型（进清单）** / **T2b 行为型（原地保留 + 登记）** | §2-T2（改）、§7-④ |
| **F3** | T5 的「20 文件/100 处」三种读法都对不上，DoD「下降 ≥40%」不可证伪 | 先**定义口径**（代码化：`tests/helpers/text_asserts.py`）再报数；DoD 改成「护栏只许降 + 基线写死 + 工作清单落盘」，⚠ 撤掉 40% | §2-T5（重写）、§3-S1、§4-DoD |
| **F4** | 仓库**已有** `tests/helpers/source_scan.py`（73 文件在用）；别再长第三个扫描器 | 新口径模块**建在它旁边**（`text_asserts.py`，同目录同约定）；T2 的单点扫描器也不另开一套读盘逻辑 | §3-S1、§7-③ |
| **F5** | `docs/nn/engineering.md` 现最大 **§70**（DoD 写的 §69 撞号）⇒ 应 **§71**；删 `test_lever_scan.py` 会让 `experiments.md:417/:470` 指针悬空 | 新节改 **§71**；指针随 T1 同 commit 修（并把错误的 `tests/` 路径一并订正为 `tests/tools/`） | §7-① |
| **F6** | 规模数字：e2e 实为 **13** 个用例文件、`tests/` 下 **302** 个、子目录 **9** 个（漏 `golden/`） | §1 表按实测重写；S0 用例数用本机实测 **3963 passed / 15 skipped** | §1、§3-S0、§7-⓪ |
| **F7** | `_SKIP_BLOCKED` 是 7 处（非 6） | §2-T4 数字改为 7 | §2-T4 |
| **F8** | `test_instance_lock.py:117-119` 是 **Windows-only 回落**（本机反而会跑）——T4 把它归反了档 | T4 名单改为「本机真恒 skip」那几条，`instance_lock` 移出 | §2-T4 |

被 F1–F2 撤掉的原始判据（留档，别再走回去）：
· 「T1 = 恒 skip 空壳，直接删三个文件」——**撤**：门只在 6 条用例上，其余 41 条是分析工具的纯逻辑守卫。
· 「T2 = 8 处散落断言合并成 1」——**撤**：其中 6 条是行为拒绝（不是「断言不在场」），并进文本扫描器会使判据**变弱**。

---

## 0. 一句话

**不是给门禁减 20 秒，是把「这个测试还在守卫什么」变成可机械查询的事实。**
清掉的是「不再守卫任何现行契约」的用例，留下的每一条都要能在归属表里指到一条现行契约。

### 目标

1. 建立 **测试 → 契约** 归属表（S1 产物），下一次重构能机械回答「哪些测试该跟着改」。
2. 按六类判据处置已定位的过时用例（S2），每类一个 commit。
3. 给三类最容易腐化的写法加防回流护栏（S3）。

### 非目标（明确不做）

- 不动生产代码。发现僵尸生产代码只登记，另开任务。
- 不为降墙钟砍覆盖率；门禁墙钟**不上升**是约束，不是目标。
- 不动门禁的并行/分发策略（xdist vs forkdist 已实测定案）。
- 不做「按覆盖率删测试」——覆盖率低 ≠ 没价值（边界/契约守卫常常覆盖很低）。

---

## 1. 现状（数字与出处）

| 项 | 值 | 出处 |
|---|---|---|
| 门禁范围 | `pytest tests/ e2e/`，一次 xdist/forkdist 调用 | `nn-python-gate.sh:299-332` |
| 用例规模 | **3963** passed / 15 skipped（2026-10-09 本机 `-n6` 实测，`tmp/pytest-tmp/step0-pytest.log`） | 本 plan §3-S0 |
| 墙钟 | 门禁 Windows `-n12` ≈ 23s · Linux forkdist ≈ 19.8s；本机 `-n6` 全量 **76.3s** | `nn-python-gate.sh:36-54`、§3-S0 |
| 单测层文件 | **302** 个 `tests/**/*.py`（9 个子目录：biz · common · golden · hub · remote · tools · trainer · worker · helpers + 顶层文件） | 实测 glob（F6 订正） |
| 集成层 | `e2e/` **13** 个用例文件 + conftest | 实测 glob（F6 订正） |
| **文本断言（口径 A）** | **1488** 条 / 228 文件（→ T1/T3 删除后 **1435**） | `tests/helpers/text_asserts.py`（可执行口径） |
| **生产源码文本断言（口径 B）** | **1084** 条 / 106 文件（→ 同上 **1082**） | 同上（T5 的删除面 = 口径 B） |
| 已有护栏 | per-test `--timeout=60`（挂起）· conftest >5s 警告 / >30s **报错**（耗时预算） | `pyproject.toml:278-291`、`nn-training/conftest.py:1-27` |
| LOC 预算 | 生产侧单文件 <1000 行；**测试层整体豁免** | `tests/test_python_loc_budget.py:16-23` |

**痛点定位**：墙钟 20s 不痛。痛的是维护税——`rl/`→`trainer/`+`biz/`（2026-09-30 七刀）、
`remote/hub`→`hub`、算法栈五包进 `worker/` 这类整族搬迁之后，**没有任何机制回答「哪些测试已经守了个空」**。
现存证据：全仓 `retired / legacy / 已退役` 命中 40+ 处跨 20 个测试文件（`tests/` 内 grep），
其中相当一部分是「断言某个东西不存在」——只要没人回流，它永远绿，纯成本。

---

## 2. 判据：六类 + 已锁定候选

每类给：机械检测 → 已锁定候选（`file:line`）→ 默认处置。

### T1 · 一次性分析脚手架（**最硬的一类**；F1 已把删除面收窄）

**判据**：测试钉死的是**某次实验的读数**，不是产品契约；且依赖 `tmp/` 下未入库证据（CI 上整批 skip）。

| 文件 | 证据（实测） | 账本门用例 | 文件内其余用例 |
|---|---|---|---|
| `tests/tools/test_lever_scan.py` | `LEDGERS_OK` / `C05_OK`（`tmp/h4-lane-judge/**`） | **2**（`:167` `:190`） | **12** 条纯逻辑守卫（池化口径 / 共用算术 / 常量 / skip 路径） |
| `tests/tools/test_wave2_judge.py` | `skipif(not LEDGERS_OK)` ×2 | **2**（`:196` `:213`） | **7** 条（判语方向、配对键、常量同源） |
| `tests/tools/test_paired_power.py` | `CORPUS_OK` / `EXT_OK` | **2**（`:283` `:364`） | **22** 条（均值/sd/SE/corr/MDE/cohen Q/dose） |

**处置（已拍板，F1 改判后）：只删这 6 条账本门用例。**

理由：门禁里它们的输入（`tmp/` 账本）不存在 ⇒ 只会 skip；结论已归档 `docs/nn/experiments.md` §70。
而**其余 41 条**守的是三个分析工具自己的算术与口径——与账本无关、今天全绿，删了就是纯损失（F1）。
保留 `test_main_skips_when_ledgers_missing`（钉「账本缺席 ⇒ 干净跳过」这条现行契约）。

连带核查（已做）：三个工具本体（`tools/lever_scan.py` / `wave2_judge.py` / `paired_power.py`）**保留**，
且仍被各自的逻辑守卫按路径加载；`docs/nn/experiments.md` 的三处指针已改（F5）。

### T2 · 退役守灵（断言「某物不存在/已退役」）

**判据**：断言目标是**不在场**。分两种——**(a) 扫生产源码文本、断言某标识不存在**（防回流契约）；**(b) 断言现行代码的行为拒绝**（拒收 / 回 400 / 忽略旧键）。

| 文件:行 | 守什么 | 类型（F2 实测归类） |
|---|---|---|
| `tests/trainer/test_offline_leg_retired.py:111` | 全仓扫源文本，断言 offline 腿痕迹不存在（`:51` 注明「否则判据会静默少扫 64 个模块」） | **T2a 扫描型** |
| `tests/hub/test_jobs_next_retired.py:87` | `legacy_shape` 旧形状不再回 | **T2b 行为型**（断言当前输出/拒绝） |
| `tests/remote/test_plan_run_split.py:228` | `kind=run` 腿已退役 ⇒ worker 拒收 | **T2b**（下载前拒收 = 行为） |
| `tests/hub/test_auto_handoff.py:167 / :815` | retired mode keys / mode actions 400 | **T2b**（HTTP 400） |
| `tests/hub/test_multi_course_hub.py:556` | mode POST 已退役 | **T2b**（HTTP 400） |
| `tests/common/test_offline_task_queue.py:1087` | resolve 忽略 retired mode keys | **T2b**（函数返回值） |
| `tests/remote/test_job_identity_collision.py:293` | single-course legacy 语义 | **T2b**（行为） |
| `tests/biz/test_ladder_factory.py:201` | arena2/arena3 已删，单源锁 | **T2b**（单源锁=行为/结构） |
| `tests/remote/test_bulk_sched.py:555`、`test_soft_hold_prefetch.py:447` | 注释块「已退役（2026-10-06）」 | **不是代码**（已核对：整段是注释，记录「删了哪条用例 / 判据没丢在哪」）⇒ **保留** |

**处置（已拍板，F2 改判后）：只把 T2a 那一条收成单点；T2b 六条原地保留并登记。**

理由（F2）：那六条是在断言**现行代码的行为**（worker 拒收、POST 回 400、resolve 忽略旧键）——旁证：
`tests/common/test_offline_task_queue.py` 里 `read_text(` **= 0**。把它们并进源码文本扫描器是用
「文本覆盖」换掉「行为覆盖」：源码里没这个字 ≠ 这条腿接得回来。

落点（已落地）：`tests/retired_contracts.py`（清单 `RETIRED_CONTRACTS` + 唯一扫描器 `scan()`），
驱动 `tests/test_retired_contracts.py`（2 条：零回流 + **扫描面基线 90 文件**不缩水）+ 口径自测。
新增退役 ⇒ 只改清单一行。边界写明在模块头：**只收 repo-wide 的标识级扫描**；区域性墓碑
（如 `tests/hub/test_role_routing.py:243` 的 `assert "self.parked" not in gate`——`parked` 在别处
仍是好名字，只允许在 `role_blocked` 那段里消失）**不进清单**，它本来就只能在原地守。

### T3 · legacy 兼容的重复覆盖

**判据**：同一条向后兼容契约在 2–3 个文件里各测一遍（历史搬迁留下的副本）。

| 契约 | 重复点 | 处置 |
|---|---|---|
| `payload.zip` 双读 | `tests/common/test_payload_archive_detect.py:66` · `tests/common/test_payload_split.py:151` · `tests/remote/test_remote_ppo.py:427` | **3→1**：留 `test_remote_ppo.py::test_payload_unpack_accepts_legacy_zip`（**超集**：多断 zip 里根级 `manifest.json` 的读取）；前两处删，两文件 + 保留处各留指向注释 |
| wire v1 未压缩解码 | `tests/common/test_protocol_split.py:166` · `tests/remote/test_remote_ppo.py:176` | **不删**（F2 复核）：两条守的不是同一个面——`protocol_split` 那条是 v1 往返 + 压缩级别常量（少它丢往返/常量），`remote_ppo` 那条额外钉 `decode_opt_tar` 的 legacy 兼容（全仓只此一处）。**不满足「同义」** ⇒ 保留，在归属表登记 |
| mode 段退役 | 见 T2 三处重叠（`auto_handoff` / `multi_course_hub` / `offline_task_queue`） | **不删**（T2b，见上） |

**默认处置**：每契约只留**最贴近生产调用点**的那一份，其余删除并在保留处加一行指向被删文件的注释
（避免下一个人以为漏测又补回来）。**硬门槛**：删除后该契约必须有另一条**行为**测试指到它，否则不许删。

### T4 · 恒 skip / 环境门禁

**判据**：在本机（Windows）恒 skip，只有收集成本没有守卫价值。

- `tests/tools/test_forkdist.py`：`:126/:149/:162` `pytest.skip("Windows")`、`:78` `skipif(not fork)`、`:65/:68` intentional skip/xfail
- `tests/common/test_platform_utils_proc.py:75`：POSIX-only
- `tests/test_githook_scripts.py:452` / `:525`：非 Linux skip
- `tests/remote/test_rollout_scratch.py`：**7** 处 `_SKIP_BLOCKED`（`:129` 沙箱删除配额；F7 订正：不是 6），环境敏感而非过时
- ~~`tests/common/test_instance_lock.py:117-119`~~：**F8 订正 — 移出本表**。那是「Windows-only 回落」的分支，本机（Windows）**反而会跑**，属正常覆盖而不是恒 skip。

**默认处置**：保留（它们守的是 POSIX/CI 侧契约，本机 skip 是设计），但**在归属表里标注「本机恒 skip」**，
让「本机门禁 20s 里有多少是被 skip 掉的收集」成为可查数字。若某文件在本机**零用例可跑**，才考虑整文件挪出。

### T5 · 源码文本扫描型断言（**改写类，最费人工但最值**）

**判据（F3 重写：先有可执行口径，再有数字）**：口径 = `tests/helpers/text_asserts.py`（**唯一**计数定义）。
两把尺：

* **口径 A（形态）**= `assert` 里出现 `"字面量" in <文本>` / `not in`（排除 `in ["a","b"]` 这类纯数据断言）。
  含对 `capsys` 输出的子串断言（同族：判据挂在措辞上）。**护栏用它**（§3-S3-2）。
* **口径 B（生产源码面）**= 口径 A + 被读的东西必须是 `.py` 源码（`*.py` / `rglob("*.py")` /
  `py_files` 系助手 / 由它们绑出来的名字）。**T5 的删除面用它**。

实测（`tmp/pytest-tmp/text-asserts-*.json`）：**A = 1488 条 / 228 文件 · B = 1084 条 / 106 文件**。
原稿的「20 文件 / 约 100 处」三种读法都对不上（F3）⇒ **作废**。B 的密度最高（整文件口径）：

| 文件 | B 处数 | 文件 | B 处数 |
|---|---|---|---|
| `tests/remote/test_remote_iter.py` | 118 | `tests/hub/test_hub_queue_split.py` | 49 |
| `tests/worker/test_remote_serve_pool.py` | 99 | `tests/hub/test_hub_job_store_split.py` | 30 |
| `tests/remote/test_offline_local_first.py` · `tests/trainer/test_loop_guards_split.py` | 27 · 27 | `tests/trainer/test_batch_runner_phases.py` | 16 |

**处置（已拍板）：只删不改写；逐条走，不许批量砍。**

删除判据：

1. 该断言守的东西**另有行为测试覆盖** ⇒ 删；
2. 该断言守的东西**已经不存在于生产代码语义中**（只是源码里还留着同形字符串）⇒ 删；
3. 该断言守的东西仍在、且是唯一守卫 ⇒ **保留**，在归属表登记理由。

### 批次 1 结果（2026-10-09，判据 2 的机械筛）：**0 条可删**

工具：`nn-training/tmp/triage_t5.py`（不入库，判据 2 的可执行化）——取每条断言的「路径/模块字面量」，
在生产树（`nn-training/**`，排除 `tests/` `e2e/` `tmp/`）里判它能不能解析。全量 1084 条跑下来，
候选 23 条，**逐条读完全部是假阳**：`s0/d0` 是 shard 目录名、`_marker.py`/`stub_serve.py` 是用例
当场写进 `tmp_path` 的夹具、`Scripts/python.exe`/`detach-run.py` 是仓库根（不在 nn-training 面）的脚本。
⇒ **口径 B 里没有「守死了的东西」那一类**；它们绝大多数是**活的分层/拆分守卫**（判据 3）。

回归护栏（已落地）：`tests/test_source_text_assert_budget.py` 把 A/B 两数写死（只许降不许升），
**口径 B 的降不下去恰好说明这次的发现**：这批不是「债」，是「脆」——唯一能压的是护栏，不是删除。
逐条判读的工作清单落盘在 `tmp/pytest-tmp/text-asserts-src-baseline.json`（运行时证据，不入库），
再跑：`bash tools/githook/nn-py-safe.sh -m tests.helpers.text_asserts --scope source`。

不改写 ⇒ 不做「扰动源码验证原断言能红」那一轮（那是改写才需要的保险），但要保证删除后
对应契约在归属表里**仍有一个行为测试指到它**，否则不许删。

### T6 · 慢而低价值（待 S0 数据）

`--durations=80` 出来后，按「耗时 × 是否命中 T1–T5」排序取 top 20 逐条判。
**状态（2026-10-09）：未做**——S0 的基线跑没带 `--durations`（见 §7-⓪）；命令已备好，属下一批次。
历史教训（不要靠猜）：`tests/worker/test_rollout_volume.py` 曾在沙箱下卡死不触发 per-test 超时
（`nn-py-safe.sh:37-38`）；`test_workdir_sweep.py` 会被 safe-delete 配额批量转红
（`nn-python-gate.sh:105-119`）——**单跑绿、全量红 = 环境，不是债**，这类不进清理名单。

---

## 3. 执行阶段

### S0 · 基线采集（**已做**，2026-10-09；agent 通道自己跑得了）

```bash
cd nn-training
bash tools/githook/nn-py-safe.sh -m pytest tests/ e2e/ -n 6 --maxfail=999 --tb=short \
  > ../tmp/pytest-tmp/step0-pytest.log 2>&1
```

**实测：3963 passed / 15 skipped / 7 warnings，墙钟 76.28s，EXIT=0**（日志：`tmp/pytest-tmp/step0-pytest.log`）。
原稿假定「沙箱会拦 spawn、agent 跑不了」——实测不成立：`nn-py-safe.sh` + `-n 6` 直接跑完。
後續新增：`conftest.py` 的 **skip 率上报表**（§3-S3-3）现在会在每次全量跑的末尾打印
「N skipped / M 收下」+ 按文件分组，S0 的「15 skipped 分布」从此不需 `-rs` 手工数。
**未带的项**：`--durations=80`（T6 需要）——命令备在 §2-T6。

### S1 · 归属表（**已做**，口径代码化）

* 计数口径：`nn-training/tests/helpers/text_asserts.py`（放在既有 `tests/helpers/source_scan.py`
  **旁边**，F4：不再长第三个扫描器）。两个口径、一次读盘、CLI 可重跑。
* 归属表：`docs/nn/test-contract-map.md`（覆盖 T1–T5 命中文件 + 本次动过的文件）。
* 逐文件明细：`tmp/pytest-tmp/text-asserts-baseline.json`（口径 A）与 `-src-baseline.json`（口径 B）。

### S2 · 逐类处置（每类一个 commit，顺序从硬到软）

`T1 → T3 → T2 → T5 → T6`（T4 只登记不处置）。
每个 commit 后 `bash tools/githook/nn-python-gate.sh` 必须绿，且 S0 的 `T0`（76.3s / `-n6`）不上升。

### S3 · 防回流（三条，写完就少一半下次的清理量）

1. **退役契约单点**：`RETIRED_CONTRACTS` 清单 + 唯一扫描器（T2 产物）。新增退役 ⇒ 只改清单。
2. **文本扫描断言计数护栏**：新测试 `test_source_text_assert_budget.py` —— 全仓两个口径的条数
   **只许降不许升**（基线写死在测试里：A=1435 · B=1082）。它把 T5 的战果（与「脆」的趋势）钉死，
   避免下次重构又撒一批。带「计数异常下界」断言（护栏最怕口径坏了 ⇒ 恒 0 ⇒ 恒绿）。
3. **skip 率上报表**：`conftest.py` 新增 `pytest_terminal_summary` —— 每次跑的末尾输出
   「N skipped / M 收下（%）」+ 按文件分组 top12 + 原因 top6，让恒 skip 文件自己现形
   （T4 的长期盯法；已实测在本机全量与 xdist 下都会打）。

### S4 · 已拍板决策（2026-10-09）

| # | 决策 | 选择的选项 | 影响 |
|---|---|---|---|
| 1 | T1 三个分析脚手架 | **直接删** | 门禁少 3 个文件；`tools/*.py` 本体保留，只删 pytest 包装 |
| 2 | T5 文本扫描断言 | **只删不改写** | 省掉改写风险与人工量；但必须逐条确认「另有行为测试守着」才许删 |
| 3 | T2 退役守灵 | **合并为单点** | 8 处散落断言 → 1 份 `RETIRED_CONTRACTS` 清单 + 1 个扫描器 |

被否决的备选留档：
· T1「移出到 `--selfcheck`」——用户选更彻底；代价是以后重跑分析要重建自检（接受）。
· T5「改写 top5」——用户选只删；代价是脆断言的维护税继续存在（靠 S3 第 2 条护栏只许降不许升兜住）。
· T2「维持分散」——否决理由：8 份措辞各异的清单在下次退役时必然漏同步。

---

## 4. DoD（F3 重写：每条都可证伪；完成状态见 §7）

- [x] S0 基线落盘：**3963 passed / 15 skipped / 76.28s**（日志 + `T0` 有记录）
- [x] `docs/nn/test-contract-map.md` 入库并进 `docs/nn.progress.md` 索引
- [x] T1（**改判后**）6 条账本门用例已删，三个文件的 41 条逻辑守卫全留；工具本体保留；
      `experiments.md` 三处指针已修（F5）；三个文件各自跑绿
- [x] T3 `payload.zip` 双读 3→1（保超集那一处，删的两处 + 保留处都有指向注释）；
      wire v1 复核后**不删**并已登记理由
- [x] T2a 全仓退役**扫描型**守卫 = 1（`tests/retired_contracts.py` 清单 + `scan()`），
      原 `test_offline_leg_retired.py` 的扫描用例已迁入；T2b 六条**行为型**原地保留并登记
- [x] T5 删除面 = **口径 B（1082 条 / 106 文件，已代码化）**；批次 1（机械筛判据 2）= 0 条可删
      （23 条候选逐条读完全为假阳）；⚠ **撤掉「下降 ≥40%」**（F3：原数不可证伪，且实测该族是
      「活而脆」而不是「死」）；替代 DoD = **护栏基线写死 + 工作清单落盘 + 逐条判据可重跑**
- [ ] T6 top20 逐条判定留痕 —— **未做**（S0 未带 `--durations`；命令已备，下一批次）
- [x] S3 三条护栏落地（单点清单 · 计数预算测试（A=1435/B=1082 写死）· skip 率上报表）
- [x] `nn-python-gate.sh` 全绿；`docs/nn/engineering.md` **§71** + `DECISIONS.md` 各一条
- [x] 全程未推 git（只 add/commit，报 hash 即停）

### 修正记录（2026-10-09，与本 plan 原文的差异）

| 原稿 | 实做 | 依据 |
|---|---|---|
| T1 删三个文件（47 例） | 删 6 条账本门用例 | F1 |
| T2 八处合为 1 | T2a 1 处迁移 + T2b 6 处保留 | F2 |
| T5 处数下降 ≥40% | 口径化 + 护栏 + 批次 1 = 0 删 | F3 |
| §69 | §71 | F5 |
| e2e 5 个 / tests ~250 个 | 13 / 302（9 子目录） | F6 |
| `_SKIP_BLOCKED` 6 处 | 7 处 | F7 |
| `instance_lock` 本机恒 skip | 本机**跑**（Windows 回落分支） | F8 |
| S0「交用户真终端」 | agent 通道直接跑（`nn-py-safe.sh -n 6`） | 实测 |

## 5. 风险与回滚

| 风险 | 判据 | 处置 |
|---|---|---|
| 误删仍在守现行契约的用例 | 归属表里该行「契约」列写不出 ⇒ 不许删 | 先补契约列再判；存疑一律先移出（挪到 `tests/quarantine/`）观察一轮，不直接删 |
| T5「只删」丢掉唯一守卫 | 删除后该契约在归属表里**没有行为测试指到它** | 不许删，转保留 + 写理由。T5 的三条删除判据是硬门槛，不是建议 |
| T1 删过头 | `tools/lever_scan.py` 等本体被误当死代码一起删 | 本 plan 只删 `tests/tools/` 下的三个包装文件，工具本体不动（§2-T1 已写死） |
| 沙箱删除配额导致全量假红 | 单跑绿 / 全量红 + 日志有 `[safe-delete]` | 环境噪声，不是回归；`CODEBUDDY_SAFE_DELETE_ENABLED=0` 复跑确认 |
| 门禁墙钟被新护栏拖长 | S2 每 commit 后对比 `T0` | 上升即回滚该 commit |

## 6. 明确不做

- 不引入 `pytest-cov` / mutation testing（成本 >> 收益，且已有 LOC/耗时/层三组护栏）。
- 不合并 `*_split.py` 文件族（拆分是为可读性，不是债；除非 S1 证明同一契约被两处守）。
- 不改 `pyproject.toml` 的 `--timeout=60` / 耗时预算阈值。
- 不动 `e2e/`（集成层 hermetic，**13** 个文件，成本已知且小）。
- **不把 T2b 的行为拒绝并进源码扫描器**（F2）：那会把行为覆盖换成文本覆盖。
- **不为凑数删除「活而脆」的源码形态守卫**（F3/批次 1）：它们守的分层/拆分/注入点契约
  没有别的表达方式（判据 3），只能靠 §3-S3-2 的护栏不让它再长。

---

## 7. 实施记录（2026-10-09，本 plan 的执行结果）

| # | 产物 | 文件 |
|---|---|---|
| ⓪ | S0 基线跑（3963/15/76.3s）+ 全量日志 | `tmp/pytest-tmp/step0-pytest.log` |
| ① | T1：删 6 条账本门端到端（3 文件）、修 `experiments.md` 三处指针 | `tests/tools/test_lever_scan.py` · `test_wave2_judge.py` · `test_paired_power.py` · `docs/nn/experiments.md` |
| ② | S1 口径：两个口径 + CLI + `totals()` | `tests/helpers/text_asserts.py`（新） |
| ③ | T2a：退役契约清单 + 唯一扫描器 + 驱动（含扫描面基线） | `tests/retired_contracts.py`（新）· `tests/test_retired_contracts.py`（新） · `tests/trainer/test_offline_leg_retired.py`（瘦身） |
| ④ | T3：`payload.zip` 双读 3→1 + 指向注释 | `tests/common/test_payload_archive_detect.py` · `tests/common/test_payload_split.py` · `tests/remote/test_remote_ppo.py` |
| ⑤ | S3-2 护栏：计数预算（基线写死 + 下界自检） | `tests/test_source_text_assert_budget.py`（新） |
| ⑥ | S3-3：skip 率上报表 | `nn-training/conftest.py` |
| ⑦ | T5 批次 1：判据 2 机械筛（0 可删，23 假阳逐条核） | `tmp/triage_t5.py`（不入库） |
| ⑧ | 文档：归属表 · 工程档 §71 · progress 索引 · DECISIONS | `docs/nn/test-contract-map.md`（新）· `docs/nn/engineering.md` · `docs/nn.progress.md` · `DECISIONS.md` |

**用例数变化**（`--collect-only` 实测）：**3978 → 3974** —— 删 9（T1 六 + T3 两 + T2a 迁移一）、
新增 5（`tests/test_retired_contracts.py` 4 + `tests/test_source_text_assert_budget.py` 1）。
全量跑：S0 = **3963 passed / 15 skipped**（3978 收下）。
**下一批次（已预注册，别当新任务）**：T6（`--durations=80` 拿 top20）+ T5 批次 2（对 `test_remote_iter.py`
与 `test_remote_serve_pool.py` 两个大文件按判据 1/3 逐条判读，约 217 条）。
