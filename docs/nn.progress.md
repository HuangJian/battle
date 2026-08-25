# NN Player AI — Training Progress Log

> All architecture changes, eval results, and lessons learned are recorded here.
> New entries are appended at the top (reverse chronological).

---

## §13 无道具纪元开启：M0 + M1（2026-08-26 凌晨，plan/AI-No-Items-Warmstart.md）

> 用户拍板「全部 AI 不使用主动道具 + RL 预热」路线；执行 M0（摘除道具）→ M1（分歧探针）。
> §14 记录 M2 语料纪元（OBS_SCHEMA_MAJOR 1→2）。

### 13.1 M0 God-AI 摘除主动道具（DONE，已 commit）
`superItemMode/GuardThreat 默认归零`。配对复测（A=ON, B=OFF, hard 60 seeds×35）：
胜率 76→75%、Δscore −0.0093±0.0025、t=−3.77、p=0.0002（Lattice 0.519→0.485）——
与预检 B 臂完全一致。DECISIONS §167 修订为 RETIRED by default；全文 → god-ai-tuning §M0。

### 13.2 M1 分歧探针（DONE）
新工具 `tools/diag/divergence-probe.ts`（预注册定义 + 后果代理指标 + 三桶归因）。
数据源：DAgger 学生权重（tmp/student-weights-dagger，schema v1）+ 教师观察。
结果（25 局 hard：s0-4 × sd0-4）：

| 桶 | 帧数 | 分歧率 | 静默率 | 有后果 |
|---|---|---|---|---|
| base（基地高压） | 7646 | **74.6%** | 72.1% | 191 |
| combat（交战） | 1964 | 63.7% | 63.7% | 0 |
| cruise（巡航） | 509 | 38.3% | 38.3% | 0 |
| 总体 | 10119 | 70.6% | 68.7% | 191 |

- 分歧率 70.6%（学生 0% 胜率的必然画像）；**静默分歧占绝大多数**（~97%）——大部分分歧
  是 120-tick 窗口内无可见后果的 tie-breaking/朝向差，不进归因。
- 分桶分布：基地高压桶分歧率最高（74.6% > 交战 63.7% > 巡航 38.3%），特征表完整。
- **归因结论（预注册规则）= ①/③ 边界**：基地高压桶分歧高且特征完整 →「标签或监督」——
  采取 **wins-only + 守家帧回补（near-miss 超采样）并预留 DAgger 交互采集轮**。
- 决策影响：M2 的 near-miss 超采样默认 3×（守家帧 = 环受损 OR 敌压基地 ≤12 格，
  即 M3 预注册守家帧判定的简化版）；M3 若 WIN <50% 则先补 DAgger 轮。

---

## §12.5 补丁：eval 本地参与（PPO 收尾后本机算力入列）（2026-08-25 傍晚）

> 用户指出的容量缺口：eval 只派 HTTP 节点，PPO/采集收尾后训练机 idle 无贡献；
> 极端情形（无可用节点）整轮评估直接 skip。

- **实现**（rl/eval_dispatch.py + run_rl.py）：① 派发时刻把 rl_path 复制为
  traj_dir/_eval_frozen_weights.json 冻结快照（主循环 PPO 写回会原地覆盖
  rl_path，本地局读错版本=对账灾难）；② `run_local_eval_game` 本机直跑
  export-eval-game.ts，补 wver/mode 戳后走同一 validate_eval_result 与台账聚合，
  summary 的 nodes 字典自然出现 "local" 键；③ `local_gate`（threading.Event）
  由调用方在「梯度步已尽」时 set——流式=末波排水完、串行=PPO 完成，join 前置
  set；worker 放行前每 5s 醒来看 deadline，不让位时零开销；④ 无节点时 gate 存在
  即 local-only 继续跑（旧行为=整轮 skip）；槽位 policy.evalLocalSlots 默认 4、0=禁用。
- **测试**：test_eval_local_gate 双子用例（gate 开→6 局全落 local+summary 聚合；
  gate 关→runner 零调用+窗口到期 dropped 全记），快速层 ALL PASS ×2 连跑。
  教训复训：台账按 wver 去重，跨进程残留 fixture 会把用例推进 skip 早退——
  work 目录必须 rmtree 重建；断言基线要显式捕获而非硬编码。
