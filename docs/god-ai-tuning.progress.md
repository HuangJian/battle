# God AI 调校进展总览（系统化整理）

> 汇编自：git commit history、历史日志、各阶段计划/验证文档、`.workbuddy/memory/`（每日工作日志）。
> 整理日期：**2026-08-03**（v2 重设计纪元 M0–M11 收官后体系化重写，含 2026-07-30 首版内容）。
> 此文档为**只读汇总**。DECISIONS.md 为决策索引（编号保留、正文压缩，全文在本档 / git 历史）。
> 两个纪元：**Classic 纪元**（§33–§95，2026-07-27 → 08-01，单一 classic 难度调校）与
> **v2 重设计纪元**（M0–M11，2026-08-03，三难度体系化重设计，见 Part II）。

---

# Part 0. 当前状态速览（2026-08-03）

## 0.A 三难度基线（官方口径）

> 官方口径 = 35 关 × N seeds，`runSimulation` 直驱（不传 stageIndex、正确同步 playerLevel/lives），
> 门禁 seed 1..20，决定性结论 ≥60 seeds。口径历史与修复见 §II.5。

| 难度 | 20-seed 门禁真值 | 60-seed 参考 | 命数/星级 | 目标 | 状态 |
|---|---|---|---|---|---|
| classic | **91.0%**（637/700，floor 581） | ~91% | 3 命 / 0★ | >98%（v2 目标） | 门禁全绿，距目标 7pp |
| hard | **48.0%**（336/700，floor 310） | 46.3%（§105 口径） | **2 命** / 1★ | >80%（v2 目标） | 门禁全绿，差距大 |
| chaos | **48.9%**（342/700，floor 316） | 47.7%（§105 口径） | **3 命** / 1★ | >50%（v2 目标） | 门禁全绿，**距目标 1.1pp** |

- classic 门禁真值自 M0 起保持 637/700 字节持平（所有 M 行为默认 OFF / 逐字节不变）。
- hard/chaos 真值在 §105 模拟口径修复后重生成（此前 hard 被 3 命伪口径高估 ~6pp）。
- 质量门禁：三门禁 + split-parity 12/12 全绿；**891 tests**、0 lint、`bun run build` ✓。

## 0.B v2 纪元发布清单

**SHIPPED（生产默认）**
| 里程碑 | 内容 | 关键证据 |
|---|---|---|
| M0（§96） | 三难度门禁 + 逐死亡 telemetry + 死亡归因工具 + chaos 命数 1→3 | 基线 classic 91.0 / hard 38.6 / chaos 34.6 |
| M0.5（§96） | 22 个僵尸/否决参数退役（interface 95→~73，归档 experimental.ts） | classic 门禁字节持平 |
| M1（§99） | 决策链评分制外壳（DecisionCore + 8 候选体权重序） | parity 三重验证 IDENTICAL，性能 +2.6~3.3% |
| M2a（§100） | actionWeights 权重数据化基础设施 | 默认链序字节持平 |
| **M6（§104）** | **出生即一星**（hard/chaos playerStartLevel 0→1） | 60-seed hard +9.0pp / chaos +7.9pp，唯一 >3σ 行为外杠杆 |
| §105（M7） | 模拟口径三重修复（playerLevel / lives / telemetry isPlayer） | hard 真实 2 命口径 48.0%、chaos 48.9% |

**诚实阴性（不发布，实验旋钮保留）**
| 里程碑 | 内容 | 结论 |
|---|---|---|
| M2c（§100） | 权重重排 4 实验 | 链序是局部最优，纯重排无杠杆 |
| M3（§97/§98/§101） | dodge 对枪抵消（三轮门控） | 官方口径 chaos 持平偏负；stageIndex 伪影完整机制 |
| M4（§102） | 带安全门控的紧急对枪 | +0.7pp 噪声内；`godaiParams` 大小写口径事故 |
| M5（§103） | 站位提前规避（pathThreatAvoidance） | 触发率 ~1%，无信号；口径纪律再升级 |
| M8（§106） | survivalRetreat 最后一命回防 | 60-seed Δ-3 持平偏负 |
| M9（§107） | dodgeHorizonScore 生存视界承诺闪避 | 机制成立（S0 seed2 反转）但 60-seed chaos -3.5pp；双目标教训 |
| M10（§108） | horizon 时间余量门控（MARGIN6） | hard +1.6pp / chaos -2.4pp，参数全局无法发布 |
| M11（§109→§110） | 星经济二星（playerStartLevel 1→2） | 60-seed +7.5~9.4pp 强信号，**用户否决回退**（"欺负敌人"） |

**保留实验旋钮**（默认 0 / OFF，字节持平）：`dodgeCounterFire`、`dodgeClearanceScore`、`pathThreatAvoidance`、
`survivalModeLives`、`survivalRiskWeight`、`dodgeHorizonScore`、`dodgeHorizonMinMarginTicks`、
`dodgeHorizonMaxDistCells`、EnemyModel 族（`enemyModelMode`/`tierWeightScale`/`dodgeRateShrinksT2a`/
`coordinationRiskWeight`/`enemyAccuracyRaisesSurvival`）、`actionWeights.survive`。

---

# Part I. Classic 纪元（§33–§95，2026-07-27 → 08-01）

## I.1 目标与评价体系

- **最终目标（P4 用户指令）**：全 35 classic 关，逐关过关率稳定 > 60%（floor），且平均 > 80%。✅ 已达成并超出。
- **延伸目标（Round 5）**：S32 Diamond > 80% @120 seeds。✅ **85.0% 已达成**——靠 §47 仿真层碰撞修复（原"结构性差距"实为碰撞语义 bug + 归因污染）。
- **测量纪律**：20-seed 探针有 ±11pp 二项噪声，只用于筛选方向；**一切决定性结论必须 ≥60 seeds**（P4 教训，多次证伪过 20/30-seed 的"海市蜃楼"增益）。

