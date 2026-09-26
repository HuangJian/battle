# NN 训练控制台集成 — 技术档案

> dashboard 侧：组件面 / 调度器视图 / 任务包与产物两条腿。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---
## §16 节点统计与课程解耦：合所有流 + 按天窗口 + `it` 只作内部过滤器（2026-09-26）

「节点是机群级资产」这句话控制台早就写在注释里（`/api/pool` “没有任何按课程的东西”），但数据层
一直只聚合 `tmp/**` 里 **mtime 最新的那一个** `dist-agent-meta.jsonl`（旧动机：避免旧训练流的
数千条历史淹没新数据）——节点页实际是「最近活跃那一门课」的页。本轮解耦（plan/
nodes-decouple-from-course.plan.md）：

* **数据源合并所有流**：递归扫 `tmp/` 下所有 `dist-agent-meta.jsonl`（课程目录 + 独立 eval run
  目录 `tmp/<name>.jsonl.run/`），逐流取**完成水位**（账本 `training_log.jsonl` 最后一个
  `iteration` 事件）只用于过滤「进行中那一轮」的行。
* **单一时间口径**：落桶按**本地日**（`byDay: 'YYYY-MM-DD' → node → DayBucket`），视图按
  `?days=today|yesterday|7|all|N` 切窗口。**课程内序号 `it` 不出现在任何展示字段**（跨课不可比，
  它只是服务端解析循环里的过滤器）。
* **一次算全量、切天纯投影**：`aggregateNodeHistory()`（与窗口无关，进 SWR 缓存）⊕
  `projectWindow(agg, w)`（纯函数）⇒ 切天零重算。预筛（整份文件 mtime 早于 `POOL_EPOCH_MS`
  才跳过）**钉在 epoch**，否则「切天零重算」不成立。
* **健康度 = 最新完成轮的贡献 vs 并发**（判据 `nodeHealth` 现成）：跨课「最新完成轮」按**完成
  时刻**选（`iteration.time`；读不出用 meta mtime 兜底）——**不是**比 `it` 大小。停摆课因完成
  时刻旧而自然落选。
* **两个容器各负责一层**：节点表（`/api/pool`）的「窗口内局数 `winRollout/winEval`」与 pill 行
  （`/api/state` 的 `lastContrib`）**共用同一份 `aggregateNodeHistory()`**；表格状态列改名
  **「成功率」**（窗口内最近 ≤10 次结算完成率），与 pill 的**「产能」**分列分名。

⚠ **两条容易踩的**：① meta 行的 `ts` 与账本 `time` 都是 Python `strftime` 写的**训练机本地时间、
无时区后缀**（UTC `toISOString()` 只出现在 `lastError` 的字符串前缀）；分桶前**禁止** `Date.UTC`。
② `poolStatus`（成功率）**函数不变但输入随窗口变** —— 用户可在表头看到「成功率随所选窗口变」。

决策条目：`DECISIONS.md §2026-09-26-nodes-decouple-from-course`。

## §15 「在线/离线」收敛成**一颗开关**（本机配置 + hub 模式一起动）+ 第三源漂移徽标（2026-09-24）

用户 2026-09-24 报障：「离线课切回在线后，Kaggle 仍因缺 bun 拒单」。实锤：切在线后派出的 job
仍是 `"kind": "run"`（`tmp/<课>/remote-jobs/<job_id>/manifest.json`）。

### 根因：**同名不同义的两个「离线」只有一半能被那颗开关改到**

| # | 事实 | 出处 |
|---|---|---|
| F1 | 两个「离线」：hub 派发模式（`setCourseMode`：只写意图 + POST hub）与训练模式（`trainModeKnobs` → `courses.<课>.rollout_src=run` + `run_iters=-1`） | `server/actions/course-mode.ts`、`stack/specs.ts::trainModeKnobs` |
| F2 | 两者**各只有一条入口且不是同一条**：训练模式**只有开课**能写（运行中往 `start` 动作发 `trainMode` 被 400 拒），hub 模式有独立开关 ⇒ 只翻后者必然半状态 | `course-lifecycle.ts`、`api/route.ts` |
| F3 | job 的 kind 只有一个判定点：有 `plan_bytes` ⇒ `run`；有 `rollout_spec` ⇒ `iter`；都无 ⇒ `ppo` | `rl/loop_steps.py::publish_job` 调用点 |
| F4 | bun 只在**云机自己跑 rollout** 时需要（`kind in ("iter","run")` ⇒ `run_iter_rollout` ⇒ `resolve_bun`） | `remote/worker.py`、`remote/iter_rollout.py::resolve_bun` |

### 决定（plan/train-mode-hot-switch.plan.md L1）

**那颗开关 = 唯一的模式开关**：`setCourseMode` ① 先落本机配置（同步写盘，python 每轮读的那份
rl-config）② 再推 hub 镜像。机制上不需要重开课：`_rollout_source` / `_run_segment_iters` **每轮**
各读一次 `dist_common.load_dist_config()`（`rl/loop_round_steps.py`），且控制台从不把
`--rollout-src` / `--run-iters` 传进 trainer argv（唯一传 `--run-iters` 的是 bundle 导出，只读快照）。

**语义边界（已写进回执文案）**：

| 档位 | 配置 | job kind | 节点要 bun 吗 |
|---|---|---|---|
| 离线 | `rollout_src=run` + `run_iters=-1` | `run`（本机不跑这门课：云机取包接手） | **要**（这是 `run` 的定义，不是 bug） |
| 在线（缺省） | 两键**都不在**（缺席 = local） | `ppo`（本机采样，云机只算 PPO） | 不要 |
| 在线 + 显式 `node` | `rollout_src=node` | `iter`（整轮上云） | 要（显式选择的代价） |

### 写面收口（**边界就是三个函数**）

| 函数 | 干什么 | 写 `courses.<课>` 吗 |
|---|---|---|
| `pushCourseMode` | 只推 hub + 落意图（开课 / 停课 / 回灌共用） | **绝不** |
| `setCourseMode` | 那颗开关 = `applyTrainModeToConfig` + `pushCourseMode` | 写 |
| `applyTrainModeToConfig`（`actions/train-mode.ts`） | 唯一写面：域换算仍走 `trainModeKnobs` | 写 |
| `restoreCourseModes`（起 hub 回灌） | 只推 hub —— 回灌不是用户动作 | **绝不** |

为什么必须拆：`pushHubMode`（开课/停课的 hub 推送，带 3×2s 重试）**就是循环调 `setCourseMode` 的**
⇒ 往那颗开关里塞写配置，会让**开课重复写盘 1–3 次**、还会把**停课**误翻译成「云机接手」。

### ★ 生效时机是**段边界**，不是「下一轮」

`run_iters<0`（离线缺省）时一段 job 覆盖 `it → end_it`（**课程末**），训练侧阻塞在
`_remote_ppo(..., wait_timeout_sec=8h)`。**切回在线在该段结束前完全无效果**——而这一段可能就是
课程剩下的全部。**不做抢占**是有意的（已经在飞的段不取消），所以回执与文档都写明了这条，
并指向立刻断开的把手：**停课 / 暂停**。

推论（运维口径）：想「只停 hub 派发、本机继续训」请用**暂停键**，切离线现在的含义是
**这门课交给云机接手**（本机在下一个轮边界干净收官，不再有「段等待」；云机取任务包跑）。

### node 往返：`offline` 会把显式选的 `node` 覆写掉

`offline` 写 `rollout_src=run` 是覆写，于是「显式选过 `node` 的课」一下离线再切回在线时那格已经
没了 ⇒ 静默降成 `rl.rollout_src`/`local`（开课路径没这个问题：弹窗每次重选）。
**修法**：切离线前把**当前生效的非 run 源**记进 `console-state.courseRolloutSrc`（只记 `node`/`auto`——
`local` 是缺省，记了是噪声），切回在线时取回并回执「rollout 位置恢复为 node」。

### 第三源：漂移徽标现在覆盖 `rl-config` 那一格

`modeDriftOf` 原来只比「控制台意图 vs hub 事实」。用户报障的现场是**第三个源**没跟上：
hub 与意图都回到在线，而配置里还是 `run` ⇒ 下一段照样派 `kind=run`。

* 取数：`stateView.courseRolloutSrc`（逐课 `resolveRolloutSrc(cfg, c)`，纯函数 over 内存 cfg、零 IO）
  —— `modes.rolloutSrc` 只有**查看课程**一个，而矩阵是逐行全课表，非当前课程的行需要自己那一格。
* 渲染：`modeDrift.configRun`（仅当 `intent === 'online'` 且配置是 `run`）⇒ 行上多一个
  「配置仍是离线（云机接手）」徽标，与「意图未生效」各说各的（不混成一个）。无意图 / 旧视图 ⇒ `null`，**不编**。

### 验证

* `tests/course-mode.test.ts`：切离线落两键 / 切在线两键都不在 / **node 往返回到 node** / `local` 不留痕 /
hub 不可达时配置照样落 / 文案含「本机不跑这门课」「不需要 bun」「轮边界生效」；
* 逆测试：`pushCourseMode` 与 `restoreCourseModes` **一个字都不写** rl-config（防 F9 复发）；
* 跨语言钉子：`nn-training/tests/test_run_segment.py` 钉「控制台写的两键 ↔ `_rollout_source`/`_run_segment_iters`」；
* 既有源码断言跟着写面搬家（`tests/train-mode-offline.test.ts`、`tests/rollout-src-launch-option.test.ts`）。

---
## §14 离线课三处状态各说各话：hub 事实 vs 控制台意图 + 0 回传不说「回传中」（2026-09-23）

用户 2026-09-23 报障（新开三个离线课 `x20-demo-mix` / `x20-firstkill` / `x20-terminal`，
**三个都还没被云机取走**）：总览里两个「回传中」一个「等回传」；课程区 `demo-mix` 显示
「在训」而另两个「离线（只收回传）」；操作列混着「恢复中」/「切离线」；`demo-mix` 还显示
「等远端回传，队列 1 在飞 0」且有「切离线」却**没有**「导出任务包」。

### 根因：三处独立，但都源于「同一个事实有两个源，而只有一个源在说话」

| # | 现象 | 机制 |
|---|---|---|
| ① | 课程区 `demo-mix`「在训」/另两个「离线」 | **hub 那份 mode 才是矩阵的事实源**（`overview.rows[].offline`）。三处意图（`rl-config` 的 `rollout_src=run`、`console-state.courseModes`、`training-enabled.txt`）都是 offline，但 hub 里 `demo-mix` 是 `online` ⇒ 它走本地 13 步词（`等远端回传` + 「在训」），另两个走离线段 |
| ② | 顶栏两个「回传中」 | pill 的离线口径只有**一个 bit**（`offline: Set<string>` = hub 说不说它离线）。「回传中」是一个**进度断言**，而 hub 侧离线段进度表（`/admin/offline`）是空的 ⇒ 旧口径把「云机还没取走包」说成「回传中」 |
| ③ | 操作列「切离线」vs「恢复在线」 | 与 ① 同步（同一个 `overview.offline` 决定按钮文案）——**不是**第二个开关 |
| ③b | 「恢复中」 | 它是 `pauseBadge`（暂停意图 vs 训练进程实际回执的**待生效**徽标），与「切离线/恢复在线」是两个不同元素，不在同一列打架 |

①背后的真凶（hub 日志实锤）：hub 刚重启时 `courses=[]`，而控制台的「意图回灌」跑在
**第一次顺带扫描之前** ⇒ 九条 `POST /admin/courses` 全 400；三个离线课各自靠「开课时的
有界重试（3×2s）」去赌发现时机，**恰有一门输掉**（最后一次重试与发现同一秒）⇒ 该课静默留在
`online`，而面板一路显示「在训 / 切离线」（操作员以为自己开的是离线课）。

### 修法（四处，两层各治一半）

1. **hub 侧**（`remote/hub_server.py`）：`POST /admin/courses` 在「课不在表里」时**按需真扫一次**
   （`discover(force=True)` 跳 2s 间隔闸）再试；模式非法**不**扫盘（直接 400）。全文 →
   `docs/nn/remote-transport.md §36`。
2. **控制台回灌**（`server/actions/course-mode.ts`）：`restoreCourseModes` 每课**有界重试**
   （3×2s），但**只对「hub 还不认识这门课」这一类错误**重试 —— hub 连不上重试没有意义，
   而回灌挂在 `start` 的返回路径上，白等 N×2s 只会让「起 hub」变慢。
3. **矩阵把两个源摆在一起**（`view/course-matrix.ts::modeDriftOf` + `CourseMatrix.tsx`）：
   新事实 `stateView.courseModeIntents`（控制台那份**意图**，权威）与 `overview`（hub 事实）
   比对 → 行上出「意图未生效」徽标，悬停写清「意图是 X、hub 现在当 Y」与两条恢复路径
   （点该行开关再推一次 / 点「hubServer」回灌全部）。`null` = 无从判断（没有意图 / hub 不认识
   它 / hub 不可达）—— **不把「不知道」画成「没问题」**，那正是这一类事故的成因。
4. **pill 的离线口径升级**（`view/loop-queue.ts::coursePills`）：入参由「离线课名集」改为整个
   hub 总览视图（`offline` + `offlineRounds` + `hubSeen`）⇒ 三态：

   | 判据 | 状态 | tone |
   |---|---|---|
   | 意图 ≠ hub 事实（两个源都读到） | **意图未生效** | y |
   | hub 离线 ∧ 已回传 > 0 | **回传中** | g |
   | hub 离线 ∧ **0 回传** | **等云机** | y |

   优先级仍在确定性事实（已暂停 / 已收官 / 已中止 / 待进程）之后，再之后才是本地「在等什么」四态。

### 「导出任务包」与「下载」的区别（用户同日提问）

* **导出任务包** = *生成* `<课>.zip`：只读快照（不与训练的 per-course 锁抢），随时可导，页面每 5s
  轮询 `taskBundleInfo`；**离线开课时会自动跑一次**，且先把旧包挪进 `tmp/<课>/stale-packs/`
  （代码可能已变）⇒ 导出完成前 hub 的 `/offline/task-pack` 404，云机会一直等新包（预期行为）。
* **下载** = *取走*已生成的那一份（浏览器直连 `/api/taskBundle?course=`，带 attachment 文件名）
  → 手工上传 Colab / Kaggle。包不存在时显示的是**「未导出」**，不是灰按钮。

### ★ 同日修订（用户指令）：「下载」链接删掉，导出键兼取回；导入改真按键

用户反馈原话：「歧义太大！去掉「下载」链接，点击导出按键就是下载已经生成的任务包；导入也要改成
按键形式，文本设为「导入训练结果」；「恢复在线」按键，文本改为「切换成在线」」。

上一条把两者分开讲，正是因为它们是**两个源上的两件事**（生成 = 控制台后台一次性快照；
取回 = 浏览器导航到 `/api/taskBundle`）；但从操作员看，它们是**同一个意图**——「我要拿到这个包」。
两个控件把动作挂在一个他看不见的内部状态上：包未生成时「下载」**不在页面上**（只剩一句「未导出」），
「点了导出却没反应」于是成了最常见的误判。

* **现在只有一个键**，**点一下的结局恒定 = 拿到包**：已有包 ⇒ 直接下载；尚未生成 ⇒ 先起导出，
  轮询（复用那条 5s `taskBundleInfo`，不另起通道）看到包出现后**自动下载**。
* **导入改真按键**（`导入训练结果`）：旧形状是 `<label>` 包隐藏 `<input type=file>`——看着像键、
  语义不是键（键盘/SR 读出来是文件控件），而「导入产物」也没说清导入的是**云机跑出来的训练结果**
  （权重/opt/指标），不是一份配置。
* **「恢复在线」→「切换成在线」**（课程矩阵 hub 侧开关）：原词与**暂停**那个开关的「恢复」撞车
  （两个开关并列在同一列，且都叫「恢复」），改成与「切离线」成对的叫法。
* **任务包键的可见性放宽**（用户指令）：「导出任务包 / 导入训练结果」原判据只看 **hub 事实**
  （`ov.offline`）——那形成死锁：离线课**需要包**才能上云跑，而键却要等 hub 先接受离线模式才
  出现；回灌失配时（hub 仍当它在线，见 `modeDrift`）那个键正好不见了——**最需要它的一刻**。
  现判据 = `hub 标离线 ∨ 意图离线`（纯函数层新字段 `CourseMatrixRow.bundleOps`；两个源都离线时
  仍只有一个键，明确在线的课不给）。
* 门禁：dashboard `1121 passed / 0 fail`（新增：导出键无 `<a>`/`/api/taskBundle?` 链接、导入是真
  `<button>` 且文案、SSR 显「未导出」、任务包键只给离线课、`bundleOps` 五态（含失配时给、
  明确在线不给）、漂移徽标上屏/不上屏、hub 开关新文案）。

### 门禁

`dashboard`：`bun run typecheck` + `bun run test` **1112 passed / 0 fail**（新增：pill 的「等云机」
与「意图未生效」两例、`modeDriftOf` 四例、「有界重试只对该类错误 + 连不上不重试」两例）。

---

## §13 云腿 eval 在表上看不见：读方只认 `eval_summary`，而两腿都没并它（2026-09-23）

> 全文（根因 / 修法 / 单调规则 / 为什么不在读方合成）在
> `docs/nn/remote-transport.md §35`——控制台侧只记这条链的读方事实。

用户实测：`x20-demo-mix` 云腿 **it50–110、每 5 轮 400 局、`node=cloud`** 的读数全在
`tmp/<课>/eval_log.jsonl` 里（`wver` 逐轮不同、轨迹连贯），控制台**一栏不显示**。

根因不在读方（读方没错），在写方：数据是**逐局行在、summary 行缺**，而本文件涉及的四个读方
全部**只建 summary 条目**：

| 读方 | 位置 | 建条目的条件 |
|---|---|---|
| 指标表 eval 列 / 配对基准 / 衍生列（`avgTicks` 等） | `server/iters.ts::readEvalSummaries` | `event === 'eval_summary'` |
| eval 逐局弹窗 | `iters.ts::readLatestEvalGames` | 先按 summary 找最大 iter |
| 开课回执「起点-基线对照」 | `stack/kickstart-receipt.ts` | 同 |
| 门判据趋势（python 侧） | `rl/gate_check.py::read_trend_rows` | 同 |

⇒ 修在写方（回传/导入合并时把 summary 一并并进去，单调规则），**读方一字未动**。

---
## §12 两腿同字段收口：`demo_bc`/`kickstart` 进搬运表 + 逐局画像改取**单局 manifest**（2026-09-23）

承 §10。两个都是「同一张表两条腿不可比」的缺口，都在**数据源**上，不是显示层。

### ① 搬运表漏了 `agg.kickstart` / `agg.demo_bc`（用户发现「demo_bc 全缺」）

产物行的 `agg` 里**一直有**这两个键（`remote/worker.py` 的 `result.agg`），只是
`remote/artifacts.LEDGER_FIELDS` 没收 ⇒ 回传/导入腿的 `iteration` 行永远看不到 demo 与
缰绳遥测，而本机腿（`rl/events.write_iteration`）逐轮都写。**不是没跑，是记丢了。**
修法一行两键；口径不变：`_dig` 给 None 就不写（旧包无此键 → 留空），真 0 照写。

### ② 「耗时/击杀/残血/道具」四列的逐局画像取错了源（**四列永远空**的真因）

链路上游：`readRoundActuals`（`iters.ts`）优先读 `<traj>/it<N>/per-game.json`，而那个文件由落地方
按 `row.perGame` 写。实测 `x20-demo-mix` 的 `remote-jobs/offline/a477f…/it-124/row.json`：

```
report = {games, shards, winRate, totalSamples, totalTicks, elapsedSec,
          outcomes, dimMeans, scoreStats}        ← 没有 perGame
find tmp -name per-game.json → 0 个
```

根因：`iter_rollout` 当时喂给 `compact_per_game` 的是 `collect_reports` = 每局的
**`_rl_report.json`（批次摘要）**——它的 `stage`/`seed` 是**复数数组** `stages`/`seeds`、
没有 `kills` 这些单局字段 ⇒ `compact_per_game` 的「无 (stage,seed) 就丢」把**每一行**都丢掉
⇒ `perGame` 恒 `[]` ⇒ 落地方不写 `per-game.json` ⇒ 四列**从没进过回传体**。

修法：新增 `iter_rollout.collect_shard_manifests(shard_dirs)`，逐局画像改从 **shard 目录的单局
`manifest.json`** 生成——那是唯一带齐那套字段、且与读方/本机腿（读方按 manifest 扫描）**同名同形**
的来源（无需翻译层）。`scan_shard_dirs` 已保证每个 shard 目录都有它；真缺了只少一局读数并计数。
轮末日志加 `…s｜逐局画像 N/M 行`（0 行时响亮提示）。

**留白**：已落地的旧轮（如 it-048…124）`row.json` 里根本没有 `perGame` ⇒ 这四列对它们**仍然空**
（刻意不写 0：缺数据与真零不是一回事）；重打包后的新轮才有。

**验证**：`test_deliver_zip`（两键搬进 + 缺键不留 0）、`test_remote_iter`（单局 manifest 收得下、
四列原始字段齐、批次摘要的反例、缺/坏 manifest 只少一局）、dashboard `server-iters-round-actuals`
（读方聚合）。提交 `7130f880`。

---
## §10 回传腿也点亮控制台（同步写 per-game.json + 课程账本行）（2026-09-22）

用户之问（2026-09-22）：「**如果是云机通过网络请求回传，会算这些数据回显吗？**」——
之前**不会**，而且这件事最刺眼的地方是**两条腿的观测面不一致**：人工导入能修（`docs/nn/remote-transport.md` §27④），
实时回传不能。

### 缺口的确切形状

回传（`POST /offline/artifact`）此前只做三件事：落 `offline/<run>/it-NNN/{weights,opt,row}.json`、
往**本课程**账本追一条 `offline_artifact` 事件、回 200。而控制台那张表只读 `iteration` 事件
（`state-view.ts` → `iters.readIterMetrics`），「耗时/击杀/残血/道具」四列更是**逐局**聚合的
（`readRoundActuals`）——单局 manifest 只在跑它的那台机器上，云机轮末 `prune` 就删了 ⇒
**权重在岸、末轮能评，但表一行不显、四列恒空**。

### 修法：两条腿共用同一张翻译表 + 同一个落点

* `remote/artifacts.ledger_row_from_metrics(row, run_id=…, source=…)`（新增，唯一翻译实现）：
  产物账本行 → 课程账本 `iteration` 事件。`run_id` = 哪条腿/哪个包，`source` = 谁搬的
  （`deliver_import` / `offline_backfeed`）——复盘时一眼分辨曲线从哪来。搬不到的字段**留空**
  （写 0 会被读成「真的零击杀」）。
* `remote/artifacts.metrics_row` 增补（additive，~17 float/轮）：`report.dimMeans` /
  `report.scoreStats`（kills/accuracy/loot 与 score 列的来源）+ `report.perGame`
  （`rl/reports.compact_per_game`，~200B/局）。**不落课程账本**（否则每轮 +65KB），
  由落地方写成 `it<N>/per-game.json`。
* 落地方两处：`remote/deliver_zip.write_per_game_files`（导入）+ `hub_server::_land_round_metrics`
  （实时回传）。**幂等**：账户行同 `it` 已存在就不写；`duplicate` 投递根本走不到（只在接新时写）
  ⇒ 同一轮不会出现两行（读方按 it 画曲线，两行=曲线打结）。
* 读方 `dashboard/src/server/iters.ts`：`readRoundActuals` = **`it<N>/per-game.json` 优先**，
  缺席才回落到 `manifest.json` 目录扫描；两路喂同一个 `aggregateActuals`。
  `ACTUALS_SCHEMA_V` 3→4（旧缓存里的 manifest 值不得压住新落盘的画像文件）。

### 验证

* python：`tests/test_multi_course_hub.py::test_offline_backfeed_moves_the_console_table`
  （回传一轮 ⇒ `it3/per-game.json` 逐字节等于 `row.perGame`、账本恰一行 `iteration`、
  字段搬运 `winRate/samples/expectedGames/ticks/rollout_sec/ppo_sec/kl/dim_means/score_mean`、
  重复投递仍是 `duplicate` 且不写第二行）；`tests/test_deliver_zip.py` 同规（来源标记键从
  `import_run_id` 改为共享表的 `run_id`+`source`）。
* dashboard：`tests/server-iters-round-actuals.test.ts` 5 例（只有 per-game.json 也能算四列 /
  (stage,seed) 取样本多者 / 缺席回落 manifest / 坏 JSON 与非法行跳过 / `readIterMetrics` 行带
  `actuals`）。
* 门禁：`nn-python-gate` **2129 passed** 绿、根 `bun run check` 绿、dashboard
  `typecheck+test` **1108 passed** 绿。

### 留白（不假装完整）

**已经打出来的旧包**（如 19:27 那份 `deliver-x20-demo-mix.zip`）里没有 `dimMeans`/`scoreStats`/
`perGame` ⇒ 那份包的四列与 kills/accuracy/loot/score 仍是空（刻意不编数字）；**新打的包**
（点「训练」重打包之后的每一轮）才会带上。回传腿同理：只有**新代码**跑出来的轮才有 `perGame`。

---

## §9 控制台：开课回执的「起点-基线对照行」（accident.plan §5.3，2026-09-21）

**要治的那件事**：C 双臂从收敛权重（it175）以 `kk(1)=1` 满额缰绳复活，it1 kl=0.90、锚主导
更新连烧 30 轮；两臂 ×12h 只换来「无结论」。开课前**盘上就已经有**的那个事实——「本腿恢复的
权重已经贴着课程 bc 权重那一档（甚至更低），还要拿满额锚去拉」——当时没人被告知：训练侧第五轮
已把这一行打进 `out.log`，但操作员的动作是「夜里点开课就走」。本轮回执把它摆在开课那一下。

