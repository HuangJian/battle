# 躺赢模式实现计划（Lie-Back-Win Mode Plan）

> **状态**：已拍板（7 个开放问题全部确认，见 §7；审查追加 §7.1 新增 3 项拍板；待补 `DECISIONS.md` 即可按 M1 开工）
> **作者**：Coding Agent
> **依据**：`AGENTS.md` §2（架构不变量）、MANIFEST §13（三道门）、`DECISIONS.md` #36（God AI 覆盖表协议）、#47（GodAI RNG 拆分）、`plan/Snapshot-Management-Framework.md`
> **结论先行**：**可行，且成本比直觉低**。God AI 大脑（`src/ai/god/*` + `GodAIInput`）已存在且经 P4 调优（35 关均值 81.9%）；RNG 已拆分；`tank.player2` 精灵已画好未用；replay/snapshot 均有版本字段可扩展。真正的工程量在于**拆掉「单玩家假设」**——`world.player` 单数引用遍布仿真、感知、道具、HUD、序列化各层。

---

## 0. 一句话目标

> 打开躺赢模式，God Player 作为二号玩家与你并肩作战；你可以挂机看它carry，也可以随时亲自下场——它还会把多余的命分给你。

## 1. 三道门（MANIFEST §13）

| 门 | 判定 | 理由 |
|---|---|---|
| **更愉快** | ✅ | 「玩不过的关让 AI 带飞」+「挂机观赏 AI 表演」是两种全新的五分钟；命共享让合作有温度 |
| **架构简单** | ✅（有纪律前提） | 不引入新系统：God Player = 现成 GodAIInput 接到第二个输入槽；全部改动是**把单数泛化为「至多两个」**，不是重写。前提：所有协同行为用 coop 上下文门控，单人路径字节级不变 |
| **忠于原作** | ✅→更强 | FC 原作本就是双人合作（P2 银色坦克、对称出生点）。躺赢模式复活了 P2 位——只是把手柄递给了 God AI |

## 2. 现状盘点（可白嫖的资产）

| 资产 | 位置 | 状态 |
|---|---|---|
| God AI 大脑 | `src/ai/god/*`、`src/ai/GodAIInput.ts` | ✅ P4 调优完毕，`InputLike` 接口，只读 World，不回写 |
| 独立 RNG | `GodAIInput` 构造第三参 `rng?: RNG`（DECISIONS #47） | ✅ 已拆分，浏览器接入不污染 `world.rng` |
| 逐关参数覆盖表 | `src/ai/godai-stage-overrides.ts` | ✅ 直接复用（S6/S18/S25/S26 已调） |
| P2 坦克精灵 | `assets/sprites/player2.svg` → 注册名 `tank.player2` | ✅ 已画好，游戏代码从未引用 |
| 快照泛化 | `WorldSnapshot` 里 `player` 就是普通 `Tank` 槽位 | ✅ 加一个 `player2` 槽位即可 |
| replay 版本字段 | `FRAME_SCHEMA_VERSION = 0x01`（帧流首字节）+ envelope `schemaVersion` | ✅ 可升 0x02 且保持 v1 兼容 |
| Control Center | `src/presentation/ui/ControlCenter.ts` GAMEPLAY 区已有「Key Bindings」按钮 | ✅ 正下方加 toggle 即可 |

## 3. 核心可行性问题与解法

### 3.1 单玩家假设是最大的墙（中心改动）

现状（探查结论，行号为当前代码）：
- `World.player: Tank | null`（World.ts:66）唯一玩家槽；`lives`/`playerLevel`/`playerSpawnPoint` 全部单数。
- `Simulation.updatePlayer()`（Simulation.ts:363）硬编码 `w.player` + `this.input`（构造时注入的唯一 `InputLike`）。
- `applyPowerUp()`（Simulation.ts:1791）硬编码作用于 `w.player`。
- 敌人感知 `perception.ts:105` / `scanAhead` 只认 `world.player` —— **第二个玩家坦克对敌人隐形**。
- HUD 单命条（UIManager.ts:707），快照单 `player` 槽，replay 单输入流（1 字节/tick）。

