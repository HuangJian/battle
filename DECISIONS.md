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