**落地**（控制台侧，`dashboard/`）：新模块 `stack/kickstart-receipt.ts`（纯函数组装 + 轻量账本
读）→ `server/actions/course-lifecycle.ts::openCourse` 的 `detail`。读数与阈值全与执行面同源：
基线 = `tmp/<课>/eval_log.jsonl` 文件序末条 it0 行的 winRate（同 `kickstart_burn.baseline_reading`），
起点 = 末条 it>0 行；噪声带/点数走 `courses.<课>.kickstart_burn.{margin_pp,points}`（同
`kickstart_burn.burn_overrides`）。★ 两支：起点低于基线 > 噪声带（熔断从第一个点起算）/
起点在噪声带内且 kk ≥ 0.5（满额锚先把它拉回去，C 事故的那种配置）。

**lesson**：
- **镜像常量必须对着 python 源码核对**（单测在 python 源码树里**搜定义**取字面量——不写死
  文件路径：S4 第十九刀实测，写死 `loop_core.py` 的那版在常量搬到 `rl/loop_lifecycle.py` 后就静默
  空转了）：TS 侧抄一份阈值是不可避免的（控制台不能 import python），但抄完不设闸 = 两处判据
  各自安好、对不上号（与 §2/A 的 course_fp/corpus_fp 同一个病的预防）。
- **同一份账本两个读法要当面对账**：控制台的 `readEvalSummaries` 全量 parse（视图构建用），
  回执读法按 `"eval_summary"` 子串预滤（18MB 账本、开课那一下）——单测钉「两者同数」，
  免得快的那份悄悄漂成另一种口径。
- 真盘实测：x20-clutch 差 -6.0pp（起点已低于基线）、x20-steady +2.0pp、x20-clutch-null -3.8pp
  ——三门全是「配 kk=1 会先变差」的配置，回执现在会当场喊。

**门禁**：`cd dashboard && bun run typecheck && bun run test` = **991 pass**（新增
`tests/kickstart-receipt.test.ts` 23 + `course-lifecycle.test.ts` 集成 1）；根 `bun run check` 绿。

---

## §8 本机伪 GPU 节点退出控制台：只剩冒烟预演自起自停（2026-09-19）

**一句话**：`workerServe`（`remote_worker_serve`）不再是被受管组件——没有卡片、没有账本键、
没有日志页入口、没有端口兜底清场、没有「回落本机」的 push 预设；它此后只是 trainingLoop
冒烟预演的一次性配角（`stack/push.ts` 起，跑完/失败即杀）。

用户口径：「workerServe 伪节点直接从 dashboard 去掉，**它只是用于 trainingloop 冒烟测试**，
用户只关心冒烟是否通过，不会手动去开启/停止伪节点。」

### 为什么这不是「换个轴」而是「退出去」

R3-6 曾把它的收敛列为「节点轴」(否决④) 的延后项。但它的语义轴其实是**冒烟预演的临时件**：
生命周期 20s、一次预演一份、与任何 GPU 身份无关。给它造节点轴账本 = 给一个不该常驻的东西
一个常驻身份（而它常驻的唯一后果是把「服务面 · 单例」卡行多塞一个用户不想要的开关）。

### 删了什么（受管面，逐处）

| 处 | 内容 |
|---|---|
| `core/types.ts` | `Component` 键、`Registry.workerServes`、`LegacyFlatRegistry` 项 |
| `core/registry.ts` | `COURSE_COMPONENTS`/`PLURAL`/迁移名单里的 `workerServe`（旧 `workerServes` 表已无读者） |
| `stack/specs.ts` | `WORKER_SERVE_ENTRY` + `workerServeSpec`（ProcSpec 整个删掉） |
| `server/actions/start.ts` | `case 'workerServe'`（含幂等/端口兜底/登记） |
| `server/actions/stop.ts` | 端口映射表里的 push 端口项 |
| `server/actions/smoke.ts` | 冒烟分支 |
| `server/actions/restart.ts` | `restartSpecFor` 分支 |
| `server/actions/labels.ts` · `server/api/component-meta.ts`（`ALL_COMPONENTS`/日志/健康端口）· `server/api/logs.ts`（`LOG_NAME_MATCH`） | 展示与解析面 |
| `web/view/component-groups.ts` | `NODE_FACE_COMPONENTS` 例外名单删除（它存在的唯一理由就是这个键）；`ComponentFamilyId` 回两族 |
| `web/app/panels/LogNavCard.tsx` | 不再 filter（受管组件全集 = 日志页入口全集） |

### 删了什么（能力面）：「一键本机 push」

`applyLocalPushNodeConfig` / `localPushUrl` / `configurePushEndpoint` 的 `allowLocal` opt-in /
`findHealthyGpuPushNode` 的 `includeLocal` / `PushTarget.viaLocalWorker` / `preset.ts` 的
`['selfNode','workerServe','trainingLoop']` 顺序分支。理由：执行面解析在 2026-09-15 就把
「缺 gpu_push → 自动回落本机」改成了**响亮报错**（回落会把「云机连不上」伪装成「训练正常」），
剩下的 opt-in 只服务单测；而伪节点不再是受管组件之后，这条路径唯一的效果就是**把课程 push
目标指向一条没人服务的本机地址**——留着即静默失败通道。`preset.ts` 也从此**到不了** `local`
来源（类型上只剩 manual/config 两档）。

### 保留了什么（读面识别 + 冒烟）

- `NodeConf.local_push` 标记与 `pushTargetFromConfig` 的 `kind: 'local'`：历史配置里残留的本机
  条目与指它的 `courses.<课>.push_node_url` 必须**看得见**（看得见的坏过静默的）；复用扫描
  一律排除它（没有 opt-in 了）。
- 冒烟预习侧 `stack/push.ts`：直接 `spawnBg`（不再造 ProcSpec），仍按课程取槽位 push 端口与
  per-course work 目录（**双课同冒**不得互踩），20s 未就绪则**先杀掉自己起的进程**再抛错
  （旧版对 spawn 后的 `launchSpec` 失败没有兜底，会留一个没人认领的孤儿）。
- 训练侧只改一句引导日志：`remote/worker_server.py` 的 `CodeChangedError` 指引从「重起
  workerServe」改成「重跑 `remote_worker_serve`」。

### 回归（三把防回流尺子）

`tests/push-config.test.ts`：原「本机回落」describe 重写为「没有写入口，只有识别面」——
① **受管组件全集**（`ALL_COMPONENTS`/`COURSE_COMPONENTS`/`registryTriples`）里没有它；
② **启动面与 spec 面**（start/preset/smoke/specs 源码）里没有它，且 `preset.ts` 的 push 顺序
只剩 `['selfNode','trainingLoop']`；③ **冒烟侧确实自起自停**（`stack/push.ts` 里有
`remote_worker_serve` 与 `killPid`）。另：`server-api-logs`（组件日志映射改 5 个）·
`server-api-state-view`（快照 5 组件）· `single-hub-tunnel`（所有课程组件的槽都归 `''`）·
`training-multi-course`（伪节点仍按课程隔离端口/work，但不再是 spec）· `web-component-groups`
（全组件=卡行全集）· `web-components`（SSR 去掉节点面例外）· `training-port-reclaim`（`ownsResource`
只剩 hub/隧道）同步真值。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest xdist） | ✔ 全绿 |
| `cd dashboard && bun run typecheck` / `bun run test` | ✔ 干净 / **651 pass / 0 fail** |
| 三份 bundle（`bun dashboard/src/server/build.ts`） | ✔ all bundles ok |
| 根 `bun run check` | ✔ **1868（1864 pass / 4 skip / 0 fail）** |

记录：`DECISIONS.md §2026-09-19-goalnn-retire-local-fake-node` · plan §5.2 R3-7 · memory。

---

---

## §7 R3-3：组件卡分族（服务面单例角色 vs 课程面按课程）（2026-09-19）

**一句话**：六个受管组件不再排成一行——按**作用域**分成「服务面 · 单例」（selfNode / hub / 隧道，
与课程数量无关）与「课程面 · 按课程」（trainer / 本机 worker，对象 = 当前查看的那门课）。

### 之前错在哪

`<b>共享</b>` 一个布尔徽章 + 操作员记忆，是区分「全机一份 / 一个进程服务所有课程 / 按课键控」
的全部信息。两条真会发生的误读：① hub/隧道已单例（§2026-09-18），卡片却仍按「当前查看的课」
渲染 —— 有人会给这门课**再起一个 hub**（第二个实例抢同一端口）；② trainer 卡片看着像全局对象，
按下去起的却是**当前查看的那门课**（换课程 = 换对象，视觉上零提示）。

### 形状

```
服务面 · 单例   [selfNode 单例] [hubServer 共享] [cloudflared 共享]  │  课程面 · 按课程  [trainingLoop] [localWorker]
```

族标题带悬停说明（「与课程数量无关：一个进程服务所有并行课程（0 门课也在，100 门课也只有一份）」/
「卡片上的对象是当前查看的那门课」）。

### 四个决定

1. **作用域三态单点在 `core/registry.ts::componentScope(key)`**（`singleton` / `shared` / `course`，
   就是既有两张槽位表的另一个面），服务端算一次填进 `ComponentView.scope`（取代 `shared` 布尔）。
2. **族归属 = scope 的函数，视图层不写 key 名单**：名单一旦与槽位规则漂开，症状是某个组件从 UI 上
   **消失**（而它照样被启动、被监督、被冒烟）。族内顺序才是化妆（`ORDER`；未列出的落组尾**不丢**）。
3. **节点面例外声明成数据**（`NODE_FACE_COMPONENTS = ['workerServe']`）：`worker_server` 的语义轴是
   节点/GPU 身份（hub 的 push 派发与竞速也按节点算），渲染在节点行；不再用面板里一行 `filter` 静默
   过滤（读代码的人只看到一行 filter、不知所为何来）。`LogNavCard` 复用同一常量。
4. **`scope` 缺省/未知 ⇒ 按 `course` 渲染**（单侧保守，与调度器卡片的 `kind` 同一条规矩）：少一个
   徽章只是少信息；凭空空贴「共享」会让操作员以为「停它就是停全局」（实际只停本课）。
   徽章只标 scope 说不出来的那件事：共享 / 单例；按课程 **无徽章**（默认语义，每行再挂就是噪声）。

### 回归（两把尺子：全组件恰好归属一处 + 与 registry 对拍）

`tests/web-component-groups.test.ts`(11)：分族与族内顺序（乱序输入）/ 节点面例外是真组件 /
**每个 `ALL_COMPONENTS` 的 key 要么在卡片行要么在例外名单里**（新增组件忘了归档 ⇒ 红）/
**族归属只能是 `componentScope` 的函数**（两份判据不许漂）/ `scope` 缺省保守 / 未列出的 key 落组尾不丢 /
空组不渲染 / 不改动调用方数组（原地 sort 会让上游快照顺序随渲染变化）/ 徽章三态。
`tests/web-components.test.ts` 的分族 SSR 断言（两组标题 + `data-family` + 族内顺序 + 共享×2/单例×1 +
节点面组件不在卡行）。

### 踩到的坑（构建期，值得记）

客户端代码里写**未加引号的 `node:` 对象键**（`Record<ComponentFamilyId, …>` 里的那个 `node: []`）
会让三份 bundle 全红 —— `server/build.ts` 的禁词门禁把 `node:` 当「引入了 node 内置模块」。
修法：只给**会渲染成组**的两族留元数据（`FAMILY_META: Record<'service' | 'course', …>`），
`'node'` 只作为词汇存在；`component-groups.ts` 的 `ComponentFamilyId` 注释里写明了这条。

### 未做（明确记录）

账本键的**真正收敛**：`trainingLoop` / `localWorker` 仍是 per-course 键（控制台仍按课起
`run_rl.py --course`，尽管训练侧已有 `--serve` 单进程服务所有课程），`workerServe` 仍住 per-course 表
（轴却是节点）。那是启动面/监督面的改动（含 `TrainLaunchModal` 精简与旧条目换代接管）；本轮的分族
正是它的前置——换成共享槽后，进程面全在服务面、课程面只剩数据。

---

---

## §6 R3-4 控制台半：BC 课与 RL 课在同一张调度器卡片里并列（2026-09-19）

**一句话**：`LoopQueue`（训练调度器卡）不再被「当前查看的是不是 BC 课」门控，BC 行与 RL 行
**并列**展示；行上带课程种类，于是「在等哪个 GPU job 回传」这件事对两类课程都答得出来。

### 之前错在哪（不是「少个功能」）

`app.tsx` 里那张卡是 `stateView?.isBc ? null : (…<LoopQueue/>…)` —— 而 `isBc` 说的是
**当前查看的那一门课**。用「查看对象的属性」门控「跨课程列表」是范畴错误，后果很具体：
**选中一门 BC 课 ⇒ 整张调度器卡片从页面上消失**，于是 BC 课在调度器视图里根本不存在。
而 BC 课恰恰是最需要这张卡的那类（它的全部墙钟都在「等 GPU 回传」，而这句话只有这张卡说）。

### 三个决定

1. **行上带课程种类**（python `build_rows` 的 `kind` ← `loop_plan.course_kind`，判据 =
   `curricula/<课>.bc.jsonc` 是否存在，与控制台 `isBcCourse` 同源；视图层 `LoopCourseKind`）。
   不带种类 UI 只能猜，而猜错的方式很坏：把 BC 读成 RL 会得到一整排**看着像真的**零
   （门禁 verdict / KL / 连击那些字段在 BC 账本上永远不存在）。
2. **`kind` 缺省/未知一律按 `rl` 渲染**（保守方向是**单侧**的）：python 比控制台旧（还没这个
   字段）时，少一个徽标只是少信息；反过来凭空空贴 BC 标签，会对外宣称「一轮 = 一个任务」
   （而它其实有 13 步）——**假承诺比缺标签贵**。`'BC'` 这种大小写不符同样不认。
3. **文案按种类分叉**（`kindBadge` / `stepTitle` / `pendingTitle` 三个纯函数）：BC 行说
   「一轮 = 一个任务：采集语料 → 发布 job → 等 GPU 回传 → 落位归档」并点名指标在
   `bc_epoch`/`bc_eval` 事件里；RL 行保持「待办 N 步（顺序即依赖顺序）」与 13 步原样。
   BC 行的悬停里**不出现** verdict / KL / 门禁字眼（那些它没有）。

### 顺手改的展示面

`run_rl_cluster.py` 的人读表加了 `kind` 列；BC 行的 facts 行不再摆 `iterations=/last_verdict=`
那一排 RL 的零，改成「bc 课程（轮指针 itN）；指标看账本 bc_epoch / bc_eval 事件」。

### 回归

`tests/test_loop_plan_bc_rows.py`(9，读面：种类 / 指针跟 `bc_round_completed` / 在飞来自
`job_pending` / 作废也是终局 / manifest 缺失不丢在飞 / 未知课程默认 `rl` / 两行共存 / 人读表带列)
· `dashboard/tests/web-app-loopqueue.test.ts`(+3：BC 行与 RL 行并列渲染且只有一行带 BC 徽标 /
两边悬停各说各的 / 卡片不再被 `isBc` 门控——用正则断言那个包裹形状不再存在)
· `dashboard/tests/server-api-loop-queue.test.ts`(+1：`kind` 的保守默认)。

### 踩到的坑

`build_rows` 里局部变量 `kind` 一度既是「等待种类」又是「课程种类」，赋值顺序决定了行里
那个 `kind` 到底是哪一个（症状：控制台把 BC 课标成 `ready`）。改成 `wait_kind` / `kind`
两个名字，并在视图解析器里同样改名（`parseLoopQueue` 里等待种类叫 `waitKind`）。

### 未做

BC 行的**暂停/恢复**能点（控制文件按课程名，与种类无关），但 BC 的指标面板仍是独立的；
真机「一个 serve 进程带 BC + RL」的实跑仍待做（与 R2e 同口径）。

---

---

## §5 R2c-3 余下：调度器进控制台（单例卡片 + 每课队列视图 + 在等什么）（2026-09-19）

R2a/R2b/R2c 把单进程调度器造出来了，但**没人看得见它**：`trainingLoop` 收敛为一个进程之后，
「某门课这一轮为什么还没走完」要翻 N 份日志 + 猜（`run_rl_cluster.py` 原本只给终端看）。
本节把它接进控制台——只读侧，不动训练行为。

### ① 数据源：走 python 只读入口，**不在 TS 重算判据**

`run_rl_cluster.py --json` 的输出就是控制台卡片的契约面（训练侧只读：不训练、不发布、不等待）。
判据（指针 → `RoundFacts` → `pending_tasks`）与 CLI 表逐字段同源；若在 TS 里照账本重写一遍，
就是第二份真相（R2b 否决 `loop-state.json` 的同一条理由）——两边会以不同速度演化。

- `dashboard/src/server/api/loop-queue.ts`：懒算 + **TTL 10s** + 单飞。TTL 比 hub 观测面的 5s 宽，
  因为事实变化的粒度是「一轮」（分钟级），而一次冷算要起一个 python（解释器冷启 + 扫 N 课账本/
  journal/shard 目录，亚秒级）。服务器启动时暖一次缓存，避免 SSR 首屏等子进程。
- `LoopQueueRunner` 是**可注入接缝**（与 `deliver_zip` 导入同惯例）：控制台这一半（argv 形状 /
  结果翻译 / TTL / 单飞 / 与在训事实合并）在 dashboard 用例里全测到，python 那一半由
  `nn-training/tests/test_loop_plan_waiting.py` 钉死——两边各测自己那一半，中间不再叠一层端到端。
- 读失败（解释器缺失 / 超时 / 输出不可解析 / 形状不符）**不抛**：视图带 `error` 上屏，UI 显因 + 空态。
  观测面坏掉不该把整页 `/api/state` 带崩（与 hub 总览 / 隧道 A/B 同口径）。

### ② 「在等什么」的判据留在 python（`loop_plan.waiting_state`）

控制台那一列不是 TS 从 facts 推的散文，而是 python 单点算出的 `(kind, text)`：

| kind | 何时 | 文案 |
|---|---|---|
| `inflight` | 有已发布未回传的 job（`commit_journal.inflight()`） | `等远端回传：ppo@37（jid=… via push）`；多条 → `等 N 个远端任务回传（…）` |
| `collect` | 本轮已落一部分局、配额未满（或未知） | `等采集落盘（120/150 局）` / `采集中：已落 78 局` |
| `idle` | 本轮无待办（账本已结算 / 未开训） | `本轮无待办（账本已结算 / 未开训）` |
| `ready` | 无外部等待 | `无外部等待，下一步 ppo` |

优先级 inflight > collect > idle > ready：**进程外的等待排第一**（结果在别的进程/机器上，
运维唯一能干预的那一类）。

★ **`games_planned` 的诚实性**：盘上今天**没有**任何地方记「本轮计划多少局」（只有 `rl/plan.py`
的课程计划知道）⇒ CLI 传 0 = 未知，此时 `collect` 只报已落局数、**不报分数**（绝不出现 `78/0`）。
真 supervisor 带上课程计划后会走到带分母的文案。这与 `already_done` 同一条规矩：算不出来的事实
不得当成完成，也不得编出分母。

### ③ 卡片：单例 + 两个事实源逐行合并

`dashboard/src/web/app/panels/LoopQueue.tsx`（RL 区，BC 课不出）——表头「调度器 · **单例** ·
在训 N/M · N 课等回传 · 排队等资源 X · 池占用」，每课一行：课程 / 在训-未在训 / it / 下一步 /
待办深度 / **在等什么**（四态各自一个色阶）。点行 = 切到查看该课（与总览卡同一条路径，LAN 只读
下也可用：只改本浏览器的查看目标）。

- **在训与否来自 registry**（`trainingLoop` 进程存活），不是 python 给的：盘上事实看不出「进程还在
  不在」。缺了它，一门停了的课会被读成「等外部」——所以未在训的行淡一档 + 悬停说明「这是盘上事实
  推出的队列状态」。
- 合并放在视图层（`withTraining` 纯函数）：python 说「卡在哪」，registry 说「有没有人在跑」。
- 与「并行课程总览」分工：总览回答 hub 侧「谁在派活 / 谁离线」，本卡回答训练侧「这一轮卡在哪一步」。

### 门禁

- nn python gate：**1446 passed / 3 skipped**（ruff + mypy 干净；+14 新例）
- dashboard：typecheck 绿、**567 passed**（+25：`server-api-loop-queue.test.ts` 13 / `web-app-loopqueue.test.ts` 12）、
  `bun dashboard/src/server/build.ts` 三份 bundle 通过
- 根 `bun run check` 绿、`bun run build` 绿

### 记录

`DECISIONS.md`（R2 条目新增「控制台接线」段，含「不得在 TS 重算判据」的防再犯条款）、
`plan/r2-loop-task-queue.md`（相位表）。**仍未做**：`checkpointCacheMb` 真机 RSS 实测表（R2c 上线
前置条件）、R2d 操作面（入队 / 暂停 / 单例 trainingLoop 卡片）、R2e e2e。

---

## §4 控制台 worker 登记入口 + 面板重组（P1 余下）：课程 select 解放 / 在训课程全高亮 / 并行总览（2026-09-18）

用户指令：控制台 worker 登记入口 UI + 面板重组。抉择与代价全文见
`DECISIONS.md §2026-09-18-goalnn-console-worker-register-and-overview · 全文 → 本文件 §11`，这里只记落地与实测。

**形状：登记是配置编辑器，总览是 hub 观测面的读方。**

- **worker 登记**（`/v1` 无关，纯 rl-config）：面板只 upsert `nodes[]` 里的 `gpu_push` 条目
  （hub 按 mtime 热重载），写完 best-effort `POST /admin/push-workers {action:"reload"}`；
  **ping 不通不拦登记**（云机没开机是常态），返回值如实分流 `ok:true`/`ok:false`。
  改 url 或删节点时把 `courses.<课>.push_node_url` 一并改写/清除——留着就变成「指向不存在
  的 URL」⇒ python 匹配 0 个节点后**静默回落 pull**。移除一个 rollout 节点（非 gpu_push）
  会被 409 拒（不把采集节点改写成 push worker）。
- **两列探活刻意分开**：面板直探 `{url}/ping`（此刻这台通不通）与 hub 周期探活结论
  （调度器认不认它在线的唯一判据）各占一列；两列不一致本身就是信号（面板通而 hub 判离线
  ⇒ hub 还没重载配置）。
- **课程 select 解锁**：旧锁定（§367，hub 运行中不许切课）的前提已被「单 hub 托管 N 份账本」
  推翻，多课程并行下它只会把查看/切换锁死。现在恒可切（含历史课程），只改「看哪门课」。
- **在训课程 = registry 里 `trainingLoop` 存活**（服务端 stamp `trainingCourses`）：课程 select
  里**每一门**在训课都带 🔥（不是只标第一门），查看的课之外的用「正在训练：…」标签提示。
- **并行总览**（新面板）：hub 行 = 应答基址 / 在派发课程数 / 活跃 worker 数 / 竞速 / 停机 /
  最近派发（轮转游标）；每课一行 = 在训 / 离线 / iter / 队列深度 / 在飞，点行即切查看。
  这一行回答「某门课为什么在饿着」的四种可能（不在 hub 课表 / 被标离线 / 有活但没空闲 worker /
  队列空），在日志里要靠猜。
- **每课 iter 只认账本里的 `iteration` 事件**（`training_log.jsonl` 是多写者文件：训练侧写
  iteration，hub 追加 job_completed/job_failed；把 `job_completed.it` 当轮次会读出还没跑完的那一轮）。
- **hub 基址不新增配置键**：按 registry 里活着的 hub 条目逐个试 `/admin/queue`，第一个应答者即
  观测源 ⇒ 单 hub 多课与每课一 hub 两种形状都读得对；都没有 → 「hub 无应答」，不编地址。
- **容错与预算**：观测面任何失败（无 hub / 401 / 坏 JSON / 账本不可读）→ null/空态，绝不把
  `/api/state` 带崩；总览与登记表**共用一次** hub 探测（5s TTL + 单飞），冷算里 hub 探测与
  worker 直探并行开跑（串行会到 3×超时）。

**落地**：`dashboard/src/web/view/course-overview.ts`、`src/stack/hub-admin.ts`、
`src/server/api/overview.ts`、`src/server/actions/workers.ts`、`src/web/app/panels/{CourseOverview,WorkerRegistry}.tsx`
（新）；`src/server/api/{route,state-view,index}.ts`、`src/server/actions/index.ts`、
`src/web/view/{index,console-types}.ts`、`src/web/app/app.tsx`、`src/web/theme.css`（改）。

**门禁**：dashboard typecheck / test **533 pass**（+41 例：overview 12 + 登记动作 16 + 面板 SSR 12
+ 课程 select/在训高亮改写 2 + 原有用例不变）/ lint 0 warning / build:ui 三份 bundle 均过；
根 `bun run check` 绿。

**两个实测踩点（都留了回归）**：
1. **`parseHubQueue` 的宽容解析不是装饰**：hub 是独立进程，可能比控制台新/旧一个版本——缺字段
   退化为 0/空比让整页 500 好得多；用例里专门断言「字段全缺 → 全零/缺省」。
2. **回环不可达地址会挂满直探超时**：测试里用 `http://127.0.0.1:1` 当「探过、不通」会让每条用例
   白等 1.5s（本环境对回环拒绝连接是 **DROP** 而非 RST）——改用「401 的假 worker_server」拿到
   即时确定结论，用例耗时从 9.2s 降到 0.3s。这条写进了测试注释，避免后人再踩。

**P1 余下**：① 单隧道——hub / cloudflared / trainingLoop 收敛为单例（现在仍 per-course 拉起），
随之把组件卡片拆成「单例角色」与「按课程」两种形状；② 多课程单 hub 的端到端 e2e
（训练侧 hubpush → hub → 真 worker_server + 假 PPO）。

