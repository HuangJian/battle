当前 God AI 状态：

**已实现的核心改进：**
1. ✅ 阈值常量全部移入 `GodAIParams` — 可被 CMA-ES 优化器自动调参
2. ✅ CMA-ES 自动参数优化器 (`tools/optimize-godai.ts`) — sep-CMA-ES 算法
3. ✅ 决策追踪系统 (`tools/decision-trace.ts`) — 每 tick 记录完整决策过程
4. ✅ T2a 冷却中停车修复 — 冷却时不再进入 T2a 停车，直接 fall through 到导航
5. ✅ CMA-ES 优化的默认参数 — 基地存活率 40%→80%，击杀 2.2→5.6

**当前性能（classic stage 0, 5 seeds）：**
- 胜率：0/5（需要过关但 AI 仍无法清场）
- 基地存活率：4/5 = 80%（之前 0-1/5 = 0-20%）
- 击杀数：4-8/20（之前 0-3/20）
- 失败模式：4/5 lives_exhausted, 1/5 base_destroyed
- 首杀时间：tick 1149-2528（19-42s，仍然偏慢）

**关键决策失误分析（通过 CMA-ES + 决策追踪发现）：**

1. **T2a 冷却空转（头号败因，已修复）**：
   - 玩家 10116 tick 仅开火 7 次，最大间隔 4896 tick（81s）
   - T2a 停车瞄准但冷却中不开火也不移动，浪费大量时间
   - 修复：T2a 仅在非冷却时触发

2. **防守位置偏左（已优化）**：
   - 玩家 95% 时间在 col 8-9，但基地在 col 12
   - CMA-ES 找到 defenseColSpread=3 + threatRangeCells=8 最优

3. **首杀太慢（待解决）**：
   - 首杀在 tick 1149-2528（19-42s）
   - 玩家初期导航到防守位置期间不主动交战

4. **开火频率极低（待解决）**：
   - 即使修复 T2a，每场仅开火 7-8 次
   - navigate 分支的 shouldFireInDir 太保守

**优化器找到的最优参数：**
```
reactionDelay: 0          (2→0  无延迟)
aimError: 0               (0.02→0  完美瞄准)
suboptimalPathProb: 0.3   (0.1→0.3)
defenseRowOffset: 1       (3→1  紧贴基地)
defenseColSpread: 3       (8→3  窄防守)
threatRangeCells: 8       (30→8  只响应近敌)
maxPlayerDistFromBase: 4  (12→4)
t8MaxInterceptDistCells: 8 (6→8)
baseWallScanRadius: 5     (3→5)
replanInterval: 50        (20→50)
powerupMaxDivertDistance: 3 (15→3  不追道具)
endgameEnemyThreshold: 1  (2→1)
```

**下一步调优方向：**
1. S6 攻守切换 — 当前「贴身龟缩」是纯防守策略，需要加入进攻切换才能过关
2. S10 终局猎杀 — 终局逻辑需要更强的主动追杀能力
3. 开火效率 — navigate 分支需要更积极的开火（朝目标方向 proactive fire）
4. T2a 对齐阈值 — 边缘对齐导致 T2a 触发但 scanAhead 找不到目标

**测试状态：**
- 368 pass, 2 fail (god-ai-gates 门禁测试 + ai-calibrate 已修复)
- Typecheck ✓, Lint ✓, Format ✓, Build ✓
