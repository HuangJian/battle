# GOD AI Hard 开放测试协议

> 状态：开放测试计划，供其它 agent 独立实现和取证。
>
> 目标：把最近一轮审查发现的问题转化为可复现、可判定、可回滚的测试流程，寻找 hard 难度的结构性突破。
>
> 约束：`turnCooldownMs=200ms` 是公平性基础规则，任何测试和实现都不得降低它。未来测试 500ms 时，只能观察 AI 是否正确适应更长的合法转向等待。

## 0. 先读什么

执行 agent 必须先读：

- `MANIFEST.md`：简单、经典、公平、确定性是最终约束；
- `DECISIONS.md`：尤其是 God AI 调优索引和 stageIndex 口径；
- `plan/God-AI-Hard-Breakthrough-Implementation.md`：上一轮方案及其已实现部分；
- `docs/god-ai-tuning.progress.md`：§202–§214 的基线、失败分类、A/B 和 CMA-ES 记录；
- `AGENTS.md`：测试、提交、热路径和工作树纪律。

本文是上一轮方案的“开放测试修订协议”，不是要求立即发货的行为改动方案。

## 1. 审查结论与测试目标

最近提交的总体评价：

1. `ThreatBudget`、`ActionContract`、`CoveragePlanner`、intent 和诊断工具的模块边界符合原计划。
2. 所有新机制默认关闭，因此当前没有新的线上行为；“没有新突破”不能被解释为这些机制已被充分验证。
3. Phase 5 CMA-ES 记录存在实验口径错误：优化器的 stage 参数解析器只支持逗号列表，而记录使用了 `--stages 1-35`。这会把搜索范围解析为 S1。
4. 当前 dormant 代码仍有 ETA 重复计费、射线误判、deadline/slack 混用、热路径分配和 Coverage 触发窗口过窄等问题。因此现有负向 A/B 只能证明“当前实现方式”不理想，不能证明整个结构性方向无效。

本协议的目标不是追求任意总胜率上涨，而是回答四个问题：

- `base_destroyed` 之前，玩家是否仍有一个可行且公平的行动？
- 当前失败究竟是检测晚、选靶错、赶路慢、转向锁定、射击无效，还是多威胁不可覆盖？
- 哪个最小行动改变能在不破坏原有路线的情况下改变失败结果？
- 该改变是否能在 35 关、独立 seeds 和最弱关上稳定复现？

## 2. 不得违反的判定规则

### 2.1 公平规则

- 不修改 `World.rules.turnCooldownMs` 的游戏规则值。
- AI 只能读取转向冷却，不能绕过合法转向时间，也不能通过额外输入在冷却期强行转向。
- ETA 模型必须同时通过 200ms 和 500ms 测试；500ms 只会使合法转向 ETA 增大。
- 不在 Simulation 路径加入 `Math.random()`，不消费额外 `world.rng`。

### 2.2 架构规则

- 诊断和 planner 只读 World；只有 Simulation 修改 World。
- 新的 intent、lease、缓存必须位于 World 可恢复的 gameplay state，或明确属于不影响玩法的诊断状态；不得增加模块级隐藏 gameplay state。
- planner 热路径不允许每 tick 为每个敌人创建数组、对象或执行全场排序。
- 不使用 stage ID 特判、固定关卡坐标表或只对 S34 生效的行为分支。

### 2.3 评估规则

首要指标：

1. hard 35 关总体胜率；
2. `base_destroyed` 率和首次基地受伤 tick；
3. 最弱关胜率；
4. 失败家族转化率，特别是 `base_destroyed → stage_clear`。

护栏指标：

- classic 不得出现显著回归；
- `lives_exhausted` 不得明显增加；
- self-inflicted base kill 不得增加；
- 不得引入长期静止、目标来回切换、基地已受威胁却无输出的新模式；
- 同一 `(difficulty, stage, seed, params)` 必须 deterministic。

“idle alert 减少”“局部 branch count 变好”“单关多赢几局”都不能单独作为通过理由。

## 3. M0：先修复实验口径，不改行为

