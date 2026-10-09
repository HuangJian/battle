# nn-training-test-debt-cleanup.review-bf.md — 第一轮评审

> 评审人：Buffy · 2026-10-09 · 基准 HEAD `e7cbc0a8`（goal-nn）· 方法：把 plan 的每条事实行 / `file:line` /
> 计数 / 判据拿到工作树上逐条对账（只读 grep + sed 取原行 + 文件计数；**未跑门禁、未改代码**）。
> **范围**：整份 plan（§0–§6），重点 §1 现状表、§2 六类判据与已锁定候选、§3 执行阶段、§4 DoD。
> **结论**：方向（**把「这测试还在守什么」变成可机械查询的事实**）成立，六类判据的分类学有价值，
> 且 `file:line` 层面的核实**通过率很高**（§3 已核对成立）。但 **T1 的删除判据与事实不符（F1，P0）**、
> **T2 会把「行为拒绝」降级成「源码文本扫描」（F2，P0）**——这两条按现稿执行会**删掉活的契约守卫**，
> 与本案自己的「不许删唯一守卫」纪律相抵。另有三处判据/数字需定死（F3–F5）。
> 建议 F1–F5 并入 §2/§3/§4 再开工；F6–F8 随实现就地改数字。

| # | 级别 | 一句话 |
|---|---|---|
| F1 | **P0** | T1 论证「恒 skip 空壳」**与事实不符**：三个文件共 **47** 个用例，只有**各 2 个**是账本 skip 门——其余是**分析工具的纯逻辑用例**（现网在跑、今天全绿），「直接删三个文件」= 顺手删掉 ~40 条活守卫 |
| F2 | **P0** | T2 的 10 个候选里**有 6 个是行为测试**（worker 拒收 `kind=run` / mode POST 回 400 / resolve 忽略旧键 / 单课程 legacy 语义 / merge 宽容旧键 / arena 文件不存在），把它们并进一个**源码文本扫描器** = 用文本覆盖替换行为覆盖，是覆盖率回归 |
| F3 | P1 | T5 的规模数字对不上代码，且**没给可执行定义**：点名的 `test_offline_task_queue.py`（声称 29 处）里 `read_text(` **= 0**（一次源码读都没有）、`test_boot_wire_guard.py`（声称 6）**= 0**、`test_batch_runner_phases.py`（声称 6）**= 1**；DoD 的「下降 ≥40%」在定义落地前不可证伪 |
| F4 | P1 | 「全仓只扫一次」没接上**仓库已有的**单点 `tests/helpers/source_scan.py`（73 个测试文件在用、带 2026-09-26/29 的墙钟与 GC 实测账）——T2 的扫描器与 S3-2 的计数护栏都该长在它上面，否则新写第三个扫描器 |
| F5 | P1 | 文档号撞号 + 悬空引用：`docs/nn/engineering.md` 现最大 **§70**（DoD 写的 §69 已被占）⇒ 新节应 **§71**；删 `tests/tools/test_lever_scan.py` 会让 `docs/nn/experiments.md:417/:470` 的指针悬空，须同 commit 修 |
| F6 | P2 | 规模数字对不上：`e2e/` 实为 **13** 个 `test_*.py`（plan 两处写 5）· `tests/` 下 `test_*.py` 实为 **302**（plan 写 ~250）· 子目录实为 **9**（plan 写「6 个 +helpers」，漏 `golden/` 与 12 个顶层用例文件）· 「retired/legacy/已退役 40+ 处跨 20 文件」三种读法都对不上（实测 `已退役` 单读 = 57 文件/68 处） |
| F7 | P2 | 小项：`test_rollout_scratch.py` 的 `_SKIP_BLOCKED` 实为 **7** 处（plan 写 6，含定义处）；T1 每文件的 skip 门是 **2** 个（不是「整批」） |
| F8 | P2 | T4 归错一档：`test_instance_lock.py:117-119` 是 **Windows-only 回落**（POSIX 走 `/proc`），本机**反而会跑**，归进「本机恒 skip」会污染 S1 的标注 |

---

## 1. 必修

### F1（P0）T1「恒 skip 空壳」不成立：三个文件里绝大多数用例是**活的**

**plan 说**（§2-T1）：判据 = 「依赖 `tmp/` 下未入库证据（CI 上整批 skip）」；处置 = **直接删三个文件**，
理由 =「守卫对象不在仓库里 ⇒ 门禁在没有输入的情况下跑**恒 skip 空壳**」。

