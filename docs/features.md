# Battle City Web — 功能全景

> 三十年前，我在红白机前握着方块手柄，守着那只像素老鹰打到天亮。
> 三十年后，这个项目把那五分钟的心跳装进了浏览器——不需要卡带，不需要吹灰，
> 打开页面，按下 Enter，坦克引擎就轰起来了。
>
> 这不是情怀复刻品，这是一台为未来十年造的战争机器。
> 本文讲清楚它有什么功能、以及每个功能是怎么造出来的。

**一句话使命：打开浏览器，玩五分钟，带着微笑离开。**（MANIFEST §1）

---

## 1. 你能玩到什么 —— 经典玩法，一格不差

### 1.1 35 个正版关卡

不是"致敬"，是**原汁原味的 FC 版 35 关布局**。原始数据是 13×13 的数字地块码（含半砖/四分之一砖的签名式残缺墙），由 `src/config/stages.ts` 的解码器无损展开成引擎原生的 26×26 字符网格——每个字符对应一个 16px 子格。你在第 5 关记住的那堵墙，在这里分毫不差。

- 数据：`src/config/stageData.ts`（`LEVELS` 35 张网格 + `ENEMY_FORCES` 出兵序列）
- 敌人出场顺序同样取自正版数据（a/b/c/d → basic/fast/power/armor）
- 加一关 = 追加一张网格。引擎零改动。

### 1.2 战场规则

| 规则 | 数值 | 出处 |
|---|---|---|
| 战场 | 26×26 子格，416×416 px | `constants.ts` |
| 坦克 | 32×32（2×2 子格） | 同上 |
| 每关敌军 | 20 辆，同屏最多 4 辆 | `ENEMIES_PER_STAGE` / `MAX_ENEMIES_ALIVE` |
| 奖励坦克 | 每第 4 辆携带道具 | `World.loadStage` |
| 基地 | 老鹰中弹一发，全场结束 | DECISIONS §10 |
| 出生保护 | 2 秒无敌 | `SPAWN_PROTECTION_MS` |

地形七种：空地、砖（可打碎，**按 16px 子格精确破坏**）、钢（只有穿甲弹能啃）、水（挡车不挡弹）、森林（藏车）、冰、基地。砖墙被一发发凿穿的手感——就是当年那个手感。

### 1.3 道具系统（15 种）

经典六件套：星星（全维升级）、炸弹（全屏清敌）、护盾、冰冻（8 秒定身）、1UP 坦克、头盔（短护盾）。奖励坦克掉落，20 秒不捡就消失。星星是**全维成长**：每颗星把玩家六项能力一起 +10（见 §3）。死了？星星全掉，回到起点重新挣——经典规则，绝不惯着你。

扩展八件：船（水地形通行）、栅栏（临时钢墙）；超级道具走**背包累积制**（DECISIONS §31）——天降神兵（召唤基地守卫）、狂暴宣泄（F6 主动弹幕）、同归于尽（阵亡 AoE）、时光宝盒（F7 回溯快照）；常规四件——维修（回满 HP）、电磁静默（敌方停火）、诱饵（假身吸火力）、地雷（原地布雷）。全部定义在 `PowerUpType`（`src/types.ts`）+ `config/powerups.ts`，加一种道具 = 加一行数据。

### 1.4 操控

WASD / 方向键移动，空格开火，P 暂停，R 回菜单，按键可自定义（localStorage 持久化）。移动键采用 **last-pressed-wins**（`Input.moveStack` 按按下顺序的栈）：按住上再按右，坦克立刻听新命令——这是每个玩过现代射击游戏的手都默认的直觉，老式"固定优先级"的黏滞感被彻底扫进历史。

### 1.5 难度四档

`src/config/difficulty.ts`，纯配置：