---

## §3 控制台两条腿：导出任务包 / 导入产物即评估（2026-09-17）

**为什么记这一笔**：把 `docs/nn/remote-transport.md` §10–`docs/nn/remote-transport.md` §12 的离线能力**接到人手上**——之前「导出任务包 / 导回产物」
只有命令行，而这条链的每一步（导出前要不要先停训练、包对不对得上课程、评估跑哪一轮）
都是**容易做错且错了不报错**的地方。决策与否决见
`DECISIONS.md §2026-09-17-goalnn-console-task-bundle-exchange · 全文 → 本文件 §11`。

```
导出（课程 + 起点权重 + 代码快照 = 整段剩余）
  面板「导出任务包」→ POST /api/exportTaskBundle（回环门控之后）
    ├ 门① 训练在跑？（run_rl 锁的 PID 活着）→ 拒启，说明「包里的起点就是当前进度」
    ├ 门② 没有 tmp/<课>/weights.json → 拒启（包必须有起点）
    └ 起 `run_rl.py --course <课> --ppo remote --run-iters -1 --export-bundle <abs>`
         （detach，日志 logs/<课>/export-bundle.log；**不注册组件**——它是动作不是组件）
  面板按 2.5s 轮询产出文件 mtime（不猜进程）→ 出现即可「下载」（GET /api/taskBundle 流式）

导入（跑完的产物 + 自动按课程配置评估）
  面板选文件 → POST /api/deliverUpload（multipart，走 JSON 动作层之外）
    ├ TS 侧两门：文件名课程须与当前课程一致、≤512MB
    ├ 落 tmp/<课>/deliver-uploads/<ts>-<name>（留证，不覆盖历史）
    ├ python -m remote.deliver_zip --zip … --dest tmp/<课>/deliver --course <课>
    │    （三道门：zip-slip / 形状（拿错包要指明「这是任务包」）/ 课程对账；原子落地）
    └ 接着起 evalA（共享启动器与互斥键 eval:A）评**末轮**权重 → 读数回填指标表
```

**怎么用**：面板「任务包（离线交出去 / 收回来）」；导出 → 下载 `task-<课程>.zip` → Kaggle/Colab
上传跑完 → 把产物 zip 命名 `deliver-<课程>.zip` 选回去 → 自动评估。

**实测（本机可验的部分全跑了）**：
- 真导出一次（c6-chip，dashboard 的**同一 argv**）：`task-c6-chip.zip` **3.3 MB**，`it16 → it60`
  45 轮，包内 `code.zip` 237 文件 / `ts_code.zip` 343 文件，逐件 sha 进 `task.json`。
- 门禁：nn python gate **1187 passed**（新增 5+7）· dashboard typecheck + **485 passed / 0 fail**
  + 三份 bundle ok · 根 `bun run check` **1853 pass / 0 fail**。

**两个落地时抓到的真缺陷**（都有回归）：

1. **「导出互斥」最初是假的**：键在 HTTP 请求里 `add` 又立刻 `delete`（等于没锁）——第二次
   点击会起第二个 `run_rl` 去抢同一门课的锁。改成**子进程退出时释放**（轮询 pid；不用
   `busySince` 那套 5 分钟 TTL——导出合法地会跑过 5 分钟，TTL 会在中途解锁）。
2. **`--export-bundle` 被轮内 shard 门误杀**（**最贵的一个**）：导出既不发 job 也不训练，
   但 `_remote_ppo` 里那条「M3 上云轮 shard 集必须为空（否则双份采集）」的门照旧生效——
   拿任何**跑过一轮**的课导包（traj 下有历史残留 shard）都会 `SystemExit`，包产不出来。
   即「控制台上这个按钮对真课全程不可用」。修法：判定抽成纯函数 `_gate_round_shards`，
   `exporting` 时直接返回空集（`register=False` 是同一条思路：导出不参与发布语义），
   回归 `tests/test_export_shard_gate.py` 同时钉住「关掉 exporting，两条门照旧生效」。

**代理侧踩坑（与功能无关，但会再踩）**：python 一次性进程的 cwd **不是**你 `cd` 的目录
（trainer 内部会切），**手工验证时必须给绝对路径**；相对路径带 `..` 的写入在沙箱下还可能
被静默丢掉——第一次导出「日志说 4.1MB、盘上没有」就是我自己传了相对路径。

---

## §2 it0 基线评审修订：重试语义 + 账本双向隔离 + Hero NaN 行（2026-09-13 评审）

对 §1 的 staged 实现做评审后发现三处问题，本条为修复记录（评审 + 修复同一批完成）。

**问题与修法**

- **P1（鲁棒性）基线派发是「单次尝试 + 纯远端」**：原实现只在 `it == _start_it` 派一次且不传 `local_gate`——`EvalDispatcher` 无 gate 时不建权重快照、不启本地 worker（纯远端），若首派时刻节点瞬时全挂/权重 POST 全失败，只记日志跳过，进程内永不重试（docstring 的「失败重试轮」只有跨重启才成立）。改为**落账前每轮重试**：`baseline_summary_landed(traj_dir, wver16)`（eval_local）查 eval_log 是否已有**同 bc 指纹**的 `iter=0` summary，未落账则每轮 rollout 收官后重派，账本去重保证重试只补缺口；落账 wver 缓存于 `_baseline_landed_wver`（bc 换文件 → 新指纹 → 重派新基线）。同时复用当轮 `self._eval_gate`（与 A-eval 同一把门，`_join_eval` PPO 收官置位）——基线本地局与 A-eval 一样让位 PPO；快照文件按流分流（`_eval_frozen_weights-baseline.json`），基线与 A-eval 并发重试轮不互相覆写快照（覆写会让 A-eval 本地局读错权重）。summary 带 `dropped` 也算落账：缺口在控制台诚实显示「缺N」，不为填缺口无限重跑失败局。
- **P2（潜伏）账本互吞只防了单方向**：基线账本按 `iter==0` 隔离了「A-eval 行吞基线」，但 A-eval 账本仍无过滤——若 bc 与 it1 的 `args.out` 指纹偶同（手动拷贝 bc 为 `--out` 启动等路径），it1 中途崩溃重启后 A-eval 会被同 wver 的 it0 行整轮跳过。`eval_done_keys` 新增 `min_iter` 参数：A-eval 传 `min_iter=1`，把 int iter<1 的行挡在去重外；缺 `iter` 的旧行与同 wver 跨 iter 复用（零梯度轮 args.out 未变 → 下轮免重评）照旧保留。顺带发现 **B/C evalboard 行与 A-eval 同册同 `event:"eval"`**（batch_eval 写 `source:"B"/"C"`，畸形批的 iter 缺省还是 0）——基线账本额外排除带 `source` 字段的行，B/C 局不得被当成基线已评估。
- **P3（用户可见 UI）Hero 主表漏入 it0 合成行**：`MetricsTable.buildRows` 有 `iter>0` 守卫，但 Hero「最新 6 轮完整指标」是另一套渲染（`[...iters].sort(desc).slice(0,6)`），腿的前 ~5 轮 it0 必进前 6——合成行的 NaN 字段直接渲染成红色 "NaN%" 徽章、"NaN" 单元格（`fmtPct(NaN)` 只判 `typeof number`）。抽纯函数 `heroMainRows(iters)`（view.ts：过滤 iter>0、倒序、截 6）供 Hero 使用，测试三例钉死。
- **nit**：显式 `--start-it 0` 会与基线的 dist 键空间 `{runId}.0` 撞键（采集/A-eval 任务键 = `{runId}.{it}`）——`validate_args` 启动期拒绝 `<1`；Hero 配对裁判文案 `vs开腿itN` 在 baseIter=0 时改为 `vs bc基线`（it0 是训练前基准，不是开腿首轮）。

**验证**：`test_baseline_eval.py` 重写派发语义测试（落账前重试/在飞跳过/local_gate 复用钉死/落账停/bc 换文件重派）+ `eval_done_keys` 双向隔离（含 B/C source 行与缺 iter 旧行）+ `baseline_summary_landed` 直测 + `--start-it 0` 启动期拒绝；`console-paired.test.ts` 新增 `heroMainRows` 三例（it0 排除/截 6/端到端合成行进不了主表行集）。`bun run check` 绿（2028 pass）；nn-python-gate（ruff + mypy 145 文件 + pytest 全量）绿。

**遗留（未修，已评级）**：console `readEvalGameWins` 仍按 iter 合并 B/C 行（iter≥1 的既有口径，B/C 批恰与 A-eval 同 iter 时配对图混源）——影响面在 evalboard 触发路径，与基线无涉，留作后续单独处理。

---

---

## §1 it0 bc 权重基线评估：配对基准不再随 run 起点漂移（2026-09-12，用户指令）

**症状**：控制台的 in-loop eval 配对基准恒取 `eval_log.jsonl` 里**第一条** eval 行（`console/iters.ts` 的 `evalIters[0]`）。那条基准随 run 起点漂移：resume 时首条可能是 it50，于是 `vs开腿it50` 实际比的是中途两点，而不是「从起点学会了多少」。

**改法（纯增量，训练侧与 console 两侧）**

- **训练侧**（`rl/loop_baseline.py::TrainingBaseline._maybe_dispatch_baseline_eval`；S4 第二十刀前
  住 `loop_core.py`）：在本 run **首次 rollout 收官后**（`it == _start_it`；全新腿 = it1）立刻用课程 `args.bc` 派一条 `iter=0` 的干净评估作恒定基线。守卫 `_baseline_eval_weights`：per-tick / 有课程 / `eval_games_per_stage>0` / `eval_every>0` / 有 enabled dist 节点（`nodes=[]` 纯本地路径本就不派 A-eval）/ bc 文件在盘；幂等（在飞跳过 + 跨重启按 `iter==0` 去重）；**失败自吞**（基线是观测设施，不得拖垮主线）。课程默认 `eval_every>1`（c6-bonus=5）⇒ 该轮本无 A-eval，**零重复计算**。
- **`eval_done_keys` 新增 `iter_filter`**（`rl/eval_local.py`）：it0 的已评估账本按 `iter==0` 隔离。必须如此——bc 与 it1 的 `args.out` 指纹**可能相同**（it1 就是 PPO 前的 bc 初始化权重），只按 wver 去重会让 it1 的 A-eval 把基线局吞成「已评估」，it0 行永远不落盘。`dispatch_eval_round/bg` 与 `EvalDispatcher` 加尾参 `baseline=False`；it0 用独立 `iter_id = {runId}.0`（与 A-eval 的 `{runId}.N` 在 agent 结果缓存里键空间隔离）。
- **门判据排除 it0**（`gate_check.read_trend_rows`）：`iter <= 0` 的 summary 不进趋势（用户定案：只当监控/配对基线）。否则会虚增 sustain 的「连续通过」计数、把 plateau 的上升趋势起点拉回 PPO 前。缺 `iter` 字段的旧行照旧保留（不过度收口）。
- **Console**（`console/iters.ts`）：配对基线改为「有 it0 取 0，否则退回首个 eval 轮」（老腿逐字节兼容）；`readIterMetrics` 在有 ≥1 条真实 iteration 行时合成一条 it0 行（只有 `evalData`，rollout 派生字段一律 `NaN` —— 趋势图的缺口约定，写 0 会在图上多画一个假零点）；`MetricsTable.buildRows` 跳过 `iter<=0` 的主行，只出 eval 子行（UI 标「基线」）。

**验证**：`tests/test_baseline_eval.py`（新，6 例：iter 隔离 / 守卫逐条反证 / 只在 `_start_it` 派一次 / 失败不抛 / **端到端** baseline 派发把逐局行与 summary 都写成 `iter=0` 且预置同 wver 的 it1 行不吞它 / 幂等）+ `tests/console-paired.test.ts`（新增 3 例：it0 为开腿基准、无 it0 退回首个 eval 轮、合成行与老腿兼容）+ `test_gate_check.py` 的 `read_trend_rows` 过滤。门禁：nn-python-gate（ruff + mypy 145 文件 + pytest 全量）绿；`bun run check` / `bun run build` 绿。

**遗留**：`eval_every == 1` 的课程在 it1 既有 A-eval 又有 it0 基线（测的是同一套 PPO 前权重）⇒ 会多跑一遍语料（账本按 iter 隔离，it0 行仍落盘）；本腿 c6-bonus 为 `eval_every=5`，不受影响。

---

---


## §11 决策正文归档（搬自 `DECISIONS.md`，2026-09-23）

> 2026-09-23 把 `DECISIONS.md` 里这些条目的**正文全文**搬到这里（索引行与编号仍留在
> `DECISIONS.md` —— 编号永不重排）。锚点 = `### §<旧编号>`。

### §2026-09-11-goalnn-evalboard-idle-yield（2026-09-11，用户指令：evalboard 与训练 A-eval 解耦）

- **背景**：`eval now` 只 append pending 批；旧接线在 `rollout_phase` 里且仅当
  `not eval_on_round` 才 `maybe_dispatch_batch`——B/C 与 A-eval 轮绑定，训练停或
  恒 eval 轮时队列永假「已入队」。
- **备选与否决**：继续 A/B 轮次互斥 —— 否，两子系统不该耦合；console 起独立 worker
  进程 —— 否，与 train 池争节点且多一套生命周期；只放宽为「任意 rollout 轮都可领」
  —— 否，仍会在采集高峰抢集群。
- **决定**：① 从 `rollout_phase` 去掉 B/C 领批；② TrainingLoop 每轮 `_join_eval` 后
  开 idle 窗 `_evalboard_idle`（`window_event` 置位）领最早 pending 批；③ 下一轮
  rollout 前 `_evalboard_yield` 关窗 + 短 join，在途局停派新 seed、已结算不丢
  （`_reopen_for_resume` 把 partial 批回 pending，`_done_keys` 续跑）。
- **违反后果**：不改则 God/学生 B 批在 A-eval 主导的课程上永远 pending；抢跑会拖慢
  rollout 并放大尾延迟。

---

### §2026-09-13-eval-replay-export（2026-09-13，控制台「导出 replay」：in-loop eval 局的确定性重放导出）

- **决策**：训练控制台首页「最新 6 轮完整指标」行新增「导出 replay」——弹窗列出最新
  in-loop eval（= eval_summary 最大 iter，与指标表 eval 视图同口径）的全部逐局行
  （类型/击杀/承伤/杀/残血/道具/得分/耗时，表头排序 + 行勾选 + 全部/胜利/失败/超时/
  kills=N 批量选择），导出 = **按需确定性重放**：`rl/eval_replays_once.py` 以课程
  curricula（load_course+apply_course 单一事实来源）重建 difficulty/max_ticks/
  stageJson/lives/level，按 wver（sha256[:16]）解析冻结权重快照（it*/_eval_frozen_weights*
  → 活动权重 → weights/<course> 归档），并行调 `export-eval-game.ts --replay` 重放并录制
  输入 → canonical `.replay`（ReplayBrowser 可直接导入）+ manifest（含逐局 vs eval_log
  账本的 outcome/ticks/kills 确定性对账）→ 控制台 GET /api/evalReplayDownload 打 tar.gz。
  API：GET /api/evalGames、POST /api/evalReplays（loopback-only，busy 互斥同 evalA）、
  GET /api/evalReplayJob、GET /api/evalReplayDownload。
- **拒绝的替代方案**：评估时同步录制 replay 随局落盘（export-eval-game 常开录制）——
  每轮数百局 × 整局帧字节随 pack 传输/落盘，账本与传输面翻倍，且救不了历史 eval；
  TS 侧解析 curricula 重建课程参数——apply_course 的课程语义（自定义关/等级覆盖/
  param schedule）出现第二份实现，漂移即错局。
- **边界**：export-eval-game 的 `--replay` 为可选附加（recorder 被动采样，缺省路径
  行为逐字节不变），但该文件在 dist 哈希集内——变更后节点须随新 code 同步（常规流程）；
  权重归档被清理导致 wver 无匹配时 fail loud（错版本 replay 比没有更糟）；重放局与账本
  不一致（理论不该发生）在 manifest 里诚实标记，不静默；单次导出上限 400 局；
  replay 文件名 stage 段 = 原始 --stage id（自定义关 2000+ 非映射前 loadIndex）+1
  （buildReplayFilename 1-based 显示口径），python 映射时减回。
- **教训入册**：nn-training 内脚本目录 rl/ 会遮蔽 stdlib `queue`（rl/queue.py）——
  顶层 `from concurrent.futures import ...` 必须在移除 sys.path 脚本目录项之后
  （eval_replays_once.py 头部 scrub，实测循环 import 崩）。
- **同日注记（用户裁定）**：交付形态改为**逐文件**——不打 tar.gz；完成后每局一个
  .replay 写入用户指定目录（Chromium showDirectoryPicker，导出启动时先选目录、
  完成后逐文件写入；无该 API 的浏览器退化为逐文件浏览器下载）。服务端
  /api/evalReplayDownload(tar) 移除，改 GET /api/evalReplayFile（manifest.files
  白名单 + 文件名形态校验防穿越）。

---

### §2026-09-14-goalnn-dashboard-project（2026-09-14，用户指令：训练控制台独立为 `dashboard/` bun 项目；启动命令改 `bun run dashboard`）

- **决策**：`tools/training/**` **整棵**（core 领域模块 + console 网站 + ui 组件 + evalboard
  评估板 + `train.ts` python 启动器）搬到仓库根 `dashboard/`，成为一个**独立 bun 项目**
  ——自带 `package.json`（`start` / `launch` / `build:ui` / `typecheck` / `test` / `lint` /
  `format`）、`tsconfig.json`、`tests/`、`README.md`；20 个覆盖它的测试文件从根 `tests/`
  一并迁入 `dashboard/tests/`。根 `tools/training/` 目录**删除**，不再保留任何兼容壳。
  根 `package.json` 的启动脚本 `train` → **`dashboard`**（= `cd dashboard && bun run start`，
  仍听 :8900）。python 无头启动通道改为 `bun dashboard/src/launch/cli.ts --script <name>.py`
  （AGENTS §5.6 的 "never raw python" 语义不变，只是路径变）。
- **内部结构**（职责分层，调用方只 import 各目录 `index.ts` 桶，不直连内部文件）：
  `src/core/` 路径/日志/网络/进程/venv/账本/槽位等基础原语 · `src/stack/` 训练栈组件编排
  （specs/hub/courses/smoke/push）· `src/launch/` python 无头启动器 · `src/evalboard/`
  评估板领域（store/ladder/ingest/stats/…）· `src/server/` HTTP 与服务端逻辑
  （`api/` 17 模块 · `actions/` 11 模块 · `eval-board/` 6 模块 · iters/pool-history/
  exit-watchdog/build/server）· `src/web/` SSR + 浏览器 UI（`view/` 16 模块 · components ·
  app）。四个原巨型文件按职责拆开：`api.ts` 1913→17、`actions.ts` 973→11、
  `evalboard.ts` 1046→6、`ui/view.ts` 1513→16，单文件上限由 ~1900 行降到 444 行。
- **路径事实唯一来源**：`dashboard/src/core/paths.ts` 是全项目**唯一**用 `import.meta.dir`
  上溯推导路径的地方（`DASHBOARD_ROOT` / `REPO_ROOT` / `NN_TRAINING` / `LOG_DIR` /
  `BUNDLE_DIR` / `EVALBOARD_DATA_DIR` / `LADDER_CANON_PATH`）；其余模块一律 import 常量，
  绝不自算相对深度（搬迁时只有这一处会错，且一错即立刻暴露）。
- **跨项目读边界（唯一例外，写入即契约）**：`dashboard/` 只读消费 `src/` 的游戏契约
  （`config/stages`、`nn/arena-ladder`、`nn/config-stage`、`config/difficulty`、
  `config/combat`）与 `tools/agent/codehash-files`，用相对路径 import。**反向永远禁止**——
  `src/` 或游戏侧测试不得 import `dashboard/`（dashboard 是观测与控制面，不是游戏依赖）。
  这三四个契约是权威唯一源，复制进 dashboard 才是真错（漂移即错局/错门）。
- **拒绝的替代方案**：① 只搬 console/ui、core 留在 `tools/training/`（用户裁定的备选）——
  dashboard 仍靠相对路径反向 import 旧位置，"独立项目"是名义上的，且搬迁后两侧目录语义
  割裂（一半工具在 tools/、一半在 dashboard/）；② 保留 `tools/training/train.ts` 兼容壳
  转发到新路径——AGENTS 铁律「不留读兼容后门」的反面，双份入口必然漂移；③ 在
  `dashboard/` 里复制 `src/` 的游戏配置契约——单一事实源被复制，漂移即错关卡/错门禁。
- **边界与遗留**：根 `tsconfig.json` 的 `include` 增加 `dashboard`（`bun run check` 仍是一
  条总闸：tsc 覆盖 dashboard + 根 `bun test` 连带跑 `dashboard/tests/`，实测 2114 用例）
  **_（2026-09-14 同日反转：这三项是搬迁期过渡态，已被下一条追加取代）_**；
  `tools/test-silent.ts` 的兜底全量遍历目录表补 `dashboard`；`.gitignore` 的
  `console/.build/` 与 evalboard 数据根改指 `dashboard/`。**历史记录类文档不改**：
  `docs/*.progress.md` 与 `plan/*.md` 记的是当时的事实，改写等于伪造历史；权威手册
  （`AGENTS.md`、`docs/agents.details.md`）与可直接复制执行的操作文档
  （`docs/goal-nn-*.md`、`nn-training/README.md`、python docstring）已全部改到新路径。

- **追加（2026-09-14，同日，用户指令：「给 `dashboard/` 做自己的 `bun install`，让它的依赖
  不再依赖仓库根的 `node_modules`」）—— 依赖与门禁彻底切断**（上一条 bullet 中三项过渡态声明
  至此全部反转）：
  - ① **依赖**：dashboard 自带 `node_modules/` + **入库**的 `bun.lock`（`.gitignore` 给
    `!dashboard/bun.lock` 开例外，与 `!nn-training/uv.lock` 同一逻辑：`*.lock` 本意是运行时
    PID 锁文件）；根 `package.json` 删除只有 dashboard 用的 `preact` /
    `preact-render-to-string`（根游戏代码零 `preact`、零 `.tsx`，32 个 `.tsx` 全在 dashboard/）。
    **`bun install` 只更新 lockfile、不删已装目录** —— 陈旧目录仍可被解析，等于留后门，
    必须 `rm -rf node_modules/{preact,preact-render-to-string}` 才真切断。
  - ② **门禁边界**：根 `tsconfig.json` 的 `include` 移除 `dashboard`；根套件改用
    `bun test --parallel --timeout=50000 --path-ignore-patterns='dashboard/**'`。教训：**位置参数
    是子串过滤**，`bun test tests` 会把 `dashboard/tests/` 一并跑掉（实测 194 文件）——
    排除目录只能靠 `--path-ignore-patterns`。`tools/test-silent.ts` 的 `SKIP_RE` 加 `dashboard`
    （并删掉兜底遍历表里的 `dashboard`），另加一条分支：**改动全在 `dashboard/` 下 ⇒ 不
    fallback 全量**（否则只改 dashboard 的提交会白烧一整轮根套件，撞上 spawn 真实 CLI 的慢测试）。
  - ③ **门禁不降级**：pre-commit 新增 **dashboard 门禁块** —— staged 含 `dashboard/` 时跑
    `cd dashboard && bun run typecheck` + `bun run test`；tsc 报错前缀 `dashboard/` 后与 staged
    求交（dashboard 内 tsc 输出的是相对它自己的路径，漏了前缀归因永远为空 → 红会被当「他人
    未暂存改动」放行），`--selftest` 已加该归因断言；逃生口 `SKIP_DASHBOARD_GATE=1`。
    没有这一块，「把 dashboard 切出根门禁」会静默退化成「没人跑 dashboard 门禁」。
  - ④ **验证（双向硬证据）**：dashboard `tsc` 干净 + **306 pass / 0 fail**（20 文件）；根
    `check` **1807 pass / 4 skip / 0 fail**（174 文件，日志零 `dashboard/tests/`）；
    忽略前后的 A/B 差值 = **306 用例 / 20 文件**，与 dashboard 独立套件完全一致（排除精确，
    无过杀）；146 个 dashboard 源文件用 `Bun.Transpiler.scan()` 取裸包名逐一解析 **零泄漏**；
    根解析 `preact` 失败而 dashboard 正常（供给关系已反转）。4 个 skip 是环境型
    （`it.skipIf(files.length === 0)`，沙箱 `replays/` 无生成物），非覆盖丢失。
  - **拒绝的替代方案**：只做 `bun install` 不做切断（根仍留 `preact`、根 check 继续覆盖
    dashboard）——两份依赖重叠，dashboard 文件仍能从根 `node_modules` 回退解析，「独立项目」
    是名义上的；以及「切断但不加 pre-commit 门禁块」——hook 更简单统一，但代价是 dashboard
    改动提交时不再被任何自动门禁覆盖（静默降级），用户选择不承担。
  - **仍然保留的耦合（设计，非缺陷）**：dashboard 与根之间仍有**源码级**相对 import
    （`src/config/*`、`tools/agent/*`、`tools/eval/mcnemar`、`tools/sim/pack-container`）——
    观测面只读消费游戏契约，权威唯一源在根，复制进 dashboard 才是真错。切断的只有包依赖方向。

---

### §2026-09-17-goalnn-console-task-bundle-exchange（2026-09-17，控制台「导出任务包 / 导入产物即评估」；控制台不重造包格式）

- **背景**：用户 2026-09-17 需求——dashboard 支持导出 `task-<课程>.zip`；支持导入训练产物
  `deliver-<课程>.zip`，导入完成后**自动按课程配置跑 eval**。底层能力（`remote/bundle.py`
  导出包、`remote/deliver_zip.py` 导入器）同日先落地，本条目只裁决**控制台这一侧**接法。
