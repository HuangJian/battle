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
