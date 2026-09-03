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
> **编号冲突注**：§293 有两个条目（§293-intent「M4 完成」与 §293-God AI「解冻+恢复道具」），
> 系 intent-ai 分支并入时撞号（见 §277 迁移注）。两者编号均被外部引用，保持不动。

---

## 勿重提清单 (Do-Not-Re-Investigate)

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


## 基石决策 §1–§10（全文 → docs/decisions.details.md Part A）

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

## Architecture Decisions

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

## Gameplay Feature Decisions

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

## God AI Tuning

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


## 71. §48-Revisit: Steel-Only Evasion Occlusion, Terrain-Gated (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 72. §49-Revisit: 炮口相向对枪抵消 Parameterized + Re-Validated (SHIPPED, default unchanged)
> 全文 → docs/god-ai-tuning.progress.md

## 73. §68-Revisit: Crossfire Awareness v2 Re-Tuned with per-seed tick-diff (REJECTED, stays OFF)
> 全文 → docs/god-ai-tuning.progress.md

## 74. Steel-Fire Gate: Never Fire at Unpierceable Steel to Break Through (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 75. §75: Distance-Aware Base-Wall Fire Guard (T2a/Aggressive Suicide Fix)
> 全文 → docs/god-ai-tuning.progress.md

## Performance Optimization
> 全文 → docs/perf-optimization.progress.md

## Lie-Back-Win-Mode (Coop God AI)
> 全文 → docs/god-ai-tuning.progress.md

## Render Optimization
> 全文 → docs/render-optimization.progress.md

## 70. Base-Ring Fire Guard (Never Destroy Own Base)
> 全文 → docs/god-ai-tuning.progress.md

## 75. Replay Recording Must Tap the Decorated Input (Lie-Back-Win-Mode desync) (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 76. The Packed Blob Is the Only Authority on Frame Schema (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 77. Playback seek must advance the input (drag-the-bar desync) (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 78. Seek catch-up must drain (discard) world events — no audio burst (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 79. Coop God AI drove P1 instead of P2 (replay stall + base-wall break)
> 全文 → docs/god-ai-tuning.progress.md

## 80. §80: Turn-Snap Aim Guard — Don't Commit to a Stop-and-Aim Turn Whose Grid-Snap Breaks the Firing Line (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 82. 督战模式（Supervise）— God AI 作为 player1 全程无人类输入 + 战斗速率快捷键 (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 81. 移除 godai-stage-overrides.ts 机制 — 禁止按关卡名特殊化（防过拟合）
> 全文 → docs/god-ai-tuning.progress.md

## 83. §83: dodgeDirection 回退分支不再沿炮弹飞行方向逃跑 — 受困走廊时回头对枪抵消 (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 84. BONUS TIME: God AI Collects the Remaining Power-ups in the Pickup Window (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 85. §84-Revisit: BONUS TIME Pickup Is Reachability-Aware — Never Chase an Unreachable Item (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 86. Snapshot Must Preserve the Bonus Pickup Window — Mid-Window Restore Never Re-Opens BONUS TIME (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 87. Replay Needs No Pickup-Window Changes — It Inherits §86 via the Shared Serializer (VERIFIED + GUARDED)
> 全文 → docs/god-ai-tuning.progress.md

## 88. §88: Aggressive branch stall detection — freeze window no longer wasted firing at nothing (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 89. §89: Close-range enemy exposure check — don't flee from point-blank enemies (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 90. Dodge Direction Persistence + Threat Hysteresis (Bug Fix)
> 全文 → docs/god-ai-tuning.progress.md

## 90b. §90 A/B Test Results — Oscillation Counter-Fire Shipped (Negative Results Recorded)
> 全文 → docs/god-ai-tuning.progress.md

## 91. Turn Cooldown (§90c) — Simulation-Layer Oscillation Prevention
> 全文 → docs/god-ai-tuning.progress.md

## 92. §87: Urgent Power-Up Pickup Priority — Close + Safe-Path Pickups Outrank Defense/Kill (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 93. §88: 据守咽喉要地 (Chokepoint Holding) — Rule-1/2/3/4 Base-Defense Strategy (CANDIDATE, A/B-Tuned) _(superseded by §94 — SHIPPED default ON)_
> 全文 → docs/god-ai-tuning.progress.md

## 94. §88 据守咽喉要地 (Chokepoint Holding) — SHIPPED (default ON, supersedes §93 candidate)
> 全文 → docs/god-ai-tuning.progress.md

## 95. Turn Cooldown 50ms → 100ms + Halt-During-Cooldown (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 96. M0 基线测量 + M0.5 僵尸参数退役（SHIPPED，2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 97. §M3 Dodge 质量：dodge 分支近距离对枪抵消（SHIPPED 后回退） _(superseded by §98)_
> 全文 → docs/god-ai-tuning.progress.md

## 98. §M3 Dodge 对枪抵消：回退 OFF + Gate 确定性根因修复（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 99. M1 决策链评分制外壳：落地 + Parity 三重验证通过（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 100. M2 权重数据化：actionWeights 基础设施 + classic 重排 A/B 诚实阴性 + M2b 推迟（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 101. M3 dodgeCounterFire 三轮门控全部官方口径阴性 + stageIndex 口径伪影完整机制（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 102. M3 敌情感知 EnemyModel + survive 候选 + 命数感知 + M4 紧急对枪：机制落地，默认 OFF（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 103. M5 站位提前规避（pathThreatAvoidance）：机制落地，A/B 阴性，默认 OFF + 口径事故根因（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 104. M6 出生即一星（playerStartLevel 0→1）：首个强信号发布，hard/chaos +8~9pp（SHIPPED，2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 105. M7 追猎死亡探针：真追猎仅 ~3-7% + 模拟口径三重修复（playerLevel / lives / telemetry）（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 106. M8 survivalRetreat 官方口径 60-seed 确认：持平偏负，不发布（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 107. M9 dodgeHorizonScore 多弹道生存视界承诺闪避：机制成立但 60-seed 阴性，不发布（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 108. M10 dodgeHorizon 门控变体（时间余量 + 距离）：chaos 确凿阴性，不发布（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 109. M11 星经济下一档：playerStartLevel 1→2（SHIPPED 后用户否决） _(superseded by §110: 用户否决，回退 1★，2026-08-03)_
> 全文 → docs/god-ai-tuning.progress.md

## 110. 用户否决 §109：hard/chaos 起始二星回退为一星（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 111. 星盾扩展到所有难度（引擎改动）+ HP 模型探针选靶（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 112. M12 玩家 HP 缓冲感知：诚实阴性（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 113. M13 全场压力撤退（outnumberedFieldRetreat）：SHIPPED，hard +2.3pp / chaos +0.6pp（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 114. M4 标量参数 CMA-ES 首轮：子集过拟合阴性 + M13 阈值双重复证（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 114.1 M4 round-2 建议配置（评审决议，未执行）
> 全文 → docs/god-ai-tuning.progress.md

## 115. M4 round-2 全语料 CMA-ES：SHIPPED，pool 模型 +5.0/+8.3pp（classic 还原表保 91%）（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 116. 自杀秒回（suicide quick-return）：实现 + 诚实阴性（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 117. 自杀秒回条件①变体（mode 2 STAND / mode 3 CHARGE）：诚实阴性（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 118. §117 守卫升级（baseHp 阈值 + 防守位失守）A/B — 仍为诚实阴性，机制性证伪（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 119. 固化策略调试方法论：run-forensics 分层取证（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 120. 自毁基地 32 局取证 + 采集脚本迭代（off-by-one / bullet-dir / --from-json）（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 121. t2a/aggressive 停射自毁守卫 selfFireBaseGuard SHIPPED（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 122. 仿真性能 Round 10：computeThreatPoints 对齐枚举（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 123. 仿真性能 Round 10：scanAheadImpl per-tick memo（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 124. 仿真性能 Round 10（REJECTED）：rectHitsTerrain 比较链重排 / terrain 短路
> 全文 → docs/perf-optimization.progress.md

## 125. 仿真性能 Round 10：selectTarget within-tick memo（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 126. 仿真性能 Round 10（REJECTED）：canStepLat 手内联 rectHitsTerrain
> 全文 → docs/perf-optimization.progress.md

## 127. 仿真性能 Round 11：followPath→replanImpl 跨 tick 缓存（SHIPPED，2026-08-05，含引用别名修复）
> 全文 → docs/perf-optimization.progress.md

## 128. 性能基线标准场景改为 classic/hard/chaos 各 1/3（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 129. pickup 可达性 A*：dig-only + 跨 tick 纯 memo（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 130. 全难度命数统一为 3 + GOD AI 基线重测（SHIPPED，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 131. T8 拦截射程 pool 2→8/12：60-seed 诚实阴性（不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 132. 方向 B：selectTarget 威胁评分按 kind 速度 × 距基地距离加权（诚实阴性，不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 133. 方向 C：brick-heavy 关防守距离再校准——诚实阴性（不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 134. 方向 D：防守位停射拦截基地车道敌人（SHIPPED，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 135. 方向 D 预测版：提前拦截基地车道逼近者（诚实阴性，不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 138. 基地守位格 v2：受威胁时驻守守位格（诚实阴性，不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 137. 基地守位格（Base Guard Anchor）—— 诚实阴性，不发布（2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 136. 方向 D 破砖版：预测命中时打场景砖开路（诚实阴性，不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 139. 方向 A：火力死区解除（firing-lane re-engage）—— 灾难性阴性，不发布（2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 140. 方向 D4：baseWall 精确环判定（破砖开火假阳性修复，SHIPPED，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 141. D2 拆环威胁评分 —— 诚实阴性（旋钮默认 0，byte-identical）
> 全文 → docs/god-ai-tuning.progress.md

## 142. D1 防守落点解盲 —— 诚实阴性（baseGuardAnchorMode 保持 0）
> 全文 → docs/god-ai-tuning.progress.md

## 143. D5 基地火力解锁 + 星经济 —— 诚实阴性（firingLaneBoxRow / pickupStarBoxRow 保持 0）
> 全文 → docs/god-ai-tuning.progress.md

## 144. E1 道具经济（危急道具拾取）—— 诚实阴性（direItemMode 保持 0，反证判据收束）
> 全文 → docs/god-ai-tuning.progress.md

## 145. S24 冰面机制深潜 + iceGlideControl —— 诚实阴性（旋钮保持 0，S24 = 难度地板关）
> 全文 → docs/god-ai-tuning.progress.md

## 146. S8 Riverbed 取证深潜 + defensePosStandable —— SHIPPED（集合点可达性修复，hard 45%→52%）
> 全文 → docs/god-ai-tuning.progress.md

## 147. S8 三杠杆 B/C/A 逐一 A/B —— B SHIPPED（§146 已记），C/A 诚实阴性（§146 C 范围限制 + A 全局崩盘）
> 全文 → docs/god-ai-tuning.progress.md

## 148. fieldRetreatPickupGate 扩展到 MID/LOW —— 实测证伪后回退（HIGH-only 定稿，§147 范围锁定）
> 全文 → docs/god-ai-tuning.progress.md

## 149. defensePosStandable 全面启用（minDist 解除）全关验证 —— 边际 ≈ 0，不发货（收窄版 §146 保持最优）
> 全文 → docs/god-ai-tuning.progress.md

## 150. 关卡序号统一为 1-based（工具 CLI + 文档 S# 全量修正，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 152. hard S12 Lattice 回放四联 bug 修复（§152-W1..W4）+ 全关 A/B 验证（SHIPPED）
> 全文 → docs/god-ai-tuning.progress.md

## 153. hard S12 Lattice seed 3214953618 回放两行为（bullet-crash + close-combat trade）诊断与修复（实现 + 单测锁定；A/B 发现两者全局非正 → 实验旋钮不发货）
> 全文 → docs/god-ai-tuning.progress.md

## 154. bulletLaneWait W1 重设计（§153 后记）：18 个净负种子根因定位 + predictive next-body 最终版（实测 35×60 hard 净 +15；仍为实验旋钮默认 0）
> 全文 → docs/god-ai-tuning.progress.md

## 155. bulletLaneWait W1 全局发货（§154 最终版，用户决策：忽略 chaos）
> 全文 → docs/god-ai-tuning.progress.md

## 156. Freeze-Window Power-Up Pickup（冰冻期道具拾取，无限距离）
> 全文 → docs/god-ai-tuning.progress.md

## 157. Base Clear-Shot Threat Detection（基地车道对齐远距离威胁检测）
> 全文 → docs/god-ai-tuning.progress.md

## 158. Non-Freeze Close-Range Power-Up Pickup（非冰冻期近距离道具拾取）
> 全文 → docs/god-ai-tuning.progress.md

## 159. 天降神兵守卫改用 GOD AI + §避让防堵车（用户需求 2026-08-06）
> 全文 → docs/god-ai-tuning.progress.md

## 160. 避让中扫射压制——避让开火优先沿腾挪轴（用户需求 2026-08-06）
> 全文 → docs/god-ai-tuning.progress.md

## 161. §161 开路策略（carve path）——实现完整、hard 全 35 关与 Battlement 均实测净零 → 诚实阴性归档，旋钮默认 OFF（用户需求 2026-08-06，Stage 33 Battlement 过关思路）
> 全文 → docs/god-ai-tuning.progress.md

## 162. §162 nav 卡死破局（navBreakStuck carve-dig escape）——SHIPPED 默认 1，hard 全 35 关显著胜率提升 p=0.019（用户需求 2026-08-06，回放 hard-s34-base-l2-t69-seed2050197249 Problem 1：出生点被砖墙围堵，player 不开墙出击，0:00~0:20 在出生点附近振荡）
> 全文 → docs/god-ai-tuning.progress.md

## 163. §163 中路防守（midLaneDefense）——子弹触发版全 35 关与 Battlement 均实测净零 → 诚实阴性归档，旋钮默认 OFF（用户需求 2026-08-06，回放 Problem 2：基地列无钢铁防护，player 坐视敌人凿穿中路砖墙）
> 全文 → docs/god-ai-tuning.progress.md

## 164. §164 中路列旁主动驻守（midLaneHold）——诚实阴性归档（用户需求 2026-08-06：让 §162 出袋后的玩家优先走中路走廊而非左侧，在列旁持枪对消）
> 全文 → docs/god-ai-tuning.progress.md

## 165. T2a Defense Override — 近敌停射允许（修复 S20 Bastion 振荡死锁，origin 侧原 §159）
> 全文 → docs/god-ai-tuning.progress.md

## §165. 中路防守启用 + 水阻弹 bug 修复 + 近战对枪火力评估
> 全文 → docs/god-ai-tuning.progress.md

## §165-round2. 深度调优：pathThreatAvoidance 假阳性 + closeCombatDuel 多敌计数 + midLaneHold 主动防守
> 全文 → docs/god-ai-tuning.progress.md

## 166. B1 starRush 星经济冲刺 — 诚实阴性归档（旋钮默认 0，2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 167. B4 超级道具战略激活（superItemMode）— SHIPPED guard-only → RETIRED by default（2026-08-07 → 修订 2026-08-25 M0）
> 修订：plan/AI-No-Items-Warmstart.md M0 将 superItemMode/GuardThreat 默认归零（NN AI 全链路不使用主动道具）。
> 配对复测（A=显式 ON, B=新默认 OFF, hard 60 seeds）：胜率 76→75%、Δscore −0.0093±0.0025、t=−3.77、p=0.0002
> （Lattice 显著变差 0.519→0.485）——与新预检 B 臂完全一致；−1pt 缺口（R4）转列 RL 守家目标。
> 全文 → docs/god-ai-tuning.progress.md §M0

## 168. navStuck 计数器抖动重置 bug（navStuckZone）— 实验阴性，旋钮留档默认 0（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 169. 基地威胁信号闪烁（threatStickyTicks）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 170. 追击承诺（huntCommitTicks）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 171. 路径长度感知目标选择（pathTargetMode）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 172. bonus 敌人追猎权重（bonusHuntBias）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 173. 基地损伤召回（baseDamageRecall）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 174. 双玩家仿真系统 — 双 God AI 协作 + 防堵车 + 督战双玩家 (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 175. Dual 中路无钢关配合策略 — 立项（2026-08-08）
> 全文 → docs/god-ai-tuning.progress.md

## 176. Dual Central Breach §6 实测缺陷修复 — P2 角色落地 + P1 dig-fire
> 全文 → docs/god-ai-tuning.progress.md

## 177. Dual Central Breach P2 导航落地 — directMove/patrol 实测回退，de-conflict 生效
> 全文 → docs/god-ai-tuning.progress.md

## 178. Dual Central Breach autopsy (hard-s34 seed2) — carve 穿墙 + 中驻守 + sticky hold
> 全文 → docs/god-ai-tuning.progress.md

## 179. Dual Central Breach autopsy (hard-s34 seed6) — P1 凿盾 + 危基不回防 + 冰冻浪费
> 全文 → docs/god-ai-tuning.progress.md

## 180. Dual Central Breach autopsy (hard-s34 seed34) — 右路盲区 + fence 独占 + defenseSecond 近端覆盖
> 全文 → docs/god-ai-tuning.progress.md