**解法：最小平行槽位，不做玩家数组化**。
- `World` 新增：`player2: Tank | null`、`lives2: number`、`playerLevel2: number`、`coop: boolean`、`player2SpawnPoint`。不引入 `players[]` 重构（改动面爆炸，违背门二）。
- `Simulation` 泛化 `updatePlayer(tank, input)`，每 tick 依次驱动 `(player, input)` 与 `(player2, input2)`；`input2: InputLike | null` 由 Game 注入。
- `applyPowerUp(type, collector)` 按**拾取者**结算：star/shield/repair → 拾取坦克；`tank`(+1命) → 拾取者的命池（`lives` 或 `lives2`）；freeze/bomb/emp/超级道具 → 全局（不变）。
- 敌人感知：`perception`/`scanAhead` 遍历 `[player, player2]` 取最近者为 `player` 目标——**「针对度一致」由此天然满足**（敌人目标评分逻辑不改，只是候选从 1 个变 2 个）。详见 §3.8 感知层重构。
- classic 三星星盾（`bulletHitsTank` 的 spendStarShield）泛化为按坦克自身 level 判定，God Player 同享。
- `isPlayer`（= `allegiance==='player'`）对 player2 同为 true → 冻结豁免、子弹阵营过滤、音效事件自动正确，**零改动**。God 的子弹 `isPlayer=true` → GodAI 的 ThreatAssessor 天然不把人类子弹当威胁。

### 3.2 命共享规则（对称、事件驱动）

规则（按需求原文，双向对称）：

```
某方 out（lives==0 且坦克不在场）时，每 tick 检查：
  若对方 lives > 2 → 对方 lives--，本方立即重生（带 RESPAWN_SHIELD_MS）
GAME OVER 条件收窄为：双方均 out 且互相无法分享（或基地被毁，不变）
```

- 覆盖三个用例：God 3命→玩家立即复活；God 1命→玩家等待、游戏继续（God 独走）；God 后续吃到 `tank` 道具凑到 >2 → 玩家当场复活。
- 实现位置：`checkConditions()`（Simulation.ts:2034）——现有「player 死亡 → lives-- → 重生或 gameover」分支拆成 per-player 版本 + 末尾共享检查。死亡掉星（重置为 `playerStartLevel`）双方规则一致。
- 边界：分享检查在**每 tick**做（不只在死亡瞬间），因为「后继拾取道具触发复活」是异步的。

### 3.3 God AI 接管 P2 —— 不许动 P4 基线（红线）

`GodAIInput.think()` 硬编码 `w.player`（GodAIInput.ts:543-554, 1010）。改法：

- 构造参数加 `controlledTank?: (w: World) => Tank | null`，**默认 `w => w.player`** → 现有 tools/tests 路径字节级不变，`godai-split-parity` 与回归门禁**必须原样通过，禁止 relock**。
- 协同行为（把人类坦克视为友军障碍、路径规划绕开人类、不向人类方向瞄准穿射等）全部包在 `coopMode` 标志内，仿真工具链不开启。
- God 的 RNG：`new RNG((world.seed ^ 0x9e3779b9) >>> 0)`（沿用 #47 惯例）。RNG/内部规划状态**不进 World**——见 §3.6 架构合规论证。
- 逐关覆盖表 `applyStageOverrides` 照常生效。
- God 不触发超级道具键（`wasItemPressed` 恒 false）——超级库存是全局的，花不花由人类决定（→ Q2）。

### 3.4 自动射击（挂机开火）

**纯输入层装饰器**，不进 Simulation：

```
AutoFireInput implements InputLike （包装人类 Input）
  - 每关开始 → armed = true，isFiring() 恒 true
  - 侦测到真实开火键按下 → armed = false，之后透传真实输入
  - 下一关加载 → 重新 armed
```

- 放在输入层的关键收益：replay 记录的是**装饰后的有效帧** → 回放零特判、快照恢复零特判（恢复后重新 armed，玩家按一下开火即停，代价可忽略）。
- 移动仍透传人类按键——挂机时不动，想动随时动。

### 3.5 中途启停（暂停切换）

