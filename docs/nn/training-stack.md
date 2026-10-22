# NN 训练栈 — 技术档案

> 训练循环 / 调度器 / supervisor / 课程编排 / 门禁与停车。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---
## §40 自主机首批铺满撞「首批局数同源」校验 —— 宽度随块声明（`widen_width`；2026-10-22 事故修复）

**触发**：y30-stack 离线自主段 it156 **一局没跑**就 `ProtocolError`，整条串行链停在第一门课（剩 4 门未执行）。

**根因**（三个环节各自都对，串起来必死）：

1. `remote/plan_run.py` 的 `widen_by_width` 把首批局数按本机 worker 数加宽（y30：9 局/关 → 24/24/23/23 = 94；
   包内 `workers=1`、Σ=36，云机 94 槽）；
2. `worker/plan.iter_spec` 把块转运行时块时调 `validate_runtime_volume`；
3. `worker/volume_alloc.validate_volume_block_ext` 强验 `games_per_stage_by_stage == alloc_games_by_stage(est_hi)`（=9）
   ⇒ 24 ≠ 9 响亮失败。

`plan/rollout-topup-fastpath.plan.md` S8 早已写死这条强验；`plan/rollout-corpus-supply.plan.md:266` 加宽时只改
games、没碰校验 ⇒ 缝隙没测试（`_run_iteration` 的加宽分支没有端到端用例，两刀各自绿）。

**修法**：宽度**随块声明**（`widen_width`，缺省 0 = 未加宽），校验侧 want 值改成
`widen_by_width(alloc_games_by_stage(...), widen_width)` ⇒ 同源仍是**可复算**，不是「谁都能把首批局数改大」。
节点 `iter_topup` 跑同一个 validate（它不读 games，补差按 `spread_games_by_stage(width=slots)` 铺）；
老 spec / 导出计划无该键 ⇒ 逐字不变。顺带修现场日志的 `8006`：`sum(int(v) for v in _per)` 迭代的是 dict **keys**
（2000+2001+2002+2003），真正的和是 36。

**钉住**：`tests/worker/test_plan.py::test_iter_spec_accepts_width_widened_volume_block`（y30 的逐字形状；抹掉宽度
声明必须拒）+ `tests/worker/test_volume_alloc.py` 的加宽正例与三条反例。**否定备选**：把校验放宽成
`games >= alloc`——那会让「手改局数」静默通过，同源就不再是判据。

**残余**：① 加宽让同一 `it` 的 pairs 依赖**本机宽度** ⇒ 换一台不同核数的机器重放同一轮必然对不上（2026-10-22
起如此，非本次引入）；② 铺满 = 过采（配额约 40 局却铺 94 局，已开跑的那一波停不下来），是「一开始铺满所有
worker」的既定代价。诊断全文 → `reports/y30-autoworker-widen-protocolerror-20261022.md`。

---
## §39 A 层评估语料改「固定池外池」：轮换退场，it 之间/课程之间逐点可比（2026-10-22；DECISIONS §2026-10-22-fixed-offpool-eval-seeds）

**触发**（用户 2026-10-22）：「in-loop eval 改为固定池外种子（选个很大的随机数？避免和 rollout 种子有交集），
不需要随机轮换，让使用相同 level 的不同课程也可以比较各轮 it 之间的胜率、击杀、耗时等指标。」

**旧形态（双轨）**：A 层日常评估 = **锚点 50**（固定种子，跨轮配对可比）+ **轮转 50**（每 3 轮换一段，
防记忆化）；summary 另落 `anchor_wr` / `rotor_wr` / `overfit_gap_pp`，门按 `anchor_wr` 判，
网页多一列「过拟合 gap」，还有一条「轮转退化」告警（`--eval-every 3` 时轮转轨形同固定段）。

**为什么它挡了用户要的那件事**：轮转段**每轮换一批局** ⇒ 不同课程（哪怕同 level）各轮 it 的胜率/击杀/耗时
**不在同一批局上**，差异里混着卷面难度 ⇒ 逐点不可比。要「跨课程、跨 it 比较」，语料必须固定。

**新形态（固定池外池）**

1. **池在训练空间之外**：`worker/eval_track.py::EVAL_SEEDS_BASE = 2_000_000_001`，200 个种子。
   取值条件 `2**30 < base < 2**31`——rollout 的种子是 `biz/course.build_pairs` 从
   `rng.integers(1, 2**30)` 抽的 ⇒ 旧池（860001 起）**可能**与训练局撞同一 `(stage, seed)`（读数不独立），
   新池与训练种子**永不相交**；上界 2**31 是给 32 位有符号假设留的安全带（仿真侧 RNG = `seed >>> 0` 的
   mulberry32，硬上界 2**32）。**不是随机大数**：随机基址会让「同一批语料」事后无从核对。
2. **池不含 it**：`a_eval_seed_list(it, n, baseline=…)` = `EVAL_SEEDS[:n]`（`it` / `baseline` 两个参数只为
   兼容调用面保留）⇒ 同一关每轮评**同一批** `(stage, seed)`；it 之间、**同 level 的不同课程之间**的胜率/击杀/
   耗时逐点可比。它是 in-loop / evalA / 云侧（`remote/offline_eval.py`）共用的**唯一**语料入口。
3. **成本与精度一字不变**：课程写 `eval_games_per_stage: 50` 是双轨时代「锚点那一半」的配置 ⇒ 现在翻成
   日常档 **100** 局/关（双轨时代是锚点 50 + 轮转 50 = 100，总量持平）。其它值（120/200/冒烟个位数）照配置
   切片，不重映射。启动期「判决力够不够亮」的告警要按**实际**局数算 SE ⇒
   `worker/loop_guards_gate.py` 用 `a_eval_seed_list` 翻译一次（不翻译就拿 50 局算 SE，报假警）。
4. **双轨整族退场**：`anchor_wr` / `rotor_wr` / `overfit_gap_pp` 三字段、`_maybe_warn_overfit` 过拟合报警、
   `rotor_offset` / `rotor_span` / `dual_track_seeds` / `is_anchor_seed` / `is_rotor_seed` /
   `split_anchor_rotor` / `should_dual_track`、网页的「过拟合」列、`gate_check` 的「轮转退化」告警——
   全部删掉。门（`worker/gate_inputs.py`）改读 `winRate`：固定池 ⇒ 这个读数本身就是逐点可比的。
5. **B/C 层（梯段批 / 判据批）语料不动**：`trainer/batch_plan.py::EVAL_SEED0 = 860001` 有单测钉死，
   那层有自己的历史读数与段语义 ⇒ 迁移等于另起一次实验，超出本次口径。两层因此分属**两个种子空间**
   （A 层 `eval2g` / B/C 段 0 `eval860k`，dashboard `store.ts::seedSpaceOf`）⇒ 换池前后的 A 层读数
   不会被当成同一批局相减。

**代价（诚实账）**：锚点 vs 轮转那个**过拟合探测器**随双轨退场（它测「熟题分高、生题分低」；池外固定池下
没有第二批局可比）。留下的是**选择噪声**：在同一批 100 局上反复挑 ckpt ⇒ winner's curse；靠 100 局
（SE≈5pp）+ 配对比较 + 逐轮同语料来控。另外**换池是一次性台阶**：迁池前后的 A 层读数不在同一批局上，
不可直接相减（门按 `course_fp` 另有一层隔离）。

**落点**：`nn-training/worker/eval_track.py`（池 + `a_eval_seed_list` + summary 结算）·
`nn-training/worker/eval_local.py`（门面同步删名）· `nn-training/worker/gate_inputs.py`（门读 `winRate`）·
`nn-training/worker/gate_check.py`（删轮转退化告警）· `nn-training/worker/loop_guards_gate.py`（局数翻译）·
`nn-training/trainer/eval_dispatch.py` · `nn-training/trainer/eval_a_once.py` ·
`nn-training/remote/offline_eval.py` · `nn-training/worker/eval_ingest.py`（段/空间镜像）·
`dashboard/src/evalboard/store.ts`（`A_EVAL_SEED0` + `seedSpaceOf`）· `dashboard/src/evalboard/ingest.ts` ·
`dashboard/src/server/iters.ts` + `dashboard/src/web/view/*`（删「过拟合」列）· `tools/sim/teacher-probe.ts`
（教师探针跟着换池，否则教师与学生的语料不再同批）· `tools/diag/rl-hourly-inspect.ts`（删锚/轮读数）。
**用例**：`nn-training/tests/worker/test_eval_seed_pool.py`（新，池外性 / it 不变性 / 50→100 翻译 /
续跑去重 / summary 无双轨字段）、`test_eval_track_split.py`、`test_gate_check.py`、
`test_gate_inputs_split.py`、`tests/remote/test_offline_eval_cloud.py`、
`dashboard/tests/evalboard-{store,ingest,corpora}.test.ts`、`tests/teacher-probe.test.ts`。

---
## §38 同族课程产物直取：并行课不再把同一局各算一遍（2026-10-22；DECISIONS §2026-10-22-sibling-episode-reuse）

**触发**（用户 2026-10-22，两段口径）：现在经常同时开多条并行课（y30 四腿），用相同起始权重。
① 起点权重与 level 相同的课，**it0 评估**局局相同 ⇒ 可以复用。② 如果这些课的
`paired_rotate_seed` 也相同，那么**同一次迭代抽到的 rollout** (stage,seed) 也相同 ⇒ 语料同样可复用。
③ 并行课**延迟开课**时前面的语料可能已被 `keep_iters` 清空 —— 找不到就自己重新采。
④ **不做缓存**（缓存会让内存/硬盘爆炸）。

> ⚠ 复用轴是**跨课程**，不是跨迭代。曾误读成「it0 eval ≡ it1 rollout」——两侧种子池不同、
> 贪心/采样不同，那种互相当账是伪造读数（`plan/episode-reuse.plan.md` §5 R5 仍守着）。

**机制**（`worker/sibling_reuse.py`）：复用面只限**盘上已经有的东西**，零新增存储。

1. **找出可复用的课程** = `episode_identity_fp` 相同的课（`biz/corpus_fp.episode_identity_fp` =
   语料身份减去 `reward`；与 `corpus_identity_fp` 共用唯一构造点 `_identity_payload`，现有指纹
   逐字节不变）。覆盖 level/关卡/难度/max_ticks/玩家配置/决策粒度；`reward` 刻意不在其中
   （per-tick 奖励由训练侧读 `metrics.npy` 现算，同一局的 obs/action/metrics 与奖励公式无关，
   换课重算奖励是**精确**的）。排除自己；上限 `MAX_SOURCES=100` 门课（扫不全会白算；命中才读 manifest）。
2. **到它们的工作目录里找这一局**（课程文件里的 `traj`）：
   · rollout ⇒ `**/rl_s{stage}_seed{seed}/manifest.json` 且 `wver` 与本次**逐字节同**
     （`paired_rotate_seed` 不必单独比：种子流不同就根本撞不上同一 (stage,seed)），
     且同波有 `_rl_report.json`、有 `metrics.npy` 且**无** `reward.npy`；
   · eval ⇒ 兄弟课 `eval_log.jsonl` 里 `(wver16, stage, seed)` 相同的 `event:"eval"` 行
     （带 `source` 的 B/C 批行不取：那是另一套 harness）。
3. **找到就取、找不到就自采**（不降级、不伪造）。rollout 命中把那一局的 shard 拷进本课
   `w{idx}/rl_s*_seed*` 并 **D14 改戳成本课**（`course_fp`/`corpus_fp`，事实上只差 `reward` 一项）、
   写 `reused.from` 出处；eval 命中把那一行**移植**进本课账本（只换 iter/节点/时间/来源，
   读数逐字段保留）。
4. **接缝按「派给谁」分三条**：① `trainer/dispatch` 在**派发前**过滤（`reuse_rollouts`）——命中局
   不出队则集群轮会被照样派给远端重算，因此这一条与 `local_slots` 无关：拷进来的 shard 由
   `resumed_manifests` 收进本轮聚合（目录名 `rw{i}`，避开本机槽正在写的 `w{i}`）；② 本机槽
   （`run_local_rollout`/`run_rollout.run_one`）用轮内索引 `reuse_index`（同 `wver` 只建一次，
   不落盘）；③ eval 派发前整轮移植（`reuse_eval_round`/`land_reused_eval`）。

**红线**：`reward.npy` 载体（奖励是别的课算的）不借；`--replay` 的 rollout 不借；
**起始分布课不借 rollout**（切点随 `(rotate_seed, it, stage, seed)` 变，跨课借 = 换了起跑状态）；
`node=reuse` 的评估行不进采样机健康账（它不是机器），但会出现在 summary 的 per-node 表里。

**只替代计算、不替代账**：shard 照常落在本课 traj（`completed_pairs`/PPO 装载/断点续跑看到的
形态与真跑同构），评估行照常落本课 `eval_log.jsonl` 并进 `landed` 收工口径；命中局的
`elapsedSec` 只算拷贝墙钟（源局耗时记在 `reusedSourceElapsedSec`），机器健康与有效并行度只数真算的时间。

**开关**：`--reuse-siblings 0`（或 env `NN_REUSE_SIBLINGS=0`）= 关，逐字节旧行为；测试的全局隔离
夹具把它钉成 0（否则单测会读出操作员在跑的课）。

**未做（不写成已做）**：跨机器取产物（节点集群需要 hub 侧「哪台机器有这份 shard」的索引）；
命中率的绝对读数（真开课一轮才出得来，读 `[reuse]` 日志行与账本 `reusedFrom`）。

**回归**：`tests/worker/test_sibling_reuse.py`（兄弟筛选/wver 判据/奖励载体拒收/D14 改戳/行移植/
开关/起始分布/无课程不猜/派发前过滤，13 例）· `tests/trainer/test_rollout_dispatch_resilience.py`
（命中局不派发、聚合仍覆盖整轮）· `tests/trainer/test_eval_dispatch_resilience.py`（eval 借行不派发、
读数逐字段移植）· `tests/helpers/remote_dag.py` 登记新模块（**L4**，D14 穿模要拿 L3 的 `worker.cmd`）。

## §37 多课程排程改成「按课分批 + 采集优先」：A 的 rollout 不再和 B 的 eval 交错（2026-10-22，用户口径；DECISIONS §2026-10-22-course-batched-rollout-eval）

**旧行为**：`Supervisor._pick` 是 R2c 定案的**步级公平轮转**（从上次选中的下一门课开始找第一个
可跑的）。单进程多课程下 13 步队列因此逐步跨课交错 —— A 的 `rollout` 窗口里插进 B 的
`eval_dispatch` / `eval_join`，反之亦然（eval 自身还跑在后台线程/云机上，观感就是
「rollout 与 eval 全混在一起」）。

**新行为**（`worker/loop_scheduler.Supervisor` + `worker/loop_tasks`）：

1. **按课分批**：选中一门课后把它**本 it** 的任务连续跑完再换课（`_last_course` 粘住；
   边界 = `_finish_round`（该 it 收尾）/ `WAIT` / 资源票被挡 / `ABORT`）。轮粒度下顺序不变
   （a1,b1,a2,b2）。
2. **采集恒高于 eval**：任务族表 `KIND_FAMILY`（`rollout`/`volume_topup` = `collect`，
   `eval_dispatch`/`eval_join` = `eval`，其余 `step`）是**唯一来源**；**没在分批时**挑课的族序 =
   collect → step → eval，族内仍按轮转序（末位课程不饿死）。
3. **分批的唯一例外**：本课排在 eval 步、而别的课有 collect 可跑 ⇒ 本课让位（「始终」的落点）；
   **eval 也只让位给 collect**，不让位给 `step`（否则别课的 PPO/落账会插进本课中途）。
4. **被资源池挡住的课在 `_pick` 里直接跳过**（旧写法是选中它、由 `step` 返回 None、再清空
   挡住集合）：分批后粘住的课会一直是首选，不跳过就变成两拍才给别的课一次机会（饿死）。

**代价（诚实账）**：轮粒度 + 显式 `cache_courses=1` 两门课时「轮间驱逐」现在真会发生
（两课不再同时处于轮内）；上限默认 = 开课数，生产不受影响。原 P1 用例（轮内不抽栈）改成
「a 在 WAIT 的轮内、b 建栈 ⇒ 宁可超预算也不驱逐」——与 2026-10-02 事故的判据同义。

**落点/回归**：`worker/loop_scheduler.py` · `worker/loop_tasks.py` ·
`tests/worker/test_loop_scheduler.py`（原「步级公平轮转」断言 → 分批；+批次边界 +采集优先）·
`tests/worker/test_loop_tasks.py`（族表）· `e2e/test_loop_supervisor_integration.py`（两条跨课顺序
断言）· `tests/trainer/test_serve_wiring.py`（P1 场景）。

---
## §36 PPO 步耗是 **GPU kernel 时间**，不是 CPU/同步：三路「省主机」全部判死（2026-10-21）

**读数**（Colab T4，`PPO_PROFILE=1 PPO_PROFILE_STEPS=5`，24 chunks × 4 epochs = 96 步）：

- `step wall median=488.1ms p95=539.1ms`；**`Self CUDA time total = 2.341s / 5 步 = 468ms/步`** ⇒
  墙钟的 **96% 是设备 kernel 在跑**（每步只有 ~20ms 间隙）。
- `Self CPU time total = 3.460s / 5 步 = 692ms/步` **不是「主机在算」**：那是 CUDA API 排队阻塞
  （GPU 反压）的时间，不能按它判 `CPU ≫ CUDA`。
- 设备侧大头（占 CUDA 时间）：`conv_depthwise*` 反向三项 **1.11s = 47%** · `convolution_backward` 1.26s ·
  `threshold_backward` 183ms · `add_`/`clamp_min`/elementwise 各 5–8% · 1×1 的
  `scudnn_128x64_splitK` 289ms（12%）。

**因此判死的三条**（以后不要再提）：

1. **threads（1→2）**：只影响主机算子；关键路径是设备 kernel，提到 2 也不会有感知。
2. **CUDA graph**（plan §4.6）：CPU≫CUDA 的前提不成立；每步只有 ~20ms 可抢。
3. **同卡多 worker 吃聚合**：GPU 已 96% 忙，第二个进程只是把同一批 kernel 切开，聚合吞吐不涨
   （这点推翻了同日早先「利用率个位数 ⇒ 并发 ≈ N×」的猜测——`6% of peak` 是**效率低**不是**卡闲着**）。

**仍未判死的**：fp16（T4 有 tensor core，plan §4.7 已列「单独立项」；要接受导出 fp32 / 训练 fp16 的双轨）·
模型结构（5×5 depthwise 占设备时间近一半，换 3×3 省 ~64% 那块数学，但要重训 + TS 双胞胎同步改）。
**§4.7 的一个前提被推翻**：`cudnn.benchmark` 当时以「kernel 效率不是瓶颈」否决——本次读数说明
正是瓶颈（算法选择层面的 A/B 值得做，但换算法 ⇒ 数值可能漂，红线仍是 TS 逐字节）。

**复读法**：`PPO_PROFILE=1 PPO_CUDA_DIAG=1` 跑一单；`n` 会少 `PPO_PROFILE_STEPS`（采样步不进 median）。

### 原始输出（未改写，一手数据）

来源：Colab T4（`colab-t`），job `057252a24d56a2e8`，2026-10-21 13:54，进程代码 `sha12=e1cc0356e813`，
`PPO_PROFILE=1`，采样 5 步（`PPO_PROFILE_STEPS=5`；缺省 10 步，钩 1–50）。表头/列名是 torch 原样，
`aten::` 行的时间**包含**其 kernel（两族不能相加）。

```text
[13:54:10] job 057252a24d56a2e8: 准备完成 7.2s｜...｜设备=cuda｜n=1｜[0]=Tesla T4｜cap=(7, 5)｜dp=False｜threads=1｜...
[13:54:15] [ppo] profile（前 5 步，按 self CUDA 时间降序；CPU / CUDA 两列同表）:
-------------------------------------------------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------
                                                   Name    Self CPU %      Self CPU   CPU total %     CPU total  CPU time avg     Self CUDA   Self CUDA %    CUDA total  CUDA time avg    # of Calls
-------------------------------------------------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------
                             aten::convolution_backward         0.22%       7.670ms        10.12%     350.288ms       4.121ms        1.260s        53.83%        1.328s      15.619ms            85
void at::native::(anonymous namespace)::conv_depthwi...         0.00%       0.000us         0.00%       0.000us       0.000us     505.883ms        21.61%     505.883ms      12.647ms            40
void at::native::(anonymous namespace)::conv_depthwi...         0.00%       0.000us         0.00%       0.000us       0.000us     327.643ms        14.00%     327.643ms       8.191ms            40
_5x_cudnn_volta_scudnn_128x64_stridedB_splitK_medium...         0.00%       0.000us         0.00%       0.000us       0.000us     288.631ms        12.33%     288.631ms       7.216ms            40
                                aten::_conv_depthwise2d         0.09%       3.157ms         0.17%       5.788ms     144.691us     278.554ms        11.90%     288.908ms       7.223ms            40
void at::native::(anonymous namespace)::conv_depthwi...         0.00%       0.000us         0.00%       0.000us       0.000us     278.554ms        11.90%     278.554ms       6.964ms            40
                               aten::threshold_backward         0.25%       8.680ms         0.35%      11.997ms     133.295us     183.160ms         7.82%     183.160ms       2.035ms            90
void at::native::vectorized_elementwise_kernel<4, at...         0.00%       0.000us         0.00%       0.000us       0.000us     183.127ms         7.82%     183.127ms       2.154ms            85
void at::native::vectorized_elementwise_kernel<4, at...         0.00%       0.000us         0.00%       0.000us       0.000us     172.837ms         7.38%     172.837ms     751.466us           230
                                             aten::add_         0.34%      11.758ms         0.43%      14.983ms      41.050us     153.524ms         6.56%     155.193ms     425.186us           365
void at::native::vectorized_elementwise_kernel<4, at...         0.00%       0.000us         0.00%       0.000us       0.000us     126.735ms         5.41%     126.735ms       1.267ms           100
                                        aten::clamp_min         0.52%      17.888ms         0.66%      22.982ms     255.360us     126.711ms         5.41%     128.189ms       1.424ms            90
                                aten::cudnn_convolution         4.49%     155.194ms         5.57%     192.806ms       4.285ms     112.062ms         4.79%     263.431ms       5.854ms            45
                                  Lazy Function Loading         0.12%       4.085ms         0.12%       4.085ms      39.279us     108.085ms         4.62%     108.085ms       1.039ms           104
                                  volta_sgemm_128x64_nn         0.00%       0.000us         0.00%       0.000us       0.000us      90.865ms         3.88%      90.865ms       2.019ms            45
-------------------------------------------------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------  ------------
Self CPU time total: 3.460s
Self CUDA time total: 2.341s

[13:55:00] [ppo] step wall median=488.1ms p95=539.1ms n=89（已剔前 2 步 warmup，总 91 步）
[13:55:00] job 057252a24d56a2e8: PPO done in 49.9s｜进度=epoch 4/4 done (49s total, 24 chunks)｜...
```

附注（当次同时出现，不影响读数）：`torch/profiler/profiler.py:224: UserWarning: Profiler clears events at
the end of each cycle.` —— 单次 `profile()` 上下文只有一个 cycle，表就是这 5 步的全量；
`Lazy Function Loading 108ms` 是 cuDNN 首次懒加载的一次性开销（5 步样本里约占 2%，看 10 步更干净）。

---
## §35 y26 线切腿 A（`policyExtra=true`）：POLICY_EXTRA(57) 第一次真正进网络（2026-10-20）

**起因**：`experiments.md §119` 查明 y26 六腿模型全是 `policyExtra=false` 档 —— `move_head` 宽 128、
`src/nn/infer.ts:510-515` 据此把 `extraDim` 定为 0、`forward` 的第三参 `extra` 全程不被读；
Python 侧 `PPOStudent.policy_features` 同判据。⇒ B2–B4 新增 26 个观测位里的 **20 个（整个 extra 增量：
leadHitD×4 / threat 分族 / enemyFacing / lastHitDir / prevAction 方向 one-hot）写进了每个 shard 却从未进策略**。

**决定**：切 **腿 A**（`policyExtra=true`，头输入 185 = hidden 128 + extra 57），不把那些位搬进 scalar。
理由：`extra.npy` 已经是 57 维且每个 shard 都有（**语料零重导**）；搬进 scalar 要 `SCALAR_DIM 46→66`
= MAJOR 7 + obs-encoder/schema/指纹/全语料重导，代价大一个量级。

**落地（四处）**：
1. `worker/scripts/init_spatial_leg.py` 新增族 **legacy-v6 → 腿 A v6**：`_is_v6_source()` 按**张量形状**
   （move4/fire3/hold2 + hold_head 在）识别，走 `_MIGRATION_V6_HEAD_TO_A`（`drop0`：extra 57 列**全 0**），
   并**跳过 `_migrate_action_heads`**（它为 5/2 旧源写：4 行源会被判非法 ⇒ 整头随机 init，且
   `old_move_w[0]`（v6 的 up 行）会被当 STOP 拿去重建 hold ⇒ 三层静默错位）。legacy **5/2** 源仍拒绝。
2. 起点 `nn-training/weights/y26-v6x/y26-v6x.it0.json`（`arch` = `policyExtra:true` + `actionV6:true`，
   头 185/185/185/185，`warmstart_missing: []`）。
3. 课程 `nn-training/curricula/y27-clear.jsonc`（相对 `y26-clear` 只换 `bc`；奖励逐字）。
4. 测试：`tests/worker/test_init_spatial_leg.py::test_leg_a_from_v6_source_pads_zero_columns_and_is_bit_identical`
   + `tests/fixtures/student-golden-v6x.json`（h=16/d=2，policyExtra+actionV6）+ `tests/nn/student-infer.test.ts` 新用例。

**等价性（为什么不是“换了起点”的污染）**：extra 57 列补 0 ⇒ it0 行为与 `y26-v6.it0` 等价，实测：
① torch 四头 **max|Δ| ≤ 1e-4 且 argmax 相同**（185 vs 128 宽累加次序不同，末位 ~2e-5，逐位相等做不到）；
② 真局 `export-eval-game --stage 7 --seed 860001`：两版 **89 项读数除 `weightsSha` 外逐字相同**；
③ `extra=0.5` 时 `move_head/hold_head.weight.grad[:,128:]` 非零 ⇒ 57 维**第一步就能学**；
④ 训练侧 `build_ppo(y26-v6x.it0)` ⇒ `policy_extra=True/action_v6=True/policy_input_dim=185`、严格装载
   `missing=[]`；rollout 实跑一局 ⇒ shard 带 `extra(114,57)`、`mask(114,9)`、`a_hold`。

**⚠ 两条别搞混的读数**：
· “fireBefore 永不出现”是 **y26 已训练策略在 ladder 竞技场**的事实（0/54 611，§119）；同一份**起点权重**
  在 stage 7 上实测 `a_fire=1` 占 **5.3%**（114 步）⇒ 该位不是硬掩码，只是竞技场里拿不到正梯度。
· 腿 A 只改“extra 能不能进网络”，**不改 Φ**：§119 的“奖励给一手普通弹定价 −0.10 分/过关价 ≈0”那个病仍在，
  所以本腿不承诺 win 上涨；`y27-clear` 的判据写成**同轮配对 vs `y26-clear`**（而不是绝对 0.715 ——
  那是 v5 的单点峰值）。

**后续（2026-10-20 用户拍板后的定稿：四条课 = `DECISIONS §2026-10-20-goalnn-y27-triad`）**：
`y27-clear`（本腿基线）+ 三条能力腿 `y27-shot`（`wHit 0.2→0.8`，ΔΦ +30.3%）/
`y27-lead`（`wDist 0.6→6.0`，+23.7%，= y26-dist 在 extra 活档上的可证伪重开）/
`y27-window`（B6：`+wWinHit*hitsInFreezeShieldWindow`，`wWinHit 3.0`，+17.7%）——同起点 / 同 seed / 同预算。
**不做**（用户拍板）：BC 蒸馏三头（三头是 RL 可学的；蒸馏原本只想收回迁移丢的 11pp hitRate，且 demo 的 fire 标签本身
只有 1.84% 可训练）/ `metrics v13` 两列（改用脚本读 shard：`node tmp/agg-shards.cjs <dist 目录>`）/ extra 消融（暂缓，
后果见 §119 的“能证/不能证”边界）。

---
## §34 入口分家（S3）：`run_rl.py` 收成 CLI 壳 + 一次性腿独立成模块（2026-10-17，plan/single-resident-trainer §3）

**现场**：`trainer/run_rl.py` 一个人背四种角色 —— 常驻训练循环 / 冒烟预演 / 导出快照 / 预采子进程。
S1（准入闸）+ S2（心跳判死）把口子堵住之后，**四种角色还挤在一个文件里**：读代码的人要在一屏
`main()` 里分清「哪几行只在冒烟时跑、哪几行只在导出时跑、哪几行是常驻才有的」，而锁正是被这种
混杂摸错的（2026-09-06 双 trainer 写同一 traj）。

