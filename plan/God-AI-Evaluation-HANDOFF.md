# God-AI 评估标准 — 后继 Agent 交接文档

> 用途：把一个**已完成核心设计、但尚待继续打磨**的评估标准，交接给另一个 agent 接手优化，
> 用于后继的高阶 AI 调参（把评估标准当作 God AI 的优化目标 / fitness）。
> 本文件可直接作为 prompt 粘贴给下一个 agent（见末尾「可直接粘贴的 prompt」一节）。

---

## 0. TL;DR

Battle City Web 的 God AI 目前用 `tools/optimize-godai.ts`（sep-CMA-ES）调 17 个策略参数。
它需要一个**评分标准**当 fitness。我们已经有一套 v6 连续评分标准（`tools/godai-score.ts` +
`tools/eval-suite.ts`），它比纯胜率更能区分「打得好但没赢」和「赢得丑」。

**当前状态**：v6 设计、标定、公理测试都已完成，但 §7.5 提升决策结论是**暂不把 v6 提为默认**
（`--fitness v5` 仍是默认，v6 维持 opt-in）。原因是 v6 冠军在 35×60 胜率口径（86%）低于 incumbent
DEFAULT（87%），且个别硬关显著回归。

**你的任务**：优化这个评估标准本身（权重 / 公理 / 维度 / 标定），目标是——优化器对着你的新标准
调出的参数，在 35×60 胜率上**不劣于** DEFAULT，同时保留 v6「在胜率看不出区别时仍能分辨好坏」的判别力。

---

## 1. 必读文件（按顺序）

1. `plan/God-AI-Evaluation-Redesign.md` —— **主设计文档**。§1–§8 设计，§9 标定实测，§10 提升决策（含完整 A/B 数据与「不提升」结论）。**先读这个**，否则会重复踩坑。
2. `AGENTS.md` —— 仓库契约。重点 §2（架构不变量）、§2.3（确定性）、§4（执行计划流程）。
3. `tools/godai-score.ts` —— v6 评分管线（L1–L4）、`DEFAULT_LOSS_WEIGHTS` / `DEFAULT_WIN_WEIGHTS`、11 个维度定义、A1–A6 公理。
4. `tools/eval-suite.ts` —— 记分卡、`--calibrate`（分组 5 折 CV AUC + 收缩）、`--compare`（CRN 配对 A/B + 逐关分解）、`fitLossWeights` / `groupedCvAuc` / `shrinkToPrior`。
5. `tools/optimize-godai.ts` —— CMA-ES 优化器；`--fitness v5|v6`、`--opt-seed`、17 维搜索空间。
6. `tools/eval-refs.json` —— 35 关参考值 + `lossWeightFit`（cvAuc 0.780、收缩后权重）。
7. `tests/godai-score.test.ts` —— 31 条公理测试。**改任何评分逻辑后必须全绿。**
8. `.workbuddy/promotion/ab-defaultv6.txt` 与 `ab-v5v6.txt` —— 上次提升决策的原始 A/B 输出，看逐关回归细节。

---

## 2. 当前状态（已知事实）

- v6 管线：4 层（L1 维度归一 → L2 分档合成：失败档 [0,0.55]、通关档 [0.60,1.0]，档间 gap 保证 A1 → L3 逐关 seed CVaR → L4 跨关调和均值 p=−1 + LCB）。
- 标定：失败档权重从**中局检查点**回归，分组 5 折 CV AUC = **0.780**（>0.65 采纳线），向先验收缩 λ=0.56。
- 判别力：35 关里 28 关胜率完全并列；v6 在并列组内仍有 ~4× 标准误的区分力。
- §7.5 结论：**不提升 v6 为默认**。v6 冠军 35×60 胜率 86% vs DEFAULT 87%；逐关 3 显著回归（Checkers −14.5pp、Iron Curtain −7.0pp、Twin Spires −5.6pp）对 1 改善（Waterways +4.6pp）。
- `tools/` **不在** `tsconfig` 的 include 内，`bun run check` 覆盖不到（见 §4）。

---

## 3. 仓库与运行约束（必读，否则会卡死或踩雷）

### 3.1 架构不变量（AGENTS.md §2）
- **只有 `Simulation` 能改 `World`**；评分/优化工具在 `tools/`，是分析层，可自由读写文件，但不要在 `src/` 的模拟层里引入非确定性。
- **确定性承诺**（§2.3）：模拟层所有随机走 `world.rng`，禁止 `Math.random()`。**优化器的搜索随机必须种子化**（已用 `src/utils/RNG` + `--opt-seed`），否则两次调优结果不可比。
- 改动坦克数值 / AI 行为后必须重跑 `--calibrate`（参考值会失真导致维度饱和）。

### 3.2 tools/ 类型检查
`bun run check` 的 `tsc` 已覆盖 `tools/`（`tsconfig.json` 的 `include` 含 `tools`），改 `tools/*.ts` 后正常跑 `bun run check` 即可，无需再单独跑 `npx tsc`。

### 3.3 沙箱运行硬约束（重要！）
- **本沙箱在会话/turn 之间回收所有子进程**，包括 `setsid nohup` detached 的进程。后台 `run_in_background` 任务也会在会话结束被回收。
- **单次 foreground Bash 调用上限约 10 分钟**。完整 `35×20×30` CMA-ES ≈ 50 分钟/fitness，跑不完。
- **可行做法**：用**顺序 foreground** 调用，预算 `35×14×7`（≈8.4 分钟/fitness）；v5 与 v6 用**同一 `--opt-seed`** 才是对照实验。
- **输出写持久目录**（`.workbuddy/...`，不是 `/tmp`；`/tmp` 跨会话可能丢）。

