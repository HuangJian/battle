# Goal-Space Policy NN — Progress Log（plan/Goal-Space-Policy-Rebuild.md 执行日志）

> 按 AGENTS §5.6 / 用户指令建立。新条目置顶（倒序）。架构改动 / 评估结果 / 教训都记这里。
> 任务卡编号（T0–T12）与规格 § 号均指 `plan/Goal-Space-Policy-Rebuild.md`。
> NN 训练统一经 `nn-training/start-training.sh|.ps1` 启动（AGENTS §5.6 硬规则）。

## §17 吞吐优化 T0–T2：OMP 扫档定案 OMP8+PROC_BIND，主役恢复并提速 18%（2026-08-31）

**背景**：plan/goal-nn-throughput.md 吞吐优化。T0 基线（balanced S3 cap2，152 shards it3 实测）：PPO 150chunks×4ep ≈ 1260s（chunk ~8.4s）。用户修正方案：**PPO-only 基准**（复用已有 shards 重跑 PPO 计时，不重新 rollout）——`nn-training/ppo-bench.py`（新工具，可复用）。
- 内存确认 32GB（此前 16GB 疑虑解除；T4 双缓冲可行）。
- **档序**：OMP {12, 8+PROC_BIND=close, 16} × mb{512} → 最佳 OMP=8（6.887s/chunk）→ mb{1024,2048}@OMP8。
- **结论表**（chunk_time，越小越好）：
  | 档 | chunk_time | ppo_sec |
  |---|---|---|
  | **OMP8+PROC_BIND × mb512** | **6.887s** | 1033s |
  | OMP8 × mb1024 | 7.195s | 1079s |
  | OMP8 × mb2048 | 7.388s | 1108s |
  | OMP16 × mb512 | 7.713s | 1157s |
  | OMP12 × mb512（默认） | 8.401s | 1260s |
- **定案：OMP8 + OMP_PROC_BIND=close + mb512**（PPO 提速 −18%）。mb 调大无收益（2048 反慢 7.3%）——chunk 粒度已非算术强度瓶颈。
- CPU 澄清：OMP=12/16 在 8 物理核上也是**满载 8 核**（实测 810% 单核等价），"任务管理器 50%"= HT 机器物理核占比的正常读数；OMP8+PROC_BIND 靠消除 HT 缓存争用胜出。
- **事故/教训**：① 双训练器并发 = OMP 争抢、测量失真（必须独占时停主役）；② 杀训练器必须连 bash 包装链（此前只杀 python 致残留链自动推进 spawn rollout bun）；③ ppo-bench 首版把 `ppo_update` 返回值当 list（实为**聚合 dict**）导致崩溃丢数据——已修 + 重跑。
- **主役恢复**：balanced S3（tmp/s3-cap2）以 OMP8+PROC_BIND 续跑（weights it3 09:46，shards 复用 wver 对齐，PPO 直接重放）。
- T3（`--eval-every`/`--eval-at`，commit 0f8d940）已落地默认 1 字节一致；主役暂用每轮 eval（门判节奏不变），需稀疏化时 `--eval-at` 显式指定。
- T4 双缓冲：内存 OK（32GB），待主役稳态后另行评估（高 ROI：藏掉采集 500-850s/轮）。

## §17.1 T4 双缓冲落地：两处隐式缺陷 + 端到端验证（2026-08-31，commit 85f3953）

`--double-buffer` 冒烟全链路实测（`--iters 2 --stream 1 --seeds 1`，本机+集群）暴露两个让双缓冲
**静默失效**的缺陷，修复后 2 轮端到端通过——

1. **iter_id 格式崩分布式子进程**：`_run_collect_only` 的 `iter_id=f"collect-{it}-{pid}"`
   撞 `run_rollout_queue` 的 `int(iter_id.rsplit('.',1)[-1])` → 每个 collect-only 子进程
   ValueError 崩溃（rc=1），预采从未落盘。改 `f"{RUN_ID}.{it}"`（run_rl.py）。
2. **本地路径漏 wver**：`run_rollout` 未给 export-rl-rollout.ts 传 `--wver`（队列 local slot
   有传），本地 shard manifest 无 wver → 下一轮 `completed_pairs` 永不命中。已对齐补
   `--wver`+`--node-label local`（rl/queue.py）。

**端到端证据**（collect_wall 墙钟）：
- it1（自采+PPO）：`collect_wall=155.2s` → 写回 → **spawn 子进程 pid=15500**（快照=最终权重）
- it2：`dist resume: 1/1 planned pairs already on disk` → `collect_wall=6.6s`（采集墙钟
  藏进上轮 PPO 尾段），PPO 直接消费子进程 shard（`loaded 1 RL shards from it2`）

**对你（on-policy）问题的实证结论**：spawn 位于主循环唯一写回点（run_rl.py 638，steam 返回后
= 所有 wave+tail-drain 完成）之后 → 快照恒为**整轮最终权重**；stream wave 只改内存 model 不落盘
（节点采集权重 stream 启动时冻结）。集群采集再快也只是把 it N 提前收尾，够不到 it N+1 的预采
权重。不变量已记 DECISIONS §301。

**顺带修复/发现**：
- 期间 `rl-config.json` 缺失（被 T0/T1 测试链弄丢）→ 主役一度全本地，已从 `tmp/rl-config.bak`
  恢复；`load_dist_config` 每轮动态读，主役无需重启即重挂集群。
- 节点升级恢复期 a95 偶发 ping failed / 404-409（"task lost / wver not cached"）——节点侧
  状态问题，非 T4 逻辑；现在已在线。
- 残留开销（非正确性）：`run_rollout_queue` round 收官贴 settled +~112s，collect-only 子进程
  跟挂 ~2min。吞吐收益远大于此，暂不优化。

**测试**：test_run_rl.py / test_train_loop_pure.py 全 PASS；pre-commit 全过（含 freeze gate）。

## §16 🔴 HIGH 修复：cleared 传播到分布式 eval + 训练器 clearRate（2026-08-31 晨，用户审计项）

**用户审计暴露的遗漏**（progress §15 高估了覆盖面）：§15 写"eval 同时报告 stage_clear 与
全灭"——对**本地 m1-eval** 成立，对**分布式 export-eval-game.ts** 不成立。当时 export-eval-game.ts
无 cleared、EvalResult 只有 win、eval_log 只存 win/dims ⇒ 分布式 eval 仍在报被 BONUS TIME 窗口
（600 tick）截断的 stage_clear 胜率，系统性少算 ~10-15pp（it13 实测 win 60.7% vs 真全歼 73.2%）。

**根因**（不止一处）：
1. `export-eval-game.ts` 无 `cleared` 字段（EvalResult 只有 win）
2. `rl/eval_dispatch.py` record() 只透传 win，eval_log 无 cleared
3. `export-eval-game.ts` **不在 codeHash 集内** ⇒ 节点 agent 不会随 schema 变更升级，节点局
   产出旧版数据（本地 self 用新枚举 `max_ticks`、节点 mac/a96 用旧枚举 `timeout` ——重启后
   实测实时印证）⇒ 本地/节点混合数据不可比
4. 训练器 summary 无 clearRate，门判定全歼无处可读

**修复**（commit 待定）：
- `export-eval-game.ts`：EvalResult 加 `cleared`（= `allEnemiesCleared(world)`），透传进
  `_eval_report.json`；头注释更新（本文件 2026-08-31 起入 codeHash，旧"不在哈希集"说明作废）
- `rl/eval_dispatch.py`：record() 透传 cleared 进 eval_log；summary 加 `clears`/`clearRate`
  （聚合含 ledger 重放，旧行无 cleared 保守记 0）；done_msg 打印 clearRate 与 winRate 并排
- `dist_common.py` + `sampler-agent.ts` 双语：`export-eval-game.ts` 入 codeHash（与 rollout
  同集）⇒ 节点随 schema 同步
- `tests/sim/telemetry-parity.test.ts`：分发对账加 `remote.cleared === local.cleared` 断言
- 冒烟：s1020/seed860001 单局 `_eval_report` 产出 `cleared=true`（stage_clear 且全歼）✓
- typecheck + 15 sim tests + lint 全绿

**操作**：S3 训练器已重启两次（断点续跑无损）——① 加载 cleared 透传的 eval_dispatch；
② 加载含 export-eval-game 的新 dist_common（节点升级判断）。节点将随下一轮 codeHash 检测
自动 pull + 重启。**it14 为过渡轮**：本地局已有 cleared、节点局尚无（旧 agent），clearRate
聚合以节点升级完成后的迭代为准。

**🟠 门禁字段定案**：run_rl.py 的 `winRate` 是 rollout 训练语料口径，**不读 eval_log**；
S3 门（全歼 ≥70%）出口判读**读 eval_log 的 cleared**（或 dims.progress>=1.0 兜底），
与方案 A（§14）同一口径，不会卡死。eval summary 的 clearRate 即门禁读数。

