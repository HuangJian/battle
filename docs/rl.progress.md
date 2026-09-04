# RL Training — Progress Log

> RL 阶段（BC 热启动之后的 PPO/课程化训练）的全部进展、评估结果与教训记录于此。
> 新条目置顶（倒序编号，`## §N`）；BC/蒸馏侧历史见 `docs/nn.progress.md` 与
> `docs/goal-nn.progress.md`；God-AI 调参侧见 `docs/god-ai-tuning.progress.md`。

---

## §2 p4-onset 四面围攻：BC ep60 vs God-AI（2026-09-04，DECISIONS §327）

> p1（单敌）已被 BC 蒸馏饱和（§1：ep60 94/100 ≈ 教师 92/100）后课程升级：4 敌混编
> （basic/fast/power/armor 各 1）、player 正中、敌人四角，其它设置与 p1 全同
> （hard / 1 命 0 星 / 2400 ticks / 同一 reward）。本节的读数=BC 泛化能力的体检。

### 2.1 结果（各 100 局，同一工具/口径）

| 指标 | BC ep60 | God-AI |
|---|---|---|
| 胜（stage_clear） | **14/100** | **64/100** |
| 全歼 4 敌（含压线超时） | 18 | 64 |
| 总击杀 | 193（1.93/局） | 306（3.06/局） |
| 被击中（致死） | 75 | 34 |
| 承伤 | 19460 | 15140 |
| 开火 | 1021 | 1500 |
| 平均用时（全局） | 1280 | 1187 |
| max_ticks 超时 | 12 | 2 |
| gameover | 74 | 34 |

击杀分布（ep60 / god）：0 杀 18 / 9 · 1 杀 23 / 11 · 2 杀 25 / 9 · 3 杀 16 / 7 ·
4 杀 18 / **64**。胜局平均用时：ep60 1736（紧贴上限）、god 1340。

### 2.2 判读

1. **BC 的 p1 达标不迁移**：单敌 94% → 4 敌 14%，教师同场景只掉到 64%。p1 上学的
   「发现→瞄准→击杀 1 个目标」没有泛化成多目标交战/四向受敌的生存能力——学生每局
   能打死 ~1.9 个敌人（火力在学），但 74/100 局在清场前先被打死。**p4 正是 RL 的
   目标课程**：教师-学生差距 ~50pp，远大于 p1 的平手。
2. **教师同样吃力但明显更稳**：God-AI 64%（每局 3.06 杀），且 4 杀=64 局全部真正
   清场（无压线超时）——教师的多目标交战能力是结构性的，不是 2400 tick 预算的
   产物。
3. **cap 语义**：2400 ticks 对 4 敌偏紧但非主因——ep60 仅 12 超时（其中 4 局在
   tick 2400 全歼敌人但 stage-clear 未及登记，cap 压线），God 仅 2。真正杀手是
   死亡（ep60 74 局 gameover）。
4. **并行==串行复验**：p4-onset 上 god 100 局 15-worker vs 1-worker 行级 diff 逐字节
   一致（新课程无几何 seed 依赖，仅模拟 RNG 流变化）。

### 2.3 对 RL 的意义

- p4-onset.jsonc 即下一阶段课程（RL 超参已预置：γ 0.995 / λ 0.97 / ppo_schedule /
  eval_stages 2000-2000）。BC 热启动仍可用——它不是「完成了 p1 就毕业」，而是
  「p1 达成、p4 留了 ~50pp 给 RL 吃」；从 ep60 起步在 p4 上跑 PPO 是自然路径。
- 复跑：`bun tools/sim/eval-course-ckpt.ts --course nn-training/curricula/p4-onset.jsonc
  --weights tmp/ep60/battle2-p1bc/run/weights.json.ckpt.60 --games 100`（`--policy god`
  同课程即可得教师基线）。行数据 tmp/p4-ep60-rows.jsonl / tmp/p4-god-rows.jsonl。

---

## §1 p1-onset 教师-学生基线：BC ep60 vs God-AI（2026-09-04）

> RL 启动前的对照锚点。60 epoch 全量 BC 学生（Colab T4，165K 帧 p1-godai-v2 语料）
> 与它的教师 God-AI 在同课程上各跑 100 局贪心评估——**学生已追平教师**
> （94/100 vs 92/100，统计同水平），BC 热启动质量足够进入 RL 微调。

### 1.1 训练侧（BC 60 epoch 全量，DECISIONS §325）

- 语料：`tmp/p1-godai-v2`（p1-godai 1892 胜局 / 165K 帧，含 returns.npy）。
- 命令：`train/bc.py --data-dir ... --arch student --value-coef 0.5 --epochs 60
  --ckpt-every 1 --batch 256 --lr 0.003 --val-split 0.1 --mirror-p 0.5
  --seed 1234 --device cuda`（Colab T4，~82 s/epoch，总计 4911 s）。
- 收敛：val_loss 1.108 → **0.5084**（ep60 = best，全程无过拟合平台）；move acc
  0.667 → 0.862；fire acc 0.882 → 0.929；cosine(60) lr 精确归零。中途波动
  （ep14/26/32/50 回弹）均为单轮噪声，次轮恢复。
