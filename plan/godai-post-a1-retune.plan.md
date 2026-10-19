# God-AI 侧射移除后的退化诊断与调优 plan（v1，2026-10-19）

> **本文可独立阅读**：不依赖任何对话上下文。结论均带实测数字与 `file:line` 锚点。
> **状态**：**仅诊断 + 方案设计，未实施**（引擎侧 A1 已落地并交付）。
> **上游证据**：`DECISIONS.md §2026-10-19-goalnn-a1-global-fire-facing-and-god-gates-retired` ·
> `plan/nn-capability-upgrade.plan.md §3-A1` · 实测语料 `tmp/a1-before-hard.json` / `tmp/a1-after-hard.json`
> （复算脚本 `tmp/a1-hard-diff.ts`）· `docs/god-ai-tuning.progress.md` 顶部纪元变更块。
> **下游产出**：God-AI 调参腿（本 plan 落地后再开）。

---

## 0. 一页摘要

| 项 | 内容 |
|---|---|
| **现象** | A1（引擎统一"开火方向 = 已提交朝向"）落地后，God-AI 在 hard 上**胜率 77.7% → 58.7%、算术 score 0.7554 → 0.6104**（35 stages × 20 seeds，CRN 同种子） |
| **根因（本文结论）** | God-AI 的**通用开火不变量**是「**子弹沿 `_moveDir` 飞**」；A1 之前这个不变量靠引擎 bug（转身被冷却拒绝时子弹仍走请求方向）**免费成立** = 瞬时甩炮（snap-aim）。A1 修掉引擎 bug 后该不变量**只在转身被接受的那一拍**成立 |
| **缺口** | God-AI 的开火决策**几乎不做转身冷却判断**（`ctx.onCooldown` 是**开火**冷却，不含转身冷却） |
| **推荐方案** | **方案 A：提交朝向开火门**——开火前要求 `_moveDir === p.dir`（已对齐）**或**本拍转身会被接受；否则本拍不射（等 1..k 拍转身提交后再射） |
| **预期** | 把"白耗的偏弹"换成"晚 1..k 拍的准弹"⇒ 回收大部分胜率缺口；不引入新的作弊 |
| **不建议** | 方案 E（只重标基线不调优）——除非目标只是存档；方案 D/改引擎回退 —— 见 §3.5 / §5 |

**一句话**：这不是"God-AI 变笨了"，而是"God-AI 一直靠引擎 bug 免费瞄准，现在 bug 没了，**它的瞄准时序必须补上**"。

---

## 1. 来龙去脉

### 1.1 引擎侧发生了什么（A1）

- 改动落点：`src/game/SimulationCombat.ts:282-292` —— `tryFire()` 在 `passesFireGate` 之后、取
  `DIR_VECTORS[tank.dir]` **之前**，新增一次 `this.applyTurnCooldown(tank, w)`。
- 语义：**子弹方向 == 开火时刻的已提交朝向**。若本拍请求的转身在 `turnCooldownMs`
  （`src/config/rules.ts:257,341` = 200 ms ≈ 12 拍）内被拒 ⇒ `applyTurnCooldown`
  （`src/game/SimulationCombat.ts:116-139`）先把 `tank.dir` 回退到 `prevMoveDir` 并 `moving=false`，
  随后子弹**沿旧朝向**出膛。
- 九步顺序（`src/game/Simulation.ts:169-265`）：③ 玩家 `updatePlayerTank`（设 `dir` → `tryFire`）·
  ④ 敌 AI（设 `dir` → `tryFire`）· ⑦ `updateMovement`（`applyTurnCooldown`）。
  ⇒ A1 把"转身裁决"从 step⑦ **提前到开火之前**（在 `tryFire` 内），使 step⑦ 那次调用变幂等 no-op
  （`dir === prevMoveDir` 早退）⇒ **移动行为不变**，只有"子弹方向"这一件事变。
- 人类 / 敌人 / God / 护卫 / 诱饵**一律同规**（不再侧射）。

### 1.2 ★ 根因：God-AI 的隐含不变量被打破

God-AI 的**通用开火不变量**是「**子弹沿 `_moveDir`（本拍请求的移动/瞄准方向）飞**」：