**待办（延续）**：P0-2 A5 重跑（kaiming init 纯从零臂）待 S3 稳定后并行；S3 捡道具技能
未涌现（loot it1 0.63 → 0.56 无升，kill2 无拾取激励，符合预期，门禁不卡 loot；S4a 若有
道具需求再议）。

## §15 A4b 复测**通过** + P0-1 遥测修复 + S3 启动（2026-08-31 凌晨，goal-nn-next 卡）

### A4b 复测（卡 P1-2：S2 出口立即触发，硬闸）—— **过** ✅
- 协议（方案 §4.3）：真实 hard STAGES 0–3 × 60 seed = 240 局，与随机权重基线**同 seed
  配对**，走 `.ledger.jsonl`；判据 = 胜率 ≥ 基线 +5pp **且** 中位存活 tick 高于基线。
- 基线 = `tmp/scratch-init/weights.json`（近均匀 scratch init，N=240）；候选 = S2 过门权重
  `tmp/s2-cap/weights.json`（它16）。
- **结果（2026-08-31 01:00，双臂各 240 局，同 seed）**：
  | 臂 | 胜 | 胜率 | 中位存活 tick |
  |---|---|---|---|
  | 随机基线 | 0 | 0.0% | 3299 |
  | **S2 权重** | **37** | **15.4%** | **3536** |
- **判定：双判据皆正 ⇒ 过闸**（15.4 ≥ 0+5 pp；3536 > 3299）。对比 §9 旧判定（A4-warm
  0% / 中位存活 2274 < 3299 = 门败）——S2/新配方（kill2 + seed-rotate + 量级归一化）在
  真实关**首次实测到正迁移**。二阶段（S3–S4b）解锁。
- 产物：`reports/p1-random-baseline-S2.html.{html,ledger.jsonl}`、`reports/migration-probe-S2.html.{html,ledger.jsonl}`。

### P0-1 修 eval 遥测矛盾 —— **已修 + 锚值核验通过**
- **根因查清（实测，非猜）**：reward-sweep 时代 shots=0 属一次性路径残留；当前
  `export-rl-rollout` 遥测本就正常（实测 shotsPerGame=8.75）。**真正的口径 bug 有两个**：
  1. **outcome 枚举不齐**：`runSimulation`(本地/锚) 用 `SimOutcome`
     （'max_ticks'/'gameover'/'stage_clear'），`runEvalOne`（m1-eval 远程分发）用
     'timeout'/'base_destroyed'/'lives_exhausted' ⇒ 同批内 本地/远程 结果在 ledger/报告层
     **不可比**（门判与配对评估会判错）。**修复**：`export-eval-game.ts` 对齐 SimOutcome，
     细分归一 'gameover' + 新增 `lossDetail` 字段透出；`EvalResult.outcome` 类型收紧。
  2. **kills 双口径（语义差异，非 bug，已记录）**：`world.killCount` = 敌人阵亡总数
     （含地雷/友军 AoE 殃及，KillPipeline 记账）；事件流 `by==='player'` = 玩家直接击杀。
     双侧评估（锚与 candidate）**统一用 killCount 即自洽**（runEvalOne ≡ runSimulation
     killCount 对账已单测锁定）。
- **交叉校验单测**（`tests/sim/telemetry-parity.test.ts`，独立 re-implementation 非复制实现）：
  ① 事件流重算 vs `RunTelemetry`：shots/deaths 全等、kills ≤ killCount 守恒；
  ② `player_hit` 与死亡口径兼容（death≥1 ⇒ hits≥deaths）；
  ③ 分发对账：runEvalOne ≡ runSimulation 的 outcome/ticks/killCount/playerShots/playerDeaths。
- **验收②**：重跑 A0 S2 锚（`arena-god-baseline --levels S2`，hard+classic 各 180 局）——
  锚值**不变**：胜 100%、击杀 3.0、**死亡 0.01**、aliveTicks 中位 **672**、shots 9.24。✓
  布局散列 S2-v0..2 = 43212861/bde445b2/70ade799（与 A0 报告一致）。
- typecheck + lint + 相关 sim 测试全绿。

### P2-1 S3 升档 —— **已启动**（tmp/s3-cap，2026-08-31 01:01）
- 命令：`start-training.sh --script run_rl.py --bc tmp/s2-cap/weights.json --out/--traj
  tmp/s3-cap --iters 60 --max-hours 12 --stages 1020-1022 --seed-rotate 50 --max-ticks 3600
  --workers 8 --stream 1 --dodge off --reward toy:kill2 --eval-stages 1020-1022
  --eval-games-per-stage 20 --keep-iters 3 --rotate-stages 0`。
- 启动确认：Git-Bash venv 正确（**⚠️ 启动必须显式
  `& "C:\Program Files\Git\bin\bash.exe" -c ...`——本机默认 `bash` 解析到 WSL
  WindowsApps 别名，会把 Windows venv 当 Unix 分支重装依赖失败**，踩过一次，已免疫）；
  warm_start_normalize（trunk×0.8884 / policy heads×0.04759 / value zeroed）✓；已 push；
  ⚠️ `--kill-previous` 的 pgrep 在 git-bash 不可用（skipped）——停训一律走进程名限定 ps1。
- S3 门（方案 §2.1）：全歼 ≥70% + 三项指标达 S2 水平（击杀 7.96/死亡 0.68/中位 2184）；
  中途检点：rollout <50% 持续 3 iter ⇒ 降档评估；熵 <0.7 查、<0.3 按塌缩处理。
- P0-2（A5 重跑）待 S3 曲线稳定后再定并行（本机 8 workers 已占满 + 集群被 S3 占用）。

### 启动教训（本节第三条事故免疫）
- `bash` 在 PowerShell 下解析到 WSL `WindowsApps\bash.exe` ⇒ 平台检测走 Unix 分支、
  venv 选 bin/python、`/mnt/d` 路径、pip 缺失——启动脚本必须显式 Git-Bash 全路径。

## §14 P0-0 timeout 尸检：S2 的 38% 失败里 21/60 是**伪负局**（2026-08-30 夜）

**做法**：把交接单的 `_trace-s1012-860006.ts` 批量化（`tmp/vrecord/_autopsy-{timeouts,worker}.ts`），
经 `tools/lib/worker-pool.ts` 的 `runChunkedWorkers` **分片并行**，用 it14 权重把 S2 评估集
（1010/1011/1012 × seed 860001-860020 = **60 局**）全跑一遍贪心 NN，逐局采集
结局 / 击杀 / 末杀 tick / 开火 / 玩家最长静止 / 敌人最长静止 / 末段冰封 / 剩余敌人 / 道具存活 / BONUS 窗口。

**并行度实证**（对拍同一批 12 局）：串行 39.3s vs 12 分片 8.4s（单片合计 73.9s）⇒ **加速 8.80×**。
全量 60 局 @16 分片 = **41.6s**。

**口径自校验**：本脚本测出 stage_clear **61.7%**，官方 eval（it14）为 **62.5%** ⇒ 尺子可信。

**🔶 核心发现 —— 伪负局**：

| 分类 | 局数 |
|---|---|
| stage_clear | 37 (61.7%) |
| timeout | 23 (38.3%) |
| ├ **道具窗口截断（伪负局）** | **21** |
| ├ 冰封残局（交接单那个） | 1 |
| └ 其他 | 1 |

- **真实"敌人全灭"通关率 = 58/60 = 96.7%** —— S2 门（全歼 ≥80%）**实际已达标**。
- **根因（规则，非策略）**：`SimulationEffects.checkStageClear`
  要求 `enemiesRemaining<=0 && 全灭 && **地上无存活道具**`。最后一个被击杀的敌人掉道具后
  ⇒ 进入 **BONUS TIME 窗口（`POWERUP_PICKUP_WINDOW_MS = 10000ms ≈ 600 tick`）**，窗口走完才判通关；
  而 S2 的 `max-ticks = 1200` ⇒ **末杀 tick 必须 ≤600 才来得及**。实测 timeout 局平均末杀 tick
  **768**，差 168 tick。NN 无拾取行为（God-AI 有道具逻辑，故不受影响）。
- **对交接单的裁定**：「冰封残局自锁」真实存在但**只有 1/60**，不是主失败模式。
  ⇒ **P0-3（屏蔽被挡方向）暂不实施**（原定触发阈值 ≥15%，实测自锁占比 ≈1.7%）。
  若当初直接动策略，就是给 1/60 的问题花一整笔预算 —— **先量后改这条纪律救了一次**。

**待用户拍板（涉及门禁口径，不自作主张）**：
- 方案 A（推荐）：eval 同时报 `stage_clear` 与 `全灭`，**S2 门按 §2.1 字面"全歼率"判**（= 96.7% 过门）。
- 方案 B：S2 `max-ticks` 1200 → 1400（留出窗口余量），代价是 §2.3c 钉死值变更 + 训练成本上升。
- 方案 C：教会策略捡道具 —— 本就是 **S3 的课程目标**（§2.1 S3 新能力含"捡道具"），可自然解决。

