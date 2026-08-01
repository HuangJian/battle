# God AI 调校进展总览（系统化整理）

> 汇编自：git commit history、历史日志、各阶段计划/验证文档、`.workbuddy/memory/`（每日工作日志）。
> 整理日期：2026-07-30。此文档为**只读汇总**。DECISIONS.md 已精简为索引（2026-07-31），
> 新决策仍以 DECISIONS.md 为准，详细内容以本文档为准。

---

## 0.A 当前状态速览（截至 2026-08-01 §74 基地自杀修复）

| 指标 | 数值 | 口径 |
|---|---|---|
| 全 35 关真值均值 | **92.1%**（前值 91.9%） | 35 关 × 20 seeds, classic, 18000t（§74 距离感知基地墙火控修复后基线） |
| 低于 60% floor 的关卡数 | **0 / 35** | §47 修复后所有关均达标 |
| 最弱关 S32 Diamond | **72.5%** @120（75.0% @20） | §74 修复后，killer=player 4→1（seeds 26/78/82 已修复，seed 34 残留） |
| 质量门禁 | **全绿** | tsc + oxlint + oxfmt clean |
| 回归门禁 | 全 35 关 × 20 seeds，聚合 645/700 (92.1%)，floor 581 | `tests/god-ai-regression-gate.test.ts` |

> §67（停止调参）：35×60 已达 88.9%，多轮 CMA-ES 探针确认参数空间已收敛到平坦最优——任何方向微调均在 ±1pp 噪声内，无系统性增益。调参正式冻结。
> §68（交叉火力感知 v2）：60-seed A/B 88.9%（OFF）vs 87.8%（ON）= **-1.1pp 净负**，按"负结果否决"纪律默认关闭，基础设施保留。v1 为 -0.4pp 中性，v2 采用时间感知路径投影但 diversion 响应在迷宫关卡代价过高。
> §69（交叉火力感知 v3）：地形门控 + A* 威胁成本，双负结果。地形密度无法区分回退/改善关（S1 改善+7pp 密度 37% > S6 回退-15pp 密度 27%）。A* cost=3.0 时 -1pp，cost=1.0 时 -6pp。实验系列终结。
> **§48-revisit（2026-08-01）**：钢墙专用闪避遮挡 + 地形门控（brickWallRatio < 0.10 的钢迷宫关自动启用），**首个通过"零负结果"验收的 §48 变体**。35×60 全关 A/B net +1 flip、0 关回退；S32 +3.3pp @120（68.3→71.7）、S6 +0.8pp @120。判别量是 brickWallRatio 而非 steel ratio（S26 钢 26% > S32 钢 18% 却回退）。默认 `evasionSteelOcclusionBrickRatio=0.1` 已启用（仅 S6/S32）。详见 DECISIONS §71。
> **§49-revisit（2026-08-01）**：炮口相向对枪抵消（§52 v2 保留形态）参数化为 `counterFire`（默认 1 = 逐字节不变）+ `counterFireMaxRange`（默认 5），在当前树上重新验证：**35×60 全关 net +3 flips、0 ON→OFF 负翻转**（S26 +2.5pp@120 种子 41/44/61、S20 +0.8pp@120 种子 60，其余 33 关 0pp）。与 §48 不同，对枪价值不随地形分界——无需地形门控。默认不变。详见 DECISIONS §72。
> **§68-revisit（2026-08-01）**：per-seed tick-diff 重新调优 §68-v2，**方法论级负结果**——4 个变体全负（raw -18 / 提前量上限 -25 / 目的地开阔度门控 -14 / 组合 -25）。per-seed 定位机制：坏翻转 12.6-23.1t 过早转向致死 vs 好翻转 8.3-8.4t 逃生转向；全 35 关地形指标相关性（density/avgPass/open%/brick/steel）无一能区分好坏关。实验代码全回退，crossfire 维持默认 OFF。详见 DECISIONS §73。
> **§74（2026-08-01）**：T2a/aggressive 火控路径绕过 `shouldFireInDirImpl` 直接开火，当 scanAhead 双偏移扫描一条线看到基地保护砖、另一条线看到敌人时，`scan.enemy` 短路放行，导致玩家打穿自己的基地（S32 @120 killer=player 4 次：seeds 26/34/78/82）。修复：scanAhead 新增 `baseWallDist` 字段；T2a/aggressive 入口检查改为 `!(scan.baseWall && scan.baseWallDist <= scan.enemyDist)`——仅在基地墙比敌人更近时阻止开火（6px 子弹跨两列，会先打中更近的障碍）。突破路径 `bs.enemy ||` 短路也修复为保守检查。S32 @120 86→87 (+1)，killer=player 4→1，base_destroyed 18→8。35×20 均值 92.1%（+0.2pp）。详见 DECISIONS §74。
> parity 8 seeds 保持 pre-§48 基线（§47 仿真层修复对 parity 关卡 S0 无影响）。

