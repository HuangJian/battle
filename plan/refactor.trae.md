# Refactoring Plan (Round 4) — 降低后续 Agent 开发与维护复杂度

> **Status (2026-08-25): PROPOSAL — 未执行。**
>
> 这是第四轮重构计划。历史（按时间）：
> 1. `refactor.agy.md`（2026-08-23，**EXECUTION COMPLETE**）— 核心层：mixin 洋葱、GameLoop
>    god method、World 上帝对象、One-Author 泄漏、serializer 字段守卫、UIManager 拆分。
> 2. `refactor.trae.md`（2026-08-24，**已执行，文件未入库随后删除**）— P1 活文档/死代码；
>    P2 tests/tools（helpers.makeTank 收编、parseSeeds 正名、8 个零引用工具归档、god-ai-*
>    测试改名）；P3 **God-AI 去重分解**（`params` 3636 行拆为 interface/tables/facade、
>    `think.ts` 3275→641、`evalHunt` 597→127、`dodgeDirectionImpl` 330→112、bulletLaneDist
>    单源化 ×10、守卫簇 ×4、pickup 尾巴 ×6、钢穿深 ×7、hub 包装直连 ×11 等）；P4 One-Author
>    入口路由 ×3、parseReplayFile 分解。详见 `.workbuddy/memory/2026-08-24.md`。
>
> **本轮定位**：前两轮把「单文件尺寸 / 跨文件重复」两大块收拾了大半，但**参数系统、
> 候选器注册、telemetry、config 扇出**这些「结构性」坏味仍在（zcode 拆了 params 三个文件，
> 却没解决「一个参数维护 2~4 张表」的猎枪手术；think.ts 拆小了，但 CANDIDATES 数组仍混着
> 默认关闭/已否决的候选）。
>
> **目标**：以「降低未来 agent 开发/维护复杂度」为唯一标尺，针对残留的结构性坏味给出
> 可执行、带验收标准、有确定性护栏的增量方案。**不是要让 God-AI 变简单**（那是产品复杂度，
> MANIFEST §10 允许），而是把「在跑的逻辑」与「留档的实验」分离开，消除隐性副作用与多源
> 维护。

---

## 0. 现状审计（2026-08-25）

| 区域 | 现状 | 头部问题 |
|---|---|---|
| `params` 系统 | `interface` 242 字段 / `tables` 4 表 / `facade` | 一个参数维护 2~4 张表；~50 个 `0=OFF` 留档旋钮混在 shipped 默认里 |
| 候选器 `candidates/` | 20 文件 | 已否决/默认 OFF 的候选仍挂在 `CANDIDATES` 决策链上 |
| telemetry | `branchCounts` / `_lastBranch` | 2+ 文件分散写入，无单点记账 |
| `config` | 5 文件 ~11 张 `Record<TankKind>` 表 | 「加一个坦克」扇出 ~7 文件，与 MANIFEST §6 张力 |
| `snapshot` | 手写 clone/restore + 守卫测试 | 已有守卫（agy），仅留 legacy fallback 可读性 |
| `DECISIONS.md` | 2203 行 / 173KB | 阴性/REJECTED 条目与 shipped 同权重堆叠 |
| `tools/` / `tests/` | 残余样板 + 失效引用 | `refactor.trae` 失效引用 ×10；`new RNG` / 方向数组样板 |

**关键数据点：**

- `src/ai/god/params.interface.ts`：**242 字段**（文件自述 "218-field"，已漂移 +24）。
- `src/ai/god/params.tables.ts`：**~50 个 `0 = OFF` / `default OFF`**，分属
  `DEFAULT_GOD_AI_PARAMS` / `CLASSIC_MODEL_PARAMS` / `SKILLED_HUMAN_PARAMS` /
  `GUARD_GOD_AI_PARAMS`。