**🔷 决策与结论（2026-08-31，用户拍板 = 方案 A）**：
- **用户选择方案 A** ⇒ eval 同时报告 `stage_clear` 与 `全灭(annihilation)`；**S2 门按 §2.1 字面"全歼率"判**。
- **代码落地**：`src/game/SimulationEffects.ts` 导出 `allEnemiesCleared(world)`；
  `tools/sim/simulation-runner.ts` 的 `SimResult` 加 `cleared: boolean` 字段并填充；
  `tools/sim/sim-worker.ts` 的 `SimTaskResult` 透传 `cleared`；
  `tools/sim/m1-eval.ts` 聚合 `clearRate`（新增 `WIN RATE` 行旁的全歼率）；
  `tests/level-sim.test.ts` 三个 mock 补 `cleared`；`bun run typecheck` 全绿。
- **S2 门实测（方案 A 口径）**：用 it16 权重 `tmp/s2-cap/weights.json` 跑真实 student 贪心策略于
  S2 评估集（1010-1012 × seed 860001-860020 = 60 局，16 分片并行）：
  - **全歼率 = 96.7%（58/60）≥ 80% ⇒ S2 过门** ✅；
  - 旧 `stage_clear` 口径 = **0%（60/60 timeout）** —— 证实是 BONUS TIME 窗口截断的伪负局，不可信。
- **口径自校验**：本 worker 与 P0-0 尸检脚本（同 it14-era）完全一致 —— autopsy 自家
  `_autopsy-out.txt` 同样录 `stage_clear 0% / timeout 60 (100%)` 但 `全灭 58/60=96.7%`，
  两把尺子对得上 ⇒ `全歼率` 是真实信号。
- **后果**：**S2 解锁 → 进 S3（1020-1022）**；判据②/③（受伤 ≤1.2×锚 / 存活 ≥80%×锚）仍受 P0-1
  遥测 bug 阻塞，后续修 P0-1 再补判，不阻塞进 S3。DECISIONS 已记 §299。

---

## §13 进度核实与预注册补齐（2026-08-30 夜，核实后）

承接 §12，做了一次完整核实（分支 / 测试 / 训练曲线 / 账本一致性），补齐 5 项：

1. **Phase-1 闸的复测点**（最要紧）：§297 #2 缓期 A4b 时只写"随门判定自然复测"、**没给时点**
   ⇒ 这道闸可能永不触发（拖到 S4b 就是循环论证）。**钉死：S2 出口立即复测，最迟 S3 出口，
   不得二次缓期**（→ 方案 §4.3 / DECISIONS §298 #3）。判据本身未改。
2. **熵红线**：预注册健康线 1.5–1.8，S2 实测 it1 1.24 → it10 **0.93**，已低于 S1 封顶时的 1.21。
   补监控红线：**<0.7 停下查，<0.3 按塌缩处理**（→ §298 #2，方案 §5 A6 卡）。属监控约定，非新门禁。
3. **机时豁免落账**：用户"只要收敛不考虑机时" ⇒ 82h 总账保留为核账口径但**不再作停训理由**，
   替代判据 = 迭代产能与收敛形态；**豁免不等于免记账**（→ §298 #1，方案 §4.5）。
4. **A5 判定作废并须重做**：旧结论跑在未过门的旧配方上（两臂贪心 60/60 全同）⇒ 数据作废；
   新配方下纯从零臂从未跑过 ⇒ "教师是否构成天花板"仍无答案（→ §298 #4，方案 §5 A5 卡）。
5. **A10 / A11 / A-x 作废**：A8a 登记方案 3（不开目标头）的连带记账（→ §298 #5）。

**核实结论（供下一班直接采信）**：
- `bun run check` = **1661 pass / 0 fail / 3 skip**（全量，非仅新文件）。
- **S1 过门真实有效**：三处修复（warm_start_normalize 量级归一化 / kill2 奖励 wAlive→0 /
  seed-rotate 150 局轮转）后 17 iters 25% → 97/99/100%，自评 96–100%。**路线成立。**
- S2 曲线健康但噪声大：rollout 33.3→53.6%（趋势升），eval 53–72%（门 >80% 连续 5 iters 未达）。
- **新发现的阻塞项**：eval 报告里 `shots/deaths` 与 `kills` 矛盾（交接 §9 开放项 4，未修）
  ⇒ **S2 门判据②"受伤 ≤1.2×锚"当前不可判**。已列为 P0-1。
- 账本不一致已修：§9 原标题写"门败 ⇒ 二阶段不投"，与"S2 正在跑"矛盾 ⇒ 加后续判定注，
  明确现行结论是 §297 #2 的"缓期"。

**产出**：`docs/goal-nn-next.md`（后继工作计划，交接给训练 agent 的执行清单）。

---

## §12 交接接管：goal-nn 训练任务（2026-08-30 夜，commit 待补）

接收 `docs/goal-nn-handoff.md`，交接清单逐项核对：

- **git**：`goal-nn` 分支 HEAD `73db400`（交接文档提交），commit 链与 §8-§11 相符，工作区干净。
- **训练进程**：唯一训练器链 `23496(bash start-training.sh) → 3528(.venv python run_rl.py) →
  19928(pyenv python 子进程，Windows multiprocessing spawn 结构，命令行同参)`——非僵尸
  复活，`list-rl.ps1` 只见 S2 一个战役。
- **命令核对**：与交接 §3 逐参数一致——`--bc tmp/s1-cap2/weights.json --iters 30 --max-hours 8
  --stages 1010-1012 --seed-rotate 50 --max-ticks 1200 --workers 8 --stream 1 --dodge off
  --reward toy:kill2 --eval-stages 1010-1012 --eval-games-per-stage 20 --keep-iters 3`。
  rl-config `rl` 块 rotate_stages=0（保守态）、upgradeBranch 空 ✓。
- **S2 曲线（it1–it5，21:12 起训）**：
  | iter | rollout win | eval win | entropy | kl |
  |---|---|---|---|---|
  | 1 | 33.3% | 59.3% | 1.24 | 0.265* |
  | 2 | 31.3% | 51.7% | 1.25 | 0.019 |
  | 3 | 39.3% | 67.2% | 1.21 | 0.028 |
  | 4 | 42.0% | 59.3% | 1.08 | 0.033 |
  | 5 | — | 72.3% | — | — |
  *it1 kl 高为 warm-start 首迭代正常；此后 kl<0.05 ✓（PPO 前向已跑 5/30）。
  rollout 33→42 单调升、eval 5 迭代趋势升（59→52→67→59→72）、mean_ret 0.56→1.26、
  value 收敛。**形态与 S1 过门曲线同期一致**（S1 也是 ~25% 起步 17 iters 爬到 97%）。
- **检点判定（记录决策，不降档）**：中途检点"eval<80% 且 rollout<50% ⇒ 降档减敌"当前
  字面成立（72%<80%、42%<50%），但 §11 明示"用 5 迭代 eval 趋势代替单点"；两线均处
  强上升期，过早降档会打断在途收敛。**决策：继续观察，看板**——rollout 站上 50%
  ⇒ 无降档风险；rollout 停滞 ≥3 iter 或回落 ⇒ 触发降档评估（1010-1012 → 减 enemyCount
  到 2 敌）；eval 连续 5 iter ≥80% ⇒ 过 S2 门资格评估。本决策属预注册检点的趋势化执行，
  非改门。
- **节点池（/pool 21:42）**：8 台 6 在线·代码同步 + lite 旧版 v7b0beea（交接 §9 开放项 5
  残留）+ **a96 已回归**（v73db400 最新、最近成功 19:54，21:35 一笔 404 task-lost 属
  restart/purge 常态）。评估批已走 a95/a97/a98。lite 升级请求待训练器下轮发起。
- **`bun run check` 后台进行中**（tmp/check-handoff.log）。
- 预算核账：21:12 起 `--max-hours 8 --iters 30`，单 iter ≈ 6-9 min 墙钟 ⇒ ~20+ iters 内
  `--max-hours` 不构成瓶颈；S2 CPU 预算 8h 帐篷核账正常。

**下一步**：监控检点看板；check 绿后按 §7 硬纪律待命（S2 过门 → S3 需 S2 门三项达标 +
  eval 连续 5 iter >80%）。

## §11 文档同步：决策包 §297 归档 + S2 进行中（2026-08-30 晚）

**S1 过门确认**（§10）后至本条的增量，全部决策已归档 **DECISIONS §297**（一次
读齐，不再散落 commit message）：

