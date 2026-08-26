# Dual 中路无钢关配合策略 — 交接 Spec / Agent Prompt

> 本文件是一份可直接交给实现 agent 的自包含 prompt。核心目标：为"中路无钢铁、敌人从中路顶部出生持续凿穿砖墙"的关卡（典型：S34 Battlement，hard，dual 模式）设计并实现一套 **dual 专用配合策略**，使 God AI 在 dual 模式下轻松过关，且对单玩家 / 其他关卡**零回归**。

---

你是一个 Battle City Web 仓库的编码 agent。任务：为"中路无钢铁、敌人从中路顶部出生持续凿穿砖墙"的关卡（典型：S34 Battlement，hard，dual 模式）设计并实现一套 **dual 专用配合策略**，使 God AI 在 dual 模式下轻松过关，且对单玩家 / 其他关卡零回归。

## 0. 执行前必读（读全，不读就动手算失职）
- `AGENTS.md`：操作契约。重点 §2.1 One Author（只有 Simulation 改 World；God AI 是 input 层，只读 World）、§2.2 无隐藏状态、§2.3 确定性（随机走 world.rng，Sim 内禁止 Math.random）、§2.4 数据优于代码（旋钮放 config/params.ts，不要硬编码行为）、§2.7 三道门（§13）。
- `MANIFEST.md`：信条，§13 三道门是最终仲裁。
- `DECISIONS.md`：已有决策，扩展而非矛盾。本次涉及 §59/§74/§87/§127/§146/§155/§157/§159/§169/§173 与 D2。
- 复盘依据 replay：`hard-s34-base-l3-t20-seed1929185026.replay`
  （已确认 dual 模式；~20s base loss，仅 3 杀；全场 steel=0；敌出生 col 0/6/12，col 12 为基地正上方中柱；col 12 从 row10→22 是纯砖通道直插基地环；P1 出生 col8 / P2 col16）
- **实测缺陷 replay**：`hard-s34-base-l3-t27-seed251482356.replay`（S34 hard dual，seed 251482356，1616 ticks）。前一轮实现后复现此局，确认 **P2 的"侧翼+拾取"角色根本没真正实现**（整局 `nav=(0,0)` 占 0%）。三个用户报告的问题全部属实，详见文末 §6。

## 1. 根因（为什么输）
1. 防御是"环倒后才反应"型：§59/§157 要等敌人已对齐鹰才升优先。本该在"敌人正凿完整环砖"就压制的 D2（`defenseBreachBonus`）默认 0 → 关着，凿墙者被当普通敌人按距离/兵种打分，AI 还在猎杀侧翼，20s 只杀 3 个。
2. 默认防守位 = (12,23) 正好在中柱 breach 车道上。dual 下 P1/P2 都往这挤，触发 §159 双玩家 yield：一人让另一人，两人都在中心低效打转，没人真正卡住 col12 凿墙者。
3. 无稳定守位锚点（`baseGuardAnchorMode=0`）+ 无粘性/受创召回 → 防守级联在中途被拾取/猎杀分支抢走。

## 2. 要实现的设计

> ⚠️ **关键约束（务必遵守）**：以下所有增强**仅对 dual 模式生效**。单玩家（非 `spectateDual`）必须保持现状——旋钮保持默认 0、检测器不激活、角色不分。任何在单玩家下开启这些增强的改动都会导致胜率倒退，视为**失败**。激活条件必须写成 `world.spectateDual === true && centralBreachRisk === true`；单玩家下即使 `centralBreachRisk` 为真也必须短路返回（不生效）。

