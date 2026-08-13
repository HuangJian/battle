# God AI 调校进展总览（系统化整理）

> 汇编自：git commit history、历史日志、各阶段计划/验证文档、`.workbuddy/memory/`（每日工作日志）。
> 整理日期：**2026-08-12**（v2 重设计纪元 M0–M11 收官后体系化重写，含 2026-07-30 首版内容；本次新增 §131–§191 双玩家 Central Breach / 导航 carve-dig 等详细决策，并刷新三难度基线）。
> 此文档为**只读汇总**。DECISIONS.md 为决策索引（编号保留、正文压缩，全文在本档 / git 历史）。
> 三个纪元：**Classic 纪元**（§33–§95，2026-07-27 → 08-01，单一 classic 难度调校）、
> **v2 重设计纪元**（M0–M11，2026-08-03，三难度体系化重设计，见 Part II）+ **§96–§191 详细决策补录**（2026-08-04 → 08-12，含双玩家 Central Breach 取证，见文末「叙事未覆盖条目的精简补录」）、
> **Phase III · Hard 聚焦行为调优纪元**（2026-08-12 起，评估框架见 §0.C：弃"胜率压倒性"，转 hard 难度仿真驱动 + 多指标行为评估）。

> ★ **核心诊断方法论：per-seed tick-diff**（详见 §I.5.1；脚本 `tools/diag/per-seed-diff.ts`）
> 总胜率只告诉你"回归了多少"，per-seed tick-diff 能定位"哪个 seed、哪个 tick、哪个决策"首次分歧。流程：`dump` 逐 tick 紧凑签名（位置 / 方向 / 开火 / 敌数 / 子弹数 / 状态）→ `git stash` 回退改动再 `dump` → `diff` 找第一个分歧 tick。是一切 God AI 调参回归定位的基石；与 `eval-suite.ts --compare`（全量 A/B）+ `flip-scan.ts`（参数翻转扫描）组成诊断工具箱（工具表见 §I.5.1）。

> 🔥 **新阶段（Phase III，2026-08-12 起）：Hard 聚焦行为调优** —— 调优重心从"冲过关率"转向"在 hard 难度上仿真、挖掘并修正不合理行为模式"。胜率仍是**首要指标**（方向筛选与回归定位靠它），但不再是压倒性唯一指标；与击杀数 / 存命数 / 开火命中率 / 中弹数 / 过关时间共同评估 GOD AI 表现。classic / chaos 过关率仅作参考，不出现大幅度倒退即可；chaos 未来还会增强敌人 AI、过关率预期下降属正常（评估框架详见 §0.C）。

---

# Part 0. 当前状态速览（2026-08-12，含 2026-08-12 三难度基线重测，见 §0.A）

## 0.A 三难度基线（官方口径）

> 官方口径 = 35 关 × N seeds，`runSimulation` 直驱（不传 stageIndex、正确同步 playerLevel/lives），
> 门禁 seed 1..20，决定性结论 ≥60 seeds。口径历史与修复见 §II.5。

| 难度 | 20-seed 门禁真值 | 60-seed 参考 | 命数/星级 | 目标 | 状态 |
|---|---|---|---|---|---|
| classic | **88.6%**（620/700，floor 581） | 89.6% | 3 命 / 0★ | >98%（v2 目标） | 门禁全绿（620≥581）；较 §134 91% 微降 ~1.4pp，距目标 ~9pp |
| hard | **72.0%**（504/700，floor 415） | 72.7% | **3 命** / 1★ | >80%（v2 目标） | 门禁全绿（504≥415）；较 §134 63.1% **+8.9pp**，距 80% 目标 ~8pp |
| chaos | **67.9%**（475/700，floor 394） | 68.8% | **3 命** / 1★ | >50%（v2 目标） | 门禁全绿（475≥394）；较 §134 60.0% **+7.9pp**，已超目标 ~18pp |

- classic 门禁真值自 M0 起保持 637/700 字节持平（所有 M 行为默认 OFF / 逐字节不变）。
- §130（2026-08-05）：**全难度命数统一为 3**（relax 5→3、hard 2→3；classic/chaos 已为 3）。
  hard/chaos 门禁真值按 gate-context 重测：hard 54.4%→**61.6%**（全 35 关无回退）、chaos 58.1%→**58.3%**
  （命数未变，7 关 ±1 跨进程 genId 上下文噪声）。此前 hard/chaos 真值在 §105 模拟口径修复后重生成
  （hard 曾被 3 命伪口径高估 ~6pp）；§130 后命数差异不再参与难度区分。
- §134（2026-08-05）：**方向 D 防守位停射拦截 SHIPPED**（defenseInterceptMode=1，pool-only，
  classic restore 0）。hard/chaos 门禁真值按 gate-context 重测：hard 61.6%→**63.1%**（Battlement
  1→3 首次离开地板）、chaos 58.3%→**60.0%**（60-seed 显著 p=0.0087，+2.15pp）。
- 质量门禁：三门禁 + split-parity 12/12 全绿；**891 tests**、0 lint、`bun run build` ✓。
- 2026-08-12 重测（eval-suite v6 官方口径，三难度各 20/60 seed）：classic 88.6%/89.6%、hard 72.0%/72.7%、chaos 67.9%/68.8%。hard/chaos 较 §134 显著 **+8~9pp**（§131–§191 防守位/导航 carve-dig/双玩家 Central Breach 等收益落地）；classic 较 §134 91% 微降 ~1.4pp（20-seed 门禁 620/700 仍 ≥ floor 581，门禁全绿——classic 为 byte-identical 路径，降幅疑为 20-seed 采样噪声，60-seed 89.6% 更稳）。

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
| **§130（2026-08-05）** | **全难度命数统一 3**（relax 5→3、hard 2→3，用户指令） | hard 35×20 54.4%→**61.6%**、60-seed 60.6%；chaos 持平（±1 噪声）；门禁真值重生成 405/382 |
| **§134（2026-08-05）** | **方向 D：防守位停射拦截基地车道敌人**（defenseInterceptMode=1，pool-only） | 20-seed 三臂 +8/+11/+8；60-seed hard +0.76pp（S33 +15pp / Battlement +2.5pp）、chaos **+2.15pp（p=0.0087 显著）**；门禁真值重生成 415/394 |

**诚实阴性（不发布，实验旋钮保留）**
| 里程碑 | 内容 | 结论 |
|---|---|---|
| M2c（§100） | 权重重排 4 实验 | 链序是局部最优，纯重排无杠杆 |
| M3（§97/§98/§101） | dodge 对枪抵消（三轮门控） | 官方口径 chaos 持平偏负；stageIndex 伪影完整机制 |
| M4（§102） | 带安全门控的紧急对枪 | +0.7pp 噪声内；`godaiParams` 大小写口径事故 |
| M5（§103） | 站位提前规避（pathThreatAvoidance） | 触发率 ~1%，无信号；口径纪律再升级 |
| M8（§106） | survivalRetreat 最后一命回防 | 60-seed Δ-3 持平偏负 |
| M9（§107） | dodgeHorizonScore 生存视界承诺闪避 | 机制成立（S1 seed2 反转）但 60-seed chaos -3.5pp；双目标教训 |
| M10（§108） | horizon 时间余量门控（MARGIN6） | hard +1.6pp / chaos -2.4pp，参数全局无法发布 |
| M11（§109→§110） | 星经济二星（playerStartLevel 1→2） | 60-seed +7.5~9.4pp 强信号，**用户否决回退**（"欺负敌人"） |
| §131（2026-08-05） | T8 拦截射程 pool 2→8/12 | 20-seed −10/−13；60-seed paired −0.7pp（p=0.10）净负；机制激活但放弃击杀节奏，不发布 |
| §132（2026-08-05） | 方向 B：威胁评分按 kind 速度×基地逼近加权 | 20-seed w500 −8 / w1000 −20 / w800 −1 全非正；Battlement 全臂不变（1/20→1/20/1/1）；fast 4.5cps > 1★玩家 4.19cps 追不上，威胁块内重排目标无杠杆，不发布 |
| §133（2026-08-05） | 方向 C：brick-heavy 关防守距离再校准（race↑/maxDist↓/M13↓） | 20-seed mild −7 / balance −20 / tight −29 全负；6 目标关全负（S4 Crossfire 13→2/4/0 毁灭性）；「早回防」在 brick-heavy 关系统性有害（M13 ON4@10 教训放大版），不发布 |
| §135（2026-08-05） | 方向 D 预测版：提前拦截基地车道逼近者 | 20-seed p1/p2 +0（逐字节相同，intercept 提交 645=645）、p3 −3；预测判定命中 168 次但全被 base 保护环砖墙的 scan 确认挡掉——无新开火窗口，不发布 |
| §136（2026-08-05） | 方向 D 破砖版：预测命中时打场景砖开路 | 20-seed dig1/p1/p2 +0（逐字节相同，4 关探针 commits 645/791/371/136 全等）、p3 −3；破砖分支零触发——有砖时是保护环（禁打）、没砖时 scan.enemy 已覆盖，不发布 |
| §137（2026-08-05） | 基地守位格：默认防守位 (12,23) 全 35 关都是环砖不可达，计算可站的守位格（Battlement 选 (12,22) 前厅口）作防守锚点 | 20-seed 净 −4（438 vs 442，+10/−11 关互抵）、Battlement 3→2（1-seed 噪声）；探针：站位分布与基线逐格相同、(12,22) 从未被访问——锚点只接「无敌人/紧急/撤退」分支，主力「拦截在防守行」分支不用它，机制几乎未生效，不发布 |
| §138（2026-08-05） | 守位格 v2：base 受威胁且无 clear-shot 敌人时驻守守位格（holdRange 0/6/10） | 20-seed h0 438（−4，≈v1 校验 ✓）/ **h6 432（−10）最差** / h10 439（−3）；Battlement 全臂 3→2/3/2 不动；驻守前厅带被双向射击 + 拖慢击杀（Brickworks/Iron Curtain/Frozen Field/Oasis −4~−5），「站着防守」再证伪（M13 ON4@10 / §133 家族），不发布 |
| §139（2026-08-05） | 方向 A：火力死区解除——无 LOS 时寻有射界的瞭望格重新接战（firingLaneMode，候选 weight 300 于 hunt 前） | 20-seed **m1 292（−150）/ r7 320（−122）/ d2 279（−163）灾难性崩塌**；Battlement 3→2 仍负。「四方向无 LOS」在迷宫关是常态而非死区——门控把正常寻路全误判为死区、不断拉离目标绕路，击杀节奏崩溃（Iron Curtain −12 / Spider −10 / Ice Palace −11），不发布 |

**保留实验旋钮**（默认 0 / OFF，字节持平）：`dodgeCounterFire`、`dodgeClearanceScore`、`pathThreatAvoidance`、
`survivalModeLives`、`survivalRiskWeight`、`dodgeHorizonScore`、`dodgeHorizonMinMarginTicks`、
`dodgeHorizonMaxDistCells`、EnemyModel 族（`enemyModelMode`/`tierWeightScale`/`dodgeRateShrinksT2a`/
`coordinationRiskWeight`/`enemyAccuracyRaisesSurvival`）、`actionWeights.survive`、
`fastBaseApproachWeight`/`fastBaseApproachRangeCells`（§132 方向 B）、
`brickHeavyDefenseWallRatio`/`brickHeavyBaseRaceRangeCells`/`brickHeavyMaxPlayerDistFromBase`/
`brickHeavyFieldDistCells`（§133 方向 C）、`defenseInterceptPredictCells`（§135 方向 D 预测版）、
`defenseInterceptDigBricks`（§136 方向 D 破砖版）、
`baseGuardAnchorMode`（§137 基地守位格）、
`baseGuardAnchorHoldRange`（§138 守位格 v2 驻守范围）、
`firingLaneMode`/`firingLaneRadius`/`firingLaneMinEnemyDist`/`firingLaneReplanTicks`（§139 方向 A 火力死区）。

## 0.C 新阶段评估框架（Phase III · Hard 聚焦行为调优，2026-08-12 起）

> 自 §191 / 2026-08-12 起，GOD AI 调优进入新阶段。本阶段不再以"过关率压倒性"为唯一追求，
> 转而以 **hard 难度仿真**为主战场，系统性挖掘并修正"胜率高但行为不合理"的模式。

### 0.C.1 驱动难度与定位

- **主战场 = hard**：比 classic 噪声更小、比 chaos 更贴近"合理行为"边界，最适合暴露 GOD AI 的异常决策习惯。
- **仿真驱动**：所有行为模式挖掘在 hard 难度 35 关 ×（≥20 seed 筛选方向，≥60 seed 定论）上进行，沿用官方口径（`runSimulation` 直驱，35×N，门禁 seed 1..20）。
- **诊断基石**：仍用 per-seed tick-diff（§I.5.1）定位"哪个 seed / 哪个 tick / 哪个决策"首次分歧——行为不合理必先定位到具体 tick 才能改。

### 0.C.2 评估指标体系（多指标共同评估，胜率为首要非唯一）

| 指标 | 角色 | 说明 |
|---|---|---|
| **过关率（win rate）** | **首要指标** | 方向筛选与回归定位的主依据；不再是"压倒性唯一"，高胜率不再自动等于"调好了" |
| 击杀数（kills） | 行为质量 | 是否主动、有效地消灭威胁；过低提示回避/保守过度 |
| 存命数（lives 残留） | 行为质量 | 收尾是否稳；过低提示"惨胜"或拖入多敌人混战 |
| 开火命中率（fire hit rate） | 行为质量 | 开火是否精准、是否无谓浪射；低命中提示瞄准/时机逻辑异常 |
| 中弹数（hits taken） | 行为质量 | 是否无意义挨打；过高提示走位/闪避/掩体利用不合理 |
| 过关时间（clear time） | 行为质量 | 是否拖沓；过久提示犹豫/绕路/占位不当 |

- **核心判读**：优先关注"胜率高但多维指标异常"的组合（如击杀偏低却靠命数硬扛、中弹数过高、过关时间过长）——这类即"不合理行为模式"，是 Phase III 的修正对象。
- 指标来源：仿真 telemetry / scorecard（`eval-suite.ts --compare` 可并排 A/B 对比上述指标）。

### 0.C.3 参考难度口径（不追求提升，只防大幅倒退）

- **classic / chaos 过关率仅作参考**：保持不出现**大幅度倒退**即可，不要求提升、不作为本阶段优化目标。
- **chaos 预期下降属正常**：未来会**增强敌人 AI**（更聪明/更激进的敌方行为），chaos 过关率下降是设计内预期，不计入"回退"判定。
- classic 因走 byte-identical 路径，仍作"无意外行为变更"的稳定锚（门禁真值不应无故波动）。

### 0.C.4 阶段纪律（沿用）

- 决定性结论仍须 **≥60 seeds**（20-seed ±11pp 二项噪声只筛方向，见 §I.1 测量纪律）。
- 所有改动仍以"不泄漏到 SP / 不冻结某失败种子当硬门槛 / 确定性 byte-identical 架构保证"三条回归线把关（见 working memory 验收须知）。

---

# Part I. Classic 纪元（§33–§95，2026-07-27 → 08-01）

## I.1 目标与评价体系