| 锚点 | 代码 |
|---|---|
| `src/ai/god/candidates/shared.ts:143-144` | `self._moveDir = self.navigateTowards(target)` → `self._fire = !ctx.onCooldown && self.shouldFireInDir(ctx.pcx, ctx.pcy, self._moveDir ?? ctx.p.dir)` |
| `src/ai/god/candidates/shared.ts:246-255` | `carveFire(...)`：`self._fire = !onCooldown && shouldFireInDir(pcx, pcy, dir ?? p.dir, false)` |
| `src/ai/god/candidates/Hunt.ts:65-69` | detour：`self._moveDir = detourDir` → `self._fire = !onCooldown && self.rng.next() >= aimError` |

**旧引擎下**（A1 之前）：step③ 已把 `p.dir = _moveDir` 写入，`tryFire` 用 `p.dir` 取弹道
⇒ **即使转身在 step⑦ 被冷却拒绝、朝向被回退，子弹仍沿新 `_moveDir` 飞出**
⇒ 对 AI 而言 = **瞬时甩炮**：请求转向 + 同拍开火 = 一发命中新方向的炮弹。
`tests/godai-travel-fire.test.ts §218` 当时**明文把这个语义钉成了契约**
（"the sim fires along the NEW dir, deferring the turn"）。

**A1 之后**：同样的输入，若本拍转身**被冷却拒绝**，子弹**沿旧朝向**飞 ⇒ 这一枪**打偏**，
且白耗一发（`bulletCap` / 开火间隔），敌人继续逼近。

⇒ **AI 每一次「转向 + 同拍开火」过去几乎必中，现在只在本拍转身被接受时才中。**
这是 hard −19pp 的**主因假设**（§1.4 有分布证据；§4 有证伪判据）。

### 1.3 关键区分：`ctx.onCooldown` ≠ 转身冷却（**这是调优的落点**）

- `src/ai/god/think.ts:250-266`：`onCooldown` 的来源是**开火冷却** ——
  ① `fireModel === 'bulletCap'` 时 = 场上在飞玩家弹 ≥ 上限；② 否则 = `now - p.lastFire < nextFireInterval`。
  **两者都不含转身冷却。**
- 转身冷却只在三处被读，且都不是"开火门"：
  · `src/ai/god/candidates/Hunt.ts:697-701` —— 仅 `bulletLaneWait` 分支用 `turnDeferred` 决定"是否因子弹让路而 Hold"；
  · `src/ai/god/ThreatBudget.ts:190-208` —— 威胁记账；
  · `src/ai/god/CoveragePlanner.ts:224` —— 覆盖规划的转身代价 `msToTicks(turnCooldownMs)+1`。
- ⇒ **开火决策缺少"这一枪会不会沿 `_moveDir` 飞"的判据。** A1 之前无所谓（反正沿 `_moveDir` 飞）；
  A1 之后，这就是必须补上的一环。

### 1.4 实测（hard，35 stages × 20 seeds，CRN 同种子）

| | 算术 score | SUITE | 胜率 | 最弱关 |
|---|---|---|---|---|
| **A1 前** | **0.7554** | 0.5602 | **77.7%** | Battlement 0.337 / 20% |
| **A1 后** | **0.6104** | 0.4189 | **58.7%** | Riverbed 0.251 / 10% |

**逐关跌幅（dScore / dWin，完整表见 §7 附录）** —— 跌幅最大的：

| 关 | dWin | 关 | dWin |
|---|---|---|---|
| S8 Riverbed | **−45pp** | S16 Crossroads | **−40pp** |
| S19 Frozen Field | −35pp | S28 Thicket | −35pp |
| S31 Eagle Nest | −35pp | S5 Maze / S21 Checkers / S22 Oasis | −30pp |
| S26 Ice Palace | −30pp | S30 Concentric | −30pp |
| S33 Diamond | −30pp | S35 Final Redoubt | −30pp |

跌幅最小（≈免疫）：S20 Bastion（**+0.013 / +0pp**，它本就只有 45% 胜率）· S6 Brickworks −5pp ·
S7 Iron Curtain −5pp · S25 Quarry −5pp · S2 Waterways −5pp · S23 Ramparts −5pp · S34 Battlement −5pp。
⇒ 退化**不是均匀的**：**开阔/多车道/冰面/需要转向接战**的关跌得最狠（Riverbed / Crossroads / Frozen Field /
Maze / Ice Palace / Thicket），**本来就靠窄走廊/守口**的关几乎不动（Iron Curtain / Ramparts / Brickworks）。
这与"甩炮在需要转向瞄准的场景里最值钱"的假设一致。