| # | 决策 | 触发证据 |
|---|---|---|
| 1 | 奖励臂 **kill2**（wAlive→0） | A4 贪心塌缩诊断；S1 重开 17 iters 97-100% 过门 |
| 2 | **A4b 缓期** | S1-only 学生缺 S2/S3 技能，真实关复测结构性无解 |
| 3 | **L0 退场判据更换** | off vs l0 逐位一致（强权重 L0 惰性）；红线 = off deaths/局 + alive-ticks |
| 4 | **eval 门控升难** | 新档 eval>80%（5 迭代趋势）进档；rollout<50% 降档 |
| 5 | **arena 自评**（--eval-stages） | 真实关评估对 S2 实验无意义；EVAL_SEEDS 扩至 20 |
| 6 | **stream 默认**（AGENTS §15.6） | 串行闲置集群 ~50% 墙钟；ppo.update 别名补齐 |
| 7 | **rl-config rl 块**（11 项机制默认） | "config 键被忽略"类别根治；切换器默认取保守态（二次 35 关意外免疫） |

**AGENTS §15 落地**（用户确认后）：闭环训练语料纪律五条——语料逐轮轮转 /
微课只配当哨兵 / 部署口径单独评估 / 大批量稳 KL / 换语义=换实验；细节与
s1-cap 案例进 docs/agents.details.md §15。

**S2 进行中**（tmp/s2-cap，stream + local_slots=10 + dodge off）：it1 = 33%
（150 全新局，诚实基线），arena 自评 60 局/迭代已上线（s1012 timeout 但
2/3 击杀，quality 0.82——比 S1 同期低，符合升档预期）。检点：eval<80% 且
rollout<50% ⇒ 降档减敌；eval>80% 连续 5 迭代 ⇒ 过门进 S3。

## §10 S1 过门 + S2 升档 + 节点池运营（2026-08-30 下午–晚间）

### S1 封顶实验：**过门**（回答了"路线成立吗"）
大语料修订后（seed-rotate 50 × 3 变异 = 150 局/迭代，kill2 奖励，stream 模式），
17 iters 内 rollout 胜率 25% → **97/99/100%**（末三轮），arena 自评 96–100%
（固定 held-out 种子 860001+，与训练抽签空间近乎无交）。**S1 门（≥90%）通过，
"从零练执行器"路线成立。** 对比：A4 时代（固定 12 局 + wAlive 锚 + 坏 init）
21 iters 只到 26.7%——三个修复各自可归因：①量级归一化（init/warm-start）；
②wAlive→0（kill2，拔掉"原地骚扰"锚）；③语料轮转（去记忆化）。

### 观察员交接（观测建议 2026-08-30 19:50，全部采纳）
- 双口径饱和（rollout it13-15 = 95.3/98.0/97.3%，eval 96-100%，KL<0.03、熵缓降
  1.77→1.21 = 健康饱和非塌缩）⇒ S1 语料打穿，it16+ 纯烧算力 ⇒ 停。
- dodge A/B 复测：当前强权重 `--dodge off` vs `l0` 15 局逐位一致（93.3%、0 死），
  弱权重（a4-warm）亦无差 ⇒ L0 在强策略下惰性；S2 起以 `--dodge off` 自持探针
  开档，红线 = off 下 deaths/局 + alive-ticks（弱 0.222 / 强 0.0），dodgeCov≥2%
  判据随 L0 退场作废。
- eval 门控升难：新档 eval >80% 再进下一档；rollout 崩 <50% 判过难降档；
  用 5 迭代 eval 趋势代替单点。

### S2 已启动（tmp/s2-cap）
1010-1012（size14、3 敌 basic+fast）× seed-rotate 50 = 150 局/it，S1 过门权重
热启动，--dodge off，toy:kill2 不变，stream 模式，eval 自评 60 局/it。
S2 门（方案 §2.1）：全歼 ≥80% + 受伤 ≤1.2×锚(0.01) + 存活 ≥80%×锚(672)。
中途检点：eval <80% 且 rollout <50% ⇒ 降档（减敌数）。

### 流式修复（用户报障 → 一行根因）
`"stream": 1` 在 intent_rl 块（run_rl_intent 专属），run_rl.py 从不读它且启动
命令未传 ⇒ 四次训练全串行、PPO 窗口集群闲置。且 `run_rl --stream 1` 的流式
路径本身从未拉通：`stream.py` 契约要求 backend 暴露 `update`，ppo 后端缺别名
（仅 intent 有）。修复 = ①ppo.py 补 `update = ppo_update` 别名；②启动命令
`--stream 1`；③AGENTS §15.6 新规：RL 训练默认 stream（run_rl 代码默认 0→1），
串行仅调试用；新增 backend 必须实现完整 stream 契约。

### 二次"35 关意外"——config 默认值 authored bug（2026-08-30 晚，自省）
rl 块提炼时照抄了 intent_rl 的 `rotate_stages: 35`，且 `--rotate-stages` 默认接
`_d("rotate_stages", 0)` ⇒ config 的 35 覆盖了显式意图 0 ⇒ S2 战役静默跑成
rotate 模式（真实 35 关 × 10 seed = 350 局，`--stages 1010-1012` 被 rotate 模式
忽略）。修复：`rl.rotate_stages → 0`（arena 战役显式模式；真实关 rotate 属各
战役显式传参）+ 启动命令显式 `--rotate-stages 0` + 清目录重跑。
**教训**：config 化默认值 = 把"启动命令里看不见的语义"搬进配置文件——每个键都
要过一遍"这个默认对所有未来战役都安全吗"；rotate_stages 这类**切换器**的默认值
必须取保守态（0=off），非拷贝他人块。

### 节点池卫生（待用户节点侧处理）
- lite：7b0beea↔8a319c0 三次横跳，疑节点侧 pull 分支竞争或双 agent，需上机排查
- a95：停在 3a9b907（codeHash 恰好正确——3a9b907 后无 hash 集内改动），需正常
  pull 升级到最新
- a96：仍 ping 不通

## §9 Phase-1 收官：A4/A5/A4b/dodge-A/B 全部落地，A4b 门败（2026-08-30）

> ⚠️ **后续判定（2026-08-30 夜，现行结论以本注为准）**：本条标题与末尾"二阶段不投"
> 是**当时的判定**；次日 S1 三处修复后过门（§10），**DECISIONS §297 #2 将 A4b 改为"缓期"** ——
> 理由：探针运行在 S1 未过门（26.7%）时，0/240 是能力不足，不是迁移被证伪。
> ⇒ **二阶段（S2 起）已投**（`tmp/s2-cap` 在跑）。**复测点已钉死：S2 出口，最迟 S3 出口**
> （§298 #3，方案 §4.3）。读本条时请以本注为准，不要据标题认为"违反了止损线"。

### A4/A5 出口（reports/s1-exit-report.md）
- 训练曲线（随机 rollout，12 局/iter）：A4-warm last-5 **50.1%**（上升）；A5-scratch **16.6%**
- 贪心门评估（60 局/臂）：两臂均 **26.7%**，逐局 outcome/ticks **60/60 全同**——
  logits 实测不同（EVAL_DEBUG 指纹）但 argmax 策略在所有访问状态收敛一致
  （S1 主导策略 = 上推 + 中线开火）
- **S1 绝对门（≥90%）未达 ⇒ 预算停**（止损线 5-①：指标上升，结论"未知"非"失败"）
- **A5 三态结论：≈ 未定**（贪心口径无可分差异；随机口径 A4 占优但噪声大）
- 期间修复：BC warm-start 量级病理（trunk 激活 ~200 / logits ±7600 / value 随机头）
  ⇒ run_rl.build_model 的 warm_start_normalize（真实 shard obs 校准，trunk→h15、
  头→logit 范围 3 软先验、value 清零）；eval 报告补 weightsSha 指纹（审计缺口）

### A4b 二元闸（reports/p1-a4warm-s0-3.html vs p1-random-baseline-s0-3.html）
| | 胜 | 中位存活 | 局数 |
|---|---|---|---|
| A4-warm 策略 | **0** | 2274 | 240 |
| 随机基线 | 0 | 3299 | 240 |

胜率差 0pp < 5pp **且** 中位存活更短（2274 < 3299，开火型策略主动接战反而死得快）
⇒ **门败（双判据皆负）**。⚠️ 前提偏离须诚实记录：探针在 S1 未过门（26.7% < 90%）
时运行——结论是"S1 当前能力下无可测迁移"，不是"迁移假说被证伪"。

### dodge A/B（卡 A3 验收③，S2 × 3 变异 × 20 seed × A4 权重）
| 模式 | 胜率 | 死亡/局 | 覆盖率 |
|---|---|---|---|
| l0 | 8.3% | 0.567 | 0.62% |
| god | 6.7% | 0.450 | 3.92% |
| off | 6.7% | 0.500 | 0% |
差异在噪声内（60 局）；验收① 覆盖率 0.62% < 2% ✓ 可辩护；④ L0 优先级为构造保证。

### 止损与下一步（预注册规则执行）
- 止损线 5-②：**门败 ⇒ 二阶段（S2–S5，≈9d + 60h）不投**
- 悬而未决（用户决策）：① 是否给 S1 追加预算（A4 曲线仍在上升，4h 未收敛）；
  ② 追加后仅重跑 S1 门 + A4b（≈4h + 1h），二阶段闸门语义不变

## §8 并行流水线 + 节点池基建重写 + A2 扫描判定（2026-08-30）