- 现状：Control Center 按钮在游玩中点击会自动暂停（ControlCenter.ts:136-139），但设置项多为 menu-only。躺赢 toggle 需同时在 **menu 态与 paused 态**可用。
- 合法作者路径：toggle 不直接改 World —— Game 调 `simulation.requestCoopToggle(on)`，Simulation 在**下一 tick 开头**结算（暂停中无 tick，恢复时生效）：
  - **启用**：`coop=true`；God 以「本局初始状态」入场——`lives2 = difficulty.startLives`、`playerLevel2 = difficulty.playerStartLevel`（classic 即 3 命 0 星）、在 P2 出生点重生（带出生盾）。与 difficulty 天然正交：只读当前 `world.difficulty`，四种难度通吃。**（§7.1 Q8 拍板：中途加入固定 0 星，不随关卡缩放）**
  - **停用（Q5 拍板：直接作废，不捐给玩家）**：God 坦克移除（小型消失特效，非爆炸）、`lives2` **直接作废（不再把 >2 的命分给玩家）**、`coop=false`。再次启用 = 重新按初始状态入场（符合需求「加入状态为游戏开始时状态」）。
- P2 出生点：经典位 `(col 16, row 24)`（与 P1 col 8 沿基地对称）；关卡自定义 `playerSpawn` 时取水平镜像 `col' = 24 - col`，被占用则沿同行找最近空位（→ Q3）。

### 3.6 快照 & 回放无缝支持

**快照**（`snapshot/types.ts` + `WorldSerializer.ts`）：
- `WorldSnapshot` 增加 `player2`、`lives2`、`playerLevel2`、`coop`、`player2SpawnPoint?`、`score2`。旧快照缺字段 → 默认 `null/0/false/0`，向后兼容，无需版本断裂。
- RecoverySystem 的 rewind/恢复自动获得 God 坦克（就是多克隆一个 Tank）。恢复后 GodAIInput 从新 World 状态重新思考——与人类恢复后重新操作同构。

**回放**（帧 schema v1 → v2）：
- v1：`[0x01][每 tick 1 字节]`（bit0-3 方向，bit4 fire，bit5 guard，bit6 frenzy）。
- v2：`[0x02][flags: bit0=hasP2][每 tick 1 或 2 字节]`。hasP2 时每 tick 为 `[p1字节][p2字节]`。
- **God 帧照录不重演**：录制时把 GodAIInput 每 tick 的输出当作 P2 输入帧录下；回放用双 `ReplayInput` 流喂 `(input, input2)`，**不重新运行 AI** → 后续 AI 调参/改代码不会让历史录像失真（与 God-AI-Replay 一役的教训一致）。
- 录制期始终双缓冲，`finalize()` 时若本局从未开过 coop 则落成 v1 单流（旧尺寸不变）；开过则落 v2。解析器同时支持 v1/v2；sim 工具链继续产 v1。
- 中途启停在帧流里天然成立：God 不在场的 tick 其字节为 0x00（无输入）。
- `ReplayMetadata` 加 `coop?: boolean`，Replay Browser 列表加「躺赢」徽标。

**「无隐藏状态」合规论证**（须录入 DECISIONS.md）：GodAIInput 的内部规划/RNG **属输入层**，与人类的键盘、肌肉记忆同级——它从不回写 World，其对局面的全部影响都以输入帧形式被录制。回放依赖录制帧而非 AI 内部状态，故确定性承诺（§2.3）完好。

### 3.8 God AI / 感知层重构方案（审查追加：P1–P3 三个严重问题）

> **来源**：对代码库的深度审查发现了三个原计划未覆盖的严重问题——`GodAIInput.think()` 在玩家死亡时整个 bail out、威胁评估只保护 `world.player`、敌人感知只认一个玩家。以下逐一给出解法。

#### P1: `think()` 生存依赖——God 坦克在人类死亡时冻结

**现状**：`GodAIInput.think()` 开头读 `const p = w.player`，若 `!p || !p.alive` 则 `_moveDir=null, _fire=false` 并 return。God 控制的是 `player2`，但 `think()` 以 `world.player` 的存活为前提——人类死亡期间 God 坦克完全不动。