### 3.1 修复 stage 参数解析器

修复 `tools/optimize/optimize-godai.ts` 的 stage parser，使以下输入含义一致：

```text
--stages all       35 stages
--stages 1-35      35 stages
--stages 1,3,7     S1、S3、S7
--stage 34        S34
```

解析器必须拒绝空范围、越界、反向范围和非法 token，而不是静默变成 S1。

必须增加单元测试，至少覆盖：

- `1-35` 得到 35 个 0-based index；
- `1,3,7` 得到 `[0,2,6]`；
- `all` 得到所有 stage；
- 非法输入失败并给出明确错误；
- parser 不改变 seed、params 或 RNG。

在修复前，禁止引用旧文档中的“35-stage CMA-ES 搜索结果”。

### 3.2 锁定官方 stageIndex 口径

所有用于 hard 行为比较的任务必须使用：

```ts
stage: STAGES[stageIndex]
stageIndex: 0
```

stage 编号用于选择地图；`stageIndex=0` 用于保持评估、掉落和 RNG 口径与现有 gate 一致。工具输出必须同时打印：

```text
difficulty / stage count / seed count / stageIndex / maxTicks / params hash
```

如果实验有意测试真实 stage index，必须另开实验，不得与官方 baseline 混写。

### 3.3 建立可复用 baseline

先运行并保存 JSON；不要只保存终端摘要：

```bash
bun tools/diag/threat-ledger.ts \
  --seeds 60 --difficulty hard \
  --json tmp/open-test-threat-baseline.json

bun tools/diag/run-forensics.ts \
  --seeds 60 --difficulty hard \
  --json tmp/open-test-forensics-baseline.json

bun tools/eval/eval-suite.ts \
  --seeds 60 --difficulty hard --dims \
  --json tmp/open-test-eval-baseline.json
```

baseline 记录必须包含：总胜率、逐关胜率、`base_destroyed`、`lives_exhausted`、base HP 变化、玩家生命、live enemies、失败 tick 和失败分类。

若只是修改诊断器或分类器，使用旧 JSON 的失败 subset 重跑，不重新生成完整 corpus：

```bash
bun tools/diag/run-forensics.ts \
  --from-json tmp/open-test-forensics-baseline.json \
  --kinds base_destroyed,lives_exhausted \
  --json tmp/open-test-forensics-subset.json
```

M0 通过条件：parser 测试通过，baseline 可重跑，默认参数下行为结果和原始 baseline 一致。

## 4. M1：修正 dormant threat 模型

这一阶段只修正纯函数和测试，默认行为仍为 OFF。禁止一边修模型一边接入行为，避免无法区分模型错误和策略效果。

### 4.1 ThreatBudget 的 ETA 必须互不重复

`playerActionEta()` 的各项必须是互斥成本：

```text
total = movement + legalTurnWait + aimAlignment + fireCooldown + shotCadenceAndFlight
```

具体要求：

- `nextLegalTurnEta` 只能计入一次；
- 路径中的一次垂直换轴成本不能与当前瞄准转向成本重复计入；
- `targetValue` 不得再次把 `reach` 加到已经包含 reach 的 `eta.total` 上；
- 每个返回字段写清楚“包含”和“不包含”的成本。

增加以下单测：

- 当前方向正确时，增加 turn cooldown 不应改变瞄准等待；
- 当前方向错误时，转向等待只增加一次；
- 200ms → 500ms 时所有需要转向的 ETA 严格增加；
- 玩家更远、需要更多射击、需要转向时 kill slack 不增加；
- 同一 World 两次计算 byte-identical 且不改变 RNG state。

### 4.2 敌人 deadline 必须明确是下界还是安全上界

现有几何 Manhattan 距离可以作为相对排序或最早到达下界，但不能同时被描述为“保守 deadline”并用于允许玩家远离基地。

实现 agent 必须为每个字段注明语义：

- `enemyArrivalLowerBound`：敌人最快可能到达；
- `enemyDamageEarliest`：最早可能造成基地伤害；
- `enemyDamageDeadline`：用于允许行动时的安全截止时间。