## I.2 时间线总览（Classic 纪元）

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
| **§47** | 07-30 | — | 基地保护环碰撞修复 + base_destroyed 归因（§47） | S32 72.5%→**85.0%**@120，35×60 真值 81.9%→**87.7%**，门禁真值重生成 |
| **§48** | 07-30 | (已回退) | 假闪避地形遮挡"修复" | **负结果**：S32 -10pp@120，闪避地形盲是承重行为，测试锁定 |
| **§49** | 07-30 | (已回退) | 炮口相向火后闪避 | **负结果**：35×20 A/B -2.6pp，S18 -25pp、S28 -15pp |
| **§58** | 07-31 | — | 覆盖表泛化（逐关硬编码→数据驱动适配） | 覆盖表清空，均值 87.7%→88.9% |
| **§67** | 07-31 | — | 调参冻结（平坦最优确认） | 多轮 CMA-ES 探针均在 ±1pp 噪声内，正式停止调参 |
| **§68** | 08-01 | (默认 OFF) | 交叉火力感知 v2（时间感知路径威胁投影） | **负结果**：60-seed -1.1pp，迷宫关 -15pp、开阔关 +12pp |
| **§69** | 08-01 | (默认 OFF) | 交叉火力感知 v3（地形门控 + A* 威胁成本） | **双负结果**；实验系列终结 |
| **§48-revisit** | 08-01 | (默认 ON, 门控) | 钢墙专用闪避遮挡 + 地形门控 + 钉死位门控 | **首个通过验收的 §48 变体**：35×60 net +1 flip、0 关回退 |
| **§49-revisit** | 08-01 | (默认 ON, 参数化) | §52 v2 对枪抵消参数化 + 当前树重验 | **零负结果验收**：35×60 net +3 flips、0 ON→OFF 负翻转 |
| **§68-revisit** | 08-01 | (默认 OFF, 否决) | per-seed tick-diff 重新调优 §68-v2（4 变体全负） | **方法论级负结果**，crossfire 维持默认 OFF |
| **§80** | 08-01 | (默认 ON, 修复) | 冰冻窗口转身抖动守卫（aimTurnSnapGuard） | 35×60×2：冰冻击杀 +136%~+157%，single +0.9pp（net +20），≥5pp 回退 0 关 |
| **§87** | 08-02 | (默认 ON) | 近距离安全路径拾取优先级（8/4/2 格 + 三守卫） | **35×60 +9 wins**，0 显著负向关，门禁真值重生成（floor 581→610） |
| **§95** | 08-03 | (默认 ON, 100ms) | 转弯周期限制 turnCooldownMs 50→100ms + 原地等待 | **35×60 全档扫描选优**：100ms+等待 91.2%（net +5）SHIPPED；门禁 floor 610→612 |

> 注：本纪元所有 A/B 均为 classic 单一难度口径（hard/chaos 门禁是 v2 纪元 M0 才建立的）。

## I.3 各阶段详情

### I.3.1 基础设施与早期轮次（2026-07-27/28）

- **参数化**：阈值常量全部移入 `GodAIParams`，供 CMA-ES 自动调参（12→20 维）。
- **工具**：`tools/optimize/optimize-godai.ts`（sep-CMA-ES）、`tools/diag/decision-trace.ts` + `analyze-trace.ts`（决策追踪，找到 T2a 冷却空转、防守偏左、首杀过慢三大失误）。
- **Round 2 关键发现（§33）**：仿真工具漏设 `world.rules`（classic 规则从未生效，头号 bug）、`onCooldown` 需用子弹数冷却、navigate 分支曾无条件开火自毁基地、directMove 改垂直优先后单种子击杀 0→17。
- **Round 3（curriculum）**：5 个迷你关隔离验证子系统；`hasBase()` 守卫修复无基地关假阴性；`endgameEnemyThreshold` 声明未用的潜在 bug 接上。
- **v4.1 结论（§40）**：胜率钉死 20%，5 个 0 杀种子是确定性死锁 —— **参数调优已到天花板，必须改行为架构**。催生 P0–P3。

### I.3.2 P0–P3：行为死锁逐个击破（2026-07-29）

| 阶段 | 修复 | 根因 | 效果 |
|---|---|---|---|
| P0（§41） | T2a 仅当 `scan.enemy==true` 才驻车；反驻扎计时；卡死逃逸向地图中心 | 旧代码对着墙无限开火不前进（单种子 5900 tick 空转） | S1 22.5%→87.5%（单项最大杠杆） |
| P1（§42） | 闪避对齐阈值 12px→32px；`baseUnderThreat` 提前到 row≥18；受威胁时无条件回防 | 闪避阈值窄于坦克判定箱 → 撞向检测不到的子弹 | S0 70%→87.5%，S1 gameover 清零 |
| P2（§43） | 驻扎判定改 ±1 格区域；卡死兜底任意可通行方向；`predictEnemyCrossing` 预判横穿射击 | 精确格匹配被 32↔40px 振荡打败，逃逸从未触发 | S1 100%，S3 50%→66.7% |
| P3（§44-P3） | **A* 拆砖寻路**（brick 可通行 5× 代价）；followPath 对砖开火；中心附近卡死改追最近敌 | A* 视砖不可通行 → 密砖关永远找不到路（S9 瘫痪根因） | **S9 0%→80%**，多关 CMA-ES 首次防单关过拟合 |

P3 重要否决：**漫游约束（回防软约束）引发负反馈循环**（约束移动→杀敌少→漏敌多→更多 gameover）——此教训在 Round 5 / Phase A / M8 反复重现。

### I.3.3 P4 战役：全 35 关 floor-aware 调优（2026-07-29）

- **7 轮 CMA-ES**（IPOP，15-worker 池，fitness v5.0 = 逐关胜率块 + deficit×8000 floor 惩罚），决策全部 60 seeds 复核。
- **两大方法论发现**：① 单一全局参数集无法满足 35 关（失败家族需求相反）；② 20-seed 探针会选中海市蜃楼（S32 单关 60%@30 seeds 复测仅 43%）。
- **解法：逐关参数覆盖表**（后经 §58/§81 泛化移除）。每条覆盖须 ≥60 seeds 与无覆盖对照验证。
- **定稿**：均值 81.9%@60 seeds；34/35 ≥60%；回归门禁重写为全 35 关。

### I.3.4 Round 5：S32 贴身缠斗（2026-07-29）

- **用户洞察**：4 血重甲远程对射极低效，贴身 2 格弹道 ≈0、0.5s 击毙 —— **8 倍效率**。
- **实现**：`t2aMaxRange`（默认 15）S32 覆盖设 2 + 辅助参数。
- **成绩**：S32 43.3%→**72.5%** @120；35 关均值 86.9%@20。
- **7 个否决方案**（全部 ≥60 seeds）。共同教训：**任何打断重甲击杀的干预都是净负**。

### I.3.5 Phase A：智能基地威胁模型（2026-07-30）—— 高价值负结果

- **假设**：`isBaseUnderThreat()` 类型盲+地形盲是 S32 剩余 base_destroyed 尾部的根因。
- **实现**：`src/ai/god/SmartThreatModel.ts` + 7 个新参数全默认 OFF。
- **结果：8+ 变体 @120 seeds 全部否决**（最差 −20pp）。
- **三条诊断发现**：① S32 基地杀手归因被 §47 推翻（killerKind 口径 bug）；② 贴身缠斗极脆弱；③ **瓶颈是响应时间不是检测**（15/19 拆家发生在前 3000 tick）。
- **处置**：S32 覆盖回退；基础设施保留默认 OFF；计划文档加"已实测否决"横幅。

