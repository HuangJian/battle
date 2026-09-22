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
- **接线**：`loop_core._volume_collect_continuous` 收官处
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
loop_core._setup_common   ← load_ledger 一次
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
（2026-09-15；计划 `plan/dynamic-rollout-volume.plan.md`；决策 `DECISIONS.md §2026-09-15-goalnn-dynamic-rollout-volume`）

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
决策与拒绝的备选：`DECISIONS.md §2026-09-15-goalnn-local-ppo-worker`。

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

**机制**（DECISIONS §2026-09-13-hot-reload）：trainer 每 iter（rollout 前）重读课程文件，按
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

**分类学**（DECISIONS §2026-09-13-level-extraction，全量版）：A 语料身份（关卡 env + reward
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
  - **P3b**（DECISIONS §2026-09-12-multi-course-p3b-supersedes-343）：hub 独占租约 `CLAIM_TTL_SEC=300`（领取即设 owner+expiry+heartbeat，心跳续租，过期回池，有活租约验 `X-Lease-Token`）；worker_server 有界 FIFO `WORKER_QUEUE_MAX=8` + 同 jid 幂等 + `/ping queued`。
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

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §23→§1 · 旧 §26→§2 · 旧 §36→§3 · 旧 §37→§4 · 旧 §38→§5 · 旧 §43→§6 · 旧 §44→§7 · 旧 §45→§8 · 旧 §48→§9 · 旧 §76→§10 · 旧 §77→§11 · 旧 §78→§12 · 旧 §79→§13 · 旧 §80→§14 · 旧 §81→§15 · 旧 §83→§16 · 旧 §84→§17 · 旧 §85→§18 · 旧 §86→§19 · 旧 §89→§20 · 旧 §90→§21 · 旧 §108→§22 · 旧 §118→§23 · 旧 §120→§24