**演进主线**：基础设施 → classic 适配 → 死锁修复（P0–P3）→ 全关战役（P4）→ 单关攻坚（Round 5）→ 智能威胁模型（Phase A，负结果）→ **§47 基地保护环碰撞修复（真正的 S32 破局点）+ §48 假闪避"修复"否决** → §58 覆盖表泛化（逐关硬编码→数据驱动适配）→ §67 调参冻结 → §68-§69 交叉火力感知实验系列（全部负结果，默认 OFF）。

---

## 0.B 方法论创新：per-seed tick-diff 诊断法

本次研究过程中发现了一种高效的 God AI 回归诊断方法，已固化为可复用脚本 `tools/diag/per-seed-diff.ts`。

**方法**：
1. 用 `dump` 模式运行一个翻转 seed 的完整仿真，导出逐 tick 紧凑签名（位置、方向、开火、移动方向、敌人数、子弹数、游戏状态）
2. `git stash` 回退代码，再运行一次 `dump`
3. `git stash pop` 恢复代码
4. 用 `diff` 模式比较两次输出，找到第一个分歧 tick 和变化的字段

**为什么需要**：总胜率对比（eval-suite）只能告诉你“回归了多少”，但不能告诉你“哪个 tick、哪个决策导致了翻转”。per-seed tick-diff 能精确定位第一个分歧点，然后回溯到导致分歧的代码变更。

**V8 JIT 教训**：在每秒调用数千次的热循环里加代码（即使功能上是 no-op）会改变 V8 的优化决策，导致 cascade 行为差异。热循环改动必须用 per-seed 对比验证，不能只看总胜率。本次的 -1pp 残留回归就是通过此方法发现的——diff 显示 tick 1062 的 `fire` 字段从 `.` 变成 `F`，由此追溯到 steel 分支里的额外计算改变了 V8 对 `scanAheadImpl` 的 JIT 优化。

---

## 0.C 调优签入规则（per-seed tick-diff 方法）

调优循环（A/B → per-seed tick-diff → 修复 → 组合重验）产出的非交付物按以下规则处理：