- 训练-评估跟踪：ep30（val 0.5991）→ 86/100；ep60（val 0.5084）→ 94/100——
  cosine 尾段（lr 1.89e-3→0）的增益几乎全部落在 power 关（14/25 → 20/25，
  死亡 11 → 5），basic 25/25 补齐，fast/armor 已达天花板。

### 1.2 评估协议（同一工具/口径，DECISIONS §326）

- 课程 `nn-training/curricula/p1-onset.jsonc`：stages 2000-2003（basic/fast/power/
  armor 各 1 敌，8 布局 spawn_variants），hard / 1 命 / 0 星 / max_ticks 2400。
- 100 局 = 4 关 × seeds 0–24（seed_rotate 50），贪心部署口径
  （`export-eval-game.runEvalOne`，掩码 argmax，无探索）。
- 工具：`tools/sim/eval-course-ckpt.ts`（多权重 × N 局，round-robin 分片，
  并行 == 串行，JSONL + 汇总表）。本次为对比新增 `--policy god`：
  走 runEvalOne 的 god 分支（真 GodAIInput + DEFAULT_GOD_AI_PARAMS，RNG 派生
  `seed ^ 0x9e3779b9` 与 simulation-runner 逐字节一致，无需 --weights）。

### 1.3 结果：BC ep60 vs God-AI，各 100 局

| 关卡 | ep60 胜 | God 胜 | kills (ep60/god) | 被击中 (ep60/god) | 承伤 (ep60/god) | 开火 (ep60/god) | 平均用时 (ep60/god) |
|---|---|---|---|---|---|---|---|
| basic | **25/25** | 24/25 | 25 / 24 | 0 / 1 | 2800 / 2200 | 126 / 135 | 680 / 539 |
| fast | 25/25 | 25/25 | 25 / 25 | 0 / 0 | 1872 / 1080 | 86 / 105 | 534 / 481 |
| power | **20/25** | 18/25 | 20 / 18 | 5 / 7 | 2960 / 1628 | 88 / 75 | 460 / 363 |
| armor | 24/25 | **25/25** | 24 / 25 | 1 / 0 | 2236 / 1720 | 230 / 158 | 790 / 757 |
| **合计** | **94/100** | **92/100** | 94 / 92 | 6 / 8 | 9868 / 6628 | 458 / 545 | 616 / 535 |

### 1.4 判读

1. **学生追平教师**：94 vs 92，100 局样本噪声（±~5%）内同一水平；两侧失败簇
   完全一致——都集中在 power 关（ep60 死 5、god 死 7，其余关接近满胜）。对比
   起点（3-epoch smoke 0/50 胜、从不命中），60 轮 BC 完成了从"不会开枪"到
   "教师水平"的整段爬升。
2. **风格差异**：God-AI 更"会玩"——承伤少 1/3（6628 vs 9868）、清关更快
   （535 vs 616 ticks）、armor 关用更多火力（230 vs 158 发）换更高胜率；
   BC 学生更省弹药（458 vs 545 发）但多挨打。power 关上学生反而略优
   （20 vs 18）——BC 不是教师的劣化复制，局部已超教师。
3. **机制验证**：`--policy god` 15-worker 并行与 1-worker 串行 100 局行级
   `diff` 逐字节一致（determinism 契约成立）；God-AI 100 局仅 0.8 s
   （1 敌空旷场 think 便宜），远快于 ep60 的 9.7 s——教师基线可以任意加量。

### 1.5 复跑与数据

- 行数据：`tmp/ep60/eval-rows.jsonl`（BC）、`tmp/p1-god-rows.jsonl`（God，
  与 `tmp/p1-god-serial.jsonl` 逐字节相同）。
- 复跑命令：
  ```bash
  bun tools/sim/eval-course-ckpt.ts --course nn-training/curricula/p1-onset.jsonc \
      --weights tmp/ep60/battle2-p1bc/run/weights.json.ckpt.60 --games 100
  bun tools/sim/eval-course-ckpt.ts --course nn-training/curricula/p1-onset.jsonc \
      --policy god --games 100
  ```

### 1.6 对 RL 阶段的意义

- **起点基线**：ep60 权重（val 0.5084）即 RL 热启动权重；教师 92%、学生 94%
  意味着课程"入门"目标已被 BC 单方面完成——RL 的边际课题应从 power 关失败簇
  与风格收敛（换伤 vs 规避）开始，而非从零学击杀。
- **基础设施已备**：GPU 训练（DECISIONS §325）、课程 checkpoint 评估
  （DECISIONS §326）、Colab 全流程 notebook（`battle.ipynb`，含
  `--device cuda` 与断点 resume）、本机 8 物理核 rollout/eval 并行
  （worker-pool 物理核上限）。
- **规划中的 RL 架构**：本机局域网 rollout 集群采集 → 云端（Colab/Kaggle）PPO
  更新（mailbox 轮询式搬运，非实时推送——云端无法入站连接；Colab T4 2 vCPU
  不适合 rollout，见 plan/python-env-bootstrap-and-device.md 相关讨论）。