A. **中路无钢检测器**（新增，放 ai/config 或 god/ 下，config 驱动）：扫初始 tileGrid，若中央带 cols 11–13、rows 0–22 的 steel 数 = 0 且敌出生点含中列（col 12±1）→ `centralBreachRisk = true`。仅作 dual 模式下的触发条件，**不参与单玩家逻辑**。
B. **dual 角色分工**（核心配合策略，仅当 `spectateDual && centralBreachRisk`）：
   - P1（player 索引 0）= **中路守口**：持有稳定守位锚点（启用 §155，如 (12,22) 前厅口），专职拦截/点掉 col12 中柱凿墙者与清空射击基地的敌人。
   - P2（player2）= **侧翼+拾取+基地防守**（⚠️ 见 §6 实测缺陷：前一轮此处**未真正实现**，P2 整局零目标）。必须每 tick 有明确导航目标：
     (a) **拾取优先**：dual 无钢关卡把 `fence`/`shovel` 设为**最高优先**（=给基地砌钢铁墙，结构性解决被凿穿，见 §6.1-②）；
     (b) **双坦回防**：当 `baseUnderThreat`（§157）或任意象限（含右下）有敌逼近基地时，**P2 也要回防/拦截**，不只有 P1（见 §6.1-③）；
     (c) 覆盖 col0/col6 出生点，不抢 (12,23) 中心位（避免 §159 yield 内耗）。
   - 分工确定性、对称无关：以 player 索引区分，不依赖实时谁更闲，防止振荡。
   - ⚠️ **前一轮致命遗漏**：P2 整局 `nav=(0,0)` 占比 = 0%，即没有任何导航目标，只是默认向上飘（drift）。本项必须真正落地（给 P2 每 tick 派发目标），否则 Battlement dual 不会达标。
C. **开启被关掉的增强旋钮**（默认值从 0 调开，A/B 定档，初始建议：defenseBreachBonus≈400、baseGuardAnchorMode=1、threatStickyTicks≈30、baseDamageRecall=1）。**这些旋钮的"生效"必须被 `spectateDual && centralBreachRisk` 门控**——单玩家下它们保持 0/未激活，行为逐字节不变。
D. **§159 适配**：T2a 防守覆盖要尊重角色——P1 已占中心锚点时，P2 不得再瞄准 (12,23) 附近，确保不双占中心。
E. 所有改动必须 config 驱动（params.ts 加旋钮/默认 0 的 opt-in），不动 Simulation 内的游戏判定，不改字节（默认关 = 与现状逐字节一致）。

## 3. 改动落点（先 grep 定位最新行号）
- `src/ai/god/params.ts`：新增/打开 defenseBreachBonus、baseGuardAnchorMode、threatStickyTicks、baseDamageRecall、centralBreachRisk 检测阈值。
- `src/ai/god/SmartThreatModel.ts`（敌正在凿完整环砖的谓语，约 89–136）+ `src/ai/god/StrategyPlanner.ts`（D2 评分 1606–1614、S7 回防/栅栏 540–580、§155 守位锚点 977/1428、拦截候选 1489–1585）。
- `src/ai/GodAIInput.ts`（§157 isBaseUnderThreat 1020–1035、§169/§173 1035–1065、hasLivingPartner 角色感知）。
- `src/ai/god/think.ts`（§159 T2a 覆盖 2186–2228，需读 partner 状态以区分角色）。
- 注意 dual 下 god1(sim.input)/god2(sim.input2) 是独立 GodAIInput 实例；角色分工通过检测器 + player 索引共享同一 centralBreachRisk 判定实现，必要时经 perception 互相读取 partner 位置（已有 hasLivingPartner / P2 自感知）。

## 4. 验收（Definition of Done）
- `bun run typecheck` 干净；`bun test` 全绿。
- 单机关：`bun tools/optimize/level-sim.ts --stage 34 --difficulty hard --dual --size 20` 应稳定通关（≥95%，目标 100%）。
- **replay 断言（来自 §6，必须全部满足）**：复现 `hard-s34-base-l3-t27-seed251482356.replay`，断言：① P2 `nav≠(0,0)` 占比 **>50%**（修复前 =0%）；② t≈1080 P2 转向去捡 (col23,row1) 的 fence；③ t≈1137 P2 转向拦截右下 (col20,row21) 的 Carrier / 回防基地；④ P1 开局 60 ticks 内有向中心锚点的有效 move 且推进中 `fire=true` 破砖（修复前开局 1s `move=null` 且不破墙）。
- **单玩家回归（重点，不可违反）**：用同一组 seed 跑单玩家 sweep，Battlement 及其他关行为必须**逐字节不变**——即这些增强在单玩家下完全未激活。若发现单玩家胜率有任何变动（哪怕上升，也说明行为被改变，需排查误激活），立即回滚。
- **单玩家基线（权威值，pre-change）**：classic ≈91.5% / hard ≈74.4% / chaos ≈71.5%（跑 `sweep-winrate.ts` **不带 --dual**）。单玩家 post-change 必须与此**逐字节一致**（同 seed 同 outcome）；任何偏差 = 增强被误激活，立即回滚。
- **三难度回归红线（dual 模式，post-harness-fix / pre-feature 基线）**：classic 99.0% / hard 96.7% / chaos 95.7%（跑 `sweep-winrate.ts --dual`）。注意这是 **dual 基线，不是单玩家**！Dual post-change 不应跌破；Battlement 单关 dual 目标 ≥95%（pre-feature 仅 5%）。
- 在 `DECISIONS.md` 追加条目记录本次决策（编号接续，注明 opt-in 默认值、激活条件严格为 `spectateDual && centralBreachRisk`）。

