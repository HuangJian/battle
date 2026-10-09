# 把 `workers` 从课程设置里去除

> plan：`plan/course-workers-removal.plan.md`
> 触发：用户 2026-10-09「课程里的 `workers: 8` 不合理——课程并不知道它将会在哪个环境做 rollout。
> 不管是 LAN 集群还是自主 worker（tpu 机有 96 cpu 核心可用），该设置项都未能有效校准 rollout 进程数。」
> 相关：`docs/nn/rl-config.md` §1（键分类学）· `docs/nn/remote-transport.md` §60（并发口径校准）·
> `docs/nn/runtime-opt.md` §30（`cpu_worker_slots`）· `plan/multi-course-parallel-training.md` §3.4
>
> **状态：已实施（2026-10-09，含一轮评审修订，见 §7）** —— 实施范围 = S1 + S2（`§3` 全表），
> **零课程文件写盘**；存量 167 个 `workers` 键永不剃（死键由启动告警点名），
> `CourseConfig.workers` 字段永久保留。S2 采用 **显式 0 = 节点自定（auto）** 的 wire 设计（§3 S2-2）。

---

## §0 结论（已证实，非推断）

**病灶不在「值没校准好」，在「这个键根本不该待在课程文件里」。**

1. **它是一个复制粘贴常量，不是实验设计**：`nn-training/curricula/*.jsonc` **167/167** 门课都写了
   `workers`（**164 个 `8`、3 个 `2`**；三个 `2` = `bc-e2e.bc.jsonc` / `tiny-a.jsonc` / `tiny-b.jsonc`）。
   没有任何一门课靠它做对照变量。

2. **它同时污染两个环境**，且两处的正确口径本来就不同：

   | 环境 | 课程值怎么到那儿 | 正确口径 | 现状 |
   |---|---|---|---|
   | 本机直跑 | `biz/course_spec.py:1040` → `args.workers` → `trainer/queue_local.py:114` | rl-config `rl.workers` / `courses.<课>.workers`（`worker/config.py:432 resolve_course_quota` 每轮热读） | 侥幸不痛（本机那台就是 8 核档） |
   | **远端/自主 worker** | `trainer/loop_remote_drive.py:115` / `trainer/loop_export.py:183`：`workers = remote_iter_workers or args.workers` → `build_iter_spec(workers=…)` → 节点 `worker/iter_rollout.py:796` | 节点自己按核数（`cpu_worker_slots()`） | **96 核 TPU 机被钉死在 8** |

3. **为什么「钉死」而不是「被夹」**：`worker/iter_rollout.py:798`
   `workers = max(1, min(requested_workers, len(argvs), MAX_WORKERS, cap))`，`cap = workers_cap()`
   （`:117-140`，缺省 = `cpu_worker_slots()`）。夹取**只降不升**（`min`）⇒ 课程给的 8 就是
   **硬天花板**，96 核机器上 `min(8, 94) = 8`。这就是用户说的「未能有效校准 rollout 进程数」的机械解释。

4. **wire 事实（评审补，2026-10-09）**：`manifest["rollout"].workers` 在发布侧被
   `common/manifest.py:437/564-577` 归一化为「缺席 = 1；**显式 0 拒收**」，节点读到的恒为 ≥1。
   ⇒ 「不下发 workers」在 kind=iter 上**不可表达**。本版改为**显式 0 = auto**（§3 S2-2）——
   这是本条线唯一能表达「节点自定」的 wire 形状。

---

## §1 判据：这个键属于哪一类

既有论据现成，**不必新造**：

- `worker/course_args.py:25`（COURSE_MACHINE_OVERRIDE_KEYS 的注释）：
  「为什么这些键住 rl-config 而**不能**住 `curricula/<课>.jsonc`：课程文件字节 = `course_fp`
  （语料血缘 / 熔断口径，D14）——往课程文件里加一个旋钮，熔断会把同一份语料读成新语料。
  **机器侧旋钮永不进 curricula**。」
- `worker/config.py:411`：「机器配额只住 rl-config 的 `courses.<课>` 块，**永不写进 `curricula/*.jsonc`**」。
- `worker/course_args.py:41`（2026-09-19 precedent）：`remote_transport` / `remote_hub_url` 已因
  「课程任务与 worker 节点**互相正交**」被摘除——**同一个论证，同一个动作**，本次只是换了个键。
- `docs/nn/rl-config.md §1.1` A 类 / §1.4b：`workers` / `local_slots` 是**机器级键**
  （那两条专指 rl-config 里的那一份，保留不动）。