## I.4 Classic 纪元重要实验详情

### I.4.1 §47 基地保护环碰撞修复（S32 破局点）

`bulletHitsTerrain()` 在同一 tick 内子弹可穿过保护砖命中基地（含玩家自毁）。修复后 S32 85.0%@120、base_destroyed 21→6、35×60 真值 81.9%→**87.7%**。**修复根因不是 God AI 策略，而是仿真层碰撞语义。**

### I.4.2 §48 假闪避遮挡：负结果 → §48-revisit 钢墙专用通过

原 §48（遮挡砖）S32 **-10pp**@120 —— 地形盲闪避实为**有效的预判闪避**（砖几 tick 内被打穿）。`tests/threat-assessor.test.ts` 锁定行为，未来想"修"必须先过 S32@120 + 35×60 A/B。
§48-revisit 只遮挡钢墙（临时性低）+ 钉死位门控 + `brickWallRatio<0.10` 地形门控（只对 S6/S32 钢迷宫关启用）：**35×60 net +1 flip、0 关回退**，默认 ON。

### I.4.3 §49 炮口相向火后闪避：负结果 → §49-revisit 对枪抵消通过

"火后立即垂直闪避"实现有根本缺陷（打断优先级链、冰面失控、打断击杀循环），35×20 A/B **-2.6pp** 回退。
§52 v2 改为 **T2a 内联对枪抵消**（相向敌人开火时开火抵消敌方子弹）：35×120 **+5 wins**。
§49-revisit 参数化（`counterFire` 默认 1 + `counterFireMaxRange` 默认 5）：**35×60 net +3 flips、0 ON→OFF 负翻转**，默认 ON。对枪价值不随地形分界。

### I.4.4 §68-§69 交叉火力感知实验系列：全部负结果（默认 OFF）

- **§68-v2**（时间感知路径威胁投影 `findPathThreatImpl` + `findSafeMoveDirImpl`）：60-seed **-1.1pp**。迷宫关 -15pp（diversion 代价高）、开阔关 +12pp。**检测正确但 diversion 响应在迷宫中有害——子弹安全 ≠ 位置安全。**
- **§69-A**（地形门控）：S1 改善 +7pp 密度 37% > S6 回退 -15pp 密度 27% —— **地形密度无法区分好坏关**。
- **§69-B**（A* 威胁成本）：cost=3.0 时 -1pp、cost=1.0 时 -6pp。
- **§68-revisit**（4 变体 per-seed tick-diff 重调优）：raw -18 / 提前量上限 -25 / 开阔度门控 -14 / 组合 -25 全负；增益与损失共享同一触发（坏翻转 12.6-23.1t 过早转向 vs 好翻转 8.3-8.4t），**不存在干净判别量**。
- **核心结论**：任何形式的前瞻式炮弹规避（post-hoc diversion 或 A* 威胁成本）都是净负 —— 反应式闪避已足够好，路径偏离代价 > 炮弹风险。基础设施完整保留（M5 曾复用）。

### I.4.5 §70 基地环开火保护（修复 coop 自杀 + V8 JIT 热循环敏感性）

coop 模式 T2b 导航开火绕过 T6 基地保护检查 → 玩家打掉自家基地保护砖。修复：**不在热循环里做 baseSteel 检测**（steel 分支只做赋值，循环后一次性带状检查）。发现并记录 **V8 JIT 敏感性**：热循环里加 no-op 代码会改变 JIT 优化决策导致行为差异——热循环改动必须 per-seed 对比验证。60-seed A/B：suite 0.7254→0.7291，零净回归。

### I.4.6 §79 coop God AI 误读 w.player（躺赢模式 P2 修复）

`src/ai/god/` 7 处误读 `w.player`（P1）而非 `self.controlledTank(w)`（P2），导致 P2 重生后卡出生点并打穿基地墙。修复后单人逐字节不变，coop 过关率 100%、P2 平均剩余命 -7.63→+2.90。

### I.4.7 §80 冰冻窗口转身抖动守卫（aimTurnSnapGuard）

**根因**：转向不是免费的——`updateMovement` 换轴时 snap 垂直坐标，非网格对齐坦克一转身边缘被推 ≤CELL/2 px，目标甩出 scanAhead 偏移线；aggressive 分支无反驻车守卫。**修复**：commit 停火转向**之前**用转身后位置重跑扫描，假瞄准 → 落入 navigate。**35×60×2 终验**：冰冻击杀 coop +136%、single +157%；single 过关率 +0.9pp（net +20）；≥5pp 回退 0 关。S32 10/30-seed 回退在 60-seed 确认为种子噪声（Δ0.0pp）。

### I.4.8 §83 dodgeDirection 回退分支逃跑 bug

回退分支沿炮弹飞行方向逃跑 = 受困走廊必死。修复：排除飞行方向、优先朝向炮弹（对枪抵消）。**过关率 byte-identical**（35×60 前后 90.05% 相同）——bug 真实、单测锁定，但对 sim-runner 净中性。**方法论教训**：20-seed ⊂ 60-seed，若诊断期 git 态不干净，bulk 总胜率给出假"进步/回退"。

### I.4.9 §84-§85 冰冻驻车 + 近战逃跑检测（默认 ON）

- **§84** `aggCampTimeoutTicks=120`：aggressive 停火瞄准超时无击杀 → 抑制器 + 落入 navigate（35×20 +0.3pp）。
- **§85** `closeCombatDangerCheck=1` + `closeCombatDangerRange=2`：仅 `moveDir === opposite(enemyDir)`（逃跑）且 32px 内才触发（range=4 太激进 -1.6pp；range=2 最优 +0.4pp）。
- 最终 35×20：92.6% → **93.0%**（+0.4pp，0 关低于 80%）。

### I.4.10 §87 近距离安全路径拾取优先级（SHIPPED 默认 ON）

用户指令：炸弹/冰冻/护栏 8 格、星星/加命/护盾 4 格、船 2 格且路径安全 → 拾取 > 回防/杀敌。
新 think() 分支位于 dodge 与 T8 之后；`pickupPriorityMode=1` + 三档范围 + **三个调优循环发现的守卫**：
`pickupPriorityMaxDanger=0`（路上无敌人）、`pickupPriorityMinEnemyDist=5`（5 格内无完全生成敌人）、
`pickupPrioritySpawnRowMax=3`（出生带行 ≤3 永不紧急）。**35×60 A/B：+9 wins（suite 0.7439→0.7551）**；
0 显著负向关；OFF 逐字节不变已验证（排除 V8 JIT 级联）。门禁真值重生成（floor 581→610）。

### I.4.11 §88 据守咽喉要地（SHIPPED 默认 ON，DECISIONS §93/§94）