如果字段只是几何下界，主动偏离基地必须额外有 safety margin；不能因为一个乐观 Manhattan ETA 得出正 slack。

增加单调性测试：

- 敌人更近时威胁不得变轻；
- 环砖减少时威胁不得变轻；
- base HP 降低时威胁不得变轻；
- 整条射线有额外阻挡时，模型不能错误地提前宣称已能射基地；
- cbr 分支不得重复计入破环成本和基地伤害窗口。

### 4.3 修正坐标协议

ThreatBudget 使用中心 cell 还是左上角 corner cell，必须在类型或函数名中明确。Coverage 使用 corner space 时不得直接拿中心 cell 比较。

至少增加：

- 32px tank 位于 cell 边界、半格、边界前后 1px 时的转换测试；
- `playerCell()`、`tankCell()`、Coverage candidate 的坐标交叉测试；
- 不得因中点附近 ±1px 抖动改变同一物理 footprint 的 lane 判断。

## 5. M2：修正 ActionContract 与 intent

### 5.1 ActionContract 的敌弹判断必须是真正的拦截段

`enemyBulletOnRay()` 只有在以下条件同时成立时才能返回 true：

- 敌弹与玩家当前射线横向/纵向重叠；
- 敌弹方向朝基地；
- 敌弹位于玩家与基地之间；
- 按当前速度和转向规则，玩家保持该射线确实有机会拦截；
- 敌弹没有已经越过基地或位于玩家背后。

增加反例测试：

- 同列但已经越过基地的敌弹必须 false；
- 同列但在玩家背后的敌弹必须 false；
- 同列且位于玩家与基地之间、方向正确的敌弹才为 true；
- 横向场景对称覆盖。

### 5.2 三类防守分支必须统一接入

`actionContractMode` 的行为门控必须覆盖：

```text
defenseIntercept
midLaneDefense
baseLaneSentry
```

允许站桩的条件只能是：

- 当前站位确实拦截基地方向敌弹；
- 当前站位的合法开火/击杀 ETA 在威胁期限内；
- 自己的子弹正在解决当前威胁；
- 保持位置本身改善下一次有效输出。

不能把“敌人存在”当成站桩理由。也不能简单地把所有冷却期分支 fall-through；必须区分“无效站桩”和“正在等待合法开火/弹道解决”的有效等待。

### 5.3 intent 必须保存真正的 slack

不要保存敌人 deadline 后再把它命名为 `minSlack`。建议保存：

```ts
committedSlack = enemyDamageDeadline - committedPlayerKillEta
```

intent 每 6–15 ticks 或威胁事件发生时重新验证。以下任一条件释放 intent：

- 目标死亡、不可达或路线地形改变；
- 当前 slack 低于释放阈值；
- 连续窗口没有移动、射击、击杀或覆盖改善；
- 新威胁的 slack 明显更差；
- 合法转向等待使原行动已经赶不上期限。

intent 不是延长 `huntCommitTicks`，也不能阻止更高优先级的基地拦截。

## 6. M3：因果反事实开放测试

这是本协议的主要突破方向。目标是确定“疑似 idle/no-output 是否真的造成失败”，而不是继续减少诊断告警数量。

### 6.1 触发点

在诊断模式下记录以下事件：

- 防守或 hunt 分支提交；
- `_moveDir=null` 且 `_fire=false`；
- 当前是否 on cooldown；
- 当前目标、基地威胁、最近敌人和各自 ETA/slack；
- 下一次移动、开火、击杀、基地受伤的 tick。

只对满足以下条件的事件做反事实：

- 发生在失败局；
- 距离基地首次受伤或 base_destroyed 不超过固定窗口；
- 同一连续静止段只取第一次事件，避免重复统计。

### 6.2 反事实动作集

从该 tick 的完整 World/RNG 状态复制出最多四个分支：

1. `continue`：保持原输入；
2. `turn-and-fire`：朝当前有效威胁合法转向并开火；
3. `move-to-intercept`：朝威胁拦截点移动；
4. `clear-or-advance`：若射线被砖阻挡，执行合法破墙/前进动作。