### A2 扫描判定（reports/reward-sweep-S1.{json,md}）
kill 臂 26.7% 决定性胜出（balanced/survival 均 1.7%）→ `TOY_REWARD_DEFAULT_ARM` 不变
（kill）。survival 臂近零胜率实证了"高生存权重压制杀敌梯度"的预期风险。
A2 只选臂不判门；S1 绝对门（≥90% 通关）由 A4 出口判。

### 节点池事故与基建重写（用户三指令 + 两个 bug 报告）
**事故链**：rl-config 残留 `upgradeBranch: 'intent-ai'` → goal-nn 推送后 codeHash
变化 → 全部节点（含本机 self agent）被远控 `reset --hard origin/intent-ai` →
回到 31 个提交前。我的提交均在 origin（无损），本地 fast-forward 恢复。

修复（commit 7b0beea / ecfb9e8 / 3553b22）：
1. 升级分支永远 = 训练机当前分支（run_rl 启动锁存 `dist_common.UPGRADE_BRANCH`），
   config 键清空仅作回退；
2. 节点同步 = fetch → checkout branch → pull --ff-only（禁 hash），分叉且干净才
   硬回齐；**脏工作区拒绝破坏性同步**；
3. `/pool` 节点池监控页（主控机 agent，配置含 nodes 才启用）：实时 ping +
   meta 历史聚合，v2 = 60s 刷新 + 最近失败时间 + 表头点击排序；
4. self/回环节点跳过远控（`is_self_node`）——共享工作区禁破坏性 pull；
5. restart 单飞护栏（并发升级请求去重）+ 优雅交接（先停监听再 spawn child）；
6. agent pidfile（agent.pid / agent-child.pid）+ SIGTERM 取消 pending 交接
   ——手动停服不再被 detached 子进程复活；
7. agent 本体入 codeHash（双语同集）+ relPath 归一化正斜杠（Windows self agent
   与 Python 哈希不一致的第二个根因）；权重桶 8 → 64（三训练流并发时慢节点
   409 的根因：每轮 3 个新 sha，8 桶不够滞后任务用）。

### 并行流水线状态
P1 随机基线（真实关 0–3 × 60 seed）：**0/240 胜、中位存活 3299 tick** ——
A4b 闸判据：胜率 ≥5% 且中位存活 >3299。arena-DAgger BC 完成（val_loss 0.97，
acc move 0.83/fire 0.78）。A4(warm=bc-arena) 与 A5(scratch=init_scratch)
同批启动（同 seed / 21 iters / toy:kill / dodge=l0）。

## §7 崩溃归因反转：stageIndex 里程碑爆内存（非 JIT）+ scratch init 修复 + 三处用户修正（2026-08-30）

> 本条取代 §6 中"A8a 按 headroom 不足登记方案 3"的表述，并落账用户指出的三处修正。

### 校准爆出的崩溃与两次归因（第二次才对）

S1 校准（`--stages 1000 --seeds 0-3`）中 export-rl-rollout 在 s1000/seed2 段错误
（RSS 12.7GB / Peak 20GB，bun 1.4.0 "segfault @0x10"）。**第一次归因错误**：用
`BUN_JSC_useJIT=0` 后进程不再立即崩（实为慢到没跑完）＋崩点近空解引用的形状，
误判为 Bun DFG/FTO JIT 误编译，在 `SimulationCombat` 做了三处"绕行扰动"
（快照循环界 / 数值 dir 分支 / 抽出 applyBulletHit 经数组派发）——全部无效且已
**git checkout 回退**。**正确归因**（tmp/memprobe.ts 逐语句打点定位）：

- 崩点 = `scheduleItemDrops` 的里程碑掉落循环 `for (i < milestones) drops.push(...)`；
- 我在 arena 集成把 **stageIndex=1000** 传进了 `World.loadStageData`。killScore 的
  `levelFactor = 1.05^(index+偏移)` 在 index=1000 下单杀得分 ≈4e22 ⇒
  `milestones ≈ 8e18` ⇒ 一次性 push 亿级掉落物 → 内存耗尽段错误；
- "以前不崩"的原因：历史 rollout 全部用真实关 index 0–34。**用户质疑
  （"就你新加的代码会崩？"）是对的**——不是策略轨迹触发 JIT，是我违反了
  `loadStageData` 文档口径（"generated stages use index 0"）却误判 index
  "只影响计分"（分数经里程碑掉落反哺玩法，不是纯观测）。
- **修复**：export-rl-rollout / export-dagger-labels 对 arena 编号传 index 0
  （真实关不变）。修复后该局正常 `stage_clear` 跑完，同 seed 双跑逐字节一致。
- 教训：确定性仿真里"关 JIT 就不崩"不是 JIT bug 的证据（LLInt 只是慢，循环还在
  跑）；管道里的退出码归属（`| tail` 的 EXIT 是 tail 的）是这次误判的直接来源。

### scratch init（纯从零臂的前置条件）

默认 kaiming init 在 8 层残差 ConvMixer 上复合放大（输入 0..255 无归一）：策略头
logits 随机初始化即 ±2000 ⇒ 采样 one-hot、熵≈0 无探索；value 头量级失衡还会在
首个 PPO 更新把策略打成确定性（实测 entropy 0.41→0.009、kl 后续恒 0 自锁）。
`nn-training/init_scratch_weights.py`（工作流级，不改共享 student_model.py）：
trunk×0.1 / move+fire 头×0.01 / value×0.1（正齐次性，测一次按比缩放是精确的）。
校准实测（12 局/iter）：**it1 winRate 16.7%（entropy 1.83、kl 0.0037）→ it2 50%**
——坍缩消失、学习在飞。A5 消融的"纯从零"臂必须从这份 init 起步，否则消融测的
是坏 init 而不是教师价值。

### 用户三处修正的处置

1. **账本滞后（已由用户补）**：§6 置顶条目 + 卡片状态表已建；本条继续置顶维护。
2. **A8a 判读逻辑（已改）**：headroom = **−25pp**（orig 35.83% < random-legal 60.83%
   < static-corner 0%，n=120，可区分性冒烟 114/120 不同）是**倒挂**而非"headroom 不足"。
   正确读法：**当前执行器下测不出正 headroom（探针非真上界）**——教师目标选择对该
   执行器失配（与 §1.1 goal-god 0% 同源，探针保真度①已声明）。A4 仍登记方案 3
   （不开目标头），但理由记为"测不出正 headroom"；A9b 出口由 A8b 用在训 RL 执行器
   复核，可撤销。`goal-headroom-a8a.ts` 的判读分支已改为三态。
3. **obs 位势表口径（已修）**：census 的通道步长误用 `OBS_CHANNELS×26×26`（=整张
   obs 长度），ch1–13 全部越界读到 undefined≠0 计成假 100%。改回 26×26 后重跑，
   现在两列有判别力（S1：ch1 钢 78.7%、ch0/ch5=0；真实 35 关：砖 11.7% / 水 3.6% /
   林 12.3% / 冰 4.1% / **ch5 基地 1.2%**——幻影基地修复在位势表上可见）。

### 其余 A0/A1 出口结论

- **锚值表**（60 seed × 3 变异 × {hard, classic}）：S1/S2/S3/S3H 锚可用（hard 通关
  100/100/97.78/97.78%）；**S4a 锚不可用**（49.44% < 60% ⇒ 按预案该级用绝对阈值，
  `reports/arena-god-baseline.{json,md}`）。
- **max-ticks 钉死**：S1/S2 = 1200（A0 p95 430/992）；S3 = 3600（p95 3129）；
  S3H = 4000；S4a = 12000（p95 触 20000 timeout 上限，按真实关口径）。
- **配对粒度与 CPU 锚**：已按用户指示落账 plan §4.2（(stage,seed) 2100 对定案）
  与 §4.5（实测单价 0.9 CPU-s/100 tick，S4b 反推历史口径吻合）。
- 训练链新旗标：`run_rl.py --reward ''|v7|toy:<arm>` 与 `--dodge ''|off|l0|god`
  经 queue/dist_common/sampler-agent 全链透传（缺省按 stage 解析，真实关行为不变）。

---

## §6 基建卡落地：A0a / A0 / A1 / A2 / A3 / A8a（2026-08-30 凌晨，未 commit）

> 补记：本条由核实进度时补写（纪律 6：卡落地即更账本）。产物均在，代码与单测已过。

**已完成**（`bun test tests/nn/{obs-encoder,arena-ladder,dodge-l0}.test.ts` → **50 pass / 0 fail**）：

- **A0a**：`makeArena`/`makeMazeStage` 加 `layoutSeed`、`makeMazeStage` 加 `enemyCount` 覆盖位；
  **`obs-encoder` 幻影基地已修**（无基地场 `s1/s6/s17/s18` 实测全为 0，S4a 有基地场 s6=0.995 ⇒ 修复生效）。
  产出 `reports/arena-layout-hashes.json`。
