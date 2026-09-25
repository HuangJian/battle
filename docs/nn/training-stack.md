# NN 训练栈 — 技术档案

> 训练循环 / 调度器 / supervisor / 课程编排 / 门禁与停车。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---
## §24 教训：缰绳初值显式化 + 干烧熔断（accident.plan §5，2026-09-21）

**事故**：C 双臂从收敛权重（it175）以 `kk(1)=1` 满 kickstart 复活，锚主导更新连烧 30 轮
（it1 kl=0.90），两臂从 ~34 洗回 ~50；锚释放后平摆、又跑 140 轮没爬出来 ⇒ 两臂 ×12h
只换来「无结论」。**过程熔断全程没响**（kl/ent 都正常）——坏的是**结果**。

**① 初值显式化（四条管道，一条不通就静默失效，`ent_break` 前科）**：

| 管道 | 落地 |
|---|---|
| 课程键 → args | `CourseConfig.kickstart_init` + `flat_overrides` **异名**映射 → `kickstart_kl` |
| 热加载分类 | 进 `RESTART_ONLY_FIELDS`（与 `kickstart_ref` 同类）+ 新增 `RESTART_ONLY_ALIASES` 修异名字段的**假变更行** |
| 语料身份 | **不进** `corpus_identity_fp`（ref/优化器语义 ≠「一个样本是什么」，进去全体课程指纹漂移） |
| 公式 | 接 `args.kickstart_kl` ⇒ 仍是 `run_rl.update_kwargs` **唯一**一处衰减源，`coef_active` 精确归零链一字未动；`kk(it)=init×decay^(it-1)`（run 原点） |

自洽拒启：声明 `kickstart_init>0` 却没开 `kickstart_ref` ⇒ 启动期 `SystemExit`（否则那个数被静默丢弃）。
启动自检报**初值来源**（课程显式 / 缺省），缺省大值（≥0.5）warning——C 事故正是“拿缺省 kk=1 复活”。

**② 干烧熔断落执行面**（新模块 `rl/kickstart_burn.py`，纯函数）：基线**不靠人填** =
`eval_log.jsonl` 的 **it0 行**（课程 bc 权重的干净评估 = 缰绳锚定的同一份权重）；连续
`points`（3）个评估点低于基线 `margin_pp`（5pp）⇒ 停腿告警「疑似回锚/塌陷」。阈值走执行面
`courses.<课>.kickstart_burn`（课程 gates 块仍为空——阶梯课程不配 gates 是 I2 铁律）。

- **判据同源**：给 `gate_check.read_trend_rows` 加 `include_baseline`（it0 默认仍被滤），
  与 `_gate` 共用同一读者/同一文件；干烧计数落 `kickstart_burn` 事件 ⇒ 重启可回放。
- **停腿而不只告警**：本地停腿 + ABORT 判决 + 按课程下发云端 halt——停腿的意义就是停止烧钱，
  本地停了云机接着领活 = 白停。
- **状态转移才落账**（同 `_stop_loss`）：streak 变才写，0→0 不写（否则每轮一行淹账本）。
- 读数缺失的行（0 局的轮）**既不计数也不清零**：当 0 会误停腿，清零会打断真趋势。

**验收（盘上）**：`tests/test_kickstart_plan.py`（15：四管道逐条 / 公式三点 / 缺席逐字节不变 /
自洽拒启 / CLI 冲突仍被拦 / 基础线缺失不判 / 门槛边界 / 反弹清零 / 缺失读数 / 执行面阈值覆盖 /
停腿+账本回放 / 云端达令按课程 / 缰绳关着零行为 / 计数不重复写）；nn 门禁全绿 **1955 passed**。
未做（有意）：§5.3 的**控制台开课回执**（需 dashboard 侧开发；训练侧日志先行）。

---

---

## §23 架构：单一 PPO 路径落地（`--ppo` / `--remote-degrade-after` 双删）——训练侧不再有「算力位置」旋钮（2026-09-21）

**背景**：C 双臂事故（x20-clutch / -null）的第一根因是 `--ppo` 缺省 `local` 被漏传 ⇒
本机 CPU PPO 空烧（it1 `eta~47297s`）。用户口径（2026-09-21，plan/accident.plan.md §3）：
厨师只管把菜放上传菜口，不问谁来吃——loop 只「发布 job 到 hub 队列 + 等 worker 认领」。

**改动（纯减法，无新机制）**：

| 面 | 改动 |
|---|---|
| 旗标 | `--ppo`、`--remote-degrade-after` 删除；`args.ppo` 全仓零引用（AST 级单测钉死） |
| 循环 | 本机 PPO 引擎路径（`_serial_ppo` 的 load/chunk/update 全链）、降级链、本机 torch 栈（`_setup` 不再 import torch/模型）全退役；发布-等待是唯一路径 |
| 恒置 0 | `stream` / `double_buffer`（本机没有 PPO 窗口可重叠）；显式传参仍响亮报错 |
| 入口冻结 | `intent` / `goal` 无远端实现 ⇒ **响亮拒启 + 保留实现**（盘上 0 门课程在用） |
| 控制台 | 两处 spawn argv、`export.ts`、`remoteDegrade` 开关（UI + 透传链 + `course-knobs` 旋钮）删除；`remote_degrade_after` 进 `LEGACY_COURSE_KEYS`（旧值开课时 prune） |

**踩到的坑（值得记）：删旗标删一半 = 三处静默僵尸分支**——`getattr(args, "ppo", 缺省)`
在旗标删除后不报错、只恒取缺省值，行为测试全绿但语义已死：
① `_export_offline_bundle` 恒判「非 remote」⇒ **整条导出路径永久拒绝**；
② `_rotate_cleanup` 恒假 ⇒ 旧 job 目录**永不清理**（磁盘线性增长）；
③ `_dispatch_delayed_eval` 恒判本机 PPO ⇒ 本机 eval 份额错拿 `on_join` 档（白等）。
处置：前两处删闸/改无条件，第三处恒传 `True`，并把两个零调用点的本机 PPO 方法
（`_regate_local_eval` / `_local_gate_epoch_hook`）与 `_eval_gate_early_released` 一并删除。
**教训**：删可选参数后必须**按属性名全仓复扫一次**（含 `getattr` 形态），而不是只看 diff 里删掉的那几行。

**饥饿响亮（§3.3，同轮落地）**：单一路径之后「等 worker」成了唯一的失败形态，而它**不报错**
（C 腿：无人认领静默 3.5h）。`wait_job` 排队期每 `report_every_sec=300s` 写一行，并用
`/jobs/{id}/status` 把两种「还没回」分开：
`等待认领中（state=pending；hub 在线 worker = N）——要算就去控制台起 worker，不要就停腿` vs
`执行中（state=leased；租约剩 Xs、上次心跳 Ys 前）`。worker 数取自 hub 既有的 `/admin/queue`
（`active_workers`，不新造端点）。**踩坑**：报告节流不能按「探测次数」——测试会把 `poll_sec`
调到 0.05，次数节流当场刷爆日志；按墙钟推进下一个报告点。另：`leased` 的收尾语义是「再给一个完整
预算」（H3），小 timeout 下会无限递归——测试必须在后台线程里等、落结果收兵。

**验收（盘上）**：`tests/test_single_ppo_path.py`（5 例：argparse 无 `--ppo`/无 `args.ppo` 属性且传参报错、
无 degrade 旗标、AST 零 `args.ppo` 引用、无 `*PPO*BACKEND*` env 读点、课程 schema 拒 backend 键）；
`tests/test_wait_starvation.py`（3 例：零 worker 响亮 / leased 不喊饥饿 / 可关）；
nn 门禁（ruff+mypy+全量 pytest 1919）、`bun run check`、`dashboard typecheck/test`（967）、`build:ui` 全绿。
细节与调用面清单：`plan/accident.plan.md` §0.1「§3 落地实录」。

---

## §22 continuous volume 报告真源 = 本轮盘上 shard（修 it76 winRate=0 监控盲区，2026-09-20）

**一句话**：配额已满重启后，`_volume_collect_continuous` 零新采（batches=0）时不得把
除零保护的 `winRate=0.0` 写进账本——报告与配额账本同源，从本轮 `traj/it{N}` 下
wver/course 匹配的 shard manifest 重聚合 outcomes。

- **根因**（用户诊断 + `docs/nn/experiments.md` §16 缺口）：`combine_reports` 只统计本进程本轮新采局；
  停机前 quota 已满（50086/48000）⇒ 新进程 while 立刻 break ⇒ `combined=None`
  ⇒ `adopt_volume_report(None)` → `games=0, winRate=0.0`。磁盘上 238 个 shard 的
  `stage_clear` 无人重聚合；账本上「0 局胜率 0」与「N 局全输」不可区分。
- **决定**：`rl/reports.py::merge_volume_report(wave, disk_manifests)` —— 盘上有
  games 则 **以 disk combine 为报告体**，wave 只覆盖时间锚点
  （`pure_collect_sec` / `weights_dist_*` / `collect_end_ts`）。拒绝「仅 wave 为空时
  回填」：重启后本进程可能只补缺口批，wave 有 games 但仍小于盘上全量，会低估。
- **接线**：`loop_core._volume_collect_continuous` 收官处（S4 第十八刀后该簇住
  `rl/loop_volume.py::TrainingVolume`，方法名不变）
  `resumed_manifests(traj_dir, wver, extra_wver, course_fp)` + stages 过滤 → merge。
  `_traj_dir = traj/it{N}` 已是迭代作用域。
- **验证**：`tests/test_rollout_volume.py::test_continuous_restart_quota_met_backfills_winrate_from_shards`
  + `tests/test_rl_reports.py` 三条 merge 不变量；volume/ledger/scheduler/e2e 相关
  113 用例绿。
- **不在 DECISIONS**：bugfix 口径（HOW-TO-ADD §1：防重犯靠测试断言 + 本条）。
- **遗留（未修）**：continuous `start_idx` 重启后从 0 重抽种子流，首批可能整批命中
  盘上已 done 的 pair（dispatch 磁盘回填、不新采）；配额靠后续批推进。与本 bug 独立。

---

---

## §21 本机 PPO worker 也不绑课程：一个进程领所有课程的活（2026-09-19）

**一句话**：`localWorker` 从「每课一进程」收敛为**共享槽单进程**——它和云端 worker 逐字同语义：
只与 hub 通信（轮询领活）、领到什么课的 job 就干哪门课的活、结果按 job_id 回家。

### 为什么这是**事实**而不是需求

`GET /jobs/next` **从来不看课程**：挑活的是 hub 的队列（每课程一条 FIFO + **跨课程轮转**），
响应里的 `course` 只是随包告知的观测字段；job 的 manifest 又自带整份课程快照（`course` 字段
是课程文件正文，worker 照它重建 reward 函数）。于是「按课程键控」只产生三样副作用：

1. 一份进程只能服务一门课；
2. 同机多份进程抢同一份队列里的活；
3. 「这门课的 worker」这个不存在的归属感（它甚至没拦住跨课领活——旧形状下 A 课的 worker
   本来就可能领走 B 课的 job，因为轮询的就是同一个 hub）。

### 六处落地（控制台）

| 处 | 内容 |
|---|---|
| `core/registry.ts` | `localWorker` 进 `SHARED_COMPONENTS`（槽恒 `''`）；新增 `SharedComponent` 类型，换代/停止按它窄化（不再各自写名单） |
| `stack/specs.ts` | `localWorkerSpec(cfg, venv)`——**去掉课程参数**：单一 `tmp/local-worker` work 目录、单一 `tmp/local-worker.log` |
| `stack/local-worker.ts` | 启动先 `supersedeLegacyInstances('localWorker')`（必须在 spawn **之前**），再登记共享槽；新增纯函数 `coursesInLocalMode(cfg, exclude)` |
| `actions/{start,stop,restart,smoke}.ts` | 幂等早退（「一个进程服务所有课程」）；停它 = 本机不再执行**任何**课程的 PPO job（且把这句话说出来）；每课条目**拒重建**；冒烟/日志走共享槽 |
| `actions/preset.ts` | 离开 local 时只在「本课是最后一门 local 课」才停（否则保留并说明）——停共享 worker 会连坐其它 local 课 |
| `web/view/component-groups.ts` | 本机 worker 进服务面顺序；至此**课程面无成员**（workerServe 走节点行）——写明「只渲染一组不是坏了」 |

### 操作员必须知道的三件事

- **停它 = 本机不再执行任何课程的 PPO job**（云端 worker 不受影响）；
- **一份进程同一时刻只干一份活**（与云端 worker 同语义：想要本机并发就多起云端 worker）；
- 离开 local 时若还有别的课在 local，**worker 会保留**（否则那门课的 job 从此无人领取，
  而表面「训练正常」）。

### 判据为什么要从配置算

`coursesInLocalMode` = 「`courses.<课>.remote_transport === 'pull'` ∧ `remote_hub_url ===
sharedHubUrl(cfg)`」——正是 local 预设写下的那两个键。**刻意不按「同端口就算本机 hub」放宽**：
云机 pull 课程写的是 tailnet 地址但**同一个 hub 端口**，放宽会让「把唯一的课从 local 切到 pull」
永远停不掉 worker（正是 2026-09-16 用户反馈要修的那个「以为未启动却在跑」）。

### 训练侧一行未改（这才是重点）

`remote.worker` 本来就与课程无关（`poll_job(base_url, token, …)` 没有课程参数）。新增集成用例
`tests/test_local_worker_multi_course.py`(3) 用**进程内真 hub（HTTP）+ 真 worker 领活函数**钉住：
① 同一 worker 身份依次领到两门课的 job（跨课程轮转 + 响应自报 `course`）；②「先起 worker、
后加课」时同一进程立刻能领新课的活；③ 形参围栏——领活链路里不得出现「课程」（哪天有人给
worker 加 `--course`，这条会红；否则它不会崩，只会表现为「另一门课的 job 永远没人领」）。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest xdist） | ✔ **1570 passed / 4 skipped** |
| `cd dashboard && bun run typecheck` / `bun run test` | ✔ 干净 / **655 pass / 0 fail** |
| 三份 bundle | ✔ all bundles ok |
| 根 `bun run check` | ✔ **1868（1864 pass / 4 skip / 0 fail）** |

### 记录

`DECISIONS.md` §2026-09-19-goalnn-shared-local-worker（含四条否决项与三条未做）·
`plan/multi-course-parallel-training.md §5.2 R3-6`。

### 仍未做

① `workerServe`（本机伪 GPU 节点）的**节点轴**收敛——它的端口按课程派生（R3-1 刚把每课 push
端口摊开防撞），与 push 目标解析耦合，值得单独一轮；② 本机多 worker 实例（worker 身份轴）——
当前并发模型是「一份本机 worker + 云端多 worker」；③ 真机实弹：本机 worker 领两门并行课的真实
PPO job（本轮为夹具级 + 进程内真 hub 的证据）。

---

## §20 R3-5：trainer 收敛为「一个进程服务所有课程」（2026-09-19）

**一句话**：`trainingLoop` 从「每课一进程」收敛为**共享槽单进程**（`run_rl_cluster.py --serve`，
发现模式），于是 hub / 隧道 / selfNode / trainer 四者各只有一个进程就能服务所有并行课程；
RL 与 BC 课走同一条启动路径。

### 之前错在哪

R3-3 把组件卡分成「服务面 · 单例」与「课程面 · 按课程」，但只交付了**读面**：判据表里
`SHARED_COMPONENTS = ['hubServer','cloudflared']`，trainer 仍在课程面。这不是显示问题而是形状问题——
控制台仍按课起 `run_rl.py --course`，于是：

1. 「BC 课 A + RL 课 B」要**两个进程**，而 BC 与 RL 共用 `trainingLoop` 这一个角色键；
2. 训练侧明明已有单进程驱动者（R2d `rl/loop_serve.py`：按课锁 / 按课日志镜像 / 引擎池 / 故障隔离 /
   暂停恢复），R3-4 又让同一个进程能带 BC 课——能力在，入口没换；
3. 用户的验收口径是「hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程」。

### 现在的形状

```
服务面 · 单例  [selfNode 单例] [hubServer 共享] [cloudflared 共享] [trainingLoop 共享]
课程面 · 按课程  [localWorker]
```

启动一次 = 起一个 `--serve` 进程；之后**加课不需要重启**——课程 = 文件系统事实
（`<traj-root>/<课>/training_log.jsonl` 存在），控制台负责替新课把这个文件建出来。

### 六条不可交易的细节（每条都是「不做就会静默地不对」）

| 细节 | 不做的后果 |
|---|---|
| **不给 `--courses`**（发现模式） | 进程绑死课程表，「先起 trainer、后加课」当场失效 |
| **每课旋钮住 rl-config** `courses.<课>.remote_transport` 等（`stack/course-knobs.ts` 唯一写面；python 开课时施加） | 单进程没有「这门课的 flag」；`auto` 会按残留 `push_node_url` 把 job 推给云机（2026-09-17 事故的复发路径） |
| **绝不写进 `curricula/*.jsonc`** | 课程文件字节 = `course_fp` 语料血缘/熔断口径（D14）——加一个传输旋钮会把同一份语料读成新语料 |
| **幂等早退仍做本课准备，但准备失败只说本课** | 说成「trainer 启动失败」会诱使操作员去停/重启它，而停共享 trainer = 停掉**所有**课程的训练 |
| **进程级单实例锁**（`.run_cluster.lock`） | 按课锁拦不住「两套调度器各跑一半课程，每门课都恰好只有一个跑者」 |
| **每课条目拒重建 + 启动时换代接管** | 用共享 spec 重建一个 per-course 条目 = 两套调度器抢同一批 traj |

### 一条被红测试揪出来的真问题（本轮最值得记的一件事）

`tests/training-console-busy.test.ts` 红了：「startComponent 结束后按课键被释放」失败在
`初始权重缺失且 BC 产物不存在`。查下去是两件事叠在一起：

1. **测试夹具不密闭（测试的错）**：R3-5 让幂等早退分支照样做**本课准备**（播权重 / 建账本 / 写旋钮），
   而这个 2026-09-14 时代的用例把断言挂在了「本机 `tmp/c5-gae` 恰好有没有权重文件」上——
   它测的已经不是 busy 键了。修法：夹具重定向 `BCITY_TMP_LOGS_DIR` + `BCITY_RL_CONFIG` 并预置
   本课权重（对真实 BC 产物零依赖）。
2. **生产语义确有毛病（代码的错）**：准备阶段抛错会冒泡成 `startComponent` 的通用失败话术
   「TrainingLoop (trainer) 启动失败: …」——而**进程其实在跑**。操作员照此去停/重启，
   就会连坐停掉所有并行课程。修法：早退分支自己接住错误，消息仍以「已在运行 / 服务所有课程」开头、
   失败只归因到本课，并明说「别停它，停单门课用『暂停』」。

新用例钉住第 2 条（造一个**确定性**的准备失败：`BCITY_TMP_LOGS_DIR` 指向一个普通文件 ⇒
播种/建目录/建账本三路都必抛），与「本机有没有 BC 产物」无关。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest xdist） | ✔ **1567 passed / 4 skipped**（26s） |
| `cd dashboard && bun run typecheck` / `bun run test` | ✔ 干净 / **648 pass / 0 fail** |
| 三份 bundle（`bun dashboard/src/server/build.ts`） | ✔ all bundles ok |
| 根 `bun run check` | ✔ **1868（1864 pass / 4 skip / 0 fail）** |

### 记录

`DECISIONS.md` §2026-09-19-goalnn-shared-trainer-single-process（含四条否决项与三条未做）·
`plan/multi-course-parallel-training.md §5.2 R3-5`。

### 仍未做

① 真机「一个 serve 进程同时带 RL + BC 并行课」实弹运行（本轮全在夹具/假件下证明逻辑，与 R2e 同口径）；
② `workerServe` 账本键的轴仍是课程（展示面已按节点例外声明，账本键未动）；
③ `TrainLaunchModal` 精简（模式仍按课选，落点已改为课程旋钮）。

---

## §19 R3-4：serve 也能带 BC 课（BC 编排体引擎化，单进程 supervisor 收编 BC）（2026-09-19）

**一句话**：`run_bc.py` 从「一个进程服务一门 BC 课」的脚本变成**入口薄壳**，编排体搬进
`rl/bc_loop.py::BcLoop` —— 于是 `run_rl_cluster.py --serve` 那一个进程现在能同时带 RL 课与
BC 课，BC 等云端 GPU 回传时把执行权让给别的课。

### 为什么必须搬（不是「统一风格」）

BC 的 `main()` 是整段 procedural 编排，而 supervisor 要的是**引擎子集**：`_setup()` /
`run_one_round(it)` / `finish_course(it)` / `release_torch()` / `ledger_next_it()`。
`BcLoop` 就是那个子集，与 `TrainingLoop` **同名同义**（serve 的执行体不认识课程种类）。

### 搬迁方式：AST 逐字节，不「顺手改善」

14 个函数体（`collect_corpus` / `publish_bc_job` / `train_local_bc` / `wait_bc_round` 的重构件 /
`finish_all_rounds` / `archive_round` / `resolve_transport` …）用脚本按 `ast` 段落整体搬，
**只在内部调用名上去下划线**（`_append_ledger` → `append_ledger` 等）；搬完用
`ast.dump` 对拍（14/14 逐节点相同）才落盘。`run_bc.py` 剩下的只有：utf8/chdir/单实例锁/
启动前 `git push`/清 hub 停机态 + **阻塞式**驱动（单课程语义：等外部时原地退避重问）。

### 三条新机制

1. **`ROUND_WAIT`（第三种轮终态）**：`retry` = 「我试过、失败了、重做」；`wait` = 「已发布，
   **正在等外部事实**」——本轮未完。调度器据此把执行权交给别的课（不占资源票、不计失败连击），
   过一会儿回来问同一轮。`RoundOutcome.detail` 是它的**人读原因**（带 jid），直接上屏到控制台
   调度器卡片的「在等什么」。
2. **`ledger_next_it` 钩子**（指针语义归引擎）：BC 读 `bc_round_completed`（`rl/bc_ledger.py`），
   RL 读 `iteration`。在桥里写死一种 = 给另一类课程读错指针（BC 课会永远停在 it1）。
3. **`round_tasks_for(course, it)`**：粒度按课程种类选表（BC ⇒ 单个轮任务）。13 步表是 RL 的一轮，
   硬套会给 BC 发它不认识的待办（执行体响亮 ABORT）。

### 重入不重发布（最贵的一条，四处都钉）

BC 的续训按 `jid` 存（hub `/jobs/{jid}/resume`、worker 本地 `bc-resume/<jid>`）⇒ 重发 = 新 jid =
**从头训** = 一轮 GPU 时间白烧。所以「让位后再来问」用内存会话、**进程重启/引擎驱逐后**先
`find_round_job` 认领盘上那份未收口 job（按 manifest mtime 取最新，其余响亮列出但不回收——
它们可能仍在某台云机上跑）。冒烟轮**不认领**（语料口径被压缩过，复用等于拿冒烟语料冒充真语料）。

### 回归

`tests/test_bc_ledger.py`(9) · `tests/test_bc_loop.py`(16：重入不重发布 / 盘上认领 / 已完成轮跳过 /
push 每轮一次 / 本机一枪到底 / 冒烟与回显作废 / 指标增量不重复且失败不致命 / 阻塞驱动收官) ·
`tests/test_serve_bc.py`(5，**serve × BC 集成**：BC 让位时 RL 课照常跑完 / 细粒度下 BC 仍是轮粒度 /
容量 1 驱逐后认领而**不重发** / 锁 = `.run_bc.<课>.lock` / 坏 BC 课不带倒 RL 课)。
既有 BC 用例改指向新家（`test_bc_course.py` / `test_remote_transport.py` / `e2e/test_bc_epoch_e2e.py`）。

### 踩到的坑（值得记）

- `from remote.hub_client import _request` 若写在模块顶层，`monkeypatch.setattr(hub_client,
  "_request", …)` 就**失效**——单测会真去连 hub 并挂满 60s 超时（原 `wait_bc_round` 是函数内导入，
  搬迁时差点丢掉这个性质）。现在 `poll_once`/`ingest_bc_metrics` 保持**函数内导入**并在 docstring
  写明原因。
- 引擎驱逐（容量 1）≠ 只能从盘上重建权重：BC 侧「重建后必须重新认领 job」是**同一条路径的另一半**，
  且它在真机上是常态（容量 1 + 两门课），不是边角。

### 未做

① 真机「一个 serve 进程带 BC + RL」实跑（全在假件下证明逻辑，与 R2e 同口径）；② BC 一轮再下沉
成细粒度步骤。（③「控制台把 BC 课与 RL 课并列」已于同日接上，见 `docs/nn/console.md` §6。）

---

---

## §18 R2d 操作面：进程不绑课程 + 暂停/恢复控制通道（含生效回执）（2026-09-19）

用户定案两点（2026-09-18）：① 控制指令走**控制文件**（不在训练进程里再挂 HTTP 服务）；
② **trainingLoop 进程独立于课程**——没有课在训也能起，队列空着等。本轮落地这两条，并把控制台
「离线开关」之外的另一半操作面（暂停/恢复）补齐。

### ① 发现模式：进程不绑课程

- `--serve` 不给 `--courses` ⇒ `serve(None)`：启动扫 `--traj-root/*/training_log.jsonl`，之后**每个
  空转拍再扫一次**（新课程账本出现即自动开课入队）；**一门课都没有也照常运行**（空队列是合法稳态，
  不是结束条件，`stop_reason` 不再有 `no_courses`）。显式课程表则退化为「只看这几门」，全收官即退。
- 被跳过过的课**不重试**（配置缺失这一轮修不好，否则空转拍每秒刷日志）。
- **`--mode` 必须显式声明**：此前 `--serve --mode goal` 会被 argparse 判成 unrecognized arguments
  而拒启（代码靠扫 raw argv 取，但解析先炸）——声明成 cluster 的参数后原样透传给开课。

### ② 控制通道：意图文件（控制台写、训练侧每拍读）

- `tmp/loop-control.json`：`{"version":1,"paused":["c5"]}`。TS 侧**原子写**（tmp + rename：训练侧
  每拍都在读，半个 JSON = 坏文件 = 静默失效），python 侧容错解析。
