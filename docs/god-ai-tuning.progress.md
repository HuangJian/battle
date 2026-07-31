# God AI 调校进展总览（系统化整理）

> 汇编自：git commit history、历史日志、各阶段计划/验证文档、`.workbuddy/memory/`（每日工作日志）。
> 整理日期：2026-07-30。此文档为**只读汇总**。DECISIONS.md 已精简为索引（2026-07-31），
> 新决策仍以 DECISIONS.md 为准，详细内容以本文档为准。

---

## 0. 当前状态速览（截至 2026-07-30 §47 修复定稿）

| 指标 | 数值 | 口径 |
|---|---|---|
| 全 35 关真值均值 | **87.7%**（前值 81.9%） | 35 关 × 60 seeds, classic, 18000t（§47 后正式重测） |
| 低于 60% floor 的关卡数 | **0 / 35** | §47 修复后所有关均达标 |
| 最弱关 S32 Diamond | **85.0%** @120（90.0% @60） | §47 定稿，**> 80% 目标达成**（base=6 lives=12） |
| 质量门禁 | **541 测试全绿** | tsc + oxlint + oxfmt clean |
| 回归门禁 | 全 35 关 × 20 seeds，真值已按 §47 后 60-seed 重生成，聚合 floor 77%→83% | `tests/god-ai-regression-gate.test.ts` |

> §48（假闪避修复）实测为 **-10pp 回归已回退**：地形盲闪避是承重行为（详见 §4 与 DECISIONS §48）。
> parity 8 seeds 保持 pre-§48 基线（§47 仿真层修复对 parity 关卡 S0 无影响）。

**演进主线**：基础设施 → classic 适配 → 死锁修复（P0–P3）→ 全关战役（P4）→ 单关攻坚（Round 5）→ 智能威胁模型（Phase A，负结果）→ **§47 基地保护环碰撞修复（真正的 S32 破局点）+ §48 假闪避"修复"否决**。

---

## 1. 目标与评价体系

- **最终目标（P4 用户指令）**：全 35 classic 关，逐关过关率稳定 > 60%（floor），且平均 > 80%。✅ 已达成并超出（86.9%）。
- **延伸目标（Round 5）**：S32 Diamond > 80% @120 seeds。✅ **85.0% 已达成**——但靠的不是 God AI 策略，而是 §47 仿真层碰撞修复（原"结构性差距"实为碰撞语义 bug + 归因污染，见 §4）。
- **测量纪律**：20-seed 探针有 ±11pp 二项噪声，只用于筛选方向；**一切决定性结论必须 ≥60 seeds**（P4 教训，多次证伪过 20/30-seed 的"海市蜃楼"增益）。

---

## 2. 时间线总览