- 默认关闭/已否决但仍阿在决策数组里的候选：`FIRING_LANE`（§139 灾难性阴性，`firingLaneMode=0`）、
  `CARVE_PATH`（§161 诚实阴性，`carvePathMode=0`）、`MID_LANE_HOLD`（§164 诚实阴性，
  `midLaneHold=0`）、`UNIFIED_CANDIDATES`（§221 reject，`candidateMode=0`）、
  `enemyModelMode=0`（M3 默认 OFF）、`suicideReturnMode=0`（§116 阴性）、`direItemMode=0`（§144 阴性）。

---

## 0.5 勿重提清单（已审计/已否决，未来 agent 不要再重查）

> 前几轮反复审计过的结论，记录在此**防重提**（避免未来 agent 再花时间调查并产生矛盾结论）。

| 项 | 结论 | 依据 |
|---|---|---|
| `Tank` 实体字段拆分 | **不拆**。42 字段经逐字段 grep 读写方，**零死字段**；spread 克隆零漂移。 | zcode §257（08-24 复核维持） |
| `WorldSerializer` 改实体级 spread 为字段级手写 | 实体已走 spread 克隆，护栏只需守 World 顶层且已完备。维持现状。 | zcode §257 |
| 主题 `skipSvg` 分支删除 | **保留**。`skipSvg = (themeKey !== 'modern')` 是活代码。 | agy §3.1 / §252 |
| 测试子目录化 | **不做**（churn 高、零玩家价值）。仅「廉价改名」已做（god-ai-* → godai-* ×4）。 | agy §3.5 / zcode §269 |
| `byId` 事件字段删除 | **保留**。`simulation-runner` 取证工具在消费它。 | zcode P1 |
| `applyPowerUp` switch → 派发表 | **维持**。switch 本身就是派发表，形态合理。 | zcode 复核 |
| 大文件机械搬移（无测试网领域） | 高返工率，**先补特征测试再动手**。 | zcode 教训 3 |

---

## 1. 关键坏味（高 agent 摩擦）

### 1.1 `params` 系统：一个参数维护 2~4 张表 + 留档旋钮混入 shipped 默认

**现状**
zcode 把 3636 行 monolith 拆成 `params.interface.ts`（类型 + 注释）/ `params.tables.ts`
（4 张值表）/ `params.ts`（facade）。但**结构性问题未解决**：
- `params.interface.ts` 242 字段仍是单一手写接口，字段自带 A/B 实验说明注释，其中 ~50 个
  字段 DEFAULT 为 0（对应 DECISIONS「阴性/不发布/REJECTED」）。
- 同一参数在多张表里数值不同，必须同步改：`defenseInterceptMode` DEFAULT=1 / CLASSIC=0；
  `baseLaneSentryMode` DEFAULT=1 / CLASSIC=0；`baseConnectClearMode` DEFAULT=1 / CLASSIC=0。

**为什么是坏味**
- **补一个参数要改 2~4 处**（interface + DEFAULT，可能还要 CLASSIC_MODEL / GUARD）。这是
  「猎枪手术」，未来 agent 加旋钮易漏改其中一张表 → 隐性行为漂移。
- shipped 参数与「被否决但保留的退路参数」混在一起，无法一眼判断某字段是否真的参与决策。

**方案（分两小步）**
1. **（零行为）加「留档旋钮清单」**：在 `params.interface.ts` 顶部集中列出「默认关闭/已否决、
   仅留档」的字段名，把散落的 0 态显式化。纯注释。
2. **（数据重组）单真源 + 覆盖层**：`DEFAULT` 为唯一真源，`CLASSIC_MODEL` / `SKILLED_HUMAN` /
   `GUARD` 一律 `{ ...DEFAULT, ...overrides }`。字段在 4 表里最多出现在 DEFAULT + 一处覆盖，
   消灭「同一参数四处数值不一致」。

**验收**
- 任一参数在 4 表中最多出现在 DEFAULT + 一个覆盖表。
- `bun run check` 绿 + `bun test --parallel --timeout=50000` 绿 + 确定性签名 byte-identical（§5.3）。

**风险**：低（步骤 1 无行为变化；步骤 2 纯数据重组，不改变最终解析出的对象内容）。

---