⇒ 判据：值描述「**跑 rollout 的那台机器**」⇒ A 类 ⇒ 住 rl-config，**不住课程文件**。
课程定义**任务**，机器提供**算力**，并发是机器的属性。

## §2 非目标

- 不动 `rl-config` 的 `rl.workers` / `rl.local_slots` / `courses.<课>.workers`（本机配额，位置正确）。
- 不动 `effective_cores()` / `cpu_worker_slots()` / `cores_note()` 口径（`docs/nn/runtime-opt.md` §30）。
- 不动 `NN_ROLLOUT_WORKERS_MAX`（`worker/iter_rollout.py:104`，操作员退出阀）。
- 不动任何任务侧参数（命数 / 敌数 / 关卡形态 / max_ticks）。
- **不动 manifest 的缺席默认**：`ROLLOUT_SPEC_DEFAULTS` 里 `workers` 缺席仍 = `1`（历史字节语义；
  在树内的两个生产者——`build_iter_spec` / `plan.iter_spec`——都**总是显式写**这个键，缺席只对
  手写/老 spec 有意义）。新语义只加一档：**显式 `0` = 节点自定**。
- **不动 BC 课程**（`worker/bc_config.py:174` 自己也有一份 `workers: int = 8`，全仓无读者）：
  那是另一条 schema 与另一条开课链（`_open_bc_course`），本次不夹带；要动另立案。

## §3 分步（顺序锁死，见 §4 R1）

### S1 删读面 + 死键告警 —— 零血缘风险

| # | 位置 | 动作 |
|---|---|---|
| 1.1 | `biz/course_spec.py:1040` | `flat_overrides()` 的 mapping 删 `"workers": "workers"` ⇒ 课程值不再覆盖 `args.workers` |
| 1.2 | `biz/hot_reload.py:42` | `RESTART_ONLY_FIELDS` 删 `"workers"`（不再影响 args，无需 restart-only 记账） |
| 1.3 | `biz/ladder_factory.py:291` | 生成模板删 `"workers": 8` —— **防长草的关键**，否则新课程继续抄 8 |
| 1.4 | `biz/course_spec.py:873` | 字段 `workers: int = 8` **保留**，标注 deprecated（原因见 §4 R1） |
| 1.5 | `biz/course_spec.py` 新增 `DEAD_COURSE_KEYS` + `dead_key_warnings(course)`；在**开课侧**调用（`trainer/loop_serve.py::open_course` 与 `trainer/run_rl.py` 的课程段） | 课程文件含 `workers` ⇒ 只告警不拒一行（照 `worker/rl_config_schema.py` 口径）。**不放在 `apply_course` / `course_args`**：只读视图 `course_openable`（`worker/course_args.py:139-148` ← `trainer/loop_plan.py:355` / `run_rl_cluster.py:109`）走的是同一条链，放那里会在控制台每拍轮询时重复打（R6 破功），还会把 `run_rl_cluster --json` 的输出弄脏 |

**本机侧行为**：`--workers` 的 argparse 默认 = `_d("workers", min(effective_cores(), 12))`
（`worker/cli.py:263-268`），`_d` = rl-config 的 `rl` 块（`worker/cli.py:31`），当前 `rl.workers = 8`
⇒ **164 门写 8 的课逐字节同行为**。**三个例外**（写 `2` 的课）本机并发 2→8：
`bc-e2e.bc.jsonc` / `tiny-a.jsonc` / `tiny-b.jsonc`（`tiny-a` 是 `e2e/test_cloud_iter_e2e.py:329`
的真上云夹具；全仓无并发断言，已核）。要么接受这条口径变化，要么先给这 3 门补
`courses.<课>.workers: 2` —— 本版**接受**（课程值本就要退场，夹具口径随机器走是目标状态）。

### S2 修远端泄漏 —— 显式 0 = auto，六处改动

**S2-1 wire 契约**（`common/manifest.py`）

| # | 位置 | 动作 |
|---|---|---|
| 2.1a | `common/manifest.py:564-577` | `>= 1` 放宽为 `>= 0`：**显式 `0` = 节点自定（auto）**，`< 0` 仍拒收；缺席仍 = `1`（§2 非目标）。注释重写：旧的「显式 0 拒收——不静默改成 1，因为『配错』与『没配』长得一样」**换靶**：0 现在有**定义**（机器自定），与 1 不再可混；配错（比如 `-1`/非整数）仍响亮拒 |
| 2.1b | `worker/iter_job.py:71` | `"workers": int(workers) if int(workers) > 0 else 1` → `"workers": max(0, int(workers))`（0 = auto，透传；docstring 同步：「`workers<=0` ⇒ `0` = 节点自定」） |