| 阶段 | 日期 | commit | 主题 | 关键成果 |
|---|---|---|---|---|
| 基础设施 | 07-27/28 | `62d1270`…`5378f8f` | GodAIParams 参数化 + CMA-ES 优化器 + 决策追踪 | 基地存活 40%→80%（stage 0），胜率 0% |
| Round 2 | 07-28 | `6d44663` 等 | Classic 模式适配，13 项根本性修复（§33） | 基地存活 100%，胜率仍 0%（全 timeout） |
| Round 3 | 07-28 | `28683be` | 分阶段验证框架（curriculum）+ S6 参数化 + hasBase 守卫 | 失败归因可见化；canHunt 阈值接参 |
| 重构 | 07-28 | `0d3275b` | GodAIInput 拆分为 `src/ai/god/*`（纯重构，parity 验证） | ThreatAssessor / FireControl / StrategyPlanner / Navigator |
| v3/v4.1 | 07-28/29 | `f010823`, `fd126ae` | CMA-ES v3→v4.1 + 回归门禁 | 胜率 20% 封顶，基地存活 97.5% — 证明参数空间已穷尽，需改架构 |
| **P0** | 07-29 | `aec21f4` | T2a 死锁修复（§41） | S0 20%→70%，S1 22.5%→87.5% |
| **P1** | 07-29 | `2cedb7a` | 生存与防御修复（§42） | S0 87.5%，S1 92.5%，gameover 清零 |
| **P2** | 07-29 | `6780322` | 反驻扎区域 + 卡死兜底 + 预判射击（§43） | S1 100%，S3 50%→66.7% |
| **P3** | 07-29 | `c01985f` | A* 拆砖寻路 + 中心死锁修复 + 多关 CMA-ES（§44） | S9 0%→80%⭐，35 关均值 51.7%→53.9% |
| **P4** | 07-29 | `2d9fa77` | 7 轮 floor-aware CMA-ES + 逐关覆盖表（#36） | 均值 **81.9%**@60 seeds，34/35 ≥60% |
| **Round 5** | 07-29 | `49b1011` | S32 贴身缠斗 `t2aMaxRange=2`（§43-S32） | S32 43.3%→**72.5%**@120，均值 86.9%@20 |
| **Phase A** | 07-30 | `7435089` | 智能基地威胁模型（§44-SmartThreat） | **负结果**：8+ 变体全否决，基础设施保留默认 OFF |
| **§47** | 07-30 | (本轮提交) | 基地保护环碰撞修复 + base_destroyed 归因（§47） | S32 72.5%→**85.0%**@120，35×60 真值 81.9%→**87.7%**，门禁真值重生成 |
| **§48** | 07-30 | (已回退) | 假闪避地形遮挡"修复" | **负结果**：S32 -10pp@120（lives 12→22），闪避地形盲是承重行为，测试锁定禁止再"修" |
| **§49** | 07-30 | (已回退) | 炮口相向火后闪避 | **负结果**：35×20 A/B 85.0% vs 基准 87.6%（-2.6pp），S18 -25pp、S28 -15pp；post-fire dodge 打断 threat 检测 + 冰面失控 |

> 注：DECISIONS.md 已精简为索引（2026-07-31），详细决策见本文档和 `docs/perf-optimization.progress.md`。

---

## 3. 各阶段详情

### 3.1 基础设施与早期轮次（2026-07-27/28）

- **参数化**：阈值常量全部移入 `GodAIParams`，供 CMA-ES 自动调参（12→20 维）。
- **工具**：`tools/optimize/optimize-godai.ts`（sep-CMA-ES）、`tools/diag/decision-trace.ts` + `tools/diag/analyze-trace.ts`（决策追踪，用它找到 T2a 冷却空转、防守偏左、首杀过慢三大失误）。
- **Round 2 关键发现（§33）**：仿真工具漏设 `world.rules`（classic 规则从未生效，头号 bug）、`onCooldown` 需用子弹数冷却、navigate 分支曾无条件开火自毁基地、directMove 改垂直优先后单种子击杀 0→17。
- **Round 3（curriculum）**：5 个迷你关隔离验证子系统（火控/威胁优先级/S6 切换/破墙追击/防守回归）；`hasBase()` 守卫修复无基地关假阴性；`endgameEnemyThreshold` 声明未用的潜在 bug 接上（1→6）。
- **v4.1 结论（§40）**：胜率钉死 20%，5 个 0 杀种子是确定性死锁 —— **参数调优已到天花板，必须改行为架构**。这个判断催生了 P0–P3。

### 3.2 P0–P3：行为死锁逐个击破（2026-07-29）

