# GOD AI hard 突破实施方案

> 状态：提案，供后续 agent 按阶段执行。
>
> 目标：在不破坏人类/电脑公平物理规则的前提下，解决 hard 主要失败模式：基地在玩家仍有生命、场上仍有多个敌人时被击毁。
>
> 本文取代旧的 `plan/God-AI-Next-Round.md` 中尚未执行的 SmartThreat Phase A/B/C 计划。旧计划的“类型/路线威胁评分 → 中途截杀”假设已经被后续实验否证；旧文档保留作历史记录，不应直接照单执行。

## 0. 给执行 agent 的结论

当前不要再做一轮全参数 CMA-ES，也不要继续叠加独立的 dodge、回防、驻守、target sticky 参数。

应按以下顺序推进：

1. 先增加“基地威胁期限”诊断，不改变行为。
2. 用诊断确认每种失败究竟是检测晚、选靶错、赶路慢、转向锁定、射击无效，还是多威胁不可覆盖。
3. 实现一个只读、确定性的 threat-slack 计算层，把“下一次合法转向”纳入行动 ETA。
4. 用短时 action intent 和动态攻击覆盖点评估，替代长期静态驻守。
5. 只有在新结构稳定后，对少数当前 active 参数做局部搜索；CMA-ES 只用于收尾校准。

`turnCooldownMs` 是公平性基础规则，绝不能为了 AI 胜率降低。200ms 必须保持；未来提高到 500ms 时，AI 仍必须遵守同一规则。

## 1. 当前基线与成功定义

### 1.1 当前 hard 基线

以下数字来自当前代码 HEAD 的 35 关 × 60 seeds（2100 局）fresh run，不能与旧日志中的历史基线直接混用：

| 指标 | 当前观察 |
|---|---:|
| stage clear | 1582/2100，75.3% |
| 失败中的 `base_destroyed` | 447/518，86.3% |
| 失败中的 `lives_exhausted` | 70/518，13.5% |
| base 被毁时玩家平均剩余生命 | 2.6 |
| base 被毁时仍有 4 个以上敌人 | 73.6% |
| base 被毁前平均击杀数 | 9.7 |
| base 被毁前平均射击数 | 47.1 |

结论：第一目标是减少基地毁灭，不是提高 clearSpeed，也不是继续优化纯闪避。当前 clearSpeed 低主要由 modern cooldown fire model 的物理射速造成；无 AI 行为修改时不应以它作为首要优化目标。

### 1.2 首要、次要和护栏指标

首要指标按以下顺序比较：

1. 35 关总体 hard 胜率和最弱关胜率。
2. `base_destroyed` 率、首次基地受伤 tick、基地被毁时的 live enemy 数。
3. 每个失败家族的转化率：`base_destroyed → clear`，而不是只看总均值。

次要指标：

- 击杀数、每发子弹击杀转化、星级成长；
- 玩家死亡数和剩余生命；
- `defenseIntercept`、`t2a`、`navigate` 等分支的有效产出率；
- 4 敌同时存活时长、玩家到最近有效射击位的 ETA。

护栏：

- classic 保持 byte-identical 或无统计显著回归；
- 不增加 self-inflicted base kill；
- 不提高 `lives_exhausted` 家族超过统计噪声；
- 不引入新的“玩家长期静止”“目标来回切换”“基地受威胁却无输出”模式；
- 决策仍然是 World 的只读函数，不偷看敌方 RNG。

## 2. 明确不再重复的方向