- **最终目标（P4 用户指令）**：全 35 classic 关，逐关过关率稳定 > 60%（floor），且平均 > 80%。✅ 已达成并超出。
- **延伸目标（Round 5）**：S33 Diamond > 80% @120 seeds。✅ **85.0% 已达成**——靠 §47 仿真层碰撞修复（原"结构性差距"实为碰撞语义 bug + 归因污染）。
- **测量纪律**：20-seed 探针有 ±11pp 二项噪声，只用于筛选方向；**一切决定性结论必须 ≥60 seeds**（P4 教训，多次证伪过 20/30-seed 的"海市蜃楼"增益）。

## I.2 时间线总览（Classic 纪元）

| 阶段 | 日期 | commit | 主题 | 关键成果 |
|---|---|---|---|---|
| 基础设施 | 07-27/28 | `62d1270`…`5378f8f` | GodAIParams 参数化 + CMA-ES 优化器 + 决策追踪 | 基地存活 40%→80%（stage 0），胜率 0% |
| Round 2 | 07-28 | `6d44663` 等 | Classic 模式适配，13 项根本性修复（§33） | 基地存活 100%，胜率仍 0%（全 timeout） |
| Round 3 | 07-28 | `28683be` | 分阶段验证框架（curriculum）+ S7 参数化 + hasBase 守卫 | 失败归因可见化；canHunt 阈值接参 |
| 重构 | 07-28 | `0d3275b` | GodAIInput 拆分为 `src/ai/god/*`（纯重构，parity 验证） | ThreatAssessor / FireControl / StrategyPlanner / Navigator |
| v3/v4.1 | 07-28/29 | `f010823`, `fd126ae` | CMA-ES v3→v4.1 + 回归门禁 | 胜率 20% 封顶，基地存活 97.5% — 证明参数空间已穷尽，需改架构 |
| **P0** | 07-29 | `aec21f4` | T2a 死锁修复（§41） | S1 20%→70%，S2 22.5%→87.5% |
| **P1** | 07-29 | `2cedb7a` | 生存与防御修复（§42） | S1 87.5%，S2 92.5%，gameover 清零 |
| **P2** | 07-29 | `6780322` | 反驻扎区域 + 卡死兜底 + 预判射击（§43） | S2 100%，S4 50%→66.7% |
| **P3** | 07-29 | `c01985f` | A* 拆砖寻路 + 中心死锁修复 + 多关 CMA-ES（§44） | S10 0%→80%⭐，35 关均值 51.7%→53.9% |
| **P4** | 07-29 | `2d9fa77` | 7 轮 floor-aware CMA-ES + 逐关覆盖表（#36） | 均值 **81.9%**@60 seeds，34/35 ≥60% |
| **Round 5** | 07-29 | `49b1011` | S33 贴身缠斗 `t2aMaxRange=2`（§43-S33） | S33 43.3%→**72.5%**@120，均值 86.9%@20 |
| **Phase A** | 07-30 | `7435089` | 智能基地威胁模型（§44-SmartThreat） | **负结果**：8+ 变体全否决，基础设施保留默认 OFF |
| **§47** | 07-30 | — | 基地保护环碰撞修复 + base_destroyed 归因（§47） | S33 72.5%→**85.0%**@120，35×60 真值 81.9%→**87.7%**，门禁真值重生成 |
| **§48** | 07-30 | (已回退) | 假闪避地形遮挡"修复" | **负结果**：S33 -10pp@120，闪避地形盲是承重行为，测试锁定 |
| **§49** | 07-30 | (已回退) | 炮口相向火后闪避 | **负结果**：35×20 A/B -2.6pp，S19 -25pp、S29 -15pp |
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
| P0（§41） | T2a 仅当 `scan.enemy==true` 才驻车；反驻扎计时；卡死逃逸向地图中心 | 旧代码对着墙无限开火不前进（单种子 5900 tick 空转） | S2 22.5%→87.5%（单项最大杠杆） |
| P1（§42） | 闪避对齐阈值 12px→32px；`baseUnderThreat` 提前到 row≥18；受威胁时无条件回防 | 闪避阈值窄于坦克判定箱 → 撞向检测不到的子弹 | S1 70%→87.5%，S2 gameover 清零 |
| P2（§43） | 驻扎判定改 ±1 格区域；卡死兜底任意可通行方向；`predictEnemyCrossing` 预判横穿射击 | 精确格匹配被 32↔40px 振荡打败，逃逸从未触发 | S2 100%，S4 50%→66.7% |
| P3（§44-P3） | **A* 拆砖寻路**（brick 可通行 5× 代价）；followPath 对砖开火；中心附近卡死改追最近敌 | A* 视砖不可通行 → 密砖关永远找不到路（S10 瘫痪根因） | **S10 0%→80%**，多关 CMA-ES 首次防单关过拟合 |

P3 重要否决：**漫游约束（回防软约束）引发负反馈循环**（约束移动→杀敌少→漏敌多→更多 gameover）——此教训在 Round 5 / Phase A / M8 反复重现。

### I.3.3 P4 战役：全 35 关 floor-aware 调优（2026-07-29）

- **7 轮 CMA-ES**（IPOP，15-worker 池，fitness v5.0 = 逐关胜率块 + deficit×8000 floor 惩罚），决策全部 60 seeds 复核。
- **两大方法论发现**：① 单一全局参数集无法满足 35 关（失败家族需求相反）；② 20-seed 探针会选中海市蜃楼（S33 单关 60%@30 seeds 复测仅 43%）。
- **解法：逐关参数覆盖表**（后经 §58/§81 泛化移除）。每条覆盖须 ≥60 seeds 与无覆盖对照验证。
- **定稿**：均值 81.9%@60 seeds；34/35 ≥60%；回归门禁重写为全 35 关。

### I.3.4 Round 5：S33 贴身缠斗（2026-07-29）

- **用户洞察**：4 血重甲远程对射极低效，贴身 2 格弹道 ≈0、0.5s 击毙 —— **8 倍效率**。
- **实现**：`t2aMaxRange`（默认 15）S33 覆盖设 2 + 辅助参数。
- **成绩**：S33 43.3%→**72.5%** @120；35 关均值 86.9%@20。
- **7 个否决方案**（全部 ≥60 seeds）。共同教训：**任何打断重甲击杀的干预都是净负**。

### I.3.5 Phase A：智能基地威胁模型（2026-07-30）—— 高价值负结果

- **假设**：`isBaseUnderThreat()` 类型盲+地形盲是 S33 剩余 base_destroyed 尾部的根因。
- **实现**：`src/ai/god/SmartThreatModel.ts` + 7 个新参数全默认 OFF。
- **结果：8+ 变体 @120 seeds 全部否决**（最差 −20pp）。
- **三条诊断发现**：① S33 基地杀手归因被 §47 推翻（killerKind 口径 bug）；② 贴身缠斗极脆弱；③ **瓶颈是响应时间不是检测**（15/19 拆家发生在前 3000 tick）。
- **处置**：S33 覆盖回退；基础设施保留默认 OFF；计划文档加"已实测否决"横幅。

## I.4 Classic 纪元重要实验详情

### I.4.1 §47 基地保护环碰撞修复（S33 破局点）

`bulletHitsTerrain()` 在同一 tick 内子弹可穿过保护砖命中基地（含玩家自毁）。修复后 S33 85.0%@120、base_destroyed 21→6、35×60 真值 81.9%→**87.7%**。**修复根因不是 God AI 策略，而是仿真层碰撞语义。**

### I.4.2 §48 假闪避遮挡：负结果 → §48-revisit 钢墙专用通过

原 §48（遮挡砖）S33 **-10pp**@120 —— 地形盲闪避实为**有效的预判闪避**（砖几 tick 内被打穿）。`tests/threat-assessor.test.ts` 锁定行为，未来想"修"必须先过 S33@120 + 35×60 A/B。
§48-revisit 只遮挡钢墙（临时性低）+ 钉死位门控 + `brickWallRatio<0.10` 地形门控（只对 S7/S33 钢迷宫关启用）：**35×60 net +1 flip、0 关回退**，默认 ON。

### I.4.3 §49 炮口相向火后闪避：负结果 → §49-revisit 对枪抵消通过

"火后立即垂直闪避"实现有根本缺陷（打断优先级链、冰面失控、打断击杀循环），35×20 A/B **-2.6pp** 回退。
§52 v2 改为 **T2a 内联对枪抵消**（相向敌人开火时开火抵消敌方子弹）：35×120 **+5 wins**。
§49-revisit 参数化（`counterFire` 默认 1 + `counterFireMaxRange` 默认 5）：**35×60 net +3 flips、0 ON→OFF 负翻转**，默认 ON。对枪价值不随地形分界。

### I.4.4 §68-§69 交叉火力感知实验系列：全部负结果（默认 OFF）

- **§68-v2**（时间感知路径威胁投影 `findPathThreatImpl` + `findSafeMoveDirImpl`）：60-seed **-1.1pp**。迷宫关 -15pp（diversion 代价高）、开阔关 +12pp。**检测正确但 diversion 响应在迷宫中有害——子弹安全 ≠ 位置安全。**
- **§69-A**（地形门控）：S2 改善 +7pp 密度 37% > S7 回退 -15pp 密度 27% —— **地形密度无法区分好坏关**。
- **§69-B**（A* 威胁成本）：cost=3.0 时 -1pp、cost=1.0 时 -6pp。
- **§68-revisit**（4 变体 per-seed tick-diff 重调优）：raw -18 / 提前量上限 -25 / 开阔度门控 -14 / 组合 -25 全负；增益与损失共享同一触发（坏翻转 12.6-23.1t 过早转向 vs 好翻转 8.3-8.4t），**不存在干净判别量**。
- **核心结论**：任何形式的前瞻式炮弹规避（post-hoc diversion 或 A* 威胁成本）都是净负 —— 反应式闪避已足够好，路径偏离代价 > 炮弹风险。基础设施完整保留（M5 曾复用）。

### I.4.5 §70 基地环开火保护（修复 coop 自杀 + V8 JIT 热循环敏感性）

coop 模式 T2b 导航开火绕过 T6 基地保护检查 → 玩家打掉自家基地保护砖。修复：**不在热循环里做 baseSteel 检测**（steel 分支只做赋值，循环后一次性带状检查）。发现并记录 **V8 JIT 敏感性**：热循环里加 no-op 代码会改变 JIT 优化决策导致行为差异——热循环改动必须 per-seed 对比验证。60-seed A/B：suite 0.7254→0.7291，零净回归。

### I.4.6 §79 coop God AI 误读 w.player（躺赢模式 P2 修复）

`src/ai/god/` 7 处误读 `w.player`（P1）而非 `self.controlledTank(w)`（P2），导致 P2 重生后卡出生点并打穿基地墙。修复后单人逐字节不变，coop 过关率 100%、P2 平均剩余命 -7.63→+2.90。

### I.4.7 §80 冰冻窗口转身抖动守卫（aimTurnSnapGuard）

**根因**：转向不是免费的——`updateMovement` 换轴时 snap 垂直坐标，非网格对齐坦克一转身边缘被推 ≤CELL/2 px，目标甩出 scanAhead 偏移线；aggressive 分支无反驻车守卫。**修复**：commit 停火转向**之前**用转身后位置重跑扫描，假瞄准 → 落入 navigate。**35×60×2 终验**：冰冻击杀 coop +136%、single +157%；single 过关率 +0.9pp（net +20）；≥5pp 回退 0 关。S33 10/30-seed 回退在 60-seed 确认为种子噪声（Δ0.0pp）。

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
3 轮 35×60 调优（per-seed 定位 S20/S27/S33 机制）→ 终值 suite +0.0010（p=0.30 无显著差异）→
**120-seed 确认（S7/S17/S33 全 ≥ 持平）→ 用户拍板启用**：`chokepointMode` 默认 1，门禁真值重生成。

### I.4.12 §95 转弯周期限制 50→100ms（SHIPPED 默认，DECISIONS §95）

用户指令：player/enemy 转弯周期限制改为 160ms ≈ 360 APM 超级人类水平。**35×60 全档扫描**：
50ms 基线 91.0% → 160ms 原始 87.4%（net −75）→ 160ms+原地等待 89.8%（net −24）→ 50ms+等待 88.9%（net −44）→
**100ms+等待 91.2%（net +5，SHIPPED）**。per-seed 定位漂移致死（cooldown 期间沿旧方向滑行 → 改为原地等待）；
AI 转向承诺锁尝试净负回退。110/125/140ms 细粒度确认 100ms 是邻域局部最优。门禁真值重生成（floor 610→612）。

## I.5 方法论沉淀（Classic 纪元，v2 纪元继承）

1. **60-seed 规则**：20-seed ±11pp 噪声只配筛方向；决定性结论必须 ≥60 seeds（S33 用 120）。
2. **Trust-but-verify**：每轮他人/上轮报告的数字先独立复跑再采信。
3. **参数门控默认 OFF**：新行为一律 `param=0` 默认关闭，OFF 时逐字节不变 → 回归门禁天然守护其余 34 关。
4. **Data over code**：逐关差异走覆盖表/`computeStageAdaptedParams` 特征适配，不写关卡特判代码（§81 起禁逐关覆盖表）。
5. **负结果照常提交并全记录**（DECISIONS §44-SmartThreat 是范本）：基础设施可复用，诊断数据扭转方向。
6. **回归门禁随收益上调 floor**，禁止静默降低。
7. **警惕负反馈循环**：一切"约束移动保基地"的方案（P3 漫游约束、Round 5 守卫带、Phase A skipT2a、M8 survivalRetreat）都因同一机制失败——约束→杀敌少→漏敌多→更多失败。
8. **per-seed tick-diff 诊断法**（见 §I.5.1）+ **V8 JIT 敏感**：热循环改动必须 per-seed 对比验证。

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

**三重验收全过**：① 18 份 per-seed-diff dump（弱关 S3/S24/S28 + 强关 S1/S23/S35 × 3 seeds）全部 IDENTICAL；② split-parity 9/9；③ 三 gate 字节持平 M0（classic 637/700、hard 270/700、chaos 242/700）。性能 +2.6%~+3.3%（5% 预算内）。
**M1 是唯一「重构不改行为」窗口，已关闭**；后续任何改动默认走 60-seed A/B + 官方口径。

## II.3 M2 权重数据化（M2a SHIPPED / M2c 诚实阴性 / M2b 推迟，DECISIONS §100）

- **M2a**：`GodAIParams.actionWeights?: Partial<Record<ActionId, number>>` + `orderedCandidates`
（有效权重降序稳定排序，`GodAIInput.reset()` 预构建，禁止每 tick 排序 AGENTS §14.3）。默认无 overrides = M1 链序（parity 由构造保证）。
- **M2c 诚实阴性**：classic 35×20 官方口径 4 个重排实验全部持平/劣化（hunt↑ -2.7pp / engage↑MID +0.1 / pickupHigh↑ +0.0 / engage↑HIGH -0.6）——**M1 链序是局部最优**，91→93% 需行为改动而非重排。
- **M2b**（selectTarget mini-scoring）推迟：零行为收益 + 高 parity 风险，M4 若出现 chokepoint-vs-hunt 信号再议。

## II.4 M3–M5 行为家族：dodge/站位候选全部阴性（默认 OFF 旋钮保留）