威胁点（可射击基地的格子）→ 威胁路径（炮口朝向门控）→ 咽喉要地（下半区 coverage 印章式选择）。
3 轮 35×60 调优（per-seed 定位 S19/S26/S32 机制）→ 终值 suite +0.0010（p=0.30 无显著差异）→
**120-seed 确认（S6/S16/S32 全 ≥ 持平）→ 用户拍板启用**：`chokepointMode` 默认 1，门禁真值重生成。

### I.4.12 §95 转弯周期限制 50→100ms（SHIPPED 默认，DECISIONS §95）

用户指令：player/enemy 转弯周期限制改为 160ms ≈ 360 APM 超级人类水平。**35×60 全档扫描**：
50ms 基线 91.0% → 160ms 原始 87.4%（net −75）→ 160ms+原地等待 89.8%（net −24）→ 50ms+等待 88.9%（net −44）→
**100ms+等待 91.2%（net +5，SHIPPED）**。per-seed 定位漂移致死（cooldown 期间沿旧方向滑行 → 改为原地等待）；
AI 转向承诺锁尝试净负回退。110/125/140ms 细粒度确认 100ms 是邻域局部最优。门禁真值重生成（floor 610→612）。

## I.5 方法论沉淀（Classic 纪元，v2 纪元继承）

1. **60-seed 规则**：20-seed ±11pp 噪声只配筛方向；决定性结论必须 ≥60 seeds（S32 用 120）。
2. **Trust-but-verify**：每轮他人/上轮报告的数字先独立复跑再采信。
3. **参数门控默认 OFF**：新行为一律 `param=0` 默认关闭，OFF 时逐字节不变 → 回归门禁天然守护其余 34 关。
4. **Data over code**：逐关差异走覆盖表/`computeStageAdaptedParams` 特征适配，不写关卡特判代码（§81 起禁逐关覆盖表）。
5. **负结果照常提交并全记录**（DECISIONS §44-SmartThreat 是范本）：基础设施可复用，诊断数据扭转方向。
6. **回归门禁随收益上调 floor**，禁止静默降低。
7. **警惕负反馈循环**：一切"约束移动保基地"的方案（P3 漫游约束、Round 5 守卫带、Phase A skipT2a、M8 survivalRetreat）都因同一机制失败——约束→杀敌少→漏敌多→更多失败。
8. **per-seed tick-diff 诊断法**（§0.B，见下）+ **V8 JIT 敏感**：热循环改动必须 per-seed 对比验证。

### I.5.1 方法论创新：per-seed tick-diff 诊断法

固化为可复用脚本 `tools/diag/per-seed-diff.ts`：dump 模式导出逐 tick 紧凑签名（位置/方向/开火/移动方向/敌人数/子弹数/状态），git stash 回退代码再 dump，diff 找第一个分歧 tick。总胜率只能告诉你"回归了多少"，per-seed 能定位"哪个 tick、哪个决策"。

**方法工具箱（§88 战役固化，v2 纪元继续使用）**：

| 工具 | 命令 | 替代的手工环节 |
|---|---|---|
| `tools/eval/eval-suite.ts --compare a.json b.json` | 全量 A/B（35×60 paired CRN） | — |
| `tools/diag/per-seed-diff.ts` | `dump` + `diff`（含 `--set` 覆盖） | 单种子 tick 级分歧定位 |
| `tools/diag/flip-scan.ts` | `--stages X --seeds 1-60 --set k=v` | 手写 bash 翻转扫描循环（自动分类 FLIP-TO-WIN / FLIP-TO-LOSE / TIED） |
| `tools/diag/decision-probe.ts` | `<stage> <seed> <tick> [--set ...]` | 每次手写 tmp/probe-*.ts（打印该 tick 完整决策上下文） |
| `tools/eval/gate-truth.ts` | `<eval-suite --json 输出>` | 手工 awk 提取门禁真值（生成可直接粘贴的代码块） |

## I.6 调优签入规则（per-seed tick-diff 方法）

1. **A/B test 脚本不做版本管理**：验证完毕后的 `ab-test-*.ts` 不入库（本地保留，`.gitignore` 已忽略）。
2. **per-seed-diff.ts 的修改必须可泛化**：参数覆盖统一走通用 `--set <key>=<value>` 标志；参数特化的硬编码标志不入库。
3. **临时数据文件不入库**：`tmp/` 下的 tick-dump、A/B 输出在 `.gitignore` 中忽略，commit 前删除。

## I.7 参数与覆盖表现状（Classic 纪元遗留）

- **全局默认**：`DEFAULT_GOD_AI_PARAMS` = P4 R7 最优（关键：threatRangeCells 10、maxPlayerDistFromBase 26、powerupMaxDivertDistance 16、huntAllyCount 1、aimError 0.03）。
- **覆盖表机制已完全移除**（§81）：不允许按关卡名做特殊化；统一 `computeStageAdaptedParams()`（armorAdaptRatio / brickDenseAdaptRatio / 钢砖比 / 森林 / 水域密度等）按关卡特征自动触发。
- **保留未启用**（M0.5 退役前）：smartThreatModel 族 7 参数、crossfireAwareness 族（§68/§69）、guardBandMode、§86 dodge 族 —— **M0.5 已全部退役/归档至 `experimental.ts`**（见 §II.1）。
- **Classic 纪元已发布参数默认**：`counterFire=1`、`evasionSteelOcclusionBrickRatio=0.1`、`aimTurnSnapGuard=1`、`pickupPriorityMode=1`（+三守卫）、`chokepointMode=1`、`turnCooldownMs=100`、`aggCampTimeoutTicks=120`、`closeCombatDangerCheck=1`。

---

# Part II. v2 重设计纪元（M0–M11，2026-08-03）

## II.0 设计文档（已归档，核心内容保留于此）

> `plan/God-AI-Redesign-Review.md`（诊断）与 `plan/God-AI-Redesign-v2.md`（设计）已于 2026-08-03
> 用户要求瘦身时删除；本节为其核心内容的永久归档。代码注释中的 `plan/God-AI-Redesign-v2 §X`
> 引用指向本设计文档原文（git 历史可找回未跟踪文件）。

### II.0.1 Review 诊断（四项架构级盲区 + 四项用户质疑）

**三个代码验证的盲区**（`god/*` 中引用数均为 0）：
1. **难度盲**：同一套 `DEFAULT_GOD_AI_PARAMS` 打所有难度（hard/chaos 敌人 rookie~commander 层级是另一个物种）。
2. **敌人 AI 层级盲**：不读 `tank.aiState.level`，commander（预测深度 8、闪避 0.9、瞄准误差 0.05）与 rookie 同等对待。
3. **自身命数盲**：不读 `world.lives`，chaos 1 命仍按"3 命可以浪"的 classic 节奏打。
4. **评估循环只有 classic**：门禁/真值/CMA-ES 全 classic 口径，hard/chaos 无任何护栏。