**事实**（实测）：

| 文件 | `def test_` | `@pytest.mark.skipif` |
|---|---|---|
| `tests/tools/test_lever_scan.py` | **14** | **2**（`:167`、`:190`） |
| `tests/tools/test_wave2_judge.py` | **9** | **2** |
| `tests/tools/test_paired_power.py` | **24** | **2** |

`test_lever_scan.py` 的非门用例逐条读下来是**分析工具的纯逻辑守卫**，与账本无关、今天全绿：
`:51 test_shared_arithmetic_comes_from_wave2_judge_and_paired_power`（三文件**共享算术的单一来源**）、
`:58` 常量与引文数字、`:68` per-tick、`:72` mean/sum 缺列当 0、`:79` 税基份额池化、`:86` Cohen's d、
`:109` 分位边界、`:117` 动作份额五桶、`:127` group_by_outcome、`:134` leg_trend、**`:160 test_main_skips_when_ledgers_missing`**
（唯一钉「无账本时干净 skip」这条路径的用例）。⇒ 判据里的「**整批** skip」为假，「空壳」结论不成立。

**建议**（写进 §2-T1 与 S2）：

- 删除面收窄为**账本门用例本身**（三个文件各 2 条，共 6 条），或把「工具纯逻辑」用例**搬家**到一个瘦文件里；
  无论哪种，**在 plan 里写死「删掉的活用例数」**（现在是 0 说明、正文却给了「整批」的结论字样）。
- `test_main_skips_when_ledgers_missing` 这类**跳过路径**的用例属于「脚手架也有契约」，删除理由要单独写，
  不能挂靠在「守卫对象不在仓库」上。
- `tools/{lever_scan,wave2_judge,paired_power}.py` 本体在 `nn-training/` 内**零引用**（实测：全仓 `.py/.sh/.md/.ipynb`
  除自身与 `tests/tools/test_*` 外无命中）⇒ plan 里「若本体也无人调用，另开任务评估」这句现在就能**结案写死**
  （顺带说明：docs 里**有**指针，见 F5）。

### F2（P0）T2 把「行为拒绝」当成了「守灵」——合并成文本扫描 = 覆盖率回归

**plan 说**（§2-T2）：判据 =「断言目标是**不在场**」；处置 = **合并为单点，不删**：抽 `RETIRED_CONTRACTS`
清单 + **全仓只留一个扫描器**，8 处散落断言删掉。

**事实**：把 10 个候选逐个读了函数体，其中 **6 个断言的是「代码仍在、且拒绝旧输入」的行为**——不是源码文本：

| 候选 | 实际断言的东西 |
|---|---|
| `tests/remote/test_plan_run_split.py:228` | worker **拒收** `kind=run`（命中 handler 分支） |
| `tests/hub/test_auto_handoff.py:167` | `dispatch_record_merge` **读到旧 mode 键即忽略**（纯函数） |
| `tests/hub/test_multi_course_hub.py:556` | mode POST **只回 400 退役文案**、不再触发按需发现 |
| `tests/common/test_offline_task_queue.py:1087` | `resolve` **忽略** `authority/seize/auto_handoff` 旧键，`claimable=true` 照取 |
| `tests/remote/test_job_identity_collision.py:293` | 单课程（空串）**旧语义逐字不变** |
| `tests/biz/test_ladder_factory.py:201` | `levels/arena2.jsonc` **文件不存在**（文件系统事实 + 单源锁） |

旁证：`test_offline_task_queue.py` 的 `read_text(` **= 0**（该文件根本没有源码扫描），
`test_ladder_factory.py` 的是 `assert not (repo_levels / "arena2.jsonc").exists()`——都是行为/盘面断言。
真正「扫源文本的墓碑」只有 `tests/trainer/test_offline_leg_retired.py:111`
（`test_production_code_has_no_trace_of_the_retired_leg`）；`test_bulk_sched.py:555` / `test_soft_hold_prefetch.py:447`
是注释块（plan 已注明需核对，实测确为注释）。

⇒ 按现稿执行，`test_worker_refuses_the_retired_kind_run_leg` 这类**唯一行为守卫**会被一条文本扫描取代，
而它守的正是「退役后的兼容窗/拒收语义」——这与 T5 自己的第 3 条判据（「唯一守卫不许删」）
以及本案的非目标（不为清理砍覆盖率）直接冲突。

**建议**：T2 拆两档，判据不同、处置不同：