| 里程碑 | 候选 | 实现 | 结果（官方口径） |
|---|---|---|---|
| M3（§97/§98/§101） | dodge 对枪抵消 | `dodgeCounterFire` + `dodgeClearanceScore`；三轮门控（distance / timing-aware pinned / terrain-only pinned） | 官方口径 chaos 34.6→34.1%（持平偏负）；S26 确定性回归 5/20→1/20；**对枪在任何门控下对 chaos 无发布级杠杆** |
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
S1 seed2 逐 tick trace：横向子弹 36 tick 前开始逼近，玩家在顶角 ±1px 振荡 30+ tick（flip 计数器永不达 3），
从未垂直移出 32px 命中带——闪避数学完全可行（tArr=36 >> 清带 ~18 tick）却被二元 isSafeDir + base-closer 决胜浪费。

`dodgeHorizonScore`（默认 OFF）：`dodgeHorizonTicksImpl` 对每个垂直候选方向估算生存视界（清带时间 vs t_arrive、
地形受限自由路径钳制、next-cell 交叉火力保守计数，无分配）。**S1 seed2 机制级验证：OFF 死于 tick2158 vs ON 零死亡过关**——
**证明 dodge 分支行为改动确实有杠杆（§97/§101 的「dodge 不可修」结论是修法问题）**。
**但 60-seed 整体阴性**：chaos OFF 47.7% vs ON 44.2%（**-3.5pp**）。S11 seed6 trace 定位根因：ON 玩家 0 死亡却
gameover——tick1822 基地被拆（承诺闪避提升生存但牺牲防守/杀敌效率，敌多时效率损失主导）。
**方法论升级：dodge 行为改动必须双目标评估（生存 + 清关效率），只看 winrate 会掩盖机制。**

### M10 时间余量门控变体（DECISIONS §108）

成本机制修正（修正探针 endFrame bug 后）：M9 全开时 dodge tick +6%，**fireRate 恒低 1-2%**（OFF/ON 相同——
dodge 分支本就极少开火，`shouldFireInDir(moveDir)` 垂直方向无敌人对齐），**真实成本是 dist +25px**（承诺闪避把玩家带离基地/战场）。

门控家族 A/B（全部官方口径）：MARGIN8 20-seed chaos -2.4 / hard 0.0；**MARGIN6 20-seed hard +2.0 / chaos -2.0 →
60-seed hard +1.6pp / chaos -2.4pp**；距离门控（maxDist=8）chaos -4.0pp **有害**。
**双目标机制**：hard（2 命）保命收益 > 效率损失 → 弱正；chaos（3 命、敌多）效率损失主导 → 确凿负。参数全局无法按难度发布。
**可复用信号**：S14 Steel Web 双难度大正（hard +13 / chaos +12）——走廊/窄道关承诺闪避保命收益大。

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

## II.8b §111 星盾全难度 + §112 M12 HP 感知（阴性）+ §113 M13 全场压力撤退（SHIPPED，2026-08-04）

- **§111 星盾全难度扩展（引擎，用户拍板）**：3★ 星盾从 classic-only 扩展到所有难度（移除
  `SimulationCombat` 的 difficultyKey 守卫，classic 字节不变）。HP 模型探针（35×20）：磨血死亡
  hard 70% / chaos 67%（≥3 发）、危险区时间 19-20%、3★ 存活时间仅 1.3-1.6%（星盾触发 10-15/700，
  win 影响噪声内）。hard/chaos 真值重生成 341/700（48.7%）。
- **§112 M12 玩家 HP 缓冲感知（诚实阴性）**：dodge 分支 HP 自适应 commit 余量（danger 放宽 / trade 加严，
  5 参数默认 OFF，pool-only）。20-seed delta ≤0.6pp 噪声；60-seed hard +2.3 / chaos -2.2 反向抵消
  （horizon 基底签名非 M12）。**第三次证伪 dodge 分支行为族**（M3/M9/M10/M12 同一 hard+/chaos- 签名）。
- **§113 M13 全场压力撤退（SHIPPED，首个无 chaos 负向机制）**：死亡场景探针（telemetry 新增
  hp/liveEnemies）揭示主导死法 = 敌满编（70-73%）+ 深入 >20 格（39%）+ 1★（80-85%）→ 磨血死亡。
  `outnumberedFieldRetreat`=1 / 3 只 / 15 格（pool-only）：`selectTarget` 在全场敌人 ≥3 且距基地 >15 格
  时回防守位。A/B：20-seed hard +2.7 / chaos +2.6pp；60-seed hard +2.3（2.1σ）/ chaos +0.6pp——双目标
  （死亡↓、基地失守↓）双难度全改善，**无 chaos 负向**（此前所有 dodge 机制都有）。ON4@10 反向有害
  （-5.3pp 过于被动）。M5 pathThreatAvoidance 口径事故纠正后重测：hard +0.4 / chaos -0.8pp 确认中性
  偏负不发布。门禁真值：hard/chaos 341→**360/359（51.4%/51.3%）**，floor 315→**333**；classic 不动。

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

> 已证伪方向汇总（避免重复投入）：dodge 分支行为（M3 对枪 / M4 紧急对枪 / M9 horizon / M10 余量门控 /
> M12 HP 门控，五次）、权重重排（M2c）、survivalRetreat 回防（M8）、站位提前规避（M5，口径纠正重测
> 后确认中性偏负）、出生 2★（M11/§110 用户否决）、前瞻式炮弹规避（Classic 纪元 §68/§69/§68-revisit 系列）。

1. **M4 标量参数 CMA-ES（round-2 SHIPPED，DECISIONS §115）**：`optimize-godai.ts` SEARCH_SPACE 20 个参数。
   首轮（6 关子集）全量 60-seed 双难度劣化（-0.9/-3.0pp）——子集过拟合（§114）。
   **round-2 全 35 关 × 10 seeds × 20 代**（fitness 即官方口径，天然规避过拟合）：hard 51→59% / chaos 50→58%（训练集）。
   60-seed 交叉验证 HARD_BEST 双向强信号（hard 53.2 / chaos 56.9）→ 发布 14 参数（replan=1 / threatRange 23 /
   campTimeout 20 / baseRace 18+2 / defenseColSpread 3 / endgameThreshold 10 / M13 阈值放宽等），
   **剥离 game-feel 参数**（aimError / suboptimalPathProb 保留默认——搜索噪声，剥离后 54.0/56.5 增益保持）。
   pool 模型专属：新增 `CLASSIC_MODEL_PARAMS` 还原表（classic instant 实测 -2.4pp，还原保 91% 门禁字节）。
   发布路径验证：hard 53.0 / chaos 56.6 / classic 91.2（60-seed，DEFAULT 默认参数）。
2. **生存站位（已交付 M13，§113）**：全场压力撤退（3 只 + 15 格）已发布并提升 hard/chaos。下阶段可探索
   M13 参数边界（M4 CMA-ES 加入 SEARCH_SPACE）或「回防路径安全」（navigate 分支回防时用
   findPathThreat/findSafeMoveDir 规避交叉火力，M5 基础设施可复用）。
3. **重跑死亡归因**（`tools/diag/death-attribution.ts`）：§110 回退 1★ 后确认最新死亡分布，数据驱动选靶点。
4. **Pillar C 泛化语料**（评审决议 5）：冻结语料 35 关 × 20 seeds × 3 难度门禁（hard > classic > chaos 分期上线），
   证明没有过拟合 classic 35——评审已授权，尚未实施。
5. **S14 走廊承诺信号**：M10 发现走廊/窄道关承诺闪避保命收益大（hard +13 / chaos +12），地形条件承诺
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


---

# 追加：2026-08-07 HARD 瓶颈轮——selectTarget 决策层四连阴性（§168–§171）

> 目标：HARD 35×120 全关 >50%、平均 >90%。本轮基线 hard 76.2%（3200/4200，35×120 口径；
> 35×20 口径 77.3%）。91% 败局 = base_destroyed。本轮对「selectTarget 决策质量」层做
> 系统性清算，四个方向全部阴性，**该层已确认碰顶**。

## 实验链（全部 DECISIONS.md 有完整条目）

| 编号 | 方向 | 结果（hard 35×120 除非注明） | 结案 |
|---|---|---|---|
| §168 | navStuckZone 卡死区检测 | 35×20 净 +5 不显著（z=0.30），窗口臂负 | 阴性，旋钮默认 0 |
| §169 | threatStickyTicks 威胁信号粘滞 | 120 臂净 −9（z=−0.73）/60 臂净 +1 | 阴性，旋钮默认 0 |
| §170 | huntCommitTicks 追击承诺 | 35×20：120 臂 71.9% 净 −38 **显著负**（z=−2.97）/30 臂中性 | 阴性，旋钮默认 0 |
| §171 | pathTargetMode 路径长度感知目标选择 | 四臂：全评分净 +36（z=0.45）、bonus×4 净 −15、最小介入净 +1、dig 惩罚 1000 = 全评分 | 阴性，旋钮默认 0 |

**SHIPPED（本轮唯一正结果，前轮交付）**：guard-only superItemMode（§167，hard z=2.14 显著，chaos 中性）。

## §171 细节（路径距离分歧探针 tmp/probe-pathdiv.ts）

- 信号真实：败局中曼哈顿选中目标的平均路径超支 20.8 格（胜局 3.3，6.3×）；S15/S11/S10 avgGap 117–230。
- 但 35×120 呈**关卡级 churn**：迷宫/多墙关改善（S20/S31 −10、S3 −8、S7/S28 −7），开阔关退化
  （S12/S22 +10、S29 +9、S24 +6），正负抵消。
- 退化关（S12/S22/S24/S29）恰是扇区错位假设的显著关——防御型关的胜利依赖守位而非追猎，
  路径重排把玩家拉离防御扇区。此反证为扇区感知防御预置提供了额外证据。

## 合并教训（决策层碰顶论证）

1. 反应式逐-tick 决策链**本身是适应性优势**（§170：承诺窗锁死过时目标，base_destroyed 143→173）。
2. 信号连续性、目标承诺、选择度量三类「决策平滑/修正」全灭——败局的 navigate 超支（73.8% vs 59.1%）
   是击杀吞吐螺旋的**果**，不是决策质量的因。
3. 防御分支在位 87.6% 仍救不回（§169 归因）：瓶颈在防御激活后的**击杀转化**与全局**掉落经济**
   （75% 败局无星、道具生成 531 vs 948）。
4. 开局 12s 胜败完全同构（probe-opening.ts：first kill 7.3 vs 7.95s 败局反而领先）——分歧在中盘。

## 下一杠杆候选（按期望排序）

1. **扇区感知防御预置**：S24/S12/S26 错位 42–53% + §171 反证（这些关正是路径重排退化关）。
   定向修复，预期 +1–2pp，证据链最完整。
2. **防御模式击杀转化**：防御分支激活后 closer-tick 仅 3%——需要剖析防御中玩家的射击/走位
   低效根因（对齐 lane 预置？拦截点选择？）。
3. 掉落经济为击杀螺旋的果，直接干预（道具生成率）属游戏规则改动，暂缓。

工具留档：tmp/probe-pathdiv.ts（分歧率/代价）、tmp/probe-opening.ts（开局同构）、
tmp/s171-ab-{off,on,b4,min,v3}.json（四臂语料，35×120）。


---

# 追加：2026-08-07 §172 bonusHuntBias 阴性结案——目标选择层封盘（五连阴性）

> 掉落经济杠杆的最后一根：把 bonus 敌人（唯一道具来源）追猎偏置从硬编码 −2
> 参数化为 4 / 6。35×120 双臂 vs OFF 基线 76.2%：
> bias=4 → 75.4%（净 −33，z=−1.13）；bias=6 → 74.9%（净 −55，z=−1.73）。
> **剂量-反应单调为负**，且 20% 对局被重排——不是噪声，是真实有害。

## 结论

1. 追远端 bonus 的走位代价 > 道具收益。base_destroyed 仍 ~91% 败因，
   拉走玩家放空的防御缺口比掉落补不回。
2. 掉落经济缺口（败局生成 531 vs 胜局 948）是击杀螺旋的**果**：杀得少 →
   bonus 少 → 道具少 → 火力弱 → 杀得更少。目标选择层无法打破此环。
3. **五连阴性（§168 navStuckZone / §169 threatSticky / §170 huntCommit /
   §171 pathTargetMode / §172 bonusHuntBias）正式封盘 selectTarget 层**：
   平滑化、承诺、度量修正、经济偏置四类干预全灭。逐-tick 自由最近敌评选
   是适应性优势而非缺陷。

## 下一杠杆（离开目标选择层，进入攻防结构层）

1. **扇区感知防御预置**：S24/S12/S26 错位 42–53% + §171 反证（开阔/防御型关
   在路径重排与 bonus 追猎下均一致退化，证明这些关的胜利依赖守位）。
2. **防御模式击杀转化**：防御分支激活后 closer-tick 仅 3%，拦截点/射界
   效率待剖析。

工具留档：tmp/cmp172.cjs（配对翻转 + 关卡 churn 对比）、
tmp/s172-ab-{b4,b6}.json（双臂语料，35×120）、
tests/godai-bonus-hunt.test.ts（机制 6 测试，旋钮默认 2 byte-identical）。


## §173 基地损伤召回（baseDamageRecall）— 阴性结案（2026-08-07）



**杠杆重估（五连阴性后）**：逐关缺口量化（OFF 35×120，tmp/stagegap.cjs）——

S34 Battlement 14%（17/120）唯一 <50%（需 +43 胜）；S20 58%、S8/S26 57%；

到 90% 需 +580 胜。候选方向①「扇区感知防御预置」被 §137/§138/§142 历史

直接证伪（驻守族全灭），不重测。



**hp-leash 探针（tmp/probe-hpleash.ts，S34 120 局确定性重演）**：

- 口径事故与修正：前两轮探针因 runner 门控错误（`world.gameState` 恒

  undefined，须用 `world.state`）+ stageIndex=33（官方口径为 0——stageIndex

  喂 killScore 并实质改变胜负）产出无效数据，曾误判「0/120 局跌入低血量

  区」。修正后胜负重演 mismatches=0。

- 修正后数据推翻旧阴性：低血量区（≤30）胜局 1/17 vs 败局 63/103 进入，

  但进入→死亡窗口中位仅 0.8s（太迟）；**上游触发点=基地首次受伤**：

  胜 10/17 vs 败 103/103；受伤时玩家距基地中位 10 vs 25 格；受伤→终局/

  死亡窗口 29.2s vs 5.1s；败局可召回人群（受伤+玩家>8格+窗口>5s）37/103。



**实现**：`isBaseUnderThreat()` 新增事实性分支——baseHp < baseMaxHp 且

（arm g12：玩家距基地 >12 格）即返回 true，复用全部既有防御级联，不新写

行为代码。旋钮 baseDamageRecall 默认 0（byte-identical），classic/guard

均 0；tests/base-damage-recall.test.ts 7 测试全绿；check 门禁绿

（1197 pass，唯一失败=replays/ 空目录既有例外）。



**两臂 35×120 筛选（基线 3200/4200）**：

| 臂 | 胜率 | 翻转 L→W/W→L | 净 | z |

|---|---|---|---|---|

| arm 1（无条件） | 75.6% | 135/159 | −24 | −1.40 |

| arm g12（距离门 12） | 75.4% | 111/146 | −35 | −2.18（显著负） |


---

## 叙事未覆盖条目的精简补录（2026-08-12，来自 DECISIONS.md 迁入块）