**实测基线**（35×20）：classic 91%、hard 39%、chaos 35%。**决定性发现：hard/chaos 失败 100% 是
lives_exhausted（玩家被打死）、0% base_destroyed**（非 classic 基地 HP=120）——瓶颈是玩家自己的生存。
最弱关全是"敌人容易包围玩家"的密集/迷宫关（Steel Fortress / Labyrinth / Thicket / Battlement）。

**四项用户质疑逐条检验**：
1. 补丁叠补丁/策略冲突（**证实**）：~20 分支顺序 if-else 即优先级；`GodAIParams` 95 字段；20+ 跨 tick 状态；六处冲突表（导航微调三连、拾取三通道、四个防卡死机制、§86 振荡四方案、chokepoint vs canHunt、适配层无记忆叠加）。
2. 大量 ON/OFF 开关（**证实且更糟**）：~30 个 0=OFF 开关；4 族僵尸参数 ~16+ 个从未在发布配置生效；2 个已回退死参数仍留 interface。
3. classic 过拟合（**证实且有实际后果**）：`computeStageAdaptedParams` 阈值注释点名 classic 关；生成库悖论——`gen-library.ts` 默认 hard 验证而 God AI hard 只有 39%，生成库被"classic 调优能力"反向筛选 = **过拟合的镜像**。
4. 方法论不成体系（**部分证实**）：per-seed-diff/flip-scan/decision-probe/gate-truth 骨架先进，但缺死亡归因工具、无决策直方图、4 个 ab-test 已入库（违反 §0.C）、无三难度门禁。

### II.0.2 v2 设计（三大支柱 + 六条评审决议）

**目标口径**（60-seed 终审）：classic >98%、hard >80%、chaos >50%。

| 支柱 | 解决 | 核心动作 |
|---|---|---|
| **A 决策链评分制** | 补丁叠补丁/顺序即优先级 | think() ~20 分支重构为候选行为评分制（与敌方 evaluateGoals 同构）；M1 外壳用"链序权重+二值得分"保证逐字节不变；之后权重数据可调 |
| **B 战斗感知+自适应** | 僵尸参数/难度盲 | 四层参数（L0 全局基线冻结 / L1 难度增量 / L2 关卡适配 / L3 运行时）；**EnemyModel 敌情感知模型**（评审决议 3 升级：不读难度标签，战斗中感受敌人进攻倾向/配合/纪律） |
| **C 生成地图泛化测试集** | 过拟合镜像 | 冻结确定性生成语料（35 关 × 20 seeds × 3 难度），God AI 向语料达标（评审决议 5：hard > classic > chaos 分期上线） |

**六条评审决议**（2026-08-03 拍板）：
1. M1 允许顺手清理分支内部（拆 M1a 外壳 + M1b 内部清理，每个动作单独过 per-seed-diff）。
2. 僵尸参数移入收纳区，**interface 必须移除**（编译器强制清理引用）。
3. **L1 降级为初始先验**：God AI 不依赖难度标签，主要自适应由 EnemyModel 承担（支柱 B 最大架构变化）。
4. **chaos 命数 1→3**；若仍难达，启用备用档"出生即一星"（playerStartLevel 0→1）。M0 基线必须先应用再测量。
5. 语料 35 关 × 20 seeds，三难度分期上线。
6. M0.5 退役边界：按策略逻辑整合（trapAvoidance→survive 候选、smartThreat→EnemyModel 特征）或清理。

**执行路线图**：M0 测量 → M0.5 退役 → M1 外壳（parity 窗口，唯一允许"重构不改行为"）→ M2 权重 →
M3 行为 → M4 调优 → M5 泛化 → M6 收官。**M1 之前禁止任何权重/行为改动。**

## II.1 M0 测量层 + M0.5 僵尸参数退役（SHIPPED，DECISIONS §96）

一次性落地五项：
1. **chaos 命数 1→3**（`src/config/difficulty.ts`，评审决议 4）——先改配置再测基线；
2. **逐死亡事件 telemetry**：`SimResult.telemetry.deaths[]`（tick/凶手 AI 层级/凶手 kind/当时行为分支 `_lastBranch`），`tank_destroyed` 事件新增 `byId`；
3. **死亡归因工具** `tools/diag/death-attribution.ts`（M0 第一交付物）；
4. **三难度门禁** `tests/god-ai-hard-chaos-gate.test.ts`（hard/chaos 逐关 floor，基于 20-seed 实测容差 4 wins）；
5. **22 个僵尸/否决参数退役**：interface 95→~73，归档 `experimental.ts`（结构化 `ArchivedSelf` 类型，与生产解耦）。

**基线（35×20，命数调整后）**：classic 91.0%（门禁持平）/ hard 38.6% / chaos 34.6%。

**关键发现**：
- **chaos 命数 1→3 几乎无提升（35%→34.6%）**→ 失败是 AI 反复死亡，不是命数紧张（推翻"调命数即可达标"假设）。
- **死亡归因：hard/chaos 各 83% 玩家死亡发生在 dodge 闪避分支**——M3 第一优化靶点。
- **模块增强陷阱（M0.5a）**：归档代码曾用 `declare module './params'` 读退役字段——TS 模块增强程序全局生效，把生产 interface 字段变 optional，破坏 optimize-godai 的 keyof 索引。改为结构化 `ArchivedSelf` 后修复。教训：归档代码绝不通过模块增强触碰生产 interface。

## II.2 M1 决策链评分制外壳（SHIPPED，DECISIONS §99）

新建 `src/ai/god/DecisionCore.ts`（`ActionId` / `ACTION_WEIGHTS` / `DecisionContext` / `Candidate` / `runChain`），
think.ts 顶层 if-else 链替换为「公共前缀外壳 + 8 候选体权重序循环」。候选体 = 原分支**原样转录**，
`evaluate()` 提交即执行（返回 true 当且仅当原分支会 `return`）。权重严格镜像链序：
`dodge(1000) > interceptBase(900) > pickupHigh(800) > aggro(700) > pickupMid(600) > engage(500) > pickupLow(400) > hunt(200)`。

**四条 M1 定理**：① 仅胜出候选的体执行（前置条件求值无副作用 → 选胜者 → 仅胜者体执行，防污染跨 tick 缓存）；② 前置条件精确复制；③ 内部判定 = 二值（M1 不用连续 value/urgency，否则递减权重 + 连续分 ≠ 链式 first-match）；④ 权重 = 链序，early-exit 精确。

