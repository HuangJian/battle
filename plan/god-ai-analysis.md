# God AI 深度分析报告

> 基于 [God-AI-Tuning.md](file:///Users/hj/dev/github/battle/plan/God-AI-Tuning.md)、[god-ai.progress.md](file:///Users/hj/dev/github/battle/plan/god-ai.progress.md)、[GodAIInput.ts](file:///Users/hj/dev/github/battle/src/ai/GodAIInput.ts) 全量代码审查 + [tuning-log](file:///Users/hj/dev/github/battle/docs/god-ai-tuning-log.md) + 仿真基建工具链。

---

## 一、当前状态总结

| 指标 | 值 | 评价 |
|------|------|------|
| 胜率 (classic stage 0) | **0/5** | ❌ 无法过关 |
| 基地存活率 | 80% (4/5) | ⬆ 从 0–20% 提升 |
| 击杀数 | 4–8/20 | ⬆ 从 0–3 提升 |
| 首杀时间 | 19–42s | ❌ 远超合理范围 |
| 每场开火次数 | 7–8 次 | ❌ 严重不足 |
| 失败模式 | 4/5 lives_exhausted, 1/5 base_destroyed | 转为「被磨死」而非「基地秒丢」|
| 目标级 | O2（基地存活）尚未达标 | ≥ 90% 门禁未过 |

> [!IMPORTANT]
> 核心矛盾：**CMA-ES 找到了「贴身龟缩」局部最优** —— 基地保住了但永远杀不完 20 只敌人。AI 需要从「纯防守」进化到「攻守切换」，这是纯参数优化无法解决的**架构级**问题。

---

## 二、代码级 Bug 与问题

### Bug 1: 🐛 `urgencyBonus` 计算逻辑反转

[selectTarget L1152](file:///Users/hj/dev/github/battle/src/ai/GodAIInput.ts#L1152):
```typescript
const urgencyBonus = tc.row >= defenseRow ? (baseRow - tc.row) * 100 : 0
```

意图是「越接近基地越紧急、bonus 越高」。但 `defenseRow = baseRow - 1 = 23`，`baseRow = 24`：

| 敌人 row | `baseRow - row` | urgencyBonus |
|----------|-----------------|-------------|
| 23 (defenseRow) | 1 | 100 ✅ |
| 24 (baseRow，极近) | 0 | **0** ❌ |
| 25 (在基地下方) | -1 | **-100** ❌❌ |

**敌人越接近基地，urgencyBonus 反而越低甚至变负。** 最危险的情况（敌人在基地行或以下）反而被打低了优先级。

> [!CAUTION]
> 这是一个真正的 bug —— 当敌人已经到达基地行时，`urgencyBonus = 0`，甚至当敌人在 row 25 时变为负数，导致 AI 忽略最紧急的威胁。

**修复建议**：`urgencyBonus = tc.row >= defenseRow ? (tc.row - defenseRow + 1) * 100 : 0`

---

### Bug 2: 🐛 Power-up 评分公式方向反转

[findPowerUpTarget L865](file:///Users/hj/dev/github/battle/src/ai/GodAIInput.ts#L865):
```typescript
let score = priority * 1000 - dist * 10 - dangerLevel * 500
```

`POWERUP_PRIORITY` 中 `bomb = 0`（最高优先级），`boat = 6`（最低优先级）。
所以 `bomb` 的 base score = `0 * 1000 = 0`，而 `boat` 的 base score = `6 * 1000 = 6000`。

**低优先级道具反而得到更高的基础分！** 后面虽然用 `+2000`/`+1000` 的 bonus 和 `-500` 的 penalty 做了修补，但逻辑方向是反的。

bomb 最终 score ≈ `0 + 2000 = 2000`，而 `tank` (priority=4) 最终 score ≈ `4000`。**tank 比 bomb 分高。**

**修复建议**：`(6 - priority) * 1000` 或直接用倒转映射表。

---

### Bug 3: 🐛 `maxDist` 逻辑与 CMA-ES 参数互斥

[findPowerUpTarget L883](file:///Users/hj/dev/github/battle/src/ai/GodAIInput.ts#L883):
```typescript
const maxDist = priority <= 1 ? this.params.powerupMaxDivertDistance : 8
```

CMA-ES 优化后 `powerupMaxDivertDistance = 3`。这意味着 **高价值道具 (bomb/star, priority 0/1) 的最大绕行距离反而是 3 cells**，而低价值道具 (freeze/fence/tank/shield) 允许绕行 **8 cells**。完全反了。

**修复建议**：交换逻辑 —— 高价值道具允许更远绕行。

---

### Bug 4: 🐛 `findBulletThreatToBase` 未过滤已被墙挡住的威胁

[findBulletThreatToBase L583-609](file:///Users/hj/dev/github/battle/src/ai/GodAIInput.ts#L583-L609):

弹道投射会在遇到 brick/steel 时 break，但**检查顺序是先检查是否过了基地区域再检查地形**：
```typescript
if (Math.abs(fx - baseCx) < baseHalf * 2 && Math.abs(fy - baseCy) < baseHalf * 2) {
  crossesBase = true; break; // 先命中 base 范围判定
}
// ...之后才检查地形遮挡
const terrain = w.tileMap.get(col, row)
if (terrain === 'brick' || terrain === 'steel') break
```

如果基地前面有砖墙保护，子弹理应被砖墙挡住。但由于基地范围检测（`baseHalf * 2 = 32px`）比实际基地大，且检测在地形之前执行，可能在墙后面的格子也被判为 `crossesBase = true`。这不是严重 bug（会导致过度防守而非漏防），但浪费了大量 T8 拦截行动。

---

### Bug 5: 🐛 `killerKind` 从未被填充

[simulation-runner.ts L166-173](file:///Users/hj/dev/github/battle/tools/simulation-runner.ts#L166-L173):
```typescript
if (baseDestroyed) {
  for (let i = allEvents.length - 1; i >= 0; i--) {
    const e = allEvents[i]
    if (e.type === 'base_destroyed') {
      // Walk back to find the last tank_destroyed or bullet_fired near base.
      break  // ← 只有 break，没有实际赋值 killerKind
    }
  }
}
```

`FailureTaxonomy` 定义了 `killerKind` 字段，但从未被赋值。失败归因中永远不知道「谁毁了基地」，对调校无用。

---

### Bug 6: 🐛 测试中的潜在 NPE

[god-ai-gates.test.ts](file:///Users/hj/dev/github/battle/tests/god-ai-gates.test.ts) L25 附近：
```typescript
expect(result.failure!.cause)...
```
如果 God AI 在 4000 ticks 内过关（`outcome = 'stage_clear'`），`result.failure` 为 `undefined`，`!` 断言会导致运行时异常。虽然当前 AI 无法过关所以不触发，但随着 AI 变强这会是定时炸弹。

---

## 三、架构设计问题

### 问题 1: ⚠️ `suboptimalPathProb = 0.3` 对 God AI 反直觉

God AI 被定义为「理论上限玩家」，但 30% 概率走随机方向！这是 CMA-ES 优化的结果 —— 优化器发现随机走位（意外闪避子弹）比精确导航更能存活。

**问题在于**：这不是有意义的「战术」，是「用随机性弥补缺失的闪避逻辑」。实际效果：
- 30% 的 tick，AI 放弃 A* 路径走随机方向
- 导航不稳定，经常走回头路
- 两处独立调用（[L973](file:///Users/hj/dev/github/battle/src/ai/GodAIInput.ts#L973) + [L1028](file:///Users/hj/dev/github/battle/src/ai/GodAIInput.ts#L1028)），叠加后实际随机率更高

**建议**：一旦 M3（闪避安全验证）实现完善，应将 `suboptimalPathProb` 降到 0.05 以下。随机性应来自「战术选择」而非「盲目乱走」。

---

### 问题 2: ⚠️ `think()` 中 `directMove` 绕过了 A* 路径

[think() L357](file:///Users/hj/dev/github/battle/src/ai/GodAIInput.ts#L357):
```typescript
// ---- T2b: Navigate directly towards target ----
this._moveDir = this.directMove(this.playerCell())
```

注释说是 T2b，但 T2b 是「移动中不开火」，这里实际是**完全放弃 A* 路径改用直线移动**。`directMove` 不走 A*、不考虑迷宫拓扑，只是朝目标方向硬走+破墙。这意味着：
- `followPath()` 和 `replan()` 的整个 A* 导航系统在 `think()` 主分支中**从未被调用**（只在 dodge 分支里用）
- AI 会无脑朝目标撞墙，浪费大量子弹和时间破不必要的墙
- `replanInterval` 参数在主循环中无效

这是代码演进中的架构退化 —— 曾经用 A*，后来改成 directMove，但 A* 相关代码（`followPath`、`replan`、`replanInterval`）没有清理。

---

### 问题 3: ⚠️ Power-up 检测只在 aggressive 模式中触发

[think() L316](file:///Users/hj/dev/github/battle/src/ai/GodAIInput.ts#L316):
```typescript
if (this.aggressive) {
  // ...
  const puTarget = this.findPowerUpTarget(pcx, pcy)
```

S5 道具拾取只在 `frozen || shielded` 时检查。**正常模式下 AI 完全忽略道具**（包括 bomb、star 等高价值道具）。这与 tuning plan 中「道具经济」的设计意图严重不符。

---

### 问题 4: ⚠️ 1304 行的「上帝类」

`GodAIInput.ts` 一个文件 1304 行、45KB，包含了：
- 目标选择（战略层）
- 拦截计算（战术层）
- 子弹威胁检测
- 闪避逻辑
- 开火控制
- 路径导航
- 道具经济
- 碰撞检测辅助

随着技巧库的扩展（计划还有 ~20 项未实现），这个文件会膨胀到 2000+ 行。

**建议**：拆分为模块 —— `StrategyPlanner`（目标选择）、`ThreatAssessor`（威胁评估+闪避）、`FireControl`（射击决策）、`Navigator`（导航+拦截），由 `GodAIInput.think()` 编排。

---

### 问题 5: ⚠️ 缺少攻守切换（S6）是 0% 胜率的核心瓶颈

tuning log 的结论清楚：「贴身龟缩」策略保住了基地但无法清场。`selectTarget` 在非 aggressive 模式下**只有两种行为**：
1. 拦截最近敌人（但 `maxPlayerDistFromBase = 4` 限制了追击距离）
2. 回到默认防守位

没有「场上敌人少时主动出击」的逻辑（S10 endgame 的触发条件是 `enemiesRemaining ≤ 1 && enemies.length ≤ 1`，几乎是最后一只敌人才激活）。

---

## 四、调校方法论问题

### 问题 1: CMA-ES 优化的局限性

CMA-ES 可以优化**连续参数**，但 God AI 的核心问题是**缺少决策分支**（攻守切换、主动出击、咽喉控制等）。参数优化能在现有决策架构下找到最优配置，但无法发明新策略。

**当前状态**：CMA-ES 已经把「纯防守」策略优化到极限（80% 基地存活率），继续调参的边际收益趋近于 0。

### 问题 2: 仿真基建缺少「热力图」诊断

当前诊断工具（decision-trace、analyze-trace）是 tick 级的文本日志。缺少空间维度的可视化：
- 玩家位置热力图（验证「是否过于集中在某区域」）
- 击杀位置分布（验证「是否在有效区域交战」）
- 基地子弹来源方向分布（验证「哪个方向的防守最弱」）

### 问题 3: 测试状态与门禁不一致

progress.md 报告「368 pass, **2 fail**」—— 但 AGENTS.md §9 要求 `bun run check` 全绿才能声明完成。有失败的测试意味着当前代码不满足 quality gate。

---

## 五、优化路线图建议

按 tuning plan 的「战斗目标阶梯」重新排列优先级，**聚焦于解锁 0% → 过关的核心瓶颈**：

### 第一阶段：修复 Bug + 解锁过关能力（1–3 轮）

```mermaid
graph LR
  B1[修 urgencyBonus 反转] --> B2[修 powerup score 反转]
  B2 --> S6[S6 攻守切换]
  S6 --> V[验证: classic 过关率 > 0%]
```

| 优先级 | 项目 | 预期效果 |
|--------|------|----------|
| **P0** | 🐛 修复 `urgencyBonus` 反转 (Bug 1) | 最危险敌人被正确优先 |
| **P0** | 🐛 修复 `powerup score` 反转 (Bug 2+3) | 道具经济恢复正常 |
| **P0** | 🐛 填充 `killerKind` (Bug 5) | 失败归因可用 |
| **P1** | **S6 攻守切换** — 核心瓶颈 | 从 0% 到 >0% 过关率 |
| **P1** | 正常模式下启用道具拾取 | bomb/star 可被利用 |

**S6 攻守切换的简单实现方案**：
```
if (enemies.length <= 2 || enemiesRemaining <= 5) → 主动出击模式（放宽 maxPlayerDistFromBase）
if (baseWallIntegrity < 50%) → 紧急回防模式
else → 正常拦截防守
```

### 第二阶段：稳定过关率（3–5 轮）

| 优先级 | 项目 | 目标 |
|--------|------|------|
| **P2** | S2 咽喉点控制 | 减少漏防 |
| **P2** | T2a/T2b 对齐阈值收紧 | 减少无效停车 |
| **P2** | directMove 与 A* 混合策略 | 近距离直走、远距离 A* |
| **P3** | M1 预判射击 | 提高命中率 |
| **P3** | S3 出生点压制 | 减少敌人涌入 |

### 第三阶段：效率优化 + 高难度达标（5–8 轮）

| 优先级 | 项目 | 目标 |
|--------|------|------|
| **P4** | S7 基地墙完整度感知 | O2 100% 达标 |
| **P4** | T8 基地子弹拦截完善 | 终极防线 |
| **P5** | 代码重构（拆分上帝类） | 可维护性 |
| **P5** | 降低 suboptimalPathProb | 导航稳定性 |

---

## 六、与 Tuning Plan 的差距矩阵

````carousel
### 战略层技巧实现状态

| 技巧 | Plan 标注 | 实际代码 | 差距评估 |
|------|-----------|----------|----------|
| S1 基地防御优先 | 【缺】 | 部分实现（`selectTarget` 有拦截逻辑） | 🟡 有但不完整 |
| S2 咽喉点控制 | 【缺】 | ❌ 完全未实现 | 🔴 |
| S3 出生点压制 | 【缺】 | ❌ 完全未实现 | 🔴 |
| S4 波次节奏管理 | 【缺】 | ❌ 完全未实现 | 🔴 |
| S5 道具经济 | 【缺】 | 部分实现，但有 bug | 🟡 有但评分反转 |
| S6 攻守切换 | 【缺】 | ❌ **核心缺失** | 🔴🔴 |
| S7 基地墙完整度 | 【缺】 | ❌ 完全未实现 | 🔴 |
| S8 冻结窗口 | 【缺】 | ✅ 已实现 (`aggressive` 模式) | 🟢 |
| S9 重生保护期 | 【缺】 | ✅ 已实现 (`shielded` 跳过闪避) | 🟢 |
| S10 终局收尾 | 【缺】 | 部分实现，触发条件太严 | 🟡 |
| S11 出生点轮换预测 | 【缺】 | ❌ 完全未实现 | 🔴 |

<!-- slide -->
### 战术层技巧实现状态

| 技巧 | Plan 标注 | 实际代码 | 差距评估 |
|------|-----------|----------|----------|
| T1 拦截几何 | 【缺】 | ✅ `interceptCell()` 已实现 | 🟢 |
| T2a 停车瞄准 | 【缺】 | ✅ 已实现+冷却修复 | 🟢 |
| T2b 移动中不开火 | 【缺】 | ✅ `shouldFireInDir` 检查 | 🟢 |
| T3 优先级目标 | 【弱】 | ✅ `KIND_THREAT_WEIGHT` 已实现 | 🟢 |
| T4 破墙开路 | 【弱】 | ✅ `directMove`+`canMoveOrBreak` | 🟢 |
| T5 子弹拦截 | 【弱】 | 🟡 只在 `shouldFireInDir` 中 | 🟡 |
| T6 不拆自家墙 | 【缺】 | ✅ `isBaseProtectionBrick` | 🟢 |
| T7 风筝拉扯 | 【缺】 | ❌ 未实现 | 🔴 (低优先级) |
| T8 基地子弹拦截 | 【缺】 | ✅ 已实现 (有 bug, Bug 4) | 🟡 |
| T9 同列排序 | 【缺】 | ✅ `findEnemyDirection` 带权重 | 🟢 |
| T10 破墙价值评估 | 【缺】 | ❌ 未实现 | 🔴 |
| T11 钢墙绕行 | 【缺】 | ✅ `scanAhead` 区分 steel | 🟢 |
| T12 对向先手 | 【弱】 | 🟡 T2a 部分覆盖 | 🟡 |
| T13 林间隐蔽 | 【未来储备】 | ❌ 跳过 | ⬜ |

<!-- slide -->
### 微操层技巧实现状态

| 技巧 | Plan 标注 | 实际代码 | 差距评估 |
|------|-----------|----------|----------|
| M1 预判射击 | 【缺】 | ❌ 未实现 | 🔴 |
| M2 闪避后回位 | 【弱】 | 🟡 dodge 后回 followPath | 🟡 |
| M3 闪避安全验证 | 【缺】 | ✅ `isSafeDir` 已实现 | 🟢 |
| M4 射速管理 | 【弱】 | 🟡 冷却感知已有 | 🟡 |
| M5 格子对齐 | 【有】 | ✅ snap() 在用 | 🟢 |
| M6 冷却感知射击 | 【缺】 | ✅ `onCooldown` 检查 | 🟢 |
| M7 格子对齐后转向 | 【有】 | ✅ 已有 | 🟢 |
| M8 冰面减速意识 | 【缺】 | ❌ 未实现 | 🔴 (低优先级) |
| M9 子弹速度差利用 | 【缺】 | ❌ 未实现 | 🔴 (低优先级) |
| M10 多目标同列连射 | 【缺】 | ❌ 未实现 | 🔴 |
````

---

## 七、关键建议总结

> [!TIP]
> **一句话**：先修 bug（尤其是 urgencyBonus 和 powerup 评分反转），再加 S6 攻守切换。这两步可能直接让过关率从 0% 跳到 30%+。

1. **Bug 先修**：urgencyBonus 反转 + powerup 评分反转是「低风险高回报」改动，可能显著改善当前表现
2. **S6 是解锁关键**：纯参数优化已到天花板，必须增加攻守切换逻辑
3. **正常模式启用道具**：当前只在 freeze/shield 下检查道具，浪费了大量 bomb/star
4. **降低 suboptimalPathProb**：30% 随机走位是对缺失闪避逻辑的 workaround，而非真正的战术
5. **directMove vs A* 混合**：近距离直走+远距离 A* 的混合策略比「全程直走破墙」更高效
6. **代码可维护性**：1304 行的上帝类需要在 O3 达标后拆分
7. **测试红灯**：先修复 2 个 failing test，再继续调校