> 以下 § 在原叙事 Part I/II 中无对应摘要，按 Decision/Rationale 首行精简保留，完整取证见 git 历史。

## 剩余未封盘杠杆

1. **防御模式击杀转化**：防御分支激活后 closer-tick 仅 3%，拦截点/射界
   效率需 per-tick trace 级剖析（投入大，未启动）。
2. **逐关深潜型结构修复**：历史唯一正结果族（§146 S8 口袋、§152 S12、

## 71. §48-Revisit: Steel-Only Evasion Occlusion, Terrain-Gated (SHIPPED)

**Decision:** The original §48 terrain-occlusion evasion was rejected (-10pp S33, brick+steel both occluded). The revisit ships a **steel-only** occlusion **gated to steel-maze stages** (`evasionSteelOcclusionBrickRatio: 0.1`, auto-enabled in `computeStageAdaptedParams` when `brickWallRatio < 0.10`):
1. `findMostDangerousBulletImpl` skips enemy bullets whose path to the player is blocked by STEEL — but only when the player is NOT pinned (≤2 open directions). Brick is never occluded (dodging brick-blocked bullets is load-bearing anticipatory dodge — the original §48 lesson).
2. The terrain gate is the key discriminator: brickWallRatio, NOT steel ratio, predicts the mechanism's value. S27 Brick Maze has MORE steel (26%) than S33 Diamond (18%) yet regresses while S33 gains.
3. A re-ranking guard (`nearestBlocked < bestDist → null`) was prototyped and **removed** — its motivating case (S27) is gated OFF, and it cost ~0.8pp on S33 (+3.3 → +2.5 @120 on same seeds).

> 状态： (SHIPPED)

## 72. §49-Revisit: 炮口相向对枪抵消 Parameterized + Re-Validated (SHIPPED, default unchanged)

**Decision:** The retained §49-family behavior (§52 v2 对枪抵消 — facing-enemy counter-fire + keep-alignment, inline in T2a) was parameterized as `counterFire` (default **1** = current shipped behavior, byte-identical) + `counterFireMaxRange` (default 5 = the original hardcoded 5-cell range), then re-validated on the current tree (post-§47/§58/§48-revisit) with the same per-seed methodology as §48-revisit:
1. `counterFire: 0` → plain pre-§52 T2a (turn to face + fire, no facing-enemy special-casing) — the A/B OFF arm.
2. Default stays **ON** (1): the A/B shows counter-fire is a clean positive on the current tree, so flipping it OFF would lose S27/S21 wins. `SKILLED_HUMAN_PARAMS` inherits it automatically (derived from `DEFAULT_GOD_AI_PARAMS`).
3. Per-seed byte-identity (the §70 JIT-sensitivity check): the parameterization's ternary + `counterFireMaxRange * CELL` hot-path shape change is byte-identical to the committed hardcoded baseline — S27 seed-41 and S21 seed-60 dumps (committed vs param-default) both **IDENTICAL**.

> 状态： (SHIPPED)

## 73. §68-Revisit: Crossfire Awareness v2 Re-Tuned with per-seed tick-diff (REJECTED, stays OFF)

**Decision:** The user directive re-processed §68-v2 (crossfire awareness, default OFF since its original -1.1pp) with the per-seed tick-diff method. The re-tune confirmed the negative result at mechanism level and **shipped nothing** — all four fix variants were net-negative, and the experiment code was reverted (src/ byte-identical; crossfire stays OFF per "基础设施保留默认 OFF" policy):
1. **A/B reproduction on the current tree**: 35×60 OFF 89.0% vs ON 88.1% (-0.9pp, 138→156 paired flips, net -18) — matches the original -1.1pp.
2. **Per-seed mechanism (cf-trace, GodAIInput subclass)**: bad flips (S27/S7/S15) fire on threats 12.6-23.1 ticks out (premature perpendicular commitment off the A* path into death); good flips (S29/S28) fire at 8.3-8.4 ticks (imminent escape). The reactive dodge handles 12-23t threats fine — the crossfire diversion is redundant early and deadly when it commits the wrong way.
3. **Variant 1 — lead-time cap** (`crossfireThreatTicks=10`, only flag bullets arriving within 10t of NOW): net -25. Helped mazes (S27 -12→-7, S7 -10→-7, S32 -8→-2, S31 -7→-5) but destroyed open-stage gains (S29 +7→-3, S33 +5→-2, S2 +5→+2). Chain-breakage: S29-15's escape needed a SECOND 31.7t-lead diversion at tick 3700 that the cap suppressed → the whole win chain collapsed.

> 状态： (REJECTED)

## 74. Steel-Fire Gate: Never Fire at Unpierceable Steel to Break Through (SHIPPED)

**Decision:** New param `steelFireGate` (default **1** = ON; 0 = OFF = byte-identical pre-§74). When ON, the two navigate **break-through** fire sites in `think()` (aggressive navigate + T2b navigate) — which fire WITHOUT calling `shouldFireInDirImpl` — apply the same T11 steel gate that `shouldFireInDirImpl` already enforces: steel blocks fire while `p.level < STEEL_PIERCE_PLAYER_LEVEL` (3). Implemented as `steelFireBlockedImpl` (the T11 predicate) + `shouldFireBreakThroughImpl` (steel gate + §70 base-ring guard) in `FireControl.ts`, used at both sites.
**Rationale:**
- User report (2026-08-01): "player 不具备破钢能力时，不要射击钢铁障碍物来试图开路" — the AI fired at indestructible steel to open a path, wasted the bullet cap, then camped at the wall for the full camp timeout, cutting combat efficiency.
- Root cause: T11 lives in `shouldFireInDirImpl`, but the break-through sites bypass it entirely, firing at whatever blocks the move direction — including steel.

> 状态： (SHIPPED)

## 75. §75: Distance-Aware Base-Wall Fire Guard (T2a/Aggressive Suicide Fix)

**Decision:** The §70 base-ring fire guard protected `shouldFireInDirImpl` and the two break-through fire paths, but the T2a (stop-and-aim) and aggressive-mode fire paths bypassed `shouldFireInDirImpl` entirely — firing directly when `scan.enemy` was true, without checking `scan.baseWall`. Because `scanAheadImpl` uses two independent offset scan lines, one offset can find a base-protection brick (`baseWall=true`) while the other finds an enemy (`enemy=true`). The T2a path fired whenever `scan.enemy` was true, destroying the player's own base. This caused 4 `killer=player` base-destruction failures in S33 Diamond (120 seeds: 26, 34, 78, 82).
The fix has three parts:
1. **`scanAheadImpl` (FireControl.ts)**: New `baseWallDist` field — stores the step count when a base-protection brick or 'base' (eagle) terrain is found. Initialized to `Infinity`. Set alongside `baseWall=true` for both 'brick' and 'base' terrain cases.
2. **T2a and aggressive-mode entry guards (GodAIInput.ts)**: Changed `if (scan.enemy)` to `if (scan.enemy && !(scan.baseWall && scan.baseWallDist <= scan.enemyDist) && !(scan.baseSteel && (p.level ?? 0) >= 3))`. This prevents firing only when the base wall is **closer than or at the same distance as** the enemy — the 6px bullet spans both offset columns and WILL hit a closer base wall before reaching the enemy. If the enemy is closer, the bullet hits the enemy first, so firing is safe. This distance-aware check avoids the over-conservative regression of a blanket `!scan.baseWall` check (which prevented valid shots at enemies behind the base wall and caused +12 lives_exhausted on S33).

## Lie-Back-Win-Mode (Coop God AI)

| Decision | Detail |
|----------|--------|
| Q1–Q10 sign-off + hidden-state compliance | `plan/Lie-Back-Win-Mode.md` |

## 75. Replay Recording Must Tap the Decorated Input (Lie-Back-Win-Mode desync) (SHIPPED)

**Decision:** The recorder always taps `simulation.input` / `simulation.input2` —
the exact objects the preceding `tick()` consumed — never the raw `Input` /
`godInput` fields. This is decoration-agnostic: any future input decorator is
recorded correctly by construction.

> 状态： (SHIPPED)

## 76. The Packed Blob Is the Only Authority on Frame Schema (SHIPPED)

**Decision:** the packed blob's **leading byte** is the single authority on
layout. Everything else is descriptive and must agree with it.
- `SUPPORTED_FRAME_SCHEMA_VERSIONS` + `isSupportedFrameSchema()` (`config.ts`) —
readers accept every schema this build can decode, not just the newest.

> 状态： (SHIPPED)

## 77. Playback seek must advance the input (drag-the-bar desync) (SHIPPED)