- **备选与否决**：① 控制台自己拼包（否决——`--export-bundle` 已在 trainer 内，
  重造 = 第二份真相，`bundle.py` 模块注释写明）；② 导入后另写一套评估命令（否决——
  语料口径/双轨种子/账本格式会与 `evalA` 漂，读数无法与训练期对比）；③ 导出互斥键
  在 HTTP 请求里 `add`/`delete`（否决——导出跑几分钟，等于没有锁，第二次点会起第二个
  `run_rl` 抢同一门课的锁）；④ 上传体在控制台解包/校验（否决——zip 是人搬来的、
  最不可信，三道门（zip-slip / 形状 / 课程对账）留在 python 一侧，控制台只挡文件名课程
  与体积）。
- **决定**：
  1. **智能在 python 一侧，控制台只做三件事**：拼 argv（`--course/--ppo remote/--run-iters -1/
     --export-bundle <abs>`）、起一次性 detach 进程（日志 `logs/<课>/export-bundle.log`，
     **不注册组件**——进账本会被监督器当「该重启的组件」）、把产出文件交给浏览器
     （`GET /api/taskBundle` 流式 `Bun.file` + Content-Disposition）。
  2. **导入 = 上传（multipart，不走 JSON 动作层）+ `python -m remote.deliver_zip`（同步，
     调用方需要它的结果）+ 接着起 `evalA`**。评估评**包里末轮**权重、用课程配置语料；
     评估起不来**不算导入失败**（产物已落地可读，那是这一半的全部价值），响应里明说原因。
  3. `evalA` 唯一启动点抽到 `eval-a-run.ts`：按钮与「导入后自动评估」共享命令**与互斥键
     （`eval:A`）**——两处各写一份会出现「按钮说在跑、导入那边不知道」。
  4. 跨语言常量（`DELIVER_IMPORT_JSON=` / `deliver-`）单列 `bundles/marks.ts`，测试直接读
     python 源码对账：改一边忘另一边会红（写岔是**静默**的，控制台会把「导入失败」错报）。
- **落地时抓到的两个缺陷**（均有回归）：① **导出互斥键**：键必须由**子进程退出**释放
  （轮询 pid），在请求里删 = 没有锁；② **`--export-bundle` 被轮内 shard 门误杀**——导出
  既不发 job 也不训练，`rollout_spec` 非空 + traj 有历史残留 shard 会命中「M3 上云轮必须
  空 shard」⇒ 任何跑过一轮的课都导不出包（实测 c6-chip）。判定抽成纯函数
  `_gate_round_shards`，例外**只**覆盖导出（`tests/test_export_shard_gate.py` 同时钉住
  「关掉 exporting 两条门照旧生效」，防顺手删门）。
- **边界（未做）**：① 真云端端到端一次（本机无节点）；② 导入是**同步**阻塞（几 MB 秒级，
  几十 MB 会让控制台这段时间不响应轮询——可接受，量大再挪后台+状态位）；③ 导出期间刷新
  页面会丢「生成中」态（只轮询产出文件）。
- **落地**：`dashboard/src/server/bundles/{export,import,marks}.ts`（新）、
  `server/run-python.ts`（新：一次性 python 的唯一入口）、`server/eval-a-run.ts`（新）、
  `server/server.ts`（`POST /api/deliverUpload` + `GET /api/taskBundle[Info]`，全部落在
  既有回环门控之后）、`web/app/panels/TaskBundlePanel.tsx`（新）+ `api-client.ts`、
  `api/route.ts`（`exportTaskBundle`，`evalA` 改调共享启动器）；回归：
  `dashboard/tests/server-api-task-bundle.test.ts`(17) +
  `nn-training/tests/{test_deliver_zip,test_export_shard_gate}.py`。

---

### §2026-09-19-goalnn-console-course-mode-toggle（2026-09-19，plan R3-2：控制台每课离线/在线开关 + 意图回灌）

**背景**：hub 的「离线课」语义**早就实现且被 e2e 钉住**（`POST /admin/courses?mode=offline`：
不实时派发、只收 it 权重/指标回传），但**全仓没有任何控制台代码调它**——面板上只有一个只读
徽标，想真用这个闸只能手敲 curl；而且 hub 的 `mode` 是 **volatile**（重启回启动参数）⇒
hub 一重启就**静默**恢复派发。这是「功能存在但不可达 + 重启即失忆」的两层缺口。

**定案（`actions/course-mode.ts` + 总览卡开关）**：

1. **热切 + 意图落盘**（`setCourseMode`）：调 hub `/admin/courses` 后把意图写进
   `console-state.courseModes[课]`（additive 键，旧 state 文件读作空表）。
2. **起 hub 时回灌**（`restoreCourseModes`，接在 `startComponent('hubServer')` 的两个分支上）：
   ① **两种模式都发**（不是只补 offline）——hub 可能被以别的启动参数拉起来（例如
   `--offline c5`），只补 offline 会让「我明明点过在线」惄惄失效；② 「已运行」那条早退路径
   也回灌（hub 可能是手敲命令/别的终端拉起来的）。
3. **hub 拒绝/不可达 → 意图照样落盘**并如实报告「已记录，但 hub 未接受：<原因>（起 hub 时
   会按意图回灌）」：运维的决定不因为 hub 没起来而蒸发；但**也不谎报切换成功**。
4. **开关只在 hub 认识这门课时出现**（`hubOnline && hubSeen`）：hub 不认识就 400，
   给按钮等于给一个假承诺；开关是行按钮的**兄弟节点**（行本身是 `<button>`，嵌套 button 非法），
   hub 无应答时整个开关都不渲染。
5. 新增 `hubSetCourseMode`（`stack/hub-admin.ts`）：与 halt/resume 同性质的 `/admin/*` 客户端，
   返回人读错误而非抛（观测/运维面不得把页面带崩）。

**回归**：`dashboard/tests/course-mode.test.ts`（11 例：请求形状与 Bearer / 意图落盘 / 幂等重发文案 /
400 与连接被拒都**保留意图**且不抛 / 非法模式与空课程**一次都不打 hub** / 回灌两种模式都发 /
全失败逐课点名 / 无意图 → 空摘要 / 旧文件与脏键归一化）+ 总览面板 3 例（开关只在 hubSeen 时出现 /
文案按模式反转且行按钮仍 4 个 / 无 onAction 或 hub 无应答都不渲染 + `setCourseMode` 在面板与路由
**两侧**存在的接线断言）。gate：dashboard **588 pass / 0 fail**（574 → 588）+ `tsc --noEmit` 干净。

**仍未做**：操作面（单例 `trainingLoop` 卡片 + 「入队/暂停该课」）——暂停需要一个**跨进程控制通道**
（控制台不能直接调 python supervisor），需先定形态再动训练侧调度环。

---

---

### §2026-09-18-goalnn-console-worker-register-and-overview（2026-09-18，用户指令：控制台 worker 登记入口 UI + 面板重组）

- **背景**：P1 的 python 侧（多课程单 hub + hub 中介 push 派发）已就位，但控制台还停在单课程时代：课程 select 在 hub 运行时**被锁死**（§367 的保护，当时 hub 按课程建 jobRoot/日志目录——那个前提已被「单进程托管 N 份账本」推翻），「正在训练」只高亮**一门**课（多课程并行时其余在训课程在界面上隐形），`/admin/queue` 与 `/admin/push-workers` 两个观测面**没有任何读方**，worker 登记只能手改 rl-config。用户口径（本次）：dashboard 提供 worker 登记入口、回填 rl-config.json、hub 周期 ping 检测联通。
- **决定**：
  - **登记 = 配置编辑，不是第二份登记表**：面板只 upsert rl-config `nodes[]` 里那条 `gpu_push` 条目（hub 按 mtime 热重载），写完顺手 `POST /admin/push-workers {action:"reload"}` **best-effort** 让 hub 立刻拾取（失败不算失败——配置已落盘，下一拍热重载兜底）。两处各存一份登记必然漂。
  - **探活失败不拦登记**：云机还没开机是常态，拒登记会把「先配好、再开机」这条路堵死。返回值如实分流——`ok:true`（ping 通）/ `ok:false`（已登记但 `/ping` 不通，并点名查 worker_server / 隧道 / authKey），与 `setNodeConcurrency` 的 `done(smoke.passed, …)` 同款语义。
  - **课程指针收口**：改 url 或删节点时，`courses.<课>.push_node_url` 里指向旧 URL 的指针**一并改写/清除**。留着它就是「指向 config 里不存在的 URL」⇒ python `_gpu_push_nodes` 匹配 0 个节点后**静默回落 pull**，而操作员看到的一切正常（2026-09-15 同类事故的入口）。
  - **两列探活刻意分开**：面板直探 `{url}/ping`（登记那一刻的体检）与 hub 周期探活结论（调度器此刻认不认为它在线的**唯一判据**）各占一列；两列不一致本身就是信号（面板通而 hub 判离线 ⇒ hub 还没重载配置）。
  - **课程 select 解除锁定**（含历史课程自由可切）：切课程只改「本浏览器看哪门课」+ 本机操作员课程，不碰任何在训进程；多课程并行下旧锁定会把查看/切换彻底锁死。
  - **「在训课程」的唯一判据 = registry 里 `trainingLoop` 进程存活**（服务端 stamp `trainingCourses`，课程 select 的 🔥 与总览的「在训」列同源）。不问 hub（它只知谁派过活，训练停在两轮之间时一无所知），不问 console-state（那是「在看的课」）。
  - **并行总览**：hub 行（应答基址 / 在派发课程数 / 活跃 worker 数 / 竞速 / 停机 / **最近派发**=轮转游标）+ 每课一行（在训 / 离线 / iter / 队列深度 / 在飞）。这一行回答的是「某门课为什么在饿着」的四种可能，在日志里要靠猜。
  - **每课 iter 取账本尾行、且只认 `iteration` 事件**：`training_log.jsonl` 是**多写者**文件（训练侧写 iteration，hub 追加 job_completed/job_failed），把 `job_completed` 的 it 当轮次会读出「还没跑完的那一轮」。
  - **hub 基址解析不新增配置键**：按 registry 里**活着**的 hub 条目逐个试 `/admin/queue`，第一个应答者即观测源。单 hub 服务多课（新形状）与每课一 hub（旧形状）都能读对；都没有 → null，面板显示「hub 无应答」而不是编一个地址。
  - **观测面一律容错**：任何失败（无 hub / 401 / 坏 JSON / 账本不可读）→ null/空态/零值，绝不把 `/api/state` 带崩；总览与登记表共用一次 hub 探测（5s TTL + 单飞）——进程级全局观测不该按查看课程各探一遍。
- **备选与否决**：把登记做成控制台自己的 worker 表 —— 否（hub 与训练侧都按 rl-config 判定，第二份表 = 三处口径）；登记时 ping 不通即拒 —— 否（见上，堵死正常工序）；把总览塞进慢快照 —— 否（慢快照按课程键控，进程级全局观测会乘上查看课程数）；由 hub 的 course 表推「在训」 —— 否（hub 不知道训练循环是否停在两轮之间）；**组件卡片按「单例角色 / 按课程」拆两种形状** —— **推迟**：卡片的数据源仍是 per-course 账本条目，而 hub/隧道收敛为单例（单隧道那一步）之前拆形状只会做出一个「看着像已经支持多课程」的假象。
- **违反后果**：面板另存一份登记 ⇒ 三处漂、hub 与训练侧对「哪几台能接活」判断不一致（症状是 job 永远躺在队首）；登记拒掉 ping 不通的机器 ⇒ 正常工序被堵、操作员绕开面板去手改配置；改 url 不收口课程指针 ⇒ 指向死 URL ⇒ python 静默回落 pull（故障被伪装成正常）；用 hub 的 job 历史判「在训」⇒ 训练间歇期课程被从 UI 上抹掉；把 `job_completed.it` 当轮次 ⇒ 总览显示还没跑完的那一轮。
- **落地**：`dashboard/src/web/view/course-overview.ts`（新：视图类型 + `parseHubQueue` / `latestIterFromLedgerTail` / `overviewCourseNames` / `buildCourseRows` / `validWorkerId`）、`dashboard/src/stack/hub-admin.ts`（新：`hubCandidates` / `liveHub` / `hubPushWorkers` / `hubReloadPushWorkers` / `probePushWorker` / `withWorkerProbes`）、`dashboard/src/server/api/overview.ts`（新：`trainingCourses` / `getHubAdmin` 5s 缓存 + 单飞 / `buildOverview` / `buildWorkerRegistry` / `courseIter`）、`dashboard/src/server/actions/workers.ts`（新：`registerPushWorker` / `removePushWorker` / `reloadPushWorkers`）、`dashboard/src/server/api/route.ts`（三个动作接线）、`dashboard/src/server/server.ts`（动作后与慢快照同一时机置空 hub 观测缓存——一个失效点，不在 route 层重复）、`dashboard/src/server/api/state-view.ts`（注入 `trainingCourses` / `overview` / `workerRegistry`）、`dashboard/src/web/app/panels/CourseOverview.tsx`（新）、`dashboard/src/web/app/panels/WorkerRegistry.tsx`（新，表单独立成 `WorkerForm` 以便 SSR 断言）、`dashboard/src/web/app/app.tsx`（解锁课程 select、在训课程全高亮、挂载两个面板）、`dashboard/src/web/theme.css`（`.tc-cov*` / `.tc-wreg*`）；回归：`dashboard/tests/server-api-overview.test.ts`（12 例）、`dashboard/tests/server-actions-worker-register.test.ts`（16 例）、`dashboard/tests/web-app-course-overview.test.ts`（12 例），并改写 `dashboard/tests/web-ssr-readonly.test.ts` 的课程 select 用例（锁定语义随本轮变更）。
- **本轮未做（P1 余下）**：① 单隧道——hub / cloudflared / trainingLoop 收敛为单例（现在仍是 per-course 拉起），随之把组件卡片拆成「单例角色」与「按课程」两种形状；② 多课程单 hub 的端到端 e2e（训练侧 hubpush → hub → 真 worker_server + 假 PPO）。

---

### §2026-09-19-goalnn-console-card-families（2026-09-19，R3-3：组件卡按「单例角色 vs 按课程」分族）

- **背景**：控制台把六个受管组件排成一行 chips，谁跟课程绑定、谁是全机/全局一份，只能靠 `<b>共享</b>` 这一个布尔徽章与操作员的记忆区分。两条具体误读都是这台机器上真会发生的：① hub/隧道已单例（§2026-09-18），但卡片仍按「当前查看的课」渲染，操作员会给这门课**再起一个 hub**（第二个实例抢同一端口）；② trainer 卡片看着像全局对象，但按下去起的是**当前查看的那门课**——换课程 = 换对象这件事在视觉上没有任何提示。
- **决定**：把「作用域」提升为一等事实，卡片按它分族渲染（`web/view/component-groups.ts`）。
  - **三态作用域由 `core/registry.ts::componentScope(key)` 单点给出**（`singleton` = selfNode / `shared` = hub·隧道（账本槽恒 `''`）/ `course` = 其余），服务端算一次填进 `ComponentView.scope`；客户端**不许**自己按 key 猜。
  - **族归属 = scope 的函数**，视图层**不写**「哪些 key 属于哪一族」的名单：族名单一旦与账本槽位规则漂开，症状是某个组件从 UI 上**消失**（而它照样被启动、被监督、被冒烟）。面板只负责画。
  - **节点面例外声明成数据**（`NODE_FACE_COMPONENTS = ['workerServe']`），不再用面板里的一行 `filter` 静默过滤：`worker_server` 的语义轴是节点/GPU 身份（id/url/concurrency 都是节点的，hub 的 push 派发与竞速也按节点算），卡片行再渲染一份就与节点行出现「同一件事两个入口」。`LogNavCard` 复用同一常量（两处 filter 漂开 = 某个组件某处消失）。
  - **组内顺序是纯化妆**（`ORDER` 表；未列出的 key 落组尾但**不丢**），**空组不渲染**（没东西可说时不留空壳）。族标题 + 悬停说明上屏（「与课程数量无关」/「卡片上的对象是当前查看的那门课」）。
  - **`scope` 缺省/未知 ⇒ 按 `course` 渲染**（单侧保守，与 loop-queue 的 `kind` 同一条规矩）：少一个徽章只是少信息；凭空空贴「共享」会让操作员以为「停它就是停全局」（而它其实只停本课）——假承诺比缺标签贵。
  - 徽章只标 scope 说不出来的那件事：`shared` ⇒ 「共享」、`singleton` ⇒ 「单例」、`course` ⇒ **无徽章**（按课程是默认语义，组标题已说；每行再挂一个只是噪声）。
- **备选与否决**：① 继续排一行、只加徽章——否（这正是问题本身：单例角色与按课对象混在一个序列里）；② 在面板里写两族 key 名单——否（第二份真相，且新增组件会静默落进没人认识的桶）；③ 保留 `shared` 布尔再另加 `scope`——否（两个字段 = 两个真相，必然漂开）；④ 顺手把 trainer/localWorker 的账本键也收敛成共享槽——**本轮不做**（见下）。
- **违反后果**：任何客户端按 key 自建族别名，都会在 registry 改规则的那天让某个组件**静默消失**；任何把 `shared` 语义空贴给按课程组件的写法，都会把「只停本课」演成「停全局」。
- **落地**：`core/registry.ts`（`ComponentScope` + `componentScope`）· `server/api/views.ts`（`ComponentView.scope` 取代 `shared`）· `web/view/component-groups.ts`（新：`cardFamilies` / `scopeBadge` / `NODE_FACE_COMPONENTS` / `FAMILY_META`）· `web/app/panels/ComponentCards.tsx`（分组渲染）· `web/app/panels/LogNavCard.tsx`（复用例外常量）· `web/theme.css`（`.tc-comps__group*` / `.tc-cc__scope--*`）。回归：`tests/web-component-groups.test.ts`(11：分族与族内顺序 / 节点面例外是真组件 / **全组件恰好归属一处** / **与 registry 判据对拍** / `scope` 缺省保守 / 未列出的 key 不丢 / 空组不渲染 / 不改动调用方数组 / 徽章三态) · `tests/web-components.test.ts`（分族 SSR：两组标题与 `data-family`、族内顺序、共享×2+单例×1、节点面组件不在卡行）· `tests/single-hub-tunnel.test.ts`（`.shared` → `.scope`）。进度 `docs/nn/console.md` §7。
- **未做（明确记录，不是漏）**：账本键的真正收敛——`trainingLoop`/`localWorker` 仍是 per-course 键（控制台仍按课起 `run_rl.py --course`，尽管训练侧已有 `--serve` 单进程服务所有课程），`workerServe` 仍住 per-course 表（轴却是节点）。那是**启动面/监督面**的改动（含 `TrainLaunchModal` 的精简与旧条目换代接管），与本轮的「把两族读出来、说清楚」是两件事；本轮的分族恰好是它的前置（换成共享槽后，课程面只剩数据、进程面全在服务面）。
- **一条构建期坑（值得记）**：客户端代码里写**未加引号的 `node:` 对象键**（`{ node: [] }`）会让三份 bundle 全红——`server/build.ts` 的禁词门禁把 `node:` 当「引入了 node 内置模块」。本文件已在 `ComponentFamilyId` 注释里写明。

---

### §2026-09-19-console-node-wallsec（2026-09-19，用户指令：节点统计新增训练机侧平均墙钟）

- **背景**：节点统计「平均耗时」= meta `elapsedSec` 滑动均值；该字段是**节点侧**接单→
  结果就绪（sampler-agent `stampServiceSec`，含冷启动，**不含**训练机↔节点网络）。用户
  要求另加一列训练机侧墙钟，观测派发→结算的真实回传成本。
- **备选与否决**：把 trainer 墙钟**覆盖**进 `elapsedSec` —— 否，摧毁节点算力横向比
  （2026-09-06 口径升级正是为对齐 local/remote 服务时长）；只改 dashboard 从现有
  字段反推网络 —— 否，meta 无派发时刻，推不出来；在 agent 侧再起一个计时器写第二
  字段 —— 否，网络段只在 trainer 视角完整，节点侧测不到提交/回传。
- **决定**：双字段并列，互不覆盖。`wallSec` = **本 worker 本 attempt** 派发→结算墙钟
  （`t_task_start` 本地量，**勿读** `inflight_ts[task]`——竞速副本会覆盖）；成功结算时
  写入 `dist-agent-meta.jsonl` + summary。dashboard 聚合 `avgWallSec`（≤50 滑动，与
  `avgElapsedSec` 同窗），NodeStats 新列「机侧墙钟」。历史 meta 无 `wallSec` → 显示 `-`。
  含网络/异步轮询/排队，**不是**纯 RTT；慢节点判定仍用服务时长，不改 `isSlowNode`。
- **违反后果**：覆盖 `elapsedSec` ⇒ 节点算力对比被网络污染；用 `inflight_ts` 算墙钟
  ⇒ 竞速赢家的墙钟被输家起点抬高/压低；把墙钟当 ping 用 ⇒ 轮询间隔被误读成故障。
- **落地**：`nn-training/rl/{dispatch,queue,eval_dispatch}.py`（meta `wallSec`）；
  `dashboard/src/server/pool-history.ts`（`pushWindowSample`/`windowMeanSec`/`avgWallSec`）、
  `pool-types.ts`/`api/pool.ts`/`NodeStats.tsx`；回归 `dashboard/tests/server-pool-history.test.ts`。

---

### §2026-09-19-evalboard-phase0-census（2026-09-19，Phase 0 逐敌种画像进 EvalStore schema（中方案 P1））

- **背景**：T5 主端点 = power 曝光归一命中/千 tick（七列：hitsByKind/killsByKind/
  exposureByKind/firstHitKind/firstKillKind/killOrder/killerKinds）。报告层已有这七列
  （`export-eval-game.ts` 顶层，同日早些时候落地），但 A/B/C/m1 四条逐局行构造点都没搬
  ⇒ EvalStore（唯一账本，§3.1）查不到分敌种读数。用户拍板「中方案」：列进 schema +
  判决批走 B 层（P2/P3 见 `docs/evalboard-phase0-census.md`）。
- **备选与否决**：① 维持现状（判决只读临时自造 JSONL）——否，账本永不沉淀分敌种读数、
  控制台无法看；② 只给 A 层行加列 —— 否，B/C 批（判决批的宿主）同样要读；③ 把七列塞
  `scorable.telemetry` 靠 `eval_loot_fields` 回退 —— 否，形态不同（telemetry 是标量，
  这七列是 4 元数组 + 序列表），且无端改 codehash 集内文件的报告形态；④ 直接进
  `REQUIRED_FIELDS` 不设豁免 —— 否，本批次之前的资产行会全量报缺（P0 覆盖率要求
  100% **或明示豁免**）。
- **决定**：`rl/eval_local.py::eval_census_fields` 单源（**只认顶层**，缺键 = None
  不伪造），A/B/C/m1 四个写点接线；`ingest.ts` 映射（畸形计数列/非字符串归零或空）；
  `store.ts` 七列进 `EvalGameRow` + `GAMEPLAY_FIELDS` + `REQUIRED_FIELDS`，并新增
  `PHASE0_FIELDS` 作为**旧资产行的明示豁免清单**（豁免由调用方传，不写死在
  `coverageReport` 里）。
- **违反后果**：七列改走 telemetry 回退 ⇒ 同一字段两种形态、旧值真假难辨；缺键填零
  却不进豁免清单 ⇒ 覆盖率报表冤报旧行、真缺失被噪声淹没；把七列排除在
  `GAMEPLAY_FIELDS` 外 ⇒ 双跑不一致无人发现（§3.4 确定性契约失效）。
- **落地**：`nn-training/rl/eval_local.py`（`EVAL_CENSUS_KEYS` / `eval_census_fields`）、
  `rl/{eval_dispatch,batch_eval,eval_a_once,eval_ingest}.py`；`dashboard/src/evalboard/
  {ingest,store}.ts`；回归 `nn-training/tests/test_eval_census_fields.py`（5 例）+  
  `dashboard/tests/evalboard-{ingest,store}.test.ts`（映射/豁免）。验证：真实
  `_eval_report.json` 七列齐全可抽；nn-python-gate 1263 绿 · dashboard 513 绿 +
  typecheck · 根 `bun run check` 1875 绿。

---

### §2026-09-20-dashboard-shell-routing（2026-09-20，训练控制台信息架构重设计 P0：应用外壳 + 路由化详情页；**路由实现 = 单 bundle + 服务端路由 SSR + pushState**）

**背景**：控制台是单列纵向堆叠的 12 个平权面板（无分区/无导航），四类详情（指标/传输/节点统计/日志）
住在全屏模态抽屉里（**没有 URL**：看不到也分享不了「我在看这个视图」），课程选择器（整页主语）被
`flex: 1` 挤在顶栏正中间与状态 chip 抢权重，底部还有一段 299 字的说明文字占了主版面最长的文本块。
全部问题按编号列在 `docs/dashboard-redesign.md` §1.2（C1–C13），目标形态在 §3。

**决定（P0 已落地）**：
1. **外壳**：`Sidebar(216px) + Topbar(56px) + 主内容`（`web/app/shell/{Shell,Sidebar,Topbar}.tsx`）。
   课程选择器 + 触发门禁 + 刷新间隔一并移入**侧栏底部**——课程是整页主语，位置固定且全页唯一；
   顶栏只剩「本页（标题 + 这一页回答什么）」与「全局状态（阶段/在训/节点/连接 + ⟳ + 更新时间）」。
2. **详情路由化**：`/metrics` · `/nodes` · `/wire` 三个新路由接管原抽屉三个 tab；**`Drawer.tsx` 删除**
   （无引用、无测试）。`/eval` 与 `/log/<key>` 维持独立页与独立 bundle，不并入控制台路由表。