| 档位 | 命数 | 敌方倍率（速度/火力/HP） | 起始星级 | 指挥官概率 |
|---|---|---|---|---|
| Relax | 5 | 0.7 / 0.6 / 0.5 | 1 | 0% |
| Classic | 3 | 1.0 / 1.0 / 1.0 | 0 | 15% |
| Hard | 2 | 1.3 / 1.4 / 1.5 | 0 | 30% |
| Chaos | 1 | 1.6 / 1.8 / 2.0 | 0 | 50% |

注意最后一列——**难度提升的主力不是数值膨胀，而是敌人变聪明**（§4）。

---

## 2. 引擎骨架 —— 一条铁律撑起一切

```
Input → Simulation → World → Renderer / Audio / UI
```

**One Author（唯一作者）**：只有 `Simulation` 能改 `World`。输入、渲染、音频、UI 全部只读。这一条铁律换来了下面所有超能力：

- **无隐藏状态**：所有游戏状态都在 `World` 对象里。没有单例、没有模块变量、没有闭包私藏。一份快照 = 完整世界。
- **确定性是承诺**：固定 60Hz 时间步（累加器 + 每帧最多 5 步防死亡螺旋）；一切随机数走 `world.rng`（种子化 mulberry32，状态可导出/恢复）。同输入 + 同种子 = 逐帧一致的重演。这不是优化，是**契约**——回溯、回放、未来的联机实验全靠它。
- **数据高于代码**：坦克是配置、关卡是配置、难度是配置、主题是配置、AI 智能等级也是配置。引擎执行，不硬编码。
- **表现层可抛弃**：粒子、震屏、动画状态从不进 World；时光回溯或回菜单时 `PresentationLayer.reset()` 一把梭，视觉从 World 重建。

核心文件：`World.ts`（状态 + 实体管理）、`Simulation.ts`（每 tick 顺序执行出生/AI/移动/子弹/道具/胜负判定诸系统）、`Game.ts`（固定步长主循环 + 状态机 + 设置持久化）。

---

## 3. 战斗能力系统 —— 六维定生死，预算定平衡

老 FC 里每种坦克的数值是写死的。这里不是。**每辆坦克（包括玩家）由同一张六维能力卡描述**（`CombatProfile`：火力 / 弹速 / 火控 / 机动 / 装甲 / 特殊，0–100，50 为基准），具体数值（速度、HP、冷却、穿甲）全部由 `profileToStats()`（`src/config/combat.ts`）**推导**而来。

**平衡哲学：预算制。** 四种敌人的六维总和一律 = 300。fast 把点数砸在机动（80），armor 砸在装甲（90 → 4 HP），power 砸在火力（75）。差异来自**分配**，不是总量——"难度来自多样性，不是膨胀"。

几条用测试钉死的硬核不变量：

- **子弹永远追得上坦克**：坦克速度带 0.9–2.1 px/tick，子弹带 3.6–6.0 px/tick，两带永不相交。最快的车也会被最慢的弹甩开 1.7 倍。
- **对枪公平**：无 buff 玩家（420ms 冷却）的射速 ≥ 任何敌方原型。对枪时子弹 1:1 抵消，射速快者必胜——所以任何敌人都不许在纯拼枪里靠射速赢你（`tests/fire-rate-duel.test.ts`，6 个真模拟对枪用例把关）。
- **穿钢门槛**：火力 ≥ 80 才能碎钢。默认 power（75）啃不动；精英 power（+15% → 86）和满星玩家（80）可以——正是 FC 里三星战车碎钢板的那个瞬间。
- **中弹不掉能力**：非致命命中只扣 HP + 叠加裂纹/焦痕贴花（`hitCount` 0–4 档受损遮罩），坦克的类型、外观、六维一概不变。

**玩家成长**：每颗星全维 +10（Lv0=50 → Lv3=80），速度、弹速、射速、穿甲同步水涨船高。阵亡即全部清零回难度基线——星星是荣耀，不是存款。

---

## 4. 战术智能框架 —— 敌人不再是无头苍蝇