## 5. 约束与坑
- 严守 One Author：God AI 只产生 input/瞄准意图，绝不改 World。
- 确定性：A/B 用同 seed、同 maxTicks；报告用 §74/§127 同机对比。
- **严禁在单玩家 / coop 路径激活 centralBreachRisk 或角色分工**。激活条件必须写成 `world.spectateDual === true && centralBreachRisk === true`；单玩家下即使 centralBreachRisk 为真也必须短路返回（不生效），否则必然胜率倒退。
- 不要顺手重构无关代码；改动最小且可回滚。
- 不要 git commit / push，除非我后续明确说"提交"。
- 完成后用中文给我一份 ≤15 行摘要：改了哪些旋钮/函数、单玩家是否确认零变动、Battlement dual 通关率、三难度回归数字、以及任何未决风险。

---

## 6. 实测复盘缺陷（来自新 replay，必须修复）

另一份 replay `hard-s34-base-l3-t27-seed251482356.replay`（S34 hard dual，seed 251482356，确定性复现，1616 ticks）暴露前一轮实现的真实缺陷。三个用户报告的问题**全部属实**，且指向同一个共同根因：**P2 的"侧翼+拾取"角色根本没实现**。

**复现方法**：同 seed 跑 `runSimulation`，逐 tick 采样 P1/P2 的决策分支（`_lastBranch`）、导航目标、`_moveDir`/`_navTarget`、敌人/道具位置。

### 6.1 三个确认的问题（附数据）
1. **① 开局 P1 不迅速破墙到中路** —— P1 出生（col8, 底行）后前 ~60 ticks（≈1s）`move=null` 完全不动；到 t=240（4s）才到 (col10,row21)，未到中心锚点（~col12,row22+）；且全程 `fire=false`，**不破墙**，只是绕行；中途还在 col10↔col12 游移，中心守位不稳。
2. **② P2 不捡附近 fence** —— 本 seed **确实有 fence 出现**：原始（pre-fix）replay 约 t=1080 在 P2 附近生成 fence，P2 `nav=(0,0)` 毫无拾取意图，仅 `move=up` 向上飘、永远没碰；post-fix 复现下首个 fence 在 **t=1185 生成于 (col22,row0)**、第二个在 **t=1360 (col12,row0)**。无钢关卡 fence=给基地砌钢铁墙，是**单关最高价值道具**，捡了直接结构性解决"中路被凿穿"。⚠️ 注意：§176 实现后复核发现"该 seed 从未出现 fence"的说法是**误判**（源于 `world.powerups` 字段名笔误，正名为 `world.powerUps`）；fence 实际出现且 §6.3-A 的拾取逻辑已生效（见 §6.5）。
3. **③ 0:19 P2 不回防右下基地威胁** —— t≈1137 起，一个 `power` Carrier 在 (col20,row21→22)（**右下象限**）持续向基地逼近；同期 P2 在 (col20,row3 顶部)，`branch=navigate`，**完全不回防**，与敌同列却各在一头。

### 6.2 共同根因（决定性数据）
全 1616 ticks 中，**P2 的 navigate target ≠ (0,0) 的比例 = 0%**（P1 也仅 ~10%）。
→ P2 整局**没有任何导航目标**，只是默认向上飘（drift）。所以：
- 问题②不是"漏捡 fence"，而是 **P2 压根没有拾取行为**；
- 问题③不是"回防慢"，而是 **P2 没有任何基地防守行为**（§157 基地威胁回防似乎只驱动 P1）；
- 问题① P1 中心守位只是"半实现"——中段防守偶尔触发，但启动发呆 1s、不破墙、游移不稳。
**一句话：dual 角色分工里 P1 有了中心倾向，P2 等于零目标。**