### 1.2 候选器死代码混入 `CANDIDATES` 决策链

**现状**
`think.ts:368-397` 的 `CANDIDATES` 数组混着三类候选：
- **已否决/默认关闭**：`FIRING_LANE`、`CARVE_PATH`、`MID_LANE_HOLD`、`UNIFIED_CANDIDATES`、
  `DEFENSE_INTERCEPT`（classic 下关闭）。
- **默认关闭的感知模块**：`enemyModelMode=0`、`suicideReturnMode=0`、`direItemMode=0`。
- **默认启用**：`BASE_LANE_SENTRY`、`BASE_CONNECT_CLEAR`、`MID_LANE_DEFENSE`、`HUNT` 等。

**为什么是坏味**
未来 agent 遍历 `CANDIDATES` 会把「数组里存在」误判为「默认运行」，追进永远不执行的
`evaluate`。每个死候选还拖着一整条依赖（`CARVE_PATH`→`PathCarve.ts` 991 行、
`UNIFIED_CANDIDATES`→`ActionCandidates.ts` 629 行）。

**重要定性**：这些不是「垃圾」，而是**留档实验资产**——项目 Phase III 调优依赖「旋钮默认
OFF、可随时重开 A/B」的能力（AGENTS §6.3b）。所以方案是**分离，不是删除**：把「在跑」与
「留档」分开，让决策链干净、留档可检索。

**方案**
1. 产出「候选存活状态清单」（id → 门控参数 → DEFAULT 值 → DECISIONS 结论），作为唯一事实源。
2. 把「DECISIONS=阴性/reject + DEFAULT=0 + 零引用 + 无 1 态测试调用」的候选与依赖**移入
   `src/ai/god/experiments/`（或复用 `tools/diag/archive/` 模式）**，从 `CANDIDATES` 数组移除；
   保留文件与 DECISIONS 指针，供未来重开 A/B 时 `git` 或 import 找回。
3. 不满足四条件、仍需保留旋钮的，在数组里加注释标注「默认 OFF，仅留档」。

**验收**
- 每个移出的候选：`grep -rn` 零引用，确定性签名 byte-identical，`bun run check` 绿。
- `CANDIDATES` 里剩余条目旁标注存活状态，杜绝「以为它在跑」。

**风险**：中。必须在确定性签名门禁下进行（§5.3）；任何被测试以非零态 A/B 调用的旋钮不得动（§5.1）。

---

### 1.3 候选器注册分散两文件 + `evaluate` 直接写 `self` 副作用

**现状**
- 每个候选的 `Candidate` 对象（`{ id, weight, evaluate }`）在 `think.ts`，`evaluate` 函数体在
  `candidates/*.ts`。改一个候选要在两文件间跳转，还需同步 `ACTION_WEIGHTS`（`DecisionCore`）。
- `evaluate` 直接写 `self._moveDir` / `self._fire` / `self.branchCounts.x++` / `self._lastBranch`
  （`CarvePath.ts:46-50`、`UnifiedCandidates.ts:50-55`），副作用隐式且分散。

**为什么是坏味**
候选既是「评估器」又是「决策提交器」；`Candidate` 对象与 `evaluate` 分离，改动易漏改
权重/门控/数组位置三处之一。

**方案（分步）**
1. **就地收敛注册**：把 `Candidate` 对象定义从 `think.ts` 移到对应 `candidates/*.ts`，让
   「候选 = id + weight + evaluate + 门控」单文件自包含；`think.ts` 只 import + 组装数组。
2. **副作用显式化**：`evaluate` 返回决策结果（`{ moveDir, fire } | null`），由 `runChain` /
   `thinkImpl` 统一提交；`branchCounts`/`_lastBranch` 改为「候选返回命中 id」后单点记账（§1.4）。

**验收**
- 新增候选只需创建 `candidates/X.ts` + 数组加一行，无需再改 `think.ts` 内部。
- `bun run check` 绿 + 确定性签名 byte-identical。

**风险**：中。步骤 2 触碰每 tick 热路径（AGENTS §14），保持「零数组分配」约束；决定权在
「行为等价」而非「代码更漂亮」。