**为什么 0=auto 不引入版本 skew**（评审问）：kind=iter 的节点跑的是**随 job 落地的 hub 侧代码**
——`remote/worker.py:527-536` 的 `_ACTIVE_CODE_SHA` 热替换护栏（版本不符 ⇒ `CodeChangedError`
自重启换代码）。两侧永远同一 commit 解释 `workers`，不存在「老节点把 0 读成 1」的静默降级。

**S2-2 节点侧**（`worker/iter_rollout.py:794-820`）

| # | 位置 | 动作 |
|---|---|---|
| 2.2 | `worker/iter_rollout.py:796` | `requested_workers`：`>0` = 显式（夹取照旧）；`0/缺席` = **本机自定** = `min(cpu_worker_slots(), cap, len(argvs), MAX_WORKERS)`（`cap` = `workers_cap() or MAX_WORKERS`；env=0「不夹」时 auto 仍取核数口径，不是 256——评审 M4） |
| 2.3 | `worker/iter_rollout.py:812-818` | 日志分档：显式=「并发夹取」（现状不变）；auto = 新行「**并发定档**」带 `cores_note()` + 并行槽上限 |

**S2-3 训练侧**（不再把训练机读数当节点并发）

| # | 位置 | 动作 |
|---|---|---|
| 2.1c | `trainer/loop_remote_drive.py:115`、`trainer/loop_export.py:183` | `workers = int(remote_iter_workers or 0)`——**不再回退 `args.workers`**。0（缺省）= 自定；正整数 = 操作员加压阀（节点仍按核数夹取） |
| 2.4 | `worker/cli.py:576-579` + `worker/config.py:411-419` + `worker/config.py` 课程段注释 | help / 注释同步：`--remote-iter-workers 0 = 节点按本机核数自定`；`worker/config.py:417` 的「课程文件里的 workers 是课程声明」已成死键描述，改掉 |

**对齐已修好的对照**：`remote/plan_run.py:142 with_rollout_workers` —— 离线段**早已**用本机核数覆盖
「导出机钉的值」（`plan_run.py:510`，注释明写「`workers` 不进 `data_fp`」）。
注意两条腿的差别是**谁在拼 spec**：离线段的 spec 由**节点自己**拼（节点知道自己几核），
kind=iter 的 spec 由训练机拼 ⇒ 只能走「显式 0 让节点自己定」这一条路（不能照抄 `with_rollout_workers`）。

**预期收益**：96 核 TPU 机 `cpu_worker_slots(96) = max(96−2, 0.8×96) = 94` ⇒ **8 → 94**。

### S3 存量剃键 —— **用户 2026-10-09 裁决：不做**

**裁决：永不剃，只删读面。** 存量 **167** 个 `curricula/*.jsonc` 里的 `"workers"` 键**永久保留**为死键，
课程文件字节一个不动 ⇒ `course_fp` 零变化、血缘零风险。

由此推出的三条连带约束（写死，别后来者手痒）：

- **`CourseConfig.workers` 字段永久保留**（`biz/course_spec.py:873`）。它是 `extra="forbid"`
  （`biz/course_spec.py:711`）⇒ 删字段 = 167 门课拒启。字段留着 + 无读者 = 死键无害。
- **1.5 的启动告警是死键**唯一**的可见手段**，不能省。167 门课都会命中 ⇒ 每条开课日志各一行，
  只告警不拒（`worker/rl_config_schema.py` 同口径）。
- **`tests/biz/test_reward_golden.py:876`（`assert c.workers == base.workers`）与 `:1082`（same 元组含
  `"workers"`）不动**——字段还在、值还是 8，断言仍成立（它们比的是「两臂/两课是否同构」，不是语义）。

（若将来改主意要剃，脚本形状照 `tools/rl_config_clean.py`：幂等 + 先备份 + sha 回读校验，
且**必须**在停课窗口跑——剃键改课程字节 ⇒ `worker/eval_track.py:208` 门过滤断裂、
`worker/gate_check.py:15` sustain 去重键变、`worker/cmd.py:117` D14 shard 血缘拒收。）

---

## §4 风险