> 60-seed 三难度（A1 后，新纪元基线）：hard SUITE 0.4123 / win 58% · classic 0.6421 / 83% · chaos 0.3786 / 53%。

### 1.5 门禁现状（**先读，避免白做工**）

- **God-AI 表现门禁已退役**：`tests/godai-score-gate.test.ts` 已 `test.skip`（用户指令 2026-10-19：
  旧 truth 是在侧射作弊行为上采集的）。**本 plan 不得复活它**；验收改用 `tools/eval/eval-suite.ts` 直接测。
- **`freeze:check` 仍启用**（确定性门，AGENTS §2.3）⇒ 本 plan 的任何 God-AI 行为改动**都会让它变红**，
  按新纪元三件套**更新 golden**（`tools/det-golden.v1.sha256`）而不是停用。
- 连带已退役的旧行为断言（不要当 bug 修）：§218 侧射契约 2 条 · §164 `sticky=90` · §162 `navBreak=0`。

---

## 2. 诊断结论（一句话）

**God-AI 必须在开火决策里引入「本拍转身是否会提交」的判据**：

> 只有当 `_moveDir === p.dir`（已对齐、子弹必然沿瞄准方向飞）**或**本拍请求的转身会被接受时，才允许开火；
> 否则本拍**不射**（等 1..k 拍、转身提交后再射）。

A1 之后的等价表述：**"AI 不能再把 `_moveDir` 当作子弹方向；必须把 `p.dir`（已提交朝向）当子弹方向。"**

---

## 3. 调优方案（候选；**逐项可独立立项，建议按 A→B 顺序**）

> 所有方案都是 **God-AI 行为改动** ⇒ 每个 = 新纪元三件套（新 DECISIONS + ≥60-seed 基线 + golden 更新），
> 且**不得**与引擎改动混在一个 commit。

### 3.1 方案 A：**提交朝向开火门**（最小、推荐先做）

**机制**：在开火决策处加一个纯谓词

```ts
// src/ai/god/ 内新增纯函数（与 Hunt.ts:698-701 的 turnDeferred 同口径）
export function turnWillCommit(w: World, p: Tank, dir: Direction | null): boolean {
  if (dir === null || dir === p.dir) return true          // 已对齐 ⇒ 子弹必然沿 dir
  const cd = w.rules?.turnCooldownMs ?? 0
  if (cd <= 0) return true
  return w.frame * (1000 / 60) - (p.lastTurnMs ?? TURN_SENTINEL_MS) >= cd
}
```

把每个"要开火"的点改成 `self._fire = !onCooldown && shouldFireInDir(...) && turnWillCommit(w, p, self._moveDir)`
（**含** `shared.ts:144`（追击）· `shared.ts:255`（carveFire）· `Hunt.ts:65-69`（detour）·
以及其它 `_fire = ...` 直写点 `src/ai/GodAIInput.ts:1337-1338` 的 `shouldFireInDir` 消费方）。

**预期**：偏弹变"晚 1..k 拍的准弹"。k = 距转身冷却结束的拍数（≤12）。多数场景 1–3 拍即可回头射击。
**风险**：① 若目标快速横移，等到能射时目标已不在线 ⇒ 需要用方案 D 的提前量补偿；
② 减少开火次数可能触发"该射不射"（中弹/被破墙）⇒ DoD 必须看**击杀/命中**而非只看胜率。
**落点**：`src/ai/god/candidates/shared.ts` · `Hunt.ts` · 建议统一走一个 helper，避免 11 个 candidate 各写一遍。

### 3.2 方案 B：**转向优先、下一拍开火**（turn-then-fire）

**机制**：当 AI 判定"需要转向 + 打一个值得的目标"时，**分两拍**表达：本拍只转向（`_moveDir = aimDir`、
`_fire = false`），下一拍（`p.dir === aimDir` 后）再 `_fire = true`。
即把"甩炮"显式拆成"转 → 打"，用 1 拍延迟换确定性命中。