3. **路由真相只有一份**：`web/view/routes.ts`（`PageKey` / `PAGES` / `NAV_ITEMS` / `pageForPath` /
   `bootstrapPage`，纯函数）。服务端 `server.ts` 通过 `pageForPath(url.pathname)` 判定页面并
   `renderConsolePage(state, { page })` 注入 `window.__INITIAL__.page`，客户端据此渲染首帧。
4. **首帧不读 `location`**：`page` 由服务端 stamp 决定（客户端用 `canonicalPath(page)` 推导航激活态），
   挂载后的 effect 才按 URL 校准 + 监听 `popstate`。理由同既有 hydrate 纪律（SSR 无 `location`，
   首帧读浏览器状态 = 水合错配）。

**被否方案**：① **每页一个独立 bundle**（`/metrics.js` `/nodes.js` `/wire.js`）——三页共用同一份
`/api/state` 快照，页面差异只是「渲染哪部分」；独立 bundle 换来 3 次额外构建、3 份 SSR 入口、3 套
hydrate 路径，而收益只有体积（当前 app 包 68.7KB / 预算 150KB，远未吃满）。② **保留模态抽屉只重做视觉**
——C2 的核心损失是「没有 URL + 打断监控上下文」，换皮不解决。③ **只读时物理禁用动作按钮**
（原设计倾向）——owner 拍板维持既有哲学（物理禁用会让组件区灰败破碎），只读可见性改由
侧栏常驻 `tc-lock` 锁徽标 + 首屏一次性横幅承担（`docs/dashboard-redesign.md` §7 O1–O4）。

**落地范围**：`web/view/{routes.ts,format.ts(fmtElapsed)}` · `web/app/shell/{Shell,Sidebar,Topbar}.tsx` ·
`web/app/app.tsx`（外壳 + 四页分派）· `web/render.tsx`（`ConsolePageOpts.page`）· `web/app/index.tsx`
（`ConsoleBootstrap`）· `server/server.ts`（页面族路由）· `theme.css`（外壳 token + 12 栏栅格 + 三档响应式）。
**删除**：`web/components/Drawer.tsx`。**尚未做（P1–P4）**：统一行原语 `StatusRow`（现 4 套行式实现）·
`CourseMatrix` 合并「并行课程总览 + 训练调度器」· `AlertDock` 合并 7 类横幅 · `KpiStrip` ·
空态四态 · 底部大段说明拆进各页口径折叠块 · 字号阶梯全量替换（现正文仍 11.5–12.5px）。

**配套回归**（新增/改写）：`tests/web-view-routes.test.ts`（新，22 例：路径归一化/往返一致/URL 带课程/
导航激活/路由表结构约束/引导载荷三层回退）· `web-ssr-console.test.ts`（改用 `#root` 切片断言 DOM——
整个 `theme.css` 被内联进 `<style>`，类名断言此前靠样式表文本**假通过**；顺带揪出 `tc-cc__name` 只存在于
CSS 里的空断言）· `web-wire-panel-wiring.test.ts`（抽屉接线 → 路由表/服务端/分派三处接线）·
`web-ssr-readonly.test.ts`（角标 `tc-badge--ro` → 侧栏 `tc-lock`；标签文案「正在训练：」→「还有在训：」
——后者会被读成「当前查看的这门在训」，而标签列的恰恰是别的课）。

**证据**：`cd dashboard` — `bun run typecheck` ✓ · `oxlint` 0 警告 0 错误 · `bun run test` **746 pass / 0 fail**
（P0 前基线 717 pass / 86 文件）· `bun src/server/build.ts` 三份 bundle 全绿（app 68.7KB gzip）。

**已知局限**：① P0 只做外壳与路由，页面内仍是单列堆叠（栅格与面板归组是 P1/P2）；② 概览页三个面板
（`MetricsTable`/`NodeStats`）仍带 `tc-drawer__panel` 类——抽屉没了但类名沿用，重命名随 P3 的 CSS 清理；
③ `LogNavCard` 暂留在总览底部（与组件卡的「≡ 日志」入口职责有重叠，是否下线待 P3 定）。

**P1 续（同一轮工作：行原语）**：四套「一行实体 + 状态 + 指标 + 动作」实现（`tc-comps` chip /
`tc-npill` / `tc-wreg__pill` / `tc-cov__row`·`tc-loopq__row`）收敛为 `StatusRow` 单一原语
（配 `StatusDot` / `SectionHeader` / `Empty`）；组件卡、节点行、worker 登记行已迁移，
课程矩阵两行随 P2 合并时迁移。同类问题的第二个实例一并收敛（`docs/dashboard-redesign.md` §1.2
新增 **C14**）：**动作结果反馈 6 处手写、5 种样式**（其中一处漏了 `tc-muted` 因而不灰，一处误用
卡片页脚样式 `tc-caption` 而多画一条上边框）→ `InlineNotice`。

**决定**：
1. **动作反馈分两层，原计划的 `Toast` 作废**：`components/Flash.tsx` **本就是**右上角浮层
   （`position: fixed` + 8s 自动隐藏），不存在「顶部 `Flash` 行」可替代——按原计划再新增 `Toast`
   只会造出**第七处**同类实现。定为：跨面板的全局动作 → `Flash` 浮层；面板局部动作 →
   `InlineNotice` **就地**（「在哪个按钮旁边」正是它的信息价值，搬到屏幕角落是净损失）。
2. **不给 dashboard web 测试加 DOM 夹具**：实测 `preact-render-to-string` **丢弃全部事件处理器**
   （`h('pre', {onClick}, 'x')` → `<pre>x</pre>`），而本仓 web 测试全是 SSR、无 `happy-dom`/`jsdom`。
   于是**交互行为类缺陷在 HTML 上不可观测**，断言拦不住——实例：旧 `tc-cc__detail` 是
   `<pre onClick={close}>`，在日志尾部拖选文字一按鼠标就误收起，而任何 SSR 断言都看不见它。
   选择**不加依赖**（MANIFEST §14 零新依赖 + dashboard 自包含纪律），改用「结构断言钉形状 +
   交互缺陷靠读代码评审」，并把这条局限写在用例头注里。**写新 web 用例时不要以为 SSR 断言
   守住了点击行为。**

**P1 证据**：`cd dashboard` — `bun run typecheck` ✓ · `bun run test` **776 pass / 0 fail**
（P1 前 746）· `bun src/server/build.ts` 三份 bundle 绿（app **69,987 B gzip** / 预算 150 KB）。

**P2a 续（同一轮工作：课程矩阵）**：「并行课程总览」（hub 侧：注册/离线/队列/离线段）与
「训练调度器」（训练侧：指针/卡在哪一步/在等什么）两张各写半边的表，按课程名 **outer join**
合并为一张（C5）。新增 `web/view/course-matrix.ts`（纯函数）与 `web/app/panels/CourseMatrix.tsx`；
**删除** `CourseOverview.tsx` · `LoopQueue.tsx` 与两套行样式（`.tc-cov__*` / `.tc-loopq__*`，~335 行 CSS）。
合并的产出不是「少一块卡」，而是**两条从前两边的表各自都看不见的矛盾**上屏：

- `在训 · hub 未注册`（warn）——rollout 在跑、hub 的课程表里没有它 ⇒ **PPO job 永远不会被派发**；
- `hub 已注册 · 无进程`（warn）——hub 在给它派活、没有任何进程推进它 ⇒ **job 堆着没人消费**。
  这条在合并前长得像普通的「停」（前者的两半在各自表里都完全正常）。

**决定**：
1. **矩阵用真 `<table>`，不用 `StatusRow` 芯片行**（与 `docs/dashboard-redesign.md` §4.2 的初稿相反）。
   `StatusRow` 是 `inline-flex` 芯片行、**跨行不对齐**；矩阵七列要**竖着比**（哪门课队列最深 /
   段内停最久），芯片拼出来的「表」同一列每行宽度不同，比较只能靠读。矩阵复用 `.tc-table` 基础
   样式 + `tabular-nums`；**状态词表与语义档仍归 `matrixStatus` + `StatusDot` 一处**（一个状态只有
   一个说法、一个色）——这条才是「同一语义只有一个原语」的实质。
2. **「在训」判据两半同源，故不设「两表说法不一」的冲突**：服务端把 `trainingFromQueue`
   （调度器存活 ∧ 该课未收官）**同时**喂给 overview 与 loopQueue，它们不会互相矛盾。会打架的是
   **hub 注册与否 × 进程死活**，冲突判定因此建在这两条上。
3. **状态词表择一：`未在训` 胜过 `停`**（同一局面两张表说法不同，合并必须选一个）。`停` 暗含
   「被停过」这个我们**看不到**的事实。
4. **矩阵两区通用**（不再 `isBc` 门控）：它是跨课程表、行自带 BC/RL 徽标；按「当前查看的课
   是不是 BC」隐藏它，等于又回到 C5。

**顺手修掉的真 bug**：原 `rowBadge` 的判据 `!hubSeen && training` 在 **hub 无应答**时也成立
（队列整个读不到 ⇒ `hubSeen` 恒 false），于是每个在训课程都被贴上「hub 未注册」+「以 `--course`
重启 hub」的**假诊断**，而同一张卡的表头正写着「hub 无应答」。新判据以 `hubOnline` 为前提，
配两条回归闸（`web-course-matrix.test.ts` 与 `web-app-coursematrix.test.ts`）。

**P2a 证据**：`bun run test` **804 pass / 0 fail**（90 文件；P2a 前 776 / 88）· `typecheck` ✓ ·
`oxlint` 0/0 · `oxfmt` clean · 三份 bundle 绿（app **71,746 B gzip** / 预算 150 KB）。

### §2026-09-20-dashboard-shell-routing — P2b 续（KPI 条 + 告警坞）

**背景**：总览页首屏没有任何**结论性读数**（胜率/阶段/算力/队列散在 5 块卡里，要滚屏拼），
而页面顶部同时最多堆 **6 条同权重横幅**（停机 / 已恢复 / 训练完成 / PPO 排队超时 / 课程编辑被拒 /
只读），谁更急、哪条已被 ack 过，全靠人读文字判断；`cloudHaltAcks` / `tc.ro-banner-dismissed` 等
本地态也因此散在 3 处各自读写。

**决定**：
1. **横幅收敛为「告警坞」（`components/AlertDock.tsx` + `view/alerts.ts`）**：条目化 + 按严重度
   `err > warn > info > history` 排序 + 默认展 2 条、其余折成「还有 N 条 ▸」；**全空时整坞不渲染**
   （空坞等于给「一切正常」再画一块卡）。本地态读写与判据收进 `alerts.ts` 一处，组件纯渲染。
2. **`title` 与 `detail` 分层而不是删信息**：`title` 是「只读第一行就能决策」的结论，原来那句长文案
   **一字未删**地降到 `detail`——弱化的是排版层级，不是信息量。
3. **`resume`（真调 API）与 `ack`（只写本地）在类型上分开**：两者长得像「按钮」，但一个会改训练
   状态、一个只改本机可见性；混为一谈会在只读/LAN 场景点错。只读提示的 `ack` 走**自己的固定键**
   （`tc.ro-banner-dismissed`），不复用停机那种按事件身份（`cloudHaltAcks[<id>]`）的键。
4. **只读横幅归 `info`，且是坞里唯一的会话属性条目**：它带「关闭」语义（与其它 5 条事件型
   警告不同）——是**会话属性**而非事件。常驻职责仍由侧栏 `tc-lock` 徽标承担，所以关掉它不会
   让人看不见只读态。
5. **KPI 条只给「有时序」的两格画微型走势**（采样胜率 / eval 胜率，52×18 inline SVG，复用仓库
   里早已存在但**零消费者**的 `sparkPoints`）。阶段 / 在训课程 / 算力 / 队列**不补一条平线**——
   平线看起来就是一个「一直没变」的结论，比没有走势更坏；空位留给副读数。
6. **`kpiTiles(state, nowMs)` 用顶栏那个 10s ticker 的 `now`，不取当场 `Date.now()`**：它与顶栏
   阶段 chip 显示同一个阶段的耗时，两处秒数不一致就是 bug。

**顺手补掉一个潜伏 bug（审计 C15）**：`var(--line)` 在 `.tc-cc__scope` / `.tc-mx__singleton` /
`.tc-comps__group + .tc-comps__group` / `.tc-mx__opsep` 四处被用了很久，而 `--line` **从未定义**
——CSS 中无后备值的 `var()` 会让**整条声明作废**，于是两个徽章的描边根本没画、矩阵操作列之间
那条 1px 竖线**完全不可见**（P2a 落地时无人察觉，因为「少一条 1px 线」不会让任何断言变红）。
已在 `:root` 补齐。**教训：`var(--x)` 拼错一个 token 名不会报错，只会静默丢弃整条声明——
新写 `var()` 时先 grep 该 token 是否定义过。**

**P2b 证据**：**863 pass / 0 fail**（92 文件；P2a 后 804 / 90）· `typecheck` ✓ · `oxlint` 0/0 ·
`oxfmt` clean · 三份 bundle 绿（app **75,713 B gzip** / 预算 150 KB）。

### §2026-09-20-dashboard-shell-routing — P3 续（抽屉残留清理 + 独立页共用外壳）

**背景**：抽屉 P0 已删，但它的痕迹分三类留着——① `.tc-drawer*` 一整块 CSS 还在服务两个面板；
② `tc-drawer__panel` 这个类名还在两个面板的根节点上（抽屉不存在了，名字无法自解释）；
③ /eval 与 /log 两个**独立 bundle** 各写一个「返回控制台」链接，与控制台没有共享外壳：从侧栏
点进「评估」后侧栏连同六个入口一起消失，那是**导航断层**（进去就出不来，想去节点页得先回总览）。

**决定**：
1. **`tc-drawer__panel` → `tc-panelbody`**（唯一两个消费者：`MetricsTable` / `NodeStats`），
   `.tc-drawer*` 整块（mask / drawer / __hd / __tabs / __tab / __tab--on / __body / __panel）删除。
2. **抽屉回归闸做成三通道**：① 首帧 `#root` 切片（**不含** head 里内联的样式表）无该串；
   ② `src/web/theme.css` 无 `^\.tc-drawer` **规则**（注释里允许留旧名说明改名史，故只锤行首
   选择器而不锤字符串）；③ 遍历 `src/web/**/*.tsx` 无该串。另加**前提闸**（`html` 必含内联样式表），
   免得 ① 退化成永真。原断言只查 `<aside class="tc-drawer"` 一个具体标签——换标签名、或把类名
   写进字符串拼接就漏，那不是闸是装饰。
3. **抽 `app/shell/NavSidebar.tsx`，而不是让独立页硬套 `Sidebar`**。`SidebarProps` 要的是课程
   选择器 / 触发门禁 / 刷新间隔——那是**控制台才拥有的全局设置**（独立页各自的轮询节奏属于页面
   自己）。硬塞就得上假数据（下拉框里列出假的刷新间隔），拿谎话换整齐。故按「**导航 vs 全局设置**」
   切一刀：导航全站共用（含激活判定与 `?course=` 透传），设置各页自持（由可空 `footer` 插槽注入）。
   导航项的点击回调也做成**可空**：不传 = 原生跳转——客户端 pushState 只能在**同一个 bundle** 的
   路由表内切页，独立页拦截四页导航等于白屏。
4. **`Shell` 由「收 props」改成「收节点」**（`sidebar` / `topbar` 为 `ComponentChildren`）：原签名
   已把外壳焊在控制台的数据形状上，独立页永远进不来。节点化之后外壳真的只剩布局（与它自己的
   头注一致：状态与动作的所有权留在各页 App）。
5. **`PAGES` 扩容为 `AnyPageKey`（四页 + eval/log）**，但保留窄的 `PageKey` 用于路由判定——
   独立页**要读同一份元信息**（否则顶栏标题只能写成散落在两个 bundle 里的字面量，两处描述同一页
   迟早不一致），可它们**不参与本 bundle 路由**（`pageForPath` 对它们返回 null；窄类型保证
   `bootstrapPage` 不可能返回独立页）。`Topbar` 随之退化为可复用：`stateView=null` 时不伪造读数、
   `onRetry`/`onRefreshNow` 可空。
6. **两大独立页删掉自己的「返回 / 回去」链接**（由侧栏六个入口取代），页名改由外壳顶栏给；
   /log 的组件名从 `<h1>` 降为 `.tc-loghead__title`（一面一 h1）。

**顺手修掉的两个真缺陷（都是「接进来才看得见」的那类）**：
- **C16：`.tc-row` 在 theme.css 里有两套定义，且在先的那套被静默覆盖**（同为单类选择器，后者胜）。
  旧版是页级「一行控件」的 flex（`gap: sp-6`）+ 搭档 `.tc-col`（**零消费者**）；新版是 P1 引入的
  芯片行（StatusRow）。后果不仅难看：`TaskBundlePanel` 与 `LogNavCard` 的作者发现「行不成行」，
  就地用内联 `style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}` 打了补丁
  （这直接解释了审计 C10 的一部分实例）。已删死定义，行布局统一用本就存在的 `.tc-line`，
  并拆掉两处内联补丁。
- **C17：`.tc-evalpage*` 三个类名从未有过任何样式**（`git log -S` 查遍历史确认：不是迁移丢的，
  而是写在标记里就没配过）⇒ 评测页从头到尾没有页级容器（贴窗口边缘、无最大宽、无留白）。
  同类：`.tc-wrap` 只剩日志页一个消费者，与外壳 `.tc-main__body` 职责重叠。两者一并删掉
  ——页面容器一律由外壳给。

**顺手记下的测试纪律（C18）**：删掉日志页「← 返回控制台」按钮后，那条
`expect(html).toContain('返回控制台')` **仍然通过**——因为整个 `theme.css` 被内联进 `<style>`，
而新写的一条 **CSS 注释**里恰好写着那几个字。**断言实际检查的是样式表文本**。两个独立页的
测试文件已同规改为先切 `#root` 再断言（与 `web-ssr-console.test.ts::body()` 一致）。

**P3 证据**：**867 pass / 0 fail**（92 文件；P2b 后 863）· `typecheck` ✓ · `oxlint` 0/0 ·
`oxfmt` clean · 三份 bundle 绿（app **75,872 B** / log **16,046 B** / eval **21,049 B** gzip）。

### §2026-09-20-metric-table-single-home（2026-09-20，总览「最新 6 轮」与 `/metrics` 整表的**单一归属**；已决，**已落地**）

**背景（用户要求「把重复收掉、定一个单一归属」）**：总览 Hero 的「最新 6 轮」表与 `/metrics` 的完整
指标表展示同一批数字。查清后区分两层「重复」：
- **页面层：不是重复。** `docs/dashboard-redesign.md` §3.3（已批准规格）故意同时保留两者，职责不同：
  总览 = 「最新 6 轮」**速览**（全宽、可折叠、主行/eval 双视图、evalA 入队、导出 replay）；
  `/metrics` = **全部 iter 的完整表**（搜索 / 排序 / 列显隐）。删掉总览那份 = 推翻已批准规格里
  的「最新 6 轮指标表：Hero 现有表格原样保留」。
- **代码层：是真重复。** 同一套 15+ 列被**手写了两遍**：`Hero.tsx` 是 15 个 `<th>`（含各自的
  `title` 口径文案）+ 逐格 `<td>` 格式化；`MetricsTable.tsx` 是另一套 `Col<MetricRow>`
  （`label` + `cell` + 排序/显隐）。两套**当前标签一致**，但那是人手抄得仔细的结果，不是结构保证
  —— 改一处漏一处的漂移是时间问题（与 C6「动作反馈 6 处手写」/ C14 同一类缺陷）。

**决定（owner 2026-09-20 拍板，选项 1）**：**保留总览速览，只收「实现重复」。**
- 单一归属 = **列模型**（每列的 `label` / `title` 口径文案 / `num` 对齐 / 值格式化）只留一份在
  `view/`；两个页面都从它取。总览渲染它的「最近 6 行 + 少量列」**投影**，`/metrics` 仍是
  「全量 + 搜索/排序/列显隐」的唯一权威。
- **不**删总览那份表（不推翻 §3.3）、**不**把 DataTable 搬进总览（那要给公共组件加 minimal 模式，
  影响面大于收益）。
- 落地形态（待做）：`view/metric-col-specs.ts`（纯数据，无 JSX：`key/label/title/num/side`）→ `Hero`
  主行表与 eval 表改为按 spec 渲染表头并按 `key` 取值；`MetricsTable` 的 `Col` 改为 `{ ...spec,
  cell }`（保留它自己的排序/显隐/配对逻辑）。验收：两处表头集合由 spec 驱动，且新增一条
  **源文件级闸**（禁止再出现手写的 `胜局耗时`/`承伤/杀` 类表头字面量两份）。

**已落地（2026-09-20）**：
- 新增 `src/web/view/metric-columns.ts`：`METRIC_COLS`（23 列 × `label`/`title`/`num`）+ 两个顺序数组
  （`MAIN_ROW_KEYS` / `EVAL_ROW_KEYS`）+ `colsOf()`。配对四列与过拟合列的 `title` 仍引用
  `view/format.ts` 的既有常量（那份“防两处分化”的先例就是本次扩张的模板）。
- `Hero.tsx`：两张表的表头改由 `colsOf(...).map(colHead)` 生成（文件里只剩 colHead 一处 `<th>` 模板）；
  空态 `colSpan` 也改由模型算 —— **顺手修了一个没人会发现的 bug**：此前手写 `colSpan={15}`，
  而 eval 表实为 16 列，空态那一行一直少跨一格。
- `MetricsTable.tsx`：每个列壳改为 `...colDef('<key>')`，本文件零内联 `label`/`align`/`thTitle`。
- 新用例 `tests/web-metric-columns.test.ts`（11 例）三层：模型完整性 / **源文件级闸**（Hero 只剩
  1 处 `<th>` 模板；MetricsTable 零内联列字段）/ **渲染级闸**（首页主行表表头逐列 = 模型；指标页
  表每个表头都能在模型里找到且口径一字不差；**指标页 eval 模式的列序逐列等于 `EVAL_ROW_KEYS`**
  ——这一条是「两处 = 同一份」的交叉证据）。
- **迁移中的自伤与拦截**：我建模型时漏给 `entropy` 标 `num`，直接把 `/metrics` 的熵列从右对齐
  改成左对齐；被新用例的数字列断言拦住。另有两处是**测试自己的错**（这也是值得记的）：`<th([^>]*)>`
  会先匹配到 `<thead>`（断言全错位），以及「零 `<th>`」的源文件闸把我自己的注释也算进去了。
  教训：结构类断言要么看语义（`key`/顺序），要么就得让正则真的只匹配目标标签。

**两处口径变更（有意统一，非意外）**：① `熵` 列在指标页原标 `entropy`，现统一为 `熵`（与另一侧的
中文列名一致）；② `承伤/杀` 的 hover 口径两处原本略有出入（总览多一句「越小越会周旋」），现两边
都用更完整的那一句。

**证据**：**878 pass / 0 fail**（92 文件；本次前 867）· `typecheck` ✓ · `oxlint` 0/0 · `oxfmt` clean ·
三份 bundle 绿。

### §2026-09-20-dashboard-shell-routing — P3d 续（`LogNavCard` 下线：日志入口的单一归属）

**背景**：P0 遗留 ③ —— 总览底部的「组件日志入口全集」面板（`LogNavCard`）与组件卡行内的
「≡ 日志」链接职责重叠，定案留给了 P3。

**决定：下线 `LogNavCard`**。判据不是「页面太挤」，而是**同一语义两个入口**：
1. 组件卡行内已有指向 `/log/<key>` 的链接，同一页再列一遍同一组入口 = 一语义两入口。
2. 它那句「日志页独立轮询（follow 2s / 关 4s），上滚读历史不被拉回」描述的是**日志页自己的行为**，
   写在总览页本属错位——已并入组件卡「≡ 日志」的 `title`（入口与它的说明住同一处）。
3. **「全组件」并不由它承担**：日志页内自带 `<nav class="tc-logtool__nav" aria-label="组件">`
   的逐组件 chips，从侧栏「日志」进去即可切任意组件 ⇒ 下线不丢任何路径。
连带下线 `.tc-preset` / `.tc-preset--on` / `a.tc-preset`（唯一消费者就是它），CSS 里留一条注释
记明去向，免得日后被当成悬空规则重加。

**新发现（写在这里是因为它决定闸的形态）**：组件卡行内的日志链接是**条件渲染**的——
`exited && error` 走 ⚠ 入口（也指向 `/log/<key>`）、`running` 走 ≡ 入口、`stopped` **两个都不渲染**。
所以「行内入口覆盖全部状态」是假的，**真正的兜底是侧栏「日志」+ 日志页组件导航**。

**能力保全闸（此前零覆盖）**：面板下线前，本仓**没有任何断言**盯着「每张组件卡能去自己的日志页」
——链接再被删掉也不会有测试变红，最后一条路径会默默消失。新闸分两层：
- **渲染级**：fixture 中「退出且有错」的组件的 ⚠ `href` 按 key 指向自己的 `/log/<key>`；
  且侧栏「日志」入口在场（一个进程都没跑时它是**唯一**入口）。
- **源文件级**：`ComponentCards.tsx` 里 `href={logHref}` **恰好 2 处**（exited ⚠ + running ≡），
  任一支被拆掉即红。
- **日志页内组件导航（真正的全集载体）也要逐个断言**：`web-ssr-log-page.test.ts` 此前只查
  `toContain('/log/trainingLoop')` **一个链接**——nav 退化成一个 chip、或某组件被漏掉，它照样绿。
  已改为「逐组件断言 + `tc-lognav` 计数恰好等于 `state.components.length`」，并加一条**前提闸**
  （组件数 > 0），免得两者同时为 0 时退回永真。