**修复**：
1. `GodAIInput` 新增字段 `controlledTank: (w: World) => Tank | null`，默认 `w => w.player`。
2. `think()` 开头改为 `const p = this.controlledTank(w)`，替代 `const p = w.player`。若 `p` 为 null 才 bail out。
3. `coopMode` 构造标志：co-op 时传入 `w => w.player2`；单人模式不传（默认 P1）。
4. `Navigator.playerCell()` 也改为读 `self.controlledTank(self.world)`。

**parity 影响**：`controlledTank` 默认 `w => w.player`，现有 tools/tests 路径**字节级不变**。M2 的 DoD 门禁要求 `godai-split-parity` 原样通过。

#### P2: 威胁评估保护的是 `world.player` 而非受控坦克

**现状**：`isBaseUnderThreat()`（GodAIInput.ts）检查**人类玩家**到基地的距离来决定"是否该回防"。`findMostDangerousBullet()`/`dodgeDirection()` 用 `pcx/pcy` 参数（传入值），但 `isBaseUnderThreat` 内部硬编码读 `world.player`。

**修复**：
1. `isBaseUnderThreat()` 中 `const p = this.world.player` 改为 `const p = this.controlledTank(this.world)`。
2. `hasFastThreatNearBase()` 同理。
3. `dodgeDirection(bullet, pcx, pcy)` 的参数已经是传入值——确保 `think()` 调用时传的是 God 坦克的坐标（P1 修复后自然满足）。
4. `findMostDangerousBullet()` 和 `baseBulletInterceptCell()` 同理改为使用 controlledTank 坐标。

**注意**：God 保护的是**自己控制的坦克**和**基地**，不是人类坦克。这是正确的——God AI 的设计目标是"带飞"，它需要先活下来才能输出。

#### P3: 感知层敌人只认 `world.player`——player2 对敌人隐形

**现状**：
- `perceive()`（perception.ts:105）：`const player = world.player`，只检测一个玩家。
- `scanAhead()`（perception.ts:166）：只检测 `world.player` 是否在射击线上。
- 结果：enemy AI 的目标评分、闪避、射击决策全部**只考虑一个人类玩家**。God 坦克对敌人完全隐形——敌人不会绕开它、不会优先攻击它、不会因它在射击线上而调整方向。

**修复**：
1. `perceive()` 的 `player` 变量改为 `[world.player, world.player2]` 中最近的一个（遍历取 min manhattan distance），用于 `hasPlayer`/`playerX`/`playerY`。当最近玩家在 tick 间切换时（例如人类后退、God 前进），observation 会自然切换——这不会导致振荡，因为 goal evaluation 的 scoring 是 distance-weighted 的，切换目标只是改变了分数权重，不会反转方向。
2. `scanAhead()` 增加 player2 检测——在现有 player AABB 检查之后加一个 player2 检查。两个玩家都在射击线上时，返回第一个命中的 `'player'`（敌人只需知道"有玩家在那里"来决定是否开火，不需要区分是谁）。
3. `World.allTanks` getter 中加入 `player2`（当前只有 `player` + `allies` + `tanks`）。这样敌人的碰撞感知、移动避障**自然包含 player2**，无需额外改动。
4. `perceive()` 中 `if (o.isPlayer) continue` 保持不变——player2 的 `isPlayer=true`，所以敌人不会把 player2 当作"队友"（这与 player1 行为一致）。

**影响分析**：
- 单人模式：`player2 == null`，所有遍历退化为只看 player1，路径完全等价。
- co-op 模式：敌人现在会看到两个玩家，行为更丰富（会绕开 God 坦克、会在 God 坦克的射击线上闪避等）。这是期望行为。
- **已知 trade-off**：敌人加入 player2 到碰撞避障后，可能会在接近 God 坦克时绕路而非正面交火。这是"敌人更聪明"的副产品——God AI 的 T2a（stop-and-aim）和直接移动逻辑不受影响（God 主动开火不依赖敌人行为），整体效果是 co-op 战斗更动态。
- 性能：per-tick 多 1 次 AABB（scanAhead）+ 1 次 manhattan（perceive），在微秒级 sim 预算内可忽略。

#### God AI coopMode 行为门控（§3.3 补充）

God AI 在 co-op 模式下需要知道人类坦克是友军。方案：