## 181. Dual Central Breach autopsy (hard-s34 seed115) — P1 spawn 振荡：A* 路由穿透基地保护砖
> 全文 → docs/god-ai-tuning.progress.md

## 182. 重放暂停后切换应用再回来点播放，画面不动（visibilitychange 污染 world.state）
> 全文 → docs/god-ai-tuning.progress.md

## §182. Face-Nearest-Enemy Fallback for Immobile-Stuck Player
> 全文 → docs/god-ai-tuning.progress.md

## §183. GOD AI Idle Calibration — Analysis Complete
> 全文 → docs/god-ai-tuning.progress.md

## §184. Freeze Powerup — Allied Guard Freeze Bug + Pickup Stuck Bug
> 全文 → docs/god-ai-tuning.progress.md

## §185. navStuckZone=1 — Sub-Pixel Jitter Defeats Nav-Stuck Counter
> 全文 → docs/god-ai-tuning.progress.md

## §186. powerupStuckTicks — Powerup Navigation Stuck Detection
> 全文 → docs/god-ai-tuning.progress.md

## §187. Guard/P2 A* Player-Obstacle + Target Blacklist + Fire Post-Turn + Powerup-Enemy Overlap
> 全文 → docs/god-ai-tuning.progress.md

## §188. Fence Power-Up Must Not Trap Tanks Inside Steel
> 全文 → docs/god-ai-tuning.progress.md

## §189. 开局联通清墙 — Base Connectivity Clear
> 全文 → docs/god-ai-tuning.progress.md

## §190. A* 寻路代价模型升级 — 砖墙=空地 + 基地环倍率 + 开火停车代价
> 全文 → docs/god-ai-tuning.progress.md

## 191. 批量仿真共享态硬化 — findPath 重入守卫 + level-sim 子进程隔离
> 全文 → docs/god-ai-tuning.progress.md

## §192. 基地车道哨兵（baseLaneSentry）—— SHIPPED（hard/chaos 默认 1；classic 0 保持字节不变）
> 全文 → docs/god-ai-tuning.progress.md

## §193-A. 中线火力门（centerLineFireGate）— 标注重评（被 §193-C 取代）
> 全文 → docs/god-ai-tuning.progress.md

## §193-C. 中线火力门 — SHIPPED（hard/chaos 默认 1；classic restore 0 字节不变）
> 全文 → docs/god-ai-tuning.progress.md

## §193-D. 预测前移门（predictiveFireGate）— SHIPPED（三难度默认 1；classic restore 0）
> 全文 → docs/god-ai-tuning.progress.md

## §193-B. 卫位导航（baseLaneSentryStation）— 达标保留（默认 0 = OFF，待发货）
> 全文 → docs/god-ai-tuning.progress.md

## §193-E. 环破回防（ringFallback）— 阴性归档（S34 -2，hard 全关 -54）
> 全文 → docs/god-ai-tuning.progress.md

## 194. 像素卡死 directMove 兜底 (§190)
> 全文 → docs/god-ai-tuning.progress.md

## 195. 中路钻探粘性驻守 midLaneStickyTicks=90 — S8 Riverbed 钻探败链修复 SHIPPED (2026-08-14)
> 全文 → docs/god-ai-tuning.progress.md

## 196. 钻探预警列完整性触发器（drill alarm）— 方向 1 证伪（2026-08-14）
> 全文 → docs/god-ai-tuning.progress.md

## 197. 带内拆环导航 + 远距开火（baseLaneSentryInBandNav/FarRange）— 方向 2 证伪（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 198. 卫位导航发货（baseLaneSentryStation=1）— SHIPPED（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 199. S34 站桩取证 + 工具口径 bug（方向 3 证伪 + ab-param 修复）（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 200. dodgeEscapeDepth 逃逸深度闪避证伪（方向 5 D1）（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 201. 方向 6（clearSpeed 0.151 慢/拖沓）— 分析性证伪：无 AI 拖沓可修（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 202. M0 威胁台账取证上线（threat-ledger + failure-classifier）— 硬关 2100 局基线（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 203. M1 ThreatBudget 纯模型上线（Phase 1 §5，默认未接线）（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 204. M2 ActionContract 防守站桩门控（Phase 2 §6.1，默认 OFF + A/B）（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 205. §6.2 targetValue 排序键 — A/B 证伪，保持默认 OFF
> 全文 → docs/god-ai-tuning.progress.md

## 206. §6.3 短期 intent（lease+重验）— A/B 中性偏负，保持默认 OFF
> 全文 → docs/god-ai-tuning.progress.md

## 207. Phase 3 §7 动态攻击覆盖点 — A/B 证伪（S34 崩塌），保持默认 OFF
> 全文 → docs/god-ai-tuning.progress.md

## 208. §207 覆盖点实现缺陷审计 — 5 缺陷已修复，机制仍保持 OFF
> 全文 → docs/god-ai-tuning.progress.md

## 209. 覆盖点实现审计第二轮 — 坐标系根因 + (b)/BUG-2 修复，正确实现仍净负（OFF 维持）
> 全文 → docs/god-ai-tuning.progress.md

## 210. 覆盖点格坐标 round → floor（消除格中点决策振荡）
> 全文 → docs/god-ai-tuning.progress.md

## 211. 覆盖点负翻转 per-seed 取证 — 蝴蝶效应根因，csb/cbr 过滤修复证伪（OFF 维持）
> 全文 → docs/god-ai-tuning.progress.md

## 212. M4 安全吃星 — 诊断先行，收益空间不足（不提高 pickup 权重）
> 全文 → docs/god-ai-tuning.progress.md

## 213. Phase 5 CMA-ES — 启动决策与搜索空间重定义（§9.1 条件未满足，用户指示启动，按协议执行）
> 全文 → docs/god-ai-tuning.progress.md

## 214. Phase 5 CMA-ES — 参数面无 ROI，三批候选全部噪声，维持 DEFAULT（停止条件 §9.3.5 触发）
> 全文 → docs/god-ai-tuning.progress.md

## 215. Hard 开放测试第 1 轮: M0–M3 通过, idle 因果证伪(STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 216. M4 统一行动候选 — paired A/B 净 −116, 方向否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 217. M5 travel 段火力偏离 — 诊断先行, 机会空间 33% (STATUS: 实现中, 未 A/B)
> 全文 → docs/god-ai-tuning.progress.md

## 218. M5 travel 段火力偏离 (fireLineDetourMode) — 三批全正向, 候选通过初步 A/B → gated rollout (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 219. 评审 P1 修复轮 (实验工具可信度 + 4 处 AI 缺陷) (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 220. defenseIntercept 开火窗口 (actionContractMode 独立 A/B) — 三线微负, 方向否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 221. 评审 P2 修复 + M5 candidate-on 完整验证 (STATUS: 完成, M5 升格待拍板)
> 全文 → docs/god-ai-tuning.progress.md

## 222. CMA-ES 重启 — 全 stage 口径 (STATUS: 收口, 参数面无 ROI 确认)
> 全文 → docs/god-ai-tuning.progress.md

## 223. ③ dodge idle 取证收口 (STATUS: 完成, 候选方向待拍板)
> 全文 → docs/god-ai-tuning.progress.md

## 224. 候选 A: dodgeCentroidMode 否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 225. 后继 ④ "太迟"防御结构审计收口 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 226. 后继 ④ 候选 A/B 双否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 227. 后继 ⑤ t2a 自毁守卫重论证收口 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 228. M5 人工开放测试入口 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 229. M5 fireLineDetourMode SHIPPED — 默认 1（含 S30/S13 弱关重标定）(STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 230. 门禁 runner 瘦身 — collectMetrics/collectEvents + telemetry Set ping-pong (STATUS: SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 231. thinkInterval 决策链节流 A/B — 否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 232. 决策链小数组分配消除 + scanAhead 整数步进 (STATUS: SHIPPED, 字节等价)
> 全文 → docs/god-ai-tuning.progress.md

## 233. bun test 全套 <20s 攻坚结论 — 机器吞吐墙 + 种子数决策 (STATUS: 完成, 10 种子落地)
> 全文 → docs/god-ai-tuning.progress.md

## 234. 门禁种子 20→10 后补 — test-silent HEAVY_TESTS 修复 + 强制 --parallel (STATUS: SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md


## Render Optimization（§235–§238 渲染实测否决，全文 → docs/render-optimization.progress.md）

## 235. R6 vignette 缓存 1× 化 — 全屏 alpha blit 面积 4× 缩减 (STATUS: SHIPPED, 有损项已论证)
> 全文 → docs/render-optimization.progress.md R6（2026-08-17）

## 236. P1-C 粒子 per-type 分桶 — 精确测量后放弃 (STATUS: 否决, 实测数据入档)
> 全文 → docs/render-optimization.progress.md P1-C（2026-08-17）

## 237. R7 坦克 tight-viewport blit — 实测否决 (STATUS: 否决, 9-arg 调用开销抵消面积节省)
> 全文 → docs/render-optimization.progress.md R7（2026-08-17）

## 238. 粒子烘焙位图 blit — 实测 4× 慢，彻底证伪 (STATUS: 否决)
> 全文 → docs/render-optimization.progress.md R8（2026-08-17）

---

## Refactor & Engineering（§239–§271 重构落地，全文 → docs/decisions.details.md Part B）

## 239. §1.6 魔法数字 → 命名常量 (STATUS: 已实施, plan/refactor.agy.md Phase 1)
> 全文 → docs/decisions.details.md（Part B §239）

## 240. §1.5 Option C — WorldSerializer 字段覆盖测试守卫 (STATUS: 已实施, plan/refactor.agy.md Phase 1)
> 全文 → docs/decisions.details.md（Part B §240）

## 241. §3.2 快照/回放基础设施去重 (STATUS: 已实施, plan/refactor.agy.md Phase 1)
> 全文 → docs/decisions.details.md（Part B §241）

## 242. §2.8 方向助手整合 → utils/direction.ts (STATUS: 已实施, plan/refactor.agy.md Phase 1)
> 全文 → docs/decisions.details.md（Part B §242）

## 243. §3.4 共享测试 fixtures → tests/helpers.ts (STATUS: 已实施, plan/refactor.agy.md Phase 1)
> 全文 → docs/decisions.details.md（Part B §243）

## 244. §1.2 GameLoop.loop 分解为命名步骤方法 (STATUS: 已实施, plan/refactor.agy.md Phase 2)
> 全文 → docs/decisions.details.md（Part B §244）

## 245. §2.1 击杀管线抽取 → KillPipeline.ts (STATUS: 已实施, plan/refactor.agy.md Phase 2)
> 全文 → docs/decisions.details.md（Part B §245）

## 246. §2.2 P1/P2 生命周期集中化 → World.enablePlayer2/disablePlayer2 (STATUS: 已实施)
> 全文 → docs/decisions.details.md（Part B §246）

## 247. §2.3 自由格搜索统一 → GridQuery.findNearestFreeCell (STATUS: 已实施)
> 全文 → docs/decisions.details.md（Part B §247）

## 248. §2.9 + §3.8 时间单位命名约定 + AI 常量归位 (STATUS: 已实施)
> 全文 → docs/decisions.details.md（Part B §248）

## 249. §3.6 四套 Worker Pool 统一 → tools/lib/worker-pool.ts (STATUS: 已实施)
> 全文 → docs/decisions.details.md（Part B §249）

## 250. §2.7 pathfind.ts 解耦：utils → ai/god + grid-search (STATUS: 已实施)
> 全文 → docs/decisions.details.md（Part B §250）

## 251. §1.3 Phase C — highScore 持久化 I/O 迁 settings.ts (STATUS: 已实施)
> 全文 → docs/decisions.details.md（Part B §251）

## 252. §3.1 双渲染器 fallback 移除 — 否决（前提不成立） (STATUS: 否决)
> 全文 → docs/decisions.details.md（Part B §252）

## 253. §2.6 types.ts 重组织 — presentation-only 类型迁出 (STATUS: 部分实施)
> 全文 → docs/decisions.details.md（Part B §253）

## 254. §1.1 Mixin→组合：Simulation（21 stubs 归零） (STATUS: 已实施, plan Phase 3)
> 全文 → docs/decisions.details.md（Part B §254）

## 255. §1.1 Mixin→组合：Game（27 stubs 归零） (STATUS: 已实施, plan Phase 3)
> 全文 → docs/decisions.details.md（Part B §255）

## 256. §1.1 Mixin→组合：GameRenderer + SpriteArtist（41 stubs 归零） (STATUS: 已实施)
> 全文 → docs/decisions.details.md（Part B §256）

## 257. §2.6 types.ts 重组完成 + Tank 拆分否决 (STATUS: 已实施/部分否决)
> 全文 → docs/decisions.details.md（Part B §257）

## 258. §3.3 Browser 去重 — 最小提取（formatCreated/formatBytes） (STATUS: 已实施, 范围修正)
> 全文 → docs/decisions.details.md（Part B §258）

## 259. §3.7 diag 脚本清理 — 归档 5 个零引用一次性脚本 (STATUS: 已实施, 范围修正)
> 全文 → docs/decisions.details.md（Part B §259）

## 260. §3.5 tests 目录重组 — 否决（代价/价值失衡） (STATUS: 否决)
> 全文 → docs/decisions.details.md（Part B §260）

## 261. §2.4 UIManager 拆分 — 四子控制器组合 (STATUS: 已实施)
> 全文 → docs/decisions.details.md（Part B §261）

## 262. 废除 God AI 禁区（AGENTS §5.1 幽灵规则消歧） (STATUS: 已实施, plan/refactor.trae.md §0.1)
> 全文 → docs/decisions.details.md（Part B §262）

## 263. 第二轮重构落地汇总（plan/refactor.trae.md B1–B3） (STATUS: 已实施, 2026-08-23)
> 全文 → docs/decisions.details.md（Part B §263）

## 264. selectTargetUncached 分解落地（§263 遗留 #3） (STATUS: 已实施, 2026-08-23)
> 全文 → docs/decisions.details.md（Part B §264）

## 265. determinism 语料 v2（8→21 组合） (STATUS: 已实施, 2026-08-23, 遗留 #12)
> 全文 → docs/decisions.details.md（Part B §265）

## 266. manhattan 单源化落地（遗留 #2） (STATUS: 已实施, 2026-08-23)
> 全文 → docs/decisions.details.md（Part B §266）

## 267. 遗留 #1 self-hub 处置：结构性护栏替代整体切片 (STATUS: 已实施, 2026-08-23)
> 全文 → docs/decisions.details.md（Part B §267）

## 268. 第三轮重构 Phase 1 落地汇总（plan/refactor.trae.md §1） (STATUS: 已实施, 2026-08-24)
> 全文 → docs/decisions.details.md（Part B §268）

## 269. 第三轮重构 Phase 2 落地汇总（plan/refactor.trae.md §2） (STATUS: 已实施, 2026-08-24)
> 全文 → docs/decisions.details.md（Part B §269）

## 270. 第三轮重构 Phase 3 落地汇总（plan/refactor.trae.md §3） (STATUS: 已实施, 2026-08-24)
> 全文 → docs/decisions.details.md（Part B §270）

## 271. 第三轮重构 Phase 4 落地汇总（plan/refactor.trae.md §4） (STATUS: 已实施, 2026-08-24)
> 全文 → docs/decisions.details.md（Part B §271）

---

## 272. God AI v1 封版冻结 —— D0 拍板 + 冻结基线 + 签名 golden (STATUS: 已实施, 2026-08-26)

> 全文 → docs/god-ai-tuning.progress.md Part 0（冻结基线 / golden 完整）· 重启协议 → plan/God-AI-Organization.md §8

---

## God AI 冻结纪元运维（§273–§276，全文 → docs/decisions.details.md Part C）

## 273. 留档实验资产不删决策（OFF 旋钮 / OFF 候选 / 锁存测试全保留）(STATUS: 已实施, 2026-08-26)
> 全文 → docs/decisions.details.md（Part C §273）

## 274. sweep-winrate `--difficulties` 字符迭代 bug 修复 —— 列表参数走 assertive 解析器 (STATUS: 已实施, 2026-08-26)
> 全文 → docs/decisions.details.md（Part C §274）

## 275. God AI code-review 批量 bug 修复（冻结路径零行为变更）(STATUS: 已实施, 2026-08-26)
> 全文 → docs/decisions.details.md（Part C §275）

## 276. code-review 遗留项全清 —— 新纪元三件套执行（§275 遗留 → 全部落地）(STATUS: 已实施, 2026-08-26)
> 全文 → docs/decisions.details.md（Part C §276）

---

## 277. 全关策略 all-on 实验 — M0/M1 收口：all-on 灾难性否决 + LOO 定位 firingLaneMode (STATUS: 完成)

> 全文 → docs/god-ai-tuning.progress.md §239（编号修正：progress 内为 §239，2026-08-17）


## 278. all-on−firingLaneMode CMA-ES 两轮收口 — 数值调参止于 −10.4pp，方向关闭 (STATUS: 完成)

