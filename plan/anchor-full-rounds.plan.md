# plan/anchor-full-rounds — 首轮与 eval 轮发整份，其余发 delta

> 用户口径（2026-10-23 08:56）：**首轮和 eval 轮发整份 weight 和 opt，其它轮发 delta。**
> 目标：把「每轮都发整份 opt」这条 ~79% 的带宽大头换成 delta，同时保证 **eval 轮永远能单独装载**。
> 2026-10-23 评审后改口径（三处硬墙 + 三处定位错误）：基准只能是**最近的锚点整份**、
> 形态改写**注入**（L0 不许碰 torch）、投递侧的展开器是**死代码**（别接线）。

---

## 0. 一句话

把「发整份还是 delta」从**投递侧的档位判断**改成**写盘时的轮类型判断**：
`anchor 轮（首轮 + eval 轮）⇒ 整份；其它 ⇒ delta`，判据只从 `deliver_wire.is_full_round` 来一份。

**顺带修一处漏传**（§1.1 / S6）：补传体不再摘 `perGame`（逐局耗时/击杀/承伤/残血/道具，
线上只有 ~4.3 KB/轮）—— 权重体送出过的那几轮**没有第二次机会**（统计批已销账），摘掉即永久丢。

---

## 1. 现状（实测，`tmp/wire-size.mjs` + y31-null 28 轮）

| 件 | 磁盘/轮 | **线上/轮**（gz+base64） | 形态（今天） |
|---|---|---|---|
| `weights` | 385 KB 整份 / 146 KB delta | **387 / 127 KB** | 每轮 delta（worker 侧 R1；首轮靠 `blob.init`） |
| `opt` | 615 KB 整份 | **509 KB** | **每轮整份**（这条腿没有 delta 路径） |
| `row.json` | 1.1 KB | 0.8 KB | 每轮 |
| **整轮** | — | **~637 KB** | opt 占 **79%** |

500 轮、`eval_every=5`（锚点轮 101 个）的账：

| 方案 | 线上总量 | 相对现状 |
|---|---|---|
| 现状（opt 全整份 + weight 全 delta） | **311 MB** | — |
| **本 plan**（锚点轮 weight+opt 整份，其余全 delta） | **~198 MB** | **省 ~113 MB（36%）** |
| 全 delta（连 eval 轮也 delta，**不采用**） | 139 MB | 省 172 MB（55%） |

⚠ 本 plan 比「全 delta」少省 ~60 MB —— 那 60 MB 就是**买 eval 轮独立可装载**的价钱
（101 轮 × (387−127 + 509−154) KB）。这笔钱值得付：eval 轮是门判据与控制台读数**指向的那一轮**（见 §2）。

**另一条支持本 plan 的既有事实**：`remote/deliver_mode.BYTES_PER_ROUND["bulk"] = 276,852` 正好等于
「opt delta 存在」时的每轮线上量（127+154+1 KB）—— 那张档位表**本来就是按本 plan 的结果测的**，
而今天 `bulk` 实际发 637 KB。本 plan 是让它成真的那一步（但**不改**档位常量：见 §9）。

### 1.1 同一批一起修的漏传：`perGame`（逐局原始量）

云机腿上「耗时/击杀/承伤/残血/道具」在控制台**看不到**，但**不是没算**：

* 生产者早就在：`worker/reports.compact_per_game` 的 `_PER_GAME_FIELDS`（`ticks`/`kills`/
  `playerDamageTaken`/`startLives`/`playerDeaths`/`puGotTank`/`powerUpsCollected`/`puSpawn*`/…）；
* 实测体积：94 局 = **23.3 KB 原 / 4.3 KB 线上（gz+b64）**，占整轮 637 KB 的 **0.7%**；
* **权重体把它摘掉了**：`offline_deliver._post_artifact` 走 `wire_row`（浅拷摘 `perGame`/`perGameSecs`），
  理由是 Q2「逐局明细随抽屉/离线包到达」。