| 阶段 | 修复 | 根因 | 效果 |
|---|---|---|---|
| P0（§41） | T2a 仅当 `scan.enemy==true` 才驻车；反驻扎计时；卡死逃逸向地图中心 | 旧代码对着墙无限开火不前进（单种子 5900 tick 空转） | S1 22.5%→87.5%（单项最大杠杆） |
| P1（§42） | 闪避对齐阈值 12px→32px；`baseUnderThreat` 提前到 row≥18；受威胁时无条件回防；受威胁时跳过 T2a/道具 | 闪避阈值窄于坦克判定箱 → 玩家撞向检测不到的子弹 | S0 70%→87.5%，S1 gameover 清零 |
| P2（§43） | 驻扎判定改 ±1 格区域（防亚格振荡重置计时）；卡死兜底任意可通行方向；`predictEnemyCrossing` 预判横穿射击 | 精确格匹配被 32↔40px 振荡打败，逃逸从未触发 | S1 100%，S3 50%→66.7% |
| P3（§44-P3） | **A* 拆砖寻路**（brick 可通行 5× 代价）；followPath 对砖开火；中心附近卡死改追最近敌 | A* 视砖不可通行 → 密砖关永远找不到路（S9 瘫痪根因） | **S9 0%→80%**，多关 CMA-ES 首次防单关过拟合 |

P3 另有重要否决：**漫游约束（回防软约束）引发负反馈循环**（约束移动→杀敌少→漏敌多→更多 gameover），验证了 §41 的警告 —— 此教训在后续 Round 5 / Phase A 反复重现。

### 3.3 P4 战役：全 35 关 floor-aware 调优（2026-07-29, `2d9fa77`）

- **7 轮 CMA-ES**（IPOP，15-worker 池，fitness v5.0 = 逐关胜率块 + deficit×8000 floor 惩罚），内环 35 关 × 20 seeds，决策全部 60 seeds 复核。
- **两大方法论发现**：
  1. **单一全局参数集无法满足 35 关** —— 失败家族需求相反（S6 要禁回撤，S18 要更宽回撤；全局动任一方向另一关掉 −30pp）。
  2. **20-seed 探针会选中海市蜃楼**（S32 单关 CMA-ES 的 60%@30 seeds 在 60 fresh seeds 复测仅 43%）。
- **解法：逐关参数覆盖表**（`src/ai/godai-stage-overrides.ts`，data over code）。每条覆盖须 ≥60 seeds 与无覆盖对照验证。
- **结构性行为保留**：race-to-base 判定并入 `isBaseUnderThreat()`；寡不敌众回撤进 `selectTargetImpl`。否决回滚：race 路径膨胀、猎杀出生带规避。
- **定稿**：均值 81.9%@60 seeds；34/35 ≥60%；唯 S32 Diamond 52%。回归门禁从 2 关重写为全 35 关。

### 3.4 Round 5：S32 贴身缠斗（2026-07-29, `49b1011`）

- **用户洞察**：4 血重甲远程对射极低效（15 格弹道 1s / 4 枪 4s），贴身 2 格弹道 ≈0、0.5s 击毙 —— **8 倍效率**。
- **实现**：新参数 `t2aMaxRange`（默认 15 = 其余 34 关逐字节不变），S32 覆盖设 2；辅助 `campTimeoutTicks:50`、`antiCampSuppressTicks:50`、`damagedArmorBonus:1`、`navStuckTicks:90`。
- **成绩**：S32 43.3%→**72.5%** @120 seeds（base_destroyed 43→21，lives_exhausted 25→12）；35 关均值 86.9%@20 seeds，0/35 破 floor。
- **7 个否决方案**（全部 ≥60 seeds）：守卫带 7%、基地中心目标 32%、T2a 全跳 28%、T2a 快车跳 50.8%@120、缩 leash 24–48%、aimError=0 47%、damagedArmorBonus>1 无差异。共同教训：**任何打断重甲击杀的干预都是净负**。

### 3.5 Phase A：智能基地威胁模型（2026-07-30, `7435089`）—— 高价值负结果