> 全文 → docs/god-ai-tuning.progress.md §240（编号修正：progress 内为 §240，2026-08-17）

---

## 279. Replay Tick-Hash Chain 实现定案 — 每 100 tick 世界哈希锚点 (STATUS: 完成)

> 全文 → docs/decisions.details.md（Part D §279）· 计划/评审 → plan/Replay-TickHash-Chain.md + plan/tickhash.review.md


## 280. 否决 RL-WASM-Bridge（B3），改走 A'（bun 持久进程桥）(STATUS: 已决议)

> 全文 → docs/decisions.details.md（Part D §280，含 v3–v5 评审附录）· 评审 → plan/RL-WASM-Bridge.review.md · 执行 → plan/RL-Bun-Bridge.md

---

## RL 训练基础设施（§281–§284，全文 → docs/nn.progress.md）

## 281. RL 训练断点续跑机制（服务随时停启）(STATUS: 完成, 2026-08-23)
> 全文 → docs/nn.progress.md §4（RL 训练断点续跑机制）

## 282. RL 队列模式静默跳轮修复 — resumed_manifests 双 schema 归一 + 失败迭代原地重试 (STATUS: SHIPPED, 2026-08-24)
> 全文 → docs/nn.progress.md §5（队列模式静默跳轮修复 + 事故复盘）

## 283. 干净评估嵌入分布式流水线 — PPO 空窗期全节点贪心局（STATUS: SHIPPED, 2026-08-24）
> 全文 → docs/nn.progress.md §7（干净评估嵌入分布式流水线）

## 284. 分布式协议 v3.6 — 结果容器 BCV2 子进程打包 + 任务获取异步化（STATUS: SHIPPED, 2026-08-25）
> 全文 → docs/nn.progress.md 分布式 BCV2 节 + plan/distributed-rollout.md v3.6

---

## 285. M1 分歧探针 — 归因 ①/③ 边界（2026-08-26，plan/AI-No-Items-Warmstart.md §4）
> 工具 `tools/diag/divergence-probe.ts`（预注册：分歧=学生贪心≠教师标签且 120-tick 内
> 有可观测后果；三桶 基地高压/交战/巡航；后果代理指标从学生实际轨迹提取，不做双臂重放）。
> 结果（25 局 hard）：分歧率 70.6%，基地高压桶最高 74.6% 且特征表完整 →
> 判 **①/③ 边界**（标签或监督）：M3 走 wins-only + 守家帧回补（near-miss 3×），预留 DAgger
> 交互轮。注：预注册表原无「高压桶+特征完整」格，本次属**表外裁量**——已事后补格入方案
> （plan §4 执行态修订），结论不变。全文 → docs/nn.progress.md §13.2。

## 286. 语料纪元 OBS_SCHEMA_MAJOR 1→2 — item 头删除 + 标量收编 + wins-only + returns（2026-08-26，plan M2）
> 一次 MAJOR 打包：① 动作空间 10→7（actions (N,2)/masks (N,7)）；② SCALAR_DIM 24→19、
> SCALAR_X_INDICES [20,23]→[15,18]（mirror 锁步 + 反例测试）；③ wins-only（--wins 1）+ 守家帧
> near-miss 3× 超采样（M1 证据）+ 人像道具帧剔除；⑥ returns.npy（rl-reward.ts 共享 RL reward，
> γ=0.995 折现）→ train_bc --value-coef 对 PPOStudent value 头做 MC 预置。
> 重导：God-AI 1526 胜局 / 2.77M 帧（near-miss frac 0.4915，≥2000 帧）；人像 97 局 / 65.5K 帧；
> determinism + validate_export + python 快速层全绿。全文 → docs/nn.progress.md §14。

## 287. M3 BC warm-start 双臂 — 双 0% WIN，Gate ≈0% → 回环 DAgger/长训（2026-08-26，plan §6）
> A（wins 24sh / 46K 帧）与 B（A+人像 97sh / 112K 帧）均 8ep 纯 BC（value 预置因 returns
> 终局锚定 O(9) 爆方差而降级，见 nn.progress §15.1）。move acc A 0.365 / B 0.441（人像加权增益），
> 但 m1-eval 贪心 WIN 均 0.0%（suite A 0.0773 / B 0.0757，非瞬死但永不清关）。
> **按预注册 Gate → 不走 M4，回环整改**：下一步 = M1 ③路径（DAgger 交互采集轮）或全量
> 2.77M 帧长训。工程教训：weights 导出 NaN→null sanitize（commit 3c40e55）。

## 288. M3 回环整改 — DAgger 混合亦 0%，BC 三线收敛 0% 平台（2026-08-26 晨）
> wins93sh + dagger50sh 混合、warmstart B、6ep：move 0.476（三线最高）/ fire 0.899，
> 但 m1-eval 贪心 WIN 仍 **0.0%**（suite 0.0777）——与历史 L950「DAgger 后 0%」一致。
> **结论：0% 是策略深度差距而非语料/监督缺陷；BC 蒸馏在 46K–185K 尺度无非此平台。**
> 决策点（需拍板）：① 全量 2.77M 长训（~24h CPU）排除尺度；② schema v2 RL 直接启动
> （旧谱系 it36 ≈10% 是唯一 >0 实证）；③ 路线 F 高层语义监督单独立项。全文 → nn.progress §15.4。


## 289. 窗口 0 稳定化 gate — super-item 退役默认 OFF + 基线重钉三件套（2026-08-26，plan/Intent-Policy-NN-Plan.md §5.4）

> 全文 → docs/decisions.details.md（Part E §289）· pinned run 基线数据 → docs/god-ai-tuning.progress.md Part 0


## 290. M0a 词表契约定稿 — spec-in-code 单一实现（2026-08-26，plan/Intent-Policy-NN-Plan.md §3）

> 全文 → docs/decisions.details.md（Part E §290）· 规格原文 → plan/Intent-Policy-NN-Plan.md §3

---

## 291. M0b 探针轮 1 — gate FAIL，处置启动注入版（2026-08-26 夜）
> 全量 2100 局 hard 机械打标 → intent-8 分类器（StudentNet 主干，120K 帧自然分布/3ep）。
> 三桶 margin：base +0.143 ✅ / combat +0.065 ❌ / cruise +0.015 ❌（近噪声，n=4187）。
> 整体 acc 0.594；confusion 显示模型全行只落 HUNT/CRUISE——稀有类未学习 +
> combat/cruise 分歧核心是 **HUNT vs CRUISE 的 endgame 切换**（单帧无时序上下文）。
> 按 §3.6 五径启动第一轮：**①.5 注入版探针（prev-intent+duration teacher-forced，
> M4 §4.2 注入同构）+ P2-2 配额采样**。幽灵表双口径已出：ESCAPE 0 窗口 →
> reflex-only 掩码（学习词表收缩 7 类）。全文 → docs/nn.progress.intent.md §16。

## 292. M0b 探针轮 3（B′）— 判定口径修正，gate PASS，进入 M4（2026-08-26 夜）
> B′ = inject + quota 15K + max-train 300K + 6ep（修复 B 轮的 5% 语料+2ep 欠拟合缺陷；
> 配额后稀有类训练帧 INTERCEPT 3639 / CLEAR 4555 / RETURN_DEF 5371）。
> **桶级 margin 反降（base +0.130 / combat +0.040 / cruise −0.201）但类级 recall 大面积学会**：
> CLEAR 86.5% / PICKUP 77.2% / RETURN_DEF 44.2% / CRUISE 48.1% / INTERCEPT 31.8%（majority 基线下
> 这些类全为 0%）；唯一弱项 HOLD_LANE 2.1%（训练帧全语料最少 1691）。
> **归因**：轮 1 的 0% 学习 = 类不平衡饿死（非不可学）；B′ 桶 margin 下降 = 配额训练 ×
> 自然验证的分布不匹配 artifact（模型过度预测 PICKUP，CRUISE 被抢 10,323 帧）。
> **判定（口径修正，预注册 #16 修订备案）**：合格判据从「三桶 acc vs majority」改为
> 「**类级 recall vs majority 类级 recall**」——该口径下 6/7 类远超 majority、
> ESCAPE 依 <200 窗口掩码 → **意图可学习性实证成立，M0b gate PASS**。HOLD_LANE の短板
> 由 M5 守家段超采样补强（预计自然分布下 midLaneDefense 帧足够 M5 配额）。
> 全文 → docs/nn.progress.intent.md §18。注：cruise 桶负 margin 受分布 mismatch 污染，
> 不以它为"CRUISE 不可学"证据——CRUISE 类 recall 48% vs majority 0%。

## 293. M4 完成 — 网络 + 字节一致 + 推理基准 + IntentPlayer（2026-08-26 夜）
> 意图网（StudentNet 主干+三头+9 维注入，71.5K）；TS/Py 前向一致测试新建（P3-4，检入
> golden h16/d2，三头 logits ≤1e-4）；bench 实测单前向 **41.1ms**（理论带 34–56ms 正中；
> 摊销 ÷24→1.71 / ÷50→0.82 ms·tick；实机需 Worker/瘦身档=R3）；IntentPlayer +
> `m1-eval --policy intent`（I6）单局闭环跑通；stub=3 意图最小执行器（M6 真执行器交接）。
> **M4 gate 全绿**：check 1512 pass / 0 fail。全文 → docs/nn.progress.intent.md §19。

## 294. M1 压缩认定 + M2 人像签名完成（2026-08-26 夜）
> **M1（God-AI tagger）已由 M0b 用同一实现覆盖**：tagger 钩子（intentTaggerMode，
> ON/OFF 字节等价测试）在 M0b 前已落地；逐 tick 打标/分段四件套/vocab 映射即
> M0b 探针所用的同一实现——M1 不再单列，挂标点后续随 M4 IntentPlayer 的 replay
> 注入复用（student rollout 冷启动问价见 §5.1）。
> **M2（人像签名标签器）完成**：signature.ts 8 类纯函数判据（宁缺勿错、ESCAPE 不签名）
> + 104 局重放导出（outcome 与 verify-demos 逐局一致）+ two-oracle 分布报告。
> B 臂支柱证据：人像更据守（10.2% vs 1.2%）/回防（11.0% vs 7.4%）/巡航；PICKUP 4.5%
> 为纯拾取下限（用户修正：人"边走边打顺路捡"属战斗类，非不捡）。CLEAR 人像 60 窗口
> <200 → B 臂宁缺勿错、A 臂补齐。全文 → docs/nn.progress.intent.md §20。

## 295. M5 双臂完成 — A 臂 learnability 成立 + B′ 人像温和混合定向增益（2026-08-27 凌晨）
> **A 臂**（纯 God-AI 意图标签，quota 15000、inject、8ep）：trainAcc 0.568、自然分布 val acc
> **60.3%**、三桶 margin 全 ≥0.1 → gate PASS；类级 recall 6/7 >0（INTERCEPT 82.7 / HUNT 90.3 /
> CLEAR 66.6 / PICKUP 49.5；RETURN_DEFENSE 14.7 / CRUISE 24.8 弱；HOLD_LANE 0% 已知弱项）。
> 四必报项：self-feed gap 12.8pp（运行时自喂 prev 低于 teacher）、prev ±3 鲁棒、守家安全级误判
> **12.55% > 5%**、路由错配 37.8%。
> **B 臂**（+人像签名，quota 4000 + priority 45% 人类）：自然分布塌向 RETURN_DEFENSE（acc 16.9%）
> → **配置失败非"人像无用"**；#20 的 ≥30% 混合比须以温和配额落地。
> **B′ 平滑对照臂**（quota 15000 + priority 26.6% 人类）：overall acc 60.1%（≈A）、base 桶 margin
> +0.142（>A）、守家安全级误判 **7.72%（较 A 减半）**、RETURN_DEFENSE recall **14.7%→31.3%**、
> stub 冒烟 WIN **24%**（A 22% / B 18%）→ **人像守家信号定向增益成立，B 臂降级分支不触发**。
> M5 gate PASS；A + B′ 双轨权重进 M6/M7，完整 WIN 归因在 M7② 全执行器配对评估定论。
> 全文 → docs/nn.progress.intent.md §21–§23。

## 296. M6 仲裁修复 — reflex dodge 默认保留，仅 suppressDodge 显式压制（2026-08-27 凌晨）
> 原 applyIntent 只把 window 层候选写入 `_candidateOverride` → **排除 reflex(dodge)，违反 P0-5
> "reflex 覆盖移动默认成立"**。修复：override = 白名单全部三层候选（window+overlay+reflex），
> 仅当 window 候选标注 `suppressDodge`（RETURN_DEFENSE 的 suicideReturn）时剔除 dodge。
> 新增行为级仲裁测试（8 意图 dodge 保留/剔除断言，P0-5）。确定性不受影响（freeze gate 通过）。

## 297. M7① 天花板探针 — 白名单分类法 bug 修复，oracle 47%→73.4%、NN 44.6%→72.3%（2026-08-27 凌晨）
> **探针**：IntentOracleProbe（双 God：oracle 全链 = 完美意图源 + executor 受限链驱动世界）。
> **初测**：oracle 全量 46.9% vs God-AI 74.3%（m1-eval 35×10 hard）→ 27pp 压缩损失，M7① 返工判定。
> **根因**：WHITELISTS 引用细分支标签（t8/t2a/navigate... `_lastBranch` 分类法），override 过滤用
> 候选 ActionId——两套分类法不匹配，命中率仅 **46%**（CRUISE/PICKUP 只剩 dodge）。
> **修复**：`LABEL_TO_CANDIDATE`（vocab.ts 正向映射第②层），白名单标签→候选 id 翻译，映射率 100%。
> **修复后**：oracle **73.4%**（≈God-AI 74.3%，噪声带内）→ **M7① 前置标定通过**；NN 执行器（B′）
> **72.3%**（oracle 天花板 99% 价值、距 God-AI 2.0pp）→ **M7② WIN gate（≥50%）决定性通过，进 M8 门开**。
> 教训：WHITELISTS 双重消费（tagger 标签侧 vs 执行器候选侧）需显式半桥，两套分类法不可混用。
> 全文 → docs/nn.progress.intent.md §24。

## 298. M5 训练脚本多根 + priority 配额（2026-08-27 凌晨）
> train_intent_probe.py 支持多 data 根合并训练 + `--priority-root`（B 臂人类优先配额：#20 混合比
> 达标需 priority 而非比例采样——比例采样下人类仅 13% <30%）。eval_intent_m5.py 新脚本计算
> M5 gate 四必报项（teacher/self-feed gap、prev ±3 鲁棒、守家安全级误判、路由错配率）。

## 299. M7① cadence 定稿 + risk-gated 负结果（2026-08-27 凌晨）
> **oracle cadence 扫描**（35×10 hard）：{12: 76.3%, 24: 74.6%, 30: 73.4%, 36: **76.6%**, 50: 73.7%}
> ——承诺期在正确 cadence 超越 God-AI（74.3%），R0 风险反转（执行器逐 tick 读 World + reflex 豁免成立）。
> **NN cadence 扫描**（B′）：{12: 70.6%, 30: **72.3%**, 36: 70.9%}——中速最优，**定稿 replan=30**（预注册 #1）。
> **risk-gated 负结果**：base30/danger8 → 68.0%（< 固定 30 的 72.3%），危险窗口频繁重选造成扰动；
> 维持固定档，risk-gated 留 M8 RL 可选动作空间。
> **头部空间**：oracle 36 的 76.6% = M8 RL 优化意图选择后的可达上限（NN 现 72.3%）。
> 全文 → docs/nn.progress.intent.md §25。

## 300. M7② rollout 意图分布探针 — B′ vs SS 冷启动风险预评（2026-08-27）
> **同一确定性 (stage×seed) 网格各 50 局**（hard，`tools/sim/rollout-intent-probe.ts`，`tmp/rollout_intent_probe.json`）：
>
> | 指标 | B′ | SS | Δ(B′−SS) |
> |---|---|---|---|
> | 胜率 | 78.0% | 76.0% | +2.0pp |
> | replan 意图熵（原始 argmax） | 1.880 bits | 1.858 bits | +0.022 |
> | replan HUNT 占比 | **42.6%** | **36.9%** | **+5.7pp** |
> | 承诺意图熵 | 2.318 bits | 2.207 bits | +0.111 |
> | 承诺 HUNT 占比 | 26.7% | 23.7% | +3.1pp |
>
> **口径**：主口径=每 replan 的原始 argmax（自馈注入序列推进的意图流，最接近 RL 冷启动策略产出）；
> 次口径=实际承诺意图 trace（margin 门控后真正驱动玩法）。
> **结论**：SS 未如期"更防御"——熵几乎持平（+0.022，无更分散/更保守信号），而 HUNT 占比低 5.7pp。
> SS 的代价（胜率 −12pp 于 m1-eval 大网）源自 **HUNT recall 下降（0.93→0.71）**，
> 而非意图分布熵的涣散；此探针佐证"佣金偏移不显著，进攻主力意图削弱"这一判断。
> **M8 RL 冷启动选臂**：B′ 初始 rollout 意图分布更进攻（HUNT 高 5.7pp）、胜率更高 → 采样效率更优，
> 作为 RL 起始策略优于 SS（SS 的 self-feed 优势在 RL on-policy 下收益为零）。开始 RL 即用 B′。
> 全文 → docs/nn.progress.intent.md §26。