* ★ 评审修正：**统计批是另一条通道**（`?parts=metrics` 的 row 不摘，hub 的
  `store_offline._land_round_metrics` 照样写 `<课程>/it<N>/per-game.json`）。但它只覆盖
  「没走过权重体」的轮 —— `_sync` 成功后把该轮记进 `_metrics_sent`（`offline_deliver:1143`），
  于是**权重体送出过的那几轮永久没有逐局明细**。`anchors` 档 = 锚点轮（恰好是控制台 eval 列
  指向的那几轮）；`full`/`bulk` 档 = 每轮。y31-null 的现场（28/28 走权重体、0 条 `offline_metrics`、
  账本 `perGame` 计数 0）就是这个最坏形状。

⇒ 本 plan 顺带修掉（S6）。体积代价 4.3 KB/轮，500 轮 +2.1 MB（占 §1 的 198 MB 的 1%）。

---

## 2. 为什么 eval 轮必须整份（不是偏好，是判据要求）

1. **判据与读数指向它**：门判据、A 层读数、控制台 eval 列/弹窗，认的都是 `(iter, wver)` 这一对。
   只有 delta ⇒ 任何「拿这一轮权重去回放/对比」的分析都要先重建整条链。
2. **`resume_anchor` 只挑整份轮**（`hub/queue_resume.py:183` 逐条判 `any_container_file`）：
   eval 轮不整份，续跑锚点就只能退到更早的轮，丢掉最近的进度。
3. **链一断就全灭**：今天 y31-null 已经演示过（it1 缺 numpy ⇒ it2–it28 全部「基准不在本机」）。
   eval 轮整份 ⇒ 每 5 轮有一个**可独立装载**的落点，链断只损失 ≤ N 轮而不是整段。

---

## 3. 判据（唯一来源，不许第二份）

```python
# remote/deliver_wire.py —— 写盘侧与投递侧**共用**的这一条（O(1)，不需要产物清单）
def is_full_round(it: int, *, first_it: int, eval_every: int) -> bool:
    return int(it) == int(first_it) or (int(eval_every) > 0 and int(it) % int(eval_every) == 0)
```

* **为什么不用 `pick_anchors` 的读数分支**：写盘那一刻 `eval_log.jsonl` 里还没有这一轮的读数
  （评估在这一轮落盘之后才跑），所以写盘只能取**计划判据**（= `pick_anchors` 的 `every` 兜底
  分支，逐字同一条）。两处不一致的方向是安全的：**多写整份无害，少写整份才会毁掉 §2 的保证**。
* **`first_it` = 开段时产物目录里最小的 `it-*`**（首次开段就是 `plan.start_it`）。续跑同一目录时
  它仍是本 run 的第一轮（不是本会话第一轮）——这没问题：续跑的第一轮有上一轮当基准。
  真要「新 hub 上也能单独装载」，那是 `full` 档 / hub 落地的事（§8-2），不该靠首轮兜。

⇒ 统一出口（只这一处）：`remote/deliver_wire.is_full_round`。
⚠ **不许**在 `ArtifactStore`、`deliver_parts`、`hub/deltas` 各写一份「是不是锚点轮」的判断
——今天的教训（`pick_anchors` 与 `pending_anchors` 两处口径）就是这么来的。

---

## 4. 设计：形态在**写盘时**定，不在投递时定

| 位置 | 今天 | 本 plan |
|---|---|---|
| 合成 manifest（`plan_run._run_iteration`） | 继承 `w_fmt`（每轮 delta） | **锚点轮弹掉 `w_fmt`** ⇒ worker 回整份权重；其余保持 delta |
| `ArtifactStore.checkpoint`（**注入** `after_write`） | 原样落两件 | 锚点轮：opt 若是容器就地展开成整份；其余：**`opt.tar` 就地写成 BOD1 容器**（base = 最近那份整份） |
| 投递（`offline_deliver._post_artifact`） | 产物目录里是什么发什么 | 不变（**`_full_parts_for` 是死代码，别接线** —— 接线会把 delta 重新展开，省量当场归零） |
| hub 落地（`store_offline` → 注入的 `land_into_stage`） | 两件都认容器、按内容命名 | 不变 |
| `resume_anchor` / `find_existing` | 两态都认 | 不变 |