### 6.3 必须修复（实现要点）
A. **真正落地 P2 角色（核心）**：当前 `getDefaultDefensePositionImpl` / `selectTargetImpl` 给了 P1 中心偏置，但 **P2 的"shift=-2 侧翼"没有配套目标分配**。需让 P2 每 tick 有明确目标：
   - (a) **拾取优先**：P2 的 pickup-seek 候选；dual 无钢关卡把 `fence`/`shovel` 设为**最高优先**（=给基地砌钢铁墙）。
   - (b) **双坦基地防守**：当 `baseUnderThreat`（§157）或某象限（含右下）有敌逼近基地时，**P2 也要回防/拦截**，不只有 P1。
B. **P2 定位为"自由坦克"**：优先抢最高价值道具（fence），空闲时覆盖 col0/col6 出生点与右下威胁，而非默认向上飘。
C. **修 P1 启动发呆 + 破墙**：首 playing tick 立即下达"向中心锚点推进"指令（消除 1s `move=null`）；开启"推进时开火破砖"（dig-while-moving），满足破墙到达中路。
D. **复用 §159/§157 回防判定到双坦**：当前回防似只动 P1，需让任一空闲/更近的坦在任意象限（含右下）威胁时回防。

### 6.4 验证（除 §4 回归外，须满足这些 replay 断言）
- 复现同 replay，断言 **P2 `nav≠(0,0)` 占比显著 >0**（目标 >50%，修复前 =0%）。
- t≈1185（post-fix 复现首个 fence 生成于 (col22,row0)）：P2 应转向去捡该 fence。
- t≈1137：P2 应转向拦截右下 (col20,row21) 的 Carrier / 回防基地。
- P1 开局 60 ticks 内应有向中心锚点的有效 move，且推进中 `fire=true` 破砖。

### 6.5 §176 修复后复核（2026-08-08，实测）
用 `world.powerUps`（非 `powerups`）逐 tick 复现本 seed，结论：
- **fence 确实出现**：t=1185 (col22,row0)、t=1360 (col12,row0)、另 t=1677 一个 mine。**"该 seed 从未出现 fence" 为假阴性**（字段名笔误导致扫描为空）。
- **§6.3-A 生效**：t=1190 起 P2 `branch=powerup`、`nav=right`，从 (col15,row0) 持续向右逼近 (col22,row0) 的 fence（中途 t=1250 被敌弹打断进入 dodge 但仍向右推进）。即 P2 在 dual central breach 下会主动抢 fence，修复有效。
- **仍存真问题（未达标根因）**：Battlement dual 120-seed 仅 **68.3%**（远低于 §4 的 95% 目标）；P2 `fire=false` 100%——根因是 P2 走 A* `followPath` 绕墙，从不与敌同行/同列，**从不射击、贡献 0 杀**。这是冲 95% 的真正瓶颈，需改 P2 导航（directMove 替代 followPath）或 P2 主动巡逻敌出生点。
- **单玩家回归仅 3-seed 验证（噪声 ±10pp），建议在提交前跑同 seed 逐字节一致 sweep 终验**（详见 §4 单玩家基线要求）。

### 6.6 §177 导航轮实测结论（2026-08-08，纠正 §6.5 的前提）
**重要纠偏**：§6.5 写的"P2 fire=false 100%、从不与敌同行/同列"是**过时前提**。§177 agent 用确定性复现探针（seed 251482356）发现：post-§176 代码里 **P2 已对齐（±1 格 48.8%）、fire 36 发、kills=6**。真正的瓶颈不是"对齐/导航绕墙"，而是 **威胁覆盖重复**——
- base-under-threat 评分只含 敌/基地、与玩家位置无关 → P1/P2 排名完全相同、追同一辆 top 威胁 → 其余 3 敌持续凿环。
- 故 A/B 直接改导航（directMove / spawn-patrol）**全部回退**：base 61.7% → directMove 60.0% / patrol 60.0% / 组合 59.2%。