## 301. M8 RL 冷启动方案定案 — B′ 即开 + s32-35 人类胜利回放降级为辅助注入（2026-08-27）
> **决策**：不以"离线增强 B′ 更像人类"作为 RL 前菜——RL on-policy 会重写意图头，
> 把模仿学习费劲学到的偏好大部分洗掉，投入不值。路线：
> 1. **直接用 B′ 启动 M8 RL**（主线，冷启动站台已定 §300）。
> 2. **不外推人类录像堆量**（如 10+ 局/关）以增强 B′——会被 RL 稀释。
> 3. **只补最稀缺的 s32-35 人类*胜利*回放**（God-AI 在这些关胜率最低、擅长守家的
>    人类打法 RL 自 roll 极难长出来），作为 RL **辅助正样本 / 守家先验侧信源**注入，
>    而非离线学习阶段。
> **触发时机**：等 RL 轨道（rollout/PPO 数据流）搭好后专门落地此注入；落地前先确认
> RL 数据流确实消费 rollout 实际意图（B′ 初始值），注入与 PPO 冲突走 §300 判读。
> **记录**：docs/nn.progress.intent.md §26（下一段续写落地）。

## 302. M8 意图 RL 落地 — 半 MDP 意图步 PPO + 变步长 GAE + B′ 冷启动 value 预热（2026-08-27）
> **决策**（plan §6/I13/P1-7k3，M8 架构落地）：
> 1. **半 MDP 意图步 PPO**：决策只在 replan tick（30，M7① 定稿）；动作 = 采样的意图
>    （8 类，ESCAPE 死类掩码）；窗口冻结（IntentExecutor rlPick → God-AI 白名单子链）。
>    GAE 在意图步上算，**γ_step = γ_tick^Δt**（Δt = 窗口时长 tick，dt.npy），
>    **Δt≡1 退化与 ppo.py 定长 per-tick GAE 逐字节一致**（单元测试断言）。
> 2. **奖励 = 意图窗口稠密分量**（击杀 +4 / 清砖 +0.5 / 拾取 +2 / 阵亡 −5 / 基地墙损 −3）
>    **+ potential shaping**（Φ = −最近敌 base pressure，**γ=1 势差** F=Φ(s′)−Φ(s)——
>    γ_tick 势差会累积 (γ−1)ΣΦ 残余，高压长局实测 +60 伪正奖励；γ=1 精确 telescoping、
>    整局塑形和 = Φ_T−Φ_0 ∈ [−1,1] 有界，P1-8k3）+ **无产出切换成本**（−0.05/切换）
>    + 终局（通关 +50 / 基地失守 −50 / 命尽 −30 / 超时 −1）。
> 3. **B′ 冷启动 value 预热**：value 头随机 → 直接 PPO 实测 KL 爆炸 262（优势被 value
>    噪声主导、策略单 epoch 塌缩）。预热 = 前 warmup-iters 迭代**只训 value 头且冻结
>    主干+三头**（经共享主干训 value 会扰动策略特征→意图分布，实测熵 0.90→0.33，
>    等于 RL 前先毁掉 B′）；adv/ret 全局归一（I13 逐关规范化，value MSE 数百→O(1)）。
> 4. **注入特征**（prev one-hot 8 + duration 1，9 维）由 rollout 记录、PPO 前向消费；
>    **value 头 137→1 与三头并列消费同一 137 隐藏**（P1-5②：value 必须看到承诺状态，
>    infer.ts 修复 137 宽 value 头经 intentForward 计算 valueOut）。
> 5. **评估与止损**：m1-eval intent-exec 固定语料贪心局（iter15 350 局，P1-1k3）；
>    主指标 = Δ vs M7② 基线（B′ 72.3%）；iter15 Δ≤0 → 止损转 M9（P2-5 不续命）；
>    target_kl=0.1 per-epoch 早停（参照现有 per-tick RL 健康 kl≈0.05/iter，breaker 0.15）。
> 6. **reward 有界性**：逐 reward ∈ (−60, 60)；整局 Σr = 终局 + 塑形(±1) + 稠密 + 切换。
> **Rationale**：意图步 semi-MDP 是 plan I13 定案；γ=1 塑形修正了 per-tick 折扣塑形的
> 累积残余（伪正奖励会奖励"高压持续"这一与守家相反的行为）；value 预热修 B′ 冷启动
> 基线噪声塌缩（M8 的 kickstarting 落地形式）。
> **记录**：docs/nn.progress.intent.md §27（训练首轮结果续写）。


## 293. God AI 解冻 + 恢复超级道具策略（super-item 战略激活）(STATUS: 已实施, 2026-08-28)

> 全文 → docs/god-ai-tuning.progress.md Part 0.1（2026-08-28 解冻纪元基线 / golden 重钉）

> （编号冲突：与 §293-intent「M4 完成」撞号，见头部「编号冲突注」；两者编号均被外部引用，保持不动）

## 302. 追尾导航（pursuit-tail / 并入目标车道后方）— 三轮归档：用户规格的等待后并道（am=3）净 +29 为全程序最佳，仍噪声带内，维持 OFF _(STATUS 已被 §304 取代：2026-08-29 用户拍板启用，见下两条)_(原 STATUS: 否决, 默认 0 = OFF, 2026-08-29)

> 全文 → docs/god-ai-tuning.progress.md §302（§1–6 一轮 modes 1–6；§7–8 二轮
> mode 7；§9 三轮 AlongMode=3 三版）