- **保守方向**：读不到/解析失败/形状不对 ⇒ 当作没有暂停意图（继续训练）。控制面坏掉不该停掉整条腿。
- **暂停只影响调度**（用户口径「保留队列，不删」）：队列与账本一个字不动，恢复即接着跑。
- 只在**意图变化时**产出日志（每拍都读，不能刷屏）；解析失败也只报一次（记错误签名）。

### ③ 回执文件：意图 ≠ 事实（本轮新增的第三个面）

光有意图文件，控制台点完暂停只能盲猜「生效了没」（进程可能没在跑，也可能还没轮到读文件）。所以训练
进程把**自己实际施加了什么**写回 `tmp/loop-control.applied.json`（`at`/`pid`/`paused`，**只在施加结果
变化时**写，不心跳），控制台用 **pid 存活**核对分辨四态：

| 意图 | 回执（进程活着） | UI |
|---|---|---|
| 无 | 无 | 按钮「暂停」 |
| 有 | 无 | 按钮「取消暂停」+ 虚线徽标「待生效」（悬停分两种：进程没跑 / 还没轮到读） |
| 有 | 有 | 按钮「恢复」+ 实线徽标「已暂停」 |
| 无 | 有 | 虚线徽标「恢复中」 |

**进程已死 ⇒ 残留回执不作数**（否则界面永远显示「已暂停」）——这是回执能被当事实的唯一前提。

### ④ 一条真教训：暂停不算收官

`_all_settled` 原先把 PAUSED 当收官 ⇒ 「暂停一门课」会顺手把整个进程退掉，恢复意图永远没人执行。
现只认 `done`/`aborted`（暂停的课让显式课程模式的进程一直等，`--max-seconds` 兜底；发现模式本来不退）。

### 回归与门禁

- python：`tests/test_loop_control.py`(22：解析边界 / 保守方向 / 幂等 / 坏文件不覆盖 / 回执原子性与
  **未变化不写盘**／pid 语义) + `tests/test_serve_wiring.py`(+8：零课程照跑 / 中途出现的课自动入队 /
  坏课只试一次 / 暂停只停点名课 / 恢复从原处接着跑 / 坏控制文件保守继续 / CLI 转发形状)。
- dashboard：`tests/loop-control.test.ts`(22) + `web-app-loopqueue.test.ts`(+9：四态 / 文案 / 徽标 /
  只读不渲染开关 / route+cache 接线) + `server-api-loop-queue.test.ts`(+1)。
- 门禁：nn python gate **1511 passed / 4 skipped**；dashboard **621 passed**；三份 bundle 构建 +
  根 `bun run check`（1864 passed）+ `bun run build` 全绿。

**仍未做**：**R2e**（多课 × 假 worker/假 PPO 的 e2e + 真机双课并行跑通——serve 的真机行为需要人验；
本轮同样只在假件下验证过）· R3-3（组件卡片分组：单例角色 vs 按课程）。

---

---

## §17 R2d 写的一半：单进程 supervisor 真的能跑（serve + 引擎池 + 日志行路由）（2026-09-19）

R2c 造好了调度器（`loop_scheduler`）与任务体↔引擎的桥（`loop_runner`），但**没有驱动者**——今天仍是
「一门课一个 `run_rl.py` 进程」。本轮交付驱动者：`rl/loop_serve.py` + `rl/engine_pool.py` + `rl/log.py`
的行路由，入口 `run_rl_cluster.py --serve --courses a,b`（默认仍是只读计划视图）。

**分层各做一次**：进程级（utf8/faulthandler/chdir/启动前 push 一次/bun 检查）→ 课程级（参数解析与
`run_rl.py --course` 逐字段一致 / validate_args / **按课程的单实例锁** / 清本课 hub 停机态 / 建引擎对象）
→ 步骤级（`EnginePool.get` → `ensure_ready` → `LoopRunner.executor`，包在 `prefix_scope(课)` 里）。
`ensure_ready` 按**对象身份**判断：对象换了（首用/被驱逐后重建）走 `_setup()`，没换则幂等补栈。

**四个刻意的设计点**：① serve 模式**不收官停车**——新拆 `TrainingLoop.finish_course(it)`（收敛预采/云机
PAUSE/`run_complete` 落账，与单课程路径共用一份实现），停车会冻住其余课；② 不换 `sys.stdout`，课程归属
改成**行前缀 + 镜像写本课 `out_log`**（两个 Tee 会互相复制）；③ 引擎池驱逐 = 响亮的一次「重启」
（权重可从 `args.out` 复原、**Adam 动量重置**；绝不驱逐在用引擎；容量默认 = 并行课程上限 5）；
④ 初次入队的粒度必须匹配执行体（细粒度 13 步 / 轮粒度单个 `round`），且只用判据原语
（`course_facts` + `pending_tasks(round_tasks(...))`），不拉 `plan_course` 的展示面字段。