**三重验收全过**：① 18 份 per-seed-diff dump（弱关 S2/S23/S27 + 强关 S0/S22/S34 × 3 seeds）全部 IDENTICAL；② split-parity 9/9；③ 三 gate 字节持平 M0（classic 637/700、hard 270/700、chaos 242/700）。性能 +2.6%~+3.3%（5% 预算内）。
**M1 是唯一「重构不改行为」窗口，已关闭**；后续任何改动默认走 60-seed A/B + 官方口径。

## II.3 M2 权重数据化（M2a SHIPPED / M2c 诚实阴性 / M2b 推迟，DECISIONS §100）

- **M2a**：`GodAIParams.actionWeights?: Partial<Record<ActionId, number>>` + `orderedCandidates`
（有效权重降序稳定排序，`GodAIInput.reset()` 预构建，禁止每 tick 排序 AGENTS §14.3）。默认无 overrides = M1 链序（parity 由构造保证）。
- **M2c 诚实阴性**：classic 35×20 官方口径 4 个重排实验全部持平/劣化（hunt↑ -2.7pp / engage↑MID +0.1 / pickupHigh↑ +0.0 / engage↑HIGH -0.6）——**M1 链序是局部最优**，91→93% 需行为改动而非重排。
- **M2b**（selectTarget mini-scoring）推迟：零行为收益 + 高 parity 风险，M4 若出现 chokepoint-vs-hunt 信号再议。

## II.4 M3–M5 行为家族：dodge/站位候选全部阴性（默认 OFF 旋钮保留）

| 里程碑 | 候选 | 实现 | 结果（官方口径） |
|---|---|---|---|
| M3（§97/§98/§101） | dodge 对枪抵消 | `dodgeCounterFire` + `dodgeClearanceScore`；三轮门控（distance / timing-aware pinned / terrain-only pinned） | 官方口径 chaos 34.6→34.1%（持平偏负）；S25 确定性回归 5/20→1/20；**对枪在任何门控下对 chaos 无发布级杠杆** |
| M4（§102） | 带安全门控的紧急对枪 | dodge 分支内 `hasCrossFireBulletImpl` 安全门控 + 近距离对枪 | 修正口径事故后 +0.7pp 噪声内，不发布 |
| M5（§103） | 站位提前规避 | HUNT 候选 `findPathThreat` + `findSafeMoveDir` 换 cell-1 单步 | 触发率 ~1%，classic 0.0 / hard +0.4 / chaos -1.1pp 全噪声，不发布 |

**M3 两轮完整机制记录**：
- **§97 伪影**：A/B 脚本传 `stageIndex`，与 eval-suite/gate 口径不一致，"chaos +3.8pp" 为伪影。官方口径重测 chaos 持平偏负。**stageIndex 伪影完整机制**（§101 实证）：`killScore` 用 `levelFactor(stageIndex)` 缩放 → `dropOnScoreMilestone` 掉落时机改变 → power-up 掉落不同 → world.rng 流分歧 → 整场模拟分歧。
- **§98 Gate 确定性根因**：`bun test` 跨文件共享模块状态，测试突变 DEFAULT 单例污染全局。修复：`GodAIInput` 构造器克隆 `_baseParams` + 门禁传克隆 + 测试显式克隆——**gate 在任何 bun test 上下文下都确定**。
- **§101 机制级结论**：走廊/迷宫关 terrain-pinned 对枪确实保命（+15~25pp）但开阔关站定对枪送死（-10~20pp），净值为零偏负。

## II.5 M6 出生一星（SHIPPED）+ §105 模拟口径三重修复 + M7 追猎探针

### M6 出生即一星（DECISIONS §104，首个 >3σ 发布）

**靶点锁定（全链路数据驱动）**：玩家 **93% 存活时间都是 0★（单发慢弹）是 hard/chaos 打不好的根本瓶颈**。
- 死亡机制探针：死亡时 0★ 占 hard 90% / chaos 88%；追猎途中（≥18 格）死亡 hard 45% / chaos 37%。
- 等级暴露探针（排除伪相关）：存活时间内 0★ 占 hard 93% / chaos 88%——**不是死亡重置等级的伪影，是整局几乎从未升过星**（星掉落期望 ~0.4/局，实际收集 0.23-0.29/局）。
- **60-seed 确认**：hard 36.2→45.3%（**+9.0pp**）、chaos 34.4→42.3%（**+7.9pp**），31/29 关变好。
- 发布：hard/chaos `playerStartLevel` 0→1（§99 评审决议 4 授权的备用档）。门禁真值重生成。
- 方法论固化：**先查星经济/数值配置，再动 AI 行为**——行为改动对 0★ 基础火力下的失败模式几乎无杠杆。

### §105 模拟口径三重修复（DECISIONS §105，2026-08-03）

`tools/sim/simulation-runner.ts` 三处与浏览器路径不一致的 bug：
1. **playerLevel 同步**：此前模拟第一命恒 0★（浏览器第一命 = playerStartLevel），gate/A/B/归因全测的是"第一命 0★、重生 1★"口径。
2. **lives 同步**：此前模拟 hard 恒用默认 3 命，而浏览器 hard 是 **2 命** —— hard 门禁/A/B 全部高估 ~6pp。
3. **telemetry isPlayer 过滤**：`tank_destroyed` 从 `kind==='player'` 改为 `isPlayer` —— 诱饵坦克（视觉伪装 `kind='player'` 但 `isPlayer=false`）此前被误计为玩家死亡，chaos 误捕 201/689（29%）。

修复后门禁真值重生成：hard **48.0%**（2 命真实难度）、chaos **48.9%**（lives 同步为 no-op）。

### M7 追猎死亡探针（DECISIONS §105）：靶点证伪

§96 的「追猎途中死亡 39%」是**误读**——死亡时距基地 ≥18 格中，「真追猎」（死亡前 60 tick 净远离基地）仅
**6.6%（hard）/ 3.9%（chaos）**，**85-93% 是「回防途中」（净朝向基地）**。
- 回防中死亡画像：dodge 分支 83-87%；凶手 tier 均衡；交叉火力 29-35%；**死角（≤2 出口）仅 0.4-1.5%**。
- **SURVIVE 候选**（死角+包围触发）几乎永不触发 → 低杠杆，不投入。
- **survivalRetreat 重新评估为 high-value**：2 命正确口径下 hard 死亡 **82.7-84.0% 发生在最后一命**。
- 真追猎死亡画像：navigate 分支 54-75%、平均等级 0.00-0.13★——0★ 去追猎 = 送死（进一步支持星经济结论）。

## II.6 M8 survivalRetreat（阴性，DECISIONS §106）