- **假设**（plan/God-AI-Next-Round.md）：`isBaseUnderThreat()` 类型盲+地形盲是 S32 剩余 base_destroyed 尾部的根因；引入速度/朝向/HP/距离加权威胁评分应能改善。
- **实现**：`src/ai/god/SmartThreatModel.ts`（threatScore / canShootBaseFrom / smartIsBaseUnderThreat）+ 7 个新参数全默认 OFF（OFF 时逐字节不变，已独立复验）。
- **结果：8+ 变体 @120 seeds 全部否决**（最差 −20pp；类型权重单项 −1.7pp 噪声内；35×20 A/B 全关中性）。
- **三条推翻计划假设的诊断发现**：
  1. ~~S32 基地杀手是 armor 53% + power 37%~~ —— **此归因已被 §47 推翻**：当时的 killerKind 是"事发前最后发射的敌方子弹"，非实际命中子弹。真实拦截（120 seeds）：**fast=14、armor=4、power=2、player=1**；且 S32 队列实为 8 armor/8 fast/4 power（旧文档 10/7/3 有误）。Phase A 干预否决结论仍然有效。
  2. 贴身缠斗极脆弱：任何目标切换都让 lives_exhausted 暴涨（12→32 最差）。
  3. **瓶颈是响应时间不是检测**：15/19 拆家发生在前 3000 tick 早期波；7/19 时玩家就在基地 0–5 格却来不及；致命子弹是护墙毁后近距发射，T8 拦截无法触发。
- **处置**：S32 覆盖回退原配置（72.5% 不变）；基础设施保留默认 OFF 备用；计划文档顶部加"已实测否决"横幅。

---

## 4. 未解决问题与下一步方向

**S32 差距已关闭（72.5%→85.0% @120，正式值）**：§47 基地保护环碰撞修复将 S32 提升至 85.0%@120 / 90.0%@60，**超过 80% 目标**。修复根因不是 God AI 策略，而是仿真层碰撞语义：`bulletHitsTerrain()` 在同一 tick 内子弹可穿过保护砖命中基地（含玩家自毁）。修复后 base_destroyed 21→6。35×60 真值均值 81.9%→**87.7%**，0/35 破 floor，门禁真值已重生成。

**§48 假闪避"修复"= 负结果（已回退）**：给 `findMostDangerousBulletImpl` 加弹道地形遮挡检查实测 S32 **-10pp**@120（85.0→75.0，lives 12→22），35×60 均值 -1.0pp；`isSafeDirImpl` 单独加遮挡为中性，按"中性结构改动一律否决"纪律一并回退。机制：贴身缠斗中"挡住子弹的砖"通常几 tick 内就被同一弹流打穿，地形盲闪避实为**有效的预判闪避**。`tests/threat-assessor.test.ts` 已锁定该行为，未来任何人想"修"它必须先过 S32@120 + 35×60 A/B（DECISIONS §48）。

**§49 炮口相向火后闪避 = 负结果（已回退）**：用户洞察正确——炮口相向时错开半格开火导致双方互中。但"火后立即垂直闪避"的实现方式有根本缺陷：① `_postFireDodgeDir` 在 `think()` 顶部优先于子弹威胁检测，打断真正的闪避；② 冰面关 S18 暴降 25pp（65→40%），垂直闪避在冰面上失控滑入更危险位置；③ 对 1HP 敌人不必要的闪避浪费 tick；④ 打断 armor 多枪击杀循环。35×20 严格 A/B：修改后 85.0% vs 基准 87.6%（**-2.6pp**）。代码已回退。教训：任何在 `think()` 顶部插入新分支的改动都会打断 threat → T8 → T2a 的既定优先级链，后果不可预测。

**已知代码层面的剩余结构性问题**（非碰撞/几何 bug，属设计局限）：
- 子弹闪避不查弹道遮挡（隔钢墙也躲）——**已实测确认为有益启发式，勿修**（§48 负结果，测试锁定）。
- 回防点几乎写死（(12, 24−defenseRowOffset)，仅列向 ±5 平移），不看地形。
- nav-stuck 逃逸盲目朝 (12,12)（有兜底与瞬态性，风险有限）。
- God AI 无"主动占据要道"概念 —— 默认行为 = 追最近敌 + 对齐才停火。多敌 A* 通路交点选防守位的设计已写入 plan（未实施，Phase B/C 因 Phase A 负结果已冻结）。