每个分支运行固定短窗口，例如 60、120、240 ticks，记录：

- base HP 是否下降；
- 目标是否死亡；
- 是否产生有效移动/开火；
- 玩家是否死亡；
- 新的 RNG state 和世界状态是否可重复。

反事实只能用于 tools/diag，不得进入正常 Simulation 行为。优先复用已有 snapshot/WorldSerializer 能力，确保复制包含 RNG、子弹、地形、敌人和 cooldown。

### 6.3 反事实结论

每个静止段归入：

- `idle_causal`：至少一个替代动作在窗口内避免基地伤害，而 continue 失败；
- `idle_legitimate`：continue 与替代动作结果相同，或保持射线是最佳动作；
- `travel_or_turn_causal`：替代动作也赶不上，根因是更早的选靶/路线/转向；
- `unresolved`：窗口不足或状态无法可靠复制。

M3 通过条件：至少 10 个代表性失败逐 tick 人工抽查，自动分类与事实一致率 ≥80%；同时统计 `idle_causal` 占比。若 `idle_causal` 很低，不得继续以消灭 idle alert 为主线。

## 7. M4：统一行动候选的最小原型

只有 M1–M3 通过后，才允许接入行为。不要继续堆叠独立分支参数。

### 7.1 固定候选

每 tick 或低频重规划只比较有限候选：

```text
kill-current
intercept-base
clear-lane
return-defense
```

每个候选至少提供：

```text
firstOutputTick
playerKillEta
enemyDamageEarliest
killSlack
interceptSlack
secondThreatRisk
```

### 7.2 选择规则

候选只有在以下条件成立时才可取代当前行动：

- `slack > 0`，并且包含 safety margin；
- `firstOutputTick` 有限且不是无意义站桩；
- 不让第二威胁在完成当前行动前进入不可逆窗口；
- 当前候选的预期基地伤害不高于继续当前行动；
- 如果只是移动到覆盖点，覆盖点必须通过真实攻击/拦截路径获得收益，而不是只更换 target cell。

### 7.3 Coverage 的实现约束

- 已持有 lease 时先走廉价有效性检查，再决定是否重算 threat；
- 不在每 tick 创建 threat 数组、candidate 对象或执行全量排序；
- 缓存失效条件必须包括敌人移动、敌人死亡、地形改变、基地环改变和玩家转向锁定；
- Coverage 不能只在 `!baseUnderThreat` 时运行。若基地已经受威胁，必须先经过紧急拦截分支，再允许 Coverage 评估是否能同时改善多个威胁；
- 多威胁护栏必须把到达覆盖点的移动时间算入，而不是只比较到达后的射击时间；
- default 仍为 OFF，先做定向 A/B。

## 8. M5：开放测试矩阵

### 8.1 阶段性测试规模

| 阶段 | corpus | 目的 | 是否可作结论 |
|---|---|---|---|
| smoke | S8、S20、S24、S34 × 5 seeds | 检查崩溃、坐标、分支接线 | 只能发现硬错误 |
| diagnostic | hard 35×60 | 失败分类、反事实、逐关影响 | 可判断失败机制 |
| paired A/B | hard 35×60，同 seed | 候选行为效果 | 可作主筛选结论 |
| holdout | hard 35×60，冻结另一 seed 集 | 防止在训练 seeds 上过拟合 | 可作最终行为结论 |
| guardrail | classic/chaos 35×20 起步，必要时 60 | 检查大回归 | 不用于主优化 |

所有 A/B 必须保存 JSON，输出每关：baseW、candW、L→W、W→L、net、`base_destroyed` 差异和最差翻转 seed。

### 8.2 建议命令

候选行为可用现有工具：

```bash
bun tools/diag/ab-param.ts \
  --param <key=value> \
  --difficulty hard --stages all --seeds 1-60 \
  --json tmp/open-test-ab.json

bun tools/diag/ab-multi-param.ts \
  --params <key=value,key=value> \
  --difficulty hard --stages all --seeds 1-60 \
  --json tmp/open-test-ab-multi.json

bun tools/eval/eval-suite.ts \
  --seeds 60 --difficulty hard --dims \
  --json tmp/open-test-candidate.json
```