**§177 实际生效的修复（设为默认 1/2，全部仅 `spectateDual && centralBreachRisk && isPlayer2()` 生效）：**
- `dualCentralBreachP2DefenseSecond=1`：gated P2 取**亚军威胁**，两坦覆盖两条最危险 lane（61.7% → 69.2%，自爆 base 死亡 7→3）。
- `dualCentralBreachP2AnchorSplit=2`：P2 **跳过共享受 §137 锚点**，直扑亚军威胁（=1 改守自身防位更差，60.8%；=2 达 72.5%）。
- 组合（defenseSecond + anchorSplit:2）= **70.0%**（fixed seed，+10pp）；随机 `--size 120` 实测 69.2%–72.5%，高于 §176 基线 68.3%。
- directMove / patrol 保留为 **opt-in 旋钮（默认 0）**，供后续 A/B 使用。

**结论**：P2 缺的是**威胁去重覆盖**，不是对齐/导航。do-conflict（亚军威胁 + 跳过共享锚）是有效杠杆，但 **S34 dual 仍远低于 95% 目标**——4 敌同时凿环超出双坦拦截上限。后续方向（给下一轮 agent）：调 `defenseSecond` 权重、P2 主动破环（在敌人破环前抢先打掉环砖）、anchor 位置优化、或评估"单坦守口 + 单坦主动巡逻 3 个 spawn 列"的分工。

**交付物核对**：DECISIONS.md §177 已追加；新增旋钮 `dualCentralBreachP2DefenseSecond`/`dualCentralBreachP2AnchorSplit`/`dualCentralBreachP2DirectMove`/`dualCentralBreachP2Patrol`(+`PatrolEnemyDist`/`PatrolRow`)；改动落点 think.ts:1558、StrategyPlanner.ts:745/1626/1716/1855、params.ts:2150；`sim-worker.ts` 补 `stageIndex` 传递修复 baseline mismatch。


### 6.7 §176/§177 之后新失败模式：双坦被钉顶部、物理到不了防守位（seed 2 复盘，2026-08-08）
逐帧 autopsy 报告与其复现脚本为本地一次性产物（未入库）；确定性重建与原 replay 结果一致：gameover @t1511、基地被毁、kills=4。
- **三个问题全部确认**：(1) P1 开局折返出生点反复振荡（目标 (15,24) 右翼，非中路）；(2) P2 0:14–0:25 滞留顶部右上、不回防右下；(3) P1 0:20–0:25 滞留顶部、不回防左下。
- **根因（同源）**：dual central-breach 下 P1/P2 锚点在基地砖环两侧（(15,24)/(10,24)），须穿中墙(cols11-13)或绕环；但 carve-dig 逃生被硬卡——中列砖造价 1e9（`PathCarve.ts:87`）+ `carveMaxBaseColumn=1`（`params.ts:2170`）→ 不肯穿中墙；A* 绕顶部周长把两坦钉在 row 0-3 振荡、下不去。`dualCentralBreachP1DigFire` 因 A* 绕墙、`_moveDir` 从不朝墙 → 空操作。§177 去重把目标设对了但物理到不了。
- **与 §6.5/§6.6 不同**：那两轮是“P2 不开火/威胁重复”（目标层）；本轮是“路由/凿墙缺口导致物理卡顶”（可达性层）。
- **方案（全 gated `spectateDual && centralBreachRisk`，单玩家字节一致）**：A. 新增 `dualCentralBreachCarveCentralColumn`，在 override 块内抬高 `carveMaxBaseColumn` 并下调 `carveBaseColumnCost`（默认 1e9→可破值）让 carve 穿中墙；B. 配合 A 让 `_moveDir` 朝墙、`dualCentralBreachP1DigFire` 生效；C. 新增 dual 专用**中路驻守锚点** `(12,12)`（对齐用户“开墙抵达中路驻守点”预期，P1 出生 col8 向上凿穿即到）；D. 解卡后角落再分配（P1 右/P2 左已匹配本轮威胁，§177 去重负责打哪辆）。
- **验收**：P1 ~5s 内到位不卡顶；0:14–0:25 两坦下到受威胁角落；基地存活回到 ≥95%；单玩家不变。