M7 重估的 high-value 靶点，官方口径验证：**60-seed OFF 46.3% vs ON 46.1%（Δ-3 持平偏负）**，15 变好 / 17 变差。
**机制低覆盖（先验）**：死亡 83-87% 发生在 dodge 分支，而 survivalRetreat 只挂在 hunt 分支（权重 200，最低）——它只改 navTarget，不改变 dodge 分支的死亡事件本身。
教训固化：**hard/chaos 的死因在 dodge 分支，凡不改变 dodge 分支本身的候选体杠杆都趋零**。

## II.7 M9–M10 horizon 承诺闪避：机制成立但 60-seed 阴性 + 双目标教训

### M9 多弹道生存视界承诺闪避（DECISIONS §107）

**探针证伪原假设**：交叉火力方向误选 ~0%、撞覆盖格 ~0%——「多弹道评分替代二元 isSafeDir」本身无杠杆。
**真杠杆 = 承诺不足（commitment failure）**：起点可闪避 + 从未清带 = hard 31.8% / chaos 35.0% 的 dodge 死亡。
S0 seed2 逐 tick trace：横向子弹 36 tick 前开始逼近，玩家在顶角 ±1px 振荡 30+ tick（flip 计数器永不达 3），
从未垂直移出 32px 命中带——闪避数学完全可行（tArr=36 >> 清带 ~18 tick）却被二元 isSafeDir + base-closer 决胜浪费。

`dodgeHorizonScore`（默认 OFF）：`dodgeHorizonTicksImpl` 对每个垂直候选方向估算生存视界（清带时间 vs t_arrive、
地形受限自由路径钳制、next-cell 交叉火力保守计数，无分配）。**S0 seed2 机制级验证：OFF 死于 tick2158 vs ON 零死亡过关**——
**证明 dodge 分支行为改动确实有杠杆（§97/§101 的「dodge 不可修」结论是修法问题）**。
**但 60-seed 整体阴性**：chaos OFF 47.7% vs ON 44.2%（**-3.5pp**）。S10 seed6 trace 定位根因：ON 玩家 0 死亡却
gameover——tick1822 基地被拆（承诺闪避提升生存但牺牲防守/杀敌效率，敌多时效率损失主导）。
**方法论升级：dodge 行为改动必须双目标评估（生存 + 清关效率），只看 winrate 会掩盖机制。**

### M10 时间余量门控变体（DECISIONS §108）

成本机制修正（修正探针 endFrame bug 后）：M9 全开时 dodge tick +6%，**fireRate 恒低 1-2%**（OFF/ON 相同——
dodge 分支本就极少开火，`shouldFireInDir(moveDir)` 垂直方向无敌人对齐），**真实成本是 dist +25px**（承诺闪避把玩家带离基地/战场）。

门控家族 A/B（全部官方口径）：MARGIN8 20-seed chaos -2.4 / hard 0.0；**MARGIN6 20-seed hard +2.0 / chaos -2.0 →
60-seed hard +1.6pp / chaos -2.4pp**；距离门控（maxDist=8）chaos -4.0pp **有害**。
**双目标机制**：hard（2 命）保命收益 > 效率损失 → 弱正；chaos（3 命、敌多）效率损失主导 → 确凿负。参数全局无法按难度发布。
**可复用信号**：S13 Steel Web 双难度大正（hard +13 / chaos +12）——走廊/窄道关承诺闪避保命收益大。

**dodge 分支第三次同构结论**（§97/§101/§107/§108）：dodge 行为改动（对枪、紧急对枪、horizon、余量门控）在
chaos 上全部无发布级杠杆；引擎方向耦合（移动 = 面朝 = 开火）使「边闪边打」机械上不可行。

## II.8 M11 星经济二星（SHIPPED → 用户否决回退，DECISIONS §109/§110）

**A/B（官方口径）**：60-seed hard **+9.4pp**（46.3→55.7%）、chaos **+7.5pp**（47.7→55.1%）——6-7σ 确凿强信号，
双双破 50% 目标。机制：M7 正确口径显示玩家 78% 存活时间困在 1★（单发慢弹被装甲压制），2★ 子弹提速直接对冲。
实现：hard/chaos `playerStartLevel` 1→2，门禁真值重生成（双 54.7%，floor 357）。

**但用户评审否决**（§110）：「hard/chaos 起始两星，有点儿欺负敌人了」——difficulty 配置影响**人类游戏体验**
不只是 God AI；2★ 起步稀释难度挑战性（与 MANIFEST「尊重原作精神」冲突）。**回退为 1★**，门禁真值回退 §105
（hard 48.0% / chaos 48.9%，floor 310/316）。§109 标记 superseded。

**星经济杠杆边界明确**：0★→1★（M6，+7.9~9.0pp）可；1★→2★（M11，+7.5~9.4pp）不可——**出生星级的合理上限是 1★**。

## II.9 v2 纪元方法论沉淀（纪律升级）

1. **口径纪律（四级）**：
   - **stageIndex**：A/B 必须与 eval-suite/gate 同口径（不传 stageIndex）——stageIndex 进 killScore → 掉落时机 → RNG 分歧（§98/§101）。`tools/optimize/level-sim.ts` 除外（生成地图工具，内部自洽，标注已知偏差）。
   - **字段名**：A/B 脚本传参后必须用 live probe 验证参数真实到达 sim（`godaiParams` vs `godAIParams` 大小写事故让 M4/M5 测了 DEFAULT vs DEFAULT，§103）。
   - **lives/playerLevel 同步**：走 `loadStageData` 直驱的探针必须手动同步 `world.playerLevel` 与 `world.lives`（§105）。
   - **telemetry isPlayer**：死亡归因按 `isPlayer` 过滤（诱饵坦克伪装 kind，§105）。
2. **双目标评估**：dodge/生存类行为改动必须同时看生存（死亡分布）与效率（清关速度/base 防守）——hard/chaos 方向相反是常态（M9/M10）。
3. **20-seed 只配 screening，发布前 60-seed 确认**：M8 +1.1pp、M10 +2.0pp 在 60-seed 下归零/衰减（§98/§106/§108）。
4. **Gate 确定性**：`GodAIInput` 构造器克隆参数 + 门禁传克隆 + 测试显式克隆——任何测试污染共享单例都不会影响门禁（§98）。
5. **先查星经济/数值配置，再动 AI 行为**（M6 教训）。
6. **负结果照常全记录 + 实验旋钮保留**：每个失败候选的机制知识（pinned、horizon、margin）都是后续变体的基础设施。
7. **Gate 真值随口径修复/行为变更重生成**，floor 随收益上调（§105/§109 纪律）。

## II.10 v2 纪元新增基础设施与实验旋钮