**Decision:** the fast-forward loop must replay frames `0..targetFrame-1` exactly
like `update()` does — `simulation.tick()` then `this.input.advance()` — so the
world lands on the true timeline at the seek target and resume is seamless.
- `seekTo()`: dropped the pre-seek `input.seekTo(targetFrame)` (the fresh

> 状态： (SHIPPED)

## 78. Seek catch-up must drain (discard) world events — no audio burst (SHIPPED)

**Decision:** a silent catch-up must drain events every tick — mirrors what the
render loop does, we just don't play them. The world state is already updated by
`tick()`, so discarding the observation events is safe and keeps both audio AND
presentation backlogs empty after seek (no stale particle/flash burst either).

> 状态： (SHIPPED)

## 82. 督战模式（Supervise）— God AI 作为 player1 全程无人类输入 + 战斗速率快捷键 (SHIPPED)

**Decision:** 新增「督战模式」：与躺赢模式（coop）同源但反其道而行——God AI 控制 **player1**（`GodAIInput` 默认 `controlledTank = w.player`），人类键盘完全脱离游戏输入（`simulation.input = godInput`，`input2 = null`）。`world.spectate` 标记（World 字段 + 快照序列化 + 回放 metadata）。督战与躺赢互斥（`requestSpectateToggle` 开启时先退出 coop，反之亦然）。督战局不存最高分（延续 Q4：无人类参与的成绩不入榜）。
**Rationale:**
- MANIFEST §2.1 One-Author：与 coop 完全同构——`requestSpectateToggle` 走 `simulation.requestSpectateToggle` 延迟到 `updatePlaying()` 首 tick 应用；Game 在 menu/paused 时立即应用（与 `pendingCoopToggle` 同款）。
- AGENTS §2.2 No Hidden State：`spectate` 是 World 字段，快照/回放可复原；恢复（recovery restore）时若快照带 spectate 而 `godInput` 已清，重建 God AI（镜像 coop 的 §3.8 路径）。

> 状态： (SHIPPED)

## 89. §89: Close-range enemy exposure check — don't flee from point-blank enemies (SHIPPED)

**Decision:** New params `closeCombatDangerCheck` (default **1** = ON; 0 = OFF) and `closeCombatDangerRange` (default **2** = point-blank, 32px). When ON, after the navigate branch determines `_moveDir`, `closeCombatExposureImpl` checks: is there an enemy within `range` cells, aligned (same row/col, within TANK px), with no wall between (scanAhead finds enemy), AND the player's moveDir is the OPPOSITE of the enemy's direction (fleeing)? If so, cancel the move — face the enemy and fire instead.
**Critical design choices (discovered via A/B testing):**
1. **Perpendicular moves are safe** — the initial implementation caught ALL non-toward moves (including perpendicular dodges), causing -1.7pp regression. Fixed: only `moveDir === opposite(enemyDir)` (fleeing) triggers the check. Perpendicular moves are dodges and are always safe.
2. **Range 2, not 4** — at range 4, the check fired too often, cancelling legitimate navigation (retreating to defend, repositioning). A/B at 35×20: range 4 = 91.0% (-1.6pp), range 2 = 93.0% (+0.4pp), range 1 = 92.9% (+0.3pp). Range 2 is the sweet spot — the enemy is truly adjacent (32px), where fleeing is almost certainly death.

> 状态： (SHIPPED)

## 90. Dodge Direction Persistence + Threat Hysteresis (Bug Fix)

**Decision:** Two fixes for player evasion failures found in `classic-s12-died-l0-t43-seed1322088985.replay`:
1. **Dodge direction persistence** (`dodgeDirectionImpl`): when the same threat bullet persists across ticks, return the last dodge direction if it's still `canMoveDir` + `isSafeDir`. Prevents the 1px oscillation where `canMoveDir` or `isSafeDir` flips at the sub-cell boundary, causing the dodge direction to reverse every tick (e.g., up→down→up→down, making the player effectively stationary at y=55↔56 while the bullet approaches and hits).
2. **Threat hysteresis** (`findMostDangerousBulletImpl`): for the recently-dodged threat bullet (`b.id === _lastDodgeThreatId`), widen the alignment threshold from `< TANK` (32) to `< TANK + 2` (34). Prevents the threat from flickering between detected/not-detected at the exact boundary (|bcy-pcy| = 31 vs 32), which caused the player to alternate between dodge and navigate branches every tick. New threats still use the standard `< TANK` threshold.
**Rationale:**

## 90b. §90 A/B Test Results — Oscillation Counter-Fire Shipped (Negative Results Recorded)

**Decision:** After 35×60 A/B testing, only the **oscillation counter-fire** (threshold=3) ships ON by default. Hysteresis, persistence, and floorSnap are all OFF — each caused net regressions.
**A/B Results (35 stages × 60 seeds, classic, 18000 ticks, all params=0 for baseline):**
| Approach | Net Delta | Worst Stage | Shipped |
|---|---|---|---|

## 91. Turn Cooldown (§90c) — Simulation-Layer Oscillation Prevention

**Decision:** Added `turnCooldownMs` (default 50ms ≈ 3 ticks at 60fps) to `GameplayRules` — enforced in `SimulationCombat.updateMovement()`. After a tank turns (dir changes), it must wait `turnCooldownMs` before turning again. During the cooldown, `tank.dir` is reverted to `tank.prevMoveDir`. This blocks per-tick direction oscillation at the simulation layer (the source), rather than patching it in the AI layer (§90).
**Implementation:**
- `rules.ts`: `turnCooldownMs: 50` in both `DEFAULT_RULES` and `RULES.classic`.
- `types.ts`: `Tank.prevMoveDir?: Direction` and `Tank.lastTurnFrame?: number` fields.

## 92. §87: Urgent Power-Up Pickup Priority — Close + Safe-Path Pickups Outrank Defense/Kill (SHIPPED)

**Decision:** User directive (2026-08-02): "炸弹/冰冻/护栏 8 格内、星星/加命/护盾 4 格内、船 2 格内且路径安全时，拾取优先级 > 回防/杀敌；然后全 35 关仿真验证，下降严重的用 per-seed tick-diff 分析处理。"
New `think()` branch placed AFTER dodge (survive) and T8 (in-flight bullet aimed at the base — an immediate loss) but BEFORE aggressive/T2a/S5: a power-up within its category range AND with a safe path diverts the player immediately, overriding stop-and-aim kills and base-defense repositioning. Normal mode only (during freeze, the aggressive branch already grabs pickups when no enemy is aligned, and an aligned frozen enemy is a free kill not to interrupt).
**Params (SHIPPED defaults):** `pickupPriorityMode=1`, `pickupPriorityHighRange=8` (bomb/freeze/fence + modern emp/guard), `pickupPriorityMidRange=4` (star/tank/shield + remaining modern items), `pickupPriorityLowRange=2` (boat), plus three safety gates discovered by the tuning loop:
1. **`pickupPriorityMaxDanger=0`** — route danger (enemies strictly BETWEEN player and item, `calculateRouteDanger`) must be 0.

> 状态： (SHIPPED)

## 116. 自杀秒回（suicide quick-return）：实现 + 诚实阴性（2026-08-04）

**Decision:** 新增 God AI 决策候选 `suicideReturn`（DecisionCore `ActionId` 权重 1100，高于 dodge 1000），默认 **OFF**（`suicideReturnMode=0`，字节持平）。当 5 个前置条件同时满足时，player 站立饮弹、无闪避、立即在出生点重生以处理基地威胁。新文件 `src/ai/god/SuicideReturn.ts`；新参 `suicideReturnMode / BulletTimeTicks(60) / EnemyDistTicks(300) / MinLives(2) / SpawnDistCells(6)`。
**5 前置条件（对应任务）：** ①敌人处威胁点（可直击基地）；②出生点能处理该敌（0-1 转直击 或 出生点比 player 当前位更近）；③player 库存命数充足；④player 全速亦需 >5s 才够到该敌；⑤1s 内被致命弹命中（扫描所有敌方子弹，非仅最近 ctx.threat）。
**验证（60-seed A/B，hard 全 35 关逐档）：**
- 无 base 威胁守卫版本（仅按任务 5 条）：hard 子集 **net -1 flips**（S24 seed14 回归）——player 在基地其实不会沦陷时盲目自杀换命属浪费。per-seed tick-diff 定位：OFF 臂（不自杀）dodge+存活并胜出，ON 臂自杀丢命后仍保不住基地。

## 117. 自杀秒回条件①变体（mode 2 STAND / mode 3 CHARGE）：诚实阴性（2026-08-04）

**Decision:** 按取证建议重启 §116——把触发条件从条件⑤（濒死）改挂到条件①（敌人进入威胁点），新增两个变体：`suicideReturnMode=2`（STAND：站立等弹，超时 `suicideReturnStandMaxTicks=300` 兜底 + 超时后 `_suicideStandSuppress` 防重提交）与 `=3`（CHARGE：不闪避、直线冲锋威胁敌）。均**保留**基地活跃子弹守卫（S24 修复）。默认仍 OFF（mode=0，字节持平）。新增参数仅 `suicideReturnStandMaxTicks`。
**实现要点：**
- 健康 player 无法靠「站立饮弹」快死（pool 229HP 需 2-3 发），故 mode 2 用超时兜底、mode 3 主动赴死——两者都避免 §116 S31 站立冻结病理（单元测试抓到 mode 2 超时后立即重提交的二次冻结，用 suppress 修复）。
- 执行中交易用弱检查 `anyThreatPointEnemyImpl`（仅条件①）而非全量前置——冲锋/站立中途不会因 player 已拉近距离而中止。

## 118. §117 守卫升级（baseHp 阈值 + 防守位失守）A/B — 仍为诚实阴性，机制性证伪（2026-08-04）

**Decision:** 按根因修复方向（守卫只验证了「有一发弹在飞」，未验证基地真会沦陷），为 mode 2/3 增加两个严格死局守卫参数（默认 0，字节持平）：`suicideReturnBaseHpFrac`（基地 HP ≤ 该比例 × baseMaxHp 才触发）与 `suicideReturnDefendDistCells`（player 距基地超过该格数=防守位失守才触发）。A/B 工具新增 `--strict` 臂 D（mode2+strict）/ E（mode3+strict），参数可调（默认 0.5 / 8 格）。
**验证（120 seeds × 35 关 × {hard, chaos}，5 臂 42000 sims，36000 ticks）：**
- 触发率降约 38%：hard 378→236 runs、chaos 539→350 runs——满血基地 + 有防守的假阳性被过滤（§117 hard S35 s8 的 FLIP-TO-LOSE 已消失）。
- 但净翻转**未转正**：hard D +1（1胜/0负）/ E +0（0/0）；chaos D −1（0/1）/ E −2（0/2）。跨难度净 = D 0、E −2。B/C 复现上一轮数字逐位一致（确定性）。

## 119. 固化策略调试方法论：run-forensics 分层取证（2026-08-04）

**Decision:** 把本次自杀秒回调试沉淀为可复用取证工具链：跑 20/60/120-seed 仿真时，除胜率外必须能产出分层细节数据，用于理解详情/找规律/定位瓶颈。新增：
- `tools/sim/simulation-runner.ts` 增加 `forensics: true` 选项（默认关 → 逐字节不变），每次运行返回 `RunForensics`：
1. 终局快照 `terminal`：player 命数/HP（可承受打击数，vs 100 基准）/距基地/星级/存活、base HP（可承受打击数）/护墙完好数、**每个存活敌人**（type/HP/距 player/距 base/AI tier）、**每发在飞敌弹**（位置/方向/距 player/距 base/ETA/命中数经济学 hitsToDie）；
2. 失败前 10 ticks 行动+生效规则日志 `lastActions`（每 tick：branch 候选/_moveDir/_fire/位置/HP/命数/基地 HP）；

## 120. 自毁基地 32 局取证 + 采集脚本迭代（off-by-one / bullet-dir / --from-json）（2026-08-04）

**Decision:** 用 §119 的 run-forensics 采集 hard/chaos 120 seeds 全部自毁基地局（hard 14 / chaos 18 = 32 局，0.33%/0.43%），并按调试过程迭代采集脚本三轮：
1. **shot 事件 off-by-one 修复**：bullet_fired 事件在 `input.endFrame()` 之后才被消费，此前读到的 branch/dir 是**下一 tick** 的状态（S6 s43 的致命下射被记成左射）。修复：在 `sim.tick()` 后立即快照本 tick 决策态（fxTick），事件处理用快照。
2. **朝向改取子弹真实弹道**：tank 转弯当帧的 `tank.dir` 会偏离子弹轴向（S33 s81 致命左射记成朝上）——shot 事件的 dir/towardBase 改用 `e.bullet.dir`（地面真值）。修复后**致命一枪指向基地区比例 29/32 → 32/32（100%）**。
3. **--from-json 子集重跑**（本次用户方法论要求）：迭代调试重跑失败局时，**只跑前期已识别失败的 (difficulty, stage, seed) 组合**，不再全量 stage×seeds（本次验证：32 局 2.1s vs 全量 8400 局 ~4min；确定性 ⇒ 复现同一失败清单）。

## 121. t2a/aggressive 停射自毁守卫 selfFireBaseGuard SHIPPED（2026-08-04）

**Decision:** §120 取证根因（t2a 81% 直射基地区、护墙已破缺口）的修复：新增 `selfFireBaseGuard`（0=OFF / 1=strict / 2=lenient），默认 **2**（lenient，120-seed A/B 胜出），classic 经 CLASSIC_MODEL_PARAMS 还原 0（§115 纪律，字节持平）。
**机制：**
- `shotReachesBaseImpl`（FireControl.ts）：沿子弹**真实中心线**（6px 弹道，非 scan 的 ±8px 偏移线）做地形行走——环砖/环钢 STOP（安全）、非环钢 level<3 停、非环砖犁穿、base 格或 2×2 基地区矩形重叠（含 3px 边缘擦碰，hard S16 s82）→ true。坦克**故意不算遮挡**（敌人可闪避，正是 §120 机制）。
- 守卫挂在三处：ENGAGE(T2a) 停射、AGGRO 冻结窗停射、`shouldFireInDirImpl`（aggressive navigate fall-through 开火入口）。strict(1) 一律抑制；lenient(2) 仅当无敌人身体重叠 6px 走廊（±19px 带）时抑制——保住贴脸重叠击杀。

## 140. 方向 D4：baseWall 精确环判定（破砖开火假阳性修复，SHIPPED，2026-08-05）

**Decision:** 新增 `baseWallExactRing`（DEFAULT **1** = SHIPPED；classic 经 CLASSIC_MODEL_PARAMS
restore 0）。scanAheadImpl 的基地保护砖判定从「baseWallScanRadius×≤2 带」松散矩形改为**精确
环格谓词**——与 `SimulationCombat.isBaseProtectionCell` 逐字一致（row 23 cols 11-14 + cols
11/14 rows 24-25 共 8 格）。这是机制级 bug 修复，不是调参旋钮。

## 141. D2 拆环威胁评分 —— 诚实阴性（旋钮默认 0，byte-identical）

**Decision:** 实现并测量 `defenseBreachBonus`（Battlement 探索 D2）：新增静态谓词
`canBreachRingFrom`（敌人与 8 个环格之一对齐、中间无砖/钢、且该环格仍是砖——其下一发子弹
就拆环），接入 `selectTargetUncached` 基地威胁评分为加分项，评分随环完整度下降而上升
（×1 满环 → ×1.875 仅 1 砖）。默认 0 = OFF。A/B：hard 60-seed **基线 6/60 (10.0%) vs

## 143. D5 基地火力解锁 + 星经济 —— 诚实阴性（firingLaneBoxRow / pickupStarBoxRow 保持 0）

**Decision:** 实现并测量 D5：① **死区重定向限定基地盒**——§139 FIRING_LANE 候选叠加
`pc.row >= firingLaneBoxRow`（目标 20）门控；② **星经济豁免**——`pickupStarBoxRow` 开启时，
基地盒内（row ≥ 20）star/tank 道具绕过 §87 近敌门与路线危险门（两门在 4 敌常驻下永远挡路，
D4 前 star 0.07/run 即此病因）。A/B（臂 = firingLaneMode=1 + firingLaneBoxRow=20 +

## 144. E1 道具经济（危急道具拾取）—— 诚实阴性（direItemMode 保持 0，反证判据收束）

**Decision:** 实现并测量计划的最后一块板子 E1（bomb/freeze 清环前带、fence 补环）：新增
`findDireItemTargetImpl` + `direItemMode` 旋钮——基地危急态（敌人 swarm 在
`direItemApproachCells` 6 格内且 ≥`direItemMinEnemies` 3，**或**环砖 ≤`direItemRingLow` 4）
时，10 格内（`direItemRangeCells`）的 bomb/freeze/fence/emp 无视 §87 近敌门/路线危险门优先

## 145. S24 冰面机制深潜 + iceGlideControl —— 诚实阴性（旋钮保持 0，S24 = 难度地板关）

**Decision:** 实现并测量 S24（Labyrinth 迷阵，全关最差：hard 43.3% / chaos 36.7%）的冰面滑行控制旋钮
`iceGlideControl`（+`iceGlideMinSpeed` 0.3）：HUNT navigate 段在冰上滑行中（|v|≥阈值）若目标方向与
滑行轴反向，先松键（null）让滑行以 ICE_DECEL_TRACTION 自然衰减，替代当前「反向倒车」制动。纯函数
`iceGlideAdjust`（Navigator.ts）+ 8 个单测锁定。A/B（60-seed）：S24 hard 26→21（−8.3pp）、chaos 22→24

## 146. S8 Riverbed 取证深潜 + defensePosStandable —— SHIPPED（集合点可达性修复，hard 45%→52%）

**Decision:** S8 远位弃守型败局（hard 45% / chaos 44%，败时距基 23.7 格）三层根因定位后，实现并发货
`defensePosStandable`（+`defensePosStandableMinDist`=8）：默认防守位 (12, 24−offset=1) = **(12,23) 在全部
35 关上都是环砖格**（§137 注释已承认），corridor 与 breakBrick A* 到砖格目标均返回空路径 → 紧急防御/
§113 场退/§88 回防的路由全部失效，玩家只能 directMove 盲目破砖（S8 实测 pocket→(12,23) corridor=0

## 147. S8 三杠杆 B/C/A 逐一 A/B —— B SHIPPED（§146 已记），C/A 诚实阴性（§146 C 范围限制 + A 全局崩盘）

**Decision:** S8 三层根因（阈值空档 / 集合点不可达 / pickup 劫持回防）对应三杠杆逐一 A/B 收束：
C（fieldRetreatPickupGate）与 A（maxPlayerDistFromBase 26→20）均**诚实阴性，不发货**；B
（defensePosStandable，§146）已 SHIPPED。C 的实现与谓词保留（`isFieldRetreatConditionImpl` 成为 M13
判定单一来源，selectTarget 与 PICKUP_HIGH 共用），A 无实现（纯参数探针）。

## 148. fieldRetreatPickupGate 扩展到 MID/LOW —— 实测证伪后回退（HIGH-only 定稿，§147 范围锁定）

**Decision:** 审查建议的「补全拾取劫持防线」（把 §146 C 门控从 HIGH tier 扩展到 MID/LOW）经 120-seed
权威口径 A/B 实测**证伪并回退**：门控保持 HIGH-only，MID/LOW 恢复 byte-identical，新增 scope-lock 测试
（「MID tier is NOT gated」）+ 注释补全双难度证据。
**Rationale:**

## 149. defensePosStandable 全面启用（minDist 解除）全关验证 —— 边际 ≈ 0，不发货（收窄版 §146 保持最优）

**Decision:** 按 §146 的「发货需全关验证后统一启用」承诺，解除 `defensePosStandableMinDist` 门控
（=0，近基 idle 也启用 standable 回退）做全关 120-seed hard+chaos 扫描（fresh 语料 fx-bfull-arm，
8400 runs）。结论：**全面启用相对收窄版（minDist=8）边际 ≈ 0**（hard +0.1pp / chaos +0.2pp），且引入
hard 回归面——**不发货，minDist=8 收窄版保持为最终配置**。

## 150. 关卡序号统一为 1-based（工具 CLI + 文档 S# 全量修正，2026-08-05）

**Decision:** 全仓库统一关卡序号为 **1-based**：`S1`=Outpost … `S33`=Diamond、`S34`=Battlement、`S35`=Final Redoubt（即 `STAGES[n-1]`）。所有接受关卡选择的 CLI 工具（`--stages`/`--stage`/位置参数）改为 1-based 解析，所有 `S#` 输出标签、文档（DECISIONS/docs/plan）与测试注释同步 1-based。
**Rationale:**
- 原状割裂：取证工具（run-forensics/ab-fire-guard/ab-suicide-v2/base-loss-forensics，§119-§121 起）已用 1-based，其余工具与文档用 0-based——`--stages 33` 与文档「S33」指向不同关卡（33→Diamond vs S33=Battlement）。
- 1-based 与用户直觉（第 33 关 = S33）及 `StageData.id`（本已 1-based）一致。

## 152. hard S12 Lattice 回放四联 bug 修复（§152-W1..W4）+ 全关 A/B 验证（SHIPPED）

**Decision:** 从浏览器回放 `hard-s12-base-l2-t138-seed934391936.replay`（S12 Lattice hard，gameover@8272 基地被毁）定位四个 God AI 行为 bug，全部修复并加单元测试，随后在 hard 全 35 关 × 60 seeds 配对 A/B 验证。**发货配置：W1（`t2aSteelPathBlock=1`）与 W2（`aggNavStuckTicks=120`）ON；W3（`pickupCommitTicks`）默认 0（实验旋钮，实测净负，不发货）；W4（decoy 出生点）为纯 bug 修复无条件发货。**
### W1 — 停瞄被半格钢铁路径阻挡仍开火（0:59-1:01，t3540-3660）
- **症状：** player 停在 (17,18)（中心 x=288 恰在 col-17/18 分界线上）向 (17,3) fast enemy 停瞄开火；扫描 ±8px 偏移线看到敌人，但子弹真实 6px 盒 [285,291] 在 rows 8-9 夹住 steel col 18 [288,304) 并死在行 9——火力被浪费且持续空射。
- **根因：** T2a/aggressive 停瞄门只查 baseWall/baseSteel（§74/§75），从未验证子弹真实中心线是否被非环钢铁阻挡。

## 153. hard S12 Lattice seed 3214953618 回放两行为（bullet-crash + close-combat trade）诊断与修复（实现 + 单测锁定；A/B 发现两者全局非正 → 实验旋钮不发货）

**Decision:** 接手用户回放 `hard-s12-base-l3-t106-seed3214953618.replay`（S12 Lattice hard，seed 3214953618），定位上报的两个 God AI player 行为，各自修复并补单测，再在 hard 全 35 关 × 60 seeds flip-scan A/B 验证。**两者均为默认 OFF（0）的实验旋钮 `bulletLaneWait`（W1）与 `closeCombatDuel`（W2）：单元测试锁定机制正确、且对上报事件有效，但 60-seed 全关实测——W1 净负、W2 中性——与 §48/§103 家族结论一致（dodge/近身微调在 hard 全关净非正），故不提升为默认。**
### W1 — player 主动撞上一颗下穿子弹（0:26，t1599）
- **症状：** hard S12 回放 0:26（t1599）：player 在左走廊 (1,9) 侧移/turn-snap 时左缘从 x=24 瞬时弹到 x=16，撞进 col-0 一颗正在 `down` 下穿的敌弹（盒 x≈[13,19]，y 恰好经过 body），hp 315→187，且 `threat` 当时为 null。
- **根因：** `findMostDangerousBulletImpl` 用**中心对齐 + `approaching`（中心未越过）**判定威胁。该弹：竖直中心已越过 player 中心 y（故 `approaching=false`）、且位于**相邻列**（中心 x 偏移 24px < TANK 但盒不真正重叠）——中心检测结构性漏报。真正触发是 player 侧移/转弯把 body 送进该弹车道。

## 154. bulletLaneWait W1 重设计（§153 后记）：18 个净负种子根因定位 + predictive next-body 最终版（实测 35×60 hard 净 +15；仍为实验旋钮默认 0）

**Decision:** §153 的 W1（expanded-box ±margin 判定）在 hard 全关 60-seed sweep 净负，本轮逐种子定位全部 18 个 to-lose 根因，并完成 4 轮设计迭代，最终版为 **predictive next-body + 排他 AABB + marginPx=1 + turn-cooldown 门控**（`bulletLaneClearImpl`，ThreatAssessor.ts；think.ts 接线）。实测 hard 35 关 × 60 seeds **净 +15（39W/24L）**；焦点组（S1/6/9/12/13/33）净 +7（10W/3L，S12 34→38/60）。**维持默认 0**（0 = byte-identical；发货需先经全量 sweep 复核并接受 3 个已文档化残余翻转）。
**Rationale:**
- **18 个净负种子根因（全部 per-seed 定位）：** 每个 to-lose 的首分歧 = B 侧 `moveDir=-`（hold）而 A 照常移动，结局 A clear → B gameover。分类：S9 全部 8 个 + S13 全部 6 个 + S12 s36/s52/s56 探针 = **垂直于移动方向的弹**（crossfire 关站桩 = §48 假规避致死）；S12 s1 = **同轴但 turn-cooldown 本会放行**的弹（过等 ~7 tick）；S9 s24@705 = **hold 吞掉本应「转身开火」的枪**（hold 位于 fire 决策之前）。→ 旧判定（任何弹在 margin 内即等）结构性误报。
- **t1599 复现证明问题真实：** HEAD 下 t1598 玩家 (23.6,144) dir=left moveDir=up、b#201 down (13,160.4)；predictive next-body（moveDir 一步 + off-axis snap(CELL)，与 SimulationCombat axis-lock 同款）[16,48]×[142,174] 与弹盒真实重叠 → 最终版正确拦截（单测锁定）。

## 155. bulletLaneWait W1 全局发货（§154 最终版，用户决策：忽略 chaos）

**Decision:** 将 `DEFAULT_GOD_AI_PARAMS.bulletLaneWait` 从 0 改为 **1**（§154 predictive next-body 最终版全局生效）。用户明确指示只关注 hard（chaos 暂不计）。发货快照：`reports/winrate/history/2026-08-06_093217__§155 发送 bulletLaneWait=1 (W1 predictive hold, 全局默认).json`。
**Rationale:**
- **hard 全量验证（同语料 4200 局，seeds 1-120 × 35 关）**：74.4% → **75.1%（+0.7pp）**；60-seed 语料 flip-scan 全关净 +15（39W/24L）。硬门禁 `god-ai-hard-chaos-gate` aggregate 637→**639/700**（floor 612）。
- **classic（+0.1pp，91.2→91.3%）、chaos（−0.3pp，70.7→70.4%）** 如实记录；chaos −7/2100 为 freeze-vs-hit 权衡在 chaos 更多弹幕下的已知倾向，未触碰 chaos 门禁 floor（394/700，实际 ~493），后续如需纠正可在 think.ts 按难度关闭 hold。

## 156. Freeze-Window Power-Up Pickup（冰冻期道具拾取，无限距离）

**Decision:** 在 AGGRO 候选（weight 700）的开头、stop-and-aim 之前，插入一段冰冻期道具拾取逻辑。新增参数 `freezePickupRange`（默认 999 = 无限距离，0 = OFF → byte-identical）。实现位于 `findFreezePickupTargetImpl`（`StrategyPlanner.ts`），由 `think.ts` AGGRO 分支调用。
**v2 变更（2026-08-06 用户指示）**：`freezePickupRange` 从 2 改为 **999**（无限距离）。冰冻期间敌人完全冻结（不能移动/射击），唯一威胁是飞行中的子弹（DODGE weight 1000 > 700 已处理），因此冰冻期可以安全地穿越全图拾取任何可达道具。移动过程中如果移动方向有敌人，随手开火（`shouldFireInDir`）。
**Rationale:**
- **根因**（hard S12 Lattice，0:18~0:28）：冰冻期间 `PICKUP_HIGH`（weight 800）被 `!self.aggressive` 门控跳过。AGGRO（700）随后优先对任何对齐的冻结敌人执行 stop-and-aim，从不检查附近道具。一个 2 格外的道具在整个冰冻窗口被忽略，玩家一直站着射击冻结的敌人。

## 157. Base Clear-Shot Threat Detection（基地车道对齐远距离威胁检测）

**Decision:** 在 `isBaseUnderThreat()` 中新增 `enemyCanShootBase` 检查：任何存活且已生成的敌人如果与基地对齐且视线无遮挡（brick/steel/base 均不挡），无论距离多远，都视为威胁。新增参数 `baseClearShotThreat`（默认 1，0 = OFF → byte-identical）。
**Rationale:**
- **根因**（hard S12 Lattice，0:38~0:48）：一个与基地列对齐的敌人在远处通过已清理的车道射击基地。`isBaseUnderThreat()` 返回 false（row < 18，distance > race range），`selectTarget` 未返回防守位置，玩家一直在地图上方追猎，基地被毁。
- **修复**：`enemyCanShootBase`（`SmartThreatModel.ts`）检查敌人是否与基地同列或同行、且子弹路径上无 brick/steel/base 遮挡。该检查比 §88 chokepoint 的 `facingGate` 更宽泛——§88 要求敌人面朝基地，而 §157 认为对齐的敌人随时可以转向开火，因此无论朝向都触发。

## 158. Non-Freeze Close-Range Power-Up Pickup（非冰冻期近距离道具拾取）

**Decision:** 新增 `CLOSE_PICKUP` 候选（weight 540，位于 DEFENSE_INTERCEPT 550 与 ENGAGE 500 之间），在非冰冻/护盾模式下，当无炮弹危险时拾取 `closePickupRange`（默认 2）格内的道具。新增参数 `closePickupRange`（默认 2，0 = OFF → byte-identical）。实现位于 `findClosePickupTargetImpl`（`StrategyPlanner.ts`），与 `findFreezePickupTargetImpl` 共享 `findNearestReachablePowerUp` 逻辑。
**Rationale:**
- **用户需求**：非冰冻期，如果道具距离近并且走过去路上没有炮弹危险，也要拾取，也要随手开火打敌人。
- **权重调整（650→540）**：初始权重 650（高于 DEFENSE_INTERCEPT 550）导致 seed-999 回归——玩家在敌人接近基地车道时去捡道具，回来防守已来不及。降至 540（低于 DEFENSE_INTERCEPT 550）后，防守拦截优先执行；玩家不在防守位时 CLOSE_PICKUP 仍可拾取近处道具。

## 159. 天降神兵守卫改用 GOD AI + §避让防堵车（用户需求 2026-08-06）

**Decision:** 「天兵」召唤的基地守卫（§31 Phase 2）不再使用旧的简单 "Commander-defend" 策略，改为每个守卫一个完整的 `GodAIInput` 大脑（与 God AI 玩家完全相同的决策管线），并在其上叠加 §避让 override：当守卫挡住「正在移动」的 player 前方一格（forward cell）时，无条件避让——
1. 优先垂直让开（`YIELD_PERPS` 候选，两侧都通时取腾挪空间更大的一侧）；
2. 垂直方向都没有空间时，无条件转为与 player 同方向并前进（走廊护航）；
3. 一直避让到不再堵车才恢复自主行动；

## 160. 避让中扫射压制——避让开火优先沿腾挪轴（用户需求 2026-08-06）

**Decision:** §159 的避让开火原为「只沿 player 车道方向（fwd）开火」——守卫垂直让开时子弹从车顶竖直飞出、与身体滑行方向不一致，且对守卫正在横穿的走廊侧翼毫无压制。§160 将避让期火控改为「扫射轴优先、敌人优先」（`updateGuardYield`）：
1. 先沿 **腾挪轴（moveDir，即守卫实际移动的垂直方向）** 判定开火，但只在轴上确有**敌人**时才优先——炮管与移动方向一致（消除「开火方向偏离目标」），且随身体滑行，逐发子弹从不同位置射出，横扫守卫正在横穿的走廊带（避让中优先扫射压制）；
2. 腾挪轴无敌人时回退到 §159 原行为：沿 player 车道（fwd）开火压制（避让过程保持向前方开火压制），车道门仍为 `shouldFireInDir`（敌或可拆砖皆可）；
3. 两条路径都以大脑 `scanAhead`（敌判定）+ `shouldFireInDir`（T6/T11/§121 安全门：绝不打基地环、不打不可穿透钢）门控；引擎冷却模型限速——每 tick 至多一枪，无论哪条分支胜出。

## 161. §161 开路策略（carve path）——实现完整、hard 全 35 关与 Battlement 均实测净零 → 诚实阴性归档，旋钮默认 OFF（用户需求 2026-08-06，Stage 33 Battlement 过关思路）

**Decision:** 新增泛化的「开路策略」（无关卡名，数据驱动，`carvePathMode` 门控，默认 0 = OFF → byte-identical）：
- **Mode A（R1/R2）**：玩家在下半区（`carveLowerRow=13`）且基地无威胁时，若到防守驻点（`computeBaseGuardAnchorImpl`/默认防守位）无**顺畅**路线（无 corridor 路径 → R4），则用破砖 A*（`findCarvePathImpl`）挖一条通途到驻点——优先 0 破坏（基地环/基地列砖记 1e9 代价绕行），必要时最多破 `carveMaxBaseColumn=1` 个基地列砖（R6）；
- **Mode B（R3）**：已在驻点（`carveAtPostCells=2`）且 `carveChaseCells=5` 内无敌人时，向 `carveThreatDistCells=8` 内最可能威胁基地的敌人（`enemyCanShootBase`/`enemyCanBreachRing` 优先）挖路；
- **硬约束（R5/R6）**：`pathCarveSafeImpl` 逐足迹校验——钢、基地环（精确 8 格环，`isCarveRingBrickImpl`）永远不打；基地列（BASE_POS.col..+1、环以上）最多 1 格。

## 162. §162 nav 卡死破局（navBreakStuck carve-dig escape）——SHIPPED 默认 1，hard 全 35 关显著胜率提升 p=0.019（用户需求 2026-08-06，回放 hard-s34-base-l2-t69-seed2050197249 Problem 1：出生点被砖墙围堵，player 不开墙出击，0:00~0:20 在出生点附近振荡）

**Decision:** 三层机制（全部 `navBreakStuck>0` 门控，SHIPPED 默认 1）：
- **破砖回退**：`followPathImpl`/`directMoveImpl` 全向不可通行时，回退尝试**可破**方向（`canMoveOrBreak`）——密封出生点口袋的薄墙被打破而非反向振荡（回放：玩家 128↔136px 摆荡在 cell 8↔9 之间，passable-only 回退永远只会返回口袋内反向，17-30s 无法出击）。
- **像素级卡死检测（endFrame，每 tick 运行）**：净位移 < `carveDigNetEscape=24`px 且连续 `carveDigBlockTicks=90` tick 即判墙堵。cell 级 `_navStuckTicks` 永远检测不到口袋振荡——tank 中心坐标在墙边摆动时跨 cell 线（128↔136px ↔ cell 8↔9），每几 tick 重置 cell 计数器；且 HUNT 并非每 tick 求值（高权重候选优先），卡死计数必须挂在每 tick 的 endFrame。
- **carve-dig 会话**：卡死即 `findCarveEscapeImpl` 启动持久挖路会话（精确环安全 dig 路径），跟随直到口袋打开 / 超时 `carveDigMaxTicks=2700`；spawnTimer>0 不计卡死（spawn 等待≠口袋锁定，防止每关开局误挖放弃防守）。

## 163. §163 中路防守（midLaneDefense）——子弹触发版全 35 关与 Battlement 均实测净零 → 诚实阴性归档，旋钮默认 OFF（用户需求 2026-08-06，回放 Problem 2：基地列无钢铁防护，player 坐视敌人凿穿中路砖墙）

**Decision:** 泛化「中路防守」候选（`midLaneDefense` 门控，默认 0 = OFF）：触发信号为**基地列内真实敌弹**（`laneShellInColumnImpl`——敌弹在 BASE_POS.col..+1 列向下飞行且与基地间无钢/水阻挡，即「凿穿瞬间」，与子弹-子弹碰撞对消机制耦合）；锚定基地列上方可站防守点（`findLaneDefensePointImpl`），持枪位（`midLaneHoldRange=1`）朝列上方停射（列内任意位置有弹即开火对消，突破 T5 的 128px 射程局限），牵绳（`midLaneMaxDist=8`，近基才锚定）、短挖门控（`midLaneMaxDigCells=3`，避免重复挖刚逃出的密封口袋）。权重 545（defenseIntercept 550 之下、closePickup 540 之上）。
**Rationale（实测净零 → 归档）：**
- **测量（只测 hard，按要求）**：Battlement 120 种子配对 A/B（§162 基线 vs §162+§163）：0.2379 → 0.2451（p=0.54，+1pp 噪声内）；全 35 关 60 种子：suite 0.5522 → 0.5523（p=0.55，47/57/1996 better/worse/tied，verdict no significant difference）。
- **迭代历史（3 版触发器的 A/B 教训）**：① 敌人**在场/朝向**列（14-35% tick，B 测 29/35 关更差，suite 0.55→0.40 崩塌）——列内路过敌人不是威胁；② 纯敌弹列内信号（0-12%，Battlement 26%）→ 全图 0% 关不再受影响，但 Battlement 仍净零；③ 加可达性门控（口袋内防守点 = 8 步挖 = 自损）后 Battlement 仍 +1pp 噪声。

## 164. §164 中路列旁主动驻守（midLaneHold）——诚实阴性归档（用户需求 2026-08-06：让 §162 出袋后的玩家优先走中路走廊而非左侧，在列旁持枪对消）

**Decision:** 新增 MID_LANE_HOLD 候选（权重 220，carvePath 之下 hunt 之上；驻守判定硬编码 dist===0，不读 midLaneHoldRange）+ 3 个新参数（midLaneHold 默认 0 OFF、midLaneHoldMaxRow=14、midLaneHoldEnemyDist=12）+ 3 个纯函数（laneColumnOpenToBaseImpl / findParryHoldCellImpl / enemyNearLaneImpl）。机制：玩家在地图上半区（row≤14）且基地列无钢/水防（列开放）且中路繁忙（列内有向下敌弹或敌人距列 ≤12 格）时，导航到/驻守列旁对消格（pcx 在列 x 范围 ±6px 内、可站、走廊可达——Battlement 顶部广场 (12,4)），面朝上开火对消；中路无威胁时放行 hunt/engage。两轮 A/B 全部显著为负 → 归档默认 OFF（byte-identical），代码保留作 A/B 基线。
**Rationale（证据链）：**
- **机制诊断**：S34 获胜跑（base，stageclear 20 kills）的 breach12=12/12 —— 基地列 12 块砖**全部被凿穿但基地照样存活**。基地死于边路/环砖威胁，靠玩家整体击杀压力而非列内对消。"凿穿中路砖墙=威胁基地"的用户假设在 Battlement 上不成立。
- **A/B 1（mlh 单独，Battlement 120 种子）**：0.2379→0.1590（p=0.0000，-7.9pp 显著更差）。驻守饿死击杀压力（目标种子 20 kills→16 kills→gameover）。

## 165. T2a Defense Override — 近敌停射允许（修复 S20 Bastion 振荡死锁，origin 侧原 §159）

**Decision:** 新增 `t2aDefenseOverrideRange` 参数（hard/chaos 默认 4，classic 默认 0 → byte-identical）。当基地受威胁且玩家越过 `maxPlayerDistFromBase` 阈值时（正常情况会 `skipT2aForDefense` 阻止 ENGAGE），若满足以下条件则允许 ENGAGE 开火：
1. **距离门控**：玩家距基地 ≤ `maxPlayerDistFromBase + t2aDefenseOverrideRange`（仅阈值附近 1–4 格内生效，远离基地不触发）
2. **近敌检测**：当前 `aimDir` 方向 scanAhead 命中敌人且距离 ≤ `t2aDefenseOverrideRange`
3. **aimDir 覆盖**：当当前 `aimDir` 无近敌时，扫描四方找最近敌人覆盖 `aimDir`（仅当 aimDir 无近敌时触发，避免不必要目标切换）

## §165. 中路防守启用 + 水阻弹 bug 修复 + 近战对枪火力评估

**Decision:** 三项修复针对 replay `hard-s08-base-l1-t27-seed2585395049` 观察到的三个行为异常：
1. **midLaneDefense=1** (SHIPPED ON)：启用 §163 中路防守候选。基地列无钢铁防护时，敌人子弹可沿列向下凿穿砖墙直逼老鹰，但此前该候选默认 OFF。
2. **水阻弹 bug 修复**：`laneShellInColumnImpl` / `laneShellAboveImpl` 误将 `water` 视为阻弹地形（检查 `steel || water`），但 `TileMap.blocksBullet` 明确只有 `brick/steel/base` 阻弹。水不阻弹（Battle City 原版行为）——此 bug 导致 S8 Riverbed 等有水的基地列永远无法触发中路防守。
3. **closeCombatDuel=1** (SHIPPED ON)：启用 §153-W2 近战火力评估——当对齐近敌射速快于玩家时，横移闪避而非站定对枪（必败交易）。

## §165-round2. 深度调优：pathThreatAvoidance 假阳性 + closeCombatDuel 多敌计数 + midLaneHold 主动防守

**Decision:** 三项深度调优均经 A/B 验证后**保持 OFF**——数据证明现有 God AI 已充分调优，提出的修复方案均有净负副作用。
**Rationale:**
### 1. pathThreatAvoidance 假阳性根因分析与修复尝试
**根因（3 层假阳性）：**

## 166. B1 starRush 星经济冲刺 — 诚实阴性归档（旋钮默认 0，2026-08-07）

**Decision:** 新增 4 参数（`starRushMode` 默认 0 OFF、`starRushMaxLevel=2`、`starRushRangeCells=8`、`starRushLiftGates=1`）。开启且 level < maxLevel 时，星的紧急拾取范围从 4 格扩到 starRushRangeCells，并（liftGates=1）解除 §87 nearby-enemy / route-danger 门。实现于 `findUrgentPowerUpTargetImpl`（StrategyPlanner.ts）。
**验证（hard）：**
- 35×20 四臂筛选：OFF 76.6% / A(r8,lift0) 76.7% / B(r8,lift1) 77.3% / C(r12,lift1) 77.1% — 方向为正但噪声内。
- 60-seed 决定性确认：OFF 75.9% / B 76.3%（+9 胜）/ C 76.2%（+7 胜）；配对翻转检验 B: L→W 24 / W→L 15，z=1.44；C: z=0.98 — 均 < 1.96，**不显著**。

## 174. 双玩家仿真系统 — 双 God AI 协作 + 防堵车 + 督战双玩家 (SHIPPED)

**Decision:** 扩展仿真系统支持双玩家模式：双 God AI 协作对战、P1↔P2 防堵车机制、督战双玩家模式。hard 35×120 过关率 97.1%（单玩家基线 76.3% 无回归）。
**具体实现：**
1. **仿真基础设施扩展**：`SimTask` 增加 `coop?: boolean` 字段；`sim-worker.ts` 透传 `coop` 到 `runSimulation`；`sweep-winrate.ts` 新增 `--coop` / `--dual` 标志。
2. **GOD AI 配合意识**（`GodAIInput` + `StrategyPlanner` + `think.ts`）：

> 状态： (SHIPPED)

## 175. Dual 中路无钢关配合策略 — 立项（2026-08-08）

**Decision:** 为 dual 模式下的"中路无钢、敌人从中路顶部出生持续凿穿砖墙"关卡（典型：S34 Battlement）实现专用配合策略。所有增强**仅对 `spectateDual && centralBreachRisk` 生效**，单玩家逐字节不变。
核心改动：
1. **中路无钢检测器** (`detectCentralBreachRisk` in `params.ts`)：扫中央带 cols 11–13 / rows 0–22 的 steel 数 = 0 + 敌出生点含中列 (col 12±1) + col 12 rows 0–9 有 ≥4 格 empty（开放通道，排除 S14 Steel Web 等砖墙从 row 2 开始的关）。当前仅 S34 通过。
2. **Dual 角色分工**（`StrategyPlanner.ts`）：

## 176. Dual Central Breach §6 实测缺陷修复 — P2 角色落地 + P1 dig-fire

**Decision:** 针对 plan/dual-central-breach-strategy.md §6 实测复盘发现的三个缺陷，实施以下修复（全部仅 `spectateDual && centralBreachRisk` 下生效，单玩家逐字节不变）：
1. **P2 fence 拾取** (§6.3-A)：在 PICKUP_HIGH 候选顶部新增 P2 专用 fence 拾取路径，绕过所有门控（nearby-enemy / retreat-gate / divert-distance）。新增 `findDualFencePickupImpl`（StrategyPlanner.ts）+ `dualCentralBreachP2FencePickup` 旋钮（params.ts，默认 1）。P1 守锚点、P2 捡 fence（=给基地砌钢墙），结构性解决中路被凿穿。
2. **P1 dig-while-moving** (§6.3-C)：HUNT 候选 fire 逻辑中，P1 在 dual central breach 下 `allowWallFire=true`（`shouldFireInDir` 第 4 参数从 `false` 改为 `p1DigFire`）。P1 推进时开火破砖，不再等 navStuck 检测器。新增 `dualCentralBreachP1DigFire` 旋钮（默认 1）。P2 保持 `false`（A/B 实测 P2 开 wall-fire 反而 -12pp，浪费弹量上限）。
3. **§159 T2a P2 bypass** (§6.3-D)：ENGAGE 候选中，P2 在 dual central breach 下跳过 `skipT2aForDefense`（不被强制回防）。P1 守锚点，P2 自由狩猎——允许 P2 停下射击近敌而不被基地威胁召回。A/B 实测 +4pp。

## 177. Dual Central Breach P2 导航落地 — directMove/patrol 实测回退，de-conflict 生效

**Decision:** 实施 plan/dual-central-breach-strategy.md §6.3-D 的 P2 导航两件套 **作为 opt-in 旋钮**（默认 0，全部仅 `spectateDual && centralBreachRisk && isPlayer2()` 下生效，单玩家逐字节不变）：
1. **A) directMove 替代 A\***（`dualCentralBreachP2DirectMove`）：think.ts HUNT 候选长程分支优先 `directMove(pc)`，失败回退 `followPath()`。默认 **0**（A/B 实测回退）。
2. **B) 敌出生点巡逻**（`dualCentralBreachP2Patrol` + `PatrolEnemyDist`/`PatrolRow`）：`findDualPatrolTargetImpl`（StrategyPlanner.ts，模块级 `_dualPatrolCell` 缓冲，AGENTS §14.1）在无可射敌时扫敌 spawn 列。`=2` 改为驻守 P2 自身防位。默认 **0**（A/B 实测回退）。
3. **实测生效的修复**（设为默认 1/2）：