---

## 5. 方法论沉淀（可复用纪律）

1. **60-seed 规则**：20-seed ±11pp 噪声只配筛方向；决定性结论必须 ≥60 seeds（S32 用 120）。
2. **Trust-but-verify**：每轮他人/上轮报告的数字先独立复跑再采信（v3 曾出现 37.5% 头条不可复现；v4.1 起报告诚实度显著提升）。
3. **参数门控默认 OFF**：新行为一律 `param=0` 默认关闭，OFF 时逐字节不变 → 回归门禁天然守护其余 34 关。
4. **Data over code**：逐关差异走覆盖表，不写关卡特判代码。
5. **负结果照常提交并全记录**（DECISIONS §44-SmartThreat 是范本）：基础设施可复用，诊断数据扭转方向。
6. **回归门禁随收益上调 floor**，禁止静默降低（S32 truth 51.7→72.5→90.0、聚合 77%→83% 已同步；§47 改仿真语义后全表真值按新 35×60 重生成）。
7. **警惕负反馈循环**：一切"约束移动保基地"的方案（P3 漫游约束、Round 5 守卫带、Phase A skipT2a）都因同一机制失败 —— 约束→杀敌少→漏敌多→更多失败。

---

## 6. 参数与覆盖表现状

- **全局默认**：`DEFAULT_GOD_AI_PARAMS` = P4 R7 最优（关键：threatRangeCells 10、maxPlayerDistFromBase 26、powerupMaxDivertDistance 16、huntAllyCount 1、aimError 0.03 —— 微量瞄准噪声全局打破互堵僵局）。
- **覆盖表**（`src/ai/godai-stage-overrides.ts`，2 条，全部 ≥120 seeds 验证）：

| 关 | 覆盖 | 120-seed 对比 |
|---|---|---|
| S26 Brick Maze | replanInterval:30, suboptimalPathProb:0.05 | 68.3% vs 裸默认 67.5% (+0.8pp, 防 base) |
| S32 Diamond | t2aMaxRange:2 + camp/armor/navStuck 辅助 | 72.5% vs 裸默认 48.3% (+24.2pp, 必需) |

- ~~S6 Iron Curtain~~ 覆盖已移除（§54, 2026-07-30）：R8 保守覆盖（maxPlayerDistFromBase:16 等）在 RNG split + §47 后过时，120-seed 探针覆盖 59.2% < 裸默认 62.5%，且 base 破坏数反增（43 vs 30）。移除后 35×60 S6 从 57%→72%，suite +0.019。
- ~~S18 Frozen Field~~ 覆盖已移除（§55, 2026-07-30）：outnumberedRadiusCells:14 导致过早回撤丢失中盘控制权，120-seed 覆盖 56.7% < 裸默认 60.8%。aimError:0 也已无效（默认 0.03 已足够小）。
- ~~S25 Ice Palace~~ 覆盖已移除（§55, 2026-07-30）：aimError:0 vs 默认 0.0303 逐种子完全相同（77.5% = 77.5%），覆盖完全无效。
- **审计结论**：5 个原始覆盖中 3 个（S6/S18/S25）已过时，过时率 60%。基线变动后必须重新审计所有覆盖。

- **保留未启用**：`smartThreatModel` 族 7 参数（Phase A，默认 OFF）、`guardBandMode`（已否决）。

---

## 7. 工具链索引