1. `coopMode: boolean` 构造标志，默认 `false`。
2. `coopMode=true` 时：
   - `canMoveOrBreak`/`canMoveDir`：人类坦克视为障碍（已有 `isPlayer` 检查——人类坦克 `isPlayer=true`，God 的子弹不会击中人类，但 God 坦克的**移动**仍会与人类坦克碰撞——这是正确行为，避免重叠）。
   - `shouldFireInDir`：不修改。God 坦克的子弹 `allegiance='player'`，人类坦克也是 `allegiance='player'`，子弹不会伤害友军（`bulletHitsTank` 的阵营过滤已处理）。
   - 路径规划：`selectTarget` 的 defense/attack 逻辑不改。God 仍按自己的策略行事，只是"看到"人类坦克作为碰撞体。
3. `coopMode=false` 时：字节级不变（parity 红线）。

#### `applyPowerUp` 得分归属补充

道具拾取**不产生分数**——经典 FC 行为：只有击杀携带者才得分。`score`/`score2` 的归属仅通过击杀事件（`tank_destroyed` 的 `by` 字段）结算：谁的子弹击毁了敌人，谁的分数增加。道具效果（star/shield/repair/tank 等）按拾取者结算，不涉及分数。

#### `spawnPlayer2` 交付物补充

M1 交付物新增：`World.spawnPlayer2()` 方法——读 `world.player2SpawnPoint`，写 `world.player2`，与 `spawnPlayer()` 对称。`checkConditions` 中 player2 死亡时调用 `spawnPlayer2()` 重生（而非 `spawnPlayer()`）。

### 3.7 UI / 呈现

> **注：§3.8 补充了 God AI / 感知层的详细重构方案（P1–P3 三个严重问题），见上方。**

  - **Control Center**：GAMEPLAY 区「Key Bindings」按钮正下方加 `cc-btn` toggle「躺赢模式 LIE-BACK WIN」，样式随 Performance Mode 的 on/off 态（`setPerfModeState` 同款模式）。**状态不持久化进 settings（Q6 拍板：每局想用随时开，toggle 仅当前进程有效，重开页面需手动再开）**。
  - **HUD**：现有命条下方加第二行金色 `♥ × lives2` + `GOD` 标签（仅 coop 时显示）；得分区并排显示 `P1: score` 与 `GOD: score2`（Q1 分账）。`coop===false` 时 HUD 与普通单人对局完全一致。
- **渲染**：`GameRenderer` 玩家分支按 `tank === world.player2` 选 `tank.player2` 精灵（银/绿配色现成）。
- **音效**：双坦克持续开火会翻倍射击音——God 的 shoot 音量衰减 50%（呈现层细节，不影响 World）。

## 4. 明确不做（负空间）

- ❌ 玩家数组化重构（`players: Tank[]`）——本模式至多 2 人，平行槽位够用且改动面可控。
- ❌ 人类二号玩家（键盘双打）——本计划只接 God AI；但 M1 的泛化天然为将来留门。
- ❌ God Player 独立分数面板/独立高分榜（→ Q1、Q5 拍板前默认共享）。
- ❌ God AI 为协同做行为大改（让位、抢救、押运）——首版只要求「别撞队友、别误伤」，高级协同留待玩过再说（三道门第一门用五分钟检验）。

## 5. 里程碑

> 每个里程碑独立可验收，全绿标准一致：`tsc --noEmit`、`oxlint` 0 警告、全量 `bun test` 通过、**parity 与回归门禁不 relock**。

### M1 — 双玩家世界基础（纯仿真层，无 UI）
- World 平行槽位五件套；`updatePlayer(tank, input)` 泛化 + `input2` 槽；`applyPowerUp` 拾取者结算；感知层双目标；per-player 死亡/重生/掉星；命共享 + 新 gameover 条件；classic 星盾泛化。
- **DoD**：新增单测覆盖命共享三用例、道具归属、双目标感知、gameover 矩阵（2×2 命池状态）、`allTanks` 含 player2（`coop=true` 时 player2 出现在 allTanks 中且碰撞/移动路径正确）；`coop=false` 时全部现有测试**不改一字**通过。