- **A0**：`reports/arena-god-baseline.{json,md}`（5 场 × 3 变异 × 60 seed，hard 主 / classic 对照）。
- **A1**：arena 身份贯通，`1000+n` 命名空间生效（`tmp/arena-smoke/rl_s1000_*`、`rl_s1010_*`）、
  dagger arena 冒烟（`tmp/dagger-smoke/dagger_s1000_seed0`）、`--max-ticks 1200` 已钉、
  CPU 校准（`tmp/cpu-calibration.log`，`--stages 1000 --seeds 0-3 --iters 5 --workers 8`）。
- **A2**：`src/nn/rl-reward-toy.ts` 已接入 `export-rl-rollout.ts:60`。
- **A3**：`src/nn/dodge-l0.ts` 已接入 `:62/:542`；**F1 白名单合规**（只 import `perception.canStep`
  与常量，无 `GodAIInput`/`ThreatAssessor`）；**F3 记账合规**（覆盖步落盘 executed 动作 +
  `logProbAt(...)` 重算的 logp，`l0` 与 `god` 两臂都有）。
- **A8a**：`reports/goal-headroom-a8a.{json,md}`（含可区分性冒烟 114/120 不同 + 保真度声明）。

**待办（都不是代码活，是账本活）**：① A0 缺 §4.2 配对粒度定案；② A1 缺把实测缩放写进 §4.5 账本；
③ A2 三组扫描 / A3 四条验收需随 S1、S2 数据补。

---

## §5 路线定案：课程学习从零练执行器（2026-08-29，plan/goal-nn-action.md）

**决策（用户拍板）**：A/B/C 三条路线都不取。改走**玩具竞技场课程学习**：
S1 开火命中 → S2 闪避走位 → S3 砖墙+道具 → S4 有基地→真实关卡 → S5 解冻目标头。
理由：① 模仿学习的天花板在构造上就是教师本身，蒸馏 God-AI 出不了 78.81% 以上；
② 直接用 God-AI 执行器同样被其结构封顶；③ 目标轴在玩具场上没有战略时域可学，
提前开只是噪声。派工文档 → `plan/goal-nn-action.md`（任务卡 A0–A11 + 可选 A-x）。

**God-AI 的新角色**：只当 warm start（定起点），**RL 自身奖励才是天花板（定终点）**；
由卡 A5 做同预算「纯从零 vs DAgger warm start」消融来验证教师是否构成天花板。

**可复用基建（已核对）**：`tools/optimize/curriculum.ts` 的 `makeArena` / `makeMazeStage`
（5 个 arena，原为 God-AI 验证脚手架）、`nn-training/rl_model.py` 逐决策步 `[move(5), fire(2)]`
双头、`export-dagger-labels.ts` v2 schema、`run_rl.py --bc` + `--curriculum-*` 开关
（现有课程维度是"关卡数量"，本方案要的是"环境复杂度"，由卡 A1 新增）。
缺口：`export-rl-rollout.ts` 需加 `--arena` 入口；玩具场需一套**非守家**稠密奖励。

**纪律升级（三次伪影教训）**：新 policy / 奖励 / 权重必须先做**可区分性冒烟**；
任何"上限探针"必须报告**代理保真度**；门数字必须来自**修复后的代码**（T9a 门② 即反例）。

---

## §3 T9a 金丝雀：门 FAIL 与三重归因（2026-08-29，commits b04f4d6..b84c012）

> **后续判定（§5）**：本节的 goal 0.05% 与 goal-god 0.0% 分别来自冻结修复前代码与失真探针，
> 均不作为路线判定依据；路线已转向课程学习，两个数字由卡 A0 重测。

### 训练（本地 10 槽，~14 min / 6 轮）

```
./start-training.sh --script run_rl_intent.py --goal --goal-coarse   --bc nn-training/tmp/goal-bc/weights.json --out nn-training/tmp/goal-rl-t9a/weights.json   --iters 6 --warmup-iters 1 --kickstart-kl 1.0 --kickstart-decay 0.85 --heartbeat 240 --eval-at 999
```

BC：15 epochs loss 59→**4.60**（39,663 点，λ=0.5 τ=1.0，长样本 ×3 加权）。
PPO：140 局/轮 ≈2,200 步；熵 3.15→3.33；value 326→1.65；KL 每 epoch 触发 target_kl=0.04
早停；it6 出现首个 stage_clear（1/140）。

### 门判定（tools/sim/paired-gate.ts，2100 局 --policy goal vs 基线 78.81%）

**canary ② FAIL**：overall **−78.81pp ± 2.49**（goal 0.05%）；B 桶 −83.18pp / C 桶 −78.91pp
非劣界双破。**canary ① PASS**（学习机器正常：BC loss 59→4.6；同网格击杀
random 0.9 → BC 2.9 → PPO 2.9 单调）。

### 三重归因（两次反转的追查，全部记录以警示）

1. **伪影**：首个 "executor-ceiling 85%" 是 `--policy goal-god` 未接入
   m1-eval/simulation-runner policy 链、静默回落纯 God-AI 所致（配对差 0.00pp = 同跑暴露）。
   **教训：新增 policy 必须先做与已知策略的可区分性冒烟（同网格 avgKills 对比）。**
2. **坦克冻结 bug**：接线修复后 goal-god 仍 0% → 插桩发现 989 tick 只走 5 格。根因 =
   §6.1.1 可满足性门 "travelEst ≤ T 否则拒绝重选" 在 T=240（≈10 格 @23 tick/格）下形成
   **移动拴绳**：到达首个目标后任何更远目标被永久拒绝，E4 缓解分支 keeps 旧契约 ⇒ 冻结。
   这是**规格内在矛盾**（承诺期 T=H≤240 vs 地图级 travelEst 300–500 tick），实现期选错了
   语义。**修复**：可满足性门只拒不可达（travelEst=∞），T 只管重评估节奏（commit b84c012，
   含单测更新）。
3. **修复后的真话**：2100 局 goal-god（God-AI 导航目标喂执行器、零网络）＝ **0.0%**
   （−78.81pp ± 2.49）。执行层短板与目标选择、学习无关：L2 裸 A* 跟随 + L3-min 开火
   （顺路+凿墙）+ 窄 dodge（仅 dodge 候选提交时生效）+ 无道具 + 承诺期 240 tick 不回防，
   在 hard 下无生存能力。God-AI 的 78.8% 靠 19 候选全链 + 威胁感知导航 + 主动道具
   （§293 解冻）+ 逐 tick 重定向。

**T9a 最终判定**：②FAIL 且在独立执行器架构下**不可测**（执行器 −78.8pp 淹没一切目标轴
信号）；手册 T9a 隐含前提"执行器 + 好目标 ≈ 竞争力下限"被证伪。T9/T10 暂停。

### 三条路线（待用户决策；本 agent 推荐 B）

- **A. 按手册止损**：机时转 T3——但 T3（导航参数化）量级不足以填 78pp。
- **B. 换集成口径重定义 canary（推荐）**：网络热图作为**目标提供者嵌进 God-AI**
  （替换其导航目标选择，保留全部逐 tick 执行链）——在 78.8% 级执行器上单独测目标轴
  边际贡献。与 R1"反掩码化"不冲突（网络选目的地，不是候选链剪枝）；
  是 §9.2"当函数库/保底层"哲学的自然延伸。改动小、可快速重跑 canary。
- **C. 把独立执行器练到竞争力**：真 L3（§10 全量开火纪律）+ 威胁感知路径 + 道具 +
  回防——本质是重写 God-AI 的执行技艺，风险与工期最大。

### §T9.0（k9）预注册归因处置

新基线由 super-item 恢复 + pursuit-tail 造成，与 T2 承诺机制无关 ⇒ "红利重叠"不适用；
T2 本轨未做 ⇒ 只对新基线判定。

---

## §4 m1-eval auto-dist：每批评估机会性利用远程 agents（2026-08-29，commit 61538b6）

用户指令：远程节点随时可能上线，m1-eval 要像 rollout 一样周期性检查节点状态、每批都
充分利用 agents 算力（"每次跑批能缩短一两分钟都是好的"）。

**缺口 → 修复**（v4.0）：
1. `--dist-nodes` 是 opt-in → **auto-dist 默认开**：不传时默认读 `nn-training/rl-config.json`
   （存在即走混合分派）；`--no-dist` 显式关闭。v3.9 rescan（120s 周期重读配置 + ping）保持
   ——节点中途上线即刻接管份额。死节点开销 = 并行 5s ping（实测 7 死节点配置下 6 局
   总墙钟 5.2s，本地立即开跑）。
2. **无权重策略不可分发** → `kind='none'` 占位桶（3 字节，wver 协议兼容）：
   `god` / `goal-god` 进 DIST_POLICIES 白名单——基线/上限这类最常跑的 2100 局批
   现在可全量外派。
3. **潜在 409 bug**：`uploadWeights(node, 'intent', ...)` 写死 kind——goal 分发会在活节点上
   wver-not-cached 409（此前无活节点从未暴露）。改为随 distKind。