---

### 1.4 telemetry（`branchCounts` / `_lastBranch`）无单一定义点

**现状**
`branchCounts.dead`/`hold` 在 `think.ts` 写，`branchCounts.midLaneHold`/`unifiedCandidates`
在候选 evaluate 写，`_lastBranch` 随 evaluate 改写。排查 replay/telemetry 要跨文件核对计数来源。

**为什么是坏味**
分支计数没有单一记账点，加新分支易漏计/重复计，导致 `godai-score` 的 `tempo`/`openingTempo`
维度数据失真而不自知。

**方案**
由 §1.3-2 顺势统一：`runChain` 命中候选时在**一处**更新 `branchCounts[id]` 与 `_lastBranch`；
dead/hold 早期返回分支收敛到同一记账函数。

**验收**
- `grep -rn "branchCounts\." src/ai/god` 只命中单一记账函数。
- `godai-score` 相关测试通过，评分维度数值不变（确定性签名覆盖）。

**风险**：低~中。纯 telemetry 收敛，但须保证计数顺序不变（影响 replay 确定性）。

---

### 1.5 超大单文件残留（可选收尾）

**现状**（zcode 分解后仍 >900 行的 God-AI 文件）

| 文件 | 行数 | 混入的职责 |
|---|---|---|
| `StrategyPlanner.ts` | 2402 | 防守威胁选靶 + dual-central-breach + guard-anchor + 清障攻击点 |
| `ThreatAssessor.ts` | 1638 | 子弹威胁 + 基地车道拦截 + 排列计数 |
| `FireControl.ts` | 1224 | 目标扫描 + 对齐判定 + 多种 fire gate |
| `PathCarve.ts` | 991 | 环砖判定 + base-col 判定 + carve/dig 代价 + 缓存 dig path |
| `params.interface.ts` | 2376 | 类型 + 默认值语义注释 |

**为什么是坏味**
维护成本依「决策/状态复用/多门控是否耦合于单文件」而定，上述文件职责边界偏宽，单独测试
需模拟大量参数组合。

**方案（低优先级，仅在 1.1~1.4 之后、收益明确时做）**
按「§ 编号对应的行为簇」切分（如 `StrategyPlanner` 抽出 `guard-anchor` 族为
`strategy/guard-anchor.ts`），遵循既存 `xxxImpl(self, ...)` 纯函数约定，不引入新抽象。

**验收**：目标文件 ≤ ~800 行；`bun run check` 绿；确定性签名 byte-identical。

**风险**：中高。God-AI 改动须走 §6.3b 门禁（hard 为主、胜率为主指标）。本轮排在最后，
作为可选收尾不强求。**注意**：无测试网的领域先补特征测试（zcode 教训 3：机械搬移返工率高）。

---

## 2. 主要坏味

### 2.1 「加一个坦克」的配置扇出与 MANIFEST §6 张力

**现状**
MANIFEST §6「加坦克=加一行」，但 `TankKind` 扇出到 **5 文件 ~11 张表**：

| 文件 | 表 |
|---|---|
| `combat.ts` | `TANK_PROFILES`、`ELITE_DIMENSION` |
| `speed.ts` | `BASE_SPEED_CPS`、`BULLET_SPEED_MULT`、`BASE_BULLET_SPEED_CPS` |
| `fire-rate.ts` | `FIRE_FREQUENCY_MULTIPLIER` |
| `rules.ts` | `hitsToKill`、`maxBullets`、`speedCps`、`bulletSpeedCps`、`scoreByKind` |
| `ai/god/constants.ts` | `KIND_THREAT_WEIGHT` |

另有 `SpriteArtistTanks.ts`、`SimulationCombat.ts`（armor flash / player 星数）、`god/constants.ts`
的 `switch(kind)` 分支。

**为什么是坏味**
「加坦克」实际是「改类型字面量 + 补多达 11 张表 + 检查多处 switch」。它与 `CombatProfile`
六维派生系统（combat.ts）形成了第二套并行的 per-kind 字面量表（speed/fire-rate/score），
未来 agent 分不清哪个是单源真值。