**回归**：`tests/test_engine_pool.py`(9) + `tests/test_log_router.py`(6) + `tests/test_serve_wiring.py`(8，
含「轮转交替 a,b,a,b」「13 步全表」「让位让别的课跑完」「容量 1 时驱逐重建再 setup」「坏课隔离」
「参数与 `run_rl.py` 对拍」——对拍 oracle 是在子进程里跑 `run_rl.main()` 本体，本机缺当前 era 权重时
跳过并写清理由，其余情况失败即红）。门禁：python gate **1476 passed / 4 skipped**；根 `bun run check` 绿。

**仍未做**：控制台的入队/暂停 + 单例 `trainingLoop` 卡片（读面卡 R2c-3 已交付）；**R2e** 多课 e2e 与
真机双课并行跑通（serve 的真机行为需要人验）。

---

## §16 R2c-3 收口：真机 RSS 实测（checkpoint 缓存上限的真实约束是「数量」）（2026-09-19）

R2c-3 的最后一项：单进程 supervisor 要为 N 门课各持一份 torch 栈，`checkpointCacheMb` 给多少
此前只能拍脑袋（本机 0 卡、remote 为主）。新增 `nn-training/scripts/measure_checkpoint_rss.py`
按训练路径**同一套构建代码**把栈造出来，逐课累加测 RSS。

### 实测（本机 CPU-only torch 2.7.1+cpu，单位 MB）

| 档 | 参数量 | model | grads | Adam | refs | **每课增量** |
|---|---|---|---|---|---|---|
| per-tick（无 ref） | 70,216 | 0.7 | 0.0 | 0.6 | 0.0 | **≈1.3** |
| intent（含冻结 ref） | 74,227 | 0.4 | 0.3 | 0.8 | 0.25 | **≈1.8** |
| goal（含冻结 ref） | 70,566 | 0.4 | 0.3 | 0.8 | 0.23 | **≈1.8** |
| **torch 基线**（与课程数无关） | — | — | — | — | — | **294.5** |
| **N=5 累计**（混合档） | — | — | — | — | — | 294.8 → **301.8** |

### 结论

- **每课栈是 MB 级 ⇒ `checkpointCacheMb` 只是第二道保险，真实约束是数量**
  （`checkpointCacheCourses` = 并行课程上限 5）；推荐 `checkpointCacheMb = 256MB`（≈130 课，
  正常永不触发，真触发就该被看见）。
- **两处最容易把表读错的地方**（已写进脚本 docstring + 用例）：① **先暖一次再测**——torch 惰性初始化
  会让**第一份**栈看起来贵两个量级（实测 78MB vs 真值 0.75MB）；② **栈必须活着**——被 gc 回收后
  第二课的增量会变成 0（分配器复用），累计曲线就是假的。
- **不在本表内的峰值**：单轮 episodes/chunk 缓冲（由 `mb` 与本轮样本量决定）是每轮瞬态，且本机 PPO
  池容量 1（跨课排队）⇒ 同一时刻只有一份；已由 forensics 埋点，不属缓存上限管的常驻量。
- **缓存实现本身未做**：随「单进程 supervisor 真上线」的相位落地（R2d/R2e）——今天没有持有者，
  先写就是无消费者的投机代码；这张表 + 两个默认值就是它上线时需要的全部输入。

### 门禁

`nn-training/tests/test_measure_checkpoint_rss.py`（7 例：推荐值取整/余量、候选表恒有默认架构兼底、
表格必须自带「测的哪份权重 / 跳过了谁 / 理论 vs 实测」、真造一份栈的量级保护带）；
nn python gate **1453 passed / 3 skipped**（ruff + mypy 干净）；根 `bun run check` 绿。

### 记录

`DECISIONS.md`（R2 条目「七续」）· `plan/r2-loop-task-queue.md` §6.1/§6.2（表 + 默认值）与相位表
（**R2c-3 ✅ 完成**）。下一相位 **R2d** 需先定「单例 `trainingLoop` 的操作对象键/迁移路径」。

---

## §15 R2c-3 余下：远端 PPO 三相拆分（发布 / 等结果 / 落位）（2026-09-18）

上一节把 13 步切出来之后，唯一还堵着调度链的就是 **`ppo` 这一步**：它是「打包 → 发布 → 阻塞轮询 →
三重校验落位」一个 400 行函数。闸门不能挂在它前面（= 还没发布就被挡住 ⇒ 永远等不到回传），
所以让位点必须**下沉到这一步内部**。

### ① 三相：同一任务内，不是三个任务

| 相位 | 方法 | 干什么 |
|---|---|---|
| ① 发布 | `_remote_ppo_publish` | 打包（payload/code/blob）→ `publish_job` → WAL attach → **直推时提交** → 会话成型 |
| ② 等结果 | `_remote_ppo_probe`（非阻塞）/ `_remote_ppo_fetch`（阻塞） | 问一句 / 等到 |
| ③ 落位 | `_remote_ppo_land` | 三重校验 + 落位 + job 记账 + WAL 收口 + 结算字段 + 结绳告警 |

`_remote_ppo` 保留为**组合入口**（三相串联），四个既有调用点（本机轮 / 节点轮 / 半离线整段 /
全离线导出）行为不变。

**为什么不拆成 `ppo_publish` + `ppo_wait` 两个任务**：三相共享一份会话（jid / 超时预算 / 打包墙钟 /
运输方式）。拆成两个任务就得把会话序列化到盘上才能跨任务传 —— 那正是 R2b 否决过的「第二份真相」
（`loop-state.json`）。任务粒度只决定让位点密度，而这一步的让位点已经在步骤内部了。

### ② 会话：纯数据，住轮内上下文

`rl/loop_round.RemotePpoJob`（`RoundContext.remote`）：**不进引擎实例属性**（§2.2；单进程多课程会
互相覆盖），**不落盘**（第二份真相）。刻意**不持有 payload 字节**（几十 MB）——直推换节点重发时从
job 目录重读（`find_payload`），于是它会话很小、可打印、读面不触发 IO。

### ③ 探针：状态码分类的唯一实现

`remote/hub_client.probe_job_result`（一次请求）→ 三态：

| 状态 | 含义 | 上层动作 |
|---|---|---|
| `ready`（200） | 结果已落 | 取结果 → 落位 |
| `pending`（202/404） | 还没回（正常排队） | **让位**，下一轮再问 |
| `transient`（网络错 / 5xx） | 「没答」 | 让位，下一轮再问 |
| 410 / status=failed | **终局失败** | **抛 `JobFailedError`** ⇒ 立刻停腿 |

「还没好」与「永远好不了」分开，是 x3-step 事故（把「bun 缺失」写成 25 分钟超时）的直接教训。
hub 与节点两条链路只差端点路径（`/jobs/{jid}/result` vs `/job/{jid}/result`），靠 `path` 注入复用
同一实现；`wait_job` / `wait_result` 两个阻塞版**改建在探针之上**，各自只保留退避策略
（hub 指数退避 / 直推固定 poll —— 历史差异，保留）。`poll_job` / `poll_result` 是给调度器用的
非阻塞出口；超时取 10s（探针误报「还没好」的代价只是再等一轮，永远不会误报成成功）。

### ④ 直推链路：发布即提交

探针要问「那份 job 现在怎么样了」，节点上还没有这份 job 时它只会一直答「还没回」⇒ **提交必须落在
发布相位**（提交本身是有界上传，不是那 25 分钟的等待）。换节点必须**重新提交**（新节点从没见过这份
job）。failover 判决抽成 `_push_over_nodes`：组合入口 `_push_job_round`、发布相位的
`_push_submit_first`、等待相位的 `_push_fetch` 三处共用一份实现（确定性失败 410 的原因在所有节点都
倒时原样上抛）。

### ⑤ 让位点由「谁在驱动」决定：`ctx.resumable`

细粒度驱动器（`LoopRunner`）造上下文时置 `resumable=True` ⇒ 未就绪就 `wait_for`；组合路径
（`run_one_round`）保持 False ⇒ 就地阻塞取结果。于是「组合路径没有让位点」从一个运行期异常
（`RoundYieldError`）变成了一个**事实字段**。

顺带把「在等谁」补进让位结果（`jid`，由 `LoopRunner` 从 `ctx.remote` 取）——状态 + 原因 + 在飞
`job_id` 三件套齐了才够定位一次卡住。

### ⑥ `WAIT_HOOKS` 里不再有 `ppo`（不得加回去）

表里的闸门跑在**步骤之前**，对 ppo 来说那是「还没发布」。让位已由 `step_ppo` 自己产生——
`tests/test_loop_runner.py` 把「不在表里」写成了断言（这个坑已经踩过一轮）。

### ⑦ 验收与实测

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest xdist -n 12） | ✔ **1432 passed / 3 skipped** |
| 根 `bun run check` | ✔ 绿 |

新回归：

| 文件 | 例 | 钉住 |
|---|---|---|
| `tests/test_remote_probe.py` | 14 | 三态分类 · 410 抛而 202/404 不抛 · **只问一次**（预置第二个响应没被消费）· 探针超时 ≤10s · 两条链路端点各自正确 |
| `tests/test_remote_ppo_phases.py` | 8 | 未就绪⇒让位且不取结果 · **再入不重发布** · 不可续跑⇒就地阻塞 · 成功复位连败 · 发布/取结果/落位失败**都**进同一份判决 · 真策略接线（连败到阈值即降级） |
| `e2e/test_loop_supervisor_integration.py` | 改 | ppo 让位用例改为驱动**真三相**（假件只替 `publish`/`probe`/`land`）：a 让位时 b 跑完整轮；`entered` 里 ppo 连着两次（第一次让位、第二次落位）；走完的仍是 13 步各一次 |
| `e2e/test_push_mode_integration.py` | +3 | 发布相位提交 / 换节点**重提交**（断言 payload 真从盘上重读）· 确定性原因优先上抛 · 探针跟着当前节点 |

### ⑧ 踩坑记录

1. **`ast.parse` 不查 `return` outside function**：脚本化拆相时，错误切片让两行落到了模块级缩进，
   `ast.parse` 放行、mypy 才报 `"return" outside function`（`PyCF_ONLY_AST` 只跑解析器，符号表阶段
   的检查在编译成字节码时才发生）。**结论：脚本化搬运之后必须跑 mypy，不能只信 `ast.parse`。**
2. **函数内 import 清单是「拆相前的残留」**：`_remote_ppo` 顶部那份 7 个名字的 import 现在按相位各留
   所需（`git_head`/`iter_shard_dirs`/`pack_code_zip`/`publish_job` 在发布，`wait_job` 在等待，
   `verify_and_land`/`mark_job_completed` 在落位）——否则 ruff F401 立刻红（这正是它的价值）。
3. **测试补丁打在老 HTTP 出口上**：`wait_result` 改走共用探针后，`monkeypatch.setattr(
   "remote.push_client._request")` 不再拦截（真探针出网 ⇒ 用例超时）。补丁目标跟着实现搬到
   `remote.hub_client._request`（**HTTP 出口现在只有一处**）。另外假 `_request` 常把 `timeout`
   声明成 keyword-only，所以探针内部必须**关键字传参**（位置传参会 TypeError，症状是「一调就炸」
   而不是「问了没答」）。

---

## §14 R2c-3：轮内切成 13 步 + 让位闸门（2026-09-18）

R2 第三相位的第三段。用户口径（本轮）：**把轮内切成 13 步细粒度任务（先提 `RoundContext`）**。

### ① `RoundContext`：轮内状态搬家（为什么不放引擎实例上）

原轮体锁着四个轮内局部量（`pairs` / `dist_cfg` / `t_rollout` / `seg`）与一个**会被改写的指针**
（半离线整段 `_remote_run_segment` 一次吃掉 `it..end_it`）。步骤化之后它们必须跨步可见 ⇒ 提成
显式对象 `RoundContext`（`rl/loop_round.py`）。

**不放引擎实例属性**的理由是硬的：单进程多课程下，`self._pairs` 会被别的课覆盖（§2.2 无隐藏状态，
多课程只是把这条老规矩的代价从「难查」变成「串课」）。上下文生命周期 = 一轮，`it` 变了就换新的。

### ② 步骤表是唯一顺序来源

```
ROUND_TASKS (rl.loop_tasks)  ← 唯一顺序来源
     ├── STEP_ORDER  = ROUND_TASKS（同序）
     └── STEP_METHOD : kind → 引擎方法名
                        └── 组合路径 run_one_round 与细粒度驱动器都从表取步骤
```

步骤实现住在 `rl/loop_round_steps.py`（`RoundSteps` mixin，MRO 排最前）。切法**不是重新设计控制流**：
每一行都从轮体搬来，`break`/`return`/`it -= 1` 改由 `StepResult` 表达、驱动器施加。

| 原轮体 | 现在 |
|---|---|
| 顺序 190 行脚本 | `step_precollect_join` … `step_cleanup` 共 13 个方法，一步一个 `RoundContext` 入参 |
| `break` / `it -= 1` | `StepResult.outcome`（`ROUND_*`）由驱动器施加 |
| 该停就停 | `finish(ROUND_STOP)` / 正常 `None` |

**★ 顺序错纠正（真发现）**：`precollect_join` 必须排**第一**——它产出的是本轮 `it{it}` 的 shard，
而紧随的 `prepare_iter` 靠 `completed_pairs` 看盘决定「保留续跑」还是「清场重建」；反了就把上一轮
的预采整个作废（白烧一轮采集）。原轮体里这次 join 坐在 `run()` 循环头，位置一致。

**写测试时挖出的第二个坑**：`run_one_round` 里「让位」必须**先于通用失败分支**捕获。否则步骤
要求的让位会被 `except Exception` 吞成一次普通失败 ⇒ 落 `iter_error`、计连击、无限重试同一轮
（症状与「每轮都重试」完全一样）。为此立了专门的 `RoundYieldError`：组合路径没有让位点，遇到就让
它响亮上抛；细粒度驱动器才支持让位。

### ③ 让位闸门：一张表，且只有两处能装

`rl/loop_runner.WAIT_HOOKS`（kind → 引擎钩子名）。**钩子不存在 ⇒ 不让位**（老引擎行为逐字节不变），
所以表可以只先装能吃的部分。

| kind | 钩子 | 状态 |
|---|---|---|
| `precollect_join` | `TrainingLoop.precollect_ready` | ✅ 本轮装 |
| `ppo` | `remote_job_ready` | ⛔ **刻意不装**（见下） |
| `eval_join` | —（`WAIT_HOOKS` 里没有） | ⛔ **刻意不装**（见下） |

**⛔ `ppo` 必须先拆三相**：今天的 `_remote_ppo` 是一个阻塞函数（打包→发布→阻塞轮询→三重校验落位）。
在它**前面**加闸门 = 还没发布就被挡住 ⇒ 永远等不到回传。要装它得先把「发布 / 等结果 / 落位」拆开，
否则宁可不装。表里留位 + 理由写在 `loop_runner` 的 docstring 里。

**⛔ `eval_join` 不加让位**：本机 eval 局的墙钟是**故意藏在下一轮 rollout 里**的（`_eval_tail` 交棒、
`_dispatch_delayed_eval` 入口收拢）。给它让位 = 把那条尾巴重新串回轮边界，正好抵消掉当初压掉的
软等窗口。`tests/test_loop_runner.py` 把这个「缺席」写成断言，防止后来者顺手补上。

### ④ 已装的那一处：预采（1 小时轮询 → 让位）

`join_precollect_child` 旧形态每 2s 轮询 `completed_pairs`、上限 **1 小时**。单课程前台跑时这只
是「等一下」；单进程多课程下它就是**一个慢子进程拖垮所有课**的入口。

判据抽成 `rl/rollout_phase.precollect_ready`（**非阻塞**：句柄为空 / 子进程已退出 / 就绪 shard ≥
半波），**步骤内部循环改成调同一个函数** ⇒「调度器认为可以往下走」与「步骤进去真的不阻塞」不可能
分叉（两个口径分叉的症状是「调度器说开训、步骤进去还在轮询」，极难排查）。外部语义不变：超时仍
`terminate` + 回合自己采。`min_wave` 也抽成 `precollect_min_wave()`（两处共用同一个数）。

### ⑤ 读面补全「在等什么」

`WAIT` 时把原因落到队列（`q.reason = result.reason`），推进/收官时清空。理由：状态 + 原因 + 在飞
`job_id` 三者合起来才够定位一次「卡住」；而过期的原因比没有原因更坏。

### ⑥ 验收与实测

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest xdist -n 12） | ✔ **1406 passed / 3 skipped**（含 +28 新例） |
| 根 `bun run check` | ✔ 90s 绿 |

新回归：

| 文件 | 例数 | 钉住 |
|---|---|---|
| `tests/test_loop_round.py` | 13 | 表 == `ROUND_TASKS`、`STEP_METHOD` 与实现一一对应、`precollect_join` 必须最前、组合循环按表施加终止/异常分类 |
| `tests/test_loop_runner.py` | 5 | `WAIT_HOOKS` 的 kind 都在表里、`eval_join` 刻意缺席、钩子缺失⇒不让位、不就绪⇒`WAIT`+原因+`resume_at` |
| `tests/test_precollect_ready.py` | 7 | 非阻塞（<0.5s）、半波即就绪、已退出即就绪、与 `join` 同源、超时仍 terminate |
| `e2e/test_loop_supervisor_integration.py` | +3 | 13 步逐一走完 + **步级轮转**（前两步来自不同课）、ppo 处让位、预采处让位；细粒度与轮粒度**账本形状逐行一致** |

### ⑦ 踩坑记录

1. **mypy 只在 mixin 声明依赖属性时才认 `self.x`**——`RoundSteps` 引用了引擎的 50 个属性/方法，
   必须像既有 `TrainingSteps`/`TrainingGuards` 那样声明；类型要**照实际赋值写**（`_total` 是展示用
   字符串 `"0"/"∞"`、`_node_rollout_sec` 是 `float | None`），写窄了立刻红。
2. **ruff B010 vs mypy method-assign**：测试里替换实例方法时，字面量 `setattr(obj, "name", fn)` 被
   ruff 拦（B010），直接赋值被 mypy 拦（method-assign）。两处都用 `_stub(obj, name, fn)`（**变量名**
   传字符串）——这是本仓测试替换方法的标准手法。
3. **细粒度任务占用资源池 ⇒ 测试必须声明池**：`{local_rollout, local_ppo, eval_local}` 不声明时
   `PoolSet` 会响亮报「未知资源池」（比静默放行好，但写测试时容易愣一下）。轮粒度任务（`kind=round`）
   不占池。

下一步：R2c-3 余下 ① `ppo` 三相拆分 ② `Supervisor` 进控制台 ③ `checkpointCacheMb` 真机 RSS 表。

---

## §13 R2c-2：抽出 run_one_round + 任务体↔引擎的桥 + 假件集成测试（2026-09-18）

R2 第三相位的第二半。用户口径（本轮）：**写集成测试验证流程，rollout/ppo/eval 一律用假件**。

### ① `run()` 拆出 `run_one_round(it) -> RoundOutcome`（只搬不改）

原 `run()` 的 190 行轮体搬进 `run_one_round`，控制流逐条保留；差异只在**控制流的出口表达**：

| 原 | 新 |
|---|---|
| `break`（停腿/熔断/止损/门/预算） | `return RoundOutcome(ROUND_STOP, it)` |
| `return`（全离线任务包已导出） | `ROUND_BUNDLE_EXIT` |
| 冒烟作废：`smoke_void = True; break` | `ROUND_SMOKE_STOP` |
| `it -= 1`（三种原地重试） | `ROUND_RETRY` |
| 轮末 `self._consec_fail = 0` | + `ROUND_NEXT` |

`run()` 变成驱动器（预采 join / 预算到点检查 / 按 outcome 施加 / 收官 drain / 停车）；
**`RoundOutcome.it` 必须带回**：半离线整段 `_remote_run_segment` 一次吃掉 it..end_it，
丢掉返回值就会重跑已跑完的那一段。

实现方式：一次性拼接脚本（AGENTS §17.1 的纪律）——先在内存里逐 hunk `assert count == N`，
全过才写盘；写完 `ast.parse` 校验。脚本跑完即删（改动本身在提交里）。

### ② `rl/loop_runner.py`（新）：任务体 ↔ 引擎的**唯一**桥

- `planner`：读该课账本 → `next_it`（账本是 SSOT；队列只是本进程的加速器）；课已收官 ⇒ 空表。
- `run_round`：`RoundOutcome` → 四态（NEXT→DONE / RETRY→`retry(same_iter)` / STOP·SMOKE·
  BUNDLE→DONE(final) 并标记收官 / 引擎异常→RETRY）。
- **`WAIT` 的判据不猜**：只认引擎显式提供的 `remote_job_ready(it)` 钩子。**钩子不存在**
  （今天的 `TrainingLoop`）⇒ 按「跑完即 DONE」处理 = 行为与改造前逐字节一致；钩子存在
  （R2c-3 的轮询化实现，或测试里的假件）⇒ 未就绪就 `WAIT` 让位。
- **粒度诚实记账**：今天一个任务 = **一轮**。轮内 13 步需要的轮内局部量
  （`pairs`/`dist_cfg`/`t_rollout`/`seg`）还锁在 `run_one_round` 里，提成 `RoundContext`
  之后即可细化（R2c-3）。粒度只决定**让位点的密度**，不改本模块与调度器的契约。

### ③ 集成测试：重活全假、记账全真

`nn-training/e2e/test_loop_supervisor_integration.py`（4 例）：

```
真 TrainingLoop（真 args / 真 _setup_common / 真 run_one_round 控制流 / 真 _record_iteration）
真 Supervisor（轮转 + 资源票 + 四态收敛 + 按课隔离）
真 LoopRunner
假：_rollout_phase / _serial_ppo / _join_eval / 预采 / 巡检 / 轮转 / 报告打印 / rl-config 读盘
```

| 用例 | 钉住的东西 |
|---|---|
| 两门课在一个进程里各自跑满 2 轮 | 账本互不串（各自 `[1,2]`）· 前两步来自不同课程（轮转公平，不是「跑完 a 再 b」）· 队列收敛 `done` |
| a 在 it2 等远端 PPO（`WAIT`） | b 先跑完 it1+it2（**让位的证据**）· 在飞集带 `job-a-2` · 时钟推进 + 置就绪后 a 续跑 |
| 进程重开 | 指针从账本重建（`next_it=3`）· **不重写任何已有行、不跳轮** |
| 引擎异常 | 原地重试（RETRY 痕迹）· it 不前跳 · 最终成功落账一次 |

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest） | ✔ **1373 passed / 3 skipped**（28s；+4 集成例） |
| 根 `bun run check` | ✔ 52s 绿 |

### 观察到的一次门禁偶发（非本轮回归）

`e2e/test_multi_course_single_hub_e2e.py::test_single_hub_dispatches_two_courses_to_one_worker`
在一次全量门禁里红过：hub 子进程报「端口 54749 已被占用——拒绝启动（禁止双监听）」。
同文件单跑绿、紧接的全量门禁再跑绿 ⇒ **端口抢占竞态**（xdist 12 路并行下「先探空闲端口、
再启动」之间的窗口被别人抢走），不是改动引入。已记在此处备查；若再现频繁，修法是让
free-port 助手「绑定即持有」到子进程接手（而不是探完就放）。

### 下一步（R2c-3）

`RoundContext` 提出轮内 13 步（细粒度让位）+ 三处长等待真轮询化
（`wait_job` / eval 尾巴 / 预采子进程各加 `*_ready(it)` 钩子）+ `Supervisor` 进控制台
（单例卡片 + 每课队列视图）+ `checkpointCacheMb` 真机 RSS 实测表。

---

## §12 R2c-1：单进程多课程调度核心 + 只读计划视图（2026-09-18）

R2 第三相位的第一半。设计稿 `plan/r2-loop-task-queue.md` §4/§8 同步（R2c 拆成 R2c-1/R2c-2）。

### 三个新件

| 件 | 职责 |
|---|---|
| `nn-training/rl/loop_scheduler.py` | **纯调度**（无 torch/网络/IO）：`PoolSet` 资源票 + `CourseQueue` + `Supervisor`（公平轮转 → 闸门 → 单线程执行 → 四态收敛） |
| `nn-training/rl/loop_plan.py` | 唯一碰盘的 IO 边缘：账本指针（`LedgerView`）→ `RoundFacts` → `pending_tasks`；shard 结算数；`commit_journal` 在飞集；课程发现 |
| `nn-training/run_rl_cluster.py` | 入口：**一个进程**读所有并行课程，输出「下一步 / 待办 / 在等谁（含 job_id）/ 被什么挡住 / 关键事实」 |

### 三条不可交易的性质（测试逐条钉住）

1. **同一时刻只跑一个任务**（单线程执行器）——用「执行体重入即计数、峰值必须为 1」证明；
2. **`WAIT` 不占执行权**——一门课等远程 PPO 时，另一门课跑完自己整轮（时钟注入，测试不真睡）；
3. **故障域按课**——一门课 `ABORT`（门禁停腿 / 5 连击）只脏它自己的队列，另一门课照常跑完。

外加：轮转公平（`a,b,c,a,b,c` 而不是「跑完 a 再 b」）、`RoundFacts` 算不出时不跳（保守方向）、
执行体抛异常 = 一次失败（不打崩调度器）、planner 空转 ⇒ 响亮抛错。

### 两处设计上的新东西（原设计稿没写到的）

- **票可跨 `WAIT` 保留**（`waiting(hold=True)` / `TaskResult.hold`）：本机 eval 的局还在后台跑时
  `eval_join` 先返回 WAIT——**票必须留着**，否则另一门课的本机重资源会插进来把机器压爆。
  这是我写「池闸门」测试时发现的：若 WAIT 一律还票，容量 1 的池在单线程调度器里**永远不会挡住任何人**
  （一个任务体跑完就还票），池就失去了意义。真实形态是「后台仍在干活」⇒ 票必须能跨步持有。
- **「能跑的都被池挡住」= 没事可做**：否则调度器会在两门互相挡住的课之间空转（灌爆 traces、
  单线程纯烧 CPU）。被挡住的事实留在 `blocked_courses`（读面可见），一旦有票释放就清空重探。

### 与数据的实测（真课程目录，只读）

```
course                   it state    next task          pending inflight
s-dodge                   1 ready    prepare_iter            13        0
tiny-a                    6 ready    prepare_iter            13        0
    facts: iterations=5 last_verdict=None train_sec=1.2 soft_remediate=0 kl_streak=0 shards(it)=0
```

`s-dodge` 的 `shards(it)=150` 而 `iterations=0` 恰好演示了「采集完成、账本未结算」这一态——
计划视图说「rollout 仍要做」（`games_planned` 未知 ⇒ 不跳），而真执行体的 `completed_pairs`
会把已结算的 150 局剔除，两者不冲突（前者是**保守**，后者是**精确**）。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest） | ✔ **1369 passed / 3 skipped**（24s；+18 新例） |
| 根 `bun run check` | ✔ 绿 |

### 踩坑

1. **重试计数口径**：`q.attempts.get(tid, task.attempt) + 1` 在**首次**失败时把计数推到 2
   ⇒ 5 连击提前一轮变 4 连击停腿。正确口径 = 「已记录的失败次数」，从 0 起。
2. **测试里 planner 必须有限**：`lambda c, it, q: [Task(...)]`（不带 it 门）会每轮供应任务 ⇒
   调度器永远不 idle，`run_until_idle` 直接撞步数上限。「planner 空转」其实是个**真**故障模式，
   所以调度器侧也留了响亮抛错（而不是静默死循环）。
3. mypy：只读模式的 executor 不能返回 `None`（签名要的是 `TaskResult`）——写成「被调用即 raise」，
   既过类型检查，又把「计划视图退化成真执行」这件事变成响亮错误。

### 下一步（R2c-2）

抽 `run_one_round` + 把任务体接到真 `TrainingLoop`；三处长等待改 `WAIT` 让位（`wait_job` /
eval 尾巴 / 预采子进程）；`Supervisor` 进控制台（单例卡片 + 每课队列视图）；`checkpointCacheMb`
真机 RSS 实测。

---

## §11 R2b 落地：任务模型 + 在飞集；`loop-state.json` 经落地否决（2026-09-18）

R2 第二相位。设计稿 `plan/r2-loop-task-queue.md` §3/§8/§10 同步更新。

### `rl/loop_tasks.py`（新，纯逻辑：无 torch / 无网络 / 无文件 IO）

| 件 | 内容 |
|---|---|
| `Task` | `task_id = course:it:kind`（**幂等键**；重试只动 `attempt`）· `resource` 由 `RESOURCE_OF` 给出 |
| `TaskResult` | 四态 `DONE` / `WAIT(resume_at)` / `RETRY` / `ABORT` + `is_terminal`（DONE/ABORT 出队）；非法状态当场 `ValueError` |
| `ROUND_TASKS` | 一轮 **13 步**：`prepare_iter` → `hot_reload` → `course_iter` → `precollect_join` → `rollout` → `volume_topup` → `eval_dispatch` → `ppo` → `export_weights` → `eval_join` → `record_iteration` → `gate` → `cleanup` |
| `RESOURCE_OF` | 只有 `rollout`/`volume_topup`/`ppo`/`eval_join` 占池；**等待型/记账型不占**——这才是单进程能服务多课程的机制 |
| `RoundFacts` + `already_done` + `pending_tasks` | 幂等判据只认**盘上事实**；`iteration_recorded` ⇒ 整轮为空（★ 不得重写账本、不得重发 job） |
| `resolve_failure` | 与现主循环逐条一致：冒烟作废原地重试 / 死腿立刻 ABORT（不再 5×30s 空转）/ `attempt≥5` 才停、it 不前跳 |

### 在飞集：复用既有 WAL，不建第二份登记表

`rl/commit_journal.py`（I1 的 `started/finish` WAL）新增：

```
attach(phase, round, jid=..., dispatch=..., ts=...)   # 只补事实，**不改状态机**（pending 语义逐字不变）
inflight()                                            # pending + 它们的 job_id / 派发方式 / 开始时刻
```

`_remote_ppo` 在 `publish_job` 拿到 `jid` 后立刻 attach（`jid` 是 publish 的返回值，`start` 时还没有 ⇒
必须另设 op）；`_commit_journal()` 在每 it 首次创建时把在飞集打进日志：

```
WAL replay-check: 1 个未完成提交 [ppo_remote@37 jid=... via push] —— 本地轮由 ppo_ckpt 续跑、远端轮重发同 it job（幂等）
```

### ★ 设计变更：`loop-state.json` **不建**

初稿计划每课一份状态文件（指针 + 在飞集 + 预算）。落地时逐条对照，发现盘上**已有三份权威来源**
覆盖全部四类信息：

| 要落的信息 | 已存在的权威来源 |
|---|---|
| 指针（`next_it`/`rotate_seed`/阶梯） | 账本（R2a `LedgerView`，已接） |
| 预算与门禁计数 | 账本（R2a，已接） |
| 在飞集（job_id / 等谁回传） | `commit_journal.jsonl`（R2b，已接） |
| eval 尾巴意图 / 预采子进程 | `eval_log.jsonl` summary + `(stage,seed,wver)` shard 对账 |

再写一份 JSON = **第二份真相**，分叉方向恰是最贵的一种（续跑读错指针）。⇒ 不建；唯一允许留在内存的是
调度器唤醒条件（`WAIT.resume_at`、资源池票），它可重算。备选否决也记在案：写它当「加速器」（零独立信息并集）、
把在飞集写进账本（账本是**事件流**，在飞是**状态**，混进去污染所有账本读者）。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest） | ✔ **1351 passed / 3 skipped**（25s；+14 新例） |
| 根 `bun run check` | ✔ 38s 绿 |

新回归：`tests/test_loop_tasks.py`（10 例：任务表/幂等键/资源池/四态/幂等铁律/失败语义）、
`tests/test_commit_journal.py`（+4 例：attach 不动状态机、孤立 attach 不造在飞、finish 后不可复活、
**硬死后带 job_id 的在飞集**）。

### 踩坑

1. **attach 不能进状态机**：`pending()` 是「每个 (phase,round) 的最后一条 op」——若把 attach 当普通记录写，
   它的 op 会顶掉 `start`，未完成提交立刻变「已完成」。实现上 `_scan()` 明确把 attach 从 last-op 里剔除，
   只并入 meta（并加了用例钉住）。
2. **别把在飞集塞进 iteration 事件的 `start`**：`jid` 只能 publish 之后才知道；在 `start` 里塞空值再靠
   `finish` 补，等于让「未完成的那些」永远看不到 job_id——恰是最需要它的场景。

### 下一步

R2c：单进程 supervisor（每课队列 + 资源池容量 1 + `WAIT` 让位）+ `checkpointCacheMb` 真机实测。

---

## §10 R2 设计稿 + R2a 落地：门禁与续跑改扫账本（重启不再洗白）（2026-09-18）

用户指令：训练循环任务队列化（「trainingloop 不是请求处理器，是一个有状态的会话」）。本轮交付
**设计稿 + 第一个相位（R2a）**；四个未决问题已由用户定案。

### 设计稿：`plan/r2-loop-task-queue.md`（327 行）

**「有状态会话」的字面证据**：`TrainingLoop.__init__`（`rl/loop_core.py:151`）约 60 个跨轮字段；
`run()` 是 190 行顺序脚本 ⇒ 一步阻塞整条腿阻塞 ⇒「多课程 = 多进程」是形状的必然结果，不是选择。

**状态五分类**（R2 的全部工作量）：

| 类 | 内容 | 归宿 |
|---|---|---|
| A 指针 | `next_it` / `rotate_seed` / 阶梯（= `it` 的纯函数） | 账本（已有） |
| B 门禁指标 | `kl_streak` / `ent_streak` / `stop_loss_streak` / `soft_remediate_count` / 累计量 | **改扫账本** |
| C 在飞集 | `job_id` / 等谁回传 / eval 尾巴 / 预采子进程 | 落**意图**，重启按意图重建（线程句柄不可序列化） |
| D torch 对象 | `_model` / `_opt` / `_ref_model` | 每课内存缓存最新 checkpoint（权重 + Adam） |
| E 轮内瞬态 | `_report` / `_agg` / `_volume_*` | **禁持久化**（否则拿旧数字记新轮） |

**任务模型**：一轮 → 13 个细粒度任务，执行器四态 `DONE / WAIT / RETRY / ABORT`；`WAIT`（等远程
PPO、等 eval 尾巴）**不占执行权**是单进程多课程的关键；每条任务先有**盘上判据**（幂等 guard）——
这同时就是「重放安全」与「扫账本」的同一件事。

**用户定案（2026-09-18）**：① 直接做到 R2c 单进程；② 细粒度步骤 + `WAIT` 让位；
③ 本机 `local_ppo`/`eval_local` 跨课排队、池容量 1；④ checkpoint 缓存上限 = 并行课程上限（默认 5）。

### R2a 落地：`nn-training/rl/train_ledger.py`

`LedgerSpec`（阈值，与 `_breaker` 同源）+ `LedgerView`（单遍 `load_ledger` + `apply_event` 增量）
+ `soft_remediate_count(kinds)`。**不新造账本**：`training_log.jsonl` 已含全部事件，且已有五个扫描器
（`rl/resume.py` × 3、`rl/gate_check.py` × 4）——R2a 只是把它们收敛成一份视图。

接线：

```
loop_lifecycle._setup_common  ← load_ledger 一次（S4 第十九刀前住 loop_core）
    next_it / rotate_seed / ent_peak / train_sec_total / train_samples_total
    kl_streak / ent_streak / stop_loss_streak / soft_remediate_count
events.write_*             → 返回事件 dict
loop_guards._ledger_apply  → 写账本处顺手并入视图（观测失败绝不阻断训练）
events.write_stop_loss     → 止损连击的**状态转移**落账（命中 / 从 >0 回落）
```

**刻意不继承**：`_consec_fail`（重试连击是单腿内的进程护栏，继承会「重启即秒死」）、
`_zero_shard_streak`（口径依赖 `_node_rollout`，而 `iteration` 行没有 `rollout_src` ⇒ 会对节点轮报假事故，
R2b 加字段后再接）。

### 这一相位修掉的真 bug

**I2「提示类门 REMEDIATE ×N 即停腿」原先只在内存计数 ⇒ 重启即洗白。** 边际收益枯竭的 N 次确认
可以从头再来（`c6-pickup3` 6 次 / `c6-bonus` 10 次 cloud halt 那类事故的判据）。现在读**整条账本**
（换新 traj = 新纪元，重新计数）；同类问题的 F4 连击与止损连击一并对齐（止损原先在账本里**毫无痕迹**
——命中只打日志，所以新增 `stop_loss` 事件）。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest） | ✔ **1338 passed / 3 skipped**（26s；+18 新例） |
| 根 `bun run check` | ✔ 21s 绿 |

新回归：`tests/test_train_ledger.py`（15 例：与五个旧扫描器**奇偶断言**、增量==单遍、
独立复算连击、坏行/未知事件透明、提示类计数口径）、`tests/test_train_ledger_wiring.py`
（3 例：`_setup_common` 继承、继承计数当轮停腿、空账本从零起）。

### 踩坑（本轮）

1. **`_ent_peak` 的赋值位置**：原代码在 `if start_it > 1:` 分支里才从账本回读 ⇒ 视图化后必须
   提到**无条件**赋值，否则「空账本但 view 有值」的组合说不清（本轮统一为「视图即真相」）。
2. **`NO_CLOUD_HALT_KINDS` 是 `TrainingGuards` 的类属性**（不是模块级名）——从 `rl.loop_guards`
   直接 import 会 ImportError，用 `TrainingGuards.NO_CLOUD_HALT_KINDS`。
3. **mypy 需要显式声明 mixin 属性**：`TrainingSteps` 里调 `self._ledger_apply` 必须在类体声明
   `_ledger_apply: Any`（与既有 `_jsonl_path: Any` 同法），否则 `attr-defined` 报错。
4. **测试期望值别跟着实现写**：agg 缺失轮（`kl=None`）在 `_breaker` 门控下**既不续也不断**连击
   ——我第一版测试写成「断」（期望 1），实测 2 才是对的；已把这条语义写进断言注释。

### 下一步

R2b：`rl/loop_tasks.py`（任务 schema + 四态执行器）+ 每课 `loop-state.json`；随后 R2c 单进程
supervisor（资源池容量 1）+ R2d 控制台单例卡片/队列视图 + R2e e2e。

---

## §9 采集配额量纲 10× 修（T9）：`est_ticks_per_game` → `est_samples_per_game`（2026-09-15；`plan/x3-power-followup.plan.md` §T9）

**为什么**：`docs/nn/experiments.md` §4 的判负把「transitions = samples」定为口径令，但实现层分母仍是 ticks ——
`target_transitions`（已结算 `nSamples` 之和）÷ `est_ticks_per_game`（ticks/局）⇒ 初波反解出的
局数只有应采量的 ~⅒；补波量按同一个错估缩放（`ceil(缺口/980)`），**补满 3 波仍是零头**，
终以 `wave_cap` 收场（评审复算的「兑现 37% 触顶」用例）。同错另蔓延到三处文档（已一并修）。

**决定**（计划 §T9 的「二选一并写明」）：**键改名** `est_ticks_per_game` → `est_samples_per_game`
（值 = 局均 ticks / K；x3 实测 samples/ticks ≈ 0.1007 ⇒ 980 ticks ≈ 98 samples），
**不取**「公式里显式 `/K`」。理由：K 是 exporter 侧实现细节，课程文件不该编码它（K 变 = 全部课程文件改）；
键名自带单位 + `CourseConfig(extra="forbid")` ⇒ 旧键照写即启动期响亮报错，不可能静默复辟。
（DECISIONS 的 `goalnn-dynamic-rollout-volume` 条目已加日期化修订。）

**落到代码的四处**（全部在 `nn-training/rl/`，数学一行未动）：
- `volume_waves.py`：`initial_games` / `topup_games` / `plan_topup` 形参改 `est_samples_per_game`
  （`G0 = max(1, ceil(达标线/est))` 原式）；文件头新增「量纲」节写死 samples/ticks 换算与 K。
- `resume.py`：`trailing_samples_per_game` 只读 jsonl 的 **`samples`**（**绝不读 `ticks`**）——
  这是同一条 bug 的另一半（首轮按声明值采对、第二轮起又 10×）；无历史且无声明值 ⇒ 响亮 ValueError。
- `loop_core.py`：`_volume_est_ticks` → `_volume_est_samples`（兜底读 `args.est_samples_per_game`）。
- `cli.py` / `config.py`：argparse dest 与课程字段同步改名（旧键走 `extra="forbid"` 响亮报错）。

**同批修掉的文档/注释**：`curricula/x3-start.jsonc`（收官总结「23 万 transitions/轮」勘误 = ticks）、
`curricula/_example-custom-stage.jsonc`（量纲节 + 示例键名）、`plan/dynamic-rollout-volume.plan.md` §1/§2.1。

**钉死单测**（`tests/test_rollout_volume.py`）：新增「① T9 量纲钉死（600000/4/980）」——
同一条 target=600000、4 关：新语义（est=98 samples）`G0=1531` **一波达标**；旧语义（est=980 ticks）
`G0=154` **补满 `DEFAULT_MAX_WAVES`=3 波仍 < 达标线的 1/3**。另加
`test_t9_old_ticks_key_name_is_rejected`（改名护栏：旧键照写必须响亮报错）。桩里 `totalTicks` 刻意写成
`10×samples`，任何误读 ticks 的路径都会 10× 暴露。回归：`tests/test_rollout_volume.py` 52 用例 + 
`e2e/test_volume_e2e.py` 10 用例（真调度器/真 shard）绿，nn python gate + 根 `bun run check` 绿。

---

---

## §8 双轨日常评估（Dual-Track Eval Seeds）：P0+P1+P2 单测/本地 e2e
（2026-09-15；计划 `plan/dual-track-eval-seeds.plan.md`）

**为什么**：日常 eval 恒取 `EVAL_SEEDS[:50]`，17 轮零轮换 ⇒ 选点对这 50 种子过拟合
（实测日常 85% vs 全量 80%）。正式门仍用池外段（0-199/1000+/…），本计划只改日常趋势读数。

**语义（v1）**：
- 锚点轨 `EVAL_SEEDS[:50]`（与历史逐字节可比）+ 轮转轨 `EVAL_SEEDS[50+((it-1)%3)*50 : +50]`
  （池内 50-199 三段轮换，周期 3，与锚点无重叠）。
- **仅** A-eval 且 `eval_games_per_stage==50` 时拼双轨（每关 50→100 局）；it0 基线与
  n_seeds=100/200 正式前缀、冒烟小 n_seeds 一律保持 `EVAL_SEEDS[:n]` 逐字节兼容。
- summary 单行 `wins/games` 口径不变，另加 `anchor_wr` / `rotor_wr` / `overfit_gap_pp`。
- 过拟合报警：近 **3** 轮 `mean(anchor)−mean(rotor) ≥ 5pp` ⇒ WARN（选点看轮转线）；
  单轮 gap 只落账。历史窗按 course_fp 收窄、**不按 wver**（过拟合是跨迭代现象）。

**实现**：纯函数在 `rl/eval_local.py`（`rotor_offset` / `rotor_span` / `dual_track_seeds` /
`should_dual_track` / `split_anchor_rotor` / `overfit_gap_pp` / `overfit_fires` +
种子段注册表注释）；`settle_eval_summary` 拆分台账 seed 落段并写三字段；
`EvalDispatcher.run` pairs 构造处接线（日志带 `[dual-track anchor+rotor]`）。

**验证**：`tests/test_dual_track_eval.py` 10 用例（周期/无重叠/报警 4.9 静默・5.0×3 响・
2 轮不响/summary 三字段/续跑去重/本地 runner 注入 100 局双轨 + it=2 换段 + n_seeds=2
前缀不扩）；相关 eval 回归 53 用例绿；ruff+mypy 189 文件绿；根 `bun run check` 1825 pass。
**全量 nn pytest xdist 在本机环境 ~34% 处稳定 KeyboardInterrupt（排除本测试文件后复现）**
= 环境问题，非本改动；单文件/相关子集绿。

**未做（按计划非目标/后续）**：EVAL_SEEDS 扩池、正式门口径、dashboard 必改、
试点 10 轮 wall/gap 基线（需集群无在跑腿时实弹）、课程头模板批量改写。

---

---

## §7 按样本量动态采集（Dynamic Rollout Volume）：P0+P1 实现与 P2 端到端验证
（2026-09-15；计划 `plan/dynamic-rollout-volume.plan.md`；决策 `DECISIONS.md §2026-09-15-goalnn-dynamic-rollout-volume · 全文 → 本文件 §25`）

**为什么**：PPO 吃的是 transitions，不是局数（x3 240 局×967t ≈ 23 万/轮，c4-dodge 600 局×1100+t
≈ 66 万+/轮），而课程至今用 `seed_rotate`（每关局数）配采集量——固定局数下 transitions/轮随局长
漂移（x3 局均 700–1150t）。**只承诺「量准」，不承诺涨点**（反例：x2-acbc 3 倍密度 30 轮 pooled −3/400
——加量解决的是方差不是信号）。

**实现**：课程三键 `target_transitions` / `est_ticks_per_game` / `max_games_per_stage`（缺席 = 老行为
逐字节不变）。纯逻辑 `rl/volume_waves.py`（配额数学 + 按 (stage,wave) 独立种子流 + 终止谓词 +
补波计划 + WAL 行解析）；账本 `rl/resume.settled_stage_totals`；est 来自 jsonl trailing 均值
（`rl/resume.trailing_ticks_per_game`，纯函数可 replay）；接线 `rl/loop_core.py::{_iteration_pairs,
_volume_topup,_dispatch_volume_wave,_volume_journal_replay}`；iteration 事件追加
`transitions_target/_collected/_capped`。**v1 边界**：只接串行路径（stream 保持老语义 + 写日志说明），
与 curriculum/rotate 门控窗口不兼容（响亮 SystemExit）。

**P2 端到端验证**（`nn-training/e2e/test_volume_e2e.py`，10 用例；假 sim 节点 HTTP 经**真调度器**
`run_rollout_queue` 落真 shard，再走真账本与真补波循环；跑法 `cd nn-training && .venv/Scripts/python
-m pytest e2e/test_volume_e2e.py -q`）：

| 用例 | 场景 | 观测（日志原话） |
|---|---|---|
| 定额收敛 | est 高估 20 vs 实际 14，达标线 100/关 | `G0=5/关` → `w1 {0:2,1:2}`（缺 30）→ `w2 {0:1,1:1}`（缺 2）→ `收官 waves=3 collected=224/200 达标关=2/2`；过冲 12t/关 < 1 波 |
| 初波即达标 | est 与实际都是 20 | `waves=1 collected=200/200`，零补波、零额外派发 |
| 跨重启重放 | 3 波收敛后崩在 w2 中间（删其 shard + 抹 WAL 的 finish） | 重启日志：`WAL 重放未完成的补波 w2 games={0:1,1:1} → 2 局`；初波 pairs 与重放 pairs 与首跑**逐字节同一**；预算不重领（重放后即波次上限） |
| 长短局独立 | stage 0 = 5t/局、stage 1 = 50t/局 | 长局关一次到位后**零补波打扰**（`w1 games={0:5}`，stage 1 缺口 0）；短局关被补到 `wave_cap`（`未达标={0:'wave_cap'}`） |
| 掉局补采 | stage 0 前 2 局零样本（真派发真落盘 transitions=0） | `w1 games={0:2}` 补回；该关 `(games=10, transitions=112)` ≥ 100（零样本局占局数不入配额）；正常关不被牵连 |
| 硬顶打标 | `max_games_per_stage=12` | `WARN 触单关局数硬顶（cap=12）但配额未满 shortfall={0:40}` + 事件 `capped`；该关停采 |

**④ 尾部竞速（tail fan-out）下的配额算术**（2026-09-15 追加，4 用例，`tailFanoutN/Dup=4/2`）：

| 用例 | 场景 | 观测（日志原话） |
|---|---|---|
| 副本不污染账本 | `dup_hang=0.35`（副本稳定后到、静默丢弃） | 账本与无竞速**逐值相同**（`(8,112)`/关），`dup_copies>0`，`fanout_settled=3` |
| double-settle 边界 | `dup_hang=0`（副本同速） | 账本**绝不重复计数**（`settled = games×14`、`games ≤ 去重派发对数`）；缺口只允许出现在预算耗尽时（`波次预算未耗尽却未达标` 是本用例最硬一条）；`_volume_collected` 与账本一致 |
| 丢局被补回 | 手工落「dup_settle 那一刀」（rmtree 共享 shard 目录），派发关竞速 ⇒ 损失是唯一变量 | 初波 `(8,112)`/关 → 退休 2 局/关 ⇒ 账本 `(6,84)` → `缺口 16t/关` ⇒ `w1 {0:2,1:1}` 补回 `(8,112)` 且 `waves=2`、WAL 无 pending |
| 预算耗尽要响亮 | 短局关（5t/局）+ 竞速 + `wave_cap` | `wave_cap` 停采时日志给出 `未达标={0:'wave_cap'}`（不是假装达标）；长局关 500t 一次到位不被拖累 |

**竞速取证（新发现，值得后续跟进）**：同节点 fan-out 的两份副本 `out_dir` **逐字相同**
（`traj/itN/dist/<node>/rl_s{stage}_seed{seed}`）——两份结果若都在对方进 `seen` 前通过锁外
`validate_result`（窗口 = 一次 `write_shard`），后到者走 `dup_settle` 分支 rmtree **共享目录**，
那一局数据被删（两行日志坐实：`dup settle s0/seed… — dropped (+retired …\rl_s0_seed…)`）。
单节点 + `dup_hang=0` 下实测每轮 3–5 局被退休（`DEFAULT` 多份副本时更常见）：
`dispatched=25 unique=19 settled={0:(8,112),1:(8,112)}`，即 3 局白跑、补波补回 2 局、
预算被吃到 `wave_cap`。**方向性结论**：丢局只会让账本变短（绝不膨胀），配额达标性由补波兜住，
代价是墙钟——与「宁可多采几局，不可静默少采」一致。跨节点竞速（race lane）不共享目录，
不存在这种损失。

**e2e 拓出的两个真 bug（P2 的全部价值就在这里）**：

1. **账本只认一种 manifest schema**：`settled_stage_totals` 原先只读 `nSamples`（TS exporter 的正规单局形），
   而队列/远端 path 的 shard 是聚合单局形（`totalSamples`，`dist_common.write_shard` **原样写** agent 返回的
   manifest）⇒ 那些关在账本里永远「零样本」、补波永不达标，只会白烧墙钟到波次上限。修：两种都认
   （`nSamples` → 回退 `totalSamples`），两者都缺则计 0（不猜）。单测钉死（`test_settled_totals_accept_both_manifest_schemas`）。
2. **波次序号 `wave_idx` 是决策，不是账本的函数**——它同时是种子流的键。首版设计假定「全部决策都是
   (配置, it, 账本, jsonl) 的纯函数 ⇒ 重算即 replay、无需 WAL」，**该假说被 e2e 证伪**：崩在 w2 中间后重启，
   计数器回到 1，会拿 **wave-1 的种子去补 wave-3 的缺口**（同观测史、不同波次序列——正是计划 §2.3.1 要禁止的
   「重新抛硬币」）。修：`wave_idx` 从 commit journal 回读（`parse_wave_records`）——预算按 WAL 续算
   （跨重启**不重领额度**，防「崩一次就再领 3 波」），停在波中（有 start 无 finish）的那一波按 WAL 的
   对局表**原样重放**，其余继续按账本推进。

**验证**：单测 46 用例 + P2 e2e 10 用例（含 4 个竞速用例）全绿；e2e 全量 70 用例绿；
nn python gate（ruff + mypy 188 文件 + pytest xdist）、根 `bun run check` 1826 用例绿。

**补波决策 overhead 实测（2026-09-15 微基准，纯函数）**：`plan_topup` 6.1µs ·
`parse_wave_records` 28µs · `wave_pairs`(80 seeds) 236µs；典型 3 波决策包 ≈ **754µs**，
相对真 bun 试点单轮 wall ~24s = **0.003%**（DoD 预算 <1%，过两个数量级）。脚本一次性，
已删。

**真 bun 微课试点（2026-09-15，串行路径 + 真 sim；教训见下）**：5 轮跑通，volume
初波/补波/WAL/收官日志全链在位。观测：S1 + `max_ticks=120` 下 timeout 局只产出
~12 samples/game（远低于 est=40）⇒ 3 波后 `collected=144/400`，正确触 `wave_cap`
并响亮打标（`未达标={1000,1001:'wave_cap'}`）——**不是静默少采**。est 用 trailing
均值后它被抬到 120t/局（jsonl 口径是 tick 不是 nSamples），说明生产课的 est 必须
按 **nSamples/局** 校准，不能拿 max_ticks/局长 tick 顶替。

**污染教训（用户 2026-09-15 指令，铁律）**：集成/微课试点用的模拟课程**禁止**
写进 `nn-training/curricula/`（生产课程表）或把试点权重归档进 `weights/`；一律
落在 **`nn-training/e2e/fixtures/`**，经 `--course-file e2e/fixtures/...` 启动且
out/traj 指到测试 tmp。本次误把 `tiny-vol.jsonc` 放进 curricula 并归档了
`rl-weights.it1-5.*`，已全部清除；模拟课程规范落点 =
`e2e/fixtures/tiny-vol.jsonc`。另：真 bun 试点会打真实 dist 节点（self/mac ping、
甚至触发 mac 升级请求）——**隔离未就绪时不得再对生产节点跑试点**；接线验收以
`e2e/test_volume_e2e.py`（FakeAgent，不读生产课程表）为准。

---

---

## §6 本机 PPO 拆分为独立 worker（2026-09-15，用户指令「把本地 PPO 拆分为一个独立 worker，可以随时启停，与云端 worker 一致，同样支持 pull/push 模式」）

**变更**：本机 PPO 不再是 `TrainingLoop`（run_rl）进程内的阻塞调用，而是新的受管组件
`localWorker`——跑的就是**云端同一个入口** `python -m remote_worker --poll
http://127.0.0.1:<本课 hub> --out tmp/local-worker-<课> --device cpu`（配了
`rl.torch_threads` 才透传 `--threads`）。协议/租约/心跳/幂等重拉/热替换退出码 86 + 内部
监督器重拉**零分叉继承**；push 侧不新增实现（执行面 = 既有 `workerServe`）。控制台 `local`
预设改义为 `hubServer → localWorker → trainingLoop(--ppo remote)`，进程内 PPO 不再是
控制台选项（`--ppo local` / run_bc `--local` 保留给直调 CLI 与 R9 远端失败降级落点）。
决策与拒绝的备选：`DECISIONS.md §2026-09-15-goalnn-local-ppo-worker · 全文 → docs/nn/remote-transport.md §32`。

**唯一的新语义（也是最贵的一个坑）：`--remote-transport {auto,pull,push}`**。`_remote_ppo`
的历史优先级是「rl-config 里本课 `gpu_push` 节点 > hub」——某课用 push 跑过一次后
`courses.<课>.push_node_url` 就留在配置里，于是 `local` 预设会把 job **静默推去云机**，
本机 worker 永远领不到活，而账本/日志看起来「训练正常」。所以 local preset 必须钉
`--remote-transport pull`（run_rl 与 run_bc 同步支持；`auto` = 历史行为零变化，非法组合
响亮 `SystemExit`）。刻意**不**动 `rl.remote_hubs[<课>]`：它是 pull preset 隧道 URL 的家，
而 `stepCloudflared` 复用已建隧道时不会重写它——写本机 hub 进去会把 pull preset 悄悄改成
打本机 hub；本机 hub 只经显式 `--remote-hub-url` 注入。

**push 侧同样可用本机执行面（同日追加，用户指令）**：`configurePushEndpoint` 改为三档裁决
（用户填 endpoint / 复用 config 可用 `gpu_push` / **回落本机 worker_server**），回落时写
`local_push` 节点 + 课程 `push_node_url` 指向本机、预设多起一个 `workerServe`——云/本机两份节点
条目共存互不覆盖（绝不静默吃掉用户填的云 URL）。详见 `DECISIONS.md` 同条目的「追加」bullet。

**整树停止**：`localWorker` 是「父 supervise_worker + 子 worker_loop」两进程，停/重启走
`dashboard/src/core/net.ts::killPidTree`（Windows `taskkill /T /F`；POSIX 先验 `pgid === pid`
再组杀，否则退回单进程 stop），判定唯一来源 `stack/specs.ts::COMPONENT_KILL_TREE`——只杀
父进程会留下继续轮询 hub 抢 job/抢租约的孤儿，「随时启停」名存实亡。

**验证（2026-09-15）**：dashboard `tsc` 干净 + 351 pass / 0 fail（59 文件，新增
`tests/local-worker.test.ts` 11 用例：spec 形态 / poll 目标 / killTree / 双课隔离 / pull 注射 /
重建逐字段一致 / 接线 grep 门禁）；根 `bun run check` 1819 pass / 4 skip / 0 fail；
nn-training python gate 绿（新增 `tests/test_remote_transport.py`：run_rl 与 run_bc 两侧裁决
+ argparse 默认值与 choices）；`bun dashboard/src/server/build.ts` 三份 bundle 与根 `bun run build`
均通过。顺手补了 HEAD 上已红的 `selfNodeSpec.cwd`（控制台以 dashboard/ 为 cwd 启动时的
`Module not found` 回归护栏）。

---

---

## §5 课程热加载：非语料改动下一 iter 应用；语料改动拒绝+横幅+不泄漏云端（2026-09-13，用户拍板）

**机制**（DECISIONS §2026-09-13-hot-reload · 全文 → 本文件 §25）：trainer 每 iter（rollout 前）重读课程文件，按
`corpus_identity_fp` 分流——

- **未变（B/C 类）**：`rl/hot_reload.apply_hot_fields` 白名单写回 args（消费点每轮活读：
  iters/gamma/lam/lr/epochs/mb/eval_*/ent_break*），**下一 iter 生效**；结构绑定字段
  （bc/workers/out/traj/backup_*/freeze*/update_kwargs 一次构建族）记 restart-only（`*`），
  响亮日志不静默；max_hours 热应用重算 deadline。
- **变了（A 类）**：拒绝 + `course_edit` 事件（本地账本）+ 控制台错误横幅
  （`courseEditFromLedgerTail` 取尾部窗口最近一条，持久显示；改回文件 → trainer 写
  restored → 横幅消失）；**沿用启动配置继续训练**。整单拒绝（iters+wChip 同改不部分应用）。
- **不泄漏云端**：课程字节启动期冻结（`args.course_frozen_bytes`），D13 快照/course_fp/
  shard `--course-fp` 三处统一用冻结源——任何 mid-run 编辑永不进 job payload。
- 半行写/坏档：沿用旧配置静默重试，不打横幅。

**验证**：`test_hot_reload.py`（分类学/整单拒绝/restart-only 记账/冻结 fp 免疫编辑/字段清单）
+ `console-course-edit.test.ts`（最近一条胜出/restored 顶掉 rejected/半行竞态/防御）；nn-python-gate
绿 + `bun run check` 绿（2067 pass）。**顺带**：F-B6 夹具 s-dodge→c6-dmgfix（原 bc 文件
tmp/s2-cap/weights.json 已被 tmp 清理移除，环境性失败——tmp/ 不该被测试当稳定依赖）。

---

---

## §4 关卡配置抽离 + D14 语料身份改语义哈希（2026-09-13，用户拍板）

**事故**：mid-run 编辑 c6-chip 课程（iters 30→60）触发指纹脑裂——shard manifest 与 job
envelope 各自在采样/发布时刻重读课程文件字节，D14 把 mismatched shard 整轮拒收（job=abbf…
shard=e6c6…，rollout 卡 it16）。原则修正：**iters 类预算旋钮不改训练契约，mid-run 编辑不应
要求新课程**（我此前把「改文件」与「改语义」混为一谈）。

**分类学**（DECISIONS §2026-09-13-level-extraction · 全文 → 本文件 §25，全量版）：A 语料身份（关卡 env + reward
语义，改动=破裂）／B 训练语义非语料（bc/优化器/λ，改动=§15.5 新实验，stop→start 生效）／
C 预算测量路径（iters/eval_*/out 等，随时可改）。

**第一步落地**：

- `nn-training/levels/arena6.jsonc` / `arena4.jsonc`：环境语义（地图/敌人队列/敌数/出生点/
  **命/星**/difficulty/max_ticks）独立成关；课程以 `"level": "arena6"` 引用，内联重复声明 =
  raise；内联旧用法逐字节兼容。
- `corpus_identity_fp`（rl/config.py）：语料身份 = env+reward **解析值**规范化哈希（内联与
  level 引用同形同指纹）；经 `--corpus-fp` 进 shard manifest（exporter TS 双写点）、经
  publish_job 进 job manifest；worker `d14_corpus_match` 优先比 corpus_fp、任一侧缺席回退
  legacy course_fp。course_fp 文件血缘保留（D13 快照/resume 对账不变）。
- c6-dmgfix 已迁移引用 arena6（预检 validate_reward ok）；c6-chip 在跑不迁移。evalB/探针
  直接吃关卡文件（`eval-course-ckpt.ts --course nn-training/levels/arena6.jsonc`）。
- 门禁：nn-python-gate 绿（21s）+ `bun run check` 绿（2031 pass）；新测试
  `test_course_level.py`（合并/冲突/兼容/fp 分类学/同形同指纹）+ `test_d14_corpus_match`。

---

## §3 多课程并行训练（multi-course parallel training，plan/multi-course-parallel-training.md）

- **性质**：纯工程化改造（课程 = 并行单元），不碰任何训练算法；PPO/BC/课程/熔断/奖励公式零改动。
- **已落地**：
  - **P0**：`tests/training-multi-course.test.ts`（双课 spec/锁/配额/门禁）、`nn-training/tests/test_multi_course_locks.py`、`docs/multi-course-audit.md`。
  - **P1**：`tools/training/slots.ts`（端口算术 `hub_base + slot*10` 唯一归宿 + `checkCapacity`/`allocateSlot`）；锁 per-course（`.run_rl.<course>.lock`/`.train_loop.<course>.lock`，无课程沿用旧名）；账本 `Record<course, Entry>` 四新键 + 旧扁平键读兼容（R1）+ 旧条目一次性回填迁移；`train.ts` 双锁 per-course 预检 + `kill (script, course)`（R3）；`git push` 串行化 `.git_push.lock`。
  - **P2**：hub per-course（jobRoot/jsonl/端口），BC 种子 `courses.ts::seedWeightsFromBc`（缺文件 fail loud）。
  - **P3**：cloudflared/workerServe per-course；`rl.remote_hubs[course]` + 兼容单键；`gpu_push` 清单按 `push_node_url` 过滤（非空零匹配 → WARN + manifest 打标，不抛）；notebook 引导文本。
  - **P3b**（DECISIONS §2026-09-12-multi-course-p3b-supersedes-343 · 全文 → docs/nn/remote-transport.md §32）：hub 独占租约 `CLAIM_TTL_SEC=300`（领取即设 owner+expiry+heartbeat，心跳续租，过期回池，有活租约验 `X-Lease-Token`）；worker_server 有界 FIFO `WORKER_QUEUE_MAX=8` + 同 jid 幂等 + `/ping queued`。
  - **P4**：`saveConfig` 落盘前 `capacityError` 加法校验（`Σ eff ≤ max(rl.workers, rl.local_slots)`）；`rl/config.py::resolve_course_quota` 热读 `courses.<课>` 优先 + 响亮行 `[quota] workers X -> Y (multi-course split)`；连续 2 轮零 shard 落盘告警；manifest `course_name`（审计短名，不进幂等键） + dispatch 报告带 course；EvalBoard `batches.jsonl` 跨进程 `claim.lock`（复用 `train.loop_util` 锁，拒绝第三套实现）。
- **P5**：`ConsoleState.activeCourse`（additive + 旧 `course` 回填）；`cloudHalts` per-course（S17，旧 `cloudHalt` 一次性迁移）+ 横幅按课 `立即恢复`；busy 键按课程（S10）+ 监督/watchdog 三元组；`restartSpecFor(key, course)` fail-closed。
  - **W4（R2：删旧扁平账本键读写）**：`loadRegistry()` 每次加载把旧扁平单键（`hubServer`/`cloudflared`/`trainingLoop`/`workerServe` + hub-start 分文件账本）**搬迁**进 per-course 表再删键（`migrateFlatCourseEntries`，缺 `course` 取 console-state、缺 `slot` 取 0；同课已有新条目则保新弃旧）；迁移后 `entryForCourse` 严格按课（不再兜底扁平键）、`registryTriples` 不再枚举扁平键、`saveComponent`/`clearComponent` 类型收窄为 `SingletonComponent`（课程组件写不了扁平键）、`Registry` 类型**不再声明**扁平键（`LegacyFlatRegistry` 只给搬迁读）。修掉 `specs.ts::trainingLoopSpec.healthy` 的遗留扁平读（改 `entryForCourse`，此前多课下恒 false）。
  - **W5（LAN 只读回归）**：门控判定抽成纯函数 `net.ts::isReadonlyAction(method, ip)`（`server.ts` fetch 首行唯一调用点），使「LAN 的 POST 必 403 / 回环放行 / GET 放行 / 来源不可得 fail closed」成为可回归断言；另加一条接线回归（扫 `server.ts` 确认门控与 403 仍在）。
- **P5-W2 同屏多课总览**（已做）：`/api/state` 新增 `courseOverviews`（每课一行：阶段 + 四组件状态点 + 最近一轮 it/胜率/采集·训练耗时 + 停机/排队超时徽标，账本判定进程状态、**不发健康探测**，与慢快照同拍 5s 缓存、课程上限 = 槽位数 4）；新面板 `console/ui/panels/MultiCourseOverview.tsx`（单课自动不渲染，点课程名即切换查看）。完整指标表/节点池仍为「选中课程」视图——总览一行点开即钻取，避免 N 课 × 每拍节点 ping。`/eval` 页已原生支持多课程（checkboxes + 逐课视图 + CSV 归属），无需改。
- **P2 验收补跑 + run_rl 锁跨平台修复（2026-09-13，验收被用户叫停）**：P1 验收（9-12 上午，s5-open20/s-dodge/s1-cap 各 ~1 轮）遗留的 stale 锁让双课验收重启当场两连崩——`run_rl._runrl_pid_alive` 把 Windows 的 `ctypes.windll` 分支写成了**无条件**路径，Linux 上一遇已存在的锁文件（无论持有者死活）即 AttributeError：陈旧锁永不清理、同课双开变崩溃而非响亮拒启（DoD「2026-09-06 事故回归」在本机形同虚设）。当日没暴露是因为 P1 首跑时锁尚不存在（O_EXCL 新建路径未走 liveness 分支），且 `test_multi_course_locks.py` 只覆盖 `loop_util.acquire_lock`、run_rl 锁的 stale 分支零覆盖。修复：按 `os.name` 分支（Windows 分支原样保留；POSIX 走 signal 0 存在性探测，宽捕获同 `loop_util._pid_alive`）；红→绿回归两例（stale 接管 / 持有人活着的同课拒启）。
  - **被叫停前的部分活体证据**（s1 + s-dodge 并行 ~20 分钟，第一轮 PPO 阶段中杀停）：双课 stale 锁自动接管并各持本课锁；采集 shard 零交叉（s1 全为 stage 1000-1002、s-dodge 全为 1050-1052，各 50×3）；rotateSeed 独立（1789176181 vs 1789176208）；C5 push 串行化响亮跳过（`另一进程正在 git push` WARN）。权重文件零改动、无残留 ckpt，下次真实启动按 resume 语义干净续跑。
  - **预算实测（§16.1/16.4）**：本机（0 卡）单轮 = 采集 ~6-8 分钟（150 局 / 8 workers）+ CPU PPO **~1.4 小时**（119 chunks × 4 epochs）→ 5 轮 ≈ 7-8 小时；且 per-tick 的 KL 熔断（0.15/3 连击，硬编码不可 CLI 放宽）大概率在第 3 轮自我停车。**plan P2 的「双课各跑满 5 轮」在本机不现实**——要么 remote GPU 路径，要么用户显式批长的本机跑。**用户指令（2026-09-13）：agent 不得启动实际训练**；后续任何实跑验收需用户明确批准。
  - **流程验证完成（2026-09-13，用户批准的轻量方案）**：新增微型验证课程 `curricula/tiny-{a,b}.jsonc`（1 stage × seed_rotate 2、max_ticks 150、epochs 1、mb 64、lr 3e-5、eval 关、iters 5 自停——**纯课程数据行，零算法代码改动**；同类先例 = s1 夹具课程「不再实际训练」，用户 2026-09-02 决策）。双课并行真实全链 5 轮 × 2 课 **19 秒**完成：iteration 1→5 单调零错、rollout 0.4-0.7s/轮、PPO 0.1-2.0s/轮；隔离证据 = shard stage 互斥（1000 vs 1051，各 10 shard 零交叉）+ course_fp 各异（761b29… vs 9e33ef…）+ rotateSeed 独立 + 每轮 (rotateSeed, it) 全新 seed（§15.1 轮转真实生效）+ 双课锁并行持有后干净自清；C5 push 串行化响亮跳过（WARN 行）同窗复现。**教训两则**：①课程缺 `reward` 块会 FormulaError fail-loud（写夹具课程别漏 version/player/reward）；②真课程全量验收（hub/隧道/账本实弹与 `(course, job_id)` 抽查）仍待用户批准，账本/端口/隧道半由单测覆盖。
- **P5 状态**：**全部完成**（W1–W5）；余 P6 归档（DECISIONS 总条目 + DoD 勾选）。
- **纪律**：配额只住 `rl-config.json` 的 `courses` 块，**永不写 `curricula/*.jsonc`**（一改 `course_fp` 即触发 D14 熔断误判，plan C1）。
- **门禁**：`bun run check` 2009 pass、`bun run build`、`make -C nn-training python-gate` 全绿。
- **merge 复核（85e740d，origin/goal-nn 并入）**：冲突 3 文件（进度日志节号撞车 → 我方改 §36；consoleStatePath 归宿取 paths.ts；停机横幅取按课版 + 追加对方 loopComplete 完训横幅）；两处测试随合并 API 修正（consoleStatePath 导入源、buildExitMarker 补 course 参）。合并后全套门禁绿（check/python-gate/build），**tiny-a/b 双课 5 轮流程验证重跑 PASS**（隔离证据与首次一致：stage 互斥/course_fp/rotateSeed 独立/零错误）。**行为变化知悉**：对方分支把「跑满 iters」从进程退出改为**停车不断开**（等待控制台重启 / EvalBoard B 批认领）——锁在停车期保持持有，验证完需按课停止（S14 kill-previous 被 preflight 挡住时直接 SIGTERM 停车 PID，残留 stale 锁由下次启动的接管路径自动清理）。
- **merge 复核 2（00e387d，关卡抽离 + corpus_fp 语义血缘并入）**：publish_job 的
  `course_name`（归属短名）与 `corpus_fp`（D14 语义血缘）两 kwarg 并存，调用点双传；
  进度日志节号二次撞车 → 对方节改 §37 置顶。合并后 check/python-gate/build 全绿；
  **tiny-a/b 双课 5 轮重跑 PASS（24 秒）**，且新字段全链落位：shard manifest 同时携带
  course_fp（761b29…/9e33ef…，与历次隔离证据同值）与 corpus_fp（4bd04f…/674cdf…，互异）
  ——归属审计与语义熔断两套字段互不干扰。预算类课程编辑不再触发 D14 拒收（§4 分类学）
  不改变本节纪律：机器配额仍只住 rl-config 的 courses 块。停车进程验证完即 SIGTERM 清理。

---

---

## §2 门判决永不停车 → 只联动云机停机/恢复（2026-09-11，用户定案，DECISIONS §2026-09-11-gates-never-park-loop）

- **事故与根因**：c4-dodge 一天内两次"设计内停车"——G13 占空比（it7/it8）与 G4 plateau（it36，win_rate 0.69→0.74 横盘 6 轮）。原语义"非 HOLD 门判决即停车"（§4.3）让 loop 每次停车 → console exit-watchdog 按 §385 自动 halt 云机 + 红横幅，loop 等人手重启，每 ~6-7 轮一次停线。
- **现场误判**：kaggle 收到停机达令后日志静默（纯达令分支不打 60s 存活日志）被读成"罢工"——实际 worker 每 5s 照常轮询（无 poll failed），只是 halt 期无任务可接。
- **改动**：① `rl/loop_guards.py`：`_park_on_gate`→`_apply_verdict`，非 HOLD 判决仅落 `gate_verdict` 事件并返回 False（继续训练）；新增 `_sync_cloud_halt`——REMEDIATE/PAUSE/ABORT→hub halt，HOLD/ADVANCE→resume，实例态 `_cloud_halted` 防重发；② `remote/hub_client.py` 新增 `set_cloud_halt`（console 与 loop 共用 /admin/workers/{} 端点）；③ `remote/worker.py` 停机达令分支补 60s 存活日志。停车只剩预算到顶/F4 熔断/止损 2σ/远端不可用 ABORT 等硬边界。
- **验证**：新增 `tests/test_loop_gate_nopark.py`（REMEDIATE 不停车+halt、HOLD resume、STOP 不发、无 hub 短路）+ `test_remote_hotswap.py` 停机期存活日志 + `test_remote_ppo.py` set_cloud_halt 链路；gate_check/degrade/run_rl -k gate 全绿，mypy 干净。
- **遗留**：loop 直连 hub 的停机不走 console-state（`cloudHalt`），控制台红横幅不会显示门引发的云机停机；如需横幅需 console 侧从 hub /admin/workers/status 同步状态（待办，未做）。

---

---

## §1 训练停车机制审计 + G13 duty 门修复（2026-09-11，c6b-margin 事故：每小时自停 2 次）

用户指令："TrainingLoop 近一个小时自行关闭了几次，请检查原因，分析问题" → 审计全部 11 个停车点 → 用户拍板"处理所有问题" → §385。

### 1.1 事故根因（数据核对）

- 09:29:33 首启 → it1 冷启动 **47.2min**（占分母 62%，分子仅 68.9s）→ 10:24:25 G13 duty=0.065 停车；
- 10:26:46 重启 → it4 首轮 9.4min（PPO 往返 8min/真训练 2min 排队）→ it6 duty=0.127 又停 → it9 必停 → **死亡螺旋**：
  G13 用「首条 run_start、终身累计、跨重启」口径——冷启动与停车死时间永久锁进分母，任何评估轮必响。
- 热态单轮占空比 42-53%（it5/it7/it8）其实都过 0.35——门不是阈值错，是**口径把"起步费"当事故**。

### 1.2 修复（4 处，§385）

| 位置 | 改动 | 验证 |
|---|---|---|
| rl/gate_check.py | G13 分母基线 = **首个完成迭代结束时刻**（起步热身不计账）+ 完成迭代 ≥2 守卫 + evaluate(only_kinds) | 事故读数回放：213s/463s=0.46 → HOLD（修前 0.065 必停）；c6 慢性 0.15 仍 REMEDIATE |
| rl/loop_guards.py | `_gate` 非评估轮只查 duty（每轮现形）；`_budget_hard_cut` 轮级 max_hours 兜底 | test_train_loop_pure / test_run_rl_m1 通过 |
| rl/loop_core.py | run() 接入轮级预算硬断 | — |
| tools/training/console/exit-watchdog.ts | 账本近 300s 有 gate_verdict/circuit_break → 标「已停车(原因)」非「意外退出」 | tests/exit-watchdog.test.ts 20 通过 |

实测口径：403/404 数字逐项对上（it3 Σcloud=213s/wall=3292s→0.065；it6 575s/4538s→0.127）。

### 1.3 未决（另立决策）

- **停车不省云配额**：pull 模式下停本机 loop 只停派活，Kaggle/Colab worker 按 max_idle(≥1h) 才退出——真省配额需要"停车连带 hub 停机 + 显式释放云会话"的动作链。
- 探针腿 c6b-margin 当前以 GATE_OVERRIDE（HOLD）人工止血跑至 it20 或 4h 预算；删文件后 G13 新口径即时生效（下轮进程重启加载）。

---

---


## §25 决策正文归档（搬自 `DECISIONS.md`，2026-09-23）

> 2026-09-23 把 `DECISIONS.md` 里这些条目的**正文全文**搬到这里（索引行与编号仍留在
> `DECISIONS.md` —— 编号永不重排）。锚点 = `### §<旧编号>`。

### §2026-09-10-course-exit-gates（2026-09-10 晚 / 09-11 晨，c6 判否后自主实施 R1/R3/R7/R9）

- **背景**：c6-margin 跑满 50 轮 8550 局、eval 均值 25.4（老师 50、突破线 35）、**零学习信号**，且
  it50 因远端 PPO job 三次 1800s 超时而进程消失（docs/rl.progress.md §22）。根因与修正清单在
  `plan/feasibility-map.md` §12（D1–D9 / R1–R9）；用户拍板：**判否停腿，改设计后重开一腿**，
  优先级 R1（门槛进代码）> R3（重定价）+ R7（每轮局数）> R9（远端失败降级）；R2/R4/R5 暂缓。
- **决定（R1 门槛进代码，M0+M1）**：① `rl/config.py` 加 `GateTeacher/GateRule/GatesSpec`
  （parser 强校验 §3.4 全 8 条，未知 kind 响亮报错，`_GATE_FRAC_FIELDS [0,1]` 与 `_GATE_REL_FIELDS ≥0`
  分家——§3.2 示例的 `max_phits_rel: 1.5` 是相对倍数，本就 >1，计划原文按批注修正）；
  ② 新增 `rl/gate_check.py` 纯函数求值器（`evaluate(course, trend_rows, health, budget, now=None)`），
  9 种 kind + lattice `override > ABORT > PAUSE > STOP > REMEDIATE > ADVANCE > HOLD` + sustain 去重；
  ③ `loop_guards._gate` 作第四守卫（每 eval_every 调一次），判决写 `gate_verdict` 事件；
  `_breaker` 熔断同写 ABORT 行（否则执行面在真 ABORT 场景读不到判决）+ 补 NaN/inf 检测
  （NaN 与阈值比较恒 False，旧代码永不熔断）；④ `settle_eval_summary` 顺带落
  kills_mean/zero_kill_frac/phits_mean/pickup_mean/timeout_frac/course_fp（门控技能子指标单源）。
- **与计划的两处偏差（严格增强）**：sustain **从 trend_rows 复算**（最近 sustain 个不同 wver 的
  summary 行全达标）而非建在 gate_log 历史行上——求值器保持纯函数、崩溃重放幂等；
  ADVANCE 的「≥2 seed 集」仅当行携带 `seed_fp` 时才校验，全缺记 unknown **且放行**（否则历史
  语料让 ADVANCE 永久不可达）。
- **决定（R3/R7，载体 `curricula/c6b-margin.jsonc`）**：`wTick` 0.01→0.001（满局 −24→−2.4，不再压过
  击杀）、`terminal.stage_clear` 2.0→6.0（＝2× wKill，通关成最大单项）；`seed_rotate` 150→600
  （采样侧 48s→≈3.2min，单轮瓶颈在远端 PPO 而非采样）；`eval_every` 5→3（门要 3 轮才敢判）。
  教师块 wins=50/100 取 c6 探针值，**kills/phits 未测 = 0**——依赖它们的相对子项按 §3.4-7 跳过，
  不编造。G3/G8 首期休眠。
- **决定（R9 远端降级）**：`wait_job` 轮询指数退避（5s×2^k，封顶 60s；404 正常排队不退避）；
  新增 `--remote-degrade-after N`（默认 3）：远端连败达 N 次 → `args.ppo="local"` +
  `remote_degrade` 事件 + 本轮继续（训练活着）；`N=0`（禁降级）连败 3 次 → 写
  `gate_verdict: ABORT` 后停腿。
- **违反后果**：不落地 R1 则任何课程都只能靠事故终止（c6 已兑现一次）；不落地 R9 则云端不可达时
  整条腿耗在轮询上（c6 it50 白烧 2h）；教师基线编造会让 ADVANCE 判决失真（门形同虚设）。

---

### §2026-09-11-nntrain-g13-duty-fix（2026-09-11，用户指令"处理所有问题"）

- **背景**：G13（有效训练占空比<0.35）在 c6b-margin 首日每个评估轮必停——分母用首条
  run_start 终身累计，it1 的 47min 冷启动与停车死时间永久进分母（duty 0.06→0.13），自激死亡螺旋。
- **备选与否决**：滑动窗口（最近 N 轮/小时）—— 否，开腿头 1-2 个窗口仍背着冷启动债、还要多解
  一个窗口长度参数；阈值 0.35 改小 —— 否，改课程文件=新实验版本（§D14），且热态单轮 42-53%
  说明阈值本身没错；duty 加 sustain 连续 2 窗 —— 否，基线下修后无必要、多一层状态。
- **决定**：① G13 分母基线 = **首个完成迭代的结束时刻**（起步热身不计账）+ 完成迭代 ≥2 才判
  （防开门即响；无 duty 数据的旧调用方回退终身口径）；② 非评估轮也查 duty（only_kinds 过滤，
  `_gate` 不解坏签名）；③ G5 max_hours 加**轮级硬断**（原先只在评估轮被查，会过冲 ~eval 周期）；
  ④ exit-watchdog 识别**设计内停车**（账本近 300s 有 gate_verdict/circuit_break → 标「已停车(原因)」
  而非「意外退出」）。终身口径只留给 G5 预算门（cap 语义）。
- **违反后果**：回归终身口径则每条新腿的起步冷启动会再次让 G13 必停；漏掉轮级硬断则
  max_hours 可过冲；不识别设计内停车则每次门停车都误导操作员翻崩溃日志。
- **未决（另立决策）**：pull 模式停车只停派活、不省 Kaggle 配额（worker max-idle ≥1h），
  真省需「停车连带 hub 停机 + 释放云会话」动作链。

---

### §2026-09-11-nntrain-cloud-halt-final（2026-09-11，用户五条语义：停机命令随任务同发、云机先试停机再干活、本地全不动、红灰双横幅）

- **背景**：前两版只解决"worker 退出≠停机"，存储层面仍无法编程释放 Kaggle 会话（Colab 可 unassign）。
  用户定死停机操作语义五条：①云机取任务时任务与停机命令同发；②先尝试停机、停不掉就继续执行任务；
  ③hub 本地所有组件进程正常；④控制台红横幅显示停机问题与状态；⑤云机继续跑、停机条件消失后一切回
  正常、灰横幅显示"曾停机已恢复"。
- **备选与否决**：停机=worker 退出 —— 否（上版已否，且 quit 后恢复要人工重启会话）；停机时停发任务让云机空转 —— 否，
  空转才是最大的浪费；停机状态为一次性事件（不持久）—— 否，五条要求 halted/recovered 双态可迁移。
- **决定**：① /jobs/next 恒带 halt:bool（有任务时与任务同批下发，空闲时单独送达）；② worker 停机过渡
  尝试一次 _release_cloud_machine（Colab unassign / 其余提示人工断开），**不退出、照常执行任务**；
  halt 清除后复位可再试；③ console-state cloudHalt 双态（halted 红横幅 / recovered 灰横幅保留历史，
  TrainingLoop 重启或手动即恢复）；④ 本地组件全程不动。
- **违反后果**：回退到"停机=杀 worker"则恢复续跑要人工重启会话、停机期间真正闲置空烧；不清 halted/recovered
  则操作员看不到"停机中/已恢复"的迁移，云机配额的处置失去可见性。## §2026-09-11-gates-never-park-loop（2026-09-11，用户定案：TrainingLoop 永不因门停车，该停的是远端云机）

- **背景**：c4-dodge 连续两起"门停车"事故（it7/it8 G13 占空比 REMEDIATE、it29→it36 G4 plateau REMEDIATE），
  每次停车 → console 按 §385 自动向云机下发停机 + 红横幅，loop 停着等人手重启。用户质疑两点：
  kaggle 关不了机却"看着罢工"（实为停机达令分支吞了存活日志）；trainingloop 为何"还是停了"。
- **备选与否决**：维持 §4.3"非 HOLD 即停车" —— 否（用户原话：trainingloop 永远不要停！该停的是远端云机）；
  只对 G4 REMEDIATE 放行 —— 否（G13/G7/G9 同样会再犯，掩耳盗铃）。
- **决定**：① 门判决**永不因门停车**：`_park_on_gate` 改为 `_apply_verdict`——任何非 HOLD 判决只落
  `gate_verdict` 事件（复盘记录 + exit-watchdog「已停车」分类源）后**返回 False 继续训练**；
  ② 停机/恢复动作全部映射到远端云机：REMEDIATE/PAUSE/ABORT → `set_cloud_halt(True)`
  （走 hub /admin/workers/halt，console 同端点复用，`hub_client.set_cloud_halt` 新函数）；
  HOLD/ADVANCE → resume；实例态 `_cloud_halted` 防重复下发，重启即复位；local/push 无 hub 短路零行为；
  ③ 真正的停车只剩硬边界：预算到顶（_budget_hard_cut）、F4 熔断、止损 2σ、远端不可用 ABORT（leg 级）；
  ④ worker 停机达令分支补 60s 存活日志（"cloud halted, polling hub …"），停机期不再被误读为罢工；
  ⑤ 预算 STOP 不是停机达令，不触发云机停机。
- **违反后果**：回退到"门即停车"→ 每 ~6-7 轮一次停线 + 云机连带停机 + 人手重启（本次现场连锁）；
  停机期 worker 日志静默会继续被误判成罢工。

---

### §2026-09-12-multi-course-key-is-stem（多课程命名空间键 = 课程文件 stem，不是内部 name）

- **背景**：P1 活体验收前发现 `s-dodge.jsonc` 内部 `name` 是 `s-dodge-mix`，而控制台课程选择 /
  `tmp/<course>/` / `out` 路径 / TS launcher `peekCourse` 全用文件名 stem（`s-dodge`）。
  `run_rl.py` 锁曾用 `args.course_name`（内部名）→ `.run_rl.s-dodge-mix.lock`，与 TS 预检查的
  `.run_rl.s-dodge.lock` 对不上：preflight 永远查不到在跑进程，同课双开只能靠 python 侧兜底。
- **备选与否决**：锁用内部名、TS 侧改读内部名 —— 否（内部名要解析课程文件才知道，launcher
  preflight/kill 匹配全要变重；且 `tmp/`、`out`、`courses` 块全是 stem，改一边不如统一到多数方）。
- **决定**：命名空间键 = 课程文件 stem。`train/loop_util.py::course_key_from_path()` 为唯一推导点；
  `run_rl.py` 锁改调它；`train_loop.py --course` 文档写明传短名（与 `--course s-dodge` 同拼写）；
  内部 `name` 退为 S9 归属标注（events/gate 内用，不参与调度）。回归测试
  `test_course_key_is_file_stem_not_inner_name` 锁死 stem 规则。
- **违反后果**：回到内部名 → 双课 preflight/kill/账本课程键三方错位（静默错位，最难查的一类）。

---

### §2026-09-13-multi-course-course-keyed-singletons（多课程并行：课程 = 并行单元，一切单例按课实例化）

- **背景**：GPU 资源增加要求 N 课同跑（常态 2 课、4 槽位留余量）。原链路住着一组同构
  单例假设——全局实例锁（`.run_rl.lock`）、账本 5 个扁平单键、`rl.hub_port` 一处 base、
  隧道/`remote_hub_url` 单键、监督器猜课程、halt 单键、`--kill-previous` 按脚本名全杀
  （差距全表：`docs/multi-course-audit.md`；约束清单 S1–S17：plan §2）。
- **备选与否决**：发现服务/调度器进程（否——Simple beats clever：算术槽位
  `hub_base + slot*10` + 每事实一归宿就够）；两课共用一 hub（否——jobRoot/jsonl 串味，
  hub per-course 后调用方传参即隔离）；配额写进 `curricula/*.jsonc`（否——一改课程文件
  `course_fp` 就变，触发 D14 熔断误判：机器配额只住 `rl-config.json` 的 `courses` 块）；
  监督重建时用全局状态猜课程（否——fail-closed，查不到 = 放弃重建 + 响亮告警，绝不猜）。
- **决定**：①锁/账本条目/端口/隧道/trainer/日志/监督/halt 全部 course-keyed，命名空间键
  = 课程文件 stem（§2026-09-12-multi-course-key-is-stem）；②端口算术唯一归宿
  `tools/training/slots.ts`（`portForSlot`/`allocateSlot` busy 锁内分配 + `checkCapacity`
  加法校验 `Σ max(workers_c, local_slots_c) ≤ max(rl.workers, rl.local_slots)`，超量
  fail-fast 点名课程，拆分值允许 0 = 关闭该课本机直跑）；③账本 = `Record<course, Entry>`
  四新键，重建契约完备字段（Q4）+ 旧扁平键在 P5 由 `loadRegistry` 一次性搬迁后删读写
  （R2）；④本机并发配额热读 `courses.<课>` 优先 + 覆盖生效打响亮行
  `[quota] workers X -> Y (multi-course split)`；⑤控制台按课启/停/冒烟/日志/halt
  （`activeCourse`/`cloudHalts`/busy 键按课），LAN 只读不变，`stopAll` 保留全局总闸语义；
  ⑥remote 侧每课一隧道 + worker 有界 FIFO + 独占租约（§2026-09-12-multi-course-p3b-
  supersedes-343）；⑦归属字段（manifest `course_name`/dispatch course）只做对账审计，
  不参与调度，`course_fp` 血缘不动。
- **违反后果**：新单例回落全局键/端口 → B 课覆盖 A 课登记（杀错/停错/连错 hub）；
  配额进课程文件 → D14 熔断误判；重建时拿全局状态猜课程 → A 课进程被 B 课配置拉起；
  删 per-course 锁文件 → 2026-09-06 双 trainer 写同一 traj 事故重演。

---

### §2026-09-13-level-extraction（2026-09-13，关卡配置抽离 + D14 语料身份改语义哈希；用户拍板）

- **触发**：mid-run 编辑课程文件（iters 30→60）把 c6-chip 打成指纹脑裂——shard manifest 的
  course_fp 与 job envelope 的 course_fp 都在各自时刻重读文件字节（`cmd.course_fp_for_args` /
  `loop_steps` 发布），编辑落在采样与发布之间 ⇒ D14 整轮 shard 拒收（job=abbf… shard=e6c6…）。
  原则确认：**iters 这类预算/测量旋钮不改变训练契约，mid-run 编辑不应要求新课程**。
- **配置修改分类学**（此后所有课程/关卡变更按此归类）：
  - **A 语料身份**（改动 = 语料破裂，禁止对在跑腿修改；要变 = 新关卡/新课程）：地图/敌人
    队列/敌数/出生点/**命/星**（→ 抽离到关卡文件）、difficulty/max_ticks、mode/dodge、
    seed_rotate/seeds、reward（formula/params/terminal/scheme——PPO 端按 manifest 公式重算
    回报，混入不同 reward 语义的 shard = 错账）。
  - **B 训练语义、非语料**（改动 = §15.5 新实验，stop→start 生效，不破坏 shard 血缘）：
    bc、γ/λ/clip/vf/ent/max_grad_norm、epochs/mb/lr、ppo_schedule、normalize_ret、kickstart。
  - **C 预算/测量/路径**（随时可改，stop→start 热应用）：iters/max_hours/workers/stream/
    keep_iters、eval_stages/eval_games_per_stage/eval_every、out/traj/backup_*、ent_break*。
- **第一步（本条）——关卡抽离**：`nn-training/levels/*.jsonc` 持有环境语义
  （stages/difficulty/max_ticks/player），课程以 `"level": "<name>"` 引用；课程侧内联重复
  声明四类环境键 = 配置冲突 raise（`rl/config.load_course` 合并）。内联 stages 旧用法逐字节
  兼容（在跑课程不迁移照常跑）。首两份：arena6（c6 家族）/ arena4（c4 家族）。
- **D14 升级为双指纹**：新增 **corpus_fp** = `config.corpus_identity_fp`（env+reward **解析值**
  规范化 JSON 的 sha256；内联与 level 引用同形同指纹，文件内注释/格式不敏感），经 rollout cmd
  `--corpus-fp` 进 shard manifest、publish_job 进 job manifest；worker 装载校验
  `d14_corpus_match` **优先比 corpus_fp**，任一侧缺席回退 legacy course_fp（旧 payload 兼容）。
  course_fp（文件字节哈希）保留作血缘/快照（D13）与 resume 对账不变。
- **复用**：关卡文件与 evalB/探针同形 schema——`eval-course-ckpt.ts --course
  nn-training/levels/arena6.jsonc` 直接可跑（不再需要 tmp/fmap-*.jsonc 复制品）。
- **迁移状态**：c6-dmgfix 已引用 arena6；c6-chip 在跑不迁移（其 legacy course_fp 校验在
  stop→start 后自然恢复一致）；其余历史课程按需渐进迁移。

---

### §2026-09-13-hot-reload（2026-09-13，课程热加载：非语料改动下一 iter 应用；语料改动拒绝+横幅+不泄漏云端；用户拍板）

- **机制**：trainer 每 iter（rollout 前，`loop_core.run` → `_hot_reload_course`）重读课程文件，
  以 `corpus_identity_fp` 分流（§2026-09-13-level-extraction 分类学的机制化）：
  - **未变（B/C 类）**：`rl/hot_reload.apply_hot_fields` 把白名单字段写回 args——消费点每轮
    活读（iters `loop_core:207` / gamma,lam / lr,epochs,mb / eval_* / ent_break*，
    `loop_steps._course_iter`），**下一 iter 即生效**；结构绑定字段（bc/workers/stream/
    out/traj/backup_*/freeze*/clip/vf/ent_coef/normalize_ret/warmup_iters/kickstart_ref）
    记 restart-only（`*` 后缀账），响亮日志「停止→启动后生效」，不静默吞。max_hours 热应用
    时重算 `self._deadline`。
  - **变了（A 类破坏性）**：拒绝热应用，写 `course_edit` 事件（verdict=rejected，本地账本）+
    响亮日志；控制台读账本渲染错误横幅（`courseEditFromLedgerTail`，取尾部窗口内最近一条，
    跨后续 iteration 事件持久；改回文件后 trainer 写 restored → 横幅自然消失）。**沿用启动
    配置继续训练**。
- **不泄漏云端**：课程文件字节在启动期冻结（`args.course_frozen_bytes`）——D13 全文快照、
  course_fp、shard `--course-fp` 一律用冻结字节（`loop_steps` 发布 / `cmd.course_fp_for_args` /
  `loop_core._course_file_fp` 三处统一）。mid-run 的任何编辑（含被拒的语料身份改动）永不进
  job payload / 代码包 / 远端日志。
- **§15.5 修订**：B 类字段（γ/λ/lr/epochs/mb/ppo_schedule）经热加载 mid-run 修改自此**允许**，
  记账 = `course_edit` 事件（applied，含字段清单）自动进账本；corpus/reward 语义仍禁止 mid-run
  （拒绝分支）。`plan_reload` 对「iters+wChip 同改」整单拒绝，不做部分应用（防半新半旧配置）。
- **文件半行写/瞬时坏档**：沿用旧配置静默重试（一次日志），不打横幅。
- **夹具迁移**：`training-multi-course.test.ts` F-B6 的 bc 夹具 s-dodge→c6-dmgfix（原
  tmp/s2-cap/weights.json 已被 tmp 清理移除，属环境性失败；新夹具指向 nn-training/weights/
  稳定备份）。

---

### §2026-09-13-goalnn-max-ticks-rule（2026-09-13，D7 终局标准：max_ticks 取值规则一次性立案）

- **背景**：D7 把 max_ticks 划入任务侧不可动项，规则须在工厂定型时一次立案、不再逐调。原拟式
  `ceil(2400×count/4)`（= `600×count`）在低 count 端塌缩：c01=600 的 200 局 God 实测 61 局超时、
  **超时局 0 击杀**（可赢的局被截断），pass 69.0% < D4 门 80% ⇒ 该级门构造性不可达；同一批种子
  放宽到 1200 后 pass 99.0%、超时 0。同类事故第三次（§338 max_ticks 2400→3600；
  §2026-09-10-goalnn-rleval「2400 截断压胜率」）。
- **备选与否决**：A 保留原式只抬 floor `max(2400, 600×count)` —— 否，c04=2400 实测仍有 1/200 超时
  （胜局右尾被删失）且低 count 端白送 2× 上限；B 按观测拟合 `~840+285×count` —— 否，等于把上限
  钉在教师长尾上，违 §0.2（教师不是标尺），且脱离 c20=12000 的预算包络；C 原式斜率 + 实测固定项
  —— 取此。
- **决定**：**`max_ticks(count) = 600×count + 900`**（c01=1500 … c20=12900）。斜率 600 沿用
  roadmap 原式（c05-c07 实测该斜率恰好解除 2400 的截断）；固定项 900 = 接敌/穿场/生成节奏的
  一次性开销。判据 = 上限 ≥ 1.25× 实测胜局最大 tick。实测（200 局 God/级，EVAL_SEEDS
  860001-860200）max 胜局 tick / 新上限余量：c01 1121/+34% · c02 1640/+28% · c03 1775/+52% ·
  c04 2510/+31% · c05 2720/+43% · c06 2776/+62% · c07 3381/+51% · c10 3677/+88% · c14 4823/+93%，
  逐点零超时；**上界不缩**（12900 ≥ 原式 12000）。单一实现 = `rl/ladder_factory.max_ticks_for()`，
  由 `nn-training/tests/test_ladder_factory.py` 钉死。
- **违反后果**：低 count 端截断会把可赢局记成超时 ⇒ 门（pooled 400 ≥80%）构造性不可达，或超时率被
  误读成能力信号去调训练侧；终局标准反复改则 §6「现线证据沿用」与跨级/跨版本可比性失效。

---

### §2026-09-15-goalnn-dynamic-rollout-volume（2026-09-15，采集配额量纲从「局」切「transitions」；plan/dynamic-rollout-volume.plan.md §2）

- **背景**：PPO 吃的是 transitions 而不是局数（x3 240 局 × 967t ≈ 23 万/轮；c4-dodge 600 局 × 1100+t
  ≈ 66 万+/轮），而课程至今用 `seed_rotate`（每关局数）配采集量——固定局数下 transitions/轮随局长漂移
  （x3 局均 700–1150t），PPO 更新量与 advantage 归一稳定性跟着漂。反例在先：x2-acbc 3 倍密度 30 轮
  pooled −3/400 ⇒ 加量解决的是方差、不是信号；**本决策只承诺「量准」，不承诺涨点**（DoD 里不许写胜率条款）。
- **决定**：课程新增三键（全可选；**缺席 = 老行为逐字节不变**）：`target_transitions`（/轮，口径 = 已结算
  shard 的 `nSamples` 之和）、`est_ticks_per_game`（首轮反解局数用，之后由 jsonl trailing 均值覆盖）、
  `max_games_per_stage`（0 = 默认「初波 × 4」）。语义：分关达标线 `ceil(target/关数)`；初波
  `G0 = max(1, ceil(关达标线/est))`；结算后**逐关独立**补波 `ceil(缺口/est)`，每关至多 3 波；掉局零样本不计
  （天然触发补采——特性）、超时局计入；触硬顶 = 停采 + 响亮日志 + iteration 事件 `transitions_capped`。
  纯逻辑住 `rl/volume_waves.py`，账本口径 `rl/resume.settled_stage_totals`，接线 `rl/loop_core.py::_volume_topup`
  （S4 第十八刀后接线整簇搬到 `rl/loop_volume.py::TrainingVolume`；`_volume_topup` 的 wave 规则自
  VOLUME_RULE_V2 起退役，生产路径走 `_volume_collect_continuous`）；
  iteration 事件追加 `transitions_target` / `transitions_collected`（additive，旧行无此键）。
- **两条派生偏离计划的地方（本决策的实质）**：
  1. **种子流按 (stage, wave) 独立**，不是「初波沿用今日单条顺序流」。计划 §2.2.1 说「种子流与今日
     `build_pairs` 同键（rotateSeed, it）」——取**同键族**（同 rotate_seed/it、初波同 tag 0x5EED）
     但按 stage 拆独立流。理由：单流下「每关抽几签」会随 est/配额变化而移动**后续关**的流位置，
     §2.2.5 的跨关独立性（给 A 关加波不改 B 关任何一局种子）直接失守。带 key 的课程本就是新实验
     （§15.5 要求 fresh `--out/--traj`），不需要与老流逐字节同构。
  2. **身份键条件进 payload**：仅当 `target_transitions > 0` 才把 `volume_rule`(=VOLUME_RULE_V1) +
     `target_transitions` 加进 `corpus_identity_fp`。无条件加会让**所有**既有课程指纹一起漂移
     （D14 血缘断裂、在跑的腿把已落盘 shard 判成异身份、云端 job 全拒），与「缺席 = 老行为逐字节不变」
     直接矛盾。`est` / `max_games_per_stage` 刻意**不进**（同 iters/max_hours 分类学：估计与硬顶不是语料身份）。
- **被否决的备选**：① 走**折中版**（轮首按 trailing 均值反解局数，x3-power「批量附录」）——保留为回落线，
  但仍是「一轮一锤子」，est 偏了整轮就偏；② 全局池配额（不分关）—— 否，短局关淹长局关
  （x3 acd 733t vs abd 989t 差 35% 就是前车）；③ 让补波也走 stream/双缓冲路径 —— v1 不做（动那套墙钟优化
  收益为负、风险为正），stream 课程本轮只按初波结算并**写日志说明**；④ 补波决策靠 WAL 重放 —— 否，
**首版确实这么想过，被 P2 的 e2e 证伪**：波次序号 `wave_idx` 是决策而不是账本的函数
  （它同时是种子流的键），只按账本重算会在重启后把计数器拉回 1、用 wave-1 的种子补 wave-3 的缺口。
  现设计：纯函数负责「给定 (账本, wave_idx) 算同一波」，`wave_idx` 本身从 commit journal 回读
  （预算续算 + 停在波中的那一波原样重放）。
- **违反后果**：把 `target_transitions` 无条件塞进 `corpus_identity_fp` ⇒ 全库血缘断裂、云端 job 全拒；
  把 volume 初波改回单条顺序流 ⇒ 跨关种子耦合，补波会静默改掉别的关的对局；补波不看硬顶就无限补 ⇒
  短局关吃光墙钟（`max_hours` 是最后一道闸）；给 volume 课程硬配 curriculum/rotate 门控窗口 ⇒ 配额分母
  与真实采样关不符（代码响亮 SystemExit，**勿放宽成静默取一个关集**）。
- **验证**：单测 46 用例（配额数学 / 跨关独立性 / 终止优先级 / 硬顶截断 / WAL 解析 / 冻结 `build_pairs` 摘要 /
  桩 self 调 **真方法** 的接线）；P2 e2e 10 用例（`e2e/test_volume_e2e.py`：真调度器落地真 shard——定额收敛 /
  跨重启重放 / 长短局独立 / 掉局补采 / 硬顶打标 / **尾部竞速 4 例**：副本不污染账本、double-settle 不重复计数、
  丢局被补回、预算耗尽要响亮）；nn python gate + 根 `bun run check` 绿。**遗留（调度器侧，未修）**：同节点 fan-out
  副本共享 `out_dir`，double-settle 的 rmtree 连赢家数据一起删（报告仍记 `ok=N/N`）——单独立项修，取证见 §44。
- **未做（勿当已验）**：微课 5 轮试点与补波 overhead 实测（需真实 bun sim / 训练，未跑）；
  dashboard 展示 `transitions_collected/target` 未动。
- **修订（2026-09-15 T9，plan/x3-power-followup §T9 的「二选一并写明」已定）**：第二键
  **改名 `est_ticks_per_game` → `est_samples_per_game`**，值 = 局均 ticks × (samples/ticks)
  = 局均 ticks / K（x3 实测 0.1007 ⇒ 980 ticks ≈ 98 samples）。旧名把 ticks 填进 samples 分母
  ⇒ 初波只反解出目标的 ~⅒，补波按同一错估缩放、补满 3 波仍不达标（评审「兑现 37% 触顶」用例）。
  **不取另一选路「公式显式 /K」**：K 是 exporter 侧实现细节，课程文件不该编码它（K 一变
  所有课程文件都要改）；键名自带单位 + `extra="forbid"` 使旧键照写即启动期响亮报错，静默复辟不可能。
  同批修三处 ticks-as-transitions 文档，并让 `trailing_samples_per_game` 只读 jsonl 的 `samples`
  （不读 `ticks`）——两半缺一即「首轮对、第二轮起 10×」；单测「① T9 量纲钉死（600000/4/980）」
  把新（一波达标）/旧（补满 3 波 < 1/3）两读数一起钉死。

---

### §2026-09-18-goalnn-r2-loop-task-queue（2026-09-18，用户指令：训练循环任务队列化 —— trainingLoop 由一个进程服务所有并行课程）

- **背景**：`trainingLoop` 一直不是「请求处理器」，而是**有状态会话**：`TrainingLoop.__init__`（`nn-training/rl/loop_core.py:151`）声明约 60 个跨轮字段（进度指针 / 门禁计数 / 在飞线程与子进程 / torch 模型与优化器 / 轮内瞬态），`run()` 是 190 行顺序脚本——一步阻塞整条腿阻塞（等远程 PPO、等 rollout 子进程、等 eval 尾巴）。所以「多课程 = 多进程」不是设计选择而是形状的必然结果，与用户口径「hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程」冲突。用户进一步给定形态：**任务队列，任务自带一切**（与 hub 的 job 同构）。
- **决定（设计稿 `plan/r2-loop-task-queue.md`）**：
  - **状态五分类**（逐字段给出归宿）：**A 指针**（`next_it`/`rotate_seed`/阶梯＝`it` 的纯函数）· **B 门禁指标**（连击/累计量/止损/提示类计数）· **C 在飞集**（`job_id`/等谁回传/eval 尾巴/预采子进程——线程句柄不可序列化 ⇒ 落盘的是**意图**，重启按意图重建）· **D torch 对象**（每课内存缓存最新 checkpoint：权重 + Adam）· **E 轮内瞬态**（`_report`/`_agg`/`_volume_*` 等，**禁持久化**——持久化它 = 拿旧数字记新轮）。
  - **任务模型**：一轮拆成 13 个细粒度任务（`prepare_iter` → `rollout_dispatch/wait` → `ppo_publish/wait` → `export_weights` → `eval_dispatch/join` → `gate_eval` → `record_iteration` → `cleanup`）；执行器返回四态 **DONE / WAIT / RETRY / ABORT**。`WAIT`（等远程结果、等 eval 尾巴）**不占执行权**是单进程多课程的关键；持资源的步才是串行的（用户口径：用任务队列防资源竞争）。
  - **每条任务必须先有盘上判据**（幂等 guard：shard 齐 / 权重落位 / 账本已有 `iteration` 行）——这条同时是「重放安全」与「扫账本」的同一件事。
  - 每课一份 `train-loop` 状态文件（`loop-state.json`，v1）：**账本永远是 SSOT**，该文件只是加速器，版本不认/字段缺失即从账本重建。
  - **用户定案四问（2026-09-18）**：① 直接做到 **R2c 单进程**（R2a/R2b 为途中产物）；② 任务粒度 = **细粒度步骤 + WAIT 让位**；③ 本机重资源（`local_ppo`/`eval_local`）**跨课排队、池容量 1**（rollout 子进程池不受影响）；④ checkpoint 缓存上限 = **并行课程上限（默认 5，`rl.checkpointCacheCourses` 可配）**，RSS 实测表是 R2c 上线前置。
  - **门禁扫账本（用户裁决）**：门禁语义逐条从「内存计数」改成「扫账本」；指标**按课程缓存**，只在开课/续跑读一遍，之后由写事件处**增量**维护。**不新造账本**：`training_log.jsonl` 已含 `run_start`/`iteration`/`gate_verdict`/`iter_error`/`circuit_break`/`run_complete`，且已有五个扫描器（`rl/resume.py`、`rl/gate_check.py`）——R2a 只是把它们收敛成**一份视图**。
- **R2a 已落地（2026-09-18）**：`nn-training/rl/train_ledger.py`（`LedgerSpec` + `LedgerView` + `load_ledger` 单遍扫描 + `apply_event` 增量）；`rl/loop_core.py::_setup_common` 改由视图继承 `next_it`/`rotate_seed`/`ent_peak`/`train_sec_total`/`train_samples_total`/`kl_streak`/`ent_streak`/`stop_loss_streak`/`soft_remediate_count`；`rl/events.py` 新增 `stop_loss` 事件（止损连击的**状态转移**落账）且 `write_*` 返回事件 dict 供增量视图消费；`rl/loop_guards.py` 加 `_ledger_apply` 钩子（写账本处顺手并入视图，观测失败绝不阻断训练）。
- **本相位**刻意**的行为变化**（就是修复内容，必须知道）：门禁计数**不再随进程重启清零**——① I2「提示类门 REMEDIATE ×N 即停腿」的计数读**整条账本**（换新 traj = 新纪元，重新计数）；② F4 `kl_streak`/`ent_streak` 与 `ent_peak` 同源继承（连击是**连续**计数，只有下一轮再越线才续，继承既真又无害）；③ 止损连击靠新事件跨重启成立。**刻意不继承**：`_consec_fail`（重试连击是单腿内的进程护栏，继承会「重启即秒死」）、`_zero_shard_streak`（口径还依赖 `_node_rollout`，而 `iteration` 行今天没有 `rollout_src` ⇒ 从账本重算会对节点轮报假事故——R2b 给事件加该字段后再接）。
- **备选与否决**：另造一份「训练状态账本」JSON 作 SSOT——否（两份真相必然分叉，且旧扫描器/读盘面/控制台全部已在读 `training_log.jsonl`）；把 13 步合并成「一轮一个任务」——否（用户定案：轮粒度下 executor 串行，一门课的远程等待会挡住其它课，单进程只省了进程数、没换来并行）；重启时把 torch 对象序列化恢复——否（Adam 动量序列化成本高且不必要：落盘面已有权重，缓存是**加速器**不是真相）；用 `Math.random`-式时间戳推断在飞任务 —— 否（幂等判据必须来自账本/盘面，不能来自时间猜测）。
- **违反后果**：任何新增的跨轮门禁计数若写在内存里，就会重演本轮修掉的 bug（重启即洗白，`c6-pickup3` 6 次 / `c6-bonus` 10 次 REMEDIATE 那类判据被无限延长）；任何绕过 `_ledger_apply` 的账本写入会让视图与盘面分叉（R2b/R2c 的任务幂等判据随之失效）；把 E 类轮内瞬态写进 `loop-state.json` 会让重启后的记账与真实轮次错位。
- **落地**：`nn-training/rl/train_ledger.py`（新）、`rl/events.py`（`write_*` 返回事件 + `write_stop_loss`）、`rl/loop_core.py`（`_setup_common` 继承）、`rl/loop_guards.py`（`_ledger_apply` + 止损落账）、`rl/loop_steps.py`（`_record_iteration` 增量并入 + 类型声明）；回归 `nn-training/tests/test_train_ledger.py`（15 例：与五个旧扫描器**奇偶**、增量==单遍、独立复算连击、坏行/未知事件透明）、`tests/test_train_ledger_wiring.py`（3 例：`_setup_common` 继承 + 继承计数当轮停腿 + 空账本从零）。设计稿 `plan/r2-loop-task-queue.md`；进度 `docs/nn/training-stack.md` §10。
**R2b 落地（2026-09-18 续）——任务模型 + 在飞集，并把 `loop-state.json` 否决掉**：

- **`rl/loop_tasks.py`**（新，纯逻辑）：`Task`（`task_id = course:it:kind` = 幂等键；重试只动 `attempt`）·
  `TaskResult` 四态（`DONE`/`WAIT`/`RETRY`/`ABORT` + `is_terminal`；非法状态当场 `ValueError`）·
  `ROUND_TASKS` 13 步任务表 + `RESOURCE_OF`（只有 `rollout`/`volume_topup`/`ppo`/`eval_join` 占资源池，
  等待型/记账型不占——这正是单进程能服务多课程的机制）· `RoundFacts` + `already_done` + `pending_tasks`
  （幂等判据只认**盘上事实**）· `resolve_failure`（与现主循环逐条一致：冒烟作废原地重试 / 死腿立刻 ABORT /
  attempt≥5 才停）。
- **在飞集复用既有 WAL**（`rl/commit_journal.py`）：新增 `attach(phase, round, **extra)`（**不改状态机**，
  只补 `job_id`/`dispatch`/`ts`）与 `inflight()`（pending + 这些事实）。`_remote_ppo` 在 `publish_job`
  拿到 `jid` 后立刻 attach；`_commit_journal()` 在每 it 首次创建时把在飞集打进日志（`jid=` / `via push|pull`）
  ——「上一轮在等哪个 job、推给了谁」从事故考古变成一条日志。
- **★ `loop-state.json` 否决**：初稿计划每课一份状态文件（指针 + 在飞集 + 预算）。落地时发现盘上
  **已有三份权威来源**覆盖全部四类信息——账本（指针/预算/门禁计数，R2a 已接）、`commit_journal`（在飞集，R2b 已接）、
  `eval_log.jsonl` + `(stage,seed,wver)` shard 对账（eval 尾巴意图、预采子进程）。再写一份 JSON 就是**第二份真相**，
  且分叉方向恰是最贵的一种（续跑读错指针）。⇒ **不建该文件**；唯一允许留在内存的是调度器的唤醒条件
  （`WAIT.resume_at`、资源池票），它可重算。（备选否决：写它当加速器——内容为零独立信息并集，只带来分叉风险；
  把在飞集写进账本——账本是**事件流**，在飞是**状态**，混进去污染所有账本读者。）
- **落地**：`nn-training/rl/loop_tasks.py`（新）、`rl/commit_journal.py`（attach/inflight）、`rl/loop_steps.py`
  （`_remote_ppo` attach + `_commit_journal` 在飞集日志）；回归 `tests/test_loop_tasks.py`（10 例）、
  `tests/test_commit_journal.py`（+4 例，含硬死注入带 `job_id`）。
**R2c-1 落地（2026-09-18 再续）——单进程调度核心 + 只读计划视图**：

- **`rl/loop_scheduler.py`**（新，纯调度：无 torch/网络/IO）：`PoolSet`（资源票 + 记账，
  未知池名/超发释放都 `PoolError`）· `CourseQueue` · `Supervisor`（公平轮转 → 闸门 → 单线程
  执行 → 四态收敛 → 按课隔离故障）。三条不可交易性质：**同一时刻只跑一个任务**、
  **`WAIT` 不占执行权**、**故障域按课**。
- **★ 票可跨 `WAIT` 保留**（`TaskResult.hold` / `waiting(hold=True)`）：写池闸门测试时发现的
  设计缺口——若 `WAIT` 一律还票，单线程调度器里容量 1 的池**永远不会挡住任何人**（任务体
  跑完即还票），池就失去意义；真实形态是「后台仍在干活」（本机 eval 的局还在子进程里跑）
  ⇒ 票必须能跨步持有，否则另一门课的本机重资源会插进来把机器压爆。
- **「能跑的都被池挡住」= 没事可做**（`step()` 返回 None，`blocked_courses` 保留事实）——
  否则调度器会在两门互相挡住的课之间空转。
- **`rl/loop_plan.py`**（新，唯一碰盘之处）：账本指针 → `RoundFacts` → `pending_tasks`；
  shard 结算数；`commit_journal` 在飞集；课程发现（`<traj-root>/*/training_log.jsonl`）。
  判据永远朝「不跳」保守：算不出的留 `False`/`0`。
- **`nn-training/run_rl_cluster.py`**（新入口）：**一个进程**读出所有并行课程的「下一步 /
  待办 / 在等谁（含 job_id）/ 被什么挡住 / 关键事实」。当前只提供只读计划视图（不训练、
  不发布、不等待）——它零训练行为变化且立刻可用，同时把调度核心放在真数据上跑通。
- **R2c 拆相**：R2c-1（本段，机制 + 只读面）已完成；**R2c-2** = 抽 `run_one_round` 并把任务体接到
  真 `TrainingLoop` + 三处长等待改 `WAIT` 让位 + 进控制台 + `checkpointCacheMb` 真机 RSS 实测。
- **落地**：`nn-training/rl/{loop_scheduler,loop_plan}.py`、`nn-training/run_rl_cluster.py`（均新）；
  回归 `tests/test_loop_scheduler.py`（18 例）；实测：`run_rl_cluster.py --traj-root tmp` 在真课程
  目录上读出 5 门课的计划（含「采集完成但账本未结算」那一态）。
**R2c-2 落地（2026-09-18 三续）——轮体抽出 + 任务体↔引擎的桥 + 假件集成测试**：

- **`TrainingLoop.run_one_round(it) -> RoundOutcome`**：`run()` 的 190 行轮体搬进新方法，`run()` 退为
  驱动器（预采 join / 预算到点检查 / 按 outcome 施加 / 收官 drain / 停车）。控制流**只搬不改**：
  `break`→`ROUND_STOP`、包导出 `return`→`ROUND_BUNDLE_EXIT`、冒烟作废→`ROUND_SMOKE_STOP`、
  三种 `it -= 1`→`ROUND_RETRY`、轮末→`ROUND_NEXT`。**`RoundOutcome.it` 必须带回**：半离线整段
  `_remote_run_segment` 一次吃掉 it..end_it，丢掉返回值就会重跑已跑完的段。
- **`rl/loop_runner.py`**（新，任务体↔引擎的**唯一**桥）：`planner` 读账本给指针（SSOT）、
  `run_round` 把 `RoundOutcome` 映成四态；**`WAIT` 只认引擎显式提供的 `remote_job_ready(it)` 钩子**
  ——钩子不存在（今天的引擎）⇒ 跑完即 DONE = 行为与改造前逐字节一致；钩子存在（R2c-3 的轮询化，
  或测试里的假件）⇒ 未就绪就 `WAIT` 让位。**不猜、不睡、不自己轮询**。
- **粒度诚实记账**：今天一个任务 = **一轮**；轮内 13 步需要的轮内局部量
  （`pairs`/`dist_cfg`/`t_rollout`/`seg`）还锁在 `run_one_round` 里，提成 `RoundContext` 后即可细化
  （R2c-3）。粒度只决定让位点密度，不改调度器/桥的契约。
- **集成测试（用户口径：不跑真 rollout/PPO/eval）**：`nn-training/e2e/test_loop_supervisor_integration.py`
  （4 例）——真 `TrainingLoop` 控制流 + 真账本写入 + 真 `Supervisor`/`LoopRunner`，只把
  `_rollout_phase`/`_serial_ppo`/`_join_eval`/预采/巡检/轮转/`rl-config` 读盘换成假件；钉住
  ①一个进程服务多课互不串账 ②`WAIT` 让位（另一门课先跑完且顺序可断言）③进程重开按账本续跑且
  不重写行 ④引擎异常 = 原地重试。
- **落地**：`nn-training/rl/loop_core.py`（抽方法 + 常量/`RoundOutcome`）、`rl/loop_runner.py`（新）、
  `e2e/test_loop_supervisor_integration.py`（新）；拼接用一次性脚本（逐 hunk `assert` + `ast.parse`，
  跑完即删）。
- **仍未做**：R2c-3（轮内细粒度 `RoundContext` + 三处长等待真轮询化 + 控制台视图 + 真机 RSS 表）·
  R2d · R2e。
**R2c-3 余下（2026-09-18 五续）——远端 PPO 三相拆分：让位点从「每轮」下沉到「这一步」**：

- **问题**：拆相前 `_remote_ppo` 是一个 400 行阻塞函数（打包 → 发布 → 阻塞轮询 → 三重校验落位）。
  它前面**不能**挂让位闸门：闸门跑在步骤之前 = 还没发布就被挡住 ⇒ 永远等不到回传。于是单进程
  多课程下，「等云机」这一段仍然会堵住整条调度链（25 分钟量级）。
- **拆法：同一任务内的三个相位，不是三个任务**（`rl/loop_steps.py`）：
  `_remote_ppo_publish`（打包 + 发布 + 直推提交 → 会话）/ `_remote_ppo_probe`（**非阻塞**问一句）/
  `_remote_ppo_fetch`（阻塞等：组合路径 / 节点轮 / 整段）/ `_remote_ppo_land`（校验落位 + 记账 +
  结算字段）。`_remote_ppo` 保留为三者的**组合入口**，四个既有调用点（本机轮 / 节点轮 / 半离线整段 /
  全离线导出）行为不变。
  **为什么不拆成 `ppo_publish` + `ppo_wait` 两个任务**：三相共享一份会话（jid / 超时预算 / 打包墙钟 /
  运输方式），拆任务就得把它序列化到盘上才能跨任务传 —— 那正是 R2b 否决过的「第二份真相」
  （§2026-09-18-goalnn-r2-loop-task-queue 的 `loop-state.json` 段）。任务粒度只决定让位点密度。
- **会话是纯数据、住在轮内上下文里**（`rl/loop_round.RemotePpoJob`，`RoundContext.remote`）：
  不进引擎实例属性（§2.2 无隐藏状态；单进程多课程会互相覆盖），也不落盘（同上）。刻意**不持有**
  payload 字节（几十 MB 级）——直推换节点重发时从 job 目录重读（`find_payload`）。
- **非阻塞探针是新的唯一分类实现**（`remote/hub_client.probe_job_result`）：一次请求 → 三态
  `ready` / `pending`（202/404：还没回，让位等下一轮）/ `transient`（网络错/5xx：没答，也算让位），
  **410 照抛 `JobFailedError`**——「还没好」与「永远好不了」必须分开，把后者当前者正是 x3-step 事故
  把「bun 缺失」写成 25 分钟超时的原因。hub 与节点两条链路只差端点路径（`/jobs` vs `/job`），
  靠 `path` 注入复用同一实现；`wait_job` / `wait_result` 两个阻塞版改为**建立在探针之上**，各自只保留
  自己的退避策略（历史差异：hub 是指数退避，直推是固定 poll）——状态码分类从此不可能在两条链路漂开。
- **直推链路：发布即提交**（`_push_submit_first` / `_push_submit_node` / `_push_fetch`）。探针要问
  「那份 job 现在怎么样了」，节点上还没有这份 job 时它只会一直答「还没回」⇒ 提交必须落在发布相位；
  提交本身是**有界**上传（几十 MB），不是那 25 分钟的等待。换节点必须**重新提交**（新节点没见过这份
  job），failover 判决与组合入口 `_push_job_round` 共用 `_push_over_nodes`（一份实现，三个调用方）。
- **evalboard idle 窗提前到发布相位**：它的用途是「等待期集群空闲，赶紧开窗领批」。细粒度路径会让位，
  若仍留在等待相位，窗口要等结果回来才开 ⇒ 永远错过它要服务的那段空闲。
- **`ctx.resumable`：让位点由「谁在驱动」决定**（`rl/loop_round.RoundContext`）。细粒度驱动器
  （`LoopRunner`）造上下文时置 True ⇒ 未就绪就 `wait_for`；组合路径 `run_one_round` 保持 False ⇒
  就地阻塞取结果（拆分前语义）。于是「组合路径没有让位点」从一个运行期异常（`RoundYieldError`）
  变成了一个**事实字段**。
- **`WAIT_HOOKS` 里不再有 `ppo`（且不得加回去）**：表里的闸门跑在步骤之前，对 ppo 来说那是「还没发布」。
  让位已由 `step_ppo` 自己产生（`tests/test_loop_runner.py` 把「不在表里」写成断言）。预采那一处不变。
- **失败判决只留一份**：`_handle_remote_failure`（从 `_remote_ppo_or_degrade` 的 except 分支抽出，
  逐字搬移）；发布 / 取结果 / 落位任何一段失败都进它——三段各自演化出不同的连败计数/停腿口径是本仓
  最贵的一类分叉。`_remote_ppo_or_degrade` 保留原签名与原 docstring（有 9 个既有用例钉着它）。
- **落地**：`nn-training/{remote/hub_client.py,remote/push_client.py,rl/loop_steps.py,rl/loop_round.py,
  rl/loop_round_steps.py,rl/loop_runner.py}`；拆分用一次性脚本（逐 hunk `assert` + `ast.parse`，
  被搬的行逐字节不变，跑完即删）。回归：`tests/test_remote_probe.py`（14）·
  `tests/test_remote_ppo_phases.py`（8）· `e2e/test_loop_supervisor_integration.py`（ppo 让位用例改为
  驱动真三相）· `e2e/test_push_mode_integration.py`（+3：发布相位提交 / 换节点重提交 / 探针目标）。
- **仍未做**：`Supervisor` 进控制台（单例卡片 + 每课队列视图）· `checkpointCacheMb` 真机 RSS 实测表。
**R2d 写的一半（2026-09-19 八续）——单进程 supervisor 真的能跑了：进程级/课程级/步骤级三层各做一次**：

用户 2026-09-18 指令（「训练循环任务队列化 —— trainingLoop 由一个进程服务所有并行课程」）的落地前半：
R2c 造好了调度器与任务体，但**没有驱动者**（至今仍是「一门课一个 `run_rl.py` 进程」）。本轮交付
`rl/loop_serve.py`（驱动者）、`rl/engine_pool.py`（N 课共享的 torch 栈缓存）、`rl/log.py` 的**行路由**
（单进程下的课程归属），入口 = `run_rl_cluster.py --serve --courses a,b`。

**分层纪律（每层各做一次；越界做两次会伤到既有护栏）**：
- **进程级一次**（`prepare_process`）：UTF-8 stdio / faulthandler / `chdir(repo)` / 启动前 `git push`
  （`.git_push.lock` 串行化）/ 节点升级分支锁到训练机分支 / bun 存在性。每课各做一次 = 每课都推一遍 git。
- **课程级一次**（`open_course`）：参数解析（与 `run_rl.py --course` **逐字段一致**；对拍见
  `tests/test_serve_wiring.py::test_course_args_match_run_rl_echo_config`——oracle 是在子进程里跑
  `run_rl.main()` 自己、把 `echo_config` 换成 dump，本机缺当前 era 权重则**跳过并写清理由**）+ `validate_args`
  + **按课程的单实例锁**（同课双开响亮拒启；2026-09-06 双 trainer 并写同一 traj 的护栏不删，只是文件名
  按课程命名）+ 清本课 hub 停机态。
- **步骤级**（`build_executor`）：`EnginePool.get(课)` → `ensure_ready` → `LoopRunner.executor`，整段包在
  `prefix_scope(课)` 里。`ensure_ready` 的判据是**对象身份**：引擎对象换了（首用 / 被驱逐后重建）⇒ 走
  `_setup()`（等同一次进程重启）；对象没换 ⇒ `_ensure_local_ppo_stack()` 幂等补齐。

**四条定案（都写进代码注释 + 用例）**：
1. **serve 模式不收官停车**：`_park_after_completion` 的死循环语义前提是「这个进程就是这门课」，多课程下会
   冻住全部 ⇒ 新拆 `TrainingLoop.finish_course(it)`（收敛预采 / 云机 PAUSE / `run_complete` 落账，**三件事
   与单课程路径共用一份实现**），serve 只调它 + 把该课队列置 `done`；全部收官才退出。
2. **不换 `sys.stdout`，改行级路由**：单进程里套两个 `Tee` 会把每行复制进两份课日志；课程归属 = 行前缀
   `[课]` + 镜像写进该课 `out_log`（`rl.log.open_course_sink` / `prefix_scope`）。无前缀时与改造前逐字节相同。
3. **引擎池的驱逐 = 响亮的一次「重启」**：容量默认 = 并行课程上限 5（§6.1 实测每课 MB 级），字节上限
   256MB 只是第二道保险；驱逐时**必须**记一行「谁被驱逐 + 权重可从 `args.out` 复原但 Adam 动量重置 + 若
   常发生请调大上限」，且**绝不驱逐在用引擎**（任务中途抽走栈会撞 `None.load_episodes`）。退出时释放全部
   栈（只关自己建的池——注入的池归调用方）。
4. **初次入队的粒度必须匹配执行体**：细粒度给 13 步任务表，轮粒度给**单个** `round` 任务；混了就是把 13 个
   步骤 kind 塞进只认 `round` 的执行体（响亮 ABORT，不是静默跳步）。初次入队用 `course_facts` +
   `pending_tasks(round_tasks(...))`（判据原语与只读计划视图同源），**不**调 `plan_course`——那会连 CLI 表格
   用的展示面字段（累计量/verdict）一起算。

**回归**：`tests/test_engine_pool.py`(9：惰性/命中/LRU/两道上限/不驱逐在用/响亮超预算/释放钩子/快照) ·
`tests/test_log_router.py`(6：无前缀逐字节不变/作用域还原含异常/镜像到本课/坏 sink 不崩/重复注册换句柄) ·
`tests/test_serve_wiring.py`(8：轮转交替 a,b,a,b / 13 步全表 / 让位让别的课跑完 / 容量 1 时驱逐重建再 setup /
账本已结算不建引擎 / 坏课隔离 / 参数对拍)。门禁：nn python gate **1476 passed / 4 skipped**；根 `bun run check` 绿。

**未做（R2d 剩下的操作面）**：控制台的入队/暂停 + 单例 `trainingLoop` 卡片（读面卡 R2c-3 已交付）；**R2e**：
多课 × 假 worker/假 PPO 的 e2e + 真机双课并行跑通（serve 的真机行为需要人验）。

**R2d 操作面（2026-09-19 九续）——进程不绑课程 + 暂停/恢复的控制文件通道（含生效回执）**：

用户定案两点：① 控制指令走**控制文件**（不在训练进程里再挂 HTTP 服务）；② **trainingLoop 进程
独立于课程**——没有课在训也能起，队列空着等。本轮把这两条落地，并把「离线开关」（R3-2）之外的
另一半操作面补齐。

**① 发现模式：进程不绑课程（`serve(courses=None)`）**
- `--serve` 不给 `--courses` ⇒ 启动扫 `--traj-root/*/training_log.jsonl`，之后**每个空转拍再扫一次**
  （新课程账本出现即自动开课入队）；**一门课都没有也照常运行**，`stop_reason` 不再有 `no_courses`
  这一条（空队列是合法稳态，不是结束条件）。显式课程表则退化为「只看这几门」，全收官即退（e2e/单课调试）。
- **被跳过过的课不再重试**（课程配置缺失 = 这一轮修不好；否则空转拍每秒刷日志）。
- `--mode` 必须**显式声明**在 cluster 解析器上：此前靠扫 raw argv 取，但 argparse 会先把
  `--serve --mode goal` 判成 unrecognized arguments 而拒启（声明了才能真透传）。

**② 控制通道 = 一份意图文件（`tmp/loop-control.json`）**
- 控制台写 `{"version":1,"paused":["c5"]}`（`dashboard/src/server/actions/loop-control.ts`，**原子写**
  tmp+rename：训练侧每拍都在读，读到半个 JSON = 读到坏文件 = 静默失效），训练侧每拍读一次并施加到
  调度器（`rl/loop_control.py`）。hub 挂了也能用，**文件本身就是状态**（对比 hub 的 course-mode 是
  volatile，那边要靠回灌）。
- **保守方向是刻意的**：读不到 / 解析失败 / 形状不对 ⇒ 当作「没有任何暂停意图」（继续训练）。控制面
  坏掉不该停掉整条腿——这与 `already_done` 的「算不出的判据不得当成完成」同一条纪律。两侧各自单测。
- **暂停只影响调度**（用户口径「暂停 = 保留队列，恢复后接着跑」）：队列与账本一个字不动。

**③ 回执面（意图 ≠ 事实）——为什么必须有第三个文件**
只有意图文件时，控制台点完暂停只能盲猜生效没生效（进程可能没在跑，也可能还没轮到读文件）。所以训练
进程把**自己实际施加了什么**写回 `tmp/loop-control.applied.json`（`at` + `pid` + `paused`，仅在
施加结果**变化时**写，不心跳），控制台用 `pid` 存活核对分辨「已暂停 / 待生效 / 恢复中 / 运行中」四态。
**进程已死 ⇒ 残留文件不作数**（否则界面永远显示「已暂停」）——这是回执能被当事实的唯一前提。

**④ UI：按钮改意图、徽标报事实（`LoopQueue` 卡片每行）**
- 按钮方向由**意图**定（未生效时说「取消暂停」、已生效说「恢复」）；徽标只在「意图 ≠ 事实」时出现，
  且**待生效用虚线、已暂停用实线**（同色即等于骗人）。「待生效」的悬停分两种解释：进程没跑 vs 还没
  轮到读——不能一句「处理中」糊过去。
- 开关是行按钮的**兄弟节点**（行本身是 `<button>`，嵌套 button 非法）；`onAction` 缺省 = 一个开关都不
  渲染（LAN 只读下不假装能控，同总览卡的离线开关）。
- 动作后**显式作废调度器视图缓存**（TTL 10s 比 hub 观测面的 5s 宽）——否则点下去要等一个 TTL 才上屏。

**⑤ 暂停不算收官（教训）**：`_all_settled` 原先把 PAUSED 当收官 ⇒ 「暂停一门课」会顺手把整个进程退掉，
恢复意图永远没人执行。现改为只认 `done`/`aborted`（暂停的课会让显式课程模式的进程一直等，用
`--max-seconds` 兜底；发现模式本来就不退）。

**回归**：`tests/test_serve_wiring.py`(+8：零课程照跑 / 中途出现的课自动入队 / 开不起来的课只试一次 /
暂停只停被点名的课 / 恢复从原处接着跑 / 坏控制文件保守继续 / CLI 发现模式与 `--control-file`/`--mode` 转发) ·
`tests/test_loop_control.py`(22：解析边界 / 非法名不废整份意图 / 保守方向 / 幂等 / 坏文件不被覆盖 /
回执形状与原子性 / **未变化不写盘** / pid 存活语义) ·
`dashboard/tests/loop-control.test.ts`(22) · `dashboard/tests/web-app-loopqueue.test.ts`(+9：四态 / 文案 /
徽标 / 只读不渲染开关 / route+cache 接线) · `dashboard/tests/server-api-loop-queue.test.ts`(+1)。
门禁：nn python gate **1511 passed / 4 skipped**；dashboard **621 passed**；三份 bundle + 根 `bun run check` +
`bun run build` 绿。

**仍未做**：单例 `trainingLoop` 卡片的分组（R3-3）· **R2e**（多课 × 假 worker/假 PPO 的 e2e + 真机双课
并行跑通——serve 的真机行为需要人验，本轮改动同样只在假件下验证过）。

**R2c-3 收口（2026-09-19 七续）——真机 RSS 实测：checkpoint 缓存上限的真实约束是「数量」不是「字节」**：

- **为什么要实**：单进程 supervisor 要为 N 门课各持一份 torch 栈（model + Adam + 冻结 ref），
  `checkpointCacheCourses` / `checkpointCacheMb` 的默认值此前**只能拍脑袋**（本机 0 卡、remote 为主）。
  plan §6 把这张表列为 R2c 上线前置条件。
- **实测（`nn-training/scripts/measure_checkpoint_rss.py`；本机 CPU-only torch 2.7.1+cpu）**：
  每课增量 per-tick **≈1.3MB**（70,216 参：model 0.7 + Adam 0.6，无 ref）/ intent **≈1.8MB** /
  goal **≈1.8MB**（各含一份冻结 ref）；**torch 基线 294.5MB 与课程数无关**；
  N=5 混合档累计 **294.8 → 301.8MB（仅 +7MB）**。
- **定案**：**`checkpointCacheMb` 是第二道保险，真实约束是数量** `checkpointCacheCourses`
  （= 并行课程上限 5）。推荐 `checkpointCacheMb = 256MB`（≈130 课，正常永不触发）；真触发即说明
  「某课的栈长得离谱」——那时该被看见（响亮拒绝缓存），不得静默驱逐。
- **口径诚实性（这张表最容易被读错的两处，已写进脚本 docstring 与用例）**：① **先暖一次再测**
  ——torch 惰性初始化（首次 kernel 选择 / 分配器建池 / Adam 首步）会让**第一份**栈看起来贵两个
  量级（实测 78MB vs 真值 0.75MB）；② **栈必须活着**——被 gc 回收后第二课的增量会变成 0
  （分配器复用），累计曲线就是假的。实测工具本身有回归：
  `nn-training/tests/test_measure_checkpoint_rss.py`（7 例：推荐值取整/余量、候选表恒有默认架构兼底、
  表格必须自带「测的哪份权重 / 跳过了谁 / 理论 vs 实测」、真造一份栈的量级保护带）。
- **不测且写明的**：单轮 episodes/chunk 缓冲是**另一处峰值**（由 `mb` 与本轮样本量决定）——
  它是每轮瞬态、且本机 PPO 池容量 1 ⇒ 同一时刻只有一份；已由 forensics 埋点，不属缓存上限管的常驻量。
- **仍未做（缓存实现本身）**：随「单进程 supervisor 真上线」的相位落地（R2d/R2e）。今天没有持有者，
  先写就是无消费者的投机代码；本表 + 上述两个默认值就是它上线时需要的全部输入。
**R2c-3 余下（2026-09-19 六续）——控制台接线：单例调度器卡片 + 每课队列视图（「在等什么」）**：

- **数据源 = python 只读入口，★ 控制台不得在 TS 重算判据**（防再犯条款）：卡片走
  `nn-training/run_rl_cluster.py --json`（训练侧只读：不训练/不发布/不等待），与 CLI 表逐字段同源。
  指针 → `RoundFacts` → `pending_tasks` / `waiting_state` 这套判据已在 python 侧被用例钉住；
  在 TS 里照账本重写一遍 = **第二份真相**（同 `loop-state.json` 段），两边会以不同速度演化。
  谁想「顺手在 TS 里读账本省掉一个子进程」，先重读本段：省下的是亚秒级冷算，换来的是两套语义。
- **成本与缓存**：`dashboard/src/server/api/loop-queue.ts`——懒算 + **TTL 10s** + 单飞 + 服务启动
  暖一次（避免 SSR 首屏等子进程）。TTL 比 hub 观测面（5s）宽，因为事实变化的粒度是「一轮」
  （分钟级）而冷算要起一个 python。读失败（解释器缺失 / 超时 / 输出不可解析 / 形状不符）**不抛**：
  视图带 `error` 上屏，UI 显因 + 空态——观测面坏掉不该把整页 `/api/state` 带崩（与 hub 总览、
  隧道 A/B 同口径）。
- **「在等什么」的判据留在 python**（`rl/loop_plan.py::waiting_state`，CLI 与控制台同一个函数）：
  `inflight`（已发布未回传，带 phase@round + jid + dispatch）/ `collect` / `idle` / `ready`，
  优先级 inflight > collect > idle > ready（**进程外的等待排第一**：结果在别的进程/机器上，
  运维唯一能干预的那一类）。★ **`games_planned` 诚实性**：盘上今天没有任何地方记「本轮计划多少局」
  ⇒ CLI 传 0 = 未知，`collect` 只报已落局数、**不报分数**（绝不出现 `78/0`）；这与 `already_done`
  同一条规矩——算不出来的事实不得当成完成，也不得编出分母。
- **两个事实源逐行合并**（`web/view/loop-queue.ts::withTraining`）：python 说「这一轮卡在哪」，
  registry（`trainingLoop` 进程存活）说「这门课此刻有没有人在跑」。★ 缺了后者，一门**停了的课**
  会被读成「等外部」——故未在训的行淡一档 + 悬停说明「下面是盘上事实推出的队列状态」。
- **与「并行课程总览」的分工**（防重复建设）：总览回答**hub 侧**「谁在派活 / 谁离线」（job 队列），
  本卡回答**训练侧**「这一轮卡在哪一步」（任务队列）——同一条流水线的两段，不合并。
- **落地**：`dashboard/src/{server/api/loop-queue.ts, web/view/loop-queue.ts, web/app/panels/LoopQueue.tsx}`
  （三个新）+ `server/run-python.ts`（新增同步脚本入口 `runRunPythonSyncScript`，与模块入口共用一份
  实现）+ `api/{state-view,index}.ts` / `web/view/console-types.ts` / `web/app/app.tsx` / `web/theme.css` /
  `server/server.ts`（启动暖一次）；训练侧 `nn-training/rl/loop_plan.py`（`waiting_state`）+
  `run_rl_cluster.py`（抽 `build_rows`、JSON 加 `waiting`、表里也打印）。
- **回归**：`dashboard/tests/server-api-loop-queue.test.ts`（13：解析容错 / 结果翻译四条失败分支 /
  TTL 复用 / 单飞 / 在训合并 / state 注入）· `dashboard/tests/web-app-loopqueue.test.ts`（12：SSR 每课
  一行 / 四态着色 / 未在训淡档 + 悬停 / 排队与页脚 / 读失败显因 / 接线断言）·
  `nn-training/tests/test_loop_plan_waiting.py`（14：优先级 / 未知配额不编分母 / 真读盘组装）。
  子进程是**可注入接缝**（同 `deliver_zip` 导入惯例）⇒ dashboard 用例不跑 python，python 用例不碰
  控制台，两边各测自己那一半。
- **仍未做**：① `checkpointCacheMb` 真机 RSS 实测表（R2c 上线前置条件）；② R2d 操作面（入队 / 暂停 /
  单例 `trainingLoop` 卡片）；③ R2e e2e（单进程多课 × 假 worker/假 PPO）。
**R2c-3 落地（2026-09-18 四续）——轮内切成 13 步 + 让位闸门（`WAIT_HOOKS` 表）**：

- **`RoundContext` 与 13 步表**：`rl/loop_round.py`（纯数据 + 表，无 torch/网络/IO）定义
  `RoundContext`（轮内跨步可见的量：`pairs`/`dist_cfg`/`t_rollout`/`seg`/会被半离线整段推进的
  `it`）与 `STEP_ORDER`（= `ROUND_TASKS`，**单一来源**）+ `STEP_METHOD`（kind → 引擎方法名）。
  步骤实现在 `rl/loop_round_steps.py`（`RoundSteps` mixin，逐行从轮体搬来）；组合路径
  `run_one_round` 与细粒度驱动器**都从同一张表取步骤** ⇒ 加一步必须同时进表，两条驱动不可能漂移
  （`tests/test_loop_round.py` 断言表与实现一一对应）。轮内状态**不长在引擎实例上**（§2.2 无隐藏
  状态：单进程多课程会互相覆盖）。
- **顺序错纠正（真发现）**：`precollect_join` 必须排在 `prepare_iter` **之前**——它产出的是本轮
  `it{it}` 的 shard，而 `prepare_iter` 靠 `completed_pairs` 看盘决定「保留续跑 / 清场重建」；
  反了就把上一轮的预采整个作废。
- **让位闸门 = 一张表**（`rl/loop_runner.WAIT_HOOKS`：kind → 引擎钩子名）。**钩子不存在 ⇒ 不让位**
  （行为与改造前逐字节一致）⇒ 表可以只先装能吃的两处。
- **★ `eval_join` 刻意不进表**（防再犯）：本机 eval 局的墙钟是「藏在下一轮 rollout 里」的
  （`_eval_tail` 交棒 → `_dispatch_delayed_eval` 入口收拢，2026-09-17 用户口径）。给它加让位 =
  把那条尾巴重新串回轮边界，正好抵消当初压掉的软等窗口。用例把这个缺席写死（`test_loop_runner.py`）。
- **★ `ppo` 闸门必须在「发布之后」**：今天的 `_remote_ppo` 是一个阻塞函数（打包→发布→阻塞轮询
  →校验落位）。在它前面加闸门 ⇒ 还没发布就被挡住 ⇒ 永远等不到回传。要装它必须先做「发布 / 等结果 /
  落位」三相拆分；**宁可不装也不装错**（本轮不装，表里留位 + 理由写在 `loop_runner` docstring）。
- **已装的一处：预采**（`precollect_join` → `TrainingLoop.precollect_ready`）。`join_precollect_child`
  旧形态每 2s 轮询、上限**1 小时**——单进程多课程下这就是「一个慢子进程拖垮所有课」的入口。
  判据抽成 `rl/rollout_phase.precollect_ready`（非阻塞：句柄为空 / 子进程已退出 / 就绪 shard ≥ 半波），
  步骤内部循环**改成调同一个函数** ⇒ 「调度器认为可以往下走」与「步骤进去真的不阻塞」不可能分叉。
  `join_precollect_child` 的外部语义（含 1h 超时 terminate）逐条不变。
- **读面补全「在等什么」**：`WAIT` 时把原因落到队列（`q.reason`），并在推进/收官时清空——
  状态 + 原因 + 在飞 `job_id` 三者合起来才够定位；过期原因比没有原因更坏。
- **落地**：`nn-training/rl/{loop_round,loop_round_steps,loop_runner,loop_scheduler,loop_core,loop_tasks}.py`
  （前两个新）、`nn-training/rl/rollout_phase.py`（抽判据）；回归 `tests/test_loop_round.py`（13 例）、
  `tests/test_loop_runner.py`（5 例）、`tests/test_precollect_ready.py`（7 例）、
  `e2e/test_loop_supervisor_integration.py`（+3 例：13 步逐一走完 + 步级轮转 / ppo 处让位 /
  预采处让位，账本形状与轮粒度逐行一致）。
- **仍未做**：① `ppo` 的三相拆分（让位点从「每轮」变成「每步」的最后一块）；② `Supervisor` 进控制台
  （单例卡片 + 每课队列视图）；③ `checkpointCacheMb` 真机 RSS 实测表（R2c 上线前置条件）。

---

### §2026-09-19-goalnn-serve-bc-course（2026-09-19，R3-4：单进程 supervisor 也能带 BC 课）

- **背景**：多课程并行之后训练侧收敛为**一个进程**（`run_rl_cluster.py --serve` → `rl/loop_serve.py`，§2026-09-18）。但 BC 课当时仍只能靠 `run_bc.py` 单开一个进程——理由不是需求，而是**形状**：`run_bc.py` 的 `main()` 是一整段 procedural 编排（解析 → 采集 → 发布 → 阻塞等待 → 落位归档），没有 supervisor 要的引擎子集（`_setup` / `run_one_round` / `finish_course` / `release_torch` / `ledger_next_it`）。用户口径「一个 trainer 进程服务所有课程」⇒ BC 必须能被同一个进程驱动。
- **决定**：把 BC 的编排体**逐字节**搬进 `rl/bc_loop.py`（`BcLoop` 引擎 + `BcRuntime` 解析 + 纯函数），`run_bc.py` 退为**入口薄壳**（进程级一次性副作用 + 阻塞式驱动）；`serve` 按课程种类分派引擎与粒度。
- **关键的四个取舍（都是「另一条路更好写但不该走」）**：
  - **不给 BC 造第二份一轮实现**：引擎里一轮的三段（开轮 → 等回传 → 落位归档）与单课程路径**共用同一份代码**。BC 的续训按 `jid` 存（hub `/jobs/{jid}/resume`、worker 本地 `bc-resume/<jid>`）⇒ 任何一次重发布 = 新 jid = **从头训**；两份实现里只要有一份漏了「先认领盘上 job」，代价就是一轮 GPU 时间。
  - **BC 课恒为「一轮 = 一个任务」**，不进 13 步表：13 步是 RL 的一轮（rollout/ppo/eval/门禁/记账），BC 的一轮是「采语料 → 发布 → 等回传 → 落位归档」。硬套 = 给 BC 发它不认识的待办（执行体响亮 ABORT，不是静默跳步）。粒度由 `loop_plan.round_tasks_for(course, it)` 单点决定（按课程种类选表），`LoopRunner(step_mode=False)` 由 `build_factory` 按同一判据设置。
  - **指针的语义归引擎**（`LoopRunner._ledger_next_it` 的 `ledger_next_it` 钩子）：BC 的「跑到第几轮」= `bc_round_completed`（`rl/bc_ledger.py`），RL 的 = `iteration`（`LedgerSpec`）。在桥里写死一种就是给另一类课程读错指针（症状：BC 课永远停在 it1）。读面（`loop_plan.course_facts(course=...)`）走**同一份**判据，`course_kind` = `curricula/<课>.bc.jsonc` 是否存在（与控制台 `isBcCourse` 同源，不靠账本事件推断——刚建的 BC 课账本是空的）。
  - **让位点只落在「等远端」这一段**：`BcLoop.run_one_round` 每次最多做一件事，等 GPU 回传时返回 `ROUND_WAIT`（新增的第三种轮终态：**本轮未完**，既不是失败也不是完成）⇒ 调度器把执行权交给别的课，过一会儿回来问同一轮。本机训练（`--local`）与 push 直推照旧阻塞（墙钟花在本机/邻居节点上，没有可让的余地）——与 RL 的 `eval_join` **刻意不进让位表**是同一条纪律。
- **备选与否决**：让 serve 把 BC 课委托给 `bcRound(course, it)` 子任务类型（另一条执行路径）——否（第二条「一轮」实现，正是上面第一条要禁的）；把 BC 课也拆成细粒度步骤（采集/发布/等/落位各自一个任务）——本相位否（收益只是更细的让位点，而 BC 的墙钟几乎全在「等 GPU 回传」这一段，已让位；留作需要时再下沉）；让 `run_bc.py` 变成只 import 新模块的 re-export 壳（保持旧测试导入路径）——否（同一对象两个名字会让「谁是家」含混，改为把测试指向新家）；BC 课仍用一个专属进程（本轮不动）——否（与用户口径冲突）。
- **违反后果**：任何在 `rl/bc_loop.py` 之外再写一遍「一轮」（含控制台/工具脚本自己发布 job）都会重新引入「重发布 ⇒ bc-resume 失效 ⇒ 从头训」这条最贵的错误；任何把 BC 课按 RL 读账本的地方都会得到 `next_it=1`（看起来「这课没在训」）；给 BC 课发 13 步任务表会让该课在第一次执行时就 ABORT。
- **落地**：`nn-training/rl/bc_loop.py`（新，1395 行；14 个函数体从 `run_bc.py` **AST 逐字节搬迁**、`_append_ledger`/`_ledger_bc_epoch`/`_run_epoch_eval`/`_finish_all_rounds`/`_archive_round` 去下划线）· `rl/bc_ledger.py`（新，BC 指针/完成集/收口 job 的单一读面）· `rl/loop_round.py`（新终态 `ROUND_WAIT` + `RoundOutcome.detail`）· `rl/loop_runner.py`（`ROUND_WAIT` → `waiting(..., jid=..)`；`ledger_next_it` 钩子）· `rl/loop_core.py`（单课程驱动器对 `ROUND_WAIT` 的阻塞语义：退避重问同一轮）· `rl/loop_plan.py`（`course_kind` / `round_tasks_for` / `course_facts(course=)`）· `rl/bc_config.py`（`is_bc_course`）· `rl/loop_serve.py`（`_open_bc_course` + 工厂分派 + 入队粒度）· `run_bc.py`（薄壳）· `run_rl_cluster.py`（`build_rows` 带 `kind` + 人读表加种类列 + BC 行的 facts 行不再摆一排 RL 的零）· 控制台：`web/view/loop-queue.ts`（`LoopCourseKind` + `kindBadge`/`stepTitle`/`pendingTitle`）· `web/app/panels/LoopQueue.tsx` · `web/app/app.tsx`（解除 `isBc` 门控）· `web/theme.css`（`.tc-loopq__kind--bc`）。回归：`tests/test_bc_ledger.py`(9) · `tests/test_bc_loop.py`(16) · `tests/test_serve_bc.py`(5，serve × BC 集成：让位/驱逐认领/粒度/锁/故障隔离) · `tests/test_loop_plan_bc_rows.py`(9，读面：种类/指针/在飞/两行共存) · `dashboard/tests/web-app-loopqueue.test.ts`（+3：BC 行与 RL 行并列 / 悬停各说各的 / 卡片不再被 `isBc` 门控）· `dashboard/tests/server-api-loop-queue.test.ts`（+1：`kind` 的保守默认）· 既有 BC 用例改为指向新家（`tests/test_bc_course.py`、`tests/test_remote_transport.py`、`e2e/test_bc_epoch_e2e.py`）。进度 `docs/nn/training-stack.md` §19 / `docs/nn/console.md` §6；plan `plan/r2-loop-task-queue.md §8 R3-4`。
- **控制台半（2026-09-19 同日接上）**：调度器卡片是**跨课程**卡（一次列出所有账本可发现的课），BC 行与 RL 行**并列**——读面（`run_rl_cluster.py --json` 的 `kind`）早在 R3-4 就通了，缺的只是 UI。三处关键决定：
  - **行上带课程种类**（`build_rows` 的 `kind` / 视图层 `LoopCourseKind`）：BC 的指针（`bc_round_completed`）、粒度（单个轮任务）、在飞来源（账本 `job_pending`）与 RL 全不同，不带种类 UI 只能猜（猜错就把 BC 读成一排看着像真的零）。种类判据 = `curricula/<课>.bc.jsonc` 是否存在（`loop_plan.course_kind`，与控制台 `isBcCourse` 同源）。
  - **`kind` 缺省/未知一律按 `rl` 渲染**（保守方向单侧）：python 比控制台旧时（还没这个字段）少一个徽标只是少信息；凭空空贴 BC 标签则会对外宣称「一轮 = 一个任务」（而它有 13 步）——假承诺比缺标签贵。`BC` 这种大小写不符也不认（只认 python 的确切取值）。
  - **卡片解除 `isBc` 门控**：`stateView.isBc` 说的是**当前查看的那门课**，而这张卡是**跨课程**的——用它门控是范畴错误，后果是「选中一门 BC 课 ⇒ 整张卡片消失」，于是 BC 课在调度器视图里根本不存在（而 BC 课正是最需要看「在等哪个 GPU job 回传」的那种）。BC 行只多一个 `BC` 徽标 + 换成「一轮 = 一个任务」的悬停文案（不出现 RL 的门禁/verdict/KL 字眼）。
- **未做（明确记录，不是漏）**：① 真机「一个 serve 进程带 BC + RL 双课」的实跑（本轮全在假件下证明逻辑，与 R2e 同一口径）；② BC 一轮再下沉成细粒度步骤。

---

### §2026-09-19-goalnn-shared-trainer-single-process（2026-09-19，R3-5：trainer 收敛为「一个进程服务所有课程」）

- **背景**：R3-3 分族时留了一条明写的边界——分族只交付**读面**，账本键的真收敛未做：控制台仍按课起 `run_rl.py --course`，于是「BC 课 A + RL 课 B」要两个进程，尽管 ① R2d 已造好单进程驱动者（`rl/loop_serve.py`：按课锁 / 按课日志镜像 / 引擎池 / 故障隔离 / 暂停恢复）、② R3-4 让同一个进程也能带 BC 课。用户口径：「hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程，就能同时支持所有并行训练课程」。
- **决定**：`trainingLoop` 进 `SHARED_COMPONENTS`（账本槽恒 `''`，与 hub/隧道同一张表同一套哨兵），控制台一律拉起 `run_rl_cluster.py --serve`；卡片自动从课程面落到**服务面**（族归属是 `componentScope` 的函数，R3-3 已把这条路修好——本轮只改判据，不改 UI）。
  - **课程 = 文件系统事实**（`<traj-root>/<课>/training_log.jsonl` 存在，与训练侧 `rl/loop_plan.discover_courses` 同一判据）⇒ 启动时**不给 `--courses`**，「先起 trainer、后加课」不需要重启进程；代价是控制台得替**这门课**把账本文件建出来（`prepareCourseForSharedTrainer` ②）。
  - **每课旋钮住 rl-config**（`courses.<课>.{remote_transport,remote_hub_url,remote_degrade_after}`；新 `stack/course-knobs.ts` 是唯一写面，python `loop_serve.apply_course_machine_overrides` 在**开课时**施加：白名单 + 值域校验 + 逐键打印）。单进程没有「这门课的 flag」这一说（命令行只有一份）。**绝不写进 `curricula/*.jsonc`**——课程文件字节 = `course_fp` 语料血缘/熔断口径（D14），往里加一个传输旋钮，熔断会把同一份语料读成新语料。已开课的课程要**重开**才换传输（暂停该课 → 重启共享 trainer / 等引擎驱逐），这一点写进了动作返回值。
  - **幂等早退仍做本课准备，但准备失败不改事实**：早退前照样写本课旋钮 + 建账本（不写则「给这门课换成 push」是静默无效的动作），但准备阶段抛错时**不冒泡成通用失败**——消息必须以「已在运行 / 服务所有课程」开头、失败只说本课。理由很具体：说成「trainer 启动失败」会诱使操作员去停/重启它，而**停共享 trainer = 停掉所有课程的训练**。
  - **进程级单实例锁**（`nn-training/.run_cluster.lock`，`lockName('', 'run_cluster')`）：按课锁拦不住「两套调度器各跑一半课程，每门课都恰好只有一个跑者」。python `--serve` 自己响亮拒启 + 控制台在 spawn 之前先探（错误落在动作返回值里，不在日志里）；停机时释放。
  - **旧形状换代接管**：存活的 per-course trainer 由 `stack/hub.ts::supersedeLegacyInstances('trainingLoop')` 显式停掉并清账（与 hub/隧道同规），死条目也清；`restartSpecFor('trainingLoop', <课>)` 对**每课条目返回 null**——用共享 spec 重建一个每课条目 = 两套调度器抢同一批 traj。
  - **停止语义**：停 trainer = 停**所有**课程的训练（消息里必须说出来，否则操作员以为只停了当前查看的那门课）；停单门课用调度器卡片的「暂停」（控制文件 `tmp/loop-control.json`，只影响调度，队列/账本一个字不动）。
  - **「在训」判据随之改口径**：registry 里不再有每课条目，故「哪几门课在训」= **调度器（`sharedTrainerAlive()`）+ 该课未收官**（`loop-queue` 的行），总览与课程 select 高亮同源。
- **备选与否决**：① 给 `--serve` 传 `--courses <课表>`——否（进程绑死课程表，「先起 trainer、后加课」当场失效，而 hub 已确立「课程 = 发现」的口径）；② 每课传输旋钮塞进 `curricula/*.jsonc`——否（熔断口径，见上）；③ 共享 trainer 改住扁平单例键——否（旧账本里的 per-course 条目必须继续可见、可枚举、可停止；静默失监督是事故。共用一张表天然做到，靠槽 `''` 区分）；④ 顺手把 `localWorker`/`workerServe` 也收敛——**不做**（本机 PPO worker 的语义就是「poll **本课** hub」，每课一个是对的；`worker_server` 的语义轴是节点，见 `NODE_FACE_COMPONENTS`）。
- **违反后果**：任何「按课起一个 trainer」的残留路径都会与共享调度器抢同一批 traj（症状：同一轮被两个进程各跑一半、账本交错、锁语义失效）；任何把本课准备失败说成 trainer 启动失败的话术，都会让操作员停掉所有课程。
- **落地（训练侧）**：`nn-training/rl/loop_serve.py`（`apply_course_machine_overrides` + 覆盖叠加与优先级 + 非法值响亮）· `nn-training/run_rl_cluster.py`（`--serve` 进程级单实例锁 + `--ppo` 直通）。回归：`tests/test_serve_course_overrides.py`(10：覆盖叠加/优先级/非法值响亮/未知键不认) · `tests/test_serve_wiring.py`(+1：单实例锁接线)。
- **落地（控制台）**：`core/registry.ts`（`trainingLoop` 进共享表）· `core/slots.ts`（锁名归一）· `core/types.ts` · `launch/cli.ts` · `stack/specs.ts`（`trainerServeSpec`，发现模式 argv）· `stack/course-knobs.ts`（新：机器侧旋钮写面）· `server/actions/{start,stop,restart,preset,smoke,train-smoke}.ts`（启动/换代/锁/停止语义/冒烟独占）· `server/api/{overview,loop-queue,state-view}.ts`（在训判据）· `web/`（视图同源）。回归：`tests/training-shared-trainer.test.ts`(9：argv 不绑课程表 / 每课条目拒重建 / mode→transport 逐条 / 账本=发现判据 / 进程级锁 / 停止语义 / 冒烟独占) · `tests/training-console-busy.test.ts`(9：**幂等早退仍释放 busy 键** + **准备失败不许冒充「启动失败」**，夹具已重定向 traj 根与 rl-config——此前它把断言挂在「本机 tmp 恰好有没有权重文件」上)。进度 `docs/nn/training-stack.md` §20。
- **未做（明确记录，不是漏）**：① 真机「一个 serve 进程同时带 RL 课 + BC 课」的实弹运行（本轮全在夹具/假件下证明逻辑，与 R2e 同口径）；② `workerServe` 账本键的轴仍是课程（展示面已按节点例外声明，账本键未动）；③ `TrainLaunchModal` 的精简（模式仍按课选，落点已改为课程旋钮）。

---

### §2026-09-19-volume-continuous-quota（2026-09-19，用户指令：退役离散补波 → 配额感知连续派发）

- **背景**：x20 分关样本缺口/wave_cap 复盘 —— 全局 est × 等 G0 盖不住 1 命下变体间
  产量方差（通关率波动 → 局均 nSamples 波动）。用户裁定：**完全去掉补波机制**，loop
  实时观测 samples 分布，即将足额不再派、差额大者多派。
- **规则 VOLUME_RULE_V2**（`rl/volume_quota.py`）：分关配额 `ceil(target/n_stages)`；
  每批 `allocate_stage_games`：`collected+inflight*est_s ≥ quota` → 软停；差额按
  `ceil(shortfall/est_s)` 派；`game_cap` 硬顶；`DEFAULT_MAX_BATCHES=12` 安全阀（触顶
  响亮 WARN，非静默短采）。种子 `(rotate_seed,it,stage)` 独立流第 k 局（无 wave_idx）。
- **备选与否决**：保留 G0+补波仅改分关 est —— 否，用户要求去掉波次语义；worker 内
  每局实时选关 —— 改动面过大，v2 用「账本驱动小批」逼近同一语义（批间同步结算）。
- **§15.5**：相对 wave 规则的语料构造变更 —— 迁课程建议 fresh `--out/--traj`。
  wave 纯函数（`volume_waves.plan_topup` 等）仍保留供旧单测/e2e；**生产串行路径**
  已切 `_volume_collect_continuous`（`loop_core`）。`resume.trailing_stage_samples_per_game`
  提供分关 est_s。
- **落地**：`rl/volume_quota.py`、`loop_core._volume_collect_continuous`（S4 第十八刀后住
  `rl/loop_volume.py`）、
  `tests/test_volume_quota.py`。回归：相关 pytest + parse 绿。

---

### §2026-09-20-console-process-course-decoupled（2026-09-20，用户指令：服务进程启动与课程解耦 + 开课/停课独立入口）

**背景（一次实测故障里其实躺着两件事）**：用户点「启动训练」（课程 `x20-steady`）失败，控制台输出：

```
❌ 启动训练中断于 trainingLoop
selfNode: self-node 已在运行 (port 8443)
hubServer: hub-server 已在运行 (port 8787)；已回灌 0 门课的离线/在线意图
失败 x20-steady: 需要合法 course（[]）与 mode（['online', 'offline']）
trainingLoop: 共享 trainer 启动即退出 (PID 2496)
```

- **① trainer 死因 = spec 漏挂 venv site-packages**（与课程耦合无关）：`venv.python` 是 uv 跳板的**真身**
  （基础解释器——这台机器 `pyvenv.cfg` 无 `executable`，`resolveVenvPython` 只能从 `home` 取），第三方包
  只能靠 `PYTHONPATH` 挂 venv site-packages。R3-5 新增的 `trainerServeSpec` 只挂了 `NN_TRAINING`
  ⇒ `ModuleNotFoundError: No module named 'pydantic'` ⇒ 「启动即退出」（`tmp/trainer-cluster.log` 实锤）。
  hub/其余组件无恙：它们要么只用 stdlib，要么本就带这一项（`localWorker` / `trainingLoop` / BC spec 都是
  `${sitePackages};${NN_TRAINING}`）。**定案**：`trainerServeSpec` 与它们同规；实测 `run_rl_cluster.py --help`
  在该 PYTHONPATH 下 import 链全通。
- **② 「需要合法 course（[]）」= 时序错位**：hub 的课程表是**扫盘发现**
  （`<traj-root>/<课>/{remote-jobs,offline}` 存在且新鲜，`_course_dir_live`），而旧形状把「置 hub 模式」挂在
  **启动 trainer 的第三步**上——那一刻课程目录还没建出来（`remote-jobs/` 要等训练侧第一次发布 job）
  ⇒ hub 诚实地回 400。

**用户指令（原文）**：「服务进程启动不应与课程绑定。进程启动时不要自动开启课程训练，需要增加独立的入口
开启/停止课程训练！」

**定案**：

1. **启动 = 只起进程**：`startPreset(opts)` 不再要课程；`startSharedTrainer` 不再有任何按课程的副作用（旧的
   「播权重 / 建账本 / 写课程旋钮 / 置 hub 模式」四件全迁走）——「起个进程」不必先选一门课，② 的时序
   也从结构上消失。停止语义随之更干净：重启共享 trainer 的自动解停改成**全课程**（无课程 = 全课程）——
   被停掉的正是整个调度器，只解「当前查看的那门」会让其它课替它背锅。
2. **开课 / 停课 = 独立入口**（`actions/course-lifecycle.ts`；路由 `openCourse` / `stopCourse`）：
   - **开课** = 课程级旋钮（`courses.<课>.{rollout_src,run_iters,remote_degrade_after}`）+ 发现事实
     （账本 + **`remote-jobs/`** + RL 权重播种）+ 解暂停 + 置 hub 模式（**有界重试** 3×2s：hub 扫到这门课
     要一两拍）+ 报执行面。**进程没跑也能开**（trainer 是发现式的，下一拍就入队）。
   - **停课** = **非破坏**：暂停意图（`tmp/loop-control.json`，与调度器卡片的「暂停」同一份契约）
     + 该课 hub 置 offline。队列/账本/课程表一个字不动，恢复走开课。
3. **课程级选项整体迁到「开课」**：训练模式（在线/离线）· rollout 位置 · 降级本机——它们落
   `courses.<课>.*`，而进程是共享的一台。启动弹窗只剩进程级（隧道/瘦身/行为开关/预演）；`preset` 路由对
   `trainMode`/`rolloutSrc`/`remoteDegrade` **响亮 400**（静默丢掉 = 一条假承诺）。
4. **入口位置（用户选）**：顶部课程选择旁的按钮（已停课显示「开课」，否则「停课」）。判据由 `/api/state`
   的 `courseLifecycle` stamp：`open` = 账本存在（与训练侧 `discover_courses` / hub `--discover` 同一判据）、
   `stopped` = 暂停意图。只读视图**不物理禁用**按钮（只读是动作边界，不是按钮状态——
   `web-ssr-readonly.test.ts` 钉着这条）。

   ★ **本点当天即被取代**（同日晚些的报障）：判据「账本存在」= 历史课全在训（进程一起来把 tmp/ 下
   21 门历史课一起拉去跑），按钮形状也被用户在后面的指令里改成「课程 select + 「训练」按键 +
   每门课一个 pill（含停课）」——见 §2026-09-20-course-enable-marker-and-training-pills，
   那里有完整的判据、形状与否决理由。

**备选与否决**：

- 停课 = **下架账本**（删/改名 `training_log.jsonl`，让调度器与 hub 都看不见它）——否：真从训练里消失，
  但 iter/队列/账本等阅读面**同时**消失，且重开 = 重建账本（历史归零）。
- 停课 = **只停本机**（只写暂停意图、不动 hub 派发）——否：云机仍会领走队列里已入队的整段 job 跑完，
  而操作员以为停了。
- 保留「启动时顺带开课」并加一个「启动后立即开课」勾选框——否：默认路径仍把两件事绑在一起，② 照旧存在。
- 用 `core/config.ts::validateCourseArg` 做开课的课程校验——否：它最后一手是 `process.exit(1)`，对长驻的
  控制台进程就是自杀（一次误点 = 整个控制台消失）；改成可捕获的 `ActionError`，判据保持一致。
- 为让 hub 立刻认课而在 hub 侧加显式注册 API——否：hub 与 trainer 共享同一份盘，「课程在这跑」本来就写在
  盘上（R3-5 的发现模式原则）；多一条注册旁路 = 会失败、会乱序、会忘了调的第二个事实源。开课改为先建
  `remote-jobs/`（训练侧第一次发布时本就会建的那个目录），空目录 = hub 认课的锚点。

**违反后果**：把课程准备重新挂回启动路径 ⇒ 「起进程先选一门课」与 ② 的 400 一起复活；停课改成下架账本 ⇒
操作员看不到自己刚停的那门课的任何状态（阅读面连带消失）。

**落地**：`dashboard/src/stack/specs.ts`（`trainerServeSpec` PYTHONPATH 修复）·
`server/actions/{start,preset,course-lifecycle,index}.ts` · `server/api/{route,state-view}.ts` ·
`web/view/console-types.ts` · `web/app/{app.tsx,panels/{TrainLaunchModal,OpenCourseModal}.tsx}`。

**回归**：`dashboard/tests/course-lifecycle.test.ts`（新，19 例：开课把发现事实写全（含 `remote-jobs/`）·
课程级旋钮两向（在线撤离线标记 / 离线成对落键）· 置 hub 模式的「hub 还不认识这门课」回归与有界重试 ·
停课非破坏与可逆 · hub 不可达时意图照样落盘）· `training-shared-trainer.test.ts`（② 段改钉「准备只住
course-lifecycle + 启动路径零课程写盘」）· `training-console-busy.test.ts`（幂等早退不碰课程：traj 根
坏掉也必须成功）· `web-train-launch-wiring` / `train-mode-offline` / `rollout-src-launch-option` /
`slim-launch-option` / `push-config`（课程级选项不得回流启动链路）。

**gate**：dashboard **737 pass / 0 fail**（717 → 737）+ `tsc --noEmit` 干净 + 三份 bundle 构建通过
（app 292245B / gzip 68110B）；根 `bun run check` **1938 pass / 0 fail**。

**未做（明确记录，不是漏）**：① 实弹验证「停课后云机也不再领活」需要真节点（本轮全在假 fetch 下证明逻辑）；
② 课程生命周期只在顶部按钮一处可见——`CourseOverview` 行内那格仍是「切离线」，两者语义不同
（离线 ≠ 停课：离线课照旧在本机跑/整段上云）。（② 已被次日口径部分解决：在训课程现在顶部有
pill 行，见下一条。）

---

### §2026-09-20-open-course-lock-identity-and-write-order（2026-09-20，用户报障：开课被「自己人」的锁拒掉）

**背景**：用户点开课，回执是

```
❌ x20-steady 未开课：run_rl 锁被 PID 18364 持有
训练模式 在线：已撤掉离线标记（run/run_iters）
rollout 位置覆盖：courses.x20-steady.rollout_src=local
远端连败降级本机：关（连败即 ABORT）
已写开课标记 training-enabled.txt（训练侧/hub 的「在训」判据）
先停掉在跑的那一份（或删除锁文件）再开课——它与共享 trainer 抢同一批 traj。✕
```

两处独立缺陷叠在一起：

1. **判据错**：PID 18364 = 控制台自己起的**共享 trainer**（`run_rl_cluster.py --serve`）。
   它服务多课，每开一门课就取该课自己的**按课锁**（单进程多课模型的正常持有）——而检查的
   判据是「该课锁活着就拒」⇒ **每次开课都被自己人拒**，只要 trainer 在跑就永远开不了课。
   真冲突的形状是「另一份**按课** runner（手工 `run_rl.py --course <本课>`）活着」——它与
   共享 trainer 抢同一批 traj（两套调度器各跑一半课程、互相覆盖权重）。
2. **写序错**：检查排在写面**之后** ⇒ 被拒的那一次照样写了课程旋钮 + **开课标记**（标记就是
   训练侧/hub 的「在训」闸）+ 撤了离线标记，而暂停意图还留着。于是三种口径并存：控制台说
   「未开课」、hub/云机说「这课在训」、调度器按暂停意图一拍不推。

**决定**：

* **开课前置检查带身份看**（`course-lifecycle.ts::courseRunnerFacts`）：该课按课锁的存活持有人
  == **共享 trainer 进程级锁**（`nn-training/.run_cluster.lock`）的持有人 ⇒ 同一个进程 ⇒
  正常持有，**放行**并在回执里说明白（免得操作员在日志里找一个不存在的双开）；持有人活着但
  不是它 ⇒ 真冲突，拒开课。陈旧锁（持有人已死）两侧都归 null ⇒ 不拦。BC 同规（锁名换 `run_bc`，
  比较的仍是那把进程级锁）。
* **写序 = 「会拒的先拒、会抛的最前、开课标记最后」**：① 全部会拒绝的判据零副作用先行
  （锁身份 + **暂停意图文件健康**——`setCoursePaused` 对坏文件是保守拒绝、不覆盖）；② 写面
  按 `saveConfig`（唯一会抛的一步，容量/槽位守卫）→ 解暂停意图 → `prepareCourseForOpen`
  （旋钮/账本/`remote-jobs/`/**开课标记**）排序 ⇒ 任何失败都不留「已开课」的盘上假象。
* 停课**不删**按课锁：锁归进程所有（停机时释放），控制台删它只会让「双开」的判据消失。

**备选与否决**：

* **去掉开课时的锁检查，直接开**——否：手工跑着 `run_rl.py --course <本课>` 时开课就是两套
  调度器抢同一批 traj（静默互相覆盖权重），这一类必须拦。
* **按「锁文件存在」判、不看持有人身份**——否：陈旧锁（崩溃残留）会把课永久锁死，而陈旧态
  在盘上极常见（`nn-training/` 里就躺着 20+ 把 09-14/09-20 的旧锁）。
* **把「共享 trainer 在服务本课」当成「已经开着」而直接返回成功**——否：回执会变成一个假
  成功（暂停意图、hub 模式这些开课动作一步都没做）；这些动作是**幂等且有意义的**，照做并按
  事实报告才对。
* **开课失败时回滚已写的盘**（补偿事务）——否：写序已经让「被拒 = 零副作用」，补偿逻辑本身
  是第二处可能出错的地方；真实失败的窗口只落在「旋钮已写、标记未写」（= 无人调度的旋钮，
  重按一次开课即自愈）。

**gate**：dashboard **792 pass / 0 fail**（`course-lifecycle.test.ts` 新增 9 例：共享 trainer
持有按课锁 ⇒ 开课成功且回执说明「正常持有」／另一份按课 runner ⇒ 拒开且**零副作用**（无标记、
无旋钮、无 `remote-jobs/`、没碰 hub）／陈旧锁不拦／三态判据 + BC 锁名 ／暂停意图文件坏了 ⇒
拒开且不覆盖／`saveConfig` 抛 ⇒ 连暂停意图都不解／成功时回执行序与盘上一致）·
`tsc --noEmit` · oxlint 0 warning · 三份 bundle 构建通过 · 根 `bun run check` 绿。
**真盘验证**（只读探针，`nn-training/` 真锁）：`cluster()=18364`、`run_rl(x20-steady)=18364`、
`facts={holder:18364,cluster:18364,conflict:null}` ⇒ 那次被拒的开课现在会放行。
---

---

### §2026-09-22-course-error-isolation-loud（2026-09-22，课程配置错误不得弄崩共享 trainer；控制台响亮报错）

2026-09-22 事故：开课 x20-demo-mix 选「离线（整段上云）」但课程 `iters=0`，run_rl 的
SystemExit（`--run-iters<0 需要课程声明 iters——没有终点就不叫整段`，BaseException）从一步级
执行穿透调度器 `except Exception` 的 RETRY 兜底，把**整个共享 trainer**（一个进程服务所有课程）
弄崩（PID 18748）→ exit-watchdog 判死 → 误向云机下发停机。用户口径：「控制台应响亮报错，
而不是泛泛的意外退出」「课程设置错误，应该是把课程下线，而不是把基础设施弄崩」。

**三层防线（本文落地）**：
- **开课预校验（预防）**：`openCourse` 的零副作用预检区新增「离线模式要求课程声明有限
  `iters>0`」，违规即 `ActionError`（文案与 python 守卫同口径：「没有终点就不叫整段」），
  盘上零副作用——trainer 根本接触不到坏配置。
- **一步级按课隔离（遏制）**：`rl/loop_serve.py::build_executor` 在课程归属内 catch `SystemExit`
  （只此一类 BaseException，不动 Exception 的 RETRY 语义），转 `abort(...)` 走调度器既有
  「ABORT：只脏本课（§4.3）」：该课队列 ABORTED + `report.skipped/failures` 记录原因，
  发现模式不重试；**其余课照跑，进程不再被单课错误带崩**。KeyboardInterrupt 不受影响。
- **意外退出原因进横幅（可观测）**：`exit-watchdog` 新增 `tailFailureReason`（traceback 末行 /
  无时间戳裸 `[run_rl]`/`[serve]`/`[loop]` 前缀行两种形状），`runExitCheck` 在 `recentPlannedStop`/
  `tailNormalCompletion` 之外命中即把具体原因写入 `registry.error` 与云停机 reason——横幅从
  「意外退出 (PID …)」变为「意外退出 (PID …)——原因：…」（保留「意外退出」字样，与
  「已停车」判别面不混）。

**否决的备选**：
- 给调度器 `except Exception` 直接扩成 `BaseException`——否：会把 KeyboardInterrupt 也吞成
  RETRY（Ctrl-C 停不了 serve），且 RETRY 会无限重试刷日志；只按课捕 SystemExit 才能
  "只脏本课"。
- 开课预校验放 UI 弹窗——否：拒绝必须在服务端动作边界（UI 可绕过、且要零副作用保证），
  放 `openCourse` 预检区与既有「被拒 = 什么都没发生」契约同区。

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §23→§1 · 旧 §26→§2 · 旧 §36→§3 · 旧 §37→§4 · 旧 §38→§5 · 旧 §43→§6 · 旧 §44→§7 · 旧 §45→§8 · 旧 §48→§9 · 旧 §76→§10 · 旧 §77→§11 · 旧 §78→§12 · 旧 §79→§13 · 旧 §80→§14 · 旧 §81→§15 · 旧 §83→§16 · 旧 §84→§17 · 旧 §85→§18 · 旧 §86→§19 · 旧 §89→§20 · 旧 §90→§21 · 旧 §108→§22 · 旧 §118→§23 · 旧 §120→§24