**目标形态（落地后）**：

```
trainer/run_rl_cluster.py --serve    常驻调度器（唯一常驻写者）
trainer/run_rl.py                    CLI 壳：解析 → --echo-config dump / 分流 / 拒启
trainer/once_shot.py                 一次性腿的唯一实现：冒烟 / 导出 / 预采
trainer/collect_only.py              预采子进程的执行体（入口仍是 run_rl.py）
```

**四条腿各自的契约**（搬进 `once_shot.py` 时**逐条保持**，顺手改任何一条都是行为变更）：

| 腿 | 取 `.run_rl.<课>.lock` | 过开课闸 | 说明 |
|---|---|---|---|
| `--smoke` | **取** | 不过 | 真跑一轮（collect → publish → echo → 作废）；作废点全在收官/权重层，**没有**「零写 traj」的判据 ⇒ 与 serve 抢 traj 的面还在（§3.1 选项 A） |
| `--export-bundle` | **不取** | **过** | 只读快照，与 serve 并行是设计目标；「停课的课程不能导出」这条行为此前由常驻分支顺带守着，迁移时**唯一不能丢**的那条（§3.3） |
| `--collect-only` | 不取 | 不过 | 预采子进程；只带 `--course` + `--collect-only-config`（P0-3 的动作覆盖通道） |
| `--echo-config` | — | — | 不是腿：它是**解析面**的 dump（对拍 oracle 读 stdout），留在壳里、在分流之前短路 |

锁策略提成**纯判据** `once_shot.takes_course_lock(args)`（可单测），而不是埋在分支里。

**逃生舱退役（用户裁决，2026-10-17）**：S1 曾给 `--allow-detached-trainer` 一条运维旁路（放行 +
醒目告警 + 自报 `tmp/trainer-detached.json` ⇒ 控制台 `warn` 横幅）。S3 把常驻训练分支从 `run_rl.py`
删掉之后，那条旁路**无处可去**（横幅数据源会永远为空）⇒ 旗标 / 自报文件 / 服务端读面
（`server/api/detached-trainers.ts`）/ 告警坞条目四件一起删。S1 的判据随之从「拦住旁路」变成
「没有旁路可走」：**常驻训练的唯一入口 = `run_rl_cluster.py --serve`**（`--force` 一样过不了，
它是锁抢先）。

**验收（都是结构判据，不是「某个词没出现」）**：

- `tests/trainer/test_single_resident_trainer.py` 用 **AST** 钉住：`run_rl.py` 的调用/import 集合里
  没有 `run_training`（`trainer.loop` 也不许 import），而 `once_shot.py` 里**必须有**（冒烟真跑一轮）；
- 真入口分流：`--smoke` / `--export-bundle` / `--collect-only` 都带同一个 `course_key` 进 `once_shot`
  （打桩 `run_once_shot` 记录），`--echo-config` 在分流前短路；
- 锁策略：导出 ⇒ 不取锁、冒烟 ⇒ 取锁（参数化用例）；
- 预采子进程真跑一遍（`tests/trainer/test_precollect_child.py`，子进程吃 `--collect-only-config`）。

**为什么不给 `once_shot.py` 一个 `__main__`**：那会变成**第二份 argparse**（mode/rl-config/课程
覆盖全要再写一遍）——两份解析面必然 drift，而预采子进程的命名空间重建正是靠「只有一份解析面」。
入口仍然是 `run_rl.py`（`collect_only.RUN_RL_ENTRY` 钉着它）。

**落地**：`nn-training/trainer/once_shot.py`（新）· `run_rl.py`（壳）· `worker/trainer_gate.py`
（trim）· dashboard 侧删 `detachedTrainers` 读面与横幅 · 文档（`agents.details.md` §5 /
`goal-nn-next.md` A5 配方 / `console.md` §40 该段加退役注记）。

---
## §33 `biz/ladder_factory.py` 退役：它只是刚删掉的 ladder 模板库的生成器（2026-10-13）

**用户提问（2026-10-13）**：`biz/ladder_factory.py` 干啥用的？只为生成 level 文件的话，也删掉吧。
**核查结论：是，删。** 它是 I3「阶梯工厂（数据不是代码，§2.4）」的**一次性生成器**，产物恰好就是
§32 刚删掉的那三族：

| 产物 | 现状 / 性质 |
|---|---|
| `levels/ladder-cNN.jsonc`（20） | §32 已删 13、留 7（别的课在用） |
| `curricula/ladder-cNN.jsonc`（20） | §32 已全删 |
| `ladder/plan.jsonc`（I5 LEDGER 初稿） | 它是工厂的**输出**（`generate(plan_path=…) → write_jsonc`），**不是输入** |

**输入侧只有一样**：`levels/arena4.jsonc`（几何源 `DEFAULT_ARENA`）+ 一堆钉死的常量
（`max_ticks = 600×count + 900`、`wChip = 4.5/承伤基数`、lives tier、腿矩阵）。
**消费者只有一个**：它自己的测试 `tests/biz/test_ladder_factory.py`；`worker/kickstart_burn.py:13`
只在 docstring 里引它的 I2 结论，不构成依赖。

**决定**：删 `biz/ladder_factory.py`（447 行）+ `tests/biz/test_ladder_factory.py`（269 行）。
其中**唯一不依赖工厂的那条判据迁走**：`test_arena_retired_single_source`（arena2/arena3 不许复活、
arena2-acbc 必须在）→ `tests/biz/test_course_resolve_split.py:213`（同族主题 = 关卡解析面的盘面事实；
也避免一条**已登记的契约**静默消失 —— `docs/nn/test-contract-map.md` 把它登记为 T2b）。

**★ 更正 §32 的一处事实错误（方向写反了）**：§32 原文写「工厂**从** `ladder/plan.jsonc` 生成 …」
—— 反了，`plan.jsonc` 是工厂的**输出**。于是 §32 那条"会重犯的坑"（跑一次 generate 静默复活 20 门死课）
**随工厂一起消失**：这条路径不再存在。

**`ladder/plan.jsonc` 一并删（用户 2026-10-13 拍板）**：工厂的第三个产物，删工厂后即成**孤儿**
（全仓 grep：无写入者；读取者 0 —— dashboard / `tools/sim/*` / `biz/ladder_ledger.py` 读的都是同目录的
**`LEDGER.jsonc`**，那是另一个活文件，保留）。且它的内容列着 20 个已不存在的关卡 ⇒ 留着是误导。
删后 `nn-training/ladder/` 只剩 `LEDGER.jsonc` + `reports/teacher-probe.json`（后者由
`tools/sim/teacher-probe.ts` 写）。

**顺带清掉的引用**：`biz/__init__.py` 的模块表那行 · `nn-training/README.md` 的 `biz/` 树
（「本包 12 个模块」→ **11**）· `docs/nn/test-contract-map.md` 的测试路径。
**故意不动的**：`curricula/x2|x3|x4|x5|x6-rebirth.jsonc` 与 `x20-snowball.jsonc` 注释里引
`biz/ladder_factory.py:228` 当"先例" —— **课程文件字节 = `course_fp`**，改一个注释字就换指纹，
正在跑的腿（x28-d01 / x28-w08）要按 D14 对账 ⇒ 一律不碰（死引用留档）。`worker/kickstart_burn.py:13`
的 docstring 同理（它只记 I2 结论的出处，判据不依赖该文件存在）。

**踩到的两条护栏（诚实记一笔）**：

1. `tests/test_layering.py:428` 的 `len(biz) + len(worker) >= 66` 是**下界**：实测改前 73、改后 72，
   仍过 ⇒ 无需改（那个数早就松了；顺带说明它并不像 docstring 写的那样"钉死 64"）。
2. `tests/test_source_text_assert_budget.py` **红了**（`1092 > 1091`）。真因**不是**本次删除，而是
   **上一批 code.zip 裁剪**那轮我在 `test_hub_client_code_zip.py` 里新加的两条断言 —— 口径 B 的
   `.py` 门把「**zip 成员名**里的 `*.py` 路径」误判成源码文本断言（该护栏 docstring 自己承认这道门
   是文本级的、会误伤路径字符串）。修法**不是抬基线**（护栏明令「不许直接改数字」）：收件名单经
   `_expected_members()` 转一手、被裁目录改用**不含 `.py`** 的 `TRIM_TOPS` 目录集 ⇒ 该文件 B 由 2 降 0。
   复测全仓 **A/B = 1437/1090**（双基线 1445/1091 之内；删掉 `test_ladder_factory.py` 的 8 条 A
   也把水位顺带降了一格）。

**验证**：`tests/test_layering.py` · `tests/test_source_text_assert_budget.py` · `tests/biz` ·
`tests/tools` · `tests/worker/test_course_level.py` · `tests/trainer/test_verdict_corpus.py` ·
`tests/remote/test_hub_client_code_zip.py` · `tests/remote/test_code_snapshot.py`
= **388 passed / 6 skipped**（skip 全是 `test_forkdist` 的 Windows 分支）。

---
## §32 ladder 模板库裁剪：删掉「只有 ladder 自己引用」的 20 门课 + 13 个关卡（2026-10-13）

**用户指令（2026-10-13）**：`curricula/` 下 `ladder-cNN.jsonc` 全部移除；它们引用的 `levels/*.jsonc`
**如果没被其它课程引用**，一并移除。（本节的判据就是这句话的字面实现。）

**判据 = 只按课程引用**（`curricula/*.jsonc` 的 `"level"` 字段）。顺序很重要：**先删课，再重算关卡的存活引用**
（否则 c01–c07 会被自己那门课误判成"还有人用"）。脚本一次性算出两批（`tmp/ladder_trim_plan.py`）。

**删（33 个文件）**：

| 批次 | 内容 | 理由 |
|---|---|---|
| 课程 20 个 | `curricula/ladder-c01..c20.jsonc` | 用户点名全删 |
| 关卡 13 个 | `levels/ladder-c08..c20.jsonc` | 删课后引用数归 **0**（c01–c07 各有 3/3/11/20/6/1/1 门别的课在用） |

**留（不被本次波及）**：`levels/ladder-c01..c07`（上表右列）· `levels/ladder-c20-lives1.jsonc`
（**66 门**课在用 —— 现行 x20 全线）· `levels/ladder-c20-lives1-powered.jsonc`（`x20-powered`）·
`levels/arena2-acbc / arena4 / arena6`（`x2-acbc` / 3 门 BC / `c6-dmgfix`）。

**★ 会重犯的坑（2026-10-13 **更正 + 已随工厂退役消化**，见 §33）**：~~`biz/ladder_factory.py` 从
`ladder/plan.jsonc`（仍在，且仍列着 20 级）生成 `levels/ladder-cNN.jsonc` 和 `curricula/ladder-cNN.jsonc`，
跑一次 `lf.generate()` 就把这 20 门"死课"复活~~ —— **方向写反了**：`ladder/plan.jsonc` 是工厂的**输出**，
不是输入；而该工厂本身已在同一天退役（§33）⇒ 这条"静默复活 20 门死课"的路径**已经不存在**。
（保留原注：工厂测试全程在 `tmp_path` 上自产自销，从不写真仓库。）

**不用动的两处（已核，别顺手清）**：
- `ladder/LEDGER.jsonc` 的 `levels` 键 = `ladder-c01..c07` + `x2` / `x2-acbc` / `x3`（10 个），**没有 c08+**
  ⇒ 与本次删除无关；`biz/ladder_ledger.py` 也不校验键对应的关卡文件是否存在（它只记 teacher 探针与 disk_bytes）。
- `rl-config.json` 的 `courses` 块只有 `x21-psh-k5` / `x28-d01` / `x28-w08`，**无 ladder 课程** ⇒ 无孤儿配置。

**⚠ 一处自我更正（2026-10-13 同日）**：先前本节写「`levels/obstruction.jsonc` 是全仓零引用的关卡，
比 c08–c20 更死」—— **错了，别删**。它**不是**死文件，而是**阶段二「障碍/基地专场」的预备关卡**：
`plan/obs-v5-vector-gap.plan.md:95`「第二阶段障碍/基地专场（G0 过后另起账本）：obstruction（＋基地
tile (6,12)，lives 1）」、`:364`「G0 过 ⇒ 专场（课程 obstruction＋基地，B1–B4）」。现在没有课程引用它
是因为**两阶段纪律**（`DECISIONS.md:4858`：提前开 obstruction 并行腿被用户 veto）—— 判据是
「**计划中的引用**」而不是「今天的引用」。⇒ 教训：判"零引用"时 grep 的搜索面必须含 `plan/` 与
`DECISIONS.md`（本次第一次只按课程 + 代码扫，漏掉了计划面）。

**验证**：`tests/biz/` + `tests/tools/` 全量 **354 passed / 6 skipped**（skip 全是 `test_forkdist`
的 Windows 分支）；另跑课程/关卡相关 12 个文件 **162 passed**（含 `test_reward_golden.py::test_jsonc_courses_load`
—— 它遍历真 `curricula/*.jsonc`，只要求 ≥5 个，删后仍 161 个）。
**恢复**：这些文件都已跟踪 ⇒ `git checkout -- <路径>` 可取回。

---
## §31 决策周期 K 通道：课程 `decision_k` 全链落地（2026-10-07，plan/k5-rhythm）

**病**：决策门 `t % K === 0` 的 K 有单一实现（`src/nn/decision-gate.ts` + 8 处调用点），但**没有通道**——
课程/CLI/计划都传不进去，所有执行面一律吃缺省 K=10。K→5 节奏实验（腿 A"看见了但没转化"的唯一遗留出口）
要两臂唯一差别 = K，缺的正是这条链路。

### 31.1 设计（激活型通道，缺席逐字节不变）
- **课程**：`decision_k: int = 10`（脏值拒课：bool/非 int/<1）。**仅 ≠10 激活**三处：
  `--decision-k`（rollout 命令）· wire `decisionK`（节点任务）· `corpus_identity_fp`（K 变 = 决策粒度变 = 样本身份，
  同 `decision_events` 待遇）。
- **rollout 链**：`worker/cmd.build_rollout_cmd` 追加 `--decision-k`（本机池 / 逐轮上云 argv / 全离线计划同一拼装点）；
  `export-rl-rollout.ts` 收 `--decision-k`（非法响亮拒）→ `createDecisionGateConfig(events, K)` + 快照对齐 + shard manifest
  `k` 记**有效值**。
- **eval 链**：`export-eval-game.ts` 同收 `--decision-k`；本机 `worker/eval_local`、三个派单点
  （`trainer/{dispatch,eval_dispatch,batch_runner}`）、云机 `remote/offline_eval`、终判 `eval-course-ckpt --decision-k`
  （写进 spec，Python 侧 `args.decision_k`）。
- **节点侧**：agent 收 `?decisionK=<n>`（非法 400）→ 同值透传导出器；taskKey 尾缀 `:k<N>`（提交/轮询/透传三处同配方，
  缓存隔离）；ping 报 `decisionKSupport`，训练侧**无位不派**（fail-closed：旧 agent 会静默跑 K=10，混批比不派更坏）。
- **评估预注册**（plan §2）：主判按**各自臂 K**——K5 臂日常 eval 随课程自动 K=5；终判显式 `--decision-k 5`。

### 31.2 量纲与耦合（K≠10 时必须显式处理的派生项）
- `est_samples_per_game = 局均 ticks ÷ K`（K=5 ⇒ ≈2×）；`per_stage_quota` 不变 ⇒ 每轮训练步数不变、
  K5 每轮 distinct games 减半（K 派生，对账白名单；白名单外差异 = 违规）。
- γ/λ 按决策步 ⇒ tick 视野随 K 成比例缩短（K 减半 = 视野减半）；属 K 固有耦合，登记不补偿（改 γ/λ = 另案）。

### 31.3 实测取证（2026-10-07 冒烟，`tmp/k5-smoke/`）
同 seed/stage 出口 shard manifest：`k=5 / nSamples=300` 对 `k=10 / nSamples=150`（2× 决策密度，1500 ticks）；
eval 报告 `decisions` 160 vs 80；坏值 `--decision-k abc` 三端响亮拒（rollout throw / eval exit2 / agent 400）。

### 31.4 顺带修复
`common/distribution._poll_result` 的轮询 URL 此前缺决策门分量（提交键含 `:de1`、轮询键不含）⇒
事件课程的 async 竞速副本必 404；`decisionEvents`/`decisionK` 一并补进（`tests/worker/test_decision_k.py` 钉住）。

### 31.5 落点
课程 schema `biz/course_spec.py` · 指纹 `biz/corpus_fp.py` · 派单/门
`trainer/{batch_plan,dispatch,eval_dispatch,batch_runner,eval_course_once}.py` · 导出器/终判
`tools/sim/{export-rl-rollout,export-eval-game,eval-course-ckpt}.ts` · agent `tools/agent/sampler-agent.ts` ·
单测 `nn-training/tests/worker/test_decision_k.py` + `tests/{nn/decision-gate,agent/decision-events-taskkey,dist-node-gate}.test.ts`。
两课 `nn-training/curricula/x21-psh-{k5,k10}.jsonc`（起点 B0-it290 五份 sha 同源；**开课待人工**）。

---
## §30 分关采样平衡：`est_hi` 初批 + 有界多批补差（2026-10-07，plan/rollout-stage-balance）

**病**：动态采集的**全离线腿**（`kind=run`）一轮只跑一批，局数按**全局** est 反解 ⇒ 每关同一个局数，
而达标线是**分关** `quota`。关间 `samples/局` 实测差 **1.75×**（x21-psh-b it168：关 2002 ≈ 241，关 2000 ≈ 422）
⇒ 同一轮里既浪费（长局关溢出被裁）又缺口（短局关欠采），而节点**没有第二次机会**——一轮一个 job、
PPO 在 job 里跑完。it168 逐关真值：**缺 4405 样本、同时丢 3333**（`tmp/x21-psh-b/it168/per-game.json`）；
按 37 局/关反解可知导出那刻的 est 实际是 **338.6**（计划文本里的 ~332 会解出 38 局）。

### 30.1 规则（`VOLUME_ALLOC_RULE = "per-stage-v3"`）

| 环节 | 口径 | 为什么 |
|---|---|---|
| 首批 | 逐关 `G_s = ceil(quota / est_hi_s)`，`est_hi_s = ceil(est_s × 1.15)` | 上界 ⇒ 欠采概率小；定点化（`_scale`）避免二进制浮点把恰好整数的界抬一档（`20 × 1.15` 必须还是 23） |
| 补差 | **有界多批**（≤3 补差批 = ≤4 次 `run_iter_rollout`）；每批用**已收样本的实测均值**重估 | 单批 + 实测均值 P(有缺口) **71.6%**（与 DoD 互斥）；≤3 批 ⇒ 18.9%、≤4 批 ⇒ **3.0%** |
| 末批 | `est_lo = floor(est × 0.85)` ⇒ 派得**更多** | 缺口不可恢复、浪费可恢复（不对称） |
| 硬顶 | 逐关 `4 × ceil(quota / est_s)`（课程显式 `--max-games-per-stage` 优先） | 旧的全**局** est 口径会让低 est 关「配额未满就触顶」，而 DoD 把触顶当合格路径 ⇒ 静默短采 |
| 记账 | 缺口 = 硬指标（WARN + 轮报可见）；浪费 = 软指标（有上界） | 不静默短采是本节的唯一 KPI |

### 30.2 常量与标定（真机 142 轮 per-game，bootstrap 2×10⁴ 轮）

`DEFAULT_EST_HI_FACTOR = 1.15` · `DEFAULT_TOPUP_MAX_BATCHES = 3` · `DEFAULT_LAST_BATCH_LO_FACTOR = 0.85`。
口径结果：P(本轮有缺口) **91.1% → 3.0%**、P(四关全达标) 8.9% → 97.0%、E[局数/轮] **157.8 → 162.0**（+2.7%）、
E[浪费] ≈ 2056 样本（≈17% 配额，只烧节点时间）、调用数分布 2:28% / 3:49% / 4:23%。
`per-stage est` 的抖动实测 p05/p50/p95 = 0.80/0.99/1.20–1.24 —— **`×1.15` 不是上界**（首批仍有 10.6% 过采），
这正是「按批重估」而非「一次定死」的理由。复算配方：评审 `plan/rollout-stage-balance.review-hy.md` §7。

### 30.3 落点（两条链共用一套数学）

* **纯函数**：`worker/volume_alloc.py`（`est_hi` / `alloc_games_by_stage` / `topup_games_by_stage` /
  `shortfall_by_stage` / `wasted_samples` / `stage_totals` / `default_game_caps` / 同源校验）——无 IO、无 torch。
* **本机链（连续配额）**：`trainer/loop_volume._volume_collect_continuous` 用分关 `game_cap_by_stage`
  （`worker/volume_quota.plan_continuous_batch` additive）⇒ 收官写 `self._volume_stage_stats` ⇒
  `trainer/loop_steps._record_iteration` → `worker/events.write_iteration` 的
  `volume_alloc_rule` / `volume_stage_stats`（additive，旧行 None）。
* **全离线链（`kind=run`）**：`trainer/loop_export._volume_plan_block` 把分关 est（`trailing_stage_samples_per_game,
  window=3, fallback=全局 est`）与分关 G / `topup{}` 钉进计划块（`worker/volume_waves.volume_block`）；
  节点侧 `worker/iter_topup.topup_rollout` 逐批补差，`remote/worker.py` 接线，轮报增 `volumeTopup`。
* **spec 通路**：`worker/plan.iter_spec(volume=)` + `common/manifest` 白名单（只查「是对象」；完整校验在
  `volume_alloc.validate_runtime_volume`，节点跑前必跑）。

### 30.4 与 DoD/身份的关系

分关分配与 est **同类**（预算/运行参数）⇒ 不进 `corpus_identity_fp`、不 bump `VOLUME_RULE`：在训课程
不拒收、不需要改课程文件。代价是 hub 重启后云机腿每关局数会变（§15.5 意义上的断点）⇒ 用**可见化**兜
（上面那两处字段 + `volumeTopup`），而不是拒收。`target_transitions ≤ 0` 的课程一个函数都不调
（`build_pairs` / `initial_wave_pairs` / `pairs_for` 输出逐字节不变，有单测钉住）。

### 30.5 分层维护（为什么多了一个 `worker/rollout_argv.py`）

`retarget_argv` 原先住 `worker/plan.py`（账本 **L4**），2026-10-07 多了一个读者 `worker.iter_topup`（**L2**）
⇒ 上向边。按本仓既定手段「把共同依赖**下沉**」：纯替换逻辑下沉成 L0 叶子 `worker/rollout_argv.py`，
`worker/plan.py` 顶层再引回来（`worker.plan.retarget_argv` 对读者不变）。**不得**改成「补差模块 import `plan`
再靠延迟 import 化解」：延迟边同样要严格向下，而 `remote/worker.py`（L5）顶层 import 补差模块。
账本同时把 `worker.volume_alloc` 记 L1、`worker.volume_waves` L0→**L2**（它现在靠 L1 的分关公式：
**一份公式，不抄第二份**）。

### 30.6 未闭（显式）

* **真机轮次未取**：`kind=run` 的一轮真机读数（轮报 `volumeTopup` + 缺口为 0 或触 cap）——见
  `docs/nn.progress.md` §3.3 未决表。
* 训练侧 `dropped_shards`（`np_core`）**不进** iteration 事件（本次不动：那是加载侧的另一条账）。

## §29 跳过课的复活通道 + 只读 `openable`/`blocked` 判据（2026-10-05，plan/course-startup-recover）

**事故**：`x20-adv3-open-r2` 课程文件自相矛盾（`kickstart_init>0` ∧ `kickstart_ref=false`）⇒ 启动期
SystemExit、整课被共享 trainer 跳过（`report.skipped`）；人改好文件 + 停→开都无效（`fresh` 过滤把
`skipped` 当**终身黑名单**），唯一出路是重启 trainer——而那会打断所有并行课程（事故实证 → plan §6.3）。

### 29.1 P0：跳过 = 待重试表（判据指纹，按来源取事实）

`ServeReport.skipped_at[course] = {kind, fp}`；每拍（既有 discovery 空转拍，**不新增节拍**）纯函数
`reopenable_skipped(skipped_at, traj_root)` 比对当时/此刻的判据：

| 跳过来源 | `kind` | 判据快照 |
|---|---|---|
| `_open_courses` 配置错（课程文件） | `config` | 课程文件身份（`mtime_ns`+`size`）+ 开课标记 mtime |
| 按课程锁被占（`拒绝双开`） | `lock` | 同上 + 锁签名（释放 / 换主 / 持有者死）——失败快、自愈类 |
| `_enqueue_opened` 入队失败 | `enqueue` | 同上 + 账本 mtime（修复对象是账本/轨迹盘） |
| 一步级 SystemExit（`build_executor`） | `step` | 同上；**通道不同**（课仍在 `runtimes`、队列 ABORTED） |

复活动作：`config/lock/enqueue` 类撤销记账后重走 `_open_courses` + `_enqueue_opened`；`step` 类走
**第二条通道** `_revive_aborted`——重置队列（`_enqueue` + 把旧 `rounds_done` 带回）+ **保留
runtime/引擎**。不得重建 runtime：C-0 前科（新 runtime `runner=None` + 池里旧引擎 ⇒ `ensure_ready`
对不上 ⇒ 无限 RETRY）。判据没变 ⇒ 不重试、不刷日志（`:851` 注释原意）；文件从缺到有（`None → 有值`）
也算变；用户「停→开」（标记 mtime 变新）是同一机制里的合法复活信号。

**与 restart-only 正交**：`kickstart_init`/`kickstart_ref` 在 `RESTART_ONLY_FIELDS` 的规矩是给
**已在跑**的课的（衰减曲线一旦排好不得中途换口径）；本条治的是**压根没开成的课**——P0 **不动**
`RESTART_ONLY_FIELDS`，也不动 `plan_reload` 分类学。

### 29.2 P1：只读判据（控制台「起不来」不再读成「无外部等待」）

* 校验链抽中立模块 `worker/course_args.py`（`_read_rl_config` / `apply_course_machine_overrides` /
  `course_args`；`loop_serve` 按旧名重导出 ⇒ 既有调用点/对拍用例零改动）。住 `worker/`（训练栈家）
  而非 `trainer/` 是分层法定的：`trainer/` 只许编排（`tests/test_layering.py::
  test_trainer_holds_only_orchestration_modules`）——**依赖方向**同时解掉：`loop_plan` 不得反向
  import `loop_serve`（循环 + 只读 `--json` 每轮拉起 serve 的导入代价）。
* `course_args.course_openable(course) -> (ok, reason)`：跑 `course_args`、零副作用（不建锁/不写账本/
  不碰 runtimes）；失败原文透传（含「读不到课程文件」——不得因为读不到就假定没事）。
* `loop_plan.course_openable` 只加**覆盖边界**：BC 课走 `_open_bc_course` 另一条链 ⇒ 一律 `(True, "")`
  （不把「没判过」写成「坏」）；serve 级 `--mode` 只读端拿不到 ⇒ 判据对应「无 serve 级 argv」。
* `waiting_state(..., blocked=reason)` 新增 `WAIT_BLOCKED`：优先级 `finished > inflight > collect >
  blocked > idle > ready`，仅 `pending>0` 时报。（§28 的「刻意不新增 `WAIT_*`」只针对收官文案，不矛盾。）
* `run_rl_cluster.build_rows` 每行加 `openable {ok, reason}`（`--json` 契约面；顶层不加 `skipped`）。

**回归**：`tests/trainer/test_serve_skipped_retry.py`（9）· `tests/trainer/test_course_openable.py`（6，
含真夹具复制 + 改一字对照）· `tests/trainer/test_loop_plan_waiting.py`（`blocked` 优先级/文案）·
`tests/worker/test_serve_course_overrides.py`（模块搬家后调用点不变）。反向探针（已跑）：文件身份判据
stub 成恒等 ⇒ P0 失效；恢复真判据 ⇒ 同一改动被看见。只读路径守卫（已跑）：`--json` 冷算
1.2–2.4s（两次实测）、import 链 0 行 torch（10s TTL 预算内）。控制台读面 → `docs/nn/console.md` §30。

---
## §28 只读读面的收官判据 = `iters` 预算（`budget_exhausted` 单点共用，2026-10-02）