- **T2a 墓碑（源码文本扫描）**：`test_offline_leg_retired.py` 那种「全仓不得出现某符号」⇒ 合并成
  `RETIRED_CONTRACTS` + 唯一扫描器（**复用** `tests/helpers/source_scan`，见 F4）。
- **T2b 退役输入的行为拒绝**（上表 6 个）⇒ **保留在各自关注点**（它们测的是**现行**代码的**现行**分支：
  兼容窗关掉之前，拒收语义就是契约），只在归属表登记「守的契约 = 退役输入拒收（兼容窗关闭后失效）」。

### F3（P1）T5 的规模数字对不上代码，且判据没有可执行定义

**plan 说**（§2-T5）：「命中 20 个文件、约 100 处」，并给出逐文件表（29 / 13 / 8 / 6×5）；
DoD = 「处数下降 **≥40%**（基线由 S1 脚本数出）」。

**事实**（两种口径都测了）：

| plan 声称 | 实测 |
|---|---|
| `tests/common/test_offline_task_queue.py` **29** | `read_text(` **0**；`assert … in <文本>` 型 **0**（该文件 70 条 `assert` 全是行为断言，如 `dead not in rows[0]`） |
| `tests/remote/test_boot_wire_guard.py` **6** | `read_text(` **0** |
| `tests/trainer/test_batch_runner_phases.py` **6** | `read_text(` **1** |
| `tests/test_githook_scripts.py` **13** | ✓ 13（启发式计数一致） |
| `tests/hub/test_auto_handoff.py` / `test_offline_task_pack.py` / `tests/tools/test_forkdist.py` 各 **6** | ✓ 各 6 |
| `tests/common/test_dist_common_poll.py` **8** | 6（**启发式 6**：口径差异，见下） |
| 全仓 **20 文件 / ~100 处** | 朴素启发式：**64 文件 / 186 处**；`read_text(` 出现于 **180** 个测试文件；`tests/helpers/source_scan` 被 **73** 个文件引用 |

⇒ 三处是**明确的错**（`test_offline_task_queue.py`、`test_boot_wire_guard.py`、`test_batch_runner_phases.py`），
而「文件数/处数」随口径在 20–180 之间浮动 ⇒ **DoD 的 40% 在基线定义之前不可证伪**（这也是本案要治的病：
数字得能机械复现）。

**建议**：把 T5 的判据写成**一条可执行的定义**并写进 plan + S1 脚本注释，例如：

> T5 处数 = 「**经 `tests/helpers/source_scan`（或其 `read_text` 缓存）取到生产源码字符串后**，
> 对该字符串做 `in` / `startswith` / `re` 匹配的 `assert` 处数」（纯字符串常量比较不算；
> 数据文件读取不算；`assert x not in y` 计一半还是不计，写死一种）。

并把上表按新定义重数一遍（我给的三个错行请以新定义复核后替换）。

### F4（P1）「全仓只扫一次」没接上已有的单点扫描器

`tests/helpers/source_scan.py` 已经存在且是**这个仓库的既有单点**：文件头写明「把『读盘 + 解析』收成一个
lru_cache」，并带 2026-09-26 墙钟账与 **2026-09-29 改判**（AST 不再常驻：常驻 2.55s vs 不常驻 0.82s；GC 随常驻量累积）。
**73** 个测试文件在用它。

⇒ T2 的 `RETIRED_CONTRACTS` 扫描器、S3-2 的「处数只许降」护栏**都应该长在它上面**（读文本走它的缓存、
判据用它的 AST 即用即弃），而不是新写一套 `rglob + read_text`。plan 的措辞（「全仓只扫一次 O(仓库)」）
目标正确，但没点名这个既有机制，实施时极易长出**第三个**扫描器——正好撞上本案自己的「不造第二个真相」。
另外 S3-2 提到「基线由 S1 脚本给出」：该脚本本身就应**调用这个 helper**，否则基线口径与未来计数口径会漂。

### F5（P1）文档号与引用面：§69 撞号 + 删文件后 docs 指针悬空

- `docs/nn/engineering.md` 现最大节号实测 **§70**（`grep -o '^## §[0-9]\+' | sort -n | tail` = 68/69/70）
  ⇒ §4 DoD 写的「`docs/nn/engineering.md` §69」是**已占号**，新节应为 **§71**（同 `plan/self-node-disk-alert`
  那条 §35→§36 的教训）。