### M2 — God Player 大脑接入
- `GodAIInput` 加 `controlledTank` 访问器（默认 P1，parity 不变）+ coopMode 行为门控（人类坦克视为障碍/友军）；独立 RNG 播种；覆盖表生效；不触发超级道具。
- （可选）`tools/optimize/level-sim.ts --coop`：无头跑「God 带飞」局，量化躺赢胜率，验证「挂机可赢」成立。
- **DoD**：`godai-split-parity` 与回归门禁原样通过；coop 无头仿真 classic 全 35 关挂机（人类零输入）胜率 ≥ 有意义的下限（跑完基线后定数）。

### M3 — 游戏接线 + UI（玩家可见的躺赢模式）
- Game 注入 `input2 = GodAIInput`；`requestCoopToggle` 暂停/菜单双入口；AutoFireInput 装饰器（每关重臂）；Control Center toggle + 持久化；HUD 第二命条；`tank.player2` 精灵渲染；God 射击音衰减。
- **DoD**：手测清单——菜单开局带 God；中途暂停开/关/再开（状态重置）；四种难度均可用；挂机 5 分钟不碰键盘可推进；按开火键接管射击。

### M4 — 快照支持
- `WorldSnapshot` 扩字段 + `WorldSerializer` 读写；旧快照兼容加载；rewind/恢复/缩略图路径验证。
- **DoD**：coop 快照往返单测（含 God 在场/阵亡/未启用三态）；旧格式快照加载不炸且 coop=false。

### M5 — 回放 schema v2
- 双流录制（God 帧照录）、finalize 降级 v1、双 `ReplayInput` 回放、v1 兼容解析、Replay Browser coop 徽标 + 导入导出。
- **DoD**：v2 往返单测；v1 存量 replay 回放不受影响；录一局中途开关 coop 的浏览器局，导出→导入→回放帧级一致。

## 6. 风险登记

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | GodAIInput 改动导致 P4 调优基线漂移 | **高** | `controlledTank` 默认值保守；协同行为 coopMode 门控；parity/门禁作为 M2 硬闸，禁止 relock |
| R2 | `world.player` 单数引用遗漏（呈现/音频/统计约 30+ 处） | 中 | M1 前做一次 `world.player`/`isPlayer` 全量 audit 清单（AGENTS §4 Step 2），逐条标注「需泛化/无需动」 |
| R3 | 感知层双目标改动影响单人仿真结果 | 中 | `player2==null` 时候选列表退化为 `[player]`，路径完全等价；回归门禁验证 |
| R4 | 帧 schema v2 引入回放兼容 bug | 中 | 双向往返测试 + v1 存量录像回归；version 字节严格校验 |
| R5 | 浏览器每 tick 跑 God think() 的性能 | 低 | 无头仿真单线程 ~280× 实时，单实例 tick 成本可忽略；perf.html 验证兜底 |
| R6 | 双人局 gameover/生命边界组合爆炸 | 中 | M1 用状态矩阵单测穷举（人类 out × God out × 可分享 × 基地） |

## 7. 开放问题拍板结果（7/7 已确认）

| # | 问题 | 拍板结果 | 对计划的影响 |
|---|---|---|---|
| **Q1** | 得分：共享还是分账？ | **P1/P2 各自计算**（`score` 归人类，`score2` 归 God） | §3.1 加击杀归属结算；HUD/快照/回放增加 `score2`（§3.6、§3.7） |
| **Q2** | 超级道具库存归属 | **入共享库存，只有人类能花**（与建议一致） | §3.3 设计不变 |
| **Q3** | P2 出生点 | **经典 (16,24)；自定义关水平镜像 `col'=24-col`**（与建议一致） | §3.5 设计不变 |
| **Q4** | 高分榜写入 | **躺赢局不写 highScore**（与建议一致） | highScore 写入门控 `coop===false`（§3.1、§3.7） |
| **Q5** | 中途停用 God 命 | **直接作废，不捐给玩家** | §3.5 停用逻辑去掉结算分支 |
| **Q6** | toggle 持久化 | **不持久化 settings，想用随时开** | §3.7 去掉 localStorage 记忆 |
| **Q7** | 自动射击再武装 | **本关内停止后不能重开**（与建议一致，仅下一关重臂） | §3.4 设计不变 |