## 178. Dual Central Breach autopsy (hard-s34 seed2) — carve 穿墙 + 中驻守 + sticky hold

**Symptom:** replay `hard-s34-base-l3-t25-seed2.replay`（督战双玩家）三异常：P1 出生点振荡、P2 滞留右上、P1 滞留顶部（逐帧 autopsy 报告与其复现脚本为本地一次性产物，未入库）。root cause：dual central-breach 下两坦防守锚点在基地砖环两侧，须穿中路砖墙；但 carve-dig 逃生被硬卡（中列砖 1e9 + `carveMaxBaseColumn=1`），两坦被钉顶部、下不到防守位，敌人从底部凿穿基地。committed 基线（pre-§178，438d240）S34 dual 隔离 per-seed 仅 **1/12**（仅 seed9 过）。
**Fix（全部 `spectateDual && centralBreachRisk` gated，单玩家逐字节不变）：**
1. **A) carve 穿中墙**：override 块置 `carveMaxBaseColumn = dualCentralBreachCarveMaxBaseColumn(99)`、`carveBaseColumnCost = dualCentralBreachCarveBaseColumnCost(5)`。`PathCarve.buildCarveCosts` 中 base-column 砖代价由固定 `1e9` 改为 `self.params.carveBaseColumnCost` 驱动 → nav-stuck carve-dig 逃生直穿中墙而非绕顶部。