---

## 4. 绝不能重复的统计陷阱（我们已踩过）

1. **标签泄漏**：`progress = kills/enemyTotal`，通关时恒为 1.0。用「终局维度」拟合 `P(通关)` 会得到 99.9% 假准确率。权重**只能从中局检查点**回归。
2. **类不平衡**：通关基础率 ~90%，准确率毫无意义。用 **AUC（Mann–Whitney U / 分组 CV）**，判据 ≥0.65 才采纳。
3. **分组泄漏**：同一局贡献 3 个检查点、共享同一标签。必须按 `stage×seed` **分组**做 k-fold，绝不能按行切。
4. **收缩而非替换**：原始逻辑回归会把 `tempo`/`openingTempo`/`loot` 权重压到 0（无梯度）。用 `shrinkToPrior`（λ=clamp(2(cvAuc−0.5),0,0.8)）保留非零地板。
5. **维度饱和**：`tempo` 曾因 `kpmRef` 太低恒为 1.0（有效权重 0）。用 `--weights` 的 stdev 列发现；标定时已修（kpmRef 15–29）。

---

## 5. 开放研究方向（「优化评估标准」具体可做啥）

按性价比排序：

1. **对齐胜率（最高优先，直接解决 §10 回归）**
   - v6「输得好看」塑形稀释纯胜率，是 §10 回归的根因。
   - 方向 A：把胜率信号重新并入损失档（如 `lives`/`baseIntegrity` 提权，或加一个「通关优先」硬约束）。
   - 方向 B：**多目标** fitness = `0.7*winRate + 0.3*v6Quality`，让冠军在胜率与质量间权衡，闭环 §10 的「3 回归/1 改善」问题。
   - 验收：对着新标准调出的冠军，35×60 胜率 ≥ DEFAULT(87%)，且逐关无显著净回归。

2. **维度修剪 / 扩充**
   - `tempo`/`openingTempo`/`loot` 在标定中是冗余 / 被压制（solo AUC 低、与 `progress` 高相关）。用 `--weights` 确认后：删，或重推导成更独立的信号。
   - 可加维度：**道具效率**（拾取率×战力增益）、**通关用时塑形**（快通关加分）、**基地经济压力**（敌人逼近基地的缓解度）。

3. **胜率天花板问题**
   - 28/35 关默认参数下 `win=1.00`，指标区分不出强参数。可加**子胜率区分**（净胜幅度、用时、剩余资源）或引入 harder 参考 seed 拉开区分度。

4. **代内逐次减半（successive-halving）**
   - 接进 `optimize-godai.ts` 的代内评估，省算力。**这是独立 TODO，与提升决策无关**，可不阻塞前三项。

---

## 6. 工作流与命令

```bash
# 0) 类型/测试门禁
bun run check                                   # src+tests+tools 全绿
bun test tests/godai-score.test.ts              # 31 条公理测试

# 1) 记分卡（看维度分解 / 名义 vs 有效权重）
bun tools/eval-suite.ts --seeds 60 --dims --weights

# 2) 改了坦克/AI/评分逻辑后重标定
bun tools/eval-suite.ts --calibrate --seeds 30   # 写 tools/eval-refs.json

# 3) A/B 两个参数文件（CRN 配对 + 逐关分解）
bun tools/eval-suite.ts --compare a.json b.json --seeds 60

# 4) 优化（受控：同 seed，仅 fitness 不同；预算适配沙箱 ≤10min）
STAGES=$(python3 -c "print(','.join(map(str,range(35))))")
bun tools/optimize-godai.ts --fitness v6 --stages "$STAGES" \
  --seeds 14 --generations 7 --opt-seed 7 --output .workbuddy/promotion/opt-v6

# 5) 提升判据（§7.3）：新标准冠军的 35×60 胜率 不劣于 DEFAULT
#    DEFAULT 参数 = tools/ 里 DEFAULT_GOD_AI_PARAMS（v5 事实最优，87%）
```

十七维搜索空间（God AI 参数，非评分参数，但优化器会动它们）：
`reactionDelay, aimError, suboptimalPathProb, defenseRowOffset, defenseColSpread,
threatRangeCells, maxPlayerDistFromBase, t8MaxInterceptDistCells, baseWallScanRadius,
replanInterval, powerupMaxDivertDistance, endgameEnemyThreshold, huntAllyCount,
baseRaceRangeCells, baseRaceMarginCells, outnumberedEnemyCount, outnumberedRadiusCells`

---

## 7. 后继 Agent 的 DoD（完成定义）

- [ ] 评分逻辑改动后 `bun run check` + 31 条公理测试全绿；`tools/` 显式 tsc 无错。
- [ ] `--calibrate` 重跑，分组 CV AUC ≥ 0.65；维度无饱和（`--weights` stdev 正常）。
- [ ] 对着新标准调出的冠军，35×60 胜率 **≥ DEFAULT(87%)**，且 `--compare` 逐关分解**无显著净回归**（回归关数 ≤ 改善关数，且最弱关不塌）。
- [ ] A/B 结论落到 `plan/God-AI-Evaluation-Redesign.md`（新 §11），并更新 `.workbuddy/promotion/` 产物。
- [ ] 若证明 v6（或新标准）胜率不劣于默认，按 §7.3 给出「提为默认 / 仍 opt-in」的明确建议。