> 初版闸写成 `expect(dom).toContain('aria-label="日志 ')`，**红了**——因为 fixture 里没有任何
> `isRunning` 的组件。这次红灯不是噪声，它暴露的正是上面那条「条件渲染」事实，闸据此才拆成两层。

**证据**：**878 pass / 0 fail**（93 文件）· `typecheck` ✓ · `oxlint` 0/0 · `oxfmt` clean ·
三份 bundle 绿（app gzip **76,297 B**，本次 −224 B / log 17,386 B / eval 22,377 B）。

---

### §2026-09-20-dashboard-shell-routing — P4a 续（字号阶梯单一化 + 内联样式清零 + 样式纪律闸）

**背景**：P4 视觉精修的 DoD 有两条硬口径——「正文 ≥ 13.5px；数字列 `tabular-nums`」与
「无 tsx 内色值字面量；无 `style={{ margin/gap }}` 类布局内联」。计划里的底数（「10.5px ×7、11px ×1」
/「31 处内联」）只数了 `theme.css` 与对象式内联，**实测差一个数量级**。

**决定 1：字号阶梯 = `--fs-*` 唯一一份，旧别名 `--fs-1..--fs-5` 彻底删除。**
- 实测 79 处消费者在用旧别名，新阶梯只有 20 处——所以这个阶段的**主体不是「替换字号」，是「迁消费者」**。
- 阶梯：`xs 11 / sm 12 / base 13.5 / md 15 / lg 18 / xl 24 / 2xl 30`。119 条 `font-size` 声明
  **全部**改取 `var(--fs-*)`（零字面量 px）。
- **「正文 ≥ 13.5px」的判定口径写死在此**，免得下一个人重新争：`body` / 表格 `td` / 按钮 /
  输入框 / 开关 / 横幅 = `--fs-base`（13.5px，旧基线 11.5–12.5px 已抬到可读线）。
  留在 11px（15 条：胶囊/徽标/kicker/脚注）与 12px（63 条：标签、表头 `th`、说明文字、小号按钮）
  的均**非正文**，逐个判过；表头小于单元格是有意的（标签在数据之上）。

**决定 2：内联布局 style 只允许「计算值」，其余进 CSS 类。**
- 实测 **80 处**（45 对象式 + 30 `style={常量}` 式 + 5 字符串式 `style="…"`），不是 31。
- 唯一保留：`TrendChart` 的 3 处——图形 `height`、提示框按 x 比例 `left`。**SVG 坐标系运行时
  才知道，写不成静态类名**；这是「计算值」的判据，不是「这文件特殊」。
- `data-*` 常量式内联（`style={TH}`）与字符串式内联全部清零。

**决定 3：新建源文件级纪律闸 `dashboard/tests/web-style-discipline.test.ts`（11 例）。**
这是本仓库**第一个**盯「样式纪律」的测试——上面两条都是人手一次性扫全量做出来的，
而**任何渲染级断言都不会因此变红**（样式表被内联进 SSR，裸类名断言恒真）。三组：
1. 字号：阶梯定义在场（前提）· 每条 `font-size` 必带 `var(--fs-` · 档位严格递增 · 旧别名
   定义与消费者均不得复活。
2. 内联：除 `TrendChart` 的 **3 处**（写死次数）外 `style=` 为 0 · 无字符串式内联 ·
   豁免处确实是计算值（`height: '12px'` 这类能写成类的要红）。
3. 色值：十六进制字面量只允许两个文件并限次数（`render.tsx` 店标 SVG ×8、`TrendChart`
   SVG 白描边 + `var(--eval-line, #…)` fallback ×2）· 字符串里的 CSS 颜色（`color: #…` /
   `rgba(…)`）一律不许——**豁免只豁免 SVG 呈现属性**。

**为何用「文件 + 次数的白名单」而不是「全绿」**：这两个豁免是真的（店标配色不跟主题走；SVG 标记
要白描边），一律禁止只能靠把真豁免也改坏来满足。写死次数让**第 4 处/第 3 处成为一次显式决定**，
而不是顺手加一个。

> **闸验证过会红**：落盘后把 `font-size: 11px` 与一个 `style={{ marginTop: 8 }}` 注回去跑，
> 两条分别 `(fail)`，随后恢复（残留计数 0）。前几轮吃过「闸写错但全绿」，这次先坏后好。
>
> 两个正则坑一并记下（下一个写同类闸的人会撞上）：
> ① 三位色值正则会把 HTML 数字实体 `it&#123;N&#125;` 里的 `&#123` 当成 `#123`（假阳）——需 `(?<!&)`；
> ② `font-size` 断言必须只匹配**声明**（`font-size:` 到分号），全文找名字会把自己的注释判红。

**证据**：**889 pass / 0 fail**（94 文件；P4a 前 878 / 93）· `typecheck` ✓ · `oxlint` 0/0 ·
`oxfmt` clean · 三份 bundle 绿（app gzip 75,877 B · log 17,386 B · eval 22,377 B；预算 150 KB）。
**未做**（P4 余下）：三档响应式实测、空态四态审计、`?` 帮助与快捷键、README 目录树段落。

---

### §2026-09-20-course-enable-marker-and-training-pills（2026-09-20，用户报障 + 用户指令：课程开训必须手动开；在训课程显示为 pill）

**背景（上一条落盘当天就撞上的两个洞）**：

```
启动trainingloop 成功后，界面显示一堆课程正在训练！！！ 课程开训需要用户手动开启！！！
正在训练：c6-chip、remote-smoke、x1-rebirth、…（21 门）
```

- **① 课程表判据错了**：共享 trainer 是**发现式**的（扫 `<traj-root>/*/training_log.jsonl`），tmp/ 下堆着
  几十门历史课的账本 ⇒ 进程一起就把它们**全部**拉进训练；hub 同理（`_course_dir_live` 只看
  `remote-jobs/` 存在且新鲜）⇒ 残留的 pending job 还会被继续派给真 GPU worker（白烧租约）。
- **② 顶部没有「哪几门在训」的样子**：旧形状把其余在训课程堆成一串文字（`正在训练：a、b、c`）
  ——读不出各自进度，也没有停课入口（停课挂在「当前查看的那门课」的按钮上）。

**用户指令（原文）**：「课程开训需要用户手动开启」·「顶部课程 select 选择某课程，点击「训练」按键；
正在训练的所有课程，都在顶部显示为一个 pill，概览显示 it 数和状态（参考节点 pill），有停止按键，
点击 pill 后切换显示其趋势图和指标表，并高亮 pill；点击停止按键后，停止课程，从顶部区域移除」。

**定案**：

1. **开课 = 一个显式标记文件**：`<traj-root>/<课>/training-enabled.txt`（`remote/protocol.py::COURSE_ENABLE_MARKER`，
   控制台「开课」写 /「停课」删）。训练侧（`rl/loop_plan.enabled_courses` → `loop_serve` 发现模式、
   `run_rl_cluster.py --json` 只读课程表）与 hub（`_course_dir_live`）的判据统一改成
   **账本 ∧ 开课标记**；`discover_courses`（盘上跑过哪些课）**保持不变** —— 课程下拉仍要能选历史课。
   照落点选目录内的独立文件（而不是共享 JSON）：一个判据、一处位置，开/停各是一次文件操作
   （无读-改-写竞态），控制台重启不丢「哪几门开着」，且它同时是**证据**（写入了开课时刻）。
2. **控制台的服务端事实同源**：`/api/state` 的 `trainingCourses` 改成**已开课课程**（标记扫描），
   与 `courseLifecycle.enabled` 同一表达式——它同时供课程 select 的 🔥 标记、在训 pill 行、
   `CourseOverview` 的「在训」列与「触发门禁」开关的可见性（一处算，四处用）。旧口径
   （「共享 trainer 在跑 ∧ 队列未收官」）回答的是另一个问题：进程没跑时已开课的课会从顶部消失。
3. **顶部形状**：课程 select + **「训练」按键**（弹 `OpenCourseModal` 收课程级选项：训练模式 /
   rollout 位置 / 降级本机；已在课程表时再点 = 按当前选项重写旋钮并重置 hub 模式）+ **在训 pill 行**
   （顶栏正下方，与节点 pill 同一套视觉语言）：每门课一个 pill = 状态点 + 课名 + `it<N>`（账本指针，
   不是已结算轮数）+ 一句状态（采集中 / 等回传 / 推进中 / 空闲 / 待进程 / 已暂停 / 已收官 / 已中止 /
   视图不可用）+ ■ 停课。**点 pill 切查看目标**（Hero 趋势 + 抽屉指标表随查看课程走）并高亮；
   ■ 停课走 `stopCourse` 且**显式带课程名**（不走「当前查看课程」兜底——点 A 课的 ■ 必须停 A），
   服务端下一拍 stamp 里不再有它 ⇒ pill 自行消失（**不做本地乐观删除**：队列/账本一个字没动这件事
   只能由服务端事实说话）。没有在训课程时整行不渲染（空行会被读成「有东西没加载出来」）。
4. **停课 = 删除开课标记** + 暂停意图 + 该课 hub 置 offline（仍是**非破坏**：队列/账本/课程表不动，
   恢复走开课）。这是上一条停课语义的**必需补充**：只写暂停意图而不删标记，发现式的训练进程
   下一拍又会把这门课拉起来。

**备选与否决**：

- **判据用「控制台自己记一份在训名单」**（`console-state.json` 里加 `enabledCourses`）——否：训练侧与 hub
  必须知道同一件事，而它们读不到控制台的状态文件 ⇒ 只能各记一份（正是「一启动 21 门课」的成因）；
  盘上的标记是三方（trainer / hub / 控制台）都能读到的那一份。
- **判据用「有没有账本 mtime 新鲜」**——否：历史课目录随时因为动过账本而变新鲜，正是最贵的那类误派
  （真 GPU worker 白烧租约）；且 freshness 是滑动的，操作员意图（开/停）会被时间悄悄改写。
- **停课 = 删账本 / 改课程文件**——否（同上一条：阅读面连带消失、历史归零）。
- **在训 pill 行的数据源用 python 的 `run_rl_cluster.py --json` 一门定生死**——否：它要起子进程（10s TTL），
  读失败时 pill 会整行消失——**停课入口不能因观测面坏了就消失**。故 pill 的全集来自服务端 stamp 的
  标记扫描（纯 fs），it/状态来自队列行（读面不可用时它才降级为「视图不可用」，pill 仍在、仍可停）。
- **pill 上只给状态点不给 it 数**——否：多课程并行时操作员第一眼要看的就是「各自跑到第几轮」
  （用户原话「概览显示 it 数和状态（参考节点 pill）」）。
- **点 pill 时也把 localStorage/服务端操作员课程一并改掉**——否：那会把「看哪门课」变成持久副作用
  （切一下看一眼就把动作目标换掉了）；`selectCourse` 本来就是「本浏览器查看 + 本机同步操作员课程」
  的既有语义，pill 复用它。

**违反后果**：把开课标记退回「有账本」⇒ 一次「启动服务进程」就把 tmp/ 下全部历史课拉起来跑（并让 hub
继续派残留 job）；停课不删标记 ⇒ 点到「停课」后课程自己又回来（比没有停课更坏：操作员会以为停不掉）；
在训名单在 TS 里按进程存活重算 ⇒ 进程一停已开课的课程全从顶部消失（开课与进程是两件事）。

5. **「空课程表」必须回契约形状**：默认表 = 已开课的课，而开课是显式动作 ⇒「一门课都没开」是启动后的
   **第一种状态**。只读入口 `run_rl_cluster.py --json` 的空表分支原本回一行人话，而控制台是直接
   `JSON.parse` 这段 stdout（`server/api/loop-queue.ts`）⇒ 在默认状态下常年报「输出不可解析」的红错。
   定案：空表也回 `{"courses": [], "pools": {...}}`（形状在所有分支一致；池容量是进程事实，与有没有课无关）。

**落地**：`nn-training/remote/protocol.py`（`COURSE_ENABLE_MARKER`）· `nn-training/rl/loop_plan.py`
（`course_enabled` / `enabled_courses`）· `nn-training/rl/loop_serve.py`（发现模式两处扫描）·
`nn-training/run_rl_cluster.py`（只读课程表）· `nn-training/remote/hub_server.py`（`_course_dir_live`）·
`dashboard/src/server/actions/course-lifecycle.ts`（标记写/删）· `dashboard/src/server/api/state-view.ts`
（`trainingCourses` = 已开课）· `dashboard/src/web/view/loop-queue.ts`（`coursePills` 纯函数）·
`dashboard/src/web/app/panels/TrainingPills.tsx`（新）· `dashboard/src/web/app/app.tsx`（顶部形状）·
`dashboard/src/web/theme.css`（`.tc-tpills` / `.tc-tpill`）。

**回归**：`nn-training/tests/test_loop_plan_waiting.py`（+2 例：空课程表的 `--json` 形状 · 默认课程表 = 已开课）·
`dashboard/tests/training-pills.test.ts`（新，14 例：pill 推导四态 + 确定性事实优先 +
「待进程」/「视图不可用」不编造 · `trainingCourses` = 标记而非账本 · SSR 上屏/高亮/BC 徽标/停课键/
空行不渲染/顶栏只有「训练」）· `course-lifecycle.test.ts`（+2 例：开课写标记、停课删标记、可逆）·
`web-ssr-readonly.test.ts`（旧 `tc-training-tag` 用例改写为「select 标记 + pill 行」）·
nn python：`test_multi_course_hub.py`（`_enable` 助手 + 新回归「没开过课的目录不进课程表」）·
`test_serve_wiring.py` / `test_offline_task_pack.py` / `test_worker_offline_cap.py`（造课目录的助手补写标记
——它们钉的是各自那件事，不是「没标记也能被发现」）。

**gate**：dashboard **752 pass / 0 fail** + `tsc --noEmit` + oxlint 0 warning + 三份 bundle 构建通过
（app 296114B / gzip 69219B）；根 `bun run check` 绿；nn python 全量 **0 failed**（`bash tools/githook/nn-py-safe.sh`）。

---

### §2026-09-20-console-decision-log（2026-09-20，用户指令：控制台的启/停/重启/判死要写日志文件）

**背景**：上一节那条「同窗口 hub 停服」的复盘最后一公里——hub 被**人工**停掉后，盘上的证据
（组件日志 + 账本 + console-state）只能显示「日志断在半分钟前」「账本条目没了」「没有意外退出
标记」，**分不出**「人工停的」与「自己死的」，只能反过来去问操作员。根因不是「没写日志」：
`core/log.ts` 早就是 stdout + 文件双写，但

1. **控制台从没 arm 它**——只有 `launch/cli.ts`（一次性 python 启动）调 `initLog(tag)`；
   `bun run dashboard` 走的是 `server/server.ts::main`，`logFile` 恒为空串；
2. 监督器 / 启动对账 / 请求异常的判决用**裸 `console.*`**，连双写都绕过；
3. **动作结果不落任何盘**——「谁在何时点了停/启/开课」只存在于 UI 回执与终端滚屏。

**决定**：

* `core/log.ts`：新增 `initConsoleLog()`——稳定文件名 `tmp/training-start/console.log`，
  每次启动追加一行会话头 `=== console session <ISO> pid=N ===`，启动时超过 8MB 轮转一代
  `.1`，任何 IO 失败 best-effort 不抛（**日志写不进绝不能搞停控制台**）；新增 `error()`
  （stderr + 文件，与 log/warn/fail/info/ok 同规）。路径走 `paths.consoleLogPath()`
  （惰性；`BCITY_CONSOLE_LOG` 可重定向，单测不污染仓根 tmp/）。
* `server/server.ts::main`：在**任何决策之前** arm，并把路径打进启动横幅（不然没人知道去哪看）。
* `server/api/route.ts`：唯一动作出口 `routeAction` 拆成 `dispatchAction` + 统一落盘——
  **每个动作结果一行**（`[action] stop hubServer → ok (HTTP 200): …`，失败走 `warn` 带 ⚠️），
  纯读接口 `getGateHaltMode` 排除（高频回读不是决策）。
* 决策站点（`server.ts` / `exit-watchdog.ts` / `stack/hub.ts` / `route.ts` / `actions/stop.ts`）
  不再有裸 `console.*`（接线门禁钉着）。

**备选与否决**：

* **每次会话一个新文件**（沿用 `initLog(tag)`）——否：操作员要的是「一个固定路径能翻到全部
  决策」；会话边界用会话头表达，不必用文件名，顺带避免了百来个 `console-<ts>.log` 堆积。
* **让操作员自己重定向 stdout（`bun run dashboard > x.log`）**——否：默认没人重定向
  （2026-09-20 的现场就是这样），而且终端滚屏一关就没了。
* **只记组件动作（start/stop/restart），不记其它 POST**——否：同一出口记全部才不漏
  （「谁把课程切走了」「谁改了节点并发」同属决策），成本是一行/点击；只把纯读接口排除。
* **引第三方日志库（分级/滚动）**——否（MANIFEST §14 零依赖精神）：`appendFileSync` + 一次
  启动轮转足够，且行为可读。
* **把日志按组件分文件**——否：决策的因果链跨越组件（「停 hub」→「trainer 等不到结果」），
  一份按时间排的流水才能看出因果。

**gate**：dashboard **785 pass / 0 fail**（新增 `console-decision-log.test.ts` 24 例：会话头 /
追加不截断 / 阈值轮转 / 写不进不炸 / 路径惰性；接线门禁：决策站点无裸 `console.*`、`route.ts`
唯一出口且结果落盘、`initConsoleLog` 早于 `startSupervisor` 调用、真动作的结果落盘）·
`tsc --noEmit` · oxlint 0 warning · 真盘探针确认默认路径可写、会话头与两级行如期落盘。

---

### §2026-09-20-node-health-by-completed-round-contrib（2026-09-20，用户指令：节点 pill 的贡献数取上一轮**完成**值，健康度按「贡献 vs 并发」重定义）

**背景**：节点 pill 行有两处口径都不对：

1. **贡献数取的是进行中那一轮**——对齐基准 `globalMaxIt` = meta 里所有节点成功结算的**最大 it**。
   轮次是**逐局结算**的，进行中那一轮的行一直在变多：先交活的节点数字漂亮、还没轮到的显示 0
   （看着像掉线，而机器完全健康）。
2. **健康度取的是 ping**——1.5s `/v1/ping` 超时 ⇒ 标「慢 / 离线」。ping 只是**可达性**；
   「上一轮到底交没交活」才是节点可用性的直接事实。

**决定**：

* **对齐基准轮 = 最近已完成轮**：完成水位 = 同一训练流目录 `training_log.jsonl` 里最后一个
  `iteration` 事件（训练循环在轮末写，正好是「这一轮账已结清」的判据）。只认 `iteration`——
  hub 追加的 `job_completed` 也带 `it`，照它取会读出「还没跑完的那一轮」
  （`latestIterFromLedgerTail` 的既有口径）。
  安全阀：水位缺失 / 为负 / **在 meta 里没有任何行**（账本与 meta 不同步）⇒ 退化为 meta 最大 it
  （旧口径）；硬用水位会把**全员**算成 0 贡献，那比退化口径糟得多。
* **健康度 = 纯函数 `nodeHealth(贡献, 并发)`**：贡献 0（或无池数据 `-1`）⇒ 离线；`≥ 并发` ⇒ 健康；
  其间 ⇒ 缓慢。pill 行据此三桶（健康 / 缓慢 / 离线，缓慢与离线仍不折叠），**ping 出局**
  （`NodeView.slow` 保留给 API / 节点统计表，pill 不再消费）。
* **local（本机直跑）同口径**——它也是一个 rollout 执行面；`rl.local_slots = 0`（直跑未启用）时
  并入「停用」折叠桶（不再占行内位置）。槽位数仍恒出（§361⑤ 的展示契约不回退）。
* **并发数前的 `✓` 去掉**（它把「并发数」误读成「在线数」）：并发数恒出，状态词另起一格
  （`a95 2 缓慢 | 1`）——判据的两个数当场可核对；「停用」仍顶替数字列。

**备选与否决**：

* **用「meta 最大 it − 1」当完成轮**——否：轮次不一定连续（换流 / 重跑 / BC 与 RL 交错），
  减一可能落在没有行的轮 ⇒ 全员 0 贡献。
* **时间静默判完成**（最近 N 秒无新行 ⇒ 该轮算完成）——否：阈值是猜的，且慢节点会把
  「还在跑」读成「已完成」。
* **按 mode 各取完成水位**（rollout / eval 分开）——否：eval 没有自己的完成事件（它与 PPO 重叠，
  行会在 `iteration` 之后继续落），分开只会做出一个「更精确但同样在漂」的水位；同基准轮下
  eval 的尾行会在下一次刷新自然补全。
* **ping 参与判定**（如「贡献 0 但 ping 通 ⇒ 慢」）——否：那正是本次要消除的混淆
  （ping 通而零贡献的节点是真没在产出）。
* **健康节点写字「健康」**——否：绿点 + 并发数已经表达；字只留给异常态（缓慢 / 离线）。
* **给停用行做减法（隐掉贡献数 / 并发数）**——否：重设计后的行是「同一形状、同一位置」
  （`StatusRow` 原语），停用行仍出值列「停用」+ 元信息「上轮 N」；数字是事实，判断由
  色 / 形 / 词给出——把数字藏掉反而让「它上轮还在贡献」这条信息消失。

**违反后果**：回到「按最大 it 取对齐基准」⇒ 每轮开头那几十秒健康节点集体显示 0，
操作员去查一台没问题的机器；用 ping 判健康 ⇒ 算力受限 / 网络抖动的节点被反复标红、
而真正零贡献的节点因 ping 通而看起来正常（本次的起因）；用 `readLogTail`（**展示**口径，
500 字符截断）读账本 ⇒ `iteration` 单行 >1.2KB，JSON 不可解析 ⇒ 完成水位与总览「轮次」列恒为空。

**顺带修掉的（同一根因）**：`readLogTail` 的 500 字符截断被误用在机器解析上 ⇒ `courseIter`
对**每一门课**都返回 null（总览「轮次」列恒显 `—`；x20-steady / c6-chip 实测）。新增
`readLedgerTail`（不截断）并把 `courseIter` 切过去；`readLogTail` 加 `maxLineLen` 参数
（默认 500 保持展示行为不变）。

**落地**：`core/paths.ts`（`tmpPoolDir()` + `BCITY_POOL_DIR`——完成水位要用**真目录树 + 真账本**
才能验，夹具写进仓根 tmp/ 会被真实训练流淹没、也会反向污染别的用例的活跃流判定）·
`server/pool-history.ts`（`lastCompletedIter` / `pickBaseIter` + 对齐基准换水位）·
`server/api/logs.ts`（`readLogTail(maxLineLen)` + `readLedgerTail`）· `server/api/overview.ts`
（`courseIter` 走 `readLedgerTail`）· `server/api/snapshot-cache.ts`（池历史**聚合一次**——
此前同一份数 MB meta 冷算两遍：慢节点判定一遍、贡献数一遍）· `web/view/console-types.ts`
（`nodeHealth` 纯函数，客户端安全）· `web/app/panels/NodePills.tsx`（三桶 + 去 ✓ + local 折叠 +
贡献数上非健康 pill）· `web/app/panels/NodeStats.tsx`（陈旧节点 tooltip 与新基准对齐）·
`web/theme.css`（`.tc-npill__state`）。回归：`tests/web-app-nodepills.test.ts`（14 例，改写为
贡献口径 + 三态 + local 折叠 + 无 ✓）· `tests/server-pool-history.test.ts`（+5 例：完成水位 e2e、
无账本退化、安全阀、只认 iteration、`pickBaseIter`）· `tests/server-api-logs.test.ts`
（+1 例：长行不截断）。**gate**：dashboard **808 pass / 0 fail** · `tsc --noEmit` ·
oxlint 0 warning · 三份 bundle 构建通过 · 根 `bun run check` 绿。

---

### §2026-09-20-merge-origin-goal-nn — 合并 `origin/goal-nn`：在训 pill 行 × 控制台重设计（IA 相撞的裁决）

**背景**：本地 6 个重设计提交（P0…P4a，`2450301`…`5898936`）与 `origin/goal-nn` 的 7 个提交在
`f883afb` 分叉。远端那条线**也动了 dashboard**——「课程开训改为显式开课」（进程与课程解耦）、
「在训课程 pill 并入顶栏行」——但它们做在**被 P0 重写掉的老 `app.tsx`** 上（抽屉 + 顶栏居中课程
select）。因此这不是文本冲突，是**两个版本的 IA 相撞**：11 个冲突块 / 5 个文件里，有 4 块是布局与
归属之争，不是标点之争。

**总则（先定后裁）**：**重设计的 IA 胜**（它是已批准的规格 `docs/dashboard-redesign.md`）；
远端的**功能与语义原样保留、重新安放到新 IA 里**。不推翻重设计去迁就旧布局，也不因布局不同
丢掉远端功能。理由：规格是 P0 就定下的、已落地 8 个阶段的骨架；远端那 7 个提交是**功能**提交，
它们的价值在行为（开课/停课、pill 的 it 与状态），不在那个具体容器。

**逐项裁决**：