**方案（数据层，渐进）**
1. 产出「每属性单源真值表」：`speed`/`bulletSpeed`/`fireCooldown`/`score` 归口到一张
   `TankSpec` 注册表，其余 `Record<TankKind>` 变为派生。
2. `switch(kind)`（渲染颜色 / AI 威胁权重）换查表。
3. 目标验收：加一个新坦克 = ① `types.ts` 加 `TankKind` 字面量 + ② `TankSpec` 加一行，
   编译器以 `Record<TankKind,...>` 穷尽性提示遗漏点。

**验收**
- 新增 tank 的「改动文件数」从 ~7 降到 ~2（含类型字面量）。
- `grep -rn "switch (kind)\|switch(kind)" src/` 仅剩渲染器角色绘制与必要性态分支。

**风险**：低~中。纯 config 重组，确定性不受影响（config 在 loadStage 解析，不在 tick 热路径）。
**保留** `rules.ts` 里 classic 的忠实 FC 数值（是刻意忠实行为，只挪位置、不删），建议独立成
`config/fc-faithful.ts` 覆盖层并注释「忠实 FC，勿与平衡值混改」。

---

### 2.2 config 数值双写（speed / score / fire-rate 与 rules 并存）

**现状**
- 速度：`speed.ts` `BASE_SPEED_CPS` 与 `rules.ts` `speedCps`（含 classic 硬编码）。
- 分数：`score.ts`、`score-constants.ts`、`rules.ts`（`scoreModel`/`scoreByKind`/`itemScore`）。
- 开火：`fire-rate.ts` `FIRE_FREQUENCY_MULTIPLIER` 与 `combat.ts` 生成的 `fireCooldown`。

**为什么是坏味**
同一口径多个真源，调数值易改一处漏一处，classic 忠实值还可能被误当「旧值」删掉。

**方案**
配合 §2.1 单源化回收：`rules.ts` 仅保留规则配置形态，数值从 speed/score/fire-rate 表**引用**，
删除重复字面量；classic 忠实值独立成 `fc-faithful.ts` 覆盖层。

**验收**：每个数值口径唯一真源；`grep` 确认无跨文件同义字面量双写。

**风险**：低~中。

---

### 2.3 `DECISIONS.md` 173KB 索引膨胀

**现状**
2203 行 / 173KB。「God AI Tuning」段 §71~§169 混着 SHIPPED / 诚实阴性 / REJECTED / CANDIDATE，
阴性归档条目与 shipped 条目同权重堆叠。

**为什么是坏味**
这是未来 agent 每次动手前要读的索引（AGENTS §1），越厚越难扫；且已单向膨胀到 173KB。

**方案（纯文档重构）**
1. 「God AI Tuning」段已终结的阴性/REJECTED 条目批量压缩为归档小节（保留「结论 + 指针」，
   A/B 表细节下沉到 `docs/god-ai-tuning.progress.md`），正文只留「当前生效 + 最近 N 条」。
2. 保留 § 编号连续性，历史不删（AGENTS：superseded 标注而非删）。

**验收**：主索引回到可扫读尺寸（目标 <~1000 行），阴性细节均有 `docs/*.progress.md` 指针承接。

**风险**：无运行时风险；`DECISIONS.md` 已在 git 跟踪内，可安全编辑提交。

---

## 3. 中等坏味

### 3.1 失效文档引用（`refactor.trae.md` 已删仍有 10 处引用）

**现状**
zcode 计划文件未入库随后删除，但 `params.ts` 注释、`tools/lib/cli.ts`、`tools/eval/eval-suite.ts`、
`tests/helpers.ts`、`tools/sim/*`、`plan/tasks.chat.md` 等 10 处仍引用它。

**为什么是坏味**
未来 agent 顺着引用找文档会扑空；更关键的是**重构计划文件不应在未保留内时被删**——zcode 的
§7 否决清单现在只存在于 daily log，失去了计划文件这份一手记录。