| # | 风险 | 缓解 |
|---|---|---|
| R1 | **删 `CourseConfig.workers` 字段 ⇒ 167 门课全部拒启**：`extra="forbid"`（`biz/course_spec.py:711`） | **字段永不删**（用户裁决 S3 不做）。只删读面：mapping / restart-only / 生成模板 |
| R2 | 节点把 auto 读成 1 ⇒ 远端并发 8→1（比现状更糟） | **评审修正**：真凶不在 `iter_rollout` 的 `or 1`，而在 **wire 归一化**（`manifest.py` 缺席=1 + 拒收 0）与 `iter_job` 的 `else 1`。三处（2.1a/2.1b/2.2）**同批**，且钉子必须是**端到端**：`build_iter_spec(0)` → `validate_rollout_spec` → `run_iter_rollout` 断言并发 = `cpu_worker_slots()`（不是手搓 spec 的单测） |
| R2b | 旧钉子红：`tests/remote/test_remote_iter.py:584`（0→1）、`:264-265` 与 `tests/common/test_manifest_split.py:297-299`（显式 0 拒收） | 三处按新语义改写（0 允许、`<0` 仍拒），并把「出席=1」的老断言留着（`test_manifest_split.py:278`，缺席默认未动） |
| R3 | ~~`course_fp` 断裂~~ | **已消除**：S3 不做 ⇒ 课程文件零写盘 |
| R4 | `resolve_course_quota`（`worker/config.py:432-455`）的响亮行 `[quota] workers 8 -> 4 (multi-course split)` 对照值从「课程 8」变「机器侧读数」 | 语义仍成立（都是「变更前的值」）；`tests/worker/test_course_quota.py` 直接传显式数值，不受影响（已核） |
| R5 | 远端并发 8 → 94 ⇒ 单局墙钟变长、可能撞 `game_watch` 硬顶 | 已有硬顶 + 池回退 + `NN_ROLLOUT_WORKERS_MAX` 加压阀；判读看 **p50 与总时长**（`remote-transport.md` §60 的裁决） |
| R6 | 启动告警刷屏 | 一条课程只打一次（**开课侧**调用，见 1.5）；措辞固定、可 grep |
| R7 | S1.2 的连带：只改 `workers` 的热编辑会被判 `("same", [], [])`（`biz/hot_reload.py:81`），`trainer/loop_steps.py:202` 直接 return ⇒ 文件字节变了却报「没变」 | **可接受**（键是死的，没有活字段随它变）；写进本文与 DECISIONS，免得后来者当 bug 查 |
| R8 | 三门口径变化（`2`→`8`，§3 S1 末） | 已核全仓无并发断言（含 `e2e/test_cloud_iter_e2e.py`）；接受，理由 = 课程值退场后并发随机器走是目标状态 |

---

## §5 门禁（DoD）

```bash
cd nn-training
bash ../tools/githook/nn-py-safe.sh -m pytest tests/biz/test_hot_reload.py tests/biz/test_reward_golden.py \
    tests/biz/test_course_spec_split.py tests/biz/test_rl_course.py \
    tests/common/test_manifest_split.py tests/remote/test_remote_iter.py tests/worker -q
# 全量（含 e2e；~30s 级）
bash ../tools/githook/nn-python-gate.sh
```

新增/修订用例：

- `tests/biz/`：`flat_overrides()` 不含 `workers`；课程文件**带** `workers` 仍能解析（不炸，R1 的钉子）；
  `dead_key_warnings()` 只在显式声明时命中（`model_fields_set` 语义）且文案可 grep（R6）。
- `tests/remote/test_remote_iter.py`：
  - **端到端钉（R2）**：`build_iter_spec(workers=0)` → `validate_rollout_spec` → `run_iter_rollout`
    （mock `cpu_worker_slots` = 96 ⇒ 94；日志含「并发定档」）；
  - auto + `NN_ROLLOUT_WORKERS_MAX=0`（不夹）仍 = 94（**不是 256**，评审 M4）；
  - auto + `NN_ROLLOUT_WORKERS_MAX=2` ⇒ 2（操作员上限对 auto 也生效）；
  - 显式值路径不受影响（既有夹取两例保持绿）；
  - `build_iter_spec(workers=0)["workers"] == 0`（旧 :584 的 1 改写）；显式 4 仍 = 4；
  - `validate_rollout_spec(workers=0)` 通过、`-1` 拒收（旧 :264-265 改写）；
  - 训练侧：`remote_iter_workers=0` ⇒ `build_iter_spec` 收到 `workers=0`（不回落 `args.workers`）；
    显式 `3` ⇒ 3。
- `tests/common/test_manifest_split.py`：显式 0 接受（== 0）；负值拒收；缺席仍 = 1。
- `tests/biz/test_hot_reload.py:78-85`（拿 `workers` 当 restart-only 样例）**改挂 `stream`**
  ——`workers` 已从 `RESTART_ONLY_FIELDS` 摘掉，原用例会红。
- `tests/biz/test_reward_golden.py`：**不动**（见 S3 裁决）。