| 方向 | 处置 | 原因 |
|---|---|---|
| 降低 turn cooldown | 禁止 | 破坏人类/电脑公平性；AI 必须适应规则 |
| 全局降低 `t2aMaxRange` 或继续调 aim | 不作为主线 | 只修少数关，可能破坏远程火力 |
| 更长 dodge horizon / escape depth | 关闭 | 局部救活玩家，跨关弃守基地 |
| 更早回防、长期驻守、drill alarm | 关闭 | “statue”效应，侧射和第二威胁失控 |
| target sticky / hunt commit | 不继续扩大 | 会把错误目标坚持更久 |
| 只调 action weights | 不作为主线 | first-commit 链的局部顺序不能表达行动期限 |
| 直接优化 clearSpeed | 延后 | modern fire cooldown 是结构性限制，不是 AI 空转 |
| 按 stage ID 加特判 | 禁止 | 违反数据驱动和泛化目标 |
| 立刻全量 CMA-ES | 延后 | 当前搜索空间和行为结构已不匹配，容易在噪声上过拟合 |

## 3. 目标架构

### 3.1 数据流

```text
World（只读）
  ├─ 当前基地状态、环砖、敌人、玩家、子弹、地形
  ├─ 行动 ETA：合法转向、移动、瞄准、开火、击杀
  └─ threat-slack / coverage / action contract
          ↓
      GodAI 决策
          ↓
   _moveDir / _fire / item input
          ↓
      Simulation 修改 World
```

新增层只能观察 World 并产生输入，不能直接修改坦克、子弹、基地或地形。

### 3.2 建议代码落点

第一阶段可以新增以下纯模块；名称可调整，但职责不要合并回一个巨型 `think.ts`：

| 文件 | 职责 |
|---|---|
| `src/ai/god/ThreatBudget.ts` | 计算基地期限、敌人 ETA、玩家行动 ETA、slack；纯函数 |
| `src/ai/god/CoveragePlanner.ts` | 计算动态攻击覆盖点和多威胁覆盖收益；纯函数 |
| `src/ai/god/ActionContract.ts` | 描述短期 intent 是否仍有效、是否有有效产出 |
| `tools/diag/threat-ledger.ts` | 离线失败归因和 threat ledger 汇总 |
| `tests/godai-threat-budget.test.ts` | ETA、slack、单调性、确定性测试 |
| `tests/godai-action-contract.test.ts` | 冷却期有效等待/无产出提交测试 |
| `tests/godai-coverage-planner.test.ts` | 覆盖点、基地暴露和多威胁测试 |

`GodAIInput.ts` 只增加必要的缓存和诊断计数，并在 `reset()` / `endFrame()` 正确清理；不得把新的持久 gameplay state 放进模块变量。

### 3.3 热路径规则

- 初版诊断允许在 tools 中做较重计算，但 runtime planner 不得每 tick 为每个敌人分配数组或对象。
- 运行时使用已有 scratch buffer、定长候选槽或标量 helper；候选点数量固定上限。
- A*、威胁重算和覆盖点搜索按事件或低频 interval 触发，不在每 tick 全量重算。
- planner 不得调用 `Math.random()`，也不得消费 `world.rng`。
- 任何新缓存必须说明失效条件：敌人移动、地形改变、基地环变化、玩家转向锁定变化。

## 4. Phase 0：建立失败归因基线（不改行为）

### 4.1 诊断字段

在 `tools/sim/simulation-runner.ts` / `tools/diag` 增加可选 threat ledger。默认关闭，关闭时不改变模拟轨迹。

每次采样至少记录：

```ts
type ThreatLedgerSample = {
  tick: number
  baseHp: number
  intactRing: number
  playerCell: { col: number; row: number }
  playerDir: Direction
  playerLives: number
  branch: string
  onCooldown: boolean
  liveEnemies: number
  baseThreatNow: boolean
  nearestThreatEta: number
  playerEtaToBestIntercept: number
  threatSlack: number
  noOpReason: string | null
}
```

每个敌人增加诊断字段：

- kind、HP、cell、direction；
- 当前是否能射基地、是否能破环；
- 到环/基地射击线的保守 ETA；
- 玩家到达有效射击位的 ETA；
- 玩家预计击杀 ETA；
- 该敌人造成的预计基地伤害窗口。

诊断采样不需要每 tick 写完整 JSON。推荐只在以下事件记录：基地 HP 改变、环砖改变、威胁谓词改变、分支改变、玩家死亡、敌人死亡、slack 跨过 0。