**方案**
1. 把引用统一改指「已完成」的计划（`refactor.agy.md` 或本文件）。
2. 补一条约定：**重构计划文件除非内容并入 DECISIONS.md，否则保留**（或以「否决结论」摘要
   落 DECISIONS，如 §0.5 勿重提清单）。

**验收**：`grep -rn "refactor.trae"` 归零；否决结论有持久落点。

**风险**：极低。

---

### 3.2 `tools/diag` 与测试样板残留

**现状**
- zcode P2 已归档 8 个零引用工具 + 新建 `replay/archive`；`tools/diag/` 仍有 30 个非归档脚本
  （`*-audit`/`*-probe`/`diagnose-*` 一次性取证口径），grep 时制造无关命中。
- `tests/helpers.ts` 已存在，但仍有测试直接 `world.rng = new RNG(...)`、手写
  `['up','down','left','right']` 数组（`corridor-escape` 等 8 处）。
- 5 个大测试文件合计 ~3,249 行：`simulation`/`tactical-ai`/`godai-score`/`snapshot-framework`/
  `dodge-m3`。

**方案**
- `tools/diag` 延续零引用归档标准；`god-ai-*`/`godai-*` 命名已归一（zcode 已做改名），保持。
- 补齐 `tests/helpers.ts` 的 `seedWorld()`/`ALL_DIRS` 导出并逐步替换散落样板；大文件按 describe
  主题拆。**注意** `tools/test-silent.ts` 按 basename 映射测试，拆分前先确认映射逻辑。

**验收**：`new RNG(` 与手写方向数组在 tests/ 内只出现在 helper；`bun run test` 绿。

**风险**：低。

---

### 3.3 snapshot 手写字段 clone/restore 残留（低优先级）

**现状**
agy §1.5 Option C 已加 `serializer-field-guard.test.ts`。但 `WorldSnapshot` 仍 70+ 字段，
`cloneWorld`/`restoreWorld` 两处手写，`restoreWorld` 多个 `?? legacy fallback`
（`enemiesTotal ?? enemiesRemaining` 等）。

**方案（低优先级）**
在守卫测试上增加「clone→restore 往返字段等价」测试；对已无旧存档引用的 legacy fallback
评估移除（需确认无旧 replay/snapshot 依赖）。

**验收**：往返等价测试绿；fallback 项显式标注供哪个旧版本使用。

**风险**：低~中（触及 snapshot 兼容，需确认旧存储迁移策略）。

---

## 4. 执行优先级与分阶段

> 按「每单位努力的 agent 摩擦降低」排序；God-AI 相关项受 §5 护栏约束。

### Phase 1 — 纯文档/盘点（零运行时风险）
1. **§3.1** 失效 `refactor.trae` 引用归零 + 落「勿重提清单」到 DECISIONS。
2. **§2.3** DECISIONS 阴性条目归档下沉（仅改 md）。
3. **§1.1-1** params 顶部「留档旋钮清单」注释协议。
4. **§1.2-1** 产出「候选存活状态清单」事实源表。

### Phase 2 — 数据/配置单源化（低风险，确定性无关）
5. **§1.1-2** params 表合并「单真源 + 覆盖层」。
6. **§2.2 + §2.1** 数值单源化 + `TankSpec` 注册表 + `fc-faithful.ts` 覆盖层。

### Phase 3 — AI 层死代码分离 + 注册收敛（中风险，需确定性门禁）
7. **§1.2-2/3** 「阴性 + 零引用 + 无 1 态测试」候选与依赖移入留档目录。
8. **§1.3-1 + §1.4** 候选注册就地收敛 + telemetry 单点记账。

### Phase 4 — 结构改进（中高风险，收益明确才做）
9. **§1.3-2** evaluate 副作用显式化（返回决策结果）。
10. **§1.5** 超大文件按行为簇切分（可选，走 §6.3b 门禁）。
11. **§3.3 / §3.2** snapshot 往返守卫、测试拆板、diag 归档收尾。