- 「删之前 grep 一遍，别留悬空引用」的范围只写了 tests。实测 `nn-training/` 内确实零引用，
  但 **`docs/nn/experiments.md` 有两处指向将被删的测试文件**：`:417`（`测试 nn-training/tests/test_lever_scan.py`）、
  `:470`（`由 tests/test_lever_scan.py 钉住`），`:218/:532` 指向工具本体（保留，不用改）。
  ⇒ S2 的 T1 commit 必须**同批**修这两处指针（指向 `docs/nn/experiments.md` 的归档节或新归属表），
  否则「删测试」会留下指向不存在文件的文档。

---

## 2. 需定死（数字/口径）

### F6（P2）§1 现状表的规模数字与树对不上

| plan 说 | 实测 |
|---|---|
| 单测层文件「约 **250** 个 `tests/**/*.py`」 | `find tests -name 'test_*.py'` = **302** |
| 「**6** 个子目录：biz/common/hub/remote/tools/trainer/worker + helpers」 | **9** 个：biz(15) · common(25) · **golden**(0) · helpers(0) · hub(24) · remote(65) · tools(10) · trainer(52) · worker(99) ⇒ 290 + **12 个顶层用例文件**（`tests/test_layering.py`、`test_githook_scripts.py`、`test_entry_scripts_in_place.py` …）被漏掉 |
| 「集成层 `e2e/` **5** 个用例文件 + conftest」（§1 与 §6 两处） | **13** 个 `test_*.py`（auto_handoff / bc_epoch / cloud_iter / hold / loop_supervisor_integration / multi_course_single_hub / offline_training / push_mode / run_rl / run_rl_m1 / volume / worker_name_ledger / worker_queue） |
| 「全仓 `retired / legacy / 已退役` 命中 **40+ 处跨 20 个测试文件**」 | 三种读法：`已退役` 单读 = **57 文件 / 68 处**；`retired\|legacy` = **211 文件 / 299 处**；`test_*retired*.py` = 2 个文件 ⇒ 没有一个等于 40+/20 |

`e2e/` 的 5 vs 13 尤其要改：§6「不动 `e2e/`（5 个文件，成本已知且小）」——数量错了，
「成本已知」这句就没有依据（13 个文件里含真训练/真 sim 的长 e2e）。**把 grep 命令逐字写进 plan**，
否则 T2/T5 的清理面无法核对（这正是本案要治的「数字不可机械复现」）。

### F7（P2）两处小数字

- `tests/remote/test_rollout_scratch.py`：`_SKIP_BLOCKED` 实测 **7** 处（plan 写 6；多的是定义/引用那一处）。
- T1 三个文件的 skip 门各 **2** 个（plan 未给数，但「恒 skip 空壳」的论证依赖它，见 F1）。

### F8（P2）T4 把「Windows-only 回落」归成了「本机恒 skip」

`tests/common/test_instance_lock.py:117-119` 的注释是「Windows 专用回落（POSIX 走 `/proc`）」——
即**本机（Windows）反而会跑这条**。归进「本机恒 skip」档会让 S1 的标注反着写。
建议该档拆两列：`本机恒 skip`（POSIX-only / 非 Linux skip）与 `本机才跑`（Windows-only 回落）。

### 附：S2 的复跑命令建议（口径，非缺陷）

S0 用 `nn-py-safe.sh` 是对的；S2 每个 commit 跑 `nn-python-gate.sh` 也是对的（门禁本身）。
但 T5 逐条删时**单文件复跑**请走 `bash tools/githook/nn-py-safe.sh -m pytest tests/<file> -q`
（AGENTS §5：nn python/pytest 一律经它；60s/用例 + 480s 外墙钟），别裸 `python -m pytest`。

---

## 3. 已核对**成立**的部分（不用改，逐条实测）

- **门禁范围** ✓ `nn-python-gate.sh:303 PYTEST_TARGETS="tests/ e2e/"`（一次调用）；用例规模 2980 出现在 `:60` 的注释账里 ✓。
- **墙钟口径** ✓ 同文件的 2026-09-17 交错 A/B 注释：`-n12 线程=1 → 21/25/28s`（≈23s）、旧默认 36s；默认 worker = min(物理核,32) ✓。
- **两条护栏的分工** ✓ `pyproject.toml:278-291`（`addopts … --timeout=60` 是**挂起**护栏）
  ↔ `nn-training/conftest.py:1-27` docstring（>5s 警告 / >30s **报错** 的**耗时预算**，根 conftest 同时覆盖 `tests/` 与 `e2e/`；
  实现是 `pytest_runtest_makereport`，实测在 `conftest.py:98`）——plan 的引用逐条正确。
