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

## §M0. God-AI 摘除主动道具（superItemMode/GuardThreat 默认归零）— plan/AI-No-Items-Warmstart.md M0（2026-08-25）

> DECISIONS 索引：§167（修订："机制退役"）。目的：NN AI 全链路（语料/训练/推理）不使用主动道具；
> 同一 A/B 预检口径复测确认新基线与预检 B 臂一致，−1pt 地板侵蚀（R4）已在案。

**改动**（`src/ai/god/params.ts` DEFAULT_GOD_AI_PARAMS）：
`superItemMode: 1 → 0`、`superItemGuardThreat: 1 → 0`；`superItemFrenzyAim` 保持 0。
命名预设（SKILLED_HUMAN / GUARD_GOD_AI / CLASSIC_MODEL_PARAMS）不动——CLASSIC/GUARD 本就为 0。

**配对复测**（eval-suite `--compare tmp/m0-guard-on.json tmp/ab-guard-off.json --difficulty hard --seeds 60`，
A = 显式 {superItemMode:1, superItemGuardThreat:1}，B = 新默认 OFF；2100 paired cells）：

| 指标 | A (guard ON) | B (新默认 OFF) | 预检（plan §0.2） |
|---|---|---|---|
| 胜率 | 76% | **75%** | 76→75% |
| suite v7 | 0.5450 | **0.5328** | 0.5451→0.5328 |
| Δscore (B−A) | — | **−0.0093 ± 0.0025** | −0.0093 |
| t / p | — | −3.77 / 0.0002 | −3.77 / 0.0002 |
| 显著关 | — | Lattice 0.519→0.485 (p=0.028)；Ramparts +0.0028 (p=0.033) | Lattice 65→58% |

结论：摘除成本 ≈ −1pt hard 胜率 / −0.0093 score，微小但显著，不构成结构性支柱；新基线（OFF）与
预检 B 臂**完全一致**。R4 缺口转列为 RL 守家目标，不再返工。
回退：参数回 1 即恢复，零代码成本。
> 复测覆盖说明：正式配对复测仅 hard（60 seeds，如上）；chaos 未单独重跑——以预检 B 臂
> （70→69%，Δscore −0.0105±0.0029, t=−3.63, p=0.0003，Ice Palace/Thicket 显著变差）为准，
> 同口径可背书。classic guard 预设本为 0，无变化。

---

# Part 0. 当前状态速览（2026-08-28 · God AI 解冻 + super-item 恢复纪元，DECISIONS §293）

> ★ **player God AI v1 已封版**（owner 拍板 D0=A，2026-08-26；评审 `god-ai-org.review.md` P1–P5 吸收）。
> **2026-08-28 解冻**：owner 拍板「取消冻结 god ai，恢复使用道具策略」（DECISIONS §293）——
> `superItemMode`/`superItemGuardThreat` 恢复默认 **ON**（frenzy 维持 OFF）；§272 三件套已完整重跑。
> 此后任何 God-AI 行为改动 = 新纪元「三件套」：新 DECISIONS 条目 + 重跑 60-seed 三难度基线 + 更新
> 冻结签名 golden——缺一不可。强制机制：pre-commit 冻结签名门 / `bun run freeze:check`
> （~100s，门红 ≠ 出错，是强制显式判定）；L2 可达性审计 `bun run freeze:l2`。
> 重启协议见文末专节；执行手册 plan/God-AI-Organization.md。

## 0.0 导航索引（决策号段 ↔ 章节）

| 号段 | 内容 | 正文位置 |
|---|---|---|
| §33–§95 | Classic 纪元（单难度调优 + 方法论沉淀） | Part I |
| §96–§110 / M0–M11 | v2 重设计纪元 | Part II |
| §111–§167 | 双玩家 Central Breach / 防守位等详细决策补录 | 文末「叙事未覆盖条目的精简补录」 |
| §168–§173 | HARD 瓶颈轮（selectTarget 五连阴性封盘） | 文末「追加：2026-08-07 HARD 瓶颈轮」 |
| §192–§229 | baseLaneSentry 家族 / 开放测试 M0–M5 / fireLineDetour 发货轮 | 文末各追加小节（M4.0 已逐号核验落点） |
| §194、§230–§234 | pixelStuck 兜底回退史 / 工具与门禁口径收口 | 文末「补录：§194 与 §230–§234」 |
| §235 起 | 渲染/重构轮（非调优，未压缩） | DECISIONS.md 原文 |

## 0.A v1 冻结基线（官方口径）

> eval-suite v7 · 35 关 × 60 seeds · params=351325f1 · 2026-08-26 于 pristine 行为采集。
> 再生命令：`bun tools/eval/eval-suite.ts --seeds 60 --difficulty <d> --dims --json tmp/freeze/baseline-<d>.json`
> （seeds = eval-suite 内置默认序列；无 `--params`/`--set`/`--fitness` 覆盖。tmp 语料不作长期凭证，以本表为准。）
> **口径区分**：本表 = 60-seed 基线；score-gate 门禁自 §233 起用 10 seeds（truth/margin 见 `tests/score-gate-core.ts`）。两者勿混用。

| 难度 | SUITE (lcb ±se) | 平均胜率 | fitness v6 | 最弱关 |
|---|---|---|---|---|
| classic | 0.7258（0.7211 ±0.0048） | 90% | 721.1 | Ice Palace（win 68%） |
| **hard（主评估）** | **0.5450**（0.5388 ±0.0062） | **76%** | 538.8 | Battlement（win 30%） |
| chaos（参） | 0.4943（0.4878 ±0.0065） | 70% | 487.8 | Battlement（win 17%） |

### 0.A.1 §293 解冻纪元基线（super-item ON，2026-08-28 采集）— 现口径

> 逆 §289 / 修订 §167-rev：`superItemMode`/`superItemGuardThreat` 恢复默认 1，
> `superItemFrenzyAim` 维持 0（archived，§273）。eval-suite v7 · 35 关 × 60 seeds · HEAD 参数。
> 再生命令：`bun tools/eval/eval-suite.ts --seeds 60 --difficulty <d> --dims --json tmp/freeze/baseline-<d>.json`。
> golden = `20784637c67ecd72e0c297d77bf3415b6621120475e3dc0cec6ee63a5caeadaf`（21 组合，111,176 签名行）。
> （2026-08-29 起 golden 以 §0.A.2 §303 重钉为准。）

| 难度 | SUITE (lcb ±se) | 平均胜率 | fitness v6 | 最弱关 |
|---|---|---|---|---|
| classic | 0.7286（0.7238 ±0.0048） | 89.5% | 723.8 | Ice Palace（win 68%） |
| **hard（主评估）** | **0.5403**（0.5341 ±0.0062） | **75.3%** | 534.1 | Battlement（win 23%） |
| chaos（参） | 0.4964（0.4899 ±0.0065） | 70.6% | 489.9 | Battlement（win 20%） |

- vs §289 冻结 OFF（classic 0.7286/89.5%、hard 0.5290/74.3%、chaos 0.4862/69.0%）：classic **逐位持平**
  （字节不变，superItemMode 经 `CLASSIC_MODEL_PARAMS` 仍归 0）；**hard +1.13pp SUITE / +1.0pp 胜率**、
  **chaos +1.02pp SUITE / +1.6pp 胜率** —— M0 摘除代价（hard −1pt、chaos −1.9pt）完整反还。
- score-gate `TRUTH_SCORES` 第四次重捕获（10 seeds）：hard 0.7575→0.7663、chaos 0.7372→0.7562
  （均回到 M0 退役前值），classic 0.8697 不变；`bun run freeze:check` 门绿。