1. **A/B test 脚本不做版本管理**：验证完毕后的 `ab-test-*.ts` 不入库。它们是一次性诊断脚本，保留价值在结果而非代码——本地保留（`.gitignore` 已忽略），需要时直接重跑。
2. **per-seed-diff.ts 的修改必须可泛化**：验证过程中对 `tools/diag/per-seed-diff.ts` 的修改，只有能泛化到将来其它参数诊断的才入库。参数覆盖统一走通用 `--set <key>=<value>` 标志（可重复，任意数值型 GodAIParams key）；参数特化的硬编码标志（如 `--steelOcclusion`、`--noCounterFire`、`--brickGate`）不入库。
3. **临时数据文件不入库**：验证产生的临时数据文件（如 `tmp/` 下的 tick-dump、A/B 输出）在 `.gitignore` 中忽略，并在每次 commit 前删除。

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
| **§58** | 07-31 | `godai-stage-overrides.ts` | 覆盖表泛化（逐关硬编码→数据驱动适配） | S32/S26 覆盖泛化为 armorAdaptRatio/brickDenseAdaptRatio，覆盖表清空，均值 87.7%→88.9% |
| **§67** | 07-31 | — | 调参冻结（平坦最优确认） | 多轮 CMA-ES 探针均在 ±1pp 噪声内，无系统性增益，正式停止调参 |
| **§68** | 08-01 | (默认 OFF) | 交叉火力感知 v2（时间感知路径威胁投影） | **负结果**：60-seed A/B 88.9% vs 87.8%（-1.1pp），迷宫关 S6/S26 各 -15pp，开阔关 S28 +12pp；检测正确但 diversion 响应在迷宫中有害 |
| **§69** | 08-01 | (默认 OFF) | 交叉火力感知 v3（地形门控 + A* 威胁成本） | **双负结果**：地形密度无法区分回退/改善关（S1 改善+7pp 密度 37% > S6 回退-15pp 密度 27%）；A* cost=3.0 时 -1pp（p=0.001），cost=1.0 时 -6pp。实验系列终结 |
| **§48-revisit** | 08-01 | (默认 ON, 门控) | 钢墙专用闪避遮挡 + 地形门控 + 钉死位门控 | **首个通过验收的 §48 变体**：35×60 net +1 flip、0 关回退；S32 +3.3pp@120、S6 +0.8pp@120。钢迷宫关（brickWallRatio<0.10）启用，砖密关（S14/S26）逐字节不变 |
| **§49-revisit** | 08-01 | (默认 ON, 参数化) | §52 v2 对枪抵消参数化 + 当前树重验 | **第二个通过"零负结果"验收**：35×60 net +3 flips、0 ON→OFF 负翻转（S26 +2.5pp@120、S20 +0.8pp@120）。逐字节验证参数化默认 = 已提交硬编码基线（§70 纪律） |
| **§68-revisit** | 08-01 | (默认 OFF, 否决) | per-seed tick-diff 重新调优 §68-v2（4 变体全负） | **方法论级负结果**：raw -18 / 提前量上限 -25 / 开阔度门控 -14 / 组合 -25 全负；坏翻转 12.6-23.1t 过早转向 vs 好翻转 8.3-8.4t 逃生；全地形指标无法区分好坏关。代码回退，crossfire 维持默认 OFF |

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

**§48-revisit 钢墙专用遮挡 = 通过验收（默认 ON，地形门控）**：原 §48 失败根因有两个——①遮挡了砖（砖是临时的，几 tick 内被打穿，闪避实为预判）；②无视玩家几何约束（钉死角落时闪避就是逃生）。revisit 只遮挡钢墙，且仅在玩家未钉死（>2 个可移动方向）时生效；再用 `brickWallRatio < 0.10` 地形门控只对钢迷宫关（S6/S32）启用。诊断主线：per-seed tick-diff 找到 S32 seed-11 钉死机制（tick 738 玩家卡 (0,1) 不躲致死）→ 钉死位门控修复 → S26 seed-7 暴露 re-ranking 级联（跳过钢墙阻挡弹后改躲更远的无阻挡弹，tick 2954 提前一 tick 下躲）→ 地形门控让 S26/S14 逐字节不变。A/B 数据：S32 +3.3pp@120、S6 +0.8pp@120、35×60 net +1 flip 零回退。S32 base_destroyed 11→18 但 lives_exhausted 27→16——以基地风险换生存，净胜。

**§49 炮口相向火后闪避 = 负结果（已回退）**：用户洞察正确——炮口相向时错开半格开火导致双方互中。但"火后立即垂直闪避"的实现方式有根本缺陷：① `_postFireDodgeDir` 在 `think()` 顶部优先于子弹威胁检测，打断真正的闪避；② 冰面关 S18 暴降 25pp（65→40%），垂直闪避在冰面上失控滑入更危险位置；③ 对 1HP 敌人不必要的闪避浪费 tick；④ 打断 armor 多枪击杀循环。35×20 严格 A/B：修改后 85.0% vs 基准 87.6%（**-2.6pp**）。代码已回退。教训：任何在 `think()` 顶部插入新分支的改动都会打断 threat → T8 → T2a 的既定优先级链，后果不可预测。