- **本地参与保底三件套**（同日追加，用户质询「local 完全没参与 dist 和 eval」驱动，
  语义按用户修订定稿）：
  ① `--local-slots N` 显式 CLI（0=自动 max(2,workers//4)），**调度语义 =
  「dist 阶段最先被分派 → PPO 启动即让位 → PPO 结束转 eval 尾段」**：
  · dist：洗牌后前 N 个任务划入本机专用队列（头部分配，节点线程不可触及，
  确定性保底）；首个 PPO 波次启动置 local_suspend → 本机停止领新任务、保留段
  一次性并回主队列交远端消化（防饿死；远端集体失联超 remoteDeadSec 时让位
  自动失效）。附带修复两处真缺陷：完成判定/missing 原用切割后的 len(tasks)
  会提前 all_settled（日志溢出 3/2 可见）；_fast_enough 的 EWMA 持留曾把 local
  彻底饿死——现豁免（上限+让位自治理）。
  · eval：gate 放行前节点不取最后 evalLocalSlots 局（hold_for_local 纯函数，
  距窗口截止 300s 强制释放），派发行打 `[local tail-reserved ×N]` 标记。
- 生效时机：运行中的进程已加载旧模块，**下次 relaunch 生效**。

---

## §12 R6 破局三件套落地 + 评审修订与重启记录（2026-08-25 下午）

> §11 审计定位 loss-band 局部最优后，实施三方向改动（奖励重做 / 课程化 / PPO
> 收紧），顺带修复两个潜伏奖励 bug。本节含独立评审结论、执行修订（A/B/C）与
> R6 首次启动参数——旧谱系数据已归档，见 §12.4。

### 12.1 改动摘要（均已验证）
- **奖励重做**（export-rl-rollout.ts）：`RL_LOSS_WEIGHTS` 显式重写为守家优先
  （baseIntegrity 0.25 + baseSafety 0.25 + progress 0.30 + tempo 0.08 +
  accuracy 0.06 + openingTempo/loot 各 0.03，和=1.00）；`BASE_LOSS_MULT
  0.25→0.1`（失守更贵）。七键在 lossPartialQ 全部有产出。
- **两个潜伏 bug 修复**（评审独立复核属实）：① tempo 曾除以 `w.tempo(=0.026)`
  → 恒饱和无梯度；② accuracy 因旧 DEFAULT_LOSS_WEIGHTS 无键恒 null。现统一用
  `DEFAULT_STAGE_REFS`（kpmRef=8 / accuracyRef=0.3）。
- **课程化**（rl/course.py + run_rl.py）：`--curriculum-stages/start/every/grow`，
  纯函数 `(order_len,it)` 确定性扩展，种子流 `[rotateSeed,0xC0E,it]` 键控，
  断点续跑安全；排序取自逐关干净评估胜率（与数据重算一致，差异在平局噪声内）。
- **PPO**：GAMMA 0.99→0.995（K=10 决策间隔下信用时域 16.7s→33s，守家是长时域
  行为）、VF_COEF 0.5→1.0。串行/流式双路径经模块常量自动继承。

### 12.2 评审核实与技术性备忘
- 门禁复跑全绿：python 快速层 ALL PASS / tsc 干净 / oxlint 0 error；bun test 的
  12 个失败均为 God-AI 既有行为测试（tests/ 无文件 import 被改模块，src/ 未动，
  归因成立）。Σr≡SCALE×gatedScore 恒等式由终局对账结构保证。
- **kpmRef=8 会再饱和**：God-AI-Evaluation-Redesign.md 已载明默认 kpmRef=8 对
  强手恒饱和（标定后逐关 15–29）。当前水平（~1–4 KPM）梯度可用；策略进步到
  8 KPM 后应换逐关 refs（后续项）。
- 训练 Φ 与评估维度口径不同（评估套件用逐关标定 refs）——终局对账保证正确性，
  但巡检对比 dims 时须知非同源。

### 12.3 执行修订（评审要求，已全部落实）
- **A 轮规模**：启动命令补 `--seeds-per-stage 3`。缺省 10 会令课程满员时达
  350 局/轮且 KL 预算回归必触；35×3=105 局贴合预算。⚠️ it73/74 实测每局成本
  上行至 ~0.0022–0.0025（此前 0.0018），cap=0.20 实际预算仅 ~85 局——课程成熟期
  若 dropped_games 回升，决策规则：实测满轮成本 ×1.1 抬 cap，或 epochs 4→3，
  不盲抬。
- **B 节点同步**：codeHash 只覆盖 export-rl-rollout.ts + src/nn/**，故仅需提交
  后各远端节点 `git pull` + 重启 agent（部署契约见 plan/distributed-rollout.md；
  自动化暂缓属人工步骤）。每迭代重读配置重新 ping，可懒同步中途归队。未同步
  期间以 self+local 容量运行（it73/74 即此状态，~950s 收齐 105 局仍可行）。
- **C 历史归档**：training_log.jsonl（74 轮）/eval_log.jsonl/dist-agent-meta.jsonl
  与旧 RL 权重快照移入 `nn-training/archive/pre-R6-20260825/`；tmp/rl-traj 清空
  重建（保留默认目录以维持自动巡检 HTML）。fresh restart 决策依据：新奖励下
  value 头尺度失准是真代价；「policy 沿用 + value 重置」折中案因科学对照性与
  旧谱系已完整归档而未采纳。

### 12.4 R6 启动参数与观察项
- 启动：`start-training.{sh,ps1} -Script run_rl.py --iters 0 --stream 1
  --seeds-per-stage 3 --curriculum-start 4 --curriculum-every 8 --curriculum-grow 4
  --curriculum-stages "13,1,16,21,8,4,15,31,29,0,33,22,14,17,10,2,27,34,30,3,
  11,12,18,19,24,5,7,23,28,6,9,20,25,26,32"`（顺序=逐关胜率降序）。
- 观察：① 干净评估胜率是否突破 ~13%（历史单轮上限；注意相邻轮翻转噪声底
  13.2%，看 5 轮滑动均值）；② base_destroyed 占比是否从 ~80% 下行；
  ③ **龟缩失败模式**（防守占 50% 的新风险）：timeout 占比上升 + progress 中位
  数下滑即预警；④ dropped_games 是否保持零（验证 A 修正）；⑤ entropy 是否随
  dense 防守信号锐化。
- 判定规则：同墙钟对照点（~it20）干净评估 5 轮均值 ≥ 旧谱系同期（7–9%）为
  有效；持续 ≤ 则考虑回滚权重表单因子（GAMMA/VF_COEF 回滚需改常量，无 CLI 入口）。

---

## §11 收敛性审计：it1-68 平台期定量画像（2026-08-25 午后）

> 用户质询「it1 至今似乎没有收敛和提升迹象」。全量日志定量审计结论：**观察基本
> 成立，但需精确化——初日确有一波真提升（BC≈0% → ~10%），其后 60+ 轮胜率平台；
> 策略仍在动，但动的全是奖励指向的非胜负维度。**

### 11.1 数字
- **rollout 胜率分段**（探索采样）：A it1-12=10.2% / B 13-29=11.2% / C 30-47=10.2% /
  D 48-59=9.3% / E 60+=8.6%；斜率 -0.33pp/10轮 ±0.15（≈2σ 轻微下行，跨机制变更
  证据强度中等）。entropy 斜率 -0.0013/iter±0.0003（1.31→1.22 后回升）；每步 KL
  0.021→0.039 上行 = 更新趋激进而收益不涨，追噪声特征。
- **干净评估**（贪心 70 固定局）：7.9%/7.6%/8.8%/9.0% 分段持平，斜率 ≈0±0.03pp。
  **噪声底实测：相邻轮同键胜负翻转率 13.2%**——单轮 ±5pp 读数全是噪声；配对
  it24 vs it68 净胜 +1 局、score 差 +0.024±0.025 不显著。
- **真实进步在维度层**：it24-32→it60-66 干净评估 progress +0.044 / tempo +0.047 /
  openingTempo +0.037 / mobility +0.053；accuracy -0.024；baseIntegrity 0.245 持平；
  score 0.229→0.245 微涨。

### 11.2 定性：loss-band 局部最优
reward = v7 loss-band 势差密集结算（RL_LOSS_WEIGHTS 里 lives=0）+ 终局
Σr≡SCALE×gatedScore、base_destroyed ×0.25。策略精确优化了这份梯度——「输得更有
质量」（多杀/快节奏/跑位好），而 loss-band 分数可在永不通关前提下持续改善 →
梯度停在「会死但死得漂亮」的局部最优。防守侧无 dense 信用（baseSafety 只在终局
间接体现），baseIntegrity 卡 0.25 不动即症状。教师 God-AI 参照 72%，学生 9%。

### 11.3 若要胜率突破的候选杠杆（均属用户级决策，未实施）
① 防守项入 dense Φ（baseIntegrity/baseSafety 进势函数）或恢复 lives 权重试验；
② 课程加权：对已有胜局的关加权采样（可学梯度所在）；③ self-imitation：干净评估
胜局回灌 BC 正样本；④ 工程侧配套：target_kl 早停（控 KL/step 上行）、best-clean-eval
checkpoint 归档（当前权重只进不退无法回滚峰值）。沿现奖励继续爬的预期：score 维度
缓慢改善，胜率难破平台。

---

## §10 streamKlCap 预算重标定 0.12→0.20（2026-08-25 午）

> 起因：用户质询「最新几次 105 局 rollout 全触发 KL 熔断」。逐波取证（detach
> stdout 日志）后定性：**不是策略失稳，是预算算术脱节**——每训练一局的 KL 成本
> 实测 ~0.0018（it61: 0.132÷69 局），cap=0.12 只买得起 ~67 局；105 局全量需
> cum_kl≈0.19~0.21 → 结构性必触。

### 10.1 取证要点
- 触发的是 **streamKlCap 轮内熔断**（Σ各波均值 >0.12 停派发+dropped 记账），
  不是 F4（每步均值 kl 0.036–0.047 远低于 0.15×3）。
- it60/61/62/64/65/66 全在**第 4 波后、训满 ~69 局时停车**（cum 0.124–0.153，
  dropped 13–37）；唯一未触顶的 it63 恰是续跑轮（仅 2 新局）——天然对照组。
- 波均 kl 与串行时代全轮均值同量级（串行 0.02–0.03 vs 流式首波 0.02–0.03、
  末波 0.046），逐波缓慢递增属波间漂移累积，无异常跳变。
- §8 已记录熔断自流式上线起 33/50 轮静默触发；R2 的「sps 4→3 贴预算」估算
  偏乐观（预算实际只够 ~70 局，105 仍超 ~50%）。
- 影响面：干净评估胜率维持 0.04–0.13 平台无崩塌——代价是每轮 ~1/3 语料白采 +
  训练子集偏向先结算的短局 + it60 高漂移后 it61 rollout winRate 一度 0.0095
  （同轮干净评估正常，探索态扰动非永久损伤）。

### 10.2 决策与观察项
- **决策（用户拍板 A1）**：`dist-nodes.json` policy 增加 `"streamKlCap": 0.20`
  （≈105 局成本 0.19+增长余量）。热生效：主循环每迭代重读配置，无需重启进程。
  软降档点随动至 0.14（~第 78 局收缩波次），仍保留熔断兜底。
- 观察：① dropped_games 应归零或个位数；② 干净评估胜率是否因单轮移动幅度变大
  而波动加剧（对照 it61 教训）；③ 若某轮 cum_kl 实测 >0.21 仍触顶，说明单位成本
  上行，届时考虑 B3（ppo_update 波内 target_kl 早停）而非继续抬 cap。
- 未采纳：A2 缩轮（sps→2）、B1/B2 降 epochs/lr（拖慢学习）、删除熔断（它是唯一
  轮内漂移约束）。中期候选：B3 target_kl 早停 + C1 预测性停派发（用剩余预算提前
  停派，可把 in-flight 白采砍到个位数）。

---

## §9 Python 侧工程化重组 + 常驻单元测试（2026-08-25）

### 9.1 结构（nn-training/rl/ 新包）
run_rl.py 从 ~1700 行瘦身为 ~500 行入口（CLI + 迭代主循环 + 权重归档/巡检），
编排逻辑抽取至 `rl/`：course（课程纯函数）/ queue（中央队列+本地回退）/
stream（流式波次）/ eval_dispatch（干净评估）/ resume（断点对账）/
reports（聚合）/ breaker（F4 纯逻辑）/ log。入口必须留在顶层——启动器只接受
裸 .py 文件名；`rl/queue.py` 自算 REPO_ROOT（parents[2]）。run_rl 保留全部
re-export，旧引用路径不破。

### 9.2 可测试性抽取与潜在缺陷修复
- **breaker_update(kl_streak, ent_streak, *, kl, entropy, win_rate)** 纯函数化
  （阈值+连击判定），主循环改调它；顺带修复潜在缺陷：流式 checkpoint-complete 轮
  agg=None 时旧代码直接读 agg["kl"] → TypeError → 被兜底 except 吞掉后原地重试。
  现在 agg None 短路（不计连击不告警——本轮本无梯度步）。
- **wave_params(cum_kl, kl_cap, wave_games, wave_cap)** 纯函数化（软降档阈值）。

### 9.3 测试（test_run_rl.py 常驻，禁临时脚本）
快速层默认跑（~30 断言）：parse_range / build_pairs 确定性+sps 重叠=6 回归 /
combine_reports / completed_pairs+resumed_manifests(only/exclude) / jsonl 锚点 /
breaker_update 全规则 / wave_params 边界 / compute_gae 手算用例 / chunk_episodes
ragged / backup_weights 有界清理。集成层 `--itest`：假 HTTP 节点驱动真队列与
流式轮（结算、halt、queue-drained 次序契约、评估恰一次）。教训入册：
① float32 ret 与 float64 重算差 ~1.2e-8 会越过 allclose 默认 atol——手算用例
必须带显式容差；② 大块 SearchReplace 在该文件上会模糊匹配失真（缩进漂移/
整文件截断各一次），结构性移动优先用「新文件 Write + 入口重写」，行内小修才用
SearchReplace 且改后必 py_compile。

---

## §8 流水线衔接优化：KL 遥测补盲 + 熔断止损 + dist/eval 解串（2026-08-25）

> 起因：用户质询「跑这么多轮 KL 都没超 0.1」——jsonl 的 `kl` 在流式模式下只是
> **末 wave 单值**，而轮内累计 cum_kl 实际在 50 轮里触发熔断 33 次（66%，通宵段
> 95%），max 0.129–0.155。遥测盲区让最常触发的路径无人察觉；且熔断后已结算语料
> 整批丢弃、远端仍在采无人消费的局（it53 实测 53/140 局白采）。

### 8.1 落地（run_rl.py / ppo.py / rl-hourly-inspect.ts）
- **KL 记录**：jsonl 新增 `kl_cum`（流式=Σ各 wave，串行=单次更新均值）、
  `halted`、`dropped_games`、`waves`；HTML 巡检「KL 累计」列优先显示 kl_cum，
  ⛔N 角标标注熔断丢局数（旧日志行无 kl_cum 时回退显示单值 kl）。
- **R1 熔断止损**：`halt_event` 贯穿 run_rollout_queue——触顶后 worker 停止领取
  新任务（在途局自然收尾），报告带 `halt_aborted`；cum_kl 过 cap 70% 后软降档
  （wave 12→4 / drain cap 24→8），把过冲从一个整波压到个位数局。
- **dist↔eval 解串（用户指令，同日修订一次）**：初版按「权重分发完毕即并行派发」，
  用户复核后纠正为**中央派发队列清空**时触发——全部采集任务已交到节点/本地线程、
  结果仍在途，评估局顺势填收尾空槽；既不与采集全程抢节点（初版问题），也不必等
  全部结果落定（回到串行时代）。熔断 / collector 收官仅兜底再触发（eval_fired
  护栏去重）。评估线程句柄经 report["_eval_thread"] 回传主循环，jsonl 写回前
  join——修复流式模式评估线程此前根本不被 join 的隐患。**仅流式改此时机**；
  串行关键路径是采集→PPO，评估提前会抢采集节点反而拉长整轮。
- **rollout_sec 锚点修正**：collector 线程 finally 里记 box["t_end"]，主线程以它为
  collect_done——此前最后一个 in-flight wave 的 PPO 时长被误计入"纯采集窗口"
  （即 rollout_sec − pure_collect_sec 缺口 450–650s 的真正来源）。
- **新增耗时字段**：`dist_phase_sec`（ping+POST 阶段）、`tail_drain_sec`、
  `load_sec`（_load_wave 的 npy IO+GAE，此前完全不可见）、`eval_join_sec`
  （轮间气泡直接观测）。join 前置于 jsonl 写回以便同轮入账；崩溃时断点续跑按
  「语料秒回 + PPO checkpoint 完整」路径无损重放。
- **ppo.load_shard astype(copy=False)**：obs 每轮 ~550MB 的无谓 dtype 拷贝消除。
- **UI/遥测微调（用户指令）**：「KL 累计」的口径说明从表头 th-note 移到迭代健康表
  下方；`run_local` 注入 `report["elapsedSec"]`（整局墙钟，与远端 manifest 同口径）
  → dist-agent-meta.jsonl 的 local 行开始带耗时，「采样机健康」表的局均耗时列对
  local 不再恒为 '—'（数据自下次 relaunch 起积累）；串行模式 local 也因此进入
  tail-dispatch 快慢分档速度表。
- **断点续跑「计划口径」修复（it60 实测驱动）**：sps 4→3 后重启同一迭代，目录残留
  旧计划同权重 shard（140 局 vs 新计划 105），旧逻辑把两者混算——resume 日志显示
  「140/105」歧义、报告聚合混入 134 局计划外语料。修复：`completed_pairs` 结果与
  `plan_set` 求交后再算缺口；`resumed_manifests` 增加 `only=` 计划内过滤；日志改为
  「planned pairs on disk (+ ignoring N off-plan shards)」无歧义格式；dist 遥测新增
  `offPlanShards`；早退路径补齐 missing/expectedGames/dist 与全流程同 schema。
  重叠恰为 6 局的数学：sps 变更只改变每关种子索引窗（旧 [4i,4i+4) / 新 [3i,3i+3)，
  底流前缀一致），仅置换前三关相交，3+2+1=6，实测逐位吻合。

### 8.2 设计判断
- **F4 熔断仍读 `kl`（每梯度步均值）不读 kl_cum**：阈值 0.15 是按每更新均值标定
  的（健康带 0.045–0.054）；喂 Σwave 值（常态 0.12–0.15）会三轮即假熔断。轮内
  漂移的治理者是 streamKlCap 本身，kl_cum 只负责可见性。
- **远端全挂时没有本地 eval 兜底**（确认过的现状）：节点配置缺失→纯本地路径
  根本不走 eval 分支；节点配置了但全 ping/POST 失败→回退本地后 dispatch_eval
  因无 evalSupport 节点而 skip。如需本地贪心评估需另起 runner（未做）。
- **R2 配方建议（未改代码）**：PPO 已是瓶颈（ppo_cpu≈927s vs 纯采集≈540s），
  下次启动可试 `--seeds-per-stage 3`（140→105 局）让采集量贴 KL 预算走。

### 8.3 生效与观察项
- 当前运行中的 run_rl 仍是旧代码（run_rl 无锁文件）；改动在下次 relaunch 生效。
- 观察项：① `dropped_games` 应显著下降（软降档+停派发）；② `load_sec` 占比决定
  是否值得做双缓冲预装载；③ eval 与采集并行后 pure_collect_sec 会略涨——只要
  迭代墙钟下降即为净赚；④ `eval_join_sec` 若仍常态 >0，考虑再压 eval 语料。


## §7 干净评估嵌入流水线（2026-08-24 晚，DECISIONS §245）

> 背景：采集成本趋零后，training_log 的 winRate 仍是"带探索噪声的移动靶"——
> rollout 采样熵≈1.27 nats + 每轮 rotate 换 seed，轮间 ±5pp 摆动无法归因。
> 用户拍板：PPO 空窗期把固定语料贪心局分发到全部 agents（权重恰为上一轮 PPO
> 产物，与本轮 rollout winRate 同策略直接对照），评估墙钟藏在 PPO 计算里零成本。

### 7.1 落地（四处）
- `tools/sim/export-eval-game.ts`（**新文件**）：argmax 贪心单局 runner，纯 v7 打分
  （无 F3 门控），只写 `_eval_report.json`。干跑双执行字节级一致（DETERMINISM OK，
  s0/860001 → base_destroyed@3547 ticks、8 杀、score 0.144）。
- `sampler-agent.ts`：`mode=eval` 任务路由 + ping/status `evalSupport` 能力声明 +
  manifest 回显 mode；权重切换删除改尽力而为 + retention 清扫（修在飞评估局
  Windows EBUSY 竞态——此前切换只在 140/140 全结算后发生故未暴露）。
- `dist_common.py`：`fetch_task(mode=)` + `validate_eval_result()`。
- `run_rl.py`：rollout 返回后 spawn 守护线程；语料 `EVAL_SEEDS=(860001,860002)` ×35 关；
  收账 `tmp/rl-traj/eval_log.jsonl`（逐局行 + eval_summary 行，按 wver16 去重断点不重评）。

### 7.2 关键设计判断
- **不动 export-rl-rollout.ts**：codeHash 是 rollout 准入硬门，动它 = 五节点全部
  剔除直到同步完。独立文件 + ping 能力声明 → 逐节点灰度，旧 agent 零影响。
- **节点门四条件**：enabled ∧ ping ∧ evalSupport ∧ bun major.minor 一致。旧 agent
  会静默忽略 mode 参数把评估跑成采样局——能力声明是防误吃的唯一闸门。
- **iterId 后缀 `ev`**：agent 结果缓存键空间天然隔离。
- **读数对照**：eval_summary 行带 rolloutWinRate 同列——clean vs sampled 的系统性
  偏移本身是健康信号（贪心通常更高；若反而更低说明熵还在乱开火）。

### 7.3 生效条件与观察项
- 生效需各节点同步代码并重启 agent（ping 出现 evalSupport 即点亮）；trainer 重启时
  新参数默认已开（--eval-games-per-stage 2）。
- 观察项：① clean winRate 曲线斜率 vs 墙钟——这才是收敛速度的真裁判；
  ② dropped 计数若常态非零，调大 --eval-window-sec；③ 后续可挂 HTML 趋势报告。

### 7.4 流式模式时序缺陷（实跑发现，21:05 修复）
- **现象**：it23 rollout 收官后始终无 [eval] 行。根因：§7.1 的钩子位置假设
  "rollout 返回后有长 PPO 空窗"，这只在串行模式成立；流式的空闲窗已被
  run_rollout_stream 内部 drain 吃掉，返回后距下轮权重分发秒级——后台派发的
  70 局会全体撞上权重切换作废。
- **修正**：串行=后台隐藏（不变）；流式=阻塞执行（预算 --eval-window-sec，
  默认 1500→900s）。诚实账：流式下评估是显式流水线阶段（每轮约 +5min），
  "零成本隐藏"只在串行模式成立。
- 本机 agent 已重启点亮（ping evalSupport=true, agentVersion=55645d4），
  权重缓存已手动幂等补发（避免 self 本轮熔断）。

### 7.5 最终形态（21:57 实跑验证通过）
- 阻塞式也被推翻（用户纠偏：原设计就是"PPO 跑的同时跑 eval"）。终案：
  **eval 派发提前到 collector 收官时刻**（run_rollout_stream 新增
  on_collect_done 回调），与 drain 完全重叠；主循环在写回 jsonl 后才 join
  （预算封顶），保证下轮新权重分发前评估必已收官。
- 同时按用户指令：流式模式禁用收尾智能分发（tail_dispatch=False，无脑转发；
  串行模式保留），并加 drain 横幅日志（积压 < wave 阈值时打印）。
- **实跑验证（it25）**：round done 21:57:23 → eval dispatch 21:57:36 → DONE
  22:04:04（4/70 = 5.7%，dropped=0），期间 drain wave 持续推进至 22:06+——
  评估墙钟完全藏进 drain，迭代间零额外等待。it24（阻塞旧版）：clean 2.9%
  vs sampled 11.4%；it25 clean 5.7%。clean 曲线正式开画。
- 观测注记：drain 横幅在积压降至 wave 阈值下方才打印，属正常节奏。

---

## §6 训练可观测性落地 + 权重逐轮归档 + mb1024/workers10/self-agent2 重启（2026-08-24 早）

> 背景：it2 的 PPO 阶段跑了 **110 分钟全程零输出**（旧代码只有首尾两行日志），
> 任务管理器只见 python ~50% CPU，无法判断进度与健康。用户要求阶段性日志。

### 6.1 可观测性（纯打印，零 RNG/数值影响）
- **ppo.py**：① 分片加载进度（每 128 局一行）；② 更新心跳（≥60s 一行：epoch/chunk/
  step/elapsed/**eta** + 最近 32 chunk 滚动 kl/entropy/policy/value/gnorm）；③ 每 epoch
  汇总（含 ckpt 落盘确认）；`gnorm` 入 stats（clip_grad_norm_ 返回值顺手捕获）。
- **run_rl.py**：本地 rollout 每 10 局结算一行（as_completed 重排，结果按原索引回填）；
  队列模式逐局 settle 行（node/stage/seed/elapsed）；missing 行附结算进度。
- **生产验证（it3）**：rollout 140 局逐局可见；PPO 心跳实时读数 kl≈0.014–0.018、
  entropy 1.33 稳定、gnorm 1.3–1.6、ETA ~2000s——观测黑洞消灭。

### 6.2 权重逐轮归档（用户指令）
- 每次 PPO 写回后 `shutil.copyfile` 至 `nn-training/weights/rl-weights.it<N>.<YYYYMMDD-
  HHMMSS>.json`；保留最近 20 份（有界增长）。命名**刻意避开** weights_io
  `weights.<ts>_ep<N>_val<V>` BC 自动发现正则（§5.3 手工备份同理由）；
  glob 清理只认 `rl-weights.it*.json`，手工备份永不被删。tmp 合成数据单测通过。

### 6.3 重启（用户指令：mb 512→1024、workers 2→10、本地 agent --workers 2）
- it2 PPO 于 07:38 自然完成（kl=0.026 健康）后停机重启，rotateSeed 继承、自 it3 续跑。
- **效果**：rollout 17.5min→**6.7min**（workers 2→10 直连 + macos 8 并发）；PPO 语料
  回归正常轮规模（190 局/192 chunks×4 epochs≈768 步），ETA ~33min（对比 it2 膨胀语料
  110min——频繁重启合并历史 shard 的隐性代价再次确认）。

### 6.4 事故与教训
1. **dist-nodes.json concurrency 必须 ≤ agent 实际 --workers**：首轮 self 按 10 并发打
   2-worker agent → 3 连 `503 busy` → nodeFailStreak=3 即熔断出局。503 是协调器侧
   计数的失败，重试还烧 MAX_TASK_ATTEMPTS。已改 concurrency=2（下轮 ping 生效）。
2. **配置文件被神秘回写**：07:35 编辑的 self.concurrency 10→2 在重启前被改回 10
   （无进程应写此文件；疑运维编辑器旧缓冲保存）。二次修改后需在后续轮次复核，
   若复发需查写入方。
3. 心跳 ETA 用"本 run 已完成步均速"外推，断点续跑时偏乐观——可接受。

### 6.6 报告口径修复 + 收尾智能分发（tail dispatch）（2026-08-24 午后）

- **巡检报告漏计远程局（真 bug）**：`scanIterDir` 只匹配本机 `w*` 目录，
  `dist/<node>/rl_*_seed*/manifest.json` 全部漏扫——整份 HTML（累计/逐轮表/
  各关表现/首胜）只统计了本机直连份额。远程 manifest 字段与本机导出摘要同构
  （stages/seeds/scoreList/outcomes/totalTicks/dimLists），直接按 RlReport 消费；
  dims 走 dimLists 回退（loot 列远程局暂缺省）。重置账本全量重扫：
  **5 轮 ×140=700 局入表，crossCheck 0 不一致**（修复前每轮都在悄悄报"扫描≠日志"）。
- **报告 UX 四项**：移除「本段新胜局明细」表；「最近迭代健康指标」时间列明确标注为
  **完成时刻（PPO 写回时间）**——jsonl 的 time 字段在 PPO 完成后写入；entropy/KL/
  局均 ticks 列头标注告警阈值（与 healthVerdict 规则同源）；「采样机健康」新增
  「上轮局数」列（meta 按 it×node 聚合，全局最新迭代为基准，未参与=0）。
- **收尾智能分发（tail dispatch）**：此前中央 deque 自由竞争，慢节点可能在
  135/140 时抢走最后一局长局，PPO 空等。现按局均耗时 EWMA 分快/慢两档：
  速度表用跨轮 dist-agent-meta.jsonl 最近 20 局播种、本轮 α=0.3 在线更新；
  队列剩余 ≤ 快速集群单波容量（快节点槽位和，含本机 workers）时慢节点让路
  （`policy.tailFastFactor=1.8` 分档）；120s 无结算进度则饥饿兜底重新准入
  （`policy.tailGraceSec`）。无样本节点乐观按快速处理。分配仍属实时负载语义，
  洗牌确定性不变。**下次重启训练生效。**
- **流水线提案分析（rollout/PPO 重叠）**（→ **被 §6.7 推翻**：否决理由中的
  "语料跨版本"不成立，见 §6.7 的修正）：①前提纠正：当前 rollout 完全结束后才进
  PPO，PPO 独占整机；~50% CPU 是小模型+串行分发的 torch 天花板（§3.10），不是被
  采样抢占，减 worker 无益。②~~严格依赖链……~~（误判：轮内权重本就冻结）。
- **实际可用的提速杠杆仍是缩短 PPO**：epochs 4→3（线性 -25%，KL≈0.02 远低于
  0.08 有富余）或继续观察 mb=1024 后的 KL 再定。

### 6.7 流式迭代落地（--stream 1）：推翻 6.6 的流水线否决（2026-08-24 下午）

- **用户驳倒了两条反对理由**：①"语料横跨两个策略版本"不成立——权重分发只发生在
  迭代边界，整轮 rollout 期间 agent 持有的都是 W(N)，任意时刻到达的语料都出自
  同一策略版本，on-policy 比率数学不受到达顺序影响；GAE 用采样时存储的 value，
  与装载时机无关。②"省 7 分钟不值"是误判——35min/轮 × 几百轮的持续复利，
  改造成本是一次性的。
- **实现**（run_rl.py `--stream 1`，默认关闭；串行路径零改动保字节基线）：
  collector 线程跑 run_rollout_queue（新增 `on_result` 回调 + `local_slots_max`
  参数），本机槽压到 max(2, workers//4) 给 torch 让核；主线程每当积压 ≥12 局
  （policy.streamWaveGames）装载这批 shard（load_shard+compute_gae+wave 内 adv
  归一化）、chunkify 后按 --epochs 遍 ppo_update——每局总更新遍数与串行一致。
  轮内累计 KL 超 policy.streamKlCap（默认 0.12）即转"只采不训"。断点续跑轮
  零结算时回退全量磁盘更新。
- **与串行的语义差异（有意为之，需观察）**：adv 归一化从全轮变为每 wave；
  各 wave 的更新分布在 θ 漂移轨迹的不同点（PPO clip 容忍范围内）；流式期间
  不落 PPO epoch checkpoint（崩溃重启该轮重训，语料靠 completed_pairs 秒回）。
- **验证状态**：py_compile 通过；待下次重启以 --stream 1 实跑一轮观察
  wave KL / entropy / winRate 曲线后再定是否默认开启。
- **流式首次实跑三连 bug（15:31-15:43，全部修复）**：①包装函数返回 report 本身，
  主流程却取 `meta["report"]` → KeyError；②断点续跑"剩余 0 epoch"时
  `stats[0]` IndexError（ppo_update 空聚合无守卫，已加零聚合返回）；③本地局
  shard 目录多一层子目录（w9/rl_s30_seed*/obs.npy），loader 拿工作目录当 shard
  找 obs.npy 扑空 → 本地局全部被 skip（加 `_shard_dir` 探测含 obs.npy 的层）。
  三连杀导致 it13 重试 5/5 耗尽进程退出一次。
- **it13 假零指标事件**：skip-update 路径曾以全零聚合写 jsonl（entropy=0/kl=0），
  报告显示 0.0000 且会误触健康判定。修正为 agg=None → jsonl 写 null → 报告 '—'，
  历史行已补正（真实值只在旧进程日志：kl≈0.0258 / ent≈1.283）。
- **流式轮耗时语义（重要）**：ppo_sec = 各 wave 纯更新时间之和（与 rollout 重叠）；
  rollout_sec = collector 墙钟（含被藏进去的 PPO）。it14 因中途修 bug 重启被劈成
  两半：95 局 resume 秒回不参与训练，仅 45 新结算局训练（188 grad steps vs 串行
  ~572），故 ppo=609.8s 远小于串行 1670s——是语料少了不是变快了；干净轮预期
  ppo_sec ~20m 但完全藏在 rollout 内，墙钟 ~12-15min vs 串行 35min。
  **观察项**：轮内后期 wave 的 kl 天然偏大（数据由数个 wave 之前的 θ 采集，
  it14 末波 kl=0.0514 vs 串行 0.02），cum_kl 封顶 0.12 自动停训，继续观察。
- **it15 巨浪事故与墙钟地板（16:40 修复）**：`_drain` 无上限，结算高峰后积压
  90 局被一口吞下（单波 376 步算 20 分钟）；且 rollout_sec 在收尾训练后才计时，
  把训练尾巴记进采集列（报 30.9m，真实采集仅 ~9min）。修复：wave_cap=max(24, 2×
  wave_games) 封顶 + collector 结束后持续分批清空 + rollout_sec 改为纯采集窗口。
  **关键认知：ppo_cpu/wall≈96%——流式的墙钟地板是 PPO 纯算力**（epochs4/mb1024
  ≈29min），藏只能藏采集的 9 分钟。要破地板需砍 PPO 计算量：mb 2048（步数减半，
  预期 ppo_cpu≈15min）为下一候选实验。it15 本体完全健康：140/140 missing=0、
  ent 1.268、cum_kl 0.068<0.12、winRate 12.1%（较 it14 回升）、新增第 5 节点
  android-98 正常入列。
- **饿死保护（边缘场景：远端集体掉线）**：①启动时全离线 → 既有回退路径
    （run_rollout 纯本机满额）天然安全；②轮中集体掉线（ping 过了之后死）→
  本机线程按满额孵化、并发闸门初始压低，若 `remoteDeadSecs`（默认 150s）
  无任何远端结算则闸门自动放开到满 workers 并打日志——语料供应恢复，
  流式更新继续吃本地波；③30min queueWindow 兜底：到点强制收轮，
  missing 局回队语义不变，PPO 用已有语料照常更新。非流式模式行为零变化
  （cap 恒等于满额，闸门恒开）。

---

### 6.5 rotate 抽签失配事故：it5 整轮重跑（2026-08-24 晚）

- **症状**：09:35 重启后 `resume: 140/140 pairs already done — run 140 remaining`
  ——140 个已完成对却全部未被剔除，整轮 rollout 重跑，it5 语料 280 局。
- **根因**：`build_pairs` 从单一连续流按调用顺序抽签。旧进程 it5 = 流的第 3 次抽取；
  新进程重启后流复位，it5 复用第 1 次抽取（= 旧 it3 的签）→ 与已落盘 shard 完全
  不相交 → `p not in done` 全员命中。铁证：新旧日志的抽签范围逐字节相同
  （7050345..1073087402），而那是旧 **it3** 的签。§243 的"rotateSeed 继承"只保证了
  种子本身连续，没保证流的消费位置与迭代号对齐。
- **修复**：`build_pairs(args, it, rotate_seed)` 改为 **(rotateSeed, it) 的纯函数**：
  permutation 按 epoch 键控（同 epoch 窗口仍平铺公共排列）、seeds 按 it 键控
  （`default_rng([rotate_seed, tag, key])`）。同一 it 任意时刻重放逐字节一致，
  断点续跑剔除真正生效。无状态冒烟验证：交错调用下 it5 直接求值 == 重放值。
- **处置**：丢弃错配的 it5 语料（280 局，含两套不相交签）→ 带修复重启 →
  it5 干净跑满 140 局（winRate 10%，missing=0/retried=0）→ PPO 146 chunks×4 正常。
- **教训**：①"继承种子"≠"可复现课程"——随机消费必须键控到迭代号而非调用序；
  ②对同一文件的并行编辑会相互覆盖（本次 run_rl.py 两处编辑丢过一次，串行重做）；
  ③新节点接入首日隧道偶发 10054 属预期，回队机制兜住（本轮 missing=0）。

---

## §5 队列模式静默跳轮事故复盘 + 修复 + 重启（2026-08-24 凌晨）

> 决策（DECISIONS §244）。事故窗口 00:19–00:44：it1 PPO 完成后，it2/it3 整轮 rollout
> 完成却从未进 PPO，直接跳下轮（用户发现时已在跑 it4）。

### 5.1 根因（证据链定罪，非猜测）
- `resumed_manifests` 不排除本轮已采局，且把本轮 shard manifest（**单局 schema**，
  无 `games`/`totalSamples` 键）原样并入 `combine_reports` → `r["games"]` KeyError
  秒崩 → 主循环 except 吞掉 + `it+=1`。
- 时间线铁证（dist-agent-meta.jsonl）：it3 末局 00:34:48 → it4 首局 00:35:35，
  **间隔 47 秒**（含 sleep 30s）——PPO 根本来不及启动；it2/it3/it4 均无 ppo_ckpt。
- it1 未崩纯属侥幸：438 个前序局全 done → 早返回空报告路径（绕过合并），代价是
  it1 事件 winRate/samples/outcomes 全空（指标盲区另一症状）。
- detach 启动无 stdout 落盘 → 失败栈零痕迹，只能靠数据考古。

### 5.2 修复（run_rl.py ×3 + 启动器 ×1 + 巡检工具 ×1）
1. `resumed_manifests(..., exclude=seen)`：排除本轮已采；双 schema 归一（本地单局式
   转换 / 远端聚合式透传）。真实事故数据验证：修复前 KeyError，修复后 games=140、
   outcomes 全归类（111 bd + 20 le + **7 stage_clear** + 2 timeout）。
2. 全 done 早返回改磁盘聚合出完整报告（消灭空报告盲区）。
3. except 分支：`iter_error` jsonl 事件 + `it -= 1` 原地重试同轮（§243 断点保证
   不重跑已完局）；consec_fail≥5 才退出。
4. start-training.ps1 detach 分支 stdout/stderr → `tmp/run_rl-<stamp>.{out,err}.log`
   （编辑后 BOM 被剥，已手工补回——§2.2 同坑三踩，教训再次确认）。
5. rl-hourly-inspect.ts：null score_mean 渲染崩溃修复（it1 空聚合事件触发；
   这也是巡检 HTML 从未产出的原因）。

### 5.3 运维动作（按用户指令逐项）
- 权重备份：`nn-training/weights/rl-weights.20260824-001921_post-it1ppo.json`
  （= it1 PPO 后权重，SHA256 校验一致；命名避开 BC `weights.*` 自动发现模式）。
- 巡检账本清零重计（用户指令）：删旧 state 后重跑，it1 新基线 = 98 局 / 6 胜 /
  812 击杀；HTML 报告 `tmp/rl-traj/inspection-report.html` 首次成功产出。
- 语料抢救：it2~it5 同权重有效语料全并入 it2（140+140+140+18=**438 局**，
  目录整体 rename 为 `it2/merged_itN` 零拷贝），源目录随之消失。
- 05:29 经启动器 detach 重启（参数同前 + keep-iters 5）：resume 自 post-it1 权重 ✓、
  rotateSeed 继承 ✓、`438/140 pairs already done — run 140 remaining` ✓（macos 节点
  8 并发补采中；本机 self agent 未起被排除，吞吐减半不影响正确性）。

### 5.4 教训
1. **异构数据管道必须 schema 归一后再进聚合器**——两条采样路径（本地/远端）的
   manifest 结构差异在单机时代不存在，分布式化第一天就炸。
2. **吞异常的循环必须有旁路观测**（iter_error 落盘），否则生产事故只剩行为考古。
3. 失败迭代前跳 = 静默丢语料；断点续跑机制（§243）使原地重试成为零成本安全选择。
4. .ps1 编辑三连坑：BOM 必查（第三次踩）。

---

## §4 RL 训练断点续跑机制（2026-08-23）

> 决策（DECISIONS §243）：三层断点续跑，崩溃/停启后自动继续而非重跑。

### 4.1 实现
- **it 续跑**：`--start-it`（缺省自动 = jsonl 最后完成迭代 + 1）。
- **rollout 任务续跑**：`completed_pairs(traj_dir, wver)` 剔除已完整落盘且 wver 匹配的局；
  `resumed_manifests` 并入聚合保报告完整。
- **PPO epoch 续跑**：`ppo_update(..., ckpt_path=it{n}/ppo_ckpt)` 每 epoch 存 model/opt/epochs_done/numpy RNG。

### 4.2 验证
- **PPO 续跑等价**（`tmp/dist-resume-check.py`）：从 checkpoint 续跑 vs 一次跑完，**最终权重逐参数相等**
  ——证明 minibatch 乱序（numpy MT19937）+ optimizer 状态精确重建。首跑失败根因是测试脚本 mA/mB
  随机初始化不同（nn init 走 torch RNG 不受 numpy seed 控），非机制缺陷；同初始后全等。
- **it 断点端到端**：启动1 完成 it1；启动2 日志 `resume: continuing from iteration 2` 直跑 it2
  （不重跑 it1），`it2/ppo_ckpt` 三件套落盘。
- rollout 断点 pure 函数：无 stage/seed 不计入 done，有则计入（dist-resume-check 覆盖）。

### 4.3 教训
- `ppo_update` 的 minibatch 乱序是**唯一**的全局 numpy RNG 消耗点（build_pairs 用独立
  `default_rng`、agents 用 `random.Random`），这是精确续跑的前提，勿在别处引入全局
  `np.random` 消耗破坏确定性。
- 崩溃在「权重写回后、jsonl 写前」的极小窗口会用新权重重跑整轮——on-policy 正确，接受。

---

## §3 RL 阶段设计（承接 P1.5 蒸馏，2026-08-21）

> 决策：DAgger 对 RL 的价值已基本兑现（验证推理链路 + warm-start + 观测充分性证明），
> 继续压 DAgger 边际收益低。下一步主线 = **收尾 round2（锁定最佳 warm-start）→ 转 RL**。
> 用户已拍板走「推荐」路线。

### 3.1 观测审计结论（关键，免扩展）
重读 `src/nn/obs-encoder.ts`（14 通道 + 24 标量）。**obs 已自带基地防御 + 胜利进度信号，RL 无需扩展通道**：
- 空间：ch5=基地(eagle+护墙)、ch7–10=敌种+智能层、ch11=子弹(含方向)、ch13=waveHeat(未来 600 tick 预测刷怪)。
- 标量：s[1]=minBaseDeadline/600（**敌人对基地受损时限**，显式基地威胁计时）、s[6]=护墙完整度、
  s[22–23]=最近基地相对位/向、s[13]=剩余敌人比例（进度代理）、s[0]=最近击杀 slack。
→ **残余 0% 属目标优化缺口（BC 不优化胜率），非观测缺口**。RL 优化真实 reward 即可直接吃现有 obs，不被卡。

### 3.2 架构复用（最小改动）
- 主干：`StudentModel` 的 stem + 8×ConvMixer blocks + fc 作共享特征提取器，**从 DAgger 检查点 warm-start**
  （写死 `tmp/student-weights-dagger2/weights.json`）。
- 策略头：保留 3 个 factored categorical 头（move5/fire2/item3），与 BC 一致 → 动作=(move,fire,item)，
  决策 tick K=10 持有门控不变。
- 价值头：新增 `value_head`（fc 特征→标量），随机初始化、RL 训。PPO 用 shared-trunk + 分离 policy/value。
- 动作空间：离散 factored，PPO categorical 直接适用。首轮不扩 item 头（rewind/emp/decoy/mine 暂不纳入 RL 动作）。

### 3.3 奖励设计（权重待拍板）
r = w_win·win + w_kill·kills + w_base·baseIntegrityΔ + w_surv·survivalTicks − w_dead·baseDestroyed
- win：通关 +1（稀疏终局）；baseIntegrityΔ：每 tick 基地护墙/鹰完整度变化（塑形，引向防御）；
  kills：击杀数（复用 world 击杀计数）；baseDestroyed：终局大惩罚（引向保基地）；
  survivalTicks：弱塑形，防过早送。需小范围网格/经验初值，避免 reward hacking（只保基地不进攻）。

### 3.4 Rollout 回路（TS 导出 + Python 训练，沿用范式）
- TS 端 `tools/sim/export-rl-rollout.ts`：复用 `ObsEncoder`+`StudentModel`(+value head)+headless sim 驱动
  （骨架取 `export-dagger-labels.ts`），按决策 tick 跑策略→采 (obs,scalars,action,logprob,value,reward,done)
  写 trajectory shards（npy，兼容 `load_dataset` 递归扫描）。
- Python 端 `nn-training/ppo.py`：消费 shards，clipped PPO（shared trunk warm-start + value head），
  复用 `train_bc.py` 的 venv/torch 入口（`start-training.ps1`）。
- 在线性：RL 多轮 rollout→训练→再 rollout（on-policy）。首轮用离线固定轨迹 mini-batch PPO 起步验证链路，
  再上异步 on-policy。

### 3.5 任务拆分
观测审计(#12, ✅) / 奖励设计(#11) / round2 收尾→锁定 warm-start(#13) / TS rollout(#15) / PPO(#14) / 首轮量化(#16)。
#13 先跑完锁定最佳 warm-start，再 #11→#15→#14→#16 串行。

### 3.6 接手验证：RL 链路端到端冒烟全通（2026-08-21 接手）

前手交付 `ppo.py` / `export-rl-rollout.ts` / `run_rl.sh` + value head（`student_model.py`
PPOStudent、`infer.ts` 可选 value_head 槽）。接手后逐契约复核（World 字段 /
computeMasks 1=valid / writeNpy 签名 / rules 模块 / buildModelFromJson 全参数透传 /
PPOStudent 继承 arch()→kind:'student' 路由正确）并跑通完整闭环：

```
init   : ppo.py --init-from tmp/student-weights-dagger → value_head 随机初始化，
         missing=[value_head.*] 符合预期；68683 params；JSON 写入 [1,128]+[1] ✅
rollout: export-rl-rollout.ts s0/seed0/600ticks → 59 样本（60 决策点 − 超时丢弃
         末 pending，设计内）；12 个 npy + manifest ✅
update : ppo.py --resume --data --epochs 2 → policy/value/entropy/kl 正常输出 ✅
roundtrip: 用更新后权重再 rollout → TS 加载含训练后 value head 的权重 ✅
门     : tsc --noEmit 绿；bun run check 全绿（1429 pass / 0 fail）
```

**接手修复的 2 个 PPO bug**（冒烟暴露，前手未跑到 update 步）：
1. `ppo_update()` 签名/实现错位：函数体按 dict 取 `batches["obs"]`，调用方传的是
   episode **list** → TypeError。改为按 list 迭代。
2. 维度 bug：episode 张量已是 `(T,14,26,26)`，函数内再 `unsqueeze(0)` 成 5-D 崩溃。
   重构为 **chunk_episodes(mb=256) 固定 minibatch**：GAE 先按局算好，chunk 只作更新
   粒度——同时解决整局 1200 样本单次前向的激活内存问题，并增加梯度步数。
另：补 `--seed`（默认 7，minibatch 洗牌可复现）；移除未使用的 old_val。

**遗留提示**：(a) run_rl.sh 无 `set -e`，单轮失败会静默续跑，正式训练前建议加；
(b) 超时局的末个 pending 被丢弃且 done=0 → GAE 对末步 bootstrap V=0，轻微偏差可接受；
(c) 奖励权重初值（W_WIN=5/W_KILL=0.2/W_BASE=1.0/W_SURV=0.01）按 §3.3 待首轮 reward
曲线监控后定稿。

### 3.13 R3 实施：奖励与 godai-score v7 全面对齐 + 维度遥测（2026-08-21）

**动机（击杀趋势诊断，`tmp/kills_trend.py`）**：旧奖励下击杀数 11 轮无增长
（2.4–3.9 波动；标量法与奖励分解法交叉验证一致）。根因：存活流收益
(W_SURV=0.01×~340 步 ≈ 3.4/局) ≫ 击杀收益 (W_KILL=0.2×3 杀 = 0.6/局)，
策略理性选择苟活。候选 R1/R2/R3/C1 中用户拍板 R3。

**R3 设计**（export-rl-rollout.ts 整体重写）：
- 势函数 Φ(s) = SCALE(10) × lossBandMax(0.4) × Q_partial(s)；Q_partial 按
  DEFAULT_LOSS_WEIGHTS 复刻 weightedQuality 的 null-剔除重分配。
- 支付：决策窗势差 r_t = Φ(t)−Φ(t−1)，终局对账 `extra = SCALE×score − paidTotal`
  ⇒ **每局 Σr ≡ SCALE×score 精确恒等**（胜局经带切换自然放大 0.70 vs 0.40）。
- 苟活零收益（v7 无 survival 维）——结构性修复 reward hacking。
- Telemetry 逐字段对齐 simulation-runner（SAMPLE_TICKS=6/RADIUS=12/RING 8 格/
  事件计数/powerup census same-tick 对账），dims{value,raw} 全量进 manifest 与
  _rl_report → run_rl 聚合 → training_log.jsonl（score_mean/std + dim_means）。

**实现坑**（均已修）：块注释内 `a_*/lp_*` 提前闭合注释；首决策点 pending=null
时 paidTotal 已入账但 flushPending 空转 → 恒等式差 Φ_0（冒烟抓到，−1.80 精确
吻合）；timeout 局 done=0 样本丢失改为统一终局 flush。

**验证**：timeout 6 局 + base_destroyed 6 局恒等式 max|diff|≈1e-7；正式训练
it8 全部 50 shards 恒等成立（2.2e-07）。分数随行为合理分化（10 杀局 0.194 vs
速死局 0.117）。

**R3 首轮观察（it1–it8）**：行为画像剧变——局均 ticks 7000+→3300–4100、timeout
近消失、出门交战捡道具；但 score 平坦 0.10–0.12、击杀未涨。KL 0.036–0.064 偏高
但未触警；entropy 震荡无坍缩。判定：激励结构生效、梯度尚未爬上——待 14h 长跑。

**长跑基建（run_rl.py）**：`--iters 0` 无限 + `--max-hours` 墙钟预算；
`--keep-iters 3` 轨迹磁盘上限（~170MB/iter，不清盘 14h 写满磁盘）；连续失败
5 次重试（30s 退隔）防瞬时故障中止。rotate 模式改**随机分批**（每 epoch 全
35 关随机置换切 7 批）——修固定窗口在续训迭代号归零导致的 stages 0-4 过采样；
再升级为 **35 关×2 种子=70 局/iter**（中断免疫 + 每轮全分布指标，代价迭代
8.4→13–16min）。rotate 种子掺启动时刻防课程重放（记入 run_start.rotateSeed）。
顺手修 wins 计数从未自增的潜伏 bug（历史 winRate 恒 0 掩盖）。

### 3.14 R3 长跑失败复盘：秒投降坍缩 + F3/F3b/F4 修复（2026-08-22）

**失败事实（training_log.jsonl it1–101 全量核验）**：R3 无限长跑在 it41 后 score
钉死 0.1211（精确 = 0.4×(0.256×1.0+0.044×1.0)/0.991，分毫不差），策略坍缩为
**开局弃战退化解**——mobility≤0.04 / progress=0 / lives≈1.00，敌人 ~211 tick
拆基地速终（it100 manifest 单局 ticks=211）。0.121 > 认真打仗实测 ~0.110，
PPO 收敛到投降是理性的。it100 KL=0.96 爆表更新。

**根因（Goodhart 倒挂，三机理）**：
1. **指标冻结**：局越短采样越少，basePressure 只采到开局敌人尚远的样本，
   baseSafety 均值停在 ~0.90–1.0；lives 零损耗。
2. **权重比倒挂**：progress 权重 0.477 但击杀难赚；lives 0.256 躲着就满值。
   低击杀率下交战净亏（阵亡扣的 lives > 击杀赚的 progress）。
3. **鸡生蛋护城河**：打仗有利需先有击杀技术；练击杀必先接受亏损。
   PPO 选不交学费路径——连枪都不开（accuracy=0），比旧苟活更彻底。

**监控失效教训**：KL_WARN 从 it37 持续告警但无动作，夜间无人值守空转 ~60 轮。
→ **告警必须自带牙齿**（F4）。

**F3 基地失守门控**（export-rl-rollout.ts）：`BASE_LOSS_MULT=0.25`，
base_destroyed 局终局锚点 `SCALE×score×M`。**关键设计：M 必须双点落地**——
只改 Φ 不改终局锚点时，对账项 `extra = SCALE×score − paidTotal` 会精确抵消
门控差值（Ng et al. 势塑形不变性：Φ-only 改变重分配时机但不改总回报）；
故 M 同时进 `countersPhi()`（base 拆后所有窗 Φ×0.25，窗口级信用分配语义
一致）与终局锚点（真正翻转总回报排序）。

**F3b 败局剔除 lives 维度**：`RL_LOSS_WEIGHTS = {...DEFAULT_LOSS_WEIGHTS,
lives: 0}`。败局里 lives>0 只出现在 base_destroyed 局（lives_exhausted 局
lives=0），它支付的正是「基地死时自己没死」的投降画像且与交战负相关——
坍缩主要收入源（0.256/0.991）。其余维度审计结论：**保留全部**——
progress/tempo/openingTempo/loot 是努力型指标（多打多得）；baseIntegrity 对
lives_exhausted 局有区分度；baseSafety 的冻结伪影被 F3 门控中和（残余投降
分 ≈0.4×(0.044/0.735)×0.25 ≈ 0.006）；**胜局带不动**（全维度以「赢」为前提，
无被动通路）。godai-score.ts 评估口径不动（God-AI 基线可比性），RL 专用
RL_SCORE_CONFIG 分流。

**F4 KL/熵双判据熔断**（run_rl.py）：KL_BREAK=0.15 连续 3 轮（暴力漂移）或
ENT_BREAK=0.60 连续 8 轮且 winRate<0.5（退化确定性；纯 KL 判据抓不住本次
失败——it65/73/79 尖峰均为单轮，从不连续；熵地板按日志本应 it63 即触发）。
触发后写 circuit_break jsonl 事件 + exit code 3。实现坑：主循环 except 会吞
SystemExit 重试，故用标志位+break 而非 raise；winRate<0.5 守卫防误杀合法收敛。

**冒烟验证**（坍缩权重 s0/seed0，复现秒投降 231 ticks）：gated score
0.1211 → **0.005986**（quality=0.044/0.735×0.4，loot 为 null 一并出分母），
≪ 打仗实测 0.110，倒挂彻底翻转；恒等式 Σr ≡ SCALE×gatedScore 成立
（|diff|=1.75e-9，float32 精度口径）；manifest 新增 rewardScheme=
'v7-aligned-f3' / scoreUngated / quality 三值自洽。tsc + py_compile 通过。

**重启指引**：归档坍缩权重后删除 tmp/rl-weights/weights.json（build_model
自动回退 BC warm-start 全新初始化——坍缩权重 entropy 已塌至 0.36–0.68，
resume 会滑回同一盆地）。经启动器跑：`nn-training/start-training.ps1 -Script run_rl.py ...`。

### 3.15 R4 无限长跑启动 + detach 后台化修复（2026-08-22）

- 坍缩权重已删，旧轨迹归档 `tmp/rl-traj-r3-collapsed`（取证保留）。
- **启动器缺口修复**（start-training.ps1）：detach 分支原硬编码只认
  `train_loop.py`，run_rl.py 落入前台执行（绑终端会话，会话结束即死）。
  放宽为 `-in @('train_loop.py','run_rl.py')`。坑：编辑工具写回剥掉 UTF-8
  BOM → PS 5.1 GBK 误读中文注释 ParserError（§2.2 同坑二踩），手工补回
  EF BB BF。**教训：凡编辑 .ps1 必查 BOM**。
- **run_rl.py cwd 锚定**：detach 的 WorkingDirectory 是 nn-training/，而
  run_rl.py 全部默认路径是 repo 根相对（tmp/...）→ 后台首启即死于读不到
  BC 权重（前台复现过一次）。修法同 train_loop.py 的 REPO_ROOT 模式：
  main() 开头 `os.chdir(dirname(dirname(abspath(__file__))))`。
- R4 启动确认：07:45 run_start（iters=0 无限、max_hours=0、rotate 35×2
  种子、lr 3e-4、epochs 3、BC warm-start 全新初始化），it1 shards 正常产出。
- **每小时自动巡检上线**（TRAE 定时任务）：进程存活 + training_log 健康度
  判读（entropy 地板 / KL 连续超限 / ticks 骤降=秒投降复发特征）+ 异常时
  直接修复并经启动器重启，修复记入本文件。

### 3.16 R4 首次健康度巡检：进程环境性死亡，续训重启（2026-08-22）

- **发现**：12:01 巡检时无 python 进程；日志尾部为 it18（11:32:43 完成），
  `tmp/rl-traj/it19` 半成品 rollout 最后写入 11:35:02 → 进程死于 it19
  rollout 中途。**无 circuit_break**（F4 未触发）、无崩溃栈。
- **根因**：环境性死亡——系统事件日志无重启（uptime 3.2 天）无 OOM，
  推断为现代待机/外部干预杀进程；非代码缺陷，无需改训练代码。
- **18 轮健康度（it1–18，hard，70 局/轮）全部健康**：
  winRate 0%（BC warm-start 起步 4h，base_destroyed 为主败因）；
  score_mean 0.020→0.051 平稳波动（F3 门控压低败局分属预期）；
  entropy 1.14→1.31 缓升（≫0.8 地板）；KL 0.035–0.051（稳态区间
  0.045–0.054 内）；局均 ticks 3105–3891（秒投降 <1000 特征未复发）；
  progress 0.09→0.17 缓爬、mobility 0.46–0.61 远离 0。无新 hack 模式，
  不触发奖励公式修改。
- **修复**：清残留 python → 启动器 detach 重启（同 R4 参数）。权重安全：
  `tmp/rl-weights/weights.json` 停于 it18（11:32:43），run_rl.py
  `build_model()` 自动 resume（run_start 在 build_model 之后写盘，
  12:06:48 run_start = resume 成功）；残缺 it19 目录由每轮开头
  `shutil.rmtree` 自清理，无 off-policy 样本污染。
- **验证**：12:07 python 双 PID 存活 + 新 run_start 参数与原配置逐项一致。
- **待观察**：winRate 连续 18 轮为 0 属 RL 早期正常（progress 在涨），
  若 ~40 轮后仍全零且 progress 停滞，再议奖励倒挂排查（届时报人工确认）。

### 3.17 keep-iters 3→5 无损切换 + 巡检：可胜关卡 6→16（2026-08-22）

- **巡检（19:00，it25–30 健康）**：winRate 1.4%–14.3% 波动、score_mean 0.087–0.183
  随胜率联动（F3 门控正常）；entropy 1.19–1.22、KL 0.022–0.028、局均 ticks
  4000–4300、progress 0.359→0.385 缓爬、mobility ~0.74。无 circuit_break。
- **数据缺口**：it27 原始报告被 `--keep-iters 3` 滚动删除，巡检增量扫描漏扫
  （每关少记 2 局）。胜局记录依赖巡检 state 累计，原始目录保留窗口太短是
  结构性风险 → 用户拍板 keep-iters 3→5（磁盘 ~170MB/iter，5 轮 ~850MB 可接受）。
- **切换时机（无损）**：it31 的 70/70 rollout 报告已完整落盘、PPO 更新进行中
  → 先把 it31 扫入 state（7 胜，含新破关 11/堡垒、20/棱堡；同心圆、终极堡垒
  打破连续 0 胜）→ Stop-Process 停机（权重停于 it30，it31 in-flight 更新作废、
  新 run 首轮重做）→ 启动器 detach 重启，参数同前仅 keep-iters=5。
- **验证**：19:20:42 新 run_start `keep_iters: 5` ✓；python 双 PID 存活 ✓；
  it1 rollout 开跑 ✓。迭代编号重置为 it1，巡检 state 保留累计
  （420 局 26 胜、可胜关卡 16 个）并注明「新 run_start 不重置累计」；
  旧 run 孤儿 it29–it32 目录留存盘上（已扫描/作废，新 run 到同编号时自清理）。
- **教训**：`--keep-iters` 是启动期参数，改值必须重启；重启前先确认当前轮
  rollout 报告是否已完整（70/70），完整则先扫后杀，零数据损失。

### 3.12 决策：不做 rollout/PPO 流水线，瓶颈转向 PPO 自身（2026-08-21）

用户问「下一轮 rollout 能否与上一轮 PPO 并行」。实测相位（it7–it9）：rollout
~2.5min / PPO ~5.8min（**PPO 占 70%，瓶颈已换位**）。

**结论：严格 on-policy 下不能。** 依赖链 `rollout(i+1) ← W_i ← PPO(i)` 是算法语义：
数据必须来自被优化策略本身，提前采集 = off-policy 数据，importance ratio 系统性偏移。
推演过的变体：
- **A. 有界陈旧性**：允许落后一个更新的权重（KL≈0.05 下近似成立），省 ~30%，
  但训练语义改变、与严格基线不可比 → **否决（本轮）**
- **G. 分波流水线**：权重整窗冻结、两波游戏交错 SGD，on-policy 性质保留，
  但收益仅 ~15%（相位不等长限制重叠窗口）→ 复杂度/收益不成比例，**否决**
- **H. epochs 3→2**：PPO 内部直接砍 ~2min/轮（KL 0.045 说明更新有富余）→
  **留作下轮规模决策时的首选提速杠杆**，当前验证跑不动

另清理 `tmp/rl-traj/it10–14` 陈旧目录（旧运行残留；判定用 `find -newermt`，
字符串比较 `%T+` 的 `+` 字符会破坏排序——第一版检查因此全误判 LIVE）。

---

### 3.11 评审处置：rl-mimo.review.md 确认项全部落地（2026-08-21）

外部评审（`rl-mimo.review.md`）逐条核实后处置。**采纳并落地**：
- **Q1 死代码**：删除 `rl_env.py`（np.random mock 桩）/ `rl_ppo.py`（未接线的 PPO 类）/
  `train_rl.py`（无入口调用）；`rl_model.py` 保留加 STATUS 注释（P1 教师模型参考）；
  `eval_bridge.py` 经核实**非死代码**（BC 管线工具，AGENTS/README/启动器引用），保留。
- **Q4 监控**：`run_rl.py` 每轮追加 `training_log.jsonl`（run_start 元行 + per-iter
  winRate/outcomes/samples/policy/value/entropy/kl/lr/mb/epochs）+ KL 预警（阈值 0.08，
  按实测稳态 0.045–0.054 校准；评审建议的 0.02 会永久误报）+ entropy 单轮骤降 >0.1 预警。
- **时间戳**：run_rl.py / ppo.py 全部日志行加 `[HH:MM:SS]` 前缀（此前无法事后分析节奏）。
- **计划偏差回填**：`plan/RL-Bun-Bridge.md` 顶部加状态横幅指向实际 npy shard 架构。

**证伪的评审主张**（留档防复发）：Q5 参数数字全过期且 epochs=8 与 KL 实测矛盾；
Q6 break 路径 flush 已存在；Q8 torch/bun「竞争」不存在（两阶段严格串行）；
3.1 语法错误不存在（py_compile 通过）；3.4 rotate_rng 实为可复现。

**顺延项**：(a) Q2 的 `export-rl-rollout.ts` 权重注释——活体训练每局重新 spawn 该文件，
本轮跑完前禁改；(b) Q3/S4 reward 归一化、Q7 value warmup、S2 v7 对齐——等首轮完整曲线，
当前 advantage 归一化已兜底、曲线健康；(c) S1 复用 train_loop 日志框架——P2。

---

### 3.10 PPO 提速排查：安全杠杆无效，mb 翻倍是正解（2026-08-21）

用户要求 PPO 提速。独立进程基准（90→16 chunks，4 配置 × 2 轮）结论：
- **线程数（12 vs 8）与 flush_denormal 在稳态下零差异**（四配置全部 ~27s/epoch）。
  第一轮测得的巨大差异（28–68s）是杀训练后系统抖动噪声——单次采样基准不可信，
  必须稳态复测。
- 已落地的无害改动：chunk 张量转换移出 epoch 循环（省 2× 冗余转换）、
  `--threads` 参数（默认 8 对齐物理核）、flush_denormal best-effort。
- **真正杠杆：mb 256→512**。梯度步 270→135/轮 → PPO ~5.5min→~3min，一轮
  ~8.5–9min→**~6.5min**，15 轮 ETA 从 ~2.2h 降到 **~1.5h**。动态影响：更少更大
  的更新 = 每轮 KL 漂移更小，与 §3.7 的 KL 偏高顾虑同向，属双赢。

训练已用新配置重启（自 it4 权重 resume，轮番方案不变）。

---

### 3.9 正式训练启动 + rollout 并行化（2026-08-21）

**并行改造两步**：(1) 初版按种子分区 `seeds[k::W]`——seeds=4 时 worker 被钳到 4，
8 物理核只吃满一半；(2) 改为 **按 (stage,seed) 游戏对粒度调度**：ThreadPoolExecutor
把 16 局摊进 W 个并发槽，每局一个 bun 进程（单局启动 ~300ms 可忽略），shard 落
`w{i}/` 子目录、`discover_rl_shards` 递归消费无需改动。实测 16 局 rollout 从串行
~5min 降到 **~1.5–2.5min**（尾效应限制：快局先退、长局收尾）。

**正式训练**（`--epochs 3 --workers 12`，自 BC 全新初始化，权重每轮原子落盘）：
KL 稳定在 0.047–0.054（EPOCHS=3 生效，对比试跑 EPOCHS=4 的 0.094）；entropy
1.298→1.213 缓降（策略开始收敛）；policy loss 0.373→0.096；outcome 出现
lives_exhausted 占比上升（2→4/16 局）的早期存活信号；win rate 仍 0%（hard 冷启动
预期内）。节奏 ~3.5–4 min/轮，15 轮预计 ~1h 内完成。

---

### 3.8 启动脚本收敛：run_rl.sh 下沉为 run_rl.py（2026-08-21）

用户要求减少启动脚本。**不把循环塞进 sh+ps1 各一份**（双实现漂移），而是把
on-policy 循环下沉为 Python：重写 `run_rl.py`（原为旧脚手架 train_rl.py 的包装）
为主循环——bun rollout 走 subprocess、PPO 更新进程内复用 `ppo.py` 的
`load_episodes/chunk_episodes/ppo_update`（模型常驻内存，省去每轮 torch 重启），
权重每轮原子写回（`save_weights_json` 改 temp+os.replace，长跑崩溃不留半截文件）。
删除 `run_rl.sh` 与旧启动脚本 `start_rl.bat`；启动器收敛为规范一对：

```
bash nn-training/start-training.sh --script run_rl.py --iters 15 [--epochs 3 ...]
powershell ... start-training.ps1 -Script run_rl.py --iters 15   # 参数透传
```

新路径小规模验证 EXIT=0（且幂等 resume 生效：从上轮 RL 权重续跑而非重初始化）。
`ppo.py` 单步 CLI（--init-from/--resume）保留不变。
**待清理候选**（旧脚手架，已被新管线取代，删除前待拍板）：`train_rl.py`、
`rl_env.py`、`rl_ppo.py`、`rl_model.py`。

---

### 3.7 小规模试跑 it1：链路全通（2026-08-21）

`ITERS=1 STAGES=0-1 SEEDS=0-1 bash nn-training/run_rl.sh`（hard，MAXT=12000，EPOCHS=4）：

```
init    : BC warm-start + 随机 value head，68683 params ✅
rollout : 4 局 / 1152 样本 / 11498 ticks；outcome 全部 base_destroyed
          （s0sd0 4405t / s0sd1 1284t / s1sd0 2651t / s1sd1 3158t）
          —— hard 冷启动 0% 胜率符合 §3.5 预期 ✅
PPO     : epochs=4 policy=0.5790 value=0.7676 entropy=1.2888 kl=0.09391
          mean_ret=-0.195 ✅
权重验证: value_head.* 新增；40/42 策略张量发生变化（非原样写回）✅
```

**试跑暴露并修复**：run_rl.sh 未 `mkdir -p $TRAJ` → 日志重定向失败导致 rollout/PPO
整步被静默跳过（正是 §3.6-(a) 预警的形态）。已修：`set -eu` + 循环前建目录。

**正式跑前的调参读数**：
- **kl=0.094 偏高**（经验安全区 ~0.01–0.05）：EPOCHS=4 × mb=256 下单轮策略偏移较大，
  有侵蚀 BC warm-start 的风险。建议正式跑降为 EPOCHS=3 或 LR=2e-4，并在 ppo.py 加
  KL 早停（approx_kl 连续超阈则 break epoch 循环）——未实施，待拍板。
- value=0.77 属正常（价值头从零学起，需数轮收敛）。
- entropy=1.29 > 冒烟期 1.06：随机化探索正常，观察后续迭代是否按预期下降。

---

## §2.2 启动器 Windows 本机验证：ps1 双 bug 修复（BOM + 参数风格兼容，2026-08-21）

用 AGENTS.md 规定的幂等自检验证本机 torch 可用性：

- bash 版 `bash nn-training/start-training.sh --check` ✅ 直接通过，
  torch **2.7.1+cpu** @ `.venv/Scripts/python.exe`（OMP threads=12）。
- PowerShell 版 `powershell ... start-training.ps1 -Check` ❌ 解析失败，两个独立 bug：

1. **UTF-8 无 BOM → PS 5.1 按 GBK 解码**。本机无 pwsh 7，Windows PowerShell 5.1
   对无 BOM 的 .ps1 一律按系统 ANSI 代码页（中文系统=cp936）解码，UTF-8 中文注释
   字节被误读后破坏字符串引号配对，整个文件 ParserError。
   **修复**：文件头加 UTF-8 BOM（`EF BB BF`，微软官方推荐做法，对 pwsh 无副作用）。
2. **switch 只匹配 `'--check'`，`-Check` 落入透传**。`powershell -File` 调用时参数
   按字面量进入 `$args`（不做原生参数绑定），`-Check` 无法命中 POSIX 风格分支，
   被当未知参数转发给 train_loop.py → `unrecognized arguments: -Check`。
   **修复**：CLI 解析改 `switch -Regex` + `'^--?name$'`，同时兼容 `-Check` 与
   `--check`（PS switch 默认不区分大小写）；训练脚本参数均为 `--xxx` 长选项，
   不会被误吞。

**验证**：`-Check` / `--check` / `-Echo -Script train_bc.py --arch student` 全部
exit=0 且正确消费/透传参数；`bun run check` 全绿（1429 pass / 0 fail）。

**教训**：(a) 含非 ASCII 的 .ps1 必须带 BOM 才能跨 PS 5.1/pwsh 正确解析；
(b) `-File` 调用无参数绑定，脚本内 CLI 解析必须自行兼容两种前缀风格；
(c) Git Bash 终端显示 powershell.exe 的 GBK stdout 会乱码——那是终端显示问题，
以退出码和 ASCII 行（venv/torch version）为准。

**后续（同日）：VBS 启动器移除。** 删除 `launch-training.vbs` / `launch_rl.vbs`，
`--detach` 改为原生 `Start-Process -WindowStyle Hidden`（ShellExecute 派生完全
脱离控制台的进程，等价旧 VBS 行为，且规避 VBScript 被微软弃用的趋势）；bash 版
detach 分支委托给 ps1（detach 行为单一定义，避免两处维护）。顺手修复
`smoke_test.py` 的 arg-proxy 缺属性 bug（缺 `arch`/`notes`/`resume`，
train_bc.train 2026-08-20 前后新增字段未同步），修复后端到端 PASS。
验证：Start-Process 隐藏派生真实执行 smoke_test 并回传退出码；`-Check`/`--echo`
干跑 exit=0；`bun run check` 全绿。

---

## §2.1 移动死锁修复 + 0% 根因收敛（续 §2，2026-08-20）

### 关键修正：policy-input.ts 的 move-freeze 死锁（§2 评估 0% 的真正主因）

§2 报告「学生 BC 12ep → avgKills=0 / avgTicks=3933」与「+DAgger 6ep 仍 0%」。
重新诊断发现一个被忽视的**部署语义死锁**（非权重/精度/方向问题）：

- `policy-input.ts` 原把 move 头 argmax=0（`none`）映射为 `moveDir = null`；
- `SimulationPlayer` 在 `getMoveDirection()` 返回 null 时设 `moving=false`，
  坦克在出生点原地静止。但 `none` 在教师(God-AI)语义里 = **「保持当前航向」**，不是「停」；
- 世界状态冻结 → 模型每 tick 仍见静止状态 → 持续预测 `none` → 永久锁死，
  0 击杀、基地最终被毁（avgTicks≈3933 即 base_destroyed 时间线）。

**修复**：`none` 改为「持有上次指令方向」(`lastDir`)，坦克保持移动、世界状态
活跃。修复后单局 trace 从 `distinctCells=1 / kills=0` 变为 `distinctCells=200 / kills=1`。

### 修复后正式评估（stages 1-5 × seeds 1-10 = 50 局，hard，--policy nn，权重不变）

```
WIN RATE 0.0% (gate 60%) -> FAIL
totalKills=13  avgKills=0.26  avgTicks=3331
SCORE V7 suite=0.0691 lcb=0.0656 meanWinRate=0
```
avgKills 从 **0（冻结）→ 0.26（移动）**，avgTicks 3331 表明坦克现在会移动并
零星开火，但仍在 ~55s 内阵亡。**结论**：冻结死锁已修复（移动恢复），但残余
0% 来自**分布漂移**——学生在「自己的部署状态」上几乎不开火（部署 trace：
ready=true 时 `fireLogits[0] >> [1]`，fire 命中仅 3/477）。这与 §2 的
「BC 分布漂移」根因一致，但本次是更纯粹的**学生自部署漂移**（教师只在教师
状态上标过 fire）。

### 对 §2 DAgger 回合的影响（重要）

§2 的 DAgger 冒烟（9 局 3725 样本）是在 **freeze bug 存在时**采集的——学生
冻结在出生点，所有采集状态都聚集在 spawn 邻域，是一份**退化分布**，故续训
毫无帮助（move acc 0.35→0.386 也只是拟合 spawn 邻域）。修复后学生真正移动、
访问真实状态空间，DAgger 采集才首次有效。

### 本次交付

| 交付 | 文件 | 状态 |
|------|------|------|
| 移动死锁修复 | `src/nn/policy-input.ts`（`lastDir` 持有语义） | ✅ 已落地，评估验证 |
| DAgger 采集器（清理版） | `tools/sim/export-dagger-labels.ts` | ✅ smoke 2 局/330 样本通过 |
| 正式 DAgger 采集 | `tmp/dagger/`（stages 0-4 × seeds 0-9，50 局） | ✅ 完成：19783 样本 / 163124 ticks，50 shards，obs `(N,14,26,26)` 与 godai 同构（godai 368M / dagger 182M） |
| 混合重训 | `start-training.ps1 -Script train_bc.py --data-dir tmp/mix --arch student --resume tmp/student-weights-full/weights.json --out tmp/student-weights-dagger/weights.json --epochs 30` | ✅ 完成（task 8898no，11663.6s≈3.24h，torch 2.7.1+cpu）。30ep: train_loss 1.90→1.1242, val_loss 1.33→1.2064, move_acc 0.449→0.616, fire_acc 0.860→0.887, item 1.000。`tmp/mix`=100 shards/549M，samples=59995。权重：`tmp/student-weights-dagger/weights.json`(active) + `weights.20260821-123437_ep30_val1.2064.json` |
| DAgger 续训后保留率评估 | `m1-eval --policy nn --weights-dir tmp/student-weights-dagger`（st 1-5×sd 1-10, hard） | ✅ 完成（task LXzEAh）：WIN 0%（gate 60% FAIL）；suite 0.0691→0.1027(+49%)，totalKills 13→104(8×)，avgKills 0.26→2.08，Crossfire 冻结→1。DAgger 证实修复射击漂移；残余 0% 为策略深度差距，非 bug |

### 下一步

1. ✅ `tmp/dagger/` 已采集完成（19783 样本，50 shards）；已合并为 `tmp/mix/`（100 shards / 549M）。
   续训已用启动器拉起。`start-training.sh` 在 Windows 上实测把 `/d/...` 双重化成
   `D:\d\...` 导致 exec 秒失败（task 56DGy3），改走 `start-training.ps1`（原生 Windows
   路径，无 MSYS 转换）成功：`powershell ... start-training.ps1 -Script train_bc.py
   --data-dir tmp/mix --arch student --resume tmp/student-weights-full/weights.json
   --out tmp/student-weights-dagger/weights.json --epochs 30`（task 8898no，后台运行中）。
   **`.sh` 路径双重化 bug 已修复**：新增 `to_win_path` + `MSYS_NO_PATHCONV=1`，`--echo`/`-h`
   实测输出干净 `D:\github\...\python.exe -u D:\github\...\train_bc.py`，不再出现 `D:\d\`。
   目标：把 fire 头在学生自部署状态上拉起。
2. ✅ 重训后保留率评估完成（task LXzEAh）：`m1-eval --policy nn --weights-dir
   tmp/student-weights-dagger`，50 局（st 1-5 × sd 1-10, hard）。
   **结果**：WIN 0%（gate 60% FAIL）；但 suite 0.0691→0.1027(**+49%**)，
   totalKills 13→104(**8×**)，avgKills 0.26→2.08，avgTicks 3331→3572，
   Crossfire 从冻结(0)→1。DAgger **证实修复射击分布漂移**，学生从"几乎不开火"
   变为"会作战"。残余 0% 是**策略深度差距**（基地防御/清场效率/路径），
   非部署 bug——God-AI 同条件 suite≈0.5753(75%胜)。
   → 已选：**迭代 DAgger 第二轮**（用户 skip 决策、按 continue 推进推荐项）。
3. 🔄 DAgger 第二轮采集运行中：`export-dagger-labels.ts --out tmp/dagger2
   --weights-dir tmp/student-weights-dagger`（st 0-4×sd 0-9，学生=第一轮权重，
   状态分布更真实）。完成后 mix=`tmp/godai`+`tmp/dagger2`（丢弃旧 dagger 弱状态），
   resume=`tmp/student-weights-dagger/weights.json`，output=`tmp/student-weights-dagger2`，
   再跑 `m1-eval` 量化。
4. RL 教师落地后，同一管线直接复用（仅换 label 源）。

---

## §2 P1.5: God-AI 教师端到端蒸馏管线（学生架构）验证 (2026-08-20)

> 计划：`plan/RL-Net-Selection.md` §4.3–4.4（v4/v5）。目标：在 RL 教师落地前，用现成
> God-AI 当教师，端到端验证「CoordConv-ConvMixer-Lite 学生（68,554 参数）+ 离线蒸馏 +
> DAgger 在线蒸馏 + TS 推理 + 保留率测量」整条管线。

### What was built

| 组件 | 文件 | 说明 |
|------|------|------|
| 学生模型 | `nn-training/student_model.py` | ConvMixer-Lite h=64/d=8，BN-free，68,554 参数 / ~37M MAdds；forward 内追加 2 个 coord 通道（uint8 0..255，不除 255）；语料保持 14ch 不 bump schema |
| 学生训练 | `nn-training/train_bc.py` | 新增 `--arch student`（默认 `bc` 路径不动）；复用 masked CE / AdamW / Cosine / best-val 导出 |
| TS 学生推理 | `src/nn/infer.ts` | `StudentModel`（conv3x3/conv5x5dw groups=h/conv1x1/GAP/linear，零分配缓冲）+ `ModelLike` 接口 + `buildModelFromJson/Text` 按 `arch.kind` 分发 |
| 输入适配 | `src/nn/policy-input.ts` | 改用 `ModelLike`（cachedModel/loadModel/NNInput.model）；`think()` 决策谓词 `t==0 || t%K==0 || itemAppeared` |
| God-AI 采样器 | `tools/sim/export-godai-labels.ts` | 离线蒸馏语料导出器：`--stages/--seeds/--difficulty/--out/--max-ticks/--verify-determinism`；writeShard 与 BC 格式一致；确定性双跑字节比较 |
| DAgger 采样器 | `tools/sim/export-dagger-labels.ts` | 学生（NNInput）驱动真实引擎 + 独立 RNG 的 God-AI labeler 每 tick 跟读世界；在 `t==0 || t%K==0 || itemAppeared` 采 (state, God-AI label)；labeler 每 tick think 保持内部状态一致 |
| 评估 | `tools/sim/m1-eval.ts`（既有） | `--policy nn --weights-dir <dir>`；God-AI 基线 `--policy god` 同种子对比 |

### Verification results

- **确定性导出**：`--verify-determinism` 双跑 3 局 5 npy 文件字节一致（`[DET OK]` ×3）。
- **TS↔Python 前向一致**：同权重 + 同 obs/scalars（corpus shard 第 0 样本），TS `StudentModel`
  对 Python `StudentNet` 三头 logits maxAbsDiff ≈ 4e-5（float32 累加顺序噪声），argmax 全 MATCH。
- **权重格式**：42 键（stem/8×blocks{dw,pw}/fc/三头 ×{weight,bias}），与 `StudentModel` 完全匹配。
- **端到端冒烟**：9 局 God-AI 语料（8,252 样本）→ 12 epochs → 5×5 hard 评估。
- **DAgger 冒烟**：学生 9 局（3,725 样本）→ 合并续训 6 epochs → 5×5 hard 评估。

### Eval 对比（hard，5 stages × 5 seeds，同种子）

| 策略 | 胜率 | 说明 |
|------|------|------|
| God-AI（教师，基线） | **72%** (18/25) | suite=0.5985 lcb=0.5291 |
| 学生（BC 12ep，8.2K 样本） | 0% (0/25) | avgKills=0，avgTicks=3933 |
| 学生（+DAgger 6ep，11.9K 样本） | 0% (0/25) | move acc 0.35→0.386 |

0% 属**语料量/轮次不足**（不是管线 bug）：学生在打游戏（平均 96 发子弹/局）但 move 头太弱不会瞄准；
God-AI 教师 72% 门内。val_loss 1.73–1.87，move acc ~0.39（5 类 hard-label，teacher 自身随机）。

### 性能实测（本机，torch CPU 8 线程）

- 训练吞吐：学生 ~3.4ms/sample/step（depthwise 5×5 + pw 1×1 在 torch CPU NCHW 上极慢，1.7s/step@b256；
  channels_last 后 0.88s/step@b256；batch512 无增益；16 线程反降）。实测 ~2.5min/epoch @ 8.2K 样本。
- **全量 35×10 语料（~158K 样本）× 25 epochs ≈ 3.5–4h CPU** —— 这是唯一能抬出非零保留率的下一步。
- DAgger 导出：labeler 每 tick think 使导出 ~18× 慢于纯 God-AI 导出（9 局 162s）。

### 关键教训

1. **学生架构的 depthwise 卷积是 CPU 训练瓶颈**：torch 对 groups=h 的 5×5 dw 无高效实现；
   `channels_last` 仅 2× 加速。69K 参数换来 10× 的每样本 FLOPs（vs BC 52K）——训练成本必须
   计入保留率实验预算（web 推理端 TS 零分配 ~ms 级，部署不受影响）。
2. **BC 语料 label 的 teacher 自身随机性**：God-AI 在相同状态有随机性，hard-label CE 天花板低
   （move ~0.4）。DAgger 标签同样受此影响。
3. **确定性契约成立**：TS 端逐字节复现 Python 前向（float32 顺序噪声内），coord 通道公式、
   uint8 0..255 尺度、GAP、scalar concat 全部对齐。
4. **保留率基线（尚未达成）**：需全量语料训练后重测；届时报告 `学生胜率 / God-AI 胜率`。

### 下一步

1. 全量导出 `--stages 1-35 --seeds 1-10` God-AI 语料 → 学生训练（可后台跑，~4h CPU）。
2. 评估 + 算保留率；若不足，追加 DAgger 回合（学生当前权重 + 更多 seeds）。
3. RL 教师落地后，同一管线直接复用（`student_model.py` 不变，仅换 label 源）。

---

## §1 v2: Scalar Fusion Architecture (2026-08-19)

### What changed

The v1 backbone ignored all 24 scalar inputs. v2 concatenates the 24-dim scalar vector with the GAP output before the FC layer:

```
v1:  obs(14×26×26) → Conv(32→48→64) → GAP → FC(64→64) → heads
v2:  obs(14×26×26) → Conv(32→48→64) → GAP → cat(scalars) → FC(88→64) → heads
```

**FC layer input**: 64 (GAP) + 24 (scalars) = 88. Weight shape [64, 88].

### Files modified

| File | Change |
|------|--------|
| `nn-training/model.py` | `nn.Linear(c + scalar_dim, head_hidden)` + `torch.cat([x, scalars], dim=1)` |
| `nn-training/weights_io.py` | `load_state_into` tolerates FC shape mismatch (loads 13/14 params, skips FC) |
| `src/nn/infer.ts` | `fusedBuf` = pooled + scalars → FC; TS forward matches Python exactly |

### Parameter count

| | v1 | v2 |
|--|-----|-----|
| Total | ~50K | ~52K |
| FC input dim | 64 | 88 |
| FC params | 4,160 | 5,728 |

### Training warm-start strategy

Old conv weights (13/14 params) loaded into v2 model. FC layer randomly initialized.
This preserves learned spatial features while the FC layer learns to use scalar inputs from scratch.

### First epoch results (warm-started from v1 R10)

| Epoch | train_loss | val_loss | move_acc | fire_acc |
|-------|-----------|----------|----------|----------|
| 1 | 1.7835 | 1.3834 | 0.590 | 0.852 |
| 2 | 1.2787 | 1.3521 | 0.583 | 0.851 |

val_loss 1.35 at epoch 2 is already lower than v1's from-scratch start (1.91),
confirming the warm-start works — conv features transfer.

### Training timeline (v2, 68K samples)

| Round | val_loss | Δ vs R2 | Interpretation |
|-------|----------|---------|----------------|
| R1 | 1.1974 | +21.9% | Starting point |
| **R2** | **0.9984** | — | 🏆 Best — breaks v1 ceiling (1.0919) |
| R3 | 1.0066 | +0.8% | Plateau |
| R4 | 1.0172 | +1.9% | Overfitting begins |
| R5 | 1.0256 | +2.7% | — |
| R6 | 1.0342 | +3.6% | — |
| R7 | 1.0481 | +5.0% | — |

**Pattern**: same as v1 — val_loss bottoms at R2, then monotonically increases.
Scalar fusion lowered the ceiling (0.998 vs 1.092) but didn't change the shape.

### M1 Sim Eval (v2, best weights R2 val_loss=0.9984)

```
policy=nn  difficulty=hard  35 stages × 10 seeds = 350 games
WIN RATE 0.0% (gate 60%) → FAIL
SCORE V7 suite=0.1087  lcb=0.1071  meanWinRate=0
avgKills=3.04  avgTicks=4207
```

**All 350 games ended in gameover.** 0% win rate — same as v1 despite val_loss
improving 8.4% (1.0919 → 0.9984).

### v1 vs v2 comparison

| Metric | v1 (no scalars) | v2 (scalar fusion) | Δ |
|--------|-----------------|--------------------|----|
| val_loss | 1.0919 | **0.9984** | -8.4% ↓ |
| Win rate | 0.0% | 0.0% | — |
| Avg kills | 2.6 | **3.04** | +17% ↑ |
| Avg ticks | 4755 | 4207 | -11% |
| Score V7 | 0.1085 | 0.1087 | +0.2% |

**Key finding**: Scalar fusion improved learning (val_loss ↓, kills ↑) but didn't
improve winning. The model kills 17% more enemies but still can't survive to
clear a stage.

### Per-stage highlights

| Stage | avgKills | Notes |
|-------|----------|-------|
| Ramparts | 8.0 | Highest kills — still 0% win |
| Waterways | 6.5 | — |
| Eagle Nest | 6.4 | — |
| Checkers | **0.0** | Complete paralysis — 0 kills in all 10 games |
| Iron Curtain | 1.1 | — |
| Gauntlet | 1.3 | Worst score V7 (0.089) |

### Why scalar fusion didn't help winning

The model can now "see" lives, base distance, enemy distribution, etc. But it
still can't *act on* this information effectively. Root causes:

1. **BC distribution shift still dominates**
   - Scalar fusion reduces the information gap but doesn't fix the fundamental
     problem: once the NN's trajectory diverges from the human's, it can't recover
   - The model needs to be *robust* to its own mistakes, not just accurate on the
     first few decisions

2. **7×7 receptive field can't capture global strategy**
   - 3 layers of 3×3 conv → 7×7 receptive field on a 26×26 board
   - Model can't reason about "enemies are coming from the north, base is south"
   - Scalars give relative positions but the spatial backbone can't plan paths

3. **Checkers stage = complete failure mode**
   - 0 kills in 10 games — the model literally cannot move or shoot
   - Suggests the model has learned a brittle policy that collapses on certain
     terrain layouts

### Lessons learned (v2 additions)

7. **Scalar fusion is necessary but not sufficient** — the model needs scalars to
   make context-aware decisions, but scalars alone don't solve distribution shift
8. **val_loss continues to be a poor game-performance proxy** — 8.4% improvement
   with zero win-rate improvement
9. **Receptive field is the next bottleneck** — model can see the data but can't
   reason about spatial relationships beyond 7×7
10. **BC has a fundamental ceiling on hard difficulty** — the model needs to be
    robust to its own mistakes, which BC doesn't train for

### Status (2026-08-19)

**v2 scalar fusion: 0% win rate on hard. BC approach has reached its ceiling.**

Next options:
- Train on classic difficulty (easier → model can learn complete strategies)
- Switch to RL (reinforcement learning) — train with win/loss signals
- Increase model capacity (deeper conv, attention mechanism)

---

## §0 v1: Conv-Only Baseline (2026-08-18 → 2026-08-19)

### Architecture

```python
# nn-training/model.py v1
class NNPolicy(nn.Module):
    # Conv backbone: 14ch → 32 → 48 → 64, 3×3 kernels
    # GAP → FC(64→64) → ReLU → 3 heads (move/fire/item)
    # scalars parameter: ACCEPTED but IGNORED in forward()
    def forward(self, obs, scalars):
        x = obs.float()
        x = self.conv(x)           # (B, 64, 26, 26)
        x = self.gap(x)            # (B, 64, 1, 1)
        x = x.flatten(1)           # (B, 64)
        h = self.fc_relu(self.fc(x))  # (B, 64)  ← scalars NOT used
        return self.move_head(h), self.fire_head(h), self.item_head(h)
```

**Fatal flaw**: `scalars` parameter accepted but never concatenated into the FC input.
The model had no access to: lives, base distance, enemy distance, fire cooldown, ring integrity, inventory, etc.

### Training timeline

| Phase | Dates | Samples | Rounds | Best val_loss | Notes |
|-------|-------|---------|--------|---------------|-------|
| Initial baseline | 8/18 17:00 | 43,566 | 1×40ep | 1.2431 | First training run |
| Continuous 40ep | 8/18 21:08–23:30 | 43,566 | 3×40ep | **1.1320** | val_loss rebounded after R2 |
| Corpus expansion | 8/19 07:46 | 68,571 | 21×1ep | 1.4083 | From scratch after venv rebuild |
| Continuous 40ep | 8/19 09:54–15:07 | 68,571 | 9×40ep | **1.0919** (R2) | val_loss rebounded from R3 onward |

### val_loss trend (68K samples, v1)

| Round | val_loss | Δ vs R2 | Interpretation |
|-------|----------|---------|----------------|
| R1 | 1.1974 | +9.7% | Starting point |
| **R2** | **1.0919** | — | 🏆 Best |
| R3 | 1.0974 | +0.5% | Plateau |
| R4 | 1.1192 | +2.5% | Overfitting begins |
| R5 | 1.1422 | +4.6% | — |
| R6 | 1.1499 | +5.3% | — |
| R7 | 1.1639 | +6.6% | — |
| R8 | 1.1601 | +6.2% | — |
| R9 | 1.1625 | +6.5% | — |

**Pattern**: val_loss bottoms at R2, then monotonically increases — textbook overfitting.

### M1 Sim Eval (v1, best weights R2 val_loss=1.0919)

```
policy=nn  difficulty=hard  35 stages × 10 seeds = 350 games
WIN RATE 0.0% (gate 60%) → FAIL
SCORE V7 suite=0.1085  lcb=0.1069  meanWinRate=0
avgKills=2.5  avgTicks=4234
```

**All 350 games ended in gameover.** 0% win rate — same as the initial baseline
despite val_loss improving 12% (1.2431 → 1.0919).

### Per-stage highlights

| Stage | avgKills | progress | baseIntegrity | mobility | accuracy |
|-------|----------|----------|---------------|----------|----------|
| Waterways (best) | 5.1 | 0.255 | 0.244 | 0.169 | 0.300 |
| Lattice (worst) | 0.6 | 0.030 | 0.072 | 0.085 | — |
| Ramparts | 4.0 | 0.200 | 0.182 | 0.484 | — |
| Steel Fortress | 2.5 | 0.125 | 0.000 | 0.314 | 0.552 |

**Key observations**:
- `baseIntegrity` ≈ 0 on most stages → base always destroyed
- `progress` ≤ 0.255 → kills at most 25% of enemies
- `mobility` ≤ 0.48 → limited map exploration
- No correlation between avgKills and score — killing more doesn't help if you can't protect the base

### Corpus analysis

**94.2% of training replays are wins** (98/104 cleared all enemies).
Only 6 losses in the corpus (partial clears on Bunker Hill, Labyrinth, Brick Maze, Spider).

This means the NN was trained primarily on winning trajectories but couldn't reproduce them in sim.

### Root cause analysis

#### Why val_loss ↓12% but win rate = 0%

1. **BC loss measures imitation accuracy, not winning ability**
   - val_loss = cross-entropy between NN predictions and human actions
   - A model that perfectly mimics a winning trajectory should win — unless it can't
     maintain the trajectory under distribution shift

2. **Distribution shift (the real killer)**
   - Training: given obs_t, predict action_t (ground truth from human replay)
   - Inference: NN's action_0 may match human, but action_1 diverges slightly →
     obs_1 diverges → action_2 diverges more → ... → cascade failure
   - Even 94% winning training data can't prevent this if the NN lacks the information
     needed to make the same decisions as the human

3. **Missing scalar inputs = missing decision context**
   - Human player decides "retreat to base" based on knowing: "I have 1 life left,
     base ring is damaged, enemy is approaching from the north"
   - NN only sees the 14-channel spatial snapshot — it can't distinguish "aggressive
     push" from "desperate retreat" without scalar context
   - The 24 scalar features (lives, base distance, fire cooldown, enemy count, etc.)
     were available in the encoding but never fed to the model

4. **Model capacity bottleneck**
   - 50K params for 68K samples — near the capacity boundary
   - GAP compresses 26×26 spatial info to 64 dims — heavy information loss
   - 3×3 convs have 7×7 receptive field — can't capture long-range spatial relationships

#### Why move_acc improved but didn't help

- move_acc 0.586 → 0.709 over 10 rounds
- But accuracy is measured against **human actions**, not **optimal actions**
- The human's movement in winning replays is context-dependent — "go left" is only
  correct when you know the base is to the right and enemies are above
- Without scalar context, the NN learns a statistical average of directions, not
  a context-aware policy

### Lessons learned

1. **Never ignore available inputs** — if scalars are encoded, they must be consumed
2. **val_loss is a poor proxy for game performance** — always validate with sim eval
3. **BC requires the model to see everything the human sees** — otherwise distribution
   shift makes inference unreliable
4. **Warm-starting conv weights is effective** — v2 epoch 1 val_loss (1.38) already
   below v1 from-scratch start (1.91)
5. **94% winning corpus ≠ easy BC** — distribution shift dominates even with clean data
6. **Architecture changes require `load_state_into` tolerance** — shape mismatches
   should be caught and handled gracefully, not crash the training loop

---

## §-1 Pre-history (before 2026-08-18)

Training infrastructure established:
- `nn-training/train_loop.py` — continuous training loop with auto-resume
- `nn-training/train_bc.py` — behavior cloning trainer
- `nn-training/start-training.sh` — launch script with VBS detach on Windows
- `tools/replay/export-observations.ts` — NDJSON → npy shard exporter
- `src/nn/infer.ts` — TS runtime inference
- `src/nn/policy-input.ts` — NNInput InputLike implementation
- `src/nn/obs-encoder.ts` — 14-channel spatial + 24-dim scalar encoder