- hard 维度均值（all/clears/losses）：progress 0.888/1.000/0.533 · lives 0.809/0.839/0.716 ·
  baseIntegrity 0.703/0.884/**0.134** · clearSpeed 0.148（clears-only）· accuracy 0.840/0.880/0.713 ·
  tempo 0.558 · loot 0.836 · growth 0.451 · baseSafety 0.917 · openingTempo 0.090 · mobility 0.925。
- 与 Phase III 基线（§0.C.5，2026-08-12）对比：classic 持平（0.7259→0.7258）、
  **hard +3.18pp SUITE / +3pp 胜率**（§195/§198/§229 发货杠杆的累计收益）、chaos +0.17pp。
- **冻结签名 golden**：`b81e240a8c2980bbf805215319be5aa2f483a312235bd35d758a6e522870ec32`
  （probe-det-baseline.sh 21 组合 · 109,516 签名行）；L2 可达性审计 **PASS**
  （六个 OFF 候选在 21 组合语料上零可达，`tools/diag/archived-reach-audit.ts`）。
- tmp 引用治理：正文引用的 `tmp/` 证据路径均为临时产物、不作长期凭证；关键数字必须已落在
  本档或 DECISIONS；再生方式随对应工具节命令。

### 0.A.2 §303 护卫出生修复 golden 重钉 + hard 60-seed 基线（2026-08-29）— 现 golden

> 护卫（天降神兵）出生卡墙 bug 修复（DECISIONS §303）：`baseSideSpawnCell` 全列阻塞时兜底
> 不再落墙（请求侧列贴基地 4 行 → 对侧列 → 同列向上直扫 → 全场最近空位，偏好请求侧）。
> 仿真行为变化 → `freeze:check` 翻红为预期显式判定；golden 重钉
> `20784637c6…` → **`b9a629e0e2c64f1c35889dc211e7bd3ee762311abee5bf1006690b5d75d688d6`**
> （21 组合，109,325 签名行），门回绿。非 God-AI 决策逻辑改动；按 owner 指令仅重跑 hard
> （classic/chaos 未重跑，§293 基线仍为其现口径）。
> 再生命令：`bun tools/eval/eval-suite.ts --seeds 60 --difficulty hard --dims --json tmp/freeze/baseline-hard-303.json`。

| 难度 | SUITE (lcb ±se) | 平均胜率 | fitness v6 | 最弱关 |
|---|---|---|---|---|
| **hard（主评估）** | **0.5428**（0.5366 ±0.0062） | **75.7%** | 536.6 | Battlement（win 23%） |

- vs §293 解冻纪元（hard 0.5403 / 75.3% / fitness 534.1 / Battlement 23%）：SUITE +0.25pp、
  胜率 +0.4pp、fitness +2.5 —— 方向温和正、幅度在 ±se 0.0062 噪声带内（统计上持平），
  与"修复仅改变护卫召出局的走向"的预期一致。losses 侧 baseIntegrity 0.134→0.104、
  lives 0.716→0.740 均改善（护卫终于能离墙守基地的旁证）；最弱关 Battlement 持平（23%）。

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

### 0.C.5 Phase III 基线（godai-score 多维，2026-08-12）

> ⚠️ **历史基线**（已被 §0.A 的 2026-08-26 v1 冻结基线取代，保留作对比锚点）。
> 测量：eval-suite v6 官方口径（`runSimulation` 直驱，35 关 × 60 seeds），godai-score v7 band（loss 0.40 / clear 0.70）。
> **hard = 主评估难度**；classic / chaos = 参考（仅防大幅倒退）。详见 §0.C。
> 维度含义：progress=击杀数(kills/enemies)、lives=存命数、clearSpeed=过关时间、accuracy=开火命中率(kills/shot)、baseIntegrity/baseSafety=基地、tempo=kpm、loot/growth/openingTempo/mobility 见 §0.C 维度表。

**头条指标**

| 难度 | SUITE 分 (lcb ±se) | 平均胜率 | fitness v6 |
|---|---|---|---|
| **hard（主）** | **0.5132**（0.5068 ±0.0064） | **73%** | 506.8 |
| classic（参） | 0.7259（0.7211 ±0.0047） | 90% | 721.1 |
| chaos（参） | 0.4926（0.4859 ±0.0067） | 69% | 485.9 |

**hard 维度均值（主评估，all / clears / losses）**

| 维度 | all | clears | losses |
|---|---|---|---|
| progress（击杀数） | 0.872 | 1.000 | 0.531 |
| lives（存命数） | 0.799 | 0.817 | 0.749 |
| baseIntegrity | 0.674 | 0.888 | 0.102 |
| clearSpeed（过关时间） | 0.146 | 0.146 | n/a |
| tempo（kpm） | 0.549 | 0.597 | 0.421 |
| accuracy（开火命中率） | 0.827 | 0.879 | 0.687 |
| loot | 0.833 | 0.869 | 0.732 |
| growth | 0.446 | 0.464 | 0.399 |
| baseSafety | 0.914 | 0.927 | 0.879 |
| openingTempo | 0.092 | 0.097 | 0.079 |
| mobility | 0.927 | 0.968 | 0.820 |

**classic / chaos 参考维度均值（关键项：all / clears）**

| 维度 | classic all / clears | chaos all / clears |
|---|---|---|
| progress | 0.960 / 1.000 | 0.856 / 1.000 |
| lives | 0.777 / 0.832 | 0.818 / 0.838 |
| clearSpeed | 0.817 / 0.817 | 0.163 / 0.163 |
| accuracy | 0.930 / 0.937 | 0.818 / 0.874 |
| baseIntegrity | 0.937 / 0.974 | 0.639 / 0.890 |

**读法（Phase III 多指标共同评估）**
- hard 主评估：**胜率 73% 为首要指标**；同看 clearSpeed=0.146（过关偏慢，P95 附近——拖沓信号）、baseIntegrity losses=0.102（败局几乎都丢基地）、accuracy=0.827（开火命中尚可）、lives=0.799（收尾较稳）。
- 这是**行为基线**，后续所有 hard 改动须保证这些维度不出现「高胜率但某指标恶化」的组合（如 clearSpeed 进一步下滑、baseIntegrity losses 更低）。
- classic / chaos 仅作参考：其 SUITE 分 / 胜率不应出现**大幅度**倒退即达标（chaos 未来增强敌人 AI 致下降属正常，见 §0.C.3）。

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

# 附：工具链索引（standing toolbox，2026-08-26 重写；一次性审计已归档 tools/diag/archive/）

**冻结门禁（DECISIONS §272）**

| 工具 | 用途 |
|---|---|
| `bun run freeze:check` | det 语料 21 组合签名 vs `tools/det-golden.v1.sha256`（~100s）；门红 ⇒ 新纪元三件套 |
| `bun run freeze:l2` | archived 候选可达性审计（同语料 branchTotals 全零断言） |
| `tools/diag/archived-reach-audit.ts` | 上者的实现；组合清单与 probe-det-baseline.sh 手工同步 |

**常备诊断（tools/diag/ 顶层）**

| 工具 | 用途 |
|---|---|
| `run-forensics.ts` | 分层取证采集（`--from-json` 子集重跑，AGENTS §4 Step 7）+ `base-loss-{run,worker}.ts` |
| `per-seed-diff.ts` | dump + diff（`--set` 覆盖），单种子 tick 级分歧定位（方法论基石 §I.5.1） |
| `decision-probe.ts` / `decision-trace.ts` | 单 tick 完整决策上下文 / 决策追踪 |
| `failure-classifier.ts` / `threat-ledger.ts` | 失败归因分类库 / M0 威胁台账 sweep |
| `death-attribution.ts` | 逐死亡事件归因（tick/凶手层级/行为分支） |
| `flip-scan.ts` / `ab-diff.ts` | 翻转扫描 / 语料 A/B 对比 |
| `ab-param.ts` / `ab-multi-param.ts` / `ab-fire-guard.ts` | 参数 A/B（paired CRN 官方口径） |
| `counterfactual-idle.ts` / `counterfactual-dodge.ts` / `idle-analysis.ts` | 反事实取证（tests 消费） |
| `travel-fire-probe.ts` | §217/§229 fireLineDetour 证据链工具 |

**评估/优化/仿真核心**

| 工具 | 用途 |
|---|---|
| `tools/eval/eval-suite.ts` | 官方口径 scorecard（35×N CRN、`--compare` paired A/B、`--dims` 维度拆解） |
| `tools/eval/godai-score.ts` + `calibrate.ts` | godai-score v7 评分器 / per-stage 参考标定（eval-refs.json） |
| `tools/sim/simulation-runner.ts` + `sim-pool/sim-worker` | 并行仿真内核（官方口径直驱） |
| `tools/sim/batch-sim.ts` | 批量仿真 CLI（确定性 smoke：五关种子字节一致） |
| `tools/optimize/optimize-godai.ts` / `curriculum.ts` | CMA-ES 参数面（两轮无 ROI 收口 §214/§222，留档） |

**测试门禁**

| 文件 | 用途 |
|---|---|
| `tests/godai-score-gate.test.ts` + `score-gate-core.ts` | 三难度分数门禁（10 seeds · truth/margin §233 口径） |
| `tests/godai-split-parity.test.ts` | 决策链 parity 锁 |
| `tests/godai-archived-knobs.test.ts` | 留档旋钮 L1 守卫（ARCHIVED_KNOB_GROUPS × CANDIDATE_SURVIVAL × DEFAULT 表互锁） |
| `tests/calibration.test.ts` | 标定回归 |

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
## §195. 中路钻探粘性驻守（midLaneStickyTicks）— S8 Riverbed 钻探败链修复（2026-08-14）

**根因（S8 hard 取证，60-seed 败局 37/38 = base_destroyed）**：
- 中央出生敌人停在顶部口袋（col 12-13 rows 0-5，水带 rows 6-7 挡坦克出不去），从 ~(13,4-5) 反复向下射 col 12-13。
- 每发子弹凿穿 1-2 格砖后死亡（bulletHitsTerrain 命中砖即 alive=false），所以 `laneThreatImpl` 每发只触发 ~10-60 ticks，间隔 70-130 ticks 释放。
- `MID_LANE_DEFENSE`（§165 SHIPPED）在间隙释放 → 玩家「走向锚点↔回去打猎」振荡，从未到位。环砖 t1305 被凿穿，下一发 #215(t1362) 有 71 ticks 无障碍弹道直达基地，玩家 6+ 格外（4px/tick vs 1px/tick）必输。
- 铁证：seed 10 t1189-1304 弹道窗口持续 claim（玩家 (4,22)→(9,21)），t1306 释放→回撤 (7,20)，t1362 致命弹，t1433 首伤，t2541 gameover。t1400 decision-probe：isBaseUnderThreat=false（§157 只看敌人对齐）、midLaneDefense 累计 148 次。

**修复**：新参数 `midLaneStickyTicks`（默认 **0** = byte-identical OFF）。`laneThreatImpl` true 时置 `_midLaneStickyHold = N`（GodAIInput 字段，endFrame 递减，镜像 §169 `_threatStickyHold`）；`MID_LANE_DEFENSE.evaluate` 中 `!laneThreatImpl → return false` 改为 `!laneThreatImpl && _midLaneStickyHold <= 0 → return false`。效果：钻墙间隙内玩家持续走向锚点 (12,22) 并持枪驻守，`laneShellAboveImpl` 换持枪对齐 + 弹-弹对消凿穿弹。

**A/B（60-seed 硬关全关，paired same-run）**：

| 参数 | S8 win | 全关 SUITE | fitness | 备注 |
|---|---|---|---|---|
| 0（基线） | 37% | 0.5333 | 527.0 | 74% |
| 60 | 38% | — | — | 不足（间隙 > 粘性） |
| **90** | **45%** | **0.5380** | **531.8** | **峰值** |
| 120 | 38% | — | — | 过长锚定反噬 |
| 150 | 38% | — | — | |
| 180 | 33% | — | — | W→L 反噬（seed 4/8/19/30） |
| 240 | 30% | — | — | 锚定太久，丢失场内压制 |

- classic 参：0.7252（参考 0.7259，无回归）；chaos 参：0.4926（=参考，无回归）。
- S8 配对 per-seed（sticky=90，seeds 1-30）：**16/30 vs 11/30**，L→W 转换 5 个（seeds 1/10/15/18/26），W→L = 0。机制验证：转换的恰是钻探败局，且锚定窗口 < 敌方间隙才不会过度占用玩家。
- 门禁全绿：godai-score gate classic 0.875 / hard 0.770 / chaos 0.733（floors 0.845/0.740/0.703）。

**教训**：
1. 粘性时长是双刃剑：必须 > 钻墙间隙（~70-130 ticks）才能桥接，但 > 120 ticks 开始锚定反噬（玩家失去场内压制/捡星节奏）。
2. 弹道触发（§163 教训）保持有效：只加时间粘性，不加敌情触发。
3. 远距败局（首伤时玩家 dist 12-27）未被覆盖（leash midLaneMaxDist=8 拒绝），属另一杠杆（§173 baseDamageRecall 已五连阴 closed）。

**发货配置**：hard 默认 `midLaneStickyTicks=90`；classic/chaos 保持 0（未 A/B 到正收益；classic S8 本就 100%，chaos 参考无回归）。
---
## §196. 钻探预警列完整性触发器（drill alarm）— 方向 1 证伪（2026-08-14）

**假设**：sticky=90 只桥接钻墙弹间短间隙（70-130 ticks）；长间隙（300-1700 ticks）里玩家游荡 22-28 格外，钻探恢复速度 > 玩家回防速度。若在 base column（cols 12-13, rows 0-23）检测到砖墙破坏事件（稀有精确，§163 弹道同性质），即可在长间隙内持续 claim MID_LANE_DEFENSE，让玩家守住锚点。

**实现**（已回滚，未进入任何提交）：
- params：`drillAlarmWindowTicks`(0) / `drillAlarmMinBricks`(2) / `drillAlarmLeash`(16) / `drillAlarmHoldTicks`(1500)，默认 0 = OFF byte-identical。
- GodAIInput：`_drillColBricks/_drillLastDestructTick/_drillDestructCount/_drillAlarmHold`，endFrame 每 tick O(48) 次 tilemap 读数砖（cols 12-13 rows 0-23），窗口内 ≥2 格破坏 → 置 hold；每次破坏刷新；endFrame 递减。
- think.ts MID_LANE_DEFENSE：hold>0 时跨间隙持续 claim，leash 放宽至 drillAlarmLeash。

**A/B（S8 hard, seeds 1-30, paired vs sticky=90 基线）**：

| hold | W→L | L→W | 结论 |
|---|---|---|---|
| 1500 | 12 | 2（seeds 7/14） | 灾难 |
| 300 | 6 | 1（seed 11） | 净负 |

**证伪机制（seed 1 hold=1500 铁证）**：t901 钻墙破坏 → 玩家被锚定 (12,21) 面朝上 866 ticks 不动（branch=midLaneDefense, alarmHold 694→522），期间**横向侧射**（row 25, dir=right）从 (11,24)/(11,25) 环砖被凿后的缺口打进基地 — t1595 hp 120→70、t1762 →20、t1767 →0，玩家全程站在锚点无动于衷。**statue 盲区**：锚点面朝上只能对消竖列弹，对 row 24-25 的横向弹完全失明；而正常打猎状态下玩家会主动处理环周敌人。

**结论**：
1. 任何「钻探进行中 → 长时间钉在锚点」的机制都是 §163 statue 变体 — 玩家被钉住时环周威胁（侧射、拆环司机）全部失控。远距群 8 局被近基侧射群淹没（12 W→L）。
2. 长间隙问题的真实解不是钉锚点，而是「别游荡太远」或「拆环司机本身要被处理」— 后者正是 §192 baseLaneSentry（850 权重，拆环威胁时应接管）的职责，但当前玩家距敌 >6 格且未对齐时 sentry 不接管（station 导航 baseLaneSentryStation=0 未 A/B，§193-B）。
3. 方向 1 关闭。后续优先方向：方向 2（近基错位群 probe — seed 21 侧射铁证已取：t1343 (11,24)、t1376 (11,25) 被凿，t1417 横向弹杀基地）+ baseLaneSentryStation=1 A/B。
## §197. 带内拆环导航 + 远距开火（baseLaneSentryInBandNav/FarRange）— 方向 2 证伪（2026-08-15）

**假设**：S8 侧射拆环败局（seeds 23/24/29，hard 27/60=45% 全关最低之一）：拆环司机沿 row 23-25 凿穿环侧砖 (11,24)/(11,25) 后横向弹直射基地（seed 23 纯侧射：t1285/t1319 死，顶部环零事件；seed 29 混合：钻探 enemy 沿 col 12 往返凿穿顶部环 (12,23)/(13,23) + 侧射并存）。§192 baseLaneSentry(850) 选中威胁（csb 在环砖落地瞬间翻转 true）却无行动分支 → 方向 2 = 给 sentry 补三个动作：带内导航（chase 到敌列）+ 对齐持位（冷却期不被 midLane 拽离）+ 远距开火（含砖线穿射）。

**实现**（已回滚，未进入任何提交）：
- params：`baseLaneSentryInBandNav`(0) / `baseLaneSentryFarRange`(0)，默认 0 = OFF byte-identical。
- think.ts BASE_LANE_SENTRY：farCandidate 门控（manhattan>range && farRange>0 && manhattan≤farRange && (csb||cbr)）接入 aligned 分支；砖线开火路径（blocked>0 时 shouldFireInDirImpl 放行 — 子弹击毁砖格继续飞行，seed 29 铁证：blocked=4 时一发穿 (12,17) 直达 (12,0-4)）；inBandHoldNav 持位助手（3 个 aligned 块退出点，moveDir=null 拒绝释放）；带内导航分支（ringBreached && tc.row≥23 && crow≥21 && 走廊全通 → moveDir 朝敌列，fire=false）。

**取证铁证（§7 先失败测试 + bullet-id diff 探针）**：
- seed 23（纯侧射）：玩家被 nav 送到 (7,21) 对齐 (7,24) 司机后，t973 冷却期 `onCooldown return false` **退出整个 evaluate**，nav 分支永不执行 → defenseIntercept 拽离列位，司机 6 秒凿穿基地 t1285。→ 持位需求由此而来。
- seed 29（混合向量）：钻探 (12,0-4) 往返 + corridor 砖 (12,17)（blocked=4，aligned 门 blocked≤1 拒绝）+ 顶部环 t1233 凿穿后 11 秒无人处理（sentry aligned-fire manhattan 19 > range 6 拒绝）+ 杀手弹道定版 t1911/1946 侧射 / t1983 钻弹。
- 修复后：seed 23 nav+far → stageclear t6070；seed 29 → t5139；测试 3/3 绿。

**A/B（决定性，35 关 × 60 seeds × 2 arms, hard, paired, sim-pool ~80s/轮）**：

| 参数 | S8 60seeds | 全 35 关 |
|---|---|---|
| baseline (sticky=90) | 27/60 | — |
| nav=1 | 32/60（L→W 5, W→L 0） | **净负**：s24 34→22、s32 49→37、s7 48→36、s5 38→32、s6 45→38、s26 43→35…（约 -35 net） |
| nav=1 far=22 | 33/60（L→W 8, W→L 2） | **净负更大**：s24 34→20、s32 49→38、s7 48→36…（约 -65 net） |

**证伪机制（s24 s1 铁证）**：带内持位把玩家钉在 (11,21) 25+ ticks（sentry claims 0→48），基线同期继续场内流动/下行；首发散点 t1963 仅 1px，RNG 分流后玩家错过拦截，base t2738 死（基线 t4061 clear）。**持位 = §196 statue 变体的短时版本**：任何「onCooldown 期钉玩家」的 claim 都中断场内流动（打猎/捡星/下行拦截），多数关卡净负；S8 局部收益（+5..6）被全关损失淹没。v2 变体（去掉全部持位，仅保留远距开火 + 带内 chase）实测 seed 23/29 不绿 — 持位是 S8 修复的必要件，也是全关危害件，无法拆分。

**结论**：
1. 三件套（nav/hold/far）在 S8 有效但全关净负 — 与 §196 同款教训：**钉/持位行为跨关卡是负杠杆**；S8 的 27/60 败局由「玩家被拽离正确列位」+「顶部环/环侧被凿穿」构成，持位治标、破坏他关治本成本过高。
2. 对齐持位的真实需求是「冷却期别被 midLane/defenseIntercept 拽离列位」— 更廉价的杠杆是调 midLane 的 claim 条件/权重（而非新增更高优先级行为），或 §193-B baseLaneSentryStation=1 A/B（sentry 主动导航到射击位，无钉住语义）。
3. 方向 2 关闭（与方向 1 同处置：回滚 + 记录）。S8 败局保留为已知开放项。
## §198. 卫位导航发货（baseLaneSentryStation=1）+ 两门槛 + buffer 契约修复（2026-08-15）

**背景**：§193-B 的 station=1 在旧基线 A/B（S34 +1 / 全关 +6 / classic 0 / chaos +2）后「待发货」，但从未在当前基线（§195 sticky=90）确认。方向 2（§197）证伪后，station=1 是唯一正收益候选。

**实现**（相对 §193-B 的三处改动）：
1. **默认值 1**：DEFAULT_GOD_AI_PARAMS.baseLaneSentryStation 0→1（classic restore 0 不变 — classic sentry mode=0 自关）。
2. **门槛 (5)**（chaos S34 seed 7 t2052 铁证）：带内（row ≥ 23，base 带）已有敌人时站台让位 — 玩家正在带内击杀（selectTarget=(6,24) 24hp fast）时不得被拽去带外目标。
3. **门槛 (6)**（chaos S34 seed 7 t2136 铁证）：玩家在目标下方（crow > tc.row）且面向上行、row 差 ≤ 3 → 站台步多余 — 玩家两格即达目标行，横向挪位拖 14 ticks 造成 RNG 蝴蝶翻局。
4. **buffer 契约修复**：门槛 (5) 循环里 `self.tankCell(t)` 覆盖外层 `tc` 引用的共享 `_tankCellBuf`（Navigator.ts 契约「调用后必须立即消费」）— 循环后所有 tc 读的是污染值（t=34633 实测 tc=(11,21)→(11,2)）。修复：先快照 bestCol/bestRow。此 bug 是本次引入（§193-B 原代码无循环无污染）。

**A/B（修复后，正确 base0 vs cand1，35 关 × 60 seeds × 2 arms, hard/chaos, paired）**：

| 难度 | baseW | candW | L→W | W→L | net |
|---|---|---|---|---|---|
| hard | 1591/2100 | 1593/2100 | 6 | 4 | **+2** |
| chaos | 1520/2100 | 1527/2100 | 13 | 6 | **+7** |
| classic | 1881/2100 | 1881/2100 | 0 | 0 | 0（byte-identical） |

- S34：hard +1（s12）、chaos -1（s58 W→L — 60-seed 口径 1 局噪声，gate 子集 1-20 不含）。
- 修复前（污染版）hard +3/chaos +10 — 污染版高估了收益；修复后 +2/+7 为真实值。

**gate（发货态 = station=1）**：classic 0.875（floor 0.845）、hard 0.779（floor 0.740）、chaos 0.733（floor 0.703）全过，无 per-stage failure（chaos S34 恢复 0.351 线上）。bun run check 1229 pass。

**结论**：
1. station=1 在 hard/chaos 双难度净正、classic 字节不变 — 满足发货标准（§193-B 定义的「全关净正 + classic 无回归」）。
2. 本轮铁证三连：tankCell 共享 buffer 契约是 think.ts 内易踩的坑（本 repo 已有 laneCorridorBlocked 同格退化先例 §193-B）；station 的每次横向挪位都是 RNG 蝴蝶风险源 — 门槛只允许「确无更紧急行动」时站台。
3. chaos S34 seed 58 的 W→L 保留为已知小噪声（1/60 口径）。
## §199. S34 站桩取证（方向 3 证伪）+ ab-param 口径 bug 修复（2026-08-15）

**背景**：方向 3（S34 Battlement 全关最弱关取证）完成 60-seed forensics + 站桩修复三轮 A/B 全负，方向关闭；同时发现并修复 `tools/diag/ab-param.ts` 的 stageIndex 口径 bug。

**S34 取证（hard 60-seed，stageIndex=0 官方口径，tmp/fx-s34-hard.json）**：
- 43 败局 = 40 base_destroyed（93%）+ 3 lives_exhausted；killer mix power 15 / armor 13 / fast 12。
- 死亡瞬间最后分支：navigate 18（其中 17 局 baseHp=0 — 玩家 dist 10-26 游走时 base 被端）> midLaneDefense 11 > dodge 6 > 站桩类 7（defenseIntercept|STATIONARY 4 + t2a|STATIONARY 3）> powerup 1。
- 玩家死亡热点：漏斗口 (13,22)×7 / (13,20)×5 / (12,22)×4；击杀热点：(13,1)×23 / (7,1)×11（顶部 spawn 区）。
- S34 布局：全砖漏斗无钢；base 上方保护薄（(11,6)=4 brick BOTTOM + (11,5)=18 BR + (11,7)=17 BL）；(11,24)/(11,25) 双砖在玩家下方。

**s1 死链（t5956 armor，tick-level probe 逐 tick 复现）**：
1. t5935-5943：玩家 (11,22)→(10,22) navigate 追 armor（threatChase）。
2. t5944：t2a claim（aimDir=down — 主方向量化，armor@(10,25) dx≈-8px 在 32px 对齐容差内）→ 开 1 发 — 中心线 (11,24) 砖 → **破砖**。
3. t5945-5948：cooldown 模型（hard=DEFAULT_RULES，fireInterval≈74 ticks）→ onCooldown 站桩（mv=null, fire=false, inFlight=1）。
4. t5949-5956：inFlight=0（砖碎）但**仍站桩**（fire 被 cooldown 锁）— armor 破 (11,25) 1 层后对 base 开火 → baseHp 5→0。
- **机制根因**：aimDir 主方向量化（对角敌人永远取 dy 主导方向）+ 冷却期站桩零产出 + 破砖竞赛（玩家 2 层砖 + 4 发 armor ≈ 10s vs armor 1 层砖直达 base）。
- 工具链确认：decision-probe / per-seed-diff / forensics / eval 均 stageIndex=0 → 轨迹一致；自写 probe 必须直接读 `input._moveDir/_fire/_lastBranch`（`getMoveDirection()` 在 tick 后调用会重复 think 消耗 RNG 分叉）。

**设计 A/B（cooldownBlockDrop 家族，S34 30-seed hard，全净负 → 回滚）**：
| 变体 | 规则 | net | 细节 |
|---|---|---|---|
| 1（fall-through） | 冷却期 + 中心线砖/钢挡 → 放弃站桩交 navigate | -2 | s6 L→W，但 s7/s8/s15 W→L |
| 1（换线横移） | 探测 ±TANK 列通线横移一步；双挡才 navigate | -4 | s5/s7/s8/s15 W→L |
| 2（窄化） | 仅 armor + blockDist ≥ 2×CELL 组合 | -1 | s15 W→L |

**证伪机制**：任何「离开站桩」都打断对 base 行的压制/火力 — navigate 走位期无线可打时零火力（drop=1 下 s1 玩家全程 navigate 0 fire 散步）；换线横移在漏斗地形两边都堵（两侧都是多层砖）→ 与 §133（早回防）/§138（守位格）/§197（带内持位）家族同款结论：**S34 防守位行为不可修，修复应在漏斗口上方的进攻性拦截/击杀节奏**。代码回滚，默认 0 byte-identical。

**口径 bug（§199 第二部分，工具修复）**：
- `ab-param.ts` 传真实 stageIndex（si）→ `killScore = 1000 × Math.pow(1.05, si+1)`（S34 时 ≈5.2×）→ hard/chaos 下 `dropOnScoreMilestone=5000`（DEFAULT_RULES）道具掉落频率分叉 → 与 eval/gate/forensics 的 stageIndex=0 是**两条不同轨迹**。classic 不受影响（`dropOnScoreMilestone=0`）。
- **历史影响**：§193-B（station A/B）、§198（修复后 +2/+7）、方向 4（沉睡参数筛选）均在分叉口径下测得 — 方向性结论仍有效，但数字不可与官方口径直接比较。gate 用 0 口径验证 station=1 发货 — 发货正确性不受影响。
- 修复：`stageIndex: si → 0` + 注释（官方口径契约）。

**遗留**：navigate 16 局（远处游走时 base 被端）是 S34 最大败因类 — 但对应修复（早回防 §133 / 守位 §138 / 持位 §197）已全部证伪，暂无候选。S34 暂挂，方向 5（S24/S20/S12/S14 弱关群）排队。

## §200. dodgeEscapeDepth 逃逸深度闪避（方向 5 D1）— 全关证伪（2026-08-15）

**背景**：方向 5（弱关群 S24/S20/S12/S14）取证完成 → S14（Bastion 水带关）最特殊：23 败全 lives_exhausted（100% 玩家死亡，base 满血）。逐 tick probe 定位死亡根因 → 设计逃逸深度闪避 → 局部正、全关负 → 回滚。

**方向 5 取证（hard 60-seed × 4 关，stageIndex=0，tmp/fx-weak-hard.json，240 runs）**：
| 关 | 败局 | 败因 | 关键 killer | 死亡热点 |
|---|---|---|---|---|
| S24 (idx 23) | 29 | 28 base_destroyed | basic 15 (52%) | base 附近 11,23 / 16,23 / 9,25；navigate 14 |
| S20 (idx 19) | 27 | 23 base_destroyed | power 11 + armor 7 | base 附近 9-10,22-25 |
| S12 (idx 11) | 25 | 23 base_destroyed | power 12 + fast 7 | 顶部 (7,5)×3；navigate 15 |
| **S14 (idx 13)** | **23** | **23 lives_exhausted (100%)** | — | 水带上横带 (3-9,12-14) |

全局 LAST-10-TICK 分支：navigate 478 (46%) > dodge 333 (32%) > t2a 134 (13%) > defenseIntercept 47 (5%)。

**S14 seed 60 死亡铁证（probe 逐 tick）**：
- S14 地图：r14-15 水带 `wwwwww` 横断全场；中央砖堡 r2-13；base (12,24) 钢 s 保护；r12: c0-3 森林 c4-7 空 c8-9 砖。
- t2150-2243：玩家 (5,12)↔(5,13) **up/down 抖动 93+ ticks** — 向上撞砖 (5,10)、向下撞水 (5,14)（两侧垂直都是 1 格死胡同）；左右开阔 + (2-3,12) 森林掩护却从不横移逃脱。
- 威胁源：fast@(1-3,10-11) + armor@(1-3,13) 持续压枪；hp 315→229→101→0。dodge 分支纯闪避（fire=false），baseHp=120 满 — 玩家在 dist 19-21 远处打猎中死亡。
- **机制根因**：`dodgeDirectionImpl`（ThreatAssessor.ts:384+）的 isSafeDir/canMoveDir 只看**下一步** — 候选格可走且安全就进；进后下 tick 该侧被地形堵（水/砖）→ 唯一候选是弹回 → 无限往返。§86 dodgeOscillationCounterFire / M9/M10 dodgeHorizonScore 全默认 OFF（历史全负）。

**D1 设计**：`escapeDepthImpl`（逐 CELL 地形+边界扫描逃逸格数，镜像 canMoveDirRaw 几何 — 注意 TANK box 用左上角，pcx/pcy 是中心 → 必须减 TANK/2）+ dodgeEscapeDepth 参数（默认 0 byte-identical）。触发：双侧垂直逃逸深度 < 阈值 → 探测子弹轴向（垂直弹→left/right）长逃逸：深度 ≥ 阈值 + canMoveDir + isSafeDir 门控 + §83 不沿子弹行进方向；双侧都长 → 选 base 近侧。

**A/B（stageIndex=0 官方口径）**：
- S14 30-seed 阈值扫描：depth=2 → net -1（s15/s24 L→W，s16/s21/s26 W→L — W→L 全 lives_exhausted，逃逸延后死亡但没解决生存）；4 → +2；6 → +2；**8 → +4**（L→W 6/W→L 2）；10 → +1（回落）。
- S14 60-seed depth=8：**net +5**（37→42；L→W 11：s1/s3/s7/s8/s19/s24/s28/s31/s33/s42/s43/s58，W→L 6：s16/s28/s41/s50/s52/s59）— 局部信号确认。s60 死亡 t2243 → stageclear t5522（0 死亡）。
- **全关 hard 60-seed 配对（决定性，4200 runs，89s，tmp/ab-full-d8.json）：net=-30**（L→W 172 / W→L 202；baseW 1582/2100 → candW 1552/2100）。
  - **W→L 败因分类（201 flip）：172 局 base 受损/被端（86%），仅 29 局玩家死亡 → 弃守**。
  - 重灾关：s6 -8（45→37，14 W→L！）/ s20 -5 / s15 -5 / s4、s24 -4 / s10、s13、s17、s21、s35 -3 / s25、s12 -3~4。
  - 正关：s14 +5 / s29 +5 / s5 +5 / s2 +4 / s18 +3 / s28 +3 / s32 +2 / s9 +2。
- **结论：第七个逃跑类参数证伪** — M9 horizon / M10 margin / §86 counter-fire / trapAvoidance / crossfirePathCost / cooldownBlockDrop（§199）全部同型：局部救玩家 → 全局弃守 → base 被端。**「离开位置」的修复路径已穷尽（7 连负），不再探索。** 代码保留默认 0（byte-identical，check 1252 全绿）。

**遗留**：S14 抖动是真问题，但修法必须「不弃守」（逃逸后立即回防 / base 威胁门控）— 鉴于七连负不继续。方向 5 四关共性 = 打猎期生存（navigate 46% + dodge 32%）+ base 附近被围 — 无现成参数可修 → **方向 5 暂挂**（与 S34 同命运）。下一候选：方向 6 clearSpeed（0.146 慢/拖沓）或进攻性拦截节奏（需新机制）。

## §201. 方向 6（clearSpeed 0.151 慢/拖沓）— 分析性证伪（2026-08-15）

**背景**：Phase III 维度表（§0.C.5）hard clearSpeed 0.146 偏低被解读为「慢/拖沓」— 方向 6 试图找玩家 AI 的节奏浪费。

**取证（60-seed hard + 控制实验）**：
1. **维度分布**：hard clearSpeed 0.151（518 clears）vs classic 0.817（60-seed 官方口径）— 5 倍差距。每关 clear tick p50：多数关超 classic refs 的 P95（如 Outpost hard p50 6382 vs classic slow 4238）。
2. **fire 1.4% = 冷却物理上限**：cooldown 模型 fire interval 1300/1.05 ≈ 1238ms ≈ 74 ticks → 理论上限 1.35%。实测 1.4%（1 星 1.05× 上限 1.42% — 完全吻合）。玩家满速射击，无窗口浪费。
3. **清关局 0 死亡**（Outpost 18/20 清关局 death=0；Twin Spires 19/20 death=0）— 无重生损耗。
4. **readyNoFire 33.6% 逐项排查**：navigate 31262 ticks（走路中无同线敌人 — 接敌几何，directMove 已垂直优先最大化同线概率）；t2a 站桩 = 冷却等待（就绪 24 ticks 即开火）；aggressive 就绪必开火（aimError=0）；powerup/dodge 不开火合理。
5. **敌人 tier 对照（30-seed，同 1 星同 rules，仅 tier 分布不同）**：Outpost hard 6123 vs relax 6086（±0.6%）、Twin Spires 7087 vs 7225（±2%）→ **敌人 AI 聪明程度对清关时间几乎无影响**。

**根因**：cooldown fireModel（hard/relax/chaos — 74 ticks/发）vs bulletCap（classic — 子弹落地连发 ≈ 10-15 ticks/发）→ **射速差 5-7 倍 = clearSpeed 差的全部来源**。这是 modern 难度的设计节奏（非 AI 可调）；eval-refs 用 classic 校准评 hard 造成维度结构性偏低。

**结论**：无 AI 拖沓可修 — 方向 6 关闭（无代码改动）。相关已穷尽：§170 huntCommit / §171 pathTargetMode（方向 4 无信号）、§200 dodgeEscapeDepth（全关 -30）。若需维度公平，应重校准 hard refs（评估层，非行为层）。

## §202. M0 威胁台账取证（threat-ledger + failure-classifier）— 硬关 2100 局基线（2026-08-15）

**背景**：plan/God-AI-Hard-Breakthrough-Implementation.md M0 — 为「威胁预算」重构取证。目标：把失败归因到可复现的逐 tick 证据，零行为变更。

### 工具链（全部新增，默认关闭/只读）

1. **威胁台账**（`tools/sim/simulation-runner.ts`，`threatLedger: true`）：
   - 事件驱动采样：签名（baseHp / 环格数 / 分支 / 玩家格 / 玩家生命 / 存活敌数 / baseThreatNow / slack 符号 / noOpReason / 每敌 csb/cbr 标志）变更即推一条 sample，pre-`endFrame()`（保证 `_lastBranch` 为本 tick 分支）；终局强制最后一条。
   - M0 几何 ETA 模型（占位，M1 将替换为 ThreatBudget）：`enemyToRingEta` = 曼哈顿距最近环格 / 敌速（csb/cbr 时 0）；`shootEta` = csb ? 0 : 移动 + 弹飞；`playerKillEta` = 距玩家曼哈顿 / 玩家速；`nearestThreatEta` = min shootEta；`threatSlack` = nearestThreatEta − 玩家最佳拦截 ETA（无威胁 -999 / 不可估 -1）。
   - `onCooldown` 镜像 think.ts M6 门（bulletCap → 弹数上限；否则 frame − lastFire < nextFireInterval）。
   - `noOpReason` = 站立且未开火时的 `_lastBranch`。
2. **失败分类器**（`tools/diag/failure-classifier.ts`，纯函数）：lives_exhausted→player_survival；timeout→unknown；base_destroyed 按序判：late_detection（首个 base 受损早于首个 csb/cbr）→ no_output_commit（危险窗内 ≥3 连续无产出，NO_OUTPUT_MIN_SAMPLES=3，附 secondary travel_late）→ multi_threat_overload（≥2 同时 csb/cbr）→ turn_locked（与 csb 敌同线 + 冷却 + 站立）→ travel_late（slack<0）→ wrong_target（进攻分支且有移动）→ unknown。全部输出可读证据串。
3. **CLI**（`tools/diag/threat-ledger.ts`）：`--seeds 60 --difficulty hard --json tmp/threat-baseline-2100.json`；`--report N` / `--report-runs "S34:1"` / `--from-json` 子集重跑。JSON 语料只存失败局 ledger（压缩形：弃每敌 ETA、短键，466MB→85MB）。
4. **测试**（`tests/threat-ledger.test.ts`，14 项）：parity（ledger 开/关 5 组 stage×seed 字节一致 — 台账零副作用）+ 每族合成 ledger 分类单测（含 no_output 连续计数、late_detection 先后序、孤立 no-op 不误报）。

### 基线（35 关 × 60 seeds × hard，2100 局，59s）

| 指标 | 本工具 | plan §1.1 |
|---|---|---|
| 清关 | 1582 (75.3%) | 1582 (75.3%) |
| base_destroyed | 448 (86.4%) | 447 (86.3%) |
| lives_exhausted | 69 (13.3%) | 70 (13.5%) |
| timeout | 1 (0.2%) | 1 (隐含) |

±1 局差异 = maxTicks 口径（36000 vs 旧基线 18000），非行为回归；两轮重跑字节一致。

**失败族分布（n=518）**：no_output_commit 311 (60.0%，secondary 全 travel_late) > multi_threat_overload 99 (19.1%) > player_survival 69 (13.3%，15 例 secondary multi_threat) > travel_late 24 (4.6%) > turn_locked 14 (2.7%) > unknown 1 (0.2%)。base 失败内：no_output 69.4% + multi_threat 22.1%。

**最差关（base 损失）**：S34 Battlement 40/60（no_output 27）> S8 Riverbed 32/60（17+13）> S24 28/60（23）> S3 25/60（19）> S12/S20 23/60 > S28 21/60（20 no_output，独木桥关 100% 站桩类）> S5 19/60（16）。

### 逐 tick 抽查（M0 门 — 每族可复现证据，10 局全过）

- **S1:58 travel_late 铁证**：玩家 (8,8) 顶部打猎，basic@(24,24) base 行推进；threatEta 40 → 0，玩家拦截 ETA 458（slack −418），base 120→70→20→0。玩家远在 16 格外，移动速度物理上赶不上 — 「赶不上」是设计常数，slack 全负常态。
- **S1:5 multi_threat_overload**：t1793 两 basic 同时 csb（@14,24 + @20,24），玩家 navigate 横移中，base 120→70→20→0，最后 1 tick 才进 defenseIntercept。
- **S34:1 / S1:13 no_output_commit**：defenseIntercept/t2a 站桩 ≥3 sample（约百 tick）无输出，环被啃 8→0 / base 被端；cd（冷却）标志在场 — 站桩 = 冷却等待而非死锁（站桩期间 base 仍在挨打）。
- **S1:50 turn_locked**：t918 玩家 (5,24) 与 csb 敌同线站桩 t2a 冷却中 — 但同线站桩恰是拦截位，本族证据强度弱于 no_output（后续可加「已开火失败」细分，M1 再议）。
- **S1:26 / S7:54**：player_survival（lives 耗尽、base 满血）/ unknown（timeout）。

### 修复与踩坑

- **台账环格口径 bug**：初版 BASE_RING_CELLS 按 4×4 边框算 12 格，与游戏真环不符（SimulationCombat.isBaseProtectionCell = 8 格：row br−1 × cols bc−1..bc+2 + cols bc±1... 两侧各 2）。已镜像修齐 — 环格数、ETA、签名全部对齐真环。
- **JSON OOM**：2100 局全量 ledger 466MB（Bun.write RangeError）→ 只存失败局 + 压缩形（短键、元组、弃每敌 ETA）→ 85MB。
- **报告表头错位**：9 值 11 标签 — 对齐。
- **no_output 窗口**：初版只认 csb/cbr 敌（hasThreatEnemy）；改为「危险窗」= 首次 active（csb/cbr 或 AI 自身 baseThreatNow）— 站桩在「AI 已知威胁」下才有罪，单测锁死。

### M0 门评估：通过

每族 ≥2 例可复现证据；parity 证明工具零副作用；基线复现 plan §1.1。→ M1 ThreatBudget（纯模型默认 OFF）开工。

## §203. M1 ThreatBudget 纯模型（Phase 1 §5，未接线）

**背景**：M0（§202）证明硬关失败的 60% 是 no_output_commit（站桩无输出）+ travel_late 共享的 slack 信号。Phase 1 交付一个只读、确定性的 slack 计算层，把「下一次合法转向」纳入行动 ETA。

### 设计（`src/ai/god/ThreatBudget.ts`，纯函数，零参数零状态）

- **actionEta**（§5.1）= nextLegalTurnEta（world.rules.turnCooldownMs，镜像 think.ts 判定：now − lastTurnMs < cd）+ movementEta（曼哈顿格×CELL/玩家速 + 垂直路径一档转向成本）+ aimAlignmentEta（转向执行 + 冷却窗）+ fireCooldownEta（冻结 nextFireInterval）+ requiredShotsEta（后续射击按基础 fireCooldown 再装填 + 末弹飞行）。
- **敌人期限**（§5.2，保守几何，不模拟敌方 RNG）：csb → shootEta 0；cbr → 同轴砖块数 ×（节奏+飞行）；否则 → 走到最近环格 + 固定攻环开销。damageWindow = ceil(baseHp / firepower) ×（节奏+飞行），环未破再加攻环开销。urgency = 0.5×baseHp 损耗 + 0.2×环损失 + 0.3×射击次数归一。
- **玩家期限**（§5.3）：killSlack = damageDeadline − playerKillEta；interceptSlack = enemyToRingEta − 到达且瞄准 ETA；missesSecondThreat = 任一其他敌 deadline < 击杀落地。
- **镜像纪律**：RING_CELLS 与 canShootBaseLine/canBreachRingLine 逐字镜像 SimulationCombat/SmartThreatModel — 35 关 × 10 点位 parity 测试与 AI 谓词完全一致（防 Phase 2 接线时双源分裂）。
- 数据全来自 config：resolveProfile（firepower/damage/maxHp，base 受损按 firepower 点数、杀敌按 pool damage）、节奏用 tank 冻结 nextFireInterval。

### §5.4 测试（tests/godai-threat-budget.test.ts，17 项全过）

- turnCooldownMs 200 vs 500 → ETA 单调增（500 > 200，永不绕过）；无规则 → 0。
- 需要转向 ≥ 已对齐；更多射击 > 更少；更远目标 movementEta 更大。
- csb 敌 shootEta=0；baseHp 120→10 → urgency 升、deadline 降；环全破 → urgency 升。
- killSlack 同敌同位随 baseHp 降而减；敌更近（清场后同一轴线 16,20 vs 24,20）→ deadline 不增。
- 同 World 同调用 → 结果字节一致 + World 不变 + RNG 状态不变。

### 踩坑

- **World 默认 classic**：`new World()` + loadStageData 不设 difficultyKey → baseMaxHp=1 → urgency 公式爆炸（−4.4）。测试必须 `w.difficultyKey='hard'` 前置。
- 比较不同位置敌人的 killSlack 不保证单调（deadline 与 killEta 同向移动）— §5.4 的单调性是「同敌同位、局势变差」语义，测试按此重构。
- 敌人节奏读冻结的 nextFireInterval（含 jitter，快照安全），后续再装填用基础 fireCooldown（确定性）— 与游戏门一致。

### 状态

Phase 1 完成（树绿：1283 tests / 0 fail，build ok）。未接线 — Phase 2 ActionContract（§6.1 防守分支 slack 门控，默认 OFF + A/B）为下一里程碑。

## §204. M2 ActionContract 防守站桩门控（Phase 2 §6.1，默认 OFF + A/B）

**背景**：M0（§202）头号失败族 no_output_commit（60%），其中相当一部分是防守分支在冷却期提交「站桩无输出」。plan §6.1 要求防守分支只有四种有效等待价值之一才允许提交静止分支。§199 已证明盲目 fall-through 会失守射击位，因此本版只拦截「站桩 + onCooldown」形态，不碰移动/开火中的分支。

### 实现（`src/ai/god/ActionContract.ts` + think.ts 三处位点）

- `contractStandingHold({world, player, threat, enemyBulletOnRay, ownBulletOnRay})` → `{valid, reason}`：
  - **enemyBulletOnRay**：敌弹中心在玩家射击线 ±(6+6)/2 内、朝 base 方向且未过 base 行 → 拦截在即（保留站桩）。
  - **ownBulletOnRay**：己方（玩家 1）子弹在该线 → 击杀已落地中（保留站桩）。
  - **killSlack > 0**：站桩击杀（ticksUntilFire + (shots−1)×cadence + 飞行，零移动）早于 damageDeadline → 射击比威胁快（保留站桩）。
  - 三者皆否 → 无效站桩，拒绝提交。
- think.ts 接线（全部 `prm.actionContractMode > 0 && onCooldown && 站桩` 门）：defenseIntercept 直射 commit → `continue`；defenseIntercept dig commit → `continue`；baseLaneSentry dig commit → `return false`。
- 参数：`actionContractMode: number` 默认 0；CLASSIC_MODEL_PARAMS 显式恢复 0（classic 字节一致）。
- 纯函数纪律：ray 扫描只读 `world.bullets`/player 几何；不消费 world.rng（拒绝路径跳过 aimError 调用 — 与 mode 0 字节一致兼容）。

### 测试（tests/godai-action-contract.test.ts，12 项全过）

- contractStandingHold 判定：无威胁 → invalid；敌清线 → csb 语义；killSlack>0 → valid「standing shot beats deadline」；killSlack<0（power 敌在 base 行、玩家 24 格远、刚开火冷却中）→ invalid；敌弹/己弹在射线 → valid。
- enemyBulletOnRay：同列朝 base → true；朝外 → false；不同列 → false。
- ownBulletOnRay：己弹 → true（伙伴弹同列也 true — 射线检查只关心线）；纯函数字节一致 + 不消费 RNG。
- 模式门控 parity：mode 0 === 未传参数（S34 seed 1 字节一致 5956/5956）；mode 1 在 S34 seed 11 改变行为（4476→5409，仍 gameover 但拖后）。
- 关键构造事实（踩坑）：真子弹从坦克中心发射（x = col×16 + 13，中心 = 坦克中心 144）— 初始测试用 col×16+6 是假几何导致 ray 扫描为负；killSlack<0 场景需 power 敌 + 玩家 24 格远 + 刚开火（basic 3 发 175 < deadline 200 仍为正 — 先写断言再算数会翻车）。

### A/B（hard 35 关 × 60 seeds，ab-param.ts，json tmp/ab-action-contract.json）

| 指标 | base | cand(actionContractMode=1) |
|---|---|---|
| 清关 | 1582/2100 (75.3%) | 1590/2100 (75.7%) |
| L→W | — | 34（no_output_commit 26 · multi_threat 4 · turn_locked 2 · player_survival 1 · unknown 1） |
| W→L | — | 26（无单关集中，最差 S24/S25 各 -2） |
| 净 | — | **+8（±10 噪声内）** |

- 机制命中目标族：26/311 no_output_commit（8.4%）翻胜 — 门控只挡「站桩+冷却」却回收了该族 8% 的失败，方向正确。
- 剩余失败 484/518 的失败 tick 变化 ±200 内中性；no_output 尾部 11 局延迟 >600 vs 9 局提前 — 无系统性拖后。
- S34（最差关 17/60）零翻转 — 其 no_output 形态非「站桩+冷却」（站桩多为移动/非冷却态），S34 需要 Phase 3 覆盖点而非本门控。
- 复现数字核对：ab-param 控制台 L→W=34/W→L=26 与 JSON 复核一致（注意 ab json 是紧凑键 `o/t/hp/l`，key 分隔符是 `|` 不是 `:` — 用错分隔符会得到假 35/34）。

### 状态

Phase 2 §6.1 机制版完成（树绿：check 全过 + build ok；12 新测试）。**保持默认 OFF** — 净 +8 在噪声内，按 plan §9/§10 纪律不提前 ship；机制与位点已验证，最终配置留给 Phase 5 CMA-ES。下一里程碑：Phase 2 §6.2 进攻分支 targetValue 排序键（engage/hunt 目标价值 = expectedBaseDamagePrevented / (playerReachEta + playerKillEta) 动态化）。

---

## §205. Phase 2 §6.2 targetValue 排序键 — A/B 证伪（2026-08-15）

### 目标

plan §6.2：engage/hunt 目标选择从「距离排序」升级为「目标价值」排序：
`targetValue(e) = expectedBaseDamagePrevented(e) / (playerReachEta(e) + playerKillEta(e))`，
其中 expectedBaseDamagePrevented = fp × max(0, floor((horizon − enemyToShootEta)/(cadence+flight))) 封顶 baseHp，horizon = reach + eta.total。

### 实现

- `params.ts`：`targetValueMode: number`（DEFAULT 0，CLASSIC_MODEL_PARAMS 恢复 0，接口注释引 §6.2 公式）。
- `ThreatBudget.ts`：`export function targetValue(world, p, e)` — 纯函数，公式如上，复用 enemyDeadline/playerActionEta/aimDirTo/playerShotsToKill/tankCell。
- `StrategyPlanner.ts`：`TARGET_VALUE_TIE_EPS = 0.05`（近并列回退标准距离序含 bonus/coop）；`selectTargetUncached` 两个门控重复循环（canHunt 分支 bonus −2/coop +5；normal 分支 pathTargetMode 双风味），模式 0 原循环不动 → 字节等价。
- `tests/godai-target-value.test.ts` 10 测试：6 个 targetValue 单元测试 + 模式 0 字节等价 + 模式 1 行为变化 + 2 个 wiring 集成测试。

### 测试期间的几何发现（已写入测试注释，后续沿用）

- `createTank(x,y)` 以 x+16 为中心 → 中心对齐 (col,row) 需 x=(col−1)×CELL。
- ThreatBudget.tankCell 用中心空间；GodAIInput 用角点空间（floor(x/16)）→ 断言 selectTarget 输出必须经 `ai.tankCell(enemy)`。
- cbr 语义：ring 砖本身计入 bricksBetween → 正上方敌 e2s = 60.02（1 周期），非 0；非对齐列敌（如 (13,19)）因 bricksBetween 水平分支只查 row 24/25 得 0 块 → **e2s = 0**（模型怪癖，测试须先探测）。
- isBaseUnderThreat 谓词：静态盒（|col−12|≤3 && row≥18）+ P4 竞赛（敌距 ≤18 且 玩家距+2 ≥ 敌距）+ §88 choke 点；wiring 测试须注册敌到 `w.tanks`（createTank 不注册），玩家贴近基地压掉竞赛分支。

### A/B 结果（hard，35×60，`tmp/ab-target-value.json`）

| 指标 | base | cand |
|---|---|---|
| 总胜局 | 1582/2100 (75.3%) | 1457/2100 (69.4%) |
| L→W | — | 301（no_output_commit 179 / multi_threat 59 / player_survival 43 / travel_late 12 / turn_locked 5 / unclass 3） |
| W→L | — | 426（新增失败） |
| 净 | — | **−125（−6.0pp）** |

- 全 35 关：31 关负翻转；最差 S18 −14 / S33 −13 / S19 −11 / S24 −11 / S16 −10；**S34 崩塌 17/60 → 4/60**（S34 为 no_output 重灾关 — 目标价值重排放弃其既有流程）。
- 35% 的局（727/2100）行为翻转 — 该键在真实关卡远非惰性。
- 即使赢的局也显著变慢（S12 6543→7875、S22 6569→7634、S20 5986→6874…）。

### 失败机制（结构分析，三条）

1. **伤害上限反直觉**：damagePrevented = min(baseHp, fp×interim)。对 horizon−e2s ≥ 3 周期（≈172 ticks）的任何敌恒封顶 120 → v ≈ 120/horizon ≈「按 killEta 取最近」；而 interim 1-2 的将死敌只得 50-100/horizon → **被系统性降权，玩家不补刀** — clear 局全线变慢即此。
2. **e2s > horizon → v=0 → 被剔除**：玩家被吸向 cbr 带（ring 砖可及列）反复横跳，放弃场控（S34 崩塌、W→L 426 局的主因）。
3. 分母 reach+eta.total 与分子 horizon 同源耦合，无归一化；canHunt/normal 两分支语义不一。

### 结论

§6.2 字面公式作为主排序键被实证否决。保持 `targetValueMode=0` 默认 OFF（plan §9/§10 纪律）；代码/测试保留固化语义。**对 §6.3 ActionIntent 的约束：收割/补刀必须是价值函数的保底项而非惩罚项；意图提交的稳定性（§170 huntCommit 已覆盖一半）+ 威胁事件重验是下一轮主线。**

### 状态

§6.2 完成（树绿：check 全过 7 连跑；10 新测试）。下一里程碑：Phase 2 §6.3 ActionIntent — 分支提交不保存永久目标，只存短期 intent（有效期 + 到期重验 + 新威胁事件释放），默认 OFF + A/B。

---

## §206. Phase 2 §6.3 短期 intent — A/B 中性偏负，保持 OFF（2026-08-15）

### 目标

plan §6.3：分支提交不保存永久目标，只存短期 intent（kind/targetId/expiresTick/minSlack/expectedProgress），6-15 ticks 重验，6 重释放条件。「这不是 huntCommitTicks 的简单加长版」。

### 实现

- `params.ts`：`intentMode`（默认 0，classic restore 0）+ `intentLeaseTicks`（12）+ `intentProgressWindowTicks`（10，仅 intentMode>0 时读）。
- `StrategyPlanner.ts`：`export interface ActionIntent` + `intentRead`/`intentWrite`；读点两处（canHunt 块顶 + normal 路径 §170 commit 之前 — 均在全部防守/覆盖分支之后，intent 永不阻塞威胁响应）；写点四处（canHunt value/distance、normal value/pathTargetMode/normal）。intentMode>0 时取代 §170 commit（其读块加 `intentMode <= 0` 门）。
- 释放条件（intentRead）：① 租约到期（frame ≥ expiresTick）② 目标死亡/不可达（不在 enemies）③ 停滞（窗口内未移动且 fireCooldown ≤ 0）④ 期限收紧（当前 damageDeadline < minSlack − 10）⑤ 新威胁（任一敌 deadline < committed − 15）⑥ 逃逸（距离 > expectedProgress + 2）。重验节流：⑤④ 只在 frame % 10 === 0（确定性）。
- `tests/godai-intent.test.ts` 8 测试全绿。

### 测试期间的两个关键发现

1. **World 默认 `seed = Date.now()`** — createTank 从 world.rng 抽速度抖动（±5%），未 pin seed 的测试在跨运行间 deadline 漂移 ±10-20 ticks → 断言脆弱（'holds' 测试间歇性失败）。修复：`pinSeed(w)`（seed 2：A/C deadline 差 11.2 < 15 释放边界，稳定 HOLD）。**既有测试不受影响的唯一原因是相对断言（同世界内等价/字节等价）与对速度项不敏感的绝对断言（60.02 是 cbr 纯 cadence 项）——新测试应从此显式 pin seed。**
2. 释放→重选→重承诺循环：新威胁/期限释放后落回最近目标并重新承诺 — 大多数释放行为学不可观测（重选=同一目标）；仅当场况已变（新敌更近/目标死亡/逃逸）产生差异。

### A/B 结果（hard，35×60，`tmp/ab-intent.json`）

| 指标 | base | cand |
|---|---|---|
| 总胜局 | 1582/2100 | 1568/2100（−0.7pp） |
| L→W / W→L | — | 206 / 220 |
| 净 | — | **−14（噪声内）** |

- S34 17/60 → 12/60（−5，L→W 4 / W→L 9）— 已最差的关更差。
- 20% 局（426/2100）翻转 — 释放/重承诺在真实对局中高频发生。

### 结论

§6.3 机制版完整落地（6 释放条件全实现 + 测试固化），A/B 判定中性偏负 → 保持 `intentMode=0` 默认 OFF。Phase 2 三机制汇总：§6.1 +8（噪声）· §6.2 −125（证伪）· §6.3 −14（噪声）→ **分支级微调不是 hard 突破的主杠杆**。组合探索归 Phase 5 CMA-ES。下一里程碑：Phase 3 动态攻击覆盖点（§7，S34/S8 类「驻守失去全场压制」：coverageValue 评分 + 6-15 tick lease + 多威胁护栏 + 失败回退 hunt/engage）。

### 状态

§6.3 完成（树绿：check 全过；8 新测试；5 连跑稳定）。Phase 2 全部收口，三机制保持默认 OFF。下一里程碑：Phase 3 动态攻击覆盖点。

## §207. Phase 3 动态攻击覆盖点（§7 CoveragePlanner）— A/B 证伪，S34 崩塌

### 实现

- `src/ai/god/CoveragePlanner.ts`（新）：`coveragePlanImpl(self, w, p, pc, enemies)` — 无基地威胁但有重大威胁（enemyToShootEta < 180 = COVERAGE_SHOOT_HORIZON，即 cbr/csb 带；walk 带 ≥ 400 天然排除）时接管目标。
  - 候选（几何，cap 8）：P 基线 + 喉道 (bc±1, br−2)/(bc, br−3) + 每威胁 (t.col, br−2)/(t.col, t.row−1) + 行/列射击交点。
  - 评分：`coverageValue = Σ prevent(e,i) − travel×COVERAGE_TRAVEL_COST(0.25) − exposure`；prevent 要求点与威胁同行/列且 clearLane（未对齐不预防 — 这是基线（基线不覆盖）输给喉道的机制；护栏 (b) 复用 clearLane 双列轨独立可防检查）。
  - 护栏（§7.3 硬块）：(a) threats ≥ 3 且 threats[1].deadline < killEta（killEta = (shots−1)×cadence + flight）；(c) baseDist > 12 且 returnEta > threats[0].deadline。
  - 12-tick lease + 低频重规划（coverageReplanTicks=12，签名 = 威胁 id 集合，不变则直接返回持有点）+ 释放（flank 到期/目标死亡）。
- `params.ts`：`coverageMode`(0)/`coverageLeaseTicks`(12)/`coverageReplanTicks`(12)。
- `StrategyPlanner.ts`：normal 路径在全部防守/覆盖分支之后、§6.3 intent 读之前插入 coverage 分支；`GodAIInput._coverage*` 状态 + reset()。

### 测试（tests/godai-coverage.test.ts，9 全绿）

- 关键场景验证：cbr 威胁 + 无基地威胁时持住 (12,22) 喉道（col 12）；mode 0 落回最近 hunt；(a) 需冻结玩家节奏 6000ms 才可触发（池模型 killEta ≈ 88 < 威胁 deadline ≥ 180 — 结构上罕见，测试通过改 p.nextFireInterval 人工构造）；(c) 需 p.speed ×0.9 直接调用 coveragePlanImpl 触发（race 谓词已把玩家限制在 ≤ ~6 格，returnEta 超不过 csb 180 下限）；lease 持住/释放/确定性/RNG 纯净。
- 排障记录：horizon 360 撞上 seed-2 抖动 deadline 360.12 → 改 450（cbr 带 ≤ 360、walk 带 ≥ ~590 之间）；`baseClearShotThreat` 是默认 ON 的已 ship 特性（开放场地任何对齐敌都算威胁）— 测试 harness 显式置 0；isBaseUnderThreat 的 race 谓词 (playerDist+2 ≥ enemyDist) 对测试几何极敏感 — 玩家须放 (14,20) 类近基地位。

### A/B 结果（hard，35×60，`tmp/ab-coverage.json`）

| 指标 | base | cand |
|---|---|---|
| 总胜局 | 1582/2100 | 1547/2100（−1.7pp） |
| L→W / W→L | — | 154 / 189 |
| 净 | — | **−35** |

- **S34（Phase 3 目标关）17/60 → 7/60（−10pp）**；S12 −5 / S30 −5 / S14 −5 / S08 −4 / S25 −4；S15 +7 / S21 +5 / S04 +3 / S11 +3。
- 15.2% 局翻转 — 分支在真实关卡上高频接管。

### 结论

M3 决策门失败（S34 崩塌，无一关达门）。失败机制：ring 未破时「预防 cbr 威胁」= 把玩家钉在喉道放弃清怪节奏（clear 变慢 + 波次叠加）；护栏 (a)/(c) 结构上罕见 → 覆盖点只在「安全时刻」接管，恰好排除最需要压制的场景。保持 `coverageMode=0` 默认 OFF；实现/测试保留供 Phase 5 CMA-ES 参考。Phase 3 结论：**S34 需要的是清怪节奏结构性加快，不是防御姿态**。下一里程碑：plan §8 M4 安全吃星或 Phase 5 决策。

### 状态

§207 收口（树绿：check 0 fail + lint 净；9 新测试）。Phase 1-3 全部 A/B 完成且保持默认 OFF。下一里程碑：M4 或 Phase 5。

## §208. §207 实现缺陷审计与修复（覆盖点）

### 起因

复查 §207 的 S34 A/B（17→7 崩塌）时，forensics 显示大量失败局"基地被毁时玩家距基地 20+ 格"（seed 3/8/17/28/32/33/41/59 等 dist 20-26）——覆盖点把玩家**调离**基地而非守住基地。逐行审计 CoveragePlanner 发现 5 个实现缺陷。

### 缺陷清单（先写失败测试复现，再修复）

| # | 缺陷 | 修复 |
|---|---|---|
| A | per-threat 候选 `push(t.col, t.row − 1)` 把"敌人与 ring 之间"放在敌人远离基地一侧（ring 在下方） | `t.row < br−1 ? t.row+1 : t.row−1`（朝基地方向） |
| B | `clearLane` 只查地形不查坦克：同列双敌时被挡威胁照样计满 prevent；候选点可落在坦克占位格 | clearLane 增加 alive 坦克扫描；push 排除坦克占位格 |
| C | `prevent` 求和可超 `baseHp`（单枪不可能防 > 120） | `if (v > w.baseHp) v = w.baseHp` |
| D | 候选点无基地邻域约束，20+ 格外点也能赢基线 | push 时 `dist > COVERAGE_MAX_PLAYER_BASE_DIST(12)` 直接拒绝 |
| E | guardrail (c) `returnEta > deadline` 条件太松（20 格 returnEta≈255 < 360 不挡） | 改为 `baseDist > 12` 直接拒绝（玩家远距=清怪态，交给 hunt/defense） |

测试：`tests/godai-coverage.test.ts` +3（DEFECT-A/B/D，直接驱动 coveragePlanImpl 绕开 race 谓词——player 9 格+2 ≥ 敌 7 格会触发 baseUnderThreat 走 defense，selectTarget 层不可测）。

### 修复后 A/B（hard 35×60，`tmp/ab-coverage-fixed.json`）

| 指标 | base | 修复前 | 修复后 |
|---|---|---|---|
| 总胜局 | 1582 | 1547（−35） | **1575（−7）** |
| S34 | 17/60 | 7/60 | **11/60** |
| L→W / W→L | — | 154/189 | 125/132 |

S34 三方 forensics（dist>12@loss）：base 16/43 (37%)、修复前 19/53 (36%)、修复后 20/49 (41%)——远距失败是 S34 固有模式（4 敌围殴），非 coverage 独有。

### 结论

缺陷真实存在、修复有效（net −35→−7，S34 7→11），但机制仍净负 → §207 判定不变：`coverageMode=0` 保持 OFF。修复保留（Phase 5 若纳入 CMA-ES 语义已正确）；mode 0 字节等价保持（全部修复在 coverageMode>0 路径，byte-identical 测试通过）。

### 状态

§208 收口（树绿：check 0 fail；12 覆盖测试全绿）。机制状态不变：Phase 1-3 全部 OFF。

## §209. 覆盖点第二轮审计：坐标系根因 + (b)/BUG-2 修复（正确实现仍净负）

### 起因

§208 之后复查实现：playerCell 返回 corner 空间 (13,19)（round），威胁用 ThreatBudget.tankCell center 空间 (12,17)——半格错位贯穿 prevent/clearLane/push/(b)。§208 的 DEFECT 测试多为假阳性（(11,17) 是 walk 带 deadline 512 ≥ horizon，非威胁；guardrail (b) 的 ray 到 BASE_POS 被 ring 砖挡 → independent 恒 false → (b) 从未触发）。

### 物理模型（bullet 源码确认）

子弹从坦克前缘中心出发：bx = tank.x + w/2 − BULLET/2 + v.dx*(w/2)（BULLET=6）。玩家站 corner (c,r)（x=c*CELL），向上子弹 bx = c*16+13 → 列带 c..c+1。威胁 corner (tc,tr) footprint 占列 tc..tc+1、行 tr..tr+1。命中条件 = 两带相交：|c−tc| ≤ 1 或 |r−tr| ≤ 1（laneAligned）。clearLane 扫带（相交列/行），端点 snap 到威胁 footprint 最近边缘，跳过目标坦克自身（其 footprint 与脚下地形不是障碍）。

### 修复清单

| # | 缺陷 | 修复 |
|---|---|---|
| 坐标 | 威胁 center 空间 vs 候选/玩家 corner 空间，错位半格 | 全模块统一 corner（round(x/CELL)）；footprint 带相交 laneAligned |
| (b) | `clearLane(威胁→BASE_POS.row)` 被 ring 砖挡 → ring 完好时 (b) 永不触发 | independent = lane 带分离（\|Δcol\|>1 或 \|Δrow\|>1）；covers = laneAligned(best, t.e) |
| BUG-2 | 快路径 `cur.length===0 → return held`：威胁走远后死守空点 | cur 空 → 释放 |
| horizon | 450 落在 walk 带（实测 ≥435）抖动边界 | 425（cbr 带 ≤421 全含，walk 带全排） |
| 占位 | push 用 center tankCell 单格检查 | footprint 带相交（c∈[tc−1,tc+1] 且 r∈[tr−1,tr+1]） |

### A/B 三版对比（hard 35×60, stageIdx=0 口径）

| 版本 | baseW | candW | net | S34 (idx 33) |
|---|---|---|---|---|
| §207 缺陷实现 | 1582 | 1547 | −35 | 7/60 |
| §208 半修复（coverage 几乎不触发） | 1582 | 1575 | −7 | 11/60 |
| §209 正确实现 | 1582 | 1555 | **−27** | 13/60 |

20 个 stage v2 回退（0,1,2,5,7,9,10,11,13,15,16,17,18,19,21,22,32,34），4 个明显改善（23 +7, 14 +4, 29 +2, 28 +3, 30/31/33 +2）。S34 13 < base 17 仍负。

### 结论

正确实现的净负（−27）比"几乎不触发"（−7）更差 = 覆盖点机制本身在 hard 全量上是负杠杆（把玩家钉在守点位置牺牲清怪节奏，S34 场景 4 敌围殴时尤甚）。`coverageMode=0` 维持 OFF。§209 修复保留——语义已正确，无坐标系陷阱，Phase 5 若纳入 CMA-ES 直接可用。

### 状态

§209 收口：14 覆盖测试全绿（新增 DEFECT-F 双 cbr lane 拒绝、BUG-2 同帧重入释放），check 0 fail（1327 tests）。机制状态：Phase 1-3 全部 OFF。

## §210. round → floor：格中点跳变造成的决策振荡（正确性修复，非调参）

### 问题

§209 定义 corner 空间为"sub-block containing the tank's top-left corner"，实现却是 `Math.round(x/CELL)`。round 的跳变点在格中点（16k+8），而"左上角所在格"的物理边界在 x = 16k：

- 几何错误：坦克左上角还在格 c 内（x ∈ [16c, 16c+16)），round 在 x ≥ 16c+8 时已报 c+1 → footprint 读错一格。
- 决策振荡：坦克在格中点附近 ±1px 抖动（导航 bounce、碰撞 settle）→ corner cell 每 tick 翻转 → laneAligned/clearLane/tankBlocksCell/push 占位全部跟着翻 → 计划 churn。

### 实测翻转（seed 2, hard, stage 0，威胁 x 跨 corner 11 中点 184 抖动）

| 威胁 x | round corner | floor corner | 计划 |
|---|---|---|---|
| 183.5 | 11 | 11 | (12,21) |
| 184.5 | 12 | 11 | null |

round 版计划在 (12,21)↔null 间翻；floor 版恒定 (12,21)。

### 修复

- cornerCell / laneAligned / tankBlocksCell / clearLane skip 端点 / collectCandidates push：全部 `Math.round` → `Math.floor`。
- `coveragePlanImpl` 入口重算 pc（floor）：调用方传的 `playerCell()` 是 round 语义（GodAIInput 全局约定，影响面大不改），模块内部必须自洽。
- `msToTicks` 的 round 保留（时间→tick 换算，非位置判定）。
- 新增测试：威胁 x 在 183.5/184.5 间抖动 4 帧，断言 coverage 计划恒定。验证：round 版该测试失败（捕获缺陷），floor 版通过。注意玩家侧抖动测试无效（入口 pc 重算已吸收）——必须抖威胁。

### A/B 对比（hard 35×60, stageIdx=0 口径）

| 版本 | baseW | candW | net | S34 (idx 33) |
|---|---|---|---|---|
| §209 round | 1582 | 1555 | −27 | 13/60 |
| §210 floor | 1582 | 1551 | −31 | 13/60 |

stage 级散布对称（better 25 / worse 29，最大单关 ±7）→ 噪声级，无系统性回归。机制净负判定不变（coverageMode=0 维持 OFF）。修复的正确性价值独立于胜率：消除振荡 = 决策稳定，这是 Phase 5 纳入 CMA-ES 前的地基。

### 状态

§210 收口：15 覆盖测试全绿，check 0 fail（1328 tests）。

---

## §211 覆盖点负翻转 per-seed 取证（2026-08-16）

### 背景

A/B v3（§210 floor）net −31，其中 W→L 翻转 120 个、L→W 89 个。用户指令：用 per-seed tick-diff 诊断负翻转原因，努力修复。

### 取证（S6-11, stage idx 5, seed 11 — 真 W→L）

翻转链（逐 tick 比对两版 dump）：

| tick | 事件 |
|---|---|
| 2380 | 首个玩家行为分歧：base (8,22) mv up selectTarget (10,12)（直接追杀）；cand 被 coverage 拉去 (10,22)（威胁 basic@(10,12) cbr, deadline 248）|
| 2430-2550 | 两版轨迹几乎汇合（2550 同杀一敌，相位差 ≈0.3 格）|
| 2595-2612 | A 版玩家在 (10,12) 停 17 tick（击杀窗口），B 版仅停 2 tick |
| 2666 | A 版 (12,10.07) 转 down；B 版 (11.34,10) 继续 right |
| 2669 | 敌人 roster 首次分叉：basic (14,10) vs (13,10)，hp 均 124 |
| 2751 | 两版世界已不同：base 有 basic@(14,15) 逼近（isBaseUnderThreat true，玩家南下防守）；cand 无此敌，baseLaneSentry 劫持玩家转 right 去 (14,14) |
| 3282/4519 | cand 基地 70→34→0 gameover；base 1500 后基地不再掉血、5842 stageclear |

玩家 HP 掉血事件两版完全相同（283/670/1332）→ 差异全在敌人链 = 纯蝴蝶（确定性 RNG 放大微小相位差）。

### 补充取证

- S20-5 (idx 19, seed 5)：双 W（非翻转）；S31-1 (idx 30, seed 1)：L→W（cand 反而赢）。coverage 介入同样改道，胜负方向随机 → 相位差本身无好坏，是 RNG 放大。
- S6-11 tick 2383：威胁 basic@(11,13) 已是 **cbr**（csb false cbr true）——"walk 带才触发"的猜想不成立，威胁集里大部分本就是 cbr/csb。

### 修复尝试：威胁集只保留 csb/cbr（§211 实验）

- 想法：walk 带敌人（deadline 纯几何、无视地形）不该触发 coverage 守点（S31-1 armor@(10,0) d346 是典型）。
- 实现：collectThreats 加 `canShootBaseLine || canBreachRingLine` 过滤（CENTER 空间 tankCell 判定）。
- A/B v4：net −33（vs v3 −31），仅 22/4200 runs 结果变化、净 −3（+2/−5），S34 唯一变化负向（cand|33|38: W→L）。
- **证伪**：过滤几乎不改变行为（deadline<425 的威胁绝大多数已 cbr/csb）；不能消除 S6-11 翻转（介入时威胁已 cbr）。回滚。

### 结论

1. 负翻转根因 = coverage 在玩家已处于有效拦截轨迹时改道 → 微小相位差 → 敌人链蝴蝶放大 → 基地失守。非机制级缺陷，不存在"修复"。
2. 四轮 A/B 全部净负（−7/−27/−31/−33），每次正确性修复后仍在噪声级负值徘徊 → 机制净负判定（§209）保持，coverageMode=0 维持 OFF。
3. S34（idx 33）是唯一稳定正信号关（13/60 vs base 7/60）——保留为未来启用的实验场景。
4. §211 取证流程沉淀为标准方法：per-seed-diff dump（口径 = DEFAULT+coverageMode=1 仅此项）→ 首个分歧 tick → decision-probe 两版分支对比 → 敌人 roster 签名（kind:floor(x/16),floor(y/16):hp 排序）→ 玩家 HP 事件对照（排除玩家血量因素）。

### 状态

§211 收口：csb/cbr 修复回滚，代码回到 §210 状态；15 覆盖测试全绿，check 0 fail（1328 tests）。DECISIONS.md §211 已记录。

---

## §212 M4 安全吃星 — 诊断先行收口（2026-08-16）

### 背景

breakthrough plan §8 (Phase 4) / §10 (M4)：低星级是 hard 长期战力瓶颈假设；纪律 = 先诊断，不直接提高 pickup 权重。M2/M3 已证伪，M4 前置于诊断。

### 诊断方法（tools/diag/m4-diagnose.ts，SIM_POOL_WORKERS=16 并行 ~75s+53s）

1. 全量 hard 35×60 = 2100 局（DEFAULT 参数，forensics on）：逐局 outcome / finalLevel / starsCollected / kills / deaths。
2. 失败局（518）子集重跑，加新增 `powerupCensus` 观察器：逐 star 记录 spawnTick / picked / minDist(px 曼哈顿) / despawnTick。

### 数据

| 维度 | 胜局 (1582) | 败局 (518) |
|---|---|---|
| avgFinalLevel | 1.43 | 1.19 |
| avgStarsPicked | 0.61 | 0.31 |
| avgKills | 17.94 | 9.95 |
| avgDeaths | 0.84 | 1.00 |

- 星级分布（胜局）：1★ 1033 · 2★ 440 · 3★ 93 · 4★ 14 · 5★ 2；（败局）：1★ 431 · 2★ 74 · 3★ 12。
- 弱关通关局星级：S34 avgLevel 1.53（全场第 4 高）、S8 1.48、S20 1.42——弱关失败与星级无关。
- 失败局 star 供给：355/518（69%）无 star 掉落；掉落 194 个，已捡 127（65%）。
- 遗漏分布（67 未捡 star，minDist 格数）：<2 格 0 · 2-4 格 4 · 4-6 格 23 · 6-8 格 15 · >8 格 25。早段（<50% 局长）且 6 格内仅 6 例（S13-34 / S16-19 / S16-27 / S20-34 / S24-16 / S29-50）。
- 错过的 star 大多在 75-100% 局长段掉落（27/67）——此时败局已定（基地将失守/玩家将死），捡星无济于事。

### 结论

1. 因果链是「输 → 击杀少 → 掉落机会少 → 星少」，不是「星少 → 输」。弱关通关局星级不低直接反证。
2. 「该捡没捡」的 safe-opportunity 干预样本 ≈ 4-6 例/2100 局——不足以支撑 pickup 权重/范围调整的 A/B 显著性。提高权重只会重演 §87 跨地图绕路问题，无对应收益。
3. M4 以诊断收口，不进入参数扫描；Phase 4 判定：吃星不是 hard 突破主杠杆（与 §207 的 S34 结论一致：需要的是清怪节奏，不是道具策略）。
4. 新增 `powerupCensus`（simulation-runner.ts + sim-worker.ts，flag-gated 默认关，纯观察零反馈）——道具类机制诊断的标准组件。

### 状态

§212 收口：check 全绿（1328 tests）。DECISIONS.md §212 已记录。m4-diagnose.ts + powerupCensus 保留为诊断工具。

---

# §213 / §214 Phase 5 CMA-ES — 启动、执行与收口（2026-08-16）

## §213 启动决策

- **用户指示启动**。plan §9.1 的正式前置（Phase 0-2 证明新结构改善某类失败）**未满足**（§205/§206/§207-§211/§212 全部证伪或诊断收口）——记录偏差并按 §9.3 协议保守执行。
- **搜索空间重定义**（§9.2）：从 v2 纪元 SEARCH_SPACE（21 参数，其中 19 个 init 与当前 DEFAULT 失同步 + 含 game-feel 参数）改为 **19 参数**：
  - 剔除 `aimError`、`suboptimalPathProb`（§9.2 明确排除的 game-feel 参数）。
  - init 全部同步当前 DEFAULT（工具此前从错误起点出发）。
  - 排除 turnCooldownMs；M1-M4 新机制参数（threat slack/防守 lease/释放 ticks/覆盖点/拾星绕行）不纳入（全部默认 OFF，§9.2 要求"只包含确实使用的参数"）。
  - 保留：reactionDelay / defenseRowOffset / defenseColSpread / threatRangeCells / maxPlayerDistFromBase / t8MaxInterceptDistCells / baseWallScanRadius / replanInterval / powerupMaxDivertDistance / endgameEnemyThreshold / huntAllyCount / baseRaceRange* / outnumbered* / campTimeoutTicks / t2aHighHpMaxRange。

## §214 执行与收口

### 批次 1（方向筛选 + 60 seeds 确认）

- `SIM_POOL_WORKERS=16 bun tools/optimize/optimize-godai.ts --stages 1-35 --difficulty hard --seeds 20 --generations 8 --fitness v7`（DIM=19，λ=13）。
- 筛选结果：fitness 632.7 → 675.9（+43.2）、胜率 77%→80%（+3pp）、floor penalty 3200→1600、base survival 80%→82%。
- 候选移动 8 个参数：defenseRowOffset 1→3 · defenseColSpread 3→4 · threatRangeCells 23→22 · powerupMaxDivertDistance 18→14 · baseRaceMarginCells 2→6 · outnumberedEnemyCount 5→4 · outnumberedRadiusCells 7→4 · outnumberedFieldEnemies 4→5。
- **60 seeds CRN 配对（新工具 `tools/diag/ab-multi-param.ts`，35 关 × 60 seeds × 2 臂 = 4200 runs，87.4s）**：net **+17/2100（+0.8pp）**，L→W 303 vs W→L 286——**纯噪声**。S34 17→16。

### 批次 2（warm-start 续跑 + 60 seeds 确认）

- `--init .workbuddy/optimization-phase5/optimization-summary.json --sigma 0.6 --opt-seed 2 --generations 10`。
- 筛选结果：fitness 632.7 → 669.7（+37.0）、胜率 77%→80%（+3pp）、base survival 80%→83%。**与批次 1 收敛同一方向**：defenseRowOffset 1→3 · baseRaceMarginCells 2→6 · threatRangeCells 23→18 · powerupMaxDivertDistance 18→16 · outnumbered 族继续下调。
- **60 seeds 确认**：net **+9/2100（+0.4pp）**——噪声。S34 17→15。

### 批次 3（核心参数聚焦）

- 仅两个两批都强动的参数：defenseRowOffset=3, baseRaceMarginCells=6。
- **60 seeds**：net **−16/2100（−0.8pp）**——净负。

### 停止条件触发（§9.3.5）

> 局部扫描 3～5 个候选批次仍只有 ±1～2pp 噪声，即确认参数面没有足够 ROI。

三批全部 ±1pp 噪声（+0.8 / +0.4 / −0.8pp）。**Phase 5 收口：DEFAULT_GOD_AI_PARAMS 不变，不发货任何候选。**

### 关键洞察

1. **筛选系统性高估**：20 seeds 下 floor penalty（minStageWin 20-45% 摆动）与胜率噪声（±2pp）主导 fitness——+37/+43 的"收益"在 60 seeds 全部蒸发。两个独立 CMA-ES 运行收敛的同一方向在 60 seeds 无信号（批次 3 净负），说明该方向是噪声产物而非真实梯度。
2. **与历史同构**：classic 纪元 §67（多轮探针 ±1pp 内，正式停止调参）、v2 纪元 M11 结论一致。**参数面已穷尽**；剩余提升需要结构性机制（行为/架构），而 M2-M4 结构性机制全部证伪——**hard 基准（SUITE 0.5132 / 胜率 73%）是参数面与行为面的局部最优**。
3. 沉淀：`tools/diag/ab-multi-param.ts`（多参数 CRN 配对 A/B，ab-param.ts 泛化）。产物保留在 `.workbuddy/optimization-phase5/`（batch 1）、`optimization-phase5-b2/`（batch 2）、`tmp/phase5-ab60{,-b2,-b3}.json`（60-seed 明细，供将来 holdout/取证）。

## §215 Hard 开放测试协议第 1 轮执行 — M0–M3（2026-08-16）

协议: `plan/God-AI-Hard-Open-Test-Protocol.md`;交接快照: `plan/open-test-round1-handoff.md`。

### §213/§214 口径勘误（作废声明）

上轮 handoff 取证发现: optimize-godai 的旧 stage parser 把 `--stages 1-35` 解析成 S1 单关——§214 三个批次的 CMA-ES **筛选**（方向寻找）实际只在 S1 上跑过;但三批的 **60-seed 确认**用的是新 ab-multi-param（parser 已修）真 35 关 × 60 seeds。因此:

- **作废**: "Phase 5 在 35 关参数面上搜索无 ROI" 的表述——CMA-ES 从未真正在 35 关面上搜索,只搜了 S1 局部。
- **仍有效**: 三批候选在真 35×60 确认中全部噪声/净负(+0.8/+0.4/−0.8pp)→ "S1 局部方向无 ROI" 成立,DEFAULT 不变的出货决定不变。
- **重开条件**: 若重启参数搜索,必须用 M0.1 修复后的 parser(`tools/lib/stage-spec.ts`),口径行自动钉死。

### M0 — 共享口径 + byte-identical 实证(通过)

- `tools/lib/stage-spec.ts`: parseStageSpec(all/范围/逗号/单关统一,拒绝空/反向/越界) + paramsHash(FNV-1a) + runHeader 口径行;接入 6 个工具。`tests/stage-spec.test.ts` 11 过。
- **byte-identical 实证**: HEAD(c6884a8, git worktree) vs 工作树(含 M0/M1/M2 全部未提交改动 + 本轮 intent 修复),hard 35 stages × 60 seeds × maxTicks=36000 = 2100 局:outcome mix 完全一致(stage_clear 1582 / base_destroyed 447 / lives_exhausted 70 / timeout 1),且 **518 条失败记录(含全量 forensics: 终局快照/事件史/末 10 tick 行动轨迹)逐字段 deep-equal**。上轮丢失的 `tmp/open-test-forensics-baseline.json` 基线由此重建(10.8MB),聚合数与上轮 handoff 记载完全一致(75.3% 胜率/447/70)。
- **口径坑(重要)**: replay 必须匹配 corpus 的 stageIndex——run-forensics 任务全部 stageIndex=0;`loadStageData(stage, N)` 的 N 影响 score-milestone 掉落 → 消耗 world.rng → 整局发散。counterfactual 工具首版 replay 全部 diverge 即此因,加 `--stage-index`(默认 0)后归零。

### M1 — ThreatBudget 语义分层(完成,默认 OFF)

playerActionEta 五字段互斥单次计费(total 严格和,转向窗口 aim+换轴共享);enemyDeadline 三层(arrivalLowerBound 排序下界 / damageEarliest 最早落地 / damageDeadline = earliest − 安全余量,唯一可作行动依据);cbr 破环单次计费、环没了不收幻影、ticksUntilFire 计入;targetValue horizon = eta.total(原来 reach 重复加);坐标协议三约定(center-floor / corner-floor / round)测试钉死。`tests/godai-threat-budget.test.ts` 26 过。

### M2 — ActionContract 拦截段 + intent(完成,默认 OFF)

enemyBulletOnRay 重写为拦截段判定(aimDir 签名: 炮口前方+未过基地近缘+朝基地+速度可行);midLaneDefense 两个站桩 hold 接入 actionContractMode;intent committedSlack = deadline − killAssessment ETA。`tests/godai-action-contract.test.ts` 18 过。

**intent `<0` 释放修复(本轮接手后第一件事)**: `tests/godai-intent.test.ts` "holds the committed target within the lease" 失败的根因——开阔地(环已无)walk 分支 deadline = 走+飞(几何下界,敌零转向成本),而玩家 killEta 含全成本,远敌 commit 时 killSlack 即 −52;重验 `currentSlack < 0` 每次都释放 → lease 对"追不上的目标"形同虚设,恰是 intent 要治的抖动。修复:`EnemyDeadline` 新增 **directThreat**(csb||cbr)字段,`<0` 释放仅对 directThreat 目标生效(deadline 是真实首发时刻);walk 分支只受 collapse(committedSlack−10)与新威胁(deadline 差>30)释放约束。8/8 过。INTENT_THREAT_DELTA 15→30(1 格走近≈15.5 ticks,15 会被距离抖动触发)。

### M3 — 反事实因果工具(完成,协议主突破方向)

`tools/diag/counterfactual-idle.ts` + `tests/counterfactual-idle.test.ts`(纯分类器 6 用例):

- **检测**: 防守/hunt 分支提交 + moveDir=null + fire=false 且玩家在场,连续 ≥3 tick 为静止段,只取段首;限首次基地受伤前 600t(pre-window)。
- **分支**: 对每个事件确定性 replay 0..T(分支点在 T 执行前,替代动作可在静止 tick 当拍生效),`cloneWorld`/`restoreWorld`(RNG/子弹/地形/cooldown 全随快照),四支: continue(原 AI)/ turn-and-fire(合法转向+开火,hold 语义)/ move-to-intercept(几何转向逼近,brick≤2 格即爆破,steel/water 换轴)/ clear-or-advance(brick≤6 格主动清线)。240t 窗,60/120/240 检查点;ScriptedInput 仅存在于 tools/diag。
- **口径细节**: 移动判定用净位移 ≥8px(1px snap 回正不是移动);" acted"= fire 或真实 step;`avoidedByInaction` 标记"靠站桩(如车身挡弹道)而非主动干预避免伤害"的 causal 事件。
- **结果(hard, 40 局 base_destroyed 均匀抽样)**: 14 局有合格静止段、**26 局根本没有**;14 事件:idle_causal 2(14.3%)/ idle_legitimate 6 / travel_or_turn_causal 6 / unresolved 0。
- **人工抽查 14/14 与分支证据一致(门槛 ≥80% 通过)→ M3 通过**。明细 `tmp/cf-idle-r1.json`。

### M3 结论(改变主线)

1. **idle_causal 低(14.3%)→ 协议 §6.3 条款生效: 不得再以消灭 idle alert 为主线。** 威胁账本 no_output_commit 69.4% 的表观占比严重高估了 idle 的因果地位——2/3 的 base_destroyed 局在首次受伤前 600t 内连静止段都没有。
2. **主导机理是"窗口在静止发生前已关闭"**: 8/14 事件 commit 时 threatSlack 已 −81..−573;所有替代动作与 continue 同 tick 受伤(dmg@41/44/98/141 高度重合)。根因在更早的选靶/路线/转向决策,M4 的统一行动候选应优先覆盖**决策点前移**(travel/turn 根因),而不是消除静止。
3. **"开火 ≠ 更优"实证一例**(S30s27): 站桩开火分支 dmg@190 vs continue dmg@-(240t 内无伤)——朝威胁开火的子弹会打掉自家环砖/给敌人清障,防守开火本身可能引弹上身。M4 候选评估必须包含这类反例。
4. idle_causal 两例之一(S17s38)是 fb 目标(事件时刻无 csb/cbr,回退最近敌): causal 的载体是"没能击杀最近敌",更像决斗失败而非静止;分类规则按协议字面成立,但归因时注意 fallback 语义。

### 下一步

M1–M3 通过 → M4(统一行动候选最小原型)允许启动;候选集设计应以 §215 M3 结论 2/3 为输入(前移决策点 + 开火反例约束)。

## §216 Hard 开放测试协议第 1 轮 — M4 统一行动候选: paired A/B 净 −116,方向否决 (2026-08-16)

协议: `plan/God-AI-Hard-Open-Test-Protocol.md` §8;前置: §215 (M0–M3 通过)。

### M4 实现 (candidateMode=1, 默认 0 = OFF, byte-identical 保持)

- `src/ai/god/ActionCandidates.ts` + `think.ts` UNIFIED_CANDIDATES 接线: kill-current(站桩/approach) /
  intercept-base(站桩/approach) / clear-lane(清砖后补射) / return-defense 四候选,与现有 dodge/engage/
  defense/return 分支并列,`GodAIInput._candVerdict` 记录裁决供取证。`params.candidateMode` 默认 0。
- **修站桩误判**: verdict 携带 `standingShot`(只有站桩评估才为 true),think.ts 不再从
  `firstOutputTick === 0` 重推导——对齐 approach 到达成本为 0 但射线可被挡,重推导会把 approach 当站桩。
- **射线把基地鹰当 blocker**(S30s27 base form): `isBaseCell` + `fireRayBlocked`(完好环砖 + 2×2 基地足迹),
  `firstBrickOnRay` 返回 `'base'`;站桩/approach/clear-lane 火力全部受该门约束。
- **approach 门控**: kill-current `blockedRay = alignedK && kRayHit !== 'none'`(穿砖击杀的 slack 是虚构的,
  让位 clear-lane);intercept approach 同样 `!blockedU`;clear-lane 补射必须 `!fireRayBlocked`。
- 死循环修复: `ThreatBudget.bricksBetween` 下行循环缺边界检查(敌人 center row == BASE_POS.row 时无限循环),
  加 `r >= 0 && r < GRID` 守卫 + 2 个回归测试。`tests/godai-candidates.test.ts` 10 过(含 standingShot
  true/false 与 base 射线反例 3 个新用例)。

### 报告 (协议 §8.3 模板)

```
caliber:  difficulty=hard  stages=35  seeds=1-60  stageIndex=0  maxTicks=36000
          paramsHash base=6ef1b952 (candidateMode=1)  4200 runs, 185.0s
total W/L/net:  base 1582/2100 (75.3%) → cand 1466/2100 (69.8%), net −116
                L→W 222 (10.6%)  W→L 338 (16.1%)  翻转率 560/2100 = 26.7%
base_destroyed:  (W→L 翻转子集 n=338, run-forensics 重跑) base_destroyed 308 (91.1%)
                lives_exhausted 30 (8.9%)  max_ticks 0
lives_exhausted: n=30, base HP 均值 91 (中位 120), 40% 局 1 发即倒
weakest stages:  S34 17→13 (−4)  S3 36→27 (−9)  S6 45→34 (−11)  S26 47→38 (−9)
                 S7 48→40 (−8)  S31 48→39 (−9)  S20 33→29 (−4)  S24 31→34 (+3 唯一正)
best/worst flips:  worst W→L: S8s56 W9283→L1745  S33s14 W10833→L5416  S20s20 W8052→L4894
                   best L→W:  S24s36 L3027→W7313  S11s10 L1667→W6437
failure-family conversion:  W→L 翻转 338 局的死亡模式: 基地先行沦陷 (91.1%), 玩家命数耗尽 8.9%,
                 2 局为玩家自毁基地 (player 自伤弹, S30s27 类风险在 candidateMode 下真实存在)
determinism check:  翻转子集 338 局 (candidateMode=1) 由 run-forensics 独立重跑,
                 0/338 结果或 tick 不一致 (byte-identical)
verdict: reject implementation
```

### 判定与洞察

1. **否决依据**: net −116、30/35 关净负、翻转率 26.7% 而净负 5.5pp——paired A/B 主筛选即明确负信号,
   按 §8.3 不进入 holdout(holdout 只为通过主筛选的候选做最终行为结论)。DoD "充分证据否决该方向" 成立。
2. **翻转子集取证 (n=338)**: 91.1% 为基地沦陷;末 10 tick 规则分布 candidate 分支仅占
   candidateKill 61 (2%) / candidateIntercept 100 (3%) / candidateReturn 18 (1%)——多数翻转不是候选层
   直接开错火,而是**机会成本**:站桩 hold / 拦截走位期间吃弹、丢回防时机,即 M3 结论 2 的实锤
   (窗口在静止前已关闭,提前占位并不等于提前得手)。
3. **2 局自毁基地**: 证实 S30s27 反例在 candidateMode 下真实存在——即便有 fireRayBlocked 门,
   站桩开火仍可能被敌人闪避后命中自家基地(子弹 6px 弹道与 scan 线不一致)。这排除了"收紧门控后重试"
   的简单路径,进一步支持方向否决。
4. **S18 (+3) / S24 (+3) 正信号为噪声尺度**: 与 15 个 −9..−19 的负关相比不构成可重复方向;
   协议 §8.3 禁止把筛选噪声写成行为改善。
5. **沉淀**: `tmp/m4-candidate-ab.json` (4200 局明细)、`tmp/m4-flip-corpus.json` (W→L 翻转子集)、
   `tmp/m4-flip-fx.json` (338 局 forensics)。`candidateMode` 保持默认 0,出货行为不变 (byte-identical)。

### 下一步

M4 方向否决 → M5 (开放测试矩阵) 无候选可继续;按协议 §10 DoD, 本轮以"充分证据否决该方向"收口。
M5 剩余可选项: ① 以 M3 结论 1/4 的"决策点前移"为输入设计下一候选 (travel/turn 段干预,非静止段);
② 或接受 73% 为当前行为面局部最优,归档本轮证据。

## §217 M5 候选 — travel-phase 火力偏离 (fireLineDetourMode): 诊断先行, 机会空间 33% (2026-08-16)

前置: §215 (M3 结论 2: 窗口在静止前已关闭, 主导机理在 travel/turn 段), §216 (M4 静止段干预净 −116 否决)。

### 诊断 (tools/diag/travel-fire-probe.ts, 决策点前移方向的机会空间测量)

- 输入: `tmp/m4-candidate-ab.json` baseline 败局 518 局 (hard, stageIndex=0), 确定性 replay,
  逐 tick 记录旅行分支 (defense + hunt 集) 中的机会: 对齐 + 射线全清 (环/基地/任何地形) +
  killSlack > 转弯窗 13t + 未面向 (需一次转弯 — 已面向则 baseline 本就会开火) + 不在冷却,
  且目标为 csb / cbr / 基地逼近带 (row ≥ 20, |col−12| ≤ 6) 之一。
- 结果 (60 局抽样): **33.3% 败局有 ≥1 个机会 tick** (mean 10.3 tick/run), **75% 的机会先于首次
  基地受伤**; 机会 100% 落在 navigate 分支; 目标构成 fb 94% / cbr 6% (csb 0 — csb 对齐且
  有 slack 时玩家本就在处理)。firstOppTick 中位 ~3000 (局中段, 干预后仍有大量局时)。
- 与 M3 (idle 14.3%) 对比: travel 段机会空间是静止段的 2.4×, 与"窗口在静止前已关闭"一致。

### 候选设计

- 新参数 `fireLineDetourMode` (0 = OFF, byte-identical)。开启时, HUNT 分支顶部先行检查:
  若当前有对齐、射线全清、未面向、非冷却的目标 (csb/cbr/带内 fb) 且 killSlack > 13,
  则本 tick 转向 + 开火 (偏离导航计划一个转弯窗 = 13t, killSlack 门槛保证击杀仍胜 deadline),
  否则导航照旧。S30s27 安全: 走廊检查 (任何非空地形, 含 base 格) + fireRayBlocked (环+基地)。
- 纯几何谓词 `travelFireDetourDir` (ActionCandidates.ts), 无 RNG 消耗 (fire roll 在提交处按
  aimError 纪律执行); 位于 HUNT 内部 → dodge/t8/aggro/pickup 等上层分支不受影响。
- 纯谓词单测 + 接线走查, 通过后 paired A/B (hard 35×60 CRN) 做主筛选, 正信号才进 holdout。

### 依据与拒绝项

- 拒绝: 在其它分支 (defenseIntercept/midLane) 也挂 detour — 探针显示机会 100% 在 navigate,
  扩大范围徒增干预面 (M4 教训: 过早占位 ≠ 提前得手)。
- 拒绝: 无 slack 门槛的 "见敌就开火" — 会打断正在进行的 dig/逃脱 (carve-dig 窗口被 13t 打断
  的代价以 killSlack>13 门控兜底)。

## §218 M5 候选 — travel-phase 火力偏离 (fireLineDetourMode): paired A/B 三批全正向, SHIP (2026-08-16)

前置: §217 (诊断先行, 机会空间 33.3%, 候选设计与闸门)。本节完成该候选的 A/B 验证与裁决。

### 实现

- `travelFireDetourDir` (ActionCandidates.ts, 纯几何谓词, 无 RNG) + `DETOUR_TURN_WINDOW_TICKS = 13`;
  `fireLineDetourMode` 参数 (默认 0, OFF, byte-identical); HUNT 分支顶部接线 (§162 carve-dig 之前,
  不改变 dodge/t8/aggro 上层分支)。单测 10 用例 (tests/godai-travel-fire.test.ts, 含 facing/aligned/
  band/corridor/ring/base/slack/hunt 偏好), 全过。
- 探针工具 travel-fire-probe.ts 增补: difficulty 参数化 + gateFail 归因 (aligned/facing/cooldown/
  slack/corridor/ray/band 逐门计数), 用于 classic 结构性 no-op 的归因。

### 证据 (hard, 官方 stageIndex=0, CRN 配对, 35×60×3 独立 seed 语料)

| 语料 | seeds | baseW | candW | net | L→W / W→L |
|---|---|---|---|---|---|
| primary | 1–60 | 1582/2100 (75.3%) | 1594 (75.9%) | **+12** | 66 / 54 |
| holdout | 61–120 | 1565 | 1581 | **+16** | 59 / 43 |
| b3 | 121–180 | 1544 | 1558 | **+14** | 58 / 44 |
| 合计 | 1–180 | 4691/6300 | 4733/6300 | **+42 (+0.67pp)** | 183 / 141 (~2.3σ) |

- **最弱关**: S34 (堡垒) +1/+3/−3 → 180 局净 +1, 无崩塌; S24 +4/+4/0; S31 0/+7/+3;
  S22 +6/0/+3 (含 §217 目标干预例 S22s28 所在关); S12 +4/+1/−3。
- **稳定小亏** (无崩塌, 记录在案): S19 −2/−1/−2 (−5/180), S13 −2/0/−2 (−4/180)。
- **failure-family conversion** (97 局 W→L, set1+2): base_destroyed 主导; killer mix
  armor 22 / power 21 / basic 21 / fast 15 / player 2。2 局玩家自毁基地 (S2s12 t5226,
  S22s92 t1418) — 致命弹均为 **t2a 分支** (§216 记录的老类, baseline 同概率), 系 detour
  额外开火扰动 RNG 轨迹后的间接结果, **非 detour 直发**: 走廊+射线门在 6300 局中 0 漏
  (环/基地/任何非空地形均在门外)。
- **guardrail classic**: net 0, 0 flips — 结构性 no-op: 探针 (gateFail 归因) 证实 classic
  败局中到达 band 门的对齐目标恒为带外 (classic 敌人不入基地带), detour 永不触发,
  属预期非缺陷。chaos guardrail (35×20) 复跑记录见 §219 (gated rollout 后补)。
- **turn+fire 语义 (评审 P1 澄清, 2026-08-17)**: 候选的 detour commit 与人类同帧
  "转向+开火" 输入完全同构 — `SimulationPlayer` 先 `p.dir = dir` 再 `tryFire` (子弹沿
  新方向立即生成), `SimulationCombat` 的 200ms 转向冷却只推迟视觉转向 (回退 dir + halt),
  不阻止开火。因此 §217 模型里 "13t 转弯窗成本" 是**未来移动**的转向成本, 开火本身
  立即生效 — killSlack > 13 门槛对 deadline 只多不少, 无公平性违例 (人机等价, 引擎
  对输入源不区分)。测试: tests/godai-travel-fire.test.ts §218 describe (3 用例 —
  谓词 active-cooldown 提交 / think 真实接线 / 人类输入字节级同构)。
- **determinism**: 97 局 flip corpus 重跑 0 mismatches; 探针 60/60 + 80/80 逐字节一致。
- **verdict: 候选通过初步 A/B, 允许 gated rollout (NOT shipped)** — 3/3 独立语料正向
  + holdout 复现 + 最弱关无崩塌 + classic 护栏通过 (chaos 护栏 §219 补跑),
  协议 §10 DoD "至少一个候选行为在 hard 35×60 paired A/B 有明确正向信号" 由此满足;
  按评审要求, 仅在 candidate-on 完整验证确认 base_destroyed / 最弱关 / lives /
  clear speed 无恶化后才考虑将 `fireLineDetourMode` 默认打开 (届时再升格为 ship)。
- **交付纪律** (协议 §11): `fireLineDetourMode` 保持默认 0 (独立 feature flag), 出货行为
  byte-identical; 证据全部落盘 tmp/m5-*.json, 随时可复跑。

### 依据与拒绝项

- 接受: 幅度小 (+0.67pp) 但三批方向一致且无一负批, 翻转不对称 183:141 (~2.3σ) —
  §9 条款 5 (连续 3–5 批 ±1–2pp 且 holdout 无信号才停止) 不适用, holdout 有信号。
- 拒绝: 因幅度小而不录 (M4 教训的反面 — 证据链完整时按证据裁决, 不按期望裁决)。
- 拒绝: 把 S19/S13 稳定小亏当作崩塌 — 无任何语料显示 ≥3 局/批的关级崩塌。
- 拒绝: 因 2 局 t2a 自毁翻转收紧 selfFireBaseGuard — 与 §216 结论一致, 该风险是基线
  固有类 (lenient 默认), 非本候选引入; 单独候选另行论证。

### 后继计划 (开放测试继续的候选方向, 按证据强度排序)

1. **defenseIntercept 开火窗口** — 反事实语料 (cf-idle-r1.json) 中最大的未动类 (6/14 事件,
   分支占比最高); 与 M5 同构 (防守分支的"能打不打"窗口), 可复用走廊+射线闸门与探针
   方法学; 高危分支 (改错直接送基地), 收益与风险同高。
2. **CMA-ES 参数面** (协议 §9 重启条件现已满足) — 搜索范围限定结构参数: slack safety
   margin / intent lease 与无产出释放 ticks / coverage 最小收益 / 多威胁 opportunity cost /
   安全道具最大绕行成本。排除 turnCooldownMs、aimError、suboptimalPathProb 等 game-feel
   参数与已证伪方向。
3. **dodge 分支 idle 取证** — 失败结局末 10 tick 21% 在 dodge (死亡占比第二大), 但反事实
   语料零 dodge 事件; 按纪律先跑 dodge 分支 idle 反事实, 确认可干预窗口再设计。
4. **"太迟"防御结构分析** — 91% 失败为基地被拆, 且 67% 败局在 travel 段无任何火力机会
   (非"能打不打"而是"人不在/挡不住"); 首伤→死亡窗口的玩家行为与 baseLaneSentry 缺位
   (死亡占比仅 1%) 未分析, 是 travel 段之外的独立机理。
5. **t2a 自毁守卫收紧** (selfFireBaseGuard) — 2/97 翻转 + §216 2/338 同源; 需先重论证
   lenient 默认的理由是否仍成立, 再做独立 A/B。

### 沉淀

`tmp/m5-ab.json` / `m5-ab-holdout.json` / `m5-ab-b3.json` (12600 局明细)、
`tmp/m5-flip-corpus.json` (97 局 W→L)、`tmp/m5-flip-fx.json` (97 局 forensics)、
`tmp/m5-classic.json` (guardrail 700 局, 0 flips)、探针 (travel-fire-probe.ts) 带 gateFail
归因与 difficulty 参数化。测试: godai-travel-fire.test.ts 10 用例 + godai-candidates.test.ts 10 用例。

### §219 评审 P1 修复轮 (2026-08-17) — 工具可信度 + 4 处 AI 缺陷 + 修正后门禁重跑

#### 1. 实验工具 hash/seed 修正 (P1)

- `tools/lib/stage-spec.ts`: `RunHeaderInfo` 新增 `paramsB?: unknown`; `runHeader` 在
  传入 paramsB 时打印 `paramsA=<hash> paramsB=<hash>` (否则保持 `params=` 单 hash)。
- `tools/diag/ab-param.ts` / `ab-multi-param.ts`: 传 `paramsB: CANDIDATE`, 表格行用
  `seeds.length` (原硬编码 `/60` 会错标非 60-seed 语料)。
- `tools/eval/eval-suite.ts`: `params` 移到 caliber 行之前加载 (原在行后才声明)。
- 实测 caliber: `difficulty=hard stages=35 seeds=60 stageIndex=0 maxTicks=36000
  paramsA=f5cc0288 paramsB=493a5081` (base=f5cc0288, M5 candidate=493a5081)。

#### 2. 门禁重跑 (修正打印后, 运行内容不变 — 打印 bug 仅影响报告文本)

| 难度 | 语料 | baseW | candW | net | flips L→W:W→L | 结论 |
|---|---|---|---|---|---|---|
| hard | 35×60 (1–60) | 1582/2100 | 1594/2100 | **+12** | 66:54 | 与 §218 primary 逐局一致 (determinism 复证) |
| classic | 35×20 | 620/700 | 620/700 | **0** | 0:0 | 结构性 no-op 复确认 |
| chaos | 35×20 (首次) | 494/700 | 502/700 | **+8** | 25:17 | **护栏通过** — 净正, 无崩塌 |

chaos 分关 (净翻转): S21 +4, S2 +3, S6 +2, S0/S5/S10/S12/S15/S16 −1, S12 −2,
其余 ≥0; 最差单关 −2, 无 ≥3 崩塌。artifact: `tmp/m5-rerun-{hard,classic,chaos}.json`
(均含 paramsA/paramsB caliber 行)。

#### 3. 4 处 AI 缺陷修复 (各带回归测试, 旧代码验证失败)

1. **enemyBulletOnRay** (`ActionContract.ts`): 新增同侧门 + 交汇点门 —
   `baseInFront` (玩家与基地同侧) 与 `playerEdge` (交汇点在玩家—基地区间内),
   两道 `sigma*(pos−playerEdge) >= 0` 的 `continue`。repro: 玩家在基地右方朝左、
   敌弹从基地左方横穿 — 原判可拦截 (错), 现 false; 同侧追尾保持 true。
   3 用例, tests/godai-action-contract.test.ts 21 pass; 旧代码 2 repro 失败。
2. **counterfactual-idle** (`tools/diag/counterfactual-idle.ts`): `rngStateEnd` 读点
   移到分支循环之后 (break 路径同样在 break 后读)。
3. **M4 standing second-threat 门** (`ActionCandidates.ts`): standing kill-current
   `killValid` 含 `!killSecondThreat`, standing intercept `interceptValid` 含
   `!kaU.missesSecondThreat`; 4 处选择点 `secondThreatRisk` 由硬编码 false 改为
   派生值。拒绝测试: 第二威胁更紧急时 evaluate 不得 commit killCurrent 且 reason
   含 secondThreat; tests/godai-candidates.test.ts 11 pass; 旧代码下失败。
4. **M5 turn+fire 语义 (记录非修复)**: 引擎同帧先 `p.dir` 再 `tryFire` — 子弹沿新
   方向立即生成; 200ms 冷却只推迟视觉转向 (SimulationCombat 回退 dir + halt)。
   人类同帧输入完全同构 (updatePlayerTank 对输入源不区分) → AI 无额外能力;
   "13t 转弯窗" 是未来移动成本。3 用例: 谓词 active-cooldown 仍提交 / think 真实
   接线 (navigate 分支, 弹 dir='right', p.dir 回退 'down', moving=false) / 人类
   输入字节级同构 (含击杀验证, instant 模型下同帧弹命中)。13 pass。

#### 4. 措辞修正

- §218 verdict: "ship candidate" → "候选通过初步 A/B, 允许 gated rollout" —
  仅在 candidate-on 完整验证 (base_destroyed/最弱关/lives/clear speed) 无恶化后
  才考虑默认打开 (届时升格 ship)。
- §218 chaos "无行为差异可测" 断言删除, 由 §219 真实 35×20 artifact 替换。
- DECISIONS.md:963 行尾空格清除。

#### 后继

按 §218 Implications 顺序: ① defenseIntercept 开火窗口 (反事实 6/14 最大未动类)
→ ② CMA-ES (工具链已可信) → ③ dodge idle 取证 → ④ "太迟"防御结构 → ⑤ t2a 自毁守卫。

### §220 defenseIntercept 开火窗口 — actionContractMode 独立 A/B: 三线微负, 方向否决 (2026-08-17)

#### 1. 动机与候选形态

- §218 Implications ①: 反事实语料 (`tmp/cf-idle-r1.json`) 中 defenseIntercept 分支
  占 6/14 事件 (最大分支类)。6 事件的干预窗口分析:
  - S17s38 / S28s26 (idle_causal): turnFire/intercept 分支 240t 窗口内零基地伤害 vs
    cont 首伤@216/@48 — 干预有效 (2/6)。
  - S19s16: 全分支同 tick 首伤@41 — 干预太晚 (窗口在静止发生前已关闭)。
  - S22s28: 边际 (首伤@74→@87, 终局同)。
  - S24s9 / S34s51 (idle_legitimate): 全分支无伤 — 无需干预。
- 候选 = 现成 `actionContractMode` (M2 设计, 5 个防守站桩点已接线: baseLaneSentry 打砖
  think.ts:697 / defenseIntercept 拦截 :1337 / defenseIntercept 破砖 :1370 /
  midLaneDefense 对弹 :1467 / midLaneDefense hold :1528)。语义: 冷却中已面向的无产出
  站桩必须先有等待价值 (敌弹在射线 / 自己弹在飞 / killSlack>0, `contractStandingHold`),
  否则放弃分支 fall through。**从未独立验证** (M4 整包 A/B 时被一并否决)。

#### 2. 独立 A/B (caliber: paramsA=f5cc0288 paramsB=20907853)

| 难度 | 语料 | baseW | candW | net | flips L→W:W→L | 结论 |
|---|---|---|---|---|---|---|
| hard | 35×60 (1–60) | 1582/2100 | 1575/2100 | **−7** | 120:127 | 微负, 高翻转 |
| chaos | 35×20 | 494/700 | 492/700 | **−2** | 45:47 | 一致微负 |
| classic | 35×20 | 620/700 | 620/700 | **0** | 0:0 | 结构性 no-op |

- artifact: `tmp/cw-ab.json` (hard), chaos 行同批跑出。无单关崩塌 (最差 S23 −5/60),
  但翻转分散于 30/35 关 — 全局行为扰动而非窗口精确修复。

#### 3. W→L 翻转取证 (127 局, 候选参数 forensics)

- 127 局失败族: **base_destroyed 117 (92%) / lives_exhausted 10** — 基线失败族
  base_destroyed 约 73–80%, 显著恶化。放弃站桩让玩家在防守窗口移开, 代价是送基地,
  印证 §218 高危标注 ("改错直接送基地")。

#### 4. 裁决: reject implementation (actionContractMode 维持默认 0)

- 三线一致无正收益; W→L 失败族 92% 基地被毁, 机理负面。
- 与既有证据链吻合: §215 M3 结论 1 (idle_causal 仅 14.3%, 不得以消灭 idle 为主线 —
  本 A/B 是对该结论的独立复证: 高翻转 ≈ 无关噪声, 不产净益) + §216 M4 整包 −116
  (含同语义) + §218 高危标注。
- **不修改代码** (参数已默认 0, byte-identical 保持); 不删除参数 (M2 时代已有测试,
  保留无害, 未来若 defenseIntercept 重做窗口语义可复用判据)。

#### 后继

- §218 Implications 顺序继续: **② CMA-ES (工具链已可信, 候选搜索自动化)** →
  ③ dodge idle 取证 → ④ "太迟"防御结构 → ⑤ t2a 自毁守卫。
- ① 正式关闭 (方向否决)。defenseIntercept 的窗口问题不再以"放弃站桩"形态追;
  若重启, 应改从 "开火窗口" 本义 (冷却恢复前最后 13t 的 killSlack 判定) 入手, 而非
  "放弃站桩" — 后者已被两条独立证据否决 (M4 + 本 §)。

### §221 评审 P2 修复 + M5 candidate-on 完整验证 (2026-08-17)

#### 1. 同格死循环修复 (ActionCandidates.ts)

- `fireRayBlocked` / `firstBrickOnRay`: 玩家与目标中心格完全相同 (瞬时重叠/测试构造)
  时旧循环 `step = tc.row > pc.row ? 1 : -1` 得 −1, 从同格出发向负方向无限递减,
  永不满足 `r !== tc.row` → 死循环。修复: 同格提前返回 (false / 'none' —
  零长度射线无中间格)。
- 回归测试 (tests/godai-candidates.test.ts "same-center-cell"): 同格时 fireRayBlocked
  false / clearLaneFireDir null / evaluate 不挂起。旧代码验证: 移除 guard 后测试挂起
  (死循环实证)。

#### 2. 候选热路径 scratch 化 (§14.1/§14.2)

- 评审指出: evaluateUnifiedCandidates 开启时每 tick 创建 `brickOut`/`kRayOut` 数组并
  调用返回对象的 ThreatBudget 计算 — 违反协议热路径分配约束。
- 改造 (保持 API 兼容, 不传 out 时行为不变):
  - ThreatBudget: `tankCenterCell`/`playerActionEta`/`enemyDeadline`/`killAssessment`/
    `standingKillAssessment` 加可选 caller-owned `out` 参数; `killAssessment` 的
    second-threat 循环前拷贝 `damageDeadline`/`arrivalLB` (循环复用内部 _DL 缓冲会
    覆盖引用); 新增导出接口 `StandingAssessment`。
  - ActionCandidates: 模块级 scratch 常量 (cell ×6 / EnemyDeadline ×2 / KillAssessment ×2 /
    StandingAssessment / ActionEta / brick 数组 ×2), 全部调用点传 scratch; 顶部注释
    更新为"零 per-tick 分配"。
- 行为等价实证: M5 candidate (fireLineDetourMode=1) hard 35×10 重跑 vs §219 artifact
  `tmp/m5-rerun-hard.json` 逐局比对 — **350 局 0 差异**。
- 顺手修 ab-param.ts 记录 bug: `lives` 需 task `telemetry: true` 才回传 (worker 返回
  顶层 `lives`, 非 `finalState.lives`), 原记录恒 0。

#### 3. M5 candidate-on 完整验证 (hard 35×60, 36000 ticks, §218 gated 条款)

| 指标 | baseline | candidate | 变化 | 判定 |
|---|---|---|---|---|
| wins | 1582/2100 | 1594/2100 | +12 | 正向 |
| base_destroyed | 447 | 433 | **−14** | 改善 |
| lives_exhausted | 70 | 72 | +2 | 微劣化 |
| avg lives/win | 2.79 | 2.80 | +0.1% | 中性 |
| avg ticks/win (clear speed) | 5893 | 5888 | −0.1% | 中性 |
| 最弱关 S34 wins | 17 | 18 | +1 | 无崩塌 |
| S34 avg lives | 2.88 | 2.67 | −0.21/局 (18 局小样本) | 关注, 非 M4 式否决 |
| S34 avg ticks | 6561 | 6702 | +2% | 小样本 |

- §218 gated 条款四项 (base_destroyed / 最弱关 / lives / clear speed) 全部无恶化 →
  条款满足。S34 单关 lives 微降属 18 局小样本波动 (幅度为 §216 M4 否决理由的 ~1/13,
  且全关 lives +0.1% 中性)。
- **默认打开与否留待决策**: 验证通过仅解锁"考虑升格 ship"的权限 (§218 条款字面),
  默认打开改变所有玩家体验 — 需 human 拍板; 未拍板前 fireLineDetourMode 默认 0 不变。

#### 4. handoff 文档单一可信版本

- plan/open-test-round1-handoff.md 重写: 顶部过时状态 (1330/1fail) 移除, 当前树状态 /
  已完成 (M0–§221) / 待办 / 接手者必读 (含 §219 继承坑) 为单一来源; 历史轮次压缩保留。

#### 后继

- 待 human 拍板: M5 默认打开 (ship) 或保持 gated。
- §218 Implications 顺序: ② CMA-ES → ③ dodge idle 取证 → ④ "太迟"防御结构 →
  ⑤ t2a 自毁守卫。

#### 5. 评审 P2 收尾 (2026-08-17 二次评审)

- **think.ts M5 回调热路径分配**: isWorthKillNow 闭包内 `tankCenterCell(t)` 每次
  分配 cell 对象 — 改标量计算 (center-floor 语义不变, §14.1)。
- **ab-multi-param.ts lives 记录**: task 缺 `telemetry: true` → lives 恒 0, 与
  ab-param 同病, 已补。
- **等价性脚本修正**: tmp/m5-equiv.ts 原用 `params` 字段 (RunOptions 实际字段为
  `godAIParams`) → base/cand 均跑默认参数, 验证无效; 已修正并用 godAIParams 重跑:
  outcome/ticks/lives 一致。此前 350 局逐局比对走的是 ab-param → SimTask.params
  (正确链路), 不受影响。
- **handoff 漂移修复**: 待办 "M5 candidate-on 完整验证" 已完成 (§221) — 已改列为
  "默认打开决策 (人工开放测试)"; 测试统计更正为 1367 pass / 23 skip / 0 fail (1390 总)。
- 结论不变: M5 保持默认 0, 进入 candidate-on 人工开放测试; 确认体验更好后再拍板默认打开。

## §222 CMA-ES 重启 — 真 35 关口径 (2026-08-17, 收口: 参数面无 ROI 成立)

### 背景

§215 勘误: §214 三批筛选因旧 stage parser 把 `--stages 1-35` 解析成 S1 单关,
"参数面穷尽"结论基于单关过拟合口径。M0.1 已统一 parseStageSpec (tools/lib/
stage-spec.ts) → 重启筛选, 验证结论在真 35 关下是否成立。

### 执行 (DECISIONS §222)

- 筛选: `SIM_POOL_WORKERS=16 optimize-godai --stages 1-35 --difficulty hard
  --seeds 20 --generations 8 --fitness v7` (DIM=19, λ=12, maxTicks=18000, caliber
  params=f5cc0288 打印正确)。
- gen 轨迹: 639.5 / 643.9 / 641.4 / 640.5 / **675.9** / 643.5 / 659.4 / 666.4。
  best = gen 4 (fitness 675.9, win 79.6%, minW 0.40, floor 1600 vs default
  632.7 / 76.9% / 0.30 / 4399)。
- **8 参数移动与 §214 批次 1 完全相同** (defenseRowOffset 3 · defenseColSpread 4 ·
  threatRangeCells 22 · powerupMaxDivertDistance 14 · baseRaceMarginCells 6 ·
  outnumberedEnemyCount 4 · outnumberedRadiusCells 4 · outnumberedFieldEnemies 5):
  两个独立运行 (S1 单关 vs 真 35 关) 收敛同一候选 → 方向真实存在, 非单关过拟合
  幻觉, 也非 parser bug 产物。

### 60-seed CRN 确认 (ab-multi-param, 真 35 关 × 60 seeds × 2 臂, telemetry lives)

- **net +17/2100 (+0.8pp)** — 与 §214 批次 1 确认逐字节一致 (L→W 303 / W→L 286),
  确定性复验通过。
- avg lives/win: 3.54 → 3.47 (−0.07); S34 (最弱关) 17 → 16 (−1)。
- **分关对冲结构 (首次可视化)**: 改善 S12 +10 / S24 +9 / S22 +6 / S30 +6 /
  S3+S5+S18+S27+S29 +5…… (≈ +73); 恶化 S9 −12 / S11 −11 / S26 −6 / S6 −5 /
  S21 −4…… (≈ −56)。防守姿态 (defenseRowOffset 3 + defenseColSpread 4 +
  baseRaceMarginCells 6 + outnumbered 族) 在迷宫/防守压力关改善, 在开阔地形关
  节奏损失对冲。

### 判定 (§9.3.5 停止条件触发)

4 批候选 (§214 ×3 + 本轮 ×1) 全部 ±1-2pp 噪声 → **hard 标量参数面确认无 ROI
(真 35 关口径)**。§214 "参数面穷尽"结论成立, 但机制从"单关过拟合噪声"修正为
"防守参数在 hard 上是零和再分配 (迷宫关 ↔ 开阔关对冲)" — 这解释了防守类参数
永远 ±1pp 的结构性原因。DEFAULT_GOD_AI_PARAMS 不变, 不发货。

### 沉淀

- `.workbuddy/optimization-phase5-r2/` (summary + all-candidates),
  `tmp/phase5-r2-ab60.json` (60-seed 明细), `tmp/cmaes-r2.log`。
- 方向性洞察: 若未来要做防守强化, 需按关型 (迷宫/开阔) 分治, 全局单一阈值
  必然是零和 — 与 M4/§220 否决教训同构。

### 后继

§218 Implications: **③ dodge idle 取证** → ④ "太迟"防御结构 → ⑤ t2a 自毁守卫。

### §223 ③ dodge idle 取证 — 移动闪避死亡族: 干预窗口存在 (2026-08-17)

前置 (§218 后继 ③): 失败结局末 10 tick 21% 在 dodge 分支, 反事实语料零 dodge
事件 — 需确认 dodge 死亡是否有可干预窗口。

#### 取证 1: dodge 不是 idle (0% 静止)

dodge-audit.ts 对 518 失败局 (base_destroyed 447 + lives_exhausted 70 + timeout 1)
终局前 ≤10 tick (5180 ticks) 分支审计:

| 分支 | ticks | 占比 | fire | move | idle(moveDir null) |
|---|---|---|---|---|---|
| navigate | 2780 | 53.7% | 19 | 2754 | 26 |
| **dodge** | 1059 | 20.4% | 9 | **1059** | **0 (0.0%)** |
| t2a | 611 | 11.8% | 21 | 169 | **442 (72%)** |
| midLaneDefense | 307 | 5.9% | 7 | 256 | 51 |
| defenseIntercept | 273 | 5.3% | 2 | 31 | 242 |

- **dodge 永远是移动闪避, 从不静止** — §215 idle 证伪对 dodge 分支完全成立且更强。
  "dodge idle" 字面不存在, 无 idle 可修。
- 顺带发现: **t2a 分支末段 72% idle** (站桩近距对枪) — 另一个可疑面, 记录留待。
- 玩家死亡 tick 分支 (315 死亡局): navigate 44.1% / **dodge 30.8% (97 局)** / t2a 10.2%。
  97 局 dodge 死亡中 ~60% 死亡前整 10 tick 都在闪避循环 (闪避中死, 非不动而死)。

#### 取证 2: 反事实 — 86% 死亡局存在可干预窗口

counterfactual-dodge.ts (沉淀于 tools/diag/, 参照 counterfactual-idle 的
cloneWorld@T + ScriptedInput 分支): 对 315 死亡局在死亡 tick T−60 克隆世界,
4 分支跑 60 ticks: factual (应复现死亡) / turn-and-fire (站定反击) /
advance (推进击杀) / hard-away (远离弹群质心)。

| 分支 | 全 315 局 | dodge 死亡 97 局 |
|---|---|---|
| factual 复现死亡 | 315/315 (100%) | 97/97 (100%) — determinism ✓ |
| turn-and-fire 存活 | 204/315 (64.8%) | 61/97 (62.9%) |
| advance 存活 | 218/315 (69.2%) | 65/97 (67.0%) |
| **hard-away 存活** | **242/315 (76.8%)** | **73/97 (75.3%)** |
| 任一分支存活 | 272/315 (86.3%) | **83/97 (85.6%)** |
| 全死 (不可救) | 43/315 (13.7%) | 14/97 (14.4%) |

#### 结论 (③ 收口)

1. **dodge idle 证伪终审**: 0% 静止, 与 §215 M3 (idle_causal 14.3%) 一致 — idle
   修复对 dodge 无意义。
2. **dodge 死亡可干预**: 85.6% 在死亡前 60 ticks 有分支存活 → 候选空间存在,
   方向 = 闪避路径/闪避+反击, 不是 idle 修复。
3. **最强方向信号: hard-away (弹群质心远离) 75.3% vs 当前单弹闪避 (factual)
   0%** — dodgeDirectionImpl 的"对最近单弹闪避"与"远离弹群质心"有 ~12pp 存活差。
   注意 M3/M9/M10/M12 的 hard+/chaos− 签名警告: dodge 家族增强须 hard 主口径 +
   chaos 护栏, 防重蹈 S26 确定性回归。
4. 不可救 14.4%: 贴脸/包围死亡, 死亡前 60 ticks 任何本地改道均无效 — 归入
   travel 段更早决策问题 (④ "太迟"防御结构分析的对象)。

#### 沉淀

- `tools/diag/dodge-audit.ts` (末窗口分支审计) + `tools/diag/counterfactual-dodge.ts`
  (死亡窗口反事实) + `tmp/dodge-cf.json` (315 局明细) + `tmp/dodge-audit.ts` 输出。

### §224 候选 A: dodgeCentroidMode — 质心远离闪避 (2026-08-17, 否决: 约束下 0 行为差异)

§223 反事实 hard-away 分支 75.3% 存活 → 设计候选 A: 多弹 (≥2, 96px 内) 时
dodge 方向改为"远离弹群质心" (dodgeCentroidMode, 默认 0 OFF)。实现: 默认路径
内新块, 四方向候选 (排除 threat 轴向, §83) + canMoveDir + isSafeDir + 基地门
(新格不得比当前格更远离基地, slack=0); 单弹/无弹 byte-identical。测试 6 用例
(tests/dodge-centroid.test.ts, 含 base gate 与 unsafe-lane 拒绝) 全过。

#### 证据

- hard 35×60 (ab-param, telemetry lives): **net 0, L→W 0 / W→L 0 — 零翻转**。
- 触发率 (diag 计数器 _centroidChecks/_centroidTriggers/_centroidEscapes):
  60 局 12 关 × 5 seeds: dodge-ticks 13891, centroid-checks 13891 (每次 dodge),
  triggers 1226 (8.83%), escapes 63/76 (82.9% 触发时有候选)。
- **方向差异 0.00%** (S14/S21/S24/S31/S34 25 局 8645 dodge ticks 逐 tick 对比
  legacy vs centroid 的 _moveDir)。

#### 机制解剖 (为何 0 差异)

1. 弹群质心几乎总在玩家**上方** (敌人从上方射击) → away = 下方 = 朝基地 →
   基地门通过, 且与 legacy 的 base-closer 选择天然一致。
2. 弹从下方来 (质心在下) → away = 上方 = 远离基地 → slack=0 基地门拒绝 →
   fall through legacy。
3. 结论: **基地门 (防 M9/S10s6 式逃逸) 恰好把质心远离的所有差异化能力掐死**。
   无门版 = 持续逃离基地 = M9 horizon 模式的既否决方向 (chaos −3.5pp, 生存↑
   但基地/击杀效率↓)。hard 失败模式里 dodge 死 = "基地侧弹群无法逃" (逃=丢
   基地, 不逃=死); §223 反事实 75% 存活是 60-tick 短窗口伪影 — 真实长局中
   逃离方向必丢基地。

#### 裁决

net 0 + 0 翻转 + 0 方向差异 → **无信号, 不 ship**。dodgeCentroidMode 保持默认 0
(实验旋钮, 与 M9/M10/M12 同族 — dodge 增强族在 hard 上第四度确认无发布级杠杆:
M3 对枪 / M9 horizon / M10 门控 / 本次质心远离)。计数器 (diag 只读) 保留。

#### 沉淀

- `tests/dodge-centroid.test.ts` (6 用例), `tmp/centroid-probe/diff/diff2/diff3.ts`,
  `tmp/dc-ab60.json` (2100 局明细)。

### §225 后继 ④: "太迟"防御结构审计 (2026-08-17, 收口 — 3 因子 + 机制空白)

§218 遗留: 91% 失败 = 基地被拆; 67% 败局 travel 段无火力机会; baseLaneSentry
缺位未分析。新工具 `tools/diag/toolate-audit.ts` (从 forensics 语料分层抽样
40 局 base_destroyed, threatLedger 重放, 提取首伤 tick / 窗口 / 玩家行为画像;
产物 `tmp/toolate-audit.json`)。

#### 数据 (40 局, 分层覆盖快/中/慢毁)

- 首伤 tick: 中位 3146 (min 726, max 5927) — 防线前 ~52s 完整, 首伤≈崩溃信号。
- **窗口 (首伤→毁): 中位仅 271 ticks (4.5s)** — 敌人 6 发 (120 HP) 拆完。
- 玩家死亡后才被拆: 17.5% (7/40)。
- **absent (>8 cells >50% 窗口): 0/40; stationary 无输出: 0/40** — 推翻"人不在"
  假设: 玩家始终在动、在回防。
- 窗口分支: navigate 59.4% / t2a 22.0% / powerup 6.7% / dodge 5.9% /
  defenseIntercept 3.7% / **baseLaneSentry 1.0%** / midLaneDefense 0.9%。
- **sentry 仅 37.5% 局触发, 中位 2 ticks** — 防线崩溃窗口内哨兵几乎全程缺位;
  62.5% 局窗口内 0 tick (玩家 navigate + powerup + t2a)。

#### 机制解剖 (3 因子)

1. **窗口结构性短**: 弹速 4px/tick × 3-8 cells 短程 → 拦截窗口窄;"人到弹道
   延长线"是稀缺事件, 来不及。
2. **sentry 机制空白 (主发现)**: sentry 路径只有两条 — ① 已对齐+中线畅通+
   非冷却 → 持位开火 (冷却 800ms 期间让位, navigate 接管 → 玩家漂出 lane →
   哨兵永久失效, 中位 2 ticks 实证); ② 站台导航只服务**带外**敌人 (row
   20-22)。**ring 已破、敌人在带内 (row ≥ 23)、玩家在 lane 外 → 无任何哨兵
   路径** → return false → navigate 盲跑。这正是"太迟": 机制本可指引玩家进
   lane 击杀, 却让位给无方向感的 navigate。
3. **危局期行为未切换**: 基地掉血后 powerup 仍占 6.7% (214 ticks) — 拾取
   候选 (800) 在 sentry (850) 之下, 但 sentry 不触发 → 拾取继续; t2a 22%
   表明玩家在追逐/缠斗而非回防。

#### 候选方向 (供拍板)

- A. **sentry 带内应急进 lane 导航** (补空白): ringBreached + 带内敌人 + 玩家
  lane 外 → 最短路径导航到对齐站位 (非站台语义, 服务带内) — 直接攻击 2 号
  因子, 但改动最重 (需防与站台/§198 门控冲突)。
- B. **危局拾取抑制**: ringBreached (或 baseThreatNow) 时 pickupHigh 让位 —
  最轻, 有先例 (sentry 850 > pickupHigh 800 的本意), 但只砍 6.7% tick。
- C. **sentry 冷却期保位**: 冷却中不 claim 但方向保持 lane 对齐 — 与 §6.1
  行动有效性契约 (站桩无产出先过契约) 冲突, 有先例阻力。

> 推荐 A (机制空白补全, 直接对应 37.5% 触发率与 1% 窗口占比), B 可作为 A 的
> 附加; C 不做 (契约冲突)。

### §226 后继 ④ 候选 A/B 双否决 (2026-08-17, 收口 — hard 防守微调杠杆耗尽)

§225 三因子 → 两个候选，均 hard 35×60 主口径 A/B 否决：

#### A baseLaneSentryInBandNav（带内应急进 lane，补机制空白）

- v1（colGap 1-3）：**net −35**（127 L→W / 162 W→L），分散无集中模式（S12 −7、
  S3/S5/S6 −4 等；S14 0 翻转、S23 全胜 0 翻转）。
- v2（colGap 2-3，§198 seed25 先例去掉 colGap=1）：**net −53**（115/168）——更差；
  colGap=1 部分净 +18，colGap 2-3 净 −71。
- 机制：带内横移劫持玩家的代价 > 收益——横移期间不射击（_fire=false）+ 打乱
  站位；§225 的"62.5% 局 sentry 0 tick"不是缺进 lane 路径，而是"对齐+非冷却"
  开火瞬间条件苛刻，横移不增加该瞬间。§198 门控先例（站台多次补丁防劫持）
  再次验证。

#### B baseAlertPickupSuppress（危局拾取抑制：MID/LOW 让位, HIGH 豁免）

- **net −42**（145 L→W / 187 W→L）。
- 机制：star/tank 是**永久 DPS 升级**（M6/M11：每 star ≈ +9pp），危局期抑制
  拾取破坏 star 经济 → 输出降 → 败局增；且"不拾取"≠"去防守"（sentry 不触发
  时玩家依旧 navigate）——抑制只产生空洞，不产生防守。

#### 裁决

双候选否决，参数保持默认 0（测试沉淀 9 用例 tests/base-alert.test.ts，覆盖
默认 OFF / B 抑制与 HIGH 豁免 / A 横移与距离限与 colGap=1 不劫持）。

**④ 收口结论**：hard 的"太迟"失败面是**结构性**的（弹速 4px/tick × 短窗口 271
ticks 中位 × 环破后敌人贴脸连发），防守微调（sentry 进 lane / 拾取抑制）在
60-seed 口径下全部净负。与 dodge 增强族（M3/M9/M10/§224 质心远离）同理：
**hard 上行为微调杠杆已耗尽**。候选 C（冷却保位）与 §6.1 行动契约冲突不做。
剩余可探索：⑤ t2a 自毁守卫重论证（§218 最后一项，新嫌疑面：t2a 末段 72%
idle）；或开放测试 M5 fireLineDetourMode。

#### 沉淀

- 产物: tmp/inband-ab60.json / tmp/inband-ab60b.json / tmp/suppress-ab60.json
  （各 2100 局明细）。

### §227 后继 ⑤ t2a 自毁守卫重论证 (2026-08-17, 收口 — 伪嫌疑)

§223 遗留: t2a 分支末段 72% idle（442/611）疑为自毁守卫（玩家在枪口前站桩）。
新工具 `tools/diag/t2a-audit.ts`（40 局 base_destroyed 分层抽样 + threatLedger
末 300 ticks 解剖; 产物 tmp/t2a-audit.json）。

#### 数据

- t2a commits (末 300 ticks): 284, idle 43.3%（全生命周期基线 42.2% — **idle
  率非末段特有**）。
- **idle 100% onCooldown** — 站桩 = 800ms 冷却等待, 无异常行为。
- **94.3% idle 时基地威胁 imminent（nearestThreatEta ≤ 20 ticks）**; 死亡前
  60 ticks 内 t2a idle: 17/40 局。
- **imminent idle 时玩家平均距基地 11.5 cells（184px）, >3 cells 占 94.8%**
  （tmp/t2a-audit2.ts）。

#### 机制解读（3 层证据 → 伪嫌疑）

1. **idle 构成 = 冷却**（100% onCooldown）: 800ms 冷却 vs 1 tick 开火 → 冷却
   占 t2a 生命周期 ~98%; 43% idle 是"t2a 段内冷却占比", 非站桩缺陷。
2. **94% imminent = 基地已在倒下过程**: nearestThreatEta 是敌人"再开一发"的
   落地下限; 玩家 11.5 cells 外 + 弹速 4px/tick → 20 ticks 内物理无法回防
   （拦截需要玩家在弹道上）— 结构性, §226 已收口。
3. **dodge 未接棒是正常的**: idle 时无"玩家威胁弹"（弹打基地不打玩家）→
   dodge（1000）无需触发; 站桩不阻止任何躲避。

#### 结论

t2a idle 是冷却形态的正常表现, 不是独立病因。"中场缠斗 → 基地被掏"是
§225/§226 已证的窗口结构问题（t2a 22% 窗口占比是结果不是原因）。唯一可微调
面（skipT2aForDefense 阈值 26 cells 收紧）是 §159 已探索失败的方向（1 cell
超限 → oscillation 零火力）, 且 §226 泛化证据（防守微调 60-seed 全净负）否定
同类杠杆。**⑤ 收口, 不产候选**。

#### 沉淀

- tools/diag/t2a-audit.ts（复用 §225 抽样框架）+ tmp/t2a-audit.json;
  tmp/t2a-audit2.ts（距离取证）。

## §229 M5 fireLineDetourMode SHIPPED — 默认 1; S30/S13 弱关重标定 (2026-08-17)

### 决策与门禁

- `fireLineDetourMode: 1`（DEFAULT, 全难度; classic 无覆盖继承）。用户拍板
  "默认开启 + commit"; gate 拦截后用户拍板 "Ship + 重标定"（DECISIONS §229）。
- **gate truth 重标定**: hard S30 0.9046→0.8388, chaos S13 0.9138→0.8439
  （`tests/score-gate-core.ts`, floor 随 truth 平移; 注释含成因与探针矩阵）。
- 新增 `fireLineDetourMinSlack: 13` 二级参数（= DETOUR_TURN_WINDOW_TICKS 语义,
  探针无上调证据, 保持 §217 原值）。

### S30 退化取证（20-seed score-gate 口径, hard, telemetry on）

| 配置 | S30 mean score | 全局 35×60 |
|---|---|---|
| M5=0（基线, 当前代码复测 0.9075 ≈ truth 0.9046） | 0.9075 | — |
| M5=1 带内原版（§217） | **0.8388** | **+12 wins**（3 批） |
| slack 18 / 22 / 26 | 0.8407 / 0.8426 / 0.8426 | 未测（+0.004 微效） |
| + maxDist 2 / 3 / 4 cells | 0.8388 逐位相同 | 无效 |
| + 不回头判据（opposite） | 0.8407 | 无效（S30 无反向 detour） |
| csb/cbr 收窄（去掉纯带内游走） | **0.8716**（过 floor） | **0 wins**（94% 机会是带内） |

- 维度拆解（s30-m5-diff）: clearSpeed 0.722→0.798（−0.077 最大项）, baseIntegrity
  0.673→0.733（−0.061）, lives 0.767→0.800, accuracy 0.895→0.907; baseSafety
  +0.003（微升）。**60-seed 0 翻转**（ab-param: 51/60 双方同）— 胜率不变,
  纯赢局质量降。
- 机制结论: detour 打断迷宫 navigate 计划 — 12t 站定 + 重导航（偏移后回走廊）
  ≈ 24-36t 代价, 换 ~10t 击杀提前; 近距（≤2 cells, maxDist 探针）目标击杀也
  无增量（kills 187→184）— 结构性, 判据空间穷尽。
- csb/cbr 收窄教训: §217 探针"机会 94% 带内游走" → 收窄到真威胁 = 关掉 M5
  （全局 0 wins）; 带内超集是 M5 收益的载体, 也是迷宫关退化的载体 — 单参数
  无法分离。

### 验收

- `bun run check`: **1408 tests 0 fail**（含 godai-score-gate 三个难度全过）。
- `bun run build`: 通过（oxlint + tsc + vite build）。
- 无效探针全部回滚（travelFireDetourDir 回到 §217 原样: minSlack 参数化,
  maxDist/不回头/csb-cbr 均移除）; `fireLineDetourMinSlack` 保留为调参入口。
- `tests/godai-params-override.test.ts` 三处断言更新为默认 1（§228 钩子语义
  不变, 恢复默认 = 1 而非 0）。

## §239 全关策略 all-on 实验 — M0/M1 收口：all-on 灾难性否决 + LOO 定位 firingLaneMode (2026-08-17)

> 实验计划：GOD-AI-all-strategies-CMA-ES.md（§217 之后 M5=1 基线上的「全开关闭策略」
> 协同假设检验）。结论：**all-on 灾难性否决（−33.0pp wins），M1 门禁拦截，未进入大规模
> CMA-ES（M2/M3 不执行）**；LOO 定位冲突策略 firingLaneMode（主导）+
> dodgeHorizonScore / candidateMode / dodgeCounterFire（次级），全部单独 A/B 亦净负。

### 工具交付（本次新增）

- `src/ai/god/all-on-experiment.ts`：`ALL_ON_EXPERIMENT_PARAMS`（default + manifest 21 项
  开关翻转，依赖参数最小激活：survivalRiskWeight=1 纯门控、hpDangerHits=2 按 §111 口径、
  hpDangerCommitMargin 保持 0 如实记录 inert；离散钉值 defenseInterceptPredictCells=2 /
  suicideReturnMode=1）+ `ALL_ON_M5_OFF_CONTROL_PARAMS`（all-on−M5）。
- `tools/optimize/optimize-godai.ts --profile default|all-on|all-on-m5-off`：`vectorToParams`
  base 替换为 profile；SEARCH_SPACE init 同源。**live probe**：worker pool 跑 1 局断言
  `paramsHash` 等于 profile 哈希，失败即 exit 1。
- `SimResult.paramsHash` + `SimTaskResult.paramsHash`（FNV-1a，tools/lib/stage-spec.ts）—
  probe 身份标签，全部 worker 路径透传。
- `tools/diag/run-profile.ts`：M0 artifact 跑批（官方口径 stageIndex=0 / maxTicks=36000 /
  telemetry on，逐关 W/base_destroyed/lives_exhausted/timeout + --json 逐 run artifact）。
- `tools/diag/loo-allon.ts`：21 开关 leave-one-out 筛选（--seeds 1-10）+ `--only` 60-seed 确认。
- `tools/diag/ab-param.ts --params <json>`：全 profile paired A/B（baseline 保持 DEFAULT）。

### M0 三 artifact（hard 35×60，官方口径，params hash 6768f1f0 = all-on）

| profile | W/2100 | win% | base_destroyed | lives_exhausted | timeout | avgKills |
|---|---|---|---|---|---|---|
| current shipped default | 1594 | 75.9% | 433 | 72 | 1 | 17.9 |
| all-on（未优化） | 901 | 42.9% | 1186 | 11 | 2 | 13.7 |
| all-on − M5（control） | 904 | 43.0% | 1181 | 13 | 2 | 13.7 |

- 10-seed 冒烟同向（267 / 149 / 146），60-seed 复现稳定（±0.1pp）。
- **M5 在 all-on 语境贡献 ≈ 0**（901 vs 904）：§229 的 +12 wins 不在此语境复现——无关
  紧要，因为 all-on 整体已否决。
- 失败形态翻转：lives_exhausted 72→11（玩家很少耗命），base_destroyed 433→1186
  （+753）——all-on 策略把玩家拖离基地/消耗击杀效率（avgKills 17.9→13.7），基地失守。

### M1 门禁：all-on 灾难性负向 → 不进入 CMA-ES，转 LOO

- **−693 wins / −33.0pp**，base_destroyed +753。计划 §4 M1 门：「若 all-on 灾难性负向，
  不进入大规模 CMA-ES，先做 leave-one-out 找冲突策略」→ 触发。

### LOO 筛选（hard 35×10，21 开关 × 350 = 7700 runs，1 次 pool batch）

| 关闭开关 | Δwins vs all-on | 说明 |
|---|---|---|
| **firingLaneMode** | **+68** | 主导冲突（base_destroyed 198→131） |
| candidateMode | +15 | 次级 |
| dodgeHorizonScore | +13 | 次级 |
| dodgeCounterFire | +11 | 次级 |
| baseGuardAnchorMode | +6 | 轻微 |
| baseDamageRecall / baseAlertPickupSuppress / actionContractMode / baseLaneSentryInBandNav | +1..+4 | 轻微 |
| defenseInterceptPredictCells / DigBricks / dodgeCentroidMode / dodgeClearanceScore / playerHpAwareness / survivalModeLives / suicideReturnMode | 0 | 该 10-seed 语料内 inert（playerHpAwareness/survival 符合 manifest 预期：依赖参数 0 时接近 no-op） |
| coverageMode | −2 | 噪声 |
| pathThreatAvoidance / targetValueMode / intentMode / pathTargetMode | −5..−8 | 在 all-on 语境反而净正（±8 = ±2.3pp 噪声带内，无后继） |

### LOO 60-seed 确认（top 4，hard 35×60）

| 关闭开关 | W/2100 | Δ vs all-on | base_destroyed |
|---|---|---|---|
| all-on（ref） | 901 | — | 1186 |
| **off:firingLaneMode** | **1317** | **+416（+19.8pp）** | 770（−416） |
| off:dodgeHorizonScore | 974 | +73 | 1066 |
| off:candidateMode | 938 | +37 | 1151 |
| off:dodgeCounterFire | 921 | +20 | 1165 |

### 单独 A/B（default + 单开关，hard 35×10）— 冲突均内在净负，非协同伪影

| 开关 | candW/350 vs 267 | net | W→L / L→W |
|---|---|---|---|
| firingLaneMode=1 | 162 | **−105（−30.0pp）** | 132 / 27 |
| candidateMode=1 | 243 | −24（−6.9pp） | 57 / 33 |
| dodgeHorizonScore=1 | 248 | −19（−5.4pp） | 64 / 45 |

### 结论与沉淀

- **协同复活假设证伪**：独立否决的策略组合开启后不协同，反而叠加灾难（§204–211 的
  coverageMode 四轮净负在本实验 all-on 中继续净负，LOO −2 噪声；dominant 冲突是此前
  从未全语料 A/B 过的 firingLaneMode）。
- **firingLaneMode 全语料首次测量 = 灾难**（单独 −30pp，W→L 132 vs L→W 27）：§139 死区
  修复仅在 Battlement 局部测过，全语料口径下是当前最强的负面杠杆——归档为证伪方向。
- dodge 增强族（centroid/counterFire/horizon/clearance）延续 §224 结论：hard 无杠杆。
- **DEFAULT_GOD_AI_PARAMS 不变**，无任何开关升格；`--profile all-on` 仅作实验入口保留，
  一键回退 = 不传 profile。M2/M3（CMA-ES / holdout / 消融）因 M1 门禁不执行。
- 四开关（targetValueMode/intentMode/pathThreatAvoidance/pathTargetMode）在 all-on 语境
  的 LOO 净正信号在 10-seed 噪声带内，不作后继依据。

### 验收

- `bun run check`：tsc + 1408 tests 0 fail（含全部 godai 门禁）；oxlint 0。
- 确定性：LOO 10-seed 与 60-seed 每 seed 增量一致（firingLaneMode +0.194 vs +0.198
  wins/run）；profile hash 6768f1f0 全批一致（live probe 断言）。
- Artifacts：`tmp/m0-default.json` / `tmp/m0-allon.json` / `tmp/m0-allon-m5off.json` /
  `tmp/loo-s10.json` / `tmp/loo-confirm-60.json`。

## §240 all-on−firingLaneMode CMA-ES 两轮收口 (2026-08-17)

> 用户指令（§239 后）：「把头号元凶关掉，其它所有策略全参与，跑一跑 CMA-ES，看看运气怎么样。」
> 执行：新增 `ALL_ON_MINUS_FLM_PARAMS` profile（all-on − firingLaneMode），两轮独立 CMA-ES。

### 配置（两轮一致，除 opt-seed / warm-start）

- difficulty=hard · 35 stages × 20 seeds（搜索集 1..20）· maxTicks=36000 · telemetry on
- 19 维数值搜索空间，IPOP-CMA-ES：12 pop × 12 gens，σ=1.0，floor=0.6
- 15 workers；live probe hash f5ac3ad7 全批一致（params 真实到达 Simulation）
- 第 1 轮：opt-seed 1，从 profile 基线起搜 → best fitness 532.5
- 第 2 轮：opt-seed 2，warm-start 从第 1 轮 best（sigma=1）→ best fitness 548.1

### 结果（hard 35×60 官方口径确认）

| 版本 | wins @ seeds 1-60 | 胜率 | 对比 all-on−FLM 基线 |
|---|---|---|---|
| all-on−FLM 基线（未调参） | 1317/2100 | 62.7% | — |
| 第 1 轮 best | 1363/2100 | 64.9% | +46 wins (+2.2pp) |
| 第 2 轮 best | 1376/2100 | 65.5% | +59 wins (+2.8pp) |
| 第 2 轮 best **holdout 61-120** | 1313/2100 | 62.5% | **≈ 基线（过拟合归零）** |
| shipped default（对照） | 1594/2100 | 75.9% | —（差距 −10.4pp） |

- 第 2 轮 best 的失败形态：base_destroyed 704、lives_exhausted 18、timeout 2、
  avgTicks 5319、avgKills 16.7 —— base_destroyed 仍 1.6× default（433）。
- 第 2 轮 best 的搜索集（20 seeds）胜率 0.70（gen 1 起即触顶），但 60-seed 回落 0.655、
  holdout 0.625 —— 调参 gains 是搜索集特异的。

### 关键移动（第 2 轮 best vs all-on−FLM 基线，19 维中 11 维移动）

- threatRangeCells 16（基线 23）、baseRaceRangeCells 18→16、baseRaceMarginCells 5→4、
  defenseInterceptRangeCells 15→18、baseLaneSentryRange 6→5、aimError 0.03、
  reactionDelay 3、defenseRowOffset 1、defenseColSpread 3、outnumbered 4/7、t8Max 7…
- 方向解读：威胁感知半径收窄 + 基地护圈略收 —— 数值上把「激进防守」调回中庸，但
  无法抵消 dodgeHorizonScore/candidateMode/dodgeCounterFire 的结构性负收益。

### 停止判定（计划 §6）

两轮独立 opt-seed 的 best（64.9% / 65.5%）均未超过 shipped default（75.9%），
且 holdout 显示过拟合 —— 停止条件触发，**不再跑第 3 轮**。「开启被否策略」方向
正式双否决收口（§239 开关门禁 + §240 数值调参）。DEFAULT_GOD_AI_PARAMS 未动。

### 验收

- `bun run check`：tsc + 全量测试绿；oxlint 0。
- 确定性：两轮搜索种子固定可复现；60-seed / holdout 每 run 均带 paramsHash f5ac3ad7。
- Artifacts：`tmp/cma-flm-smoke-best.json` / `tmp/cma-flm-smoke-best-60.json` /
  `tmp/cma-flm-search2-best.json` / `tmp/cma-flm-search2-best-60.json` /
  `tmp/cma-flm-search2-best-holdout.json` / `tmp/cma-flm-search2/optimization-summary.json`。
---

# 补录：§194 与 §230–§234（2026-08-26 M4.0 落点盘点补齐，DECISIONS 压缩前落点）

> M4.0 机械盘点发现六条无 progress 落点的 DECISIONS 条目，压缩前在此补录要点。

## §194 像素卡死 directMove 兜底（2026-08-13，默认 0 关闭）

- 根因：`replanInterval=1` 下 A* 每 tick 重规划，敌方移动使缓存失效 → 首步方向 left↔right
  振荡 × turn cooldown(50ms) = 「来回走净位移零」卡死（S35@seed10 卡 30.6s 等 17 处告警）。
- 机制：HUNT 分支绕过 A* 改 directMove 选向（优先垂直），阈值参数 `pixelStuckDirectMoveTicks`
  （480=8s，高于 navBreakStuck escape 3s、低于 10s 告警线）。
- 结案：paired A/B（hard 20 seeds CRN）suite 0.5308→0.5363（Δ+0.0053 p=0.0185，B 更优）
  ——**净负回退默认 0**；300 值曾回归 chaos S5/S8。代码路径保留门控待重调。
- 门禁当时全绿（hard 0.742 / chaos 0.716 / classic 0.875）；剩余 14 个告警根因不同
  （snap 精度/密封口袋/dead-end）。

## §230 门禁 runner 瘦身（SHIPPED）

- `runSimulation` 新增 opt-in `collectMetrics:false` / `collectEvents:false`（默认 true 行为不变），
  score-gate 与 pass-rate gate 关闭两者——scorer 只读 outcome/ticks/finalState/firstKillTick/telemetry。
- telemetry power-up census 每 tick `new Set()`（§14.1 反模式）→ 双缓冲 ping-pong（liveIdSetA/B）。
- 读-only 采样跳过不消耗 RNG、不触碰 World：60 sims score-sum 开/关完全相同，classic truth 字节不变。
- 背景：门禁 CPU-bound（2100 sims ≈ 全套 85%），runner 开销直接放大。

## §231 thinkInterval 决策链节流（否决，旋钮保留默认 1）

- thinkInterval=2（off-tick 保持上次输出）：hard 35×60 paired A/B win 75.6%→72.8%（**−2.8pp**
  ≈2SE 真实回归，691/2100 outcome 翻转）。
- 机制：1-tick 决策延迟仍打穿 dodge/火力窗口；且 off-tick 跳过 godRng aim roll → RNG 流移位
  级联改决策（与 §68 同型）。classic 不测不开（instant 1-HP 纪律）。
- 节流方向收束：naive −2.8pp；条件节流理论收益 ~5-6% sim CPU，不值得（Three Gates）。

## §232 决策链小数组分配消除 + scanAhead 整数步进（SHIPPED，字节等价）

- 三处 per-call 小数组改局部变量：Navigator.directMoveImpl `dirs[]`、BASE_LANE_SENTRY `cands[]`、
  HUNT navStuck 回退 `pref[]`；scanAhead 改整数 cell 步进；`world.allies` 提出循环。
- 选择顺序/比较次序/AABB 像素语义逐位不变；45 sims 签名 IDENTICAL + godai-score-gate 通过。
- 全套 41.4s→37.5s（连同 §230/§231 runner 改动）。拒绝 getDefaultDefensePositionImpl 的 def 对象
  （共享 buffer 别名风险 > 收益）。

## §233 bun test 吞吐墙 + 门禁种子 20→10（完成）

- 测量结论：Ryzen 5800H 上该负载有效并行度仅 ~2.5×（内存带宽/功耗墙，非结构问题）；
  gate 1050 sims 实测 ~20s 已达机器地板 ~88%。全套 54.5s→~25.9s，<20s 未达成（用户接受）。
- score gate 种子 **20→10**（用户拍板保统计功效），truth 重标定（seeds 1-10），
  margin ~2-SE 加宽：MARGIN_SCORE 0.05→0.07、AGG_MARGIN_SCORE 0.03→0.04。

## §234 test-silent HEAVY_TESTS 修复 + 强制 --parallel（SHIPPED）

- 清洁树 `bun run test` 曾 12 fail 根因：runner 缺 `--parallel` 致跨文件模块态泄漏
  （order-dependent）+ HEAVY_TESTS 指向已 skip 的旧 gate。既有缺陷，非 §230-233 引入。
- 修复：HEAVY_TESTS 过期项 `god-ai-gate`→`godai-score-gate`；spawnCapture 强制
  `--parallel --timeout=50000`。修复后清洁树 7.3s 0 fail、check ~26s 全绿。

---

# 重启协议（Resume Protocol，2026-08-26 · v1 冻结）

> 未来任何 agent 若要重开 God AI 调优（= 宣告新纪元），按序执行：

1. **坟场核查**：`plan/God-AI-Organization.md` §1.1 封盘清单 + `plan/refactor.trae.md` §0.5
   勿重提清单——先证明新想法不在已证伪方向里；
2. **口径装载**：本档 §0.A 冻结基线 + §0.C 评估框架 + §I.5 方法论（per-seed tick-diff 基石）；
3. **开关面盘点**：`params.interface.ts` ARCHIVED_KNOB_GROUPS × `think.ts` CANDIDATE_SURVIVAL
   （哪些旋钮存在但没在跑）；un-archive 走四步闸门（改常量/断言 → 新 DECISIONS 条目 →
   更新 golden → 重跑 60-seed 基线）；
4. **纪律三条回归线不变**：不泄漏 SP / 不冻结失败种子当硬门槛 / byte-identical 确定性；
   决定性结论 ≥60 seeds；子集重跑用 `run-forensics --from-json`（AGENTS §4 Step 7）。

新纪元「三件套」（缺一不可）：**新 DECISIONS 条目 → 重跑 60-seed 三难度基线 → 更新
冻结签名 golden**。行为改动会让 `bun run freeze:check` 变红——这是预期闸门而非故障。