- **LOC 预算** ✓ `tests/test_python_loc_budget.py:16-23`：`tests/` 与 `e2e/` 两层整体豁免（用户 2026-09-28 裁定）。
- **T1 的 `file:line` 与引文** ✓ 三个文件都在（各 ~10–20KB），三个工具本体都在 `nn-training/tools/`，
  `test_lever_scan.py:11-12` 的「故意钉死」引文、`:44-45` 的 `LEDGERS_OK`/`C05_OK`、
  `test_wave2_judge.py:196`、`test_paired_power.py:283/:364` 全部逐字命中。
- **T2 的 10 个 `file:line`** ✓ 全部命中同名用例（含两个注释块 —— plan 已正确标注「需核对是否为整段死代码」，实测确为注释）。
- **T3 的 5 个重复点** ✓ 逐条命中：`test_payload_archive_detect.py:66` / `test_payload_split.py:151` /
  `test_remote_ppo.py:427`（`payload.zip` 三处）、`test_protocol_split.py:166` / `test_remote_ppo.py:176`（wire v1 两处）。
- **T4 的 `file:line`** ✓ `test_forkdist.py:65/:68`（intentional skip/xfail）、`:78`（`_no_fork = skipif`）、
  `:126/:149/:162`（`pytest.skip("Windows")`）；`test_platform_utils_proc.py:75`（POSIX-only）；
  `test_githook_scripts.py:452/:525`（非 Linux）。只有 `test_instance_lock.py` 归档错（F8）。
- **T6 的两条历史教训** ✓ `tests/worker/test_rollout_volume.py`、`tests/worker/test_workdir_sweep.py` 都在；
  `nn-py-safe.sh:37-38` 逐字写着「沙箱下 test_rollout_volume 卡住不触发」；
  `nn-python-gate.sh:103-119` 的 safe-delete 块逐字写着「单跑绿、全量红 = 环境」+ `CODEBUDDY_SAFE_DELETE_ENABLED=0` 复跑法
  —— plan 把这类判为「环境，不是债」**与仓库既有裁决一致**。
- **S1 的落点可行性** ✓ `tests/test_layering.py` 只管 `common/biz/worker/remote/trainer/hub` 五个**生产包**的依赖方向，
  **不枚举 `tests/` 文件**（实测无闭集/快照守卫拦新文件）；而且 `tests/conftest.py:137`、`tests/test_layering.py:77`
  已经在用 `from tests.helpers import source_scan` ⇒ 新增 `tests/retired_contracts.py` 以 `tests.retired_contracts`
  导入即可，**无需登记**（plan 里那段「若判为更像生产契约则放 tests/…」的顾虑可以删掉，没有这个约束）。
- **S0 的沙箱纪律** ✓ 「交用户真终端 / agent 不跑长任务」「脚本被拦则改 `%USERPROFILE%` 下跑」与 AGENTS §16/§17.3 一致。
- **非目标里「不做按覆盖率删测试」「不引入 pytest-cov/mutation」** ✓ 与既有三组护栏（LOC/耗时/层）不重复投资。

---

## 4. 建议（可选增强，不进必修）

1. **T5 的护栏（S3-2）不要用「处数只许降」这一条孤悬的数字**：它天然要一层定义（F3），且极易假绿
   （把 `assert X in text` 改写成 `assert text.count(X)` 就绕过了）。更稳的两条：
   ① 新写的源码扫描断言**必须**经 `source_scan`（可用 AST 判「谁 import 了它之外还 `read_text` 生产源码」）；
   ② 与 T2a 合并：源码扫描断言**必须**只出现在 `RETIRED_CONTRACTS` 的扫描器里（即「唯一扫描点」既是判据也是护栏）。
2. **归属表加一列「失效条件」**（例如「兼容窗关闭后失效」「POSIX 门禁下才跑」）：本案的价值是让下一次
   重构能机械回答「哪些测试该跟着改」，而**能被机械判定的失效条件**比自由文本的「守什么」更有用。
3. **S0 顺手多收一份数字**：`-rs` 已给 skip 原因分布，但 plan 的 T4 想要「本机门禁 20s 里有多少是收集成本」
   ⇒ 再加一条 `--collect-only -q` 的**每文件收集耗时**（或 `--durations=80` 里看 collection 段），
   否则「恒 skip 文件的收集成本」仍只是估值。
