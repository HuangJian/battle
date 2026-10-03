# Design Decisions — 决策索引

> **本文是索引，不是正文。** 每条决策 = 编号 + 一句话描述 + 状态 + 指针；完整缘由与细节在
> 对应文档。编号是外部引用契约（AGENTS / features / architecture / docs 均按编号引用），
> **永不删除、永不重排**；新增决策从下个可用编号继续，被取代的条目标注
> `_(superseded by §N)_` 而非删除（AGENTS §6.3）。
>
> 落点分布：
> - 基石决策 §1–§10 全文 → `docs/decisions.details.md` Part A
> - 重构 / 工具链 / 回放 / RL 桥正文 → `docs/decisions.details.md` Part B–D
> - God AI 调优历史（含基线 / golden） → `docs/god-ai-tuning.progress.md`
> - 仿真 / 渲染性能 → `docs/perf-optimization.progress.md` / `docs/render-optimization.progress.md`
> - NN 训练与意图策略 → `docs/nn.progress.md`（总索引 + 旧号对照）/ `docs/nn/*.md`（主题档案）/ `docs/nn.progress.intent.md`
> - 功能 / 架构现状 → `docs/features.md` / `docs/architecture.md`
>
> **本条目的正文在哪（2026-09-23 起）**：索引行末尾的 `—— 全文（…）→ <路径> §N` 就是答案。
> 第 1–2 轮瘦身后，成篇正文只有两个去处：① 主题档（`docs/nn/*.md`、`docs/*.progress.md`）
> 末尾的 **「决策正文归档」** 节，锚点 `### §<本文件的编号>`；② `docs/decisions/details/<领域>.md`，
> 锚点 `## §<本文件的编号>`。读细节：先看索引行，再走指针，最后才回 git 历史。
>
> **旧路径约定（2026-10-01）**：本文件是**追加型日志** —— 条目里的文件 / 模块路径与**行号**是
> **写下当时**的坐标（早期 `rl/…` · `remote/protocol.py` · 算法栈五包等），**不就地改名**：改名会把
> 「当时那个文件的第 N 行」读成「今天这个文件的第 N 行」（**假精确**，比旧名更坑），且条目里大量行
> 本身就是旧→新映射 / 引文（改了自相矛盾）。**新写的条目用今天的名字**；读旧条目对不上时走对照：
> `nn-training/README.md` 模块图 · `docs/nn/engineering.md` §51–§55（§53 改名刀 · §55 两轮散文 keep 表）。
> 口径来源（含被否决备选）→ `§2026-10-01-goalnn-log-keeps-era-names`。
>
> **历史编号占位**（外部文档仍引用的旧编号，正文已归档）：
>
> | 编号 | 主题 | 现落点 |
> |---|---|---|
> | §21–§26 | 渲染 / 仿真性能四轮压榨 | `docs/perf-optimization.progress.md` |
> | §31 | 超级道具背包累积制 | `docs/features.md` §1.3 |
>
> **编号冲突注**：同号多条均为历史分支并入撞号（§75/§165/§182/§293–§304/§307/§354/§355/§361，
> 其中 §302 有三条），各条均被外部引用，保持不动；完整计数以 `tools/decisions-baseline.json` 为准。
>
> **编号规则变更（2026-09-08，plan/decisions-governance.md）**：旧编号 §1–§384 是
> **冻结历史契约**（永不重排、永不删除）；**自治理生效起新条目改用日期 ID
> `§YYYY-MM-DD-<branch>-<slug>`**（branch 去连字符；首个样本 = §2026-09-08-decisions-governance）。
> 新 ID 本地可生成（写前先 tail 查当日已有 ID 防重）；基线中不存在的 ID 出现两次即撞号，
> `tools/check-decisions.ts` 直接 fail。
>
> **准入三问（答 No 就不写）**：① 有没有被否决的备选方案？ ② 未来 agent 会不会重犯/重查？
> ③ 能不能就近表达（代码注释 / 测试断言 / 配置字段 / docstring）？真决策/禁令/铁律 → 本文件；
> 实验记录/调优 → `docs/*.progress.md`（禁止双写）；bugfix/UI/运维 → commit message。
>
> **归档约定**：长正文已收编为「编号 + 一句话 + 指针」索引行（实验数据 → progress 文档指针，
> bugfix → commit 承载）；查细节先走指针，再查 progress，最后才回提交历史。旧全文存留于
> ccf49ff^（瘦身前全文版，git 历史为准）；**2026-09-23 第二轮瘦身**（107 条正文 >10 行的条目）
> 把这批正文搬进上节说的两个去处，索引行压到「标题 + 决定（≤~12 行）+ 指针」。
> 写法见 `docs/decisions/HOW-TO-ADD.md`；正确性由 `bun run check`
> 里的 `tools/check-decisions.ts` 强制（撞号/丢号/新条目格式；单条正文 >40 行会给警告）。

---

### 铁律清单 (Iron Laws)

> 防重犯必读。全文在对应条目；以下为最短形式。

| 铁律 | 出处 | 备注 |
|---|---|---|
| 课程引用权重（bc / init 等）常备双份于 `nn-training/weights/in-use/`，防 tmp 清理误杀 | §379 | 无条件遵守；丢失须从备份恢复，禁「裸奔重训/换 ref 救场」 |
| TrainingLoop 不许静默失败：退出看护 + 失败写日志 | §380 | 状态机(exited) ≠ 日志；失败必须写进日志文件 + 可查询字段 |
| intent 后端永不用于 p 系执行（credit-assignment 家族全部剔除） | §362 | 天花板锁死略逊于 God，无论稀疏度如何不回头 |
| 仓库内 PowerShell 调用一律 `pwsh` 7，禁裸 `powershell`（inbox 5.1） | §323 | 落 AGENTS §17.7 |
| 「读课程某字段」的任一第二实现，一律以课程文件为唯一来源 | §384 | 禁止在别处硬编码同义常量 |

---

### 勿重提清单 (Do-Not-Re-Investigate)

> 以下结论已在重构审计中反复确认，未来 agent 不要再重查或推翻。完整依据见 `plan/refactor.trae.md` §0.5。

| 项 | 结论 | 依据 |
|---|---|---|
| `Tank` 实体字段拆分 | 不拆。42 字段逐字段 grep 读写方，零死字段；spread 克隆零漂移。 | refactor.trae.md §0.5 / zcode §257 |
| `WorldSerializer` 改实体级 spread 为字段级手写 | 维持现状（实体已走 spread 克隆，护栏只需守 World 顶层且已完备）。 | refactor.trae.md §0.5 / zcode §257 |
| 主题 `skipSvg` 分支删除 | 保留。`skipSvg = (themeKey !== 'modern')` 是活代码。 | agy §3.1 / §252 |
| 测试子目录化 | 不做（churn 高、零玩家价值）。仅廉价改名已做。 | agy §3.5 / zcode §269 |
| `byId` 事件字段删除 | 保留。`simulation-runner` 取证工具在消费它。 | zcode P1 |
| `applyPowerUp` switch → 派发表 | 维持。switch 本身就是派发表，形态合理。 | zcode 复核 |
| 大文件机械搬移（无测试网领域） | 先补特征测试再动手（高返工率）。 | zcode 教训 3 |

> **重构计划文件保留约定**：重构计划文件（如 `plan/refactor.agy.md` / `plan/refactor.trae.md`）除非其内容已并入本文件，否则保留在仓库内，不得删除；其否决结论以本文件 § 编号或上方「勿重提清单」作持久落点。（`refactor.zcode.md` 已删、仅留历史于本文件与 refactor.trae.md 顶部，未来引用一律改指 `refactor.trae.md`。）

---


### 基石决策 §1–§10（全文 → docs/decisions.details.md Part A）

| 编号 | 一句话 | 交叉引用 |
|---|---|---|
| §1 | 精灵全部手写 SVG（96×96），SpriteCache 按 DPR 预光栅化；零 PNG 资产 | details Part A §1 · architecture §2 · features §6.1 |
| §2 | 音效全部 Web Audio API 运行时合成；零音频文件 | details Part A §2 · architecture §2 · features §6.1 |
| §3 | 26×26 子格网格，16px 子块；坦克 2×2（32px）；战场 416×416 | details Part A §3 · architecture §3 · features §1.2 |
| §4 | 关卡数据用 TypeScript 配置（stages.ts + stageData.ts），构建期打包，无异步加载 | details Part A §4 · architecture §2 · features §1.1 |
| §5 | 35 个原版 FC 关卡，13×13 数字码无损解码为 26×26 字符网格 | details Part A §5 · features §1.1 |
| §6 | 垂直轴每帧吸附最近 16px 网格边界，允许 1 格走廊通行 | details Part A §6 · architecture §3 |
| §7 | 敌方 AI 单管线三时间尺度，智能是配置（ai/config.ts）非代码 | details Part A §7 · features §4 |
| §8 | 固定 1000/60ms 时间步累加器，每渲染帧最多 5 步 | details Part A §8 · architecture §3 |
| §9 | 逐帧边沿检测 + last-pressed-wins 移动栈 | details Part A §9 · architecture §3 · features §1.4 |
| §10 | 任一基地子块中弹即摧毁全部子块（经典行为） | details Part A §10 · features §1.2 |

---

### Architecture Decisions

| Decision | Detail |
|----------|--------|
| Presentation layer (event-driven, read-only, canvas 416×416 + HTML HUD) | `docs/architecture.md` §4 |
| DPR-aware rendering (offscreen buffer + DPR-scaled display canvas) | `docs/architecture.md` §4 |
| Animation system (time-based, VisualComponent) | `docs/architecture.md` §4 |
| Particle system (pool-based, pre-allocated) | `docs/architecture.md` §4 |
| Camera system (shake + offset) | `docs/architecture.md` §4 |
| Theme system (ThemeColors + CSS variables) | `docs/architecture.md` §4 |
| State transitions (CSS-animated HTML overlays) | `docs/architecture.md` §4 |
| Determinism (seeded RNG, `Math.random()` banned in Simulation) | `docs/architecture.md` §7 |
| InputLike interface (Simulation depends on interface, not concrete Input) | `docs/architecture.md` §3 |
| Generic pathfinding (`utils/pathfind.ts`, A* + BFS + flood-fill) | `docs/architecture.md` §3 |
| Stage loading API (`World.loadStageData`) | `docs/architecture.md` §8 |
| Level generator (7-layer procedural pipeline) | `docs/architecture.md` §11 |

### Gameplay Feature Decisions

| Decision | Detail |
|----------|--------|
| Combat capability system (6-dim `CombatProfile`, 300-point budget, derived stats) | `docs/features.md` §3 |
| Fire-rate standard (per-kind table, 3-bullet math anchor, per-fire jitter) | `docs/features.md` §3 |
| HP level visual aura (6-tier light rings, dynamic degradation) | `docs/features.md` §3 |
| Spawn-rolled 5-tier AI (commander succession, compliance, floor/cap) | `docs/features.md` §4 |
| Centralized scoring (kill/clear/item formulas, per-difficulty/stage/tier) | `docs/features.md` §1 |
| Item drop rules (elite kills + every-10-kills, super power-ups 10%) | `docs/features.md` §1.3 |
| Gameplay rules (per-difficulty rule profiles, classic faithful feel) | `docs/features.md` §1.2 |
| Timed power-ups stack duration on re-pickup | `docs/features.md` §1.3 |
| Enemy dead-end shaft recovery (tunnel out of 1-wide channel) | `docs/features.md` §4 |
| Snapshot management framework (one model, four origins, policy-driven retention) | `docs/architecture.md` §7 |
| Recovery-screen UI state guards extracted to pure predicates (`uiFlowGates.ts`) | 修复 Recovery 屏按钮因状态守卫漏 `'recovery'` 而死/报错；抽为无 DOM 纯谓词后可无头回归。→ `docs/decisions.details.md` 附录 A1 · `tests/recovery-screen-flow.test.ts` |

### God AI Tuning

Full history in `docs/god-ai-tuning.progress.md`. Key milestones:

| Phase | Outcome |
|-------|---------|
| Infrastructure (CMA-ES, decision tracing, simulation pool) | `docs/god-ai-tuning.progress.md` §3.1 |
| P0–P3 deadlock fixes (anti-camp, wider dodge, A* dig-through-brick) | `docs/god-ai-tuning.progress.md` §3.2 |
| P4 all-35 floor-aware tuning (81.9%→87.7%, 0/35 below floor) | `docs/god-ai-tuning.progress.md` §3.3 |
| Round 5 S33 close-combat (t2aMaxRange=2, 72.5%→85.0%) | `docs/god-ai-tuning.progress.md` §3.4 |
| Phase A SmartThreatModel (rejected, 8+ variants all negative) | `docs/god-ai-tuning.progress.md` §3.5 |
| §47 base protection ring collision fix (real S33 breakthrough) | `docs/god-ai-tuning.progress.md` §4 |
| §48 terrain-occlusion evasion (rejected, terrain-blind is load-bearing) | `docs/god-ai-tuning.progress.md` §4 |
| §49/§52 muzzle-to-muzzle (v1 rejected, v2 counter-fire neutral) | `docs/god-ai-tuning.progress.md` §9 |
| §67 stop tuning at 88.5% (flat optimum confirmed) | `docs/god-ai-tuning.progress.md` §4 |
| §68 crossfire awareness v2 (negative -1.1pp, default OFF, infra preserved) | `docs/god-ai-tuning.progress.md` §10 |
| §69 crossfire terrain-gate + A* threat cost (both negative, infra preserved) | `docs/god-ai-tuning.progress.md` §10 |
| §70 base-ring fire guard (T2b/aggressive break-through + T6 steel ring + post-loop baseSteel) | `docs/god-ai-tuning.progress.md` |
| §71–§95 Classic 纪元里程碑 | `docs/god-ai-tuning.progress.md` Part I |
| §96–§130 v2 重设计纪元 M0–M13 | `docs/god-ai-tuning.progress.md` Part II |
| §131–§234 方向 A–E / 守卫族 / 督战双玩家 / ThreatBudget 纪元 | `docs/god-ai-tuning.progress.md` |
| §272 v1 封版冻结（三件套协议 + 冻结基线 + golden） | `docs/god-ai-tuning.progress.md` Part 0 |
| §293-God AI 解冻 + 恢复超级道具（2026-08-28 新基线） | `docs/god-ai-tuning.progress.md` Part 0.1 |

> **Current state（2026-08-28 解冻纪元，eval-suite v7 · 35 关 × 60 seeds）**：hard（主）SUITE 0.5403 ·
> 胜率 75.3% · 最弱关 Battlement 23%；classic 89.5%；chaos 70.6%。完整基线与 golden →
> `docs/god-ai-tuning.progress.md` Part 0.1（§293-God AI）。此后任何 God-AI 行为改动 = 新纪元，
> 必须走三件套（新 DECISIONS 条目 + 60-seed 三难度基线 + 更新 det-golden），见 §272。

> **§2.3 压缩状态（refactor.trae.md）**：§71–§169 条目已统一压缩为 `docs/*.progress.md`
> 指针（全文 → 共 165 处）；本索引各条保持「编号 + 标题 + 状态 + 指针」形态，无冗余正文。

---


## 71. §48-Revisit: Steel-Only Evasion Occlusion, Terrain-Gated (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 72. §49-Revisit: 炮口相向对枪抵消 Parameterized + Re-Validated (SHIPPED, default unchanged) —— 全文 → docs/god-ai-tuning.progress.md

## 73. §68-Revisit: Crossfire Awareness v2 Re-Tuned with per-seed tick-diff (REJECTED, stays OFF) —— 全文 → docs/god-ai-tuning.progress.md

## 74. Steel-Fire Gate: Never Fire at Unpierceable Steel to Break Through (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 75. §75: Distance-Aware Base-Wall Fire Guard (T2a/Aggressive Suicide Fix) —— 全文 → docs/god-ai-tuning.progress.md

### Performance Optimization —— 全文 → docs/perf-optimization.progress.md

### Lie-Back-Win-Mode (Coop God AI) —— 全文 → docs/god-ai-tuning.progress.md

### Render Optimization —— 全文 → docs/render-optimization.progress.md

## 70. Base-Ring Fire Guard (Never Destroy Own Base) —— 全文 → docs/god-ai-tuning.progress.md

## 75. Replay Recording Must Tap the Decorated Input (Lie-Back-Win-Mode desync) (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 76. The Packed Blob Is the Only Authority on Frame Schema (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 77. Playback seek must advance the input (drag-the-bar desync) (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 78. Seek catch-up must drain (discard) world events — no audio burst (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 79. Coop God AI drove P1 instead of P2 (replay stall + base-wall break) —— 全文 → docs/god-ai-tuning.progress.md

## 80. §80: Turn-Snap Aim Guard — Don't Commit to a Stop-and-Aim Turn Whose Grid-Snap Breaks the Firing Line (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 82. 督战模式（Supervise）— God AI 作为 player1 全程无人类输入 + 战斗速率快捷键 (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 81. 移除 godai-stage-overrides.ts 机制 — 禁止按关卡名特殊化（防过拟合） —— 全文 → docs/god-ai-tuning.progress.md

## 83. §83: dodgeDirection 回退分支不再沿炮弹飞行方向逃跑 — 受困走廊时回头对枪抵消 (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 84. BONUS TIME: God AI Collects the Remaining Power-ups in the Pickup Window (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 85. §84-Revisit: BONUS TIME Pickup Is Reachability-Aware — Never Chase an Unreachable Item (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 86. Snapshot Must Preserve the Bonus Pickup Window — Mid-Window Restore Never Re-Opens BONUS TIME (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 87. Replay Needs No Pickup-Window Changes — It Inherits §86 via the Shared Serializer (VERIFIED + GUARDED) —— 全文 → docs/god-ai-tuning.progress.md

## 88. §88: Aggressive branch stall detection — freeze window no longer wasted firing at nothing (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 89. §89: Close-range enemy exposure check — don't flee from point-blank enemies (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 90. Dodge Direction Persistence + Threat Hysteresis (Bug Fix) —— 全文 → docs/god-ai-tuning.progress.md

## 90b. §90 A/B Test Results — Oscillation Counter-Fire Shipped (Negative Results Recorded) —— 全文 → docs/god-ai-tuning.progress.md

## 91. Turn Cooldown (§90c) — Simulation-Layer Oscillation Prevention —— 全文 → docs/god-ai-tuning.progress.md

## 92. §87: Urgent Power-Up Pickup Priority — Close + Safe-Path Pickups Outrank Defense/Kill (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 93. §88: 据守咽喉要地 (Chokepoint Holding) — Rule-1/2/3/4 Base-Defense Strategy (CANDIDATE, A/B-Tuned) _(superseded by §94 — SHIPPED default ON)_ —— 全文 → docs/god-ai-tuning.progress.md

## 94. §88 据守咽喉要地 (Chokepoint Holding) — SHIPPED (default ON, supersedes §93 candidate) —— 全文 → docs/god-ai-tuning.progress.md

## 95. Turn Cooldown 50ms → 100ms + Halt-During-Cooldown (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 96. M0 基线测量 + M0.5 僵尸参数退役（SHIPPED，2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 97. §M3 Dodge 质量：dodge 分支近距离对枪抵消（SHIPPED 后回退） _(superseded by §98)_ —— 全文 → docs/god-ai-tuning.progress.md

## 98. §M3 Dodge 对枪抵消：回退 OFF + Gate 确定性根因修复（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 99. M1 决策链评分制外壳：落地 + Parity 三重验证通过（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 100. M2 权重数据化：actionWeights 基础设施 + classic 重排 A/B 诚实阴性 + M2b 推迟（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 101. M3 dodgeCounterFire 三轮门控全部官方口径阴性 + stageIndex 口径伪影完整机制（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 102. M3 敌情感知 EnemyModel + survive 候选 + 命数感知 + M4 紧急对枪：机制落地，默认 OFF（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 103. M5 站位提前规避（pathThreatAvoidance）：机制落地，A/B 阴性，默认 OFF + 口径事故根因（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 104. M6 出生即一星（playerStartLevel 0→1）：首个强信号发布，hard/chaos +8~9pp（SHIPPED，2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 105. M7 追猎死亡探针：真追猎仅 ~3-7% + 模拟口径三重修复（playerLevel / lives / telemetry）（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 106. M8 survivalRetreat 官方口径 60-seed 确认：持平偏负，不发布（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 107. M9 dodgeHorizonScore 多弹道生存视界承诺闪避：机制成立但 60-seed 阴性，不发布（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 108. M10 dodgeHorizon 门控变体（时间余量 + 距离）：chaos 确凿阴性，不发布（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 109. M11 星经济下一档：playerStartLevel 1→2（SHIPPED 后用户否决） _(superseded by §110: 用户否决，回退 1★，2026-08-03)_ —— 全文 → docs/god-ai-tuning.progress.md

## 110. 用户否决 §109：hard/chaos 起始二星回退为一星（2026-08-03） —— 全文 → docs/god-ai-tuning.progress.md

## 111. 星盾扩展到所有难度（引擎改动）+ HP 模型探针选靶（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 112. M12 玩家 HP 缓冲感知：诚实阴性（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 113. M13 全场压力撤退（outnumberedFieldRetreat）：SHIPPED，hard +2.3pp / chaos +0.6pp（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 114. M4 标量参数 CMA-ES 首轮：子集过拟合阴性 + M13 阈值双重复证（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 114.1 M4 round-2 建议配置（评审决议，未执行） —— 全文 → docs/god-ai-tuning.progress.md

## 115. M4 round-2 全语料 CMA-ES：SHIPPED，pool 模型 +5.0/+8.3pp（classic 还原表保 91%）（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 116. 自杀秒回（suicide quick-return）：实现 + 诚实阴性（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 117. 自杀秒回条件①变体（mode 2 STAND / mode 3 CHARGE）：诚实阴性（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 118. §117 守卫升级（baseHp 阈值 + 防守位失守）A/B — 仍为诚实阴性，机制性证伪（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 119. 固化策略调试方法论：run-forensics 分层取证（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 120. 自毁基地 32 局取证 + 采集脚本迭代（off-by-one / bullet-dir / --from-json）（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 121. t2a/aggressive 停射自毁守卫 selfFireBaseGuard SHIPPED（2026-08-04） —— 全文 → docs/god-ai-tuning.progress.md

## 122. 仿真性能 Round 10：computeThreatPoints 对齐枚举（SHIPPED，2026-08-05） —— 全文 → docs/perf-optimization.progress.md

## 123. 仿真性能 Round 10：scanAheadImpl per-tick memo（SHIPPED，2026-08-05） —— 全文 → docs/perf-optimization.progress.md

## 124. 仿真性能 Round 10（REJECTED）：rectHitsTerrain 比较链重排 / terrain 短路 —— 全文 → docs/perf-optimization.progress.md

## 125. 仿真性能 Round 10：selectTarget within-tick memo（SHIPPED，2026-08-05） —— 全文 → docs/perf-optimization.progress.md

## 126. 仿真性能 Round 10（REJECTED）：canStepLat 手内联 rectHitsTerrain —— 全文 → docs/perf-optimization.progress.md

## 127. 仿真性能 Round 11：followPath→replanImpl 跨 tick 缓存（SHIPPED，2026-08-05，含引用别名修复） —— 全文 → docs/perf-optimization.progress.md

## 128. 性能基线标准场景改为 classic/hard/chaos 各 1/3（SHIPPED，2026-08-05） —— 全文 → docs/perf-optimization.progress.md

## 129. pickup 可达性 A*：dig-only + 跨 tick 纯 memo（SHIPPED，2026-08-05） —— 全文 → docs/perf-optimization.progress.md

## 130. 全难度命数统一为 3 + GOD AI 基线重测（SHIPPED，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 131. T8 拦截射程 pool 2→8/12：60-seed 诚实阴性（不发布，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 132. 方向 B：selectTarget 威胁评分按 kind 速度 × 距基地距离加权（诚实阴性，不发布，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 133. 方向 C：brick-heavy 关防守距离再校准——诚实阴性（不发布，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 134. 方向 D：防守位停射拦截基地车道敌人（SHIPPED，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 135. 方向 D 预测版：提前拦截基地车道逼近者（诚实阴性，不发布，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 138. 基地守位格 v2：受威胁时驻守守位格（诚实阴性，不发布，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 137. 基地守位格（Base Guard Anchor）—— 诚实阴性，不发布（2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 136. 方向 D 破砖版：预测命中时打场景砖开路（诚实阴性，不发布，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 139. 方向 A：火力死区解除（firing-lane re-engage）—— 灾难性阴性，不发布（2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 140. 方向 D4：baseWall 精确环判定（破砖开火假阳性修复，SHIPPED，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 141. D2 拆环威胁评分 —— 诚实阴性（旋钮默认 0，byte-identical） —— 全文 → docs/god-ai-tuning.progress.md

## 142. D1 防守落点解盲 —— 诚实阴性（baseGuardAnchorMode 保持 0） —— 全文 → docs/god-ai-tuning.progress.md

## 143. D5 基地火力解锁 + 星经济 —— 诚实阴性（firingLaneBoxRow / pickupStarBoxRow 保持 0） —— 全文 → docs/god-ai-tuning.progress.md

## 144. E1 道具经济（危急道具拾取）—— 诚实阴性（direItemMode 保持 0，反证判据收束） —— 全文 → docs/god-ai-tuning.progress.md

## 145. S24 冰面机制深潜 + iceGlideControl —— 诚实阴性（旋钮保持 0，S24 = 难度地板关） —— 全文 → docs/god-ai-tuning.progress.md

## 146. S8 Riverbed 取证深潜 + defensePosStandable —— SHIPPED（集合点可达性修复，hard 45%→52%） —— 全文 → docs/god-ai-tuning.progress.md

## 147. S8 三杠杆 B/C/A 逐一 A/B —— B SHIPPED（§146 已记），C/A 诚实阴性（§146 C 范围限制 + A 全局崩盘） —— 全文 → docs/god-ai-tuning.progress.md

## 148. fieldRetreatPickupGate 扩展到 MID/LOW —— 实测证伪后回退（HIGH-only 定稿，§147 范围锁定） —— 全文 → docs/god-ai-tuning.progress.md

## 149. defensePosStandable 全面启用（minDist 解除）全关验证 —— 边际 ≈ 0，不发货（收窄版 §146 保持最优） —— 全文 → docs/god-ai-tuning.progress.md

## 150. 关卡序号统一为 1-based（工具 CLI + 文档 S# 全量修正，2026-08-05） —— 全文 → docs/god-ai-tuning.progress.md

## 152. hard S12 Lattice 回放四联 bug 修复（§152-W1..W4）+ 全关 A/B 验证（SHIPPED） —— 全文 → docs/god-ai-tuning.progress.md

## 153. hard S12 Lattice seed 3214953618 回放两行为（bullet-crash + close-combat trade）诊断与修复（实现 + 单测锁定；A/B 发现两者全局非正 → 实验旋钮不发货） —— 全文 → docs/god-ai-tuning.progress.md

## 154. bulletLaneWait W1 重设计（§153 后记）：18 个净负种子根因定位 + predictive next-body 最终版（实测 35×60 hard 净 +15；仍为实验旋钮默认 0） —— 全文 → docs/god-ai-tuning.progress.md

## 155. bulletLaneWait W1 全局发货（§154 最终版，用户决策：忽略 chaos） —— 全文 → docs/god-ai-tuning.progress.md

## 156. Freeze-Window Power-Up Pickup（冰冻期道具拾取，无限距离） —— 全文 → docs/god-ai-tuning.progress.md

## 157. Base Clear-Shot Threat Detection（基地车道对齐远距离威胁检测） —— 全文 → docs/god-ai-tuning.progress.md

## 158. Non-Freeze Close-Range Power-Up Pickup（非冰冻期近距离道具拾取） —— 全文 → docs/god-ai-tuning.progress.md

## 159. 天降神兵守卫改用 GOD AI + §避让防堵车（用户需求 2026-08-06） —— 全文 → docs/god-ai-tuning.progress.md

## 160. 避让中扫射压制——避让开火优先沿腾挪轴（用户需求 2026-08-06） —— 全文 → docs/god-ai-tuning.progress.md

## 161. §161 开路策略（carve path）——实现完整、hard 全 35 关与 Battlement 均实测净零 → 诚实阴性归档，旋钮默认 OFF（用户需求 2026-08-06，Stage 33 Battlement 过关思路） —— 全文 → docs/god-ai-tuning.progress.md

## 162. §162 nav 卡死破局（navBreakStuck carve-dig escape）——SHIPPED 默认 1，hard 全 35 关显著胜率提升 p=0.019（用户需求 2026-08-06，回放 hard-s34-base-l2-t69-seed2050197249 Problem 1：出生点被砖墙围堵，player 不开墙出击，0:00~0:20 在出生点附近振荡） —— 全文 → docs/god-ai-tuning.progress.md

## 163. §163 中路防守（midLaneDefense）——子弹触发版全 35 关与 Battlement 均实测净零 → 诚实阴性归档，旋钮默认 OFF（用户需求 2026-08-06，回放 Problem 2：基地列无钢铁防护，player 坐视敌人凿穿中路砖墙） —— 全文 → docs/god-ai-tuning.progress.md

## 164. §164 中路列旁主动驻守（midLaneHold）——诚实阴性归档（用户需求 2026-08-06：让 §162 出袋后的玩家优先走中路走廊而非左侧，在列旁持枪对消） —— 全文 → docs/god-ai-tuning.progress.md

## 165. T2a Defense Override — 近敌停射允许（修复 S20 Bastion 振荡死锁，origin 侧原 §159） —— 全文 → docs/god-ai-tuning.progress.md

## 165. 中路防守启用 + 水阻弹 bug 修复 + 近战对枪火力评估 —— 全文 → docs/god-ai-tuning.progress.md

## 165-round2. 深度调优：pathThreatAvoidance 假阳性 + closeCombatDuel 多敌计数 + midLaneHold 主动防守 —— 全文 → docs/god-ai-tuning.progress.md

## 166. B1 starRush 星经济冲刺 — 诚实阴性归档（旋钮默认 0，2026-08-07） —— 全文 → docs/god-ai-tuning.progress.md

## 167. B4 超级道具战略激活（superItemMode）— SHIPPED guard-only → RETIRED by default（2026-08-07 → 修订 2026-08-25 M0） —— 全文 → docs/god-ai-tuning.progress.md §M0

## 168. navStuck 计数器抖动重置 bug（navStuckZone）— 实验阴性，旋钮留档默认 0（2026-08-07） —— 全文 → docs/god-ai-tuning.progress.md

## 169. 基地威胁信号闪烁（threatStickyTicks）— 立项（2026-08-07） —— 全文 → docs/god-ai-tuning.progress.md

## 170. 追击承诺（huntCommitTicks）— 立项（2026-08-07） —— 全文 → docs/god-ai-tuning.progress.md

## 171. 路径长度感知目标选择（pathTargetMode）— 立项（2026-08-07） —— 全文 → docs/god-ai-tuning.progress.md

## 172. bonus 敌人追猎权重（bonusHuntBias）— 立项（2026-08-07） —— 全文 → docs/god-ai-tuning.progress.md

## 173. 基地损伤召回（baseDamageRecall）— 立项（2026-08-07） —— 全文 → docs/god-ai-tuning.progress.md

## 174. 双玩家仿真系统 — 双 God AI 协作 + 防堵车 + 督战双玩家 (SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 175. Dual 中路无钢关配合策略 — 立项（2026-08-08） —— 全文 → docs/god-ai-tuning.progress.md

## 176. Dual Central Breach §6 实测缺陷修复 — P2 角色落地 + P1 dig-fire —— 全文 → docs/god-ai-tuning.progress.md

## 177. Dual Central Breach P2 导航落地 — directMove/patrol 实测回退，de-conflict 生效 —— 全文 → docs/god-ai-tuning.progress.md

## 178. Dual Central Breach autopsy (hard-s34 seed2) — carve 穿墙 + 中驻守 + sticky hold —— 全文 → docs/god-ai-tuning.progress.md

## 179. Dual Central Breach autopsy (hard-s34 seed6) — P1 凿盾 + 危基不回防 + 冰冻浪费 —— 全文 → docs/god-ai-tuning.progress.md

## 180. Dual Central Breach autopsy (hard-s34 seed34) — 右路盲区 + fence 独占 + defenseSecond 近端覆盖 —— 全文 → docs/god-ai-tuning.progress.md

## 181. Dual Central Breach autopsy (hard-s34 seed115) — P1 spawn 振荡：A* 路由穿透基地保护砖 —— 全文 → docs/god-ai-tuning.progress.md

## 182. 重放暂停后切换应用再回来点播放，画面不动（visibilitychange 污染 world.state） —— 全文 → docs/god-ai-tuning.progress.md

## 182. Face-Nearest-Enemy Fallback for Immobile-Stuck Player —— 全文 → docs/god-ai-tuning.progress.md

## 183. GOD AI Idle Calibration — Analysis Complete —— 全文 → docs/god-ai-tuning.progress.md

## 184. Freeze Powerup — Allied Guard Freeze Bug + Pickup Stuck Bug —— 全文 → docs/god-ai-tuning.progress.md

## 185. navStuckZone=1 — Sub-Pixel Jitter Defeats Nav-Stuck Counter —— 全文 → docs/god-ai-tuning.progress.md

## 186. powerupStuckTicks — Powerup Navigation Stuck Detection —— 全文 → docs/god-ai-tuning.progress.md

## 187. Guard/P2 A* Player-Obstacle + Target Blacklist + Fire Post-Turn + Powerup-Enemy Overlap —— 全文 → docs/god-ai-tuning.progress.md

## 188. Fence Power-Up Must Not Trap Tanks Inside Steel —— 全文 → docs/god-ai-tuning.progress.md

## 189. 开局联通清墙 — Base Connectivity Clear —— 全文 → docs/god-ai-tuning.progress.md

## 190. A* 寻路代价模型升级 — 砖墙=空地 + 基地环倍率 + 开火停车代价 —— 全文 → docs/god-ai-tuning.progress.md

## 191. 批量仿真共享态硬化 — findPath 重入守卫 + level-sim 子进程隔离 —— 全文 → docs/god-ai-tuning.progress.md

## 192. 基地车道哨兵（baseLaneSentry）—— SHIPPED（hard/chaos 默认 1；classic 0 保持字节不变） —— 全文 → docs/god-ai-tuning.progress.md

## 193-A. 中线火力门（centerLineFireGate）— 标注重评（被 §193-C 取代） —— 全文 → docs/god-ai-tuning.progress.md

## 193-C. 中线火力门 — SHIPPED（hard/chaos 默认 1；classic restore 0 字节不变） —— 全文 → docs/god-ai-tuning.progress.md

## 193-D. 预测前移门（predictiveFireGate）— SHIPPED（三难度默认 1；classic restore 0） —— 全文 → docs/god-ai-tuning.progress.md

## 193-B. 卫位导航（baseLaneSentryStation）— 达标保留（默认 0 = OFF，待发货） —— 全文 → docs/god-ai-tuning.progress.md

## 193-E. 环破回防（ringFallback）— 阴性归档（S34 -2，hard 全关 -54） —— 全文 → docs/god-ai-tuning.progress.md

## 194. 像素卡死 directMove 兜底 (§190) —— 全文 → docs/god-ai-tuning.progress.md

## 195. 中路钻探粘性驻守 midLaneStickyTicks=90 — S8 Riverbed 钻探败链修复 SHIPPED (2026-08-14) —— 全文 → docs/god-ai-tuning.progress.md

## 196. 钻探预警列完整性触发器（drill alarm）— 方向 1 证伪（2026-08-14） —— 全文 → docs/god-ai-tuning.progress.md

## 197. 带内拆环导航 + 远距开火（baseLaneSentryInBandNav/FarRange）— 方向 2 证伪（2026-08-15） —— 全文 → docs/god-ai-tuning.progress.md

## 198. 卫位导航发货（baseLaneSentryStation=1）— SHIPPED（2026-08-15） —— 全文 → docs/god-ai-tuning.progress.md

## 199. S34 站桩取证 + 工具口径 bug（方向 3 证伪 + ab-param 修复）（2026-08-15） —— 全文 → docs/god-ai-tuning.progress.md

## 200. dodgeEscapeDepth 逃逸深度闪避证伪（方向 5 D1）（2026-08-15） —— 全文 → docs/god-ai-tuning.progress.md

## 201. 方向 6（clearSpeed 0.151 慢/拖沓）— 分析性证伪：无 AI 拖沓可修（2026-08-15） —— 全文 → docs/god-ai-tuning.progress.md

## 202. M0 威胁台账取证上线（threat-ledger + failure-classifier）— 硬关 2100 局基线（2026-08-15） —— 全文 → docs/god-ai-tuning.progress.md

## 203. M1 ThreatBudget 纯模型上线（Phase 1 §5，默认未接线）（2026-08-15） —— 全文 → docs/god-ai-tuning.progress.md

## 204. M2 ActionContract 防守站桩门控（Phase 2 §6.1，默认 OFF + A/B）（2026-08-15） —— 全文 → docs/god-ai-tuning.progress.md

## 205. §6.2 targetValue 排序键 — A/B 证伪，保持默认 OFF —— 全文 → docs/god-ai-tuning.progress.md

## 206. §6.3 短期 intent（lease+重验）— A/B 中性偏负，保持默认 OFF —— 全文 → docs/god-ai-tuning.progress.md

## 207. Phase 3 §7 动态攻击覆盖点 — A/B 证伪（S34 崩塌），保持默认 OFF —— 全文 → docs/god-ai-tuning.progress.md

## 208. §207 覆盖点实现缺陷审计 — 5 缺陷已修复，机制仍保持 OFF —— 全文 → docs/god-ai-tuning.progress.md

## 209. 覆盖点实现审计第二轮 — 坐标系根因 + (b)/BUG-2 修复，正确实现仍净负（OFF 维持） —— 全文 → docs/god-ai-tuning.progress.md

## 210. 覆盖点格坐标 round → floor（消除格中点决策振荡） —— 全文 → docs/god-ai-tuning.progress.md

## 211. 覆盖点负翻转 per-seed 取证 — 蝴蝶效应根因，csb/cbr 过滤修复证伪（OFF 维持） —— 全文 → docs/god-ai-tuning.progress.md

## 212. M4 安全吃星 — 诊断先行，收益空间不足（不提高 pickup 权重） —— 全文 → docs/god-ai-tuning.progress.md

## 213. Phase 5 CMA-ES — 启动决策与搜索空间重定义（§9.1 条件未满足，用户指示启动，按协议执行） —— 全文 → docs/god-ai-tuning.progress.md

## 214. Phase 5 CMA-ES — 参数面无 ROI，三批候选全部噪声，维持 DEFAULT（停止条件 §9.3.5 触发） —— 全文 → docs/god-ai-tuning.progress.md

## 215. Hard 开放测试第 1 轮: M0–M3 通过, idle 因果证伪(STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 216. M4 统一行动候选 — paired A/B 净 −116, 方向否决 (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 217. M5 travel 段火力偏离 — 诊断先行, 机会空间 33% (STATUS: 实现中, 未 A/B) —— 全文 → docs/god-ai-tuning.progress.md

## 218. M5 travel 段火力偏离 (fireLineDetourMode) — 三批全正向, 候选通过初步 A/B → gated rollout (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 219. 评审 P1 修复轮 (实验工具可信度 + 4 处 AI 缺陷) (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 220. defenseIntercept 开火窗口 (actionContractMode 独立 A/B) — 三线微负, 方向否决 (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 221. 评审 P2 修复 + M5 candidate-on 完整验证 (STATUS: 完成, M5 升格待拍板) —— 全文 → docs/god-ai-tuning.progress.md

## 222. CMA-ES 重启 — 全 stage 口径 (STATUS: 收口, 参数面无 ROI 确认) —— 全文 → docs/god-ai-tuning.progress.md

## 223. ③ dodge idle 取证收口 (STATUS: 完成, 候选方向待拍板) —— 全文 → docs/god-ai-tuning.progress.md

## 224. 候选 A: dodgeCentroidMode 否决 (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 225. 后继 ④ "太迟"防御结构审计收口 (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 226. 后继 ④ 候选 A/B 双否决 (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 227. 后继 ⑤ t2a 自毁守卫重论证收口 (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 228. M5 人工开放测试入口 (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 229. M5 fireLineDetourMode SHIPPED — 默认 1（含 S30/S13 弱关重标定）(STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 230. 门禁 runner 瘦身 — collectMetrics/collectEvents + telemetry Set ping-pong (STATUS: SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

## 231. thinkInterval 决策链节流 A/B — 否决 (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md

## 232. 决策链小数组分配消除 + scanAhead 整数步进 (STATUS: SHIPPED, 字节等价) —— 全文 → docs/god-ai-tuning.progress.md

## 233. bun test 全套 <20s 攻坚结论 — 机器吞吐墙 + 种子数决策 (STATUS: 完成, 10 种子落地) —— 全文 → docs/god-ai-tuning.progress.md

## 234. 门禁种子 20→10 后补 — test-silent HEAVY_TESTS 修复 + 强制 --parallel (STATUS: SHIPPED) —— 全文 → docs/god-ai-tuning.progress.md

### Render Optimization（§235–§238 渲染实测否决，全文 → docs/render-optimization.progress.md）

## 235. R6 vignette 缓存 1× 化 — 全屏 alpha blit 面积 4× 缩减 (STATUS: SHIPPED, 有损项已论证) —— 全文 → docs/render-optimization.progress.md R6（2026-08-17）

## 236. P1-C 粒子 per-type 分桶 — 精确测量后放弃 (STATUS: 否决, 实测数据入档) —— 全文 → docs/render-optimization.progress.md P1-C（2026-08-17）

## 237. R7 坦克 tight-viewport blit — 实测否决 (STATUS: 否决, 9-arg 调用开销抵消面积节省) —— 全文 → docs/render-optimization.progress.md R7（2026-08-17）

## 238. 粒子烘焙位图 blit — 实测 4× 慢，彻底证伪 (STATUS: 否决) —— 全文 → docs/render-optimization.progress.md R8（2026-08-17）

### Refactor & Engineering（§239–§271 重构落地，全文 → docs/decisions.details.md Part B）

## 239. §1.6 魔法数字 → 命名常量 (STATUS: 已实施, plan/refactor.agy.md Phase 1) —— 全文 → docs/decisions.details.md（Part B §239）

## 240. §1.5 Option C — WorldSerializer 字段覆盖测试守卫 (STATUS: 已实施, plan/refactor.agy.md Phase 1) —— 全文 → docs/decisions.details.md（Part B §240）

## 241. §3.2 快照/回放基础设施去重 (STATUS: 已实施, plan/refactor.agy.md Phase 1) —— 全文 → docs/decisions.details.md（Part B §241）

## 242. §2.8 方向助手整合 → utils/direction.ts (STATUS: 已实施, plan/refactor.agy.md Phase 1) —— 全文 → docs/decisions.details.md（Part B §242）

## 243. §3.4 共享测试 fixtures → tests/helpers.ts (STATUS: 已实施, plan/refactor.agy.md Phase 1) —— 全文 → docs/decisions.details.md（Part B §243）

## 244. §1.2 GameLoop.loop 分解为命名步骤方法 (STATUS: 已实施, plan/refactor.agy.md Phase 2) —— 全文 → docs/decisions.details.md（Part B §244）

## 245. §2.1 击杀管线抽取 → KillPipeline.ts (STATUS: 已实施, plan/refactor.agy.md Phase 2) —— 全文 → docs/decisions.details.md（Part B §245）

## 246. §2.2 P1/P2 生命周期集中化 → World.enablePlayer2/disablePlayer2 (STATUS: 已实施) —— 全文 → docs/decisions.details.md（Part B §246）

## 247. §2.3 自由格搜索统一 → GridQuery.findNearestFreeCell (STATUS: 已实施) —— 全文 → docs/decisions.details.md（Part B §247）

## 248. §2.9 + §3.8 时间单位命名约定 + AI 常量归位 (STATUS: 已实施) —— 全文 → docs/decisions.details.md（Part B §248）

## 249. §3.6 四套 Worker Pool 统一 → tools/lib/worker-pool.ts (STATUS: 已实施) —— 全文 → docs/decisions.details.md（Part B §249）

## 250. §2.7 pathfind.ts 解耦：utils → ai/god + grid-search (STATUS: 已实施) —— 全文 → docs/decisions.details.md（Part B §250）

## 251. §1.3 Phase C — highScore 持久化 I/O 迁 settings.ts (STATUS: 已实施) —— 全文 → docs/decisions.details.md（Part B §251）

## 252. §3.1 双渲染器 fallback 移除 — 否决（前提不成立） (STATUS: 否决) —— 全文 → docs/decisions.details.md（Part B §252）

## 253. §2.6 types.ts 重组织 — presentation-only 类型迁出 (STATUS: 部分实施) —— 全文 → docs/decisions.details.md（Part B §253）

## 254. §1.1 Mixin→组合：Simulation（21 stubs 归零） (STATUS: 已实施, plan Phase 3) —— 全文 → docs/decisions.details.md（Part B §254）

## 255. §1.1 Mixin→组合：Game（27 stubs 归零） (STATUS: 已实施, plan Phase 3) —— 全文 → docs/decisions.details.md（Part B §255）

## 256. §1.1 Mixin→组合：GameRenderer + SpriteArtist（41 stubs 归零） (STATUS: 已实施) —— 全文 → docs/decisions.details.md（Part B §256）

## 257. §2.6 types.ts 重组完成 + Tank 拆分否决 (STATUS: 已实施/部分否决) —— 全文 → docs/decisions.details.md（Part B §257）

## 258. §3.3 Browser 去重 — 最小提取（formatCreated/formatBytes） (STATUS: 已实施, 范围修正) —— 全文 → docs/decisions.details.md（Part B §258）

## 259. §3.7 diag 脚本清理 — 归档 5 个零引用一次性脚本 (STATUS: 已实施, 范围修正) —— 全文 → docs/decisions.details.md（Part B §259）

## 260. §3.5 tests 目录重组 — 否决（代价/价值失衡） (STATUS: 否决) —— 全文 → docs/decisions.details.md（Part B §260）

## 261. §2.4 UIManager 拆分 — 四子控制器组合 (STATUS: 已实施) —— 全文 → docs/decisions.details.md（Part B §261）

## 262. 废除 God AI 禁区（AGENTS §5.1 幽灵规则消歧） (STATUS: 已实施, plan/refactor.trae.md §0.1) —— 全文 → docs/decisions.details.md（Part B §262）

## 263. 第二轮重构落地汇总（plan/refactor.trae.md B1–B3） (STATUS: 已实施, 2026-08-23) —— 全文 → docs/decisions.details.md（Part B §263）

## 264. selectTargetUncached 分解落地（§263 遗留 #3） (STATUS: 已实施, 2026-08-23) —— 全文 → docs/decisions.details.md（Part B §264）

## 265. determinism 语料 v2（8→21 组合） (STATUS: 已实施, 2026-08-23, 遗留 #12) —— 全文 → docs/decisions.details.md（Part B §265）

## 266. manhattan 单源化落地（遗留 #2） (STATUS: 已实施, 2026-08-23) —— 全文 → docs/decisions.details.md（Part B §266）

## 267. 遗留 #1 self-hub 处置：结构性护栏替代整体切片 (STATUS: 已实施, 2026-08-23) —— 全文 → docs/decisions.details.md（Part B §267）

## 268. 第三轮重构 Phase 1 落地汇总（plan/refactor.trae.md §1） (STATUS: 已实施, 2026-08-24) —— 全文 → docs/decisions.details.md（Part B §268）

## 269. 第三轮重构 Phase 2 落地汇总（plan/refactor.trae.md §2） (STATUS: 已实施, 2026-08-24) —— 全文 → docs/decisions.details.md（Part B §269）

## 270. 第三轮重构 Phase 3 落地汇总（plan/refactor.trae.md §3） (STATUS: 已实施, 2026-08-24) —— 全文 → docs/decisions.details.md（Part B §270）

## 271. 第三轮重构 Phase 4 落地汇总（plan/refactor.trae.md §4） (STATUS: 已实施, 2026-08-24) —— 全文 → docs/decisions.details.md（Part B §271）

## 272. God AI v1 封版冻结 —— D0 拍板 + 冻结基线 + 签名 golden (STATUS: 已实施, 2026-08-26) —— 全文 → docs/god-ai-tuning.progress.md Part 0（冻结基线 / golden 完整）· 重启协议 → plan/God-AI-Organization.md §8

### God AI 冻结纪元运维（§273–§276，全文 → docs/decisions.details.md Part C）

## 273. 留档实验资产不删决策（OFF 旋钮 / OFF 候选 / 锁存测试全保留）(STATUS: 已实施, 2026-08-26) —— 全文 → docs/decisions.details.md（Part C §273）

## 274. sweep-winrate `--difficulties` 字符迭代 bug 修复 —— 列表参数走 assertive 解析器 (STATUS: 已实施, 2026-08-26) —— 全文 → docs/decisions.details.md（Part C §274）

## 275. God AI code-review 批量 bug 修复（冻结路径零行为变更）(STATUS: 已实施, 2026-08-26) —— 全文 → docs/decisions.details.md（Part C §275）

## 276. code-review 遗留项全清 —— 新纪元三件套执行（§275 遗留 → 全部落地）(STATUS: 已实施, 2026-08-26) —— 全文 → docs/decisions.details.md（Part C §276）

## 277. 全关策略 all-on 实验 — M0/M1 收口：all-on 灾难性否决 + LOO 定位 firingLaneMode (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md §239（编号修正：progress 内为 §239，2026-08-17）

## 278. all-on−firingLaneMode CMA-ES 两轮收口 — 数值调参止于 −10.4pp，方向关闭 (STATUS: 完成) —— 全文 → docs/god-ai-tuning.progress.md §240（编号修正：progress 内为 §240，2026-08-17）

## 279. Replay Tick-Hash Chain 实现定案 — 每 100 tick 世界哈希锚点 (STATUS: 完成) —— 全文 → docs/decisions.details.md（Part D §279）· 计划/评审 → plan/Replay-TickHash-Chain.md + plan/tickhash.review.md

## 280. 否决 RL-WASM-Bridge（B3），改走 A'（bun 持久进程桥）(STATUS: 已决议) —— 全文 → docs/decisions.details.md（Part D §280，含 v3–v5 评审附录）· 评审 → plan/RL-WASM-Bridge.review.md · 执行 → plan/RL-Bun-Bridge.md

### RL 训练基础设施（§281–§284，全文 → 见各条指向的主题档案）

## 281. RL 训练断点续跑机制（服务随时停启）(STATUS: 完成, 2026-08-23) —— 全文 → `docs/nn/legacy.md` §8（RL 训练断点续跑机制）

## 282. RL 队列模式静默跳轮修复 — resumed_manifests 双 schema 归一 + 失败迭代原地重试 (STATUS: SHIPPED, 2026-08-24) —— 全文 → `docs/nn/legacy.md` §9（队列模式静默跳轮修复 + 事故复盘）

## 283. 干净评估嵌入分布式流水线 — PPO 空窗期全节点贪心局（STATUS: SHIPPED, 2026-08-24） —— 全文 → `docs/nn/legacy.md` §11（干净评估嵌入分布式流水线）

## 284. 分布式协议 v3.6 — 结果容器 BCV2 子进程打包 + 任务获取异步化（STATUS: SHIPPED, 2026-08-25） —— 全文 → plan/distributed-rollout.md v3.6（原 `docs/nn.progress.md` 的分布式 BCV2 节在这次按主题重组中无对应节；早期流水线记录见 `docs/nn/legacy.md`）

## 285. M1 分歧探针 — 归因 ①/③ 边界（2026-08-26，plan/AI-No-Items-Warmstart.md §4）

## 286. 语料纪元 OBS_SCHEMA_MAJOR 1→2 — item 头删除 + 标量收编 + wins-only + returns（2026-08-26，plan M2）

## 287. M3 BC warm-start 双臂 — 双 0% WIN，Gate ≈0% → 回环 DAgger/长训（2026-08-26，plan §6）

## 288. M3 回环整改 — DAgger 混合亦 0%，BC 三线收敛 0% 平台（2026-08-26 晨）

## 289. 窗口 0 稳定化 gate — super-item 退役默认 OFF + 基线重钉三件套（2026-08-26，plan/Intent-Policy-NN-Plan.md §5.4）
> 全文 → docs/decisions.details.md（Part E §289）· pinned run 基线数据 → docs/god-ai-tuning.progress.md Part 0

## 290. M0a 词表契约定稿 — spec-in-code 单一实现（2026-08-26，plan/Intent-Policy-NN-Plan.md §3）
> 全文 → docs/decisions.details.md（Part E §290）· 规格原文 → plan/Intent-Policy-NN-Plan.md §3

## 291. M0b 探针轮 1 — gate FAIL，处置启动注入版（2026-08-26 夜）

## 292. M0b 探针轮 3（B′）— 判定口径修正，gate PASS，进入 M4（2026-08-26 夜）
> 全文 → docs/nn.progress.intent.md §18。注：cruise 桶负 margin 受分布 mismatch 污染，

## 293. M4 完成 — 网络 + 字节一致 + 推理基准 + IntentPlayer（2026-08-26 夜）

## 294. M1 压缩认定 + M2 人像签名完成（2026-08-26 夜）

## 295. M5 双臂完成 — A 臂 learnability 成立 + B′ 人像温和混合定向增益（2026-08-27 凌晨）
> 全文 → docs/nn.progress.intent.md §21–§23。

## 296. M6 仲裁修复 — reflex dodge 默认保留，仅 suppressDodge 显式压制（2026-08-27 凌晨）
> **结论**：reflex dodge 默认保留，仅对标注 suppressDodge 的候选（RETURN_DEFENSE）显式压制；行为级仲裁测试 8 例。

## 297. M7① 天花板探针 — 白名单分类法 bug 修复，oracle 47%→73.4%、NN 44.6%→72.3%（2026-08-27 凌晨）
> 全文 → docs/nn.progress.intent.md §24。

## 298. M5 训练脚本多根 + priority 配额（2026-08-27 凌晨）
> **工具**：train_intent_probe.py 多 data 根 + --priority-root 配额；eval_intent_m5.py 四必报项计算器。

## 299. M7① cadence 定稿 + risk-gated 负结果（2026-08-27 凌晨）
> 全文 → docs/nn.progress.intent.md §25。

## 300. M7② rollout 意图分布探针 — B′ vs SS 冷启动风险预评（2026-08-27）
> 全文 → docs/nn.progress.intent.md §26。

## 301. M8 RL 冷启动方案定案 — B′ 即开 + s32-35 人类胜利回放降级为辅助注入（2026-08-27）
> **决定**：M8 RL 直接用 B′ 冷启动（s32-35 人类胜利回放降级为辅助注入，离线增强被 RL 稀释不值）；记录 docs/nn.progress.intent.md §26–§27。

## 302. M8 意图 RL 落地 — 半 MDP 意图步 PPO + 变步长 GAE + B′ 冷启动 value 预热（2026-08-27）

## 293. God AI 解冻 + 恢复超级道具策略（super-item 战略激活）(STATUS: 已实施, 2026-08-28)
> 全文 → docs/god-ai-tuning.progress.md Part 0.1（2026-08-28 解冻纪元基线 / golden 重钉）

## 302. 追尾导航（pursuit-tail / 并入目标车道后方）— 三轮归档：用户规格的等待后并道（am=3）净 +29 为全程序最佳，仍噪声带内，维持 OFF _(STATUS 已被 §304 取代：2026-08-29 用户拍板启用，见下两条)_(原 STATUS: 否决, 默认 0 = OFF, 2026-08-29)
> 全文 → docs/god-ai-tuning.progress.md §302（§1–6 一轮 modes 1–6；§7–8 二轮

## 303. 护卫出生卡墙 bug 修复 — baseSideSpawnCell 兜底不再落墙 + golden 重钉 b9a629e0（STATUS: 已实施, 2026-08-29）

## 304. 启用追尾导航 — pursuitTailMode=7 + AlongMode=4 默认 ON（用户拍板，新纪元三件套完成；同日 am=4 增补见 dated note）(STATUS: 已实施, 2026-08-29)

## §294 Goal-Space 策略网络重建开工（2026-08-29，M9 时代启动）
> 全文 → `docs/goal-nn.progress.md`（§0–§3）· 规格 → `plan/Goal-Space-Policy-Rebuild.md`

## §295 路线转向：课程学习从零练执行器（2026-08-29，T9a 门② 之后）
> 全文 → `plan/goal-nn-action.md`（**实施手册**：任务卡含步骤 / 改动文件 / 验收 / 失败处置）·

## §296 大语料 rotate 战役（2026-08-30，用户指令）
> **结论**：S1 微课（固定 12 局/it）过拟合退役，改 repo 标准 rotate 语料（350 局/it、(rotateSeed,it) 键控）；kill2 修复后 17 iters 即 97–100% 过门；封顶 --max-hours 8 / --iters 40。

## §297 S1 过门后的战役修订包（2026-08-30，随 Phase-1 重启累积决策，一次归档）

## §298 机时豁免 + 监控红线 + 两处预注册补齐（2026-08-30 夜）

## §299 S2 门禁口径决策：按"全歼率"判，S2 过门（2026-08-31）
> **决定（用户拍板方案 A）**：S2 门按「全歼率」判（stage_clear 被 BONUS 窗口伪负污染）；落地 SimulationEffects.allEnemiesCleared + SimResult.cleared 全链 + m1-eval clearRate；实测 it16 权重全歼率 96.7%（58/60）≥80% ⇒ S2 过门。判定口径仅限 S2，S3+ 沿用。

## §300 S3 换臂 balanced，tmp/s3-cap2 新实验（2026-08-31，用户拍板"方案 B"）
> **结论**：kill2 下"以命换击杀"正期望致 S3 相对门字面判负；另开 tmp/s3-cap2（reward=toy:balanced，有效臂），旧 s3-cap 曲线存档不续用。执行 plan/s3-balanced-restart.md。

## §301 T4 双缓冲落地的两个隐式缺陷修复（2026-08-31，commit 85f3953）

## §302 S3 balanced 胜率崩塌 → 回滚 it4 峰值权重续训（2026-08-31，用户拍板"回滚"）
> **结论**：S3 balanced accuracy 三连降 + eval 70%→38% 触发风险线；用户拍板回滚 it4 峰值权重续训；it9 评估 75% 未复现"70% 后第一步崩塌"；KL/gnorm 逐轮爆炸（44-56/常 >0.08）列为观察项。

## §303 v3.10 长尾竞速：in-flight 尾部任务空槽即竞速（2026-08-31，用户指令"有空槽就派发"）

## §303a v3.11 竞速副本只派快节点（2026-08-31，用户观察"副本落到慢节点=白等"）
> **结论**：竞速副本只派 top-N 快节点（race_tier_ok 纯函数，local 豁免/无样本放行/退化为全员）；test_race_tier_ok 9 断言。

## §304 v3.12 eval 最低优先级：软等待 + 后台消化 + 集成测试（2026-08-31，commit e79ca5c）
> **结论**：eval 最低优先级——全额等待改软等待 ≤180s（后台消化晚入账，账按 wver16 去重）；I7 集成测试断言"不抢主链、后台消化"；实测一次 20min eval 与 PPO 重叠完成。

## §305 v3.13 提前预采首波：epoch3 快照 spawn + 双 wver 对账 + stream 首波注入（2026-08-31，commit c1e33db/3ebf8d2）

## §306 远控重启护栏：脏工作区拒发 + 跨代去重 + agent grace 窗口（2026-09-01）

## §307 RL 入口整合：run_rl_intent（含 --goal）并入 run_rl.py（2026-09-01，用户拍板" 直接删除）

## §307 RL 入口整合：run_rl_intent（含 --goal）并入 run_rl.py（2026-09-01，用户拍板直接删除）

## §308 RL 训练配置化 M1：公式引擎 + metrics.npy + 课程启动通道（2026-09-02，plan/rl-training-config.md v8）

## §308b M1 收尾：尾逗号容忍 + 课程/CLI 冲突 fail-loud + eval 双侧同规（2026-09-02，commit e828331 后续）

## §308c 评审 F1–F3 处理（2026-09-02，commit e828331/8b849e4/0dec734 之后）

## §309 新课程 S5-open20：20×20 空旷无基地 / 一命无星 / 20 敌 4 类混编（2026-09-03）
> **结论（S5-open20 课程）**：起始权重实测迁移第一 = s2-cap（域内强≠迁移强：S3 迷宫掩体策略在空旷 20 敌图失效）；奖励 = toy+杀敌超线性+/受伤/拾取/走位罚（不放 cellsVisited 防刷分）；一命满血→闪避只剩死亡二元负反馈（已知限制）。

## §310 S5 测试 iter 全链路体检：5 个修复 + 性能 profile + 激励函数检验（2026-09-03）

## §311 TS 推理性能调优结论：JS 标量循环已达上限，需结构性方案（2026-09-03）
> **结论**：model.forward ~39.6ms = JS/JIT 标量循环上限（37M MACs ≈1.9GFLOPS；三种实现均 ~39-43ms）；候选：WASM SIMD conv（最大单点收益）、onnxruntime、torch_threads 临时上调。

## §312 TS 推理提速①落地：conv_feats.wasm（WASM SIMD，×5.1–5.7）+ 方案②可行性
> **结论**：方案① conv_feats.wasm 落地——features ×6、forward ×5.7、端到端 ×5.1（pooled max|Δ|=4.8e-6，conv-wasm.test 随机权重 ≤1e-3）；方案② onnxruntime-node 可行性确认未落地（链路长且已获 ×5.7）。

## §313 提速方案异构平台兼容性评估（2026-09-03）
> **结论**：方案①可直接兼容异构（wasm 字节码同执行 + dispatch bun 版本红线防 wasm/TS 混跑 + 无 SIMD 回退 TS）；方案② N-API 无 Android 包 + 跨平台数值 ~e-4 混跑破坏 M4 红线，不引入。

## §314 v3.14 竞速可见域修正：主副本派发一律登记 inflight（2026-09-03，it6 实测）
> **结论**：主副本派发一律登记 inflight（早派任务对竞速可见）；v3.14b：集成测试编排化（零外部 fixture + RUN_RL_ITEST=1） + rescan halt 感知（熔断后 dispatch 立即收尾）。

## §315 轴 2 补测试：per-tick 策略头（move/fire/value-128）torch↔TS parity golden（2026-09-03）

## §316 python 测试提速 v3.15：integration 提速 + heavy 分层 + 等待轮询化（2026-09-03）

## §317 全量测试自动并行：xdist 解禁 + FakeAgent 实例隔离（2026-09-03，用户裁定）

## §318 test_integration 拆分 9 独立函数，xdist 全量并行再提速（2026-09-03，用户裁定）

## §319 长程任务纪律入规：启动测速预算 + 日志落盘可观测（2026-09-04，用户裁定）
> **纪律入规**：AGENTS §16.1–16.4（长任务预算实测 + 日志落盘 + checkpoint + CPU 双采样判 kill-vs-wait）；train/bc.py 新增 --ckpt-every N。案例：723 帧冒烟外推 8-11× 误差的 11h 事故。

## §320 长程任务并行入规：可分片任务禁止默认串行（2026-09-04，用户裁定）
> **纪律入规**：AGENTS §16.5–16.6（可分片任务并行默认 + 每工具并行==串行字节比对一次）。

## §321 Windows 文本编辑纪律入规：脚本化替换 + 断言守卫 + 原子写（2026-09-04，用户裁定）
> **纪律入规**：AGENTS §17（脚本化替换 + assert 守卫 + 原子写 + Windows 路径/编码纪律）。

## §322 PowerShell 环境事实与编码纪律（2026-09-04，用户裁定）
> **纪律入规**：AGENTS §17.6（pwsh 7.6.5 Core 事实 / gb2312 默认 / 涉 CJK 先设两个 UTF-8 变量或走 python 通道）。

## §323 仓库内 PowerShell 调用一律 pwsh 7（2026-09-04，用户裁定）
> **规则（铁律）**：仓库内一切 PowerShell 调用一律 `pwsh` 7（禁裸 `powershell`=inbox 5.1）；理由：agent 通道即 pwsh7，双版本双编码双真相；规则落 AGENTS §17.7。

## §324 launcher --script 支持子包入口与旧名别名（2026-09-04，修复 stale 契约）
> **结论**：launcher --script 接受三类（根裸名 / 子包相对路径 / 旧扁平名自动别名）；守卫拒绝绝对路径、盘符、反斜杠、.. 越级。

## §325 train/bc.py 增加 --device（GPU 训练支持）（2026-09-04，Colab 全量 p1-bc 需求）
> **工具**：train/bc.py 加 --device（默认 cpu，镜像 goal_bc.py；导出前 .to(cpu) 保权重跨设备 bitwise 稳定）。resume 1-epoch smoke 与改动前逐值一致。

## §326 eval-course-ckpt 工具 + export-eval-game 报告补 被击中 字段（2026-09-04）
> **工具**：export-eval-game 报告补 playerHits/playerDamageTaken；新工具 tools/sim/eval-course-ckpt.ts（多 checkpoint × N 局课程自定义关贪心评估）。

## §327 p4-onset 课程：4 敌混编四面围攻（2026-09-04，p1 饱和后课程升级）
> **结论**：p1-onset 被 ep60 BC 饱和（94/100≈教师），课程升级 p4-onset（4 敌四角/居中/1 命 0 星/2400tick）；ep60 vs God 各 100 局结果见 docs/rl.progress.md §2。

## §328 远程 PPO 训练架构定案：rollout 在 LAN、PPO 在云端 GPU（2026-09-04，方案拍板）
> **定案（远程 PPO 架构，plan/remote-ppo-architecture.md v0.2）**：rollout 留 LAN、PPO 上云端 GPU；迭代级双缓冲（弃 wave 级 stream）；云 worker 无状态可重连（幂等键+租约）；Adam 保真随 job 往返；云端不可达=暂停不降级；v1 仅 per-tick 课程，intent/goal 远程化为独立后续。

## §329 远程 PPO 架构评审处置（2026-09-04，plan/remote.review-ms.md → 计划 v0.3）
> **处置（评审 ms → v0.3）**：F1–F8 核验采纳（manifest 课程快照/metrics_version、per-job 确定性种子、opt 状态 tar 载体、data_fp 定义、三重校验）；Q7 租约超时只告警回池、Q8 hub 维持 Windows 训练机；M0 测量门先于一切代码（半天）；验证锚 = M0 四数落盘 / M1 假云回环 / M3 三曲线。

## §330 远程 PPO 第二评审处置（2026-09-04，plan/remote.review-ds.md → 计划 v0.4）
> **处置（评审 ds → v0.4）**：G1 weights_json 产出方锁死云 worker；G2 可领取池=jsonl+租约重算（kill -9 纯重读）；G3 云 worker 独立入口 remote_worker；G6 M0d 全往返计时；Q9 免费档长跑接受回收、Q10 远程预采默认 0。

## §331 远程 PPO 内容管控补充需求（2026-09-04，plan/remote-ppo-architecture.md v0.5）
> **拍板（内容管控，v0.5 D13–D15）**：Q11 修改从下一轮整体生效（course 快照固化）；Q12 课程切换延续 run 热启动；D13 course 快照入 manifest + hub publish 前本地校验；D14 shard 带 course_fp 防跨课混训；D15 obs/action/schema 改动=新 era（v1 红线排除）。

## §332 p10-onset 奖励函数：p4 toy 公式 + 拾取项（2026-09-04，用户指令） _(superseded by §333)_
> **结论**：p10 奖励 = p4 toy + wPickup*powerUpsCollected + wStar*starsCollected（1.5/1.0，star 实得 2.5≈一杀）；按道具差别定价否决（21 维无分型计数器，加维=shard 变更，不合算）。

## §333 p4-onset 奖励同步拾取项（2026-09-04，用户指令，取代 §332 的"p4 不动"）
> **结论**：p4 同步拾取项（同 p10 定价 wPickup=1.5/wStar=1.0）；启动前改动不触发在途 run 冻结，BC 不受影响。

## §334 远程 PPO M1 落地：hub-server/worker/协议全链路 + 语料血缘（2026-09-05，实现签入）
> **落地（M1 假云回环）**：remote/{protocol,hub_server,worker,remote_worker,hub_client}.py 全 stdlib；TrainingLoop --ppo remote 接线（hub 免 torch）；course_fp 语料血缘全链（sha256 原始字节）；冒烟 5.8s 全往返，单测 30 用例全绿；远程预采默认 0 成立。

## §335 hy 评审处置：H1–H11 修复 + F2 channels_last + F3 重估（2026-09-05，plan/remote.review-hy.md 处置签入）
> **处置（评审 hy）**：H1/H2 心跳 P0 修复（守护线程 + lease_token）；H3 wait_job 25min<LEASE30min；H4 发布前 dirty-tree fail-fast；H5 opt tar 去 state.json；H6 账本增量读；H7–H11 P2 修复；F2 channels_last 2.08×（2.06 vs 4.28 s/chunk）；F3 200 局/轮重估不用调。

## §336 M0 测量门：四数落盘（2026-09-05，先于 M2 代码前完成）
> **测量门（四数）**：M0a PPO 占比 seed_rotate=50 外推 ≈48.6%（远程明确划算，channels_last 后 ~32%）；M0b shard zip 压缩比 104×（50 局 payload ~0.8MB）；M0c 隧道吞吐 200–330KB/s（传输<10s 非瓶颈）；M0d 回环往返 8.0s ≪ rollout。结论：M2 真 GPU 冒烟可推。

## §337 课程结束条件与停机规范 v0.3 拍板（2026-09-05，转实施依据）
> **拍板（课程退出规范 v0.3）**：过门线 p4 G1 ≥40/100 连 3 次 + 技能三项；G9 首期降级 PAUSE；裁剪双相确认/executor 握手/.run_meta；loop 内纯函数库 + eval 单源；落地 = 门禁库 + 第四守卫 + 停机执行器。

## §338 课程结束条件 v0.4 拍板：ds 评审 13 项 + P10-CAP 重标定（2026-09-05，转实施依据）
> **拍板（v0.4，评审 ds 全接受）**：EVAL_SEEDS 扩池、趋势单源化、G5 基线跨重启累计、breaker 同写 ABORT 行；门控语料统一 in-loop 现池（p4 G1 ≥36%、被击中 ≤0.60/局）；P10-CAP：max_ticks 2400→3600（教师超时 30%）。

## §339 hub-start.ts 全原生 Bun 重写：跳板进程与注册竞态修复（2026-09-05）
> **定案**：hub-start.ts 全原生 Bun 重写（Bun.connect/spawn/process.kill + 分文件注册账本）。四平台陷阱（勿再踩）：① Bun Job Object 默认 kill-on-close → 须 detached:true；② choco shim 另起真身不透传 stdio → 反推 lib\cloudflared	ools 直启 + --logfile；③ uv venv trampoline 杀跳板留孤儿 → 读 pyvenv.cfg executable 直启 + PYTHONPATH 挂 Lib\site-packages；④ 注册账本竞态 → registry.<name>.json 分文件。

## §340 冒烟预演设计：真课程 + echo 回显 + 作废轮，不建虚拟课程（2026-09-05，用户拍板）
> **定案（用户拍板）**：冒烟预演走真课程路径（TrainingLoop + --smoke 真 job + 伪 Kaggle --echo 回显 + SmokeVoidRound 作废本轮），否决虚拟课程；硬门严格化（隧道/code.zip/self-rollout 任一失败 exit 1，--no-tunnel 逃生门）；补充：位置参数课程名 + 启动前课程快速失败；GPU↔HUB 通信分层重传加固（RetryableError 划界 / worker 下载 3 次退避 / post_result 5 次 / release 还租约 / 结果缓存复用）。实施记 `docs/nn/remote-transport.md` §1（冒烟预演）。

## §341 /pool 页面热加载——pool-page.ts 改动免重启 agent（2026-09-06，用户指令）
> **定案**：pool-page.ts 改 mtime 键控动态 import 热加载（键=mtimeMs，文件版本只占一条模块记录；坏文件回退上版且页面 200）；
>   sampler-agent 本次接线=最后一次节点升级波；核心协议文件不纳入热切换。

## §342 / p4-onset 监控四修复（2026-09-06，监控发现 → 用户拍板"修全部问题"）
> **结论**：p4-onset 监控四修复——ppo_schedule lr 三段表远程模式全程未生效（同步折进 args.lr）；同 seed shard 双份落盘（dup-settle 退役输家目录 + iter_shard_dirs 同名去重）；贪心 eval 被 EVAL_SEEDS=20 截断（扩池）；课程 backup_prefix/dir 未被采用（优先课程声明）。监控教训：判断配置是否生效看 manifest 打包链路而非日志回显。

## §343 / PPO job 分发改竞速广播——废租约独占（2026-09-06，用户指令） _(superseded by §2026-09-12-multi-course-p3b-supersedes-343；竞速语义后经 §2026-09-17-goalnn-race-broadcast 复活、终被 §2026-09-22-goalnn-race-retired-priority-only 退役)_
> **拍板（租约独占废弃）**：PPO job 改竞速广播——所有轮询 worker 领同一份任务，先回传者落账，后到者丢弃；claimable = pending 且未 completed 且结果未落盘（不再读租约）；存储结果首写锁定 409；lease 机制保留为兼容路径。

## §344 / pre-commit 门禁按文件归因——多 agent 并行安全版（2026-09-06，用户指令）
> **拍板（门禁按文件归因 v3，多 agent 并行安全版）**：v1 快照丢在途工作、v2 影子树危险被否决；v3 = 全量门禁照常跑、拦截只认 staged 归因（ruff 范围化 / tsc-mypy 报错求交 staged / pytest-bun 拦+归因指引 / oxfmt 跳 MM 文件 / freeze 仅 staged 含 TS 时跑）；hook 零写工作树；逃生口 NN_GATE_SKIP 等。实操演练见正文（已删，commit 承载）。

## §345 / p4-horizon 新实验：视野假设 γ+λ（2026-09-06，用户拍板"γ+λ一起动"）
> **结论（视野假设 γ+λ，p4-horizon）**：warm-start it70，γ0.998/λ0.99（GAE 视野 29→83 步）；其余与 onset 逐字节同义；判定：贪心持续 >35% 为成、≤30% 横盘 40 轮为败；启动纠正：必须 `bun tools/training/start.ts hub p4-horizon`（--ppo remote）且切课程必须重启 hub-server。详见 docs/rl.progress.md §4。

## §346 / tools/training/ 统一启动器：hub-start.ts 与 start-training.{sh,ps1} 三合一（2026-09-06，用户指令） _（路径已失效：`tools/training/**` → `dashboard/`，见 §2026-09-14-goalnn-dashboard-project）_
> **定案**：tools/training/ 模块族拆分（paths/types/log/config/net/proc/.../smoke/hub/push/train/start + monitor/）；全 Bun 原生 API 纪律（§339 延续，唯 wmic/netstat 论证例外）；变更检测监督（codehash-files.txt 哨兵 mtime/size）；冒烟门禁三模式全覆盖；vite/svelte 不引入。

## §348 / NN 训练控制台：tools/training/console（本地网页，2026-09-06，用户指令） _（路径已失效：`tools/training/**` → `dashboard/`，见 §2026-09-14-goalnn-dashboard-project）_
> **定案（训练控制台）**：tools/training/console 四模块（server/api/actions/page）；零新依赖；动作层与 CLI 同一套原语；模式开关两级落点（rl.stream 等写 rl-config，pull/push/local 持久化 console-state）；写回即冒烟；并发 per-key busy 互斥；setCourse 改抛 ActionError→409（不再 process.exit）。补充：指标 sparkline、/log/<key> 组件日志页（尾部窗口读 + 定点替换 + follow）。

## §347 / pre-commit oxfmt 循环跳过 staged 删除源（2026-09-06，§7 复现→修复）
> **结论**：pre-commit oxfmt 循环对 staged 删除源先 continue（git add 对不存在文件 fatal）；本提交实弹验证。

## §349 / 删除一键启动器 start.ts——控制台 + train.ts 双入口（2026-09-06，用户指令） _（路径已失效：`tools/training/**` → `dashboard/`，见 §2026-09-14-goalnn-dashboard-project）_
> **定案（用户指令删除 start.ts）**：能力全量归并——train 模式 → tools/training/train.ts 真 CLI（if(import.meta.main) 守卫教训）；hub/push → 控制台预设；push 预演 → smokeTrain 动作；监督 → 控制台 createSupervisor；新 SSOT tools/training/specs.ts；入口 bun run train = console，train.ts --script 无头。

## §350 / p4-fast 加速课程：GPU 吃满 + 语料×2（2026-09-07，用户指令）

## §351 / 训练控制台两处缺陷：course 显示/动作双源 + local×stream 假互斥（2026-09-07，用户指令）

## §352 / p4-wdmg：死亡惩罚×2（2026-09-07，p4-fast it20 提前干预链）

## §353 / 训练控制台 Preact 化（plan/Training-Console-Preact.md v3.3 执行，2026-09-07）

## §354 / p2-step 诊断台阶：1敌→4敌步子太大？（2026-09-07，用户指令）
> **结论（p2-step 诊断台阶）**：1 敌→4 敌步子过大假说；p2-step.jsonc（count 4→2，其余同义）；探针协议（BC ep60 ×100 + God ×100，CPU 零训练成本）：BC≥50% 且 God≥85% → 开 RL 腿；BC<30% → 假说死。

## §355 / p3-step 诊断台阶补完：悬崖在 2→3 还是 3→4？（2026-09-07，用户指令）
> **结论（p3-step 补齐）**：同套路派生 p3-step.jsonc（count 4→3）；判读 BC 单调 p2(67)>p3>p4(14) 为台阶成立，p3≈60 → 悬崖在 3→4；RL 腿一律另起条目。

## §354 / 控制台 UI 紧凑化三项调整（2026-09-07，用户指令；纯客户端，零进程影响）
> **（UI 运维）**控制台 UI 紧凑化三项：固定头部训练状态条 + 卡片紧凑化默认 + worker_server 移出组件栏。正文已删（commit 承载）。

## §355 / 控制台 UI 重设计：一屏仪表盘 + 抽屉 + 启动弹窗（2026-09-07，用户拍板草图后实施）
> **（UI 运维）**控制台一屏仪表盘重设计 + 抽屉 + 启动弹窗（用户拍板草图）。正文已删（commit 承载）。

## §356 / p2-step RL 腿启动（2026-09-07，wdmg it40 证伪后，用户直接启动）

## §357 / p4-open 敞篷探针：钢盒子是不是帮凶？（2026-09-07，用户指令）
> **结论（p4-open 敞篷探针）**：去边框 6→0、内场 22×22→26×26（+40% 面积）单变量"是否有墙"；门：BC/God gap 收窄 ≤30pp → confinement 主犯开腿；仅授权探针。

## §358 / p3-step RL 腿：晋升门兑现（2026-09-07，p2 it35/it40 连击 75/84）
> **结论**：p2 腿 84% 结业（媲美教师）；开 p3 腿 warm-start p2-it40，it5 检查点 ≥35 继续 / ≤25 切 ep60；p3→p4 门连续 2 eval ≥70。

## §359 / p3-bc 臂：transfer 证伪，切 ep60 重开（2026-09-07，§358 it5 检查点触发）
> **结论**：p2 冠军权重在 p3 上越训越差（27→20→14，转移方向性证伪）；开 p3-bc 臂（ep60 探针权重），it15 verdict 停腿（42→37→22→11：BC 起点也被练差）。

## §360 / 全 p 系出生点变体化：固定靶退役（2026-09-07，用户指令"四个角随机"）
> **结论（出生点变体化）**：历史冻结线固定（p1/p4*/p2-step/p3-p2arm），新/改 p2-var/p3-bc/p4-var；探针 verdict：多样性税不存在（p2-var BC 87 反 +20pp；固定=硬核训练集、var=泛化测试集），p2-var 不扶正；p3-bc 放行（对称门 it5≥40）；it15 停腿（11/100）。

## §361 / R5 value-head 实验：normalize_ret 先行（2026-09-07，p3-bc 停腿后）
> **结论（R5 value-head 首投 normalize_ret）**：p3-bc 同数学另起 fresh run（p3-vr1），单变量；成功门 value MSE<2 毕业；毕业+胜率平 → critic 出局（→容量 R6）；实施需单测 + 旧数学逐字节回归。

## §361 训练控制台 UI 五项调整（2026-09-07，用户指令）
> **（UI 运维）**控制台 UI 五项调整（CopyButton icon 模式 / 指标表 500 轮上限 / loadConfigSafe 抗抖 / iter 事件驱动刷新 / local 节点 pill）。正文已删（commit 承载）。

## §362 / intent 后端永不用于 p 系执行（2026-09-07，用户陈述既往否决结论，入档）
> **规则（否决入档）**：intent 后端**永不**用于 p 系执行——执行层依赖有缺陷的 God-AI 行动方案，天花板锁死略逊于 God，credit-assignment 家族剔除 intent/goal 后端（含 replan/heartbeat 全套）。候选重排：①BC-anchored per-tick kickstart（§363）②回报重分配（§382）③短局（暂否）。

## §363 / BC-anchored per-tick kickstart 移植（2026-09-07，p3-vr1 it15 verdict 后）
> **结论（kickstart 移植）**：engine.ppo_update + ref_model/kickstart_kl（exact-KL 分头、衰减复用 run_rl 语义、ref=课程 bc 冻结快照）；三重默认关闭（ref None 或 coef 0 → 逐字节不变）；成功门 it1 KL<0.1 且 it5 ≥25。补记：ent_break 0.25 从未落地（flat_overrides 漏映射，已修）；自定义熵 tripwire 三振退役（假阳性率约束）。

## §365 / 控制台 /api/state 空回复：nodeViews 串行 ping 修复（2026-09-08，用户报告）
> **结论**：/api/state 空回复 = nodeViews 串行 ping（5 节点 10.1s）超 Bun.serve idleTimeout 10s；改 Promise.all 并行 + 不可达 4s 超时（13.2s→6.7s）；测试断言并行发起计数（串行必红）。教训：注释契约与实现脱节（§363 同款）。

## §364 / p3-vk1 combo：归一＋缰绳叠加（2026-09-08，ks1 it25 停腿后，用户指令）
> **结论（p3-vk1：归一+缰绳叠加）**：由 ks1 派生加 normalize_ret:true（三臂对照 vr1=归一/ks1=缰绳/vk1=叠加）；首发作废 verdict 被实测推翻撤回（训练侧结算丢 kickstart 遥测，非 hub/worker 问题，缰绳一直在跑）；修复 _remote_forward_agg 透传 kickstart 键 + 启动协议机器版监护。成功门 value MSE<2（it2 即过）。

## §366 / 页面加载 <1s：慢部件快照缓存（2026-09-08，用户指令"6.7s 太慢"）
> **结论**：慢部件（节点 ping/组件探测/池历史聚合）移出请求路径——后台刷新器 5s 重算 + buildStateView 只读缓存；walk 深度 ≤2 限 'traj'（1.39s→20ms）；超时收敛 + 组件探测并行。warm 页面 11ms / 冷算 6.7s→1.5s。教训：聚合"每次请求全量重扫"是隐藏慢路径。

## §367 / rollout 子进程改由 node(V8) 执行：wasm 推理跨引擎 ×1.6（2026-09-08，实测驱动）
> **定案**：rollout 子进程交给 node(V8)（features ×1.63），agent 本体仍跑 bun；bun build --target=node 预打包；坑：wasm 相对产物解析须复制到产物同级（缺文件静默回退 TS ×14 慢）；降级链（无 node/打包失败/连败≥2 → 回 bun）；同权重同 seed 两引擎产物逐字节相同（M4 红线不破）。tools/agent/** 改动必须合批同一 commit 触发升级波。

## §368 / 推理提速②③④：pointwise intrinsics + encode 降频 + 长驻 serve worker（2026-09-08）
> **定案**：②pointwise 手写 wasm_simd128 intrinsics（features 4.59→3.43ms node，产物逐字节同）；③obs 只在决策 tick 编码（encode 2.4%→1.0%）；④长驻 serve worker（--persist 默认关，serve 与一次性 spawn 逐字节同）；补记：int16 dot 量化实测否决（node -18% + 精度 bug，HWC8 平铺才可再评）。⚠️ serve worker stdin 必须 'pipe'。

## §369 / 训练侧架构实验族提案留档（2026-09-08，未启动）
> **留档（未启动）**：架构实验族（bottleneck 64→16→64 首选核心候选 / board 26→13 最深 / h 64→48 / d 8→6 / K 20 口径级）；批次税清单（新 BC + fresh 课程 + 基线重立 + codeHash 升级波）；int16 dot 维持否决；计划文件 plan/nn-arch-speedup.md（untracked）。

## §370 / 日志页视觉重设计：终端美学 + 结构化日志 + 搜索/过滤（2026-09-08，用户指令"日志页很丑"）
> **（UI 运维）**日志页终端美学重设计 + 结构化事件卡 + 搜索/过滤（顶卡/吸顶工具栏/view.ts 纯函数）。正文已删。

## §371 / 日志页四项交互修复 + bundle 404 根因（2026-09-08，用户反馈"跟随/过滤未起效"）
> **结论**：日志页无客户端 JS 元凶 = renderLogPage 默认 scriptSrc /app-log.js 而服务端只挂 /log.js（bundle 404）；改 /log.js；顺带尾行截断 all / 智能跟随滚动 / FAB 常驻 / 过滤。

## §372 / 日志页细节二改：「共 N 行」= 文件总行数；直达底部按钮并入工具栏（2026-09-08，用户指令）
> **（UI 运维）**「共 N 行」= 文件总行数（readLogTail totalLines ≤8MB 精确）；直达底部按钮并入工具栏。正文已删。

## §373 / 日志乱码修复 + 暂停提示入工具栏（2026-09-08，用户报告）
> **结论**：GBK 乱码 → readLogTail 按 
 切行逐行 UTF-8 fatal→GB18030 兜底（整段误判不可取）；暂停提示条并入工具栏最左（后改为 .tc-logtool__right 搜索框前，保与「过滤日志」同行）。

## §374 / sampler-agent 工作目录磁盘泄漏根治（2026-09-08，用户报告）
> **结论**：磁盘泄漏三根因（sweepWeightFiles 旧正则匹配不到 kind 命名→永远空操作；强杀孤儿 game-*；陈旧 pid）；新纯函数模块 tools/agent/workdir-cleanup.ts（KEEP=4 + 在飞引用永不删 + 孤儿 5min 年龄门）；本机 62MB→3.7MB；7 例单测含旧正则回归。

## §375 / workdir 收敛同步到 trainer 本地采样路径（2026-09-08，§374 后续）
> **结论**：trainer 本地采样波次目录（it{N}/w{idx}）失败/废弃残留 → nn-training/rl/workdir_sweep.py（只删无 _rl_report 的 w<idx>，完整波次永不删），钩子挂 _rotate_cleanup；6 例单测。

## §376 / 双 tmp 目录统一：全项目只使用仓库根 ./tmp（2026-09-08，用户指令）
> **结论（双 tmp 统一）**：全项目只使用仓库根 ./tmp——paths.ts LOG_DIR 改 REPO_ROOT/tmp；python 相对路径一律锚定仓库根；conftest/pyproject 缓存/清理脚本目标同步；nn-training/tmp 存量不迁移待用户确认删除。

## §377 / 仓库根 tmp/ 统一收敛脚本 tools/tmp-clean.py（2026-09-08，§376 后续）
> **工具**：tools/tmp-clean.py（分类器=it 子目录 / 保留策略 keep-runs 3 + keep-days 14；永不碰 dist-agent/git-repair-backup/training-start；.run_rl 锁存活跳过运行目录；输出 ASCII 防 GBK 乱码）。

## §378 / 引擎改微基准自动选：节点本机实测 JSC vs V8（2026-09-08，三平台数据驱动）
> **定案**：引擎改微基准自动选（engine-bench.ts，bun vs node 各 warmup 计时，node ≤ bun×0.97 才选；结果缓存engine-choice.json 随 codeHash stamp 清理）；五平台实测 V8 只在 x64 Linux/Windows 赢、JSC mac/arm64 赢。

## §379 / 铁律：课程引用的权重文件常备备份到 nn-training/weights/in-use/（2026-09-08，用户指令）
> **规则（铁律，见头部「铁律清单」）**：课程（curricula/*.jsonc 的 bc/init/引用路径）使用的权重文件必须常备一份 nn-training/weights/in-use/（不受 tmp 清理影响）；新课程/新引用同步落一份；原件丢失从 in-use 恢复后启动，禁止"无备份裸奔"直接重训或换 ref 救场；临时目录只放可再生中间产物。现场：ep60 BC 本机不可恢复，来源需用户提供。

## §380 / TrainingLoop 不许静默失败：退出看护 + 失败写日志（2026-09-08，用户指令）
> **规则（铁律，见头部「铁律清单」）**：TrainingLoop 不许静默失败——意外退出看护（4s 周期两帧确认→日志标记+registry error/exitAt + UI 展示）；启动即退出先落失败标记再清账；状态机 exited 不是日志。

## §383 / 悬空 job 清理 + 阶段灯 idle 修复（2026-09-08，用户指令）
> **结论**：悬空 job → cancel_stale_jobs 发布前作废 it≤当前 的非本次 pending（幂等，avoid GPU 补做历史 jid）；阶段灯 idle → parsePhaseFromLog 补 published-job→ppo 行匹配。

## §381 / 组件日志动态查找在组件表落地 + 账本真实性回归（2026-09-08，用户指令）
> **结论**：组件日志页「文件不存在」根因 = 旧控制台进程（静态路径失效）+ 组件表未用动态查找；统一走 resolveComponentLog（静态 → 账本 → scanLatestLog 动态、mtime 最新、只扫一层）+ 账本真实性按真进程 pid 重登记。训诫：改码后看到旧行为先核对控制台进程启动时间。

## §382 / ②回报重分配 = terminal-spread 均匀重分配（2026-09-08，vk1 停腿后用户指令"开发下一棒"）
> **定案（②回报重分配 = terminal-spread）**：p3 四腿全灭，§362 排序只剩②；终局对账额 T 改每步 +T/N（Σr 恒等、RUDDER 均匀基线、零新超参；按开火/活动加权否决——超时组恰在打）；做在 wrapper 层（TIME_AXIS_REDUCERS 不变式不破）；identity 指纹含新键 → formula_hash 变强制隔离；载体 p3-rd1（由 vk1 派生单变量）；预注册门 it5 ≥35 / it15−it5 ≥+15pp 且 ≥45。

## §384 / pull 预设 trainingLoop 种子路径硬编码修好（2026-09-08，p3-rd1 启动失败）
> **结论**：pull 预设种子路径硬编码 weights.json 与课程 bc 字段脱节（ckpt.60）；即时解堵（同 sha 恢复）+ 根治 resolveCourseBc(course)（读课程 jsonc，失败回退 legacy）+ 回归测试。教训：读课程字段的第二实现必须以课程文件为唯一来源。

## §2026-09-08-decisions-governance（2026-09-08，用户拍板执行 plan/decisions-governance.md）

- **决定**：收紧准入（三问闸门 + 三类路由，禁双写）+ 防冲突命名（日期 ID `§YYYY-MM-DD-<branch>-<slug>`，
  旧编号 §1–§384 冻结契约不动）+ union 合并（`.gitattributes`）+ 只追加（新条目只写末尾）+
  模板外置（`docs/decisions/HOW-TO-ADD.md`）+ 自动校验（`tools/check-decisions.ts` 接进 `bun run check`，
  基线 `tools/decisions-baseline.json`）+ 一次性瘦身（编号集合不变式：只删正文不删编号；
  实验/调优 → progress 指针，bugfix/UI → commit 承载）。本次迁移 3734 → 约 980 行，编号集合逐条核对未变。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/repo-governance.md` §2026-09-08-decisions-governance

## §2026-09-09-goalnn-console-lan-readonly（2026-09-09，用户指令：控制台改局域网只读）
> **定案（局域网只读 + localhost 控制）**：训练控制台绑 0.0.0.0（局域网可达），POST /api/* 动作仅回环来源可执行（server.requestIP → isLoopbackAddress，fail closed：无法判定来源 = 拒绝，403）；GET 全开但 ?course= 只读课程覆盖（sanitizeViewCourse：真实课程 + 防路径穿越，绝不写 console-state）；客户端 isLocalHost 判定：本机切课程 = 查看 + POST setCourse 同步操作员课程，局域网切课程 = 仅查看 + 写 URL（?course= 可分享/刷新保持）；课程敏感动作统一带 body.course（所见即所控）。备选与否决：LAN 直通 POST —— 否，杀进程/改配置暴露给整网；每会话独立课程 —— 否，LAN 多 tab 本就共享视图。违反后果：局域网可启停组件/改 rl-config、?course= 路径穿越读任意文件。
> **（2026-09-09 修订，用户指令）**：撤销数据脱敏——cloudflared token 行与复制键局域网照常展示（原「非回环 redactSecrets 剥 auth key」作废）；只读是动作边界，不是数据边界。

## §2026-09-10-goalnn-rleval（2026-09-10，plan/rl-eval-system.md P0–P4 自主实施）

- **决定**：B/C 节点门 engine_epoch 严格拒派，A 层过渡期旧 agent 记日志放行（舰队升级完收紧）；W1 入账走控制台
  read-through（训练循环零改动）；A/B 按 eval_on_round 确定性分配；agent taskKey 无 policy 分量 ←→
  iterId 命名空间隔离 god/nn（不动节点缓存键，避升级波外负担）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-10-goalnn-rleval`

## §2026-09-10-course-exit-gates（2026-09-10 晚 / 09-11 晨，c6 判否后自主实施 R1/R3/R7/R9）

- **背景**：c6-margin 跑满 50 轮 8550 局、eval 均值 25.4（老师 50、突破线 35）、**零学习信号**，且
  it50 因远端 PPO job 三次 1800s 超时而进程消失（docs/rl.progress.md §22）。根因与修正清单在
  `plan/feasibility-map.md` §12（D1–D9 / R1–R9）；用户拍板：**判否停腿，改设计后重开一腿**，
  优先级 R1（门槛进代码）> R3（重定价）+ R7（每轮局数）> R9（远端失败降级）；R2/R4/R5 暂缓。
- **决定（R1 门槛进代码，M0+M1）**：① `rl/config.py` 加 `GateTeacher/GateRule/GatesSpec`
  （parser 强校验 §3.4 全 8 条，未知 kind 响亮报错，`_GATE_FRAC_FIELDS [0,1]` 与 `_GATE_REL_FIELDS ≥0`
  分家——§3.2 示例的 `max_phits_rel: 1.5` 是相对倍数，本就 >1，计划原文按批注修正）；
  ② 新增 `rl/gate_check.py` 纯函数求值器（`evaluate(course, trend_rows, health, budget, now=None)`），
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-10-course-exit-gates`

## §2026-09-11-c6-review-disposition（2026-09-11，§12 复盘评审的处置：逐条核数据，非照单全收）

- **背景**：用户转来一份对 `plan/feasibility-map.md` §12（c6 复盘）的评审，要求谨慎评估、
  充分论证。评审指认三处事实错误（F1 kickstart 机制、F2 it50 口径、F3 R1 设施状态）、
  三点诊断偏漏、以及"R 清单缺执行顺序"这一最大缺口。
- **核实方法**：一律回到盘面与代码——`tmp/c6-margin/{training_log,eval_log}.jsonl`、
  `remote-jobs/*/manifest.json`、`rl/reward_library.reward_from_spec`（真实公式而非笔算）。
- **接受（已改文档/配置）**：① D10 单变量失真（c6 注释写"单变量=敌数"，但 bc 同时
  从 c4-it140 换成 c5-it40）；② D11 无梯度假说链（稀疏通关+廉价死亡+时间税 → terminal
  主导 → PPO 只能学"更快结束"）；③ `§12.3.1` Phase 0–4 执行顺序 + R3/R4/R5 互斥；
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-11-c6-review-disposition`

## §2026-09-11-goalnn-evalboard-idle-yield（2026-09-11，用户指令：evalboard 与训练 A-eval 解耦）

- **决定**：① 从 `rollout_phase` 去掉 B/C 领批；② TrainingLoop 每轮 `_join_eval` 后
  开 idle 窗 `_evalboard_idle`（`window_event` 置位）领最早 pending 批；③ 下一轮
  rollout 前 `_evalboard_yield` 关窗 + 短 join，在途局停派新 seed、已结算不丢
  （`_reopen_for_resume` 把 partial 批回 pending，`_done_keys` 续跑）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-11-goalnn-evalboard-idle-yield`

## §2026-09-11-nntrain-g13-duty-fix（2026-09-11，用户指令"处理所有问题"）

- **决定**：① G13 分母基线 = **首个完成迭代的结束时刻**（起步热身不计账）+ 完成迭代 ≥2 才判
  （防开门即响；无 duty 数据的旧调用方回退终身口径）；② 非评估轮也查 duty（only_kinds 过滤，
  `_gate` 不解坏签名）；③ G5 max_hours 加**轮级硬断**（原先只在评估轮被查，会过冲 ~eval 周期）；
  ④ exit-watchdog 识别**设计内停车**（账本近 300s 有 gate_verdict/circuit_break → 标「已停车(原因)」
  而非「意外退出」）。终身口径只留给 G5 预算门（cap 语义）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-11-nntrain-g13-duty-fix`

## §2026-09-11-nntrain-cloud-halt（2026-09-11，用户指令：停机=停云端省 GPU 限额、本地进程都不停、console 出横幅）

- **决定**：① hub 新增管理端点 /admin/workers/halt|resume|status（Bearer 同 worker 鉴权；volatile），置位后
  /jobs/next 下发 {"halt":true}；② worker 收到达令即干净退出（keepalive 停、cell 走完）；③ exit-watchdog 在
  TrainingLoop 死亡（含设计内停车与崩溃）时自动调 hub halt（幂等）+ console-state.json cloudHalt{at,reason}
  持久化；④ console 顶栏红色横幅显示停机原因 +「恢复云端」按钮（手动 cloud-halt/cloud-resume 动作随之提供）。
  云商无 release API 的事实（Kaggle/Colab 需手工断连或到 9h 上限）如实写进横幅文案与注释，不假装全释放。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-11-nntrain-cloud-halt`

## §2026-09-11-ppo-tpu-step-mark（2026-09-11，c6b-margin 首个 TPU job 单步 45~52s / eta 5.9h 根因定案）

- **决定**：根因 = torch_xla 惰性模式下 `.tolist()` materialize 只断言依赖子图，backward/
  optimizer 在途节点不 drain、跨步骤累积 → 图线性变大 → 单步耗时 ∝ 步数。修复 =
  `ppo/engine.py::ppo_update` 每步 stats 后 `xla_mark_step(device)`（图执行边界；非 XLA
  no-op，CPU/CUDA 数值逐位不变）+ ref 预计算后一次 mark（防首步巨图）+ worker 日志打印
  device/world_size。**修正旧结论**：`plan/ppo-optimization.plan.md` §0.5「TPU 快 GPU 4.7× /
  44ms」是 ≤3 步微基准测量假象；44ms 只属于「每步有 mark」形态。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/tpu-perf.md` §9「决策正文归档」· 锚 `### §2026-09-11-ppo-tpu-step-mark`

## §2026-09-11-remote-worker-hotswap-supervisor（2026-09-11，云端热更新事故修复：os.execve 打掉 notebook kernel，改监督器+子进程）

- **决定**：① 热替换统一改为「以退出码 HOT_RELOAD_EXIT=86 干净退出，由**监督器**用同一套参数
  重新拉起子进程」——fresh 进程 sys.modules 必然为空，新代码一定生效；② worker.py 新增
  `supervise_worker(restart_argv)`：worker 跑在子进程，stdout/stderr 逐行转发到本进程 stdout
  （notebook 里本进程= kernel，转发保住单元格），退 86 → 同参重拉，KeyboardInterrupt → 先杀子
  再上抛（不留孤儿 worker）；③ `main()` 拆两模式：默认监督器，env `REMOTE_WORKER_CHILD=1`
  的子进程直跑 `worker_loop`；`worker_loop` 传 `restart_argv` = 有监督器（热替换退 86）、
  None = 无监督器（提示人工重启并返回，旧降级行为保持）；④ battle-rl.ipynb `run_pull_worker`
  改调 `supervise_worker(restart_argv)`；CLI `python -m remote_worker` 照常可用（supervisor 包
  一层、rc 原样上浮，M1 冒烟与 `--once` 退出码语义不变）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-11-remote-worker-hotswap-supervisor`

## §2026-09-16-kaggle-kernel-no-torch（2026-09-16，Kaggle 完整引导后 kernel 内 import torch 无声杀会话）

- **背景**：Kaggle 上跑 `battle.tailscale` / `tailscale.debug` 完整引导（apt 装 tailscale + userspace daemon + 登录 + 代理 env 注入）后，**同一 kernel 进程里 `import torch` 稳定无声杀死整会话**——无 traceback、probe 前置行（nvidia-smi/字符串）能打印、随后 kernel 死→ 会话被回收（peer lastSeen 冻结）。而逐步拆开复刻（apt 装 / daemon / `tailscale up` 登录 / 代理 env 各组合：S1/S2/S4/D1-D3）**全部存活**——唯一稳定死点 = 完整引导后 kernel 进程内 import torch，机理未定位（平台倾向），不阻塞修复。
- **决定**：torch/CUDA 探测下沉子进程——`notebook_runtime._probe_cuda` 用 `sys.executable -c` 子进程探版本/卡数/卡名并回传 JSON；**kernel 本体永不 import torch**。子进程死 → 按无 CUDA 落 TPU/CPU，kernel 照常继续（"死也只死子进程"）。TPU 分支保持 find_spec + /dev 扫描（本就零 torch import）。与「内核绝不 import torch_xla」同一纪律延伸。**生效需重启 TrainingLoop**（code.zip 在 loop 启动时 `pack_code_zip` 一次性打包，notebook 引导从 hub `/code` 拉）。
- **违反后果**：任何人把 torch 引用放回 notebook cell / notebook_runtime 的 kernel 进程（如内联 `import torch` 探测设备），在 Kaggle 完整引导后会复现"整会话无声回收"；设备探测一律走子进程。

## §2026-09-11-c4dodge-course（2026-09-11，用户拍板：c4 残血/闪避后继腿，wChip 单变量 0.005→0.02）

- **决定**：新建 `nn-training/curricula/c4-dodge.jsonc`（派生 c4-margin，单变量 = wChip
  0.005→0.02，其余学习侧全锁；bc = c4-margin.it140；iters=160 + max_hours=10）。闪避目标 =
  wChip 抬到"挨 144 血 ≈1 个击杀"开始值钱。门：机器 catalog 无 margin 类，故 G1 胜率轨
  （池化 3 点 ≥77.5%）作 ADVANCE 代理 + G2/G7 防苟活 + G4/G5/G13 护栏，margin 门
  （胜局掉血中位 <120，超老师一档；God 同语料实测 68%/144）留人读复核。**教师基线重测**：
  in-loop 语料 = EVAL_SEEDS 860001-860100@stage2000（非 §2 的 seeds 0-99），God = 68/100
  （旧 64 不可比，c6b 教训）。开腿前先跑 3 轮看 G13 duty 与 ppo_sec。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-11-c4dodge-course`

## §2026-09-11-nntrain-cloud-halt-final（2026-09-11，用户五条语义：停机命令随任务同发、云机先试停机再干活、本地全不动、红灰双横幅）

- **决定**：① /jobs/next 恒带 halt:bool（有任务时与任务同批下发，空闲时单独送达）；② worker 停机过渡
  尝试一次 _release_cloud_machine（Colab unassign / 其余提示人工断开），**不退出、照常执行任务**；
  halt 清除后复位可再试；③ console-state cloudHalt 双态（halted 红横幅 / recovered 灰横幅保留历史，
  TrainingLoop 重启或手动即恢复）；④ 本地组件全程不动。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-11-nntrain-cloud-halt-final`

## §2026-09-12-multi-course-key-is-stem（多课程命名空间键 = 课程文件 stem，不是内部 name）

- **决定**：命名空间键 = 课程文件 stem。`train/loop_util.py::course_key_from_path()` 为唯一推导点；
  `run_rl.py` 锁改调它；`train_loop.py --course` 文档写明传短名（与 `--course s-dodge` 同拼写）；
  内部 `name` 退为 S9 归属标注（events/gate 内用，不参与调度）。回归测试
  `test_course_key_is_file_stem_not_inner_name` 锁死 stem 规则。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-12-multi-course-key-is-stem`

## §2026-09-12-multi-course-p3b-supersedes-343（多课程：独占加超时租约重启用 §343；worker 侧有界 FIFO）

- **supersedes §343**：PPO job 分发从"竞速广播"改回**独占加超时**——`GET /jobs/next` 领取即设
  租约（owner + expiry + last_heartbeat **同时置**）并下发 `lease_token`；`claimable_job_ids` 排除
  持有未过期租约的 job；`POST /jobs/{id}/result` 有活租约时验 `X-Lease-Token`（无租约照收，兼容
  旧 worker/重发）；首写锁定保留（hub 重启丢租约的兜底）。新常量 `CLAIM_TTL_SEC = 300`；心跳
  60s 续租，且 `heartbeat()` 必须以它为**唯一** TTL 来源（沿用 `LEASE_SEC = 1800` 会让死 worker
  隐身 30min）。hub 记 `last_heartbeat` 并在 `/jobs/status` 暴露 `lease_expires_in`/
  `last_heartbeat_ago`（worker 侧吞错保持现状）。
- **为何敢重启租约**：§343 的竞速广播在多 worker 下让同 job 被重复算、慢者 409 白烧；单 worker
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-12-multi-course-p3b-supersedes-343`

## §2026-09-13-multi-course-course-keyed-singletons（多课程并行：课程 = 并行单元，一切单例按课实例化）

- **决定**：①锁/账本条目/端口/隧道/trainer/日志/监督/halt 全部 course-keyed，命名空间键
  = 课程文件 stem（§2026-09-12-multi-course-key-is-stem）；②端口算术唯一归宿
  `tools/training/slots.ts`（`portForSlot`/`allocateSlot` busy 锁内分配 + `checkCapacity`
  加法校验 `Σ max(workers_c, local_slots_c) ≤ max(rl.workers, rl.local_slots)`，超量
  fail-fast 点名课程，拆分值允许 0 = 关闭该课本机直跑）；③账本 = `Record<course, Entry>`
  四新键，重建契约完备字段（Q4）+ 旧扁平键在 P5 由 `loadRegistry` 一次性搬迁后删读写
  （R2）；④本机并发配额热读 `courses.<课>` 优先 + 覆盖生效打响亮行
  `[quota] workers X -> Y (multi-course split)`；⑤控制台按课启/停/冒烟/日志/halt
  （`activeCourse`/`cloudHalts`/busy 键按课），LAN 只读不变，`stopAll` 保留全局总闸语义；
  ⑥remote 侧每课一隧道 + worker 有界 FIFO + 独占租约（§2026-09-12-multi-course-p3b-
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-multi-course-course-keyed-singletons`

## §2026-09-12-c5-gae（2026-09-12，用户指令：先建 c5-gae，c5-tick 继续跑）

- **决定**：新建 `nn-training/curricula/c5-gae.jsonc`（c5-margin 派生，唯一变量 lam 0.99→0.95，
  wTick 锁 0.01 不回继承 c5-tick；kl_coef 末段 0.03 护栏非变量；不写 ent_coef；bc=c4-margin.it140；
  80 轮/12h；无 gates 块）。诚实声明：λ 只测"advantage 方差 ↓ ⇒ 策略动起来"，不声称救 value 头。
  判据：配对胜率 >39%（起点）+ McNemar p<0.05 为主；value/entropy 作参考。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-12-c5-gae`

## §2026-09-12-goal-layer-test（2026-09-12，用户拍板两套目标源；goal 硬 mask 实证为负）

- **背景**：c5-gae（λ=0.95）修好机制后执行器配对 +7pp 但封顶 ~46%（in-loop ~40%），40 轮
  平台。用户提出解冻 goal 头 → 澄清 goal≠intent（intent 骑 God 执行器已证伪；goal=空间目标
  热图+独立执行器，T9a 证执行器是瓶颈，goal-nn-action 定"最后才解冻"）。当前 per-tick 模型
  就是 goal-nn 的执行器本体。评估后用户拍板：做 goal 层测试（两套目标源都测）+ c5-gae 续跑。
- **实现**：新增 `src/nn/goal-mask.ts`（BFS 距离硬掩码"禁背离目标" + God 导航目标源（读
  `god._navTargetCol/Row`）+ 手写启发式源（血低撤退/追最近敌）+ GoalSteering 承诺重选）；
  `export-eval-game.ts` 加 `--policy nn-goal`（frozen StudentNet move logits 应用硬掩码，
  `mask[i]!==1 → -1e9`，只禁不禁劝）；goal-source 显式走 worker payload（env 继承不可靠，两
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn.progress.intent.md` §35「决策正文归档」· 锚 `### §2026-09-12-goal-layer-test`

## §2026-09-12-c5-gae-finale（2026-09-12，λ 修复 160 轮显著破平台；c6 转移稳定）

- **c5-gae 终判（it160 收官）**：λ=0.95 修好 value 头后，160 轮配对语料（seeds 0-99 vs
  起点 39%）**it145=57%（2p=0.010）/ it160=55%（2p=0.023）——统计显著 +16-18pp**，全项目
  第一条 hard 5 敌关显著爬升腿。战斗质量全面升级（击杀 3.15→3.7+、被击 60→42-44）。in-loop
  中枢 ~40%→~44-45%（it125 53% 是尖峰）。机制全程健康（value 0.50-0.62、熵稳定 0.33-0.40）。
  全轨：腿初 28-37% → 中段 40-52% → 终局 55-57%。此前所有"c5 平台"判断 = 机制坏+训练不足。
- **c5→c6 转移（稳定弱阳性）**：c5-gae it100/145/160 零样本 c6 = 29/27/23% vs 基线 18%
  （池化不一致对 72:47，2p=0.027）；击杀 2.64→3.34 大涨但收不了 6 敌关。零样本 ~26% ≈
  c6b 直接训 20 轮的 22%——强 c5 执行器跨关能力 ≈ c6 自己训一点，卡点全在"6 敌收关"。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-12-c5-gae-finale`

## §2026-09-12-rollout-flag-bug（2026-09-12，local rollout 3命1星污染事件；已修、已记录、暂不重训）

- **背景**：`nn-training/rl/cmd.py` `build_rollout_cmd` 用 `f"--{k}"` 拼 override 键
  （`lives_override`/`player_level` 下划线），而 `tools/sim/export-rl-rollout.ts` 只认连字符
  `--lives-override`/`--player-level`（未知 flag 静默忽略）⇒ **local 直跑全程以 hard 缺省
  （3命1星）执行，远端节点以课程覆盖（1命0星）执行**。commit `1ee8955`（2026-09-12 16:55）
  修复（下划线→连字符，`tests/worker/test_rl_cmd.py` 锁死口径）。bug 自 `e828331`（2026-09-02 22:55，
  M1 配置化）引入。
- **污染范围**：e828331 → 1ee8955 之间所有课程的 **local 直跑 rollout 轨迹**（PPO 吃进
  3命1星环境的样本）。各课程 local 局占比实测（`tmp/<course>/dist-agent-meta.jsonl`）：
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-12-rollout-flag-bug`

## §2026-09-13-reward-wdmg-dead-term（2026-09-13，wDmg 死项修复：击杀/承伤/死亡语义各归其位）

- **机制（代码核验）**：`src/game/SimulationCombat.ts:599` 口径下，玩家**非致命**命中推
  `player_damage`（累计入 `playerDamageTaken`），**致命**命中推 `player_hit`（`playerHits++`；
  另一触发 = 3★ 星盾消耗，本族课程 level=0 起步不可达）。⇒ **1 命课程里 `playerHits` 恒等于
  败局指示器**（胜局 0 / 败局 1，c6 实测败局分布 {1:110, 2:1}）。
- **判决**：reward 里的 `- wDmg*playerHits` 是**死项**——不承载挨打信息、与
  `terminal.lives_exhausted=-1.0` 重复扣败局分（败局合计 −2）、且伪装成「挨打惩罚」
  （历史上对 wDmg 的任何调参实际都是在调死刑）。修复 = **新课程删除该项与 `params.wDmg`**；
  承伤定价唯一归 `wChip*playerDamageTaken`，死亡定价唯一归 `terminal.lives_exhausted`。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-13-reward-wdmg-dead-term`

## §2026-09-13-level-extraction（2026-09-13，关卡配置抽离 + D14 语料身份改语义哈希；用户拍板）

- **触发**：mid-run 编辑课程文件（iters 30→60）把 c6-chip 打成指纹脑裂——shard manifest 的
  course_fp 与 job envelope 的 course_fp 都在各自时刻重读文件字节（`cmd.course_fp_for_args` /
  `loop_steps` 发布），编辑落在采样与发布之间 ⇒ D14 整轮 shard 拒收（job=abbf… shard=e6c6…）。
  原则确认：**iters 这类预算/测量旋钮不改变训练契约，mid-run 编辑不应要求新课程**。
- **配置修改分类学**（此后所有课程/关卡变更按此归类）：
  - **A 语料身份**（改动 = 语料破裂，禁止对在跑腿修改；要变 = 新关卡/新课程）：地图/敌人
    队列/敌数/出生点/**命/星**（→ 抽离到关卡文件）、difficulty/max_ticks、mode/dodge、
    seed_rotate/seeds、reward（formula/params/terminal/scheme——PPO 端按 manifest 公式重算
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-level-extraction`

## §2026-09-13-hot-reload（2026-09-13，课程热加载：非语料改动下一 iter 应用；语料改动拒绝+横幅+不泄漏云端；用户拍板）

- **机制**：trainer 每 iter（rollout 前，`loop_core.run` → `_hot_reload_course`）重读课程文件，
  以 `corpus_identity_fp` 分流（§2026-09-13-level-extraction 分类学的机制化）：
  - **未变（B/C 类）**：`rl/hot_reload.apply_hot_fields` 把白名单字段写回 args——消费点每轮
    活读（iters `loop_core:207` / gamma,lam / lr,epochs,mb / eval_* / ent_break*，
    `loop_steps._course_iter`），**下一 iter 即生效**；结构绑定字段（bc/workers/stream/
    out/traj/backup_*/freeze*/clip/vf/ent_coef/normalize_ret/warmup_iters/kickstart_ref）
    记 restart-only（`*` 后缀账），响亮日志「停止→启动后生效」，不静默吞。max_hours 热应用
    时重算 `self._deadline`。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-hot-reload`

## §2026-09-13-bc-cloud-integration（2026-09-13，BC 训练整合进 云-HUB-LAN：云端第二任务类型 kind=bc；用户指令五步）

- **决策**：远程任务协议引入 `kind` 维度（"ppo" 缺省 wire 兼容 | "bc"），BC job 复用
  HUB job 全套（发布/租约/账本/回传/落位）与 LAN 节点协议（/v1/task ?mode=bc），
  云端 worker 按 kind 分叉执行（bc → `train/bc.py::train`），控制台 BC 课程复用
  trainingLoop 组件键（spec 分叉为 run_bc.py）。设计全量：`plan/bc-cloud-integration.plan.md`。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-13-bc-cloud-integration`

## §2026-09-13-multi-gpu-default（2026-09-13，云端多卡默认启用 DataParallel：PPO + BC；用户指令）

- **决策**：battle-rl.ipynb 单 cell 的 `use_multi_gpu` 默认 **True**——云端 2+ GPU 时
  PPO 与 BC 都真跑多卡（PPO 走 worker 既有 DP 分支；BC 由 bc.py 新增 `_resolve_bc_device`
  + `_bc_raw` 支撑）。opt-out 保留：要与历史单卡 run 逐位 A/B 的腿把
  `use_multi_gpu` 改 `False`（或 device 显式 "cuda"）。
- **拒绝的替代方案**：维持 opt-in 默认 False——用户指令「双/多卡时训练跑在双/多卡上」
  直接否决「第二张卡闲置」；改为 opt-out 后，逐位可比性从默认诉求降级为显式诉求
  （等价于把「新实验臂」的判断权交给操作员的显式开关）。
- **边界**：单卡机器行为零变化；TPU 拒收链不变（bc 任务 tpu/xla 仍 ProtocolError）；
  DP 激活时 worker/bc.py 双侧响亮日志「梯度归约顺序变化，与单卡 run 不可逐位比」；
  DP state_dict 的 "module." 前缀防线 = bc.py `_bc_raw` + worker 既有 raw_model 模式。


## §2026-09-13-eval-replay-export（2026-09-13，控制台「导出 replay」：in-loop eval 局的确定性重放导出）

- **决策**：训练控制台首页「最新 6 轮完整指标」行新增「导出 replay」——弹窗列出最新
  in-loop eval（= eval_summary 最大 iter，与指标表 eval 视图同口径）的全部逐局行
  （类型/击杀/承伤/杀/残血/道具/得分/耗时，表头排序 + 行勾选 + 全部/胜利/失败/超时/
  kills=N 批量选择），导出 = **按需确定性重放**：`rl/eval_replays_once.py` 以课程
  curricula（load_course+apply_course 单一事实来源）重建 difficulty/max_ticks/
  stageJson/lives/level，按 wver（sha256[:16]）解析冻结权重快照（it*/_eval_frozen_weights*
  → 活动权重 → weights/<course> 归档），并行调 `export-eval-game.ts --replay` 重放并录制
  输入 → canonical `.replay`（ReplayBrowser 可直接导入）+ manifest（含逐局 vs eval_log
  账本的 outcome/ticks/kills 确定性对账）→ 控制台 GET /api/evalReplayDownload 打 tar.gz。
  API：GET /api/evalGames、POST /api/evalReplays（loopback-only，busy 互斥同 evalA）、
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-13-eval-replay-export`

## §2026-09-13-goalnn-max-ticks-rule（2026-09-13，D7 终局标准：max_ticks 取值规则一次性立案）

- **决定**：**`max_ticks(count) = 600×count + 900`**（c01=1500 … c20=12900）。斜率 600 沿用
  roadmap 原式（c05-c07 实测该斜率恰好解除 2400 的截断）；固定项 900 = 接敌/穿场/生成节奏的
  一次性开销。判据 = 上限 ≥ 1.25× 实测胜局最大 tick。实测（200 局 God/级，EVAL_SEEDS
  860001-860200）max 胜局 tick / 新上限余量：c01 1121/+34% · c02 1640/+28% · c03 1775/+52% ·
  c04 2510/+31% · c05 2720/+43% · c06 2776/+62% · c07 3381/+51% · c10 3677/+88% · c14 4823/+93%，
  逐点零超时；**上界不缩**（12900 ≥ 原式 12000）。单一实现 = `rl/ladder_factory.max_ticks_for()`，
  由 `nn-training/tests/biz/test_ladder_factory.py` 钉死。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-goalnn-max-ticks-rule`

## §2026-09-14-goalnn-dashboard-project（2026-09-14，用户指令：训练控制台独立为 `dashboard/` bun 项目；启动命令改 `bun run dashboard`）

- **决策**：`tools/training/**` **整棵**（core 领域模块 + console 网站 + ui 组件 + evalboard
  评估板 + `train.ts` python 启动器）搬到仓库根 `dashboard/`，成为一个**独立 bun 项目**
  ——自带 `package.json`（`start` / `launch` / `build:ui` / `typecheck` / `test` / `lint` /
  `format`）、`tsconfig.json`、`tests/`、`README.md`；20 个覆盖它的测试文件从根 `tests/`
  一并迁入 `dashboard/tests/`。根 `tools/training/` 目录**删除**，不再保留任何兼容壳。
  根 `package.json` 的启动脚本 `train` → **`dashboard`**（= `cd dashboard && bun run start`，
  仍听 :8900）。python 无头启动通道改为 `bun dashboard/src/launch/cli.ts --script <name>.py`
  （AGENTS §5.6 的 "never raw python" 语义不变，只是路径变）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-14-goalnn-dashboard-project`

## §2026-09-15-goalnn-gate-pooled（2026-09-15，用户拍板）

- **背景**：多关课正式门跑 200/关（x2 两轮 2400 局）vs roadmap D4 pooled 400 局——量级差 6 倍，“x2 的 80%”与单关课的 80% 不是同一个量（对账结论）。
- **备选与否决**：分关数字进判据 —— 否，分关 SE≈8pp 全是噪声且会掩盖性误杀单关波动；纯 400 局 pooled —— 否，分关诊断能力归零，bc 式偏科（143→132→120）将不可见。
- **决定**：多关课门按 200/关跑足量，**判据只读 pooled**（点估计≥80% 且 Wald 95% LB≥77%，n=800 时 LB≈点估计−2.3pp）；**分关永远只诊断**（配哨兵报告，不进判据）。
- **违反后果**：分关进判据 ⇒ 噪声卡毕业；只跑 400 ⇒ 偏科瞎眼到毕业才爆雷。

## §2026-09-15-goalnn-xn-absorb（2026-09-15，用户拍板 B 案）

- **决定**：工厂模板吸收 xN 语义——c01-c03 多变体（C(4,1/2/3)=4/6/4 关）+
  干净奖励（与 x2/x3-start 逐字同构）；c04+ v2 词干逐字节不动；
  seed_rotate 保持 600（xN 的 240 不吸收）；ladder-c02/c03 与 arena2/3 逐关
  同形由单测钉死；xN 转试点存档（权重链保留，探针表头仍住 xN 文件，JSON 无注释位）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-15-goalnn-xn-absorb`

## §2026-09-15-goalnn-local-ppo-worker（2026-09-15，本机 PPO 拆成独立 worker：控制台 `local` 预设 = hub-server + local-worker + trainer；用户指令）

- **背景（用户指令）**：「把本地 PPO 拆分为一个独立 worker，可以随时启停，与云端 worker 一致，
  同样支持 pull/push 模式。」此前 `--ppo local` 是**进程内**执行：`TrainingLoop`（run_rl）在自己的
  进程里 `load_episodes → chunk_episodes → ppo_update`，整轮阻塞——PPO 不能单独停、不能单独换代码、
  不能挪到另一台机器；而云端 worker（`remote_worker`）早就是无状态、可重连、可随时启停的独立进程。
- **决定（三条，用户逐项拍板）**：
  - ① **本机 PPO = 新的受管组件 `localWorker`**，跑的就是**云端同一个入口**
    `python -m remote_worker --poll http://127.0.0.1:<本课 hub> --out tmp/local-worker-<课>
    --device cpu`（+ 配了 `rl.torch_threads` 才透传 `--threads`）。协议、租约、心跳、幂等重拉、
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-15-goalnn-local-ppo-worker`

## §2026-09-15-goalnn-dynamic-rollout-volume（2026-09-15，采集配额量纲从「局」切「transitions」；plan/dynamic-rollout-volume.plan.md §2）

- **决定**：课程新增三键（全可选；**缺席 = 老行为逐字节不变**）：`target_transitions`（/轮，口径 = 已结算
  shard 的 `nSamples` 之和）、`est_ticks_per_game`（首轮反解局数用，之后由 jsonl trailing 均值覆盖）、
  `max_games_per_stage`（0 = 默认「初波 × 4」）。语义：分关达标线 `ceil(target/关数)`；初波
  `G0 = max(1, ceil(关达标线/est))`；结算后**逐关独立**补波 `ceil(缺口/est)`，每关至多 3 波；掉局零样本不计
  （天然触发补采——特性）、超时局计入；触硬顶 = 停采 + 响亮日志 + iteration 事件 `transitions_capped`。
  纯逻辑住 `rl/volume_waves.py`，账本口径 `rl/resume.settled_stage_totals`，接线 `rl/loop_core.py::_volume_topup`；
  iteration 事件追加 `transitions_target` / `transitions_collected`（additive，旧行无此键）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-15-goalnn-dynamic-rollout-volume`

## §2026-09-15-sandbox-precommit-immunity（2026-09-15，用户指令：杜绝编码 agent 删除保护沙箱反复拦截 pre-commit / 反复弹删除审批；hook 进程树级免疫 + 零删除纪律）

- **背景**：WorkBuddy / Mimo 类编码 agent 自带 python 运行时 + 删除保护沙箱，经两个载体注入：
  **bash 函数**（BASH_ENV 指向 safe-delete-bash-env.sh → 每个**子** bash 启动时把 rm/unlink/rmdir
  重新包装成函数，带每回合删除配额 ~50）与 **python sitecustomize**（守卫 os.remove/shutil.rmtree）。
  后果：mypy 自清理 / pytest 临时目录被塞 SystemExit → INTERNAL ERROR 假红；hook EXIT trap 的
  rm 撞配额 FAIL_CLOSED → 门禁全绿提交却被 git 静默丢弃（2026-09-14 实测 count=181/182；
  2026-09-15 Mimo 再犯：反复重试 commit + 反复弹删除审批）。旧防御只在本 shell `unset -f rm`——
  子进程经 BASH_ENV 重新注入后照拦不误；python 侧开关只写进注释让「调用方设」= 等于不设。
- **决定（三管齐下，只对本 hook 子树生效，不碰宿主环境）**：检测到注入载体（名字含
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/repo-governance.md` §2026-09-15-sandbox-precommit-immunity

## §2026-09-15-goalnn-x3-power-negative（2026-09-15，x3-power 判负派生的三条口径令：判读禁令 / transitions=samples / wChip 破规程序）

- **背景**：含 power 关恒差 15-20pp 被归因「伤害摊薄零代价」，x3-power 用**杀/中信用比**
  陡峭化（wKill 3.0→4.0 / wHit 0.3→0.15，即 10:1→27:1）单变量代理 30 轮。终局同批配对
  （池外种子 400000-400199，四关各 200）：**553/800 = 69.125% vs 基线 576/800 = 72.00%**，
  pooled **Δ=−2.875pp**，McNemar 单侧 p=0.9998（双侧 p=0.0011，净 −3.35σ），pd=5.9%
  ⇒ 判线失败、**判负**。分关/伴随量/事件账全文在 `docs/nn/experiments.md` §4。
- **本条目立的是该腿派生的三条口径/程序（实验记录本身不进本文件）**：
  1. **判读禁令**：KL it30=0.00213（`kl_cap` 本路径不接线，见 §2026-09-15-goalnn-kl-cap-unwired）⇒ 结论的**唯一合法写法**是
     「**梯度无方向 / 执行瓶颈**」（指向 metrics v6，分敌种命中列 TS+Python 全链）；
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-15-goalnn-x3-power-negative`

## §2026-09-15-goalnn-kl-cap-unwired（2026-09-15，x3-step 评审 P0-1：串行/远端路径 kl_cap 不接线）

- **决定**：**禁**在串行/远端课程、结算、progress 中把 `kl_cap` 写成生效硬顶或「咬合」；
  本路径生效旋钮只有 `kl_coef`（软惩罚）+ `lr` + F4 `KL_BREAK`。stream 路径才读
  `args._kl_cap` / `policy.streamKlCap`。x3-power「kl_cap 从未咬合」改读为
  **「该键本路径未接线，谈不上咬合；KL≈0.002 由 kl_coef+小步长决定」**。
  判负八字判决（梯度无方向/执行瓶颈）本身不受影响。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-15-goalnn-kl-cap-unwired`

## §2026-09-15-goalnn-r9-default-abort（2026-09-15，T7：远端连败默认 ABORT，降级本机 opt-in） _(superseded by 2026-09-21「单一 PPO 路径」：`--remote-degrade-after` 与 `--ppo` 均已删除 —— `docs/nn/training-stack.md` §23)_

- **决定**：`--remote-degrade-after` **默认 0**（连败 3 次写 `gate_verdict: ABORT` 停腿）。
  N>0 为显式 opt-in：控制台启动弹窗「降级本机」（localStorage + registry 复现）→
  `--remote-degrade-after 3`；降级前必调 `loop_core._ensure_local_ppo_stack()` 懒加载
  torch/model/opt。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-15-goalnn-r9-default-abort`

## §2026-09-15-gate-trigger-scope（2026-09-15，用户提问「tools 改动有必要跑 src 的测试吗 / src 改动能不触发 dashboard 吗」⇒ 三处触发面重排）

- **决定**：① `test-silent` 删除 basename 映射与 `--strict`：**代码改动一律跑全量**（仍保留 heavy 排除、
  静默输出、失败单跑取详情，以及「跳过无关改动」= 纯文档/课程配置/dashboard-only）。② `FREEZE_STAGED`
  豁免 `^tests/` 与 `^src/assets/`，理由由 `tests/freeze-scope.test.ts` 钉住（算 `per-seed-diff.ts` 的
  import 传递闭包，断言与这两目录零交集、且非空防假通过）。③ 新增 CI `.github/workflows/dashboard.yml`：
  触发 = `dashboard/**` ∪ 它消费的 10 个仓根模块，清单不得落后于真实 import
  （`dashboard/tests/ci-scope.test.ts` 自动核对，拼错的路径也拦）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-15-gate-trigger-scope`

## §2026-09-15-heavy-tests-criterion（2026-09-15，用户提问「根套件实测墙钟 ~6s 下，HEAVY_TESTS 名单还有意义吗」⇒ 判据改为实测 + 名单纠错）

- **决定**：① 名单只剩 `godai-score-gate`；② 判据与实测数字写进 `tools/test-silent.ts` 的 `HEAVY_TESTS`
  注释与 `docs/agents.details.md` §5.3；③ 新增 `tools/measure-suite.ts`（只读剖面 + 达标判定）；
  ④ `tests/test-silent-scope.test.ts` 增硬断言：条目必须命中真实测试文件、名单非空、名字口径与执行器过滤
  一致且在根套件枚举内（实测：伪造死条目 → 2 fail；复原 → 6 pass）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-15-heavy-tests-criterion`

## §2026-09-16-goalnn-x3-step-negative（2026-09-16，x3-step 授权照跑判负：工厂复合第二段禁作可测性治疗）

- **决定**：① 工厂复合第二段（放缰 + 降 lr 同施）**禁**再用作可测性治疗；可测性证明只认
  单旋钮 A/B 或 pd/指纹主端点。② `Δ≤pd` 为判决硬约束：pd<5% 时 +5pp 判线不可观测，
  verdict 可按先例显式注销。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-16-goalnn-x3-step-negative`

## §2026-09-16-goalnn-run-noise-floor（2026-09-16，T6 四臂定案：run 噪声地板规则＋wChip 判死）

- **决定**：① **run 噪声地板规则**：单 run 总 SE≈2.2pp（对局 1.6＋run 1.5）；
  声称效应 <2pp 须 ≥2 独立 run（跨 run 预注册 pooling），否则结算标「未过噪声地板」；
  配对 McNemar/配对 t 只消对局噪声，禁做跨 run 因果断言（纯噪声对 shots t=−2.46
  的 demo）。现有 +5pp 门线安全（5＞2×1.5＋余量），不动。
  ② **wChip 可耐受剂量判死**：dmg/kill 四臂无方向、指纹复印、T6 字面叙事证伪；
  不再开 chip 腿（c4-0.03 前科并案）。③ §46/§50 判负加幅度 caveat
  （未经重复 run 验证），结论不动（详 §52）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-16-goalnn-run-noise-floor`

## §2026-09-16-goalnn-t5-credit-negative（2026-09-16，T5双run一致判负⇒奖励重定价路线关闭）

- **决定**：① 奖励侧重定价路线在 `ladder-c03` 关闭（幅度/方向/粒度均已证伪，
  不再开奖励腿）。② pd 尺修正：本分布 pd≈8% 为噪声地板，5–10% 灰度带无判别力、
  不单独判决。③ 下一腿只认单旋钮放缰 A（零奖励改动；产出＝KL≥0.005＋pd 过地板），
  否则转执行器分支。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-16-goalnn-t5-credit-negative`

## §2026-09-17-kaggle-cred-before-proxy（2026-09-17，Kaggle 引导期凭据必须在装 tailnet 代理之前读；诊断日志落文件）

- **决定**：① `notebook_boot.run()` 在 `ensure()` **之前**一次性读完三个凭据并下传（`_pull` 不再持有
  secret 句柄，签名级防回归）；② `tailscale_boot` 新增 `set_proxy_env()` / `platform_net_env()`：
  NO_PROXY **合并**（不再覆盖平台条目）、记录引导前原值、公网调用前临时还原为平台代理；cell 的
  `_secret` 用同一套（内联 `_platform_net_env()`）；③ 缺 token 时**响亮点名**，不再落到
  「`/code` 401 — HUB_TOKEN 不一致」这种误导措辞；④ cell 日志同时落文件
  （`/kaggle/working/battle-boot.log` → `/content` → `/tmp`），`SystemExit` 收尾/未捕获异常也落文件
  （此前第一手线索全靠人工复制 `[battle]` 行，**SystemExit 正文恰恰不带该前缀**，最容易被漏掉）；
  ⑤ `CFG["ts_engine"]` 阀门（引擎顺序实验，默认 `kernel,userspace` 不变；用于验证
  「kernel 模式那次 TUN/路由尝试动过容器网络」这条待验证假设）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-kaggle-cred-before-proxy`

## §2026-09-17-goalnn-remotewire-m0m2（2026-09-17，远程 PPO 传输计量 + 协议瘦身落地；退路与安全阀是硬要求）

- **决定**：① `wire` 子字典（iteration 事件 + worker result 回传）是**唯一**传输口径，键为 additive，
  旧行无键 = None，`validate_result` 不校验未知字段；`/admin/net-probe?bytes=N` 走鉴权、确定性填充。
  ② 瘦身走**运行期开关**（`slim`，缺省关＝逐字节旧行为），开关取值必须进指标，否则事后无法按选项分组。
  ③ opt/ref 一律对 **raw（编码前）字节**取 sha256 做内容寻址；节点侧重写在 `blob_cache/<sha>` 的那个
  opt 就是它自己刚产出的那份 ⇒ 同会话命中率 100%。④ **安全阀**：`opt_sha` 存在但 blob 取不到 ⇒
  响亮失败（`RetryableError`/`ProtocolError`），**绝不**静默退回 `load_state_into` 开新 Adam ——
  那是把 D5 的动量延续悄悄改成「每轮零动量」，而日志上一切正常。⑤ B5 的 `/job` 体走二进制（BRJ2），
  但 4xx 时**保留一次 JSON 退路重发**：协议不匹配绝不能让一整轮 job 丢在最后一米（同 result v2 规矩）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-remotewire-m0m2`

## §2026-09-17-goalnn-rollout-on-cloud（2026-09-17，M3 rollout 上云：**人工指令覆盖计划的决策门**，

- **决定**：① 新 job `kind="iter"`（走 BC 开过的 kind 通道），manifest 追加必填 `ts_code_sha256` + `rollout`；
  rollout 规格 = 逐局 argv（白名单只放行 `tools/sim/export-rl-rollout.ts`，`--out`/`--weights` 必须 job 内
  相对路径）。② TS 运行时按内容寻址（`pack_ts_code_zip` 固定时间戳 ⇒ 同内容同 sha ⇒ 节点缓存可命中）；
  白名单打**整棵 `tools/**` + `src/**`** 而不是手挑子目录（实测依赖闭包会跨出 `tools/sim` 到
  `../eval/godai-score`；手挑 = 在猜依赖图）。③ 声明集用 `iter_expected_data_fp`，节点侧对实产集
  用**同一个函数**复算并拒收不符 —— 这不是等价替代，是「上云轮没有本地副本可重算」的唯一替代。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-rollout-on-cloud`

## §2026-09-17-hub-restart-deadlock-hardening（2026-09-17，hub-server 重启死锁：D9 改序 + 回环永不封禁 + 原子实例锁 + 停止 trainer 即释放锁）

- **背景**：hub-server「崩溃后手动重启失败」，控制台只报「意外退出」，日志为 `端口 127.0.0.1:8787
  已被占用——拒绝启动（禁止双监听）`。根因链与现场日志、备选否决的完整版见 `docs/nn/remote-transport.md` §6。
- **根因（三层同族）**：① 旧 `_auth_ok` **先查封禁再验 token** ⇒ 本机组件用陈旧 token 连打 5 次就被
  封一小时，且**连正确 token 的健康检查/训练循环/worker 拉活一起 403**；② cloudflared 回源把隧道
  流量也归成 127.0.0.1，回环被封 = 整机服务面连坐；③ 封禁只住**进程内存**、只能重启清除，而旧实例
  还活着占着端口 ⇒ **重启被自己占的端口挡死** = 只能人工杀进程。另两处同族障碍：双监听守卫的
  「探测→bind」TOCTOU（Windows 还能双绑成僵尸）、账本 pid ≠ 锁持有者时「停止→启动」被 trainer 锁卡死。
- **决定（四点）**：① **D9 改序**（`remote/hub_server.py::HubHandler._auth_ok`）——先验 token，
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-hub-restart-deadlock-hardening`

## §2026-09-17-goalnn-python-gate-parallel-policy（2026-09-17，python 门禁并行策略：worker 数 × CPU 内线程数必须成对调；"-n 4 最优" 作废）

- **决定**：门禁 worker = `min(核数, 12)`（`NN_GATE_NPROC` 覆盖）、CPU 内线程 = 1
  （`NN_GATE_THREADS` 覆盖，0 = 不设；须在 python 启动前 export OMP/MKL/OPENBLAS）。
  **同策推广到所有本地入口**（防「门禁快、日常慢」漂移）：`task.py`（线程封顶收在共用的
  `clean_env()`，四个 target 一律 `-n auto`——**同时回退 2026-09-15 把 `-n auto` 判为
  「沙箱 ~34% 停滞头号嫌疑」的误判**）、`nn-training/Makefile`（`NPROC ?= auto` / `THREADS ?= 1`）、
  CI `nn-training.yml`（job 级封顶 + 单测层由串行改 `-n 2`）。**`-n 4` 不再是任何入口的默认值。**
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-17-goalnn-python-gate-parallel-policy`

## §2026-09-17-job-fail-report（2026-09-17，节点**确定性**失败必须带原因回传控制面：`POST /jobs/{id}/fail` + `/result` 410 终局）

- **背景（真缺口，用户 2026-09-17 提问暴露）**：云机确定性失败（bun 装不上 / TS 运行时取不到 /
  argv 非法）此前只落在**云机日志**里。pull 侧 `worker_loop` 的 `except ProtocolError` 只打一行
  「REJECTED … skip (not retried)」——**不回传、不还租约**，训练侧只能等 `wait_job` 25 分钟超时，
  读到的是「超时」而不是「bun 缺失」；push 侧节点 `worker_server` 用 **500** 报 failed，而 500 在
  `push_client.wait_result` 里被当**瞬时错误**重试到 1800s 预算耗尽。两条路都把「确定性能力缺失」
  伪装成「网络/排队问题」，且每次重试再白烧一个超时窗口。
- **备选与否决**：① *只把云机日志写得更清楚*（否决——训练侧读不到，人的第一现场是控制台，不是云机
  stdout）；② *把确定性失败并进既有连败/降级链*（否决——重试只会撞同一堵墙，且
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-job-fail-report`

## §2026-09-17-goalnn-halfoffline-run（2026-09-17，半离线整段：云机领一次就自主跑完，产物可打包下载；hub 失联不影响）

- **背景（用户需求，2026-09-17 两次确认）**：现状是 **hub 拥有迭代循环**——每轮 publish 一个 job、
  节点跑一轮就回等；hub 一断，云机除了等无事可做。用户要的是：云机领到（课程 + 初始权重 + 代码）
  后**即使本机 hub 一直失联**也能全程自主跑完，并以 Kaggle/Colab 官方方式（工作目录产物 zip）交付
  **逐轮**权重与指标。用户口径（三问已答）：一次领 = 整段；逐轮权重/指标/可续跑；**不在云上跑评估**。
- **备选与否决**：① *把 hub 的迭代循环抄一份到云上*（否决——`build_pairs`/`build_rollout_cmd`/课程重建
  各有**唯一**一份，抄一份就是造第二个真相）；② *等 hub 重连再逐轮问*（否决——正是要治的病）；
  ③ *产物边跑边 POST 回 hub*（否决——失联时 POST 必失败，产物会跟着丢；产物必须落**本机**，回传尽力而为）；
  ④ *新写一条「段」执行链*（否决——本轮语义与 kind=iter **逐字段同构**）。采用：**kind="run" = kind="iter"
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-halfoffline-run`

## §2026-09-17-goalnn-offline-task-bundle（2026-09-17，全离线任务包：hub 导出 → Kaggle/Colab 上传 → 云机自主跑完；包是搬文件不是配网络）

- **背景（用户需求，2026-09-17）**：「hub 支持打包导出训练任务（课程、初始权重、代码），以
  kaggle/colab **官方支持方式**上传云机后，云机全程自主完成训练，并以官方方式提供产物打包下载；
  若中途能连上 hub，云机自动恢复产物在线回传」。半离线（`kind="run"`）已经解决了「云机不依赖
  hub 也能跑完 + 产物落盘」，但**任务本身还在网络上**（云机要轮询 hub 领 job）——全离线要的是
  「hub 关机也能开工」。
- **备选与否决**：① *让云机 clone 仓库 / pip 装依赖*（否决——官方入口是「数据集 / Drive / 文件
  上传」，不是「配网络」；rollout 用 TS、训练用 torch，装环境本身是一整天）；② *拆成一堆 curl +
  环境变量*（否决——把「搬文件」变成「配网络」，正是要消除的东西）；
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-offline-task-bundle`

## §2026-09-17-goalnn-offline-reconnect-delivery（2026-09-17，产物补传：中途能连上 hub 就自动恢复在线回传；不可影响训练是唯一硬约束）

- **背景（用户需求，2026-09-17）**：全离线任务包（上一条）交付了「hub 关机也能开工」，需求的后
  半句是「如果训练中途发现可以联通 hub，云机也能自动恢复产物在线回传」。补传 = 产物目录之外
  的**第二份拷贝**（产物本来就已经落在节点本地目录），让控制面不必等人搬 zip。
- **备选与否决**：① *新增无鉴权 `GET /health` 探活*（否决——探针要回答「我能不能用这条链」，
  只证可达会让「token 配错」在第一次上传 ~1.9MB 后才暴露，且多一个向公网泄露「hub 在线」的
  端点）；② *逐轮产物覆盖写*（否决——同一轮权重是不可变快照，覆盖 = 谁先到谁定义历史，而补
  传天然会重传）；③ *自定义裸二进制容器*（**部分否决**：比复用 `encode_weights_json` /
  `encode_opt_tar` 省 33% 体量 ≈0.5s/轮，但要多养一种容器格式 + 两端实现；best-effort 旁路不
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-offline-reconnect-delivery`

## §2026-09-17-goalnn-console-task-bundle-exchange（2026-09-17，控制台「导出任务包 / 导入产物即评估」；控制台不重造包格式）

- **决定**：
  1. **智能在 python 一侧，控制台只做三件事**：拼 argv（`--course/--ppo remote/--run-iters -1/
     --export-bundle <abs>`）、起一次性 detach 进程（日志 `logs/<课>/export-bundle.log`，
     **不注册组件**——进账本会被监督器当「该重启的组件」）、把产出文件交给浏览器
     （`GET /api/taskBundle` 流式 `Bun.file` + Content-Disposition）。
  2. **导入 = 上传（multipart，不走 JSON 动作层）+ `python -m remote.deliver_zip`（同步，
     调用方需要它的结果）+ 接着起 `evalA`**。评估评**包里末轮**权重、用课程配置语料；
     评估起不来**不算导入失败**（产物已落地可读，那是这一半的全部价值），响应里明说原因。
  3. `evalA` 唯一启动点抽到 `eval-a-run.ts`：按钮与「导入后自动评估」共享命令**与互斥键
     （`eval:A`）**——两处各写一份会出现「按钮说在跑、导入那边不知道」。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-17-goalnn-console-task-bundle-exchange`

## §2026-09-17-goalnn-unified-node-gate（2026-09-17，用户指令：rollout 与 eval 统一用 tools/agent/codehash-files.txt 判定节点是否可用）

- **背景**：rollout 门比 `codeHash`（展开自 SSOT 清单），eval 门比 `engine_epoch = sha256(git_full_commit + GAMEPLAY_SPECS 指纹)[0:16]`——掺了 git commit，且 gameplay 集是 TS（codehash-files.ts）/Python（dist_common.py）两侧手工镜像的第二份清单。任何与 rollout/eval 无关的提交（dashboard / nn-training / docs）都把全节点判 stale，逼运维同步+重启 sampler-agent（2026-09-15 x3-power it30：epoch 全员 mismatch → nodes_ok=[] → 600s 零局）。
- **备选与否决**：只从 epoch 删 git commit、保留 gameplay 子集 —— 否，eval 门仍不是清单派生的第二事实来源，且 src/nn 策略代码改动的漏网口重新打开；epoch 仍只覆盖 gameplay、eval 门另改比 codeHash —— 否，清单不含引擎文件时 codeHash 不覆盖 eval 语义，等于把漏网口从 epoch 挪到 codeHash；彻底删除 `engineEpoch`*账本字段*（账本改存 codeHash）—— 否，`EvalGameRow.engine` 与 S10 哨兵已按该值落存量账本，改口径要动历史数据；但 ping 里的 `engineEpoch` 是另一回事（它不是门，删——见决定④）。
- **决定**：① `src/game/**`·`src/config/**`·`src/utils/**`·`src/ai/**`·`tools/det-golden.v1.sha256` 并入 `codehash-files.txt`（唯一事实来源；排除原则同处写明 dashboard/nn-training/docs/plan/tests 严禁入集）；② `engine_epoch = sha256(codeHash)[0:16]`，三处实现同式（codehash-files.ts `engineEpochFromCodeHash` / dist_common.py / evalboard engine.ts），节点门与 rollout 同源，**不掺 git commit**；③ 删除 GAMEPLAY_SPECS 双份清单，账本仍存该值，`git_commit` 降为纯观测字段；④ **`/v1/ping` 删除 `engineEpoch`**，eval 门（`eval_dispatch` / `batch_eval`）改比 `codeHash`（新 `dist_common.check_code_hash`，`check_engine_epoch` 删）——rollout 与 eval 落到**同一个字段、同一个判据**；旧 agent「无 engineEpoch → 过渡期放行」的分支随之取消（codeHash 是 rollout 门一直在用的字段）。本条**撤销** §2026-09-10-goalnn-rleval 中「不扩 codehash-files.txt、另立 engine_epoch」的否决。
- **违反后果**：无关提交再次触发节点重启波（运维被迫重启 sampler-agent）；或引擎文件被移出清单 ⇒ 异构 gameplay 的 stale 节点过门（S10 沦为摆设）。
- **落地**：`tools/agent/codehash-files.txt`/`codehash-files.ts`（+`engineEpochFromCodeHash`）、`tools/agent/sampler-agent.ts`（ping 去 `engineEpoch`，删 gitFullHash/memo）、`nn-training/dist_common.py`（`check_engine_epoch` → `check_code_hash`）、`nn-training/rl/{eval_dispatch,batch_eval}.py`（eval 门改 codeHash）、`dashboard/src/evalboard/engine.ts` + `dashboard/src/server/eval-board/ingest.ts`（`dist_codehash` 与节点门同值，诊断一眼可比）；回归：`tests/dist-agent.test.ts`、`dashboard/tests/evalboard-engine.test.ts`、`nn-training/tests/{test_eval_ingest,test_upgrade}.py`。
## §2026-09-17-goalnn-eval-wallclock（2026-09-17，用户指令：压缩 PPO 之后的 eval 软等窗口，并让本机 eval 份额更早开始；同日追加：边界收拢取代固定秒数）

- **背景**：`_dispatch_delayed_eval(it)` 已排在 `_serial_ppo(it)` **之前**（读归档 W(it-1)、标权重轮 → 节点侧 eval 局藏进本轮 PPO 窗口），但 `_join_eval` 事后**硬编码软等 180s**，本机预留份额（`policy.evalLocalSlots`）的 gate 又只在 `_join_eval` 置位 ⇒ 本机局在 PPO 收尾后才开跑，两段墙钟叠加成「PPO 后的第二次串行等待」。
- **备选与否决**：软等一刀切为 0 且**不做任何收拢** —— 否（同日修正：固定秒数本身是错形状，但“完全不看尾巴”也不对：需要知道它是否跑崩了、并在收官前清账），故改为在**自然边界**收拢（见决定①）；把本机 gate 改成「派发即放行」不分档 —— 否，本机 PPO 占本机全部核心，本机局同时开跑会与 torch 抢核（R6 的原始理由仍成立）；仅在 `_join_eval` 里把软等改成 0 而仍让本机份额等 gate —— 否，两处是同一个问题的两个面，只改一半仍留下「PPO 后串行等待」；边界处 join 等到它自己的 deadline 为止（“有界等待”） —— 否，届时尾巴已跑过整段采集（分钟级），还在活就是异常（节点慢/挂了），等它只是把异常成本转嫁给主链，改为**零等待 + WARN + 交后台**。
- **决定**：① **不站等任何固定秒数**：per-tick 的 `_join_eval` 只放行/记录，未收官的尾巴整根交给 `_sweep_eval_tail`，在**下一轮 rollout 收官**这个自然边界收拢（`_dispatch_delayed_eval` 入口 + 收官 `_drain_pending_eval` 各一次；非阻塞、只观测/清账）；尾巴在整段采集期间自己跑完就自己写 summary（wver 键控、幂等），还在跑的只打 WARN（用**它自己的** `eval_window_sec` 作时间基准判“是否越窗”，不引入新魔数）并继续后台消化。`policy.evalJoinSoftSec` 保留为**应急旋钮**，**缺省改为 0**（>0 = 回到旧的“PPO 后最多站等 N 秒”，坏值/NaN 回落 0，负值夹 0）；intent/goal 模式不动（止损判门要吃同轮 summary，仍走 `window+60` 全预算 join）；② 本机份额按「本轮本机是否跑 PPO」分档放行（`policy.evalLocalEarlyEpochs`，缺省 1）：远端 PPO / 整轮上云 / stream 轮 ⇒ **immediate**（本机核心此刻空闲，派发即开闸，`hold_for_local` 预留同步解除）；本机 PPO ⇒ **last_epoch**（复用 `ppo_update` 的 `on_epoch_done` 钩子，判据 `ep_done >= epochs - early`，与吞吐 T4 预采同口径）；`early=0` ⇒ **on_join**（维持 R6 原语义）；远端降级本机时 `_regate_local_eval` 收回我们提前放的 gate（节点饥饿兜底放行的 gate 不收回，否则本机局被卡到收官）——分档判据 `local_gate_release_plan`/`early_epoch_reached` 与 `hold_for_local` 一样是**纯函数**（`rl/eval_local.py`），可单测、不依赖运行环境。
- **违反后果**：把固定秒数的站等重新加回 `_join_eval` ⇒ 每轮 PPO 后白站等被悄悄加回主链；把尾巴的收拢点从“下一轮 rollout 收官”挪回“本轮 PPO 之后”⇒ 尾巴失去整段采集时间，只能靠等待；把本机份额退回「只在 `_join_eval` 放行」⇒ 本机语料份额在 PPO 后串行补跑；无分档地「派发即放行」⇒ 本机 PPO 轮与 torch 抢核。
- **落地**：`nn-training/rl/eval_local.py`（纯函数/常量：`eval_join_soft_sec`/`eval_local_early_epochs`/`local_gate_release_plan`/`early_epoch_reached`/`eval_tail_overran`）、`nn-training/rl/loop_steps.py`（`_eval_policy_cfg`/`_eval_join_soft_sec`/`_regate_local_eval`/`_local_gate_epoch_hook`/`_sweep_eval_tail`，`_join_eval` 不站等+交棒、`_dispatch_delayed_eval` 边界收拢+分档放行、`_serial_ppo` 降级收回 + 注入 epoch 钩子）、`nn-training/rl/loop_core.py`（`_eval_gate_early_released`/`_eval_tail`/`_eval_tail_start` 初值）；回归：`nn-training/tests/worker/test_eval_timing.py`（16 例，含“缺省零 join”与“边界收拢”）、`nn-training/e2e/test_run_rl.py`（`eval_deferred`/`eval_post_ppo_weights`/`eval_local_gate`/`tail_join_grace`/`early_race`）。
## §2026-09-17-goalnn-race-broadcast（2026-09-17，用户指令：只有一个课程在训练 + 多个云机 GPU worker ⇒ 竞速广播，最新 job 派给每个 worker，先回传者胜、后到者丢弃） _(superseded by §2026-09-22-goalnn-race-retired-priority-only)_

- **背景**：`GET /jobs/next` 现为 P3b 独占加超时（领取即设 300s 租约）⇒ 单课程多卡时只有一张卡在干活，其余卡空转。竞速广播是 §343（2026-09-06）的原语义，被 §2026-09-12 P3b 以「多课程并行时同 job 被重复算、慢者 409 白烧」为由 supersede；用户给出的前提（**单一课程** + 多卡）恰好消掉那个顾虑（那些卡本来就在同一个 hub 上空转）。
- **备选与否决**：“现在跑了几门课”由全局课程表/控制台判定 —— 否，没人能可靠回答（hub 每课一进程，看不见别课；worker 可能同时轮询多个 hub；运维还得记得关）；只在 worker 侧自选（“我只服务一个 hub 所以我去竞速”）—— 否，会把独占租约与竞速副本混在一起（同一 job 一半独占一半广播）；不做判据、直接恒开（回到 §343）—— 否，多课程机群上重复烧卡正是 P3b 修复的回归；广播**所有**在池 job —— 否，陈旧 job 上堆满 worker 是纯浪费（只广播最新那份）。
- **决定**：① **判据 = worker 自报的事实**：`X-Worker-Id`（身份）+ `X-Hub-Scope`（它 `--poll` 几个 hub），hub 保留 180s 窗口内的登记表，`race_decision(mode, workers, now)`（纯函数，`remote/protocol.py`）在 auto 下要求「≥2 个**不同** worker 且全部 scope==1」——多 hub/未知 scope 任一出现即退回独占（P3b 语义不被破坏），单 worker 不广播（只它一个执行者时广播无收益）；② 竞速轮：`claimable_job_ids(race=True)` 让**最新** job 忽略租约（更老的维持独占），`claim(race=True)` **不下租约并清掉先前那份独占租约**、返回空 token（worker 侧“无租约 ⇒ 不心跳”是 §343 既有分支，故无协议破坏）；③ 胜负由 `store_result` 首写锁定：赢家 200/201，后到者 **409**——worker 对 409 本来就按成功处理（“hub 已有同 job 结果”），不重试、不报失败；④ `_post_result` 必须在**租约校验之前**先判“结果已落盘”并 409（否则先独领、后转竞速的时序里，输家会因非持有人拿 **403**，而 worker 把 4xx 一律读成确定性拒绝并上报 job 失败——一个赢家把输家炸成事故→见 `test_race_releases_earlier_exclusive_lease`）；⑤ 应急闸：`rl.race_mode=auto|on|off` → `hub-server --race`，并可 `POST /admin/race?mode=...` 热切（volatile，切后清空登记），`GET /admin/race` 为观测面；`/admin/workers/status` 体形状**不动**（console 读它）。
- **违反后果**：把判据改成配置/人工声明 ⇒ 多课程并行时忘关 ⇒ 同 job 在多卡重复算（P3b 回归）；少了“广播时清旧租约 + 只认首写”两道 ⇒ 输家拿 403 被读成确定性失败，job 被钉成 fail.json 终局；广播所有在池 job ⇒ 陈旧 job 上堆卡空烧。
- **落地**：`nn-training/remote/protocol.py`（`RACE_MODE_*` / `HUB_SCOPE_HEADER` / `WORKER_ID_HEADER` / `RACE_WORKER_WINDOW_SEC` / `parse_hub_scope` / `race_decision`）、`nn-training/remote/hub_server.py`（worker 登记表 + `race_active`/`race_state`/`set_race_mode`/`clear_workers`，`claimable_job_ids(race)`/`claim(race)`，`/jobs/next` 接线 + RACE 日志行，`_post_result` 前置 409，`GET|POST /admin/race`，`--race`）、`nn-training/remote/worker.py`（上报身份/范围 + 竞速副本日志 + 409 措辞）、`dashboard/src/core/types.ts`（`RaceMode`/`normalizeRaceMode`）、`dashboard/src/stack/specs.ts`（`--race` 透传）；回归：`nn-training/tests/test_race_broadcast.py`（22 例）、`dashboard/tests/hub-server-race-arg.test.ts`（4 例）。【2026-10-01 追记：本条**已由 ▼ §2026-09-22-goalnn-race-retired-priority-only supersede**；两文件均随判定退役删除：nn 侧继任 = `nn-training/tests/hub/test_priority_schedule.py`，dashboard 侧**无继任**（`--race` 旗标与 `/admin/race` 已整体移除；hub argv 面现存 `dashboard/tests/hub-server-push-arg.test.ts`）。】后续（本轮明确不做）：赢家落账后叫停还在算的副本（省 N-1 份 GPU）。

## §2026-09-19-goalnn-console-course-mode-toggle（2026-09-19，plan R3-2：控制台每课离线/在线开关 + 意图回灌）

**背景**：hub 的「离线课」语义**早就实现且被 e2e 钉住**（`POST /admin/courses?mode=offline`：
不实时派发、只收 it 权重/指标回传），但**全仓没有任何控制台代码调它**——面板上只有一个只读
徽标，想真用这个闸只能手敲 curl；而且 hub 的 `mode` 是 **volatile**（重启回启动参数）⇒
hub 一重启就**静默**恢复派发。这是「功能存在但不可达 + 重启即失忆」的两层缺口。
**定案（`actions/course-mode.ts` + 总览卡开关）**：
1. **热切 + 意图落盘**（`setCourseMode`）：调 hub `/admin/courses` 后把意图写进
   `console-state.courseModes[课]`（additive 键，旧 state 文件读作空表）。
2. **起 hub 时回灌**（`restoreCourseModes`，接在 `startComponent('hubServer')` 的两个分支上）：
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-goalnn-console-course-mode-toggle`

## §2026-09-19-goalnn-slot-cap-5-and-loud-rejection（2026-09-19，plan R3-1：课程上限 5 + 越界槽位响亮拒启）

**背景**：用户口径「允许同时训练多个课程，上限先设为 5」（2026-09-18），而实现是
`dashboard/src/core/slots.ts::SLOT_COUNT = 4`，且 `slotOf` 对**越界值也静默回落 0**。
两者叠加 = **第 5 门课必然越界，然后静默用第 1 门课的 push 端口**：两门课的本机 push
互相顶掉，而且没有任何一行日志说这件事（只有端口占用冲突能间接看出）。这是真 bug，
不是「缺功能」。
**定案（三件事，都在 `slots.ts` 一个聚合点内）**：
1. **`SLOT_COUNT` 4 → 5**（= 用户口径的并行课程上限）。hub/隧道自 2026-09-18 起是单实例，
   槽位不再决定 hub 端口——它只剩「本机 push（worker_server）端口」与历史 metrics 命名，
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-goalnn-slot-cap-5-and-loud-rejection`

## §2026-09-18-goalnn-multi-course-single-hub（2026-09-18，用户指令：多课程并行训练流程与操作重组；单进程服务所有课程 + hub 队列 + 分课程权重缓存）

- **背景**：多课程并行（`docs/multi-course-audit.md` / plan multi-course-parallel-training）当时定的形状是**课程 = 并行单元**：每门课各一套 hub-server / cloudflared / trainingLoop / localWorker / workerServe 进程，端口按槽位 `base + slot*10`，账本按课程键控。它解决了「第二课覆盖第一课登记」那类串账，但代价是进程数按课程线性增长（5 课 = 25 个进程），且 **hub 完全不知道「课程」这回事**（`--job-root`/`--jsonl` 都是每课程路径），所以没有跨课程的调度面：一门课积压 20 轮就独占自己的 hub，别的课的 worker 空转。
- **用户要求**（本次）：① 上限先设 5；② hubserver/trainingloop/selfNode/cloudflared **各只需一个进程**；③ hub 设 PPO 任务队列（push 按队列顺序推给空闲 worker，推送超时回落队首改推其它 worker；pull 按队列顺序分发给请求者）；④ 离线模式课程不实时派发 PPO、但接收 it 权重/指标回传；⑤ rollout 集群按课程缓存最近权重，避免同一份权重多次传递；⑥ 并行课程数 < PPO worker 数时用单课程那样的竞速；⑦ 面板重组（课程 select 不锁死、在训课程高亮等）；⑧ e2e 保证畅通。
- **决定（P1 已落地部分）**：
  - **单 hub 多课程 = 一个进程托管 N 份 `_JobStore`，磁盘契约逐字节不变**（每课程仍是 `tmp/<course>/remote-jobs` + `training_log.jsonl`）。这是本设计最关键的取舍：多课程只是「同一进程里多挂几份账本」，不是换一套磁盘约定 ⇒ 既有工具、既有 100 个单课程 hub 用例、`tmp/<course>` 约定全部照旧，回滚只需改启动参数。
  - **调度面 `_HubQueue`**：每课程一条 FIFO（沿用发布序）+ **跨课程轮转**（`rotation_order`，从上次派发的下一门开始——否则一门积压把其它课饿死）；**离线课程不参与实时派发**（只收回传）；`active_courses()`（非离线且有待领或在飞）是竞速分母。
  - **超时回落队首 + 改为派给别的 worker**：`claim` 在回收过期租约时记下「谁跑死的」（`_stale_holders`），队列在**还有别的活跃 worker** 时把该 job 避开那位前持有人（`may_avoid_stale_holder` 是闸，身份比对在 store 内——在队列层先读 stale 记录会踩时序，实测恒为空、避让永不生效）。独苗时恒允许自领（否则那台 worker 永远空转）。
  - **竞速口径改为按课程数**：`race_decision(..., active_courses=N)` 要求「窗口内不同 worker 数 **严格大于** 在派发课程数」。缺省 `active_courses=1` 时与旧口径（≥2 worker）**逐字节等价** ⇒ 旧调用与旧用例不变。多 hub worker 仍是安全条件的一票否决（scope 不随课程数放宽）。
  - **鉴权面提取为 `_AuthGuard` 且进程级一份**：多课程下不按课程各算一套失败计数（否则「同来源 5 次无效鉴权」的封禁阈值变成 5×N）；`_JobStore` 继承它，旧调用不变。单课程队列**借**那一份 store 的鉴权/竞速/停机状态（不是洁癖：既有用例会在 store 上预热封禁态再发 HTTP）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-18-goalnn-multi-course-single-hub`

## §2026-09-18-goalnn-hub-push-dispatch（2026-09-18，用户指令：hub 中介的 push 派发 + worker 登记入口 + 周期探活，训练侧 push 改走 hub）

- **决定**：
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-18-goalnn-hub-push-dispatch`

## §2026-09-18-goalnn-console-worker-register-and-overview（2026-09-18，用户指令：控制台 worker 登记入口 UI + 面板重组）

- **决定**：
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-18-goalnn-console-worker-register-and-overview`

## §2026-09-18-goalnn-single-hub-single-tunnel（2026-09-18，用户指令：hubserver/cloudflared 只需要开一个进程，就能同时支持所有并行训练课程）

- **决定**：
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-18-goalnn-single-hub-single-tunnel`

## §2026-09-18-goalnn-loopback-http-no-proxy（2026-09-18，门禁实测红：本机 127.0.0.1 请求被环境代理截走）

- **决定**：
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-18-goalnn-loopback-http-no-proxy`

## §2026-09-18-goalnn-r2-loop-task-queue（2026-09-18，用户指令：训练循环任务队列化 —— trainingLoop 由一个进程服务所有并行课程）

- **背景**：`trainingLoop` 一直不是「请求处理器」，而是**有状态会话**：`TrainingLoop.__init__`（`nn-training/rl/loop_core.py:151`）声明约 60 个跨轮字段（进度指针 / 门禁计数 / 在飞线程与子进程 / torch 模型与优化器 / 轮内瞬态），`run()` 是 190 行顺序脚本——一步阻塞整条腿阻塞（等远程 PPO、等 rollout 子进程、等 eval 尾巴）。所以「多课程 = 多进程」不是设计选择而是形状的必然结果，与用户口径「hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程」冲突。用户进一步给定形态：**任务队列，任务自带一切**（与 hub 的 job 同构）。
- **决定（设计稿 `plan/r2-loop-task-queue.md`）**：
  - **状态五分类**（逐字段给出归宿）：**A 指针**（`next_it`/`rotate_seed`/阶梯＝`it` 的纯函数）· **B 门禁指标**（连击/累计量/止损/提示类计数）· **C 在飞集**（`job_id`/等谁回传/eval 尾巴/预采子进程——线程句柄不可序列化 ⇒ 落盘的是**意图**，重启按意图重建）· **D torch 对象**（每课内存缓存最新 checkpoint：权重 + Adam）· **E 轮内瞬态**（`_report`/`_agg`/`_volume_*` 等，**禁持久化**——持久化它 = 拿旧数字记新轮）。
  - **任务模型**：一轮拆成 13 个细粒度任务（`prepare_iter` → `rollout_dispatch/wait` → `ppo_publish/wait` → `export_weights` → `eval_dispatch/join` → `gate_eval` → `record_iteration` → `cleanup`）；执行器返回四态 **DONE / WAIT / RETRY / ABORT**。`WAIT`（等远程结果、等 eval 尾巴）**不占执行权**是单进程多课程的关键；持资源的步才是串行的（用户口径：用任务队列防资源竞争）。
  - **每条任务必须先有盘上判据**（幂等 guard：shard 齐 / 权重落位 / 账本已有 `iteration` 行）——这条同时是「重放安全」与「扫账本」的同一件事。
  - 每课一份 `train-loop` 状态文件（`loop-state.json`，v1）：**账本永远是 SSOT**，该文件只是加速器，版本不认/字段缺失即从账本重建。
  - **用户定案四问（2026-09-18）**：① 直接做到 **R2c 单进程**（R2a/R2b 为途中产物）；② 任务粒度 = **细粒度步骤 + WAIT 让位**；③ 本机重资源（`local_ppo`/`eval_local`）**跨课排队、池容量 1**（rollout 子进程池不受影响）；④ checkpoint 缓存上限 = **并行课程上限（默认 5，`rl.checkpointCacheCourses` 可配）**，RSS 实测表是 R2c 上线前置。
  - **门禁扫账本（用户裁决）**：门禁语义逐条从「内存计数」改成「扫账本」；指标**按课程缓存**，只在开课/续跑读一遍，之后由写事件处**增量**维护。**不新造账本**：`training_log.jsonl` 已含 `run_start`/`iteration`/`gate_verdict`/`iter_error`/`circuit_break`/`run_complete`，且已有五个扫描器（`rl/resume.py`、`rl/gate_check.py`）——R2a 只是把它们收敛成**一份视图**。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-18-goalnn-r2-loop-task-queue`

## §2026-09-19-goalnn-serve-bc-course（2026-09-19，R3-4：单进程 supervisor 也能带 BC 课）

- **决定**：把 BC 的编排体**逐字节**搬进 `rl/bc_loop.py`（`BcLoop` 引擎 + `BcRuntime` 解析 + 纯函数），`run_bc.py` 退为**入口薄壳**（进程级一次性副作用 + 阻塞式驱动）；`serve` 按课程种类分派引擎与粒度。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-19-goalnn-serve-bc-course`

## §2026-09-19-goalnn-console-card-families（2026-09-19，R3-3：组件卡按「单例角色 vs 按课程」分族）

- **决定**：把「作用域」提升为一等事实，卡片按它分族渲染（`web/view/component-groups.ts`）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-goalnn-console-card-families`

## §2026-09-19-goalnn-shared-trainer-single-process（2026-09-19，R3-5：trainer 收敛为「一个进程服务所有课程」）

- **决定**：`trainingLoop` 进 `SHARED_COMPONENTS`（账本槽恒 `''`，与 hub/隧道同一张表同一套哨兵），控制台一律拉起 `run_rl_cluster.py --serve`；卡片自动从课程面落到**服务面**（族归属是 `componentScope` 的函数，R3-3 已把这条路修好——本轮只改判据，不改 UI）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-19-goalnn-shared-trainer-single-process`

## §2026-09-19-goalnn-shared-local-worker（2026-09-19，用户指令：localWorker 也不应绑定课程）

- **决定**：`localWorker` 进 `SHARED_COMPONENTS`（账本槽恒 `''`，与 hub / 隧道 / trainer 同一张表同一套哨兵），spec **与课程无关**（`course: ''`、单一 `tmp/local-worker` work 目录、单一 `tmp/local-worker.log`）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-goalnn-shared-local-worker`

## §2026-09-19-goalnn-retire-local-fake-node（2026-09-19，用户指令：workerServe 伪节点直接从 dashboard 去掉）

- **背景**：`workerServe`（`remote_worker_serve`，本机伪 GPU 节点）是控制台里的第五个受管组件：有卡片、有账本键、有日志页入口、有端口兜底清场、有变更检测重启、有一套「无可用 gpu_push 就回落本机」的 push 预设。用户口径：**「它只是用于 trainingloop 冒烟测试，用户只关心冒烟是否通过，不会手动去开启/停止伪节点」**。
- **决定**：伪节点**整体退出控制台**——不是换个轴（原计划 R3-6 否决④的「收敛为节点轴」也不再需要），而是从「受管」这件事里退出。它此后只以**冒烟预演的一次性配角**存在（`stack/push.ts` 起、跑完/失败即杀）。
  - **受管面全删**：`Component` 键、`COURSE_COMPONENTS`、`PLURAL`/`Registry.workerServes`、`ALL_COMPONENTS`、`restartSpecFor` 分支、`startComponent` 分支、`stopComponent` 端口表、`smokeComponent` 分支、`COMPONENT_LOGS`/`HEALTHY_PORTS`/`LOG_NAME_MATCH`、`WORKER_SERVE_ENTRY`/`workerServeSpec`（ProcSpec）。
  - **冒烟自起自停**：`stack/push.ts::startLocalWorkerServer` 直接 `spawnBg`（不再造 ProcSpec——它没有账本键可用），仍按课程取槽位 push 端口与 per-course work 目录（**双课同冒**不得互踩），20s 未就绪则杀掉自己起的进程再抛错（不再留一个没人认领的孤儿）。
  - **「一键本机 push」一并删除**：`applyLocalPushNodeConfig` / `localPushUrl` / `configurePushEndpoint` 的 `allowLocal` opt-in / `findHealthyGpuPushNode` 的 `includeLocal` / `PushTarget.viaLocalWorker` / `preset.ts` 的第三条启动顺序分支。**理由**：执行面解析在 2026-09-15 就已经把「缺 gpu_push → 自动回落本机」改成响亮报错（那会把「云机连不上」伪装成「训练正常」），剩下的 opt-in 只是测试口；而在伪节点不再是受管组件之后，这条路径唯一的效果就是**把课程 push 目标指向一条没人服务的本机地址**——留着就是一条静默失败通道。
  - **读面只留「识别」**：`NodeConf.local_push` 标记保留，`pushTargetFromConfig` 仍把它报成 `kind: 'local'`——历史配置里残留的 `local_push` 条目与指它的 `courses.<课>.push_node_url` 必须**看得见**（卡片会显示「执行面 = 本机」），否则操作员看到的是「一切正常」。复用扫描**一律排除**它（没有 opt-in 了）。
  - **UI 的连带退出**：`NODE_FACE_COMPONENTS` 例外名单删除（它存在的唯一理由就是这个键）；`ComponentFamilyId` 回到两族；`LogNavCard` 的 filter 消失（受管组件全集 = 日志页入口全集）；`TrainLaunchModal` 的 endpoint 文案改为「留空 = 复用 config 里 ping 通的 gpu_push；都没有则**响亮报错**」。
- **备选与否决**：① 保留卡片但默认隐藏——否（隐藏 ≠ 不存在：端口兜底清场仍会杀它、变更检测仍会重启它，操作员无法理解一个自己看不见的东西被谁停掉）；② 改成「节点轴」账本键（原 R3-6 否决④的延后项）——否（它的语义轴是**冒烟预演的临时件**：生命周期 20s、只服务一次预演、与任何 GPU 身份无关；给它造节点轴只是给一个不该常驻的东西一个常驻身份）；③ 顺手把 `run_rl.py --smoke` 的伪节点依赖也去掉（改用假执行器）——否（预演要证明的正是「真课程发布 job → 推送 → 执行 → 回传 → 落位」这条链路本身，换假件会让它不再证明它该证明的事）；④ 保留 `allowLocal` 供离线诊断——否（见上：它此刻唯一的效果是静默指向死端点）。
- **违反后果**：任何「重新给伪节点加受管键」的改动都会让它重新出现在「服务面 · 单例」卡行（用户明确不要的管理面）；任何在 `configurePushEndpoint` 里恢复「回落本机」的改动都会让云机连不上时的现象变成「训练正常」（旧缺陷原样复活）；把 `local_push` 标记从读面删掉则会让历史配置里的本机执行面**静默**留在 `courses.<课>.push_node_url` 里。
- **落地**：`core/{registry,types}.ts` · `stack/{specs,push,push-config}.ts` · `server/actions/{start,stop,restart,smoke,preset}.ts` · `server/api/{component-meta,logs}.ts` · `web/view/{component-groups,console-types,course-overview}.ts` · `web/app/panels/{LogNavCard,TrainLaunchModal}.tsx`。回归：`tests/push-config.test.ts`（本地回落 describe 重写为「没有写入口、只有识别面」+ 三把防回流尺子：受管组件全集 / 启动面与 spec 面 / 冒烟侧确实自起自停）· `tests/{server-api-logs,server-api-state-view,single-hub-tunnel,training-multi-course,web-component-groups,web-components,training-port-reclaim}.test.ts` 同步真值。训练侧只改一句日志提示（`remote/worker_server.py` 的 `CodeChangedError` 指引不再是「重起 workerServe」，而是「重跑 `remote_worker_serve`」）。

## §2026-09-19-goalnn-course-worker-orthogonal（2026-09-19，用户口径：启动训练不选 pull/push 模式）

- **决定**：**传输不再是课程属性，也不再是启动选项**——它是部署事实，由「有没有登记节点」推出来。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-goalnn-course-worker-orthogonal`

## §2026-09-19-goalnn-test-port-contention（2026-09-19，平台性存量红：e2e「探端口 → 起子进程 bind」的 TOCTOU）

- **背景**：R4 提交被 pre-commit 的 nn python gate 拦下，红的是一条与本轮改动毫无关系的 e2e：`test_multi_course_single_hub_e2e.py::test_offline_course_is_parked_and_resumes_on_going_online` —— `hub-server 未就绪或课程表不对（rc=1）`，真因只埋在子进程输出里：`[hub-server] ERROR: 端口 127.0.0.1:53637 已被占用——拒绝启动（禁止双监听）`。根因是测试侧的经典 TOCTOU：`_free_port()`（`bind(0)` → `close()`）与子进程真正 `bind` 之间有一个足以跑完 Python 冷启动（~1s）的窗口；gate 用 pytest xdist，另一个 worker 的探测会拿到**刚刚被释放**的同一个端口并先绑上 ⇒ 先绑者赢、后绑者被 `_port_guard` 拒启。
- **决定**：把这段竞态收进测试侧的唯一出口 `tests/subproc_util.py::spawn_bound_port()` —— 在探测到的端口上起真服务进程，**成功判据 = 这个子进程自报 `listening on <host>:<port>`**（不是「端口上有人监听」：那可能是别人的服务，会让我们对着陌生 hub 跑完整用例）；撞端口的子进程带着 `PORT_TAKEN_MARKER` 退出 ⇒ 换端口重试（默认 5 次）并 **print 一行**（重试发生了要看得见，否则偶发红又会变成谜）。
- **备选与否决**：① 保持原样、给这条 flaky 用例加重试插件——否（重试会把真 bug 一起吞掉，而这里的真因是**可消除**的）；② 靠 `SO_REUSEADDR` 让后绑者也能绑上——否（`_port_guard` 的语义正是「禁止双监听」，放开等于把 2026-09-09 双实例事故请回来）；③ 用「端口有人监听」当成功判据 + 就绪短等——否（会静默地把陌生进程当成被测服务）；④ 让每个 xdist worker 在按 `PYTEST_XDIST_WORKER` 偏移的端口段里取号——否（窗口只变小不消失，还给测试引入与 worker 拓扑耦合的端口算术；真撞上时同样无从恢复）；⑤ 改 socket activation（父进程 bind 好再把 fd 交给子进程）——否（被测对象是 CLI 的 `--port`，改传 fd 就不再测控制台真实启动的那条 argv）。
- **违反后果**：起真服务进程的测试再写回裸「探端口 → 起子进程」，xdist 并行下会重新出现**与本用例无关**的假红（真因埋在子进程输出里，属最贵那类）；把成功判据退回「端口上有人监听」，会让用例对着别人的 hub/worker 跑完并且通过。
- **落地**：`tests/subproc_util.py`（`free_port` / `spawn_bound_port` / `BoundServer` / `PORT_TAKEN_MARKER`）· 调用点 `e2e/test_multi_course_single_hub_e2e.py::_Hub` · `tests/hub/test_multi_course_hub.py::test_main_discover_picks_up_course_from_disk`（两份裸 `_free_port` 与各自的 `_startup_output` 一并删除，诊断改由 `BoundServer.tail()` 出——进程活着也能安全取）· 回归 `tests/test_subproc_util.py`（6 例：真守卫文案对齐 · 撞端口换端口重试 · 非端口死法不重试 · 上限到顶响亮失败 · 超时兜底 + tail 可读 · **源码守卫**：两个调用点不得再出现裸取端口）。
- **仍未做（明确记录）**：① 平台侧的正解仍是让服务支持「`--port 0` ⇒ 自选端口并回报」——hub/worker 现在都要求显式 `--port`，那是产品面改动，不属测试修复；② `nn-training/` 之外的 python 测试面若将来出现同类写法，守卫（扫 `tests/**`、`e2e/**`）需要同步扩大范围。
- **收敛（同日续，见 §2026-09-19-goalnn-test-port-convergence）**：全部剩余取端口点（`test_instance_lock` / `test_worker_server_lock` / `test_port_guard` / `test_push_bootstrap_teardown`）已收编，守卫从 2 个文件扩到全测试面。
## §2026-09-19-goalnn-offline-training-mode（2026-09-19，离线训练模式：启动选在线/离线 + 云端整段执行 + 补传归位）

- **背景**（用户口径 2026-09-19）：「启动课程训练时，需指定 在线/离线 模式，缺省在线。在线模式下，使用 rollout 集群，每个 it 都需向云端 worker 传语料。离线模式下，支持下载任务包（课程配置、代码），上传到云端 worker 后自动跑完课程；也支持带特别标识的云端 worker 在线领取。写一个 `battle.offline.ipynb` 用于在云端执行离线训练任务；ipynb 里设置一个选项，是否实时把训练结果回传到 hub，不实时回传则任务完成后统一打包让用户手动下载后导入；ipynb 执行时先尝试连接 hub，能连通就从 hub 获取离线任务包，不能连通则等待用户手动上传。」
- **决定**："在线/离线"是**启动时的模式选项**（缺省在线），落成**课程级** rl-config 键 + hub 该课的模式：① 模式 → 键的换算只有**一处**（`dashboard/src/stack/specs.ts::trainModeKnobs`）：离线 ⇒ `courses.<课>.{rollout_src:'run', run_iters:-1}`（声明 + 段长），在线 ⇒ 撤掉离线标记（段长必删，课程级 `run` 也删，但**不**顺手清别的覆盖）；② 离线启动同时把该课 hub 模式置 offline（整段 job 只交给**带标** worker）；③ 「能领离线课」= worker **自报能力**（`X-Battle-Offline: 1`，`--offline`），不是课程绑定——带标 worker 仍领在线课；④ 补传的**归位键** = hub 在 `/jobs/next` 里下发的课程键（领活路径）或 `--hub-course`（全离线包路径，由 notebook 的 `CFG.course` 给），空 = 单课程 hub（此时不带这个键）。
- **为什么模式不写全局 `rl.rollout_src`**：那是所有课共用的默认面——用户在弹窗里只选了**这一门课**，落进全局就等于把全部课程一起拖进离线。故离线只写课程级键；`run` 也**绝不**进全局（`preset.ts` 用 `trainModeKnobs` 算出的全局面：算出 `run` ⇒ 置空不写）。
- **为什么在线要"撤标记"而不是只写新值**：`rollout_src:'run'` 与 `run_iters` 是**一对**；只留 `run` 不给段长在训练侧是配置错误（响亮拒跑），只删 `run` 留段长则是半状态。删段长 + 删 `run`（保留别的课程级覆盖，如显式写过的 `node`）才是"切回在线"。
- **为什么能力头不放在 worker 白名单/课程绑定上**：用户口径是「带**特别标识**的云端 worker 在线领取」，而 2026-09-19 R4 刚把「课程与 worker 节点正交」定案——按课程绑定会立刻回退那条。能力声明（"我能自己跑完整段"）也正好是判错方向明确的那种：低估只少一个 worker 领离线课（看得见：队列不降），高估会让只会逐轮的 worker 领走整段 job 并卡在那里（看不见）。
- **补传必须带课程键（本轮修的存量 bug）**：hub 的 `/offline/artifact` 要在多门课里定位这条腿（`locate_offline_course`），而补传体里原来**没有**可用的课程身份——`manifest.course_name` 是课程文件的 `name` 字段（`bc-c4-v3` 的 name 是 `bc-c4-v3-distill`），与 hub 侧的课程键（`<traj>/<课>/` 目录名）不是一回事。后果：**多课程 hub 下每一条补传都被 400「无法归属课程」拒掉**，节点侧补传**整体停用**（体是自己造的，重试不会变对）——训练照常，但控制台上段内进度永远是空的。修法是**生产者带上归位键**（不是 hub 猜）：领活路径由 hub 在 `/jobs/next` 下发（它本来就发了 `course`，只是没人透传下去），全离线包路径由 `--hub-course` 给。
- **否决项**：① 让 hub 在"体里没课程"时猜一门（单课程 fallback）——否，补传落到错课程上那条曲线**看起来完全正常**，只有事后对账才发现，比拒收危险得多；② 用 `manifest.course_name` 当归位键——否，与 hub 的课程键不是一回事（见上）；③ 让离线课也参与竞速广播——否，整段 job 广播 = 让每台带标 worker 各跑一遍完整课程；④ 给控制台另开一个「离线模式」开关与 R3-2 的在线/离线开关并存——否，同一件事两个旋钮必然漂开，R3-2 那个开关保留为运行期微调，启动模式是它的**初始值**。
- **违反后果**：把离线档的 `run` 写进全局 `rl.rollout_src` ⇒ 全部课程静默变离线；补传不带课程键 ⇒ 多课程 hub 下控制台永远看不到段内进度（且只有节点日志里一行 400）；按课程绑定离线 worker ⇒ 回退 R4 的"课程与节点正交"。
- **落地**：python —— `remote/protocol.py`（`OFFLINE_TASK_PACK_PATH` / `OFFLINE_CAP_HEADER` / `OFFLINE_CAP_VALUE` / `has_offline_capability`）· `remote/hub_server.py`（`claim_next(offline_ok=)` + 离线课永不竞速、`/admin/offline`、`/offline/task-pack`、`offline_progress()`、交领日志）· `remote/worker.py`（`poll_job(offline_ok=)` / `worker_loop` / `--offline` / 把 `/jobs/next` 的 `course` 透进 `run_plan_job`）· `remote/offline_deliver.py`（`course` 归位键，逐轮体与段末摘要都带）· `remote/run_loop.py`（`hub_course` 三跳透传 + `--hub-course`）· `remote/offline_boot.py` + `ipynb/battle.offline.ipynb`（取包三分支 / 实时回传开关 / 交付物打包）· `rl/{cli,loop_steps,loop_round,loop_round_steps}.py`（`--rollout-src run` + 段长解析）。控制台 —— 启动弹窗「训练模式 在线/离线」+ 课程级键与 hub 模式一起下发；总览行显示离线段内进度（`/admin/offline`，超 1h 无新产物变醒目）。
- **回归**：nn 侧 `tests/{test_offline_task_pack,test_offline_boot,test_offline_notebook,test_worker_offline_cap,test_offline_deliver,test_multi_course_hub,test_run_segment,test_loop_round}.py` + e2e `e2e/test_offline_training_e2e.py`（真 hub 进程：整段 job 发布 → 普通 worker 领不到 / 带标领得到 → 真 `OfflineDeliverer` 逐轮补传 → `/admin/offline` 读面 → 取包端点 200/404/401/越界）；控制台 `dashboard/tests/train-mode-offline.test.ts`（模式→键换算表 + 落盘 + 入口接线 + 读面解析）+ 总览面板离线段断言。

## §2026-09-19-goalnn-test-port-convergence（2026-09-19，测试侧取端口全量收敛 + 守卫扩到全测试面）

- **背景**：§2026-09-19-goalnn-test-port-contention 只修了两处调用点，仓库里还躺着四份私有的 `_free_port`（`test_instance_lock.py` / `test_worker_server_lock.py` / `test_port_guard.py` / `test_push_bootstrap_teardown.py`）——两处起**真服务进程**（同样暴露在 xdist 竞争下），两处只是**进程内**探端口。
- **决定**：测试里取端口只允许**三个出口**，且全在 `tests/subproc_util.py`：① `free_port()` —— 进程内用（bind 紧随探测，窗口微秒级）；② `spawn_bound_port()` —— **单个**服务子进程（成功判据 = 它自己自报监听）；③ `retry_on_port_stolen(scenario)` —— **多个**进程抢同一端口的场景（`scenario(port)` 抛 `PortStolenError` = 场景作废，换端口重跑，其余异常原样上抛）。守卫从「2 个文件」扩到**全部测试文件**。
- **为什么需要第三个出口**：「三启同时启动 ⇒ 恰好一个成为实例」这类用例必须让 N 个进程抢**同一个**端口，用不了单进程版的 `spawn_bound_port()`；端口若被外人抢走，N 个全灭且输出带端口占用文案 —— 那不是被测行为不对，而是场景作废，应由**换端口重跑**消化，既不该报红、也不该把断言放宽。
- **备选与否决**：① 把这类用例改成串行起一个进程——否（测的正是并发抢锁/抢端口那个窗口）；② 把「0 存活」当环境噪声跳过——否（锁真的失效时会被静默 skip，正是这两条用例存在的理由）；③ 静态分配一个「大概率没人用」的端口段——否（会与本机常驻服务冲突，且仍需探测）；④ 用「无条件重试整条用例」包住——否（重试必须按**真因**触发，否则真 bug 也会被重试掉）。
- **违反后果**：新写的测试再自己 `bind(0) → close → 交给子进程`，xdist 下会重现那条无头假红；把「端口有人答就算成功」引入成功判据，会让用例对着**别人的** hub/worker 跑完并且通过。
- **落地**：`tests/subproc_util.py`（新增 `PortStolenError` / `retry_on_port_stolen`；子进程 stdout 改**显式 `encoding="utf-8", errors="replace"`** —— `text=True` 按控制台代码页解码，zh-CN Windows 撞 gbk 会让读线程静默死掉，正是 2026-09-17 在本仓吃过的那个坑）· 四处调用点收敛（`test_instance_lock` / `test_worker_server_lock` 起真进程的两条各改 `spawn_bound_port`（顺序双启）与 `retry_on_port_stolen`（三启抢端口）；`test_port_guard` / `test_push_bootstrap_teardown` 只换共享 `free_port()`）· 守卫重写为扫全部 `tests/**` + `e2e/**`（**剥注释后**扫，R4 那条教训）：① 不许再有私有 `_free_port`；② 凡 argv 里出现 `"-m", "remote.hub_server" | "remote_worker_serve" | "remote.worker_server"` 的文件必须借端口。回归 `tests/test_subproc_util.py` 共 9 例（新增编码守卫 + 中文 marker 端到端）。
- **仍未做**：平台侧正解仍是「`--port 0` ⇒ 自选端口并回报」（见 §2026-09-19-goalnn-test-port-contention 的「仍未做」①）。
## §2026-09-18-pickup-shaping-metrics-v7（2026-09-18，课程 x5-approach 立项：plan/pickup-shaping.plan.md Phase 0/1 落地）

- **背景**：x5（wPickup 3.0）结课判部分达成 —— pu 0.71→0.69 纹丝不动而 dmg 同种子 −39% ⇒ 拾取特异性不动；意愿半径 cliff（0–4 格拾取率 74%，5 格+ ≈5%）证明「朝道具走」行为维度不存在。本腿 = 唯一训练变量 `−wApproach×pickupDist`（势能法，不改最优策略），让该维度被塑形出来。
- **metrics v7（shard 格式变更 ⇒ golden 重生成，语义变更声明）**：`METRICS_DIM` 39→41、`METRICS_VERSION` 6→7。新列：**idx40 `pickupDist`**（每决策步玩家到最近存活拾取中心格曼哈顿距离；无存活拾取/玩家阵亡 = 哨兵 −1，公式侧 `where(pickupDist<0,0,pickupDist)` 归零 —— 哨兵与 firstKillTick/clearTick 同构）+ **idx39 `puGotOther`**（顺手修 x5⑩「puGot* 全零输出 bug」）。
- **puGotOther 与 x5⑭ 的关系（否决陷阱澄清）**：x5⑭ 把「分列 collected-by-kind」整个判成独立工程不开（doctrine：fence 除外无法低成本实现，接受残差）；本列是**更小**的修法 —— 只补一个**残差桶**（四桶外类型全进它），不做按类型定价（这些列不进任何公式，「不进训练变量」），只恢复输出侧守恒（`powerUpsCollected ≡ stars + 四桶 + puGotOther`，可测）。未否定 ⑭ —— 全分列仍不开。
- **wApproach 剂量冻结 = 0.008（Phase 1，方法按 plan §3 预注册）**：it0 语料（it35-best/40 局/v7）Σwhere0(pickupDist) 均值 753.8、kill+win 均值 12.68 ⇒ 门②上限 0.0084 ⇒ 冻 0.008（留 ~5%）。边际算术 −1.57 dmg/格（趋近 dmg 成本非正 ⇒ 定价不被承伤约束）。telescoped 读数：d0 恒 0 ⇒ 趋近项每局净 ≤0（势能法原理，不改最优策略；来回刷分净零同源）。§5 趋近 hack 熔断（cellsVisited 暴涨而 kills 不升 / zero-kill-frac 翻倍）作护栏。
- **作废声明**：v6 及更早 shard（traj/）**不可续跑**（列序已变，下游按 version 判别会响亮报错）；旧 `-best` 权重当 bc 可用；v6/v7 的 metric 数值**禁直接比较**（终点三选项限于 v7 内）。
- **落地**：`tools/sim/export-rl-rollout.ts`（`nearestPickupDist`/`PICKUP_DIST_SENTINEL`/两新列/`puGotOther` 残差桶，idx40 热路径零分配）、`tools/sim/export-eval-game.ts`（puGotOther 同桶）、`nn-training/rl/reward_library.py`（METRICS 缀尾 + v7）、`reward_validation.py`（范围：puGotOther 0–30、pickupDist −1–50）、golden 重生成（64 cases）、`tests/pickup-dist-metric.test.ts`（codec 独立重实现 + 炸弹局/多拾取局/BONUS 窗口局等价用例 + determinism）、`nn-training/curricula/x5-approach.jsonc`（判据原文搬家，plan 文件退役归档）。回归：`bun run check` 绿 + nn pytest 396 用例绿。

## §2026-09-19-x20-dense-only（2026-09-19，20 敌 1 命总攻腿 x20-rebirth 立项：dense-only 第一例 + lives1 晋升 + 4 出场变体）

- **背景**：c20-3命 83% vs c20-1命 9.9% 撞出 §0.2（1 命任务定义）vs D1/D2（阶梯命数）矛盾，用户裁定执行 A 面（1 命）。1 命下机会成本（没收后面全部未杀，c20 高达 +60）淹没终局常数（wWin +2 / death −5）⇒ 去 wWin、去双 terminal 只留持续项；围歼下苟均衡不存在（待验证，不断言 —— 超时熔断 >5% 即加回重开是预注册反悔键）。
- **备选与否决**：保留双 terminal（称"自杀威慑/苟定价"）—— 否，离线 EV 三档重算去留排序不变（走>莽>坐；1 命真值语料 n=40），威慑主由机会成本承担；缩短 max_ticks 防苟 —— 否，cap 2500 会把 30% 真通关（含教师 13%）变超时（D7 立案的截断正是要删的东西），且当前策略 24 枪/局无苟病；wWin 单留 —— 否，杀光=通关机械恒等，+2 只是 top-up，删了 EV 不变。
- **D11 合规**：命数 tier 变化触发立案（roadmap:37），四项逐项打勾 —— ① 本条目；② fresh `tmp/x20-rebirth/`；③ reward 重验算（§2 剂量复核 + EV 三档）；④ bc 零样本重测（x6best c20-1命对决胜 + it0 三件套）。`ladder-c20` 原 3 命毕业账不混入。
- **作废声明**：3 命探针数全作废（est 478→147、边际 −2.71→+0.90、鸿沟 45pp→0.5pp；根因 `export-rl-rollout` 漏传 lives 静默 3 命，已修 fail-fast + 回归测试）；4 变体 it0（pooled 2.0%/3.63杀）替代单变体旧数（11.5%/7.49杀）。
- **落地**：`nn-training/levels/ladder-c20-lives1.jsonc`（4 轮转变体，成分恒 5/5/5/5；`ladder-c20` 整名 D11 账不动）、`nn-training/curricula/x20-rebirth.jsonc`（kills 里程碑 8/12/16/20 + pass 门 30% + 超时熔断 + 回测门 c07/c06；it40 主检点 kills +50%；it20 只记录不判决）、`tools/sim/export-rl-rollout.ts`（`resolveLivesFlag` 无 flag 即抛错 + 回归测试）。回归：课程 `CourseConfig` 校验通过（terminal 空表合法）。

## §2026-09-19-rollout-pipeline-metric（2026-09-19，边分发边开采 + rollout 耗时口径，用户定义）

- **口径（用户拍板）**：rollout 耗时 = **权重就绪开始分发 → 所有样本采集完毕可交 PPO**。
  `pure_collect_sec` = `last_settle − t_dist_start`（**含**与采集重叠的分发墙钟，端到端）。
  旧口径（2026-08-24：末局结算 − **全部**权重分发完毕）作废——在「先等全节点再开采」下
  把分发墙钟藏进 net/dist_phase，volume 多波时 dashboard 显示的 rollout 与真实采集周期脱节。
- **实现**：`dist_common.post_weights_parallel(..., on_alive=)` 每节点 POST 成功即回调；
  `rl/dispatch.py` 先起 local/reuse 采样线程，need 节点后台 POST 成功立刻 spawn（边分发边开采）。
  `weights_dist_start_at` / `weights_dist_done_at` 作诊断锚点；`dist_phase_sec` 仍为
  ping→权重分发完成（与采集重叠部分不再从 rollout 里抠掉）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-19-rollout-pipeline-metric`

## §2026-09-19-volume-continuous-quota（2026-09-19，用户指令：退役离散补波 → 配额感知连续派发）

- **背景**：x20 分关样本缺口/wave_cap 复盘 —— 全局 est × 等 G0 盖不住 1 命下变体间
  产量方差（通关率波动 → 局均 nSamples 波动）。用户裁定：**完全去掉补波机制**，loop
  实时观测 samples 分布，即将足额不再派、差额大者多派。
- **规则 VOLUME_RULE_V2**（`rl/volume_quota.py`）：分关配额 `ceil(target/n_stages)`；
  每批 `allocate_stage_games`：`collected+inflight*est_s ≥ quota` → 软停；差额按
  `ceil(shortfall/est_s)` 派；`game_cap` 硬顶；`DEFAULT_MAX_BATCHES=12` 安全阀（触顶
  响亮 WARN，非静默短采）。种子 `(rotate_seed,it,stage)` 独立流第 k 局（无 wave_idx）。
- **备选与否决**：保留 G0+补波仅改分关 est —— 否，用户要求去掉波次语义；worker 内
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-19-volume-continuous-quota`

## §2026-09-19-console-node-wallsec（2026-09-19，用户指令：节点统计新增训练机侧平均墙钟）

- **决定**：双字段并列，互不覆盖。`wallSec` = **本 worker 本 attempt** 派发→结算墙钟
  （`t_task_start` 本地量，**勿读** `inflight_ts[task]`——竞速副本会覆盖）；成功结算时
  写入 `dist-agent-meta.jsonl` + summary。dashboard 聚合 `avgWallSec`（≤50 滑动，与
  `avgElapsedSec` 同窗），NodeStats 新列「机侧墙钟」。历史 meta 无 `wallSec` → 显示 `-`。
  含网络/异步轮询/排队，**不是**纯 RTT；慢节点判定仍用服务时长，不改 `isSlowNode`。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-console-node-wallsec`

## §2026-09-19-volume-purecollect-stale-merge（2026-09-19，bugfix：指标表 rollout 时间轮轮累加）

- **决定**：轮初 `self._report = {}`；continuous 收官只采纳本轮
  `adopt_volume_report(combined)`（无 batch 时返回**合法空 shape** `combine_reports([])`，
  绝不返回 `{}` —— 否则 `_log_report`/events 读 `games` KeyError，2026-09-19 同日回归已修：
  combine 跳过空 dict、日志/events 用 `.get`）。历史 jsonl 的
  pure_collect/samples 累加行**作废对照**，请改看同轮 `rollout_sec`/`transitions_collected`。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-19-volume-purecollect-stale-merge`

## §2026-09-19-console-wallsec-undefined-guard（2026-09-19，bugfix：节点统计「机侧墙钟」显示 undefineds）

- **背景**：新列渲染 `${r.avgWallSec}s`；服务端进程未重启或旧 API 缺键时
  `undefined !== null` 为真 → 显示 `undefineds`。
- **备选与否决**：只提醒用户重启 dashboard —— 否，UI 不应把缺字段画成 undefined；
  只改服务端 —— 否，客户端仍可能吃到缓存/旧包。
- **决定**：展示层 `secCell`（仅正有限数 → `Ns`，否则 `-`）；`fetchPool` 将
  `avgWallSec ?? null` 归一；服务端继续显式写字段。
- **违反后果**：任何 `x !== null ? x : '-'` 模板在字段缺席时都会画出 `undefined…`。
- **落地**：`dashboard/src/web/app/panels/NodeStats.tsx`、`api-client.ts`；
  回归 `dashboard/tests/server-pool-history.test.ts`（secCell 语义）。

## §2026-09-19-evalcourse-dist-eval（2026-09-19，eval-course-ckpt 开分布式评估：同节点门 + 同规 stageJson）

- **决定**：eval-course-ckpt 增混合分派路径，**缺省仍是原本地 chunked 路径（行为逐字不变）**。
  节点门与 rollout/m1-eval 同源：`evalSupport ∧ stageJsonSupport ∧ bun major.minor ∧ codeHash`，
  不匹配只 skip（打印原因）不中断；局经 `mode=eval` + `kind=<rollout|none>` + `stageJson`
  （课程自定义关原文）+ `livesOverride`/`playerLevel` 派发，stageJson > 16KB 自动退回纯本地；
  回包 BCV2 manifest 顶层 → `manifestToCourseRow`（缺 Phase 0 键填零，旧节点不崩）。
  `--dist-local`（缺省 = `--workers`）保留本地份额；`--policy nn-goal` 强制本地（GOAL_* 未
  进 agent 协议）。**真相锚**：同 (stage, seed, 权重) 的 dist 行与本地行**逐字节相同**
  （2026-09-19 实测 `diff` 空；§16.6 并行==串行验收）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-evalcourse-dist-eval`

## §2026-09-19-evalboard-phase0-census（2026-09-19，Phase 0 逐敌种画像进 EvalStore schema（中方案 P1））

- **决定**：`rl/eval_local.py::eval_census_fields` 单源（**只认顶层**，缺键 = None
  不伪造），A/B/C/m1 四个写点接线；`ingest.ts` 映射（畸形计数列/非字符串归零或空）；
  `store.ts` 七列进 `EvalGameRow` + `GAMEPLAY_FIELDS` + `REQUIRED_FIELDS`，并新增
  `PHASE0_FIELDS` 作为**旧资产行的明示豁免清单**（豁免由调用方传，不写死在
  `coverageReport` 里）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-evalboard-phase0-census`

## §2026-09-19-evalboard-verdict-batch（2026-09-19，判决批走 B 层：语料注册表 + 多 ckpt 批类型（中方案 P2））

- **决定**：① 语料身份独立成注册表 `dashboard/src/evalboard/corpora.json`
  （`{id, level, seed0, games_per_stage, policy?}`，读/校验/身份派生在 `corpora.ts`，
  坏行**响亮失败**）；② 新批类型 `kind='verdict'`（`trigger='verdict'`、`corpus`、
  `ckpts[]`），**`course/rung_from/ckpt` 置空串**——键空间分离靠 `kind` 判别，不用假 course
  去骗旧读方的键；③ 台账**单写者**不变：`verdict-cli.ts` 只往 `requests.jsonl` 追加
  `kind='verdict'` 请求，物化由 runner/`kick-once` 完成；④ unit = 一个 (ckpt × 关卡)，
  **权重在 unit 上**（批次级无权重 ⇒ 多 ckpt 批成立），每 ckpt 每关跑同一 seed 段；
  ⑤ 展开只有一处（`plan_verdict_units` / `units_for_batch`），训练内派发与一次性 kick
  共用。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-19-evalboard-verdict-batch`

## §2026-09-19-eval-tools-node-upgrade（2026-09-19，一次性评估工具复用训练循环的节点升级守卫 + m1-eval 补节点门）

- **决定**：① 新增 `nn-training/dist_upgrade_cli.py`（stdin JSON spec → 逐节点调
  `dist_common.request_upgrade_guarded`，**单源**：护栏与 dirty 判据仍只在 Python 一处）；
  ② 新增 `tools/lib/node-upgrade.ts`（经 `nn-py-safe.sh` 拉起该 CLI；memo 文件
  `tmp/node-upgrade-memo.json` 跨调用去重，语义同 `_RESTART_SEEN`；**永不抛**——失败以
  `{ok:false,error}` 返回供调用方响亮告警）；③ 新增 `tools/lib/dist-node-gate.ts`（门 +
  聚合 WARN + provenance，两个工具共享；`eval-course-ckpt` 保留同名再导出，既有测试
  导入面不变）；④ 两工具接上：门逐条日志 + 收尾聚合 WARN（可用数/原因/两侧 codeHash）+
  provenance（`node:<id>`/`local` 逐局计数）+ **`--upgrade-nodes` 显式开关**才下发 pull+restart；
  ⑤ m1-eval 补上原本缺失的 codeHash/bun/能力位门（与 rollout/eval 同源）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-19-eval-tools-node-upgrade`

## §2026-09-19-m1-eval-python-dispatch（2026-09-19，m1-eval 分派链回归 Python：TS 只写 spec/读行/打分）

- **决定**：上一轮的「仍留的重复」点名的就是 `m1-eval.ts` 自己的分派链（判门/rescan/尾竞速/权重下发
  ≈300 行）。用户裁定同一口径——**Python 侧已有实战版，别再在 TS 重建**。新增
  `nn-training/eval_m1_once.py`（spec → `rl/batch_eval.BatchEvalRunner` → 逐局行），TS 只做
  「写 spec → 经 nn-py-safe.sh 调 Python → 读回逐局行 → scoreV7/报告/HTML/banner」。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-19-m1-eval-python-dispatch`

## §2026-09-19-x20-snowball（2026-09-19，x20 后继腿：中盘激励，里程碑 bonus 单变量 + god-prefix 否决）

- **背景**：x20 结算（池外 it30 7.5%/6.10杀，无合格终点＋旧能力丢失）＋ 用户假说
  "通关靠捡道具滚雪球"。证实：it30 池外段通关局 1.22 pu/千tick vs 死亡局 0.64、
  早期死亡局 0.20（存活归一化仍 2 倍）；早期死亡局 0.12 个/局；凶手四类全有 ⇒
  开局拾取缺口＋全面中盘续航赤字。线性 wKill 在中盘（5–12杀）是回报洼地。
- **本腿 thesis（单变量）**：`wMS8/12/16 = 6/9/12` edge-trigger bonus（Φ-diff，
  数值已验：7→8 跨越精确 +6.0 一次）。坐 EV 不动（到 8 杀概率 0）⇒ 走>莽>坐不变。
- **证据性否决（不做的理由）**：re-warm——peak it30 落在 it25 降温**之后**，冻结说
  证伪；加 batch——plateau 在噪声带之上清晰可见，batch 非瓶颈；容量——用户指令不动；
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-19-x20-snowball`

## §2026-09-19-nn-decision-instant（2026-09-19，nn 决策时点归一到语料口径：NNInput 末帧决策 + nn 入列分派白名单）

- **决定**：① `m1-eval` 分派白名单加 `nn`（`--weights-dir` 解析出**最新**权重文件后上传，`kind='rollout'`）
  ——上一轮条目「nn 走 --weights-dir 自动发现、无文件可上传 ⇒ 留本机池」的**理由已被自身证伪**（解析出的
  文件就是可上传的权重）。② `src/nn/policy-input.ts`（NNInput）的**决策时点**改为「tick 末决策、下一 tick 生效」。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-nn-decision-instant`

## §2026-09-19-eval-tier-channels（2026-09-19，B/C 层派发改为「每节点独立通道」：无阶段屏障、就绪即派单、失联重探、settled 满即断连）

**触发**：用户 2026-09-19 五条裁定（原话）：①「不要把分发权重和派发 eval 任务划分为串行的不同阶段！！！
一个节点权重分发成功后，立即！马上！right now！给它派发任务！！！不要等慢节点！」②「不要分什么 u0/u1/u2
阶段！！！一直持续不停派发，直到所有 eval 任务完成！」③「已经在正常工作的节点！！！就不要再 ping 它！
不要再给它分发同样的权重，一直派活就好了！」④「竞速后 settled 一满，直接关闭所有节点的连接！！！立即！
马上！right now！」⑤「失联的节点，每 20 秒 ping 一次，ping 通了就立即传权重派任务！」。两次追问收紧了细节：
「从 settled 满到 DONE 为什么还花 5 秒？能省掉吗？」（→ 收工只等写行的赢家）与「你这任务都没分出去呀」阶段
日志停在 13:17 的反面取证。
**实施（B/C 层 `rl/batch_eval.BatchEvalRunner`）**：派发由「ping 全部节点 → 全部节点收权重 → 才开派」
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-eval-tier-channels`

## §2026-09-19-node-fault-taxonomy（2026-09-19，A/B/C 三层共用瞬断判据 + 409 wver-not-cached 自愈：把「节点故障」与「可刷新条件/背压」分开）

**触发**：用户 2026-09-19 审计后指名修 A1+A2+A3（训练侧 rollout/eval 与一次性评估同源的三个洞）。
证据链（`tmp/x20-rebirth/training-loop.log`，08:00–10:36，235 个 rollout 轮 / 20 个 eval 轮）：
- **A1**：09:48:29 `weights[rollout] reuse wver=1a01aa045436… skip POST for ['self','mac','a97','gcs']`
  → 5 条 `HTTP 409 {"error":"wver not cached here"}` → `node a97: 3 consecutive failures —
  circuit-broken for this round`；该轮 `byNode={"self":11,"mac":8}`，**a97 的 7 个槽位整轮闲置**。
  而同一 wver 在 44 秒前（09:47:45）刚在 a97 上 POST 成功（`(purged)`）。
- **A3**：09:48:13 三条 `HTTP 502:`（cloudflared 隧道，非节点问题）同样记 streak → a97 熔断；
  实测分布：rollout 617×503（旧实现唯一豁免项）+ 9×502 + 5×409 + 1×10054；eval 层 153×503 +
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-node-fault-taxonomy`

## §2026-09-19-rollout-midround-recover（2026-09-19，A 层 rollout 中途重探真正跑起来 + 轮内回场：`rescan_nodes` 从「每轮 0 次 pass」到 5s 首探/20s 周期 + 已停派节点回场）

**触发**：用户 2026-09-19 指名修 A4（审计发现）。**证据（两条独立线索都指向 0 次执行）**：
① `tmp/x20-rebirth/training-loop.log` 2.5h / **235 个 rollout 轮**里 `rescan` 日志 **0 行**，而同期
节点排除 130+ 次（`ping failed`：a96 61 / a97 43 / a95 26）；② 机制上必然如此——旧实现
`sleep_sec = min(rescan_sec=120, …)` → `all_settled.wait(sleep_sec)` → 紧接
`if all_settled.is_set(): return`，而该 run **234 轮全部 <120s**（p50 7s，max 115s）⇒ 线程每轮都在
首个 sleep 里被结算事件唤醒并退出；且候选集用 `if nid in spawned_ids: continue` 过滤，**已熔断的
节点永远不是候选**（熔断即整轮出局，窗口 1800s）。
**实施（`rl/queue_local.rescan_nodes` + `rl/dispatch` 调用点）**：
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-rollout-midround-recover`

## §2026-09-19-eval-gate-lanes（2026-09-19，C 层（训练干净评估）的门与收工形态：并行 ping + 逐条留痕 + POST 全败走本地 + 本机槽位先开工 + settled 满即断连）

**触发**：用户 2026-09-19 连续两条指令（「把 C 层收工空等 4–76s/轮榨掉：all_done 即断连 + 只等写行的赢家」、
「把 eval 层的病态分支与串行门一起修」），底稿是同日的 C 层审计（`tmp/x20-rebirth/training-loop.log`，
08:00–10:36 共 20 个 eval 轮）。
**证据（五个缺陷，均为一手日志）**：① **B1 收工空等**：末局结算 → `DONE` 的墙钟 = it10 42s / it15 47s /
it40 32s / it80 76s，而这些行在 `all_done` 置位前就已全部落盘——旧收工是
`t_.join(timeout=max(30, window + task_timeout))`，卡在 HTTP 里的线程要等请求自己结束；② **B2 门串行**：
逐节点 `node_ping(timeout=3s)`，墙钟 = Σ 每台延迟（两台超时即 ~7s），而门每轮重跑一次；③ **B4 静默丢节点**：
`if ping is None: continue` 零日志——节点被丢时既看不出是谁、也看不出为什么（`ping failed` 计数只能靠
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-eval-gate-lanes`

## §2026-09-19-eval-weights-kind（2026-09-19，训练干净评估的权重 kind 独立成 'eval'——不再与训练 rollout 共用节点权重桶）

**触发**：用户 2026-09-19（「给训练评估独立 kind（我定名 eval），顺带一次现场验证」）。来源是同日审计的
**B6** 项：`rl/eval_dispatch.py` 的权重下发与局请求都走 `kind="rollout"`，与训练 rollout 的 churn 共用节点
同一个权重桶。
**根因（节点侧分桶语义，已核实代码）**：节点按 `(kind, sha)` 分桶缓存（`sampler-agent.ts::weightsByKindSha`）：
内存桶**每 kind 上限 64**（`WEIGHT_BUCKETS_PER_KIND`）、落盘文件**每 kind 保留最新 4 份**
（`workdir-cleanup.ts::WEIGHT_FILES_KEEP`，在飞桶引用的文件豁免），而 `/v1/task` **只查内存桶**
（`weightsOf`，不回查磁盘）。⇒ eval 与训练 rollout 同 kind 时，eval 那份与训练每轮的 churn 共用同一组
计数（64 / 4），任一侧轮换都可能把对方挤掉；被挤掉后节点答 409「wver not cached here」，客户端只能靠
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-eval-weights-kind`

## §2026-09-19-node-weights-disk-fallback（2026-09-19，节点侧权重查找在内存桶未命中时回查磁盘——根治「agent 重启后对盘上已有的权重答 409」）

**触发**：用户 2026-09-19「让节点侧的权重查找在内存桶未命中时回查磁盘，根治重启后的 409」。承接
`§2026-09-19-eval-weights-kind` 的已知局限 ①（kind 拆桶只消除**两条腿互相驱逐**，重启仍会 409）。
**根因（已核实代码）**：`/v1/task` 只查内存桶（`sampler-agent.ts::weightsOf` → `weightsByKindSha`）。
agent 一重启，内存桶就空了，而权重文件**仍在 `WORK_DIR`**（boot 收敛只按 kind 删到最新 `KEEP=4` 份）
⇒ 节点对**盘上就有的**权重答 409「wver not cached here」，客户端只能靠 409 自愈重发兜（每节点每次重启
白传一份；在 A1 之前还会把该节点**当故障熔断整轮**）。
**决定（`tools/agent/sampler-agent.ts`）**：
- `weightsOf(kind, wver)`：内存命中 → 原路；未命中 → **磁盘回查** `readWeightsFile`，命中即**回填桶**
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-node-weights-disk-fallback`

## §2026-09-19-node-memory-restart-blindspots（2026-09-19，依赖节点内存态的三处重启盲区：取包丢失 404 判据 / `/v1/update` 假收敛 / 升级去重冷却窗）

**触发**：用户 2026-09-19「系统排查其它依赖节点内存态的地方（结果缓存、inflight 表）是否也有重启后行为
盲区」，随后裁定按 F1 → F3 → F2 修。
**审计（只读，判据 = 进程内可变状态重启后消失，而**是否有人据此做过判断**）**：
节点侧逐个过：`weightsByKindSha`（已覆盖，见 §node-weights-disk-fallback）/ `resultCache` / `inflight` /
`failedTasks`（**F1**）/ 计数器（`gamesDoneTotal/ByIter`、`cacheHits/Evicted`、`rejectedCount`、`activeWorkers`、
`lastError`）→ 只喂 `/v1/status`，面板的贡献度算的是**客户端写的** `dist-agent-meta.jsonl`，live 计数只显示、
无跨轮 delta 运算 ⇒ **无盲区**；`persistPool` 子进程 → 父进程退出即关掉它们的 stdin 管道 ⇒
`export-rl-rollout.ts` 的 `stdin.on('end') → process.exit(0)` 自灭，boot 的 `sweepWorkdir()` 另清孤儿
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-node-memory-restart-blindspots`

## §2026-09-20-dashboard-shell-routing（2026-09-20，训练控制台信息架构重设计 P0：应用外壳 + 路由化详情页；**路由实现 = 单 bundle + 服务端路由 SSR + pushState**）

**背景**：控制台是单列纵向堆叠的 12 个平权面板（无分区/无导航），四类详情（指标/传输/节点统计/日志）
住在全屏模态抽屉里（**没有 URL**：看不到也分享不了「我在看这个视图」），课程选择器（整页主语）被
`flex: 1` 挤在顶栏正中间与状态 chip 抢权重，底部还有一段 299 字的说明文字占了主版面最长的文本块。
全部问题按编号列在 `docs/dashboard-redesign.md` §1.2（C1–C13），目标形态在 §3。
**决定（P0 已落地）**：
1. **外壳**：`Sidebar(216px) + Topbar(56px) + 主内容`（`web/app/shell/{Shell,Sidebar,Topbar}.tsx`）。
   课程选择器 + 触发门禁 + 刷新间隔一并移入**侧栏底部**——课程是整页主语，位置固定且全页唯一；
   顶栏只剩「本页（标题 + 这一页回答什么）」与「全局状态（阶段/在训/节点/连接 + ⟳ + 更新时间）」。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-dashboard-shell-routing`

## §2026-09-20-console-process-course-decoupled（2026-09-20，用户指令：服务进程启动与课程解耦 + 开课/停课独立入口）

**背景（一次实测故障里其实躺着两件事）**：用户点「启动训练」（课程 `x20-steady`）失败，控制台输出：
```
❌ 启动训练中断于 trainingLoop
selfNode: self-node 已在运行 (port 8443)
hubServer: hub-server 已在运行 (port 8787)；已回灌 0 门课的离线/在线意图
失败 x20-steady: 需要合法 course（[]）与 mode（['online', 'offline']）
trainingLoop: 共享 trainer 启动即退出 (PID 2496)
```
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-20-console-process-course-decoupled`

## §2026-09-20-course-enable-marker-and-training-pills（2026-09-20，用户报障 + 用户指令：课程开训必须手动开；在训课程显示为 pill）

**背景（上一条落盘当天就撞上的两个洞）**：
```
启动trainingloop 成功后，界面显示一堆课程正在训练！！！ 课程开训需要用户手动开启！！！
正在训练：c6-chip、remote-smoke、x1-rebirth、…（21 门）
```
- **① 课程表判据错了**：共享 trainer 是**发现式**的（扫 `<traj-root>/*/training_log.jsonl`），tmp/ 下堆着
  几十门历史课的账本 ⇒ 进程一起就把它们**全部**拉进训练；hub 同理（`_course_dir_live` 只看
  `remote-jobs/` 存在且新鲜）⇒ 残留的 pending job 还会被继续派给真 GPU worker（白烧租约）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-course-enable-marker-and-training-pills`

## §2026-09-20-publish-lineage-filter（2026-09-20，用户报障：云机领到 ppo 任务后一直报错）

**症状（用户贴的云机日志）**：worker 领到 job 后逐份失败，刷屏三类错误：
```
job 2e2dabb810bf9299 REJECTED: D14 course_fp 不匹配：job=abbf8045102c… shard=e6c69a46167c… — skip (not retried)
job 991b9e70a3e1efed FAILED: RuntimeError: CUDA error: uncorrectable ECC error（硬件，非本次）
job 8d2ff3ab54e2b72b: 代码已变更：本进程加载 f4e2e4041c95… != job 要求 6224d4b41404… ⇒ 退出码 86 重启
```
**取证（`tmp/<课>/remote-jobs/<job>/payload.tar.xz` 逐 shard 读 manifest）**：`c6-chip` it16 那份
payload 里**混着两个血缘**——550 份里 21 份的 `course_fp` 是 `e6c69a46…`（课程文件编辑前的旧版本），
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-publish-lineage-filter`

## §2026-09-20-hub-dispatch-gate-and-stale-adoption（2026-09-20，同一故障的第二/第三层根因）

**为什么还有两层**：D14 那条修的是「坏包被造出来」；用户贴的日志里还有两个独立症状——
**worker 领到的是 11 门课的陈旧 job**（x20-floor 6.8h、x20-powered 15.2h、c6-chip 157.8h…），
且**盘上今天新加的课程闸对它无效**。
**取证**：
- `netstat` + `tmp/training-start/registry.json`：监听 :8787 的 hub = **PID 20860，今早 08:04 起的进程**
  （新闸是之后落的盘）。`tmp/*/training-enabled.txt` 全盘只有 `x20-steady` 一个（10:38 开课）。
  ⇒ 在跑的 hub 还把 20 门历史课留在自己的课程表里，继续把它们残留的 pending job 派给真 GPU worker。
- 真盘只读探针（新代码）：`discover()` 只登记 `x20-steady` 并派发它那份新 job，20 个历史课目录一个不碰。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-hub-dispatch-gate-and-stale-adoption`

## §2026-09-20-body-transfer-stall-guard（2026-09-20，用户报障：云机 claim 第二个 job 后几分钟无动静、无日志）

**背景**：云机领到 `it2` 的 PPO job（`a4b2e1e2d438c28a`）后卡在 payload 下载——
日志从 `claim ... downloading payload` 到 5 分钟后的 socket 超时**一行都没有**（用户原话：
「几分钟一直没有动静，也没有 log 打出」）。取证：`cloudflared` 日志同期每 5 分钟一条
`lookup region1.v2.argotunnel.com: i/o timeout`（DNS 劣化窗口，隧道只有 1 条 ha-connection），
**小 POST（心跳/轮询）照常、大 body 卡死**；hub 侧 `/payload` 的访问行属于高频静默规则 ⇒
两端日志同时沉默。这不是「网络抖动」一种病，是**两侧都没有停滞判据**：
1. worker `download_payload` 只有一把 `timeout=300` 的**整读**：停滞时静默等满 5 分钟，
   而 socket 超时抛出的 `TimeoutError()` **没有正文**（`_get_with_retry` 只把 `repr(e)` 写进
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-body-transfer-stall-guard`

## §2026-09-20-console-decision-log（2026-09-20，用户指令：控制台的启/停/重启/判死要写日志文件）

**背景**：上一节那条「同窗口 hub 停服」的复盘最后一公里——hub 被**人工**停掉后，盘上的证据
（组件日志 + 账本 + console-state）只能显示「日志断在半分钟前」「账本条目没了」「没有意外退出
标记」，**分不出**「人工停的」与「自己死的」，只能反过来去问操作员。根因不是「没写日志」：
`core/log.ts` 早就是 stdout + 文件双写，但
1. **控制台从没 arm 它**——只有 `launch/cli.ts`（一次性 python 启动）调 `initLog(tag)`；
   `bun run dashboard` 走的是 `server/server.ts::main`，`logFile` 恒为空串；
2. 监督器 / 启动对账 / 请求异常的判决用**裸 `console.*`**，连双写都绕过；
3. **动作结果不落任何盘**——「谁在何时点了停/启/开课」只存在于 UI 回执与终端滚屏。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-console-decision-log`

## §2026-09-20-open-course-lock-identity-and-write-order（2026-09-20，用户报障：开课被「自己人」的锁拒掉）

**背景**：用户点开课，回执是
```
❌ x20-steady 未开课：run_rl 锁被 PID 18364 持有
训练模式 在线：已撤掉离线标记（run/run_iters）
rollout 位置覆盖：courses.x20-steady.rollout_src=local
远端连败降级本机：关（连败即 ABORT）
已写开课标记 training-enabled.txt（训练侧/hub 的「在训」判据）
先停掉在跑的那一份（或删除锁文件）再开课——它与共享 trainer 抢同一批 traj。✕
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-20-open-course-lock-identity-and-write-order`

## §2026-09-20-wire-slow-reroll（2026-09-20，用户报障：云机传输长时间卡在 6–9 KB/s；评审 plan/minimize-payload.plan.md 时的实测）

**背景**：上一节（body 停滞判据）让「卡住」变得可见之后，现场读数暴露出**真正的病**：
| 时刻 | 端点 | 字节 | 耗时 | 速率 |
|------|------|------|------|------|
| 10:41:35→10:43:04 | payload | 2,012,020 | 88.6 s | 22.7 KB/s |
| 11:25:01→11:25:06 | payload | 4,848,536 | 4.9 s | 990 KB/s |
| 12:49:28→12:49:32 | payload | 3,454,884 | 3.9 s | 885 KB/s |
| 12:49:58→12:51:30 | **code** | 1,433,893 | 106 s | **13.5 KB/s** |
| 12:51:30→12:51:42 | **blob（opt_init）** | —（无进度行 ⇒ <5 s） | — | ≥120 KB/s |
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-wire-slow-reroll`

## §2026-09-20-node-health-by-completed-round-contrib（2026-09-20，用户指令：节点 pill 的贡献数取上一轮**完成**值，健康度按「贡献 vs 并发」重定义）

**背景**：节点 pill 行有两处口径都不对：
1. **贡献数取的是进行中那一轮**——对齐基准 `globalMaxIt` = meta 里所有节点成功结算的**最大 it**。
   轮次是**逐局结算**的，进行中那一轮的行一直在变多：先交活的节点数字漂亮、还没轮到的显示 0
   （看着像掉线，而机器完全健康）。
2. **健康度取的是 ping**——1.5s `/v1/ping` 超时 ⇒ 标「慢 / 离线」。ping 只是**可达性**；
   「上一轮到底交没交活」才是节点可用性的直接事实。
**决定**：
* **对齐基准轮 = 最近已完成轮**：完成水位 = 同一训练流目录 `training_log.jsonl` 里最后一个
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-node-health-by-completed-round-contrib`

## §2026-09-20-console-declutter（2026-09-20，用户指令：课程区只列在训课程 / 服务面不显示共享·单例 / 删掉关键指标区）

**背景**：控制台首屏纵向空间被三处「重复或索引式」的内容占掉——课程表里几十门未在训的历史课
把在训的那几行挤出屏幕；服务面每行重复一个「共享/单例」（一族里每行都同一个值）；KPI 条六格是
「索引」型面板（每个数都能在 Hero / 节点行 / 课程表头读到同一个值）。三条一起收。
### ① 课程区只列在训课程
- **判据只有一个出处**：`course-matrix.ts::rowTraining(ov, lq) = ov?.training ?? lq?.training ?? false`
  （与状态列 `matrixStatus` **同一个函数**）。新导出 `isTrainingRow(row)` 供面板筛选——
  「哪几行上屏」与「状态列说什么」漂开就是 bug，两条用例盯着这个双向关系。
- **纯函数层不筛**：`mergeCourseRows` 仍产全集（outer join 不丢课）——计数、悬停点名、页脚
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-console-declutter`

## §2026-09-20-topbar-and-family-labels（2026-09-20，用户指令：顶栏去掉页问题/「在训」、pill 靠左；族标题改「服务」）

**背景**：同一轮减肥的第二批（承接 §2026-09-20-console-declutter）。用户扫顶栏从左到右点出的四处：
「总览 现在能不能跑？这轮跑到哪了？」、pill 组前的「在训」、pill 组的水平位置、组件区族标题。
### ① 页问题不上屏（`tc-top__desc` 删除）
顶栏只留 `<h1>{title}</h1>`；`PAGES[].desc`（"一页一个问题"契约）改为**标题悬停**。
**为何不连数据一起删**：`desc` 是非空契约，`web-view-routes.test.ts` 守着（每页都要能一句话说清
它回答什么）；删渲染 = 视觉噪声没了，删数据 = 把那句「这一页是干什么的」也一并丢了。故保留数据、
换载体（悬停）。若日后确认悬停也多余，正确的删除顺序是「数据 + 契约测试 + 悬停」一起（留下没人读的字段
比删错更贵）。四页**统一**处理（不只总览）：同一个元素只给一页去掉，看起来就是坏了。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-topbar-and-family-labels`

## §2026-09-20-console-naming-and-trend-source — 服务角色名 / 节点行不写字 / 走势数据源档位

**用户指令（同一天追加的一轮）**：① 节点 pill 不显示「缓慢/离线」字样，离线时显示状态描述和判据；
② trainingLoop pill 去掉「dispatch→拉取」描述；③ 顶部趋势图加「全部 / rollout / eval」toggle 切换数据源；
④ 服务显示名换角色名（trainingLoop→管事 / hubServer→门房 / selfNode→采办 / cloudflared→跑腿 /
localWorker→丹徒），hover 提示各服务用途。
### ① 节点行：状态**不写字**（四档一视同仁）
色 + 形（`tc-dot--warn` 菱形 ◆ / `tc-dot--dead` 方 ■）+ 行级 tint（`tc-row--slow`）已经是三重编码，
行里再写一个字只是重复。**行内只剩该行的两个数**（并发 + 最近完成轮贡献），判据一律归悬停
（点 title + 元信息 title）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-console-naming-and-trend-source`

## §2026-09-20-preact-svg-attribute-spelling — 趋势图面积块变黑：Preact 客户端不归一 SVG 属性名

**用户报告**：从「指标」页切回「总览」，趋势线与横轴之间的**面积块显示为黑色**；**硬刷新后恢复**。
### 根因（两层，缺一不成立）
1. **属性名写成了 camelCase**：`TrendChart` 里是 `<stop stopColor=… stopOpacity=…>`、`strokeWidth`、
   `textAnchor`、`fillOpacity` 等 —— 这是 React 习惯。
2. **Preact 两条路径对属性名的处理不一致**（实测读 `node_modules/preact/dist/preact.mjs`）：
   - `preact-render-to-string`（SSR）把 camelCase **归一**成真 SVG 拼法（`stopColor` → `stop-color`）；
   - 客户端 diff 在 SVG 命名空间下只做 `l.replace(/xlink(H|:h)/,'h').replace(/sName$/,'s')`，
     其余 **原样 `setAttribute(l, u)`** ⇒ `stopColor` 是个 SVG 不认识的属性 ⇒ 被忽略 ⇒
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-preact-svg-attribute-spelling`

## §2026-09-20-eval-prefers-dist（2026-09-20，用户指令：评估慢是原罪，走分布式；`--no-dist` 下不为例）

**背景**：x20-steady 终点判决（`eval-course-ckpt.ts`，800 局）为求"确定性"用了
`--no-dist`（本机单跑 2.2局/s，约 6 分钟/800 局）。用户纠正：分布式本就能保证
逐字一致，慢是白付的代价。
**为什么分布式同样逐字一致**：仿真是确定性的（固定步长 + `world.rng`，§2.3）⇒
同一 `(weights, stage, seed)` 在哪台机器跑都是同一行 JSON；分布式 tail-race /
duplicate-settle/drop 只是"同一内容的多个拷贝留哪一份"，不改变数值。it0 判决基线
文档自己就写了"`--no-dist` 跑，与分布式逐字等价"（`plan/x20-floor.plan.md:122`）——
等价是双向的，只敢用单向是多余的保守。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-20-eval-prefers-dist`

## §2026-09-20-boot-wire-guard（2026-09-20，收尾 §2026-09-20-wire-slow-reroll：引导期两个大 body 也在同一次连接抽签里，却跑在 code.zip 之前）

**缺口**：上一节的护栏住在 `remote/worker.py` —— 而引导期有**两个大 body 幂等 GET 跑在
code.zip 之前**（那时 `remote.worker` 不存在，因为 code.zip 正是被下载的那个东西）：hub `/code`
（code.zip；实测最坏 1.43 MB / 106 s / **13.5 KB/s**）与离线盘的 `task-pack`（自己文档里就写着
「几 MB～几十 MB」）。两处都是 `opener.open(req, timeout=…).read()` 整读：**没有进度行、没有停滞
判据、没有墙钟预算、没有重抽** ⇒ 坏签下 106 s 一行日志都不多；几十 MB 的包在 10 KB/s 下就是
85 分钟零输出（这正是 §104「静默」事故的形状）。
**决定**：护栏做成**引导期**的一份，住 `remote/tailscale_boot.py::fetch_guarded`：
分块读 256 KB + 进度行 + 停滞 45 s（`BOOT_IDLE_TIMEOUT_SEC`）+ 调用方给的墙钟预算 +
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-boot-wire-guard`

## §2026-09-21-goalnn-native-features-engine（2026-09-21，落地 `plan/rollout-eval-opt.plan.md` §4：native 内核进入**生产** features 路径）

**背景**：Student `features` 是 rollout/eval 的绝对大头（本机实测：TS 52.4ms / wasm 6.0ms /
native 2.6ms per forward；每局 ~236 次调用 ⇒ 单局 sim 1338ms 里九成）。native 与 wasm
**逐字节一致**（`tests/native-parity.test.ts` 现为门禁，8 次随机输入 pooled+bufA 逐位相等），
但「怎么接进生产」此前没有任何裁决：进程模型没定、跨平台字节一致没保、资产分发没写。
**决定**：
1. **单一咽喉 + 选择链**：`src/nn/conv-wasm.ts::runStudentFeatures` = `native → wasm → TS`，
   `infer.features` 只调它 ⇒ rollout 与 eval **同引擎**（T4 免费达成）。native 失败一律回落，
   调用方永不感知；TS 实际生效时 `noteFeaturesTs()` 记账（防「以为开了加速其实在 TS」）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-21-goalnn-native-features-engine`

## §2026-09-21-goalnn-native-prebuilt-distribution（2026-09-21，落地 `plan/rollout-eval-opt.plan.md` §2.5/T2：native 库改「训练机交叉编译 + 随仓库分发」）

**背景**：原 T2 是「节点上自己 `bun tools/agent/native-build.ts` 编一次」。用户提问（2026-09-21）：
「rollout 节点机器上可能没有 clang，能本机编译出所有平台的 native 库直接给它们使用吗？」——查节点池
（docs/goal-nn-handoff.md §4：self(win) / mac / a95·a96·a97·a98(Android-Termux) / lite / gcs）可见节点是
异质的且**多数没有 clang**，原设计等于 native 臂在远端永远开不起来（只会得到「编译器不可用」一行）。
**决定**：
1. **训练机交叉编译 6 目标入库**：`src/nn/native/prebuilt/<platform>-<arch>/conv_feats_native.{dll,so,dylib}`
   + `manifest.json`（源码 sha + flags + cc 版本 + 每目标产物 sha，共 ~78 KB）。目标 = `win32/linux/darwin ×
   x64/arm64`。分发通道就是既有的 `git pull`（节点升级本来就是同一分支 ff-only）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-21-goalnn-native-prebuilt-distribution`

## §2026-09-21-goalnn-probe-negative-arm（2026-09-21，用户指令：负向臂——人类每局都给结论，直接用）

- **决定**：负向臂**不是新协议**，就是操作员手里已有的 `无解 + 理由` 按钮；聚合改三态，优先级
  **可解 > 无解 > 未知**（通关是构造性证据，优先于「没找到路」的判读），`无解` 由「未知」改为判
  **不可解**，并声明它**可被任一后续通关推翻**（同一 seed 后来者覆盖先前者）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-21-goalnn-probe-negative-arm`

## §2026-09-21-goalnn-probe-multi-course（2026-09-21，用户指令：别把文件名绑死为 x20，以后还有很多关卡要人类探针）

- **决定**：**一门课一个清单文件**。`?probe=<course>` 决定取 `/probe/<course>.json` —— 名字因此是
  **URL 片段**：用 `^[a-z0-9][a-z0-9._-]*$` 白名单 + 保留名 `index` 校验（挡掉 `?probe=../../…` 变任意
  路径 fetch），不合法者在 **fetch 之前**就响亮拒绝。课程表 `public/probe/index.json` 由**扫描
  `public/probe/` 派生**（`flatten-manifest.ts` 重建），永不手工维护：加一门课 = 生成它的清单，索引
  自动跟上。生成器参数化 `--games/--level/--out/--index`，默认值 = 原常量（零参行为逐字节不变）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-21-goalnn-probe-multi-course`

## §2026-09-21-god-bc-attractor-basin（2026-09-21，用户纠正 + 取证：God-BC 是陷阱，不是跳板）

**前科（实证，非观点）**：
1. `nn-training/curricula/x1-rebirth.jsonc` 头文件（2026-09-16 实测，2026-09-17 用户订正）：
   BC 把教师的「先打 power」弄丢（首命中 power 33.3% → 0.5% → 训练后 0.0%）；
   七条腿 per-iter KL 全锁 0.0018–0.0027，策略被 kl_coef=0.2 **钉在 BC 附近** ⇒
   结论原文："从零学是唯一能离开「教师吸引域」的路径"。
2. c 系：God-BC 起步，4 敌关难突破 ⇒ 重开 x 系（用户 2026-09-21 陈述；x1 头文件"纯从零 RL 臂（不蒸馏 God-AI）"即执行）。
3. x 线监管链（已核）：x1(scratch, kickstart OFF) → x2…x6 → x20-rebirth → floor/steady，
   kickstart ref 全是上一腿权重 —— x 线历史上**零 God 掺入**（God 只出现过 `--policy god` 参照臂）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-21-god-bc-attractor-basin`

## §2026-09-22-demo-mix-bc-aux（2026-09-22，demo 混 batch 接线：BC 辅 loss，不是 kickstart）

**否决的备选**：
- kickstart 锚到人类 BC-ref——否：ref 在 held-out 挂零（§120），KL 朝常量坍缩策略锚定 = 投毒。
- demo 内联进 payload（base64，旧 slim-off 口径）——否：3MB 进 manifest 不可接受；blob 内容寻址
  首轮一传后缓存命中，与 opt/ref 同规（post() 内非 slim 带 bank 直接响亮拒绝）。
- rollout 侧掺 demo——否：BC 梯度必须进 PPO update，采样侧掺只会污染 advantage 血缘。
**落点**（单变量纯度：loss 侧加项，corpus 不动）：
- `ppo/engine.ppo_update` 新三参（`demo_bank/demo_bc_coef/demo_per_mb`，缺省全关、数学逐字节不变）；
  每 minibatch 步 np RNG 抽样（ckpt 精确复现）、合法类掩码 CE 与 `train.bc._masked_ce` 同数学。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/tpu-perf.md` §9「决策正文归档」· 锚 `### §2026-09-22-demo-mix-bc-aux`

## §2026-09-22-course-error-isolation-loud（2026-09-22，课程配置错误不得弄崩共享 trainer；控制台响亮报错）

2026-09-22 事故：开课 x20-demo-mix 选「离线（整段上云）」但课程 `iters=0`，run_rl 的
SystemExit（`--run-iters<0 需要课程声明 iters——没有终点就不叫整段`，BaseException）从一步级
执行穿透调度器 `except Exception` 的 RETRY 兜底，把**整个共享 trainer**（一个进程服务所有课程）
弄崩（PID 18748）→ exit-watchdog 判死 → 误向云机下发停机。用户口径：「控制台应响亮报错，
而不是泛泛的意外退出」「课程设置错误，应该是把课程下线，而不是把基础设施弄崩」。
**三层防线（本文落地）**：
- **开课预校验（预防）**：`openCourse` 的零副作用预检区新增「离线模式要求课程声明有限
  `iters>0`」，违规即 `ActionError`（文案与 python 守卫同口径：「没有终点就不叫整段」），
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-22-course-error-isolation-loud`

## §2026-09-22-offline-bundle-autoready（2026-09-22，离线课开课自动出包 + 随时可导 + 行内操作 + 状态列修正）

承接 §2026-09-22-course-error-isolation-loud 的离线链路收口（用户三条指令叠加）：
①「离线课程开课后自动生成任务包，kaggle 配好接离线任务后经 hub 下载执行」；②「开课后
也不应锁定任务包不给导出，应支持随时导出」；③「首页任务包区域去掉、导出/导入放课程行
操作列；iter/本轮/在等什么/队列 列未正确显示离线课程状态，要修正」「总览离线课不应是
「it1 推进中」」。
**落地**：
- **随时导出（python）**：`run_rl.py` 的 `--export-bundle` 分支**不取 per-course 单实例锁**
  （导出=只读快照：打 zip 不推进账本/不落新权重）；`loop_steps._remote_ppo` 的导出分支同步
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-22-offline-bundle-autoready`

## §2026-09-22-goalnn-course-switch-cache-tier（2026-09-22，用户报障：多课程并行切课程要等几秒）

- **决定**：快照**分两层键控** —— **机群级**（节点 / 本机槽位 / push 群 / hub 观测面）单条目全局共用
  （`core/swr-cache.ts`：新鲜直接给、陈旧先给旧值再后台重算、`clear()` 后必须等重算）；**课程级**
  （组件表 / 日志尾 / 阶段 / 账本尾派生）按课程键控；视图态动作（`setCourse` / `getGateHaltMode`）
  **不作废**任何快照缓存（判据 = `route.ts::VIEW_ONLY_ACTIONS`，`invalidatesSnapshot` 门禁钉住）。
  修复后：切课零新增机群级探测、4ms 上屏（`tests/server-api-course-switch.test.ts`）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-22-goalnn-course-switch-cache-tier`

## §2026-09-22-goalnn-transfer-scheduling-pull（2026-09-22，传输∥PPO 的优先级调度面：P1/P1.5/P2 落地）

- **背景**：多课程并行下网络传输耗时 ≈ rollout/PPO，串行 `claim → 下载 → PPO → 回传 → 轮询`
  把 GPU 饿死在传输上。plan `transfer-scheduling.plan.md`（同日两轮评审 R1/R2）给出解法的
  完整语义；本条目只记**已落地部分**里「后来者极容易重新做错」的几条决定与它们的否决面。
- **落地范围（"绿"的判据看这些名字，不看行号）**：`remote/worker.py::acquire_job`
  （= `peek_jobs` → `request_priority` → `claim_job` 取活三件套）、`peek_jobs` / `request_priority`
  / `claim_job` / `job_started` / `job_ready` / `abandon_job` / `job_status` / `start_cancel_watcher`；
  `remote/hub_server.py` 的 `GET /jobs/peek` 与 `POST /jobs/{priority|claim|start|ready|abandon}`、
  `_JobStore.claim_outcome/_claim_locked`、`_claimed/_computing/_ready/_epoch/_backup_authorized`、
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-22-goalnn-transfer-scheduling-pull`

## 2026-09-22-goalnn-bulk-single-channel — bulk 单通道 / 让路预算 / 软持有预取（plan/transfer-scheduling P0+P2）

**背景**：§2026-09-22-goalnn-transfer-scheduling-pull 只换了 pull 线的取活面；本条落地「传输那半场」
（`docs/nn/remote-transport.md` §29）：三条流分层（P0 控制面 / P1 关键 bulk / P2 预取），让 GPU 不再被传输饿死。
**决定（后来者极容易做错，故入册）**
① **bulk 单通道是硬不变量**（`remote/bulk_sched.py::BulkScheduler.slot`）：`post_result` 与任何下载
**不得同时在途**。看起来「两条一起传更快」，实测是链路双侧静默（§104 现场）＋控制环读不回；且 POST 大
body **没有安全 Range**，并发只会互相拖慢。**唯一的槽位入口**，不要在别处再加一个「临时并行」旁路。
② **让路预算有界，且上界要同时看两个**（`BULK_YIELD_BUDGET_SEC=5s`）：worker 侧
`BODY_IDLE_TIMEOUT_SEC=45s`（分块读的空闲判停）与 hub 侧 `SEND_TIMEOUT_SEC=60s`（分片写超时）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### 2026-09-22-goalnn-bulk-single-channel`

## §2026-09-22-goalnn-race-retired-priority-only（2026-09-22，落地 `plan/transfer-scheduling.plan.md` P3：竞速广播**判定**退役，取活只剩「peek → priority → claim」一条路；push 腿同表 + 备份副本）
**supersede §2026-09-17-goalnn-race-broadcast**（以及它在 `dashboard/src/**` 的落地物）。该条目的
**机制**被本条目取代：不再有「最新 job 广播给每个 worker、先回先胜」的判定，也不再需要
`race_decision` / `race_mode` / `--race` / `/admin/race` / `hub_scope`。**保留**的是它当年解决掉的
真实问题（多 worker 无排序地抢同一批 job）的替代解，以及两样与判定无关的机制：
- **`claim(mode="backup")` 机制保留**（R1-1）——删的是**判定**，不是**机制**。备份副本仍无租约、
  不进 `_stale_holders`、不产 `_reclaims`、回传经 `_backup_authorized` 放行（403 单列丢弃）。
- **worker 登记表保留**（R2-2）——`WORKER_SEEN_WINDOW_SEC`（原 `RACE_WORKER_WINDOW_SEC`）窗口与
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-22-goalnn-race-retired-priority-only`

## §2026-09-22-goalnn-async-result-upload（2026-09-22，plan/transfer-scheduling **P2.5**：结果回传异步化 —— 把 `out` 从关键路径上摘下来；阶段账新增 `overlap=` 字段）
**决定**：`post_result` 不再同步阻塞主循环。结果**入队即返回**（`remote/result_upload.py::ResultUploader`，
有界队列 + 专用上传线程），主循环立刻去领下一份 job；`--result-upload {async,sync}` 缺省 `async`。
配套三条硬契约：① **退出前必 drain**（`close()` = drain + join，包住整段主循环的 `try/finally`，
覆盖 `break` / `--once` / 热替换 `SystemExit(86)` / 任何异常）；② **绝不丢**（队列满、入队超时、
上传器已收尾 ⇒ 一律**退回同步**发出）；③ **失败响亮**（重试耗尽/确定性拒绝落带 jid 的 `★` 行 +
收尾汇总计数）。记账侧：`out` 的秒数**照报**，阶段行只多一个 `overlap=` 说明它被重叠掉了 ——
判据从「`in+out+ppo+other ≈ wall`」变成「`in+ppo+other ≈ wall`（关键路径）」。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-22-goalnn-async-result-upload`

## §2026-09-23-goalnn-conv-single-source（2026-09-23，conv-optimize.plan.md §4.2 落地）

卷积内核**单源化**：`src/nn/conv/conv.c` 一份纯 C 编 native 与 wasm32 两目标，差异只有目标条件常量
`CF_PW_PX`（wasm 8 / 其余 16 —— 只决定「哪些像素同处一条向量寄存器」，**不改每元素累加次序**）。
**决定**：单源 + 统一 ABI（native 的单 blob + 4 参 `cf_student_features`）；wasm 产物与 6 个 native 目标
同入 prebuilt manifest ⇒ `--cross` / `--check-prebuilt` 一次抓全「改了 conv.c 漏重编某个目标」。
**被否决**：全局 8px 单一常量（native 白吐 ~5pp）· wasm 也设 16px（寄存器溢写，locals 373→455）·
保留两份实现各自优化（同步链漏编 = 静默回落 TS = 41ms/forward > 帧预算）。
**违反后果**：把 `CF_PW_PX`「简化」成单一常量 ⇒ native 白丢 ~5pp 或 wasm 溢写；任何改动**累加次序**的
「优化」都会让 `native-parity` 红 —— 那是语义变更（新纪元），须走 AGENTS §6.3b 的三件套。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-23-goalnn-conv-single-source`

## §2026-09-23-goalnn-agents-injection-budget（2026-09-23，用户指令：AGENTS.md 加预算门禁）

`AGENTS.md` 的注入预算由 `tools/check-agents-budget.ts` 强制：**字符**数 ≤ 9,200（2026-09-10 实测注入
截断点 ≈9,205），≥9,100 告警，两文件必须仍含 §0–§17。
**决定**：挂 `bun run check` + pre-commit 一个**无条件**跑的小块（并在 pre-commit 里按文件归因：超预算
且 `AGENTS.md` 在暂存清单 ⇒ 拦；只在他人未暂存改动里 ⇒ 告警放行）。
**违反后果**：超预算 ⇒ §5 之后的规则对 agent 不可见（截断口落在 §4，正是原来「规矩被读一半」的根因）
⇒ 规则静默回归「不被遵守」；**调大 `HARD_LIMIT` 等于自欺**——截断点由 harness 决定，不由本仓库决定。
**为什么不能只挂 `bun run check`**：纯文档提交会整跳根套件（`tools/test-silent.ts`），而往 `AGENTS.md`
追加正文正是这类提交 —— 只有在 hook 里才在最该生效的场景生效。
—— 全文（背景 / 阈值推导 / 度量口径 / 归因语义）→ `docs/agents.details.md` §0.1

## §2026-09-23-goalnn-common-primitives-layer（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 提可维护性」）

- **背景**：同名原语各写 2~4 份且**语义已漂移**（`sha256_file` / `bun_version` 各三份、`_log_default` 四份、
  13 处裸 `text=True` 捕获 = §19 那类「响亮错误变哑巴」的温床）。重复处的注释写着「与 X 同口径…故就地保留同款
  助手」——**口径写进注释不算单一实现**。
- **备选与否决**：分散合并（`remote/` 内一份、`rl/` 内一份）——否，跨包重复正是漂移发生处；塞进 `dist_common`
  ——否，那是采样协议模块，不是「纯 stdlib、可单独搬运」的落点；把 `bun_version` 两侧分歧「统一」掉——否
  （那是静默行为变更；改用显式 `require_zero` 形参，两侧口径各留一行文档）。
- **决定**：新增 **stdlib-only 的 `common/` 包**（hashing / proc / fs / text / logutil）作共享原语层；上层只留
  re-export / 薄包装（历史名字与 monkeypatch 接缝全保留）⇒ **零行为变化**（门禁 2230 → 2247 全绿）。层契约：
  只依赖 stdlib、不反向 import 上层、无副作用、无模块级可变状态。**结构性例外**：三个从 GitHub raw 单独拉取的
  引导模块（`tailscale_boot` / `notebook_boot` / `offline_boot`）**禁用本包**——cell 侧在拿到 `code.zip` 之前
  就要 import 它们；其重复是豁免，不是漏网（测试守着）。
- **违反后果**：`common/` 里 import `torch` / `rl.*` ⇒ 云机解开 `code.zip` 当即 ImportError（本机全绿、只有云机炸）；
  重抄一份 `sha256_*` / `bun_version` ⇒ 账本「同字节同哈希」与版本对账重回两份真相；合并引导模块孪生 ⇒ cell
  引导链断在首包之前。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §26「`common/` 共享原语层」

## §2026-09-23-goalnn-layering-common-sink（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 提可维护性」）

- **背景**：`rl/` 有 10 个文件 import `remote/*`，`remote/` 有 8 个文件 import `rl/*` —— 双向包循环，
  当年靠 `rl/queue.py` 一处**函数内延迟 import** 维持「能跑」（延迟 import 把失败推到调用期，启动时看不出环）。
- **备选与否决**：一次把 `remote → rl` 的 8 处全改注入式——否（先拿到**单向可达**的增量并钉住，再逐项改）；
  `remote/protocol.py` 留薄门面——否（多一层空壳 + 「哪份是真的」歧义，本仓先例是 `pid_probe` 式真下沉）；
  门面用 `import *` 省事——否（`game_watch.__all__` 已漏 `PROGRESS_LOG_SEC` / `progress_due` 且正被使用 ⇒ 静默少导出）；
  分层守卫写成 grep/lint 规则——否（需跨文件判断 + 白名单双向对账，且 AST 才看得见函数内延迟 import）。
- **决定**：把**模块级零上层依赖**的 `remote/protocol.py`（92 处 / 65 文件引用）与 `remote/game_watch.py`
  （14 处 / 12 文件）下沉为 `common/` 成员；`remote/` 保留**显式清单**门面（不用 `import *`）。确立分层
  `L0 common/·platform_utils·pid_probe·dist_common·schema → L1 models/·ppo/·data/·train/·rl/·scripts/
  → L2 remote/·根入口`，允许 `L2→L1→L0`、反向禁止，由 `tests/test_layering.py` 断言。`rl/` 保留 4 项
  **过渡白名单**（`remote.bundle` / `hub_client` / `push_client` / `serve_pool` = plan §5.2 第 ④ 步待办）；
  白名单**未使用即红**，防其腐烂成「合法的历史遗留」。
- **违反后果**：`rl/` 编排层外再 `import remote.*` ⇒ 纯逻辑包被迫拖入传输依赖，云机无 bun 首包即断；
  把两模块搬回 `remote/` ⇒ 包循环重现，守卫红。
- **同日修订（2026-09-23，原计划第 ④ 步被否决）**：原定把 `rl → remote.{bundle,hub_client,
  push_client,serve_pool}` 抽成**注入式接口**；实测后否决（`loop_steps` 2328 行且外部用户为零、
  正要被 S4 拆；`bc_loop` 的测试直接 monkeypatch `remote.hub_client._request`，接缝本身是契约）。
  改做两件更小的事：① 把 TS 导出器路径收进 `common/protocol.py`（`ROLLOUT_SCRIPT` / `EVAL_SCRIPT`），
  `serve_pool` 只 re-export ⇒ `rl/eval_local` 回纯逻辑，**编排层 17 → 11 个模块**；② 白名单换成
  **声明式快照 `RL_ORCHESTRATION`** + 两条性质断言（纯逻辑不得 import 编排；`remote` 不得传递
  触及编排 = 环已断）。环的断开由全仓 **SCC 分析**证实（零个跨包环）。门禁 **2255 passed / 3 skipped**。
  另：反向探针揪出判据盲点——`from <pkg> import <mod>` 只给 `remote` 展开、没给 `rl` 展开 ⇒
  两条断言静默失效（全绿但守卫是瞎的）；改为按实存子包统一展开。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §27「断开 rl ↔ remote 包循环」

## §2026-09-23-goalnn-godmodule-loop-transport（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 提可维护性」）

- **背景**：`rl/loop_steps.py` 2328 行里混着两种东西——`TrainingSteps` mixin（1 个 **1715 行**类）与
  **19 个模块级自由函数 + 2 异常类 + 4 常量**（课程/rollout 源解析、transport 选择、hub 推送、节点 failover、
  kickstart 系数、远端可重试异常集合），后者**没有一个是方法**，只是历史上「从 `loop_core.py` 拆出」时
  按大小切、没按职责切。
- **备选与否决**：把 `dist_common` / `_push_*` 改成**参数注入**（一次做完）——否（牵动 20+ 调用点与类方法，
  而下一步「拆那个 1715 行类」还要动同一批函数 = 两个高风险重构叠加）；把两份**同名 DI seam 规范化成一份**
  ——否（等于同时改注入语义 + 搬文件，且类方法确实需要自己的注入点 ⇒ 改为**显式记录成契约**）；删掉门面、
  全仓 `import` 改指 `loop_transport`——否（20+ 调用点 + 4 个测试文件 patch 目标要一起改，diff 大而无行为收益）；
  一次拆三个神模块——否（`hub_server` 3972 行还有**模块级可变状态**，风险高；每次只动一件事）。
- **决定**：把 `loop_steps` 的**模块级函数簇（43–611 行）零逻辑改动**搬到新 `rl/loop_transport.py`
  （传输/发布策略的唯一实现），`loop_steps` 只留**显式清单门面** + 列全的 `__all__`（2328 → 1812 行），
  既有 `from rl.loop_steps import X` 调用点零改动。**关键口径**：DI seam 是**模块全局**，patch 目标
  **随实现走**——`_push_job_round`（已搬）读 `rl.loop_transport._push_submit`，而 `TrainingSteps` 的
  方法（未搬，闭包/直读）仍读 `rl.loop_steps._push_submit`；**同名 seam 两份不是重复，是两个各自真实的
  注入点**（由 `tests/trainer/test_loop_transport_split.py` 守住两边并存）。`loop_transport` 登记进
  `RL_ORCHESTRATION` 声明式快照。
- **违反后果**：把传输函数搬回 / 就地再定义一个 ⇒ 守卫红；seam 只留一边却共用函数体 ⇒ 注入**静默失效**
  （绿而无效，e2e 的 patch 成了摆设）；新 `rl/*.py` 偷 import `remote.push_client` 不登记 ⇒ 分层快照红。
- **教训（已写进 §23）**：`import rl.loop_steps as ls; ls._push_submit = …` **不含**字面量
  `rl.loop_steps.`（少了那个点），按「patch 字符串」grep 的清单抓不到——是**全量门禁**点名的。
  ⇒ seam 清点要比文本 grep 多想一层：**模块对象别名也是一个注入点**。
- **同日第二步（2026-09-23）**：继续拆该文件里那个 **1715 行 / 33 方法 / 67 个声明实例属性**的
  `TrainingSteps`——把**远端 PPO 腿** 13 方法 / **862 行（整类的 50%）**搬到 `rl/loop_remote.py`
  的 `TrainingRemote`（发布 → 领取 → 三重校验落位 → failover → 事件落账，覆盖 `kind=run` 半离线段
  与 `kind=iter` 整轮上云）。**方向修正**（与最初设计不同）：不改成「给组合类加基类」，而是
  **`class TrainingSteps(TrainingRemote)`**——簇的唯一入口 `_remote_ppo` 正是**被 TrainingSteps
  其余方法调用**的，所以「调用者依赖被调用者」就是这个方向；收益是组合类不变 · 零 MRO 变化 ·
  4 个「继承真混入」的测试宿主一行不改。切法依据（均是量出来的）：外界→簇只有 `self._remote_ppo`
  一条入口 · 簇→外界只 5 个小助手 · `log` 被 22 方法共读（⇒ 不能按「谁用 log 谁搬」切）。
  mypy 两个坑都是「混入状态契约必须逐文件可见」：5 个跨混入助手要声明（按 `_ledger_apply` 先例用
  `Any`）；7 个从未声明、只在方法体自赋值的属性也要补声明（其中 `_ts_code_zip_path` 是**非簇**方法
  赋值、**簇**方法读）；同一判据筛掉 `_evalboard_idle`（它是方法不是属性）。
  **seam 从两份收敛到一份**：`_push_submit` / `_push_wait_result` 搬走后 `loop_steps` 连 import 都
  没有（ruff F401 证实）⇒ e2e 两处 patch 目标迁 `rl.loop_remote.*`（否则 `AttributeError`）。
  `loop_steps.py` 1812 → **952** 行（含首簇共 2328 → 952）；`loop_remote.py` 984 行。门禁
  **2255 → 2262 → 2266 passed / 3 skipped**；mypy 364 源文件绿。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §28「神模块拆分：`loop_steps` 的传输/发布簇与远端 PPO 腿」

## §2026-09-23-goalnn-godmodule-hub-admin（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/hub_server.py` 3974 行 —— 三个大状态类（`_JobStore` 1002 / `_HubQueue` 1033 /
  `HubHandler` 1343）占了 **85%**。原计划想先撇 5 个顶层纯函数，实测它们加起来只 **58 行 / 1.5%**
  ⇒ **否决该初判**，改为从 `HubHandler`（49 方法 / 1343 行）里取最安全的一组。
- **备选与否决**：先撇 5 个顶层纯函数——否（收益 1.5%，不单开一轮）；先拆 `HubHandler` 的
  `do_GET` / `do_POST` 派发——否（派发链是路由真相，动它收益低风险高）；先拆两个千行状态类——否
  （拆 = 拆状态，最后做）；**一次拆多个路由组**——否（每次只动一件事）。
- **决定**：把 admin 控制面 **9 方法 / 218 行**（停机恢复 · 课程热切 · 队列与状态 · push-worker 清单 ·
  net-probe）拆成 `remote/hub/admin.py::AdminRoutes`，方向 `class HubHandler(AdminRoutes,
  BaseHTTPRequestHandler)`。三条依据是量出来的：`HubHandler` 只有 3 个类属性（本组近乎无状态）·
  本组只往外调 4 个通用助手 · **本组测试接缝为零**（全仓对 `remote.hub_server` 的 patch 只有
  `SEND_TIMEOUT_SEC`，且不在本组）。两个支撑名字 `NET_PROBE_MAX` / `_deterministic_fill`
  **随迁**（只被本组用、全仓无其它读者）——留下会与「hub_server import admin 拿混入」成双向环；
  随迁后**不需门面**。
- **新坑（比名字成环更险）：混入里的类型声明会遮蔽类型库**。在混入里把 `headers` / `rfile` 声明成
  `Any`，而 `AdminRoutes` 在 MRO 里**早于** `BaseHTTPRequestHandler` ⇒ `Any` 盖掉了类型库的精确类型，
  使组合类里 `self.headers.get(...)` / `self.rfile.read(n)` 的推断拓成 `Any`，进而让 `hub_server` 里
  两个做 `-> str` / `-> bytes | None` 的方法报 `no-any-return`（**症状在组合类，病因在混入**）。
  ⇒ 混入里的这类声明必须**逐字照抄类型库**（`headers: email.message.Message` ·
  `rfile: BufferedIOBase` · `path: str`）。前两刀的 `self.*` 用 `Any` 是安全的，因为那边没有
  「基类已提供同名精确类型」这一层。
- **违反后果**：把两个支撑名留在 hub_server 而由新模块 import ⇒ 包级环重现（新模块 ⇄ 组装模块）；
  在混入里用 `Any` 声明基类属性 ⇒ 组合类类型推断退化（本仓当下就有两处 no-any-return 会红）；
  把 `AdminRoutes` 写在 `BaseHTTPRequestHandler` **之后** ⇒ 同上且静默。
- **门禁**：**2274 passed / 3 skipped**；mypy 366 源文件绿。`hub_server.py` 3974 → 3728 行。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §28「第三步」

## §2026-09-23-goalnn-godmodule-wire（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 3450 行 / 68 个顶层函数，是 S4 最后两个神模块之一。它与前三刀
  有本质差别——前三刀拆的是**类**（刀口靠「方法互调」量得出），这里要拆的是一整片顶层函数，
  而顶层函数与**模块级可变状态同生共死**（先铺的安全网 `tests/remote/test_worker_state_contract.py`
  就是为这一步准备的）。
- **备选与否决**：状态**留在宿主**、新模块延迟 import 回来——否（`wire` 读 `worker` 的全局 =
  反向边，而 `worker` 又 import `wire` 拿函数 = 环；且 `_note_rate` 用 `global` **重绑**
  `_BEST_RATE`，跨模块只改自己那份）；只搬**无状态**的几个函数——否（本簇 15 个函数里 6 个读
  状态，拆一半只剩半张账）；连 `_wire_block` 一起搬——否（它是结果 payload 里的 `wire` 子字典，
  **同名不同物**，属 `run_job` 的输出结构）；保留 `__all__` 之外的「隐式 re-export」——否
  （ruff F401 会报未使用导入）。
- **决定**：`WIRE_*` 阈值 7 个 + 状态 3 份（`_WIRE` / `_BEST_RATE` / `_BULK`）+ 15 个自由函数
  （`set_bulk_log` · `_bulk_pace` · `_note_rate` · `_min_rate` · `_reroll_decision` ·
  `WireSlowError` · `_wire_bucket` … `_wire_flush`）整簇搬进新 `remote/wire.py`；**状态随簇搬迁**，
  `remote/wire.py` 是这三份状态的**唯一所有者**，`remote/worker.py` 只做
  `from remote.wire import … as …` 的**显式转发**（自别名的写法是 ruff 认可的 re-export 写法）。
  `_BULK` 同时被 `worker.py` 的传输核心（`_request` / `_get_with_retry` / `post_result`）使用——
  同一实例经转发共享，`_wire_flush` 里的 `sched0` 增量（`s1 - s0`）才不会失真。
- **注入点（这是本刀唯一需要迁移的 seam）**：`_WIRE` / `_BULK` 是**原地可变**（`.clear()` /
  `.reset()`）——转发名指向同一对象，任意入口都一样（`test_wire_report` / `test_bulk_sched` /
  `test_async_result_upload` / `test_control_plane_bypass` **一行不改**）；`_BEST_RATE` 是**重绑式**
  会话标量——`worker._BEST_RATE = 0.0` 只换转发名，`_min_rate` 读不到，注入点**必须**是
  `remote.wire._BEST_RATE`（只改了 `tests/remote/test_wire_reroll.py` 的 autouse fixture）。
- **违反后果**：状态留宿主而延迟 import ⇒ 两份账（`_wire_flush` 只读其中一份，静默的读数损失）；
  新模块各自 `BulkScheduler()` ⇒ `sched0` 快照取自另一个实例、增量永久失真；把 `_BEST_RATE`
  的注入点留在 `worker` ⇒ 测试重置无效而**照旧绿**（`_min_rate` 沿用会话旧值）；`_wire_block`
  误随迁 ⇒ `run_job` 的结果 payload 缺字段。
- **门禁**：**2295 passed / 3 skipped**（2287 → +8：状态守卫 13 → 15 例 + 新 `test_wire_split.py`
  6 例）；mypy 369 源文件绿。`worker.py` 3450 → **3245** 行；`remote/wire.py` 289 行。
  反向探针：`remote/_probe_wire.py::_WIRE` 被状态守卫点名、`worker.py` 里重复实现 `_wire_flush`
  被拆分守卫点名。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §28「第四步」

## §2026-09-23-goalnn-godmodule-http（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：拆 `remote/worker.py` 的第五刀。原计划是拆 BC 簇（`_bc_*` ×7 +
  `normalize_ppo_device` + `resolve_bc_seed` + `_run_bc_job`），但 AST 实测发现：BC 簇虽然
  **零模块级状态**，却整簇站在 HTTP 传输原语（`_request` 系）上，而宿主里另外 18 个函数
  （作业生命周期 / 下载 / 结果回传）也站在同一原语上。
- **备选与否决**：直接拆 BC 簇——否（`bc_job` 要 `_request`、而 `worker` 又要 import `bc_job` ⇒
  环；用函数内延迟 import 只是「把环藏起来」，本仓 `tests/test_layering.py` 头部专门记过这种旧账）；
  只拆无依赖的纯助手（6 个无依赖函数，≈86 行）——否（收益 2.6%，且把一个关注点劈成两个模块）；
  一步拆到底（连下载 / 作业生命周期一起搬）——否（每次只动一件事）。
- **决定**：先把**公共底座**下沉为 `remote/http.py`：`_opener` + `_get_opener` · `BODY_*` 阈值 ·
  `_read_body` · `_POLL_WARN_AT` + `_warn_non_200` · `_request` · `_sched_headers` ·
  `_get_with_retry`（281 行，含 2 处状态）。状态仍**随簇搬迁**（与第四步同规），`worker.py`
  只做显式转发。依赖方向核实为 `http ← wire ← worker`（DAG；`http` 不 import `worker`）。
  搬完 `worker.py` 3290 → **3030** 行，BC / 下载 / 作业生命周期三组从此可选送。
- **seam 分档（本步最有价值的结论，也是差点静默坏掉的地方）**：测试里 patch
  `worker._request` 的有 20+ 处，必须按「调用点解析在哪个命名空间」分两类，**两类都得留**：
  已搬的 `_get_with_retry` / `_read_body` 在 `remote.http` 解析 ⇒ 必须改指 `http`；
  直调 `_request` 的宿主函数（`post_result` / `peek_jobs` / `claim_job` / `heartbeat` /
  `_bc_fetch_resume`）仍在 `worker` 解析 ⇒ **一行不改**。实际只迁了 3 个文件的 7 处。
  `BODY_PROGRESS_MIN_SEC` 同理（`_read_body` 读 `http` 的全局）。
- **违反后果**：先拆业务簇 ⇒ `worker ⇄ bc_job` 环（下一次改动就会破）；seam 只迁一半 ⇒
  「patch 打偏而测试全绿」（下载族尤其隐蔽：它们是**宿主的外形、传输层的里子**——
  `download_payload` 在 `worker` 里、但它走 `_get_with_retry`，所以注入点在 `http`）。
- **门禁**：**2302 passed / 3 skipped**（2295 → +7：新 `tests/remote/test_http_split.py` 8 例，状态守卫
  改三宿主后净 -1）；mypy **371** 源文件绿。反向探针：`remote/_probe_http.py::_POLL_WARN_AT`
  被状态守卫点名、`worker.py` 里重复实现 `_request` 被拆分守卫点名。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §28「第五步」

## §2026-09-23-goalnn-godmodule-jobfs（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 第六步的第一刀。原计划直接拆 BC 簇，但 BC 的 `_run_bc_job` 除
  BC 自身外还依赖两个**非 BC 专属**的宿主名：`d14_corpus_match`（可自 import）与
  `_persist_result`（**`run_job` 也在用**）⇒ 直接搬 BC 会成环。故先把「作业 I/O」下沉。
- **备选与否决**：`_persist_result` 随 BC 一起搬（`worker` 改从 `bc_job` 导入）——否（无环但语义
  不合：结果落盘不是 BC 专属）；给 `_run_bc_job` 加 `persist=` 参数或函数内延迟 import——否
  （改签名会动 e2e；延迟 import 只是「把环藏起来」）；**一次把下载簇 + 工作区一起搬**——否
  （下载簇依赖 `_progress_logger` 且体量更大，每次只动一件事）。
- **决定**：把 `REPO_ROOT` · `JOB_DIR_KEEP` · `_persist_result` · `prune_job_dirs` ·
  `unpack_opt_tar` · `pack_opt_tar` · `unpack_payload_or_fail` · `_git_head` · `_ensure_commit`
  （128 行）搬进新 `remote/job_fs.py`；`worker.py` **3030 → 2915**；依赖 `job_fs ← worker`。
  `REPO_ROOT` 随组搬迁（全仓无其它读者）——它在 `remote/` 下推导出的仍是同一个 nn-training 根。
- **本刀唯一需要盯的静默风险**：`REPO_ROOT` 是**由 `__file__` 推导**的常量，换目录后必须推导出
  同一个值——守卫用「`REPO_ROOT/remote/worker.py` 存在」+「`== ROOT`」两面钉住。
- **副产品（已登记，不在本刀处理）**：`_ensure_commit` **全仓零调用**（只有 `_git_head` 被它自己
  调）——既存死代码；删代码要单开一次并有决策。
- **门禁**：**2308 passed / 3 skipped**（2302 → +6：新 `tests/remote/test_job_fs_split.py` 6 例）；
  mypy **373** 源文件绿。本刀是五刀里唯一 **seam-free** 的（全仓对本组都是直接调用，无
  `setattr`）⇒ `worker` 的显式转发就够，测试一行不改。反向探针：往 `worker.py` 追加
  `def prune_job_dirs` ⇒ 拆分守卫立刻点名。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §28「第六步之一」

## §2026-09-23-goalnn-godmodule-bcjob（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 第六步的第二刀（`plan §5.3.5` 清单里的业务簇）。BC 簇在文件里本来就**连续
  363 行**，且对外只经两个向下依赖：`remote.http._request`（第五步已下沉）与
  `remote.job_fs._persist_result`（第六步之一已下沉）。
- **备选与否决**：先直接搬 BC（前几轮已否决：与 `worker` 成环）；把 BC 拆成「hub 交互 / 本地
  resume / 设备与种子」三个小模块——否（它们是同一条链的代名与助手，拆开只增加导入面）；
  把 `normalize_ppo_device` 留在 `worker`（它是 PPO 语义）——否（它在 `run_job` 里只是**调用**，
  定义搬走不改变调用面；留在 worker 又会让 `_run_bc_job` 反向依赖）。
- **决定**：`_bc_fetch_resume` · `_bc_local_resume_dir` · `_bc_store_local_resume` ·
  `_bc_load_local_resume` · `_bc_post_epoch` · `_bc_device` · `normalize_ppo_device` ·
  `resolve_bc_seed` · `_run_bc_job`（**363 行**）整块搬进新 `remote/bc_job.py`；`worker.py`
  **2915 → 2579**；依赖 `bc_job → {http, job_fs}`（向下，无环）。
  `d14_corpus_match` 两边各自从 `common.protocol` 取（worker 那份是 `run_job` 在用，不随簇走）——
  守卫钉住两边是**同一个函数对象**。
- **本仓一条老规矩第一次被机械钉住**：**顶层零 torch**（hub 侧与协议单测不得拉 torch）。新守卫
  `test_torch_stays_a_deferred_import_inside_run_bc_job` 同时断言「模块级确实没有 torch / train.bc」
  与「`_run_bc_job` 体内确实有」（前者防回归，后者防漏搬）。
- **本刀 seam-free**：全仓对 BC 名字只有直接调用与 `from remote.worker import …`（无 `setattr`）
  ⇒ 显式转发就够；`e2e/test_bc_epoch_e2e.py` 与 `tests/`（取 `normalize_ppo_device` /
  `resolve_bc_seed` / `_bc_device`）一行不改。注意 `tests/remote/test_worker_device.py` 有一条读
  `worker.py` **源码文本**的守卫（`normalize_ppo_device(device)` 计数 + 顺序）——它看的是
  `run_job` 里的调用点（宿主），本刀后仍绿（已跑确认）。
- **门禁**：**2314 passed / 3 skipped**（2308 → +6：新 `tests/remote/test_bc_job_split.py` 6 例）；
  mypy **375** 源文件绿。反向探针：worker 里重复实现 `normalize_ppo_device`、bc_job 顶层
  `import torch`——两处都被点名。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §28「第六步之二」

## §2026-09-23-goalnn-godmodule-download（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 第七刀（第六步的第三刀）。BC 与作业 I/O 相继下沉后，下载簇成为
  最独立的一组：AST 实测它**零跨组函数依赖**（只经 `remote.http` 的 `_request` / `_get_with_retry`
  与 `remote.wire` 的账），且 `_progress_logger` 是它唯一的告警出口。
- **备选与否决**：先拆作业生命周期簇——否（它的 seam 最密：测试大量 patch `worker.post_result` /
  `acquire_job` / `run_job`，需逐点定档；下载簇则只有 2 处组内互调 seam，先做风险低的）；
  把 `_progress_logger` 留在 `worker` 让下载簇 import——否（会构成 `download ⇄ worker` 环，
  正是前几刀反复撞的那个坑）；把 `_progress_logger` 与 `tailscale_boot` 的孪生合并一份——否
  （结构性豁免：tailscale_boot 要能独立拉取，见 plan §5.4）。
- **决定**：`_progress_logger` · `download_payload` / `download_code` / `download_ts_code` /
  `download_blob` · `_cache_blob` / `_resolve_blob` · `_ensure_ts_code`（**252 行**）搬进新
  `remote/download.py`（313 行）；`worker.py` **2579 → 2348**。依赖
  `download → {http, wire, bulk_sched}`（全向下，无环）。`BODY_*` 不复制——从 `remote.http` 取
  单一定义（常量复制是比函数复制更隐蔽的漂移源）。
- **本刀的核心（第一次真正双向验证「注入点分档」）**：前几刀的子模块都是**叶子**（只被宿主调），
  所以「显式转发就够」；本组含**组内互调**（`_resolve_blob` → `download_blob`、
  `_ensure_ts_code` → `download_ts_code`）⇒ 搬走后这两个调用点在 **`remote.download`** 命名空间，
  对它们的 patch 必须改指 `download`（实迁 2 处，`tests/remote/test_remote_ppo.py`）；而宿主
  （`run_job` / `_prefetch_fill`）仍把 `download_*` 当**裸名字**用 ⇒ 解析在 `worker` ⇒
  `tests/remote/test_soft_hold_prefetch.py` / `test_remote_ppo.py` 的缓存命中用例**一行不改**。
- **违反后果**：若宿主改成属性式访问（`download.download_payload(...)`），现有那批 patch 会
  静默失效（测试全绿而注入无效）——守卫 `test_host_callers_still_resolve_the_worker_namespace`
  就是这条警报。若 `_progress_logger` 留宿主而下载簇 import，则成环。
- **附带修正**：`tests/common/test_common_layer.py` 有一条钉「`_progress_logger` 全仓**恰好两份**」的旧守卫
  （它记录的是「tailscale_boot 的孪生是有意的」）——它写死了 `remote/worker.py`，随本刀改为
  `remote/download.py`；新守卫也自包一份同样断言（两处都钉，免得任一侧漂移）。
- **门禁**：**2321 passed / 3 skipped**（2314 → +7：新 `tests/remote/test_download_split.py` 7 例）；
  mypy **377** 源文件绿。反向探针两处均命中：往 `worker.py` 追加 `def download_payload`
  ⇒「定义唯一」被点名；往 `remote/` 再放一份 `_progress_logger` ⇒ 孪生计数被点名。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §28「第七刀」

## §2026-09-23-goalnn-godmodule-joblifecycle（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 第八刀。前七刀（wire / http / job_fs / bc_job / download）把**底座**
  与**叶子簇**拆完后，作业取活 / 生命周期 / 回传面是剩下一块**连续 543 行 / 17 个顶层名**的整块
  代码，且只向下依赖已下沉的 `http` / `wire` / `bulk_sched` / `common.*`。
- **备选与否决**：先拆 `_prefetch_fill` / 预取簇——否（它只有约 130 行，且与 `PrefetchStore`
  和 `worker_loop` 的启动/收尾强耦合，切下去就要动宿主）；把 `worker_tag` 留下（它被
  `worker_loop` 直接调用）——否（它是 `report_job_failure`（已搬）的**被调者**，留下就成环；
  实测它全仓只被「簇内 + 宿主」两处引用，转发即可）；把 `post_result` 留在 `worker`——否
  （簇内 `ResultUploader` 引用它、且它是回传语义的主体，与 `acquire_job` 同生命周期；
  且「引用即接缝」使得留下与搬走的测试效果等同，搬走更干净）。
- **决定**：整块搬进新 `remote/job_lifecycle.py`（682 行）：`peek_jobs` · `request_priority` ·
  `claim_job` · `_priority_rank` · `acquire_job` · `job_started` · `job_ready` · `abandon_job` ·
  `job_status` · `start_cancel_watcher` · `post_result` · `release_job` · `worker_tag` ·
  `_failure_detail` · `job_body_error` · `report_job_failure` · `heartbeat`。`worker.py`
  **2348 → 1805**；依赖 `job_lifecycle → {common.protocol, common.text, http, wire, bulk_sched}`
  （全向下，零 `worker`，守卫钉住）。
- **seam 口径（本仓最密的一组，60+ 处 patch）**：只按「调用点解析在哪个命名空间」分档——宿主
  调用（`worker_loop` / `run_job` / `_prefetch_fill`）仍 patch `remote.worker`；**簇内互调**
  （`acquire_job` → `peek_jobs` / `request_priority` / `claim_job` / `_priority_rank`、
  `start_cancel_watcher` → `job_status`、`report_job_failure` → `worker_tag`）与本簇直调
  `_request` / `_wire_add` / `_bulk_pace` / `_sched_headers` / `_warn_non_200` 全部改指
  `remote.job_lifecycle`（共迁 22 处 seam）。
- **★ 新判据：「引用即接缝」**。「worker 里已无 `post_result` 调用点」**不等于**「patch
  `remote.worker.post_result` 失效」：`worker_loop` 把它当**值**传给
  `ResultUploader(upload=post_result, …)`，读的仍是 `worker` 的模块全局。审计必须用 AST 的
  **`Load`**（任何引用）而不是 `Call`——否则会误判并去改 9 处**本来正确**的 patch。
- **附带修正**：`tests/remote/test_http_split.py::test_worker_forwards_every_moved_name` 对 `_opener`
  做 `is` 恒等断言是错的——`_opener` 是 `http._get_opener` 里 `global` **重绑**的懒建单例，
  `worker._opener` 注定停在 import 时的快照。该断言的红绿取决于**文件顺序**（
  `pytest tests/hub/test_priority_schedule.py tests/remote/test_http_split.py` 红、单跑绿），且在全量
  xdist 下恰好撞不出来。改为「重绑式标量只查名字在」+ 一条与顺序无关的语义断言（重绑只发生
  在 `http`、worker 侧无 `global`）。
- **门禁**：**2331 passed / 3 skipped**（2321 → +10：新 `tests/remote/test_job_lifecycle_split.py` 8 例
  + `test_http_split.py` 新增 2 例）；mypy **380** 源文件绿；根 `bun run check` 2120 pass / 0 fail。
  反向探针四处均命中：`worker.py` 里重复定义 `peek_jobs` / `job_lifecycle` 反向 import `worker` /
  `worker.py` 出现 `_request` 调用点 / `job_lifecycle` 顶层可变容器。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §28「第八刀」

## §2026-09-23-goalnn-remote-dag-ledger（2026-09-23，用户指令「给 remote/ 加一条全局无环守卫，把散在各子模块的『不得反向 import』断言收成一张 DAG 对账」）

- **背景**：S4 八刀把 `remote/worker.py` 拆成 `wire` / `http` / `job_fs` / `bc_job` / `download` /
  `job_lifecycle` 后，「新模块不得反向 import `remote.worker`」在**六个**拆分守卫里各写了一遍
  （其中三个还各带一份 `ALLOWED_IMPORTS` / `PROJECT_ROOTS`）。同一件事写六遍的坏处不是啰嗦而是
  **漂移**——§22 里首版 `test_layering` 就因「只给 `remote` 展开子模块、没给 `rl` 展开」而
  **静默变瞎**（测试全绿而守卫看不见那条边）。
- **备选与否决**：把六份断言合成一个「禁止清单」（`remote.worker` 加进去就完事）——否（清单只能
  指名道姓，新拆一个模块就得记着回来添一行，正是腐烂路径）；拿现成的 import 图工具——否（新依赖
  要单独论证，而这件事 AST 五十行就完了，与 `test_layering.py` 同风格）；把 `remote/` 内部的环
  **当场拆掉**——否（见下，它是两处**有意**的延迟 import，拆它要动 `run_loop` 的 torch-light 策略
  或 `worker.run_job` 的调用方式，属单开的决策，不该混进「加一条守卫」里）。
- **决定**：新增 `tests/helpers/remote_dag.py`（**账本 + 判据实现，只有一处**）与
  `tests/test_remote_dag.py`（整图对账，18 例）；账本两个常量：
  `LAYERS`（remote/ 全部 **35 个生产模块**的**拓扑秩**，8 层）与 `DEFERRED_CYCLES`
  （**唯一**允许的环，仅限延迟 import，必须写明理由）。六个拆分守卫改调
  `assert_remote_module(module, allowed_project_imports=…)`——原「不得 import `remote.worker`」
  被**一般化**为「顶层 intra-remote 边必须严格向下」，另保留「任何 import 不得碰 `rl`」。
- **钉住的实质性质**：顶层边严格向下（顶层环 = 启动即 `ImportError`，**无豁免**）· 延迟边同样
  严格向下 · 全图的环**恰好**等于 `DEFERRED_CYCLES`（多一个红、少一个也红）· **环里不许出现
  顶层边**（即「用延迟 import 掩盖循环」的反面判据）· 账本**双向**覆盖（增模块不给层号红 /
  删了还留着也红）· 三个自包含引导模块顶层零 `remote.*`（以前只是 `remote/__init__.py` 的注释）·
  解析不出的 `remote.*` 目标必须为 0（堵「判据瞎了但全绿」）· 用合成源码自证检测器活性。
- **实测发现（既有事实首次被看见）**：`remote/` 内部**真有一个环**——`remote.run_loop ⇄
  remote.worker`，两边都是**函数内**延迟 import（`run_loop._real_run_job` 要顶层保持 torch-light；
  `worker.run_job` 不把编排入口当宿主的依赖）。顶层图仍是无环 DAG ⇒ **不构成故障**；处置是
  **登记**为唯一被批准的延迟环（带理由）+ 「环里不许有顶层边」警报。
- **违反后果**：若有人把 `run_loop → worker` 或 `worker → run_loop` 提到顶层，`ImportError` 会从
  某条启动路径冒出来，而这条守卫在**提交时**就能拦（反探针实测：分层与「环里不许有顶层边」两处
  同时红）。若新增模块不登记层号，它的所有边**不会**被对账（所以覆盖断言是这一层的必配）。
- **门禁**：**2349 passed / 3 skipped**（2331 → +18）；mypy **382** 源文件绿；根 `bun run check`
  2120 pass / 0 fail。反探针六处全命中：顶层反向 import · 同层延迟边 · 环里出现顶层边 ·
  新模块不登记 · `http` 顶层/延迟碰 `rl` · 摘掉声明环的两条边（stale）。
—— 全文（背景：为什么合并六份断判）→ `docs/nn/engineering.md` §28「收口：`remote/` 内部依赖账本」

## §2026-09-23-goalnn-remote-ring-split（2026-09-23，用户指令「拆掉 remote/ 里唯一那个被登记的延迟环：把 run_loop 的 verify_plan_file / run_plan_job 下沉到 L1，让 worker 直接拿」）

- **背景**：上一条账本收口时实测发现 `remote/` 内部真有**一个**环：`run_loop ⇄ worker`（两边都是
  函数内延迟 import——`run_loop._real_run_job` 为顶层 torch-light，`worker.run_job` 为「不把编排入口
  当宿主依赖」），当时**登记**而非拆（拆它要动 torch-light 策略或调用方式，属单开决策）。本条就是那次
  单开。
- **备选与否决**：① 照用户字面把 `verify_plan_file` / `run_plan_job` 塞进 `remote/job_fs.py`（L1）
  ——否：这两个函数的**传递闭包就是整个执行引擎**（`RunContext` + 单轮 + 主循环 + 云机评估装配 ≈963 行），
  塞进 184 行的作业 I/O 模块 = 造第二个神模块；② 让 `worker.run_job` 从调用方收这两个函数——方向对
  （注入）但对象错：这两个只是引擎的门面，真正要注入的是「**一轮怎么跑**」（`run_job` 自己）；
  ③ 只把 `run_loop → worker` 那条边提到顶层——否：顶层就不 torch-light 了，等于拿启动开销换账本好看。
- **决定**：引擎**整块**下沉到新模块 `remote/plan_run.py`（**L2**），`run_loop.py` 只留 CLI /
  独立续跑 / 门面（1451 → **491** 行）；「一轮怎么跑」由调用方**注入**（`worker` 传 `run_job_fn=run_job`，
  CLI 侧传 `_real_run_job`）。**引擎里那个 `_real_run_job` 兜底删掉**（它正是反向 import 的成因）：
  漏注入时响亮 `RuntimeError`，而不是静默跑错执行器。依赖变成 `worker → plan_run`、`run_loop → plan_run`
  （纯向下）+ 参数注入 ⇒ 环消失。
- **层号为 L2 而非用户口径的 L1（带理由的偏离）**：账本把 `LAYERS` 定义为**拓扑秩**
  （`LAYERS[m] = 1 + max(依赖层)`），而 `plan_run` 顶层依赖 `offline_deliver`(L1)、延迟依赖
  `offline_eval`(L1) ⇒ 它**只能是** L2；标 L1 会与自身依赖同层（分层断言当场红）。本步把这句话加成
  守卫 `test_every_layer_number_equals_its_topological_rank`（对全部 36 个模块逐条验算，今天全绿）
  ——**「沉到 L1」若真要按字面执行，该做的是再把共同依赖往下拉一层**，而不是把标签改小。
- **seam 分档（第一次为「拆环」而非「拆文件」）**：引擎读的模块全局（`iter_spec` / `pairs_for` / `time` /…）
  调用点在 `plan_run` ⇒ patch 目标迁 `remote.plan_run`，且 `run_loop` **不再转发** `iter_spec`
  （打错模块 = `AttributeError`，**响亮**而非静默失效，守卫钉住）；引擎公开名由 `run_loop` 做 `X as X`
  门面转发（取名字可以、patch 无效）。实迁 **1 处** setattr，其余测试/e2e **一行不改**。
- **账本变更**：`DEFERRED_CYCLES` 清空 + 两条硬断言（`undeclared == []` = 全图**零环**，比原来
  「恰好等于声明值」更严；`not dag.DEFERRED_CYCLES` = 要网开一面必须先改守卫）。机制保留：真要再引入环，
  它会归入「已声明」而不是静静绿着。**本仓从此没有「用延迟 import 换环」这条路**——只有下沉与注入。
- **违反后果**：引擎里一旦再出现 `remote.worker` / `remote.run_loop` 的 import，或有人拿掉
  `run_job_fn=run_job`，守卫在**提交时**就红（反探针七处全命中）；`worker.run_job` 漏注入则云机上
  kind=run 尾巴**立即** `RuntimeError`（不是静默少跑几轮）。
- **门禁**：**2349 → 2359 passed / 3 skipped**；mypy **383** 源文件绿；根 `bun run check` 2120 pass / 0 fail。
—— 全文（引擎/入口的职责划分 / 反探针清单 / 选层推导）→ `docs/nn/engineering.md` §28「拆环：半离线执行引擎下沉 `plan_run`」

## §2026-09-24-goalnn-godmodule-traincore（2026-09-24，用户指令「拆 worker.py 的宿主：按一条真实调用链把 run_job / worker_loop 里的簇切出去」）

- **背景**：S4 前八刀 + 账本收口后，`remote/worker.py` 余下 **1805 行全是宿主**（`run_job` 752 /
  `worker_loop` 364 / `main` 127 / `_prefetch_fill` 69）——叶子簇已全搬完，再拆就是拆宿主。
- **备选与否决**：① 按行数对半切——否（会把一条调用链切两半，两半都要读对方的局部量）；② 把
  `run_job` 拆成 5-6 个同文件小函数——否（不降耦合，只把 752 行的直线变成 752 行的跳动）；
  ③ 先拆 `worker_loop` 的「每 job 一轮」——缓（它需要 `job_lifecycle`(L3) 与 `_prefetch_fill`，
  同属下L4，但 seam 很密：`setattr(W, "job_ready"/"abandon_job"/…)` 会被搬进新命名空间 ⇒
  那是单开一刀，本刀先切 seam-free 的那一半）。
- **决定**：按**一条真实调用链**切，共三块（`worker.py` 1814 → **1281**）：
  ① **训练核**（`run_job` 650-1076，427 行）→ 新 `remote/train_core.py::run_training_core`：
     课程上下文/reward_fn → torch+种子 → 模型构建（`opt_init` 优先）→ opt 内容寻址解析 → 多卡 →
     kickstart ref → demo bank → PPO → 产物/`result`；
  ② **进程生命周期**（`HOT_RELOAD_EXIT` / `_request_reload` / `supervise_worker` /
     `_release_cloud_machine`）→ 新 `remote/worker_proc.py`（纯 stdlib，**L0**）；
  ③ `_wire_block`（两个调用方都要）→ `remote/wire.py`（L1）。
- **接口（先量后下刀，AST 清点）**：块内自由变量 = 11 个块外局部 + 9 个 `run_job` 形参；逃出去的
  输出只有 **2** 个（`result` / `course`）；`global`/`nonlocal` **0**；测试 patch 过这一族名字
  **0 个** ⇒ **seam-free**。**唯一语义改动**：核不再拿 payload 字节，只拿 `payload_bytes`
  （“壳测好的事实下传、训练的活下沉”就是这条界）；漏传的检查交给了编译器——ruff `F821` 当场报出
  `len(raw)` 里那个不存在的 `raw`。
- **选层**：核依赖最深到 L3（`download`/`job_lifecycle`）⇒ 拓扑秩只能是 **L4**；`worker` 因此升
  **L5**，其余 6 个下游各升一层（手工重算 + 秩/分层两条断言双向护住）。`worker_proc` = **L0**。
- **违反后果**：壳里再出现训练核的调用点 / 调用点漏传一个关键字 / 核反向 import 宿主 / 核顶层
  拉 torch / `worker_proc` 引入 `remote.*` / `_wire_block` 被抄第二份——六类都会在**提交时**红
  （新守卫 14 例 + 反探针七处全命中）。读源码文本的四个老守卫（`test_job_body_crash` /
  `test_tpu_backend_guard` / `test_worker_device` / `test_priority_schedule`）随本次改指新模块：
  它们「找不到就红」的性质正是本刀需要的那种响亮。
- **门禁**：**2359 → 2374 passed / 3 skipped**；mypy **387** 源文件绿；根 `bun run check` 2120 pass / 0 fail。
—— 全文（接口清点表 / 分层级联 / 源文本守卫迁移表）→ `docs/nn/engineering.md` §28「第九刀：拆宿主」

## §2026-09-24-goalnn-godmodule-jobround（2026-09-24，用户指令「拆 worker_loop 的『每 job 一轮』成 remote/job_round.py（先定档 seam）」）

- **背景**：第九刀后 `worker.py` 余 1281 行，`worker_loop`（364 行）把轮询壳与「一轮」（177 行：
  旁路线程组 + `run_job` + 回传落定 + finally 全收）织在一起——第九刀的**明确遗留项**。
- **备选与否决**：
  1. **只搬块、把 `_prefetch_fill` 留宿主 + 参数注入**（churn 最小：0 处测试改动）——否：它依赖最深到
     L3（`job_lifecycle`/`download`），本来就该在 L4；留在宿主只为「少改测试」= 拿架构换便利。
  2. **把 6 个宿主侧函数全做成注入参数**（seam object）——否：它们是本模块的**向下依赖**，注入后
     本模块离了宿主就无法单独测；只有 `run_job` 因**住 L5**（不能反向 import）才必须注入。
  3. **20 个入参收成一个 `RoundContext` dataclass**——否（本轮）：它们全是「宿主已经定好的事实与
     旋钮」，分组只改调用观感、不改依赖；而分组会把「接口双向一致」的可对账性变成「哪个字段属于
     哪组」的争议。先显式 20 参 + 守卫钉住，若真需要再单开一条决策（本仓 `run_job` 早有 15 参先例）。
  4. **顺手拆 `_JobStore` / `_HubQueue`**——否：拆状态与拆执行是两类工作，混做会让 seam 对账失焦。
- **决定**：`worker_loop` 的「每 job 一轮」（L943-1119）下沉 `remote/job_round.py::run_one_round`；
  配套下沉 `_prefetch_fill`（69 行）· `settle_result`（原 `_result_settled` 闭包）·
  `PREFETCH_WIRE_ID` / `PREFETCH_ROUND_SEC`。`worker.py` **1281 → 1042**；新模块 **418** 行。
- **seam 分档（唯一判据：调用点解析在哪个命名空间）**：`run_job` ⇒ 注入（`run_job_fn=run_job`，
  宿主按**裸名字**读值 ⇒ 那 10 处 `W.run_job` patch 一行不改）；`job_ready` / `abandon_job` /
  `release_job` / `report_job_failure` / `start_cancel_watcher` / `peek_jobs` / `download_payload` /
  `PREFETCH_ROUND_SEC` ⇒ **迁移**（12 + 8 处 setattr、4 个文件 + 2 处 `W._prefetch_fill` 调用）；
  `uploader` 跨 job 存活 ⇒ 宿主建制并传入。`multi`（「有几个 hub」）由宿主算好 `code_cache_dir` 再传。
- **★ 「不留假门面」成为断言**：`_prefetch_fill` / `PREFETCH_*` / `settle_result` 是内部结构而非 e2e
  门面；照惯例留 `X as X` 转发会让 `setattr(W, "PREFETCH_ROUND_SEC", …)` 变成**没人读的变量**
  （测试全绿、注入为零）。故钉成 `not hasattr(worker, …)`，并在宿主与模块文档写明「要拦请 patch
  `remote.job_round.*`」。
- **宿主账目**：搬前块内直接改宿主局部（`done += 1` / `_polls_since_accept = 0` / CodeChangedError
  分支的 `return done`），搬后只能回读 `RoundOutcome{jid, ok, uploaded, stop}`。
- **违反后果**：宿主留下搬走名字的转发名 / 调用点漏传一个关键字 / `run_job_fn` 写成属性式或 lambda /
  反向 import 同层上层（含延迟）/ 顶层冒出可变容器 / 账本层号标错——六类都在**提交时**红
  （新守卫 13 例 + 反探针七处全命中）。
- **门禁**：**2374 → 2387 passed / 3 skipped**；mypy **389** 源文件绿；根 `bun run check` 2120 pass / 0 fail。
—— 全文（分档表 / 注入 vs 迁移判据 / 两个守卫自身的坑）→ `docs/nn/engineering.md` §28「第十刀」

## §2026-09-24-goalnn-hub-routes-split（2026-09-24，用户指令「拆 hub_server 的其余路由组：把 _get_* 12 / _post_* 11 收成通用助手」）

- **背景**：第三步已把 admin 控制面（9 方法）拆成 `hub/admin.py`，定下「路由按域进混入」的先例；
  `hub_server.py` 3728 行里余 25 个路由方法（13 `_get_*` + 12 `_post_*`，本体 644 行）全挂在
  `HubHandler` 上，且四类形状（鉴权+404 / 读小体 / 租约头 / 递文件）各抄了 3–14 遍。
- **备选与否决**：
  1. **按 HTTP 方法（GET / POST）分两组**——否：把 `/jobs/peek` 与 `/jobs/{id}/code` 放进同一模块，
     只因都是 GET；域才是变更的边界（改「租约」不该碰到「字节服务」）。
  2. **一组一个文件共 25 个**——否：过度切分，路由的域就四个，单方法文件只会让 MRO 变成 25 项。
  3. **一步做完「纯搬 + 去重」**——否：混在一起时一旦某条端点变味，分不清是「搬错」还是「收错」。
     先 Phase A 逐字节等价纯搬（只改 `self.` 的解析命名空间），再 Phase B 去重。
  4. **去重时顺手把 admin 的 `self.rfile.read(` 也收进 `_read_raw_body`**——否：`hub/admin.py` 的
     三个读法是「读完就丢」且 try 还裹着 json，形状不同；admin 拆分早于本批助手，不在本刀范围。
  5. **顺手拆 `_JobStore` / `_HubQueue`**——否：拆状态与拆路由是两类工作（同第十刀的理由）。
- **决定**：25 个路由方法按**域**分成四组混入——`hub/schedule.py`（取活/租约/打点）·
  `hub/result.py`（回传与终局）· `hub/blob.py`（字节服务）· `hub/offline.py`（离线段），与 `AdminRoutes`
  并列进 MRO；四组共用的形状收成 5 个助手（`_job_or_404` / `_job_body` / `_lease_token` /
  `_serve_path` / `_read_raw_body`），**实现只住 `hub_server`**（组合类提供、混入以 `Any` 声明并消费）。
  `hub_server.py` **3728 → 3017**。`PRIORITY_BODY_MAX` / `PEEK_MAX` 随迁 `common/protocol.py`。
- **层号是算出来的**：`_post_result` 走 `push_dispatch.accept_result`（推/拉模式必须共用同一个校验
  函数）⇒ `hub.result` 秩 **4**，宿主 `hub_server` 被迫 **4 → 5**，`smoke_loopback` /
  `tunnel_ab_probe` 5 → 6。其余三组只依赖 `common.protocol` ⇒ 与 `hub.admin` 同层 L0。
- **语义保留点（`count` 不出来的）**：`known=True`（`/start` `/fail` `/result` 要「已知 job」；
  `/ready` **故意**用 `known=False`——回传就要开始的那份可能还没落 manifest）；`_job_body` 先闸后体；
  `_serve_path` 的 404 必须带**具体**原因；鉴权仍在**没有 job** 的端点内联（计数被守卫钉住）。
- **违反后果**：路由方法搬回宿主 / 混入没接进 MRO / 助手在混入里又实现一份 / 抄回内联租约头 ·
  裸读体 · 裸 `_job_id()` · 裸递文件 / 内联鉴权计数变化 / 混入上向 import 组装模块 / 账本层号标错 /
  `known` 闸失效 / 先读体再判 job / 404 退回通用文案 / 助手变成死代码——十三类都在**提交时**红
  （新守卫 15 例 + 反探针**十一**处全命中）。
- **门禁**：**2387 → 2402 passed / 3 skipped**；mypy **394** 源文件绿；根 `bun run check` 2120 pass / 0 fail。
—— 全文（分组依据 / 助手对照表 / 真子类替无 socket 实例 / 又一次撞上读源码文本的守卫）→
`docs/nn/engineering.md` §28「第十一刀」

## §2026-09-24-goalnn-worker-landing-trio（2026-09-24，用户指令「清掉 remote/job_fs._ensure_commit 死代码并重构 worker.py 余下的 run_job / worker_loop / main」）

- **背景**：第十刀后 `worker.py` 余 1042 行，三个宿主函数 `run_job`（350）/ `worker_loop`（197）/
  `main`（127）之外全是转发门面。用户指令是「重构这三块」，而侦察给出的答案是：`run_job` 里确实
  还有**一整段可搬**的（69 行物料落地），而 `worker_loop` / `main` 是**宿主本体**。
- **备选与否决**：
  1. **把 `worker_loop` 的轮询壳再切一块出去**——否：它持有的全是**跨 job 存活**的东西
     （`uploader` 队列 / `pf_stores` / `halt_seen` / 轮询计数器）。第十刀已量过：`uploader` 只能
     **传进**一轮、不能搬走（队列跨 job 存在）；再切 = 把一个对象的生命周期交给两个模块管。
  2. **把 `main` 的 argparse 声明拆成 `remote/worker_cli.py`**——否：argparse 声明是**数据**，
     与「解析后怎么起进程」同属入口；本仓先例 `remote/run_loop.py` 同样把 CLI 留在入口模块。
  3. **只搬 payload 落地、code 落地留在 `run_job`**——否：两者失败语义同规（sha 不匹配 ⇒
     `RetryableError`、解包失败 ⇒ `ProtocolError`），与原有 `_ensure_ts_code` 是同一条判据；
     只搬一半 ⇒ 同一条规则住两个模块，下次改一条必漏另一条。
  4. **让 `_ensure_*` 多返几个零散值（`code_root` / `blob_root` 用 tuple 位置）**——否：13 个
     返回值里「哪个是根、哪个是 per-sha 目录」正是搬前那个**同名歧义**（`code_cache_dir` 先当共享根
     后被改成 per-sha）。改用 `NamedTuple`，参数叫 `code_cache_root`，返回值里 `code_root` 与
     `code_cache_dir` 分列。
  5. **顺手把 `download_*` 在 `worker` 上的转发删掉**——否：tests 把 `remote.worker` 当**取名字的
     入口**直接调（`test_wire_reroll` / `test_control_plane_bypass` / `test_remote_ppo` 共 7 处）。
     **名字是契约，位置不是**：留着转发 ≠ 留着注入点（对它们 patch `worker` 已静默失效）。
- **决定**：`run_job` 的物料落地整段（payload 与 code.zip 的「取字节 + 摆好」）下沉
  `remote/download.py`，与原有 `_ensure_ts_code` 并列为**三兄弟**，各返回 `NamedTuple`
  （`PayloadLanded` / `CodeLanded`）；`worker.py` **1039 → 1033**（`run_job` **350 → 300**），
  `download.py` **313 → 519**。层号**不动**（`download` L3 → 新增边 `job_fs` L1，本就向下）。
  另外把 `worker_loop` 里两条分支各写一份的存活日志收成 `_alive_log` + `ALIVE_LOG_SEC`。
- **★ 「不拆」也是决定（写进 `worker.py` 头部）**：三个宿主函数不再往下切——理由不是「拆不动」而是
  它们就是「宿主」这个概念的形状（跨 job 生命周期 / CLI 是数据 / 每分支只做一件事）。下一次动
  `remote/` 的目标应是 `hub_server` 的两个千行状态类（拆 = 拆状态）。
- **语义顺序（碰了就是 bug）**：清场在解包**之前**（解包要面对空目录）、prune 在清场**之后**
  （放末尾 ⇒ 失败轮永远轮转不掉旧目录）。`sys.path.insert` 留落地（落地与「可 import」是一件事），
  热替换护栏 `_ACTIVE_CODE_SHA` 留宿主（那是**进程**的状态）。
- **违反后果**：`run_job` 又冒出 `download_*` 调用点 / 改属性式访问 / 调用点漏传一个形参（13 个）/
  落地不走本模块的 `download_payload` / worker 丢转发名 / 清场调用点消失 / worker 又冒
  `prune_job_dirs` / 落地函数在 worker 又实现一份 / 某相位内联存活日志 / 阈值写死 60s——十一类都在
  **提交时**红（守卫 +4 例，反探针 **11/11** 命中）。
- **门禁**：**2402 → 2406 passed / 3 skipped**；mypy **394** 源文件绿；根 `bun run check` 2120 pass / 0 fail。
—— 全文（三兄弟判据表 / NamedTuple 的理由 / 注入点第三档表）→ `docs/nn/engineering.md` §28「第十三刀」

## §2026-09-24-goalnn-hub-jobstore-mixins（2026-09-24，用户指令「拆 hub_server 的 _JobStore 状态类（先定档接缝与拆法）」）

- **背景**：`remote/hub_server.py` 3017 行里 `_JobStore` 独占 **1002 行 / 49 方法 / 19 个状态字段**，
  而**一把 `_lock` 守着全部**（49 个方法里 30 个取锁，`_*_locked` 后缀标的就是临界区内的那半）。
  前十三刀分别拆了「执行」与「路由」，状态类一直刻意留着（第十/十一刀都把它写进「本轮不做」）。
- **备选与否决**：
  1. **拆成几个各自持锁的协作对象**（`self.leases = LeaseTable(...)` 那种）——否（**本轮的核心否决**）：
     30/49 个方法在同一把 `_lock` 下，「所有状态一把锁」就是**类的不变式**；各持一把锁 = 换并发
     语义，不满足「零行为变化」。而且 3 个 tests 文件直接读 `store._leases` / `._lease_owners` /
     `._stale_holders` / `._claimed` / `._backup_authorized` / `._last_heartbeat` / `halt_workers`
     （20+ 处断言）⇒ 协作对象要让这些**全部改路**。
  2. **按「锁内 / 锁外」拆**——否：锁外那批（`_job_dir` / `_read_ledger` / `get_bc_*`…）彼此没有
     业务粘性，按锁的边界切出来的模块读起来是「一堆巧合同处一室的函数」；域才是变更的边界。
  3. **拆完顺手把 `_HubQueue` 也按同法拆**——否：`_HubQueue` 是**另一个**状态类（多课程调度面，
     1035 行），拆法与验证都要重新定档；混做会让 seam 对账失焦（同第十/十一刀理由）。
  4. **把 `_write_bytes` 留在 `hub_server`**——不可能：它随离线簇走（唯一调用方在那里），而
     `hub/*` 反向 import `hub_server` 会成环。⇒ 搬进 `store_offline.py`。
- **决定**：`_JobStore` 按**域**拆成六个**混入**（`remote/hub/store_{ledger,wire,scheduling,leases,`
  `results,offline}.py`），组合类只留 `__init__` / `note_worker` 与**进程级状态**
  （`halt_workers` / `_workers`——多课程单 hub 下 `_HubQueue` 借的就是这一份）。
  `hub_server.py` **3017 → 2072**（−31%）；新六块共 1273 行；47 个方法与本体 895 行搬走。
- **★ 拆法判据（三条实测）**：① 一把锁是不变式（30/49）；② 跨域互调 37/49（`_claim_locked` →
  `_job_priority_locked` / `_collect_expired_locked` → `_drop_commitment_locked` → `_bump_epoch_locked`）
  ⇒ 混入留在 `self.X` 上 = **零 seam**；③ tests 直读私有属性 ⇒ 协作对象会全改路。**混入 = 同一个
  对象、同一把锁、零行为变化**。
- **状态声明分散的对价**：四个有状态的域各有一个 `_init_<域>(self)`（`store_results` /
  `store_offline` 真无常驻状态 ⇒ **不造 `pass` 空钩**，守卫改成正面断言「其方法体里零
  `self.X = …` 赋值」）；组合类 `__init__` **逐个显式调用**钩子（不用 `super()` 链：顺序要读得出来）。
- **顺手清掉的既存陷阱**：旧 `_JobStore.__init__` 先 `self._lock = Lock()`、末尾又调
  `_AuthGuard.__init__`（它也建锁）⇒ 前一把被丢弃。删掉前者，守卫钉住「组合类不得再自建锁」。
- **随簇搬走的两个对外名字**：`ClaimOutcome` / `FREEZE_AFTER_RECLAIMS` → `store_leases`，由
  `hub_server` 反过来 import（`FREEZE_AFTER_RECLAIMS as FREEZE_AFTER_RECLAIMS` 保住测试的取名字入口）；
  `_write_bytes` → `store_offline`（唯一调用方）。
- **纯搬对账**：AST 逐成员比对 HEAD vs 新家 ⇒ **58 个成员逐字节等价、零差异**；旧 `__init__` 的
  21 条赋值 + 43 行字段注释全部逐字在新家。**规模到这个量级时「测试绿」不是搬运等价性的尺子**。
- **违反后果**：任何域又实现一份兄弟方法 / 组合类又定义被搬走的方法 / 混入没接进 MRO / `__init__` 漏调
  钩子 / 混入 import 兄弟混入 / 无钩子的域冒出常驻状态 / 裸注解带值 / 组合类又自建锁 / `hub_server`
  又留 `_write_bytes` / 层号标错 / 状态归属窜门——十一类都在**提交时**红
  （新守卫 17 例 + 反探针 **11/11** 命中）。
- **门禁**：**2406 → 2423 passed / 3 skipped**；mypy **394 → 401** 源文件绿；根 `bun run check`
  2120 pass / 0 fail；`check-decisions` 通过。
—— 全文（拆法三判据 / 显式钩子 vs super 链 / 声明怎么写 / 逐字节对账怎么做）→
`docs/nn/engineering.md` §28「第十四刀」

## §2026-09-24-goalnn-hub-hubqueue-mixins（2026-09-24，用户指令「拆 _HubQueue 多课程调度面（先侦察那 28 个同名委托方法）」）

拆 `_HubQueue`（1033 行 / 76 方法）——**先侦察那 28 个同名委托方法**，侦察结论改变了整刀的切法。

- **先决条件（A 相）**：第七个混入要按课程构造/注解 `_JobStore`，而 `remote/hub/*` 不得 import
  `hub_server`（成环）⇒ 先把 `_AuthGuard` + `_is_loopback` 搬到 `remote/hub/auth.py`、把 `_JobStore`
  组合类搬到 `remote/hub/store.py`。`hub_server` **自别名 re-export** 保住 `hs._JobStore` /
  `hs._AuthGuard` / `patch remote.hub_server.*` 这些既有入口（名字是契约，位置不是）。
- **★ 那 28 个同名方法是门面，不是可删的重复**（三条判据）：① 签名分三档 —— 28 条逐参数一致、
  3 条多一个**前置 `course`**（store 每课程一份、队列要跨课程寻址）、1 条**改名**（`abandon` →
  `abandon_job`）；② 缺归属时返回值**逐方法不同**（`False` / `None` / `{}` / `[]` / `0`）⇒
  `__getattr__` 收不掉（得把 31 行「空值表」藏进字符串）；③ 它是类 docstring 写下的对外承诺，
  `__getattr__` 会让 mypy 看不见、IDE 跳不过去。⇒ 三张写死闭集表 + **可执行断言**，不是承诺。
- **★ 另一个结构决定：`queue_peer.QueuePeer` = 只有声明的柱子**（74 个 `def X(...) -> T: ...`，零实现）。
  七个混入必须互不 import（本刀要消灭耦合），但各自要调兄弟方法 ⇒ mypy 一片 `attr-defined`。
  「各造一份 Protocol」= 七份会漂；「import 兄弟」= 违反本刀目标；**共同声明面**两者皆免：
  运行时由后续混入的活动实现覆盖，mypy 看声明，守卫看真身。`_store_of` **刻意不进**它
  （那要 import `hub.store` ⇒ `queue_peer` L3 ⇒ 混入 ≥L4 ⇒ `hub.queue` L5 ⇒ `hub_server` L6 =
  与 `smoke_loopback`(L6) 同层），守卫正面断言它不在里面并写明这条推论链。
- **拆法判据与第十四刀同源**（一把 `_lock` 是类的不变式、跨域互调是常态、tests 直读私有状态）
  ⇒ 仍然是**混入**（同一个对象、同一把锁、零行为变化），**测试一行没改**。
- **★ 与第十四刀刻意相反的一条：`__init__` 不拆钩子**。`_JobStore` 那时拆成四个 `_init_*`（状态散在
  900 行、四域独立）；这里只有 44 行且几处**咬合**（`_solo` 决定 `_now`、`_discover_root` 决定
  `_discover_last` 初值、`_adopt_solo` 运行期把三个字段从 store 搬到 `self`）⇒ 拆开只会把直线扯成跳转。
  守卫改为正面断言「组合类体只有那两个类常量 + `__init__` 每条状态声明都带值 + 申报字段集恰好是状态表的键集」。
- **★ 门面的两面对账（签名 + 活性）**：签名一致抓不住「方法还在、活不干了」。活性判据**不看名字看结构**：
  方法体里**恰好一处**「拿到 store 的取用」且转发目标名等于声明值。取用**四种写法都认**
  （`_store_of(job_id)` / `_stores[course]` / `_stores.get(course)` / `_solo`）—— 这是跑出来的不是先验的：
  第一版只认前两种，`note_worker` 与 `claimable_job_ids` 立刻顶出来。`note_worker` 因此被认定为
  **唯一一条非转发的同名方法**（队列自己就是登记表的拥有者），写成独占例外表 `FACADE_OWN` + 一条反面断言。
- **纯搬对账**：AST 逐成员比对 ⇒ **76/76 逐字节等价**；旧 `__init__` 的 16 条字段声明 + 43 行字段注释
  逐字在新家；七混入**零重名**。
- **违反后果**：域成员换家 / MRO 换序 / 门面签名漂 / 门面转错名字 / 门面多取一次 store /
  `note_worker` 变转发 / 混入带值类常量 / `__init__` 冒出未登记字段 / 状态写者漂 / 声明面长出实现 /
  声明面收 `_store_of` / 混入偷 import 兄弟 / 层号漂 / 改名转发漂 —— 十四类都在**提交时**红
  （新守卫 25 例 + 反探针 **14/14** 命中）。
- **门禁**：**2423 → 2449 passed / 3 skipped**；mypy **401 → 413** 源文件绿；根 `bun run check`
  2120 pass / 0 fail；`check-decisions` 通过。`hub_server.py` 累计 **3017 → 887 行（−71%）**。
- **⚠ 操作教训**：反探针的**锚点也要断言命中次数**——⑨ 选 `set_halt` 写 `_halts` 时同方法后面还有
  `_halts.clear()`（也是写者），⑭ 的 `abandon` 锚点少写了第二个实参：两次「没红」都不是守卫空档
  而是锚点写错。脚本现每条 `assert count(old) == 1`。
—— 全文（八模块表 / 三档签名 / 声明面推论链 / `__init__` 不拆的两条理由 / 逐字节对账）→
`docs/nn/engineering.md` §28「第十五刀」

## §2026-09-24-goalnn-hub-entry-split（2026-09-24，用户指令「拆 hub_server 剩下的引导链与 HTTP 面，收口 S4」）

收口 `remote/hub_server.py`：**887 → 100 行**（累计 3017 → 100，**−97%**），且里面只剩 20 行代码。

- **三件东西，两个新家**：`hub/http_face.py`（**L5**，575 行：来源判定 + `HubHandler` + 通用助手）·
  `hub/boot.py`（**L6**，310 行：`as_hub` / `make_server` / `main` + `DISCOVER_SCAN_SEC`）·
  `hub_server.py`（**L7**，100 行：入口 + 17 条自别名 re-export）。理由：HTTP 面对**每个请求**负责、
  引导链对**一次进程启动**负责 —— 读者/生命周期/失败模式（请求级 500 vs 启动即 exit(1)）都不同。
- **层号先算后切**：`LAYERS` 是拓扑秩 ⇒ 在中间插一层会顺反向边涨上去。`audit_s16b.py` 先模拟、
  切完实测复核 ⇒ **级联只有 3 个模块**：`hub_server` 5→7 · `smoke_loopback` / `tunnel_ab_probe` 6→8。
  顺带修掉账本顶部把 `hub_server` 列在「L4 组装」的**过时散文**（数字有守卫管，散文没人管）。
- **★ 薄入口的 `__all__` 就是契约**：`hub_server` 现在**零定义**（无 `def`/`class`，唯一赋值是
  `__all__`）。守卫正面断言这条 —— 它挡的是「顺手在入口补个小函数」，而层号看不出「只长了一个小函数」。
  配套：`hs.X is 新家.X` 逐条对象恒等 · `__all__` 恰好 17 名闭集 · 入口不得挂实现用 import。
- **★ 踩到的真坑：`SEND_TIMEOUT_SEC` 的 patch 变成静默空操作**。它唯一的读者是
  `HubHandler._bytes`，读的是**所在模块的全局**；搬走后入口上那个只是同一个对象的 re-export ⇒
  `setattr("remote.hub_server.SEND_TIMEOUT_SEC", …)` 名字还在、没人读它（第十三刀「名字 ≠ 注入点」
  第二次现身，由 `test_body_transfer_guard` 真跑时当场报出）。守卫两条机械化：`_bytes` 里必须是**裸
  `Name`** + 全仓**恰好一处** patch 且写在**实现所在模块**上（AST 判据）。
- **⚠ 搬走代码会静默废掉四条读源码的守卫**（本仓第三/四/五次撞上）：`_class_methods(HUB_SERVER,
  "HubHandler")` → `StopIteration`（改读 http_face）；`test_hub_admin_split` 的「名字不在 hub_server 里」
  变成恒真空话（改成「不在入口也不在 http_face」）；`test_jobs_next_retired::_PROD_FILES` 扫一个只剩
  re-export 的空壳 ⇒ 有人把退役端点加回路由表**不会被发现**（把 http_face 加进扫描集）。
  ⇒ **搬文件时必须一条条问「谁会按路径读它」。**
- **★ 顺手修掉一个跨项目静默回归（第十四刀留下、HEAD 上已经红）**：dashboard 的镜像常量守卫
  `poison-unfreeze.test.ts` 按写死路径读 `hub_server.py` 找 `FREEZE_AFTER_RECLAIMS`，第十四刀把那常量搬到
  `hub/store_leases.py` ⇒ 该用例当场失败，**但没有任何门禁会发现**（nn 侧 pre-commit 不跑 dashboard 测试；
  dashboard 侧只在动过 `dashboard/**` 才跑）。修法不是换一个写死路径，而是**在 python 源码树里搜定义**
  （搬家不再红，常量真消失才红）。同一类盲区：dashboard 监督器哨兵只盯入口那一个文件 ⇒ **改
  `hub/http_face.py` 不会触发重启**（第十一刀起就这样，`hub/schedule.py` 等一直不在哨兵里）；修成
  `hubImplementationFiles()` 枚举 `remote/hub/*.py`，并加 dashboard 守卫钉住「哨兵覆盖全部实现文件」。
- **纯搬对账**：AST 逐成员 ⇒ **11/11 逐字节等价**（含 411 行 `HubHandler` + 202 行 `main`）；
  旧 body 零残余；docstring 59 行只改首行。
- **违反后果**：实现搬回入口 / 入口长小函数 / 门面少一条 / 门面被同名副本顶替 / 入口挂回实现 import /
  `_bytes` 改用属性读 / patch 写回入口 / 入口层号标错 / 引导链反向 import / `__main__` 调错东西 /
  组装链断 / 入口自己起服务 —— 十二类都在**提交时**红（新守卫 13 例 + 反探针 **12/12** 命中）。
- **门禁**：nn **2449 → 2462 passed / 3 skipped**；mypy **413 → 415**；根 `bun run check` 2120 pass /
  0 fail；dashboard `bun run test` **1105 pass / 0 fail** + `typecheck` 绿；`check-decisions` 通过。
—— 全文（层号级联表 / patch 同源的两条机械判据 / 四条读源码守卫的迁移表 / 跨项目盲区）→
`docs/nn/engineering.md` §28「第十六刀」

## §2026-09-24-goalnn-loop-eval-chain-split（2026-09-24，用户指令「按同一条「真实调用链」手法拆 rl/loop_steps 的 TrainingSteps 本体（952 行 / 20 方法）」）

拆 `rl/loop_steps.py`：**940 → 666 行**；`TrainingSteps`（845 行 / 20 方法）里**唯一一条真正的
方法间调用链**（8 成员 / 275 行 / 5 个状态槽）搬到新模块 `rl/loop_eval.py::TrainingEval`
（375 行）。

- **刀口怎么选的（先量后定，不按行数等分）**：20 个方法里只有 7 个**互相调用**，且连成一条链
  （`_dispatch_delayed_eval → _sweep_eval_tail` · `_drain_pending_eval → _eval_covered/_eval_on_round/
  _sweep_eval_tail` · `_join_eval → _eval_join_soft_sec → _eval_policy_cfg`）；其余 13 个都是被轮内
  步骤各自调用的**叶子**（外部入口只有 3 个：轮内 `_dispatch_delayed_eval` / `_join_eval`、收官
  `_drain_pending_eval`）。⇒ 簇 = 这条链（含消费 `_eval_on_round` 的占位）。
- **切法仍是混入**（同一对象、同一把锁、零行为变化，测试一行不改），但**基类元组是追加**：
  `class TrainingSteps(TrainingRemote, TrainingEval)`。追加而非插队的判据是硬的 —— 2026-09-23
  写下的 `TrainingSteps.__mro__[1] is TrainingRemote` 与四个「继承真混入」的测试宿主**逐字仍成立**；
  两混入间零重名、零互调、零 `super()` ⇒ 顺序今天完全惰性，没有理由去动已记录的 MRO。
- **状态归属唯一**：五个 eval 槽位（`_eval_thread` / `_eval_gate` / `_eval_tail` / `_eval_tail_start` /
  `_eval_join_sec`）声明**只在** `TrainingEval`。旧类里剩下两处**跨模块使用**（`_log_report` 把 stream
  报告里的线程句柄写进 `_eval_thread`、`_record_iteration` 读 `_eval_join_sec`）**经继承**解析 ——
  它们被写成一张闭集表（`CROSS_MODULE_HANDS`），第三条手出现即红。
- **★ 本刀与前两刀最大的不同：零模块级名字**。搬走的全是方法 + 槽位，所以**没有任何 patch 点需要
  迁移**（对 `rl.loop_steps.*` 的注入本来就是空操作：那些名字从来没住在那儿）。DI 目标照旧是
  方法体内延迟 import `rl.eval_dispatch` / `rl.eval_local` / `rl.queue` / `rl.archive`，测试 patch 的
  一直是那些**实现模块**。
- **守卫 = 契约**（`tests/trainer/test_loop_eval_split.py`，11 例）：8 成员定义只在 `TrainingEval`（**闭集**，
  顺手加 helper 会红）· `TrainingSteps.X is TrainingEval.X` 对象恒等 · `__bases__ == (TrainingRemote,
  TrainingEval)` 且组合类三件套不变 · 真实现在 `loop_core` 仍胜过占位 · 五槽位**单处声明**（旧类里
  再声明即红）· 跨模块手闭集 · 顶层 import **闭集** + DI 只许延迟 · 不得反向 import `rl.loop_steps` /
  `rl.loop_core` · **两条功能性**：跨模块交棒（`_log_report` 写 → `_join_eval` 读 → `_eval_tail` 落在
  同一实例）· 占位**响亮失败**（`raise NotImplementedError` 而不是静默返回 falsy 把 eval 全关掉）。
- **⚠ 第六次撞上「按路径读源码的守卫」**：`tests/trainer/test_eval_a_once.py` 断言 `"eval_dispatch import
  dispatch_eval_bg" in loop_steps 源码` ⇒ 搬走后**红**（这次是响亮失败，运气）。修法升级为
  **在 `rl/` 源码树里找谁持有这个名字**，并要求「拿到它的模块里住着 `_dispatch_delayed_eval`」——
  既不怕改名，也不会在搬走后退化成「另一个无关模块替我绿」。
- **⚠ `ruff format --check` 不是门禁**：HEAD 上 `loop_steps.py` 本来就有两处不满足 `ruff format`
  （line-length 100 下的隐式拼接/长行）⇒ 别顺手 `ruff format`（会往 diff 里掺进与本案无关的重排）。
  只认 `ruff check`（I001：新建的注释块与 import 之间要空行）。
- **纯搬对账**：AST 逐成员 ⇒ **8/8**（其中 1 处**申报差异**：占位 raise 文案 `TrainingSteps` →
  `TrainingEval`，点名新家）+ **5/5 槽位逐字随簇** + 旧类零残余。
- **违反后果**：就地补同名方法 / 新家塞 helper / 基类插队 / 组合类重复接线 / 删掉 `loop_core` 的真实
  实现 / 抹掉槽位默认值 / 旧类重复声明槽位 / 跨模块手改成 `getattr` / DI 提到顶层 / 顶层长重依赖 /
  反向 import 门面 / 占位改静默 / 交棒断链 / 尾巴丢掉 —— 十四类都在**提交时**红（反探针 **14/14**）。
- **门禁**：nn **2462 → 2473 passed / 3 skipped**；mypy **418** 源文件绿；根 `bun run check`
  2119 pass（1 例 `dist-node-gate`「单次慢响应不判死」在全量并发下计时 flake，单独复跑 3/3 绿）。
—— 全文（选簇判据 / 调用图 / 守卫表 / 坑）→ `docs/nn/engineering.md` §28「第十七刀」。

## §2026-09-25-goalnn-loop-volume-chain-split（2026-09-25，用户指令「全程自主，继续按同一手法拆 `rl/` 侧神模块」）

拆 `rl/loop_core.py`：**1386 → 931 行**（搬走的块共 462 行 —— 445 行方法 + 9 行分节注释；
留下的 8 行是旧位置的**指路注释**）；`TrainingLoop`（25 方法 / 1089 行）里**唯一一条真正的
方法间调用链**（9 成员 / 445 行 = 原模块 32%，且恰是旧类的**尾块**）搬到新模块
`rl/loop_volume.py::TrainingVolume`（**573 行** = 445 行方法 + 前导 docstring/import/声明块）。

- **刀口先量后定（工具化）**：新写 AST 侦察工具（`nn-training/tmp/recon_god.py`）把「类内调用图 +
  连通分量 + 每方法读的模块全局 + 写槽」一次量出。同一份量法在 `rl/batch_eval.py` / `rl/bc_loop.py` /
  `rl/loop_core.py` 上跑，结论：`loop_core` 有 **3 条独立链**（volume 9 成员 / 生命周期 7 / 基线评估 2），
  最大且最内聚的是 volume（9 成员，一个连通分量，5 个共享槽）；
- **方向 = 调用者依赖被调用者**（与 S4 第二步、第十七刀同源）：本簇的**生产入口全部**在
  `RoundSteps`（`step_course_iter` → `_iteration_pairs`；`step_rollout` → `_volume_active` /
  `_volume_collect_continuous`）⇒ **`class RoundSteps(TrainingVolume)`**。
  **刻意不走「给 `TrainingLoop` 加基类」**：那要改组合类 + 四个「继承真混入」的测试宿主，并让
  第十七刀守卫里「组合类三件套不变」那句失守 —— 走调用者一侧 ⇒ `TrainingLoop.__bases__ ==
  (RoundSteps, TrainingSteps, TrainingGuards)` 与**全部既有守卫一行不改**。
- **`_volume_topup`（离散补波）自 VOLUME_RULE_V2 起已退役**：生产路径全走 `_volume_collect_continuous`，
  `step_volume_topup` 只是 `STEP_ORDER` 要求的**空步**、不调本簇任何方法（本刀的守卫会断言这两条）。
  它仍留在此簇，因为既有 e2e/单测以 unbound 形式直接驱动它（`volume_waves` 的纯逻辑是那条规则的可复算实现）。
- **本刀唯一要迁的 patch 目标 = `log`**：`_volume_topup` 的日志按**模块全局**解析 ⇒
  `monkeypatch.setattr(rl.loop_core, "log", …)`（`e2e/test_volume_e2e.py`）搬后成**静默空操作**
  （同名 seam 在两个命名空间里是两个各自真实的注入点 —— S4 第二步的教训第三次现身），已改到
  `rl.loop_volume.log`。判定 `dist_common` 的两个方法内重复 import 后：**删顶层那份**（而不是删方法内那份）
  —— 这是唯一能保持「方法体逐字节不变」的改法（`ruff` 的 F401/F811 只认前者）。
- **纯搬对账**：AST 逐成员比对 HEAD vs 新家 ⇒ **9/9 逐字节等价、零申报差异**（与前两刀不同：本刀
  连文案都不用改，占位/异常都没有）；旧类在**同一位置**留下**指路注释**（读者找 `_volume_topup` 不两手空空）。
- **守卫 = 契约**（`tests/trainer/test_loop_volume_split.py`，11 例）：9 成员定义只在 `TrainingVolume`（**闭集**）·
  `TrainingLoop.X is TrainingVolume.X` 对象恒等（既有用例用的是 unbound 绑定）· `RoundSteps.__bases__`
  + 组合类三件套 + **判定 MRO 逐项** · 七槽位声明在新家且 `__init__` 仍全部赋值 · **跨模块手闭集**
  （`__init__` Store×7 + `_record_iteration` Load×3 —— 量出来的，不是猜的）· 顶层 import 闭集 ·
  `rl.volume_waves` / `rl.volume_quota` 只许延迟 import · 不得反向 import · **两条功能性**：
  `log` seam 在本模块（打 `rl.loop_core.log` 一个字节都收不到）· unbound 绑定经 MRO 取到真实现。
  反探针 **14/14 命中**（每条先 `assert count(old) == 1`）。
- **⚠ 坑（两个，都是「散文会撒谎」）**：① 侦察初稿把「外部入口全在 `RoundSteps`」写成了
  「`step_rollout` / `step_volume_topup`」——而后者是**退役空步**，`_volume_topup` 生产零调用点；
  守卫里那句「RoundSteps 真的在调它」当场把它顶出来 ⇒ 四处散文（两处 docstring + 指路注释 + 模块头）改对。
  ② 分层快照按设计**先红再登记**：`rl/loop_volume.py → rl.rollout_phase` 使 `loop_volume` 成为
  「经 rl 传递可达 remote」的一员 ⇒ 登记进 `tests/test_layering.py::RL_ORCHESTRATION`（第三次依此流程）。
- **违反后果**：就地补同名方法 / 新家塞 helper / 基类不继承 / 组合类改元组 / 调用方就地重定义入口 /
  反向 import / 顶层长重依赖 / 延迟 import 提到顶层 / 删槽位声明 / 新增跨模块手 / 宿主不初始化槽位 /
  删指路注释 / 退役空步接回生产 / `log` seam 改经宿主命名空间 —— 十四类都在**提交时**红。
- **门禁**：nn **2473 → 2484 passed / 3 skipped**（+11 = 新守卫）；ruff / mypy **420** 源文件绿；
  根 `bun run check` 2120 pass / 0 fail；dashboard 未动（本刀零 `dashboard/**` 改动，故不触发其门禁）。
—— 全文（侦察表 / 调用图 / 守卫表 / 坑 / 下一刀候选的实测理由）→ `docs/nn/engineering.md` §28「第十八刀」。

## §2026-09-25-goalnn-loop-lifecycle-chain-split（2026-09-25，用户指令「拆 loop_core 余下的生命周期链（7 成员 = 主循环骨架），先答清「拆出去后谁是宿主」」）

拆 `rl/loop_core.py`：**931 → 446 行**；`TrainingLoop` 本体里最后一条方法间调用链（7 成员 / 351 行）
连同 7 个**只能随它走**的模块级定义（150 行，闭包实测）搬到 `rl/loop_lifecycle.py::TrainingLifecycle`
（**673 行**）。

- **宿主判据（本刀题眼，可证不是偏好）**：这一簇的入边是 `_evalboard_idle`——被 `rl/loop_remote.py`（1 处）
  与 `rl/loop_round_steps.py`（2 处）以 `self.` 调用。新混入必须同时是两个 caller 的祖先才接得住这条**既有**
  入边；而 `set(RoundSteps.__mro__) ∩ set(TrainingRemote.__mro__) == {object}` ⇒ **任何 sibling 宿主都不存在**
  （S17/S18 的「挂到调用者一侧」在此无解），唯一出路 = 组合根 `TrainingLoop`。出边同指：`.run` /
  `.run_one_round` / `._setup` / `.finish_course` 的调用者全是 `TrainingLoop` 实例（`loop_serve` 的 `engine`
  是多态 `BcLoop | TrainingLoop`，而 `BcLoop` 自带 `_setup`／`run_one_round`，与本簇无关）。
- **代价与边界**：`TrainingLoop.__bases__` 三件套 → **末位追加**四件套 `(RoundSteps, TrainingSteps,
  TrainingGuards, TrainingLifecycle)`；追加末位的依据是实测「7 个成员名在既有混入里零同名定义」（MRO 不遮罩）。
  `TrainingSteps.__bases__` / `RoundSteps.__bases__` / 各 `__mro__[1]` 逐字不变——S17/S18 的「追加不插队」纪律仍成立。
- **两处旧断言的演进登记**：`tests/trainer/test_loop_eval_split.py` 与 `tests/trainer/test_loop_volume_split.py` 里钉「组合类三件套
  逐字不变」的断言（S17/S18 写的）必须随本刀演进为四件套；改的是**断言里的事实**，两处**把心**（本簇不是组合类
  的直接基类 / `RoundSteps` 与 `TrainingSteps` 的基类元组）逐字未动。
- **状态归属不变**：槽位声明仍全在 `TrainingLoop.__init__`；本混入的声明块只是**借用**（逐项等于「碰到的、
  不属于本模块的」名字集合，42 个，守卫按派生集对账）。`round_failure` 声明为 `Callable[..., RoundOutcome]`
  而不是 `Any`——因为 `run_one_round` 直接 `return` 它，`Any` 会让 mypy 报 `no-any-return`，而方法体要逐字节
  保持搬前原样（精度放声明里，不放方法体）。
- **跨模块手（三张表）**：入边闭集（`loop_remote` ×1 · `loop_round_steps` ×2）· 出边闭集（`run` →
  `_drain_pending_eval`（TrainingEval）· `run_one_round` → `round_steps`/`round_failure`（RoundSteps）·
  `finish_course` → `_sync_cloud_halt`（TrainingGuards））· 槽位写-读手（`_course_fp`/`_corpus_fp` 由
  `_setup_common` 写、`_prepare_iter_dir`/`_rollout_phase` 读）——全是量出来的。
- **纯搬对账**：AST 逐成员 ⇒ **14/14 逐字节等价、零申报差异**；留下的成员 **10/10 也逐字节等价**。
- **7 个模块级名字零 monkeypatch 点**（实测）⇒ 只同步 4 处普通 import（`rl/collect_only.py` 延迟 import 保环断 ·
  `tests/trainer/test_loop_park.py` · `tests/worker/test_paired_seed_check.py` · `e2e/test_run_rl_m1.py`），旧家**不留别名**
  （陈旧 `setattr(rl.loop_core, …)` 会响亮 `AttributeError`，不是静默空操作）。`run_inspect` **反向**：它是
  文档化的可替换点（`_run_inspect` 的委托点）⇒ 必须留在旧家，守卫正面断言。
- **守卫** `tests/trainer/test_loop_lifecycle_split.py`（13 例）：成员/模块级名字定义只在新家 · 对象恒等 · 旧家无别名 ·
  组合类四件套 + 判定 MRO · **宿主判据的机器形式**（入边呼叫点计数 + 祖先集交集为空 + `not hasattr(RoundSteps,
  "_evalboard_idle")`）· 借用声明 == 派生集（且类体零带值槽位）· 三张手表 · 顶层 import 闭集与禁反向边 ·
  **★ 三条功能性**（出边手解析到预期的那个类 · `run_one_round` 真跑通（终态直通 / 异常分类走 `round_failure` /
  让位响亮报错）· 旧家不再吸收 patch）。反探针 **18/18**（每条先 `assert count(old) == 1`）。
- **坑（三个，全是「搬家后按路径读源码的守卫失效」家族）**：① `tests/trainer/test_batch_eval.py` 按写死路径读
  `loop_core.py` 找 `maybe_dispatch_batch` ⇒ 搬到 `loop_lifecycle` 后假红；改读**持有者**（并把「恰好一处定义」
  写进断言）。② `e2e/test_loop_supervisor_integration.py` 用 `monkeypatch.setattr(lc.time, "sleep", …)`
  这个**中间名字**打补丁 ⇒ 旧家不再 import `time` 后响亮 `AttributeError`；改成直接补 `time` **模块对象**
  （与原先等价，且不再依赖任何中间命名空间）。③ **跨项目盲区（第十六刀同族）**：`dashboard/tests/kickstart-receipt.test.ts`
  按写死路径读 `loop_core.py` 找 `KICKSTART_DEFAULT_WARN` ⇒ 齐红；按第十六刀的修法改成正的**源码树搜定义**
  （`rlSourceDefining(name)`），并同步四处注释里的模块路径。
- **反探针锚点的教训（延续第十五刀）**：⑧ 原定改 `self._evalboard_idle(it, ctx.dist_cfg)`——该字面量在
  `loop_round_steps.py` 里**有两处**，`assert count == 1` 当场拦下（锚点写错 ≠ 守卫空档），改用 `loop_remote`
  里唯一那处。
- **违反后果**：旧家留别名/副本 · 成员就地在旧家重定义 · 组合类元组插队或抽掉新基类 · 挂到 sibling · 入边被
  sibling 截胡或改形状 · 新增入边/出边手 · 槽位写手或读者漂 · 顶层 import 长出或反向 import · 成员改名 ·
  同名副本抢走出边手的归属 · `run_inspect` 被误搬 —— 十八类都在**提交时**红。
- **门禁**：nn **2484 → 2497 passed / 3 skipped**（+13 = 新守卫）；ruff `All checks passed`；mypy 绿；
  根 `bun run check` 2120 pass / 0 fail；dashboard typecheck + **1105 pass / 0 fail**（动了四处注释 +
  一条跨项目守卫）。
—— 全文（侦察表 / 宿主判据 / 守卫表 / 坑）→ `docs/nn/engineering.md` §28「第十九刀」。

## §2026-09-25-goalnn-loop-core-tail-split（2026-09-25，用户指令「拆 loop_core 剩下的基线评估链（2 成员）与叶子方法，让组合根成为纯组合类」）

拆 `rl/loop_core.py` 的**尾簇**：**446 → 240 行**，余下的 7 个叶子按**判据同源**分三簇，各自成模块（合计 355 行 = 128 + 96 + 131）。
`TrainingLoop` 至此是**纯组合类**——只剩 `__init__`（槽位）与 `_run_inspect`（唯一结构性例外）。

- **三簇（分组判据 = 谁调它 / 同源在哪，不是按大小切）**：`rl/loop_baseline.py::TrainingBaseline`
  （`_baseline_eval_weights` / `_maybe_dispatch_baseline_eval`，判据「日志里有没有这份 bc 权重的 it0 干净评估」）·
  `rl/loop_iter_dir.py::TrainingIterDir`（`_check_quota_incident` / `_prepare_iter_dir`，判据「`self._traj_dir` 里有没有
  本轮的活」）· `rl/loop_dispatch.py::TrainingDispatch`（`_rollout_phase` / `_eval_on_round` / `_evalboard_yield`，判据
  「本轮把活派给谁 / 让位给谁」）。
- **宿主：三簇都挂 `RoundSteps` 一侧，组合根一行不改**——mixin 级父调用者全部是 `RoundSteps`；`_eval_on_round` 另有
  `TrainingEval` / `TrainingGuards` 两个 sibling 调用者，但三者互不继承**不构成**「必须挂组合根」的理由：`RoundSteps`
  是组合根的**第一个基类**，挂它的基类里就已经在 `TrainingLoop` 的线性化上（与第十九刀的区别：那刀两个 caller 都是
  组合根的基类且交集为空）。⇒ `TrainingLoop.__bases__` 逐字不变，S17/S18/S19 三处元组断言两句不改。
- **★ 唯一真约束 = `_eval_on_round` 的顺序契约**：`rl/loop_eval.py` 的同名成员是**占位**（body `raise`），真实现必须
  在 MRO 里更靠前，否则占位胜出 ⇒ 静默返回 falsy 把 eval 全关掉。挂 `RoundSteps` 一侧天然满足；把三簇改成组合根
  **末位**（看似更整齐）**会反过来胜出** —— 反探针 ⑤ 实测变红。
- **结构性例外（「纯组合类」在本仓的精确含义）**：`_run_inspect` 与模块级 `run_inspect` 必须同住组合根——`_run_inspect`
  按**模块全局**解析它（文档化可替换点，`rl/loop.py` 再导出），而那个模块不能 import `rl.loop_core`（成环）⇒ 只能留一个
  方法。守卫 `test_composition_root_keeps_only_the_structural_pair` 正面钉住闭集 `{__init__, _run_inspect}`。
- **三张跨模块手表**：入边闭集（`_check_quota_incident`/`_prepare_iter_dir`/`_rollout_phase`/`_evalboard_yield`/
  `_maybe_dispatch_baseline_eval` 各 1 · `_eval_on_round` 3）· **出边为空**（三簇互不调兄弟方法）· 槽位读者闭集 +
  **写手唯一**（`_course_fp`/`_corpus_fp` **只在** `loop_lifecycle._setup_common` **赋值**，本刀三簇只读）。
- **★ 写手那条是反探针抓出来的**：首版沿用 S19 的宽松口径（「谁碰到这个名字」），而 `run_one_round` 也**读**
  `_course_fp` ⇒ 把赋值点改名成 `_course_fp_x` 时守卫**不红**（探针 ⑬ 首次 17/18）。修法：`_self_assigns()` 只看
  `Assign`/`AnnAssign` 的 target，并断言赋值点集合 == `{"_setup_common"}`。**只数「谁碰到」不够，要把「谁拥有」写成断言**。
- **写法教训（同一处假红）**：入边首版沿用 `src.count(f"self.{member}(")` 口径，而新模块头注里那句
  「`self._maybe_dispatch_baseline_eval(...)` 的解析与搬家前逐字相同」被算成**一条入边** ⇒ 守卫对着**合法的文档**报假红。
  改成 AST 计真实 `Call` 节点 + AST 求定义面：**入边是语法事实，就该用语法量**。
- **坑（第九/十/十一次同族）**：`tests/trainer/test_batch_eval.py`（写死路径读 `loop_core.py`）·
  `e2e/test_loop_supervisor_integration.py`（经中间名字 `rl.loop_core.time` 补 `time.sleep`）·
  dashboard 跨项目盲区（`kickstart-receipt.test.ts` 改成源码树搜定义）。
- **记账校正**：第十九刀公布的 `loop_lifecycle.py` 行数 **617 → 672** 还差一（真值 **673**）⇒ 连提交消息（`--amend`）
  带四处文档一次改齐；教训：**可测量值别在编辑过程中随手报，落盘后量一次**。
- **不做**：`docs/nn/training-stack.md` / `remote-transport.md` 里带日期的历史记录不改写（那时指针正确）。
- **违反后果**：旧家留别名/副本 · 组合根多出方法 · `RoundSteps.__bases__` 插队或抽基类 · 三簇改挂组合根末位（顺序契约
  翻车）· 入边被 sibling 截胡或改形状 · 新增入边/出边手 · 槽位写手或读者漂 · 顶层 import 长出或反向 import · 成员改名 ·
  占位失去「响亮失败」语义 · 类体长出带值槽位 —— 十二类都在**提交时**红（反探针 18/18）。
- **门禁**：nn **2497 → 2510 passed / 3 skipped**（+13 = 新守卫）；ruff `All checks passed`；mypy 绿；
  根 `bun run check` 2120 pass / 0 fail；dashboard typecheck + **1105 pass / 0 fail**；`check-decisions` ok。
—— 全文（分组表 / 宿主与顺序契约 / 三张表 / 反探针教训）→ `docs/nn/engineering.md` §28「第二十刀」。

## §2026-09-25-goalnn-loop-export-cluster-split（2026-09-25，用户指令「继续按同一手法拆 `rl/loop_steps.py` 或 `loop_remote.py`」）

拆 `rl/loop_steps.py` 的**产物出包簇**：**667 → 541 行**，4 个成员 → 新模块 `rl/loop_export.py::TrainingExport`
（**210 行**）。同一对象、同一把锁、零行为变化，测试一行不改。

- **侦察先答「有没有链可牵」**：`TrainingSteps`（667 行 / 12 方法）里**唯一一条方法间调用链** =
  `_export_offline_bundle` → `_volume_plan_block`；其余 **8 个成员全是叶子**（类内零互调）。⇒ 无连通链可顺手牵，
  改按**判据同源**取簇：判据 = 「产物打包 / 打指纹 / 缓存 TS 码」= **出包家族**的同一件事（同源失败语义：指纹不匹配 ⇒
  `RetryableError` 之外一律响亮；缓存语义同规）。
- **四成员**：`_ensure_ts_code` · `_volume_plan_block` · `_export_offline_bundle` · `_export_weights`。
- **宿主：末位追加** —— `class TrainingSteps(TrainingRemote, TrainingEval, TrainingExport)`；`__mro__[1]` 仍是
  `TrainingRemote`，`TrainingLoop.__bases__` 一行不改。**遮罩面实测**：4 个成员名在既有混入里**零同名 `def`** ⇒
  末位追加不会被 MRO 遮罩（这正是「追加末位」的依据，而不是「看起来整齐」）。
- **方向 = 调用者依赖被调用者**：入边 `loop_round_steps.py` ×2（`_export_offline_bundle` / `_export_weights`）+
  `loop_remote.py` ×1（`_volume_plan_block`）+ 本模块内 ×1（`_export_offline_bundle` → `_volume_plan_block`）；出边闭集 = `{_remote_ppo}`。
- **patch 面 = 零迁移**：`rl.loop_steps` 命名空间被 `monkeypatch.setattr` 的只有 `log`，而它测的是**留守**的
  `_write_iter_stats` ⇒ 本刀无 patch 点需迁移；`dist_common` / `backup_weights` / `_MODE_BACKUP_PREFIX` 全仓无测试 patch。
  但 `log` 在**新模块**里解析 ⇒ 守卫钉「`_export_weights` 真跑且 `log` seam 落在**本模块**（打旧家一个字节都收不到）」。
- **槽位手**：`_ts_code_sha256` / `_ts_code_zip_path` 的读手仍住 `rl/loop_remote.py`（写成闭集表）。
- **死 import 清理**：删 `import dist_common` / `from rl.archive import backup_weights` / `from rl.modes import
  _MODE_BACKUP_PREFIX`（AST 断言零引用）——「搬走实现后旧家的 import 会变成死代码」。
- **★ 真实发现（不是代码错，是散文错）**：`_export_offline_bundle` 的「无起点权重」分支在盘上**走不到**——
  `dist_common.weights_fingerprint` → `sha256_file` 对不存在的文件**响亮抛 `FileNotFoundError`**（不是返回 falsy）；
  故功能性用例改测第一行的 `iters<=0` 门（真跑 `SystemExit`）。
- **分层快照先红再登记（第六次）**：`RL_ORCHESTRATION` 加 `loop_export`——理由与既有条目不同：它**自己不经 remote**，
  是 `_ensure_ts_code` 方法体内**延迟 import** `remote.hub_client` 而入选（AST 也看函数内 import）。
- **dashboard 跨项目盲区（第十六/十九/二十刀同族）又一次**：`src/server/actions/course-lifecycle.ts` 两处注释把
  `--run-iters<0` 守卫指到 `rl/loop_steps.py`，而该守卫在 HEAD（S20）上**早已**住 `rl/loop_remote.py`（旧文件零
  `run-iters`）⇒ 改成 `rl/loop_remote.py`。**注释-only**。另两处 `specs.ts` / `push-config.ts` 明写「S4 首簇前在
  `loop_steps.py`」= 历史记录，**不改**；`tests/exit-watchdog.test.ts` 是**伪造 traceback 夹具**，与真实路径无关。
- **违反后果**：搬走成员在旧家留别名/副本 · 基类元组顺序漂（`__mro__[1]` 或组合类元组）· 入边闭集长出或被截胡 ·
  新增出边 · 槽位手漂 · 顶层 import 长出或反向 import · 成员改名 · 旧家重吸收 `log` patch —— 均在**提交时**红
  （反探针 19/19）。
- **门禁**：nn **2510 → 2524 passed / 3 skipped**（+14 = 新守卫）；ruff `All checks passed`；mypy 绿；
  根 `bun run check` 2120 pass / 0 fail；dashboard typecheck + **1105 pass / 0 fail**；`check-decisions` ok。
—— 全文（成员表 / 宿主与末位追加依据 / 三张表 / 死 import / 反探针教训）→ `docs/nn/engineering.md` §28「第二十一刀」。

## §2026-09-25-goalnn-loop-remote-cluster-split（2026-09-25，用户指令「把 `loop_remote.py` 那 862 行连通分量按「判据同源」拆成多个混入」）

`rl/loop_remote.py` **997 → 35 行**（−96%）。13 个方法（862 行，一条连通分量）按**判据同源**切成四簇，
各成模块（111 / 551 / 112 / 291 行，方法体本身 69 / 470 / 76 / 247），组合根留在原文件且**零方法**。同一对象、同一把锁、零行为变化。

| 混入 | 判据 | 模块 | 方法 |
|---|---|---|---|
| `TrainingRemotePush` | 把一份 job **送到节点**（提交 / 首发 / 取回） | `rl/loop_remote_push.py` | `_push_submit_node` · `_push_submit_first` · `_push_fetch` |
| `TrainingRemoteJob` | **一份远端 PPO job 的四步** + 组合入口 | `rl/loop_remote_job.py` | `_remote_ppo` · `_remote_ppo_publish` · `_remote_ppo_probe` · `_remote_ppo_fetch` · `_remote_ppo_land` |
| `TrainingRemoteFail` | **远端失败的唯一处置策略** | `rl/loop_remote_fail.py` | `_abort_node_failure` · `_handle_remote_failure` |
| `TrainingRemoteDrive` | **谁驱动这条腿**（轮内 / 整轮 / 整段） | `rl/loop_remote_drive.py` | `_remote_ppo_step` · `_remote_iter` · `_remote_run_segment` |

- **刀口：一条连通分量没有「链」可牵 ⇒ 判据同源**。S19/S20 的取法是「找方法间调用链」，而这里 13 个节点
  本就连成**一个 862 行连通分量**（S4 第二步已按链切过一次）。于是按「同一件事 / 同一套失败语义」切：
  推送腿 · 一个 job 的四步 · 失败策略 · 驱动入口。**不是按大小、不是按物理位置。**
- **宿主 = 把 DAG 写进类声明**（调用者依赖被调用者）：`Push ← Job ← {Fail, Job} ← Drive ← TrainingRemote`。
  `Fail` 与 `Job` 并列成为 `Drive` 的第二个基类（驱动要用失败策略）。MRO 线性化 = `TrainingRemote,
  TrainingRemoteDrive, TrainingRemoteFail, TrainingRemoteJob, TrainingRemotePush, object`——13 名**各一所有者**。
- **组合根仍住 `rl/loop_remote.py` 且零方法**（`class TrainingRemote(TrainingRemoteDrive)`）——因此
  `TrainingSteps.__bases__` / `TrainingLoop.__bases__` / 四个「继承真混入」的测试宿主 / 既有
  `from rl.loop_remote import TrainingRemote` 调用点**一行不改**（`__mro__[1]` 仍是它）。
- **patch 面**：`_push_submit` / `_push_wait_result` 随推送腿迁到 `rl.loop_remote_push`（e2e 的 2 处
  `monkeypatch.setattr` 改址）；`dist_common` 随驱动迁到 `rl.loop_remote_drive`。**patch 旧的
  `rl.loop_remote.*` 现在是静默空操作**——新守卫正面钉「组合根零 seam」+ 每模块读自己新家的全局。
- **守卫演进 6 处**：`test_loop_transport_split`（定义面改读**组合根 MRO**、入边/import 面改读一族、
  `_remote_ppo.__module__` → `rl.loop_remote_job`）· `test_loop_export_split`（两条入边换落点、槽位读者
  改元组）· `test_loop_lifecycle_split`（`_evalboard_idle` 入边 → `loop_remote_job.py`）·
  `test_loop_core_tail_split`（全量 `MRO_NAMES` 插四名）· `test_loop_volume_split`（远端一族改为
  「四新家 + 组合根」）· `test_layering`（四名登记，先红再登记**第七次**）。新守卫
  `tests/trainer/test_loop_remote_split.py`（**15 例**）+ 反探针 **24/24 全红**（含功能性两条）。
- **★ 本刀的新形态坑（与「搬家后按路径读源码的守卫**响亮**失效」不同）**：`test_loop_volume_split` 里
  按 `rl/loop_remote.py` **类名**枚举方法的覆盖面，在类体被切空之后**静默失去覆盖却不红**（读到空集
  也算通过）⇒ 主动改成读「四新家 + 组合根」。**搬家时要问的不只是「谁会响亮地读它」，还有「谁会静静地读到空」。**
- **dashboard 跨项目盲区（第十六/十九/二十/二十一刀同族）又一次**：`course-lifecycle.ts` 两处注释把
  `--run-iters<0` 守卫指到 `rl/loop_remote.py`——S21 才刚修成那个名字，S22 又把它搬进
  `rl/loop_remote_drive.py`（`_remote_run_segment` 本体）⇒ 同步改址（注释-only，dashboard 门禁照跑）。
- **违反后果**：五件链断任一环或插队 · 组合根长出方法/seam · 成员在错家重复定义 · 借用声明多/少 ·
  helper hand 没在用到它的文件里声明 · 入边/出边/槽位写手漂 · 顶层或延迟 import 面长出 · 反向边 ·
  失败策略不再停腿 —— 均在**提交时**红（反探针 24/24）。
- **门禁**：nn **2524 → 2539 passed / 3 skipped**（+15 = 新守卫）；ruff `All checks passed`；mypy 绿（433 文件）；
  根 `bun run check` 2120 pass / 0 fail；dashboard typecheck + 1105 pass / 0 fail；`check-decisions` ok。
—— 全文（成员表 / 四簇判据 / 链式宿主 / 三张表 / 静默失去覆盖那条教训）→ `docs/nn/engineering.md` §28「第二十二刀」。

## §2026-09-25-goalnn-loop-guards-cluster-split（2026-09-25，用户指令「拆 `rl/loop_guards.py` 或 `loop_steps.py` 剩下的叶子，先给侦察结论与刀口判据」）

`rl/loop_guards.py` **785 → 194 行**（−75%）。13 个方法按**判据同源**切成四簇，各成模块
（177 / 200 / 287 / 78 行），组合根留在原文件并留下**四个共享 sink + 三个判决词表常量**。
同一对象、同一把锁、零行为变化。

| 混入 | 判据（同一件事的一条轴） | 模块 | 方法 |
|---|---|---|---|
| `TrainingGuardsTrip` | **过程面**硬边界（更新健康度 / 评估显著度，**连击式**） | `rl/loop_guards_trip.py` | `_breaker` · `_stop_loss` |
| `TrainingGuardsLeg` | **结果面**停腿（退回了吗 / 比对照臂差吗；读趋势 + 停云机） | `rl/loop_guards_leg.py` | `_kickstart_burn` · `_paired_kill` |
| `TrainingGuardsGate` | **课程结束门一整族**（求值 → 判决落地 → 预算硬断） | `rl/loop_guards_gate.py` | `_gate` · `_apply_verdict` · `_warn_min_train_unreachable` · `_budget_hard_cut` |
| `TrainingGuardsSweep` | **轮级磁盘回收**（keepIters / job 目录 / 孤儿波次） | `rl/loop_guards_sweep.py` | `_rotate_cleanup` |

- **刀口：先选文件，再选切法**。`loop_steps.py` 余下 8 叶**被否决**——它们类内零互调、且判据彼此
  无关（课程读盘 / 配额 / 落账 / 取证 / journal），拆它只能按大小。`loop_guards.py` 的调用图是
  **多 sink 的 DAG**（不是 S22 的连通分量、也不是 S19/S20 的单链）：`_ledger_apply` 被 **3 簇**调、
  `_sync_cloud_halt` 被 **2 簇 + 外部** `loop_lifecycle.finish_course` 调。
- **宿主 = 留宿主**（提供者留根、调用者出包），依据是 S19 已记录的规则「**入边来自多个 sibling ⇒
  锁进组合根**」。四簇彼此零互调 ⇒ 基类元组顺序恒惰性（零重名、零 `super()`）。
  `TrainingLoop.__bases__` / `TrainingSteps.__bases__` / 全部既有 import 与测试宿主**一行不改**。
- **★ 本刀与前三刀最大的差别：patch 面零迁移**。留根的两人正是 **patch 锚点**——`set_cloud_halt` /
  `dist_common` 被**四个测试文件 5 处**以 `monkeypatch.setattr("rl.loop_guards.…", raising=True)`
  打桩（`test_paired_kill` / `test_loop_gate_nopark` / `test_loop_gate_soft_remediate` /
  `test_kickstart_plan` / `test_loop_park`）。搬走`_sync_cloud_halt` 会让这些桩**静静失效** ⇒
  「谁是 patch 锚点」升级为**宿主定档的硬判据**（新守卫正面钉锚点必须在根、四簇不得持有）。
- **守卫演进 4 处**：`test_loop_core_tail_split`（全量 `MRO_NAMES` 插四名 + `INBOUND_CALLS["_eval_on_round"]`
  的呼叫点随 `_gate` 迁到 `loop_guards_gate.py`）· `test_loop_volume_split`（覆盖面主动扩成
  「四新家 + 组合根」——S22 那条「静默读到空」的教训的**预防性**应用）· `test_layering`（**未红**：
  四簇经 `rl` 传递**不达** remote，故无需登记——「先红再登记」的反面，「不红」同样是信息）·
  dashboard **无需改**（`specs.ts:416` 指的 `_gate_halt_mode` 正留根）。
- **mypy 强制出的设计事实**：四簇的类体必须声明**借用**的槽位（首轮 13 个 `attr-defined` 错误），
  且 `loop_guards_leg` / `loop_guards_sweep` 原成员从不需要 `Any` ⇒ 声明面 = 「派生的事实 ∪ 借用的方法」
  （新守卫逐项对账，多一个没用声明也红）。
- **违反后果**：成员在错家重复定义 / 组合根长出方法或回调客户端 / 基类元组漂 / 借用声明多或少 /
  入边出边漂 / 停机态多写手 / 判决词表常量不再是类属性 / patch 锚点搬家 / 旧家又吸收 seam /
  反向往上游 import / 四簇任一真判失效 —— 均在**提交时**红（反探针 27/27）。
- **门禁**：nn **2539 → 2566 passed / 3 skipped**（+27 = 新守卫）；ruff `All checks passed`；mypy 绿（438 文件）；
  根 `bun run check` 2120 pass / 0 fail；`check-decisions` ok。
- **★ 反探针的元教训**：首版两条变异「存活」——查下去是**探针锚点打偏**（变异落在被测 fixture
  走不到的分支上），不是守卫漏。**「存活」先怀疑探针本身，再怀疑守卫。**
—— 全文（刀口对照表 / 宿主定档 / 三张表 / mypy 逼出的声明面）→ `docs/nn/engineering.md` §28「第二十三刀」。

## §2026-09-25-goalnn-batch-store-interface（2026-09-25，用户指令「给 `batch_eval.py` 设计「批存储」接口，为拆那个 1785 行的文件做准备」）

**决定：`rl/batch_eval.py`（1785 行）的拆分前置件 = 一个 `BatchStore` 接口**（新模块 `rl/batch_store.py`）：
`<EVALBOARD_DATA>` 的台账 `batches.jsonl` 与两个请求文件**只有一个所有者**，`status` / `units` / `node_dist`
的每一次变更都发生在**具名转移**里，一次转移 = 一次事务 = 一次落盘。**本节只设计，实施切在 B1–B4。**

- **诊断（实测，不是感觉）**：巨团的真因不是调用图，而是「**台账没有所有者**」——**八个**独立
  read-modify-write 点（`consume_requests` / `claim_pending` / `mark_unit_done` / `_persist_of` / `_requeue` /
  `_reopen_for_resume` + 读面），每个自己决定「改哪些字段 / 何时落盘 / 什么转移」，其中**三套落盘策略**
  （无条件写 / 只在分支内写 / `dirty` 才写）与**五处散落的 `status` 赋值**。任何一条链都要横穿它们 ⇒
  按链切无解（与 §5.3.17 量的同一件事）。
- **接口**：`enqueue` / `enqueue_verdict` / `abort` / `claim` / `set_units_of` / `mark_unit_done` / `requeue` /
  `reopen_for_resume`（写面）· `get` / `all` / `done_units`（读面）· `pending_requests` / `mark_requests_done` /
  `consume_requests`（请求面）。`_tx()` 取代 `@_claim_locked`：跨进程仍用 `train.loop_util` 的 `claim.lock`
  （**拒绝第三套锁**），进程内 `RLock` 可重入 ⇒ `claim()` 内部调 `consume_requests()` 不再需要
  `_claim_held` 那条 thread-local 缝。
- **★ 两条行为语义必须原样保留（集中化时最容易被抹掉）**：① `units.of == 0`（未定型）时 `mark_unit_done`
  **不判 done**；② `aborted` 批的在途 unit 只回填 `node_dist`、**不复活**，且 `requeue` **不改** `aborted`。
- **★ 顺带修一个实测缺陷：非原子落盘。** `write_batches` 用 `Path.write_text`（截断式），而 `batches.jsonl`
  有一个**无锁的跨语言读者** —— console/TS 的 `batches.ts::loadBatches`（坏行**静默跳过**）；它的
  `enqueueBatch` 去重**依赖读全** ⇒ 短读会**重复入队**。探针（读者照抄 TS 读法，写者连续重写 60 轮）：
  现状在 **12 批/2.7 KB 就 126/622 短读**、2000 批 444 KB 时 144/376 短读 + 12 坏行；改 `tmp + os.replace`
  后三档全 **0/0**。同一次事务里把六套落盘策略统一成 **`dirty` 才落盘**（行为等价，且缩小截断窗）。
  **→ ✅ 该缺陷已按 §7 提前单独修完（2026-09-25，独立一小刀，见下一条 § 条目与 `docs/nn/engineering.md`
  第二十四刀）**；§5.5.4 的 B2 因而只剩状态机事务化。
- **它解锁的拆分**：`rl/batch_plan.py`（纯规划/判据/门，零 IO ⇒ 最安全的一刀）· `rl/batch_store.py`（唯一所有者）·
  `rl/batch_runner.py`（执行器 896 + `dispatch_batch_bg` 36，纯搬）· `rl/batch_eval.py` 退成**门面**
  （常量 + `maybe_dispatch_batch` + 逐个**再导出**公开名 ⇒ 既有 import 点一行不改）。
- **被否决的备选**：① 「按链切」（S19–S23 的主流刀法）—— 巨团横穿八个写点，无链可牵（§5.3.17 已量）；
  ② 「把 store 做成一堆模块级函数 + 显式传 `root`」（= 现状换个名字）—— 所有权仍无主，三套落盘策略照旧；
  ③ 「保留 `@_claim_locked` 装饰器、只把函数分组搬家」—— 跨函数嵌套仍靠 thread-local 缝，且一次逻辑事务
  仍要两次落盘。
- **明确不改**：三个文件的字节格式与**写者唯一性**（`batches.jsonl` runner 单写 / `requests.jsonl` console 单写
  append-only / `requests.done.jsonl` runner 单写）· `EVALBOARD_DATA` 口径 · `consume_requests` 的「绝不抛出」·
  `utc_now_iso` 的 UTC+毫秒+Z 格式（`enqueueCovered` 用**字符串比较**判「已物化」）与 `BATCH_STAGE_BASE` /
  `EVAL_SEED0` / `SEGMENT_LEN` 等**双侧镜像**值 · 锁仍复用 `train.loop_util`。
- **违反后果**：落盘不再原子 / 两套落盘策略并存 / `status` 在 store 之外被赋值 / `of==0` 被判 done /
  `aborted` 被复活 / 既有 import 点被改 —— 均在提交时红（B2 交付 `tests/trainer/test_batch_store_txn.py` 五条功能性 +
  「`status` 赋值点闭集」契约守卫）。
—— 全文（分区实测 / 状态机表 / 方法表 / 探针数据 / B1–B5 / 守卫演进清单 / 风险）→ `plan/nn-training-refactor.md` §5.5。

## §2026-09-25-goalnn-batch-ledger-atomic-publish（2026-09-25，用户指令「先把非原子落盘那个缺陷按 §7 写了失败测试再修（不等 B2，独立一小刀）」）

**决定：`rl/batch_eval.write_batches` 从 `Path.write_text` 改为「同目录固定临时名 + `os.replace`」原子发布。**
不改 `batches.jsonl` 的字节格式，不改任何调用方，不碰 B2 的状态机改造。

- **缺陷**：`write_text` 先 `open('w')` **原地截断** live 文件再写字节 ⇒ 「截断」到「写完」之间有一个
  **读者可见**的窗口。本文件的读者里有一个**无锁的跳语言读者** —— console/TS 的
  `batches.ts::loadBatches`（「runner 单写；console 只读」，坏行**静默跳过**），而它的
  `enqueueBatch` 去重（「同 course+rung+ckpt 的 pending 批已存在则返回它」）**依赖读全**
  ⇒ 落在窗口里就**重复入队**。探针实测（写者连续重写 60 轮、读者照抄 `loadBatches` 读法）：
  12 批/2.7 KB **126/622 = 20.3%** · 100 批/22 KB 114/453 = 25.2% · 2000 批/444 KB 144/376 = 38.3% + 12 坏行
  （终次实测 log = `nn-training/tmp/probe-batch-store-final.log`；比例是探针紧循环下的量，逐轮波动）。
- **修法与证据**：`tmp.write_text(...)` + `os.replace(tmp, dst)`（同文件系统原子替换，POSIX `rename(2)` /
  Win32 `MoveFileEx(REPLACE_EXISTING)`）⇒ 读者只能看到**完整的旧快照或完整的新快照**。
  同一探针复测：三档全 **0 短读 / 0 坏行**。临时名**固定**（不随机）⇒ 崩溃残留的 `.tmp` 下次直接覆盖，
  不累积；无需 `finally` 清理、无需改 `.gitignore`（该目录整目录已忽略，且 `.jsonl` 后缀过滤不会误吸）。
- **§7 顺序（已照做）**：① 先在未改动代码上写确定性失败用例
  `tests/trainer/test_batch_eval.py::test_batch_ledger_publish_is_atomic` 并确认**红**（实测`读者读到了 [0] 批`）；
  ② 只做让它的最小改动（一个函数体）；③ 新用例绿 + 同关注点 30 例绿 + 全部门禁绿。
  ★ **确定性是刻意设计的**：不靠线程时序、不碰墙钟 —— 在 live 文件被以 `'w'` 打开的**那一刻**读一次台账。
  写方若原地截断，此刻读到残局；写方若临时文件发布，则 live 文件根本不会被打开来写 ⇒ 一次也读不到中间态。
  （并发探针只能当**证据**，不能当回归用例。）
- **被否决的备选**：① 保留 `write_text` + 加 `fsync`（不改截断语义，窗口照旧）；② 随机/PID 后缀的临时名
  （残留会累积，需配 `finally` 清理，反而多一个删除失败面）；③ 改成 append-only 台账（改字节格式、
  动 TS 双侧契约，属真设计改动）。
- **明确不改**：`batches.jsonl` 的字节格式与写者唯一性 · 锁仍复用 `train.loop_util` ·
  其余五个写点的落盘策略（那属 B2 的事务化）。
- **同族未修（另开一刀）**：`dashboard/src/evalboard/batches.ts::rewriteBatches`（TS 侧 `claimPending` /
  `updateBatch`）也是截断式 `writeFileSync` —— 要动 `dashboard/**` 且与 P1「触发端不得写台账」的守卫相邻，
  值得自己一刀。
- **记账**：`rl/batch_eval.py` **1785 → 1805 行**（+20 = `write_batches` 的 docstring；§2026-09-25-goalnn-batch-store-interface
  里的 1785 是修前读数，已在 `plan/nn-training-refactor.md` §5.5 标注基准顺移）；`tests/trainer/test_batch_eval.py` 838 → 880。
- **门禁**：nn **2566 → 2567 passed / 3 skipped**（+1 = 新用例）；ruff / mypy 绿；
  根 `bun run check` 2120 pass / 0 fail（121404 expect，与改动前逐字一致）。

## §2026-09-25-goalnn-batch-store-b2-split（2026-09-25，B2：台账/请求面收进唯一所有者 `BatchStore`）

**决定：`rl/batch_eval.py` 拆它的第二步 B2 —— 台账与两个请求文件收进新模块 `rl/batch_store.py`
的 `BatchStore`**（`status` / `units` / `node_dist` 的每次变更 = 一个**具名转移** = 一次事务 =
一次落盘；`_claim_guard` / `_claim_locked` → `BatchStore._tx`；`_persist_of` 并入 `set_units_of`）。
全文（刀口 / 两条★特例 / 三套验证 / 反探针收获 / 记账）→ `docs/nn/engineering.md` §28「第二十六刀」。

- **刀口 = 按状态所有者切，不是按链切**（与前五刀不同类）：台账有 8 个独立 read-modify-write 点，
  每个自己决定「改哪些字段 / 何时落盘 / 状态怎么转」（六种落盘策略、两种在没变时也整文件重写）⇒
  任何一条调用链都要横穿它们，**按链切无解**（plan §5.5.1 已量）；真因是**台账没有所有者**。
  两条**语法级**契约守卫：`status` / `node_dist` 的赋值点闭集 = 五个具名转移，落盘写点闭集 = `_publish`。
- **★ 两条刻意保留的特例语义**（集中化时最容易被「统一」掉，守卫正面钉）：① `units.of == 0`（未定型）
  时 `mark_unit_done` **不判 done**（否则判决批在 `set_units_of` 前就被标 done，剩余 unit 永远跑不到）；
  ② `aborted` 批的在途 unit 只回填 `node_dist`、**不复活**，`requeue` **不改** `aborted`。
- **★ 实施时才浮出的坑（设计未预见，已回写 plan §5.5.7）**：锁若只放在单个具名转移上，
  `consume_requests` 分不清「锁忙」与「去重跳过」两种 `None` ⇒ 把请求标成已消费却**没建批**（丢请求、
  无日志）⇒ 整轮也拿一把 `_tx`（内层转移可重入，不多付文件锁）；回归钉子
  `test_lock_busy_consumes_nothing_and_marks_nothing`。落盘统一成「改了才落盘」（byte 等价）。
- **验证（三套独立证据）**：① 纯搬对账 **9/9**（AST 去 docstring 后逐字等价，`read_batches` 一处
  **宣告差异** = 字面量提成常量）；② **★ 差分探针 54/54** —— 同一串操作分别打在旧实现
  （`git show HEAD:` 的 `batch_eval.py`）与新 store 上，逐操作比台账/`requests.done` 规范化字节与返回值；
  ③ 新守卫 `tests/trainer/test_batch_store_txn.py`（15 例，含结构契约与「不缓存台账」）。
- **★ 反探针 22/22**（首轮 1 条存活 = **真守卫空档**：删「running ∧ 未完成 ⇒ 可认领」分支全绿 ——
  既有用例那条断言走的是 `pending` 分支，孤儿批续跑路径无人覆盖；补用例后全红）。
- **★ 顺手记下、本刀不改的既存缺陷**：`running ∧ of == 0` 的批永远不可认领（崩在「认领 → `set_units_of`」
  窗口里就只能等 `abort`）；但把 `of == 0` 算 incomplete 会让**另一个进程在派发前抢走整批**（双派）——
  两个方向都有代价，属真设计问题（差分探针已证与旧实现逐字相同），记在守卫注释与工程记里，**不静默改**。
- **零迁移依据**：门面 14 条公开名自别名再导出 ⇒ 调用点一行不改且 `batch_eval.X is batch_store.X`；
  三个私有 seam（`_persist_of` / `_requeue` / `_reopen_for_resume`）不再转发，两处测试调用点改到 store。
- **被否决的备选**：① 删掉旧签名、改全部测试调用点（30+ 处，与 §5.5.6「一行不改」冲突，且把行为改动
  混进搬家）；② store 做成单例/带内存缓存（会吃掉跨进程写）；③ 省掉 `consume_requests` 的外层锁
  （就是上面那条丢请求的坑）。
- **记账/门禁**：`batch_eval.py` **1616 → 1190** · `batch_store.py` **680** · nn **2586 → 2608 passed / 3 skipped**
  （ruff + mypy 绿）· 根 `bun run check` 2120 / 0 · dashboard typecheck + 1105 / 0 · `check-decisions` ok。
  **下一步 = B3**（执行器纯搬 → `rl/batch_runner.py`）。

## §2026-09-25-goalnn-batch-plan-b1-split（2026-09-25，B1：批语料规划 + 判据/门 纯搬出包）

**决定：`rl/batch_eval.py` 拆它的第一步 B1 —— 23 个成员（13 函数 + 10 常量）纯搬到新模块
`rl/batch_plan.py`**（1 常量 `DEFAULT_DATA_ROOT` 留给旧家，由再导出的 `REPO_ROOT` 派生）。
全文（成员清单 / 宿主的选法 / 守卫演进 / 被否决备选 / 记账）→ `docs/nn/engineering.md` §28「第二十五刀」。

- **为什么先 B1**：§2026-09-25-goalnn-batch-store-interface 的 B1–B4 里，B2（台账事务化）是真设计
  改动、B3/B4 依赖 B2 的接口；B1 是**零锁零台账**的纯函数面（只读 `ladder.json` / `corpora.json` /
  课程关卡文件）⇒ 可单独验证/提交/回滚，且把 B2 的改动面削到「台账 + 执行器」。
- **零迁移依据 = 门面再导出（`X as X`，21 条）**：既有 `from rl.batch_eval import plan_units` 等调用点
  （含 `dashboard/src/evalboard/kick-once.py`）一行不改，且 `batch_eval.X is batch_plan.X`；
  两个私有名（`_forces_of` / `_KIND_CHAR`）刻意不再导出。
- **唯一演进的守卫**：`tests/common/test_dist_common_poll.py` 写死了「同名定义只许住 `dist_common.py` /
  `batch_eval.py` + 读 `batch_eval.py` 文本」⇒ 改为**按定义搜家**：非 `dist_common` 的同名定义恰好一个
  且必须是纯转发，且 `rl.batch_eval` 拿到的就是那一份（对象级 `is`）。
- **★ 反探针掀出的真漏洞**：首版 21 条变异 **4 条存活**（四个镜像常量改值）—— 守卫**拿常量比自己**
  ⇒ 改常量同时改掉断言两边。修法 = 加一条**字面量**断言（这四个常量与
  `dashboard/src/evalboard/{runner,store}.ts` 双侧镜像，值本身就是契约）。**测试绿 ≠ 篡改会红。**
- **验证**：纯搬对账 **23/23 搬走 + 36/36 留下的逐字节等**（对 `git show HEAD:`）· 闭集 59 = 23 + 36 ·
  再导出 21/21 对象恒等 · 反探针 **21/21 全红**。
- **记账/门禁**：`batch_eval.py` **1805 → 1616** · `batch_plan.py` **271** · 守卫 **19 例**；
  nn **2567 → 2586 passed / 3 skipped**（ruff + mypy 绿）；根 2120 / 0；dashboard typecheck + 1105 / 0；
  `check-decisions` ok。**下一步 = B2**（`rl/batch_store.py`：8 个写点 → 具名转移 + `dirty` 才落盘）。

## §2026-09-25-goalnn-batch-runner-phase-split（2026-09-25，B5a：`_run` 按**相位**切三段，机器体逐字节不动）

**决定：`BatchEvalRunner._run`（821 行）按相位拆成 `_open_unit` / `_run_channels` / `_settle_unit` /
`_log_provenance`，`_run` 退成 6 行相位叙述；机器体（615 行）**逐字节不动**，只多一个显式取值段；
新增 `_UnitPlan`（29 字段）作为开头的**对外契约**。** 全文 → `docs/nn/engineering.md` §28；设计 → `plan/nn-training-refactor.md` §5.6。

- **侦察定量（决定刀口）**：`_run` 外层赋值 65 个名字、58 个被机器段读 ⇒ **212 处引用**，其中
  **139 在 11 个闭包内**；**零 `nonlocal`**（状态靠可变容器绕过闭包只读）⇒ 「按调用图切」只能得到
  615 行的连通分量。**病根与 B2 同型：不是没有链，是状态没有所有者。**
- **切法 = 相位**（谁的失败模式是什么、谁对台账负责）：开头（短路）/ 机器（并发与背压）/ 收尾
  （**对台账的唯一交代**）+ 参与度账。`_UnitPlan` **只收真要用到的 29 个名字** —— 开头内部的中间量
  （`policy_cfg` / `window` / `god` / `iter_base` / `pairs` / `done_before` / `snapshot_path`）**不出界**。
- **验证**：逐段字节对账（`tmp/verify_b5.py` 对 `git show HEAD:`：A 119 · S 615 · D_head 34 ·
  D_tail 20 · PROV 31 行原样且各一次）+ **8 条恒等映射取值**（`x = plan.x`，写错名字不会静默换值）+
  契约闭合（字段 == 返回实参 == 取值名）+ 反探针 **14/14**，sha256 无漂移。
- **★ 收益 = 可测性**：`_settle_unit` / `_log_provenance` 现在**直接可调** ⇒ 台账交代（全结算 ⇒
  `mark_unit_done` + `node_dist`；部分 ⇒ `reopen_for_resume`）、「任何失败只记日志绝不抛出」、
  两条响亮告警、两处短路全部成为单测（`tests/trainer/test_batch_runner_phases.py` 10 例）。
- **守卫演进 2 处**：import 闭集 +`typing`；`test_batch_plan_split` 入边归属者改名
  （`_run.bringup` → `_run_channels.bringup` 等）—— 又一次「搬成员 = 改归属者名字」。
- **遗留 B5b（本刀不做）**：`_run_channels` 仍 677 行；状态对象化是**改写**（212 处引用 + 40 处
  `self.` 改回指）⇒ 字节对账不成立，主证据须换成「AST 变换证明 + 差分探针」，已写进 plan §5.6.3。
- **记账/门禁**：`batch_runner.py` **1031 → 1238 行** · nn **2633 → 2643 passed / 3 skipped** ·
  根 `bun run check` 2120 / 0。

## §2026-09-25-goalnn-batch-unrunnable-filter-loud-requeue（2026-09-25，B4 发现的既存缺陷：无可跑 unit 的批不再静默卡死）

**决定：`maybe_dispatch_batch` 在「规划成功但 `select_next_unit` 无待跑 unit」时，从**静默 `return None`**
改为**记日志 + `store.requeue`**。** 依据是**代码自身的先例**（§6.2 优先级第三条）——三条兄弟路径
（模式不适 / 规划失败 / nn 缺权重）全部是「记日志 + 退回队列」，只有这一条不一致。全文 → `docs/nn/engineering.md` §28。

- **病根（可验证）**：该批留在 `running`，而 `claim` 的可跑判据是 `pending ∨ (running ∧ of>0 ∧ len(done)<of)`，
  而 `of` 建批时已默认 **2**（`enqueue` 写 `units: {of: 2, done: []}`）⇒ **每个 idle 窗都被再认领一次，
  永不发车、不落一行日志、状态永远 `running`**。
- **可达条件**：`only_rungs` 没有一个命中本次 plan（ladder.json 在入队与派发之间被改过 / 只剩已跑完的 rung；
  判决批的 rung 名是 `语料id#关卡名`，与 ladder rung id 不同域）。`backfill.ts` 与 console 都从**同一个 rung**
  推 `ladder_pos`，所以正常路径不触发 ⇒ 属窄可达、但后果是「静默永卡」。
- **被否决的备选**：① **只记日志、不改状态**（可观测性有了，但永不发车 + 每窗空转仍在）；
  ② **改标 `aborted`**（终止且可见，但 abort 在本仓一直是**人的动作**，让 runner 自动中止用户请求的批
  是新的权力，得单独设计：谁有权中止、console 怎么展示、能不能重入）。两条都不是本刀能隐式决定的 ⇒ 取先例。
- **代价（写明不是没想到）**：批变成 `pending` 后会**每窗重新 plan 一次**（claim → plan → 过滤空 → requeue），
  与模式不适那条路径**同形**；但多了日志 ⇒ 可见、可排查（此前是「进度看着正常」的静默）。
- **§7 三步**：① 先在未改动代码上写确定性失败用例 `tests/trainer/test_batch_eval_facade.py::test_a_batch_whose_filter_matches_no_unit_is_not_left_silent`
  并确认**红**（`assert 'running' == 'pending'`）→ ② 只加「记日志 + requeue」一个分支 → ③ 新用例绿 + 门禁绿。
- **记账/门禁**：nn **2632 → 2633 passed / 3 skipped** · 根 `bun run check` 2120 / 0（121404 expect）·
  反探针 **15/15 全红**（新增 ⑭：把该分支改回静默 return）。

## §2026-09-25-goalnn-batch-eval-facade-b4（2026-09-25，B4：门面收尾 —— 把「门面是门面」变成机器可判的契约）

**决定：`rl/batch_eval.py` 的第四步 B4 收尾 —— ① 新守卫 `tests/trainer/test_batch_eval_facade.py` 把门面形态钉成契约
（模块级 `def` 闭集 = {`maybe_dispatch_batch`} · 自别名再导出表闭集且对象恒等 · 刻意不转发的名字响亮
AttributeError · 门面不改写 store 交回的台账 dict）；② 删掉旧实现残留的就地改台账 `batch.setdefault("units", {})["of"]`；
③ 散落的指路注释合并成 docstring 一张表。** 全文 → `docs/nn/engineering.md` §28「第二十八刀」。**

- **1. 删除行为等价的旧残留**：那行是 B2 之前「就地改台账再落盘」的第二半（同行还有 `_persist_of(root, …)`）。
  B2 之后它**无任何读者** —— `store.claim()` 交回的是认领时的台账快照，执行器只读 `batch_id` / `iter`
  （`of` 走参数 `unit_of`）⇒ 删后行为等价。删了它，「门面不是第二写者」才能成为可断言的事。
- **2. 不转发的名字是**契约**而不是例外**：执行器五常量 + `_heartbeat` · store 文件名常量 · 三个台账私有 seam ——
  在门面上必须**响亮** AttributeError（转发会造成「名字还在、没人读」的静默空操作，S16/S19 同款）。
- **3. 补上一条真空白**：`maybe_dispatch_batch` 此前**没有任何直接单测**（只被轮内间接覆盖）——
  补主线（认领→规划→定型→起线程）+ 三条 requeue 路径 + 判决批权重取 unit。
- **★ 反探针的第三种归因：断言无区分力**。首轮 13/14：把判决批权重改成 `rl_path` 优先**存活**，
  因为用例传的是 `rl_path=None`（`None or w` 与 `w or None` 同结果）⇒ **不是探针打偏，是用例太弱**
  （S26「先怀疑探针」、S27「守卫搜索方式太松」之后的第三种成因）。改成故意传另一份 `rl_path` 后 14/14。
- **记一笔不改的既存缺陷**：`select_next_unit` 过滤后为空（`only_rungs` 无一命中）时门面 `return None`
  而不 requeue，而 `claim` 看 `of>0 ∧ len(done)<of` ⇒ 该批**每窗被再认领、永不发车、无日志、永远 running**；
  与三条兄弟路径（模式不适 / 规划失败 / 缺权重）不一致 —— 另开一刀，不在本刀静默改
  （→ 已按 §7 落地，见 §2026-09-25-goalnn-batch-unrunnable-filter-loud-requeue）。
- **记账/门禁**：`batch_eval.py` **225 → 166** · nn **2621 → 2632 passed / 3 skipped**（ruff + mypy 绿）·
  根 `bun run check` 2120 / 0（121404 expect）· 反探针 **14/14 全红**，还原 sha256 无漂移。
- **B1–B4 到此完整收官**（plan §5.5.4）；原 1785 行巨团的三个面各自成家，门面零迁移。**余下真实工作 = B5**
  （`_run` 的 821 行按阶段切），按 §5.5.4 另开一轮。

## §2026-09-25-goalnn-batch-runner-b3-split（2026-09-25，B3：执行面纯搬出包 `rl/batch_runner.py`）

**决定：`rl/batch_eval.py` 拆分的第三步 B3 —— `BatchEvalRunner` + `dispatch_batch_bg`（连同它独占的
五个常量与 `_heartbeat`，共 8 个成员）纯搬到新模块 `rl/batch_runner.py`；五个常量与 `_heartbeat`
不建门面转发。** 全文 → `docs/nn/engineering.md` §28「第二十七刀」。

- **本刀题眼 = 注入点改址**：执行器的依赖注入靠**模块全局**（`bun_version` / `log` /
  `run_local_eval_game`），搬家后 `monkeypatch.setattr("rl.batch_eval.X")` 不再生效（门面也 import `log`
  自用 ⇒ 名字还在、没人在读 = **静默空操作**，S16/S19 记过两次的同款坑）。**改址 5 处 setattr +
  两处按路径读源码的守卫**（`test_batch_eval_wver.py` 的 `SRC` · `test_eval_loot_fields.py` 的文件清单）。
  又：**五个常量与 `_heartbeat` 刻意不转发** ⇒ 打在旧家会**响亮** AttributeError，而不是静默空操作。
- **守卫演进**：`test_batch_plan_split.py` 的入边闭集原本只数旧家 ⇒ 搬走后静默退化成「2/6」
  ⇒ 改成 `_inbound_calls()` 把**门面 + 执行器两个宿主**合并计数（用例名同步去掉 `in_the_old_home`）。
- **★ 反探针掀出的真守卫空档（第二个同族案例）**：17 条变异 1 条存活 —— 把
  `wver = sha256(...).hexdigest()` 改成 `[:16]` 时 `test_batch_eval_wver.py` 全绿，因为既有断言是
  **子串**匹配（`"wver = hashlib.sha256(weights_bytes).hexdigest()" in src`，后面多 `[:16]` 照样命中）
  ⇒ 补 `test_wver_is_never_derived_from_a_slice()`（AST：`wver` 的右值不得含切片/下标），
  守住 2026-09-19 那场事故的**上游形态**（此前只守下游实参）。
- **验证**：纯搬对账 **8/8 逐字节等**（`BatchEvalRunner` 896 行 · `dispatch_batch_bg` 36 · `_heartbeat` 8 ·
  五个常量含注释行）+ 成员并集守恒 · 新守卫 `tests/trainer/test_batch_runner_split.py`（**12 例**：结构 6 /
  注入点契约 3 / 功能性 3）· 反探针 **17/17 全红**，sha256 无漂移。
- **记账/门禁**：`batch_eval.py` **1190 → 223**（−967）· `batch_runner.py` **1031** ·
  nn **2608 → 2621 passed / 3 skipped**（ruff + mypy 绿，444 源文件）· 根 `bun run check` 2120 / 0
  （121404 expect）· dashboard typecheck + 1105 / 0 · `check-decisions` ok。
- **B4 因此不再是独立一步**：门面已自然退成目标形态（常量 + `maybe_dispatch_batch` + 再导出，**223 行**）；
  `maybe_dispatch_batch` 留门面的理由不变（轮内接线，`test_batch_eval.py:82` 按源码树读它）。
  **余下真实工作 = B5**（`_run` 的 821 行按阶段切），按 §5.5.4 另开一轮。

## §2026-09-25-goalnn-batch-lanes-objectify（2026-09-25，B5b：`_run_channels` 的通道机器收进 `_UnitLanes` —— 状态对象化）

**决定：把 677 行机器体按**状态所有者**收进新类 `_UnitLanes`（★ 住在**同一模块**
`rl/batch_runner.py`，不另开模块）：契约字段 29 → `self.*`（`__init__` 从 `_UnitPlan` 逐名接）、
状态块 23 → `__init__`、11 个闭包 → 11 个方法、主循环 → `run()`；`BatchEvalRunner._run_channels`
退成 1 条转发。**对台账的唯一交代仍在 `_settle_unit`**（机器零 `store.`）。**
全文（段落对账 / 迁移映射 / 守卫演进 / 反探针 / 记账）→ `docs/nn/engineering.md` §28「第三十一刀」。

- **★ 偏离 plan §5.6.3（必须记）：目标形态原写「新模块 `rl/batch_lanes.py`」，实际**不换模块**。**
  理由 = 机器体的依赖注入全靠**本模块全局**（实测 20 个：`log` / `run_local_eval_game` /
  `is_transient_error` / `node_gate_reason` / `_record_agent_meta` / `pick_race_target` /
  `register_inflight` / `pop_inflight` / `clear_inflight` / `dist_common` / `deque` / `threading` /
  `time` / `json` / `BUSY_BACKOFF_CAP_SEC` / `STUCK_GRACE_SEC` / `EVAL_TASK_ATTEMPTS` /
  `eval_loot_fields` / `eval_census_fields` / `_UnitPlan`）。换模块 ⇒ 每一条
  `monkeypatch.setattr("rl.batch_runner.X", …)` 都会变成**静默空操作**（S16/S19/S27 记过三次的
  同款坑；B3 的五个常量「刻意不转发」正是为了让它响亮 AttributeError）。**被否决的备选**：
  ① 新模块 + 把 20 个全局改成显式注入（构造函数传 20 个依赖 ⇒ 契约退化成「什么都依赖」，且
  每个测试都要跟着改 monkeypatch 目标）；② 保留闭包但显式传状态（与对象化同量改写却没换来
  「一个方法一个判据」）。**本类不独立成模块**这条写进了类的 docstring。
- **病根同 B2**：不是「没有链」而是「状态没有所有者」——外层 65 个名字里 58 个被机器段读
  （212 处引用、139 处在闭包内），**零 `nonlocal`**，状态靠可变容器（`seen` / `settled: [0]` /
  `lanes`）绕开闭包只读限制 ⇒ 按调用图切只能得到 677 行连通分量。
- **★ 验证：字节对账不成立，换成「AST 规范化等价」（对全输入成立，比逐场景差分更强）。**
  `tmp/verify_b5b.py` **不使用生成器**（独立机制）：① 段落守恒 —— 旧方法体 48 条顶层语句被
  「前置取值 8 + 状态块 23 + 10 个顶层 def + 主循环 6 + 收尾 1」**恰好覆盖一次**；
  ② 规范化 = `self.owner.X` → `self.X`、`self.X`（X ∈ 被搬的 63 个名字）→ `X`、并抹掉
  `AnnAssign.simple`（裸名改属性必 1→0，是变换本身而非差异）⇒ 逐节点等：状态块 23/23 ·
  11 个闭包**签名 +self、体逐节点等** · `run()` = 主循环 6 + 收尾 1；③ 注入点 §：从**旧**体内
  **导出**它读到的 20 个模块全局，逐个断言在新类里仍是**裸名**（且没有 `self.<全局名>`）；
  ④ 注释守恒：80 条注释全在，**宣告删除 1 条**（前置取值段那句「显式取值 ⇒ 下面逐字保留、
  不改成 plan.x」本刀正是改写它 ⇒ 已过期，换成新注释）。
  **差分探针（plan 原要求的第 2 条）以「跨版本行为用例同结果」替代并说明**：本刀改动的测试文件
  **只有 4 个**（3 处结构守卫按设计演进 + 1 个新守卫），其余用例在 HEAD 与新工作区**逐字节相同**
  ⇒ 它们在两版上全部通过（HEAD 2643 / 新 2653）即是「同 fixture ⇒ 同可观测行为」的差分；
  其中 `tests/trainer/test_eval_dispatch_resilience.py`（89 例里的主力）是**真跑 `dispatch_eval_round`**
  （假节点 + 真台账 + 真 jsonl）的端到端场景（瞬断 502 / 熔断 / 丢局 / 结算）。
- **守卫演进 3 处（同一族第四次）**：`test_batch_runner_phases` 的契约段从「`_run_channels` 开头
  元组解包」改到「`_UnitLanes.__init__` 逐名 `self.X = plan.X`」+ 「每个字段都必须真被读」；
  `test_batch_plan_split` 的入边归属者**换类**（`BatchEvalRunner._run_channels.worker` →
  `_UnitLanes.worker`、`...bringup` → `_UnitLanes.bringup`）；`test_batch_runner_split` 的常量读取
  改成**两个类合并扫**（`BUSY_BACKOFF_CAP_SEC` / `STUCK_GRACE_SEC` 随机器搬走 ⇒ 只扫旧类会
  静默退化成 3/5）。
- **★ 反探针 21/21 全红**（无存活），sha256 无漂移；首轮 5 条「不唯一/零命中」的**是探针锚点问题**
  （锚点在文件里出现 2–4 次，或缩进写错）⇒ 修探针而不是改守卫 —— 与 S26「存活先怀疑探针」同源：
  **探针自己也会错，且「锚点不唯一」必须显式报错而不是静默跳过**。
- **★ 新获得的可测性**（与 B5a 同型）：`lane_state` / `mark_tripped` / `window_open` 现在**直接可调**
  —— 归一化（`authKey` → `key`，2026-09-19 的真事故）· 幂等（同 nid 同一通道对象）· 掉线计数与
  「`max_recovery_tries` 才判死」· 槽位不得减成负数 · 关窗优先于墙钟；共 10 例新守卫
  `tests/trainer/test_batch_lanes_split.py`（另有结构契约 6 例：类定义唯一 · 方法名闭集 13 ·
  **无嵌套 def**（闭包升平）· `_run_channels` 只剩转发 · **实例属性面 == 闭集 64** · 机器零台账）。
- **记账/门禁**：`batch_runner.py` **1238 → 1247**（类 675 行 / 13 方法；`_run_channels` 677 → 4）·
  nn **2643 → 2653 passed / 3 skipped**（ruff + mypy 绿，447 源文件）· 根 `bun run check`
  **2120 / 0**（121404 expect）· 反探针 **21/21 全红**。
- **B5a + B5b 至此收官**：`_run` 的 821 行 = 相位 6 + 开头 158 + 机器（类 675 / 13 方法）+
  收尾 87 + 账 47。**余下 = 本轮无**；若还要继续，走 §5.6.4 的候选（`worker` 180 行内的
  「背压 / 竞速 / 结算」三段）。
## §2026-09-24-goalnn-train-mode-hot-switch（2026-09-24，plan/train-mode-hot-switch.plan.md 主体 L1 + L3.2）

**「在线/离线」收敛成唯一那颗开关**：`setCourseMode` ① 先落本机训练配置（唯一写面
`actions/train-mode.ts::applyTrainModeToConfig`，域换算仍走 `stack/specs.ts::trainModeKnobs`）
② 再推 hub 镜像。用户 2026-09-24 报障「离线课切回在线后 Kaggle 仍因缺 bun 拒单」的根因就是
只翻了后一半：`courses.<课>.rollout_src=run` 没撤 ⇒ 下一段照样派 `kind=run`。
**写面边界**：`pushCourseMode`（只推 hub + 落意图，开课/停课/回灌共用）绝不写配置；`setCourseMode`
（那颗开关）写；`restoreCourseModes`（回灌）绝不写 —— `pushHubMode` 本来就是循环调 `setCourseMode`
的，不拆会让**开课重复写盘 1–3 次**、并把**停课**误翻成「整段上云」。
**L3.2**：worker 在**零下载**处（结果复用块之后、payload/code/ts_code 之前）做 bun 能力自检
（`kind in (iter, run)` ∧ 非 echo）——现状是 3.42MB / 12.1s 全白传才 `REJECTED`；异常类型与
重试策略不变（复用既有的 `ProtocolError` ⇒ 确定性拒绝链）。
**生效时机**：段边界，不是「下一轮」——`run_iters<0` 时一段覆盖到课程末，训练侧阻塞在 8h 段等待里
⇒ 已在飞的段不抢占（**明确不做**），回执/文档写明最坏情形并指向「停课/暂停」。
**node 往返**：`offline` 覆写 `rollout_src` 会吞掉显式选的 `node` ⇒ 切离线前记进
`console-state.courseRolloutSrc`（只记 `node`/`auto`），切回在线时恢复。
**第三源可见性**：`modeDrift` 增 `configRun`（`stateView.courseRolloutSrc` 逐课下发）——
「hub 与意图都在线、配置仍是 run」这种半状态在**非当前课程**的行上也看得见。
**被否决**：① 让 `course-mode.ts` import `course-lifecycle.ts`（反向 import 成环）⇒ 新开
`train-mode.ts`；② 把配置写入放进 `pushHubMode`/`setCourseMode` 共用体（= 开课重复写盘 + 停课误译）；
③ 自检放在 `normalize_manifest` 紧后面（会把「已算完、只差重传」的 job 按 bun 拒掉）；
④ 把 `opts.rolloutSrc` 也过一遍 `trainModeKnobs`（`run` 会被换成本机 `local` = 行为漂移）。
**违反后果**：再出现「开关只翻半场」⇒ 用户报障原地复发；「离线」被当成「只停派发」（真正含义是
**整段上云**）⇒ 运维以为自己停派发了而本机仍在本机采样。
—— 全文（背景 / 事实链 / 设计 / 判据）→ `docs/nn/console.md §15` + `docs/nn/remote-transport.md §37`（零下载自检）

## §2026-09-24-goalnn-x20-metrics-v8（2026-09-24，plan/x20-dodge-avoidance.plan.md §2）

给 metrics 向量永久追加危险暴露四列 `idx41–44`：`playerHpRatio`（hp/maxHp，clamp01，与 obs s19 同源）·
`dangerTicks`（累计 hpRatio<0.4 的 tick）· `threatTicks`（累计「在敌方弹道/炮口线上」的 tick）·
`dmgFirst600`（tick<600 累计承伤）；`METRICS_DIM 41→45`、`METRICS_VERSION 7→8`。**只加观测，不进任何现存公式**
（reward golden 64/64 逐位不变、oracle phi 逐位不变已验证）。
**口径冻结**：`threatTicks` = 同轴 ±0.75 格 + ≤6 格 +（弹：逼近 / 车：炮口朝玩家），常数取自
`src/nn/dodge-l0.ts`（同一反应半径）；**不判墙体遮挡**（c20 阶梯关是全空场，audit §7.3）。
**lockstep 七处**（TS 行构造+常量 / eval 侧导出器 / eval-course-ckpt 逐局行+汇总 / Python METRICS+版本+行数断言 /
`DEFAULT_RANGES` / golden / 测试）漏一处即静默错读（§2 的 P0 先例）。
**被否决**：只算弹不算炮口线 · 判遮挡 · 用 `dmgFirst300` · **不 bump 版本只加列** · 把时长型计数器直接入公式。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-x20-metrics-v8`

## §2026-09-24-goalnn-halt-crash-vs-conservative（2026-09-24，plan/x20-dodge-avoidance.plan.md §4.1）

x20 闪避系列的熔断分两类：① **崩溃类**（`mean < 6.19` / `timeout > 5%` / `kl` 连 3 轮 ≥ 0.075）走
`gate-halt-mode=notify`（**只记录不自动停**）+ 人工盯盘；② **保守陷阱类**（`cellsVisited`/`move%`/`pass`
任一显著下降）**保留杀腿权**（立即停腿，不是「记录并继续」—— 与 `x20-noexplore.jsonc:36` 及 audit 通用护栏一致）。
理由：A 腿被「变保守」杀死的形态是本系列最大失败模式；代价（`experiments.md:56` 的 notify 放行形态、
`demo-mix` 300 轮白烧同源）用户已知并接受。**回收条件**：崩溃类漏停造成 ≥10 轮白烧 ⇒ 恢复 `halt` 并另起条目。
—— 全文（背景 / 备选与否决 / 代价 / 回收条件）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-halt-crash-vs-conservative`

## §2026-09-24-job-identity-collision（2026-09-24，plan/job-identity-collision.plan.md）

**job 身份纳入课程维度，且归属唯一才认**。2026-09-24 用户报障「双课程并行，调度请求 409」，
根因是 `idempotency_key = (runId, it, init_weights_fp, data_fp)` **不含课程**：单 hub 多课程
（`--serve` 共享 trainer）下 `runId` 进程级共享、`data_fp` 只哈希 shard 元组（per-stage seed 与课程无关）、
`init_weights_fp` 同 warm-start、`it` 同轮 ⇒ 两门课发布出**同一个 `job_id`**；而 `course_of` 的
「取第一个匹配」在**读（`wait_job`→`/result`）写（claim/result）两条路径**上都生效 ⇒ 两个训练轮
读到**同一份结果**、各自落进自己的 `args.out`（静默污染 + 自持循环：污染让 `init_weights_fp`
持续相同 ⇒ 下一轮继续撞）。
**L1**：`idempotency_key = (runId, course_fp, it, init_weights_fp, data_fp)`。选 `course_fp`（jsonc 字节
sha256）而非课程名/hub 课程键 —— 它是 manifest 必填字段，且**同进程内恒定**（课程字节装载时冻结，
`args.course_frozen_bytes`）⇒ mid-run 编辑不换 id、不产生孤儿。**只做键分量，不做路由键**。
**L1'**：`course_of` 归属**唯一才认**（≥2 门课都持有 ⇒ `None`/404 + 一行歧义日志 + `/admin/queue`
的 `ambiguous_jids`），歧义**绝不**进 `_locate_cache`。原「取第一个匹配」正是事故的静默通道；
代价是「响亮失败」（404/等到超时），收益是「绝不污染」—— 方向刻意如此。
**L3**：发布端守卫 `protocol.collision_rows`，判据 = 「**完整幂等键**相同 且 落在**别的 store**」
⇒ 拒发，位置在 `job_id` 算完之后、**任何写盘之前**（连 `.extra_tmp` 都不建）。它是哨兵（正常发布
撞不出来），拦手写 manifest / 回灌历史 job / 跨 hub 搬目录。
**L4**：409 不再被 worker 读成「hub 异常」；worker 打 `reason`+jid，hub 侧对每次 claim/result 拒绝
留一行（`(jid,status)` 60s 节流）。
**被否决**：① 给 17 个 job 作用域端点加 `course` 形参的逐端点迁移（L1 后 id 已按 store 唯一、
L1' 已堵死静默通道 ⇒ 不值得半套迁移的第二事实源风险；重开条件 = 出现新碰撞源或需同 id 跨课程共存）；
② 守卫判据用「四分量相同且 `course_fp` 不同」（那正是本事故的配置，L1 之后**合法**，会拒掉已修好的
场景）；③ 用 hub 课程键（目录名/stem）当键分量（要新 manifest 字段 + 第二个派生点，违反判据唯一）；
④ `course_fp` 纳入 `data_fp`（改所有 `data_fp` ⇒ 波及 D12 验收/离线包，另起 plan）；
⑤ `job_seed` 纳入课程（换数值，与修复分开裁）。
**违反后果**：再出现「两门课共享一个 job 身份」⇒ 权重互串且**静默**（三重校验天然通过）；
把归属歧义「猜一个」⇒ 直接回到本事故的通道。
—— 全文（现场五条实测 / 三层决定 / 判据表 / E0 成本 / 迁移）→ `docs/nn/remote-transport.md §38`
## §2026-09-24-goalnn-opt-blob-diet（2026-09-24，plan/opt-blob-diet.plan.md）

回传的 `opt` tar 从 `model.pt + opt.pt` 缩成**只 `opt.pt`**；初始权重改走内容寻址的 **`init` blob**
（`BLOB_NAMES` 加 `init`，`sha = manifest.init_weights_fp` —— **复用既有字段，manifest 字段集不变**）。
上行 760,658 → ~491,662 B/轮（x20 it176 口径 **−35.3%**），稳态下行零变化；同一份权重**一个方向只传一遍**。
**可判据的不变量是差值**：去掉 tar 里 `model.pt` 成员 = 本机实测 **−262,790 B（−25.4%）**
（绝对值随 `opt.pt` 可压缩性漂，§40 有口径警告）。
worker 侧五源：payload → `blob_cache` → preloaded → `GET ?name=init` → tar 里旧形状 `model.pt`；
四源皆尽 ⇒ `ProtocolError`（**永不**静默 warm-start），瞬时 ⇒ `RetryableError`；哨兵
`init_weights_fp ∈ {"", "bc"}`（BC job / 全新 run 首轮）⇒ **零 blob 请求**，节点侧索取 `init`
还要过 `manifest.slim` 闸。**稳态零下行依赖 `run_job` 产物段把 `weights.json` 原始字节也写进
`blob_cache`**（与 opt tar 缓存同一手法；缺它每轮净亏 110 KB）。
**被否决**：另写 init 下载路径 · hub 重打 tar · 「同一 worker」当命中判据 · 新增 `init_sha` 字段 ·
保留 `model.pt` 让旧 worker 不炸 · 给 `_resolve_blob` 加 `cache` 开关。
**升级顺序**：停 worker → 升 hub → 升 worker（禁半套回退；混跑时旧 hub + 换机必然响亮失败）。
—— 全文（背景 / 实测量 / 五源 / 顺序 / 落地差异 / 判据 / 未决）→ `docs/nn/remote-transport.md` §40 · 锚 `## §40`

## §2026-09-24-goalnn-offline-local-first（2026-09-24，plan/offline-rerun-local-first.plan.md）

**重跑离线 cell 时，起点、计划、代码一律以「机器上已经跑过的那份产物」为准**。决策与实现都落在
`remote/offline_boot.py`（notebook 每次会话从 GitHub raw 刷新它），**不碰 `run_loop`/`bundle` 的导入语义**。
判据（`local_artifacts`）：产物目录三件齐全（`state.json` + `plan.json` + `manifest.json`）⇒ **本机优先**：
`run_loop` 的 argv **不带 `--bundle`**（`--artifacts` 单用），包只当代码/TS 的**备源**；取包改走
`obtain_pack(optional=True)`（只试一次 hub、取不到返回 `None`、不弹上传框、**不 `SystemExit`**）⇒ 本机有
进度时不再为取包白等 30 分钟。`CFG.force_pack=true` 或显式 `CFG.task_zip` = 显式老行为（包覆盖计划/清单）。
有 `state.json` 而缺计划/清单 ⇒ **响亮拒**（让包补齐会重置本机 state 并**重跑**已跑过的 `it-NNN`）。
代码/TS「跟着产物走」：`<dest>/code.zip` → 包 → hub `/code` 逐候选用 **manifest 的 sha** 选中，全部对不上
⇒ 拒（不写 `CODE_DIR`）；选中字节 ≠ 现盘 `<dest>/code.zip` 时**用同 sha 副本修复**（`run_loop` 读的是那份）。
**为什么决策不住在 `run_loop`/`bundle`**：那两份来自**代码快照**（= 本机优先要保护的那份，可能很旧），
新参数传给旧快照要么 argparse 崩、要么静默退化 ⇒ 跨层 version skew。**不变式**：同一进程里住着
① raw 刷新的 `offline_boot.py` 与 ② 产物 `code.zip` 的 `remote.*`，跨这条边界只允许走两条腿都认的东西
（argv/`--artifacts`），不得新增只有新代码认识的参数。
**§8 附则（云机重启 + 包必须跟课程状态走）**：`GET /offline/task-pack` 递包前判
`sha256(tmp/<课>/weights.json)` 是否等于包内 `init_weights.json` 的 sha（导出本来就是 `args.out` 的原字节，
判据可满足）；过期 ⇒ 触发控制台 action `exportTaskBundle`（控制台是唯一打包者）+ **409**（含节流 600s、
连续触发上界 2）；到上界仍过期 ⇒ **照发旧包 + 告警**（不把云机 brick 到 deadline，起点仍由 resume 锚点兜）；
控制台不可达/只读门控 ⇒ 降级 409+指引；**判不了（索引不可读/课程无权重）⇒ 照发**。
**被否决**：`bundle.preserve_existing` + `safe_extract_zip(skip_existing)` + `run_loop --force-pack`
（跨层 version skew；逐件对账读的是**落点**，保留件必然与包索引不符 ⇒ 第一次真实重跑就 `ProtocolError`；
`skip_existing` 会填补 `code.zip`/`it-N/weights.json` ⇒ 包代码 + 本机 manifest 混血，报错还指向"传输损坏"）·
hub 自己重打包（造第二份打包逻辑）· 进度停滞时自动清空 `dest` 重开（**绝不**静默重跑）。
**违反后果**：按包覆盖计划 ⇒ 从旧起点重跑几十上百轮（白烧算力，本条目要修的就是它）；
把进度停滞当成"没什么可做" ⇒ 人看不出下一步是清目录还是等新包。
—— 全文（现状七环 / 判定表 / 评审 F1–F8 处置 / §8 判据与上界）→ `docs/nn/remote-transport.md` §41 · 锚 `## §41`

## §2026-09-24-offline-it0-baseline（2026-09-24，plan/offline-it0-baseline-eval.plan.md）

**离线腿（`rollout_src:'run'`）的 it0 读数由控制台在「离线开课」那一刻补派一次**：`launchEvalA(course, '', 0,
{baseline:true})` → `eval_a_once.py --baseline`，权重缺省取课程活动权重 `out`（= 任务包 `init_weights` 同一份
字节 = 段起点 W(0)），`iter` 恒 0，同 wver 的 it0 summary 已落账即早退；**云机侧不动**（`offline_eval.due()`
保持 `it<1 → False`）。理由：控制台的配对基线取不到 it0 时会退化成「首条 eval 轮」（`iters.ts:1034`），随 run
起点漂移 ⇒ 跨腿失去共同锚。**被否决**：云机侧评 it0 · 控制台加「补跑」按钮 · 包内 `init_weights` 自动对账。
**违反后果**：缺 it0 ⇒ 配对基线漂移（跨腿不可比）；baseline 的 `iter` 写成非 0 ⇒ 被当成那一轮的读数；
幂等判据用 `baseline_summary_landed(课程目录, …)` ⇒ 恒 False、每次开课白评一轮。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-24-offline-it0-baseline`（变更记录 §42）

## §2026-09-25-goalnn-offline-switch-auto-bundle（2026-09-25，plan/offline-switch-auto-bundle.plan.md）

**「切离线」这颗开关必须顺手把任务包导出来；包真没有时 hub 替离线课推一次重导；云机侧一门课取不到包
就跳过继续下一门（末尾汇总、全跳过非零退出）**。用户报障 2026-09-25：在线课切成离线后云机 404 白等 28 分钟
才由一句 `SystemExit` 告诉人，第二门课一行没跑。四层同改：① 控制台 `setCourseMode` 在 hub 推送之后跑纯函数
`autoBundleDecision`（八条规则：非离线 / hub 未接受 / 逃生阀 / 导出忙 / 缺起点权重 / **盘上已有包** ⇒ 不导，
有包**不作废**；缺包且可导 ⇒ 起导出；**任何结局都不改 `ok`**）；② 云机 404 日志经 `_http_error_parts`
（HTTPError 的 fp **只能读一次**）打出 hub 的 `path`/`known_courses`/触发回执；③ hub 缺包自愈门
（另一本账 + 上界 3 + 节流 600s，包出现即清零；**候选面 = 盘上的事实**：表里明确 online 才排除，
否则看开课标记）；④ `PackUnavailableError` ⇒ 跳过继续下一门 + 汇总，配置错误/训练失败照旧立即停。
**被否决**：让 hub 自己打包（控制台是唯一打包者）·「有包也重导」（作废旧包 ⇒ 云机在窗口期探到 404）·
按包内 `commit` 与 HEAD 不符判过期（新鲜度只看 `init_weights` sha，比对另开工单）。
**违反后果**：热切离线仍不出包 ⇒ 云机白等满 `wait_pack_sec`（用户花 28 分钟才拿到一行日志）；
缺包路径没有自愈 ⇒ 排障人要自己猜是该重导还是课程名写错；把「全被跳过」与「队列本来就空」合并 ⇒
没活干时响亮失败（两条判据来源不同，见 plan/offline-task-discovery §3.3）。
—— 全文（S1–S6 落码清单 / 评审 F1–F9 + S-1 + X1–X3 处置 / 实测）→ `docs/nn/remote-transport.md` §43 · 锚 `## §43`

## §2026-09-25-goalnn-offline-task-discovery（2026-09-25，plan/offline-task-discovery.plan.md）

**离线云机不再在 notebook 里写死课程名**：新增只读清单 `GET /offline/tasks`（课程 + 包 + 新鲜度 +
持有者 + 段内进度；`?include=all` 才带 `not_offline`）+ 硬租约三端点
`POST /offline/{claim,heartbeat,release}`（TTL 900s + 60s 心跳、**惰性过期**、**409 而非 403**、
`release` 不覆盖别人的）。**候选面 = 课程表 ∪ 盘上的开课标记**（离线课本机不训练 ⇒ 冷掉后从
1 小时新鲜窗里消失而包还在盘上）。`worker_id` 持久化在 `<work>/.worker-id` ⇒ 同一台机器重跑
判 `mine` 直接续领（否则被自己留下的租约挡到过期，Kaggle 上等于废掉整个会话）。
云机侧 `CFG.course` 退化为**可选覆盖**：填了 = 老行为且**一次都不问清单**；留空 = 问清单 → 领租约 →
取包 → 跑完 → 交还，`served[包 sha]` 防自激（同段重跑 = 回传全判 `duplicate`）；`queue_mode`
缺省 `drain`，受 `session_budget_sec`（**新键**，与逐段 `budget_sec` 是两把旋钮）/`idle_wait_sec`/
停机信号限制；老 hub 只探测一次就降级。**边界**：点名要跑的课取不到包 = 异常（跳过继续、全跳过
响亮失败）；**队列空 = 正常收工（rc=0）**。**被否决**：软占位（两台同跑白烧一张卡，第二份回传被
静默丢弃）· 只靠 `(run_id,it)` 幂等不要租约 · 403 表达租约不匹配（会重演「worker 把它读成
ProtocolError ⇒ 停腿」）· 租约参与回传。**违反后果**：清单只认课程表 ⇒ 冷课永远等不到云机；
租约不绑定持久 worker id ⇒ 中断重跑白白等满 TTL；把「清单空」与「全被跳过」合并 ⇒ 没活干时响亮失败。
—— 全文（规则表 / 兼容矩阵 / 评审 G1–G7 + S-1 + X1–X3 处置）→ `docs/nn/remote-transport.md` §44 · 锚 `## §44`

## §2026-09-25-goalnn-role-routing（2026-09-25，plan/online-offline-role-routing.plan.md）

**「该由哪个盘执行」成为 job 的不变式**：`manifest.role ∈ {offline, online}` 在**发布时**定死
（`publish_job`，`kind` 的同一快照：`run ⇒ offline`；`iter/ppo/bc ⇒ online`；`MANIFEST_KINDS`/`KIND_ROLES`
共用一份全集 + 穷举用例）。**闸下沉到 `_JobStore._claim_locked`**（租约写入的唯一临界区）⇒ 四条认领腿
（`claim_next` / `peek`+`claim` / 按 id 直领 / **push 派发**）天然同源；判据函数只有一份
`role_blocked(job_id, role) -> "" | "parked" | "role"`（派发面用它过滤候选、临界区用它拒绝）。
**两道正交的闸**：归属闸（job 级，`manifest.role`）+ **停摆闸**（课程级，`mode=offline` 且请求方不是
离线盘——2026-09-20 的既有语义，**保留**：离线课的活留给切回在线，`pending_n` 不降）。
worker 侧**复用既有载体**（`CFG["offline_worker"] → --offline → X-Battle-Offline: 1`，头名与取值逐字节不变，
只把语义从「能力」升为「归属」），且 **peek 与 claim 两跳都带**（旧代码里 claim 从不带这条头）；
取包端点 `_get_task_pack` 补 mode 闸（只在「表里有它且明确 online」时 409；冷课/未扫到的课放行）；
`/admin/queue` 每行加 `roles: {jid: role}`（**不**报 `claimable` 布尔——它是相对请求方角色的属性）。
**行为变更**：带 `X-Battle-Offline` 的盘**不再兼领在线盘的活**（一个盘一种任务，用户 2026-09-25 裁决）
——这**supersede `§2026-09-19` 的能力语义**（原 `protocol.py`「带标 worker 仍可领在线课」）：`kind=run`
整段**确实**由在线盘跑得动（审计 §3）⇒ 能力闸拦不住「有能力的盘接走不属于它的活」，判据必须是角色。
顺带修：`rl/cli.py --rollout-src` 的默认从 `_d("rollout_src", …)` 改为字面量 `"auto"`（旧写法把顶层
`rl.rollout_src` 读成 argparse 默认值 ⇒ `_rollout_source` 早返回 ⇒ **课程级配置被整个忽略**；顶层仍在兜底链里）。
**被否决**：新增独立 `X-Battle-Role` 头 / `--role` 参数（= 第 4 个模式载体，且旧 worker 不带新头会静默掉线）·
`role` 进 `MANIFEST_OPTIONAL_DEFAULTS`（静态默认值 = 第二个事实源）· `NN_ROLE_ROUTING=0` 回退开关
（把「一个盘一种任务」变成**可选**，正好在最需要它的混部期复现事故；回滚面 = 笔记本开关 + 本提交同批）·
只在各调用点分别加闸（push 腿必然漏——它就是本轮的第一个洞）· 归属缓存永不失效（重发覆盖 manifest 即谎报；
改为 `publish` 里 pop）。**违反后果**：归属写在调用点 ⇒ 新加一条腿就静默绕过闸；归属读 mode ⇒ 每切一次模式
历史 job 跳一次（本轮事故：两小时前缺 bun 被拒的 `kind=run` job 被另一块盘领走）；claim 不带头 ⇒
带标 worker 自锁（peek 说能领、claim 当场拒）；取包端点不查 mode ⇒ 离线盘能取走在线课的包跑整段（L6）。
—— 全文（症状 / 三条根因 / 落地表 / 被否决策 / 真机验证点）→ `docs/nn/remote-transport.md` §47 · 锚 `## §47`

## §2026-09-25-goalnn-cloud-cpu-ledger（2026-09-25，云机 rollout 卡死取证）

**云机上的「rollout 卡死」= 两条 CPU 腿同时开满 + 池的回退放大**（不是导出器坏了、也不是局真的
卡住：超时局 `rc=<仍在运行>`、尾行只到 `[features] native 启用`，是**活着的慢局**）。三处落地：
① `run_loop._maybe_cloud_eval` 提交后**有界等**本轮云机评估收线（`EVAL_ALTERNATE_WAIT_SEC=300`）
——「rollout 与 eval 交替跑、互不预留」从注释假设变成**代码保证**（旧版的提交点恰好落在下一轮
rollout 的开跑瞬间 ⇒ 两条腿同时各开满一份 ⇒ 成批踩 5s 硬顶）；
② `serve_pool` 加**熔断**（累计回退 ≥ `max(4, workers//4)` ⇒ 余下局改一次性、**不再补位/重建
worker**）+ 补位就绪等待受**本次尝试硬顶**约束 + 回退行**带 kind/label/where**且只留前 5 条详情
（「一次超时 = 三份进程」是「越回退越慢」的正反馈，必须有刹车）；
③ **核数按物理数目**：`platform_utils.effective_cores()` = min(cgroup 配额, 亲和掩码) 优先，
`os.cpu_count()` 只当最后的兜底 —— 容器里它报的是**宿主机**核数（Kaggle 实测 224，而 cgroup
只给 **96**），所以日志里那个 `workers=220` 本身就是 2.3× 超订的产物（不是「这台机器有 224 核」），
两条腿各开满时真实超订是 4.6×；
④ `iter_rollout` 按该核数夹取 hub 给的并发（旧状态只有 `MAX_WORKERS=256` 一道闸；云机
220→92），夹取进轮末日志、`NN_ROLLOUT_WORKERS_MAX=0` 可关；
⑤ **「本机几核」只允许一个答案**（同日收尾）：口径铺到所有**定并行度**的入口 —— TS 侧新增
`tools/lib/cores.ts`（`cgroupCpuQuota` + `affinityCores`（`/proc/self/status` 的 `Cpus_allowed_list`，
Node/Bun 无 `sched_getaffinity`）+ `hostLogicalCores` 兜底，逐条镜像 `effective_cores`），消费方 =
`worker-pool.physicalCores`（⇒ `eval-course-ckpt`/`export-*`/`sim-pool`/`m1-eval` 的默认 worker 数）、
`sampler-agent` 的 `CPUS`（默认 `workers` **与**上报的 `cpus`）、`perf-cmp-rollout` 的核探测、
`dashboard/src/core/venv.ts` 的 torch 线程数；python/脚本侧补 `rl/cli.py --workers`、
`nn-python-gate.sh` 的 `-n`、两个 notebook 的并发单元格。**夹取只降不升** ⇒ 裸机/dev 机逐位不变
（本机 16 核读数不变）。dashboard 因此多消费一个仓根模块 ⇒ 已同步 `.github/workflows/dashboard.yml`
的 paths（`dashboard/tests/ci-scope.test.ts` 钉这条边）。
**被否决**：抬高 5s 单局硬顶（用户口径「>5s 肯定不正常」，而本案的慢局正是被超订挤慢的，
抬高只会让它更久停在超订态）· 给 eval 预留核数（2026-09-22 已否；按物理数目定池后 rollout
就吃掉 96 核的大部分，预留等于停掉云机评估——真交替能同时满足两边）· 评估挪到另台机
（没有这条设施，且语料/口径必须与 in-loop 同一份）· 继续用 `os.cpu_count()` 当核数（容器里
它是宿主机的数字，不反映配额——本轮的 220 就是从它算出来的）。
**违反后果**：两条腿同时开满 ⇒ 每轮成批超时并触发回退放大（现场：整轮 4 分钟 `[run]` 零行）·
池无熔断 ⇒ 一个环境抖动就能把节点推入「回退越多、进程越多、越慢」的死循环 · 节点不夹取 ⇒
小核数节点被 hub 的导出机规模打爆 · 回退行不带 kind ⇒ 一屏同形行，分不出哪条腿（本轮排查的
第一道坎）· 只改一处核数读数 ⇒ python 按 96 算、TS 工具/脚本按 224 算，同一次跑里两个口径
（`eval-course-ckpt` 的默认 worker、agent 上报的 `cpus`、gate 的 `-n` 都还在按宿主机数排）。
—— 全文（现场读数 / 三条根因 / 落地表 / 下次真机该看的四个读数）→ `docs/nn/runtime-opt.md` §23 · 锚 `## §23`

## §2026-09-25-goalnn-offline-leg-retired（2026-09-25，plan/online-offline-role-routing.plan.md §7）

**「离线 = 本机发一份 `kind=run` 队列项、随后等 8h」这条腿退役** —— 离线场景**没有「一整段」这个
中间概念**（用户 2026-09-25 口径）：云机接手一门课就一直跑，直到 ① 跑完课程 ② 云机配额用尽
③ 人工停机/停课，**能传回来多少是多少**。离线课的唯一载体是**任务包**（`--export-bundle` →
云机 `battle.offline.ipynb`：`/offline/tasks` 清单 → 取包 → 跑完逐轮回传 → 控制台导入产物）。
落地（P5，逐条都在代码里）：

1. **砍在发布点，不砍配置**：发布端（发一份带段长的队列项那个方法）删除，
   `RUN_WAIT_DEFAULT_SEC` / `_run_wait_sec` / `--run-wait-sec` / `rl.run_wait_sec` 一并退役（**没有
   8h 白等这回事了**）。配置里的 `rollout_src=run` + `run_iters` **保留**——它是「这门课由云机
   接手」的既有声明，也是导出腿的终点口径（删字段会让 `resolve_collect_mode` 落回 `COLLECT_LOCAL`
   ⇒ **本机偷偷自己采样、与云机双跑**）。
2. **本机循环遇离线课干净收官**：`resolve_collect_mode` 的 `COLLECT_SEGMENT` → `COLLECT_OFFLINE`
   （来源 `run` **或**写了终点值 ⇒ 离线，**绝不**回落本机采样）；`step_course_iter` 打一行指路
   （`battle.offline.ipynb`）后返回 `ROUND_OFFLINE_EXIT`：不采样、不发队列项、不等待、不进账本
   （不是失败：不落 `iter_error`、不计连击）。该课的权重/读数由**产物导入**推进本机账本。
3. **无消费者改成当场报错**：`_remote_ppo_publish` 是 `kind=run` 的唯一咽喉 —— 带 `plan_bytes`
   而不带 `export_path` ⇒ `SystemExit`，消息指路取包链与本机该走的那一步（任务包）。
   worker 侧对 `kind=run` **响亮拒收**（最前，零指令零下载）：不是「当成 iter 跑一轮」——半跑会
   产出权重、让控制面看着像在推进，而 hub 侧早就不等了（那是最难查的静默分叉）。
4. **离线盘报名**（R3：离线盘在 hub 眼里是匿名的）：`offline_boot` 的每个 hub 调用都带
   `X-Battle-Offline: 1`（与 job 腿共用同一份归属判据；本模块不 import `remote.*`，两份字面量由
   测试守逐字相同），hub 在 `/offline/*` 前缀记一次 ⇒ `/admin/queue.offline_disk` 报
   `recent`/`last_seen_ago` + **`stale_jobs`**（还挂着的离线待领项 = 盘上遗留 / 手写参数 /
   混部期旧 hub）+ `hint`。这是「本环境有没有离线盘」**唯一**的报到面（此前恒为空）。
5. **保留的两条能力（用户点名，一个字节都不改）**：① 离线云机**串行跑多门课**（清单 + `drain`
   驻守 + 租约 + 预算/空闲上限）；② **notebook 不写课程名**（`requested_courses` 空 ⇒ 清单发现）。
   所以「报名」只能是**这块盘的身份**，不许做成「每门课一个开关」。

**被否决**：① 取向 2「保留这条腿、只给离线盘」——`role=offline` 的消费者**当时并不存在**（全仓只有
测试设过 `CFG["offline_worker"]`），要成立就得新造并部署一个，而它的活已被取包链完整覆盖
⇒ 一个任务两个执行者，正是 2026-09-25 云机接错盘事故的结构；② 在 `setCourseMode` 里**删配置字段**
来关掉这条腿（见落地 1：会静默退回本机采样、与云机双跑）；③ 只做「熔断」（单独用 = 每次切离线先
白等一轮，且不解决「两个执行者」的结构）；④ 把 `remote/run_loop.py::run_plan_job` 一并删掉
（它无生产调用者了，但它是「从首轮结果续下去」的唯一入口，`tests/remote/test_run_loop.py` 的 4 组段语义
回归挂在它上面——删它等于把这些回归一起删）；⑤ 加回退开关（`NN_ROLE_ROUTING=0` 那类）——
会把「一个盘一种任务」变成可选，而它正是这次事故的判据。

**违反后果**：留着旧腿 ⇒ 队列项无人能领、本机白等 8h，而日志一切正常（队列不降、没人报错）·
在配置里删字段 ⇒ 本机与云机**双跑**同一门课（两份权重、账本互相污染）· 只在测试里改断言而不动
生产枚举 ⇒ 第 5 条认领/发布路径静默绕过归属闸 · 把「报名」做成每门课的开关 ⇒ 用户点名的两条能力
（多课程串行 / 不写课程名）当场作废。
—— 全文（口径、R1–R4、残留清单、DoD）→ `plan/online-offline-role-routing.plan.md` §7 ·
档案 `docs/nn/remote-transport.md` §48 · 回归 `nn-training/tests/trainer/test_offline_leg_retired.py`

---

## §2026-09-25-goalnn-online-worker-no-bun（2026-09-25，plan/online-offline-role-routing.plan.md §9）

> 前身：同日 16:39 曾立过一条 `…-cloudflared-notebook-retired`（「把 cloudflared 盘退役」）——
> **那条是误判，已于当日全量撤回**（notebook 已恢复原状）。留下这个错形状，防止再犯。

**裁决（两块盘的分工与 bun）**：在线 worker 的两块盘 —— `battle.tailscale.ipynb`（**tailnet**）与
`battle.cloudflared.ipynb`（**cloudflared 公网隧道**），服务**不同的云机网络环境**，**都保留**；
且**都不跑 rollout** ⇒ 两块盘**不装 bun、也不传 ts 代码**。
bun 只属于**自己跑 rollout 的链**：`battle.offline.ipynb`（Kaggle/TPU，Kaggle 不给 tailnet ⇒ 只能走
cloudflared 隧道进 hub，它自己跑 rollout）与采样节点 `rollout.cloudflared.ipynb`。

**拦截的错误形状（两条，都得记住）**：
① **退役**：把「能力归属」（谁跑 rollout）当成「接入方式」（怎么连到 hub）来判 —— 能力重叠 ≠ 入口冗余；
公网隧道的机器进不了 tailnet，那块盘就是它**唯一**的路。
② **补装 bun**：把「在线腿收到 kind=iter 却被拒单」当成「盘少了 bun」去修（§46 那修法）——
真正的错是**不该给不跑 rollout 的盘派 kind=iter**，而不是让它们也装上 bun。

**落地**：`remote/tailscale_boot.py` 的 `ensure()` 不再调 `ensure_bun`；`BUN_INSTALL_URL` /
`bun_path()` / `ensure_bun()` 三处**退役**（无消费者）；`remote/iter_rollout.py::resolve_bun` 的拒单
消息改成「这份活派错了盘」+ 指路；`tests/remote/test_tailscale_boot_bun.py` 重写成三条边界的守卫
（在线盘 4 份源码 + 2 个 ipynb 零 bun 安装 / 退役符号不许回来 / 跑 rollout 的盘**必顶还有** bun）。
`kind=ppo` 本来就不碰 bun 与 ts_code（零下载自检与 `_ensure_ts_code` 都只在 `kind == "iter"` 分支），
所以「不传 ts 代码」这一半不需要新代码 —— 守卫用例把它钉住了。

**被否决**：① 退役 cloudflared 盘（见上 ①）；② 给 cloudflared 线也补装 bun（见上 ②）；
③ bun 改由 hub 随包下发 / 发布 `bun build --compile` 产物（本仓无此流水线）—— 两条都是为「本不该发生的
派单」修路；④ 「派单前按能力筛节点」（L3.1）—— 本次不做（另题），但拒单消息已带指路。

**违反后果**：把 bun 安装加回在线引导链 ⇒ 两块盘重新背上「公网 curl + 必须在改代理之前」的顺序
硬约束，而这件根本不需要发生；把「不跑 rollout」的盘派上 kind=iter ⇒ 每单零下载空转（安静）。

**回归**：`nn-training/tests/remote/test_tailscale_boot_bun.py`（5 条）· `tests/remote/test_ts_offline_install.py`（去桩）。
—— 全文（分工表、实施表、DoD、误判留档）→ `plan/online-offline-role-routing.plan.md` §9 ·
档案 `docs/nn/remote-transport.md` §49

## §2026-09-25-evala-baseline-bc（2026-09-25，it0 基线污染修复）

**背景**：x20-dodge-l1/L3 的 `eval_log` it0 各混入 200 局后期权重读数（L1 混 it43、L3 混 it51），
基线 low 被带偏 6pp（L1 it0 34.0 真值应为 40.0）。门控 verdict 不受影响（独立段），日常分析已更正。

**根因（三段链）**：① `rl/eval_a_once.py --baseline` 缺省取 live `out`
（`tmp/<课>/weights.json`，每轮被训练覆盖）；② 幂等早退判据是 `iter=0 ∧ 同 wver`，
权重变了就不命中；③ 离线开课/重启自动补派基线（`shouldAutoBaseline`）⇒ 补派瞬间读到
新权重，照跑并写进 it0 槽。触发两次：L1 在 10:42 重启（it43 当时最新）、L3 在 15:45
重启（it51 当时最新）。下游放大：`kickstart-receipt` 取最后一条 it0 行当基线，控制台
基线显示同步被带偏。

**变更**：`--baseline` 缺省改取课程 `bc`（起点冻结权重，归档文件永不变）；`resolve_eval_ckpt`
抽成单一事实来源函数；bc 缺席响亮拒（不拿 out 顶）；日志/TS 注释同步改口。
`--ckpt` 显式永远优先；wver 幂等保留（bc 换了会自然重评）。

**被否决**：① **iter=0 槽写过一次就永久锁死** —— 杀掉合法场景（课程 bc 换了要重评基线）；
② **补派时传归档 ckpt 快照** —— 调用方（控制台开课）不知道归档布局，知识放错了地方，
权重归属是训练侧课程文件的事实，就该 python 侧从课程文件取。

**回归**：`nn-training/tests/trainer/test_eval_a_once.py`（bc 缺省 ×2：取 bc 断言 + 重启后早退不写别轮
wver；旧钉死 live-out 的用例已按新语义改写）+ `test_baseline_eval.py` 全绿。
—— 全文即本条 · 档案 `docs/nn/engineering.md` §25

## §2026-09-25-goalnn-rollout-reap-wedge（2026-09-25，云机 rollout 二次卡死取证）

**现场**：battle.offline.ipynb × x20-dodge-l3（96 核配额）。§23 的三件套都已生效（`workers=92`、
健康轮 336 局 3.29s、熔断会响），但两次各损失 15–24 分钟：开跑 5s 内 ≥23 局踩硬顶 ⇒ 熔断 ⇒
随后 `进度=271/336 games settled (910s)` 一行不动（890s 里零结算），两条同批回退局的重试行
在 910s 那一刻才出现，且写着「上次：rollout 单局超时（5.0s > 硬顶 5s）」。

**根因**：`p.kill(); p.wait()` **没有上限**。超时行里的 elapsed 是 kill **之前**算的（所以照旧
写 5.0s），而 890s 花在 kill 之后的**回收**上——子进程卡在不可中断的 IO（D 状态）时 SIGKILL 要
等系统调用返回才生效。一条线程不返回 ⇒ 整轮收不齐（后面的 65 局连开始都开始不了）。
**放大器**：熔断（§23）把余下每一局推去**一次性冷启动**，而手上 70 个**暖** worker 空转——
那正是它要掐掉的正反馈；「不再补位」被实现成了「余下局全走一次性」。

**变更**：① `platform_utils` 新增 `popen_own_group`（POSIX 进程组）/ `kill_process_tree`（SIGKILL
整组、不等回收）/ `reap_bounded`（有界回收）/ `keep_unreaped`+`sweep_unreaped`（收不了尸的记账）
与 `KILL_REAP_SEC=5.0`（全仓唯一那个数）；② 两条 CPU 腿（`iter_rollout` 的逐局、`eval_local` 的
抓取）都改成进程组 + **有界**回收；③ 回收不了 ⇒ **`UnreapableChildError`**（`RetryableError`
子类）：本局**不就地重跑**（同一 `w{i}/` 上再起一个写者 = 半截/交错 shard = 静默错数据）；
④ 熔断语义收窄为 **只停补位**（暖 worker 继续服务、一个都不新建）；⑤ `game_watch.STALL_WARN_SEC=120`
+ `stall_line`：整轮停滞时点名（进度行只在「有局结算」时才打 ⇒ 全卡住时它们一起哑，这就是
890s 零行的成因）；⑥ **重投放在轮内**（用户口径「失败就重试，不许关云机让任务失败，也不许空转
烧配额」）：`run_iter_rollout` 自己接住 `UnreapableChildError`，**只补没产出的那几局**（已结算的
局不重跑，先 `_clean_attempt` 清掉半截产出），不睡、不报失败、不消耗调用方重试预算 ⇒ 训练腿与
云机都不受影响；重投次数缺省**不限**（`ENV_ROUND_RETRY_MAX` 是操作员退出阀，缺省 0=不限）；
**为什么不能扔给外层**：外层每条腿预算极小（云机自主段 `run_loop._run_with_retries` 只有 3 次）
⇒「一台机器短管卡住」会直接把整段训练判死；⑦ `worker_loop` 保留同名分支作为回落：
能上抛到那里的（操作员设了上限 / 非 rollout 腿）只做「还租约 + 立即重领」，不睡、不报失败（hub
只对**租约过期**计毒包，主动 release 不算 ⇒ 反复重投不会把自己冻死）。

**被否决**：收不了尸就**原地重跑这一局**（静默错数据的入口）· 熔断=停用整池（旧实现，等于在最挤的
时刻把每一局都换成冷启动）· 抬高 5s 硬顶（§23.3 已否，且本条的慢局是被系统事件挤慢的）·
给「回收不了」加固定等待/固定重试（把机器问题记在内容头上，且那个等待又是无上限的风险）·
**退避/冷却（试过 30s，用户当天否决）**：云机按分钟计费，机器卡死的那 15 分钟里睡掉的分钟
就是白烧的配额；而要的东西（别把整轮反复重投打在被卡住的机器上）由「**只补没产出的那几局**」
回答 —— 重投不再是「336 局重跑一遍」，而是接着把它没做完的那几局做完（既不空转也不浪费）·
把重投扔给调用方重试预算（3 次即判死整段训练）· 拿 `FREEZE_AFTER_RECLAIMS` 当刹车（主动 release
刻意不计数，否则诚实重试会被冻成毒包）· 拆掉 `_clean_attempt` 的全树 rglob（与 `scan_shard_dirs`
同口径，收窄 = 半截 shard 会被当成产出）。

**违反后果**：任何一处裸 `wait()`/`communicate()`（无上限）⇒ 一条线程就能把整轮按住且**日志看不出
来**（elapsed 是 kill 之前算的）· 熔断写成「余下局全走一次性」⇒ 卡死时反而最大化进程 churn ·
进度/心跳只挂在「有局结算」上 ⇒ 全卡住时零日志，operator 只能看着机器卡死 ·
把「机器级停滞」当成一轮失败扔出去 ⇒ 3 次就把整段训练判死（云机白烧）· 重投时**重跑整轮** ⇒
机器卡 15 分钟就白烧 15 分钟的重复活（要的是「只补没产出的那几局」）。
—— 全文（现场读数 / 根因 / 落地表 / 否决项 / 下次该看的四个读数）→ `docs/nn/runtime-opt.md` §24 · 锚 `## §24`

## §2026-09-25-goalnn-eval-round-retry（2026-09-25，eval 腿对齐机器级停滞口径）

**现场/来历**：`§2026-09-25-goalnn-rollout-reap-wedge` 把 rollout 腿的「机器级停滞」改成轮内重投，
但**eval 腿只改了一半**：`rl/eval_local.run_eval_runner_capture` 已经用进程组 + 有界回收（收不了尸
就 `keep_unreaped`），可它把那一态**当成普通超时上抛** ⇒ `run_cloud_eval.run_one` 的通用分支把它当
内容失败：原地重跑 3 次、失败就记一轮 `failed` ⇒ 这一轮**永远缺一个读数**（且没有任何重订机会：
评估腿的调用方 `CloudEvalRunner` / 云机自主段不重试，rollout 腿的 3 次预算也不盖它）。

**变更**：① `UnreapableChildError` 的**定义**上移到 `remote/protocol.py`（与 `RetryableError` 同册：
它是**两腿共用**的分类语义），`remote/iter_rollout.py` 只 import 它当模块属性（worker.py / 用例
零改动）；② `run_eval_runner_capture` 在「SIGKILL 之后 `KILL_REAP_SEC` 内连输出都收不回来」时抛它
（普通超时照旧 `TimeoutExpired` —— 分类就住在抛出点上）；③ `run_cloud_eval` 加**轮循环**：
`except UnreapableChildError` ⇒ 本局**不原地重跑**（同一 `eval-<it>-s<stage>-d<seed>/` 上可能还有
活写者 ⇒ 半截/交错的 `_eval_report.json`）、**不记 `failed`**，只把它记进 `machine_stuck`；一趟跑完
若有它的局，就用 `eval_done_keys` 算缺口**只补没评的局**（已落账的不重跑 = 不空转），投前
`rmtree_best_effort` 清半截产出，然后重投；④ 重投次数缺省**不限**（`NN_EVAL_ROUND_RETRY_MAX`
是操作员退出阀，缺省 0=不限；到上限**不上抛**——评估腿「永不抛」，抛出去连 summary 都没了——
而是把还没评的局记成本轮 `failed` 并照常收尾）；⑤ 内容面失败（rc≠0 / 落账失败）仍归 `failed`；
⑥ `out` 新增 `roundRetries`（只进日志/诊断，不进 wire），DONE 行在卡过时追加「整轮重投 N 次」。

**被否决**：收不了尸 = 普通超时（把机器的问题记在内容头上 ⇒ 每轮都缺同一批读数）· 原地重跑同一
`game_dir`（两个写者写同一份报告 = 静默错读数）· 把机器级停滞上抛给调用方（`CloudEvalRunner` 只
记一笔 WARN 就走 ⇒ 这一轮白评）· 重投时重跑整轮（机器卡 15 分钟就白烧 15 分钟重复活）· 睡/退避
（云机按分钟计费；且「只补没评的局」已经把「反复重投」的范围缩到真正没做完的那几局）。

**违反后果**：把收不了尸并进 `TimeoutExpired` ⇒ 调用方只能二选一（机器问题记成内容失败 / 内容
失败无限重投）· 用 `seen` 内存计数而非账本算缺口 ⇒ 断点续跑/落账失败时漏补或重复评 ·
在轮内重投时**不**清半截产出 ⇒ 旧写者与被杀进程的产物被当成有效读数。
—— 全文（现场/两腿口径对照/日志样例/下次该看的读数）→ `docs/nn/runtime-opt.md` §25 · 锚 `## §25` ·
同源条目 `§2026-09-25-goalnn-rollout-reap-wedge`（rollout 腿）

## §2026-09-25-state-init-course-key（2026-09-25，课程键 `state_init` 进 CourseConfig）

> _(本条的 `rebase_counters` 字段与「中段交棒」措辞已在同日被 §2026-09-25-state-init-snapshot 修订：
> 起始分布改为**快照注入**，`rebase_counters` 删除。本条只保留「课程键怎么进配置层」那部分。)_

**来历**：`curricula/x20-state-init.jsonc`（rollout 起始分布 = 人类 demo 中段起跑，plan 六腿六负
后的第七条攻击面）起草时就带着 `state_init` 块，而 `CourseConfig` 是 `extra="forbid"` ⇒ 那个文件
**长期加载失败**，连带 `tests/biz/test_reward_golden.py::test_jsonc_courses_load`（遍历 `curricula/*.jsonc`）
成为门禁唯一那条红。plan `plan/x20-state-init.plan.md` P2 = 把这个键映射进配置层。

**变更**：① `StateInitBlock`（`rl/config.py`）：`bank/cut_from/cut_to/cut_step/rotate_cuts`
全可选 + v1 缺省，`extra=forbid`，切点自相矛盾（`cut_from<0` / `cut_to>0` /
`cut_step<1`）在校验期拒；② `CourseConfig.state_init: StateInitBlock | None = None`（缺席 = 标准
开局，老课程逐字节不变）；③ `flat_overrides` 把整块转成 **dict** 交出去（不进 mapping 那张标量表）；
④ 「声明了就必须能跑」的自洽检查住 **`apply_course`（启动期）**：`bank` 空、或不在盘上（cwd /
仓库根 / nn-training 三种基准都认，`resolve_state_init_bank`）⇒ `SystemExit`（响亮，不静默退回
标准开局——那等于换了一个实验，而账本还以为跑的是中段起跑）。

**被否决**：把 bank 存在性检查放进 `load_course`（`load_course` 只读课程文件本身；在那儿读盘会让
**任何**遍历课程的调用方被一个未落地的产物打红——就是这次那条门禁红）· 把 pydantic 模型对象原样
传进 `args`（`echo_config` 对非标量做 `json.dumps` ⇒ 当场炸；P3 的 `build_rollout_cmd` 也只要
JSON 数据）· 允许 `cut_to > 0`（绝对上界：得先读银行 manifest 知道局长，v1 只支持相对局尾）·
`bank` 缺失时静默关掉 state_init（映射漏了/数据缺了都表现为「静默失效」，`ent_break` 前科）。

**违反后果**：新增课程键不映射 `flat_overrides` ⇒ 训练静默按缺省跑（`ent_break` 前科：`0.25` 从未
落地）· 在 `load_course` 里做产物存在性检查 ⇒ 起草中的课程文件把全体遍历课程的用例打红 ·
块内乱写键被静默忽略（`extra=forbid` 是这一层的唯一防线）。
—— 全文（P2 落地记录 / 银行内容校验为何留 P0-P3）→ `plan/x20-state-init.plan.md` P2

---

## §2026-09-26-goalnn-refactor-merge（2026-09-26，本地 S4 重构分支并入 origin/goal-nn）

**背景**：本地 `goal-nn`（43 个纯重构提交，S4 拆 `rl/` 神模块）与已推送的 `origin/goal-nn`
（32 个语义提交）分叉，合并基准 `8448028`，以 `--no-ff` 并入。

**裁决规则（逐 hunk 判，不整文件站队）**：① **机械改名**（`remote.protocol`→`common.protocol`、
补丁目标 `W`→`JL`/`JR`/`http_mod`）= 本地赢；② **语义更新**（角色路由 / 离线腿退役 `kind=run` /
事件驱动探针 / 任务包门禁 / it0 基线 / 离线盘去 bun）= origin 赢；③ 两侧各自新增的段落 = **都留**。
**编号撞号**按先例 `ad0f448`：**已推送一侧保号**，本地一侧改号 + 就地注（本次 `engineering.md`
§21/22/23→§26/27/28、`nn.progress.md` §146/147/148→§150/151/152）。

**被否决**：整文件 `--ours`/`--theirs` —— `--ours` 曾静默吞掉 origin 在 `course-lifecycle.ts`
的语义改动（测试绿也算），`--theirs` 会丢本地拆分。
**违反后果**：按文件而非按 hunk 站队 ⇒ 语义改动静默丢弃，或本地拆分的模块引用回退（import 当场炸）。
—— 全文（冲突清单 / 门禁证据）→ 合并 commit message
## §2026-09-25-state-init-snapshot（2026-09-25，rollout 起始分布 = 人类中段**世界快照**注入）

**来历**：六条价格腿全阴性后的第七条攻击面 = 只换 rollout 的起始**状态分布**、不动任何价格项
（plan/x20-state-init.plan.md）。首版设计是「人类**磁带**（逐 tick 输入）headless 快进 T tick
后交棒」；评审发现六个阻断级问题，其中四条全部源自「用**输入**去重建**状态**」：B2 人类 idle
（20–30% 帧）在动作空间里表达不了（`ScriptedInput` 的 move=0 = 继续走）、B5 快进段内可能先死、
B6 交棒点不在决策边界上、B1 重建出的不是人类那个状态且**无外部证据**可对。改为**直接注入状态**。

**决定（后来者极容易做错，故入册）**
① 起始分布 = `cloneWorld` 快照（`tools/sim/build-state-init-bank.ts` 产物，人类**真正到达过**的世界），
不是人类输入的产物。资产全是现成机制（`ReplayInput` + `cloneWorld`/`restoreWorld` +
`worldTickHash`），不新造格式、不新造解码器。
② **状态来自人类，未来来自本局抽到的 seed**：restore → 核 tickHash → `world.rng.reseed(seed)` →
重施 CLI/关卡权威值。故 `(stage,seed)` 种子流逐轮照旧轮换（§15.1 真满足、判决段配对干净），
而起始状态集合 = 银行 972 个切点（池小 ⇒ V 头记忆化风险，已在 plan §5 预注册读数）。
③ **不做 rebase**：奖励 = Φ 的差分，对「继承的进度」天然不付钱；计数器列保持**游戏真值**，
`initCounters`/`initSnapshot`/`initTick` additive-only 进 shard manifest 供分析侧自行 rebase。
④ **v1 只在本机采集**，执法点在**两处**：ⓐ `dispatch.RolloutDispatcher.run()` **入口的派发闸门**——
开了 `state_init` 的轮只要还剩一个可行远端节点就 `SystemExit`，否则零派发、整轮交本机腿
（`run_rollout`）；`local_slots` **不是**这个开关（它只是并发配额，主循环照旧向 pool 派发）。
ⓑ `node_side=True` 的三条 argv 路径（`build_rollout_cmd` / `iter_job.build_iter_spec` /
`plan.template_argv`）在**发布前** `SystemExit`。两处都要：老导出器**静默忽略未知 flag**，云节点
cwd = job 目录 ⇒ 「云上跑标准开局、账本写中段起跑」是最贵的错，宁可不发。
**代价（2026-09-25 实付）**：首版只有 ⓑ，而主循环走的是 ⓐ 的 volume 路（`fetch_task` 拼任务参数
**没有**快照项）⇒ 三条护栏一条没触发 = 混语料（pool 标准局）+ 缺 `initTick` 的 shard 被全剔 =
无限波次，烧掉 **51.9 万 transitions** 才发现；教训 = 护栏写在 **argv 构造点**对 volume 路天生无效，
派发模式必须在**派发入口**定。
**被否决**：把 `local_slots` 当「纯本机」开关用（它语义只是并发配额，会让配额调参意外改采集分布）·
只在 `fetch_task` 侧补 `node_side` 拒（闸门仍在上游派发之后，且节点侧调用点不止一处）。
⑤ **shard 侧护栏**：缺正整数 `initTick` 的 shard 在**六个 funnel** 一律不计入（`_scan_shards` /
`completed_pairs` / `settled_stage_totals` / `resumed_manifests` / `iter_shard_dirs` /
`verify_and_land`），判据函数单点 = `rl.resume.shard_state_init_ok`；开关只从 `args.state_init` 派生。
⑥ **起始分布进语料身份**（`corpus_identity_fp`，仅激活时，bank 只算文件名）：决定「一个样本从哪个
世界开始」与 `seed_rotate`/`mode` 同类 ⇒ 课程中途加/删/改 `state_init` 时旧 shard 在四个 funnel
全被 D14 自动排除，零额外参数。
⑦ 保真门是**外部**证据（plan §2 M4）：切点取 `hashInterval` 整数倍 ⇒ 每个快照都能与录像记录链
逐点对账（P0 已 2916/2916 全对）；采集侧每局 restore 后再自检一次，不符 = 该局响亮失败。

**被否决**：① 磁带 FF（B2 idle 不可表达 / B5 快进段内会死 / B6 边界 / 无外部证据）·
② 「用 demo 那一局的 seed 复现」（踩 §15.1「第 it 轮 (stage,seed) 不得与更早轮重复」= 重磨固定集，
且训练 seed 集与 414000 判决段本体重合）· ③ 在 TS 侧 rebase 计数器（对奖励是恒等变换，却让
`metrics.kills` 与 manifest 真值分叉）· ④ 云侧顺手做 blob 搬运（用 `node_side` 响亮拒换时间；
规格留 P2.5，届时必须同时动 `_RETARGET_FLAGS` 与 plan 自检）· ⑤ bank **内容**进语料身份（要读盘 + 
节点上不一定有该文件 ⇒ hub/节点指纹分叉，整份 job 误拒）。

**违反后果**：把 `--init-snapshot` 放上云而不做 blob 通道 ⇒ 云上跑标准开局、账本写中段起跑 ·
护栏只设在 argv 构造点、不管派发入口 ⇒ 主循环的 volume 路整条绕开（51.9 万 transitions 事故）·
在 `reseed` **之后**核 tickHash ⇒ 外部证据变成恒假/恒真 · 交棒点不在 `K` 边界 ⇒ 首个决策前 K−1
tick 由「上一帧残留动作」驱动 · 课程中途删 `state_init` 还复用旧 shard ⇒ 两种起始分布混训。
—— 全文（六条评审处置表 / P0 实测读数 / 单局 smoke 读数 / P2.5 规格）→ `plan/x20-state-init.plan.md`
§2 / §3 / §6 · 前身条目 `§2026-09-25-state-init-course-key`

## §2026-09-25-goalnn-prefetch-p0-not-preempt — 抢占权专属 P1：控制面只让路 + P2 补齐开工门禁 + 挤走不占重试预算（plan/bulk-p2-preempt-fix）

**背景**：2026-09-24 现场（x20-dodge-l1 / x20-dodge-l3 双课程 + 单云 worker）：**预取零命中**，每个 job 都刷
`定期预取被高优 bulk 挤走 → 重试次数用完，放弃这份提前量`，且**每条** payload 下载都带
`reroll=1(wasted 0.25MB)`。四条根因（全文读数与算术自证 → `docs/nn/remote-transport.md` §50）：
① `control()` 里 `if self._holding: self._preempt_at = self._holding` ⇒ **任何**控制面请求都置抢占标记，
而 job 期间常驻的取消环每 1.5s 一个 `/jobs/{id}/status` ⇒ P2 每读完一个 256KB 分片就被打断一次 ⇒
3 次 attempt 上限 ≈1.5MB < 3.4MB payload，**数学上永远传不完**；② `_control_waiting` 只写不读且没有
`_p1_waiting` ⇒ 被挤走后 P2 立刻回抢 ⇒ 关键下载排队 17.6s；③ 让路时间污染首块速率探针
（256KB/5s ≈ 51KB/s 恒低于 `WIRE_MIN_RATE=80KB/s`）⇒ 每次下载假性重抽；④ 抢占作废字节不入账
⇒ 预取成本恒等于 0。

**决定（后来者极容易做错，故入册）**
① **抢占权专属 P1**：控制面（P0）**只让 bulk 让路、永不抢占**。取消环每 1.5s 一个包是**常态**，
一个常态事件不该有权丢掉别人的半截；P0 怕的是被大 body 拖到分钟级，而那由 `pause_if_needed`
（分片间隙暂停，预算 ≤5s/次）解决。**不要再把「P0 能抢占」加回来**去换 p0 延迟（见「被否决」）。
② **P2 开工门禁**：`slot()` 的 P2 分支加两条否决 —— 此刻有 P1 在等（`_p1_waiting`）或有控制面在途
（`_control_waiting`）就不许新开工。口径是「**此刻**」而不是「最近」（取消环 1.5s 一个包，要求
「最近无控制面」= P2 永远开不了工）。**P1 不受这道门禁**（否则两个 P1 互等）。
③ **被挤走不占重试预算**：P2 的 `BulkPreemptError` 走独立预算 `WIRE_PREEMPT_MAX=6`（`_get_with_retry`
因此是**外层 while + 内层 for**，`WireSlowError` 仍在内层消耗 `attempts`）；**上限用完抛的仍是
`BulkPreemptError`**（不是 `RetryableError`）—— 挤走不是失败，`_prefetch_fill` 只吞前者。
④ **速率判据用净值**：`elapsed_net = 墙钟 − Σ(pace() 返回的让路秒)`；`total_timeout` 与进度行
**仍按墙钟**（它们回答的是「这份传了多久」，含暂停才诚实）。
⑤ **抢占作废字节入账**：`BulkPreemptError.bytes_read` → `_wire_note_preempt` → wire 行
`preempt=N(wasted X.XXMB)`（仅 N>0 打印），与日志里的「挤走」行数**恒等**（G4 的对账口径）。

**被否决**：保留 P0 抢占 + 把预取深度调小/关掉（`--prefetch-depth 0`）—— 那是**拿机制换读数**，
现场要的正是这份提前量能命中 · 取消环降频（1.5s → 更稀疏）—— 延迟判据 <20s 是硬需求，且它治不了
「P0 有抢占权」这个根因 · 只补 P2 门禁而不动抢占权（P2 仍会被每 1.5s 打断）· 抢占也做 HTTP Range
续传（双端改造，成本远高于本次收益）· 用 `time.sleep` 赌时序证明门禁（已改用 `_CountingEvent` 事件驱动）。

**违反后果**：把「P0 可抢占」加回来 ⇒ 多 MB 预取重新变成不可用（现场那屏日志一模一样）·
抢占计入 `attempts` ⇒ 三次被 P1 打断就把整份预取判死 · 耗尽时抛 `RetryableError` ⇒ 预取失败被
当成节点故障上报（违反「预取失败不是失败」）· 门禁用「最近 1.5s 内无控制面」⇒ P2 永久不开工 ·
让路算进速率 ⇒ 每次下载白扔一块（现场 100%）。
—— 全文（现场读数表 / 四条根因 / 改后语义表 / 门禁与独立预算的测试口径 / 真机复核待办）→ `docs/nn/remote-transport.md` §50 · 锚 `## §50` ·
同源条目 `§2026-09-22-goalnn-bulk-single-channel`（本条是它的更正/延伸：单通道与让路预算不变，改的是
**谁有抢占权**）

## §2026-09-25-local-rollout-serve-pool（2026-09-25，本机腿入池：trainer 自己的两条本机腿接 `--serve` 长驻池）

**来历**：用户问「local 节点的 bun 进程是 persist 的吗？如果不是，要改！」。追证结论：**不是**——
`rl/queue_local.py` 的两条本机腿（无可用节点时的整轮 `run_rollout` / dispatcher 本机槽的
`run_local_rollout`）都是**逐局 `Popen`**，而长驻池（`docs/nn/runtime-opt.md` §22）当时只接在
节点侧 rollout 与云机离线 eval 上。本机腿的局往往更短（x20-state-init 一局 ≈250 样本 vs 标准局
≈1290），固定开销占比反而更高；而 state_init 轮的唯一可跑腿正是 `run_rollout`。

**决定**：① 要不要建池由**真 argv**（`build_rollout_cmd` 为第一个 pair 拼出的那份）决定，
`make_local_pool` 只转发给 `serve_pool.make_pool` 的白名单——脚本不在名单里（goal / intent 两种
RL 模式）⇒ None，本轮与本改动前**逐字节相同**（不再抄一份「哪个模式用哪个导出器」的判断）；
② 池宽度 = 本机槽位数，生命周期 = 一轮采集 / 一次 `dispatcher.run()`；
③ 单局硬顶 `LOCAL_GAME_TIMEOUT_SEC=1800`——本机口径一直是「不限」（本机卡住的是自己的终端），
这个数只给池一个「死 worker 最终能被收掉」的兜底，**刻意不加 5s 硬顶**；
④ 回退与熔断完整复用节点侧那一份（池拿不准就地回退逐局 `Popen`，只慢不错）；
⑤ `dispatcher.run()` 拆成 `run()`（只管 try/finally 收池 + 打 `summary()`）+ `_run()`：`_run` 有多条
提前 return，池必须在**每一条**出口收掉（否则每轮留下 `local_slots` 个常驻 bun）；
⑥ e2e 层用 **autouse 夹具**关池（该层 hermetic，不许起真 bun）。

**被否决**：自建一份常驻进程池（= 第二份协议，必漂）· 把 `local_slots=0` 当「纯本机」开关用
（并发配额 ≠ 派发模式，会让配额调参改变采集分布）· 给本机腿也上节点侧那个 5s 硬顶（本机 8 并发单局
p99 已 16.8s，超订更慢 ⇒ 成批变成「kill worker + 回退一次性」，正是 `docs/nn/runtime-opt.md`
§23 的回退放大回路）·
在 `e2e/conftest.py` **模块级** `os.environ` 关池（门禁是 `pytest tests/ e2e/` 一次进程——
实测把 `tests/` 三个真 bun 池用例一并关红）。

**违反后果**：池里留跨局状态（不重建 World）⇒ 账本写「中段起跑」而样本来自别的世界 ·
池不收 ⇒ 每轮留一批常驻 bun 等 stdin · 给本机腿加 5s 硬顶 ⇒ 回退放大回路（runtime-opt §23）·
e2e 真起 bun ⇒ hermetic 层失约且门禁变慢 · 抄一份「哪个模式用哪个导出器」⇒ 新导出器入池时两边漂。

—— 全文（改了什么 / 等价性证据 / 真机 A/B 待办）→ `docs/nn/runtime-opt.md` §26 · 锚 `## §26` ·
同源条目 `§2026-09-25-goalnn-cloud-cpu-ledger`（池的熔断/背压/核数夹取定义在本条）·
`§2026-09-25-state-init-snapshot`（起始分布是池化最容易被突破的一处：restore 换世界）

## §2026-09-25-clutch-null-kill（2026-09-25，paired-kill 杀错臂 + 重启键坏，两处修）

**来历**：C-0（`x20-clutch-null`）22:08 死在 it46，账本 `gate_verdict: paired-kill 连续 2 点
< −3pp`；用户控制台重开课（22:15 marker 重写）毫无反应。两处都是代码与课程文件不一致：

- **杀错臂**：两份课程文件只授权**单向**杀（差值 < −3pp ⇒ 杀 Cw，本腿证伪）；`rl/paired_kill.py`
  实现却是**对称自杀**（Δ=本臂−对端，谁落后谁死）。C-0 落后（it40 −3.75pp / it45 −6.00pp）
  恰等于 C-w 领先 +3.75/+6.0pp——正是加权要证明的——规则却杀了对照。对照一死，终点 verdict
  （414000 配对 McNemar 要两条臂）即不可能。且 firing 在 ~2.9h，课程写的是 6h 中点、
  日常"只看趋势不判决"。
- **重启键坏**：`loop_serve.py` 重扫只认"不在 `runtimes` 里"的课；收官课永在 `runtimes` 里 ⇒
  控制台停→开（marker 重写）永远没人执行。唯一的旧路是重启整个 serve 进程（打断健康腿）。

**决定**：① `courses.<课>.paired_kill.self_kill`（rl-config 执行面，缺席/写坏=True，即现状；
  只有显式 `false` 才关）：命中时判据照算、streak 照落账，只是不停车（`loop_guards._paired_kill`
  在落账后、云端停机前return False）。C-0 经控制台设 `false`（rl-config 是 live 配置，不进库）。
  ② serve 重扫：`reopened_parked`（纯函数：队列已终 + 落过收官副作用 + marker mtime 新于入队
  记录）⇒ **只重置队列、复用原 runtime 与热引擎**（重走 open 会建 runner=None 的新 runtime，
  池里旧引擎还在，`ensure_ready` 只对引擎不对 runner ⇒ 每轮断言失败进无限 RETRY，测试抓获）+
  rounds_done 累计带过去；`_settle_rounds` 的旧文案同步改（"停→开后自动重新入队"）。
  ③ 复活顺序写死：先设开关 → 再重启 serve（新进程才读新代码）→ C-0 从 it47 指针续跑；
  streak 从账本重算（尾部仍 2/2）但开关已关 ⇒ 只记录不杀；it50 新 eval 点定去留。

**被否决**：把对称改单向改默认（已有课程行为突变；开关缺席=现状）· 复活重建 runtime/引擎
（丢 runner 无限 RETRY + 白付 torch 重建）· 重启键修成"只要 marker 在就重入队"
（分不清"一直开着"与"停→开"，正常收官的课会被反复拉起）· 单课另起进程跑 C-0
（与共享 trainer 抢同一 traj，lifecycle 明令禁止的双调度器）。

**违反后果**：对照臂被杀 ⇒ 终点配对 verdict 永不可判（McNemar 要两臂同 seed 800 局）·
收官课重开课无响应 ⇒ 唯一复活路是重启 serve（打断健康腿）· 复选用新 runtime ⇒ 无限 RETRY
幽灵轮（账本有入队、无产出）。

## §2026-09-25-state-init-routing（2026-09-25，起始分布派发：改路由不改放行名单）

**来历**：c4d42a04 的派发闸门（有可行远端节点就 `SystemExit`）把 51.9 万混语料变成了 6 秒
响亮拒，但 v1 因此构造性不可跑（self 也算节点，pool 健康时永远被拒；"只留本机"的自救指南
是错的——只留 self 照样 abort）。另一条路"放行 local 节点"错层：`fetch_task` 协议没有快照项，
放行≠给快照， self 经节点协议跑的还是标准开局。

**决定**：`state_init` 开 ⇒ 整轮直接交纯本机 `run_rollout`、向 pool 零派发，跟节点健康度无关
（`dispatch.run()` 入口，替代旧闸门；旧测试①按新契约改写）。additive-only 照旧。

**被否决**：按"本地与否"放行名单（缺 flag 的局照样进 pool，护栏全剔=无限波次重演，且无响铃）·
保留闸门等人下线节点（出路不存在，课程永远停车）。

**违反后果**：向 pool 派发一局 ⇒ 该局标准开局 + 护栏剔除 + 波次凑不齐（无限波次）。

## §2026-09-26-goalnn-notorch-half（2026-09-26，python 门禁的「免 torch 半边」拆分纪律 + 静默绿变红守卫）

**背景**：nn-training 的 python 门禁把 ~3000 用例一次跑完；31 个测试文件因 `import torch`
（或运行期延迟 import）无法在**没有 torch 的机器/镜像**上跑。拆分的收益是**可移植性 / 覆盖**
（torch 屏蔽实测：本次 2836→**2875 passed**，失败 21→6、收集错误仍 18；门禁全量
3005 passed / 3 skipped。分两步：先 2869，再扫掉剩余 13 个红里的 6 个），**不是墙钟**：
实测把 torch 集单独开池只会更慢（`torch -n1 ‖ notorch -n11` 40s > 单次 `-n12` 33s；机制 =
套件墙钟由免 torch 的重用例决定，360 个 torch 用例本就落在 worker 空档里）⇒ 默认门禁**维持
单次 `-n12`，不做两池拆分**（理由写进 `tools/githook/nn-python-gate.sh` 头注）。

**拆分纪律（就近放 + 顶层零 torch + 原模块再导出）**：一个模块混装「纯 numpy/stdlib 判据」与
「torch 张量胶水」时，把前者抽成**同目录**的孪生模块，原模块再导出 ⇒ 只测那一半的用例可直指
孪生模块。已有先例 `data/mirror.py`、`ppo/np_core.py`；本次再四处：`data/weights_meta.py`
（清单强校验 + 最新权重发现）、`data/shard_split.py`（shard 级切分；随机顺序仍由调用方的
`perm` 给 ⇒ 切分逐字节不变）、`train/device.py`（`cuda-dp` 判据，**探针降成参数** ⇒ 不必
`monkeypatch.setattr(torch.cuda, …)`）。

**铁律（本会话踩了两次，会再犯）**：**导入点与 monkeypatch 点必须随函数一起搬到新家**。
`load_episodes_common` 搬去 `np_core` 后，打在 `ppo.common` 上的 monkeypatch 成了**静默空操作**
（`test_log_diet` / `test_ppo_quota`）；`_XLA_CACHE_STATE` 的家在 `np_core`，却从 `ppo.common`
`import`（那只是再导出）⇒ 把 torch 拖回运行期（`test_xla_step_diag`）。判据：搬函数 =
同时改 import 行、monkeypatch 目标、以及**只测那一半**的用例文件。

**被否决**：① 门禁拆 torch 池 ‖ 免 torch 池（更慢，见上）；② 用 `pytest.importorskip` 静默
skip torch 用例（掩盖覆盖；torch 真缺失时应当**红**）；③ 为省 torch 把 `demo_index` 搬进
`np_core`（它是张量胶水，家就该在 `ppo/common.py`——只把它的**用例**搬到 `test_ppo_common.py`）。

**违反后果**：从再导出点 import ⇒ torch 被拖回测试路径（拆分白做）；monkeypatch 打在旧家 ⇒
静默失效，用例看着绿而根本没测到东西。

### 补记（同日第三轮）：`engine` 的装载半边 + 三份测试分家（2875 → **2907 passed**）

- **生产两刀**：XLA 设备/诊断助手从 `ppo/common.py`、trajectory 装载半边（含 `GAMMA`/`LAM`
  权威定义）从 `ppo/engine.py`，两簇都**一行不碰 torch** ⇒ 都搬 `ppo/np_core`，两处再导出；
  `ppo/__init__._EXPORTS` 三个便捷名同步改指 np_core（**第三次**踩「指向没随函数搬家」）。
- **测试三处纯搬迁**（新文件顶层零 torch）：`test_np_core.py`（9，自 `test_ppo_common`）、
  `test_bc_resume_store.py`（4，自 `test_bc_epoch_resume`）、评估块 2 条入 `test_bc_course`；
  `test_metrics_shard` 就地改取 `ppo.np_core.load_episodes`。搬迁完备性用 **nodeid 多重集逐条相同**
  （真 torch 两侧各 3008）证明，不靠行数。
- **新铁律（与上一条同族）**：**「没人 `import` 这个名字」≠「没人在属性上取它」**——删掉 ruff 判
  F401 的再导出（`engine.compute_gae` 等四个）⇒ `test_ppo_goal` 当场 AttributeError；已加身份守卫。
- **测量口径**：`-o addopts=""` 重置 addopts（否则 addopts 里的 `-x` 会在首个红处把各 worker
  截断，passed 偏小）；worktree 对比须 `NN_PY=<主树 venv python>`；worktree 建在仓库内会让
top 层源码扫描类闸（`bun test`）成批**假红**，比完立即 `git worktree remove`。
- **不动**：`test_backend_contract` 改源码扫描会换掉 `isinstance(RolloutBackend)` 这条真结构断言
  （需单独决策）；`ppo/goal|intent` 的 `compute_gae_variable` 是各线 `GAMMA_TICK` **别名**不是重复实现。
—— 全文（逐项拆分理由 / 测量数据 / 守卫清单）→ `docs/nn/engineering.md` §29

## §2026-09-26-goalnn-contract-scan-layer（2026-09-26，运行期契约判据拆成「源码扫描层 + ground truth + 交叉校验」）

**背景**：`tests/worker/test_backend_contract.py`（P0-1 的捕获器：三套 PPO 后端必须满足
`RolloutBackend` 的 5 个成员 + `update` 必须接受 stream.py 无条件注入的 `ckpt_path` /
`on_epoch_done`）原本只走**运行期** `importlib.import_module` 三个后端 ⇒ 三个后端顶层都
`import torch`，**无 torch 的机器/镜像上整文件收集失败**，这类缺陷在那边一条都守不住。

**做法（三层，判据一条不删）**：
1. **源码扫描层**（免 torch，`tests/helpers/backend_contract_scan.py`）：AST 解析 5 个成员
   （含 `from X import y` 递归确认名字来路）、把 `def update(...)` 的形参表翻成
   `inspect.Signature` 后跑**同一段** `sig.bind(...)`；`if`/`try` 体内的定义标 conditional
   且必需成员不得 conditional；解析不出的形状（lambda 赋值 / partial / `__getattr__`）报
   `UNRESOLVED` 而**不是**当作通过。
2. **运行期 ground truth**（`test_backend_contract_runtime.py`）：原 3 条断言原样保留。
3. **双向交叉校验**：静态结论 ⇔ 运行期事实（成员表、可绑定性），任一方向分歧即红。

**「不弱化」的证据（突变探针，实测）**：把 `ppo_update_goal` 的 `on_epoch_done` 摘掉
（P0-1 原形）⇒ 两层**同时**红，且静态层在 **`PYTHONPATH=tmp/no_torch`（无 torch）下也红**；
两条交叉校验在突变下仍绿（两层同结论）。+ 文件内自证用例把四条边界各砸一遍。

**被否决**：① `pytest.importorskip("torch")` 静默 skip（掩盖覆盖，且与
`§2026-09-26-goalnn-notorch-half` 的「torch 真缺失时应当**红**」口径冲突）；② 留运行期一份
了事（无 torch 机零覆盖）；③ 为省 torch 把 `isinstance(RolloutBackend)` 这条真结构断言删掉
（可移植性不该拿掉真判据）。

**代价（明说）**：无 torch 机上**测试文件数不变**——老文件退出收集失败名单，新的运行期文件
按「不静默 skip」口径顶进来；匀出来的是用例（2907 → **2918 passed**，收集失败文件仍 16 个）。
—— 全文（逐项边界 / 突变探针输出 / 自证清单）→ `docs/nn/engineering.md` §29 补记

## §2026-09-26-goalnn-notorch-final-16（2026-09-26，剩下 16 个收集失败文件逐条终判：再匀出 6 条）

**做完的**（都是「被同文件的 torch 邻居连坐」，搬到主题相同且免 torch 的孪生文件）：
`test_ppo_numerics` 的 3 条 GAE（numpy 口径）→ `test_np_core`；`test_shard_split` 的
shard_ids 对账 1 条 → `test_shard_plan`（顺带把「合成形状 = load_dataset 生产形状」钉住）；
`test_coord_golden` 的 `.5` 平局守护 1 条 → `test_schema_fingerprint`（它守的是**跨语言取整
语义**：torch.round 四舍六入五取偶 vs TS `Math.round`，而 `BOARD` 也在 `schema.py`）。
**2918 → 2924 passed**，测试名多重集对账：3025 → 3026（1 改名 × 1 新增，其余同名搬迁）。

**剩下 16 个判定为「真需要 torch」**（逐文件真因与理由表 → `docs/nn/engineering.md` §29 第五轮）：
真张量运算（masked CE / KL / sync_scalars / coord 渲染 / RLNet·StudentNet 模型）、真 DataLoader
（跨集泄漏）、真 state_dict 序列化、真 `bc_train`、真三后端 import（运行期 ground truth）。

**判据（两次被 AST 骗过，写进规矩）**：判「这用例要不要 torch」必须看**它把什么交给谁**——
`test_ppo_demo_mix` / `test_ppo_kickstart_cache` 的“免 torch”用例是把 numpy chunk 交给
`ppo_update`（engine 内部转张量）；`test_student_model::test_arch_metadata` 看着只比字典，
实际靠 `StudentNet()`。**AST 里没有 `torch` 字 ≠ 不要 torch。**

**被否决**：① 给张量胶水模块（`ppo.common` 的 `sync_scalars` / `demo_index`）再造孪生——
它们的家就该在 torch 那侧（只把**用例**归位）；② 为 1–2 条用例新建只装那几条的文件——除
`test_shard_split` 那条有真主题归属外，其余留在原处并在表里写明理由；③ 跨文件借 helper
（孪生文件里重写一小段自造语料）。
—— 全文（16 文件逐条真因表 / 搬迁对账 / 两条新标准）→ `docs/nn/engineering.md` §29 补记
## §2026-09-26-paired-kill-opt-in（2026-09-26，配对杀臂默认关火）

**来历**：`§2026-09-25-clutch-null-kill` 的对称自杀修了对照臂，但"谁该被比"没修——
state-init 出生 11 轮即被杀：拿它的 it5/it10 日常（12.0%/11.5%）去比**已归档** L1 的
it5/it10（16.0%/15.5%），连跪两点 −4pp。两腿同 V（1789876303）只是门派同源（全屋种子流），
不是配对实验；L1 当天已判负归档，拿它当"对端"比出"落后"是必然。

**决定**：`courses.<课>.paired_kill.enabled` 显式 `true` 才判（缺席/写坏=False；关了的守卫
连 streak 落账都不写）。C-w 设 `enabled: true`（课程要的中点杀臂仍在）+ C-0 保持
`self_kill: false`（只记录不停车）；其余一切腿（state-init/L1d2/L2a/L2b…）缺席=不判——
未来同 V 的活腿之间也不会交叉开火。

**被否决**：只给"归档腿"加存活判断（治标：未来两条活腿同 V 照样互杀；且"归档"无机器定义）·
守卫内写死白名单（课程对名单进代码，换一批腿改一次；执行面策略住 rl-config 已有先例）。

**违反后果**：默认开火 ⇒ 任何新腿出生即被拉去跟历史最强点比，连跪即死（state-init it11）。

## §2026-09-26-nodes-decouple-from-course（2026-09-26，节点统计与课程解耦 + 按天窗口）

**来历**：控制台节点页自称“机群级”，但 `aggregateNodeHistory()` 一直只聚合 `tmp/**` 里 mtime
最新的**那一个** `dist-agent-meta.jsonl`（旧动机：避免旧训练流数千条历史淹没新数据）——实际是
「最近活跃那一门课」的页。plan/nodes-decouple-from-course.plan.md（2026-09-24 用户指令 + 2026-09-26
评审修订）。

**决定**：① 数据源合并**所有**训练流（课程目录 + 独立 eval run 目录），逐流取完成水位
（账本 `iteration`）**只用于过滤进行中那一轮**；② 视图按**本地日**分桶、`?days=` 切窗口
（today/yesterday/7/all/N）；③ 课程内序号 `it` 是**课程内**序号、跨课不可比，**不进任何展示
字段**；④ `aggregateNodeHistory()`（全量、与窗口无关、进 SWR 缓存）⊕ `projectWindow()`（纯投影）
⇒ 切天零重算，预筛钉在 epoch；⑤ 健康度取「最新完成轮」（跨课按**完成时刻**选，`nodeHealth` 判据
不变）；pill 的 `lastContrib` 与节点表**共用同一份 agg**；表格状态列改名「成功率」（窗口内完成率）
与 pill「产能」分列分名。

**被否决**：把「全局 it 基准」当跨课对齐口径（`it` 是课程内序号，课 A 的 it300 会把课 B 的 it50
全算成 0 贡献，看着像掉线）；预筛随窗口变（会让 `days=1` 与 `days=all` 读不同文件集 ⇒
「切天零重算」不成立）；把「最新完成轮」按 it 大小排序（停摆课的高序号会误胜）。

**违反后果**：展示面出现 `it` ⇒ 跨课比较无意义（同一数字两门课含义不同）；按 it 选最新完成轮 ⇒
停摆课永久占据基准，活跃节点被算成 0 贡献（假离线）；UTC 换算 ts（训练机本地时间）⇒ 傍晚的局
跨天错桶。

**落点**：`dashboard/src/server/pool-history.ts`（byDay / projectWindow / resolveWindow /
lastContrib）、`server/api/pool.ts` + `snapshot-cache.ts`（两个消费者）、`web/view/pool-types.ts` +
`NodeStats.tsx`（Segmented + 列改名）；正文 `docs/nn/console.md §16`。

**二轮评审补正（2026-09-26，同日）**：① `aggregateNodeHistory()` 加**进程内 memo**（池根 + epoch +
候选指纹〔路径/mtime/体积〕+ `AGG_MEMO_MIN_MS = 30s` 时间下限）—— 两个消费者共一份，训练中
（meta 逐秒追加）重扫节奏也封顶（否则快照刷新器每 5s 重扫全池）；② **水位只作用于计数/样本，
时间戳/错误列取全部行**：`isSlowNode` 与「最近成功/失败」是**可达性**口径（含进行中那一轮），
统计是数据卫生口径 —— 两者混用会把「轮前段就交完活」的节点推回上一轮时刻、在 30 分钟窗边界
把「慢」误判成「离线」（2026-09-11 报障的反面）；③ 「最近失败」与「最近错误」同按**近一小时**
判定；④ 预筛锚点随**池扫描根**取（默认路径逐字节不变）⇒ 单测可端到端验预筛；⑤ 预筛命中数
按**扫描深度护栏**算（`tmp/**` 下 131 个 meta 文件 ≠ 实际命中的 15 个流），ts 解析不出的行丢弃、
不再落进 1970-01-01 桶。

**被否决（二轮）**：只给候选流数加硬上限（静默丢流，面上看不出、行却少算）；memo 只留指纹不留
时间下限（训练中 meta 逐秒追加 ⇒ 指纹每秒都变，等于没缓存）；把 `lastFailTs` 从窗口改成窗口无关
的全量最大（脚注说的近一小时就变成假话）。

---

## §2026-09-26-course-archive-compress（2026-09-26，课程封存默认 gzip：重开 O3「不压缩」）

**来历**：`plan/course-archive.plan.md §8-O3` 原裁决「不压缩」，理由是「单课 ≤80 MB 收益小、
且会破坏 `eval_log.jsonl` 可直接读」。同日 E0 基线实测 13 门已停课的保留集（L0）合计
**334 MB**（其中 `eval_log.jsonl` 单门 16–30 MB），用户 2026-09-26 指令重开该裁决。

**决定**：
① **默认 gzip（level 6）逐件压缩 L0 文本件**（`*.jsonl` / `*.json` / `.console.log` / settle 行）；
实测 `eval_log.jsonl` 10.5–11.7×、`judge/backtest` 10.0×、`dist-agent-meta` 11.6×、
`training_log` 8.9× ⇒ 334 MB → **≈50 MB**。
② **不压**四类：`archive-manifest.json` 与 `ARCHIVE.md`（控制台 S3「只读 manifest、不扫盘」
必须零解压成本）、L3 保留的 `.npy` shards（dense 数组 + BC 复算要直接 mmap）、
`ppo_ckpt_remote.tar`（实测仅 1.4×，白搭一层）。
③ `--codec xz` **可选**，固定 **preset 3**（依 `remote-transport.md §B6`：preset 3→6 已量、
不采用）；`--no-compress` 为逃生舱（一次性排障要直接 grep）。
④ manifest 的 `sha256` 一律算**解压后的原字节**，另记 `codec` / `bytes_raw` / `bytes_stored`
——否则换机/换级别重压 ⇒ sha 变，⑤校验仪式失效。
⑤ `tools/course_compare.py` **透明读 `.gz`**（照 `remote/protocol.py` 的 gzip 魔数判别先例）；
G5 断言由「逐行逐字段一致」升为「**解压后逐字节一致**」（更强且更好测）。
⑥ 封存目录形态 = `<课>/eval_log.jsonl.gz` 等（原名 + `.gz`，逐件、不打包）。

**被否决**：**zstd**（新依赖，§5 要求先论证；仓库既有惯例就是 stdlib gzip）；
**xz preset 6**（`remote-transport §B6` 已量，体积仅 −2.6…−3.0%）；
**压整包含 manifest**（控制台列封存课要逐课解压 gz，违背 S3「不扫盘」初衷）；
**整目录打一个 tar.gz**（丢「一件一文件」的 manifest 模型，也丢单件 sha 可验）。

**违反后果**：sha 算在压缩字节上 ⇒ 换机重压即校验失败，「建→校验→删」的顺序保证被架空；
压 manifest ⇒ 控制台每次列封存课都要解压。

**落点**：`nn-training/rl/course_archive.py`（未建，E1+ 实现）、`nn-training/tools/course_compare.py`
（未建，E5）；裁决正文同步进 `plan/course-archive.plan.md §8-O3`，基线数 `docs/nn/course-archive.md §1`。

**边界**：这是一条**便利 vs 体积**的裁决，**不是 G1 的杠杆** —— 封存后 `tmp/` 地板 10.0 GB 里
L0 只占 0.3 GB（3%），压到 0.05 GB 只把地板降到 9.75 GB。`tmp/ ≤ 1 GB` 的瓶颈是
在训课 9.0 GB 与非课程 0.70 GB（E0 基线），别拿压缩当空间问题的答案。

---

## §2026-09-26-course-archive-guard-and-startpoint（2026-09-26：封存硬闸补第二道新鲜度 + 起点 sha 可验）

**来历**：2026-09-26 外部评审用真模块探针实测两处实现稿缺陷
（`plan/course-archive.plan.md`）：① `course_guard` 首句 `if not marker.exists(): return ""`
⇒ **无 marker 时新鲜度整条失效**；② manifest `keys` 只认 `L0'-opt-key`（offline 族），
A/B 形态的 `it<N>/ppo_ckpt_remote.tar` 走 `L0-ckpt-key` ⇒ 多数课 `weights[]` 恒空、
`ARCHIVE.md` 标签（「opt/ckpt」）与实际不符。另有一处规格偏差未记：`weights[]` 只有目录级
glob 提示，没有 sha256/bytes（G4-① 明写「sha 可验」）。

**决定**：
① **新鲜度升为与 marker 无关的第二道闸**：目录新鲜（不论有无 marker）默认拒绝封存；
`--force` 只越「陈旧 marker」与「无 marker 的新鲜目录」，**不越「新鲜 marker」**。
来历：停课动作 = 删 marker 但**不杀循环**（`stopCourse` 注释自己承认云机上那份只能由操作员
在云机侧停）⇒ 正规流程「先停课 → 再封存」正好落在「无 marker 但仍在写」的窗口，旧实现会把
**撕裂的树**搬进档案再 `rmtree` 活体（⑤ 的 sha 链只证「拷贝保真」，证不了「源已静止」）。
② **关键轮 = opt ∪ ckpt 两族并集**（`_KEY_REASONS`）；`ARCHIVE.md` 标签与实际一致。
③ **`weights[]` 起点「sha 可验」落地**：解析到具体归档件 ⇒ 记实际 `path` + `sha256`/`bytes`；
解析不到 ⇒ 退化为目录级 glob 提示且**不带** sha（绝不编造哈希）。
④ **控制台起点选择器落地**：`openCourse` 收 `seedFrom:{sourceCourse,it}`（**不收路径**，服务端按
manifest 自解析并校验落在权重归档内）；解析不到响亮拒绝（绝不退回 BC 播种）。

**被否决**：修正 ①时把「无 marker」一律放行到 `--force` 才做（会让正规流程必须先 `--force`，
把逃生阀变成必经步）；起点「让客户端传路径」（可被篡改的写面）；找不到归档件时退回 BC
（静默拿错起点，与 §384 同类错）；把 sha 记在 glob 提示上（假哈希）。

**违反后果**：放行新鲜无 marker 目录 ⇒ 搬到撕裂的树 + 删活体（不可逆）；只认 opt 族 ⇒
A/B 课起点入口失效；编造 sha ⇒ 校验仪式变假。

**落点**：`nn-training/rl/course_archive.py`（guard / `_KEY_REASONS` / `resolve_archived_weight`）、
`dashboard/src/stack/courses.ts::resolveArchivedSeedPath`、
`dashboard/src/server/actions/course-lifecycle.ts`、`dashboard/src/web/app/panels/OpenCourseModal.tsx`；
正文同步进 `plan/course-archive.plan.md §1.3-1 / §3.5 / §3.6`。

## §2026-09-26-x20-steady-cont（2026-09-26，B续跑立项：八连阴后唯一阳性斜率 + H1/H2判决实验）

**来历**：noexplore it105 之后八条腿七死一停（floor/firstkill/terminal/demo-mix 判负；
dodge L1 两档判死、L3 两档判负、L2a 无信号、L2b B2 未开；state-init 154 轮日常反 хуже）。
唯一阳性是 B：判决 35.5 vs 门 35（差 4 局），终点区日常已摸门。B 结算④遗留总量混杂
（"每轮更稳还是总量更多"）从未被回答。

**决定**：开 `x20-steady-cont`（`nn-training/curricula/x20-steady-cont.jsonc`）——与 B
逐字同（奖励/位移/采集全同，validate 零错零警），起点 B-it175 纯 warm start
（`kickstart_ref=false`，C 教训 kk≈0），200 轮/14h，门沿用 B 原门（414000 ≤35% 且
p10≥2，不移球门）。过门 = H1（量不够，继续加）；200 轮横盘 = H2（到顶，停烧转蒸馏评审）。
横盘后不转 state-init（154 轮已阴性）。L3d2 并跑（算力足）。

**被否决**：新价格腿（六条全死，重开是挖坟）；现在接受平台（B 斜率未死，先烧便宜的这枪）；
continuation 带 kickstart（C 事故：kk=1 前 30 轮全回锚）；续 B 的种子流（B 先于 pinning
机制，无值可续；新流 + 绝对门，代价是训练曲线只可比趋势）。

**违反后果**：带 kickstart 开 ⇒ 前 30 轮白烧；中途改门 ⇒ 0.5pp 级 verdict 变 p-hacking；
预算烧完横盘还续 ⇒ L1d2 证明过这是"再等等"无底洞。

**落点**：`nn-training/curricula/x20-steady-cont.jsonc`（结算节待回填）。

## §2026-09-26-hub-mode-restore-enabled-only（2026-09-26，用户报障：起 hub/trainer 等很久——回灌把停掉的历史课逐课重试）

**症状**：控制台首页点「启动服务进程」，停在 `hubServer` 那一步几十秒；回执刷出一串
`失败 x20-clutch: 需要合法 course（[]）与 mode(...)`、`x20-demo-mix`、`x20-dodge-l1` …（真机 11 门）。

**根因（代码实锤，非「扫盘慢」）**：`courseModes` 是**只增**意图表（开课 / 停课 / 热切都往里写），
停在 `tmp/` 下的历史课永久留着一份意图；而 `restoreCourseModes`（起 hub 回灌，挂在
`startComponent('hubServer')` 的**两个分支**上、同步 await）**逐条** 推全部意图。hub 的认课判据是
**开课标记**（`remote/hub/queue_discover.py::_course_dir_live` / `_serves_course` 都要求
`training-enabled.txt`）⇒ 停掉的课按设计**必回 400**，于是每门课都白烧整段有界重试
（`pushModeWithRetry`：3 次首试 + 2×2s ≈ **4s/门**，串行）。11 门 ≈ **44s** 纯 sleep。
实盘核对：`tmp/*/training-enabled.txt` 存在的两门（`x20-dodge-l3d2`/`x20-steady-cont`）回灌成功，
其余 11 门全失败——与报障日志一一对应。

**决定**：`restoreCourseModes` **只回灌已开课的课程**（判据 = 开课标记
`stack/courses.ts::courseEnabled`，与 hub 同源）。未开课的课进 `skipped`（**不是失败**）：
意图一个字不丢（仍在 `courseModes` 里），摘要如实写「跳过 N 门未开课（意图保留，开课即下发）」，
真正开课时 `openCourse` 按弹窗模式重新下发。

**落地**：判据从 `server/actions/course-lifecycle.ts` 搬到 `stack/courses.ts`（该文件本就是
仓库里「课程域共享判据、不断环」的家：`course-lifecycle` 已 import `course-mode`，回灌反向
import 会成环）；`course-mode.ts`（gate + `RestoreResult.skipped`）· `state-view.ts` 改 import 口 ·
`course-lifecycle.ts` 改 import；回归 `tests/course-mode.test.ts`（+2：未开课 ⇒ 零 POST 零重试、
跳过如实上摘要不算失败；既有 5 例补开课标记）。

**备选与否决**：① 只把回灌改成并行（`Promise.all`）——否，省掉串行 sleep 但**仍**白打 33 次
POST、仍刷一串假「失败」，根因（推了必被拒的课）没治；② 缩短重试窗口 / 去掉重试——否，
那次有界重试是 2026-09-23 真机事故（新开课 hub 还没扫到）的解药，不能为省时间砍掉；
③ 停课时删掉意图项——否，丢记录且 `stopCourse` 的 hub 置离线要写它；
④ 起 hub 时先 `GET /admin/courses` 取 hub 课程表再筛——否，又把「发现时序」引回回灌路径
（`docs/nn/remote-transport.md §36` 的坑）。

**违反后果**：任何「回灌按意图表全量推」的写法都会随历史课积累线性变慢（且刷假失败）；
任何把回灌判据换成「hub 课程表」的写法会把发现时序竞态引回来。全文 →
`docs/nn/console.md §15`。

---

## §2026-09-26-rl-config-scope（2026-09-26，`rl-config.json` 职责边界：只留「别处无处安放」，删配置不删代码）

**触发（用户 2026-09-25/26）**：「已经在其它地方配置过的数据就不该再往里填；`intent_rl` 已废弃，
后期重启也要配成现在 x 系列一样」→「intent/goal、`stream`/`double_buffer`/`precollect_early`
**只删配置、不删代码**」。

**决定**：`nn-training/rl-config.json` 只保留五类里的 A（机器/环境级）、C（调度策略）、D（每课
机器侧旋钮活课）；B（课程文件已有同名键的全局缺省）只留必要兜底；E（废弃/死键）删。
**新键该写哪**：机器事实 → rl-config；「怎么训」的超参/奖励/关卡/门 → `curricula/<课>.jsonc`。

**本次删除（8 个点号路径，含代码读点）**：`intent_rl`（整块 29 键）、`policy.upgradeBranch`
（**连 `rl/dispatch.py` 的读点一并去掉**——只删键不删读点，2026-08-30「残留旧分支名 reset 全部
节点」的坑就还在）、`policy.minDiskFreeMB`（零消费者）、`policy.streamKlCapIntent` /
`policy.streamWaveGamesIntent`（intent 专属）、`rl.stream` / `rl.double_buffer` / `rl.precollect_early`
（单一 PPO 路径下 `validate_args` 恒置 0 / 只在 `double_buffer` 开时被读 ⇒ 填了也不生效）。

**代码保留**（用户裁决）：`ppo/intent.py`、`ppo/goal.py`、`rl/modes.py` 的 `intent_rl(legacy)` 回退、
`rl/eval_m1.py` 与 m1-eval 派发链、`stream`/`double_buffer`/`precollect_early` 的读取点**都不动**
——重启时复用，不从 git 历史重补。intent/goal 入口仍冻结（`validate_args` 拒启），重启前须先解冻。

**防再长草**：新增 `nn-training/rl_config.schema.json`（键白名单数据）+ `rl_config_schema.py`
（**只告警不拒**，`run_rl.py` 启动时把未知/已退役键点出来）；清洗工具
`nn-training/tools/rl_config_clean.py`（dry-run/`--apply` 先备份 sha 校验/`--matrix`/脱敏）；
顺手修 `tools/agent/agent-setup.md` 里把磁盘阈值当成 rl-config 键的指路。

**备选与否决**：① 连 `ppo/intent.py`、`ppo/goal.py` 一起删——否（用户裁决：删配置不删代码）；
② 顺手去掉 `rl/modes.py` 的 legacy 回退与改 `e2e/test_run_rl_m1.py` 断言——否（同上）；
③ 一次性真删（B 类全删、零缺省）——否（会让历史课程无法复现，漏声明时静默落 argparse 默认）；
④ 未知键硬拒开训——否（用户裁决 O3：只告警不拒）。

**违反后果**：把「怎么训」的超参写回 rl-config = 造第二事实源（用户原则针对的正是这一类）；
不删 `policy.upgradeBranch` 的读点 = 下次有人手填非空值就再 reset 一遍全部节点。全文 →
`docs/nn/rl-config.md` §1 · 计划 → `plan/rl-config-cleanup.plan.md`。

---

## §2026-09-26-rl-config-b-class（2026-09-26，B 类兜底键删除：范围取「全部课程」超集 + `workers` 归机器级）

**触发（用户 2026-09-26）**：「在训练机跑 `rl_config_clean.py --matrix`，按全绿结果删掉多余 B 类兜底键」。

**前置事实**：训练机（= 本机，hub / sampler / trainer 都活在这个工作区）上操作员已于 17:49 把最后
两门在训课停掉（`x20-steady-cont` / `x20-dodge-l3d2`；控制台日志 `已停课 …（已删开课标记）`）
⇒ `live`（`training-enabled.txt`）集为**空**，plan §3.2 / §8-O1 的常规判据出不了结论
（`is_green` 对空集恒 False）。

**决定 1 — 判据范围改用「全部课程」超集**：`--matrix` / `--drop-b-class` 增 `--scope {live,all}`；
`all` = `curricula/*.jsonc` 全部课程（本机 108 门）。理由：B 类兜底的**风险面**是「任何课程没声明该键
时落到 rl-config 的值」，所以「全部课程都不靠它」是**更强**的证据（全绿于 `all` ⇒ 全绿于 `live`，
反之不然）；在训集为空时它是唯一能出结论的范围。**本次按 `all` 删 `rl.difficulty` / `rl.max_ticks`**
——两者 108/108 显式声明（44 课程文件 + 64 关卡注入），且唯一消费者 `rl/eval_a_once.py:273-274`
的兜底 `or 12000` / `or "hard"` 与被删值**逐字同值** ⇒ 零行为变化。

**决定 2 — `workers` 与 `local_slots` 同归机器级**（`tools/rl_config_clean.py::MACHINE_KEYS`）：
二者在 `--scope all` 下判「全绿」（108/108 课程都写了 `workers`），但 rl-config 里这条是**裸机读数**：
`dashboard/src/core/slots.ts::bareCapacity` = `max(rl.workers, rl.local_slots)`，
`rl/config.py::apply_course_machine_overrides` 也把 `rl.{workers,local_slots}` 当本机配额缺省。
删 `workers` ⇒ `Number(undefined ?? 0)` = 0 ⇒ 容量塌成 0、`checkCapacity` 把每门课报成超量（假红）。
这是 plan §3.2-4 对 `local_slots` 豁免的推广。

**保留兜底（非全绿）**：`mb`（9 门 BC/demo 课没写）、`seed_rotate`（4 门）、`keep_iters`（106 门）、
`eval_window_sec` / `total_stages` / `rotate_stages` / `seed`（各 108 门）等一律**不删**——
plan §8-O1 已否决「零缺省」。

**顺手修的 bug（同一路径）**：`tools/rl_config_clean.py::load_jsonc` 此前是 `strip_comments` +
`json.loads`，**漏了去尾逗号** ⇒ 实测 108 个课程文件里 88 个、25 个关卡文件**全部**读不进，
`--matrix` 一遇在训课程就 `JSONDecodeError`；更危险的是若哪天调用点吞掉异常，「读不进来」会被
读成「课程没声明该键」= 静默删兜底。改为产品同源 `rl.jsonc.loads`（= `strip_comments` →
`_drop_trailing_commas` → `json.loads`）。

**备选与否决**：① 只用 `live` 范围、本次什么都不删——否（在训集空是操作员的即时状态，不是「无课程」；
空集下不删等于把「召回训练机跑矩阵」的目的作废）；② 按「4 门 dodge 课」（course-archive §1.1 的
基线在训集）范围删——否（会把 `mb` / `seed_rotate` / `lr` / `epochs` / `gamma` / `lam` 一起删掉，
而 9 门 BC/demo 课正靠 `rl.mb` 兜底 ⇒ 历史课程行为漂移，正是 §8-O1 否决的形态）；
③ 连 `workers` 一起删——否（机器容量读数，见决定 2）；④ 现场补写 `training-enabled.txt` 造一个 live 集
——否（伪造开课标记 = 越权改控制台拥有的状态文件，AGENTS 明禁）。

**违反后果**：把 `live` 当唯一范围 ⇒ 全停课时工具永久无法收敛；删 `rl.workers` ⇒ 控制台并发校验假红。
全文 → `docs/nn/rl-config.md` §1.4b。

**评审补正（2026-09-26 晚，对 `12187fa4` + `9fd0834c` 两个提交）**：另一 agent 逐条核过后，以下四处**改了实现**：

1. **「与控制台冒烟共用一份」的说法作废**（`rl_config_schema.py` docstring / `rl_config.schema.json`
   `_doc` / `run_rl.py` 注释三处）：`dashboard/src/**` 对该 JSON **零引用**，`rlConfigSmoke` 仍只查
   port/token/nodes ⇒ plan §3.4/E8 的「冒烟面板也点出假键」**未达成**（只达成启动日志那一半）。
   改 dashboard 时再接线，那时才恢复「共用一份」的措辞。
2. **脱敏收敛成一条清单，且不给「打印原文」的口子**：`rl_config_clean.py` 的
   `_SECRET_PATHS=("rl.remote_token","rl.remote_hub_url")` 与 `desensitize()` 实际只脱
   token/authKey **不一致** ⇒ 改为 `SECRET_KEYS=("remote_token","authKey")`（打印面与 diff 面
   共用一份）；同时把误导性的 `--no-redact` 改成诚实的 `--no-preview`。**否决**「按名字实现成
   打印原文」：plan §1.5 红线 1 说任何输出（含本地排障）都不得带出凭据 ⇒ 那就**不存在**这条路径。
3. **升级分支的坑在签名层面关死**：`dist_common.upgrade_branch_or(explicit)` ⇒
   `current_upgrade_branch()`（无参）。读点已验证删除，但「显式值优先于锁存」这个**语义**还留在
   签名里 ⇒ 顺手写一行 `or` 就能把 2026-08-30 的坑复发。拿掉参数后，复发必须改签名（并改
   `tests/tools/test_dist_upgrade_cli.py::test_upgrade_branch_has_no_explicit_override_path` 的断言）=
   一次有意识的决定。备选：保留函数不动——否（评审正确指出那是不可达死分支）。
4. **接线补钉子**：`check_rl_config` 有单测，但 `run_rl.py` 那三行「读配置 → 打告警」此前无人盯
   ⇒ 新增 `tests/worker/test_rl_config_schema.py::test_run_rl_logs_the_advisory_and_still_starts`
   （子进程 oracle，同 `test_serve_wiring` 手法）：假键 ⇒ 启动日志出现告警行且**照常起训**。

**控制台死开关的处置（2026-09-26 用户裁决）**：`stream` / `double_buffer` / `precollect_early`
三个开关**保留在界面上，但标灰 + 写清「当前不生效」**——否决「摘掉开关」与「只留文档」两档：
`TrainLaunchModal.tsx::INERT_TOGGLES` 清单驱动渲染，`disabled` **常闭**、`note` 就写「当前不生效」，
且显示值改成 rl-config 的当前值（**不再**读写 localStorage 偏好——灰着的开关显示成「开」会被读成
「它开着且有效」）；`preset.ts::setMode` 的回写白名单**保留**这三键（解冻 intent/多路时直接复用）。
守卫：`dashboard/tests/web-train-launch-wiring.test.ts`（呈现 / 常闭 / 不再写回 / 白名单保留四处）。

## §2026-09-26-x20-series-close（2026-09-26，x20 全系列关账：八腿结论 + L1/收入侧判死 + 转向新纪元）

**来历**：noexplore it105 之后全部 x20 腿关账（`docs/nn/experiments.md` §37–§44）：
L1 两档（0.01 跑满 300 轮 / 0.02 31 轮 + 三候选门控）机制双零进展、门控七候选全灭 ⇒ **L1 方向判死**；
L2a（65 轮）danger/threat 双平 ⇒ 判负；L2b（66 轮）threat 60 轮零位移 ⇒ 判负；
L3 两档（(3.5,2.0) 94 轮 / (4.0,4.0) 173 轮 + 三候选门控）拾取 +5/+9.5pp 但 low 不跟、
it100 池外显著更差 ⇒ **收入侧判死**；state-init（154 轮）日常比起点差 ⇒ 判负；
B-cont（25 轮）因表示层缺"停"被叫停，H1/H2 未决移交新纪元。

**决定**：
① L1/收入侧两方向判死：不开 wOpen=0.03，不升 (5.0,6.0)；② L2b 的 B2 升档（wThreat=60）
不再执行——60 轮零位移下加倍期望≈0，且新纪元已取代塑形路线，按 plan §6 后半句转 DAgger 排队；
③ state-init 的 B1/B4 指望落空（154 轮阴性）；④ 全系列 verdict 移交新纪元
（`plan/new-era-stop.plan.md`：STOP + 控制执行 bundle，从零爬梯子）。

**被否决**：L2b-60 照开（烧 3 小时买一个已知答案）；L3d2 再燒到 300 輪（拾取横盘 100 轮，
摸 90% 也无 low 跟随先验）；state-init 续跑（日常更差，无正斜率可续）；
逐腿 415000 回测全补（最好 −1.4pp/−3.4pp 噪声，L3 首轮"先验低不补测"先例）。

**违反后果**：重开任一判死方向 ⇒ 复刻七连阴烧机时；DAgger 提前立项（BC-ref 栽的 held-out 门未过，
先答"第一代学生凭什么活"）。

**落点**：各课程文件结算节（`nn-training/curricula/x20-dodge-*.jsonc`、`x20-state-init.jsonc`、
`x20-steady-cont.jsonc`）+ `docs/nn/experiments.md` §37–§44；门控数据 `tmp/x20-dodge-l*/settle/`。

## §2026-09-26-goalnn-stop-k-is-training-side（2026-09-26，K=10 决策门周期属训练侧：可动）

**来历**：新纪元首刀（`plan/new-era-stop.plan.md`）反复出现「K 要不要收紧」的悬案——AGENTS §0.2
规定**任务侧**（命数/敌数/关卡形态/终局标准/物理冷却）不可动，而 K 是 NN 输入侧的决策周期
（盲 hold 的长度），既非规则也非关卡。

**决定**：**K 是训练侧可动参数**。改 K 只改「策略每隔多少 tick 重新观察一次」，不改任务的胜负
条件与单位 ⇒ 收紧 K（如 10→5）属于训练侧调参，不触发 §0.2 禁令，也不需要新关卡/新任务口径。
首腿保持 K=10（Phase -1 实测：人类非冰面停段中位 9 tick ≥ K/2，相容），收紧留作 x2 rung 期权。

**被否决**：把 K 归为任务侧（会让「10 tick 盲 hold」这个已确诊病灶永远不可修，与诊断②矛盾）；
首刀就收紧 K（与 STOP 单变量声明冲突，hy P0-4）。

## §2026-09-26-goalnn-stop-mdp-no-change（2026-09-26，B案不构成 MDP 变更声明）

**来历**：B案把 move index 0 从「保持朝向」改为 **STOP**——动作空间的**语义**变了，需要明确
声明它算不算 MDP 变更（会不会波及 state-init bank / 决策门 / divergence-probe）。

**决定**：**B案不是 MDP 变更**。状态空间、转移、奖励、K=10 的均匀决策边界全部未动；变的只是
「同一个动作索引代表哪个物理命令」，而该映射在仿真里本来就存在（`moveDir=null ⇒ moving=false`）。
⇒ 决策门 5 处（`t%K`）、divergence-probe、state-init bank 的 `t%K==0` 切点**全部无需改**
（Phase 0 #4「天然一致即验收」）。真正的 MDP 变更（threat-onset 事件化 / 半 MDP γ^Δt）**defer
到 x2 rung**，届时按 R2 包重新预注册。

**被否决**：把动作语义翻转也算 MDP 变更（会误导后续把所有基于 K 的装置重算——它们依赖「决策
边界」而非「索引含义」）；顺带做事件化（等于首刀变多变量，hy P0-4 / glm P0-3）。

**违反后果**：混为一谈 ⇒ 无谓重出 bank / 改决策门，且掩盖真正的 MDP 变更（事件化）根本没做。

## §2026-09-26-goalnn-stop-rng-principle（2026-09-26，RNG/特权观测不进 shard：原则存档）

**来历**：新纪元讨论中反复提出「给 critic/actor 特权观测」（RNG 状态、敌开火倒计时、tick 堆、
事件堆栈）以降低信用分配难度。评审指出这会改 shard 格式并使既有 value 读数整体偏移。

**决定**：**特权观测一律不进 shard / 不进 obs**。判据①进 shard = 格式变更（`OBS_SCHEMA_MAJOR`
bump + 全量重转），且会让已训 value 头的输出量纲漂移（旧 checkpoint 不可比）；②敌开火倒计时
等本就是**超人信息**——人类看不到，进了就破坏「以人类熟练玩家为难度唯一口径」（§0.2）。RNG 只走
`world.rng`（AGENTS §2.3），不进观测。需要时以**训练侧**手段解决（GAE λ / value 头 / 事件化）。

**被否决**：把 RNG state 塞进 shard（格式 + value 偏移双代价）；观测侧加倒计时列（超人信息出局）。

---

## §2026-09-27-goalnn-eval-rows-split（2026-09-27，`rl/eval_local` 的 eval 行/账本簇下沉 `rl/eval_rows`）

**来历**：用户指令「重构 nn-training，降低模块耦合 / 复用代码 / 提可维护性与可扩展性」。前序 S1–S4 已收口
`common/` 原语层、`rl↔remote` 包循环与 `plan_run`/`loop_*`/`batch_*`（`plan/nn-training-refactor.md` §5 DoD 达成）。
本轮续开 **S5**：按「独立所有者 + 独立触发」拆 `rl/eval_local`（侦察：它有 **5 个干净的模块级连通分量**，
是剩余大文件里缝最清的一个）。全文 → `engineering.md` §30。

**决定**：把 `rl/eval_local.py` 的「逐局 eval 行 schema + `eval_log` 账本 I/O」整块（**连续行 287–602，
逐字节不动**；17 个名：三字段抽取器 + `eval_row`/`eval_row_key`/`eval_row_keys` + 账本读/并/去重）搬到新模块
**`rl/eval_rows.py`**（345 行，stdlib-only），`rl/eval_local.py` **1130 → 870 行**，原模块留 `X as X` 门面
（全仓调用点与测试**一行不改**——「名字是契约，位置不是」）。

**为什么值一做**：`remote/` 侧本来要 `import rl.eval_local` 才拿得到这些**纯行/账本**原语——「传输层伸手进
本机评估运行器」的语义错位。独立后纯数据模块（只依赖 `json`/`time`/`pathlib`）可被任一侧依赖。

**被否决的备选**：① 拆 `rl/gate_check.py`（31 个顶层函数是**一个巨团**，按链切不动）· ② 拆
`remote/hub_client.py` 组2（缝清但 `_request`/`pack_*` 是**多处** patch 锚点）· ③ 仅在原文件内重组
（不产生「可被独立依赖的所有者」，等于没拆）。

**同日续（改指调用点，消掉耦合边）**：把 `remote/` 里**只**用纯行/账本原语的三处改指 `rl.eval_rows`
（`hub/queue_resume` 延迟 import · `deliver_zip` 延迟 import · `offline_eval` 的纯 `eval_row`；后者的
`run_local_eval_game`/`settle_eval_summary` **仍**走 `rl.eval_local`——**那条边合法，不动**），把
`remote → eval_local` 的边从 3 条收到 1 条；同步 `test_hub_queue_split` 的 lazy-import 断言 + 新增两条边守卫。

**同日续二（第三刀：双轨/过拟合/结算簇 → `rl/eval_track`）**：把 `eval_local` 剩下的「双轨日常评估」关注点
整块搬走（四段跨度：`EVAL_SEEDS` 池 · 双轨种子分类 · `report_winrate_safe` · `_acc`/`_ratio`/
`_read_recent_track_wrs`/`_maybe_warn_overfit`/`settle_eval_summary`；共 23 名，逐段逐字节不动）⇒
**`rl/eval_track.py`**（404 行；stdlib + `rl.log`），`rl/eval_local.py` **870 → 573 行**。**为什么**：它是
「读账本算趋势」的纯逻辑（不起进程），与「怎么跑一局」（runner）是两件事。**刻意不搬 `EVAL_ITER_SUFFIX`**
（dispatch 的 eval iterId，不属于双轨）——「按关注点切」而非「按相邻切」。全文 → `engineering.md` §30。

**验证**（三刀）：① **逐字节对账**（第一/二刀：HEAD 行 287–602 vs `rl/eval_rows.py` 正文零差异；第三刀：
`tmp/verify_track_exact.py` 四段逐段 `ALL BYTE-EXACT`）· ② 门禁 nn **3120 → 3128 → 3130 → 3139 passed /
3 skipped / 0 failed**（+8/+2/+9 = 三个新守卫）；ruff `All checks passed`；mypy **498** 文件绿；根
`bun run check` **2181 pass / 0 fail** · ③ 新守卫 `test_eval_rows_split.py`（10 例，含 `remote/` 两条耦合边）·
`test_eval_track_split.py`（9 例，含反向断言「执行面必须留守」+ 双轨/过拟合功能语义）。

**违反后果**：在 `eval_local.py` 里再实现一份搬走的名字（「搬了一半」）⇒ 守卫红；`rl/eval_rows` /
`rl/eval_track` 反向 import 运行器 ⇒ 成环，守卫红。（进度 → `docs/nn.progress.md`；memory →
`.workbuddy/memory/2026-09-27.md`。）

## §2026-09-27-goalnn-bc-ingest-split（2026-09-27，S5 第四刀：`rl/bc_loop` 的 BC job 回传消费下沉 `rl/bc_ingest`）

**来历**：用户指令「按同一手法拆 `rl/bc_loop.py` 的独立连通分量」。用 `tmp/recon_god.py` 量出 6 条
模块级链（回传消费 / 归档·磁盘 / 采集 / 发布 / 运行时解析 / CLI）——取「回传消费」。全文 → `engineering.md` §31。

**决定**：把「等一个远端 BC job 回传、并把回传入账」整块（两段跨度共 11 名，**逐字节不动**：
`IDLE_WARN_SEC`/`POLL_SEC`/`POLL_MAX_SEC` + `READY`/`PENDING`/`TRANSIENT` + `BcWait` + `ledger_bc_epoch` +
`run_epoch_eval` + `ingest_bc_metrics` + `wait_bc_round`）搬到 **`rl/bc_ingest.py`**（291 行），
`rl/bc_loop.py` **1433 → 1213 行**，原模块留 `X as X` 门面。

**为什么**：① 一段**连续跨度**（行 374–622）；② **独立所有者**（「怎么问结果、怎么写账本」≠「一轮怎么跑」）；
③ **两条驱动路径共用**（单课程阻塞 `wait_bc_round` 与 supervisor 让位 `BcWait.poll_once`）⇒ 它必须能被两条
路径同时依赖，不该住在 1433 行的编排模块里。

**关键约束（本仓教训）**：**monkeypatch 点随函数一起搬** —— `bc_loop.time` 是共享的 stdlib `time` 模块对象
（`e2e/test_bc_epoch_e2e.py` / `tests/worker/test_bc_course.py` patch 的就是它），`bc_loop.wait_bc_round` 走门面 ⇒
既有用例零改动仍生效。分层快照先红再登记：`bc_ingest` 沿用「`_request` **函数内** import」的口径 ⇒ 它成为
`RL_ORCHESTRATION` 的**直接**成员（`tests/test_layering.py`）。

**被否决**：① 先拆「归档/磁盘有界」（纯 L1 最安全，但减耦合收益小——确实伸向远端的是回传消费）·
② 拆 `BcLoop` 的 9 方法分量（共享 `_jobs`/`_waits`/`_submitted` 三份 mutable 账，现在切只会互相传参）·
③ 仅在 `bc_loop.py` 内重组（不产生可被两条路径依赖的所有者）。

**验证**：① 分段逐字节对账（`tmp/verify_bc_ingest_exact.py` 两段 `BYTE-EXACT`）· ② 门禁 nn
**3148 passed / 3 skipped / 0 failed**（+9 = 新守卫 `tests/trainer/test_bc_ingest_split.py`）；ruff + mypy 绿 ·
③ 守卫含反向（编排面必须留守）与 `BcWait.poll_once` 三态分类 + `backoff_sec` 指数封顶（功能性）。

**违反后果**：在 `bc_loop.py` 里再实现一份搬走的名字 ⇒ 守卫红；`bc_ingest` 反向 import `bc_loop` ⇒ 成环；
把 `bc_ingest` 从 `RL_ORCHESTRATION` 摘掉 ⇒ `test_layering` 红。（进度 → `docs/nn.progress.md`；
memory → `.workbuddy/memory/2026-09-27.md`。）

## §2026-09-27-goalnn-hub-http-split（2026-09-27，S5 第五刀：`remote/hub_client` 的 HTTP 往返分量下沉 `remote/hub_http`，把 HTTP 面做成可注入薄壳）

**决定**：把 `remote/hub_client.py` 里「把一次请求发到 hub、把回答读回来」那一簇整块搬到新模块
**`remote/hub_http.py`**（**两段跨度共 17 名，逐字节不动**，478 行；原 1688 → **1299 行**）。搬走：
传输薄壳 `_request`（全模块**唯一**碰 `urlopen` 处）· 回传消费 `probe_job_result`/`poll_job`/`wait_job`/
`_wait_state_note`/`_job_failed_from_body`/`ProbeResult` + 三态与阈值常量 · 行政面 `report_job_failure`/
`set_cloud_halt`/`hub_halted`/`clear_halt_on_startup` · 失败类型 `HubClientError`。

**为什么**：这是传输面（发/收），与「打包 / 磁盘 IPC 发布 / 校验落位」是两件事；且它被**两条链路**
共用（pull 侧 `wait_job` 与 push 侧 `push_client` 的 `probe_job_result`——状态码分类的唯一实现），
必须能被两者同时依赖。分开后 `push_client` 与 `rl/bc_ingest` 直接依赖薄壳，不再依赖 1299 行的门面。
**用户指令**：「拆 remote/hub_client.py 的 HTTP 往返分量，先把 HTTP 面做成可注入薄壳」。

**关键约束（踩到并修的真问题）**：① 门面必须留 **`_request` 的转发名**（历史 import 在），但
**注入点只能是 `remote.hub_http._request`**——照转发名打补丁**不再是**宿主函数的命名空间解析，
这是**刻意**的分档（守卫正反各一条断言，防「patch 打偏而测试全绿」）；② `AUTH_HEADER`（`common.protocol`）
原经 `hub_client` 传递可达，本刀后不再转发 ⇒ `e2e/test_bc_epoch_e2e.py` 改向**所有者**取。

**分层**：`remote.hub_http` = **L1**（`common.protocol` + `net_http`(L0，延迟)）；`hub_client` 顶层 import 它
⇒ 从 L1 升 **L2**（`remote_dag.LAYERS` 同步、`assert_remote_module` 先红再登记）。

**验证**：**逐字节对账**（`tmp/verify_hub_http_exact.py` 两段 `BYTE-EXACT`）· 门禁 nn **3148 → 3158
passed / 3 skipped / 0 failed**（+10 = 新守卫 `tests/remote/test_hub_http_split.py`）；ruff + mypy（505 文件）绿 ·
根 `bun run check` **2181 pass / 0 fail**。守卫七条：定义唯一 / 分层（+`hub_client` 严格高于 `hub_http`）/
转发同一对象 / **无 `_request` 调用点** / **seam 正反两档** / import 面闭集（`urllib.*`+`net_http` 只许函数内）/
不得反向 import 门面 / 三态+410 终局（功能性）。

**违反后果**：在 `hub_client.py` 里再实现一份搬走的名字、或加回 `_request` 调用点 ⇒ 守卫红；
把 `hub_client` 的层号压到 ≤ `hub_http` ⇒ `assert_remote_module`/`test_remote_dag` 红。
（全文 → `docs/nn/engineering.md` §32；进度 → `docs/nn.progress.md`；memory → `.workbuddy/memory/2026-09-27.md`。）

## §2026-09-27-goalnn-protocol-split（2026-09-27，S5 第六刀：`common/protocol` 的失败类型族与线格式下沉 `common.errors` + `common/wire_codec`）

**决定**：把 `common/protocol.py`（**1708 行**）里两簇**格式/契约**面整块搬出（**三段跨度，逐字节不动**）：

* **失败类型族** → **`common/errors.py`**（118 行）：`ProtocolError` · `RetryableError` ·
  `UnreapableChildError` · `JobCancelledError` · `JobFailedError` · `CodeChangedError`；
* **传输编码 / 线格式** → **`common/wire_codec.py`**（231 行）：v1 gzip+base64（`_pack_wire` /
  `_unpack_wire` / `encode_*` / `decode_*` + 魔数常量）与 v2 裸二进制（`BRV2` / `BRJ2` 四函数）。

`common/protocol.py` **1708 → 1482 行**，留 `X as X` 门面 ⇒ 全仓 `from common.protocol import …` 一行不改。

**为什么**：`errors` 是**唯一的零依赖叶子**（每条继承线 = 一个处置分支，人人要 import）；线格式是「字节↔对象」
的**格式**面，与 manifest/角色的**语义**校验是两件事。**errors 必须一起走**：v2 解包要抛 `ProtocolError`，
若 `wire_codec` 反向 import `protocol`、而 `protocol` 又为门面 import `wire_codec` ⇒ 模块级成环。

**⚠ 关键不变式**：`wire_codec` 的 `ProtocolError` 与旧家 `raise`/全仓 `except` 的必须是**同一个类**
（本刀靠门面 `ProtocolError as ProtocolError` 保证；守卫用 `is` 钉住）。

**验证**：**三段逐字节对账**（`tmp/verify_protocol_exact.py` 两段 `BYTE-EXACT`）· 门禁 nn **3158 → 3167
passed / 3 skipped / 0 failed**（+9 = 新守卫 `tests/common/test_protocol_split.py`）；ruff + mypy（508 文件）绿 ·
根 `bun run check` **2181 pass / 0 fail**。守卫六条：定义唯一 / **`errors` 零 import 叶子** /
`wire_codec` 依赖面闭集（**不得 import `common.protocol`**）/ 不得碰上层包 / 门面对象恒等（含 `ProtocolError`）/ v1 往返
+ 旧格式兼容 + v2 job/result 往返 + 截断/余料响亮拒绝。

**违反后果**：在 `protocol.py` 里再实现一份搬走的名字 ⇒ 守卫红；给 `errors.py` 加任何 import ⇒ 守卫红；
`wire_codec` 反向 import `common.protocol` ⇒ 成环，守卫红。（全文 → `docs/nn/engineering.md` §33。）

## §2026-09-27-goalnn-job-identity-split（2026-09-27，S5 第七刀：`common/protocol` 的 job 身份簇下沉 `common/job_identity`）

**决定**：把 `common/protocol.py`（**1482 行**）的 **job 身份簇**整块搬到新模块 **`common/job_identity.py`**
（134 行，**一段跨度共 4 名，逐字节不动**）：`idempotency_key`（D1 幂等键，含 2026-09-24 加的 `course_fp`）·
`job_id`（`sha256(键)[:16]`）· `collision_rows`（发布端撞名守卫的**唯一判据**）+ `SIBLING_MANIFEST_GLOB`
（扫描面的单一定义）。`common/protocol.py` **1482 → 1390 行**，留 `X as X` 门面 ⇒ 历史 import 一行不改。

**为什么**：身份是「两个 store 会不会抢同一份活」的唯一答案，与「manifest 字段合不合法」
「字节怎么编解码」是三个不同的问题；`collision_rows` 是**发布那一刻**的哨兵，谁改发布链都要读它，
但它**不需要**协议校验面。依赖面 = **stdlib only**（`hashlib`/`json`/`pathlib`）⇒ **零环、零反向依赖**。
**刻意留守** `job_seed`（D5 **种子**，不是身份——recon 里它是孤立节点，与身份簇零同族边）。

**判据（recon 实测）**：`idempotency_key` / `job_id` / `collision_rows` 正是 **组2（3 节点）**；本刀与
⑥ 不同之处：这里**不需要**带 `errors`（这 3 名一个不抛 `ProtocolError`）⇒ 更单纯的叶子。

**验证**：**逐字节对账**（`tmp/verify_job_identity_exact.py` ⇒ `BYTE-EXACT`）· 门禁 nn **3167 → 3176
passed / 3 skipped / 0 failed**（+9 = 新守卫 `tests/common/test_job_identity_split.py`）；ruff + mypy（510 文件）绿 ·
根 `bun run check` **2181 pass / 0 fail**。守卫五条：定义唯一 / 依赖面闭集（**不得 import `common.protocol`**）/
不得碰上层包 / 门面对象恒等 / 身份语义（**键含 `course_fp`** · `job_id` 稳定 16 位十六进制 · 撞名守卫只在
「**完整键**相同 且 **别的 store**」明确命中（自课历史 / 不同键 / 缺键 / 坏 JSON / 扫不到根 = 不误伤））。

**违反后果**：在 `protocol.py` 里再实现一份搬走的名字 ⇒ 守卫红；`job_identity` 反向 import 门面 ⇒ 成环，守卫红。
（全文 → `docs/nn/engineering.md` §34。）

## §2026-09-27-goalnn-payload-split（2026-09-27，S5 第八刀：`common/protocol` 的语料归档面下沉 `common/payload`）

**决定**：把 `common/protocol.py`（**1390 行**）的**语料归档（payload）面**整块搬到新模块 **`common/payload.py`**
（213 行，**三段跨度共 10 名，逐字节不动**）：`PAYLOAD_NAME` / `PAYLOAD_LEGACY_NAMES` · `INIT_WEIGHTS_NAME` /
`PAYLOAD_PERTURB_NAME` / `PAYLOAD_XZ_PRESET` · `find_payload` / `_add_bytes` / `_extract_archive` /
`pack_payload` / `unpack_payload`。`common/protocol.py` **1390 → 1238 行**，留 `X as X` 门面 ⇒ 历史 import 一行不改。

**为什么**：这是**字节容器**面（一份语料怎么变成一个可传输的字节串）；与「manifest 字段合不合法」
「这份活是谁」「HTTP 体上限多少」是四件事。两处**事故复盘**的判据都在这里：① 判别**先 tar 后 zip**
（2026-09-21：EOCD 启发式把完好 tar.xz 误判成 zip ⇒ 毒包每 5 分钟复现、零告警空转 3.5h）；
② 解出**空包必须响亮**（判别反转后的新失败形态）。依赖面 = stdlib + `common.errors`（失败类型叶子）。

**刻意留守**：`TS_CODE_NAME`（**别的**产物：TS 运行时 zip 走独立 body 段，不进取 payload）·
`FAIL_NAME`/`FAIL_BODY_MAX`/`PRIORITY_BODY_MAX`/`PEEK_MAX`（HTTP 体上限）· `OFFLINE_*`（补传端点）·
`is_content_sha`/`blob_path`（**内容寻址 blob** 面，opt-blob-diet M2——与「归档怎么打包」不同）。

**验证**：**三段逐字节对账**（`tmp/verify_payload_exact.py` ⇒ `A/B/C` 均 `BYTE-EXACT`）· 门禁 nn
**3176 → 3187 passed / 3 skipped / 0 failed**（+11 = 新守卫 `tests/common/test_payload_split.py`）；ruff + mypy（512 文件）绿 ·
根 `bun run check` **2181 pass / 0 fail**。守卫五条：定义唯一 / 依赖面闭集+无环 / 不得碰上层包 /
门面对象恒等 / 归档语义（tar.xz 往返 · **legacy zip 双读** · **空包响亮** · 坏容器＝内容决定性 `ProtocolError` ·
显式扰动换字节且读侧兼容 · `find_payload` 新名优先回退旧名）。

**违反后果**：在 `protocol.py` 里再实现一份搬走的名字 ⇒ 守卫红；`payload` 反向 import 门面 ⇒ 成环，守卫红。
（全文 → `docs/nn/engineering.md` §35。）

## §2026-09-27-goalnn-manifest-split（2026-09-27，S5 第九刀：`common/protocol` 的 manifest/rollout 校验面下沉 `common/manifest`）

**决定**：把 `common/protocol.py`（**1238 行**）的 **job manifest 契约面**整块搬到新模块 **`common/manifest.py`**
（547 行，**四段跨度共 38 名，逐字节不动**）：① `PROTO`；② 角色**词汇** `ROLE_OFFLINE`/`ROLE_ONLINE`/`ROLES`/
`ROLE_FIELD`；③ manifest schema（`MANIFEST_REQUIRED`/`_OPTIONAL_DEFAULTS`/`_BC_EXEMPT`/`_BC_EXTRA`/
`_ITER_EXTRA`/`_RUN_EXTRA`/`_KINDS`）+ `KIND_ROLES` + `role_of` + TS·plan 产物契约（`ROLLOUT_SCRIPT`/
`EVAL_SCRIPT`/`ROLLOUT_SCRIPTS`/`ITER_OUT_REL`/`ITER_NODE_LABEL`/`PLAN_NAME`/`PLAN_PROTO`/`RUN_NODE_LABEL`/
`RUN_MAX_ITERS_HARD_CAP`）；④ `normalize_manifest` + rollout 规格校验（`validate_rollout_spec`/`_iter_flag_value`/
`_iter_rel_path`/`_ITER_PATH_FLAGS`/`ROLLOUT_SPEC_DEFAULTS`/`_WIN_DRIVE_RE`）+ shard 命名/语料指纹
（`shard_name`/`parse_shard_name`/`data_fp_entries`/`data_fp`/`d14_corpus_match`/`iter_declared_entries`/
`iter_expected_data_fp`/`DataFpEntries`）。`common/protocol.py` **1238 → 848 行**，留 `X as X` 门面 ⇒ 历史 import 一行不改。

**为什么**：这是「这份活**要什么**」的声明面与校验面。读者要加一个 manifest 字段 / 一种 kind，只需看一个文件；
与「字节怎么编解码」（⑥）·「这份活是谁」（⑦）·「语料怎么打包」（⑧）·「**结果**合不合法」都分了家。

**⚠ 关键切分判断（没按相邻切）**：① **角色只搬「值域」** —— `ROLE_OFFLINE`/`ROLE_ONLINE`/`ROLES`/`ROLE_FIELD`
随行走（它们是 `manifest.role` 的词；`KIND_ROLES`/`ROLES` 都要它们）；而 `ROLE_HEADER`/`ROLE_HEADER_VALUE`
（**HTTP 头名**）与 `role_from_header`（会话角色解析）**留守** `protocol`——那是**请求头**面。② `BLOB_*`/`blob_path`
留守（内容寻址 blob）；③ `PROTO` 随行（全仓只被 `normalize_manifest` 用）。

**验证**：**四段逐字节对账**（`tmp/verify_manifest_exact.py` ⇒ `P/R/M/D` 均 `BYTE-EXACT`）· 门禁 nn
**3187 → 3200 passed / 3 skipped / 0 failed**（+13 = 新守卫 `tests/common/test_manifest_split.py`）；ruff + mypy（514 文件）绿 ·
根 `bun run check` **2181 pass / 0 fail**。守卫五条：定义唯一 / 依赖面闭集+无环 / 不得碰上层包 /
门面对象恒等 / **契约语义**（必填 fail fast · proto/kind 红线 · **bc 与 iter 的 mode 互斥** · role 拼错拒收 ·
`role_of` 字段优先+kind 兜底 · **kind→role 穷举** · shard 名往返 · `data_fp` 与**声明集**同源 · rollout argv
白名单/相对路径/重复局/workers=0 全部拒收）。

**违反后果**：在 `protocol.py` 里再实现一份搬走的名字 ⇒ 守卫红；`manifest` 反向 import 门面 ⇒ 成环，守卫红；
把 `ROLE_HEADER`/`role_from_header` 也搬进 `manifest` ⇒ 守卫红（请求头面不属它）。
（全文 → `docs/nn/engineering.md` §36。）

## §2026-09-27-goalnn-config-faces-split（2026-09-27，S5 第十刀：`rl/config.py` 的文件面/类面/解析面下沉 `rl/config_file` + `rl/course_spec` + `rl/course_resolve`）

**决定**：把 `rl/config.py`（**1566 行**）按「独立所有者 + 独立触发条件」切成三面，三个新模块（**逐字节不动**）：
① **文件面** `RL_CONFIG_ENV` · `rl_config_path` · `read_rl_config_file` → **`rl/config_file.py`**（55 行，stdlib + `dist_common`）；
② **类面** `CourseConfig`/`GatesSpec`/`StageSpec`/`RewardBlock`/`PpoScheduleEntry`/…（25 名，17 段）→ **`rl/course_spec.py`**（953 行）；
③ **解析面** `CURRICULA_DIR`/`LEVELS_DIR`/`resolve_level`/`load_course`/`resolve_course`/`course_from_args`/
`resolve_state_init_bank`/`_resolve_courses`/`_LEVEL_ENV_KEYS`（9 名，9 段）→ **`rl/course_resolve.py`**（146 行）。
`rl/config.py` **1566 → 604 行**（−61.4%），留 `X as X` 门面 ⇒ 全仓 40+ 处 `from rl.config import …` 一行不改。

**为什么**：三面的触发条件互不相干——改「本机那份永不入库的配置在哪/怎么读」只看 `config_file`；加一个课程字段/门 kind
只看 `course_spec`；换课程目录布局/加载规则只看 `course_resolve`。此前它们挤在一个文件里，`config.py` 的读者（启动校验、
语料身份、配额、展示、执行面）要在一千五百行里穿行。

**⚠ 关键切分判断**：
- **类面 + 解析面同刀**（一个环的耦合体，先例 ⑥ errors+wire_codec）：解析面静态依赖类面（`load_course` 造 `CourseConfig`）；
  类面唯一反向边是 `GatesSpec` 跨课门校验调 `_resolve_courses` ⇒ 段内插**函数内**延迟 import（顶层会成环）。
- `resolve_state_init_bank` 随**解析面**（路径存在性解析，读 `CURRICULA_DIR`）；`_default_lives` 随**类面**（只喂 `CourseConfig.reward_spec`）。
- `RLConfig`/`validate_args`（启动校验）· `corpus_identity_fp`（语料身份）· `apply_course`/`course_key_of`/`resolve_course_quota`/
  `stage_json_for_args`/`course_cli_conflicts`/`args_rollout_overrides`/`echo_config`（执行面/配额/展示）**留守**。

**两处测试指针改指**（模块全局的 setattr 必须打在新家，打门面只是改一份副本、静默失效）：
`tests/biz/test_ladder_factory.py` 的 `"rl.config.LEVELS_DIR"` → `"rl.course_resolve.LEVELS_DIR"`；
`tests/worker/test_state_init.py` 的 `import rl.config as cfg` → `import rl.course_resolve as cfg`。

**验证**：逐段逐字节对账（三脚本 ⇒ 1+17+9 段全 `BYTE-EXACT`；类面 `GatesSpec` 段 1 处**既知插入**=延迟导入）·
门禁 nn **3200 → 3224 passed / 3 skipped / 0 failed**（+24 = 三守卫）· ruff + mypy（**520** 文件）绿 ·
根 `bun run check` **2181 pass / 0 fail** · check-decisions 527 ids。守卫：定义唯一 / 依赖面闭集 + 无环（**顶层禁反向边**）/
门面对象恒等 / **模块全局是活读取点**（monkeypatch 指针依据）· 契约语义（gates 未知 kind · 跨课引用找不到 ·
level 注入 + 重复声明拒收 · 冻结字节 / 互斥 / 无参 None · 路径三分基准 · 坏 JSON 空 dict）。

**违反后果**：三新模块反向 import `rl.config` ⇒ 成环，守卫红；把 `CURRICULA_DIR`/`LEVELS_DIR` 的 monkeypatch
打回门面 ⇒ `test_course_resolve_split` 红（读取点在实现模块）。
（全文 → `docs/nn/engineering.md` §37。）

## §2026-09-27-goalnn-dist-clusters-split（2026-09-27，S5 第十一刀：`dist_common.py` 的 shard 面与权重下发账本下沉 `dist_shard` + `dist_weights_ledger`）

**决定**：把 `dist_common.py`（**1503 行**）里两个小簇按「独立所有者 + 独立触发条件」搬出（**逐字节不动**）：
① **shard 面**（7 段 7 名）——三份 shard 清单 + `BC_COLLECTOR` + `_shard_files_for` / `validate_result` / `write_shard`
→ **`dist_shard.py`**（137 行，stdlib-only）；② **权重下发账本**（2 段 6 名）——`_WEIGHTS_PUSHED` +
`weights_push_cache_reset` / `note_weights_pushed` / `forget_weights_node` / `weights_already_pushed` /
`partition_weights_nodes` → **`dist_weights_ledger.py`**（74 行，零 import 叶子）。`dist_common.py` **1503 → 1373 行**
（−130；diff +53/−183），留 `X as X` 门面 ⇒ 全仓 `dist_common.validate_result(...)` / `dist_common.partition_weights_nodes(...)`
与 `from dist_common import …` 一行不改。

**为什么**：两个簇的触发条件互不相干——「远端结果包到了 → 先验后落盘」只看 `dist_shard`；「同 it 补波要不要重发权重」
只看账本。此前它们与 HTTP / codeHash / 远控重启护栏 / 中断语义挤在同一个 1503 行文件里。

**⚠ 关键切分判断**：
- **账本按 6 名搬、不按 recon 的 2 节点搬**：`recon_god.py` 的组3 只圈出 `partition_weights_nodes`+`weights_already_pushed`，
  但二者读的 `_WEIGHTS_PUSHED` 是**共享模块全局**（记/忘/清写它）⇒ 只搬两名会让孩子**反向 import 门面**（顶层成环）或读到副本。
- `import base64` 随实现走（留守代码零引用；ruff `unfixable=["F401"]` ⇒ 手动删）；`BC_COLLECTOR` 单独搬、注释一分为二
  （`BC_MODE` 留守 / collector 判据随行）；`BC_MODE`/`BC_WVER` 留守（任务模式标识，`bc_dispatch` 用）。
- 组织边是留守 → 块的**单方向**：`post_weights_parallel` / `refresh_weights` 调 `note_weights_pushed`/`forget_weights_node`，
  由门面转发承接（与⑨ manifest「两向皆空」的纯净叶子不同；**块 → 留守两向仍空**）。

**patch 目标零迁移**：全仓既有调用点（`rl/dispatch` · `rl/eval_dispatch` · `rl/queue_local` · `rl/batch_runner` · 6 个测试文件）
全按 `dist_common.X` 属性或 `from dist_common import X` 解析 ⇒ 打门面仍生效；账本 dict 是**同一个对象**
（`dist_common._WEIGHTS_PUSHED is dist_weights_ledger._WEIGHTS_PUSHED`，不是副本）。

**两处名单面补登记**（不登记 = 判据对它们瞎着）：`tests/test_layering.py` 的 `L0_TOP_MODULES` += `dist_shard` / `dist_weights_ledger`；
`nn-training/pyproject.toml` 的 `py-modules` += 两名（否则 editable 安装下 `import dist_common` 直接 ImportError——
测试靠 conftest 的 sys.path，看不出来）。

**验证**：**九段逐字节对账**（`tmp/verify_dist_clusters_exact.py`，跨度表独立复写 ⇒ 7+2 段全 `BYTE-EXACT`；`dist_shard`
1 处既知插入=collector 注释行）· 门禁 nn **3224 → 3243 passed / 3 skipped / 0 failed**（+19 = 两守卫）；ruff + mypy
（**520 → 522** 文件）绿 · 根 `bun run check` **2181 pass / 0 fail** · check-decisions 529 ids。守卫五条：定义唯一 /
依赖面闭集（+无环）· 不得碰上层包 · 门面对象恒等 · **模块全局是活读取点** · 契约语义（三态清单选择 · 七条拒收 +
BC 败局跳过 · 落盘只写清单内文件 + `manifest.json` indent=2 单次写 · v1/v2 双模 · kind 分桶 · forget 范围/计数 · reuse/need 拆分）。

**违反后果**：在 `dist_common.py` 里再实现搬走名 ⇒ 守卫红；`dist_shard`/`dist_weights_ledger` 反向 import 门面 ⇒ 成环，守卫红；
把账本按 2 名切（只搬查询/拆分）⇒ 必须造上向边或状态副本，守卫红。
（全文 → `docs/nn/engineering.md` §38。）

## §2026-09-27-goalnn-eval-yield-split（2026-09-27，S5 第十二刀：评估「让位/份额（尾巴）策略」两处一起切 → `rl/eval_yield.py`）

**决定**：按「独立所有者 + 独立触发条件」把评估的让位/份额（尾巴）策略做成**独立所有者**——从**两处一起**切：
① `rl/eval_local.py` 的 L110–L226（**117 行**：`EVAL_LOCAL_SLOTS_DEFAULT` / `EVAL_LOCAL_RELEASE_GRACE` /
`EVAL_INFLIGHT_GRACE_SEC` / `EVAL_JOIN_SOFT_SEC_DEFAULT` / `EVAL_LOCAL_EARLY_EPOCHS_DEFAULT` + 7 个判决函数
`eval_join_soft_sec` / `eval_tail_overran` / `eval_local_early_epochs` / `local_gate_release_plan` / `early_epoch_reached` /
`hold_for_local` / `release_local_gate_if_starved`，含两段族注释）**逐字节**搬进新模块 `rl/eval_yield.py`；
② `rl/eval_dispatch.py`（`EvalDispatcher.run`）里**只有实现、没有名字**的三个内联判决点提出成
`reserve_local_slots` / `local_release_due` / `inflight_grace_cap`（**原式逐项等价**），派发器改为调用它们。
`rl/eval_local.py` **573 → 497 行**，留 `X as X` 门面（12 块）⇒ 旧 import 路径与历史调用点一行不改；
判据的三个读者（`rl/eval_dispatch` / `rl/loop_eval` / `rl/batch_runner`）改从 `rl/eval_yield` 取。

**为什么**：这些判决的触发者是**边界事件**——派发那一刻的份额分档（immediate / last_epoch / on_join）· PPO 收官的
join 与交棒 · 下一轮 rollout 收官时的尾巴收拢 · 窗口到期后的在飞落账宽限——与「怎么在本机跑一局评估」
（子进程 / 看门狗 / 账本）**零共享状态**。此前派发器为一条判据就得经 `rl.eval_local`（573 行运行器）取它，
批执行面（B 层）为一条份额缺省同理。分开后新模块是**零依赖叶子**（`__future__` 之外零 import），两侧直接依赖。

**⚠ 关键判断**：① **「两处一起」不是两刀**：只搬 `eval_local` 那份，`EvalDispatcher` 里三个内联表达式仍是
第二份实现（判决点没有名字 = 本刀要消灭的东西）；只提公式不搬函数，判据仍留在运行器里。② 派发器**仍**要
`rl.eval_local`（执行面：`run_local_eval_game` / `eval_row` / `eval_done_keys` / `EVAL_TASK_ATTEMPTS`）——
切的是判据面，不是执行面。③ `EVAL_TASK_ATTEMPTS`（单局重试上限）**留守**运行器：它是执行重试，不是让位份额。
④ 门面块放在 `eval_local` 顶部门面区（`rl.eval_track` 之后）：isort 把「连续 import 区」当一个排序域，
紧贴 `eval_rows` 门面块会要求把既有的 17 块整段挪位（零既有块挪位是这条放置的理由）。⑤ 既有守卫
`test_eval_track_split.py::test_eval_local_kept_the_execution_surface` 原先钉「尾巴策略留守」= 上一轮的反判据
⇒ 本刀**改判**并收窄到执行面（带日期说明）；`test_loop_eval_split.py` 的 DI 名单与 `test_batch_runner_split.py`
的 import 闭集同样补登记。

**验证**：逐段逐字节对账（`tmp/verify_eval_yield_exact.py`：搬入块与动刀前快照同一 + **HEAD 版本交叉验证**同 117 行
逐字节存在）· 门禁 nn **3243 → 3262 passed / 3 skipped / 0 failed**（+19 = 新守卫）· ruff 全过 · mypy **526** 文件绿 ·
根 `bun run check` **2181 pass / 0 fail** · check-decisions **530 ids**。守卫：定义唯一 / 执行面反向留守 / 依赖面闭集
（**零依赖叶子**）/ 无反向 import / 门面对象恒等 / 边缘重指 + 判决点迁移（旧表达式必须消失）/ 语义（让位真值表 ·
无节点开闸 · 放行档与提前点 · 越窗与站等旋钮 · 三条新公式逐项等价）。

**违反后果**：`rl/eval_yield.py` import `rl.eval_local`（或任何仓内模块）⇒ 守卫红；在 `eval_local.py` 里再实现
搬走名 ⇒ 守卫红；判决点回写成内联表达式 ⇒ `test_eval_dispatch_inline_formulas_are_gone` 红。
（全文 → `docs/nn/engineering.md` §39。）

## §2026-09-27-goalnn-plan-handoff-split（2026-09-27，S5 第十三刀：`plan_run` 18 节点交接面下沉 → `remote/plan_handoff.py`）

**决定**：按 `plan/nn-training-refactor.md` §5.7.4 落刀——`remote/plan_run.py`（**1090 行**）的**交接面**
18 名（面日志 + 校验 2 · `RunContext` 1 · 取包播种 9 · 评估装配 3 · 常量 3）整块下沉为
**`remote/plan_handoff.py`（630 行，L2）**；`plan_run` **1090 → 577 行**（−513），只余驱动引擎 + 18 名 `X as X` 门面。
**4 跨度逐字节纯搬**（562 行 / 26386 B）：A1 `TS_TREE_DIR`（含注释 2 行）· A2 `TS_CODE_ZIP_NAME`（3 行）·
R2 `[83,543]` 461 行（日志/校验/`RunContext`/取包播种 9 件/评估装配 3 件/`EVAL_ALTERNATE_WAIT_SEC`）· R3 `[708,803]` 96 行；新头 = 既知插入。

**⚠ 关键判断**：① **18 而非最小闭包 17**：纳入 `verify_plan_file`（它只引 `_log_default`；加入后块→外仍为空，
已实测）⇒「交接面」语义完整：**校验 → 取包 → 上下文 → 装配**。② 评估生命周期被劈两半（装配走、驱动 + 收线留守）
= 闭包代价，槽位契约（`ctx.eval_*` 谁写谁读）写进新模块 docstring。③ `EVAL_ALTERNATE_WAIT_SEC` **双命名空间**：
`_maybe_cloud_eval`（留守）读 `plan_run`、`_setup_cloud_eval`（搬走）读新家——既有 patch 点（`test_offline_eval_wiring`）
**零迁移**，新守卫把事实钉死。④ 依赖单向：新模块零引用 `plan_run`（块→外为空），引擎侧 9 个留守节点引用交接面名
（7×`RunContext` + `_maybe_cloud_eval` + `run_plan_job`）。⑤ `_setup_cloud_eval` 内 `from remote.offline_eval import …`
是**函数内延迟 import**（随跨度搬走）：offline_eval 站 L1 ⇒ L2 的 `plan_handoff` 依赖方向合法（守卫白名单登记）。

**分层**：新模块 **L2**（deps = `common.logutil`/`common.protocol`/`remote.artifacts`/`remote.bundle`/`platform_utils`/`rl.plan`/
`remote.offline_deliver`(L1)/`remote.offline_eval`(L1，延迟)）；`plan_run` **2 → 3**；`tests/helpers/remote_dag.py` 三处同步
（表项 + L2/L3 段文字）——DAG 是**自动秩校验**（先红后绿）；`worker`(L5) / `run_loop`(L6) 不变、无环。

**名字契约**：`plan_run` 留 18 名 `X as X` ⇒ `remote/run_loop.py` 的 13 个 import 块与全仓调用点一行不改；
门面链 `run_loop ← plan_run ← plan_handoff`（`is` 恒等仍成立）。

**验证**：逐段逐字节（`tmp/verify_plan_handoff_exact.py`：4 段全 BYTE-EXACT + 原家 ABSENT + 18/18 门面齐备）·
新守卫 `tests/remote/test_plan_handoff_split.py` **8 例**（定义唯一 / 依赖面闭集 / 禁反向 import / 门面恒等 /
功能性 `verify_plan_file` 四道门 / 双命名空间事实 + 写入面四槽守恒）· 改判 `test_plan_run_split`（11 例：18 名拆分 +
引擎 14 名反向留守）· `test_worker_offline_cap`（import 改指新家）· `remote_dag` 同步 · nn 门禁
**3262 → 3271 passed / 3 skipped / 0 failed**（+9 = 新守卫 8 + 改判 1）· ruff 全过 · mypy **526 → 528** 文件 ·
根 `bun run check` **2181 pass / 0 fail**。

**违反后果**：`plan_handoff` 反向 import `plan_run` / `worker` / `run_loop`（含延迟）⇒ 守卫红；在 `plan_run` 里
再实现门面名 ⇒ 守卫红；双命名空间任一侧被合并 ⇒ `test_dual_readers_read_their_own_home` 红。
（全文 → `docs/nn/engineering.md` §40。）

## §2026-09-27-goalnn-offline-deliverable-split（2026-09-27，S5 第十四刀：offline_boot 交付面 + standalone 运输面 → `remote/offline_deliverable.py`）

**决定**：把 `remote/offline_boot.py`（1801）的**交付面**（13 节点 / 179 行干净闭集：交付三函
`package_deliverable`/`_partial_last_it`/`package_partial` + 课程路径链 5 名 + 4 常量 + `_COURSE_NAME_RE`）
整块搬进 **standalone 兄弟文件** `remote/offline_deliverable.py`（227 行，stdlib-only ⇒ L0）；
原家 **1801 → 1647**（−154）。**2 跨度逐字节纯搬**（198 行）：`L94–100`（4 常量 + 3 条 `#:` 注释）·
`L1008–1198`（`_COURSE_NAME_RE` 注释起 → `package_partial` 的 `return made`，191 行）；新模块头 = 既知插入。

**⚠ 本刀是 S5 首例「运输面 > 纯搬」**（前十三刀都是纯搬 + `X as X` 门面、调用点零迁移；本刀的**装载方式**必须改）：
`offline_boot` 在 `remote_dag.STANDALONE_BOOT_MODULES` 里 ⇒ `TOP["remote.offline_boot"]` 必须为空——顶层
`try/except` 里的 `from remote.offline_deliverable import …` 仍算顶层边（`Try` 不改变 AST 深度）⇒ **必红**；
而顶层 `from offline_deliverable import …` 在仓库/包内形态没有那个顶层模块名 ⇒ 也红。**所以**：
① **懒装载** `_load_deliverable()`：`importlib.import_module` 按 `("offline_deliverable", "remote.offline_deliverable")`
顺序（**引导兄弟优先** = 与会话刚刷新的 offline_boot 同源；2026-09-25「看着新跑着旧」教训；与 `_load_tailscale_boot`
的 remote-first 有意不同），两者都缺 ⇒ `ImportError` 点名缺谁；
② **门面** = PEP 562 `__getattr__`：只转发**闭集 13 名**，未知名照常 `AttributeError`——「名字是契约，位置不是」
（notebook 取回格 `_ob.package_partial`、`offline_boot.courses_of` 等 ~15 处调用点零迁移）；
③ **内部 6 处调用点**（`run_one_course`×3 / `_queue_work_dir` / `resolve_courses` / `run`）改走
`_load_deliverable().x(…)`（不经门面——模块全局是活读取点）。

**拉取列表**（notebook 3 处）：主引导格名单 += `offline_deliverable.py`；`sys.modules.pop` 名单 +=
`offline_deliverable`/`remote.offline_deliverable`（同 kernel 二次 Run 的新鲜度）；取回格 raw 兜底改**双文件循环**。
失败语义：老 notebook（名单缺新文件）⇒ 训练照跑、**交付格**响亮 ImportError（故意：训练面不陪葬）。
**登记**：`remote_dag.LAYERS` += `remote.offline_deliverable: 0`（offline_boot 仍 **L7**——惰性装载不产生 AST 可见边）
+ `STANDALONE_BOOT_MODULES`（DAG + `test_common_layer` 两处）。**patch 面**：`test_offline_local_first` 的
`setattr(offline_boot, "courses_of", …)` 自始惰性（`run()` 路径不读它）⇒ 零迁移；纪律：要影响交付面必须打
`offline_deliverable.<名>`（门面只改副本）。

**验证**：逐段逐字节（`tmp/verify_offline_deliverable_exact.py`：A/B 各 ×1 + 原家 13 处定义/常量 ABSENT +
门面/调用点/口径到位）· 新守卫 `tests/remote/test_offline_deliverable_split.py` **12 例**（定义唯一 / stdlib 闭集 /
禁反向 import / 门面恒等 / 门面闭集 + 笔误不吞 / 装载顺序 + 命名语义 / notebook 名单 ×2）· 改判
`test_offline_boot`（exec 守卫升级为**引导文件集形态** + 反例「兄弟缺失 ⇒ 响亮 ImportError」；双生常量守卫改指新家）·
`test_offline_notebook` 名单 +1 · `test_common_layer` +1 · `remote_dag` 两处 · nn 门禁 **3271 → 3285 passed /
3 skipped / 0 failed**（+14）· ruff 全过 · mypy **528 → 530** 文件 · 根 `bun run check` **2181 pass / 0 fail**。

**违反后果**：往 `offline_boot` 顶层加兄弟/remote import ⇒ DAG standalone 守卫红；门面名单 ≠ 搬运名集合 ⇒
`test_facade_closure_is_exactly_the_moved_names` 红；notebook 名单缺文件 ⇒ `test_notebook_fetches_and_pops_the_sibling` 红。
（全文 → `docs/nn/engineering.md` §41）

## §2026-09-27-goalnn-gate-faces-split（2026-09-27，S5 第十五刀：`gate_check` 求值器接口 —— 输入面 + 判决面成家）

**决策**：1412 行 `rl/gate_check.py`（`recon_god` 口径「1 组 31 节点巨团」；引用图实测 **47 节点 = 46 单团 + 1 孤立**
（`sum_train_samples`））一刀切两面、成两个新家（纯搬 + `X as X` 门面，S5 收官刀）：

- **输入读数面 15 名**（`EvalRow`/`BudgetInfo` 模型 + `_row_from_summary`/`_num`/`normalize_rows` 行归一化 +
  `read_trend_rows` + `first_*`/`count_*`/`sum_*` 事件扫描 + `load_override` + `_TS_FMT`/`_OVERRIDE_VERDICTS`/
  `GateOverrideError`）→ **`rl/gate_inputs.py`**（379 行，**stdlib-only 叶子**）；
- **判决项面 22 名**（`_Partial`/`_Ctx` + `_mean`/`_slope`/`_sustain`/`_window`/`_halves` + 11 个 `_eval_*` +
  `_route_by_completion` + 注册表 `_JUDGES_NO_TEACHER` + **判决项接口** `_eval_one`）→ **`rl/gate_judges.py`**（572 行）；
- `rl/gate_check.py` **1412 → 622**（引擎：`evaluate` 两趟 + ADVANCE 附加条件 + CLI + 全量 37 名门面）。

**为什么（判据）**：原反判据「按链切不动 ⇒ 必须先设计求值器接口、是真设计改动」经引用图量测**收敛**——
判决项在引用图上本就是**干净闭集**（25 节点 / 588 行，块→外为空、外部边仅 4）；「独立求值一项」的路径
（`only_kinds=("duty",)`）已由 `evaluate` 暴露、`loop_guards_gate` 在用。真正的设计点只剩**共享输入模型的归属**：
`EvalRow`/`_row_from_summary`/`_num`/`BudgetInfo` 被判决面与读数面共用 ⇒ **归输入面**、判决面 import 之 ⇒
**DAG 单向 `gate_inputs ← gate_judges ← gate_check`**（无环；运行期零 `rl.config`——判决面只 TYPE_CHECKING
取 `GateRule`/`GatesSpec` 类型、`_lazy_config` 仍归引擎，红线随家）。

**刀口**：**10 跨度逐字节**（输入面 5 段：`L85–91` / `L96–98` / `L103–126` / `L128–166` / `L277–556`；
判决面 5 段：`L93–94` / `L256–274` / `L559–1045` / `L1050–1057` / `L1249–1270`）+ 新头 + 门面（既知插入）。
既知改动：① docstring 加分层指针；② TYPE_CHECKING 块删（`GatesSpec` 只由 `evaluate` 内 `_lazy_config()` 解包）；
③ 清 `math`/`Callable` 空转 import；④ 缝空行归一到 ≤2。

**被否决备选**：① 只搬判决面、把它拉入的行模型一并带走（`EvalRow`/`_row_from_summary`/`_num`/`BudgetInfo`）⇒
方向倒置（读数面反过来 import 判决面），且锁死输入面独立；② 只搬输入面 ⇒ 计划点名的判决项接口未动、大块收益
留在 1412 行里；③ 按链搬注册表（8 行）/ CLI（闭包 46/47 节点 ≈ 文件本体）；④ 升名公开化
（`_eval_one` → `eval_rule` 等）⇒ 纯搬优先：接口由守卫钉（入口/覆盖/方向三件），不做无谓改名与签名改动。

**验证**：逐段逐字节（`tmp/verify_gate_faces_exact.py`：10 跨度各 ×1（原文 sha `233caa57…`）+ 原家 40 名定义
与 `GateRule`/`math` 零残留 + 门面 37 名 `is` 恒等 + 新家导入 `rl.config`/torch/numpy 均不入 `sys.modules`）·
新守卫 `tests/worker/test_gate_inputs_split.py` **12 例** + `tests/worker/test_gate_judges_split.py` **11 例**（定义唯一 /
依赖闭集 / 禁反向 / 门面恒等 / 判决项接口（注册表 6 键 + 5 分支 = 11 kind + 兜底 dormant）/ 运行期纯净子进程 /
不进 `remote_dag.LAYERS` / 读数与分流语义）；改判 `test_gate_check::test_module_import_has_no_torch_numpy`
扩面（三模块 + `rl.config`）。nn 门禁 **3285 → 3308 passed / 3 skipped / 0 failed**（+23）· ruff 全过 · mypy
**530 → 534** 文件 · 根 `bun run check` **2181 pass / 0 fail** · check-decisions **533 ids / 553 entries**。

**违反后果**：往 `gate_judges` 加 `rl.gate_check` import（哪怕 TYPE_CHECKING）⇒ 方向守卫红；往 `gate_inputs`
加任何仓内 import ⇒ 叶子守卫红；注册表与 `_eval_one` 分支的 kind 覆盖漂移 ⇒ 接口守卫红；门面名单 ≠ 搬运名集合
⇒ 恒等守卫红。（全文 → `docs/nn/engineering.md` §42）

## §2026-09-28-goalnn-python-loc-budget（2026-09-28，用户指令「给 python 门禁加一个测试：python 源文件 loc 必须 < 1000 行（tests/e2e · offline_boot 除外，不包括注释/空行/doc）」）

- **背景**：S5 把九个 1000+ 行的神模块拆成 19 个可独立依赖的模块（`plan/nn-training-refactor.md` §5.7），
  但那是一次性人力侦察——没有任何断言拦着下一个模块长到 1400 行（`rl/gate_check.py` 在拆之前就一直是 1412 行）。
- **备选与否决**：① 超限文件加进豁免名单、不设上限——否（巨型文件可无限增长，等于没有规则）；
  ② 棘轮：豁免文件登记当前 LOC、只准不涨——否（用户裁定测试层整体豁免即可，行数不是测试的度量）；
  ③ 本轮把 `tests/remote/test_remote_iter.py`（1309）/`tests/remote/test_remote_ppo.py`（1258）拆到 <1000——否
  （它们的行数来自场景枚举，压它等于少测）。
- **决定**：新增 `nn-training/tests/test_python_loc_budget.py` 进 python 门禁——全仓 `.py`（git 跟踪 +
  未跟踪未忽略）的 **LOC = 物理行 − 空行 − 纯注释 − docstring** 必须 `< 1000`（**≥1000 即红**）；
  `tests/` · `e2e/` 两层与 `remote/offline_boot.py`（standalone 运输单元，§5.7.3 实测拆不开）豁免；
  豁免目录名只许命中那两层、豁免文件降到阈值下即红（豁免会过期）。
- **违反后果**：`bash tools/githook/nn-python-gate.sh` 红，消息点名文件与超额行数；门禁不过 ⇒ 不许交付。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-28-goalnn-python-loc-budget`
## §2026-09-27-goalnn-r2-event-gate（2026-09-27，x2 事件门契约：γ 保 per-step + 交棒首段禁事件 + Δt≥3）

**来历**：新纪元 R2 包（`plan/new-era-stop.plan.md §6 R2`）给 x2 rung 预注册「事件驱动决策」——
决策门从「均匀 K」扩成「均匀 K ∪ threat-ONSET 沿（+最小间隔闸）」，落地前有三件**不能就近表达**
的裁决：折扣语义、与 state-init 起始分布的共存、决策间隔上界（hy P0-3/P0-6 指出的三条）。
实施记录与基线数字在同 plan 的「R2 实施记录」节。

**决定**（三项，一次定死）：
① **γ 保持 per-step**（`nn-training/rl/config.py:974` γ=0.995），**不**切半 MDP `γ^Δt`。换算声明：
   视界 = 200 决策步 × avg-Δt；事件门实测（x20-noexplore it99 × ladder-c20 四局）
   avg-Δt = **9.79–9.94** tick ⇒ 视界 **1958–1988** tick（相对均匀 2000 漂移 **−0.6%…−2.1%**）；
   最小间隔闸 minGap=3 给出硬下界 600 tick（实测未逼近，minDt ∈ {3,4}）。事件密度若因 K 收紧/
   电平化而显著上升，**重议**本条（`np_core.py::gamma_step = gamma ** dt` 已具备半 MDP 能力）。
② **state-init 共存 = 方案 i**：银行切点规则**不变**（仍 `t % K == 0`），交棒 tick 恒是决策 tick
   （决策流首个候选恒放行），且**交棒后第一个 K 段禁 threat 事件**——快照只存当前态、不存
   `prevThreat` 历史，交棒瞬间的「首次入带」是伪造 onset。**不重出银行、不改切点规格**。
③ **Δt ≥ 3 是全门不变式**（含均匀边界）：事件后 1–2 tick 的 `t % K == 0` 边界会被跳过
   ——这是事件模式唯一会改「均匀子集」的路径，无事件的局逐字节不变（HEAD A/B 实测）。
   门读数（n / events / avg-Δt / min-Δt / max-Δt）落 shard manifest 与 `_eval_report.json`，
   **仅 `--decision-events` 时写**（缺省调用逐字节不变）。

**被否决**：首版就上 `γ^Δt`（同刀两个自变量，x2 相对 x1 的边际不可归因，hy P0-4）；
重出 state-init 银行（无理由的语料轮次，§15.1/§15.5）；把 threat **电平**写进谓词
（持续期每 tick 成立 ⇒ Δt=1 ⇒ 部署 6Hz→60Hz + 视界塌缩，hy P0-3）；给 BC label 导出器
顺带开事件（那是新语料轮次 + 与 x1 的 BC 教师口径分叉，属 x2 排产决定，不在 R2）。

**违反后果**：改折扣语义 ⇒ 必须新实验（新 `--out`/`--traj` + 新 DECISIONS，§15.5），
不得与 x1/x2 的读数混算；把电平/无条件事件写进 `decisionDue` ⇒ 部署侧前向频率与视界同时失控。

**落点**：`src/nn/decision-gate.ts`（唯一实现 + 读数）· `src/nn/obs-encoder.ts::decisionTick`
（`condition === 4`，可选 `prevThreat`）· 五处决策门（`policy-input` / `export-rl-rollout` /
`export-eval-game` / `export-nn-replays` / `record-games-video`）+ `divergence-probe` 全改调
`decisionDue` · `build-state-init-bank.ts` 共存契约 · 测试 `tests/nn/decision-gate.test.ts`、`tests/sim/eval-game-parity.test.ts`、`tests/state-init.test.ts`

## §2026-09-28-goalnn-console-metrics-harvest（2026-09-28，逐轮实际值抗轮转抄录：表列不再依赖「谁在屏幕上」）

**来历**：用户报障「两课并行，`h5a-earlydmg` 的击杀/残血/承伤全都有，`h5b-clean` 丢了一大半」。
查下来那五列（击杀/残血/承伤·杀/道具/耗时）**不是账本字段**，是 `/api/state` 扫 `it<N>` 现算后写
`<traj>/.pool-actuals-cache.json` 的派生值；而请求路径只算**查看课程**（`state-view.ts`），训练侧每轮
按 `keep_iters`（rl-config 缺省 3）删 `it<N>`（`rl/loop_guards_sweep.py::_rotate_cleanup`）⇒
「谁在屏幕上谁才有数」（实测两课缺口在时间轴上互补：h5a 缺 it22–25 ↔ h5b 抄录窗，h5b 缺 it7–17 ↔ h5a 抄录窗）。

**决定**：
① 控制台侧修：`snapshot-refresher` 新增抗轮转抄录（**独立 15s 节拍**），对**所有已开课**课
   （`training-enabled.txt`，与训练侧同一道闸）跑一次 `iters.readIterMetrics` 让缓存落盘；
② 两道闸：只抄在训课；账本 stat 签名（`size:mtime`）未变则跳过——**仅当上一轮把该课所有轮次都抄到**
   时才允许跳过（云机/回传腿的 `it<N>/per-game.json` 与账本行落地先后不保证，早一拍抄到 null
   必须重试，否则缺口被签名闸自锁 = 空白）；
③ 训练侧不动（不改 `keep_iters`、不加跨轮画像文件）：那是另一条腿的改动，留白另议。

**被否决**：调大这两课的 `keep_iters`（只拖长曝光窗口，`it<N>` 目录线性涨盘，且治不了下一门课）；
训练侧轮末把逐局画像写进跨轮文件（治本但要动账本口径 + python 侧 + 归档清单，超出本次报障范围）；
控制台侧不加签名闸（长驻服务 × 在训课数 × 天数：实测 27ms/课/拍的稳态整本账本解析纯浪费）；
抄录挂进慢快照那次 `run()`（会被节点 ping 1.2–2.5s 超时预算拖住，抄录赶不上轮转）。

**违反后果**：抄录节拍挪进慢快照刷新或改成「只抄查看课程」⇒ 本 bug 原样回来（列随轮转永久空白）；
去掉签名闸 ⇒ 在训课每 15s 解析一次整本账本 + 全量 eval 账本。

**落点**：`dashboard/src/server/api/snapshot-refresher.ts`（`harvestTrainingCourseActuals` /
`startSnapshotRefresher`）· `dashboard/tests/server-metrics-harvest.test.ts`（4 例）·
全文 `docs/nn/console.md` §20。
。

## §2026-09-28-goalnn-bc-retrial（2026-09-28，h5 四腿全阴后的 BC 翻案重测）

- **背景**：§32 死刑（held-out mean 0.00/按住上零开火）执行于毒标签下（0=keep 时训的）；
  B 案后同字节标签变对，h5 四腿全阴，BC 重测是最便宜的未执行项。
- **备选与否决**：沿用§32死刑直接转 DAgger —— 否，死刑依据（毒标签）已失效，跳过翻案等于用错判指导下一腿；
  重采人类语料 —— 否，61 shards/38564 在库且字节有效（stop 27.4%，fire 正例 28.6%），无需重采。
- **决定**：同语料原样重训 BC-ref2（镜像§32：kaggle cuda/60 epochs/val-split/从随机起，唯一变量＝语义修正）；
  held-out 重定义 255 对（c20-lives1，排除 61 demo 对，名单先 pin，原名单随 tmp 丢失不考古）；
  门沿用§32字面（对照同场重跑：low%≤70% 且 mean≥对照+2.0）；陷阱 P2-4/kk 小开/val 安慰剂预注册；
  成功填 x20-human-anchor 占位并另议开腿，失败§32死刑维持转 DAgger；启动由用户，agent 不启动训练。
- **违反后果**：跳过翻案转 DAgger ⇒ 若 BC 本可立锚，搜索预算全浪费在已解决的问题上；
  换数据/改门开训 ⇒ 翻案不可比，结论作废。
- 全文 → `docs/nn/experiments.md` §45。

## §2026-09-28-goalnn-b2-c20-qualify-2（2026-09-28，B2@c20 须先验密度，不自动继承）

- **背景**：h5e 处理量 ~0.5% 判事件无边际；R2 基线 c20 仅 1~4 次/局；c05/c20 开局同源且信用 c20 更难。
- **备选与否决**：B2@c20 直开 —— 否，预期读数与 h5e 同带，是换地方重复；
  先开 B1 信用窗 —— 否（暂缓：plan §4 在 idle 不动后第一分支是 B2，跳分支等于改排序，需另立决策，不在本条）。
- **决定**：c20 开事件腿须先验 onset 处理量 >5%（shard manifest 同口径）；否则 B2 归档，信用窗/DAgger 排队。
- **违反后果**：直开 ⇒ 再烧 80~100 轮横盘腿，读数与 h5e 同带。
- 全文 → `docs/nn/experiments.md` §46。

## §2026-09-28-goalnn-god-no-wait-3（2026-09-28，God 教不了等＋God-BC 等数据污染）

- **背景**：翻案死于吸引子后排 DAgger 专家；用户判定 God 的停全是目标冲突振荡（追敌 vs
  回防互拽），从不停（物理正确≠意图正确：null→0 字节对，dither-as-stop 教学毒）。
- **备选与否决**：God 当 DAgger/BC 专家教等 —— 否，教的是 rush＋把犹豫学成策略；
  “量一下停率再定” —— 否，停率数字分不清动机，省这趟 resim。
- **决定**：等的老师只认人类；God 数据禁用于一切教等用途（教打另议）；人类 DAgger 先估
  工具＋sessions 成本再立项；§32⑤ 的 God-bulk fallback 加同注。
- **违反后果**：用 God 数据教等 ⇒ 学出“冲突就冻住”，且 held-out 上停率涨得像学会了停、
  意图全错——最难查的一种 regression。
- 全文 → `docs/nn/experiments.md` §48。

## §2026-09-28-goalnn-fusion-last-4（2026-09-28，融合只能是终端步：PPO 洗等）

- **背景**：h3-dosage 融合提议；用户裁定 h3 上训出等也无价值——后续 rung 的 PPO
 （v4，H1–H5 实测停率恒 0）必洗掉，等是负收益行为。
- **备选与否决**：h3/c05 中段融合（dosage 彩票）—— 否，transient 无部署价值，
  连诊断价值都约等于零（共存性结论搬到 c20 要重做）；先改 rung 配方留住停 —— 否
  （h5a/h5b 已证价格侧买不到等，配方重开是另一个大项，不为融合前置）。
- **决定**：顺序倒置——先爬梯（PPO 只打）→ c20 终端融合（CE-dose 彩票先、
  DAgger-at-c20 重器后，门即 c20 真门）→ 直接部署，其间不再 PPO；
  DAgger 不同步在低 rung 做。
- **违反后果**：中段融合 ⇒ 训出即被洗（或从未训出），烧 GPU 且读数不可比。
- 全文 → `docs/nn/experiments.md` §49。

## §2026-09-28-goalnn-retention-ppo-5（2026-09-28，35关继续PPO：留存三件套）

- **背景**：c20 终端融合后 35关仍须 PPO（§49 保质期到部署为止）；“已有等会被洗”
  是外推（H 系列只证了学不会），v4 下等→赢链为正，硬 rung 上可能被留住。
- **备选与否决**：冻住 fused 权重 —— 否（35关要学新东西，冻住＝弃赛）；
  裸 PPO 续训 —— 否（弱 rung 中性漂移真会磨，且是静默腐烂）；
  先改 rung 配方 —— 否（h5a/h5b 已证价格侧无用，不为留存前置）。
- **决定**：留存三件套（行为地板熔断＋小 kk 链＋信用回流），全现成零件，开训前 pin
  进课程文件；Router 分簇在后（plan §8 B4）。
- **违反后果**：无地板续训 ⇒ 等被磨掉时无声无息，事后只能复盘不能止损。
- 全文 → `docs/nn/experiments.md` §52。

## §2026-09-28-goalnn-c20-target-6（2026-09-28，c20 靶子错了：等≠桥，1命≠产品）

- **背景**：“停学不会⇒c20 死路”；但停≠桥（不钻堆可用走位满足，从未直接练过开局别送），
  人类水平是雾（414000 上无人类 low%，God 在崩盘切片上 low 46.9%＝环境方差），
  产品 `ladder-c20` 3 命而 verdict 全跑 1 命探针（2 命 NN 已 low 1.6%）。
- **备选与否决**：c20 死路停训 —— 否，靶子错了不是路断了（开局生存＋残局转化未定向打过）；
  改命数 —— 否（任务侧不可动；这里只是确认产品口径，不是改口径）。
- **决定**：靶子重定为开局生存（动，不用等）＋残局转化；下一步按便宜排序
  （确认产品命数→人类真 low%→开局专项辅助目标），等只是候选药之一。
- **违反后果**：把 1 命探针门当产品门 ⇒ 在比产品难两档的基准上判死刑，错杀整条路线。
- 全文 → `docs/nn/experiments.md` §53。

## §2026-09-28-goalnn-god-out-7（2026-09-28，God 全退训练管线：同 seed 11.5%）

- **背景**：God 1 命打人类全过的 61 个 seed，`pass 11.5%（7/61）/kills 3.79` vs 人类
  100%/20（同 harness，`tmp/_god-demo61.json`）；单对单 dodge 行，一被包夹完蛋。
  与 NN 同档，非“弱一点的老师”。
- **备选与否决**：God-BC / God-bulk（§32⑤）/ God-expert（DAgger）/ God 混教（§54⑤）——
  全部否，God 只留诊断/调试（全可观测＋弱基线），禁入一切教师位置；
  “God 至少打架可教” —— 否，1 命 3.79 杀 vs 人类 20 杀无可教性。
- **决定**：往后一切教师位置只认人类（Opening-BC 切片、人类 DAgger、C 重开的对局理解）。
- **违反后果**：用 God 数据/标注训练 ⇒ 学到莽＋冻住，上限锁死在 11.5% 那档。
- 全文 → `docs/nn/experiments.md` §48（追补段）。

## §2026-09-28-goalnn-persist-prewarm-breaker（2026-09-28，a95 贡献量 1/10 归因：池启动预热 + 熔断收窄为「只停补位」）

**现场**：a95（Android/Termux/proot，SM7250，rl-config concurrency 6）rollout 贡献 ≤ mac 的 1/10。
真机取证（adb 上机）：轮参与率 **567/5119**（`ping failed — excluded` 2029 次 + 半路回线 174 次）、
同轮份额 **3.9% vs mac 35.1%**、单局服务时长 med **9.1s vs mac 1.1s**；而**引擎与 CPU 都不是原因**
（`engine=bun` + `BENCH-ARM native` 6.47ms、8 核在线无节流、无 swap 参与、单局独立跑 1.6–1.9s、user≈real）。

**根因**：**每批首批必付 N 次冷启**。6 路**真并发**（独立 curl 进程）经 agent 实测：**冷池 31.6s/局**
（6 个 spawn 互踩、拉开 25s）vs **热池 6 局共 9–11s ≈ 1.6s/局**。冷启成本 = bun + 整图 TS transpile
+ JIT + native/权重装载，而节点每轮（尤其掉线回线后）都是冷池。

**三处落地**（`tools/agent/persist-pool.ts` 纯策略 + sampler-agent 接线）：
① **启动预热**：按盘上最近用过的权重桶 kind 预 spawn `workers` 个池位，主腿吃满 `workers-(其它腿数)`、
其它腿各 1（池上限是 `workers` 且一波任务几乎总是同一 kind，摊平会让主腿撞上限退回一次性）；`--no-prewarm` 关。
② **熔断收窄为「只停补位」**：`runViaPersistWorker` 改三态，**只有「worker 挂但一次性跑通」才记连败**；
`busy`（池满 / 停补位冷却）是背压不记；熔断不再 `persistEnabled=false`（暖 worker 继续服务），
冷却 `PERSIST_REARM_MS=60s` 后放一次探针（半开）。**与云端池同规**
（§2026-09-25-goalnn-cloud-cpu-ledger、§2026-09-25-goalnn-rollout-reap-wedge ④），
后者已把「熔断=停用整池」明确列为被否决项。

**被否决**：靠调低 concurrency 治（实测 6 路稳态够快，降并发只会更闲）· 靠 `--no-persist` 治
（每局一次性在慢机上更贵：6 局 29–34s/局）· 预热平均摊给各 kind（见①）· 半开探针失败再升格惩罚
（只重计时）· 把池满当失败（旧实现这条 + 任务级错误一起把池 3 连打死）。

③ **预热必须在 bind 之前**（同日第三轮上机追加，同一条目）：第一版是「先 bind 再后台铺 worker」，
上机复验暴露它把「首批冷」换成了「上线即失联」：`listening on` → `persist prewarm` 隔 **51s**，其间
`/v1/ping` 连续 **8.00s 超时**（`--no-prewarm` 对照组 ping 全程 2–8ms）。机理直测：a95/proot 上
**每一次 fork 都把父进程事件循环占死 ~7s**，与子进程无关——`spawn('/bin/true')` 也要 **7.1s**
（`Bun.spawn` 7.06s、绝对路径 7.0s），而 `bun -e '1'` 自身启动只要 34ms；7 个槽位连发 = 全程失联 ~50s。
协调器 `node_ping(timeout=3s)` ⇒ 这 50s 里的探针必被判 `ping failed — excluded`，**即账本 2029 次的
同一机制**。故改为「bind 之前 await、逐个 spawn、等 `__SERVE_READY__` 再铺下一个」，带单槽 60s /
整体 180s 预算。修后同套探针：预热日志在前、4s 后 bind，上线即 `persistPoolSize=7 persistReady=7`，
ping 2–25ms，首批 6 路 rollout **WALL 8.3s ≈ 1.39s/局**。代价：a95 上线时间 ~26s → ~95s（但这段时间
旧形态本来也要付：上线后失联 50s + 首批全冷）。

**违反后果**：熔断写成停用整池 ⇒ 在最挤的时刻把每一局换成冷启动（a95 实测慢 20×）· 不预热 ⇒
首批永远冷（轮内份额 1/9）· 把池满/任务级错误记成失败 ⇒ 一条缺参数的请求就能永久关掉节点最快路径 ·
**先 bind 再预热 ⇒ 对着协调器的 3s 探针失踪 ~50s（比不预热还糟）**。

④ **任务级错误不再动 worker**（同日第四版）：`__SERVE_ERR__` 只说明**这一局**错了——serve-loop 捕的是
`main` 抛的异常、捕完继续收下一行，worker 本身是好的；旧写法就地 recycle ⇒ 连发 5 条缺 `--lives-override`
的 rollout 把池 7 打亏到 2、每条还白付一次冷启。改为：worker 报这一局错 + 一次性**也只 rc≠0** ⇒ 任务/数据
问题，池无责、**worker 留用**（只记一笔 `taskErr`，状态不变）；worker 报错而一次性同 argv **却跑通** ⇒
两路结论不一致（serve 与一次性在 argv / 环境上起了分歧）= 池这一路可疑 ⇒ 回收那个 worker + 记连败；
worker 进程死 / 无产物 / 超时 ⇒ 照旧回收。上机复验：`C after` = `persistPoolSize=7 persistReady=7`
（改前 2），日志零条 `persist worker closed`，坏任务 4.8–5.8s/条（改前 7.0–7.9s）。

⑤ **权重 POST 后的补位预热**（同日第五版）：启动预热的主腿是「盘上 mtime 最新的 kind」——那只是「上一代
跑过什么」；真正知道这一波用什么的是协调器的权重 POST（`x-kind`）。改为 `POST /v1/weights` 里
`queuePersistTopUp(kind)`，把点名的 kind 当主腿重算同一个 `prewarmEntryPlan`（新增第三参 `primaryKind`，
不参与新鲜度窗过滤）并**把池换到位**：只回收「超出计划且空闲」的 worker（`retireSurplusPlan`，busy 的
一个都不动 = 不丢局），再逐个 spawn。串行队列 + 先让出一拍——POST 的响应不等 spawn（同拍 spawn 会把本机
POST 拖到 7.7s，实测 115ms）。同时治了两个病：① 新节点（盘上无权重，启动计划为空）首批永远冷；
② 老节点猜错腿时池满 ⇒ 这一波每一局都退回一次性 spawn。a95 实测（3 路 eval 真并发）：补位前
`rollout=6 eval=1` → **3.1s/局**；POST `x-kind=eval` 后 `rollout=1 eval=6` → **1.55s/局**，5 条回收印记
全是 `was busy=false`。

**待办**：换腿会退役空闲 worker（a95 一次 ~39s），**rollout / eval 轮次交替频繁**时理论上会来回切
（本轮未量、未做节流）。
另记一条老行为（本轮只记录不改）：sync 路径上失败任务仍回 HTTP 200（错误走 `controller.error`，
客户端只能看 body 截断）——探针别拿 `%{http_code}` 当健康信号。
—— 全文（真机读数表 / 探针清单）→ `docs/nn/runtime-opt.md` §27

## §2026-09-28-goalnn-persist-homogeneous-serve（2026-09-28，长驻池改**同质进程**：单入口 + 每任务 mode token，删掉整套「腿」策略）

**问题**（用户提出）：池按导出器分「腿」（rollout 一类、eval 一类……），于是预热得**猜腿**
（按盘上权重 mtime）、猜错腿的那一波每局都退回一次性 spawn（a95 实测 **3.1s/局** vs 对腿 **1.39–1.55s/局**），
换腿还要退役空闲 worker 再补满（一次 **~39s**）。而用户的问题切中要害：**eval / rollout 都是无状态外壳，
为什么不能复用同一个 bun 进程？** 查证 ⇒ 拦路的三件事**都与状态无关**，「分家」是历史包袱：

* 「腿」在物理上就是「哪个 entry 文件」——worker spawn 时烧死 `bun <exporter>.ts --serve`，池的 key =
  `argv[0]`，任务只送 `args.slice(1)`（**不含 entry**）⇒ 一个 worker 只能跑那一个导出器；
* 两个导出器都只是 `runServe(main)` 的**无状态外壳**（每局新建 World；`grep '^let |new Map|new Set'`
  在两个文件里**一个都没有**；eval 的 import 表已含 `GodAIInput`/`IntentExecutor`/`GoalExecutor`/`danger-metrics`）；
* 原始分家理由（「别动 `export-rl-rollout.ts`，它在 codeHash 红线里」）**已过期**：`codehash-files.txt`
  现在把四个导出器 + `serve-loop.ts` 全收进去了（eval 自己也注明「2026-08-31 起本文件入集」）。

**决策**：池改成**同质进程**——`PERSIST_SERVE_ENTRY = 'tools/sim/serve-any.ts'`，一个 worker 按每行第一个
元素（`rollout` / `eval` / `goal` / `intent`，即 mode token）在进程内分派到对应导出器的 `main`
（`serve-loop.ts` 一行未改；未知/缺失 mode **抛**，不静默跑默认网格——分派错了不再有进程边界挡着）。
agent 侧「用哪个导出器」从 **spawn 时的 argv** 变成 **每行的 token**（`persistModeFor(entryTs)`，
表外条目如 BC ⇒ `null` ⇒ 一次性 spawn），挑 worker 只按「**空闲**」。**删掉**：`prewarmEntryPlan`
（mtime 主腿）、`retireSurplusPlan`（腾位）、`queuePersistTopUp` + `POST /v1/weights` 钩子（补位换腿）、
池 worker 的 `key` 字段——预热退化成「铺 `workers` 个 `serve-any`」。**机制被删掉，不是又加一层节流。**

**证据**：
* 本地 `tests/serve-any.test.ts`：① `PERSIST_MODE_BY_ENTRY` 值域 == `SERVE_MODES` 键集（两侧任何一边加
  导出器都红）；② **同一进程**交替 `eval` → `goal` → 坏 mode → `eval`，每局容器与一次性调用**逐字节一致**
  （除墙钟 `elapsedSec`），坏 mode 报 `__SERVE_ERR__` 后 worker 不倒；③ `persistModeFor` 对 BC 给 `null`。
  加源级钉子（池只 spawn 同质入口 ×2 处、送 `[mode, ...args.slice(1)]`、旧腿部策略不许回来）。
* a95 真机（同机 `workers 6`）：`persist prewarm: 6/6 worker(s) ready — tools/sim/serve-any.ts (47461ms)`
  → 4s 后 `listening`；进程表 `serve-any --serve`=**6**、任一导出器入口=**0**；首批 6 路 rollout WALL **8.57s**
  ≈ **1.43s/局**；**紧接着** 3 路 eval（**无任何 POST**）WALL **5.06s** ≈ **1.69s/局**；任务级错误 ×3 后池
  `6/6` 不动、`persistFailStreak=0`、`persistStopped=false`；全程 `spawned`（预热之外）/`top-up`/停补位 = 0 条，
  `agent_errors=3`（=3 条故意坏任务，其余 11 局全成）。本地门禁 **2244 pass / 3 skip / 0 fail（219 files）**。

**代价（已记账）**：rollout 执行路径多一个 entry ⇒ `tools/sim/serve-any.ts` 已入 `codehash-files.txt`
（否则改分派语义不触发节点升级 ⇒ 节点间静默分歧）并进 `NODE_BUNDLE_ENTRIES`（否则 node 引擎机器上
「一次性走 node 产物、池 worker 走 bun 源码」两条引擎混用）。丢掉「worker 物理隔离」这条边界。

**被否决**：① **保持异构、把上限解耦**（池容量抬到 `2×workers`，两腿各留满宽）——改动更小，但「腿错配」
与「上线后换腿」这类问题仍在，且常驻进程翻倍（a95 8 worker ≈ 304MB ⇒ 12–14 个 ≈ 500MB）；同质化是**删代码**
而不是加容量。② 给换腿加节流（治标：仍在猜腿）。③ 保留 `POST /v1/weights` 补位——同质后它无事可做。

**违反后果**：再按 kind/导出器给池分种类 ⇒ 预热又得猜（猜错整批退回一次性 + 换腿退役重补），
且这类错配**在账本上表现为节点变慢**（不是报错），最难发现 · 把 mode token 分派成「静默兜底默认网格」
⇒ 整批数据静默错（故未知 mode 一律抛）· 同质入口不进 codehash 清单 ⇒ 改分派语义不触发升级、
节点间行为漂移（本仓最敏感的面：准入靠 codehash）。

**顺带（同一条目，上机过程抓到的真缺口）**：`prewarmPersistPool` 新增 `PREWARM_DRY_LIMIT=2` ——
连着两个 worker 到不了 READY 就**中止预热**并走一次性兜底。上机实测（推错版本的 exporter 引了设备上不存在的
模块）：每个 worker 活 ~7.5s 就 `code=1`，重试把 **180s** 预算耗光，而这段时间**端口还没开**（既没上线、
也没预热）；加限制后 **14–15s** 就放行。
—— 全文（读数表 / 上机两个坑 / 判例清单）→ `docs/nn/runtime-opt.md` §27.10

## §2026-09-28-goalnn-local-serve-pool-homogeneous（2026-09-28，Python 侧长驻池也同质：单入口 + 每任务 mode token，门槛与池的形状解耦）

**问题**（用户点名）：TS 侧 agent 池已改成同质入口（`tools/sim/serve-any.ts` + 每行 mode token，
§2026-09-28-goalnn-persist-homogeneous-serve），而 Python 侧那份池（`remote/serve_pool.py`，
服务三条腿：节点 `iter_rollout`、本机 `rl/queue_local`、云机离线 eval）**还停留在旧形状**：
池的身份就是导出器——建池时烧死一个脚本（`bun <exporter>.ts --serve`），每条任务还要
`owns(argv[0])` 对一次脚本；于是「腿」这个概念在 Python 侧继续活着，「哪条腿能进池」与
「池是哪种 worker」被绑成同一件事。

**决策**：
① **入口固定** —— 池一律起 `tools/sim/serve-any.ts --serve`（`SERVE_ANY_SCRIPT`，与 TS 侧
`PERSIST_SERVE_ENTRY` 同值）；送进 stdin 的行 = `[mode token, ...该导出器的 argv]`，token 由
`common/manifest.serve_mode_for(argv[0])` 推出（`SERVE_MODE_BY_SCRIPT` = TS
`PERSIST_MODE_BY_ENTRY` 的 Python 镜像，值域 = `serve-any.ts::SERVE_MODES` 的键集）。
`owns()` 退化成 `mode_for()`：**池认不认得这个导出器**（仍按每条任务再过一遍门，池不认识 ⇒
`not-ours` ⇒ 调用方一次性路径）。
② **门槛与形状解耦** —— `make_pool(bun, ts_dir, workers, log, *, for_script=…)`：`for_script`
是「**本轮要池化的是哪条腿**」，不是「池是哪种 worker」。三个建池点（`rl/queue_local.make_local_pool`、
`remote/iter_rollout._make_pool`、`remote/offline_eval.run_cloud_eval`）跟着改；iter 保留
「整轮只能一个脚本」——准入是**按轮**判的，不是池的能力限制。
③ **准入门槛保持现状**（用户明确选择）：`SERVE_CAPABLE_SCRIPTS` 仍是
`{export-rl-rollout.ts, export-eval-game.ts}` ⇒ **本机 goal/intent 轮继续逐局 spawn**。要放开
只需往那个 frozenset 加一行（`serve_mode_for` 已经认这两个脚本），池侧零改动。

**证据**：送 stdin 的行逐字核对（新桩 `_STUB_ECHO` 把收到的行落盘 ⇒
`["rollout", ...argv[1:]]`，脚本路径不得进去）· 池入口恒为 `SERVE_ANY_SCRIPT`（两条腿同一形状）·
门槛仍按真名单（goal/intent 不建池、`NN_SERVE_POOL=0` 不建池）· 脚本→token 表四个真导出器 +
路径容错 · **跨语言同源**（TS 侧 `tests/serve-any.test.ts` 读 `nn-training/common/manifest.py`
逐条对拍：常量定义 + token 值）。桩的接法随之变：新增 `_install_entry()` 把同一个 python 桩写到
生产入口路径 `<ts_root>/tools/sim/serve-any.ts`，端到端用例（`run_iter_rollout` 那族）因此
不需要任何注入；直接建 `ServePool` 的用例用 `entry=` 注入 + autouse 夹具把 `stub_*` 放行两道门
（真脚本名仍走真表）。
读数：`pytest tests/ e2e/` **3328 passed / 1 skipped**，`ruff check .` 全绿。

**被否决**：① 顺手把本机 eval 腿（`EvalDispatcher` / 批评估的本机槽）也建池（今天只有云机离线
eval 有池）—— 范围更大、与本次「机制同质化」不同题，留作独立决策。② 顺手放开 goal/intent
（同质化后它们在结构上已经可以进池）—— 用户点名保持现状，避免把「机制改造」与「把两种模式
推上生产池」混成一次改动。③ 保留「池按脚本建」而只把 `serve-any` 当一个特殊脚本 —— 那等于
两套形状并存，迟早要再同质化一次。

**违反后果**：再在 Python 侧按导出器建池 ⇒ 「腿」复活（谁进池、池是什么，重新绑成一件事）·
`serve_mode_for` 与 `PERSIST_MODE_BY_ENTRY` 漂移 ⇒ worker 当场 `unknown mode`（响亮，但等到
上机才知道；故有跨语言对拍钉子）· 把 `for_script` 又当成池的身份 ⇒ 同一轮混 mode 时又要建多个池。

—— 全文（判例表 / 读数 / 桩接法）→ `docs/nn/runtime-opt.md` §28

## §2026-09-28-goalnn-gate-baseline-8（2026-09-28，门必须配平凡基线）

- **背景**：wait-gate 的 F1≥0.60 门被一行“重复上一步”（F1≈0.69–0.72）击穿——
  门立了半年（plan §3 同款写法），从没人问过平凡基线是多少。
- **备选与否决**：“门数字面合理即可” —— 否；合理数字可能全是粘性/多数类/同义反复
  （本案三样占全：粘性 a=0.689、多数类、s22 后果变量）。
- **决定**：一切行为门（plan 课程/探针的通过线）在 pin 数字的同时，必须 pin 一个
  同口径平凡基线（重复上一步/多数类常数/单特征，共位计算），门过线且**显著超基线**
  才算过；泄漏消融（去后果变量重跑）是 P0 第一道门。
- **违反后果**：门成了同义反复的毕业典礼——模型学到粘性，账本记成功劳，下一腿在
  沙子上继续盖。
- 全文 → `docs/nn/experiments.md` §58。

## §2026-09-29-goalnn-metrics-v9（2026-09-29，观测列先于定价；改谓词必须断代）

- **背景**：三件事都是「先有数才有门」——通道 1（背面奖）缺方位参数；生产 `threatTicks` 漏判约六成
  （12px vs 19px，`docs/nn/experiments.md` §60）；穿越税立法缺观测列。合并为一次 metrics 批量改动。
- **决定**：`METRICS_VERSION 8→9`、行宽 `45→54`，尾部追加 9 列：`backHits/sideHits/frontHitsExempt/`
  `farHits/geoFallback`（idx45–49）＋`onLaneTicks/onLaneExemptTicks/onLaneMoveTicks/onLaneHoldFireTicks`
  （idx50–53）；`threatTicks` 谓词**原地改定义**（19px = 坦克半宽 16 + 子弹半高 3 / 去半径上限 /
  判墙体遮挡）；**公式一个字不动**（零调参；`wGeo*`/`wMuzzle*` 另开课程文件）。
- **豁免住公式侧**：观测只记事实——raw 列不因豁免而重定义，冻/盾以**加法列**（`*Exempt`）另记，
  交集必须在共位处一次算好（逐行 flag 相乘会在翻转沿打出幻影 ± 尖峰；且公式引擎禁跨步归约
  `reward_library.assert_no_time_axis_reducers`）。定价权在课程公式。
- **备选与否决**：① §60 的「新增 `threatTicks2` 并存」——否：近义列污染立法侧，且旧谓词下
  逐 tick 状态已丢、**复算本来就不可能**，新列只是把「已知偏低」留在 schema 里被后人误引；
  ② 逐行 flag 相乘——否（见上）；③ 靠 `targetId` 回查受害几何——否：致死命中同 tick 被
  `removeDeadEntities` 压实 ⇒ 改事件侧**快照**（`targetDir/targetX/targetY`）＋`bulletId`
  反查开火时刻（`bullet_fired` 登记，非首见采样）。
- **口径断代（祖父条款）**：新旧 `threatTicks` 不可比 ⇒ L1/L1d2/L2b/dodge-l3 的「机制零位移」
  verdict 维持原判但**标注断代**；l2a 用 `dangerTicks` 不受影响。
- **断代义务（评审 B2/B3，已核）**：`corpus_identity_fp` 的 payload 只含 obs schema/课程/reward
  （`nn-training/rl/config.py`）——**不含 metrics 列/版本，也不含 `decision_events`** ⇒ 换谓词后
  旧/新 shard 同血缘、D14 不拒收、可静默混训。故：本刀不开训练腿；要开腿必须新 `--out`/`--traj`，
  或照 `move_label_semantics` 先例把语义标签**无条件**写进 payload。`inThreatLane` 同时是**决策门**
  输入（7 处调用，含部署链 `policy-input.ts` 与 `obs-encoder.decisionTick` condition 4）⇒ 已训过的
  `decision_events` 课换谓词同样必须先断代（`h5e-events` 是草稿未开训，无需）。
- **违反后果**：① 按旧口径引用 v8 之前的 `threatTicks` 与 L 系 verdict ⇒ 结论错误且无从察觉
  （没有任何门拦得住）；② 给 `decision_events` 课换谓词不换 `--out` ⇒ 同一课程目录混两种 MDP
  采样，账本写着「同一分布」；③ 再往 schema 加近义列 ⇒ 立法侧引用分叉。
- **证据（不是承诺）**：`freeze:check` 绿（事件加字段不进 det 签名）· reward golden 64 例 reward
  **逐位不变**（只多 9 尾列）· `bun run check` 2264 pass/0 fail · nn 门禁 3339 passed。lockstep
  **七处 + ⑧** 全部登记（⑦含 `test_item_metrics_layout_locked` 尾部清单；⑥含
  `v7_phi_ts_oracle.json` 宽度同步）。
- 全文 → `docs/nn/experiments.md` §64；审计（读者/写者闭集 + 语料身份 + 决策门半径）→
  `docs/geo-threat-audit.md`。

---

## §2026-09-29-goalnn-publish-metrics-version-gate（2026-09-29，行宽版本门进**发布端**：按 shard 自己的声明剔，不送必炸字节上 GPU）

- **背景（现场）**：metrics v9 落地后（commit `b49b52ff`），h3-geo / x20-geo 的 it1 采集中，节点 `a98`
  同一轮内**同时**产出两种版本（`bun-26105` 4 份 (74,**45**)/metrics_version=8，`bun-12841` 3 份 (136,**54**)/9）。
  旧版 shard 进了 payload ⇒ 云 GPU worker（`code.zip` = 本机 v9）在 `_reward_from_metrics` 首秒炸
  `ValueError: metrics 形状应为 [N+1,54]，收到 (74,45)`；内容决定性 ⇒ 同份字节重领必炸 ⇒
  hub 侧 5 轮连续失败 → `aborted ppo 连续失败 5 次（≥5）`（整门课停）。
- **判据（为什么现有门都没拦住）**：`wver`/`course_fp`/`corpus_fp` 血缘与行宽**正交**——两者都是
  「同血缘、同权重、同关卡」，只是导出器列数变了；`corpus_identity_fp` **不含** metrics 列（见 §2026-09-29-goalnn-metrics-v9）。
  云 worker 的响亮报错只能**事后**止损（那时 GPU 轮次已经废了），不是判决门。
- **决定**：发布端 `remote.hub_client.iter_shard_dirs(..., metrics_version=<本机 METRICS_VERSION>)` ——
  manifest 声明的 `metrics_version` ≠ 本机 ⇒ **不进 payload**（与 D14 血缘过滤、state_init 起始分布过滤同族语义：
  「云端会整份拒收/必炸，故不进 payload」），逐版本点名日志（含节点指纹线索）。缺省 `None` = 逐字节旧行为。
  读取端 `ppo.np_core._reward_from_metrics` 改为**先判声明版本、形状检查降为兜底**，报错直接点「产出端导出器与读取端版本不同」。
- **被否决的备选**：① **硬失败**（发布端 raise）——会把「一个节点跑旧代码」升级成训练中止，与
  D14 的处置不一致（同一类问题的既有答案就是「剔掉 + 响亮日志」，剩余局由 tail-race/重采补）；
  ② 只加**节点门**（比对 node codeHash 更严）——挡不住本次现象（池暖 worker 与磁盘代码脱钩时，
  节点自报的 memo codeHash 描述不了它实际执行的那份；见「下一步」）；③ 改 `corpus_identity_fp`
  纳入 metrics 版本——语义上错（那是**语料身份**，不是读取格式），且会让所有历史课整批断代。
- **可逆性与代价**：不改行宽、不改公式、不进 codehash 集（`nn-training/**` 不在其中）⇒ 不触发节点升级波；
  代价 = 一轮可能少几局样本（缺口由既有重采补齐），换来「绝不把必炸字节送上 GPU」。
- **下一步（未做，属独立刀）**：产出端**代码版本钉住**——节点池暖 worker 读磁盘代码（子进程启动时 re-read），
  而 agent 的 `codeHash` memo 是「进程启动那份」（`sampler-agent.ts` F2 设计）⇒ 磁盘换代码、进程不重启时
  「报旧跑新 / 报新跑旧」双向漏网，同一节点混出两种行宽。要么池 worker 钉住快照代码，
  要么 agent 发现「磁盘 hash ≠ memo」时**自报 stale**（让训练侧按既有门排除并重启）。
- **证据**：`tests/biz/test_metrics_shard.py` 新增 3 例（旧版本报版本不报形状 / 发布集剔旧版本 / 缺声明算不符）；
  `tests/{test_remote_iter,test_state_init,test_rl_remote_fixes,test_payload_split,test_remote_ppo}.py` 106 例绿；
  ruff check / mypy（`--config-file pyproject.toml`）过。

---

## §2026-09-29-h4-nolane-open（2026-09-29，h4 移动穿越税新实验；§63 豁免待用户）

- **背景**：h3-geo 最佳落定 it45（pass 三方持平 93.0 中零伤 64.5% 最高，Pareto 占优，非 it50）；
  h4 it0 预跑（bc=h3-geo.it45 × ladder-c04 × 860001 × 200 局）lane 暴露 78.7/局 ≈ h3 的 2.3×、
  move 率均值 0.003868、dmg 110.1——lane-shaped 残余摆在面上，channel-2 在 h4 有可测目标。
- **决定**：开 h4 平行双臂（h4-geo 去 explore／h4-nolane 加移动税，两臂公式只差一项）；
  同 bc（h3-geo.it45，379114B，sha256 `3a5cba10…e8ac` 已验）＋同 `paired_rotate_seed=1790491796`
  （h4-stop 实测流，与 h4-stop 历史可比；McNemar 前提；声明≠生效拒启）；
  新 out/traj（`tmp/h4-{geo,nolane}`，空目录，禁续跑）；wLane=200.0 已 pin
  （it0：0.075×10.23/0.003868=198.4→取整；5%/10% 界 132/264；税 EV≈0.77/局≈wDmg 罚的 23%，
  可见不主导）；wGeo 沿用 1.0/0.5 未 pin（保与 h4-geo 单变量纯度，见否决③）。
- **被否决的备选**：① raw 全量形态 `-w*d(onLaneTicks)+w*d(onLaneExemptTicks)`——连带罚架枪，
  与 §62 证据冲突；② wLane 沿用 L2b 的 30——h4 move 率下 EV~0.12/局（~1% killEV），不可见；
  ③ h4-nolane 内顺手把 wGeo 改成 0.37/0.18——破与 h4-geo 的单变量纯度；geo 修正等 h4-geo
  自己的 pin 流程（若 h4-geo 后改剂量，本腿禁中途跟改，比较带剂量 caveat）。
- **可逆性与代价**：课程文件零代码改动（`nn-training/**` 不在 codehash 集）⇒ 不触发节点升级波；
  代价＝150 轮/12h 预算（横盘门常备：pass/dmg 连续 4 点无改善即停，宽限一次）。
- **证据**：`validate_reward` 两文件零错误（h4-nolane 仅 1 不可达角包络 warning，L2b 同款）；
  it0 预跑 `tmp/h4-nolane/pin-it0-h4.jsonl`（200 局：pass75.5/dmg110/零伤32/b/e0.308/
  holdFire13.64/cells61.7）。
- **未决（开课前用户拍板）**：§63 重开条件——channel-1 机制零位移（h3 back 占比 45 轮 +0.006），
  条件（a）未满足；条件（b）wLane 已 pin 但 wGeo/冻住守卫未全 pin。豁免开课需在此条下记一行
  用户批示；不豁免则本腿维持草稿，只跑 h4-geo。

---

## §2026-09-30-h4-lane-wave1-verdict（2026-09-30，Wave 1 三臂判**无效**：控制臂自漂 ≥ 目标效应 + 功效不足；A2 的 ABORT 判**假阳性**）

- **背景**：E0 判红「绕后奖」→ 机制归到穿越税（§2026-09-29-h4-nolane-open）→ F1 臂身份锁（训练 `ladder-c04`
  （`count 4`/`max_ticks 3300`），评估 `ladder-c20-lives1`（`count 20`/`12900`）——两关 grid / enemy_spawns /
  player_spawn / forces / lives / level / difficulty **逐字节相同**，只差 `count` 与 `max_ticks`）→ E1a 分布内
  对照（obs `s[8] = spawnQueue.length / enemiesTotal`：c04 值域 {1.00, 0.75, 0.50, 0.25, 0} vs c20
  {…0.95, 0.90, 0.85, 0.80} **不重叠** ⇒ 输入级 OOD 成立；E0 的 2.88× 拆成 OOD 1.80× × 分布内 1.48×）→
  E1b 剂量锚重算（三处口径错误被推翻；权威账本 `*.run/eval_log.jsonl` 复测探针偏差 ≤0.2%）⇒ Wave 1 三臂
  2026-09-30 开训：**A0** 零臂（v4 包逐字，无 lane 项）/ **A1** 尾部 `- wLane*onLaneMoveTicks/(ticks+1)`
  wLane=127 / **A2** 尾部 `- wLane*onLaneTicks/(ticks+1)` wLane=7.0；三臂同 bc（h4-stop.it60）· 同关卡
  `ladder-c04` · 同 `paired_rotate_seed=20260930` · 200 局 × stage 2000 × seed 860001–860200 × `eval_every 5`。
- **决定（第一判：Wave 1 无效）**：判 **uninformative**，**不进 Wave 2 剂量扫描**。三条独立理由，任一条单独
  成立即足以判无效：
  1. **控制臂无效**——A0（零 lane 奖励、逐字 v4 包）在**同一主终点 rA2 = E[onLaneTicks/(ticks+1)]** 上自身
     漂移 **+15.6% / +34.6% / +25.4% / +21.6% / +16.5%**（it10/15/20/30/35；配对同 seed，单侧「上升」
     p = 0.069 / **0.0017** / **0.012** / **0.018** / 0.054），**≥ 预注册的 16% 机制绿线**；同批 A0 通过率
     86.0% → 79–82.5%（it15 −13.0pp，t=−3.58）。⇒ 「奖励起效」与「训练自身把在线暴露推高」同量级，不可分离。
  2. **功效不足**——配对差 sd 实测 ≈ 9.0–10.2pp ⇒ **MDE(80%，双侧) N=200 = 25.0%、N=400 = 17.7%**
     （tick 加权口径 25.1% / 17.7%），**均 > 16% 绿线**；N=800 才到 12.5%。预注册的「判据段 400 局」**实际
     没跑**（三课均 `eval_games_per_stage: 200`），且其「400 → 12.8%」误用**单臂 sd（7.01pp）**而非配对差 sd
     ——本实验第四处口径错误。
  3. **主终点未过**——共同评估点 **it20**：A1 Δrel **−5.34%**（t=−0.60，单侧 p=0.275）、A2 Δrel **+6.15%**
     （符号错，t=+0.63）。三臂全程无一点同时满足「Δrel ≤ −16% ∧ 单侧 p < 0.05」：A1 仅 it5（−18.29%，
     p=0.029）与 it15（−26.57%，p=0.0025）两点达标，it10/it20/it25/it30 回零，it40（ref=A0 it35）+0.91%；
     7 点均值 −11.1%、中位 −6.0%。A2 四点（+4.24 / +7.26 / −18.84 / +6.15）均值 ≈ 0 且三点符号错。
- **决定（第二判：A2 的 ABORT 是假阳性）**：A2 于 it21 触发 `gate_verdict: ABORT`（kickstart-burn 连续 3 个
  评估点低于基线 5pp，86.0%→73.5%，判词「疑似回锚/塌陷」）。**不追认为机制证据**，三条反证：
  1. **零奖励臂走过同一段坑**——A0 it15 = **73.0%**（低于 A2 收官 73.5%），burn streak 到过 **2**
     （it16=73.0 / it21=79.0），靠 it26=81.0 清零；A2 = 76.5 / 78.0 / 73.5 三点连坐。⇒ 该判据在零奖励臂上
     假阳性率过高，A2 只是没抽到反弹点。
  2. **优化侧健康，没有「崩」**——三臂 kl 0.011–0.020（cap 0.2）· entropy 0.49–0.63 · value 0.25–0.42 ·
     policy gnorm ~1e-4–8e-3 **同带宽**；无 KL 尖峰、无熵塌、无 NaN。
  3. **剂量只占回报 1.5%**——lane 项 EV（两臂同预算）= 127×0.003142 = 7.0×0.056640 = **0.40/局**，其余各项
     合计 ≈ 26.2/局 ⇒ **1.53%**。把在线率清零也只多 1.5% 回报 ⇒ 无法驱动 −12.5pp 胜率。A2 的真实退化仅
     **~−1.5pp**（训练 rollout 同 iter 配对 Δ=−3.07pp / t=−2.77，**剔除 it8/it14 两个 −14pp 跳变后只剩
     −1.45pp**；评估端点配对 it5 +0.00 / it10 −5.50 / it15 **+5.00** / it20 −5.50 ⇒ 符号逐点翻转）。
- **被否决的备选**：① **「A2 剂量歪导致训练崩」**——否，优化指标与零奖励臂同带宽、剂量占回报仅 1.5%，
  无因果路径；② **「Wave 1 无效 ⇒ 穿越税路线关闭」**——否，被税量在 A1 方向正确（7 点均值 −11.1%）且守卫
  未破，只是不显著；16% 绿线落在 MDE 之下 ⇒ **功效不足不得当作路线判死**；③ **「立刻重开 A2 补满 40 轮」**
  ——否，即使跑满也答不了主问题（控制臂漂移 ≥ 目标效应），应先修止损规则与功效；④ **「照预注册补跑 400 局
  判据段」**——否，实测 MDE(N=400) = 17.7% > 绿线 16%，补跑注定仍不显著。
- **可逆性与代价**：仅记录，**零代码 / 零课程改动**；`nn-training/curricula/h4-lane-a{0,1,2}.jsonc` 维持
  「草稿待审」状态（`bun run check` 绿：2264 pass / 3 skip / 0 fail；前置 `load_course + validate_reward` +
  跨臂 19 字段机检 OK）；A2 的 ABORT 不追认为机制证据；三腿账本 `tmp/h4-lane-a*` 仅作证据保留。
- **证据**：`tmp/h4-lane-a{0,1,2}/eval_log.jsonl`（200 局 × stage 2000 × seed 860001–860200；A0 止于 it35
  ——it40 评估未落盘 · A1 止于 it40（run_complete 记 it41/40）· A2 止于 it20（run_complete 记 it21/40））·
  `training_log.jsonl`（`kickstart_burn` / `gate_verdict` / `iteration`）· `remote-jobs/*/manifest.json`
  公式指纹（A0 `cbe36ea1ade88cb5` 无 lane 项 / A1 `f1c27b5b55c7038b` onLaneMoveTicks / A2
  `0b6ee0e69afe4abe` onLaneTicks）。**配对立证**：三臂 it1 rollout **逐位一致**（samples=24626 /
  ticks=245167 / `{stage_clear 140, lives_exhausted 40}`），it2 起才分叉 ⇒ 共同起点与共同语料成立；三臂超参
  同源，A2 尾段 manifest 的 `lr 0.0005 / kl 0.02 / ref=it0` 只是 `until_iter 25` 日程边界，非臂差异。
  **守卫表 it20**：A1 静止∧在线 −5.7% / holdFire +15.6% / pass +3.8% / dmg +11.4% / kills +0.7%（未破）；
  A2 静止∧在线 +5.6% / holdFire +9.9% / pass −6.96% / dmg +18.22%（t=+2.39）/ kills −6.47%（t=−2.27）；
  timeout 全 4800 局仅 1 局 `max_ticks`。⚠ **探针与判决脚本均为 `tmp/` 一次性产物：未落盘、无单测、未独立
  review ⇒ reported 级**，进 plan / 门 / 判据前必须落盘 + 独立复算（§58/§60/§64 同款教训）。
- **未决（用户拍板）**：① **止损规则修法**——把「各自对固定基线 86.0% 的 3 连续点」改为「同网格**配对差**
  检验（A2−A0）」，或 streak 3→4–5，或要求 kl/entropy 同时异常才 ABORT；② **控制臂漂移的处置**——换不漂的
  锚 / 改同臂前后对照（把训练漂移当协变量）/ 压缩到漂移可不计的轮数；③ **是否按「lane 项占回报 5%」重锚**
  （目标 EV 1.31/局 ⇒ wA2 ≈ 23、wA1 ≈ 420；10% ⇒ 46 / 840）——属奖励语义变更（§15.5），须走新实验
  （新 `--out`/`--traj` + 新 `paired_rotate_seed` + 本条后继）；④ **Wave 2 的几何选择**——保留 A1 几何
  （127 打在 0.314% 稀有事件 = 稀疏·尖锐；胜率三臂最好 84.0 vs A0 79.1 / A2 77.6），A2 几何
  （7.0 抹在 5.66% 常见状态 = 稠密·钝）退为对照。 **→ ①②③④ 已定案（2026-09-30）**：① §2026-09-30-goalnn-kickstart-burn-paired；②③④ §2026-09-30-goalnn-wave2-design（推导、功效复算与 N 表 → `docs/nn/experiments.md` §66；**三腿按新判据回填：主终点判语不变、但「无效」标签改判为「答了、不够绿」→ §66 ⑤**）。
- **回填后的修订（2026-09-30）**：把新判据（尾巴 3 点均值 / K 点合并）回填到 Wave 1 三腿
  （`nn-training/tools/paired_power.py` §9 · `docs/nn/experiments.md` §66 ⑤）后，本节的判语改两处：
  ① **主终点判语不变**——没有一条线到绿线 16%（A1 尾巴 −7.26% p₁ 0.097、全 7 点 −11.14% p₁ 0.001，
  A2 ≈ 0）；② 但 **「无效」这个标签改判为「答了、答案是不够绿」**：「功效不足（MDE 17.7%@400 > 16%）」
  被新判据（MDE 15.66% ≤ 16%）顶掉，「控制臂自漂」已按共模消降为诊断量（A0 自己的尾巴漂移 +15.0%，
  与绿线同量级），只剩「一条腿被假阳性掐掉」维持（§66 §5 的 pass MDE 又添一条独立支持）。
  **③ 补评估收口（同日，§67）**：7 个评估点各补 300 局（seed 861011–861310，**只评估不重训**；管线先验过
  ——同 checkpoint 同 seed 重跑 40 局逐位一致）⇒ 尾巴 3 点 Δrel = **−8.52%**（SE 3.22%，95% CI
  [−14.84%, −2.21%]）⇒「**不到绿线已证实**」+「**效应可靠为正**」（同质 Q 0.93）；全 7 点 −13.39% 但
  **异质**（Q 16.96 > 12.59）⇒ 只作描述。⇒ 本条的「无效」判语**正式作废**，定为「**答了：f = 3.6%
  这档不够绿**」。剂量推论（预注册规则）：|E| = 8.52% ⇒ 到绿线需 ≈**1.88×** ⇒ 括号里只有 **f = 7.5%
  （2.08× ⇒ wA1 265）**够，4.0%（1.11×）不够（一阶外推，待用户拍板）。
## §2026-09-30-goalnn-kickstart-burn-paired（2026-09-30，止损参照物改为同网格配对差；`auto` 缺省 = 对已配对课的行为变更）

- **背景**：Wave 1 判无效（§2026-09-30-h4-lane-wave1-verdict）——A2 的 ABORT 查实为**假阳性**：旧
  `kickstart_burn` 的参照物是**本腿自己的 it0 基线**，而控制臂 a0（零奖励 = 真零效应）自身漂移到位
  （逐点 pattern `..TT.T.` ⇒ 峰值 streak 2，只差 1 点被自己的规则杀掉）。参照物会飘 ⇒ 任何腿都可能
  被自己的起点误杀，与效应无关。
- **决定**：参照物两档。`baseline` = 旧口径（本腿 `it<=0` 末条）；`paired` = Δ = 本臂 − **对端同 it**
  读数（pp），尾部连续 `points` 点 Δ < −`margin_pp` ⇒ 触发。常量不变（`BURN_POINTS=3` /
  `BURN_MARGIN_PP=5.0` / `METRIC=winRate`）——只换参照物 = 最小改动。
- **接线**：`rl/kickstart_burn.py` 增 `MODE_BASELINE/PAIRED/AUTO` + `burn_mode()`；`burn_verdict(peer_rows=…)`
  非 None 时**整段委托** `rl/paired_kill.py::paired_kill_verdict`（Δ 判据只留一份，不写第二实现）；
  `BurnVerdict` 增 `mode/delta_pp/peer`（带缺省 ⇒ 既有五元位置构造不变）；事件 `write_kickstart_burn`
  同步落三字段。`loop_guards_leg.py::_burn_paired_rows` 前提闸同 `_paired_kill`（`declared_paired_seed`
  + 对端账本末条 `run_start.rotateSeed` 同 V）；**对端不唯一不猜 ⇒ 回退 baseline 并只 WARN 一次**。
- **★ 行为变更**：`mode=auto` 是**缺省值**；现 `rl-config.json` 已被配对的课（`x20-clutch` 等）**没有任何
  `kickstart_burn` 覆盖** ⇒ 下次开训会**静默**从 baseline 切到 paired 参照物。有意（正是要修假阳性），
  但读旧日志须知道两档口径不同。
- **回测证据**（可复算：`nn-training/tools/backtest-burn-rule.py`，语料 `tmp/h4-lane-{a0,a1,a2}/eval_log.jsonl`）：
  - 旧规则实测：a0 峰值 streak 2 / a1 1 / **a2 3 ⇒ 触发**（即那条被误杀的腿）⇒ 三腿假阳性 1/3。
  - 新规则实测：a1 vs a0 峰值 0；a2 vs a0 Δ = (+0.00, −5.50, +5.00, −5.50) 峰值 1 ⇒ **两臂都不触发**。
  - MC 20000 次（每点 200 局二项噪声，7 点 it5..35）：N1 无漂移 旧 **0.0050%** / 新 **0.3100%**；
    N2 共享漂移（两臂共享对照臂实测轨迹）旧 **49.3350%** / 新 **0.4650%**（**低 106×**）⇒ 假阳性来自
    「参照物会飘」而非抽样噪声。阈值 grid 复测：现常量 (5pp,3点) 在 N2 下 FP = **0.56%**，比旧规则
    低 **88×**，且全 grid 三腿实际触发均 [False, False]。
  - **代价明买明卖**：无漂移下新规则略差（0.005% → 0.31%），因配对差抽样方差更大
    （σ_Δ = √2·σ ≈ 3.48pp vs 单臂 2.46pp）。
- **被否决备选**：① 只调常量（margin 3→8 / points 3→4）——参照物漂移仍在，且会拖死真效应；
  ② 止损改硬失败（crash）——丢掉「停训但留证据」的诊断价值；③ 要求 `kl`/`entropy` 同时异常才触发——
  无回测支撑，判据复杂化一倍；④ 为 burn 单写 Δ 判据——与 `paired_kill` 两套实现会分叉。
- **未决（用户拍板）**：控制臂漂移处置 · 是否按 lane 项占回报 5% 重锚（新实验 §15.5）· Wave 2 几何选择。
## §2026-09-30-goalnn-wave2-design（2026-09-30，Wave 1 三项遗留定案：漂移按共模消、剂量不重锚、几何只留 A1）

- **② 漂移按共模消**：主终点保持**同 iter 同 seed 配对差**（对照臂 = 同时训练的零奖励臂），漂移降为
  **每个评估点必报的诊断量**。依据（it20）：三臂自漂 +25.4 / +18.7 / +24.0%（绝对 +1.44pp），配对差
  只剩 **−0.379pp（−5.34%）** ⇒ 共模被消掉 3.8× 于效应本身。**否决**换锚（唯一不漂的锚 = 冻结 bc
  ⇒ 把「训练本身」与「税」混成一个量）与**否决**压轮数（对照臂 it5 已 +8.9%、it15 起显著 ⇒ 不存在
  「漂移不计」窗口）。逐局复算：sd_Δ/σ_arm = **1.221**（ρ ≈ 0.26）⇒ 配对只省 13.6% sd / 26% N，**不是银弹**。
- **判据 = 尾巴 K 个评估点取均值（预注册 K=3、200 局/点），不是单点**：功效只取决于 **N×K 的乘积**
  （MDE ∝ 1/√(N·K)）。实测尾巴三点（it25/30/35）合并后 SE(Δrel) = 5.59% ⇒ MDE(80%) = **15.66% ≤ 16%**；
  合并合法性已验：Δrel **跨点** sd 8.65% **小于**单点抽样 SE 9.72% ⇒ 真效应在尾部平稳（无趋势、无额外
  跨点方差）。单点方案需 N = 568（中位口径）/ 490（it20 口径），功效相同但要**另开一段大评估**；而
  Wave 1 盘上本有 8 点 × 200 = **1600 局**，判据只需 600 ⇒ **不是局数不够，是只用了八个点里的一个**。
  （本条**修正**同日早先的「判据段 N = 768 单点」：那是只算单点方差时的结论，算进尾巴合并后同样功效更省。
  推导与修订记录 → `docs/nn/experiments.md` §66。）
- **Wave 2 预注册必须先修三处，否则守卫表判不了**：① 四条「非劣」终点（零伤局 / dmg / kills / shots）
  课程里**没给数字** ⇒ 不可判，必须按本设计真能分辨的幅度给阈值（见 §66 的 MDE 列）；② 「静止∧在线
  不升」margin = 0 ⇒ 需无限局，必须改成「不升过 Xpp」；③ `timeout` 全库 1/3400（sd = 0）⇒ 只报频次。
  另：**pass「−3pp 非劣」需 N ≈ 2130、stuckTicks「+10%」需 N ≈ 1844** ⇒ 在此规模下**结构性不可判**，
  只能放宽阈值或降级为诊断项；可判的守卫只有 cellsVisited（−10% ⇒ 132）与 holdFire（−30% ⇒ 191）。
  **副产品**：pass 的 MDE(200 局) = 9.8pp 绝对 ⇒ A2 那次「破 pass 守卫」（−6.96% ≈ 5.6pp）落在里面
  **同样是噪声**（与 §65 判 A2 ABORT 假阳性同向，多一条独立支持）。
- **更长时程**：逐点 SE(Δrel) 对 it 的斜率 ≈ 0（−0.017pp/it，it5–35）⇒ N 表可外推到 it40/60/80；反过来
  **跑更久不买到功效**（功效只由「局数 × 评估点数」决定）。⚠ 超出 it35 属外推（语料只到 it35，且尾部仍在训）。
- **③ 不重锚**（保持 f = 3.6% killEV / wLane 127）。**单位纠正**：本族锚法 = `f × killEV / rateA1`
  （plan Q5 写「5–10% **killEV**」），而「占**总回报** 1.53%」是 A2 事后诊断用的量，**不是锚**；照后者
  重锚 ⇒ wA1 127→420（3.3×）。重锚 = 奖励语义变更（§15.5），与「修功效」「换几何」同时改会破坏单变量
  纪律。**「剂量太小」无证据 → 已作废（2026-09-30 补评估；§66 ⑤ + §67）**：合并 500 局/点后尾巴 3 点 −8.52%
  （95% CI [−14.84%, −2.21%]：不到绿线已证实 + 效应可靠为正）⇒ 剂量被证据指到 **f = 7.5%（wA1 265，≈1.88×）**
  档（4.0% 的 1.11× 不够；档位待拍板）。若将来重锚：只走预注册括号（f = 3.3 / 4.0 / 7.5% ⇒ wA1 116 / 141 / 265），先统一两套数。
- **④ 几何只留 A1**（`onLaneMoveTicks`，稀疏·尖锐）；A2（`onLaneTicks`，稠密·钝）退为**档案对照**，
  不进 Wave 2；D9「正对来袭炮弹」豁免暂不加。依据：A1 守卫全未破；A2 四点三点符号错**且**破三条守卫
  （pass −6.96% / dmg +18.22% t=+2.39 / kills −6.47% t=−2.27）⇒ **有伤害迹象、无效果迹象**。两臂
  （零臂 + A1）＝答主问题的**最小**设计，臂数 3→2 对冲判据段成本。豁免要新的 metrics 列，而现在
  **没有**「税在罚真躲弹」的证据（holdFire 守卫未塌 10.53 → 13.53/局），等看到「有效果但罚到真架枪」
  再按 §11.5 补。
- **开工闸（三条齐才开）**：① 课程头注冻结「判据 = 尾巴 3 点均值 × 200 局/点 + 池外新 seed0 + 配对
  止损」（与 ② 同一个参照物）并把 §66 的 MDE 列写成每条线自己的阈值（四条「非劣」必须先给数字）；
  ② 新 `--out`/`--traj`/`paired_rotate_seed`（§15.1/§15.5）；③ 草稿过 `load_course + validate_reward`
  后**由人在控制台开课**（agent 不开训）。
- **证据与详细推导**：`docs/nn/experiments.md` §66（功效复算表 + 次要终点/守卫表 + 尾巴合并 + 判据表）·
  `nn-training/tools/paired_power.py`（逐局复算：§65 归档 Δrel/t/p₁ 逐点一致、MDE 三元组 = it20 的
  SE × √N）· `tests/test_paired_power.py`。

## §2026-09-30-goalnn-wave2-dose-ladder（2026-09-30，Wave 2 = 剂量阶梯 {7.5%, 15%}；含括号外档位）

- **定案**：Wave 2（b 腿）= 零奖励对照 + **A1 几何两档剂量**：**f = 7.5%（wA1 265）** 与
  **f = 15%（wA1 529）**；判据 = 尾巴 3 点均值 × **300 局/点**（每条线一个数字 ⇒ 工具 §11 表）；
  池外新 seed0 + 配对止损沿用（§2026-09-30-goalnn-kickstart-burn-paired）。
- **依据（§67 补评估：只评估、不重训）**：f = 3.6% 档的效应尾巴 **−8.52%**（95% CI [−14.84%, −2.21%]）
  ⇒ ① 绿线 16% 已被排除、② 效应可靠为正 ⇒ 到绿线需 ≈ **1.88×** 当前剂量；原括号（f = 3.3 / 4.0 / 7.5%）
  里只有 **7.5%** 够，4.0%（1.11×）预注册规则下已知到不了。
- **第二档为什么取 15%（4.17×）而不是 11–12%**：一阶外推不可信（惩罚项会被策略适应吸收）。若效应按
  **√剂量**走，7.5% 只到 ~12%（仍不够绿）、到绿线需 ≈3.5× ⇒ f ≈ 12.7%；**{7.5%, 15%} 同时对冲「线性」与
  「次线性」两种响应**（线性 ⇒ 7.5% 就该 ~18% 过线；√ ⇒ 15% 到 ~17%）。**这是本档位超出 ③ 预注册括号的
  原因**（§15.5：奖励语义变更 = 新实验 ⇒ b 腿走新 `--out`/`--traj`/`paired_rotate_seed`）。
- **否决**：① 「只跑 7.5% 一档」——外推若错就白付一轮，且拿不到剂量-响应形状；② 「只跑 4.0%」——1.11× < 1.88×，
  预注册规则下已知到不了绿线；③ 「按同剂量 3.6% 重测」——§67 已把同剂量那条问题答完（且已证实不到绿线）。
- **风险与预登记**：15% 档若把守卫打崩（holdFire / cellsVisited / 零伤局）而主终点没改善 ⇒ 那是**剂量上限**
  的结论、属信息不属失败；臂数 3（对照 + 两档）与 Wave 1 同价。
- **证据**：`docs/nn/experiments.md` §67（预注册 + 结果）· 工具 `nn-training/tools/paired_power.py`
  §9/§10/§11 · `tests/test_paired_power.py`。**课程草稿已落盘**：`h4-lane-b0`（零臂）/ `b1`（7.5%）/
  `b2`（15%）——三课逐字同判据（尾巴 3 点 it30/35/40 × 300 局/点 @ 池外 seed0 862001 + 同质性 Q 前置 +
  §11 每条线阈值 + paired 止损）；已过 `load_course + validate_reward`：**errors = 0**，仅 b1/b2 各 1 条
  数值包络 warn（与 a1 同款同因：域角 ticks=0 处 wLane·36000 越 1e6，随剂量线性缩放，非红旗）。
  开工闸 ①② 已完成 ⇒ 只剩 ③ **由人在控制台开课**（agent 不开训）。
## §2026-09-30-goalnn-wave2-verdict（2026-09-30，Wave 2 三臂判决：两条剂量都不到绿线；7.5% 是唯一可靠正效应、15% 判零效应 ⇒ 剂量旋钮到顶）

- **主判定（预注册判据一字未改）**：主终点 rA2 = `onLaneTicks/(ticks+1)`，配对差 = 本臂 − `b0`（同 stage 同 seed）；
  判据量 = 尾巴 3 点（it30/35/40）Δrel 均值 × 300 局/点 @ 池外 seed0 `862001`（与日常段 860001–860200、
  §67 补评估段 861011–861310 均不相交）；前置 Q ≤ χ²(0.95, df=2) = 5.991 **两点都过**（0.40 / 2.63）。
  - **b1（f = 7.5%，wLane 265）**：Δrel = **−10.60%**（SE 4.34%，p₁ = 0.007，95% CI [−19.11%, −2.10%]）
    ⇒ **未达绿线（−16%）**，但**效应可靠为正**；「不到绿线」**未证实**（CI 下沿 −19.11% 越过 −16%）。
  - **b2（f = 15%，wLane 529）**：Δrel = **+3.19%**（SE 4.48%，p₁ = 0.762）⇒ **零效应**（逐点
    +3.08 / +11.65 / −5.16 反号；日常段独立复现 −3.44%、跨点 sd ≈14% ⇒ 两套 seed 都是「≈0 ± 大噪声」）。
  - **守卫表 9 条守卫线 × 2 臂 = 18 条判语全部「未破」**（两档都不破；主终点/A1 是**目标线**，
    在实验档里读作「未达标」：b1 未达标（未证实）−10.49% / b2 未达标且已证实 +2.87%）⇒ 预登记风险「15% 档把守卫打崩」**没有兑现**；b2 的零效应
    **不是**被守卫拦出来的，而是奖励塑形没买到行为。两条余量 <1pp（`dmg/局` −9.52 vs −10.0；`stuckTicks`
    +9.14 vs +10.0，方向均有利）已记 §69 ⑦ 备查；timeout 全批 0/2700。
- **剂量-响应被判为次线性且非单调**（本批最重要的负结果）：3.6% → **−8.52%**（§67，500 局/点）·
  7.5% → **−10.60%**（实测/线性预测 = 0.60）· 15% → **+3.19%**（实测/预测 = −0.09）。
  ⇒ **「加剂量到绿线」这个 Wave 2 的核心假设被否证**；一阶外推给的「到 16% 需 f ≈ 11.3%」**不可信**
  （15% 已反号 ⇒ 外推前提「单调递增」不成立）。
- **决策**：**停止在 A1 几何上继续爬剂量**（不再开第三档）。下一步换**塑形杠杆**（几何 / 相位轴），
  任务侧（命数/敌数/关卡形态/终局标准）**一个字不动**（AGENTS §0.2）。**被否决**：① 再开 f ≈ 11% 档
  ——15% 已反号，线性与 √ 两种外推**同时**被证伪，再试点买的是同一份信息的重复；② 用 b2 的 +3.19%
  声称「15% 反而更差」——SE 4.48% ⇒ 只能说零效应，不能说反向效应；③ 用训练内 winRate/人类数字
  反推难度或阈值（AGENTS §0.2）。
- **止损实测教训（新发现，读旧日志前必读）**：三臂共享 `paired_rotate_seed`（20261001）⇒ 「对端不唯一不猜」
  ⇒ `kickstart_burn` **静默回退 `baseline`**（事件 `mode: "baseline"`、`delta_pp: null`、`peer: null`）
  ⇒ 本批**并未真正跑过 paired 口径**（峰值 streak b0 2 / b1 0 / b2 1 < 3，无 ABORT；事后按 paired 口径
  补算 = streak 0 / 1，同样不触发）。**同网格多臂课若要用 paired 止损，必须显式指定对端**，否则新口径
  覆盖不到位且无人察觉（这是 §2026-09-30-goalnn-kickstart-burn-paired 的实测首次遭遇）。
- **跨批比较的边界**：3.6% 档来自 §67（500 局/点 = 300 新 + 200 旧），7.5/15% 档来自本批（300 局/点）⇒
  剂量-响应只作**方向性**描述（同尺子、同判据、同绿线），不作严格同批检验。
- **证据（可复算）**：`docs/nn/experiments.md` **§69**（全文 + 阈值表）· 工具
  **新工具** `nn-training/tools/wave2_judge.py`（判据段归属 / 尾巴判定 / 剂量-形状 / §11 逐条 / 止损诊断 /
  日常段独立复现；共用算术仍只有 `tools/paired_power.py` 一份）· 测试 `nn-training/tests/test_wave2_judge.py`（9 例）· 账本
  `tmp/h4-lane-judge/judge_it{30,35,40}.jsonl.run/eval_log.jsonl`（2700 局；臂归属 = `wver` = `ckpt_sha16`
  = 权重文件 sha256[:16]）· 冻结权重 `nn-training/weights/h4-lane-b{0,1,2}/*.it{30,35,40}.*.json` ·
  预注册头注 `nn-training/curricula/h4-lane-b{0,1,2}.jsonc`。

## §2026-09-30-goalnn-lever-scan（2026-09-30，塑形杠杆扫描：lane 族到顶、危险/卡死/固定窗/**停轴**退场；新价唯一候选 = 伤害形态）

- **背景**：Wave 2 判「lane 剂量轴到顶」⇒ 下一步换塑形杠杆、选型未定。用已有账本把候选逐个筛一遍
  （工具 `nn-training/tools/lever_scan.py` 九节，测试 `nn-training/tests/test_lever_scan.py`；全文 `docs/nn/experiments.md` §70）。
  语料 = 判据段 2700 局 + Wave 2 日常段 + c05 h5a/h5b；**探索性**（不进 DoD/门/阈值）。
- **税基（数值地基）**：现役 lane 税（`onLaneMoveTicks`）只压在 **0.251%** 的 tick 上（lane 的 5.45%）；
  lane 全家 4.610%；danger 7.0%、dmgFirst600 2.9%、承伤 6.4%、stuck 12.9% ⇒ 剂量到顶是**结构**不是调参。
- **结局解剖（b1，逐 tick 归一）**：承伤/tick（d=−3.12）≫ lane（−1.65）≈ 开局承伤（−1.73）；
  **危险 tick 几乎不区分（d=−0.09）**、卡死反向（清关更高，+0.42）；kills 的 d/r 是**定义性重合**
  （clear = 全歼口径）⇒ 只当一致性检查。
- **相位（pooled）**：开局 600t 占 41.7% 时长 / 45.2% 伤害（密度比 1.15 ≈ 摊平）⇒ 固定开局窗**不是热点**；
  与 c05 h5a/h5b 实付账互证（机制 −34.3%、pass 仅 +2.0pp / −6.0pp）。
- **致死余量**：承伤总量非单调（阵亡下界 144 / 清关上界 316）；口径 = 非致死真实扣血、`repair` 回血不回滚
  ⇒ 伤害类价应落**速率/临近致死**，不应再往总量加码。
- **停轴已实付退场（2026-09-30 复盘更正）**：NN 决策 STOP ≈ 0.03%（引用人类 54.5% / 24.4%）曾被读作
  「表示层缺词」；实际 new-era-stop 已执行（h1–h5-stop + h4b/h5e + BC/Opening-BC）：h1/h2 过门但机制
  盲区、h3 门边未过、h4/h5 判负、能测的 rung 停率≈0（h4「v4 价格下理性弃子」）⇒ 词已补（B 案
  `97bffe7a`）、行为不出现；再碰停需新证据。
- **方向排序**：①（唯一新价候选）伤害「时间形态」（连击/短窗/临近致死）——**先补列再定价**；立项须
  说清与已死三笔（`wDmg`/h5a 开局窗/h5b 零伤）的形态差异；② 退场：**停轴**（H 系列已实付退场）/
  lane 第三档 / 危险税 / 卡死税 / 固定开局窗 / 全在线·罚站立·罚开火（更早判死）。
- **被否决的备选**：由 kills d=6.43 立「多杀」价；由阵亡组相位 0.37 立「死前窗」价；由承伤上界 316
  读「挨打无碍」。
- **边界**：本节读数**不进** DoD/门/阈值；任何新价仍须按 §15.5 另立项、另预注册（AGENTS §0.2）。
- **证据（可复算）**：`tmp/lever-scan.txt` · `nn-training/weights/h4-lane-b{0,1,2}/*.it{30,35,40}.*.json` ·
  c05 腿账本 `tmp/h5a-earlydmg/eval_log.jsonl`、`tmp/h5b-clean/eval_log.jsonl` · `docs/nn/experiments.md` §70。

## §2026-09-30-goalnn-metrics-v10（2026-09-30，plan/metrics-v10-gap-columns.plan.md；只加观测、零公式、零训练腿）

metrics 向量尾部追加「差距四族」15 列（`idx54–68`）：输入级 `stopTicks`/`fireHeldTicks`/`idleTicks`(54–56) ·
距离 `enemyDist`(57，哨兵 −1)/`nearEnemy4Ticks`(58) · 伤害时间形态 `damageBursts`(59)/`maxDamage120`(60)/
`damageWhileLow`(61) · 被包围 `encl1/2/3pTicks`(62–64) / `enclExempt1/2/3pTicks`(65–67) / `enclMax`(68)；
`METRICS_DIM 54→69`、`METRICS_VERSION 9→10`。**公式一个字不动**（reward golden 64/64 逐位不变、
oracle phi 逐位不变、旧 54 列 1944 行逐位不变），**零训练腿**（门禁绿即交付）。
**口径冻结**：被包围 = 与玩家中心垂直偏移 < `THREAT_ALIGN_BAND_PX`（19px = 坦克半宽 16 + 子弹半高 3）
∧ 沿源自己的轴无阻弹地形 ∧ 已激活 ∧ 存活的**敌车**数（**不带朝向、不带半径上限**）。**19px 不可改**——
§71 ②b 带宽敏感性三档实测：12px 旧带读数**腰斩且翻符号**（268/251）、naive「中心格同行列」直接归零
（200/200，cliff 0.05）；判不判遮挡几乎无差。豁免（冻/盾）只作**加法列**（`threatLaneExempt`；人类侧占
被包围 tick **26%** vs NN **13%** ⇒ 不成对建列会把人类算高），交集必须共位一次算好、raw 永不重定义。
输入级读**动作头**（`a_move`/`a_fire`；不读 `bullet_fired` —— 实弹被冷却/弹量上限门掉）；`enemyDist` 只算
**已激活**车（出生保护期不算近敌）；`damage*` 三列都由 `player_damage` 事件驱动（本就不含致死一击）。
**lockstep 八处**（TS 行构造 / eval 导出器 / ckpt 逐局行+汇总 / Python `METRICS`+版本+行数断言 /
`DEFAULT_RANGES` / golden / 测试 / **Python 两处 `eval_log` 行构造点经同一 helper**）漏一处即静默错读。
本次实测又踩到**同键双通道**（§24 同族）：`idleTicks` 在 `eval_log` 行里早有通道（`EVAL_NEWERA_KEYS` 族）
⇒ `EVAL_V10_KEYS` **刻意只放 14 键**，否则行构造点重复赋值；行宽对账全绿也抓不到。
**被否决**：① 把新列直接入公式（累计 tick 计数器 ⇒ Φ 逐行差分 = 每 tick 罚款且 Φ 无界，须封顶或改深度型势）·
② 只记 raw 不记豁免（人类被算高）· ③ 用 12px 旧带或 naive 同行列（§71 ②b 已实测不可用）·
④ 重复建「本来就能由 v8/v9 列推」的列（命中率/承伤率/危险 lane/低血/拾取/换格率）。
—— 全文（规格 15 列 / lockstep / 对账 / 偏差 / 门禁读数）→ `docs/nn/engineering.md` §57 ·
触发证据 → `docs/nn/experiments.md` §71 · 落地记录 → 同文件 §72 · 实施记录 → `plan/metrics-v10-gap-columns.plan.md` §5

## §2026-09-30-goalnn-damage-form-retired（2026-09-30，伤害「时间形态」E0 判红 ⇒ 家族退场；plan/damage-time-form.plan.md）

- **背景**：§70 Ⅷ ② 判「lane 剂量轴到顶」，唯一新价候选 = 伤害的**时间形态**（变「挨打的集中度」而非窗/总量/零伤）。
  定立项时先跑 **E0 判决表**（预注册 K1/K2/K3/K4，plan §5）——探针 `tmp/dmgform-e0.ts`（不入库，reported 级），
  bc = `h4-stop.it60.best` @ `ladder-c04`（n=200，seed 860001–860200）+ c20 对照（n=100）。
  探针保真 = 与权威账本 `tmp/e1b-it0-h4stop-c04.jsonl.run/eval_log.jsonl` **逐 seed 全等**（pass 86.0% / dmg 89.17 / ticks 1405.0；
  per-seed damage/ticks/outcome 200/200 相等）。
- **判决**：三形状**全不过**预注册 K 表 ⇒ **本族退场**（不做第四次；前四笔 = `wDmg` 常驻 / h5a 开局窗 / h5b 零伤 / `dangerTicks` 同族）。
  S1（状态条件型 `damageWhileLow`）死于 K1（0/2 档，重档**反向**）+ K2/K2′（无行动窗口）；S2（记录型 `maxDamage120`）
  死于 K1（1/2 档）且与总量 Spearman **0.948**（同族）；S3（事件型 `damageBursts`）死于 K3 稀疏线（0.315 事件/局）。
- **机制（结构性，不是「剂量不够」）**：① `maxHp 263` × 阀值 0.4 ⇒ `hp<105`——阈值以下最多还能计 ~105 点**非致死**承伤，
  该列**天花板 = 剩余 hp**；② **最危险的那一笔不在口径里**（`player_damage` 只推非致死命中，致命一击走 `player_hit`）；
  ③ 实测 ①+②：**87%**（c20 81%）的低血段承伤就发生在**把你打到 0.4 以下的那一击**（低血段第 0 拍，受伤前决策步中位 0）
  ⇒ 该列量的是「**跨线**」而不是「跨线之后继续挨打」；④ ∴ 「临近致死价」语义上退化为「总量税的尾部加权」，
  而总量税买的是**保守**（三笔实付）。
- **税基事实（留档）**：c04 零伤局 41%（82/200）· 有 lowDmg 的局 20.5% · 低血段扣血事件 0.23/局 ·
  lowDmg 中位 0 · 低血态 tick 里 93.9% 都在 lane 外（即「在线挨打」不是形态）·
  rung 桥 lowDmg 每千 tick c04 14.07 / c20 65.01 = 0.216× ⇒ 缺口闭合 89.3% ⇒ Wave 1/2 的「R/2 绿线（44.7%）」**不可达**。
- **被否决的备选**：① “加价”（剂量两次证平：2.08× 只 +2.1pp、4.17× 反号）；② S1+S2+S3 三件套（多变量）；
  ③ `-w*dangerTicks`（d=−0.09 不区分胜负 + tick 计数器 Φ 无界）；④ 致死余量阈值型（Y 无锚：
  阵亡下界 144 / 清关上界 316 重叠，无干净切点）；⑤ “换成 S1”（替换而非追加：形态效应与去掉总量税不可分）；
  ⑥ 改 `DANGER_HP_THRESHOLD`（0.4→0.5，改谓词 = 新 metrics 纪元）；⑦ c20 rung（功效差 + 守卫基线全要重 pin）。
- **重开硬门槛**（任一，不必全）：① 观测口径能带上**致死一击**（`player_hit` 带 damage；观测层改动 = 新 metrics 纪元 + 新条目）；
  ② 出现「**可行动的低血窗口**」证据（低血段平均决策步 ≥3 且 π_new ≥0.5）。**两条都没有 ⇒ 不再重启本族。**
- **顺带定的一条口径**（以后新价要在本仓定绿线时用）：rung 上的「缺口」可能大到 ≫16%（本轮 89%），
  「完全闭合的一半」作为绿线会**不可达** ⇒ 绿线用 Wave 1/2 的**固定 16%** 档，把「完整闭合」另立长期腿。
- —— 读数与机制 → `docs/nn/experiments.md` §73 · 预注册与偏差记录 → `plan/damage-time-form.plan.md` §5 ·
  起点（势能杠杆扫描选型）→ `docs/nn/experiments.md` §70 Ⅷ ② · 列前置 → §72 / §64。

## §2026-10-01-goalnn-exempt-bakein（2026-10-01，豁免机制改「导出器拌入计数列」（决定 A）；plan/aim-dodge-levers.plan.md v10 增补批次）

- **背景**：v9 起豁免以「raw + 加法豁免列」对存在（`onLaneExemptTicks`、v10 `enclExempt1/2/3pTicks`）；一个豁免场景要为每个受影响族各加列（族×场景）。
  用户 2026-10-01 指令：找不随场景加列的通用表达，并拍板方案 A。
- **否掉的三个方案（机制证据，不是偏好）**：
  ① 公式侧逐行 flag × 累计列——翻转拍拿**整局累计值**乘 0/1 ⇒ 幻影 ± 尖峰；引擎禁跨步归约（白名单无 sum/cumsum），逐拍交集必须共位算（v9/v10 列注释已写死）；
  ② 公式侧「每窗常数扣减」（`C − (10 if freezeTimer>0 else 0)`）——差分下**恒等抵消**：状态偏移不是流量，整段冻结净扣 0，只留 ±10w 边缘脉冲；
  ③ 入库 `freezeTimer/shieldTimer` 观测列代豁免——timer 是决策点快照，公式拿不到逐拍交集，替代不了共位计数。
- **决定（A）**：新计数列一律**只累计非豁免拍**（导出器 `exposureExempt(world)` 拌入）；不再产出 raw/exempt 对；
  新增豁免场景 = 改谓词函数一处，列与公式零变更。谓词口径 = `freezeTimer > 0 ∨ 盾道具窗`（粘性 tracker；出生/复活盾与星盾不计入豁免）。
- **边界**：v9 已签入列（`onLaneTicks/onLaneExemptTicks/onLaneMoveTicks/onLaneHoldFireTicks`）保留原 lineage 不动；
  v10 未签入批次内移除 `enclExempt1/2/3pTicks`（净价用途被 A 取代，原始三档观测保留 raw）。
- **代价（明示）**：库内列不再是纯物理量（「定价口径」）；跨改动读数需标注；豁免份额审计移到探针/离线（不入库，粗代理 = `puGotFreeze/puGotShield`）。
- **被否决备选**：per-场景豁免列（原方案）· per-族豁免列（B）· 公式侧 flag / 常数扣减 · timer 观测列。
- 落点：`plan/aim-dodge-levers.plan.md` §1.2/§3.3/§4；v10 plan 注记随实现更新。
## §2026-10-01-goalnn-aim-dodge-metrics（2026-10-01，aim-dodge 杠杆 8 列落地：idx66–73 / dim 74 / 事件扩展 / 回写机制；plan/aim-dodge-levers.plan.md §11）

- **背景**：`docs/nn/experiments.md` §71 人类 vs NN 面板判出「射术 / 暴露管理」四族全程分不开，但 v9 无列可测；
  用户 2026-10-01 定稿 aim-dodge 批次（开火结果族 / 火线承伤族 / 暴露杠杆族）并**裁决保留 `aimBricks`**（经典关复用）。
  规格与逐条评审裁决 → `plan/aim-dodge-levers.plan.md`（§0.2a ms 24 条 + §0.2b sb 19 条）。
- **决定（列布局）**：v10 批次**未签入** ⇒ 批次内整理 + 追加：移除 `enclExempt1/2/3pTicks`（原 idx65–67，兑现改
  决定 A「拌入」）、`enclMax` 顺位 68→65；追加 8 列（idx66–73）——`aimHits` / `aimHitDistSum` / `aimBricks` /
  `aimIgnited` / `aimMisses` / `hurtWeight` / `enclWeightTicks` / `cornerWeightTicks`
  ⇒ `METRICS_DIM 69→74`，**`METRICS_VERSION` 保持 10**（升 11 被驳回：持久产物中不存在「版本 10 + 69 列」）。
- **决定（引擎事件，additive 只读）**：`player_damage`/`player_hit`（致死 + 星盾两条）补 `bulletId`；
  `terrain_destroyed` 4 处补 `bulletId`；新增 `bullet_cancelled{aId,bId}`（对消唯一归因口，几何近似与派生半径作废）；
  `spendStarShield(tank, bullet)` 签名修复（旧签名拿不到弹 ⇒ 星盾承伤静默漏记）。事件不进 `tickHash`
  ⇒ `freeze:check` 逐字节不变。
- **决定（唯一新机制：回写）**：跨步归约不在公式白名单 ⇒ 信用在 TS 导出器做后缀累加：开火时记 `patchFrom`、
  结算时 `applySuffixPatches`（纯函数）给已推行补区间 delta ⇒ 信用落**开火决策步**；本批腿限定**均匀 K** 课程、
  `--init-snapshot` 关闭。只结算 registry 内登记弹（交棒弹不入桶；恒等式由构造保证）。
- **口径**：`playerShots = aimHits+aimBricks+aimIgnited+aimMisses`（局末在飞/出界/打钢未破/基地折入 miss）·
  三列（hurt/encl/corner）**拌入豁免 A**（窄谓词 = 冻 ∨ 盾道具窗；≠ v9 `threatLaneExempt`）零伴列 ·
  角锚点只从 `GRID` 派生（`enemy_spawns` 降一致性断言）· raw-only 名单（`encl1/2/3pTicks`/`nearEnemy4Ticks`/
  `onLaneMove|HoldFireTicks`）**仅审计不得定价**。
- **证据/门禁**：根 `bun run check` 2306 pass / 0 fail · `build` ✓ · `freeze:check` FROZEN OK · 决策检查 ok；
  双端对账（训练侧恒等式 + 评估侧 God 同局事件流独立重算）与回写 oracle（Python 合成 4 行）落测；
  **E2 前置 Pearson（b0 探针 160 局）= 0.533 < 0.8 ⇒ 不替换 b0 包 `wDmg` 项**；hurtW/hurtN ≈ 28.97 / CV 0.474
  ⇒ E2 按「命中数重定价」剂量纪律记档，不得宣称弹道机制。
- **落点**：`plan/aim-dodge-levers.plan.md` §11（实施记录）· `docs/nn/engineering.md` §58 ·
  `docs/nn/threat-lane-reward.md` §12 · `docs/nn.progress.md` §3.5 索引 · `nn-training/rl/eval_ingest.py`
  （m1 通道**显式不接**本批列）· `tools/sim/aim-shot-registry.ts`（双端共用 settle-once）。
- **欠账**：§5.4 rate 表待 b0 ≥200 局实测；c04 复测门（encl/corner 信号）；E1 形式（计数 vs 比值）列落地前可改；
  过渡期 69 列旧 shard 需在发布/认领前清空（行宽门会拦）；E2 最终门按现役拌入列复算。**零训练腿**（E1–E4 开课 = 人在控制台点）。
## §2026-09-29-goalnn-pytest-scan-cache（2026-09-29，最慢用例清算：全仓扫描「先廉价预筛、再 parse」+ 派生小结果才可缓存）

**决策**：仓库内**任何「全仓源码扫描」守卫**（分层 / 单一来源 / 注入点 / 退役标识 …）都必须先付一道
**廉价子串预筛**，再 `ast.parse` 或 `tokenize`；跨用例重复要的**派生小结果**（模块名集合 / 顶层定义面
/ `self.<attr>` 调用计数 / 文件清单）统一住 `tests/helpers/source_scan.py` 并 `functools.cache`。
**AST 本身不得常驻**（原 `parse` 的 `@cache` 已删：常驻 653 份 AST 让之后每次 `gc.collect()` 多付
~0.74s，GC 开着实测 2.55s vs 0.82s）。

**被否决**：① 给 `source_scan.parse` 恢复 `@cache`（重复解析的毫秒 vs 常驻 AST 的秒级 GC）·
② 用单次 `--durations` 判「最慢」（本机负载 1.13× 波动 ⇒ 排名随负载换人、改前改后不可比）·
③ 把真训练用例（`test_bc_epoch_resume`）改小来提排名（会削窄守卫语义）·
④ 调生产缺省去迁就测试（一律提为参数，生产缺省不动）。

**违反后果**：新写的守卫又对全仓 `ast.walk`/`tokenize` ⇒ 单条用例秒级、`sum_min` 静默回涨
（本机 653 文件 × 每条判据一次解析）；把 AST 挂进模块级容器 ⇒ 之后每次 GC 都遍历百万级节点图。

—— 全文（测速配方 / 十条对照 / 硬顶 0.5s 的实测依据 / 效果表）→ `docs/nn/engineering.md` §43

## §2026-09-29-goalnn-wait-gates-not-test-walls（2026-09-29，剩余 ~1s 用例群清算：「闸门时长」不得同时是用例的墙钟）

**决策**：测试侧提速时，凡遇到「墙钟 = 等满某个**为生产语义**写的闸门/地板/步长」，一律
**把那个量提成旋钮**（具名模块/类常量、参数或实例属性），**生产缺省逐字不变**，用例按自己的
判据需要调小；**不得**改判据去迁就时长，也**不得**为了让排名好看去改生产缺省。
本批落地的旋钮：`ServePool.READY_BUDGET_FLOOR_SEC`（就绪预算 1.0s 地板）、
`dist_common.POLL_MIN_BUDGET_SEC`（轮询预算 1.0s 地板）、`policy.tailGraceJoinSecDeadline`
（窗到期后的在飞 join grace，生产缺省 5s）、`BulkScheduler._yield_step`（让路单步 0.5s）。
同类先例：`tripped_idle_polls`（熔断后空等）、`transientBackoffSec` / `nodeRecoverFirstSec`
/ `recoverPingSec`（回场与退避配速）、`pool.breaker_after`。

配套一条**真的行为修正**：`rl/dispatch.py` 收尾的 `all_settled.wait(0.5)` 现在**钳到 deadline**
（原实现退出条件下一轮才检查 ⇒ 「窗口到期」实际晚到最多 0.5s；`queue_local` 的 rescan 循环
早已同源钳过）。生产窗 1800s 无感，且**不改任何结局**——窗口到期后循环本来也只会退出。

**被否决**：① 把 `test_rollout_dispatch_resilience` 的窗口 0.6s 缩到 0.35s（实测工时极稳
0.173s，但余量从 3.5× 掉到 2×；本仓为负载抖动付过太多假红）· ② 收紧
`test_single_ppo_path` 预筛到 `.ppo` 字面量（68→10 文件）：会漏「`args` 与 `.ppo` 间插注释」
的合法写法，而守卫失效是**静默**的，0.2s 不值一个盲区 · ③ 给 `tests/helpers/remote_dag.py`
的 `graph()/remote_modules()` 加缓存（返回可变嵌套字典 ⇒ 跨用例污染）· ④ 硬压 `e2e/`
（真进程/真 I/O 的集成层，要压得动分发与装载面，是另一个题目）。

**违反后果**：新用例又盯着生产地板过日子 ⇒ 单条 1s 级安静回归（不占 CPU、不报错，只在墙钟
上现形）；或反过来，为了测试把生产地板调小 ⇒ 生产语义被测试绑定（§14 的「死测试」镜像）。

—— 全文（定位手法 cProfile / 20 条对照 / 效果表 / 被否决）→ `docs/nn/engineering.md` §44

## §2026-09-29-goalnn-fork-after-collect-runner（2026-09-29，Linux 门禁的 pytest 分发改成「收集一次 + fork」）

**决策**：nn-training 的 pytest 分发在 **Linux** 上改用「master 收集一次 → `os.fork()` 出 worker」
（`nn-training/tools/forkdist.py`，POSIX-only 插件，`--forkdist N` 显式开启，与 xdist 的 `-n`
**互斥**）；**Windows/macOS 继续 xdist**。判据与既有的「双向路径」同源：**看选中的 python 是
什么**（`case "$NN_PY" in *.exe) → xdist`），再看内核（`uname -s` = Linux 才 forkdist），
**不看 uname/wslpath 存不存在**（2026-09-20 同型事故）。逃生口 `NN_GATE_FORKDIST=0/1`。

**为什么**（§48.5 记的「唯一剩下的结构性杠杆」）：`-n 12` 下**每个 worker 都要收集全部 ~275 个
测试模块**，12 份里 11 份纯冗（占 worker 自用 CPU 的 35%）。fork 让子进程经 COW 继承
`sys.modules` 与已收集的 `Item`，收集相从「12 × 4.8s 争用」降到「1 × 2.4s」。16 核实测
（各 3 轮轮转取 min）：墙钟 **22.69 → 19.81s（−12.7%）**、user **191.7 → 115.3s（−39.9%）**、
sys −4.5s；门禁 24~26s → **20s**。整树峰值 RSS 持平（4.77 → 4.81GB）——**别拿省内存当理由**。

**换 runner 不许换测试语义**（逐条对齐 xdist）：逐条动态派发（= `--dist=load`，不按文件切块——
§47 已实测批派更慢）· `nextitem` 预留（module/class 夹具不被提前收）· 报告经
`pytest_report_to_serializable` / `pytest_report_from_serializable`（xdist 同源的那对**核心 hook**）
回传并重放 ⇒ 汇总行 / `-ra` / `--tb=short` / `-x`(maxfail) / `--timeout` 全是 pytest 自己的语义 ·
子进程摘 TerminalReporter（否则 12 份进度/汇总打在共享 stdout 上）· 填 `config.workerinput`
（cacheprovider 的 `lastfailed` / junitxml / stepwise 据此跳过 master-only 收尾）· 子进程自己跑
`pytest_sessionfinish`（session 夹具 finalizer + `tests/conftest.py` 的临时目录清理）·
Linux 上 `PR_SET_PDEATHSIG`（`nn-wall.py` 在 POSIX 只杀树根，否则留下孤儿 worker）。

**两条红线**（都是一旦丢掉「不会立刻红」的失效）：① **丢用例必须响**——worker 死在半途时 master
对「派了但没上报」的用例合成失败报告并点名 worker（否则整批少跑而退出码仍是 0）；
② **子进程必须重建全局捕获**——继承来的捕获临时文件是同一个 open file description（共享偏移）
⇒ 12 个进程互相读到**半个 UTF-8 字符**，且被归因到**前一条**用例的 setup（实测 5/5 轮红）。
两条都在 `tests/tools/test_forkdist.py` 里钉住；门禁分支选择用假仓库骨架真跑两种 python 钉住。

**被否决**：按文件静态切块（本套用例异质，长尾拖死整块，同 §47 否决 `loadfile` 的理由）·
`pytest-forked`（per-test fork、不并行）· 拿「fork 省内存」当卖点（实测持平）· 把 forkdist 推广到
`task.py` / Makefile / CI（那些入口没有实测收益记录，保持 `-n`）。

**违反后果**：把 `-n` 加回 Linux 分支 ⇒ 每轮多付约 11 份收集（+76s CPU、+2.9s 墙钟）；
删掉 `_reset_global_capture` 或「先关命令管道再 waitpid」⇒ 满机时以「随机用例报半个字符」或
「门禁整段挂死」的形式假红。

—— 全文（实测表 / 为什么 CPU −40% 而墙钟只 −2.9s / 设计要点 / 两个坑的定位手法 / 复现配方）
→ `docs/nn/engineering.md` §49

## §2026-09-29-goalnn-forkdist-entrypoints（2026-09-29，`task.py` / Makefile 的 pytest 分发也改 forkdist，CI 实测后继续 `-n 2`）

**决策**：把 §2026-09-29-goalnn-fork-after-collect-runner（当时**只接了门禁**、并把「推广到
`task.py` / Makefile / CI」列为未做）逐个入口实测后处置——**`task.py`（`check` / `test*`）与 `Makefile`（`test` / `test-fast` / `test-e2e`）采纳**：
Linux 上分别走
`-p tools.forkdist --forkdist auto` 与 `-p tools.forkdist --forkdist $(NPROC)`，Windows/macOS 继续
`-n auto` / `-n $(NPROC)`（判据与门禁同源：**Linux 且 `os.fork` 在**）；**CI 两层继续 `-n 2`**，
那句「为什么不用 forkdist」的实测依据写进 workflow 文件本身。

**为什么（各自实测，16 核，交错 2 轮取 min）**：Makefile 真入口 `make -s test-fast` min 墙钟
**19.73 → 15.05s（−23.7%）**、user **203.9 → 111.4s**；`task.py test-fast` 真跑 rc=0 / 15.43s；
两入口共用的 fast 路径（`tests/` + `e2e/`）**21.99 → 17.57s（−20.1%）**、user −48%。比门禁那次的
−12.7% 更大是因为**收益 = 冗工份数 = worker 数**：门禁 `min(核数, 12)` = 12，这些入口 `auto` = 16。

**CI 为什么不换（实测否决）**：forkdist 省的是「N 个 worker 同时收集」的**争用**，而 runner 只有
2 个 worker ⇒ 无争用可省。`taskset` 模拟 runner 规模（分层单跑、`--maxfail=0`）：2 vCPU 单测层
**44.95 vs 45.03s（同价）**、e2e 层 7.05 vs 7.80s（xdist 反而快）；4 vCPU 单测层 41.51 vs 40.93s
（1.4%，噪声内）。CPU 仍低 ~12%，但 CI 的判据是墙钟。**换多核 runner 要重测**——护栏钉的是
「换之前必须先有实测且记录不能丢」，不是「永不许换」。

**配套**：`--forkdist auto` 必须解析成 `platform_utils.effective_cores()`（本仓「本机几核」只允许
一个答案；容器里 `os.cpu_count()` 报宿主机核数——2026-09-25 云机卡死那笔账），拿不到就拒绝而非
猜数；解析值挂 `config.forkdist_workers`，校验两道（选项转换器 + `pytest_configure`）。
`NPROC=8 make test` / `PYTEST_DISPATCH=…` / `THREADS=…` 这些覆盖旋钮的打法一律不变。

**被否决**：CI 换 `--forkdist 2`（上面实测）· 给 `task.py` / Makefile 的 **Windows 分支也塞
forkdist**（没有 `os.fork`，插件当场 `UsageError`，等于写个假分支）· macOS 上开 forkdist（§49.5 的
torch-在-fork-前已有 import 的面未验证）。

**违反后果**：把 `-n auto` 加回两个入口的 Linux 分支 ⇒ 每轮白付 ~4.7s 墙钟与近一倍 user；
给 CI 换 forkdist ⇒ 只拿到 CPU 降幅、墙钟不动，而且下一个人会以为「没试过」再测一遍。

—— 全文（入口实测表 / CI 两规模实测表 / `auto` 语义 / 接线护栏与 `MAKEFLAGS` 那个坑 / 复现配方）
→ `docs/nn/engineering.md` §50

## §2026-09-29-goalnn-single-instance-guard（2026-09-29，一个 node id 只准有一个 agent 在听：互斥在应用层，不靠内核 EADDRINUSE）

**决策**：「一个 node = 一个监听实例」是 codeHash 门的前提，必须由 agent 自己保证。起听**之前**做一次
互斥判定（`tools/agent/single-instance.ts` 纯函数，判定表见全文）：**HTTP 探活 `/v1/status` 优先于 pid
锁文件**；`<WORK_DIR>/agent.lock`（`tmp/dist-agent/agent.lock`）用 `fs` 的 `'wx'` 独占创建作唯一互斥
原语（同时冷启动只有一个赢家），且锁在**预热之前**就抢。缺省 **fail-closed：拒起**（`exit(2)` + 一行
指路日志：既有实例的 `pid`/`codeHash`/`bootId`/`uptime` + 怎么停它）；接管只认两个显式入口——
`--takeover`、或 `/v1/restart` 交接时由父进程注入的 `SAMPLER_HANDOFF=1`（+父的 bootId；child 只准接管
**自己的父进程**）。`/v1/status` 与 `/v1/ping` 加法新增 `pid`/`bootId`/`instanceGuard`（旧字段逐字不变）。

**为什么**（h3-geo / x20-geo it1 云端 `grad` 5 连炸）：`serveWithRetry` 为重启链在 Linux 开了
`SO_REUSEPORT`（TIME_WAIT 期间即时重绑），副作用是**第二个实例绑同一端口也静默成功**——EADDRINUSE
这条护栏被拆掉了。a98 上两个 agent 并存 ⇒ 一轮 192 局被内核按 4 元组哈希分到两个代码版本 ⇒ 旧版本
（metrics 45 列）shard 进 payload、云 worker 是 v9 ⇒ 5 轮连炸、整门课 aborted；同时 `/v1/restart` 可能
重启错那个实例、`codeHash` 门变成抽签。Termux/proot 无豁免（proot 只伪造 `getuid()` 视图，内核看到的
仍是同一个 Termux app UID ⇒ 同 euid 约束天然满足）。

**被否决**：① 删 `reusePort`（TIME_WAIT 那条腿会退化成 120s 重试 / 崩——内核那条路是重启链的底座，
不能拿来当互斥）· ② 只靠 pid 文件当判据（pid 只在交接路径写、且会被系统复用；`reusePort` 下 bind 必
成功 ⇒ 没有「真的有人在服务」的判据）· ③ 缺省自动杀旧实例（共享 Termux/proot 环境里杀错进程代价高；
要接管就显式说）· ④ 「换个端口 / 不带 `reusePort` 再试」当兜底（把「同 node 两版本」变成「同 node 两
端口」，门与账本照样失效）。

**违反后果**：退回「第二个实例静默共存」⇒ `metrics_version` 混血、`grad` 连炸、整门课 aborted（发布端
的行宽版本门只拦得住「已进 payload 的旧版本」，拦不住「一个 node 两个版本」这个更上游的事实）；把
`'wx'` 换成「先 stat 再 write」⇒ 同时冷启动的两个实例双双通过判定。

—— 全文（现场 / 三平台对照 / 判定表 / 交接标记 / 观测字段 / 真机排查配方 / 本机复验读数）
→ `docs/nn/runtime-opt.md` §29

## §2026-09-29-goalnn-node-bootid-gate（2026-09-29，节点门多一道「本轮 bootId 一致」：训练侧用 ping 的 pid/bootId 判 stale 并排除）

**决策**（用户裁定，采纳 `plan/sampler-single-instance.plan.md` §8-Q2 的**备选**而非其原推荐）：训练侧
节点门在 codeHash/bun 之外多一条「**本轮 bootId 一致**」——新模块 `nn-training/rl/node_identity.py`
（纯逻辑 + 进程内小账本，零依赖）按 `round_key(腿, 采集单元 id)`（`rollout:it42` / `eval:it42.eval` /
`batch:run.b7u3`）在**本轮**第一次 ping 时钉住该节点的 `bootId`（只钉一次，不一致后不改写 ⇒ 重复 ping
原因幂等），之后同键 ping 不同 ⇒ 本轮**排除**该节点：rollout 准入与回场（`rl/dispatch.py` /
`rl/queue_local.py`，两处共用同一个键）· A-eval 准入（`rl/eval_dispatch.py`）· B/C 回场
（`rl/batch_runner.py::_UnitLanes.bringup`，复用 `given_up` 以保住「全部通道已放弃 ⇒ 收摊」的退出条件）。
**只上报 + 排除，绝不下发 `/v1/restart`**（两个进程同时服务不是重启一个能修好的事）。

**为什么**：① a98（2026-09-29）一台机器两个 agent 同听 8443，一轮 192 局被内核按 4 元组哈希分给两个
代码版本 ⇒ 45 列旧 shard 混进 payload ⇒ 云 worker `grad` 5 连炸、整门课 aborted；② agent 侧的应用层互斥
（`tools/agent/single-instance.ts`，本次同批上线）是**节点自证**，本门是**客户端观测**的第二道；
③ 与 codeHash 门相比它**更早**（不需等一个代码版本差异，同一份代码的进程换代也看得见），且在同端口双
进程下 codeHash 门是**抽签**（同一轮 ping 落在哪个进程上看 4 元组哈希），而 bootId 变化正是这个缺陷的指纹。

**被否决**：① plan 原推荐「先只记进 `dist-agent-meta.jsonl` 供归因、不加门」（用户改判：要更早止损）·
② **全局**（跨轮）钉：一次正常升级/重启就换 bootId ⇒ 会把「重启前后的一轮」误伤；按轮钉把影响面限在
触发变化的那一轮、下一轮自动回场（代价：轮中途重启的节点本轮不再被用——已接受并写进文档）·
③ 把这条塞进纯函数 `node_gate_reason`（判据需要**状态/账本**，纯函数不许长状态）·
④ mismatch 时顺手重启/升级节点（§1.4-2「不自动杀旧」同理）。

**违反后果**：对新 agent 以外也生效（对旧 agent 不 fail-open）⇒ 升级波期间把还没升级的节点整批排除；
拿 bootId 当**跨轮**判据 ⇒ 每次正常升级都白搭一轮；把这条判据换成「看 ping 命中哪个进程」（不做账本）⇒
它在只 ping 一次的腿上永远不会响（判据名存实亡）。

—— 全文（判据 / 四个消费面 / 已知代价 / 接线守卫与测试 / 门禁读数）→ `docs/nn/training-stack.md` §26
（agent 侧契约与真机配方 → `docs/nn/runtime-opt.md` §29）

## §2026-09-30-goalnn-nn-training-module-reorg（2026-09-30，nn-training 按角色重组为六包：`common/biz/worker/remote/hub/trainer`；第一刀 hub 出包）

**决策**（用户 2026-09-29 拍板口径，2026-09-30 追加 Q5 并落地第一刀）：把 `nn-training/` 的文件从
「按历史拆分动机散落」重排为**六个按角色命名的包**，并把依赖方向写成**可机器校验的偏序**：

```
L0 common/                 stdlib-only（含 platform_utils / pid_probe / schema / dist_* / jsonc）
L1 biz/ · models/ ppo/ data/ train/ scripts/     纯逻辑：领域判据（biz）与**算法栈**（保持顶层并列）
L2 worker/                 iter_rollout · serve_pool（节点侧执行体）
L3 remote/                 跨端线路 + 云引导 + 云 worker
L4 hub/ · trainer/         hub 服务端 / 本机训练编排（+ 根入口薄壳）
```

**第一刀（本次已落地）**：hub 服务端从 `remote/hub/` + `remote/hub_server.py` 出包为**顶层 `hub/`**，
入口从 `-m remote.hub_server` 改为 `-m hub.server`；三个「站在门面上的运维工具」（`smoke_loopback` /
`tunnel_ab_probe` / `backfill_offline`）随刀搬进 `hub/`；`remote/hub_client.py` / `remote/hub_http.py`
**不动**（它们是客户端侧线路，不是服务端）。

**为什么**：① `remote/` 原来是一只大伞包（传输 + 云机执行体 + hub 服务端三件事一包），
「这段代码在哪个角色上跑、能依赖谁」读不出来；② 分层守卫（`tests/test_layering.py` 三层 +
`tests/helpers/remote_dag.py` 的秩账本）已经存在，但 `remote/hub/` 把服务端藏在传输包下面，
「hub 不许依赖 trainer」这类判据写不出来；③ 出包后 hub 与 trainer 成为**同层对端**，
「互不 import」第一次成为可断言的性质（实测零条）。

**被否决**：

- **Q2 原答复「算法栈并入 trainer/」**（用户先选，审计后改判 Q5「保持顶层并列」）：实测
  `remote/bc_job.py` 与 `remote/train_core.py` **就是云机 worker 的训练/评估执行体**，import
  `ppo.engine` / `ppo.common` / `ppo.goal` / `ppo.intent` / `data.weights_io` / `train.bc` ⇒
  算法栈进 L4 会立刻长出 **9 条 `remote → trainer` 向上边**（外加 2 条 `biz → trainer`）；
  另有硬编码跨项目路径 `nn-training/data/state-init-bank`（`tools/sim/build-state-init-bank.ts`）与
  `nn-training/scripts/regen_v7_ts_oracle.py`（`tools/diag/v7-phi-oracle.ts`），且 `data/` 同时是算法包
  与数据目录 ⇒ 搬它等于顺手动数据目录与 TS 硬编码路径。
- **`worker/` 与 `remote/` 并列（同为 L3）**：`serve_pool` / `iter_rollout` 实测零 `remote.*` 依赖，
  却同时被云机侧（`remote/offline_eval.py`、`remote/worker.py`）与 trainer 侧（`rl/dispatch.py`、
  `rl/queue_local.py`）引用 ⇒ 放在 `remote/` 之上会得到 4 条向上边；坐在它**下面**（L2）则四条全是
  向下边、零豁免。
- **`common/` 里 `dist_*` 保留前缀**：用户裁定**去前缀**（`dist_shard` → `common/shard.py` 等；
  刀 2 落地），理由是同包内再带 `dist_` 只重复「分布式」这个已由包名隐含的词。
- **`tests/` 目录同步重组**：用户裁定暂不动（目录不重排，但里面**写死模块路径/点分名的断言必须随刀改**）。

**本刀踩到並记档的四个坑**（都是「测试绿也会静默瞎掉」那一类）：

1. **`remote_dag` 的 AST 门禁写的是 `startswith("remote")`** ⇒ hub 出包后 `from hub.http_face import …`
   被整支跳过，hub 内部边**一条都不进账本**（症状：层号对账报 `hub.boot` 应为 L4）；修成
   `ledger_package()`（同时覆盖 `remote` / `hub`），账本扩成**一账两包**（不另开一份会漂的名单）。
2. **按叶子名判据会被标准库撞车**：守卫原来用叶子 `"hub_server"` 判「混入不得反向 import 组装模块」；
   改名后叶子是 `"server"`，而 `from http.server import ThreadingHTTPServer` 的叶子也是 `server` ⇒
   `hub/boot.py` 被误判成环。改成**点分全名**（`hub.server`）判据。
3. **`test_subproc_util` 的 spawn 标记要跟着改形**：入口改名后 `"hub.server"` 不再只出现在 argv 里
   （账本的字典键 `"hub.server": 7`、守卫里的名字比较都带这个串）⇒ 拿旧标记会把两个**不起服务**的
   守卫误判成 spawner；标记改成**带尾逗号**的 argv 元素形态（那正是「它是一个 argv 列表元素」的形状）。
4. **⚠ 搬家会静默改掉 `__file__` 深度算术**：`hub/store_offline.py` 的 `REPO_ROOT = parents[3]`
   在原 `remote/hub/` 下是对的，搬到 `hub/` 后少一层 ⇒ REPO_ROOT 指到仓库的**父目录**（不报错、
   只是路径全错，由工装用例当场抓住）。以后搬 python 文件必须 grep `parents[` / `__file__`。

**违反后果**：① 保留 `remote/hub/` 或留一个转发壳 ⇒ 两套名字长期共存，「谁是真家」再次失守
（本仓第十刀起就定过「名字是契约、位置不是」，出包是这条规矩第一次真正被执行）；② 在 `hub/*` 里
import `hub.server` ⇒ 与「`server` import 实现簇」成环，本仓对环只有「下沉共同依赖」与「参数注入」
两种处理（`DEFERRED_CYCLES` 至今为空）；③ 忘改 `pyproject.toml` 的 `packages.find` ⇒ 装机环境
（`pip install -e .`）下 `import hub.*` 直接 ImportError，而测试靠 conftest 的 `sys.path` **看不出来**。

—— 全文（六桶判据 / 完整文件→包映射表 / 契约与守卫迁移清单 / 五刀刀序 / 反探针）
→ `plan/nn-training-module-reorg.plan.md`；目标分层审计器 → `nn-training/tmp/reorg_audit.py`
（AST 扫全部 import 边，当前违规边为 **0**）。

---

## §2026-09-30-goalnn-nn-training-l0-consolidation（2026-09-30，刀 2：`common/` 收口为**唯一 L0**；`dist_*` 去前缀）

**决策**：把散在 `nn-training/` 根下的 11 个 L0 模块全部收进 `common/`，其中三个 `dist_*` 去前缀：

| 旧家 | 新家 | 备注 |
|---|---|---|
| 根下 `platform_utils.py` / `pid_probe.py` / `schema.py` / `log_bundle.py` | `common/{platform_utils,pid_probe,schema,log_bundle}.py` | 形状从「顶层模块」变成「`common` 包的子模块」 |
| 根下 `dist_common.py` / `dist_shard.py` / `dist_weights_ledger.py` | `common/{distribution,shard,weights_ledger}.py` | **去 `dist_` 前缀**（用户裁定） |
| `rl/jsonc.py` | `common/jsonc.py` | `rl/` 是上层，L0 的解析器不该住那 |
| `remote/net_http.py` / `remote/_instance_lock.py` / `remote/_port_guard.py` | `common/{net_http,instance_lock,port_guard}.py` | 后两个**去下划线私有前缀**（同包内本来就要用，`_` 名实不符） |

配套：`pyproject.toml` 的 `py-modules` **清空**（旧清单里那五个顶层单文件已不存在），`common*` 成为 L0 的唯一入口；
`tests/test_layering.py` 新增两条机械守卫 —— `test_l0_top_level_single_files_are_gone_from_the_root`
（根下不留旧名，且新家必须存在）与 `test_the_moved_modules_are_gone_from_remote`（`remote/` 不留旧家）。

**为什么**：① L0 的定义是「stdlib-only、零上层依赖」，但它当时**散在根下**，只能靠「`NN_ROOT` 进 `sys.path`」
被 import —— 「哪一层」这件事读不出来，`test_common_is_a_leaf_package` 也扫不到根下的文件；
② 三个引导模块（`dist_common` 族）本来就与 `common/` 同层级、同依赖纪律，住一起才让「L0 = 一个包」这句话成立；
③ `remote/` 里那两个下划线哨兵模块（单实例锁 / 端口守卫）**同时被 `hub/` 与根入口引用**，坐在 `remote/` 里是
「下层依赖上层」，收到 L0 是唯一合法位置。

**被否决**：

- **保留旧名转发壳**：搬完在根下留 `dist_common.py` 那种「`from common.distribution import *`」壳，会让
  「两处都能 import」长期共存 —— 本仓第十刀定的「名字是契约、位置不是」正是靠「删掉旧家」执行的。
- **保留 `dist_` 前缀**：用户裁定去前缀（同包内 `dist_` 只是重复包名已隐含的词）。
- **`schema` / `jsonc` 这类「通用词」不改名**：它们在 L0 里是**协议常量/解析器的唯一实现**，改名会牵动
  TS 侧逐字镜像（`src/nn/obs-encoder.ts`、`dashboard/src/core/jsonc.ts`）与 fixture 文本，收益低于代价。

**本刀踩到並记档的四个坑**（同样是「测试绿也会静默瞎掉」那一类）：

1. **⚠ 文本通行证对「新名里含旧名」的四个模块重复加前缀**：`platform_utils` / `pid_probe` / `log_bundle` /
   `schema` 的**新名里含着旧名**，AST 通行证先写成 `from common.platform_utils import …`，文本通行证接着又加一次
   前缀 ⇒ `common.common.platform_utils`，文件路径形态则是 `common/common.log_bundle.py`（21 个文件 38 处，
   含 `nn-training/README.md` 的模块树与 4 个测试里**真的读这个路径**的断言）。修正无歧义（`common/common.` → `common/`），
   但必须有**零残留断言**才算完（两次补丁：`common.common.` / `common/common/`，再补 `common/common.<mod>.py`）。
2. **⚠ 改名会把两类守卫的「名字口径」搅混** —— 这是本刀最隐蔽的一类。同一个名字在两种口径下写法不同：
   *「模块路径」口径（import 闭集/账本）* 写 `common.distribution`；
   *「绑定名」口径（`hasattr(mod, name)` / `ast.Name` 读 / `_module_globals`）* 写 `common`（因为
   `import common.distribution` 绑定的名字就是 `common`）。
   两者混着写出来的断言不是报错，而是**永真或永假**（`hasattr(m, "common.distribution")` 恒 False）。
   本刀改了 6 处：`test_batch_plan_split` / `test_batch_runner_split` 的 `_top_imports`（改为**模块路径**口径）、
   `test_loop_guards_split` 的 `_module_globals` + `PATCH_ANCHORS`（模块路径）、
   `test_loop_export_split` 的 `GONE_FROM_STEPS`、`test_loop_transport_split` 的 `expect`（两者为**绑定名**口径）。
3. **文本通行证会把「局部变量同名的属性访问」当模块引用**：`rl_config_schema.py` 里 `schema = load_schema()`
   是**局部 dict**，`test_volume_e2e.py` 里 `self._srv.schema` 是**对象属性** —— 都被改成了 `common.schema.*`。
   该类**mypy 抓得住**（`name-defined` / `attr-defined`），所以本刀的查错顺序是 mypy → 测试。
4. **写死的**基名**被当成模块路径改**：`tests/common/test_dist_common_poll.py` 的 `definers_of()` 返回的是
   `path.name`（**基名**），断言里写的 `"dist_common.py"` 被改成 `"common/distribution.py"` ⇒ 断言**永假**
   （只有真跑才炸）。凡「拿文件名当期望值」的断言，改名时必须看它的生产者是拼接路径还是取 `name`。

**环境备注（与本刀无关，已复现于 HEAD）**：venv 里的 ruff 是 0.16.6（`pyproject` 只要求 `>=0.6.0`，venv 比仓库
上次格式化时新），`ruff format .` 会重排 **296 个无关文件**（已回退、**不得再跑**）；`ruff check .` 在 HEAD 上
就已经报 `N999 tools/tpu-probe.py`（文件名带连字符，与内容无关）。本刀只跑 `ruff check --select I --fix`
（50 个文件的 import 顺序，由改名强制），跑完 `ruff check .` 只剩那一条 N999。

**违反后果**：① 在根下留旧名 ⇒ `test_l0_top_level_single_files_are_gone_from_the_root` 红（**已反探针验证**：
把 `common/log_bundle.py` 放回根下，该守卫当场红）；② 把某个模块搬回 `remote/` ⇒
`test_the_moved_modules_are_gone_from_remote` 红（同样反探针验证）；③ 忘改 `pyproject.toml` 的 `packages.find` /
`py-modules` ⇒ 装机环境（`pip install -e .`）下 `import common.*` 直接 ImportError，而测试靠 conftest 的
`sys.path` **看不出来**。

—— 全文与剩余两刀（`biz/` · `trainer/`）的判据 → `plan/nn-training-module-reorg.plan.md`；
刀 2 的普查/改写/修复/审计工具 → `nn-training/tmp/cut2_*.py`（一次性工具，全部带零残留断言）。

---

## §2026-09-30-goalnn-nn-training-worker-package（2026-09-30，刀 3：节点侧执行体出包为**顶层 `worker/`**，坐在 `remote/` **下面**）

**决策**：`remote/iter_rollout.py` + `remote/serve_pool.py` → 顶层 **`worker/`** 包，位于 **L2**
（`remote/` 是 L3、`hub/`/`trainer/` 是 L4）。**就这两个**：其余候选（`job_fs` / `job_lifecycle` /
`job_round` / `download` / `wire` / `http`）按 Q1 判为云机专属，留 `remote/`。

```
L0 common/          ┐
L1 biz/ · algo 栈   ├─ 向下依赖（严格：每条边 importer 层号 > imported）
L2 worker/          │   ★ 本刀：serve_pool · iter_rollout
L3 remote/          │   remote/worker.py → worker.iter_rollout（云 worker 用它）
L4 hub/ · trainer/  ┘   remote/offline_eval.py → worker.serve_pool；rl/dispatch + rl/queue_local 也用它
```

**为什么坐在 `remote/` 下面（而不是并列）**：两模块实测**零 `remote.*` 依赖**（只靠 `common/`），
却被三层引用 —— 云机侧（`remote/offline_eval.py`、`remote/worker.py`）与 trainer 侧
（`rl/dispatch.py`、`rl/queue_local.py`，本机槽位的长驻池）。放 `remote/` 之上/并列会长出
**4 条向上边**；坐下则四条全是向下边、**零豁免**。

**为什么出包而不是留在 `remote/`**：出包后「这段代码在哪台机器上跑、能依赖谁」才读得出来；
而且它们是 `remote/` 里唯一不依赖 `remote/` 的模块，留在包里是分层叙事里的一个假成员。

**被否决**：

- **把 `worker/` 与 `remote/` 并列（同为 L3）**：实测会得到 4 条向上边（见上）。
- **允许 `worker → remote`**：方向反了 —— worker 是**下层**，合法方向是 `remote|hub|trainer → worker`。
- **把 `worker → rl.reports` 当成违规而把 `reports.py` 划进 `trainer/`**：`rl/reports.py`
  **不在** `RL_ORCHESTRATION` 里 ⇒ 按机械定义它归 `biz/`（L1），于是 `worker(L2) → biz(L1)`
  是**合法向下边**。反过来把它当 `trainer/`（L4）会立刻把这条边变成向上边。
- **给 `worker/` 另排一套层号**：层号是**拓扑秩**，必须跨包全局一致；另排一套等于把
  「谁在谁上面」变成三份各自会漂的真相。做法是 `LEDGER_PACKAGES` + `package_roots()` 各加一项，
  层号沿用（`worker.serve_pool`=0 · `worker.iter_rollout`=1）。

**本刀踩到並记档的四个坑**：

1. ⚠ **快照的语义会被搬包整批失效**：`_rl_reaching_remote()` 判的是「可达 `remote`」，
   `serve_pool` 一走就一次失效 **14 个** `RL_ORCHESTRATION` 成员（它们只是把
   `import remote.serve_pool` 写成 `import worker.serve_pool`，成员一个没变）。
   正解是**扩判据**（「远端」= `remote` **或** `worker`），不是改清单 —— 改清单就等于把一次
   机械搬家记成一次架构变更。
2. ⚠ **`from <pkg> import a, b` 一条语句里一半留下、一半搬走**：
   `tests/remote/test_offline_eval_cloud.py` 的 `from remote import offline_eval, serve_pool` 必须**拆成两条**。
   通配正则抓不到这个形状，靠 mypy（`Module "remote" has no attribute "serve_pool"`）抓到。
3. ⚠ **合成用例的账本要跟着扩**：`test_remote_dag.py::_synth` 是把**每个** ledger 包目录都指向 tmp。
   刀 1 漏了 `HUB_DIR` 会「被真包的边淹没」（静默），刀 3 漏了 `WORKER_DIR` 则当场 `KeyError`
   —— 同一根因，两种症状。两刀都把教训写进了 `_synth` 的 docstring。
4. **排序型断言会因目录名变序**：`test_common_layer.py` 的 `_defs_of("bun_version")` 比的是
   **路径排序**：`remote/…` < `rl/…`（`e`<`l`），而 `worker/…` > `rl/…` ⇒ 期望列表要**重排**
   （成员没变）。

**违反后果**：① `worker/` 里 import `remote` / `hub` ⇒ 新守卫
`test_worker_never_reaches_the_transport_the_hub_or_the_orchestration` 红（已反探针验证：塞一行
`from remote import hub_client` 当场红）；② 把模块搬回 `remote/` ⇒
`test_the_moved_modules_are_gone_from_remote` 红（已反探针）；③ 给 `worker/` 另排一套层号 ⇒
`test_layer_numbers_are_dense_and_meaningful` 报空洞；④ 忘改 `pyproject.toml` 的 `packages.find`
⇒ 装机环境（`pip install -e .`）下 `from worker import serve_pool` 直接 ImportError，
而测试靠 conftest 的 `sys.path` **看不出来**。

—— 全文与剩余两刀 → `plan/nn-training-module-reorg.plan.md`；审计器（已扩成**三源**扫描：
`remote` + `hub` + `worker`）→ `nn-training/tmp/reorg_audit.py`，向上边 **0**；
改写器 → `nn-training/tmp/cut3_worker_rewrite.py`（有序规则 + 每条实中数 + 零残留断言）。

## §2026-09-30-goalnn-nn-training-biz-package（2026-09-30，刀 4：`rl/` 拆出**顶层 `biz/`** —— 纯逻辑与编排分家）

**决策**：`rl/` 里**不达传输/执行面**的 **64 个模块** → 顶层 **`biz/`** 包（L1，与
`models/` `ppo/` `data/` `train/` `scripts/` 并列）。判据**不是手感**，是刀 3 之前就存在的
那张机械快照：

```
biz/<mod>.py  ⇔  <mod> ∈ rl/*.py − RL_ORCHESTRATION
RL_ORCHESTRATION = 「rl 中直接或经 rl 内部传递可达 remote|worker 的模块」
```

分家后 `rl/` **只剩编排**（37 个模块，每个都可达 `remote|worker`），「谁是纯逻辑」在名字上
直接看得见 —— 不再需要逐个读 import。

**为什么必须拆**（而不是「rl 一个包也挺好」）：同一个包名住着两种东西时**依赖方向读不出来**。
`rl/queue.py`（编排，import `remote`）与 `rl/course.py`（纯逻辑，只碰 stdlib）看上去是同一层；
刀 3 出包 `worker/` 时已经暴露过一次（`rl.reports` 是**向下**边、`rl.queue_local` 是**向上**边，
同名前缀分不出来）。拆完：`biz` 零上层依赖（可被云机 worker / hub / 编排三层安全引用），
`rl` 每个模块都在快照里。

**为什么 `biz` 是 L1 而不是并入 `trainer/`（用户 Q2 的改判，§8-Q5 已记）**：
`remote/bc_job.py` 与 `remote/train_core.py`（**云 worker 的训练/评估体**）import
`ppo.*` / `data.weights_io` / `train.bc`；`rl/model_build.py`（纯逻辑）import `models.student` +
`ppo.*`。把算法栈并入 `trainer/`（L4）会立刻长出 **9 条 `remote → trainer` 向上边**，另有硬编码
跨项目路径（`nn-training/data/state-init-bank`、`nn-training/scripts/…`）⇒ 保持顶层并列。

**被否决**：

- **挑一份「看着像纯逻辑」的名单手工搬**：判据会是第三种真相（与 `RL_ORCHESTRATION` 快照各漂各的）。
  实测：`rl/` 的 101 个模块里 64 个是纯逻辑，其中 `loop_guards_trip/leg/gate/sweep` 这四个
  「护栏混入」很容易被误判成编排（它们调 `self.` 共享 sink，但**自己不经传输层**）。
- **把 `test_pure_rl_never_imports_orchestration` 删掉**（`rl/` 里没有纯逻辑半了 ⇒ 判据变永真）：
  改为 `test_biz_never_imports_the_orchestration_rl` —— **判据一字不改，扫描面跟着家走**。
- **让 `biz` 进 `remote_dag` 的层号账本**：账本是 `remote/hub/worker` 三包的**内部拓扑秩**，
  `biz` 是 L1 单层叶子（零仓内依赖的两个包之一，另一个是 `common`）⇒ 进账本只会引入空段。
- **把 `allowed_rl` 直接改名不改语义**：原表里列的**从来就是纯逻辑**（`rl.archive` / `rl.eval_rows`），
  所以把它改成 `allowed_biz` 并**拆成两条断言**：编排 `rl` **零豁免**（硬红），纯逻辑 `biz` 需**点名登记**。

**本刀踩到并记档的五个坑**（详见 `docs/nn/engineering.md` §52）：

1. ⚠ **扫描面缩水是哑的**：8 个守卫用 `(ROOT/"rl").glob("*.py")` 扫「谁调用 / 谁定义 X」
   （入边闭集那批）—— 搬走 64 个文件后扫描面**静默变窄**，判据变**永真**。正解：
   `tests/helpers/source_scan` 新增**两棵树口径**（`LOGIC_PACKAGES` / `logic_py_files` /
   `logic_module` / `logic_dotted`），8 处扫描面 + 4 处目录元组 + 7 处 banned 名单统一补齐。
   同一次也撞见硬编码路径的**响**症状（`RL / fname` ⇒ `FileNotFoundError`）—— **哑症状才是贵的**。
2. ⚠ **一族文件可以跨两棵树**：`rl/loop_guards.py`（组合根）+ 4 个 `biz/loop_guards_*.py`（混入簇）
   是**一族**，刀 4 后分住两棵树。正解是**两棵树解析器**（`_home` / `_dotted`），判据与期望值一个字不改。
3. ⚠ **模板串文本通行证看不见**：`f"rl.{fname[:-3]}"`（`__import__` / `__module__` 断言）全名是
   **运行时拼的**（7 处人工）；其中一处兼任 `importlib.import_module`，改成 `logic_dotted` 才真正位置无关。
4. ⚠ **逗号分片路径**：`path.join(NN_TRAINING, 'rl', 'eval_replays_once.py')` 是**两个独立字面量** ⇒
   整条字面量规则看不见，实测漏掉 1 处**真 spawn**（dashboard「导出 replay」）：五条门禁全绿、
   **点按钮才炸**。补 dashboard 守卫 `tests/python-spawn-paths.test.ts`（已反探针）。
5. ⚠ **改写范围漏了仓库根 `tests/`**：`tests/export-rl-rollout-metrics.test.ts` 按路径读
   `nn-training/rl/reward_library.py`（跨语言 SSOT）⇒ 根 `bun run check` 1 红。教训：跨项目读者的
   范围表要**按仓库根枚举**（`dashboard/src` · `dashboard/tests` · **`tests/`** · `tools` · `src`）。

**钉住它的新契约**：`test_rl_holds_only_orchestration_modules`（`rl/` 的模块集合**恰好**等于快照 ——
往 `rl/` 丢一个不达远端的纯逻辑模块，快照对账的两个方向都看不见它）·
`test_the_pure_logic_tree_is_gone_from_rl`（两棵树零同名 + `biz/` 不是空壳）·
`L1_PACKAGES` 加 `biz`（L1 零上层引用）· `pyproject.packages.find` 加 `biz*`。

**违反后果**：① 纯逻辑搬回 `rl/`（或同名留两份）⇒ 两条机械守卫红（已反探针）；
② `biz` 里 import 编排 `rl` ⇒ 两条切线守卫红（已反探针）；③ `remote`/`hub`/`worker` 未登记地
import `biz` ⇒ `assert_remote_module` 红（已反探针）；④ dashboard spawn 一个已搬家的 `.py` 路径 ⇒
`python-spawn-paths.test.ts` 红（已反探针）；⑤ 忘改 `packages.find` ⇒ 装机环境 ImportError
（测试靠 conftest 的 `sys.path` 看不出来）。

—— 全文与最后一刀（`trainer/`）→ `plan/nn-training-module-reorg.plan.md`；审计器（三源 + biz 桶）→
`nn-training/tmp/reorg_audit.py`，向上边 **0**；改写器 → `nn-training/tmp/cut4_biz_rewrite.py`
（AST + 文本两条通行证 + 零残留断言）。

## §2026-09-30-goalnn-nn-training-trainer-package（2026-09-30，刀 5 收官：编排整包改名 `rl/` → **顶层 `trainer/`**，`rl/` 从此不存在）

**决策**：`rl/` 剩下的 **37 个编排模块**（每个都直接或经包内传递可达 `remote|worker`）整包改名
**`trainer/`**（L4，与 `hub/` 同层、互不 import）；**成员一个没变**，只是整包换门牌，`rl/` 这个包
从此**不存在**。声明式快照随之改名 `RL_ORCHESTRATION` → **`TRAINER_ORCHESTRATION`**。五刀至此收官：
`common`(L0) · `biz`/`models`/`ppo`/`data`/`train`/`scripts`(L1) · `worker`(L2) · `remote`(L3) ·
`trainer`/`hub` + 根入口(L4)。

**为什么改名**：「rl」这个名字在本仓有**三套口径** —— 模块路径（`rl.queue`）· 配置节名与启动参数
前缀（`cfg["rl"]` / `rl.stream` / `--rl.<k>` / `rl-config.json`）· 任务种类（`kind ∈ {rl, bc}`）。同一个词
同时指包与配置节，读代码要靠上下文猜。改名后「包名 = 职责」：`trainer/` = 训练编排；配置节名与
它无关，因此 `cfg["rl"]` 与 `rl-config.json` **一个字没动**。

**★ 本刀的题眼 —— 快照的固定点前缀与切片必须同改**：`_trainer_reaching_remote()` 判「可达
`remote|worker`」靠的是**包内传递** —— 内部边前缀 `d.startswith("rl.")` → `"trainer."`，切片
`d[3:]` → `d[8:]`。只改一处**不报错**，而是静默塌成「只算直接可达」⇒ 一批只经内部链达远端的
成员（`batch_*` / `eval_*` / `stream` / `queue`…）假性 `shrank`（像一次架构变更，其实只是改错名字）；
切片留在 `[3:]` 则算出垃圾名 ⇒ `grew`。双向对账把两个方向都抓住（反探针 ④/⑤）。前四刀是
「从包里切一族」（包名不动），本刀是「整包改名」（判据自己的内部口径也变）。

**本刀踩到并记档的八个坑**（全部是「改名型哑故障」，症状都不是报错；详见 `engineering.md` §53）：

1. ⚠ 固定点前缀 / 切片（见上）。
2. ⚠ 模板串 `f"rl.{fname[:-3]}"` 文本通行证看不见 ⇒ `tests/trainer/test_loop_remote_split.py` 全量门禁才红
   （`ModuleNotFoundError: No module named 'rl'`）；正解走 `source_scan.logic_dotted`（第九次同型）。
3. ⚠ 两处**永真断言**：`remote_dag.assert_remote_module` 的 `== "rl"` 比较（扫描面已换 `trainer`）
   与 `test_config_file_split` 的 `assert "rl" not in tops` ⇒ 守卫静默失效，都改成 `trainer`。
4. ⚠ `packages.find` 漏 `trainer*` ⇒ 装机环境 ImportError 而测试全绿（刀 1–4 各一次，第五次）。
5. ⚠ 裸 `rl.stream` 探针（`test_no_torch_on_import`；唯一「模块名 == 配置键」的碰撞名，人工处理）。
6. import 顺序整体漂移（`rl.X` → `trainer.X` 改变 isort 排序位）⇒ `ruff --select I --fix` 修 12 个文件。
7. 长串 / 逗号分片路径 `path.join(REPO_ROOT, 'nn-training/rl')`（dashboard 守卫）看不到 ⇒ 真跑才现形。
8. **旧包的存在性本身要判**：留一个 `rl/__init__.py` 就能让 `import rl.x` 继续解析，改名退化成
   「两个名字并存」（扫描面缩水是哑的）⇒ 新守卫 `test_the_old_rl_package_is_gone_after_the_rename`。

**被否决**：① 保留 `rl/` 当兼容别名（`import rl.x` 仍可用）——否：本仓第十刀起「名字是契约、位置不是」
的要义就是**删掉旧家**，别名会让漏改 forever 静默；② 只改包名不改快照名（继续叫 `RL_ORCHESTRATION`）
——否：名字里的 `RL` 那时指向一个不存在的包，下一个人会去 `rl/` 找它；③ 顺手再重命名/细分那 37 个模块
——否：本刀只做搬家 + 改名，语义改动单开。

**违反后果（反探针 7/7 全红，复位即绿）**：① 旧 `rl/` 复活 ⇒ 机械守卫红；② `biz/` 越位 import
`trainer` ⇒ **3 条**红（上层面包 / 编排闭集 / 真切线）；③ `worker/` 越位 ⇒ **2 条**红（含「环回来了」）；
④ 固定点前缀回退 ⇒ 快照对账红；⑤ 切片 `[3:]` ⇒ 红；⑥ 快照多一个假成员 ⇒ `shrank` 红；
⑦ 摘掉一个真成员 ⇒ 集合相等红。（探针 → `nn-training/tmp/cut5_probe.sh`）

**纯搬对账**（`nn-training/tmp/cut5_verify_move.py`，五刀改名全逆 + 括号内 import 折回一行再比非空行
多重集）：37 个模块 **0 个不等** —— 本刀没增删改任何一行代码，只有 `loop_eval.py` 一处 import 因
名字变长被 ruff 折成括号多行（纯格式）。

**门禁**：nn **3372 → 3373 passed / 3 skipped**（+1 = 新守卫）；mypy **547** 文件绿；ruff 只剩**预存**的
`N999 tools/tpu-probe.py`（文件名带连字符，HEAD 上就有）；根 `bun run check` **2277 pass / 0 fail**；
dashboard typecheck + **1229 pass / 0 fail**；`bun run build` 过 + 三份 bundle 过。`DECISIONS.md` / `plan/` /
`docs/**` 的**历史正文**一律不改写。

**追加（2026-09-30，同日第二轮）—— 散文里的旧路径必须跟着搬家**：首轮把 `trainer/**` 与 `tests/**`
里**散文**中的旧 `rl` 路径留了下来（照刀 2–4 的先例「只改代码不改注释」）。复审改判：这是**最坏的一种
残渣** —— 注释是下一个人唯一的导航，它指向一个**不存在的目录**时把人带沟里，而门禁全绿 ⇒ 没有任何信号。
判据是**名实**，不是「见 `rl` 就改」（`rl` 在本仓另有配置节名 / 启动参数前缀 / 任务种类三种合法含义）：
`rl/` 指**今天还叫这个名字的东西**（当年的包，与「出包 / 曾经 / 刀 X 前 / 今 `trainer/` / 前 `rl/`」同句）
⇒ 不改；其余一律改到今天的家名 `trainer/`（`biz/` 那半刀 4 已改）。首轮 21 个文件（`rl/<module>` 与
`rl.<module>` 形状）· 次轮 18 个文件 / 30 处（裸 `rl/` 目录名），逐条断言命中数、锚点写错不写盘
（`tmp/prose_rl_rewrite.py` / `tmp/prose_rl_dir_rewrite.py`；规则表 → `docs/nn/engineering.md` §53）。
**第三轮（同日）**：同一判据往前扫，清掉刀 1/3 的同类残渣 —— 散文里指向**已搬走的 `remote/*`** 的路径
（`remote/protocol.py` → `common/`、`remote/smoke_loopback.py` → `hub/`、`remote/serve_pool.py` → `worker/`
等 9 个模块），12 个文件 / 19 处；`remote/` 作**旧家**引用（「原 …」/「刀 1 后从 … 搬进」）与夹具里的
**合成 zip 成员**（`z.writestr("remote/_marker.py", …)`）照旧保留。
**第四轮（同日）—— 扫描面放大到全树**：前三轮只扫 `trainer/**` 与 `tests/**`，漏了 `biz/`、`common/`、
`hub/`、`worker/`、`remote/`、根脚本、`curricula/*.jsonc`、`levels/*.jsonc` 里的百来处 `rl/<module>`。
判据不变（**名实**），但扫描面按**仓库根**枚举 ⇒ 同一处残渣不再因「住哪个目录」而漏。命中分三类：
① 指向**今天仍实存的 `rl.stream` 配置节**（`rl.stream` / `["rl"]` / `rl-config.json` 等 ~26 处，**不改**）；
② 指向**已搬走的模块名** ⇒ 按名改到 `trainer/` 或 `biz/`；③ 散文里的**目录名**（`全 rl/`、`rl/ 编排 + biz/ 纯逻辑`）
⇒ `trainer/`。**坑**：改写器第一版把 `["rl", "stream"]` 这类「多义 token 表」整个丢进 keep 名单，
而同一批里 `config.py` 的每一条 `rl.*` 键都带 `stream` 兄弟键 ⇒ 静默漏改 26 处；`test_rl_config_clean`
（钉 config 里不许残留旧键名）当场红 ⇒ 兜住。**这是本轮唯一的真信号**，其余全绿。
改写器 `tmp/prose_rl_wide.py` + `tmp/prose_rl_hand.py`（后者 7 条 hunk / 7 文件，收机器判不了的四类：
`parents[N]` 自述深度、「不 import 任何 `rl/*`」这类跨层声明、`rn-training/…` 拼写事故、碰撞名 `stream`
的模块口径），逐条断言命中数。
**刻意保留**：`test_layering` 钉「`rl/` 不存在」那条守卫的字面量、`test_hub_client_code_zip` 的合成 zip
成员表（不是真路径）、以及所有把 `rl/` 当**旧名**引用的历史句。代码里的旧**标识符**（`test_loop_guards_split`
的 `RL = NN_ROOT / "trainer"` 已是死常量、`test_batch_eval` 的局部名 `rl_dir`）本轮不动：不改行为。

—— 五刀总账与映射表 → `plan/nn-training-module-reorg.plan.md` §5；全文（字段名对照 / 反探针表）→
`docs/nn/engineering.md` §53；改写器 → `nn-training/tmp/cut5_trainer_rewrite.py`（AST + 文本两条通行证 + 零残留断言）。

## §2026-09-30-goalnn-dashboard-sentinel-paths-must-exist（2026-09-30，`specs.ts` 哨兵路径必须实存 —— 孤儿哨兵修复 + 机械守卫）

**背景**：`dashboard/src/stack/specs.ts` 的 `localWorker` / `trainingLoop` 各有 `sentinel`（`core/reload.ts`
的 `snap()` 记 mtime，变了就重启进程；`core/sentinels.ts` 比对）。它是 `codehash-files.txt` 之外的**手工补面**，
注释写明「漏报 = worker 用旧协议跑新 job」。

**问题**：刀 2（S3，5624856，2026-09-23）把 `remote/protocol.py` 下沉进 `common/`，两条哨兵仍指旧路径。
`snap()` 对**不存在的文件**返回 `null` ⇒ 哨兵**永不触发**、一行日志都没有 —— 恰好就是它要防的那件事。
更糟：`dashboard/tests/local-worker.test.ts` 用 `endsWith('remote/protocol.py')` 把旧路径**断言住了**
（把 bug 钉成契约），于是 1229 个 dashboard 测试全绿，没有任何信号。

**决策**：① 哨兵路径改指真实家（`common/protocol.py`）；② 加**机械守卫**
`dashboard/tests/stack-sentinel-paths.test.ts` —— 扫 `specs.ts` 源码里所有 `'nn-training/…'` / `'tools/…'`
**文件**字面量，逐条断言 ①扫描面 ≥5 条（防「扫描面缩水 = 哑绿」）②`existsSync` ③是文件不是目录（目录 mtime 只在
增删条目时变，当哨兵同样哑）。**否决的备选**（只改路径、不加守卫）：搬家是本仓常态，而这种失效**完全哑**
（无报错、无日志，面板照常显示），下一次搬家必重犯。先证明守卫在**旧**代码上红，再改代码。

**理由**：可机械判定的不变量就该机械判定；「按路径监视」的东西被搬家静默孤儿化是这一类残渣的**共同根因**
（同源先例：`hub` 侧 S4 用 `hubImplementationFiles()` 目录枚举修过同一个坑）。

**影响面**：`specs.ts`（2 处哨兵路径）、`local-worker.test.ts`（断言改到新路径）、`WirePanel.tsx` /
`server/api/tunnel-ab.ts` 文案，新增 1 个测试文件。**不动**：`snap()` 语义与重启策略。

—— 全文（守卫口径 / 事故链）→ `docs/nn/engineering.md` §53 末节。

## §2026-09-30-goalnn-worker-local-training-stack（2026-09-30，刀 6：`worker/` = 本地 torch 训练全栈；`biz/` 只留游戏业务）

**背景**：用户 2026-09-30 三句话逐步收紧口径 —— ①「纯训练的内容都放在 `worker/` 下，比如 `rl/bc/ppo` 这些与业务
（游戏）逻辑无关的训练代码」②「`worker/` 应该包含所有支持**本地 torch 训练**的所有代码，**云机 worker = local
worker + `remote/`**」③「`biz/` 只放和业务（游戏）逻辑相关的内容」。这推翻了 `plan/nn-training-module-reorg.plan.md`
§8-Q5（算法栈保持顶层并列）：Q5 当年否决并入的是 **`trainer/`（L4）**（会产生 9 条 `remote → trainer` 向上边），
而 `worker/` 坐在 `remote/` **下面**（L2）⇒ `remote → worker` 是**向下**边，「云机 worker = 本地 worker + remote」
正是这条偏序的自然读法。

**搬移集**（机械）：A 算法栈五包 `models/` `ppo/` `data/` `train/` `scripts/` → `worker/<同名>/`（39 文件）·
B 训练侧单体 `biz/<m>.py` → `worker/<m>.py`（**52**）· C `biz/log.py` → **`common/log.py`**（统一日志是通用基础设施：
stdlib-only，`biz/worker/remote/hub` 共用）。`biz/` 剩 **12** 个游戏业务模块（11 个领域模块 + 前置修边新建的
`corpus_fp`）。三处边界用户逐条拍板：课程结束门一族（`gate_check` / `gate_inputs` / `gate_judges` / `loop_guards_gate`）
⇒ `worker/`（它们读的是 eval 行/指标与阈值）· `state_init`（人类中段快照 → rollout 起始分布）⇒ `worker/`（服务
rollout 采样）· `log` ⇒ `common/`（既不是游戏也不是训练）。

**★ 刀口 = 留在 `biz/` 的模块不得 import 搬走的**（实测 4 条向上边，`tmp/biz_split_recon.py`）：
`hot_reload → config.corpus_identity_fp`（指纹下沉成 **`biz/corpus_fp.py`** —— 语料身份由游戏域定义）·
`ladder_factory → config.load_course`（改指真家 `biz.course_resolve`）· `reward_library → log`（`common.log` ⇒ 向下）·
`state_init → config`（自身进 `worker/` ⇒ 同包消解）。

**账本后果**（`tests/helpers/remote_dag.py`）：`worker/` **整包入账** —— 150 个模块一账三包。不入账，`remote/hub`
与算法栈之间的边会静默消失（刀 3 的同一条理由）。层号仍是**算出来的**拓扑秩，于是 12 条既有条目被抬
（`remote.plan_run` 3→6 · `remote.plan_handoff` 2→5 · `remote.run_loop` 6→7 · `remote.offline_boot` 7→8 ·
`remote.bc_job` 3→4 · `remote.offline_eval` 1→2 … —— 它们的依赖从「账本外的 `biz.*`」变成「账本内的 `worker.*`」），
6 条被压（`hub.task_pack` 1→0 · `remote.hub_http` 1→0 · `remote.hub_client` 2→1 · `remote.push_client` 2→1 ·
`remote.push_dispatch` 3→2 · `hub.result` 4→3）—— `common.*` 的边自刀 2 起就不在账本里，秩按「账本内最大依赖 +1」
重算。逼出这次重排的是守卫 `test_every_layer_number_equals_its_topological_rank`（它把「秩 = 拓扑秩」写成契约）。

**守卫修复（本刀最值钱的部分）——「哑守卫」三型**：
① **集合成员型**：`assert "worker.X" not in dag.LAYERS` —— 它想说的是「X 不达传输面」，但「不在账本里」只是
代理判断：刀 4 写 `"biz.X" not in dag.LAYERS` 时是**哑真**（`biz/` 从不进账本 ⇒ 恒过，判据是瞎的），
刀 6 写 `"worker.X" not in dag.LAYERS` 时是**恒假**（`worker/` 整族入账 ⇒ 必红 = 假警报）。改判成直接表达那句话的
`dag.reaches_transport("worker.X")`（账本内能否传递可达 `remote.*`）；五个纯逻辑守卫（`eval_rows` / `eval_track` /
`eval_yield` / `gate_inputs` / `gate_judges`）全部改判。
② **前缀型**：`startswith("rl.")`（`test_eval_yield_split` 的「不反向 import 运行器」· `test_gate_judges_split` 的
「不碰编排」）⇒ 改成 `worker.eval_local` / `trainer.`（前者是**永远为空**的哑判据，后者才是它想说的那句话）。
③ **扫描面缩水型**：`test_action_head_hygiene` 手写包名清单里的 `ppo` 搬家后成空目录（`rglob` 静默返回空）
⇒ 按**目录枚举**（生产树全部包 + 根脚本）并加「扫描面 > 100 文件」自证；`test_hub_queue_split._logic_layer_import`
的包集加 `worker`；子进程探针里的 `biz.gate_*` 字面量与打印标签（`test_gate_check` / `test_gate_judges_split` /
`test_no_torch_on_import`）逐条对账 —— **标签与 import 必须同名**，不同名时断言恒真（这也是哑守卫的一种）。

**路径修复**（三轮扫描，判据 = existence；按仓库根枚举）：`dashboard/src/launch/cli.ts` 的 `LEGACY_ALIAS` 八条
（`'train_bc.py' → 'worker/train/bc.py'` …；它是 `--script` 旧扁平名映射，写错就是启动器报 `script not found`）·
`dashboard/src/stack/specs.ts` 两条**哨兵**（`biz/bc_config.py` / `biz/bc_dispatch.py` → `worker/`，被上一刀新加的
`dashboard/tests/stack-sentinel-paths.test.ts` 当场抓出）· `dashboard/src/server/api/route.ts` 的 replay 导出
spawn 路径 · `dashboard/src/evalboard/kick-once.py` 的 `from biz.log` → `common.log` ·
`curricula/x20-state-init.jsonc` 的银行路径 · `tests/golden/reward_golden.json` 的 `generated_by` ·
`dashboard/tests/{kickstart-receipt,training-train}.test.ts` · 根 `tests/nn/coord-golden.test.ts` ·
`nn-training/README.md` 模块地图 · `tools/tpu-probe.py`（内嵌 notebook 经 `tools/sync_tpu_probe_nb.py` 重生成）。

**读数**：nn 门禁 **3373 passed / 3 skipped**（ruff `All checks passed`，**117 处** isort 漂移 `--fix`；mypy **548**
文件干净）· 根 `bun run check` **2277 pass / 0 fail** · dashboard typecheck + **1232 pass / 0 fail** ·
`bun run build` 过 · 反探针 `tmp/cut6_probe.py` **5/5**（`biz → worker` 向上边 · `worker.eval_rows` 直达 `remote` ·
模块搬回 `trainer/` · 账本秩贴高 · dashboard 哨兵指向不存在的文件 —— 逐条红、复位即绿）。

—— 全文（哑守卫三型的前后代码 / 路径清单 / 分家计数）→ `docs/nn/engineering.md` §54；搬移集与三处边界 →
`plan/nn-training-module-reorg.plan.md` §5.5。

## §2026-09-30-goalnn-entry-scripts-into-packages（2026-09-30，刀 7：14 个入口脚本归位 —— `nn-training/` 顶层只剩 `conftest.py`）

**背景**：用户「nn-training 目录下还有几个 py 文件，把它们移动到合适的目录下」。按功能归位：六个训练/评估入口 →
`trainer/`；两个 worker 入口 → `remote/`；`rl_config_schema` → `worker/`；五个环境/运维脚本 → `tools/`；`conftest.py`
留根（pytest rootdir 守卫 + `tests/test_conftest_check_guard.py` 直接 import 它）。

**三条前提随搬家重算**（本刀真正的技术含量 —— 前两条**只在真机启动时现形**，单测天然看不见）：
① **脚本模式下 `sys.path[0]` = 脚本目录**（不再 nn-training）⇒ 六个入口各自前置「摘掉脚本目录项 + 放回 nn-training
根」：`trainer/queue.py` 会遮蔽 stdlib `queue`，`concurrent.futures` 一导入就循环炸（惯用法抄 `trainer/eval_a_once.py`）；
② **`Path(__file__)` 上溯层数 +1**：`HERE` / `NN_ROOT` / `ROOT` 的**语义**不变（nn-training 根 / 仓根），只改推导；
③ **启动器**：`--script` 一直是「`nn-training/` 下的相对路径」（`resolveTrainScript` 只拒绝对路径/盘符/`..`），
故写成 `trainer/run_rl.py`；`LEGACY_ALIAS` 里 `'train_rl.py'` 的值同步改。

**刻意不加别名**（被否决的备选）：给 14 个旧裸名补 `LEGACY_ALIAS` 能让旧命令行继续可用，但别名森林让「脚本搬家了」
永远没人发现（旧名与新路径两份真相）。取**响亮失败**：`--script run_rl.py` 现在报 `script not found`。

**新守卫** `tests/test_entry_scripts_in_place.py`：① `nn-training/` 顶层只有 `conftest.py`；② 9 个文件入口以
**脚本方式**真起一次（`python nn-training/<入口> --help`，退码 0 无栈）；③ 两个 `-m` 模块名
（`remote.remote_worker[_serve]` —— 控制台 `specs.ts` / `push.ts` 与 Kaggle notebook 的契约面）。它填的正是既有守卫的盲区：
单测里没人以脚本方式起过入口（`importlib` / `-c` 的 `sys.path[0]` 不是脚本目录）。

**快照与账本**：`TRAINER_ORCHESTRATION` 37 → **43**（六个入口本来就是「可达 remote|worker」的直接成员，只是此前不在
`trainer/` 视野里）；`tests/helpers/remote_dag.py` 账本 +3：`worker.rl_config_schema` **L0** · `remote.remote_worker`
**L6** · `remote.remote_worker_serve` **L7**（层号 = 1 + max(依赖)，仍是算出来的，不是贴上去的）。

**改写器踩的两个坑**：① 路径正则**尾部**缺否定前瞻 ⇒ `task.pytest_dispatch()` 里的 `task.py` 被当成路径
（改出 `tools/task.pytest_dispatch()`，测试当场炸）；② 裸字符串规则误伤**锁种类**字面量（`"run_bc"` →
`"trainer.run_bc"`）—— 锁名 `.run_rl.<course>.lock` 是**跨语言契约**（`slots.ts::lockName` ↔ python `course_lock_path`
必须逐字节一致），由 `tests/trainer/test_serve_bc.py::…own_traj` 当场抓出。两条都写进 `docs/nn/engineering.md` §55。

**同日续（`.md` 散文轮，2026-09-30）**：代码面改写器显式跳过 `*.md`，散文单独一轮清完 —— 42 个命中文件（含
`tmp/` 两份草稿副本）去掉草稿后 40 个里，**21 个文件 / 147 处路径 + 10 处同行连带**改到今天家
（`tmp/md_prose_refs.py`：逐文件断言命中数 + keep 锚点）；**19 个文件整文件留**（`DECISIONS.md` / memory 这类
追加型日志 · `tmp/` 草稿 · 四篇年代快照 · 本轮 plan 的决策表）。五类例外（映射左列 / 令牌·事故记录 /
已死启动器记录 / 日志与草稿 / 快照）→ `docs/nn/engineering.md` §55「散文里的旧路径（`.md` 轮）」。
门禁：根 `bun run check` **2277 / 0** · dashboard **1232 / 0**。

**同日续二（`.md` 散文轮二：更早层残渣，2026-09-30）**：第一轮只清刀 7 的 14 个扁平入口；刀 1–6 之前的旧形
（`rl/…` · `remote/…` · `models|ppo|data|train|scripts/…` · 根 L0 `dist_*`）同日续清 —— **20 个文件 / 418 处**
（改写器 `tmp/older_refs.py`：逐行 keep 锚 + 三道守卫 —— 旧形已在新家前缀内 / 解析无解 / keep；落盘后
`tmp/older_rescan.py` 复查 **WOULD-CHANGE 0**）。keep 55 处 = 映射左列 /「原 X」自述 · 事故·字面量
（`SRC = rl/batch_eval.py` · `endsWith('remote/protocol.py')` · `setattr("rl.…")` · `git show HEAD:rl/…` · 断言错误原文）·
决策记录（算法栈并入 `trainer/` 的「9 条向上边」分析 · 六包重组第一刀行 · `rl.queue/stream` 缩写列）。
已知残留：日志/草稿/年代快照/本 plan 决策表（判据同 §55 首表）· brace 列表 `rl/{…}.py` · 裸 `rl/` 统称 ·
根下未跟踪草稿 `transfer-scheduling.*`（不在扫描面）。门禁：根 `bun run check` **2277 / 0**。

—— 全文（14 个文件的新老家 / 读数 / 反探针 / 未做）→ `docs/nn/engineering.md` §55；`plan/nn-training-module-reorg.plan.md` §5.6。

## §2026-10-01-goalnn-log-keeps-era-names（2026-10-01，`DECISIONS.md` 的旧路径口径：追加型日志**不就地改名**，头部落一条译注）

刀 7 两轮 `.md` 散文清理（§2026-09-30-goalnn-entry-scripts-into-packages 同日续 / 续二）后清点本文件：仍有
**327 处**可解析旧形（`rl/…` · `remote/<搬走的>` · 算法栈五包路径 · 根 L0 名），散在 **108 个条目**里 ——
其中 **107 行自带映射 / 引文**（旧→新对 ·「原/旧/改名」· 断言字面量 · `git show HEAD:` 引用），其余多是刀口
条目里「当时那个文件 / 第 N 行」的坐标（`tmp/decisions_probe.py` → `tmp/decisions_probe.txt`）。

**决策**：全部**保持原文**，只在文件头（「正文在哪」段后）落一条**旧路径约定**译注（读者/agent 的进入点，
写一次管全部）；**新条目用今天的名字**。

**被否决的备选**：① **就地改**（200 处纯 locator）—— 条目里的 `rl/config.py:1264` 这类**行号**是当时文件的
坐标，改名后读成今天文件的行号（假精确）；且要逐行判 108 个条目、107 行映射/引文不能碰 —— 成本高、零收益。
② **只手写一份映射表**（不落进文件头）—— 读者/agent 从文件头进，映射放别处等于没放。

**判据 / 先例**：AGENTS §6.3（就近表达）· 本文件头部「永不删除、永不重排」· `docs/nn/engineering.md` §55
keep 表（追加型日志整文件不动；改字面量 = 篡改记录）。**对照指针**：`nn-training/README.md` 模块图 ·
`docs/nn/engineering.md` §51–§55。

## §2026-10-01-goalnn-dashboard-gate-net-direct（2026-10-01，dashboard 门禁：测试进程出网直连 + 停课重试可注入 —— 全目录墙钟 11.4s → 1.8s，用例与断言逐条不变）

**症状**：dashboard `bun run test` 墙钟 11.4s（119 文件 / 1232 用例），而 per-file 时长总和 67.5s。
JUnit 逐用例（`--reporter=junit`）一看：几乎每个文件只有 1–3 个用例慢（1.5–4.0s），其余全在 0.01s
量级——慢的全是「hub 不可达 / buildStateView 探测」这类**注定连不上**的出网点。

**根因（探针实测）**：dev 机有 HTTP(S)_PROXY，而 Bun 只认 NO_PROXY 里的**精确主机**，不认 `127.*`
这类通配写法：`NO_PROXY='127.*'` 下 `fetch('http://127.0.0.1:1/…')` 仍走代理（2.9s）；
把 `127.0.0.1` 写成精确条目 → 1ms ECONNREFUSED。于是「连不上」从 ~0.01s 涨到 1.5–4s，且每个 worker
进程都要重付一次（同时证明了 bun 是**逐请求**读 env，而两个拼写都在时读**小写**那个）。

**决策**：门禁脚本挂一个测试预载 `tests/helpers/no-proxy.ts`（`bun test --preload ./…`，注意 bun 要
`./` 前缀，否则 preload not found）：把 `NO_PROXY` / `no_proxy` 两个拼写都置 `*`，再调生产的
`core/net.shapeLoopbackNoProxy()` 追加精确回环做兜底。只改测试进程 env，不碰被测代码 / 断言 / 产物；
无代理环境（CI）逐字节不变。另修 `course-lifecycle` 里残留的 4.0s：停课走 `pushHubMode` 的缺省 3×2s
重试（= 2×2s 死等），给 `stopCourse` 补上 `openCourse` 早就有的 `hubMode` 注入位（缺省行为不变），
用例注入 `delayMs: 0`（3 次照跑，只去空等）。

**读数**：单品 `cloud-halt.test.ts` 12.0s → 0.06s；逐用例时长总和 61.9s → 15.3s（JUnit 口径；per-file
口径 67.5s）；最慢单用例 4.00s → 0.66s；全目录墙钟 11.4s →
1.6–2.2s（4 次）。**等价性**：改动前后 JUnit 的 1232 个 `(file|name|assertions)` 集合零差异（5726 次
`expect()` 不变），用 README 的「用例与断言逐一不变」判据履行。**反向读数**（为什么两条 flag 都不能删）：
去掉 `--parallel` ⇒ 文件同进程共享 global，58 个用例红（不是慢，是错）；`--parallel=1` 66.5s vs `=8` 12.5s。

**被否决的备选**：① 每个受影响文件 import 一个 helper——现在就要改 ~20 个文件，将来每个新文件还得记得，
而预载把「新文件自动覆盖」买到手；② `dashboard/bunfig.toml` 的 `[test] preload`（实测可行，但门禁命令
组成会分裂到脚本 + 配置两处，且 bunfig 同时作用于 install/build）；③ 改生产默认重试时长——缺省 3×2s 是
为 hub 扫盘竞态留的，不该为测试改。**守卫**：`tests/gate-composition.test.ts` 钉住 `--parallel`、预载 flag
与本进程 env 被改写——删任一条测试就红（否则只是静默慢 6×）。

**判据 / 先例**：同规则的生产先例 = §2026-09-18-goalnn-loopback-http-no-proxy（`shapeLoopbackNoProxy`）·
AGENTS §6.3（就近表达：把「为什么」写进预载文件头）· §9（门禁即 DoD；pre-commit 与
`.github/workflows/dashboard.yml` 都调 `bun run test`，所以单一挂载点在脚本）· `tests/ci-scope.test.ts`
（同一体裁的守卫：让门禁组成不能静默腐烂）。**对照指针**：`dashboard/tests/helpers/no-proxy.ts` 头注 ·
`dashboard/README.md`（命令表 + 纪律「测试出网直连」）· `dashboard/tests/gate-composition.test.ts`。

## §2026-10-01-goalnn-dashboard-tests-no-wallclock（2026-10-01，dashboard 测试不许有墙钟等待：等待由可观测事件触发，夹具主机不用 DNS）

**症状**：上一条（代理预载）修完后，逐用例时长里仍剩一批 1.5–4.0s 与大幅抖动——同一个
`buildStateView` 注入用例在 0.14–0.79s 之间来回跳。

**三处根因，共性是「等待靠墙钟猜」**：

1. `server-api-pool-swr` 的「`?fresh=1` 硬清」用例用 `await sleep(400)` 猜「这么久还没回来 ⇒ 它确实在等
   探测」。改成**等重算自己的探测注册**（`probeStub.nextProbe()`，计数口径 = 自 `hold()` 起，故同步派发的
   探测也兑现）；软作废回归会先兑现读请求 ⇒ **第一拍就红**（还多了一条更强的因果断言）。顺带把另一条
   有界再校验的 `sleep(20)` 定值步长换成按拍让路（`setTimeout 0`；退出判据仍是可观测的 `cachedAt` 推进）。
2. `training-port-reclaim` 的三处 `waitUntil(() => portListen(port), 8000, 100)`：监听就绪改为**子进程自己打印
   `READY`**（命令输出触发）——轮询的 100ms 步长是每次白等半拍，而子进程没起来时还要等满 8s 才报红。
3. `console-fixture` 的 gpu_push 节点 `https://push.fixture.invalid`：**不存在的域名靠 DNS 失败来「不通」**，
   那段墙钟依机器/解析器而变（本仓 `server-api-overview.test.ts` 自己的注释就记了「1500ms/用例，纯等 DNS」）。
   换成死回环端口 `https://127.0.0.1:1`（即时 ECONNREFUSED，同 `cloud-halt.test.ts` 的「port 1」惯例）——
   这条影响面最大：十几个探测/SSR 用例各掉 0.2–0.5s。

**读数**（JUnit 逐用例口径）：pool-swr 文件 0.58s → 0.19s（最慢条 0.47s → 0.14s）；port-reclaim 0.99s → 0.87s；
全目录逐用例时长总和 61.9s → **15.1s**；全目录墙钟 11.4s → **1.5–1.8s**（5 次里 4 次；一次 3.7s 是机器噪声）。
**等价性**：1235 个用例零丢失、零改名；断言数只动了 1 条（就是新加的那条屏障断言，3 → 4）。

**被否决的备选**：① 把 `sleep(400)` 缩短成 `sleep(50)`——仍是墙钟猜，回归时更难看清；② 给 SWR 缓存加
`settle()` 之类的测试专用 API——「等下一次重算落地」本身就带竞态，语义不稳；③ 只把 `waitUntil` 的步长
调小（100 → 20ms）——省的只是过冲，没去根；④ 保留 `*.invalid` 靠 DNS「立即可靠失败」——实测就是不可靠。

**判据 / 先例**：`core/net.ts` 头注「一切等待以命令输出/健康探测触发，无硬编码 sleep」· `stack/local-worker.ts`
的 `/ready` 探活（同一条原则在产线代码里的形态）· README 纪律新增「测试不写墙钟等待」。**对照指针**：
`dashboard/tests/helpers/probe-stub.ts`（`nextProbe` / `guardMs(msg)`）· `dashboard/tests/helpers/console-fixture.ts`
（gpu1 死回环端口 + 理由）· §2026-10-01-goalnn-dashboard-gate-net-direct（上一轮）。

## §2026-10-01-goalnn-tests-mirror-package-tree（2026-10-01，nn-training 单测按源码包树镜像：`tests/<pkg>/` 子树，跨包守卫留根）

**背景**：六包重组（§51–§55・`plan/nn-training-module-reorg.plan.md`）明确留的后续刀——该 plan 写「`tests/` 目录本轮不动」。
用户口径（2026-10-01）：「nn-training 下的单元测试，也按照现在的项目源代码文件树结构，重新拆分组织」。

**决策**：`nn-training/tests/` 从 271 个 `.py` 的扁平目录改为**按包边界镜像**（`common/` `biz/` `worker/` `remote/` `hub/` `trainer/`，
外加 `nn-training/tools/` 的镜像 `tests/tools/`）；**跨包/结构守卫与基础设施留根**（`conftest.py` · `helpers/` · `golden/` · `subproc_util.py` +
10 个全仓纪律守卫：layering / remote_dag 三包账本 / LOC 预算 / 墙钟纪律 / CI / githook / conftest / dashboard kick-once / 入口脚本结构 / subproc_util）；
**`e2e/` 不动**。只镜像到**包**这一层——包边界是分层守卫钉住的语义边界，不再往下拆子包。

**判据（分类口径）**：机器探针（文件名 token × 包内模块 stem · import 深度 · 源码路径串）定 197 个，人工裁定 45 个（探针盲区 = 浅层
`common.protocol` / `common.schema` import 压过主语：`test_remote_ppo` → `remote/`、`test_multi_course_hub` → `hub/`、`test_hot_reload` → `biz/` …），
裁定依据一律是**被 import 最深模块的归属 + 文件 docstring 的主语**；扫多包或扫全仓的守卫留根。

**读数**：留根 10 test + 3 基础设施；搬动 258（`common` 23 · `biz` 11 · `worker` 92 · `remote` 61 · `hub` 17 · `trainer` 48 · `tools` 6）。
纯搬对账：HEAD worktree 逐模块 `--collect-only` **用例数逐条相同**（268 模块 / 3379 例，零丢失零改名）；nn 门禁 **3376 passed / 3 skipped / 26s** ·
dashboard **1235 / 0** + typecheck · 根 `bun run check` **2277 / 0** · `bun run build` 过。链接改写的工具面三处：ruff per-file-ignores 补
`"tests/**/*"`（原 `"tests/*"` 只覆盖直接子文件）· 两处递归扫描的守卫 `glob` → `rglob`（`test_subproc_util._test_files()` 自带「扫了个寂寞」断言，
落地时当场红）· `test_plan_run_split` 读 `tests/remote/test_run_loop.py` 源码的字面路径。此外生产脚本
`worker/scripts/regen_reward_golden.py` 的 `from tests.test_reward_golden import …` 与 `ipynb/tpu-probe.ipynb` 的 `tools/tpu-probe.py`
逐字节副本（单源守卫）都随搬家同步（后者跑 `tools/sync_tpu_probe_nb.py` 重生成）。

**被否决的备选**：① 各包共置（`common/tests/`）——无仓内先例，会把门禁参数/层语义/跨测试 import 的包路径分裂到六处；
② 再往下镜像子包（`tests/worker/ppo/`）——目录不是语义边界；③ 扁平 + 前缀命名（`test_worker_*.py`）——没解决「按源码树找用例」；
④ `e2e/` 一起拆——集成用例天然跨包，拆它只会得到 12 个装错抽屉的文件；⑤ 把 217 个文件的锚改写做成「就地保留旧写法」——
`.parent.parent` 在新深度下语义就错了，不存在兼容解。

**回报口径**：历史散文（`DECISIONS.md` / `docs/**` / `plan/**` / `.workbuddy/memory`）按 §55 散文轮「日志/年代快照/决策表留」先例**不改写**，
跟踪代码 + `README.md` 已全部指向新家（166 文件 / 239 处，`tmp/nn_tests_repath.py`）；残留约 380 处历史散文路径，要清另起一轮并逐文件断言。

**散文轮（同日，续）**：上面那条「历史散文不改」的残留（~380 行命中）按 §55 先例另起一轮清掉——**40 个文件 / 723 处**
（`DECISIONS.md` 72 · `docs/**` 405 · `plan/**` 121 · `.workbuddy/memory/**` 108 · 未跟踪草稿 3 份 18；脚本 `tmp/nn_prose_refs.py`）。
判据改为「名实」：① 只改**被搬动**的测试名（留根名/`helpers/`/`conftest.py`/`subproc_util.py`/`e2e/` 一字不碰）；② 只改路径形态，
`dashboard/tests/test_*.py` 这类同形异根靠后视保护；③ 三道自检：无解命中 = 红 · 落盘后旧形残留 = 0 · 无 `tests/<pkg>/<pkg>/` 双插。
**逐文件断言**（40 个文件的命中数/改写数都打出来）后落盘：旧形残留 0。顺带修 4 处**层号错**的散文（`tests/` → `e2e/`：
`test_run_rl.py` · `test_run_rl_m1.py` · `test_bc_epoch_e2e.py` ×2 —— 它们一直在 e2e 层）。**留下的**：`.workbuddy/memory` 是 gitignore
（改了只是本地日志可读，不入提交）· 3 份未跟踪草稿（改了但同样不入提交）· 事故字面量（`tools/githook/pre-commit` 与
`tests/pre-commit-staged-scope.test.ts` 里那个已删的 `test_remote_degrade.py` 是自检夹具/事故记录）· 7 个**不存在**的历史测试名
（`test_remote_degrade` / `test_race_broadcast` / `test_rl_queue_itest` / `test_notebook_retired` / `test_serve_courses` / `test_serve_args` / `test_x`）
——前者是删除/改名留下的指针，不在本刀搬家面内。

**续二（同日）：7 个「不存在」的历史测试名按「先找继任者、再改指针」清掉**——逐个用提交史（`git log`/`-S`）与现存用例交叉验证：
`test_remote_degrade` → `tests/trainer/test_remote_failure_policy.py`（c23d877 单 PPO 路径时删旧名，新文件自述「去降级版」）·
`test_race_broadcast` → `tests/hub/test_priority_schedule.py`（dd171f9 竞速退役；旧条目就地加 supersede 追记）·
`test_rl_queue_itest` → `e2e/test_run_rl.py`（`RUN_RL_ITEST` 门仍在；旧名只在 plan 拆分清单里出现过）·
`test_serve_courses` / `test_serve_args` → `tests/trainer/test_serve_wiring.py` + `e2e/test_loop_supervisor_integration.py`
（两处 docstring 同行的第三处幻名 `e2e/test_serve_integration.py` 一并修）· `test_notebook_retired` → **无继任**
（退役被 ad465f3 撤回）· `test_x` = 格式示例（非指针，repath 说明改用 `tests/test_*.py` 泛指）。改后复扫
（含 `tests/<pkg>/` 与 `e2e/` 全形态：939 处 / 233 个去重名）**悬空指针 0**；余下 11 处全非指针：历史注记 6（旧名带「今 →」/「无继任」/「幻名」，含本段与 engineering §56 对该 e2e 幻名的记载 2）·
事故/自检字面量 4（`tools/githook/pre-commit` ×3 含 selftest 合成 mypy 行、`tests/pre-commit-staged-scope.test.ts` ×1）· 格式示例 1。

**续三（同日，dashboard 侧）**：同一口径暴露的 4 个已删 `dashboard/tests/` 名字同样按「先找继任者」落位——
`hub-server-race-arg`（dd171f9 竞速退役）**无继任** · `training-console`（b39d158 按 src 层拆成 34 个）→ 活指针改真：
`dashboard/src/server/server.ts` 注释 → `tests/server-lan-gate.test.ts`、根套件夹具字面量 → `training-console-busy.test.ts` ·
`web-app-course-overview` / `web-app-loopqueue`（fd0af06 课程矩阵合并时删）→ 面板 `dashboard/tests/web-app-coursematrix.test.ts`
+ 视图 `dashboard/tests/web-loop-queue.test.ts`；历史回归表就地加「今 →」，拆出来源自述保留（provenance）。详见 `docs/nn/engineering.md` §56「续三」。

**判据 / 先例**：AGENTS §6.3（决策记录：有被否决备选 · 未来会重犯 · 不能就近表达）· §55（刀 7 的层数前提：脚本模式 `sys.path[0]` 与
`Path(__file__)` 上溯层数）· AGENTS §5（提交只动已跟踪文件 + 人点名的那份）。**对照指针**：`docs/nn/engineering.md` §56（全文与三条真机前提）·
`plan/nn-training-module-reorg.plan.md`（六包目标）· `nn-training/README.md`（目录树已同步）· `tmp/nn_tests_migrate.py` / `tmp/nn_tests_verify.py`
（映射与纯搬对账脚本）· `tmp/nn_prose_refs.py`（散文轮脚本 + 三道自检）。

---

## §2026-10-01-goalnn-gate-halt-platform-level（2026-10-01，门禁停机模式升为平台级单开关：`tmp/gate-halt.json` 意图 + 回执；课程级三写面退役）

**决策**（用户 2026-09-25 口径：「`gate-halt-mode` 应是平台级而非课程级，有人盯盘时切为提示，离开时切停机」）：
「门禁触发时停不停」从课程级散装旋钮升成**平台级单开关**——`tmp/gate-halt.json`（控制台写、
supervisor 每轮判定读）+ `tmp/gate-halt.applied.json` 回执（逐课 `effective_mode` / `source`）；
`notify` 可带 `until`（2/4/8h/不限，缺省 8h），**到点由读时求值自动回落 halt**（不需要任何进程定时翻牌）。

**为什么**：它回答的是「**有没有人在盯盘**」——操作员此刻的状态，不是某门课的属性。课程级三写面
（每课 argv `--gate-halt-mode` / `courses.<课>.gate_halt_mode` / `<traj>/gate-halt-mode.txt`）外加隐藏第四面
（`rl.gate_halt_mode`）造成两类事故：① **配对腿漂**（2026-09-25 现场 `x20-dodge-l3` 写了 `notify` 而 `l1` 没有
⇒ 同一实验一腿停、一腿只提示，序列可比性受损）；② UI 摆在平台位置、语义却是课程级。

**两个裁决点**（评审发现初稿自相矛盾 / 落点被分层守卫拦）：

* **O4 优先级 = 平台文件 > CLI `--gate-halt-mode` > 缺省 `halt`**；CLI 只兜底「文件不存在」。
  备选「CLI 优先（单课例外临时用 CLI）」被否决：那会让控制台写出的意图被历史命令行静默盖掉，
  「一处切、全局生效」当场失效。
* **O5 模块落点 = `nn-training/worker/gate_halt.py`**（L0：只 stdlib + `common.log`）。备选
  `biz/gate_halt.py` 被否决：刀 6 后 `biz/` 的定位是「只留游戏业务」（12 模块），训练控制面住进去是
  类别错误；`trainer/` 则会被 `tests/test_layering.py` 的 `TRAINER_ORCHESTRATION` 快照拦下
  （每模块必须可达 `remote|worker`）。代价是显式的：`tests/helpers/remote_dag.py::LAYERS` 登记一行
  （ownership，不是罚款）。

**保守方向（不可回退）**：任何「读不到」都回落 `halt`，**绝不回落到 notify**；文件**存在但坏**
（非法 JSON / 非法 mode / `until` 非数字）⇒ `halt` + 告警，且**不许被旧 CLI 值接管**。

**被否决的备选（逐处）**：① 残留 `<traj>/gate-halt-mode.txt` 首次迁移成平台值——会把**一门课**的值
升格成全平台，违背「平台级是人显式切的」⇒ 取「忽略 + 首次告警 + 控制台提示可删」。② 把开关做成
rl-config 键——它每进程启动才读一次，而平台开关要**每轮热读**（且 rl-config 承载密钥、写面重）。
③ 离线腿「也能 notify」——今天云机侧没有读点（读点只在 supervisor），不为此改 hub 下发链路；
取 `NN_GATE_HALT_LEG=offline` 短路 + `source=default` 日志兜底。④ 顺手重构 `trainer/loop_control.py`
（同构范式）——不动：它有自己的契约与用例，同构不等于同体。

**为什么值得记**（AGENTS §6.3）：① 有被否决备选（上面四条）；② **未来会重犯**——「把操作员的
此刻状态写成课程属性」是同族错误的温床（下一处可能是：谁在盯盘 / 这条腿要不要停 / 是否在维护窗口），
判据是「这个值描述的是**课**，还是人/机群？」；③ 不能就近表达：契约、优先级、读时求值、两侧原子写、
离线腿口径跨 python/TS 两棵树。

**指针**：全文 `docs/nn/training-stack.md` §27（训练侧）+ `docs/nn/console.md` §21（控制台侧）·
计划 `gate-halt-platform-level.plan.md`（**未跟踪**，不入库）· 实现 `nn-training/worker/gate_halt.py` ·
`trainer/loop_guards.py::_gate_halt_mode`（唯一读点，移 hub 短路之前）· `worker/cli.py`（`default=None`）·
`trainer/loop_serve.py`（白名单摘除）· `rl_config.schema.json`（`rl.gate_halt_mode` 挪 retired）·
`dashboard/src/stack/gate-halt.ts` · `core/paths.ts` · `server/api/route.ts` · `web/view/gate-halt.ts` ·
测试 `nn-training/tests/worker/test_gate_halt.py` · `dashboard/tests/gate-halt-platform.test.ts`。
落账门禁：nn 子集绿（gate_halt 19 例 + 守卫族：意图 / 分层 / 护栏）· dashboard 1245/0 + typecheck · 三份 bundle 过。

---

## §2026-10-01-goalnn-lease-orphan-reap（2026-10-01，孤儿租约早收：claim 后**零心跳**超 `ORPHAN_GRACE_SEC=180` 即回池；hub-only，协议面零改动）

**决策**：租约若**自 claim 起一次成功心跳都没有**且 `now - claimed_at > ORPHAN_GRACE_SEC` ⇒ 判为孤儿，
走**同一条** `_collect_expired_locked` 回收路径提前回池（不等满 `CLAIM_TTL_SEC=300`），并记账本事件
`lease-orphan-reaped {job_id, worker, silent_sec, reclaims, ts}`。实现只在 hub 侧：
`hub/store_leases.py` 新增 `ORPHAN_GRACE_SEC` + 唯一判据 `_lease_state`（四态
`LEASE_NONE/ALIVE/EXPIRED/ORPHAN`）+ `_lease_held`（布尔视图，供拿不到常量的兄弟混入）。

**为什么**：认领是**单程**——hub 在 claim POST 到达时即设 owner+expiry+`last_heartbeat`，而
`lease_token` 装在响应 body 里；客户端读超时（`remote/job_lifecycle.claim_job`，`timeout=30s`）⇒
「hub 有主、世上无人持有 token」。还租与心跳都要 token ⇒ 这份 job 在 peek 里**隐身整整一个 TTL**
（2026-09-28 bc-human-retrial job `460b637b`：16:09:39 → 16:14:12，V100 干烧 5 分钟）。
立项理由不是省机时（触发苛刻、一年几次），是**队列诚实**：peek 看不见的 job 不该存在。

**判据只能长在既有事实上**：store 里唯一会写租约活动的信号是 `heartbeat()`——`/start` 只打
`computing_at`、`/ready` 只 `set_ready`、`/result` 只做 token 校验，hub **看不到**下载进度
（blob GET 不记任何租约活动，POST 面就是那 10 个）。把「零 /start / 零下载进度」写进判据会逼实现者
给四个端点各加一个写点；改成「零心跳」后**零新状态**（`_last_heartbeat` 不晚于 `_claimed[jid]["at"]`，
正是控制台 `last_heartbeat_ago` 读的字段）。

**三条边界**：① **push 腿豁免**——`PushDispatcher` 住 hub 进程内、只在 `_await_result` 里续租，
**上传段零心跳是它的正常形状**（判它孤儿 = 同一份活两处跑 + 回传 403 丢结果）；判别据 = 协议层已有的
`PUSH_WORKER_PREFIX` + `push_worker_id_of`。② **离线腿不适用**（`/offline/lease` 是小时级另一套，
不经 `_JobStore._leases`）。③ **手工认领（无 worker_id）适用**——它同样不可能有心跳。

**实现形状（★ 判据唯一，否则做出更坏的形状）**：池过滤 / `_claim_locked` 的 `recovering` /
`lease_worker` / `inflight` **四处同源**调 `_lease_state`。只在 `claimable_job_ids` 的过滤里加判据
是不够的——`_claim_locked` 会因 `job_id in self._claimed` 回 `held` ⇒ **池里看得见、谁都领不到**。
回收点仍只有 `_collect_expired_locked`（stale + 毒包计数 + `_drop_commitment_locked`），
**不许**用 `release()`：它不动 `_claimed`，那份 job 会被 highest 唯一性闸永久挡在门外。
无后台 tick：沿用「谁先看谁回收」⇒ 事件时刻 = 第一次有人看的时刻，`silent_sec` 是被看见时的静默秒数。

**代价（写下来才算诚实账）**：误杀 = 一次 reclaim（`_reclaims+1`，三度达 `FREEZE_AFTER_RECLAIMS=3`
冻成毒包）+ 进 `_stale_holders`（避让链在 ≥2 活跃 worker 时拒它重领）+ 结果回传 403 被丢
（`remote/result_upload._call` 吞成一行 ★ 日志）+ 同一份活两处跑。窄判据下只剩一种窗口：
**已收到 token 却在心跳线程起来之前死掉/卡住**（`remote/job_round.run_round` 在下载之前就起心跳）。

**被否决的备选**：① 认领下载两阶段（意向锁短 TTL + 就绪转正）——两端都动，云机旧码不认新语义就要
全网升级一次，不值得为一年几次的事故付升级税；② blind-abandon（凭 worker_id 放宽 H2）——开放 hub 上
等于允许别人 abandon 你的 job（可 DoS）；③ 过期回池后优先重领——治发现延迟，不治隐身本身；
④ 「静默 180s（从最后一次活动起算）」而非「自 claim 起零心跳」——会把「心跳过两次后死掉」的租约
提前判死（误杀率上升），而事故形状（零心跳）不需要它；⑤ 取 `CLAIM_TTL_SEC` 的一半当阈值——与
`HEARTBEAT_SEC` 脱钩且对「心跳过但慢」的活 worker 同样误杀。

**为什么值得记**（AGENTS §6.3）：① 有被否决备选（上面五条）；② **未来会重犯**——「给观测面加一条
写点就能让判据更准」是同族诱惑（本次拒绝了给 `/start`/`/ready`/`/result` 加写点），判据是
「这个信号在**今天的 store 里**有没有」；③ 不能就近表达——判据唯一性、push 豁免的理由、
误杀的具体代价跨 `store_leases`/`store_ledger`/`push_dispatch` 三处。

**指针**：全文 `docs/nn/remote-transport.md` §52 · 计划 `plan/lease-orphan-reap.plan.md`（2026-10-01
评审修订版，§7 记 O1–O6）· 实现 `nn-training/hub/store_leases.py`（`ORPHAN_GRACE_SEC` / `_lease_state` /
`_lease_held` / `_collect_expired_locked(orphan=)`）· `hub/store_ledger.py::claimable_job_ids` ·
回归 `nn-training/tests/hub/test_hub_leases.py`（4 例新用例 + 既有 TTL 边界用例补一次心跳）·
`tests/hub/test_hub_job_store_split.py`（方法计数 49 → 51）。
落账门禁：`tests/hub` 294/0 + push 腿族（`tests/remote/test_hub_push_dispatch.py` 等）绿 ·
ruff + mypy 全量绿。

## §2026-10-02-goalnn-transfer-residual-yield（2026-10-02，让路税清算：条件让路 + 按传输累计上限 + 观测补面）

**背景**：L2 真机实测一条 wire 传输让路 **7.0s（= 传输时间的 38%）**，其中 **5.5s 压在关键路径**
（`payload` 1.0 + `blob:opt` 2.0 + `blob:ref` 2.5）。机制：控制请求整体在途期间 `_read_body` 每
256KB 分片都无条件让路，而旧预算只有「**单次调用** ≤5s」、**一条传输无累计上界**。

**备选与否决**：① 保持「无条件让路」（2026-09-25 §50 现状语义）—— 否：`p0_p95=1.02s` 远低于既有
`≤6s` 门槛（余量 ≈6×），让路买到的保护用不完，而税随取消环 1.5s 周期结构性复发；② 直接默认删让路
（`never`）—— 否：没有真机 `p0_*` 对照前不拍死，留 `always`/`never` 逃生口，按预注册门槛现场裁。

**决定**：让路条件化（**最老在途**控制请求 ≥ `YIELD_AFTER_SEC_DEFAULT=1.0s` 才让）+ 按传输累计 ≤5s；
库层缺省保持老语义（0.0 / None），由 worker 显式装配 `--bulk-yield {auto,always,never}` 且 `worker_loop`
**finally 还原**；观测补面（label 带优先级 / `p0_p50` / 排队按 jid 归属 / 预取命中入账 + 每轮摘要 /
`wire_report.py` 调度账）。两条预注册门槛（不改默认）：预取 `hits/misses < 1:3` 且白传 ≥3MB ⇒ 默认关；
`never` 臂下 `p0_p95`/`p0_max` 劣化 <2× ⇒ auto total 收到 0。

**违反后果**：装配不还原 ⇒ 同进程后续测试确定性变红（`test_control_plane_bypass.py` 的 bulk ~0.1s、
age 到不了 1.0s）；把「单次」预算当「每条传输」⇒ 改库层缺省语义并丢掉 45s/60s 的上界推导；
`_wire_hit` 落 `_ensure_payload` ⇒ 把 push 腿误标为预取命中。

**指针**：全文 `docs/nn/remote-transport.md` §53 · 现场账 `docs/nn.progress.md` 2026-10-02 节 ·
计划 `plan/transfer-residual.plan.md` · 回归 5 文件新 19 例 + 1 例加断言 · 门禁 nn **3483 passed /
9 skipped** · 根 `bun run check` **2345 pass / 0 fail**。

## §2026-10-02-goalnn-eval-final-round-and-dropped（2026-10-02，收官轮 eval 缺失 + dropped 局静默丢失）

- **背景**：h4-aim-k25 真机产物显示 ① it40（收官轮）训练期间 eval 零局——多课程 serve 的收官
  `finish_course` 没有 drain，而延迟派发只到 it-1 ⇒ 最后一轮权重永远没有 eval 路径；② it0 缺 8 局、
  it25 缺 2 局——`seen`（认领）在 `record()` 落盘之前计数且异常逃逸，缺口既不补派也不带原因
  （plan/eval-final-round-and-dropped §1–§2）。
- **备选与否决**：把 `seen.add` 直接下移到 `record()` 之后 —— 否：tail-race 副本可在原件 record
  进行中结算（去重全看 `seen`），下移后同 (stage,seed) 记两行、wins/n 双计（W0 fanout 用例可证）；
  「同 wver 旧轮并入本轮 pending」补旧轮 —— 否：`wver = sha256(权重字节)` 每轮变，结构性无效；
  「dropped 口径留给读方兜」—— 否：口径必须单一（谁喂公式只有一处）。
- **决定**：`finish_course(it, *, drain=True, block=True)` 收编收官 eval drain（serve 的 RL 传
  `block=False` 不冻其它课；BC 另一份实现按 kind 分派、不带 kwargs）；claim 集（`seen`，位置不动）
  与 landed 集拆分——`_settle_complete` 与 `settle_eval_summary` 只认**落盘**（参数 `seen`→`landed`，
  `dropped` 公式一字未动，语义=结算→落盘）；`record()` 失败本线程重试 `EVAL_RECORD_RETRY_MAX` +
  `record-failed` meta + WARN，仍失败计入缺口；summary 新增 `missing`（有界 20，原因
  record-failed/node-failed/undispatched）；收官 drain 按归档权重**逐轮**补（不再只 cand[-1]）。
- **违反后果**：把 `seen` 下移 ⇒ fanout 双计（账本行与胜率分母漂移）；给 BC 的 `finish_course`
  传 kwargs ⇒ TypeError 带崩 serve；serve 用 `block=True` ⇒ 冻住其它课（plan N4）。
- **指针**：计划 `plan/eval-final-round-and-dropped.plan.md` · 全文 → `docs/nn/engineering.md` §61 ·
  回归 `tests/trainer/test_finish_course_drains_eval.py` · `tests/trainer/test_eval_dispatch_resilience.py`
  （2026-10-02 节）。

## §2026-10-02-goalnn-course-pill-precision（2026-10-02，pill 精确化：hub 派发面接进 pill + 只读读面拿 iters 判收官）

- **背景**：两起独立缺陷。① pill 的在线课五态全是**本地进度词**；h4-aim-c0 的首个 job 被一个误启
  持有者「有心跳地」按住 27 分钟（真 GRAD 33.7s，对照 k10/k25 同配置 2.7/3.3 分钟），三道既有
  防线结构上判不出（孤儿要零心跳 / 熔断要租约过期 / 队列停滞检测看 pending→completed 缺口），
  而 pill 只会说「等回传」。② h4-aim-k10/k25 跑满 40 轮仍显示「推进中」——只读读面按盘重建计划
  却拿不到 `iters` 预算，`state='done'` 从未点亮。
- **备选与否决**：① 对「有心跳但慢」加自动处置（回收/降优先级）——否：先要读数面跑一段再定阈值
  与动作（plan O1），且不动调度是本刀的边界；② 用账本 `run_complete` 当收官判据——否：它是上一次
  run 的陈述（改大 iters 续跑后陈旧），收官 SSOT 是预算，第二份判据必然分叉；③ pill 布尔
  `peeked`——否：无法区分「旧 hub 没字段」与「确实不在窗口」；④ 控制台自己判收官——否：显示层
  不新增判据；⑤ 本机 inflight 与「排队·无人取」并置——否：对不上 hub jid = 本机在算，必须压过排队态。
- **决定**：hub 观测面纯加法（inflight 行 `claimed_ago`/`computing_ago`；顶层 `peeked_courses`
  = 60s 内被 `peek_jobs` 返回过候选的课程集；只被 `/admin/queue` 读，调度判据零引用）· 收官判据
  单点 `worker/loop_tasks.budget_exhausted(it, iters)`（`iters<=0` ⇒ False；`LoopRunner.planner`
  行为逐字节不变），读面 `plan_course(iters=)` 跑满 ⇒ 空任务表 ⇒ `QUEUE_DONE`，
  `waiting_state(finished=)` ⇒ `WAIT_IDLE` + 诚实文案（不新增 WAIT kind）；预算 = 课程声明
  `curricula/<课>.jsonc`（读不到 ⇒ 0 = 不限，保持今天行为）· pill 四态（卡住/等回传/排队·无人取/
  预取中）+ 龄 + jid 连接（本机/远端）+ 未登记持有者点名；`PILL_STUCK_SEC=300` 是**展示层常量**，
  不得被 hub/训练侧 import。
- **违反后果**：展示字段接进 `_claim_locked`/优先级 ⇒ 展示需求烧进调度语义；读面写第二份收官判据
  ⇒ pill 与实际续跑各说各话；缺龄编 0 ⇒ 卡死 27 分钟显示成「刚认领」。
- **指针**：全文 → `docs/nn/remote-transport.md` §54 · `docs/nn/training-stack.md` §28 ·
  `docs/nn/console.md` §22；计划 → `course-pill-precision.plan.md`（评审 P1–P4 已并入）；回归 →
  `tests/hub/test_queue_observe_pill.py` · `tests/worker/test_loop_tasks.py` ·
  `tests/trainer/test_loop_plan_waiting.py` · `dashboard/tests/training-pills.test.ts`。

## §2026-10-02-goalnn-worker-contribution（2026-10-02，并行 worker 贡献度：承接面归属 + 两个单位组永不合并 + 课程只作 breakdowns 轴）

- **背景**：7 门课并行、2 个云端 worker 领活的场景下，「谁干得多」在数据面上不成立：采样侧只有绝对
  局数（无份额/无课程维度/无盯盘窗）；PPO 侧**归属在结算那一刻被丢掉**——`job_completed` 由训练侧写
  （`remote/hub_client.py::mark_job_completed`）且无 worker，身份只在 hub 承接结果那一刻可得。评审另纠
  两处：`hub/store_leases.py::mark_completed` 是生产零调用者的死代码（改它没有数据）；`DayBucket` 无
  per-event 时间戳（滚动窗不能从日桶投影，须有界子日事件环）。
- **备选与否决**：① 做一个综合贡献分（局 + job + 秒加权）——否：口径混算（与「成功率/产能分列分名」
  同款裁决相悖），且会把 7 门课之间的差异抹平；② 把课程维度接回「按课缓存」——否：2026-09-26 解耦
  裁决不动，课程只作 breakdowns 轴（按课名、不用 `it`）；③ 让云端 PPO worker 的采样局也计进 PPO 组
  （或反向合并）——否：两组分母/单位/失败语义都不同，用户裁决**角色隔离**（归属由来源决定），同名不合并；
  ④ 滚动窗从日桶投影——否：日桶只有日级计数、无子日时间戳，会做出看着对实际错的窗。
- **决定**：① hub 承接面新增归属事件（**扩事件不新建账本**）：`accept_result` 成功写
  `job_result_accepted`（worker = 显式传入 ∪ 当前租约持有人，push 腿 `push:` 前缀），三条 409 路径
  （`_post_result` 早退 / `store_result` 失败 / push 输家）写 `job_rejected`；结果 POST 补 `X-Worker-Id`
  （`post_result`，与 claim 同一身份）。事件与训练侧 `job_completed` 同册（`tmp/<课>/training_log.jsonl`），
  读侧按 `job_id` join。⚠ 身份缺失（旧 worker 无头）⇒ 回退当前租约持有人，但 `lease_worker` 住在每课
  `_JobStore`（`_HubQueue` 没有）⇒ 必须经 `_store_of` 解析，结果面不得 500。② 面板两组**永不合并**
  （采样=局 / PPO=job），份额分母组内自洽，缺数据「—」；课程维度只作 breakdowns（机器×课矩阵 + 单点依赖
  标记）；新增 `近 30 分钟/近 2 小时`滚动窗（既有 4 档一字不动），滚动窗吃**有界子日事件环**（2h+10min 保、
  每节点 20000 条、触顶 `rollingTruncated` 出脚注）。③ 落位：节点页主体（份额列表 ⇄ 矩阵）+ 首页一行
  缩略（同一份聚合的 `compactSummary` 裁剪，防两份真相）。
- **违反后果**：把两组分母互加/做综合分 ⇒ 产出既非局数也非 job 数的假数；按名字归并同名身份 ⇒ 把采样机
  与云机 worker 当同一台机器；用 `mark_completed` 或训练侧 `job_completed` 补归属 ⇒ 永远空手（写手无
  身份）；从日桶投影滚动窗 ⇒ 窗内数字静默失真；缩略行另起一份计算 ⇒ 首页与节点页对不上；`_append_attribution`
  直接调 `hub.lease_worker` ⇒ 旧 worker 的结果面 500（全量门禁抓到，回归用例已钉）。
- **指针**：全文 → `docs/nn/console.md` §23 · `docs/nn/remote-transport.md` §55；计划 →
  `plan/worker-contribution-view.plan.md`（2026-10-02 评审修订版）；回归 → `dashboard/tests/worker-contribution.test.ts`
  （12 例）· `nn-training/tests/hub/test_worker_attribution.py`（6 例）；实现 → `dashboard/src/server/contribution.ts` ·
  `dashboard/src/web/view/contribution.ts` · `dashboard/src/server/pool-history.ts`（滚动环/byCourse）·
  `nn-training/remote/push_dispatch.py`（`_append_attribution`）· `nn-training/remote/job_lifecycle.py` ·
  `nn-training/hub/result.py`。
## §2026-10-02-goalnn-e1-aim-open（2026-10-02，E1（aim 距离加权）开腿：剂量 κ 冻结 + 判据段 864001 + paired 止损显式对端；E2 终门现役复算 0.796；E3 复测门读数）

- **背景**：aim-dodge 8 列（dim 74）已落地（§2026-10-01-goalnn-aim-dodge-metrics）；E1–E4 的
  「开腿前必办」四项欠账在 2026-10-02 开课前补办。编排由用户拍板「**顺序波次、E1 先开三臂**」
  （每波自带对照臂、沿用 §5.5 的 3 臂预算）；E1 判读后按序开 E2/E3a/E3b；**E4 定义上依赖前三波
  最优子集**，不在首批。
- **决定（E1 开腿）**：三臂 = 对照 `h4-aim-c0`（v4 逐字，无 aim 项）+ `h4-aim-k10` / `h4-aim-k25`
  （唯一差 = `+ wAim*aimHitDistSum`）；同起点（h4-stop.it60）同包、同 `paired_rotate_seed=20261002`
  （新开，§15.1）、新 out/traj（§15.5）；控制台 openCourse（在线档）开课。判据段 = **段A
  （seed0 864001–864300，300 局/点 @ it30/35/40）**，与日常段 860001–860200、Wave 1 补评估
  861011–861310、Wave 2 判据段 862001–862300、开腿前校准段 863001–863200 均不相交。
- **决定（剂量冻结）**：校准语料 `tmp/aim-calib/c04-calib-863001.jsonl`（b0 it30/35/40 + b1it40 +
  h4stop60，各 200 局；b0 合并 n=600）：E[d|hit]=2.9284 格 · E[aimHitDistSum/局]=28.473 ·
  E[kills/局]=3.603 ⇒ killEV=10.810。κ = wAim·E[d|hit]/wHit ⇒ **wAim = 0.0205（κ=0.10）/
  0.0512（κ=0.25）**（税 EV 0.583 / 1.458 每局；killEV 占比 5.4% / 13.5%，落在 3–20% 记账区）。
  预注册 MDE（900 配对）：E[d|hit] MDE80=0.079 格（2.7% rel）、kill 线=MDE/2=0.039 格；
  `playerShots/(ticks+1)` MDE80=2.0% rel（防刷量读数）。
- **决定（止损对端）**：`kickstart_burn` 走 paired + **显式 peer**
  （`courses.h4-aim-k10|k25.kickstart_burn = {mode:'paired', peer:'h4-aim-c0'}`）；用户批准手写
  rl-config 两块（控制台无此写面）——不开显式 peer 会重演 Wave 2 的静默 baseline 回退
  （§2026-09-30-goalnn-kickstart-burn-paired / experiments §69 ⑧）。
- **决定（E2 终门复算）**：现役拌入列、b0 n=600 ⇒ r_within=**0.796**（95% CI [0.767,0.824]；
  逐点 0.812/0.812/0.763）⇒ 按预注册点估计 ≤0.8 **不替换 `wDmg`**（E2 保持叠加单变量设计）；
  CI 上沿过 0.8 已记档（复核线索：it40 是唯一 <0.8 的点）。
- **决定（E3 复测门；E3a 过 / E3b 待定）**：4 敌 rung 上 encl 有信号且跨臂可辨（b0 0.238 vs b1 0.307，
  +29.0%，t=3.31，196/200 局非零）⇒ E3a 前置过；corner 信号在（165/200 非零，b0 0.272）但
  b0↔b1 跨臂不显著（+8.3%，t=0.84）⇒ **E3b 不做自动放行**，开前由用户定夺（加对照/加局数）。
- **被否决**：① 「r 点估计 0.796 贴线 ⇒ 直接按替换设计 E2」（违反预注册判据 + 改变单变量语义）；
  ② E1 不设对照臂（拿 b0 历史臂后验配对——丢训练期配对止损，且违反「不得用历史臂当对照」）；
  ③ 开 E1b（比值形）——plan 默认计数形，未开（如需另起一腿）；
  ④ 对 69 列产物的额外清理动作——新腿新目录天然干净（在跑 shard 已核全部 (version 10, width 74)，
  self/mac/a95 三节点一致；旧课历史产物只在本课目录内、不被新腿消费）。
- **落点**：课程 `nn-training/curricula/h4-aim-{c0,k10,k25}.jsonc`（头注 = 冻结契约）·
  契约测试 `nn-training/tests/biz/test_h4_aim_courses.py`（4 例）· 配置 `nn-training/rl-config.json`
  （两块 kickstart_burn）· 校准语料 `tmp/aim-calib/` · 实验档 `docs/nn/experiments.md` §74 ·
  索引 `docs/nn.progress.md` §3.5。

## §2026-10-02-goalnn-e2-e3a-design（2026-10-02，E2/E3a 开腿设计冻结：f=7.5%/15% 两档 + MDE 实测 + 段B/C + 新 seed；E1 段A输入判无效）

- **背景**：E1 段A收官（864001 配对 900 局/臂，判据见 `h4-aim-c0.jsonc` 头注）：pooled dE[d|hit]
  k10 −0.037（反号）/ k25 +0.029（< kill 线 0.039）⇒ **机制无效**；Q 过（0.25/4.14 ≤ 5.99）；
  it40 k25 单点 +0.114 未过单点 MDE 且同点主分破线 ⇒ 不得挑点。用户指令按顺序波次开 E2/E3a
 （E4 依赖前三波最优子集，不在首批；E3b 前置未过，不自动放行）。
- **决定（E2 · 承伤 hurt-only，叠加）**：三臂 `h4-hurt-c0`（v4 逐字）+ `h4-hurt-f75`/`h4-hurt-f150`
  （唯一差 = `- wHurt·hurtWeight`，wDmg 保留——Pearson 前置门 r_within=0.796 ≤ 0.8，
  pooled 复核 r=0.798，同结论）；同起点 h4-stop.it60、同包、新 out/traj。
  剂量锚（b0it30/35/40 pooled n=600，`tmp/aim-calib/...run/eval_log.jsonl`，脚本
  `tmp/aim-e2e3a-anchor.py`）：E[hurtW/局]=39.4767，killEV=10.810 ⇒ **wHurt=0.0205（f=7.5%）/
  0.0411（f=15%）**（税 EV 0.809/1.622，记账区；wDmg 税 EV 2.972，不淹没）。
  判据段 = **段B（seed0 865001–865300）** @ it30/35/40 × 300 局；主读数 hurtW/局配对差
  （负向好），MDE80_900=**5.84/局**、kill 线=**2.92/局**（b0it30-vs-b0it35 同 seed 200 配对
  sd=62.54，E1 同法 2.8016×sd/30）；`playerDamageTaken` 非增 + 主分 −3pp + 守卫表
  （stop/hold 非劣）；paired 止损显式 peer=`h4-hurt-c0`；`paired_rotate_seed=20261003`。
- **决定（E3a · 被包围 encl，速率形）**：三臂 `h4-encl-c0` + `h4-encl-f75`/`h4-encl-f150`
  （唯一差 = `- wEncl·enclWeightTicks/(ticks+1)`，速率形；共线名单 encl2/3pTicks、
  nearEnemy4Ticks 同腿禁上）；复测门已过（4 敌 rung b0 0.238 vs b1 0.307，+29%，t=3.31）。
  剂量锚（同源 n=600）：E[rate]=0.209396 ⇒ **wEncl=3.87（f=7.5%）/ 7.74（f=15%）**
  （税 EV 0.810/1.621；数值 > wKill 是速率口径所致，可比量是税 EV）。
  判据段 = **段C（seed0 866001–866300）**；主读数 enclRate 配对差，MDE80_900=**0.0214**、
  kill 线=**0.0107**（配对 sd=0.22923，同法）；peer=`h4-encl-c0`；`paired_rotate_seed=20261004`。
- **被否决**：① E2 替换 wDmg（门未触发 + 改单变量语义）；② 加 f=3% 档凑三剂量
  （3 臂预算 = 对照 + 两剂量，E1/Wave2 先例；且 hurtW 读数 rel-MDE ~13%，低剂量必欠功效）；
  ③ E3a 用计数形 encl2/3pTicks 定价（raw-only + 同谓词共线，§5.3）；④ 复用 h4-aim-c0
  当对照（跨实验续跑 + 漂移不对齐，§15.5）；⑤ E3b 同批开（corner 跨臂不显著，待用户定夺）；
  ⑥ 给负价腿设开火量刷量线（负价无刷量方向；退化由 stop/hold/kills 守卫捕，E1 对比备忘）。
- **落点**：课程 `nn-training/curricula/h4-hurt-{c0,f75,f150}.jsonc` +
  `h4-encl-{c0,f75,f150}.jsonc`（头注 = 冻结契约）· 契约测试
  `nn-training/tests/biz/test_h4_{hurt,encl}_courses.py`（各 4 例）· 配置
  `nn-training/rl-config.json`（四块 kickstart_burn，显式 peer；本机未入库文件，控制台开课时读它）·
  锚脚本 `tmp/aim-e2e3a-anchor.py` + 段A证据 `tmp/e1-frozen/` · 实验档 `docs/nn/experiments.md` §74 ·
  索引 `docs/nn.progress.md` §3.5。

**指针**：全文 `docs/nn/experiments.md` §74 · 计划 `plan/aim-dodge-levers.plan.md` §5.2–§5.5 ·
实现 `nn-training/curricula/h4-{hurt,encl}-*.jsonc` · 回归 `nn-training/tests/biz/test_h4_{hurt,encl}_courses.py`。
落账门禁：新契约 8/8（+aim 4 例同跑 12/12）· kickstart 23/23（除 1 例控制台开关耦合，见下）·
`bun run check`（进行中，`tmp/logs/check-e2e3a.log`）。
注：`test_guard_also_halts_the_cloud_on_the_same_course` 在控制台「触发门禁：提示」档下红——
平台文件 `tmp/gate-halt.json`（`by: console`，mode=notify）活状态所致；干净 worktree 无此文件即绿、
复制该文件即红（同 HEAD 对照），与本次课程文件无关；开关拨回「停机」即复绿，不改代码。

## §2026-10-02-goalnn-x20-advanced-design（2026-10-02，x20 kitchen-sink 腿：B 底包 + 新 5 项 human-gap 定价，单腿无对照）

- **背景**：用户直接指定（非 plan 推导）：基于 x20 最佳权重（it175），新 5 项全上，大样本、
  多 epoch、无 it 限制；只比最终杀敌数与过关率，不与历史课程单变量对比。底包 = B
  （x20-steady）奖励逐字；起点 = it175（cont 同规，kickstart_ref=false，opt fresh）。
- **决定（校准）**：human 160 局 vs it175 160 局（同关 c20 同 seed，v5 探针，
  `tmp/gap2-panel-v5-{human,nn-x20steady}.jsonl`，脚本 `tmp/x20adv-calib.py`）：
  kills 17.494 vs 6.162、通关 160/160 vs 15/160；Δacc=+0.2062、ΔE[d|hit]=+1.8794 格、
  ΔhurtRate=−0.05478、ΔenclRate=−0.28600、ΔcornerRate=−0.14274（全部与 plan §1.4 互验；
  新列差豁免拌入 A 一项，人类豁免多 ⇒ raw 定价是保守方向，已记档）。
  killEV = 3.0×6.162 = 18.487。
- **决定（形式）**：用户原话 5 个计数项 → 改比值/速率形（x20 horizon 可变 4973t vs 1805t，
  计数形正价给苟活发钱）：命中率形 `aimHits/(playerShots+1)`、均距形 `aimHitDistSum/(aimHits+1)`、
  三税 ÷(ticks+1)（E3a 先例）。命中数 double-count（≡enemyHits−交棒弹）已向用户明示并保留。
- **决定（剂量）**：uniform gap pricing f=7.5% killEV/项（Wave2/E2 默认梯）⇒ **wAcc=6.72 /
  wDist=0.738 / wHurt=25.3 / wEncl=4.85 / wCorner=9.71**（五项走完 37.5% killEV；
  起点净流入 +0.21/局，无梯度冲击；三税与 wDmg 同量级）。
- **决定（预算/判据）**：target 49152（48×1024，ragged=0；B 48000 非 1024 倍数）、epochs 8
  （B 4）、iters 400 / max_hours 48（机械上限；判据只看终点，无中途停）；
  终判 = kills/局 + 通关率 @日常终点 + 414000 段 800 局 vs it175（B 门口径，不移门）；
  熔断只留 kl 连续 3 轮 ≥0.075 记录+人工盯盘；MDE/Q/paired burn 本腿不适用。
- **被否决**：① 计数形照字面上（苟活套利，horizon 差 2.8×）；② 设对照臂/配对（用户明确不要；
  且 kitchen-sink 本来就不是单变量可归因设计）；③ 中途横盘停（用户要无 it 限制，判据只看终点）。
- **落点**：课程 `nn-training/curricula/x20-advanced.jsonc`（头注 = 冻结契约）· 契约测试
  `nn-training/tests/biz/test_x20_advanced.py`（5 例）· 配置 `nn-training/rl-config.json`
  （`x20-advanced: {rollout_src: local}`，B/cont 同形）· 校准脚本 `tmp/x20adv-calib.py` ·
  实验档 `docs/nn/experiments.md` §75 · 索引 `docs/nn.progress.md` §3.5。

**指针**：全文 `docs/nn/experiments.md` §75 · 实现 `nn-training/curricula/x20-advanced.jsonc` ·
回归 `nn-training/tests/biz/test_x20_advanced.py`。落账门禁：新契约 5/5 · `bun run check` 待补。

## §2026-10-02-goalnn-x20adv-h2-design（2026-10-02，x20-advanced hurt 重剂量对照臂：同起点 it175，wHurt 25.3→50.6）

- **背景**：it80 vs 人类复核 hurtR 反向（0.0693 vs 起点 0.0621）；用户指定加一档 hurt 重剂量对照。
- **决定**：新课 `x20-advanced-h2`，与主腿同包同预算、起点为主腿最优 it80（用户改起点；
  wHurt=**50.6**（f=15%，uniform gap pricing；it80 水平税 EV 3.51/局 ≈ 19% killEV，记账区上限内）。
  终判 @414000 段 800 局：hurtR 配对比较 + kills/通关率不劣于主腿终点（±3pp 诊断）。
- **落点**：`nn-training/curricula/x20-advanced-h2.jsonc`（validate_reward 零错误）·
  `nn-training/rl-config.json`（`x20-advanced-h2: {rollout_src: local}`）。

## §2026-10-03-goalnn-x20adv-plus-design（2026-10-03，二轮定价：新基线 a355，acc/hurt 加压，dist 维持，底包全留）

- **背景**：用户指定按出/被背刺两轮人类复核给全部 12 参数重新定价；基础权重用主腿最优 a355
  （414000 段 800 局 24.25%/9.666）。新基线（a355 同 160 对）：acc 0.5656（gap 0.2249，
  比 it175 时还宽——dist 抢戏）· Edhit 3.6756（gap 0.7239，61% 走完）· hurtR 0.06560 ·
  enclR 0.29846 · cornerR 0.09678 · kills 10.181 ⇒ killEV=30.543。
- **决定**：税用 §5.4 基线形、比值奖用 gap-value：**wAcc=13.6**（f=10%，acc 退化加压 rebalance）/
  **wDist=0.738 HOLD**（61% 走完，按 gap 重定价会给最后 0.7 格 3× 边际价，方向反，故维持 ~9% 份额）/
  **wHurt=70**（f=15%，9× gap + h2 的 50.6 已证不够；税 EV 15%）/ **wEncl=7.67** /
  **wCorner=23.7**（f=7.5%，税 EV 各 2.29/局；数值大是率小所致）。底包 7 个全留
  （wPickup 已占 34%、wDmg 由 hurt 专项覆盖、wHit 加量买量、余无信号）。
  起点净流入 +1.23/局（4%），小幅正压，无冲击。
- **被否决**：① dist 按 gap 重定价到 ~3.2（1 格均距 ≈ 1 kill，边际失衡）；② 加 dmgFirst600 税
  （7× gap 很诱人，但 h5a 开局窗已判死，不挖坟）；③ 被背刺率单独立列（走位税已有 encl/corner
  承担；只观测不定价，用户确认方向）；④ 动 wKill/wPickup（numeraire/已占 34%，动即换实验）。
- **落点**：`nn-training/curricula/x20-advanced-plus.jsonc`（validate_reward 零错误）·
  `nn-training/rl-config.json`（`x20-advanced-plus: {rollout_src: local}`）· 实验档 §76。

## §2026-10-03-goalnn-x20adv-plus-rebase（2026-10-03，plus 换基 it415：锚重算，w 全跟进）

- **背景**：it415 日常新高经 414000 段 800 局兑现（28.25%/10.465 vs a355 24.25%/9.666）；
  用户指定 plus 起点换 it415。新基线（it415 同 160 对）：acc 0.5711（gap 0.2194）·
  Edhit 3.7384（gap 0.6611）· hurtR 0.06489 · enclR 0.29256 · cornerR 0.10063 ·
  kills 11.181 ⇒ killEV=33.544。
- **决定**：同算法重算 ⇒ **wAcc=15.3 / wDist=0.738 HOLD / wHurt=77.5 / wEncl=8.60 /
  wCorner=25.0**（税 EV 15%/7.5%/7.5%；起点净 +1.44/局）；bc 指 it415 文件；
  终判对照换 it415（28.25%/10.465）。旧参数复核结论（wShot 0.06 唯一动作）不变。
- **落点**：课程头注已同步；实验档 §76 已同步。

## §2026-10-03-goalnn-x20legs-verdict（2026-10-03，三停腿收官：主腿/plus 成功加冕，h2 诊断成功归档）

- **背景**：三腿停腿（主腿 it592 / plus it167 / h2 it330）。414000 段 800 局终筛：
  主腿 it570/580 + plus it90/95/135/140 + h2 h285/h300（同 seed 可比；既有数复用 it500/p100/a355）。
- **决定（终判）**：① x20-advanced **成功**，加冕 **a580**（28.75%/10.723；亚军 it500）：
  it175（13.50%/7.619）→ 过关翻倍、杀敌 +41%；Edhit 走完 ~50%、encl/corner −37%/−30~45%、
  acc/hurtR 平；停腿时仍在高原爬升，无 plateau 证据。② x20-advanced-plus **成功**，
  加冕 **q90**（29.25%/10.887；q95 28.25%/10.569）：相对高起点 it415 只 +1pp/+0.42，
  相对 it175 翻倍/+43%；hurt 税停在 it167 未成熟（h2 形态：200 轮后才低头）。
  ③ x20-advanced-h2 **诊断成功、终点落后**，归档不加冕：hurtR 0.086→0.061（−29%）
  证明加压有效（plus 的 77.5 即据此定）；终点最优 h285（25.00%/9.963）低于两加冕线。
- **落点**：三课程文件尾 verdict（状态同步收官）· 实验档 `docs/nn/experiments.md` §78 ·
  索引 `docs/nn.progress.md` §3.5。

**指针**：全文 `docs/nn/experiments.md` §78 · 证据 `tmp/x20adv-414k-{best2,best3,best4,final,h2fin}.jsonl(.run/)`。

## §2026-10-03-goalnn-x20adv-3legs-design（2026-10-03，三加压腿 same-start q90 比收敛速度）

- **背景**：plus 停腿（it167）；用户指定以 q90 为共同起点开 2–3 条加压腿比收敛速度；
  被背刺率用户裁决用被包围税近似，不加观测列。基线（q90 同 160 对）：acc 0.5895（gap 0.201）·
  Edhit 3.7044（gap 0.695）· hurtR 0.06062 · enclR 0.25551 · cornerR 0.08616 · kills 11.169 ⇒
  killEV=33.51。
- **决定**：三腿与 plus 逐字同（起点 q90 + kickstart 锚 ref/init=0.1，预算 250 轮），各加压一轴：
  **acc 腿 wAcc 15.3→23.0**（×1.5；inflow 偏大已记档 level-vs-边际）/
  **hurt 腿 wHurt 77.5→110**（1.5× 破 20% 上限 ⇒ 按上限封顶 f=20%，税 EV 19.9%）/
  **encl 腿 wEncl 8.6→13.0**（×1.5，税 EV 9.9%）；dist/corner/wShot/底包全 hold。
  yardstick（三腿共用预注册）：机制首达线（acc≥0.69 / hurtR≤0.034 / enclR≤0.194，
  gap 对半）首达 it + 终判 kills/pass @414000 vs q90（29.25%/10.887）+ 必报另两轴。
- **被否决**：① corner 加压（gap 仅 1.5×，为收尾付高价，dist HOLD 同理）；
  ② 被背刺单独立列（已有 encl 承担；只观测不定价方向已确认，本次连观测列都不加）。
- **落点**：`nn-training/curricula/x20-adv-{acc,hurt,encl}.jsonc`（validate 零错误）·
  `nn-training/rl-config.json`（三项 rollout_src local）· 实验档 §77。

**指针**：全文 `docs/nn/experiments.md` §77。

**指针**：全文 `docs/nn/experiments.md` §76。

## §2026-10-03-goalnn-x20adv-plus-oldparams（2026-10-03，plus 旧参数复核：按 tick 率剥离 horizon，只动 wShot）

- **背景**：用户要求旧参数差距大的一起调。按千 tick 率重算（a355 同 160 对）：
  kills 3.52 vs 3.92（NN 反快 11%）· shots 12.38 vs 16.84（NN 多开 36%）·
  hits 9.75 vs 9.53（逐字相等）· dmg 22.5 vs 100.5（4.5×，真 gap）·
  pickups 1.41 vs 1.28（持平）⇒ **杀敌/拾取/命中 gap 全是“活得短”，只有承伤率和开火浪费是真信号**。
- **决定**：只动 **wShot 0.05→0.06**（NN 火多中少，加罚“少而准”，EV 动 0.44/局）；
  其余 6 个全留（wKill numeraire；wHit 加量只买量；wDmg×2 有 −2.8pp 实付代价且 hurt-70
  已覆盖；wPickup 已占三成；wExplore/wApproach 无信号）。明确拒绝：dmgFirst600
  （h5a 死）、max120/damageWhileLow（damage-form 死）、threatTicks（Wave 2 死）、
  首杀奖（方向反）、停/静止（H 死）。
- **落点**：`nn-training/curricula/x20-advanced-plus.jsonc`（头注旧参数复核节）。

## §2026-10-02-goalnn-engine-pool-midround-pin（2026-10-02，引擎缓存上限不再钉死 5 + 轮内不许抽引擎 + `_node_rollout` 初值）

- **背景（事故）**：7 门课同时开课（h4-hurt-{c0,f75,f150} · h4-encl-{c0,f75,f150} · x20-advanced），
  而 `EnginePool` 数量上限是死值 `DEFAULT_CACHE_COURSES = 5` ⇒ 按 LRU 互逐。被逐的课下一拍
  `EnginePool.get()` **重建一个全新 `TrainingLoop`**，而 supervisor 的 `CourseQueue.tasks` 已走到
  `ppo`/`cleanup` ⇒ 重建引擎缺轮内属性 `_node_rollout`（它只在 `step_course_iter` 里赋值，
  `__init__` 从未初值）⇒ `AttributeError` ⇒ 该课每秒重启一次、一轮都跑不完。
  实测 `tmp/h4-hurt-f75/training_log.jsonl` = **1076 条 `run_start` / 1073 条 `iter_error` / 0 条
  `iteration`**（h4-hurt-f150 同形）。
- **决定（P0，止血）**：`loop_core.TrainingLoop.__init__` 补 `self._node_rollout: bool = False`；
  `loop_round_steps` 的两处裸读（`step_ppo` / `step_cleanup`）改 `getattr(..., False)` 兜底。
  初值 `False` = 本地轮：即便读到早值也只是把采集判成本地，不改变采集行为。
  **不变量**：引擎被重建后必须能安全续跑（与上限怎么调无关）。
- **决定（P1，驱逐语义）**：`EnginePool.pinned`（新，注入式判据，保持本类纯逻辑）；候选为空的
  ⇒ 不驱逐 + 响亮记一行（宁可超预算也不中途抽栈）。判据 = `CourseQueue.mid_round`（新字段，
  本轮已动过手）—— **不能**用 `tasks` 非空：`_finish_round` 会立刻为下一轮重新规划，两者形状相同，
  用它等于永久禁止驱逐。这补的是本类 docstring 早就写着的那句「绝不驱逐正在用的引擎」
  （此前 `keep` 只护住「本次被取的那一门」）。
  连带：`test_serve_bc.test_bc_round_is_not_republished_when_the_engine_is_evicted` 的**触发方式**
  改为显式关钉住（该用例验的是「重建后认领而非重发」，与「何时驱逐」正交）。
- **决定（P2，上限从哪来）**：`serve(cache_courses=0)` = **自动** `max(DEFAULT_CACHE_COURSES, 开课数)`；
  显式正值原样尊重（e2e/单测要靠它构造驱逐）但越界时响亮告警；自动抬升也打一行。
  理由：真约束是**字节顶**（每课栈 MB 级，256MB ≈ 130 课，`scripts/measure_checkpoint_rss.py`），
  数量顶在这里没有保护价值；而「开课数」是人手点的操作面事实。
- **被否决**（都记录备选）：① 只把 5 改大 —— 不修 P1，下次字节顶触发照样崩，且「文档说可配、
  代码不认」继续存在；② 上限设无限 —— 丢掉「越界要可见」这个信号（RSS 实测表就是为它做的）；
  ③ 重建时保留旧引擎的轮内状态 —— 等于持久化 E 类轮内瞬态，本文件已明确否决过。
- **文档口径更正**：`rl.checkpointCacheCourses` / `checkpointCacheMb` **从未被任何生产代码读取**
  （唯一调用点 `run_rl_cluster --serve` 不传参，也无对应 CLI flag）⇒ 本文件 §2026-09-18-goalnn-r2-loop-task-queue
  第四问与 `docs/nn/training-stack.md` 里「可配」的说法作废，改以本条为准（自动 = 开课数；
  要显式上限只能改调用方传参）。**接线该配置键**留作后续（需跨 rl-config schema + launch 透传）。
- **落点**：`trainer/loop_core.py`（初值）· `trainer/loop_round_steps.py`（两处 getattr）·
  `worker/engine_pool.py`（`pinned` / `_is_pinned` / `_log_once` / `_evict`）·
  `worker/loop_scheduler.py`（`CourseQueue.mid_round` + 三处置位、两处清零）·
  `trainer/loop_serve.py`（`_pinned_mid_round` + P2 自动上限）· 回归
  `tests/worker/test_engine_pool.py`（3 例）· `tests/trainer/test_loop_park.py`（P0 1 例）·
  `tests/trainer/test_serve_wiring.py`（P1 1 例 + P2 1 例）· 调整 `tests/trainer/test_serve_bc.py`（1 例触发方式）。

**指针**：实况账本 `tmp/h4-hurt-f75/training_log.jsonl` · 相关 plan `plan/course-pill-precision.plan.md`
（同源事故的展示面：pill 的 `已收官` 与节点「贡献 0」另有独立缺陷，另行处理）。

## §2026-10-02-nodes-lastcontrib-winner-must-have-completed-round（2026-10-02，节点「贡献 0 / 离线」误报的根治）

- **症状**：控制台节点 pill 把 `self` 显示成「贡献 0 / 离线」，而它是当天贡献最高的节点
  （窗口内 rollout 9644 + eval 1950 局）；表格展开行的「最近完成轮贡献」也是 0。
- **根因**：`lastContrib` 取自跨流的「最新完成轮」winner，而 winner 的完成时刻写作
  `completed?.atMs ?? src.mtimeMs`（`dashboard/src/server/pool-history.ts`）——**一轮都没跑完**
  的流（没有 `iteration` 事件）也会带着 meta 文件的 mtime 进场，而它常常是最新写入的那个 ⇒
  抢走 winner ⇒ `contribAtBase` 落在它那条**没跑完**的轮上 ⇒ 没参与那轮的节点全被算成 0 局。
  数字对得上：线上 `mac=130 / a95=38` 正是崩溃循环流 `h4-hurt-f75` 那条 it1 的逐节点计数。
  0 局 → `nodeHealth(0, 并发)` = `offline` → pill 报「离线」。
- **决定**：winner **只在真跑完过至少一轮的流**（`completedIt !== null`）里选；全部流都没跑完
  ⇒ `latestRound = null` + `lastContrib` 空表（消费方得 -1 = 「无池数据」），**不挑一个充数**。
  实现形状：`FlowState` 增 `completedIt`（该流账本最后一个 `iteration` 的 it），判据只用它。
- **被否决**：改 `nodeHealth`（不再用「最近完成轮贡献」判离线）—— `console-types.ts` 记着
  2026-09-20 **用户指令**「健康度只由最近完成轮的贡献数判定，与 ping 探测无关」⇒ 那是刻意的
  语义，不是洞。故「该轮确实交了 0 局 ⇒ 仍判离线」这条**不动**（它现在是诚实的 0，而不是被
  别的流污染的 0）；若将来要改成窗口口径，属新指令。
- **落点**：`dashboard/src/server/pool-history.ts`（`FlowState.completedIt` + winner 资格）
  · 类型文档 `web/view/{console-types,pool-types}.ts` · 回归
  `dashboard/tests/server-pool-history.test.ts`（+2 例：崩溃流不得抢 winner /全员未完成 ⇒ 无完成轮）。

**指针**：事故现场 `tmp/h4-hurt-f75/`（含其崩溃循环的真因）· 训练侧修复
`DECISIONS.md §2026-10-02-goalnn-engine-pool-midround-pin` · 课程 pill 的状态精确化
`plan/course-pill-precision.plan.md`（不同面：那个是**课程** pill）。

## §2026-10-02-goalnn-burn-rule-in-course-file（2026-10-02，止损块进课程文件：`kickstart_burn`/`paired_kill` 从 rl-config 迁入 `curricula/*.jsonc`，块不进 `corpus_fp`）

- **背景**：结果面止损（干烧熔断 / 配对中点杀臂）的参照物与阈值住 `rl-config.json`（机器本地、
  不进 git）——换机器/新克隆静默回落 `baseline`（回测假阳性 49.3% vs paired 0.47%，~105×）；
  kickstart 家族被劈成两半（ref/init 在课程、burn 在 rl-config）。旧注释的否决理由「课程文件
  参与 course_fp 血缘（D14）」**把 `course_fp` 当成了 `corpus_fp`**：D14 判 `corpus_fp`，止损是判据不是语料。
- **决定**：`biz/course_spec.py` 加 `KickstartBurnBlock`/`PairedKillBlock`（解析期强校验，脏值拒课）；
  5 个读函数改「课程块优先 + `legacy_*` 兼容回落」；`RESTART_ONLY_FIELDS` 加两键（不进 `HOT_FIELDS`）；
  `corpus_identity_fp` payload 不加两键；判据本体 `burn_verdict`/`paired_kill_verdict` **逐字不动**；
  控制台 `burnThresholds` 同源（课程块 > rl-config > 常量）。h4-aim-{k10,k25} 落块、h4-aim-c0 写
  `paired_kill:{enabled:false}`；h4-hurt/h4-encl 四臂与 x20-clutch 两门暂留 rl-config 兼容回落（第二刀迁）。
- **被否决**：① 直接删 rl-config 读面（迁移期弄丢现有两条腿的止损，必须双读兜底）；
  ② 块进 `HOT_FIELDS`（判读窗口中途换口径 = 换实验）。
- **落点**：`nn-training/biz/{course_spec,hot_reload,corpus_fp}.py` ·
  `worker/{kickstart_burn,paired_kill,loop_guards_leg}.py` · `curricula/h4-aim-{c0,k10,k25}.jsonc` ·
  `dashboard/src/stack/kickstart-receipt.ts` · 测试 `tests/biz/test_h4_aim_courses.py`（新建）等。

**指针**：全文 `docs/nn/engineering.md` §59 · plan `plan/burn-rule-in-course-file.plan.md`
（含评审 `-review-bf.md`）· 回测 `nn-training/tools/backtest-burn-rule.py`。

## §2026-10-02-goalnn-burn-rule-cut2（2026-10-02，止损块第二刀：删 rl-config 回落读面 + 六处迁移 + 存量清理）

- **背景**：`§2026-10-02-goalnn-burn-rule-in-course-file` 第一刀留了双读兜底（课程块优先 + `legacy_*`
  回落 rl-config）。六个使用者（h4-hurt×2 + h4-encl×2 + x20-clutch 两门；h4-aim×2 已于第一刀迁）
  现已全部收官且都有课程块 ⇒ 回落读面已无活用户；留一把「机器本地还能改止损」的回落口 = 预注册
  的止损仍可能被本地值盖掉（块优先也挡不住旧值漂移误导读面）。
- **决定**：① 删 `legacy_burn_mode/overrides`、`legacy_paired_kill_overrides/self_kill/enabled`
  六个回落读函数；`burn_mode/burn_overrides`/`paired_kill_*` 五函数去 `fallback` 参数（块缺席 =
  模块常量）；`loop_guards_leg` 两个守卫去 `dist_cfg` 参数（`course_key_of` 唯一用处消失）。
  判据本体 `burn_verdict`/`paired_kill_verdict` 逐字不动。② 迁移六处块（值逐字复制）：h4-hurt-{f75,f150}
  → `{mode:paired,peer:h4-hurt-c0}`、h4-encl-{f75,f150} → `{mode:paired,peer:h4-encl-c0}`、
  x20-clutch → `paired_kill:{enabled:true}`、x20-clutch-null → `paired_kill:{self_kill:false}`；
  六处头注「见 rl-config」改指本文件块。③ 控制台 `burnThresholds(course)` 去 `cfg`、`CourseConf`
  删字段；两键进 `LEGACY_COURSE_KEYS`。④ 存量清理：新 `pruneNoiseCourses()`（停课清理同一条分档
  扫全库：纯 `rollout_src:'local'` 删 / 空壳整条删 / 无读者旧键删；显式 node/auto/run 不动；幂等
  无变化不写盘），2026-10-02 实跑 92 项（48 → 4 门，只剩显式 run 档；备份
  `rl-config.json.bak.20261002-224412`）。
- **门槛③如实记录**：plan §9 P1 的「≥1 次会话在无 rl-config 键的环境跑满并确认止损生效」**未即时
  满足**——六个使用者已全部收官、当前无在跑的处理臂；删除安全性由「无生产读者 + 课程块齐全 +
  块缺席回模块常量（与旧回落同值）」承担，③降级为后续第一条带止损的新腿自然验证。
- **被否决**：① 保留 `fallback` 参数只删 `legacy_*`（留一把空口子 = 第二事实源迟早回来）；② 先不迁
  x20-clutch 两门（已收官；块进课程文件是归档/复现的预注册，不是只为在跑的腿）。
- **落点**：`nn-training/worker/{kickstart_burn,paired_kill,loop_guards_leg}.py` ·
  `trainer/loop_round_steps.py` · `curricula/h4-hurt-*`/`h4-encl-*`/`x20-clutch*` ·
  `dashboard/src/stack/{course-knobs,kickstart-receipt}.ts` · `dashboard/src/core/types.ts` ·
  测试 `tests/biz/test_burn_blocks_in_courses.py`（新建）等。

**指针**：全文 `docs/nn/engineering.md` §60（前刀 §59）· plan `plan/burn-rule-in-course-file.plan.md` §9 P1。

## §2026-10-03-goalnn-auto-offline-handoff（2026-10-03，离线盘 claim 即接管：一拖一闸 + 派发状态落盘 + 三态意图表）

- **背景**：Kaggle TPU 排队数小时，开课时无法预判「该不该离线」；「开课时指定离线/在线」只能事后手切，
  而离线课程在 TPU 上优势极大。用户 2026-10-02 口径 U1-U6（plan/auto-offline-handoff，评审两轮 bf/hy）。
- **决定**：① 触发点在 **claim**（不是「盘上线」）：清单一律对未 pin 的在训课开放且**允许无包**；
  claim 无包 = 翻 mode + 控制台反向调用（`/api/autoOfflineHandoff`）导包 + 409；云机侧
  `claim_course` 返回值升级为 `(token, reason)`，中间态（`busy`/`pending_export`/`completed`/
  `not_offline`）本拍不跑、不耗 idle 预算，到上界就本会话放弃。② U2「一拖一」做成 **hub 侧不变量**
  （活租约 ∪ 交接窗口都占闸；release 后自动解除）。③ 派发状态（mode/pin/claim 记账/完成锚）落
  `<课>/offline-dispatch.json`，重启**优先于启动参数**（数据损坏防线）。④ 控制台意图表**三态**
  （unset/online/offline）：开课未选模式不推不落意图；人的开关 = `pin=1`；新增「交还自动」
  （`mode=online&pin=0` + 删意图）。⑤ `eval_on_cloud` 默认 true（ipynb + runtime 两处同值）。
  ⑥ 完成态锚在**包 sha**（重导即自动解封，不新增状态机）。
- **被否决**：控制台每轮常驻导包（包体积大 + `plan_sha` 每轮变 = 每次都在制造 404 窗口）；TS 侧写
  `run_complete`（python 是账本唯一写者，TS 复刻 = 第二个实现源）；「人工切回在线 = pin online」
  （等于该课永久退出自动逻辑，与 U1「TPU 一上线就能接续」冲突）；检测到盘上线即全切（会收走全部本机
  采样，且 `recent_n` 在长段期间衰减 ⇒ 双跑）；把自动逻辑放进 hub 调度（`offline_tasks` 仍是零副作用读面）。
- **落点**：`nn-training/hub/{queue,queue_scope,queue_offline,queue_peer,offline,admin,task_pack}.py` ·
  `nn-training/remote/offline_boot.py` · `nn-training/ipynb/battle.offline.ipynb` ·
  `dashboard/src/server/actions/{course-mode,console-state,course-lifecycle}.ts` ·
  `dashboard/src/server/api/route.ts` · `dashboard/src/stack/hub-admin.ts` · 测试
  `nn-training/tests/hub/test_auto_handoff.py`（新建，22 例）· `tests/common/test_offline_task_queue.py` ·
  `tests/remote/test_offline_notebook.py` · `tests/remote/test_offline_eval_wiring.py` ·
  `dashboard/tests/{course-mode,course-lifecycle}.test.ts`。
- **门槛如实**：T0（`switch-mode-drops-jobs` 撤单）/ T6（跑满灰横幅走导入链）/ T3 的面板接线（「交还
  自动」按钮）/ T8 的控制台告警渲染 **未落地**；plan 文档（未跟踪）已按 2026-10-03 实况标注。
- **指针**：全文 `docs/nn/remote-transport.md` §56 · plan `plan/auto-offline-handoff.plan.md`（§7a 评审处置）。

## §2026-10-03-goalnn-auto-offline-handoff-t6（2026-10-03，T6 完成态链 + T3 面板钮 + T8 告警渲染落地）

- **背景**：`§2026-10-03-goalnn-auto-offline-handoff` 门槛如实记了四处未落地；本轮除 T0 外全部落地。
- **决定**：
  ① **T6 判决在控制台、落账在 python**：`end_it_reached` 由云机自报（`plan_run._end_it_reached`：
     `complete`/`noop` ∧ `it_end >= plan.end_it`；`max_iters` 截断不算）；hub 字段白名单 + 联动
     `note_offline_completed`（该包 completed ⇒ 不可再领，重导 sha 变自动解封——此前它是死代码）；
     `/admin/offline.results[课][run_id]` 按 run 带出；控制台在导入那一步按「run_id 对齐 ∧
     `end_it_reached` ∧ **末轮号一致**」转交 python **第二趟** `deliver_zip --end-it-reached --run-id …`
     （不重新解包）落 `run_complete` ⇒ 现成灰横幅读面直接亮。**半段导入不亮横幅**；TS 侧无账本写入。
  ② **T3 面板**：矩阵 / 课程管理离线行加「交还自动池」钮（`unsetCourseMode`），与「切换成在线」
     （`pin=1` 永久固定）**文案分开**。
  ③ **T8 控制台**：告警坞渲染 `/admin/offline.stalled`（`pending-export` 红 / `running-stale` 橙），
     文案点名三条出路并自带「交还自动池」动作；hub 不可达/旧版 = `null`（不可知 ≠ 没停）。
- **被否决**：① 一趟导入里转交（run_id 在解包前不可知 ⇒ 只能两趟）；② TS 直接写 `run_complete`
  账本（python 是唯一写者；`server-api-task-bundle` 源码守卫钉住）；③ 转交只对 run_id + 跑满、
  不校末轮号（同一 run 的旧半包会被误亮横幅）；④ stalled 自动切回在线（U3 明令不许自动回退，
  只出告警 + 人来按）。
- **落点**：`nn-training/{hub/{store_offline,queue_resume,offline,admin}.py, remote/{deliver_zip,offline_deliver,plan_handoff,plan_run}.py}` ·
  `dashboard/src/{server/{api/overview,bundles/import},stack/hub-admin,web/{view/{course-overview,alerts},app/{app.tsx,panels/{CourseMatrix,CourseAdmin}}.tsx}}` ·
  回归 `nn-training/tests/{hub/test_auto_handoff.py（24 例）,remote/{test_deliver_zip,test_offline_deliver,test_offline_deliver_async}.py}` ·
  `dashboard/tests/{server-api-task-bundle,web-alert-dock,web-app-coursematrix,train-mode-offline}.test.ts`。
- **门槛**：`bun run check` 绿（2357 pass/3 skip）· nn python gate 绿（ruff+mypy+pytest 3568 pass/7 skip）·
  dashboard 全量 1303 pass。**仍未落地：T0**（`switch-mode-drops-jobs` 撤单；T0a 落点重定位已确认）。
- **指针**：全文 `docs/nn/remote-transport.md` §56（2026-10-03 补段）· plan `plan/auto-offline-handoff.plan.md`。

## §2026-10-03-goalnn-switch-mode-drops-jobs（2026-10-03，切模式撤单落地：复用 `job_cancelled` 账本事件 + 只撤没被领的）

- **背景**：`plan/auto-offline-handoff` 的 T0（唯一未落地项）= 落地 `plan/switch-mode-drops-jobs`：
  切「在线/离线」= 上一个模式留在队列里的 job 该作废。原始触发是 2026-09-25 报障「x20-dodge-l1 离线
  跑到 it43，切成在线后云机仍在节点跑 rollout」（离线时期发布的整段 job 躺队列里被补做）。
- **决定**：
  ① **落点重定位（T0a 的实质结论）**：原 plan §3 改点 2 钉 `remote/plan_run.py::run_plan_job`，但它
     **今天零生产调用者**——`kind=run`（整段 job）2026-09-25 双端退役（发布端 `loop_remote_job.py`
     当场 `SystemExit` / worker 端 `worker.py` 响亮 `ProtocolError`），`tests/remote/test_plan_run_split.py`
     有机器断言「worker 里不得再出现这个名字」。⇒ 按原文落点做 = 给死代码加功能，改走**既有**
     `job_cancelled` 账本事件（读面 `claimable_job_ids` 早已把它当终态剔除；写面此前只有训练侧的
     `remote/hub_client.py::cancel_stale_jobs`，按 `it <= 当前` 清自己的滞后项，管不到模式翻转）。
  ② **只管没领的**（用户 2026-10-03 裁决，逐字：「不管在算的，只管没领的」）：
     `_JobStore.cancel_unsettled_jobs()` 只对 `claimable_job_ids()` 里**未被认领**的 job 写
     `job_cancelled` + `_drop_commitment_locked`（撕承诺 + bump epoch ⇒ 拿作废前 epoch 来的认领被
     判 `demoted`）+ 进程内 `_cancelled` set（挡「拿作废前 peek 到的 jid 硬领」的秒级窗口）；
     有活租约 / 在 `_computing` 的**不撤**——撤销在飞 job 属于轮边界强杀，本 plan 不做。
  ③ **claim 自动翻模式也撤**（裁决 Q2）：`begin_auto_handoff`（无包腿）/ `note_claim`（有包腿）与人的
     开关共用同一条切模式链，不只挂在 `&drop_jobs=1` 上（`auto-offline-handoff` §4 的口径）。
  ④ **只有人的动作带参数**：`POST /admin/courses?...&pin=1&drop_jobs=1`；控制台只有那颗开关带它
     （`setCourseMode` → `pushCourseMode(..., { dropJobs: true })`）。**开课/停课/回灌一律不带** ⇒
     「停课队列与账本一个字不动」的既有契约（`course-lifecycle.ts`）逐字不变。
  ⑤ 撤单失败**不回滚** mode（那会做成「切了但没切」），只响亮记一笔；残留由可领池按账本重算兜。
- **被否决**：① 按原 plan 在 `run_plan_job` 里加 `/jobs/state` 查询（死代码，加了不生效）；
  ② 新建内存 `_dropped` 表（原 plan 裁决 4 的口径）——账本 `job_cancelled` 读面已存在且落盘持久，
  新建表 = 第二个事实源；③ 撤销在飞 job（轮边界强杀，本轮不做）；④ `GET /jobs/{id}/status` 加
  `dropped` 位 + 云机取消监视器（= 「在飞也撤」方案的一半，随 ③ 一并搁置；既有 cancel 通道
  `remote/job_lifecycle.py::start_cancel_watcher` 只认 `landed`，未动）。
- **落点**：`nn-training/hub/{store_ledger,store_leases,queue_offline,queue_peer,admin}.py` ·
  `dashboard/src/{stack/hub-admin.ts,server/actions/course-mode.ts}` ·
  回归 `nn-training/tests/hub/test_auto_handoff.py`（+4 例：撤未认领 / 在飞不撤 / 不带参数则队列不动 /
  claim 自动翻模式也撤 / 作废两道闸）· `dashboard/tests/course-mode.test.ts`（+1 例）·
  **端到端** `nn-training/e2e/test_auto_handoff_e2e.py`（新，5 例：全链 / U2 一拖一 / T8 stalled /
  T0 撤单 / 云机腿中间态；真 hub 子进程 + 真 HTTP + 假控制台）。
- **门槛**：`bash tools/githook/nn-python-gate.sh` 绿 + `cd dashboard && bun run typecheck && bun run test` 绿
  （按本仓纪律由用户终端复跑）。
- **指针**：全文 `docs/nn/remote-transport.md` §57 · plan `plan/switch-mode-drops-jobs.plan.md`（未跟踪）
  · `plan/auto-offline-handoff.plan.md` §4 T0。

## §2026-10-03-goalnn-gate-parallelism-physical-cores（2026-10-03，门禁/本地 worker 池并行度改按**物理核数**：新增 `physical_cores()` 第二口径）

- **背景（事故）**：`bun test` 裸 `--parallel` 的缺省 = **逻辑核**（本机 8c/16t ⇒ 16 worker），
  实测把 Windows 的提交上限顶穿（物理 28.9G + 固定分页 14G = 42.9G，空闲提交仅 5.3G）⇒
  `RegisterWaitForSingleObject 1455 paging file is too small` ⇒ worker `0x80000003` 崩 + 兄弟
  worker 连环 abort ⇒ 一次提交里 32 个"失败"**全是崩出来的假红**（同套用例 4 worker 全绿）。
  用户裁决：**所有门禁的并发数 = 所在机器的物理核数**。
- **决定**：
  ① **两个口径按消费域分工**（每一域仍只有一个答案）：`effective_cores()` 留作**训练运行时**
     （rollout/eval 槽位、`biz/cli.py`、notebook、`sampler-agent`、torch 线程数）；**新增**
     `physical_cores()` = **min(真物理核, cgroup 配额, 亲和掩码)**，都读不到才回落 `os.cpu_count()`
     —— 给**门禁与本地 worker 池**。
  ② **落点铺满所有门禁入口**：`nn-python-gate.sh`（`NPROC`）· `tools/task.py::pytest_dispatch()` /
     `Makefile`（**传显式数字**，不再 `auto` —— Windows 的 `-n auto` 是 xdist 自己按逻辑核解析的，
     我们看不见）· `forkdist --forkdist auto` · 根 `bun run check` / `bun run test`（新增 launcher
     `tools/run-root-tests.ts`，JSON 脚本串做不了算术；`test-silent.ts` 显式传数）· dashboard
     （新增 `dashboard/scripts/run-tests.ts`）· **CI**（新增 `nn-training/tools/nproc.py` 现探，
     替掉写死的 `-n 2`）· `worker-pool::defaultWorkerCount` / `gateCoreCount`。
  ③ **探测零新依赖**（venv 无 psutil）：Windows ctypes `GetLogicalProcessorInformationEx
     (RelationProcessorCore)`（本机实测 8，比 spawn 快一个数量级）/ macOS `sysctl hw.physicalcpu` /
     Linux `/proc/cpuinfo` 的 `(physical id, core id)` 唯一对；TS 侧镜像（Windows 走 **pwsh** CIM，
     实测 ~1.5s ⇒ 进程内记忆化）。
  ④ **上界 `min(核数, 12)` → `min(核数, 32)`**（只给大机器兜内存）。
  ⑤ **顺手修** `worker-pool::physicalCores` —— 它名叫物理核、实际返回**逻辑核**（`min(cpus().length,
     effectiveCores())`，本机 16）；合并进 `cores.ts` 唯一实现，`perf-cmp-rollout` 的内联副本一并下沉
     （顺带修掉那份里的裸 `powershell`，AGENTS §17.7），`worker-pool` 只 re-export 保下游 import 不变。
- **被否决**：① 保持逻辑核/`auto`（事故已证明会假红）；② 把 `effective_cores()` 直接改物理核
  （会连**训练运行时**的 rollout/eval 槽位一起改——那是另一个消费域，不在本次范围）；③ 猜
  `逻辑核 ÷ 2` 当物理核（无 HT 的机器上直接错一半）；④ `wmic`（新 Windows 已移除，实测 `not
  recognized`）；⑤ 去掉 `NPROC` 上界（大机器按核数放大内存有 OOM 风险）。
- **已知灰区（写进两边 docstring，不修）**：cpuset 恰好只给 HT sibling 时 `min` 会高估物理数；
  精确做法要把 affinity 的 cpu id 与 `/sys/.../topology/{core_id,physical_package_id}` 求交。
- **落点**：`nn-training/common/platform_utils.py` · `nn-training/tools/{forkdist,task,nproc}.py` · `nn-training/Makefile` ·
  `tools/{lib/cores.ts,lib/worker-pool.ts,test-silent.ts,run-root-tests.ts,sim/perf-cmp-rollout.ts,githook/nn-python-gate.sh}` ·
  `dashboard/{scripts/run-tests.ts,package.json,tsconfig.json,tests/gate-composition.test.ts}` · `tests/{effective-cores,worker-pool-defaults}.test.ts` ·
  `nn-training/tests/{common/test_platform_utils_cores.py,tools/{test_forkdist,test_nproc}.py,test_githook_scripts.py}` ·
  `.github/workflows/nn-training.yml` · `package.json` · 文档 `docs/nn/runtime-opt.md` §23.6。
- **门槛**：nn python gate 绿（**3586 pass/7 skip，8 worker，52s**）· 根套件 `bun run check` 绿
  （**2365 pass/0 fail，banner `8x PARALLEL`**，62.6s，含 sim 打分门禁）· dashboard 绿（**1305 pass/0 fail，19.5s**）。
- **实测（本机 8c/16t）**：**sim 打分门禁** 8 worker **min 23.1s** vs 4 worker min 27.9s（旧注释里「4 最优」在这台
  机器上已不成立——那份实测是 700 局 classic 单模块场景，整门 2100 局下 8 更快）。**未测（如实记）**：
  `defaultWorkerCount` 15→7 之后 sim 批处理工具（`m1-eval`/`export-*`）的吞吐——本工作区 `nn-training/weights` 为空，跑不起来。
- **指针**：全文 `docs/nn/runtime-opt.md` §23.6 · 计划 `.trae/documents/gate-parallelism-physical-cores.md`。

## §2026-10-03-offline-pack-truncation-and-ref-blob（2026-10-03，用户报障×2：半截任务包被当成功 / 离线包缺 kickstart ref 字节）

- **背景（事故一）**：Kaggle 取包 `task-pack` 声明 `Content-Length=4322911`，隧道在 3670016
  （**14×256KB**）处干净 FIN，而引导期护栏记 `attempts=1 rerolls=0`（= 成功）⇒ 半截 zip 落盘
  → `read_pack_index` 才炸 `BadZipFile: File is not a zip file`（未捕获 ValueError ⇒ 整场多课程
  会话陪葬、后两门课未跑；重跑一次 cell 就好）。机制：**`http.client` 对 `read(amt)` 的提前 EOF
  故意不抛 IncompleteRead**（兼容）⇒ 旧 `_read_body` 只看 EOF、不看「已收 == 声明长度」。
- **背景（事故二）**：x20-adv-hurt（`kickstart_ref: true`，kk=0.1）云机 it1 的 PPO 装载失败：
  `/jobs/…/blob?name=ref` 重试 3 次仍 `ValueError: unknown url type`（相对路径 ⇒ 空 base_url）。
  发布 slim 后 manifest 只剩 `ref_sha`（`blob.ref` 379114B 就在 job 目录，sha 逐字相符），而
  `export_bundle` 带 code/ts/init/opt/demo **唯独不带 ref**；离线段逐轮 job 由
  `plan_run._run_iteration` 本地合成（`run_job("", "", …)`；hub 上没有这份 job）⇒ 内容寻址
  未命中 = 向空 base_url 发 GET，重试只是在刷同一条必然失败。
- **决定**：① 两处孪生 `_read_body`（`remote/tailscale_boot.py`、`remote/http.py`）在 EOF 处
  比对「已收 vs Content-Length」，不足即按传输失败抛（引导期 `BootBodyError` 进同一次 fetch 的
  内层换连接重试；worker 侧带正文 `TimeoutError` 进退避重试）。② ref 与 demo 同规进离线链：
  `export_bundle` 带 `ref_weights.json`（`OPTIONAL_PARTS` + README + `blob.ref` sha 对账），
  新增 `plan_handoff._seed_ref_blob_cache`（启动期种子进 `blob_cache/<ref_sha>`，候选 = 产物目录
  `ref_weights.json` → job 目录 `blob.ref`；缺件**启动期响亮拒收**、一轮都不跑），`plan_run` /
  `run_loop` 门面与分片表 18→19 同步。
- **被否决**：① 离线段给 `run_job` 传 hub_url 让 ref 联网下载——run 模式 hub 上没有这份合成
  job（下载通道不存在），且违反「全离线不依赖网络」；② 只在 `read_pack_index` 前加 zip 校验
  兜住——语义上「传了半截」是传输失败，该在传输层重试，且 worker 侧下载的 payload/code/blob
  同病（孪生）；③ 给 export 改回 `slim=False` 内联 ref——放弃 opt-blob-diet 的内容寻址账且包更大。
- **运维口径**：已导出的旧包（没有 ref 件）会被收紧后的启动期门**直接拒收**；x20-adv 三腿上
  云机前先在控制台**重导任务包**（job 目录 `blob.ref` 还在，重导即自动带上）。
- **门槛**：nn python gate 绿（**3592 pass/7 skip，54s**，含 e2e；对照上一条 3586 → +6 = 本次新增用例）。
- **指针**：全文 `docs/nn/remote-transport.md` §58。
## §2026-10-03-goalnn-dashboard-alert-dock-global（2026-10-03，告警坞全局化：收官横幅按课成列 + 七类条目全可关可复制）

- **背景**：用户 2026-10-02 两条指令（收官横幅要全局可见 / 所有横幅可关闭可复制）。改前收官横幅读面是
  单课单值（`StateView.loopComplete` + 按课程键控的 `computeSlowSnapshot`）⇒ 不切课看不到「已经跑完了」；
  七类条目里 4 类没有 ack、7 类全没有复制。
- **备选与否决**：① 停机 ack 换到新键空间（新 kind + 新表）——否：无收益的净回归（升级后重弹红条），
  且要双表并读 + 迁移；② 保留 `loopComplete` 单值做兼容——否：第二份真相（server/web 同批构建无版本错位）；
  ③ 聚合塞进按课程键控的慢快照——否：全课聚合会被按「请求课程」各缓存一份，切课即重算、并发各算一遍；
  ④ 聚合遍历全部已知课程（含历史课）——否：几十门历史课账本在盘上，逐拍全扫是浪费（同 harvest 闸）；
  ⑤ 给收官条造「立即恢复」——否：第二份真相（N6）。
- **决定**：收官横幅读面扩为 `loopCompletes: Record<course, LoopComplete>`，聚合落 `state-view.ts`，判活用
  共享 `trainingLoop` 的全局存活事实，课程清单 = 已开课 ∪ 查看课，条目按课成列、标题带课名、按课名排序；
  新 `alertAckKey(kind, subject, eventId)` 统一事件身份键（`cloudHaltAckKey` 委托它 ⇒ 停机键逐字节不变），
  存储仍用同一张表（常量改名 `TC_ALERT_ACKS`、值冻结 `tc.cloudHalt.ack`）；`AlertItem.copyText` 由
  `withCopy()` 统一派生（三行纯文本），`AlertDock` 用现成 `CopyButton`（icon）与动作区分区。
- **违反后果**：改 `visibleCloudHalts` 的「只弹本课」= 重演 2026-09-14 事故；换停机键 kind = 用户重关一次红条；
  开第二张 ack 表 = 双读双写 + 迁移；给 `alerts.ts` 引进 IO/localStorage = SSR/水合红线（它必须仍是纯函数）。
- **指针**：全文 `docs/nn/console.md` §24 · plan `plan/dashboard-banner-global.plan.md`（评审修订版，§11 处置表）。

## §2026-10-03-goalnn-request-path-zero-aggregate（2026-10-03，控制台重载 ~10s + 常驻 1GB：请求路径零聚合 + 聚合增量入账）

- **背景**：用户 2026-10-03 报「dashboard 重载 ~10s + 进程占 1GB」。实测：`/api/state` 40 连击
  17/40 落 0.3–2.7s、WS 涨到 1.38GB、空载 CPU ~30% 单核；`aggregateNodeHistory()` 冷算 1057ms /
  +116MB、命中 memo 仍 37ms（walk 在 memo 前）、`listCourseLedgers()` 9.9ms/次无 memo。
- **备选与否决**：① 只降 `LARGE_META_BYTES`（少读几份大文件）——否：**丢历史窗口**（把内存换成错数，
  与「全部根治」口径冲突；本 plan 用增量而非截断换性能）；② 新增一个 `createSwrCache<ContributionView>`——
  否：WC-plan §1.3 明禁的**第二个缓存层**，且会与 `fleetProbeCache` 各算一份 `agg`；③ 后台刷新器为缩略
  的 inflight 多 await 一次 hub 探测（1.2s）——否：为不显示的数字付超时预算、拖慢整拍；④ 给 G7 开
  「纯在飞 worker 可缺失」的例外——否：那是把不一致写成规格；⑤ 跨流同毫秒平局沿用 mtime 序——否：
  增量下不可复现（旧流 mtime 会被新写入推走），canonical 目录名升序才是「增量/全量相等」的必要条件；
  ⑥ truncated 流也做增量累计——否：与「只读尾部」两条口径打架，28.1/15.6MB 两条流数字必变。
- **决定**：三条一起做（缺一条只是把症状搬位置）——
  **R1** 贡献度缩略挂 `FleetProbes.contributionBrief`（`computeFleetProbes` 后台顺手产出；
  `inflight` 走 `peekHubAdmin()` 的上一拍值），`state-view.ts` 只读缓存（请求路径零聚合）；
  **R2** `aggregateNodeHistory` 改「扫描 memo（同 `AGG_MEMO_MIN_MS`）+ 每流增量入账（`size:mtime`
  指纹；未变零读、变大读 `[offset,size)` 分块游标、回退/重写/水位后退全量重建）+ 可合并桶归并
  （每流尾窗口截尾 + `(ts, seq)` 归并再截尾；`>=` 决胜同规）+ 水位 `pending` 翻转」；
  **R3** `listCourseLedgers` 消费 `scanPoolStreams()`（一次 walk 两组候选），PPO memo 判定前置。
- **违反后果**：把聚合放回请求路径 = 每请求 47ms 同步扫描 + 每 30s 1s 级全停（本事故重演）；用
  `split` 全文读 = 13 万行数组 + 116MB 峰值（JSC 堆高水位不归还）；反向扣减水位 = 双计/漏计；
  两个消费者各扫一遍 = R1 的病根同构；truncated 流做增量 = 贡献度数字与面板不一致。
- **落点**：`dashboard/src/server/{pool-history,contribution}.ts` ·
  `dashboard/src/server/api/{snapshot-cache,state-view,overview}.ts` · `dashboard/src/server/server.ts`（注释）·
  `dashboard/tools/perf-probe.ts`（新）· 回归 `dashboard/tests/{server-pool-history,server-api-state-view,worker-contribution}.test.ts`。
- **指针**：全文 `docs/nn/console.md` §25 · plan `plan/dashboard-reload-perf.plan.md`（评审修订版 A1–A6）。