## 179. Dual Central Breach autopsy (hard-s34 seed6) — P1 凿盾 + 危基不回防 + 冰冻浪费

**Symptom:** §178 修复后 S34 dual 12 个种子仅 seed6 仍 `gameover@5549`（base_destroyed, kills=17, lives=3）。逐帧法医重建（autopsy 报告与复现脚本为本地一次性产物，未入库）定位 4 个根因：
| # | 失误 | 根因 |
|---|---|---|

## 180. Dual Central Breach autopsy (hard-s34 seed34) — 右路盲区 + fence 独占 + defenseSecond 近端覆盖

**Symptom:** replay `hard-s34-base-l2-t33-seed34.replay`（督战双玩家）`gameover@t2002 / base_destroyed / kills=7 / lives=2`。§179 基线 S34 dual 120-seed 85.8%（103/120），seed34 在 17 个失败 seed 中。逐 tick 取证（handoff `plan/dual-s34-seed34-base-loss-handoff.md`）定位 4 个缺陷：
| # | 失误 | 根因 |
|---|---|---|

## 181. Dual Central Breach autopsy (hard-s34 seed115) — P1 spawn 振荡：A* 路由穿透基地保护砖

**Decision:** 新增 `dualCentralBreachP1DirectMove` 参数（默认 1），让 P1 在 dual central breach 模式下使用 `directMove` 代替 A* `followPath` 进行全距离导航，与 P2 的 `dualCentralBreachP2DirectMove`（§180）对称。Gated by `spectateDual && centralBreachRisk && !isPlayer2` — 单玩家和 P2 路径逐字节不变。
**Rationale:**
- **根因**：诊断报告 `plan/dual-s34-seed115-base-loss-handoff.md` 描述了 4 个症状（P2 振荡、P2 朝墙空射、P2 弃守 BR 敌、P1 锚点漂移），但逐 tick 取证发现它们全部是**同一根因的不同表现**：A* `followPath()` 路由穿过"基地保护砖"（`isBaseProtectionBrick` with `baseWallScanRadius=5` 标记了出生点周围 5 格内的所有砖墙），但 `canMoveOrBreak` 拒绝打破这些砖（return false）。结果：
- P1 在 (128,384) 卡死：`followPath` 返回 'right'（A* 路由穿过 (11,24) 基地保护砖），但 `canMoveOrBreak('right')` = false → P1 既不能移动也不能开火（break-through fire 被 base wall guard 禁止），卡在出生点 1693 ticks（整局 28 秒）

## 182. 重放暂停后切换应用再回来点播放，画面不动（visibilitychange 污染 world.state）

**Decision:** 两处修复：
1. `main.ts` visibilitychange 监听器增加 `!game.playback` 守卫——重放期间不调用 `simulation.togglePause()`。
2. `PlaybackController.update()` 增加防御性守卫——若 `world.state === 'paused'`（被外部代码污染），在 tick 前恢复为 `'playing'`。
**Rationale:**

## §182. Face-Nearest-Enemy Fallback for Immobile-Stuck Player

**Decision:** In the HUNT candidate, after all movement options (followPath, directMove, carve-dig, nav-stuck escape) have failed to produce a passable `_moveDir`, when the player has been physically immobile for >= `carveDigBlockTicks` (90 ticks = 1.5s), turn to face the nearest enemy and fire at it via `shouldFireInDir`. Also reset `_digBlockTicks = 0` when a carve-dig session ends (timeout or unbreakable path) to give this fallback a 90-tick window before the carve-dig can re-start.
**Rationale:**
- Root cause (S2@seed120, hard, 150s stuck → gameover): Player at defense position (9,25) was completely surrounded by enemies and base-protection bricks. `followPath()` and `directMove()` both returned null every tick. The player faced a fixed direction (UP) and fired 189 bullets uselessly — the adjacent enemies were NOT in the UP direction. The `navStuckZone` parameter was 0 (OFF), so the nav-stuck escape never triggered. The carve-dig never started because `findCarveEscapeImpl` couldn't find a non-base-protection wall to break through.
- The fix adds a fallback that detects this condition (`_moveDir` null or enemy-blocked + `_digBlockTicks >= 90`) and turns the player to face the nearest enemy. `shouldFireInDir` then fires at the enemy (with all T6/T11 base-protection safety gates intact).

## §183. GOD AI Idle Calibration — Analysis Complete