4. **export-eval-game 无 god 分支** → 新增真 God-AI 分支（RNG 派生与 runSimulation 逐字节
   一致；weights 仅 'nn' 策略必需）；顺手修 `scripted.reset()` 写死导致的
   **GodAIInput.reset() 从未执行** bug（关卡自适应参数从未生效——远程 god 局此前
   等于默认参数乱打）。

**护栏测试**（tests/sim/eval-game-parity.test.ts）：
- 远程/本地等价：export-eval-game god ≡ runSimulation god（4 局 outcome/ticks 逐一对齐）。
- **可区分性冒烟**：god vs goal-god 同局必须不同（goal-god 静默回落伪影的永久哨兵）。

**遗留提示**：run_rl_intent 训练器的 post_weights 仍有 60–300s 死节点超时（每轮一次），
靠"配置离线"绕过；如需训练器也 auto-dist 可复用本套 ping-first 激活。

---

## §5 训练器 ping-first 并行激活（2026-08-29，§4 的移植）

用户指令：把 m1-eval 的 ping-first 激活搬训练器去。核对发现训练器的 ① ping 门
本就是 3s 短超时（无 60–300s 问题——那是我此前未读代码的错误推断，已在 §4 的
"遗留提示"语境更正），真正的浪费是**串行**：7 死节点 × 3s = 每轮 ~23s。

**修复**（rl/queue.py ① ping 门）：`ThreadPoolExecutor` 并行 probe 全部启用节点，
判定与日志按配置顺序串行回放（保序、线程安全、upgrade 请求仍在串行段）。
实测（真实死节点 7 台）：**23.1s → 5.3s/轮**（残差 = 不可达主机 TCP SYN 的固有超时）。
节点中途上线的接管语义不变：下一轮迭代的 ping 门纳入（与 rollout 既有行为一致）。

**勘误**：§4 "遗留提示"里"run_rl_intent post_weights 仍有 60–300s 死节点超时"表述
不准确——post_weights 只对 ping 通过的节点执行；真实成本是串行 ping 的 ~23s/轮，
本节已修。

---

## §6 rollout 机制 → m1-eval 的能力提取（2026-08-29，commit 4d21d9b）

用户指令：检查 rollout 的巡航报告 / 断点续跑 / 本地优先 / 尾部 fan-out 竞速是否适用于
m1-eval，适用的提取为公共能力、两边复用。

### 适用性判定

| 机制 | 适用 | 处置 |
|---|---|---|
| 断点续跑（rl/resume.py completed_pairs + wver 过滤） | ✅ 高价值——此前崩一批全丢 | `tools/lib/batch-ledger.ts`：逐局 jsonl 账本，(stage,seed)+wver 记账，后写覆盖先读（错误重跑审计留痕） |
| 尾部 fan-out 竞速（queue.py v3.7 tail_fanout_n/dup + duplicate-settled suppression） | ✅ 尾部时延从 max 变 min | `tools/lib/hybrid-batch.ts` `TailRaceBatch`：共享游标 + 竞速（每任务副本 ≤ dup、first-settle-wins 幂等）+ 无消费者守护 |
| 错误局自动重跑（run_rl_intent CLEAN_EVAL_MAX_RETRY） | ✅ 瞬态 503 不再污染整批 | main 重试循环：错误局最多再跑 2 次，账本追加审计行 |
| 巡航报告（training_log.jsonl + 巡检 HTML） | ✅ 部分提取 | `<out>.partial.json` 25/50/75% 里程碑快照 + 全量 jsonl 账本；终局 HTML 评分卡不变 |
| 本地优先（local_slots/local_suspend） | ❌ 不适用 | eval 无 PPO 抢核问题；共享游标本就公平调度。Python 侧语义保留在 queue.py |

### 复用关系

- **m1-eval**（本次接线）：runHybrid 两类消费循环 claim/settle 走 TailRaceBatch；
  main 分 tasks→(ledger-done ∪ todo)，todo 跑完对 ok=false 局重跑 ≤2 次。
- **rollout（python）**：queue.py/resume.py 机制已完备不动；trainer 的 clean-eval
  经 `m1-eval --dist-nodes` 间接继承本套能力。
- **未来 TS 批工具**（export-*-rollout 等）可直接复用两个 lib 模块。

### 实测

- **断点续跑**：同一命令双跑——第二次 `ledger resume: 6/6 already settled — running 0`，
  0.16s 完成，评分卡由账本重建（结果与首跑一致）。
- **单测 9 项**：游标顺序 / 幂等结算 / 竞速进入条件与副本上限 / failUnsettled /
  账本往返 / wver 过滤 / 重跑覆盖。全量 check 1641 tests 绿。

### 语义细节（实现期澄清）

- 竞速的进入条件 = 游标耗尽 ∧ remaining ≤ fanoutN ∧ 有在跑副本；游标发号时每个任务
  已带 1 个在跑副本，竞速将其补到 dup 上限后轮到下一未结算任务（单测 3 记录了完整序列）。
- wver 覆盖"影响结果的输入"：goal/intent 权重字节、god/goal-god 占位；nn 策略走
  `local-nn` 键（纯本地无权重文件）。代码变更需 `--fresh` 忽略账本。

---

## §2 T7.2 goal PPO 基建 + T6 反事实标注（2026-08-29，commits b04f4d6/f74a7cb + pilot）

### T7.2（全绿）

- `nn-training/ppo_goal.py`：GoalRLNet + **双动作空间**（fine 676 / coarse 169 块 logsumexp，
  §T9a.1b）+ multi-head loss（surrogate_clip 主项 + 可选 BC kickstart + engage 辅助 CE +
  value MSE + 熵 + KL 锚）+ ppo_common 变步长 GAE + value warmup + stream backend 别名。
- `tools/sim/export-goal-rollout.ts`：goalPick 回调式采集器（与执行器共享 L2/L3 代码路径）；
  §12.3 奖励 = R_event（继承 INTENT_REWARD 量级）+ 到达 1.0 + 守家 0.5（γ^dt telescoping）
  + 交战效率 0.3；shard 含 goal_mask（u1）/dt/inject/engage，manifest 记 firePolicy（§11.3.1）。
- rl/ goal_rollout 分支：queue 采集命令 ×2 + wkind='goal'，stream semi-MDP 波次语义，
  sampler-agent kind=goal 桶 + heartbeat/goalCoarse URL 参数，`run_rl_intent.py --goal` 开关
  （同一主循环/熔断/巡检/断点续跑复用）。
- `test_ppo_goal.py`：dt 退化字节一致、coarse logsumexp 单调/可导、双空间掩码 logp
  （被 mask 动作 < −1e8）、微型 shard 冒烟 + warmup 冻结断言。
- **采样/训练一致性修复**：coarse 块 logit 两侧统一为"全 4 格 logsumexp + 块级有效性过滤"
  （采集器原按可达格聚合，importance ratio 会偏）。
- 前向实测 **~64ms**（h=64，含间隔 sim），比 §11.9.1 保守口径（110–170ms）快 ~4.5×
  ⇒ hb240 on-policy 单局 ≈1.1s，140 局/轮单核 ≈2.6 min。

### T6（全绿 + pilot 已跑）

- `tools/sim/export-counterfactual-goals.ts`：God-AI 状态分布 + 候选生成（§11.2 六来源 +
  §11.4 确定性 top-K + 来源标记）+ cloneWorld 分支 rollout（§T6.1b，每分支一次克隆）+
  **多窗口检查点打分**（一次 480-tick 分支产出 {60,120,240,480} 四档分数，4× 省时）+
  inject 自馈流（prevGoal = 上决策 argmax）+ cand_src 来源标记 + engage 逐窗口。
- `tools/sim/cf-goal-worker.ts` + WorkerPool：并行 == 串行**逐字节一致**（哈希对账）。
- `tools/sim/cf-hsweep-report.ts`：§11.8 三判据判读。
- `nn-training/train_goal_bc.py`：软目标 + 全 676 维稀疏 CE（λ/τ 训练超参，shard 存原始
  (s_i,k_i)）+ engage CE + **长承诺样本加权**（§8.1.1 a1 缓解#2）。

### Pilot（350 局 = 35 关 × 10 seed，replan30×210 + replan240×140，K=12）

吞吐：**2.06 s/局**（8 workers；含四档窗口分支）⇒ 350 局 ≈ 12 min，符合 T6 验收口径
（≤15 min@6 节点折算）。语料 383MB / 39,663 决策点 / 覆盖率 0.981。

**§11.8 H 扫描定案**（argmax 落点占比，λ=0.5）：

| window | enemyRear(追尾) | anchor(守家) | carve/brick | godTarget 重合 |
|---|---|---|---|---|
| 60 | 8.9% | 0.6% | 1.0% | 72.8% |
| 120 | 21.5% | 1.5% | 2.9% | 56.6% |
| **240** | **39.1%** | 2.9% | 5.7% | 36.9% |
| 480 | 52.7% | 3.5% | 10.5% | 22.9% |