**与方案 A 的关系**：A 是"挡掉偏弹"，B 是"补上被挡掉的那一枪该何时打"。**B 通常需要 A 作为前置**
（否则无法知道"现在能不能打"）。
**预期/风险**：比 A 更贴近旧胜率；代价是**每发需转向的射击晚 1..k 拍**，对快目标仍可能落空 ⇒ 与 D 组合。

### 3.3 方案 C：**利用已提交朝向**（不浪费这一拍）

**机制**：转身被拒 + 想开火时，若**旧朝向**恰好也有可打目标（或可破的墙/砖），就**沿旧朝向打**；
否则按 A 不射。即把 A 的"一律不射"升级为"沿当前朝向做一次机会射击"。
**预期**：回收一部分"被 A 挡掉"的开火机会，不增加偏弹。
**风险**：选目标的逻辑变复杂（要在两个方向各做一次几何判定）；容易与既有 candidate 的"目标承诺"冲突
（记忆里的已知缺陷：God-AI **目标切换不承诺**）⇒ 建议 A 落地并测完再评估 C。

### 3.4 方案 D：**提前量 / 预测射击**（与引擎无关，独立立项）

**机制**：`src/ai/god` 的 `aimDir` 计算引入目标速度外推（lead），使"等转身提交"的期间目标恰好进入弹道。
NN 侧的同类设计见 `plan/nn-capability-upgrade.plan.md §4-B3`（`leadHitD`）。
**预期**：直接补 §3.1 风险①（等到能射时目标已横移）。
**风险**：外推在迷宫/遇墙转向时失真（NN 侧已记同一风险）⇒ 需关闭"目标在窗口内转向"的情形。

### 3.5 方案 E：**只重标基线，不调优**（仅当目标 = 存档/冻结）

**机制**：用 `bun tools/diag/recapture-score-truth.ts` 重采 `TRUTH_SCORES` 并复活 score gate。
**为何不推荐**：那不是"恢复 God-AI"，只是**把退化写进地板**（等于承认 God-AI 从此更弱）；
且旧 truth 是作弊行为上采集的，用户已明确该门无意义。**只在"决定放弃 God-AI 作为参考系"时采用。**

### 3.6 明确**不做**（防后来者跑偏）

1. **不回退 A1**（引擎规则是基础机制，已交付、已记录）。
2. **不延长 `turnCooldownMs` / 不改掩码 / 不给 AI 加"引擎补丁式"补偿**（AGENTS §8 风险表 + 用户口径）。
3. **不复活 `tests/godai-score-gate.test.ts`**。
4. **不动任务侧**（命数 / 敌数 / 关卡 / `max_ticks`）。

---

## 4. 实施顺序与验收（DoD）

### 4.1 顺序

```
方案 A（提交朝向开火门，最小）      ← 一个 commit（God-AI 行为改动 = 新纪元三件套）
  ↓ 出 hard 35×20 + 60-seed 三难度对账
方案 B（转向优先、下一拍开火）      ← 视 A 的缺口决定是否做
  ↓
方案 D（提前量）或 方案 C（利用旧朝向） ← 二选一/按读数决定
  ↓
（若最终决定冻结）方案 E
```

### 4.2 每个方案的 DoD

- [ ] **确定性复现/证伪用例**：新增单测钉住"`turnWillCommit` 为假 ⇒ 本拍不开火 / 为真 ⇒ 沿 `dir` 出膛"（AGENTS §7）。
- [ ] `bun run check` 绿 · `bun run build` 过 · `freeze:check` 用**新** golden 绿。
- [ ] **主判据 = hard 35×20（CRN 同种子，对 A1 后基线）**：胜率 / 算术 score 的**回升幅度**；
      基线 = `tmp/a1-after-hard.json`（58.7% / 0.6104）。
- [ ] **≥60-seed 三难度**（`bun tools/eval/eval-suite.ts --difficulty hard --seeds 60` 等）出报告，
      hard 为主、classic/chaos 作参考（不追 classic/chaos 的最优，只看不结构性倒退）。