**Decision:** After a comprehensive analysis of all 35 stages × 120 seeds (4200 simulations) under督战+单人+hard mode, all player stationary periods >3s (180 ticks) are classified as combat logic. Two code bugs were found and fixed (§182, §184). The calibration is complete.
**Rationale:**
- The analysis script (`tools/diag/idle-analysis.ts`) was developed to detect and categorize idle periods, capturing player position, AI branch, fire count, enemy distance, and terrain context.
- Pattern analysis identified three recurring scenarios:

## §184. Freeze Powerup — Allied Guard Freeze Bug + Pickup Stuck Bug

**Decision:** Two bugs related to the freeze powerup were found during idle calibration and fixed:
1. **Bug 1 — Freeze froze allied guards** (`SimulationCombat.ts`): The freeze check used `!tank.isPlayer`, which incorrectly included allied guards (天降神兵). Changed to `tank.allegiance === 'enemy'` so only hostile tanks are frozen.
2. **Bug 2 — Player stuck during freeze pickup** (`think.ts` AGGRO branch): When the player navigated toward a freeze powerup but was physically blocked by a frozen enemy, the player kept trying to navigate (returning a blocked direction) and never fired at the blocking enemy. Fix: when `_digBlockTicks >= carveDigBlockTicks` (90 ticks = 1.5s of immobility) during freeze pickup, fall through to AGGRO's stop-and-aim / navigate sub-branches so the player kills the blocking enemy first, then resumes the pickup next tick.
**Rationale:**

## §185. navStuckZone=1 — Sub-Pixel Jitter Defeats Nav-Stuck Counter

**Decision:** Enable `navStuckZone: 1` and `navStuckSuppressTicks: 60` in `DEFAULT_GOD_AI_PARAMS` (hard/chaos). Classic keeps `navStuckZone: 0` via `CLASSIC_MODEL_PARAMS` (byte-identical classic gate). Also add a CARVE_PATH deferral guard in the nav-stuck escape: when `carvePathMode > 0` and the player is in the carve zone (`pc.row >= carveLowerRow`), the nav-stuck center-escape is suppressed so CARVE_PATH can handle the escape.
**Rationale:**
- **Root cause**: The P0.3 nav-stuck escape (`navStuckTicks=180`, 3s) uses `playerCell()` for its same-cell check. `playerCell()` is the tank CENTER, and a 1px bounce across a cell boundary flips it (e.g. S26 seed51: center bounces (5,4)↔(6,4) every ~10 ticks). With `navStuckZone=0` (exact-cell comparison), the counter resets every few ticks and never reaches 180 — the escape NEVER fires. S26 seed51: player stuck for **581.6 seconds** (entire game, 0 kills, 0 fire, gameover).
- **§168 fix was developed but never shipped**: The zone-based check (±1 cell, same as §152 `aggNavStuckTicks`) was implemented in think.ts but `navStuckZone` was left at 0 in DEFAULT_GOD_AI_PARAMS. Classic explicitly restored 0, but hard/chaos never enabled it.

## §186. powerupStuckTicks — Powerup Navigation Stuck Detection

**Decision:** Add a `powerupStuckTicks` parameter (default 300 ticks = 5s, OFF in classic) to all powerup branches (PICKUP_HIGH, CLOSE_PICKUP, PICKUP_MID, PICKUP_LOW and AGGRO's powerup check). When the player has been pixel-stuck for >= `powerupStuckTicks` (via `_digBlockTicks` counter), skip powerup navigation and let the HUNT branch's nav-stuck escape run. Also add `t2aSkipStuck` check in T2a: when pixel-stuck, skip stop-and-aim entirely (even if aimDir is valid) and fall through to the nav-stuck escape.
**Rationale:**
- **Root cause (powerup stuck)**: The GOD AI 35×120 idle calibration found 12 alerts >=15s where the player was stuck navigating toward a powerup but not making progress. The powerup branch returns true with a navigation direction, but the player can't actually move (blocked by walls/enemies), and the branch blocks lower-priority branches (HUNT/nav-stuck escape) from ever running. Examples:
- S33@seed47 (18.6s): 100% powerup branch, player at (11,9), navigating to powerup but stuck. `pathLen=8-26`, no firing, terrain changed (brick destruction) but player didn't move.

## §187. Guard/P2 A* Player-Obstacle + Target Blacklist + Fire Post-Turn + Powerup-Enemy Overlap

**Decision:** Four independent fixes targeting idle alerts S7@seed54, S3@seed65, S18@seed113, S27@seed107, S2@seed83:
1. **Guard/P2 A* player-obstacle** (`navAvoidPlayer`): Guard and P2 A* pathfinding treats P1 as an impassable, indestructible obstacle. P1 does NOT treat P2 or guard as obstacle. Adds `blockedCell` to `PathConstraints` — `findPath` skips candidate cells whose 2×2 footprint overlaps the blocked cell. The guard brain gets `isGuardAI=true`; `getNavBlockedCell()` returns P1's cell when `isGuardAI || isPlayer2()`.
2. **Target blacklist** (`targetBlacklistStuckTicks` / `targetBlacklistDuration`): When the player has been stuck (pixel-stuck via `_digBlockTicks`) for ≥240 ticks (4s) while targeting enemy A, A is temporarily removed from the target pool for 180 ticks (3s). Implemented as a single-slot blacklist `_blacklistEnemyId` + `_blacklistExpiryFrame` on `GodAIInput`. `selectTargetUncached` skips the blacklisted enemy. Note: the initial value was 120 (2s) but caused S35 chaos regression (18→12/20); raised to 240 (4s) which restored S35 to 19/20 while still resolving idle alerts (all stuck periods <5s).
3. **Fire post-turn position** (S3@seed65): `shouldFireInDirImpl` now uses the post-turn-snap position when `dir !== p.dir`. Mirrors `aimSurvivesTurnImpl`: horizontal turn snaps y, vertical turn snaps x. This prevents misses when the player turns from vertical to horizontal and the position shifts.

## §188. Fence Power-Up Must Not Trap Tanks Inside Steel

**Decision:** `applyFencePowerUp` now skips any base-ring cell that overlaps a tank body (checked via `aabb` against `w.allTanks`). Previously, the fence converted any `empty` or `brick` ring cell to steel without checking for tank overlap.
**Rationale:**
- **S9@seed119 (532.7s stuck, game timeout)**: During gameplay, base-ring bricks at col 14 were destroyed by bullets (cells became `empty`). The player tank moved onto those cells (valid — empty terrain is passable). When the fence power-up later converted those `empty` cells to `steel`, the tank was permanently trapped: `rectHitsTerrain` detects the steel overlap on every subsequent move attempt, so the tank can never leave. The nav-stuck escape fires every 240 ticks but cannot help — the tank is physically walled in at the pixel level. The game timed out (532s of 600s max).
- Root cause confirmed via pixel-level trace: player at (224, 384) = cell (15, 25), body spans cols 14-15, rows 24-25. Steel appeared at col 14 at tick 4042 while player was already there.

## §189. 开局联通清墙 — Base Connectivity Clear

**Decision:** Added a `BASE_CONNECT_CLEAR` candidate (weight 270, between `firingLane`(300) and `carvePath`(250)) that proactively clears lower-half brick walls to connect the player's side of the base to the P2 spawn point (opposite side) at game start.
**Rationale:**
- **Replay `hard-s04-base-l3-t82-seed1017`**: The player only cleared walls to reach above-base (the defense post area), not the opposite side. In the endgame, the player couldn't pathfind to the right side to defend, and the base was destroyed.
- **User request**: "开局阶段必须在下半区找到通道通往基地对侧（P2出生点），如果没有就清墙开路。先绕基地环，清墙打通基地两侧的通道，到达基地另一侧，再从那一侧选择 清墙到据守点/出击/防守。"

## §190. A* 寻路代价模型升级 — 砖墙=空地 + 基地环倍率 + 开火停车代价

**Decision:** Upgraded the God AI `breakBrick` A* cost model from the old flat "brick=5, empty=1" to a time-efficiency-based model with three components, per `plan/god-ai-nav-cost-req.md`:
1. **§3.1 — Brick cost = 1 (same as empty):** In `breakBrick` mode, a destroyable brick costs the same as empty terrain (1). The old `cost=5` penalized paths that were actually efficient (the tank fires while moving, clearing bricks without stopping). The brick-vs-empty distinction is now expressed by §3.3's fire-stop cost, not the base step cost.
2. **§3.2 — Base ring multiplier (`navBaseRingMult=1.5`):** Base-protection bricks (per `isBaseProtectionBrick`) get an extra cost of `(mult-1)` added on top of the base cost of 1, making them cost `1.5` total. This gently discourages the AI from breaking its own base walls without making them impassable. The old PoC's `1e6` caused S7/S12/S13 base losses (the sole defender was forced to detour around the base); 1.5x is safe.
3. **§3.3(c) — Firecontrol-linked stop cost (`navFireStopModel='firecontrol'`):** Every brick edge gets an additional stop cost computed dynamically from the tank's real fire state via `fireClearStopTicks()` — the shared pure function that mirrors `shouldFireInDir`'s geometric alignment + `think.ts`'s cooldown logic. The A* loop tracks arrival tick (`_pfArriveTick`) and cooldown expiry (`_pfCooldownExpiry`) along the path via parallel `Float64Array` buffers, computing real stop ticks per brick edge. This makes A* prefer straight-line brick paths (fire-while-marching, no stop) over zigzag paths (turn forces 1-tick stop + potential cooldown wait), and prefer paths where the cooldown expires before arrival (no wait) over paths where it doesn't. `navBrickStopCost=2` gates the model ON (>0); the actual cost is dynamic.

## 191. 批量仿真共享态硬化 — findPath 重入守卫 + level-sim 子进程隔离

**Decision:** 两项硬化措施（plan/batch-sim-shared-state-hardening.md）：
1. **T1 — `findPath` 重入守卫**（`src/utils/pathfind.ts`）：在模块级加 `_pfInUse` 布尔标志，`findPath()` 入口检测重入（throw `findPath reentered`），`try/finally` 保证所有退出路径释放。`findPath` 使用模块级 typed-array 缓冲区（`_pfGScore`/`_pfState`/堆数组等），设计上永不重入，但无运行时保证。此守卫将未来误用（重入→静默污染）变成立即崩溃，不改任何已有路径结果。
2. **T2 — `level-sim --size N` 子进程隔离**（`tools/optimize/level-sim.ts`）：批量模式不再用 in-process 串行循环，改为每 seed 派生一个 `bun level-sim.ts --seed S --size 1 ...` 子进程（并发上限 8）。父进程解析每个子进程 stdout JSON，按原有格式聚合 `results[]` + `winRate` 汇总。`--size 1` 保持原有 in-process 路径不变。
**Rationale:**

---
## §192. 基地车道哨兵（baseLaneSentry）— 取证 → 5 版迭代 → SHIPPED（2026-08-13）

**目标关**：S34 Battlement hard 基线 13/60（21.7%），全 35 关最差。

**根因取证（seed 14 弹道级还原，会话自建探针 tmp/probe-s34-*.ts）**：
- 拆环 fast 在 row 24-25 口袋（(16,24)/(17,24)/…）横走，t2162-2201 玩家在 (16,21) 与其同列 40 ticks；t2189 唯一一枪 fire=true（fireCount 32→33）但从 (16,23) 砖格内出生（bx = x+14），子弹中线被墙吃掉；nextFireInterval≈798ms（48 ticks）冷却中敌人 t2202 转身逃离；（16,23) 上方玩家侧所有柱子还在。
- 双偏线扫描根因：PERP_OFFSETS = ±8px 两条独立偏线 OR——线1 打 (16,23) 砖 → wall=true break；线2 经 (17,23) 开口看到敌人 → enemy=true；defenseIntercept 只查 scan.enemy → 开火，子弹走中线 → 砖格自爆。此洞对 §134/§157 系候选是结构性缺陷：把唯一一发冷却弹浪费在墙上。
- 玩家随后被 midLaneDefense 拖去中路，（16,24) 环砖 t2247 被速、基地首伤 t2359、~t2255 玩家死亡。
- 46/60 败局近同理（池模型下快车与玩家同列、玩家唯一一发打墙）。

**候选设计（第 5 杠杆）**：决策链分支 baseLaneSentry（850，interceptBase 900 下、pickupHigh 800 上）：
- csb/cbr 敌人与玩家对齐（±1 格、lanes 同列/同行）且曼哈顿 ≤6、其间无挡子弹砖（laneCorridorBlocked==0）→ 立定向目标翻转 + 开火（aimError 门）。
- 环先破（ringBreached）兜底：对齐任意附近带威胁敌人也开火（把枪线对准已经站在洞口的敌人）。
- **v4 修复**：blocked==1 且 shouldFireInDir 拒绝打砖 → 返回 false 让位（否则玩家原地冻结死锁，seed 6 t3186+ 实证）。
- **v5 修复（关键）**：仅在本 tick 可开火（onCooldown==false）才 claim；冷却期交还 midLane/navigate 流动——保留击杀弹、去掉轴锁。

**A/B 迭代（S34 hard 60-seed flip-scan，A=基线）**：
| 版 | 描述 | S34 结果 | 全关（35×60） |
|---|---|---|---|
| 基线 | — | 13/60 | — |
| v1 | 长行军站位(pass2 diggable) + nav 开火 + 松 tier | 6/60（win4 lose11）| — |
| v2 | pass1-only 站位 + nav 禁射 + 紧 tier3 | 18/60（win11 lose6）| — |
| v3 | v2 + nav 恢复开火 | 12/60（win6 lose7）| — |
| v4 | 纯持位射击、无 nav 接管 + 死锁修复 | 17/60（win7 lose3）| — |
| v5 | v4 + 冷却期不 claim | 20/60（win8 lose1）| 净 +17（67/50）|

- 三条实证：(a) 哨兵绝不接管导航（nav 开火=杀伤主力，v3 反向验证）；(b) 冷却期站位移交流动（v5 净 +3，seed 6/3/38 三项轴锁败局消除为仅剩 seed 38——蝶变单 tick）；(c) 仅站位不做长驻（v1 教训）。
- 残败 seed 38（Battlement）：t2846 哨兵对口袋 fast 打一发（单 tick 停驻，fire=true）→ RNG 流蝶变 → 基地 1500 ticks 后死于 armor/it 路径不同。60 seeds 中仅此 1 例，属混沌级联噪声而非系统缺陷。

**发货配置**：hard/chaos 默认 `baseLaneSentryMode=1`、`baseLaneSentryRange=6`；classic restore `baseLaneSentryMode=0`（instant 1-HP 未 A/B，classic gate 字节不变 629/700）。

**门禁真值重校准**（新默认下 60-seed 全关重测）：
- hard：均值 72.86%→AGGREGATE_FLOOR 484/700；S34 21.7%→33.3%（20-seed 真值 3→7）。
- chaos：均值 68.71%→455/700；S34 5%→21.7%；S22/S26 60-seed 分量 61.7%/58.3% 与 S26 120-seed 49.2%（S26 取 120-seed 口径 10，60-seed 58.3% 为乐观样本）。
- 门禁 20-seed 全通过：classic 629、hard 512、chaos 503（floors 594/484/455）。

**下一步（可选）**：chaos hard 车道防守的 mx 差异（chaos 敌人数更多、隧道更挤）；S34 seed 38 蝶变无修复目标。
---
