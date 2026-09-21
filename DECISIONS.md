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
> - NN 训练与意图策略 → `docs/nn.progress.md` / `docs/nn.progress.intent.md`
> - 功能 / 架构现状 → `docs/features.md` / `docs/architecture.md`
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
> ccf49ff^（瘦身前全文版，git 历史为准）。写法见 `docs/decisions/HOW-TO-ADD.md`；正确性由 `bun run check`
> 里的 `tools/check-decisions.ts` 强制（撞号/丢号/新条目格式）。

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

### RL 训练基础设施（§281–§284，全文 → docs/nn.progress.md）

## 281. RL 训练断点续跑机制（服务随时停启）(STATUS: 完成, 2026-08-23) —— 全文 → docs/nn.progress.md §4（RL 训练断点续跑机制）

## 282. RL 队列模式静默跳轮修复 — resumed_manifests 双 schema 归一 + 失败迭代原地重试 (STATUS: SHIPPED, 2026-08-24) —— 全文 → docs/nn.progress.md §5（队列模式静默跳轮修复 + 事故复盘）

## 283. 干净评估嵌入分布式流水线 — PPO 空窗期全节点贪心局（STATUS: SHIPPED, 2026-08-24） —— 全文 → docs/nn.progress.md §7（干净评估嵌入分布式流水线）

## 284. 分布式协议 v3.6 — 结果容器 BCV2 子进程打包 + 任务获取异步化（STATUS: SHIPPED, 2026-08-25） —— 全文 → docs/nn.progress.md 分布式 BCV2 节 + plan/distributed-rollout.md v3.6

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

## §332 p10-onset 奖励函数：p4 toy 公式 + 拾取项（2026-09-04，用户指令）
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
> **定案（用户拍板）**：冒烟预演走真课程路径（TrainingLoop + --smoke 真 job + 伪 Kaggle --echo 回显 + SmokeVoidRound 作废本轮），否决虚拟课程；硬门严格化（隧道/code.zip/self-rollout 任一失败 exit 1，--no-tunnel 逃生门）；补充：位置参数课程名 + 启动前课程快速失败；GPU↔HUB 通信分层重传加固（RetryableError 划界 / worker 下载 3 次退避 / post_result 5 次 / release 还租约 / 结果缓存复用）。实施记 docs/nn.progress.md §18。

## §341 /pool 页面热加载——pool-page.ts 改动免重启 agent（2026-09-06，用户指令）
> **定案**：pool-page.ts 改 mtime 键控动态 import 热加载（键=mtimeMs，文件版本只占一条模块记录；坏文件回退上版且页面 200）；
>   sampler-agent 本次接线=最后一次节点升级波；核心协议文件不纳入热切换。

## §342 / p4-onset 监控四修复（2026-09-06，监控发现 → 用户拍板"修全部问题"）
> **结论**：p4-onset 监控四修复——ppo_schedule lr 三段表远程模式全程未生效（同步折进 args.lr）；同 seed shard 双份落盘（dup-settle 退役输家目录 + iter_shard_dirs 同名去重）；贪心 eval 被 EVAL_SEEDS=20 截断（扩池）；课程 backup_prefix/dir 未被采用（优先课程声明）。监控教训：判断配置是否生效看 manifest 打包链路而非日志回显。

## §343 / PPO job 分发改竞速广播——废租约独占（2026-09-06，用户指令）
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

- **背景**：DECISIONS.md 膨胀至 3734 行、编号至 §384，并行分支撞号（§293/§354/§355/§361 各 2 条同号）、
  历次瘦身必输（写入成本 0、清理成本 100，三 agent 并行每月 +30 条）。实测定案为准入问题，非整理问题。
- **备选与否决**：更勤瘦身 —— 否，写入门槛为零时任何频率瘦身都是负收益；换编号格式（全局计数器加锁）—— 否，
  顺序整数是全局共享可变计数器，并发必然撞号；ADR 分片（一决策一文件，P4）—— 暂缓，瓶颈在准入而非文件组织，
  收紧准入后写入量掉 80%，单文件 append 够用。
- **决定**：收紧准入（三问闸门 + 三类路由，禁双写）+ 防冲突命名（日期 ID `§YYYY-MM-DD-<branch>-<slug>`，
  旧编号 §1–§384 冻结契约不动）+ union 合并（`.gitattributes`）+ 只追加（新条目只写末尾）+
  模板外置（`docs/decisions/HOW-TO-ADD.md`）+ 自动校验（`tools/check-decisions.ts` 接进 `bun run check`，
  基线 `tools/decisions-baseline.json`）+ 一次性瘦身（编号集合不变式：只删正文不删编号；
  实验/调优 → progress 指针，bugfix/UI → commit 承载）。本次迁移 3734 → 约 980 行，编号集合逐条核对未变。
- **违反后果**：撞号与膨胀回归（目标 ≤5 条/月 vs 现状 ~30）、合并冲突复现、外部引用断链。
- **留存**：旧全文在 ccf49ff^（瘦身前全文版，git 历史为准，不另存本机备份）。

## §2026-09-09-goalnn-console-lan-readonly（2026-09-09，用户指令：控制台改局域网只读）
> **定案（局域网只读 + localhost 控制）**：训练控制台绑 0.0.0.0（局域网可达），POST /api/* 动作仅回环来源可执行（server.requestIP → isLoopbackAddress，fail closed：无法判定来源 = 拒绝，403）；GET 全开但 ?course= 只读课程覆盖（sanitizeViewCourse：真实课程 + 防路径穿越，绝不写 console-state）；客户端 isLocalHost 判定：本机切课程 = 查看 + POST setCourse 同步操作员课程，局域网切课程 = 仅查看 + 写 URL（?course= 可分享/刷新保持）；课程敏感动作统一带 body.course（所见即所控）。备选与否决：LAN 直通 POST —— 否，杀进程/改配置暴露给整网；每会话独立课程 —— 否，LAN 多 tab 本就共享视图。违反后果：局域网可启停组件/改 rl-config、?course= 路径穿越读任意文件。
> **（2026-09-09 修订，用户指令）**：撤销数据脱敏——cloudflared token 行与复制键局域网照常展示（原「非回环 redactSecrets 剥 auth key」作废）；只读是动作边界，不是数据边界。

## §2026-09-10-goalnn-rleval（2026-09-10，plan/rl-eval-system.md P0–P4 自主实施）

- **背景**：跨课程 RL 评估从人工翻日志变为自动账本。P0 数据底座/入账/字段贯通、P1 阶梯+God 透传、
  P2 批次派发、P3 统计/哨兵/网页、P4 回填验收工具链一次落地；T0.6/T1.3/P4 实测需集群+活腿，标 TBD(标定)。
- **备选与否决**：扩 codehash-files.txt 纳 gameplay 集 —— 否，升级波前科+两套生命周期，另立 engine_epoch
  （表与配方单源 codehash-files.ts 集内文件，TS/Python 双语指纹已对拍一致；**2026-09-17 撤销**——见
  §2026-09-17-goalnn-unified-node-gate：gameplay 集已并入 codehash-files.txt，epoch 收敛为 `sha256(codeHash)[0:16]`）；训练循环内改 A 层语料轮转 —— 否，
  A 层钉死 EVAL_SEEDS 是历史可比基石，B 层 16 段轮转（§11-4）；TS 重写 fetch_task —— 否，双语协议维护翻倍，
  执行侧走 Python 复用原语；阶梯 max_ticks 沿 §4.2 例 2400 —— 否，实测 god 在 s1 仅 6 杀被截断，改 12000 对齐训练口径。
- **决定**：B/C 节点门 engine_epoch 严格拒派，A 层过渡期旧 agent 记日志放行（舰队升级完收紧）；W1 入账走控制台
  read-through（训练循环零改动）；A/B 按 eval_on_round 确定性分配；agent taskKey 无 policy 分量 ←→
  iterId 命名空间隔离 god/nn（不动节点缓存键，避升级波外负担）。
- **违反后果**：跨 policy 串键（同 iterId 混 god/nn）、stale 节点产出异构 gameplay 仍过门、2400 截断压胜率抬超时门。

## §2026-09-10-course-exit-gates（2026-09-10 晚 / 09-11 晨，c6 判否后自主实施 R1/R3/R7/R9）

- **背景**：c6-margin 跑满 50 轮 8550 局、eval 均值 25.4（老师 50、突破线 35）、**零学习信号**，且
  it50 因远端 PPO job 三次 1800s 超时而进程消失（docs/rl.progress.md §22）。根因与修正清单在
  `plan/feasibility-map.md` §12（D1–D9 / R1–R9）；用户拍板：**判否停腿，改设计后重开一腿**，
  优先级 R1（门槛进代码）> R3（重定价）+ R7（每轮局数）> R9（远端失败降级）；R2/R4/R5 暂缓。
- **决定（R1 门槛进代码，M0+M1）**：① `rl/config.py` 加 `GateTeacher/GateRule/GatesSpec`
  （parser 强校验 §3.4 全 8 条，未知 kind 响亮报错，`_GATE_FRAC_FIELDS [0,1]` 与 `_GATE_REL_FIELDS ≥0`
  分家——§3.2 示例的 `max_phits_rel: 1.5` 是相对倍数，本就 >1，计划原文按批注修正）；
  ② 新增 `rl/gate_check.py` 纯函数求值器（`evaluate(course, trend_rows, health, budget, now=None)`），
  9 种 kind + lattice `override > ABORT > PAUSE > STOP > REMEDIATE > ADVANCE > HOLD` + sustain 去重；
  ③ `loop_guards._gate` 作第四守卫（每 eval_every 调一次），判决写 `gate_verdict` 事件；
  `_breaker` 熔断同写 ABORT 行（否则执行面在真 ABORT 场景读不到判决）+ 补 NaN/inf 检测
  （NaN 与阈值比较恒 False，旧代码永不熔断）；④ `settle_eval_summary` 顺带落
  kills_mean/zero_kill_frac/phits_mean/pickup_mean/timeout_frac/course_fp（门控技能子指标单源）。
- **与计划的两处偏差（严格增强）**：sustain **从 trend_rows 复算**（最近 sustain 个不同 wver 的
  summary 行全达标）而非建在 gate_log 历史行上——求值器保持纯函数、崩溃重放幂等；
  ADVANCE 的「≥2 seed 集」仅当行携带 `seed_fp` 时才校验，全缺记 unknown **且放行**（否则历史
  语料让 ADVANCE 永久不可达）。
- **决定（R3/R7，载体 `curricula/c6b-margin.jsonc`）**：`wTick` 0.01→0.001（满局 −24→−2.4，不再压过
  击杀）、`terminal.stage_clear` 2.0→6.0（＝2× wKill，通关成最大单项）；`seed_rotate` 150→600
  （采样侧 48s→≈3.2min，单轮瓶颈在远端 PPO 而非采样）；`eval_every` 5→3（门要 3 轮才敢判）。
  教师块 wins=50/100 取 c6 探针值，**kills/phits 未测 = 0**——依赖它们的相对子项按 §3.4-7 跳过，
  不编造。G3/G8 首期休眠。
- **决定（R9 远端降级）**：`wait_job` 轮询指数退避（5s×2^k，封顶 60s；404 正常排队不退避）；
  新增 `--remote-degrade-after N`（默认 3）：远端连败达 N 次 → `args.ppo="local"` +
  `remote_degrade` 事件 + 本轮继续（训练活着）；`N=0`（禁降级）连败 3 次 → 写
  `gate_verdict: ABORT` 后停腿。
- **违反后果**：不落地 R1 则任何课程都只能靠事故终止（c6 已兑现一次）；不落地 R9 则云端不可达时
  整条腿耗在轮询上（c6 it50 白烧 2h）；教师基线编造会让 ADVANCE 判决失真（门形同虚设）。

## §2026-09-11-c6-review-disposition（2026-09-11，§12 复盘评审的处置：逐条核数据，非照单全收）

- **背景**：用户转来一份对 `plan/feasibility-map.md` §12（c6 复盘）的评审，要求谨慎评估、
  充分论证。评审指认三处事实错误（F1 kickstart 机制、F2 it50 口径、F3 R1 设施状态）、
  三点诊断偏漏、以及"R 清单缺执行顺序"这一最大缺口。
- **核实方法**：一律回到盘面与代码——`tmp/c6-margin/{training_log,eval_log}.jsonl`、
  `remote-jobs/*/manifest.json`、`rl/reward_library.reward_from_spec`（真实公式而非笔算）。
- **接受（已改文档/配置）**：① D10 单变量失真（c6 注释写"单变量=敌数"，但 bc 同时
  从 c4-it140 换成 c5-it40）；② D11 无梯度假说链（稀疏通关+廉价死亡+时间税 → terminal
  主导 → PPO 只能学"更快结束"）；③ `§12.3.1` Phase 0–4 执行顺序 + R3/R4/R5 互斥；
  ④ ADVANCE 加 effect size ≥+5pp；⑤ 预算拆"有效训练 ≥2h"与"事故熔断"两件事；
  ⑥ R2 依赖 R9；⑦ R4 准入加测学生零样本；⑧ verdict 从"零学习"收敛为
  "当前定价下无存活梯度"；⑨ it50 从 eval 全表剔除。
- **部分接受（评审数字不准、方向对）**：F2 说 it50 是"105 局/31.4%"——磁盘是
  **100 局/33 胜**（另有 4 条 `games=0/dropped=100` 作废 summary），但"该点采集于事故中、
  不能当第 10 点"成立，全表回到 9 点均值 24.8。F1 说 kickstart"it49 仍活跃"——爬升的是
  **KL(π‖BC) 距离**（0.0041→0.1453），惩罚项 = kk×KL 中 kk = 0.5^(it−1)，**it10 后 ≈0**；
  但旧版"ks 衰减到 0.01"确是量纲错误，已按两条轨迹分别重写。
- **否决（有实测依据）**：① 评审建议 R3 同步抬 `lives_exhausted` —— 本腿速死已 −2.33
  对全歼 −26.78，再抬只放大 terminal 主导（D5 方差来源）且构成双变量，留 R5 单独腿；
  ② 评审说 R7"先 600 是加压"——实测远端 PPO 中位 **70s**@150 局（rollout 32s），
  ×4 ≈4.7min ≪ 1800s 超时，c6 的 it50 是**隧道不可达**而非 payload 过大（前 49 轮同尺寸
  全部成功）。
- **过时**：F3 称 R1 求值器/守卫未接线 —— 已于 1380b6e 落地；但"per-tick 止损恒 False 是
  设计如此"的指正成立，课程止损改由 gates 承担，不再动 `stop_loss_hit`。
- **配置随之改动**：`c6b-margin.jsonc` 定位为 **Phase 1 探针腿** → `iters=20`、
  `max_hours=4`、`est_iter_min=8`；头注释写明"R3+R7 双变量，结论不可拆"，bc 不换。
- **评审建议落码（2026-09-11 第二批）**：不止改文档——① `wins_mastery` 加
  `min_gain_pp`（effect size，参照 `GatesSpec.baseline_win_rate` 零样本起点）与
  `require_rising`（窗口斜率 ≥0，"≥3 点同向"机器化）；② `GatesSpec.min_train_hours`
  （ADVANCE 前置：有效训练不足 → HOLD）；③ 新增 kind **`duty`**（G13 事故熔断：
  `Σ ppo_sec / 墙钟 < min_train_frac` → REMEDIATE；分子跨重启从 iteration 账本重算，
  事故轮走 iter_error 不入分子、入分母——占空比下降正是想要的语义）。
  c6b 的 G1 配 5pp+同向、新增 G13 duty(0.35)、`min_train_hours=2.0`、
  `baseline_win_rate=0.26`（⚠ 开腿前须重测回填，改值 = course_fp 变 = 新实验）。
- **2026-09-11 后续（dated note）**：`min_train_hours` 已**移除**（字段 + 求值逻辑 +
  测试 + 课程键全部清理），改由 `GatesSpec.min_train_samples`（Σ samples×epochs）承担
  ADVANCE 的证据充分性判定。原因：远端模式的 `ppo_sec` 是往返墙钟（含打包上传/排队/
  下载），排队越久越"达标"，且本地采样期间云端空转——照烧 Kaggle 配额——它看不见；
  且 c6b 的 20 轮上限（Σppo ≈1.5h）根本够不到 2.0h，该门在本腿物理不可达。
  同期 `train_sec` 改用云端自报的真训练秒 `ppo_cloud_sec`，G13 duty 的分子随之更准。
- **违反后果**：不查数据照单全收会把 105/31.4% 这类错数字写进复盘；把 R3/R4/R5 混在
  一条腿里开跑则违反单变量纪律，下一腿仍无法归因。

## §2026-09-11-goalnn-evalboard-idle-yield（2026-09-11，用户指令：evalboard 与训练 A-eval 解耦）

- **背景**：`eval now` 只 append pending 批；旧接线在 `rollout_phase` 里且仅当
  `not eval_on_round` 才 `maybe_dispatch_batch`——B/C 与 A-eval 轮绑定，训练停或
  恒 eval 轮时队列永假「已入队」。
- **备选与否决**：继续 A/B 轮次互斥 —— 否，两子系统不该耦合；console 起独立 worker
  进程 —— 否，与 train 池争节点且多一套生命周期；只放宽为「任意 rollout 轮都可领」
  —— 否，仍会在采集高峰抢集群。
- **决定**：① 从 `rollout_phase` 去掉 B/C 领批；② TrainingLoop 每轮 `_join_eval` 后
  开 idle 窗 `_evalboard_idle`（`window_event` 置位）领最早 pending 批；③ 下一轮
  rollout 前 `_evalboard_yield` 关窗 + 短 join，在途局停派新 seed、已结算不丢
  （`_reopen_for_resume` 把 partial 批回 pending，`_done_keys` 续跑）。
- **违反后果**：不改则 God/学生 B 批在 A-eval 主导的课程上永远 pending；抢跑会拖慢
  rollout 并放大尾延迟。

## §2026-09-11-nntrain-g13-duty-fix（2026-09-11，用户指令"处理所有问题"）

- **背景**：G13（有效训练占空比<0.35）在 c6b-margin 首日每个评估轮必停——分母用首条
  run_start 终身累计，it1 的 47min 冷启动与停车死时间永久进分母（duty 0.06→0.13），自激死亡螺旋。
- **备选与否决**：滑动窗口（最近 N 轮/小时）—— 否，开腿头 1-2 个窗口仍背着冷启动债、还要多解
  一个窗口长度参数；阈值 0.35 改小 —— 否，改课程文件=新实验版本（§D14），且热态单轮 42-53%
  说明阈值本身没错；duty 加 sustain 连续 2 窗 —— 否，基线下修后无必要、多一层状态。
- **决定**：① G13 分母基线 = **首个完成迭代的结束时刻**（起步热身不计账）+ 完成迭代 ≥2 才判
  （防开门即响；无 duty 数据的旧调用方回退终身口径）；② 非评估轮也查 duty（only_kinds 过滤，
  `_gate` 不解坏签名）；③ G5 max_hours 加**轮级硬断**（原先只在评估轮被查，会过冲 ~eval 周期）；
  ④ exit-watchdog 识别**设计内停车**（账本近 300s 有 gate_verdict/circuit_break → 标「已停车(原因)」
  而非「意外退出」）。终身口径只留给 G5 预算门（cap 语义）。
- **违反后果**：回归终身口径则每条新腿的起步冷启动会再次让 G13 必停；漏掉轮级硬断则
  max_hours 可过冲；不识别设计内停车则每次门停车都误导操作员翻崩溃日志。
- **未决（另立决策）**：pull 模式停车只停派活、不省 Kaggle 配额（worker max-idle ≥1h），
  真省需「停车连带 hub 停机 + 释放云会话」动作链。

## §2026-09-11-nntrain-cloud-halt（2026-09-11，用户指令：停机=停云端省 GPU 限额、本地进程都不停、console 出横幅）

- **背景**：§385 只做了门口径修复，没动"停车不省云配额"：loop 一停只是不再派活，Kaggle/Colab worker 照烧
  （max_idle ≥1h 才退）。用户明确语义：**停机 = 停云端机器省 GPU 配额，本地 hub/console/trainingLoop 一律不停**；
  停机后 console 界面显示红色横幅。
- **备选与否决**：训练进程内自行发停机 —— 否，SIGKILL/OOM 时执行不到、还多一份调用面；只靠 worker 空闲退出 —— 否，
  要等 ≥1h、停机期间照烧；hub 拒发新 job 但不通知 worker —— 否，worker 继续空转轮询烧会话。
- **决定**：① hub 新增管理端点 /admin/workers/halt|resume|status（Bearer 同 worker 鉴权；volatile），置位后
  /jobs/next 下发 {"halt":true}；② worker 收到达令即干净退出（keepalive 停、cell 走完）；③ exit-watchdog 在
  TrainingLoop 死亡（含设计内停车与崩溃）时自动调 hub halt（幂等）+ console-state.json cloudHalt{at,reason}
  持久化；④ console 顶栏红色横幅显示停机原因 +「恢复云端」按钮（手动 cloud-halt/cloud-resume 动作随之提供）。
  云商无 release API 的事实（Kaggle/Colab 需手工断连或到 9h 上限）如实写进横幅文案与注释，不假装全释放。
- **违反后果**：不加则 loop 一停云端照烧（事故复诊/崩溃期间都在空转烧配额）；恢复时漏重启 worker 会话会卡派发——
  横幅与「恢复云端」动作就是主介入路径。
> **（2026-09-11 修订，用户确认＂停机≠省配额＂）**：worker 退出≠云机释放——Kaggle/Colab
> 宿主会话不因此停止计费（Kaggle 无释放 API、Colab 空闲约 90min 才回收）。停机的真实边界：
> ① Colab 部署下 halt 达令触发 `google.colab.runtime.unassign()` 真释放实例；② Kaggle 等无 API——
> 横幅与动作文案改为**诚实警告**（worker 已离线、宿主会话仍在计费、必须人工断开），不再声称＂省 GPU 配额＂。
> 恢复路径不变（hub resume + 重启云端 worker 会话）。


## §2026-09-11-ppo-tpu-step-mark（2026-09-11，c6b-margin 首个 TPU job 单步 45~52s / eta 5.9h 根因定案）

- **背景**：Colab worker PPO 每梯度步 24→45→52s **线性递增**（142 chunks × 4 ep ≈ 7h，hub
  wait_job 1800s 必超时）；探针 E 段真机复现（Colab/Kaggle TPU）：E0/E1/E2（含已 warmup 的
  干净基准）全部 ~10s+/step 递增，`--tail 0` 固定 shape 亦不例外。
- **备选与否决**：ref 预计算执行时机 —— 否，E1≈E0 同速；尾块 shape 编译税 —— 否，
  tail=0 仍慢；多 chunk/perm 结构 —— 否，E2（bench_case warmup 后）亦 10s+；归因设备
  （PJRT 回退）—— 否，E1 + 每步 mark **收敛回 44ms**，正是旧微基准数字。
- **决定**：根因 = torch_xla 惰性模式下 `.tolist()` materialize 只断言依赖子图，backward/
  optimizer 在途节点不 drain、跨步骤累积 → 图线性变大 → 单步耗时 ∝ 步数。修复 =
  `ppo/engine.py::ppo_update` 每步 stats 后 `xla_mark_step(device)`（图执行边界；非 XLA
  no-op，CPU/CUDA 数值逐位不变）+ ref 预计算后一次 mark（防首步巨图）+ worker 日志打印
  device/world_size。**修正旧结论**：`plan/ppo-optimization.plan.md` §0.5「TPU 快 GPU 4.7× /
  44ms」是 ≤3 步微基准测量假象；44ms 只属于「每步有 mark」形态。
- **违反后果**：任何 torch_xla 持续训练循环若每步只有 materialize 而无显式 mark，都会再现
  「单步递增、多步后爆炸」；TPU 吞吐评估必须以 ≥6 步持续循环口径，且读「引擎即有 44ms」前
  必须先确认每步有图边界。后续 TPU 端 ppo_sec 读数均须按此修正解读。

## §2026-09-11-remote-worker-hotswap-supervisor（2026-09-11，云端热更新事故修复：os.execve 打掉 notebook kernel，改监督器+子进程）

- **背景**：远程 worker 热更新（CodeChangedError → 重启换代码）原用 `os.execve` 原地替换进程
  镜像。pull 模式 notebook（battle-rl.ipynb）把 `worker_loop` 跑在 **kernel 进程内**——execv 后
  ipykernel 对 sys.stdout 的重定向对象丢失（日志只进 kernel server 控制台、单元格断流），ZMQ
  执行服务不再应答、Jupyter 判定 kernel 死；用户看到「自重启」后单元格无下文 → 按停止 →
  SIGINT → kernel 重启 → 云端会话报废。真实事故序列：ipynb 日志止于「自重启」行，系统日志显示
  worker 仍在跑 job 却被中断 → kernel restarted。
- **备选与否决**：保留 execv、仅让 notebook 拉子进程 —— 否，execv 替换的是 kernel 进程本体，
  任何进程模型下都吃 stdout 重定向与 kernel 服务，属根本错误；notebook 裸跑子进程不转发 —— 否，
  子进程 inherited stdout=fd1=server 控制台，单元格仍看不到（同一根因的另一面）。
- **决定**：① 热替换统一改为「以退出码 HOT_RELOAD_EXIT=86 干净退出，由**监督器**用同一套参数
  重新拉起子进程」——fresh 进程 sys.modules 必然为空，新代码一定生效；② worker.py 新增
  `supervise_worker(restart_argv)`：worker 跑在子进程，stdout/stderr 逐行转发到本进程 stdout
  （notebook 里本进程= kernel，转发保住单元格），退 86 → 同参重拉，KeyboardInterrupt → 先杀子
  再上抛（不留孤儿 worker）；③ `main()` 拆两模式：默认监督器，env `REMOTE_WORKER_CHILD=1`
  的子进程直跑 `worker_loop`；`worker_loop` 传 `restart_argv` = 有监督器（热替换退 86）、
  None = 无监督器（提示人工重启并返回，旧降级行为保持）；④ battle-rl.ipynb `run_pull_worker`
  改调 `supervise_worker(restart_argv)`；CLI `python -m remote_worker` 照常可用（supervisor 包
  一层、rc 原样上浮，M1 冒烟与 `--once` 退出码语义不变）。
- **违反后果**：任何人把热更新改回 execv、或把 worker_loop 直跑进 kernel 并 execv 自重启，都会
  复现「单元格断流 + kernel 判定死亡 + 中断毁会话」；监督器必须由**不 execv 的进程**承担。

## §2026-09-16-kaggle-kernel-no-torch（2026-09-16，Kaggle 完整引导后 kernel 内 import torch 无声杀会话）

- **背景**：Kaggle 上跑 `battle.tailscale` / `tailscale.debug` 完整引导（apt 装 tailscale + userspace daemon + 登录 + 代理 env 注入）后，**同一 kernel 进程里 `import torch` 稳定无声杀死整会话**——无 traceback、probe 前置行（nvidia-smi/字符串）能打印、随后 kernel 死→ 会话被回收（peer lastSeen 冻结）。而逐步拆开复刻（apt 装 / daemon / `tailscale up` 登录 / 代理 env 各组合：S1/S2/S4/D1-D3）**全部存活**——唯一稳定死点 = 完整引导后 kernel 进程内 import torch，机理未定位（平台倾向），不阻塞修复。
- **决定**：torch/CUDA 探测下沉子进程——`notebook_runtime._probe_cuda` 用 `sys.executable -c` 子进程探版本/卡数/卡名并回传 JSON；**kernel 本体永不 import torch**。子进程死 → 按无 CUDA 落 TPU/CPU，kernel 照常继续（"死也只死子进程"）。TPU 分支保持 find_spec + /dev 扫描（本就零 torch import）。与「内核绝不 import torch_xla」同一纪律延伸。**生效需重启 TrainingLoop**（code.zip 在 loop 启动时 `pack_code_zip` 一次性打包，notebook 引导从 hub `/code` 拉）。
- **违反后果**：任何人把 torch 引用放回 notebook cell / notebook_runtime 的 kernel 进程（如内联 `import torch` 探测设备），在 Kaggle 完整引导后会复现"整会话无声回收"；设备探测一律走子进程。

## §2026-09-11-c4dodge-course（2026-09-11，用户拍板：c4 残血/闪避后继腿，wChip 单变量 0.005→0.02）

- **背景**：c4-margin.it140（c4 上 72%）零样本 c5/c6 = 39%/18%；c5/c6 平台期的直接死因是
  无闪避——胜局掉血中位 144（剩 119/263 ≈45% 血），败局掉血中位 216–228（≈3 发快弹磨死），
  c6 连胜局都挨 200 血（margin 归零）。c5/c6/c6b 三腿已证"该关形态无梯度"，问题不在定价在
  生存技能，而 c4 是唯一可教闪避的练兵场（已能清场，才有余力学"赢得干净"）。
- **备选与否决**：再练 c5 —— 否，74 轮/14 点已充分表征为平台，且 reward 里没有闪避信号
  （wChip 0.005 太小、wDmg 只计死亡、wTick 0.01 罚多活），续跑是负 ROI；c6b 只降 wTick 不
  动 wChip → "多活不再是负债、闪避仍不赚钱"，正好解释其无效；R5（1→2 命）—— 否，是方差
  控制不是闪避本身，留作后续。
- **决定**：新建 `nn-training/curricula/c4-dodge.jsonc`（派生 c4-margin，单变量 = wChip
  0.005→0.02，其余学习侧全锁；bc = c4-margin.it140；iters=160 + max_hours=10）。闪避目标 =
  wChip 抬到"挨 144 血 ≈1 个击杀"开始值钱。门：机器 catalog 无 margin 类，故 G1 胜率轨
  （池化 3 点 ≥77.5%）作 ADVANCE 代理 + G2/G7 防苟活 + G4/G5/G13 护栏，margin 门
  （胜局掉血中位 <120，超老师一档；God 同语料实测 68%/144）留人读复核。**教师基线重测**：
  in-loop 语料 = EVAL_SEEDS 860001-860100@stage2000（非 §2 的 seeds 0-99），God = 68/100
  （旧 64 不可比，c6b 教训）。开腿前先跑 3 轮看 G13 duty 与 ppo_sec。
- **违反后果**：任何人把 c4-dodge 的 wTick 一起改掉（c6b 已证 wTick 单测无效）或把 ADVANCE
  判据只放胜率不等人读 margin，都会污染"wChip 教闪避"这一单变量归因；结业后必须做 c5/c6
  零样本转移验证（残血可转移的唯一证明）。
- **补记（R7 加样本量，13:30）**：用户拍板 GPU 余粮充足，`seed_rotate` 150→600（次变量）。
  理由：①弱信号腿吃梯度质量（600 局/轮 advantage 噪声小）；②固定开销摊薄（同 2.1 万局
  ≈4h vs 7h，省 ~40% 墙钟）；③R7 有 c6b 中性前科（600 vs 150 结局相同），归因仍落 wChip。
  `eval_every` 5→3（测量侧）、`min_train_samples` 2M→4M（600 局下 2M 只挡 7 轮）。已声明
  双变量。**实测踩坑**：腿 13:16 以旧文件（150 局）启动，it1-4 后 G13 duty=0.31 设计内停车
  （150 局/轮 ppo_cloud_sec 太小撑不起 0.35 占空比）——600 局顺带解决 duty（~130s/350s≈0.37）。
  console cloud-resume + start 重启后已按 600 续跑（it4 权重保留，150 局 4 轮有效数据作废
  不可惜）。
- **补记（转移阴性 + 停机轴修正，17:30）**：① 用 it18/21/30/33 跑 c5/c6 零样本转移——
  **阴性**：胜率 c5 34–44%（基线 39）、c6 6–20%（基线 18）全部平，c5 胜局掉血不降反升
  （128→144/158/172）——"残血→c5/c6 余地"假说被证伪。且在场敌数上限=4（MAX_ENEMIES_ALIVE），
  c5/c6 难在**总清场队列 5→6 杀**（拖长暴露 25–50%），不是密度；c4 的 144→100 是节奏红利
  （胜局 tick 1341→1231）非可泛化闪避，节奏不转移（c5 平均 tick 不降反升）。
  ② **停机轴错误修正**：c4-dodge 目标=ticks/hp，但门系统趋势源无这两项，我原先 G1/G4
  锚在 win_rate/kills_mean → G4 以错误指标判平台停车。修正 = 删 G1/G4，skill_floor 不能
  休眠（§3.3）故 G2 也删（防苟活条件保留人读 DoD），仅留 G5/G7/G9/G13 护栏；毕业=人读
  margin DoD（掉血<120 已稳定达标 6 点）。教训：**margin 腿的门必须锚 margin 轴，
  门系统缺该轴时宁可不配机器 ADVANCE，也不要用 win_rate 当代理**。

## §2026-09-11-nntrain-cloud-halt-final（2026-09-11，用户五条语义：停机命令随任务同发、云机先试停机再干活、本地全不动、红灰双横幅）

- **背景**：前两版只解决"worker 退出≠停机"，存储层面仍无法编程释放 Kaggle 会话（Colab 可 unassign）。
  用户定死停机操作语义五条：①云机取任务时任务与停机命令同发；②先尝试停机、停不掉就继续执行任务；
  ③hub 本地所有组件进程正常；④控制台红横幅显示停机问题与状态；⑤云机继续跑、停机条件消失后一切回
  正常、灰横幅显示"曾停机已恢复"。
- **备选与否决**：停机=worker 退出 —— 否（上版已否，且 quit 后恢复要人工重启会话）；停机时停发任务让云机空转 —— 否，
  空转才是最大的浪费；停机状态为一次性事件（不持久）—— 否，五条要求 halted/recovered 双态可迁移。
- **决定**：① /jobs/next 恒带 halt:bool（有任务时与任务同批下发，空闲时单独送达）；② worker 停机过渡
  尝试一次 _release_cloud_machine（Colab unassign / 其余提示人工断开），**不退出、照常执行任务**；
  halt 清除后复位可再试；③ console-state cloudHalt 双态（halted 红横幅 / recovered 灰横幅保留历史，
  TrainingLoop 重启或手动即恢复）；④ 本地组件全程不动。
- **违反后果**：回退到"停机=杀 worker"则恢复续跑要人工重启会话、停机期间真正闲置空烧；不清 halted/recovered
  则操作员看不到"停机中/已恢复"的迁移，云机配额的处置失去可见性。## §2026-09-11-gates-never-park-loop（2026-09-11，用户定案：TrainingLoop 永不因门停车，该停的是远端云机）

- **背景**：c4-dodge 连续两起"门停车"事故（it7/it8 G13 占空比 REMEDIATE、it29→it36 G4 plateau REMEDIATE），
  每次停车 → console 按 §385 自动向云机下发停机 + 红横幅，loop 停着等人手重启。用户质疑两点：
  kaggle 关不了机却"看着罢工"（实为停机达令分支吞了存活日志）；trainingloop 为何"还是停了"。
- **备选与否决**：维持 §4.3"非 HOLD 即停车" —— 否（用户原话：trainingloop 永远不要停！该停的是远端云机）；
  只对 G4 REMEDIATE 放行 —— 否（G13/G7/G9 同样会再犯，掩耳盗铃）。
- **决定**：① 门判决**永不因门停车**：`_park_on_gate` 改为 `_apply_verdict`——任何非 HOLD 判决只落
  `gate_verdict` 事件（复盘记录 + exit-watchdog「已停车」分类源）后**返回 False 继续训练**；
  ② 停机/恢复动作全部映射到远端云机：REMEDIATE/PAUSE/ABORT → `set_cloud_halt(True)`
  （走 hub /admin/workers/halt，console 同端点复用，`hub_client.set_cloud_halt` 新函数）；
  HOLD/ADVANCE → resume；实例态 `_cloud_halted` 防重复下发，重启即复位；local/push 无 hub 短路零行为；
  ③ 真正的停车只剩硬边界：预算到顶（_budget_hard_cut）、F4 熔断、止损 2σ、远端不可用 ABORT（leg 级）；
  ④ worker 停机达令分支补 60s 存活日志（"cloud halted, polling hub …"），停机期不再被误读为罢工；
  ⑤ 预算 STOP 不是停机达令，不触发云机停机。
- **违反后果**：回退到"门即停车"→ 每 ~6-7 轮一次停线 + 云机连带停机 + 人手重启（本次现场连锁）；
  停机期 worker 日志静默会继续被误判成罢工。

## §2026-09-12-multi-course-key-is-stem（多课程命名空间键 = 课程文件 stem，不是内部 name）

- **背景**：P1 活体验收前发现 `s-dodge.jsonc` 内部 `name` 是 `s-dodge-mix`，而控制台课程选择 /
  `tmp/<course>/` / `out` 路径 / TS launcher `peekCourse` 全用文件名 stem（`s-dodge`）。
  `run_rl.py` 锁曾用 `args.course_name`（内部名）→ `.run_rl.s-dodge-mix.lock`，与 TS 预检查的
  `.run_rl.s-dodge.lock` 对不上：preflight 永远查不到在跑进程，同课双开只能靠 python 侧兜底。
- **备选与否决**：锁用内部名、TS 侧改读内部名 —— 否（内部名要解析课程文件才知道，launcher
  preflight/kill 匹配全要变重；且 `tmp/`、`out`、`courses` 块全是 stem，改一边不如统一到多数方）。
- **决定**：命名空间键 = 课程文件 stem。`train/loop_util.py::course_key_from_path()` 为唯一推导点；
  `run_rl.py` 锁改调它；`train_loop.py --course` 文档写明传短名（与 `--course s-dodge` 同拼写）；
  内部 `name` 退为 S9 归属标注（events/gate 内用，不参与调度）。回归测试
  `test_course_key_is_file_stem_not_inner_name` 锁死 stem 规则。
- **违反后果**：回到内部名 → 双课 preflight/kill/账本课程键三方错位（静默错位，最难查的一类）。

## §2026-09-12-multi-course-p3b-supersedes-343（多课程：独占加超时租约重启用 §343；worker 侧有界 FIFO）

- **supersedes §343**：PPO job 分发从"竞速广播"改回**独占加超时**——`GET /jobs/next` 领取即设
  租约（owner + expiry + last_heartbeat **同时置**）并下发 `lease_token`；`claimable_job_ids` 排除
  持有未过期租约的 job；`POST /jobs/{id}/result` 有活租约时验 `X-Lease-Token`（无租约照收，兼容
  旧 worker/重发）；首写锁定保留（hub 重启丢租约的兜底）。新常量 `CLAIM_TTL_SEC = 300`；心跳
  60s 续租，且 `heartbeat()` 必须以它为**唯一** TTL 来源（沿用 `LEASE_SEC = 1800` 会让死 worker
  隐身 30min）。hub 记 `last_heartbeat` 并在 `/jobs/status` 暴露 `lease_expires_in`/
  `last_heartbeat_ago`（worker 侧吞错保持现状）。
- **为何敢重启租约**：§343 的竞速广播在多 worker 下让同 job 被重复算、慢者 409 白烧；单 worker
  时代它靠"孤儿零等待重领"避开 it24 白等 30min，但多课程并行需要 worker 之间不撞车。it24 的教训
  由四道闸抵消：TTL 300s + 60s 心跳续租 + 主动 release + 首写锁定兜底；大抖动双算/hub 重启丢租约
  属已知 edge，结果一致。halt 与租约正交（停机不拦分发、不清租约）。
- **备选与否决**：HUB 侧等待（状态应住执行方，竞态）/ worker 多线程并发 PPO（单 GPU 互挤）——
  均否；选 worker 侧**有界 FIFO**（`WORKER_QUEUE_MAX = 8`，满才 409）+ 同 jid 幂等（顺带修超时
  重试重复执行）+ 失败不堵队 + `/ping queued`；HUB 传输语义零改动。pull 侧 `--poll` 可多 hub
  轮询、`work_dir` 按源分区（`work_dir/<hub_id>/<jid>`），共享课程须同 commit（异 commit 走既有
  86 + 监督器重拉）。
- **违反后果**：回退到竞速广播 → 多 worker 重复算 PPO + 慢者白烧；TTL 调大 → it24 倒车（死 worker
  回收失灵）。plan：`plan/multi-course-parallel-training.md` §3.8/§3.9、P3b；实现见
  `nn-training/remote/{protocol,hub_server,worker_server}.py`。

## §2026-09-13-multi-course-course-keyed-singletons（多课程并行：课程 = 并行单元，一切单例按课实例化）

- **背景**：GPU 资源增加要求 N 课同跑（常态 2 课、4 槽位留余量）。原链路住着一组同构
  单例假设——全局实例锁（`.run_rl.lock`）、账本 5 个扁平单键、`rl.hub_port` 一处 base、
  隧道/`remote_hub_url` 单键、监督器猜课程、halt 单键、`--kill-previous` 按脚本名全杀
  （差距全表：`docs/multi-course-audit.md`；约束清单 S1–S17：plan §2）。
- **备选与否决**：发现服务/调度器进程（否——Simple beats clever：算术槽位
  `hub_base + slot*10` + 每事实一归宿就够）；两课共用一 hub（否——jobRoot/jsonl 串味，
  hub per-course 后调用方传参即隔离）；配额写进 `curricula/*.jsonc`（否——一改课程文件
  `course_fp` 就变，触发 D14 熔断误判：机器配额只住 `rl-config.json` 的 `courses` 块）；
  监督重建时用全局状态猜课程（否——fail-closed，查不到 = 放弃重建 + 响亮告警，绝不猜）。
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
  supersedes-343）；⑦归属字段（manifest `course_name`/dispatch course）只做对账审计，
  不参与调度，`course_fp` 血缘不动。
- **违反后果**：新单例回落全局键/端口 → B 课覆盖 A 课登记（杀错/停错/连错 hub）；
  配额进课程文件 → D14 熔断误判；重建时拿全局状态猜课程 → A 课进程被 B 课配置拉起；
  删 per-course 锁文件 → 2026-09-06 双 trainer 写同一 traj 事故重演。
## §2026-09-12-c5-gae（2026-09-12，用户指令：先建 c5-gae，c5-tick 继续跑）

- **背景**：c5-tick（wTick 0.01→0.003 单变量）it20 判一级门 FAIL：value loss 纹丝不动（~0.75，
  目标 ≤0.65），eval 32/43/47/35 无趋势——**"wTick 方差主导 ⇒ value 头不 fit"被自己数据证伪**
  （降 57%→10% 方差占比后 value 不动；熵稳定 0.49 也否定了"value 欠拟合 ⇒ 熵失控"的链条，
  熵失控是 c5-ent 的 ent_coef=0.05 吹出来的）。按预注册转进 λ 轴。
- **备选与否决**：停 c5-tick —— 用户否（继续跑着）；同轮再试 wTick=0 或 wChip 抬升 —— 否，
  单变量纪律，c5-tick 还没跑完；直接改 normalize_ret —— 否，变量太多。
- **决定**：新建 `nn-training/curricula/c5-gae.jsonc`（c5-margin 派生，唯一变量 lam 0.99→0.95，
  wTick 锁 0.01 不回继承 c5-tick；kl_coef 末段 0.03 护栏非变量；不写 ent_coef；bc=c4-margin.it140；
  80 轮/12h；无 gates 块）。诚实声明：λ 只测"advantage 方差 ↓ ⇒ 策略动起来"，不声称救 value 头。
  判据：配对胜率 >39%（起点）+ McNemar p<0.05 为主；value/entropy 作参考。
- **违反后果**：若 c5-gae 又顺手改 wTick/ent_coef/normalize_ret，λ 的归因被污染；若把 value loss
  当主判据，会重蹈 c5-tick"假说与数据不符"的覆辙——value 欠拟合的成因在关卡随机结构，
  不是任何单一超参能救的。

## §2026-09-12-goal-layer-test（2026-09-12，用户拍板两套目标源；goal 硬 mask 实证为负）

- **背景**：c5-gae（λ=0.95）修好机制后执行器配对 +7pp 但封顶 ~46%（in-loop ~40%），40 轮
  平台。用户提出解冻 goal 头 → 澄清 goal≠intent（intent 骑 God 执行器已证伪；goal=空间目标
  热图+独立执行器，T9a 证执行器是瓶颈，goal-nn-action 定"最后才解冻"）。当前 per-tick 模型
  就是 goal-nn 的执行器本体。评估后用户拍板：做 goal 层测试（两套目标源都测）+ c5-gae 续跑。
- **实现**：新增 `src/nn/goal-mask.ts`（BFS 距离硬掩码"禁背离目标" + God 导航目标源（读
  `god._navTargetCol/Row`）+ 手写启发式源（血低撤退/追最近敌）+ GoalSteering 承诺重选）；
  `export-eval-game.ts` 加 `--policy nn-goal`（frozen StudentNet move logits 应用硬掩码，
  `mask[i]!==1 → -1e9`，只禁不禁劝）；goal-source 显式走 worker payload（env 继承不可靠，两
  冒烟逐字节相同后改为显式传参）；测试 `tests/nn/goal-mask.test.ts` 10 项全绿 + tsc 过。
- **结果（c5 关，frozen c5-gae.it100，seeds 0-99）**：**nn 10/20=50% → nn-goal(god) 1/20=5%、
  100 局 1%**；heuristic 源逐字节同崩（God 导航目标≈最近敌启发式）。机制：距离硬掩码砍掉
  执行器的战术机动（后撤装填/走位闪避都涉及"暂时背离目标"）→ 被迫直线冲火线 → 被击 9→19、
  击杀 3.7→0.57、局时 30% 更短。
- **结论**：**goal 硬 mask（1a）在"已会打"的执行器（~50%）上是灾难（→1%）**——goal-nn-action
  标注的"只能禁止不能鼓励、弱控制"局限实证。目标意识的价值**不能**用 1a 注入成熟执行器验证；
  若 goal 层要测，注入机制须换（1b 软偏置诱导 / goal 编进 reward 重训）。也解释了 1a 原设计
  配给"从零练的弱执行器"——弱执行器没有机动可被砍。
- **违反后果**：任何人把 1a 硬 mask 当"验证 goal 价值"的现成工具，会在成熟执行器上得到
  "goal 无用"的错误结论；goal 层测试必须先定注入机制（硬 mask 只适用于从零练的弱执行器）。
- **补记（1b-posthoc 软偏置，无信号）**：`goalMoveBias`（logits += β·align，align∈{-1,0,1}，
  诱导不禁止）接 `--goal-bias`。c5 关 frozen it100、seeds 0-19：β=0.3 9/20、β=1.0 9/20、
  β=2.0 5/20（退化逼近硬 mask）；**β=1.0 全量 100 局 = 46% vs nn 47%（无信号）**。结论：
  c5 执行器已"朝目标走"，commitment nudge 与既有行为重合 ⇒ 无增益；β 加大才崩。**两个外部
  注入机制（1a mask / 1b bias）在成熟执行器上都不能抬平台**——c5 瓶颈不在"目标方向/承诺"，
  在清场吞吐/生存层（杀不完第 5 敌就死）；1b-input（网络通道+重训）不乐观（学的也是同一
  个"往目标走"偏置），需另寻杠杆。

## §2026-09-12-c5-gae-finale（2026-09-12，λ 修复 160 轮显著破平台；c6 转移稳定）

- **c5-gae 终判（it160 收官）**：λ=0.95 修好 value 头后，160 轮配对语料（seeds 0-99 vs
  起点 39%）**it145=57%（2p=0.010）/ it160=55%（2p=0.023）——统计显著 +16-18pp**，全项目
  第一条 hard 5 敌关显著爬升腿。战斗质量全面升级（击杀 3.15→3.7+、被击 60→42-44）。in-loop
  中枢 ~40%→~44-45%（it125 53% 是尖峰）。机制全程健康（value 0.50-0.62、熵稳定 0.33-0.40）。
  全轨：腿初 28-37% → 中段 40-52% → 终局 55-57%。此前所有"c5 平台"判断 = 机制坏+训练不足。
- **c5→c6 转移（稳定弱阳性）**：c5-gae it100/145/160 零样本 c6 = 29/27/23% vs 基线 18%
  （池化不一致对 72:47，2p=0.027）；击杀 2.64→3.34 大涨但收不了 6 敌关。零样本 ~26% ≈
  c6b 直接训 20 轮的 22%——强 c5 执行器跨关能力 ≈ c6 自己训一点，卡点全在"6 敌收关"。
- **道具（pickup）缺口分析（任务口径，教师只作参考 §0.2）**：c5 上平坦不是缺陷的判据
  = 任务目标而非教师水平；c6 上学生 0.82/局（God 参考 1.26）未跟上道具密度（c5 0.88 → c6
  0.82 反降）。"道具→star→击杀吞吐→破 c6/c7/c8 清场瓶颈"任务级假设成立 ⇒ wPickup 作为
  **c6 基线腿之后**的单变量（不混进 c6 腿的 bc/λ）。
- **下一条腿**：`c6-gae`（c6-margin 派生：bc=c5-gae.it160、λ=0.95、wPickup=1.5 锁定、
  seed_rotate 150、无 gates 块；起点基线 23%）。问题：机制修复后 c6 能否从 23% 爬 +
  策略是否自发多捡。

## §2026-09-12-rollout-flag-bug（2026-09-12，local rollout 3命1星污染事件；已修、已记录、暂不重训）

- **背景**：`nn-training/rl/cmd.py` `build_rollout_cmd` 用 `f"--{k}"` 拼 override 键
  （`lives_override`/`player_level` 下划线），而 `tools/sim/export-rl-rollout.ts` 只认连字符
  `--lives-override`/`--player-level`（未知 flag 静默忽略）⇒ **local 直跑全程以 hard 缺省
  （3命1星）执行，远端节点以课程覆盖（1命0星）执行**。commit `1ee8955`（2026-09-12 16:55）
  修复（下划线→连字符，`tests/test_rl_cmd.py` 锁死口径）。bug 自 `e828331`（2026-09-02 22:55，
  M1 配置化）引入。
- **污染范围**：e828331 → 1ee8955 之间所有课程的 **local 直跑 rollout 轨迹**（PPO 吃进
  3命1星环境的样本）。各课程 local 局占比实测（`tmp/<course>/dist-agent-meta.jsonl`）：
  c4-kb1 **39.1%**、c4-margin 28.3%、c6-gae 21.1%、c5-gae/c5-margin/c5-ent ~14-15%、
  c6-margin/c6b-margin 11-13.6%、c5-tick/c6-pickup 13.3-13.8%。**样本量权重更高**：it160
  local 45 局 7204 样本（avgTicks 1595）vs remote 105 局 11631 样本（avgTicks 1103）⇒
  c6-gae 污染在 PPO 中的实际权重 ≈ **38%**（> 局数占比 21%，3命局活更久）。
- **关键事实（判定可信的依据）**：eval 链路（`export-eval-game.ts` + `run_local_eval_game` +
  sampler-agent）与 rollout 命令模板（cmd.py）是**两套独立代码**，eval 一直用正确连字符
  flag ⇒ **所有 eval 口径结论（c5-gae 55% 爬升、c6-gae +7pp 等）未被污染**。修复后实测
  it160 归档权重 1命0星采样 rollout = 27% ≈ eval 29% ≈ 配对 30%，三口径回归一致。
- **c6-pickup 探针（污染窗口内训的 it35，修复后评估）**：c6 关 seeds 0-99 配对
  **38% vs 起点 23% = +15pp，McNemar p=0.025 显著**；且高于 c6-gae 160 轮的 30%（in-loop
  eval it5=38%/it35=34% 同步确认，非单点假象）。污染排除：污染更重的 c6-gae 反而不如它 ⇒
  **38% 落在 wPickup 1.5→3.0 杠杆上（唯一变量）**，道具杠杆真效初证。
- **处置（用户拍板）**：① 已收官课程（c5-gae/c6-gae/c5-tick 等）**不重训**——判定全走 eval
  （干净），权重保留作 bc/参考，但出身含 X% 多命样本需知情；② c6-pickup **暂不重启**
  （it36 权重 PPO 未完成即停，归档停在 it35；训练进程内存旧 cmd.py，修复不会热更新）；
  重启时须用修复后代码、以 c6-pickup.it35 为 bc 续跑 it36+。
- **违反后果**：任何人拿 training_log 的 rollout winRate 当能力口径（虚高 17pp 量级）；
  任何人把污染窗口课程的权重当作"纯 1命0星数据"训出的（引用前必须查本条目占比表）；
  任何人未经"修复后代码 + 冻结快照"就用本地直跑出教训性结论。

## §2026-09-13-reward-wdmg-dead-term（2026-09-13，wDmg 死项修复：击杀/承伤/死亡语义各归其位）

- **机制（代码核验）**：`src/game/SimulationCombat.ts:599` 口径下，玩家**非致命**命中推
  `player_damage`（累计入 `playerDamageTaken`），**致命**命中推 `player_hit`（`playerHits++`；
  另一触发 = 3★ 星盾消耗，本族课程 level=0 起步不可达）。⇒ **1 命课程里 `playerHits` 恒等于
  败局指示器**（胜局 0 / 败局 1，c6 实测败局分布 {1:110, 2:1}）。
- **判决**：reward 里的 `- wDmg*playerHits` 是**死项**——不承载挨打信息、与
  `terminal.lives_exhausted=-1.0` 重复扣败局分（败局合计 −2）、且伪装成「挨打惩罚」
  （历史上对 wDmg 的任何调参实际都是在调死刑）。修复 = **新课程删除该项与 `params.wDmg`**；
  承伤定价唯一归 `wChip*playerDamageTaken`，死亡定价唯一归 `terminal.lives_exhausted`。
  历史课程**不回改**（死项是每败局常数 −1，只平移败局回报、不改局内 credit assignment 时序，
  已收官结论仍成立）；自本条起新课程模板不再含 wDmg。基座课程 = `c6-dmgfix.jsonc`
  （由 c6-pickup 派生，单变量删死项，wChip 保持 0.005，bc=c6-pickup.it35，iters=60，eval 200）。
- **后果**：此后任何课程若再引用 `playerHits` 作「承伤」语义 = 违反本条；调「挨打痛感」
  只允许动 `wChip`（或后续承伤项），调「死刑」只允许动 `terminal.lives_exhausted`。

## §2026-09-13-level-extraction（2026-09-13，关卡配置抽离 + D14 语料身份改语义哈希；用户拍板）

- **触发**：mid-run 编辑课程文件（iters 30→60）把 c6-chip 打成指纹脑裂——shard manifest 的
  course_fp 与 job envelope 的 course_fp 都在各自时刻重读文件字节（`cmd.course_fp_for_args` /
  `loop_steps` 发布），编辑落在采样与发布之间 ⇒ D14 整轮 shard 拒收（job=abbf… shard=e6c6…）。
  原则确认：**iters 这类预算/测量旋钮不改变训练契约，mid-run 编辑不应要求新课程**。
- **配置修改分类学**（此后所有课程/关卡变更按此归类）：
  - **A 语料身份**（改动 = 语料破裂，禁止对在跑腿修改；要变 = 新关卡/新课程）：地图/敌人
    队列/敌数/出生点/**命/星**（→ 抽离到关卡文件）、difficulty/max_ticks、mode/dodge、
    seed_rotate/seeds、reward（formula/params/terminal/scheme——PPO 端按 manifest 公式重算
    回报，混入不同 reward 语义的 shard = 错账）。
  - **B 训练语义、非语料**（改动 = §15.5 新实验，stop→start 生效，不破坏 shard 血缘）：
    bc、γ/λ/clip/vf/ent/max_grad_norm、epochs/mb/lr、ppo_schedule、normalize_ret、kickstart。
  - **C 预算/测量/路径**（随时可改，stop→start 热应用）：iters/max_hours/workers/stream/
    keep_iters、eval_stages/eval_games_per_stage/eval_every、out/traj/backup_*、ent_break*。
- **第一步（本条）——关卡抽离**：`nn-training/levels/*.jsonc` 持有环境语义
  （stages/difficulty/max_ticks/player），课程以 `"level": "<name>"` 引用；课程侧内联重复
  声明四类环境键 = 配置冲突 raise（`rl/config.load_course` 合并）。内联 stages 旧用法逐字节
  兼容（在跑课程不迁移照常跑）。首两份：arena6（c6 家族）/ arena4（c4 家族）。
- **D14 升级为双指纹**：新增 **corpus_fp** = `config.corpus_identity_fp`（env+reward **解析值**
  规范化 JSON 的 sha256；内联与 level 引用同形同指纹，文件内注释/格式不敏感），经 rollout cmd
  `--corpus-fp` 进 shard manifest、publish_job 进 job manifest；worker 装载校验
  `d14_corpus_match` **优先比 corpus_fp**，任一侧缺席回退 legacy course_fp（旧 payload 兼容）。
  course_fp（文件字节哈希）保留作血缘/快照（D13）与 resume 对账不变。
- **复用**：关卡文件与 evalB/探针同形 schema——`eval-course-ckpt.ts --course
  nn-training/levels/arena6.jsonc` 直接可跑（不再需要 tmp/fmap-*.jsonc 复制品）。
- **迁移状态**：c6-dmgfix 已引用 arena6；c6-chip 在跑不迁移（其 legacy course_fp 校验在
  stop→start 后自然恢复一致）；其余历史课程按需渐进迁移。

## §2026-09-13-hot-reload（2026-09-13，课程热加载：非语料改动下一 iter 应用；语料改动拒绝+横幅+不泄漏云端；用户拍板）

- **机制**：trainer 每 iter（rollout 前，`loop_core.run` → `_hot_reload_course`）重读课程文件，
  以 `corpus_identity_fp` 分流（§2026-09-13-level-extraction 分类学的机制化）：
  - **未变（B/C 类）**：`rl/hot_reload.apply_hot_fields` 把白名单字段写回 args——消费点每轮
    活读（iters `loop_core:207` / gamma,lam / lr,epochs,mb / eval_* / ent_break*，
    `loop_steps._course_iter`），**下一 iter 即生效**；结构绑定字段（bc/workers/stream/
    out/traj/backup_*/freeze*/clip/vf/ent_coef/normalize_ret/warmup_iters/kickstart_ref）
    记 restart-only（`*` 后缀账），响亮日志「停止→启动后生效」，不静默吞。max_hours 热应用
    时重算 `self._deadline`。
  - **变了（A 类破坏性）**：拒绝热应用，写 `course_edit` 事件（verdict=rejected，本地账本）+
    响亮日志；控制台读账本渲染错误横幅（`courseEditFromLedgerTail`，取尾部窗口内最近一条，
    跨后续 iteration 事件持久；改回文件后 trainer 写 restored → 横幅自然消失）。**沿用启动
    配置继续训练**。
- **不泄漏云端**：课程文件字节在启动期冻结（`args.course_frozen_bytes`）——D13 全文快照、
  course_fp、shard `--course-fp` 一律用冻结字节（`loop_steps` 发布 / `cmd.course_fp_for_args` /
  `loop_core._course_file_fp` 三处统一）。mid-run 的任何编辑（含被拒的语料身份改动）永不进
  job payload / 代码包 / 远端日志。
- **§15.5 修订**：B 类字段（γ/λ/lr/epochs/mb/ppo_schedule）经热加载 mid-run 修改自此**允许**，
  记账 = `course_edit` 事件（applied，含字段清单）自动进账本；corpus/reward 语义仍禁止 mid-run
  （拒绝分支）。`plan_reload` 对「iters+wChip 同改」整单拒绝，不做部分应用（防半新半旧配置）。
- **文件半行写/瞬时坏档**：沿用旧配置静默重试（一次日志），不打横幅。
- **夹具迁移**：`training-multi-course.test.ts` F-B6 的 bc 夹具 s-dodge→c6-dmgfix（原
  tmp/s2-cap/weights.json 已被 tmp 清理移除，属环境性失败；新夹具指向 nn-training/weights/
  稳定备份）。


## §2026-09-13-bc-cloud-integration（2026-09-13，BC 训练整合进 云-HUB-LAN：云端第二任务类型 kind=bc；用户指令五步）

- **决策**：远程任务协议引入 `kind` 维度（"ppo" 缺省 wire 兼容 | "bc"），BC job 复用
  HUB job 全套（发布/租约/账本/回传/落位）与 LAN 节点协议（/v1/task ?mode=bc），
  云端 worker 按 kind 分叉执行（bc → `train/bc.py::train`），控制台 BC 课程复用
  trainingLoop 组件键（spec 分叉为 run_bc.py）。设计全量：`plan/bc-cloud-integration.plan.md`。
- **拒绝的替代方案**：为 BC 建独立管线（第二个 hub/job 协议/独立控制台组件）——重复
  鉴权/租约/账本/落位/冒烟五套已验证机制，维护面翻倍；BC 用 Colab notebook 手工跑
  （现况）——无断点续跑、无节点并发、无归档纪律，且与多课程体系脱节。kind-branch 让
  "第三种任务类型"（如 offline eval job）有先例可循。
- **边界**：bc manifest 免必填 reward/γ/λ（无 RL 语义）；mode 红线 ppo↔bc 互斥（串型
  拒收）；BC 无 init-weights（`init_weights_fp` 恒 "bc"）、无 opt tar 往返（不跨轮续训）；
  wins-only 败局 = `kept:false` 空容器（合法结果非失败）；BC 课程独立文件种类
  `.bc.jsonc`（rl/bc_config.py，D14 同规 `bc_corpus_identity_fp`）；smoke = 尺寸压缩
  真一轮 + scratch 落位 + 账本零污染（不覆盖 out、不写 bc_round_completed）。
- **教训入册**（docs/nn.progress.md §39）：单 shard 语料 shard 级切分 train=0 崩溃
  （make_loaders 回退样本级）；agent 结果缓存键不含任务参数 → smoke 独立 iterId 命名空间；
  LAN 节点需升级（git pull + restart）才有 `bcSupport` 能力位——升级前 fail-closed 不派。


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
  GET /api/evalReplayJob、GET /api/evalReplayDownload。
- **拒绝的替代方案**：评估时同步录制 replay 随局落盘（export-eval-game 常开录制）——
  每轮数百局 × 整局帧字节随 pack 传输/落盘，账本与传输面翻倍，且救不了历史 eval；
  TS 侧解析 curricula 重建课程参数——apply_course 的课程语义（自定义关/等级覆盖/
  param schedule）出现第二份实现，漂移即错局。
- **边界**：export-eval-game 的 `--replay` 为可选附加（recorder 被动采样，缺省路径
  行为逐字节不变），但该文件在 dist 哈希集内——变更后节点须随新 code 同步（常规流程）；
  权重归档被清理导致 wver 无匹配时 fail loud（错版本 replay 比没有更糟）；重放局与账本
  不一致（理论不该发生）在 manifest 里诚实标记，不静默；单次导出上限 400 局；
  replay 文件名 stage 段 = 原始 --stage id（自定义关 2000+ 非映射前 loadIndex）+1
  （buildReplayFilename 1-based 显示口径），python 映射时减回。
- **教训入册**：nn-training 内脚本目录 rl/ 会遮蔽 stdlib `queue`（rl/queue.py）——
  顶层 `from concurrent.futures import ...` 必须在移除 sys.path 脚本目录项之后
  （eval_replays_once.py 头部 scrub，实测循环 import 崩）。
- **同日注记（用户裁定）**：交付形态改为**逐文件**——不打 tar.gz；完成后每局一个
  .replay 写入用户指定目录（Chromium showDirectoryPicker，导出启动时先选目录、
  完成后逐文件写入；无该 API 的浏览器退化为逐文件浏览器下载）。服务端
  /api/evalReplayDownload(tar) 移除，改 GET /api/evalReplayFile（manifest.files
  白名单 + 文件名形态校验防穿越）。

## §2026-09-13-goalnn-max-ticks-rule（2026-09-13，D7 终局标准：max_ticks 取值规则一次性立案）

- **背景**：D7 把 max_ticks 划入任务侧不可动项，规则须在工厂定型时一次立案、不再逐调。原拟式
  `ceil(2400×count/4)`（= `600×count`）在低 count 端塌缩：c01=600 的 200 局 God 实测 61 局超时、
  **超时局 0 击杀**（可赢的局被截断），pass 69.0% < D4 门 80% ⇒ 该级门构造性不可达；同一批种子
  放宽到 1200 后 pass 99.0%、超时 0。同类事故第三次（§338 max_ticks 2400→3600；
  §2026-09-10-goalnn-rleval「2400 截断压胜率」）。
- **备选与否决**：A 保留原式只抬 floor `max(2400, 600×count)` —— 否，c04=2400 实测仍有 1/200 超时
  （胜局右尾被删失）且低 count 端白送 2× 上限；B 按观测拟合 `~840+285×count` —— 否，等于把上限
  钉在教师长尾上，违 §0.2（教师不是标尺），且脱离 c20=12000 的预算包络；C 原式斜率 + 实测固定项
  —— 取此。
- **决定**：**`max_ticks(count) = 600×count + 900`**（c01=1500 … c20=12900）。斜率 600 沿用
  roadmap 原式（c05-c07 实测该斜率恰好解除 2400 的截断）；固定项 900 = 接敌/穿场/生成节奏的
  一次性开销。判据 = 上限 ≥ 1.25× 实测胜局最大 tick。实测（200 局 God/级，EVAL_SEEDS
  860001-860200）max 胜局 tick / 新上限余量：c01 1121/+34% · c02 1640/+28% · c03 1775/+52% ·
  c04 2510/+31% · c05 2720/+43% · c06 2776/+62% · c07 3381/+51% · c10 3677/+88% · c14 4823/+93%，
  逐点零超时；**上界不缩**（12900 ≥ 原式 12000）。单一实现 = `rl/ladder_factory.max_ticks_for()`，
  由 `nn-training/tests/test_ladder_factory.py` 钉死。
- **违反后果**：低 count 端截断会把可赢局记成超时 ⇒ 门（pooled 400 ≥80%）构造性不可达，或超时率被
  误读成能力信号去调训练侧；终局标准反复改则 §6「现线证据沿用」与跨级/跨版本可比性失效。
## §2026-09-14-goalnn-dashboard-project（2026-09-14，用户指令：训练控制台独立为 `dashboard/` bun 项目；启动命令改 `bun run dashboard`）

- **决策**：`tools/training/**` **整棵**（core 领域模块 + console 网站 + ui 组件 + evalboard
  评估板 + `train.ts` python 启动器）搬到仓库根 `dashboard/`，成为一个**独立 bun 项目**
  ——自带 `package.json`（`start` / `launch` / `build:ui` / `typecheck` / `test` / `lint` /
  `format`）、`tsconfig.json`、`tests/`、`README.md`；20 个覆盖它的测试文件从根 `tests/`
  一并迁入 `dashboard/tests/`。根 `tools/training/` 目录**删除**，不再保留任何兼容壳。
  根 `package.json` 的启动脚本 `train` → **`dashboard`**（= `cd dashboard && bun run start`，
  仍听 :8900）。python 无头启动通道改为 `bun dashboard/src/launch/cli.ts --script <name>.py`
  （AGENTS §5.6 的 "never raw python" 语义不变，只是路径变）。
- **内部结构**（职责分层，调用方只 import 各目录 `index.ts` 桶，不直连内部文件）：
  `src/core/` 路径/日志/网络/进程/venv/账本/槽位等基础原语 · `src/stack/` 训练栈组件编排
  （specs/hub/courses/smoke/push）· `src/launch/` python 无头启动器 · `src/evalboard/`
  评估板领域（store/ladder/ingest/stats/…）· `src/server/` HTTP 与服务端逻辑
  （`api/` 17 模块 · `actions/` 11 模块 · `eval-board/` 6 模块 · iters/pool-history/
  exit-watchdog/build/server）· `src/web/` SSR + 浏览器 UI（`view/` 16 模块 · components ·
  app）。四个原巨型文件按职责拆开：`api.ts` 1913→17、`actions.ts` 973→11、
  `evalboard.ts` 1046→6、`ui/view.ts` 1513→16，单文件上限由 ~1900 行降到 444 行。
- **路径事实唯一来源**：`dashboard/src/core/paths.ts` 是全项目**唯一**用 `import.meta.dir`
  上溯推导路径的地方（`DASHBOARD_ROOT` / `REPO_ROOT` / `NN_TRAINING` / `LOG_DIR` /
  `BUNDLE_DIR` / `EVALBOARD_DATA_DIR` / `LADDER_CANON_PATH`）；其余模块一律 import 常量，
  绝不自算相对深度（搬迁时只有这一处会错，且一错即立刻暴露）。
- **跨项目读边界（唯一例外，写入即契约）**：`dashboard/` 只读消费 `src/` 的游戏契约
  （`config/stages`、`nn/arena-ladder`、`nn/config-stage`、`config/difficulty`、
  `config/combat`）与 `tools/agent/codehash-files`，用相对路径 import。**反向永远禁止**——
  `src/` 或游戏侧测试不得 import `dashboard/`（dashboard 是观测与控制面，不是游戏依赖）。
  这三四个契约是权威唯一源，复制进 dashboard 才是真错（漂移即错局/错门）。
- **拒绝的替代方案**：① 只搬 console/ui、core 留在 `tools/training/`（用户裁定的备选）——
  dashboard 仍靠相对路径反向 import 旧位置，"独立项目"是名义上的，且搬迁后两侧目录语义
  割裂（一半工具在 tools/、一半在 dashboard/）；② 保留 `tools/training/train.ts` 兼容壳
  转发到新路径——AGENTS 铁律「不留读兼容后门」的反面，双份入口必然漂移；③ 在
  `dashboard/` 里复制 `src/` 的游戏配置契约——单一事实源被复制，漂移即错关卡/错门禁。
- **边界与遗留**：根 `tsconfig.json` 的 `include` 增加 `dashboard`（`bun run check` 仍是一
  条总闸：tsc 覆盖 dashboard + 根 `bun test` 连带跑 `dashboard/tests/`，实测 2114 用例）
  **_（2026-09-14 同日反转：这三项是搬迁期过渡态，已被下一条追加取代）_**；
  `tools/test-silent.ts` 的兜底全量遍历目录表补 `dashboard`；`.gitignore` 的
  `console/.build/` 与 evalboard 数据根改指 `dashboard/`。**历史记录类文档不改**：
  `docs/*.progress.md` 与 `plan/*.md` 记的是当时的事实，改写等于伪造历史；权威手册
  （`AGENTS.md`、`docs/agents.details.md`）与可直接复制执行的操作文档
  （`docs/goal-nn-*.md`、`nn-training/README.md`、python docstring）已全部改到新路径。

- **追加（2026-09-14，同日，用户指令：「给 `dashboard/` 做自己的 `bun install`，让它的依赖
  不再依赖仓库根的 `node_modules`」）—— 依赖与门禁彻底切断**（上一条 bullet 中三项过渡态声明
  至此全部反转）：
  - ① **依赖**：dashboard 自带 `node_modules/` + **入库**的 `bun.lock`（`.gitignore` 给
    `!dashboard/bun.lock` 开例外，与 `!nn-training/uv.lock` 同一逻辑：`*.lock` 本意是运行时
    PID 锁文件）；根 `package.json` 删除只有 dashboard 用的 `preact` /
    `preact-render-to-string`（根游戏代码零 `preact`、零 `.tsx`，32 个 `.tsx` 全在 dashboard/）。
    **`bun install` 只更新 lockfile、不删已装目录** —— 陈旧目录仍可被解析，等于留后门，
    必须 `rm -rf node_modules/{preact,preact-render-to-string}` 才真切断。
  - ② **门禁边界**：根 `tsconfig.json` 的 `include` 移除 `dashboard`；根套件改用
    `bun test --parallel --timeout=50000 --path-ignore-patterns='dashboard/**'`。教训：**位置参数
    是子串过滤**，`bun test tests` 会把 `dashboard/tests/` 一并跑掉（实测 194 文件）——
    排除目录只能靠 `--path-ignore-patterns`。`tools/test-silent.ts` 的 `SKIP_RE` 加 `dashboard`
    （并删掉兜底遍历表里的 `dashboard`），另加一条分支：**改动全在 `dashboard/` 下 ⇒ 不
    fallback 全量**（否则只改 dashboard 的提交会白烧一整轮根套件，撞上 spawn 真实 CLI 的慢测试）。
  - ③ **门禁不降级**：pre-commit 新增 **dashboard 门禁块** —— staged 含 `dashboard/` 时跑
    `cd dashboard && bun run typecheck` + `bun run test`；tsc 报错前缀 `dashboard/` 后与 staged
    求交（dashboard 内 tsc 输出的是相对它自己的路径，漏了前缀归因永远为空 → 红会被当「他人
    未暂存改动」放行），`--selftest` 已加该归因断言；逃生口 `SKIP_DASHBOARD_GATE=1`。
    没有这一块，「把 dashboard 切出根门禁」会静默退化成「没人跑 dashboard 门禁」。
  - ④ **验证（双向硬证据）**：dashboard `tsc` 干净 + **306 pass / 0 fail**（20 文件）；根
    `check` **1807 pass / 4 skip / 0 fail**（174 文件，日志零 `dashboard/tests/`）；
    忽略前后的 A/B 差值 = **306 用例 / 20 文件**，与 dashboard 独立套件完全一致（排除精确，
    无过杀）；146 个 dashboard 源文件用 `Bun.Transpiler.scan()` 取裸包名逐一解析 **零泄漏**；
    根解析 `preact` 失败而 dashboard 正常（供给关系已反转）。4 个 skip 是环境型
    （`it.skipIf(files.length === 0)`，沙箱 `replays/` 无生成物），非覆盖丢失。
  - **拒绝的替代方案**：只做 `bun install` 不做切断（根仍留 `preact`、根 check 继续覆盖
    dashboard）——两份依赖重叠，dashboard 文件仍能从根 `node_modules` 回退解析，「独立项目」
    是名义上的；以及「切断但不加 pre-commit 门禁块」——hook 更简单统一，但代价是 dashboard
    改动提交时不再被任何自动门禁覆盖（静默降级），用户选择不承担。
  - **仍然保留的耦合（设计，非缺陷）**：dashboard 与根之间仍有**源码级**相对 import
    （`src/config/*`、`tools/agent/*`、`tools/eval/mcnemar`、`tools/sim/pack-container`）——
    观测面只读消费游戏契约，权威唯一源在根，复制进 dashboard 才是真错。切断的只有包依赖方向。

## §2026-09-15-goalnn-gate-pooled（2026-09-15，用户拍板）

- **背景**：多关课正式门跑 200/关（x2 两轮 2400 局）vs roadmap D4 pooled 400 局——量级差 6 倍，“x2 的 80%”与单关课的 80% 不是同一个量（对账结论）。
- **备选与否决**：分关数字进判据 —— 否，分关 SE≈8pp 全是噪声且会掩盖性误杀单关波动；纯 400 局 pooled —— 否，分关诊断能力归零，bc 式偏科（143→132→120）将不可见。
- **决定**：多关课门按 200/关跑足量，**判据只读 pooled**（点估计≥80% 且 Wald 95% LB≥77%，n=800 时 LB≈点估计−2.3pp）；**分关永远只诊断**（配哨兵报告，不进判据）。
- **违反后果**：分关进判据 ⇒ 噪声卡毕业；只跑 400 ⇒ 偏科瞎眼到毕业才爆雷。

## §2026-09-15-goalnn-xn-absorb（2026-09-15，用户拍板 B 案）

- **背景**：同 count 三套任务定义并存（v2 cN / 工厂 ladder-cNN / 手工 xN+arenaN），
  数字不可比、维护×3。工厂输出跑不起来：v2 残留奖励（wTick/wPickup/wStuck，
  codex 明禁）+ 单 ab 循环零类型覆盖 + BC 权重文件不存在——xN 是等不起才无尘室重造。
- **备选与否决**：A（xN 转正、工厂废弃）—— 否，I3 自动生成是立项基建；
  C（长期分工）—— 否，数字永不对账，等于把失血合法化。
- **决定**：工厂模板吸收 xN 语义——c01-c03 多变体（C(4,1/2/3)=4/6/4 关）+
  干净奖励（与 x2/x3-start 逐字同构）；c04+ v2 词干逐字节不动；
  seed_rotate 保持 600（xN 的 240 不吸收）；ladder-c02/c03 与 arena2/3 逐关
  同形由单测钉死；xN 转试点存档（权重链保留，探针表头仍住 xN 文件，JSON 无注释位）。
- **违反后果**：再手造第四套任务定义 ⇒ 对账地狱；改 c04+ 奖励不经单独立项 ⇒
  17 级语义漂移无人察觉。遗留债：c04+ v2 残留奖励、BC 腿权重缺失、工厂探针表头无位。
- **追加（2026-09-15）：arena2/arena3 已退役删除**（与 ladder-c02/c03 除名字外
  逐字节同、顺序一致；x2-start/x3-start/x3-power 的 `level` 已切过去，stage ID
  段不变；`test_arena_retired_single_source` 锁死不再复活；arena2-acbc 是偏科
  子集、无工厂等价物，保留）。
## §2026-09-15-goalnn-local-ppo-worker（2026-09-15，本机 PPO 拆成独立 worker：控制台 `local` 预设 = hub-server + local-worker + trainer；用户指令）

- **背景（用户指令）**：「把本地 PPO 拆分为一个独立 worker，可以随时启停，与云端 worker 一致，
  同样支持 pull/push 模式。」此前 `--ppo local` 是**进程内**执行：`TrainingLoop`（run_rl）在自己的
  进程里 `load_episodes → chunk_episodes → ppo_update`，整轮阻塞——PPO 不能单独停、不能单独换代码、
  不能挪到另一台机器；而云端 worker（`remote_worker`）早就是无状态、可重连、可随时启停的独立进程。
- **决定（三条，用户逐项拍板）**：
  - ① **本机 PPO = 新的受管组件 `localWorker`**，跑的就是**云端同一个入口**
    `python -m remote_worker --poll http://127.0.0.1:<本课 hub> --out tmp/local-worker-<课>
    --device cpu`（+ 配了 `rl.torch_threads` 才透传 `--threads`）。协议、租约、心跳、幂等重拉、
    热替换退出码 86 + 内部监督器重拉**全部继承、零分叉**（用户选「完整继承」）。push 侧不新增实现：
    执行面就是既有 `workerServe` 组件（`remote_worker_serve`）。
  - ② **控制台 `local` 预设改义**：`hubServer → localWorker → trainingLoop(--ppo remote
    --remote-transport pull --remote-hub-url 本机 hub)`，并新增 `--remote-transport` 开关（run_rl 与
    run_bc 同步）把传输**钉死 pull**；控制台**不再提供进程内 PPO 入口**（run_rl `--ppo local` /
    run_bc `--local` 保留给直调 CLI 与 R9 远端失败降级落点，只是不再被预设编排）。
  - ③ **整树停止**：`localWorker` 是「父 supervise_worker + 子 worker_loop」两进程，停止/重启走
    `core/net.ts::killPidTree`（Windows `taskkill /T /F`；POSIX 先验 `pgid === pid` 再组杀，否则退回
    单进程 stop），判定唯一来源 `stack/specs.ts::COMPONENT_KILL_TREE`。
- **为什么必须 `--remote-transport pull`（本次唯一的新语义，也是最贵的一个坑）**：`_remote_ppo` 的
  历史优先级是「rl-config 里本课 `gpu_push` 节点 > hub」——某课用 push 跑过一次后
  `courses.<课>.push_node_url` 就留在配置里，此时 `local` 预设会把 job **静默推去云机**，本机 worker
  永远领不到活，而账本/日志看起来「训练正常」。`auto`（默认）保持历史行为零变化，`pull`/`push`
  是显式裁决，非法组合响亮 `SystemExit`（不静默回落）。
- **拒绝的替代方案**：① **不新增组件，只给 `workerServe` 加 pull 能力**（用户裁定的备选）——
  同一个进程既被推送又轮询会让「push 服务端」与「pull 轮询者」的生命周期/健康语义混在一起，
  而两者的失败形态与日志完全不同（无法一眼定位）；② **新增 `local-pull`/`local-push` 预设、
  `local` 保持进程内**（用户否）——两套本机 PPO 语义长期并存，等于把「已拆干净」这件事半途而废；
  ③ **控制台把 `courses.<课>.push_node_url` 清空来实现 pull**——该键是 push preset 的用户配置，
  静默改写别人的配置是比多传一个旗标严重得多的事故面；④ **让本机 worker 也走 `--local` 的进程内
  回退**——那就没有「随时启停」，正是本次要消灭的东西。
- **刻意不做的事**：不动 `rl.remote_hubs[<课>]`（它是 pull preset 隧道 URL 的家，`stepCloudflared`
  复用已建隧道时**不会重写**它，写本机 hub 进去会把 pull preset 悄悄改成打本机 hub）——本机 hub
  只经显式 `--remote-hub-url` 注入；不动 pull/push preset 的传输语义（`auto`，行为零变化）；
  不动 `rl/loop_steps._serial_ppo`（R9 降级与直调 CLI 仍走进程内路径）。
- **副产物（顺手修的一处既有红）**：`dashboard/tests/training-selfnode-cwd.test.ts` 在 HEAD 上就是
  红的——`selfNodeSpec` 缺 `cwd: REPO_ROOT`，控制台以 `dashboard/` 为 cwd 启动时
  `bun run tools/agent/sampler-agent.ts` 会 Module not found（2026-09-14 回归的护栏先落了红测试）。
  本次补上该字段（一行，spec 与测试同时在线）。
- **违反后果**：local 预设若不钉 pull ⇒「训练在跑但 PPO 其实在云机」的静默错位（最贵的一类）；
  停在 localWorker 上若不整树杀 ⇒ 孤儿 worker 继续轮询 hub 抢 job、抢租约，「随时启停」名存实亡；
  把本机 hub 写进 `rl.remote_hubs` ⇒ pull preset 的隧道 URL 被偷偷改写，云机再也领不到 job。
- **追加（2026-09-15，用户指令：「让 push 模式也能一键用本机 worker_server 作执行面（config
  无 gpu_push 时自动回落到本机 workerServe）」）—— push 的第三种执行面来源**：
  `configurePushEndpoint` 改为三档裁决，返回 `PushTarget {url, source: manual|config|local,
  viaLocalWorker}`：① 用户填 endpoint（ping 门 + upsert **云**节点）；② 留空且 config 有 ping 通的
  gpu_push → 只写课程 `push_node_url`；③ 都没有 → **回落本机 worker_server**：写 `nodes[]` 的
  `local_push` 节点（`gpu_push: true` + `local_push: true`，authKey = `rl.remote_token`，与本机
  `worker_server --token` 同源）+ 课程 `push_node_url` 指向 `http://127.0.0.1:<push 端口>`。
  预设顺序在 `viaLocalWorker` 时多一步 `workerServe`（本机 worker_server 是受管组件）。
  - **为什么必须落 config 而不只注入 `REMOTE_PUSH_NODE` env**：python `_gpu_push_nodes` 的
    gpu_push 分支要求 `push_node_url` 能**匹配到一个节点**——只给 env 而课程键仍指旧/空，
    会走到「匹配 0 个 → WARN → 回落 pull」，而 push 模式没有 hub（`wait_job` 空 URL）直接卡死。
  - **与云节点共存（拒绝的替代）**：直接复用 `applyPushNodeConfig` 覆盖唯一的 `gpu_push` 条目
    更简单，但会把用户填的云 URL 静默吃掉（本仓反复出现过“静默改写别人配置”类事故）。故云/本机
    各一份条目：`applyPushNodeConfig` 的 findIndex 加 `!n.local_push`，回落也从不碰云条目；
    `enabledGpuPushNodes`（复用门/健康探测）**含**本机节点——它同样是真执行面。
  - **`workerServe` 启动改幂等**：回落/复用路径下它可能已经活着，旧实现对每次「启动」都
    kill+重起，会把正在跑 PPO job 的 server 当场打死（还会撞端口）；改为「账本存活或 `/ping`
    通 → 已在运行，不动它」——与 hubServer/trainingLoop/localWorker 同规。
  - **验证**：dashboard `tsc` 干净 + **358 pass / 0 fail**（含 push-config 新增 7 用例：返回
    `source=local` 且写盘 URL 能被 python 过滤命中、两种执行面共存互不覆盖、`allowLocal:false`
    仍响亮报错、预设把 `workerServe` 排进顺序、workerServe 启动幂等的源码级接线门禁）。
- **追加（2026-09-15，用户指令：「把『本机 push 执行面』的当前指向显示出来——卡片上标出本课 job
  现在推给本机还是云机」）—— 执行面可见化**：新增 `pushTargetFromConfig(cfg, course)`（纯函数；唯一指针 =
  `courses.<课>.push_node_url` → 认领 `nodes[]` 条目 → `local | cloud | unresolved`）；慢快照做一次
  `{url}/ping` 直探（`PushTargetProbe{kind,url,nodeId,healthy}`，1500ms，后台刷新同一拍，不进请求路径）；
  `buildStateView` 再补 `active`（trainer 正以 push 模式在跑）。UI = trainingLoop 卡模式徽章旁的
  `tc-cc__push` 徽章（绿=本机 worker_server / 蓝=云 GPU / 红=未匹配；`--idle` = 当前未以 push 在跑，
  只是「配置指向」）。
  - **为什么直探 `/ping` 而不复用节点 pill 的 `/v1/ping`**：判据必须与 python `_gpu_push_nodes` 同一条
    URL——复用会造成两个真相（节点在线 ≠ 能被 push 到）。
  - **`unresolved` 必须显式上屏**：`push_node_url` 指向 config 里不存在的节点时，python 侧「匹配 0 个 →
    WARN → 回落 pull」，而 push 模式无 hub（`wait_job` 卡死）——旧界面看起来完全正常，这正是本次可见化
    的动机。
- **验证（2026-09-15，执行面可见化）**：dashboard `tsc` 干净 + **366 pass / 0 fail**（新增
  `pushTargetFromConfig` 5 用例 + `buildStateView.pushTarget` 快照 1 用例 + SSR 徽章 2 用例）；
  `bun dashboard/src/server/build.ts` 三份 bundle 通过（app gzip 51016B）。
- **验证（2026-09-15）**：dashboard `tsc` 干净 + **358 pass / 0 fail**（59 文件，含追加上面的
  push 回落 7 用例后计数；本条目首落时 351；含新增
  `tests/local-worker.test.ts` 11 用例：spec 形态/poll 目标/killTree/双课隔离/`--remote-transport pull`
  注射/重建逐字段一致/接线 grep 门禁）；根 `bun run check` **1819 pass / 4 skip / 0 fail**；
  nn-training python gate（ruff + mypy + pytest xdist -n 4）绿，含新增
  `tests/test_remote_transport.py`（run_rl 与 run_bc 两侧裁决 + argparse 接线）；
  `bun dashboard/src/server/build.ts` 三份 bundle 与根 `bun run build` 均通过。
## §2026-09-15-goalnn-dynamic-rollout-volume（2026-09-15，采集配额量纲从「局」切「transitions」；plan/dynamic-rollout-volume.plan.md §2）

- **背景**：PPO 吃的是 transitions 而不是局数（x3 240 局 × 967t ≈ 23 万/轮；c4-dodge 600 局 × 1100+t
  ≈ 66 万+/轮），而课程至今用 `seed_rotate`（每关局数）配采集量——固定局数下 transitions/轮随局长漂移
  （x3 局均 700–1150t），PPO 更新量与 advantage 归一稳定性跟着漂。反例在先：x2-acbc 3 倍密度 30 轮
  pooled −3/400 ⇒ 加量解决的是方差、不是信号；**本决策只承诺「量准」，不承诺涨点**（DoD 里不许写胜率条款）。
- **决定**：课程新增三键（全可选；**缺席 = 老行为逐字节不变**）：`target_transitions`（/轮，口径 = 已结算
  shard 的 `nSamples` 之和）、`est_ticks_per_game`（首轮反解局数用，之后由 jsonl trailing 均值覆盖）、
  `max_games_per_stage`（0 = 默认「初波 × 4」）。语义：分关达标线 `ceil(target/关数)`；初波
  `G0 = max(1, ceil(关达标线/est))`；结算后**逐关独立**补波 `ceil(缺口/est)`，每关至多 3 波；掉局零样本不计
  （天然触发补采——特性）、超时局计入；触硬顶 = 停采 + 响亮日志 + iteration 事件 `transitions_capped`。
  纯逻辑住 `rl/volume_waves.py`，账本口径 `rl/resume.settled_stage_totals`，接线 `rl/loop_core.py::_volume_topup`；
  iteration 事件追加 `transitions_target` / `transitions_collected`（additive，旧行无此键）。
- **两条派生偏离计划的地方（本决策的实质）**：
  1. **种子流按 (stage, wave) 独立**，不是「初波沿用今日单条顺序流」。计划 §2.2.1 说「种子流与今日
     `build_pairs` 同键（rotateSeed, it）」——取**同键族**（同 rotate_seed/it、初波同 tag 0x5EED）
     但按 stage 拆独立流。理由：单流下「每关抽几签」会随 est/配额变化而移动**后续关**的流位置，
     §2.2.5 的跨关独立性（给 A 关加波不改 B 关任何一局种子）直接失守。带 key 的课程本就是新实验
     （§15.5 要求 fresh `--out/--traj`），不需要与老流逐字节同构。
  2. **身份键条件进 payload**：仅当 `target_transitions > 0` 才把 `volume_rule`(=VOLUME_RULE_V1) +
     `target_transitions` 加进 `corpus_identity_fp`。无条件加会让**所有**既有课程指纹一起漂移
     （D14 血缘断裂、在跑的腿把已落盘 shard 判成异身份、云端 job 全拒），与「缺席 = 老行为逐字节不变」
     直接矛盾。`est` / `max_games_per_stage` 刻意**不进**（同 iters/max_hours 分类学：估计与硬顶不是语料身份）。
- **被否决的备选**：① 走**折中版**（轮首按 trailing 均值反解局数，x3-power「批量附录」）——保留为回落线，
  但仍是「一轮一锤子」，est 偏了整轮就偏；② 全局池配额（不分关）—— 否，短局关淹长局关
  （x3 acd 733t vs abd 989t 差 35% 就是前车）；③ 让补波也走 stream/双缓冲路径 —— v1 不做（动那套墙钟优化
  收益为负、风险为正），stream 课程本轮只按初波结算并**写日志说明**；④ 补波决策靠 WAL 重放 —— 否，
**首版确实这么想过，被 P2 的 e2e 证伪**：波次序号 `wave_idx` 是决策而不是账本的函数
  （它同时是种子流的键），只按账本重算会在重启后把计数器拉回 1、用 wave-1 的种子补 wave-3 的缺口。
  现设计：纯函数负责「给定 (账本, wave_idx) 算同一波」，`wave_idx` 本身从 commit journal 回读
  （预算续算 + 停在波中的那一波原样重放）。
- **违反后果**：把 `target_transitions` 无条件塞进 `corpus_identity_fp` ⇒ 全库血缘断裂、云端 job 全拒；
  把 volume 初波改回单条顺序流 ⇒ 跨关种子耦合，补波会静默改掉别的关的对局；补波不看硬顶就无限补 ⇒
  短局关吃光墙钟（`max_hours` 是最后一道闸）；给 volume 课程硬配 curriculum/rotate 门控窗口 ⇒ 配额分母
  与真实采样关不符（代码响亮 SystemExit，**勿放宽成静默取一个关集**）。
- **验证**：单测 46 用例（配额数学 / 跨关独立性 / 终止优先级 / 硬顶截断 / WAL 解析 / 冻结 `build_pairs` 摘要 /
  桩 self 调 **真方法** 的接线）；P2 e2e 10 用例（`e2e/test_volume_e2e.py`：真调度器落地真 shard——定额收敛 /
  跨重启重放 / 长短局独立 / 掉局补采 / 硬顶打标 / **尾部竞速 4 例**：副本不污染账本、double-settle 不重复计数、
  丢局被补回、预算耗尽要响亮）；nn python gate + 根 `bun run check` 绿。**遗留（调度器侧，未修）**：同节点 fan-out
  副本共享 `out_dir`，double-settle 的 rmtree 连赢家数据一起删（报告仍记 `ok=N/N`）——单独立项修，取证见 §44。
- **未做（勿当已验）**：微课 5 轮试点与补波 overhead 实测（需真实 bun sim / 训练，未跑）；
  dashboard 展示 `transitions_collected/target` 未动。
- **修订（2026-09-15 T9，plan/x3-power-followup §T9 的「二选一并写明」已定）**：第二键
  **改名 `est_ticks_per_game` → `est_samples_per_game`**，值 = 局均 ticks × (samples/ticks)
  = 局均 ticks / K（x3 实测 0.1007 ⇒ 980 ticks ≈ 98 samples）。旧名把 ticks 填进 samples 分母
  ⇒ 初波只反解出目标的 ~⅒，补波按同一错估缩放、补满 3 波仍不达标（评审「兑现 37% 触顶」用例）。
  **不取另一选路「公式显式 /K」**：K 是 exporter 侧实现细节，课程文件不该编码它（K 一变
  所有课程文件都要改）；键名自带单位 + `extra="forbid"` 使旧键照写即启动期响亮报错，静默复辟不可能。
  同批修三处 ticks-as-transitions 文档，并让 `trailing_samples_per_game` 只读 jsonl 的 `samples`
  （不读 `ticks`）——两半缺一即「首轮对、第二轮起 10×」；单测「① T9 量纲钉死（600000/4/980）」
  把新（一波达标）/旧（补满 3 波 < 1/3）两读数一起钉死。

## §2026-09-15-sandbox-precommit-immunity（2026-09-15，用户指令：杜绝编码 agent 删除保护沙箱反复拦截 pre-commit / 反复弹删除审批；hook 进程树级免疫 + 零删除纪律）

- **背景**：WorkBuddy / Mimo 类编码 agent 自带 python 运行时 + 删除保护沙箱，经两个载体注入：
  **bash 函数**（BASH_ENV 指向 safe-delete-bash-env.sh → 每个**子** bash 启动时把 rm/unlink/rmdir
  重新包装成函数，带每回合删除配额 ~50）与 **python sitecustomize**（守卫 os.remove/shutil.rmtree）。
  后果：mypy 自清理 / pytest 临时目录被塞 SystemExit → INTERNAL ERROR 假红；hook EXIT trap 的
  rm 撞配额 FAIL_CLOSED → 门禁全绿提交却被 git 静默丢弃（2026-09-14 实测 count=181/182；
  2026-09-15 Mimo 再犯：反复重试 commit + 反复弹删除审批）。旧防御只在本 shell `unset -f rm`——
  子进程经 BASH_ENV 重新注入后照拦不误；python 侧开关只写进注释让「调用方设」= 等于不设。
- **决定（三管齐下，只对本 hook 子树生效，不碰宿主环境）**：检测到注入载体（名字含
  CODEBUDDY/SAFE_/BUDDY/SANDBOX 的环境变量，或 BASH_ENV/ENV 含 safe/delete/buddy/runtime）即拉闸：
  ① BASH_ENV/ENV 指向 /dev/null + unset 全部注入变量 → 子 bash 拿不到包装函数与配额配置，
  删除走真 `rm`（配额彻底失效）；② CODEBUDDY_SAFE_DELETE_ENABLED=0 + PYTHONNOUSERSITE=1 →
  python 侧 sitecustomize 守卫对整棵子树（mypy/pytest/ruff 及其自清理）整体关闭；
  ③ PATH 滤掉命中 safe-delete/safe-bin/codebuddy/shell-runtime 的目录 → 直接可执行 shim 也无处可寻。
  **铁律追加（hook 零删除纪律）**：hook 自带临时文件清理一律「截断不删除」（`: > file`），
  删除 syscall 归零——截断是写不是删，任何删除拦截都对其失效；残留 0 字节文件在 gitignored
  tmp/ 由 periodic 清理兜底。权威文档 = `tools/githook/pre-commit` 顶部注释。
- **被否决的备选**：① v1「本 shell unset -f」—— 已被实测证伪（子进程重注入）；②「exec 重执行 +
  env -u 剥离」—— 引号/递归复杂度不值，BASH_ENV=/dev/null 更简单且等效；③「让调用方自己设
  CODEBUDDY_SAFE_DELETE_ENABLED=0」—— 调用方正是肇事 agent，指望它们设等于不设。
- **违反后果**：hook 再出现删除 syscall（trap/selftest 里的 rm 等）或依赖 agent 自设开关 ⇒
  沙箱弹审批/配额重演、agent 反复重试 commit（本次要消灭的循环）；child 进程再被 BASH_ENV
  注入 ⇒ mypy/pytest 假红回归。
- **验证**：hook `--selftest` 绿；伪造 BASH_ENV 注入文件（真定义 rm() 包装函数并 export -f）+
  CODEBUDDY 标记变量跑 selftest → 拉闸行出现且三层子进程（self/child/grandchild）rm 均为真身、
  载体变量已清、真实删除可用（无配额/无审批）；根 `bun run check` **1825 pass / 3 skip / 0 fail**。
- **追加（2026-09-15，用户提问「coding agent 直接跑 python.exe -m pytest xxx 也会被拦截吗」
  ——**会**）——把 python 入口也一起处理**：删除拦截发生在 **python 进程内部**
  （os.remove / shutil.rmtree / pathlib.unlink 层，由启动时加载的 sitecustomize 守卫实现），
  bash 的 rm() 包装覆盖不到——`python -m pytest` 的 tmp_path 清理、测试内删除一删一个准
  （本机实测：两个解释器 site-packages 都**没有**烘焙 sitecustomize，注入是**环境变量驱动**的，
  沙箱 env 随进程树带下来才激活，所以整树净化可治）。免疫做成三件套：① 共享实现
  `tools/githook/_sandbox-sanitize.sh`（**唯一事实源**，pre-commit 改为 source 它，禁止复制粘贴漂移；
  幂等 SANDBOX_SANITIZED 守卫）；② 新增 python 启动器 `tools/githook/nn-py-safe.sh` ——
  **先拉闸、再 exec 指定解释器**（默认 nn-training/.venv，NN_PY 可覆盖），语义与裸
  `python.exe …` 逐字节一致，agent 跑 pytest/任意 python 一律经它；
  ③ `CODEBUDDY_SAFE_DELETE_ENABLED=0` + `PYTHONNOUSERSITE=1` 改**无条件设**（惰性变量，
  普通机器无害）——不再依赖「调用方自觉设」。**验证口径**：bash 层三层免疫在注入模拟下
  逐层为真（self/child/grandchild rm 均为真身、变量已清）；python 内部 env 在本验证宿主
  （bash→Windows exe 边界剥环境）无法直视，靠「沙箱 env 能传进去就能传开关出来」+ 既有
  `CODEBUDDY_SAFE_DELETE_ENABLED` 文档语义成立。**违反后果**：agent 再裸跑 `python -m pytest`
  ⇒ pytest 假红/弹审批重演；有人在别处复制粘贴拉闸逻辑 ⇒ 二处漂移，任一失效用户都得重推一次。

## §2026-09-15-goalnn-x3-power-negative（2026-09-15，x3-power 判负派生的三条口径令：判读禁令 / transitions=samples / wChip 破规程序）

- **背景**：含 power 关恒差 15-20pp 被归因「伤害摊薄零代价」，x3-power 用**杀/中信用比**
  陡峭化（wKill 3.0→4.0 / wHit 0.3→0.15，即 10:1→27:1）单变量代理 30 轮。终局同批配对
  （池外种子 400000-400199，四关各 200）：**553/800 = 69.125% vs 基线 576/800 = 72.00%**，
  pooled **Δ=−2.875pp**，McNemar 单侧 p=0.9998（双侧 p=0.0011，净 −3.35σ），pd=5.9%
  ⇒ 判线失败、**判负**。分关/伴随量/事件账全文在 `docs/nn.progress.md` §46。
- **本条目立的是该腿派生的三条口径/程序（实验记录本身不进本文件）**：
  1. **判读禁令**：KL it30=0.00213（`kl_cap` 本路径不接线，见 §2026-09-15-goalnn-kl-cap-unwired）⇒ 结论的**唯一合法写法**是
     「**梯度无方向 / 执行瓶颈**」（指向 metrics v6，分敌种命中列 TS+Python 全链）；
     **禁**写「信用比无效」（须 KL≥0.01 而 Δ≈0 才成立）与「信用比有害」
     （单腿 −2.9pp 可来自起点游走 + 目标关噪声，且四关同降 ≠ 因果）。
  2. **量纲令：`transitions = samples`**（`rl/resume.py::settled_stage_totals` 自声明「nSamples
     之和」，ticks 只是 clocks，K=10 降采样）。一切「X 万 transitions」规划按 **samples/局 ≈ 97**
     重算（本腿实测 samples/轮均值 23343 ≈ **2.33 万 transitions/轮**）；旧「23.5 万 transitions/轮」
     = ticks，**10×，作废**（c4-dodge 的「66 万+」同理 ≈6.6 万）。同错已蔓延四处
     （`curricula/x3-start.jsonc:66`、`_example-custom-stage.jsonc:77`、
     `plan/dynamic-rollout-volume.plan.md:26`、`rl/volume_waves.py` 分子 samples 配分母
     `est_ticks_per_game`）⇒ 单独立项修（缺陷单 T9，钉「600000/4/980 语义」单测）。
  3. **wChip 生存腿程序占位（批准后激活）**：wChip 上 1 命早期关**破** roadmap N3
     （「c01-c03 承伤基数极小不上 wChip」，§5.3 表 c01 行同引 R5）；实测局均 dmg ~97–124
     已证伪「基数极小」前提，且败局死因正是挨打（败 dmg≈196 vs 胜≈94）。**激活三者缺一不可**：
     用户明示批准 + 本条破规条款落地 + 单变量课程（`wChip=k/97`，k∈{0, 0.5, 1.0}→{0, 0.005, 0.010}，
      每臂 30 轮，范围仅早期关；c4 的 0.03 翻车案在前故零头起步）。**未获批前本占位不生效**
      （勿据此开腿）。
      **已激活（2026-09-16）**：用户明示批准开课 T6，三门已齐（T3 门控过＋批准＋本条）。
      课程 `nn-training/curricula/x3-chip-k{0,05,10}.jsonc`（wChip＝k/97，k∈{0,0.5,1.0}→
      {0,0.005,0.010}，相对 x3-power 唯一训练变量＝奖励加项；同 bc/同 ladder-c03/
      同日程；verdict 共用冻结基线 576/800）。破规范围仅早期关 ladder-c03。
- **三问门（通过）**：① 被否决备选——见下；② 未来再犯——量纲错**已实际蔓延四处**（下一腿
  照抄即 10× 缺口，volume_waves 已跑出「兑现 37% 触顶」用例），判读外推会错误回滚 x2/x3 全系
  奖励语义；③ 无法就近表达——跨 4 个文件的量纲口径 + 一条铁律级破规程序，无单一代码位置承载。
- **被否决的备选**：① 把判负写成「信用比有害」并回滚奖励语义 —— 否，单腿显著≠因果，预注册
  只允许判「梯度无方向」；② 继续调 wKill/wHit 剂量 —— 否（同剂量轴第二腿，先做 metrics v6）；
  ③ wChip 直接开腿、不立案 —— 否，破 N3/R5 须留可追溯破规记录，审批是人的动作。
- **违反后果**：按 ticks 口径规划采集量 ⇒ 下一腿照抄 10× 误差、配额缺口 90%；把判负外推成
  「信用比有害」⇒ 错误回滚奖励语义、丢掉「执行瓶颈」这一真信号；未立案开 wChip ⇒
  N3/R5 被静默架空，日后无从追溯何时因何破了哪条规则。

## §2026-09-15-goalnn-kl-cap-unwired（2026-09-15，x3-step 评审 P0-1：串行/远端路径 kl_cap 不接线）

- **背景**：`ppo_schedule` 的 `kl_cap` 在 per-tick **remote/serial** 执行路径**不接线**——
  `ppo/engine.py::ppo_update` 无形参；`remote/worker.py` 只读 `kl_coef`；全仓消费者仅
  `rl/stream.py`（stream 波次闸）与 manifest 打包。x3 线 `stream=0`（remote 强制）⇒
  字段写了也不生效。`p4-fast.jsonc` 已写对；但 x3-step / x3-power 结算 / `nn.progress.md`
  §46、§19.1 仍把「kl_cap 从未咬合 / 回落兜底」当成生效护栏叙事。
- **备选与否决**：A 继续当护栏写 —— 否，机制假、后腿会按「失去护栏」解释第二段；
  B 引擎接线硬顶 —— 否，属新训练变量/算法变更，须另立项，本条只钉口径；
  C 只改课程注释 —— 否，跨文件反复误用，须 DECISIONS 禁令。
- **决定**：**禁**在串行/远端课程、结算、progress 中把 `kl_cap` 写成生效硬顶或「咬合」；
  本路径生效旋钮只有 `kl_coef`（软惩罚）+ `lr` + F4 `KL_BREAK`。stream 路径才读
  `args._kl_cap` / `policy.streamKlCap`。x3-power「kl_cap 从未咬合」改读为
  **「该键本路径未接线，谈不上咬合；KL≈0.002 由 kl_coef+小步长决定」**。
  判负八字判决（梯度无方向/执行瓶颈）本身不受影响。
- **违反后果**：后腿把「第二段无 kl_cap」当成第二变量或「失去紧护栏」⇒ 错误解释 KL
  不升的原因，继续烧 2–4h 腿；或反向去「修」一个根本没接线的键。

## §2026-09-15-goalnn-r9-default-abort（2026-09-15，T7：远端连败默认 ABORT，降级本机 opt-in）

- **背景**：x3-power it1 远端连败触发 R9 自动降级 → `None.load_episodes` ×3（remote
  模式 D2 把 `ppo_backend` 置 None，降级只改 `args.ppo` 未建栈）。用户拍板：**不**默认
  静默降级到本机；启动界面提供开关，**默认关**。
- **备选与否决**：A 只修 None bug 仍默认降级 3 —— 否，远端失败应响亮停腿，静默切慢速
  本机会把事故吞掉；B 维持旧默认 —— 否，已打穿过一次；C 直接删 R9 —— 否，opt-in
  仍有价值（长腿/无值守）。
- **决定**：`--remote-degrade-after` **默认 0**（连败 3 次写 `gate_verdict: ABORT` 停腿）。
  N>0 为显式 opt-in：控制台启动弹窗「降级本机」（localStorage + registry 复现）→
  `--remote-degrade-after 3`；降级前必调 `loop_core._ensure_local_ppo_stack()` 懒加载
  torch/model/opt。
- **违反后果**：再默认降级 ⇒ 静默切慢速本机 + 无栈时 None crash；不建栈就改 ppo=local
  ⇒ x3-power it1 事故复现。
## §2026-09-15-gate-trigger-scope（2026-09-15，用户提问「tools 改动有必要跑 src 的测试吗 / src 改动能不触发 dashboard 吗」⇒ 三处触发面重排）

- **背景（全部实测）**：
  1. 根 `bun run test`（`tools/test-silent.ts`）按 basename 把改动映射到同名测试，**命中就只跑那几个**：
     `src/config/stages.ts` → 1 个（而 **50** 个测试直接 import 它）、`difficulty.ts` → 1（39 个）、
     `combat.ts` → 3（13 个）⇒ 改配置类文件能绿着过去而 49 个依赖测试一个没跑；
     **映射为空才 fallback 全量** ⇒ 反倒比命中更安全。非 heavy 全量实测 **6s**、tools 子集 5s。
  2. freeze 触发按扩展名判（`\.(ts|js|mjs|cjs|tsx|jsx)$|^src/`）⇒ `tests/**` 与 `src/assets/**` 也各付
     ~100s。**（2026-09-15 当日实测更正：实为 ~3.7s／21 组合全网格（16 核 Linux），旧数字错 ~27×。
     成本现由 `tools/probe-det-baseline.sh` 与 pre-commit 自报 elapsed；豁免的**安全论据（签名闭包）
     不受影响**，但收益从「百秒级」降为「秒级」——是否还值得留这套豁免（及其 freeze-scope 断言）
     属独立待决项。）**
  3. `dashboard/` 只读消费仓根 10 个模块，却**三条防线同时失效**：hook 只在 staged 含 `dashboard/**` 时
     跑它、根套件 `SKIP_RE` 排除它、CI 里没有它的 workflow ⇒ src/tools 改断契约会静默落地。
- **备选与否决**：保留 basename 窄跑并「修正确性」（补 import 图 / 传递闭包）—— 否，为省 1s 引入一个
  失败模式是**漏跑（假绿）**的启发式不划算；把 `tools/**` 一律豁免 freeze —— 否，签名由
  `tools/diag/per-seed-diff.ts` 生产，探针口径改动**真会**移动签名；把 dashboard 门禁也挂进 hook
  （按消费清单触发）—— 否，那会把「每人都必付」的路径变成依赖清单维护点，CI 才是它该有的节拍。
- **决定**：① `test-silent` 删除 basename 映射与 `--strict`：**代码改动一律跑全量**（仍保留 heavy 排除、
  静默输出、失败单跑取详情，以及「跳过无关改动」= 纯文档/课程配置/dashboard-only）。② `FREEZE_STAGED`
  豁免 `^tests/` 与 `^src/assets/`，理由由 `tests/freeze-scope.test.ts` 钉住（算 `per-seed-diff.ts` 的
  import 传递闭包，断言与这两目录零交集、且非空防假通过）。③ 新增 CI `.github/workflows/dashboard.yml`：
  触发 = `dashboard/**` ∪ 它消费的 10 个仓根模块，清单不得落后于真实 import
  （`dashboard/tests/ci-scope.test.ts` 自动核对，拼错的路径也拦）。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯——「只跑受影响的测试」是每个 agent 都会想做的优化
  （本仓历史上正是这么写的），而「tools 改动要不要跑 src 测试」的直觉也会反复出现；
  ③ 无法就近表达——跨「根 runner 语义 / hook 触发 / CI 范围」三个面，代码注释各说一半。
- **违反后果**：恢复 basename 窄跑 ⇒ 改配置类文件只跑 1 个测试即绿灯（静默漏测）；放宽 freeze 到
  `tools/**` ⇒ 改探针即可绕签名门禁；dashboard CI 触发清单不跟 import ⇒ 契约破坏重新回到静默落地。

## §2026-09-15-heavy-tests-criterion（2026-09-15，用户提问「根套件实测墙钟 ~6s 下，HEAVY_TESTS 名单还有意义吗」⇒ 判据改为实测 + 名单纠错）

- **背景（全部实测，16 vCPU；非 heavy 全量 179 files = 6.1s）**：`HEAVY_TESTS` 的注释一直写着「keep in sync
  with measured wall-time (see per-file profiling)」，但**那份 profiling 根本不存在**，于是两个条目都腐烂了：
  `godai-score-gate` 记 ~19.5s（实测 11.7–13.1s）、`calibration` 记 ~2.5s（实测 **0.66–0.71s**，5/5 稳定——
  它的 full sweep 早已移交 CLI `tools/eval/calibrate.ts`，测试自己写着「小 stage 子集，完整版走 CLI」）。
  逐项代价：排除 `godai-score-gate` 使套件 6.1s → 18.6s（**3.0×**）；排除 `calibration` 只值 ≤0.7s（实测落在
  噪声带内）；次慢的 `nn/intent-rl-rollout`（3.8s/1.7s）剔除也只省 ~0.6s。
- **判据（改为可复核）**：**只排除「单跑墙钟 ≥ 整份非 heavy 套件」的文件**——它一个就抵得上整份套件。
  低于这条线时 `--parallel` 会把它填进既有尾巴，剔除收益 ≤ 它自己的运行时间，却白送一个静默盲区
  （CI **无**根套件 workflow ⇒ 本地 hook 是它唯一的自动化通道）。旧口径「exceeds a few seconds」是错的：
  「几秒」正是排除不产生收益的区间（`calibration` 0.11×、`intent-rl-rollout` 0.3–0.6× 全落在里面）。
- **备选与否决**：① 保留 `calibration` 在名单里——否，省不到 0.7s 而丢掉它的本地覆盖；② 把判据写成一个
  写死的秒数阈值——否，那正是腐烂源（两个数字全被测错）；改为「用 `bun tools/measure-suite.ts` 现测现判」，
  该工具把「每文件墙钟 + 条目达标判定」变成一条命令（原注释引用的 profiling 从未存在）；③ 维持 §1.4 的
  ⚠ 软提示——否，它挂在绿行摘要上没人会读，拦不住名单腐烂（先例：`god-ai-gate` 重命名后残留，§234）。
- **决定**：① 名单只剩 `godai-score-gate`；② 判据与实测数字写进 `tools/test-silent.ts` 的 `HEAVY_TESTS`
  注释与 `docs/agents.details.md` §5.3；③ 新增 `tools/measure-suite.ts`（只读剖面 + 达标判定）；
  ④ `tests/test-silent-scope.test.ts` 增硬断言：条目必须命中真实测试文件、名单非空、名字口径与执行器过滤
  一致且在根套件枚举内（实测：伪造死条目 → 2 fail；复原 → 6 pass）。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯——「CMA-ES calibration 听着就像重负载」会让下一个 agent
  把它加回名单，「多排除几秒无所谓」也会持续压低名单的可信度；③ 无法就近表达——判据跨「runner 注释 /
  测量工具 / 硬断言」三处，且必须推翻 §1.4 记录的软提示口径。
- **违反后果**：往名单里塞低于地板的文件 ⇒ 名单变装饰、覆盖率静默流失；名字腐烂不管 ⇒ 12s 的 score gate
  静默塞回每次提交（提交测试步 6s→19s）；恢复「写死秒数」的判据 ⇒ 数字再次腐烂且无人能发现。

## §2026-09-16-goalnn-x3-step-negative（2026-09-16，x3-step 授权照跑判负：工厂复合第二段禁作可测性治疗）

- **背景**：x3-step 在 HOLD 下经用户授权按原工厂第二段（`kl_coef 0.03 + lr 5e-5` 复合处理）
  跑满 30 轮；it21–30 KL 仅 1.286× x3-power 同窗，pd=5.5%（详账 `docs/nn.progress.md` §50）。
- **备选与否决**：把本腿读成「任何放缰都无效」—— 否，重设计单旋钮 A/B 未测；把本腿读成
  「信用比有害/有益」—— 否，KL≈0.002 下奖励侧任何结论都不可测；继续在本复合段加轮数 ——
  否，与 x3-power 判负同构，测不出任何奖励重定价。
- **决定**：① 工厂复合第二段（放缰 + 降 lr 同施）**禁**再用作可测性治疗；可测性证明只认
  单旋钮 A/B 或 pd/指纹主端点。② `Δ≤pd` 为判决硬约束：pd<5% 时 +5pp 判线不可观测，
  verdict 可按先例显式注销。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯 —— HOLD 禁令下本腿仍被跑起来，
  「松一下缰绳试试」的诱惑持续存在，且 `Δ>0` 式无阈值判据会反复出现；③ 无法就近表达 ——
  禁令横跨课程 / 评审 / 训练三处，无单一落点。
- **违反后果**：再跑复合段 ⇒ 2–4h 换回 1.3× KL 与噪声内胜率；用胜率点估计判奖励好坏 ⇒
  x2-acbc 式误判重演。

## §2026-09-16-goalnn-run-noise-floor（2026-09-16，T6 四臂定案：run 噪声地板规则＋wChip 判死）

- **背景**：T6 三剂量＋真复刻 k0 同批配对：69.1/72.1/69.8/71.1；同配方复刻对
  （x3-power/k0）差 2.0pp 且配对显著（p=0.028）⇒ run sd≈1.4–1.5pp；
  k05−k0 仅 +1.0pp（null）。详账 `docs/nn.progress.md` §52。
- **决定**：① **run 噪声地板规则**：单 run 总 SE≈2.2pp（对局 1.6＋run 1.5）；
  声称效应 <2pp 须 ≥2 独立 run（跨 run 预注册 pooling），否则结算标「未过噪声地板」；
  配对 McNemar/配对 t 只消对局噪声，禁做跨 run 因果断言（纯噪声对 shots t=−2.46
  的 demo）。现有 +5pp 门线安全（5＞2×1.5＋余量），不动。
  ② **wChip 可耐受剂量判死**：dmg/kill 四臂无方向、指纹复印、T6 字面叙事证伪；
  不再开 chip 腿（c4-0.03 前科并案）。③ §46/§50 判负加幅度 caveat
  （未经重复 run 验证），结论不动（详 §52）。
- **三问门（通过）**：① 被否决备选见 §52（单调 H1/倒 U/第二半裂）；② 未来再犯 ——
  单 run p<0.05 的因果冲动每条腿都会出现，且"非单调⇒噪声"偷运单调假设；
  ③ 无法就近表达 —— 横跨 verdict 口径/门禁数学/课程 DoD，无单一落点。
- **违反后果**：单 run ±3pp 定方向 ⇒ 复刻即翻案（k05 案）；改旧账字 ⇒ 账本失信；
  带着旧 tie-break 进 T5 ⇒ 标签硬币重演。

## §2026-09-16-goalnn-t5-credit-negative（2026-09-16，T5双run一致判负⇒奖励重定价路线关闭）

- **背景**：power 2×信用双 run 主端点 Δ−0.08/−0.03（判线 +0.7），转化率三臂 49.6%，
  pd 全灰度 7–9%；详账 `docs/nn.progress.md` §54。
- **备选与否决**：加剂量到 3× —— 否，灰度带按冻结表不触发加剂量且方向为负；
  拿单 run p / 灰度 pd 断言 —— 否，run 噪声地板规则
  （§2026-09-16-goalnn-run-noise-floor）；再调奖励其他项 —— 否，七 run KL 窄带
  0.0018–0.0027 证明位移不足、非选错项。
- **决定**：① 奖励侧重定价路线在 `ladder-c03` 关闭（幅度/方向/粒度均已证伪，
  不再开奖励腿）。② pd 尺修正：本分布 pd≈8% 为噪声地板，5–10% 灰度带无判别力、
  不单独判决。③ 下一腿只认单旋钮放缰 A（零奖励改动；产出＝KL≥0.005＋pd 过地板），
  否则转执行器分支。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯 —— “信用不够再加价 / 灰度 pd
  读成动了”每条腿都会出现；③ 无法就近表达 —— 横跨课程 verdict 口径 / 门禁数学 /
  后继路线，无单一落点。
- **违反后果**：再开奖励腿 ⇒ 2–4h 换复印零；拿灰度 pd 或单 run 显著断言 ⇒ k05 翻案重演。
## §2026-09-17-kaggle-cred-before-proxy（2026-09-17，Kaggle 引导期凭据必须在装 tailnet 代理之前读；诊断日志落文件）

- **背景**：`battle.tailscale.ipynb` 在 Kaggle 上「完整引导后」会话无声终结（Colab 一切正常；改了几轮
  tailscale/daemon/up-flags 都无效）。人工复制出来的日志止于 `Tailscale IP = …(mode=userspace)`。
- **根因（代码级，2026-09-17 定位）**：`notebook_boot.run()` 把 `HUB_TOKEN`/`PUSH_TOKEN` 的读取放在
  `tailscale_boot.ensure()` **之后**，而 ensure 会把 `HTTP_PROXY/ALL_PROXY` 指到 userspace tailscaled 的
  本地代理——**该代理只转发 Tailscale IP**。Kaggle 的凭据链必然落到 `kaggle_secrets`（对
  `www.kaggle.com` 的 HTTPS 调用），于是引导后读不到 token；`_secret()` 的 `except Exception` 又把它
  吞成空串 ⇒ `/code` 401 ⇒ `SystemExit` ⇒ 会话终结。Colab 的链在 `google.colab.userdata`（localhost
  通道，被 NO_PROXY 覆盖）就命中，`_kaggle()` 永不执行 ⇒ **平台差异**。同 cell 的**内联回退**本来就是
  「先读凭据、再 `_inline_ensure` 注入代理」，顺序是在迁移到远端模块时丢掉的（回归第 5 条）。
- **备选与否决**：让 tailnet 代理也能走公网——否，tailscaled userspace 出站代理设计上只转发 tailnet；
  只往 NO_PROXY 塞 `.kaggle.com`——否（清单不可穷举：Secrets/元数据/git/pip，且**顺序错误本身仍在**），
  但「NO_PROXY 合并平台条目」作为第二道保险保留；给 `_secret` 加重试/回退——否，代理方向本身就是错的。
- **决定**：① `notebook_boot.run()` 在 `ensure()` **之前**一次性读完三个凭据并下传（`_pull` 不再持有
  secret 句柄，签名级防回归）；② `tailscale_boot` 新增 `set_proxy_env()` / `platform_net_env()`：
  NO_PROXY **合并**（不再覆盖平台条目）、记录引导前原值、公网调用前临时还原为平台代理；cell 的
  `_secret` 用同一套（内联 `_platform_net_env()`）；③ 缺 token 时**响亮点名**，不再落到
  「`/code` 401 — HUB_TOKEN 不一致」这种误导措辞；④ cell 日志同时落文件
  （`/kaggle/working/battle-boot.log` → `/content` → `/tmp`），`SystemExit` 收尾/未捕获异常也落文件
  （此前第一手线索全靠人工复制 `[battle]` 行，**SystemExit 正文恰恰不带该前缀**，最容易被漏掉）；
  ⑤ `CFG["ts_engine"]` 阀门（引擎顺序实验，默认 `kernel,userspace` 不变；用于验证
  「kernel 模式那次 TUN/路由尝试动过容器网络」这条待验证假设）。
- **违反后果**：任何人再把「读平台 Secrets / pip / git」放到引导之后，Kaggle 上都会复现「无声终结」；
  任何只打 stdout 的引导都会在下一次无声死亡里丢掉全部证据（本轮排障成本的一大半在这里）。
- **配套事实（同批）**：`code.zip` 是 **TrainingLoop 启动时**的快照（`rl/loop_steps.py::pack_code_zip`，
  hub `/code` 直接回文件）——改了 `remote/` **必须重启 loop**，否则云机跑的是旧运行时；日志里的
  `sha12` 就是用来跟 loop 侧对账的（§2026-09-16-kaggle-kernel-no-torch 的子进程探测修复正是靠它才生效）。
- **回归测试**：`nn-training/tests/test_bootstrap_proxy.py`（7 例：NO_PROXY 合并 / 平台代理还原 /
  异常路径还原 / 引擎顺序 / ★凭据前置 / `_pull` 签名 / 缺 token 点名）；`tmp/repro-old-order.py`
  对 HEAD 的**修复前**代码复现了「引导后读 HUB_TOKEN」，断言当场抓住（§7.1）。
- **未决（下一步验证）**：E1 用落盘日志跑一次 Kaggle 定位真实死点；E2 「boot 完静置 5 分钟不 import
  torch」对照（排除 daemon/平台网络被杀）；E3 `ts_engine=userspace`（排除 kernel 尝试的副作用）；
  E4 对开卡 `sha12` 与 loop 日志核对 code.zip 新鲜度。
- **同日扩展（2026-09-17）：本机 hub 地址也走同一批凭据（键名 `HUB_IP`）**。
  - **背景**：`CFG["hub_url"]` 是「每个会话都要手填、值却长期不变」的值（hub 跑在操作者本机，
    Tailscale IP 在设备重注册前稳定）——**正是 secret 的用途**；留在 CFG 里等于每开一次
    Colab/Kaggle 就要人肉改一次，而它的模板值 `http://<本地TS_IP>:8787` 忘了改就会拿一个
    带尖括号的主机名去连（错在 DNS 层，比当场点名难查得多）。
  - **决定**：① `HUB_IP` 走与 `TS_AUTHKEY`/`HUB_TOKEN` 同一条取用链（环境变量 → Colab/Kaggle
    Secrets → CFG 手填），读进 `CFG["hub_ip"]`；有值时**压过** CFG `hub_url`。② 取值两吃：
    裸 IP/主机名自动配 `CFG["hub_port"]`（缺省 8787），整条 URL（自定义端口/域名/协议）原样用 ——
    同一个键不必记两套约定。③ CFG 模板值含 `<` 一律视为**未填**（返空串，由调用方响亮失败并
    点名该填哪个键）。④ 解析逻辑单源住在 `remote/tailscale_boot.py::resolve_hub_url`：两个
    notebook 都从 GitHub raw 拉这个模块**同一个文件**，而体检那边只拉这一个（消费点
    `notebook_boot.run()` 与 `diagnose()`）；⑤ `HUB_IP` 与其它凭据同批**在引导之前**读（同上条根因：
    引导后平台 Secrets 就够不着了）。
  - **备选与否决**：写进 CFG 让人肉填 —— 否（每会话一次的人肉步骤，且忘改就是带 `<` 的主机名）；
    只在训练 cell 支持、体检 cell 不改 —— 否（会出现「体检说连不上、真跑却连得上」这种最难信的
    诊断结论）；新增 `HUB_URL` 键名 —— 否（`HUB_TOKEN`/`HUB_IP` 同族命名更可猜，且 URL 形态仍
    由 `HUB_IP` 一个键容纳）。
  - **违反后果**：把 `HUB_IP` 的读取挪到 `ensure()`/`_inline_ensure()` 之后，Kaggle 上会复现
    「凭据读成空串 → /code 401 → 会话终结」；把内联回退的解析改得与 `resolve_hub_url` 不同源，
    则两条路会连到不同的 hub（GitHub raw 不可达时才暴露，最难复现的一种）。
  - **回归测试**：`nn-training/tests/test_notebook_hub_ip.py`（14 例：内联回退的解析与
    `resolve_hub_url` **逐例对账**（8 例取值 + 2 例未填 → `SystemExit` 且点名两个键）、缺省端口
    跟随模块常量、两个 cell 的 HUB_IP 读取位置早于代理引导、体检 cell 把 `hub_ip`/`hub_port`
    喂进 `diagnose`）；notebook 侧接线改动用**先红后绿**验过（把缺省端口字面量改成 9999，
    对账断言当场红）。

## §2026-09-17-goalnn-remotewire-m0m2（2026-09-17，远程 PPO 传输计量 + 协议瘦身落地；退路与安全阀是硬要求）

- **背景**：`plan/remote-wire-remediation.plan.md` M0–M2 实施（分析见 `plan/kaggle-rollout-feasibility.md`：
  push 上行 4.43MB/轮里 ≥3.2MB 是恒定或可再生的字节）。切片提交：M0 `0bc7a69`、M1 `fa6a34f`、M2 `a59b1ef`。
- **备选与否决**：① 只在 `_remote_ppo` 里记字节 —— 否（生产路径与验收 harness 会各记一套，数字对不上账）；
  改记在传输客户端层的返回值，两边共用同一份账。② M2 让节点「自己留着状态、hub 不管」 —— 否（违反 D12：
  缓存只决定「这坨字节要不要再传」，sha 对账必须仍能在 hub 侧重算）。③ B5 只在旧节点上试一次 JSON —— 否。
- **决定**：① `wire` 子字典（iteration 事件 + worker result 回传）是**唯一**传输口径，键为 additive，
  旧行无键 = None，`validate_result` 不校验未知字段；`/admin/net-probe?bytes=N` 走鉴权、确定性填充。
  ② 瘦身走**运行期开关**（`slim`，缺省关＝逐字节旧行为），开关取值必须进指标，否则事后无法按选项分组。
  ③ opt/ref 一律对 **raw（编码前）字节**取 sha256 做内容寻址；节点侧重写在 `blob_cache/<sha>` 的那个
  opt 就是它自己刚产出的那份 ⇒ 同会话命中率 100%。④ **安全阀**：`opt_sha` 存在但 blob 取不到 ⇒
  响亮失败（`RetryableError`/`ProtocolError`），**绝不**静默退回 `load_state_into` 开新 Adam ——
  那是把 D5 的动量延续悄悄改成「每轮零动量」，而日志上一切正常。⑤ B5 的 `/job` 体走二进制（BRJ2），
  但 4xx 时**保留一次 JSON 退路重发**：协议不匹配绝不能让一整轮 job 丢在最后一米（同 result v2 规矩）。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯 ——「数字分别记两处」「取不到就静默 warm-start」
  「把退路优化掉」每条都会再出现；③ 无法就近表达 —— 横跨传输客户端 / 两个解析端 / 校验语义 / 回退开关。
- **违反后果**：再出现静默降级 warm-start ⇒ D5 语义被无声改写（本仓最怕那类 bug）；删掉退路重发 ⇒
  新旧混合部署时整轮 job 丢失；开瘦身却不在指标里记开关 ⇒ 无法归因。
- **配套事实（M1 实测，2026-09-17 本机）**：`http2` 上行 p50 4.7–4.9s / `quic` 23–33s（2MiB，每臂 2 轮
  独立 run，run3 八连发无退化）⇒ **决策门 1 命中，默认 `http2`/`edge-ip-version 4` 有实测支撑**；
  数字与探针用法见 `docs/nn.progress.md` §58（合并时为避开 origin 已推送的 §56 而改号）。同在那一轮量到的一个坑：本机 `HTTPS_PROXY=127.0.0.1:7890`
  而 `NO_PROXY` 不含 `*.trycloudflare.com` ⇒ **任何打隧道 URL 的本机客户端必须绕过代理**
  （不绕会拿回 `SSL: UNEXPECTED_EOF_WHILE_READING`，而 cloudflared 日志看起来完全健康）。
- **B6（xz preset 3→6）已量、不采用**（2026-09-17，3 份真 payload）：体积只 −2.6…−3.0%（~34KB），
  打包却 +188…+199%（关键路径 +6.3s/轮），按隧道实测 3.5 Mbps 那 34KB 只值 0.08s ⇒ 净亏。
  结论就写在 `remote/protocol.py` 常量旁（就近表达，防将来重测）。
- **补记（2026-09-17，同批）**：① M2 的 `slim` 补上「启动时提供选项」整条链（原来只能手改
  rl-config.json ⇒ A/B 无路）：类型/双域 `SlimMode`、`resolveSlim`/`slimToCfg`、console-state、
  preset 白名单与落库、启动弹窗控件与当前生效值。**双域是铁的**：UI/console-state/body 用
  `'on'|'off'`，rl-config 必须 `1|0`（python `--remote-slim` 是 `type=int,choices=(0,1)`，
  写字符串会让训练启动直接报错），换算只允许走 `slimToCfg()`。② 修掉一个真缺口：
  `wire.protocol`/`wire.edge_ip` 原**恒为 null**（CLI 从未声明这两个参数，而 `_wire_from_result`
  读的正是 args）⇒ §1.4「开关取值必须写进 iteration 事件」过去并未满足。现由 CLI 参数 +
  `_course_cf_tunnel`（CLI > `courses.<stem>.cf_*` > `rl.cf_*` > None）填上。
- **未做（不写成已做）**：M2 的云机绝对值确认**未跑**；M3（rollout 上云）按 §5.1 门 1 已命中、
  门 2 待测，且 M2 后上行仅 ~1.2MB ⇒ 当时留档不做（**后经人工指令开工，见下条**）。

## §2026-09-17-goalnn-rollout-on-cloud（2026-09-17，M3 rollout 上云：**人工指令覆盖计划的决策门**，
安全阀与「不许静默降级」是硬要求）

- **背景与授权**：`plan/remote-wire-remediation.plan.md` §5/§6 的原文是「门 1 命中 ⇒ M3 直接留档不做」，
  门 1 在 M1 实测中已命中（`http2` 把 2MiB 上行压到 p50 4.8s）。本次开工依据是**用户指令**：目标腿是
  TPU 实例（v3-8 = 96 vCPU），rollout 上云在那里收益极高。**门的结论没变，是决策权变了** —— 记录本条
  是为了让后续 agent 不会把「代码里有 M3」误读成「门开了」，也不会拿旧的『留档不做』去回退它。
- **备选与否决**：① 让节点自己拼 rollout 命令（否决 —— hub 的 `build_rollout_cmd` 是三导出器 + 课程覆盖
  + D14 血缘的唯一拼装点，节点重算 = 在协议里复制一份它的知识，早晚漂）；改为 hub 发 argv、节点只执行。
  ② 上云轮降级本机（否决 —— 本地没有 shard 可训，「降级」只能是静默丢掉一整轮）；③ 用 `local_slots=0`
  + 手工摘节点来关本地采样（否决 —— 靠配置正确；改为 loop_core 在 node 轮**结构上**跳过整个采样相位）。
- **决定**：① 新 job `kind="iter"`（走 BC 开过的 kind 通道），manifest 追加必填 `ts_code_sha256` + `rollout`；
  rollout 规格 = 逐局 argv（白名单只放行 `tools/sim/export-rl-rollout.ts`，`--out`/`--weights` 必须 job 内
  相对路径）。② TS 运行时按内容寻址（`pack_ts_code_zip` 固定时间戳 ⇒ 同内容同 sha ⇒ 节点缓存可命中）；
  白名单打**整棵 `tools/**` + `src/**`** 而不是手挑子目录（实测依赖闭包会跨出 `tools/sim` 到
  `../eval/godai-score`；手挑 = 在猜依赖图）。③ 声明集用 `iter_expected_data_fp`，节点侧对实产集
  用**同一个函数**复算并拒收不符 —— 这不是等价替代，是「上云轮没有本地副本可重算」的唯一替代。
- **开关与回退**：`--rollout-src auto|local|node`（缺省 auto → local，逐字节旧行为）；值住 rl-config
  （`rl.rollout_src` / `courses.<课>.rollout_src`，D14 血缘：选项永不进 curricula），并随每轮写入
  iteration 事件的 `wire.rollout_src`（否则事后无法按「实测在哪跑」分组）。控制台启动弹窗提供选项。
- **安全阀（配错一律响亮，禁止静默降级）**：node 与 `--target-transitions` 互斥（补波要读本地 shard）；
  发布时 traj 已有本地 shard ⇒ 拒发（双份采集）；实产 shard 集 ≠ 声明集 ⇒ 拒收；`_ensure_ts_code` 的
  sha 不符 ⇒ `RetryableError`。上云轮**不经过** `_remote_ppo_or_degrade`，所以 4xx（鉴权/闭锁）
  立即停腿的判据必须在 `_remote_iter` 里**另补一份**——否则 x3-step 事故的「403 白烧 5×30s 重发同一 job」
  会重演。
- **控制面同步（计划 §5.4 「最容易漏一半」）**：本地采样/预采结构上关闭；`rollout_sec` 用节点自报
  `elapsedSec`（用 t_rollout 会把 PPO + 传输算进采集 = 假指标）；`metrics_stats` 与 `_check_quota_incident`
  在两轮均**跳过**（上云轮本地零 shard 是预期，不跳就是假精度统计 + 每轮假配额告警）；eval 链零改动。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯 ——「节点自己重算调度规格」「上云轮降级本机」
  「内容寻址缓存忘了加进 prune 豁免名单」每条都会再出现（第三条 M2 已经犯过一次）；③ 无法就近表达 ——
  横跨协议 / 两个执行端 / 控制面读取点 / 回退开关 / 控制台 UI。
- **配套事实**：逐位对拍已验（`tests/test_remote_iter_real_bun.py`，真 bun + 真权重，同 argv 跑两遍逐文件 diff，
  含 `_rl_report.json` 除 `elapsedSec` 全字段）；协议/规格/执行器/失败语义/传输端点共 ~70 例。
  **未做（不写成已做）**：真远程轮次的绝对值（`wire.up_sec`、每轮墙钟）与 TPU 腿上的 target ~10s ——
  本机无节点可跑；M2 的云机绝对值确认同样仍欠。细节见 `docs/nn.progress.md` §59（合并时改号，原 §57）。
- **收益前提不变**：≥16 vCPU 的腿才成立（GPU T4×2 = 4 vCPU 直接否，见 `plan/kaggle-rollout-feasibility.md` §3.3）。
## §2026-09-17-hub-restart-deadlock-hardening（2026-09-17，hub-server 重启死锁：D9 改序 + 回环永不封禁 + 原子实例锁 + 停止 trainer 即释放锁）

- **背景**：hub-server「崩溃后手动重启失败」，控制台只报「意外退出」，日志为 `端口 127.0.0.1:8787
  已被占用——拒绝启动（禁止双监听）`。根因链与现场日志、备选否决的完整版见 `docs/nn.progress.md §56`。
- **根因（三层同族）**：① 旧 `_auth_ok` **先查封禁再验 token** ⇒ 本机组件用陈旧 token 连打 5 次就被
  封一小时，且**连正确 token 的健康检查/训练循环/worker 拉活一起 403**；② cloudflared 回源把隧道
  流量也归成 127.0.0.1，回环被封 = 整机服务面连坐；③ 封禁只住**进程内存**、只能重启清除，而旧实例
  还活着占着端口 ⇒ **重启被自己占的端口挡死** = 只能人工杀进程。另两处同族障碍：双监听守卫的
  「探测→bind」TOCTOU（Windows 还能双绑成僵尸）、账本 pid ≠ 锁持有者时「停止→启动」被 trainer 锁卡死。
- **决定（四点）**：① **D9 改序**（`remote/hub_server.py::HubHandler._auth_ok`）——先验 token，
  **合法 token 永远放行**，封禁只拒无效鉴权尝试（封禁期的无效尝试 403、不计数、不延长）；
  ② **回环永不封禁**（`_is_loopback`）——`127.0.0.0/8` / `::1` / `::ffff:127.0.0.1` 的失败**不计数、
  不封禁**（鉴权边界与 401 审计行不变；用户口径「本地 127.0.0.1 鉴权失败不要锁地址」）；
  ③ **服务侧原子实例锁**（新 `remote/_instance_lock.py`，路径 `nn-training/.<kind>.<port>.lock`，
  按端口键控）——拿锁 → 端口探测 → bind 三道闸，陈旧锁按「持有者已死 / 命令行缺该服务指纹」
  接管（指纹可给多个：同一服务常有多个合法入口），身份读不到即 fail-closed，锁文件**写不下**
  （只读 FS）时 fail-open 且响亮告警（守卫是纵深防御的第二道闸）。接线：`hub_server`（8787 类）
  与 `worker_server`（push 端口，经 `remote_worker_serve`）——后者的僵尸更贵（HUB 会把 job POST 进
  无人应答的监听端口，表现为推送静默卡死）；④ **「停止 trainer」即释放**（`launch/cli.ts::releaseTrainerLock(s)` +
  `server/actions/stop.ts`）——释放本课 run_rl/run_bc 锁，**先核验进程身份**才停存活持有者，身份不符只告警；
  ⑤ **隧道来源还原（B，同日追加）**——②把回环整段豁免后，隧道入口（cloudflared 回源也归成 127.0.0.1）
  变成「只 401、不计数、不封禁」，等于对公网暴露面零封禁。新增 `hub_server.attributed_source(peer, cf)`：
  **只在「TCP 对端是回环」时**采信边缘注入的 `CF-Connecting-IP`（须为合法 IP 字面量且非回环值）
  ⇒ 按**归因 IP** 计数/封禁；无头 / 头不是合法 IP / 头写的还是回环值 / 对端不是回环 ⇒ 一律归因 TCP 对端。
  直连（tailnet）对端**只认 TCP 对端 IP**——那台机器能自己写任何头，采信它等于把封禁能力交给攻击者。
  审计行随之带上 `peer=` / `src=` / `via=cf|peer`（否则事后分不清封的是隧道来源还是直连来源）；
  **访问日志（`log_message`）同样补 `src=… via=cf`** —— 回源流量原本全写成 `[hub-server 127.0.0.1]`，
  这正是本次事故排查的最大阻雾（`log_message` 只打非常规事件，不刷屏；`self.headers is None` 的早期
  错误路径有护栏）。
  可采信的头**只认 `CF-Connecting-IP`**，不认 `X-Forwarded-For`（后者是可追加的逗号列表，取哪段都是语义游戏）。
- **被否决备选**：调大阈值/时长（本质是封禁**作用面**，不是次数）；回环也计数（回源流量冒充回环来源，
  惩罚无据）；加解封管理端点（真解药本就无需人工介入）；只靠端口守卫（TOCTOU + Windows 双绑）；
  按「锁里 PID 活着」直接杀（PID 复用误杀无辜）；**全局无效尝试退避闸（429/延迟，方案 C+D）**——
  它的全部收益建立在「封禁不可用」之上，而 B 一旦生效就重复了；B 失效时它也只能压速率、不加安全，
  却要给每条合法路径都加一条延迟分支（合法 token 先过、不受影响，但多一个恒常状态机）。收益重叠、
  成本不为零 ⇒ 不做。**删掉封禁机制（方案 E）**同理被否：tailnet 直连那一侧的来源标识是可信的，
  在那里封禁仍然有效，不该为隧道侧的难题陪葬。
- **头部可伪造时的失效代价（预登记，供事后对照）**：本方案唯一未实测的假设是「Cloudflare 边缘**会覆写**
  `CF-Connecting-IP`」（仓里无 CF 头读取先例、无 ingress 配置、此沙箱无网络，无法离线验证）。假设不成立时
  最坏后果**两条都良性**：① 攻击者轮换头值 ⇒ 拿不到封禁，效果退化为「回环豁免」（= 本方案之前的状态，
  不会更差）；② 伪造某个 tailnet worker 的 IP ⇒ 那个 IP **只**会被拒「无效鉴权尝试」，带正确 token 的
  请求照常放行（改序使然）⇒ 不构成对合法对端的 DoS。**实测法**（2 分钟，用户可随时做）：向隧道发一次
  带伪造头的无效鉴权（`curl -H 'Authorization: Bearer wrong' -H 'CF-Connecting-IP: 203.0.113.7'`），
  看 `hub-server.out` 的 `AUTH FAIL` 行 `src=` 显示伪造值（⇒ 可伪造，本方案降级为 B0）还是真实公网
  出口 IP（⇒ 假设成立）。若显示伪造值，回退动作 = 把 `attributed_source` 的 `cf` 分支删掉（一行），
  语义即回到「回环豁免」，不需要改测试以外的任何东西（`test_attributed_source_matrix` 是唯一直接
  断言该分支的用例）。
- **违反后果**：把封禁检查挪回 token 校验之前 = 整机自锁；让回环重新计数/封禁 = 隧道流量与本机组件
  互相连坐；停止 trainer 只杀账本 pid = 「停止→启动」死锁回归。
- **回归测试（第二批）**：`nn-training/tests/test_pid_probe_windows_safe.py`（6 → **8**，覆盖六处入口，
  含 AST 唯一实现门禁与 tmp-clean 副本契约）；`dashboard/tests/training-port-reclaim.test.ts`
  （6 → **15**：stepCloudflared 接线门禁、`ownsResource` 声明/监督器接线门禁、
  `portOwnedBy` 注入/真实监听/空清单两义三组）。
  A/B 取证（`tmp/probe-red.log`、`tmp/cf2_probe.py` 输出）：detached worktree 对 HEAD 跑新测试
  6 红（含 `tmp-clean._pid_alive(0) = True`、`_pid_alive(-1) = True` 的行为级红）；cloudflared 侧
  HEAD 上 `stepCloudflared` 既无 `reclaimPort` 也无端口归属校验、就绪判定是裸 `tunnelEdgeReady`；
  监督器侧 HEAD 上 `ownsResource` 在 `ProcSpec`/`specs.ts`/`server.ts` 三处**全都为 0 次**。
- **回归测试（第一批）**：`nn-training/tests/test_hub_auth_d9_order.py`（11 → **19**，2026-09-17 追加 8 例：
  归因矩阵 / 隧道来源 5 次即封且第 6 次 403 / 被封归因 IP 持合法 token 仍放行 / 本机无头组件仍豁免 /
  直连对端自带头不算数 / 伪造头无害 / 访问日志带 `src=`（含直连与无头两负例）/
  `headers is None` 的早期错误路径不抛）、`nn-training/tests/test_instance_lock.py`（9，含真进程同时三启
  恰好存活一个）、`dashboard/tests/trainer-lock-release.test.ts`（13）；A/B 取证 = detached worktree 跑
  新测试对 HEAD 红（`assert 403 == 200`；归因来源一节另附行为取证
  `tmp/cf-red-behavior.log`：修复前 5 次「回环+CF 头」无效鉴权后 `is_blocked(203.0.113.7) = False`、
  `_auth_fail = {}` ⇒ 隧道入口确实零计数）。
- **配套（同日同族）**：控制台启动前按端口回收幸存占用者（`stack/hub.ts::reclaimPort`，已接在
  `stepHubServer` / `stepSelfNode`）。**`workerServe` 控制台路径故意不接**：worker_server 可能在跑
  数小时的 PPO job，`/ping` 失败（如 token 临时不匹配）就回收它 = 直接炮掉在途 job；而该路径
  现在会先复用 `/ping` 通的幸存者，不通时拿不到锁也会**响亮拒绝并指向日志**（含持有者 PID 与
  锁路径），足以人工处置 —— 要不要让控制台代劳杀进程，留待用户拍板。
- **铁律（新增，实现唯一化）**：**存活探测在 Windows 侧禁止用 `os.kill(pid, 0)`**——它是
  `TerminateProcess(handle, 0)`，会把被探测的进程**直接杀掉**；且 `except Exception → 不活` 会把
  「我杀了它」记成「它本来就是死的」，护栏静默失效；`pid <= 0` 一律判不活（POSIX 上
  `os.kill(0/-1, 0)` 命中**进程组**，会把残缺锁当成活人持有 ⇒ 同名课永久拒启 / tmp-clean 永不收敛）。
  **唯一实现 = `nn-training/pid_probe.py::pid_alive`**（stdlib-only 顶层模块，与 `platform_utils`
  同层；`remote/` 与 `train/` 都直接 import 它而**不经过对方的 `__init__`**——`remote/` 要独立
  打进 code.zip、`train/loop_util` 刻意保持 torch-free，两个方向都不能反向依赖）。
  四份具名薄壳全部**委托**它：`train/loop_util._pid_alive`、`run_rl._runrl_pid_alive`、
  `remote/_instance_lock._pid_alive`、`remote/notebook_runtime._pid_alive`（后者原是**函数内的
  嵌套闭包**、不可被测试导入，已提到模块层）。**唯一的保留副本** = 仓根 `tools/tmp-clean.py`
  （根级开发工具不能依赖 nn-training 的包/路径布局），按契约自带 Windows 分支 + `pid<=0`，
  一致性由源码门禁守住。
  **为什么必须唯一**（18 小时内同类隐患在 3 个不同文件各自踩了一次：`loop_util` 裸 `os.kill`、
  `notebook_runtime` 嵌套闭包+裸 `os.kill`、`tmp-clean` 缺 `pid<=0`）：「每加一个调用点就多一份
  可漂移的实现」就是这类 bug 的根因面；收敛成一份后，“Windows 安全”只需在一个地方成立。
  回归 + 行程门禁：`nn-training/tests/test_pid_probe_windows_safe.py`（六处入口全纳入同一组断言：
  注入假 kernel32 断言 Windows 分支**零 os.kill**；AST 门禁断言 `nn-training/` 里真调用
  `os.kill(pid, 0)` 的文件**只有 `pid_probe.py` 一个**——已排除注释/docstring 与
  `os.kill(pid, 15)` 这类**故意发的信号**；并断言四份薄壳不得再自带 `import ctypes`）。
- **隧道 metrics 端口的双绑窗口**（同日追加）：cloudflared 是**第三方二进制**，没法在它内部
  装实例锁（hub/worker 那层是 python 自己拿 `O_CREAT|O_EXCL`）⇒ 控制台侧回收就是它**唯一**
  的一道闸。`stepCloudflared` 在 spawn 前 `reclaimPort(metricsPort)`（与 hub/selfNode 同族），
  堵住 `supersedeSlotTunnels` 看不见的那类幸存者（孤儿 / 登记丢失 / 控制台重启竞态）——
  metrics 端口既是 `--metrics` 的 bind 目标、又是 `/ready` 的探测目标，被占着会**同时**造成
  「新隧道 bind 失败」与「就绪读数读自旧僵尸」。后者另加一道：**就绪归属**
  ——`hub.ts` 的就绪判定改为「**本进程持有该端口** ∧ /ready 200」，算法是
  `core/proc.ts::portOwnedBy(pid, port)`（唯一实现；名字从早期的 `tunnelOwnsMetrics`
  改成中性名，因为 hub-server / worker_server 也要用它——见下一条）。
- **就绪归属推广到所有「独占端口的组件」**（同日追加，用户点名「监督器那条路径也要」）：
  `ProcSpec` 新增可选字段 **`ownsResource?: (pid) => Promise<boolean>`**，声明的三处：
  `hubServerSpec`（hub 端口）、`cloudflaredSpec`（metrics 端口）、`workerServeSpec`（push 端口）；
  **监督器**（`server.ts::restart`）与**启动步骤**的就绪判定都变成
  「`ownsResource`（未声明 = 不阻塞）∧ `healthy()`」。为什么监督器同样需要：它杀旧 pid 后
  紧接着拉起同一条 spec，若新进程 bind 失败（EADDRINUSE / python 侧双监听守卫拒绝）**早已退出**，
  而端口上的旧实例照样答 `/ping` / `/ready` ⇒ 监督器会把**僵尸的 200 记成「重启成功」**：
  账本写新 pid、实际服务的是旧进程——这正是 hub-server 重启事故的相位（账本 pid ≠ 服务者）。
  未就绪时的日志会点出归因（“新实例未持有该端口”），而不是只报「45s 未就绪」。
- **`portOwnedBy` 的空清单必须用 TCP 探测二次区分**（同日发现并修，否则语义荒谬）：
  lsof/netstat 无输出有两种含义——**根本没人监听**（⇒ 新进程显然没拿到端口 ⇒ **false**，
  这正是要抓的那类）与**列举不出归属**（⇒ 探测不可用 ⇒ **true**，不判死，否则工具缺失会让
  所有组件启动失败）。有清单时只认「包含自己」。fail-closed 只落在「确知不是自己」那一侧。
- **监督器重启路径不接 `reclaimPort`**（保留的刻意选择，非残留）：它杀的是账本里确切的
  pid、紧接着拉起同一条 spec，没有孤儿窗口（kill 失败时回收也一样杀不掉），故不回收、只核归属。

## §2026-09-17-goalnn-python-gate-parallel-policy（2026-09-17，python 门禁并行策略：worker 数 × CPU 内线程数必须成对调；"-n 4 最优" 作废）

- **背景**：用户「检查 python 全量门禁，提高可维护性，减少耗时」。门禁 39~50s，而 ruff(~1s)/
  mypy(~4s) 全藏在 pytest 后面 ⇒ 唯一瓶颈是 pytest 步。
- **根因**：旧默认 `-n 4` × torch 默认内线程（= 物理核）⇒ 4×16 = 64 线程抢 16 核（**超订**）。
  `docs/goal-nn.progress.md §25`「n=4 最优、auto=16 反更慢」是超订假象的读数（worker 越多越慢
  本身就是证据），不是「torch import 开销」。16 核实测均值（同机交错 3 次/项）：`-n 4` 默认 36.3s /
  `-n 12` 默认 44.7s / `-n 4` 线程=1 39.7s / **`-n 12` 线程=1 = 24.7s**（`-n 8/16/auto` 线程=1 同水平）。
- **备选与否决**：① 只加 worker——否（44.7s，更慢）；② 只封线程——否（39.7s，无收益：小并发盖不住
  尾部长用例）；③ `-n auto` 且不封顶——否（worker 数 = 逻辑核，无法给内存封顶；本机 auto ≈ n16 同水平
  但峰值 RSS 随核数线性涨）；④ 把新数字写死（如 `-n 12`）——否（换机器就错），改「核数派生 + 上界」。
- **决定**：门禁 worker = `min(核数, 12)`（`NN_GATE_NPROC` 覆盖）、CPU 内线程 = 1
  （`NN_GATE_THREADS` 覆盖，0 = 不设；须在 python 启动前 export OMP/MKL/OPENBLAS）。
  **同策推广到所有本地入口**（防「门禁快、日常慢」漂移）：`task.py`（线程封顶收在共用的
  `clean_env()`，四个 target 一律 `-n auto`——**同时回退 2026-09-15 把 `-n auto` 判为
  「沙箱 ~34% 停滞头号嫌疑」的误判**）、`nn-training/Makefile`（`NPROC ?= auto` / `THREADS ?= 1`）、
  CI `nn-training.yml`（job 级封顶 + 单测层由串行改 `-n 2`）。**`-n 4` 不再是任何入口的默认值。**
- **违反后果**：任一入口把封顶 export 删掉、或把 worker 写死回 4，全量立刻退回 ~36s——而**用例仍全绿**，
  只有人肉计时才看得出来（静默退化）。由 `nn-training/tests/test_githook_scripts.py` 两条静态护栏钉住
  （已做变异验证：删 export / 退回 `-n 4` / 去上界 3/3 被抓住）。
- **证据与完整表格**：`docs/nn.progress.md §57`。另：门禁不再重复加 `-q`（addopts 已有 ⇒ 原本
  `-qq` 吞掉了「N passed in Xs」，hook 日志里看不到用例数与耗时）。

## §2026-09-17-job-fail-report（2026-09-17，节点**确定性**失败必须带原因回传控制面：`POST /jobs/{id}/fail` + `/result` 410 终局）

- **背景（真缺口，用户 2026-09-17 提问暴露）**：云机确定性失败（bun 装不上 / TS 运行时取不到 /
  argv 非法）此前只落在**云机日志**里。pull 侧 `worker_loop` 的 `except ProtocolError` 只打一行
  「REJECTED … skip (not retried)」——**不回传、不还租约**，训练侧只能等 `wait_job` 25 分钟超时，
  读到的是「超时」而不是「bun 缺失」；push 侧节点 `worker_server` 用 **500** 报 failed，而 500 在
  `push_client.wait_result` 里被当**瞬时错误**重试到 1800s 预算耗尽。两条路都把「确定性能力缺失」
  伪装成「网络/排队问题」，且每次重试再白烧一个超时窗口。
- **备选与否决**：① *只把云机日志写得更清楚*（否决——训练侧读不到，人的第一现场是控制台，不是云机
  stdout）；② *把确定性失败并进既有连败/降级链*（否决——重试只会撞同一堵墙，且
  `--remote-degrade-after` 会把上云轮静默切到本机 PPO，而上云轮**没有本地 shard**，降级即打穿）；
  ③ *启动前 smoke 探 bun 存在性*（否决——只覆盖「装没装」一类，TS 运行时取不到 / argv 非法 /
  commit 不符同样要回传，且探测本身多一次往返）；④ *调小 `wait_job` 超时预算*（否决——治不了
  「原因不可见」，还把真慢的云端 PPO 误杀）。采用：**失败即终局 + 原因随行**。
- **契约（硬要求）**：`POST /jobs/{id}/fail`（bearer 鉴权；活租约须持有人；`reason` 必填非空；
  体 ≤ 64KB）= 节点判定「这个 job 在这台机器上跑不成」→ 落 `fail.json`（**首写锁定**：已有结果
  不收失败、首个原因胜出；原子 tmp+replace）→ ① `GET /jobs/{id}/result` → **410 + 原因**
  （不是 404「还没回来」、不是 5xx「瞬时错误」）；② `/status` → `state="failed"` + 原因；
  ③ 该 job **不再回池**（`claimable_job_ids` 排除——换节点只会重演同一失败）；④ 账本追加
  `job_failed`（带 worker 身份：多机共用同一 token，这是定位现场的唯一线索）；⑤ 训练侧
  `JobFailedError` → **第一次**就写 `gate_verdict: ABORT`（真原因入判决）+ 停腿，不消耗连败
  配额、不降级。**重发同 job（同幂等键 → 同 job_id，逐轮重试走的正是这条路）由 `publish_job`
  清掉 `fail.json`**——不清 = 「失败即终局」把重试永久钉死（无人认领 + 立刻 410），这是两条
  语义共存的**唯一**接口。
- **名词纪律**：只有**确定性**失败（能力缺失 / 协议级拒收）走这条路；瞬时失败（网络 / 5xx）仍走
  `release` 回池、**不报** `fail`。搞反的代价单边且严重：瞬时失败报成终局 = 可恢复的 job 被钉死；
  确定性失败报成瞬时 = 白烧一个超时窗口（本次要治的病）。`CodeChangedError` 刻意不报——它靠
  重启进程 + 租约回池自愈。
- **落地**（9 个文件，逐处行为见 `docs/nn.progress.md §60`）：protocol（`FAIL_NAME`/`JobFailedError` 带
  reason/kind/detail）、hub_server（端点 + 首写锁定 + 池排除 + 410 + `status=failed` + `job_failed` 账本）、
  hub_client（`report_job_failure` + 立即抛 + `publish_job` 清标记）、worker / worker_server（**500→410**）/ push_client、
  `rl/loop_steps.py`（首败即 ABORT + `_push_job_round` 不再包成 RetryableError）、dashboard `ppo-queue.ts`
  （`job_failed`/`fail.json` 不算排队超时；账本读法改 **last-write-wins**——重发同一 job 又该被盯排队，
  旧口径会把它永久当已关闭）。回归：`test_job_fail_report.py`（9）+ `test_remote_degrade.py`（2）+ dashboard（2）。
  实测（2026-09-17）：`wait_job` 从「等满 25min」变为**秒级抛错**（整个用例含起服务仅 0.52s）。
- **违反后果**：删掉 `publish_job` 的清标记 ⇒ 重试被永久钉死（整条腿再也跑不起来）；去掉池排除 ⇒
  每个节点轮询都重演同一失败；`worker_server` 退回 500 ⇒ 训练侧重新等满 1800s；把确定性失败并回连败链 ⇒
  上云轮静默降级打穿（无本地 shard 可训）。
- **遗留（未做，不写成已做）**：真远程轮次的端到端验证（本机无节点可跑）；节点侧 preflight
  **硬门**（bun 版本对账不匹配直接拒单，计划 §5.3）仍只有记录与日志。


## §2026-09-17-goalnn-halfoffline-run（2026-09-17，半离线整段：云机领一次就自主跑完，产物可打包下载；hub 失联不影响）

- **背景（用户需求，2026-09-17 两次确认）**：现状是 **hub 拥有迭代循环**——每轮 publish 一个 job、
  节点跑一轮就回等；hub 一断，云机除了等无事可做。用户要的是：云机领到（课程 + 初始权重 + 代码）
  后**即使本机 hub 一直失联**也能全程自主跑完，并以 Kaggle/Colab 官方方式（工作目录产物 zip）交付
  **逐轮**权重与指标。用户口径（三问已答）：一次领 = 整段；逐轮权重/指标/可续跑；**不在云上跑评估**。
- **备选与否决**：① *把 hub 的迭代循环抄一份到云上*（否决——`build_pairs`/`build_rollout_cmd`/课程重建
  各有**唯一**一份，抄一份就是造第二个真相）；② *等 hub 重连再逐轮问*（否决——正是要治的病）；
  ③ *产物边跑边 POST 回 hub*（否决——失联时 POST 必失败，产物会跟着丢；产物必须落**本机**，回传尽力而为）；
  ④ *新写一条「段」执行链*（否决——本轮语义与 kind=iter **逐字段同构**）。采用：**kind="run" = kind="iter"
  的延长**（同一轮 + 一个计划尾巴）。
- **契约（硬要求）**：① `plan.json`（`rl/plan.build_plan` + `dump_plan` 规范序列化）随 payload 下发，
  manifest 记 `plan_sha256`；节点在**跑第一局之前**过三道门——sha256 / 形状（`validate_plan`）/
  **全段对集指纹**（`plan_pairs_fp` 重放每一轮），任一条不符就一局不跑（跑到半途才发现语料漂了，
  已经产出一批不可信 shard）。② 计划只带「纯函数入参 + argv 模板」：对集靠 `build_pairs` 重放，逐局
  argv 只做四个动态 flag 重定向 + 自定义关 `--stage-json`（重定向不认识任何导出器细节）；发布期自检
  保证「重放 == 真 args」且「重定向对模板恒等」。③ 产物目录是**唯一**长期记录、**中断即有效**：
  `it-NNN/weights.json` + `opt.tar`（Adam 动量，缺它续训静默归零）+ `metrics.jsonl`（一行一轮）+
  `state.json` + `LATEST.zip`/`artifacts.zip`；TS 运行时随产物携带 ⇒「只下载产物 zip」的机器也能续跑。
  ④ 末轮形状 + `iters` 明细回传 ⇒ hub 侧落位链**零新代码**（三个指纹逐字段对的是本 job 自己）。
  ⑤ 续跑判据 = `run_id` + **磁盘上 plan.json 的 sha** + 该轮权重在盘（自描述，见下）。
- **两个被测试抓出来的真缺陷（都写进回归）**：① `ArtifactStore.start` 曾用**调用方传入**的计划 sha 做
  续跑判据，而目录写盘的是另一份格式（hub 走 `dump_plan` 规范形）⇒ 新会话拿着同一目录永远算不出相等
  的值，每次都被当成**新段**从 `start_it` 重跑——「关掉会话明天接着跑」静默退化成重跑。改为**写盘后
  再算**。② 起点快照曾往 `metrics.jsonl` 写一行（无 agg/report），使「账本一行 = 一轮、it 唯一」失效，
  逼下游用「过滤掉没有 report 的行」绕开——用过滤器掩盖一条本不该写的行。改为起点只落 checkpoint **不记账**。
- **开关与缺省**：`--run-iters N`（>0 一次领 N 轮；<0 到课程末尾）> `courses.<课>.run_iters` >
  `rl.run_iters` > **0 = 关**（历史行为逐字节不变）；等待上限 `--run-wait-sec` > `rl.run_wait_sec` > 8h
  （≈Kaggle 单会话上限）。要求 `--ppo remote`。段尾那一轮照常走本机结算（iteration 事件 / eval 派发 /
  归档），**段中间那些轮不派发本机 eval**——其权重不在本机归档，拿活指针充 W(it-1) 正是 P0 修过的
  「eval 标签超前一轮」。逐轮明细落成一条 `run_segment` 事件。
- **违反后果**：让云机自己算下一轮 ⇒ 第二份对集实现；省掉对集指纹门 ⇒ 产出不可信 shard 后才发现；
  产物只留 hub ⇒ 失联时产物与训练一起丢；`opt.tar` 缺失 ⇒ 续训静默丢 Adam 动量（D5）；用传入 sha 做
  续跑判据 ⇒ 每次续跑都重跑（本条抓出的缺陷）。
- **遗留（未做，不写成已做）**：真云端（Kaggle/Colab）端到端跑一次（本机无 GPU 节点）；段内**进度上报
  到控制台**（长段期间只有等待，逐轮指标要等段尾）；控制台启动弹窗的 `run_iters` 选项（现只有 rl-config /
  CLI）；push 传输等待预算仍 1800s（半离线的自然形态是 pull）。
- **落地**：`rl/plan.py`、`remote/artifacts.py`、`remote/run_loop.py`（含无 hub 续跑 CLI
  `python -m remote.run_loop --artifacts <dir>`）、`remote/protocol.py`、`remote/worker.py`、
  `remote/hub_client.publish_job`（计划进 payload）、`rl/loop_steps.py`（段长解析 + `_remote_run_segment`
  + `wait_timeout_sec`）、`rl/loop_core.py`（段优先 + 跳过中间轮 eval 派发）、`rl/cli.py`。
  回归：`test_plan.py`(8) + `test_run_loop.py`(11) + `test_run_segment.py`(12)；细节 `docs/nn.progress.md §61`。
## §2026-09-17-goalnn-offline-task-bundle（2026-09-17，全离线任务包：hub 导出 → Kaggle/Colab 上传 → 云机自主跑完；包是搬文件不是配网络）

- **背景（用户需求，2026-09-17）**：「hub 支持打包导出训练任务（课程、初始权重、代码），以
  kaggle/colab **官方支持方式**上传云机后，云机全程自主完成训练，并以官方方式提供产物打包下载；
  若中途能连上 hub，云机自动恢复产物在线回传」。半离线（`kind="run"`）已经解决了「云机不依赖
  hub 也能跑完 + 产物落盘」，但**任务本身还在网络上**（云机要轮询 hub 领 job）——全离线要的是
  「hub 关机也能开工」。
- **备选与否决**：① *让云机 clone 仓库 / pip 装依赖*（否决——官方入口是「数据集 / Drive / 文件
  上传」，不是「配网络」；rollout 用 TS、训练用 torch，装环境本身是一整天）；② *拆成一堆 curl +
  环境变量*（否决——把「搬文件」变成「配网络」，正是要消除的东西）；
  ③ *导出时在 hub 登记 job 并把 token 塞进包*（**部分否决**：包会四处搬运，凭据不进包；导出也
  不该以 hub 可达为前提）；④ *重建一份 manifest 而不复用训练侧的*（否决——解析者只有训练侧一份）。
  采用：**单 zip + 两条命令 + 逐件 sha 对账**；token 走 Kaggle secret / `--hub-token-file`。
- **契约（硬要求）**：① 包内 = 跑完整段所需的一切：`plan.json` / `manifest.json` /
  `init_weights.json` / `opt.tar`（Adam 动量，缺它续训静默归零）/ **`code.zip`**（同 commit 的
  python + TS 源码——云机没有仓，`build_pairs` 的重放靠它成立）/ `ts_code.zip`（rollout 运行时）；
  `task.json` 是索引（逐件 sha256 + 字节数 + run_id + it/end_it + hub_url）。② 导入 = 铺成**可直接
  续跑的产物目录**（`it-{it}/weights.json` + `opt.tar` + `ts_code/`），随后
  `python -m remote.run_loop --artifacts <dir>` 即可；`run_loop --bundle <zip>` 一步到位。③ 包是
  **不可信输入**：越界成员（zip-slip：绝对路径 / `..` / 盘符）、magic 不符、任一件 sha/字节数不符
  一律**拒收且一局不跑**。④ 导出侧三道自检：计划 sha 与 manifest 不符 / 缺 code.zip / 缺
  ts_code.zip 都拒导（缺件的包在云上只会以更难懂的方式失败）。⑤ 无 hub 运行**必须有代码快照**：
  standalone 入口硬门（`code.zip` 或 `code_cache/<sha>/` 命中二者其一），否则拿空 base_url 去下载、
  报一个跟真因无关的错。⑥ 导出**不记账本、不进待领池**（`publish_job(register=False)`）：这条腿的 job
  没有人会来领，记一条 `job_pending` 只会让控制台显示一条永远等不到工人的任务。
- **轮次对齐（最容易错的一处）**：loop 的 `it` = 已完成的下一轮 = 包里要跑的**第一轮**，所以
  `plan.start_it = it - 1`（区间语义是 `start_it+1 .. end_it`）、`max_iters = n`（**不是 n-1**：
  这里没有「job 自己那一轮」要扣——那一轮已由 hub 跑完，`args.out` 就是它的产物 = 包的起点）。
- **违反后果**：把 `code.zip` 换成就地仓库 ⇒ 云机与 hub 的代码版本可以不一致，而 shard 血缘、
  `wver`、`pairs_fp` 全都建立在「同一 commit」上；不逐件对账 ⇒ 一次截断的搬运变成一堆无法归因的
  怪结果；standalone 不卡代码快照 ⇒ 空 base_url 下载报错把真因藏起来；导出记账本 ⇒ 控制台出现
  永远等不到工人的 job（并可能被别的节点领去做错事）。
- **遗留（未做，不写成已做）**：① **自动补传**（用户同一条需求的后半句）——设计已定：
  云机每轮 best-effort `POST /offline/artifact`（权重 + 指标，按 `(run_id, it)` 幂等，
  first-write-locked；含轮末 `POST /offline/result`），`GET /health` 做轻量探活，连不上静默跳过，
  产物目录里 `delivered.json` 记已投递项；hub 侧落在 `<job_root>/offline/<run_id>/` 并记
  `offline_artifact`/`offline_result` 账本事件；token 走 secret 不进包；② 真云端（Kaggle/Colab）
  端到端一次（本机无 GPU 节点）；③ 控制台入口（导出按钮 / 进度显示）。
- **落地**：`remote/bundle.py`（导出/导入/索引/README/zip-slip 防护）、`remote/run_loop.py`
  （`--bundle` 导入即跑；代码与 TS 字节走 `preloaded`；standalone 的代码快照硬门）、
  `remote/hub_client.publish_job(register=False)`、`rl/loop_steps.py`（`--export-bundle` 导出钩子 +
  `BundleExportedError` 干净退出）、`rl/loop_core.py`、`rl/cli.py`。回归：`tests/test_bundle.py`(7)。

## §2026-09-17-goalnn-offline-reconnect-delivery（2026-09-17，产物补传：中途能连上 hub 就自动恢复在线回传；不可影响训练是唯一硬约束）

- **背景（用户需求，2026-09-17）**：全离线任务包（上一条）交付了「hub 关机也能开工」，需求的后
  半句是「如果训练中途发现可以联通 hub，云机也能自动恢复产物在线回传」。补传 = 产物目录之外
  的**第二份拷贝**（产物本来就已经落在节点本地目录），让控制面不必等人搬 zip。
- **备选与否决**：① *新增无鉴权 `GET /health` 探活*（否决——探针要回答「我能不能用这条链」，
  只证可达会让「token 配错」在第一次上传 ~1.9MB 后才暴露，且多一个向公网泄露「hub 在线」的
  端点）；② *逐轮产物覆盖写*（否决——同一轮权重是不可变快照，覆盖 = 谁先到谁定义历史，而补
  传天然会重传）；③ *自定义裸二进制容器*（**部分否决**：比复用 `encode_weights_json` /
  `encode_opt_tar` 省 33% 体量 ≈0.5s/轮，但要多养一种容器格式 + 两端实现；best-effort 旁路不
  值这个复杂度）；④ *坏 token 每轮重试*（否决——D9 是「同 IP 五次无效鉴权封 3600s」，等于
  自己把自己封掉，且配置错重试一百次也不会对）；⑤ *把补传做成一种 job*（否决——这条腿没有
  job 也没有租约，记 `job_pending` 只会让控制台显示一条永远等不到工人的任务，还可能被别的
  节点领走）；⑥ *把补传做成逐字节续传/断点续传*（否决——整轮 ~1.9MB，代价大于收益）。
- **契约（硬要求）**：① **训练永不因网络停摆**：`sync()` / `deliver_result()` 永不抛（传输异常
  收在 `_post` 里，异常冒到外层会跳过 `_save_ledger` ⇒ 已投递的轮次没落盘、下次整批重传）；
  没有重试预算、没有退避等待、没有阻塞调用。② **重启续投**：`delivered.json`（原子写）是唯一
  记账，`pending()` = 「磁盘上有权重且不在记账里」——**第一次连上时把之前攒的积压一次补齐**。
  ③ **幂等 + 首写锁定**：hub 按 `(run_id, it)` 落盘，重复投递回 `duplicate`（200，**不改写**；
  回 409 会让节点每轮把已投过的再传一遍）。④ **不可信输入边界**：`run_id` 是 hub 侧目录名 ⇒
  `sanitize_run_id`（只放行 `[A-Za-z0-9][A-Za-z0-9._-]*`、≤64、拒 `..`、非字符串即非法）；权重
  指纹须与实收字节相符；账本行自称的指纹也须与权重相符（不一致 = 产物目录自相矛盾）；体上限
  在声明长度上挡（413）。⑤ **401/403/400/413 → 本会话停用**（配置/形状问题，重试无意义）；
  5xx / 网络异常 → 只记日志（节流），下轮再试。⑥ **逐轮落盘之后才补传**（先自洽，再尽力
  出网），一次 `sync()` 有上限（`SYNC_CAP=4`），积压留给下轮。
- **违反后果**：补传一旦能阻塞或以任何方式上抛，就会把「产物落在节点本地即交付完成」这条
  本已成立的契约毁掉（网络变成训练的必要条件）；不首写锁定 ⇒ 重连/重启/重试会改写历史产物；
  `run_id` 不净化 ⇒ hub 上任意文件写；坏 token 每轮重试 ⇒ 自己把来源 IP 封一小时。
- **遗留（未做，不写成已做）**：① 真云端端到端一次（本机没有节点：整条链只在真 HTTP 端点 +
  替身 `run_job` 上跑过）；② 控制台显示补传进度（账本已有 `offline_artifact` / `offline_result`
  事件，读方未接）；③ push 模式节点侧没有 hub 地址 ⇒ 该形态默认不开补传（需要显式配 URL）。
- **落地**：`remote/offline_deliver.py`（新：探活 / 积压扫描 / 逐轮投递 / 段末摘要 / 记账）、
  `remote/hub_server.py`（`POST /offline/artifact`·`/offline/result` + `store_offline_*` +
  `offline/<run_id>/` 落位 + 账本审计事件）、`remote/protocol.py`（`sanitize_run_id` + 契约常量）、
  `remote/run_loop.py`（逐轮/收尾钩子 + `--hub-url`/`--hub-token[-file]`/`BATTLE_HUB_TOKEN`）、
  `remote/worker.py`（kind=run 默认开启）。回归：`tests/test_offline_deliver.py`(16)。
## §2026-09-17-goalnn-console-task-bundle-exchange（2026-09-17，控制台「导出任务包 / 导入产物即评估」；控制台不重造包格式）

- **背景**：用户 2026-09-17 需求——dashboard 支持导出 `task-<课程>.zip`；支持导入训练产物
  `deliver-<课程>.zip`，导入完成后**自动按课程配置跑 eval**。底层能力（`remote/bundle.py`
  导出包、`remote/deliver_zip.py` 导入器）同日先落地，本条目只裁决**控制台这一侧**接法。
- **备选与否决**：① 控制台自己拼包（否决——`--export-bundle` 已在 trainer 内，
  重造 = 第二份真相，`bundle.py` 模块注释写明）；② 导入后另写一套评估命令（否决——
  语料口径/双轨种子/账本格式会与 `evalA` 漂，读数无法与训练期对比）；③ 导出互斥键
  在 HTTP 请求里 `add`/`delete`（否决——导出跑几分钟，等于没有锁，第二次点会起第二个
  `run_rl` 抢同一门课的锁）；④ 上传体在控制台解包/校验（否决——zip 是人搬来的、
  最不可信，三道门（zip-slip / 形状 / 课程对账）留在 python 一侧，控制台只挡文件名课程
  与体积）。
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
  4. 跨语言常量（`DELIVER_IMPORT_JSON=` / `deliver-`）单列 `bundles/marks.ts`，测试直接读
     python 源码对账：改一边忘另一边会红（写岔是**静默**的，控制台会把「导入失败」错报）。
- **落地时抓到的两个缺陷**（均有回归）：① **导出互斥键**：键必须由**子进程退出**释放
  （轮询 pid），在请求里删 = 没有锁；② **`--export-bundle` 被轮内 shard 门误杀**——导出
  既不发 job 也不训练，`rollout_spec` 非空 + traj 有历史残留 shard 会命中「M3 上云轮必须
  空 shard」⇒ 任何跑过一轮的课都导不出包（实测 c6-chip）。判定抽成纯函数
  `_gate_round_shards`，例外**只**覆盖导出（`tests/test_export_shard_gate.py` 同时钉住
  「关掉 exporting 两条门照旧生效」，防顺手删门）。
- **边界（未做）**：① 真云端端到端一次（本机无节点）；② 导入是**同步**阻塞（几 MB 秒级，
  几十 MB 会让控制台这段时间不响应轮询——可接受，量大再挪后台+状态位）；③ 导出期间刷新
  页面会丢「生成中」态（只轮询产出文件）。
- **落地**：`dashboard/src/server/bundles/{export,import,marks}.ts`（新）、
  `server/run-python.ts`（新：一次性 python 的唯一入口）、`server/eval-a-run.ts`（新）、
  `server/server.ts`（`POST /api/deliverUpload` + `GET /api/taskBundle[Info]`，全部落在
  既有回环门控之后）、`web/app/panels/TaskBundlePanel.tsx`（新）+ `api-client.ts`、
  `api/route.ts`（`exportTaskBundle`，`evalA` 改调共享启动器）；回归：
  `dashboard/tests/server-api-task-bundle.test.ts`(17) +
  `nn-training/tests/{test_deliver_zip,test_export_shard_gate}.py`。
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
## §2026-09-17-goalnn-race-broadcast（2026-09-17，用户指令：只有一个课程在训练 + 多个云机 GPU worker ⇒ 竞速广播，最新 job 派给每个 worker，先回传者胜、后到者丢弃）

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
   ① **两种模式都发**（不是只补 offline）——hub 可能被以别的启动参数拉起来（例如
   `--offline c5`），只补 offline 会让「我明明点过在线」惄惄失效；② 「已运行」那条早退路径
   也回灌（hub 可能是手敲命令/别的终端拉起来的）。
3. **hub 拒绝/不可达 → 意图照样落盘**并如实报告「已记录，但 hub 未接受：<原因>（起 hub 时
   会按意图回灌）」：运维的决定不因为 hub 没起来而蒸发；但**也不谎报切换成功**。
4. **开关只在 hub 认识这门课时出现**（`hubOnline && hubSeen`）：hub 不认识就 400，
   给按钮等于给一个假承诺；开关是行按钮的**兄弟节点**（行本身是 `<button>`，嵌套 button 非法），
   hub 无应答时整个开关都不渲染。
5. 新增 `hubSetCourseMode`（`stack/hub-admin.ts`）：与 halt/resume 同性质的 `/admin/*` 客户端，
   返回人读错误而非抛（观测/运维面不得把页面带崩）。

**回归**：`dashboard/tests/course-mode.test.ts`（11 例：请求形状与 Bearer / 意图落盘 / 幂等重发文案 /
400 与连接被拒都**保留意图**且不抛 / 非法模式与空课程**一次都不打 hub** / 回灌两种模式都发 /
全失败逐课点名 / 无意图 → 空摘要 / 旧文件与脏键归一化）+ 总览面板 3 例（开关只在 hubSeen 时出现 /
文案按模式反转且行按钮仍 4 个 / 无 onAction 或 hub 无应答都不渲染 + `setCourseMode` 在面板与路由
**两侧**存在的接线断言）。gate：dashboard **588 pass / 0 fail**（574 → 588）+ `tsc --noEmit` 干净。

**仍未做**：操作面（单例 `trainingLoop` 卡片 + 「入队/暂停该课」）——暂停需要一个**跨进程控制通道**
（控制台不能直接调 python supervisor），需先定形态再动训练侧调度环。

---

## §2026-09-19-goalnn-slot-cap-5-and-loud-rejection（2026-09-19，plan R3-1：课程上限 5 + 越界槽位响亮拒启）

**背景**：用户口径「允许同时训练多个课程，上限先设为 5」（2026-09-18），而实现是
`dashboard/src/core/slots.ts::SLOT_COUNT = 4`，且 `slotOf` 对**越界值也静默回落 0**。
两者叠加 = **第 5 门课必然越界，然后静默用第 1 门课的 push 端口**：两门课的本机 push
互相顶掉，而且没有任何一行日志说这件事（只有端口占用冲突能间接看出）。这是真 bug，
不是「缺功能」。

**定案（三件事，都在 `slots.ts` 一个聚合点内）**：

1. **`SLOT_COUNT` 4 → 5**（= 用户口径的并行课程上限）。hub/隧道自 2026-09-18 起是单实例，
   槽位不再决定 hub 端口——它只剩「本机 push（worker_server）端口」与历史 metrics 命名，
   所以「槽位数 = 可并行课程数」是准确的语义，不存在第二个上限要同步。
2. **越界/非法槽位响亮拒启**（`slotOf` 抛错，点名课程 + 越界值 + 合法范围 + 总数）：
   未配置（`undefined`/`null`）⇒ 仍旧回落 0（legacy 单课行为零变化，§0.5-4）；
   **配置了非法值** ⇒ 报错。"只有未配置才回落"是本次要钉死的边界（旧注释与实现不符
   已在 `slotIssue` 的 docstring 里写明）。
3. **`slotError(cfg)` 配置级守卫 + 接进 `saveConfig`**：除非法槽位外，还拒绝
   **两门课显式配到同一槽位**（那才是撞端口的病根，比单课越界更早发生）并点名双方；
   未配置槽位的多课配置不拒（升级路上常态）。与 `capacityError` 同契约（null = 通过）。
   `allSlotPorts`（端口兜底清场清单）随 `SLOT_COUNT` 自动扩容，无需另改。

**取舍**：没有做「让课程数与槽位解耦」（plan R3-1 的备选）——hub/隧道已单例后槽位本就
只剩 push 端口，加一层抽象只会多一个概念；上限 5 与用户口径一致，越界现在响亮，成本为零。

**回归（先红后绿，§7）**：`dashboard/tests/training-multi-course.test.ts` 新增 W7 共 7 例
（上限 ≥5 / 5 门课 push 口互异且第 5 门不再回落 0 / 越界与非法值（5、-1、1.5、"2"）点名拒启
且 `slotPort` 同样响亮 / 未配置仍回落 0 且共享 hub 地址不受影响 / `allSlotPorts` 满 5 槽无重复 /
两课同槽位点名双方 / `saveConfig` 拒落盘且磁盘保持原样）。复现证据：修前该块 **4 红**
（`SLOT_COUNT` 实测 4、5 门课只算出 4 个端口、`slot: 4` 不抛而返回 0、`slotError` 不存在），
修后该文件 **35 pass / 0 fail**（W7 全绿）；dashboard 全量 **574 pass / 0 fail** + `tsc --noEmit` 干净。

---

## §2026-09-18-goalnn-multi-course-single-hub（2026-09-18，用户指令：多课程并行训练流程与操作重组；单进程服务所有课程 + hub 队列 + 分课程权重缓存）

- **背景**：多课程并行（`docs/multi-course-audit.md` / plan multi-course-parallel-training）当时定的形状是**课程 = 并行单元**：每门课各一套 hub-server / cloudflared / trainingLoop / localWorker / workerServe 进程，端口按槽位 `base + slot*10`，账本按课程键控。它解决了「第二课覆盖第一课登记」那类串账，但代价是进程数按课程线性增长（5 课 = 25 个进程），且 **hub 完全不知道「课程」这回事**（`--job-root`/`--jsonl` 都是每课程路径），所以没有跨课程的调度面：一门课积压 20 轮就独占自己的 hub，别的课的 worker 空转。
- **用户要求**（本次）：① 上限先设 5；② hubserver/trainingloop/selfNode/cloudflared **各只需一个进程**；③ hub 设 PPO 任务队列（push 按队列顺序推给空闲 worker，推送超时回落队首改推其它 worker；pull 按队列顺序分发给请求者）；④ 离线模式课程不实时派发 PPO、但接收 it 权重/指标回传；⑤ rollout 集群按课程缓存最近权重，避免同一份权重多次传递；⑥ 并行课程数 < PPO worker 数时用单课程那样的竞速；⑦ 面板重组（课程 select 不锁死、在训课程高亮等）；⑧ e2e 保证畅通。
- **决定（P1 已落地部分）**：
  - **单 hub 多课程 = 一个进程托管 N 份 `_JobStore`，磁盘契约逐字节不变**（每课程仍是 `tmp/<course>/remote-jobs` + `training_log.jsonl`）。这是本设计最关键的取舍：多课程只是「同一进程里多挂几份账本」，不是换一套磁盘约定 ⇒ 既有工具、既有 100 个单课程 hub 用例、`tmp/<course>` 约定全部照旧，回滚只需改启动参数。
  - **调度面 `_HubQueue`**：每课程一条 FIFO（沿用发布序）+ **跨课程轮转**（`rotation_order`，从上次派发的下一门开始——否则一门积压把其它课饿死）；**离线课程不参与实时派发**（只收回传）；`active_courses()`（非离线且有待领或在飞）是竞速分母。
  - **超时回落队首 + 改为派给别的 worker**：`claim` 在回收过期租约时记下「谁跑死的」（`_stale_holders`），队列在**还有别的活跃 worker** 时把该 job 避开那位前持有人（`may_avoid_stale_holder` 是闸，身份比对在 store 内——在队列层先读 stale 记录会踩时序，实测恒为空、避让永不生效）。独苗时恒允许自领（否则那台 worker 永远空转）。
  - **竞速口径改为按课程数**：`race_decision(..., active_courses=N)` 要求「窗口内不同 worker 数 **严格大于** 在派发课程数」。缺省 `active_courses=1` 时与旧口径（≥2 worker）**逐字节等价** ⇒ 旧调用与旧用例不变。多 hub worker 仍是安全条件的一票否决（scope 不随课程数放宽）。
  - **鉴权面提取为 `_AuthGuard` 且进程级一份**：多课程下不按课程各算一套失败计数（否则「同来源 5 次无效鉴权」的封禁阈值变成 5×N）；`_JobStore` 继承它，旧调用不变。单课程队列**借**那一份 store 的鉴权/竞速/停机状态（不是洁癖：既有用例会在 store 上预热封禁态再发 HTTP）。
  - **补传按课程归属**：`POST /offline/artifact|result` 的课程归属顺序 = 体里的 `course`/`course_name` → `?course=` → 已有 `offline/<run_id>/` 的课程（补传天然重传续投，第一条建目录后续自动归位）；归不到 → 400 且点名该填哪个。
  - **权重桶按 (course, kind)**（rollout 集群那一半）：改造前 5 门课的新 sha 全挤进同一个 `rollout` 桶（64 桶 LRU 被分摊，历史深度掉到 ~13 轮，慢节点回来取旧权重就 409）。纯逻辑出列到 `tools/agent/weight-buckets.ts`（可单测）。查找顺序 = 本课桶 → 旧单课程桶 → 任意同 kind 桶（sha 内容寻址 ⇒ 同一份字节），因此**升级顺序自由**（新训练侧 + 旧 agent / 旧训练侧 + 新 agent 都不破）。课程身份走**进程级环境变量 `RL_COURSE_NAME`**（在 `rl/config.apply_course` 里挂——那是训练进程唯一知道课程名的地方；出口散在 6 个文件的闭包里，逐点穿参要改十几个签名且每加一处都要记得再穿）。
  - **避免同一份权重多次传递** = 上传前预检 `GET /v1/weights?sha=&kind=&course=`（命中返回 `cached:true`，调用方连体都不传）；任何不确定（旧 agent 404 / 5xx / 空 sha）一律**保守上传**——少传一次是省流量，错判不传是 `409 wver not cached here` 停活。
  - **CLI**：`--course NAME[=online|offline]`（可重复）+ `--traj-root`（派生每课程 job-root/jsonl）；`--job-root`/`--jsonl` 保留为单课程旧形状，两者同时给 = 响亮拒启。观测面新增 `GET /admin/queue`（每课程 深度/在飞/心跳/队首 + 轮转游标 + 两个竞速判据数）与 `GET|POST /admin/courses`（看课程表 / 热切 online|offline，volatile）。
- **备选与否决**：把课程状态做成「无状态执行器 + 任务队列」（用户 2026-09-18 追问的方向）——**不作为本轮**：它要求把 12 个跨轮内存变量逐个迁到磁盘并把门禁语义从「内存计数」改成「扫账本」，是一次独立的、风险集中在**审计**上的改造（漏一个 = 静默语义漂移），已定为本轮之后的 P2（形态由用户拍板 = 迭代任务化；验收 = 「断开续跑 == 连续跑，逐字节等价」）。逐调用点加 `course` 参数而不是进程级 env —— 否（十几个签名 + 每加一处都要记得穿，且「这个进程是哪门课」本就是进程级身份）。给权重上传加「先 HEAD 再 POST」之外的第三条路（只在 hub 侧做去重）——否，白传发生在**训练侧到节点**这一段，hub 管不到。竞速的 auto 判据保留「worker 自报 scope」——是（它同时挡住「worker 还配着旧 hub」这类现场，见同日另一条）。
- **违反后果**：让多课程退回「每课一套进程」⇒ 进程数线性增长且无跨课程调度（一门课的积压独占自己的 worker 池）；把「找不到归属」写成空串而不是 None ⇒ 单课程队列（课程名**就是空串**）每一次 `/jobs/next`/补传都 500/400（本次实测踩过两次）；在队列层判 stale 身份 ⇒ 避让永不生效、超时过的 job 只会还给跑死它的那台；按课程各存一份鉴权计数 ⇒ 封禁阈值 5×N；按课程各存一份权重桶却让旧调用方落错桶 ⇒ 慢节点 409 停活（因此保留旧桶 + 同 kind 兜底查找）。
- **落地**：`nn-training/remote/protocol.py`（`COURSE_MODE_*`/`COURSE_MODES`/`parse_course_arg`/`rotation_order`/`may_avoid_stale_holder`/`race_decision(active_courses)`）、`nn-training/remote/hub_server.py`（`_AuthGuard` 提取、`_JobStore` 租约持有人身份 + stale 避让 + `inflight`、`_HubQueue` 调度面、`as_hub` 兼容包装、`make_server` 双形状、`/admin/queue`+`/admin/courses`、补传按课程路由、`--course`/`--traj-root`）、`nn-training/dist_common.py`（`COURSE_ENV`/`course_name_of`/`weights_cached_on_node`/`post_weights_cached`/`post_weights(course)`/`fetch_task(course)`）、`nn-training/rl/config.py`（`apply_course` 导出进程身份）、`nn-training/rl/{eval_dispatch,batch_eval,dispatch,queue_local,bc_eval}.py`（改用带预检的上报）、`tools/agent/weight-buckets.ts`（新，桶纯逻辑）+ `tools/agent/sampler-agent.ts`（按 (course,kind) 分桶 + `GET /v1/weights` 预检 + 任务 URL 带 `course`）；回归：`nn-training/tests/test_multi_course_hub.py`（17 例）、`nn-training/tests/test_weight_course_buckets.py`（12 例）、`tests/agent/weight-buckets.test.ts`（13 例）。
- **本轮未做（P1 余下 + P2）**：hub 中介的 push 派发（hub 主动推给空闲 worker + worker 登记入口 + 周期 ping 探活）与训练侧 push 改走 hub；单隧道（hub/cloudflared 收敛为单例，随单 hub 自然成立）；dashboard 重组（课程 select 自由可切 + 在训课程高亮 + 队列总览 + worker 登记入口）；多课程单 hub 的 e2e；P2 = 训练循环迭代任务化。

## §2026-09-18-goalnn-hub-push-dispatch（2026-09-18，用户指令：hub 中介的 push 派发 + worker 登记入口 + 周期探活，训练侧 push 改走 hub）

- **背景**：P1 已把 hub 变成多课程调度器（`_HubQueue`），但派发方向仍是 **pull 单边**：只有 worker 自己来 `GET /jobs/next`。而线上 `push` 模式是训练侧**直推**云机隧道（DECISIONS §340 补充 4）——于是「队列顺序 / 空闲判定 / 超时回落 / 多课程公平」这四件事在 push 腿上**一件都没有**：训练侧只能对着几张卡盲推，一台忙就等于等它，一台死就要等满 30min 才失败。用户口径（本次）：push 模式下 hub 按队列顺序轮番向空闲 ppo worker 推送任务；已推送任务超时则回落队首并改为推送其它 worker。
- **决定**：
  - **派发权归 hub，不归训练侧**：`manifest.dispatch = "push"` 是训练侧唯一要说的话（写定者 = `--remote-transport hubpush`，或 auto + `courses.<课>.hub_push`/`rl.hub_push`）；之后 hub 认领这份活自己推，训练侧回到 `wait_job`——与 pull 完全同一条收尾链（三重校验 → 落位 → 记账），**不新增第二条客户端**。
  - **登记表 = rl-config 的 `gpu_push` 节点**（控制台的 worker 登记入口回写它），按 mtime 热重载 ⇒ 写完配置不必重启 hub；判据与训练侧 `_gpu_push_nodes` **同一把尺子**（`gpu_push` + `enabled` 缺省 true + 非空 url）。运行时增删走 `POST /admin/push-workers`（volatile，且**在重载后仍然保留**——临时挂的机器不该被一次 mtime 变化静默抹掉）。
  - **探活 = 周期 `GET /ping`**：「在线」= 答过 200 且连续失败 < 3 次；**从没答过 = 不在线**（宁可这一拍不推，也不往一台可能是死的机器上推几十 MB）；**失败但未达阈值 ⇒ `busy=True`**（状态未知必须当局忙，否则一次隧道抖动就会让调度器认为它空闲并二次推送同一门课）。
  - **挑活**：跨课程轮转（复用 P1 的 `rotation_order` 游标）+ **每课程至多一份在途**（课程内轮次有硬序）+ 只推**队首**且队首必须是 push job（不越过它推后面的——那会把轮次跑成乱序）+ 四道闸（在线 / 不忙 / 在飞 < concurrency / 不在本次避让名单）。
  - **回落 = 放租约 + 避开那台 + 立刻换人**：超时（缺省 45min 兜底）、连续探活失败、拒收（409/428）、结果入账被拒，一律走这条路。job 留在该课队首（「每课程单在途」让这条天然成立），**不是**「跑得久就抢回来重算」——那会白扔已算掉的大半轮。
  - **结果两条腿共用一个入账函数**（`push_dispatch.accept_result`）：云机 POST 与 hub 代发取回的校验（对账 → 租约 → 首写锁定）不可能一条有一条无；推模式若跳过对账，一份对不上账的结果会被静默写成一轮「看起来正常」的训练。`_post_result` 改为调用它。
  - **租约同源**：hub 代持的推送用 `claim(worker_id="push:<id>")` + 轮询期间心跳续租 ⇒ pull worker 不会把同一份活领走；`/admin/queue` 的 inflight 持有人带 `push:` 前缀，一眼分清哪条腿（避让记录也按同一身份比对）。
  - **可观测**：`GET /admin/push-workers`（登记表 + 探活态 + 在途/避让/计数）；hub 侧实测字节回写 job 的 wire 账（`record_push_wire`），训练侧 `_wire_from_result(is_push=...)` 的读法与直推**逐字一致**——两种 push 的可观测性不该一个有一个无。
  - **缺省关**（`--push` 才启用）：不打开时连探活线程都不起，既有单课程用例与线上行为逐字节不变；控制台经 `rl.hub_push` 透传（`dashboard/src/stack/specs.ts`），`--push-config` 显式指向仓库那份 rl-config（登记表住那里，指到 per-course 目录 = 登记表恒空）。
- **备选与否决**：训练侧自己维护 worker 池并推 —— 否（那是把 P1 刚收敛掉的「每个训练进程各算各的」再放大 N 倍，且多课程下训练进程看不见别课的占用）；hub 侧另写一套 job 上传 —— 否（`push_client.submit_job` 已有 v2 体 / 内容寻址 / 428 补传 / 409 退避，两条腿必须是**同一份**传输语义，否则失败分类会漂）；按「worker 自报 scope」决定要不要推 —— 否（那是 pull 竞速的判据，与「这份活该谁推」无关）；把派发做成「拉不到时才兜底」 —— 否（push 腿的队头阻塞原样保留）；「跑太久就抢回来」当超时 —— 否（PPO 一轮 10–30min，正常与卡死无法区分，抢回来 = 白扔算力）。
- **违反后果**：训练侧与 hub 各写一份 `dispatch` 字面量 ⇒ job 永远躺在队首（两边日志都很安静，最难查的一种）；「从没答过」当在线 ⇒ 往死机器上推 payload 并白等一轮；失败未达阈值不当忙 ⇒ 一次抖动触发同一门课的二次推送（两份 PPO 抢同一轮）；不卡「每课程单在途」⇒ 同一课轮次并行跑、后一轮拿到过期 init 权重；push 结果跳过对账 ⇒ 错结果静默落盘成「看起来正常」的一轮。
- **落地**：`nn-training/remote/push_dispatch.py`（新：`PushWorkers` 登记表 + 探活、`PushDispatcher` 派发拍 + 每 job 线程 + 回落、`accept_result` 共用入账）、`nn-training/remote/protocol.py`（`DISPATCH_HUB_PUSH` / `PUSH_*` 常量、`push_worker_from_node` / `pick_push_worker` / `push_job_wants_hub_push` / `push_worker_id_of`）、`nn-training/remote/hub_server.py`（`_post_result` 改走 `accept_result`、`/admin/push-workers`、`--push` / `--push-config` / `--push-poll-sec` / `--push-timeout-sec`、`record_push_wire`）、`nn-training/remote/hub_client.py`（`publish_job(dispatch=)`）、`nn-training/rl/loop_steps.py`（`hubpush` 传输 + `_course_hub_push` + `resolve_hub_push` + wire 口径）、`nn-training/rl/cli.py`（choices 四值）、`dashboard/src/{stack/specs.ts,core/types.ts}`（`rl.hub_push` 透传）；回归：`nn-training/tests/test_hub_push_dispatch.py`（14 例）、`nn-training/tests/test_remote_transport.py`（+5 例）、`dashboard/tests/hub-server-push-arg.test.ts`（3 例）。
- **本轮未做（P1 余下）**：控制台「worker 登记入口」UI（写 `nodes[].gpu_push`）与面板重组（课程 select 自由可切 / 在训课程高亮 / 队列与 push 总览）、单隧道（hub/cloudflared 收敛为单例）、多课程单 hub 的端到端 e2e（训练侧 hubpush → hub → 真 worker_server + 假 PPO）。

## §2026-09-18-goalnn-console-worker-register-and-overview（2026-09-18，用户指令：控制台 worker 登记入口 UI + 面板重组）

- **背景**：P1 的 python 侧（多课程单 hub + hub 中介 push 派发）已就位，但控制台还停在单课程时代：课程 select 在 hub 运行时**被锁死**（§367 的保护，当时 hub 按课程建 jobRoot/日志目录——那个前提已被「单进程托管 N 份账本」推翻），「正在训练」只高亮**一门**课（多课程并行时其余在训课程在界面上隐形），`/admin/queue` 与 `/admin/push-workers` 两个观测面**没有任何读方**，worker 登记只能手改 rl-config。用户口径（本次）：dashboard 提供 worker 登记入口、回填 rl-config.json、hub 周期 ping 检测联通。
- **决定**：
  - **登记 = 配置编辑，不是第二份登记表**：面板只 upsert rl-config `nodes[]` 里那条 `gpu_push` 条目（hub 按 mtime 热重载），写完顺手 `POST /admin/push-workers {action:"reload"}` **best-effort** 让 hub 立刻拾取（失败不算失败——配置已落盘，下一拍热重载兜底）。两处各存一份登记必然漂。
  - **探活失败不拦登记**：云机还没开机是常态，拒登记会把「先配好、再开机」这条路堵死。返回值如实分流——`ok:true`（ping 通）/ `ok:false`（已登记但 `/ping` 不通，并点名查 worker_server / 隧道 / authKey），与 `setNodeConcurrency` 的 `done(smoke.passed, …)` 同款语义。
  - **课程指针收口**：改 url 或删节点时，`courses.<课>.push_node_url` 里指向旧 URL 的指针**一并改写/清除**。留着它就是「指向 config 里不存在的 URL」⇒ python `_gpu_push_nodes` 匹配 0 个节点后**静默回落 pull**，而操作员看到的一切正常（2026-09-15 同类事故的入口）。
  - **两列探活刻意分开**：面板直探 `{url}/ping`（登记那一刻的体检）与 hub 周期探活结论（调度器此刻认不认为它在线的**唯一判据**）各占一列；两列不一致本身就是信号（面板通而 hub 判离线 ⇒ hub 还没重载配置）。
  - **课程 select 解除锁定**（含历史课程自由可切）：切课程只改「本浏览器看哪门课」+ 本机操作员课程，不碰任何在训进程；多课程并行下旧锁定会把查看/切换彻底锁死。
  - **「在训课程」的唯一判据 = registry 里 `trainingLoop` 进程存活**（服务端 stamp `trainingCourses`，课程 select 的 🔥 与总览的「在训」列同源）。不问 hub（它只知谁派过活，训练停在两轮之间时一无所知），不问 console-state（那是「在看的课」）。
  - **并行总览**：hub 行（应答基址 / 在派发课程数 / 活跃 worker 数 / 竞速 / 停机 / **最近派发**=轮转游标）+ 每课一行（在训 / 离线 / iter / 队列深度 / 在飞）。这一行回答的是「某门课为什么在饿着」的四种可能，在日志里要靠猜。
  - **每课 iter 取账本尾行、且只认 `iteration` 事件**：`training_log.jsonl` 是**多写者**文件（训练侧写 iteration，hub 追加 job_completed/job_failed），把 `job_completed` 的 it 当轮次会读出「还没跑完的那一轮」。
  - **hub 基址解析不新增配置键**：按 registry 里**活着**的 hub 条目逐个试 `/admin/queue`，第一个应答者即观测源。单 hub 服务多课（新形状）与每课一 hub（旧形状）都能读对；都没有 → null，面板显示「hub 无应答」而不是编一个地址。
  - **观测面一律容错**：任何失败（无 hub / 401 / 坏 JSON / 账本不可读）→ null/空态/零值，绝不把 `/api/state` 带崩；总览与登记表共用一次 hub 探测（5s TTL + 单飞）——进程级全局观测不该按查看课程各探一遍。
- **备选与否决**：把登记做成控制台自己的 worker 表 —— 否（hub 与训练侧都按 rl-config 判定，第二份表 = 三处口径）；登记时 ping 不通即拒 —— 否（见上，堵死正常工序）；把总览塞进慢快照 —— 否（慢快照按课程键控，进程级全局观测会乘上查看课程数）；由 hub 的 course 表推「在训」 —— 否（hub 不知道训练循环是否停在两轮之间）；**组件卡片按「单例角色 / 按课程」拆两种形状** —— **推迟**：卡片的数据源仍是 per-course 账本条目，而 hub/隧道收敛为单例（单隧道那一步）之前拆形状只会做出一个「看着像已经支持多课程」的假象。
- **违反后果**：面板另存一份登记 ⇒ 三处漂、hub 与训练侧对「哪几台能接活」判断不一致（症状是 job 永远躺在队首）；登记拒掉 ping 不通的机器 ⇒ 正常工序被堵、操作员绕开面板去手改配置；改 url 不收口课程指针 ⇒ 指向死 URL ⇒ python 静默回落 pull（故障被伪装成正常）；用 hub 的 job 历史判「在训」⇒ 训练间歇期课程被从 UI 上抹掉；把 `job_completed.it` 当轮次 ⇒ 总览显示还没跑完的那一轮。
- **落地**：`dashboard/src/web/view/course-overview.ts`（新：视图类型 + `parseHubQueue` / `latestIterFromLedgerTail` / `overviewCourseNames` / `buildCourseRows` / `validWorkerId`）、`dashboard/src/stack/hub-admin.ts`（新：`hubCandidates` / `liveHub` / `hubPushWorkers` / `hubReloadPushWorkers` / `probePushWorker` / `withWorkerProbes`）、`dashboard/src/server/api/overview.ts`（新：`trainingCourses` / `getHubAdmin` 5s 缓存 + 单飞 / `buildOverview` / `buildWorkerRegistry` / `courseIter`）、`dashboard/src/server/actions/workers.ts`（新：`registerPushWorker` / `removePushWorker` / `reloadPushWorkers`）、`dashboard/src/server/api/route.ts`（三个动作接线）、`dashboard/src/server/server.ts`（动作后与慢快照同一时机置空 hub 观测缓存——一个失效点，不在 route 层重复）、`dashboard/src/server/api/state-view.ts`（注入 `trainingCourses` / `overview` / `workerRegistry`）、`dashboard/src/web/app/panels/CourseOverview.tsx`（新）、`dashboard/src/web/app/panels/WorkerRegistry.tsx`（新，表单独立成 `WorkerForm` 以便 SSR 断言）、`dashboard/src/web/app/app.tsx`（解锁课程 select、在训课程全高亮、挂载两个面板）、`dashboard/src/web/theme.css`（`.tc-cov*` / `.tc-wreg*`）；回归：`dashboard/tests/server-api-overview.test.ts`（12 例）、`dashboard/tests/server-actions-worker-register.test.ts`（16 例）、`dashboard/tests/web-app-course-overview.test.ts`（12 例），并改写 `dashboard/tests/web-ssr-readonly.test.ts` 的课程 select 用例（锁定语义随本轮变更）。
- **本轮未做（P1 余下）**：① 单隧道——hub / cloudflared / trainingLoop 收敛为单例（现在仍是 per-course 拉起），随之把组件卡片拆成「单例角色」与「按课程」两种形状；② 多课程单 hub 的端到端 e2e（训练侧 hubpush → hub → 真 worker_server + 假 PPO）。
## §2026-09-18-goalnn-single-hub-single-tunnel（2026-09-18，用户指令：hubserver/cloudflared 只需要开一个进程，就能同时支持所有并行训练课程）

- **背景**：P1 的前两步（多课程单 hub 的 python 侧、hub 中介 push 派发、控制台 worker 登记）已落地，但**进程形状**还是单课程时代的：控制台按课程拉起 hub-server（`hubServers[<课>]`，端口 = 槽位算术）与 cloudflared（每课一条隧道 + 每课 metrics 口 + 同槽位接管）。用户口径（本次）：hubserver / selfNode / cloudflared 都只需要开一个进程就同时支持所有并行课程。注：**trainingLoop 不在本次范围**——它是「有状态会话」（进度指针 / torch 与 Adam / 在飞任务集 / 资源占用），收敛成单进程要先把会话改成任务队列，属 P2；本轮只动 hub 与隧道这两条「无状态中介」。
- **决定**：
  - **共享槽 = 空串 `''`**：hub/隧道在账本里仍住 `hubServers` / `cloudflareds` 两张表，槽固定 `''`（沿用既有的「无课程槽」，语义正好重合：共享实例不属于任何单门课）。**所有读写一律经 `core/registry.ts::scopeOf(key, course)`**（唯一归一入口）。为什么不改成扁平单例键：旧账本里的 per-course 条目必须继续**可见、可枚举、可停止**（静默失监督是事故），共用一张表天然做到。
  - **课程表从盘上发现，不做注册**：hub 加 `--discover`，扫 `<traj-root>/<课>/{remote-jobs,offline}`，**新鲜窗口**（1h）内的自动登记、登记后不撤销。训练侧把 job 发布到 `tmp/<课>/remote-jobs` 就是「这门课在跑」的文件系统事实（hub 与 trainer 共享同一份盘）——再加一条 HTTP 注册旁路就是「会失败、会乱序、会忘了调」的第二事实源，而漏注册的后果是那门课**永久饿死**（跨课程轮转表里没有它），表面却一切正常。扫描有两处触发：`claim_next()` 前置（带 2s 最小间隔闸，新课程下一次轮询就能被领到）+ 后台节拍线程（5s，push 模式/无 worker 时兜底）。
  - **新鲜窗口而非「看到目录就登记」**：几天前的陈旧实验目录磁盘形状完全相同且同样残留 pending job，误登记会把死课程的 job 继续派给真 GPU worker（白烧租约）。窗口 1h ≫ 单个 PPO 轮次（10–30min），活课程每轮都在窗口内。
  - **单实例切换必须搬状态**：单课程时 halt / race / worker 登记 / 鉴权计数住在那一份 `_JobStore` 里（既有用例直接预热 store 字段），课程数变 2 时 `_adopt_solo()` 把它们搬到队列自己身上——不搬就是「多发现一门课，把停机达令、竞速模式、鉴权闭锁一起悄悄清了」。
  - **hub 端口 = `rl.hub_port` 基数本身**（`sharedHubPort`），隧道 metrics = 基数+1（`sharedTunnelMetricsPort`）；课程槽位此后只决定 push 端口。`slotPort(…, 'hub')` 不再有任何调用面（grep 门禁守）。
  - **旧形状换代接管**：共享实例启动前 `supersedeLegacyInstances(key)` 把所有非共享槽的旧条目**活则杀、死则清账**并点名。旧 hub 与共享 hub 服务的是同一个角色（同一棵 job 目录树），两个并存 = 双派发 / 双租约 / 结果回错家；旧隧道则在同一 metrics 口上撞车，且幸存者的 200 会被当成新隧道的就绪。
  - **旧条目拒重建（fail-closed）**：`restartSpecFor('hubServer'|'cloudflared', <非空课程>)` → null + 响亮告警。用共享 spec 去重建一个 per-course 条目等于凭空再造一个 hub。
  - **URL 是全局事实**：`writeRemoteHubUrl(url)` 只写单键 `rl.remote_hub_url`；python 侧（run_rl / run_bc）删掉「按课程回填 `rl.remote_hubs[<课>]`」那段——留着它就会把训练指向一个已不存在的每课隧道（控制台写单键，两边不一致）。
  - **配置面简化**：`resolveCfTunnel` / `cfTunnelArgs` 去掉 course 参数（一条隧道没有「谁的 cf_protocol 说了算」的问题）；`courses.<课>.cf_protocol` 读旧配置不报错但不再生效。
  - **共享在 UI 上必须标出来**：`ComponentView.shared` + 卡片「共享」徽章。不标，操作员会以为「这门课自己的 hub 停了」而重复启动。
  - **停机达令必须按课程（本轮的连带必修）**：halt / resume / status 一律支持 `?course=<课>`（空 = 全课程，供不带课程上下文的全局读取）。单课程时代「一个 hub 一份 halt 布尔」≈ 按课程；收敛成单进程后那个布尔升格为**进程级**——A 课门禁 ABORT 就会把 B 课的云机一起停掉，而被连坐的课表现只是「云机莫名停机」（最贵的一种静默故障）。故 hub 的停机态改为按课程存（`_HubQueue.set_halt(course)`，`is_halted(course)`），训练侧（门禁 `loop_guards` 经 `dist_common.course_name_of()`、`run_rl`/`run_bc` 的 `clear_halt_on_startup`）与控制台（`hubAdminOk(cfg, path, course)`）各自带上课程名。
  - **本机 worker 与共享 hub**：`localWorker` 的 `--poll` 指向共享 hub（两个本机 worker 轮询同一地址），「领到哪门课的 job 就干哪门课的活」——job 自带课程快照，结果按 job_id 回家；隔离面只剩工作目录与日志（per-course）。
- **备选与否决**：让控制台在启动时把课程表传给 hub（`--course A --course B`）+ 新课程走 HTTP 热加 —— 否（启动顺序脆弱 + 漏调即永久饿死；盘上事实已经够用）；把 hub/隧道改成扁平单例键 + 一次性迁移 —— 否（多出来的迁移要么静默丢监督、要么逼着挑一个 per-course 赢家，收益只是「形状好看」）；看到 `remote-jobs` 目录就登记（不做新鲜度判定）—— 否（见上，误登记 = 真金白银）；每课一条隧道 + 共享 hub —— 否（同一 hub 的连接多几份出网状态，还复现 2026-09-17 的隧道回源 → 127.0.0.1 归并 → D9 闭锁连坐训练主循环）；**训练循环也一并收敛** —— 否（有状态会话，P2 任务队列改造的命题）。
- **违反后果**：拿课程槽去读写共享组件 ⇒ 「看 A 课的卡片说 hub 停了」（其实在跑）、「停 A 课把共享 hub 杀了」；不搬 `_solo` 状态 ⇒ 新开一门课静默清掉停机达令 / 竞速模式 / 鉴权闭锁；不换代接管 ⇒ 两个进程读同一棵 job 目录（双派发、双租约、结果回错家）；允许 per-course 重建 ⇒ 凭空再造一个 hub；继续读 `remote_hubs[<课>]` ⇒ 训练指向不存在的每课隧道（job 永远发不出去）；误登记陈旧课程目录 ⇒ 死课程的 job 派给真 GPU worker；停机达令仍走进程级布尔 ⇒ 一门课的门禁 ABORT 连坐停掉其它课的云机（症状是「云机莫名停机」，极难归因）。
- **落地**：python —— `nn-training/remote/hub_server.py`（`--discover` / `--discover-sec` + `DISCOVER_SCAN_SEC` / `_HubQueue.add_course` / `_adopt_solo` / `discover` / `_course_dir_live` / `claim_next` 前置扫描 / 后台节拍线程；`--course` 显式路径与旧单课程 `--job-root/--jsonl` 行为不变），`run_rl.py` / `run_bc.py`（删 per-course hub URL 回填，只认单键；`clear_halt_on_startup(course=)` 按课程清停机态）、`remote/hub_client.py`（`set_cloud_halt(course=)` / `hub_halted(course=)`；hub 侧 `/admin/workers/{halt,resume,status}?course=`）、`rl/loop_guards.py`（门禁 ABORT 带本课课程名）；dashboard —— `core/registry.ts`（`SHARED_COMPONENTS` / `isSharedComponent` / `scopeOf`）、`core/slots.ts`（`sharedHubPort` / `sharedHubUrl` / `sharedTunnelMetricsPort`）、`core/config.ts`（`writeRemoteHubUrl` 单键）、`stack/specs.ts`（`hubServerSpec(cfg)` / `cloudflaredSpec` 单例化 + `--traj-root <REPO_ROOT>/tmp --discover`；`cfTunnelArgs` 去 course）、`stack/hub.ts`（`hubServerHealthy(cfg)` / `stepHubServer(cfg)` / `stepCloudflared(cfg, noTunnel)` / `supersedeLegacyInstances` 取代 `supersedeSlotTunnels`）、`dashboard/src/server/actions/cloud-halt.ts`（`hubAdminOk(cfg, path, course)` 拼 `?course=` + `triggerCloudHalt` / `markCloudHaltRecovered` 按课程下发达令）、`server/api/{component-meta,views}.ts`、`launch/cli.ts`、`stack/{hub-admin,local-worker}.ts`、`web/view/console-types.ts`（`shared?`）、`web/app/panels/ComponentCards.tsx` + `web/theme.css`（「共享」徽章）；回归 —— `dashboard/tests/single-hub-tunnel.test.ts`（新，7 例：槽位唯一 / 地址唯一 + grep 门禁 / 旧条目拒重建 / URL 全局）、`dashboard/tests/training-multi-course.test.ts`（改写 W1-W4-W6-P2-P3）、`dashboard/tests/cloud-halt.test.ts`（+2 例：假 hub 上的线上字节形状 `?course=` 与「A 课停机不碰 B 课记录」）、`nn-training/tests/test_loop_gate_{nopark,soft_remediate}.py`（门禁 ABORT 断言带课程名）、`dashboard/tests/local-worker.test.ts`、`hub-server-{push,race}-arg.test.ts`、`training-port-reclaim.test.ts`、`nn-training/tests/test_multi_course_hub.py`（+8 例：发现判定 5 例 + 真进程 `--discover` 主流程 1 例 + 节流/状态搬迁）。
- **本轮未做（P1 余下）**：多课程单 hub 的端到端 e2e（训练侧 hubpush → hub → 真 worker_server + 假 PPO）；组件卡片按「单例角色 / 按课程」**分组**（本轮只做到「共享徽章 + 共享槽取数」，卡片仍是同一形状）。

## §2026-09-18-goalnn-loopback-http-no-proxy（2026-09-18，门禁实测红：本机 127.0.0.1 请求被环境代理截走）

- **背景**：`tests/test_offline_deliver.py::test_offline_endpoints_require_auth` 在全量门禁里偶发红：hub-server 日志明明白白写了两次 `401`（鉴权边界是对的），测试侧读到的却是 **502**。根因不在被测代码：本机**用户级**环境带 `HTTP_PROXY`/`HTTPS_PROXY`（指向局域网代理），而 `no_proxy` 里写的是 `127.*` 这种通配——Python 的 `urllib.request.proxy_bypass()` 只认 `host == entry` / `*.suffix` / `.suffix` 三种形式，**不认 `127.*`**，实测 `proxy_bypass("127.0.0.1") is False`。于是每一发去 `http://127.0.0.1:<hub|worker|agent>` 的请求都被送进外部代理再转回来（代理抖动/回错误页 ⇒ 502），本机训练也凭空多一跳。
- **决定**：
  - **回环地址的 HTTP 一律绕开环境代理**，实现落在唯一的 `remote/net_http.py`（`is_loopback` / `no_proxy_opener` / `urlopen` 替身，与 `urllib.request.urlopen` 同签名、返回值同形）。
  - 四条本机出口全部接上它：`remote/hub_client.py::_request`（训练侧↔hub）、`remote/push_dispatch.py::_http`（hub↔GPU worker 的探活与推送）、`remote/worker.py::_request`（worker↔hub）、`remote/offline_deliver.py::_urllib_opener`（产物补传）。**非回环分支保持原样**：`net_http.urlopen` 在非回环时仍调 `urllib.request.urlopen`（保住测试的 monkeypatch 缝），`worker._get_opener()` 的显式 ProxyHandler 只服务非回环（Colab userspace 实测需求，不受影响）。
  - **判据只看 host**：`127.0.0.0/8`、`::1`、`localhost`、`*.localhost`；不做 LAN（10./172./192.168.）例外——那些在架构上不是「本机通信」，擅自绕过会改掉真实拓扑下的行为。
  - **测试侧另加一层兜底**：`nn-training/tests/conftest.py` 把**精确回环主名**（`127.0.0.1` / `localhost` / `::1`）补进 `no_proxy`/`NO_PROXY`——`proxy_bypass()` 认精确匹配，所以 8 个仍用**裸 `urllib.request.urlopen`** 打本机临时端口的既有用例（hub/worker/agent 的真实进程用例）一并脱离代理；生产侧不靠环境变量（就在 `net_http` 里）。两层分工：**生产靠代码、测试靠环境**，任一层单独失效都不会再让门禁变红（2026-09-18 实测：只改生产侧时 `test_multi_course_hub` 在满载下仍会吃到代理的 `Errno 111`）。
  - 回归（复现→修复，§7）：`nn-training/tests/test_loopback_http_no_proxy.py`（6 例，含一例钉 conftest 那层环境归一）——环境代理指到**死端口**后打本机真服务，三条出口必须仍通（修复前 `ConnectionRefused`，已用临时脚本实测 raw urllib 挂 / `net_http.urlopen` 200），另有一例钉「非回环仍走 urllib 默认」。
- **备选与否决**：让运维去改用户级 `no_proxy`（写成 `localhost,127.0.0.1`）——否（改环境不修代码，换台机器/换个人就复发，且**云机侧**同样可能带着代理变量）；一处处地改 `urlopen` 调用点、不建公共模块——否（同一个坑会被下一个新写的本机 HTTP 路径再踩一次，且「哪几条出口算本机」会失去唯一答案）；把回环判断塞进 `remote/worker.py::_get_opener()`——否（那个 opener 的存在意义就是「Colab 必须走代理」，两件事混在一个函数里迟早互相破坏）。
- **违反后果**：新写的本机 HTTP 路径若直接用 `urllib.request.urlopen`，在有代理变量的机器上会**静默**多一跳并可能收到代理的 502（症状像「hub 挂了」/「worker 离场」，实际两者都好好的）；反过来，若把非回环请求也一并绕开代理，Colab userspace 那条唯一出网路径会直接断（云机取不到 job/payload）。
- **落地**：`nn-training/remote/net_http.py`（新）、`remote/{hub_client,push_dispatch,worker,offline_deliver}.py`（改四处出口）、`nn-training/tests/conftest.py`（测试侧 `no_proxy` 归一，兜住裸 `urlopen` 的既有用例）；回归 `nn-training/tests/test_loopback_http_no_proxy.py`（6 例）。
- **本轮未做（P1 余下的形状整理）**：组件卡片按「单例角色 / 按课程」分组（见 §2026-09-18-goalnn-single-hub-single-tunnel 的「本轮未做」）。
## §2026-09-18-goalnn-r2-loop-task-queue（2026-09-18，用户指令：训练循环任务队列化 —— trainingLoop 由一个进程服务所有并行课程）

- **背景**：`trainingLoop` 一直不是「请求处理器」，而是**有状态会话**：`TrainingLoop.__init__`（`nn-training/rl/loop_core.py:151`）声明约 60 个跨轮字段（进度指针 / 门禁计数 / 在飞线程与子进程 / torch 模型与优化器 / 轮内瞬态），`run()` 是 190 行顺序脚本——一步阻塞整条腿阻塞（等远程 PPO、等 rollout 子进程、等 eval 尾巴）。所以「多课程 = 多进程」不是设计选择而是形状的必然结果，与用户口径「hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程」冲突。用户进一步给定形态：**任务队列，任务自带一切**（与 hub 的 job 同构）。
- **决定（设计稿 `plan/r2-loop-task-queue.md`）**：
  - **状态五分类**（逐字段给出归宿）：**A 指针**（`next_it`/`rotate_seed`/阶梯＝`it` 的纯函数）· **B 门禁指标**（连击/累计量/止损/提示类计数）· **C 在飞集**（`job_id`/等谁回传/eval 尾巴/预采子进程——线程句柄不可序列化 ⇒ 落盘的是**意图**，重启按意图重建）· **D torch 对象**（每课内存缓存最新 checkpoint：权重 + Adam）· **E 轮内瞬态**（`_report`/`_agg`/`_volume_*` 等，**禁持久化**——持久化它 = 拿旧数字记新轮）。
  - **任务模型**：一轮拆成 13 个细粒度任务（`prepare_iter` → `rollout_dispatch/wait` → `ppo_publish/wait` → `export_weights` → `eval_dispatch/join` → `gate_eval` → `record_iteration` → `cleanup`）；执行器返回四态 **DONE / WAIT / RETRY / ABORT**。`WAIT`（等远程结果、等 eval 尾巴）**不占执行权**是单进程多课程的关键；持资源的步才是串行的（用户口径：用任务队列防资源竞争）。
  - **每条任务必须先有盘上判据**（幂等 guard：shard 齐 / 权重落位 / 账本已有 `iteration` 行）——这条同时是「重放安全」与「扫账本」的同一件事。
  - 每课一份 `train-loop` 状态文件（`loop-state.json`，v1）：**账本永远是 SSOT**，该文件只是加速器，版本不认/字段缺失即从账本重建。
  - **用户定案四问（2026-09-18）**：① 直接做到 **R2c 单进程**（R2a/R2b 为途中产物）；② 任务粒度 = **细粒度步骤 + WAIT 让位**；③ 本机重资源（`local_ppo`/`eval_local`）**跨课排队、池容量 1**（rollout 子进程池不受影响）；④ checkpoint 缓存上限 = **并行课程上限（默认 5，`rl.checkpointCacheCourses` 可配）**，RSS 实测表是 R2c 上线前置。
  - **门禁扫账本（用户裁决）**：门禁语义逐条从「内存计数」改成「扫账本」；指标**按课程缓存**，只在开课/续跑读一遍，之后由写事件处**增量**维护。**不新造账本**：`training_log.jsonl` 已含 `run_start`/`iteration`/`gate_verdict`/`iter_error`/`circuit_break`/`run_complete`，且已有五个扫描器（`rl/resume.py`、`rl/gate_check.py`）——R2a 只是把它们收敛成**一份视图**。
- **R2a 已落地（2026-09-18）**：`nn-training/rl/train_ledger.py`（`LedgerSpec` + `LedgerView` + `load_ledger` 单遍扫描 + `apply_event` 增量）；`rl/loop_core.py::_setup_common` 改由视图继承 `next_it`/`rotate_seed`/`ent_peak`/`train_sec_total`/`train_samples_total`/`kl_streak`/`ent_streak`/`stop_loss_streak`/`soft_remediate_count`；`rl/events.py` 新增 `stop_loss` 事件（止损连击的**状态转移**落账）且 `write_*` 返回事件 dict 供增量视图消费；`rl/loop_guards.py` 加 `_ledger_apply` 钩子（写账本处顺手并入视图，观测失败绝不阻断训练）。
- **本相位**刻意**的行为变化**（就是修复内容，必须知道）：门禁计数**不再随进程重启清零**——① I2「提示类门 REMEDIATE ×N 即停腿」的计数读**整条账本**（换新 traj = 新纪元，重新计数）；② F4 `kl_streak`/`ent_streak` 与 `ent_peak` 同源继承（连击是**连续**计数，只有下一轮再越线才续，继承既真又无害）；③ 止损连击靠新事件跨重启成立。**刻意不继承**：`_consec_fail`（重试连击是单腿内的进程护栏，继承会「重启即秒死」）、`_zero_shard_streak`（口径还依赖 `_node_rollout`，而 `iteration` 行今天没有 `rollout_src` ⇒ 从账本重算会对节点轮报假事故——R2b 给事件加该字段后再接）。
- **备选与否决**：另造一份「训练状态账本」JSON 作 SSOT——否（两份真相必然分叉，且旧扫描器/读盘面/控制台全部已在读 `training_log.jsonl`）；把 13 步合并成「一轮一个任务」——否（用户定案：轮粒度下 executor 串行，一门课的远程等待会挡住其它课，单进程只省了进程数、没换来并行）；重启时把 torch 对象序列化恢复——否（Adam 动量序列化成本高且不必要：落盘面已有权重，缓存是**加速器**不是真相）；用 `Math.random`-式时间戳推断在飞任务 —— 否（幂等判据必须来自账本/盘面，不能来自时间猜测）。
- **违反后果**：任何新增的跨轮门禁计数若写在内存里，就会重演本轮修掉的 bug（重启即洗白，`c6-pickup3` 6 次 / `c6-bonus` 10 次 REMEDIATE 那类判据被无限延长）；任何绕过 `_ledger_apply` 的账本写入会让视图与盘面分叉（R2b/R2c 的任务幂等判据随之失效）；把 E 类轮内瞬态写进 `loop-state.json` 会让重启后的记账与真实轮次错位。
- **落地**：`nn-training/rl/train_ledger.py`（新）、`rl/events.py`（`write_*` 返回事件 + `write_stop_loss`）、`rl/loop_core.py`（`_setup_common` 继承）、`rl/loop_guards.py`（`_ledger_apply` + 止损落账）、`rl/loop_steps.py`（`_record_iteration` 增量并入 + 类型声明）；回归 `nn-training/tests/test_train_ledger.py`（15 例：与五个旧扫描器**奇偶**、增量==单遍、独立复算连击、坏行/未知事件透明）、`tests/test_train_ledger_wiring.py`（3 例：`_setup_common` 继承 + 继承计数当轮停腿 + 空账本从零）。设计稿 `plan/r2-loop-task-queue.md`；进度 `docs/nn.progress.md §76`。
**R2b 落地（2026-09-18 续）——任务模型 + 在飞集，并把 `loop-state.json` 否决掉**：

- **`rl/loop_tasks.py`**（新，纯逻辑）：`Task`（`task_id = course:it:kind` = 幂等键；重试只动 `attempt`）·
  `TaskResult` 四态（`DONE`/`WAIT`/`RETRY`/`ABORT` + `is_terminal`；非法状态当场 `ValueError`）·
  `ROUND_TASKS` 13 步任务表 + `RESOURCE_OF`（只有 `rollout`/`volume_topup`/`ppo`/`eval_join` 占资源池，
  等待型/记账型不占——这正是单进程能服务多课程的机制）· `RoundFacts` + `already_done` + `pending_tasks`
  （幂等判据只认**盘上事实**）· `resolve_failure`（与现主循环逐条一致：冒烟作废原地重试 / 死腿立刻 ABORT /
  attempt≥5 才停）。
- **在飞集复用既有 WAL**（`rl/commit_journal.py`）：新增 `attach(phase, round, **extra)`（**不改状态机**，
  只补 `job_id`/`dispatch`/`ts`）与 `inflight()`（pending + 这些事实）。`_remote_ppo` 在 `publish_job`
  拿到 `jid` 后立刻 attach；`_commit_journal()` 在每 it 首次创建时把在飞集打进日志（`jid=` / `via push|pull`）
  ——「上一轮在等哪个 job、推给了谁」从事故考古变成一条日志。
- **★ `loop-state.json` 否决**：初稿计划每课一份状态文件（指针 + 在飞集 + 预算）。落地时发现盘上
  **已有三份权威来源**覆盖全部四类信息——账本（指针/预算/门禁计数，R2a 已接）、`commit_journal`（在飞集，R2b 已接）、
  `eval_log.jsonl` + `(stage,seed,wver)` shard 对账（eval 尾巴意图、预采子进程）。再写一份 JSON 就是**第二份真相**，
  且分叉方向恰是最贵的一种（续跑读错指针）。⇒ **不建该文件**；唯一允许留在内存的是调度器的唤醒条件
  （`WAIT.resume_at`、资源池票），它可重算。（备选否决：写它当加速器——内容为零独立信息并集，只带来分叉风险；
  把在飞集写进账本——账本是**事件流**，在飞是**状态**，混进去污染所有账本读者。）
- **落地**：`nn-training/rl/loop_tasks.py`（新）、`rl/commit_journal.py`（attach/inflight）、`rl/loop_steps.py`
  （`_remote_ppo` attach + `_commit_journal` 在飞集日志）；回归 `tests/test_loop_tasks.py`（10 例）、
  `tests/test_commit_journal.py`（+4 例，含硬死注入带 `job_id`）。
**R2c-1 落地（2026-09-18 再续）——单进程调度核心 + 只读计划视图**：

- **`rl/loop_scheduler.py`**（新，纯调度：无 torch/网络/IO）：`PoolSet`（资源票 + 记账，
  未知池名/超发释放都 `PoolError`）· `CourseQueue` · `Supervisor`（公平轮转 → 闸门 → 单线程
  执行 → 四态收敛 → 按课隔离故障）。三条不可交易性质：**同一时刻只跑一个任务**、
  **`WAIT` 不占执行权**、**故障域按课**。
- **★ 票可跨 `WAIT` 保留**（`TaskResult.hold` / `waiting(hold=True)`）：写池闸门测试时发现的
  设计缺口——若 `WAIT` 一律还票，单线程调度器里容量 1 的池**永远不会挡住任何人**（任务体
  跑完即还票），池就失去意义；真实形态是「后台仍在干活」（本机 eval 的局还在子进程里跑）
  ⇒ 票必须能跨步持有，否则另一门课的本机重资源会插进来把机器压爆。
- **「能跑的都被池挡住」= 没事可做**（`step()` 返回 None，`blocked_courses` 保留事实）——
  否则调度器会在两门互相挡住的课之间空转。
- **`rl/loop_plan.py`**（新，唯一碰盘之处）：账本指针 → `RoundFacts` → `pending_tasks`；
  shard 结算数；`commit_journal` 在飞集；课程发现（`<traj-root>/*/training_log.jsonl`）。
  判据永远朝「不跳」保守：算不出的留 `False`/`0`。
- **`nn-training/run_rl_cluster.py`**（新入口）：**一个进程**读出所有并行课程的「下一步 /
  待办 / 在等谁（含 job_id）/ 被什么挡住 / 关键事实」。当前只提供只读计划视图（不训练、
  不发布、不等待）——它零训练行为变化且立刻可用，同时把调度核心放在真数据上跑通。
- **R2c 拆相**：R2c-1（本段，机制 + 只读面）已完成；**R2c-2** = 抽 `run_one_round` 并把任务体接到
  真 `TrainingLoop` + 三处长等待改 `WAIT` 让位 + 进控制台 + `checkpointCacheMb` 真机 RSS 实测。
- **落地**：`nn-training/rl/{loop_scheduler,loop_plan}.py`、`nn-training/run_rl_cluster.py`（均新）；
  回归 `tests/test_loop_scheduler.py`（18 例）；实测：`run_rl_cluster.py --traj-root tmp` 在真课程
  目录上读出 5 门课的计划（含「采集完成但账本未结算」那一态）。
**R2c-2 落地（2026-09-18 三续）——轮体抽出 + 任务体↔引擎的桥 + 假件集成测试**：

- **`TrainingLoop.run_one_round(it) -> RoundOutcome`**：`run()` 的 190 行轮体搬进新方法，`run()` 退为
  驱动器（预采 join / 预算到点检查 / 按 outcome 施加 / 收官 drain / 停车）。控制流**只搬不改**：
  `break`→`ROUND_STOP`、包导出 `return`→`ROUND_BUNDLE_EXIT`、冒烟作废→`ROUND_SMOKE_STOP`、
  三种 `it -= 1`→`ROUND_RETRY`、轮末→`ROUND_NEXT`。**`RoundOutcome.it` 必须带回**：半离线整段
  `_remote_run_segment` 一次吃掉 it..end_it，丢掉返回值就会重跑已跑完的段。
- **`rl/loop_runner.py`**（新，任务体↔引擎的**唯一**桥）：`planner` 读账本给指针（SSOT）、
  `run_round` 把 `RoundOutcome` 映成四态；**`WAIT` 只认引擎显式提供的 `remote_job_ready(it)` 钩子**
  ——钩子不存在（今天的引擎）⇒ 跑完即 DONE = 行为与改造前逐字节一致；钩子存在（R2c-3 的轮询化，
  或测试里的假件）⇒ 未就绪就 `WAIT` 让位。**不猜、不睡、不自己轮询**。
- **粒度诚实记账**：今天一个任务 = **一轮**；轮内 13 步需要的轮内局部量
  （`pairs`/`dist_cfg`/`t_rollout`/`seg`）还锁在 `run_one_round` 里，提成 `RoundContext` 后即可细化
  （R2c-3）。粒度只决定让位点密度，不改调度器/桥的契约。
- **集成测试（用户口径：不跑真 rollout/PPO/eval）**：`nn-training/e2e/test_loop_supervisor_integration.py`
  （4 例）——真 `TrainingLoop` 控制流 + 真账本写入 + 真 `Supervisor`/`LoopRunner`，只把
  `_rollout_phase`/`_serial_ppo`/`_join_eval`/预采/巡检/轮转/`rl-config` 读盘换成假件；钉住
  ①一个进程服务多课互不串账 ②`WAIT` 让位（另一门课先跑完且顺序可断言）③进程重开按账本续跑且
  不重写行 ④引擎异常 = 原地重试。
- **落地**：`nn-training/rl/loop_core.py`（抽方法 + 常量/`RoundOutcome`）、`rl/loop_runner.py`（新）、
  `e2e/test_loop_supervisor_integration.py`（新）；拼接用一次性脚本（逐 hunk `assert` + `ast.parse`，
  跑完即删）。
- **仍未做**：R2c-3（轮内细粒度 `RoundContext` + 三处长等待真轮询化 + 控制台视图 + 真机 RSS 表）·
  R2d · R2e。
**R2c-3 余下（2026-09-18 五续）——远端 PPO 三相拆分：让位点从「每轮」下沉到「这一步」**：

- **问题**：拆相前 `_remote_ppo` 是一个 400 行阻塞函数（打包 → 发布 → 阻塞轮询 → 三重校验落位）。
  它前面**不能**挂让位闸门：闸门跑在步骤之前 = 还没发布就被挡住 ⇒ 永远等不到回传。于是单进程
  多课程下，「等云机」这一段仍然会堵住整条调度链（25 分钟量级）。
- **拆法：同一任务内的三个相位，不是三个任务**（`rl/loop_steps.py`）：
  `_remote_ppo_publish`（打包 + 发布 + 直推提交 → 会话）/ `_remote_ppo_probe`（**非阻塞**问一句）/
  `_remote_ppo_fetch`（阻塞等：组合路径 / 节点轮 / 整段）/ `_remote_ppo_land`（校验落位 + 记账 +
  结算字段）。`_remote_ppo` 保留为三者的**组合入口**，四个既有调用点（本机轮 / 节点轮 / 半离线整段 /
  全离线导出）行为不变。
  **为什么不拆成 `ppo_publish` + `ppo_wait` 两个任务**：三相共享一份会话（jid / 超时预算 / 打包墙钟 /
  运输方式），拆任务就得把它序列化到盘上才能跨任务传 —— 那正是 R2b 否决过的「第二份真相」
  （§2026-09-18-goalnn-r2-loop-task-queue 的 `loop-state.json` 段）。任务粒度只决定让位点密度。
- **会话是纯数据、住在轮内上下文里**（`rl/loop_round.RemotePpoJob`，`RoundContext.remote`）：
  不进引擎实例属性（§2.2 无隐藏状态；单进程多课程会互相覆盖），也不落盘（同上）。刻意**不持有**
  payload 字节（几十 MB 级）——直推换节点重发时从 job 目录重读（`find_payload`）。
- **非阻塞探针是新的唯一分类实现**（`remote/hub_client.probe_job_result`）：一次请求 → 三态
  `ready` / `pending`（202/404：还没回，让位等下一轮）/ `transient`（网络错/5xx：没答，也算让位），
  **410 照抛 `JobFailedError`**——「还没好」与「永远好不了」必须分开，把后者当前者正是 x3-step 事故
  把「bun 缺失」写成 25 分钟超时的原因。hub 与节点两条链路只差端点路径（`/jobs` vs `/job`），
  靠 `path` 注入复用同一实现；`wait_job` / `wait_result` 两个阻塞版改为**建立在探针之上**，各自只保留
  自己的退避策略（历史差异：hub 是指数退避，直推是固定 poll）——状态码分类从此不可能在两条链路漂开。
- **直推链路：发布即提交**（`_push_submit_first` / `_push_submit_node` / `_push_fetch`）。探针要问
  「那份 job 现在怎么样了」，节点上还没有这份 job 时它只会一直答「还没回」⇒ 提交必须落在发布相位；
  提交本身是**有界**上传（几十 MB），不是那 25 分钟的等待。换节点必须**重新提交**（新节点没见过这份
  job），failover 判决与组合入口 `_push_job_round` 共用 `_push_over_nodes`（一份实现，三个调用方）。
- **evalboard idle 窗提前到发布相位**：它的用途是「等待期集群空闲，赶紧开窗领批」。细粒度路径会让位，
  若仍留在等待相位，窗口要等结果回来才开 ⇒ 永远错过它要服务的那段空闲。
- **`ctx.resumable`：让位点由「谁在驱动」决定**（`rl/loop_round.RoundContext`）。细粒度驱动器
  （`LoopRunner`）造上下文时置 True ⇒ 未就绪就 `wait_for`；组合路径 `run_one_round` 保持 False ⇒
  就地阻塞取结果（拆分前语义）。于是「组合路径没有让位点」从一个运行期异常（`RoundYieldError`）
  变成了一个**事实字段**。
- **`WAIT_HOOKS` 里不再有 `ppo`（且不得加回去）**：表里的闸门跑在步骤之前，对 ppo 来说那是「还没发布」。
  让位已由 `step_ppo` 自己产生（`tests/test_loop_runner.py` 把「不在表里」写成断言）。预采那一处不变。
- **失败判决只留一份**：`_handle_remote_failure`（从 `_remote_ppo_or_degrade` 的 except 分支抽出，
  逐字搬移）；发布 / 取结果 / 落位任何一段失败都进它——三段各自演化出不同的连败计数/停腿口径是本仓
  最贵的一类分叉。`_remote_ppo_or_degrade` 保留原签名与原 docstring（有 9 个既有用例钉着它）。
- **落地**：`nn-training/{remote/hub_client.py,remote/push_client.py,rl/loop_steps.py,rl/loop_round.py,
  rl/loop_round_steps.py,rl/loop_runner.py}`；拆分用一次性脚本（逐 hunk `assert` + `ast.parse`，
  被搬的行逐字节不变，跑完即删）。回归：`tests/test_remote_probe.py`（14）·
  `tests/test_remote_ppo_phases.py`（8）· `e2e/test_loop_supervisor_integration.py`（ppo 让位用例改为
  驱动真三相）· `e2e/test_push_mode_integration.py`（+3：发布相位提交 / 换节点重提交 / 探针目标）。
- **仍未做**：`Supervisor` 进控制台（单例卡片 + 每课队列视图）· `checkpointCacheMb` 真机 RSS 实测表。
**R2d 写的一半（2026-09-19 八续）——单进程 supervisor 真的能跑了：进程级/课程级/步骤级三层各做一次**：

用户 2026-09-18 指令（「训练循环任务队列化 —— trainingLoop 由一个进程服务所有并行课程」）的落地前半：
R2c 造好了调度器与任务体，但**没有驱动者**（至今仍是「一门课一个 `run_rl.py` 进程」）。本轮交付
`rl/loop_serve.py`（驱动者）、`rl/engine_pool.py`（N 课共享的 torch 栈缓存）、`rl/log.py` 的**行路由**
（单进程下的课程归属），入口 = `run_rl_cluster.py --serve --courses a,b`。

**分层纪律（每层各做一次；越界做两次会伤到既有护栏）**：
- **进程级一次**（`prepare_process`）：UTF-8 stdio / faulthandler / `chdir(repo)` / 启动前 `git push`
  （`.git_push.lock` 串行化）/ 节点升级分支锁到训练机分支 / bun 存在性。每课各做一次 = 每课都推一遍 git。
- **课程级一次**（`open_course`）：参数解析（与 `run_rl.py --course` **逐字段一致**；对拍见
  `tests/test_serve_wiring.py::test_course_args_match_run_rl_echo_config`——oracle 是在子进程里跑
  `run_rl.main()` 自己、把 `echo_config` 换成 dump，本机缺当前 era 权重则**跳过并写清理由**）+ `validate_args`
  + **按课程的单实例锁**（同课双开响亮拒启；2026-09-06 双 trainer 并写同一 traj 的护栏不删，只是文件名
  按课程命名）+ 清本课 hub 停机态。
- **步骤级**（`build_executor`）：`EnginePool.get(课)` → `ensure_ready` → `LoopRunner.executor`，整段包在
  `prefix_scope(课)` 里。`ensure_ready` 的判据是**对象身份**：引擎对象换了（首用 / 被驱逐后重建）⇒ 走
  `_setup()`（等同一次进程重启）；对象没换 ⇒ `_ensure_local_ppo_stack()` 幂等补齐。

**四条定案（都写进代码注释 + 用例）**：
1. **serve 模式不收官停车**：`_park_after_completion` 的死循环语义前提是「这个进程就是这门课」，多课程下会
   冻住全部 ⇒ 新拆 `TrainingLoop.finish_course(it)`（收敛预采 / 云机 PAUSE / `run_complete` 落账，**三件事
   与单课程路径共用一份实现**），serve 只调它 + 把该课队列置 `done`；全部收官才退出。
2. **不换 `sys.stdout`，改行级路由**：单进程里套两个 `Tee` 会把每行复制进两份课日志；课程归属 = 行前缀
   `[课]` + 镜像写进该课 `out_log`（`rl.log.open_course_sink` / `prefix_scope`）。无前缀时与改造前逐字节相同。
3. **引擎池的驱逐 = 响亮的一次「重启」**：容量默认 = 并行课程上限 5（§6.1 实测每课 MB 级），字节上限
   256MB 只是第二道保险；驱逐时**必须**记一行「谁被驱逐 + 权重可从 `args.out` 复原但 Adam 动量重置 + 若
   常发生请调大上限」，且**绝不驱逐在用引擎**（任务中途抽走栈会撞 `None.load_episodes`）。退出时释放全部
   栈（只关自己建的池——注入的池归调用方）。
4. **初次入队的粒度必须匹配执行体**：细粒度给 13 步任务表，轮粒度给**单个** `round` 任务；混了就是把 13 个
   步骤 kind 塞进只认 `round` 的执行体（响亮 ABORT，不是静默跳步）。初次入队用 `course_facts` +
   `pending_tasks(round_tasks(...))`（判据原语与只读计划视图同源），**不**调 `plan_course`——那会连 CLI 表格
   用的展示面字段（累计量/verdict）一起算。

**回归**：`tests/test_engine_pool.py`(9：惰性/命中/LRU/两道上限/不驱逐在用/响亮超预算/释放钩子/快照) ·
`tests/test_log_router.py`(6：无前缀逐字节不变/作用域还原含异常/镜像到本课/坏 sink 不崩/重复注册换句柄) ·
`tests/test_serve_wiring.py`(8：轮转交替 a,b,a,b / 13 步全表 / 让位让别的课跑完 / 容量 1 时驱逐重建再 setup /
账本已结算不建引擎 / 坏课隔离 / 参数对拍)。门禁：nn python gate **1476 passed / 4 skipped**；根 `bun run check` 绿。

**未做（R2d 剩下的操作面）**：控制台的入队/暂停 + 单例 `trainingLoop` 卡片（读面卡 R2c-3 已交付）；**R2e**：
多课 × 假 worker/假 PPO 的 e2e + 真机双课并行跑通（serve 的真机行为需要人验）。

**R2d 操作面（2026-09-19 九续）——进程不绑课程 + 暂停/恢复的控制文件通道（含生效回执）**：

用户定案两点：① 控制指令走**控制文件**（不在训练进程里再挂 HTTP 服务）；② **trainingLoop 进程
独立于课程**——没有课在训也能起，队列空着等。本轮把这两条落地，并把「离线开关」（R3-2）之外的
另一半操作面补齐。

**① 发现模式：进程不绑课程（`serve(courses=None)`）**
- `--serve` 不给 `--courses` ⇒ 启动扫 `--traj-root/*/training_log.jsonl`，之后**每个空转拍再扫一次**
  （新课程账本出现即自动开课入队）；**一门课都没有也照常运行**，`stop_reason` 不再有 `no_courses`
  这一条（空队列是合法稳态，不是结束条件）。显式课程表则退化为「只看这几门」，全收官即退（e2e/单课调试）。
- **被跳过过的课不再重试**（课程配置缺失 = 这一轮修不好；否则空转拍每秒刷日志）。
- `--mode` 必须**显式声明**在 cluster 解析器上：此前靠扫 raw argv 取，但 argparse 会先把
  `--serve --mode goal` 判成 unrecognized arguments 而拒启（声明了才能真透传）。

**② 控制通道 = 一份意图文件（`tmp/loop-control.json`）**
- 控制台写 `{"version":1,"paused":["c5"]}`（`dashboard/src/server/actions/loop-control.ts`，**原子写**
  tmp+rename：训练侧每拍都在读，读到半个 JSON = 读到坏文件 = 静默失效），训练侧每拍读一次并施加到
  调度器（`rl/loop_control.py`）。hub 挂了也能用，**文件本身就是状态**（对比 hub 的 course-mode 是
  volatile，那边要靠回灌）。
- **保守方向是刻意的**：读不到 / 解析失败 / 形状不对 ⇒ 当作「没有任何暂停意图」（继续训练）。控制面
  坏掉不该停掉整条腿——这与 `already_done` 的「算不出的判据不得当成完成」同一条纪律。两侧各自单测。
- **暂停只影响调度**（用户口径「暂停 = 保留队列，恢复后接着跑」）：队列与账本一个字不动。

**③ 回执面（意图 ≠ 事实）——为什么必须有第三个文件**
只有意图文件时，控制台点完暂停只能盲猜生效没生效（进程可能没在跑，也可能还没轮到读文件）。所以训练
进程把**自己实际施加了什么**写回 `tmp/loop-control.applied.json`（`at` + `pid` + `paused`，仅在
施加结果**变化时**写，不心跳），控制台用 `pid` 存活核对分辨「已暂停 / 待生效 / 恢复中 / 运行中」四态。
**进程已死 ⇒ 残留文件不作数**（否则界面永远显示「已暂停」）——这是回执能被当事实的唯一前提。

**④ UI：按钮改意图、徽标报事实（`LoopQueue` 卡片每行）**
- 按钮方向由**意图**定（未生效时说「取消暂停」、已生效说「恢复」）；徽标只在「意图 ≠ 事实」时出现，
  且**待生效用虚线、已暂停用实线**（同色即等于骗人）。「待生效」的悬停分两种解释：进程没跑 vs 还没
  轮到读——不能一句「处理中」糊过去。
- 开关是行按钮的**兄弟节点**（行本身是 `<button>`，嵌套 button 非法）；`onAction` 缺省 = 一个开关都不
  渲染（LAN 只读下不假装能控，同总览卡的离线开关）。
- 动作后**显式作废调度器视图缓存**（TTL 10s 比 hub 观测面的 5s 宽）——否则点下去要等一个 TTL 才上屏。

**⑤ 暂停不算收官（教训）**：`_all_settled` 原先把 PAUSED 当收官 ⇒ 「暂停一门课」会顺手把整个进程退掉，
恢复意图永远没人执行。现改为只认 `done`/`aborted`（暂停的课会让显式课程模式的进程一直等，用
`--max-seconds` 兜底；发现模式本来就不退）。

**回归**：`tests/test_serve_wiring.py`(+8：零课程照跑 / 中途出现的课自动入队 / 开不起来的课只试一次 /
暂停只停被点名的课 / 恢复从原处接着跑 / 坏控制文件保守继续 / CLI 发现模式与 `--control-file`/`--mode` 转发) ·
`tests/test_loop_control.py`(22：解析边界 / 非法名不废整份意图 / 保守方向 / 幂等 / 坏文件不被覆盖 /
回执形状与原子性 / **未变化不写盘** / pid 存活语义) ·
`dashboard/tests/loop-control.test.ts`(22) · `dashboard/tests/web-app-loopqueue.test.ts`(+9：四态 / 文案 /
徽标 / 只读不渲染开关 / route+cache 接线) · `dashboard/tests/server-api-loop-queue.test.ts`(+1)。
门禁：nn python gate **1511 passed / 4 skipped**；dashboard **621 passed**；三份 bundle + 根 `bun run check` +
`bun run build` 绿。

**仍未做**：单例 `trainingLoop` 卡片的分组（R3-3）· **R2e**（多课 × 假 worker/假 PPO 的 e2e + 真机双课
并行跑通——serve 的真机行为需要人验，本轮改动同样只在假件下验证过）。

**R2c-3 收口（2026-09-19 七续）——真机 RSS 实测：checkpoint 缓存上限的真实约束是「数量」不是「字节」**：

- **为什么要实**：单进程 supervisor 要为 N 门课各持一份 torch 栈（model + Adam + 冻结 ref），
  `checkpointCacheCourses` / `checkpointCacheMb` 的默认值此前**只能拍脑袋**（本机 0 卡、remote 为主）。
  plan §6 把这张表列为 R2c 上线前置条件。
- **实测（`nn-training/scripts/measure_checkpoint_rss.py`；本机 CPU-only torch 2.7.1+cpu）**：
  每课增量 per-tick **≈1.3MB**（70,216 参：model 0.7 + Adam 0.6，无 ref）/ intent **≈1.8MB** /
  goal **≈1.8MB**（各含一份冻结 ref）；**torch 基线 294.5MB 与课程数无关**；
  N=5 混合档累计 **294.8 → 301.8MB（仅 +7MB）**。
- **定案**：**`checkpointCacheMb` 是第二道保险，真实约束是数量** `checkpointCacheCourses`
  （= 并行课程上限 5）。推荐 `checkpointCacheMb = 256MB`（≈130 课，正常永不触发）；真触发即说明
  「某课的栈长得离谱」——那时该被看见（响亮拒绝缓存），不得静默驱逐。
- **口径诚实性（这张表最容易被读错的两处，已写进脚本 docstring 与用例）**：① **先暖一次再测**
  ——torch 惰性初始化（首次 kernel 选择 / 分配器建池 / Adam 首步）会让**第一份**栈看起来贵两个
  量级（实测 78MB vs 真值 0.75MB）；② **栈必须活着**——被 gc 回收后第二课的增量会变成 0
  （分配器复用），累计曲线就是假的。实测工具本身有回归：
  `nn-training/tests/test_measure_checkpoint_rss.py`（7 例：推荐值取整/余量、候选表恒有默认架构兼底、
  表格必须自带「测的哪份权重 / 跳过了谁 / 理论 vs 实测」、真造一份栈的量级保护带）。
- **不测且写明的**：单轮 episodes/chunk 缓冲是**另一处峰值**（由 `mb` 与本轮样本量决定）——
  它是每轮瞬态、且本机 PPO 池容量 1 ⇒ 同一时刻只有一份；已由 forensics 埋点，不属缓存上限管的常驻量。
- **仍未做（缓存实现本身）**：随「单进程 supervisor 真上线」的相位落地（R2d/R2e）。今天没有持有者，
  先写就是无消费者的投机代码；本表 + 上述两个默认值就是它上线时需要的全部输入。
**R2c-3 余下（2026-09-19 六续）——控制台接线：单例调度器卡片 + 每课队列视图（「在等什么」）**：

- **数据源 = python 只读入口，★ 控制台不得在 TS 重算判据**（防再犯条款）：卡片走
  `nn-training/run_rl_cluster.py --json`（训练侧只读：不训练/不发布/不等待），与 CLI 表逐字段同源。
  指针 → `RoundFacts` → `pending_tasks` / `waiting_state` 这套判据已在 python 侧被用例钉住；
  在 TS 里照账本重写一遍 = **第二份真相**（同 `loop-state.json` 段），两边会以不同速度演化。
  谁想「顺手在 TS 里读账本省掉一个子进程」，先重读本段：省下的是亚秒级冷算，换来的是两套语义。
- **成本与缓存**：`dashboard/src/server/api/loop-queue.ts`——懒算 + **TTL 10s** + 单飞 + 服务启动
  暖一次（避免 SSR 首屏等子进程）。TTL 比 hub 观测面（5s）宽，因为事实变化的粒度是「一轮」
  （分钟级）而冷算要起一个 python。读失败（解释器缺失 / 超时 / 输出不可解析 / 形状不符）**不抛**：
  视图带 `error` 上屏，UI 显因 + 空态——观测面坏掉不该把整页 `/api/state` 带崩（与 hub 总览、
  隧道 A/B 同口径）。
- **「在等什么」的判据留在 python**（`rl/loop_plan.py::waiting_state`，CLI 与控制台同一个函数）：
  `inflight`（已发布未回传，带 phase@round + jid + dispatch）/ `collect` / `idle` / `ready`，
  优先级 inflight > collect > idle > ready（**进程外的等待排第一**：结果在别的进程/机器上，
  运维唯一能干预的那一类）。★ **`games_planned` 诚实性**：盘上今天没有任何地方记「本轮计划多少局」
  ⇒ CLI 传 0 = 未知，`collect` 只报已落局数、**不报分数**（绝不出现 `78/0`）；这与 `already_done`
  同一条规矩——算不出来的事实不得当成完成，也不得编出分母。
- **两个事实源逐行合并**（`web/view/loop-queue.ts::withTraining`）：python 说「这一轮卡在哪」，
  registry（`trainingLoop` 进程存活）说「这门课此刻有没有人在跑」。★ 缺了后者，一门**停了的课**
  会被读成「等外部」——故未在训的行淡一档 + 悬停说明「下面是盘上事实推出的队列状态」。
- **与「并行课程总览」的分工**（防重复建设）：总览回答**hub 侧**「谁在派活 / 谁离线」（job 队列），
  本卡回答**训练侧**「这一轮卡在哪一步」（任务队列）——同一条流水线的两段，不合并。
- **落地**：`dashboard/src/{server/api/loop-queue.ts, web/view/loop-queue.ts, web/app/panels/LoopQueue.tsx}`
  （三个新）+ `server/run-python.ts`（新增同步脚本入口 `runRunPythonSyncScript`，与模块入口共用一份
  实现）+ `api/{state-view,index}.ts` / `web/view/console-types.ts` / `web/app/app.tsx` / `web/theme.css` /
  `server/server.ts`（启动暖一次）；训练侧 `nn-training/rl/loop_plan.py`（`waiting_state`）+
  `run_rl_cluster.py`（抽 `build_rows`、JSON 加 `waiting`、表里也打印）。
- **回归**：`dashboard/tests/server-api-loop-queue.test.ts`（13：解析容错 / 结果翻译四条失败分支 /
  TTL 复用 / 单飞 / 在训合并 / state 注入）· `dashboard/tests/web-app-loopqueue.test.ts`（12：SSR 每课
  一行 / 四态着色 / 未在训淡档 + 悬停 / 排队与页脚 / 读失败显因 / 接线断言）·
  `nn-training/tests/test_loop_plan_waiting.py`（14：优先级 / 未知配额不编分母 / 真读盘组装）。
  子进程是**可注入接缝**（同 `deliver_zip` 导入惯例）⇒ dashboard 用例不跑 python，python 用例不碰
  控制台，两边各测自己那一半。
- **仍未做**：① `checkpointCacheMb` 真机 RSS 实测表（R2c 上线前置条件）；② R2d 操作面（入队 / 暂停 /
  单例 `trainingLoop` 卡片）；③ R2e e2e（单进程多课 × 假 worker/假 PPO）。
**R2c-3 落地（2026-09-18 四续）——轮内切成 13 步 + 让位闸门（`WAIT_HOOKS` 表）**：

- **`RoundContext` 与 13 步表**：`rl/loop_round.py`（纯数据 + 表，无 torch/网络/IO）定义
  `RoundContext`（轮内跨步可见的量：`pairs`/`dist_cfg`/`t_rollout`/`seg`/会被半离线整段推进的
  `it`）与 `STEP_ORDER`（= `ROUND_TASKS`，**单一来源**）+ `STEP_METHOD`（kind → 引擎方法名）。
  步骤实现在 `rl/loop_round_steps.py`（`RoundSteps` mixin，逐行从轮体搬来）；组合路径
  `run_one_round` 与细粒度驱动器**都从同一张表取步骤** ⇒ 加一步必须同时进表，两条驱动不可能漂移
  （`tests/test_loop_round.py` 断言表与实现一一对应）。轮内状态**不长在引擎实例上**（§2.2 无隐藏
  状态：单进程多课程会互相覆盖）。
- **顺序错纠正（真发现）**：`precollect_join` 必须排在 `prepare_iter` **之前**——它产出的是本轮
  `it{it}` 的 shard，而 `prepare_iter` 靠 `completed_pairs` 看盘决定「保留续跑 / 清场重建」；
  反了就把上一轮的预采整个作废。
- **让位闸门 = 一张表**（`rl/loop_runner.WAIT_HOOKS`：kind → 引擎钩子名）。**钩子不存在 ⇒ 不让位**
  （行为与改造前逐字节一致）⇒ 表可以只先装能吃的两处。
- **★ `eval_join` 刻意不进表**（防再犯）：本机 eval 局的墙钟是「藏在下一轮 rollout 里」的
  （`_eval_tail` 交棒 → `_dispatch_delayed_eval` 入口收拢，2026-09-17 用户口径）。给它加让位 =
  把那条尾巴重新串回轮边界，正好抵消当初压掉的软等窗口。用例把这个缺席写死（`test_loop_runner.py`）。
- **★ `ppo` 闸门必须在「发布之后」**：今天的 `_remote_ppo` 是一个阻塞函数（打包→发布→阻塞轮询
  →校验落位）。在它前面加闸门 ⇒ 还没发布就被挡住 ⇒ 永远等不到回传。要装它必须先做「发布 / 等结果 /
  落位」三相拆分；**宁可不装也不装错**（本轮不装，表里留位 + 理由写在 `loop_runner` docstring）。
- **已装的一处：预采**（`precollect_join` → `TrainingLoop.precollect_ready`）。`join_precollect_child`
  旧形态每 2s 轮询、上限**1 小时**——单进程多课程下这就是「一个慢子进程拖垮所有课」的入口。
  判据抽成 `rl/rollout_phase.precollect_ready`（非阻塞：句柄为空 / 子进程已退出 / 就绪 shard ≥ 半波），
  步骤内部循环**改成调同一个函数** ⇒ 「调度器认为可以往下走」与「步骤进去真的不阻塞」不可能分叉。
  `join_precollect_child` 的外部语义（含 1h 超时 terminate）逐条不变。
- **读面补全「在等什么」**：`WAIT` 时把原因落到队列（`q.reason`），并在推进/收官时清空——
  状态 + 原因 + 在飞 `job_id` 三者合起来才够定位；过期原因比没有原因更坏。
- **落地**：`nn-training/rl/{loop_round,loop_round_steps,loop_runner,loop_scheduler,loop_core,loop_tasks}.py`
  （前两个新）、`nn-training/rl/rollout_phase.py`（抽判据）；回归 `tests/test_loop_round.py`（13 例）、
  `tests/test_loop_runner.py`（5 例）、`tests/test_precollect_ready.py`（7 例）、
  `e2e/test_loop_supervisor_integration.py`（+3 例：13 步逐一走完 + 步级轮转 / ppo 处让位 /
  预采处让位，账本形状与轮粒度逐行一致）。
- **仍未做**：① `ppo` 的三相拆分（让位点从「每轮」变成「每步」的最后一块）；② `Supervisor` 进控制台
  （单例卡片 + 每课队列视图）；③ `checkpointCacheMb` 真机 RSS 实测表（R2c 上线前置条件）。

## §2026-09-19-goalnn-serve-bc-course（2026-09-19，R3-4：单进程 supervisor 也能带 BC 课）

- **背景**：多课程并行之后训练侧收敛为**一个进程**（`run_rl_cluster.py --serve` → `rl/loop_serve.py`，§2026-09-18）。但 BC 课当时仍只能靠 `run_bc.py` 单开一个进程——理由不是需求，而是**形状**：`run_bc.py` 的 `main()` 是一整段 procedural 编排（解析 → 采集 → 发布 → 阻塞等待 → 落位归档），没有 supervisor 要的引擎子集（`_setup` / `run_one_round` / `finish_course` / `release_torch` / `ledger_next_it`）。用户口径「一个 trainer 进程服务所有课程」⇒ BC 必须能被同一个进程驱动。
- **决定**：把 BC 的编排体**逐字节**搬进 `rl/bc_loop.py`（`BcLoop` 引擎 + `BcRuntime` 解析 + 纯函数），`run_bc.py` 退为**入口薄壳**（进程级一次性副作用 + 阻塞式驱动）；`serve` 按课程种类分派引擎与粒度。
- **关键的四个取舍（都是「另一条路更好写但不该走」）**：
  - **不给 BC 造第二份一轮实现**：引擎里一轮的三段（开轮 → 等回传 → 落位归档）与单课程路径**共用同一份代码**。BC 的续训按 `jid` 存（hub `/jobs/{jid}/resume`、worker 本地 `bc-resume/<jid>`）⇒ 任何一次重发布 = 新 jid = **从头训**；两份实现里只要有一份漏了「先认领盘上 job」，代价就是一轮 GPU 时间。
  - **BC 课恒为「一轮 = 一个任务」**，不进 13 步表：13 步是 RL 的一轮（rollout/ppo/eval/门禁/记账），BC 的一轮是「采语料 → 发布 → 等回传 → 落位归档」。硬套 = 给 BC 发它不认识的待办（执行体响亮 ABORT，不是静默跳步）。粒度由 `loop_plan.round_tasks_for(course, it)` 单点决定（按课程种类选表），`LoopRunner(step_mode=False)` 由 `build_factory` 按同一判据设置。
  - **指针的语义归引擎**（`LoopRunner._ledger_next_it` 的 `ledger_next_it` 钩子）：BC 的「跑到第几轮」= `bc_round_completed`（`rl/bc_ledger.py`），RL 的 = `iteration`（`LedgerSpec`）。在桥里写死一种就是给另一类课程读错指针（症状：BC 课永远停在 it1）。读面（`loop_plan.course_facts(course=...)`）走**同一份**判据，`course_kind` = `curricula/<课>.bc.jsonc` 是否存在（与控制台 `isBcCourse` 同源，不靠账本事件推断——刚建的 BC 课账本是空的）。
  - **让位点只落在「等远端」这一段**：`BcLoop.run_one_round` 每次最多做一件事，等 GPU 回传时返回 `ROUND_WAIT`（新增的第三种轮终态：**本轮未完**，既不是失败也不是完成）⇒ 调度器把执行权交给别的课，过一会儿回来问同一轮。本机训练（`--local`）与 push 直推照旧阻塞（墙钟花在本机/邻居节点上，没有可让的余地）——与 RL 的 `eval_join` **刻意不进让位表**是同一条纪律。
- **备选与否决**：让 serve 把 BC 课委托给 `bcRound(course, it)` 子任务类型（另一条执行路径）——否（第二条「一轮」实现，正是上面第一条要禁的）；把 BC 课也拆成细粒度步骤（采集/发布/等/落位各自一个任务）——本相位否（收益只是更细的让位点，而 BC 的墙钟几乎全在「等 GPU 回传」这一段，已让位；留作需要时再下沉）；让 `run_bc.py` 变成只 import 新模块的 re-export 壳（保持旧测试导入路径）——否（同一对象两个名字会让「谁是家」含混，改为把测试指向新家）；BC 课仍用一个专属进程（本轮不动）——否（与用户口径冲突）。
- **违反后果**：任何在 `rl/bc_loop.py` 之外再写一遍「一轮」（含控制台/工具脚本自己发布 job）都会重新引入「重发布 ⇒ bc-resume 失效 ⇒ 从头训」这条最贵的错误；任何把 BC 课按 RL 读账本的地方都会得到 `next_it=1`（看起来「这课没在训」）；给 BC 课发 13 步任务表会让该课在第一次执行时就 ABORT。
- **落地**：`nn-training/rl/bc_loop.py`（新，1395 行；14 个函数体从 `run_bc.py` **AST 逐字节搬迁**、`_append_ledger`/`_ledger_bc_epoch`/`_run_epoch_eval`/`_finish_all_rounds`/`_archive_round` 去下划线）· `rl/bc_ledger.py`（新，BC 指针/完成集/收口 job 的单一读面）· `rl/loop_round.py`（新终态 `ROUND_WAIT` + `RoundOutcome.detail`）· `rl/loop_runner.py`（`ROUND_WAIT` → `waiting(..., jid=..)`；`ledger_next_it` 钩子）· `rl/loop_core.py`（单课程驱动器对 `ROUND_WAIT` 的阻塞语义：退避重问同一轮）· `rl/loop_plan.py`（`course_kind` / `round_tasks_for` / `course_facts(course=)`）· `rl/bc_config.py`（`is_bc_course`）· `rl/loop_serve.py`（`_open_bc_course` + 工厂分派 + 入队粒度）· `run_bc.py`（薄壳）· `run_rl_cluster.py`（`build_rows` 带 `kind` + 人读表加种类列 + BC 行的 facts 行不再摆一排 RL 的零）· 控制台：`web/view/loop-queue.ts`（`LoopCourseKind` + `kindBadge`/`stepTitle`/`pendingTitle`）· `web/app/panels/LoopQueue.tsx` · `web/app/app.tsx`（解除 `isBc` 门控）· `web/theme.css`（`.tc-loopq__kind--bc`）。回归：`tests/test_bc_ledger.py`(9) · `tests/test_bc_loop.py`(16) · `tests/test_serve_bc.py`(5，serve × BC 集成：让位/驱逐认领/粒度/锁/故障隔离) · `tests/test_loop_plan_bc_rows.py`(9，读面：种类/指针/在飞/两行共存) · `dashboard/tests/web-app-loopqueue.test.ts`（+3：BC 行与 RL 行并列 / 悬停各说各的 / 卡片不再被 `isBc` 门控）· `dashboard/tests/server-api-loop-queue.test.ts`（+1：`kind` 的保守默认）· 既有 BC 用例改为指向新家（`tests/test_bc_course.py`、`tests/test_remote_transport.py`、`e2e/test_bc_epoch_e2e.py`）。进度 `docs/nn.progress.md §86/§87`；plan `plan/r2-loop-task-queue.md §8 R3-4`。
- **控制台半（2026-09-19 同日接上）**：调度器卡片是**跨课程**卡（一次列出所有账本可发现的课），BC 行与 RL 行**并列**——读面（`run_rl_cluster.py --json` 的 `kind`）早在 R3-4 就通了，缺的只是 UI。三处关键决定：
  - **行上带课程种类**（`build_rows` 的 `kind` / 视图层 `LoopCourseKind`）：BC 的指针（`bc_round_completed`）、粒度（单个轮任务）、在飞来源（账本 `job_pending`）与 RL 全不同，不带种类 UI 只能猜（猜错就把 BC 读成一排看着像真的零）。种类判据 = `curricula/<课>.bc.jsonc` 是否存在（`loop_plan.course_kind`，与控制台 `isBcCourse` 同源）。
  - **`kind` 缺省/未知一律按 `rl` 渲染**（保守方向单侧）：python 比控制台旧时（还没这个字段）少一个徽标只是少信息；凭空空贴 BC 标签则会对外宣称「一轮 = 一个任务」（而它有 13 步）——假承诺比缺标签贵。`BC` 这种大小写不符也不认（只认 python 的确切取值）。
  - **卡片解除 `isBc` 门控**：`stateView.isBc` 说的是**当前查看的那门课**，而这张卡是**跨课程**的——用它门控是范畴错误，后果是「选中一门 BC 课 ⇒ 整张卡片消失」，于是 BC 课在调度器视图里根本不存在（而 BC 课正是最需要看「在等哪个 GPU job 回传」的那种）。BC 行只多一个 `BC` 徽标 + 换成「一轮 = 一个任务」的悬停文案（不出现 RL 的门禁/verdict/KL 字眼）。
- **未做（明确记录，不是漏）**：① 真机「一个 serve 进程带 BC + RL 双课」的实跑（本轮全在假件下证明逻辑，与 R2e 同一口径）；② BC 一轮再下沉成细粒度步骤。

## §2026-09-19-goalnn-console-card-families（2026-09-19，R3-3：组件卡按「单例角色 vs 按课程」分族）

- **背景**：控制台把六个受管组件排成一行 chips，谁跟课程绑定、谁是全机/全局一份，只能靠 `<b>共享</b>` 这一个布尔徽章与操作员的记忆区分。两条具体误读都是这台机器上真会发生的：① hub/隧道已单例（§2026-09-18），但卡片仍按「当前查看的课」渲染，操作员会给这门课**再起一个 hub**（第二个实例抢同一端口）；② trainer 卡片看着像全局对象，但按下去起的是**当前查看的那门课**——换课程 = 换对象这件事在视觉上没有任何提示。
- **决定**：把「作用域」提升为一等事实，卡片按它分族渲染（`web/view/component-groups.ts`）。
  - **三态作用域由 `core/registry.ts::componentScope(key)` 单点给出**（`singleton` = selfNode / `shared` = hub·隧道（账本槽恒 `''`）/ `course` = 其余），服务端算一次填进 `ComponentView.scope`；客户端**不许**自己按 key 猜。
  - **族归属 = scope 的函数**，视图层**不写**「哪些 key 属于哪一族」的名单：族名单一旦与账本槽位规则漂开，症状是某个组件从 UI 上**消失**（而它照样被启动、被监督、被冒烟）。面板只负责画。
  - **节点面例外声明成数据**（`NODE_FACE_COMPONENTS = ['workerServe']`），不再用面板里的一行 `filter` 静默过滤：`worker_server` 的语义轴是节点/GPU 身份（id/url/concurrency 都是节点的，hub 的 push 派发与竞速也按节点算），卡片行再渲染一份就与节点行出现「同一件事两个入口」。`LogNavCard` 复用同一常量（两处 filter 漂开 = 某个组件某处消失）。
  - **组内顺序是纯化妆**（`ORDER` 表；未列出的 key 落组尾但**不丢**），**空组不渲染**（没东西可说时不留空壳）。族标题 + 悬停说明上屏（「与课程数量无关」/「卡片上的对象是当前查看的那门课」）。
  - **`scope` 缺省/未知 ⇒ 按 `course` 渲染**（单侧保守，与 loop-queue 的 `kind` 同一条规矩）：少一个徽章只是少信息；凭空空贴「共享」会让操作员以为「停它就是停全局」（而它其实只停本课）——假承诺比缺标签贵。
  - 徽章只标 scope 说不出来的那件事：`shared` ⇒ 「共享」、`singleton` ⇒ 「单例」、`course` ⇒ **无徽章**（按课程是默认语义，组标题已说；每行再挂一个只是噪声）。
- **备选与否决**：① 继续排一行、只加徽章——否（这正是问题本身：单例角色与按课对象混在一个序列里）；② 在面板里写两族 key 名单——否（第二份真相，且新增组件会静默落进没人认识的桶）；③ 保留 `shared` 布尔再另加 `scope`——否（两个字段 = 两个真相，必然漂开）；④ 顺手把 trainer/localWorker 的账本键也收敛成共享槽——**本轮不做**（见下）。
- **违反后果**：任何客户端按 key 自建族别名，都会在 registry 改规则的那天让某个组件**静默消失**；任何把 `shared` 语义空贴给按课程组件的写法，都会把「只停本课」演成「停全局」。
- **落地**：`core/registry.ts`（`ComponentScope` + `componentScope`）· `server/api/views.ts`（`ComponentView.scope` 取代 `shared`）· `web/view/component-groups.ts`（新：`cardFamilies` / `scopeBadge` / `NODE_FACE_COMPONENTS` / `FAMILY_META`）· `web/app/panels/ComponentCards.tsx`（分组渲染）· `web/app/panels/LogNavCard.tsx`（复用例外常量）· `web/theme.css`（`.tc-comps__group*` / `.tc-cc__scope--*`）。回归：`tests/web-component-groups.test.ts`(11：分族与族内顺序 / 节点面例外是真组件 / **全组件恰好归属一处** / **与 registry 判据对拍** / `scope` 缺省保守 / 未列出的 key 不丢 / 空组不渲染 / 不改动调用方数组 / 徽章三态) · `tests/web-components.test.ts`（分族 SSR：两组标题与 `data-family`、族内顺序、共享×2+单例×1、节点面组件不在卡行）· `tests/single-hub-tunnel.test.ts`（`.shared` → `.scope`）。进度 `docs/nn.progress.md §88`。
- **未做（明确记录，不是漏）**：账本键的真正收敛——`trainingLoop`/`localWorker` 仍是 per-course 键（控制台仍按课起 `run_rl.py --course`，尽管训练侧已有 `--serve` 单进程服务所有课程），`workerServe` 仍住 per-course 表（轴却是节点）。那是**启动面/监督面**的改动（含 `TrainLaunchModal` 的精简与旧条目换代接管），与本轮的「把两族读出来、说清楚」是两件事；本轮的分族恰好是它的前置（换成共享槽后，课程面只剩数据、进程面全在服务面）。
- **一条构建期坑（值得记）**：客户端代码里写**未加引号的 `node:` 对象键**（`{ node: [] }`）会让三份 bundle 全红——`server/build.ts` 的禁词门禁把 `node:` 当「引入了 node 内置模块」。本文件已在 `ComponentFamilyId` 注释里写明。

## §2026-09-19-goalnn-shared-trainer-single-process（2026-09-19，R3-5：trainer 收敛为「一个进程服务所有课程」）

- **背景**：R3-3 分族时留了一条明写的边界——分族只交付**读面**，账本键的真收敛未做：控制台仍按课起 `run_rl.py --course`，于是「BC 课 A + RL 课 B」要两个进程，尽管 ① R2d 已造好单进程驱动者（`rl/loop_serve.py`：按课锁 / 按课日志镜像 / 引擎池 / 故障隔离 / 暂停恢复）、② R3-4 让同一个进程也能带 BC 课。用户口径：「hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程，就能同时支持所有并行训练课程」。
- **决定**：`trainingLoop` 进 `SHARED_COMPONENTS`（账本槽恒 `''`，与 hub/隧道同一张表同一套哨兵），控制台一律拉起 `run_rl_cluster.py --serve`；卡片自动从课程面落到**服务面**（族归属是 `componentScope` 的函数，R3-3 已把这条路修好——本轮只改判据，不改 UI）。
  - **课程 = 文件系统事实**（`<traj-root>/<课>/training_log.jsonl` 存在，与训练侧 `rl/loop_plan.discover_courses` 同一判据）⇒ 启动时**不给 `--courses`**，「先起 trainer、后加课」不需要重启进程；代价是控制台得替**这门课**把账本文件建出来（`prepareCourseForSharedTrainer` ②）。
  - **每课旋钮住 rl-config**（`courses.<课>.{remote_transport,remote_hub_url,remote_degrade_after}`；新 `stack/course-knobs.ts` 是唯一写面，python `loop_serve.apply_course_machine_overrides` 在**开课时**施加：白名单 + 值域校验 + 逐键打印）。单进程没有「这门课的 flag」这一说（命令行只有一份）。**绝不写进 `curricula/*.jsonc`**——课程文件字节 = `course_fp` 语料血缘/熔断口径（D14），往里加一个传输旋钮，熔断会把同一份语料读成新语料。已开课的课程要**重开**才换传输（暂停该课 → 重启共享 trainer / 等引擎驱逐），这一点写进了动作返回值。
  - **幂等早退仍做本课准备，但准备失败不改事实**：早退前照样写本课旋钮 + 建账本（不写则「给这门课换成 push」是静默无效的动作），但准备阶段抛错时**不冒泡成通用失败**——消息必须以「已在运行 / 服务所有课程」开头、失败只说本课。理由很具体：说成「trainer 启动失败」会诱使操作员去停/重启它，而**停共享 trainer = 停掉所有课程的训练**。
  - **进程级单实例锁**（`nn-training/.run_cluster.lock`，`lockName('', 'run_cluster')`）：按课锁拦不住「两套调度器各跑一半课程，每门课都恰好只有一个跑者」。python `--serve` 自己响亮拒启 + 控制台在 spawn 之前先探（错误落在动作返回值里，不在日志里）；停机时释放。
  - **旧形状换代接管**：存活的 per-course trainer 由 `stack/hub.ts::supersedeLegacyInstances('trainingLoop')` 显式停掉并清账（与 hub/隧道同规），死条目也清；`restartSpecFor('trainingLoop', <课>)` 对**每课条目返回 null**——用共享 spec 重建一个每课条目 = 两套调度器抢同一批 traj。
  - **停止语义**：停 trainer = 停**所有**课程的训练（消息里必须说出来，否则操作员以为只停了当前查看的那门课）；停单门课用调度器卡片的「暂停」（控制文件 `tmp/loop-control.json`，只影响调度，队列/账本一个字不动）。
  - **「在训」判据随之改口径**：registry 里不再有每课条目，故「哪几门课在训」= **调度器（`sharedTrainerAlive()`）+ 该课未收官**（`loop-queue` 的行），总览与课程 select 高亮同源。
- **备选与否决**：① 给 `--serve` 传 `--courses <课表>`——否（进程绑死课程表，「先起 trainer、后加课」当场失效，而 hub 已确立「课程 = 发现」的口径）；② 每课传输旋钮塞进 `curricula/*.jsonc`——否（熔断口径，见上）；③ 共享 trainer 改住扁平单例键——否（旧账本里的 per-course 条目必须继续可见、可枚举、可停止；静默失监督是事故。共用一张表天然做到，靠槽 `''` 区分）；④ 顺手把 `localWorker`/`workerServe` 也收敛——**不做**（本机 PPO worker 的语义就是「poll **本课** hub」，每课一个是对的；`worker_server` 的语义轴是节点，见 `NODE_FACE_COMPONENTS`）。
- **违反后果**：任何「按课起一个 trainer」的残留路径都会与共享调度器抢同一批 traj（症状：同一轮被两个进程各跑一半、账本交错、锁语义失效）；任何把本课准备失败说成 trainer 启动失败的话术，都会让操作员停掉所有课程。
- **落地（训练侧）**：`nn-training/rl/loop_serve.py`（`apply_course_machine_overrides` + 覆盖叠加与优先级 + 非法值响亮）· `nn-training/run_rl_cluster.py`（`--serve` 进程级单实例锁 + `--ppo` 直通）。回归：`tests/test_serve_course_overrides.py`(10：覆盖叠加/优先级/非法值响亮/未知键不认) · `tests/test_serve_wiring.py`(+1：单实例锁接线)。
- **落地（控制台）**：`core/registry.ts`（`trainingLoop` 进共享表）· `core/slots.ts`（锁名归一）· `core/types.ts` · `launch/cli.ts` · `stack/specs.ts`（`trainerServeSpec`，发现模式 argv）· `stack/course-knobs.ts`（新：机器侧旋钮写面）· `server/actions/{start,stop,restart,preset,smoke,train-smoke}.ts`（启动/换代/锁/停止语义/冒烟独占）· `server/api/{overview,loop-queue,state-view}.ts`（在训判据）· `web/`（视图同源）。回归：`tests/training-shared-trainer.test.ts`(9：argv 不绑课程表 / 每课条目拒重建 / mode→transport 逐条 / 账本=发现判据 / 进程级锁 / 停止语义 / 冒烟独占) · `tests/training-console-busy.test.ts`(9：**幂等早退仍释放 busy 键** + **准备失败不许冒充「启动失败」**，夹具已重定向 traj 根与 rl-config——此前它把断言挂在「本机 tmp 恰好有没有权重文件」上)。进度 `docs/nn.progress.md §89`。
- **未做（明确记录，不是漏）**：① 真机「一个 serve 进程同时带 RL 课 + BC 课」的实弹运行（本轮全在夹具/假件下证明逻辑，与 R2e 同口径）；② `workerServe` 账本键的轴仍是课程（展示面已按节点例外声明，账本键未动）；③ `TrainLaunchModal` 的精简（模式仍按课选，落点已改为课程旋钮）。

## §2026-09-19-goalnn-shared-local-worker（2026-09-19，用户指令：localWorker 也不应绑定课程）

- **背景**：R3-3/R3-5 把 hub / 隧道 / trainer 收敛成共享实例后，本机 PPO worker 仍是「每课一份」——控制台按课起 `remote_worker --poll 本课 hub`，账本住 `localWorkers[<课>]`，work 目录与日志也 per-course。用户口径：「localWorker 也不应绑定课程，**它和云端 worker 一样，只与 hub 通信（pull/push），领到任务后直接执行，完成后回传结果**」。
- **为什么这是事实而不是需求**：`GET /jobs/next` **从来不看课程**——挑活的是 hub 的队列（每课程一条 FIFO + 跨课程轮转），响应里的 `course` 只是**随包告知的观测字段**；job 的 manifest 又自带整份课程快照（`course` 字段是课程文件正文），worker 侧连 reward 函数都是照 job 快照重建的。于是「按课程键控」只产生了三样副作用：① 一份进程只能服务一门课；② 同机多份进程抢同一份队列里的活；③ 「这门课的 worker」这个不存在的归属感。
- **决定**：`localWorker` 进 `SHARED_COMPONENTS`（账本槽恒 `''`，与 hub / 隧道 / trainer 同一张表同一套哨兵），spec **与课程无关**（`course: ''`、单一 `tmp/local-worker` work 目录、单一 `tmp/local-worker.log`）。
  - **启动幂等 + 换代接管**：已在跑 ⇒ 早退报「一个进程服务所有课程」；未跑 ⇒ 先 `supersedeLegacyInstances('localWorker')`（必须在 spawn **之前**：旧形状的每课实例与共享实例服务的是同一份队列里的活，多份并存就是互相抢）再 spawn，登记固定走 `''` 槽。
  - **重建路径拒每课条目**：`restartSpecFor('localWorker', <课>)` 在共享槽为空时返回 null——绝不允许凭一条每课残留就把共享 worker「重建」出来（那是操作员从未同意过的第二个进程）。旧的每课条目由启动/停止时的换代接管收掉。
  - **离开 local 的预设不连坐**（2026-09-16 那条「切到 pull/push 后卡片仍亮绿点」的修法升级版）：停共享 worker = 本机不再执行**任何**课程的 PPO job，故只在「本课是最后一门 local 课」时才停。判据是 `stack/local-worker.ts::coursesInLocalMode`——`courses.<课>.remote_transport === 'pull'` ∧ `remote_hub_url === sharedHubUrl(cfg)`，即 local 预设写下的那两个键（`prepareCourseForSharedTrainer` 的 local 分支）。**刻意不按「同端口就算本机 hub」放宽**：云机 pull 课程写的是 tailnet 地址但同一个 hub 端口，放宽会让「把唯一的课从 local 切到 pull」永远停不掉 worker——正是用户反馈要修的那个「以为未启动却在跑」。
  - **并发语义写进卡片文案**：一份进程同一时刻只干一份活（与云端 worker 逐字同语义——想要本机并发就多起几个云端 worker）；「停止」= 本机不再执行任何课程的 PPO job（云端 worker 不受影响）。
  - **读面的终点**：至此「服务面 · 单例」= selfNode + hubServer + cloudflared + trainingLoop + localWorker，「课程面」**没有成员**（`workerServe` 走节点行）。`cardFamilies` 因此只渲染服务面一组——**这不是坏了**，故在 `component-groups.ts` 里写明，并保留 `course` 族（它是 scope 的函数：日后真出现按课程的卡片会自然落进去，不必改代码）。
- **备选与否决**：① 保留每课一份 worker——否（见上三样副作用；且多份进程的日志把「谁在干活」彻底打散）；② 给 worker 加 `--course` 按课领活——否（hub 是**单队列**模型：job 自带课程快照，过滤只会制造「某门课的活没人领」这种静默饥饿）；③ 让 worker 按课起多个实例（worker 身份轴）——否（同一台机器多份进程抢同一份队列 = 旧的竞态，只是换了个名字）；④ 顺手把 `workerServe`（本机伪 GPU 节点）也收敛为节点轴——**本轮不做**：它的端口按课程派生（`slotPort(cfg, course, 'push')`，R3-1 刚把每课 push 端口摊开防撞），与 push 目标解析耦合，值得单独一轮。
- **违反后果**：任何「按课起一个 worker」的残留路径都会让同一份 job 被两份进程抢（一个白跑一轮）；任何无脑「离开 local 就停 worker」的写法会把其它 local 课的 job 变成无人领取（**表面训练正常**——最坏的一类静默失败）；反过来把 worker 常驻留着，则会把云机 pull 课的 job 抢来本机跑（云机空转，同样『正常』）——这三点正是判据要从配置算出来的理由。
- **落地（控制台）**：`core/registry.ts`（`localWorker` 进共享表；新增 `SharedComponent` 类型让换代/停止按它窄化，不再各自写名单）· `core/types.ts`（槽位契约注释）· `stack/specs.ts`（`localWorkerSpec(cfg, venv)`）· `stack/local-worker.ts`（共享启动 + `coursesInLocalMode`）· `stack/hub.ts`（`supersedeLegacyInstances` 收 localWorker）· `server/actions/{start,stop,restart,smoke,preset}.ts` · `server/api/component-meta.ts`（单一日志路径）。回归：`tests/local-worker.test.ts`（重写为共享形状 19 例：spec 与课程无关 / 槽归一 / 账本住空串槽 / 重建只认共享槽 / **换代不碰共享实例** / `coursesInLocalMode` 的五种配置 / 接线五处门禁）· `tests/{single-hub-tunnel,web-component-groups,web-components}.test.ts`（scope 与分族同步到真值）。
- **落地（训练侧）**：**一行未改**——worker 本来就行得通（`poll_job(base_url, token, …)` 无课程参数）。新增集成用例 `tests/test_local_worker_multi_course.py`(3，真 hub 进程内 HTTP + 真 worker 领活函数)：① 同一 worker 身份依次领到两门课的 job（跨课程轮转 + 响应自报 `course`）；②「先起 worker、后加课」时同一进程立刻能领新课的活；③ 形参围栏——领活链路里不得出现「课程」（哪天有人给 worker 加 `--course`，这条会红）。
- **未做（明确记录，不是漏）**：① `workerServe` 的节点轴收敛（见否决④）；② 本机多 worker 实例（worker 身份轴）——当前是「一份进程 + 云端多 worker」的并发模型；③ 真机实弹：本机 worker 领两门并行课的真实 PPO job（本轮是夹具级 + 进程内真 hub 的证据，与 R2e 同口径）。

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

- **背景**：控制台启动一门课要在弹窗里选 Pull / Push / Local，并把选择折成**课程级传输耦合**写进 rl-config：`courses.<课>.remote_transport`（`auto|pull|push|hubpush|local`）、`courses.<课>.push_node_url`（把某课钉到某台机器）、`courses.<课>.remote_hub_url`、`courses.<课>.hub_push`。用户口径：**「启动课程训练时，trainloop 不需要指定 pull/push 模式。pull 模式是由远端 worker 自己请求，本机只需要保证 hub 在线，配以 tailscale/cloudflared tunnel。push 模式只看系统是否已经配置了 push worker 节点，界面留配置入口，节点数据存 rl-config.json」**，并强调「课程任务与 worker 节点互相正交！所有 worker 都可能接到在训的课程任务！本地 worker 与云端 worker 完全一致」。
- **决定**：**传输不再是课程属性，也不再是启动选项**——它是部署事实，由「有没有登记节点」推出来。
  - **启动编排只剩一条**：`selfNode → hubServer → trainer`（`TRAIN_START_ORDER`）。**pull 零配置**：hub 在线 + （可选）隧道就够，任何 worker 自己来领活；每次启动仍写 `rl.remote_hub_url`（pull 与 hub 派发都要它）。本机 worker 不再是「local 模式」的一部分——它是独立共享卡片，起它就参与领活，与云机逐字同权。
  - **push 只看登记事实**：`nodes[].gpu_push`（enabled）+ `rl.hub_push`（**缺省 true** = 配了节点就走 hub 中介派发）+ hub 地址/token ⇒ hub 按队列推给空闲 worker；缺任一 ⇒ 直推登记节点（按序 failover）；一个节点都没登记 ⇒ hub pull。判定是**纯函数** `stack/push-config.ts::remoteExecutionFace`（卡面徽章 / 启动详情 / 总览同一份）。
  - **课程级键全删**（类型表 + python 读面 + 写面一起）：`push_node_url` / `remote_transport` / `remote_hub_url` / `courses.<课>.hub_push`。python 侧对应：`_course_push_url` / `_course_hub_push` 删除，`_gpu_push_nodes(token)` 不再按课过滤（登记即全部候选），`_hub_push_opt_in()` 只读全局 `rl.hub_push` 且**缺省 True**；`--remote-transport` 仍在（运维钉死一条路的最后手段），但**控制台不再代写它**。
  - **清理而非停止读取**（`pruneLegacyCourseKnobs`，启动训练时跑一次、幂等）：残留的旧键会让「新配的 push 节点永远吃不到活」或「旧指针指向死端点」——都是**静默**失败，故从 rl-config 里剃掉并记一行日志。`local_push` 伪节点条目同时清（python 的 auto 会把它当真节点）。
  - **控制台界面随之收敛**：启动弹窗**删掉模式选择与 push 凭据输入**（只剩隧道/瘦身/rollout 三个 rl-config 选项 + 降级开关 + 预演入口）；`console-state.trainerPpo` 退役；localStorage 的 `tc.train.mode` 退役；`setMode('trainer.ppo')` 响亮拒绝。**唯一配置入口 = 「push worker 登记」面板**（增删改 `nodes[]` + `rl.hub_push` 开关 + 探活两列），节点数据住 rl-config.json，hub 按 mtime 热重载；执行面徽章从「本课指向谁」换成机群级「这轮 PPO 会去哪」。
  - **冒烟预演改用 env 独占**：`REMOTE_PUSH_NODE` 一旦设置就**只有它**（登记节点一律不参与），否则伪节点失败时 failover 会把预演的 job 送去真 GPU 上跑。
- **备选与否决**：① 保留模式但默认 `auto`（少改 UI）——否（「模式」这个词本身就是误诊源：它让人以为 pull/push 是每门课的属性，而实际是机群的部署形态）；② 保留 `courses.<课>.push_node_url` 但默认不写——否（留着就有「这次启动写了没写」的二义，且用户口径是彻底删掉；N:1 共享由「登记一次、全体候选」天然得到）；③ 只删读面、保留键「以防万一」——否（`local_push` 那条仍**有读者**：留一条指向本机死端点的 gpu_push 条目会让训练静默地跑不起来，而表面一切正常）；④ 让控制台继续往 `rl.hub_push` 写 `1`——否（缺省已是开，写死反而让「显式关掉」在下次启动被覆盖）；⑤ 保留 `trainerPpo` 只为展示历史模式——否（它的唯一用途就是启动时选路，没有読者就成了一个会误导人的死键）。
- **违反后果**：任何重新引入按课程的传输旋钮（`courses.<课>.remote_transport|push_node_url|hub_push`）的改动都会重新制造「同一门课换个机器就得改课程配置」与「某课被某台机器独占」的耦合；把 `rl.hub_push` 缺省改回 `false` 会让「配了节点」不再够用（还得记得去开开关）——用户明确要的是「配了就走 hub 派发」；把 `REMOTE_PUSH_NODE` 的独占性去掉会让冒烟预演在伪节点失败时把 job 送上真 GPU。
- **落地**：训练侧 `rl/{loop_steps,loop_serve,bc_loop,cli}.py` · `run_bc.py` · 回归 `tests/{test_course_push,test_serve_course_overrides}.py`；控制台 `core/types.ts` · `stack/{course-knobs,push-config,specs,local-worker}.ts` · `server/actions/{start,preset,workers,console-state,train-smoke}.ts` · `server/api/{route,courses,overview,snapshot-cache,state-view}.ts` · `web/view/{console-types,course-overview,legacy-keys}.ts` · `web/app/{app.tsx,panels/{TrainLaunchModal,WorkerRegistry,ComponentCards}.tsx}` · `theme.css`；回归 `tests/{push-config,web-train-launch-wiring,server-actions-worker-register,server-api-state-view,server-api-route,training-shared-trainer,training-multi-course,training-train,local-worker,slim-launch-option,rollout-src-launch-option,web-components,web-app-course-overview,web-app-bc-rl-exclusive,web-app-hero-overview,web-view-trend-range,training-console-busy}.test.ts`（`push-config` 重写为「部署事实推导表 + 防回流尺子」，其中一把尺子剥注释后扫代码，因为文档注释里恰恰写着这些键已退役）。
- **仍未做（明确记录）**：① 真机实弹——「一个 serve 进程同时带 RL + BC 并行课 + 云机登记节点」的端到端（本轮全在夹具下证明逻辑）；② `Dashboard README / docs/features.md` 里的模式说明未同步（属文档面）；③ 冒烟预演仍是「本机伪节点 + env 独占」形态——若将来支持「预演也用真节点」，需另开一轮设计（当前口径是预演绝不碰真训练）。

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
- **备选与否决**：保持「等全节点 ready 再开采 + pure_collect=末局−全 ready」——否，与用户
  端到端口径冲突，且 it19 实测串行分发 50s×多波白白空转；rollout 只记纯仿真（末局−各节点
  自 ready）——否，用户明确要「开始分发→样本齐」一体读数。
- **落地**：`dist_common.rollout_collect_sec` / `partition_weights_nodes` / `_WEIGHTS_PUSHED`
  同 it 补波复用；dashboard `phaseSecs` 注释同步。DECISIONS 本条 = 口径变更备案（防再
  「优化」回旧锚点）。
- **多波聚合（同日补充）**：`rl/reports.aggregate_rollout_collect`——volume 各波报告带
  `weights_dist_start_ts` / `collect_end_ts`，`combine_reports` 压成 it 级
  `pure_collect_sec = min(start)→max(end)`（首波分发→全部样本齐，含波间空隙）。无 ts
  时回退 max(per-wave) 并标 `rollout_collect_aggregated=False`。iteration 事件附加
  `rollout_collect_aggregated` / `rollout_collect_waves`。

## §2026-09-19-volume-continuous-quota（2026-09-19，用户指令：退役离散补波 → 配额感知连续派发）

- **背景**：x20 分关样本缺口/wave_cap 复盘 —— 全局 est × 等 G0 盖不住 1 命下变体间
  产量方差（通关率波动 → 局均 nSamples 波动）。用户裁定：**完全去掉补波机制**，loop
  实时观测 samples 分布，即将足额不再派、差额大者多派。
- **规则 VOLUME_RULE_V2**（`rl/volume_quota.py`）：分关配额 `ceil(target/n_stages)`；
  每批 `allocate_stage_games`：`collected+inflight*est_s ≥ quota` → 软停；差额按
  `ceil(shortfall/est_s)` 派；`game_cap` 硬顶；`DEFAULT_MAX_BATCHES=12` 安全阀（触顶
  响亮 WARN，非静默短采）。种子 `(rotate_seed,it,stage)` 独立流第 k 局（无 wave_idx）。
- **备选与否决**：保留 G0+补波仅改分关 est —— 否，用户要求去掉波次语义；worker 内
  每局实时选关 —— 改动面过大，v2 用「账本驱动小批」逼近同一语义（批间同步结算）。
- **§15.5**：相对 wave 规则的语料构造变更 —— 迁课程建议 fresh `--out/--traj`。
  wave 纯函数（`volume_waves.plan_topup` 等）仍保留供旧单测/e2e；**生产串行路径**
  已切 `_volume_collect_continuous`（`loop_core`）。`resume.trailing_stage_samples_per_game`
  提供分关 est_s。
- **落地**：`rl/volume_quota.py`、`loop_core._volume_collect_continuous`、
  `tests/test_volume_quota.py`。回归：相关 pytest + parse 绿。

## §2026-09-19-console-node-wallsec（2026-09-19，用户指令：节点统计新增训练机侧平均墙钟）

- **背景**：节点统计「平均耗时」= meta `elapsedSec` 滑动均值；该字段是**节点侧**接单→
  结果就绪（sampler-agent `stampServiceSec`，含冷启动，**不含**训练机↔节点网络）。用户
  要求另加一列训练机侧墙钟，观测派发→结算的真实回传成本。
- **备选与否决**：把 trainer 墙钟**覆盖**进 `elapsedSec` —— 否，摧毁节点算力横向比
  （2026-09-06 口径升级正是为对齐 local/remote 服务时长）；只改 dashboard 从现有
  字段反推网络 —— 否，meta 无派发时刻，推不出来；在 agent 侧再起一个计时器写第二
  字段 —— 否，网络段只在 trainer 视角完整，节点侧测不到提交/回传。
- **决定**：双字段并列，互不覆盖。`wallSec` = **本 worker 本 attempt** 派发→结算墙钟
  （`t_task_start` 本地量，**勿读** `inflight_ts[task]`——竞速副本会覆盖）；成功结算时
  写入 `dist-agent-meta.jsonl` + summary。dashboard 聚合 `avgWallSec`（≤50 滑动，与
  `avgElapsedSec` 同窗），NodeStats 新列「机侧墙钟」。历史 meta 无 `wallSec` → 显示 `-`。
  含网络/异步轮询/排队，**不是**纯 RTT；慢节点判定仍用服务时长，不改 `isSlowNode`。
- **违反后果**：覆盖 `elapsedSec` ⇒ 节点算力对比被网络污染；用 `inflight_ts` 算墙钟
  ⇒ 竞速赢家的墙钟被输家起点抬高/压低；把墙钟当 ping 用 ⇒ 轮询间隔被误读成故障。
- **落地**：`nn-training/rl/{dispatch,queue,eval_dispatch}.py`（meta `wallSec`）；
  `dashboard/src/server/pool-history.ts`（`pushWindowSample`/`windowMeanSec`/`avgWallSec`）、
  `pool-types.ts`/`api/pool.ts`/`NodeStats.tsx`；回归 `dashboard/tests/server-pool-history.test.ts`。

## §2026-09-19-volume-purecollect-stale-merge（2026-09-19，bugfix：指标表 rollout 时间轮轮累加）

- **背景**：x20-powered/x20-snowball 的 `pure_collect_sec`（指标表 rollout 列）it1≈30s
  起每轮 +70~100s，it6 达 425s；同轮真采集 `rollout_sec` 始终 ~25–36s。
- **根因**：`_volume_collect_continuous` 收官时 `combine_reports([self._report, combined])`，
  而 `self._report` **未在轮初清空**——仍带上一轮 `weights_dist_start_ts`；
  `aggregate_rollout_collect` 的 min(start) 被钉在 run 起点，pure_collect ≈ 累计墙钟。
  同路径 `totalSamples` 也被跨轮相加（jsonl `samples` 虚高；`transitions_collected` 正常）。
- **备选与否决**：在 combine 前手工剥掉 prior 的 ts —— 否，prior 整份都不该进本轮报告
  （games/score 会双计）；dashboard 改读 `rolloutSec` 回避 —— 否，掩盖错误账本；
  保留跨轮 combine「凑完整 run 窗口」—— 否，与用户定义的「本轮采集」口径冲突。
- **决定**：轮初 `self._report = {}`；continuous 收官只采纳本轮
  `adopt_volume_report(combined)`（无 batch 时返回**合法空 shape** `combine_reports([])`，
  绝不返回 `{}` —— 否则 `_log_report`/events 读 `games` KeyError，2026-09-19 同日回归已修：
  combine 跳过空 dict、日志/events 用 `.get`）。历史 jsonl 的
  pure_collect/samples 累加行**作废对照**，请改看同轮 `rollout_sec`/`transitions_collected`。
- **违反后果**：任何把上一轮 `_report` 再 combine 进本轮采集的改动，都会让指标表
  rollout 列再次单调暴涨；任何让 volume 收官后 `_report` 停在 `{}` 的改动都会
  在 `_log_report` 打 KeyError 打死 trainer。
- **落地**：`nn-training/rl/reports.py`（`adopt_volume_report`/`empty_collect_report`/combine 跳空）、
  `loop_core.py`（轮初复位 + continuous 恒 adopt）、`loop_steps.py`/`events.py`（.get）；
  回归 `tests/test_rl_reports.py::test_adopt_volume_report_*`。

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

- **背景**：判决类评估（T5/T6 体量：多权重 × 数百～800 局课程自定义关）在纯本地
  chunked 路径（一 chunk 一 fresh worker，物理核封顶）上耗时以小时计，而节点在跑
  rollout 的间隙具备 eval 能力（`evalSupport` + `stageJsonSupport`），且 m1-eval 早已
  用同一 HTTP 协议派发 eval 局。缺的是**把 eval-course-ckpt 的逐局行**（JSONL，
  `phase0-fingerprints`/`paired-pd` 消费）也搬到节点上。
- **备选与否决**：① 另起一个 dist 专用工具 —— 否，映射/汇总两套实现必然漂移；
  ② 把课程评估塞进 m1-eval —— 否，m1-eval 的行语义是 godai scorecard（stageIndex/dims），
  不带课程自定义关与逐局 Phase 0 列；③ 只靠本地并发不开 dist —— 否，节点算力闲置；
  ④ 强制显式 `--dist-nodes`（不 auto）—— 否，与 m1-eval auto-dist 同规（用户 2026-08-29
  指令：节点随时上线、每批都要吃满；`--no-dist` 显式关）。
- **决定**：eval-course-ckpt 增混合分派路径，**缺省仍是原本地 chunked 路径（行为逐字不变）**。
  节点门与 rollout/m1-eval 同源：`evalSupport ∧ stageJsonSupport ∧ bun major.minor ∧ codeHash`，
  不匹配只 skip（打印原因）不中断；局经 `mode=eval` + `kind=<rollout|none>` + `stageJson`
  （课程自定义关原文）+ `livesOverride`/`playerLevel` 派发，stageJson > 16KB 自动退回纯本地；
  回包 BCV2 manifest 顶层 → `manifestToCourseRow`（缺 Phase 0 键填零，旧节点不崩）。
  `--dist-local`（缺省 = `--workers`）保留本地份额；`--policy nn-goal` 强制本地（GOAL_* 未
  进 agent 协议）。**真相锚**：同 (stage, seed, 权重) 的 dist 行与本地行**逐字节相同**
  （2026-09-19 实测 `diff` 空；§16.6 并行==串行验收）。
- **违反后果**：节点门放宽成「能 ping 就派」⇒ 新旧代码混跑，Phase 0 列静默缺失/口径不同，
  判决用错读数；改 `export-eval-game.ts` 顶层 schema（**在 codehash-files.txt 集内**）而不
  等节点同步 ⇒ 门把全节点判 stale、dist 静默退化成本地（本条的已知代价，不是故障）；
  直接拿 dist 行的 wallSec/网络时段做节点算力对比 ⇒ 网络污染（见
  §2026-09-19-console-node-wallsec）。
- **落地**：`tools/sim/eval-course-ckpt.ts`（`buildCourseJobs`/`buildRemoteTaskUrl`/
  `manifestToCourseRow`/`nodeGateReason` + `runHybrid`）、`tools/sim/export-eval-game.ts`
  （报告顶层补 Phase 0 逐敌种列 hitsByKind/killsByKind/exposureByKind/firstHit/firstKill/
  killOrder/killerKinds；集内文件 ⇒ 需节点 resync）、回归 `tests/eval-course-ckpt.test.ts`
  （纯函数对拍）+ 实测：对 `self` 节点 2 局 god 全链（ping→权重下发→stageJson 派发→回包
  →JSONL 行，Phase 0 列齐全）通过。

## §2026-09-19-evalboard-phase0-census（2026-09-19，Phase 0 逐敌种画像进 EvalStore schema（中方案 P1））

- **背景**：T5 主端点 = power 曝光归一命中/千 tick（七列：hitsByKind/killsByKind/
  exposureByKind/firstHitKind/firstKillKind/killOrder/killerKinds）。报告层已有这七列
  （`export-eval-game.ts` 顶层，同日早些时候落地），但 A/B/C/m1 四条逐局行构造点都没搬
  ⇒ EvalStore（唯一账本，§3.1）查不到分敌种读数。用户拍板「中方案」：列进 schema +
  判决批走 B 层（P2/P3 见 `docs/evalboard-phase0-census.md`）。
- **备选与否决**：① 维持现状（判决只读临时自造 JSONL）——否，账本永不沉淀分敌种读数、
  控制台无法看；② 只给 A 层行加列 —— 否，B/C 批（判决批的宿主）同样要读；③ 把七列塞
  `scorable.telemetry` 靠 `eval_loot_fields` 回退 —— 否，形态不同（telemetry 是标量，
  这七列是 4 元数组 + 序列表），且无端改 codehash 集内文件的报告形态；④ 直接进
  `REQUIRED_FIELDS` 不设豁免 —— 否，本批次之前的资产行会全量报缺（P0 覆盖率要求
  100% **或明示豁免**）。
- **决定**：`rl/eval_local.py::eval_census_fields` 单源（**只认顶层**，缺键 = None
  不伪造），A/B/C/m1 四个写点接线；`ingest.ts` 映射（畸形计数列/非字符串归零或空）；
  `store.ts` 七列进 `EvalGameRow` + `GAMEPLAY_FIELDS` + `REQUIRED_FIELDS`，并新增
  `PHASE0_FIELDS` 作为**旧资产行的明示豁免清单**（豁免由调用方传，不写死在
  `coverageReport` 里）。
- **违反后果**：七列改走 telemetry 回退 ⇒ 同一字段两种形态、旧值真假难辨；缺键填零
  却不进豁免清单 ⇒ 覆盖率报表冤报旧行、真缺失被噪声淹没；把七列排除在
  `GAMEPLAY_FIELDS` 外 ⇒ 双跑不一致无人发现（§3.4 确定性契约失效）。
- **落地**：`nn-training/rl/eval_local.py`（`EVAL_CENSUS_KEYS` / `eval_census_fields`）、
  `rl/{eval_dispatch,batch_eval,eval_a_once,eval_ingest}.py`；`dashboard/src/evalboard/
  {ingest,store}.ts`；回归 `nn-training/tests/test_eval_census_fields.py`（5 例）+  
  `dashboard/tests/evalboard-{ingest,store}.test.ts`（映射/豁免）。验证：真实
  `_eval_report.json` 七列齐全可抽；nn-python-gate 1263 绿 · dashboard 513 绿 +
  typecheck · 根 `bun run check` 1875 绿。

## §2026-09-19-evalboard-verdict-batch（2026-09-19，判决批走 B 层：语料注册表 + 多 ckpt 批类型（中方案 P2））

- **背景**：T5 判决语料 = 课程关卡文件 stages[] × **池外** seed 段（400600+，与训练池
  860001-860200 及已用池外段 400000/400200 不相交，§15.1 轮转纪律）× ≥2 个 ckpt
  **同种子逐局配对**（§3.5④ 不许事后求交集）。执行层（`mode=eval` + stageJson +
  lives/level 覆盖 + `export-eval-game.ts`）本来就在复用，缺的是**驱动器**：A 层
  （`EvalDispatcher`）语料写死在课程配置、单权重、无外部语料入口；B 层批键 =
  `(course, rung_from, ckpt)` ⇒ 一批一个 ckpt，且 ladder rung 承载「arena 阶梯几何 +
  段推进」语义。用户 2026-09-19 拍板「中方案」并选定两个分叉：**新建语料注册表** +
  **新增判决批类型**（见 `docs/evalboard-phase0-census.md` §3/§6）。
- **备选与否决**：① 给 A 层加 `--seed0/--games/多 --weights` —— 否，判决语料混装进
  训练课程配置会破坏「轮转键控」纪律（判决与日常读数本就该吃不同语料）；② 把语料塞进
  `ladder.json` 的 rungs —— 否，污染阶梯几何/段推进/去重键/ladder_pos 四处；③ 改造现有
  批键支持多权重 —— 否，动到 A/B/C 全链去重语义与历史行；④ 判决继续各造临时 JSONL ——
  否（这就是要修的现状：读数永不沉淀、控制台看不到趋势）。
- **决定**：① 语料身份独立成注册表 `dashboard/src/evalboard/corpora.json`
  （`{id, level, seed0, games_per_stage, policy?}`，读/校验/身份派生在 `corpora.ts`，
  坏行**响亮失败**）；② 新批类型 `kind='verdict'`（`trigger='verdict'`、`corpus`、
  `ckpts[]`），**`course/rung_from/ckpt` 置空串**——键空间分离靠 `kind` 判别，不用假 course
  去骗旧读方的键；③ 台账**单写者**不变：`verdict-cli.ts` 只往 `requests.jsonl` 追加
  `kind='verdict'` 请求，物化由 runner/`kick-once` 完成；④ unit = 一个 (ckpt × 关卡)，
  **权重在 unit 上**（批次级无权重 ⇒ 多 ckpt 批成立），每 ckpt 每关跑同一 seed 段；
  ⑤ 展开只有一处（`plan_verdict_units` / `units_for_batch`），训练内派发与一次性 kick
  共用。
- **违反后果**：语料登记进 rungs ⇒ 阶梯推进/去重/ladder_pos 全按假 rung 走；判决批填假
  `course` ⇒ 旧读方按 ladder 键匹配，判决行被并进课程读数；unit 不带权重而回落批次级
  `rl_path` ⇒ 多 ckpt 批实际全跑同一个权重（配对数看着齐、其实是同一策略）；两侧各写一份
  unit 展开 ⇒ 训练内能用而一次性 kick 跑不了（或将来的漂移）。
- **落地**：`dashboard/src/evalboard/{corpora.json,corpora.ts,verdict-cli.ts}`、
  `{batches,requests}.ts`（`kind/corpus/ckpts` + `verdictQueued/verdictCovered`，键 = 语料 id +
  ckpt **标签序**，顺序敏感）、`kick-once.py`（先 `consume_requests` 再 claim，`units_for_batch`
  统一展开；同批修其 `ROOT` 少算两层的既有 bug）；`nn-training/rl/batch_eval.py`
  （`load_corpora/corpus_doc/plan_verdict_units/units_for_batch` + `consume_requests` 判决分支
  + 单元权重透传）。**同批修两处硬伤**：god 局不 POST 权重且 wver 传 12 位 `key16` ⇒ agent
  `/v1/task` 按全量 sha 查桶必然 409（现 god 也 POST 占位 `{}` 并把其 sha 当 wver；`key16`
  仍是行身份/续跑键）；`kick-once.py` 的 `ROOT` 路径算错（2026-09-15 目录迁移遗留）⇒ 脚本
  一直 import 不到 nn-training。回归：`nn-training/tests/test_verdict_corpus.py`（10 例）+
  `dashboard/tests/evalboard-corpora.test.ts`（17 例）+ `test_batch_eval_wver.py` +
  `test_kick_once_paths.py`。验证：一次真判决批 kick（本机 self）行带齐 Phase-0 七列 +
  真 batch_id；nn-python-gate 1277 绿 · dashboard typecheck + 530 绿 · 根 `bun run check`
  1875 绿。**未做**：判决读数自动入 store（需显式 `ingest-cli.ts` 一步，属 P3）、控制台发起
  按钮。

## §2026-09-19-eval-tools-node-upgrade（2026-09-19，一次性评估工具复用训练循环的节点升级守卫 + m1-eval 补节点门）

- **背景**：`eval-course-ckpt.ts` 遇到 stale 节点只打一行 `codeHash mismatch … — skipped`，
  既不升级也不汇总告警；全灭时 `hybrid failed — falling back to local` 后**照跑本地**，
  汇总行不区分节点/本地（2026-09-19 实测：x20 it96 跑 ladder-c20-lives1，5 台远端全 stale，
  唯一的局其实是 self 节点跑的）。`m1-eval.ts` 更旧：`tryActivate` 只看 HTTP 200，
  **根本没有 codeHash/bun/能力位门** ⇒ 陈旧节点会被当可用算力，不同 era 的结果混进同一份读数。
- **备选与否决**：① TS 重写护栏/dirty 判据 —— 否，dirty 是字节级判据（`git ls-files -s` +
  `git cat-file --batch`，不能用 `git status`：autocrlf 会把 CRLF 污染藏起来，2026-09-09 mac
  事故），重写就是双语漂移 + 重演事故；② 只告警不升级 —— 否，用户要「能推升级」；③ 在 TS 里
  内联 `python -c` 拼脚本 —— 否（argv 引号/路径脆弱，且仓库规定 nn python 须经
  `tools/githook/nn-py-safe.sh`）；④ 升级默认开 —— 否，一次性判读工具不该默默重启别人的机器。
- **决定**：① 新增 `nn-training/dist_upgrade_cli.py`（stdin JSON spec → 逐节点调
  `dist_common.request_upgrade_guarded`，**单源**：护栏与 dirty 判据仍只在 Python 一处）；
  ② 新增 `tools/lib/node-upgrade.ts`（经 `nn-py-safe.sh` 拉起该 CLI；memo 文件
  `tmp/node-upgrade-memo.json` 跨调用去重，语义同 `_RESTART_SEEN`；**永不抛**——失败以
  `{ok:false,error}` 返回供调用方响亮告警）；③ 新增 `tools/lib/dist-node-gate.ts`（门 +
  聚合 WARN + provenance，两个工具共享；`eval-course-ckpt` 保留同名再导出，既有测试
  导入面不变）；④ 两工具接上：门逐条日志 + 收尾聚合 WARN（可用数/原因/两侧 codeHash）+
  provenance（`node:<id>`/`local` 逐局计数）+ **`--upgrade-nodes` 显式开关**才下发 pull+restart；
  ⑤ m1-eval 补上原本缺失的 codeHash/bun/能力位门（与 rollout/eval 同源）。
- **违反后果**：TS 重写 dirty 判据 ⇒ 重演 autocrlf 掩盖 CRLF（mac 卡 40 分钟）；升级默认开 ⇒
  判读脚本随处重启节点；不回填 memo ⇒ 每次跑 CLI 都再捶一遍同一 stale 节点；m1-eval 无门 ⇒
  陈旧节点的局混进 gate/scoreV7 读数且**看不出来**（本条的起点）。
- **落地**：`nn-training/dist_upgrade_cli.py` + `tests/test_dist_upgrade_cli.py`（8 例：spec 校验 /
  current 短路 / 映射到共享守卫 / dirty=null 真探测 / self 不探 dirty / dry-run 零 POST / 端到端）；
  `tools/lib/{dist-node-gate,node-upgrade}.ts` + `tests/{dist-node-gate,node-upgrade}.test.ts`
  （+21 例，含真子进程 dry-run）；`tools/sim/{eval-course-ckpt,m1-eval}.ts` 接线。
- **证据**：mock 节点全链实测——TS → `nn-py-safe.sh` → CLI → 守卫 → `POST /v1/restart` → 202，
  mock 端看到 body `{"pullBranch":""}`（self/回环语义强制禁 pull，护栏③ 生效）→ 回传
  `restart-requested` 并落 memo；真节点告警实测：`WARN dist nodes: 1/6 usable (self) ·
  5 stale (mac,a95,a97,a96,gcs)` + `provenance: node:self=1（共 1 局）`；m1-eval 同样输出 6/6 门
  结果。门禁：根 `bun run check` 1896 绿 · nn-python-gate 1285 绿。
- **未做**：控制台里的节点升级按钮（仍只有 CLI/训练循环）；`--require-nodes` 这类「无可用节点即
  非零退出」的判定开关（本轮只做到「响亮说出来」）。
- **追记二（同日，本机槽位必读 rl-config——用户 2026-09-19 实测报障）**：一次 1600 局的
  `eval-course-ckpt`（`--dist-local` 未给）跑出 `local=730 / 远端 870`，而 `nn-training/rl-config.json`
  写的是 `rl.local_slots: 0`（= 本机不参与、全交集群）。根因：两个工具的 `--dist-local` 缺省写死
  `workers`（物理核数 15），**从不看配置** ⇒ 机器口径被静默覆盖。
  - **决定**：本机槽位取值序 = 显式 `--dist-local` > `policy.evalLocalSlots`（评测专用旋钮，
    与 `rl/eval_local.py` 的 `EVAL_LOCAL_SLOTS_DEFAULT` 同序）> `rl.local_slots`（机器级，
    `dashboard/src/core/slots.ts` 同源）> 物理核数（配置未约定时的兜底）。实现为共享纯函数
    `configLocalSlots()`，两工具共用；启动日志固定打印生效值与**来源**（`--dist-local` /
    `配置 rl.local_slots` / `物理核数`）——缺省值从哪来决定了「本地 N 局」是配置意图还是意外。
  - **行为变化（重要）**：本机 **0 槽位现在真的意味着 0**。节点忙/不可达（503、ping 超时）时
    不再静默用本机补上，而是响亮失败（`incomplete hybrid results N/M — exit 1`）；想留本机兜底
    就显式 `--dist-local N`。节点被别的作业占满时这是预期行为，不是回归。
  - **同时修掉我上一版告警文案的误报**：`local>0` 曾被一律写成「节点部分失败或本地兜底」，
    把健康混跑报成故障；现只对**远端零参与**喊 WARN，混跑只报份额并点明由 `--dist-local` 决定。
  - **验证**：真配置下 16 局——`distLocal=0（来源：配置 rl.local_slots）` → `远端 8 / 本地 0`；
    m1-eval 同配置（单节点假配置）4 局全走节点；新增 `configLocalSlots` 3 例 + provenance 重写用例。
- **追记（同日，真节点收敛 + 两个判读修正）**：
  1. **升级真的收敛了**：`--upgrade-nodes` 后轮询 `rl-config.json` 全部 6 节点，`codeHash` 均为
     `ba6f7b4eda13…`（= 本机）、`agent=52cf887`（= HEAD）。**但收敛有尾巴**：mac/a95/a97 秒级收敛，
     a96 与 gcs 在 push 后的下一分钟里才翻过来（a96 曾返 502、gcs 曾保持 stale）——所以「push 成功」
     与「节点已可用于本批」不是同一时刻，判读时以**再 ping 一次**为准，不要拿 push 当次结果下结论。
     节点环境不支持远控升级属正常（本机隧道/反代差异），不必追求 6/6，一两台成功即达到目的。
  2. **修：小批量下排头的节点会秒光整批**（`fanOutOrder`）。链体在首次 await 前**同步** claim，旧代码
     按配置顺序把每个节点的并发链一次性起完 ⇒ 第一个节点（常是 self）把整批任务吃掉：实测 5 节点可用、
     8 局的批量里 `provenance: node:self=8`（远端一条没分到）。现改为轮转 spawn（每轮 1 条本地链 +
     每节点各 1 条），只改启动顺序，共享游标 + 尾部竞速语义不变；纯函数 + 单测 `fanOutOrder`。
- **追记三（同日，节点探测/判 stale 也回归 Python 单源——用户裁定）**：用户指出「Python 侧早就有这套
  节点通信与重试且经长期实战检验，别再在 TS 里重建」。复核属实：上一条的 `--upgrade-nodes` 虽然把
  **护栏**（dirty 判据/去重/self 禁 pull）交给了 `dist_upgrade_cli.py`，但**「谁是 stale」这一步仍在 TS 里
  自己 ping + 比 codeHash**——而 `dist_common.upgrade_stale_nodes(cfg, expected, branch, ...)` 早就是
  训练循环里那个「ping 每个 enabled 节点 → hash ≠ expected → request_upgrade_guarded」的完整实现。
  - **决定**：探测与判门也**只能有一处实现**。`dist_upgrade_cli.py` 增扫描模式（spec 给 `cfg_path`，
    由它自己 ping）；新增 `dist_common.seed_restart_state(entries)` 让一次性进程把调用方持久化的
    跨调用 memo 预置回 `_RESTART_SEEN`（判据仍是同一函数，调用方只存状态不写规则）；
    `upgrade_stale_nodes` 的结果补 `pingHash`（调用方写 memo 用的键）。`tools/lib/node-upgrade.ts`
    随之改为**扫描客户端**：spec = `{cfg_path, expected_hash, branch, seen, dry_run}`，TS **不再 ping、
    不再比 hash**；memo 键改为全量 hex（`nid|pingHash|expectedHash`，旧截断键自然失效、无害）。
    `eval-course-ckpt.ts` 因此不再 import `pingNode`/`nodeGateReason`；`m1-eval.ts` 的 `--upgrade-nodes`
    同样只传 cfg 路径。
  - **证据**：真集群 `--upgrade-nodes` 实跑（2 局 × x20 it96 × ladder-c20-lives1）——
    `node self/mac/a95/a97/gcs: 已是期望 codeHash` · `node a96: ping 不通`，随后 dispatch 照常
    `5 nodes → settled=1/1`（探测由 Python 做，TS 侧零 ping）。门禁：根 `bun run check` 绿 ·
    `nn-python-gate` 绿（ruff/mypy + 1296 例）。新增 Python 测试 6 例（扫描/stale·current 分流/dry-run
    零 POST/seen→dedup 真闸门/结构错误），重写 `tests/node-upgrade.test.ts` 为 spec 契约 + memo 往返。
  - **仍留的重复（明确不做假动作）**：`m1-eval.ts` 自己的**分派链**（判门/rescan/尾竞速/权重下发）
    还是 TS 实现——它的产物（`[m1-eval] WIN RATE` + 顶层 JSON report）被 `rl/eval_m1.py` 解析，
    整段改走 Python 得新写一个跑**内置关**的入口并接回训练循环契约，不是本轮的范围；本轮只把
    「升级探测」这一个已确认的重建点收敛掉。
  3. **加：`claims` 注脚（分派口径）与 `provenance`（结算口径）配对读**——实测一批 8 局出现 11 次分派：
     a95/a96/a97 各领到 1 局但结果被尾部竞速的重复副本抢走（`fanoutDup=2` 的设计使然，同一 (stage,seed)
     重跑结果逐字相同，故不影响读数），只看 provenance 会误读成「远端没拿到活」。
     `m1-eval` 本轮仍只打 provenance（其本地链先入队，分配口径的同样问题未动）。

- **追记四（同日，`eval-course-ckpt` 的 10054 真因：权重桶撞车；含一处我自己引入的回归）**：
  用户指出「`eval-course-ckpt` 还是有问题」，证据是另一 agent 的课程记录
  （`nn-training/curricula/x20-powered.jsonc` 第 5 行：「它占着 dist 集群，本腿 it0 探针会被 10054
  挤死」）。逐层排查（不是猜）得到四个独立缺陷，前两个是真因。
  1. **本机槽位链被我上一轮的重构悄悄弄回归了**（用户上一轮报障原样复活）：`configLocalSlots()`
     是上一轮为「`rl.local_slots: 0` 必须真的 0」建的共享纯函数，m1-eval 还在用，但
     **`eval-course-ckpt` 改成调 Python 后把它丢了** —— 缺省 `localSlots` 不写进 spec ⇒ Python 侧
     取 `policy.evalLocalSlots` 缺省 **4** ⇒ 整条配置链被跳过（`eval_course_once.py` 的注释甚至
     声称读了 `rl.local_slots`，而 Python 侧从来没读过）。
     - **修**：求解器收敛成 `dist-node-gate.pickDistLocal(explicit, cfg, fallback)`（纯函数，
       **两个工具共用**，m1-eval 的内联三元也换掉）；`eval-course-ckpt` 启动日志打印生效值 + 来源，
       并**总是**把解析结果写进 spec。实测 `distLocal=0（来源：配置 rl.local_slots）`。
  2. **10054 的真因 = 一次性评估的权重文件被训练作业扫掉**（不是「节点忙」）：节点侧按 kind 收敛
     权重文件（`workdir-cleanup.WEIGHT_FILES_KEEP = 4`），而训练作业**每轮**往 `rollout` 桶 POST 新
     权重 ⇒ 我们那份固定权重（`weights-rollout-d66378e…`）在几秒内被扫掉；但另一支 agent 进程
     （同机还有 8789 那支，共享 `tmp/dist-agent`）的内存桶仍答 "kept" ⇒ 任务子进程 `ENOENT` 退出 ⇒
     agent 直接断连 ⇒ client 只见 `WinError 10054`，与「节点满负荷」在传输层**不可区分**。
     重试耗尽 ⇒ 单元 0/50 settled；`local_slots: 0` 时整批 0 行、exit 1（这正是它「被挤死」的表象）。
     - **修**：新增 `ONESHOT_EVAL_KIND = "eval"`，两个一次性入口（`eval_course_once` /
       `eval_m1_once`）把权重 POST 进专用桶（agent 的 `x-kind` 本就能任意分桶），训练作业的
       `rollout` churn 扫不到它；**按关键字传** —— 位置写错会静默回落 `rollout`（我第一版真踩了：
       写到 14 号位置 = `init_sha16`，行为与修复前一模一样，靠单测才发现）。
     - **证据（训练作业**在跑**时实测）**：修复前 —— `weights[rollout] -> self (kept)` 后 8 局全
       `背压 6/6 耗尽 + 真失败` ⇒ `provenance: none`、0 行、exit 1；修复后 ——
       `weights[eval] -> … (kept)`、`weights-eval-d66378e3….json` 存活（同时 4 份 rollout 仍被
       KEEP 轮换）、**16/16 全远端、local 0、exit 0**。
  3. **10054 不再被当成节点故障**（既有实现缺陷，独立于上条）：`dist_common.fetch_task` 把
     连接被重置/超时/408·429·5xx 标为 `transient`（`DistError.transient`），`batch_eval` 对它**背压
     重排 + 指数退避**（上限 8s）而**不计** `nodeFailStreak`，并把「背压次数/真失败次数」与
     `provenance` 一起入账；单元 0 局且本机槽位 0 时打一行**响亮提示**指向权重文件缺失这一真因。
     旧行为：一瞬 10 次 10054 把 6 个节点在 1 秒内全部熔断（与 rl/bc_dispatch 的 busy 背压同源问题）。
  4. **runDir 复用会污染 provenance 判读**：`eval_log.jsonl` 每次运行都重建，而
     `dist-agent-meta.jsonl` 只追加 ⇒ 200 局的重跑里 meta 积 343 条（含上一轮 114 条远端条目），
     照它判「是否降级本地」会得出**反的**结论。新增 `_reset_run_ledgers()` 开跑前两个都清。
     （顺带纠正上一轮的一处判读：那份「200 局全 local」的产物来自**第二次运行**、且它的
     `spec.json` 写着 `noNodes: true` —— 是调用方显式 `--no-dist`，不是节点被熔断。）
  - **验证**：真集群 + **训练作业在跑**下三种模式实测（默认 `distLocal=0` 16/16 远端 ·
    `--dist-local 2` 打印来源且 `[local ×2]` 生效 · `--no-dist` 全本机）；`m1-eval`（god，2 局）
    `localSlots=0 kind=eval` 全远端。门禁：根 `bun run check` 绿 · `bun run build` 绿 ·
    `nn-python-gate` 绿（ruff/mypy + 1312 例）。新增测试：TS `pickDistLocal` 4 例 + 槽位链 2 例；
    Python `_reset_run_ledgers` 1 例 + **专用 kind 契约 1 例（源码级守卫，已实测对「位置传参」变体变红）**。

- **追记五（同日，800 局探针暴露两个真缺陷：起跑 7s 的串行 ping + 慢节点拖尾巴；事件级日志落地）**：
  用户实测「等了十几秒 CPU 才满」「CPU 满一阵又掉档一阵子（几十秒）」⇒ 要求把**权重传输完毕**与
  **评测结果返回**打到日志里排查（命令：`x20-rebirth` it96 × `ladder-c20-lives1` × 800 局
  `--seed0 418000`，非判决段、仅诊断）。
  - **落地的事件日志**（新增，已真集群验证）：① `dist_common.post_weights_parallel` 逐节点
    `weights[kind] -> <节点> (<mode>, X.XXs)` + **阶段总计**
    `weights[kind] ready on N/M nodes in X.XXs (sha …)`（分发起点即此）；② `batch_eval` 逐单元
    `阶段 gate X.XXs alive=N/M` / `阶段 weights X.XXs ok=N/M`；③ 逐局 `→ <节点> s/seed` 与
    `← <节点> s/seed X.Xs ticks=… outcome=…`（配对即得**在飞曲线**）；④ 每 2s 在飞采样
    `⏱ pending=… inflight=… settled=…`；⑤ **被 ping 丢掉的节点不再静默**（原先只是 `continue`，
    实测 `alive=4/6` 时看不出丢的是谁、为什么）。开关 `EVAL_TRACE_EVENTS`（一次性工具缺省开，
    训练循环路径缺省关 ⇒ A/B/C 层日志逐字不变）。
  - **发现①（起跑慢）**：`阶段 gate 6.83s alive=4/6` —— 节点门是**串行** ping，每台预算 3s，
    两台负载高的节点直接吃掉 ~7s；而门**每单元重跑一次**。修：新增
    `dist_common.ping_nodes_parallel`（保序、并行）⇒ 阶段墙钟 == 最慢一台，实测 6.83s → **3.01s**；
    并把「谁掉了、为什么」写进日志（实测 `node a96: ping 失败/超时`）。
  - **发现②（CPU 掉档的真因：慢节点拖尾巴）**：单元内前 ~30s 快节点（self/mac/gcs 平均 2.9/3.7/3.8s
    每局）就干完 ~170 局，之后 **pending=0**，只剩配置固定并发（a95/a97/a96 各 7）的慢节点在跑：
    **a96 平均 124s/局（最大 180s）、a97 42.3s、a95 20.3s**（同一台机器上训练作业抢 CPU）⇒ 每单元尾巴
    1–3 分钟、本机 8 个槽位与其余节点全部空转（实测 u3：`pending=0 inflight=7` 卡了 ~170s，7 局全在
    a96）。**这 3 台只贡献 71/800 局（8.9%）却吃掉 63% 的节点秒**。首轮（297s）与次轮（653s）的差距
    就来自这里，不是网络。**调度策略怎么改尚未定**（见下），本轮只做到「看得见 + 起跑不再白等」。
  - **实测读数（非判决段，仅吞吐参考）**：800/800 全远端 · **local=0** · 653.2s / 1.2 games/s；
    来源分布 self=356 · mac=251 · gcs=122 · a96=32 · a95=25 · a97=14；pass 82/800=10.3% ·
    kills 6.27（与首轮逐值相同：同种子确定性 ✓）。**不要拿它当 it96 capability 读数**（段未预注册）。

## §2026-09-19-m1-eval-python-dispatch（2026-09-19，m1-eval 分派链回归 Python：TS 只写 spec/读行/打分）

- **决定**：上一轮的「仍留的重复」点名的就是 `m1-eval.ts` 自己的分派链（判门/rescan/尾竞速/权重下发
  ≈300 行）。用户裁定同一口径——**Python 侧已有实战版，别再在 TS 重建**。新增
  `nn-training/eval_m1_once.py`（spec → `rl/batch_eval.BatchEvalRunner` → 逐局行），TS 只做
  「写 spec → 经 nn-py-safe.sh 调 Python → 读回逐局行 → scoreV7/报告/HTML/banner」。
- **边界（明确划出，不是半途而废）**：只有**分派**回归 Python。`--no-dist` 仍走本机 in-process
  worker 池——那是游戏引擎本身、不涉节点通信，且 `tools/perf/scan-intent-concurrency.ts` 正是量它的
  并发度；非分派 policy（`nn` 走 `--weights-dir` 自动发现、无文件可上传，`intent`/`intent-oracle`
  与 cadence 探针）也留在本机池。`goal-god` **不再分派**：远端 goal 执行器需要 goal 权重桶，
  而它按 kind='none' 分派时远端必然缺权重（旧实现看似分派、实则不可用）⇒ 要跑用 `--no-dist`。
- **Python 侧三处扩展（都是加法；既有调用方行为逐字节不变）**：
  1. `rl/batch_eval.py`：`kind` 由 policy 推（`KIND_FOR_POLICY`：intent-exec→'intent'、goal→'goal'、
     nn/god→'rollout'），**上传与查询同 kind**（此前写死 'rollout' ⇒ intent/goal 一律 409）；
     `include_scorable`（默认关；True 时逐局行多带 agent 报告的原始 `scorable` = scoreV7 的完整输入，
     原样回传、不做字段级搬运 ⇒ 不可能两端漂移）；unit 的 `lives`/`level` 缺省 = **不覆盖**
     （difficulty/关卡默认说了算；写死 3 会把「难度默认」硬编码成常数，改难度即错）。
  2. `rl/eval_m1.py`：`subprocess.run(text=True)` 补 `encoding="utf-8", errors="replace"`——父进程不传
     encoding 时按 locale 解码（zh-CN Windows = cp936），而 m1-eval 的 stderr 带中文 ⇒
     UnicodeDecodeError 被 `dispatch_eval_bg_m1` 的 except 吞成「clean eval failed (ignored)」，
     **干净评估静默消失**（2026-09-19 实测：本地/分布式两种调用都复现；与 gate_check §30 同类坑，
     那边靠 ensure_ascii 免疫）。
  3. 两个一次性入口（m1 / course）把 `sys.stdout` 改道 stderr：训练栈 `rl.log.log()` 按设计写 stdout，
     而这两个入口的 stdout 是调用方的**产物通道**（m1 的 JSON 报告 / 课程行）——实测 `[dist] weights[…]`
     行混进 stdout 后 `json.loads(stdout)` 取 perGame 会**静默失败**（D5(a) 入账缺口）。
- **验证（真集群 + 真消费方）**：
  - 三种分派 policy 实跑（`god` / `intent-exec` 用 `tools/gen-intent-weights.ts` 生成的全尺寸权重 /
    `goal` 用形状合法的合成权重）：6 节点在线、配置 `rl.local_slots: 0` ⇒ 逐局 `node:…` 全远端、本地 0；
  - **跨 runner 对拍**（同 stage/seed/权重，dist=export-eval-game vs 本机池=sim-worker）：逐字段一致，
    唯一差异是 `firstKillTick` ±1 tick 的采样口径（scoreV7 suite 完全相同）；
  - **训练循环真入口** `rl/eval_m1.py::run_clean_eval` 实跑 35 关 × 1 seed →
    `winRate=0.714 total=35 cleared=25 error=0 retries=0 perGame=35`；
  - 断点：dist 走 Python run dir 台账（二次运行 `already settled — skip`，0.0s；`--fresh` 才清），
    本机池仍走 TS ledger（`ledger resume: N/M already settled`）——两套各自完整，不叠加。
- **门禁**：根 `bun run check` 绿 · `nn-python-gate` 绿（ruff/mypy）。新增 Python 6 例
  （spec→unit 归一/行映射/kind 表与 DISPATCHABLE 对齐）+ TS 7 例（spec 构造/行映射/白名单）。

## §2026-09-19-x20-snowball（2026-09-19，x20 后继腿：中盘激励，里程碑 bonus 单变量 + god-prefix 否决）

- **背景**：x20 结算（池外 it30 7.5%/6.10杀，无合格终点＋旧能力丢失）＋ 用户假说
  "通关靠捡道具滚雪球"。证实：it30 池外段通关局 1.22 pu/千tick vs 死亡局 0.64、
  早期死亡局 0.20（存活归一化仍 2 倍）；早期死亡局 0.12 个/局；凶手四类全有 ⇒
  开局拾取缺口＋全面中盘续航赤字。线性 wKill 在中盘（5–12杀）是回报洼地。
- **本腿 thesis（单变量）**：`wMS8/12/16 = 6/9/12` edge-trigger bonus（Φ-diff，
  数值已验：7→8 跨越精确 +6.0 一次）。坐 EV 不动（到 8 杀概率 0）⇒ 走>莽>坐不变。
- **证据性否决（不做的理由）**：re-warm——peak it30 落在 it25 降温**之后**，冻结说
  证伪；加 batch——plateau 在噪声带之上清晰可见，batch 非瓶颈；容量——用户指令不动；
  gamma/lam——value-loss 不可观测（remote 只回 kl/entropy），不盲调。
- **god-prefix 中盘开局否决（本条核心）**：agent 已实现一半（export-rl-rollout
  前缀分支＋回退设计）后叫停并全 revert（git diff 确认零残留）。理由：prefix
  跳过开局，但缺口恰在开局拾取 —— 学生永远学不到"如何到达中盘"，药下错地方；
  且代价是热路径＋六文件垂直链＋节点升级波。fallback ① 改为 powered-opening
  （现成 --player-level，零引擎改动）/wPickup 剂量腿；god-prefix 重提需"到达后
  转化率瓶颈"的新证据。
- **§15.5 合规**：reward 语义变化 ⇒ 新实验：fresh `tmp/x20-snowball/` ＋ 本条目 ＋
  判决段 413000（已查 413000–413999 全空）；命数 tier 未变 ⇒ D11 不触发；
  c06 回测门按用户指令删除（c07 保留）；bc = it30（池外三选一胜者）。
- **落地**：`nn-training/curricula/x20-snowball.jsonc`（CourseConfig 校验通过；
  里程碑 8→12→16→20，分母池外 6.10；pass 门 ≥30% 不动；it40 主检点 kills<7.0 停；
  超时/换血/横盘/M2 全延续）。开训后填 it0 三件套。

## §2026-09-19-nn-decision-instant（2026-09-19，nn 决策时点归一到语料口径：NNInput 末帧决策 + nn 入列分派白名单）

- **决定**：① `m1-eval` 分派白名单加 `nn`（`--weights-dir` 解析出**最新**权重文件后上传，`kind='rollout'`）
  ——上一轮条目「nn 走 --weights-dir 自动发现、无文件可上传 ⇒ 留本机池」的**理由已被自身证伪**（解析出的
  文件就是可上传的权重）。② `src/nn/policy-input.ts`（NNInput）的**决策时点**改为「tick 末决策、下一 tick 生效」。
- **背景（2026-09-19 用户要求「nn 分派后与本地一致」时实测挖出）**：nn 有**两套实现且从不互相对账**
  （god 有 `tests/sim/eval-game-parity.test.ts` 钉远程/本地同局，nn 侧无对应用例）。
  同一 (权重=ep96, stage 0, seed 1..6)、同 maxTicks/difficulty：
  节点引擎 `export-eval-game` 得 4601/3167/6623/2113/2606/5580；本机池（`runSimulation`→NNInput）
  **6/6 全不同**（首个动作分歧在 seed 1 的 tick 290）。
- **根因两层，都在 NNInput**：
  1. **观察时点**：`Simulation.tick()` 先 `frame++`、递减 freeze/emp/spawn/pickup 计时器、跑
     `updateSpawning()`，**之后**才读玩家输入（Simulation.ts:205-245）；而三个构建器都在自己
     `sim.tick()` **之前**观察（`export-rl-rollout.ts:642`、`export-nn-replays.ts:116`、
     `export-eval-game.ts:482`）⇒ mid-tick 决策看到的是策略训练时从未见过的状态。
  2. **相位**：NNInput 用 `frame % K === 0`，构建器用 `t % K === 0`（`world.frame` = 已完成 tick 数
     = 构建器的 `t`；末帧 `frame` 即在读输入时是 `t+1`）⇒ 决策 tick 整体错一格。
- **修法**：决策只在 `reset()`（tick 0，调用方都在 `loadStageData` 之后 reset）与 `endFrame()`
  （`world.frame % K === 0`、且状态仍为 `playing`）发生；`getMoveDirection()`/`isFiring()` 只读已提交动作，
  不再 mid-tick 前向。推理次数仍是 1/K；`thinkNow()` 保留「当前状态强制一次前向」的诊断语义
  （divergence-probe 用）。
- **影响面（必须知道）**：历史所有**本机** nn 评估读数（m1-eval 本机池、eval-course-ckpt 本机 worker、
  `export-dagger-labels`、`nn-trace`）都是在该错时点上评的 ⇒ 这些数字会变；**远端节点侧不变**
  （export-eval-game 本来就是对的），故此前「本机池 vs 分派批」的混跑读数本就不可配对。
- **验证（四腿一致，stage 0 seeds 1-3 = 4601/3167/6623 gameover/gameover/stage_clear）**：
  真集群分派（weights 上传 → `mode=eval` kind=rollout → self 节点，`provenance: node:self=3 远端 3 / 本地 0`）、
  本机池、Python 分派路径（localSlots=3 实跑）、节点引擎 CLI 直跑——四者逐局 ticks/kills/outcome 全等；
  in-process 对拍 5 局（stage 0 seeds 1-3、stage 5 seeds 1/7）逐 tick 动作序列零分歧。
- **守卫（本案的副作用）**：`src/nn/policy-input.ts` 在 codeHash 集内 ⇒ 改它会让全集群 stale（节点 hash
  memo 到 `/v1/update` 真 pull 才失效）。self/回环节点**纯重启**（共享工作区、禁 pull、不受脏工作区护栏
  限制）即收敛到本机 live hash（实测 `c78d48de`）；其余节点须 **commit + push → `--upgrade-nodes`**
  （`dist_common.request_upgrade_guarded` 对脏工作区拒发，日志点名未提交的集内文件）。
- **回归守卫**：`tests/sim/eval-game-parity.test.ts` 新增 nn 分支对账（stage 0 seeds 1/2，maxTicks 3000，
  权重用 `tests/fixtures/student-golden.json` 的 params 落进临时目录的 `weights.json`）——已实测：旧
  `policy-input.ts` 下该用例**红**，修复后绿（≈0.3s）。
- **追记（2026-09-19 16:40，push 后真多节点复验）**：`908f7cf` 推到 `origin/goal-nn` 后集内脏集合清空
  （`{"dirty": []}`）⇒ 守卫放行远端升级：mac/a97 `restart-requested`、a95 首轮 `restart-failed` 但随即收敛，
  self 本就 current ⇒ **4/6 节点同本机 `c78d48de`**（a96 agent 不可达、gcs 拒重启，属用户已裁定的环境事实，不追 6/6）。
  12 局实跑（stage 0 seeds 1-12，`localSlots=0`）：**12/12 全落远端**（`provenance: node:mac=4, node:self=8`，
  7.5s），逐局 outcome/ticks/kills 与**本机池完全相等**（4601/3167/6623/2113/2606/5580/3535/3527/7183/2531/5875/2794），
  聚合也一致（`WIN RATE 33.3%`、`SCORE V7 suite=0.3621`）——跨两台不同机器验证，不再只是同机自证。
## §2026-09-19-eval-tier-channels（2026-09-19，B/C 层派发改为「每节点独立通道」：无阶段屏障、就绪即派单、失联重探、settled 满即断连）

**触发**：用户 2026-09-19 五条裁定（原话）：①「不要把分发权重和派发 eval 任务划分为串行的不同阶段！！！
一个节点权重分发成功后，立即！马上！right now！给它派发任务！！！不要等慢节点！」②「不要分什么 u0/u1/u2
阶段！！！一直持续不停派发，直到所有 eval 任务完成！」③「已经在正常工作的节点！！！就不要再 ping 它！
不要再给它分发同样的权重，一直派活就好了！」④「竞速后 settled 一满，直接关闭所有节点的连接！！！立即！
马上！right now！」⑤「失联的节点，每 20 秒 ping 一次，ping 通了就立即传权重派任务！」。两次追问收紧了细节：
「从 settled 满到 DONE 为什么还花 5 秒？能省掉吗？」（→ 收工只等写行的赢家）与「你这任务都没分出去呀」阶段
日志停在 13:17 的反面取证。

**实施（B/C 层 `rl/batch_eval.BatchEvalRunner`）**：派发由「ping 全部节点 → 全部节点收权重 → 才开派」
三段串行 + 每单元重跑，改为**每节点一条通道**（`_run` 内 lane 状态机）：

- **lane**：`{ready, tripped, next_try, tries, strikes, given_up, c(槽位)}` + 归一化节点描述
  `{"id","url","key"}`（旧实现直接拿配置 dict 取 `nd["key"]` 会 KeyError —— 本轮实测踩过：整批 0 局）。
  `supervise(nd)` 线程：未就绪 → `bringup`（ping → `post_weights`）；`ready=True` 才算消费者；就绪后**只在**
  该节点槽位线程里领活（永不重复 ping/传权重 = 裁定③）。
- **无阶段屏障**（裁定①）：每个节点的通道各自就绪、各自开派；`local` 槽位不需要门/权重 ⇒ 与节点通道并行、
  立刻开工。慢节点再也拖不住整批。
- **失联重探**（裁定⑤）：`recoverPingSec`（缺省 20s）重探一次，ping 通 → 立即传权重 → 立即派单；日志逐次
  响亮（`node <id>: ping 失败/超时（3.20s）— 20s 后重探`）。**有界**：连续 `nodeRecoveryTries`（缺省 3）轮
  「恢复后仍 0 局成功」⇒ 判定节点是坏的（不是一时失联）而非无限等；任意一局结算即清零轮次（有进展即信任）。
- **settled 满即断连**（裁定④）：最后结算的 worker 置位 `all_done` 后立即 `dist_common.abort_active_requests(scope)`
  —— 停发新请求 + **异步**（daemon 线程）关闭在飞连接。**作用域 = 线程 tag**（`batcheval:<iterId>`）：训练主循环
  里 rollout 与本层同进程并发，全局关连接会误伤别人的在飞请求。新单元开头 `clear_abort()`。
- **收工只等「写行的赢家」**（用户追问）：`writers` 计数（赢家在锁内自增、`record()` 落盘后自减），收工等它归零
  （≤1s 兜底，实测同秒）；线程 join 只给 0.25s 总预算（daemon 线程本就随进程退出）。慢节点/竞速副本的回包
  **一律不等**（那些行永不需要）——旧实现 join(5s×线程) 实测占 800 局墙钟 3%，更早还有 `close()` 阻塞主线程
  **81 秒**（占该批 32%）。
- **失败分类补洞**：主动断连/已结算任务的回包按「无关」丢弃（既非背压也非节点故障，否则会误熔断慢节点）；
  硬失败**每条都留痕**（原先只有「还会重排」的那条打日志 ⇒ 实测「真失败计数 gcs=1」却零原因可查）；竞速副本
  **不再消耗** `attempts` 配额（一局被 6 台各抢一次后一次真失败就会耗满配额而丢局）。

**单单元跨关（一次性评估，裁定②）**：`eval_course_once.build_course_units` 改为**每权重一个单元**、跨该权重的
全部关卡（`unit["pairs"]` = `[[stageId, seed], …]`，`unit["stageParams"][str(stageId)]` 带逐关
stageJson/lives/level/maxTicks/difficulty；B 层 `params_for` 逐字段回落 unit 级值）。ladder/corpora 的既有单元
（`stageId` × `seeds`）逐字不变。副作用修复：行元数据改按 **`(ckpt_sha16, stage, seed)`** 分键——原先
`meta[(stage, seed)]` 在多权重调用下被后一个权重覆盖，逐行 label 全错、`_row_id` 的 `wi * games` 也全错（多权重
产物实际不可用）。

**被否方案**：① 保留阶段结构只做并行（`ping_nodes_parallel`/`post_weights_parallel` 已有）——阶段**屏障**本身
才是病根，快节点仍要等慢节点；② 在 TS 侧实现同样的通道（`tools/sim/eval-course-ckpt.ts` 自带一套重试/探测）
——用户已裁定「Python 端有长期实战检验的机制，不要在 TS 重写一套」，本轮继续单一实现（TS 只写 spec/读行/打分）；
③ 无界重探失联节点——一个必坏节点会拖满整窗（一次性评估窗口缺省 86400s）；④ 收工 `join` 全部线程——实测 5s+，
且对结果毫无贡献。

**实测（800 局 × ladder-c20-lives1 × x20-rebirth it96 × seed0 418000，全远端 / 本地 0）**：
| 形态 | 墙钟 | 说明 |
|---|---|---|
| 旧（4 单元 × 3 阶段，17:34） | 297.0s | 全远端 |
| 旧（带事件追踪，17:42） | 653.2s | 慢节点兜底尾巴空转 170s |
| 新（首轮验证，18:41） | **268.3s** | 181s 跑完 800 局 + **86s 收工阻塞（已修）** |
| 新（断连非阻塞，18:48） | **166.8s** | 4.8 局/s；开跑时 3/6 节点失联，a95/a97 于 18:48:55 重探成功后立即投入 |
| 新（收工只等写行，18:58） | **174.0s** | `settled 满` → `DONE` **同秒**；`dup=20` |
| 新（与门禁并发，19:00） | **202.8s** | 本机槽位争用下的保守值 |
逐局对拍（对旧形态产物，三次）**0 差异**（800/800 行、passed 154/800、kills 7144）——本改动是纯调度，不改语义。

**配置旋钮（`policy` 段，均有缺省）**：`recoverPingSec`（20）· `nodeRecoveryTries`（3）· `noConsumerGraceSec`（180，
**只在整批从未有过任何消费者**时生效：无节点就绪过 + 本机槽位 0 ⇒ 有界响亮收摊而非等满窗）。

**已知局限（如实记录）**：阻塞在 `urlopen`（响应头都还没回来）的慢节点连接不在注册表里 ⇒ 关不到；那部分只能等
其自身超时（`taskTimeoutSec`）。`all_done` 已置位 + 新请求拒发 ⇒ 调用方不再依赖它们的结果。日志里的
「断连 0 条在飞连接」正是这个含义（此刻没有处于读体阶段的响应），不是没做事。

**守卫（`nn-training/tests/`）**：`test_batch_eval.py` +5（门判据纯函数 / 就绪节点只 ping 1 次 + 传权重 1 次 /
失联重探后可用 / 快节点不等慢节点 bring-up / settled 满即断连且不算节点故障）+ `test_dist_common_poll.py` +1
（`abort_active_requests` 不阻塞、置位后新请求按瞬停分类、别的作用域不受影响）+ `test_eval_course_once.py` +1
（每权重单单元跨关 + 多权重元数据分键）。python 门禁 1324 例全绿。
## §2026-09-19-node-fault-taxonomy（2026-09-19，A/B/C 三层共用瞬断判据 + 409 wver-not-cached 自愈：把「节点故障」与「可刷新条件/背压」分开）

**触发**：用户 2026-09-19 审计后指名修 A1+A2+A3（训练侧 rollout/eval 与一次性评估同源的三个洞）。
证据链（`tmp/x20-rebirth/training-loop.log`，08:00–10:36，235 个 rollout 轮 / 20 个 eval 轮）：

- **A1**：09:48:29 `weights[rollout] reuse wver=1a01aa045436… skip POST for ['self','mac','a97','gcs']`
  → 5 条 `HTTP 409 {"error":"wver not cached here"}` → `node a97: 3 consecutive failures —
  circuit-broken for this round`；该轮 `byNode={"self":11,"mac":8}`，**a97 的 7 个槽位整轮闲置**。
  而同一 wver 在 44 秒前（09:47:45）刚在 a97 上 POST 成功（`(purged)`）。
- **A3**：09:48:13 三条 `HTTP 502:`（cloudflared 隧道，非节点问题）同样记 streak → a97 熔断；
  实测分布：rollout 617×503（旧实现唯一豁免项）+ 9×502 + 5×409 + 1×10054；eval 层 153×503 +
  1×502 + 1×10054，**旧实现对这 155 次全部当节点故障**（3 次即熔断该节点整轮）。

**实施**：

1. **判据单源**：`dist_common.is_transient_error(e)`（408/425/429/500/502/503/504、`DistError.transient`、
   文案含 busy、非 DistError 的 OSError/TimeoutError）成为唯一实现；`rl/batch_eval.is_transient_error`
   退化为薄转发（保留名以兼容既有引用与单测）。A 层（`rl/dispatch.py`）与 C 层（`rl/eval_dispatch.py`）
   直调同一实现——旧实现只有 B 层有判据、A 层只豁免 503、C 层什么都不豁免。
2. **409 = 可刷新条件**（`dist_common.refresh_weights`，A/C 两层共用）：失败时**清进程内 reuse 缓存**
   （`_WEIGHTS_PUSHED` 原先只在 ping/codeHash 门失效 ⇒ 脏缓存让 409 持续到熔断）+ **就地重发**该 wver；
   成功则不入 streak、不耗 attempt 配额，同一节点继续用。重发也失败才按真失败记。
   （节点侧根因是 per-kind 桶只留 KEEP=4 份且**所有客户端共享**——别的训练作业/本机 eval 上传/agent
   重启都能把文件挤掉，而客户端看不出来；修节点侧要动 `tools/agent/sampler-agent.ts`（codeHash SSOT
   ⇒ 全集群 stale + 需 push），故本轮只做客户端自保。）
3. **瞬断不计节点故障，但有上界**：新增 `soft_streaks`（`policy.nodeSoftFailStreak`，缺省 3×`nodeFailStreak`）。
   单次瞬时错误不熔断；**连续**软失败达到上界则停派该节点并单独措辞记日志（`连续 N 次瞬时失败（背压/瞬断，
   非节点故障）— 本轮停派`），与真故障的 `circuit-broken` 区分。理由：不给上界的话，隧道/集群整体脉停时
   会无限重排把整轮拖到窗口超时（旧行为是快速熔断，也有害，但至少不空转）。
4. **可观测**：背压/瞬断重排日志带上「瞬断/背压（不计节点故障）」；409 自愈单独一条
   （`wver not cached（409）—— 已就地重发权重（…）并清 reuse 缓存`）。

**被否方案**：① 只在 A 层补 503 之外的豁免（不共用判据）——B/C 层仍在误熔断，且三处判据必然漂移；
② 把 409 也算 transient 一扔了事——409 是**可修**的（重发即恢复），扔进背压队列会让同一节点持续 409、
   任务被重排到窗口耗尽，还丢掉「节点侧到底有没有那份权重」这个信号；③ 瞬时错误完全不设上界——
   整体脉停时整轮空转到 deadline（见 3）；④ 让 rollout 也改用一次性评估的专用 kind 隔离权重——
   rollout 的权重就是节点采样要用的那份，无法隔离，只能保证丢了能立刻补。

**实测（单测，非仅源码断言）**：A 层新 `tests/test_rollout_dispatch_resilience.py`（7 例）——502×3 后
4/4 局全结算（`dist.nodes={"a97":4}`、retried=3、无 `circuit-broken`）／真故障连续 3 次即停派（只取活 3 次）／
409 触发一次就地重发且节点继续跑完整轮／软失败上界停派；C 层新 `tests/test_eval_dispatch_resilience.py`（8 例）
——502×3 后 4/4 结算、409 自愈、真故障仍熔断；`test_dist_common_poll.py` +4（分类表、刷新语义与缓存清空、
单源守卫：全仓只有 dist_common 一份实现、B 层必须是纯转发、A/C 层必须接线 409 分支）。
**两套行为测试都在旧代码上实测变红**（A 层：`missing=[全部 4 局]`；C 层：0/4 结算、`refreshed==[]`），
不是事后补的绿灯。nn-python-gate 1343 例全绿。

**已知局限**：客户端无法阻止别的客户端（或 agent 重启）挤掉权重，只能事后补；`soft_streak` 上界是启发式
（3×真故障阈值），不是测量结果；409 重发是整份权重上传（MB 级），高频 409 会吃带宽（实测一次/轮量级，可接受）。
## §2026-09-19-rollout-midround-recover（2026-09-19，A 层 rollout 中途重探真正跑起来 + 轮内回场：`rescan_nodes` 从「每轮 0 次 pass」到 5s 首探/20s 周期 + 已停派节点回场）

**触发**：用户 2026-09-19 指名修 A4（审计发现）。**证据（两条独立线索都指向 0 次执行）**：
① `tmp/x20-rebirth/training-loop.log` 2.5h / **235 个 rollout 轮**里 `rescan` 日志 **0 行**，而同期
节点排除 130+ 次（`ping failed`：a96 61 / a97 43 / a95 26）；② 机制上必然如此——旧实现
`sleep_sec = min(rescan_sec=120, …)` → `all_settled.wait(sleep_sec)` → 紧接
`if all_settled.is_set(): return`，而该 run **234 轮全部 <120s**（p50 7s，max 115s）⇒ 线程每轮都在
首个 sleep 里被结算事件唤醒并退出；且候选集用 `if nid in spawned_ids: continue` 过滤，**已熔断的
节点永远不是候选**（熔断即整轮出局，窗口 1800s）。

**实施（`rl/queue_local.rescan_nodes` + `rl/dispatch` 调用点）**：

- **首个 pass 提前**：`nodeRecoverFirstSec`（缺省 5s）后首探，之后每 `recoverPingSec`（缺省 20s，
  与 B/C 层同口径；旧的 `agentRescanSec` 仍可覆盖、显式 0 = 关闭）。防忙等地板从 0.5s 降到 0.05s
  ——0.5s 地板会让小值旋钮（测试/调频）名不副实。
- **候选 = 尚未孵化 ∪ 已停派**（`streaks ≥ nodeFailStreak` 或 `soft_streaks ≥ nodeSoftFailStreak`）；
  后者是新增的「回场」路径：清 reuse 缓存 → 强制重握手（`post_weights` 自带 cached 探针）→
  **重置失败计数** → 补孵 c_n 个采样线程。每节点每轮 `nodeRearmLimit`（缺省 3）次上界，
  用尽后告警一次并停手（防「永远失败的节点」无限起线程）。
- **ping 并行**（`dist_common.ping_nodes_parallel`，与 B/C 层同款）：串行 3s/台会让一个 pass 卡十几秒。
- **两个旧 bug 顺带修**：① 漏传 `kind=wkind` ⇒ goal/intent 腿的中途上线节点把权重发进 rollout 桶，
  任务全 409（与 §2026-09-19-node-fault-taxonomy 的 A1 同一类陷阱）；② `nd` 漏带 `ping`
  ⇒ stageJson 任务在回场/中途上线节点上被能力握手拒掉（自定义关课程下等于白孵这些节点）。
- **调用点全关键字传参**：该调用有 20+ 实参，历史上第 19 个位置参数错位过一次（线程启动即抛、
  运行中上线的节点永不被发现）——位置传参是这类 bug 的温床。

**被否方案**：① 只把 `rescan_sec` 缺省从 120 改小——线程仍会在结算瞬间退出，「短波轮里永不扫描」
不变；② 用 `time.sleep` 代替 `all_settled.wait`（保证 pass 跑到）——会让 round done 滞后一个
cadence（2026-09-05 修过的老毛病复活）；③ 回场不做上界——节点持续失败时会无限补孵线程；
④ 回场沿用进程内权重缓存（跳过 POST）——节点刚重启/桶被挤时缓存是脏的，正是 A1/A2 的教训。

**契约（测试钉住，11 例）**：`tests/test_rollout_dispatch_resilience.py`
中途上线节点在轮内供样（旧实现 `{'self': 6}`，新实现 a97 拿到 ≥2 局）／熔断后轮内回场把剩余任务跑完
（旧实现 6 局全 missing，新实现 6/6）／真失败「熔断 + 有界回场」= 3×(`nodeFailStreak`)×(1+`nodeRearmLimit`)
= 12 次取活、回场日志恰好 3 条／`nodeRearmLimit=1` 时上界告警且共 6 次取活／halt 置位后 3s 内收工
（不等满 30s 窗口）。**A4 三条行为测试在旧代码上实测变红**。nn-python-gate ✓（1347 例）。

**已知局限**：回场会暂时叠加线程（旧线程可能还在收尾一局，新线程又孵），故为「有界多孵」而非
精确替换——自愈性影响可忽略（同一节点、多余槽位在下一轮自然收敛）；节点侧根因（per-kind 桶
KEEP=4 且全客户端共享）仍未修，回场只是客户端侧的自保（见 §2026-09-19-node-fault-taxonomy）。
## §2026-09-19-eval-gate-lanes（2026-09-19，C 层（训练干净评估）的门与收工形态：并行 ping + 逐条留痕 + POST 全败走本地 + 本机槽位先开工 + settled 满即断连）

**触发**：用户 2026-09-19 连续两条指令（「把 C 层收工空等 4–76s/轮榨掉：all_done 即断连 + 只等写行的赢家」、
「把 eval 层的病态分支与串行门一起修」），底稿是同日的 C 层审计（`tmp/x20-rebirth/training-loop.log`，
08:00–10:36 共 20 个 eval 轮）。

**证据（五个缺陷，均为一手日志）**：① **B1 收工空等**：末局结算 → `DONE` 的墙钟 = it10 42s / it15 47s /
it40 32s / it80 76s，而这些行在 `all_done` 置位前就已全部落盘——旧收工是
`t_.join(timeout=max(30, window + task_timeout))`，卡在 HTTP 里的线程要等请求自己结束；② **B2 门串行**：
逐节点 `node_ping(timeout=3s)`，墙钟 = Σ 每台延迟（两台超时即 ~7s），而门每轮重跑一次；③ **B4 静默丢节点**：
`if ping is None: continue` 零日志——节点被丢时既看不出是谁、也看不出为什么（`ping failed` 计数只能靠
别的账本反推）；④ **B5 病态分支**：`if not nodes_ok` 时无条件 `return`，本机槽位明明可用却整轮 0 局
（且旧写法的 `if not alive and …` 条件自相矛盾）；⑤ **B3 门即屏障**：权重 POST 全部返回后才孵化线程，
本机槽位干等（本地权重就是本机冻结快照，根本没有下发开销）。

**实施（`rl/eval_dispatch.py`；常量 `EVAL_INFLIGHT_GRACE_SEC` 落 `rl/eval_local.py`）**：

- **B1**：收口**显式**成三段——① 等 `all_done`（或墙钟 `deadline`，或消费线程全退）；② 未满时给在飞局一个
  **有界**落账窗（`min(task_timeout, EVAL_INFLIGHT_GRACE_SEC=120)`，在飞清空即走）；③ 之后
  `abort_active_requests(req_scope)` 断连 + 拒发新请求，只等**正在写行的赢家**（`writers` 计数，1s 兜底）
  并把线程 join 预算压到 0.25s。`writers` 在 `seen.add` 时 +1、`record()` 落盘后 -1。
- **B2**：门改用 `dist_common.ping_nodes_parallel`（保序，== 最慢一台）。
- **B4**：`ping is None` 逐条 `[eval] node <id>: ping 失败/超时（并行探测，预算 Ns） — 本轮不参与`。
- **B5**：`alive` 非空但 POST 全败 ⇒ 本机可用则 **local-only**（响亮记一行），不可用才跳过；
  `alive` 为空时不再重复打「POST 失败」误导行。门/权重段整体移到闭包之后（B3 的前提）。
- **B3**：本机槽位在**门之前**孵化并启动（`_spawn_tracked`），门/权重只影响节点侧。
- **顺带（收工的护栏）**：`live_workers` 计数（孵化即 +1、线程退出 -1）——「任务全被 drop 且无在飞」时
  旧形态只能空等整个窗口（60/1500s）；现在全退即收工，并在日志里注明「消费线程已全退」。

**被否方案**：① 只加一个 `all_done.wait(…)` 超时——收工只是变慢而非**立即**，且窗口到期时仍会把在飞的
有效局一起砍掉；② 用 `join(timeout=2s)` 代替断连——线程仍卡在 socket 读上（进程内连接数按节点并发累积）；
③ 门/权重段整体前移到快照之前——`nodes_ok` 必须先于 `streaks`/日志，前移等于把闭包拆散；改为「本机先开工 +
门后移」；④ 本机槽位在门失败时也照旧 `return`（旧行为）——用户点名要「POST 全败走本地」。

**契约（测试钉住，新增 7 例）**：`tests/test_eval_dispatch_resilience.py`
并行门（3 台 × 0.3s 实测 <0.7s；串行基线实测 0.906s）／ping 失败逐条留痕（含节点 id）／
POST 全败 + 本机可用 ⇒ 2/2 局全由 `local` 结算且日志为 `— local-only eval this round`／
本机也不可用时仍响亮跳过／本机首局早于权重门完成（`post_delay=1.0s`）／
settled 满（4/4）不等慢节点（慢节点 fetch 睡 3s，整轮实测 <2s）／窗口到期不砍在飞局（有界宽限）。
**其中 5 例（B1/B2/B3/B4/B5）在旧代码上实测变红**（`git show HEAD:…` 换回旧实现跑同一套测试：5 failed /
10 passed；旧实现必须在 `--maxfail=99` 下一次拿全，因为 pyproject 的 addopts 带 `-x`）。

**已知局限**：① `clear_abort()` 是**全局**清账（同进程并发的另一轮 eval/baseline 的收工态会被清）——
与 B 层 `batch_eval` 同款语义，重叠窗口只在 baseline 与 A-eval 同迭代并发时出现，后果是少量 dup 回包
（丢弃、不双计）；② 阻塞在 `urlopen`（响应头都未回）的连接不在断连注册表里，只能等其自身超时
（`all_done` 已置位，结果本就被丢弃）；③ 本机槽位在门失败时立即开闸（`release_local_gate_if_starved`）
⇒ 本机局可能与训练主循环的资源窗重叠（与「无可用节点」分支同语义）。
## §2026-09-19-eval-weights-kind（2026-09-19，训练干净评估的权重 kind 独立成 'eval'——不再与训练 rollout 共用节点权重桶）

**触发**：用户 2026-09-19（「给训练评估独立 kind（我定名 eval），顺带一次现场验证」）。来源是同日审计的
**B6** 项：`rl/eval_dispatch.py` 的权重下发与局请求都走 `kind="rollout"`，与训练 rollout 的 churn 共用节点
同一个权重桶。

**根因（节点侧分桶语义，已核实代码）**：节点按 `(kind, sha)` 分桶缓存（`sampler-agent.ts::weightsByKindSha`）：
内存桶**每 kind 上限 64**（`WEIGHT_BUCKETS_PER_KIND`）、落盘文件**每 kind 保留最新 4 份**
（`workdir-cleanup.ts::WEIGHT_FILES_KEEP`，在飞桶引用的文件豁免），而 `/v1/task` **只查内存桶**
（`weightsOf`，不回查磁盘）。⇒ eval 与训练 rollout 同 kind 时，eval 那份与训练每轮的 churn 共用同一组
计数（64 / 4），任一侧轮换都可能把对方挤掉；被挤掉后节点答 409「wver not cached here」，客户端只能靠
409 自愈重发兜（A1）。

**决定**：`EVAL_WEIGHTS_KIND = "eval"`（`rl/eval_dispatch.py` 单源常量），**三处同源**——权重 POST 的
`x-kind`、局请求的 `?kind=`、409 自愈重发的 `kind`。协议侧 kind 是**不透明字符串** ⇒ 旧节点无需任何改动
（现场实测 5/5 台未升级节点直接接受并正常出局），也不触 codeHash（`nn-training/**` 不在 SSOT 内）。

**配套（同一坑的另一面）**：进程内下发账本 `dist_common._WEIGHTS_PUSHED` 由**键 = wver** 改为
**键 = (kind, wver)**（`note_weights_pushed` / `weights_already_pushed` / `partition_weights_nodes` /
`refresh_weights` 同改；缺省 `kind="rollout"` ⇒ 既有调用方行为逐字不变）。理由：同一个权重文件（同一 sha）
会被两条腿使用（干净评估评的就是刚训练出的那份 θ）——账本不带 kind 时，先跑那条腿的 note 会让另一条腿
被判成 reuse 而**跳过 POST** ⇒ 该节点对另一条腿整轮 409（脏缓存，与 A1 同类陷阱、方向相反）。
A 层调用点同步接线：`rl/dispatch.py`（`kind=wkind`）、`rl/queue_local.py`（`kind=wkind`）。

**顺带修（现场探针实测踩到）**：`dist_common.ping_nodes_parallel` 只读 `authKey`，而 `post_weights_parallel`
两种都认（`key` / `authKey`）⇒ 把归一化配置（`{id,url,key}`，eval_dispatch / batch_eval 用的形态）喂进来会
静默 401、整批节点判「ping 失败」（探针第一版 6/6 台全灭，第二版才对）。已统一键名兼容。

**现场验证（真实集群 2026-09-19 21:19，`tmp/b6probe/run2.log`）**：x20-rebirth `it96`（sha `232158d9…`）
× 6 台 enabled 节点 —— ① 并行门 6 台 2.58s（gcs 超时，其余 5 台 evalSupport / stageJsonSupport /
bun 1.4.2 / codeHash 全过）；② `kind='eval'` 下发 **5/5 台 ok，0.20s**（self `purged`、其余 `kept`）；
③ 桶隔离实测 `eval=True / rollout=False`（self / mac / a95）——两条腿的桶确实分开；④ 以 `kind='eval'`
取一局：**HTTP 200，1.2s**，`wver=232158d9…` 对账一致。

**被否方案**：① 继续共用 'rollout'、只靠 409 自愈兜——把可预防的故障做成常态，还掩盖节点侧真实丢失；
② 用新 **mode** 而非 kind 区分——节点早已按 kind 分桶（v3.7），新增 mode 要动 `sampler-agent.ts`（在
codeHash SSOT 内 ⇒ 需 push + 集群重启），而 kind 是现成的**零升级**通道；③ 账本保持 wver 单键、只在 eval
侧「发前 forget 节点」——治不了另半边（rollout 腿复用 eval 的账）；④ 一次性工具链
（`tools/sim/eval-course-ckpt.ts` / `rl/batch_eval.py`，kind 走 `'rollout'`/`'none'`）**本轮不动**：
它是独立命名空间（iterId 自带 `evalcourse-`），且迭代节奏与训练循环无关。

**契约（测试钉住，新增 3 例 + 补 1 例断言）**：`tests/test_dist_weights.py::test_push_cache_is_keyed_by_kind`
（同 sha 的 eval 不得被 rollout 的账判成 reuse；缺省 kind 行为不变；`forget_weights_node` 两条腿一起清）／
`tests/test_eval_dispatch_resilience.py::test_eval_leg_uses_its_own_weights_kind`（POST 与请求同 kind）＋
`test_wver_409_reposts_weights_and_keeps_node` 补断言（重发也走 `EVAL_WEIGHTS_KIND`）＋
`test_eval_dispatch_kind_is_single_sourced`（源码守卫：该文件不得残留 `kind="rollout"` 字面量）／
`tests/test_dist_common_poll.py::test_ping_nodes_parallel_accepts_key_and_authkey`。
**其中 3 例在旧实现上实测变红**（`git show HEAD:` 换回旧实现跑同一套：`AttributeError: module
'rl.eval_dispatch' has no attribute 'EVAL_WEIGHTS_KIND'` ×2 + 键参数 `TypeError` ×1）。

**已知局限（未修，如实记）**：① 本改动只消除「两条腿互相驱逐」；**节点重启清空内存桶**后该 kind 仍会 409
（`weightsOf` 不回查磁盘）——那是节点侧根因，要动 `sampler-agent.ts`（codeHash SSOT ⇒ 需 push + 集群重启）；
② 节点上会多出一份 kind 目录（`weights-eval-*`，每 kind 4 份 ≈ 1.5MB/台）——换来两条腿的桶与日志都可分；
③ `rl/bc_eval.py` 仍是 `kind="rollout"`（BC 每 epoch 评估）——同类站点，但属另一条腿、且改动会连带其
请求侧，未并入本轮。
## §2026-09-19-node-weights-disk-fallback（2026-09-19，节点侧权重查找在内存桶未命中时回查磁盘——根治「agent 重启后对盘上已有的权重答 409」）

**触发**：用户 2026-09-19「让节点侧的权重查找在内存桶未命中时回查磁盘，根治重启后的 409」。承接
`§2026-09-19-eval-weights-kind` 的已知局限 ①（kind 拆桶只消除**两条腿互相驱逐**，重启仍会 409）。

**根因（已核实代码）**：`/v1/task` 只查内存桶（`sampler-agent.ts::weightsOf` → `weightsByKindSha`）。
agent 一重启，内存桶就空了，而权重文件**仍在 `WORK_DIR`**（boot 收敛只按 kind 删到最新 `KEEP=4` 份）
⇒ 节点对**盘上就有的**权重答 409「wver not cached here」，客户端只能靠 409 自愈重发兜（每节点每次重启
白传一份；在 A1 之前还会把该节点**当故障熔断整轮**）。

**决定（`tools/agent/sampler-agent.ts`）**：
- `weightsOf(kind, wver)`：内存命中 → 原路；未命中 → **磁盘回查** `readWeightsFile`，命中即**回填桶**
  （后续任务零额外开销）并按 `evictWeightBucket` 做同规驱逐。
- `readWeightsFile`：文件名只有 **16 hex 前缀**，**不足以判定内容** ⇒ 回查时**按字节重算 sha256 全量
  比对**，不符即视为未命中（坏/被截断的文件留给 sweep 收拾）。`iterId` 只在 POST 时记账（无消费方），
  回查来的置空。
- `weightFileBase(kind, sha)` 提为**单一来源**：POST 落盘、磁盘回查、retention 正则三处必须同名
  （名字一旦漂移，回查文件会被 boot/切换时的清扫当垃圾删掉）。`weightsKeyOk` 守门（kind 直接进文件名 ⇒
  防路径穿越；sha 必须全量 64 hex）。
- `/v1/weights/cached` 探针同样**磁盘感知**（`weightsCachedInBucket(...) || weightsOf(...) !== null`）：
  否则重启后探针答 false，客户端会重传一份**盘上已有**的权重。
- `latestWeightsOfKind(kind)`（intent/goal 评估的「最新桶」语义：`policy=intent-exec|goal` 的 409 前置检查 +
  runGame 的 `--intent-weights/--goal-weights`）：内存桶按插入序取最后一个；**桶空则回查磁盘**取 mtime 最新的
  一份（逐候选按字节重算 sha256，并要求 sha 前 16 hex 与文件名一致，改名/损坏的跳过；命中即回填桶）。
  这是另一类重启 409：intent-exec / goal 评估会整轮答「intent weights not cached」。

**代价与影响（必须知道）**：本文件**在 codeHash SSOT 内**（`tools/agent/codehash-files.txt`）⇒
codeHash `c78d48de…` → **`355f0738…`**，**必须 push + 节点升级/重启才生效**。升级窗口内未升级节点会被
门排除（可用节点数变少，属预期，不会污染数据）。`freeze:check` 不受影响（不触 God-AI/仿真签名）。

**被否方案**：① 回查只信文件名（16 hex 前缀）——前缀碰撞/坏文件会被放行，故障从「409」变成「局跑错
权重」，更糟；② boot 时把盘上全部权重文件预载进内存——要逐文件哈希、启动变慢，且把「最新 KEEP 份」
当权威；懒惰按需回查更小、语义相同；③ 客户端侧继续只靠 409 自愈——把可预防的故障做成常态，重启后每
节点每轮白传一份；④ 探针直接答 `true` 不校验内容——把「缓存」变成谎言。

**契约（测试钉住，`tests/dist-agent.test.ts` 新增 4 例）**：`readWeightsFile` 内容不符 / 前缀碰撞 /
缺文件 / 路径穿越与短 sha 一律未命中；`weightsOf` 未命中回查磁盘、命中后**文件消失也仍命中**（证明回填）；
`weightFileBase` 与 retention 正则 `WEIGHT_RE` 同域（5 个 kind 全覆盖）；`latestWeightsOfKind` 桶空时取盘上
mtime 最新的一份（不串 kind）、**最新那份是改名坏文件时跳过它取旧的**、回填后文件删掉仍返回。
旧实现上该文件 **1 fail + 1 error**（`Export named 'readWeightsFile' not found`）；变异体（删掉内容校验那行
`sha.slice(0, 16) !== c.prefix`）**只让「坏文件跳过」那条断言变红**——证明它守住的正是「文件名 16 hex 后缀
不可信」这个性质，而不是顺手写绿的。

**现场实测 A/B（`self` 节点，同一权重 `it96` sha `232158d9…`，同一探针 `tmp/restart-probe.py`；
证据 `tmp/restart-probe/{213330-before,213405-after}.json` + 两份 .log）**：
- **BEFORE（HEAD 代码，codeHash `c78d48de…`）**：① 重启前取局 seed900 → **200**；② `/v1/restart` accepted
  → 新进程 uptime=0s；③ **不重传** → 探针 `eval=False`、取局 seed901 → **409 `{"error":"wver not cached
  here"}`** ← 缺陷在真实节点上复现（而 `weights-eval-232158d94975e6a5.json` 379KB 全程躺在盘上）。
- **AFTER（本次代码，codeHash `355f0738…`）**：重启（等过 30s grace 窗口后发出，`waited=2.0s`）→ 新进程
  codeHash=`355f0738…`；**不重传** → 探针 `eval=True`（探针也已磁盘感知）、取局 seed951 → **200**
  （`outcome=gameover ticks=1483`）；节点日志出现
  `weights[eval] rehydrated 232158d94975… from disk (in-memory bucket was empty — agent restarted)`。
- **注意**：本次 A/B 跑在 `self`（本机 agent 直接执行工作区 TS）⇒ 验证的是**代码路径**；远端 5 台仍是
  `c78d48de…`，需 push + 升级后才具备同样行为（本地工作区 hash 已是 `355f0738…`，未升级节点会被门判 stale）。

**已知局限**：① 盘上只留最近 `KEEP=4` 份/kind（更早的被 sweep 删）⇒ 更早的 sha 仍 409（客户端重传，
这是正确行为）；② `latestWeightsOfKind`（intent/goal 评估的「最新桶」语义）仍只查内存——同类站点，未并入
②（**已并入本轮**）`latestWeightsOfKind` 同样磁盘回查，但它的「最新」只能按 **mtime 近似**（POST 命中 kept 不重写文件时 mtime 偏旧；与内存桶插入序语义等价、但有此边界），且「最新」是**跨客户端共享**的语义（别的训练流 POST 的 intent/goal 权重也会成为最新——与改动前一致）；③ 回查是请求路径上的同步 IO（`readFileSync` + sha256）——每个 `(kind, sha)` 每进程只付一次
（命中即回填），首次命中的那一局多几毫秒；④ 未做重启预载，故「重启后第一局」付出这次哈希。
## §2026-09-19-node-memory-restart-blindspots（2026-09-19，依赖节点内存态的三处重启盲区：取包丢失 404 判据 / `/v1/update` 假收敛 / 升级去重冷却窗）

**触发**：用户 2026-09-19「系统排查其它依赖节点内存态的地方（结果缓存、inflight 表）是否也有重启后行为
盲区」，随后裁定按 F1 → F3 → F2 修。

**审计（只读，判据 = 进程内可变状态重启后消失，而**是否有人据此做过判断**）**：
节点侧逐个过：`weightsByKindSha`（已覆盖，见 §node-weights-disk-fallback）/ `resultCache` / `inflight` /
`failedTasks`（**F1**）/ 计数器（`gamesDoneTotal/ByIter`、`cacheHits/Evicted`、`rejectedCount`、`activeWorkers`、
`lastError`）→ 只喂 `/v1/status`，面板的贡献度算的是**客户端写的** `dist-agent-meta.jsonl`，live 计数只显示、
无跨轮 delta 运算 ⇒ **无盲区**；`persistPool` 子进程 → 父进程退出即关掉它们的 stdin 管道 ⇒
`export-rl-rollout.ts` 的 `stdin.on('end') → process.exit(0)` 自灭，boot 的 `sweepWorkdir()` 另清孤儿
`game-*`/陈旧 pid；`gameSeq` → 目录名带 pid，重启不碰撞；`codeHashMemo/gitShortMemo` → **F2**；
`persistFailStreak`/`updating`/`restartPending` → 更宽松的闩，无消费方。
客户端跨轮、按节点为键的内存态：`_WEIGHTS_PUSHED`（已由 409 自愈 + 节点磁盘回查覆盖）/
`_RESTART_SEEN` + TS 落盘 memo（**F3**）/ `_ACTIVE`·`_ABORTED_TAGS`（进程内、按轮）/ `DEDUP_STREAK`（日志告警
计数）。`/v1/ping` **没有**权重字段 ⇒ 客户端从不信节点内存来判权重。

**F1（客户端，`dist_common` + A/C 层）——取包丢失 404 不许当节点故障**：
`resultCache/failedTasks/inflight` 都是节点进程内状态，agent 一重启即空；轮询 `/v1/result` 得到 404
（文案明写 `expired/purged/restart`）。旧实现在 A 层按确定性失败记 streak（3 条即 `circuit-broken for this
round`）并在 C 层按 attempt 打光即 `dropped`（**丢局**）——与 §node-fault-taxonomy 里 409 的错误同族、方向相反。
- 新增 `dist_common.TASK_LOST_MARKER` + `is_task_lost_error(e)`：判据 = **状态 404 ∧ 文案带标记**（裸 404 =
  路径写错等客户端 bug，必须继续响亮失败，绝不静默成无限回队）。与 `is_transient_error` **刻意分开**
  （处置不同：一个回队重跑、一个背压退避），但两层都按「不计节点故障、不耗 attempt 配额」处理。
- `forget_weights_node(nid, kind=None) -> int`：摘某节点账本（缺省全 kind；给 kind 只摘那条腿）。404 ⇒ 立刻
  摘该节点这条腿的 reuse 账本——重启同时也意味着它的权重桶可能空了（升级后的节点会靠磁盘回查零重传）。
- 可达性（精确）：async 只在 `abandon_event`（仅 fanout 竞速副本；副本失败有独立分支静默丢弃）或
  `DIST_TASK_ASYNC=1` 运维模式；面板 `smoke.ts` 把 404 当「继续轮询」⇒ 重启节点表现为 30s 朦胧超时（诊断噪声）。

**F2（节点侧，`tools/agent/sampler-agent.ts`）——`/v1/update` 不得让节点「报新代码、跑旧代码」**：
旧实现在 pull 成功后 `codeHashMemo.value = null` / `gitShortMemo.value = null`，而 `/v1/ping` 报的正是
`memoizedCodeHash()` ⇒ pull 过但**没重启**的节点会以**新 hash** 通过 codeHash 门
（`dist_common.check_code_hash`）、静默跑启动时那份代码——正是该门要拦的东西的反向漏网。
- 决定：memo 的**生命周期 = 本进程**，永不因 pull 失效；pull 只留一行响亮日志（新代码重启后生效）。抽
  `applyPullResult(r)`（导出，供单测钉住不变量），HTTP 分支只调它。要换 hash 只有 `/v1/restart`（可带 pullBranch）。
- 顺带：`/v1/restart` 分支在 `process.exit(0)` 前显式 `killPersistPool()`——池靠 stdin EOF 自灭，但**忙** worker
  要跑完当前那局才回到事件循环 ⇒ 旧代码会顶着旧代码继续算一段、把没人消费的 `game-*` 留在盘上。

**F3（`dist_common` + CLI/TS memo）——升级去重从「永久」改成「冷却窗」**：
旧 `_RESTART_SEEN` 命中即永久 dedup；pull 失败 / 环境不支持远端升级的节点带着**同一个** codeHash 回来 ⇒
该节点再也收不到升级指令（训练循环里直到训练机有新提交；TS 工具那条腿还把 memo 落盘
`tmp/node-upgrade-memo.json`，跨调用继续压制）。
- `RESTART_DEDUP_COOLDOWN_SEC = 600`（env `NN_RESTART_DEDUP_COOLDOWN_S` 可覆盖，0 = 关闭去重）；
  `_RESTART_SEEN[nid] = (pingHash, expectedHash, at)`；窗内 dedup、**窗过期 ⇒ 允许再发一次并重置时钟**
  （防连环杀 §2026-09-01 不破）。
- memo 的时刻要**进判据**：`seed_restart_state` 接受可选 `atSec`（缺省 = 现在 ⇒ 旧调用方语义逐字不变）；
  CLI spec 新增可选 `cooldown_sec` / `seen[].atSec`；TS 侧 `latestSeenEntries` 把 memo 值（ISO 时刻）换算成
  `atSec` 一并送过去（**判据仍只在 dist_common**，TS 只送时刻）。`memoAtSec` 先判纯数字再试 ISO——实测
  `Date.parse('1758300000')` 会给一个毫不相干的日期（2001-05-01），静默把新鲜 memo 变成「一小时前」。
- A 层 dedup 的 WARN 文案补上冷却窗秒数（运维知道它会自愈，不必手删 memo）。

**证据**：
- F1 两例行为测试在旧层上实测变红：A 层 `circuit-broken for this round`；C 层 `dropped=2 / games=0`；新实现
  A 层 `byNode={"a97":2}`、C 层 2/2 结算，且 `forget_weights_node` 被调用、`refreshed == []`（404 不走 409 路径）。
- F3 行为 A/B（`seed` 一条 1 小时前的 memo）：旧 `(False, 'dedup')` → 新 `(True, 'restart-requested')`。
- F2 守卫 A/B（HEAD vs 工作区）：`无 memo 置空 false→true`、`restart 前收池 false→true`、`导出 applyPullResult false→true`。
- 门禁：`nn-python-gate` ✓（ruff/mypy + pytest）· 根 `bun run check` ✓ · `bun run build` ✓ · `freeze:check` ✓。

**代价与影响**：`tools/agent/sampler-agent.ts` **在 codeHash SSOT 内** ⇒ F2 需 **push + 节点升级/重启**才生效；
F1/F3 是 `nn-training/**`（不在 SSOT）⇒ 无需 push。另：F2 改动期间工作区对 SSOT 变脏 ⇒ 从本仓跑工具会把远端
判 stale/拒发升级（既有护栏语义），提交后消失；两个 scan 用例顺手钉死「与工作区脏不脏无关」
（`dirty_hash_files → []`），否则改 `tools/agent/**` 就会把它们弄红（实测）。

**被否方案**：① F1 把 404 并入 `is_transient_error`——措辞与处置都不同，且会让「裸 404 客户端 bug」也变
静默回队；② F1 只豁免 streak 但照旧耗 attempt——C 层 attempt 打光即 `dropped`，等于照旧丢局；③ F2 让
`/v1/update` 顺带自重启——把「拉代码」这个可逆操作变成不可逆的杀进程，且 `test-dist-ops --pull` 的语义会变；
④ F3 把去重彻底删掉——2026-09-01 重启循环事故会回来；⑤ F3 只在 TS 侧按 memo 时间过滤——判据会一分为二。

**已知局限**：① F3 冷却窗是**时间**判据，窗内仍不重发（连续 3 轮 dedup 的 WARN 已在 A 层，TS 工具那条腿只有
日志行 + 面板 `versionOk=false`）；② F1 只覆盖 async 取包路径（同步路径没有 `/v1/result`，重启表现为连接被
重置 = 瞬断，已豁免）；③ F2 不做运行中进程的自我重启（要重启请 `/v1/restart`，这条刻意保留人工/协调器触发）。

---

## §2026-09-20-dashboard-shell-routing（2026-09-20，训练控制台信息架构重设计 P0：应用外壳 + 路由化详情页；**路由实现 = 单 bundle + 服务端路由 SSR + pushState**）

**背景**：控制台是单列纵向堆叠的 12 个平权面板（无分区/无导航），四类详情（指标/传输/节点统计/日志）
住在全屏模态抽屉里（**没有 URL**：看不到也分享不了「我在看这个视图」），课程选择器（整页主语）被
`flex: 1` 挤在顶栏正中间与状态 chip 抢权重，底部还有一段 299 字的说明文字占了主版面最长的文本块。
全部问题按编号列在 `docs/dashboard-redesign.md` §1.2（C1–C13），目标形态在 §3。

**决定（P0 已落地）**：
1. **外壳**：`Sidebar(216px) + Topbar(56px) + 主内容`（`web/app/shell/{Shell,Sidebar,Topbar}.tsx`）。
   课程选择器 + 触发门禁 + 刷新间隔一并移入**侧栏底部**——课程是整页主语，位置固定且全页唯一；
   顶栏只剩「本页（标题 + 这一页回答什么）」与「全局状态（阶段/在训/节点/连接 + ⟳ + 更新时间）」。
2. **详情路由化**：`/metrics` · `/nodes` · `/wire` 三个新路由接管原抽屉三个 tab；**`Drawer.tsx` 删除**
   （无引用、无测试）。`/eval` 与 `/log/<key>` 维持独立页与独立 bundle，不并入控制台路由表。
3. **路由真相只有一份**：`web/view/routes.ts`（`PageKey` / `PAGES` / `NAV_ITEMS` / `pageForPath` /
   `bootstrapPage`，纯函数）。服务端 `server.ts` 通过 `pageForPath(url.pathname)` 判定页面并
   `renderConsolePage(state, { page })` 注入 `window.__INITIAL__.page`，客户端据此渲染首帧。
4. **首帧不读 `location`**：`page` 由服务端 stamp 决定（客户端用 `canonicalPath(page)` 推导航激活态），
   挂载后的 effect 才按 URL 校准 + 监听 `popstate`。理由同既有 hydrate 纪律（SSR 无 `location`，
   首帧读浏览器状态 = 水合错配）。

**被否方案**：① **每页一个独立 bundle**（`/metrics.js` `/nodes.js` `/wire.js`）——三页共用同一份
`/api/state` 快照，页面差异只是「渲染哪部分」；独立 bundle 换来 3 次额外构建、3 份 SSR 入口、3 套
hydrate 路径，而收益只有体积（当前 app 包 68.7KB / 预算 150KB，远未吃满）。② **保留模态抽屉只重做视觉**
——C2 的核心损失是「没有 URL + 打断监控上下文」，换皮不解决。③ **只读时物理禁用动作按钮**
（原设计倾向）——owner 拍板维持既有哲学（物理禁用会让组件区灰败破碎），只读可见性改由
侧栏常驻 `tc-lock` 锁徽标 + 首屏一次性横幅承担（`docs/dashboard-redesign.md` §7 O1–O4）。

**落地范围**：`web/view/{routes.ts,format.ts(fmtElapsed)}` · `web/app/shell/{Shell,Sidebar,Topbar}.tsx` ·
`web/app/app.tsx`（外壳 + 四页分派）· `web/render.tsx`（`ConsolePageOpts.page`）· `web/app/index.tsx`
（`ConsoleBootstrap`）· `server/server.ts`（页面族路由）· `theme.css`（外壳 token + 12 栏栅格 + 三档响应式）。
**删除**：`web/components/Drawer.tsx`。**尚未做（P1–P4）**：统一行原语 `StatusRow`（现 4 套行式实现）·
`CourseMatrix` 合并「并行课程总览 + 训练调度器」· `AlertDock` 合并 7 类横幅 · `KpiStrip` ·
空态四态 · 底部大段说明拆进各页口径折叠块 · 字号阶梯全量替换（现正文仍 11.5–12.5px）。

**配套回归**（新增/改写）：`tests/web-view-routes.test.ts`（新，22 例：路径归一化/往返一致/URL 带课程/
导航激活/路由表结构约束/引导载荷三层回退）· `web-ssr-console.test.ts`（改用 `#root` 切片断言 DOM——
整个 `theme.css` 被内联进 `<style>`，类名断言此前靠样式表文本**假通过**；顺带揪出 `tc-cc__name` 只存在于
CSS 里的空断言）· `web-wire-panel-wiring.test.ts`（抽屉接线 → 路由表/服务端/分派三处接线）·
`web-ssr-readonly.test.ts`（角标 `tc-badge--ro` → 侧栏 `tc-lock`；标签文案「正在训练：」→「还有在训：」
——后者会被读成「当前查看的这门在训」，而标签列的恰恰是别的课）。

**证据**：`cd dashboard` — `bun run typecheck` ✓ · `oxlint` 0 警告 0 错误 · `bun run test` **746 pass / 0 fail**
（P0 前基线 717 pass / 86 文件）· `bun src/server/build.ts` 三份 bundle 全绿（app 68.7KB gzip）。

**已知局限**：① P0 只做外壳与路由，页面内仍是单列堆叠（栅格与面板归组是 P1/P2）；② 概览页三个面板
（`MetricsTable`/`NodeStats`）仍带 `tc-drawer__panel` 类——抽屉没了但类名沿用，重命名随 P3 的 CSS 清理；
③ `LogNavCard` 暂留在总览底部（与组件卡的「≡ 日志」入口职责有重叠，是否下线待 P3 定）。

**P1 续（同一轮工作：行原语）**：四套「一行实体 + 状态 + 指标 + 动作」实现（`tc-comps` chip /
`tc-npill` / `tc-wreg__pill` / `tc-cov__row`·`tc-loopq__row`）收敛为 `StatusRow` 单一原语
（配 `StatusDot` / `SectionHeader` / `Empty`）；组件卡、节点行、worker 登记行已迁移，
课程矩阵两行随 P2 合并时迁移。同类问题的第二个实例一并收敛（`docs/dashboard-redesign.md` §1.2
新增 **C14**）：**动作结果反馈 6 处手写、5 种样式**（其中一处漏了 `tc-muted` 因而不灰，一处误用
卡片页脚样式 `tc-caption` 而多画一条上边框）→ `InlineNotice`。

**决定**：
1. **动作反馈分两层，原计划的 `Toast` 作废**：`components/Flash.tsx` **本就是**右上角浮层
   （`position: fixed` + 8s 自动隐藏），不存在「顶部 `Flash` 行」可替代——按原计划再新增 `Toast`
   只会造出**第七处**同类实现。定为：跨面板的全局动作 → `Flash` 浮层；面板局部动作 →
   `InlineNotice` **就地**（「在哪个按钮旁边」正是它的信息价值，搬到屏幕角落是净损失）。
2. **不给 dashboard web 测试加 DOM 夹具**：实测 `preact-render-to-string` **丢弃全部事件处理器**
   （`h('pre', {onClick}, 'x')` → `<pre>x</pre>`），而本仓 web 测试全是 SSR、无 `happy-dom`/`jsdom`。
   于是**交互行为类缺陷在 HTML 上不可观测**，断言拦不住——实例：旧 `tc-cc__detail` 是
   `<pre onClick={close}>`，在日志尾部拖选文字一按鼠标就误收起，而任何 SSR 断言都看不见它。
   选择**不加依赖**（MANIFEST §14 零新依赖 + dashboard 自包含纪律），改用「结构断言钉形状 +
   交互缺陷靠读代码评审」，并把这条局限写在用例头注里。**写新 web 用例时不要以为 SSR 断言
   守住了点击行为。**

**P1 证据**：`cd dashboard` — `bun run typecheck` ✓ · `bun run test` **776 pass / 0 fail**
（P1 前 746）· `bun src/server/build.ts` 三份 bundle 绿（app **69,987 B gzip** / 预算 150 KB）。

**P2a 续（同一轮工作：课程矩阵）**：「并行课程总览」（hub 侧：注册/离线/队列/离线段）与
「训练调度器」（训练侧：指针/卡在哪一步/在等什么）两张各写半边的表，按课程名 **outer join**
合并为一张（C5）。新增 `web/view/course-matrix.ts`（纯函数）与 `web/app/panels/CourseMatrix.tsx`；
**删除** `CourseOverview.tsx` · `LoopQueue.tsx` 与两套行样式（`.tc-cov__*` / `.tc-loopq__*`，~335 行 CSS）。
合并的产出不是「少一块卡」，而是**两条从前两边的表各自都看不见的矛盾**上屏：

- `在训 · hub 未注册`（warn）——rollout 在跑、hub 的课程表里没有它 ⇒ **PPO job 永远不会被派发**；
- `hub 已注册 · 无进程`（warn）——hub 在给它派活、没有任何进程推进它 ⇒ **job 堆着没人消费**。
  这条在合并前长得像普通的「停」（前者的两半在各自表里都完全正常）。

**决定**：
1. **矩阵用真 `<table>`，不用 `StatusRow` 芯片行**（与 `docs/dashboard-redesign.md` §4.2 的初稿相反）。
   `StatusRow` 是 `inline-flex` 芯片行、**跨行不对齐**；矩阵七列要**竖着比**（哪门课队列最深 /
   段内停最久），芯片拼出来的「表」同一列每行宽度不同，比较只能靠读。矩阵复用 `.tc-table` 基础
   样式 + `tabular-nums`；**状态词表与语义档仍归 `matrixStatus` + `StatusDot` 一处**（一个状态只有
   一个说法、一个色）——这条才是「同一语义只有一个原语」的实质。
2. **「在训」判据两半同源，故不设「两表说法不一」的冲突**：服务端把 `trainingFromQueue`
   （调度器存活 ∧ 该课未收官）**同时**喂给 overview 与 loopQueue，它们不会互相矛盾。会打架的是
   **hub 注册与否 × 进程死活**，冲突判定因此建在这两条上。
3. **状态词表择一：`未在训` 胜过 `停`**（同一局面两张表说法不同，合并必须选一个）。`停` 暗含
   「被停过」这个我们**看不到**的事实。
4. **矩阵两区通用**（不再 `isBc` 门控）：它是跨课程表、行自带 BC/RL 徽标；按「当前查看的课
   是不是 BC」隐藏它，等于又回到 C5。

**顺手修掉的真 bug**：原 `rowBadge` 的判据 `!hubSeen && training` 在 **hub 无应答**时也成立
（队列整个读不到 ⇒ `hubSeen` 恒 false），于是每个在训课程都被贴上「hub 未注册」+「以 `--course`
重启 hub」的**假诊断**，而同一张卡的表头正写着「hub 无应答」。新判据以 `hubOnline` 为前提，
配两条回归闸（`web-course-matrix.test.ts` 与 `web-app-coursematrix.test.ts`）。

**P2a 证据**：`bun run test` **804 pass / 0 fail**（90 文件；P2a 前 776 / 88）· `typecheck` ✓ ·
`oxlint` 0/0 · `oxfmt` clean · 三份 bundle 绿（app **71,746 B gzip** / 预算 150 KB）。

### §2026-09-20-dashboard-shell-routing — P2b 续（KPI 条 + 告警坞）

**背景**：总览页首屏没有任何**结论性读数**（胜率/阶段/算力/队列散在 5 块卡里，要滚屏拼），
而页面顶部同时最多堆 **6 条同权重横幅**（停机 / 已恢复 / 训练完成 / PPO 排队超时 / 课程编辑被拒 /
只读），谁更急、哪条已被 ack 过，全靠人读文字判断；`cloudHaltAcks` / `tc.ro-banner-dismissed` 等
本地态也因此散在 3 处各自读写。

**决定**：
1. **横幅收敛为「告警坞」（`components/AlertDock.tsx` + `view/alerts.ts`）**：条目化 + 按严重度
   `err > warn > info > history` 排序 + 默认展 2 条、其余折成「还有 N 条 ▸」；**全空时整坞不渲染**
   （空坞等于给「一切正常」再画一块卡）。本地态读写与判据收进 `alerts.ts` 一处，组件纯渲染。
2. **`title` 与 `detail` 分层而不是删信息**：`title` 是「只读第一行就能决策」的结论，原来那句长文案
   **一字未删**地降到 `detail`——弱化的是排版层级，不是信息量。
3. **`resume`（真调 API）与 `ack`（只写本地）在类型上分开**：两者长得像「按钮」，但一个会改训练
   状态、一个只改本机可见性；混为一谈会在只读/LAN 场景点错。只读提示的 `ack` 走**自己的固定键**
   （`tc.ro-banner-dismissed`），不复用停机那种按事件身份（`cloudHaltAcks[<id>]`）的键。
4. **只读横幅归 `info`，且是坞里唯一的会话属性条目**：它带「关闭」语义（与其它 5 条事件型
   警告不同）——是**会话属性**而非事件。常驻职责仍由侧栏 `tc-lock` 徽标承担，所以关掉它不会
   让人看不见只读态。
5. **KPI 条只给「有时序」的两格画微型走势**（采样胜率 / eval 胜率，52×18 inline SVG，复用仓库
   里早已存在但**零消费者**的 `sparkPoints`）。阶段 / 在训课程 / 算力 / 队列**不补一条平线**——
   平线看起来就是一个「一直没变」的结论，比没有走势更坏；空位留给副读数。
6. **`kpiTiles(state, nowMs)` 用顶栏那个 10s ticker 的 `now`，不取当场 `Date.now()`**：它与顶栏
   阶段 chip 显示同一个阶段的耗时，两处秒数不一致就是 bug。

**顺手补掉一个潜伏 bug（审计 C15）**：`var(--line)` 在 `.tc-cc__scope` / `.tc-mx__singleton` /
`.tc-comps__group + .tc-comps__group` / `.tc-mx__opsep` 四处被用了很久，而 `--line` **从未定义**
——CSS 中无后备值的 `var()` 会让**整条声明作废**，于是两个徽章的描边根本没画、矩阵操作列之间
那条 1px 竖线**完全不可见**（P2a 落地时无人察觉，因为「少一条 1px 线」不会让任何断言变红）。
已在 `:root` 补齐。**教训：`var(--x)` 拼错一个 token 名不会报错，只会静默丢弃整条声明——
新写 `var()` 时先 grep 该 token 是否定义过。**

**P2b 证据**：**863 pass / 0 fail**（92 文件；P2a 后 804 / 90）· `typecheck` ✓ · `oxlint` 0/0 ·
`oxfmt` clean · 三份 bundle 绿（app **75,713 B gzip** / 预算 150 KB）。

### §2026-09-20-dashboard-shell-routing — P3 续（抽屉残留清理 + 独立页共用外壳）

**背景**：抽屉 P0 已删，但它的痕迹分三类留着——① `.tc-drawer*` 一整块 CSS 还在服务两个面板；
② `tc-drawer__panel` 这个类名还在两个面板的根节点上（抽屉不存在了，名字无法自解释）；
③ /eval 与 /log 两个**独立 bundle** 各写一个「返回控制台」链接，与控制台没有共享外壳：从侧栏
点进「评估」后侧栏连同六个入口一起消失，那是**导航断层**（进去就出不来，想去节点页得先回总览）。

**决定**：
1. **`tc-drawer__panel` → `tc-panelbody`**（唯一两个消费者：`MetricsTable` / `NodeStats`），
   `.tc-drawer*` 整块（mask / drawer / __hd / __tabs / __tab / __tab--on / __body / __panel）删除。
2. **抽屉回归闸做成三通道**：① 首帧 `#root` 切片（**不含** head 里内联的样式表）无该串；
   ② `src/web/theme.css` 无 `^\.tc-drawer` **规则**（注释里允许留旧名说明改名史，故只锤行首
   选择器而不锤字符串）；③ 遍历 `src/web/**/*.tsx` 无该串。另加**前提闸**（`html` 必含内联样式表），
   免得 ① 退化成永真。原断言只查 `<aside class="tc-drawer"` 一个具体标签——换标签名、或把类名
   写进字符串拼接就漏，那不是闸是装饰。
3. **抽 `app/shell/NavSidebar.tsx`，而不是让独立页硬套 `Sidebar`**。`SidebarProps` 要的是课程
   选择器 / 触发门禁 / 刷新间隔——那是**控制台才拥有的全局设置**（独立页各自的轮询节奏属于页面
   自己）。硬塞就得上假数据（下拉框里列出假的刷新间隔），拿谎话换整齐。故按「**导航 vs 全局设置**」
   切一刀：导航全站共用（含激活判定与 `?course=` 透传），设置各页自持（由可空 `footer` 插槽注入）。
   导航项的点击回调也做成**可空**：不传 = 原生跳转——客户端 pushState 只能在**同一个 bundle** 的
   路由表内切页，独立页拦截四页导航等于白屏。
4. **`Shell` 由「收 props」改成「收节点」**（`sidebar` / `topbar` 为 `ComponentChildren`）：原签名
   已把外壳焊在控制台的数据形状上，独立页永远进不来。节点化之后外壳真的只剩布局（与它自己的
   头注一致：状态与动作的所有权留在各页 App）。
5. **`PAGES` 扩容为 `AnyPageKey`（四页 + eval/log）**，但保留窄的 `PageKey` 用于路由判定——
   独立页**要读同一份元信息**（否则顶栏标题只能写成散落在两个 bundle 里的字面量，两处描述同一页
   迟早不一致），可它们**不参与本 bundle 路由**（`pageForPath` 对它们返回 null；窄类型保证
   `bootstrapPage` 不可能返回独立页）。`Topbar` 随之退化为可复用：`stateView=null` 时不伪造读数、
   `onRetry`/`onRefreshNow` 可空。
6. **两大独立页删掉自己的「返回 / 回去」链接**（由侧栏六个入口取代），页名改由外壳顶栏给；
   /log 的组件名从 `<h1>` 降为 `.tc-loghead__title`（一面一 h1）。

**顺手修掉的两个真缺陷（都是「接进来才看得见」的那类）**：
- **C16：`.tc-row` 在 theme.css 里有两套定义，且在先的那套被静默覆盖**（同为单类选择器，后者胜）。
  旧版是页级「一行控件」的 flex（`gap: sp-6`）+ 搭档 `.tc-col`（**零消费者**）；新版是 P1 引入的
  芯片行（StatusRow）。后果不仅难看：`TaskBundlePanel` 与 `LogNavCard` 的作者发现「行不成行」，
  就地用内联 `style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}` 打了补丁
  （这直接解释了审计 C10 的一部分实例）。已删死定义，行布局统一用本就存在的 `.tc-line`，
  并拆掉两处内联补丁。
- **C17：`.tc-evalpage*` 三个类名从未有过任何样式**（`git log -S` 查遍历史确认：不是迁移丢的，
  而是写在标记里就没配过）⇒ 评测页从头到尾没有页级容器（贴窗口边缘、无最大宽、无留白）。
  同类：`.tc-wrap` 只剩日志页一个消费者，与外壳 `.tc-main__body` 职责重叠。两者一并删掉
  ——页面容器一律由外壳给。

**顺手记下的测试纪律（C18）**：删掉日志页「← 返回控制台」按钮后，那条
`expect(html).toContain('返回控制台')` **仍然通过**——因为整个 `theme.css` 被内联进 `<style>`，
而新写的一条 **CSS 注释**里恰好写着那几个字。**断言实际检查的是样式表文本**。两个独立页的
测试文件已同规改为先切 `#root` 再断言（与 `web-ssr-console.test.ts::body()` 一致）。

**P3 证据**：**867 pass / 0 fail**（92 文件；P2b 后 863）· `typecheck` ✓ · `oxlint` 0/0 ·
`oxfmt` clean · 三份 bundle 绿（app **75,872 B** / log **16,046 B** / eval **21,049 B** gzip）。

### §2026-09-20-metric-table-single-home（2026-09-20，总览「最新 6 轮」与 `/metrics` 整表的**单一归属**；已决，**已落地**）

**背景（用户要求「把重复收掉、定一个单一归属」）**：总览 Hero 的「最新 6 轮」表与 `/metrics` 的完整
指标表展示同一批数字。查清后区分两层「重复」：
- **页面层：不是重复。** `docs/dashboard-redesign.md` §3.3（已批准规格）故意同时保留两者，职责不同：
  总览 = 「最新 6 轮」**速览**（全宽、可折叠、主行/eval 双视图、evalA 入队、导出 replay）；
  `/metrics` = **全部 iter 的完整表**（搜索 / 排序 / 列显隐）。删掉总览那份 = 推翻已批准规格里
  的「最新 6 轮指标表：Hero 现有表格原样保留」。
- **代码层：是真重复。** 同一套 15+ 列被**手写了两遍**：`Hero.tsx` 是 15 个 `<th>`（含各自的
  `title` 口径文案）+ 逐格 `<td>` 格式化；`MetricsTable.tsx` 是另一套 `Col<MetricRow>`
  （`label` + `cell` + 排序/显隐）。两套**当前标签一致**，但那是人手抄得仔细的结果，不是结构保证
  —— 改一处漏一处的漂移是时间问题（与 C6「动作反馈 6 处手写」/ C14 同一类缺陷）。

**决定（owner 2026-09-20 拍板，选项 1）**：**保留总览速览，只收「实现重复」。**
- 单一归属 = **列模型**（每列的 `label` / `title` 口径文案 / `num` 对齐 / 值格式化）只留一份在
  `view/`；两个页面都从它取。总览渲染它的「最近 6 行 + 少量列」**投影**，`/metrics` 仍是
  「全量 + 搜索/排序/列显隐」的唯一权威。
- **不**删总览那份表（不推翻 §3.3）、**不**把 DataTable 搬进总览（那要给公共组件加 minimal 模式，
  影响面大于收益）。
- 落地形态（待做）：`view/metric-col-specs.ts`（纯数据，无 JSX：`key/label/title/num/side`）→ `Hero`
  主行表与 eval 表改为按 spec 渲染表头并按 `key` 取值；`MetricsTable` 的 `Col` 改为 `{ ...spec,
  cell }`（保留它自己的排序/显隐/配对逻辑）。验收：两处表头集合由 spec 驱动，且新增一条
  **源文件级闸**（禁止再出现手写的 `胜局耗时`/`承伤/杀` 类表头字面量两份）。

**已落地（2026-09-20）**：
- 新增 `src/web/view/metric-columns.ts`：`METRIC_COLS`（23 列 × `label`/`title`/`num`）+ 两个顺序数组
  （`MAIN_ROW_KEYS` / `EVAL_ROW_KEYS`）+ `colsOf()`。配对四列与过拟合列的 `title` 仍引用
  `view/format.ts` 的既有常量（那份“防两处分化”的先例就是本次扩张的模板）。
- `Hero.tsx`：两张表的表头改由 `colsOf(...).map(colHead)` 生成（文件里只剩 colHead 一处 `<th>` 模板）；
  空态 `colSpan` 也改由模型算 —— **顺手修了一个没人会发现的 bug**：此前手写 `colSpan={15}`，
  而 eval 表实为 16 列，空态那一行一直少跨一格。
- `MetricsTable.tsx`：每个列壳改为 `...colDef('<key>')`，本文件零内联 `label`/`align`/`thTitle`。
- 新用例 `tests/web-metric-columns.test.ts`（11 例）三层：模型完整性 / **源文件级闸**（Hero 只剩
  1 处 `<th>` 模板；MetricsTable 零内联列字段）/ **渲染级闸**（首页主行表表头逐列 = 模型；指标页
  表每个表头都能在模型里找到且口径一字不差；**指标页 eval 模式的列序逐列等于 `EVAL_ROW_KEYS`**
  ——这一条是「两处 = 同一份」的交叉证据）。
- **迁移中的自伤与拦截**：我建模型时漏给 `entropy` 标 `num`，直接把 `/metrics` 的熵列从右对齐
  改成左对齐；被新用例的数字列断言拦住。另有两处是**测试自己的错**（这也是值得记的）：`<th([^>]*)>`
  会先匹配到 `<thead>`（断言全错位），以及「零 `<th>`」的源文件闸把我自己的注释也算进去了。
  教训：结构类断言要么看语义（`key`/顺序），要么就得让正则真的只匹配目标标签。

**两处口径变更（有意统一，非意外）**：① `熵` 列在指标页原标 `entropy`，现统一为 `熵`（与另一侧的
中文列名一致）；② `承伤/杀` 的 hover 口径两处原本略有出入（总览多一句「越小越会周旋」），现两边
都用更完整的那一句。

**证据**：**878 pass / 0 fail**（92 文件；本次前 867）· `typecheck` ✓ · `oxlint` 0/0 · `oxfmt` clean ·
三份 bundle 绿。

### §2026-09-20-dashboard-shell-routing — P3d 续（`LogNavCard` 下线：日志入口的单一归属）

**背景**：P0 遗留 ③ —— 总览底部的「组件日志入口全集」面板（`LogNavCard`）与组件卡行内的
「≡ 日志」链接职责重叠，定案留给了 P3。

**决定：下线 `LogNavCard`**。判据不是「页面太挤」，而是**同一语义两个入口**：
1. 组件卡行内已有指向 `/log/<key>` 的链接，同一页再列一遍同一组入口 = 一语义两入口。
2. 它那句「日志页独立轮询（follow 2s / 关 4s），上滚读历史不被拉回」描述的是**日志页自己的行为**，
   写在总览页本属错位——已并入组件卡「≡ 日志」的 `title`（入口与它的说明住同一处）。
3. **「全组件」并不由它承担**：日志页内自带 `<nav class="tc-logtool__nav" aria-label="组件">`
   的逐组件 chips，从侧栏「日志」进去即可切任意组件 ⇒ 下线不丢任何路径。
连带下线 `.tc-preset` / `.tc-preset--on` / `a.tc-preset`（唯一消费者就是它），CSS 里留一条注释
记明去向，免得日后被当成悬空规则重加。

**新发现（写在这里是因为它决定闸的形态）**：组件卡行内的日志链接是**条件渲染**的——
`exited && error` 走 ⚠ 入口（也指向 `/log/<key>`）、`running` 走 ≡ 入口、`stopped` **两个都不渲染**。
所以「行内入口覆盖全部状态」是假的，**真正的兜底是侧栏「日志」+ 日志页组件导航**。

**能力保全闸（此前零覆盖）**：面板下线前，本仓**没有任何断言**盯着「每张组件卡能去自己的日志页」
——链接再被删掉也不会有测试变红，最后一条路径会默默消失。新闸分两层：
- **渲染级**：fixture 中「退出且有错」的组件的 ⚠ `href` 按 key 指向自己的 `/log/<key>`；
  且侧栏「日志」入口在场（一个进程都没跑时它是**唯一**入口）。
- **源文件级**：`ComponentCards.tsx` 里 `href={logHref}` **恰好 2 处**（exited ⚠ + running ≡），
  任一支被拆掉即红。
- **日志页内组件导航（真正的全集载体）也要逐个断言**：`web-ssr-log-page.test.ts` 此前只查
  `toContain('/log/trainingLoop')` **一个链接**——nav 退化成一个 chip、或某组件被漏掉，它照样绿。
  已改为「逐组件断言 + `tc-lognav` 计数恰好等于 `state.components.length`」，并加一条**前提闸**
  （组件数 > 0），免得两者同时为 0 时退回永真。

> 初版闸写成 `expect(dom).toContain('aria-label="日志 ')`，**红了**——因为 fixture 里没有任何
> `isRunning` 的组件。这次红灯不是噪声，它暴露的正是上面那条「条件渲染」事实，闸据此才拆成两层。

**证据**：**878 pass / 0 fail**（93 文件）· `typecheck` ✓ · `oxlint` 0/0 · `oxfmt` clean ·
三份 bundle 绿（app gzip **76,297 B**，本次 −224 B / log 17,386 B / eval 22,377 B）。

---

### §2026-09-20-dashboard-shell-routing — P4a 续（字号阶梯单一化 + 内联样式清零 + 样式纪律闸）

**背景**：P4 视觉精修的 DoD 有两条硬口径——「正文 ≥ 13.5px；数字列 `tabular-nums`」与
「无 tsx 内色值字面量；无 `style={{ margin/gap }}` 类布局内联」。计划里的底数（「10.5px ×7、11px ×1」
/「31 处内联」）只数了 `theme.css` 与对象式内联，**实测差一个数量级**。

**决定 1：字号阶梯 = `--fs-*` 唯一一份，旧别名 `--fs-1..--fs-5` 彻底删除。**
- 实测 79 处消费者在用旧别名，新阶梯只有 20 处——所以这个阶段的**主体不是「替换字号」，是「迁消费者」**。
- 阶梯：`xs 11 / sm 12 / base 13.5 / md 15 / lg 18 / xl 24 / 2xl 30`。119 条 `font-size` 声明
  **全部**改取 `var(--fs-*)`（零字面量 px）。
- **「正文 ≥ 13.5px」的判定口径写死在此**，免得下一个人重新争：`body` / 表格 `td` / 按钮 /
  输入框 / 开关 / 横幅 = `--fs-base`（13.5px，旧基线 11.5–12.5px 已抬到可读线）。
  留在 11px（15 条：胶囊/徽标/kicker/脚注）与 12px（63 条：标签、表头 `th`、说明文字、小号按钮）
  的均**非正文**，逐个判过；表头小于单元格是有意的（标签在数据之上）。

**决定 2：内联布局 style 只允许「计算值」，其余进 CSS 类。**
- 实测 **80 处**（45 对象式 + 30 `style={常量}` 式 + 5 字符串式 `style="…"`），不是 31。
- 唯一保留：`TrendChart` 的 3 处——图形 `height`、提示框按 x 比例 `left`。**SVG 坐标系运行时
  才知道，写不成静态类名**；这是「计算值」的判据，不是「这文件特殊」。
- `data-*` 常量式内联（`style={TH}`）与字符串式内联全部清零。

**决定 3：新建源文件级纪律闸 `dashboard/tests/web-style-discipline.test.ts`（11 例）。**
这是本仓库**第一个**盯「样式纪律」的测试——上面两条都是人手一次性扫全量做出来的，
而**任何渲染级断言都不会因此变红**（样式表被内联进 SSR，裸类名断言恒真）。三组：
1. 字号：阶梯定义在场（前提）· 每条 `font-size` 必带 `var(--fs-` · 档位严格递增 · 旧别名
   定义与消费者均不得复活。
2. 内联：除 `TrendChart` 的 **3 处**（写死次数）外 `style=` 为 0 · 无字符串式内联 ·
   豁免处确实是计算值（`height: '12px'` 这类能写成类的要红）。
3. 色值：十六进制字面量只允许两个文件并限次数（`render.tsx` 店标 SVG ×8、`TrendChart`
   SVG 白描边 + `var(--eval-line, #…)` fallback ×2）· 字符串里的 CSS 颜色（`color: #…` /
   `rgba(…)`）一律不许——**豁免只豁免 SVG 呈现属性**。

**为何用「文件 + 次数的白名单」而不是「全绿」**：这两个豁免是真的（店标配色不跟主题走；SVG 标记
要白描边），一律禁止只能靠把真豁免也改坏来满足。写死次数让**第 4 处/第 3 处成为一次显式决定**，
而不是顺手加一个。

> **闸验证过会红**：落盘后把 `font-size: 11px` 与一个 `style={{ marginTop: 8 }}` 注回去跑，
> 两条分别 `(fail)`，随后恢复（残留计数 0）。前几轮吃过「闸写错但全绿」，这次先坏后好。
>
> 两个正则坑一并记下（下一个写同类闸的人会撞上）：
> ① 三位色值正则会把 HTML 数字实体 `it&#123;N&#125;` 里的 `&#123` 当成 `#123`（假阳）——需 `(?<!&)`；
> ② `font-size` 断言必须只匹配**声明**（`font-size:` 到分号），全文找名字会把自己的注释判红。

**证据**：**889 pass / 0 fail**（94 文件；P4a 前 878 / 93）· `typecheck` ✓ · `oxlint` 0/0 ·
`oxfmt` clean · 三份 bundle 绿（app gzip 75,877 B · log 17,386 B · eval 22,377 B；预算 150 KB）。
**未做**（P4 余下）：三档响应式实测、空态四态审计、`?` 帮助与快捷键、README 目录树段落。

## §2026-09-20-console-process-course-decoupled（2026-09-20，用户指令：服务进程启动与课程解耦 + 开课/停课独立入口）

**背景（一次实测故障里其实躺着两件事）**：用户点「启动训练」（课程 `x20-steady`）失败，控制台输出：

```
❌ 启动训练中断于 trainingLoop
selfNode: self-node 已在运行 (port 8443)
hubServer: hub-server 已在运行 (port 8787)；已回灌 0 门课的离线/在线意图
失败 x20-steady: 需要合法 course（[]）与 mode（['online', 'offline']）
trainingLoop: 共享 trainer 启动即退出 (PID 2496)
```

- **① trainer 死因 = spec 漏挂 venv site-packages**（与课程耦合无关）：`venv.python` 是 uv 跳板的**真身**
  （基础解释器——这台机器 `pyvenv.cfg` 无 `executable`，`resolveVenvPython` 只能从 `home` 取），第三方包
  只能靠 `PYTHONPATH` 挂 venv site-packages。R3-5 新增的 `trainerServeSpec` 只挂了 `NN_TRAINING`
  ⇒ `ModuleNotFoundError: No module named 'pydantic'` ⇒ 「启动即退出」（`tmp/trainer-cluster.log` 实锤）。
  hub/其余组件无恙：它们要么只用 stdlib，要么本就带这一项（`localWorker` / `trainingLoop` / BC spec 都是
  `${sitePackages};${NN_TRAINING}`）。**定案**：`trainerServeSpec` 与它们同规；实测 `run_rl_cluster.py --help`
  在该 PYTHONPATH 下 import 链全通。
- **② 「需要合法 course（[]）」= 时序错位**：hub 的课程表是**扫盘发现**
  （`<traj-root>/<课>/{remote-jobs,offline}` 存在且新鲜，`_course_dir_live`），而旧形状把「置 hub 模式」挂在
  **启动 trainer 的第三步**上——那一刻课程目录还没建出来（`remote-jobs/` 要等训练侧第一次发布 job）
  ⇒ hub 诚实地回 400。

**用户指令（原文）**：「服务进程启动不应与课程绑定。进程启动时不要自动开启课程训练，需要增加独立的入口
开启/停止课程训练！」

**定案**：

1. **启动 = 只起进程**：`startPreset(opts)` 不再要课程；`startSharedTrainer` 不再有任何按课程的副作用（旧的
   「播权重 / 建账本 / 写课程旋钮 / 置 hub 模式」四件全迁走）——「起个进程」不必先选一门课，② 的时序
   也从结构上消失。停止语义随之更干净：重启共享 trainer 的自动解停改成**全课程**（无课程 = 全课程）——
   被停掉的正是整个调度器，只解「当前查看的那门」会让其它课替它背锅。
2. **开课 / 停课 = 独立入口**（`actions/course-lifecycle.ts`；路由 `openCourse` / `stopCourse`）：
   - **开课** = 课程级旋钮（`courses.<课>.{rollout_src,run_iters,remote_degrade_after}`）+ 发现事实
     （账本 + **`remote-jobs/`** + RL 权重播种）+ 解暂停 + 置 hub 模式（**有界重试** 3×2s：hub 扫到这门课
     要一两拍）+ 报执行面。**进程没跑也能开**（trainer 是发现式的，下一拍就入队）。
   - **停课** = **非破坏**：暂停意图（`tmp/loop-control.json`，与调度器卡片的「暂停」同一份契约）
     + 该课 hub 置 offline。队列/账本/课程表一个字不动，恢复走开课。
3. **课程级选项整体迁到「开课」**：训练模式（在线/离线）· rollout 位置 · 降级本机——它们落
   `courses.<课>.*`，而进程是共享的一台。启动弹窗只剩进程级（隧道/瘦身/行为开关/预演）；`preset` 路由对
   `trainMode`/`rolloutSrc`/`remoteDegrade` **响亮 400**（静默丢掉 = 一条假承诺）。
4. **入口位置（用户选）**：顶部课程选择旁的按钮（已停课显示「开课」，否则「停课」）。判据由 `/api/state`
   的 `courseLifecycle` stamp：`open` = 账本存在（与训练侧 `discover_courses` / hub `--discover` 同一判据）、
   `stopped` = 暂停意图。只读视图**不物理禁用**按钮（只读是动作边界，不是按钮状态——
   `web-ssr-readonly.test.ts` 钉着这条）。

   ★ **本点当天即被取代**（同日晚些的报障）：判据「账本存在」= 历史课全在训（进程一起来把 tmp/ 下
   21 门历史课一起拉去跑），按钮形状也被用户在后面的指令里改成「课程 select + 「训练」按键 +
   每门课一个 pill（含停课）」——见 §2026-09-20-course-enable-marker-and-training-pills，
   那里有完整的判据、形状与否决理由。

**备选与否决**：

- 停课 = **下架账本**（删/改名 `training_log.jsonl`，让调度器与 hub 都看不见它）——否：真从训练里消失，
  但 iter/队列/账本等阅读面**同时**消失，且重开 = 重建账本（历史归零）。
- 停课 = **只停本机**（只写暂停意图、不动 hub 派发）——否：云机仍会领走队列里已入队的整段 job 跑完，
  而操作员以为停了。
- 保留「启动时顺带开课」并加一个「启动后立即开课」勾选框——否：默认路径仍把两件事绑在一起，② 照旧存在。
- 用 `core/config.ts::validateCourseArg` 做开课的课程校验——否：它最后一手是 `process.exit(1)`，对长驻的
  控制台进程就是自杀（一次误点 = 整个控制台消失）；改成可捕获的 `ActionError`，判据保持一致。
- 为让 hub 立刻认课而在 hub 侧加显式注册 API——否：hub 与 trainer 共享同一份盘，「课程在这跑」本来就写在
  盘上（R3-5 的发现模式原则）；多一条注册旁路 = 会失败、会乱序、会忘了调的第二个事实源。开课改为先建
  `remote-jobs/`（训练侧第一次发布时本就会建的那个目录），空目录 = hub 认课的锚点。

**违反后果**：把课程准备重新挂回启动路径 ⇒ 「起进程先选一门课」与 ② 的 400 一起复活；停课改成下架账本 ⇒
操作员看不到自己刚停的那门课的任何状态（阅读面连带消失）。

**落地**：`dashboard/src/stack/specs.ts`（`trainerServeSpec` PYTHONPATH 修复）·
`server/actions/{start,preset,course-lifecycle,index}.ts` · `server/api/{route,state-view}.ts` ·
`web/view/console-types.ts` · `web/app/{app.tsx,panels/{TrainLaunchModal,OpenCourseModal}.tsx}`。

**回归**：`dashboard/tests/course-lifecycle.test.ts`（新，19 例：开课把发现事实写全（含 `remote-jobs/`）·
课程级旋钮两向（在线撤离线标记 / 离线成对落键）· 置 hub 模式的「hub 还不认识这门课」回归与有界重试 ·
停课非破坏与可逆 · hub 不可达时意图照样落盘）· `training-shared-trainer.test.ts`（② 段改钉「准备只住
course-lifecycle + 启动路径零课程写盘」）· `training-console-busy.test.ts`（幂等早退不碰课程：traj 根
坏掉也必须成功）· `web-train-launch-wiring` / `train-mode-offline` / `rollout-src-launch-option` /
`slim-launch-option` / `push-config`（课程级选项不得回流启动链路）。

**gate**：dashboard **737 pass / 0 fail**（717 → 737）+ `tsc --noEmit` 干净 + 三份 bundle 构建通过
（app 292245B / gzip 68110B）；根 `bun run check` **1938 pass / 0 fail**。

**未做（明确记录，不是漏）**：① 实弹验证「停课后云机也不再领活」需要真节点（本轮全在假 fetch 下证明逻辑）；
② 课程生命周期只在顶部按钮一处可见——`CourseOverview` 行内那格仍是「切离线」，两者语义不同
（离线 ≠ 停课：离线课照旧在本机跑/整段上云）。（② 已被次日口径部分解决：在训课程现在顶部有
pill 行，见下一条。）


## §2026-09-20-course-enable-marker-and-training-pills（2026-09-20，用户报障 + 用户指令：课程开训必须手动开；在训课程显示为 pill）

**背景（上一条落盘当天就撞上的两个洞）**：

```
启动trainingloop 成功后，界面显示一堆课程正在训练！！！ 课程开训需要用户手动开启！！！
正在训练：c6-chip、remote-smoke、x1-rebirth、…（21 门）
```

- **① 课程表判据错了**：共享 trainer 是**发现式**的（扫 `<traj-root>/*/training_log.jsonl`），tmp/ 下堆着
  几十门历史课的账本 ⇒ 进程一起就把它们**全部**拉进训练；hub 同理（`_course_dir_live` 只看
  `remote-jobs/` 存在且新鲜）⇒ 残留的 pending job 还会被继续派给真 GPU worker（白烧租约）。
- **② 顶部没有「哪几门在训」的样子**：旧形状把其余在训课程堆成一串文字（`正在训练：a、b、c`）
  ——读不出各自进度，也没有停课入口（停课挂在「当前查看的那门课」的按钮上）。

**用户指令（原文）**：「课程开训需要用户手动开启」·「顶部课程 select 选择某课程，点击「训练」按键；
正在训练的所有课程，都在顶部显示为一个 pill，概览显示 it 数和状态（参考节点 pill），有停止按键，
点击 pill 后切换显示其趋势图和指标表，并高亮 pill；点击停止按键后，停止课程，从顶部区域移除」。

**定案**：

1. **开课 = 一个显式标记文件**：`<traj-root>/<课>/training-enabled.txt`（`remote/protocol.py::COURSE_ENABLE_MARKER`，
   控制台「开课」写 /「停课」删）。训练侧（`rl/loop_plan.enabled_courses` → `loop_serve` 发现模式、
   `run_rl_cluster.py --json` 只读课程表）与 hub（`_course_dir_live`）的判据统一改成
   **账本 ∧ 开课标记**；`discover_courses`（盘上跑过哪些课）**保持不变** —— 课程下拉仍要能选历史课。
   照落点选目录内的独立文件（而不是共享 JSON）：一个判据、一处位置，开/停各是一次文件操作
   （无读-改-写竞态），控制台重启不丢「哪几门开着」，且它同时是**证据**（写入了开课时刻）。
2. **控制台的服务端事实同源**：`/api/state` 的 `trainingCourses` 改成**已开课课程**（标记扫描），
   与 `courseLifecycle.enabled` 同一表达式——它同时供课程 select 的 🔥 标记、在训 pill 行、
   `CourseOverview` 的「在训」列与「触发门禁」开关的可见性（一处算，四处用）。旧口径
   （「共享 trainer 在跑 ∧ 队列未收官」）回答的是另一个问题：进程没跑时已开课的课会从顶部消失。
3. **顶部形状**：课程 select + **「训练」按键**（弹 `OpenCourseModal` 收课程级选项：训练模式 /
   rollout 位置 / 降级本机；已在课程表时再点 = 按当前选项重写旋钮并重置 hub 模式）+ **在训 pill 行**
   （顶栏正下方，与节点 pill 同一套视觉语言）：每门课一个 pill = 状态点 + 课名 + `it<N>`（账本指针，
   不是已结算轮数）+ 一句状态（采集中 / 等回传 / 推进中 / 空闲 / 待进程 / 已暂停 / 已收官 / 已中止 /
   视图不可用）+ ■ 停课。**点 pill 切查看目标**（Hero 趋势 + 抽屉指标表随查看课程走）并高亮；
   ■ 停课走 `stopCourse` 且**显式带课程名**（不走「当前查看课程」兜底——点 A 课的 ■ 必须停 A），
   服务端下一拍 stamp 里不再有它 ⇒ pill 自行消失（**不做本地乐观删除**：队列/账本一个字没动这件事
   只能由服务端事实说话）。没有在训课程时整行不渲染（空行会被读成「有东西没加载出来」）。
4. **停课 = 删除开课标记** + 暂停意图 + 该课 hub 置 offline（仍是**非破坏**：队列/账本/课程表不动，
   恢复走开课）。这是上一条停课语义的**必需补充**：只写暂停意图而不删标记，发现式的训练进程
   下一拍又会把这门课拉起来。

**备选与否决**：

- **判据用「控制台自己记一份在训名单」**（`console-state.json` 里加 `enabledCourses`）——否：训练侧与 hub
  必须知道同一件事，而它们读不到控制台的状态文件 ⇒ 只能各记一份（正是「一启动 21 门课」的成因）；
  盘上的标记是三方（trainer / hub / 控制台）都能读到的那一份。
- **判据用「有没有账本 mtime 新鲜」**——否：历史课目录随时因为动过账本而变新鲜，正是最贵的那类误派
  （真 GPU worker 白烧租约）；且 freshness 是滑动的，操作员意图（开/停）会被时间悄悄改写。
- **停课 = 删账本 / 改课程文件**——否（同上一条：阅读面连带消失、历史归零）。
- **在训 pill 行的数据源用 python 的 `run_rl_cluster.py --json` 一门定生死**——否：它要起子进程（10s TTL），
  读失败时 pill 会整行消失——**停课入口不能因观测面坏了就消失**。故 pill 的全集来自服务端 stamp 的
  标记扫描（纯 fs），it/状态来自队列行（读面不可用时它才降级为「视图不可用」，pill 仍在、仍可停）。
- **pill 上只给状态点不给 it 数**——否：多课程并行时操作员第一眼要看的就是「各自跑到第几轮」
  （用户原话「概览显示 it 数和状态（参考节点 pill）」）。
- **点 pill 时也把 localStorage/服务端操作员课程一并改掉**——否：那会把「看哪门课」变成持久副作用
  （切一下看一眼就把动作目标换掉了）；`selectCourse` 本来就是「本浏览器查看 + 本机同步操作员课程」
  的既有语义，pill 复用它。

**违反后果**：把开课标记退回「有账本」⇒ 一次「启动服务进程」就把 tmp/ 下全部历史课拉起来跑（并让 hub
继续派残留 job）；停课不删标记 ⇒ 点到「停课」后课程自己又回来（比没有停课更坏：操作员会以为停不掉）；
在训名单在 TS 里按进程存活重算 ⇒ 进程一停已开课的课程全从顶部消失（开课与进程是两件事）。

5. **「空课程表」必须回契约形状**：默认表 = 已开课的课，而开课是显式动作 ⇒「一门课都没开」是启动后的
   **第一种状态**。只读入口 `run_rl_cluster.py --json` 的空表分支原本回一行人话，而控制台是直接
   `JSON.parse` 这段 stdout（`server/api/loop-queue.ts`）⇒ 在默认状态下常年报「输出不可解析」的红错。
   定案：空表也回 `{"courses": [], "pools": {...}}`（形状在所有分支一致；池容量是进程事实，与有没有课无关）。

**落地**：`nn-training/remote/protocol.py`（`COURSE_ENABLE_MARKER`）· `nn-training/rl/loop_plan.py`
（`course_enabled` / `enabled_courses`）· `nn-training/rl/loop_serve.py`（发现模式两处扫描）·
`nn-training/run_rl_cluster.py`（只读课程表）· `nn-training/remote/hub_server.py`（`_course_dir_live`）·
`dashboard/src/server/actions/course-lifecycle.ts`（标记写/删）· `dashboard/src/server/api/state-view.ts`
（`trainingCourses` = 已开课）· `dashboard/src/web/view/loop-queue.ts`（`coursePills` 纯函数）·
`dashboard/src/web/app/panels/TrainingPills.tsx`（新）· `dashboard/src/web/app/app.tsx`（顶部形状）·
`dashboard/src/web/theme.css`（`.tc-tpills` / `.tc-tpill`）。

**回归**：`nn-training/tests/test_loop_plan_waiting.py`（+2 例：空课程表的 `--json` 形状 · 默认课程表 = 已开课）·
`dashboard/tests/training-pills.test.ts`（新，14 例：pill 推导四态 + 确定性事实优先 +
「待进程」/「视图不可用」不编造 · `trainingCourses` = 标记而非账本 · SSR 上屏/高亮/BC 徽标/停课键/
空行不渲染/顶栏只有「训练」）· `course-lifecycle.test.ts`（+2 例：开课写标记、停课删标记、可逆）·
`web-ssr-readonly.test.ts`（旧 `tc-training-tag` 用例改写为「select 标记 + pill 行」）·
nn python：`test_multi_course_hub.py`（`_enable` 助手 + 新回归「没开过课的目录不进课程表」）·
`test_serve_wiring.py` / `test_offline_task_pack.py` / `test_worker_offline_cap.py`（造课目录的助手补写标记
——它们钉的是各自那件事，不是「没标记也能被发现」）。

**gate**：dashboard **752 pass / 0 fail** + `tsc --noEmit` + oxlint 0 warning + 三份 bundle 构建通过
（app 296114B / gzip 69219B）；根 `bun run check` 绿；nn python 全量 **0 failed**（`bash tools/githook/nn-py-safe.sh`）。


## §2026-09-20-publish-lineage-filter（2026-09-20，用户报障：云机领到 ppo 任务后一直报错）

**症状（用户贴的云机日志）**：worker 领到 job 后逐份失败，刷屏三类错误：

```
job 2e2dabb810bf9299 REJECTED: D14 course_fp 不匹配：job=abbf8045102c… shard=e6c69a46167c… — skip (not retried)
job 991b9e70a3e1efed FAILED: RuntimeError: CUDA error: uncorrectable ECC error（硬件，非本次）
job 8d2ff3ab54e2b72b: 代码已变更：本进程加载 f4e2e4041c95… != job 要求 6224d4b41404… ⇒ 退出码 86 重启
```

**取证（`tmp/<课>/remote-jobs/<job>/payload.tar.xz` 逐 shard 读 manifest）**：`c6-chip` it16 那份
payload 里**混着两个血缘**——550 份里 21 份的 `course_fp` 是 `e6c69a46…`（课程文件编辑前的旧版本），
其余是 `abbf80451…`（= 当时的 job/课程指纹）。云端 `worker.py` 对**每一份** shard 判 D14
（`d14_corpus_match`），一份不合就整份 job 拒收 ⇒ hub 侧永远等不到结果、worker 反复领同一份死活。

**根因 = 发布端漏了血缘过滤**：`hub_client.iter_shard_dirs` 只按 `it{it}` 目录枚举（同名去重），
而**对账端**（`rl/resume._scan_shards` 的 `course_fp` 过滤）与**云端**（逐 shard 拒收）都在判血缘
——同一个目录三种口径。`it{it}` 是**累积**目录：课程文件被编辑过（改语料身份语义）/ 换过 runId 时，
里面会同时躺着新旧两代 shard，于是「打进 payload 的集合」⊃「云端会接受的集合」。

**定案**：

1. **判据只有一份**：`d14_corpus_match` 从 `remote/worker.py` 搬到 **`remote/protocol.py`**
   （`worker.py` 只做名字转发，既有 import/调用面/测试一字不改）。发布端与云端**同函数** ⇒
   「打包集 ≡ 云端接受集」成为结构事实，而不是两处约定的巧合。
2. **发布端过滤**：`iter_shard_dirs` / `iter_bc_shard_dirs` 新增 `course_fp=` / `corpus_fp=`
   （仅关键字参数，缺省空串 = 旧行为逐字节不变），逐 shard 用同一条判据挑；剔除的**响亮日志**
   （`D14: 剔除 N 个异血缘 shard …` + 每份一行），manifest 不可读的一律不收（宁可少一份，
   也不让云端整份退回）。PPO 与 BC 同规（BC 语料同样带 `course_fp`，同一条链同一处坑）。
3. **落位侧重算同参**：`verify_and_land` / `verify_and_land_bc` 用 manifest 自带的
   `course_fp`/`corpus_fp` 走**同一次过滤**再算 `data_fp`（发布集 == 重算集 == 云接受集；
   三者只要有一处口径不同，data_fp 校验就会把好结果判成「云训了别的语料」）。
4. **调用方顺序**：`loop_steps._remote_ppo` 里「算血缘（`course_fp`/`corpus_fp`）」**提到**
   「扫 shard」之前——扫描要用它们过滤（原来顺序相反，正是漏过滤的温床）。

**备选与否决**：

- **云端放宽（把拒收降级为「跳过该 shard 继续训」）**——否：那等于让「跨课程语料混训」静默发生
  （D14 熔断的全部意义就是不许），且每份 payload 里混进来源不明的语料，结果无法归因到任何课程。
- **云端多轮重试同一份 job**——否：payload 是**不可变**的（内容哈希进了 `payload_sha256`），
  重试只是把同一个必然失败再烧一遍租约；病根在发布端。
- **发布端过滤 + 云端仍逐 shard 判**——采纳：云端那道是**协议层不可绕**的守卫（防篡改/防旧 hub），
  发布端这道是**不让坏包产生**。两道都在，且共用同一个判据函数。
- **让 `it{it}` 目录物理隔离两代课程（每代一个目录）**——否：`it` 目录名是 `data_fp`/resume/
  账本的公共契约（改名 = 全线血缘破裂）；过滤是局部且可逆的修复。
- **顺带清掉盘上那份已在队的坏 job**——否：不删任何历史产物（非破坏纪律）；它属于未开课课程，
  新派发闸已不再把它发给任何 worker，重开该课时训练侧的下一次发布也会按 §381 作废更早迭代的 pending。

**gate**：nn python 全量（ruff + mypy + **1834 passed**）绿；新增回归
`test_iter_shard_dirs_drops_foreign_lineage` / `test_iter_bc_shard_dirs_drops_foreign_lineage` /
`test_d14_predicate_is_single_implementation`（首个用例同时断言「不过滤时会打进 3 份」= 修复前形状）。

## §2026-09-20-hub-dispatch-gate-and-stale-adoption（2026-09-20，同一故障的第二/第三层根因）

**为什么还有两层**：D14 那条修的是「坏包被造出来」；用户贴的日志里还有两个独立症状——
**worker 领到的是 11 门课的陈旧 job**（x20-floor 6.8h、x20-powered 15.2h、c6-chip 157.8h…），
且**盘上今天新加的课程闸对它无效**。

**取证**：

- `netstat` + `tmp/training-start/registry.json`：监听 :8787 的 hub = **PID 20860，今早 08:04 起的进程**
  （新闸是之后落的盘）。`tmp/*/training-enabled.txt` 全盘只有 `x20-steady` 一个（10:38 开课）。
  ⇒ 在跑的 hub 还把 20 门历史课留在自己的课程表里，继续把它们残留的 pending job 派给真 GPU worker。
- 真盘只读探针（新代码）：`discover()` 只登记 `x20-steady` 并派发它那份新 job，20 个历史课目录一个不碰。

**定案**：

1. **派发闸 `_HubQueue._serves_course`**（发现模式）：课程目录必须仍带 `training-enabled.txt`，
   否则 `claim_next` **当拍**跳过（告警每课一次，队列/账本一个字不动 = 停课仍是非破坏的）。
   发现时判过还要在派发时再判，因为课程表是**发现那一刻**建的，而 `remote-jobs/` 里的 pending job
   不会自己消失——没有这道闸，任何在旧表/旧代码里登记过的课程都能把陈旧 job 继续喂给云机。
2. **控制台接管旧码进程**（`server.ts::reconcileWatch` + `core/reload.ts::runningStaleCode` +
   账本新字段 `RegistryEntry.startedAt`）：`watch()` 以**当下**指纹为基线，而 watch 表住内存——
   **控制台一重启就丢**，重启后的对账把在跑进程重新基线化成「就绪」，「这个进程早于最后一次改码」
   从此永久不可见。现在启动对账先判 `runningStaleCode(spec, startedAt)`（哨兵 mtime > 启动时刻
   +2s 容差；无记录 = 旧条目 ⇒ 按旧码处理），是旧码就**先 restart 再 watch**；每个 spawn 点
   （selfNode / hubServer / trainer / localWorker / 监督重启 / 冒烟）都写 `startedAt`。
   **不含 cloudflared**：第三方二进制跑的不是我们的代码，而重启它的代价是**隧道 URL 变化**
   （云机手上那个 URL 立刻作废、在跑 job 无法回传）——零收益高代价，单独豁免。

**备选与否决**：

- **只在控制台 UI 提示「hub 可能是旧码，请手动重启」**——否：这正是今天发生的形态（「hub-server
  已在运行」= 幂等成功），而「旧码」是不可见状态；把不可见交给操作员记着 = 下次照旧。
- **hub 侧自行热重载代码**——否：python 进程热换模块语义不清（已 import 的模块/闭包/线程），
  而仓库既有的契约就是**控制台监督重启**（reload.ts），这里只是补上「重启控制台后仍成立」。
- **判据用已存在但未接线的 `lastMonitorChange()`（全局触点）**——否：它是**全局**时刻（任何组件
  spawn 都会刷新），跨组件互相掩盖；`startedAt` 是每进程一份的精确值，且它本身就是证据。
- **把「无 startedAt」也算作未知而不重启**——否：那正好放过今天这件事（旧条目就是问题所在）；
  代价是**一次**接管重启，之后账本自带启动时刻，稳态零误报。
- **重启 cloudflared 也纳入旧码接管**——否（见上：URL 作废）。
- **顺手停掉/清掉那 11 份陈旧 pending job**——否：非破坏纪律（停课/历史产物一个不删）；闸挡住
  派发就够了，重启该课时 `cancel_stale_jobs`（§381）自会作废更早迭代的 pending。

**gate**：dashboard **760 pass / 0 fail**（新增 `supervisor-stale-code.test.ts` 8 例：判据边界 +
接线门禁——含「每个 spawn 点都写 startedAt」「cloudflared 必须豁免」「restart 早于 watch」）；
`tsc --noEmit` + oxlint 0 warning；根 `bun run check` 绿；nn python 全量
（ruff + mypy + 1834 passed，含新 `test_stopped_course_stops_being_dispatched`）绿。

---

## §2026-09-20-body-transfer-stall-guard（2026-09-20，用户报障：云机 claim 第二个 job 后几分钟无动静、无日志）

**背景**：云机领到 `it2` 的 PPO job（`a4b2e1e2d438c28a`）后卡在 payload 下载——
日志从 `claim ... downloading payload` 到 5 分钟后的 socket 超时**一行都没有**（用户原话：
「几分钟一直没有动静，也没有 log 打出」）。取证：`cloudflared` 日志同期每 5 分钟一条
`lookup region1.v2.argotunnel.com: i/o timeout`（DNS 劣化窗口，隧道只有 1 条 ha-connection），
**小 POST（心跳/轮询）照常、大 body 卡死**；hub 侧 `/payload` 的访问行属于高频静默规则 ⇒
两端日志同时沉默。这不是「网络抖动」一种病，是**两侧都没有停滞判据**：

1. worker `download_payload` 只有一把 `timeout=300` 的**整读**：停滞时静默等满 5 分钟，
   而 socket 超时抛出的 `TimeoutError()` **没有正文**（`_get_with_retry` 只把 `repr(e)` 写进
   日志）⇒ 连「为什么失败」都写不出来。下载过程本身**零进度输出**（分不清「在下」与「死了」）。
2. hub `wfile.write()` **没有发送超时**：对端半开（隧道/代理侧掉了、本机 TCP 还挂着）时
   永久阻塞，那个 handler 线程永久卡在写里——客户端永远拿不到 payload，而 hub 日志一个字没有。

**决定**：传输层加**有界 + 有名字 + 有进度**三件套（数字都在源码注释里，改它们要连本文一起改）：

| 位置 | 判据 | 行为 |
|---|---|---|
| `remote/worker.py::_read_body` | 空闲 45s（`BODY_IDLE_TIMEOUT_SEC`）无新字节 | 抛**有正文**的 `TimeoutError`：`body 停滞：45s 内没有新字节（已收 N bytes / 共 M）` |
| 同上 | 总预算 300s（`BODY_TOTAL_TIMEOUT_SEC`） | 治「永远在滴水」（每个读都有字节、但总也读不完） |
| `download_payload/code/ts_code/blob` | 进度回调（≥5s 一行） | `job X: payload 下载中 3.20 MB / 4.85 MB (66%) 用时 12s（270 KB/s）` |
| `remote/hub_server.py::_bytes` | 发送超时 60s（`SEND_TIMEOUT_SEC`）+ 256KB 分片 | 停滞即断并打印 `已发 N/M bytes`；≥256KB 的 body 完成时打一行（可对账速率） |

**关键不变量**：缺省路径（不给 `idle_timeout`/`progress`）**逐字节不变**——`_request` 只在
下载路径上改走分块读，`poll_job`/`post_result` 等小请求行为不动。

**备选与否决**：

- **只把 `timeout` 由 300 调小（如 60）**——否：仍然只会在**整读**上炸一次，中间零输出；
  而且分不清「链路慢但活」与「链路死了」（前者本来该让它跑完）。
- **只在 worker 侧重试/加长租约**——否：治不了「不知道发生了什么」；重试前的 5 分钟静默仍在。
- **把停滞做成静默重试（不打日志）**——否：这次事故的**全部**损失就是「静默」；
  响亮一行（带字节数与原因）比安静重试值钱得多。
- **hub 侧改成流式读盘（避免 4.8MB 进内存）**——本次不做：现有行为是 `read_bytes()`，
  改动面（`record_payload_sent` 的字节口径、Content-Length 来源）大于收益；
  分片**写**已经解掉「永久阻塞」这个真问题。
- **顺手把 `/payload` 从高频静默规则里拿掉**——否：多 worker 下会刷爆 hub 日志；
  改成「大 body 完成/停滞各一行」（有信息量、无噪声）。

**同窗口的 hub 停服 = 人工操作（用户 2026-09-20 确认是手动关的）**：这也解释了取证里那个
「疑点」——`hub-server.out` 停在 11:34:19、账本条目 `hubServers['']` 于 11:34:36 被清空、
无"意外退出"标记：`stopComponent` 杀进程后本来就是 `clearAnyComponent`（条目没了 ⇒
exit-watchdog 没有可标记的对象），控制台由此显示 `stopped`。**不是崩溃**，本次改动与它无关。

**但它留下一条真教训**：组件级决策（谁在何时停/重启了什么）目前**只打控制台 stdout**、不落
文件——所以从盘上的证据（组件日志 + 账本）**无法**区分「人工停的」与「自己死的」，
排查会往「谁杀的」方向空转（本次就绕了这一圈）。下次同类「集群突然静默」的报障，**先问
一句是不是手动停的**，再翻日志；要让机器自己回答，得把组件级决策也落盘。

**gate**：nn python 全量 **1842 passed**（ruff + mypy 绿，含新 `tests/test_body_transfer_guard.py`
8 例：停滞有名/进度可查/预算生效/停滞即响亮重试，以及真 TCP socket 的
「读端不读 ⇒ hub ≤发送超时断开并打印已发字节数」）。

---

## §2026-09-20-console-decision-log（2026-09-20，用户指令：控制台的启/停/重启/判死要写日志文件）

**背景**：上一节那条「同窗口 hub 停服」的复盘最后一公里——hub 被**人工**停掉后，盘上的证据
（组件日志 + 账本 + console-state）只能显示「日志断在半分钟前」「账本条目没了」「没有意外退出
标记」，**分不出**「人工停的」与「自己死的」，只能反过来去问操作员。根因不是「没写日志」：
`core/log.ts` 早就是 stdout + 文件双写，但

1. **控制台从没 arm 它**——只有 `launch/cli.ts`（一次性 python 启动）调 `initLog(tag)`；
   `bun run dashboard` 走的是 `server/server.ts::main`，`logFile` 恒为空串；
2. 监督器 / 启动对账 / 请求异常的判决用**裸 `console.*`**，连双写都绕过；
3. **动作结果不落任何盘**——「谁在何时点了停/启/开课」只存在于 UI 回执与终端滚屏。

**决定**：

* `core/log.ts`：新增 `initConsoleLog()`——稳定文件名 `tmp/training-start/console.log`，
  每次启动追加一行会话头 `=== console session <ISO> pid=N ===`，启动时超过 8MB 轮转一代
  `.1`，任何 IO 失败 best-effort 不抛（**日志写不进绝不能搞停控制台**）；新增 `error()`
  （stderr + 文件，与 log/warn/fail/info/ok 同规）。路径走 `paths.consoleLogPath()`
  （惰性；`BCITY_CONSOLE_LOG` 可重定向，单测不污染仓根 tmp/）。
* `server/server.ts::main`：在**任何决策之前** arm，并把路径打进启动横幅（不然没人知道去哪看）。
* `server/api/route.ts`：唯一动作出口 `routeAction` 拆成 `dispatchAction` + 统一落盘——
  **每个动作结果一行**（`[action] stop hubServer → ok (HTTP 200): …`，失败走 `warn` 带 ⚠️），
  纯读接口 `getGateHaltMode` 排除（高频回读不是决策）。
* 决策站点（`server.ts` / `exit-watchdog.ts` / `stack/hub.ts` / `route.ts` / `actions/stop.ts`）
  不再有裸 `console.*`（接线门禁钉着）。

**备选与否决**：

* **每次会话一个新文件**（沿用 `initLog(tag)`）——否：操作员要的是「一个固定路径能翻到全部
  决策」；会话边界用会话头表达，不必用文件名，顺带避免了百来个 `console-<ts>.log` 堆积。
* **让操作员自己重定向 stdout（`bun run dashboard > x.log`）**——否：默认没人重定向
  （2026-09-20 的现场就是这样），而且终端滚屏一关就没了。
* **只记组件动作（start/stop/restart），不记其它 POST**——否：同一出口记全部才不漏
  （「谁把课程切走了」「谁改了节点并发」同属决策），成本是一行/点击；只把纯读接口排除。
* **引第三方日志库（分级/滚动）**——否（MANIFEST §14 零依赖精神）：`appendFileSync` + 一次
  启动轮转足够，且行为可读。
* **把日志按组件分文件**——否：决策的因果链跨越组件（「停 hub」→「trainer 等不到结果」），
  一份按时间排的流水才能看出因果。

**gate**：dashboard **785 pass / 0 fail**（新增 `console-decision-log.test.ts` 24 例：会话头 /
追加不截断 / 阈值轮转 / 写不进不炸 / 路径惰性；接线门禁：决策站点无裸 `console.*`、`route.ts`
唯一出口且结果落盘、`initConsoleLog` 早于 `startSupervisor` 调用、真动作的结果落盘）·
`tsc --noEmit` · oxlint 0 warning · 真盘探针确认默认路径可写、会话头与两级行如期落盘。

## §2026-09-20-open-course-lock-identity-and-write-order（2026-09-20，用户报障：开课被「自己人」的锁拒掉）

**背景**：用户点开课，回执是

```
❌ x20-steady 未开课：run_rl 锁被 PID 18364 持有
训练模式 在线：已撤掉离线标记（run/run_iters）
rollout 位置覆盖：courses.x20-steady.rollout_src=local
远端连败降级本机：关（连败即 ABORT）
已写开课标记 training-enabled.txt（训练侧/hub 的「在训」判据）
先停掉在跑的那一份（或删除锁文件）再开课——它与共享 trainer 抢同一批 traj。✕
```

两处独立缺陷叠在一起：

1. **判据错**：PID 18364 = 控制台自己起的**共享 trainer**（`run_rl_cluster.py --serve`）。
   它服务多课，每开一门课就取该课自己的**按课锁**（单进程多课模型的正常持有）——而检查的
   判据是「该课锁活着就拒」⇒ **每次开课都被自己人拒**，只要 trainer 在跑就永远开不了课。
   真冲突的形状是「另一份**按课** runner（手工 `run_rl.py --course <本课>`）活着」——它与
   共享 trainer 抢同一批 traj（两套调度器各跑一半课程、互相覆盖权重）。
2. **写序错**：检查排在写面**之后** ⇒ 被拒的那一次照样写了课程旋钮 + **开课标记**（标记就是
   训练侧/hub 的「在训」闸）+ 撤了离线标记，而暂停意图还留着。于是三种口径并存：控制台说
   「未开课」、hub/云机说「这课在训」、调度器按暂停意图一拍不推。

**决定**：

* **开课前置检查带身份看**（`course-lifecycle.ts::courseRunnerFacts`）：该课按课锁的存活持有人
  == **共享 trainer 进程级锁**（`nn-training/.run_cluster.lock`）的持有人 ⇒ 同一个进程 ⇒
  正常持有，**放行**并在回执里说明白（免得操作员在日志里找一个不存在的双开）；持有人活着但
  不是它 ⇒ 真冲突，拒开课。陈旧锁（持有人已死）两侧都归 null ⇒ 不拦。BC 同规（锁名换 `run_bc`，
  比较的仍是那把进程级锁）。
* **写序 = 「会拒的先拒、会抛的最前、开课标记最后」**：① 全部会拒绝的判据零副作用先行
  （锁身份 + **暂停意图文件健康**——`setCoursePaused` 对坏文件是保守拒绝、不覆盖）；② 写面
  按 `saveConfig`（唯一会抛的一步，容量/槽位守卫）→ 解暂停意图 → `prepareCourseForOpen`
  （旋钮/账本/`remote-jobs/`/**开课标记**）排序 ⇒ 任何失败都不留「已开课」的盘上假象。
* 停课**不删**按课锁：锁归进程所有（停机时释放），控制台删它只会让「双开」的判据消失。

**备选与否决**：

* **去掉开课时的锁检查，直接开**——否：手工跑着 `run_rl.py --course <本课>` 时开课就是两套
  调度器抢同一批 traj（静默互相覆盖权重），这一类必须拦。
* **按「锁文件存在」判、不看持有人身份**——否：陈旧锁（崩溃残留）会把课永久锁死，而陈旧态
  在盘上极常见（`nn-training/` 里就躺着 20+ 把 09-14/09-20 的旧锁）。
* **把「共享 trainer 在服务本课」当成「已经开着」而直接返回成功**——否：回执会变成一个假
  成功（暂停意图、hub 模式这些开课动作一步都没做）；这些动作是**幂等且有意义的**，照做并按
  事实报告才对。
* **开课失败时回滚已写的盘**（补偿事务）——否：写序已经让「被拒 = 零副作用」，补偿逻辑本身
  是第二处可能出错的地方；真实失败的窗口只落在「旋钮已写、标记未写」（= 无人调度的旋钮，
  重按一次开课即自愈）。

**gate**：dashboard **792 pass / 0 fail**（`course-lifecycle.test.ts` 新增 9 例：共享 trainer
持有按课锁 ⇒ 开课成功且回执说明「正常持有」／另一份按课 runner ⇒ 拒开且**零副作用**（无标记、
无旋钮、无 `remote-jobs/`、没碰 hub）／陈旧锁不拦／三态判据 + BC 锁名 ／暂停意图文件坏了 ⇒
拒开且不覆盖／`saveConfig` 抛 ⇒ 连暂停意图都不解／成功时回执行序与盘上一致）·
`tsc --noEmit` · oxlint 0 warning · 三份 bundle 构建通过 · 根 `bun run check` 绿。
**真盘验证**（只读探针，`nn-training/` 真锁）：`cluster()=18364`、`run_rl(x20-steady)=18364`、
`facts={holder:18364,cluster:18364,conflict:null}` ⇒ 那次被拒的开课现在会放行。
---

## §2026-09-20-wire-slow-reroll（2026-09-20，用户报障：云机传输长时间卡在 6–9 KB/s；评审 plan/minimize-payload.plan.md 时的实测）

**背景**：上一节（body 停滞判据）让「卡住」变得可见之后，现场读数暴露出**真正的病**：

| 时刻 | 端点 | 字节 | 耗时 | 速率 |
|------|------|------|------|------|
| 10:41:35→10:43:04 | payload | 2,012,020 | 88.6 s | 22.7 KB/s |
| 11:25:01→11:25:06 | payload | 4,848,536 | 4.9 s | 990 KB/s |
| 12:49:28→12:49:32 | payload | 3,454,884 | 3.9 s | 885 KB/s |
| 12:49:58→12:51:30 | **code** | 1,433,893 | 106 s | **13.5 KB/s** |
| 12:51:30→12:51:42 | **blob（opt_init）** | —（无进度行 ⇒ <5 s） | — | ≥120 KB/s |
| 12:59:29→13:04:51 | payload | 3,486,200 | 321.8 s | 首连接 **6–9 KB/s** 烧完 300 s 预算；**重试换连接后 354 KB/s** |

12:49 那两行是**同机、同 hub、相邻 3 秒**的请求；12:59 那次「重试」本质就是一次**意外重抽**，
只是由总预算在 300 s 后触发（321.8 s 里约 280 s 白等）。`urllib` 每个请求新建连接
（`net_http.urlopen` → `OpenerDirector.open`，无 keep-alive 池）⇒ 速率由**这条连接**的
路由质量决定，双峰 44×（13 KB/s ↔ 990 KB/s）。

**决定**：把「换一条连接」做成**一等机制**（而不是继续优化平均字节数）——
**先重抽，再瘦身**（`nn-training/remote/worker.py`；阈值只此一处实现）：

| 判据 | 值 | 理由 |
|---|---|---|
| 坏签阈值 | `max(WIRE_MIN_RATE=80KB/s, 本会话最好速率/4)`（`_reroll_decision` 纯函数） | 相对判据：云机、本机、不同出口各自定标；只在「明显偏离」时动手 |
| 触发条件 | 实测速率 < 阈值 **且** 按此速率**预计剩余 > 20 s** | 快跑完了不值得折腾 |
| 判据时机 | **只在首块判一次**（`probed` 一次性） | 每次重抽浪费 ≤ 一块（256 KB）⇒ **不可能退化成「再整份重传一遍」** |
| 重抽次数 | ≤ `WIRE_REROLL_MAX=3`，**不退避**（重抽的价值就在快） | |
| 最后一次尝试 | **永不可重抽** | 6 KB/s 的坏签真实存在；重抽是赌，不能把赌注全压在赌上 ⇒ 慢链路不会变成「永远下不完」 |
| 适用范围 | **只给幂等 GET**（payload/code/ts_code/blob）；POST result 永不重抽 | 产物上行不可重放，只走既有退避重试 |
| 传输账 | 每 job 一行 `wire payload=… code=cache-hit … reroll=1(wasted 0.25MB) 合计=…`（`_wire_flush`） | 逐条进度行看不出全局；**零字节命中也要进账**（否则读数失真）；push 模式在 `worker_server` 的 finally flush；未 flush 的 job 封顶 4 个 |

**备选与否决**：

- **先做瘦身（`omit`/`Have` 协商，plan §4.1/M3）**——本次不做：实测慢腿是**连接**，
  瘦身把 1.37 MB 变 0.5 MB 在 13 KB/s 下仍是 37 s，而重抽把它变成 ~1 s（重抽成本 ≈ 建连）。
  协商机制仍留在 plan 里（M2/M3），但优先级降到「重抽之后」。
- **把「出口限速」当环境事实（plan §1.4 原话）**——否：同机 3 秒后的 blob GET 有 ≥120 KB/s，
  限速解释不了 13.5 KB/s；那是单连接抽签。
- **发现问题就整份重下（无限重抽）**——否：坏签下会永远下不完；上限 3 次 + 末次禁止重抽。
- **重抽也走指数退避**——否：重抽的价值就是**快**；退避只用于「链路真的坏了」的瞬态失败。
- **把「同 sha 传两遍」当噪声**（本次同时查明的 code.zip 重复下载）——不当噪声：
  引导 `notebook_boot` 已解到 `/tmp/worker-code`，而 worker 按 `work_dir/code_cache/<sha>` 判命中
  （缺省 `/tmp/remote-worker`）⇒ 冷缓存重下**同一份 1,433,893 字节**。交接方案见
  `plan/minimize-payload.plan.md` M0（零协议改动，本次**未实现**，只记在 plan 里）。

**顺带修掉的 e2e flake（残留根）**：§97 的 `weights_push_cache_reset()` 只治「同进程顺序跑」，
治不了**在途线程**——前一个用例的收尾 push 可能在下一个用例 reset **之后**才记账，而全仓库
哑权重内容都是 `{"stub": true}` ⇒ `wver`（文件指纹）相同 ⇒ 被判 `kept / skip POST` ⇒
`test_it_stream_smoke` 的 I3 断言假红（xdist 下随机）。修法：哑权重内容带**每用例唯一**标记
（`{"stub": true, "case": <tmp 名>}`）——只有**键不同**才与线程时序无关；reset 保留作双保险。**gate**：nn python 全量 **1859 passed**（ruff + mypy 绿；新增 `tests/test_wire_reroll.py` 17 例：判据/相对阈值/上限/末次不重抽/浪费有界/账行形状/产物不重抽）。
签入前与 §108（continuous 报告真源）合并复跑：**1863 passed in 34.65s**（ruff/mypy 绿）。

**收尾（同日）**：本条的覆盖范围只到 worker 侧四段 GET；引导期两段（code.zip / task-pack）与账的
聚合后续补齐 —— 见 §2026-09-20-boot-wire-guard。
## §2026-09-20-node-health-by-completed-round-contrib（2026-09-20，用户指令：节点 pill 的贡献数取上一轮**完成**值，健康度按「贡献 vs 并发」重定义）

**背景**：节点 pill 行有两处口径都不对：

1. **贡献数取的是进行中那一轮**——对齐基准 `globalMaxIt` = meta 里所有节点成功结算的**最大 it**。
   轮次是**逐局结算**的，进行中那一轮的行一直在变多：先交活的节点数字漂亮、还没轮到的显示 0
   （看着像掉线，而机器完全健康）。
2. **健康度取的是 ping**——1.5s `/v1/ping` 超时 ⇒ 标「慢 / 离线」。ping 只是**可达性**；
   「上一轮到底交没交活」才是节点可用性的直接事实。

**决定**：

* **对齐基准轮 = 最近已完成轮**：完成水位 = 同一训练流目录 `training_log.jsonl` 里最后一个
  `iteration` 事件（训练循环在轮末写，正好是「这一轮账已结清」的判据）。只认 `iteration`——
  hub 追加的 `job_completed` 也带 `it`，照它取会读出「还没跑完的那一轮」
  （`latestIterFromLedgerTail` 的既有口径）。
  安全阀：水位缺失 / 为负 / **在 meta 里没有任何行**（账本与 meta 不同步）⇒ 退化为 meta 最大 it
  （旧口径）；硬用水位会把**全员**算成 0 贡献，那比退化口径糟得多。
* **健康度 = 纯函数 `nodeHealth(贡献, 并发)`**：贡献 0（或无池数据 `-1`）⇒ 离线；`≥ 并发` ⇒ 健康；
  其间 ⇒ 缓慢。pill 行据此三桶（健康 / 缓慢 / 离线，缓慢与离线仍不折叠），**ping 出局**
  （`NodeView.slow` 保留给 API / 节点统计表，pill 不再消费）。
* **local（本机直跑）同口径**——它也是一个 rollout 执行面；`rl.local_slots = 0`（直跑未启用）时
  并入「停用」折叠桶（不再占行内位置）。槽位数仍恒出（§361⑤ 的展示契约不回退）。
* **并发数前的 `✓` 去掉**（它把「并发数」误读成「在线数」）：并发数恒出，状态词另起一格
  （`a95 2 缓慢 | 1`）——判据的两个数当场可核对；「停用」仍顶替数字列。

**备选与否决**：

* **用「meta 最大 it − 1」当完成轮**——否：轮次不一定连续（换流 / 重跑 / BC 与 RL 交错），
  减一可能落在没有行的轮 ⇒ 全员 0 贡献。
* **时间静默判完成**（最近 N 秒无新行 ⇒ 该轮算完成）——否：阈值是猜的，且慢节点会把
  「还在跑」读成「已完成」。
* **按 mode 各取完成水位**（rollout / eval 分开）——否：eval 没有自己的完成事件（它与 PPO 重叠，
  行会在 `iteration` 之后继续落），分开只会做出一个「更精确但同样在漂」的水位；同基准轮下
  eval 的尾行会在下一次刷新自然补全。
* **ping 参与判定**（如「贡献 0 但 ping 通 ⇒ 慢」）——否：那正是本次要消除的混淆
  （ping 通而零贡献的节点是真没在产出）。
* **健康节点写字「健康」**——否：绿点 + 并发数已经表达；字只留给异常态（缓慢 / 离线）。
* **给停用行做减法（隐掉贡献数 / 并发数）**——否：重设计后的行是「同一形状、同一位置」
  （`StatusRow` 原语），停用行仍出值列「停用」+ 元信息「上轮 N」；数字是事实，判断由
  色 / 形 / 词给出——把数字藏掉反而让「它上轮还在贡献」这条信息消失。

**违反后果**：回到「按最大 it 取对齐基准」⇒ 每轮开头那几十秒健康节点集体显示 0，
操作员去查一台没问题的机器；用 ping 判健康 ⇒ 算力受限 / 网络抖动的节点被反复标红、
而真正零贡献的节点因 ping 通而看起来正常（本次的起因）；用 `readLogTail`（**展示**口径，
500 字符截断）读账本 ⇒ `iteration` 单行 >1.2KB，JSON 不可解析 ⇒ 完成水位与总览「轮次」列恒为空。

**顺带修掉的（同一根因）**：`readLogTail` 的 500 字符截断被误用在机器解析上 ⇒ `courseIter`
对**每一门课**都返回 null（总览「轮次」列恒显 `—`；x20-steady / c6-chip 实测）。新增
`readLedgerTail`（不截断）并把 `courseIter` 切过去；`readLogTail` 加 `maxLineLen` 参数
（默认 500 保持展示行为不变）。

**落地**：`core/paths.ts`（`tmpPoolDir()` + `BCITY_POOL_DIR`——完成水位要用**真目录树 + 真账本**
才能验，夹具写进仓根 tmp/ 会被真实训练流淹没、也会反向污染别的用例的活跃流判定）·
`server/pool-history.ts`（`lastCompletedIter` / `pickBaseIter` + 对齐基准换水位）·
`server/api/logs.ts`（`readLogTail(maxLineLen)` + `readLedgerTail`）· `server/api/overview.ts`
（`courseIter` 走 `readLedgerTail`）· `server/api/snapshot-cache.ts`（池历史**聚合一次**——
此前同一份数 MB meta 冷算两遍：慢节点判定一遍、贡献数一遍）· `web/view/console-types.ts`
（`nodeHealth` 纯函数，客户端安全）· `web/app/panels/NodePills.tsx`（三桶 + 去 ✓ + local 折叠 +
贡献数上非健康 pill）· `web/app/panels/NodeStats.tsx`（陈旧节点 tooltip 与新基准对齐）·
`web/theme.css`（`.tc-npill__state`）。回归：`tests/web-app-nodepills.test.ts`（14 例，改写为
贡献口径 + 三态 + local 折叠 + 无 ✓）· `tests/server-pool-history.test.ts`（+5 例：完成水位 e2e、
无账本退化、安全阀、只认 iteration、`pickBaseIter`）· `tests/server-api-logs.test.ts`
（+1 例：长行不截断）。**gate**：dashboard **808 pass / 0 fail** · `tsc --noEmit` ·
oxlint 0 warning · 三份 bundle 构建通过 · 根 `bun run check` 绿。

---

### §2026-09-20-merge-origin-goal-nn — 合并 `origin/goal-nn`：在训 pill 行 × 控制台重设计（IA 相撞的裁决）

**背景**：本地 6 个重设计提交（P0…P4a，`2450301`…`5898936`）与 `origin/goal-nn` 的 7 个提交在
`f883afb` 分叉。远端那条线**也动了 dashboard**——「课程开训改为显式开课」（进程与课程解耦）、
「在训课程 pill 并入顶栏行」——但它们做在**被 P0 重写掉的老 `app.tsx`** 上（抽屉 + 顶栏居中课程
select）。因此这不是文本冲突，是**两个版本的 IA 相撞**：11 个冲突块 / 5 个文件里，有 4 块是布局与
归属之争，不是标点之争。

**总则（先定后裁）**：**重设计的 IA 胜**（它是已批准的规格 `docs/dashboard-redesign.md`）；
远端的**功能与语义原样保留、重新安放到新 IA 里**。不推翻重设计去迁就旧布局，也不因布局不同
丢掉远端功能。理由：规格是 P0 就定下的、已落地 8 个阶段的骨架；远端那 7 个提交是**功能**提交，
它们的价值在行为（开课/停课、pill 的 it 与状态），不在那个具体容器。

**逐项裁决**：

| 面 | 裁决 | 为什么 |
|---|---|---|
| `app.tsx` | 取我方（新外壳）为底，移植远端 handler/state（`openCourseModal`、`handleOpenCourse`/`handleStopCourse`、`courseLifecycle`、`trainerRunning`），`handleLaunch` 瘦身（课程级旋钮不再随启动走） | 新外壳是 8 个阶段的骨架，不可回退；远的 handler 是纯行为，可平移 |
| 在训 pill 行 | 进**顶栏**：有在训课时它替代「在训 n/N」裸计数 chip | 「同一事实只上屏一次」——pill 是那个计数的明细版（本仓 P3d 下线 `LogNavCard` 同一判据） |
| 开课键（「训练」） | 进**侧栏**，紧贴课程选择器 | 它作用的对象就是选中的那门课（课程级动作跟主语走）；两侧各自保持「一屏一问」 |
| 停课 | 只在 pill 上 | 与远端一致；一个键同时做「开这门/停这门」在两门课并存时语义不明 |
| 「还有在训：a、b」串行标签 | **下线**，`.tc-training-tag` 样式随之删 | 被 pill 行取代（远端已删，取远端；我方的重命名随之作废） |
| select 的 `（正在训练）` | 改 `（已开课）` | `trainingCourses` 的语义 2026-09-20 已改为**开课标记**（与进程是否在跑正交） |
| `trainingCourses` 取值 | 取远端 `stateView.trainingCourses ?? []`，**删我方「trainer 在跑就当作这门课在训」的回退** | 该回退会给从未开课的课挂上 pill，而 pill 的停课键会真去停它 |
| `TrainLaunchModal` | 取远端拆分（训练模式/rollout 移入开课弹窗），叠我方 P4a 的内联样式纪律 | 两件都对：拆分是功能，`tc-mt-0` 是纪律 |
| `theme.css` 那块 | 取远端删除（串行标签样式） | 见上 |
| `server.ts` | 两边 import 取**并集** | 各自新增（我方 `pageForPath` 路由 / 远端 `ProcSpec`+`RegistryEntry`） |
| 并入库的远端样式 | 旧字号 token 迁到我方阶梯（`--fs-2`→`--fs-sm`、`11px`/`10px`→`--fs-xs`）；`OpenCourseModal` 6 处内联样式改走已有类（`tc-banner--flush`/`tc-mt-0`/`tc-launch__lbl`/`tc-hint`） | 并进来的是 P4a **之前**的字号口径与内联习惯；不迁就会让 `var(--fs-2)` 变成**未定义**（静默丢字号），而纪律闸允许 `--fs-` 前缀，不一定报错 |

**★ 顺手抓到一条假绿（远端测试）**：`training-pills.test.ts` 的「pill 与课程 select 在同一行」
用 `indexOf('tc-topbar__row')` / `tc-topbar__course` 定位——这两个类名合并后**只存在于内联进首帧的
`theme.css` 里**（我们的外壳叫 `tc-side`/`tc-top`），于是它靠**样式表**满足：DOM 完全错位也照样绿。
已改为先切掉 `<head>`，再断言「`tc-side` 内 课程选择器 → 开课键」「`tc-top` 内 pill 行」
（沿用 P3b 起的 `#root` 切片纪律；C15/C16/C18 同款，已记进审计）。

**★ 顺手抓到一条 flake（远端测试）**：`training-pills.test.ts` 的 `courseLifecycle?.enabled`
用的是**不传参**的 `buildStateView()`——`course` 由 `effectiveCourse(state, courses)` **推**出来
（默认 = 最近活跃课），于是结论挂在真实工作目录的残影上；`--parallel` 下同进程其它文件会写真实
`tmp/` 与 `nn-training/*.lock`。实测：**修前 4 跑 2 红**（另带 `server-api-overview` 一条同源假红），
**修后 10 跑 10 绿**（964 用例 / 98 文件）。修法是把课程**钉死**（`buildStateView('c6-chip')`）+ 写明
理由，而不是放宽断言。

**残留风险（已知，本次未处理）**：**12 个测试文件**各自改 `process.env.BCITY_REGISTRY_FILE`，
夹具另改 `BCITY_RL_CONFIG`/`BCITY_CONSOLE_STATE`，而被测模块**调用时才读**这些变量——
`--parallel` 隐含的 `--isolate` 只隔离 global，**不隔离 `process.env`**，同进程其它文件仍会互相盖掉。
旁证：**去掉 `--parallel` 跑同一套会红 30+ 条**（per-file 隔离是这套夹具的前提）。系统性做法是给这些
路径换成显式传参的注入缝（本仓 `releaseTrainerLock` 有先例），**留作后续任务**——不在合并里做这种
跨 12 文件的基建改造。

**门禁（合并后实测）**：dashboard `964 pass / 0 fail`（98 文件，10 连跑全绿）· 根 `bun run check`
`1934 pass / 4 skip / 0 fail` · nn python `1860 passed / 3 skipped` · 三份 bundle 绿
（app gzip **78,006 B** / 日志 17,401 B / eval 22,394 B，预算 150 KB）· oxlint 0/0 · oxfmt clean。

---

## §2026-09-20-console-declutter（2026-09-20，用户指令：课程区只列在训课程 / 服务面不显示共享·单例 / 删掉关键指标区）

**背景**：控制台首屏纵向空间被三处「重复或索引式」的内容占掉——课程表里几十门未在训的历史课
把在训的那几行挤出屏幕；服务面每行重复一个「共享/单例」（一族里每行都同一个值）；KPI 条六格是
「索引」型面板（每个数都能在 Hero / 节点行 / 课程表头读到同一个值）。三条一起收。

### ① 课程区只列在训课程

- **判据只有一个出处**：`course-matrix.ts::rowTraining(ov, lq) = ov?.training ?? lq?.training ?? false`
  （与状态列 `matrixStatus` **同一个函数**）。新导出 `isTrainingRow(row)` 供面板筛选——
  「哪几行上屏」与「状态列说什么」漂开就是 bug，两条用例盯着这个双向关系。
- **纯函数层不筛**：`mergeCourseRows` 仍产全集（outer join 不丢课）——计数、悬停点名、页脚
  口径（「谁在等外部 / 读面可不可用」）都靠全集。筛选发生在面板一次，不是两张表。
- **代价（刻意接受，留痕不静默）**：`hub 已注册 · 无进程` 这类「两半事实打架」的行（合并初衷之一）
  不再直接上屏 ⇒ 表头 chip「未在训 N 门未列」，悬停逐门点名 + 各自状态词。这是用**读噪**换
  **信息密度**：操作员要盯的是正在跑的那几门，而历史课在盘上会积到几十门。
- **0 门在训不是「没有课程」**：空态显因（`Empty`），三种原因分开说——「当前没有在训课程」
  （能开课）/「训练侧读面不可用 ⇒ 哪些课在训**不可知**」/「上一拍读失败」；不整块静默消失。
- 副作用（已知，未回退）：`未在训` 状态词与 `STOPPED_TITLE` 在**面板上**已不可达（只有两侧不同步时
  的 `--stopped` 行还看得见）——它们仍由纯函数层用例守着（状态语义不该因为面板筛行而被掏空）。

### ② 服务面逐行不再显示「共享 / 单例」

`scopeBadge()` 删除（`view/component-groups.ts`）+ 面板不再消费 + `theme.css` 的 `.tc-cc__scope*`
整块删。**族归属仍由 `scope` 决定**（`FAMILY_OF_SCOPE` 不动，`componentScope` 对拍用例照旧）——
删的是标签，不是分组判据；「trainer 是共享进程」这件事由族标题 + `FAMILY_META.hint` 承担
（每行重复 N 遍同一句话就是噪声）。回归：`web-component-groups.test.ts` 的「scopeBadge 不再导出 /
面板不再消费 / 样式表无规则」三通道 + `web-components.test.ts` 的渲染体无该 class 断言。

### ③ 「关键指标」区下线（删除，不是隐藏）

`app.tsx` 的 `<KpiStrip>` 下线；`web/components/KpiStrip.tsx` + `web/view/kpi.ts` +
`tests/web-kpi.test.ts` 删除；`theme.css` 的 `.tc-kpi*` 整块（含两个断点覆盖）+ `view/index.ts`
的 re-export 删除。**三条一起查才算钉住**（渲染里没有 / 组件文件不存在 / 样式表无规则）——
只查渲染的话，把面板挂回去只需改一行；而这次是删除：留一个没人用的组件文件与样式块，就是
下一波「照抄一个类名出来」的诱因。

**门禁（实测）**：dashboard `949 pass / 0 fail`（97 文件；删掉的 `web-kpi.test.ts` 原有 35 例）·
`tsc --noEmit` · oxlint **0 warning** · oxfmt clean · 三份 bundle 绿（app 306,847 B / gzip 75,325 B，
预算 150 KB）· 根 `bun run check` 绿（跳过 nn/原生部分：改动全在 `dashboard/**`）。

---

## §2026-09-20-topbar-and-family-labels（2026-09-20，用户指令：顶栏去掉页问题/「在训」、pill 靠左；族标题改「服务」）

**背景**：同一轮减肥的第二批（承接 §2026-09-20-console-declutter）。用户扫顶栏从左到右点出的四处：
「总览 现在能不能跑？这轮跑到哪了？」、pill 组前的「在训」、pill 组的水平位置、组件区族标题。

### ① 页问题不上屏（`tc-top__desc` 删除）

顶栏只留 `<h1>{title}</h1>`；`PAGES[].desc`（"一页一个问题"契约）改为**标题悬停**。

**为何不连数据一起删**：`desc` 是非空契约，`web-view-routes.test.ts` 守着（每页都要能一句话说清
它回答什么）；删渲染 = 视觉噪声没了，删数据 = 把那句「这一页是干什么的」也一并丢了。故保留数据、
换载体（悬停）。若日后确认悬停也多余，正确的删除顺序是「数据 + 契约测试 + 悬停」一起（留下没人读的字段
比删错更贵）。四页**统一**处理（不只总览）：同一个元素只给一页去掉，看起来就是坏了。

### ② pill 组前的「在训」标签删除 + ③ 整组靠左

- 标签已删（pill 自带课名/it/状态，组名交给 `aria-label`——它在页面上不可见）。
- 位置：`<Topbar>` 把 `{pills}` 从 `tc-top__right` 里**拿到 head 与 right 之间**——左半「我在看什么」
  （标题 → pill 组），右半「集群现在怎样」（阶段 / 节点 / 刷新 / 更新时间，靠 `margin-left:auto` 贴最右）。
  `.tc-tpills` 的 `flex: 1 1 auto` 保留：靠左 + 向右占位，左侧不再靠 `margin-left:auto` 碰运气。
- 降级形态保住：`!(pills && trainingCount>0) && courseCount>0` 才退回「在训 n/N」裸计数 chip——
  两侧**互斥且穷尽**（这笔一改漏，零门在训时顶栏会什么都不说）。回归：`training-pills.test.ts`
  新增「无 `class="lbl"` + 组在 `tc-top__right` 之先 + 有 `aria-label`」一例（切 `#root` 后再断）。

### ④ 组件区族标题：「服务面 · 单例」→「服务」（「课程面 · 按课程」→「课程」）

理由：逐行「共享/单例」徐章已于上一批删除 ⇒ 全族同值的后缀挂在标题上也开始重复；标题只说**这一族是
什么**，作用域事实下沉到 `FAMILY_META.hint`（「hub / 隧道 / trainer / 本机 worker 各一个进程服务所有课程，
本机 agent 全机一份」）。课程族同理改成「课程」（那族当前无成员、不渲染，但两份标题必须同一口径）。

**真盘只读探针**（真实 state，x20-steady 在训）：顶栏可见文本序 = `总览 | x20-steady it161 采集中 ■ | 节点 3/10 | ⟳ | 更新于 …`；
DOM 序 `head(6768) < tc-tpills(6856) < tc-top__right(7326)`；`class="lbl"` 不在；`服务面 · 单例` 不在、`>服务</span>` 在。

**门禁（实测）**：dashboard `950 pass / 0 fail`（97 文件）· `tsc --noEmit` · oxlint **0 warning** ·
oxfmt clean · 三份 bundle 绿（app 306,461 B / gzip 75,297 B）· 根 `bun run check` 绿。

### 补记（同日，同一轮用户指令的剩下两条）：节点行去「上轮」字样 + 服务族顺序

1. **节点行元信息只剩那个数**（用户：「不需要显示上轮字样，hover 时提示就好」）。`contribText` 从
   `上轮 N` 改成 `N`；释义全部移到 `contribTitle(v)` 悬停里：**数的含义 + 单位 + 缺失语义**
   （「最近完成轮贡献 N 局（rollout + eval 合计；进行中那一轮不计；— = 无池数据）」）。
   **为何删字样而不是删数**：行内状态词（健康/缓慢/离线）已由「贡献 vs 并发」判出，但**交了多少**
   仍是个可区分的事实（缓慢的 6 与 1 是两回事）；行内不再有文字提示 ⇒ 悬停是它唯一的释义载体，
   这句必须自洽完整。真盘（x20-steady）：`self 8 178 · mac 5 65 · a95 7 7 · a97 7 离线 0 · …`——
   零个「上轮」。回归：`web-app-nodepills.test.ts`（+「不得再出现上轮」+ `—` 与 `0` 不得混同）。
2. **服务族顺序 = 用户指令**：`trainingLoop → hubServer → selfNode → cloudflared → localWorker`
   （不再是「谁依赖谁」的推演：这一行最常问「训练在不在跑 / 队列通不通」，trainer 与 hub 提到最前；
   agent / 隧道 / worker 是支撑设施，靠后不档视线）。`component-groups.ts::ORDER.service` 一处改，
   两份用例的顺序数组跟着改（真盘探针：427 < 1201 < 1872 < 2538 < 3487 升序）。

**门禁（改后重跑）**：dashboard `950 pass / 0 fail` · `tsc --noEmit` · oxlint 0 warning · oxfmt clean ·
三份 bundle 绿（app 306,527 B / gzip 75,306 B）· 根 `bun run check` 绿。

---

## §2026-09-20-console-naming-and-trend-source — 服务角色名 / 节点行不写字 / 走势数据源档位

**用户指令（同一天追加的一轮）**：① 节点 pill 不显示「缓慢/离线」字样，离线时显示状态描述和判据；
② trainingLoop pill 去掉「dispatch→拉取」描述；③ 顶部趋势图加「全部 / rollout / eval」toggle 切换数据源；
④ 服务显示名换角色名（trainingLoop→管事 / hubServer→门房 / selfNode→采办 / cloudflared→跑腿 /
localWorker→丹徒），hover 提示各服务用途。

### ① 节点行：状态**不写字**（四档一视同仁）

色 + 形（`tc-dot--warn` 菱形 ◆ / `tc-dot--dead` 方 ■）+ 行级 tint（`tc-row--slow`）已经是三重编码，
行里再写一个字只是重复。**行内只剩该行的两个数**（并发 + 最近完成轮贡献），判据一律归悬停
（点 title + 元信息 title）。

**同日修正（用户明确收回）：最初让离线行在行内改写成判据句（`离线：最近完成轮贡献 0 局 < 并发 7` /
`离线：无池数据（还没结算过这一轮）`），用户当场否掉——「行内不写「离线：xxxx」，hover 提示就好」。
所以四档现在完全同规：行内只有数（贡献 `0` / `—`），两个不同的原因（零交活 / 从未结算）只在**悬停**里分开。
真盘校验：可见文本 `self 8 136 · mac 5 76 · a95 7 12 · a97 7 6 · a96 7 0`（零个「离线：/缓慢/上轮」），
悬停 `离线：最近完成轮贡献 0 局 < 并发 7`。**下次改这块的口径：先问「行里要不要字」，默认答案是不要。**

### ② trainer 行：pull 档不出「dispatch→拉取」徽章

没有登记节点是**缺省态**，那两个字不随任何东西变化 ⇒ 常年挂着的徽章只是噪声；非 pull 才是需要
解释的部署决定（东西被推去哪、推通了没有）。`pushBadgeText` → `pushBadge(f): RowBadge | null`
（pull 返回 null），pull 分支的悬停说明一并删（徽章不存在时它是死代码，留着下一个人会当漏了的功能加回来）。
「谁在跑这门课」仍由节点行回答（登记事实 + 健康度）。

### ③ 走势图数据源档位：全部 / rollout / eval

`TrendSource` + `TREND_SOURCE_OPTIONS` + `isTrendSource` 落在 `view/series.ts`（与 `TrendRange` 同规）；
档位与范围并排成一个控制条（`.tc-trendctl`：数据源在左、范围在右——看哪条口径 × 看多少轮，两个独立问题）。
`eval` 档把 eval **升为主序列**：标签换 eval 自己的词、线色改 eval 橙（`TrendChart` 新增 `color` 覆盖并
导出 `COLOR_EVAL` 作该色的**唯一出处**——面板里不得出现第二个色字面量）、`toneOf` 跟着**正在显示的那条**走
（否则切到 eval 档时，rollout 的阈值会去染 eval 的数：一个已经过期的判断）。SSR 首帧恒「全部」
（持久化偏好 hydrate 后恢复，与范围档同套路）⇒ 回归必须**单独渲染 TrendCell** 的三档。
硬规矩：**夹具不能用默认参数**——`cell('eval', win, undefined)` 会静默落到默认值，
「该课没有 eval 数据」这条分支看起来在测其实没测（实测报红才发现）。

### ④ 服务名 = 角色名（key 仍是机器身份）

单一源 = `web/view/component-roles.ts`（`COMPONENT_ROLES` + `componentName` / `componentPurpose` /
`componentHover`），总览组件行、日志页导航、日志页页头**三处同一份词**；行名渲染成
`<b title="管事（trainingLoop）—— 用途">管事</b>`（`StatusRow` 新增 `nameTitle`：点说「此刻状态」、
名字说「它是什么」，两个问题两个悬停）。用例钉住两件易漂的事：**键集与 `core/types.Component` 逐字相同**
（漏一个 = 那个服务在页面上露出 `hubServer`）、**用途里必须有 key**（悬停是 key 在上屏面的唯一落脚处）。

**没有改 `COMPONENT_LABELS`**（服务端的长技术名）。它喂的是 flash 消息、exit-watchdog 记的
`TrainingLoop (trainer)[<课>]`（`exit-watchdog.test.ts` 直接断言这个格式）与账本流——那些地方要的是
**对账用的精确名**，跟着改会把日志格式与一批用例一起动。代价已记录：动作结果文本里的服务名仍是技术名；
若日后要统一，改的是 `labels.ts` 一处 + 那条格式用例，而不是在 UI 侧再插一张表。

**门禁（实测）**：dashboard `964 pass / 0 fail`（99 文件）· `tsc --noEmit` · oxlint 0 warning · oxfmt clean ·
三份 bundle 绿（app 310,028 B / gzip 76,386 B）。

---

## §2026-09-20-preact-svg-attribute-spelling — 趋势图面积块变黑：Preact 客户端不归一 SVG 属性名

**用户报告**：从「指标」页切回「总览」，趋势线与横轴之间的**面积块显示为黑色**；**硬刷新后恢复**。

### 根因（两层，缺一不成立）

1. **属性名写成了 camelCase**：`TrendChart` 里是 `<stop stopColor=… stopOpacity=…>`、`strokeWidth`、
   `textAnchor`、`fillOpacity` 等 —— 这是 React 习惯。
2. **Preact 两条路径对属性名的处理不一致**（实测读 `node_modules/preact/dist/preact.mjs`）：
   - `preact-render-to-string`（SSR）把 camelCase **归一**成真 SVG 拼法（`stopColor` → `stop-color`）；
   - 客户端 diff 在 SVG 命名空间下只做 `l.replace(/xlink(H|:h)/,'h').replace(/sName$/,'s')`，
     其余 **原样 `setAttribute(l, u)`** ⇒ `stopColor` 是个 SVG 不认识的属性 ⇒ 被忽略 ⇒
     `stop-color` 回默认值 **黑**（而 `stopOpacity` 同理丢失 ⇒ 不透明）。两块 stop 都黑 ⇒ 整个面积黑。

这正好解释「为什么只在切页后出现」：切页 = Hero **卸载重挂** = 这些元素由客户端新建（拿到错误属性）；
硬刷新 = 浏览器用的就是 SSR 那份**正确**的属性（hydrate 时 diff 只再叠一个无效属性，不影响已有的）。
**所以渲染级断言永远抓不到它**（SSR 字符串看起来一模一样），只能靠源码级闸。

### 修法（两处，都在 TrendChart）

1. **属性名一律用 SVG 自己的拼法**：短横线的写短横线（`stop-color` / `stop-opacity` / `stroke-width` /
   `stroke-linejoin` / `stroke-linecap` / `stroke-dasharray` / `fill-opacity` / `text-anchor` / `font-size`）；
   本身就是 camelCase 的保持 camelCase（`viewBox` / `preserveAspectRatio` / `gradientUnits`）——
   后者写短横线反而错（SSR 也不转它们）。
2. **顺手把面积渐变改成 `gradientUnits="userSpaceOnUse"` + 用户坐标系的 y1/y2**：原 `objectBoundingBox`
   依赖**面积路径的包围盒**，而包围盒退化（恒定序列 ⇒ 面积零高）时浏览器也会画成黑色；
   训练曲线里「平坦的一段」很常见，这是第二颗同类地雷。

### 回归闸（两条，都在现有文件里）

- **源码级**：`tests/web-style-discipline.test.ts` 新增一组 —— 扫 src/web 下全部 `.tsx`（去注释后），
  禁 18 个「SVG 里本来是短横线」的 camelCase 写法（匹配形状 = JSX 属性赋值 `name=`，避免误报
  `AXIS_FONT.fontSize`）；含**自测**（正则对阳性/阴性样本各断言一次），防止闸写成永真。
  已反向验证：故意把 `stop-color` 改回 `stopColor` ⇒ 该组立刻报红。
- **渲染级**：hero 用例断言每个 `fill="url(#id)"` 都指向同一次渲染里存在的 `id`，且全部用
  `gradientUnits="userSpaceOnUse"`（没有 `objectBoundingBox`）。

**门禁（实测）**：改动文件各自的用例全绿（`web-style-discipline`、`web-view-trend-range`、
`web-hero-trend-source`、`web-app-nodepills`）· `tsc --noEmit` · oxlint 0 warning · oxfmt clean ·
三份 bundle 绿（app 310,051 B / gzip 76,424 B）。
全量套件当时有 11 例红——**全部是工作区环境**，与本改动无关（已逐条归因）：
① `nn-training/rl-config.json` 在 21:59:05 被写入一行 `"concurrency": 4,export HUB_TOKEN='<hub token>'`
（人手粘贴事故）⇒ 文件不再是合法 JSON ⇒ 10 例读真配置的用例抛 `JSON Parse error`；
② `web-ssr-readonly` 断言「空坞不出现」，而真状态此刻带一条 loop-complete 告警（21:55:03 正常收官）
⇒ 坞里真的有东西 —— 这条用例本身就挂在**活状态**上（只有零告警时才成立）。**真盘只读探针**（真实 state）：五行角色名 + 各自用途悬停
（`管事（trainingLoop）—— 训练循环本体…` …）；节点区可见文本零「上轮 / 缓慢」字样（仅离线判据句）；
控制条 `走势数据源 → 走势范围`，默认压「全部」，6 条 eval 橙线。

---

## §2026-09-20-eval-prefers-dist（2026-09-20，用户指令：评估慢是原罪，走分布式；`--no-dist` 下不为例）

**背景**：x20-steady 终点判决（`eval-course-ckpt.ts`，800 局）为求"确定性"用了
`--no-dist`（本机单跑 2.2局/s，约 6 分钟/800 局）。用户纠正：分布式本就能保证
逐字一致，慢是白付的代价。

**为什么分布式同样逐字一致**：仿真是确定性的（固定步长 + `world.rng`，§2.3）⇒
同一 `(weights, stage, seed)` 在哪台机器跑都是同一行 JSON；分布式 tail-race /
duplicate-settle/drop 只是"同一内容的多个拷贝留哪一份"，不改变数值。it0 判决基线
文档自己就写了"`--no-dist` 跑，与分布式逐字等价"（`plan/x20-floor.plan.md:122`）——
等价是双向的，只敢用单向是多余的保守。

**定案**：`eval-course-ckpt.ts` 默认即分布式（有 nodes 的 `rl-config.json` 自动进；
零远端参与时工具自己告警并退回本机 —— 白付代价前会先响）。`--no-dist` 只留给
"远端不可用"的降级，不作日常选项。

**备选与否决**：

- **判决继续 `--no-dist` 求稳**——否：收益为零（上段已证等价），每 800 局多付数分钟；
  判决/回测动辄 1600+ 局，慢就是原罪。
- **为此给评估加确定性回归**——否：确定性已由仿真层不变量 + it0 基线的"逐字等价"
  实测共同担保，不再为已成立的事加闸。

---

## §2026-09-20-boot-wire-guard（2026-09-20，收尾 §2026-09-20-wire-slow-reroll：引导期两个大 body 也在同一次连接抽签里，却跑在 code.zip 之前）

**缺口**：上一节的护栏住在 `remote/worker.py` —— 而引导期有**两个大 body 幂等 GET 跑在
code.zip 之前**（那时 `remote.worker` 不存在，因为 code.zip 正是被下载的那个东西）：hub `/code`
（code.zip；实测最坏 1.43 MB / 106 s / **13.5 KB/s**）与离线盘的 `task-pack`（自己文档里就写着
「几 MB～几十 MB」）。两处都是 `opener.open(req, timeout=…).read()` 整读：**没有进度行、没有停滞
判据、没有墙钟预算、没有重抽** ⇒ 坏签下 106 s 一行日志都不多；几十 MB 的包在 10 KB/s 下就是
85 分钟零输出（这正是 §104「静默」事故的形状）。

**决定**：护栏做成**引导期**的一份，住 `remote/tailscale_boot.py::fetch_guarded`：
分块读 256 KB + 进度行 + 停滞 45 s（`BOOT_IDLE_TIMEOUT_SEC`）+ 调用方给的墙钟预算 +
**首块判一次**的低速重抽（`max(80KB/s, 本会话最好速率/4)`、预计剩余 >20 s、≤3 次、**末次必硬传**）；
`HTTPError` 原样上抛（401/404 是**确定性的答案**，不是传输抖动，由调用方按状态码处置）。
接线：`notebook_boot._pull`（code.zip，预算 300 s）与 `offline_boot.fetch_task_pack`（预算 = 既有
`PACK_TIMEOUT`）。成功即落一行与 worker 同族形状的账：
`wire: code.zip 1.37MB/106.0s(13KB/s) attempts=1 rerolls=0`（一个解析器吃两类行）。

**为什么住 `tailscale_boot`（而不是新开 `remote/boot_wire.py`、也不是 `net_http.py`）**：三个
notebook（训练 cell / 连接体检 / 离线盘）的 GitHub raw 拉取清单**都已含** `tailscale_boot.py` ——
与既有 `resolve_hub_url` 同一条「单源一份，两边不会漂」的理由；新开文件要同时改三份 notebook 的
清单，而 notebook 是**要重发**的交付物；`net_http.py` 在 code.zip 里，引导期够不到。

**与 worker 侧的关系 = 孪生实现**（阈值同值、「首块判一次」同纪律、末次不重抽同保证）。不能抽
共享件：引导期能 import 的只有它自己与 `tailscale_boot`。**改一边要同步另一边**；两侧单测各自钉住
同一组数字（`tests/test_wire_reroll.py` · `tests/test_boot_wire_guard.py`）。

**同一批的另两个口子**：

- **`wire` 账只有原文、没有聚合** ⇒ `nn-training/tools/wire_report.py`（三类行：worker 每 job
  摘要 / 引导期摘要 / hub 发送完成行 → 逐段 p50/p90 **秒** · p50/worst **速率** · bad% · reroll · 命中）。
  ★ **速率只报 p50 + worst**：坏签是**低尾**，报「速率 p90」量到的是好签那一端（会把 321.8 s 的
  坏签读成「一切正常」）；秒数相反——坏签就是高尾，p90 才有意义。这是 plan §7.2「报 p50/p90，
  不报均值」的可复算落点。
- **ts_code 缓存命中不进账**（code/blob 都记了，只漏这一处）⇒ `_ensure_ts_code` 命中即
  `_wire_hit(jid, "ts_code")`。

**备选与否决**：

- **把护栏也复制进 cell 的内联回退**——否：那是「GitHub raw 拉不到时的最小 pull 路径」；往 cell 里
  复刻 80 行与「cell 只留 CFG / 凭据 / 保活」的既有口径正面冲突。代价写明在 plan §4.0.1，真疼再议。
- **引导期只加进度行、不做重抽**——否：进度行解决「看不见」，但 106 s 的坏签仍要付；重抽成本
  ≈建连、收益≈100 s，两件事的性价比不在一个量级。
- **判据单源化（`worker.py` 反过来 import `tailscale_boot`）**——否：方向反了（worker 是产品、
  `tailscale_boot` 是引导脚手架），且把两个已发布、各有测试的实现绑成一个发布单元。
- **给 `tailscale_boot` 改名（它现在不止 tailscale）**——否：改名要动三份 notebook 的 raw 清单与
  缓存文件名（`/tmp/battle-boot/tailscale_boot.py`），收益只是名字好听。
- **把 `tools/remote_wire_scan.py` 当作已有的复核手段**——否（事实纠正）：**该文件不在仓里**
  （`git ls-files` 只有 `test_wire_cf_tunnel.py` / `test_wire_reroll.py`）⇒ plan §1.2 的体积表
  无法就地复现；已在 plan §6/§7.3/§9 标注，替代口径 = worker 的 `result=` 字节数 + 协议层用例。

**gate**：nn python 全量绿（ruff + mypy **303 files** + pytest `tests/` & `e2e/`，门禁 **37s**）；
新增 `tests/test_boot_wire_guard.py` 17 例 + `tests/test_wire_report.py` 11 例（用例数 与 `ts_code` 命中
那条改动一并在改后全量中确认）。`tools/wire_report.py` 对现场原文的实跑输出已核对：
`job:payload` p50 3.9 s / p90 **321.8 s**、worst 11 KB/s、bad% 50%、reroll 1。

---

## §2026-09-21-goalnn-native-features-engine（2026-09-21，落地 `plan/rollout-eval-opt.plan.md` §4：native 内核进入**生产** features 路径）

**背景**：Student `features` 是 rollout/eval 的绝对大头（本机实测：TS 52.4ms / wasm 6.0ms /
native 2.6ms per forward；每局 ~236 次调用 ⇒ 单局 sim 1338ms 里九成）。native 与 wasm
**逐字节一致**（`tests/native-parity.test.ts` 现为门禁，8 次随机输入 pooled+bufA 逐位相等），
但「怎么接进生产」此前没有任何裁决：进程模型没定、跨平台字节一致没保、资产分发没写。

**决定**：

1. **单一咽喉 + 选择链**：`src/nn/conv-wasm.ts::runStudentFeatures` = `native → wasm → TS`，
   `infer.features` 只调它 ⇒ rollout 与 eval **同引擎**（T4 免费达成）。native 失败一律回落，
   调用方永不感知；TS 实际生效时 `noteFeaturesTs()` 记账（防「以为开了加速其实在 TS」）。
2. **进程模型 = 共享库 + `bun:ffi`（同步零 IPC）**，不是「CLI + 常驻子进程」。features 是
   **逐决策顺序依赖**的（动作→下一状态）⇒ 跨决策不能批量；而本机进程启动实测 42–54ms/次
   （`cmd.exe` 42.1 / `hostname` 54.4，n=30），×236 次/局比整局 sim 还贵 ⇒ 起进程 = 负优化。
   FFI 每次调用 ~0.5µs，且 `in16/pooled/bufA` 直接以 JS 数组地址传入（连 wasm 的拷进 48KB+
   回拷 169KB 都省了）。代价：**native 只在 bun 引擎可用**（node 无 FFI，不做 napi 插件）——
   故 bun 臂的基准数字就是 native 的数字，与 node+wasm 比完再定引擎（见第 5 条）。
3. **准入 = 本机首用 attestation**：加载后先拿**真实权重**做 3 次 native↔wasm 逐字节对拍，
   过了才投产；不过 / 库缺失 / ABI 不符 / 参考不可用 ⇒ 本进程关闭 + 一行 warning；
   运行期任何异常 ⇒ 本进程**永久**关闭（不静默重试）。理由：节点是异质的，
   「本机 8/8 逐位一致」不能外推，只能在**本机**验（评审 B2/B4）。
4. **构建钉死 flags + 指纹**（`tools/agent/native-build.ts`）：`-O3 -ffp-contract=off
   -fno-fast-math`（x64 另加 `-mavx2 -msse4.2`；**已被 §2026-09-21-goalnn-native-prebuilt-distribution
   第 3 条修订为 `-mavx -msse4.2`** —— 收益已由 AVX1 吃满，AVX2 只多 ~2% 而代价是老 CPU SIGILL），
   **禁 `-march=native`** —— 它会打开 FMA/
   AVX-512，而 clang 默认 `-ffp-contract=fast` ⇒ 乘加融合 ⇒ 与 wasm 不再逐位、跨节点
   shard 字节抖动。产物 + `native-build.json`（源码 sha + flags + cc 版本 + 产物 sha）
   落在 `tmp/native/`（gitignored）；`--check` 判「盘上产物是不是这份源码编的」。
   native 侧**单源**：共享库与对拍 CLI 都链接 `src/nn/native/conv_feats_native.c`。
5. **引擎选择纳入 native 臂**：`tools/agent/engine-bench.ts` 改为调用**生产入口**（不再自绘
   wasm 内存布局——旧版按 v2 的 16 通道算、v3 已是 18 通道），输出 `BENCH` + `BENCH-ARM`；
   `chooseByBench` 语义不变（node 需快过 bun 臂 3% 才入选），但 `EngineChoice` 新增
   `bunArm/nodeArm/nativeSha`，**native 库指纹进缓存键**（重建过就重测）。
   资产进 bundle（`ensureNativeAssets`）+ 构建失败/缺库只降级不抛。
6. **记账**：shard manifest 新增 `feat`（`native|wasm|ts`）；**不进 `data_fp`**
   （`protocol.py::data_fp` 只对 dir/wver/stage/seed 求 sha，已核）。

**备选与否决**：

* **常驻子进程 + 二进制分帧 IPC**——否：见第 2 条的实测（42–54ms/次 vs 236 次/局），且要自己
  写同步管道读（Node 侧 pipe 非阻塞、`fs.readSync` 要 EAGAIN 重试），复杂度全换来负收益。
* **让 wasm 与 native 共享同一份 C 源**——否：wasm 内核用 `wasm_simd128` intrinsics，**改它就是
  改产品字节**（= 新 era，浏览器路径也要重验）。改为「native 侧单源 + 两侧逐字节对拍进门禁」。
* **用 golden 哈希替代本机 wasm attestation**——暂不做：省下的只是 ~10ms/进程，却要多维护一套
  golden 版本化；本机 wasm 参考一直都在（缺 wasm 时本模块直接关 native，保守优先）。
* **`-march=native` 换一点速度**——否：见第 4 条。
* **node 侧也用 native（napi 插件 / node-gyp）**——否：要每个节点有编译工具链 + ABI 随 node
   主版本漂，收益已被「bun+native < node+wasm」覆盖（本机 2.66ms vs 3.52ms）。

**gate**：`tests/native-parity.test.ts`（7 例：共享库 8 次逐字节 / 参考 CLI 逐字节 /
架构守卫 / 选择链 / 库路径候选 / poison 库 attestation 守卫 / 指纹过期）；
`tests/agent/rollout-runner.test.ts` 32 例（含 native 臂反超 node、nativeSha 失效、parseBench）；
`bun run check` + `bun run build` 绿。实测（`bun tools/sim/perf-conv-wasm.ts`）：
TS 52.4ms / wasm 6.0ms / native 2.6ms（**对 wasm 2.3×、对 TS 20×**）；
单局端到端（同 seed 同权重、x20-clutch.it99、2400 ticks）：**1447ms → 853ms（1.70×）**，
outcome/score/kills/ticks 完全一致；引擎选择实测 `bun 2.66ms[native] vs node 3.52ms[wasm] → bun`。

---

## §2026-09-21-goalnn-native-prebuilt-distribution（2026-09-21，落地 `plan/rollout-eval-opt.plan.md` §2.5/T2：native 库改「训练机交叉编译 + 随仓库分发」）

**背景**：原 T2 是「节点上自己 `bun tools/agent/native-build.ts` 编一次」。用户提问（2026-09-21）：
「rollout 节点机器上可能没有 clang，能本机编译出所有平台的 native 库直接给它们使用吗？」——查节点池
（docs/goal-nn-handoff.md §4：self(win) / mac / a95·a96·a97·a98(Android-Termux) / lite / gcs）可见节点是
异质的且**多数没有 clang**，原设计等于 native 臂在远端永远开不起来（只会得到「编译器不可用」一行）。

**决定**：

1. **训练机交叉编译 6 目标入库**：`src/nn/native/prebuilt/<platform>-<arch>/conv_feats_native.{dll,so,dylib}`
   + `manifest.json`（源码 sha + flags + cc 版本 + 每目标产物 sha，共 ~78 KB）。目标 = `win32/linux/darwin ×
   x64/arm64`。分发通道就是既有的 `git pull`（节点升级本来就是同一分支 ff-only）。
   **Termux 不单列目标**：它 `process.platform=linux`、arch=arm64 ⇒ 用 linux-arm64 那一份。
2. **内核改免 libc（freestanding）**：交叉编译时只有 Windows 的 MSVC 头/库、没有目标平台 sysroot，
   一旦 `#include <string.h>` / 引用 `memset/memcpy` 就链不出来（`-nostdlib`）。内核里 padding 的零填与
   整行拷贝改为本地 `cf_zero/cf_copy`（float 按值赋值，语义与 memset/memcpy 逐字节等价 ⇒ 不改结果）。
   收益：产物**零动态依赖**（无 DT_NEEDED / LC_LOAD_DYLIB / PE 导入表）⇒ 同一份 linux-arm64 在
   glibc / musl / **bionic(Termux)** 上都能 dlopen，不必为 Termux 单独出目标。
3. **x64 只到 AVX1（`-mavx -msse4.2`），不用 `-mavx2`**：实测量化（ffi 微基准，x20-clutch.it176）
   SSE2/SSE4.2 = 3.46–3.53ms · AVX1 = 2.60–2.64ms · AVX2 = 2.51–2.59ms ⇒ AVX1 已拿到全部收益（~27%），
   AVX2 只多 ~2%；而 prebuilt 是**发给别人**用的，`-mavx2` 的代价是「2013 年前的 x64 CPU 直接 SIGILL」。
   依旧**禁 `-march=native`**（FMA 收缩 ⇒ 与 wasm 不再逐位，见 §2026-09-21-goalnn-native-features-engine 第 4 条）。
4. **新鲜度判断只在仓库侧**（`--check-prebuilt` + `tests/native-prebuilt.test.ts`），**不做运行期源码 sha 校验**：
   提交时 prebuilt 与源码必然同源（门禁钉死）；而运行期真闸门始终是**首用 attestation**（真实权重下
   native↔wasm 逐字节，不过即关 native + 响亮回落）。节点上多读 3 个源文件换不来更安全的结论，只会把
   「库在 bundle 里、源码不在」这种正常情形误判成不可用。
5. **解析顺序统一为 env → prebuilt → 本机构建**（`src/nn/native-conv.ts` 与 `tools/agent/native-build.ts::resolveNativeLib`
   两处同序，因 src/ 不许依赖 tools/ 而各写一份）。prebuilt 排在「本机构建」之前：它是所有节点都会拿到的
   那一份，让本机也用它 = 暴露差异的机会最多。**节点因此完全不需要编译器**。
6. **prebuilt 放在 `src/nn/` 下 ⇒ 进 codeHash 集**（`tools/agent/codehash-files.txt` 的 `src/nn/`）：换了库
   就触发一次正常的节点升级波（与 `src/nn/wasm/conv_feats.wasm` 同一先例：受跟踪的构建产物）。
7. **云机通道也要带库（`ts_code.zip`，2026-09-21 用户点名）**：离线训练任务的 rollout 在 PPO 云机上
   执行（`kind=iter`：训练侧把 TS 运行时打成 `ts_code.zip` 下发，worker 解到 `ts_cache/<sha>` 后跑
   `bun tools/sim/export-rl-rollout.ts`）——云机**既没 clang 也不持仓库**，所以那条白名单
   （`src/**` + `tools/**` 的 `.ts/.jsonc/.wasm`）漏了库就等于「无库可加载 ⇒ attestation 跳过 ⇒
   **静默**回落 wasm」。故新增 `TS_CODE_BINARY_DIRS = ("src/nn/native/prebuilt",)` +
   `TS_CODE_BINARY_SUFFIXES = (".dll",".so",".dylib")`（**只对该目录生效**，不给 `.so` 开全局口子），
   6 个目标全带（云机 arch 打包时未知），二进制写 `0755`，目录缺失则 `HubClientError` + 提示重建命令。

**备选与否决**：

* **让节点各带工具链**——否：异质（Windows/mac/Android）且多数装不上/不该装；也要为每台机器维护编译
  环境，收益却只是「native 在少数机器可用」。
* **hub 侧下发二进制 / LFS / 独立 CDN**——否：节点升级链路已经是 git pull；多一条分发通道就多一处
  版本错配与运维面（且 codeHash 升级波无法覆盖它）。
* **`-march=native` / `-mavx2` 换一点速度**——否：见第 3 条（AVX2 收益噪声级、代价是 SIGILL）。
* **追 COFF 字节可重现**——不追：lld-link 的 `/Brepro` 把 TimeDateStamp 换成含临时 .o 路径的哈希
  （`-fno-temp-file` 也压不住），win 两份差 9–13 字节；而正确性靠 attestation、新鲜度靠 manifest sha256，
  都不依赖「重建字节相同」。（linux/darwin 两份**已**字节相同：darwin 需显式 `-no_uuid` + `-install_name`，
  否则 LC_ID_DYLIB 里带临时文件名的 pid。）
* **node 侧也吃 prebuilt（napi）**——否：node 无 FFI（沿用前述条目的否决）。

**gate / 证据**：`tests/native-prebuilt.test.ts`（16 例）——
① 同源：`prebuiltStaleReason` 为空、6 目标 sha/尺寸/ABI 与 manifest 一致；
② 格式与依赖：逐目标断言文件头与架构（ELF ET_DYN/EM_X86_64|AARCH64、Mach-O MH_DYLIB + cputype、
PE `PE\0\0` + machine + `IMAGE_FILE_DLL`）、两个导出符号在、且**不出现** libc.so/ld-linux/libSystem/
KERNEL32/api-ms-win/ucrtbase/VCRUNTIME；有 llvm 时再断言 `llvm-nm -u` 空 + ELF `NeededLibraries []`；
③ 解析优先级（env/prebuilt/local/无，含路径映射）；
④ **真执行**：WSL + python3 ctypes 加载入库的 linux-x64 `.so`，pooled+bufA 与 wasm **逐字节相同**
   （本机唯一能真跑非本平台产物的通道；win32-x64 那份由 `tests/native-parity.test.ts` 在生产入口真加载
   + attestation 3/3 覆盖；darwin/*-arm64 只能靠节点首用 attestation）。
⑤ **云机通道**：`nn-training/tests/test_ts_code_pack.py`（4 例：manifest 每个目标都进包且字节数/可执行位对、
   `.ts/.wasm/native-conv.ts/native-prebuilt.ts` 仍在包、同内容两次打包 sha 相同、缺目录响亮报错）；
   并在 `tests/test_remote_iter_real_bun.py`（真 bun 哨兵）里把 zip 解到临时树、**以它为 cwd 跑 rollout**，
   断言 shard manifest 的 `feat == "native"`（探针验过这条断言是活的：改成期望 wasm 会红）。
   `bun run check` / `bun run build` / `nn-python-gate.sh` 绿。
## §2026-09-21-goalnn-probe-negative-arm（2026-09-21，用户指令：负向臂——人类每局都给结论，直接用）

- **背景**：开局探针原口径是「存在性证明」：只能判「可动 / 未知」，`无解` band 在聚合时被降级成
  「未知（人类死无信息）」。于是探针**结构上说不出「这个种子别练了」**，而它唯一要服务的问题正是
  「杠杆在训练侧还是关卡侧」——一个永远说不出「不」的测量并不是在测量。
- **备选与否决**：① **K 人复核 + 能力对照局**——否：人类熟练玩家已是关卡难度的唯一尺子
  （AGENTS §0.2），单次判读即结论，再加一层「谁够格」的仪式只是把已收上来的数据重新解释一遍；
  ② **固定操作脚本**——否：脚本跑通只能证明「这局能过」（正向），脚本死了推不出「没人能过」，
  当不了负向臂；③ **God AI / 搜索当裁判**——否：违反「教师不是天花板」（§0.2），且 60 帧动作
  空间搜不完，不能当证明。
- **决定**：负向臂**不是新协议**，就是操作员手里已有的 `无解 + 理由` 按钮；聚合改三态，优先级
  **可解 > 无解 > 未知**（通关是构造性证据，优先于「没找到路」的判读），`无解` 由「未知」改为判
  **不可解**，并声明它**可被任一后续通关推翻**（同一 seed 后来者覆盖先前者）。
- **违反后果**：继续把 `无解` 降级成「未知」，训练侧会把「读得出无路」的种子当成可学目标反复烧
  算力，而探针永远给不出否决。