当年的敌人是加权随机瞎转。现在，**每辆敌方坦克跑同一条决策管线**（`src/ai/TacticalIntelligence.ts`）：

```
World → 感知 → 态势分析 → 目标评估 → 决策 → 行动规划 → 执行
```

**三层思考，三个时间尺度**：

- **战略层（~20s）**：稳定的长期目标（打基地 / 猎玩家 / 撤退）
- **战术层（~5s）**：动态目标评分 + 寻路目标点
- **反应层（每 tick）**：子弹预判闪避 + 闪避方向锁定（350ms 防抖）

**智能是配置，不是代码**（`src/ai/config.ts`）。四档等级 Rookie / Soldier / Veteran / Commander，每档一组数据：闪避概率（0.2→0.9）、预判深度（1→8）、反应延迟（420ms→150ms）、瞄准失误率（0.35→0.05）、路线噪声，外加六种候选目标（attackBase / attackPlayer / destroyWall / retreat / regroup / advance）的评分权重。basic 是新兵，fast 是士兵，power/armor 是老兵。加一档新智能 = 加一条注册表记录。

**指挥官系统**：高难度下按概率选举一名指挥官（最高档者当选），获得 +15% 专精维度的**精英加成**、满血登场，并每 ~20 秒广播战术指令（压左翼 / 压右翼 / 守基地 / 集火 / 散开）。指令是**影响而非控制**——有团队意识的老兵会听，新兵置若罔闻。当四辆车同时朝你的基地包抄时，你会怀念它们还是无头苍蝇的日子。

**不完美模型**：反应延迟、瞄准失误、路线噪声让高档 AI 强而不神（闪避率封顶 0.95）。**能力驱动决策**：`capabilityBias()` 让高机动的爱包抄、重装甲的敢硬推、高火力的更好战——同一套管线，每辆车打出自己的性格。

全部熵走 `world.rng`，全部大脑状态（`AIState`，扁平可序列化）挂在坦克上进 World——AI 完全确定性、完全可快照。`tests/tactical-ai.test.ts` 守护确定性、不卡死、指挥官选举与"智能越高闪得越多"。

### 4.1 God AI —— 会替你开车的队友

`src/ai/god/` 是仓库里最大的子系统（~40 文件）：一套给 **P1/P2 玩家坦克代驾**的决策层。菜单里可开启"躺赢"模式让 God AI 打完整关；双打督战局里空出的座位也能交给它接管。

- **管线**：`think.ts` 编排候选生成（`candidates/`，Hunt/Engage/Dodge/PickupHigh 等约 20 个评估器）→ `DecisionCore` 按权重契约择优 → `Navigator`/`PathCarve`/`pathfind.ts` 寻路 → `FireControl`/`ThreatAssessor` 火控与威胁规避 → `StrategyPlanner`/`CoveragePlanner` 战略层。
- **参数面**：218 个调优参数分三表（`params.ts` / `params.interface.ts` / `params.tables.ts`）+ 关卡自适应（`stage-adapt.ts`），全部走 `world.rng`，完全确定。
- **调优纪律**：headless 批量模拟（`tools/sim/`）、取证工具（`tools/diag/`）、分数门禁（`tests/godai-score-gate.test.ts` + `tools/eval/godai-score.ts` v7 十一维评分）——改 God AI 必须过硬难度胜率与 determinism 门。

---

## 5. 快照与时光回溯 —— 失败不是终点

这是全项目最浪漫的功能。快照框架（`src/snapshot/`）在后台**每 30 秒静默拍一张世界快照**：自动/开局/暂停三类各留 **20 张循环缓冲**，手动档（默认 Alt+S）留 **100 张永不覆盖**，全部存 IndexedDB 并带 JPEG 缩略图——固定内存，永不膨胀。基地爆炸、命数归零的那一刻，游戏不甩你一脸 GAME OVER，而是问你：