---

## 5. 风险与护栏

### 5.1 什么不要动
- **`stageData.ts`**（35 关原始数据）：数据非代码，勿碰。
- **已发货 God-AI 行为**：`默认值 ≠ 0` 的旋钮、被 A/B 测试以非零态调用的旋钮，本轮只读不删。
  死代码分离仅限「DECISIONS=阴性 + DEFAULT=0 + 零引用 + 无 1 态测试调用」四条件同时成立者。
- **Simulation 调用顺序**：触碰到子系统 tick 顺序的改动必须与现状完全一致，否则破坏 replay
  确定性（AGENTS §2.3）。
- **留档实验资产**：移出决策链 ≠ 删除文件，须能从 git / import 找回，Phase III 调优仍要能用。

### 5.2 验证门禁
```
bun run check                                  # tsc --noEmit + 全量测试
bun run build                                  # oxlint + tsc + vite build（发布口径）
bun test --parallel --timeout=50000           # 含 God-AI 分数门禁（改 AI 必跑）
```

### 5.3 确定性 smoke test（God-AI 改动强制）
```
bun tools/sim/batch-sim.ts --stages 1,5,10,20,35 --seeds 42 --difficulty hard > /tmp/pre.txt
# 改动后：
bun tools/sim/batch-sim.ts --stages 1,5,10,20,35 --seeds 42 --difficulty hard > /tmp/post.txt
diff /tmp/pre.txt /tmp/post.txt && echo "BYTE-IDENTICAL"
```
分歧种子用 `tools/diag/per-seed-diff.ts` 定位首个分歧 tick（AGENTS §6.3b 纪律）。
**前例教训**：determinism 门曾三次拦截 tsc/测试抓不到的语义级回归（FireControl 极性反相、
Hunt/Engage 基线耦合），务必逐项跑门而非只跑测试（zcode 教训 1）。

---

## 6. 指标（本轮基线 → 目标）

| 指标 | 当前 | 目标 |
|---|---|---|
| 每参数维护表数 | 最多 4 | 1 真源 + 1 覆盖 |
| `0 = OFF` 留档旋钮（DEFAULT 表） | ~50 | 移出 DEFAULT 或显式标注留档 |
| `CANDIDATES` 里「看似在跑实际默认关」的候选 | ~5 | 0（移出数组或标注 OFF） |
| 改一个候选需触碰的文件 | 3+（think/params/candidate） | 1（候选自包含） |
| 加一个坦克需触碰的文件 | ~7 | ~2（类型字面量 + TankSpec 一行） |
| telemetry 记账点（`branchCounts.` 访问） | 分散 2+ 文件 | 单点 |
| `DECISIONS.md` 行数 | 2203 | <~1000（细节下沉 progress.md） |
| 失效 `refactor.trae` 引用 | 10 | 0 |
| `tools/diag` 顶层脚本 | 30 | 仅 standing 工具 |

---

## 7. Round 4 实际落地（2026-08-25）

> 逐项执行结果。已落地项见各 commit；本表记录「做了什么 + 验收」与
> 「为何延期」（延期也是已决议，非遗留）。

### 已落地