**基础设施（SHIPPED）**：`src/ai/god/DecisionCore.ts`（评分制外壳）、`src/ai/god/experimental.ts`（退役归档区，
结构化 `ArchivedSelf`）、`src/ai/god/EnemyModel.ts`（敌情感知，默认 OFF）、`tools/diag/death-attribution.ts`
（死亡归因，含逐死亡事件 telemetry）、`tests/god-ai-hard-chaos-gate.test.ts`（三难度门禁）、
`tests/godai-split-parity.test.ts`（M1 parity 重锁）、探针族 `tmp/probe-chase-death.ts` / `tmp/ab-*.ts`（官方口径模板）。

**实验旋钮**（默认 0/OFF，字节持平，未来变体可复用）：
- dodge 家族：`dodgeCounterFire`（M3）、`dodgeClearanceScore`（M3）、`dodgeHorizonScore`（M9）、
  `dodgeHorizonMinMarginTicks` / `dodgeHorizonMaxDistCells`（M10）。
- 站位/生存家族：`pathThreatAvoidance`（M5）、`survivalModeLives` / `survivalRiskWeight`（M8）、`actionWeights.survive`（M3 survive 候选）。
- 敌情感知家族：`enemyModelMode` / `enemyModelWindowTicks` / `tierWeightScale` / `dodgeRateShrinksT2a` /
  `coordinationRiskWeight` / `enemyAccuracyRaisesSurvival` / `enemyTierWeightCommander` / `enemyTierWeightVeteran`（M3）。
- 权重面：`actionWeights`（M2a，M4 CMA-ES 调优面）。

## II.11 未来探索方向（按已证伪清单过滤）

> 已证伪方向汇总（避免重复投入）：dodge 分支行为（M3 对枪 / M4 紧急对枪 / M9 horizon / M10 余量门控，四次）、
> 权重重排（M2c）、survivalRetreat 回防（M8）、站位提前规避（M5）、出生 2★（M11/§110 用户否决）、
> 前瞻式炮弹规避（Classic 纪元 §68/§69/§68-revisit 系列）。

1. **M4 标量参数 CMA-ES（最优候选）**：`optimize-godai.ts` SEARCH_SPACE 基础设施就绪，目标 = 不改变 game feel 的
   AI 行为参数（hard/chaos 门禁口径为 fitness，60-seed 官方口径验证）。M2c 证明权重重排无杠杆、M3-M10 证明行为改动
   无杠杆后，标量参数是剩余最干净的杠杆面。
2. **生存站位（M7 数据支撑）**：回防中死亡占 85-93%（chase/engage 分支，不碰 dodge）——但 M8 的教训是
   "凡不改变 dodge 分支本身的候选体杠杆趋零"，需先解决 dodge 分支的死亡事件本身或找到 chase/engage 的直接干预点。
3. **重跑死亡归因**（`tools/diag/death-attribution.ts`）：§110 回退 1★ 后确认最新死亡分布，数据驱动选靶点。
4. **Pillar C 泛化语料**（评审决议 5）：冻结语料 35 关 × 20 seeds × 3 难度门禁（hard > classic > chaos 分期上线），
   证明没有过拟合 classic 35——评审已授权，尚未实施。
5. **S13 走廊承诺信号**：M10 发现走廊/窄道关承诺闪避保命收益大（hard +13 / chaos +12），地形条件承诺
   （isTerrainPinned 风格）可复用 M9/M10 的 horizon 基础设施。
6. **道具战术**（Review §4.5）：bomb/freeze 对闪避敌人是唯一稳定清场手段，把 §87 拾取从"顺手牵羊"升级为"战术投资"。
7. **classic 91→98 收官**（Review 路线）：最弱 10 关逐一攻坚（Ice Palace 等），需 120-seed 逐关攻坚——周期最长。

---

# 附：工具链索引（合并）

| 工具 | 用途 |
|---|---|
| `tools/optimize/optimize-godai.ts` | CMA-ES 参数优化（SEARCH_SPACE 机制，v2 纪元 M4 调优面） |
| `tools/sim/simulation-runner.ts` + `sim-pool.ts` / `sim-worker.ts` | 并行仿真（官方口径：不传 stageIndex、同步 playerLevel/lives） |
| `tools/eval/eval-suite.ts` | 全量 A/B（35×60 paired CRN，`--compare a.json b.json`） |
| `tools/eval/validate-p4.ts` / `gate-truth.ts` | 全 35 关扫描终审 / 门禁真值生成 |
| `tools/diag/per-seed-diff.ts` | dump + diff（`--set` 覆盖），单种子 tick 级分歧定位 |
| `tools/diag/flip-scan.ts` | 翻转扫描（FLIP-TO-WIN / FLIP-TO-LOSE / TIED 自动分类） |
| `tools/diag/decision-probe.ts` | 单 tick 完整决策上下文打印 |
| `tools/diag/death-attribution.ts` | **v2 纪元**：逐死亡事件归因（tick/凶手层级/行为分支） |
| `tools/diag/diagnose-s32.ts` / `probe-s32.ts` / `analyze-trace.ts` / `decision-trace.ts` | 失败归因 / 参数敏感度 / 决策追踪 |
| `tests/god-ai-regression-gate.test.ts` | classic 35×20 回归门禁 |
| `tests/god-ai-hard-chaos-gate.test.ts` | **v2 纪元**：hard/chaos 三难度门禁（35×20，floor=truth-3.7pp） |
| `tests/godai-split-parity.test.ts` | **v2 纪元**：M1 决策链 parity 重锁 |

# 附：文献索引

- **权威决策**：DECISIONS.md（编号索引体系；Classic 纪元 §27–§95，v2 纪元 §96–§110 已压缩，全文在本档 / git 历史）。
- **v2 设计文档**：`plan/God-AI-Redesign-Review.md` + `plan/God-AI-Redesign-v2.md` 已于 2026-08-03 删除，
  核心内容归档于本档 **Part II.0**；代码注释中的章节引用指向设计原文（未跟踪文件，git 无法找回，以本档为准）。
- **仍存活的相邻设计文档**：`plan/God-AI-Next-Round.md`（Phase A 已否决，smartThreatModel 冻结）、
  `plan/Automated-Level-Design-and-Simulation.md`、`plan/Lie-Back-Win-Mode.md`（coop 躺赢模式，§79 相关）。
- **性能文档**：`docs/perf-optimization.progress.md`（热路径纪律，AGENTS §14 出处）、`docs/render-optimization.progress.md`、`docs/performance-report.md`。
- **已归档的历史文档**（内容已并入本档，原文用 git 历史找回）：`docs/god-ai-tuning-log.md`、`plan/god-ai.progress.md`、
  `plan/God-AI-Tuning.md`、`plan/god-ai-analysis.md`、`plan/God-AI-Curriculum.md`、`plan/gac.review.md`、
  `plan/God-AI-P0~P3-Verification.md`（4 份）、`plan/God-AI-P3-Direction.md`。
- **每日工作记录**：`.workbuddy/memory/`（2026-07-27 起）。