长窗口系统性恢复追尾行为（§11.8 "短窗近视"论断实证成立）；480 超 §11.7 上限 ⇒
**操作点 H = T = 240**。

**§8.1.1 检查⑤（duration 覆盖）诚实记录**：按决策点算长承诺（≥0.5）占 ~12.8%
（replan240 局决策点少 ⇒ 局数占比 40% ≠ 点数占比），低于 50% 目标。缓解：BC 长样本
加权 ×3.0 + PPO on-policy 在 hb240 自行覆盖部署分布。记为 T9a 已知风险。

### 实现期新决策（续 §1）

9. **H 扫描四档共用一个 max-H 分支**：分支在检查点打分，RNG 连续性保证与独立跑一致
   （省 4× 机时；代价是 cand_s 按窗口分文件）。
10. **CF 语料 replan 混合**：210 局 replan=30 + 140 局 replan=240（局数口径 40% 长承诺，
    对齐 a1 缓解#1 的"≥1/3"精神；点数口径不足部分由加权补偿）。
11. **T4 依赖以 L3-min 替代**：T6 的 rollout 开火 = FireControl 原样（§T6.1a 钉死的
    "现有 L3 规则"），未做 T4 的 lateralFire 扫参——T6 依赖修正记账，T5（开火 canary）
    暂缓不影响本轨。

---

---

## §1 网络轨落地：T7 → T8-min → reach-mask → T8.5（2026-08-29，commit 9be15d2）

全部按手册规格实现，`bun run check` 全绿。文件与验收：

| 卡 | 交付 | 验收 |
|---|---|---|
| T7 | `nn-training/goal_net.py`（GoalNet：goal_conv 1×1 on bufA + engage 137→2 + value 137→1）；`src/nn/infer.ts` `goalForward()` + `buildGoalModelFromJson`；`src/nn/goal-inject.ts` §8.1.1 语义表 | `tests/nn/goal-infer.test.ts`：goal/engage/value 三头 TS/Py 一致（热图 1e-3 / 标量 1e-4）；inject 语义 + 保留维恒 0 断言 |
| T8-min | `src/nn/goal-contract.ts`：E1/E3/E5/E4 定序评估 + travelEst≤T 校验 + 默认 premise（baseIntact/playerOperable/stageInProgress + 归因 label） | `tests/nn/goal-contract.test.ts`：三谓词触发/不误触发、E3 动态占位不触发（§6.6 防抖）、E4/E5 边界、E3>E1 定序 |
| T3 子件 | `src/ai/goal/reach-mask.ts`：ReachMasker 池化 Dijkstra（k 砖代价字典序、51 越界格硬遮、revision+start 备忘、零堆分配）+ `selectGoal` 平局取低索引 | `tests/nn/reach-mask.test.ts`：findPath 交叉验证（k=0 ⟺ walk / k<∞ ⟺ carve）、确定性、GC 断言（1000 次 <1KB）、平局防漂移 |
| T8.5 | `src/nn/goal-executor.ts`（L0 dodge 硬约束借 god reflex `_lastBranch==='dodge'`、L1 argmax+top-K 可满足、L2 路径跟随 walk→carve、L3 FireControl + 凿墙开火、有序回退、重选冷却防抖）+ `--policy goal` 全链接线（sim-worker/simulation-runner/export-eval-game/m1-eval dist 白名单/sampler-agent kind=goal） | `tests/nn/goal-executor.test.ts`：同 seed 双跑逐字节一致、E4 心跳间隔= promiseTicks、E3 事件 3 tick 内触发、reset 清态；m1-eval 6 局冒烟出同构报告 |

### 实现期新决策（偏离/澄清手册，均已在代码注释标记）

1. **T7 TS 侧保留 intent 头加载能力**（手册写"删 enemyLogits/anchorLogits"）：§14.3 要求在新代码上重评 it38（判定"不劣"），intent 路径必须可加载；GoalNet（Py）与 goal 权重 JSON 不含 intent 头——"删"落在**新网络定义**上，TS 面向后兼容。与 §9.2.1"旁路不删除"同哲学。
2. **热图头 golden 容差 1e-3**（§T7.3 预案触发）：TS mul+add 与 torch gemm FMA 的固有舍入差实测 1.068e-4（>1e-4 且 <1e-3）；engage/value 保持 1e-4。
3. **reach-mask 的 k 定义**：Dijkstra 字典序最小化 (k, steps)，k = 路径上必须凿毁的砖数（含目标足印内的）；2×2 前缘两格都要凿 ⇒ 穿 1 格厚墙 k=2（写进单测）。λ 不进 ReachMasker 缓存键，mask(λ) 只重着色。
4. **可满足性校验的落法**（§6.1.1 "travelEst ≤ T 否则拒绝重选"）：top-K（K=6）按 heat+mask 降序找第一个 travelEst ≤ T 的格；全不可满足 ⇒ 强制提交 argmax（有目标优于站着不动，telemetry 记 'unsat'）。travelEst = carve-aware A* 步数 × 23 + 8×k。
5. **E4 同格续约语义**：bornTick 重置（承诺期重新起算，防 E4 逐 tick 抖动——实测 481 次 reselect 的教训）+ `pursueSince` 独立累计（inject duration 连续增长，不因心跳确认清零）+ dodgeTicks 重置（"自上次重承诺起"口径）。
6. **重选失败冷却 30 tick**：全遮/无效起点导致提交失败时不再逐 tick 重前向（实测密封场景 2709 次 reselect/8.4s 的教训）。
7. **E3 判据的 executor 侧实现**：mask 按 tileMap.revision 变化时重算（<1ms），E3 = 缓存 mask 在契约格 ≡ −Infinity；配合 §6.6"动态占位不触发"。
8. **硬遮哨兵**：kArr 用 Uint16Array(65535=不可达)，mask 特判 k=0（避免 `-0`，Object.is 语义）。

### 性能实测（本机，修正 §16.1 的 110–170ms 推理口径）

probe：h=64/d=8 goal 前向 + sim tick 间隔计 **~64ms/reselect**（含间隔内 sim）——远低于手册引用的 110–170ms。前向便宜 ⇒ hb240 的 on-policy rollout 单局 ≈1.1s（22 前向 × ~40ms + 0.2s sim），140 局/轮单核 ≈2.6 min，比 §11.9.1 的保守估计（11.9 min）快 ~4.5×。

---

## §0 基线重钉（2026-08-29，T9.0/G1 前置）

God-AI 自手册基线（75.86%，commit 16fc76a 口径）后有两个行为纪元：
`f4dbc0b` super-item 恢复（DECISIONS §293 三件套已跑）+ `97c3447`/`03a25a4` pursuit-tail
（SS302/SS303）。手册 §1 已声明旧 pinned 作废 ⇒ 重跑。

```
bun tools/sim/m1-eval.ts --stages all --seeds 1-60 --difficulty hard --policy god \
  --out reports/godai-baseline-hard-35x60.html     # 2.2 min @7 workers
```

| 项 | 值 |
|---|---|
| **胜率（新 pinned）** | **78.81%**（1655 通关 / 2100），SE 0.89pp，95% CI [77.07%, 80.55%] |
| scoreV7 | suite **0.6001** · lcb 0.594 |
| 最差 5 关 | Battlement 26.7 · Riverbed 41.7 · Bastion 58.3 · Labyrinth 63.3 · Thicket 65.0 |
| 最好 3 关 | Ramparts 100 · Gridlock 95.0 · Fortress 93.3 |
| 产物 | `reports/godai-baseline-hard-35x60.{html,log}` |

**对 T9 门的含义**：主门 = 对本新基线的配对差 ≥2pp 且 CI 下界 > 0。
God-AI 变强 ⇒ 剩余可改善空间被压缩（78.8% 之上每 +1pp 都更难）；
T9a canary 判定保持"方向为正"口径不变（§0.3.1）。
事后注：m1-eval 未单独落 `.json`（计划 §0.2 ③ 的 archetype-report `--report` 输入
需另想办法或给 m1-eval 补 `--json` 出口，T6 前处理）。

### 派工口径（与手册 §0.3.2 依赖图的差异，自主决策）

用户指令聚焦**目标策略 NN 的开发与训练** ⇒ 走**网络轨 + 数据轨 + 训练轨**：
`T7 → T8-min → reach-mask(T3 子件) → T8.5 → T7.2 → T6-pilot → T9a → T9`。
**暂缓**：T2/T4/T5（执行层轨 / 开火 canary —— 会改 God-AI 行为、触发新纪元三件套，
且 T9 卡明定 `fire_head` warm start 缺席时用随机初始化为已记录回退路径）；
T6-生产（2100 局标注按 §9.4.3 须等 T3 全卡，本轮用 T6-pilot 350 局喂 T9a，T9 语料视 canary 结果再定）。
reach-mask 虽记在 T3 卡下，但它是 T7.2/T8.5 的消费件且规格自足（G8 + 评审 a2 池化规格），
按规格独立实现，**不动 God-AI 任何默认参数**（不触发新纪元）。