**现场**（h4-aim-k10/k25）：两课均已跑满 40 轮并落 `run_complete`，控制台 pill 仍报「推进中」。
根因不在显示层，在**只读读面拿不到预算**：真 trainer 的收官判据是 `want > self.iters` ⇒ 返回空
任务表 ⇒ `QUEUE_DONE`；而控制台读的是另一个进程的 `run_rl_cluster --json`，它按盘重建计划
（`plan_course`），`course_facts` 的 RL 分支此前**完全不接受 `iters`** ⇒ 指针 = 账本 `next_it` = 41
⇒ 13 步表非空 ⇒ `state=READY` ⇒ `waiting_state(pending>0)` ⇒ `WAIT_READY` ⇒ pill「推进中」。
`state='done'` 这条分支在 RL 课上**从未被点亮**。

**决定（判据只一处）**

* 新纯函数 `worker/loop_tasks.py::budget_exhausted(it, iters)`：`iters <= 0`（0 = 不限）⇒ 恒 False；
  `it` 是**下一轮**指针（账本 `next_it`），`it > iters` 即 iters 轮都已结算。与 `already_done`
  同家——都是「这一轮还要不要做」的纯判据。
* `trainer/loop_runner.py::planner` 的内联 `want > self.iters` 改调它——**行为逐字节不变**
  （serve 路径零变化，只是把判据挪到共用函数）。
* `trainer/loop_plan.py::plan_course(..., iters=0)`：RL 分支判预算 ⇒ `tasks=[]` ⇒
  `Supervisor.add_course` 自然置 `QUEUE_DONE`（`loop_scheduler`）。BC 分支不动（它的预算已由
  `bc_progress(iters)` 的指针语义表达）；`course_facts` 的 RL 分支仍未用 `iters`。
* `waiting_state(..., finished=False)`：`finished=True` ⇒ `WAIT_IDLE` + 诚实文案
  「已跑满 itN/iters（改大 iters 后 停→开 可续跑）」。刻意**不新增 `WAIT_*` kind**：收官在语义上
  就是「没有待办」，缺的只是文案；且 `kind` 是 TS 侧联合类型，加一类要动控制台类型与既有用例。
* 读面预算来源：`run_rl_cluster.py::_course_iters` 读 `curricula/<课>.jsonc`（`read_course_keys`）；
  读不到/不可解析 ⇒ `0`（保持今天行为，不猜）。★ 真 trainer 的预算走 `course_args` 解析链
  （serve 级 `--iters` > rl-config 机器覆盖 > 课程文件），本读面**只认课程文件**——覆盖场景以
  训练侧为准（刻意接受的口径缝：读面只解释「课程声明」那一段，不去猜进程 argv）。

**否决方案 B（读 `run_complete` 事件当判据）**：① 它是**上一次 run** 的陈述，改大 iters 续跑后陈旧，
要额外定义 `run_start`/`run_complete` 的先后才能自纠；② 收官判据的 SSOT 是预算
（`LoopRunner.planner` 就靠它），第二份判据必然分叉。

**指针同源核对**：`LoopRunner.planner` 用 `self._ledger_next_it(it)`，读面用 `load_ledger(...).next_it`
——同一份账本口径（`iteration` 行数），对账用例钉住「同一 `(it, iters)` 下两处结论一致」。

**回归**：`tests/worker/test_loop_tasks.py`（`budget_exhausted` 真值表）·
`tests/trainer/test_loop_plan_waiting.py`（`finished` 文案 / `plan_course(iters=40)` 在指针 41 ⇒ 空表、
40 ⇒ 非空 / 与 `LoopRunner.planner` 对账 / 读不到 iters ⇒ 0 ⇒ 行为不变）。
消费面（pill 的「已收官」与读面包容）→ `docs/nn/console.md` §22。

---
## §27 门禁停机模式升为平台级单开关：`tmp/gate-halt.json` 意图 + 回执，课程级三写面退役（2026-10-01）

用户口径（2026-09-25）：「`gate-halt-mode` 应是**平台级**而非课程级，有人盯盘时切为提示，离开时切停机。」
本刀把「门禁触发时停不停」从一个课程级散装旋钮升成平台级单开关，并给「离开」配上机制（到点自动回落）。
（计划全文与两个裁决点 → `gate-halt-platform-level.plan.md`；控制台那一半 → `docs/nn/console.md` §21。）

### 27.1 为什么原来的形状是错的

「停不停」问的是**有没有人在盯盘**——操作员此刻的状态，不是某门课的属性。课程级三写面
（每课 argv `--gate-halt-mode` / `rl-config courses.<课>.gate_halt_mode` / `<traj>/gate-halt-mode.txt`）
外加一个隐藏第四面（`rl.gate_halt_mode`）造成两类事故：

1. **配对腿漂**（2026-09-25 现场：`tmp/x20-dodge-l3/gate-halt-mode.txt = notify` 而 `l1` 无此文件
   ⇒ 同一实验两条臂门禁行为不同，序列可比性受损）。该现场已随 `tmp/` 清理消失，**不再作为 DoD 输入**——
   等价的可复现形式 = 「同一份平台文件 ⇒ 两次判定 `(mode, source)` 相同」。
2. **UI 位于平台位置、实现却是课程级**（顶部开关初值跟当前查看课程变）⇒「看起来是平台开关，
   点了只影响一门课」。

### 27.2 契约（单一事实源 + 回执）

* `tmp/gate-halt.json`（`worker/gate_halt.py`：只 stdlib + `common.log` ⇒ **L0**；已入
  `tests/helpers/remote_dag.py` 的 `LAYERS` 账本）：
  `{"version":1,"mode":"halt|notify","until":<epoch 秒|null>,"by":"console","at":…}`；
  env `NN_GATE_HALT` / `NN_GATE_HALT_APPLIED` 可重定向（控制台侧对偶 `BCITY_GATE_HALT[_APPLIED]`）。
* **优先级**：平台文件 > CLI `--gate-halt-mode` > 缺省 `halt`。CLI 只兜底「文件不存在」——
  若它能在文件存在时压过平台值，控制台写出的意图会被历史命令行静默盖掉（「一处切、全局生效」的反面）。
* **保守方向**：文件缺失 ⇒ CLI ⇒ `halt`；文件**存在但坏**（非法 JSON / 非法 mode / `until` 非数字）
  ⇒ **`halt` + 告警**（读到了坏东西时，最不该发生的事就是被旧 CLI 值接管成 notify）。
* **`until` 读时求值**：`notify` + 未到 ⇒ notify；到点 ⇒ halt；缺失与 `null` 同义 = 不过期。
  ⇒「离开自动停」不需要任何进程去定时翻牌（`effective_mode(mode, until, now)` 是纯函数）。
* **回执** `tmp/gate-halt.applied.json`：逐课 `{effective_mode, source∈{platform,cli,default}, until, at}`；
  **只在结果变化时写**、按课合并、原子替换（`tmp` + `os.replace`）——控制台「实际生效」栏的数据源
  （意图文件回答不了「训练真读到了吗」）。
* **离线腿**：云机侧今天**没有**该读点（读点只在 supervisor 判门路径）⇒ 结构上不可能误读；机制上留
  `NN_GATE_HALT_LEG=offline` 短路（**根本不读**文件，缺省 halt / `source=default`），
  给「整仓目录挂到云机」与将来「随 job 下发」兜底。

### 27.3 落点与退役面

* 唯一读点 `trainer/loop_guards.py::_gate_halt_mode` 改写：删课程级 txt 分支（盘上留着 ⇒ 首次告警
  一次、**不读**），读平台文件 + 写回执 + 打 `gate_mode=… source=…` 一行；读点移到 hub 短路**之前**
  （本机/离线腿也要有日志与回执）。组合根不许新增方法（`test_loop_guards_split.py::test_root_calls_nobody`），
  所以解析/裁决/写回执全住 `worker/gate_halt.py` 的模块级函数。
* `worker/cli.py --gate-halt-mode`：`default=None`（不再 `_d("gate_halt_mode","halt")`）——
  argparse 层**不得**预读平台文件（每进程只读一次 ⇒ 热切失效；热切的关键就是读点每轮现读）。
* `trainer/loop_serve.py::COURSE_MACHINE_OVERRIDE_KEYS` 摘除 `gate_halt_mode` ⇒ 白名单今天**空**
  （结构保留：下一条「单进程表达不了、又确实按课不同」的旋钮还往这里加）。
* `rl_config.schema.json`：`rl.gate_halt_mode` 从 `sections.rl` 挪 `retired`（旧值一律不生效，
  `worker/rl_config_schema.py` 启动时会点名）。

### 27.4 判据 / 测试

`nn-training/tests/worker/test_gate_halt.py`（19 例：缺省/坏文件/`until` 三态/leg 短路/原子写/
回执只变化时写/按课合并/告警去重）· `tests/trainer/test_loop_gate_soft_remediate.py`（夹具换成平台文件 +
旧 txt 不生效）· `tests/worker/test_serve_course_overrides.py`（白名单摘除；机制用例改**探针白名单**，
不因数据空了失去守卫）· `tests/worker/test_rl_config_schema.py`（退役键点名）·
`tests/trainer/test_loop_guards_split.py`（顶层 import 面 +1）。决策 →
`DECISIONS.md` §2026-10-01-goalnn-gate-halt-platform-level。

## §26 节点门多一道「本轮 bootId 一致」：训练侧用 ping 的 pid/bootId 判 stale 并排除（2026-09-29）

> 用户裁定（plan/sampler-single-instance.plan.md §8-Q2）：**采纳备选**——让训练侧把「同 node 的
> bootId 与本轮账本不一致」判为 stale 并排除（plan 原推荐是「先只记进 meta 供归因，不加门」）。
> 起因：a98（2026-09-29）一台机器两个 agent 同听 8443，一轮 192 局被内核按 4 元组哈希分给两个
> 代码版本 ⇒ 45 列旧 shard 混进 payload ⇒ 云 worker `grad` 5 连炸、整门课 aborted。
> agent 侧的第一道防线（应用层互斥）见 `docs/nn/runtime-opt.md` §29；**本节取代其 §29.3 的
> 原口径**（「训练侧暂不看这些字段」——现在看了，且是门）。

### 26.1 判据：一个进程内小账本 + 四次 ping 点的消费

新模块 `nn-training/biz/node_identity.py`（纯逻辑、零依赖、进程内账本，无 IO）：

* **本轮** = `round_key(腿, 采集单元 id)`（`"rollout:it42"` / `"eval:it42.eval"` / `"batch:run.b7u3"`）；
  一次采集单元一枚键。腿后缀不能省：同一 iterId 的 rollout 与 eval 是两轮采集。
* 本轮**第一次** ping 到的 `bootId` 就是该节点的**钉子**（只钉一次，不一致后不改写 ⇒ 重复 ping
  原因幂等）；之后任何一次同键 ping 不同 ⇒ 返回一行人读原因（两侧 bootId/pid + 指路）。
* **旧 agent 无 `bootId` ⇒ 恒无意见**（fail-open）：这道门只在新 agent 上生效，否则升级波期间会把
  还没升级的节点整批排除（比它要防的事更大的事故）。
* 账本只留最近 8 轮；钉子按轮清 ⇒ **下一轮自动重新钉**。

消费面（节点门旁各加一条，判据与 codeHash/bun 门平级）：

| 腿 | 钉/核点 |「排除」的形式 |
|---|---|---|
| rollout 轮内准入 | `trainer/dispatch.py`（`online` 之前） | 本轮不派 + 一行 `excluded this round (stale bootId)` + 一条 meta 行 |
| rollout 中途上线/回场 | `trainer/queue_local.py::rescan_nodes`（与上行**同一个键**） | `本轮不再回场`（下一轮重新钉） |
| A-eval 轮准入 | `trainer/eval_dispatch.py`（codeHash 门之后） | `skipped` + 一条 `mode:"eval"` meta 行 |
| B/C 批单元回场 | `trainer/batch_runner.py::_UnitLanes.bringup` | `lane["given_up"]=True`（复用「放弃该通道」⇒ 主循环的「全部通道已放弃 ⇒ 收摊」与「本机槽位 0」退出条件照旧成立，不会把单元拖满整窗） |

**它不是重启/升级触发器**：mismatch 只上报 + 排除，**绝不**下发 `/v1/restart`——两个进程同时服务
不是重启一个能修好的事（要人去停掉多出来的那个；`/proc/net/tcp` 配方见 §29.4）。

### 26.2 为什么按轮、以及已知代价（用户已拍板）

一次**正常**升级/重启也会换 bootId。按轮钉把影响面限在「触发变化的那一轮」，下一轮自动回场；
代价是 —— **一个节点在轮中途重启后，本轮不再被用**（它对这一轮的贡献止于重启前；在飞的局本就
丢了、会重投）。plan §8-Q2 原话把这个代价记作「可能误伤正常重启前后的一轮」；本轮裁定为**接受**，
因为混一个代码版本进 payload 的代价（整门课 aborted）远大于一台节点少跑半轮。要回退：删四处
`node_identity.note_ping(...)` 调用即可（模块本身无副作用）。

判据比 codeHash 门**更早、更便宜**：codeHash 门要等一个代码版本差异，而 bootId 变化在**同一份
代码**的进程换代上也看得见；两个 agent 同听一个端口时 codeHash 门是**抽签**（同一轮 ping 落在哪个
进程上看 4 元组哈希），bootId 变化则是这个缺陷的指纹。

### 26.3 钉在哪 / 读数

* 测试：`nn-training/tests/worker/test_node_identity.py`（10 例：钉/不一致/不复写/旧 agent fail-open/按轮隔离/
  账本有界/坏输入不炸 + **接线守卫**：四个门点都真调该判据、rollout 的钉与核共用同一个 round key 表达式）；
  `tests/trainer/test_batch_lanes_split.py::test_bringup_rejects_a_node_whose_bootid_changed_within_the_unit`
  （消费面行为例：门全放行、唯一变量 = bootId 换代 ⇒ 拒派且 `given_up`）。
* 结构守卫连带登记：`trainer/batch_runner.py` 的顶层 import 闭集（`tests/trainer/test_batch_runner_split.py`
  `RUNNER_IMPORTS`）多了裸包 `rl`（`from rl import node_identity`）——先红、再登记（本仓惯例）。
* 门禁读数（2026-09-29，Linux/forkdist）：`bun run pygate` **3368 passed / 3 skipped**，ruff + mypy 全绿（18s）。

决策全文 → DECISIONS `§2026-09-29-goalnn-node-bootid-gate`；agent 侧契约与真机配方 →
`docs/nn/runtime-opt.md` §29。

---
## §25 x2 事件门（R2 包）落地：决策门单点化 + threat-ONSET 沿 + Δt≥3（2026-09-27）

**背景**：新纪元 R2（`plan/new-era-stop.plan.md §6`）要求 x2 rung 具备「事件驱动决策」，且不碰 x1。
原状：五处决策门各写一份 `t % K === 0`（`policy-input` / `export-rl-rollout` / `export-eval-game` /
`export-nn-replays` / `record-games-video` + `divergence-probe`）——每多一处就是一次口径分叉机会。

**做法**：新模块 `src/nn/decision-gate.ts` = 唯一谓词 `decisionDue`：均匀 K ∪ threat-ONSET **沿**
（`!prevThreat && inThreatLane`）+ 最小间隔闸 Δt ≥ 3（含均匀边界）；`events=false` 时**精确退化**为
`t % K === 0`（不读威胁、零额外开销）。六处调用点全部改调它；各工具加 `--decision-events`（缺省 off）。
读数（n / events / avg-Δt / min / max）进 shard manifest、`_rl_report.decision`、
`_eval_report.decisionReadout`——**仅带 flag 时写**（既有产物逐字节不变）。

**基线（ladder-c20 四局，x20-noexplore it99）**：onset 1–4 次/局（+0.5–2% 决策步），avg-Δt 9.79–9.94，
min-Δt ∈ {3,4}；γ 保持 per-step ⇒ 视界 1958–1988 tick（相对均匀 2000 漂移 −0.6%…−2.1%）。
state-init 共存 = 方案 i（切点规则不变 + 交棒后首段禁事件）。

**验证**：HEAD `cc9ffdbd` A/B 逐字节一致（rollout shard npy+manifest、eval 报告）；零事件局 shard
逐字节一致（单测）；部署/judge parity 新增事件局用例。决策全文 → DECISIONS
`§2026-09-27-goalnn-r2-event-gate`；实施细节与数字表 → `plan/new-era-stop.plan.md` §6「R2 实施记录」。

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

**② 干烧熔断落执行面**（新模块 `biz/kickstart_burn.py`，纯函数）：基线**不靠人填** =
`eval_log.jsonl` 的 **it0 行**（课程 bc 权重的干净评估 = 缰绳锚定的同一份权重）；连续
`points`（3）个评估点低于基线 `margin_pp`（5pp）⇒ 停腿告警「疑似回锚/塌陷」。阈值走**课程文件**的
`kickstart_burn` 块（2026-10-02 起随腿入库；rl-config 回落当天第二刀已删——课程 gates 块仍为空，
阶梯课程不配 gates 是 I2 铁律）。

- **判据同源**：给 `gate_check.read_trend_rows` 加 `include_baseline`（it0 默认仍被滤），
  与 `_gate` 共用同一读者/同一文件；干烧计数落 `kickstart_burn` 事件 ⇒ 重启可回放。
- **停腿而不只告警**：本地停腿 + ABORT 判决 + 按课程下发云端 halt——停腿的意义就是停止烧钱，
  本地停了云机接着领活 = 白停。
- **状态转移才落账**（同 `_stop_loss`）：streak 变才写，0→0 不写（否则每轮一行淹账本）。
- 读数缺失的行（0 局的轮）**既不计数也不清零**：当 0 会误停腿，清零会打断真趋势。

**验收（盘上）**：`tests/worker/test_kickstart_plan.py`（15：四管道逐条 / 公式三点 / 缺席逐字节不变 /
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

**验收（盘上）**：`tests/worker/test_single_ppo_path.py`（5 例：argparse 无 `--ppo`/无 `args.ppo` 属性且传参报错、
无 degrade 旗标、AST 零 `args.ppo` 引用、无 `*PPO*BACKEND*` env 读点、课程 schema 拒 backend 键）；
`tests/remote/test_wait_starvation.py`（3 例：零 worker 响亮 / leased 不喊饥饿 / 可关）；
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
- **决定**：`biz/reports.py::merge_volume_report(wave, disk_manifests)` —— 盘上有
  games 则 **以 disk combine 为报告体**，wave 只覆盖时间锚点
  （`pure_collect_sec` / `weights_dist_*` / `collect_end_ts`）。拒绝「仅 wave 为空时
  回填」：重启后本进程可能只补缺口批，wave 有 games 但仍小于盘上全量，会低估。
- **接线**：`loop_core._volume_collect_continuous` 收官处（S4 第十八刀后该簇住
  `trainer/loop_volume.py::TrainingVolume`，方法名不变）
  `resumed_manifests(traj_dir, wver, extra_wver, course_fp)` + stages 过滤 → merge。
  `_traj_dir = traj/it{N}` 已是迭代作用域。
- **验证**：`tests/worker/test_rollout_volume.py::test_continuous_restart_quota_met_backfills_winrate_from_shards`
  + `tests/worker/test_rl_reports.py` 三条 merge 不变量；volume/ledger/scheduler/e2e 相关
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
`tests/remote/test_local_worker_multi_course.py`(3) 用**进程内真 hub（HTTP）+ 真 worker 领活函数**钉住：
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

**一句话**：`trainingLoop` 从「每课一进程」收敛为**共享槽单进程**（`trainer/run_rl_cluster.py --serve`，
发现模式），于是 hub / 隧道 / selfNode / trainer 四者各只有一个进程就能服务所有并行课程；
RL 与 BC 课走同一条启动路径。

### 之前错在哪

R3-3 把组件卡分成「服务面 · 单例」与「课程面 · 按课程」，但只交付了**读面**：判据表里
`SHARED_COMPONENTS = ['hubServer','cloudflared']`，trainer 仍在课程面。这不是显示问题而是形状问题——
控制台仍按课起 `trainer/run_rl.py --course`，于是：

1. 「BC 课 A + RL 课 B」要**两个进程**，而 BC 与 RL 共用 `trainingLoop` 这一个角色键；
2. 训练侧明明已有单进程驱动者（R2d `trainer/loop_serve.py`：按课锁 / 按课日志镜像 / 引擎池 / 故障隔离 /
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

**一句话**：`trainer/run_bc.py` 从「一个进程服务一门 BC 课」的脚本变成**入口薄壳**，编排体搬进
`trainer/bc_loop.py::BcLoop` —— 于是 `trainer/run_rl_cluster.py --serve` 那一个进程现在能同时带 RL 课与
BC 课，BC 等云端 GPU 回传时把执行权让给别的课。

### 为什么必须搬（不是「统一风格」）

BC 的 `main()` 是整段 procedural 编排，而 supervisor 要的是**引擎子集**：`_setup()` /
`run_one_round(it)` / `finish_course(it)` / `release_torch()` / `ledger_next_it()`。
`BcLoop` 就是那个子集，与 `TrainingLoop` **同名同义**（serve 的执行体不认识课程种类）。

### 搬迁方式：AST 逐字节，不「顺手改善」

14 个函数体（`collect_corpus` / `publish_bc_job` / `train_local_bc` / `wait_bc_round` 的重构件 /
`finish_all_rounds` / `archive_round` / `resolve_transport` …）用脚本按 `ast` 段落整体搬，
**只在内部调用名上去下划线**（`_append_ledger` → `append_ledger` 等）；搬完用
`ast.dump` 对拍（14/14 逐节点相同）才落盘。`trainer/run_bc.py` 剩下的只有：utf8/chdir/单实例锁/
启动前 `git push`/清 hub 停机态 + **阻塞式**驱动（单课程语义：等外部时原地退避重问）。

### 三条新机制

1. **`ROUND_WAIT`（第三种轮终态）**：`retry` = 「我试过、失败了、重做」；`wait` = 「已发布，
   **正在等外部事实**」——本轮未完。调度器据此把执行权交给别的课（不占资源票、不计失败连击），
   过一会儿回来问同一轮。`RoundOutcome.detail` 是它的**人读原因**（带 jid），直接上屏到控制台
   调度器卡片的「在等什么」。
2. **`ledger_next_it` 钩子**（指针语义归引擎）：BC 读 `bc_round_completed`（`biz/bc_ledger.py`），
   RL 读 `iteration`。在桥里写死一种 = 给另一类课程读错指针（BC 课会永远停在 it1）。
3. **`round_tasks_for(course, it)`**：粒度按课程种类选表（BC ⇒ 单个轮任务）。13 步表是 RL 的一轮，
   硬套会给 BC 发它不认识的待办（执行体响亮 ABORT）。

### 重入不重发布（最贵的一条，四处都钉）

BC 的续训按 `jid` 存（hub `/jobs/{jid}/resume`、worker 本地 `bc-resume/<jid>`）⇒ 重发 = 新 jid =
**从头训** = 一轮 GPU 时间白烧。所以「让位后再来问」用内存会话、**进程重启/引擎驱逐后**先
`find_round_job` 认领盘上那份未收口 job（按 manifest mtime 取最新，其余响亮列出但不回收——
它们可能仍在某台云机上跑）。冒烟轮**不认领**（语料口径被压缩过，复用等于拿冒烟语料冒充真语料）。

### 回归