**为什么写在写盘时**：写盘那一刻「这一轮是不是 eval 轮」由**计划**唯一确定（`it % eval_every`），
不需要等 A 层读数；而投递可能在几小时后由另一个进程做（补传腿），那时再判就有三份口径。

⚠ **实现时的一处口径收正（2026-10-23）**：非锚点轮的容器写回 **`opt.tar`**（不是改名成
`opt.delta`）。理由：`opt.tar` 同时是 deliver 腿发什么（`_post_artifact` 只读 `opt_path`）、hub 侧
duplicate/conflict 判据的输入、以及下一轮装载的输入 —— 改名成 `opt.delta` 会让补传**一个字节
opt 都不发**（`opt.tar` 不存在就发空），而 `opt.delta` 又被 `resume_anchor` 跳过。这条链上
**内容才是权威**（`delta_files.head4/named_for` 一直是这个口径），hub 落位时按内容改名。

### 4.1 基准为什么必须是「最近的锚点整份」（评审 P0-1）

hub 的落地器找基准的判据是 **sha256 逐字节相等**（`opt_standard._base_tar_bytes:118`，找不到就
响亮拒收）。而**重打出来的 tar 字节随 mtime 变**（`opt_delta.rebuilt_opt_tar:397`：
「同一份输入两次调用的字节可能因 mtime 不同而相异」）。

⇒ 客户端拿「上一轮重建出来的整份」当基准，这个 `base_sha` 在 hub 侧**永远对不上**，第一轮
delta 之后整条链在 hub 上都解不开。**双方逐字节共有的整份只有一种：客户端写下的、hub 原样收到
的那几份 —— 即锚点轮（和起点）。** 所以：

* base = 从 `it-1` 往回扫到 `it-first_it` 的**第一个锚点轮**的 `opt.tar`（它必然是整份）；
* 找不到（锚点被删 / 段起点不在）⇒ **回落写整份** + 一行日志（D5）；
* 不需要 32 轮上限、不需要递归链、不需要成环护栏 —— 差额只有一个锚点，链不存在。

代价（`tmp/review-anchor-opt-delta.py`，28 轮实测）：非锚点轮 delta 从 152 KB（上一轮为基准）
变成 **154 KB**（锚点为基准，最大 164）—— 可忽略。

### 4.2 层的纪律（评审 P0-2）

`remote/artifacts` 与 `remote/opt_delta` 都是 **L0**，`tests/test_remote_dag.py` 要求每条边
**严格向下**（延迟 import 也算）。⇒ `checkpoint` **不 import** 任何落地器/packer，只收一个注入的
`after_write(it, it_dir)`；打包/展开全部住新模块 `remote/round_forms`（**L2**）。

---

## 5. 实施（每步独立可验）

### S1 `remote/deliver_wire.is_full_round` + `ArtifactStore` 注入点

* 新增 `is_full_round(it, *, first_it, eval_every)`（§3）。
* `ArtifactStore.__init__` 增 `after_write: Callable[[int, Path], None] | None = None`
  （缺省 `None` = 今天的行为，历史调用点零改动）；`checkpoint` 在**两件写完、算指纹之前**调它，
  失败只记一行（绝不挡这一轮落盘）。
* `weights_fp` / `opt_bytes` / `state.last_weights_fp` 一律按**改写后**的字节算（否则账本说谎）。
* ⚠ 起点快照（`store.checkpoint(..., row=None)`）也走这个钩子：`it-<start_it>` 是首轮、本来
  就是整份 opt ⇒ 零成本（只读 4 字节），但它从此就在候选面上（下一轮的基准）。
* 验：6 轮（`eval_every=2`）⇒ `it1/it2/it4/it6` 两件都是整份，`it3/it5` 两件都是容器。

### S2 `remote/round_forms`（新，L2）：按轮定形态