| 面 | 裁决 | 为什么 |
|---|---|---|
| `app.tsx` | 取我方（新外壳）为底，移植远端 handler/state（`openCourseModal`、`handleOpenCourse`/`handleStopCourse`、`courseLifecycle`、`trainerRunning`），`handleLaunch` 瘦身（课程级旋钮不再随启动走） | 新外壳是 8 个阶段的骨架，不可回退；远的 handler 是纯行为，可平移 |
| 在训 pill 行 | 进**顶栏**：有在训课时它替代「在训 n/N」裸计数 chip | 「同一事实只上屏一次」——pill 是那个计数的明细版（本仓 P3d 下线 `LogNavCard` 同一判据） |
| 开课键（「训练」） | 进**侧栏**，紧贴课程选择器 | 它作用的对象就是选中的那门课（课程级动作跟主语走）；两侧各自保持「一屏一问」 |
| 停课 | 只在 pill 上 | 与远端一致；一个键同时做「开这门/停这门」在两门课并存时语义不明 |
| 「还有在训：a、b」串行标签 | **下线**，`.tc-training-tag` 样式随之删 | 被 pill 行取代（远端已删，取远端；我方的重命名随之作废） |
| select 的 `（正在训练）` | 改 `（已开课）` | `trainingCourses` 的语义 2026-09-20 已改为**开课标记**（与进程是否在跑正交） |
| `trainingCourses` 取值 | 取远端 `stateView.trainingCourses ?? []`，**删我方「trainer 在跑就当作这门课在训」的回退** | 该回退会给从未开课的课挂上 pill，而 pill 的停课键会真去停它 |
| `TrainLaunchModal` | 取远端拆分（训练模式/rollout 移入开课弹窗），叠我方 P4a 的内联样式纪律 | 两件都对：拆分是功能，`tc-mt-0` 是纪律 |
| `theme.css` 那块 | 取远端删除（串行标签样式） | 见上 |
| `server.ts` | 两边 import 取**并集** | 各自新增（我方 `pageForPath` 路由 / 远端 `ProcSpec`+`RegistryEntry`） |
| 并入库的远端样式 | 旧字号 token 迁到我方阶梯（`--fs-2`→`--fs-sm`、`11px`/`10px`→`--fs-xs`）；`OpenCourseModal` 6 处内联样式改走已有类（`tc-banner--flush`/`tc-mt-0`/`tc-launch__lbl`/`tc-hint`） | 并进来的是 P4a **之前**的字号口径与内联习惯；不迁就会让 `var(--fs-2)` 变成**未定义**（静默丢字号），而纪律闸允许 `--fs-` 前缀，不一定报错 |

**★ 顺手抓到一条假绿（远端测试）**：`training-pills.test.ts` 的「pill 与课程 select 在同一行」
用 `indexOf('tc-topbar__row')` / `tc-topbar__course` 定位——这两个类名合并后**只存在于内联进首帧的
`theme.css` 里**（我们的外壳叫 `tc-side`/`tc-top`），于是它靠**样式表**满足：DOM 完全错位也照样绿。
已改为先切掉 `<head>`，再断言「`tc-side` 内 课程选择器 → 开课键」「`tc-top` 内 pill 行」
（沿用 P3b 起的 `#root` 切片纪律；C15/C16/C18 同款，已记进审计）。

**★ 顺手抓到一条 flake（远端测试）**：`training-pills.test.ts` 的 `courseLifecycle?.enabled`
用的是**不传参**的 `buildStateView()`——`course` 由 `effectiveCourse(state, courses)` **推**出来
（默认 = 最近活跃课），于是结论挂在真实工作目录的残影上；`--parallel` 下同进程其它文件会写真实
`tmp/` 与 `nn-training/*.lock`。实测：**修前 4 跑 2 红**（另带 `server-api-overview` 一条同源假红），
**修后 10 跑 10 绿**（964 用例 / 98 文件）。修法是把课程**钉死**（`buildStateView('c6-chip')`）+ 写明
理由，而不是放宽断言。

**残留风险（已知，本次未处理）**：**12 个测试文件**各自改 `process.env.BCITY_REGISTRY_FILE`，
夹具另改 `BCITY_RL_CONFIG`/`BCITY_CONSOLE_STATE`，而被测模块**调用时才读**这些变量——
`--parallel` 隐含的 `--isolate` 只隔离 global，**不隔离 `process.env`**，同进程其它文件仍会互相盖掉。
旁证：**去掉 `--parallel` 跑同一套会红 30+ 条**（per-file 隔离是这套夹具的前提）。系统性做法是给这些
路径换成显式传参的注入缝（本仓 `releaseTrainerLock` 有先例），**留作后续任务**——不在合并里做这种
跨 12 文件的基建改造。

**门禁（合并后实测）**：dashboard `964 pass / 0 fail`（98 文件，10 连跑全绿）· 根 `bun run check`
`1934 pass / 4 skip / 0 fail` · nn python `1860 passed / 3 skipped` · 三份 bundle 绿
（app gzip **78,006 B** / 日志 17,401 B / eval 22,394 B，预算 150 KB）· oxlint 0/0 · oxfmt clean。

---

---

### §2026-09-20-console-declutter（2026-09-20，用户指令：课程区只列在训课程 / 服务面不显示共享·单例 / 删掉关键指标区）

**背景**：控制台首屏纵向空间被三处「重复或索引式」的内容占掉——课程表里几十门未在训的历史课
把在训的那几行挤出屏幕；服务面每行重复一个「共享/单例」（一族里每行都同一个值）；KPI 条六格是
「索引」型面板（每个数都能在 Hero / 节点行 / 课程表头读到同一个值）。三条一起收。

### ① 课程区只列在训课程

- **判据只有一个出处**：`course-matrix.ts::rowTraining(ov, lq) = ov?.training ?? lq?.training ?? false`
  （与状态列 `matrixStatus` **同一个函数**）。新导出 `isTrainingRow(row)` 供面板筛选——
  「哪几行上屏」与「状态列说什么」漂开就是 bug，两条用例盯着这个双向关系。
- **纯函数层不筛**：`mergeCourseRows` 仍产全集（outer join 不丢课）——计数、悬停点名、页脚
  口径（「谁在等外部 / 读面可不可用」）都靠全集。筛选发生在面板一次，不是两张表。
- **代价（刻意接受，留痕不静默）**：`hub 已注册 · 无进程` 这类「两半事实打架」的行（合并初衷之一）
  不再直接上屏 ⇒ 表头 chip「未在训 N 门未列」，悬停逐门点名 + 各自状态词。这是用**读噪**换
  **信息密度**：操作员要盯的是正在跑的那几门，而历史课在盘上会积到几十门。
- **0 门在训不是「没有课程」**：空态显因（`Empty`），三种原因分开说——「当前没有在训课程」
  （能开课）/「训练侧读面不可用 ⇒ 哪些课在训**不可知**」/「上一拍读失败」；不整块静默消失。
- 副作用（已知，未回退）：`未在训` 状态词与 `STOPPED_TITLE` 在**面板上**已不可达（只有两侧不同步时
  的 `--stopped` 行还看得见）——它们仍由纯函数层用例守着（状态语义不该因为面板筛行而被掏空）。

### ② 服务面逐行不再显示「共享 / 单例」

`scopeBadge()` 删除（`view/component-groups.ts`）+ 面板不再消费 + `theme.css` 的 `.tc-cc__scope*`
整块删。**族归属仍由 `scope` 决定**（`FAMILY_OF_SCOPE` 不动，`componentScope` 对拍用例照旧）——
删的是标签，不是分组判据；「trainer 是共享进程」这件事由族标题 + `FAMILY_META.hint` 承担
（每行重复 N 遍同一句话就是噪声）。回归：`web-component-groups.test.ts` 的「scopeBadge 不再导出 /
面板不再消费 / 样式表无规则」三通道 + `web-components.test.ts` 的渲染体无该 class 断言。

### ③ 「关键指标」区下线（删除，不是隐藏）

`app.tsx` 的 `<KpiStrip>` 下线；`web/components/KpiStrip.tsx` + `web/view/kpi.ts` +
`tests/web-kpi.test.ts` 删除；`theme.css` 的 `.tc-kpi*` 整块（含两个断点覆盖）+ `view/index.ts`
的 re-export 删除。**三条一起查才算钉住**（渲染里没有 / 组件文件不存在 / 样式表无规则）——
只查渲染的话，把面板挂回去只需改一行；而这次是删除：留一个没人用的组件文件与样式块，就是
下一波「照抄一个类名出来」的诱因。

**门禁（实测）**：dashboard `949 pass / 0 fail`（97 文件；删掉的 `web-kpi.test.ts` 原有 35 例）·
`tsc --noEmit` · oxlint **0 warning** · oxfmt clean · 三份 bundle 绿（app 306,847 B / gzip 75,325 B，
预算 150 KB）· 根 `bun run check` 绿（跳过 nn/原生部分：改动全在 `dashboard/**`）。

---

---

### §2026-09-20-topbar-and-family-labels（2026-09-20，用户指令：顶栏去掉页问题/「在训」、pill 靠左；族标题改「服务」）

**背景**：同一轮减肥的第二批（承接 §2026-09-20-console-declutter）。用户扫顶栏从左到右点出的四处：
「总览 现在能不能跑？这轮跑到哪了？」、pill 组前的「在训」、pill 组的水平位置、组件区族标题。

### ① 页问题不上屏（`tc-top__desc` 删除）

顶栏只留 `<h1>{title}</h1>`；`PAGES[].desc`（"一页一个问题"契约）改为**标题悬停**。

**为何不连数据一起删**：`desc` 是非空契约，`web-view-routes.test.ts` 守着（每页都要能一句话说清
它回答什么）；删渲染 = 视觉噪声没了，删数据 = 把那句「这一页是干什么的」也一并丢了。故保留数据、
换载体（悬停）。若日后确认悬停也多余，正确的删除顺序是「数据 + 契约测试 + 悬停」一起（留下没人读的字段
比删错更贵）。四页**统一**处理（不只总览）：同一个元素只给一页去掉，看起来就是坏了。

### ② pill 组前的「在训」标签删除 + ③ 整组靠左

- 标签已删（pill 自带课名/it/状态，组名交给 `aria-label`——它在页面上不可见）。
- 位置：`<Topbar>` 把 `{pills}` 从 `tc-top__right` 里**拿到 head 与 right 之间**——左半「我在看什么」
  （标题 → pill 组），右半「集群现在怎样」（阶段 / 节点 / 刷新 / 更新时间，靠 `margin-left:auto` 贴最右）。
  `.tc-tpills` 的 `flex: 1 1 auto` 保留：靠左 + 向右占位，左侧不再靠 `margin-left:auto` 碰运气。
- 降级形态保住：`!(pills && trainingCount>0) && courseCount>0` 才退回「在训 n/N」裸计数 chip——
  两侧**互斥且穷尽**（这笔一改漏，零门在训时顶栏会什么都不说）。回归：`training-pills.test.ts`
  新增「无 `class="lbl"` + 组在 `tc-top__right` 之先 + 有 `aria-label`」一例（切 `#root` 后再断）。

### ④ 组件区族标题：「服务面 · 单例」→「服务」（「课程面 · 按课程」→「课程」）

理由：逐行「共享/单例」徐章已于上一批删除 ⇒ 全族同值的后缀挂在标题上也开始重复；标题只说**这一族是
什么**，作用域事实下沉到 `FAMILY_META.hint`（「hub / 隧道 / trainer / 本机 worker 各一个进程服务所有课程，
本机 agent 全机一份」）。课程族同理改成「课程」（那族当前无成员、不渲染，但两份标题必须同一口径）。

**真盘只读探针**（真实 state，x20-steady 在训）：顶栏可见文本序 = `总览 | x20-steady it161 采集中 ■ | 节点 3/10 | ⟳ | 更新于 …`；
DOM 序 `head(6768) < tc-tpills(6856) < tc-top__right(7326)`；`class="lbl"` 不在；`服务面 · 单例` 不在、`>服务</span>` 在。

**门禁（实测）**：dashboard `950 pass / 0 fail`（97 文件）· `tsc --noEmit` · oxlint **0 warning** ·
oxfmt clean · 三份 bundle 绿（app 306,461 B / gzip 75,297 B）· 根 `bun run check` 绿。

### 补记（同日，同一轮用户指令的剩下两条）：节点行去「上轮」字样 + 服务族顺序

1. **节点行元信息只剩那个数**（用户：「不需要显示上轮字样，hover 时提示就好」）。`contribText` 从
   `上轮 N` 改成 `N`；释义全部移到 `contribTitle(v)` 悬停里：**数的含义 + 单位 + 缺失语义**
   （「最近完成轮贡献 N 局（rollout + eval 合计；进行中那一轮不计；— = 无池数据）」）。
   **为何删字样而不是删数**：行内状态词（健康/缓慢/离线）已由「贡献 vs 并发」判出，但**交了多少**
   仍是个可区分的事实（缓慢的 6 与 1 是两回事）；行内不再有文字提示 ⇒ 悬停是它唯一的释义载体，
   这句必须自洽完整。真盘（x20-steady）：`self 8 178 · mac 5 65 · a95 7 7 · a97 7 离线 0 · …`——
   零个「上轮」。回归：`web-app-nodepills.test.ts`（+「不得再出现上轮」+ `—` 与 `0` 不得混同）。
2. **服务族顺序 = 用户指令**：`trainingLoop → hubServer → selfNode → cloudflared → localWorker`
   （不再是「谁依赖谁」的推演：这一行最常问「训练在不在跑 / 队列通不通」，trainer 与 hub 提到最前；
   agent / 隧道 / worker 是支撑设施，靠后不档视线）。`component-groups.ts::ORDER.service` 一处改，
   两份用例的顺序数组跟着改（真盘探针：427 < 1201 < 1872 < 2538 < 3487 升序）。

**门禁（改后重跑）**：dashboard `950 pass / 0 fail` · `tsc --noEmit` · oxlint 0 warning · oxfmt clean ·
三份 bundle 绿（app 306,527 B / gzip 75,306 B）· 根 `bun run check` 绿。

---

---

### §2026-09-20-console-naming-and-trend-source — 服务角色名 / 节点行不写字 / 走势数据源档位

**用户指令（同一天追加的一轮）**：① 节点 pill 不显示「缓慢/离线」字样，离线时显示状态描述和判据；
② trainingLoop pill 去掉「dispatch→拉取」描述；③ 顶部趋势图加「全部 / rollout / eval」toggle 切换数据源；
④ 服务显示名换角色名（trainingLoop→管事 / hubServer→门房 / selfNode→采办 / cloudflared→跑腿 /
localWorker→丹徒），hover 提示各服务用途。

### ① 节点行：状态**不写字**（四档一视同仁）

色 + 形（`tc-dot--warn` 菱形 ◆ / `tc-dot--dead` 方 ■）+ 行级 tint（`tc-row--slow`）已经是三重编码，
行里再写一个字只是重复。**行内只剩该行的两个数**（并发 + 最近完成轮贡献），判据一律归悬停
（点 title + 元信息 title）。

**同日修正（用户明确收回）：最初让离线行在行内改写成判据句（`离线：最近完成轮贡献 0 局 < 并发 7` /
`离线：无池数据（还没结算过这一轮）`），用户当场否掉——「行内不写「离线：xxxx」，hover 提示就好」。
所以四档现在完全同规：行内只有数（贡献 `0` / `—`），两个不同的原因（零交活 / 从未结算）只在**悬停**里分开。
真盘校验：可见文本 `self 8 136 · mac 5 76 · a95 7 12 · a97 7 6 · a96 7 0`（零个「离线：/缓慢/上轮」），
悬停 `离线：最近完成轮贡献 0 局 < 并发 7`。**下次改这块的口径：先问「行里要不要字」，默认答案是不要。**

### ② trainer 行：pull 档不出「dispatch→拉取」徽章

没有登记节点是**缺省态**，那两个字不随任何东西变化 ⇒ 常年挂着的徽章只是噪声；非 pull 才是需要
解释的部署决定（东西被推去哪、推通了没有）。`pushBadgeText` → `pushBadge(f): RowBadge | null`
（pull 返回 null），pull 分支的悬停说明一并删（徽章不存在时它是死代码，留着下一个人会当漏了的功能加回来）。
「谁在跑这门课」仍由节点行回答（登记事实 + 健康度）。

### ③ 走势图数据源档位：全部 / rollout / eval

`TrendSource` + `TREND_SOURCE_OPTIONS` + `isTrendSource` 落在 `view/series.ts`（与 `TrendRange` 同规）；
档位与范围并排成一个控制条（`.tc-trendctl`：数据源在左、范围在右——看哪条口径 × 看多少轮，两个独立问题）。
`eval` 档把 eval **升为主序列**：标签换 eval 自己的词、线色改 eval 橙（`TrendChart` 新增 `color` 覆盖并
导出 `COLOR_EVAL` 作该色的**唯一出处**——面板里不得出现第二个色字面量）、`toneOf` 跟着**正在显示的那条**走
（否则切到 eval 档时，rollout 的阈值会去染 eval 的数：一个已经过期的判断）。SSR 首帧恒「全部」
（持久化偏好 hydrate 后恢复，与范围档同套路）⇒ 回归必须**单独渲染 TrendCell** 的三档。
硬规矩：**夹具不能用默认参数**——`cell('eval', win, undefined)` 会静默落到默认值，
「该课没有 eval 数据」这条分支看起来在测其实没测（实测报红才发现）。

### ④ 服务名 = 角色名（key 仍是机器身份）

单一源 = `web/view/component-roles.ts`（`COMPONENT_ROLES` + `componentName` / `componentPurpose` /
`componentHover`），总览组件行、日志页导航、日志页页头**三处同一份词**；行名渲染成
`<b title="管事（trainingLoop）—— 用途">管事</b>`（`StatusRow` 新增 `nameTitle`：点说「此刻状态」、
名字说「它是什么」，两个问题两个悬停）。用例钉住两件易漂的事：**键集与 `core/types.Component` 逐字相同**
（漏一个 = 那个服务在页面上露出 `hubServer`）、**用途里必须有 key**（悬停是 key 在上屏面的唯一落脚处）。

**没有改 `COMPONENT_LABELS`**（服务端的长技术名）。它喂的是 flash 消息、exit-watchdog 记的
`TrainingLoop (trainer)[<课>]`（`exit-watchdog.test.ts` 直接断言这个格式）与账本流——那些地方要的是
**对账用的精确名**，跟着改会把日志格式与一批用例一起动。代价已记录：动作结果文本里的服务名仍是技术名；
若日后要统一，改的是 `labels.ts` 一处 + 那条格式用例，而不是在 UI 侧再插一张表。

**门禁（实测）**：dashboard `964 pass / 0 fail`（99 文件）· `tsc --noEmit` · oxlint 0 warning · oxfmt clean ·
三份 bundle 绿（app 310,028 B / gzip 76,386 B）。

---

---

### §2026-09-20-preact-svg-attribute-spelling — 趋势图面积块变黑：Preact 客户端不归一 SVG 属性名

**用户报告**：从「指标」页切回「总览」，趋势线与横轴之间的**面积块显示为黑色**；**硬刷新后恢复**。

### 根因（两层，缺一不成立）

1. **属性名写成了 camelCase**：`TrendChart` 里是 `<stop stopColor=… stopOpacity=…>`、`strokeWidth`、
   `textAnchor`、`fillOpacity` 等 —— 这是 React 习惯。
2. **Preact 两条路径对属性名的处理不一致**（实测读 `node_modules/preact/dist/preact.mjs`）：
   - `preact-render-to-string`（SSR）把 camelCase **归一**成真 SVG 拼法（`stopColor` → `stop-color`）；
   - 客户端 diff 在 SVG 命名空间下只做 `l.replace(/xlink(H|:h)/,'h').replace(/sName$/,'s')`，
     其余 **原样 `setAttribute(l, u)`** ⇒ `stopColor` 是个 SVG 不认识的属性 ⇒ 被忽略 ⇒
     `stop-color` 回默认值 **黑**（而 `stopOpacity` 同理丢失 ⇒ 不透明）。两块 stop 都黑 ⇒ 整个面积黑。

这正好解释「为什么只在切页后出现」：切页 = Hero **卸载重挂** = 这些元素由客户端新建（拿到错误属性）；
硬刷新 = 浏览器用的就是 SSR 那份**正确**的属性（hydrate 时 diff 只再叠一个无效属性，不影响已有的）。
**所以渲染级断言永远抓不到它**（SSR 字符串看起来一模一样），只能靠源码级闸。

### 修法（两处，都在 TrendChart）

1. **属性名一律用 SVG 自己的拼法**：短横线的写短横线（`stop-color` / `stop-opacity` / `stroke-width` /
   `stroke-linejoin` / `stroke-linecap` / `stroke-dasharray` / `fill-opacity` / `text-anchor` / `font-size`）；
   本身就是 camelCase 的保持 camelCase（`viewBox` / `preserveAspectRatio` / `gradientUnits`）——
   后者写短横线反而错（SSR 也不转它们）。
2. **顺手把面积渐变改成 `gradientUnits="userSpaceOnUse"` + 用户坐标系的 y1/y2**：原 `objectBoundingBox`
   依赖**面积路径的包围盒**，而包围盒退化（恒定序列 ⇒ 面积零高）时浏览器也会画成黑色；
   训练曲线里「平坦的一段」很常见，这是第二颗同类地雷。

### 回归闸（两条，都在现有文件里）

- **源码级**：`tests/web-style-discipline.test.ts` 新增一组 —— 扫 src/web 下全部 `.tsx`（去注释后），
  禁 18 个「SVG 里本来是短横线」的 camelCase 写法（匹配形状 = JSX 属性赋值 `name=`，避免误报
  `AXIS_FONT.fontSize`）；含**自测**（正则对阳性/阴性样本各断言一次），防止闸写成永真。
  已反向验证：故意把 `stop-color` 改回 `stopColor` ⇒ 该组立刻报红。
- **渲染级**：hero 用例断言每个 `fill="url(#id)"` 都指向同一次渲染里存在的 `id`，且全部用
  `gradientUnits="userSpaceOnUse"`（没有 `objectBoundingBox`）。

**门禁（实测）**：改动文件各自的用例全绿（`web-style-discipline`、`web-view-trend-range`、
`web-hero-trend-source`、`web-app-nodepills`）· `tsc --noEmit` · oxlint 0 warning · oxfmt clean ·
三份 bundle 绿（app 310,051 B / gzip 76,424 B）。
全量套件当时有 11 例红——**全部是工作区环境**，与本改动无关（已逐条归因）：
① `nn-training/rl-config.json` 在 21:59:05 被写入一行 `"concurrency": 4,export HUB_TOKEN='<hub token>'`
（人手粘贴事故）⇒ 文件不再是合法 JSON ⇒ 10 例读真配置的用例抛 `JSON Parse error`；
② `web-ssr-readonly` 断言「空坞不出现」，而真状态此刻带一条 loop-complete 告警（21:55:03 正常收官）
⇒ 坞里真的有东西 —— 这条用例本身就挂在**活状态**上（只有零告警时才成立）。**真盘只读探针**（真实 state）：五行角色名 + 各自用途悬停
（`管事（trainingLoop）—— 训练循环本体…` …）；节点区可见文本零「上轮 / 缓慢」字样（仅离线判据句）；
控制条 `走势数据源 → 走势范围`，默认压「全部」，6 条 eval 橙线。

---

---

### §2026-09-22-goalnn-course-switch-cache-tier（2026-09-22，用户报障：多课程并行切课程要等几秒）

- **背景**：慢快照**整体**按课程键控，而其中最贵的几笔探测（节点 ping 1.5s、共享 hub 观测面 1.2s、
  push 机群探活、池历史聚合）与「在看哪门课」无关 ⇒ 切到没看过的课要原地重做一遍（实测冷 2883ms /
  暖 2ms）。**且「切课」自己发的 `setCourse` 会作废全部快照缓存**——作废缓存的动作与它保护的东西是同一个。
- **备选与否决**：① 后台为**所有**课程各暖一份快照 —— 否，等于 N 门课各探一遍（§366 慢探测预算，
  注释里已明令否决过）；② 切课时先给上一门课的快照、新课数据异步补 —— 否，「课程名 = B 而指标
  是 A」是 WYSIWYG 违规，比慢更坏；③ 缩短/去掉节点 ping 与 hub 探测 —— 否，那是「离线 vs 慢节点」
  的判据预算，砍了会把掉线说成健康。
- **决定**：快照**分两层键控** —— **机群级**（节点 / 本机槽位 / push 群 / hub 观测面）单条目全局共用
  （`core/swr-cache.ts`：新鲜直接给、陈旧先给旧值再后台重算、`clear()` 后必须等重算）；**课程级**
  （组件表 / 日志尾 / 阶段 / 账本尾派生）按课程键控；视图态动作（`setCourse` / `getGateHaltMode`）
  **不作废**任何快照缓存（判据 = `route.ts::VIEW_ONLY_ACTIONS`，`invalidatesSnapshot` 门禁钉住）。
  修复后：切课零新增机群级探测、4ms 上屏（`tests/server-api-course-switch.test.ts`）。
- **违反后果**：再把进程级探测放进按课程键控的缓存（或让视图态动作作废缓存），切课立刻退回「几秒」，
  且没有任何页面会报错——只有用户手感能发现。
- **追加（同日晚，用户指令：「让动作后的第一帧也不再冷算」）**——切课修好后只剩**动作路径**还在首帧冷算：
  **按「重算贵不贵」分处置，而不是按「缓存住在哪个文件」**。① 动作路径收成**一个入口**
  `api.invalidateAfterAction()`（server.ts 不再逐个缓存手写作废，漏一个就退化成「动作后卡几秒」）；
  ② **课程级快照硬清**（`slowSnapshots.clear()`：重算毫秒级，组件存活/日志尾/阶段这类动作结果必须
  即时上屏，不值得为它引入陈旧窗口）；③ **机群级探测 / hub 观测面 / 调度器视图软作废**（`refresh()`：
  读路径先给旧值、重算丢后台）——它们的重算要等 1.2–20s 的探测或 python 子进程，让请求等它 = 动作后
  首帧又卡几秒；④ 软作废的**前置条件**：重算体不许按住事件循环 —— 调度器视图的默认执行体从
  `spawnSync` 改成异步孪生 `runRunPythonAsyncScript`，否则「后台重算」是假的（连能返回的旧值响应也发不
  出去）；⑤ `invalidateSlowSnapshot()` / `invalidateLoopQueue()` 降为**硬清**（只给产物导入与测试夹具归零）。
  「动作结果即时上屏」不靠缓存：结构（组件存活、节点行的 enabled/url、worker 行、执行面 mode）与
  **暂停意图/生效回执**（`readPauseFacts` 现读控制文件）都在请求里现算。