注意：`ab-multi-param.ts` 当前支持 `all` 和逗号 stage 列表，但不自动支持 `1-35`；在 parser 统一前使用 `--stages all`，不要混用不同工具的 stage 语法。

### 8.3 结果报告格式

每轮开放测试必须报告：

```text
commit:
baseline/candidate:
difficulty:
stages:
seeds:
stageIndex:
maxTicks:
turnCooldownMs:

total W/L/net:
base_destroyed:
lives_exhausted:
weakest stages:
best/worst flips:
failure-family conversion:
determinism check:
verdict:
```

结论只能是：`ship candidate`、`keep experimental`、`reject implementation` 或 `insufficient evidence`。禁止把“筛选 fitness 上升”直接写成“行为改善”。

## 9. 何时重新运行 CMA-ES

在以下条件全部满足前，不运行完整 CMA-ES：

- stage parser 已修复并有测试；
- ThreatBudget 的重复计费和 deadline 语义已修复；
- ActionContract 的射线反例和 midLane 接线已通过测试；
- 至少一个失败家族被反事实证明确实存在可干预窗口；
- 一个小型结构候选在 hard 35×60 paired A/B 中有可重复的正向信号；
- default 行为仍可 byte-identical 回滚。

满足后，CMA-ES 只搜索当前确实使用的少量结构参数，例如：

- slack safety margin；
- intent lease 和无产出释放 ticks；
- coverage 最小收益；
- 多威胁 opportunity cost；
- 安全道具最大绕行成本。

必须排除：

- `turnCooldownMs`；
- aimError、suboptimalPathProb 等 game-feel 参数；
- 已关闭或已证伪的 dodge/station/retreat 参数；
- stage-specific 参数。

CMA-ES 搜索协议：

1. 20 seeds 只做方向筛选；
2. hard 35×60 CRN 配对确认；
3. 另一组冻结 seeds 做 holdout；
4. 最弱关、`base_destroyed` 和失败家族必须同时检查；
5. 连续 3–5 批只有 ±1–2pp 且 holdout 无信号时停止，不继续扩大搜索。

## 10. Definition of Done

开放测试阶段完成必须满足：

- [ ] stage parser 支持并测试 `all`、range、comma list、单 stage；
- [ ] 官方 stageIndex=0 口径在所有报告中明确打印；
- [ ] baseline、诊断 corpus 和 A/B JSON 可复现；
- [ ] threat ledger 的主要失败分类经过代表性逐 tick 人工核验；
- [ ] ThreatBudget 不重复计费，200/500ms 单调性和 deterministic 测试通过；
- [ ] ActionContract 射线判断有反例测试，并覆盖三个防守分支；
- [ ] intent 保存真正 slack，Coverage 不在 lease fast path 全量分配；
- [ ] 反事实测试能区分 `idle_causal` 与 `idle_legitimate`；
- [ ] 至少一个候选行为在 hard 35×60 paired A/B 有明确正向信号，或有充分证据否决该方向；
- [ ] classic、self-kill、最弱关和 deterministic 护栏均通过；
- [ ] `bun run check` 和 `bun run build` 通过；
- [ ] 未修改 200ms 公平规则；
- [ ] 未通过上述门禁前，不发货新行为，不宣称参数面已经穷尽。

## 11. 交付与回滚纪律

- M0–M3 的诊断和纯模型改动可以独立提交；每个提交说明是否改变行为。
- 行为候选必须有独立 feature flag，默认值保持当前 DEFAULT。
- 每轮实验细节写入 `docs/god-ai-tuning.progress.md`，基础决策写入 `DECISIONS.md` 索引。
- 全量 sweep 前先保存 JSON；诊断器修复只重跑失败 subset。
- 不启动 dev server；使用 headless simulation、`bun run check`、`bun run build`。
- 若候选在任一硬门禁失败，恢复上一个通过门禁的默认参数，但保留诊断工具和否证证据。