> **回到 30 秒前？回到 60 秒前？还是重打本关？**

实现（`SnapshotManager` + `WorldSerializer` + `RecoveryController`）：

- **快照 = 完整世界**：地形网格、玩家/敌人/子弹/道具（spread 深克隆）、出兵队列、分数命数星级、全部计时器、**RNG 内部状态**、帧号。恢复时整体原子覆盖，绝不参与游戏规则——它是 One Author 铁律的唯一豁免者，也只做搬运工。
- **流程**：失败被拦截 → 回溯菜单 → 选择 → 黑屏淡出 → 快照恢复 + `PresentationLayer.reset()`（表现层全弃重建）→ 3-2-1 倒计时（配合成音效）→ 开打。
- 因为 RNG 状态在快照里，**回溯后的世界会走向和当初一模一样的未来**——除非你这次打得更好。

同一台机器，今天就驱动着回放、手动存档和接管续玩。这就叫基建。

### 5.1 回放系统 —— 已建成

`src/replay/`：`InputRecorder` 录制输入边沿，`ReplayManager` 把确定性回放存进 IndexedDB，`PlaybackController` 从录制输入重演整局。回放浏览器可列出/载入历史对局；回放进行中还能**接管续玩**（takeover）转成躺赢双人局。同种子 + 同输入 = 逐帧一致，这就是回放的全部秘密。

---

## 6. 表现层 —— Modern Retro，零素材，可抛弃

### 6.1 零素材纪律

**没有一张 PNG，没有一个音频文件。** 39 个精灵全部是手写 SVG（`src/assets/sprites/`，96×96 viewBox，坦克一律朝上、渲染时旋转），加载时由 `SpriteCache` 按 DPR 预光栅化成 canvas 位图。音效全部由 Web Audio API 实时合成。整个游戏自包含——打开页面，直接开战。

### 6.2 视觉语言：一眼分敌我

Modern Retro 风格：暖奶油底色、圆角、柔和投影。阵营识别是铁律——**玩家戴星徽，敌人长怒脸**：

- P1 暖黄 / P2 英雄蓝，炮塔中央一颗白星，无人脸
- 敌方四型各有人相：basic 深红怒目、fast 青色流线+拖尾残影、power 反派紫双炮管、armor 钢灰铆钉+黄色怒眼
- 增益全是**可叠加透明遮罩**：三段式星星光环（1/2/3 星差异一目了然）、六边形护盾能量泡、敌方 0–4 档受损裂纹
- 地形 tile 96×96 满幅无缝平铺（纹理周期整除 96）：错缝红砖、周期波浪水面、裂纹冰面——没有马赛克断点

### 6.3 渲染架构

Canvas 只画 416×416 战场（离屏缓冲 + DPR 缩放，视网膜屏像素锐利）；HUD、菜单、覆盖层全部是 HTML/CSS（`UIManager`），主题色通过 CSS 变量实时注入。三套主题：Classic / Neon / Modern Retro——换主题 = 换一组颜色数据，玩法零变化。

配套系统：池化粒子系统（预分配零 GC）、相机震屏（指数衰减）、时间基准动画系统（帧率无关）、屏闪特效。事件驱动：`Simulation` 发 `GameEvent`，音频和表现层各自消费同一条事件流。

---

## 7. 性能与能耗 —— 60fps 只是及格线

四轮压榨（DECISIONS §21–§26），目标不止流畅，还有**风扇不转**：

- **稳态零分配**：事件双缓冲交换、复用对象、swap-and-pop 原地压缩、模块级常量提升——渲染路径和模拟 tick 在稳态下每帧分配 ≈ 0，GC 无事可做。模拟本体实测 ~2µs/tick（预算 16.6ms）。
- **增量地形缓存**：打碎一块砖只重绘那一个 cell（脏格列表），不再全图 676 格重画。
- **按需渲染**：场景签名（水面相位 + 实体粗粒度位置 + 状态位）不变就整帧跳过重绘——菜单、暂停、结算画面 GPU 彻底空转，风扇静音。切后台直接停 rAF 循环。
- **回归护栏**：滚动 FPS 采样（连续 3 秒 <45fps 才告警）+ 无头基准 `tools/sim/bench-sim.ts`。