| 项 | commit | 落地内容 | 验收 |
|---|---|---|---|
| §3.1 | `d8f6e54` | 失效 `refactor.trae` 引用归指 `refactor.agy`/本文件 + 落「勿重提清单」(§0.5) | `grep refactor.trae` 归零 |
| §2.3 | `90260ba` | DECISIONS §71–§169 已预压缩为 `docs/*.progress.md` 指针（非本轮新压缩，确认即满足） | 主索引未动（§192–§271 活跃结论，按 §5.1 不碰） |
| §1.1-1 | `c91b8ca` | `params.interface.ts` 顶部集中「留档旋钮清单」注释协议 | 纯注释 |
| §1.2-1 | `8efc648` | `think.ts` 候选存活状态清单作为唯一事实源 | 清单就位 |
| §1.1-2 | `a0e19aa` | params 单真源 + 覆盖层（CLASSIC_MODEL 改 spread） | typecheck/check 绿 |
| §2.2 + §2.1 | `b56f5b1` | 数值单源化 + `TankSpec` 注册表 + `fc-faithful.ts` 覆盖层 | 派生值=旧字面量；确定性门过 |
| §1.2-2/3 | `d9555b0` | OFF 候选四条件核查→按 §5.1 留档标注、不移出数组 | 四条件均不满足，留档 |
| §1.3-1 + §1.4 | `dbdf48d` | 19 候选对象移入各自 `candidates/X.ts` 自包含 + `recordBranch` 单点遥测记账 | batch-sim 逐 run 字节一致；1392 test 绿 |
| §1.4 补齐 | `e73976d` | 遥测单点记账**补齐**：dbdf48d 首轮仅转换 6 个候选文件（§7 当时「全部经 recordBranch」的表述与树不符，2026-08-26 审计纠正）；本轮收敛其余全部站点（think dead/hold、Hunt ×8、Aggro ×6、Dodge ×4 等 ~30 站点）至 GodAIInput 三助手 | grep `branchCounts.`/`_lastBranch=` 在 src/ai/god 仅余注释；batch-sim 字节一致；check 绿 |
| §3.2 | 见下 | `tests/helpers.ts` 增 `ALL_DIRS` / `seedWorld()`；替换散落 `['up','down','left','right']` 与 `world.rng = new RNG(...)` | `bun test` 绿 |
| §3.3 | `334ed63` | `serializer-field-guard.test.ts` 增 clone→restore 往返字段等价测试 | 测试绿；legacy fallback 标注供版本 |

### §1.3-2 — evaluate 副作用显式化：延期（契约部分；遥测部分已由 §1.4 全额交付）

- **§1.4 已全额交付**（`dbdf48d` + `e73976d` 补齐）：`branchCounts`/`_lastBranch`
  写入全部收敛至 `GodAIInput` 三助手——`recordBranch(self, countKey, label?)`
  （计数+标注，label 缺省=countKey）、`markBranch(self, label)`（仅置位不计数：
  Dodge 反应延迟、Aggro stop-and-aim 子分支的既有语义）、`countBranch(self, key)`
  （仅计数不改 `_lastBranch`：StrategyPlanner chokepoint 门）。三种形态逐一
  保真了改前的值语义（unifiedCandidates 计数键≠分支名、部分路径只置位、
  chokepoint 每 tick 计一次），验收「grep 只命中单一记账点」达成。
- **延期：`evaluate` 返回 `{moveDir, fire} | null` 由 `runChain` 统一提交**。
  - 理由：各 `evaluate` 携**丰富跨 tick 持久状态**（`_suicideStanding`、
    `_carveDigTicks`、`_dodgeFlipCount`、`_carveAimDir` 等），远超
    `{moveDir, fire}`；契约改造要么丢状态、要么连带重构状态传递机制。
  - 触碰每 tick 热路径（AGENTS §14，「零分配」约束），纯属「evaluate 变纯函数」
    的代码美学收益，**无玩家可见/回放确定性收益**，却每次改动都要确定性门兜底，
    风险/收益不划算——未过「降低未来 agent 摩擦足以抵消风险」的门槛。
  - 决策：保持 `evaluate` 直接写 `self._moveDir`/`self._fire`，不再动。

### §1.5 — 超大单文件切分：延期（计划本身定为可选）

- 计划定位：「可选收尾不强求」「收益明确才做」，且 §0.5 勿重提清单（zcode 教训 3）
  「大文件机械搬移（无测试网领域）高返工率，先补特征测试再动手」。
- 5 个超大文件（StrategyPlanner 2402 / ThreatAssessor 1638 / FireControl 1224 /
  PathCarve 991 / params.interface 2376）已遵循 `xxxImpl(self, ...)` 纯函数约定、
  可单文件导航；无对应行为簇特征测试，机械切分返工率高、收益边际。
- 决策：维持现状；仅当某行为簇（如 guard-anchor 抽取）确有边界且特征测试就位时再动。