> 来源 plan/Intent-Policy-NN-Plan.md §12.1 #3（执行器实施层缺陷）。实现 = `pursuitTailDirImpl`
> （Navigator.ts，HUNT 内 `_moveDir` 覆写，射击链路共用）。三轮 hard 35×60 配对 A/B：
> ①modes 1–6 净 −35…+8 全噪声；②mode 7 拆分出**唯一显著信号 am=2 仅侧方/前方 净 −58
> (t=−2.79)**——切入挡路且朝向横向；③**用户拍板 AlongMode=3 等待后并道**并经三轮复核
> 修正：v1 hold 对但并道半途夭折（`laneGap===2` 门控在 gap→1 交还 directMove 被拽回）；
> v2 全程接管横移；v3（用户二次复核 s21@30 抓出）`along=−2` 是取整值、目标半格下行时
> **车身仍物理挡住滑行脚印**，交还 directMove 会下追把身位贴回去（振荡 1.5s）——修正为
> 滑行被拒且拒者是目标车身时 HOLD 等间隙自行张开。最终语义 = 自包含状态机：
> `along ∈ [−1,+窗]` hold；`along ≤ −2` 且像素级不被卡 → 接管横移 gap∈{1,2} 直到 gap 0；
> 上道交还纵向追击开火。剂量：Δwin +6/75、击杀首次反超（1413 vs 1358）、aligned +223、
> 在车道 7.28%→9.26%。全量**净 +29（319/290）= 全程序最佳**，版本弧线 −39/−58/+1/−4/
> +16/**+29** 随机制完整度单调改善，但 < 2SE≈49 ⇒ 按 §6.3b 不转 ON。
> 维持 `pursuitTailMode: 0`，ARCHIVED_KNOB_GROUPS 不变；归档构型 am=0/1/2 路径逐指令未动
> （基线臂三次 A/B 均 1581）。工具 `tools/diag/pursuit-tail-{probe,flip,scenes,export}.ts`
> + `tmp/s302-diag21-30.ts`（tgtBlk/othBlk 逐 tick 诊断）留用；复核录像
> `tmp/s302-replays3/`（全 MATCH ✓，含 s21@30 结局翻转 gameover→stageclear）。


## 303. 护卫出生卡墙 bug 修复 — baseSideSpawnCell 兜底不再落墙 + golden 重钉 b9a629e0（STATUS: 已实施, 2026-08-29）
> **Bug**（用户报告）：使用基地护卫（天降神兵）道具时，护卫出生在基地砖墙上被卡死无法出击。
> 根因：`SimulationEnemies.baseSideSpawnCell` 只扫基地两侧固定列各 5 行候选，全阻塞时
> 兜底直接返回 `(col, baseRow)`——即基地墙环砖所在格；普通关卡基地环砖即触发
> （新增回归测试在未改动的 stage0 上即红）。
> **修复**：候选序 = 请求侧列（贴基地 4 行）→ 对侧列 → 同列向上直扫 → 全场最近空位
> （偏好请求侧）；`isFreeSpawnCell` 统一 bounds/terrain/tank 三查，任何路径不再返回阻塞格。
> **判定**：仿真行为变化 → `freeze:check` 翻红（预期显式判定，非 God-AI 决策逻辑改动），
> golden 重钉 `20784637c6…` → `b9a629e0e2…`（21 组合，109,325 签名行），门回绿。
> **配套**：按 owner 指令仅重跑 hard 60-seed 基线（classic/chaos 未动，见 Part 0.A.2）。
> **测试**：`tests/guard-ally.test.ts` 新增 2 条（基地两侧全砖 / 普通关卡，出生格必无阻塞、不叠tank）。
> **记录**：docs/god-ai-tuning.progress.md Part 0.A.2（golden 重钉 + hard 基线对比）。

## 304. 启用追尾导航 — pursuitTailMode=7 + AlongMode=4 默认 ON（用户拍板，新纪元三件套完成；同日 am=4 增补见 dated note）(STATUS: 已实施, 2026-08-29)

> 用户决策：净 +29 已足够好，启用。随后按 §6.3b 完成新纪元三件套：
> ①本条目；②冻结签名 golden 重钉 `7b2e5097…`（tools/det-golden.v1.sha256，
> 采集于启用后）；③score-gate TRUTH_SCORES 第五次重捕获 + 三难度 60-seed
> eval-suite v7 基线（docs/god-ai-tuning.progress.md §304）。
>
> **默认**：`pursuitTailMode: 7, pursuitTailAlongMode: 4`（yield-then-tail 状态机
> + 锁定目标键控 + T2a 滑行抢占；语义见 §302/progress §9/§304）。**classic 经
> CLASSIC_OVERRIDES 保持 0 = 字节不变**（instant 1-HP 池未 A/B，复刻一致性门槛）；
> **chaos 继承默认 = ON**。
>
> **2026-08-29 同日增补（§6.4 dated note）**：用户点名处理两类自愈型中断后，
> `AlongMode=4`（`pursuitTailTargetCell` 锁定目标键控 + `pursuitTailSlideDir`
> T2a 滑行抢占、对枪抵消提交不可抢占）配对 A/B 对 am=3 **净 +20**（256/236，
> 1632 vs 1612 / 2100）→ 默认 3→4。实现教训：状态机门若写 `=== 3` 会把 am=4
> 静默漏进归档路径（正面切入 −58 几何），首跑 A/B 净 −59 才暴露——**分层参数
> 的门一律 `>=`**。三件套随默认迁移再次完成：golden 重钉 `91faa793…`、
> TRUTH 第六次重捕获、三难度基线重跑。
>
> **代价记录**：score-gate v7 口径（am=3 启用时）hard 0.7663→**0.7890（+2.26pt）**、
> chaos 0.7562→**0.7337（−2.25pt）**、classic 0.8697→0.8697（0.0000）。
> **am=4 默认后（第六次重捕获）**：hard 0.7890→0.7743（−1.47pt）、
> chaos 0.7337→**0.7528（+1.91pt）**、classic 0.0000 不变——score 与胜率两口径
> 在 hard 上方向相反（score 重罚败局余量，胜率是用户指定的治理口径），
> 如实双记。若 chaos 体验需要，可给 CHAOS_OVERRIDES 置 0（一行动手）。
>
> **连带修复**：`laneShotClear` 增加目标车道坐标的越界守卫（横向分支此前只守列
> 不守行；测试夹具的越界敌格使其显形）。NN 训练语料：训练用 God-AI 对手行为
> 随此启用改变，无道具口径语料如需再生另行处理（nn.progress.md 不涉架构变更）。

---

## §294 Goal-Space 策略网络重建开工（2026-08-29，M9 时代启动）

> 全文 → `docs/goal-nn.progress.md`（§0–§3）· 规格 → `plan/Goal-Space-Policy-Rebuild.md`

**决策**：按手册依赖图走**网络轨 + 数据轨 + 训练轨**（T7→T8-min→reach-mask→T8.5→T7.2→
T6-pilot→T9a→T9），**暂缓执行层轨**（T2/T4/T5——会改 God-AI 行为触发新纪元三件套；
T9 卡已记录 fire_head 随机初始化的回退路径）。reach-mask 按规格独立实现、不动 God-AI
任何默认参数（不触发新纪元）。

**基线重钉**：God-AI hard **78.81%**（1655/2100，pinned `reports/godai-baseline-hard-35x60`），
旧 pinned 75.86% 作废（§293-God AI + pursuit-tail am=4 两个行为纪元之后）。
T9/T9a 配对差门以新基线判定。

**解释性决策**（实现期澄清手册留白，全部记录于 goal-nn.progress.md §1）：
1. T7 TS 侧保留 intent 头加载能力（§14.3 it38 重评依赖；"删"落在 GoalNet 定义/权重 JSON）
2. 热图头 golden 容差按 §T7.3 预案降级 1e-3（TS mul+add vs torch FMA 舍入差 1.068e-4）
3. 可满足性校验 = top-K(6) 首个 travelEst≤T；全不可满足强制提交 argmax（telemetry 'unsat'）
   —— ⚠️ **2026-08-29 修订（T9a 归因，commit `b84c012`）**：该语义造成**移动拴绳**
   （T=240≈10 格；到达首个目标后更远目标被永久拒绝，而 E4 是续约非放弃 ⇒ 冻结，
   实测 989 tick/5 格）。改为**只拒绝不可达**（travelEst=∞），T 只管重评估节奏；
   手册 §6.1.1 规格正文已同步。
4. E4 同格续约：bornTick 重置 + pursueSince 独立累计（inject duration 连续）+ dodgeTicks 重置
5. 重选失败冷却 30 tick（防全遮情形逐 tick 重前向）
6. 采样分布 = softmax(热图) 限可达格（λ·k 只进执行 argmax，不进采样分布；与 §T9a.1b 一致）
7. engage 在 on-policy PPO 期只记录不训练（§8.3.0"有监督才入网"；反事实语料的 engage
   标签在 BC 期训练）

**H 扫描实证（§11.8，pilot 冒烟）**：argmax 落敌后格 9%@H60 → 25%@H120 → 44%@H240，
godTarget 重合率 62%→33% —— 长窗口系统性恢复追尾行为，手册 §11.8 "短窗近视"论断成立。

## §295 路线转向：课程学习从零练执行器（2026-08-29，T9a 门② 之后）

> 全文 → `plan/goal-nn-action.md`（**实施手册**：任务卡含步骤 / 改动文件 / 验收 / 失败处置）·
> 执行日志 → `docs/goal-nn.progress.md` §5
>
> **2026-08-29 末次重组**：文档由"派工表"重写为"体系化实施手册"（§0 怎么用 → §1 背景 →
> §2 环境阶梯 → §3 系统规格 → §4 门禁与止损 → §5 任务卡 → §6 依赖与总盘 → §7 命令 →
> §8 与手册关系 → §9 易错点 → §10 评审处置 → 附录 A 代码坐标 / B 术语）。
> **门禁与预算数值一律不变**；重组中校正了两处算术（关键路径 16.5→**16.25d**、分阶段拆分
> ≈6.25d+16h / ≈10d+76h）与一处坐标（(stage,seed) 配对数据源是 `.ledger.jsonl`，
> 不是里程碑快照 `.partial.json`）。四轮评审采纳/驳回理由压缩保留在 §10。

**决策**：放弃"蒸馏 God-AI 的执行器"与"等独立执行器自己变强"两条路，改走**玩具竞技场
课程学习**：S1 开火命中（1 敌/无基地）→ S2 闪避走位（3 敌）→ S3 砖墙+道具 → S4 有基地→真实关卡
→ S5 才解冻目标格子头。目标头在玩具场上没有战略时域可学，提前开只是噪声。

**理由（三条）**：
1. 模仿学习的天花板在构造上就是教师本身 ⇒ God-AI 78.81% 蒸馏不出更高；本方案中 God-AI
   **只当 warm start（定起点）**，RL 自身奖励才是天花板（定终点），并由 A5 同预算消融检验。
2. 两段阴性证据（IL/DAgger 学生 0% 胜率；run_rl.py 68 iters/38.5h 不收敛）同时踩了
   "真实关卡宽分布 + 守家向 Φ 奖励 + 从 0% 胜率权重起步"三条不利；课程学习同时拆掉这三条。
3. 基建已大半就绪：`tools/optimize/curriculum.ts` 的 `makeArena` / `makeMazeStage` 5 个 arena、
   `rl_model.py` 逐决策步 `[move(5), fire(2)]` 双头、`export-dagger-labels.ts` v2 schema、
   `run_rl.py --bc` warm start 与 `--curriculum-*` 开关。

**沿用/不变**：God-AI 19 候选链与 `ACTION_WEIGHTS` 仍不许删（手册 §9.2.1）；现有 35 关与
difficulty 行只增不改（不触发新纪元）；obs/scalars/shard schema 不改（保证跨级权重整份迁移）。

**门禁口径**：每级门锚在 A0 量出的 God-AI 同场行为指标上（相对阈值，非绝对值）；最终门仍是
对 78.81% 的 2100 局配对差 ≥+2pp 且 CI 下界 >0。预算为硬上限，连续两级止损即停。

**记账**：T9a 门② 的两个数字（goal 0.05% / goal-god 0.0%）分别来自冻结修复前代码与失真探针，
均不作为判定依据，由 A0 重测。

**评审处置（`plan/action.review-glm.md`，12 条）**：9 条采纳、2 条修正、1 条驳回，逐条理由记在
`plan/goal-nn-action.md` §11。三处实质性改动：① **S4 拆为 S4a（有基地 maze）/ S4b（真实关卡）**
并预注册 S4 奖励（原方案在这一级的奖励是空白）；② 保底层默认改为**独立 dodge 规则**
（不复用 God-AI 候选链，修掉"独立执行器"的叙事矛盾），God 链 dodge 降为回退；
③ S5 集成拓扑新增**"不开目标头"**出口（14 obs 通道与 19 scalars 全占满，目标输入无空位可挤）。
驳回项：**不复用现有真关卡 dagger 权重**（那是 0% 胜率学生，正是原失败条件之一）；
**A0 不新增 sim 埋点**（`SimResult.events` 已有 `player_hit`/`tank_destroyed`/`bullet_fired`，
受伤/开火/击杀零埋点可得，仅命中率用代理口径）。

**第二份评审（`plan/action.review-hy3.md`，F1–F7）**：7 条全部采纳（2 条精确化），理由记在
`plan/goal-nn-action.md` §12。四处实质改动：① **CPU 小时预算入表**（每级 4–40h，总盘 ≈82h；
人日可并行、训练墙钟不能，A1 出口必须实测缩放）—— 这是本轮最被低估的整体风险；
② §4.3 (e) 加**硬边界**：只准 `perception.ts` 基元 + `scanAheadImpl`（禁 `ThreatAssessor`，
它读 think() 填充的 `_enemies`/`_threatCache`），1.5d 做不出即升 (c) 为默认；
③ 覆盖步**落盘 executed 动作 + 其 logp**（shape 不变的正确记账，(e)/(c) 在信用分配上等价）；
④ §4.5 的"mask 偏置"是错误措辞（mask 是 u1 硬掩码，只能禁止不能鼓励）⇒ 拆 1a 硬禁止 / 1b
additive-bias（后者触发止损线 4）。另修掉我自己引入的时序矛盾：A8 拆 **A8a（A4 前 0.5d）
+ A8b（A9b 后 0.5d）**。

**第三份评审（`plan/action.review-pickle.md`，3 建议 + 2 顺带）**：**全部采纳**，理由记在
`plan/goal-nn-action.md` §13。本轮补的是**训练环境本身**的盲区（前两轮都在盯网络/拓扑/预算口径）：
① **单地图过拟合** —— `makeArena` 是无 RNG 的纯函数，每级只有一张固定图，智能体背图即可过门
（绝对+相对双轨挡得住刷分、挡不住背图）⇒ 加 `layoutSeed`，**每级 3 张布局变异（写死 3）**，
A0 在同样的 3 张上锚定；② arena **身份贯通**六七个环节（`build_pairs` 只出整数 stage 下标，
arena `id:-1` 不在 `STAGES`，shard 命名用 `rl_s<si>`）⇒ 用不相交整数段 `1000+n` + 混排解析单测，
A1 1.25→1.75d；③ CPU **四分账**（训练 82 / 评估 8 / 消融 4 / 扫参 12 ≈ 106h），此前评估/消融/扫参
不封顶 ⇒ 预算门形同虚设；④ `makeMazeStage` 的 `enemyCount` 覆盖位前移到新卡 **A0a**（排 A0 之前）。

**第四份评审（`plan/action.review-ds.md`，11 条）**：10 条采纳、1 条部分修正，理由记在
`plan/goal-nn-action.md` §14。本轮的独特价值是**下限与语义**：① **预算只装了上限没装下限** ——
S4b 的 40 CPU-h 对应单 iter ≈6.8 CPU-h（≈6 个迭代），而同档唯一先例 460 CPU-h 未收敛 ⇒ A7 出口
做**产能核算**（`N₁ < N_min` 直接写"预算停"，不启动这 40h），每级上限降为**软上限**、总训练账
82h 为硬上限（未用小时可结转）；② **冻结 obs 保护不了语义漂移** —— 无基地 arena 上 `obs-encoder`
无条件画鹰（`isBaseDestroyed()` 对无基地恒 false）⇒ 整条梯子学的是"base 信号 = 常量"，到 S4a
突然变成生死信号 ⇒ A0a 修为"无基地 ⇒ ch5 全 0"（不改 shape，不触发止损线 4）；③ **最终门功率**
需在 A0 出口定死（关级 35 对下 CI 下界 >0 实际需 ≳2.4pp，默认改 (stage,seed) 级 2100 对）；
④ **A4b 升级为二元 Phase-1 无效性闸**（STAGES 0–3 胜率 ≥ 随机基线 +5pp 且中位存活 tick 更高）。
**§3–§7 的门禁与预算自本轮起冻结**（DS-11 收口）：后续意见只进 progress/DECISIONS，除非出现
证伪某条已定案项的新代码事实。关键路径 ≈ **16.5d 人日 + ≈106 CPU 小时**。

## §296 大语料 rotate 战役（2026-08-30，用户指令）
S1 微课（固定 12 局/it）被判定为最差形态（记忆化过拟合、SE≈±14%、无泛化信号）。
改用 repo 标准 rotate 语料收尾：`--rotate-stages 35 --total-stages 35 --seeds-per-stage 10`
= 350 局/it、每迭代全新 (stage,seed)（(rotateSeed,it) 键控、断点可复现）、max-ticks 12000、
workers=8、干净评估 2 局/关开起。热启动 = tmp/s1-cap/weights.json（kill2 微课 10 iters
产物）——**value 头一次性重训可接受**（BC/微课 checkpoint 无有效 value 头，归一化路径
清零重学）；策略头经 warm_start_normalize 软先验延续。奖励臂不变（toy:kill2，换奖励
= 换实验）。新战役独立目录 tmp/s1-big（课程类型切换不作断点复用）。封顶 = --max-hours 8
与 --iters 40 先到者。改的是启动命令，不是训练代码。

## §297 S1 过门后的战役修订包（2026-08-30，随 Phase-1 重启累积决策，一次归档）
1. **奖励臂 kill2**（wAlive 0.0005→0）：A4 贪心塌缩诊断（败局非冻死，是"上推+扫射
   不追踪"套路）⇒ 拔掉"原地骚扰稳拿 0.6/局"的激励锚；存活压力由 wDmg 承担。
   实证：S1 大语料重开 17 iters 即 97-100% 过门（A4 时代 21 iters 仅 26.7%）。
2. **A4b 缓期**：只会开火的 S1 学生缺闪避/躲弹（S2/S3 课），真实关复测结构性无解；
   待 S2/S3 能力建立后随门判定自然复测，不单独烧机时。
3. **L0 退场判据更换**：强权重下 off vs l0 逐位一致（L0 惰性）⇒ dodgeCov≥2% 判据
   作废；新档以 `--dodge off` 自持探针开档，红线 = off 下 deaths/局 + alive-ticks
   （弱权重基线 0.222 / 强 0.0）；L0 代码保留，弱策略/高难档可重新启用。
4. **eval 门控升难**：新档 eval >80%（5 迭代趋势）再进下一档；rollout 崩 <50% 判
   过难降档。训练语料逐轮轮转（AGENTS §15.1），评估种子固定（860001+，可比性）。
5. **干净评估语料可配**（`--eval-stages`）：arena 战役自评训练场（OOD 信号）；
   EVAL_SEEDS 扩至 20（前 2 保历史可比）。
6. **stream 默认**（AGENTS §15.6）：run_rl 代码默认 0→1 + ppo.update 别名补齐
   （流式路径曾腐化）。串行仅调试用。
7. **rl-config `rl` 共享块**：机制默认值（rotate_stages=0 保守态、total_stages、
   difficulty、max_ticks、stream、mb、seed、keep_iters、eval_window、workers、
   local_slots）run_rl `_d` 接入；**切换器默认值必须取保守态**（rotate_stages:35
   曾把 S2 静默切到真实关 rotate——二次 35 关意外，本条为免疫记录）。

## §298 机时豁免 + 监控红线 + 两处预注册补齐（2026-08-30 夜）

**背景**：S1 三处修复（量级归一化 / kill2 / 语料轮转）后 17 iters 从 25% 爬到 97-100% 过门，
路线成立；S2 在跑（it10 rollout 53.6% / eval 67.8%）。核实进度时补齐以下五项。

**1. 机时豁免（用户指令）**：用户明确"只要收敛，不考虑机时"。
⇒ §4.5 的 **82h 训练总账保留为核账口径，但不再是停训理由**；
**替代停训判据 = 迭代产能与收敛形态**（下表红线）。
⚠️ 豁免**不等于免记账** —— 每场战役仍须记录墙钟与 CPU，否则"预算停 / 门败"（止损线 5）
的区分没有数据可依，也无法回答止损线 6 原本要回答的"够不够"。

**2. 监控红线（替代机时的停训判据）**：

| 指标 | 红线 | 处置 |
|---|---|---|
| 熵 | **<0.7** | 停下查（更新过密 / 奖励过确定 / 语料轮转失效） |
| 熵 | **<0.3** | 按塌缩处理，回 §1.2 三条修复逐项排查 |
| rollout 胜率 | **停滞 ≥3 iter 或回落** | 触发降档评估（减敌数） |
| eval 胜率 | 连续 5 iter ≥80% | 过门资格评估 |
| 单 iter 墙钟 | 突变 >2× | 查节点池 / stream 是否退化 |

（预注册健康线 熵 1.5–1.8；S2 实测 it1 1.24 → it10 0.93，已低于 S1 封顶时的 1.21，
故补此红线。属**监控约定，不是新门禁**。）

**3. Phase-1 闸复测点（补齐 §297 #2 漏掉的一半）**：A4b 缓期成立（S1-only 学生
缺 S2/S3 技能，0/240 是能力不足非迁移证伪），但原记录只写"随门判定自然复测"、
**没有时点 ⇒ 这道闸可能永不触发**（拖到 S4b 就是循环论证）。
⇒ **钉死：S2 出口立即复测；S2 出口未达则最迟 S3 出口必须复测。不得二次缓期。**
协议与判据不变（真实关 0–3 × 60 seed vs 随机基线同 seed 配对，+5pp 且中位存活更高）。

**4. A5 判定作废并须重做**：现有"≈ 未定"跑在旧配方（S1 当时 26.7%，两臂贪心 60/60 全同）
上，**数据作废**；新配方下纯从零臂从未跑过 ⇒ "教师是否构成天花板"（本路线核心理论问题
§1.3）仍无有效答案。⇒ 用新配方重跑（4 CPU-h，必须用 `init_scratch_weights.py` 近均匀 init）。

**5. 目标轴收官的连带记账**：A8a headroom −25pp ⇒ 方案 3（不开目标头）
⇒ **A10（解冻目标头）/ A11（T6 语料重标）/ A-x（B′ 先验）三卡作废**，卡片表已标 ⛔/⏸。
A8b（S4b 出口复核）保留 —— 若届时执行器已具备 S2/S3 技能，headroom 结论可能翻转。

## §299 S2 门禁口径决策：按"全歼率"判，S2 过门（2026-08-31）

**背景**：P0-0 尸检（`docs/goal-nn.progress.md` §14）证明 S2 的 38% 失败里 21/60 是**伪负局** ——
敌人全灭但最后一个敌人掉的道具触发 BONUS TIME 窗口（600 tick），窗口没走完就被 `max-ticks 1200`
截断，旧 `stage_clear` 口径恒为 0%（60/60 timeout）；**真实"敌人全灭"通关率 = 58/60 = 96.7%**。

**决策（用户拍板方案 A）**：eval 同时报告 `stage_clear` 与 `全灭(annihilation)` 两口径；
**S2 门按 §2.1 字面"全歼率"判**（即敌人全灭率），**不**按 `stage_clear`（受 BONUS 窗口污染的伪负局）。

**落地代码**：
- `src/game/SimulationEffects.ts` 导出 `allEnemiesCleared(world)`（判断 `enemiesRemaining<=0 && 全灭 && 无存活道具` 的"全灭"部分）；
- `tools/sim/simulation-runner.ts` 的 `SimResult` 加 `cleared: boolean` 并填充；
- `tools/sim/sim-worker.ts` 的 `SimTaskResult` 透传 `cleared`；
- `tools/sim/m1-eval.ts` 聚合 `clearRate`（新增全歼率输出行）；
- `tests/level-sim.test.ts` 三个 mock 补 `cleared`；`bun run typecheck` 全绿。

**实测结论**：用 it16 权重 `tmp/s2-cap/weights.json` 跑 S2 评估集（1010-1012 × seed 860001-860020 = 60 局，
真实 student 贪心策略，16 分片并行）：**全歼率 96.7%（58/60）≥ 80% ⇒ S2 过门**，进 S3（1020-1022）。
口径自校验：与 P0-0 尸检脚本（同 it14-era）完全一致（autopsy 自家 `_autopsy-out.txt` 亦录 `stage_clear 0% / 全灭 58/60`）。

**边界（重要，避免误读）**：
- 此决策**只改 S2 过门的判定口径**，不动方案 §2–§4 冻结的门数值；相对门（判据② 受伤 ≤1.2×锚 /
  判据③ 存活 ≥80%×锚）仍受 **P0-1 eval 遥测 bug 阻塞**，修 P0-1 后再补判，不阻塞进 S3。
- 旧 `stage_clear` 口径作废**仅限 S2 过门判定**；S3 起若 BONUS 窗口仍是噪声源，沿用同一 `全歼率` 口径。

## §300 S3 换臂 balanced，tmp/s3-cap2 新实验（2026-08-31，用户拍板"方案 B"）

kill2 下"以命换击杀"正期望（wKill1.0 vs wDmg0.15，3 命缓冲）致 S3 lives 维度 0.758≪S2 锚
0.997、accuracy 0.445<0.590，S3 相对门（受伤/存活/开火效率三项）按字面判负。依 §15.5 另开
新实验 tmp/s3-cap2（BC = 旧 s3-cap it15 结算权重 **97d5990d32f6**，reward=toy:balanced
wDmg0.35/wDeath1.0/wAlive0.001，预注册臂，不新增第 4 臂）。旧 s3-cap 曲线存档不续用；
节点与场景（1020-1022，hard，3600tick，8 敌迷宫）不变。监控：balanced wAlive=0.001 的
"存活复锚"风险（accuracy/mobility 连续退化则停，备选臂 survival 需用户再拍板）；
lives/loot/accuracy 逐 settle 对照 S2 锚（0.997/0.577/0.590）。
执行：`plan/s3-balanced-restart.md`（含 §3.4 验证与每 settle 必报表）。

## §301 T4 双缓冲落地的两个隐式缺陷修复（2026-08-31，commit 85f3953）

`plan/goal-nn-throughput.md` 的轻量版双缓冲（collect-only 子进程 + 行为快照 θ_N + 原子权重回写）
冒烟实测发现两处让双缓冲**静默失效**的缺陷，修复后端到端验证通过——

1. **iter_id 格式**：`_run_collect_only` 用 `f"collect-{it}-{pid}"`，而 `run_rollout_queue`
   用 `int(iter_id.rsplit('.',1)[-1])` 解析迭代号 → 分布式路径下每个 collect-only 子进程
   必 `ValueError` 崩溃（rc=1），预采产物从未落盘。改为 `f"{RUN_ID}.{it}"`。
2. **本地路径缺 wver**：`run_rollout`（纯本地采集）传给 export-rl-rollout.ts 的命令漏
   `--wver`（run_rollout_queue 的 local slot 有传）→ 本地 shard 的 manifest 无 wver →
   主进程下一轮 `completed_pairs` 永不命中 → 预采作废、回退自采。补上与队列 local slot
   一致的 `--wver` + `--node-label local`。

**已确立的 spawn 时序不变量（重构禁止破坏）**：`_spawn_collect_next` 必须保持在
「本轮最终写回 `args.out`（run_rl.py 638，唯一写回点）→ eval join → breaker」**之后**、
「下一轮 PPO」之前。stream 的 wave 更新只改内存 model、从不写盘（节点采集权重在 stream
启动时冻结），因此快照恒为"整轮所有 wave + tail-drain 完成后的最终权重"，与用户确认的
on-policy 期望一致（不会用 wave1/epoch1 中间权重预采）。

**已知残留开销（非正确性）**：`run_rollout_queue` 收官等待 settled 后 ~112s 才返回，
collect-only 子进程会跟着多挂 ~2min（主进程 join 前子进程此窗口未写盘 → 命中晚）。其
`round done` 于 `settled` 之后。吞吐收益（155s→6.6s 采集墙钟）已远大于此开销，暂不优化。

## §302 S3 balanced 胜率崩塌 → 回滚 it4 峰值权重续训（2026-08-31，用户拍板"回滚"）

**触发（plan/s3-balanced-restart.md §4 风险 1 判据）**：accuracy 连续 2 settle 塌到 <0.40
（it6=0.370、it7=0.355，且 it4 峰值 0.427 → it5 0.391 → it6 0.370 → it7 0.355 连续三降），
eval 胜率从 it4 的 70% 三连崩至 38.3%（clearRate 78.3%→51.7%）；DoD 前 3 settle 三项
（lives≥0.80 单调 / loot≥0.60 / acc≥0.50）全部不达标且反向。非初期 U 形（it1-3 平台 58-60%，
it5-7 是从 70% 高位崩塌）。it6 eval 全 drop（节点升级恢复期，样本缺失）。

**裁定**：用户选「回滚 it4 峰值权重续训」——排除"权重已劣化"因素，观察复现性以定位是
权重问题还是 balanced 臂本身问题。

**执行**：it4 结算权重 = `nn-training/weights/rl-weights.it3.20260831-093140.json`
（sha 96b1383ecf1d；归档名与迭代号 off-by-one，it{N} 文件实为 it{N+1} 权重——已核对
eval wver）。备份 it7 权重（`tmp/s3-cap2/weights.it7-backup.json`，sha a53a8e3）→ 覆盖
weights.json → 带杀重启（OMP8+PROC_BIND + --double-buffer 1），it8 起以 96b1383 重新采集
（旧 it8 shard wver 不匹配被清空重建）。

**判读约定**：it8 eval（回归 96b1383 权重后首轮）若 ≥ it4 水准（eval 70%、acc 0.43 一带）→
崩盘可归因 it5-7 的权重劣化路径，续观 3 settle 是否复现；若仍 <50% / acc<0.40 → balanced
臂本身不稳，转备选臂 survival（wKill0.5/wDmg0.5/wDeath1.5，需用户再拍板）。

**§302a it8/it9 判读结果（2026-08-31 下午）**：it8=回滚后第一轮：rollWin 58.7%、acc 0.421
（回到 it4 一带），但该轮 PPO gnorm 44.9 / kl 0.10 → 熔断丢 107 局（KL 更新爆炸仍在）；
it8 干净的贪心 eval 因 KL 熔断掐了派发而未产生。it9（回滚续训下一步）：
**evalWin 75.0% / clearRate 80.0%（wver b35d8349）——超过 it4 峰值（70%），未复现 it5
的"70% 后第一步崩塌"**。结论：回滚策略有效、权重健康路径成立，it5-7 崩盘非必然复现；
但 KL/gnorm 逐轮爆炸（gnorm 44-56、kl 常 >0.08）仍是悬而未决的动力学风险（未崩但随时
可能重演 it5-7 型崩塌），列为观察项：若再现连续 2 settle 胜率下滑+acc<0.40 则按 §302 处置。

## §303 v3.10 长尾竞速：in-flight 尾部任务空槽即竞速（2026-08-31，用户指令"有空槽就派发"）

**问题（用户实测观察）**：v3.7 尾部 fan-out 只在「pending 还有排队任务」时复制在跑副本。
末尾任务一旦被单个 worker pop 出队、独占 in-flight（长 RPC/慢节点），其余空闲执行槽因
`src=None` 干等 → 整轮被 1 个慢副本拖住（it9 实测末尾 1 局空等至 task 超时）。双缓冲
挤出的墙钟被尾部慢速全数还回。

**处置**：queue worker 在排队队列已空时，**只要空槽**就复制一个 in-flight 任务竞速
（每任务副本数上限 tailFanoutDup=2，防无限复制）——**不看任务已耗时**（用户裁定）。
选择逻辑抽为纯函数 `pick_tail_race`（字典序最小，锁内确定性）。

**配套**：
- **去重结算修复**：成功分支从「仅 fanout 副本检查 dup」改为「所有后到副本一律丢弃」——
  漏网的（main 后到/竞速副本后到）重复 append 曾导致报告 ok=3/2、seen 触顶但 all_settled
  不触发 → 每轮空等 deadline 120s（集成 I1 实测 0.2s→120s）。
- **集成测试 mock 化**：I3/I4 的 PPO 以 `_StubPpo` 桩替代（stream 的 `backend` 注入点），
  不再 build 真 torch 模型；rollout 由 FakeAgent 合成包承担（TS 引擎真实性由
  tools/sim/export-rl-rollout.ts 单测保证）。新增 **I6**：slow_first 注入 2s 慢副本 →
  验证空闲槽竞速复制（dispatch ≥2）且快速收官。
- **测试 cfg**：`agentRescanSec=1`（默认 120s 轮询会让每轮收官白等 120s）。

**效果**：集成全套 146s（原 ~10min+）；v3.10 上线后 S3 尾部慢速局由竞速兜底。

## §303a v3.11 竞速副本只派快节点（2026-08-31，用户观察"副本落到慢节点=白等"）

**问题**：长尾竞速虽已派副本，但**副本可能派到慢节点**——两个副本都落在慢速节点上时
仍是等慢的（竞速形同虚设）。

**处置**：竞速副本只派给 top-N 快节点。纯函数 `race_tier_ok(speeds, nid, top_n=3)`——
- 本机（local）豁免（实测最快、无网络往返）；
- 无速度样本（首轮/全空）乐观放行（没数据不该设门槛）；
- 否则按 EWMA 平均耗时（speed 表 = 各节点最近任务平均耗时）取 top_n 快档，
  不在快档的节点不参与竞速（慢节点对竞速是负资产）；
- 节点数 ≤ top_n 时全员参与（退化回无门槛）。

**配套测试**：`test_race_tier_ok`（9 断言：top3 内全过、第 4 快排除、最慢排除、
local 豁免、无数据放行、节点数 ≤ top_n 退化为全员）。

## §304 v3.12 eval 最低优先级：软等待 + 后台消化 + 集成测试（2026-08-31，commit e79ca5c）

**用户方向**（三连）：eval 可以慢慢做（利用后续迭代采集/PPO 间隙消化）；远程节点算力
充裕（it11 PPO 期间 it10 eval 大概率已完）；eval 最低优先级，必须写集成测试保障。

**问题（v3.12 前）**：PPO 收尾后 `eval_thread.join(timeout=budget)`，budget=eval_window_sec+60
=1860s 全额等账——eval 慢时拖死主链下一轮（日志 "waiting up to 1860s"）。

**处置**：
- 全额等待 → **软等待 ≤180s**（`soft = min(budget, 180.0)`）：只吃已收官尾巴 + 给在途
  eval 局缓存缓冲（防下轮新权重 POST purge 掐掉），长尾 eval 留到 it+1..N 采集/PPO
  空档消化（节点任务队列天然仲裁：采集忙 eval 排队，采集 done eval 补做）。
- 账按 **wver 晚入**（`eval_done_keys` 按 wver16 去重，晚到不重跑）；门判定读 eval_log
  的 eval_summary（iter 保留原轮号 + wver），晚入账只顺延判定窗口，判据不变。
- 溢出预算未收官的在途局：下轮异 sha 清场 + 阈值熔断兜底（同 v3.10 前语义）。

**实测证据**（重启前旧代码进程，恰证用户预判）：it10 eval 3.5min 收官（it11 启动前），
it11 eval 20min 与 it11 PPO（~19min）重叠完成——eval 天然在训练间隙消化、不占主链。

**I7 集成测试**（test_run_rl.py，FakeAgent.eval_delay=3s/局慢 eval 注入）：断言 ①eval
慢速在途时下一轮采集照常完成不阻塞（games==2, missing==[]）；②采集完成后 eval 仍在
后台跑（is_alive()）——证明"不抢主链、后台消化"。修测点：eval_log 台账按 wver 去重，
残留同 wver 记录会让 eval 全量 skip 早退 → I7 前清共享测试 tmp 的 eval_log.jsonl。
I1–I7 全套 + 单测 + freeze gate 全过。

## §305 v3.13 提前预采首波：epoch3 快照 spawn + 双 wver 对账 + stream 首波注入（2026-08-31，commit c1e33db/3ebf8d2）

**问题（用户 + 观察者双重指出）**：v3.10 双缓冲 spawn 在 PPO **全部结束之后**（run_rl.py
原 755 行），预采 600s 是**串行等待**（it13→14 实测 gap 252s … it16→17 736s），并未藏进
PPO。观察者建议"spawn 提前到 PPO 开始"——但 S3（stream waves=0）PPO 前期权重=θ_{N-1}，
提前 spawn 会整轮 off-policy。用户修正方案（2026-08-31 拍板）：**PPO epoch3/4 完成时**用
当前权重（θ_{N,e3}，差最后一段梯度、on-policy 带内）存快照并 spawn，预采**只采下一轮首波**
（--precollect-games 12 局），墙钟藏进最后 1 个 epoch；其余 ~138 局由下轮以 θ_N 严格现场采。

**改动**：
1. **epoch 完成回调**：ppo.py / ppo_intent.py 的 update 增加 `on_epoch_done(ep_done, model)`
   （stream 透传给 backend.update）。主循环回调在 `ep_done >= epochs - precollect_early` 时
   `save_weights_json(model, weights-collect-{it+1}.json)` + `_spawn_collect_next(snap_src=…)`。
2. **预采限局**：`--precollect-games N` — collect-only 子进程只采前 N 局（首波 wave 语料）。
3. **双 wver 对账**：`completed_pairs/resumed_manifests` 增加 `extra_wver`（预采快照 θ_{N,e3}
   指纹）；主循环 `_precollect_snapshot_wver` 回读 weights-collect-{it}.json → 下轮对账双白名单，
   否则预采首波被当"未完成" rmtree 清场。run_rollout_queue 同样透传（stream 的 collector）。
4. **stream 首波注入**：collector 启动前把盘上 extra_wver 匹配的首波 shard 注入 pend（作为
   第一 wave 语料），collector 对账跳过它只现场补采剩余局——两批语料本轮都被训练。
5. **尾部 spawn 防重复**：`_spawned_early` 置位后循环尾不重复 spawn（避免双 spawn 同快照）。

**修测点（3ebf8d2）**：提前 spawn 时调用方已 `save_weights_json` 写好目标文件，`_spawn_collect_next`
内部 `copyfile(snap_src, snap)` 因 src==snap 抛 same-file → precollect 静默失败。改：abs 路径相同则
跳过 copy 直接复用。

**集成测试 I8**：盘上预置 extra_wver 首波 shard → run_rollout_stream 应 ①注入训练（seed pend）、
②collector 只派剩余局（dispatch 不含首波对）、③报告覆盖全计划（games==2）。实测三断言全过。

**实测（重启后 it19）**：`resume: 85/150 已在盘 + 65 remaining`——历史预采与现场补采混合，对账
（含 extra_wver）正确识别；it20 起将出现"提前 spawn 藏进 epoch4 + 只采 12 局首波"的稳态行为。

**语义说明**：首波 12 局用 θ_{N,e3}（≈θ_N，kl 通常 <0.02，PPO clip 0.2 带内），IS 分母取
快照采样的 lp（on-policy 数学不破坏）；剩余 3/4 严格 θ_N。失败救济 = 只废弃 12 局（而非全量）。
训练曲线与历史有轻微口径差异（首波半代滞后），记 DECISIONS 备案。

## §306 远控重启护栏：脏工作区拒发 + 跨代去重 + agent grace 窗口（2026-09-01）

**问题**（用户报告 + 节点实测日志）：① 远控重启过的进程被再次远控重启（10:16–10:18 四连杀，节点始终无法贡献）；② 用户手动更新代码重启的进程被远控杀掉再重启。

**根因**：expected codeHash 由训练机**工作区**文件内容算出（`_collect_code_hash_files` 直读磁盘），含未提交改动；远端 `git pull` 只能拿到已推送提交，hash 永不收敛 ⇒ 每轮 ping 门 / rescan（~15s）都判 stale ⇒ 再杀再拉成死循环。叠加：旧实现去重集合 `upgrade_requested` 是每轮局部变量，新一轮迭代重建 ⇒ stale 节点每轮再收一次 restart。

**处置**（三层，全部带单测）：
1. `dist_common.dirty_hash_files()`：对 hash 集跑 `git status --porcelain`，检测未提交文件；`request_upgrade_guarded()` 在脏工作区时拒绝对**远端**节点下发 pull+restart（`dirty-tree:N`）——pull 无法收敛时重启纯属无效扰动。self/回环节点豁免（代码同源，纯重启有效，禁 pull 语义不变）。
2. `dist_common.request_upgrade_guarded()`：跨代去重——同节点 + 同 agent codeHash 只下发一次 restart（`dedup`）；节点 hash 变化（pull 生效 / 手动更新）自动恢复资格。替换掉 queue.py 每轮重建的 `upgrade_requested`；queue.py ping 门与 rescan 双调用点接入，脏树 WARN 每轮一条。
3. `tools/agent/restart-guard.ts`：agent 侧 grace 窗口（30s，覆盖 rescan 两个周期）——进程启动窗口内的 `/v1/restart` 是协调器重扫回声，回 409；`request_upgrade` 对非 200/202 记失败、不写去重状态，下轮必然重试。`restart-guard.ts` 纳入 codeHash 集（agent 重启行为变更必须触发升级波）。

**测试**：`nn-training/test_upgrade.py`（+4 用例：跨代去重 / 脏树拒发+self 豁免 / upgrade_stale_nodes 脏树 / porcelain 解析+冒烟；注意 mock 绑 127.0.0.1 会被判 self，远端用例 monkeypatch `is_self_node`）；`tests/agent/restart-guard.test.ts`（grace 边界 4 用例）。`test_run_rl.py` 全过、tsc / oxlint 绿。

**运维语义**：训练机工作区有未提交的 hash 集改动时，远端节点被抑制重启并保持 excluded（日志 `dirty-tree:N`）；要节点升级 = commit + push（run_rl 启动时已自动 push 分支）→ 下轮 rescan 下发升级。agent 日志新增 409 `restart-grace-period`。
## §307 RL 入口整合：run_rl_intent（含 --goal）并入 run_rl.py（2026-09-01，用户拍板" 直接删除）

## §307 RL 入口整合：run_rl_intent（含 --goal）并入 run_rl.py（2026-09-01，用户拍板直接删除）

**决策（D1–D5，plan/RL-Entry-Consolidation.md）**：run_rl.py 成为唯一 RL 入口，--mode {per-tick,intent,goal} 参数化三后端；--goal 保留为 --mode goal 别名。rl/eval_m1.py 承接 intent/goal 的 m1-eval 评估管线（与 eval_dispatch 双轨并存，D3）；rl-config 查找顺序 rl.<mode> → intent_rl 遗留块 → rl（D2）；止损泛化为 --stop-loss-at/--stop-loss-delta（D4）。run_rl_intent.py + test_run_rl_intent.py 直接删除（D5，intent 战役不再续跑）。

**理由**：机制层（rl/ 包）早已共享；残留差异仅剩后端/采集器/评估/配置四处绑定点，全部可参数化。per-tick 默认路径行为字节一致（回归护栏 = test_run_rl 快速层）。

**验证**：test_run_rl / test_run_rl_m1 / test_ppo_intent / test_ppo_goal / test_ppo_common 全 PASS；无遗留 run_rl_intent 引用。

## §308 RL 训练配置化 M1：公式引擎 + metrics.npy + 课程启动通道（2026-09-02，plan/rl-training-config.md v8）

**交付**（M1a/b/c/d 主体落地；golden/单测全绿，tsc/oxlint/oxfmt/ruff 通过）：
1. **M1a 公式引擎**（`rl/reward_library.py`）：AST 白名单求值器（无 eval/Attribute/Import、限长 1024/深 64、分层白名单核心+扩展 opt-in、唯一归约 helper `wavg`=特征轴；白名单无任何时间轴归约函数，单测锁死）。`RewardSpec/RewardFn` 支撑 toy 与 score_reconcile（telescoping：Σr ≡ scale×gatedScore）。JSONC 限行注释剥离器（`rl/jsonc.py`）。
2. **M1b metrics 落盘**：`export-rl-rollout.ts` 删全部 TS 侧奖励计算（v7/toy 势、paidTotal、对账），改落 `metrics.npy [N+1,21] f8`（N 决策快照 + 终局快照）+ manifest `metrics_version:2`；`ppo/engine.py` 加载器改读 metrics + manifest → holder RewardFn 算 reward（无 holder 响亮报错）。`metrics_stats.py` 每 iter 落 21 维统计。
3. **M1c 超参 schedule**：`ppo_schedule` 按绝对 iter 查表（lr 保 Adam 动量/epochs/mb 每轮改写/kl_coef 新增 `ppo_update` 形参默认 0 向后兼容 + 采样策略 KL 惩罚）；加载期 holder（`rl/reward_context.py`，frozen）承载 reward_fn/gamma/lam/it；冻结前缀表进 `_setup` 优化器只收可训参数。`--course`/`--course-file`/`--echo-config` 启动通道（课程 > rl-config > 默认，无 CLI 逐参覆盖）。
4. **M1d 远端透传**：`--stage-json` → `decodeStageGrid`（src/nn/config-stage.ts，13×13→26×26、enemyCount 恒显式、出生点 2×2 冲突校验）四守卫短路；agent 能力位 `stageJsonSupport` + stageJson 布局指纹（sha256[:16]）进 resultCache 键（无 stageJson 时逐字节不变）；lives/level override 全链路。
5. **v7 保真**：`reward_builtin.v7_phi` + `curricula/s4b.jsonc` 公式（607 字符，`wavg`+`clip`+`where`）对 TS oracle（rl-reward.ts phiNow）256 行**逐位一致**（max|Δ|=0）；golden 文件 + bun oracle（`tools/diag/v7-phi-oracle.ts`）。
6. **回归修复**：`rl/{queue,queue_local,archive}.py` REPO_ROOT 修正为仓库根（原少一层 → nn-training，本地 spawn exporter 报 module-not-found、归档落到 nn-training/nn-training/weights——2026-09-02 OO 拆分引入的既有回归，本次顺手修正）。

**关键取舍**：idx10 空槽按「连续编号 + 已编号项不变」补 `starsCollected`；manifest.score 本就是 gated（F3 门控在 TS 完成），Python 不再二次乘 BASE_LOSS_MULT（避免双重门控）；rl-reward.ts 的 `basePressureMean` 字段实为 sum（oracle 按 rollout 口径 sum/samples 喂入，命名坑已注释）。S4a 移出本期（随 A7）。v7 公式 607 字符未触降级卡——`reward.builtin` 机制保留（warning+回退）但不作默认路径。

**验证**：`tests/test_reward_golden.py`（安全边界/wrapper/N=1/末样本差异/telescoping/golden-file/v7 逐位）、`test_metrics_shard.py`（加载器端到端/版本分支/无 holder 报错/行失配报错）、`test_rl_schedule.py`、`tests/config-stage.test.ts`、`tests/dist-agent.test.ts` 全绿；`python run_rl.py --course _smoke`（1 局 arena）与自定义关 stage-json 直跑端到端通过（Σr 恒等式 + 列序抽查）。课程配置 = 配置机制验收夹具（S1/S2/S3/S-Dodge/S4b，不再实际训练）。

## §308b M1 收尾：尾逗号容忍 + 课程/CLI 冲突 fail-loud + eval 双侧同规（2026-09-02，commit e828331 后续）

**尾逗号**：首批 `.jsonc` 样例带 JSONC 惯例尾逗号（末项后 `,` + `}`）——Python `json.loads` 严格拒绝，且 `//` 注释在逗号与闭合符之间时简单文本清理会漏。处置双层：① `rl/jsonc.py` 的 `loads()` 加 `_drop_trailing_commas`（字符串外扫描，维护 in-string/转义，仅删 `,` 后随 `}`/`]` 者）；② 六个课程文件清理。加载流程 = strip_comments → drop 尾逗号 → json.loads（单测覆盖）。

**课程/CLI 冲突**（plan §3 fail-loud）：argparse 无法区分「显式传参」与「吃默认」——以 `ap.parse_args([])` 的默认命名空间为基线，凡 CLI 值 ≠ 默认 且 该键在课程 flat_overrides 覆盖集内 → SystemExit 列出冲突（此前课程静默覆盖用户显式参数）。`course_cli_conflicts()` 纯函数 + 单测。

**eval 双侧同规**（plan §6/§10）：`export-eval-game.ts` 增 `--stage-json/--lives-override/--player-level`（decodeStageGrid 短路、自定义关 loadIndex=0、覆盖在 S-Dodge 默认之后生效）；`eval_local.run_local_eval_game` + `eval_dispatch` 本地评估按课程透传；sampler-agent eval 分支同传。自定义关 eval 直跑冒烟通过。说明：legacy 非课程 arena eval 仍保留 exporter 内 S-Dodge lives=1 默认（课程路径以配置覆盖为准，双轨共存不破既有评估基线）。

**验证**：nn python gate ✓；tsc/oxlint/oxfmt ✓；bun 13 用例 ✓；test_run_rl fake_runner 签名同步（+3 可选参）。

## §308c 评审 F1–F3 处理（2026-09-02，commit e828331/8b849e4/0dec734 之后）

**F1（中）降级卡触发面收窄**：`RewardSpec` 降级回退从「任意 FormulaError」收窄到新异常
`FormulaDegradeError`（机制性量化限制：formula>1024 字符 / AST 深度>64，专用异常由
`parse_formula` 抛出）——语法错误、白名单外函数、未知名 params（wq 实测案例）现在带
builtin 也**响亮 raise**，不再静默回退内置；warning 文案拼入真实异常文本。`validate_reward`
同语义（配置错误记 errors 硬失败，仅量化触发回退 warning）。单测
`test_degrade_only_on_quantitative_triggers` 覆盖四象限。

**F2（低）envelope 扩展层误报**：`symbolic_envelope` 增 `allow_extended_funcs` 透传（子项
compile 同参）——有界扩展函数（tanh/sin 等）不再被误标 inf「数值包络超限」污染启动日志；
关闭扩展层时白名单外函数走 F1 的配置错误路径。单测 `test_envelope_extended_funcs_no_false_positive`。

**F3（低）plan 文档-实现回填三处**（行为安全、措辞更新）：
① §4.2 param_schedule mode = 硬编码 (linear, step) + 未知 mode 响亮报错（非自由字符串）；
② §5.2 布局指纹 = `sha256(stageJson 串)[:16]`（较字段级 FNV 更保守：key 序变化 → miss 而非复用）；
③ §4.1 21 维表回填 idx10 `starsCollected`。F4–F6 信息级确认无需改代码。

**验证**：nn-training pytest 全绿（含 4 个相关单测）；ruff/mypy 门禁通过。

## §309 新课程 S5-open20：20×20 空旷无基地 / 一命无星 / 20 敌 4 类混编（2026-09-03）

**目标**：AI 学会走位杀敌、闪避子弹、主动捡道具。地形=课程自定义关（2000-2002，三张出生点变异）：
13×13 grid 钢外框 + 10×10 cells = 20×20 tiles 开放区、无基地码；forces=basic×5+fast×5+power×5+armor×5
(count 20)；player.lives=1 / level=0（导出器 CLI 覆盖）。dodge 因自定义关守卫④强制 off——闪避纯靠奖励学。

**起始权重决策**（用户点名 S2 终点）：候选 = a2-kill（S1 1 敌）、s2-cap（arena S2 size14 空旷 3 敌，kill2，
30 iter，**域内 winRate 0.549**）、s3-cap（S3 迷宫 8 敌 kill2，0.603）、s3-cap2（S3 迷宫 balanced，**0.78**）。
迁移实测（新图 2000，3 seeds，一命无星 20 敌）：**s2-cap 均击杀 3.67 / 命中 11.33 / 存活 1347 / 捡道具 0.33**
显著第一（s3-cap2 2.67/7.00/1101/0.33；a2-kill 与 s3-cap 均 ~0.33 杀）。结论：**域内强 ≠ 迁移强**——
S3 迷宫掩体策略在空旷 20 敌图上失效；选同为空旷场出身的 **tmp/s2-cap/weights.json**（两重证据：用户点名 +
实测迁移第一）。

**奖励设计**（Φ 势 + diff，scheme=toy——无基地禁用 score_reconcile，v7 分守家维度缺失/承压恒 1 语义扭曲）：
- 杀敌：`wKill*(kills+1)**1.15`（超线性）+ `wHit*enemyHits`（密集正反馈，0.3）+ **首杀跳变**
  `wFirst*where(firstKillTick>=0,1,0)`（+1.0，0→1 击杀冷启动梯度）
- 闪避：−`wDmg*playerHits`（0.6→1.5 升温）+ −`wDmg2*playerDamageTaken`（**0.005**——初稿 0.05 使死亡扣血
  累计 200 → −10 支配整局 Σr≈−13，实测修正）
- 道具：+`wLoot*powerUpsCollected`（0.8→0.5）+ +`wStar*starsCollected`(0.3)
- 走位：−`wStuck*max(0,stuckTicks−120)`(0.02) + −`wShot*playerShots`(0.01)；**不放** cellsVisited 正项
  （防乱走刷分）
- terminal：stage_clear +6 / lives_exhausted −3 / timeout −1
- 超参：lr 1.5e-4 / epochs 4 / mb 512 / gamma 0.99 / lam 0.95 / seed_rotate 8（每图每轮 8 新 seed）/
  max_ticks 12000 / 40 iter / ppo_schedule kl_coef 0.6→0.2→0（防继续训练早期漂移）
- 量级实测：好局（10 杀/命中30/道具3/星1）Σr≈+21.8 vs 差局（0 杀死亡）≈−2.0

**已知限制（诚实标注）**：一命满血 200 → 被击中即死，playerHits 每局 ≤1、damageTaken 无中间态；21 维指标
无子弹近距/敌距类**密集**信号 →「闪避」在现指标下只有死亡二元负反馈可学。缓解路径（视 it10 表现再选）：
加「存活 tick 微正项」给闪避密集梯度（代价：苟活得分）/ 扩展指标 v3（子弹近距/敌距——需 TS 落盘 + 双侧同步，
工作量在 M2+）。评估：eval_stages 2000-2002 × 8 局，eval_every 5。

## §310 S5 测试 iter 全链路体检：5 个修复 + 性能 profile + 激励函数检验（2026-09-03）

**工作流体检（s5-open20 测试 iter，跑 3 遍完整 1-iter）暴露并修复 5 个真实缺陷**：
1. `rl/eval_local.py` REPO_ROOT 少一层（OO 拆分同族回归，与 queue_local 2026-09-02 同病）→ 本机 local eval spawn cwd=nn-training → `Module not found tools/sim/export-eval-game.ts`。→ parents[2]。
2. `loop_core` `_eval_every = int(... or 1)` 把显式 `eval_every=0` 吞成每轮（想关闭 eval 却每轮都跑）→ 0=关闭，默认仍每轮（字节一致）。
3. `eval_dispatch`：eval_stages 含自定义关（≥2000）时无能力握手 → 旧 agent（mac，无 stageJsonSupport）收到 stage=2000 走 arena/真实关解析 → `stage.tiles` null 崩溃。→ need_sj 时要求 ping.stageJsonSupport，任务落本机；fetch 透传 stage-json/lives/level（与 rollout 同规）。
4. `dist_common.write_shard` 不写 manifest.json → M1 metrics 方案下分布式/self-node 局落盘缺 outcome/score/metrics_version → engine 加载器把这类局错标 timeout，**奖励错算**（M1 引入回归；queue_local/exporter 直写路径无此问题）。→ write_shard 补写 manifest.json。
5. `export-rl-rollout --pack` 的 BCV2 manifest 用**聚合 summary**（缺单局 outcome/nSamples/metrics_version）→ 修复为单局 shard manifest 基底（lastShardManifest + mode/elapsedSec）。已单局解包验证：manifest 含 outcome/nSamples/metrics_version 等单局键。

**远程节点（mac）可用性结论**：mac 在线且能收 upgrade 请求，但不可用 —— 根因链：本地 push 无凭据（`git push origin goal-nn` rc=128，origin 停在 dd163ac）→ mac `git pull` 空转（Already up to date）→ codeHash 恒 stale → 每轮 run 触发 upgrade/restart → mac 端 restart 竞速（手动起 agent 与旧实例并存 → EADDRINUSE）→ 反复掉线。修复路径：① **先 push**（凭据/ssh/手动），mac pull 到新代码后 codeHash 匹配即自动正常；② 别手动起 agent（端口双实例自残）。dirty-tree 抑制（本提交未 push 时）已阻止对 mac 的无效 restart（第 3 遍日志：`remote restart suppressed (dirty-tree:1)` ✓）。

**性能 profile（本机 CPU-only）**：
- TS 侧：单局分段 98.4% 在 `model.forward`（稳态 ~39.6ms/次 → 12000-tick 满局纯推理 ~48s）；sim.tick 仅 0.8%（0.03ms/tick）。→ 吞吐瓶颈 = NN 推理；优化候选：onnxruntime/wasm、int8、降 K、模型裁剪（量级工作，另行立项）。
- Python 侧数据管线全部亚秒：reward_fn 0.1ms/局、metrics_stats 46ms、np.load 3.2GB/s、GAE 0.3ms/局 → 非瓶颈。
- 整 iter 实测（第 3 遍 local-only）：collect_wall≈142s（24 局 8 workers）、PPO CPU 321s（22 chunks×4ep=88 步）→ **PPO 占大头**（local-only 全量盘全量更新路径）；stream 双波路径（第 2 遍）collect 152s + PPO 190s。
- load_cpu=0s：加载不是瓶颈（M1 架构红利再次确认）。

**激励函数检验（修复后 24 局，outcome 全真值）**：
- step reward 79% 零步（稀疏，符合"一命局多数在移动"）；非零步 std 0.72。
- Σr/局@it1：min −6.14 / p25 +0.04 / 中位 +4.12 / max +50.37（20 杀全歼局）→ 不再全负、正负分明。
- **激励方向单调正确**：高杀局（≥3 杀）Σr 中位 +4.26 vs 低杀局 −6.14。
- kills 中位 6 / 总 162/480 / 24/24 局有击杀 / 1 局 20 杀全歼（stage_clear）→ 起点策略（s2-cap）在 20 敌图上已有动作基础。
- Σr@it25 整体下移 ~3（wDmg 0.6→1.5 升温生效方向正确）。
- **归一化状态**：adv 全局归一 ✓；ret/value **未归一** → Σr std 11.05 → value 头 raw MSE 量大（PPO value≈0.99 仍在学，gnorm 0.9-2.3 可控）。评估：可接受；若后续 value 收敛慢可加 ret 归一对照（intent 已有 normalize_ret 先例）。
- 一命二元性确认：100% 局死亡、挨打 3.04 次/局、扣血累计 859（≫200 满血）→ 「闪避」仍是死亡二元为主；缓解路径沿用 DECISIONS §309（存活微正项 / 指标 v3）。

**建议的下一步**（按序）：① 提供 push 凭据让 mac/a97/a98 升级（否则只能本机 8 workers）；② commit 本批修复后正式起 s5-open20 40 iter；③ 若 40 iter 中 value loss 持续 >0.5，考虑 per-tick normalize_ret 或 terminal 尺度下调；④ NN 推理加速（TS 侧 98% 瓶颈）单独立项评估。

## §311 TS 推理性能调优结论：JS 标量循环已达上限，需结构性方案（2026-09-03）

**背景**：S5 正式训练跑完 it2（08:20 结算后按用户指令停止）转入调优。§310 profile 定 TS 侧 `model.forward ~39.6ms/次` 占 rollout 98%。

**实测调优过程（conv1x1 pointwise，占 ~60% forward）**：naive（内层 ic 跨 676-float 平面跳读）→ 纯外积（out 写放大 64×，43.0ms 更慢）→ 分块外积 T=16（39.3ms）——三种实现均 ~39-43ms，**缓存重排无效**。结论：瓶颈是 **JS/JIT 标量循环上限**（37M MACs/forward ≈ 1.9 GFLOPS，JIT 标量 ~1ns/MAC 的典型水平），不是访存。conv1x1 已回滚（git checkout src/nn/infer.ts），保持逐字节行为。

**Python 侧确认**：数据管线全亚秒（§310）；torch_threads=8（rl-config）已生效、16 核机器 → PPO 与 rollout 平衡合理，无需改。

**后续候选（均需立项评估，非配置可及）**：
1. **WASM SIMD conv**（自写 conv3x3/5x5dw/1x1 → SIMD）→ 预计 conv 3-8×，forward 落到 ~8-15ms，rollout 提速 2-4×（最大单点收益）；
2. onnxruntime-node（原生 addon，bun 兼容性待验）或 int8 量化；
3. PPO 期临时 torch_threads 8→12-16（rollout 暂停期空核利用，~20-30% PPO 增益，影响小）；
4. 结构层：决策频率 K、模型裁剪属训练口径/架构决策，不列入推理侧。

**训练恢复**：s5-open20 已结算 it1-it2（training_log iteration 2 = last），同命令 `python run_rl.py --course s5-open20` 从 it3 续跑（resume 按 last_completed_iter，权重 tmp/s5-open20/weights.json = it2 产物）。

## §312 TS 推理提速①落地：conv_feats.wasm（WASM SIMD，×5.1–5.7）+ 方案②可行性

**背景**：§311 定 model.forward ~39.6ms = JS 标量上限。用户点试两方案。

**方案① WASM SIMD conv —— 已落地并验证**：
- `src/nn/wasm/conv_feats.c`：clang `--target=wasm32 -O3 -msimd128` 编译（无 libc、静态对齐 scratch）；把 StudentModel 卷积段（stem conv3x3 16→64 + 8×[depthwise 5×5 + pointwise 1×1 + relu + residual] + GAP）整体移入 wasm——**外积排布 + pad-拷贝去边界分支**让 LLVM 自动向量化 f32x4。
- `src/nn/conv-wasm.ts`：懒加载单例（memory 自 1MB 布局避开 .bss；权重按实例引用只上传一次，每帧只拷 in16 43KB）；`infer.ts StudentModel.features` 在 h64/d8/board26 时走 wasm，失败/架构不符自动回退 TS 原路径（双重兜底）。
- **实测**：features 6.9ms vs TS 41ms（×6）；forward 稳态 **39.6→6.9ms（×5.7）**；单局端到端 1730→337ms（×5.1）；12000-tick 满局纯推理 48s→8s。
- **正确性**：真实权重 pooled max|Δ|=4.8e-6（累加顺序级）；tests/conv-wasm.test.ts 随机权重 3 帧相对误差 ≤1e-3（防回归）；freeze 确定性门禁 OK（God-AI 基准不涉 NN）；exporter 冒烟正常。
- 踩坑记录：typedarray `.set` 目标短于源 view 抛 Range（pooled 须 subarray 限长）；wasm 默认 memory 小需 grow；JS 布局偏移需字节计。

**方案② onnxruntime-node —— 可行性确认（未落地）**：`npm i onnxruntime-node` 成功；**bun 可 require 加载**（N-API 兼容 ✓）。完整落地还需：torch 模型从 weights.json 重建 → onnx 导出 →（可选 int8 量化）→ 推理集成 + 数值/确定性验证——链路长于方案①且已获 ×5.7；建议仅在需要更高倍率（onnx+mkl 预估 ×10-15）或 int8 显著省带宽时立项。

**下一步建议**：恢复 s5-open20 训练（resume it3 起）——rollout 提速 ×5 后单 iter 墙钟主要被 PPO(torch CPU 190-320s) 主导，40 iter 预估 ~3h；机器空闲时恢复即可。

## §313 提速方案异构平台兼容性评估（2026-09-03）

**方案① conv_feats.wasm（已落地）——可直接兼容**：
- 产物实测依赖 WASM SIMD（v128 指令 410 条；同 C 源去 -msimd128 得标量版 5239B/v128×2）。SIMD 为 wasm 正式特性（2021），bun 各平台（macOS/Linux/Windows/WSL）内嵌引擎默认支持；Android Termux proot Ubuntu 若可跑 bun（x64/aarch64）同样支持。
- **跨节点确定性保障**：① wasm 字节码跨平台同执行；② dispatch 已有 **bun major.minor 版本红线** → 同轮节点引擎一致 → SIMD 能力一致 → 全走 wasm 或全走 TS，不会 wasm/TS 混跑（1e-6 输出差的 argmax 边界翻转只发生在混跑下）；③ 无 SIMD 的极旧引擎 compile 抛错 → 自动回退 TS（正确性兜底，性能降级不崩）。
- 结论：无需改造；节点唯一前提 = 能跑 bun + exporter（分布式既有基线）。

**方案② onnxruntime-node（未落地）——不可直接兼容异构集群**：
- N-API 原生 addon：官方 prebuilt 仅 win/mac(含 arm64)/linux(x64/arm64) → **Android/Termux 无包**；bun 加载 N-API 需逐平台验证（当前仅 win 冒烟 require 成功）。
- 数值确定性：onnxruntime 按平台后端（MLAS/oneDNN AVX vs NEON）累加/融合不同 → 同模型跨平台输出 ~1e-6~e-4 差异；int8 量化引入 ~e-2 量化误差 → **混跑即破坏同 seed 确定性**（M4 红线）。
- 适用边界：仅同构单平台集群（x64 Linux）且逐平台 golden 校核后可考虑；int8 必须全量统一启用。

**结论**：分布式继续方案①；② 不引入异构。

## §314 v3.14 竞速可见域修正：主副本派发一律登记 inflight（2026-09-03，it6 实测）

**问题（s5-open20 it6 实测，本机低 CPU 窗口 09:05:15→09:07:23）**：v3.7 派发登记条件是
「出队时 pending ≤ tailFanoutN(4) 才写入 inflight 表」，而 `pick_tail_race` 只从该表选
候选——**早派任务对竞速机制不可见**。it6 中 a97 于 08:48 升级重启、09:03:18 才 rejoin，
权重重灌 + worker 拉起期间分到它名下的 3 局在节点侧积压（settle 时 elapsed 仅 2.1s，
即 ~09:06 才开工）；这 3 局均为早期派发、不在 inflight 表内。PPO 09:05:15 结束后本机
让位槽按设计不竞速，mac/a98 空闲槽想竞速但表已空（唯一成员 seed474308045 已结算），
→ 整轮空等 a97 ~2min（collect_wall 249s，对比本地轮 4.8s）。

**修正**：主副本派发**一律** `register_inflight(inflight, task)`（新纯函数，
`queue_local.py`；queue.py re-export；dispatch.py 派发处调用），不限 src、不看 pending
余量。登记即竞速候选；`tailFanoutDup=2` 副本上限与 `race_tier_ok` top-3 快节点派档
仍然兜底，登记面扩大不会放大复制（竞速仅在排队队列已空时空槽触发）。

**保持不变的语义**：
- requeue 不出表、再派发再登记（计数累加），终态由 settle 全 pop / 失败路径扣减——
  均为既有逻辑，未改动；
- `missing_keys` 分支不清理 inflight（保留竞速副本"抢救"失败局的通道）；
- 本机 `local_suspend` 让位槽不竞速（v3.10 让位语义：给 PPO 腾核，不抢尾流）。

**测试**：`tests/test_run_rl.py::test_register_inflight_v314`（登记即候选 + requeue
累加 + dup 满跳过）；集成层 `test_integration`（含 I6 慢任务被空闲槽再竞速端到端）回归。

**预期效果**：拖尾局（无论派发早晚）在排队队列清空后即被空闲快槽竞速；轮末同步屏障
等待时长从「最慢节点开工+执行」缩到「min(主副本, 最快竞速副本)」。

**v3.14b 同日增补（§6.4 dated note）——集成测试编排化 + rescan halt 感知**：
1. **集成层脱离真实依赖**（用户裁定"编排测试不需要真权重"）：去掉 `bun on PATH +
   tmp/rl-weights/weights.json` skipif 与 standalone 前置检查；权重改 tmp 哑文件
   （wver=文件指纹，任意内容皆可）；本地直跑 `run_local_rollout` 在 `rl.dispatch`
   命名空间 monkeypatch 打桩（返回同构最小 summary，不 spawn bun 不写盘）。
   集成测试从此零外部 fixture、`RUN_RL_ITEST=1` 即跑。
2. **rescan halt 感知（生产修复）**：`rescan_nodes` 循环退出条件不含 halt_event →
   KL 熔断后主线程 `join(timeout=max(30, window+taskTimeout))` 白等满超时
   （queueWindowSec=120 时实测 180s/次）。追加 halt_event 参数（19 参，缺省 None
   兼容旧调用方），循环条件加 halt 检查——熔断后 dispatch 立即收尾。
3. **I9 判别修正**：早派任务竞速的判别用「竞速副本 dispatch 发生在慢窗口内
   （+0.28s < 1.5s）」，不用墙钟——迟到主副本的在途 sleep 两代语义都必须等，无区分度。

## §315 轴 2 补测试：per-tick 策略头（move/fire/value-128）torch↔TS parity golden（2026-09-03）

**背景（审计 gap 确认）**：`goal-infer.test.ts` / `intent-infer.test.ts` 的 golden 校验的是
StudentNet 主干 + 各自专用头（goal_conv/engage、intent/enemy/anchor），**从不触碰
PPOStudent 的 `move_head` / `fire_head` / 128 宽 `value_head`**——而这正是活路径
（`export-rl-rollout.ts`，s5-open20 权重 `kind='student' h=64/d=8 head_hidden=128` + value）
在采样的三头。轴 2（torch↔TS 前向语义一致）此前对该路径无自动化回归网；任一侧改这些头
或主干都可能静默漂移。

**修正**（复用 goal/intent 既有 golden 模式，两规格覆盖两条推理路径）：
- `nn-training/models/student.py --golden <out> [--h --d --golden-seed]`：新增 PPOStudent
  + value_head 的固定 seed golden 导出（`export_student_golden`，镜像 `goal_net.py` 模式；
  输出 `{format:"student-golden", h, d, head_hidden, seed, obs, scalars,
  moveLogits[5], fireLogits[2], valueLogits[1], params}`）。
- 两个 fixture：`tests/fixtures/student-golden.json`（瘦身 h=16/d=2，TS 手写循环路径）、
  `tests/fixtures/student-golden-wasm.json`（生产 h=64/d=8，走 conv-wasm，参数数 42 与
  s5-open20 活权重同构）。
- 新测试 `tests/nn/student-infer.test.ts`：`buildModelFromText`（与 export-rl-rollout 同一
  构建入口）→ `forward()` → 两规格各断言 move/fire/value 三头 ≤1e-4（沿用 intent golden
  容差先例；wasm 卷积段 §312 实测 pooled max|Δ|≈4.8e-6 远在容差内）。

**验证**：37 pass（4 个 golden 文件：coord/intent/goal/新增 student）× bun test；typecheck 绿；
ruff+mypy 对 student.py 改动干净。基底仅新增 `export_student_golden` + `__main__` argparse，
`StudentNet/PPOStudent` 本体零改动（不加依赖、不触训练路径）。

**再生成（维护说明）**：改 torch 侧学生网结构后需重新生成两 fixture：
`python models/student.py --golden ../tests/fixtures/student-golden.json --h 16 --d 2`
`python models/student.py --golden ../tests/fixtures/student-golden-wasm.json --h 64 --d 8`

## §316 python 测试提速 v3.15：integration 提速 + heavy 分层 + 等待轮询化（2026-09-03）

**背景**：全量 pytest 实测 30.5s（211 passed），`test_integration`（编排化集成）单测 17.5s
占 57%；4-shard 并行（python-gate / pre-commit 通道）24.96s —— 瓶颈在 integration 所在片。
`heavy`/`slow` marker 在 pyproject 声明已久但**零使用**，`test-fast` 与全量实际无差别。

**改动**（三管齐下）：
1. **integration 提速**（17.46→13.28s，-4.2s）：
   - I6/I9 刻意慢窗口 `sleep(2.0→0.4s)`：判据本就依赖「竞速副本 dispatch 计数 + 相对时差」
     而非窗口长度，短窗足够区分两代语义；I9 判别窗口 1.5s→0.5s 同步收紧。
   - I7 刻意慢 eval `eval_delay 3.0→2.0s`（ThreadingHTTPServer 无限并发，6 局全并行，
     2s 仍安全 ≫ 采集 ~1s，保 is_alive 断言边际）。
   - **等待轮询化（用户裁定）**：I7「等 eval 进入在途」从固定 `sleep(0.3)` 改为
     `FakeAgent.eval_dispatched：threading.Event` 轮询栅栏（首局 eval dispatch 即置位，
     `wait(timeout=3)`）——触发即继续、语义更稳（不再碰运气赌 0.3s 够不够）。
2. **heavy 分层落地**：`test_integration` 挂 `@pytest.mark.heavy`；fast gate（`make
   test-fast` 与 `tools/githook/nn-gate-shards.py` 分片命令）加 `-m "not heavy"`。
   全量 `make test`（不带 -m）与 `RUN_RL_ITEST=1` standalone 仍跑 integration——这兑现了
   pyproject「heavy excluded from fast-gate」的既有契约注释，pre-commit 日常门不再吞 17s。
3. 配套注释（I6/I7/I9）同步 0.4s/2.0s 新值。

**实测**（本机，训练结束后空闲态）：
| 通道 | 前 | 后 |
|---|---|---|
| 全量 `pytest tests/` | 30.5s | 27.2s |
| `test_integration` 单独 | 17.5s | 13.3s |
| fast-gate 串行（-m not heavy） | — | 14.0s |
| 4-shard fast-gate（python-gate 通道） | 24.96s | **10.5s** |

**保留的刻意慢**：I6/I9 慢窗口（0.4s）、I7 eval_delay（2.0s）——属状态注入（模拟慢节点/
慢 eval），不可轮询，仅按时长下限收紧。测试编排器内部的 `all_settled.wait(0.5)` 等生产
代码轮询未动。

**验证**：全量 211 passed / fast-gate 210 passed + 1 skipped（integration）/ ruff+mypy 干净。

## §317 全量测试自动并行：xdist 解禁 + FakeAgent 实例隔离（2026-09-03，用户裁定）

**背景（用户问"全量测试能自动并行吗？CPU 充裕"）**：§316 把 fast-gate 压到 14s，但全量
`make test` 仍串行 27s。仓库曾禁 pytest-xdist（worker 强制系统 %TEMP% basetemp 触发沙箱删除
确认），改用自研 `nn-gate-shards.py` 文件分片。

**根因（xdist 解禁）**：`.venv` 的 `colorama` 是**无 `__init__.py` 的损坏 namespace 目录**
（site-packages/colorama 有子模块但缺 `__init__.py`，未重新导出 `AnsiToWin32`）。串行时某处提前
加载绕过了它；xdist worker 直接 `import colorama` → `AttributeError: module 'colorama' has no
attribute 'AnsiToWin32'`（pytest terminalwriter 旧 API）。`pip install colorama`（装 0.4.6 完整
包）修复。conftest 的 `tmp_path` 覆盖已消除沙箱问题，xdist 禁令前提不复存在。

**FakeAgent 实例隔离（并发安全）**：原 `FakeAgent.events`/`slow_first`/`eval_delay` 等全是
**类变量**，xdist 按函数分发时 `test_integration` 与 `test_eval_local_gate` 并发跑会踩共享状态。
新增 `FakeServer(ThreadingHTTPServer)` 子类持有这些实例状态，handler 经 `self.server` 访问——
每个 test 起独立 server，状态完全隔离。`_ping_cache` 留类变量（纯计算缓存，共享无害）。

**worker 数调优**（16 逻辑核）：n=4 最优 **17.5s**（串行 27.2s，-36%）。更多 worker 更慢
（torch import 开销每 worker ~2s + 单函数 `test_integration` 13.9s 不可再分，auto=16 → 22.8s）。

**落地**：
- `make test` → `pytest tests/ -n 4 -q`；`make test-fast` → 加 `-m "not heavy"`。
- `nn-python-gate.sh`（pre-commit）pytest 部分换 xdist `-n 4` **全量**（含 heavy/integration，
  ~17s；FakeServer 实例隔离保证并发安全；删 `SHARDS` 变量/分片脚本调用）。
- 删 `nn-gate-shards.py`（已无引用）。

**实测**：全量 17.5s / fast-gate 7.8s / 多次运行 rc=0（无跨 worker 竞态）。

**验证**：`make test` 17.6s rc=0；ruff+mypy 干净；`colorama` 0.4.6 进 `.venv`（未进
requirements.txt——属 pytest 传递依赖，由 venv 管理）。

## §318 test_integration 拆分 9 独立函数，xdist 全量并行再提速（2026-09-03，用户裁定）

**背景（用户问"integration 能不能再拆分并行跑"）**：§317 后全量 17.5s 瓶颈是 `test_integration`
单函数 13.9s 独占一个 xdist worker（单函数不可再分，墙时 ≈ max(13.9, 其他 ~4s)）。

**拆分**：I1-I9 九个编排子用例 → 各自独立 `@pytest.mark.heavy` 函数：
`test_it_queue_normal / test_it_halt_preset / test_it_stream_smoke / test_it_stream_halt /
test_it_local_suspend / test_it_longtail_race / test_it_eval_deferred / test_it_precollect_resume
/ test_it_early_race_v314`。公共 setup 提取为 `_itest_env(monkeypatch, tmp_path)`（返回
srv/WEIGHTS/cfg/args/bun，每个函数 try/finally 关 server）。原有的 shared-eval-log 清理逻辑
不再需要（每个函数独立 `tmp_path`）。

**实测**（16 核，xdist -n 4）：
| 通道 | 拆分前 | 拆分后 |
|---|---|---|
| 9 个 itest 串行 | 13.9s（单函数） | 15.1s |
| 9 个 itest xdist n4 | —（不可分） | **7.3s** |
| **全量 `pytest -n 4`** | 17.5s | **14.8s** |

并行墙时由 9 个分散的函数均摊到 4 个 worker；全量从 17.5→14.8s（相对串行 27.2s 已 -46%）。

**验证**：全量 14.8s rc=0；standalone `main()`（RUN_RL_ITEST=1）改为逐个调用 9 函数
（各传独立 tmp 子目录 + fresh MonkeyPatch）。