### 4.2 失败分类

每局失败最终归入一个主类，并允许附带次类：

1. `late_detection`：基地已进入危险窗口才第一次检测到。
2. `wrong_target`：检测到威胁，但行动目标的 slack 更差。
3. `travel_late`：目标正确，但玩家到达 ETA 大于敌人期限。
4. `turn_locked`：路线正确，但下一次合法转向太晚。
5. `no_output_commit`：分支提交后连续若干 tick 没有移动、射击、击杀或覆盖改善。
6. `multi_threat_overload`：单个行动无法同时压制多个基地威胁。
7. `player_survival`：基地安全，但玩家死亡是主要失败原因。

Phase 0 的完成条件不是“找到修复”，而是每个失败至少能归入一个可解释类别；抽样逐 tick 检查分类与事实一致。

## 5. Phase 1：实现 threat-slack 模型

### 5.1 统一 ETA 定义

所有行动都必须把合法转向纳入成本：

```text
actionEta =
  nextLegalTurnEta
  + movementEta
  + aimAlignmentEta
  + fireCooldownEta
  + requiredShotsEta
```

`turnCooldownMs` 从 `World.rules` 读取，不能由 God AI 改写。200ms 和未来的 500ms 都应该只改变 ETA，不改变公平规则。

### 5.2 敌人期限

初版使用保守、可解释的下界和上界，不模拟敌方 RNG：

- `enemyToRingEta`: 敌人到基地保护环的几何/通路 ETA；
- `enemyToShootEta`: 进入能射击基地的条件所需 ETA；
- `enemyDamageWindow`: 从首次可射击到基地可能被击毁的时间窗口；
- `enemyUrgency`: 由基地 HP、环完整度和预计射击次数共同决定。

不要把 A* 最短路线当成敌人的实际未来路线。它只能作为保守几何下界或相对排序特征。

### 5.3 玩家期限

对每个候选敌人或候选覆盖点计算：

- 玩家到达 ETA；
- 到达后第一次合法开火 ETA；
- 击杀所需射击数和总击杀 ETA；
- 到达期间基地暴露风险；
- 是否会错过第二威胁。

定义：

```text
killSlack(e) = enemyDamageDeadline(e) - playerKillEta(e)
interceptSlack(I) = enemyArrivalEta(I) - playerArrivalAndAimEta(I)
```

只有 slack 为正且有明确行动产出时，才允许主动偏离当前目标。

### 5.4 最低测试集合

- `turnCooldownMs=200` 与 `500` 时，ETA 单调增加，AI 不绕过限制。
- 敌人更近、基地 HP 更低、环砖更少时，威胁 slack 不应增加。
- 玩家更远、需要转向或需要更多射击时，kill slack 不应增加。
- 同一 World、同一参数、同一输入得到 byte-identical 结果。
- 模型只读，不消费 World RNG。

## 6. Phase 2：用 threat-slack 约束现有决策链

不要立即重写整个 `DecisionCore`。第一版只在现有分支入口增加统一的“行动有效性”判断。

### 6.1 防守分支

`defenseIntercept` / `midLaneDefense` / `baseLaneSentry` 只有满足下列条件之一才能提交：

- 当前有即将命中的基地子弹，原地保持射线可以拦截；
- 当前有正 slack 的拦截行动；
- 当前移动会缩短 threat ETA 或改善有效射击覆盖；
- 当前持位等待的下一次合法开火时间小于威胁剩余期限。

否则不得因为“检测到了敌人”就提交一个 `moveDir=null, fire=false` 的静止分支。

注意：不能简单把冷却期的所有分支改成 fall-through。§199 已证明盲目离开射击位也会导致基地失守。必须判断“保持位置是否有有效等待价值”。

### 6.2 进攻分支

`engage` / `hunt` 选择目标时，加入以下排序键：

```text
targetValue(e) =
  expectedBaseDamagePrevented(e)
  / (playerReachEta(e) + playerKillEta(e))
```