* `RoundForms(root, first_it, eval_every, log)`：
  * `is_full(it)` ⇒ `deliver_wire.is_full_round`（单一判据）；
  * `nearest_full(it)` ⇒ 往回扫**第一份不是容器的 `opt.tar`**（稳态下它就是最近的锚点轮；
    「回落写整份」的那几轮也算候选 ⇒ 断链只损失一轮省量，而不是整条链）：
  * `prepare_manifest(m, it)` ⇒ 锚点轮 `m.pop("w_fmt")`，其余 `m["w_fmt"] = W_DELTA_FMT`
    （**权重由 worker 在回传时定形**：它手里就有整份，客户端不需要展开器）；
  * `after_write(it, it_dir)`：
    * 锚点轮：`opt.tar` 是容器 ⇒ `opt_standard.ensure_standard_opt(..., extra_roots=(root,))`
      就地落地成整份（保留 `.delta` 旁存）；不是容器 ⇒ 零成本（只读 4 字节）；
    * 非锚点轮：`opt.tar` 是整份 ⇒ 找最近锚点整份当 base ⇒ `opt_delta.pack_opt_delta(...)`
      写回容器（`base_sha` = base 的 sha256）。**任一步失败 ⇒ 保留整份 + 一行日志**（D5/D6）。
* 注入点：`plan_handoff.open_run_context`（唯一装配点，`course.eval_every` 就在手边）。
  `course` 缺失 / **`eval_every < 2`** ⇒ **不装 hook**（= 今天行为）+ 一行 WARN。
  ⚠ 门槛是 **2**（不是「缺失才不装」）：`CourseSpec.eval_every` 的缺省就是 **1** ⇒ 装了等于
  「每轮都是锚点」：opt 与今天一样、权重从 delta（127 KB）退回整份（387 KB）**净亏 ~267 KB/轮**。
  绝不把缺省补成 5（猜一个数 = 把「没生效」伪装成「生效」）。
* 验：6 轮（`eval_every=2`）⇒ 3/5 是 `BOD1`；`opt_standard.ensure_standard_opt` 逐轮解回整份
  （复用 `tests/remote/test_opt_standard.py` 的「解出来 == 原始状态」判据）。

### S3 投递侧**不动**（评审 P0-3）

* `deliver_parts.full_parts_for` 全仓只有测试在调（`_post_artifact` 直接读盘上字节、不看 mode）
  ⇒ **保持未接线**，并在它的 docstring 里写明「接线会把非锚点轮重新展开、把本 plan 的省量清零」。
* 不再新增「整份 → delta → 整份」的往返断言（那是在给死路做保）。

### S4 观测

* `status()` 增 `opt_form`（最近一次发出去的 opt 形态）、`opt_saved_bytes`
  （本会话累计：最近一份整份的线上字节 − 每份 delta 的线上字节）。
  ⚠ 实现口径：两者都从**实际发出去的字节**量（`len(body["opt_tar_b64"])`，累加逻辑在
  `deliver_wire.note_opt_wire` 那个纯函数），不是根据磁盘大小估；本会话没见过整份就不计
  （宁少不多 —— 编一个基数会把「省了多少」变成假读数）。
* hub 账本 `offline_artifact` 行增 `opt_form`（`full`/`delta`，来自收到的字节的魔数）。
* 验：单测钉字段存在与取值。

### S5 端到端（云机腿）

* 一条 30 轮的小课（`eval_every=5`）：`it1`+`it5/10/15/20/25/30` 两件整份、其余 delta；
  hub 侧 30 轮全部**可装载**；线上总量 ≤ 现状 70%。
* 断链演练：删掉 `it-005` 的整份 ⇒ `it-006` 的 opt 应回落整份（日志一行）。
* **无云机版（已落地，`tests/remote/test_round_forms.py::test_plan_run_wires_both_halves`）**：
  从 `run_plan_job` 入口跑一段真计划（`eval_every=2`），逐轮查 ① `run_job` 收到的 manifest
  （锚点弹 `w_fmt`、其余 `delta-i16`）② 产物目录的 opt 形态。**两半分开验不够**：这条链的
  失败模式正是「机制写好、装配点没接」（缺基准不报错，只是每轮都发整份）。

### S6 补传体保留 `perGame`（§1.1 的漏传）

* `remote/deliver_wire.wire_row(row, *, keep_per_game: bool = False)`：**默认行为不变**
  （在线实时体若将来恢复，仍摘）；补传路径（`offline_deliver._post_artifact`）传 `True`。
  ⚠ 摘键的调用点在**腿本体**（`offline_deliver:1239`），不在 `build_artifact_body` —— 别改错文件。