| 工具 | 用途 |
|---|---|
| `tools/optimize/optimize-godai.ts` | CMA-ES 参数优化（--stages 多关聚合 fitness） |
| `tools/sim/simulation-runner.ts` + `tools/sim/sim-pool.ts` / `tools/sim/sim-worker.ts` | 并行仿真（默认应用覆盖表） |
| `tools/eval/validate-p4.ts --seeds N` | 全 35 关扫描终审 |
| `tools/optimize/probe-params.ts` / `tools/diag/probe-s32.ts` | 参数敏感度探针（`--skipStageOverrides` 量纯参数） |
| `tools/diag/diagnose-s32.ts` | 失败归因诊断（拆家时刻/玩家位置/凶手类型） |
| `tools/optimize/ab-test-smart-threat.ts` | 35 关 off/on A/B |
| `tools/diag/decision-trace.ts` + `tools/diag/analyze-trace.ts` | 逐 tick 决策追踪 |
| `tools/relock-parity.ts`（已移除，一次性脚本） | parity 基线重锁 |
| `tools/optimize/curriculum.ts`（`bun run curriculum`） | 5 迷你关子系统隔离验证 |
| `tests/god-ai-regression-gate.test.ts` | 全 35×20 回归门禁（~11s） |

---

## 8. 文献索引

- **权威决策**：DECISIONS.md §27/§28a/§33（早期）、§36-curriculum/§37/§39/§40（框架与 CMA-ES）、§41–§44-P3（P0–P3）、#36-P4（战役）、§43-S32（Round 5）、§44-SmartThreat（Phase A 负结果）、§47（基地保护环碰撞修复 + 归因）、§48（假闪避遮挡否决，负结果）。
- **现存设计文档**：`plan/God-AI-Next-Round.md`（智能威胁模型设计规格，顶部有"Phase A 已否决"状态横幅；`SmartThreatModel.ts` / `GodAIInput.ts` / 覆盖表的代码注释直接引用其章节号，故保留）。
- **已归档的历史文档（2026-07-30 清理，内容已并入本档 + DECISIONS.md，如需原文用 git 历史找回）**：`docs/god-ai-tuning-log.md`（Round 1–3 详录）、`plan/god-ai.progress.md`（P4/Round 5 进度）、`plan/God-AI-Tuning.md`（初始目标）、`plan/god-ai-analysis.md`、`plan/God-AI-Curriculum.md`、`plan/gac.review.md`、`plan/God-AI-P0~P3-Verification.md`（4 份）、`plan/God-AI-P3-Direction.md`。找回方式：`git log --diff-filter=D --oneline -- <path>` + `git show <hash>^:<path>`。
- **每日工作记录**：`.workbuddy/memory/2026-07-27.md` 起。

---

## 9. 炮口相向策略实验（§49–§52，2026-07-30）

### §51 v1：火后闪避（已回退）
- **实现**：在 `think()` 顶部插入 `_postFireDodgeDir` 分支，火后垂直移动 4 ticks
- **结果**：35×20 A/B 85.0% vs 基准 87.6%（**-2.6pp**），S18 -25pp、S28 -15pp
- **根因**：顶层分支打断 threat → T8 → T2a 优先级链

### §52 v2：对枪抵消（T2a 内联，保留）
- **实现**：在 T2a 分支内部检测炮口相向 + 对枪抵消
- **分场景**：
  - 冰面：跳过（横移失控）
  - 1HP 敌人：对枪抵消仍生效（开火行为 ≠ 移动闪避）
  - Armor：对枪抵消 + 保持对齐等待
  - 横移：已移除（4-tick 横移 S26 -10pp）
- **35×120 A/B**：基准 3618/4200 (86.1%) → 修改 3623/4200 (86.3%)，**+5 wins**
- **关键发现**：对枪抵消对所有敌人类型都有益（ALL +5 vs armor-only +1）。当 1HP 敌人已开火时，开火抵消比直接打敌人更安全（子弹消除→玩家安全→下一枪杀敌）
- **新增函数**：`findEnemyFacingPlayerImpl` (FireControl.ts)、`hasEnemyBulletInLineImpl` (ThreatAssessor.ts)