**§68 交叉火力感知 v2 = 负结果（默认 OFF，基础设施保留）**：用户修正了 v1 的理解偏差，指出需检查整条移动路径、所有方向炮弹、多策略规避。v2 实现了时间感知路径威胁投影（`findPathThreatImpl` + `findSafeMoveDirImpl`），使用 `b.speed` 估算炮弹到达时间，±10 ticks 碰撞窗口。60-seed A/B：88.9%（OFF）vs 87.8%（ON）= **-1.1pp**（净负）。迷宫关 S6/S26 各 -15pp（diversion 代价极高），开阔关 S28 +12pp。核心诊断：检测正确但 diversion 响应在迷宫中有害——子弹安全 ≠ 位置安全。详见 §10。

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
- **覆盖表**（`src/ai/godai-stage-overrides.ts`）：**§58 后已清空**。S32/S26 覆盖已泛化为数据驱动适配（`armorAdaptRatio` / `brickDenseAdaptRatio`），按关卡特征自动触发，无需逐关硬编码。

- ~~S6 Iron Curtain~~ 覆盖已移除（§54, 2026-07-30）：R8 保守覆盖（maxPlayerDistFromBase:16 等）在 RNG split + §47 后过时，120-seed 探针覆盖 59.2% < 裸默认 62.5%，且 base 破坏数反增（43 vs 30）。移除后 35×60 S6 从 57%→72%，suite +0.019。
- ~~S18 Frozen Field~~ 覆盖已移除（§55, 2026-07-30）：outnumberedRadiusCells:14 导致过早回撤丢失中盘控制权，120-seed 覆盖 56.7% < 裸默认 60.8%。aimError:0 也已无效（默认 0.03 已足够小）。
- ~~S25 Ice Palace~~ 覆盖已移除（§55, 2026-07-30）：aimError:0 vs 默认 0.0303 逐种子完全相同（77.5% = 77.5%），覆盖完全无效。
- **审计结论**：5 个原始覆盖中 3 个（S6/S18/S25）已过时，过时率 60%。基线变动后必须重新审计所有覆盖。

- **保留未启用**：`smartThreatModel` 族 7 参数（Phase A，默认 OFF）、`crossfireAwareness`（§68，默认 OFF）、`crossfireOpenObstacleRatio`（§69-A，默认 OFF）、`crossfirePathCost`（§69-B，默认 OFF）、`guardBandMode`（已否决）。

---

## 7. 工具链索引

| 工具 | 用途 |
|---|---|
| `tools/optimize/optimize-godai.ts` | CMA-ES 参数优化（--stages 多关聚合 fitness） |
| `tools/sim/simulation-runner.ts` + `tools/sim/sim-pool.ts` / `tools/sim/sim-worker.ts` | 并行仿真（默认应用覆盖表） |
| `tools/eval/validate-p4.ts --seeds N` | 全 35 关扫描终审 |
| `tools/optimize/probe-params.ts` / `tools/diag/probe-s32.ts` | 参数敏感度探针（`--skipStageOverrides` 量纯参数） |
| `tools/diag/diagnose-s32.ts` | 失败归因诊断（拆家时刻/玩家位置/凶手类型） |
| `tools/optimize/ab-test-smart-threat.ts`（不入库，见 §0.C） | 35 关 off/on A/B |
| `tools/diag/decision-trace.ts` + `tools/diag/analyze-trace.ts` | 逐 tick 决策追踪 |
| `tools/relock-parity.ts`（已移除，一次性脚本） | parity 基线重锁 |
| `tools/optimize/curriculum.ts`（`bun run curriculum`） | 5 迷你关子系统隔离验证 |
| `tests/god-ai-regression-gate.test.ts` | 全 35×20 回归门禁（~11s） |

> 注：A/B 诊断脚本（`ab-test-*.ts`，含 `tools/diag/ab-test-counter-fire.ts`、`tools/diag/ab-test-steel-occlusion.ts`）验证完毕后不入库，本地保留可重跑（§0.C 规则 1）。

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
- **35×120 A/B（2026-07-30 原树）**：基准 3618/4200 (86.1%) → 修改 3623/4200 (86.3%)，**+5 wins**
- **关键发现**：对枪抵消对所有敌人类型都有益（ALL +5 vs armor-only +1）。当 1HP 敌人已开火时，开火抵消比直接打敌人更安全（子弹消除→玩家安全→下一枪杀敌）
- **新增函数**：`findEnemyFacingPlayerImpl` (FireControl.ts)、`hasEnemyBulletInLineImpl` (ThreatAssessor.ts)