这不是再次增加一个静态 `bonusHuntBias`，而是让目标价值随着敌人的期限、玩家距离、射击成本变化。

### 6.3 短期 intent

当一个分支提交后，不保存永久目标，只保存短期 intent：

```ts
type ActionIntent = {
  kind: ActionId
  targetId: number
  expiresTick: number
  minSlack: number
  expectedProgress: number
}
```

intent 每 6～15 ticks 或发生威胁事件时重新验证。以下任一条件成立就释放：

- 目标死亡或移动到不可达区域；
- slack 低于阈值；
- 连续 N ticks 无移动、无射击、无覆盖改善；
- 新威胁的 slack 明显更差；
- 转向锁定导致原 intent 已无法按时完成。

这不是 `huntCommitTicks` 的简单加长版；intent 必须有期限、进展和威胁约束。

## 7. Phase 3：动态攻击覆盖点

目标是解决 S34/S8 一类“回基地驻守反而失去全场压制”的问题。

### 7.1 候选点生成

候选点只能来自当前 World：

- 玩家当前 cell；
- 当前敌人到基地路径上的有限候选 cell；
- 能同时看到一个以上敌人的射击行/列交汇点；
- 基地上方咽喉、环外侧和可安全撤离点。

禁止使用 stage ID 特判或固定坐标表。

### 7.2 覆盖评分

```text
coverageValue(I) =
  sum(threatDamagePrevented(e, I))
  - travelCost(I)
  - turnCost(I)
  - opportunityCost(I)
  - baseExposureRisk(I)
```

只有当 `coverageValue(I) > currentActionValue` 且至少一个主要威胁的 `interceptSlack(I) > 0` 时才允许移动到 I。

覆盖点不是永久基地站位：

- 不设置长时间固定 hold；
- 默认 lease 6～15 ticks；
- 目标死亡、基地侧面出现新威胁、slack 变负时立即释放；
- 规划失败时回到原有 hunt/engage 行为。

### 7.3 多威胁护栏

在场上有 3～4 个敌人时，单目标追击必须支付机会成本。以下情况禁止远离当前区域：

- 第二威胁的 `enemyDamageDeadline` 小于玩家完成当前击杀的 ETA；
- 当前覆盖点只能处理一个敌人，而存在两个独立基地射线；
- 玩家离基地已经超过可接受距离，且返回 ETA 大于基地 slack。

这不是“自动回基地”，而是限制不能创造不可逆的覆盖缺口。

## 8. Phase 4：安全成长和道具策略

低星级是 hard 的长期战力瓶颈，但不能重演 `pickupPriority` 的跨地图绕路问题。

道具只在以下条件同时满足时纳入当前行动：

- 道具路线与当前攻击/拦截路线重叠，或绕行成本小于当前 slack；
- 道具收益足以抵消预计的后续射击成本；
- 不会让基地 threat slack 变负；
- 不会把玩家带入无逃生方向的局部区域。

道具策略应该是 `safe opportunity`，不是全局高优先级 `pickup`。先做诊断，不要直接提高 pickup 权重。

## 9. CMA-ES 的正确使用方式

### 9.1 何时可以重新启动

必须先完成 Phase 0～2，并证明新结构确实改善了某类失败；否则不启动完整 CMA-ES。

### 9.2 新搜索空间

重新定义 active search space，只包含当前行为结构中确实使用的少量参数，例如：

- threat slack 安全余量；
- 防守 intent 最大 lease；
- 无产出释放 ticks；
- 覆盖点最小收益；
- 多威胁机会成本；
- 安全拾星最大绕行成本。

排除：

- `turnCooldownMs`；
- aimError、suboptimalPathProb 等 game-feel 参数；
- 已关闭或已证伪的 dodge/station/retreat 参数；
- 只在单关有效的 stage-specific 参数。

### 9.3 搜索协议