---

## 8. 质量防线 —— 测试是弹药库

Bun + TypeScript strict 全家桶。**`bun run check` = `tsc --noEmit` + 全量 `bun test --parallel --timeout=50000`**，是"绿灯"的唯一定义（lint/format 在 `bun run build` 的 oxlint/oxfmt 里）。127 个测试文件、~1400 个用例，专打要害，代表性岗哨：

| 测试 | 守护什么 |
|---|---|
| `simulation.test.ts` | 确定性：同种子跑两遍 + 中途扰动 `Math.random()`，世界必须逐帧一致 |
| `tactical-ai.test.ts` | 敌方 AI 确定性、不卡死、指挥官选举、闪避随智能递增 |
| `fire-rate-duel.test.ts` | 对枪公平不变量（真模拟对射，玩家必不败） |
| `combat.test.ts` | 六维推导、预算、穿钢门槛、速度带 |
| `stages.test.ts` | 关卡解码器（独立重实现比对，黄金文件抓不到的回归它抓得到） |
| `snapshot-framework.test.ts` / `serializer-field-guard.test.ts` | 快照字段完备性、双向恢复一致 |
| `godai-score-gate.test.ts` | God AI 分数门禁（1050 局 headless 模拟，防行为退化） |
| `godai-hub-fields.test.ts` | GodAIInput 字段护栏（新增字段必须登记） |
| `input.test.ts` | last-pressed-wins、跨帧按键持久 |

铁规矩：**修 bug 必须先写出失败的复现测试**，先红后绿，绝不裸修（AGENTS §7）。

---

## 9. 留给未来的座位

架构今天就为它们留好了位置，一行引擎代码都不用动：

- **新坦克** = 一条能力配置；**新关卡** = 一张网格;**新主题** = 一组颜色；**新 AI 档** = 一条注册表
- **新模式**（无尽 / 塔防 / Boss Rush）= 规则 + 出兵 + 胜利条件的组合
- **统计面板** = 订阅现成的 GameEvent 流
- **社区内容** = 数据全部 JSON 兼容，随时可外置

（回放与快照框架已建成——见 §5。）

---

## 附录：代码地图

```
src/
  game/          Simulation + 六子系统（Spawn/Player/Enemies/Combat/PowerUps/Effects）
                 · World · TileMap · Input · Game/GameLoop/GameMenu/GameSnapshot/GameReplay
                 · systems/EventBus/KillPipeline/TankFactory/GridQuery/UIState/settings
  ai/            TacticalIntelligence + perception（敌方 AI）· god/（God AI：think、candidates/、
                 FireControl、ThreatAssessor、StrategyPlanner、Navigator、params 三表 …）
  snapshot/      SnapshotManager · WorldSerializer · RecoveryController · storage（IndexedDB）
  replay/        InputRecorder · ReplayManager · PlaybackController · storage
  config/        combat（六维）· stages+stageData（35 关）· difficulty · theme · score(+constants)
                 · rules · powerups · fire-rate · hp-level · speed · base · effects-config
  presentation/  PresentationLayer · renderer（Core+切片，SVG→SpriteCache）· ui/（HudView、
                 MenuScreen、ControlsPanel、OverlayManager、ControlCenter、ReplayBrowser…）
                 · 粒子/相机/动画/特效
  audio/         AudioManager（Web Audio 合成）
  assets/        39 个手写 SVG 精灵
  utils/         RNG（mulberry32）· helpers · idb-store
tests/           127 个文件、~1400 个用例的弹药库
```

> 三十年了，基地还在 26×26 的战场正下方等你守护。
> `bun run dev`，上车。