### §49-revisit（2026-08-01）：对枪抵消参数化 + 当前树重验 —— 通过"零负结果"验收

**背景（用户指令）**：用 §48-revisit 的 per-seed tick-diff 方法重新处理 §49，消除负结果；当前树保留的 §52 v2 对枪抵消不保证没问题，发现代码问题则一并修复后组合 A/B 验证。

**参数化**：`counterFire`（默认 1 = 当前行为逐字节不变）+ `counterFireMaxRange`（默认 5 = 原硬编码值）。`counterFire=0` 回退到 pre-§52 普通 T2a（A/B OFF 臂）。`AIM_RANGE_CELLS`=15，`counterFireMaxRange`=5 是绑定门控，未被原语扫描范围遮蔽。

**验证序列**（与 §48-revisit 同方法）：
1. **逐字节一致性（§70 纪律）**：参数化默认 vs 已提交硬编码基线，per-seed tick-diff 对比 S26 seed-41、S20 seed-60 —— 双 **IDENTICAL**（三元表达式 + `counterFireMaxRange * CELL` 热路径形状变化无 JIT 漂移）。
2. **35×60 全关 A/B**：**net +3 flips、0 ON→OFF 负翻转**（S20 +2pp、S26 +3pp，其余 33 关 0pp），均值 88.9% → 89.0%。
3. **120-seed 确认**：S26 +2.5pp（3 胜 0 负，种子 41/44/61）、S20 +0.8pp（1 胜 0 负，种子 60）—— 增益真实，非种子噪声。
4. **回归门禁（生产默认）**：644/700（92.0%）> floor 581 ✅。`bun run check` 665 测试全绿。
5. **新单元测试**（`tests/counter-fire.test.ts`，10 条）：锁定 `findEnemyFacingPlayerImpl`（相向检测/同向 null/未对齐 null）+ `hasEnemyBulletInLineImpl`（逼近 true/远离 false/未对齐 false/超 8-tank 范围 false）+ 默认参数（counterFire=1、counterFireMaxRange=5）。

**结论**：与 §48 不同，对枪抵消的价值不随地形分界——不需要地形门控。默认保持 ON，零负结果达标。DECISIONS §72。

---

## 10. 交叉火力感知实验（§68-v1 → §68-v2，2026-08-01）—— 负结果

### 用户洞察（v2 修正后）

用户指出 v1 实现的多个根本性理解偏差：

1. **不是"追击方向的前方"**，而是 player 追击产生的位置变化——需要检查整条移动路径
2. **路径中的每一格**都可能被各个方向的炮弹击中——不只检查对齐的炮弹
3. **炮弹来自所有敌人**——目标敌人和非目标敌人都要检查
4. **多策略规避**——寻找新安全路径、留在原地、切换目标，不只是垂直闪避
5. **静止时也需评估**——不追击时当前位置也要做威胁评估
6. **2 格太激进**——player 来不及闪避（子弹 8 ticks 到达，player 只能移动 8px，不够清除 32px 判定箱）

### v2 实现：时间感知路径威胁投影

**新增两个函数**（`ThreatAssessor.ts`）：

1. `findPathThreatImpl(pcx, pcy, moveDir, playerSpeed)` — 投影玩家移动路径（3 格前瞻），对每一格检查所有敌方炮弹的到达时间。使用 `b.speed`（实际子弹速度）进行时间估算：如果炮弹在玩家离开该格之前到达，就是威胁。碰撞窗口 ±10 ticks（基于 TANK+BULLET 判定箱重叠时间）。

2. `findSafeMoveDirImpl(pcx, pcy, threatenedDir, playerSpeed)` — 当路径受威胁时，检查垂直和后方方向的第 1 格安全性（cell-1-only，非全路径）。返回安全方向或 null（保持原方向）。

**集成位置**（`GodAIInput.ts` `think()` 导航分支末尾）：
- 在导航确定 `_moveDir` 后、开火控制前检查路径威胁
- 发现威胁时：尝试安全替代方向；**无安全方向则保持原方向**（不停止！）
- 只影响 T2b 导航分支，T8/T2a/aggressive 不受影响
- 参数 `crossfireAwareness`（默认 0 = OFF）

### v2 迭代过程

