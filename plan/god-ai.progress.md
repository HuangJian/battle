当前 God AI 状态：

**已实现的核心改进：**
1. ✅ 阈值常量全部移入 `GodAIParams` — 可被 CMA-ES 优化器自动调参
2. ✅ CMA-ES 自动参数优化器 (`tools/optimize-godai.ts`) — sep-CMA-ES 算法
3. ✅ 决策追踪系统 (`tools/decision-trace.ts`) — 每 tick 记录完整决策过程
4. ✅ T2a 冷却中停车修复 — 冷却时不再进入 T2a 停车，直接 fall through 到导航
5. ✅ CMA-ES 优化的默认参数 — 基地存活率 40%→80%，击杀 2.2→5.6

**Round 2 — Classic 模式适配 + 13 项修复（2026-07-28）：**

修复了仿真工具和 AI 在 classic 模式下的 13 个根本性 bug（详见 DECISIONS §33）。

**当前性能（classic stage 0, 20 seeds）：**
- 胜率：0/20（需要过关但 AI 仍无法清场）
- 基地存活率：20/20 = **100%**（Round 1: 80%）
- 平均击杀：2.6/20（Round 1: 5.6/20，下降因为 classic 模式更难击杀）
- 最佳种子击杀：17/20（seed 3）
- 0 击杀种子：55%（11/20 seeds 得 0 击杀）
- 失败模式：全部 timeout（10 分钟未清场）

**关键修复：**

1. **仿真工具漏设 `world.rules`（头号 bug，已修复）**：
   - `simulation-runner.ts` 直接调 `loadStageData` 而非 `startGame`
   - `world.rules` 保持 `DEFAULT_RULES`（现代），classic 规则从未生效
   - 修复：添加 `world.rules = RULES[difficulty] ?? DEFAULT_RULES`

2. **`onCooldown` 时间冷却 vs 子弹数冷却（已修复）**：
   - classic `bulletCap` 模式无时间冷却，仅限制在途子弹数
   - AI 用时间冷却导致每场仅开火 1-2 次
   - 修复：`bulletCap` 模式下检查在途子弹数

3. **navigate 分支无条件开火摧毁自家基地（已修复）**：
   - `!aimDir` 为 true 时不检查 `shouldFireInDir`，直接开火
   - classic 基地 HP=1，一枪自毁
   - 修复：始终调用 `shouldFireInDir`（含 T6 基地保护检查）

4. **shield 触发 aggressive 放弃防守（已修复）**：
   - `aggressive = frozen || shielded` 导致玩家 3 秒护盾期间追敌外出
   - 修复：`aggressive = frozen` only — shield 仅跳过闪避，不放弃防守

5. **directMove 水平优先（已修复）**：
   - 玩家横向移动无法进入敌人所在行，T2a 无法触发
   - 修复：垂直优先 — 先纵向靠近敌人，再横向对齐
   - 效果：击杀从 0 跃升至 17（seed 3）

6. **T2a-hold（新增）**：
   - 冷却中（子弹在途）AI 追其他目标，放弃已对齐敌人
   - 子弹结算后 AI 不再对齐，T2a 无法再次开火
   - 修复：冷却中保持对齐位置，等待子弹结算后立即 T2a 开火

7. **selectTarget 无敌人时发呆（已修复）**：
   - 所有敌人在 `threatRangeCells` 外时，AI 返回防守位置发呆
   - 修复：追击最近敌人，无论距离

8. **canHunt 过于激进（已修复）**：
   - `enemies.length <= 2 || enemiesRemaining <= 5` 在刷怪间隔触发
   - 修复：`enemies.length <= 2 && enemiesRemaining <= 3`

9. **baseUnderThreat 触发太晚（已修复）**：
   - `row >= defenseRow (23)` 敌人已贴基地
   - 修复：`row >= 20`，提前 4 行

**当前参数：**
```
reactionDelay: 0
aimError: 0
suboptimalPathProb: 0.05
defenseRowOffset: 1
defenseColSpread: 3
threatRangeCells: 26
maxPlayerDistFromBase: 7
t8MaxInterceptDistCells: 8
baseWallScanRadius: 5
replanInterval: 50
powerupMaxDivertDistance: 3
endgameEnemyThreshold: 1
AIM_RANGE_CELLS = 15
```

**下一步方向：**

1. **M1 预判射击**：当前玩家朝敌人当前位置射击，但敌人移动导致命中率极低。
   需要预测敌人移动方向，射击其未来位置。这是解决 0 击杀种子的关键。
2. **S6 攻守切换**：当前玩家过于防守，需要根据场上敌人数量/类型动态切换。
3. **T2a-hold 死亡问题**：玩家在冷却中原地等待，被敌方子弹击杀。
   需要在 T2a-hold 中加入威胁检测。
4. **AIM_RANGE_CELLS 调优**：需要实验不同值找到最优。