* 要改的既有测试：`tests/remote/test_offline_deliver.py::test_per_game_detail_does_not_ride_the_realtime_body`
  —— 改成两条：① 补传体**要带**（`body["row"]["perGame"]` 局数 == 账本里的局数）；
  ② `status()["pergame_deferred"]` 在补传路径上恒为 0（语义跟着变成「实时体摘掉的局数」，
  不许留一个假读数）。
* 验：hub 侧 `it-*/row.json` 出现 `perGame`，局数 = `report.games`，字段集 == `_PER_GAME_FIELDS`；
  控制台的「耗时/击杀/承伤/残血/道具」列对云机腿**不再空白**。

---

## 6. DoD

| # | 判据 | 怎么验 |
|---|---|---|
| D1 | 首轮 weight+opt 都是整份 | 产物目录 `it-<first>/weights.json` + `opt.tar` 头 4 字节不是魔数 |
| D2 | 每个 eval 轮都是整份 | `it % eval_every == 0` 的轮目录里两件都是整份 |
| D3 | 非 eval 轮是 delta | 同一课里其余**两件都是容器**（`BWD1`/`BOD1`，按内容判、不看名字） |
| D4 | **eval 轮可单独装载** | 只留 eval 轮目录（其余全删）⇒ `ensure_standard_weights` / `ensure_standard_opt` 仍能给出整份 |
| D5 | 缺基准回落 | 删掉最近锚点的 `opt.tar` ⇒ 下一轮写整份 + 一行日志 |
| D6 | 结构变化回落 | 改 `param_groups` ⇒ `pack_opt_delta` 抛 ⇒ 该轮写整份（不是失败） |
| D7 | 体积 | 500 轮（`eval_every=5`）线上 ≤ **210 MB**（现状 311 MB；实测口径 198 MB） |
| D8 | 可观测 | `status().opt_form/opt_saved_bytes`、hub 账本 `opt_form` 都有值 |
| D9 | **逐局原始量到位** | hub 的 `it-*/row.json` 有 `perGame`，局数 == `report.games`、字段集 == `_PER_GAME_FIELDS`；控制台的耗时/击杀/承伤/残血/道具列对云机腿不再空白 |

---

## 7. 风险与回退

| 风险 | 处置 |
|---|---|
| **hub 侧缺 torch** ⇒ 容器解不开（只影响非锚点轮的"整份"，不影响装载判据） | 今天 `hubServerSpec` 已挂 venv `sitePackages`；`hub/boot._landing_deps_warn` 启动即报。缺 torch 时**仍能落容器**（原件不丢），锚点轮不受影响（它们本来就是整份） |
| 磁盘 | **变少**：非锚点 opt 615→154 KB ⇒ 500 轮产物目录 ~380 MB → **~215 MB（−43%）**（锚点轮多 101 MB 已被这笔省量抵掉） |
| 基准不再是「上一轮」⇒ 非锚点轮的解回多走一步 | `opt_standard` 的 `_base_tar_bytes` 会先打内容寻址缓存（`blob_cache/<base_sha>`）；每轮一次 ~12ms，相对一轮 PPO 可忽略 |
| 写盘时多一次 pack（两个 tar 的 `torch.load`） | 只在非锚点轮（4/5），实测 ~15ms/轮；失败一律回落整份 |
| 形态在会话间变（`eval_every` 改了 / 补传重投） | 同一 it 换形态 = 不同字节 ⇒ hub `store_offline` 判 **409 conflict**（客户端会拉黑该轮）。所以 `first_it`/`eval_every` 只从**计划**取，且已投递的轮不重投 |
| 补传体带 `perGame` 后体变大 | 4.3 KB/轮（0.7%）；与 8 MB 上限差三个量级 |

**回退**：不装 `after_write` / 不弹 `w_fmt` 即回到今天的行为（权重每轮 delta / opt 每轮整份），
一个参数、零数据迁移。

---

## 8. 已拍板（评审结论）

1. **`eval_every` 用课程的**（`CourseSpec.eval_every`）：写盘判据取 `it % every == 0` 这一支
   （`pick_anchors` 的读数分支在写盘时拿不到）；两处不一致时以「多写整份」为安全方向。