| 配置 | 时间窗口 | 前瞻 | 替代方向检查 | 20-seed 结果 | 60-seed 结果 |
|---|---|---|---|---|---|
| ±30 ticks, 4 格 | ±TANK/ps (±30) | 4 | 全路径 | -1.9pp | — |
| ±30 ticks, 3 格, 保持原方向 | ±TANK/ps (±30) | 3 | 全路径 | -2.3pp | — |
| ±30 ticks, 3 格, cell-1 检查 | ±TANK/ps (±30) | 3 | cell-1 | -2.2pp | — |
| **±10 ticks, 3 格, cell-1** | **±10** | **3** | **cell-1** | **-1.3pp** | **-1.1pp** |
| ±10 ticks, 1 格, cell-1 | ±10 | 1 | cell-1 | -2.2pp | — |

最终配置：±10 ticks, 3 格前瞻, cell-1 替代方向检查, 无安全方向时保持原方向。

### v2 60-seed A/B 结果

| | OFF（基线） | ON（crossfireAwareness=1） |
|---|---|---|
| 35 关 × 60 seeds | **88.9%** | 87.8% |
| 差值 | — | **-1.1pp**（净负） |

**回退关卡**（迷宫/bunker 型，diversion 代价高）：
- S6 Iron Curtain: -15pp（钢墙迷宫，替代路径极长）
- S26 Brick Maze: -15pp（砖墙迷宫，同上）
- S14 Citadel: -12pp
- S30 Eagle Nest: -10pp
- S18 Frozen Field: -8pp（冰面 diversion 失控）

**改善关卡**（开阔型，有空间绕行）：
- S28 Spider: +12pp
- S1 Waterways: +7pp
- S27 Thicket: +7pp
- S8 Twin Towers: +5pp

### 核心诊断

**路径威胁检测本身是正确的**——时间感知投影能准确预测炮弹与玩家的时空交叉。问题在于 **diversion 响应策略**：

1. **子弹安全 ≠ 位置安全**：一个方向可能没有炮弹威胁，但通向死胡同或更长路径
2. **迷宫中 diversion 代价极高**：A* 最优路径偏离 1 格可能意味着多走 10+ 格
3. **现有反应式闪避已足够好**：`findMostDangerousBullet` 在炮弹到达时触发闪避，系统已充分调优
4. **与 §48/§49 相同的教训**：任何对 dodge → T8 → T2a → navigate 优先级链的扰动都是净负

### 处置

- `crossfireAwareness` 默认 **0（OFF）**——按"负结果否决"纪律
- 基础设施（`findPathThreatImpl` + `findSafeMoveDirImpl`）**完整保留**
- **未来方向**：正确方案应修改 A* 寻路本身（在路径代价中加入炮弹威胁成本），而非在路径确定后 diversion。这需要修改 Navigator 模块，属架构级变更。

---

### §69 交叉火力感知 v3：地形门控 + A* 威胁成本（2026-08-01）—— 双负结果

用户指出 §68 的回退全在迷宫/bunker 关卡（S6/S26/S14/S30），改善全在开阔关卡（S28/S1/S27），要求：

1. **方案 A**：通过 A* 路径周围的障碍物密度判断地形类型，仅在开阔地形应用 crossfire 策略
2. **方案 B**（fallback）：修改 A* 寻路本身，在路径代价中加入炮弹威胁成本

#### 地形密度诊断

对全 35 关计算障碍物密度（brick+steel+water）/totalCells 和平均可通过邻居数（AvgPass）：

| 关卡 | 类型 | 障碍物密度 | AvgPass | §68 结果 |
|---|---|---|---|---|
| S6 Iron Curtain | 回退 -15pp | 26.9% | 2.90 | 钢墙迷宫 |
| S26 Brick Maze | 回退 -15pp | 34.9% | 2.84 | 砖墙迷宫 |
| S14 Citadel | 回退 -12pp | 31.4% | 2.83 | bunker |
| S30 Eagle Nest | 回退 -10pp | 43.8% | 2.15 | 高水密度 |
| **S1 Waterways** | **改善 +7pp** | **37.3%** | **2.43** | "开阔" |
| **S28 Spider** | **改善 +12pp** | **29.0%** | **2.81** | "开阔" |