- [ ] **证伪 A 的"主因假设"**：若 A 之后胜率**几乎不动**，说明退化**不止**来自"甩炮"——
      ⇒ 转 `docs/god-ai-tuning.progress.md` 的 per-seed tick-diff（`tools/diag/per-seed-diff.ts`）
      逐 seed 找首个分歧 tick，检查还有没有**第二条**依赖 A1 前语义的路径（见 §4.3）。
- [ ] **不产生新的偏弹**：断言"任何玩家子弹的 `dir` == 该拍开火时刻的 `p.dir`"（可用 `Simulation` 事件流审计）。
- [ ] 决策进 `DECISIONS.md`（新纪元条目）+ `docs/god-ai-tuning.progress.md` + memory 一行。

### 4.3 需要重点排查的"第二路径"（若 A 效果不足）

- `GodAIInput.shouldFireInDir(pcx,pcy,dir,allowWallFire)`（`src/ai/GodAIInput.ts:1337-1338`）的**所有**消费方：
  `dir` 是否总是 == `p.dir`？（若某处传 `_moveDir` 而 `_moveDir !== p.dir`，就是同一类偏弹。）
- `shouldFireBreakThroughImpl`（破墙射击）—— 破墙不依赖"打中敌人"，偏向不影响正确性，但**会吃掉一发**。
- `Dodge` / `Survive` 分支的开火（若它们在转向中开火）。
- 敌方 AI 侧（`src/game/SimulationEnemies.ts`）—— A1 同样改了**敌人**的射击方向：
  敌人也失去了甩炮 ⇒ **它们对玩家的威胁格局也变了**（可能部分抵消玩家的损失）。
  ⚠ 报告要**分栏归因**：玩家侧损失 vs 敌人侧损失（用 `--compare` 或 per-seed tick-diff 观察）。

---

## 5. 风险与回滚

| 风险 | 影响 | 处置 |
|---|---|---|
| 方案 A/B 让 God-AI **少开火** ⇒ 击杀下降、被破墙 | 胜率不升反降 | DoD 同时看 **kills/千 · hit/千 · dmg/千**（不是只看 win）；用 `--compare` 配对 |
| "等转身提交"引入 **1..k 拍延迟** ⇒ 快目标逃掉 | 部分回收不了 | 组合方案 D（提前量） |
| 迷宫/冰面里转向被频繁拒 | 开火窗口更少 | 分关读数（§1.4 的逐关表），重点看 Riverbed/Ice Palace/Crossroads |
| 误以为"已修"而把退化记为"新常态" | 长期丢掉 God-AI 参考系 | §4.2 的"证伪主因假设"必做 |
| 与其它在飞改动混 commit | 无法归因 | 每方案独立 commit；God-AI 改动 = 新纪元三件套 |

**回滚**：每个方案一个 commit ⇒ 直接 revert 回到 A1 后基线（`tmp/a1-after-hard.json` 的对账口径）。

---

## 6. 锚点速查

```
src/game/SimulationCombat.ts:282-292     A1：tryFire 内新增一次 applyTurnCooldown
src/game/SimulationCombat.ts:116-139     applyTurnCooldown（冷却内 dir 回退 + halt）
src/config/rules.ts:257,341              turnCooldownMs = 200ms ≈ 12 拍
src/game/Simulation.ts:169-265           九步顺序（③玩家 / ④敌AI / ⑦applyTurnCooldown）
src/ai/god/think.ts:250-266              ctx.onCooldown = **开火**冷却（不含转身冷却）
src/ai/god/candidates/shared.ts:143-144  追击：_moveDir + _fire（子弹沿 _moveDir 的假设）
src/ai/god/candidates/shared.ts:246-255  carveFire 同类
src/ai/god/candidates/Hunt.ts:65-69      detour 同类（!onCooldown 只管开火冷却）
src/ai/god/candidates/Hunt.ts:697-701    已有的 turnDeferred 口径（方案 A 直接复用）
src/ai/god/CoveragePlanner.ts:224        转身代价 msToTicks(turnCooldownMs)+1
src/ai/GodAIInput.ts:1337-1338           shouldFireInDir —— 排查第二路径的入口
src/game/SimulationEnemies.ts            敌方 AI（A1 同样改变了敌人射击方向）
tools/eval/eval-suite.ts                 --difficulty hard --seeds 60 --compare a b（CRN 配对）
tools/diag/per-seed-diff.ts              per-seed/tick 首个分歧定位
tools/diag/recapture-score-truth.ts      仅方案 E 用（重采 TRUTH_SCORES）
tests/godai-score-gate.test.ts           已退役（test.skip）—— 本 plan 不复活
tools/det-golden.v1.sha256               freeze golden（每个 God-AI 改动都要更新）
tmp/a1-before-hard.json / a1-after-hard.json / a1-hard-diff.ts   本文实测语料与复算脚本
```