`tests/worker/test_bc_ledger.py`(9) · `tests/trainer/test_bc_loop.py`(16：重入不重发布 / 盘上认领 / 已完成轮跳过 /
push 每轮一次 / 本机一枪到底 / 冒烟与回显作废 / 指标增量不重复且失败不致命 / 阻塞驱动收官) ·
`tests/trainer/test_serve_bc.py`(5，**serve × BC 集成**：BC 让位时 RL 课照常跑完 / 细粒度下 BC 仍是轮粒度 /
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

- python：`tests/trainer/test_loop_control.py`(22：解析边界 / 保守方向 / 幂等 / 坏文件不覆盖 / 回执原子性与
  **未变化不写盘**／pid 语义) + `tests/trainer/test_serve_wiring.py`(+8：零课程照跑 / 中途出现的课自动入队 /
  坏课只试一次 / 暂停只停点名课 / 恢复从原处接着跑 / 坏控制文件保守继续 / CLI 转发形状)。
- dashboard：`tests/loop-control.test.ts`(22) + `web-app-loopqueue.test.ts`(+9【今 → `dashboard/tests/web-loop-queue.test.ts` + `dashboard/tests/web-app-coursematrix.test.ts`】：四态 / 文案 / 徽标 /
  只读不渲染开关 / route+cache 接线) + `server-api-loop-queue.test.ts`(+1)。
- 门禁：nn python gate **1511 passed / 4 skipped**；dashboard **621 passed**；三份 bundle 构建 +
  根 `bun run check`（1864 passed）+ `bun run build` 全绿。

**仍未做**：**R2e**（多课 × 假 worker/假 PPO 的 e2e + 真机双课并行跑通——serve 的真机行为需要人验；
本轮同样只在假件下验证过）· R3-3（组件卡片分组：单例角色 vs 按课程）。

---

---

## §17 R2d 写的一半：单进程 supervisor 真的能跑（serve + 引擎池 + 日志行路由）（2026-09-19）

R2c 造好了调度器（`loop_scheduler`）与任务体↔引擎的桥（`loop_runner`），但**没有驱动者**——今天仍是
「一门课一个 `trainer/run_rl.py` 进程」。本轮交付驱动者：`trainer/loop_serve.py` + `biz/engine_pool.py` + `biz/log.py`
的行路由，入口 `trainer/run_rl_cluster.py --serve --courses a,b`（默认仍是只读计划视图）。

**分层各做一次**：进程级（utf8/faulthandler/chdir/启动前 push 一次/bun 检查）→ 课程级（参数解析与
`trainer/run_rl.py --course` 逐字段一致 / validate_args / **按课程的单实例锁** / 清本课 hub 停机态 / 建引擎对象）
→ 步骤级（`EnginePool.get` → `ensure_ready` → `LoopRunner.executor`，包在 `prefix_scope(课)` 里）。
`ensure_ready` 按**对象身份**判断：对象换了（首用/被驱逐后重建）走 `_setup()`，没换则幂等补栈。

**四个刻意的设计点**：① serve 模式**不收官停车**——新拆 `TrainingLoop.finish_course(it)`（收敛预采/云机
PAUSE/`run_complete` 落账，与单课程路径共用一份实现），停车会冻住其余课；② 不换 `sys.stdout`，课程归属
改成**行前缀 + 镜像写本课 `out_log`**（两个 Tee 会互相复制）；③ 引擎池驱逐 = 响亮的一次「重启」
（权重可从 `args.out` 复原、**Adam 动量重置**；绝不驱逐在用引擎；容量默认 = 并行课程上限 5）；
④ 初次入队的粒度必须匹配执行体（细粒度 13 步 / 轮粒度单个 `round`），且只用判据原语
（`course_facts` + `pending_tasks(round_tasks(...))`），不拉 `plan_course` 的展示面字段。

**回归**：`tests/worker/test_engine_pool.py`(9) + `tests/common/test_log_router.py`(6) + `tests/trainer/test_serve_wiring.py`(8，
含「轮转交替 a,b,a,b」「13 步全表」「让位让别的课跑完」「容量 1 时驱逐重建再 setup」「坏课隔离」
「参数与 `trainer/run_rl.py` 对拍」——对拍 oracle 是在子进程里跑 `run_rl.main()` 本体，本机缺当前 era 权重时
跳过并写清理由，其余情况失败即红）。门禁：python gate **1476 passed / 4 skipped**；根 `bun run check` 绿。

**仍未做**：控制台的入队/暂停 + 单例 `trainingLoop` 卡片（读面卡 R2c-3 已交付）；**R2e** 多课 e2e 与
真机双课并行跑通（serve 的真机行为需要人验）。

---

## §16 R2c-3 收口：真机 RSS 实测（checkpoint 缓存上限的真实约束是「数量」）（2026-09-19）

R2c-3 的最后一项：单进程 supervisor 要为 N 门课各持一份 torch 栈，`checkpointCacheMb` 给多少
此前只能拍脑袋（本机 0 卡、remote 为主）。新增 `nn-training/worker/scripts/measure_checkpoint_rss.py`
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

`nn-training/tests/worker/test_measure_checkpoint_rss.py`（7 例：推荐值取整/余量、候选表恒有默认架构兼底、
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

`worker/loop_round.RemotePpoJob`（`RoundContext.remote`）：**不进引擎实例属性**（§2.2；单进程多课程会
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
`tests/worker/test_loop_runner.py` 把「不在表里」写成了断言（这个坑已经踩过一轮）。

### ⑦ 验收与实测

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest xdist -n 12） | ✔ **1432 passed / 3 skipped** |
| 根 `bun run check` | ✔ 绿 |

新回归：

| 文件 | 例 | 钉住 |
|---|---|---|
| `tests/remote/test_remote_probe.py` | 14 | 三态分类 · 410 抛而 202/404 不抛 · **只问一次**（预置第二个响应没被消费）· 探针超时 ≤10s · 两条链路端点各自正确 |
| `tests/worker/test_remote_ppo_phases.py` | 8 | 未就绪⇒让位且不取结果 · **再入不重发布** · 不可续跑⇒就地阻塞 · 成功复位连败 · 发布/取结果/落位失败**都**进同一份判决 · 真策略接线（连败到阈值即降级） |
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
显式对象 `RoundContext`（`biz/loop_round.py`）。

**不放引擎实例属性**的理由是硬的：单进程多课程下，`self._pairs` 会被别的课覆盖（§2.2 无隐藏状态，
多课程只是把这条老规矩的代价从「难查」变成「串课」）。上下文生命周期 = 一轮，`it` 变了就换新的。

### ② 步骤表是唯一顺序来源

```
ROUND_TASKS (biz.loop_tasks)  ← 唯一顺序来源
     ├── STEP_ORDER  = ROUND_TASKS（同序）
     └── STEP_METHOD : kind → 引擎方法名
                        └── 组合路径 run_one_round 与细粒度驱动器都从表取步骤
```

步骤实现住在 `trainer/loop_round_steps.py`（`RoundSteps` mixin，MRO 排最前）。切法**不是重新设计控制流**：
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

`trainer/loop_runner.WAIT_HOOKS`（kind → 引擎钩子名）。**钩子不存在 ⇒ 不让位**（老引擎行为逐字节不变），
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
软等窗口。`tests/worker/test_loop_runner.py` 把这个「缺席」写成断言，防止后来者顺手补上。

### ④ 已装的那一处：预采（1 小时轮询 → 让位）

`join_precollect_child` 旧形态每 2s 轮询 `completed_pairs`、上限 **1 小时**。单课程前台跑时这只
是「等一下」；单进程多课程下它就是**一个慢子进程拖垮所有课**的入口。

判据抽成 `trainer/rollout_phase.precollect_ready`（**非阻塞**：句柄为空 / 子进程已退出 / 就绪 shard ≥
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
| `tests/worker/test_loop_round.py` | 13 | 表 == `ROUND_TASKS`、`STEP_METHOD` 与实现一一对应、`precollect_join` 必须最前、组合循环按表施加终止/异常分类 |
| `tests/worker/test_loop_runner.py` | 5 | `WAIT_HOOKS` 的 kind 都在表里、`eval_join` 刻意缺席、钩子缺失⇒不让位、不就绪⇒`WAIT`+原因+`resume_at` |
| `tests/trainer/test_precollect_ready.py` | 7 | 非阻塞（<0.5s）、半波即就绪、已退出即就绪、与 `join` 同源、超时仍 terminate |
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

### ② `trainer/loop_runner.py`（新）：任务体 ↔ 引擎的**唯一**桥

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
| `nn-training/biz/loop_scheduler.py` | **纯调度**（无 torch/网络/IO）：`PoolSet` 资源票 + `CourseQueue` + `Supervisor`（公平轮转 → 闸门 → 单线程执行 → 四态收敛） |
| `nn-training/trainer/loop_plan.py` | 唯一碰盘的 IO 边缘：账本指针（`LedgerView`）→ `RoundFacts` → `pending_tasks`；shard 结算数；`commit_journal` 在飞集；课程发现 |
| `nn-training/trainer/run_rl_cluster.py` | 入口：**一个进程**读所有并行课程，输出「下一步 / 待办 / 在等谁（含 job_id）/ 被什么挡住 / 关键事实」 |

### 三条不可交易的性质（测试逐条钉住）

1. **同一时刻只跑一个任务**（单线程执行器）——用「执行体重入即计数、峰值必须为 1」证明；
2. **`WAIT` 不占执行权**——一门课等远程 PPO 时，另一门课跑完自己整轮（时钟注入，测试不真睡）；
3. **故障域按课**——一门课 `ABORT`（门禁停腿 / 5 连击）只脏它自己的队列，另一门课照常跑完。

外加：轮转公平（`a,b,c,a,b,c` 而不是「跑完 a 再 b」）、`RoundFacts` 算不出时不跳（保守方向）、
执行体抛异常 = 一次失败（不打崩调度器）、planner 空转 ⇒ 响亮抛错。

> ⚠ **2026-10-22 变更**：「轮转公平」**已被 §37 的「按课分批 + 采集优先」取代**（用户口径：
> rollout 恒高于 eval）。本节其余性质（单线程 / `WAIT` 让位 / 故障域）不变。

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

### `biz/loop_tasks.py`（新，纯逻辑：无 torch / 无网络 / 无文件 IO）

| 件 | 内容 |
|---|---|
| `Task` | `task_id = course:it:kind`（**幂等键**；重试只动 `attempt`）· `resource` 由 `RESOURCE_OF` 给出 |
| `TaskResult` | 四态 `DONE` / `WAIT(resume_at)` / `RETRY` / `ABORT` + `is_terminal`（DONE/ABORT 出队）；非法状态当场 `ValueError` |
| `ROUND_TASKS` | 一轮 **13 步**：`prepare_iter` → `hot_reload` → `course_iter` → `precollect_join` → `rollout` → `volume_topup` → `eval_dispatch` → `ppo` → `export_weights` → `eval_join` → `record_iteration` → `gate` → `cleanup` |
| `RESOURCE_OF` | 只有 `rollout`/`volume_topup`/`ppo`/`eval_join` 占池；**等待型/记账型不占**——这才是单进程能服务多课程的机制 |
| `RoundFacts` + `already_done` + `pending_tasks` | 幂等判据只认**盘上事实**；`iteration_recorded` ⇒ 整轮为空（★ 不得重写账本、不得重发 job） |
| `resolve_failure` | 与现主循环逐条一致：冒烟作废原地重试 / 死腿立刻 ABORT（不再 5×30s 空转）/ `attempt≥5` 才停、it 不前跳 |

### 在飞集：复用既有 WAL，不建第二份登记表

`biz/commit_journal.py`（I1 的 `started/finish` WAL）新增：

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

新回归：`tests/worker/test_loop_tasks.py`（10 例：任务表/幂等键/资源池/四态/幂等铁律/失败语义）、
`tests/worker/test_commit_journal.py`（+4 例：attach 不动状态机、孤立 attach 不造在飞、finish 后不可复活、
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

**「有状态会话」的字面证据**：`TrainingLoop.__init__`（`trainer/loop_core.py:151`）约 60 个跨轮字段；
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

### R2a 落地：`nn-training/biz/train_ledger.py`

`LedgerSpec`（阈值，与 `_breaker` 同源）+ `LedgerView`（单遍 `load_ledger` + `apply_event` 增量）
+ `soft_remediate_count(kinds)`。**不新造账本**：`training_log.jsonl` 已含全部事件，且已有五个扫描器
（`biz/resume.py` × 3、`biz/gate_check.py` × 4）——R2a 只是把它们收敛成一份视图。

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

新回归：`tests/worker/test_train_ledger.py`（15 例：与五个旧扫描器**奇偶断言**、增量==单遍、
独立复算连击、坏行/未知事件透明、提示类计数口径）、`tests/worker/test_train_ledger_wiring.py`
（3 例：`_setup_common` 继承、继承计数当轮停腿、空账本从零起）。

### 踩坑（本轮）

1. **`_ent_peak` 的赋值位置**：原代码在 `if start_it > 1:` 分支里才从账本回读 ⇒ 视图化后必须
   提到**无条件**赋值，否则「空账本但 view 有值」的组合说不清（本轮统一为「视图即真相」）。
2. **`NO_CLOUD_HALT_KINDS` 是 `TrainingGuards` 的类属性**（不是模块级名）——从 `trainer.loop_guards`
   直接 import 会 ImportError，用 `TrainingGuards.NO_CLOUD_HALT_KINDS`。
3. **mypy 需要显式声明 mixin 属性**：`TrainingSteps` 里调 `self._ledger_apply` 必须在类体声明
   `_ledger_apply: Any`（与既有 `_jsonl_path: Any` 同法），否则 `attr-defined` 报错。
4. **测试期望值别跟着实现写**：agg 缺失轮（`kl=None`）在 `_breaker` 门控下**既不续也不断**连击
   ——我第一版测试写成「断」（期望 1），实测 2 才是对的；已把这条语义写进断言注释。

### 下一步

R2b：`biz/loop_tasks.py`（任务 schema + 四态执行器）+ 每课 `loop-state.json`；随后 R2c 单进程
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

**钉死单测**（`tests/worker/test_rollout_volume.py`）：新增「① T9 量纲钉死（600000/4/980）」——
同一条 target=600000、4 关：新语义（est=98 samples）`G0=1531` **一波达标**；旧语义（est=980 ticks）
`G0=154` **补满 `DEFAULT_MAX_WAVES`=3 波仍 < 达标线的 1/3**。另加
`test_t9_old_ticks_key_name_is_rejected`（改名护栏：旧键照写必须响亮报错）。桩里 `totalTicks` 刻意写成
`10×samples`，任何误读 ticks 的路径都会 10× 暴露。回归：`tests/worker/test_rollout_volume.py` 52 用例 + 
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

**实现**：纯函数在 `biz/eval_local.py`（`rotor_offset` / `rotor_span` / `dual_track_seeds` /
`should_dual_track` / `split_anchor_rotor` / `overfit_gap_pp` / `overfit_fires` +
种子段注册表注释）；`settle_eval_summary` 拆分台账 seed 落段并写三字段；
`EvalDispatcher.run` pairs 构造处接线（日志带 `[dual-track anchor+rotor]`）。

**验证**：`tests/worker/test_dual_track_eval.py` 10 用例（周期/无重叠/报警 4.9 静默・5.0×3 响・
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
逐字节不变）。纯逻辑 `biz/volume_waves.py`（配额数学 + 按 (stage,wave) 独立种子流 + 终止谓词 +
补波计划 + WAL 行解析）；账本 `worker/resume.settled_stage_totals`；est 来自 jsonl trailing 均值
（`worker/resume.trailing_ticks_per_game`，纯函数可 replay）；接线 `trainer/loop_core.py::{_iteration_pairs,
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
   而队列/远端 path 的 shard 是聚合单局形（`totalSamples`，`common.distribution.write_shard` **原样写** agent 返回的
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
nn-training python gate 绿（新增 `tests/trainer/test_remote_transport.py`：run_rl 与 run_bc 两侧裁决
+ argparse 默认值与 choices）；`bun dashboard/src/server/build.ts` 三份 bundle 与根 `bun run build`
均通过。顺手补了 HEAD 上已红的 `selfNodeSpec.cwd`（控制台以 dashboard/ 为 cwd 启动时的
`Module not found` 回归护栏）。

---

---

## §5 课程热加载：非语料改动下一 iter 应用；语料改动拒绝+横幅+不泄漏云端（2026-09-13，用户拍板）

**机制**（DECISIONS §2026-09-13-hot-reload · 全文 → 本文件 §25）：trainer 每 iter（rollout 前）重读课程文件，按
`corpus_identity_fp` 分流——

- **未变（B/C 类）**：`biz/hot_reload.apply_hot_fields` 白名单写回 args（消费点每轮活读：
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
- `corpus_identity_fp`（biz/config.py）：语料身份 = env+reward **解析值**规范化哈希（内联与
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
  - **P0**：`tests/training-multi-course.test.ts`（双课 spec/锁/配额/门禁）、`nn-training/tests/trainer/test_multi_course_locks.py`、`docs/multi-course-audit.md`。
  - **P1**：`tools/training/slots.ts`（端口算术 `hub_base + slot*10` 唯一归宿 + `checkCapacity`/`allocateSlot`）；锁 per-course（`.run_rl.<course>.lock`/`.train_loop.<course>.lock`，无课程沿用旧名）；账本 `Record<course, Entry>` 四新键 + 旧扁平键读兼容（R1）+ 旧条目一次性回填迁移；`train.ts` 双锁 per-course 预检 + `kill (script, course)`（R3）；`git push` 串行化 `.git_push.lock`。
  - **P2**：hub per-course（jobRoot/jsonl/端口），BC 种子 `courses.ts::seedWeightsFromBc`（缺文件 fail loud）。
  - **P3**：cloudflared/workerServe per-course；`rl.remote_hubs[course]` + 兼容单键；`gpu_push` 清单按 `push_node_url` 过滤（非空零匹配 → WARN + manifest 打标，不抛）；notebook 引导文本。
  - **P3b**（DECISIONS §2026-09-12-multi-course-p3b-supersedes-343 · 全文 → docs/nn/remote-transport.md §32）：hub 独占租约 `CLAIM_TTL_SEC=300`（领取即设 owner+expiry+heartbeat，心跳续租，过期回池，有活租约验 `X-Lease-Token`）；worker_server 有界 FIFO `WORKER_QUEUE_MAX=8` + 同 jid 幂等 + `/ping queued`。
  - **P4**：`saveConfig` 落盘前 `capacityError` 加法校验（`Σ eff ≤ max(rl.workers, rl.local_slots)`）；`biz/config.py::resolve_course_quota` 热读 `courses.<课>` 优先 + 响亮行 `[quota] workers X -> Y (multi-course split)`；连续 2 轮零 shard 落盘告警；manifest `course_name`（审计短名，不进幂等键） + dispatch 报告带 course；EvalBoard `batches.jsonl` 跨进程 `claim.lock`（复用 `worker.train.loop_util` 锁，拒绝第三套实现）。
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
- **改动**：① `trainer/loop_guards.py`：`_park_on_gate`→`_apply_verdict`，非 HOLD 判决仅落 `gate_verdict` 事件并返回 False（继续训练）；新增 `_sync_cloud_halt`——REMEDIATE/PAUSE/ABORT→hub halt，HOLD/ADVANCE→resume，实例态 `_cloud_halted` 防重发；② `remote/hub_client.py` 新增 `set_cloud_halt`（console 与 loop 共用 /admin/workers/{} 端点）；③ `remote/worker.py` 停机达令分支补 60s 存活日志。停车只剩预算到顶/F4 熔断/止损 2σ/远端不可用 ABORT 等硬边界。
- **验证**：新增 `tests/trainer/test_loop_gate_nopark.py`（REMEDIATE 不停车+halt、HOLD resume、STOP 不发、无 hub 短路）+ `test_remote_hotswap.py` 停机期存活日志 + `test_remote_ppo.py` set_cloud_halt 链路；gate_check/degrade/run_rl -k gate 全绿，mypy 干净。
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
| biz/gate_check.py | G13 分母基线 = **首个完成迭代结束时刻**（起步热身不计账）+ 完成迭代 ≥2 守卫 + evaluate(only_kinds) | 事故读数回放：213s/463s=0.46 → HOLD（修前 0.065 必停）；c6 慢性 0.15 仍 REMEDIATE |
| trainer/loop_guards.py | `_gate` 非评估轮只查 duty（每轮现形）；`_budget_hard_cut` 轮级 max_hours 兜底 | test_train_loop_pure / test_run_rl_m1 通过 |
| trainer/loop_core.py | run() 接入轮级预算硬断 | — |
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
- **决定（R1 门槛进代码，M0+M1）**：① `biz/config.py` 加 `GateTeacher/GateRule/GatesSpec`
  （parser 强校验 §3.4 全 8 条，未知 kind 响亮报错，`_GATE_FRAC_FIELDS [0,1]` 与 `_GATE_REL_FIELDS ≥0`
  分家——§3.2 示例的 `max_phits_rel: 1.5` 是相对倍数，本就 >1，计划原文按批注修正）；
  ② 新增 `biz/gate_check.py` 纯函数求值器（`evaluate(course, trend_rows, health, budget, now=None)`），
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
  `trainer/run_rl.py` 锁曾用 `args.course_name`（内部名）→ `.run_rl.s-dodge-mix.lock`，与 TS 预检查的
  `.run_rl.s-dodge.lock` 对不上：preflight 永远查不到在跑进程，同课双开只能靠 python 侧兜底。
- **备选与否决**：锁用内部名、TS 侧改读内部名 —— 否（内部名要解析课程文件才知道，launcher
  preflight/kill 匹配全要变重；且 `tmp/`、`out`、`courses` 块全是 stem，改一边不如统一到多数方）。
- **决定**：命名空间键 = 课程文件 stem。`worker/train/loop_util.py::course_key_from_path()` 为唯一推导点；
  `trainer/run_rl.py` 锁改调它；`trainer/train_loop.py --course` 文档写明传短名（与 `--course s-dodge` 同拼写）；
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
  声明四类环境键 = 配置冲突 raise（`worker/config.load_course` 合并）。内联 stages 旧用法逐字节
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
  - **未变（B/C 类）**：`biz/hot_reload.apply_hot_fields` 把白名单字段写回 args——消费点每轮
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
  逐点零超时；**上界不缩**（12900 ≥ 原式 12000）。单一实现 = `biz/ladder_factory.max_ticks_for()`，
  由 `nn-training/tests/biz/test_ladder_factory.py` 钉死。

  ⚠ **2026-10-13 更正**：`biz/ladder_factory.py` 已退役（§33）⇒ 这个"单一实现"与它的测试都**不在了**。
  公式 `max_ticks = 600×count + 900` 本身**不受影响**（上面这段文字就是它的规范），现在只以**字面值**
  活在已生成的关卡文件里（`levels/ladder-c01..c07`、`ladder-c20-lives1*`）；要再生成新关卡时
  从 git 历史取回工厂（`git show HEAD:nn-training/biz/ladder_factory.py`）。
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
  纯逻辑住 `biz/volume_waves.py`，账本口径 `worker/resume.settled_stage_totals`，接线 `trainer/loop_core.py::_volume_topup`
  （S4 第十八刀后接线整簇搬到 `trainer/loop_volume.py::TrainingVolume`；`_volume_topup` 的 wave 规则自
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

- **背景**：`trainingLoop` 一直不是「请求处理器」，而是**有状态会话**：`TrainingLoop.__init__`（`nn-training/trainer/loop_core.py:151`）声明约 60 个跨轮字段（进度指针 / 门禁计数 / 在飞线程与子进程 / torch 模型与优化器 / 轮内瞬态），`run()` 是 190 行顺序脚本——一步阻塞整条腿阻塞（等远程 PPO、等 rollout 子进程、等 eval 尾巴）。所以「多课程 = 多进程」不是设计选择而是形状的必然结果，与用户口径「hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程」冲突。用户进一步给定形态：**任务队列，任务自带一切**（与 hub 的 job 同构）。
- **决定（设计稿 `plan/r2-loop-task-queue.md`）**：
  - **状态五分类**（逐字段给出归宿）：**A 指针**（`next_it`/`rotate_seed`/阶梯＝`it` 的纯函数）· **B 门禁指标**（连击/累计量/止损/提示类计数）· **C 在飞集**（`job_id`/等谁回传/eval 尾巴/预采子进程——线程句柄不可序列化 ⇒ 落盘的是**意图**，重启按意图重建）· **D torch 对象**（每课内存缓存最新 checkpoint：权重 + Adam）· **E 轮内瞬态**（`_report`/`_agg`/`_volume_*` 等，**禁持久化**——持久化它 = 拿旧数字记新轮）。
  - **任务模型**：一轮拆成 13 个细粒度任务（`prepare_iter` → `rollout_dispatch/wait` → `ppo_publish/wait` → `export_weights` → `eval_dispatch/join` → `gate_eval` → `record_iteration` → `cleanup`）；执行器返回四态 **DONE / WAIT / RETRY / ABORT**。`WAIT`（等远程结果、等 eval 尾巴）**不占执行权**是单进程多课程的关键；持资源的步才是串行的（用户口径：用任务队列防资源竞争）。
  - **每条任务必须先有盘上判据**（幂等 guard：shard 齐 / 权重落位 / 账本已有 `iteration` 行）——这条同时是「重放安全」与「扫账本」的同一件事。
  - 每课一份 `train-loop` 状态文件（`loop-state.json`，v1）：**账本永远是 SSOT**，该文件只是加速器，版本不认/字段缺失即从账本重建。
  - **用户定案四问（2026-09-18）**：① 直接做到 **R2c 单进程**（R2a/R2b 为途中产物）；② 任务粒度 = **细粒度步骤 + WAIT 让位**；③ 本机重资源（`local_ppo`/`eval_local`）**跨课排队、池容量 1**（rollout 子进程池不受影响）；④ checkpoint 缓存上限 = **并行课程上限（默认 5，`rl.checkpointCacheCourses` 可配）**，RSS 实测表是 R2c 上线前置。
  - **门禁扫账本（用户裁决）**：门禁语义逐条从「内存计数」改成「扫账本」；指标**按课程缓存**，只在开课/续跑读一遍，之后由写事件处**增量**维护。**不新造账本**：`training_log.jsonl` 已含 `run_start`/`iteration`/`gate_verdict`/`iter_error`/`circuit_break`/`run_complete`，且已有五个扫描器（`biz/resume.py`、`biz/gate_check.py`）——R2a 只是把它们收敛成**一份视图**。
- **R2a 已落地（2026-09-18）**：`nn-training/biz/train_ledger.py`（`LedgerSpec` + `LedgerView` + `load_ledger` 单遍扫描 + `apply_event` 增量）；`trainer/loop_core.py::_setup_common` 改由视图继承 `next_it`/`rotate_seed`/`ent_peak`/`train_sec_total`/`train_samples_total`/`kl_streak`/`ent_streak`/`stop_loss_streak`/`soft_remediate_count`；`biz/events.py` 新增 `stop_loss` 事件（止损连击的**状态转移**落账）且 `write_*` 返回事件 dict 供增量视图消费；`trainer/loop_guards.py` 加 `_ledger_apply` 钩子（写账本处顺手并入视图，观测失败绝不阻断训练）。
- **本相位**刻意**的行为变化**（就是修复内容，必须知道）：门禁计数**不再随进程重启清零**——① I2「提示类门 REMEDIATE ×N 即停腿」的计数读**整条账本**（换新 traj = 新纪元，重新计数）；② F4 `kl_streak`/`ent_streak` 与 `ent_peak` 同源继承（连击是**连续**计数，只有下一轮再越线才续，继承既真又无害）；③ 止损连击靠新事件跨重启成立。**刻意不继承**：`_consec_fail`（重试连击是单腿内的进程护栏，继承会「重启即秒死」）、`_zero_shard_streak`（口径还依赖 `_node_rollout`，而 `iteration` 行今天没有 `rollout_src` ⇒ 从账本重算会对节点轮报假事故——R2b 给事件加该字段后再接）。
- **备选与否决**：另造一份「训练状态账本」JSON 作 SSOT——否（两份真相必然分叉，且旧扫描器/读盘面/控制台全部已在读 `training_log.jsonl`）；把 13 步合并成「一轮一个任务」——否（用户定案：轮粒度下 executor 串行，一门课的远程等待会挡住其它课，单进程只省了进程数、没换来并行）；重启时把 torch 对象序列化恢复——否（Adam 动量序列化成本高且不必要：落盘面已有权重，缓存是**加速器**不是真相）；用 `Math.random`-式时间戳推断在飞任务 —— 否（幂等判据必须来自账本/盘面，不能来自时间猜测）。
- **违反后果**：任何新增的跨轮门禁计数若写在内存里，就会重演本轮修掉的 bug（重启即洗白，`c6-pickup3` 6 次 / `c6-bonus` 10 次 REMEDIATE 那类判据被无限延长）；任何绕过 `_ledger_apply` 的账本写入会让视图与盘面分叉（R2b/R2c 的任务幂等判据随之失效）；把 E 类轮内瞬态写进 `loop-state.json` 会让重启后的记账与真实轮次错位。
- **落地**：`nn-training/biz/train_ledger.py`（新）、`biz/events.py`（`write_*` 返回事件 + `write_stop_loss`）、`trainer/loop_core.py`（`_setup_common` 继承）、`trainer/loop_guards.py`（`_ledger_apply` + 止损落账）、`trainer/loop_steps.py`（`_record_iteration` 增量并入 + 类型声明）；回归 `nn-training/tests/worker/test_train_ledger.py`（15 例：与五个旧扫描器**奇偶**、增量==单遍、独立复算连击、坏行/未知事件透明）、`tests/worker/test_train_ledger_wiring.py`（3 例：`_setup_common` 继承 + 继承计数当轮停腿 + 空账本从零）。设计稿 `plan/r2-loop-task-queue.md`；进度 `docs/nn/training-stack.md` §10。
**R2b 落地（2026-09-18 续）——任务模型 + 在飞集，并把 `loop-state.json` 否决掉**：

- **`biz/loop_tasks.py`**（新，纯逻辑）：`Task`（`task_id = course:it:kind` = 幂等键；重试只动 `attempt`）·
  `TaskResult` 四态（`DONE`/`WAIT`/`RETRY`/`ABORT` + `is_terminal`；非法状态当场 `ValueError`）·
  `ROUND_TASKS` 13 步任务表 + `RESOURCE_OF`（只有 `rollout`/`volume_topup`/`ppo`/`eval_join` 占资源池，
  等待型/记账型不占——这正是单进程能服务多课程的机制）· `RoundFacts` + `already_done` + `pending_tasks`
  （幂等判据只认**盘上事实**）· `resolve_failure`（与现主循环逐条一致：冒烟作废原地重试 / 死腿立刻 ABORT /
  attempt≥5 才停）。
- **在飞集复用既有 WAL**（`biz/commit_journal.py`）：新增 `attach(phase, round, **extra)`（**不改状态机**，
  只补 `job_id`/`dispatch`/`ts`）与 `inflight()`（pending + 这些事实）。`_remote_ppo` 在 `publish_job`
  拿到 `jid` 后立刻 attach；`_commit_journal()` 在每 it 首次创建时把在飞集打进日志（`jid=` / `via push|pull`）
  ——「上一轮在等哪个 job、推给了谁」从事故考古变成一条日志。
- **★ `loop-state.json` 否决**：初稿计划每课一份状态文件（指针 + 在飞集 + 预算）。落地时发现盘上
  **已有三份权威来源**覆盖全部四类信息——账本（指针/预算/门禁计数，R2a 已接）、`commit_journal`（在飞集，R2b 已接）、
  `eval_log.jsonl` + `(stage,seed,wver)` shard 对账（eval 尾巴意图、预采子进程）。再写一份 JSON 就是**第二份真相**，
  且分叉方向恰是最贵的一种（续跑读错指针）。⇒ **不建该文件**；唯一允许留在内存的是调度器的唤醒条件
  （`WAIT.resume_at`、资源池票），它可重算。（备选否决：写它当加速器——内容为零独立信息并集，只带来分叉风险；
  把在飞集写进账本——账本是**事件流**，在飞是**状态**，混进去污染所有账本读者。）
- **落地**：`nn-training/biz/loop_tasks.py`（新）、`biz/commit_journal.py`（attach/inflight）、`trainer/loop_steps.py`
  （`_remote_ppo` attach + `_commit_journal` 在飞集日志）；回归 `tests/worker/test_loop_tasks.py`（10 例）、
  `tests/worker/test_commit_journal.py`（+4 例，含硬死注入带 `job_id`）。
**R2c-1 落地（2026-09-18 再续）——单进程调度核心 + 只读计划视图**：

- **`biz/loop_scheduler.py`**（新，纯调度：无 torch/网络/IO）：`PoolSet`（资源票 + 记账，
  未知池名/超发释放都 `PoolError`）· `CourseQueue` · `Supervisor`（公平轮转 → 闸门 → 单线程
  执行 → 四态收敛 → 按课隔离故障）。三条不可交易性质：**同一时刻只跑一个任务**、
  **`WAIT` 不占执行权**、**故障域按课**。
- **★ 票可跨 `WAIT` 保留**（`TaskResult.hold` / `waiting(hold=True)`）：写池闸门测试时发现的
  设计缺口——若 `WAIT` 一律还票，单线程调度器里容量 1 的池**永远不会挡住任何人**（任务体
  跑完即还票），池就失去意义；真实形态是「后台仍在干活」（本机 eval 的局还在子进程里跑）
  ⇒ 票必须能跨步持有，否则另一门课的本机重资源会插进来把机器压爆。
- **「能跑的都被池挡住」= 没事可做**（`step()` 返回 None，`blocked_courses` 保留事实）——
  否则调度器会在两门互相挡住的课之间空转。
- **`trainer/loop_plan.py`**（新，唯一碰盘之处）：账本指针 → `RoundFacts` → `pending_tasks`；
  shard 结算数；`commit_journal` 在飞集；课程发现（`<traj-root>/*/training_log.jsonl`）。
  判据永远朝「不跳」保守：算不出的留 `False`/`0`。
- **`nn-training/trainer/run_rl_cluster.py`**（新入口）：**一个进程**读出所有并行课程的「下一步 /
  待办 / 在等谁（含 job_id）/ 被什么挡住 / 关键事实」。当前只提供只读计划视图（不训练、
  不发布、不等待）——它零训练行为变化且立刻可用，同时把调度核心放在真数据上跑通。
- **R2c 拆相**：R2c-1（本段，机制 + 只读面）已完成；**R2c-2** = 抽 `run_one_round` 并把任务体接到
  真 `TrainingLoop` + 三处长等待改 `WAIT` 让位 + 进控制台 + `checkpointCacheMb` 真机 RSS 实测。
- **落地**：`nn-training/rl/{loop_scheduler,loop_plan}.py`、`nn-training/trainer/run_rl_cluster.py`（均新）；
  回归 `tests/worker/test_loop_scheduler.py`（18 例）；实测：`trainer/run_rl_cluster.py --traj-root tmp` 在真课程
  目录上读出 5 门课的计划（含「采集完成但账本未结算」那一态）。
**R2c-2 落地（2026-09-18 三续）——轮体抽出 + 任务体↔引擎的桥 + 假件集成测试**：

- **`TrainingLoop.run_one_round(it) -> RoundOutcome`**：`run()` 的 190 行轮体搬进新方法，`run()` 退为
  驱动器（预采 join / 预算到点检查 / 按 outcome 施加 / 收官 drain / 停车）。控制流**只搬不改**：
  `break`→`ROUND_STOP`、包导出 `return`→`ROUND_BUNDLE_EXIT`、冒烟作废→`ROUND_SMOKE_STOP`、
  三种 `it -= 1`→`ROUND_RETRY`、轮末→`ROUND_NEXT`。**`RoundOutcome.it` 必须带回**：半离线整段
  `_remote_run_segment` 一次吃掉 it..end_it，丢掉返回值就会重跑已跑完的段。
- **`trainer/loop_runner.py`**（新，任务体↔引擎的**唯一**桥）：`planner` 读账本给指针（SSOT）、
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
- **落地**：`nn-training/trainer/loop_core.py`（抽方法 + 常量/`RoundOutcome`）、`trainer/loop_runner.py`（新）、
  `e2e/test_loop_supervisor_integration.py`（新）；拼接用一次性脚本（逐 hunk `assert` + `ast.parse`，
  跑完即删）。
- **仍未做**：R2c-3（轮内细粒度 `RoundContext` + 三处长等待真轮询化 + 控制台视图 + 真机 RSS 表）·
  R2d · R2e。
**R2c-3 余下（2026-09-18 五续）——远端 PPO 三相拆分：让位点从「每轮」下沉到「这一步」**：

- **问题**：拆相前 `_remote_ppo` 是一个 400 行阻塞函数（打包 → 发布 → 阻塞轮询 → 三重校验落位）。
  它前面**不能**挂让位闸门：闸门跑在步骤之前 = 还没发布就被挡住 ⇒ 永远等不到回传。于是单进程
  多课程下，「等云机」这一段仍然会堵住整条调度链（25 分钟量级）。
- **拆法：同一任务内的三个相位，不是三个任务**（`trainer/loop_steps.py`）：
  `_remote_ppo_publish`（打包 + 发布 + 直推提交 → 会话）/ `_remote_ppo_probe`（**非阻塞**问一句）/
  `_remote_ppo_fetch`（阻塞等：组合路径 / 节点轮 / 整段）/ `_remote_ppo_land`（校验落位 + 记账 +
  结算字段）。`_remote_ppo` 保留为三者的**组合入口**，四个既有调用点（本机轮 / 节点轮 / 半离线整段 /
  全离线导出）行为不变。
  **为什么不拆成 `ppo_publish` + `ppo_wait` 两个任务**：三相共享一份会话（jid / 超时预算 / 打包墙钟 /
  运输方式），拆任务就得把它序列化到盘上才能跨任务传 —— 那正是 R2b 否决过的「第二份真相」
  （§2026-09-18-goalnn-r2-loop-task-queue 的 `loop-state.json` 段）。任务粒度只决定让位点密度。
- **会话是纯数据、住在轮内上下文里**（`worker/loop_round.RemotePpoJob`，`RoundContext.remote`）：
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
- **`ctx.resumable`：让位点由「谁在驱动」决定**（`worker/loop_round.RoundContext`）。细粒度驱动器
  （`LoopRunner`）造上下文时置 True ⇒ 未就绪就 `wait_for`；组合路径 `run_one_round` 保持 False ⇒
  就地阻塞取结果（拆分前语义）。于是「组合路径没有让位点」从一个运行期异常（`RoundYieldError`）
  变成了一个**事实字段**。
- **`WAIT_HOOKS` 里不再有 `ppo`（且不得加回去）**：表里的闸门跑在步骤之前，对 ppo 来说那是「还没发布」。
  让位已由 `step_ppo` 自己产生（`tests/worker/test_loop_runner.py` 把「不在表里」写成断言）。预采那一处不变。
- **失败判决只留一份**：`_handle_remote_failure`（从 `_remote_ppo_or_degrade` 的 except 分支抽出，
  逐字搬移）；发布 / 取结果 / 落位任何一段失败都进它——三段各自演化出不同的连败计数/停腿口径是本仓
  最贵的一类分叉。`_remote_ppo_or_degrade` 保留原签名与原 docstring（有 9 个既有用例钉着它）。
- **落地**：`nn-training/{remote/hub_client.py,remote/push_client.py,trainer/loop_steps.py,biz/loop_round.py,
  trainer/loop_round_steps.py,trainer/loop_runner.py}`；拆分用一次性脚本（逐 hunk `assert` + `ast.parse`，
  被搬的行逐字节不变，跑完即删）。回归：`tests/remote/test_remote_probe.py`（14）·
  `tests/worker/test_remote_ppo_phases.py`（8）· `e2e/test_loop_supervisor_integration.py`（ppo 让位用例改为
  驱动真三相）· `e2e/test_push_mode_integration.py`（+3：发布相位提交 / 换节点重提交 / 探针目标）。
- **仍未做**：`Supervisor` 进控制台（单例卡片 + 每课队列视图）· `checkpointCacheMb` 真机 RSS 实测表。
**R2d 写的一半（2026-09-19 八续）——单进程 supervisor 真的能跑了：进程级/课程级/步骤级三层各做一次**：

用户 2026-09-18 指令（「训练循环任务队列化 —— trainingLoop 由一个进程服务所有并行课程」）的落地前半：
R2c 造好了调度器与任务体，但**没有驱动者**（至今仍是「一门课一个 `trainer/run_rl.py` 进程」）。本轮交付
`trainer/loop_serve.py`（驱动者）、`biz/engine_pool.py`（N 课共享的 torch 栈缓存）、`biz/log.py` 的**行路由**
（单进程下的课程归属），入口 = `trainer/run_rl_cluster.py --serve --courses a,b`。

**分层纪律（每层各做一次；越界做两次会伤到既有护栏）**：
- **进程级一次**（`prepare_process`）：UTF-8 stdio / faulthandler / `chdir(repo)` / 启动前 `git push`
  （`.git_push.lock` 串行化）/ 节点升级分支锁到训练机分支 / bun 存在性。每课各做一次 = 每课都推一遍 git。
- **课程级一次**（`open_course`）：参数解析（与 `trainer/run_rl.py --course` **逐字段一致**；对拍见
  `tests/trainer/test_serve_wiring.py::test_course_args_match_run_rl_echo_config`——oracle 是在子进程里跑
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
   `[课]` + 镜像写进该课 `out_log`（`biz.log.open_course_sink` / `prefix_scope`）。无前缀时与改造前逐字节相同。
3. **引擎池的驱逐 = 响亮的一次「重启」**：容量默认 = 并行课程上限 5（§6.1 实测每课 MB 级），字节上限
   256MB 只是第二道保险；驱逐时**必须**记一行「谁被驱逐 + 权重可从 `args.out` 复原但 Adam 动量重置 + 若
   常发生请调大上限」，且**绝不驱逐在用引擎**（任务中途抽走栈会撞 `None.load_episodes`）。退出时释放全部
   栈（只关自己建的池——注入的池归调用方）。
4. **初次入队的粒度必须匹配执行体**：细粒度给 13 步任务表，轮粒度给**单个** `round` 任务；混了就是把 13 个
   步骤 kind 塞进只认 `round` 的执行体（响亮 ABORT，不是静默跳步）。初次入队用 `course_facts` +
   `pending_tasks(round_tasks(...))`（判据原语与只读计划视图同源），**不**调 `plan_course`——那会连 CLI 表格
   用的展示面字段（累计量/verdict）一起算。

**回归**：`tests/worker/test_engine_pool.py`(9：惰性/命中/LRU/两道上限/不驱逐在用/响亮超预算/释放钩子/快照) ·
`tests/common/test_log_router.py`(6：无前缀逐字节不变/作用域还原含异常/镜像到本课/坏 sink 不崩/重复注册换句柄) ·
`tests/trainer/test_serve_wiring.py`(8：轮转交替 a,b,a,b / 13 步全表 / 让位让别的课跑完 / 容量 1 时驱逐重建再 setup /
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
  调度器（`trainer/loop_control.py`）。hub 挂了也能用，**文件本身就是状态**（对比 hub 的 course-mode 是
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

**回归**：`tests/trainer/test_serve_wiring.py`(+8：零课程照跑 / 中途出现的课自动入队 / 开不起来的课只试一次 /
暂停只停被点名的课 / 恢复从原处接着跑 / 坏控制文件保守继续 / CLI 发现模式与 `--control-file`/`--mode` 转发) ·
`tests/trainer/test_loop_control.py`(22：解析边界 / 非法名不废整份意图 / 保守方向 / 幂等 / 坏文件不被覆盖 /
回执形状与原子性 / **未变化不写盘** / pid 存活语义) ·
`dashboard/tests/loop-control.test.ts`(22) · `dashboard/tests/web-app-loopqueue.test.ts`(+9【今 → `dashboard/tests/web-loop-queue.test.ts` + `dashboard/tests/web-app-coursematrix.test.ts`】：四态 / 文案 /
徽标 / 只读不渲染开关 / route+cache 接线) · `dashboard/tests/server-api-loop-queue.test.ts`(+1)。
门禁：nn python gate **1511 passed / 4 skipped**；dashboard **621 passed**；三份 bundle + 根 `bun run check` +
`bun run build` 绿。

**仍未做**：单例 `trainingLoop` 卡片的分组（R3-3）· **R2e**（多课 × 假 worker/假 PPO 的 e2e + 真机双课
并行跑通——serve 的真机行为需要人验，本轮改动同样只在假件下验证过）。

**R2c-3 收口（2026-09-19 七续）——真机 RSS 实测：checkpoint 缓存上限的真实约束是「数量」不是「字节」**：

- **为什么要实**：单进程 supervisor 要为 N 门课各持一份 torch 栈（model + Adam + 冻结 ref），
  `checkpointCacheCourses` / `checkpointCacheMb` 的默认值此前**只能拍脑袋**（本机 0 卡、remote 为主）。
  plan §6 把这张表列为 R2c 上线前置条件。
- **实测（`nn-training/worker/scripts/measure_checkpoint_rss.py`；本机 CPU-only torch 2.7.1+cpu）**：
  每课增量 per-tick **≈1.3MB**（70,216 参：model 0.7 + Adam 0.6，无 ref）/ intent **≈1.8MB** /
  goal **≈1.8MB**（各含一份冻结 ref）；**torch 基线 294.5MB 与课程数无关**；
  N=5 混合档累计 **294.8 → 301.8MB（仅 +7MB）**。
- **定案**：**`checkpointCacheMb` 是第二道保险，真实约束是数量** `checkpointCacheCourses`
  （= 并行课程上限 5）。推荐 `checkpointCacheMb = 256MB`（≈130 课，正常永不触发）；真触发即说明
  「某课的栈长得离谱」——那时该被看见（响亮拒绝缓存），不得静默驱逐。
  ★ **2026-10-02 更正**：这两个键**从未被生产代码读过**（`run_rl_cluster --serve` 不传参、也无
  CLI flag）⇒ 「可配」作废。现语义见 `DECISIONS.md §2026-10-02-goalnn-engine-pool-midround-pin`：
  数量顶**自动 = max(5, 开课数)**（理由：真约束是字节顶，而「开课数」是操作面事实），
  且**轮内**（`CourseQueue.mid_round`）的课一律不驱逐——7 门课 > 死值 5 曾导致引擎互逐、
  被逐课在轮内被重建 ⇒ `_node_rollout` 未初始化 ⇒ 每秒重启、0 条 `iteration`（h4-hurt-f75）。
- **口径诚实性（这张表最容易被读错的两处，已写进脚本 docstring 与用例）**：① **先暖一次再测**
  ——torch 惰性初始化（首次 kernel 选择 / 分配器建池 / Adam 首步）会让**第一份**栈看起来贵两个
  量级（实测 78MB vs 真值 0.75MB）；② **栈必须活着**——被 gc 回收后第二课的增量会变成 0
  （分配器复用），累计曲线就是假的。实测工具本身有回归：
  `nn-training/tests/worker/test_measure_checkpoint_rss.py`（7 例：推荐值取整/余量、候选表恒有默认架构兼底、
  表格必须自带「测的哪份权重 / 跳过了谁 / 理论 vs 实测」、真造一份栈的量级保护带）。
- **不测且写明的**：单轮 episodes/chunk 缓冲是**另一处峰值**（由 `mb` 与本轮样本量决定）——
  它是每轮瞬态、且本机 PPO 池容量 1 ⇒ 同一时刻只有一份；已由 forensics 埋点，不属缓存上限管的常驻量。
- **仍未做（缓存实现本身）**：随「单进程 supervisor 真上线」的相位落地（R2d/R2e）。今天没有持有者，
  先写就是无消费者的投机代码；本表 + 上述两个默认值就是它上线时需要的全部输入。
**R2c-3 余下（2026-09-19 六续）——控制台接线：单例调度器卡片 + 每课队列视图（「在等什么」）**：

- **数据源 = python 只读入口，★ 控制台不得在 TS 重算判据**（防再犯条款）：卡片走
  `nn-training/trainer/run_rl_cluster.py --json`（训练侧只读：不训练/不发布/不等待），与 CLI 表逐字段同源。
  指针 → `RoundFacts` → `pending_tasks` / `waiting_state` 这套判据已在 python 侧被用例钉住；
  在 TS 里照账本重写一遍 = **第二份真相**（同 `loop-state.json` 段），两边会以不同速度演化。
  谁想「顺手在 TS 里读账本省掉一个子进程」，先重读本段：省下的是亚秒级冷算，换来的是两套语义。
- **成本与缓存**：`dashboard/src/server/api/loop-queue.ts`——懒算 + **TTL 10s** + 单飞 + 服务启动
  暖一次（避免 SSR 首屏等子进程）。TTL 比 hub 观测面（5s）宽，因为事实变化的粒度是「一轮」
  （分钟级）而冷算要起一个 python。读失败（解释器缺失 / 超时 / 输出不可解析 / 形状不符）**不抛**：
  视图带 `error` 上屏，UI 显因 + 空态——观测面坏掉不该把整页 `/api/state` 带崩（与 hub 总览、
  隧道 A/B 同口径）。
- **「在等什么」的判据留在 python**（`trainer/loop_plan.py::waiting_state`，CLI 与控制台同一个函数）：
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
  `server/server.ts`（启动暖一次）；训练侧 `nn-training/trainer/loop_plan.py`（`waiting_state`）+
  `trainer/run_rl_cluster.py`（抽 `build_rows`、JSON 加 `waiting`、表里也打印）。
- **回归**：`dashboard/tests/server-api-loop-queue.test.ts`（13：解析容错 / 结果翻译四条失败分支 /
  TTL 复用 / 单飞 / 在训合并 / state 注入）· `dashboard/tests/web-app-loopqueue.test.ts`（12【今 → 面板 `dashboard/tests/web-app-coursematrix.test.ts` / 视图 `dashboard/tests/web-loop-queue.test.ts`】：SSR 每课
  一行 / 四态着色 / 未在训淡档 + 悬停 / 排队与页脚 / 读失败显因 / 接线断言）·
  `nn-training/tests/trainer/test_loop_plan_waiting.py`（14：优先级 / 未知配额不编分母 / 真读盘组装）。
  子进程是**可注入接缝**（同 `deliver_zip` 导入惯例）⇒ dashboard 用例不跑 python，python 用例不碰
  控制台，两边各测自己那一半。
- **仍未做**：① `checkpointCacheMb` 真机 RSS 实测表（R2c 上线前置条件）；② R2d 操作面（入队 / 暂停 /
  单例 `trainingLoop` 卡片）；③ R2e e2e（单进程多课 × 假 worker/假 PPO）。
**R2c-3 落地（2026-09-18 四续）——轮内切成 13 步 + 让位闸门（`WAIT_HOOKS` 表）**：

- **`RoundContext` 与 13 步表**：`biz/loop_round.py`（纯数据 + 表，无 torch/网络/IO）定义
  `RoundContext`（轮内跨步可见的量：`pairs`/`dist_cfg`/`t_rollout`/`seg`/会被半离线整段推进的
  `it`）与 `STEP_ORDER`（= `ROUND_TASKS`，**单一来源**）+ `STEP_METHOD`（kind → 引擎方法名）。
  步骤实现在 `trainer/loop_round_steps.py`（`RoundSteps` mixin，逐行从轮体搬来）；组合路径
  `run_one_round` 与细粒度驱动器**都从同一张表取步骤** ⇒ 加一步必须同时进表，两条驱动不可能漂移
  （`tests/worker/test_loop_round.py` 断言表与实现一一对应）。轮内状态**不长在引擎实例上**（§2.2 无隐藏
  状态：单进程多课程会互相覆盖）。
- **顺序错纠正（真发现）**：`precollect_join` 必须排在 `prepare_iter` **之前**——它产出的是本轮
  `it{it}` 的 shard，而 `prepare_iter` 靠 `completed_pairs` 看盘决定「保留续跑 / 清场重建」；
  反了就把上一轮的预采整个作废。
- **让位闸门 = 一张表**（`trainer/loop_runner.WAIT_HOOKS`：kind → 引擎钩子名）。**钩子不存在 ⇒ 不让位**
  （行为与改造前逐字节一致）⇒ 表可以只先装能吃的两处。
- **★ `eval_join` 刻意不进表**（防再犯）：本机 eval 局的墙钟是「藏在下一轮 rollout 里」的
  （`_eval_tail` 交棒 → `_dispatch_delayed_eval` 入口收拢，2026-09-17 用户口径）。给它加让位 =
  把那条尾巴重新串回轮边界，正好抵消当初压掉的软等窗口。用例把这个缺席写死（`test_loop_runner.py`）。
- **★ `ppo` 闸门必须在「发布之后」**：今天的 `_remote_ppo` 是一个阻塞函数（打包→发布→阻塞轮询
  →校验落位）。在它前面加闸门 ⇒ 还没发布就被挡住 ⇒ 永远等不到回传。要装它必须先做「发布 / 等结果 /
  落位」三相拆分；**宁可不装也不装错**（本轮不装，表里留位 + 理由写在 `loop_runner` docstring）。
- **已装的一处：预采**（`precollect_join` → `TrainingLoop.precollect_ready`）。`join_precollect_child`
  旧形态每 2s 轮询、上限**1 小时**——单进程多课程下这就是「一个慢子进程拖垮所有课」的入口。
  判据抽成 `trainer/rollout_phase.precollect_ready`（非阻塞：句柄为空 / 子进程已退出 / 就绪 shard ≥ 半波），
  步骤内部循环**改成调同一个函数** ⇒ 「调度器认为可以往下走」与「步骤进去真的不阻塞」不可能分叉。
  `join_precollect_child` 的外部语义（含 1h 超时 terminate）逐条不变。
- **读面补全「在等什么」**：`WAIT` 时把原因落到队列（`q.reason`），并在推进/收官时清空——
  状态 + 原因 + 在飞 `job_id` 三者合起来才够定位；过期原因比没有原因更坏。
- **落地**：`nn-training/rl/{loop_round,loop_round_steps,loop_runner,loop_scheduler,loop_core,loop_tasks}.py`
  （前两个新）、`nn-training/trainer/rollout_phase.py`（抽判据）；回归 `tests/worker/test_loop_round.py`（13 例）、
  `tests/worker/test_loop_runner.py`（5 例）、`tests/trainer/test_precollect_ready.py`（7 例）、
  `e2e/test_loop_supervisor_integration.py`（+3 例：13 步逐一走完 + 步级轮转 / ppo 处让位 /
  预采处让位，账本形状与轮粒度逐行一致）。
- **仍未做**：① `ppo` 的三相拆分（让位点从「每轮」变成「每步」的最后一块）；② `Supervisor` 进控制台
  （单例卡片 + 每课队列视图）；③ `checkpointCacheMb` 真机 RSS 实测表（R2c 上线前置条件）。

---

### §2026-09-19-goalnn-serve-bc-course（2026-09-19，R3-4：单进程 supervisor 也能带 BC 课）

- **背景**：多课程并行之后训练侧收敛为**一个进程**（`trainer/run_rl_cluster.py --serve` → `trainer/loop_serve.py`，§2026-09-18）。但 BC 课当时仍只能靠 `trainer/run_bc.py` 单开一个进程——理由不是需求，而是**形状**：`trainer/run_bc.py` 的 `main()` 是一整段 procedural 编排（解析 → 采集 → 发布 → 阻塞等待 → 落位归档），没有 supervisor 要的引擎子集（`_setup` / `run_one_round` / `finish_course` / `release_torch` / `ledger_next_it`）。用户口径「一个 trainer 进程服务所有课程」⇒ BC 必须能被同一个进程驱动。
- **决定**：把 BC 的编排体**逐字节**搬进 `trainer/bc_loop.py`（`BcLoop` 引擎 + `BcRuntime` 解析 + 纯函数），`trainer/run_bc.py` 退为**入口薄壳**（进程级一次性副作用 + 阻塞式驱动）；`serve` 按课程种类分派引擎与粒度。
- **关键的四个取舍（都是「另一条路更好写但不该走」）**：
  - **不给 BC 造第二份一轮实现**：引擎里一轮的三段（开轮 → 等回传 → 落位归档）与单课程路径**共用同一份代码**。BC 的续训按 `jid` 存（hub `/jobs/{jid}/resume`、worker 本地 `bc-resume/<jid>`）⇒ 任何一次重发布 = 新 jid = **从头训**；两份实现里只要有一份漏了「先认领盘上 job」，代价就是一轮 GPU 时间。
  - **BC 课恒为「一轮 = 一个任务」**，不进 13 步表：13 步是 RL 的一轮（rollout/ppo/eval/门禁/记账），BC 的一轮是「采语料 → 发布 → 等回传 → 落位归档」。硬套 = 给 BC 发它不认识的待办（执行体响亮 ABORT，不是静默跳步）。粒度由 `loop_plan.round_tasks_for(course, it)` 单点决定（按课程种类选表），`LoopRunner(step_mode=False)` 由 `build_factory` 按同一判据设置。
  - **指针的语义归引擎**（`LoopRunner._ledger_next_it` 的 `ledger_next_it` 钩子）：BC 的「跑到第几轮」= `bc_round_completed`（`biz/bc_ledger.py`），RL 的 = `iteration`（`LedgerSpec`）。在桥里写死一种就是给另一类课程读错指针（症状：BC 课永远停在 it1）。读面（`loop_plan.course_facts(course=...)`）走**同一份**判据，`course_kind` = `curricula/<课>.bc.jsonc` 是否存在（与控制台 `isBcCourse` 同源，不靠账本事件推断——刚建的 BC 课账本是空的）。
  - **让位点只落在「等远端」这一段**：`BcLoop.run_one_round` 每次最多做一件事，等 GPU 回传时返回 `ROUND_WAIT`（新增的第三种轮终态：**本轮未完**，既不是失败也不是完成）⇒ 调度器把执行权交给别的课，过一会儿回来问同一轮。本机训练（`--local`）与 push 直推照旧阻塞（墙钟花在本机/邻居节点上，没有可让的余地）——与 RL 的 `eval_join` **刻意不进让位表**是同一条纪律。
- **备选与否决**：让 serve 把 BC 课委托给 `bcRound(course, it)` 子任务类型（另一条执行路径）——否（第二条「一轮」实现，正是上面第一条要禁的）；把 BC 课也拆成细粒度步骤（采集/发布/等/落位各自一个任务）——本相位否（收益只是更细的让位点，而 BC 的墙钟几乎全在「等 GPU 回传」这一段，已让位；留作需要时再下沉）；让 `trainer/run_bc.py` 变成只 import 新模块的 re-export 壳（保持旧测试导入路径）——否（同一对象两个名字会让「谁是家」含混，改为把测试指向新家）；BC 课仍用一个专属进程（本轮不动）——否（与用户口径冲突）。
- **违反后果**：任何在 `trainer/bc_loop.py` 之外再写一遍「一轮」（含控制台/工具脚本自己发布 job）都会重新引入「重发布 ⇒ bc-resume 失效 ⇒ 从头训」这条最贵的错误；任何把 BC 课按 RL 读账本的地方都会得到 `next_it=1`（看起来「这课没在训」）；给 BC 课发 13 步任务表会让该课在第一次执行时就 ABORT。
- **落地**：`nn-training/trainer/bc_loop.py`（新，1395 行；14 个函数体从 `trainer/run_bc.py` **AST 逐字节搬迁**、`_append_ledger`/`_ledger_bc_epoch`/`_run_epoch_eval`/`_finish_all_rounds`/`_archive_round` 去下划线）· `biz/bc_ledger.py`（新，BC 指针/完成集/收口 job 的单一读面）· `biz/loop_round.py`（新终态 `ROUND_WAIT` + `RoundOutcome.detail`）· `trainer/loop_runner.py`（`ROUND_WAIT` → `waiting(..., jid=..)`；`ledger_next_it` 钩子）· `trainer/loop_core.py`（单课程驱动器对 `ROUND_WAIT` 的阻塞语义：退避重问同一轮）· `trainer/loop_plan.py`（`course_kind` / `round_tasks_for` / `course_facts(course=)`）· `biz/bc_config.py`（`is_bc_course`）· `trainer/loop_serve.py`（`_open_bc_course` + 工厂分派 + 入队粒度）· `trainer/run_bc.py`（薄壳）· `trainer/run_rl_cluster.py`（`build_rows` 带 `kind` + 人读表加种类列 + BC 行的 facts 行不再摆一排 RL 的零）· 控制台：`web/view/loop-queue.ts`（`LoopCourseKind` + `kindBadge`/`stepTitle`/`pendingTitle`）· `web/app/panels/LoopQueue.tsx` · `web/app/app.tsx`（解除 `isBc` 门控）· `web/theme.css`（`.tc-loopq__kind--bc`）。回归：`tests/worker/test_bc_ledger.py`(9) · `tests/trainer/test_bc_loop.py`(16) · `tests/trainer/test_serve_bc.py`(5，serve × BC 集成：让位/驱逐认领/粒度/锁/故障隔离) · `tests/worker/test_loop_plan_bc_rows.py`(9，读面：种类/指针/在飞/两行共存) · `dashboard/tests/web-app-loopqueue.test.ts`（+3【今 → `dashboard/tests/web-app-coursematrix.test.ts`】：BC 行与 RL 行并列 / 悬停各说各的 / 卡片不再被 `isBc` 门控）· `dashboard/tests/server-api-loop-queue.test.ts`（+1：`kind` 的保守默认）· 既有 BC 用例改为指向新家（`tests/worker/test_bc_course.py`、`tests/trainer/test_remote_transport.py`、`e2e/test_bc_epoch_e2e.py`）。进度 `docs/nn/training-stack.md` §19 / `docs/nn/console.md` §6；plan `plan/r2-loop-task-queue.md §8 R3-4`。
- **控制台半（2026-09-19 同日接上）**：调度器卡片是**跨课程**卡（一次列出所有账本可发现的课），BC 行与 RL 行**并列**——读面（`trainer/run_rl_cluster.py --json` 的 `kind`）早在 R3-4 就通了，缺的只是 UI。三处关键决定：
  - **行上带课程种类**（`build_rows` 的 `kind` / 视图层 `LoopCourseKind`）：BC 的指针（`bc_round_completed`）、粒度（单个轮任务）、在飞来源（账本 `job_pending`）与 RL 全不同，不带种类 UI 只能猜（猜错就把 BC 读成一排看着像真的零）。种类判据 = `curricula/<课>.bc.jsonc` 是否存在（`loop_plan.course_kind`，与控制台 `isBcCourse` 同源）。
  - **`kind` 缺省/未知一律按 `rl` 渲染**（保守方向单侧）：python 比控制台旧时（还没这个字段）少一个徽标只是少信息；凭空空贴 BC 标签则会对外宣称「一轮 = 一个任务」（而它有 13 步）——假承诺比缺标签贵。`BC` 这种大小写不符也不认（只认 python 的确切取值）。
  - **卡片解除 `isBc` 门控**：`stateView.isBc` 说的是**当前查看的那门课**，而这张卡是**跨课程**的——用它门控是范畴错误，后果是「选中一门 BC 课 ⇒ 整张卡片消失」，于是 BC 课在调度器视图里根本不存在（而 BC 课正是最需要看「在等哪个 GPU job 回传」的那种）。BC 行只多一个 `BC` 徽标 + 换成「一轮 = 一个任务」的悬停文案（不出现 RL 的门禁/verdict/KL 字眼）。
- **未做（明确记录，不是漏）**：① 真机「一个 serve 进程带 BC + RL 双课」的实跑（本轮全在假件下证明逻辑，与 R2e 同一口径）；② BC 一轮再下沉成细粒度步骤。

---

### §2026-09-19-goalnn-shared-trainer-single-process（2026-09-19，R3-5：trainer 收敛为「一个进程服务所有课程」）

- **背景**：R3-3 分族时留了一条明写的边界——分族只交付**读面**，账本键的真收敛未做：控制台仍按课起 `trainer/run_rl.py --course`，于是「BC 课 A + RL 课 B」要两个进程，尽管 ① R2d 已造好单进程驱动者（`trainer/loop_serve.py`：按课锁 / 按课日志镜像 / 引擎池 / 故障隔离 / 暂停恢复）、② R3-4 让同一个进程也能带 BC 课。用户口径：「hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程，就能同时支持所有并行训练课程」。
- **决定**：`trainingLoop` 进 `SHARED_COMPONENTS`（账本槽恒 `''`，与 hub/隧道同一张表同一套哨兵），控制台一律拉起 `trainer/run_rl_cluster.py --serve`；卡片自动从课程面落到**服务面**（族归属是 `componentScope` 的函数，R3-3 已把这条路修好——本轮只改判据，不改 UI）。
  - **课程 = 文件系统事实**（`<traj-root>/<课>/training_log.jsonl` 存在，与训练侧 `trainer/loop_plan.discover_courses` 同一判据）⇒ 启动时**不给 `--courses`**，「先起 trainer、后加课」不需要重启进程；代价是控制台得替**这门课**把账本文件建出来（`prepareCourseForSharedTrainer` ②）。
  - **每课旋钮住 rl-config**（`courses.<课>.{remote_transport,remote_hub_url,remote_degrade_after}`；新 `stack/course-knobs.ts` 是唯一写面，python `loop_serve.apply_course_machine_overrides` 在**开课时**施加：白名单 + 值域校验 + 逐键打印）。单进程没有「这门课的 flag」这一说（命令行只有一份）。**绝不写进 `curricula/*.jsonc`**——课程文件字节 = `course_fp` 语料血缘/熔断口径（D14），往里加一个传输旋钮，熔断会把同一份语料读成新语料。已开课的课程要**重开**才换传输（暂停该课 → 重启共享 trainer / 等引擎驱逐），这一点写进了动作返回值。
  - **幂等早退仍做本课准备，但准备失败不改事实**：早退前照样写本课旋钮 + 建账本（不写则「给这门课换成 push」是静默无效的动作），但准备阶段抛错时**不冒泡成通用失败**——消息必须以「已在运行 / 服务所有课程」开头、失败只说本课。理由很具体：说成「trainer 启动失败」会诱使操作员去停/重启它，而**停共享 trainer = 停掉所有课程的训练**。
  - **进程级单实例锁**（`nn-training/.run_cluster.lock`，`lockName('', 'run_cluster')`）：按课锁拦不住「两套调度器各跑一半课程，每门课都恰好只有一个跑者」。python `--serve` 自己响亮拒启 + 控制台在 spawn 之前先探（错误落在动作返回值里，不在日志里）；停机时释放。
  - **旧形状换代接管**：存活的 per-course trainer 由 `stack/hub.ts::supersedeLegacyInstances('trainingLoop')` 显式停掉并清账（与 hub/隧道同规），死条目也清；`restartSpecFor('trainingLoop', <课>)` 对**每课条目返回 null**——用共享 spec 重建一个每课条目 = 两套调度器抢同一批 traj。
  - **停止语义**：停 trainer = 停**所有**课程的训练（消息里必须说出来，否则操作员以为只停了当前查看的那门课）；停单门课用调度器卡片的「暂停」（控制文件 `tmp/loop-control.json`，只影响调度，队列/账本一个字不动）。
  - **「在训」判据随之改口径**：registry 里不再有每课条目，故「哪几门课在训」= **调度器（`sharedTrainerAlive()`）+ 该课未收官**（`loop-queue` 的行），总览与课程 select 高亮同源。
- **备选与否决**：① 给 `--serve` 传 `--courses <课表>`——否（进程绑死课程表，「先起 trainer、后加课」当场失效，而 hub 已确立「课程 = 发现」的口径）；② 每课传输旋钮塞进 `curricula/*.jsonc`——否（熔断口径，见上）；③ 共享 trainer 改住扁平单例键——否（旧账本里的 per-course 条目必须继续可见、可枚举、可停止；静默失监督是事故。共用一张表天然做到，靠槽 `''` 区分）；④ 顺手把 `localWorker`/`workerServe` 也收敛——**不做**（本机 PPO worker 的语义就是「poll **本课** hub」，每课一个是对的；`worker_server` 的语义轴是节点，见 `NODE_FACE_COMPONENTS`）。
- **违反后果**：任何「按课起一个 trainer」的残留路径都会与共享调度器抢同一批 traj（症状：同一轮被两个进程各跑一半、账本交错、锁语义失效）；任何把本课准备失败说成 trainer 启动失败的话术，都会让操作员停掉所有课程。
- **落地（训练侧）**：`nn-training/trainer/loop_serve.py`（`apply_course_machine_overrides` + 覆盖叠加与优先级 + 非法值响亮）· `nn-training/trainer/run_rl_cluster.py`（`--serve` 进程级单实例锁 + `--ppo` 直通）。回归：`tests/worker/test_serve_course_overrides.py`(10：覆盖叠加/优先级/非法值响亮/未知键不认) · `tests/trainer/test_serve_wiring.py`(+1：单实例锁接线)。
- **落地（控制台）**：`core/registry.ts`（`trainingLoop` 进共享表）· `core/slots.ts`（锁名归一）· `core/types.ts` · `launch/cli.ts` · `stack/specs.ts`（`trainerServeSpec`，发现模式 argv）· `stack/course-knobs.ts`（新：机器侧旋钮写面）· `server/actions/{start,stop,restart,preset,smoke,train-smoke}.ts`（启动/换代/锁/停止语义/冒烟独占）· `server/api/{overview,loop-queue,state-view}.ts`（在训判据）· `web/`（视图同源）。回归：`tests/training-shared-trainer.test.ts`(9：argv 不绑课程表 / 每课条目拒重建 / mode→transport 逐条 / 账本=发现判据 / 进程级锁 / 停止语义 / 冒烟独占) · `tests/training-console-busy.test.ts`(9：**幂等早退仍释放 busy 键** + **准备失败不许冒充「启动失败」**，夹具已重定向 traj 根与 rl-config——此前它把断言挂在「本机 tmp 恰好有没有权重文件」上)。进度 `docs/nn/training-stack.md` §20。
- **未做（明确记录，不是漏）**：① 真机「一个 serve 进程同时带 RL 课 + BC 课」的实弹运行（本轮全在夹具/假件下证明逻辑，与 R2e 同口径）；② `workerServe` 账本键的轴仍是课程（展示面已按节点例外声明，账本键未动）；③ `TrainLaunchModal` 的精简（模式仍按课选，落点已改为课程旋钮）。

---

### §2026-09-19-volume-continuous-quota（2026-09-19，用户指令：退役离散补波 → 配额感知连续派发）

- **背景**：x20 分关样本缺口/wave_cap 复盘 —— 全局 est × 等 G0 盖不住 1 命下变体间
  产量方差（通关率波动 → 局均 nSamples 波动）。用户裁定：**完全去掉补波机制**，loop
  实时观测 samples 分布，即将足额不再派、差额大者多派。
- **规则 VOLUME_RULE_V2**（`biz/volume_quota.py`）：分关配额 `ceil(target/n_stages)`；
  每批 `allocate_stage_games`：`collected+inflight*est_s ≥ quota` → 软停；差额按
  `ceil(shortfall/est_s)` 派；`game_cap` 硬顶；`DEFAULT_MAX_BATCHES=12` 安全阀（触顶
  响亮 WARN，非静默短采）。种子 `(rotate_seed,it,stage)` 独立流第 k 局（无 wave_idx）。
- **备选与否决**：保留 G0+补波仅改分关 est —— 否，用户要求去掉波次语义；worker 内
  每局实时选关 —— 改动面过大，v2 用「账本驱动小批」逼近同一语义（批间同步结算）。
- **§15.5**：相对 wave 规则的语料构造变更 —— 迁课程建议 fresh `--out/--traj`。
  wave 纯函数（`volume_waves.plan_topup` 等）仍保留供旧单测/e2e；**生产串行路径**
  已切 `_volume_collect_continuous`（`loop_core`）。`resume.trailing_stage_samples_per_game`
  提供分关 est_s。
- **落地**：`biz/volume_quota.py`、`loop_core._volume_collect_continuous`（S4 第十八刀后住
  `trainer/loop_volume.py`）、
  `tests/worker/test_volume_quota.py`。回归：相关 pytest + parse 绿。

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
  `${sitePackages};${NN_TRAINING}`）。**定案**：`trainerServeSpec` 与它们同规；实测 `trainer/run_rl_cluster.py --help`
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

1. **判据错**：PID 18364 = 控制台自己起的**共享 trainer**（`trainer/run_rl_cluster.py --serve`）。
   它服务多课，每开一门课就取该课自己的**按课锁**（单进程多课模型的正常持有）——而检查的
   判据是「该课锁活着就拒」⇒ **每次开课都被自己人拒**，只要 trainer 在跑就永远开不了课。
   真冲突的形状是「另一份**按课** runner（手工 `trainer/run_rl.py --course <本课>`）活着」——它与
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

* **去掉开课时的锁检查，直接开**——否：手工跑着 `trainer/run_rl.py --course <本课>` 时开课就是两套
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
- **一步级按课隔离（遏制）**：`trainer/loop_serve.py::build_executor` 在课程归属内 catch `SystemExit`
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


---

> **第二轮归档（2026-10-11）**：`DECISIONS.md` 第三次瘦身把 **2026-09-24 起、正文 >12 非空行**的 **15** 条决策全文按主题落到本节（第一轮 2026-09-23 见上文）。家族分组只归类、只连线，结论与理由一律引原文；`DECISIONS.md` 索引行末尾的指针 = `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §<id>`。

**家族谱系（同一问题的先后进展按主题归并，先后为时间序）**

- **A｜rl-config 职责边界**（2 条，2026-09-26）：`§2026-09-26-rl-config-scope` → `§2026-09-26-rl-config-b-class`
  - 末条决定（原文节录）：`--matrix` / `--drop-b-class` 增 `--scope {live,all}`；……
- **B｜止损块归位课程文件**（2 条，2026-10-02）：`§2026-10-02-goalnn-burn-rule-in-course-file` → `§2026-10-02-goalnn-burn-rule-cut2`
  - 末条决定（原文节录）：① 删 `legacy_burn_mode/overrides`、`legacy_paired_kill_overrides/self_kill/enabled`……
- **C｜训练模式热切换与启动恢复**（2 条，2026-09-24～10-05）：`§2026-09-24-goalnn-train-mode-hot-switch` → `§2026-10-05-course-startup-recover`
  - 末条决定（原文节录）：「跳过」从**终身黑名单**改成**带判据指纹的待重试表**：……
- **D｜起始分布注入（state_init）**（2 条，2026-09-25）：`§2026-09-25-state-init-course-key` → `§2026-09-25-state-init-snapshot`
  - 末条决定（原文节录）：① 起始分布 = `cloneWorld` 快照（`tools/sim/build-state-init-bank.ts` 产物，人类**真正到达过**的世界），……
- **E｜事件门契约（r2）**（1 条，2026-09-27）：`§2026-09-27-goalnn-r2-event-gate`
  - 末条决定（原文节录）：（三项，一次定死）：……
- **F｜配对止损参照物**（2 条，2026-09-25～09-30）：`§2026-09-25-clutch-null-kill` → `§2026-09-30-goalnn-kickstart-burn-paired`
  - 末条决定（原文节录）：参照物两档。`baseline` = 旧口径（本腿 `it<=0` 末条）；`paired` = Δ = 本臂 − **对端同 it**……
- **G｜门禁停机平台级开关**（1 条，2026-10-01）：`§2026-10-01-goalnn-gate-halt-platform-level`
  - 末条决定（原文节录）：（用户 2026-09-25 口径：「`gate-halt-mode` 应是平台级而非课程级，有人盯盘时切为提示，离开时切停机」）：……
- **H｜节点门 bootId 一致性**（1 条，2026-09-29）：`§2026-09-29-goalnn-node-bootid-gate`
  - 末条决定（原文节录）：（用户裁定，采纳 `plan/sampler-single-instance.plan.md` §8-Q2 的**备选**而非其原推荐）：训练侧……
- **I｜it0 基线污染（评估腿）**（1 条，2026-09-25）：`§2026-09-25-evala-baseline-bc`
  - 末条决定（原文节录）：x20-dodge-l1/L3 的 `eval_log` it0 各混入 200 局后期权重读数（L1 混 it43、L3 混 it51），……
- **J｜决策周期 K 通道**（1 条，2026-10-07）：`§2026-10-07-k5-decision-k-channel`
  - 末条决定（原文节录）：**口径修订（覆盖 `§2026-10-07-k5-filing` 原文"margin10"，全案以 plan v2 为准）**：守卫统一……

**家族 A — rl-config 职责边界**

### §2026-09-26-rl-config-scope（2026-09-26，`rl-config.json` 职责边界：只留「别处无处安放」，删配置不删代码）
> 决策 `DECISIONS.md` §2026-09-26-rl-config-scope。归档 2026-10-11（第二轮瘦身）。

**触发（用户 2026-09-25/26）**：「已经在其它地方配置过的数据就不该再往里填；`intent_rl` 已废弃，
后期重启也要配成现在 x 系列一样」→「intent/goal、`stream`/`double_buffer`/`precollect_early`
**只删配置、不删代码**」。

**决定**：`nn-training/rl-config.json` 只保留五类里的 A（机器/环境级）、C（调度策略）、D（每课
机器侧旋钮活课）；B（课程文件已有同名键的全局缺省）只留必要兜底；E（废弃/死键）删。
**新键该写哪**：机器事实 → rl-config；「怎么训」的超参/奖励/关卡/门 → `curricula/<课>.jsonc`。

**本次删除（8 个点号路径，含代码读点）**：`intent_rl`（整块 29 键）、`policy.upgradeBranch`
（**连 `rl/dispatch.py` 的读点一并去掉**——只删键不删读点，2026-08-30「残留旧分支名 reset 全部
节点」的坑就还在）、`policy.minDiskFreeMB`（零消费者）、`policy.streamKlCapIntent` /
`policy.streamWaveGamesIntent`（intent 专属）、`rl.stream` / `rl.double_buffer` / `rl.precollect_early`
（单一 PPO 路径下 `validate_args` 恒置 0 / 只在 `double_buffer` 开时被读 ⇒ 填了也不生效）。

**代码保留**（用户裁决）：`ppo/intent.py`、`ppo/goal.py`、`rl/modes.py` 的 `intent_rl(legacy)` 回退、
`rl/eval_m1.py` 与 m1-eval 派发链、`stream`/`double_buffer`/`precollect_early` 的读取点**都不动**
——重启时复用，不从 git 历史重补。intent/goal 入口仍冻结（`validate_args` 拒启），重启前须先解冻。

**防再长草**：新增 `nn-training/rl_config.schema.json`（键白名单数据）+ `rl_config_schema.py`
（**只告警不拒**，`run_rl.py` 启动时把未知/已退役键点出来）；清洗工具
`nn-training/tools/rl_config_clean.py`（dry-run/`--apply` 先备份 sha 校验/`--matrix`/脱敏）；
顺手修 `tools/agent/agent-setup.md` 里把磁盘阈值当成 rl-config 键的指路。

**备选与否决**：① 连 `ppo/intent.py`、`ppo/goal.py` 一起删——否（用户裁决：删配置不删代码）；
② 顺手去掉 `rl/modes.py` 的 legacy 回退与改 `e2e/test_run_rl_m1.py` 断言——否（同上）；
③ 一次性真删（B 类全删、零缺省）——否（会让历史课程无法复现，漏声明时静默落 argparse 默认）；
④ 未知键硬拒开训——否（用户裁决 O3：只告警不拒）。

**违反后果**：把「怎么训」的超参写回 rl-config = 造第二事实源（用户原则针对的正是这一类）；
不删 `policy.upgradeBranch` 的读点 = 下次有人手填非空值就再 reset 一遍全部节点。全文 →
`docs/nn/rl-config.md` §1 · 计划 → `plan/rl-config-cleanup.plan.md`。

### §2026-09-26-rl-config-b-class（2026-09-26，B 类兜底键删除：范围取「全部课程」超集 + `workers` 归机器级）
> 决策 `DECISIONS.md` §2026-09-26-rl-config-b-class。归档 2026-10-11（第二轮瘦身）。

**触发（用户 2026-09-26）**：「在训练机跑 `rl_config_clean.py --matrix`，按全绿结果删掉多余 B 类兜底键」。

**前置事实**：训练机（= 本机，hub / sampler / trainer 都活在这个工作区）上操作员已于 17:49 把最后
两门在训课停掉（`x20-steady-cont` / `x20-dodge-l3d2`；控制台日志 `已停课 …（已删开课标记）`）
⇒ `live`（`training-enabled.txt`）集为**空**，plan §3.2 / §8-O1 的常规判据出不了结论
（`is_green` 对空集恒 False）。

**决定 1 — 判据范围改用「全部课程」超集**：`--matrix` / `--drop-b-class` 增 `--scope {live,all}`；
`all` = `curricula/*.jsonc` 全部课程（本机 108 门）。理由：B 类兜底的**风险面**是「任何课程没声明该键
时落到 rl-config 的值」，所以「全部课程都不靠它」是**更强**的证据（全绿于 `all` ⇒ 全绿于 `live`，
反之不然）；在训集为空时它是唯一能出结论的范围。**本次按 `all` 删 `rl.difficulty` / `rl.max_ticks`**
——两者 108/108 显式声明（44 课程文件 + 64 关卡注入），且唯一消费者 `rl/eval_a_once.py:273-274`
的兜底 `or 12000` / `or "hard"` 与被删值**逐字同值** ⇒ 零行为变化。

**决定 2 — `workers` 与 `local_slots` 同归机器级**（`tools/rl_config_clean.py::MACHINE_KEYS`）：
二者在 `--scope all` 下判「全绿」（108/108 课程都写了 `workers`），但 rl-config 里这条是**裸机读数**：
`dashboard/src/core/slots.ts::bareCapacity` = `max(rl.workers, rl.local_slots)`，
`rl/config.py::apply_course_machine_overrides` 也把 `rl.{workers,local_slots}` 当本机配额缺省。
删 `workers` ⇒ `Number(undefined ?? 0)` = 0 ⇒ 容量塌成 0、`checkCapacity` 把每门课报成超量（假红）。
这是 plan §3.2-4 对 `local_slots` 豁免的推广。

**保留兜底（非全绿）**：`mb`（9 门 BC/demo 课没写）、`seed_rotate`（4 门）、`keep_iters`（106 门）、
`eval_window_sec` / `total_stages` / `rotate_stages` / `seed`（各 108 门）等一律**不删**——
plan §8-O1 已否决「零缺省」。

**顺手修的 bug（同一路径）**：`tools/rl_config_clean.py::load_jsonc` 此前是 `strip_comments` +
`json.loads`，**漏了去尾逗号** ⇒ 实测 108 个课程文件里 88 个、25 个关卡文件**全部**读不进，
`--matrix` 一遇在训课程就 `JSONDecodeError`；更危险的是若哪天调用点吞掉异常，「读不进来」会被
读成「课程没声明该键」= 静默删兜底。改为产品同源 `rl.jsonc.loads`（= `strip_comments` →
`_drop_trailing_commas` → `json.loads`）。

**备选与否决**：① 只用 `live` 范围、本次什么都不删——否（在训集空是操作员的即时状态，不是「无课程」；
空集下不删等于把「召回训练机跑矩阵」的目的作废）；② 按「4 门 dodge 课」（course-archive §1.1 的
基线在训集）范围删——否（会把 `mb` / `seed_rotate` / `lr` / `epochs` / `gamma` / `lam` 一起删掉，
而 9 门 BC/demo 课正靠 `rl.mb` 兜底 ⇒ 历史课程行为漂移，正是 §8-O1 否决的形态）；
③ 连 `workers` 一起删——否（机器容量读数，见决定 2）；④ 现场补写 `training-enabled.txt` 造一个 live 集
——否（伪造开课标记 = 越权改控制台拥有的状态文件，AGENTS 明禁）。

**违反后果**：把 `live` 当唯一范围 ⇒ 全停课时工具永久无法收敛；删 `rl.workers` ⇒ 控制台并发校验假红。
全文 → `docs/nn/rl-config.md` §1.4b。

**评审补正（2026-09-26 晚，对 `12187fa4` + `9fd0834c` 两个提交）**：另一 agent 逐条核过后，以下四处**改了实现**：

1. **「与控制台冒烟共用一份」的说法作废**（`rl_config_schema.py` docstring / `rl_config.schema.json`
   `_doc` / `run_rl.py` 注释三处）：`dashboard/src/**` 对该 JSON **零引用**，`rlConfigSmoke` 仍只查
   port/token/nodes ⇒ plan §3.4/E8 的「冒烟面板也点出假键」**未达成**（只达成启动日志那一半）。
   改 dashboard 时再接线，那时才恢复「共用一份」的措辞。
2. **脱敏收敛成一条清单，且不给「打印原文」的口子**：`rl_config_clean.py` 的
   `_SECRET_PATHS=("rl.remote_token","rl.remote_hub_url")` 与 `desensitize()` 实际只脱
   token/authKey **不一致** ⇒ 改为 `SECRET_KEYS=("remote_token","authKey")`（打印面与 diff 面
   共用一份）；同时把误导性的 `--no-redact` 改成诚实的 `--no-preview`。**否决**「按名字实现成
   打印原文」：plan §1.5 红线 1 说任何输出（含本地排障）都不得带出凭据 ⇒ 那就**不存在**这条路径。
3. **升级分支的坑在签名层面关死**：`dist_common.upgrade_branch_or(explicit)` ⇒
   `current_upgrade_branch()`（无参）。读点已验证删除，但「显式值优先于锁存」这个**语义**还留在
   签名里 ⇒ 顺手写一行 `or` 就能把 2026-08-30 的坑复发。拿掉参数后，复发必须改签名（并改
   `tests/tools/test_dist_upgrade_cli.py::test_upgrade_branch_has_no_explicit_override_path` 的断言）=
   一次有意识的决定。备选：保留函数不动——否（评审正确指出那是不可达死分支）。
4. **接线补钉子**：`check_rl_config` 有单测，但 `run_rl.py` 那三行「读配置 → 打告警」此前无人盯
   ⇒ 新增 `tests/worker/test_rl_config_schema.py::test_run_rl_logs_the_advisory_and_still_starts`
   （子进程 oracle，同 `test_serve_wiring` 手法）：假键 ⇒ 启动日志出现告警行且**照常起训**。

**控制台死开关的处置（2026-09-26 用户裁决）**：`stream` / `double_buffer` / `precollect_early`
三个开关**保留在界面上，但标灰 + 写清「当前不生效」**——否决「摘掉开关」与「只留文档」两档：
`TrainLaunchModal.tsx::INERT_TOGGLES` 清单驱动渲染，`disabled` **常闭**、`note` 就写「当前不生效」，
且显示值改成 rl-config 的当前值（**不再**读写 localStorage 偏好——灰着的开关显示成「开」会被读成
「它开着且有效」）；`preset.ts::setMode` 的回写白名单**保留**这三键（解冻 intent/多路时直接复用）。
守卫：`dashboard/tests/web-train-launch-wiring.test.ts`（呈现 / 常闭 / 不再写回 / 白名单保留四处）。


**家族 B — 止损块归位课程文件**

### §2026-10-02-goalnn-burn-rule-in-course-file（2026-10-02，止损块进课程文件：`kickstart_burn`/`paired_kill` 从 rl-config 迁入 `curricula/*.jsonc`，块不进 `corpus_fp`）
> 决策 `DECISIONS.md` §2026-10-02-goalnn-burn-rule-in-course-file。归档 2026-10-11（第二轮瘦身）。

- **背景**：结果面止损（干烧熔断 / 配对中点杀臂）的参照物与阈值住 `rl-config.json`（机器本地、
  不进 git）——换机器/新克隆静默回落 `baseline`（回测假阳性 49.3% vs paired 0.47%，~105×）；
  kickstart 家族被劈成两半（ref/init 在课程、burn 在 rl-config）。旧注释的否决理由「课程文件
  参与 course_fp 血缘（D14）」**把 `course_fp` 当成了 `corpus_fp`**：D14 判 `corpus_fp`，止损是判据不是语料。
- **决定**：`biz/course_spec.py` 加 `KickstartBurnBlock`/`PairedKillBlock`（解析期强校验，脏值拒课）；
  5 个读函数改「课程块优先 + `legacy_*` 兼容回落」；`RESTART_ONLY_FIELDS` 加两键（不进 `HOT_FIELDS`）；
  `corpus_identity_fp` payload 不加两键；判据本体 `burn_verdict`/`paired_kill_verdict` **逐字不动**；
  控制台 `burnThresholds` 同源（课程块 > rl-config > 常量）。h4-aim-{k10,k25} 落块、h4-aim-c0 写
  `paired_kill:{enabled:false}`；h4-hurt/h4-encl 四臂与 x20-clutch 两门暂留 rl-config 兼容回落（第二刀迁）。
- **被否决**：① 直接删 rl-config 读面（迁移期弄丢现有两条腿的止损，必须双读兜底）；
  ② 块进 `HOT_FIELDS`（判读窗口中途换口径 = 换实验）。
- **落点**：`nn-training/biz/{course_spec,hot_reload,corpus_fp}.py` ·
  `worker/{kickstart_burn,paired_kill,loop_guards_leg}.py` · `curricula/h4-aim-{c0,k10,k25}.jsonc` ·
  `dashboard/src/stack/kickstart-receipt.ts` · 测试 `tests/biz/test_h4_aim_courses.py`（新建）等。

**指针**：全文 `docs/nn/engineering.md` §59 · plan `plan/burn-rule-in-course-file.plan.md`
（含评审 `-review-bf.md`）· 回测 `nn-training/tools/backtest-burn-rule.py`。

### §2026-10-02-goalnn-burn-rule-cut2（2026-10-02，止损块第二刀：删 rl-config 回落读面 + 六处迁移 + 存量清理）
> 决策 `DECISIONS.md` §2026-10-02-goalnn-burn-rule-cut2。归档 2026-10-11（第二轮瘦身）。

- **背景**：`§2026-10-02-goalnn-burn-rule-in-course-file` 第一刀留了双读兜底（课程块优先 + `legacy_*`
  回落 rl-config）。六个使用者（h4-hurt×2 + h4-encl×2 + x20-clutch 两门；h4-aim×2 已于第一刀迁）
  现已全部收官且都有课程块 ⇒ 回落读面已无活用户；留一把「机器本地还能改止损」的回落口 = 预注册
  的止损仍可能被本地值盖掉（块优先也挡不住旧值漂移误导读面）。
- **决定**：① 删 `legacy_burn_mode/overrides`、`legacy_paired_kill_overrides/self_kill/enabled`
  六个回落读函数；`burn_mode/burn_overrides`/`paired_kill_*` 五函数去 `fallback` 参数（块缺席 =
  模块常量）；`loop_guards_leg` 两个守卫去 `dist_cfg` 参数（`course_key_of` 唯一用处消失）。
  判据本体 `burn_verdict`/`paired_kill_verdict` 逐字不动。② 迁移六处块（值逐字复制）：h4-hurt-{f75,f150}
  → `{mode:paired,peer:h4-hurt-c0}`、h4-encl-{f75,f150} → `{mode:paired,peer:h4-encl-c0}`、
  x20-clutch → `paired_kill:{enabled:true}`、x20-clutch-null → `paired_kill:{self_kill:false}`；
  六处头注「见 rl-config」改指本文件块。③ 控制台 `burnThresholds(course)` 去 `cfg`、`CourseConf`
  删字段；两键进 `LEGACY_COURSE_KEYS`。④ 存量清理：新 `pruneNoiseCourses()`（停课清理同一条分档
  扫全库：纯 `rollout_src:'local'` 删 / 空壳整条删 / 无读者旧键删；显式 node/auto/run 不动；幂等
  无变化不写盘），2026-10-02 实跑 92 项（48 → 4 门，只剩显式 run 档；备份
  `rl-config.json.bak.20261002-224412`）。
- **门槛③如实记录**：plan §9 P1 的「≥1 次会话在无 rl-config 键的环境跑满并确认止损生效」**未即时
  满足**——六个使用者已全部收官、当前无在跑的处理臂；删除安全性由「无生产读者 + 课程块齐全 +
  块缺席回模块常量（与旧回落同值）」承担，③降级为后续第一条带止损的新腿自然验证。
- **被否决**：① 保留 `fallback` 参数只删 `legacy_*`（留一把空口子 = 第二事实源迟早回来）；② 先不迁
  x20-clutch 两门（已收官；块进课程文件是归档/复现的预注册，不是只为在跑的腿）。
- **落点**：`nn-training/worker/{kickstart_burn,paired_kill,loop_guards_leg}.py` ·
  `trainer/loop_round_steps.py` · `curricula/h4-hurt-*`/`h4-encl-*`/`x20-clutch*` ·
  `dashboard/src/stack/{course-knobs,kickstart-receipt}.ts` · `dashboard/src/core/types.ts` ·
  测试 `tests/biz/test_burn_blocks_in_courses.py`（新建）等。

**指针**：全文 `docs/nn/engineering.md` §60（前刀 §59）· plan `plan/burn-rule-in-course-file.plan.md` §9 P1。


**家族 C — 训练模式热切换与启动恢复**

### §2026-09-24-goalnn-train-mode-hot-switch（2026-09-24，plan/train-mode-hot-switch.plan.md 主体 L1 + L3.2）
> 决策 `DECISIONS.md` §2026-09-24-goalnn-train-mode-hot-switch。归档 2026-10-11（第二轮瘦身）。

**「在线/离线」收敛成唯一那颗开关**：`setCourseMode` ① 先落本机训练配置（唯一写面
`actions/train-mode.ts::applyTrainModeToConfig`，域换算仍走 `stack/specs.ts::trainModeKnobs`）
② 再推 hub 镜像。用户 2026-09-24 报障「离线课切回在线后 Kaggle 仍因缺 bun 拒单」的根因就是
只翻了后一半：`courses.<课>.rollout_src=run` 没撤 ⇒ 下一段照样派 `kind=run`。
**写面边界**：`pushCourseMode`（只推 hub + 落意图，开课/停课/回灌共用）绝不写配置；`setCourseMode`
（那颗开关）写；`restoreCourseModes`（回灌）绝不写 —— `pushHubMode` 本来就是循环调 `setCourseMode`
的，不拆会让**开课重复写盘 1–3 次**、并把**停课**误翻成「整段上云」。
**L3.2**：worker 在**零下载**处（结果复用块之后、payload/code/ts_code 之前）做 bun 能力自检
（`kind in (iter, run)` ∧ 非 echo）——现状是 3.42MB / 12.1s 全白传才 `REJECTED`；异常类型与
重试策略不变（复用既有的 `ProtocolError` ⇒ 确定性拒绝链）。
**生效时机**：段边界，不是「下一轮」——`run_iters<0` 时一段覆盖到课程末，训练侧阻塞在 8h 段等待里
⇒ 已在飞的段不抢占（**明确不做**），回执/文档写明最坏情形并指向「停课/暂停」。
**node 往返**：`offline` 覆写 `rollout_src` 会吞掉显式选的 `node` ⇒ 切离线前记进
`console-state.courseRolloutSrc`（只记 `node`/`auto`），切回在线时恢复。
**第三源可见性**：`modeDrift` 增 `configRun`（`stateView.courseRolloutSrc` 逐课下发）——
「hub 与意图都在线、配置仍是 run」这种半状态在**非当前课程**的行上也看得见。
**被否决**：① 让 `course-mode.ts` import `course-lifecycle.ts`（反向 import 成环）⇒ 新开
`train-mode.ts`；② 把配置写入放进 `pushHubMode`/`setCourseMode` 共用体（= 开课重复写盘 + 停课误译）；
③ 自检放在 `normalize_manifest` 紧后面（会把「已算完、只差重传」的 job 按 bun 拒掉）；
④ 把 `opts.rolloutSrc` 也过一遍 `trainModeKnobs`（`run` 会被换成本机 `local` = 行为漂移）。
**违反后果**：再出现「开关只翻半场」⇒ 用户报障原地复发；「离线」被当成「只停派发」（真正含义是
**整段上云**）⇒ 运维以为自己停派发了而本机仍在本机采样。
—— 全文（背景 / 事实链 / 设计 / 判据）→ `docs/nn/console.md §15` + `docs/nn/remote-transport.md §37`（零下载自检）

### §2026-10-05-course-startup-recover（2026-10-05，课程配置错被整课跳过：判据变即自动重试 + 只读 `blocked` 判据）
> 决策 `DECISIONS.md` §2026-10-05-course-startup-recover。归档 2026-10-11（第二轮瘦身）。

- **背景（用户报障）**：`x20-adv3-open-r2` 的课程文件自相矛盾（`kickstart_init>0` ∧ `kickstart_ref=false`）
  ⇒ 启动期 SystemExit、整课被共享 trainer 跳过；人改好文件 + 控制台「停→开」后日志零变化——唯一出路
  是重启 trainer（会打断所有并行课程）。实证：07:47 跳过 → 07:51 重启仍跳过 → 改文件无变化 →
  08:06 停→开无变化 → 08:10 重启 serve 后才开课（`tmp/trainer-cluster.log`）。
- **决定（P0 复活通道）**：「跳过」从**终身黑名单**改成**带判据指纹的待重试表**：
  `ServeReport.skipped_at[course] = {kind: 来源分类, fp: 判据快照}`；每拍（既有 discovery 空转拍，
  不新增节拍）由纯函数 `reopenable_skipped` 比对——课程文件身份（mtime_ns+size）/ 开课标记 mtime /
  按课程锁签名（释放/换主/持有者死）/ 账本 mtime，**按跳过来源取事实**（config / lock / enqueue）。
  判据变了 ⇒ 撤销记账 + 重走开课入队；一步级 SystemExit 那族走**第二条通道**（重置队列 + `_enqueue`，
  **保留 runtime/引擎**——重建 runtime = C-0 无限 RETRY 前科）。判据没变 ⇒ 不重试、不刷日志（`:851`
  注释的原意保住）。用户「停→开」（标记 mtime 变新）也是复活信号。**不动** `kickstart_init` 的
  restart-only 分类（那是给**已在跑**的课的规矩；本条治的是「压根没开成的课」——正交）。
- **决定（P1 只读判据）**：`openable {ok, reason}`（`worker/course_args.py::course_openable`——与
  `open_course` **同一条校验链**、零副作用）+ `waiting.kind='blocked'`（新增一格；优先级
  finished > inflight > collect > blocked > idle > ready，仅 `pending>0` 时报）⇒ 控制台不再把起不来的课
  报成「无外部等待，下一步 …」。控制台：`WAIT_KINDS`/`WAIT_TITLES`/pill（红）/告警坞第 8 类
  （err；按课过滤；ack 身份 = reason 原文 ⇒ 改了但没修好会再弹；**不提供**恢复按钮——恢复是改文件）。
- **边界（明说）**：判据是「**配置不可开课**」不是「serve 此刻跳过了它」（在跑的课被改坏也会红、
  trainer 没起也会红）；serve 级 `--mode` 与 BC 链不在覆盖面；rl-config/env/权重类不过指纹
  （改课程文件不会触发复活）；复活发生在**全局空转拍**，不承诺零延迟。
- **被否决**：① 恢复需人重启（用户裁决不可接受——会打断并行课程）；② 定时无条件重试（每秒 pydantic
  校验 + 可能建 runtime，只为等一个可能不来的修改）；③ 复活走重建 runtime（C-0 前科）；
  ④ 把 `kickstart_init` 变成 hot 字段（把「没开成的课的重试」与「已在跑的课的 restart-only 口径」
  混为一谈——两者正交，改错会破坏实验口径）；⑤ **serve 侧落一份 skipped 状态文件**给控制台读
  （F4 的实现选型）：新增产物 + 第二个真相源（serve 写、控制台读），而只读面要回答的「起不起得来」
  已由与开课链同源的 `openable` 单点说清——判据面文案已不写死 serve 侧事实，无需再养一份状态。
- **违反后果**：用 `course_fp`（sha）当指纹 ⇒ 每拍哈希；对已在跑的课重建 runtime ⇒ 无限 RETRY；
  文案写死「serve 已跳过」⇒ 在跑/停机场景说谎；`blocked` 复用 `idle` ⇒ 停机中的课与起不来的课又
  长得一模一样（本次事故「分不清」的翻版）。
- **落点**：`nn-training/trainer/loop_serve.py`（`skipped_at` / `_skip_fingerprint` / `reopenable_skipped` /
  `_revive_skipped` / `_revive_aborted`）· `worker/course_args.py`（新，校验链抽中立模块，serve 与只读视图
  同源 import）· `trainer/loop_plan.py`（`WAIT_BLOCKED` / `course_openable`）· `trainer/run_rl_cluster.py`
  （每行 `openable`）· 用例 `tests/trainer/test_serve_skipped_retry.py`（9：判据三类 + 三条回归防线 +
  双课对照）· `tests/trainer/test_course_openable.py`（6）· 控制台 `dashboard/src/web/view/loop-queue.ts` /
  `alerts.ts` / `interaction.ts` / `theme.css` · 计划 `plan/course-startup-recover.plan.md`。
- **指针**：全文 `docs/nn/training-stack.md` §29（训练侧）· `docs/nn/console.md` §30（控制台读面）·
  计划 `plan/course-startup-recover.plan.md`。


**家族 D — 起始分布注入（state_init）**

### §2026-09-25-state-init-course-key（2026-09-25，课程键 `state_init` 进 CourseConfig）
> 决策 `DECISIONS.md` §2026-09-25-state-init-course-key。归档 2026-10-11（第二轮瘦身）。

> _(本条的 `rebase_counters` 字段与「中段交棒」措辞已在同日被 §2026-09-25-state-init-snapshot 修订：
> 起始分布改为**快照注入**，`rebase_counters` 删除。本条只保留「课程键怎么进配置层」那部分。)_

**来历**：`curricula/x20-state-init.jsonc`（rollout 起始分布 = 人类 demo 中段起跑，plan 六腿六负
后的第七条攻击面）起草时就带着 `state_init` 块，而 `CourseConfig` 是 `extra="forbid"` ⇒ 那个文件
**长期加载失败**，连带 `tests/biz/test_reward_golden.py::test_jsonc_courses_load`（遍历 `curricula/*.jsonc`）
成为门禁唯一那条红。plan `plan/x20-state-init.plan.md` P2 = 把这个键映射进配置层。

**变更**：① `StateInitBlock`（`rl/config.py`）：`bank/cut_from/cut_to/cut_step/rotate_cuts`
全可选 + v1 缺省，`extra=forbid`，切点自相矛盾（`cut_from<0` / `cut_to>0` /
`cut_step<1`）在校验期拒；② `CourseConfig.state_init: StateInitBlock | None = None`（缺席 = 标准
开局，老课程逐字节不变）；③ `flat_overrides` 把整块转成 **dict** 交出去（不进 mapping 那张标量表）；
④ 「声明了就必须能跑」的自洽检查住 **`apply_course`（启动期）**：`bank` 空、或不在盘上（cwd /
仓库根 / nn-training 三种基准都认，`resolve_state_init_bank`）⇒ `SystemExit`（响亮，不静默退回
标准开局——那等于换了一个实验，而账本还以为跑的是中段起跑）。

**被否决**：把 bank 存在性检查放进 `load_course`（`load_course` 只读课程文件本身；在那儿读盘会让
**任何**遍历课程的调用方被一个未落地的产物打红——就是这次那条门禁红）· 把 pydantic 模型对象原样
传进 `args`（`echo_config` 对非标量做 `json.dumps` ⇒ 当场炸；P3 的 `build_rollout_cmd` 也只要
JSON 数据）· 允许 `cut_to > 0`（绝对上界：得先读银行 manifest 知道局长，v1 只支持相对局尾）·
`bank` 缺失时静默关掉 state_init（映射漏了/数据缺了都表现为「静默失效」，`ent_break` 前科）。

**违反后果**：新增课程键不映射 `flat_overrides` ⇒ 训练静默按缺省跑（`ent_break` 前科：`0.25` 从未
落地）· 在 `load_course` 里做产物存在性检查 ⇒ 起草中的课程文件把全体遍历课程的用例打红 ·
块内乱写键被静默忽略（`extra=forbid` 是这一层的唯一防线）。
—— 全文（P2 落地记录 / 银行内容校验为何留 P0-P3）→ `plan/x20-state-init.plan.md` P2

### §2026-09-25-state-init-snapshot（2026-09-25，rollout 起始分布 = 人类中段**世界快照**注入）
> 决策 `DECISIONS.md` §2026-09-25-state-init-snapshot。归档 2026-10-11（第二轮瘦身）。

**来历**：六条价格腿全阴性后的第七条攻击面 = 只换 rollout 的起始**状态分布**、不动任何价格项
（plan/x20-state-init.plan.md）。首版设计是「人类**磁带**（逐 tick 输入）headless 快进 T tick
后交棒」；评审发现六个阻断级问题，其中四条全部源自「用**输入**去重建**状态**」：B2 人类 idle
（20–30% 帧）在动作空间里表达不了（`ScriptedInput` 的 move=0 = 继续走）、B5 快进段内可能先死、
B6 交棒点不在决策边界上、B1 重建出的不是人类那个状态且**无外部证据**可对。改为**直接注入状态**。

**决定（后来者极容易做错，故入册）**
① 起始分布 = `cloneWorld` 快照（`tools/sim/build-state-init-bank.ts` 产物，人类**真正到达过**的世界），
不是人类输入的产物。资产全是现成机制（`ReplayInput` + `cloneWorld`/`restoreWorld` +
`worldTickHash`），不新造格式、不新造解码器。
② **状态来自人类，未来来自本局抽到的 seed**：restore → 核 tickHash → `world.rng.reseed(seed)` →
重施 CLI/关卡权威值。故 `(stage,seed)` 种子流逐轮照旧轮换（§15.1 真满足、判决段配对干净），
而起始状态集合 = 银行 972 个切点（池小 ⇒ V 头记忆化风险，已在 plan §5 预注册读数）。
③ **不做 rebase**：奖励 = Φ 的差分，对「继承的进度」天然不付钱；计数器列保持**游戏真值**，
`initCounters`/`initSnapshot`/`initTick` additive-only 进 shard manifest 供分析侧自行 rebase。
④ **v1 只在本机采集**，执法点在**两处**：ⓐ `dispatch.RolloutDispatcher.run()` **入口的派发闸门**——
开了 `state_init` 的轮只要还剩一个可行远端节点就 `SystemExit`，否则零派发、整轮交本机腿
（`run_rollout`）；`local_slots` **不是**这个开关（它只是并发配额，主循环照旧向 pool 派发）。
ⓑ `node_side=True` 的三条 argv 路径（`build_rollout_cmd` / `iter_job.build_iter_spec` /
`plan.template_argv`）在**发布前** `SystemExit`。两处都要：老导出器**静默忽略未知 flag**，云节点
cwd = job 目录 ⇒ 「云上跑标准开局、账本写中段起跑」是最贵的错，宁可不发。
**代价（2026-09-25 实付）**：首版只有 ⓑ，而主循环走的是 ⓐ 的 volume 路（`fetch_task` 拼任务参数
**没有**快照项）⇒ 三条护栏一条没触发 = 混语料（pool 标准局）+ 缺 `initTick` 的 shard 被全剔 =
无限波次，烧掉 **51.9 万 transitions** 才发现；教训 = 护栏写在 **argv 构造点**对 volume 路天生无效，
派发模式必须在**派发入口**定。
**被否决**：把 `local_slots` 当「纯本机」开关用（它语义只是并发配额，会让配额调参意外改采集分布）·
只在 `fetch_task` 侧补 `node_side` 拒（闸门仍在上游派发之后，且节点侧调用点不止一处）。
⑤ **shard 侧护栏**：缺正整数 `initTick` 的 shard 在**六个 funnel** 一律不计入（`_scan_shards` /
`completed_pairs` / `settled_stage_totals` / `resumed_manifests` / `iter_shard_dirs` /
`verify_and_land`），判据函数单点 = `rl.resume.shard_state_init_ok`；开关只从 `args.state_init` 派生。
⑥ **起始分布进语料身份**（`corpus_identity_fp`，仅激活时，bank 只算文件名）：决定「一个样本从哪个
世界开始」与 `seed_rotate`/`mode` 同类 ⇒ 课程中途加/删/改 `state_init` 时旧 shard 在四个 funnel
全被 D14 自动排除，零额外参数。
⑦ 保真门是**外部**证据（plan §2 M4）：切点取 `hashInterval` 整数倍 ⇒ 每个快照都能与录像记录链
逐点对账（P0 已 2916/2916 全对）；采集侧每局 restore 后再自检一次，不符 = 该局响亮失败。

**被否决**：① 磁带 FF（B2 idle 不可表达 / B5 快进段内会死 / B6 边界 / 无外部证据）·
② 「用 demo 那一局的 seed 复现」（踩 §15.1「第 it 轮 (stage,seed) 不得与更早轮重复」= 重磨固定集，
且训练 seed 集与 414000 判决段本体重合）· ③ 在 TS 侧 rebase 计数器（对奖励是恒等变换，却让
`metrics.kills` 与 manifest 真值分叉）· ④ 云侧顺手做 blob 搬运（用 `node_side` 响亮拒换时间；
规格留 P2.5，届时必须同时动 `_RETARGET_FLAGS` 与 plan 自检）· ⑤ bank **内容**进语料身份（要读盘 + 
节点上不一定有该文件 ⇒ hub/节点指纹分叉，整份 job 误拒）。

**违反后果**：把 `--init-snapshot` 放上云而不做 blob 通道 ⇒ 云上跑标准开局、账本写中段起跑 ·
护栏只设在 argv 构造点、不管派发入口 ⇒ 主循环的 volume 路整条绕开（51.9 万 transitions 事故）·
在 `reseed` **之后**核 tickHash ⇒ 外部证据变成恒假/恒真 · 交棒点不在 `K` 边界 ⇒ 首个决策前 K−1
tick 由「上一帧残留动作」驱动 · 课程中途删 `state_init` 还复用旧 shard ⇒ 两种起始分布混训。
—— 全文（六条评审处置表 / P0 实测读数 / 单局 smoke 读数 / P2.5 规格）→ `plan/x20-state-init.plan.md`
§2 / §3 / §6 · 前身条目 `§2026-09-25-state-init-course-key`


**家族 E — 事件门契约（r2）**

### §2026-09-27-goalnn-r2-event-gate（2026-09-27，x2 事件门契约：γ 保 per-step + 交棒首段禁事件 + Δt≥3）
> 决策 `DECISIONS.md` §2026-09-27-goalnn-r2-event-gate。归档 2026-10-11（第二轮瘦身）。

**来历**：新纪元 R2 包（`plan/new-era-stop.plan.md §6 R2`）给 x2 rung 预注册「事件驱动决策」——
决策门从「均匀 K」扩成「均匀 K ∪ threat-ONSET 沿（+最小间隔闸）」，落地前有三件**不能就近表达**
的裁决：折扣语义、与 state-init 起始分布的共存、决策间隔上界（hy P0-3/P0-6 指出的三条）。
实施记录与基线数字在同 plan 的「R2 实施记录」节。

**决定**（三项，一次定死）：
① **γ 保持 per-step**（`nn-training/rl/config.py:974` γ=0.995），**不**切半 MDP `γ^Δt`。换算声明：
   视界 = 200 决策步 × avg-Δt；事件门实测（x20-noexplore it99 × ladder-c20 四局）
   avg-Δt = **9.79–9.94** tick ⇒ 视界 **1958–1988** tick（相对均匀 2000 漂移 **−0.6%…−2.1%**）；
   最小间隔闸 minGap=3 给出硬下界 600 tick（实测未逼近，minDt ∈ {3,4}）。事件密度若因 K 收紧/
   电平化而显著上升，**重议**本条（`np_core.py::gamma_step = gamma ** dt` 已具备半 MDP 能力）。
② **state-init 共存 = 方案 i**：银行切点规则**不变**（仍 `t % K == 0`），交棒 tick 恒是决策 tick
   （决策流首个候选恒放行），且**交棒后第一个 K 段禁 threat 事件**——快照只存当前态、不存
   `prevThreat` 历史，交棒瞬间的「首次入带」是伪造 onset。**不重出银行、不改切点规格**。
③ **Δt ≥ 3 是全门不变式**（含均匀边界）：事件后 1–2 tick 的 `t % K == 0` 边界会被跳过
   ——这是事件模式唯一会改「均匀子集」的路径，无事件的局逐字节不变（HEAD A/B 实测）。
   门读数（n / events / avg-Δt / min-Δt / max-Δt）落 shard manifest 与 `_eval_report.json`，
   **仅 `--decision-events` 时写**（缺省调用逐字节不变）。

**被否决**：首版就上 `γ^Δt`（同刀两个自变量，x2 相对 x1 的边际不可归因，hy P0-4）；
重出 state-init 银行（无理由的语料轮次，§15.1/§15.5）；把 threat **电平**写进谓词
（持续期每 tick 成立 ⇒ Δt=1 ⇒ 部署 6Hz→60Hz + 视界塌缩，hy P0-3）；给 BC label 导出器
顺带开事件（那是新语料轮次 + 与 x1 的 BC 教师口径分叉，属 x2 排产决定，不在 R2）。

**违反后果**：改折扣语义 ⇒ 必须新实验（新 `--out`/`--traj` + 新 DECISIONS，§15.5），
不得与 x1/x2 的读数混算；把电平/无条件事件写进 `decisionDue` ⇒ 部署侧前向频率与视界同时失控。

**落点**：`src/nn/decision-gate.ts`（唯一实现 + 读数）· `src/nn/obs-encoder.ts::decisionTick`
（`condition === 4`，可选 `prevThreat`）· 五处决策门（`policy-input` / `export-rl-rollout` /
`export-eval-game` / `export-nn-replays` / `record-games-video`）+ `divergence-probe` 全改调
`decisionDue` · `build-state-init-bank.ts` 共存契约 · 测试 `tests/nn/decision-gate.test.ts`、`tests/sim/eval-game-parity.test.ts`、`tests/state-init.test.ts`


**家族 F — 配对止损参照物**

### §2026-09-25-clutch-null-kill（2026-09-25，paired-kill 杀错臂 + 重启键坏，两处修）
> 决策 `DECISIONS.md` §2026-09-25-clutch-null-kill。归档 2026-10-11（第二轮瘦身）。

**来历**：C-0（`x20-clutch-null`）22:08 死在 it46，账本 `gate_verdict: paired-kill 连续 2 点
< −3pp`；用户控制台重开课（22:15 marker 重写）毫无反应。两处都是代码与课程文件不一致：

- **杀错臂**：两份课程文件只授权**单向**杀（差值 < −3pp ⇒ 杀 Cw，本腿证伪）；`rl/paired_kill.py`
  实现却是**对称自杀**（Δ=本臂−对端，谁落后谁死）。C-0 落后（it40 −3.75pp / it45 −6.00pp）
  恰等于 C-w 领先 +3.75/+6.0pp——正是加权要证明的——规则却杀了对照。对照一死，终点 verdict
  （414000 配对 McNemar 要两条臂）即不可能。且 firing 在 ~2.9h，课程写的是 6h 中点、
  日常"只看趋势不判决"。
- **重启键坏**：`loop_serve.py` 重扫只认"不在 `runtimes` 里"的课；收官课永在 `runtimes` 里 ⇒
  控制台停→开（marker 重写）永远没人执行。唯一的旧路是重启整个 serve 进程（打断健康腿）。

**决定**：① `courses.<课>.paired_kill.self_kill`（rl-config 执行面，缺席/写坏=True，即现状；
  只有显式 `false` 才关）：命中时判据照算、streak 照落账，只是不停车（`loop_guards._paired_kill`
  在落账后、云端停机前return False）。C-0 经控制台设 `false`（rl-config 是 live 配置，不进库）。
  ② serve 重扫：`reopened_parked`（纯函数：队列已终 + 落过收官副作用 + marker mtime 新于入队
  记录）⇒ **只重置队列、复用原 runtime 与热引擎**（重走 open 会建 runner=None 的新 runtime，
  池里旧引擎还在，`ensure_ready` 只对引擎不对 runner ⇒ 每轮断言失败进无限 RETRY，测试抓获）+
  rounds_done 累计带过去；`_settle_rounds` 的旧文案同步改（"停→开后自动重新入队"）。
  ③ 复活顺序写死：先设开关 → 再重启 serve（新进程才读新代码）→ C-0 从 it47 指针续跑；
  streak 从账本重算（尾部仍 2/2）但开关已关 ⇒ 只记录不杀；it50 新 eval 点定去留。

**被否决**：把对称改单向改默认（已有课程行为突变；开关缺席=现状）· 复活重建 runtime/引擎
（丢 runner 无限 RETRY + 白付 torch 重建）· 重启键修成"只要 marker 在就重入队"
（分不清"一直开着"与"停→开"，正常收官的课会被反复拉起）· 单课另起进程跑 C-0
（与共享 trainer 抢同一 traj，lifecycle 明令禁止的双调度器）。

**违反后果**：对照臂被杀 ⇒ 终点配对 verdict 永不可判（McNemar 要两臂同 seed 800 局）·
收官课重开课无响应 ⇒ 唯一复活路是重启 serve（打断健康腿）· 复选用新 runtime ⇒ 无限 RETRY
幽灵轮（账本有入队、无产出）。

### §2026-09-30-goalnn-kickstart-burn-paired（2026-09-30，止损参照物改为同网格配对差；`auto` 缺省 = 对已配对课的行为变更）
> 决策 `DECISIONS.md` §2026-09-30-goalnn-kickstart-burn-paired。归档 2026-10-11（第二轮瘦身）。

- **背景**：Wave 1 判无效（§2026-09-30-h4-lane-wave1-verdict）——A2 的 ABORT 查实为**假阳性**：旧
  `kickstart_burn` 的参照物是**本腿自己的 it0 基线**，而控制臂 a0（零奖励 = 真零效应）自身漂移到位
  （逐点 pattern `..TT.T.` ⇒ 峰值 streak 2，只差 1 点被自己的规则杀掉）。参照物会飘 ⇒ 任何腿都可能
  被自己的起点误杀，与效应无关。
- **决定**：参照物两档。`baseline` = 旧口径（本腿 `it<=0` 末条）；`paired` = Δ = 本臂 − **对端同 it**
  读数（pp），尾部连续 `points` 点 Δ < −`margin_pp` ⇒ 触发。常量不变（`BURN_POINTS=3` /
  `BURN_MARGIN_PP=5.0` / `METRIC=winRate`）——只换参照物 = 最小改动。
- **接线**：`rl/kickstart_burn.py` 增 `MODE_BASELINE/PAIRED/AUTO` + `burn_mode()`；`burn_verdict(peer_rows=…)`
  非 None 时**整段委托** `rl/paired_kill.py::paired_kill_verdict`（Δ 判据只留一份，不写第二实现）；
  `BurnVerdict` 增 `mode/delta_pp/peer`（带缺省 ⇒ 既有五元位置构造不变）；事件 `write_kickstart_burn`
  同步落三字段。`loop_guards_leg.py::_burn_paired_rows` 前提闸同 `_paired_kill`（`declared_paired_seed`
  + 对端账本末条 `run_start.rotateSeed` 同 V）；**对端不唯一不猜 ⇒ 回退 baseline 并只 WARN 一次**。
- **★ 行为变更**：`mode=auto` 是**缺省值**；现 `rl-config.json` 已被配对的课（`x20-clutch` 等）**没有任何
  `kickstart_burn` 覆盖** ⇒ 下次开训会**静默**从 baseline 切到 paired 参照物。有意（正是要修假阳性），
  但读旧日志须知道两档口径不同。
- **回测证据**（可复算：`nn-training/tools/backtest-burn-rule.py`，语料 `tmp/h4-lane-{a0,a1,a2}/eval_log.jsonl`）：
  - 旧规则实测：a0 峰值 streak 2 / a1 1 / **a2 3 ⇒ 触发**（即那条被误杀的腿）⇒ 三腿假阳性 1/3。
  - 新规则实测：a1 vs a0 峰值 0；a2 vs a0 Δ = (+0.00, −5.50, +5.00, −5.50) 峰值 1 ⇒ **两臂都不触发**。
  - MC 20000 次（每点 200 局二项噪声，7 点 it5..35）：N1 无漂移 旧 **0.0050%** / 新 **0.3100%**；
    N2 共享漂移（两臂共享对照臂实测轨迹）旧 **49.3350%** / 新 **0.4650%**（**低 106×**）⇒ 假阳性来自
    「参照物会飘」而非抽样噪声。阈值 grid 复测：现常量 (5pp,3点) 在 N2 下 FP = **0.56%**，比旧规则
    低 **88×**，且全 grid 三腿实际触发均 [False, False]。
  - **代价明买明卖**：无漂移下新规则略差（0.005% → 0.31%），因配对差抽样方差更大
    （σ_Δ = √2·σ ≈ 3.48pp vs 单臂 2.46pp）。
- **被否决备选**：① 只调常量（margin 3→8 / points 3→4）——参照物漂移仍在，且会拖死真效应；
  ② 止损改硬失败（crash）——丢掉「停训但留证据」的诊断价值；③ 要求 `kl`/`entropy` 同时异常才触发——
  无回测支撑，判据复杂化一倍；④ 为 burn 单写 Δ 判据——与 `paired_kill` 两套实现会分叉。
- **未决（用户拍板）**：控制臂漂移处置 · 是否按 lane 项占回报 5% 重锚（新实验 §15.5）· Wave 2 几何选择。


**家族 G — 门禁停机平台级开关**

### §2026-10-01-goalnn-gate-halt-platform-level（2026-10-01，门禁停机模式升为平台级单开关：`tmp/gate-halt.json` 意图 + 回执；课程级三写面退役）
> 决策 `DECISIONS.md` §2026-10-01-goalnn-gate-halt-platform-level。归档 2026-10-11（第二轮瘦身）。

**决策**（用户 2026-09-25 口径：「`gate-halt-mode` 应是平台级而非课程级，有人盯盘时切为提示，离开时切停机」）：
「门禁触发时停不停」从课程级散装旋钮升成**平台级单开关**——`tmp/gate-halt.json`（控制台写、
supervisor 每轮判定读）+ `tmp/gate-halt.applied.json` 回执（逐课 `effective_mode` / `source`）；
`notify` 可带 `until`（2/4/8h/不限，缺省 8h），**到点由读时求值自动回落 halt**（不需要任何进程定时翻牌）。

**为什么**：它回答的是「**有没有人在盯盘**」——操作员此刻的状态，不是某门课的属性。课程级三写面
（每课 argv `--gate-halt-mode` / `courses.<课>.gate_halt_mode` / `<traj>/gate-halt-mode.txt`）外加隐藏第四面
（`rl.gate_halt_mode`）造成两类事故：① **配对腿漂**（2026-09-25 现场 `x20-dodge-l3` 写了 `notify` 而 `l1` 没有
⇒ 同一实验一腿停、一腿只提示，序列可比性受损）；② UI 摆在平台位置、语义却是课程级。

**两个裁决点**（评审发现初稿自相矛盾 / 落点被分层守卫拦）：

* **O4 优先级 = 平台文件 > CLI `--gate-halt-mode` > 缺省 `halt`**；CLI 只兜底「文件不存在」。
  备选「CLI 优先（单课例外临时用 CLI）」被否决：那会让控制台写出的意图被历史命令行静默盖掉，
  「一处切、全局生效」当场失效。
* **O5 模块落点 = `nn-training/worker/gate_halt.py`**（L0：只 stdlib + `common.log`）。备选
  `biz/gate_halt.py` 被否决：刀 6 后 `biz/` 的定位是「只留游戏业务」（12 模块），训练控制面住进去是
  类别错误；`trainer/` 则会被 `tests/test_layering.py` 的 `TRAINER_ORCHESTRATION` 快照拦下
  （每模块必须可达 `remote|worker`）。代价是显式的：`tests/helpers/remote_dag.py::LAYERS` 登记一行
  （ownership，不是罚款）。

**保守方向（不可回退）**：任何「读不到」都回落 `halt`，**绝不回落到 notify**；文件**存在但坏**
（非法 JSON / 非法 mode / `until` 非数字）⇒ `halt` + 告警，且**不许被旧 CLI 值接管**。

**被否决的备选（逐处）**：① 残留 `<traj>/gate-halt-mode.txt` 首次迁移成平台值——会把**一门课**的值
升格成全平台，违背「平台级是人显式切的」⇒ 取「忽略 + 首次告警 + 控制台提示可删」。② 把开关做成
rl-config 键——它每进程启动才读一次，而平台开关要**每轮热读**（且 rl-config 承载密钥、写面重）。
③ 离线腿「也能 notify」——今天云机侧没有读点（读点只在 supervisor），不为此改 hub 下发链路；
取 `NN_GATE_HALT_LEG=offline` 短路 + `source=default` 日志兜底。④ 顺手重构 `trainer/loop_control.py`
（同构范式）——不动：它有自己的契约与用例，同构不等于同体。

**为什么值得记**（AGENTS §6.3）：① 有被否决备选（上面四条）；② **未来会重犯**——「把操作员的
此刻状态写成课程属性」是同族错误的温床（下一处可能是：谁在盯盘 / 这条腿要不要停 / 是否在维护窗口），
判据是「这个值描述的是**课**，还是人/机群？」；③ 不能就近表达：契约、优先级、读时求值、两侧原子写、
离线腿口径跨 python/TS 两棵树。

**指针**：全文 `docs/nn/training-stack.md` §27（训练侧）+ `docs/nn/console.md` §21（控制台侧）·
计划 `gate-halt-platform-level.plan.md`（**未跟踪**，不入库）· 实现 `nn-training/worker/gate_halt.py` ·
`trainer/loop_guards.py::_gate_halt_mode`（唯一读点，移 hub 短路之前）· `worker/cli.py`（`default=None`）·
`trainer/loop_serve.py`（白名单摘除）· `rl_config.schema.json`（`rl.gate_halt_mode` 挪 retired）·
`dashboard/src/stack/gate-halt.ts` · `core/paths.ts` · `server/api/route.ts` · `web/view/gate-halt.ts` ·
测试 `nn-training/tests/worker/test_gate_halt.py` · `dashboard/tests/gate-halt-platform.test.ts`。
落账门禁：nn 子集绿（gate_halt 19 例 + 守卫族：意图 / 分层 / 护栏）· dashboard 1245/0 + typecheck · 三份 bundle 过。


**家族 H — 节点门 bootId 一致性**

### §2026-09-29-goalnn-node-bootid-gate（2026-09-29，节点门多一道「本轮 bootId 一致」：训练侧用 ping 的 pid/bootId 判 stale 并排除）
> 决策 `DECISIONS.md` §2026-09-29-goalnn-node-bootid-gate。归档 2026-10-11（第二轮瘦身）。

**决策**（用户裁定，采纳 `plan/sampler-single-instance.plan.md` §8-Q2 的**备选**而非其原推荐）：训练侧
节点门在 codeHash/bun 之外多一条「**本轮 bootId 一致**」——新模块 `nn-training/rl/node_identity.py`
（纯逻辑 + 进程内小账本，零依赖）按 `round_key(腿, 采集单元 id)`（`rollout:it42` / `eval:it42.eval` /
`batch:run.b7u3`）在**本轮**第一次 ping 时钉住该节点的 `bootId`（只钉一次，不一致后不改写 ⇒ 重复 ping
原因幂等），之后同键 ping 不同 ⇒ 本轮**排除**该节点：rollout 准入与回场（`rl/dispatch.py` /
`rl/queue_local.py`，两处共用同一个键）· A-eval 准入（`rl/eval_dispatch.py`）· B/C 回场
（`rl/batch_runner.py::_UnitLanes.bringup`，复用 `given_up` 以保住「全部通道已放弃 ⇒ 收摊」的退出条件）。
**只上报 + 排除，绝不下发 `/v1/restart`**（两个进程同时服务不是重启一个能修好的事）。

**为什么**：① a98（2026-09-29）一台机器两个 agent 同听 8443，一轮 192 局被内核按 4 元组哈希分给两个
代码版本 ⇒ 45 列旧 shard 混进 payload ⇒ 云 worker `grad` 5 连炸、整门课 aborted；② agent 侧的应用层互斥
（`tools/agent/single-instance.ts`，本次同批上线）是**节点自证**，本门是**客户端观测**的第二道；
③ 与 codeHash 门相比它**更早**（不需等一个代码版本差异，同一份代码的进程换代也看得见），且在同端口双
进程下 codeHash 门是**抽签**（同一轮 ping 落在哪个进程上看 4 元组哈希），而 bootId 变化正是这个缺陷的指纹。

**被否决**：① plan 原推荐「先只记进 `dist-agent-meta.jsonl` 供归因、不加门」（用户改判：要更早止损）·
② **全局**（跨轮）钉：一次正常升级/重启就换 bootId ⇒ 会把「重启前后的一轮」误伤；按轮钉把影响面限在
触发变化的那一轮、下一轮自动回场（代价：轮中途重启的节点本轮不再被用——已接受并写进文档）·
③ 把这条塞进纯函数 `node_gate_reason`（判据需要**状态/账本**，纯函数不许长状态）·
④ mismatch 时顺手重启/升级节点（§1.4-2「不自动杀旧」同理）。

**违反后果**：对新 agent 以外也生效（对旧 agent 不 fail-open）⇒ 升级波期间把还没升级的节点整批排除；
拿 bootId 当**跨轮**判据 ⇒ 每次正常升级都白搭一轮；把这条判据换成「看 ping 命中哪个进程」（不做账本）⇒
它在只 ping 一次的腿上永远不会响（判据名存实亡）。

—— 全文（判据 / 四个消费面 / 已知代价 / 接线守卫与测试 / 门禁读数）→ `docs/nn/training-stack.md` §26
（agent 侧契约与真机配方 → `docs/nn/runtime-opt.md` §29）


**家族 I — it0 基线污染（评估腿）**

### §2026-09-25-evala-baseline-bc（2026-09-25，it0 基线污染修复）
> 决策 `DECISIONS.md` §2026-09-25-evala-baseline-bc。归档 2026-10-11（第二轮瘦身）。

**背景**：x20-dodge-l1/L3 的 `eval_log` it0 各混入 200 局后期权重读数（L1 混 it43、L3 混 it51），
基线 low 被带偏 6pp（L1 it0 34.0 真值应为 40.0）。门控 verdict 不受影响（独立段），日常分析已更正。

**根因（三段链）**：① `rl/eval_a_once.py --baseline` 缺省取 live `out`
（`tmp/<课>/weights.json`，每轮被训练覆盖）；② 幂等早退判据是 `iter=0 ∧ 同 wver`，
权重变了就不命中；③ 离线开课/重启自动补派基线（`shouldAutoBaseline`）⇒ 补派瞬间读到
新权重，照跑并写进 it0 槽。触发两次：L1 在 10:42 重启（it43 当时最新）、L3 在 15:45
重启（it51 当时最新）。下游放大：`kickstart-receipt` 取最后一条 it0 行当基线，控制台
基线显示同步被带偏。

**变更**：`--baseline` 缺省改取课程 `bc`（起点冻结权重，归档文件永不变）；`resolve_eval_ckpt`
抽成单一事实来源函数；bc 缺席响亮拒（不拿 out 顶）；日志/TS 注释同步改口。
`--ckpt` 显式永远优先；wver 幂等保留（bc 换了会自然重评）。

**被否决**：① **iter=0 槽写过一次就永久锁死** —— 杀掉合法场景（课程 bc 换了要重评基线）；
② **补派时传归档 ckpt 快照** —— 调用方（控制台开课）不知道归档布局，知识放错了地方，
权重归属是训练侧课程文件的事实，就该 python 侧从课程文件取。

**回归**：`nn-training/tests/trainer/test_eval_a_once.py`（bc 缺省 ×2：取 bc 断言 + 重启后早退不写别轮
wver；旧钉死 live-out 的用例已按新语义改写）+ `test_baseline_eval.py` 全绿。
—— 全文即本条 · 档案 `docs/nn/engineering.md` §25


**家族 J — 决策周期 K 通道**

### §2026-10-07-k5-decision-k-channel（2026-10-07，K→5 案 v2 评审修订 + 决策周期 K 通道全链落地 + 两课备好）
> 决策 `DECISIONS.md` §2026-10-07-k5-decision-k-channel。归档 2026-10-11（第二轮瘦身）。

- **背景**：`plan/k5-rhythm.plan.md` 评审（2026-10-07）发现 2 P0 + 3 P1：守卫口径三处不一（表 margin10 /
  注释 margin_pp 100 / filing"margin10"）；评估 K 未预注册且全链无通道（K5 臂会被 OOD 按 10 拍评估）；
  est 未按 K 重标定（K=5 沿用 147 ⇒ 每轮 2× 产样、一半被逐关配额裁废）；"K5>K10 ⇒ 稀释假说成立"归因过强；
  K 机制事实（旋钮已在 `src/nn/decision-gate.ts`，缺的是通道）。用户指令：处理评审 → 更新文档 → 按文档自主开发/测试/审查/签入。
- **口径修订（覆盖 `§2026-10-07-k5-filing` 原文"margin10"，全案以 plan v2 为准）**：守卫统一
  `{mode: baseline, margin_pp: 100}`（等效关，用户"熔断全关"指令；floor=基线−100pp 永不触发）；K5 臂
  `est 294`（=1470 ticks ÷ K=5）；评估 K **按各自臂**（K5@5、K10@10；终判显式 `--decision-k 5`）；
  结论措辞 = "节奏**总效应**"（两臂同带 9 维 ⇒ 测不到"列收益被兑现"交互；判该交互的唯一后继 = K5 下重做 A/A0，另案）；
  γ/λ 逐字不动 ⇒ tick 视野随 K 减半（固有耦合，登记不补偿）。
- **决定（K 通道）**：课程键 `decision_k`（默认 10；**仅 ≠10 激活**——老课程命令/URL/指纹逐字节不变）
  → `--decision-k` 贯通 rollout 链（`worker/cmd`）+ eval 链（`eval_local` / `dispatch` / `eval_dispatch` /
  `batch_runner` / `offline_eval` / `eval-course-ckpt` spec）→ wire `decisionK`（agent URL + taskKey `:k<N>` 后缀 +
  轮询同配方）→ 节点能力位 `decisionKSupport`（旧 agent fail-closed，绝不静默跑 K=10 混批）→ shard manifest
  `k` 记有效值；corpus_fp 仅非默认值进 payload。半格几何结论：玩家 1.05px/tick ⇒ 决策间隔位移 K=10→10.5px /
  K=5→5.25px（非 16px 格宽整数倍）⇒ 属 K 固有副作用，登记不修（原"半格对齐"词无定义，按此原始几何量落定）。
- **顺带修复（实现中发现）**：`common/distribution._poll_result` 轮询 URL 缺 `decisionEvents`/`decisionK`
  分量（提交键有 `:de1`、轮询键没有）⇒ 事件课程的 async 竞速副本必 404；一并补进。
- **实测取证**：同 seed/stage 冒烟——shard manifest `k=5/nSamples=300` 对 `k=10/nSamples=150`（2× 决策密度）；
  eval 报告 `decisions` 160 vs 80；坏值 `--decision-k abc` 三端响亮拒（rollout throw / eval exit2 / agent 400）。
- **两课**：`nn-training/curricula/x21-psh-k5.jsonc` / `x21-psh-k10.jsonc`——同起点（B0-it290，五份副本
  sha `2A658C67…3B6C` 逐字节同源，含 in-use 备份）/ 同流 20261013（预查空）/ 同预算 150 iter / 同守卫；
  唯一差别 = K（派生白名单对账通过：`backup_dir/backup_prefix/bc/decision_k/est/name/out/traj`）。**开课待人工（控制台）**。
- **落点**：plan/k5-rhythm.plan.md（v2）· 全文 → `docs/nn/training-stack.md` §31（索引 `docs/nn.progress.md`）·
  单测 `nn-training/tests/worker/test_decision_k.py` + `tests/{nn/decision-gate,agent/decision-events-taskkey,dist-node-gate}.test.ts`。
- **违反后果**：绕能力位给旧 agent 派 K≠10（静默 K=10 混批）；评估 K 留默认（拿 10 拍评 5 拍 policy）；
  est 不重标定（2× 产样一半裁废）——三者任一都让"唯一差别=K"对账失效。


---

> **第三轮归档（2026-10-11）**：`DECISIONS.md` 第四次瘦身把**仍带成篇正文、且正文 >6 非空行**的 **7** 条决策全文按主题落到本节（上一轮：三次瘦身切 >12 的 192 条 = 本节「第二轮归档」）。家族谱系**续上一张表**：沿用旧字母 = 同一族（链上含第一~三轮成员，全文在本节内），新族从本档下一个字母续编；只归类连线，结论引原文。`DECISIONS.md` 索引行末尾指针 = `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §<id>`。

**家族谱系（续上一张表，先后为时间序）**

- **家族 D｜起始分布注入（state_init）**（3 条，2026-09-25）：`§2026-09-25-state-init-course-key` → `§2026-09-25-state-init-snapshot` → `§2026-09-25-state-init-routing`
  - 末条决定（原文节录）：`state_init` 开 ⇒ 整轮直接交纯本机 `run_rollout`、向 pool 零派发，跟节点健康度无关……
- **家族 F｜配对止损参照物**（3 条，2026-09-25～09-30）：`§2026-09-25-clutch-null-kill` → `§2026-09-26-paired-kill-opt-in` → `§2026-09-30-goalnn-kickstart-burn-paired`
  - 末条决定（原文节录）：参照物两档。`baseline` = 旧口径（本腿 `it<=0` 末条）；`paired` = Δ = 本臂 − **对端同 it**……
- **家族 J｜决策周期 K 通道**（2 条，2026-09-26～10-07）：`§2026-09-26-goalnn-stop-k-is-training-side` → `§2026-10-07-k5-decision-k-channel`
  - 末条决定（原文节录）：**口径修订（覆盖 `§2026-10-07-k5-filing` 原文"margin10"，全案以 plan v2 为准）**：守卫统一……
- **家族 K｜B 案边界：非 MDP 变更 · RNG 不进 shard**（2 条，2026-09-26）：`§2026-09-26-goalnn-stop-mdp-no-change` → `§2026-09-26-goalnn-stop-rng-principle`
  - 末条决定（原文节录）：**特权观测一律不进 shard / 不进 obs**。判据①进 shard = 格式变更（`OBS_SCHEMA_MAJOR`……
- **家族 L｜云端多卡默认（DataParallel）**（1 条，2026-09-13）：`§2026-09-13-multi-gpu-default`
  - 末条决定（原文节录）：battle-rl.ipynb 单 cell 的 `use_multi_gpu` 默认 **True**——云端 2+ GPU 时……
- **家族 M｜门必须配平凡基线**（1 条，2026-09-28）：`§2026-09-28-goalnn-gate-baseline-8`
  - 末条决定（原文节录）：一切行为门（plan 课程/探针的通过线）在 pin 数字的同时，必须 pin 一个……

**家族 D — 起始分布注入（state_init）（续）**

### §2026-09-25-state-init-routing（2026-09-25，起始分布派发：改路由不改放行名单）
> 决策 `DECISIONS.md` §2026-09-25-state-init-routing。归档 2026-10-11（第三轮归档）。

**来历**：c4d42a04 的派发闸门（有可行远端节点就 `SystemExit`）把 51.9 万混语料变成了 6 秒
响亮拒，但 v1 因此构造性不可跑（self 也算节点，pool 健康时永远被拒；"只留本机"的自救指南
是错的——只留 self 照样 abort）。另一条路"放行 local 节点"错层：`fetch_task` 协议没有快照项，
放行≠给快照， self 经节点协议跑的还是标准开局。

**决定**：`state_init` 开 ⇒ 整轮直接交纯本机 `run_rollout`、向 pool 零派发，跟节点健康度无关
（`dispatch.run()` 入口，替代旧闸门；旧测试①按新契约改写）。additive-only 照旧。

**被否决**：按"本地与否"放行名单（缺 flag 的局照样进 pool，护栏全剔=无限波次重演，且无响铃）·
保留闸门等人下线节点（出路不存在，课程永远停车）。

**违反后果**：向 pool 派发一局 ⇒ 该局标准开局 + 护栏剔除 + 波次凑不齐（无限波次）。


**家族 F — 配对止损参照物（续）**

### §2026-09-26-paired-kill-opt-in（2026-09-26，配对杀臂默认关火）
> 决策 `DECISIONS.md` §2026-09-26-paired-kill-opt-in。归档 2026-10-11（第三轮归档）。

**来历**：`§2026-09-25-clutch-null-kill` 的对称自杀修了对照臂，但"谁该被比"没修——
state-init 出生 11 轮即被杀：拿它的 it5/it10 日常（12.0%/11.5%）去比**已归档** L1 的
it5/it10（16.0%/15.5%），连跪两点 −4pp。两腿同 V（1789876303）只是门派同源（全屋种子流），
不是配对实验；L1 当天已判负归档，拿它当"对端"比出"落后"是必然。

**决定**：`courses.<课>.paired_kill.enabled` 显式 `true` 才判（缺席/写坏=False；关了的守卫
连 streak 落账都不写）。C-w 设 `enabled: true`（课程要的中点杀臂仍在）+ C-0 保持
`self_kill: false`（只记录不停车）；其余一切腿（state-init/L1d2/L2a/L2b…）缺席=不判——
未来同 V 的活腿之间也不会交叉开火。

**被否决**：只给"归档腿"加存活判断（治标：未来两条活腿同 V 照样互杀；且"归档"无机器定义）·
守卫内写死白名单（课程对名单进代码，换一批腿改一次；执行面策略住 rl-config 已有先例）。

**违反后果**：默认开火 ⇒ 任何新腿出生即被拉去跟历史最强点比，连跪即死（state-init it11）。


**家族 J — 决策周期 K 通道（续）**

### §2026-09-26-goalnn-stop-k-is-training-side（2026-09-26，K=10 决策门周期属训练侧：可动）
> 决策 `DECISIONS.md` §2026-09-26-goalnn-stop-k-is-training-side。归档 2026-10-11（第三轮归档）。

**来历**：新纪元首刀（`plan/new-era-stop.plan.md`）反复出现「K 要不要收紧」的悬案——AGENTS §0.2
规定**任务侧**（命数/敌数/关卡形态/终局标准/物理冷却）不可动，而 K 是 NN 输入侧的决策周期
（盲 hold 的长度），既非规则也非关卡。

**决定**：**K 是训练侧可动参数**。改 K 只改「策略每隔多少 tick 重新观察一次」，不改任务的胜负
条件与单位 ⇒ 收紧 K（如 10→5）属于训练侧调参，不触发 §0.2 禁令，也不需要新关卡/新任务口径。
首腿保持 K=10（Phase -1 实测：人类非冰面停段中位 9 tick ≥ K/2，相容），收紧留作 x2 rung 期权。

**被否决**：把 K 归为任务侧（会让「10 tick 盲 hold」这个已确诊病灶永远不可修，与诊断②矛盾）；
首刀就收紧 K（与 STOP 单变量声明冲突，hy P0-4）。


**家族 K — B 案边界：非 MDP 变更 · RNG 不进 shard（续）**

### §2026-09-26-goalnn-stop-mdp-no-change（2026-09-26，B案不构成 MDP 变更声明）
> 决策 `DECISIONS.md` §2026-09-26-goalnn-stop-mdp-no-change。归档 2026-10-11（第三轮归档）。

**来历**：B案把 move index 0 从「保持朝向」改为 **STOP**——动作空间的**语义**变了，需要明确
声明它算不算 MDP 变更（会不会波及 state-init bank / 决策门 / divergence-probe）。

**决定**：**B案不是 MDP 变更**。状态空间、转移、奖励、K=10 的均匀决策边界全部未动；变的只是
「同一个动作索引代表哪个物理命令」，而该映射在仿真里本来就存在（`moveDir=null ⇒ moving=false`）。
⇒ 决策门 5 处（`t%K`）、divergence-probe、state-init bank 的 `t%K==0` 切点**全部无需改**
（Phase 0 #4「天然一致即验收」）。真正的 MDP 变更（threat-onset 事件化 / 半 MDP γ^Δt）**defer
到 x2 rung**，届时按 R2 包重新预注册。

**被否决**：把动作语义翻转也算 MDP 变更（会误导后续把所有基于 K 的装置重算——它们依赖「决策
边界」而非「索引含义」）；顺带做事件化（等于首刀变多变量，hy P0-4 / glm P0-3）。

**违反后果**：混为一谈 ⇒ 无谓重出 bank / 改决策门，且掩盖真正的 MDP 变更（事件化）根本没做。

### §2026-09-26-goalnn-stop-rng-principle（2026-09-26，RNG/特权观测不进 shard：原则存档）
> 决策 `DECISIONS.md` §2026-09-26-goalnn-stop-rng-principle。归档 2026-10-11（第三轮归档）。

**来历**：新纪元讨论中反复提出「给 critic/actor 特权观测」（RNG 状态、敌开火倒计时、tick 堆、
事件堆栈）以降低信用分配难度。评审指出这会改 shard 格式并使既有 value 读数整体偏移。

**决定**：**特权观测一律不进 shard / 不进 obs**。判据①进 shard = 格式变更（`OBS_SCHEMA_MAJOR`
bump + 全量重转），且会让已训 value 头的输出量纲漂移（旧 checkpoint 不可比）；②敌开火倒计时
等本就是**超人信息**——人类看不到，进了就破坏「以人类熟练玩家为难度唯一口径」（§0.2）。RNG 只走
`world.rng`（AGENTS §2.3），不进观测。需要时以**训练侧**手段解决（GAE λ / value 头 / 事件化）。

**被否决**：把 RNG state 塞进 shard（格式 + value 偏移双代价）；观测侧加倒计时列（超人信息出局）。


**家族 L — 云端多卡默认（DataParallel）（续）**

### §2026-09-13-multi-gpu-default（2026-09-13，云端多卡默认启用 DataParallel：PPO + BC；用户指令）
> 决策 `DECISIONS.md` §2026-09-13-multi-gpu-default。归档 2026-10-11（第三轮归档）。

- **决策**：battle-rl.ipynb 单 cell 的 `use_multi_gpu` 默认 **True**——云端 2+ GPU 时
  PPO 与 BC 都真跑多卡（PPO 走 worker 既有 DP 分支；BC 由 bc.py 新增 `_resolve_bc_device`
  + `_bc_raw` 支撑）。opt-out 保留：要与历史单卡 run 逐位 A/B 的腿把
  `use_multi_gpu` 改 `False`（或 device 显式 "cuda"）。
- **拒绝的替代方案**：维持 opt-in 默认 False——用户指令「双/多卡时训练跑在双/多卡上」
  直接否决「第二张卡闲置」；改为 opt-out 后，逐位可比性从默认诉求降级为显式诉求
  （等价于把「新实验臂」的判断权交给操作员的显式开关）。
- **边界**：单卡机器行为零变化；TPU 拒收链不变（bc 任务 tpu/xla 仍 ProtocolError）；
  DP 激活时 worker/bc.py 双侧响亮日志「梯度归约顺序变化，与单卡 run 不可逐位比」；
  DP state_dict 的 "module." 前缀防线 = bc.py `_bc_raw` + worker 既有 raw_model 模式。


**家族 M — 门必须配平凡基线（续）**

### §2026-09-28-goalnn-gate-baseline-8（2026-09-28，门必须配平凡基线）
> 决策 `DECISIONS.md` §2026-09-28-goalnn-gate-baseline-8。归档 2026-10-11（第三轮归档）。

- **背景**：wait-gate 的 F1≥0.60 门被一行“重复上一步”（F1≈0.69–0.72）击穿——
  门立了半年（plan §3 同款写法），从没人问过平凡基线是多少。
- **备选与否决**：“门数字面合理即可” —— 否；合理数字可能全是粘性/多数类/同义反复
  （本案三样占全：粘性 a=0.689、多数类、s22 后果变量）。
- **决定**：一切行为门（plan 课程/探针的通过线）在 pin 数字的同时，必须 pin 一个
  同口径平凡基线（重复上一步/多数类常数/单特征，共位计算），门过线且**显著超基线**
  才算过；泄漏消融（去后果变量重跑）是 P0 第一道门。
- **违反后果**：门成了同义反复的毕业典礼——模型学到粘性，账本记成功劳，下一腿在
  沙子上继续盖。
- 全文 → `docs/nn/experiments.md` §58。

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §23→§1 · 旧 §26→§2 · 旧 §36→§3 · 旧 §37→§4 · 旧 §38→§5 · 旧 §43→§6 · 旧 §44→§7 · 旧 §45→§8 · 旧 §48→§9 · 旧 §76→§10 · 旧 §77→§11 · 旧 §78→§12 · 旧 §79→§13 · 旧 §80→§14 · 旧 §81→§15 · 旧 §83→§16 · 旧 §84→§17 · 旧 §85→§18 · 旧 §86→§19 · 旧 §89→§20 · 旧 §90→§21 · 旧 §108→§22 · 旧 §118→§23 · 旧 §120→§24