验收（与 S1 的「本机 164 门零变化」呼应）：开一门课跑一轮，日志里 `--workers` 读数与改动前**逐字相同**
（= 8，来自 `rl.workers`）；上云轮的「并发夹取 8→8」不再出现，改为「并发定档 94（节点自定…）」。

## §6 记录

- `DECISIONS.md §2026-10-09-goalnn-course-workers-removal`（索引行 + 全文）：
  `workers` 是**机器级**键，从 `curricula/*.jsonc` 去除（167/167 门课写死 = 复制粘贴常量，无实验语义）。
  课程定义任务、机器决定并发；远端 kind=iter 的 wire 语义补一档 **显式 0 = 节点自定**
  （缺席仍 = 1；节点跑 hub 侧代码，无 skew）。存量 167 个文件里的 `workers` 键永不剃
  （课程文件字节 = `course_fp`），死键由开课告警点名；`CourseConfig.workers` 字段永久保留
  （`extra="forbid"` ⇒ 删字段 = 167 门拒启）。
- `docs/nn/rl-config.md` 新 §4（顶部）：课程文件里的 `workers` 已是死键，新的机器侧并发旋钮只住
  rl-config / 节点核数；存量键不清理，理由同上。
- `docs/nn/remote-transport.md` 新 §76（顶部）：kind=iter 的 `workers` wire 语义（`0` = auto）
  + 与离线段的差别（谁在拼 spec）+ `_ACTIVE_CODE_SHA` 无 skew 论证。
- `docs/nn.progress.md`：主题索引行补 §76 指针（如该表维护口径要求）。

## §7 评审处置（2026-10-09，一轮）

| # | 评审结论 | 处置 |
|---|---|---|
| R1 | **阻断**：「不下发 workers」在 kind=iter 上不可表达（`iter_job:71` 0→1、`manifest:437/564-577` 缺席=1 + 拒收 0；`hub_client:816` 发布即归一化）⇒ 按原稿实施是 8→**1** | **采纳**：改设计为 **显式 0 = auto**（S2-1）；改动表补 `common/manifest.py` + `worker/iter_job.py`；R2 的钉子改端到端 |
| R2 | `with_rollout_workers` 不适用作模板：差别是「谁在拼 spec」（离线段节点自拼，iter 段训练机拼） | **采纳**：§3 S2 写明；收益路径按 0=auto 重述 |
| R3 | 计数错：167/167（164×8、3×2），不是 164/164（161+3） | **采纳**：全文改 167 |
| R4 | 「本机零行为变化」只对 164 门成立；3 门写 2 的课 2→8（含 e2e 夹具 tiny-a） | **采纳**：§3 S1 末 + R8 显式记账 |
| R5 | 1.5 告警放 `apply_course` 会被只读视图 `course_openable` 每拍重复打，且污染 `run_rl_cluster --json` | **采纳**：落开课侧（`open_course` + `run_rl`），helper 住 `biz/course_spec.py` |
| R6 | 三处旧文本变假：`cli.py:576-579` help、`config.py:411-419` 注释、`course_spec.py:706-708` docstring | **采纳**：列入 1.x/2.4 一并改 |
| R7 | `manifest.py` 缺席默认若改 0 会让「没写 workers」的手写 spec 突然全核扇出（测试也变 flaky） | **采纳（更保守）**：缺席默认**保持 1**，只加「显式 0 = auto」一档（§2 非目标） |
| R8 | 行号漂移：`min()` 在 `:798`（原写 797）、「并发夹取」行在 `:812-818` 区间（原写 812） | **采纳**：行号已在正文修正 |
| R9 | M4：`cap = workers_cap() or MAX_WORKERS` 在 `NN_ROLLOUT_WORKERS_MAX=0`（不夹）时会给 auto 256 | **采纳**：auto 分支显式取 `cpu_worker_slots()`，只把 env 正整数档当上限（2.2 + 用例） |

## §8 拍板结果（2026-10-09）

| # | 问题 | 裁决 |
|---|---|---|
| 1 | 存量 167 个文件的键 | **永不剃，只删读面**（课程文件字节不动，`CourseConfig.workers` 字段永久保留） |
| 2 | S1 + S2 是否同批 | **同批做** |
| 3 | wire 形状 | **显式 0 = auto**（缺席仍 = 1；不改默认、不新增字段）——评审 R1/R7 的合并解 |

⇒ 实施范围 = S1（1.1–1.5）+ S2（2.1a/2.1b/2.2/2.3 + 2.1c/2.4）+ 测试修订与新增，**零课程文件写盘**。