2. **末轮**靠 hub 落地（照常写 delta，hub 收到后展开）—— hub 缺 torch 时锚点退到上一个 eval 轮，
   这正是 `_landing_deps_warn` 要响的事。不做客户端二次写盘（`promote_full` 不实现）。
3. **opt delta 的量化档不动**（i16/f16 已定）。opt 是 Adam 的 m/v，`sanitize_adam_sq` 与 f16
   残差在「锚点基准」下还没量清 ⇒ 不叠加第二个变量。
4. **`perGameSecs` 不带**：今天没有读者（控制台「耗时」是 `ticks` 聚合，
   `trainer/loop_remote_drive.py:372` 明确 `pop` 掉它「只用于诊断」）。带它 = 4 KB/轮买一个空列。

---

## 9. 不在本 plan 内

* 不改 `lives/敌数/关卡形态/max_ticks`（任务侧不动）。
* 不引入新的里程碑轮（milestone）—— 若将来要「每 50 轮也整份」，往 `is_full_round` 里加一条即可。
* 不改 weights 的 delta 算法（`pack_weights_delta` 的 i16 已定）——**也不在客户端重打权重 delta**：
  形态由 worker 侧 `w_fmt` 给（§4 表），客户端只负责锚点轮要整份。
* **不动 `deliver_mode.BYTES_PER_ROUND`**（896,072 / 276,852）：那是**档位策略常量**，改它会改
  慢链路上的升档阈值（= 上行行为变化），不是本 plan 的收益。已记偏差：`full` 今天实际发 637 KB
  而不是 896 KB（`_full_parts_for` 未接线）——单列一条后续，不夹带。
* **「云机掉线后本机自愈（不再手工改 `rollout_src`）」不在本 plan** ⇒ 见
  `plan/offline-hold-stale-fallback.plan.md`（同批上线，但那是**接管**问题，本 plan 是**形态**问题）。
* **不改 `.weights-standard` / `.opt-standard` 旁路缓存**、不改 `hub/deltas` 的落地判据、
  不改 `resume_anchor` —— 它们本来就不关心「这轮为什么是容器」。

---

## 10. 落地记录（2026-10-23）

| 件 | 位置 |
|---|---|
| 判据 + 线上字节累计 | `remote/deliver_wire.py`：`is_full_round` / `note_opt_wire` / `OPT_WIRE_ZERO`（`wire_row(keep_per_game=…)`） |
| 形态实现（L2） | `remote/round_forms.py`（新）：`for_store` / `is_full` / `prepare_manifest` / `after_write` / `nearest_full` |
| 装配点 | `remote/plan_handoff.py`（`open_run_context` → `store.after_write` + `ctx.round_forms`） |
| 驱动侧 | `remote/plan_run.py`（`_run_iteration` → `prepare_manifest`） |
| 落盘钩子 | `remote/artifacts.py`（`ArtifactStore.__init__(after_write=…)`，算指纹之前调） |
| 投递读数 + S6 | `remote/offline_deliver.py`（`status().opt_form/opt_saved_bytes`；`wire_row(..., keep_per_game=True)`） |
| hub 账本 | `hub/store_offline.py`（两处 `offline_artifact` 行都带 `opt_form`；形态判据 `delta_files.form_of`） |
| 层账本 | `tests/helpers/remote_dag.py`（`remote.round_forms` = **L2**） |
| 用例 | `tests/remote/test_round_forms.py`（新，16 格）· `tests/remote/test_offline_deliver.py`（S6 + S4 读数）· `tests/hub/test_offline_backfeed.py`（账本 `opt_form`） |

**没做/做不了的**：S5 的云机真跑（30 轮 + hub 侧装载）——本机没有云机腿；那条的判据（D7 的
体积、hub 可装载）只能上线后首个课重测。其余 DoD 都有无头用例钉住（D1/D2/D3 →
`test_anchor_rounds_keep_full_and_the_rest_become_containers`；D4 → `…_loadable_alone`；
D5/D6 → `missing_base` / `structure_change`；D8 → `status` 两格；D9 → 补传体用例）。
