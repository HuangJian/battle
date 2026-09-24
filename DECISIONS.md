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
  修复（下划线→连字符，`tests/test_rl_cmd.py` 锁死口径）。bug 自 `e828331`（2026-09-02 22:55，
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
  由 `nn-training/tests/test_ladder_factory.py` 钉死。
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
- **落地**：`nn-training/rl/eval_local.py`（纯函数/常量：`eval_join_soft_sec`/`eval_local_early_epochs`/`local_gate_release_plan`/`early_epoch_reached`/`eval_tail_overran`）、`nn-training/rl/loop_steps.py`（`_eval_policy_cfg`/`_eval_join_soft_sec`/`_regate_local_eval`/`_local_gate_epoch_hook`/`_sweep_eval_tail`，`_join_eval` 不站等+交棒、`_dispatch_delayed_eval` 边界收拢+分档放行、`_serial_ppo` 降级收回 + 注入 epoch 钩子）、`nn-training/rl/loop_core.py`（`_eval_gate_early_released`/`_eval_tail`/`_eval_tail_start` 初值）；回归：`nn-training/tests/test_eval_timing.py`（16 例，含“缺省零 join”与“边界收拢”）、`nn-training/e2e/test_run_rl.py`（`eval_deferred`/`eval_post_ppo_weights`/`eval_local_gate`/`tail_join_grace`/`early_race`）。
## §2026-09-17-goalnn-race-broadcast（2026-09-17，用户指令：只有一个课程在训练 + 多个云机 GPU worker ⇒ 竞速广播，最新 job 派给每个 worker，先回传者胜、后到者丢弃） _(superseded by §2026-09-22-goalnn-race-retired-priority-only)_

- **背景**：`GET /jobs/next` 现为 P3b 独占加超时（领取即设 300s 租约）⇒ 单课程多卡时只有一张卡在干活，其余卡空转。竞速广播是 §343（2026-09-06）的原语义，被 §2026-09-12 P3b 以「多课程并行时同 job 被重复算、慢者 409 白烧」为由 supersede；用户给出的前提（**单一课程** + 多卡）恰好消掉那个顾虑（那些卡本来就在同一个 hub 上空转）。
- **备选与否决**：“现在跑了几门课”由全局课程表/控制台判定 —— 否，没人能可靠回答（hub 每课一进程，看不见别课；worker 可能同时轮询多个 hub；运维还得记得关）；只在 worker 侧自选（“我只服务一个 hub 所以我去竞速”）—— 否，会把独占租约与竞速副本混在一起（同一 job 一半独占一半广播）；不做判据、直接恒开（回到 §343）—— 否，多课程机群上重复烧卡正是 P3b 修复的回归；广播**所有**在池 job —— 否，陈旧 job 上堆满 worker 是纯浪费（只广播最新那份）。
- **决定**：① **判据 = worker 自报的事实**：`X-Worker-Id`（身份）+ `X-Hub-Scope`（它 `--poll` 几个 hub），hub 保留 180s 窗口内的登记表，`race_decision(mode, workers, now)`（纯函数，`remote/protocol.py`）在 auto 下要求「≥2 个**不同** worker 且全部 scope==1」——多 hub/未知 scope 任一出现即退回独占（P3b 语义不被破坏），单 worker 不广播（只它一个执行者时广播无收益）；② 竞速轮：`claimable_job_ids(race=True)` 让**最新** job 忽略租约（更老的维持独占），`claim(race=True)` **不下租约并清掉先前那份独占租约**、返回空 token（worker 侧“无租约 ⇒ 不心跳”是 §343 既有分支，故无协议破坏）；③ 胜负由 `store_result` 首写锁定：赢家 200/201，后到者 **409**——worker 对 409 本来就按成功处理（“hub 已有同 job 结果”），不重试、不报失败；④ `_post_result` 必须在**租约校验之前**先判“结果已落盘”并 409（否则先独领、后转竞速的时序里，输家会因非持有人拿 **403**，而 worker 把 4xx 一律读成确定性拒绝并上报 job 失败——一个赢家把输家炸成事故→见 `test_race_releases_earlier_exclusive_lease`）；⑤ 应急闸：`rl.race_mode=auto|on|off` → `hub-server --race`，并可 `POST /admin/race?mode=...` 热切（volatile，切后清空登记），`GET /admin/race` 为观测面；`/admin/workers/status` 体形状**不动**（console 读它）。
- **违反后果**：把判据改成配置/人工声明 ⇒ 多课程并行时忘关 ⇒ 同 job 在多卡重复算（P3b 回归）；少了“广播时清旧租约 + 只认首写”两道 ⇒ 输家拿 403 被读成确定性失败，job 被钉成 fail.json 终局；广播所有在池 job ⇒ 陈旧 job 上堆卡空烧。
- **落地**：`nn-training/remote/protocol.py`（`RACE_MODE_*` / `HUB_SCOPE_HEADER` / `WORKER_ID_HEADER` / `RACE_WORKER_WINDOW_SEC` / `parse_hub_scope` / `race_decision`）、`nn-training/remote/hub_server.py`（worker 登记表 + `race_active`/`race_state`/`set_race_mode`/`clear_workers`，`claimable_job_ids(race)`/`claim(race)`，`/jobs/next` 接线 + RACE 日志行，`_post_result` 前置 409，`GET|POST /admin/race`，`--race`）、`nn-training/remote/worker.py`（上报身份/范围 + 竞速副本日志 + 409 措辞）、`dashboard/src/core/types.ts`（`RaceMode`/`normalizeRaceMode`）、`dashboard/src/stack/specs.ts`（`--race` 透传）；回归：`nn-training/tests/test_race_broadcast.py`（22 例）、`dashboard/tests/hub-server-race-arg.test.ts`（4 例）。后续（本轮明确不做）：赢家落账后叫停还在算的副本（省 N-1 份 GPU）。

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
- **落地**：`tests/subproc_util.py`（`free_port` / `spawn_bound_port` / `BoundServer` / `PORT_TAKEN_MARKER`）· 调用点 `e2e/test_multi_course_single_hub_e2e.py::_Hub` · `tests/test_multi_course_hub.py::test_main_discover_picks_up_course_from_disk`（两份裸 `_free_port` 与各自的 `_startup_output` 一并删除，诊断改由 `BoundServer.tail()` 出——进程活着也能安全取）· 回归 `tests/test_subproc_util.py`（6 例：真守卫文案对齐 · 撞端口换端口重试 · 非端口死法不重试 · 上限到顶响亮失败 · 超时兜底 + tail 可读 · **源码守卫**：两个调用点不得再出现裸取端口）。
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