1. 20 seeds：只做方向筛选。
2. 60 seeds：同 seed CRN 配对，确认候选。
3. 另一个冻结 seed 集：做 holdout，禁止拿训练 seed 作为最终证据。
4. hard 是主目标；classic 只做无意外回归护栏；chaos 只防大幅退化。
5. 停止条件：局部扫描 3～5 个候选批次仍只有 ±1～2pp 噪声，即确认参数面没有足够 ROI。

### 9.4 候选采纳标准

候选必须满足：

- hard 总胜率不低于当前 DEFAULT，且最好有显著提升；
- `base_destroyed` 率下降；
- 最弱关不出现 ≥5pp 的未经解释回归；
- 改善关数不少于显著回归关数；
- classic 无显著回归；
- 失败档质量改善不能替代胜率改善；
- 同一 seed 重跑 byte-identical。

如果没有候选同时满足这些条件，保留当前参数，不进行“为了有结果而发货”的调参。

## 10. 推荐执行顺序和交付物

### M0 — 诊断工具

交付：

- threat ledger 可选输出；
- 失败分类器；
- 当前 2100 局 baseline JSON；
- 至少 10 个代表性失败的逐 tick 报告；
- 无行为改动的 parity 测试。

决策门：每类主要失败都有可复现证据。

### M1 — threat-slack 纯模型

交付：

- `ThreatBudget.ts`；
- 单元测试、单调性测试、200/500ms 规则测试；
- 离线 replay/forensics 对现有失败局打分；
- 不接入行为或默认 OFF。

决策门：模型能在失败发生前识别至少一类 threat deadline，且误报不会大面积覆盖正常 hunt。

### M2 — 行动契约和局部接入

交付：

- `ActionContract.ts`；
- 防守分支无产出提交诊断；
- `defenseIntercept` / `midLaneDefense` / `baseLaneSentry` 的严格 slack 门控；
- 默认 OFF 的 hard 实验开关。

决策门：目标失败家族中 `no_output_commit` 和 `late_detection` 下降，且不增加跨关驻守型失败。

### M3 — 动态攻击覆盖点

交付：

- `CoveragePlanner.ts`；
- 固定候选上限和低频缓存；
- 短期 intent lease；
- S34、S8、S24、S20 定向 A/B。

决策门：至少两个基地毁灭主导弱关改善，并且 35 关没有新的显著回归。

### M4 — 安全道具与局部参数校准

只有 M2/M3 通过后才执行。先局部扫描，再决定是否 CMA-ES；不触碰 turn cooldown。

### M5 — 发货或回滚

所有新行为保留 feature flag 和默认值，直到通过 35×60 A/B、完整测试、determinism 和性能门禁。失败时回退到上一个通过门禁的 phase，不删除诊断工具。

## 11. Agent 执行纪律

- 修改 `Simulation` 行为前，先写失败复现测试；纯诊断工具改动不需要伪造行为测试，但必须验证输出确定性。
- 不启动 dev server；使用 `bun run check`、`bun run build` 和 headless simulation。
- 运行 full sweep 前确认使用官方 `stageIndex=0` 口径，避免掉落 RNG 分叉。
- 任何全量仿真先保存 `--json` 语料；后续修复只重跑失败 subset。
- 不用 `git add -A`，不把无关的 `*.md` 纳入提交。
- 每个非显然设计决定先记录到 `DECISIONS.md`，调优实验细节写入对应 progress 文档。

## 12. 最终 Definition of Done

- [ ] threat ledger 能解释当前主要 hard 失败家族。
- [ ] threat-slack 模型纯函数、确定性、无 World 写入、无 RNG 消费。
- [ ] 200ms turn cooldown 未被修改；模型对未来 500ms 配置仍成立。
- [ ] 新决策没有长期静态驻守，intent 有期限、进展和释放条件。
- [ ] 35×60 hard A/B：总体不劣于 DEFAULT，基地毁灭率下降，最弱关无严重回归。
- [ ] classic 无显著回归，self-kill 不增加。
- [ ] `bun run check`、`bun run build` 通过。
- [ ] 必要的 `DECISIONS.md`、`docs/god-ai-tuning.progress.md` 和工作日志已更新。