**关键发现**：S1（改善 +7pp）的障碍物密度（37.3%）**高于** S6（回退 -15pp，26.9%）。地形密度**无法**区分回退和改善关卡——两者指标完全交叠。

#### 方案 A：地形门控 crossfire（负结果）

- 新增 `crossfireOpenObstacleRatio` 参数（默认 0 = OFF）
- 在 `computeStageAdaptedParams` 中，当障碍物密度 < 阈值且非钢墙迷宫时，自动启用 `crossfireAwareness`
- 阈值 0.40 + isSteelMaze 门控：门控掉 S3/S6/S7/S9/S24/S30/S32/S34，保留 S1/S8/S27/S28 ON

**20-seed A/B（9 关键关卡）**：
- S1 +10pp ✓、S28 +5pp ✓、S6/S30 无变化 ✓（正确门控）
- **S14 -25pp ✗✗✗、S26 -15pp ✗✗✗**（阈值无法捕获）
- 净效果：suite 0.794→0.705（-0.089），胜率 92%→89%（-3pp）

#### 方案 B：A* 寻路威胁成本（负结果）

- 新增 `crossfirePathCost` 参数（默认 0 = OFF）
- 在 `pathfind.ts` 的 `findPath` 中，将 `threatCosts[nk]` 加入步骤代价
- `computeThreatCostsImpl` 预计算每格威胁成本：基于炮弹轨迹投影 + 时间感知碰撞窗口（±10 ticks）
- 在 `navigateTowardsImpl` 和 `replanImpl` 中调用，A* 自然绕开威胁格

**20-seed A/B（全 35 关，cost=3.0）**：
- suite 0.764→0.737（-0.027，p=0.0011），胜率 92%→91%（-1pp）
- S26 -15pp、S28 -5pp（§68 的改善关也变成回退！）
- 51 关改善 / 73 关回退 / 576 平

**20-seed A/B（6 回退关卡，cost=1.0）**：
- suite 0.757→0.685（-0.072，p=0.0093），胜率 89%→83%（-6pp）
- 所有关卡均回退或持平，无一改善

#### 核心诊断

**任何形式的前瞻式炮弹规避都是净负收益**，无论是 post-hoc diversion（§68-v2）还是 A* 威胁成本（§69-B）。根本原因：

1. **反应式闪避已足够好**：`findMostDangerousBullet` 在炮弹到达时触发垂直闪避，系统已充分调优
2. **路径偏离代价 > 炮弹风险**：A* 最优路径偏离 1 格可能意味着多走 3-10 格，暴露在更多敌人火力下的时间更长
3. **威胁成本破坏路径最优性**：即使 cost=1.0（仅偏好 1 格绕行），也会导致路径变长，净效果为负
4. **与 §48/§49 相同的教训**：对 dodge → T8 → T2a → navigate 优先级链的任何扰动都是净负

#### 处置

- `crossfireOpenObstacleRatio` 默认 **0（OFF）**
- `crossfirePathCost` 默认 **0（OFF）**
- 所有基础设施（`computeThreatCostsImpl`、`threatCosts` in `PathConstraints`、`crossfireOpenObstacleRatio` 门控）**完整保留**，默认 OFF 时字节一致
- **交叉火力感知实验系列正式终结**（§68-v1 → §68-v2 → §69-A → §69-B）

### §68-revisit（2026-08-01）：per-seed tick-diff 重新调优 —— 方法论级负结果（维持默认 OFF）

**背景（用户指令）**：用 §0.B 的 per-seed tick-diff 方法重新调优 §68-v2，期望消除负结果；若现有代码有问题则修复后组合 A/B 验证。

**A/B 复现（当前树 35×60）**：OFF 89.0% vs ON 88.1%（-0.9pp，138→156 net -18）—— 与原始 -1.1pp 一致。

**per-seed 机制（cf-trace 追踪：子类化 GodAIInput 观测每次转向）**：

| 种子 | 类型 | 子弹提前量 | 机制 |
|---|---|---|---|
| S26-5 / S6-3 / S14-8 | 坏 | 12.6 / 18.6 / 23.1t | 幻影威胁 → 过早垂直转向离开 A* 路径 → 进入死路（反应式闪避本可处理） |
| S28-15 / S27-28 | 好 | 8.3 / 8.4t | 紧迫威胁 → 及时逃生转向 |