- **违反后果**（追加项）：把软作废改回硬清、或让重算体退回同步 `spawnSync`，首帧立刻退回「几秒/子进程
  冷启」，而页面完全不报错；门禁侧靠**悬挂探测桩**把这种回归变成 1s 内明确红
  （`tests/server-api-course-switch.test.ts` / `tests/server-api-loop-queue.test.ts`：把 `refresh()` 改回
  `clear()` 可复现红），`tests/web-app-coursematrix.test.ts` 钉住「server.ts 不再逐个缓存手写作废」。
- **追加②（用户指令：「复查其余动作路径（导入产物、冒烟、节点编辑）」——复查结论 + 一处漏网修复）**：
  复查口径 = 「动作 POST 返回后，下一次 `/api/state` 要不要等一次机群级探测或子进程」（客户端动作后只重拉
  `/api/state`，见 app.tsx `doAction`）。逐条查「动作后到底谁被作废」：
  · **节点编辑**（`setNodeEnabled`/`setNodeConcurrency`/`registerPushWorker`/`removePushWorker`）：POST =
    配置回写（毫秒级、无 HTTP），首帧 **6ms** ✔；
  · **冒烟**（`smoke`/`smokeTrain`/`nodeSmoke`）：POST 本身是长动作（真 rollout / 里程碑等待，秒–分钟级，
    **记在动作请求上**而不是帧上），首帧 **6ms** ✔；
  · **导入产物**（`/api/deliverUpload`）**曾有漏网**：server.ts 自己写了两行**硬清**（`invalidateSlowSnapshot()
    + invalidateHubAdmin()`）⇒ 导入后第一帧冷算机群级探测（本机实测 **1575ms 冷 / 6ms 暖**）。已改为同一个
    `invalidateAfterAction()`——导入改的是**课程产物**（权重/账本/eval_log），「哪些节点在线」一个字都没变，
    硬清它纯浪费；课程级那一半仍是硬清，所以新 iter / 评估行下一帧就上屏。
  · **未改（观测，带实测）**：`/api/pool`（节点统计面板）冷 **2448–2503ms**（逐节点 ping 2.5s 超时 ∥ + 池历史
    聚合 + codeHash），但它**不在动作路径上**（课程键控 30s TTL，动作一律不作废）⇒ 不是「首帧冷算」，而是
    「最多 30s（TTL）/ 5min（面板轮询）陈旧」；客户端的 `poolFreshNonce`（切课 / iter 前进 / 连接恢复）会
    `?fresh=1` 强制重算 ⇒ 那些时刻要等 2.5s（独立面板，不堵 `/api/state`）。
  · 同族事实（已一并修，见追加④）：`importDeliverZip` 走 `spawnSync` —— 导入期间**事件循环被按住**，
    其它请求与刷新器都排队。
  · 门禁：`tests/web-app-coursematrix.test.ts` 钉住 server.ts 里**硬清函数一个都不许出现**
    （`invalidateSlowSnapshot`/`invalidateHubAdmin`/`invalidateLoopQueue`）+ 必须有 `invalidateAfterAction()`。
- **追加③（用户指令：「把 /api/pool 也改成 SWR，让节点编辑后池面板不再等 30s/5min」）**：池视图
  （`/api/pool`）把一个 zip 里最贵的东西装在一块儿：**结构**（节点行 enabled/status、local 槽数，
  来自 cfg）与**探测**（逐节点 ping 2.5s 超时 ∥ + 池历史聚合 + codeHash + selfNode `/v1/status`）。
  写成「Map<课程, {at, view}> + 命中即返回」的硬 TTL 缓存，两个后果互为代价：① 动作（如「停用节点」）
  不碰它 ⇒ 关掉的节点在池表里还能显示最多 **30s（TTL）/ 5min（面板轮询间隔）**，而同一页上方的注册表
  行已改成「已停用」——两处事实不合；② 硬清它又要等 **2.5s 冷算**（实测 2448–2552ms）把面板按在
  「池统计加载中…」。
  · **决定**：池视图换成 `core/swr-cache`（**每课程一份**，课程键控不变）；动作后
    `refreshPoolViews()` 软作废（写进 `invalidateAfterAction()` 这个唯一入口）；显式 `?fresh=1`
    （手动刷新 / 重试）仍是**硬清 + 等重算**——那时操作员要的就是「现在就给我新的」。
  · **客户端的另一半**（否则软作废没意义：池轮询间隔 300s，没人会去看它）：`doAction` 成功后推
    `poolFreshNonce`，`NodeStats` 的再校验改成「立即软拉一次（即时、2ms）+ 在服务端 `cachedAt`
    未推进前有界重拉（1.5s 间隔，最多 3 次）」；首读本来就是服务端现算的（`cachedAt ≈ 现在`，
    例如切到一门没看过的课）则直接收工。**实测**：动作后首帧 **2ms**（旧值、`cachedAt` 未动），
    **~2.5s** 后后台重算落地、面板自动变新（面板以 `cachedAt` 推进为「新的一份到了」的判据）；
    旧行为是「最多 30s/5min 不变」或「硬清按住 2.5s」。
  · 门禁：`tests/server-api-pool-swr.test.ts`（见追加⑤：已改成「第一帧就是新结构」）；共享桩提为
    `tests/helpers/probe-stub.ts`（`probeStub()` + `guardMs()`）。
- **追加⑤（用户指令：「把池视图的结构与探测拆成两层，让节点编辑真的第一帧就上屏」）**：追加③ 的 SWR
  只是“首帧不卡”，**首帧给的是旧结构**：一个 zip 里两件快慢差三个数量级的事装在一条缓存里——
  **结构**（节点行/顺序/`enabled`/local 槽数，cfg 事实、毫秒级，且就是操作员刚拨下的开关）与
  **探测**（逐节点 ping 2.5s 超时 ∥ + 池历史聚合 + codeHash + selfNode `/v1/status`，冷算实测
  2448–2552ms）。于是无论怎么处置都错：不碰 ⇒ 池表旧状态超 30s/5min（与同页注册表行矛盾）；
  硬清 ⇒ 按住面板 2.5s；整条软作废 ⇒ 首帧仍是旧状态（要等后台重算 ~2.5s）。
  · **根上的观察**：**这份视图里没有任何“按课程”的东西** —— cfg.nodes / rl.local_slots / tmp 下最新
    活跃流（`aggregateNodeHistory` 不收课程参数，只扫 `tmp/**` 取 mtime 最新的 meta）/ 本机 codeHash /
    selfNode 存活性全是**机器事实**；`course` 只是回显字段。
  · **决定**：拆**探测层** `PoolProbes`（单条目全局 SWR，与 `snapshot-cache.FleetProbes` 同规）
    ⊕ **结构层**（`assemblePoolView` 每请求现算：节点行/顺序/`enabled`→`status:disabled`、local 槽数）；
    **删掉按课程键控的 `Map<课程, cache>`**（切课不再重算 2.5s 探测）。`cachedAt` 仍是**探测层**时刻
    —— 客户端靠它推进判断「重算落地」（结构不需要任何再校验：它本来就实时）。
  · **实测**：冷算（首次打开节点页）2520ms；**切换课程再读 0ms**（旧：每门课各 2.5s）；动作后第一读
    **1ms** 且 `cachedAt` 未推进（结构已新、探测仍旧）；后台探测重算落地 2515ms。页面脚注同步改成
    「结构实时；探测列 Ns 前更新」。
  · **门禁**（`tests/server-api-pool-swr.test.ts` 改写）：第一帧就断言 `status:disabled` + local 槽数
    已变 + `cachedAt` 未推进 + 停用行历史列仍属旧探测；已验：把整条视图按探测缓存（旧式单层）会红
    （首帧拿到旧结构）。另：`tests/server-api-pool.test.ts` 钉住**跨课程共用同一份探测**。
- **追加⑥（用户指令：「看看 /api/evalboard 是否也该按快慢分层」）——复查结论：**不需要分层**（它没有探测类
  输入），但顺手补了一处**反向缺口**（动作后结果不即时）。
  · **为何不分层**：它全部输入都是**读盘 + 纯聚合**，没有一笔 http/子进程/ping —— `loadRows`（store 按月分片）/`loadBatches`/`ladderWithGod`/`readRunnerState`/`read-through 入账`。本机实测：冷算
    **0–1ms**；入账 2000 行 **4–8ms**（全重复去重路径 4000 行 5ms）；`loadBatches` 200 批 0ms；数据根
    (`dashboard/data/evalboard`) 在本机是空的。所以池视图那条「结构 vs 2.5s 探测」的轴**在这里不存在**，
    分层会是空重构。它如果哪天真变慢，那是**数据量轴**（eval_log 全量重解析 + store 全读 + 逐行去重）
    → 该做的是增量入账（按字节偏移）与按课程分片，而不是本主题的 SWR。
  · **反向缺口**（顺手修）：`invalidateEvalBoard()` 此前**只**由 30s 的爬梯 ticker 调；而写它输入的正是
    那些动作（入队/中止/爬梯启停）——客户端确实在动作后 `refresh()`，但非 fresh 的读会命中 30s TTL 内的旧
    视图 ⇒「已入队」的批次最长等 30s（TTL）/5min（页轮询）才上屏。已把 `invalidateEvalBoard()` 挂进同一入口
    `invalidateAfterAction()`，用**硬清**（0–1ms 冷算，没有“先给旧值”的价值；且它按课程键控，整张清掉就是
    每门课重读一次）。
  · **门禁**：`tests/evalboard-board.test.ts` 新增「动作后非 fresh 的再读就能看到新批次」（暖一份空视图 →
    入队 + 建批 → `invalidateAfterAction()` → 再读能看到）；已验：拿掉那句 invalidation 会红。
- **追加⑧（用户指令：「把评估板视图的 loadRows 也做成增量（按课程分片或按字节偏移），让缓存未命中只剩纯聚合」）**：
  追加⑦ 治掉了 eval_log，这一轮治账本行本身。
  · **根因**：`store.loadRows` 每次全读 + 全解析 `games/*.jsonl`，且在**同一条缓存未命中路径**上；
    `ladderTick` 更把它放进**课程循环里**（N 课 = 整本账读 N 遍）。实测 2 万行：单次 26ms，
    ladderTick 3 课 **73ms**（每 30s 一轮）。
  · **决定**：抽 `tail-read.ts`（「按字节偏移读新增尾巴」的**唯一**实现，`ingest.ts` 与 `rows.ts` 共用）：
    `readTail` = 单窗口原语（含偏移/半行/不可信判据，与追加⑦ 同规），**`readTailDrained`** = 循环到
    文件末尾的生产入口。`rows.ts::loadRowsCached` 按**分片**记住 `{pos, rows}`：分片未动 → 零 IO、
    零解析、**连返回的数组都是同一个实例**；只增长（append-only 账本的常态）→ 只读尾巴、旧行**对象
    实例**都不换；截断/同尺寸改写/inode 变 → 那一片整片重解析（安全侧）；分片被删/新增 → 相应增删。
    行序 = 分片名排序（`YYYY-MM.jsonl` ⇒ 月份序，比 `readdirSync` 的返回顺序**更**确定）。
    `view.ts` / `auto-ladder.ts` 改用它；`auto-ladder` 还把账本读取提到课程循环**外**（原本 N 课 N 遍）。
    `store.loadRows` **保留原样**：CLI / 对账要的是「刚从盘上重读一遍」的语义，缓存版只服务于会话内视图。
  · **实测踩到的坑（已修，且是必顶的）**：单窗口 4MB 对上一个 **4.5MB 的月分片**只读到前 **8006/20000**
    行；而增量读只在文件**变动**时才回来 —— 剩下的行**永远补不上**。所以生产入口必须是
    `readTailDrained`（循环到 EOF / 末尾半行）。该 bug 在写单窗口测试时是绿的（周分片小于窗口），
    只有拿 2 万行真语料实测才炸出来。
  · **实测**（2 万行）：未变动 **0.01ms**（旧 26ms）、追加 20 行 **0.38ms**；ladderTick 3 课 **73ms → 0.67ms**；
    整视图冷算 **78ms → 38ms**（删掉的那 40ms 就是重复重解析，剩下的就是纯聚合）。
  · **门禁**：`tests/evalboard-rows-incremental.test.ts` 9 例（**同实例身份** = 没重解析、只增长时旧行实例
    不换、截断/同尺寸改写整片重读、新分片月份序、分片删除、末尾半行、坏行跳过、空根/多根隔离）；
    **已验**：把 `loadRowsCached` 内部换成 `store.loadRows` → **3 例红**。另在 `tail-read` 侧加一例
    「文件大于单窗口 → `readTailDrained` 一次读完整条尾巴（且 `readTail` 单窗口确实会丢）」钉住那个坑。
- **追加⑩（用户指令：「把『输入指纹缓存』这套提炼成可复用原语，替掉控制台剩下那些拿时间当判据的
  小 TTL 缓存」）**：追加⑨ 的那套判据落成原语 `dashboard/src/core/fingerprint-cache.ts`。
  · **原语**（同步；与 `swr-cache` 分工见文件头）：`createFingerprintCache<T>({ signature, backstopMs,
    now? })` —— 命中 = 指纹相同 **且** 未超兵底；指纹变了**立即**重算；命中返回**同一实例**（调用方
    可拿它当「真的没重算」的证据）；`get / getWithStatus / peek / invalidate(key?) / clear / stats`。
    兵底的定位写死在文档里：**兜「指纹清单将来漏了某个输入」的陈旧上限，不是 TTL**（取 600s > 所有
    轮询间隔）。另配 `fileSignature` / `filesSignature`（清单式输入的公共写法，只 stat 不读内容）。
  · **选型判据一并写进原语头注**（这是本追加最想留下的东西）：**采样**（活体观测，无法指纹化：节点
    ping / hub HTTP / python 扫全部 tmp / 机群探活 → 继续用 `createSwrCache`，那里的窗口表达的是
    「多久探一次」）· **失败兜底**（busy 5min 自解锁、坏配置 1s 重试：时间就是要表达的东西）·
    **内容摘要**（`codeHash` / `engine_epoch`：输入就是全部内容，指纹不可能更便宜）· **重算本身很
    便宜**（毫秒级 → 加缓存只是加复杂度）。
  · **迁移/清理（都带实测）**：
    ① 评估板视图改用它（判据不变，退化面为零）；实测 2 万行 **命中 0.041ms**（指纹本身 0.039ms：
    行 0.010ms + 文件清单 stat）、冷算 62ms —— 原语没有引入开销；
    ② `pool.ts` 的**本机 codeHash 5s TTL memo 直接删掉**：实测这份摘要只要 **3–5ms**（145 文件 /
    2.1MB），而它住在自带 30s 节奏、本身要等 ~2.5s ping 的探测层里 —— 挂窗口换不到任何东西，只多一个
    陈旧面；内容摘要这类**正确处置是「不用缓存」**，不是「换指纹」；
    ③ `buildEvalCkptsView`（每请求扫 weights 目录）**不加缓存**：本机 `nn-training/weights` 不存在
    （0 腿，实测 0.5ms），而它的成本=对文件逐个 stat，指纹也得做同一批 stat ⇒ 无收益（真变慢时该做
    的是把「目录清单」与「逐文件 stat」分层）。
  · **踩到的两个坑（都有门禁）**：
    ① **原语会把 key 喂给 `signature`** —— 视图原来的 key 是装饰过的 `evalboard:<course>`，于是行指纹
    算到一把**不存在的课程**上（恒为 `-`）⇒ 签名恒等 ⇒ **永不重算**（静默出错，四个用例当场红）。
    已把 key 改成业务身份（课程名）并在原语文档里写死这条 + 加一条原语用例；
    ② **测试桩「一次性放闸」是个陷阱**：`probe-stub.release()` 只放行当时已注册的探测，而后台重算里
    探测的注册时刻取决于它前面的 `await`（`localCodeHash` 的 async import 恰好排在 ping 之前）⇒
    放闸之后才注册的探测被**永久悬挂**，池视图用例从绿变红。已改成「关闸 + 放行」（`hold = false`）。
  · **门禁**：新增 `tests/fingerprint-cache.test.ts` 10 例（命中同实例 / 指纹变立即重算 / 兵底到点重算 /
    **重算后重取指纹**（入账场景：不会每读都重算）/ 按 key 分条 / `getWithStatus` 命中标记 / `peek` 不
    记账 / invalidate 三态 / key 即身份 / `fileSignature`·`filesSignature`）；视图 16 例改到原语上仍全绿
    （旧判据换回 `at < 30s` ⇒ 10 例红的性质未变）。
- **追加⑨（用户指令：「看看评估板视图那 38ms 纯聚合能不能按课程缓存（只重算变化的课程）」）**：
  追加⑧ 把未命中降到只剩聚合，这一轮把聚合本身也按课程缓存（但**不用 TTL 当判据**）。
  · **根因**：视图缓存的判据是 `Date.now() - at < 30s` —— 与「数据变了没」毫无关系。于是
    ① 没变也更每 30s 重算一次（`/eval` 页与首页摘要都是 **300s** 轮询，等于每轮都白付一次聚合：
    实测 2 万行单课 22–85ms）；② 变了反而可能在 30s 内**拿旧视图**（新评估行/新批次最长等 30s）。
    两个方向都错，根子是同一条：**拿时间当数据的代理**。
  · **决定**：缓存键的判据换成**输入指纹**（`viewInputSignature`）——
    `账本行（按课程，增量滚动 FNV）/ batches / requests / requests.done / ladder 工作副本 /
    ladder 正本 / runner_state / space_calibration / eval_log 两个候选 / curricula` 的
    `size:mtimeMs` + 行指纹。命中 = 指纹相同 **且** 未超 `VIEW_BACKSTOP_MS`；指纹变了就**立即**
    重算（不等任何 TTL）。`VIEW_BACKSTOP_MS = 600s` 的定位是**兵底**（比 300s/15s 的轮询都长）：
    万一将来有人给视图加了个读取却没进清单，陈旧上限就是它 —— 不是「过期就丢」的 TTL。
  · **按课程**：行指纹按课程分隔（聚合视图 `course=''` 用全课程指纹）。实测 2 门课各 1 万行：
    **命中 0.044ms**（旧口径每次 >30s 的读都 22ms+）；只改 cB 后 **cA 0.67ms（命中）/ cB 13ms
    （重算）**；无变化的轮询 0.039ms。`fresh=1` 与 `invalidateEvalBoard()` 语义不变。
  · **踩到的坑（差点静默丢数据）**：`eval_log` 一开始**没进清单** —— 它是**入账的触发源**，
    不列它就会命中缓存、根本不跑入账 ⇒ 新评估永不上屏（新行只能等兵底或动作）。已把两个候选
    路径都列进清单（`courseEvalLogCandidates`，日志后来才出现时指纹也会变），并单钉一条用例。
  · **门禁**：`tests/evalboard-view-cache.test.ts` 16 例 —— 命中（内层数组同实例 + `ingested` 归 0）、
    **过 30s/5min 仍是命中**、本课程新行/别的课程新行（只重算变化的那门）、聚合视图全课程指纹、
    逐项输入变了都**立即**重算（行为断言：批次 / 请求→ladderState / runner_state / space_calibration /
    阶梯工作副本 god 基线）、`eval_log` 新行→入账后立即重算、`fresh`/`invalidate`/兵底、以及
    「清单含全部标签 + 改任一临时根文件指纹必变」（防清单漏项）。**已验**：把判据换回旧的
    `at < 30s` ⇒ **16 例中 10 例红**。
  · **语义变化（已写明）**：缓存命中时返回 `{...cached.view, ingested: 0}`（这次调用确实没读入
    新行），内层数组实例不变 —— 客户端本来就不消费 `ingested`（`eval-types.ts` 已补注释）。
- **追加⑦（用户指令：「给评估板入账做增量（按字节偏移），别每次缓存未命中都全量重解析 eval_log」）**：
  追加⑥ 判明评估板的风险轴是**数据量**，这就是那一轴。
  · **根因**：`ingestCourseEvalLog` 每次 `readFileSync` 整份 `eval_log.jsonl` 再逐行 `JSON.parse`；
    `ingestRows` 每次 `loadDedupKeys` 读**整个 store**（全部课程、全部月份）。两者都在
    `buildEvalBoardView` 的**缓存未命中**路径上（30s TTL 过期 / `?fresh=1` / 动作后重算），且随
    账本与日志**线性增长**。本机 2.2MB / 2 万行语料实测：全量解析 **9–12ms** + 去重扫描 **81–83ms**
    ≈ 每次未命中白付 **~93ms**。
  · **决定**：入账改**按字节偏移只读新增尾巴**（`readEvalLogTail`，偏移语义=**最后一个换行之后**）：
    ① 末尾半行**不消费**（训练进程可能在行中间 flush；现在切下来会被当坏 JSON 丢掉，而它下一拍就是
    完整一行）；② 偏移**不可信**才从头读 = 首次 / 体积变小（截断）/ inode 变了 / **体积没变却被动过**
    （同尺寸原地改写，无从知道改了哪一段——宁可整份重读，去重保证幂等）；③ 单次上限 4MB，窗口读满仍无
    换行（单行超长）整窗消费，否则永远读不动；④ 位置 memo 键 = `<数据根>|<日志路径>`（store 是
    append-only 账本、控制台无清库路径，故不为「库被清空」重置；换 `EVALBOARD_DATA` 即从零重读）。
    另一半是 `knownKeysFor`：**去重键集 memo**（指纹 = `games/*.jsonl` 的 `name|size|mtimeMs`，只 stat
    不读内容）——本进程 append 后刷新指纹（不自我失效），外部进程（回填 CLI / 另一个控制台）改过 store
    指纹自变即重载。`ingestRows` 因此加了可选 `known` 形参（默认仍现扫，行为零变化）。
    顺手把日志路径从硬编码 `REPO_ROOT/tmp` 改为 `tmpLogsDir()`（与课程发现**同源**；默认零变化）——
    否则 `BCITY_TMP_LOGS_DIR` 重定向只重定向一半。
  · **实测**（同样 2.2MB / 2 万行）：**无新增 0.06–0.08ms**、**新增 1 行 0.27–0.30ms**（旧：每次 ≈93ms）。
  · **门禁**：`tests/evalboard-incremental-ingest.test.ts` 13 例——`readEvalLogTail` 钉**偏移契约**
    （半行不消费→补齐才消费、截断 reset、同尺寸改写 reset、超长行不卡死且偏移每拍前进、无新增偏移不动、
    文件不存在不抛），集成钉（首读全量→再读只认新增、末尾半行、轮转重开、空课程）；memo 钉（store 未变
    复用**同一份** Set、本进程 append 后仍命中、外部改动重载并看到新键）。
    **诚实说明**：这是**性能修复**，行为**不可观测**（去重幂等 ⇒ 重读与否入库结果相同），所以门禁是
    「偏移契约」的单元测试 + 实测数字，而不是「旧实现会红」的行为用例；唯一行为差异是同尺寸原地改写
    （新实现选整份重读=安全侧），已单独一条钉住。
  · **未做（下一步的明确入口）**：缓存未命中路径上仍在 `loadRows`（store 全读；本机 2 万行 **69ms**，
    整视图冷算 **86ms**）——要再砍就得做 per-course 分片 / 按字节偏移的增量 `loadRows`，那是下一轮。
- **追加④（用户指令：「把产物导入的 python 调用改成异步，导入期间控制台不再整体卡住」）**：控制台服务端
  **只有一个事件循环**，而导入路径上三处同步操作把它按住（上限 300s 的 `spawnSync` python 导入器 +
  512MB 上传体的 `writeFileSync`）。按住期间 `/api/state`、5s 慢快照刷新器、局域网其它查看者的读取全部
  排队（“导入时整个控制台卡住”），而且它让追加②/③ 的「重算丢后台」名存实亡（旧值响应要等 `spawnSync`
  返回才发得出去）。
  · **决定**：控制台跑 python **只有两种模式**——`spawnRunPython`（detach，长任务）与
    `runRunPythonAsyncModule/Script`（异步捕获，要结果的短任务）；`importDeliverZip` 改用它并 `await`
    （POST 本来就该等这次结果），`saveDeliverUpload` 改 `fs/promises.writeFile`；**删除**同步变体
    `runRunPythonSyncModule/Script` 与私有的 `runSync`（留着就会被再次用上），`SyncRunResult` 更名为
    `RunPythonResult`（名字不能再提「同步」）。语义不变：仍是「跑完拿结果 + 超时杀进程报 `timeout`」。
  · **实测**（真 python）：`-m platform` 53ms 期间事件循环 tick 8 次（`spawnSync` 会是 0）；真实导入路径
    （`importDeliverZip` 跑 `remote.deliver_zip`，坏 zip 快速失败）42ms 期间 tick 7 次，失败原因照旧翻成
    人话上屏。
  · **门禁**：`tests/server-no-blocking-python.test.ts`——扫 `src/server/**` 的**调用**（`spawnSync(` /
    `execSync(`，注释里提名字不算），命中即红并列出文件；另有「两条路都在、同步孪生不得回来」一条。
    （范围只限 `src/server/**`：`src/{core,launch,evalboard}` 是启动器/CLI，进程自己就是终点、无并发读要在乎。）

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §28→§1 · 旧 §29→§2 · 旧 §64→§3 · 旧 §73→§4 · 旧 §82→§5 · 旧 §87→§6 · 旧 §88→§7 · 旧 §91→§8 · 旧 §122→§9 · 旧 §138→§10