---

## 7. 附录：hard 35×20 逐关表（A1 前 → A1 后，CRN 同种子）

| # | 关 | bScore | bWin | aScore | aWin | dWin |
|---|---|---|---|---|---|---|
| 1 | Outpost | 0.866 | 95% | 0.689 | 70% | −25pp |
| 2 | Waterways | 0.774 | 80% | 0.717 | 75% | −5pp |
| 3 | Steel Fortress | 0.679 | 65% | 0.598 | 55% | −10pp |
| 4 | Crossfire | 0.762 | 80% | 0.615 | 60% | −20pp |
| 5 | Maze | 0.681 | 70% | 0.472 | 40% | −30pp |
| 6 | Brickworks | 0.730 | 75% | 0.677 | 70% | −5pp |
| 7 | Iron Curtain | 0.789 | 80% | 0.735 | 75% | −5pp |
| 8 | **Riverbed** | 0.577 | 55% | 0.251 | 10% | **−45pp** |
| 9 | Twin Towers | 0.889 | 95% | 0.763 | 80% | −15pp |
| 10 | Gauntlet | 0.798 | 85% | 0.662 | 65% | −20pp |
| 11 | Fortress | 0.840 | 90% | 0.718 | 75% | −15pp |
| 12 | Lattice | 0.758 | 80% | 0.603 | 60% | −20pp |
| 13 | Bunker Hill | 0.779 | 80% | 0.621 | 60% | −20pp |
| 14 | Steel Web | 0.689 | 70% | 0.617 | 60% | −10pp |
| 15 | Citadel | 0.784 | 80% | 0.698 | 70% | −10pp |
| 16 | **Crossroads** | 0.733 | 75% | 0.454 | 35% | **−40pp** |
| 17 | Twin Spires | 0.772 | 80% | 0.680 | 70% | −10pp |
| 18 | Gridlock | 0.922 | 100% | 0.835 | 90% | −10pp |
| 19 | Frozen Field | 0.767 | 80% | 0.518 | 45% | −35pp |
| 20 | Bastion | 0.515 | 45% | 0.528 | 45% | +0pp |
| 21 | Checkers | 0.734 | 75% | 0.523 | 45% | −30pp |
| 22 | Oasis | 0.871 | 95% | 0.651 | 65% | −30pp |
| 23 | Ramparts | 0.949 | 100% | 0.885 | 95% | −5pp |
| 24 | Labyrinth | 0.604 | 55% | 0.522 | 45% | −10pp |
| 25 | Quarry | 0.795 | 85% | 0.747 | 80% | −5pp |
| 26 | Ice Palace | 0.749 | 75% | 0.522 | 45% | −30pp |
| 27 | Brick Maze | 0.751 | 75% | 0.588 | 55% | −20pp |
| 28 | Thicket | 0.705 | 70% | 0.455 | 35% | −35pp |
| 29 | Spider | 0.826 | 85% | 0.741 | 75% | −10pp |
| 30 | Concentric | 0.809 | 85% | 0.598 | 55% | −30pp |
| 31 | Eagle Nest | 0.790 | 85% | 0.531 | 50% | −35pp |
| 32 | Star Fort | 0.706 | 70% | 0.620 | 60% | −10pp |
| 33 | Diamond | 0.876 | 95% | 0.631 | 65% | −30pp |
| 34 | Battlement | 0.337 | 20% | 0.279 | 15% | −5pp |
| 35 | Final Redoubt | 0.834 | 90% | 0.622 | 60% | −30pp |

（bScore/aScore = 该关 20 seed 的 v7 `score` 算术均值；bWin/aWin = 该关 20 seed 的过关率。
复算：`bun tmp/a1-hard-diff.ts`。⚠ 这里的算术均值与 §1.4 表的 SUITE（power-mean/CVaR 聚合）不同口径，勿混用。）