**四个变体全部净负**：
1. **提前量上限** `crossfireThreatTicks=10`（只标志 10t 内到达的威胁）：net -25。迷宫关改善（S26 -12→-7、S6 -10→-7、S31 -8→-2、S30 -7→-5）但破坏开阔关增益（S28 +7→-3、S32 +5→-2、S1 +5→+2）。**链断裂机制**：S28-15 的逃生需要第二个 31.7t 提前量的转向（tick 3700），被 cap 抑制 → 整个逃生链崩塌。
2. **目的地开阔度门控** `crossfireMinExits=3`（只转向 ≥3 出口格）：net -14。坏翻转的死亡 lane 是**局部开阔**（≥3 出口）—— S26-5/S6-3/S14-8 与 raw ON 逐字节相同，出口计数无法区分。
3. **组合**：net -25（继承 cap 的开阔关破坏）。
4. **地形指标相关性（全 35 关）**：density / avgPass / open% / brick% / steel% **无一能区分好坏关**（S2 23% 密度坏 vs S8 23% 好；S33 avgPass 2.93 好 vs S30 2.94 坏）—— §69-A 结论扩展：纠缠是动态的（敌方位置/子弹状态/级联），非静态地形。

**结论**：增益与损失共享同一触发——不存在提前量/目的地质量/地形的干净判别量。这是 §68/§69 结论（"任何对 dodge → T8 → T2a → navigate 优先级链的扰动都是净负"）的机制级确认。实验代码全部回退（src/ 逐字节不变），crossfire 维持默认 OFF。DECISIONS §73。

---

## §70 基地环开火保护（修复 coop 自杀 + V8 JIT 热循环敏感性发现）

### 背景
`plan/fix-suicide.task.md`：God AI 在 coop 模式（player2 出生在基地右侧）会打破基地保护砖/钢铁自杀。

### 根因（三层逐层剥洋葱）

1. **自杀根因**：T2b 导航开火分支用 `!canMoveDir` 触发开火，绕过 T6 基地保护检查。coop 模式 `_moveDir=left` 指向基地砖墙直接开火打掉。

2. **S32 -5pp 回归根因（OOB 假阳性）**：`baseSteel` 检测放在 `scanAheadImpl` 热循环的 steel 分支里，对 OOB 格子（默认 `'steel'`）也运行。S32 底边 `row=GRID`（dr=|26-24|=2）被误判为 `baseSteel`，导致 T6 的非基地钢铁守卫 `result.steel && !result.baseSteel && level < 3` 变 false——AI 不再阻止对场边射击，浪费子弹。

3. **S32 -1pp 残留回归根因（V8 JIT 敏感性）**：即使加了 OOB 边界检查，steel 分支里多出的变量声明和比较改变了 V8 对 `scanAheadImpl` 的 JIT 优化，导致 `shouldFireInDir` 路径出现微妙行为差异。

### 修复方案

**不在热循环里做 baseSteel 检测**。steel 分支只做两个赋值（`r.steelCol = col; r.steelRow = row`），循环结束后在 post-loop 块做一次 baseSteel 带状检查。OOB 天然排除（steelCol=-1 或越界）。

### 60-seed A/B 对比（`bun tools/eval/eval-suite.ts --seeds 60`）

| 指标 | 修复前 | 修复后 | Δ |
|---|---|---|---|
| Suite score | 0.7254 | 0.7291 | +0.0037 |
| LCB | 0.7205 | 0.7242 | +0.0037 |
| Fitness v6 | 720.5 | 724.2 | +3.7 |
| Mean win rate | 89% | 89% | 0 |
| S32 Diamond score | 0.527 | 0.532 | +0.005 |
| S32 Diamond win | 67% | 67% | 0 |
| Coop 自杀 | 有 | 0 | 消除 |

**零净回归。** Suite score 和 fitness 均有微小提升（在 ±se=0.0049 范围内）。

### 质量门禁
- `bun run check`：644 测试全绿，tsc + oxlint + oxfmt clean
- 回归 gate 原始 truth 值不变，floor 不降