> 全部采纳「保守 / 零改动优先」原则：分账保留竞争语义、命作废避免结算复杂度、不持久化降低意外。下一步把 Q1–Q8 结果 + 输入层 AI「无隐藏状态」合规论证补进 `DECISIONS.md`，随后按 M1 开工。

### 7.1 审查追加拍板（2026-07-30）

| # | 问题 | 拍板结果 | 对计划的影响 |
|---|---|---|---|
| **Q8** | 中途加入时 God 坦克初始强度 | **固定 `difficulty.playerStartLevel`**（classic=0, relax=1, hard=0, chaos=0），不随关卡缩放。理由：保持简单，God 靠 AI 技术弥补低星劣势；动态缩放增加配置复杂度，违背"简单优先"原则。 | §3.5 启用逻辑不变；DoD 新增：God 按 difficulty 起始星入场仍能在经典难度下通关。 |
| **Q9** | 狂暴宣泄（F6）期间 God 坦克行为 | **God 不受影响**，继续独立作战。理由：狂暴宣泄是人类专属的全屏暴走，God AI 有自己的作战节奏，被锁住反而降低体验。实现：`frenzyTimer`/`frenzyShotsLeft`/`frenzyDir`/`frenzyInterval`/`frenzyLastFire` 五个字段从 World 下沉到 `Tank`（仅人类坦克使用）。`updateFrenzy(p)` 改为检查 `p.frenzyTimer` 而非 `w.frenzyTimer`；God 坦克的这些字段恒为 0，自然跳过。 | §3.1 新增：`Tank` 加 `frenzyTimer?`/`frenzyShotsLeft?`/`frenzyDir?`/`frenzyInterval?`/`frenzyLastFire?` 字段；`updateFrenzy`/`activateFrenzy` 改为 per-tank 操作。§3.5 暂停切换中 God 坦克不受人类 frenzy 影响。 |
| **Q10** | 回放 v2 格式的 flags 时机 | **flags 在录制开始时固定**（`hasP2` = 录制首帧时 coop 状态），整局不变。中途开关 co-op 在帧流中表现为 God 帧的 0x00/有效切换，但 flags 字节数不变。理由：可变 flags 导致解析器无法确定每 tick 字节数，增加复杂度。 | §3.6 回放格式不变，补充 flags 时机说明。 |

## 8. 交付物清单（预估触碰面）

```
src/game/World.ts            player2/lives2/playerLevel2/coop/player2SpawnPoint/score2 + spawnPlayer2() + allTanks getter 加 player2
src/game/Simulation.ts       updatePlayer 泛化、input2、applyPowerUp(collector)、命共享、gameover、星盾泛化、requestCoopToggle、击杀得分归属（score/score2）+ frenzy per-tank 迁移
src/types.ts                 Tank 加 frenzyTimer/frenzyShotsLeft/frenzyDir/frenzyInterval/frenzyLastFire 可选字段（§7.1 Q9）
src/ai/perception.ts         双玩家候选（详见 §3.8）
src/ai/GodAIInput.ts         controlledTank + coopMode 门控 + think() 生存依赖修复（详见 §3.8，parity 红线）
src/ai/god/ThreatAssessor.ts  威胁评估保护 controlledTank 而非硬编码 world.player（§3.8 P2）
src/ai/god/Navigator.ts       playerCell() 读 controlledTank（§3.8 P1）
src/game/Game.ts             input2 接线、AutoFireInput、toggle 入口（不持久化 settings）；highScore 写入门控 coop===false
src/game/Input.ts            （不动；AutoFireInput 为新文件或同文件新类）
src/presentation/…           ControlCenter toggle、HUD 第二命条、GameRenderer player2 精灵、音量衰减
src/snapshot/*               WorldSnapshot 字段 + 序列化
src/replay/*                 schema v2、双流录制/回放、metadata.coop
tests/                       coop-lives-share / coop-powerups / coop-gameover-matrix / coop-snapshot / replay-v2 / autofire
decisions.md                 新增：输入层 AI 的「无隐藏状态」合规论证 + Q1-Q10 拍板结果
```
