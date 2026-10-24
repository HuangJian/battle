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
> **引用本文件只按 `§<id>`，不按行号**：`DECISIONS.md:<行号>` 一律是**写下当时**的坐标，
> 已历三轮瘦身（两次正文下沉），行号必然位移；定位用 `grep -n '^## §<id>'`，指针走索引行末尾的
> `—— 全文（…）→ <路径> §N`。
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
> **2026-10-11 第三轮瘦身**（`§2026-10-11-decisions-slim-round3`）：2026-09-24 起正文 >12 非空行的
> **192** 条按同一口径搬走（9 097 → ≈4.7K 行 / 945K → ≈488K 字节，≈−48%），并在各主题档归档节顶部生成
> **「家族谱系」**（同一问题的先后进展按主题归并 + 末条决定原文节录，只归类不改写）——
> 主题档落点：`engineering` §20 · `remote-transport` §32 · `console` §11 · `training-stack` §25 ·
> `experiments` §33 · `runtime-opt` §13 · `tpu-perf` §9（表见 `docs/nn.progress.md` §2）。
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
| `num_or_none` 同名 5 处 | 只收编**逐字节相同**的两份（`gate_inputs._num` 接受 bool ⇒ 不收）。同名 ≠ 同物。 | nn 复用审计 |
| `quantile_nearest_rank` 同名 3 处（`_pct`/`_pct`/`_q`） | 不合并——秩/插值/空集返回与量纲各异，合并必改输出。 | nn 复用审计 |
| pydantic 块形状 ×3（`course_spec`/`bc_config`/`config`） | 不合并——字段集/谓词/诊断消息各异，共享的只是 pydantic 调用形式。 | nn 复用审计 |
| `_init_weights`×3 / `arch` 基类形状 | 不合并——三条是**不同的初始化策略**，改即改训练行为（属新实验，不得当重构顺手做）。 | nn 复用审计 |
| `kickstart_burn` ⇄ `paired_kill` 止损块 | 两条**独立规则**（看结果 vs 看配对差），只共用读块助手。 | nn 复用审计 |
| 账本 clone / 逐字节重复计数 | 是机械命中（AST 形状相似度）的**下界**，不是欠账判决；**不得为凑 DoD 数字合并语义不同的实现**。 | nn 复用审计 |

> **NN 训练侧复用审计（2026-10-11，`plan/nn-training-reuse-and-deps.plan.md`）**：上表末六行的依据 =
> `docs/nn/reuse-audit.md`（§口径差 · §同名清单）+ 该 plan §S2 / §S3 的逐条实测结论。
>
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
  + `_bc_raw` 支撑）。opt-out 保留：要与历史单卡 run 逐位 A/B 的腿把……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-multi-gpu-default`

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
  ⚠ **2026-10-13 更正**：工厂与其测试已退役（见 §2026-10-13-ladder-factory-retire）⇒ "单一实现"不再存在；
  公式本身不受影响，现只以字面值活在已生成的关卡文件里。
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
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-goalnn-retire-local-fake-node`

## §2026-09-19-goalnn-course-worker-orthogonal（2026-09-19，用户口径：启动训练不选 pull/push 模式）

- **决定**：**传输不再是课程属性，也不再是启动选项**——它是部署事实，由「有没有登记节点」推出来。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-goalnn-course-worker-orthogonal`

## §2026-09-19-goalnn-test-port-contention（2026-09-19，平台性存量红：e2e「探端口 → 起子进程 bind」的 TOCTOU）

- **背景**：R4 提交被 pre-commit 的 nn python gate 拦下，红的是一条与本轮改动毫无关系的 e2e：`test_multi_course_single_hub_e2e.py::test_offline_course_is_parked_and_resumes_on_going_online` —— `hub-server 未就绪或课程表不对（rc=1）`，真因只埋在子进程输出里：`[hub-server] ERROR: 端口 127.0.0.1:53637 已被占用——拒绝启动（禁止双监听）`。根因是测试侧的经典 TOCTOU：`_free_port()`（`bind(0)` → `close()`）与子进程真正 `bind` 之间有一个足以跑完 Python 冷启动（~1s）的窗口；gate 用 pytest xdist，另一个 worker 的探测会拿到**刚刚被释放**的同一个端口并先绑上 ⇒ 先绑者赢、后绑者被 `_port_guard` 拒启。
- **决定**：把这段竞态收进测试侧的唯一出口 `tests/subproc_util.py::spawn_bound_port()` —— 在探测到的端口上起真服务进程，**成功判据 = 这个子进程自报 `listening on <host>:<port>`**（不是「端口上有人监听」：那可能是别人的服务，会让我们对着陌生 hub 跑完整用例）；撞端口的子进程带着 `PORT_TAKEN_MARKER` 退出 ⇒ 换端口重试（默认 5 次）并 **print 一行**（重试发生了要看得见，否则偶发红又会变成谜）。
- **备选与否决**：① 保持原样、给这条 flaky 用例加重试插件——否（重试会把真 bug 一起吞掉，而这里的真因是**可消除**的）；② 靠 `SO_REUSEADDR` 让后绑者也能绑上——否（`_port_guard` 的语义正是「禁止双监听」，放开等于把 2026-09-09 双实例事故请回来）；③ 用「端口有人监听」当成功判据 + 就绪短等——否（会静默地把陌生进程当成被测服务）；④ 让每个 xdist worker 在按 `PYTEST_XDIST_WORKER` 偏移的端口段里取号——否（窗口只变小不消失，还给测试引入与 worker 拓扑耦合的端口算术；真撞上时同样无从恢复）；⑤ 改 socket activation（父进程 bind 好再把 fd 交给子进程）——否（被测对象是 CLI 的 `--port`，改传 fd 就不再测控制台真实启动的那条 argv）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-19-goalnn-test-port-contention`

## §2026-09-19-goalnn-offline-training-mode（2026-09-19，离线训练模式：启动选在线/离线 + 云端整段执行 + 补传归位）

- **背景**（用户口径 2026-09-19）：「启动课程训练时，需指定 在线/离线 模式，缺省在线。在线模式下，使用 rollout 集群，每个 it 都需向云端 worker 传语料。离线模式下，支持下载任务包（课程配置、代码），上传到云端 worker 后自动跑完课程；也支持带特别标识的云端 worker 在线领取。写一个 `battle.offline.ipynb` 用于在云端执行离线训练任务；ipynb 里设置一个选项，是否实时把训练结果回传到 hub，不实时回传则任务完成后统一打包让用户手动下载后导入；ipynb 执行时先尝试连接 hub，能连通就从 hub 获取离线任务包，不能连通则等待用户手动上传。」
- **决定**："在线/离线"是**启动时的模式选项**（缺省在线），落成**课程级** rl-config 键 + hub 该课的模式：① 模式 → 键的换算只有**一处**（`dashboard/src/stack/specs.ts::trainModeKnobs`）：离线 ⇒ `courses.<课>.{rollout_src:'run', run_iters:-1}`（声明 + 段长），在线 ⇒ 撤掉离线标记（段长必删，课程级 `run` 也删，但**不**顺手清别的覆盖）；② 离线启动同时把该课 hub 模式置 offline（整段 job 只交给**带标** worker）；③ 「能领离线课」= worker **自报能力**（`X-Battle-Offline: 1`，`--offline`），不是课程绑定——带标 worker 仍领在线课；④ 补传的**归位键** = hub 在 `/jobs/next` 里下发的课程键（领活路径）或 `--hub-course`（全离线包路径，由 notebook 的 `CFG.course` 给），空 = 单课程 hub（此时不带这个键）。
- **为什么模式不写全局 `rl.rollout_src`**：那是所有课共用的默认面——用户在弹窗里只选了**这一门课**，落进全局就等于把全部课程一起拖进离线。故离线只写课程级键；`run` 也**绝不**进全局（`preset.ts` 用 `trainModeKnobs` 算出的全局面：算出 `run` ⇒ 置空不写）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-goalnn-offline-training-mode`

## §2026-09-19-goalnn-test-port-convergence（2026-09-19，测试侧取端口全量收敛 + 守卫扩到全测试面）

- **背景**：§2026-09-19-goalnn-test-port-contention 只修了两处调用点，仓库里还躺着四份私有的 `_free_port`（`test_instance_lock.py` / `test_worker_server_lock.py` / `test_port_guard.py` / `test_push_bootstrap_teardown.py`）——两处起**真服务进程**（同样暴露在 xdist 竞争下），两处只是**进程内**探端口。
- **决定**：测试里取端口只允许**三个出口**，且全在 `tests/subproc_util.py`：① `free_port()` —— 进程内用（bind 紧随探测，窗口微秒级）；② `spawn_bound_port()` —— **单个**服务子进程（成功判据 = 它自己自报监听）；③ `retry_on_port_stolen(scenario)` —— **多个**进程抢同一端口的场景（`scenario(port)` 抛 `PortStolenError` = 场景作废，换端口重跑，其余异常原样上抛）。守卫从「2 个文件」扩到**全部测试文件**。
- **为什么需要第三个出口**：「三启同时启动 ⇒ 恰好一个成为实例」这类用例必须让 N 个进程抢**同一个**端口，用不了单进程版的 `spawn_bound_port()`；端口若被外人抢走，N 个全灭且输出带端口占用文案 —— 那不是被测行为不对，而是场景作废，应由**换端口重跑**消化，既不该报红、也不该把断言放宽。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-19-goalnn-test-port-convergence`

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

- **背景**：新列渲染 `${r.avgWallSec}s`；服务端进程未重启或旧 API 缺键时……
- **决定**：展示层 `secCell`（仅正有限数 → `Ns`，否则 `-`）；`fetchPool` 将
  `avgWallSec ?? null` 归一；服务端继续显式写字段。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-console-wallsec-undefined-guard`

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
  13 处裸 `text=True` 捕获 = §19 那类「响亮错误变哑巴」的温床）。重复处的注释写着「与 X 同口径…故就地保留同款……
- **决定**：新增 **stdlib-only 的 `common/` 包**（hashing / proc / fs / text / logutil）作共享原语层；上层只留
  re-export / 薄包装（历史名字与 monkeypatch 接缝全保留）⇒ **零行为变化**（门禁 2230 → 2247 全绿）。层契约：
  只依赖 stdlib、不反向 import 上层、无副作用、无模块级可变状态。**结构性例外**：三个从 GitHub raw 单独拉取的
  引导模块（`tailscale_boot` / `notebook_boot` / `offline_boot`）**禁用本包**——cell 侧在拿到 `code.zip` 之前……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-common-primitives-layer`

## §2026-09-23-goalnn-layering-common-sink（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 提可维护性」）

- **背景**：`rl/` 有 10 个文件 import `remote/*`，`remote/` 有 8 个文件 import `rl/*` —— 双向包循环，
  当年靠 `rl/queue.py` 一处**函数内延迟 import** 维持「能跑」（延迟 import 把失败推到调用期，启动时看不出环）。
- **决定**：把**模块级零上层依赖**的 `remote/protocol.py`（92 处 / 65 文件引用）与 `remote/game_watch.py`
  （14 处 / 12 文件）下沉为 `common/` 成员；`remote/` 保留**显式清单**门面（不用 `import *`）。确立分层
  `L0 common/·platform_utils·pid_probe·dist_common·schema → L1 models/·ppo/·data/·train/·rl/·scripts/
  → L2 remote/·根入口`，允许 `L2→L1→L0`、反向禁止，由 `tests/test_layering.py` 断言。`rl/` 保留 4 项
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-layering-common-sink`

## §2026-09-23-goalnn-godmodule-loop-transport（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 提可维护性」）

- **背景**：`rl/loop_steps.py` 2328 行里混着两种东西——`TrainingSteps` mixin（1 个 **1715 行**类）与
  **19 个模块级自由函数 + 2 异常类 + 4 常量**（课程/rollout 源解析、transport 选择、hub 推送、节点 failover、
  kickstart 系数、远端可重试异常集合），后者**没有一个是方法**，只是历史上「从 `loop_core.py` 拆出」时……
- **决定**：把 `loop_steps` 的**模块级函数簇（43–611 行）零逻辑改动**搬到新 `rl/loop_transport.py`
  （传输/发布策略的唯一实现），`loop_steps` 只留**显式清单门面** + 列全的 `__all__`（2328 → 1812 行），
  既有 `from rl.loop_steps import X` 调用点零改动。**关键口径**：DI seam 是**模块全局**，patch 目标
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-loop-transport`

## §2026-09-23-goalnn-godmodule-hub-admin（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/hub_server.py` 3974 行 —— 三个大状态类（`_JobStore` 1002 / `_HubQueue` 1033 /
  `HubHandler` 1343）占了 **85%**。原计划想先撇 5 个顶层纯函数，实测它们加起来只 **58 行 / 1.5%**……
- **决定**：把 admin 控制面 **9 方法 / 218 行**（停机恢复 · 课程热切 · 队列与状态 · push-worker 清单 ·
  net-probe）拆成 `remote/hub/admin.py::AdminRoutes`，方向 `class HubHandler(AdminRoutes,
  BaseHTTPRequestHandler)`。三条依据是量出来的：`HubHandler` 只有 3 个类属性（本组近乎无状态）·
  本组只往外调 4 个通用助手 · **本组测试接缝为零**（全仓对 `remote.hub_server` 的 patch 只有……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-hub-admin`

## §2026-09-23-goalnn-godmodule-wire（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 3450 行 / 68 个顶层函数，是 S4 最后两个神模块之一。它与前三刀
  有本质差别——前三刀拆的是**类**（刀口靠「方法互调」量得出），这里要拆的是一整片顶层函数，……
- **决定**：`WIRE_*` 阈值 7 个 + 状态 3 份（`_WIRE` / `_BEST_RATE` / `_BULK`）+ 15 个自由函数
  （`set_bulk_log` · `_bulk_pace` · `_note_rate` · `_min_rate` · `_reroll_decision` ·
  `WireSlowError` · `_wire_bucket` … `_wire_flush`）整簇搬进新 `remote/wire.py`；**状态随簇搬迁**，
  `remote/wire.py` 是这三份状态的**唯一所有者**，`remote/worker.py` 只做……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-wire`

## §2026-09-23-goalnn-godmodule-http（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：拆 `remote/worker.py` 的第五刀。原计划是拆 BC 簇（`_bc_*` ×7 +
  `normalize_ppo_device` + `resolve_bc_seed` + `_run_bc_job`），但 AST 实测发现：BC 簇虽然
- **决定**：先把**公共底座**下沉为 `remote/http.py`：`_opener` + `_get_opener` · `BODY_*` 阈值 ·
  `_read_body` · `_POLL_WARN_AT` + `_warn_non_200` · `_request` · `_sched_headers` ·
  `_get_with_retry`（281 行，含 2 处状态）。状态仍**随簇搬迁**（与第四步同规），`worker.py`
  只做显式转发。依赖方向核实为 `http ← wire ← worker`（DAG；`http` 不 import `worker`）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-http`

## §2026-09-23-goalnn-godmodule-jobfs（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 第六步的第一刀。原计划直接拆 BC 簇，但 BC 的 `_run_bc_job` 除
  BC 自身外还依赖两个**非 BC 专属**的宿主名：`d14_corpus_match`（可自 import）与……
- **决定**：把 `REPO_ROOT` · `JOB_DIR_KEEP` · `_persist_result` · `prune_job_dirs` ·
  `unpack_opt_tar` · `pack_opt_tar` · `unpack_payload_or_fail` · `_git_head` · `_ensure_commit`
  （128 行）搬进新 `remote/job_fs.py`；`worker.py` **3030 → 2915**；依赖 `job_fs ← worker`。
  `REPO_ROOT` 随组搬迁（全仓无其它读者）——它在 `remote/` 下推导出的仍是同一个 nn-training 根。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-jobfs`

## §2026-09-23-goalnn-godmodule-bcjob（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 第六步的第二刀（`plan §5.3.5` 清单里的业务簇）。BC 簇在文件里本来就**连续
  363 行**，且对外只经两个向下依赖：`remote.http._request`（第五步已下沉）与……
- **备选与否决**：先直接搬 BC（前几轮已否决：与 `worker` 成环）；把 BC 拆成「hub 交互 / 本地……
- **决定**：`_bc_fetch_resume` · `_bc_local_resume_dir` · `_bc_store_local_resume` ·
  `_bc_load_local_resume` · `_bc_post_epoch` · `_bc_device` · `normalize_ppo_device` ·
  `resolve_bc_seed` · `_run_bc_job`（**363 行**）整块搬进新 `remote/bc_job.py`；`worker.py`
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-bcjob`

## §2026-09-23-goalnn-godmodule-download（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 第七刀（第六步的第三刀）。BC 与作业 I/O 相继下沉后，下载簇成为
  最独立的一组：AST 实测它**零跨组函数依赖**（只经 `remote.http` 的 `_request` / `_get_with_retry`……
- **决定**：`_progress_logger` · `download_payload` / `download_code` / `download_ts_code` /
  `download_blob` · `_cache_blob` / `_resolve_blob` · `_ensure_ts_code`（**252 行**）搬进新
  `remote/download.py`（313 行）；`worker.py` **2579 → 2348**。依赖
  `download → {http, wire, bulk_sched}`（全向下，无环）。`BODY_*` 不复制——从 `remote.http` 取……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-download`

## §2026-09-23-goalnn-godmodule-joblifecycle（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **背景**：`remote/worker.py` 第八刀。前七刀（wire / http / job_fs / bc_job / download）把**底座**
  与**叶子簇**拆完后，作业取活 / 生命周期 / 回传面是剩下一块**连续 543 行 / 17 个顶层名**的整块……
- **决定**：整块搬进新 `remote/job_lifecycle.py`（682 行）：`peek_jobs` · `request_priority` ·
  `claim_job` · `_priority_rank` · `acquire_job` · `job_started` · `job_ready` · `abandon_job` ·
  `job_status` · `start_cancel_watcher` · `post_result` · `release_job` · `worker_tag` ·
  `_failure_detail` · `job_body_error` · `report_job_failure` · `heartbeat`。`worker.py`
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-joblifecycle`

## §2026-09-23-goalnn-remote-dag-ledger（2026-09-23，用户指令「给 remote/ 加一条全局无环守卫，把散在各子模块的『不得反向 import』断言收成一张 DAG 对账」）

- **背景**：S4 八刀把 `remote/worker.py` 拆成 `wire` / `http` / `job_fs` / `bc_job` / `download` /
  `job_lifecycle` 后，「新模块不得反向 import `remote.worker`」在**六个**拆分守卫里各写了一遍……
- **决定**：新增 `tests/helpers/remote_dag.py`（**账本 + 判据实现，只有一处**）与
  `tests/test_remote_dag.py`（整图对账，18 例）；账本两个常量：
  `LAYERS`（remote/ 全部 **35 个生产模块**的**拓扑秩**，8 层）与 `DEFERRED_CYCLES`
  （**唯一**允许的环，仅限延迟 import，必须写明理由）。六个拆分守卫改调……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-remote-dag-ledger`

## §2026-09-23-goalnn-remote-ring-split（2026-09-23，用户指令「拆掉 remote/ 里唯一那个被登记的延迟环：把 run_loop 的 verify_plan_file / run_plan_job 下沉到 L1，让 worker 直接拿」）

- **背景**：上一条账本收口时实测发现 `remote/` 内部真有**一个**环：`run_loop ⇄ worker`（两边都是
  函数内延迟 import——`run_loop._real_run_job` 为顶层 torch-light，`worker.run_job` 为「不把编排入口……
- **决定**：引擎**整块**下沉到新模块 `remote/plan_run.py`（**L2**），`run_loop.py` 只留 CLI /
  独立续跑 / 门面（1451 → **491** 行）；「一轮怎么跑」由调用方**注入**（`worker` 传 `run_job_fn=run_job`，
  CLI 侧传 `_real_run_job`）。**引擎里那个 `_real_run_job` 兜底删掉**（它正是反向 import 的成因）：
  漏注入时响亮 `RuntimeError`，而不是静默跑错执行器。依赖变成 `worker → plan_run`、`run_loop → plan_run`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-remote-ring-split`

## §2026-09-24-goalnn-godmodule-traincore（2026-09-24，用户指令「拆 worker.py 的宿主：按一条真实调用链把 run_job / worker_loop 里的簇切出去」）

- **背景**：S4 前八刀 + 账本收口后，`remote/worker.py` 余下 **1805 行全是宿主**（`run_job` 752 /
  `worker_loop` 364 / `main` 127 / `_prefetch_fill` 69）——叶子簇已全搬完，再拆就是拆宿主。
- **决定**：按**一条真实调用链**切，共三块（`worker.py` 1814 → **1281**）：
  ① **训练核**（`run_job` 650-1076，427 行）→ 新 `remote/train_core.py::run_training_core`：
     课程上下文/reward_fn → torch+种子 → 模型构建（`opt_init` 优先）→ opt 内容寻址解析 → 多卡 →
     kickstart ref → demo bank → PPO → 产物/`result`；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-godmodule-traincore`

## §2026-09-24-goalnn-godmodule-jobround（2026-09-24，用户指令「拆 worker_loop 的『每 job 一轮』成 remote/job_round.py（先定档 seam）」）

- **背景**：第九刀后 `worker.py` 余 1281 行，`worker_loop`（364 行）把轮询壳与「一轮」（177 行：
  旁路线程组 + `run_job` + 回传落定 + finally 全收）织在一起——第九刀的**明确遗留项**。
- **备选与否决**：……
- **决定**：`worker_loop` 的「每 job 一轮」（L943-1119）下沉 `remote/job_round.py::run_one_round`；
  配套下沉 `_prefetch_fill`（69 行）· `settle_result`（原 `_result_settled` 闭包）·
  `PREFETCH_WIRE_ID` / `PREFETCH_ROUND_SEC`。`worker.py` **1281 → 1042**；新模块 **418** 行。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-godmodule-jobround`

## §2026-09-24-goalnn-hub-routes-split（2026-09-24，用户指令「拆 hub_server 的其余路由组：把 _get_* 12 / _post_* 11 收成通用助手」）

- **背景**：第三步已把 admin 控制面（9 方法）拆成 `hub/admin.py`，定下「路由按域进混入」的先例；
  `hub_server.py` 3728 行里余 25 个路由方法（13 `_get_*` + 12 `_post_*`，本体 644 行）全挂在……
- **决定**：25 个路由方法按**域**分成四组混入——`hub/schedule.py`（取活/租约/打点）·
  `hub/result.py`（回传与终局）· `hub/blob.py`（字节服务）· `hub/offline.py`（离线段），与 `AdminRoutes`
  并列进 MRO；四组共用的形状收成 5 个助手（`_job_or_404` / `_job_body` / `_lease_token` /
  `_serve_path` / `_read_raw_body`），**实现只住 `hub_server`**（组合类提供、混入以 `Any` 声明并消费）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-hub-routes-split`

## §2026-09-24-goalnn-worker-landing-trio（2026-09-24，用户指令「清掉 remote/job_fs._ensure_commit 死代码并重构 worker.py 余下的 run_job / worker_loop / main」）

- **背景**：第十刀后 `worker.py` 余 1042 行，三个宿主函数 `run_job`（350）/ `worker_loop`（197）/
  `main`（127）之外全是转发门面。用户指令是「重构这三块」，而侦察给出的答案是：`run_job` 里确实……
- **决定**：`run_job` 的物料落地整段（payload 与 code.zip 的「取字节 + 摆好」）下沉
  `remote/download.py`，与原有 `_ensure_ts_code` 并列为**三兄弟**，各返回 `NamedTuple`
  （`PayloadLanded` / `CodeLanded`）；`worker.py` **1039 → 1033**（`run_job` **350 → 300**），
  `download.py` **313 → 519**。层号**不动**（`download` L3 → 新增边 `job_fs` L1，本就向下）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-worker-landing-trio`

## §2026-09-24-goalnn-hub-jobstore-mixins（2026-09-24，用户指令「拆 hub_server 的 _JobStore 状态类（先定档接缝与拆法）」）

- **背景**：`remote/hub_server.py` 3017 行里 `_JobStore` 独占 **1002 行 / 49 方法 / 19 个状态字段**，
  而**一把 `_lock` 守着全部**（49 个方法里 30 个取锁，`_*_locked` 后缀标的就是临界区内的那半）。……
- **决定**：`_JobStore` 按**域**拆成六个**混入**（`remote/hub/store_{ledger,wire,scheduling,leases,`
  `results,offline}.py`），组合类只留 `__init__` / `note_worker` 与**进程级状态**
  （`halt_workers` / `_workers`——多课程单 hub 下 `_HubQueue` 借的就是这一份）。
  `hub_server.py` **3017 → 2072**（−31%）；新六块共 1273 行；47 个方法与本体 895 行搬走。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-hub-jobstore-mixins`

## §2026-09-24-goalnn-hub-hubqueue-mixins（2026-09-24，用户指令「拆 _HubQueue 多课程调度面（先侦察那 28 个同名委托方法）」）

拆 `_HubQueue`（1033 行 / 76 方法）——**先侦察那 28 个同名委托方法**，侦察结论改变了整刀的切法。
- **先决条件（A 相）**：第七个混入要按课程构造/注解 `_JobStore`，而 `remote/hub/*` 不得 import……
- **★ 另一个结构决定：`queue_peer.QueuePeer` = 只有声明的柱子**（74 个 `def X(...) -> T: ...`，零实现）。
  七个混入必须互不 import（本刀要消灭耦合），但各自要调兄弟方法 ⇒ mypy 一片 `attr-defined`。
  「各造一份 Protocol」= 七份会漂；「import 兄弟」= 违反本刀目标；**共同声明面**两者皆免：
  运行时由后续混入的活动实现覆盖，mypy 看声明，守卫看真身。`_store_of` **刻意不进**它……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-hub-hubqueue-mixins`

## §2026-09-24-goalnn-hub-entry-split（2026-09-24，用户指令「拆 hub_server 剩下的引导链与 HTTP 面，收口 S4」）

收口 `remote/hub_server.py`：**887 → 100 行**（累计 3017 → 100，**−97%**），且里面只剩 20 行代码。
- **三件东西，两个新家**：`hub/http_face.py`（**L5**，575 行：来源判定 + `HubHandler` + 通用助手）·
  `hub/boot.py`（**L6**，310 行：`as_hub` / `make_server` / `main` + `DISCOVER_SCAN_SEC`）·……
- **层号先算后切**：`LAYERS` 是拓扑秩 ⇒ 在中间插一层会顺反向边涨上去。`audit_s16b.py` 先模拟、
  切完实测复核 ⇒ **级联只有 3 个模块**：`hub_server` 5→7 · `smoke_loopback` / `tunnel_ab_probe` 6→8。……
- **★ 薄入口的 `__all__` 就是契约**：`hub_server` 现在**零定义**（无 `def`/`class`，唯一赋值是……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-hub-entry-split`

## §2026-09-24-goalnn-loop-eval-chain-split（2026-09-24，用户指令「按同一条「真实调用链」手法拆 rl/loop_steps 的 TrainingSteps 本体（952 行 / 20 方法）」）

拆 `rl/loop_steps.py`：**940 → 666 行**；`TrainingSteps`（845 行 / 20 方法）里**唯一一条真正的
方法间调用链**（8 成员 / 275 行 / 5 个状态槽）搬到新模块 `rl/loop_eval.py::TrainingEval`……
- **刀口怎么选的（先量后定，不按行数等分）**：20 个方法里只有 7 个**互相调用**，且连成一条链
  （`_dispatch_delayed_eval → _sweep_eval_tail` · `_drain_pending_eval → _eval_covered/_eval_on_round/……
- **切法仍是混入**（同一对象、同一把锁、零行为变化，测试一行不改），但**基类元组是追加**：
  `class TrainingSteps(TrainingRemote, TrainingEval)`。追加而非插队的判据是硬的 —— 2026-09-23……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-loop-eval-chain-split`

## §2026-09-25-goalnn-loop-volume-chain-split（2026-09-25，用户指令「全程自主，继续按同一手法拆 `rl/` 侧神模块」）

拆 `rl/loop_core.py`：**1386 → 931 行**（搬走的块共 462 行 —— 445 行方法 + 9 行分节注释；
留下的 8 行是旧位置的**指路注释**）；`TrainingLoop`（25 方法 / 1089 行）里**唯一一条真正的……
- **刀口先量后定（工具化）**：新写 AST 侦察工具（`nn-training/tmp/recon_god.py`）把「类内调用图 +
  连通分量 + 每方法读的模块全局 + 写槽」一次量出。同一份量法在 `rl/batch_eval.py` / `rl/bc_loop.py` /……
- **方向 = 调用者依赖被调用者**（与 S4 第二步、第十七刀同源）：本簇的**生产入口全部**在
  `RoundSteps`（`step_course_iter` → `_iteration_pairs`；`step_rollout` → `_volume_active` /……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-volume-chain-split`

## §2026-09-25-goalnn-loop-lifecycle-chain-split（2026-09-25，用户指令「拆 loop_core 余下的生命周期链（7 成员 = 主循环骨架），先答清「拆出去后谁是宿主」」）

拆 `rl/loop_core.py`：**931 → 446 行**；`TrainingLoop` 本体里最后一条方法间调用链（7 成员 / 351 行）
连同 7 个**只能随它走**的模块级定义（150 行，闭包实测）搬到 `rl/loop_lifecycle.py::TrainingLifecycle`……
- **宿主判据（本刀题眼，可证不是偏好）**：这一簇的入边是 `_evalboard_idle`——被 `rl/loop_remote.py`（1 处）
  与 `rl/loop_round_steps.py`（2 处）以 `self.` 调用。新混入必须同时是两个 caller 的祖先才接得住这条**既有**……
- **代价与边界**：`TrainingLoop.__bases__` 三件套 → **末位追加**四件套 `(RoundSteps, TrainingSteps,
  TrainingGuards, TrainingLifecycle)`；追加末位的依据是实测「7 个成员名在既有混入里零同名定义」（MRO 不遮罩）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-lifecycle-chain-split`

## §2026-09-25-goalnn-loop-core-tail-split（2026-09-25，用户指令「拆 loop_core 剩下的基线评估链（2 成员）与叶子方法，让组合根成为纯组合类」）

拆 `rl/loop_core.py` 的**尾簇**：**446 → 240 行**，余下的 7 个叶子按**判据同源**分三簇，各自成模块（合计 355 行 = 128 + 96 + 131）。
`TrainingLoop` 至此是**纯组合类**——只剩 `__init__`（槽位）与 `_run_inspect`（唯一结构性例外）。
- **三簇（分组判据 = 谁调它 / 同源在哪，不是按大小切）**：`rl/loop_baseline.py::TrainingBaseline`
  （`_baseline_eval_weights` / `_maybe_dispatch_baseline_eval`，判据「日志里有没有这份 bc 权重的 it0 干净评估」）·……
- **宿主：三簇都挂 `RoundSteps` 一侧，组合根一行不改**——mixin 级父调用者全部是 `RoundSteps`；`_eval_on_round` 另有
  `TrainingEval` / `TrainingGuards` 两个 sibling 调用者，但三者互不继承**不构成**「必须挂组合根」的理由：`RoundSteps`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-core-tail-split`

## §2026-09-25-goalnn-loop-export-cluster-split（2026-09-25，用户指令「继续按同一手法拆 `rl/loop_steps.py` 或 `loop_remote.py`」）

拆 `rl/loop_steps.py` 的**产物出包簇**：**667 → 541 行**，4 个成员 → 新模块 `rl/loop_export.py::TrainingExport`
（**210 行**）。同一对象、同一把锁、零行为变化，测试一行不改。
- **侦察先答「有没有链可牵」**：`TrainingSteps`（667 行 / 12 方法）里**唯一一条方法间调用链** =
  `_export_offline_bundle` → `_volume_plan_block`；其余 **8 个成员全是叶子**（类内零互调）。⇒ 无连通链可顺手牵，……
- **四成员**：`_ensure_ts_code` · `_volume_plan_block` · `_export_offline_bundle` · `_export_weights`。
- **宿主：末位追加** —— `class TrainingSteps(TrainingRemote, TrainingEval, TrainingExport)`；`__mro__[1]` 仍是……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-export-cluster-split`

## §2026-09-25-goalnn-loop-remote-cluster-split（2026-09-25，用户指令「把 `loop_remote.py` 那 862 行连通分量按「判据同源」拆成多个混入」）

`rl/loop_remote.py` **997 → 35 行**（−96%）。13 个方法（862 行，一条连通分量）按**判据同源**切成四簇，
各成模块（111 / 551 / 112 / 291 行，方法体本身 69 / 470 / 76 / 247），组合根留在原文件且**零方法**。同一对象、同一把锁、零行为变化。……
- **刀口：一条连通分量没有「链」可牵 ⇒ 判据同源**。S19/S20 的取法是「找方法间调用链」，而这里 13 个节点
  本就连成**一个 862 行连通分量**（S4 第二步已按链切过一次）。于是按「同一件事 / 同一套失败语义」切：……
- **宿主 = 把 DAG 写进类声明**（调用者依赖被调用者）：`Push ← Job ← {Fail, Job} ← Drive ← TrainingRemote`。
  `Fail` 与 `Job` 并列成为 `Drive` 的第二个基类（驱动要用失败策略）。MRO 线性化 = `TrainingRemote,……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-remote-cluster-split`

## §2026-09-25-goalnn-loop-guards-cluster-split（2026-09-25，用户指令「拆 `rl/loop_guards.py` 或 `loop_steps.py` 剩下的叶子，先给侦察结论与刀口判据」）

`rl/loop_guards.py` **785 → 194 行**（−75%）。13 个方法按**判据同源**切成四簇，各成模块
（177 / 200 / 287 / 78 行），组合根留在原文件并留下**四个共享 sink + 三个判决词表常量**。……
- **刀口：先选文件，再选切法**。`loop_steps.py` 余下 8 叶**被否决**——它们类内零互调、且判据彼此
  无关（课程读盘 / 配额 / 落账 / 取证 / journal），拆它只能按大小。`loop_guards.py` 的调用图是
  **多 sink 的 DAG**（不是 S22 的连通分量、也不是 S19/S20 的单链）：`_ledger_apply` 被 **3 簇**调、
  `_sync_cloud_halt` 被 **2 簇 + 外部** `loop_lifecycle.finish_course` 调。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-guards-cluster-split`

## §2026-09-25-goalnn-batch-store-interface（2026-09-25，用户指令「给 `batch_eval.py` 设计「批存储」接口，为拆那个 1785 行的文件做准备」）

**决定：`rl/batch_eval.py`（1785 行）的拆分前置件 = 一个 `BatchStore` 接口**（新模块 `rl/batch_store.py`）：
`<EVALBOARD_DATA>` 的台账 `batches.jsonl` 与两个请求文件**只有一个所有者**，`status` / `units` / `node_dist`
的每一次变更都发生在**具名转移**里，一次转移 = 一次事务 = 一次落盘。**本节只设计，实施切在 B1–B4。**
- **诊断（实测，不是感觉）**：巨团的真因不是调用图，而是「**台账没有所有者**」——**八个**独立
  read-modify-write 点（`consume_requests` / `claim_pending` / `mark_unit_done` / `_persist_of` / `_requeue` /……
- **接口**：`enqueue` / `enqueue_verdict` / `abort` / `claim` / `set_units_of` / `mark_unit_done` / `requeue` /……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-store-interface`

## §2026-09-25-goalnn-batch-ledger-atomic-publish（2026-09-25，用户指令「先把非原子落盘那个缺陷按 §7 写了失败测试再修（不等 B2，独立一小刀）」）

**决定：`rl/batch_eval.write_batches` 从 `Path.write_text` 改为「同目录固定临时名 + `os.replace`」原子发布。**
不改 `batches.jsonl` 的字节格式，不改任何调用方，不碰 B2 的状态机改造。
- **缺陷**：`write_text` 先 `open('w')` **原地截断** live 文件再写字节 ⇒ 「截断」到「写完」之间有一个
  **读者可见**的窗口。本文件的读者里有一个**无锁的跳语言读者** —— console/TS 的
  `batches.ts::loadBatches`（「runner 单写；console 只读」，坏行**静默跳过**），而它的……
- **修法与证据**：`tmp.write_text(...)` + `os.replace(tmp, dst)`（同文件系统原子替换，POSIX `rename(2)` /……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-ledger-atomic-publish`

## §2026-09-25-goalnn-batch-store-b2-split（2026-09-25，B2：台账/请求面收进唯一所有者 `BatchStore`）

**决定：`rl/batch_eval.py` 拆它的第二步 B2 —— 台账与两个请求文件收进新模块 `rl/batch_store.py`
的 `BatchStore`**（`status` / `units` / `node_dist` 的每次变更 = 一个**具名转移** = 一次事务 =
一次落盘；`_claim_guard` / `_claim_locked` → `BatchStore._tx`；`_persist_of` 并入 `set_units_of`）。
全文（刀口 / 两条★特例 / 三套验证 / 反探针收获 / 记账）→ `docs/nn/engineering.md` §28「第二十六刀」。
- **刀口 = 按状态所有者切，不是按链切**（与前五刀不同类）：台账有 8 个独立 read-modify-write 点，
  每个自己决定「改哪些字段 / 何时落盘 / 状态怎么转」（六种落盘策略、两种在没变时也整文件重写）⇒……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-store-b2-split`

## §2026-09-25-goalnn-batch-plan-b1-split（2026-09-25，B1：批语料规划 + 判据/门 纯搬出包）

**决定：`rl/batch_eval.py` 拆它的第一步 B1 —— 23 个成员（13 函数 + 10 常量）纯搬到新模块
`rl/batch_plan.py`**（1 常量 `DEFAULT_DATA_ROOT` 留给旧家，由再导出的 `REPO_ROOT` 派生）。
全文（成员清单 / 宿主的选法 / 守卫演进 / 被否决备选 / 记账）→ `docs/nn/engineering.md` §28「第二十五刀」。
- **为什么先 B1**：§2026-09-25-goalnn-batch-store-interface 的 B1–B4 里，B2（台账事务化）是真设计
  改动、B3/B4 依赖 B2 的接口；B1 是**零锁零台账**的纯函数面（只读 `ladder.json` / `corpora.json` /……
- **零迁移依据 = 门面再导出（`X as X`，21 条）**：既有 `from rl.batch_eval import plan_units` 等调用点……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-plan-b1-split`

## §2026-09-25-goalnn-batch-runner-phase-split（2026-09-25，B5a：`_run` 按**相位**切三段，机器体逐字节不动）

**决定：`BatchEvalRunner._run`（821 行）按相位拆成 `_open_unit` / `_run_channels` / `_settle_unit` /
`_log_provenance`，`_run` 退成 6 行相位叙述；机器体（615 行）**逐字节不动**，只多一个显式取值段；
新增 `_UnitPlan`（29 字段）作为开头的**对外契约**。** 全文 → `docs/nn/engineering.md` §28；设计 → `plan/nn-training-refactor.md` §5.6。
- **侦察定量（决定刀口）**：`_run` 外层赋值 65 个名字、58 个被机器段读 ⇒ **212 处引用**，其中
  **139 在 11 个闭包内**；**零 `nonlocal`**（状态靠可变容器绕过闭包只读）⇒ 「按调用图切」只能得到
  615 行的连通分量。**病根与 B2 同型：不是没有链，是状态没有所有者。**
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-runner-phase-split`

## §2026-09-25-goalnn-batch-unrunnable-filter-loud-requeue（2026-09-25，B4 发现的既存缺陷：无可跑 unit 的批不再静默卡死）

**决定：`maybe_dispatch_batch` 在「规划成功但 `select_next_unit` 无待跑 unit」时，从**静默 `return None`**
改为**记日志 + `store.requeue`**。** 依据是**代码自身的先例**（§6.2 优先级第三条）——三条兄弟路径
（模式不适 / 规划失败 / nn 缺权重）全部是「记日志 + 退回队列」，只有这一条不一致。全文 → `docs/nn/engineering.md` §28。
- **病根（可验证）**：该批留在 `running`，而 `claim` 的可跑判据是 `pending ∨ (running ∧ of>0 ∧ len(done)<of)`，
  而 `of` 建批时已默认 **2**（`enqueue` 写 `units: {of: 2, done: []}`）⇒ **每个 idle 窗都被再认领一次，……
- **可达条件**：`only_rungs` 没有一个命中本次 plan（ladder.json 在入队与派发之间被改过 / 只剩已跑完的 rung；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-unrunnable-filter-loud-requeue`

## §2026-09-25-goalnn-batch-eval-facade-b4（2026-09-25，B4：门面收尾 —— 把「门面是门面」变成机器可判的契约）

**决定：`rl/batch_eval.py` 的第四步 B4 收尾 —— ① 新守卫 `tests/trainer/test_batch_eval_facade.py` 把门面形态钉成契约
（模块级 `def` 闭集 = {`maybe_dispatch_batch`} · 自别名再导出表闭集且对象恒等 · 刻意不转发的名字响亮
AttributeError · 门面不改写 store 交回的台账 dict）；② 删掉旧实现残留的就地改台账 `batch.setdefault("units", {})["of"]`；
③ 散落的指路注释合并成 docstring 一张表。** 全文 → `docs/nn/engineering.md` §28「第二十八刀」。**
- **1. 删除行为等价的旧残留**：那行是 B2 之前「就地改台账再落盘」的第二半（同行还有 `_persist_of(root, …)`）。
  B2 之后它**无任何读者** —— `store.claim()` 交回的是认领时的台账快照，执行器只读 `batch_id` / `iter`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-eval-facade-b4`

## §2026-09-25-goalnn-batch-runner-b3-split（2026-09-25，B3：执行面纯搬出包 `rl/batch_runner.py`）

**决定：`rl/batch_eval.py` 拆分的第三步 B3 —— `BatchEvalRunner` + `dispatch_batch_bg`（连同它独占的
五个常量与 `_heartbeat`，共 8 个成员）纯搬到新模块 `rl/batch_runner.py`；五个常量与 `_heartbeat`
不建门面转发。** 全文 → `docs/nn/engineering.md` §28「第二十七刀」。
- **本刀题眼 = 注入点改址**：执行器的依赖注入靠**模块全局**（`bun_version` / `log` /
  `run_local_eval_game`），搬家后 `monkeypatch.setattr("rl.batch_eval.X")` 不再生效（门面也 import `log`……
- **守卫演进**：`test_batch_plan_split.py` 的入边闭集原本只数旧家 ⇒ 搬走后静默退化成「2/6」……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-runner-b3-split`

## §2026-09-25-goalnn-batch-lanes-objectify（2026-09-25，B5b：`_run_channels` 的通道机器收进 `_UnitLanes` —— 状态对象化）

**决定：把 677 行机器体按**状态所有者**收进新类 `_UnitLanes`（★ 住在**同一模块**
`rl/batch_runner.py`，不另开模块）：契约字段 29 → `self.*`（`__init__` 从 `_UnitPlan` 逐名接）、
状态块 23 → `__init__`、11 个闭包 → 11 个方法、主循环 → `run()`；`BatchEvalRunner._run_channels`
退成 1 条转发。**对台账的唯一交代仍在 `_settle_unit`**（机器零 `store.`）。**……
- **★ 偏离 plan §5.6.3（必须记）：目标形态原写「新模块 `rl/batch_lanes.py`」，实际**不换模块**。**
  理由 = 机器体的依赖注入全靠**本模块全局**（实测 20 个：`log` / `run_local_eval_game` /……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-lanes-objectify`

## §2026-09-24-goalnn-train-mode-hot-switch（2026-09-24，plan/train-mode-hot-switch.plan.md 主体 L1 + L3.2）

**「在线/离线」收敛成唯一那颗开关**：`setCourseMode` ① 先落本机训练配置（唯一写面
`actions/train-mode.ts::applyTrainModeToConfig`，域换算仍走 `stack/specs.ts::trainModeKnobs`）……
**写面边界**：`pushCourseMode`（只推 hub + 落意图，开课/停课/回灌共用）绝不写配置；`setCourseMode`
（那颗开关）写；`restoreCourseModes`（回灌）绝不写 —— `pushHubMode` 本来就是循环调 `setCourseMode`……
**L3.2**：worker 在**零下载**处（结果复用块之后、payload/code/ts_code 之前）做 bun 能力自检
（`kind in (iter, run)` ∧ 非 echo）——现状是 3.42MB / 12.1s 全白传才 `REJECTED`；异常类型与……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-24-goalnn-train-mode-hot-switch`

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
根因是 `idempotency_key = (runId, it, init_weights_fp, data_fp)` **不含课程**：单 hub 多课程……
**L1**：`idempotency_key = (runId, course_fp, it, init_weights_fp, data_fp)`。选 `course_fp`（jsonc 字节
sha256）而非课程名/hub 课程键 —— 它是 manifest 必填字段，且**同进程内恒定**（课程字节装载时冻结，……
**L1'**：`course_of` 归属**唯一才认**（≥2 门课都持有 ⇒ `None`/404 + 一行歧义日志 + `/admin/queue`
的 `ambiguous_jids`），歧义**绝不**进 `_locate_cache`。原「取第一个匹配」正是事故的静默通道；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-24-job-identity-collision`

## §2026-09-24-goalnn-opt-blob-diet（2026-09-24，plan/opt-blob-diet.plan.md）

回传的 `opt` tar 从 `model.pt + opt.pt` 缩成**只 `opt.pt`**；初始权重改走内容寻址的 **`init` blob**
（`BLOB_NAMES` 加 `init`，`sha = manifest.init_weights_fp` —— **复用既有字段，manifest 字段集不变**）。……
**可判据的不变量是差值**：去掉 tar 里 `model.pt` 成员 = 本机实测 **−262,790 B（−25.4%）**
（绝对值随 `opt.pt` 可压缩性漂，§40 有口径警告）。……
**被否决**：另写 init 下载路径 · hub 重打 tar · 「同一 worker」当命中判据 · 新增 `init_sha` 字段 ·
保留 `model.pt` 让旧 worker 不炸 · 给 `_resolve_blob` 加 `cache` 开关。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-24-goalnn-opt-blob-diet`

## §2026-09-24-goalnn-offline-local-first（2026-09-24，plan/offline-rerun-local-first.plan.md）

**重跑离线 cell 时，起点、计划、代码一律以「机器上已经跑过的那份产物」为准**。决策与实现都落在
`remote/offline_boot.py`（notebook 每次会话从 GitHub raw 刷新它），**不碰 `run_loop`/`bundle` 的导入语义**。……
**为什么决策不住在 `run_loop`/`bundle`**：那两份来自**代码快照**（= 本机优先要保护的那份，可能很旧），
新参数传给旧快照要么 argparse 崩、要么静默退化 ⇒ 跨层 version skew。**不变式**：同一进程里住着
① raw 刷新的 `offline_boot.py` 与 ② 产物 `code.zip` 的 `remote.*`，跨这条边界只允许走两条腿都认的东西
（argv/`--artifacts`），不得新增只有新代码认识的参数。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-24-goalnn-offline-local-first`

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
就跳过继续下一门（末尾汇总、全跳过非零退出）**。用户报障 2026-09-25：在线课切成离线后云机 404 白等 28 分钟……
**被否决**：让 hub 自己打包（控制台是唯一打包者）·「有包也重导」（作废旧包 ⇒ 云机在窗口期探到 404）·
按包内 `commit` 与 HEAD 不符判过期（新鲜度只看 `init_weights` sha，比对另开工单）。
**违反后果**：热切离线仍不出包 ⇒ 云机白等满 `wait_pack_sec`（用户花 28 分钟才拿到一行日志）；
缺包路径没有自愈 ⇒ 排障人要自己猜是该重导还是课程名写错；把「全被跳过」与「队列本来就空」合并 ⇒……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-offline-switch-auto-bundle`

## §2026-09-25-goalnn-offline-task-discovery（2026-09-25，plan/offline-task-discovery.plan.md）

**离线云机不再在 notebook 里写死课程名**：新增只读清单 `GET /offline/tasks`（课程 + 包 + 新鲜度 +
持有者 + 段内进度；`?include=all` 才带 `not_offline`）+ 硬租约三端点……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-offline-task-discovery`

## §2026-09-25-goalnn-role-routing（2026-09-25，plan/online-offline-role-routing.plan.md）

**「该由哪个盘执行」成为 job 的不变式**：`manifest.role ∈ {offline, online}` 在**发布时**定死
（`publish_job`，`kind` 的同一快照：`run ⇒ offline`；`iter/ppo/bc ⇒ online`；`MANIFEST_KINDS`/`KIND_ROLES`……
**两道正交的闸**：归属闸（job 级，`manifest.role`）+ **停摆闸**（课程级，`mode=offline` 且请求方不是
离线盘——2026-09-20 的既有语义，**保留**：离线课的活留给切回在线，`pending_n` 不降）。……
**行为变更**：带 `X-Battle-Offline` 的盘**不再兼领在线盘的活**（一个盘一种任务，用户 2026-09-25 裁决）
——这**supersede `§2026-09-19` 的能力语义**（原 `protocol.py`「带标 worker 仍可领在线课」）：`kind=run`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-role-routing`

## §2026-09-25-goalnn-cloud-cpu-ledger（2026-09-25，云机 rollout 卡死取证）

**云机上的「rollout 卡死」= 两条 CPU 腿同时开满 + 池的回退放大**（不是导出器坏了、也不是局真的
卡住：超时局 `rc=<仍在运行>`、尾行只到 `[features] native 启用`，是**活着的慢局**）。三处落地：……
**被否决**：抬高 5s 单局硬顶（用户口径「>5s 肯定不正常」，而本案的慢局正是被超订挤慢的，
抬高只会让它更久停在超订态）· 给 eval 预留核数（2026-09-22 已否；按物理数目定池后 rollout……
**违反后果**：两条腿同时开满 ⇒ 每轮成批超时并触发回退放大（现场：整轮 4 分钟 `[run]` 零行）·
池无熔断 ⇒ 一个环境抖动就能把节点推入「回退越多、进程越多、越慢」的死循环 · 节点不夹取 ⇒……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-cloud-cpu-ledger`

## §2026-09-25-goalnn-offline-leg-retired（2026-09-25，plan/online-offline-role-routing.plan.md §7）

**「离线 = 本机发一份 `kind=run` 队列项、随后等 8h」这条腿退役** —— 离线场景**没有「一整段」这个
中间概念**（用户 2026-09-25 口径）：云机接手一门课就一直跑，直到 ① 跑完课程 ② 云机配额用尽……
**被否决**：① 取向 2「保留这条腿、只给离线盘」——`role=offline` 的消费者**当时并不存在**（全仓只有
测试设过 `CFG["offline_worker"]`），要成立就得新造并部署一个，而它的活已被取包链完整覆盖……
**违反后果**：留着旧腿 ⇒ 队列项无人能领、本机白等 8h，而日志一切正常（队列不降、没人报错）·
在配置里删字段 ⇒ 本机与云机**双跑**同一门课（两份权重、账本互相污染）· 只在测试里改断言而不动……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-offline-leg-retired`

## §2026-09-25-goalnn-online-worker-no-bun（2026-09-25，plan/online-offline-role-routing.plan.md §9）

> 前身：同日 16:39 曾立过一条 `…-cloudflared-notebook-retired`（「把 cloudflared 盘退役」）——
> **那条是误判，已于当日全量撤回**（notebook 已恢复原状）。留下这个错形状，防止再犯。
**裁决（两块盘的分工与 bun）**：在线 worker 的两块盘 —— `battle.tailscale.ipynb`（**tailnet**）与
`battle.cloudflared.ipynb`（**cloudflared 公网隧道**），服务**不同的云机网络环境**，**都保留**；……
**拦截的错误形状（两条，都得记住）**：
① **退役**：把「能力归属」（谁跑 rollout）当成「接入方式」（怎么连到 hub）来判 —— 能力重叠 ≠ 入口冗余；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-online-worker-no-bun`

## §2026-09-25-evala-baseline-bc（2026-09-25，it0 基线污染修复）

**背景**：x20-dodge-l1/L3 的 `eval_log` it0 各混入 200 局后期权重读数（L1 混 it43、L3 混 it51），
基线 low 被带偏 6pp（L1 it0 34.0 真值应为 40.0）。门控 verdict 不受影响（独立段），日常分析已更正。
**根因（三段链）**：① `rl/eval_a_once.py --baseline` 缺省取 live `out`
（`tmp/<课>/weights.json`，每轮被训练覆盖）；② 幂等早退判据是 `iter=0 ∧ 同 wver`，……
**变更**：`--baseline` 缺省改取课程 `bc`（起点冻结权重，归档文件永不变）；`resolve_eval_ckpt`
抽成单一事实来源函数；bc 缺席响亮拒（不拿 out 顶）；日志/TS 注释同步改口。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-evala-baseline-bc`

## §2026-09-25-goalnn-rollout-reap-wedge（2026-09-25，云机 rollout 二次卡死取证）

**现场**：battle.offline.ipynb × x20-dodge-l3（96 核配额）。§23 的三件套都已生效（`workers=92`、
健康轮 336 局 3.29s、熔断会响），但两次各损失 15–24 分钟：开跑 5s 内 ≥23 局踩硬顶 ⇒ 熔断 ⇒……
**根因**：`p.kill(); p.wait()` **没有上限**。超时行里的 elapsed 是 kill **之前**算的（所以照旧
写 5.0s），而 890s 花在 kill 之后的**回收**上——子进程卡在不可中断的 IO（D 状态）时 SIGKILL 要……
**放大器**：熔断（§23）把余下每一局推去**一次性冷启动**，而手上 70 个**暖** worker 空转——
那正是它要掐掉的正反馈；「不再补位」被实现成了「余下局全走一次性」。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-rollout-reap-wedge`

## §2026-09-25-goalnn-eval-round-retry（2026-09-25，eval 腿对齐机器级停滞口径）

**现场/来历**：`§2026-09-25-goalnn-rollout-reap-wedge` 把 rollout 腿的「机器级停滞」改成轮内重投，
但**eval 腿只改了一半**：`rl/eval_local.run_eval_runner_capture` 已经用进程组 + 有界回收（收不了尸……
**变更**：① `UnreapableChildError` 的**定义**上移到 `remote/protocol.py`（与 `RetryableError` 同册：
它是**两腿共用**的分类语义），`remote/iter_rollout.py` 只 import 它当模块属性（worker.py / 用例……
**被否决**：收不了尸 = 普通超时（把机器的问题记在内容头上 ⇒ 每轮都缺同一批读数）· 原地重跑同一
`game_dir`（两个写者写同一份报告 = 静默错读数）· 把机器级停滞上抛给调用方（`CloudEvalRunner` 只……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-eval-round-retry`

## §2026-09-25-state-init-course-key（2026-09-25，课程键 `state_init` 进 CourseConfig）

> _(本条的 `rebase_counters` 字段与「中段交棒」措辞已在同日被 §2026-09-25-state-init-snapshot 修订：
> 起始分布改为**快照注入**，`rebase_counters` 删除。本条只保留「课程键怎么进配置层」那部分。)_
**来历**：`curricula/x20-state-init.jsonc`（rollout 起始分布 = 人类 demo 中段起跑，plan 六腿六负
后的第七条攻击面）起草时就带着 `state_init` 块，而 `CourseConfig` 是 `extra="forbid"` ⇒ 那个文件
**长期加载失败**，连带 `tests/biz/test_reward_golden.py::test_jsonc_courses_load`（遍历 `curricula/*.jsonc`）
成为门禁唯一那条红。plan `plan/x20-state-init.plan.md` P2 = 把这个键映射进配置层。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-state-init-course-key`

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
（plan/x20-state-init.plan.md）。首版设计是「人类**磁带**（逐 tick 输入）headless 快进 T tick……
**决定（后来者极容易做错，故入册）**
① 起始分布 = `cloneWorld` 快照（`tools/sim/build-state-init-bank.ts` 产物，人类**真正到达过**的世界），
不是人类输入的产物。资产全是现成机制（`ReplayInput` + `cloneWorld`/`restoreWorld` +
`worldTickHash`），不新造格式、不新造解码器。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-state-init-snapshot`

## §2026-09-25-goalnn-prefetch-p0-not-preempt — 抢占权专属 P1：控制面只让路 + P2 补齐开工门禁 + 挤走不占重试预算（plan/bulk-p2-preempt-fix）

**背景**：2026-09-24 现场（x20-dodge-l1 / x20-dodge-l3 双课程 + 单云 worker）：**预取零命中**，每个 job 都刷
`定期预取被高优 bulk 挤走 → 重试次数用完，放弃这份提前量`，且**每条** payload 下载都带……
**决定（后来者极容易做错，故入册）**
① **抢占权专属 P1**：控制面（P0）**只让 bulk 让路、永不抢占**。取消环每 1.5s 一个包是**常态**，
一个常态事件不该有权丢掉别人的半截；P0 怕的是被大 body 拖到分钟级，而那由 `pause_if_needed`
（分片间隙暂停，预算 ≤5s/次）解决。**不要再把「P0 能抢占」加回来**去换 p0 延迟（见「被否决」）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-prefetch-p0-not-preempt`

## §2026-09-25-local-rollout-serve-pool（2026-09-25，本机腿入池：trainer 自己的两条本机腿接 `--serve` 长驻池）

**来历**：用户问「local 节点的 bun 进程是 persist 的吗？如果不是，要改！」。追证结论：**不是**——
`rl/queue_local.py` 的两条本机腿（无可用节点时的整轮 `run_rollout` / dispatcher 本机槽的……
**决定**：① 要不要建池由**真 argv**（`build_rollout_cmd` 为第一个 pair 拼出的那份）决定，
`make_local_pool` 只转发给 `serve_pool.make_pool` 的白名单——脚本不在名单里（goal / intent 两种
RL 模式）⇒ None，本轮与本改动前**逐字节相同**（不再抄一份「哪个模式用哪个导出器」的判断）；
② 池宽度 = 本机槽位数，生命周期 = 一轮采集 / 一次 `dispatcher.run()`；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-25-local-rollout-serve-pool`

## §2026-09-25-clutch-null-kill（2026-09-25，paired-kill 杀错臂 + 重启键坏，两处修）

**来历**：C-0（`x20-clutch-null`）22:08 死在 it46，账本 `gate_verdict: paired-kill 连续 2 点
< −3pp`；用户控制台重开课（22:15 marker 重写）毫无反应。两处都是代码与课程文件不一致：
**决定**：① `courses.<课>.paired_kill.self_kill`（rl-config 执行面，缺席/写坏=True，即现状；
  只有显式 `false` 才关）：命中时判据照算、streak 照落账，只是不停车（`loop_guards._paired_kill`
  在落账后、云端停机前return False）。C-0 经控制台设 `false`（rl-config 是 live 配置，不进库）。
  ② serve 重扫：`reopened_parked`（纯函数：队列已终 + 落过收官副作用 + marker mtime 新于入队……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-clutch-null-kill`

## §2026-09-25-state-init-routing（2026-09-25，起始分布派发：改路由不改放行名单）

**来历**：c4d42a04 的派发闸门（有可行远端节点就 `SystemExit`）把 51.9 万混语料变成了 6 秒……
**决定**：`state_init` 开 ⇒ 整轮直接交纯本机 `run_rollout`、向 pool 零派发，跟节点健康度无关
（`dispatch.run()` 入口，替代旧闸门；旧测试①按新契约改写）。additive-only 照旧。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-state-init-routing`

## §2026-09-26-goalnn-notorch-half（2026-09-26，python 门禁的「免 torch 半边」拆分纪律 + 静默绿变红守卫）

**背景**：nn-training 的 python 门禁把 ~3000 用例一次跑完；31 个测试文件因 `import torch`
（或运行期延迟 import）无法在**没有 torch 的机器/镜像**上跑。拆分的收益是**可移植性 / 覆盖**……
**拆分纪律（就近放 + 顶层零 torch + 原模块再导出）**：一个模块混装「纯 numpy/stdlib 判据」与……
- **测量口径**：`-o addopts=""` 重置 addopts（否则 addopts 里的 `-x` 会在首个红处把各 worker
  截断，passed 偏小）；worktree 对比须 `NN_PY=<主树 venv python>`；worktree 建在仓库内会让
top 层源码扫描类闸（`bun test`）成批**假红**，比完立即 `git worktree remove`。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-26-goalnn-notorch-half`

## §2026-09-26-goalnn-contract-scan-layer（2026-09-26，运行期契约判据拆成「源码扫描层 + ground truth + 交叉校验」）

**背景**：`tests/worker/test_backend_contract.py`（P0-1 的捕获器：三套 PPO 后端必须满足
`RolloutBackend` 的 5 个成员 + `update` 必须接受 stream.py 无条件注入的 `ckpt_path` /……
**做法（三层，判据一条不删）**：
1. **源码扫描层**（免 torch，`tests/helpers/backend_contract_scan.py`）：AST 解析 5 个成员……
**「不弱化」的证据（突变探针，实测）**：把 `ppo_update_goal` 的 `on_epoch_done` 摘掉
（P0-1 原形）⇒ 两层**同时**红，且静态层在 **`PYTHONPATH=tmp/no_torch`（无 torch）下也红**；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-26-goalnn-contract-scan-layer`

## §2026-09-26-goalnn-notorch-final-16（2026-09-26，剩下 16 个收集失败文件逐条终判：再匀出 6 条）

**做完的**（都是「被同文件的 torch 邻居连坐」，搬到主题相同且免 torch 的孪生文件）：
`test_ppo_numerics` 的 3 条 GAE（numpy 口径）→ `test_np_core`；`test_shard_split` 的……
**2918 → 2924 passed**，测试名多重集对账：3025 → 3026（1 改名 × 1 新增，其余同名搬迁）。
**剩下 16 个判定为「真需要 torch」**（逐文件真因与理由表 → `docs/nn/engineering.md` §29 第五轮）：
真张量运算（masked CE / KL / sync_scalars / coord 渲染 / RLNet·StudentNet 模型）、真 DataLoader
（跨集泄漏）、真 state_dict 序列化、真 `bc_train`、真三后端 import（运行期 ground truth）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-26-goalnn-notorch-final-16`

## §2026-09-26-paired-kill-opt-in（2026-09-26，配对杀臂默认关火）

**决定**：`courses.<课>.paired_kill.enabled` 显式 `true` 才判（缺席/写坏=False；关了的守卫
连 streak 落账都不写）。C-w 设 `enabled: true`（课程要的中点杀臂仍在）+ C-0 保持
`self_kill: false`（只记录不停车）；其余一切腿（state-init/L1d2/L2a/L2b…）缺席=不判——……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-paired-kill-opt-in`

## §2026-09-26-nodes-decouple-from-course（2026-09-26，节点统计与课程解耦 + 按天窗口）

**来历**：控制台节点页自称“机群级”，但 `aggregateNodeHistory()` 一直只聚合 `tmp/**` 里 mtime
最新的**那一个** `dist-agent-meta.jsonl`（旧动机：避免旧训练流数千条历史淹没新数据）——实际是……
**决定**：① 数据源合并**所有**训练流（课程目录 + 独立 eval run 目录），逐流取完成水位
（账本 `iteration`）**只用于过滤进行中那一轮**；② 视图按**本地日**分桶、`?days=` 切窗口
（today/yesterday/7/all/N）；③ 课程内序号 `it` 是**课程内**序号、跨课不可比，**不进任何展示
字段**；④ `aggregateNodeHistory()`（全量、与窗口无关、进 SWR 缓存）⊕ `projectWindow()`（纯投影）……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-26-nodes-decouple-from-course`

## §2026-09-26-course-archive-compress（2026-09-26，课程封存默认 gzip：重开 O3「不压缩」）

**来历**：`plan/course-archive.plan.md §8-O3` 原裁决「不压缩」，理由是「单课 ≤80 MB 收益小、
且会破坏 `eval_log.jsonl` 可直接读」。同日 E0 基线实测 13 门已停课的保留集（L0）合计
**决定**：
① **默认 gzip（level 6）逐件压缩 L0 文本件**（`*.jsonl` / `*.json` / `.console.log` / settle 行）；
实测 `eval_log.jsonl` 10.5–11.7×、`judge/backtest` 10.0×、`dist-agent-meta` 11.6×、
`training_log` 8.9× ⇒ 334 MB → **≈50 MB**。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-26-course-archive-compress`

## §2026-09-26-course-archive-guard-and-startpoint（2026-09-26：封存硬闸补第二道新鲜度 + 起点 sha 可验）

**来历**：2026-09-26 外部评审用真模块探针实测两处实现稿缺陷
（`plan/course-archive.plan.md`）：① `course_guard` 首句 `if not marker.exists(): return ""`……
**决定**：
① **新鲜度升为与 marker 无关的第二道闸**：目录新鲜（不论有无 marker）默认拒绝封存；
`--force` 只越「陈旧 marker」与「无 marker 的新鲜目录」，**不越「新鲜 marker」**。
来历：停课动作 = 删 marker 但**不杀循环**（`stopCourse` 注释自己承认云机上那份只能由操作员……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-26-course-archive-guard-and-startpoint`

## §2026-09-26-x20-steady-cont（2026-09-26，B续跑立项：八连阴后唯一阳性斜率 + H1/H2判决实验）

**来历**：noexplore it105 之后八条腿七死一停（floor/firstkill/terminal/demo-mix 判负；
dodge L1 两档判死、L3 两档判负、L2a 无信号、L2b B2 未开；state-init 154 轮日常反 хуже）。……
**决定**：开 `x20-steady-cont`（`nn-training/curricula/x20-steady-cont.jsonc`）——与 B
逐字同（奖励/位移/采集全同，validate 零错零警），起点 B-it175 纯 warm start
（`kickstart_ref=false`，C 教训 kk≈0），200 轮/14h，门沿用 B 原门（414000 ≤35% 且
p10≥2，不移球门）。过门 = H1（量不够，继续加）；200 轮横盘 = H2（到顶，停烧转蒸馏评审）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-26-x20-steady-cont`

## §2026-09-26-hub-mode-restore-enabled-only（2026-09-26，用户报障：起 hub/trainer 等很久——回灌把停掉的历史课逐课重试）

**症状**：控制台首页点「启动服务进程」，停在 `hubServer` 那一步几十秒；回执刷出一串
`失败 x20-clutch: 需要合法 course（[]）与 mode(...)`、`x20-demo-mix`、`x20-dodge-l1` …（真机 11 门）。
**决定**：`restoreCourseModes` **只回灌已开课的课程**（判据 = 开课标记
`stack/courses.ts::courseEnabled`，与 hub 同源）。未开课的课进 `skipped`（**不是失败**）：
意图一个字不丢（仍在 `courseModes` 里），摘要如实写「跳过 N 门未开课（意图保留，开课即下发）」，
真正开课时 `openCourse` 按弹窗模式重新下发。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-26-hub-mode-restore-enabled-only`

## §2026-09-26-rl-config-scope（2026-09-26，`rl-config.json` 职责边界：只留「别处无处安放」，删配置不删代码）

**触发（用户 2026-09-25/26）**：「已经在其它地方配置过的数据就不该再往里填；`intent_rl` 已废弃，
后期重启也要配成现在 x 系列一样」→「intent/goal、`stream`/`double_buffer`/`precollect_early`
**只删配置、不删代码**」。
**决定**：`nn-training/rl-config.json` 只保留五类里的 A（机器/环境级）、C（调度策略）、D（每课
机器侧旋钮活课）；B（课程文件已有同名键的全局缺省）只留必要兜底；E（废弃/死键）删。
**新键该写哪**：机器事实 → rl-config；「怎么训」的超参/奖励/关卡/门 → `curricula/<课>.jsonc`。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-rl-config-scope`

## §2026-09-26-rl-config-b-class（2026-09-26，B 类兜底键删除：范围取「全部课程」超集 + `workers` 归机器级）

**触发（用户 2026-09-26）**：「在训练机跑 `rl_config_clean.py --matrix`，按全绿结果删掉多余 B 类兜底键」。
**前置事实**：训练机（= 本机，hub / sampler / trainer 都活在这个工作区）上操作员已于 17:49 把最后……
**决定 1 — 判据范围改用「全部课程」超集**：`--matrix` / `--drop-b-class` 增 `--scope {live,all}`；
`all` = `curricula/*.jsonc` 全部课程（本机 108 门）。理由：B 类兜底的**风险面**是「任何课程没声明该键
时落到 rl-config 的值」，所以「全部课程都不靠它」是**更强**的证据（全绿于 `all` ⇒ 全绿于 `live`，
反之不然）；在训集为空时它是唯一能出结论的范围。**本次按 `all` 删 `rl.difficulty` / `rl.max_ticks`**……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-rl-config-b-class`

## §2026-09-26-x20-series-close（2026-09-26，x20 全系列关账：八腿结论 + L1/收入侧判死 + 转向新纪元）

**来历**：noexplore it105 之后全部 x20 腿关账（`docs/nn/experiments.md` §37–§44）：
L1 两档（0.01 跑满 300 轮 / 0.02 31 轮 + 三候选门控）机制双零进展、门控七候选全灭 ⇒ **L1 方向判死**；……
**决定**：
① L1/收入侧两方向判死：不开 wOpen=0.03，不升 (5.0,6.0)；② L2b 的 B2 升档（wThreat=60）
不再执行——60 轮零位移下加倍期望≈0，且新纪元已取代塑形路线，按 plan §6 后半句转 DAgger 排队；
③ state-init 的 B1/B4 指望落空（154 轮阴性）；④ 全系列 verdict 移交新纪元……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-26-x20-series-close`

## §2026-09-26-goalnn-stop-k-is-training-side（2026-09-26，K=10 决策门周期属训练侧：可动）

**决定**：**K 是训练侧可动参数**。改 K 只改「策略每隔多少 tick 重新观察一次」，不改任务的胜负
条件与单位 ⇒ 收紧 K（如 10→5）属于训练侧调参，不触发 §0.2 禁令，也不需要新关卡/新任务口径。
首腿保持 K=10（Phase -1 实测：人类非冰面停段中位 9 tick ≥ K/2，相容），收紧留作 x2 rung 期权。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-goalnn-stop-k-is-training-side`

## §2026-09-26-goalnn-stop-mdp-no-change（2026-09-26，B案不构成 MDP 变更声明）

**决定**：**B案不是 MDP 变更**。状态空间、转移、奖励、K=10 的均匀决策边界全部未动；变的只是
「同一个动作索引代表哪个物理命令」，而该映射在仿真里本来就存在（`moveDir=null ⇒ moving=false`）。
⇒ 决策门 5 处（`t%K`）、divergence-probe、state-init bank 的 `t%K==0` 切点**全部无需改**……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-goalnn-stop-mdp-no-change`

## §2026-09-26-goalnn-stop-rng-principle（2026-09-26，RNG/特权观测不进 shard：原则存档）

**决定**：**特权观测一律不进 shard / 不进 obs**。判据①进 shard = 格式变更（`OBS_SCHEMA_MAJOR`
bump + 全量重转），且会让已训 value 头的输出量纲漂移（旧 checkpoint 不可比）；②敌开火倒计时
等本就是**超人信息**——人类看不到，进了就破坏「以人类熟练玩家为难度唯一口径」（§0.2）。RNG 只走……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-goalnn-stop-rng-principle`

## §2026-09-27-goalnn-eval-rows-split（2026-09-27，`rl/eval_local` 的 eval 行/账本簇下沉 `rl/eval_rows`）

**来历**：用户指令「重构 nn-training，降低模块耦合 / 复用代码 / 提可维护性与可扩展性」。前序 S1–S4 已收口
`common/` 原语层、`rl↔remote` 包循环与 `plan_run`/`loop_*`/`batch_*`（`plan/nn-training-refactor.md` §5 DoD 达成）。……
**决定**：把 `rl/eval_local.py` 的「逐局 eval 行 schema + `eval_log` 账本 I/O」整块（**连续行 287–602，
逐字节不动**；17 个名：三字段抽取器 + `eval_row`/`eval_row_key`/`eval_row_keys` + 账本读/并/去重）搬到新模块
**`rl/eval_rows.py`**（345 行，stdlib-only），`rl/eval_local.py` **1130 → 870 行**，原模块留 `X as X` 门面
（全仓调用点与测试**一行不改**——「名字是契约，位置不是」）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-eval-rows-split`

## §2026-09-27-goalnn-bc-ingest-split（2026-09-27，S5 第四刀：`rl/bc_loop` 的 BC job 回传消费下沉 `rl/bc_ingest`）

**来历**：用户指令「按同一手法拆 `rl/bc_loop.py` 的独立连通分量」。用 `tmp/recon_god.py` 量出 6 条
模块级链（回传消费 / 归档·磁盘 / 采集 / 发布 / 运行时解析 / CLI）——取「回传消费」。全文 → `engineering.md` §31。
**决定**：把「等一个远端 BC job 回传、并把回传入账」整块（两段跨度共 11 名，**逐字节不动**：
`IDLE_WARN_SEC`/`POLL_SEC`/`POLL_MAX_SEC` + `READY`/`PENDING`/`TRANSIENT` + `BcWait` + `ledger_bc_epoch` +
`run_epoch_eval` + `ingest_bc_metrics` + `wait_bc_round`）搬到 **`rl/bc_ingest.py`**（291 行），
`rl/bc_loop.py` **1433 → 1213 行**，原模块留 `X as X` 门面。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-bc-ingest-split`

## §2026-09-27-goalnn-hub-http-split（2026-09-27，S5 第五刀：`remote/hub_client` 的 HTTP 往返分量下沉 `remote/hub_http`，把 HTTP 面做成可注入薄壳）

**决定**：把 `remote/hub_client.py` 里「把一次请求发到 hub、把回答读回来」那一簇整块搬到新模块
**`remote/hub_http.py`**（**两段跨度共 17 名，逐字节不动**，478 行；原 1688 → **1299 行**）。搬走：
传输薄壳 `_request`（全模块**唯一**碰 `urlopen` 处）· 回传消费 `probe_job_result`/`poll_job`/`wait_job`/……
**为什么**：这是传输面（发/收），与「打包 / 磁盘 IPC 发布 / 校验落位」是两件事；且它被**两条链路**
共用（pull 侧 `wait_job` 与 push 侧 `push_client` 的 `probe_job_result`——状态码分类的唯一实现），……
**用户指令**：「拆 remote/hub_client.py 的 HTTP 往返分量，先把 HTTP 面做成可注入薄壳」。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-hub-http-split`

## §2026-09-27-goalnn-protocol-split（2026-09-27，S5 第六刀：`common/protocol` 的失败类型族与线格式下沉 `common.errors` + `common/wire_codec`）

**决定**：把 `common/protocol.py`（**1708 行**）里两簇**格式/契约**面整块搬出（**三段跨度，逐字节不动**）：
* **失败类型族** → **`common/errors.py`**（118 行）：`ProtocolError` · `RetryableError` ·
  `UnreapableChildError` · `JobCancelledError` · `JobFailedError` · `CodeChangedError`；
* **传输编码 / 线格式** → **`common/wire_codec.py`**（231 行）：v1 gzip+base64（`_pack_wire` /
  `_unpack_wire` / `encode_*` / `decode_*` + 魔数常量）与 v2 裸二进制（`BRV2` / `BRJ2` 四函数）。……
**为什么**：`errors` 是**唯一的零依赖叶子**（每条继承线 = 一个处置分支，人人要 import）；线格式是「字节↔对象」……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-protocol-split`

## §2026-09-27-goalnn-job-identity-split（2026-09-27，S5 第七刀：`common/protocol` 的 job 身份簇下沉 `common/job_identity`）

**决定**：把 `common/protocol.py`（**1482 行**）的 **job 身份簇**整块搬到新模块 **`common/job_identity.py`**
（134 行，**一段跨度共 4 名，逐字节不动**）：`idempotency_key`（D1 幂等键，含 2026-09-24 加的 `course_fp`）·
`job_id`（`sha256(键)[:16]`）· `collision_rows`（发布端撞名守卫的**唯一判据**）+ `SIBLING_MANIFEST_GLOB`
（扫描面的单一定义）。`common/protocol.py` **1482 → 1390 行**，留 `X as X` 门面 ⇒ 历史 import 一行不改。
**为什么**：身份是「两个 store 会不会抢同一份活」的唯一答案，与「manifest 字段合不合法」
「字节怎么编解码」是三个不同的问题；`collision_rows` 是**发布那一刻**的哨兵，谁改发布链都要读它，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-job-identity-split`

## §2026-09-27-goalnn-payload-split（2026-09-27，S5 第八刀：`common/protocol` 的语料归档面下沉 `common/payload`）

**决定**：把 `common/protocol.py`（**1390 行**）的**语料归档（payload）面**整块搬到新模块 **`common/payload.py`**
（213 行，**三段跨度共 10 名，逐字节不动**）：`PAYLOAD_NAME` / `PAYLOAD_LEGACY_NAMES` · `INIT_WEIGHTS_NAME` /
`PAYLOAD_PERTURB_NAME` / `PAYLOAD_XZ_PRESET` · `find_payload` / `_add_bytes` / `_extract_archive` /
`pack_payload` / `unpack_payload`。`common/protocol.py` **1390 → 1238 行**，留 `X as X` 门面 ⇒ 历史 import 一行不改。
**为什么**：这是**字节容器**面（一份语料怎么变成一个可传输的字节串）；与「manifest 字段合不合法」
「这份活是谁」「HTTP 体上限多少」是四件事。两处**事故复盘**的判据都在这里：① 判别**先 tar 后 zip**……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-payload-split`

## §2026-09-27-goalnn-manifest-split（2026-09-27，S5 第九刀：`common/protocol` 的 manifest/rollout 校验面下沉 `common/manifest`）

**决定**：把 `common/protocol.py`（**1238 行**）的 **job manifest 契约面**整块搬到新模块 **`common/manifest.py`**
（547 行，**四段跨度共 38 名，逐字节不动**）：① `PROTO`；② 角色**词汇** `ROLE_OFFLINE`/`ROLE_ONLINE`/`ROLES`/
`ROLE_FIELD`；③ manifest schema（`MANIFEST_REQUIRED`/`_OPTIONAL_DEFAULTS`/`_BC_EXEMPT`/`_BC_EXTRA`/
`_ITER_EXTRA`/`_RUN_EXTRA`/`_KINDS`）+ `KIND_ROLES` + `role_of` + TS·plan 产物契约（`ROLLOUT_SCRIPT`/……
**为什么**：这是「这份活**要什么**」的声明面与校验面。读者要加一个 manifest 字段 / 一种 kind，只需看一个文件；
与「字节怎么编解码」（⑥）·「这份活是谁」（⑦）·「语料怎么打包」（⑧）·「**结果**合不合法」都分了家。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-manifest-split`

## §2026-09-27-goalnn-config-faces-split（2026-09-27，S5 第十刀：`rl/config.py` 的文件面/类面/解析面下沉 `rl/config_file` + `rl/course_spec` + `rl/course_resolve`）

**决定**：把 `rl/config.py`（**1566 行**）按「独立所有者 + 独立触发条件」切成三面，三个新模块（**逐字节不动**）：
① **文件面** `RL_CONFIG_ENV` · `rl_config_path` · `read_rl_config_file` → **`rl/config_file.py`**（55 行，stdlib + `dist_common`）；
② **类面** `CourseConfig`/`GatesSpec`/`StageSpec`/`RewardBlock`/`PpoScheduleEntry`/…（25 名，17 段）→ **`rl/course_spec.py`**（953 行）；
③ **解析面** `CURRICULA_DIR`/`LEVELS_DIR`/`resolve_level`/`load_course`/`resolve_course`/`course_from_args`/……
**为什么**：三面的触发条件互不相干——改「本机那份永不入库的配置在哪/怎么读」只看 `config_file`；加一个课程字段/门 kind
只看 `course_spec`；换课程目录布局/加载规则只看 `course_resolve`。此前它们挤在一个文件里，`config.py` 的读者（启动校验、……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-config-faces-split`

## §2026-09-27-goalnn-dist-clusters-split（2026-09-27，S5 第十一刀：`dist_common.py` 的 shard 面与权重下发账本下沉 `dist_shard` + `dist_weights_ledger`）

**决定**：把 `dist_common.py`（**1503 行**）里两个小簇按「独立所有者 + 独立触发条件」搬出（**逐字节不动**）：
① **shard 面**（7 段 7 名）——三份 shard 清单 + `BC_COLLECTOR` + `_shard_files_for` / `validate_result` / `write_shard`
→ **`dist_shard.py`**（137 行，stdlib-only）；② **权重下发账本**（2 段 6 名）——`_WEIGHTS_PUSHED` +
`weights_push_cache_reset` / `note_weights_pushed` / `forget_weights_node` / `weights_already_pushed` /……
**为什么**：两个簇的触发条件互不相干——「远端结果包到了 → 先验后落盘」只看 `dist_shard`；「同 it 补波要不要重发权重」
只看账本。此前它们与 HTTP / codeHash / 远控重启护栏 / 中断语义挤在同一个 1503 行文件里。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-dist-clusters-split`

## §2026-09-27-goalnn-eval-yield-split（2026-09-27，S5 第十二刀：评估「让位/份额（尾巴）策略」两处一起切 → `rl/eval_yield.py`）

**决定**：按「独立所有者 + 独立触发条件」把评估的让位/份额（尾巴）策略做成**独立所有者**——从**两处一起**切：
① `rl/eval_local.py` 的 L110–L226（**117 行**：`EVAL_LOCAL_SLOTS_DEFAULT` / `EVAL_LOCAL_RELEASE_GRACE` /
`EVAL_INFLIGHT_GRACE_SEC` / `EVAL_JOIN_SOFT_SEC_DEFAULT` / `EVAL_LOCAL_EARLY_EPOCHS_DEFAULT` + 7 个判决函数
`eval_join_soft_sec` / `eval_tail_overran` / `eval_local_early_epochs` / `local_gate_release_plan` / `early_epoch_reached` /……
**为什么**：这些判决的触发者是**边界事件**——派发那一刻的份额分档（immediate / last_epoch / on_join）· PPO 收官的
join 与交棒 · 下一轮 rollout 收官时的尾巴收拢 · 窗口到期后的在飞落账宽限——与「怎么在本机跑一局评估」……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-eval-yield-split`

## §2026-09-27-goalnn-plan-handoff-split（2026-09-27，S5 第十三刀：`plan_run` 18 节点交接面下沉 → `remote/plan_handoff.py`）

**决定**：按 `plan/nn-training-refactor.md` §5.7.4 落刀——`remote/plan_run.py`（**1090 行**）的**交接面**
18 名（面日志 + 校验 2 · `RunContext` 1 · 取包播种 9 · 评估装配 3 · 常量 3）整块下沉为
**`remote/plan_handoff.py`（630 行，L2）**；`plan_run` **1090 → 577 行**（−513），只余驱动引擎 + 18 名 `X as X` 门面。
**4 跨度逐字节纯搬**（562 行 / 26386 B）：A1 `TS_TREE_DIR`（含注释 2 行）· A2 `TS_CODE_ZIP_NAME`（3 行）·
R2 `[83,543]` 461 行（日志/校验/`RunContext`/取包播种 9 件/评估装配 3 件/`EVAL_ALTERNATE_WAIT_SEC`）· R3 `[708,803]` 96 行；新头 = 既知插入。
**⚠ 关键判断**：① **18 而非最小闭包 17**：纳入 `verify_plan_file`（它只引 `_log_default`；加入后块→外仍为空，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-plan-handoff-split`

## §2026-09-27-goalnn-offline-deliverable-split（2026-09-27，S5 第十四刀：offline_boot 交付面 + standalone 运输面 → `remote/offline_deliverable.py`）

**决定**：把 `remote/offline_boot.py`（1801）的**交付面**（13 节点 / 179 行干净闭集：交付三函
`package_deliverable`/`_partial_last_it`/`package_partial` + 课程路径链 5 名 + 4 常量 + `_COURSE_NAME_RE`）
整块搬进 **standalone 兄弟文件** `remote/offline_deliverable.py`（227 行，stdlib-only ⇒ L0）；
原家 **1801 → 1647**（−154）。**2 跨度逐字节纯搬**（198 行）：`L94–100`（4 常量 + 3 条 `#:` 注释）·……
**⚠ 本刀是 S5 首例「运输面 > 纯搬」**（前十三刀都是纯搬 + `X as X` 门面、调用点零迁移；本刀的**装载方式**必须改）：
`offline_boot` 在 `remote_dag.STANDALONE_BOOT_MODULES` 里 ⇒ `TOP["remote.offline_boot"]` 必须为空——顶层……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-offline-deliverable-split`

## §2026-09-27-goalnn-gate-faces-split（2026-09-27，S5 第十五刀：`gate_check` 求值器接口 —— 输入面 + 判决面成家）

**决策**：1412 行 `rl/gate_check.py`（`recon_god` 口径「1 组 31 节点巨团」；引用图实测 **47 节点 = 46 单团 + 1 孤立**
（`sum_train_samples`））一刀切两面、成两个新家（纯搬 + `X as X` 门面，S5 收官刀）：
- **输入读数面 15 名**（`EvalRow`/`BudgetInfo` 模型 + `_row_from_summary`/`_num`/`normalize_rows` 行归一化 +
  `read_trend_rows` + `first_*`/`count_*`/`sum_*` 事件扫描 + `load_override` + `_TS_FMT`/`_OVERRIDE_VERDICTS`/……
- **判决项面 22 名**（`_Partial`/`_Ctx` + `_mean`/`_slope`/`_sustain`/`_window`/`_halves` + 11 个 `_eval_*` +
  `_route_by_completion` + 注册表 `_JUDGES_NO_TEACHER` + **判决项接口** `_eval_one`）→ **`rl/gate_judges.py`**（572 行）；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-gate-faces-split`

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
决策门从「均匀 K」扩成「均匀 K ∪ threat-ONSET 沿（+最小间隔闸）」，落地前有三件**不能就近表达**……
**决定**（三项，一次定死）：
① **γ 保持 per-step**（`nn-training/rl/config.py:974` γ=0.995），**不**切半 MDP `γ^Δt`。换算声明：
   视界 = 200 决策步 × avg-Δt；事件门实测（x20-noexplore it99 × ladder-c20 四局）
   avg-Δt = **9.79–9.94** tick ⇒ 视界 **1958–1988** tick（相对均匀 2000 漂移 **−0.6%…−2.1%**）；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-27-goalnn-r2-event-gate`

## §2026-09-28-goalnn-console-metrics-harvest（2026-09-28，逐轮实际值抗轮转抄录：表列不再依赖「谁在屏幕上」）

**来历**：用户报障「两课并行，`h5a-earlydmg` 的击杀/残血/承伤全都有，`h5b-clean` 丢了一大半」。
查下来那五列（击杀/残血/承伤·杀/道具/耗时）**不是账本字段**，是 `/api/state` 扫 `it<N>` 现算后写……
**决定**：
① 控制台侧修：`snapshot-refresher` 新增抗轮转抄录（**独立 15s 节拍**），对**所有已开课**课
   （`training-enabled.txt`，与训练侧同一道闸）跑一次 `iters.readIterMetrics` 让缓存落盘；
② 两道闸：只抄在训课；账本 stat 签名（`size:mtime`）未变则跳过——**仅当上一轮把该课所有轮次都抄到**……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-28-goalnn-console-metrics-harvest`

## §2026-09-28-goalnn-bc-retrial（2026-09-28，h5 四腿全阴后的 BC 翻案重测）

- **决定**：同语料原样重训 BC-ref2（镜像§32：kaggle cuda/60 epochs/val-split/从随机起，唯一变量＝语义修正）；
  held-out 重定义 255 对（c20-lives1，排除 61 demo 对，名单先 pin，原名单随 tmp 丢失不考古）；
  门沿用§32字面（对照同场重跑：low%≤70% 且 mean≥对照+2.0）；陷阱 P2-4/kk 小开/val 安慰剂预注册；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-bc-retrial`

## §2026-09-28-goalnn-b2-c20-qualify-2（2026-09-28，B2@c20 须先验密度，不自动继承）

- **背景**：h5e 处理量 ~0.5% 判事件无边际；R2 基线 c20 仅 1~4 次/局；c05/c20 开局同源且信用 c20 更难。
- **备选与否决**：B2@c20 直开 —— 否，预期读数与 h5e 同带，是换地方重复；
  先开 B1 信用窗 —— 否（暂缓：plan §4 在 idle 不动后第一分支是 B2，跳分支等于改排序，需另立决策，不在本条）。
- **决定**：c20 开事件腿须先验 onset 处理量 >5%（shard manifest 同口径）；否则 B2 归档，信用窗/DAgger 排队。
- **违反后果**：直开 ⇒ 再烧 80~100 轮横盘腿，读数与 h5e 同带。
- 全文 → `docs/nn/experiments.md` §46。

## §2026-09-28-goalnn-god-no-wait-3（2026-09-28，God 教不了等＋God-BC 等数据污染）

- **背景**：翻案死于吸引子后排 DAgger 专家；用户判定 God 的停全是目标冲突振荡（追敌 vs……
- **决定**：等的老师只认人类；God 数据禁用于一切教等用途（教打另议）；人类 DAgger 先估
  工具＋sessions 成本再立项；§32⑤ 的 God-bulk fallback 加同注。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-god-no-wait-3`

## §2026-09-28-goalnn-fusion-last-4（2026-09-28，融合只能是终端步：PPO 洗等）

- **决定**：顺序倒置——先爬梯（PPO 只打）→ c20 终端融合（CE-dose 彩票先、
  DAgger-at-c20 重器后，门即 c20 真门）→ 直接部署，其间不再 PPO；
  DAgger 不同步在低 rung 做。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-fusion-last-4`

## §2026-09-28-goalnn-retention-ppo-5（2026-09-28，35关继续PPO：留存三件套）

- **背景**：c20 终端融合后 35关仍须 PPO（§49 保质期到部署为止）；“已有等会被洗”……
- **决定**：留存三件套（行为地板熔断＋小 kk 链＋信用回流），全现成零件，开训前 pin
  进课程文件；Router 分簇在后（plan §8 B4）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-retention-ppo-5`

## §2026-09-28-goalnn-c20-target-6（2026-09-28，c20 靶子错了：等≠桥，1命≠产品）

- **背景**：“停学不会⇒c20 死路”；但停≠桥（不钻堆可用走位满足，从未直接练过开局别送），……
- **决定**：靶子重定为开局生存（动，不用等）＋残局转化；下一步按便宜排序
  （确认产品命数→人类真 low%→开局专项辅助目标），等只是候选药之一。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-c20-target-6`

## §2026-09-28-goalnn-god-out-7（2026-09-28，God 全退训练管线：同 seed 11.5%）

- **背景**：God 1 命打人类全过的 61 个 seed，`pass 11.5%（7/61）/kills 3.79` vs 人类
  100%/20（同 harness，`tmp/_god-demo61.json`）；单对单 dodge 行，一被包夹完蛋。……
- **决定**：往后一切教师位置只认人类（Opening-BC 切片、人类 DAgger、C 重开的对局理解）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-god-out-7`

## §2026-09-28-goalnn-persist-prewarm-breaker（2026-09-28，a95 贡献量 1/10 归因：池启动预热 + 熔断收窄为「只停补位」）

**现场**：a95（Android/Termux/proot，SM7250，rl-config concurrency 6）rollout 贡献 ≤ mac 的 1/10。
真机取证（adb 上机）：轮参与率 **567/5119**（`ping failed — excluded` 2029 次 + 半路回线 174 次）、……
**根因**：**每批首批必付 N 次冷启**。6 路**真并发**（独立 curl 进程）经 agent 实测：**冷池 31.6s/局**
（6 个 spawn 互踩、拉开 25s）vs **热池 6 局共 9–11s ≈ 1.6s/局**。冷启成本 = bun + 整图 TS transpile……
**三处落地**（`tools/agent/persist-pool.ts` 纯策略 + sampler-agent 接线）：
① **启动预热**：按盘上最近用过的权重桶 kind 预 spawn `workers` 个池位，主腿吃满 `workers-(其它腿数)`、……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-28-goalnn-persist-prewarm-breaker`

## §2026-09-28-goalnn-persist-homogeneous-serve（2026-09-28，长驻池改**同质进程**：单入口 + 每任务 mode token，删掉整套「腿」策略）

**问题**（用户提出）：池按导出器分「腿」（rollout 一类、eval 一类……），于是预热得**猜腿**
（按盘上权重 mtime）、猜错腿的那一波每局都退回一次性 spawn（a95 实测 **3.1s/局** vs 对腿 **1.39–1.55s/局**），……
**决策**：池改成**同质进程**——`PERSIST_SERVE_ENTRY = 'tools/sim/serve-any.ts'`，一个 worker 按每行第一个
元素（`rollout` / `eval` / `goal` / `intent`，即 mode token）在进程内分派到对应导出器的 `main`
（`serve-loop.ts` 一行未改；未知/缺失 mode **抛**，不静默跑默认网格——分派错了不再有进程边界挡着）。
agent 侧「用哪个导出器」从 **spawn 时的 argv** 变成 **每行的 token**（`persistModeFor(entryTs)`，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-28-goalnn-persist-homogeneous-serve`

## §2026-09-28-goalnn-local-serve-pool-homogeneous（2026-09-28，Python 侧长驻池也同质：单入口 + 每任务 mode token，门槛与池的形状解耦）

**问题**（用户点名）：TS 侧 agent 池已改成同质入口（`tools/sim/serve-any.ts` + 每行 mode token，
§2026-09-28-goalnn-persist-homogeneous-serve），而 Python 侧那份池（`remote/serve_pool.py`，……
**决策**：
① **入口固定** —— 池一律起 `tools/sim/serve-any.ts --serve`（`SERVE_ANY_SCRIPT`，与 TS 侧
`PERSIST_SERVE_ENTRY` 同值）；送进 stdin 的行 = `[mode token, ...该导出器的 argv]`，token 由
`common/manifest.serve_mode_for(argv[0])` 推出（`SERVE_MODE_BY_SCRIPT` = TS……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-28-goalnn-local-serve-pool-homogeneous`

## §2026-09-28-goalnn-gate-baseline-8（2026-09-28，门必须配平凡基线）

- **决定**：一切行为门（plan 课程/探针的通过线）在 pin 数字的同时，必须 pin 一个
  同口径平凡基线（重复上一步/多数类常数/单特征，共位计算），门过线且**显著超基线**
  才算过；泄漏消融（去后果变量重跑）是 P0 第一道门。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-28-goalnn-gate-baseline-8`

## §2026-09-29-goalnn-metrics-v9（2026-09-29，观测列先于定价；改谓词必须断代）

- **背景**：三件事都是「先有数才有门」——通道 1（背面奖）缺方位参数；生产 `threatTicks` 漏判约六成
  （12px vs 19px，`docs/nn/experiments.md` §60）；穿越税立法缺观测列。合并为一次 metrics 批量改动。
- **决定**：`METRICS_VERSION 8→9`、行宽 `45→54`，尾部追加 9 列：`backHits/sideHits/frontHitsExempt/`
  `farHits/geoFallback`（idx45–49）＋`onLaneTicks/onLaneExemptTicks/onLaneMoveTicks/onLaneHoldFireTicks`
  （idx50–53）；`threatTicks` 谓词**原地改定义**（19px = 坦克半宽 16 + 子弹半高 3 / 去半径上限 /
  判墙体遮挡）；**公式一个字不动**（零调参；`wGeo*`/`wMuzzle*` 另开课程文件）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-metrics-v9`

## §2026-09-29-goalnn-publish-metrics-version-gate（2026-09-29，行宽版本门进**发布端**：按 shard 自己的声明剔，不送必炸字节上 GPU）

- **背景（现场）**：metrics v9 落地后（commit `b49b52ff`），h3-geo / x20-geo 的 it1 采集中，节点 `a98`
  同一轮内**同时**产出两种版本（`bun-26105` 4 份 (74,**45**)/metrics_version=8，`bun-12841` 3 份 (136,**54**)/9）。……
- **决定**：发布端 `remote.hub_client.iter_shard_dirs(..., metrics_version=<本机 METRICS_VERSION>)` ——
  manifest 声明的 `metrics_version` ≠ 本机 ⇒ **不进 payload**（与 D14 血缘过滤、state_init 起始分布过滤同族语义：
  「云端会整份拒收/必炸，故不进 payload」），逐版本点名日志（含节点指纹线索）。缺省 `None` = 逐字节旧行为。
  读取端 `ppo.np_core._reward_from_metrics` 改为**先判声明版本、形状检查降为兜底**，报错直接点「产出端导出器与读取端版本不同」。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-publish-metrics-version-gate`

## §2026-09-29-h4-nolane-open（2026-09-29，h4 移动穿越税新实验；§63 豁免待用户）

- **背景**：h3-geo 最佳落定 it45（pass 三方持平 93.0 中零伤 64.5% 最高，Pareto 占优，非 it50）；
  h4 it0 预跑（bc=h3-geo.it45 × ladder-c04 × 860001 × 200 局）lane 暴露 78.7/局 ≈ h3 的 2.3×、……
- **决定**：开 h4 平行双臂（h4-geo 去 explore／h4-nolane 加移动税，两臂公式只差一项）；
  同 bc（h3-geo.it45，379114B，sha256 `3a5cba10…e8ac` 已验）＋同 `paired_rotate_seed=1790491796`
  （h4-stop 实测流，与 h4-stop 历史可比；McNemar 前提；声明≠生效拒启）；
  新 out/traj（`tmp/h4-{geo,nolane}`，空目录，禁续跑）；wLane=200.0 已 pin……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-29-h4-nolane-open`

## §2026-09-30-h4-lane-wave1-verdict（2026-09-30，Wave 1 三臂判**无效**：控制臂自漂 ≥ 目标效应 + 功效不足；A2 的 ABORT 判**假阳性**）

- **背景**：E0 判红「绕后奖」→ 机制归到穿越税（§2026-09-29-h4-nolane-open）→ F1 臂身份锁（训练 `ladder-c04`
  （`count 4`/`max_ticks 3300`），评估 `ladder-c20-lives1`（`count 20`/`12900`）——两关 grid / enemy_spawns /……
- **决定（第一判：Wave 1 无效）**：判 **uninformative**，**不进 Wave 2 剂量扫描**。三条独立理由，任一条单独
  成立即足以判无效：
  1. **控制臂无效**——A0（零 lane 奖励、逐字 v4 包）在**同一主终点 rA2 = E[onLaneTicks/(ticks+1)]** 上自身
     漂移 **+15.6% / +34.6% / +25.4% / +21.6% / +16.5%**（it10/15/20/30/35；配对同 seed，单侧「上升」……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-h4-lane-wave1-verdict`

## §2026-09-30-goalnn-kickstart-burn-paired（2026-09-30，止损参照物改为同网格配对差；`auto` 缺省 = 对已配对课的行为变更）

- **背景**：Wave 1 判无效（§2026-09-30-h4-lane-wave1-verdict）——A2 的 ABORT 查实为**假阳性**：旧
  `kickstart_burn` 的参照物是**本腿自己的 it0 基线**，而控制臂 a0（零奖励 = 真零效应）自身漂移到位……
- **决定**：参照物两档。`baseline` = 旧口径（本腿 `it<=0` 末条）；`paired` = Δ = 本臂 − **对端同 it**
  读数（pp），尾部连续 `points` 点 Δ < −`margin_pp` ⇒ 触发。常量不变（`BURN_POINTS=3` /
  `BURN_MARGIN_PP=5.0` / `METRIC=winRate`）——只换参照物 = 最小改动。
- **接线**：`rl/kickstart_burn.py` 增 `MODE_BASELINE/PAIRED/AUTO` + `burn_mode()`；`burn_verdict(peer_rows=…)`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-30-goalnn-kickstart-burn-paired`

## §2026-09-30-goalnn-wave2-design（2026-09-30，Wave 1 三项遗留定案：漂移按共模消、剂量不重锚、几何只留 A1）

- **② 漂移按共模消**：主终点保持**同 iter 同 seed 配对差**（对照臂 = 同时训练的零奖励臂），漂移降为
  **每个评估点必报的诊断量**。依据（it20）：三臂自漂 +25.4 / +18.7 / +24.0%（绝对 +1.44pp），配对差
  只剩 **−0.379pp（−5.34%）** ⇒ 共模被消掉 3.8× 于效应本身。**否决**换锚（唯一不漂的锚 = 冻结 bc……
- **判据 = 尾巴 K 个评估点取均值（预注册 K=3、200 局/点），不是单点**：功效只取决于 **N×K 的乘积**
  （MDE ∝ 1/√(N·K)）。实测尾巴三点（it25/30/35）合并后 SE(Δrel) = 5.59% ⇒ MDE(80%) = **15.66% ≤ 16%**；……
- **Wave 2 预注册必须先修三处，否则守卫表判不了**：① 四条「非劣」终点（零伤局 / dmg / kills / shots）……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-wave2-design`

## §2026-09-30-goalnn-wave2-dose-ladder（2026-09-30，Wave 2 = 剂量阶梯 {7.5%, 15%}；含括号外档位）

- **定案**：Wave 2（b 腿）= 零奖励对照 + **A1 几何两档剂量**：**f = 7.5%（wA1 265）** 与
  **f = 15%（wA1 529）**；判据 = 尾巴 3 点均值 × **300 局/点**（每条线一个数字 ⇒ 工具 §11 表）；
  池外新 seed0 + 配对止损沿用（§2026-09-30-goalnn-kickstart-burn-paired）。
- **依据（§67 补评估：只评估、不重训）**：f = 3.6% 档的效应尾巴 **−8.52%**（95% CI [−14.84%, −2.21%]）
  ⇒ ① 绿线 16% 已被排除、② 效应可靠为正 ⇒ 到绿线需 ≈ **1.88×** 当前剂量；原括号（f = 3.3 / 4.0 / 7.5%）……
- **第二档为什么取 15%（4.17×）而不是 11–12%**：一阶外推不可信（惩罚项会被策略适应吸收）。若效应按
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-wave2-dose-ladder`

## §2026-09-30-goalnn-wave2-verdict（2026-09-30，Wave 2 三臂判决：两条剂量都不到绿线；7.5% 是唯一可靠正效应、15% 判零效应 ⇒ 剂量旋钮到顶）

- **主判定（预注册判据一字未改）**：主终点 rA2 = `onLaneTicks/(ticks+1)`，配对差 = 本臂 − `b0`（同 stage 同 seed）；
  判据量 = 尾巴 3 点（it30/35/40）Δrel 均值 × 300 局/点 @ 池外 seed0 `862001`（与日常段 860001–860200、
  §67 补评估段 861011–861310 均不相交）；前置 Q ≤ χ²(0.95, df=2) = 5.991 **两点都过**（0.40 / 2.63）。
  - **b1（f = 7.5%，wLane 265）**：Δrel = **−10.60%**（SE 4.34%，p₁ = 0.007，95% CI [−19.11%, −2.10%]）
    ⇒ **未达绿线（−16%）**，但**效应可靠为正**；「不到绿线」**未证实**（CI 下沿 −19.11% 越过 −16%）。
  - **b2（f = 15%，wLane 529）**：Δrel = **+3.19%**（SE 4.48%，p₁ = 0.762）⇒ **零效应**（逐点……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-wave2-verdict`

## §2026-09-30-goalnn-lever-scan（2026-09-30，塑形杠杆扫描：lane 族到顶、危险/卡死/固定窗/**停轴**退场；新价唯一候选 = 伤害形态）

- **背景**：Wave 2 判「lane 剂量轴到顶」⇒ 下一步换塑形杠杆、选型未定。用已有账本把候选逐个筛一遍
  （工具 `nn-training/tools/lever_scan.py` 九节，测试 `nn-training/tests/test_lever_scan.py`；全文 `docs/nn/experiments.md` §70）。……
- **税基（数值地基）**：现役 lane 税（`onLaneMoveTicks`）只压在 **0.251%** 的 tick 上（lane 的 5.45%）；
  lane 全家 4.610%；danger 7.0%、dmgFirst600 2.9%、承伤 6.4%、stuck 12.9% ⇒ 剂量到顶是**结构**不是调参。
- **结局解剖（b1，逐 tick 归一）**：承伤/tick（d=−3.12）≫ lane（−1.65）≈ 开局承伤（−1.73）；
  **危险 tick 几乎不区分（d=−0.09）**、卡死反向（清关更高，+0.42）；kills 的 d/r 是**定义性重合**……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-lever-scan`

## §2026-09-30-goalnn-metrics-v10（2026-09-30，plan/metrics-v10-gap-columns.plan.md；只加观测、零公式、零训练腿）

metrics 向量尾部追加「差距四族」15 列（`idx54–68`）：输入级 `stopTicks`/`fireHeldTicks`/`idleTicks`(54–56) ·
距离 `enemyDist`(57，哨兵 −1)/`nearEnemy4Ticks`(58) · 伤害时间形态 `damageBursts`(59)/`maxDamage120`(60)/……
**口径冻结**：被包围 = 与玩家中心垂直偏移 < `THREAT_ALIGN_BAND_PX`（19px = 坦克半宽 16 + 子弹半高 3）
∧ 沿源自己的轴无阻弹地形 ∧ 已激活 ∧ 存活的**敌车**数（**不带朝向、不带半径上限**）。**19px 不可改**——
§71 ②b 带宽敏感性三档实测：12px 旧带读数**腰斩且翻符号**（268/251）、naive「中心格同行列」直接归零
（200/200，cliff 0.05）；判不判遮挡几乎无差。豁免（冻/盾）只作**加法列**（`threatLaneExempt`；人类侧占……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-metrics-v10`

## §2026-09-30-goalnn-damage-form-retired（2026-09-30，伤害「时间形态」E0 判红 ⇒ 家族退场；plan/damage-time-form.plan.md）

- **背景**：§70 Ⅷ ② 判「lane 剂量轴到顶」，唯一新价候选 = 伤害的**时间形态**（变「挨打的集中度」而非窗/总量/零伤）。
  定立项时先跑 **E0 判决表**（预注册 K1/K2/K3/K4，plan §5）——探针 `tmp/dmgform-e0.ts`（不入库，reported 级），……
- **判决**：三形状**全不过**预注册 K 表 ⇒ **本族退场**（不做第四次；前四笔 = `wDmg` 常驻 / h5a 开局窗 / h5b 零伤 / `dangerTicks` 同族）。
  S1（状态条件型 `damageWhileLow`）死于 K1（0/2 档，重档**反向**）+ K2/K2′（无行动窗口）；S2（记录型 `maxDamage120`）
  死于 K1（1/2 档）且与总量 Spearman **0.948**（同族）；S3（事件型 `damageBursts`）死于 K3 稀疏线（0.315 事件/局）。
- **机制（结构性，不是「剂量不够」）**：① `maxHp 263` × 阀值 0.4 ⇒ `hp<105`——阈值以下最多还能计 ~105 点**非致死**承伤，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-damage-form-retired`

## §2026-10-01-goalnn-exempt-bakein（2026-10-01，豁免机制改「导出器拌入计数列」（决定 A）；plan/aim-dodge-levers.plan.md v10 增补批次）

- **背景**：v9 起豁免以「raw + 加法豁免列」对存在（`onLaneExemptTicks`、v10 `enclExempt1/2/3pTicks`）；一个豁免场景要为每个受影响族各加列（族×场景）。
  用户 2026-10-01 指令：找不随场景加列的通用表达，并拍板方案 A。
- **否掉的三个方案（机制证据，不是偏好）**：
  ① 公式侧逐行 flag × 累计列——翻转拍拿**整局累计值**乘 0/1 ⇒ 幻影 ± 尖峰；引擎禁跨步归约（白名单无 sum/cumsum），逐拍交集必须共位算（v9/v10 列注释已写死）；……
- **决定（A）**：新计数列一律**只累计非豁免拍**（导出器 `exposureExempt(world)` 拌入）；不再产出 raw/exempt 对；
  新增豁免场景 = 改谓词函数一处，列与公式零变更。谓词口径 = `freezeTimer > 0 ∨ 盾道具窗`（粘性 tracker；出生/复活盾与星盾不计入豁免）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-exempt-bakein`

## §2026-10-01-goalnn-aim-dodge-metrics（2026-10-01，aim-dodge 杠杆 8 列落地：idx66–73 / dim 74 / 事件扩展 / 回写机制；plan/aim-dodge-levers.plan.md §11）

- **背景**：`docs/nn/experiments.md` §71 人类 vs NN 面板判出「射术 / 暴露管理」四族全程分不开，但 v9 无列可测；
  用户 2026-10-01 定稿 aim-dodge 批次（开火结果族 / 火线承伤族 / 暴露杠杆族）并**裁决保留 `aimBricks`**（经典关复用）。……
- **决定（列布局）**：v10 批次**未签入** ⇒ 批次内整理 + 追加：移除 `enclExempt1/2/3pTicks`（原 idx65–67，兑现改
  决定 A「拌入」）、`enclMax` 顺位 68→65；追加 8 列（idx66–73）——`aimHits` / `aimHitDistSum` / `aimBricks` /
  `aimIgnited` / `aimMisses` / `hurtWeight` / `enclWeightTicks` / `cornerWeightTicks`
  ⇒ `METRICS_DIM 69→74`，**`METRICS_VERSION` 保持 10**（升 11 被驳回：持久产物中不存在「版本 10 + 69 列」）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-aim-dodge-metrics`

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
判据需要调小；**不得**改判据去迁就时长，也**不得**为了让排名好看去改生产缺省。……
**被否决**：① 把 `test_rollout_dispatch_resilience` 的窗口 0.6s 缩到 0.35s（实测工时极稳
0.173s，但余量从 3.5× 掉到 2×；本仓为负载抖动付过太多假红）· ② 收紧……
**违反后果**：新用例又盯着生产地板过日子 ⇒ 单条 1s 级安静回归（不占 CPU、不报错，只在墙钟……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-wait-gates-not-test-walls`

## §2026-09-29-goalnn-fork-after-collect-runner（2026-09-29，Linux 门禁的 pytest 分发改成「收集一次 + fork」）

**决策**：nn-training 的 pytest 分发在 **Linux** 上改用「master 收集一次 → `os.fork()` 出 worker」
（`nn-training/tools/forkdist.py`，POSIX-only 插件，`--forkdist N` 显式开启，与 xdist 的 `-n`
**互斥**）；**Windows/macOS 继续 xdist**。判据与既有的「双向路径」同源：**看选中的 python 是
什么**（`case "$NN_PY" in *.exe) → xdist`），再看内核（`uname -s` = Linux 才 forkdist），
**不看 uname/wslpath 存不存在**（2026-09-20 同型事故）。逃生口 `NN_GATE_FORKDIST=0/1`。
**为什么**（§48.5 记的「唯一剩下的结构性杠杆」）：`-n 12` 下**每个 worker 都要收集全部 ~275 个……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-fork-after-collect-runner`

## §2026-09-29-goalnn-forkdist-entrypoints（2026-09-29，`task.py` / Makefile 的 pytest 分发也改 forkdist，CI 实测后继续 `-n 2`）

**决策**：把 §2026-09-29-goalnn-fork-after-collect-runner（当时**只接了门禁**、并把「推广到
`task.py` / Makefile / CI」列为未做）逐个入口实测后处置——**`task.py`（`check` / `test*`）与 `Makefile`（`test` / `test-fast` / `test-e2e`）采纳**：
Linux 上分别走
`-p tools.forkdist --forkdist auto` 与 `-p tools.forkdist --forkdist $(NPROC)`，Windows/macOS 继续……
**为什么（各自实测，16 核，交错 2 轮取 min）**：Makefile 真入口 `make -s test-fast` min 墙钟
**19.73 → 15.05s（−23.7%）**、user **203.9 → 111.4s**；`task.py test-fast` 真跑 rc=0 / 15.43s；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-forkdist-entrypoints`

## §2026-09-29-goalnn-single-instance-guard（2026-09-29，一个 node id 只准有一个 agent 在听：互斥在应用层，不靠内核 EADDRINUSE）

**决策**：「一个 node = 一个监听实例」是 codeHash 门的前提，必须由 agent 自己保证。起听**之前**做一次
互斥判定（`tools/agent/single-instance.ts` 纯函数，判定表见全文）：**HTTP 探活 `/v1/status` 优先于 pid
锁文件**；`<WORK_DIR>/agent.lock`（`tmp/dist-agent/agent.lock`）用 `fs` 的 `'wx'` 独占创建作唯一互斥
原语（同时冷启动只有一个赢家），且锁在**预热之前**就抢。缺省 **fail-closed：拒起**（`exit(2)` + 一行……
**自己的父进程**）。`/v1/status` 与 `/v1/ping` 加法新增 `pid`/`bootId`/`instanceGuard`（旧字段逐字不变）。
**为什么**（h3-geo / x20-geo it1 云端 `grad` 5 连炸）：`serveWithRetry` 为重启链在 Linux 开了……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-29-goalnn-single-instance-guard`

## §2026-09-29-goalnn-node-bootid-gate（2026-09-29，节点门多一道「本轮 bootId 一致」：训练侧用 ping 的 pid/bootId 判 stale 并排除）

**决策**（用户裁定，采纳 `plan/sampler-single-instance.plan.md` §8-Q2 的**备选**而非其原推荐）：训练侧
节点门在 codeHash/bun 之外多一条「**本轮 bootId 一致**」——新模块 `nn-training/rl/node_identity.py`
（纯逻辑 + 进程内小账本，零依赖）按 `round_key(腿, 采集单元 id)`（`rollout:it42` / `eval:it42.eval` /
`batch:run.b7u3`）在**本轮**第一次 ping 时钉住该节点的 `bootId`（只钉一次，不一致后不改写 ⇒ 重复 ping……
**只上报 + 排除，绝不下发 `/v1/restart`**（两个进程同时服务不是重启一个能修好的事）。
**为什么**：① a98（2026-09-29）一台机器两个 agent 同听 8443，一轮 192 局被内核按 4 元组哈希分给两个……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-29-goalnn-node-bootid-gate`

## §2026-09-30-goalnn-nn-training-module-reorg（2026-09-30，nn-training 按角色重组为六包：`common/biz/worker/remote/hub/trainer`；第一刀 hub 出包）

**决策**（用户 2026-09-29 拍板口径，2026-09-30 追加 Q5 并落地第一刀）：把 `nn-training/` 的文件从
「按历史拆分动机散落」重排为**六个按角色命名的包**，并把依赖方向写成**可机器校验的偏序**：
```
L0 common/                 stdlib-only（含 platform_utils / pid_probe / schema / dist_* / jsonc）……
**第一刀（本次已落地）**：hub 服务端从 `remote/hub/` + `remote/hub_server.py` 出包为**顶层 `hub/`**，
入口从 `-m remote.hub_server` 改为 `-m hub.server`；三个「站在门面上的运维工具」（`smoke_loopback` /……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-module-reorg`

## §2026-09-30-goalnn-nn-training-l0-consolidation（2026-09-30，刀 2：`common/` 收口为**唯一 L0**；`dist_*` 去前缀）

**决策**：把散在 `nn-training/` 根下的 11 个 L0 模块全部收进 `common/`，其中三个 `dist_*` 去前缀：
| 旧家 | 新家 | 备注 |
|---|---|---|
| 根下 `platform_utils.py` / `pid_probe.py` / `schema.py` / `log_bundle.py` | `common/{platform_utils,pid_probe,schema,log_bundle}.py` | 形状从「顶层模块」变成「`common` 包的子模块」 |……
**为什么**：① L0 的定义是「stdlib-only、零上层依赖」，但它当时**散在根下**，只能靠「`NN_ROOT` 进 `sys.path`」
被 import —— 「哪一层」这件事读不出来，`test_common_is_a_leaf_package` 也扫不到根下的文件；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-l0-consolidation`

## §2026-09-30-goalnn-nn-training-worker-package（2026-09-30，刀 3：节点侧执行体出包为**顶层 `worker/`**，坐在 `remote/` **下面**）

**决策**：`remote/iter_rollout.py` + `remote/serve_pool.py` → 顶层 **`worker/`** 包，位于 **L2**
（`remote/` 是 L3、`hub/`/`trainer/` 是 L4）。**就这两个**：其余候选（`job_fs` / `job_lifecycle` /
`job_round` / `download` / `wire` / `http`）按 Q1 判为云机专属，留 `remote/`。
```……
**为什么坐在 `remote/` 下面（而不是并列）**：两模块实测**零 `remote.*` 依赖**（只靠 `common/`），
却被三层引用 —— 云机侧（`remote/offline_eval.py`、`remote/worker.py`）与 trainer 侧……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-worker-package`

## §2026-09-30-goalnn-nn-training-biz-package（2026-09-30，刀 4：`rl/` 拆出**顶层 `biz/`** —— 纯逻辑与编排分家）

**决策**：`rl/` 里**不达传输/执行面**的 **64 个模块** → 顶层 **`biz/`** 包（L1，与
`models/` `ppo/` `data/` `train/` `scripts/` 并列）。判据**不是手感**，是刀 3 之前就存在的
那张机械快照：
```……
**为什么必须拆**（而不是「rl 一个包也挺好」）：同一个包名住着两种东西时**依赖方向读不出来**。
`rl/queue.py`（编排，import `remote`）与 `rl/course.py`（纯逻辑，只碰 stdlib）看上去是同一层；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-biz-package`

## §2026-09-30-goalnn-nn-training-trainer-package（2026-09-30，刀 5 收官：编排整包改名 `rl/` → **顶层 `trainer/`**，`rl/` 从此不存在）

**决策**：`rl/` 剩下的 **37 个编排模块**（每个都直接或经包内传递可达 `remote|worker`）整包改名
**`trainer/`**（L4，与 `hub/` 同层、互不 import）；**成员一个没变**，只是整包换门牌，`rl/` 这个包
从此**不存在**。声明式快照随之改名 `RL_ORCHESTRATION` → **`TRAINER_ORCHESTRATION`**。五刀至此收官：……
**为什么改名**：「rl」这个名字在本仓有**三套口径** —— 模块路径（`rl.queue`）· 配置节名与启动参数
前缀（`cfg["rl"]` / `rl.stream` / `--rl.<k>` / `rl-config.json`）· 任务种类（`kind ∈ {rl, bc}`）。同一个词……
**★ 本刀的题眼 —— 快照的固定点前缀与切片必须同改**：`_trainer_reaching_remote()` 判「可达……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-trainer-package`

## §2026-09-30-goalnn-dashboard-sentinel-paths-must-exist（2026-09-30，`specs.ts` 哨兵路径必须实存 —— 孤儿哨兵修复 + 机械守卫）

**背景**：`dashboard/src/stack/specs.ts` 的 `localWorker` / `trainingLoop` 各有 `sentinel`（`core/reload.ts`
的 `snap()` 记 mtime，变了就重启进程；`core/sentinels.ts` 比对）。它是 `codehash-files.txt` 之外的**手工补面**，……
**问题**：刀 2（S3，5624856，2026-09-23）把 `remote/protocol.py` 下沉进 `common/`，两条哨兵仍指旧路径。
`snap()` 对**不存在的文件**返回 `null` ⇒ 哨兵**永不触发**、一行日志都没有 —— 恰好就是它要防的那件事。……
**决策**：① 哨兵路径改指真实家（`common/protocol.py`）；② 加**机械守卫**
`dashboard/tests/stack-sentinel-paths.test.ts` —— 扫 `specs.ts` 源码里所有 `'nn-training/…'` / `'tools/…'`
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-30-goalnn-dashboard-sentinel-paths-must-exist`

## §2026-09-30-goalnn-worker-local-training-stack（2026-09-30，刀 6：`worker/` = 本地 torch 训练全栈；`biz/` 只留游戏业务）

**背景**：用户 2026-09-30 三句话逐步收紧口径 —— ①「纯训练的内容都放在 `worker/` 下，比如 `rl/bc/ppo` 这些与业务
（游戏）逻辑无关的训练代码」②「`worker/` 应该包含所有支持**本地 torch 训练**的所有代码，**云机 worker = local……
**搬移集**（机械）：A 算法栈五包 `models/` `ppo/` `data/` `train/` `scripts/` → `worker/<同名>/`（39 文件）·
B 训练侧单体 `biz/<m>.py` → `worker/<m>.py`（**52**）· C `biz/log.py` → **`common/log.py`**（统一日志是通用基础设施：……
**★ 刀口 = 留在 `biz/` 的模块不得 import 搬走的**（实测 4 条向上边，`tmp/biz_split_recon.py`）：
`hot_reload → config.corpus_identity_fp`（指纹下沉成 **`biz/corpus_fp.py`** —— 语料身份由游戏域定义）·……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-worker-local-training-stack`

## §2026-09-30-goalnn-entry-scripts-into-packages（2026-09-30，刀 7：14 个入口脚本归位 —— `nn-training/` 顶层只剩 `conftest.py`）

**背景**：用户「nn-training 目录下还有几个 py 文件，把它们移动到合适的目录下」。按功能归位：六个训练/评估入口 →
`trainer/`；两个 worker 入口 → `remote/`；`rl_config_schema` → `worker/`；五个环境/运维脚本 → `tools/`；`conftest.py`……
**三条前提随搬家重算**（本刀真正的技术含量 —— 前两条**只在真机启动时现形**，单测天然看不见）：
① **脚本模式下 `sys.path[0]` = 脚本目录**（不再 nn-training）⇒ 六个入口各自前置「摘掉脚本目录项 + 放回 nn-training……
**刻意不加别名**（被否决的备选）：给 14 个旧裸名补 `LEGACY_ALIAS` 能让旧命令行继续可用，但别名森林让「脚本搬家了」
永远没人发现（旧名与新路径两份真相）。取**响亮失败**：`--script run_rl.py` 现在报 `script not found`。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-entry-scripts-into-packages`

## §2026-10-01-goalnn-log-keeps-era-names（2026-10-01，`DECISIONS.md` 的旧路径口径：追加型日志**不就地改名**，头部落一条译注）

刀 7 两轮 `.md` 散文清理（§2026-09-30-goalnn-entry-scripts-into-packages 同日续 / 续二）后清点本文件：仍有
**决策**：全部**保持原文**，只在文件头（「正文在哪」段后）落一条**旧路径约定**译注（读者/agent 的进入点，
写一次管全部）；**新条目用今天的名字**。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-log-keeps-era-names`

## §2026-10-01-goalnn-dashboard-gate-net-direct（2026-10-01，dashboard 门禁：测试进程出网直连 + 停课重试可注入 —— 全目录墙钟 11.4s → 1.8s，用例与断言逐条不变）

**症状**：dashboard `bun run test` 墙钟 11.4s（119 文件 / 1232 用例），而 per-file 时长总和 67.5s。
JUnit 逐用例（`--reporter=junit`）一看：几乎每个文件只有 1–3 个用例慢（1.5–4.0s），其余全在 0.01s……
**决策**：门禁脚本挂一个测试预载 `tests/helpers/no-proxy.ts`（`bun test --preload ./…`，注意 bun 要
`./` 前缀，否则 preload not found）：把 `NO_PROXY` / `no_proxy` 两个拼写都置 `*`，再调生产的
`core/net.shapeLoopbackNoProxy()` 追加精确回环做兜底。只改测试进程 env，不碰被测代码 / 断言 / 产物；
无代理环境（CI）逐字节不变。另修 `course-lifecycle` 里残留的 4.0s：停课走 `pushHubMode` 的缺省 3×2s……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-dashboard-gate-net-direct`

## §2026-10-01-goalnn-dashboard-tests-no-wallclock（2026-10-01，dashboard 测试不许有墙钟等待：等待由可观测事件触发，夹具主机不用 DNS）

**症状**：上一条（代理预载）修完后，逐用例时长里仍剩一批 1.5–4.0s 与大幅抖动——同一个
`buildStateView` 注入用例在 0.14–0.79s 之间来回跳。
**三处根因，共性是「等待靠墙钟猜」**：
1. `server-api-pool-swr` 的「`?fresh=1` 硬清」用例用 `await sleep(400)` 猜「这么久还没回来 ⇒ 它确实在等……
**读数**（JUnit 逐用例口径）：pool-swr 文件 0.58s → 0.19s（最慢条 0.47s → 0.14s）；port-reclaim 0.99s → 0.87s；
全目录逐用例时长总和 61.9s → **15.1s**；全目录墙钟 11.4s → **1.5–1.8s**（5 次里 4 次；一次 3.7s 是机器噪声）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-dashboard-tests-no-wallclock`

## §2026-10-01-goalnn-tests-mirror-package-tree（2026-10-01，nn-training 单测按源码包树镜像：`tests/<pkg>/` 子树，跨包守卫留根）

**背景**：六包重组（§51–§55・`plan/nn-training-module-reorg.plan.md`）明确留的后续刀——该 plan 写「`tests/` 目录本轮不动」。
用户口径（2026-10-01）：「nn-training 下的单元测试，也按照现在的项目源代码文件树结构，重新拆分组织」。
**决策**：`nn-training/tests/` 从 271 个 `.py` 的扁平目录改为**按包边界镜像**（`common/` `biz/` `worker/` `remote/` `hub/` `trainer/`，
外加 `nn-training/tools/` 的镜像 `tests/tools/`）；**跨包/结构守卫与基础设施留根**（`conftest.py` · `helpers/` · `golden/` · `subproc_util.py` +
10 个全仓纪律守卫：layering / remote_dag 三包账本 / LOC 预算 / 墙钟纪律 / CI / githook / conftest / dashboard kick-once / 入口脚本结构 / subproc_util）；
**`e2e/` 不动**。只镜像到**包**这一层——包边界是分层守卫钉住的语义边界，不再往下拆子包。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-tests-mirror-package-tree`

## §2026-10-01-goalnn-gate-halt-platform-level（2026-10-01，门禁停机模式升为平台级单开关：`tmp/gate-halt.json` 意图 + 回执；课程级三写面退役）

**决策**（用户 2026-09-25 口径：「`gate-halt-mode` 应是平台级而非课程级，有人盯盘时切为提示，离开时切停机」）：
「门禁触发时停不停」从课程级散装旋钮升成**平台级单开关**——`tmp/gate-halt.json`（控制台写、
supervisor 每轮判定读）+ `tmp/gate-halt.applied.json` 回执（逐课 `effective_mode` / `source`）；
`notify` 可带 `until`（2/4/8h/不限，缺省 8h），**到点由读时求值自动回落 halt**（不需要任何进程定时翻牌）。
**为什么**：它回答的是「**有没有人在盯盘**」——操作员此刻的状态，不是某门课的属性。课程级三写面
（每课 argv `--gate-halt-mode` / `courses.<课>.gate_halt_mode` / `<traj>/gate-halt-mode.txt`）外加隐藏第四面……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-01-goalnn-gate-halt-platform-level`

## §2026-10-01-goalnn-lease-orphan-reap（2026-10-01，孤儿租约早收：claim 后**零心跳**超 `ORPHAN_GRACE_SEC=180` 即回池；hub-only，协议面零改动）

**决策**：租约若**自 claim 起一次成功心跳都没有**且 `now - claimed_at > ORPHAN_GRACE_SEC` ⇒ 判为孤儿，
走**同一条** `_collect_expired_locked` 回收路径提前回池（不等满 `CLAIM_TTL_SEC=300`），并记账本事件
`lease-orphan-reaped {job_id, worker, silent_sec, reclaims, ts}`。实现只在 hub 侧：
`hub/store_leases.py` 新增 `ORPHAN_GRACE_SEC` + 唯一判据 `_lease_state`（四态……
**为什么**：认领是**单程**——hub 在 claim POST 到达时即设 owner+expiry+`last_heartbeat`，而
`lease_token` 装在响应 body 里；客户端读超时（`remote/job_lifecycle.claim_job`，`timeout=30s`）⇒……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-01-goalnn-lease-orphan-reap`

## §2026-10-02-goalnn-transfer-residual-yield（2026-10-02，让路税清算：条件让路 + 按传输累计上限 + 观测补面）

**背景**：L2 真机实测一条 wire 传输让路 **7.0s（= 传输时间的 38%）**，其中 **5.5s 压在关键路径**
（`payload` 1.0 + `blob:opt` 2.0 + `blob:ref` 2.5）。机制：控制请求整体在途期间 `_read_body` 每……
**备选与否决**：① 保持「无条件让路」（2026-09-25 §50 现状语义）—— 否：`p0_p95=1.02s` 远低于既有
`≤6s` 门槛（余量 ≈6×），让路买到的保护用不完，而税随取消环 1.5s 周期结构性复发；② 直接默认删让路……
**决定**：让路条件化（**最老在途**控制请求 ≥ `YIELD_AFTER_SEC_DEFAULT=1.0s` 才让）+ 按传输累计 ≤5s；
库层缺省保持老语义（0.0 / None），由 worker 显式装配 `--bulk-yield {auto,always,never}` 且 `worker_loop`
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-02-goalnn-transfer-residual-yield`

## §2026-10-02-goalnn-eval-final-round-and-dropped（2026-10-02，收官轮 eval 缺失 + dropped 局静默丢失）

- **背景**：h4-aim-k25 真机产物显示 ① it40（收官轮）训练期间 eval 零局——多课程 serve 的收官
  `finish_course` 没有 drain，而延迟派发只到 it-1 ⇒ 最后一轮权重永远没有 eval 路径；② it0 缺 8 局、……
- **决定**：`finish_course(it, *, drain=True, block=True)` 收编收官 eval drain（serve 的 RL 传
  `block=False` 不冻其它课；BC 另一份实现按 kind 分派、不带 kwargs）；claim 集（`seen`，位置不动）
  与 landed 集拆分——`_settle_complete` 与 `settle_eval_summary` 只认**落盘**（参数 `seen`→`landed`，
  `dropped` 公式一字未动，语义=结算→落盘）；`record()` 失败本线程重试 `EVAL_RECORD_RETRY_MAX` +……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-02-goalnn-eval-final-round-and-dropped`

## §2026-10-02-goalnn-course-pill-precision（2026-10-02，pill 精确化：hub 派发面接进 pill + 只读读面拿 iters 判收官）

- **背景**：两起独立缺陷。① pill 的在线课五态全是**本地进度词**；h4-aim-c0 的首个 job 被一个误启
  持有者「有心跳地」按住 27 分钟（真 GRAD 33.7s，对照 k10/k25 同配置 2.7/3.3 分钟），三道既有……
- **决定**：hub 观测面纯加法（inflight 行 `claimed_ago`/`computing_ago`；顶层 `peeked_courses`
  = 60s 内被 `peek_jobs` 返回过候选的课程集；只被 `/admin/queue` 读，调度判据零引用）· 收官判据
  单点 `worker/loop_tasks.budget_exhausted(it, iters)`（`iters<=0` ⇒ False；`LoopRunner.planner`
  行为逐字节不变），读面 `plan_course(iters=)` 跑满 ⇒ 空任务表 ⇒ `QUEUE_DONE`，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-02-goalnn-course-pill-precision`

## §2026-10-02-goalnn-worker-contribution（2026-10-02，并行 worker 贡献度：承接面归属 + 两个单位组永不合并 + 课程只作 breakdowns 轴）

- **背景**：7 门课并行、2 个云端 worker 领活的场景下，「谁干得多」在数据面上不成立：采样侧只有绝对
  局数（无份额/无课程维度/无盯盘窗）；PPO 侧**归属在结算那一刻被丢掉**——`job_completed` 由训练侧写……
- **决定**：① hub 承接面新增归属事件（**扩事件不新建账本**）：`accept_result` 成功写
  `job_result_accepted`（worker = 显式传入 ∪ 当前租约持有人，push 腿 `push:` 前缀），三条 409 路径
  （`_post_result` 早退 / `store_result` 失败 / push 输家）写 `job_rejected`；结果 POST 补 `X-Worker-Id`
  （`post_result`，与 claim 同一身份）。事件与训练侧 `job_completed` 同册（`tmp/<课>/training_log.jsonl`），……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-02-goalnn-worker-contribution`

## §2026-10-02-goalnn-e1-aim-open（2026-10-02，E1（aim 距离加权）开腿：剂量 κ 冻结 + 判据段 864001 + paired 止损显式对端；E2 终门现役复算 0.796；E3 复测门读数）

- **背景**：aim-dodge 8 列（dim 74）已落地（§2026-10-01-goalnn-aim-dodge-metrics）；E1–E4 的
  「开腿前必办」四项欠账在 2026-10-02 开课前补办。编排由用户拍板「**顺序波次、E1 先开三臂**」……
- **决定（E1 开腿）**：三臂 = 对照 `h4-aim-c0`（v4 逐字，无 aim 项）+ `h4-aim-k10` / `h4-aim-k25`
  （唯一差 = `+ wAim*aimHitDistSum`）；同起点（h4-stop.it60）同包、同 `paired_rotate_seed=20261002`
  （新开，§15.1）、新 out/traj（§15.5）；控制台 openCourse（在线档）开课。判据段 = **段A
  （seed0 864001–864300，300 局/点 @ it30/35/40）**，与日常段 860001–860200、Wave 1 补评估……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-02-goalnn-e1-aim-open`

## §2026-10-02-goalnn-e2-e3a-design（2026-10-02，E2/E3a 开腿设计冻结：f=7.5%/15% 两档 + MDE 实测 + 段B/C + 新 seed；E1 段A输入判无效）

- **背景**：E1 段A收官（864001 配对 900 局/臂，判据见 `h4-aim-c0.jsonc` 头注）：pooled dE[d|hit]
  k10 −0.037（反号）/ k25 +0.029（< kill 线 0.039）⇒ **机制无效**；Q 过（0.25/4.14 ≤ 5.99）；……
- **决定（E2 · 承伤 hurt-only，叠加）**：三臂 `h4-hurt-c0`（v4 逐字）+ `h4-hurt-f75`/`h4-hurt-f150`
  （唯一差 = `- wHurt·hurtWeight`，wDmg 保留——Pearson 前置门 r_within=0.796 ≤ 0.8，
  pooled 复核 r=0.798，同结论）；同起点 h4-stop.it60、同包、新 out/traj。
  剂量锚（b0it30/35/40 pooled n=600，`tmp/aim-calib/...run/eval_log.jsonl`，脚本……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-02-goalnn-e2-e3a-design`

## §2026-10-02-goalnn-x20-advanced-design（2026-10-02，x20 kitchen-sink 腿：B 底包 + 新 5 项 human-gap 定价，单腿无对照）

- **背景**：用户直接指定（非 plan 推导）：基于 x20 最佳权重（it175），新 5 项全上，大样本、
  多 epoch、无 it 限制；只比最终杀敌数与过关率，不与历史课程单变量对比。底包 = B……
- **决定（校准）**：human 160 局 vs it175 160 局（同关 c20 同 seed，v5 探针，
  `tmp/gap2-panel-v5-{human,nn-x20steady}.jsonl`，脚本 `tmp/x20adv-calib.py`）：
  kills 17.494 vs 6.162、通关 160/160 vs 15/160；Δacc=+0.2062、ΔE[d|hit]=+1.8794 格、
  ΔhurtRate=−0.05478、ΔenclRate=−0.28600、ΔcornerRate=−0.14274（全部与 plan §1.4 互验；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-02-goalnn-x20-advanced-design`

## §2026-10-02-goalnn-x20adv-h2-design（2026-10-02，x20-advanced hurt 重剂量对照臂：同起点 it175，wHurt 25.3→50.6）

- **背景**：it80 vs 人类复核 hurtR 反向（0.0693 vs 起点 0.0621）；用户指定加一档 hurt 重剂量对照。
- **决定**：新课 `x20-advanced-h2`，与主腿同包同预算、起点为主腿最优 it80（用户改起点；
  wHurt=**50.6**（f=15%，uniform gap pricing；it80 水平税 EV 3.51/局 ≈ 19% killEV，记账区上限内）。
  终判 @414000 段 800 局：hurtR 配对比较 + kills/通关率不劣于主腿终点（±3pp 诊断）。
- **落点**：`nn-training/curricula/x20-advanced-h2.jsonc`（validate_reward 零错误）·
  `nn-training/rl-config.json`（`x20-advanced-h2: {rollout_src: local}`）。

## §2026-10-03-goalnn-x20adv-plus-design（2026-10-03，二轮定价：新基线 a355，acc/hurt 加压，dist 维持，底包全留）

- **背景**：用户指定按出/被背刺两轮人类复核给全部 12 参数重新定价；基础权重用主腿最优 a355
  （414000 段 800 局 24.25%/9.666）。新基线（a355 同 160 对）：acc 0.5656（gap 0.2249，……
- **决定**：税用 §5.4 基线形、比值奖用 gap-value：**wAcc=13.6**（f=10%，acc 退化加压 rebalance）/
  **wDist=0.738 HOLD**（61% 走完，按 gap 重定价会给最后 0.7 格 3× 边际价，方向反，故维持 ~9% 份额）/
  **wHurt=70**（f=15%，9× gap + h2 的 50.6 已证不够；税 EV 15%）/ **wEncl=7.67** /
  **wCorner=23.7**（f=7.5%，税 EV 各 2.29/局；数值大是率小所致）。底包 7 个全留……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-plus-design`

## §2026-10-03-goalnn-x20adv-plus-rebase（2026-10-03，plus 换基 it415：锚重算，w 全跟进）

- **决定**：同算法重算 ⇒ **wAcc=15.3 / wDist=0.738 HOLD / wHurt=77.5 / wEncl=8.60 /
  wCorner=25.0**（税 EV 15%/7.5%/7.5%；起点净 +1.44/局）；bc 指 it415 文件；
  终判对照换 it415（28.25%/10.465）。旧参数复核结论（wShot 0.06 唯一动作）不变。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-plus-rebase`

## §2026-10-03-goalnn-x20legs-verdict（2026-10-03，三停腿收官：主腿/plus 成功加冕，h2 诊断成功归档）

- **决定（终判）**：① x20-advanced **成功**，加冕 **a580**（28.75%/10.723；亚军 it500）：
  it175（13.50%/7.619）→ 过关翻倍、杀敌 +41%；Edhit 走完 ~50%、encl/corner −37%/−30~45%、
  acc/hurtR 平；停腿时仍在高原爬升，无 plateau 证据。② x20-advanced-plus **成功**，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20legs-verdict`

## §2026-10-03-goalnn-x20adv-3legs-design（2026-10-03，三加压腿 same-start q90 比收敛速度）

- **背景**：plus 停腿（it167）；用户指定以 q90 为共同起点开 2–3 条加压腿比收敛速度；
  被背刺率用户裁决用被包围税近似，不加观测列。基线（q90 同 160 对）：acc 0.5895（gap 0.201）·……
- **决定**：三腿与 plus 逐字同（起点 q90 + kickstart 锚 ref/init=0.1，预算 250 轮），各加压一轴：
  **acc 腿 wAcc 15.3→23.0**（×1.5；inflow 偏大已记档 level-vs-边际）/
  **hurt 腿 wHurt 77.5→110**（1.5× 破 20% 上限 ⇒ 按上限封顶 f=20%，税 EV 19.9%）/
  **encl 腿 wEncl 8.6→13.0**（×1.5，税 EV 9.9%）；dist/corner/wShot/底包全 hold。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-3legs-design`

## §2026-10-04-goalnn-x20adv2-design（2026-10-04，下一阶段三腿：merge-max/dist-lastmile/acc-push，从 hu150 同起跑）

- **背景**：三最优（acc80/hu150/en225）vs 人类同 160 对全指标对照。剩余 gap：
  kills 17.5 vs ~11.5 · pass 100% vs ~30% · acc 0.79 vs 0.62–0.65 · Edhit 4.40 vs……
- **决定**：三腿同起点 hu150（32.50%/11.370，全场冠军）+ kickstart 锚 + paired 20261006 +
  预算 150 轮 + 早停：① **merge-max**（wAcc 23 + wHurt 110 + wEncl 25，三 escalations
  并存；三税合计 ~31% killEV，配胜率熔断连续 2 点 < hu150−3pp 即人工停）；② **dist**
  （wDist 0.738→1.5，f≈3%，测 last-mile）；③ **acc-push**（wAcc 15.3→30.0，f≈14%，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-04-goalnn-x20adv2-design`

## §2026-10-04-goalnn-x20adv-3legs-verdict（2026-10-04，三加压腿收官：峰全超 q90，250 轮过长，首达线设计失误）

- **决定（终判）**：三腿成功，加冕 **acc80 / hu150（全场最优） / en225**。
  收敛速度排名：acc 最快（it80见顶）> hurt 最高（it150）> encl 最慢（it225）。
  250 轮过长——峰全在 it80–225，尾巴 25–100 轮白烧（acc250 甚至崩盘 −8pp）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-04-goalnn-x20adv-3legs-verdict`

## §2026-10-03-goalnn-x20adv-encl-maxtax（2026-10-03，encl 腿顶格 25 + 胜率熔断）

- **决定**：`x20-adv-encl` wEncl 13.0 → **25.0**（f≈19%，税 EV 6.39/局，踩 20% 上限线；
  人类−q90 税差 ≈ 4.8/局）。配套硬熔断：日常胜率相对 q90 基线连续 2 点 < −3pp 即
  人工停腿（E3a 曾打出 −7.8pp，本腿税更重只紧不松；控制台人工执行）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-encl-maxtax`

## §2026-10-03-goalnn-x20adv-plus-oldparams（2026-10-03，plus 旧参数复核：按 tick 率剥离 horizon，只动 wShot）

- **决定**：只动 **wShot 0.05→0.06**（NN 火多中少，加罚“少而准”，EV 动 0.44/局）；
  其余 6 个全留（wKill numeraire；wHit 加量只买量；wDmg×2 有 −2.8pp 实付代价且 hurt-70
  已覆盖；wPickup 已占三成；wExplore/wApproach 无信号）。明确拒绝：dmgFirst600……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-plus-oldparams`

## §2026-10-02-goalnn-engine-pool-midround-pin（2026-10-02，引擎缓存上限不再钉死 5 + 轮内不许抽引擎 + `_node_rollout` 初值）

- **背景（事故）**：7 门课同时开课（h4-hurt-{c0,f75,f150} · h4-encl-{c0,f75,f150} · x20-advanced），
  而 `EnginePool` 数量上限是死值 `DEFAULT_CACHE_COURSES = 5` ⇒ 按 LRU 互逐。被逐的课下一拍……
- **决定（P0，止血）**：`loop_core.TrainingLoop.__init__` 补 `self._node_rollout: bool = False`；
  `loop_round_steps` 的两处裸读（`step_ppo` / `step_cleanup`）改 `getattr(..., False)` 兜底。
  初值 `False` = 本地轮：即便读到早值也只是把采集判成本地，不改变采集行为。
  **不变量**：引擎被重建后必须能安全续跑（与上限怎么调无关）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-02-goalnn-engine-pool-midround-pin`

## §2026-10-02-nodes-lastcontrib-winner-must-have-completed-round（2026-10-02，节点「贡献 0 / 离线」误报的根治）

- **症状**：控制台节点 pill 把 `self` 显示成「贡献 0 / 离线」，而它是当天贡献最高的节点
  （窗口内 rollout 9644 + eval 1950 局）；表格展开行的「最近完成轮贡献」也是 0。
- **根因**：`lastContrib` 取自跨流的「最新完成轮」winner，而 winner 的完成时刻写作……
- **决定**：winner **只在真跑完过至少一轮的流**（`completedIt !== null`）里选；全部流都没跑完
  ⇒ `latestRound = null` + `lastContrib` 空表（消费方得 -1 = 「无池数据」），**不挑一个充数**。
  实现形状：`FlowState` 增 `completedIt`（该流账本最后一个 `iteration` 的 it），判据只用它。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-02-nodes-lastcontrib-winner-must-have-completed-round`

## §2026-10-02-goalnn-burn-rule-in-course-file（2026-10-02，止损块进课程文件：`kickstart_burn`/`paired_kill` 从 rl-config 迁入 `curricula/*.jsonc`，块不进 `corpus_fp`）

- **背景**：结果面止损（干烧熔断 / 配对中点杀臂）的参照物与阈值住 `rl-config.json`（机器本地、
  不进 git）——换机器/新克隆静默回落 `baseline`（回测假阳性 49.3% vs paired 0.47%，~105×）；……
- **决定**：`biz/course_spec.py` 加 `KickstartBurnBlock`/`PairedKillBlock`（解析期强校验，脏值拒课）；
  5 个读函数改「课程块优先 + `legacy_*` 兼容回落」；`RESTART_ONLY_FIELDS` 加两键（不进 `HOT_FIELDS`）；
  `corpus_identity_fp` payload 不加两键；判据本体 `burn_verdict`/`paired_kill_verdict` **逐字不动**；
  控制台 `burnThresholds` 同源（课程块 > rl-config > 常量）。h4-aim-{k10,k25} 落块、h4-aim-c0 写……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-02-goalnn-burn-rule-in-course-file`

## §2026-10-02-goalnn-burn-rule-cut2（2026-10-02，止损块第二刀：删 rl-config 回落读面 + 六处迁移 + 存量清理）

- **背景**：`§2026-10-02-goalnn-burn-rule-in-course-file` 第一刀留了双读兜底（课程块优先 + `legacy_*`
  回落 rl-config）。六个使用者（h4-hurt×2 + h4-encl×2 + x20-clutch 两门；h4-aim×2 已于第一刀迁）……
- **决定**：① 删 `legacy_burn_mode/overrides`、`legacy_paired_kill_overrides/self_kill/enabled`
  六个回落读函数；`burn_mode/burn_overrides`/`paired_kill_*` 五函数去 `fallback` 参数（块缺席 =
  模块常量）；`loop_guards_leg` 两个守卫去 `dist_cfg` 参数（`course_key_of` 唯一用处消失）。
  判据本体 `burn_verdict`/`paired_kill_verdict` 逐字不动。② 迁移六处块（值逐字复制）：h4-hurt-{f75,f150}……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-02-goalnn-burn-rule-cut2`

## §2026-10-03-goalnn-auto-offline-handoff（2026-10-03，离线盘 claim 即接管：一拖一闸 + 派发状态落盘 + 三态意图表）

- **背景**：Kaggle TPU 排队数小时，开课时无法预判「该不该离线」；「开课时指定离线/在线」只能事后手切，
  而离线课程在 TPU 上优势极大。用户 2026-10-02 口径 U1-U6（plan/auto-offline-handoff，评审两轮 bf/hy）。
- **决定**：① 触发点在 **claim**（不是「盘上线」）：清单一律对未 pin 的在训课开放且**允许无包**；
  claim 无包 = 翻 mode + 控制台反向调用（`/api/autoOfflineHandoff`）导包 + 409；云机侧
  `claim_course` 返回值升级为 `(token, reason)`，中间态（`busy`/`pending_export`/`completed`/
  `not_offline`）本拍不跑、不耗 idle 预算，到上界就本会话放弃。② U2「一拖一」做成 **hub 侧不变量**……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-goalnn-auto-offline-handoff`

## §2026-10-03-goalnn-auto-offline-handoff-t6（2026-10-03，T6 完成态链 + T3 面板钮 + T8 告警渲染落地）

- **背景**：`§2026-10-03-goalnn-auto-offline-handoff` 门槛如实记了四处未落地；本轮除 T0 外全部落地。
- **决定**：
  ① **T6 判决在控制台、落账在 python**：`end_it_reached` 由云机自报（`plan_run._end_it_reached`：
     `complete`/`noop` ∧ `it_end >= plan.end_it`；`max_iters` 截断不算）；hub 字段白名单 + 联动
     `note_offline_completed`（该包 completed ⇒ 不可再领，重导 sha 变自动解封——此前它是死代码）；……
- **被否决**：① 一趟导入里转交（run_id 在解包前不可知 ⇒ 只能两趟）；② TS 直接写 `run_complete`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-goalnn-auto-offline-handoff-t6`

## §2026-10-03-goalnn-switch-mode-drops-jobs（2026-10-03，切模式撤单落地：复用 `job_cancelled` 账本事件 + 只撤没被领的）

- **背景**：`plan/auto-offline-handoff` 的 T0（唯一未落地项）= 落地 `plan/switch-mode-drops-jobs`：
  切「在线/离线」= 上一个模式留在队列里的 job 该作废。原始触发是 2026-09-25 报障「x20-dodge-l1 离线……
- **决定**：
  ① **落点重定位（T0a 的实质结论）**：原 plan §3 改点 2 钉 `remote/plan_run.py::run_plan_job`，但它
     **今天零生产调用者**——`kind=run`（整段 job）2026-09-25 双端退役（发布端 `loop_remote_job.py`
     当场 `SystemExit` / worker 端 `worker.py` 响亮 `ProtocolError`），`tests/remote/test_plan_run_split.py`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-goalnn-switch-mode-drops-jobs`

## §2026-10-03-goalnn-gate-parallelism-physical-cores（2026-10-03，门禁/本地 worker 池并行度改按**物理核数**：新增 `physical_cores()` 第二口径）

- **背景（事故）**：`bun test` 裸 `--parallel` 的缺省 = **逻辑核**（本机 8c/16t ⇒ 16 worker），
  实测把 Windows 的提交上限顶穿（物理 28.9G + 固定分页 14G = 42.9G，空闲提交仅 5.3G）⇒……
- **决定**：
  ① **两个口径按消费域分工**（每一域仍只有一个答案）：`effective_cores()` 留作**训练运行时**
     （rollout/eval 槽位、`biz/cli.py`、notebook、`sampler-agent`、torch 线程数）；**新增**
     `physical_cores()` = **min(真物理核, cgroup 配额, 亲和掩码)**，都读不到才回落 `os.cpu_count()`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-03-goalnn-gate-parallelism-physical-cores`

## §2026-10-03-offline-pack-truncation-and-ref-blob（2026-10-03，用户报障×2：半截任务包被当成功 / 离线包缺 kickstart ref 字节）

- **背景（事故一）**：Kaggle 取包 `task-pack` 声明 `Content-Length=4322911`，隧道在 3670016
  （**14×256KB**）处干净 FIN，而引导期护栏记 `attempts=1 rerolls=0`（= 成功）⇒ 半截 zip 落盘……
- **决定**：① 两处孪生 `_read_body`（`remote/tailscale_boot.py`、`remote/http.py`）在 EOF 处
  比对「已收 vs Content-Length」，不足即按传输失败抛（引导期 `BootBodyError` 进同一次 fetch 的
  内层换连接重试；worker 侧带正文 `TimeoutError` 进退避重试）。② ref 与 demo 同规进离线链：
  `export_bundle` 带 `ref_weights.json`（`OPTIONAL_PARTS` + README + `blob.ref` sha 对账），……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-offline-pack-truncation-and-ref-blob`

## §2026-10-03-goalnn-offline-seize（2026-10-03，用户裁决：pin 不再拦离线盘——离线优先、没有就抢第一个在训在线课）

- **背景（报障）**：用户把三门在训课（x20-adv-{encl,hurt,acc}）手动设成 online（pin=1）后，
  Colab 离线云机 `GET /offline/tasks` 持续 0 条（`plan/auto-offline-handoff` 的 P0-3 判据：……
- **决定**：
  ① **pin 语义坍缩**：`auto_eligible` 去掉 pin 项（在表 ∧ 开课标记在）——pin 仍落盘/重启不丢，
     但**不再拦离线盘**；唯一 opt-out = **停课**（删 `training-enabled.txt`）。副产物：pin 的
     课缺包不再 404，改走 `_claim_without_pack`→`begin_auto_handoff`（翻模式 + 触发控制台导包）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-goalnn-offline-seize`

## §2026-10-03-goalnn-cpu-slots-reserve2（2026-10-03，TPU 核数口径：Colab 配额=4 核不是 24；预留 4→2 统一；log 报三源出处）

- **背景（误读现场）**：Colab TPU 上 `kind=iter rollout done in 34.909s｜games=176｜workers=3`，
  用户据「实例有 24 个 cpu 核」怀疑 3 workers 不可能跑完 176 局。现场诊断（同一台）：宿主……
- **决定**：① **用户校准：预留 4 → 2**，统一改 `max(cores−2, cores×0.8)`（`CPU_RESERVE=2`；
  Kaggle 96→94、Colab 4→3、16→14、8→6；`rollout.cloudflared.ipynb` 的内联回退同步）；
  ② **「修 log」= 报出处**：新增 `common.platform_utils.cores_note()` →
  `cpu_count 24 / 亲和 24 / cgroup 配额 4 ⇒ 可用 4`，挂进 `plan_run`「rollout 并发局数」行与……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/tpu-perf.md` §9「决策正文归档」· 锚 `### §2026-10-03-goalnn-cpu-slots-reserve2`

## §2026-10-04-goalnn-sympy-printing-guard（2026-10-04，Colab TPU：`import torch_xla` 抛
`sympy.printing` 缺绑定 ⇒ 导入前补绑守卫）

- **背景（真机事故）**：Colab TPU 离线课程，rollout 正常跑完 176 局后，PPO 起步时
  `remote/train_core.run_training_core` → `xla_device()` → `import torch_xla` 抛……
- **决定**：`worker/ppo/np_core.py` 新增 `_ensure_sympy_printing()`——**只在缺失时**补绑
  `sympy.printing`（幂等；sympy 不存在静默放过；绝不抛），并在**本进程第一次** `import
  torch_xla` 之前调它（`xla_device()` 与 `xla_enable_compile_cache()` 两个入口，后者是训练
  路径上的首个 torch_xla 触点）。同时把上面那句误导文案改为带上真实异常……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/tpu-perf.md` §9「决策正文归档」· 锚 `### §2026-10-04-goalnn-sympy-printing-guard`

## §2026-10-04-goalnn-opt-blob-must-ride-the-pack（2026-10-04，自主段起点动量必须种子进
blob_cache；合成轮只声明「本地可兑现」的 opt sha）

- **背景（真机事故）**：Colab TPU 离线盘抢占到在线课 x20-adv-acc（计划 it195→it250），
  rollout 每轮正常跑完 184 局，紧接着 **PPO 起步前**失败：……
- **根因（两条，都在「自主段没有下载通道」这条前提下）**：
  ① `plan_run._run_iteration` 用 `run_job("", "")` 合成轮次 ⇒ `_resolve_blob` 只有……
- **决定**：① 新增 `plan_handoff._seed_opt_blob_cache`（与 `_seed_ref_blob_cache` 同规）：
  按 产物 `it-N/opt.tar` → 包根 `opt.tar` → job `blob.opt` 找字节，种进 `blob_cache/<sha>`，
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-04-goalnn-opt-blob-must-ride-the-pack`

## §2026-10-04-goalnn-pack-freshness-on-handoff（2026-10-04，抢占在训在线课必须重导包：
权重/动量维度 + **代码**维度；hub 有包那条腿也要问控制台）

- **背景（用户报障）**：「如果只在开课时生成离线包，然后手工转为在线模式，那么训练中途被
  离线 worker 抢占后，这个课程在线训练的权重和 opt 不就全丢了吗？」——属实，两个放行口叠加：……
- **决定**（判据 = 「包是否仍代表当前起点与当前代码」，全在控制台侧判）：
  ① 规则表 ⑥ 拆成 ⑥/⑥'：包的 mtime **不早于**「活动权重（`tmp/<课>/weights.json`）mtime」
  且**不早于**「`code.zip` 源文件最大 mtime」⇒ 复用（不导不废）；任一更旧 ⇒ **作废 + 重导**
  （⑥'，与 2026-09-22「重启离线课要重打包」同一条）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-04-goalnn-pack-freshness-on-handoff`

## §2026-10-03-goalnn-dashboard-alert-dock-global（2026-10-03，告警坞全局化：收官横幅按课成列 + 七类条目全可关可复制）

- **背景**：用户 2026-10-02 两条指令（收官横幅要全局可见 / 所有横幅可关闭可复制）。改前收官横幅读面是
  单课单值（`StateView.loopComplete` + 按课程键控的 `computeSlowSnapshot`）⇒ 不切课看不到「已经跑完了」；……
- **决定**：收官横幅读面扩为 `loopCompletes: Record<course, LoopComplete>`，聚合落 `state-view.ts`，判活用
  共享 `trainingLoop` 的全局存活事实，课程清单 = 已开课 ∪ 查看课，条目按课成列、标题带课名、按课名排序；
  新 `alertAckKey(kind, subject, eventId)` 统一事件身份键（`cloudHaltAckKey` 委托它 ⇒ 停机键逐字节不变），
  存储仍用同一张表（常量改名 `TC_ALERT_ACKS`、值冻结 `tc.cloudHalt.ack`）；`AlertItem.copyText` 由……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-03-goalnn-dashboard-alert-dock-global`

## §2026-10-03-goalnn-request-path-zero-aggregate（2026-10-03，控制台重载 ~10s + 常驻 1GB：请求路径零聚合 + 聚合增量入账）

- **背景**：用户 2026-10-03 报「dashboard 重载 ~10s + 进程占 1GB」。实测：`/api/state` 40 连击
  17/40 落 0.3–2.7s、WS 涨到 1.38GB、空载 CPU ~30% 单核；`aggregateNodeHistory()` 冷算 1057ms /……
- **备选与否决**：① 只降 `LARGE_META_BYTES`（少读几份大文件）——否：**丢历史窗口**（把内存换成错数，
  与「全部根治」口径冲突；本 plan 用增量而非截断换性能）；② 新增一个 `createSwrCache<ContributionView>`——……
- **决定**：三条一起做（缺一条只是把症状搬位置）——
  **R1** 贡献度缩略挂 `FleetProbes.contributionBrief`（`computeFleetProbes` 后台顺手产出；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-03-goalnn-request-path-zero-aggregate`

## §2026-10-04-goalnn-cpu-slots-small-full（2026-10-04，并发口径小机器校准：CPU ≤ 4 核全给）

- **口径（用户逐字）**：「rollout worker 现在为 max(cores−2, cores×0.8)，我希望改成如果 cpu
  少于等于 4 核，则使用全部 cores」。
- **决定**：`common/platform_utils.cpu_worker_slots()`（rollout 与 eval 的**唯一口径**）新增
  `CPU_ALL_SLOTS_CORES = 4`：`n ≤ 4 ⇒ n`（全给）；否则照旧 `max(n − CPU_RESERVE, floor(n×0.8))`。……
- **被否决**：① 只改 rollout 不改 eval——否：唯一口径（交替语义，2026-09-22 立）必须同源，
  两处各写一份就是下一次误读；② 给小机器另立一套公式——否：同一个函数里一个阈值即可，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-04-goalnn-cpu-slots-small-full`

## §2026-10-04-goalnn-worker-readable-name（2026-10-04，worker 身份 = 环境+连接方式；环境判据 = 平台包可导入性；不做重名消解但留观测守卫）

- **背景**：dashboard PPO 贡献度的 worker id 是 `hostname:pid`——Kaggle/Colab 容器的 hostname 是平台
  随机串（`2f3dd8b0e0e2:4128`），既不表环境也不表连接方式。用户裁决六条：格式 `{env}-{link}`；历史 id……
- **决定**：① 判据唯一实现 `common/env_probe.py`（纯函数）：env = `kaggle_secrets` /
  `google.colab.userdata` 可导入性（顺序 kaggle→colab→aistudio→local）或 `/home/aistudio`；
  link = offline ⇒ `o`、host ∈ `100.64/10` 或 `.ts.net` ⇒ `t`、其余 ⇒ `c`；`local` 无 link 段。
  ② `worker_tag(offline, hub_url)` 纯函数；生产三处调用点单次计算后显式下传（`post_result(worker_id=)`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-04-goalnn-worker-readable-name`

## §2026-10-04-goalnn-offline-eval-backfill（2026-10-04，离线课：云机没回传 eval 时 hub 端用 LAN 集群补评）

- **背景**：云机 A 层 eval 可关（`eval_on_cloud`）⇒ 整段回传期间零读数，日常评估点与收官轮没人评。
  用户口径：评估点 N 的权重回传后等 +3 轮仍无读数 ⇒ hub 补评；课程收官轮若需评估也执行。
- **决定**：新 `dashboard/src/server/offline-eval-backfill.ts`（纯选择 + 增量账本索引 + 编排）；
  +3 宽限 = 该 run `max(its) >= N+3`；收官 = `end_it_reached` 时最后一个评估点
  （`floor(it_end/eval_every)*eval_every`）缺读数立即补；动作复用唯一启动点 `launchEvalA`
  （LAN 节点 + 本机份额）；`(iter, wver16)` summary 幂等 + 失败指数退避（10min→2h）+ 单槽串行；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-04-goalnn-offline-eval-backfill`

## §2026-10-04-goalnn-sampler-workers-physical-cores（2026-10-04，sampler `--workers` 缺省改**物理核**：池常驻内存封顶减半）

- **背景（内存盘点，本机 live）**：sampler-agent 主进程 ~54MB（26.5h / 13.6 万局稳定，内部状态全部有界）；
  长驻池 worker **74–100MB/个**（bun + 全图 + native 卷积库/权重装载；wasm 仅 attestation 参考/回退）。……
- **决定**：`physicalCores()`（`tools/lib/cores.ts`：min(真物理核, cgroup 配额, 亲和掩码)，Windows 走
  pwsh CIM 记忆化——与 §2026-10-03 门禁/本地池口径同源）为 `sampler-agent` 的**唯一核数口径**：
  `const CPUS = physicalCores()`，`workers` 缺省 = `CPUS`；`/v1/ping`/`/v1/status` 上报的 `cpus` 同值
  （**同日追加指令**：上报也改物理核——无 `concurrency` 配置的节点按 cpus 派工回落，与并发门同口径）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-04-goalnn-sampler-workers-physical-cores`

## §2026-10-04-goalnn-sampler-idle-reap-and-task-timeout-fix（2026-10-04，sampler 池：空闲回收 + 600s 超时定时器误杀修复）

- **背景（同日内存盘点续）**：池「只涨不回缩」——按需长到 `workers` 上限后即使负载回落也不释放；
  且 live 日志每 ~10 分钟一批 `persist worker closed ... SIGTERM (was busy=true)` + 秒速重生。代码追证：……
- **决定**：
  ① **空闲回收**：`PERSIST_IDLE_MS = 30min`（长阈值；`--pool-idle-ms 0` 关）× `PERSIST_POOL_FLOOR = 4`
     （`--pool-floor`；被 `workers` 夹取）。纯计划 `reapPlan`（只收空闲 ≥ 阈值、绝不碰在飞、先收最久空闲、
     收后不小于 floor）住 `persist-pool.ts`；agent 60s 节拍接线（`unref`）。回收 = SIGTERM + 摘池，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-04-goalnn-sampler-idle-reap-and-task-timeout-fix`

## §2026-10-05-evalboard-off（2026-10-05，evalBoard 缺省停用：四处接线一门开关 + 账本冻结）

- **背景（内存盘点 R4）**：首页 EvalBoard 摘要 + `/eval` 页 + 每 30s auto-ladder 是控制台请求
  路径上最后一块常驻聚合面；用户 2026-10-04 指令「先关掉 evalBoard」——**不删代码、不删数据**。
- **决定**：功能开关 `BCITY_EVALBOARD`（`src/core/feature-flags.ts::evalboardEnabled`，**缺省 `0`**，
  `=1` 开）一处控四处接线（首页面板 / `/api/evalboard` / 30s ladder ticker / `/eval` 页）；
  决策与计数器集中 `server/eval-board/routes.ts`，`server.ts` 零 `buildEvalBoardView(` 直调；
  `/eval` 停用时出说明页且**不注入任何 bundle**（`eval-app.tsx` 自己会轮询 API）；开关信号随……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-05-evalboard-off`

## §2026-10-05-course-startup-recover（2026-10-05，课程配置错被整课跳过：判据变即自动重试 + 只读 `blocked` 判据）

- **背景（用户报障）**：`x20-adv3-open-r2` 的课程文件自相矛盾（`kickstart_init>0` ∧ `kickstart_ref=false`）
  ⇒ 启动期 SystemExit、整课被共享 trainer 跳过；人改好文件 + 控制台「停→开」后日志零变化——唯一出路……
- **决定（P0 复活通道）**：「跳过」从**终身黑名单**改成**带判据指纹的待重试表**：
  `ServeReport.skipped_at[course] = {kind: 来源分类, fp: 判据快照}`；每拍（既有 discovery 空转拍，
  不新增节拍）由纯函数 `reopenable_skipped` 比对——课程文件身份（mtime_ns+size）/ 开课标记 mtime /
  按课程锁签名（释放/换主/持有者死）/ 账本 mtime，**按跳过来源取事实**（config / lock / enqueue）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-05-course-startup-recover`

## §2026-10-05-goalnn-gate-fail-fast（2026-10-05，nn 门禁 fail-fast：任一腿先红即停其余腿）

- **背景**：2026-10-05 一次 `bun run pygate`：ruff 约 1s 就红（`test_wire_reroll.py` 的
  import 排序），门禁仍陪跑完 23s 的 pytest 才报错——失败路径的「等」是提交循环里最贵……
- **决定**：门禁 v3.20 起按启动顺序（ruff → mypy → pytest）收账，任一腿非零即 `stop_tool`
  掉还没收账的腿再 wait 回收；启动仍三路并行（happy path 墙钟不变）。杀法按「选中的
  python 是不是 Windows 二进制」判：Windows 走 `taskkill //F //T` 连树（hook 模式内层
  pytest 与 xdist worker 是 detach-run 的后代，只杀壳 = 孤儿）；POSIX 走 `kill -KILL`，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-05-goalnn-gate-fail-fast`

## §2026-10-05-goalnn-offline-online-status-switch（2026-10-05，用户报障×2：手动切在线不稳定 / 云机掉线后课程永久停摆）

- **背景**：报障一「切成在线不稳定」= pin 语义在 §2026-10-03-goalnn-offline-seize 坍缩后「我要在线」没有表达（切了被抢回）+ 撤单即时闸不认账本复活 + 本机腿把离线等待当收官；
  报障二「课程永久停摆」= 死租约冻结全池 / 无包腿烧满 `give_up` 后被新盘 skip / 导包窗口无主也不释放。……
- **决定（行为变更 21 条，摘要）**：① `pinned ∧ mode=online` 重新获得阻止力（离线盘不可 claim/seize/翻模式 = `pinned_online`）；
  ② 切在线/交还自动撤销离线租约（墓碑 + 心跳 409 `revoked`）；③ 离线租约失联早收（静默 180s 自动可接管）与 busy 活性口径
  （stale/revoked/expired 不算忙；窗口锚 `claimed_at`，停滞告警仍锚 `flipped_at`）；④ 切在线不撤单 + `_cancelled` 按**账本净态**
  对账（生产写者 = `remote/hub_client.publish_job`）；⑤ 训练侧离线 = 等待（不写假 `run_complete`），复原前提 `--rollout-src auto`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-05-goalnn-offline-online-status-switch`

## §2026-10-05-policy-spatial-head-s0（2026-10-05，POLICY_EXTRA 9 维 + C=8 空间塔：schema v4 落地与 S0′ 探针判决）

- **背景**：plan `plan/policy-spatial-head.plan.md`（v6 冻结）要求给走位/开火头加"看得见位置"的输入：
  腿 A = 独立 9 维方向直方图（`POLICY_EXTRA`），腿 B = C=8 空间塔（1×1 64→8 + ReLU + 4×4 分区均值 + FC 128→112）。……
- **决定（工程侧，全部落盘）**：
  ① **schema v4**：`OBS_SCHEMA_MAJOR` 3→4（观测流新增独立张量 ⇒ 旧代码读不了，MAJOR 是云端快拒门）；
     `POLICY_EXTRA(9)` dtype=float32（与 scalars 同族）；指纹新项双端共锚 **9bd651e3**（`common/schema.py` ↔ `src/nn/obs-encoder.ts`）。
  ② **语义**：dims 0–3 前/后/左/右威胁计数 `min(n,4)/4`（19px 命中带 + 遮挡，谓词复用 danger-metrics/helpers）；dims 4–7 前/后/左/右……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-05-policy-spatial-head-s0`

## §2026-10-06-goalnn-rollout-io-ceiling（2026-10-06，云机离线 rollout 第三次「整轮卡死十几分钟」：一局有了自己的墙钟上界，不可取消的阻塞不再把人质扣到整轮）

- **背景**：用户真机日志（Kaggle 离线轮 `x21-psh-a`）——§24 的有界回收/整轮重投/停滞点名三件套都在，
  熔断行之后仍是每 120s 一条「整轮停滞：… 还有 55 局在飞」、一局都不结算，十几分钟不动；
- **决定**：① 新增 `common.platform_utils.call_bounded(fn, timeout)`：真活跑在一条 **daemon** 线程里，
  调用线程只在预算内等，超界返回 `(False, None)` 且**绝不 join**（`concurrent.futures` 的线程不是
  daemon，解释器退出时会 join ⇒ 卡住的线程会把「进程退出」也一起按死，故不能用它兜这一层）；
  `fn` 的异常在**调用方线程**里原样重抛（调用点 `except` 语义不变）。② 新增……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-06-goalnn-rollout-io-ceiling`

## §2026-10-06-goalnn-round-end-scan-ceiling（2026-10-06，轮末扫盘也有上界：主线程上的全量盘 IO 不再让整轮对日志静默）

- **背景**：`§2026-10-06-goalnn-rollout-io-ceiling` 的同日收尾（当时 §32.4 ⑤ 就点名了下一个位置）。
  `verify_shards` / `collect_reports` / `collect_shard_manifests` 在**主线程**上做全量文件 IO……
- **决定**：① 新增 `common/game_watch.SCAN_CEILING_SEC = 60.0`（**单列的第三个数**：扫盘没有子进程/
  重试/写盘 ⇒ 不能沿 `game_ceiling_sec`，那个含尝试次数与回收预算，会大到让挂死的扫盘也过关；
  正常一轮扫盘亚秒级 ⇒ 60s 是「机器挂了」不是「盘慢」）+ `scan_ceiling_line()`；
  ② `worker/iter_rollout._scan_bounded` 把三步各套一层 `call_bounded`，超界 ⇒ `UnreapableChildError`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-06-goalnn-round-end-scan-ceiling`

## §2026-10-06-goalnn-pull-replica-any-unfinished（2026-10-06，用户报障：新 worker 上线「领不到」，要重启 trainer 才领得到；用户口径：只要没回传结果都算没完成，都能发出去竞速）

**背景**：Kaggle 新 worker 起来后 `no job yet` 刷了一两分钟，重启 trainer 才领到活。机制链：空闲 worker 的
候选只有可领取池（`hub/store_ledger.py::claimable_job_ids`，**排除活租约**）；而「活租约」由**独立心跳线程**……
**决定**：未完成就发得出去 —— `claimable_job_ids`（∧无活租约）与 `inflight_job_ids(not_held_by=…)`（∧有
活租约）成为同一份「未完成」判据的两个视图（`_unfinished_pending` 是唯一筛子）；`peek` 每课程先看池
（`mode="exclusive"`），池空则看在飞集并发**备份副本**（`mode="backup"`）；`acquire_job` 按候选自带的模式
认领。排序交给优先级表；认领模式由 **hub** 定（只有它知道有没有活租约）。备份副本**不打** `/start`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-06-goalnn-pull-replica-any-unfinished`

## §2026-10-06-goalnn-cloud-worker-log-diet（2026-10-06，用户指令「删除云机 worker 刷屏 log」）

**背景**：用户贴出云机（Kaggle）worker cell 输出里的六行，要求删掉：`job …: DataParallel 生效（…）`、
`[ppo] update start: … chunks x … epochs`、`bulk payload/P1: 让路合计 …`、……
**决定**：这六行**删除**（不降频、不加开关），每 job 的观测出口收敛到既有的一行 wire 账
（`wait=…/yield=…` 调度账 + 各段字节/秒 + `phases in/out/ppo/other`）；**异常与真异常路径各自仍然响亮**
（`result POST 409`（竞速输家）/ `403`（backup 丢弃）、`cuda-dp 但只可见 N 张卡` 的退化告警、
`排队 … 才拿到单通道`（>1s）、wire 的重抽/抢占/坏签行、状态码节流告警）。逐行落点：……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-06-goalnn-cloud-worker-log-diet`

## §2026-10-06-goalnn-test-console-isolation（2026-10-06，用户问「是不是你跑门禁触发的」⇒ 是；用户裁决「修」）

**背景**：提交 `0e8442ba` 的 pre-commit（nn python gate）跑完，用户发现 `bun run dashboard`（:8900）的
命令行刷出 **21 行** `[action] autoOfflineHandoff <课> → fail (HTTP 200): … 未开课 …`（10:32:17–10:32:42，……
**决定**：测试**永不**指向在跑的控制台——`tests/conftest.py` 与 `e2e/conftest.py` 各挂一个 autouse fixture，
把测试期 `BCITY_CONSOLE_URL` 钉到 `http://127.0.0.1:9`（死端口：连接当场被拒 ⇒ 漏网也只是「控制台不可达」
的响亮降级）；死桩常量只许有一份定义（`tests/conftest.py::TEST_CONSOLE_URL`，e2e 侧引用它）。守卫 = 4 条
用例（`tests/test_console_isolation.py`）：env 必须是死桩且 ≠ 生产默认 · 用捕获器把 `_net_urlopen` 换掉、……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-06-goalnn-test-console-isolation`

## §2026-10-06-goalnn-test-state-isolation（2026-10-06，用户令「扫其它测试 → 生产泄漏」）

**背景**：用户给完控制台隔离（上一节）后要求扫其余测试。直读法 = 跑门禁前后各拍一次真状态文件
指纹（`find tmp dashboard/data nn-training/weights -type f -printf '%T@ %s %p\n'`）再 diff：一次门禁……
**决定**：测试**永不**写生产状态——`tests/conftest.py` 立唯一的 `PRODUCTION_STATE_PINS`
（`NN_GATE_HALT` / `NN_GATE_HALT_APPLIED` / `NN_LOOP_CONTROL` / `NN_LOOP_CONTROL_APPLIED` /
`EVALBOARD_DATA` / `BCITY_WEIGHTS_ARCHIVE_ROOT` → 本用例 `tmp_path` 下的相对路径）与唯一实现
`pin_production_env(monkeypatch, root)`（含控制台死桩）；单测层与 e2e 层各挂一个 autouse fixture……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-06-goalnn-test-state-isolation`

## §2026-10-06-goalnn-replay-export-eval-round-picker（2026-10-06，用户令「导出 replay 支持任意 eval 轮」）

- **背景**：「导出 replay」只能导最新权重的轮。后端早就收 `iter` + `wver`（`POST evalReplays` →
  `worker/eval_replays_once.py`），卡点只在前端：弹窗以「此刻滤出视图」为数据源，导完的产物也按……
- **决定**：① 选择器数据源 = 轮级账本（`iters.ts::readEvalRoundOptions`，summary-only 单趟，
  谓词与 `readEvalGames` 同源，`reusedWver` 单独标注）；② **归因不变量：`POST evalReplays` 受理即
  `rmSync` 旧 manifest**（抛则整次 POST 500，不吞、不「删不掉也照常起 python」）⇒「盘上有 manifest」
  此后只可能是本次写的；③ 归因判据只看 `manifest.iter === 所选轮`，失败 manifest 透传 `failReason`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-06-goalnn-replay-export-eval-round-picker`

## §2026-10-06-psh-leg-a-verdict（2026-10-06，腿 A 终判：挂起 → "看见了但没转化"，K→5 立项 + 腿 B 可启动）

- **背景**：plan `plan/policy-spatial-head.plan.md` §5 口径，A-it150 对 A0-it150（`x21-psh-a.it150.20261006-093718.json` /
  `x21-psh-a0.it150.20261006-053532.json`），N=1600（800 @415000 + 800 @417000，同命令先后跑，配对 800/800 全中，……
- **决定（§5.4/§5.5 落判）**：杀敌配对差 D=**+0.23** 95%CI **[-0.26,+0.71]**（含 0，非直关）；通关 D=**+2.56pp**
  CI [-0.64,+5.77]（安全门过）；方向诊断 acc D=**+0.012** CI **[+0.0095,+0.0152]**（两段一致改善）⇒ **挂起**，
  二级归因第一支"看见了但没转化" ⇒ **立项 K→5，并且腿 B 也可以启动**（标注"感知有效、转化不足"，与 K→5 并行；
  腿 B 主判同时报杀敌和方向诊断，预注册可能同样读挂起，不算失败）。Edhit/enclR 不在终判行 schema 内，归因只凭 acc。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-leg-a-verdict`

## §2026-10-06-psh-leg-b-start（2026-10-06，腿 B/B0 开课：起点机制与 B0 直接复制的不对称）

- **背景**：腿 A 终判挂起→"看见了但没转化"，满足 §5.5"挂起 + 方向改善 ⇒ 腿 B 可启动"。课程
  `x21-psh-b/b0.jsonc`（paired_rotate_seed=20261012 全新流，已立）经控制台同源 API `openCourse` 开课，……
- **决定（起点机制）**：B = `init_spatial_leg.py --from 腿A-it150 --policy-extra --spatial-tower
  --seed 20261012` 转出（85345 参数，warmstart_missing=头2+塔4键；旧占位件已覆盖）。
  B0 = 腿 A 终点**直接复制**（plan 字面指令；sha256 逐字节相同见证，v4→v4 严格可载，70279 参数）。
- **否决与备选**："B0 也走脚本转出使双头同冷"（否决：脚本对同形头是装载非重初始化，达不到目的；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-leg-b-start`

## §2026-10-06-psh-leg-b-burn-abort（2026-10-06，B 在 it16 被配对熔断误杀：冷爬坡判成烧干，切 baseline 重开）

- **背景**：B 按计划冷启动（it0/it5/it10/it15 胜率全 0），`kickstart_burn` 缺省 auto → 唯一同 V 对端 B0（31%）
  ⇒ paired 模式"同 it 配对差连 3 点 < −5pp"在 it16 触发 ABORT（delta −31pp），`run_complete` 记"正常收官……
- **决定**：B/B0 课程文件加 `kickstart_burn: {mode: "baseline"}`（B：floor=own-it0−5pp，对零起点 ≡ 永不触发，
  可证明；B0：只防真塌〈连 3 点 < 30.2%〉，且防"后期 B 反超杀死对照组"。对照臂永不自杀，x20-clutch-null 前例）。
  **删块 ≠ 关**：块缺席回落 auto/paired，it20 必再杀一次——"删熔断续跑"不可行，baseline 留着就是关闭态。
  `paired_kill` 本就 opt-in 缺席（已关）；`ent_break` 0.25 / kl 缺省等过程守卫保留（冷腿熵最高，误伤不了；A 全程带着跑完）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-leg-b-burn-abort`

## §2026-10-06-psh-leg-b-verdict（2026-10-06，腿 B 终判：关闭；梯度到达，真·持平；不切换）

- **背景**：plan §5 口径，B-it150 对 B0-it150（`x21-psh-b.it150.20261006-155320.json` /
  `x21-psh-b0.it150.20261006-172154.json`），N=1600（800 @415000 + 800 @417000，配对 800/800 全中，……
- **决定（§5.4 + §5.6 落判）**：杀敌 D=**-0.01** CI **[-0.50,+0.48]**（含 0；段内 sd≈9.98 ≈ 预注册 9.88）；
  通关 D=**-2.25pp** CI **[-5.38,+0.88]**（下限 ≤ −5pp ⇒ 破线）⇒ **关闭**（有伤害证据，不走挂起流程）。
  方向诊断 acc D=**-0.006** CI 全负，无抢救。B≤B0 两岔已走：塔 it50→it150 相对变化 28–253%
  （S0-e 早验 it8 通梯度）⇒ 非工程失败 ⇒ ②真·持平。**不许写"塔没用"**；结论 = C=8/150iter 预算下无转化。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-leg-b-verdict`

## §2026-10-07-psh-poolouter（2026-10-07，池外 800 局确认 + 两课结课报告回写课程文件）

- **决定**：杀敌 D=**-0.11** CI [-0.80,+0.58]（含 0）+ 通关 D=**-1.12pp** CI [-5.51,+3.26]（破线）
  + acc≈0 ⇒ 与 it150 终判**同形关闭**（单段 MDE@800：0.98/6.4pp，口径一致）。关闭 verdict 维持，
  不切换，不加预算（延伸段已证伪欠训练）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-07-psh-poolouter`

## §2026-10-07-peak-crown（2026-10-07，四臂峰值同题排名：B0-it290 加冕，不改任何 verdict）

- **决定**：加冕 B0-it290（`x21-psh-b0.it290.20261007-004553.json`）为全纪元最强权重。
  三条如实声明：① 峰值自选 ⇒ 选择偏差（真优势小于测得值）；② B0 累计 ~290 轮 ≈ A 的两倍预算，
  "最强权重" ≠ "最优架构"（同预算架构 verdict 维持：A≃A0，B<B0）；③ 只改名分，不改任何 verdict。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-07-peak-crown`

## §2026-10-07-b0-vs-hu150（2026-10-07，B0-it290 配对挑战 hu150：双显著胜，加冕全场最强权重）

- **决定**：B0−hu150 杀敌 D=**+0.63** CI [+0.13,+1.12]、通关 D=**+3.75pp** CI [+0.48,+7.02]——**双显著**。
  加冕 B0-it290 为全场最强权重（终判段口径；hu150 同段实测 31.25%/11.09，低于其账本口径 32.50%/11.370，
  属段难度差，正常）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-07-b0-vs-hu150`

## §2026-10-07-k5-filing（2026-10-07，K→5 立案：起点 B0-it290 + K5/K10 同流配对；只写计划）

- **决定**：立 `plan/k5-rhythm.plan.md`（待批准）：两臂同起点（B0-it290 sha 见证）/同预算 150/同新流 20261013/
  同守卫（baseline+margin10），唯一差别 = K；判决沿用 §5 表；K 机制 audit（旋钮位置/半格对齐/obs 语义）
  先行，**只写计划，不改代码不开课**。峰值起点偏差由配对对照对称抵消；K10 臂兼任 B0 续跑验证。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-07-k5-filing`

## §2026-10-06-psh-extend300（2026-10-06，两臂续跑到 it300：斜率归零，"半山腰"假设被证伪）

- **背景**：it150 verdict 关闭后，"150 轮不够"的质疑成立验证条件（B it100→150 还在陡峭爬升）。……
- **决定**：延伸段证伪"欠训练" fork（+150 轮零斜率）⇒ **关闭 verdict 维持，不再加预算**；it300 配对终判
  不跑（30 个日常点一致，翻盘概率≈0，省配额）。塔分支结案：C=8 在 150–300 轮预算下无转化。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-extend300`

## §2026-10-07-k5-decision-k-channel（2026-10-07，K→5 案 v2 评审修订 + 决策周期 K 通道全链落地 + 两课备好）

- **背景**：`plan/k5-rhythm.plan.md` 评审（2026-10-07）发现 2 P0 + 3 P1：守卫口径三处不一（表 margin10 /
  注释 margin_pp 100 / filing"margin10"）；评估 K 未预注册且全链无通道（K5 臂会被 OOD 按 10 拍评估）；……
- **口径修订（覆盖 `§2026-10-07-k5-filing` 原文"margin10"，全案以 plan v2 为准）**：守卫统一
  `{mode: baseline, margin_pp: 100}`（等效关，用户"熔断全关"指令；floor=基线−100pp 永不触发）；K5 臂
  `est 294`（=1470 ticks ÷ K=5）；评估 K **按各自臂**（K5@5、K10@10；终判显式 `--decision-k 5`）；
  结论措辞 = "节奏**总效应**"（两臂同带 9 维 ⇒ 测不到"列收益被兑现"交互；判该交互的唯一后继 = K5 下重做 A/A0，另案）；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-07-k5-decision-k-channel`

## §2026-10-06-goalnn-evala-serial-queue（2026-10-06，用户报障：「evalA 在某 it 执行过后，其它 it 再点按键无反应，需要刷新页面」）

- **决定**：排队住唯一启动点 `server/eval-a-run.ts`：忙时返回「已排队（前面 N 个）」、子进程退出自动
  接棒；`eval:A` 从第一单持有到**队列排空**（补评/导入侧看到的「忙」语义不变）；同 (课, iter, baseline)
  去重；上限 8（满 ⇒ 响亮 409）；路由 busy/full → 409、spawn 类失败 → 500。按钮侧不改判据……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-06-goalnn-evala-serial-queue`

## §2026-10-06-goalnn-offline-leg-sampling-exclusion（2026-10-06，用户指令：offline worker 完成的轮算节点 PPO 贡献、不算 rollout/eval 贡献）

- **背景**：云机离线腿（`nn-training/remote/offline_eval.py` + `hub/store_offline.py`）替本机跑掉一段
  it 的 rollout/eval；控制台「采样贡献」照本机 meta 账本算，把这批轮记在本机节点头上。
- **口径（用户选定）**：**剔除离线腿整段**——例 `x21-psh-a` it9..87。
- **决定**：腿 = 课程账本里 `offline_artifact` 事件带的 it 集合（`pool-history.readOfflineLegs`）；
  剔除发生在**计数入口** `ingestLine`（日桶/课维度只有「天」粒度，事后扣减无 it 可依），行在……
- **为什么不能只剔「云机写的行」**：云腿的 meta 行**根本不写进本仓** `dist-agent-meta.jsonl`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-06-goalnn-offline-leg-sampling-exclusion`

## §2026-10-06-goalnn-eval-cloud-time-utc（2026-10-06，用户报障：offline 回传的 eval 时间是 UTC）

- **背景**：`worker/eval_track.py` 写 summary `time` 用裸 `strftime("%Y-%m-%d %H:%M:%S")`
  （写者本机钟）；云机 = UTC ⇒ 控制台把 `x21-psh-a` it10 显示成 `2026-10-05 21:39:04`，而同轮……
- **决定**：**读侧**转换（历史数据必须修正）：`server/iters.ts` 的 `readEvalSummaries` /
  `readEvalGames` 把含 cloud 的 `time` 按 UTC 解析后按本机时区写回**同格式**（本条落账时该函数名
  `readLatestEvalGames`，同日「按评估轮选」那条已泛化成 `readEvalGames(trajDir, iter?)`）；无 `nodes`
  的老行、非该格式的串一律原样透传（不猜）。写侧（新行带 `+00:00`）留作后续，不在本次。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-06-goalnn-eval-cloud-time-utc`

## §2026-10-07-goalnn-offline-deliver-isolation（2026-10-07，补传腿忙等修复 + 独立进程：落地 plan/offline-deliver-isolation 的 P0+P1）

- **背景**：云机 `x21-psh-b` 530 起 PPO 单步 `0.186 → 5.26 → 7.40 s/step`（墙钟 ≈**40×** 于
  编译+追踪+拷贝之和 ⇒ 训练线程被抢 CPU/GIL，隧道恢复即自愈）。诊断 `plan/tpu-cpu-silent-downgrade.plan.md` §9/§10；……
- **根因（代码级）**：`_repost` 是唯一「只唤醒、不消费」的标志 —— `_drain_loop` 认它当唤醒源，`_sync`
  探活失败又放回 ⇒ 内层 `while` 立刻退出、**不 wait 也不干活** = 100% 核自旋（实测 0.6s 空转 **50085 圈**）；……
- **判据（新不变量）**：`_idle_spins` 只在「这一圈既没干活、也没 `wait`、也没收线」时 +1 ⇒ 修好后**该分支
  不可达** ⇒ 断言 `== 0` 是确定性的（无窗口/容差/sleep）；`_drain_ticks` 只作埋点（它每 0.5s 超时唤醒仍涨）。……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-07-goalnn-offline-deliver-isolation`

## §2026-10-07-rollout-stage-balance（2026-10-07，分关采样平衡：est 上界初批 + 有界多批补差；不改身份，事件可见化）

- **背景**：动态采集的**全离线腿**（`kind=run`）一轮只跑一批，局数按**全局** est 反解 ⇒ 每关同一个局数，
  而达标线是**分关** quota。x21-psh-b it168 真机实测：缺 4405 样本、同时丢 3333 样本（关间 samples/局 差 1.75×）。……
- **决定（两条链共用一套数学）**：① 首批按**分关** `est_hi = ceil(est_s × 1.15)` 反解（取上界 ⇒ 欠采小）；
  ② 节点侧**有界多批补差**（≤3 补差批 ⇒ ≤4 次 `run_iter_rollout`；每批用**实测均值**重估，末批 `×0.85`
  偏保守——缺口不可恢复、浪费可恢复）；③ 分关局数硬顶 `4 × ceil(quota/est_s)`（旧的**全局** est 口径会让
  低 est 关「配额未满就触顶」，而 DoD 把触顶当合格路径 ⇒ 静默短采）；④ 缺口是**硬指标**、浪费是**软指标**：……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-07-rollout-stage-balance`

## §2026-10-07-goalnn-serve-pool-readiness（2026-10-07，长驻池「假就绪」+ 重试路径裸文件 IO：真就绪判据 + 有界清理；清理超界改判「交整轮」）

- **背景**：Kaggle 离线课 `x21-psh-b` it253（job `031e17e61777d09c`）一轮 **355s**（正常 32s）：
  `94/94 就绪` 是假满（真含义 = 94 个进程没退出）⇒ 假就绪 worker 被当暖的发出去 ⇒ 5s/20s 超时回退一次性 ⇒……
- **决定（P0-1）**：判据唯一化 = `w.ready.is_set() and not w.dead`（`_acquire` **只发放**真就绪的）；
  冷启动**分批**（`SPAWN_BATCH_SIZE=16`，批间串行、deadline 整池共用）；**未就绪不杀**（采纳评审备选 (h)）——
  留在池里标 booting（`unready` 计数），`ready` 一亮点就自动可发放（杀掉 = 这批冷启动白付 + 补位再付一次）；
  零真就绪但有一批在冷启动 ⇒ **不退池**，只有 `unready == 0`（一个都没起出来）才关池走一次性。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-07-goalnn-serve-pool-readiness`

## §2026-10-07-goalnn-offline-graceful-exit（2026-10-07，自主 worker 收工即停：终态短窗口 + 保活收工即停）

- **背景**：Kaggle 自主 worker 现场——两门课 17:03 跑完、17:04 hub 已报 `completed` + 「本会话已跑过这份包」，
  却**空转到 17:33**（29 分钟）才收工；收工后 `[keepalive] alive` 一直报到 17:49。两条根因**性质不同**：……
- **决定**：① 新增 `TERMINAL_STATES={completed,not_offline}` + 纯函数 `all_terminal(rows, served)`；
  **判据并上「本会话已跑过这份包（同 sha）」**——现场那门是 `claimable` 行、只被 `_eligible` 的 served 过滤
  （`state=ready`），**只判 state 是终态修不掉现场**；为此 `resolve_courses` 增加第三返回值 `manifest`……
- **诚实边界（写进文档，别当已证）**：保活是 daemon 线程 ⇒ 停它**不影响 kernel/TPU**（危害只是日志噪音 + 线程泄漏）；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-07-goalnn-offline-graceful-exit`

## §2026-10-07-pool-history-large-stream-rescan（2026-10-07，池历史大流：文件大只决定「怎么读」，不决定「要不要重读」）

- **背景**：用户报「没开训练时控制台空置，WS 在 280–856MB 锯齿」。原 plan 把根因定为「45 个大流每 5s/15s
  各重读一遍尾部 ≈ 每拍 5 万次 `JSON.parse`」。**评审（同日）先用 memo 判据 + 既有绿用例证伪了它**：……
- **决定**：改重建判据 —— 拆出 `grewOrChanged`，大流条件改成 `src.size > LARGE_META_BYTES && grewOrChanged`
  （阈值分支保留 ⇒ truncated 读法与口径一字不动）。真代价 = 「只要本循环被进入（任一流变动 / 首见 / 腿指纹
  变化），所有 >2MiB 的流**无论自己变没变**都重读尾部」⇒ 一个流追加连坐 12 个大流。顺带：`readChunkLines`
  逐行重建尾串（O(n²)，1MiB≈2GB 临时字符串）改 `start` 游标 + 块末归位一次（O(n)）；新增只读端点……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-07-pool-history-large-stream-rescan`

## §2026-10-07-logtail-bounded-read（2026-10-07，`logTail` 改尾部窗口读：空置 WS 280↔856MB 的真凶）

- **背景**：上一条把池历史排除（`aggMemo` 命中 ⇒ 空置零重算）后，峰值仍未定位。这次**先测量**：
  临时探头（`dashboard/tmp/probe-idle-cost.ts`，只读）逐路径测「耗时 + **未 GC 的 heapΔ** + rssΔ」……
- **根因**：`componentViews`（`views.ts:111`）对**每个**组件调 `logTail`，而 `logTail` 是
  `readFileSync(整个文件)` + 逐行 `new TextDecoder`，最后只取尾 5 行；本机 selfNode（sampler-agent.log）
- **决定**：两条都改「有界读」——`logTail` 尾部窗口（`LOG_TAIL_BYTES = 256KB`，文件 ≤ 窗口时与旧实现
  逐字相同）；`totalLines` 分块计数（`countLines`，内存恒定 1MiB，与旧全文件版逐字节等价）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-07-logtail-bounded-read`

## §2026-10-07-offline-busy-gate-anchor（2026-10-07，离线「一拖一」闸两次自锁：锚点换 episode 起点 + 只算池内的课）

- **背景**：用户报「离线 worker 领不到 `x21-psh-k10`，设成离线/在线都没有用」。现场读数：k10 是
  `ready` + 有包（4.8MB）+ 无主，唯一拒因 = `busy: x21-psh-k5 正在交接（导包中）`（`_busy_locked`……
- **决定**：三处，全是换判据、不加机制。① `_busy_locked` 的交接窗口锚从 `claimed_at` 换成**本次
  交接的起点 `flipped_at`**（`claimed_at` 只作缺键兜底）——`flipped_at` 只在首翻时写、重复 claim
  不刷新、切在线才清 ⇒ 一次交接最多占闸 `AUTO_HANDOFF_PENDING_SEC`；两个锚点时间轴仍分叉
  （`stall_verdict` 的 `pending-export` 照旧锚 `flipped_at`）。② 两条腿都改成「只算……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-07-offline-busy-gate-anchor`

## §2026-10-08-crown-confirm（2026-10-08，N=3200 加冕赛：KR10-it150 双显著胜 B0-it290，王位易主）

- **决定**：杀敌 D=**+0.48** CI [+0.14,+0.82]、通关 D=**+2.53pp** CI [+0.20,+4.87]——**双显著**，
  四段方向一致（+0.36/+0.54/+0.45/+0.56）。**王位易主**：KR10-it150
  （`x22-kr10.it150.20261008-140718.json`）为全场最强权重；且它是注册端点（非峰值自选），……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-08-crown-confirm`

## §2026-10-08-kr-verdict（2026-10-08，K-retune 终判：直关且死得更透；K5 训练侧永久关闭）

- **决定**：杀敌 D=**-3.87** CI [-4.34,-3.40]（上限 < 0 ⇒ **直关**，比旧 K5 腿的 -1.99 死得更透）；
  通关 D=**-20.75pp**（破安全门）；acc D=**+0.029** 全正（瞄准又好了，第三次"看得见打不着"）。
  retune（配额/gamma/lam）救不回 ⇒ 死因不在这三处，剩 ent/结构二选一；**K5 训练侧永久关闭**，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-08-kr-verdict`

## §2026-10-08-k5-verdict（2026-10-08，K5 终判：直关；稀释假说死，关节奏分支）

- **背景**：`plan/k5-rhythm.plan.md`，K5-it150 对 K10-it150（同起点 B0-it290、同流 20261013、同预算 150，
  熔断全关 + 人盯盘），N=1600（800 @415000 + 800 @417000，同种子配对全中；K5 侧 `--decision-k 5`）。……
- **决定**：杀敌 D=**-1.99** CI [-2.47,-1.50]（上限 < 0 ⇒ **直关**，显著变差覆盖一切）；通关 D=**-10.1pp**
  CI [-13.1,-7.1]（破安全门）；acc D=**+0.015** 全正（瞄准更好，杀敌照跌）⇒ **稀释假说死**：
  感知没问题，转化更差。关节奏分支。候选解释（未验证，另案）：半格对齐抖动 / 同超参下时域翻倍致
  value 崩（it24 连败是症状）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-08-k5-verdict`

## §2026-10-08-diag-filing（2026-10-08，诊断重仿案立案：被包围/火线/背刺/远距四维；只写计划）

- **背景**：K10@K5 深拆要四维，终判行无逐 tick 记录、export 链无 decision-k。立
  `plan/diag-lane-instrument.plan.md`（待批准）：同权重同种子重仿 + 只读探针；口径冻结在先
  （enclR/laneR/backR/Edhit 定义 + 分母 + K wire）；先量速定 N（800 或预注册 200 子集）；
  K10 重仿与终判行逐种子全字段对账，不一致即停；探索性，永不进 verdict。
- **落点**：计划 → `plan/diag-lane-instrument.plan.md`。
## §2026-10-08-diag-verdict（2026-10-08，诊断重仿结论：K5 执行赢在"不被包+侧面磨"）

- **决定**：被包围 −2.0pp ✅、火线 −1.6pp ✅、背刺占比 −3.2pp ✅、侧击 +3.7pp ✅、远距 −0.05 含 0、
  danger/near4 持平、自洽锚杀敌 +1.09 ✅。画像：细执行不靠刺杀/射程，靠"少被包 + 侧面磨 +
  活得久"（与局长+269/承伤持平/拾取更多互锁）。⚠ threat≡lane 逐字相等，疑同探针双写，待 audit。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-08-diag-verdict`

## §2026-10-08-goalnn-dispatch-model（2026-10-08，派发模型重构收口：课程无模式 + 接管（hold）唯一真源 + BC 独占；plan/worker-type-dispatch-model M1–M6）

- **背景**：旧模型把「在线/离线」当**课程的一等概念**（`_modes` + pin + `authority_of` 五档 +
  `parked` 派发闸 + 离线租约），横跨 hub / 控制台 / trainer / 云机四层，三个后果已经出过现场：……
- **决定（用户口径 D1–D4）**：**课程无模式**，派发只看两件事——**这门课有没有被接管（hold）**
  与 **这份活归谁（job 自己的 role）**；**活性只认进度信号**（D1：心跳不算）；接管期间队列任务
  **压下不撤单**（D2：掉线后即可被协作盘领取）；**一拖一放 hub、按 worker**（D3：清单 `?worker=`
  才是同参可领，缺省是**上界**）；**BC = kind=bc 队列作业**（D4）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-08-goalnn-dispatch-model`

## §2026-10-08-goalnn-rollout-local-scratch（2026-10-08，rollout/eval 热路径 IO 落节点本地盘：共用一套 scratch，回落即今天的行为；立案 plan/rollout-local-scratch）

- **背景**：云机 rollout 一轮 85 个 bun **同时**把 TS 运行时树（读）· 每局 shard（写）· 权重（读）
  压在 `/kaggle/working`（网络挂载点）上；本机同样 85 并发从不生病（它写本地盘 `tmp/`）……
- **备选与否决**：① 只写 `/dev/shm`（用户原话的「内存」）——否（Docker 默认 64MB，一轮需 170–280MB），
  降为候选链第 2 档；② 整轮跑完再一次性搬回——否（峰值占满本地盘 + 盘坏要等 85 局跑完才知道）⇒ 流式；……
- **决定**：新 `common/scratch.py`（L0 叶子层）提供落点链（`NN_ROLLOUT_SCRATCH` > `/dev/shm` > `/tmp` >
  **`None`**；容量**与速度**两闸）+ 作业私有布局（`r/`、`eval/`）+ 内容寻址的共用 TS 家……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-08-goalnn-rollout-local-scratch`

## §2026-10-08-goalnn-eval-baseline-undispatched（2026-10-08，it0 基线 eval「从未派出」缺口：收工自报原因 + 缺口三分 + 基线自带本机门 + 大缺口重派；立案 plan/eval-baseline-undispatched）

- **背景**：`tmp/x21-psh-b0` 结课盘点：it0 派 200 局只落 105，剩 95 局**从未离开 `pending`**。收工循环只看
  `live_workers`（队列还剩 95、窗口还剩 1479s 也照样 `break`），收工行不报「哪条路径收的工、队列剩多少」；……
- **决定**：① 收工自报 `close_reason ∈ settled/window/workers-gone`（单一实现 `worker/eval_yield.eval_close_reason`；
  `workers-gone` 只在**窗口还有余量**时成立 —— 否则窗口到期永远报不出来）+ `left_pending/inflight/spawned/窗口余`；
  ② `settle_eval_summary` 新增 `never_dispatched`/`lost`/`carried`/`left_pending`/`close_reason` 五个**纯新增**键
  （`dropped` 数值一字未动）：`never_dispatched` 只数 `attempts==0` 的局（重投回队归 `lost`）、`lost` 从第一项推……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-08-goalnn-eval-baseline-undispatched`

## §2026-10-08-goalnn-eval-task-key-coursefp（2026-10-08，bugfix：eval 腿任务缺课程血缘 ⇒ 兄弟课程评估局在节点 resultCache 互串）

- **背景**：x23-cm3 it50 (2001,860141) 缺 1 局，meta `wver mismatch`，回传权重实为 x23-cm2 的 it50
  —— 两课同进程（forensics pid 6852）锁步评估（皆 18:39:50 起），cm2 的同局 18:40:19 在 node mac 落账，……
- **根因**：多课程 serve 同进程共享 `RUN_ID` ⇒ eval iterId 恒同（`{RUN_ID}.{it}ev`）；
  `sampler-agent::taskKey` 里 **wver 不进键**，唯一能分离课程的分量是 `course_fp`——而 eval 腿……
- **决定**：`EvalDispatcher.run` 计算一次 `course_fp_for_args(args)` 并透传 `fetch_task(..., course_fp=...)`；
  summary 复用同一读数（消除两处各算）。非课程路径 `course_fp=""` ⇒ 键与历史逐字节一致。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-08-goalnn-eval-task-key-coursefp`

## §2026-10-09-goalnn-x23cm-verdict（2026-10-09，x23 commitment 三整形臂终判：it150 全否决）

- **背景**：cm0 对照（11 参数原样，兼 K10 production 续跑）vs cm1 `+wFireHold 2.0` /
  cm2 `+wStop 2.0` / cm3 `−wStuck 5.0`；同起点 KR10-it150（sha `a683e5da…`）/……
- **主读数**：it150 日常 400 局四臂同种子全配对（it0 四臂同 0.44/200 局）：
  cm0 0.280 / cm1 0.2975（+1.75pp，McNemar χ²cc 0.22，ns）/……
- **方向诊断**（it150 配对）：fireHeld Δ−0.27pp（反向）/ stop Δ+0.2pp（≈6 tick/局，行为学零）/……
- **决定**：三整形臂全否决，归档不续跑；cm0 对照线继续 production 续跑。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-09-goalnn-x23cm-verdict`

## §2026-10-09-goalnn-kr10-dethrones-hu150（2026-10-09，KR10-it150 正面对决掀翻 hu150，易主）

- **背景**：hu150 的冠军是对 q90 拿的（§79 加冕三最优，从未和 KR10-it150 交手）；
  冻结评估里 KR10 在同一 414000 段数字更高（0.3463 vs §79 的 0.3250），遂补打从没打过的正面对决。
- **主读数**（ladder-c20-lives1，414000~414399，1600 对同种子配对，全 settled、dropped 0）：……
- **决定**：冠军易主，**KR10-it150 为现役最优**；后续 production/终判基线改挂 KR10-it150，
  引用 hu150 为基线的结论一律重读（§79 的 q90 系内排名不受影响）。
  订正（同日）：王位 10-08 已对 B0 易主（`x22-kr10.jsonc` 尾注），本场系对 hu150 的确认战。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-09-goalnn-kr10-dethrones-hu150`

## §2026-10-10-goalnn-fore-crown（2026-10-10，fore-it335 拼池加冕 + stack 确认）

- **背景**：fore 七峰冻结六胜一平（414400/414600/416000 三段）；stack-it125 单块 +9.25pp。
  按 cm3 教训（单批名义显著不可信），it335 与 stack-it125 各欠一次复测拼池——补打 416200 段。
- **主读数**（拼池 1600 对，同种子配对）：fore-it335 0.4875 vs KR10 0.3531（+13.44pp，
  χ²cc 58.34），kills ~13.7–14.0；stack-it125 0.4344（+8.12pp，p≈0.000003）；……
- **决定**：**fore-it335 加冕现役最优**；stack-it125 确认有效（叠加成立）；后继基线改挂
  fore-it335；x24-face/move/fore 单腿 verdicts 另行结算（本条只管加冕）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-10-goalnn-fore-crown`

## §2026-10-09-goalnn-compare-trends-modal（2026-10-09，首页趋势图加「比较课程」：跨课程端点独立于 /api/state）

- **背景**：用户要的是「首页趋势图右上角一个比较入口 → 一张大图叠 N 门课的同一指标；指标/口径/起止可切、
  课程可增删（含历史课）、打开期间自动刷新」。而 `/api/state` 的 `metrics` 只算**当前查看课程**一门课；……
- **决定**：新增 `GET /api/compareTrends`（`?courses=a,b&metric=&source=&from=&to=`；≤8 门、逐个
  `sanitizeViewCourse` 剔除非法/不存在、剔空 ⇒ 400 而不静默回退）；课程级缓存 TTL 10s + 账本
  `mtimeMs:size` 指纹（命中不读日志正文）；弹窗**只在打开时**轮询、节奏复用 Hero 透传的 `refreshSec`
  （= app.tsx 顶栏旋钮），并带请求序号 + 「请求身份 ∩ 账本指纹」门闩；多课口径 = 课色实线（rollout）/……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-09-goalnn-compare-trends-modal`

## §2026-10-09-goalnn-ppo-live-rows（2026-10-09，首页 PPO 区加「每台一行」：谁在算哪一轮 / 下一轮下好了没）

- **背景**：首页 PPO 区只有一行历史份额；「此刻在干什么」的三个事实里，hub 有租约但不给 `course`/`it`，
  预取状态只住 worker 本地盘、**没有任何上行通道**；自主盘领整段任务包、不进 PPO 归属账本。
- **决定**：worker 每轮末经 `POST /admin/worker-prefetch` **只报 jid**（形状未变不发 / 失败静默退避 12 轮 /
  绝不重试）；hub 解析 `course`/`it` 并在 `/admin/queue` 的 `worker_prefetch` 出读面（TTL 60s；**纯观测**，
  不进任何派发判据）；inflight 行补 `course`/`it`，**段归属按 `computing_ago` 分档**（已认领 ≠ 计算中）；
  live 行独立于 `contributionBrief`（`ConsoleStateView.ppoWorkerLive`）；自主盘轮次取「已补传产物」派生口径。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-09-goalnn-ppo-live-rows`

## §2026-10-09-goalnn-course-workers-removal（2026-10-09，`workers` 从课程文件退场：机器级键；kind=iter 的 wire 补一档「0 = 节点自定」）

- **背景**：用户 2026-10-09「课程里的 `workers: 8` 不合理——课程并不知道它将会在哪个环境做 rollout」。
  代码事实：`curricula/*.jsonc` 167/167 门课都写 `workers`（164×8、3×2，复制粘贴常量）；远端 kind=iter……
- **决定**：`workers` 判定为**机器级**键 ⇒ 课程侧读面删除（`flat_overrides` / `RESTART_ONLY_FIELDS` /
  ladder 模板），字段与存量键永久保留，死键由**开课侧**一行告警点名；本机配额与离线段口径不动。
  wire 侧 `manifest.rollout.workers` 补一档 **显式 `0` = 节点自定（auto）**（缺席仍 = 1；负值仍拒收），
  节点按 `cpu_worker_slots()` 定档（96 核 ⇒ 94）并出「并发定档」行；发布侧不再回退 `args.workers`，……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-09-goalnn-course-workers-removal`

## §2026-10-09-goalnn-self-node-disk-alert（2026-10-09，self 节点低盘从「静默」变「响亮」：预警档 4096 + 告警坞条目 + 徽标；**拒收地板 2048 冻结**）

- **背景**：2026-10-08 self 节点 1h41m（08:13–09:55）不参与 rollout/eval——D: 可用空间破 2048MB
  硬地板 ⇒ 每个作业回 503 ⇒ trainer 连 9 次瞬时失败停派；全程 `node self: offline` 0 次、控制台零告警、……
- **决定**：agent 单源两层阈值——**预警 `DISK_WARN_MB=4096`（纯观测）** + **拒收地板
  `DISK_FLOOR_MB=2048`（冻结；准入判据抽 `decideTaskAdmission()` 纯函数）**，回差 512；五字段
  （`diskFreeMB/diskLevel/diskWarnMB/diskFloorMB/diskLevelSince`）走既有 `/v1/ping`（零新增探测，
  顺带覆盖全机群）与 `/v1/status`；dashboard 搬运到 `selfDisk`，告警坞新一类条目……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-09-goalnn-self-node-disk-alert`

## §2026-10-09-goalnn-test-debt-cleanup（2026-10-09，nn-training 测试债清理：**口径先于数字** —— 两个可执行计数口径 + 三条防回流护栏；T1 删除面收窄 6 条、T2 拆 a/b、T5 撤掉「≥40%」）

- **背景**：`plan/nn-training-test-debt-cleanup.plan.md`（同日拍板）要清掉「不再守卫任何现行契约」的用例。
  执行前评审（`plan/nn-training-test-debt-cleanup.review-bf.md`，F1–F8）实测出三处**会删掉活守卫**的判据：……
- **决定**：① **口径代码化**：`tests/helpers/text_asserts.py`（放在既有 `source_scan.py` 旁，不另开第三套读盘逻辑）
  —— 口径 A（形态：`"字面量" in <文本>`，含 capsys）与口径 B（+「被读的必须是 `.py` 源码」门）；实测 A=1488、B=1084 条；
  ② **T1 收窄**：只删 6 条账本门端到端，三文件的逻辑守卫与三个工具本体全留；`docs/nn/experiments.md` 三处指针同 commit 修；
  ③ **T2 拆 a/b**：扫描型进单点 `tests/retired_contracts.py`（清单 + 唯一 `scan()` + **扫描面基线 90 文件**），……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-09-goalnn-test-debt-cleanup`

## §2026-10-10-goalnn-pytest-top16-round2（2026-10-10，pytest 最慢 16 条第二轮：控制面第三条 HTTP 缝 + 三处全仓扫描的口径内提速；全量 sum_min −16%、墙钟中位 −24%）

- **背景**：用户指令「pytest 耗时又来到了接近一分钟…找出耗时最长的 16 个测试，尝试优化；`--forkdist 8` 已调过很多次、不要再试」。
  按 §43 的手法量准（forkdist 8 不动；4 连跑取 min；本轮新增**纯净 worktree 基线**与 `cProfile` 定位）后，16 条里……
- **决定**：① 假 hub 的桩补齐**第三条**控制面缝（`report_prefetch`）：`tests/helpers/hub_seams.stub_round_http` + 4 处显式补桩
  —— 此后**新加控制面请求必须同步进该助手**（否则一批用例各白等 ~1.4s/轮；本轮这类共 14 条 / ~22s）；
  ② `text_asserts` 口径 A/B 共用**一次索引**、`_producer_funcs` 单遍、绑定预计算、`.py` 面字面量预筛（判据逐条不变）；
  ③ `remote_dag.graph()` / `remote_modules()` 进程内缓存，**键 = 三个包目录的当前取值**（合成用例 monkeypatch 目录后各算一份），返回值给副本；……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-10-goalnn-pytest-top16-round2`

## §2026-10-10-goalnn-deliverproc-final-flake（2026-10-10，补传腿重启的满载 flake：判据对齐「可判定」的证据 + 账本优先）

- **背景**：慢层 `tests/remote/test_offline_deliver_proc.py::test_restart_does_not_replay_consumed_commands`
  满载约 1/6 红（`result POST 出现 2 次`），单跑/空载必绿；同一用例 §74 记过另一种显形（自锁挂死）。……
- **决定**：① 慢层用例的**杀点同步到磁盘证据**（账本记全 3 轮 + `result_done`；不是 hub 那边的计数），
  此后断言「result POST 恰好 1 次 · `final` 行只有 1 行 · 已投轮次各恰好 1 次」，重启后再摆第 4 轮
  断言**新积压照推**；② 窗口那条路（`result_done` 钉成 False ⇒ 必须重述 + 有界返回）留在快层
  §74 的 `test_restart_restating_the_final_does_not_self_lock`；③ `_result_done_from_disk()` 改
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-deliverproc-final-flake`

## §2026-10-10-goalnn-rollout-node-auto-register（2026-10-10，采样节点自注册：hub 成为 `rl-config.json` 第二写者）

- **背景**：采样节点（云机 rollout / eval）原来靠人工把 ★URL + ★authKey 抄进本机 `rl-config.json` 的
  `nodes[]`；quick tunnel 每次重连换名，没同步就是整天 HTTP 530。用户指令：云机自报自注册。
- **决定**：`POST /admin/nodes/register|unregister` + `GET /admin/nodes`（Bearer = `HUB_TOKEN`）由 hub
  upsert `rl-config.json` 的 `nodes[]`：只动这一项 + 原子替换 + **无变化不写盘**；新条目打
  `managed:true`/`enabled:true`；**`enabled` 归控制台** —— 既有条目只在「上轮干净收工」（带
  `unregistered_at`）时恢复，否则原样保留；`concurrency` 只在**新条目**且 body 给值时才写、且永不改写……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-rollout-node-auto-register`

## §2026-10-10-goalnn-metrics-v11（2026-10-10，metrics v11：热线族 **2 列** idx74–75 / dim 74→76 / 事件加 `bulletOwnerId`；plan/metrics-v11-hotlane.plan.md）

- **决定（列布局）**：尾部追加 2 列 —— `nearSqSum`(74)（每拍对每个**存活已激活**敌车按切比雪夫
  格距 d 累加 `(3−d)²`，**有效半径 2 格**）· `postHitLaneTicks`(75)（被击中后仍留在**该源火线轴**上
  的 tick 累计）⇒ `METRICS_DIM 74→76`、`METRICS_VERSION 10→11`。0–73 列号/语义一字不动；
  **公式一个字不动**（reward golden 64/64 逐位不变，只有 version 与向量宽度变）；**零训练腿**。
- **决定（口径）**：① 热线轴用**整数格轴**（开窗瞬间记录源中心格 col/row + 朝向轴），**不**与
  `threatLaneSources` 的 19px 连续带共用谓词（带边缘抖动会高频误关窗）；**关窗四条** = 玩家脱离该轴 /……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-10-goalnn-metrics-v11`

## §2026-10-10-goalnn-v11-criterion1-split（2026-10-10，v11 两列判据 1 分流：nearSq 可定价，hotLane 维持诊断）

- **背景**：v11 签入时判据 1（人类 vs NN 分离度）全空，两列暂定“可入库但不得定价”。……
- **决定**：nearSqSum 判据 1 通过，定价解禁（首用 `x24-stack4`，替换 near4，B6 条件替换执行）；
  postHitLaneTicks 维持诊断列，不定价（v11 commit 限制维持）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-10-goalnn-v11-criterion1-split`

## §2026-10-10-goalnn-cluster-code-snapshot（2026-10-10，`code.zip` 打包时机从「每课首次 publish」上移到「集群会话启动」，锚 = 首个声明进程的存活期；用户指令）

- **背景**：同一 hub 上多课程各自打包 ⇒ 会话内改本机代码后开新课，共享 worker 池交替领到的 job 跑在
  **两份代码**上（shard 行宽 / METRICS_VERSION / 奖励语义都可能不同）。四个打包点全是 per-course：……
- **决定**：新增 `remote/code_snapshot.py`；快照落 `<repo>/tmp/.code-snapshot/`，**内容寻址**
  （`code.<sha12>.zip` + 最后原子替换的 `snapshot.json`）；启动期 `ensure`（`hub/boot.py` ·
  `run_rl_cluster.py --serve` · `run_rl.py`——**导出进程就是 run_rl 进程** ⇒ 集群全停时导出会重打并成为
  新锚）；消费期**只 `read`、永不换代**（PPO/BC publish 走 `published_code_zip()`，缺快照 ⇒ 回落……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-cluster-code-snapshot`

## §2026-10-10-goalnn-cluster-code-snapshot-ts（2026-10-10，`ts_code.zip` 并入集群代码快照——P1，承接 §2026-10-10-goalnn-cluster-code-snapshot）

- **背景**：`code.zip` 冻结后，第四个打包点 `trainer/loop_export.py::_ensure_ts_code` 仍是同构的
  per-course 缺陷——TS 运行时 zip 落 `<job_root>/ts_code.zip` + 进程内缓存，而 `job_root` 是每课一份……
- **决定**：快照**一次打包产出两件**（`code.<sha12>.zip` + `ts_code.<sha12>.zip`，元数据加 `ts_zip` /
  `ts_sha256` / `ts_bytes`）；新消费口 `published_ts_code_zip()` 与 `published_code_zip()` **逐字同规**
  （快照优先 / 缺失回落 per-course 打包 + 响亮 WARN / **绝不建锚**）；`_ensure_ts_code` 改调它，延迟
  import 面从 `remote.hub_client` 换成 `remote.code_snapshot`（`test_loop_export_split.py::DELAYED_IMPORTS`……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-cluster-code-snapshot-ts`

## §2026-10-10-goalnn-s4-crown（2026-10-10，s4-it180 三腿拼池居首，加冕）

- **背景**：x24 三腿最优（s2-it235 / s3-it135 / s4-it180）拼池复测（416800+417200 系），
  KR10-it150 同批配对。
- **主读数**（拼池 1600 对）：s4-it180 0.4925（+11.75pp，χ²cc 43.82）/ s2-it235 0.4825
  （+10.75pp）/ s3-it135 0.4750（+10.00pp）；kills ~13.7–14.2。三腿新段单独看亦全胜。
- **备选与否决**：① 与 fore-it335（拼池 0.4875，414000 系）比点数——否（不在同一批种子，
  不直接比；用户裁定 s4-it180 为新王，后续正面对决再议）；② 只认单块——否（拼池是加冕必要条件）。
- **决定**：**s4-it180 加冕现役最优**；后继基线改挂；人机对照见三腿合并大表
  `tmp/merged-gap-trio-best-v2.txt`（s4：会停 49.2 + 用盾一半；撞墙/脱离/贴脸三硬骨头未动）。
- **违反后果**：跳过拼池加冕 ⇒ 下一个 cm3-it155。
  —— 全文 → `docs/nn/experiments.md` §84

## §2026-10-10-goalnn-cluster-code-snapshot-ui-hatch（2026-10-10，快照的可见性（组件卡 sha 徽章）与逃生开关——plan 的 P2 + §8.3；承接 §2026-10-10-goalnn-cluster-code-snapshot）

- **背景**：P0/P1 把 `code.zip` / `ts_code.zip` 的打包时机上移到**会话启动**（重启才换）之后，
  「本会话跑的到底是哪份代码」在控制台上**不可见**——而它正是这套冻结语义下最该被看见的一件事……
- **决定（P2 · 可见性）**：组件卡在**建快照的那个进程行**上贴一枚 `code <sha12>` 徽章
  （锚种类 → 行：`hub`→hubServer；`trainer`/`export`/`run_rl`→trainingLoop；**认不出 ⇒ 不贴**，不猜）。
  链路 = `bundles/snapshot.ts::readClusterSnapshot()`（只读元数据）→ `stateView.codeSnapshot` →
  `panels/ComponentCards.tsx::codeSnapshotBadge()`（纯函数，导出以便单测）。悬停给全量：zip 名 /……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-cluster-code-snapshot-ui-hatch`

## §2026-10-10-goalnn-v11-hotlane-unblock（2026-10-10，postHitLaneTicks 定价解禁：判据 1 分流通过）

- **背景**：v11 签入时判据 1 全空，`postHitLaneTicks` 暂定“可入库但不得定价”。……
- **决定**：`postHitLaneTicks` 定价解禁，首用 `x24-stack5`（wHotLane=800，−3.6/局；
  稀有行为单价大，中位局不交钱）。`nearSqSum` 解禁已在 §2026-10-10-goalnn-v11-criterion1-split。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-10-goalnn-v11-hotlane-unblock`

## §2026-10-11-goalnn-aistudio-transfer-hardening（2026-10-11，用户 2026-10-09 报障 + 现场 log；plan/aistudio-transfer-hardening）

- **背景**：aistudio（最不稳的那块云盘，走 Cloudflare quick tunnel）单 job 准备 95.2s，三笔**纯税**：
  预取白传 4.24MB / 0 命中（自己还排队 23.9s、让路 10 次）；让路税买不回控制面速度……
- **备选与否决**：① **换链路**（named tunnel / tailscale / 国内中继）——否，**用户 2026-10-09 裁决**：……
- **决定**：只做传输层省税（A1 预取自停 / A2 让路自停 / A3 持久缓存根+容量上限 / B1 批量 blob /
  B2 控制面连接池 / B3 6s 预算 + 换连接重抽 / E1 准备段计时拆分）；**每条都必须可观测地关
  （一行原因）+ 有回退档**；`p0_p95` 的门槛从「降到 6s」改为「让路关掉后不劣化」。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-11-goalnn-aistudio-transfer-hardening`

## §2026-10-11-goalnn-ledger-pid-not-liveness（2026-10-11，账本 pid ≠ 存活判据；plan/self-node-ledger-pid-desync + 首轮评审 F1–F14）

- **背景**：2026-10-10 现场 —— self agent 11:47:31 由 `/v1/restart` 换代（pid 8340 → 21300），
  控制台账本没跟上，于是①组件卡红点 `exited`、②`/api/pool.selfStatus = null`（节点统计页「agent 未启动」+……
- **决定**：**账本 pid 降级为提示，不再是判决**（S1 只针对 selfNode：`hubServer`/`trainingLoop` 的换代
  都经过控制台 ⇒ 无实测动机，登记为预备案）。(a) `fetchSelfStatus` 拆掉 pid 前置闸，超时**恒 4000**；
  (b) `computeComponentHealth` 只对**有探测端点**的组件（判据 = `HEALTHY_PORTS` 有条目）在 pid 死时照探，
  `componentViews` 的 `alive = pidAlive || health===true`；(c) `ProcSpec.probePid`（selfNode = `/v1/ping` 的……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-11-goalnn-ledger-pid-not-liveness`

## §2026-10-11-decisions-slim-round3（2026-10-11，用户指令：DECISIONS.md 瘦身 —— 正文拆分迁移进具体文档 + 整合相似内容与同一问题的先后进展）

- **决定**：2026-09-24 起、正文 **>12 非空行**的 **192** 条全文搬进 7 个主题档既有
  「决策正文归档」节的末尾（锚 `### §<id>`，与第一轮同址同规）；索引行压成「标题 + 原文节录
  （≤6 行）+ 指针」；**同主题的先后进展在各档归档节顶生成「家族谱系」**（A/B/C… 族 + 时间序谱系链……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-11-decisions-slim-round3`

## §2026-10-11-decisions-slim-round4（2026-10-11，用户指令：「切 >6 的条目」 —— 第四轮瘦身：正文下沉 + 家族谱系续链）

- **决定**：**阈值 = 「仍带成篇正文（无 `—— 全文` 指针）且正文 >6 非空行」**；本轮 35 条 + 两条记录条
  共 **37** 条正文下沉进 5 个主题档的「**第三轮归档**」块（接各档上一张「第二轮归档」表：
  eng 7 · con 3 · ts 7 · rt 1 · exp 19），索引行压成……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-11-decisions-slim-round4`

## §2026-10-11-goalnn-lock-single-source（2026-10-11，用户指令：三处同款锁收敛到 `instance_lock` 原语，消掉同样的窗口）

- **背景**：`loop_util.acquire_lock` · `run_rl._acquire_run_rl_lock` · `remote/code_snapshot._acquire_pack_lock`
  各自一份 `os.open(O_CREAT|O_EXCL)` + `os.write`，与 `common/instance_lock` 合计**四个写入窗口**
  （§2026-10-11-goalnn-load-flake-pair 只修了 hub/worker 那一个），接管规则也各自漂移。
- **决定**：`common/instance_lock` 为**唯一实现**；新增 `marker=()`（空 = 不做身份核验：活着就拒启，
  死了/够旧的残缺才接管）· `force=True`（显式接管）· `announce=False`（抢不到就改走别的路的调用方，
  如 `code_snapshot`，不打「拒绝启动」）；三处各留薄壳（`code_snapshot` 不再返回 fd，改拿 bool +
  `release_instance_lock`）。**护栏**：生产码 AST 上只允许唯一实现引用 `os.O_EXCL`（源码面）+ 三处薄壳
  对「残缺且新鲜」的锁一律拒启且不动文件（行为面）。
- **备选与否决**：保留三份实现只各自打补丁 —— 否（同一条规则四处漂，且下一个人会再抄一份）；
  给互斥型锁也编一个「同程序指纹」 —— 否（同一个锁的持有者可以是 run_rl / loop_serve / bc / 控制台，
  指纹编不准就是把活锁当 PID 复用接管）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §75

## §2026-10-11-goalnn-push-snapshot（2026-10-11，用户指令：`_dispatch` 两次状态读收敛成一次一致快照）

- **决定**：`PushDispatcher._snapshot() -> _TickView`（一张 `self._lock` 内取出 records/avoid，
  `worker_counts` 由同一份 records 派生）；`_dispatch` 入口取一次、每门课都用它；三个旧读取口
  （`_course_counts` / `_worker_counts` / `_backup_target`）**删除**。
- **闸的分层（写进注释，别再混为一谈）**：自有账本的自洽由快照保证；**store 侧的事实无法被快照**
  ——它的权威判据是 `claim`（`_claim_locked`，即上面那条 `landed` 闸）。
- **回归**：`tests/remote/test_hub_push_dispatch.py` 两条（快照面：决策途中 pop + 让开 store 闸，
  改回「第二次读」当场红；闸面：在飞账挂另一台 id ⇒ 派发器真去 claim）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §75

## §2026-10-11-goalnn-load-flake-pair（2026-10-11，两条满载 flake 的根因修复：hub 备份腿 TOCTOU + instance_lock 写入窗口）

- **背景**：提交 hook 两次被「与改动无关」的负载型 flake 拦下；复核历史日志后两者都是**真缺陷**，不是判据太严：
  ① 队首 e2e 偶发 `pushed == 3`——已结算的活仍被开备份（那台机器会**真的从头算一轮**，白烧 GPU，回传必吃 409）；
  ② 三启同抢一个锁时先行者的锁文件被后来者当「陈旧锁」删掉（⇒ 两个进程都自认唯一，且「锁在 ∧ 端口答 200」的
  读数撒谎；历史失败日志两处**全部**落在 `_wait_ready`）。
- **决定**：① 备份副本的闸住 `_claim_locked`（`_facts_locked(...)["landed"]` ⇒ 拒发，与 `claimable_job_ids`
  / `_unfinished_pending` 同一条式子：`result/` ∨ `fail.json`）；② `common/instance_lock` 的残缺锁 + 新鲜
  （`PARTIAL_LOCK_GRACE_SEC = 30s` 内）⇒ 拒启且**一个字节都不动**，够久的残留仍按陈旧锁接管。
- **备选与否决**：把 flake 读成「测试太紧」去放宽断言 —— 否（会掩盖白烧 GPU 与双实例窗口）；锁改「原子化
  创建+内容」（tempfile 写完再 `os.link`）—— 否（多一层 FS 支持面，而只省掉一个 30s 宽限）。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §74

## §2026-10-11-goalnn-no-httpx-dep（2026-10-11，**不引入** `httpx`（T1）；plan/nn-training-reuse-and-deps §5.2）

- **决定**（用户最终裁决 2026-10-11；新运行时依赖属 AGENTS §6.5 点名须问人的四类）：**不引入** `httpx`。
  曾一度按「形态 B」落地过（硬依赖 + 云机侧补装：`remote/runtime_deps.ensure_runtime_deps()` 在
  `code.zip` 落地**之后**于 `platform_net_env()` 里 `pip install`）；**同日复核利弊后撤回并全部移除**：
  `pyproject.toml` 依赖回到 `numpy` / `pydantic`（+ torch 变体），`uv.lock` 回到 **89 包**，
  `remote/runtime_deps.py` / 新守卫 `tests/remote/test_runtime_deps.py` / 宿主接线
  （`notebook_runtime.run_notebook` · `run_loop.main`）/ `tests/helpers/remote_dag.py::LAYERS` 条目**全删**。
- **★ 不替换的论证（留存，勿重提）**：① 控制面连接池**已经**由 `remote/control_pool.py`
  （L0、纯 stdlib、默认开 `REMOTE_CONTROL_POOL=1`、已接进 `remote/http.py::_request`）提供
  ⇒ httpx 的池化收益**上界 = 它已覆盖的那部分**（引入即与它重复）；② bulk（payload/code/blob/result）
  **不得池化**——「换连接 = 重抽一次签」是抗慢链路的唯一手段，httpx 只能 per-request client
  ⇒ 相对 urllib **零收益**；③ 定制传输语义（idle 45s / total 300s 双超时 + 进度回调 + 首块重抽
  `WireSlowError` + bulk pace 让路 + 回环 / Colab 代理判据 + attempts / preempts / rerolls 三套预算）
  **无法用 httpx 默认表达**，替换 = 重写热路径 ⇒ 静默漂移风险（本仓 6 次「搬家静默废掉守卫」前科；
  且 68 个测试文件靠 `urllib.request.urlopen` / `_opener` / `_request` 的 DI 缝）；④ 云机安装与
  `remote/bundle.py`「云机不必联网」口径冲突、`httpx` 拖 7 个 wheel。全文 → plan §5.1–§5.3。
- **顺带修掉并保留的既有缺陷**：`pyproject.toml` 的 `torch-rocm` / `torch-xpu` 互斥变体原来只给 triton
  条目带了 `sys_platform == 'linux'`，`torch` 本身裸着 ⇒ Windows split 仍会解析 `torch==2.7.1+xpu`
  再被它拖去要 `pytorch-triton-xpu==3.3.1`（该 explicit index 只服务被钉住的包）⇒ **任何触发重新解析的
  改动都会让 `uv lock` 直接失败**。已把 marker 补到 `torch` 本身（两个变体都改），与本项**独立保留**
  （`uv lock` 每次重解析都依赖它）。
- **R1 / R2 不变**：`common/` 仍 stdlib-only；三个引导模块仍零依赖。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `plan/nn-training-reuse-and-deps.plan.md` §5.1 · §5.2 · §5.3

## §2026-10-11-goalnn-rollout-topup-fastpath（2026-10-11，rollout 热路径四刀：探针上界**另立常量** + 补差批按机器宽度铺满 + 轮级 `RoundContext` + **取消搬回**）

- **背景**：Kaggle 自主 worker（96 核 / 94 槽）上**同一轮内相邻两批天差地别**（it34 补差1（18 局）3.35s vs
  补差2（**1 局**）48.90s；it49 同形 3.00s → 52.49s；54 轮 p90 88.9s / max 111.2s）。现场四条否定式同时成立
  （≥5s 有 0 局 / 重试 0 / 池 killed=0 unready=0 fallback=0 / 搬回 0 失败）⇒ 既不在跑局、也不在重试、池回退、搬回超界。
  两条根因（均已在代码上闭合）：① `scratch.open_scratch` 是这条热路径上**唯一没有 `call_bounded` 的一步**
  （`probe_write_speed_mbps` 只有 `except OSError`；8MB+fsync 每批付一次）；② 补差批大小由**缺口方程**
  （`ceil(缺口/est)`）决定 ⇒ 批越补越小，而**批次级固定开销与批内局数无关**（§34 的「逐局 spawn 重付启动成本」
  在批次层面的重演）。
- **决定**：
  - ① 探针上界**另立** `game_watch.PROBE_CEILING_SEC = 5.0`（本机实测 8MB 探针 0.009–0.015s / 690–1250 MB/s
    ⇒ ×300 余量）；**不复用** `SCAN_CEILING_SEC`（`call_bounded` 超界要等满 timeout，60s 与「补差批 < 8s /
    rollout max < 30s」不可兼得；而扫盘那 60s 管的是几百个小 JSON + 内容哈希，不能被探针压低）。
    超界 ⇒ **逐字回退档** + 响亮一行 + 轮报 `probe_probe` 读数（盘抖的直接读数，不是失败）。
  - ② 新增 `worker/volume_alloc.spread_games_by_stage`：补差批 = `width`（首批**实际用过的** workers）局，
    轮转均分给「未达标且未触顶」的关（顺序 = `stages` 给定序 ⇒ 确定性）；不吃 est / 不吃 `last_lo_factor`；
    超采（相对 quota）丢弃（`wasted_samples` 上升，**软指标**）；`game_cap` 仍是**硬顶**。
  - ③ 新增 `iter_rollout.RoundContext`（**轮级**资源的唯一住处）：只装 `layout`（轮级 scratch 布局，
    `root` 是它的属性）/ `ts_home` / `job_dir` / `pool` / `workers` + `resolved`/`probe_sec`；
    **批级**的 `jd`/`s_layout`/`exec_dir`/`gate` **不进** ctx（每批一个 `topup{k}/`，批级布局由
    `batch_layout(layout, batch_rel(轮 job 目录, 批 job 目录))` 镜像推出来）。**池的生涯 = 轮**：
    首批建、补差批复用，调用方（`remote/worker.py`）在**补差跑完、PPO 之前** `close()`
    （不让 94 个常驻 bun 陪 PPO 吃内存）；`ctx=None`（老调用）⇒ 本轮自建自收，逐字今天。
    池**建不起来**时（`make_pool` 返回 `None` / `start()` 0 就绪）**不**缓存进 ctx ⇒ 下一批再试（冷启动失败多为瞬态；
    而确定性拒绝下的重试也只是常数级判断）——这是有意的，不是漏缓存。
  - ④ **取消搬回**（用户第 1 条，§3.5）：产出就住在节点本地盘的轮 scratch 目录里，`shard_dirs` 报真实路径；
    节点自己的 PPO 装载根改由**产出方显式给出**（`iter_info["shards_root"]` → `train_core.ppo_shards_root`，
    评审 P0-1——漏了就是「读 0 局语料静默训空」）；回收从「每批 `finally`」移到**全 job 收尾、PPO 之后**
    （`RoundContext.cleanup()`），并新增**轮初清场**（否则上一轮/上次尝试的残留会被轮末对账当成实产）；
    容量判据改成**整轮**需求（`_round_need_bytes` = 单批局数 × (1 + `volume.topup.max_batches`) × per_game，
    评审 P0-3）；批的产出根**镜像**进轮 scratch 目录（`scratch.batch_layout`/`batch_rel`，越界响亮）——
    否则补差批的产出没有共同祖先可被 PPO 的**单一**读取根扫到。
    ⚠ **判据前移**（代价写在这里）：F7「搬不回去的局不算结算」随搬回一起消失，结算判据简化为
    「跑局 future 正常返回」；「结了算却没产出」的发现从**搬回那一刻**退到**轮末逐位对账**
    （`verify_shards`：实产集 ≠ 声明集 ⇒ 响亮 `ProtocolError`）。也就是说失败的**发现变晚**，但它们
    仍然是响亮的（不静默少一局）。
  - ⑤ **§3.4 降级为小形状**（2026-10-11 第四刀，plan §3.4 末段）：原计划的
    `worker/iter_verify.verify_round`（「扫全轮再按声明集分组」）在 §3.5 把批产出根**镜像进轮目录**
    之后已无必要——**批根本身就是分组单位**（每批各验各的 = 按批分组，且失败当场）。真正的重复读
    只剩「同一批 manifest 为 `perGame` 读一遍、又为补差记账扫一遍盘」⇒ 改成 `_batch_totals` 优先吃
    批次报告里的逐局画像（字段逐字同名，等价性用例钉住），缺画像回落磁盘扫。轮级报告装配契约
    （`shards`/`perGame`/`perGameSecs`/`elapsedSec` 由调用方一次装配、同键同型）写进 `remote/worker.py`。
    **没做** `NN_ROUND_SCAN=0`：没有「轮级一次扫盘」可关。
- **备选与否决**：① 「探针整段不跑，直接原地跑」——否（本地盘换根是 §34 的既有收益，回退只在**真超界**时发生）；
  ② 「复用 `SCAN_CEILING_SEC`」——否（两个语义共用一个数 ⇒ 必有一头错）；
  ③ 「缺口解算但设下限（如 ≥ width/2）」——否（下限是另一个要标定的魔数；「批 = 机器宽度」不需要）；
  ④ 「cap 不截断」（plan v3 原文）——否（课程字段是操作员的**硬省**，且改造前就有活判据钉着
  `games_by_stage == {2000: 10}`：保留活判据比改写它划算）；
  ⑤ 「宽度猜一个（缺省 = 首批 argv 条数就已足够）」——否（批大小直接决定墙钟；拿不到就去**不补差**）；
  ⑥ 「ctx 里连批级布局一起缓存（plan v4 的字段草图）」——否（那会让第二节批把产出写进**第一批的** exec 根，
  与「批目录隔离 / `--out` 续号 / 批级清理」相冲）；  ⑥b「把池生命周期交给 `remote/worker.py` 单独管理
  （不经 ctx）」——否（池的建与用都在 `run_iter_rollout` 内，多一条旁路就要同步一份 ts 树/宽度，
  ctx 是同一个信息的单一来源）；
  ⑦「取消搬回但保留『每批一个独立 scratch 作业目录』」——否（PPO 只收**一个**读取根，而各批目录
  彼此不是祖孙关系，`<根>` 又是整个本地盘 ⇒ 必须镜像进一个轮目录）；
  ⑦b「产出不回 job 目录但让 PPO 收一串根（`shards_roots: list`）」——否（`discover_shards` 的调用面、
  D14 与合并账都得跟着改成复数；镜像一个目录只需三行，且与回退档形状同构）。
  ⚠ 搬回原语（`scratch.drain_tree` / `InstallGate` / `drop_drain_stage`）**保留**（有全套用例）但
  **已无生产用点**（模块头已标注）——删它要另开一刀（连带清用例），本轮只降为“备用腿”。
- **后果**：§3.1/§3.2/§3.3/§3.4/§3.5 已落地（探针有界 + 轮级 ctx + 批=机器宽度 + 扫盘去重/装配契约
  + 取消搬回/PPO 读取根/整轮容量）；§3.6–§3.10（回传失败备份 / 池宽 / 探针缓存 / 读数五分项 / LAN 队列）
  仍在队列。§3.5 那个**已定案的前置已实现**：`remote/train_core.py` 的装载根不再是写死的 `job_dir`，
  而是产出方给的 `iter_info["shards_root"]`（纯函数 `ppo_shards_root`，空串/缺键/`None` 逐字回落）——
  「取消搬回但忘记改读取根 ⇒ job 内 PPO 读 0 局语料」这条已闭。
  ⚠ 满铺器改的是**每轮样本量/更新构成**（非语料身份/课程/奖励）⇒ 属训练侧；按 §15.5 口径当**新实验**处理：
  新 `--out`/`--traj`，**不跨改动续跑**。
—— 全文（现场读数 / 根因链 / 判据迁移表 / DoD）→ `plan/rollout-topup-fastpath.plan.md` §1–§4 · §10 ·
`docs/nn/runtime-opt.md` §35 · 评审 → `plan/rollout-topup-fastpath.review-bf.md`

## §2026-10-11-goalnn-aborted-course-revival（2026-10-11，停腿课复活 + 复活卫生 + 拒收终局 + `/admin/cancel-job`；plan/aborted-course-revival + 首轮评审 F1–F8）

- **背景**：x24-stack5 事故（2026-10-11）——调度器级 5 连败判停腿（ABORTED）后**三条复活通道
  都够不到它**（`reopened_parked` 要求 `course in done_hooked` 而 ABORTED 永不入；`reopenable_skipped`
  的记账只在一步级 SystemExit/开课失败两处写；`fresh` 要求课不在 `runtimes`），不重启 serve 即终身
  停车（窗口 ≈4h50m，靠当晚 node upgrade 重启经 `fresh` 在 it56 续跑）。深挖后另有两条结构性欠账：
  ① 复活**复用热引擎**（C-0 前科）但 `_leg_abort`/`_remote_fail` 是引擎级终态且全仓无复位点
  ⇒ 「复活」会立再死或一轮假收官（评审 F1）；② 三重校验拒收只抛异常、**训练侧会话保留**
  ⇒ 同一份坏结果被重落 5 次，毒 job 炸的是领它的那门课（评审 F2）。
- **决定**（W1–W7；serve 侧零协议、hub 侧纯加法）：
  - W1/W2 `_settle_rounds` 把「已处理」与「已收官」分开记账（ABORTED 进 `done_hooked`，不写
    `run_complete`）；serve 在 `trace.action=="aborted"` 拍补 `_record_skip(SKIP_KIND_STEP, …)`；
  - **W3 复活卫生** `_revive_hygiene(rt)`：复活前清 `_leg_abort`/`_remote_fail`/`_consec_fail` +
    `runner.contexts.pop(本课)`（丢弃保留会话）；两处复活共用；**不动** `runner.finished`；
  - W4 摘名去重（同一「停→开」不双复活）；W5 `SKIP_KIND_STEP` 指纹纳入**账本 mtime**
    （「作废/重发 job」本身即复活触发，用户停课仍拦）；
  - W6 拒收终局：`verify_and_land` 改抛 `TripleCheckError`（`HubClientError` 子类，消息逐字不变）；
    `cancel_job` 落账本 `job_cancelled`（净态门 + 幂等）+ `_remote_ppo_step` 对拒收**清会话**
    （瞬时性落位失败不清）；
  - W7 人工把手 `POST /admin/cancel-job`（单 job 定向作废；判据 = `claimable ∩ ¬(_claimed|_computing)`
    与全池版同源；404/409/200 + `{cancelled, inflight, why}`，幂等可重放）；全池版循环体抽
    `_cancel_job_locked` 复用，行为不变。
- **备选与否决**：① 定时器式自动重试停腿课 —— 否（I-2：复活唯一触发 = **盘上事实变化**，
  自动重试＝把 5×30s 空转变无限空转）；② 单 job 撤单直接调全池版 —— 否（多课共 hub，
  会顺手撤别人的活，违反 `test_auto_handoff.py:982` 契约）；③ 拒收按消息文本匹配判定 —— 否
  （改文案即静默失效，用专用异常类型）；④ 落位失败一律清会话 —— 否（瞬时性失败应重落取回的结果，
  清会话会白烧一次云上重算）；⑤ 复活时重建引擎 —— 否（C-0 前科：新 runtime/引擎对不上，
  进无限 RETRY；清判决态即可）。
- **后果**：停腿课在「改课程文件 / 停开一次课 / 作废毒 job」后自动复活**并真正续跑**（不重启 serve、
  不重建引擎、不写假收官）；毒 job 有账本终局与人工把手。新增守卫 7+2+2+6 例；
  门禁 `nn-python-gate.sh` **4202 passed / 10 skipped**（ruff+mypy 绿）。
  ⚠ 该改动同时修正了 `docs/nn/remote-transport.md` §68 的两句旧陈述（「也没有出口」/「唯一解法是
  训练侧重启」——自 §82 起不再成立，旧条文按「只增不改」原样保留）。
—— 全文（现场三源 / 根因链 / 评审响应表 / DoD）→ `plan/aborted-course-revival.plan.md` §1–§9 ·
`docs/nn/remote-transport.md` §82 · 评审 → `plan/aborted-course-revival.review-bf.md`

## §2026-10-11-goalnn-x24-stack7-fork（2026-10-11，stack6-it165 上并行两单变量腿：burst 连击税 + dist 远杀补贴）

- **背景**：it165 gap 表（人类 160/160 vs NN 71/160；`tmp/probe-gap-stack6it165-table.txt`）+
  早崩 51 局三列均值表（`tmp/probe-gap-early51-means.txt`）定案两条因果链：
  ① 早崩 = 连续挨打（D01 2.4x / D02 8x / D05 129 vs 26），stack5 的 bundle 失败 ≠ 单项失败；
  ② 贴脸是工具性必需（A06 0.60 vs 人 0.375，near 税只能罚不能替），`wNearSq` 已贴 E3a 上限。
  D06 经分解证伪「重炮」说（每中伤害 94/96/105 三组同款，gap 全系 D07 命中率）；
  D08 距离中介说证伪（组内 corr ≈0/−0.4），不可定价的理由换为分母操纵 + 无动作梯度。
- **决定**：fork 两腿（起点均为 stack6-it165，sha `4e0d3a6c76f26188`，it0 原样拷贝；
  §15.5 新实验：新 out/traj/opt fresh，不在 stack6 账本上续跑）——
  `x24-stack7-burst`（stack6 价 + `wBurst=6.0`，−3.1/局，与 stack5 同剂量可比；
  it40 门 burst↓ + kills≥12.2，胜率不判门）/
  `x24-stack7-dist`（仅 `wDist 1.8→2.5`；it40 门 A06↓ + A07↑（panel resim 判定）+ kills 持平，
  A07 不动即杀）。终判均为冻结 1600 对 vs s4-it180 + KR10-it150。
  maxDamage120 维持禁区；dangerT 不同时加（防重组 bundle）；wNearSq 不再加（E3a 上限）。
- **备选与否决**：① wBlock 加量治顶住 —— 否（W11 碰次数机已优于人 9 vs 15，病在 persistence，
  总量价教不会撒手；要治等加列 `stuckMax`）；② onLaneMoveTicks 代理“离开” —— 否
  （语义错 + raw-only 名单；离开奖等加列 `laneExitPostHit`）；③ burst+dist 塞同一条腿 —— 否
  （重演 bundle，credit 稀释）；④ s4-it180 起点重测 bursts —— 否（本问是 stack6 上的边际效应，
  fork 点基线冻结更直接）。
- **后果**：两 `.jsonc` + it0 已冻结待开（`nn-training/curricula/x24-stack7-{burst,dist}.jsonc`，
  公式列全对 `reward_library.METRICS` 校验过，单变量 diff 已验）；
  开课 = 人在控制台点（agent 不开训）；加列清单（`stuckMax`/`laneExitPostHit`/`shotsNear4` 类）
  因 `batch_runner.py` LOC 天花板（999/1000）顺延至重构后。
—— 全文（剂量/门/熔断/yardstick）→ 两课程文件头注 · 基线 `tmp/x24-stack6/eval_log.jsonl:it165`
  （0.5125/14.31，疑似峰已在门里声明）

## §2026-10-11-goalnn-x24-s56-close（2026-10-11，stack5/stack6 结课判杀 + gap 注两条订正）

- **背景**：stack5（rollout it2–145，eval it0–145）：终点 it145 0.4375/13.25，
  it5+ 均值 0.4567/13.45（it0 0.51/13.98）；it40 门 0/3 全反且 it145 五项仍全反/平。
  stack6（rollout it1–165 零事故，eval it0–165）：终点 it165 0.5125/14.31（单批峰），
  it5+ 均值 0.4663/13.62；it40 双 miss，it165 nearR 只 −8.7%（曲线回吐）。
  it165 gap 表 + 早崩 51 局三列均值表见上条。1600 对拼池两腿皆不跑（日常已够判阴）。
- **决定**：两腿按门杀腿归档，结课报告写回各自课程文件尾部；stack6-it165 冻结为 stack7
  双腿 fork 点；`tools/gap-text-table.ts` NOTES 订正两条——D06「重炮」说证伪
  （每中 94/96/105 同款，gap 全系 D07 + 局短虚高）→「命中率问题，局短虚高；炮弹同款」；
  D08 距离中介说证伪（组内 corr≈0/−0.4）→「结果率，分母可操纵，不定价」（头注留出处）。
- **备选与否决**：① stack6 趁 it165 峰续跑 —— 否（峰是单批 400 局噪声，门已 miss，
  续跑烧机时；要的边际已由 stack7 接手）；② 1600 对终判 —— 否（判阴不需要拼池，
  拼池只用于加冕）；③ D06/D08 照旧注留档 —— 否（注是指南，会被后人当定价依据引用，
  错注必须当场改）。
- **后果**：课程文件状态行 → 已结课；停训由人在控制台执行（agent 不开/停训）。
—— 全文 → `nn-training/curricula/x24-stack{5,6}.jsonc` 尾部结课报告

## §2026-10-12-goalnn-burst220-crown（2026-10-12，burst-it220 弑君+卫冕加冕 + stack7 双腿结课）

- **背景**：stack7 双腿 it300 收官。burst 腿 it40 门 1/2（burst↑ fail / kills pass），
  overrun 至 300，终点目标指标零漂移（burst pooled 0.488 斜率≈0）；dist 腿 it40 门
  panel 双 fail（A06 0.6000→0.6017 / A07 0.2168→0.2075），overrun 至 300，终点仍反向
  （A06 +0.018 / A07 −0.012，中位）。两腿日常三峰各三（burst：it220/it80/it265；
  dist：it215/it265/it60，SE 内皆持平）。
- **决定**：① burst-it220 加冕现役最优（用户裁定）：弑君战 1600 对 0.5550 vs s4-it180
  0.4913（+6.38pp，χ²cc 12.14，p≈0.0005，两段同向，kills +0.83）+ 卫冕战 1600 对
  0.5669 vs dist 三峰（−6.00pp 显著胜 it215 / −3.81pp marginal 胜 it60 / −1.25pp 平
  it265），四战未尝败绩，跨四新段复现 ~0.56；② stack7 双腿结课归档：burst 税判阴
  （稀疏税教不动）、dist 远杀补贴路线退场（稀疏病证实，不再复测）；后继基线改挂
  burst-it220（sha16 `caf9e8749fdad70d`）。
- **备选与否决**：① 跑 vs fore-it335 统一战 —— 否（用户令不跑；王位按直接对战序列改挂，
  fore-it335 的 414xxx 系读数保留作史实，§84"再议"关闭）；② it265（对 burst 平局）
  列加冕候选 —— 否（平局无挑战资格）；③ dist 腿拼池 vs s4 —— 否（panel 已判死方向，
  拼池只复述 null）；④ 两腿门后 overrun 的 260 轮追认有效 —— 否（记为程序瑕疵，下次
  门配自动 halt）。
- **后果**：两课程文件状态行 → 已结课 + 尾部结课报告；现役最优链挂到 burst-it220；
  王位统一战不再欠账。
—— 全文 → `nn-training/curricula/x24-stack7-{burst,dist}.jsonc` 尾部结课报告 ·
  证据 `tmp/burst-crown-4166|4174/rows.jsonl` + `tmp/dist-crown-4152|4156/rows.jsonl` +
  `tmp/panel-dist-{it0,it40,it300}.jsonl`

## §2026-10-12-goalnn-offline-start-checkpoint（2026-10-12，离线腿起点 checkpoint 的 `it` 错位：起点单源化 + 播种先于补传腿 + 补传 409 冲突闸；plan/offline-start-checkpoint-it-collision + 首轮评审 F1–F6）

- **背景**：x24-stack5 事故（2026-10-11）——自主 worker 领课后 **it1 的真训练结果永不到 hub**：
  `it-000` 与 `it-001` sha 相同（同一份起点被铺了两格，`it-001` 的 mtime 还早 1.177s），
  hub 账本前两行是 rowless 的 `it=1`/`it=0`。四条独立根因：① **起点定位有两份实现**
  （`remote/bundle.py` 按 `manifest.it` 铺 `it-{manifest.it}`，`plan_handoff._seed_start_checkpoint`
  按 `plan["start_it"]`）——而契约是 **`manifest.it == plan.start_it + 1`**（左端点=已完成那一轮，
  其权重就是 payload 的 `init_weights`；`manifest.it`=包里要跑的第一轮）；② 守卫断言写反（`!=`）
  且**无生产调用者**，夹具又造 `manifest.it == plan.start_it` ⇒ 断言与夹具**互相印证了一个错误的
  世界模型**；③ 补传腿 `pending()` 只认「目录名 + `weights.json`」，起点与真轮次不可区分；
  ④ 播种晚于补传腿启动（首扫看到「铺到一半」的目录）；再叠 hub 首写锁定 + advance 分支不看三件齐全
  ⇒ 假 it1 被锁死、真 it1 重推被判 duplicate（**= 成功**）静默丢弃、活动权重还被推到起点。
- **决定**（D1=A 起点永不回传 / D2=首写锁定不动但区分 rollout-only / D3=A 断言抽函数在装配点 / D4=A 同族一起修）：
  S1 起点单源化（删 `bundle.py` 的铺设块 + 零读者的返回键 `it`；补「包根 `opt.tar` 回退」——
  否则云上 Adam 黍黍归零）；S1b `deliverer.start()` 挪到 `_seed_start_checkpoint` **之后**（结构性质，
  不靠判据兜）；S2 抽 `_assert_plan_manifest_aligned`（判据 `+1`）进**唯一装配点** `open_run_context`
  ⇒ 矛盾包在任何落盘之前拒收；S3 客户端判据换为「**账本里有这一轮的行 ⟺ 真轮次**」
  （`_ledger_its()`，按 `(size, mtime_ns)` 惰性失效）；S4 hub 侧「已存在且**字节相同** ⇒ duplicate（仍 200）/
  **字节不同 ⇒ 409 conflict**」+ 客户端把 409 从 ok 元组摘出走 `_reject_round`（带指纹、跨会话）；
  S6 `plan_run` 的 `artifacts["start_it"]`（值其实是 `manifest.it`）改名 `anchor_it` + `build_plan` 文档串纠错；
  S9 advance 分支加「必须带真账本行」闸（判据看**请求体**的 `row`）。
- **备选与否决**：① 判据用 `it <= start_it`（旧草案）——否（事故冒充的正是 `it-001 = start_it+1`，
  它抓不到；且它更宽，会压掉跨腿复用目录里更早的合法轮次，而回溯那些轮次是 `pending()` 的本职）；
  ② 让 hub 从落盘产物反推「是不是起点」——否（`row=None` / 无 opt 的信息落盘后消失，hub 照样写裸行
  `row.json`，且 hub 侧目录里**没有** `plan.json` ⇒ 判据只能在客户端）；③ 只改 hub 加冲突闸——否
  （旧客户端把 409 记成「已投递 = 成功」，新闸会被静默吞掉）；④ 「统一」`plan.start_it` 与 `manifest.it`——否
  （两者语义本就不同且被多处依赖：区间左端点 vs 本 job 那一轮）；⑤ 冲突时覆盖 hub 上那一份——否
  （同一轮权重是不可变快照，谁先到不能决定历史；不匹配只能由人裁决）。
- **后果**：把这类事故的**上游**（起点被当成一轮）与**下游**（假轮次推进进度 / 真轮次被静默丢）两段
  都堵住；新增守卫 4+1 例（`tests/remote/test_offline_deliver.py`：409 端到端「不覆盖、不并账」·
  **客户端 409 = 内容拒收**（`artifacts` 不含它 / `rejected` 带指纹落盘 / 不停腿 / 本会话不重试）·
  无账本行不上行 · advance 闸（含反例）；`tests/remote/test_run_loop.py`：拒收
  `manifest.it == plan.start_it` 的包 + 拒收在任何落盘之前 + 正对照），另把三个文件的夹具从错世界模型
  改回真实契约。门禁 `nn-python-gate.sh` **4207 passed / 10 skipped**（ruff+mypy 绿）。
  ⚠ **`x24-stack5` 的 hub 侧假 it1 必须人工清理或重跑该轮**（`offline/0805a8f36a305708/it-001/`,
  fp=`551d7846…`、rowless，与账本 `it=1 opt_bytes=0` 那一行），否则真 it1 重传会撞 **409 conflict**
  ——这正是新闸的本意（让人看见并裁决，而不是静默丢）。**未做（明写）**：S8 `hub/task_pack.py`
  清单读面的两源 fallback（`idx["it"] or plan["start_it"]`），独立可后置。
—— 全文（现场证据 / 根因 A–D / S1–S9 / 验收门槛 / 评审修订表）→ `plan/offline-start-checkpoint-it-collision.plan.md` ·
`docs/nn/remote-transport.md` §83 · 评审 → `plan/offline-start-checkpoint-it-collision.review-bf.md`

## §2026-10-12-goalnn-obs-v5-critic-superset（2026-10-12，obs-v5：观测缺口一次补齐，**critic 输入集解禁**（推翻 engineering.md 的既有否决）；plan/obs-v5-vector-gap.plan.md + 首轮代码评审 F1–F10）

- **背景**：策略头缺 28 维「决策时不可从现有观测量推导」的方向量（`POLICY_EXTRA` 9→37）与 10 维
  标量（`SCALAR_DIM` 30→40），且 critic 只看 GAP 后的 hidden（128），与策略头看到的 `pf` 不是同一份
  输入——症状映射（D08 必死弹躲开率 0.417 vs 人 0.648、W07 撞墙 111.6 vs 45.4）指向「观测侧缺口」
  而非奖励定价。唯一拦路的是 `docs/nn/engineering.md` 的否决：「改 `SCALAR_DIM`（30→32）方向（会连带
  fc/value，否决：违反 critic 输入集不动）」。
- **决定**（D1=A 单腿一次补齐 / D2=A value 全吃 pf / D3=A 只追加不重编号 / D4=A mirror 双侧同步）：
  ① **推翻**该否决：critic 输入集**不是**不可动契约——训练侧属 AGENTS §0.2 允许域，且根因正在于
  critic 盲区（value 看不到 extra ⇒ 优势估计与策略不在同一坐标系）。② `OBS_SCHEMA_MAJOR` 4→5，
  `POLICY_EXTRA_DIM` 9→37、`SCALAR_DIM` 30→40，**现有 0–8 / 0–29 序号与语义一律不动（只追加）**。
  ③ value 头与策略头吃**同一份** `pf`（腿 A 165 = hidden 128 + extra 37；腿 B 189 = tower 112 +
  scalars 40 + extra 37；legacy/intent 不变），无用列不预剪、由优化器自行闲置。④ 跨层公式收敛到
  `src/nn/danger-metrics.ts`（`sampleBasePressure` 4 处生产拷贝 + `nearestPickupDist` 1 处全搬，
  工具侧 re-export）——单一几何事实源，禁在 `src/nn` 里复制第二份。
- **备选与否决**：① 只加 extra、不动 scalars —— 否（s6 同源精确 + `baseRing×3` 方位、s38 精确 +
  方向族转向是**同一份**基地语义，拆开就只剩半边）；② 保持 value 128（只扩策略头）—— 否（critic 与
  策略不同坐标系，优势估计会系统性错配，是本次要修的根因之一）；③ 拆 β→α→exp1′ 三腿分步上 ——
  否（用户 r6 指令回单腿：bump 对 RL 腿无一次性重导成本，分腿只多三份账本）；④ 每维单独 ablation ——
  否（维度 38 个、样本量不允许；改为「维类三行表 + G1/G2/G4 探索类内归因」的预注册门）。
- **后果**：`OBS_SCHEMA_MAJOR=5` ⇒ 旧语料/旧权重全废（RL 腿现产，无重导成本；BC 存量在 MAJOR=3
  时已废）；指纹新增末段第四字段 `EXTRA_MIRROR_NEG`（负号域与 swap 域分家，swap 表达不了取负）；
  `infer.ts` value 头判据由「长度 == 128+9」改为**按档位**（`intentHeads`→137／`spatialTower`→189／
  `policyExtra`→165／legacy→128），长度不符 throw——旧档位的静默置零只留给无 value 头的 BC 权重。
  ⚠ 本条目是施工前置：早稿 plan 引用的 `§2026-10-12-obs-v5-critic-superset` **从未落条**（首轮评审 F1），
  即「推翻既有否决」当时无授权链；本条目与代码同期落盘，此后引用以本 ID 为准。
—— 全文（冻结规格 §2.1–§2.7 / 实施步骤 S0–S9 / 验收表 A·B / 预注册门 G0·G3·G5 / 评审记录）→
`plan/obs-v5-vector-gap.plan.md` · 代码落点 → `src/nn/{policy-extra,obs-encoder,infer,danger-metrics}.ts` ·
`nn-training/common/schema.py` · `nn-training/worker/models/student.py` · `nn-training/worker/scripts/init_spatial_leg.py`

## §2026-10-12-goalnn-x26-obs（2026-10-12，x25 改名 x26-obs＋batch 减半＋奖励复核去死税；§15.5 新实验）

- **背景**：x25-obs-v5 开课前未跑（无沉没账本）。用户同日三令：课程改名 x26-obs、
  `target_transitions` 49152→24576、按人类语料（§71：62 局窗口配对）＋burst-it220
  表现（日常 ~0.51／拼池 0.5550–0.5669／kills ~15.1／pDmg 221／maxD120 139 反向）复核 17 项定价。
- **决定**（D1=改名含全身份：课程文件＋权重目录＋it0 文件＋out/traj/backup 全换 x26，
  it0 经 mv 改名 sha16 `6f32a17362cbe5ab` 不变，meta 无输出路径故干净；`paired_rotate_seed`
  沿用 20261020——x25 流从未消费；D2=batch 只动这一键，优化器键全冻结，KL 早飘第一杠杆 =
  恢复 batch；D3=17 项只动一项：删 `- wBurst*damageBursts`（项参同删，dist 腿先例公式可不带
  wBurst）——x24 结论①单变量判阴（300 轮零漂移、0.4/局、60% 零局，稀疏税教不动），留着只剩
  advantage 噪声，塑形负荷 24%→~16%；其余 16 项全留：wDist 留 1.8（dist 腿判死的是加价方向，
  不是基线）、wMiss/wAcc 留（§71 方向对、blk 手段刚到）、wDmg/wHurt/wThreat 留（人类承伤
  ~30/千tick vs NN ~150–230 方向对、判的是手段缺）、wBlock 留（罚顶墙推、人类式 STOP 不挨罚）、
  wEncl/wCorner/wNearSq 留（§71 差距在多敌分档、encl 本就 n≥2 才计）、
  wPickup/wKill/wHit/wFr×3 留（拾取 1.41 vs 1.28、中段击杀率持平均无定价信号）。
  D4=G0/G5 阈值一字不动（用户指令出处；禁令②禁拿教师表现定 DoD）；G3 记为已降级
  （A6 blk 对向 −0.93 命中条件，W07 只报数）。
- **备选与否决**：① x25 干跑保干净单变量 —— 否（用户令三变量混杂，G 门照判不多 claims）；
  ② batch 减半顺手调 lr/epochs/mb —— 否（再动是第四变量；KL 盯盘见 §15.4）；③ wBurst 留 0 不删项 ——
  否（死文本不如删项干净，引擎与 dist 腿已证明无 wBurst 公式合法）；④ 砍基线 wDist=1.8 ——
  否（未经验证的新方向）；⑤ paired_rotate_seed 换新 —— 否（旧流处女，换了白换）。
- **后果**：x25 文件／目录／权重名不复存在（git 判改名）；x26 `course_openable=(True,'')`；
  起点权重仍是 f3a42dd 后代码产物；开课走控制台，起点纪律见课程头注。
—— 全文 → `nn-training/curricula/x26-obs.jsonc`（头注含 17 项复核表）· 证据 → x24 burst/dist
结课报告 · `docs/nn/experiments.md` §71 · DECISIONS §2026-10-12-goalnn-burst220-crown

## §2026-10-12-goalnn-x26-batch（2026-10-12，x26-obs 的并行 batch 对照腿；§15.5 新实验）

- **背景**：用户令开一条 x26 并行腿喂饱 GPU worker（课程与节点正交：两条已发布腿 ⇒
  一腿 CPU rollout 相时另一腿可用 GPU；GPU 数不断言）。x26-obs 把 batch 减半，
  最大风险是 KL 失稳（§15.4 大批次是 KL 稳定器）。
- **决定**（D1=并行变量取 batch 全量：`target_transitions` 24576→49152，其余键经脚本
  逐键对账一字不差——同起点 it0、同流 20261020、同奖励、同优化器、同 eval；D2=同流
  （x24 burst/dist 共享 x23 流先例，rollout 可比）；D3=it40 硬门 kills ≥12.8
  （15.1×0.85）＋ it30–40 mean KL ≤0.2（schedule 自带 kl_cap），fail 即杀且控制台挂
  自动 halt（x24 overrun 空烧 260 轮教训）；终点与 x26-obs 同判 G0/G5/G3＋KL 轨迹对照，
  接近才加 McNemar 拼池）。
- **备选与否决**：① 塔腿（leg B）—— 否（plan 明确塔重测另案；value 冷启动是另一个问题，
  与 batch 险无关）；② 奖励探针腿 —— 否（x26 头注刚定“手段刚到先看生效”，加价自相矛盾）；
  ③ 探索/curiosity 腿 —— 否（无 crisp 门，证据弱）；④ 顺手调 lr/epochs —— 否（第二变量）。
- **后果**：本腿是减半决策的保险＋干净 ablation（稳则差值即 batch 效应；24k 飘则 49k 仍
  交付 obs-v5 答案）；`course_openable=(True,'')`；新 out/traj/backup，不碰 x26-obs 账本。
—— 全文 → `nn-training/curricula/x26-batch.jsonc`

## §2026-10-12-goalnn-x26-batch-retired（2026-10-12，x26-batch 未开课撤回；§6.4 偏离更正）

- **背景**：上条为 x26-obs 配的并行 batch 对照腿（`x26-batch.jsonc`，已删，未开课）。
  用户裁决：24k  batch 前课已证充分（h3/h4 家 31 门 24576＋22 门 24000 先例可查）；
  TPU 高配多采抑噪无碍，GPU 云机训练墙钟翻倍不值。另用户明令：不到转场障碍/基地时候，
  两阶段不动（提前开专场验证 corr/blk/基地族的想法否决）。
- **决定**：x26-batch 撤回（文件已 `rm`，从未发布）；x26-obs 单腿跑，batch 不再设对照。
  ladder 内无第二条需训练验证的 obs 方向（见本条后果）：ladder 可见族（tti/laneE/s39/
  s30/s31/pickup×3/cornerDist）由 x26-obs 本腿经 G0＋症状面板验证；退化族
  （corr/blk/wallDist/基地 14 列）在 ladder 内按构造不可验证，留 G0 后专场；W10 残余是
  记忆问题（prev 已删），由事后 panel 回答，不开腿；归因（策略真用哪几维）是 eval 期
  ablation 探针，不是课程。
- **备选与否决**：① 提前开 obstruction 并行腿验证 corr/blk —— 否（用户 veto，两阶段不动）；
  ② 为喂饱 GPU 硬凑第二腿 —— 否（已由下条 §gpu-overlap-correction 推翻：错的是否决理由，
  不是结论——见下条）。
- **后果**：x26 家只剩 x26-obs 一腿；本更正替代上条的“后果”，上条正文保留作史实。
—— 文件 → 已删 `nn-training/curricula/x26-batch.jsonc`（未入库，无残留）

## §2026-10-12-goalnn-gpu-overlap-correction（2026-10-12，认错：单 GPU 双腿墙钟论错了；§6.4 更正）

- **背景**：上条否决 ② 称“单 GPU 上双腿拉长墙钟”——**错**。用户纠正：PPO 每轮 =
  CPU 为主的 rollout（仿真＋小批量推理，GPU 基本闲置）与吃满 GPU 的 update 交替；
  两条 24k 腿交错相位，GPU 常满，**双腿墙钟与单腿基本一致**。我把“GPU 是瓶颈”想成了
  “双腿分 GPU”，漏了 rollout/GPU 相天然互补这一层。
- **决定**：① 上条结论（x26-batch 撤回）**维持**，但理由只剩一条：49k 加的是 GPU update
  相的活（真正的瓶颈），24k 已被 31＋22 门前课证明充分——不是“双腿拉长墙钟”。
  ② 正确的 GPU 命题是：**第二腿只要 batch 同为 24k，墙钟几乎白赚**——并行腿问题重开，
  候选只剩 obs-v5 另一半：腿 B（塔，189，ladder 可训、与环境无关；v4 起点
  `spatial-legB.it0.json`（schema 4、头 151/value 128）本机在库，分段迁移有单测覆盖）。
  r6 单腿是用户指令，塔腿是否开并行待用户拍板，不擅自写文件。
- **备选与否决**：见塔腿提案（用户拍板后落本条续）。
- **后果**：本条推翻上条否决 ② 的理由部分；x26-batch 撤回结论不变。
—— 证据 → 用户 2026-10-12 原话 · v4 腿 B 起点在库（本机核过形状）

## §2026-10-12-goalnn-x26-tower（2026-10-12，x26 并行架构对照腿：腿 B；override r6 单腿）

- **背景**：GPU 交叠纠正后重开并行问题：24k 定死、ladder 不动、不加价三约束下，
  唯一剩下的 obs 验证方向 = 腿 B（塔 189，obs-v5 未验证的一半；ladder 可训、与环境
  无关）。r6 单腿是用户指令，本次用户拍板开并行即 override（ carved 范围 = 仅加腿 B，
  corr/blk/基地族仍等 G0 后专场，两阶段不动）。
- **决定**（D1=单变量＝架构＋必需起点：batch/奖励/流/优化器/eval 逐键同 x26-obs，
  脚本对账零差异；起点 `weights/x26-tower.it0.json` 与 x26-obs **同源 burst220**，
  经新增 A→B 映射转出（旧 extra9 [128..136]→新 [152..160]，余 180 列重 init）：
  params 86952（= §2.6 预测值）、missing=[塔四键＋value]、sha16 `520c1b0811439071`、
  35 个 trunk 共享键与 burst220/x26-obs 三方逐位等（对照只剩 tower 路径＋头新列＋冷 value）、
  strict 装载已验；D2=it40 硬门 kills ≥12.8＋meanKL ≤0.2＋控制台自动 halt，
  终点同判 G0/G5/G3＋跨架构对照，接近才拼池；D3=冷 critic 头 40 轮只看 policy/KL/kills，
  不看 value loss）。
- **备选与否决**：① 同起点 —— 否（架构不同起点必然不同，这是对照的必需代价）；
  ② 塔腿不同流 —— 否（x24 burst/dist 共享 x23 流先例，同流 rollout 可比）；
  ③ 奖励顺手微调 —— 否（第二变量，且与 x26 复核结论矛盾）。
- **后果**：x26 家两腿（obs 165＋tower 189）同流并进；`course_openable=(True,'')`；
  新 out/traj/backup，不碰 x26-obs 账本；开课走控制台。
—— 全文 → `nn-training/curricula/x26-tower.jsonc`

## §2026-10-12-goalnn-leg-a2b-migration（2026-10-12，burst220 可迁腿 B：A→B 映射＋源族自动识别）

- **背景**：用户问 burst220 能否迁 tower。能，但旧表只认 151 宽 v4 腿 B 源；而在库
  `spatial-legB.it0.json` 的 trunk 系 x21-psh-a（非加冕系）——用它起塔腿，对照混进
  trunk 血缘。burst220（137 宽腿 A）的新 pf 里只有 extra9 有槽（hidden 无槽）。
- **决定**（D1=加 `_LEG_A2B_SEGMENTS=((128,152,9),)`：fc 照旧 drop 10，头拷一段，
  value 不进表（全重 init）；D2=腿 B 源按旧头宽自动识别（151→三段表／137→A2B 表，
  其他宽响亮拒绝，legacy 128 无槽可对）；D3=塔腿起点重转 burst220 源，
  旧 spatial 系产物作废（sha `621ee83b…` 退役，从未开课，无残留）。
  新产物 sha16 `520c1b0811439071`，35 共享键三方逐位等。
- **备选与否决**：① 沿用 spatial 系起点 —— 否（trunk 非加冕系，对照多一层混杂，而
  A→B 只多 9 列拷贝＋180 列重 init，代价一样）；② 手工指定源族 flag —— 否（宽即族，
  自动识别少一个误用面，非法宽照样响亮拒绝）；③ legacy 128 也接 —— 否（hidden 无槽，
  全重 init 的头会伪装成迁移成功）。
- **后果**：`init_spatial_leg.py` 支持 137/151 双源（单测 8 例：先红后绿）；
  x26-tower 头注已同步新源＋新 sha；两腿 trunk 同源，对照收紧。
—— 落点 → `worker/scripts/init_spatial_leg.py` · `tests/worker/test_init_spatial_leg.py` ·
`nn-training/curricula/x26-tower.jsonc`

## §2026-10-12-goalnn-x26-double-kill（2026-10-12，it206 双杀：obs 检查线挂＋tower 平台）

- **背景**：it206 同局（seed 全同）复核。obs：it130–150 均值 0.473≥0.41（①过，回到
  crown 带）／value 新列 rms 0.30→0.36 仍膨胀（②挂）／KL·entropy 稳（③过）；
  205 轮零净增益（0.50 带，G0 差 30pp，x24 先例 300 轮 +5pp）。机制：冷 critic 持续
  拟合新维噪声（旧列 drift 仅 0.03），§2.6“无用则学小”在 value 头被证伪，
  advantage 污染→暖策略游走。tower：0→0.25/10.77 健康爬升后 it160 起平台
  （0.21–0.31）；it40 kills 门（12.8）对冷启动误标（实测 8.57），不执行；
  跨架构 A >> B，塔可训、无优越信号。
- **决定**（用户拍板）：双杀（obs 按预注册“②挂即杀”fail-fast；tower 平台＋G0 无望，
  省 95 轮 GPU）。停腿人在控制台执行。修法不存在热补丁（改训练语义 = 新实验），
  重开设计另起（方向：value 新列门控／暖机纪律，正对本次机制）。
- **备选与否决**：① obs 跑满 300 —— 否（x24 overrun 教训；205 轮零增益＋机制进行中，
  赌后段 peak 证据弱）；② tower 留到 300 —— 否（平台 45 轮＋远离 G0，曲线信息已收敛，
  省 GPU 给重开）；③ 热修 value 头续跑 —— 否（§15.5：改语义即新账本，无续跑）。
- **后果**：x26 家两腿结课（未毕业）；obs-v5 的 ladder 答案 = 新维 ladder 无可测分红
  ＋冷 critic 噪声拟合机制；塔答案 = 可训但慢、无优越信号。重开前置：value 门控设计。
—— 证据 → `tmp/x26-{obs,tower}/eval_log.jsonl`（seed 全同复核）· 权重对比 it0→it94/it207
## §2026-10-12-goalnn-prefetch-stage-unpack（2026-10-12，把「解包」从关键路径搬到上一轮 PPO 上：预取线程解包 + 单点搬入 + 形状分派；plan/prefetch-stage-unpack + 首轮评审 P0-1..P2-6）

- **背景**：`§80`（aistudio 传输硬化）之后那台机器上链路**不再是病灶**（`in=` 0–3s、`out` 已与 PPO
  重叠、`p0_p95` 1.9s）——剩下 PPO 32–35s（75–85%）与本地准备 3–13s。准备段里最大的一块是**解包**
  （1.8–4.3s/轮、首包 11.8s；换算随 bytes/shard 走：2.48MB/68 shard = 26ms、2.70MB/294 shard = 11ms）。
  病根：软持有预取（`§64`）只把**下载**叠到上一轮，**解包**仍在关键路径。
- **决定**：D1 = **预取线程解包**（`PrefetchStore.store(..., unpack=False)` 缺省**不解**，
  填充器只解 peek 顺序前 N 份、`--prefetch-unpack N` 缺省 1）；D2 = **搬入只在
  `_ensure_payload` 发生一次**（`take()` 只交出 `staged_dir` 并记 `_handoff`）；D3 = **形状分派**
  （`has()`/`take()` 同认 `shards/` 与 `payload.zip`）；D4 = staged **单份上界 256MiB + 独立预算账**
  （超界删 `.tmp`、保留 zip、一行日志）；D5 = P3⑶「`_CACHE_DIRTY` 门」**不做**，只保留 P0 的
  `cache prune` 计时；D6 = 删预取对 payload 的 **blob_cache 死写**（全仓零读点）。
- **备选与否决**：① 形态 B（本 job 后台线程解包、join 在 `load_episodes` 前）—— 否：重叠窗口只有
  0–2s（被 D14 与 `load_episodes` 夹住），却要搬 D14 的位置 = 静默回归入口；② `take(jid, dest)`
  自己搬入 —— 否（评审 P0-1/P0-3）：目标目录与 `_ensure_payload` 的 `job_dir` 同源，会删掉刚搬进来的
  shards，且早于 `run_job` 的 `_result.json` 复用检查 ⇒ 把「算完只差重传」的 job 打回重算（静默白烧
  一轮 PPO）；③ `has()` 只认 `shards/` —— 否：`--prefetch-unpack 0` 会变成 0 命中，§7 三条判据的
  回退档成空头支票（「关掉旋钮」实际关掉的是预取本身）；④ 给 staged 加廉价指纹 —— 否：残余窗口
  （落盘后被外力改）与今天 `job_dir` 里那份解包产物面对的是同一个窗口，而今天它也没有复校 ⇒ 记在案
  不加复杂度；D6 的另一面「保留一份 payload 副本给别的腿」也否决：`take()` 读的是暂存副本，
  payload 的 sha 从不被 `_resolve_blob`/`resolve_blobs_batch` 查（键只有 `opt/init/ref/demo`）。
- **后果**：每 job 的压缩字节写盘 3 份 → 1 份；命中轮的解包不在关键路径上（实测收益待真机读数，
  判据：`other=` 降 ≥2s 且 `ppo=`/本 job `解包=` 不劣化 >5%）。回退档 `--prefetch-unpack 0`
  = 旧行为逐字（既有 `take()` 用例一行未改即是证据）。文本断言护栏抬 FORM 一格（1442 → 1445）。
  剩余待办：解包后体积常数、最优 N、CPU 挤占 —— 真机量。
—— 落点 → `nn-training/remote/{prefetch,download,job_round,worker,train_core}.py` ·
`nn-training/tests/remote/{test_soft_hold_prefetch,test_prefetch_adaptive,test_download_split,test_job_fs_split}.py` ·
`nn-training/tests/test_source_text_assert_budget.py`（基线 1445）

## §2026-10-12-goalnn-est-samples-retire（2026-10-12，`est_samples_per_game` 从课程/CLI 面退场：只留盘上派生量 + 无历史不猜）

- **背景**：`est_samples_per_game`（局均 samples）被做成了**课程必填字段 + 硬门禁 + CLI flag**，而运行期
  真正起作用的是 `trailing_samples_per_game`（盘上历史）——声明值只在**首轮无历史**时兜底。一个「兜底
  估计」扛起了三处结构重量（门禁 / 初批反解 / 局数硬顶），并逼课程作者写一个伪精度数
  （97 份课程写了它；x20 系全是 147 —— 同环境不同腿抄同一个数）。
- **决定**（`plan/rollout-estfree-worksteal.plan.md` v2 §4.2 E1–E6；切片 P0-a 已落地）：
  - **旋钮退场**：字段降**死键**（`DEAD_COURSE_KEYS`，开课侧一行告警）；删门禁 `_check_volume_keys`；
    删 `flat_overrides` 映射；删 CLI `--est-samples-per-game`；`rl_config.schema.json` 的
    `rl.est_samples_per_game` 移入 `retired`（原在 `rl` 白名单）。
  - **派生量保留**：局均 samples 只由 `worker.resume.trailing_*` 派生；`0` = **无估计**（不再是错误）。
  - **无历史不猜**：本机连续路径首小批走 est-free 满铺
    `spread_games_by_stage(width = local_slots → workers → 关数)`，下一批起用**本轮自己**的已结算局
    自校准；云机计划初批钉 `G_s = 1`（`est = 0` 成为合法块值），缺口交给满铺补差批
    （`DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN = 64` 作防跑飞上界）；wave 补波无估计则响亮拒绝（不猜）。
  - **有估计时逐字不变**（在跑腿零影响，有回归钉子）。
- **备选与否决**：① 删 `est_samples_per_game` 字段 —— 否（`CourseConfig` 是 `extra="forbid"` ⇒ 存量
  97 份课程全拒启）；② 剃掉课程文件里的键 —— 否（`course_fp` = 课程文件字节，为一死键抖 D14 血缘不划算
  —— 同 2026-10-09 `workers` 先例）；③ 云机初批改「= 机器宽度」—— 否（初批局数进 `argv_template`，
  发布期硬断言「条数 == 该轮对集局数」；width 只有节点运行期才有 ⇒ 破计划契约 / `pairs_fp`）；
  ④ 无历史时沿用某个默认 est —— 否（那就是伪精度本身，且会静默改采集量）。
- **后果**：新课不再需要 est；首轮冷启动从「按声明值反解」变成「按宽度满铺 + 自校准」⇒ 按 §15.5 当
  **新实验**（fresh `--out`/`--traj`，不跨改动续跑）；`VOLUME_RULE_V1` **不** bump（与 2026-10-12 满铺器
  先例一致：批构成属训练侧），`seed_rotate` 门控另算（真身份变更，另切片）。后续切片 = no-progress 守卫 /
  B1 批屏障读数 / 拉取式 feeder（决策入 WAL）。
—— 证据 → `nn-training/tests/worker/test_rollout_volume.py`（冷启动 3 例 + 死键判据）·
`nn-training/e2e/test_volume_e2e.py`（10 例判据不变，夹具改「自建 jsonl 历史」）·
门禁 `bun run pygate` 4234 passed / 1 skipped

## §2026-10-12-goalnn-value-gating（2026-10-12，x26-obs it90 诊断的修复：value 门控＋x27 双腿）

- **背景**：x26-obs it90 同局实衰（wr 0.535→0.40），机制 = 冷 critic 吃 pf 后在新 37 列上
  35 倍爆炸（0.0086→0.30）拟合噪声 → advantage 污染 → 暖策略游走；旧列 drift 仅 0.015。
  plan §2.6“无用则学小”在 value 头被证伪。优化器是裸 Adam（零 decay、单组），新列
  wild 生长无任何东西拦。x26 双杀后重开（用户令直接开干）。
- **决定**（D1=零起步：`init_spatial_leg` 加 `drop0` 模式，value 新列纯 0——0.01 噪声起步
  正是爆炸源；policy/fc 尾照旧随机的（良性 2–4x）；D2=后步乘性收缩 `w *= (1-ρ)`，
  只拴 `value_anchor_slices()` 列片（腿 A [128,165)／塔整头／legacy 空），clip+step 之
  后 ⇒ 不吃 clip 配额、不进 Adam 矩、不读 RNG（ckpt 精确复现）；ρ = 课程键
  `value_anchor`（0.0 = 关，缺席走 getattr 缺省；0.001 首猜：w=0.05 处收缩 5e-5/步 ≥
  实测漂移 3e-5/步；**不进** corpus_identity_fp，优化器语义前科同款）；
  D3=监控 `value_new_rms` 无条件记录（对照腿 ρ=0 权重恒等但照样看见膨胀），四白名单
  透传（engine stats→train_core→worker→loop_transport→events 行）；
  D4=双腿：x27-obs（ρ=0.001）＋ x27-noleash（缺键，单变量 = 加不加绳），共享同一份零起
  it0（sha16 `103afabb6eedd175`，trunk 与 burst220 逐位等）；新家族流 20261021
  （x26 流被死腿消费过段）；it40 双门（obs：kills≥12.8＋rms≤0.05／noleash：rms>0.15
  且 kills<13.5 即确证杀腿），G0/G5/G3 照判）。
- **备选与否决**：① 梯度内加 anchor（loss 里加 L2 项）—— 否（进 clip 配额、进 Adam 矩，
  语义浑浊；后步收缩可预测、可测）；② param group 式 decay —— 否（优化器只认整张量，
  新 37 列切不出来，只能手写片操作）；③ policy 新列同拴 —— 否（2–4x 良性，第二变量，
  留 makerspace）；④ freeze-then-release —— 否（释放门 defaultdict 新超参，重机制备用）；
  ⑤ ρ 网格搜索 —— 否（无预算；ρ=0.001 首猜＋rms 监控 20 轮内见分晓，调参即新实验）。
- **后果**：`course_openable` 双 True；单变量脚本对账零差异（除 `value_anchor` 一键）；
  开课走控制台。修法 lineage：本条是 it90 诊断的执行，无热补丁（§15.5）。
—— 落点 → `worker/models/student.py::value_anchor_slices` ·
`worker/ppo/engine.py::apply_value_anchor_pull` · `biz/course_spec.py::value_anchor` ·
`worker/scripts/init_spatial_leg.py::drop0` · `nn-training/curricula/x27-{obs,noleash}.jsonc` ·
单测 `tests/worker/test_value_anchor{,_course}.py`（先红后绿 17+69+44 例）

## §2026-10-12-goalnn-offline-leg-ppo-attribution（2026-10-12，自主腿跑完的轮计进 dashboard 的 PPO 组：两个身份字段 + 1 条 = 1 job 等价；plan/offline-leg-ppo-attribution）

- **背景**（用户 2026-10-12 报障）：「kaggle 自主 worker 今天完成了 600 轮 ppo 任务，却不显示在
  dashboard 的节点贡献统计里」。三处断链：① hub 落 `offline_artifact` 账本行时**不写任何身份**
  （`store_offline.py` 的两处落点都没有）；② 自主腿的进程身份（`{HOSTNAME}-{uuid8}`）与队列腿的
  **可读名**（`<env>-<link>`）是两套命名，hub 侧变不出可读名；③ dashboard 的 PPO 读面只认 `job_*`
  三类事件，且 `if (typeof jid !== 'string') continue` 把没有 `job_id` 的行整行丢掉。
  这是 `§2026-10-06-goalnn-offline-leg-sampling-exclusion` 那条例外的**前半句欠账**
  （「offline worker 完成的轮算节点 PPO 贡献」——当时只实现了「不算采样贡献」那半）。
- **决定**：D1 = 账本行落**两个**键，分工不同、**永不合并**：`worker` = claim 身份（客户端自报
  优先，否则用**本课 hold 镜像** `store.hold_meta["worker_id"]` 兜底——补传天然发生在
  claim→release 之间；这里**不判活性**：只回答「这轮是谁推的」，不回答「hold 还活着吗」）；
  `worker_name` = **可读名**（`kaggle-o`），**只有客户端自报这一条来源**（拿 claim 身份去填 =
  面板多一行 `3908d414c3ca-…` 假列名）。两者皆缺 ⇒ 键**整个缺席**（不写空串/null）⇒ 既有
  `(未登记)` 口径。D2 = 可读名 = `worker_tag(offline=True, hub_url=…)` = `<env>-o`（本机 `local`，
  **用户裁决 2026-10-12**，取代初稿的 `kaggle-c`）：自主腿走的**就是**离线链路，名字照链路写；
  `kaggle-c` 是同一台盘的队列腿（cloudflared）的名字 ⇒ 面板上**两行**是故意的（链路不同，不并）。
  D3 = 计算点住 `plan_handoff.open_run_context`（`worker_tag` 是 L3，而 `offline_deliver`(L1) /
  `deliver_proc`(L2) 在它下面 ⇒ 只能由 L5 的装配点算，且**与 `run_loop` 同一份代码快照**）。
  D4 = **单位口径：1 条 `offline_artifact` = 1 个 job 等价单位**（队列腿 1 job = 1 轮 it）；
  与 `pool-history.readOfflineLegs`（采样侧剔除同一批 it）是一加一减，**不是**重复计数。
  D5 = 历史 602 行**不修**（用户裁决：hold 已清 ⇒ 拿不回身份）：行原样留在 `(未登记)`，
  不从 hub 日志人工回填、不回溯改写。
- **备选与否决**：① dashboard 去读 hub 的 `/admin/offline` progress —— 否（progress 无身份，
  且是第二事实源）；② 把 claim 身份直接换成 `worker_tag()` —— 否（撞名：同 env 的两台盘会并错行；
  且破 `.worker-id` 的跨会话持久语义，离线盘续领靠它认 `mine`）；③ 只改 dashboard 把 602 行
  显示成 `(未登记)` —— 否（用户要的是**归到 Kaggle 这台**，份额不归人无法定位「哪台在干活」）；
  ④ 让引导侧（`offline_boot`）把 claim 身份经 argv/env 下传 —— 否（**版本偏斜**：`offline_boot`
  每次会话从 GitHub raw 刷新，而 `run_loop`/`plan_handoff` 来自 `code.zip` 快照 ⇒ 新引导器撞旧
  快照 = argparse 崩或静默退化）。
- **后果**：新的 `offline_artifact` 行带 `worker`（claim 身份）+ `worker_name`（`kaggle-o`）；
  dashboard 节点贡献统计的 PPO 组按 `machineOf` 归并后逐轮计入（`kaggle-o` 无冒号 ⇒ 恒等）；
  采样组**一个字节不变**（角色隔离 WC-plan §4.1b）。
—— 落点 → `hub/store_offline.py::_artifact_identity`（两处落点：`<run>/metrics.jsonl` 与课程账本；
新键排在 `**(row if isinstance(row, dict) else {})` **之后** = 客户端不许冒名） ·
`remote/plan_handoff.py::open_run_context`（算名字） · `remote/deliver_proc.py`（`make_deliverer` /
`DelivererProcess` / `_degrade` / `_spawn` 的 `NN_WORKER_NAME` env） · `remote/deliver_worker.py`
（子进程读 env） · `remote/offline_deliver.py`（body + `WORKER_NAME_ENV`） ·
`dashboard/src/server/contribution.ts::readPpoAttribution` + `offlineIdentity`
—— 全文 → `docs/nn/console.md §37`；计划 → `plan/offline-leg-ppo-attribution.plan.md`

## §2026-10-12-prefetch-stage-budget-revert（2026-10-12，撤销 prefetch staged 的 256MiB 字节闸：它从没生效过，还把整档关掉了；实机读数证伪 plan/prefetch-stage-unpack 的 P1-2 与 D4）

- **背景**：D4（`§2026-10-12-goalnn-prefetch-stage-unpack`）给 staged 解包产物设了「单份上界 + 总预算
  256MiB」，超界 ⇒ 删 `.tmp`、保留 zip、一行日志。落地当天 colab 真机日志连打
  `解包 325.7MB / 299.6MB 超 staged 预算 256MB —— 退回字节形状`：本会话 **3/3** 次预取全部回退。
- **实测**（= plan §2.5 一直在等的那个读数）：3,049,376 B → **325.7MB**（≈112×，70 shard）；
  2,564,244 B → **299.6MB**（≈122×，66 shard）。即现行课程的 payload 解包后**每份**都是 300–330MB，
  256MiB 一进门就超 ⇒ staged 路径 100% 不生效。plan §7 判据①要求的 `解包 aMB → bMB / k shard / t s`
  在日志里**零次出现**，job 准备行照旧写 `解包=1.1s → 70 shard（CPU，不含传输）`⇒ 解包仍在关键路径，
  命中轮只省下载（3MB/3.6s）。而这个常数的来源是「比 `job_fs.py:56` 的 c6b 280MB 小一点」——
  plan §2.5 明写「plan 不给数字、先量再定」且警告那 280MB 是**另一门课**、不能外推。
- **决定（D-revert）**：删掉 `PREFETCH_STAGED_BUDGET_BYTES_DEFAULT` 与解包时的单份否决。
  盘上的量改由 **条数 × 实测体积**约束：条数 = 配置整数（`PrefetchStore.staged_max`，缺省
  = 预取槽位数 `PREFETCH_DEPTH_DEFAULT`；`worker.py` 传 `max(prefetch_depth, prefetch_unpack)`），
  体积**只记账**（`_tree_bytes` → `meta.json` / `stats()`）。单位取**槽位**而不是每轮解包份数：
  解包份数是被 `pick_candidates(held=…)` 去重后的前 N 份（上一轮 staged 的还在册就被跳过 ⇒ 每轮
  新解一份、条数跨轮累加），要限的正是这个累加面。（第一版写成 `= prefetch_unpack`（1），当场被
  既有用例 `test_prefetch_adaptive.py::test_a_staged_hit_counts_the_same_in_the_white_bytes_ledger`
  打红：「两份 staged 共存」是既有口径。）
  `prune` 同步改写：staged 超条数**只丢最旧的 staged**，绝不拿 zip 候选偿债。zip 那本账
  （64MB / 按最旧丢 / 仅剩一份也保留）**逐字未动**。
- **备选与否决**：① 只把常数抬到 512MiB —— 否：留着的是一个「来源是猜、迟早再被同一门课碾过」的阈值，
  而体积本来就在同一函数里量得到；② 保机制不动、只在启动器里 `--prefetch-unpack 0` 撤档 —— 否：
  那等于承认 P1 白做，且留一个「看着开着、实际全是回退」的中间态，后续每次读数都会假归因；
  ③ 退役整条 staged 路径 —— 否：真机前身读数是解包 1.8–4.3s/轮（首包 11.8s，294 shard 那门），
  值一个旋钮，缺陷只在闸门。
- **三条被撤销的支撑理由**（记下来免得下次再被同一条说服）：①「体积未知所以要上界」——体积就在否决
  语句上一行的 `_tree_bytes(tmp)` 里，拿没量过的数去否决刚量到的数是把测量结果丢掉；
  ②「它同时是总预算」——`unpack_n=1` ⇒ staged 至多 1 条 ⇒ 「总预算」半边**不可达**，剩下那条 veto
  的唯一效果是让 `prune` 的「全池按最旧丢」坏分支永不触发（自证循环）；③「否则 280MB 课程会合法留盘」
  ——退回 zip 后同一份字节仍在**必走路径**上解包进 `job_dir`（`remote/download.py::_ensure_payload`，
  **零体积检查**）而 `JOB_DIR_KEEP=2` 让它合法长驻，staged 的搬入是 `rename` 不是复制 ⇒ 那道闸
  **不保护磁盘**，只决定 300MB 落在哪个目录；真正的磁盘大头（2×~300MB job 目录 + 256MB code cache）
  从头到尾不在它管辖内。
- **后果**：staged 路径首次真正生效（判据①终于有读数）；盘上 staged 占用 ≤ `staged_max` × 实测体积
  （缺省 3 × ~330MB ≈ 1GB，且每有主即被 `take` 掉 ⇒ 健康会话恒定 0–1 份；搬入时 `rename` 不复制）。回退档 `--prefetch-unpack 0` 语义不变（无 staged 条目时
  `prune` 的 staged 分支恒不触发）。剩余待办不变：**真机量**（`other=` 是否真降 ≥2s、CPU 是否挤 PPO）。
- **落点** → `nn-training/remote/prefetch.py`（常数 / `__init__` / `_unpack_into` / `prune` / `stats` 注释） ·
  `nn-training/remote/worker.py`（`PrefetchStore(..., staged_max=max(prefetch_depth, prefetch_unpack))`） ·
  `nn-training/tests/remote/test_soft_hold_prefetch.py`（删「超上界退回」用例 → 换「体积不设闸只记账」+
  「staged 超条数只丢最旧 staged」；文本断言 FORM 基线不变 1445）—— 全文 → `docs/nn/remote-transport.md §85`

## §2026-10-12-transfer-jitter-tolerance（2026-10-12，抖动是常态：坏签判据加迟滞+衰减、重抽无收益即停、控制面首败不退避；plan/transfer-jitter-tolerance）

- **背景**：kaggle ppo worker 真机 log，用户口径「网络抖动，是很常见的事情！」。同一会话内链路
  3 秒内 3,089 KB/s → 79/51 KB/s，而三条"错误"全是防线把常态当异常：① 参照速率是**会话高水位、
  只升不降**（`wire._min_rate = max(80KB/s, _BEST_RATE/4)`），一次走运把门槛钉在 772KB/s，此后
  任何正常回落都判坏签；② 误报的动作是**丢弃已收从零重来**（hub 侧完全无 Range 支持：hub/ 里
  grep `Range|206|Content-Range` 零命中）⇒ 每次重抽白扔 256KB + 建连，而真机上重抽前后
  79 → 51 KB/s（越抽越差）；③ 控制面 6s×2（`job_lifecycle.CONTROL_TIMEOUT_SEC` ×
  `control_pool.CONTROL_ATTEMPTS`）+ `worker.py` 再睡 5s ⇒ 一次抖动 ~17s 空转，而 `control_pool`
  的设计注释明写「失败不退避」——**实现比设计多赔 5s**。两次控制面超时 2/2 落在 bulk 在飞窗口内。
- **决定（四条）**：① `wire._note_rate` 加**迟滞+衰减**（`WIRE_RATE_STALE_SAMPLES=2`、
  `WIRE_RATE_DECAY=0.5`：更好即抬、连续慢才衰减，直到落回地板）；② `_read_body` 探针改
  **连续 `WIRE_REROLL_PROBES=2` 个点都慢才断连**（原先只判首块）；③ `_get_with_retry` 加
  **重抽无收益即停**（`WIRE_REROLL_GAIN=1.25`：换连接没变好 ⇒ 判链路整体降速，停抽、按当前速率传完）；
  ④ `worker_loop` **首败不退避**（连败计数 `_acquire_fails`，连败才 sleep）。
- **备选与否决**：① 删掉坏签重抽/取消 `WIRE_MIN_RATE` 地板 —— 否：§22 有实测依据（13.5KB/s 的连接，
  3 秒后另一条 ≥120KB/s），本批只加迟滞与收益判据；② 用「读时按时间过期」做参照衰减 —— 否：
  既有注入点只有 `_BEST_RATE` 一个标量（`test_wire_reroll` 的 autouse 夹具就靠 `wire._BEST_RATE = 0.0`
  清场），任何时刻戳都会在同进程用例间串味 ⇒ 改用**样本计数**（标量归零后不可能误触发）；
  ③ 直接把控制面死线放宽到 12s+ —— **否（本轮发现的自伤）**：控制面请求的墙钟同时喂
  `bulk_sched._p0_ms`，而 `yield_worth_it()`（阈值 `YIELD_GIVEUP_P95_MS=10_000`）用它的 p95 判
  「让路买不回来 ⇒ 降为 never」⇒ 放宽会抬高 p95、反而更早关掉让路。先解耦指标再谈放宽（plan C）。
- **后果**：抖动期不再每次下载都误判重抽（连续 2 个慢样本后门槛开始衰减，约 1 分钟落回地板；恢复瞬时）；
  单点慢不再断连；一次"抽了没用"即止（最坏从 3 次 × 256KB 降到 2 次）；acquire 一次失败省掉 5s 空转。
  模块级状态清点 +1（`wire._stale_samples`，`test_worker_state_contract` 的清单已同步）。
- **留到 plan**（`plan/transfer-jitter-tolerance.plan.md`，顺序 A→B→C）：**A** payload GET 的
  Range 断点续传（让 `wasted` 恒为 0；前置：定 `payload_sha256` 的校验口径）· **B** 上传的让路点
  （result POST 的 body 现在是 urllib 一次 sendall，零让路；难点是代理装配，须先补 parity 用例）·
  **C** 控制面死线自适应（前置：`_p0_ms` 口径与"放宽后的等待"解耦）。
- **落点** → `nn-training/remote/{wire,http,worker}.py` ·
  `nn-training/tests/remote/{test_wire_reroll,test_worker_state_contract}.py`
  （FORM 文本断言基线**不变** 1445：新用例全用行为判据）—— 全文 → `docs/nn/remote-transport.md §86`

## §2026-10-12-transfer-jitter-tolerance-abc（2026-10-12，plan A/B/C 落地：payload 的 Range 断点续传 · 上传让路点 · 控制面死线与 P0 指标解耦）

- **背景**：§2026-10-12-transfer-jitter-tolerance 只治了「判据误报」与「无收益还抽」，把三件结构性
  的事留在 `plan/transfer-jitter-tolerance.plan.md`（A→B→C）。本条是那三项的落地：**抖动是常态
  ⇒ 重试的代价必须趋近 0、控制面不许在任何窗口里裸奔**。
- **A（Range 断点续传）**：hub `GET /jobs/{id}/payload` 认 `Range: bytes=N-` ⇒ `206` +
  `Content-Range`，`_bytes` 无条件带 `Accept-Ranges: bytes`，起点不可满足 ⇒ **回落整份 200**（不是
  416：客户端没有「从头再来」的分支，416 会被它当成瞬时失败去退避重试）。worker 侧 `_get_with_retry`
  把 `WireSlowError.partial` 并进跨尝试累积缓冲、下次带 `Range`；**三个口径分开**（进度/共 N 用
  累计、完整性只比本段 `Content-Length`、速率与剩余用本段）。
  记账口径随之改：一次重抽**不再**默认记 `wasted`（那一截被续用），只有「服务端忽略 Range」才记
  （新函数 `_wire_note_resend`：**只加 wasted、不加重抽次数**——次数与浪费是两个事实）。
  兼容：worker 先升（老 hub 回 200 ⇒ 走「忽略」那条＝今天的行为）；hub 先升（老 worker 不带 Range
  ⇒ 一直整份）⇒ 不需要同时升。
- **B（上传让路点）**：`pace` 原先只接在 `_read_body`，result POST 的 body 由 urllib 一次 sendall
  ⇒ 整个上传窗口零让路点。做法**不动代理装配**：把 body 交成**分片生成器** + **显式 `Content-Length`**
  （⚠ 不给长度时 `http.client` 会改用 chunked，而 hub 只认 Content-Length），片间调 `pace()`。
  `post_result` 早就传了 `pace=_bulk_pace(…)`（`job_lifecycle.py:590`）⇒ 调用点零改动。
  备选与否决：自己用 `http.client` 写上传（要重做 CONNECT 隧道/绝对 URI 的代理装配，`control_pool`
  那份是控制面专用且有护栏）—— 否，收益相同而风险高一个量级。
- **C（控制面死线）**：**先解耦指标再放宽**。控制面请求的墙钟同时喂 `bulk_sched._p0_ms`，而
  `yield_worth_it()`（`YIELD_GIVEUP_P95_MS=10_000`）用它判「让路买不回来 ⇒ 降 never」，前提是
  「P0 从不排队」⇒ 把「我们允许它慢」的等待记进去只会更早关掉让路（自伤）。做法：`control(sample=False)`
  仍计入在途/让路、但不进分位表；`_request` 在 `control_path ∧ 非流式 ∧ 有 bulk 在飞` 时放宽
  `CONTROL_TIMEOUT_BULK_FACTOR=2.0` 倍并把尝试次数收成 1 ⇒ **最坏阻塞与旧行为持平**（旧 6s×2，新 12s×1）。
- **后果**：抖动下的重抽不再给链路加税（`wasted` 恒 0）；上传窗口内控制面不再裸奔；控制面死线自适应
  而 P0 自停判据不受污染。**接口新增**（都 additive）：`parse_range` · `_bytes(extra_headers=)` ·
  `WireSlowError(partial=)` · `_wire_note_resend` · `_paced_body` · `control(sample=)` ·
  `CONTROL_TIMEOUT_BULK_FACTOR`。
- **落点** → `nn-training/hub/{blob,http_face}.py` · `nn-training/remote/{http,wire,worker,bulk_sched}.py` ·
  `nn-training/tests/hub/test_payload_range.py`（新）· `nn-training/tests/remote/{test_wire_reroll,
  test_body_transfer_guard,test_bulk_sched}.py` —— 全文 → `docs/nn/remote-transport.md §87`

## §2026-10-13-goalnn-x27-frozen（2026-10-13，x27 双腿冻结 + obs-v5 感知增维退场：panel/G5/接线三证据）

- **背景**：x26-obs it90 诊断（冷 critic 新 37 列 35 倍爆炸 → advantage 污染）开的修复双腿
  （x27-obs ρ=0.001＋x27-noleash 无绳，共享零起 it0），跑到 it185/187：rms 6× 分叉
  （0.06 vs 0.35）但 kills/胜率零分叉；it40 门早因遥测断档（it34/35–75/77 精确零，旧 PPO 执行器窗口）作废。
  panel 终判（x27-obs it100 × 160 对）：通关 52.5%（G0 80% miss）；
  G5 同对 it0→it100 通关 86→84、杀敌 p=0.503、承伤 p=0.996（总账零）；
  接线测量 policy 双头新列 +1.5–4%（冻住）vs value 0→0.3（天翻地覆）——行为由 policy 解释，value 遥测无预测力。
- **决定**：D1=obs-v5 系列冻结（C 类先验"三次阴性"+本轮=四次阴性，退场；corr/wallDist 几何类转专场，
  空旷场无信号 A6 −0.93 为证）；D2=x27 双腿结课（it300 不跑，G0 大考免；x26-obs 主腿同停；
  x26-tower 另案）；D3=`policy_new_rms` 进常规遥测（与 value 并列；40 轮不动新列即杀线）+ rms 精确零告警；
  D4=下一发子弹 D01 账单 / W08 顶住二选一短腿（单变量、≤80 轮、it40 行为门、自动 halt，另案，用户拍板）。
- **备选与否决**：① 跑满 it300 —— 否（52.5% vs 80%，大考必 miss，省 ~8h 双腿算力）；
  ② 转专场 —— 否（规则：任一 miss 即冻结，三门全过才转场）；③ x27-it100 vs burst-it220 统一战 ——
  否（冻结方向不配统一战；基线仍挂 burst-it220）；④ 加剂量续命（wThreat×2 等）—— 否（第二变量 +
  D03 已平证明税生效，再加是换题不是续命，走 D4 短腿）。
- **后果**：C 类退场、几何类转专场；教训节即本条，全文 → `docs/nn/experiments.md` §85；
  证据 `tmp/panel-x27obs-{it100,it0}.jsonl` + `tmp/compare-x27obs-{it100,g5}.txt` +
  `tmp/smart-x27obs-it100.txt` + `tmp/means-x27obs-it100.txt`。

## §2026-10-13-goalnn-x28-d01w08-design（2026-10-13，x27 冻结后双腿：D01-nearSq×1.5 + W08-wBlock×4；panel 门 + 80 轮封顶）

- **背景**：panel 终判（§85）：D03 已平（威胁税功劳）而 D01 2×/D02 3×（"少进会了、躲开不会"）；
  W08 6× 全场最大 cliff。E2（hurt 叠加）死在 it13 无 verdict、tmp 账本已清——本次不重问 hurt
  （收尸另案），问 nearSq（D09/D05 家族最贴的现存税）与 wBlock 加量。
  校准（x27-obs it100 日常 eval n=400，totalEV/局 81.08，killEV 45.80）：
  nearSq −4.37/局 dense（Pzero=0.00）→ ×1.5（28→42）−6.56/局（≈威胁税量级）；
  block −0.52/局 dense（Pzero=0.10，中位 8 tick，非 burst 式稀疏病）→ ×1.5 只动总 EV 0.3%
  （顺势疗法，null 不可读）⇒ 取 ×4（110→440）−2.09/局（≈hurt 税量级）：此剂量下 null =
  方向死（burst 税级强 null）。precedent：三加压腿 ×1.5 档；x20adv-plus wHurt ×3.06 单步大 bump。
- **决定**：D1=双腿并行（GPU 交叠白赚）：`x28-d01`（wNearSq 42.0）+ `x28-w08`（wBlock 440.0），
  起点同为 x27-obs.it100（sha16 `5e6eac2c51c75aad`，165 宽直装），新家族同流 20261022，
  其余逐键同 x27-obs（含 value_anchor 0.001 继承；公式串逐字等，已脚本对账：每腿仅一键差）。
  D2=it40 panel 门（160 对，Δ 基准 = 已存 `tmp/panel-x27obs-it100.jsonl`）：
  d01 腿 panel-D05 ≤65.6（−15%）＋ D09 不反向；w08 腿 panel-W08 ≤14.7（−15%）；
  双腿 kills≥12.8 护栏＋80 轮封顶＋自动 halt，终点 it80 全表终判、不考 G0。
  D3=policy_new_rms **只读上报、不判门**（税经旧列起效是正常路径；接线杀线只配扩列腿——
  收回冻结令里"第一次实战"的判门口径，遥测本身已上线）。
- **备选与否决**：① wBlock×1.5 —— 否（0.3% total，null 不可读）；② wHurt 加量 —— 否
  （E2 的未决问题，h4 尸体未收，重问占预算，另案）；③ wThreat 加量 —— 否（D03 已平，
  加价方向已死，dist 先例）；④ wDmg 加量 —— 否（−2.8pp 实付代价已知）；⑤ W08-episode-mean
  形态税 —— 否（无列；先补列再定价，另案）；⑥ policy 接线判门 —— 否（见 D3；上报供归因）。
- **后果**：`course_openable` 双 True；`nn-training/curricula/x28-{d01,w08}.jsonc`；
  开课走控制台（双腿并行）。E2/E3a 无 verdict 死亡记卫生欠账（tmp 账本已清，weights 停 it13）。

## §2026-10-13-code-zip-role-trim（2026-10-13，`code.zip` 按角色裁剪：量法=云端入口 import 闭包，2.796 MB → 1.221 MB；`levels/` 是硬依赖不许排）

- **背景**：`plan/aistudio-transfer-hardening.plan.md` C1 / `docs/nn/remote-transport.md` §80 未取项 ⑤
  「code.zip 按角色裁剪」——门槛写的是「要先量一次冷启动里真正被 `import` 的模块占包体多少」。
  用户 2026-10-13 点名：`curricula` `e2e` `hub` `ladder` `levels` 应不参与打包、`conftest.py` 也排除，
  `tools` 一并排查。
- **量法（唯一判据，可复跑）**：从 20 个云端节点入口（`remote.offline_boot` / `run_loop` / `worker*` /
  `train_core` / `notebook_*` / `bc_job` / `offline_eval` / `worker.iter_*` / `worker.cli` …）用 `ast`
  抓全部 import（含函数内延迟导入）做 BFS。**闭包只落到 `biz` / `common` / `remote` / `worker`**；
  `remote/` `worker/` `common/` 里 `from hub` / `from trainer` / `from tools` / `from e2e` 零命中。
- **决定**：`pack_code_zip` 的 `_exclude_dirs` 增 `e2e` `hub` `ladder` `curricula` `tools` `trainer`，
  `_exclude_files` 增 `conftest.py`（根）。实测 **2.796 MB → 1.221 MB（−56%，214 文件）**；
  闭包恰好 = `{biz 13, common 31, levels 26, remote 41, worker 103}`。
- **★ `levels/` 刻意不排（唯一反例）**：课程里的 `"level"` 引用**没有**内联进快照
  （`trainer/bc_loop.py:695-696`、`remote/bundle.py:167-169` 都是原文入包），云端
  `remote/train_core.py:201` 的 `load_course` 会走 `biz/course_resolve.load_course:84-111` →
  `resolve_level` → **`LEVELS_DIR`**。把该目录指空实测：全 181 门课 **131 门 `FileNotFoundError`**，
  含现行 x20 线（`x20-floor` → `level: ladder-c20-lives1`）。它只有 15 KB 压缩，不值得冒险。
- **备选与否决**：① 一并排 `levels/` —— **否**（见上，131/181 破）；② 只排点名的 6 项、不排 `tools/`
  —— 否（闭包命中 0，135 KB 白传）；③ 不排 `trainer/`（保守档，2.299 MB）—— 否（闭包命中 0，
  且已核 TPU/Colab/Kaggle 自主段不用它：`trainer/eval_a_once.py:43` `REPO=parents[2]` 结构上要求
  住在仓库里，`remote/bundle.py:342` 明写云机续跑「不需要 hub、不需要仓」）；④ 逐文件白名单再剔
  `worker/` 62 个死文件 + `remote/` 10 个 + `biz/` 6 个 —— 否（名单会腐，见 §5 教训；
  留作下一刀，先要 5%+ 以上的收益证据）；⑤ 顺手把 `hub/smoke_loopback.py` 的 `PYTHONPATH=ROOT`
  去掉当哨兵 —— 否（本次不动它，但已在 §88 登记「冒烟绿 ≠ 裁剪没排错」）。
- **后果**：`remote/hub_client.py::pack_code_zip` 名单 + `tests/remote/test_hub_client_code_zip.py`
  （`KEEP` 改精确集合相等、新增 `ROLE_TRIMMED` 与 `test_role_trim_drops_local_only_dirs_but_keeps_levels`）；
  全文 → `docs/nn/remote-transport.md` §88。**残余已知依赖**：`curricula/` 只剩课程 `gates`
  按名引用另一门课这一条窄路径（`biz/course_spec.py:448-450`），全 181 门只有退役的 `c6b-margin` 踩到，
  失败形态是响亮 `FileNotFoundError` 而非静默。

## §2026-10-13-ladder-template-trim（2026-10-13，ladder 模板库裁剪：删 20 门 ladder-cNN 课程 + 13 个只被它们引用的关卡；★ ladder 工厂能把它们静默生成回来）

- **背景**：用户 2026-10-13 指令「`curricula/` 下 `ladder-cNN.jsonc` 全部移除；它们引用的
  `levels/*.jsonc` 如果没被其它课程引用，一并移除」。
- **判据**：只按**课程引用**（`curricula/*.jsonc` 的 `"level"`），且**先删课再重算关卡存活引用**。
- **决定**：删 33 个文件 —— `curricula/ladder-c01..c20.jsonc`（20）+
  `levels/ladder-c08..c20.jsonc`（13，删课后引用数归 0）。**保留**：`levels/ladder-c01..c07`
  （各 3/3/11/20/6/1/1 门别的课在用）· `ladder-c20-lives1(.jsonc/-powered)`（66 / 1 门）·
  `arena2-acbc / arena4 / arena6`。
- **★ 会重犯的坑（2026-10-13 当日更正，方向写反了）**：原文写「`biz/ladder_factory.py` **从**
  `ladder/plan.jsonc` 生成 …」—— 反了：`ladder/plan.jsonc` 是工厂的**输出**（`generate(plan_path=…) →
  write_jsonc`），工厂的**唯一输入**是 `levels/arena4.jsonc` + 钉死常量。而且该工厂同日已退役
  （见 §2026-10-13-ladder-factory-retire）⇒ 这条"跑一次 generate 静默复活 20 门死课"的路径**已不存在**。
- **备选与否决**：① 一并删 `levels/ladder-c01..c07` —— 否（各有别的课在用）；② 连
  `ladder/plan.jsonc` 一起删 —— 否（当时判它是"工厂的输入"，**该判断已在同日的更正里作废**；
  它现在是孤儿产物，是否清理由 §2026-10-13-ladder-factory-retire 另行处理）；③ 顺手清
  `ladder/LEDGER.jsonc` 与 `rl-config.json` —— 否
  （已核：前者键只有 `ladder-c01..c07` + `x2`/`x2-acbc`/`x3`，无 c08+；后者 `courses` 只有
  `x21-psh-k5`/`x28-d01`/`x28-w08`，本就没有 ladder 课）；④ 一并删 `levels/obstruction.jsonc`
  —— **否，且已更正**（先前误记为"全仓零引用、比 c08–c20 更死"）：它是**阶段二障碍/基地专场的预备关卡**
  （`plan/obs-v5-vector-gap.plan.md:95/364`），当前无课程引用是**两阶段纪律**（`DECISIONS.md:4858`
  用户 veto 提前开腿）⇒ 判据是「计划中的引用」，不许删。教训：判零引用时搜索面必须含 `plan/` 与 `DECISIONS.md`。
- **后果**：`levels/` 26 → 13 个文件；`curricula/` 181 → 161。验证 `tests/biz/` + `tests/tools/`
  = 354 passed / 6 skipped（Windows-only skip）+ 课程/关卡相关 12 文件 162 passed。文件均已跟踪
  ⇒ `git checkout --` 可恢复。全文 → `docs/nn/training-stack.md` §32。

## §2026-10-13-ladder-factory-retire（2026-10-13，退役 `biz/ladder_factory.py`：它只是刚删的 ladder 模板库的生成器；顺带更正上一条的事实错误 + 修两条护栏）

- **背景**：用户 2026-10-13 问「`biz/ladder_factory.py` 干啥用的？只为生成 level 文件的话，也删掉吧」。
- **核查（决定 = 删）**：它是 I3「阶梯工厂（数据不是代码）」的**一次性生成器**，产物 = `levels/ladder-cNN.jsonc`
  （20）+ `curricula/ladder-cNN.jsonc`（20）+ `ladder/plan.jsonc` —— 恰好就是上一轮删掉的那三族。
  唯一输入 = `levels/arena4.jsonc` + 钉死常量；唯一消费者 = 它自己的测试。
  `worker/kickstart_burn.py:13` 只在 docstring 引它的 I2 结论，不是依赖。
- **决定**：删 `biz/ladder_factory.py`（447 行）+ `tests/biz/test_ladder_factory.py`（269 行）；
  其中**唯一不依赖工厂**的判据迁走：`test_arena_retired_single_source`（arena2/arena3 不许复活、
  arena2-acbc 必须在）→ `tests/biz/test_course_resolve_split.py:213`（同族主题；也保住
  `docs/nn/test-contract-map.md` 登记的 T2b 契约不静默消失）。同步清 `biz/__init__.py` 模块表那行、
  `nn-training/README.md` 的 `biz/` 树（12 → 11 个模块）、test-contract-map 的测试路径。
- **★ 更正上一条的事实错误**：`§2026-10-13-ladder-template-trim` 写「工厂**从** `ladder/plan.jsonc` 生成 …」
  —— 方向反了，`plan.jsonc` 是工厂的**输出**。该条"会重犯的坑"因此整条作废（复活路径随工厂一起消失）。
- **`ladder/plan.jsonc` 一并删（用户当日拍板）**：工厂的第三个产物，删工厂后即孤儿 —— 全仓 grep 无写入者、
  读取者 0（dashboard / `tools/sim/*` / `biz/ladder_ledger.py` 读的都是同目录的 **`LEDGER.jsonc`**，
  那是活文件，保留），且内容列着 20 个已不存在的关卡。删后 `nn-training/ladder/` 只剩
  `LEDGER.jsonc` + `reports/teacher-probe.json`。
- **修掉的两条护栏**：① `tests/test_layering.py:428` 的 `len(biz)+len(worker) >= 66` 是下界，
  实测 73 → 72 **仍过** ⇒ 不必改；② `tests/test_source_text_assert_budget.py` **红了**（1092 > 1091）
  —— 真因是**上一轮** code.zip 裁剪里新加的两条断言被口径 B 的 `.py` 门误伤（断言对象是 **zip 成员名**，
  与源码文本无关）。修法**不抬基线**（护栏明令禁改数字）：收件名单经 `_expected_members()` 转一手、
  被裁目录改用不含 `.py` 的 `TRIM_TOPS` ⇒ 该文件 B 由 2 降 0。复测全仓 **A/B = 1437/1090**。
- **故意不动**：`curricula/x{2,3,4,5,6}-rebirth.jsonc`、`x20-snowball.jsonc` 注释里引
  `biz/ladder_factory.py:228` 当"先例" —— **课程文件字节 = `course_fp`**，改一个注释字就换指纹，
  正在跑的腿（x28-d01/x28-w08）按 D14 对账 ⇒ 一律不碰（死引用留档）。
- **备选与否决**：① 留工厂当"以后还要 ladder"的备份 —— 否（git 历史里 447 行随时可取回
  `git show`；留着 = 又一个会静默写真仓库的入口，且它写的全是已删文件）；② 抬文本断言基线一格
  —— 否（护栏明令「不许直接改数字」，且真因是可绕开的误报，不是"只能看文本"）；③ 把
  `test_arena_retired_single_source` 连同测试文件一起删 —— 否（已登记的 T2b 契约，删了是静默丢契约）；
  ④ 顺手删 `ladder/plan.jsonc` —— **当日已按此办**（用户拍板；见上「一并删」）；⑤ 一并删
  `levels/obstruction.jsonc` —— **否**，理由与自我更正见上一条 `§2026-10-13-ladder-template-trim` ④。
- **后果**：`biz/` 11 个模块（12 个 .py 文件）；`tests/biz/` 少 19 条用例、多 1 条迁入；
  `nn-training/ladder/` 只剩 `LEDGER.jsonc` + `reports/`。
  验证 `tests/test_layering.py` · `tests/test_source_text_assert_budget.py` · `tests/biz` · `tests/tools` ·
  课程/关卡相关 · `tests/remote/test_code_snapshot.py` = **388 passed / 6 skipped**。
  全文 → `docs/nn/training-stack.md` §33。

## §2026-10-13-goalnn-x28-it40-verdict（2026-10-13，x28 双腿 it40 双杀：D05 死 null＋W08×4 强 null；40 轮止损生效）

- **背景**：设计条 §2026-10-13-goalnn-x28-d01w08-design 的 it40 panel 门（160 对，
  Δ 基准 = 已存 it0-panel；panel-resim ~3min/腿）。双腿训练全程健康（KL 0.010–0.019，
  无 halt；policy-rms +0.7%/+0.6% 上报，不判门）。
- **决定**：D1=d01 腿杀（主 D05 均值 −1.1%、同对中位 p=0.818 死 null，门线 ≤65.6 未到；
  次 D09 p=0.093 方向对、不显著；D01 同对 p=0.042 **显著恶化**、通关 84→73——
  税动姿态（D09/A05/A06 齐朝远）不动目标伤总账，不加量）；
  D2=w08 腿杀（主 W08 +10% 反向、同对 p=0.136 null，门线 ≤14.7 未到；
  杀敌率 p=0.039 **显著恶化**；×4 强 null = 方向死，不加量；后继 episode 列先行）；
  D3=双腿结课归档（40 轮止损生效：门判只烧一轮 panel，无 overrun）；
  D4=D05 直球另案（hurt 叠加/E2 收尸，用户拍板；脱离税弱备选）。
- **备选与否决**：① nearSq 加到 ×3 —— 否（×1.5 已咬住姿态，D05 脱钩与剂量无关；
  加量只多伤总账）；② wBlock 加到 ×8 —— 否（×4 已是预注册强 null，再加是换题不是续命）；
  ③ 跑满 80 —— 否（门后 40 轮 = stack7 式空烧，门禁即为此设）；④ D09 marginal 转正续跑 ——
  否（p=0.093 非显著，且 D01 反向恶化）。
- **后果**：全文 → `docs/nn/experiments.md` §86；课程文件状态＋尾部判决已写回；
  证据 `tmp/panel-x28-{d01,w08}-it40.jsonl` + `tmp/compare-x28-{d01,w08}-{it40,delta}.txt`。

## §2026-10-13-goalnn-y1-restart（2026-10-13，新纪元 y 系列第一课：c01 从零爬梯；单腿；出生全税＋0 伤门）

- **背景**：x27 冻结（policy 冻住）＋x28 双杀（税动姿态不动 D05）后，用户令随机权重从头爬梯。
   chasing 问题：c01-04 无税时代（~6 周）埋下站桩对换（D05/D09/D01 同一套经济学）；
  tower 带 trunk 205 轮到 0.25、x1 起跑线 c01 仅 4% ⇒ rush 稀疏烧钱；rush＋重税 = 恐惧陷阱
  （x1 原话）；tower 出局（obs-v5-B 已冻结＋ladder 无几何信号＋家在专场）；165 宽保留不退 137。
- **决定**：D1=单腿 `y1`（level ladder-c01；起点 `weights/y1/scratch-init.json`，
  `init_scratch_weights.py` 加 `--policy-extra`（建 165 档，缺省行为不变）＋
  `--zero-value-new`（D1 同款纯 0 value 新列；sha16 `1211d2f9a9f0c7d5`；policy ~0.001，
  value 新精确 0）；kickstart 关；value_anchor 0.001 出生安全件；双 rms 活线，
  policy 读数反转（scratch 出生 flat=没学，长大=接线）。
  D2=出生价格逐字继承 x27-obs（17 项全税 c01 全开；x28 死剂量不带入；回溯/E2 不带入，另案）；
  D3=it40 硬门（D01≤it0×0.5＋kills≥it0×4〈it0<0.5 改≥2.0〉，pass 不判门；fail 即杀）＋
  it80 晋升门（D01≤it0×0.3＋pass≥50% ⇒ y2）；终点 it80，不考 G0；新流 20261023；
  ent_break 0.25（scratch 防熵塌）；eval 4×100（c01 短局便宜）。
- **备选与否决**：① 直冲 c20 —— 否（稀疏＋恐惧陷阱，见背景）；② tower 并行 —— 否（见背景三条）；
  ③ 退回 137 宽 —— 否（死列无害＋单形状＋leash，零收益开分叉）；④ 回溯进出生包 ——
  否（缺列，block 出生；列落地即在 plastic lineage 上审判）；⑤ 朴素 kaiming ——
  否（x1 自锁事故；必须走脚本＋断言）；⑥ panel tripwire —— 否（c01 无人类 panel 对；
  日常 eval 足够；panel 随 c20 到达）。
- **后果**：`course_openable` True；`nn-training/curricula/y1.jsonc`＋起点权重文件；
  开课走控制台。旧纪元基线（burst-it220）保留作史，不再续跑。

## §2026-10-13-goalnn-y1simple-control（2026-10-13，y1 的并行价格体系对照：x1 极简复刻，同起点同流）

- **背景**：y1 出生即开 17 项全税——这套两个月攒的价格体系是必需还是包袱（恐惧陷阱），
  y1 单腿答不了。x1-rebirth-a2 用极简价干到 100%/0 伤的先例让这题真有悬念
  （用户拍板双腿并行）。无绳对照不配（x27 已答：零起点仍炸 0.35）；tower 不配（已判死）。
- **决定**：D1=`y1-simple`（reward 逐字 x1：公式＋参数＋terminal −5/−3；
  其余逐键同 y1：**字面同一份 scratch it0**、同流 20261023、同 level/iters/优化器/K/eval、
  kickstart 关、anchor 0.001）；D2=门与 y1 同门同基线（it40 D01≤it0×50%＋kills≥it0×4，
  it80 晋升；两腿各判）；D3=双分支预注册：simple 持平/赢 ⇒ 17 项是包袱（出生包减负）；
  simple 莽化（D01 不降）⇒ 成熟税必要实锤；y1 恐惧化（kills 不起＋D01 极低）⇒ 税太狠减负。
- **备选与否决**：① E2 式 hurt 叠加当对照 —— 否（答的是另一题；D05 直球已排队，先停手令下冻结）；
  ② ent_break 对照 —— 否（ trivial，不值一腿）；③ panel 门 —— 否（c01 无人类 panel 对，同 y1）。
- **后果**：`course_openable` True；`nn-training/curricula/y1-simple.jsonc`；
  开课走控制台（与 y1 双腿并行）。

## §2026-10-13-goalnn-y1-it40-verdict（2026-10-13，y1 it40 门 fail＋it42 熵熔断 ABORT；死因探索死亡，价格无罪）

- **背景**：设计条 §2026-10-13-goalnn-y1-restart 的 it40 门（D01≤it0×50%＋kills≥0.5）。
  y1 跑到 it42：D01 主线 1.3 通过；kills 0.345 < 0.5 **fail**——greedy 峰 it5–it40
  八连 eval 400/400 逐局逐字同（wver 八变），熵 1.81→0.17，梯度死（pol −0.0002/KL 0.0003）。
  系统独立见证：it42 `circuit_break`（entropy≤0.25 连 8 轮）→ ABORT → run_complete；
  熔断器按设计工作（8 连发慢引信；`worker/loop_guards_trip.py`），非漏映射。
  simple 腿 it38 存活（熵 1.65，R~0.14）：同 138 峰 it30 到达（400/400 与 y1 逐字同——
  c01 贪心吸引子唯一），税加速发现 6×（it5 vs it30），也加速收敛。
- **决定**：D1=y1 本配置杀（门 fail＋熔断双判决；跑满 80 无意义）；D2=病因定为**探索死亡**，
  出生价格无罪（D01 −99%＋138 clears＋开火不塌三条出罪）；D3=y1-simple 不动（门待其 it40，
  熵活着，仍有展开可能）；D4=entropy-rescue 另案（y1-it30 fork＋探索压力，用户拍板；
  档在峰已找到＋熵 alive 处干预，不去停尸房）。
- **备选与否决**：① 跑满 80 —— 否（梯度已死，stack7 式空烧）；② 改读 rollout 门 ——
  否（事后换表，门禁即为此设；rollout/日常 eval 口径差异已记）；③ simple 同杀 ——
  否（熵活＋门未到，无罪推定）；④ 原地加熵续跑（不断腿改参）—— 否（§15.5 不跨改动续跑；
  走 D4 新腿）。
- **后果**：全文 → `docs/nn/experiments.md` §87；y1 课程文件状态＋双判决已写回；
  证据 training_log（it42 circuit_break 行）＋八连 eval＋双 rms 轨迹。

## §2026-10-13-goalnn-y1long-design（2026-10-13，y1/y1-simple it40 timeout 强税续跑：terminal timeout −12.0；自杀 tripwire 预注册）

- **背景**：用户令不磨了，timeout 放大 4 倍。基线三方 400/400 逐字同（y1-it40＝simple-it35＝
  simple-it40；138 通关＋262 timeout＋0 gameover）。y1 熵塌（赌反差解冻），simple 熵 alive
  （测税效应）；双腿对照看起点状态的影响。−12 口径 = simple 既有 −3.0 的 ×4，两腿同价。
- **决定**：D1=`y1-long`（y1.it40起点，sha16 `56344888e8974f3a`；terminal {}→{timeout −12.0}，
  余同 y1）；D2=`y1-long-simple`（simple.it40起点，sha16 `01c0cd679a99eb79`；
  timeout −3.0→−12.0，余同 y1-simple）；D3=四门（timeout≤180＋kills≥138＋
  gameover>10 即杀＋KL/熵掐），iters 40 封顶；D4=同流 20261023（continuation 可比）。
- **备选与否决**：① 死亡共价（timeout＋lives 同加）—— 否（第二变量；自杀由 tripwire 判，
  出现即"诱导自杀" verdict 另案）；② timeout ×2 —— 否（用户定 4x；且 −6 力度相对反差 15 不够看）；
  ③ timeout 时长/采样加权解读 —— 否（无此语义也无机器；−12 终局价是唯一自然读法）；
  ④ episode  oversample —— 否（无机器）；⑤ simple 不同流 —— 否（双腿 rollout 可比，x24 先例）。
- **后果**：`course_openable` 双 True；`nn-training/curricula/y1-long{,-simple}.jsonc`；
  开课走控制台（simple 先停）。y1 已死于熔断，无需动。

## §2026-10-13-goalnn-y1long-maxticks（2026-10-13，timeout×4 落关卡 fork：ladder-c01-long 6000；上条 terminal 设计作废）

- **背景**：上条 terminal-tax 设计已落未开，用户纠正"改 max_ticks 1500"（关卡文件，非激励）。
  三层核查：① 课程侧 override **死路**（`load_course` 四键冲突 raise＋x4/x5 课头"课程侧禁止重写"；
  lives 例外是 player 子字段通道，max_ticks 无标量豁免；内联 stages 也无此键）；
  ② max-ticks-rule（God 200 局/级：c01 胜局最大 1121，+34%，判据≥1.25×）⇒ 1500 有出处，
  6000 = 5.36× 判据外，属任务侧破例（§0.2），用户 override 已批；
  ③ 人类基线安然（God c01 439 拍 ≪1500，从不见上限；人类 panel 全是 c20）。
  代价清单：c01-NN 读数断代（与 1500-horizon 腿不可比，已声明）；rollout ~2.6× 变慢
  （65% 长局；transitions 定额下单轮局数减少）；D01/死亡新变量（4× 暴露/开火）配 watch；
  simple 必须先停（跑中污染门后 it41+ 空烧；其门 fail 未判一并结）。
- **决定**：D1=fork（`levels/ladder-c01-long.jsonc`：与标准逐字节同，唯 name＋max_ticks 两处差；
  标准 c01 不动 ⇒ rule/test/human/旧读数/codezip-set 全安；删 fork 即回退，引用响亮报错）；
  D2=双腿改 fork-level、奖励还原（y1-long terminal 回 {}，simple-long timeout 回 −3.0；
  单变量 = level 一键）；D3=四门（timeout≤180＋kills≥138＋gameover>10 即杀＋D01/墙钟 watch），
  iters 40；D4=simple 判（门 fail by letter＋extension 未批＋被 fork 取代 → 停；
  lineage 由 y1-long-simple 续，熵 alive 遗产不断）；D5=同流 20261023。
- **备选与否决**：① 直接改标准文件 —— 否（rule＋teacher-probe 1500＋human 三处全动；simple 跑中污染）；
  ② 上条 terminal 设计执行 —— 否（用户纠正为准；自杀 math 随设计作废，**本文保留备查，不删文**）；
  ③ 等人类 c01 实测样本 —— 否（God 439 代理证据已够判安全；人采 20 局可日后补，不 block）；
  ④ simple 续跑到 80 —— 否（门 fail＋被取代； Theorems 已死，simple 的慢磨问题由 long-simple 继承）。
- **后果**：`course_openable` 双 True（fork level 生效）；关卡 fork＋双腿文件（已对账）；
  开课走控制台（simple 先停）。旧 c01-NN 读数（y1/y1-simple 1500-horizon）封存作史。

## §2026-10-13-goalnn-y2-prep（2026-10-13，爬梯第二档 y2 预备：c02-long fork＋继承 y1 setup；bc 待 simple.it80，安全锁）

- **背景**：用户令提前准备 y2（继承 y1，改 c02，max_ticks 同到 6000）。c02 结构
  （levels/ladder-c02.jsonc）：6 关（ab/ac/ad/bc/bd/cd 双兵混编），count=2（kills 上限 2.0），
  规则 max_ticks 2100，hard，1 命。y1.it42 熵死无晋升资格（门不过不晋升）⇒ 起点只能是
  simple 线；simple 在 it52 卡住 20+ 分钟无 iteration 行（停滞？控制台查；y2 等 simple.it80）。
- **决定**：D1=fork（`levels/ladder-c02-long.jsonc`：脚本双断言通道，与标准逐字节同，
  唯 name＋max_ticks 6000 两处差；规则值→6000 = 2.86×，override 沿上条）；
  D2=`y2.jsonc`（奖励逐字 y1；level c02-long；eval 2000-2005×70；iters 80/max 24；
  it40 门 D01≤it0×50%＋kills≥1.0（上限 2.0 之半）＋pass 参考；it80 晋升 D01≤30%＋pass≥50% ⇒ y3）；
  D3=bc 待回填安全锁：`y1-simple.it80.TBD.json`（不存在 ⇒ 靠 openable 拦？**拦不住**——
  `course_openable` 不查 bc 存在性（已实测 True）。防线 = 本头注＋控制台指令（bc 未填不开）＋
  缺失 bc 在 seeding/packaging 侧按全仓惯例响亮失败（kickstart 缺 bc 即 SystemExit 同族）；
  openable 查 bc 列为 P2 工程欠账（本轮不动 trainer，他人地盘）；
  scratch 占位被否决（静默错跑比响亮失败坏）；simple.it80 落账后回填精确文件名＋sha 再开）；
  D4=同流 20261023（continuation）。
- **备选与否决**：① y1.it42 当爹 —— 否（熵死权重上山，违晋升纪律）；② scratch 当爹 ——
  否（断爬梯连续性；c01 防御白学；另 simple 线活着时不用）；③ y1-simple 最新 it52 当爹 ——
  否（门看 it80，五十二不是 verdict 点；等落账）；④ c02-long max_ticks 照规则 2100 ——
  否（用户令 6000；与 c01-long 同等待遇，断代声明同理）；⑤ simple 不等直接开 y2 ——
  否（无爹开课＝bc 缺失裸奔，锁死）。
- **后果**：关卡 fork＋`nn-training/curricula/y2.jsonc`（parse＋公式逐字＋openable True〈bc 未查〉）；
  开课条件：simple.it80 落账＋回填＋用户点开课。在此之前 y2 是 locked 预备件。

## §2026-10-13-goalnn-long-stale-worker（2026-10-13，y1-long 双腿跑在旧码执行器：双 rms 恒 0＋it1 头重置＋绳子疑似未装；x27 断档确认）

- **背景**：用户问 eval 恒 66%＋y1-long 熵低＋苟是否免费。查得：① eval 66%＝it0 瞬时任务效应
  （138→264/136，全图齐涨，零训练）＋其后冻结（400/400×3，跨腿亦同）；② 熵：y1-long 0.167
  未解冻（KL 0.0007，反差未生效），simple-long 1.55 alive；③ 苟：timeout 局 threat/hurt/danger
  ≈0、nearSq 266（−1.24/局）、开火 106 发——免费且在试（miss 非躲），wTick 暂不动
  （第二变量；x4 曾有 0.01，kitchen-sink 静默丢，无 verdict；timeout 局先看任务变现）。
  另抓到本条：双腿训练遥测 vrms/prms 恒 0.0000（9 轮）。
- **判定（执行器，非训练）**：零值＋grad~24s（vs 正常 ~45s）＝x27 断档同签名；
  job 归属：longs 全 kaggle-c（11/10），x28/simple 全 aistudio-c（42/52）——**按 worker 分毫不差**。
  checkpoint 直算铁证：y1-long.it1 move/fire 新列 0.049/0.060（相对 y1.it40 的 0.271/1.104
  断崖；旧列行为连续：it0→it5 400/400 同）＝旧架构 worker 静默部分加载（旧列 carried＋新列
  fresh-kaiming）；it1→it9 value-new 0.035→0.066、simple-long 0.080→0.149→0.166
  （8 轮 +87%，x26 区还有 ~8 轮）＝**绳子未装**（leash 同 0.001 在旧码不存在）。
  x27 it35–75 断档（零值＋21s）同案确认（彼时 obs 无绳跑 40 轮，it76 0.215  spike 即出狱读数）。
- **决定**：D1=腿不重启（it0 基线真权重所测＋行为连续；重开=第二次重置，更大混杂）；
  D2=遥测盲区 analytic 补位（本条直算方法；每门必读 checkpoint，不读日志 0.0）＋
  tripwire（任一腿 value-new >0.20 即人工掐，不等 it40）；D3=刷码重派走 hub lane
  （kaggle-c 刷到含 telemetry＋anchor 的 code，腿热迁无权重断点——165 对 165 直装；
  回迁自动把 0.166 拉回，x27 已证 pullback；transport lane  ownership，不在本条动手）；
  D4=门重校准（§6.4：原线 ≤180/≥138 已被任务本身在 it0 击穿→改 it0-fork 基准
  timeout≤110/kills≥264，两腿头注已改）。
- **备选与否决**：① 当 stale 没事继续盲跑 —— 否（x26 区 8 轮即到，无监控无绳＝裸奔）；
  ② 重开双腿 —— 否（D1；it0 基线有效＋行为连续，重开丢任务效应基线）；③ 立刻加 wTick ——
  否（任务变现先行；timeout 局在试（106 发）不在躲，wTick 罚 miss 恐致自杀/恐惧，另案排队）。
- **后果**：本条＋双腿头注门/注记已写；hub lane 待刷码（kaggle-c）；analytic tripwire 由盯盘执行。

## §2026-10-13-goalnn-kaggle-stale-process（2026-10-13，kaggle-c 是没重启过的长寿进程：trainer/hub 重启不管 worker；修法＝重启 worker 不是刷 hub）

- **背景**：用户报"y1-long 开课前刚重启过 trainer 和 hub"。归属全集：y1/y1-simple/x28
  全 aistudio-c（42/52/42，全活遥测）；longs 全 kaggle-c（11/10，全零＋重置）。
  ⇒ 重启范围 = hub（重打包 code.zip ✓新）＋trainer（本地环 ✓新），**不含 worker 进程**；
  kaggle-c 是带着史前代码（早于 telemetry，更早于 obs-v5 165 档——legacy 部分加载
  即 it1 头重置）活到现在的长寿进程；aistudio-c 是新/刷过的（对照干净）。
  次级可能（hub 会话启动后 kaggle-c 未重取包：不断连 resume／anchor 未变／发包静默回退）
  不必先分——修法一致，修完用"rms 活＋grad 回 45s＋不断头"三判据验收，分得清。
- **决定**：D1=修法改为**重启 kaggle-c worker 进程**（新鲜加载当前码：rms＋leash＋165 档
  一次齐；165 对 165 直装无断点；回迁 pullback 已证）；hub 重打包**不需重做**
  （包已新，旧的是进程不是包）；D2=重启前腿不动（analytic 监控＋>0.20 tripwire 继续；
  切忌在旧进程上重开腿——再 dispatch 一次就是第二次重置）；D3=验收三判据
  （下几轮 vrms/prms 非零＋grad~45s＋checkpoint 头连续），任一不过转 hub 缓存排查
  （transport lane）；D4=此前的"刷码"措辞更正为"刷进程"，hub lane 待办改派。
- **备选与否决**：① 再重启 hub/trainer —— 否（包和环已新，病灶在 worker 进程，无效操作）；
  ② 推 GitHub —— 否（kaggle-c 是队列腿走 hub 包，不走 GitHub raw；且本仓永不 push，人推另议）；
  ③ 腿迁回 aistudio-c 跑 —— 否（aistudio 在跑 simple；跨 worker 迁腿改派发，动静大于重启本机进程；
  kaggle-c 重启后自愈， jetsam 不需要）。
- **后果**：待用户/owner 重启 kaggle-c；验收由盯盘执行（下一轮迭代即见分晓）。

## §2026-10-13-goalnn-cache-not-process（2026-10-13，更正上条：kaggle-c 是开课后新起的，旧的是缓存不是进程；修法＝清缓存＋simple-long tripwire 掐停）

- **背景**：用户纠正时序（kaggle-c 开课后才启动）＋ push-gap 实测（origin 仅落后 1 笔
  guard 单测 ⇒ GitHub-raw 陈旧说死）⇒ 上条"长寿进程"判定错，§6.4 更正：
  **旧的是持久缓存（或 pack），不是进程**——新进程＋旧码（早于 telemetry、无 anchor、
  legacy 档）＝取包/缓存命中了史前字节。重启进程本身已被证伪（刚重启完还是旧码）。
- **决定**：D1=修法改为**清 kaggle-c 持久缓存**（删缓存根／版本锚升级二选一，hub/worker lane；
  重启进程单做无效——事实已证）；D2=y1-long-simple **tripwire 掐停**（checkpoint 直算
  it18 value-new 0.2167 > 0.20，x26 区 ~4 轮即到；控制台 halt；课程文件 verdict 已写回；
  无门 verdict，lineage 冻结；复活条件＝执行器修好后另开新腿）；D3=y1-long 继续跑＋盯
  （it18 0.1022 未过线；analytic 每门直算）；D4=验收三判据不变（rms 活＋grad~45s＋不断头），
  判据对象从"重启后"改到"清缓存后"。
- **备选与否决**：① simple-long 跑到 it40 门 —— 否（tripwire 先于门触发是设计本意；
  0.2167 无绳奔 0.30，门前必爆）；② 腿迁 aistudio —— 否（同上条；且 simple 占着）；
  ③ 推 GitHub 更新 workers —— 否（origin 仅差 1 笔；队列腿不走 raw；本仓不 push）。
- **后果**：待用户/owner 清 kaggle-c 缓存（或 hub 侧版本锚升级）；simple-long 已判掐停待执行；
  analytic tripwire 转守 y1-long 单腿。

## §2026-10-13-goalnn-rescind-halt（2026-10-13，收回 simple-long 掐停令：0.20 线无依据＋死亡是探索税；改症状门）

- **背景**：用户追问"为什么 halt"＋"懵逼了"后重查：① 0.20 线无机制依据——x27-noleash
  0.348 无崩溃在先（我自己的证据），0.2167 在已证安全区内；② 线性漂移（+0.008/轮，
  到 0.30 需 ~11 轮），此前"4 轮即到" arithmetic 两处算错；③ 零崩溃症状（greedy 冻结≠
  游走；熵 1.6/KL/value/rollout 全活；eval 零死亡）。
  死亡分析（it20–31）：simple rollout 死 30–50/轮平稳无上行 vs eval 死 0/400 vs
  y1-long 熵塌腿仅 0–2 死——**死的是采样（探索税 −5 买单中），不是 argmax 崩溃**；
  高熵采样的必然代价，非进行性崩溃（x26 式：胜率掉＋游走，两条全无）。
  另：simple-long.it31 value-new 0.3019（x26 的数！）仍零症状——level 线（哪怕 x26 数）
  不预测崩溃，机制（游走）才预测；y1-long.it20 KL spike 0.58 单轮已恢复（熵随后回升）。
- **决定**：D1=掐停令收回（腿没停过，无需 resume；课程文件状态＋verdict 已改回在跑）；
  D2=tripwire 改症状门（greedy 翻转转坏／rollout 进行性崩／熵死／value>0.35 超全部先例），
  现全未触发；D3=0.20 数值线作废（教训：tripwire 阈值须有机制或先例依据，不得拍脑袋；
  本次三证据 amend：noleash-0.348＋线性外推＋零症状）。
- **备选与否决**：① 维持掐停（守 tripwire 字面）—— 否（字面已证无据；杀唯一活腿，错杀成本
  大于误放成本——误放有症状门四重兜底）；② 提线到 0.30 继续数值门 —— 否（0.30 同样无
  崩溃预测力，noleash 反例在先；数值门整体降级为参考，症状门上位）。
- **后果**：双腿继续跑；analytic 直算每门必读；本条为掐停令的正式撤销（§6.4）。

## §2026-10-13-goalnn-task-revert-rerun（2026-10-13，timeout 恢复 1500＋y 系课程全删＋y1 按优先级配方重开＋y1k 对照；c01 诊断：dist 彩票 67%）

- **背景**：用户令：① c01 优先级（贴脸＞挨打＞瞄准＞动静＞杀快）；② ladder-c01-long 删、
  timeout 恢复 1500；③ y 系课程全删；④ 随机权重（新种子）重开 y1＋优先级配方；
  ⑤ 并行 1–2 腿。c01-1500 诊断（y1.it40 eval n=400）：dist +5.8 占总 EV **67%**
  （彩票；A07 死方向），kill 仅 12%，防线税 ≈0，stuck 中位 958（clear 488/timeout 1237），
  拾取全家 400 局零记录，encl 恒 0。y1-long 系（fork＋terminal-tax 设计＋§y1long-maxticks／
  §y1long-design 两条）随 fork 删除终结——timeout 变现结论（it0 138→264）保留作史，
  腿 verdict 以任务死亡计，不以门计；y2-prep（等 simple.it80）随 y2 文件删除终结
  （c02-long 保留，simple.it80 TBD 作废）。
- **决定**：D1=清场（y1/y1-simple/y1-long×2/y2 五课程文件删〈未跟踪，我的〉＋fork 删
  〈halt 后〉＋tmp/y1* 运行态清 〈halt 后〉；weights/ 归档不动；已halt确认后执行）；
  D2=y1 重开（新 scratch seed 8，sha `5445bdd0f04d6762`；优先级配方 11 项＋terminal {}：
  删 encl/pickup/approach/fr×3/dist（死/彩票，有证），加 wStuck 0.002（deadzone300/cap900），
  wNearSq 28→84 ×3（P1 第一税；−0.6≈killEV 60%，恐惧线内），余全留；
  terminal pair 不引入（y1 无，minimal diff；死亡/超时显价另案））；
  D3=y1k 对照（x27 配方逐字＋同 s8＋同流 20261024；答"配方束效应＋旧 y1 死因运气/配方"）；
  D4=门（it40 D01≤50%＋kills≥0.5＋y1-stuck↓30%/y1k-stuck读＋fear-watch shots/D01＋policy上报；
  it80 晋升 D01≤30%＋pass≥50% ⇒ y2-重写）；D5=新流 20261024（新家族）；
  scratch 同 s8（165 宽；policy ~0.001；value 新精确 0）。
- **备选与否决**：① wAcc 下调（13.6 vs kill 2，看似头重）—— 否（未判坏；P3 量级已平价；
  动它加变量）；② wWin/wTick/wShot 引入（P5 速度/开火价）—— 否（y1 无，minimal diff；
  速度由 kills 携带；wShot 另案排队）；③ corner/threat 同删（读数 0）—— 否
  （行为难辨 ambiguous 留用；删 load-bearing 比留噪声贵）；④ c02-long 同删 ——
  否（用户只点 c01-long；y2-future 可能用；孤儿状态已注记）；⑤ tmp 不清沿用 ——
  否（旧账本 resume 陷阱；清场是开课前置）。
- **后果**：`course_openable` 双 True（s8 存在）；`nn-training/curricula/y1.jsonc`＋`y1k.jsonc`；
  开课条件：三腿 halt 确认＋tmp/y1* 清＋fork 删（agent 执行）后，用户点开课。
  long 系 weights（y1.it40/simple.it40）保留作史；y1-v1 归档（`weights/y1`→`weights/y1-v1`，待执行）。

## §2026-10-13-goalnn-y1f-design（2026-10-13，y1f phase-1 先开火腿：y1 删贴脸/火线两税＋加 explore 0.2，a2 边际抄法；优化器冻结）

- **背景**：y1.it80/y1k.it80 双收官双 fail（kills 0.015/0.333<0.5，stuck 1499/978>451，
  pass 1.5%/33%<50%，无 y2；D01 双双 −98% 地板）。y1 贪心坐穿：cells 恒 1（400 局从没离开出生格，
  scratch 11–32）、stuck 1499、shots~0；it50–70 只在 2001 抽搐（5–17 clears，同批种子）。
  用户令 phase-1：先跑起来开起火来，贴脸/火线税暂免，杀敌后再学走位闪避；参考 x1-rebirth-a2
  （it46 100%/0伤：①排序≠行为 ②真实语料标定 ③杠杆是边际比走>坐；`x1-rebirth-a2.jsonc` 头注＋§88）。
- **决定**：D1=`y1f`（y1 逐字＋删 wNearSq 84/wThreat 80＋加 wExplore 0.2；同 s8（sha `5445bdd0f04d6762`）、
  同流 20261024、同 level/iters/batch/优化器/K/eval、kickstart 关、anchor 0.001；terminal {} 不动）；
  D2=门（it40 shots≥10＋cells≥5＋kills≥0.3，D01 只读 watch（线＝it0 77.9），fear-watch shots 塌杀，
  it5 前 analytic 直算复核双 rms；fail⇒杀腿）；D3=it80 kills≥0.5＋shots≥10＋cells≥5 ⇒ y2-phase2
  （加回 near+threat，explore 回落）；D4=新 out/traj（tmp/y1f）＋backup 新目录（weights/y1f），§15.5。
- **备选与否决**：① 纯删两税不加 explore —— 否（stuck 木棍 −1.8 已证不够：y1 顶着它坐穿 50 轮；
  需胡萝卜，a2 抄法）；② 连 wHurt/wMiss 同删 —— 否（用户只点两税；hurt 是残余闪避信号、
  miss（−3.1/局）是下一嫌疑，留作 phase-1b 变量）；③ 照搬 a2 位移包（kl 0.02/lr 5e-4/epochs 6）——
  否（x1 死于位移太小 KL 0.0029，y1 位移充分方向错〈熵照掉、mode 坐 park〉；优化器冻结保单变量束）；
  ④ 照搬 a2 六项公式（含 wWin/terminal −5/−2）—— 否（换终局＋换公式＝多变量；死亡/超时显价另案）；
  ⑤ 从 y1k.it80 热启动 —— 否（继承 turret-mode，断 paired 可比；必须同 s8 测配方效应）；
  ⑥ wExplore 取 0.3 —— 否（scratch-proxy 成本 −0.22/格已悲观〈含死亡〉，0.2 贴线＋stuck 兜底已使
  走 −1.03＞坐 −1.70；phase-2 再 taper）。
- **后果**：`nn-training/curricula/y1f.jsonc`（load＋编译＋对账绿，证据 `tmp/y1f-ev-check.py`）；
  全文 → `docs/nn/experiments.md` §88；开课条件：用户在控制台点开课（agent 不得启动训练）。

## §2026-10-13-goalnn-y1d-design（2026-10-13，y1d 纯删对照腿：与 y1f 只差 wExplore，测胡萝卜必需性）

- **背景**：y1f 落盘（§2026-10-13-goalnn-y1f-design）后用户令补比较腿。y1f 头注 EV 预告的诚实细节：
  历史行上 y1f 与纯删纸面 margin 几乎逐字同（+0.67 vs +0.68；cells 恒 1 ⇒ explore 两边各 +0.2 常数），
  explore 效应只能在真位移兑现，纸算不可判 ⇒ 真跑一条。
- **决定**：D1=`y1d`（y1f 逐字＋删 wExplore 一项；相对 y1＝删 near/threat；同 s8/流 20261024/
  优化器/K/门；新 out/traj tmp/y1d＋backup weights/y1d，§15.5）；D2=门与 y1f 一字同
  （shots≥10＋cells≥5＋kills≥0.3，D01 watch，fear-watch，it5 analytic 直算；fail⇒杀腿）；
  it80 同线（胜者 base 进 y2-phase2）；D3=双分支预注册（y1f 赢⇒胡萝卜必需；y1d 持平/赢⇒
  免税已够＋explore 多余，phase-2 drop；双 fail⇒另有主犯另案）。
- **备选与否决**：① 不开对照、单跑 y1f —— 否（纸面 margin 相同，胡萝卜效应无证据，
  单腿答不了"必需性"；双腿并行成本 y1/y1k 已验证可承受）；② 对照腿顺手降 wMiss ——
  否（加变量；miss 是 phase-1b 排队变量，等 it40 先看）；③ 对照用不同流/起点 ——
  否（断 paired；三向同 s8 同流是最紧配对）。
- **后果**：`nn-training/curricula/y1d.jsonc`（load＋编译＋双 diff 绿，证据 `tmp/y1d-ev-check.py`）；
  全文 → `docs/nn/experiments.md` §89；开课条件：用户在控制台点开课（可与 y1f 并行，agent 不得启动训练）。


## §2026-10-13-ppo-job-lifecycle-ownership（2026-10-13，PPO job 生命周期收口：lost 可判／重启按 runId 作废／重发入口／残留自动清理）

- **背景**：2026-10-13 06:17–06:45 事故（x27-noleash）——人工 `rm` 掉**刚发布**的 job 目录
  ⇒ 细粒度腿静默挂死 28 分钟（`/jobs/{id}/result` 的 404 ≡ pending；细粒度路径无超时、无重发、
  无一行日志）。根因：`publish_job()` 把目录一写就撒手，**发布出去的 job 没有所有者**；
  唯一的「清理工具」是人的 `rm`，而 `rm` 会误伤刚发布的那一份。全文 →
  `docs/nn/remote-transport.md` §90（合并顺延：护栏 v2 先占 §89）；现场证据 → `.workbuddy/memory/2026-10-13.md`。
- **决定**（plan/ppo-job-lifecycle-ownership，五段按 M1→M2→M3→M5→M4 落地）：
  D1 **M1 lost 可判 + 看门狗自愈**：判据 = `/jobs/{id}/status` 的 404（未知 jid 现成 404 ⇒
  **hub 一行不改**）；三态 `job_liveness`（404 unknown 起表 / 200 alive 清表 / 其它·异常 不动表）；
  节流 30s + 宽限 60s（检测上界 ≈120s）；判 lost ⇒ 账本 `job_cancelled` + best-effort 解冻与
  `/admin/cancel-job`（**409 在飞不撤、不删目录**）+ 丢会话 ⇒ **下一拍重发同一 jid**；
  连败预算复用既有 `_bump_remote_fail`（3 连败 ABORT，**不新造第二阈值**）；等待打点 300→120s。
  D2 **M2 重启 = 按 runId 整体作废并兑现到磁盘**（`cancel_jobs_of_previous_runs`，腿启动时跑，
  与 `it` 无关；删除过 `reap_job_dir` 护栏：manifest 的 job_id/runId/it 与账本一致才删）。
  D3 **M3 重发入口**：沿用既有「意图文件 + 幂等回执」通道（`tmp/<课>/redo-ppo.json` +
  `.applied.json`），去重键 = 意图 `at`；控制台只写意图、**不碰进程不碰 hub**；
  控制台缺省 `it=0`（对当前轮——面板行上的 iter 落后于在飞轮，回填它会让按钮静默失效）。
  D4 **M5 无主残留自动清理**：**扩展** `_rotate_cleanup` 的同一道 `keep_iters` 闸
  （无 result ∧（fail.json ∨ 账本已作废）），不建第二份配额；冻结/未过窗/账本读不到一律保留。
  D5 **M4 只做弱版**：开关不一致打一行可 grep 的 WARN（按 `(课,类型)` 去重、恢复即摘名），
  **零行为变更**；强版（双写/派发强制读标记）待取证。
- **备选与否决**：① 改 hub 的 404→200 或加 `state=unknown` —— 否（评审 F1：未知 jid 走不到
  内部调度摘要，对 HTTP 面是 no-op；改状态码是契约变更，波及 `wait_job`/控制台）；
  ② 改 `probe_job_result` 的 404≡pending —— 否（hub 与节点两条链共用同一份状态码分类）；
  ③ 以「本地 job_root 目录存在性」判 lost —— 否（答不了「目录在但 hub 不认识」）；
  ④ 新增 `LOST_REPUBLISH_MAX` 独立阈值 —— 否（一个阈值一份判决，x3-step 事故的教训）；
  ⑤ 重发换新 jid —— 否（幂等键不含 code/时间 ⇒ 同 jid 即同一份活复活；换 jid 会白烧一次重算）；
  ⑥ 硬杀在飞（撕租约）—— 否（2026-10-03 用户裁决「不管在算的，只管没领的」）；
  ⑦ M3 改 `pause/resume` 语义来清在飞会话 —— 否（暂停是调度意图，不是撤单）。
- **后果**：代码落点 `remote/hub_http.py`（`job_liveness`/`unfreeze_job`/`admin_cancel_job`）·
  `remote/hub_client.py`（`cancel_jobs_of_previous_runs`/`reap_job_dir`）· `worker/loop_round.py`
  （`RemotePpoJob` 三个纯内存字段）· `trainer/loop_remote_drive.py`（看门狗 + M3 消费）·
  `trainer/loop_remote_fail.py`（`_bump_remote_fail` 抽取）· `trainer/loop_control.py`（redo 意图/回执）·
  `trainer/loop_core.py`（M2 启动钩子）· `worker/loop_guards_sweep.py`（M5）· `trainer/loop_serve.py`（M4 弱版）·
  `dashboard/src/server/actions/redo-ppo.ts` + `api/route.ts` + `web/app/panels/CourseAdmin.tsx`（M3 控制台）。
  测试：`tests/trainer/test_ppo_watchdog.py` / `test_ppo_redo.py` / `test_loop_serve_switches.py` /
  `test_loop_control.py`（扩充）/ `test_loop_guards_split.py`（扩充）·
  `tests/remote/test_hub_liveness_cancel.py` / `test_cancel_previous_runs.py` · `dashboard/tests/redo-ppo.test.ts`。
  **已知欠账**：push 腿（直推节点）看门狗留 TODO（状态面是另一端点）；M4 强版待现场时间线取证；
  真机复验（删在飞 job 目录 ⇒ ≤120s 自动重发）由用户手工执行。

## §2026-10-13-goalnn-body-transfer-guard-v2（2026-10-13，传输护栏 v2：`read1` 收字节 + 时间窗采样与 2 窗迟滞 + 失败尝试入账；plan/body-transfer-guard-v2.plan.md + 首轮评审 R1–R8）

- **背景**：真机一个 job 的传输账 `合计 1.31MB/343.5s`、`other=343.7s` 独吞关键路径，而链路不慢
  （同日其它 job 1.2–7.9MB/1.4–16.1s）。真因（本机滴流 socket 实测）在**护栏自身的两处形状**：
  ① `resp.read(amt)` 是**循环 recv 凑满 amt**，而 socket 超时是**每次 recv** 的超时 ⇒ 隧道只要每
  < `idle_timeout`（45s）吐 1 字节，`body 停滞` 判据**结构上永不触发**，只剩墙钟闸兜底；
  ② 低速判据只在**首块**判一次（`probed` 一次性）：慢链路要等满一整块（≈300s）才有样本，而只修 ①
  之后首块又太小 ⇒ 判一次 `should=False` 就**永久关掉自己**（探针复现现场那条行）。第三笔：失败的
  尝试**不进任何账** ⇒ 那 300s 只以「`other` 巨大」出现，归不了因。
- **决定**：**M1** 新唯一收字节口 `_recv_some(resp, limit)`（= `read1` 语义），`remote/http.py` 与
  `remote/tailscale_boot.py` 的 `_read_body` **两处同改**（后者单文件自包含，孪生是结构性的；
  `tests/test_reuse_ledger.py` 的 `BASELINE_EXACT` 12→13 为此记账）；**M2** 采样改**净时间窗**
  （`win_elapsed ≥ WIRE_PROBE_SEC = 3s`，净值 = 墙钟 − Σ让路、不用一次性标记）+ **连续
  `WIRE_REROLL_PROBES = 2` 个慢窗**才断（抗单窗抖动与重连后的 TCP 慢启动），`_reroll_decision` 只加
  关键字 `remaining: int | None = None`（缺省 = 旧累计口径，**逐字不变**），每次重抽的浪费上界从
  「≤1 块 256KB」改为「≤2 窗」；**M3** `BodyTimeoutError(TimeoutError)` 带 `partial/got/total`
  （**只做可读性**）；**M4** `_WIRE` bucket 加 `failed{sec,bytes,n}`，每次**未成功**的尝试
  （超时/瞬时网络错/重抽/被 P1 挤走）结束时登记，同行进 phases 与 `wall` 的 `max()`，
  `_wire_totals`（预取摘要）**不含**它，**4xx 不入账**；**M7** prefetch 轮有候选要下时先 `_wire_start`。
- **备选与否决**：① 只把 `BODY_CHUNK` 调小（16KB）—— 否：结构问题没解（873B/s 下仍要 19s）；
  ② 自建 socket 读循环绕开 `read(amt)` —— 否：要重写 urllib 的代理装配（CONNECT/绝对 URI）；
  ③ 把 socket `timeout` 当「每块总预算」—— 否：即 §21 记录的「整读」（护栏设立前的病根）；
  ④ 迟滞取 1（单窗即断）—— 否：链路 44× 双峰 + 重连后第一窗天然慢 ⇒ 每次重连自我判死刑；
  ⑤ 把超时挪进重抽路径 —— 否：重抽是「幂等 GET + 换连接」，超时是「退避重试」；
  ⑥ 承诺「续传起点」—— 否：HEAD 无 Range（§51 立项门槛没动）⇒ 半截仍作废，文案不许写「续传」；
  ⑦ 引入「重抽无收益即停」判据 —— 否（**反向约束**）：无 Range 时重抽的浪费是实打实的。
- **后果**：滴水链接不再等满 `total_timeout`（≈6s 内动手）；`failed=` 让「300s 花在哪」第一次可归因；
  `read1` 把采样点从「每 256KB」变「每 recv」（`pace()` 调用变密，但其本身只是几个计数 + 条件判断）。
  **未做（有意）**：R4 最后一次尝试必然硬传 · R5 单通道 P1→P2 连带（调度面）· R7 失败样本不进
  `_note_rate`。**收益仍是推演**（343.5s ⇒ ≈20s = 单次现场反推 + 缩放探针）：真机 ≥10 job **配对**
  读数取回来之前只算「机制成立」（未决 → `docs/nn.progress.md` §3.1 #28），回退档
  `WIRE_REROLL_PROBES = 1`（保留 `read1`）或整段回滚。
- **★ 合并说明（2026-10-13，rebase 至 `origin/goal-nn`）**：本条原写于 `3c603ba9`（当时最大 §84、
  无 Range），而目标基线已先落地 §2026-10-12-transfer-jitter-tolerance（迟滞/衰减/`WIRE_REROLL_GAIN`）
  与 §2026-10-12 plan A/B/C（**payload Range 断点续传**、上传让路点、控制面死线）⇒ 本条「无 Range」
  与备选⑦「不引入停抽」两条前提在合并基线**已不成立**。合并口径：**保留上游的 Range / `WIRE_REROLL_GAIN`
  / 衰减 / 让路基建**，在其上补 M1（`_recv_some`/`read1`）、M2（时间窗 + 2 窗迟滞，**取代**上游 §86 的
  「每块一点」采样）、M3（`BodyTimeoutError`）、M4（`failed=` 入账，重抽尝试按**本段**字节计以免带 Range
  时跨尝试重复）、M7。故备选⑦**作废**；`WireSlowError` 仍带 `partial` 且 `bytes_read` 为累计口径；
  「半截作废」改为「Range 续传，仅服务端忽略 Range 才真作废（`_wire_note_resend`）」。全文节号 §85 → **§89**。
—— 全文（触发 / 探针实测表 / 五处关键设计 / 口径红线 / 未做清单）→ `docs/nn/remote-transport.md` §89
—— 落点 → `remote/http.py`（`BodyTimeoutError` / `_recv_some` / `_read_body` 窗采样 / `_get_with_retry`
失败账）· `remote/tailscale_boot.py`（孪生三处）· `remote/wire.py`（`WIRE_REROLL_PROBES` /
`_reroll_decision(remaining=)` / `failed` / `_wire_note_failed` / `_wire_flush`）· `remote/job_round.py`（M7）·
`remote/worker.py`（重导出）· `tools/wire_report.py`（phases 解析 + `T_fail`）
—— 回归 → `nn-training/tests/remote/{test_body_transfer_guard,test_wire_reroll,test_boot_wire_guard,
test_prefetch_adaptive,test_wire_report}.py`（新增/改写 15 例，先红后绿）；门禁
`bash tools/githook/nn-python-gate.sh` 绿（4275 passed / 1 skipped）

## §2026-10-13-goalnn-y1h-design（2026-10-13，y1h A线调税腿：y1f只去wHurt，测挨打税是否按头）

- **背景**：y1f/y1d it20 双坐塔（cells 恒 1），开火 21.68 没问题——按住的是腿不是枪。hurt 在 turret 行
  隐身（≈−0.06），前瞻里"动而挨打"＝110 重税；wDmg 0.03 留（a2 单杯兜得住 0.877 伤的先例）。
  用户令 A/B 两路都备好、后面并行。
- **决定**：D1=`y1h`（y1f 单变量删 wHurt；同 s8/流 20261024/优化器/K/门；新 out/traj tmp/y1h＋
  backup weights/y1h，§15.5）；D2=门与 y1f 一字同（shots≥10＋cells≥5＋kills≥0.3，D01 watch，
  fear-watch，it5 analytic；fail⇒杀腿）；it80 同线 ⇒ y2-phase2；
  D3=readout（y1h 动⇒hurt 是手；三腿不动⇒主犯另有，phase-1c）。
- **备选与否决**：① 连 wMiss 同删 —— 否（开火没问题，miss 不背腿的锅；加变量，phase-1b 排队）；
  ② explore 加量代替去 hurt —— 否（x20 反向激励旧账下胡萝卜侧已够 0.2；先松绑，phase-1c 排队）；
  ③ 不同流/起点 —— 否（断 paired；四向同 s8 同流）。
- **后果**：`nn-training/curricula/y1h.jsonc`（load＋编译＋对账绿，证据 `tmp/y1ha-ev-check.py`）；
  全文 → `docs/nn/experiments.md` §90；开课人点（并行容量人定）。

## §2026-10-13-goalnn-y1a-design（2026-10-13，y1a B线BC迁移腿：a2.it45为师＋y1f配方＋缰绳）

- **背景**：Plan B（用户令）：y1f/y1d it20 坐塔后备——把会动的冠军搬过来。师傅 a2.it45
  （`weights/x1-rebirth-a2/x1-rebirth-a2.it45.20260917-214757.json`，sha16 `b21c13c899dc70bb`；
  档案 eval 1.0/cells 12.5/shots 3.18/dmg 0.877）。架构账：师傅 schema 3（头 128 无 extra）
  vs 现行 5（头 165）⇒ bc 直指被 schema 门响亮拒绝；`load_state_into` 的部分装载只认同 schema 族。
- **决定**：D1=`y1a`（配方 y1f 逐字，变量只剩起点技能；bc 指转换产物
  `weights/y1a/a2it45-warmstart-v5.json`〈**待生成**，命令在头注：init_spatial_leg.py --legacy-arch，
  a2 头宽对得上 v3-hu150 系〉；kickstart_ref=true＋init 0.1，warmup 0；新 out/traj tmp/y1a＋
  backup weights/y1a，§15.5）；D2=冠军门（以本腿 it0 基线为准：kills≥0.8＋cells≥8＋D01≤5.0，
  shots 只读；fear＝双塌；it5 analytic；fail⇒杀腿）；it80 kills≥0.8＋pass≥80% ⇒ y2-phase2；
  D3=开课前置（产物存在＋warmstart_missing 已审计，缺一不开）。
- **备选与否决**：① bc 直指旧文件 —— 否（schema 门 fail fast；只有转换路）；② 不用缰绳 ——
  否（新列 fresh＋新奖励，首轮 KL 必 spike；M3/x24 缰绳先例）；③ 用 it46 不用 it45 ——
  否（it45 是档案 eval-1.0 verdict 点；换点加变量）；④ 配方用 a2 原版六项 —— 否（那是"a2 续跑"
  不是 phase-1；本腿测"phase-1 经济＋冠军技能"，归因死于技能侧、证配方看 y1f/y1d/y1h）。
- **后果**：`nn-training/curricula/y1a.jsonc`（配方/缰绳/前置态校验绿，证据 `tmp/y1ha-ev-check.py`）；
  全文 → `docs/nn/experiments.md` §91；转换＋开课都是人点（agent 不得启动训练）。

## §2026-10-13-goalnn-y1fd-it40-verdict（2026-10-13，y1f/y1d it40：y1f 三挂杀、y1d 偏离续命、explore 零效应、y1h 开）

- **背景**：y1f it40（shots 0/cells 1.0/kills 0，0/400 开火，it25–40 坐穿）三线全挂＋恐惧塌；
  y1d it40（shots 7.82/cells 1.0/kills 0.325，130 clears，394/400 开火，rollout 83/win 0.395）。
  y1a 早报：it1 KL 0.037→it2–4 0.004（缰绳按住跨架构冲击），rollout 0.996，熵 1.01。
- **决定**：D1=y1f **杀**（无异议）；D2=y1d **§6.4 偏离续到 it60**（字面挂两条，但唯一活口＋rollout 最好＋
  kills 过＋shots 降伴随 kills 守＝效率化非恐惧；改判线 it60：shots<5 或 kills<0.3 即杀，
  cells 仍 1 则与 y1h 会师 phase-1c）；D3=y1h **现在开**（cells 双挂＋y1f 全挂＝预注册触发条件）；
  D4=y1a 不动（等 it5 eval 首检＋analytic）。
- **备选与否决**：① y1d 按字面杀 —— 否（杀最后活口保门面；门是手段，留希望是目的；偏离已记本条）；
  ② y1f 续命 —— 否（三挂＋恐惧塌＋y1 同死法，无可辩）；③ 现在判 explore 有害 —— 否（无实锤；
  taper/drop 候选，等 y1h）；④ y1a 加课 —— 否（it5 首检前不动冠军）。
- **后果**：杀/续/开全是人点（控制台）；全文 → `docs/nn/experiments.md` §92；
  ⚠ 五条腿 analytic 直算零次——接线解释全盲，y1a 最急（缰绳 unverified）。

## §2026-10-13-goalnn-y1a-it5-hold（2026-10-13，y1a it5 满分保持＋analytic 关闭接线盲：三腿零三种死法）

- **背景**：y1a it5 eval 400/400（kills 1.0/cells 19.9/D01 3.18，冠军门全过 margin 大；
  D01＋75%/dmg＋66% watch，ticks 495→431 杀更快）。训练 KL 0.037→0.004–0.007（缰绳按住），熵 1.01。
  头注 mandate（it5 前直算）到期。
- **决定**：D1=y1a **续**（守住，无动作，等 it10 看 D01 趋势）；D2=接线盲关闭：直算证 y1a 结构性零
  （legacy 空片；缰绳看 KL）＋y1f/y1d 新列真动（policy 0.099/0.074、value 0.287/0.212；
  账本零系上报链误报，以直算为准）；D3=y1f 死因追加 lead（value 0.287≈x26 无绳爆炸位 0.30＋
  熵反升 0.83→1.05，advantage 污染嫌疑；phase-1c 候选，不单独立项）。
- **备选与否决**：① 因 D01＋75% 干预 —— 否（线内且杀更快；it10 再看）；② 重查 y1/y1k 历史零 ——
  否（旧账，新法不追）；③ 给 y1a 加 value 绳 —— 否（legacy 空片无绳可拴；缰绳就是 kickstart）。
- **后果**：证据 `tmp/y1a-rms-analytic.py`（绿）；全文 → `docs/nn/experiments.md` §93；y1a 等 it10。

## §2026-10-13-goalnn-tax-start-rung（2026-10-13，x2–x6 加冕迁新网普查：c04 发病，y4 为第一加税级）

- **背景**（用户令）：x 系无数轮把贴脸/火线傻站养成绝症才重开 y 系；a2 迁移守住 100% 证明旧成果可搬。
  六冠（x2.it15/x3.it153/x4.it45/x5r.it35/x5a.it155/x6.it45，全 schema 3 头 128）经
  init_spatial_leg --legacy-arch 转新网（41/42 原位、missing 空，`tmp/mig-audit.py` 绿），
  各在自家 rung eval（700 局 55s；c01 复用 y1a it0）。
- **决定**：D1=**y4（c04）为第一加税级**（胜率 95→76 悬崖＋D01 翻倍＋连击翻 3 倍＋转化层全点火，
  c04–06 n=50 下 19pp 实）；D2=y2/y3 沿 phase-1 经济（免 near/threat；97%/95% 不值得冒恐惧）；
  D3=税形为**带血的税**（post-hit near/bursts/max120/while-low，v10/v11 现成列），否决无差别贴脸税
  （c01 nearSq 最高却 0 伤，一刀切连干净近身一起杀）；D4=剂量开 y4 时用 c04 真实行标定。
- **备选与否决**：① c02（D01 63.5）即加税 —— 否（胜率高＋连击低；若 y2 相对 x2-migrated 退化再议）；
  ② c06（敌最多）加税 —— 否（82% 反高于 c04/05，病在 c04 不在 c06）；③ 只测 x5-rebirth 弃 x5a ——
  否（x5a 的 bursts/postHit 软化是 approach 税形唯一的 endpoint 级证据，留作 y4 税形参考）；
  ④ 现在写 y4 文件 —— 否（y1 phase-1 未收官；判决先行，文件等 y2/y3 落子）。
- **后果**：行 `tmp/mig-eval/rows-*.jsonl`（复用）；权重 `nn-training/weights/x{2,3,4,5r,5a,6}-mig/`；
  全文 → `docs/nn/experiments.md` §94；y4 文件待 phase-1 收官后按本条开。

## §2026-10-13-goalnn-y45-design（2026-10-13，y4/y5 加税腿：x4/x5r 为师＋x27 逐字，300 轮人工盯盘无熔断）

- **背景**（用户令）：§94 判 y4 第一加税级后，用户点名配方 x27-obs 逐字、iters 300、人工盯盘不设熔断、
  问能否并行。§94 尾巴"等 phase-1 收官"被本令取代（提前开 y4/y5，phase-1 与加税并行）。
- **决定**：D1=y4（c04＋x4-mig，sha `770b14b9e3d431a4`）＋y5（c05＋x5r-mig，sha `b17614921b32ff15`；
  不用 x5a：机制探针、主端点死，不当师傅）；配方/terminal/scheme/优化器/K 与 x27-obs 全逐字；
  kickstart 缰绳＋burn{baseline,100} 等效关＋ent_break 0；iters 300/max_hours 48/workers 8；
  eval 本 rung（"2000" 单关×100，y 口径）；D2=盯盘表（it40/100/200 对基线：y4 3.56/170/76%，
  y5 4.40/139/78%；kills−15%/pass−10pp/D01 翻倍/KL>0.03×3/rms 非零 ⇒ 人工掐；§15.4 每次 update
  人读 kl/entropy/value/gnorm＋rms）；D3=§15.5 新账本双份（tmp/y4＋y5，backup weights/y4＋y5）；
  D4=**并行就绪**（零共享状态；容量人定）。
- **备选与否决**：① 用 x5a 当师傅 —— 否（机制探针不当师傅；过关冠是 rebirth 系）；
  ② eval_games 50 照 x27 —— 否（单关 rung 取 100 保分辨率；700 局 55s 实测成本可忽略）；
  ③ max_hours 24 照 x27 —— 否（长 tick rung，48 纯 ceiling 偏离）；④ stream 沿 x27 的 20261021 ——
  否（y 系用 20261024，rung 不同本就无串扰）；⑤ 带血税形（§94 原推荐）—— 否（本单用户令为准，
  偏离已记本条；pickup 死列 phase-2 清理候选）；⑥ 自动门 —— 否（用户令人工盯盘，人就是熔断器）。
- **后果**：`nn-training/curricula/y4.jsonc`＋`y5.jsonc`（load＋编译＋逐字 diff 绿，
  证据 `tmp/y45-ev-check.py`）；全文 → `docs/nn/experiments.md` §95；开课人点（可并行，agent 不得启动训练）。

## §2026-10-14-wire-diet-transport（2026-10-14，payload/result 传输瘦身：**回传字节可降质，本机计算的 opt 保持 f32**）

- **背景**（用户令 + 实测）：code.zip 已瘦身且可缓存（一次性收益吃完），剩大头是每轮不同的 payload
  下行与 result 上行；家宽晚高峰掉到几十 KB/s，传输 ~6 s → >100 s。实测基线（真实 job
  `tmp/y1f/remote-jobs/7d94ec31dfd45dcb`，it42，190 shard + 相邻四轮 blob）：命中态每轮
  **2,480,178 B**（payload 1,724,704 + 上行 weights 288,205 + opt 467,269）。
  payload 解包 296 MB → 1.70 MB，xz 后占比 scalars 40.1% / metrics(f64) 31.8% / obs(u8) 20.8% /
  manifest×190 8.6%；`metrics.npy` 是 reward 的唯一输入（`worker/ppo/np_core.py:672/693`）**不能删**。
- **决定**：D1=**P1**——`metrics.npy` 改为打包端按课程公式算好 `reward.npy` 再发（**无损**，−27%；
  payload 头带 `{metrics_version, formula_fp}`，不符即 `ProtocolError`）；
  D2=**P2**——`scalars/extra/lp_*` 存 f16（加载端 `astype(copy=False)` 零改），**`value` 保持 f32**
  （它进 GAE 的 δ 再被 adv_norm 放大，只值 3.3%，风险不对称）；
  D3=**R1**——result 权重改传 delta int16+单 scale（288,205 → 87,546；量化误差/每轮真实更新 = 1.44e-4）；
  D4=**R3**——opt 回传 `Δopt` f16（467,269 → 95,055），**本机计算用的 opt 保持 f32 全量**；
  D5=**R0 是 R3 的硬前置**——新增 `course_cache/<course_fp>/latest.json` 把「hub 权威字节 sha」与
  「本机高精度副本」解耦，否则降质会让内容寻址缓存键失配 ⇒ 每轮多下 614 KB（比省下的还亏）。
- **红线**（用户 2026-10-14 裁决）：撤销 `plan/minimize-payload.plan.md:53 §1.3-1` 的
  「opt.pt 不得量化/降质」，**但仅限回传字节**；本机计算/落盘那份必须 f32 全量。
  判据 = 云机 PPO worker 并行度通常为 1，回传的 opt 本就缓存在云机 ⇒ 传输侧降质不进计算。
  hub 侧 `B_n = B_{n-1} + Q(A_n−A_{n-1})` 有**线性累积**（4.7e-5/轮，80 轮 ≈0.38%），
  但该存档只在**换机接续**时被用到一次 ⇒ 不跨机累积，可接受。
- **备选与否决**（全部有实测，别重开）：① xz preset 3→6 —— 否（−3% 但 +3.5 s CPU，净亏）；
  ② byte-shuffle 后压缩 —— 否（抽样**变胖 13.5%**）；③ opt 状态存 **f16** —— 否（更新量 L2 相对偏差
  **3.96e+3**，`exp_avg_sq` 溢出 f16 值域；opt 只能 bf16 或 delta-f16）；④ value 转 f16 —— 否（见 D2）；
  ⑤ metrics f64→f32 —— 否（只 −6.6%，尾数噪声仍在）；⑥ scalars int16 单/逐列 scale —— 否（比 f16 大）；
  ⑦ 190 份 manifest 合并 —— 否（−0.6% 却要改 shard manifest 读取面）；⑧ zstd —— 否（新依赖，需另论证）；
  ⑨ R2（opt 存 bf16，零累积、实现更简）—— **不采用但保留为 R3 失败时的退路**（上行多 127 KB/轮）。
- **后果**：全文 → `plan/wire-diet.plan.md`（方案/顺序/兼容矩阵/DoD）+ `reports/wire-diet-2026-10-14.md`
  （数据汇总）；可重跑证据 `tmp/wire-audit{,2,3,4,5,6}.py` 与同名 `.txt`。
  目标：每轮 2,480,178 → **1,118,691 B（−55%）**，晚高峰 30 KB/s 下 ~83 s → ~37 s。
  ⚠ 用户已选「先写 plan 再实施」⇒ 本条**只记裁决，未动代码**；
  M3/M4 上线后必须跑 ≥20 轮 `kl/policy/entropy/winRate` 对照确认无分叉。

## §2026-10-13-goalnn-y5hit-design（2026-10-13，y5-hit 命中腿：y5.it300 为师＋wMiss 加压＋删 dist 彩票）

- **背景**（用户令）：y5.it300 shots/kill 5.8→7.3 恶化，要求加练命中；目标继续少暴露、提命中率；
  x27-obs 时 NN 命中已和人类相当（不动 wAcc）。诊断：+6.73 发里 +6.13 脱靶（miss +52%），
  开火距离 +36%；bricks 恒 0。gap 配对模式没跑成（无 human c05＋eval 行缺 panel 列），框架＋配对 z 代替。
- **决定**：D1=`y5-hit`（y5.it300，sha `092ea1b13ebf9763`，legacy 同代直连，kickstart 关，opt fresh；
  wMiss 0.158→0.35〈边际 −0.05→−0.17〉＋删 wDist〈彩票排雷〉；wAcc/wHit/暴露全家 HOLD；
  iters 100/max 12/eval 单关×100/同流 20261024；新 out/traj＋backup，§15.5）；
  D2=门（it40 hitRate≥0.45＋shots/kill≤6.0；护栏 kills≥3.5/D01≤85；fail⇒杀）；
  it100 双线 ⇒ 收官。
- **备选与否决**：① 加 wAcc —— 否（human parity＋别加准头，既定结论）；② 加 wHit ——
  否（火力无辜；加价诱 spray）；③ 连暴露侧同调 —— 否（用户令保持少暴露）；④ iters 300 ——
  否（短腿精修 100，可延）；⑤ A02/A06/A08 定向税 —— 否（eval 行不可读，panel-only；
  先看 wMiss/dist 双杠杆，不加新列）。
- **后果**：`nn-training/curricula/y5-hit.jsonc`（校验 `tmp/y5hit-ev-check.py` 绿）；
  全文 → `docs/nn/experiments.md` §97；开课人点（agent 不得启动训练）。

## §2026-10-14-goalnn-wire-diet-m1m2（2026-10-14，wire-diet 落地 M1+M2：打包端先算 reward ＋ 字段 f16，**缺省关**）

- **背景**：§2026-10-14-wire-diet-transport 只记裁决、未动代码（用户令「先写 plan 再实施」）。
  本轮按 `plan/wire-diet.plan.md` v2（含评审 `plan/wire-diet.review-bf.md` 的 P0/P1 修订）落地
  M1（P1 打包端算 reward）＋ M2（P2 训练输入字段 f16）；开关 `--wire-diet`（`rl.wire_diet`）
  **缺省 0 = 逐字节旧行为**（真机 A/B 没取到读数之前不翻）。
- **决定**（§8-D1/D4/D5/D6 的执行，外加两处实施期推导）：
  ① P1 用**显式** `reward_spec` + `it`，**不读**模块级 holder（`biz.reward_context`）——打包点
     不止训练进程一处（hub 重发 / `smoke_loopback` / `plan_run`），那些进程里 holder 缺省
     `it=1, reward_fn=None` ⇒ 读 holder 会**静默**按 it=1 算错整轮奖励（评审 P1-4）；
     每 shard 落 `reward_meta.json`，读端**必须**校验四栏（见②）；缺 spec ⇒ 不做 P1、照旧带 metrics。
  ② 读端多补一道门 **`reward_version`**（评审只点了 it / metrics_version / formula_hash）：节点的
     code.zip 与 hub 不同代时，两版算式产出的 reward 在数值与形状上都无从分辨 —— bump 过的
     版本号是唯一能看见代际差的暴露面。四条任一不符 ⇒ `ProtocolError`（内容决定性，不重认领）。
  ③ P2 只用**内存**字节变换（`pack_payload` 成员回调 + `_add_bytes`）：落临时 f16 文件会把新
     mtime 写进 tar 头 ⇒ 同一份内容每打一次 `payload_sha256` 都漂，而 sha 是校验锚、
     `PAYLOAD_PERTURB_NAME` 的存在也以「打包决定性」为前提。
  ④ 常量（文件名 / 版本 / f16 字段表）住 **`common/shard.py`**：写端在 hub 侧（**不得** import
     `worker.*`），读端在 `worker.ppo.np_core` ⇒ 住 common 才是两侧真一致（实施期把 v2 的
     「名字住读取端」改掉）。
- **备选与否决**：① 读 holder 拿 reward 参数 —— 否（重发路径静默错算，评审 P1-4）；
  ② `value` 一并转 f16 —— 否（GAE 基线，误差经 δ→GAE→`adv_norm` 放大，只省 3.3%，风险不对称）；
  ③ 变换落临时文件再打包 —— 否（破坏打包决定性 ⇒ sha 与扰动闭环同时失效）；
  ④ 常量住读取端由写端 import —— 否（DAG 层号连坐：`payload_slim` 抬到 L2 ⇒ `hub_client` 要升
     L3 ⇒ `code_snapshot` 那串全得重排）；⑤ 把 `hub_client` 压到 999 行不拆 —— 否（LOC 预算的
     正解是拆，不是贴边；贴边等于下一次改动必红）。
- **后果**：代码 `remote/payload_slim.py`（新）· `remote/payload_pack.py`（新，见下）·
  `common/payload.pack_payload`（成员回调钩子）· `common/shard.py`（常量下沉）·
  `worker/ppo/np_core.py`（奖励双形态 + 四道门 + `need_any` 发现判据）· 接线
  `hub_client.publish_job` / `trainer/loop_remote_job` / `worker/cli.py`（开关 + `wire.wire_diet`
  归因位）。用例 23 例（`tests/remote/test_payload_slim.py` · `tests/worker/test_reward_npy_shard.py`）；
  真数据等价探针 `tmp/wire-audit7.py`（reward 逐元素相同 `max|Δ|=0.0`，f16 四字段绝对误差 ≤1e-3）。
  **顺带一刀**：`hub_client` 993 行 + 本改动必越 LOC 预算（<1000，`tests/test_python_loc_budget.py`）
  ⇒ 按「独立所有者 + 独立触发条件」把发布端 payload 封装（`pack_payload_zip` + 两条自检 + 重打上限）
  整块搬到 `remote/payload_pack.py`（只 import `common.*` ⇒ 拓扑秩 L0，`hub_client` 仍 L1，
  DAG 层号无连坐；账本两条新条目见 `tests/helpers/remote_dag.py`）。门禁：
  `bash tools/githook/nn-python-gate.sh` 绿（ruff + mypy + pytest `tests/ e2e/` 全量）。
  全文 → `docs/nn/remote-transport.md` §91 · `plan/wire-diet.plan.md` §10。
  ⚠ **真机 A/B 未取 ⇒ 开关保持 0**（回退档 = 置 0）；M0/M3/M4 一个没碰（`docs/nn.progress.md` 第 29/31 行）。

## §2026-10-14-goalnn-wire-diet-m0（2026-10-14，wire-diet 落地 M0/R0：`course_cache` 身份映射 + `manifest.opt_from`）

- **背景**：R1/R3 让 hub 的**权威字节**从「回传的原始字节」变成「重建产物」⇒ 与 worker 本机那份 f32
  全量**必然不同 sha** ⇒ 内容寻址缓存（键 = 权威字节 sha）逐轮失配，净账为负（R1 省 200,659 B /
  多付 387,540 B 下行；R3 省 372,214 / 多付 614,400）⇒ **M0 是 M3/M4 的硬前置**（`plan/wire-diet.plan.md` §5）。
- **决定**：
  ① 身份映射住**持久根** `<cache_root>/course_cache/<course_fp>/{pending,latest}.json`，命中判据是
     **身份**（`manifest.opt_from == latest.job_id`）不是字节；新可选键 `opt_from` 由**发布端**写
     （`hub_client._previous_job_id` 扫同 run/课程的 it−1 job 目录；扫不到 ⇒ **不添键**）；读取端**不**
     自己推「it−1 邻接」——那会多出第二份真相。
  ② **pending → confirm**：产物落盘只写 `pending`；`post_result` **200 受理**才升 `latest`；**403/409**
     ⇒ **作废**；其余（含 `None`）不动。理由：竞速里**输了的那台也跑出过一份产物**，而 hub 的权威链
     是从**赢家**那份重建的 —— 少了这一步，输家下一轮会拿**自己那条链**当起点 = **静默分叉**。
  ③ 命中自校 = 两份本机副本（weights / opt）**存在 + 字节数 + sha256**；字节本体**复用 `blob_cache`**，
     **不**复制第二份（plan 草图的 `*_local_path` 因此不采用：复制 = 同一份字节两处真相，且新目录不进
     `prune_caches` 的容量预算 ⇒ 无界）。
  ④ 失效即回退：身份不符 / 被 prune / 坏件 / 换机 / 旧 hub 无 `opt_from` ⇒ 逐字节落回既有内容寻址路径
     （安全性不变）；`prune_job_dirs` 豁免名单加 `course_cache`（同 2026-09-17 `blob_cache` 漏项同型）。
  ⑤ 状态码判据**单点在 `course_cache.settle`**，`job_round` 的 settled 回调只转述结论。
- **备选与否决**：① plan 草图的「本机副本另落 `*_local_path`」——否（两处真相 + 不进容量预算）；
  ② 「产物落盘即算可用起点」——否（竞速输家静默分叉，见 ②）；③ 读取端自己推 it−1 邻接——否（第二份
  真相；换机/重发下邻接不保证同链）；④ 409 也照 200 处置——否（分不清自己重试还是别人赢，按最严处置，
  最坏只少省一次字节）；⑤ 映射落 `work_dir` 而非持久根——否（会话级 ⇒ 跨会话收益 0；已由
  `cache_root_of(blob_root)` 反推持久根）。
- **后果**：`remote/course_cache.py`（新，L0，只 stdlib）· 接线 `download._resolve_weights`（init 命中）
  / `train_core`（opt 命中 + 产物段 `record_pending`）/ `job_round._on_settled` / `hub_client.publish_job`
  （`opt_from`）/ `job_fs.prune_job_dirs`（豁免）· `hub_client` 那处「原样字节落盘（重打 tar 会 sha 漂移）」
  的护栏注释同 commit 改写为「R1/R3 之后权威字节 = 重建产物 ⇒ 判据换成身份映射 + 命中自校」。用例 **18 例**
  （`tests/remote/test_course_cache.py`）+ `remote_dag` LAYERS 登记 + 三条拆分白名单各一条新边；
  `bash tools/githook/nn-python-gate.sh` 全量绿。代码**惰性**：R1/R3 未上 ⇒ 对外行为逐字节一致。
  全文 → `docs/nn/remote-transport.md` §92 · `plan/wire-diet.plan.md` §11。

## §2026-10-14-goalnn-wire-diet-m4-hub-torch（2026-10-14，用户裁决：M4/R3 的重建走 (a)「hub 引入 torch」，回传 opt delta f16）

- **背景**：M4（R3）要在 hub 侧把 `Δopt` 与 base 相加重建 `B_n`；今天 hub 只做原样字节透传（不解码 tar
  里的张量）⇒「hub 进程装不装 torch」是 `plan/wire-diet.plan.md` §7-2 / §10-2 挂起的**显式决策**
  （AGENTS §6.5：新运行时依赖属「必须问人」四类之一）。
- **裁决（用户 2026-10-14）**：**选 (a) hub 引入 torch**，按 §3 R3 走 **delta f16**
  （`Δopt = opt_new − B_{n-1}`，hub `B_n = B_{n-1} + Δ`）。本机计算用的 opt 保持 **f32 全量**（训练质量
  不受影响；误差只落在 hub 存档，且**不逐轮累积** —— 残差 = 本轮量化误差 ≈4.7e-5 相对；换机接续时它
  成为新链的起点，不放大）。
- **落地硬约束**（本条目立即生效）：
  ① torch 在 hub 侧**延迟 import**（只在 delta 重建路径；hub 冷启动与「无 delta」的路径不得因此变慢）；
  ② hub 缺 torch / 版本不符 ⇒ **不得静默降级**：拿不到 base 就没法重建 ⇒ 拒收该形态并响亮记一行、
     回退整份 blob（**绝不**把 delta 当权重落盘 —— 那正是本 plan 兼容矩阵 §4 要挡的方向）；
  ③ 形态**自述**在 blob header（`{"ofmt":"delta-f16"}`），缺键 = 旧形态（旧端忽略未知键）；
  ④ 验收含**重建决定性**用例（同 delta + 同 base 两遍 ⇒ 同字节）与一轮端到端：worker 本机 f32 链与 hub
     重建链的差 ≈ 一轮量化误差量级。
- **备选与否决**：① **R2**（opt 存 bf16 绝对态：误差恒 0.19%、零累积、不动协议结构）——**未否决**：
  作为 R3 重建链自测失败时的**回退档**（`plan/wire-diet.plan.md` §3 R2 原文）；② worker 回传 f32 全量
  （无重建）——否（上行 +372 KB/轮，正是本 plan 要砍的）；③ hub 只存 delta 不解码——否（下一轮 base
  无从重建 ⇒ 链断）。
- **后果**：M4 落地时 hub 侧多一条运行时依赖（依赖清单 + 文档同 commit）；M4 的收益（−372 KB/轮）与
  「hub 引入 torch」的运维成本一起记账。前置 = M0（`§2026-10-14-goalnn-wire-diet-m0`）。
  全文 → `plan/wire-diet.plan.md` §3 R3 / §11 · `docs/nn.progress.md` 开放问题 #31。

## §2026-10-14-goalnn-offline-segment-restart（2026-10-14，「停课 → 改大 iters → 开课 ⇒ 云机领到续段」：三处断点一次修掉）

- **来源**：用户 2026-10-14 报障（y5-hit 设 iters 100 收官 → 手改 500 想续跑 → 云机**一直不领任务**；
  重启课程 / 重启 trainer+hub / 手动删包重导都无效）+ 用户拍板期望行为 = **「手工停课，调大课程 iters
  参数，再开课后，自主 worker 能继续领到该课程续跑」**。plan → `plan/offline-segment-restart.plan.md`；
  现场全账 → `docs/nn/remote-transport.md` §93 + `.workbuddy/memory/2026-10-14.md`。
- **三条判据**（本次立）：
  ① **J1 续段收包**（云机判定表收紧）：`local_first = 三件齐 ∧ ¬force_pack ∧ ¬(包.plan.start_it ==
     本机.last_it ∧ 包.plan.end_it > 本机.end_it)`。加长段成立 ⇒ **收包的计划**（`ArtifactStore.start`
     换段 ⇒ 新 state `last_it = plan.start_it` ⇒ `plan_run` 的 `todo` 正好是 `last_it+1 … 新 end_it`，
     **一条旧轮都不重跑**）；其余一切情形逐字维持本机优先。**局部推翻** `§2026-09-24-goalnn-offline-local-first`
     的「三件齐 ⇒ 恒本机优先」——它把「包会把进度往回拖」与「包是本机进度的加长段」当成了同一件事。
  ② **J2 锚点端点归位**：续跑锚点的**字节**只认 `/offline/resume/blob`（元信息走 `/offline/resume`）；
     hub 的元信息端点收到 `?name=` ⇒ **响亮 400** 并指路（静默忽略正是这个缺陷活了整整一天的原因）。
  ③ **J3 开课保新鲜**：开课只在「盘上已有包 ∧ 已被消化（权重/代码比包新）」时重打；**缺包不预造**
     （★M4 口径不变，无包那条腿走 claim 触发的自动交接）；失败不连坐开课。
- **为什么必须修**：「跑满」闸锚在**包 zip 的 sha256** 上（`hub/queue_offline.py:1036`），而改 iters /
  停课 / 重开课 / 重启 trainer+hub **一个都不碰包**；重导本身又是**需求侧**触发的（要 claim 成功），
  `completed` 又把 claim 挡死 ⇒ 「没有需求就没有导包、没有导包就永远 completed」的**死锁**。
  实测：人手动点「导出任务包」立刻解（新包 sha ≠ 旧章 ⇒ 读面 `ready/claimable=true`）。
  另有一处**静默 100% 失效**：云机取锚点字节时把 `&it=&name=` 接在元信息 URL 上 ⇒ 每次拿回 55KB
  元信息 JSON ⇒ 指纹闸判「传输损坏」丢弃 ⇒ 锚点从未被采纳（本次无害，但那条腿等于死代码）。
- **被否决的备选**：hub 把「包已过期」纳入 `completed` 判据（云机会拿被消化的包按旧起点跑，把进度
  往回拖）· hub 在 `GET /offline/tasks` 里顺手触发重导（该端点的**零副作用**是写死的不变式）·
  控制台在**收官完成时**自动重导（会抹掉「已收官」这个生命周期状态，且每门课跑满都烧一次分钟级导出）·
  删掉 `local_first`（旧包会让本机已跑的轮次**重跑**，正是 §2026-09-24 要防的）· 依赖
  `CFG.force_pack=true` 让人手改 notebook（保留为**显式**逃生阀，语义不变）。
- **残留缺口（本次不修，plan §7 已列）**：① 课**一直开着**（不走停/开）而人只改了 iters ⇒ 死锁仍在
  （仍需手动点「导出任务包」）；② 云机**同一会话内**已服务过同名包 sha ⇒ 不再领（`offline_boot.py:1766`，
  防自激的必要闸；本次靠换包 sha 自然绕过）；③ 锚点只服务 hub 认定的那一轮，不补全区间。
- **门禁**：`tests/remote` + `tests/hub` + `tests/common` 全量 **1941 passed / 1 skipped** · ruff 全量过 ·
  dashboard `typecheck` + **1587 passed** · 根 `bun run check` **2561 passed / 3 skipped**。两处新判据都
  做过**先红后绿**反证（把判据退回修复前，用例必红；锚点 URL 反证与 hub 日志逐字一致）。
  全文 → `docs/nn/remote-transport.md` §93 · `plan/offline-segment-restart.plan.md`。
## §2026-10-14-goalnn-wire-diet-m3-weights-delta（2026-10-14，R1：权重回传走 int16 单 scale delta；hub **不**引入 torch）

- **背景**：`plan/wire-diet.plan.md` §3 R1 —— result 上行的大头是 `weights_json`（gzip 后实测 288,205 B），
  回传 `Δ = W_new − W_base` 的 int16 量化可降到 **87,546 B**；base 恒为**本轮下发的 init 权重**
  （双方按 `init_weights_fp` 内容寻址同一份）⇒ 残差 = 本轮量化误差、**不累积**。
- **定案 1（单 scale）**：全模型共享一个 `scale = max|Δ| / 32767`。**否决**逐张量/逐参数 scale：
  ① plan 的 1.44e-4 相对 RMS 是在单 scale 下量的，换口径要重测；② 42 个 scale 加进头里（~700 B）会
  吃掉小模型上的收益；③ 误差（RMS 3.0e-7）已比每轮真实更新幅度（RMS 2.1e-3）小两个数量级 —— 再降
  一个数量级换不来任何东西。**后人若想「顺手加 scale」，必须先重测并改这一条。**
- **定案 2（R1 不吃 torch，与 §10-2 的「hub 引入 torch」无关）**：权重的线上字节是 `nn-weights-json`
  （`save_weights_json` 写、TS 运行时也读），每个参数是 `{shape, data:<f32 LE base64>}` ⇒ 解析/量化/
  重建全在 `json` + `base64` + `numpy` 里完成（numpy 在 hub 侧本来就有：`remote/payload_slim.py`）。
  §10-2 那次「hub 装 torch」的显式决策**只管 R3/M4**（opt 是 tar + 真张量）。
- **定案 3（重建产物 = 权威字节，两档与 §12 同构）**：hub 落盘的是**重建**出来的 JSON（只替换
  `params[*].data`，其余键与**键序**原样带回、`indent=2` 与写端同形）⇒ `init_weights_fp = sha256(落盘
  字节)` 的算法一字不改；它与 worker 本机那份 f32 全量**必然不同 sha**，命中判据是 M0 的身份映射。
  `base_sha` 与 hub 那份 init 字节相等 = **exact 档**（逐字节对账）；不等 = **identity 档**（worker 用
  自己那条链当基准 ⇒ 只能靠身份闸 + 结构闸，偏差 ≈ 一轮量化误差的随机游走）。
- **被否决备选**：① 逐张量 scale（见定案 1）；② 在**绝对**权重上量化（同 §3 否决表里 opt 状态 f16
  那条灾难）；③ hub 侧只存 delta 不解码（下一轮 base 无从重建 ⇒ 链断）；④ 让 hub 为了 R1 引入 torch
  （定案 2：结构性不需要）。
- **补记（同日，落地时两处收紧；plan §13 定案 6/7）**：① **拒收面再加一格** —— 「魔数被改坏、头却
  自述 delta」的字节一律拒收（`_container_shaped`）：宽容档只留给「根本不是容器」的旧形态字节
  （旧 worker / 冒烟回显），改坏的容器当权重落盘是最坏的那一格（身子是 int16 载荷 ⇒ 静默污染链）。
  ② **读法去重**：`fmt_bit` / `container_base_sha` 下沉 `common.wire_codec`，两族容器只留 `partial` 别名
  —— 新开一个共用模块会让两个 L0 叶子一起升到 L1、连坐 `hub_client` 与 9 个上位模块（同 §13 定案 5）；
  复用账本结构克隆 18 → 16。
- **补记 2（同日，实机事故：hub-server 启动即退）**：M3 落地后 hub 进程挂 —— `remote/weights_delta` 的
  **顶层 `import numpy`**，而它是被 `remote/hub_client` 在**模块顶层**拉进 hub import 图的
  （`hub.queue → hub.queue_observe → remote.code_snapshot → remote.hub_client`）。两个独立缺陷叠在一起：
  ① **本片的错**：违反本仓「remote 子模块顶层零三方依赖（hub 侧免重依赖）」的规矩（§13 定案 1 里那句
  「numpy 在 hub 侧本来就有」是错的 —— `remote/payload_slim` 只在**打包端**被延迟 import，hub 从不读它）；
  ⇒ numpy 改**惰性**（`_np()`，同 `opt_delta._torch()`：顶层零三方依赖、缺了只在使用点响亮拒收）；
  ② **潜藏的错（先于本片）**：uv 造的 venv 没有 `executable=` 行 ⇒ `resolveVenvPython()` 解析出的是**基础
  解释器**（uv 跳板真身），而 `dashboard/src/stack/specs.ts` 的 hub-server spec 只挂 `PYTHONPATH: NN_TRAINING`
  ⇒ hub 进程**一个三方包都看不到**（numpy/torch 皆无），M4 决策 (a)「hub 引入 torch」在这台机上不成立；
  ⇒ spec 改为 site-packages + `nn-training/`（与 `launch/cli.ts` / `server/run-python.ts` 同规）。
  复现用例：`tests/remote/test_weights_delta.py::test_hub_import_graph_needs_no_numpy` /
  `::test_numeric_paths_reject_loudly_without_numpy`；spec 侧回归钉在
  `dashboard/tests/hub-server-push-arg.test.ts`；全文 → `docs/nn/remote-transport.md` §95。
  （2026-10-15 合并勘误：这个「全文」指针**无对应节** —— `git grep` 全树（含 `origin/goal-nn`）只在
  `remote/opt_delta.py` / `dashboard/src/**` 找到本补记 2 的**代码**，**没有任何一节写过这段事故正文**；
  且本篇 §95 现为「离线段的两条命」、§96 为「staged 预取搬入抬 mtime」，都不是这段内容。
  要补正文得另起一节，别照这个号去跳。）
- **后果**：R1 与 R3 共用同一套「形态位 + 容器自述 + 两档判别 + 缺 base 拒发」的纪律，形态位键
  分别为 `manifest.w_fmt` / `manifest.opt_fmt`；开关仍 `--wire-diet 0`，等 §6 的真机 A/B 通过才翻 1。
  全文 → `plan/wire-diet.plan.md` §3 R1 / §13 · `docs/nn/remote-transport.md` §94 · 代码
  `remote/weights_delta.py`。


## §2026-10-14-goalnn-wire-diet-enable-all（2026-10-14，用户令：**不跑 A/B，wire-diet 缺省全部启用**）

- **原话**：「不需要 A/B，全部启用！！！」（承接 `plan/wire-diet.plan.md` §6 的「A/B 通过前 `--wire-diet`
  保持 0」；同一会话里最后一轮 M3/R1 刚落地）。
- **定案**：① `worker/cli` 的 `--wire-diet` 缺省 `_d("wire_diet", 0)` → **1**（M1 打包端先算 reward +
  M2 训练输入字段 f16 从此缺省开）；`trainer/loop_remote_job` 两处 `getattr(args, …, 0)` 兜底同步改 1；
  ② `rl_config.schema.json` 的 `rl` 键表登记 `wire_diet` ⇒ 它从「未知键（只告警）」升为**一等回退旋钮**
  （`rl.wire_diet: 0` = 逐字节旧行为），`docs/nn/rl-config.md §1.1` 的 A 类行同步登记。
- **一个必须说清的账**：**R1/R3（M3/M4）的形态位早已是「开」** —— `manifest.w_fmt` / `manifest.opt_fmt`
  的门是 `use_init_blob` / `use_opt_blob`（= `slim AND 有字节`），而 `rl.slim` 缺省 1（2026-09-24 opt-blob-diet
  起）⇒ 本次翻的只是 M1+M2 那一档。要真正回到「逐字节旧行为」需 **两个**旋钮一起关
  （`rl.wire_diet: 0` + `rl.slim: 0`；后者代价 = 上行回 ~4.4 MB，opt/ref/init/demo 全走内联 base64）。
- **风险自担（无 A/B 就上线，写清以便复查）**：① R1/R3 的净收益依赖 M0 身份映射**命中**，未命中的 ~5% job
  每轮反而多付 ~1 MB 下行（`plan/wire-diet.plan.md` §3 R0 的账）；② identity 档下 hub 只能靠身份闸 +
  结构闸，链上偏差是量化误差量级的随机游走 —— 现在**没有**「两条曲线无偏离」这份证据；③ f16 只改传输副本
  （本机 shard 仍 f32、`data_fp` 不变）。
- **被否决备选**：① 仍先跑 A/B 再翻（用户明确否决：要的是省下那段时间）；② 只把课程配置里的
  `rl.wire_diet` 设 1 而不改缺省（否决：新课程/新机器会悄悄退回旧行为，缺省才是一次性决断）；
  ③ 把 R1/R3 的形态位从 `slim` 改挂 `wire_diet`（否决：会改动已落地的门语义、动到 M3/M4 的既有用例面，
  与「全部启用」无关）。
- **后果**：缺省行为变为「M1+M2+M3+M4 全开」；`plan/wire-diet.plan.md` §6 那条 DoD 降级为**事后观测**清单
  （五组数的取法见 §14 末条 / `docs/nn/remote-transport.md` §94），§14 存同一条账。
- **全文** → `plan/wire-diet.plan.md` §6 / §14 · `docs/nn/remote-transport.md` §94 · `docs/nn/rl-config.md` §1.1。

## §2026-10-14-goalnn-offline-code-authority-and-width（2026-10-14，离线：工具态以包为权威 + 满铺宽度修退化）

- **现场**（y5-hit2 云机 [2/2]，一手日志）：① 冷启动「64 批 × 1 局补差」—— 96 核里只有 1 核干活
  （`满铺 width=1` + `serve_pool: spawned=1`），缺口 `{2000: 11644}` 数学上补不平（宽度 1 ⇒ 最多 64 局）；
  ② 训练前 `ImportError: cannot import name 'METRICS_NAME' from 'common.shard'` —— 本机 `code.zip` 是半提交
  工作树的**混血快照**（np_core 已改 / shard 未改），而它「旧但自洽」⇒ 选码门槛（对本机 manifest 自证）
  让它**必赢**包里那份好代码；坏代码直到 `remote/train_core` 才爆 ⇒ rollout 全白跑、账本无行。
- **定案**：① **「满铺宽度」只有一个语义 = 本机槽位**（`cpu_worker_slots()` 经 `with_rollout_workers` 写进
  `spec["workers"]`，与并发同过一套夹取）：补差批宽 / 长驻池宽 / 整轮容量估算基准三处一律取它；**禁止**
  再拿「上一批跑了几局 / 首批 argv 条数」当预算（那是**结果**）。② **`code.zip` / `ts_code.zip` 是工具态**
  （可重建、与进度无关）⇒ 权威序 = **任务包 → hub `/code` → 本机产物**（最后兜底 + 响亮 WARN）；旧序
  （本机优先）只保留给**进度态**（plan/state/权重/shard）。选码门槛分档写死：包里有码就必须等于索引背书
  `code_sha256`（不符 = 包坏 ⇒ 硬拒），没背书（老包）⇒ 收下 + WARN；hub/本机档**接受为新权威**。
  ③ **换码是一次事务**：字节写回 `<dest>/code.zip` + 原子改写段级 `manifest.json` 的 `code_sha256`
  （ts 同），旧值进日志对照行。④ 候选**先过导入哨兵**（`common.shard` / `worker.ppo.np_core` / `remote.run_loop`，
  torch-free，有界子进程），失败分 `env`（缺第三方 ⇒ 放行 + WARN）与 `code`（本仓模块不自洽 ⇒ 换候选 / 拒跑）。
  ⑤ 打包端写 `code_meta.jsonc`（`commit`/`dirty`/`dirty_files`/`import_ok`）—— **无挥发字段**（固定时间戳）：
  脏树在**导出那一刻**就 WARN，而不是等云机跑到 `train_core`。
- **推翻的半条先例**：`§2026-09-24-goalnn-offline-local-first` 的「代码/TS 跟着产物走」对**工具态**失效
  （其对**进度态**的效力不变）；同一条线在 `§2026-10-14-goalnn-offline-segment-restart`（J1 续段）已经
  松过一次口，本条是把同一件事补到「不换段但换码」的情形。
- **被否决备选**：① 选包里的码但不回写 manifest 的 sha（评审 P0-1：下游 `download._ensure_code`/`_ensure_ts_code`
  会报「传输损坏」，或命中旧 `code_cache/<sha>` **静默跑旧码**）；② 比较「新旧」选代码（云机没有仓，无法定序；
  问题不是谁新而是谁权威）；③ 打包端硬拒脏树（用户边改边跑：改成 `dirty` 可见 + 哨兵早失败 + 消费端决策）；
  ④ 哨兵收 `worker.ppo.common`（顶层 `import torch` ⇒ 无 torch 机器把环境缺件误判成候选坏、全候选被误杀）；
  ⑤ 补差批宽度按缺口/est 逐批反解（`plan/rollout-topup-fastpath` §1.7 已否决：批越补越小、固定开销占比趋 100%）；
  ⑥ 把首批就按机器宽度铺（`volume_block` 是 hub 组装/节点重放两侧共用的语料契约，导出机不知道云机核数）。
- **后果**：已导出的任务包**无需重导**（宽度在节点侧生效）；`code.zip` 多一个成员 ⇒ **一次性** sha 变更
  （之后同源码仍同 sha：固定时间戳 + 无挥发字段）；boot/轮日志里「槽位 = 批宽 = 池宽」三个数应相等，
  `serve_pool: spawned` ≥ `min(槽位, 局数)`。
- **全文** → `plan/offline-code-authority-and-width.plan.md`（实施契约 + §4.9 回填）· 评审/处理结果 →
  `plan/offline-code-authority-and-width.review-bf.md`。

## §2026-10-14-goalnn-offline-local-disk-first（2026-10-14，离线/rollout：**删掉网络盘测速探针** + eval 腿包上界 + `submit` 装配后移）

- **现场**（云机 `y5-hit` 自主 worker，一手日志）：02:48:49 → 02:51:08 **139s 静默**——`轮次补传检查` 的
  `ticks 5→6` 而 `cpu 只 +0.91s`（纯阻塞 IO，正常轮 +19~23s）；缺口末尾才打出 eval 腿 `_layout()` 的唯一一行，
  而 eval 本体只用 2.0s。同一轮 rollout 腿刚报过 `probe_probe=超界回退｜5s 内没返回`。
- **定案（A 腿，本次落地）**：
  ① **删掉网络盘测速探针**——`common/scratch` 的 `probe_write_speed_mbps`（8MB 顺序写 + fsync + 回读）、
  `SCRATCH_PROBE_BYTES`、`SCRATCH_PROBE_MIN_SPEEDUP`（速度闸 F6）、`probe=` 注入缝，以及
  `common/game_watch.PROBE_CEILING_SEC`（`plan/rollout-topup-fastpath` §3.1 的 5s 上界）。落点判据**只剩容量**
  一条（`free ≥ need × 1.5`）；`job_root` 只用于**命名**，其上一字节都不写。
  ② **rollout 落点段改回裸调**（只有本地盘操作 ⇒ 无上界也有界），`probe_probe` 读数换成
  `落点=本地:<path>｜建布局 x.xxs` ∨ `落点=网络(回退原地｜成因=…)`；`install_tree`/`mirror_inputs` 的
  `call_bounded(SCAN_CEILING_SEC)` 保留（真网络 IO）。
  ③ **eval 腿五处裸调用一律包上界**（`install_tree`→60s；`clear_dir`×2/`install_file`→`DRAIN_CEILING_SEC`），
  超界/失败 ⇒ **本段**降档回退原地 + 响亮一行，并补上落点/建布局读数（此前**零读数**）。
  ④ **`CloudEvalRunner.submit()` 只入队 thunk**：`_build`（落点解析 + 逐字节拷权重）挪进后台 `_loop` 求值
  ⇒ 装配挂死只拖 eval 自己，不拖训练主循环（安全性 = 调用点在 `_checkpoint` 之后，权重已原子落盘）。
  ⑤ **`NN_ROLLOUT_SCRATCH=off`（同族 `none/0/false/no`）⇒ 强制回退档**（跳过候选链、不建目录、不探针）：
  此前它被当成路径 ⇒ 在 cwd 下建一个叫 `off` 的目录并当 scratch 根用（文档承诺的一键回退是坏的）。
- **被否决备选**：① **保留探针但给它包上界**（= §35.1 已落地的方案）——`call_bounded` 是「可放弃的等」
  不是熔断（弃掉的 daemon 线程不杀不 join）⇒ 买到的是「等满 5s + **一条仍在写 8MB+fsync 的残留线程**」，
  而残留线程继续压同一个挂载点；② **给探针加缓存**（`rollout-topup-fastpath` §3.8 的未落地项）：省掉的是
  「重复测」，而这里要省的是「**不再测**」；③ **把 eval 腿的裸调也交给 `submit` 的 thunk 就算完**：装配挪走
  了，但缺的**上界与读数**仍在（坏盘上它照样挂在后台线程里、日志里照样零读数）；④ **把 `off` 当成路径的
  现状保留**（不改）：逃生门是坏的比没有更糟——操作员以为已经回退，实际在 cwd 下跑了 scratch。
- **推翻的半条先例**：`plan/rollout-topup-fastpath.plan.md` §3.1/§0(4) 的「探针包上界」（→ 删探针）；
  `docs/nn/runtime-opt.md` §34.2 的速度闸 F6（→ 删闸，改由落点读数 + 首轮墙钟取证）。
  **不推翻**：§3.2–§3.5（满铺 / 轮级 `RoundContext` / 取消搬回）全部保留。
- **后果**：`docs/nn/runtime-opt.md` 新增 §36 + §35.1/§34.2 指针；文本断言基线抬一格 1457 → 1460
  （账在该模块头：新增 5 条读数行 − 删 2 条旧读数行）· 「重投计划」的盘抖动读数键从 `probe_probe` 改为
  `落点` · 逃生门 `NN_ROLLOUT_SCRATCH=off` 有真语义 · 好轮墙钟收益 ≈ 0（探针 0.009–0.015s），
  收益全在**坏轮**（139s → 本地闸 + ≤60s 后台上界）。
- **诚实账（本条目只覆盖 A 腿）**：产物根**还没**搬本地盘、网络 `backup/` 与反向恢复腿也**还没**落
  ⇒ 「网络盘在常态路径上出现 0 次」这句话目前**不成立**（B/C 腿见 `plan/offline-local-disk-first` §3.2/§3.4/§3.5）。
  A 腿单独成立的部分：**训练主循环上不再有任何一次「由网络盘测速决定」的等待**。
- **全文** → `plan/offline-local-disk-first.plan.md`（v2）+ 评审 `plan/offline-local-disk-first.review-bf.md`
  （处理结果 §11）· 技术档案 → `docs/nn/runtime-opt.md` §36。

## §2026-10-14-goalnn-offline-local-disk-first-b（同日续：**产物根落本地盘** + 网络盘 `backup/` + 恢复腿）

- **上位**：上一条（A 腿 = 删探针）的 §3.2/§3.5；评审 P0-1（拍板-1 指错了开关）与 P1-3（恢复优先级）。
- **定案**：
  ① **产物根默认 = 节点本地盘**：`<NN_LOCAL_ROOT ∨ /tmp>/battle2-offline[-<课>]`（`resolve_local_root`，
  显式 `CFG.local_root` 同义）；**不可写/不可建 ⇒ 回落网络侧产物根 + 响亮一行**（拍板-2：产物根没有
  第二处落脚，拒跑比换个地方跑更糟 —— 与 scratch 的 env 语义相反，那里显式不可写 = 响亮失败）。
  ② **生效点是 `--artifacts`**（评审 P0-1）：`course_work_dir` 拆成 `net_root`（下载缓存/包/job 目录/
  `hub.token`）与 `net_artifacts_dir`（= 今天的 `work/run`）；`run_one_course` 的 `dest`/`--artifacts`
  改指本地根，网络侧**常态零写入**。`resolve_artifact_dir` 的「本地优先」**只作兜底**（生产路径恒有
  显式 `--artifacts`；唯一走默认分支的 `kind=run` 腿已退休）。
  ③ **备份抽屉 = `<网络产物根>/backup`**（`--backup-root` 由此递到 `run_loop`；新 `resolve_backup_root`
  只作兜底）。⚠ 命名硬约束：**不得**以 `it-` 开头 —— `ArtifactStore.finalize/_refresh_latest` 按顶层
  `it-*` 扫轮次，撞上就把备份打进交付物（与 `job_fs.RESULT_BACKUP_DIR` 同名不同义）。
  ④ **恢复腿**（「产物落本地」的必要配套；没有它 cell 一死 = 段进度静默归零）：`restore_from_backup`
  —— 本地三件不齐 ∧ 备份齐（三件 + `it-<last_it>/weights.json`）⇒ 原子反向铺回本地根（逐件
  `os.replace`、三件锚点最后才进），本地非空⇒**一个字都不动**；优先级 = **本地根 > hub 锚点 > 备份**
  （hub 那一档不必在这里分先后：`--resume-dir` 照旧递给 `run_loop`，它自己按「不新于当前进度就丢」处理）。
  ⑤ **口径改写**：`ArtifactStore` 的「新会话用同一个目录再跑一次即可续上」⇒ **可续跑性 = hub 已收轮次
  ∪ 网络盘 `backup/`**；`package_partial`（会话中途、人点的 cell）改读**本地根**。
- **被否决备选**：① 只改 `resolve_artifact_dir`（评审 P0-1 的事实：产物一个字节都不会搬；默认分支在生产
  路径上不被求值）；② 把 `work_dir` 一起搬本地盘（那是下载缓存 + job 目录 —— 搬它等于把网络流量换成
  重下载，与本计划反向）；③ 产物根用 `/dev/shm`（那是 scratch 的家：RAM 一回收就什么都没了，而产物契约
  是「进程随时可以死，卷上永远自洽」）；④ 本地不可用就**响亮失败**（拍板-2 否决：安全方向是「宁可慢也跑完」）；
  ⑤ 恢复腿与 B1 分两批落（本仓纪律：两者必须同批 —— 否则「产物落本地」会静默吃掉续跑能力）。
- **后果**：`docs/nn/runtime-opt.md` §36 续写（两个根 + 恢复腿 + 还没落的那半）· `artifacts.py` 模块头/
  `ArtifactStore` docstring 改口径 · notebook 主文案改（中途取包 cell 指本地根）· 文本断言基线 1460 → 1462
  （两条落点/恢复读数行的账见模块头）· 用例把 `local_root` **显式钉住**（缺省 `/tmp/...` 是跨用例共享位置）。
- **诚实账（本条目仍只覆盖 B1/C2）**：网络盘 `backup/` 的**写方**（每轮观测 pending + 段末 sweep）与
  段末交付面的新判据（C1/B4）**还没落** ⇒ 「回传失败 ⇒ 备份」目前没有写方；恢复腿的读方已就位等它。
  在那之前「网络盘在常态路径上出现 0 次」成立（产物面），但「出现 1 次」那一半还不存在。
- **全文** → `plan/offline-local-disk-first.plan.md` §3.2/§3.5 · `docs/nn/runtime-opt.md` §36。

## §2026-10-14-goalnn-offline-local-disk-first-c（同日续：**备份抽屉的写方** + 段末交付面判据）

- **上位**：上两条（A 腿 = 删探针 · B1/C2 腿 = 两个根 + 恢复腿）；计划 §3.4 = C1/B4。
- **定案**：
  ① **C1 `backup_pending()`**（`remote/offline_deliver.py`）：判据逐字沿用 `pending()`（磁盘上有、
     `delivered.json` 里没有）= 「回传失败」的**持久化**定义，不引入新状态源。落点 `<网络产物根>/backup/`
     （名字由调用方解析后递进来，**不在本模块拼**；硬约束：不得带 `it-` 前缀）。纪律逐条落实：
     **绝不抛**（抽屉建不出来/判据算不出只 WARN）· **不改 job 状态** · **有界**（逐件
     `call_bounded(DRAIN_CEILING_SEC)`）· **幂等**（已存在的轮次目录跳过）· **常态零写入**（`pending()` 空
     连抽屉目录都不建）。轮次目录名走 `ArtifactStore` 的那一个口径（`it-<03d>`）——自己拼 `it-1`
     会找一个不存在的源目录（实测三轮全 0 字节失败而抽屉空空）。
     时机：① 每轮观测（后台腿 `_push_backlog` 的末尾 / 同步腿 `sync()` 的末尾）；
     ② 段末强制 sweep（`close()`）。**进程模式由父侧扫**（`DelivererProcess.backup_pending()`）：
     子进程可能是导出那一刻的老快照（新键它不认 ⇒ 静默不备），而父侧的 `pending()` 判据逐字相同。
  ② **B4 段末交付面判据**（`remote/offline_boot`）：段末**只在三种情形**落 `deliver-<课>.zip` ——
     **无 hub**（纯手工递送：交付档是唯一动线，DoD-12）· 网络侧**有备份抽屉**（= 有轮次没送出去，
     人需要一份能导入控制台的包）· 人显式给了 `CFG.download_dir`（这块钱由人主动付）。否则只打一行
     「本段产物在本地根 …（补传全成功 ⇒ 没有交付档）」——一次段末大 zip 拷到慢挂点，正是本计划
     要移除的那类等待。
  ③ B1 期间一并把**中途那条读面**改了（`package_partial` 读本地根）：不改就是「搬完产物之后
     那个 cell 响亮地说目录为空」，把人拿产物的路整条断掉（评审 P0-2 的另一半）。
- **被否决备选**：① 备份交给子进程做（老快照不认新键 ⇒ **静默不备**，比不实现更糟）；② 备份恒开
  （哪怕零欠账也写网络盘 —— 拍板-1 的全部意义就是常态零写入，用例把它当反探针钉住）；
 ③ 段末交付档照旧无条件落（为了「人可能想要」而每段付一次大 zip 拷贝 —— 那正是 139s 那类等待）；
  ④ 备份失败改成响亮失败（它是第二份拷贝，产物本来就在本地根）。
- **后果**：恢复腿的读方终于有写方（B1/C2 留下的那个空档补上）；段末网络盘只在「有欠账 / 无 hub /
  人点名」时被碰；文本断言基线 1462 → 1466（四条响亮行的账见模块头）。
- **诚实账**：B2（持久面容量判据）与 `docs/nn/remote-transport.md` 的离线版（§81 同族）仍未落。
- **全文** → `plan/offline-local-disk-first.plan.md` §3.4 · `docs/nn/runtime-opt.md` §36。

## §2026-10-14-goalnn-offline-local-disk-first-d（同日收尾：**持久面容量判据** B2 + 备份**对账** C3 + 离线版档案）

- **上位**：前三条（A 腿 = 删探针 · B1/C2 = 两个根 + 恢复腿 · C1/B4 = 备份写方 + 段末交付面）。
  本条把上一条「诚实账」里那两笔（B2 与离线版文档）落下，并补上 §3.4 自己写明的「对账」那一半。
- **定案**：
  ① **B2 持久面容量判据**（`remote/offline_deliverable.py`）：开段前判
     `free(本地根所在设备) ≥ ARTIFACT_BYTES_PER_ROUND(4MB/轮) × 轮数 × 1.5`，`rounds` 由
     `offline_boot.run_one_course` 从 `CFG.max_iters` 递进来（取不到 ⇒ `SEGMENT_ROUNDS_DEFAULT`=100，
     现场观测的段长）。不过 ⇒ 与「写不进」同一条出口：**回落网络侧产物根 + 一行带 `free=…MB < need=…MB`**。
     判据住交付面（不是 plan §4 表里写的 `common/scratch.py` / `remote/plan_handoff.py`）——因为 B1 之后
     **产物根的生效点在 `offline_deliverable.resolve_local_root`**，`plan_handoff` 只收显式 `--artifacts`；
     判据写在「谁决定落点」那里，才不会被绕过。
  ② **轮内峰值**（4MB/轮之外的第二项）**没有并进这条判据**：它要 spec/槽位/批数，只有 rollout 腿求得到
     （`worker/iter_rollout._round_need_bytes`），由 scratch 自己的容量闸按**候选盘**算。两条腿共用 `/tmp`
     时两项相加而「合起来超了」没人在开段前判 ⇒ 由 ③ 兜（这也是如实记账，不是漏项）。
  ③ **段中盘满响亮**（DoD-14，`remote/plan_run._checkpoint`）：产物写的 `OSError` 当场记一行
     `WARN: 产物落盘失败（…）—产物根=<path>｜…换根续跑`，再转成 `ProtocolError`（`run_loop` 的失败支路：
     非零退出 + `[run] 失败`，**不会**走到 `finalize` 的成功口径 = 段末不谎报成功）；`state.json` 仍是上一轮
     （`checkpoint` 写序「先产物后状态」）⇒ 续跑锚点不受影响。`ValueError`（产物字节坏了）**不**并进来：
     那类由 `_weight_bytes` 在进 try 之前抛，归因是「结果不可用」而不是「盘写不下去」。
  ④ **C3 对账**：`backup_pending` 的幂等从「抽屉里有这个目录 ⇒ 跳过」收紧成「**字节一致**才跳过」
     （对账 `weights.json` + `opt.tar`；不一致 ⇒ 撤掉重装并记一行）。理由：本机同一轮可能被**重跑**过，
     旧字节留在抽屉里会被恢复腿当成「这一轮」铺回去（看起来完全正常）。新增孪生常量
     `offline_deliverable.OPT_TAR_NAME`（与 `ArtifactStore.opt_path` 末段对账）。
  ⑤ **备份/恢复原语不共享 `common/scratch.install_tree`（C3 的形态变更）**：抽屉要被 `it-*` 视图扫
     （`install_backup` 的 `glob("it-*")`），而 `install_tree` 的临时目录名是 `<name>.tmp-<pid>` ⇒ 崩后残留的
     `it-005.tmp-1234` 会被当成一个**轮次**恢复；恢复腿又住在 standalone 兄弟文件里（拿不到 `common/`）。
     ⇒ 抽屉侧保留自己的 `.` 前缀临时名（原语各一份是**有理由的**，不是重复）。
  ⑥ **离线版档案**：`docs/nn/remote-transport.md` **§95**（= §81 的离线版）——四个落点的常态表 ·
     日志该/不该出现什么 · 现场四步判读 · 诚实账（`/tmp` 本地介质仍未取证 · 4MB/轮是地板估算 ·
     备份不抗网络盘挂掉）。
- **被否决备选**：① 把 B2 判据写进 `common/scratch.py`（它不知道产物根，等于让每个 scratch 调用方各传一次；
  而真正决定落点的地方就在交付面）；② 把轮内峰值猜一个 GB 级常数并进判据（猜大了把好机器推去网络盘，
  猜小了等于没判——真机取证之前不猜）；③ 段中盘满让它裸抛 `OSError`（traceback 里有 `.tmp` 路径，但归因难、
  看起来像崩溃，且没有「已收轮次不受影响」这句话）；④ 幂等只判「存在」（会把重跑过的旧字节永久留在抽屉里）；
  ⑤ 为「原语唯一」把 `install_tree` 的临时名改成点前缀（会动 scratch 侧既有契约，收益只是少一份实现）。
- **后果**：开段前的三条判据（能写 · 装得下 · 段长算得准）齐了；段中盘满不再是「看起来像崩溃」；抽屉里的
  每一轮都与本机字节相符；离线链路第一次有一份**运维读法**档案。文本断言基线 1466 → 1469（三条响亮行的账
  见 `tests/test_source_text_assert_budget.py` 模块头）。
- **诚实账**：① `/tmp` 是节点本地介质、4MB/轮的取值**都要真机读数**（不由 agent 跑训练腿）；② 备份不抗
  网络盘挂掉；③ 轮内峰值与产物预算**共用 `/tmp` 时的合判**没做（见 ②③）。
- **全文** → `plan/offline-local-disk-first.plan.md` §3.1/§3.2/§3.4/§8 · `docs/nn/remote-transport.md` §95 ·
  `docs/nn/runtime-opt.md` §36。

## §2026-10-15-goalnn-offline-open-ended-segment（2026-10-15，离线包支持无限轮：课程 `iters<=0` = 无限 ⇒ 发**开区间段**）

- **来源**：用户 2026-10-14 报障两次（「hub 导出 y5-hit / y5-hit2 的任务包一直失败」→「y5-hit2 改成
  `"iters": 2000` 还是失败！」）+ 用户口径 **「① 离线包应支持 iters 0（无限轮）；② 导出离线包失败应
  响亮报错」**。plan → `plan/offline-open-ended-iters-and-loud-export.plan.md`（v2，已并入评审
  `plan/offline-open-ended-iters-and-loud-export.review-bf.md`；两份都在 `plan/` 下且是**未跟踪 md**）。
- **现场（本条目只盖 J1 这一半）**：同一门课三次尝试、三个拒因，共因是「把课程预算 `iters` 直接当成计划的
  **唯一**终点」：`iters=0` 撞两道 `SystemExit`（`trainer/loop_lifecycle.py:276` 启动期 +
  `trainer/loop_export.py:177` 轮内）；`iters=2000` 撞 `worker/plan.py:218` 的
  `RUN_MAX_ITERS_HARD_CAP=500`；`validate_plan`（`:310`）**即使**前两条放开也会拒（任何 `end_it>0` 对
  `iters_total=0` 都判「超出课程预算」）⇒ 「无限」在协议层**不可表达**。
- **定案（J1）**：**`iters_total <= 0` = 无限**——这不是新概念：`worker/cli.py` 的 `--iters` help 原文就是
  `0 = infinite (stop via --max-hours or Ctrl-C)`，`trainer/loop_lifecycle.py:370` 的
  `while args.iters <= 0 or it < args.iters` 同一口径；旧代码只是**导出侧不认**它。
  导出的包改成**开区间段**：`end_it = it + 段长`（段长 = `max_iters>0` ? `max_iters` : 硬上界），
  `plan.open_ended = true`，**`iters_total` 保持 0 原值**（改成段末会让云机把段末当成课末）。
  三处导出闸放行（启动期 / 轮内 / `step_course_iter` 的段长闸**文案**：`<0` 在无限课时是「一段」而不是
  「直到课程末尾」）；硬上界**值不动**（500），角色从「拒导闸」变成「段长」。
- **豁免带收紧（评审 P1-7）**：`open_ended` 只在 `iters_total <= 0` 时免查「`end_it` 不得越过课程预算」；
  否则一份 `open_ended=true ∧ iters_total=2000 ∧ end_it=2500` 的（写错/伪造）计划能越过课程预算——
  而那正是该校验存在的理由。`open_ended` 非布尔也拒（未知字段是忽略，已声明字段就得合法）。
- **被否决备选**：① 抬 `RUN_MAX_ITERS_HARD_CAP` 到 2000+（无限**不可**靠抬阈值表达——2000 之后还有 5000，
  且拆掉「防手写/损坏计划把节点按一整天」的闸）；② 计划不设 `end_it`（哨兵 ∞）（违「计划的终点是任务
  定义的一部分」；墙钟跨机不可复现；`planned_iters` 需要有限区间才能逐轮派发）；③ `iters=0` 只导**单轮**包、
  靠 hub 反复续（那不是无限轮，是把人手动停课换成机器高频重复；每次导出含 code-snapshot + push）；
  ④ 云机**自延长**段（`end_it_reached` 永不触发 ⇒ hub 永不盖章 ⇒ 另一节点能把同一份包再跑一遍，`served`
  闸跨会话也不挡）；⑤ hub 侧构造下一段计划（新节点会拿到与 `start_it` 不匹配的权重 ⇒ **静默错血缘**）。
- **后果与边界**：`plan_fp` / `plan_sha256` 是**规范化 JSON 的 sha（不排除任何字段）**⇒ 新键 `open_ended`
  改变计划身份：**对校验向后兼容（未知字段忽略），对身份面不透明**（这正是段滚动要的「必换包」）。
  有限模式的算术与包内容**逐字节不变**（`open_ended=False`）。**段末自动滚（J2）与「导出失败响亮」
  （J3/J4）尚未落地**，见 plan §4 动作表（B0/C/D 腿）；在那之前「无限轮」是**半自动**——段末仍需人导入
  产物 + 重新导出（plan §3.2.1 已定案承重腿 = hub→控制台自动回灌，选型②③被否）。
- **门禁**：`bash tools/githook/nn-python-gate.sh` 绿（ruff + mypy 672 文件 + pytest **4454 passed / 1 skipped**，
  47s）。文本断言基线**净 0**（新增 1 条「开区间段」响亮行 − 删 1 条旧「必须有终点」判据 ⇒ 不动基线）。
  `tests/trainer/test_loop_export_split.py` 的顶层 import 闭集**登记一格** `+common.protocol`：硬上界那个数
  只能从**同一个常量**读，抄一份进本模块就是自己拆「预判与真闸不许漂」（同 plan §3.4 的对账口径）。
  用例：`tests/worker/test_plan.py`（开区间段形状 + 有限模式逐字节不变 + 豁免收紧三档）·
  `tests/trainer/test_offline_leg_retired.py`（三处闸：无限课**真的**走到 `build_plan`（哨兵异常）·
  段长 > 硬上界仍拒 · 「无剩余轮次」那道闸没被 J1 松开）· `tests/trainer/test_loop_export_split.py`
  （无起点权重仍是响亮 `SystemExit`）。
- **全文** → `plan/offline-open-ended-iters-and-loud-export.plan.md` §3.1/§3.4 · 评审
  `plan/offline-open-ended-iters-and-loud-export.review-bf.md` · 前序 `docs/nn/remote-transport.md` §93。

## §2026-10-15-goalnn-offline-export-status（2026-10-15，导出**成败**有权威落点：python 退出层写 `export-status.json`，控制台与 hub 都读它）

- **来源**：用户 2026-10-14 报障第二条 **「导出离线包失败应响亮报错」**。plan →
  `plan/offline-open-ended-iters-and-loud-export.plan.md` §3.3（J3）· 评审
  `plan/offline-open-ended-iters-and-loud-export.review-bf.md`（P0-4 + P1-5/P1-6；本条**含一处评审勘误**，
  见最后一节）。
- **静默的机理（为什么要落到盘上）**：控制台起导出是 **detached 子进程**
  （`core/proc.ts` 的 `detached + unref`，`:43`/`:48`）⇒ **拿不到退出码**；控制台重启后子进程仍在跑 ⇒
  控制台的内存状态不可靠。于是每一方都在演「成功」：面板说「产物将落在 …」、hub 的自动交接拿 `ok:true`、
  hub 的触发账本只判 HTTP 码、束末告警只会说 `pending-export`。**失败证据只留在
  `tmp/<课>/export-bundle.log` 的末行，没有任何一方读它**（代价：每次触发白烧一次 code-snapshot +
  一次 `git push`）。
- **定案（落点与写点）**：
  ① 落点 = **与包同目录**（`tmp/<课>/export-status.json`）。python 侧
     `remote/bundle.py::export_status_path(Path(out).parent)`（`out` = `--export-bundle` 的取值），
     TS 侧 `path.dirname(taskBundlePath(course))` ⇒ **包搬到别处也不会分家**；名字与魔数
     （`EXPORT_STATUS_NAME`/`EXPORT_STATUS_MAGIC` ↔ `taskBundleStatusFileName`/`taskBundleStatusMagic`）
     读 python 源逐字对账（同 `BUNDLE_META_SUFFIX` 的手法）。
  ② 写点 = **进程退出层**（`trainer/run_rl.py` 的 `with export_status_guard(args):`）——**不是**包住那两处
     `raise SystemExit`：轮内的 `ProtocolError`（撞硬上界 / 空计划）走 `round_failure` ⇒ 落 `iter_error`
     + **同轮重试 5 次**（每次退避 30s）才退，包住 `raise` 会漏掉那条**真实**失败路径
     （「每次进程都秒退」只对启动期拒成立）。**每次尝试覆盖写**（末次即真值）+ 临时文件 + `replace`。
  ③ **成功也写**（`ok:true` + `exit_code:0`）：只写失败的话，「文件不在」就同时意味着「失败了」与
     「还没跑完」。**`null`（没写过）= 不可知，不是成功**——这个三态是这条腿的全部要害。
  ④ `reason` 是**第一手**的（写它的人就是那个进程手上的异常原文）；控制台那条读**日志尾**的
     `tailFailureReason`（`exit-watchdog.ts:211-236`）降为**找不到状态文件时**的兜底（被 kill / OOM）。
     两处各司其职，不叠第二份口径。
  ⑤ 旁挂件**不配把主腿拖红**：`write_export_status` 写不下返回 `None`、不抛；异常一律继续上抛
     （`SystemExit` 的 code 按解释器口径记：`0`/`int`/`None→0`/其它→1）。
- **读面（谁读它）**：控制台 `GET /api/taskBundleInfo` 的 5s 轮询多带 `statusLine` + `status`（不另起通道）；
  面板在课程行上亮红徽标「上次导出失败 · <原因>」；`POST /api/taskBundleStatus` 是**只读**动作
  （`READONLY_ACTIONS` + `VIEW_ONLY_ACTIONS`，零副作用）供 hub 问；回执语义从「产物将落在 …」
  改成「**成败待定**」（那句在失败时是假承诺）。
- **hub 侧：不再盲重试（P1-6 的收口）**：`hub/task_pack.console_export_status()` 只读回读；
  `poll_console_export_status()` 在**导包窗**里（云机每 ~60s 重试 claim 那条路）**限频 60s** 问一次，
  且**只认「新」失败**（那份状态的 `at` 不早于我们上一次触发——否则它属于上一轮，拿它归罪这一轮是栽赃）。
  已知失败 ⇒ `auto_handoff_decision` 直接回 **`failed`**（409 带 `give_up=true` + `export_failed=true` +
  真因），窗口 `TASK_BUNDLE_FAILURE_WINDOW_SEC=900`；**窗口锚是那份状态的写入时刻（持久）**，不是进程内
  计数——触发账本会被 `reset_auto_handoff_triggers` 在「换主 ∨ 超窗」时清零，所以「3 次用完就 give_up」
  在时间轴上不成立，现场正是这么演成「一直在失败」的。**不可知不是失败**：控制台不可达 / 旧版本 ⇒
  结论一字不变（只负责「知道失败时少烧资源」）。
- **被否决备选**：① 只让控制台读**日志尾**（`tailFailureReason`）——控制台重启即失去活进程日志，而 hub
  根本读不到；② 控制台在 `watchExportExit` 里「失败就自动重试」——静态拒因重试一万次也一样，
  动态失败应**响亮上报**；③ 把状态塞进 `/api/state`（那是高频大 payload，而这是按需一问）；
  ④ 让 hub 在 claim 的**临界区**里同步等控制台（`pending_export` 不占闸正是为了不冻住所有盘——
  本次把回读放在已经做网络调用的那一支，并限频 + 有界超时）；⑤ 把「已知失败」写进 `stale_reason`
  （那是**另一个东西**：包起点 sha vs 活动权重；本次新增字段 `export_failed`，不挤别人的语义）。
- **未覆盖的边界（诚实账）**：① 比 `run_training` 更早的启动期拒因（课程未开课 / `--course` 与 CLI 冲突）
  **不进**这个文件——那条路靠控制台的 `tailFailureReason` 读日志尾兜底；② 被 SIGKILL/OOM 的导出不会写；
  ③ 状态文件是**最后一次尝试**的事实（不是历史）：反复失败只留最后一条原因；④ 面板红徽标与
  `statusLine` 只在 `taskBundleInfo` 轮询里上屏（5s），不推送。
- **门禁**：`bash tools/githook/nn-python-gate.sh` 绿（ruff + mypy 673 文件 + pytest **4465 passed / 1 skipped**）·
  `cd dashboard && bun run typecheck && bun run test` 绿（**1588 passed / 0 fail**）·
  `bun dashboard/src/server/build.ts` 三份 bundle 过。文本断言基线 1469 → **1470**（+1 = hub 递给云机的那行
  `trigger_note`；理由见 `tests/test_source_text_assert_budget.py` 模块头，同批能改的都改成了行为断言）。
  用例：`tests/remote/test_export_status.py`（8 例：非导出腿一字不写 · 成功也写 · 第一手原因 + 异常原样上抛 ·
  `SystemExit` 四种 code · 轮内 `ProtocolError` 也落盘 · 覆盖写 + 无 `.tmp` 残留 · 写不下只降级）·
  `tests/hub/test_auto_handoff.py`（+3：控制台报失败 ⇒ 不再盲重试 + 真因进读面 + 限频 ·
  旧失败与不可知**不归罪** · `failed` 窗口过期与清零）·
  `dashboard/tests/auto-offline-handoff.test.ts`（+两段：三态读面 · 名字/魔数/落点跨语言对账）。
- **顺手修的一处**（**与本次改动无关的既有红**，见勘误）：同文件里「排除表与 `pack_code_zip` 对账」
  用的是写死的 `py.slice(i, i + 3000)` 窗口，而 `eb26884b`（2026-10-14）把该函数 docstring 加长后
  排除名单被推到 3000 字之外 ⇒ **HEAD 上这条用例本来就是红的**。改成「整个函数体」（到下一个顶层
  `def`）——覆盖面只增不减，也不会再因注释变长而假红。
- **勘误（评审 P0-4 的第一条是错的）**：评审说「落点在 `courseLogDir()` 而不在 `tmp/<课>/`」——
  实测 `core/paths.ts:33` 的 `LOG_DIR = REPO_ROOT/tmp`，`stack/specs.ts:30-32` 的 `courseLogDir`
  = `path.join(LOG_DIR, course)` ⇒ **同一个目录**。v1 原稿的 `tmp/<课>/` 本来就对；
  已把勘误追加进评审文件（`plan/offline-open-ended-iters-and-loud-export.review-bf.md` 末节），
  实施改用「与包同目录」这条更根上的口径。评审的另两条（全员演成功 · 写点在进程退出层）成立，已按原评审实施。
- **全文** → `plan/offline-open-ended-iters-and-loud-export.plan.md` §3.3 · 评审
  `plan/offline-open-ended-iters-and-loud-export.review-bf.md`（含勘误节）。
## §2026-10-14-goalnn-wire-diet-hub-unplugged（2026-10-14，hub 与 numpy/torch 解耦：delta 容器只在 trainer 侧被消费）

- **背景**：`§2026-10-14-goalnn-wire-diet-m4-hub-torch` 选了 (a)「hub 引入 torch」，把 torch 摆进了
  **hub 进程**；随后 2026-10-14 实机事故（`remote.hub_client` 顶层 `import numpy` ⇒ hub-server 启动即退，
  `ModuleNotFoundError`）暴露了一件事：**这条依赖从来不是「hub 要算」，而是 import 图连坐**。
- **事实（核过代码）**：reward 由 **trainer** 在打包端算（`publish_job` → `remote.payload_slim`）；
  delta 重建由 **trainer** 落位时做（`verify_and_land` → `opt_delta` / `weights_delta` 的 `land_*`）。
  生产 `hub/` 里**零处**调用 `publish_job` / `verify_and_land`——只有 `hub/smoke_loopback.py`（回环冒烟
  脚本，非 hub-server）跑全链。hub 之所以碰它们，只因 `hub.queue_observe(L3) → remote.code_snapshot(L2)`
  借了 `pack_code_zip`/`pack_ts_code_zip`，而 `code_snapshot → remote.hub_client(L1)` 又在**顶层**
  import 了两个容器模块（`weights_delta` 吃 numpy、`opt_delta` 吃 torch）。
- **决定**：**D1** hub_client 取消顶层 import 两个容器模块（收进 `publish_job` / `_pack_opt_init` /
  `verify_and_land` 的函数体，取值点内联）⇒ hub 的 import 闭包从此**不含** `remote.weights_delta` /
  `remote.opt_delta`；**D2** 撤 `dashboard/src/stack/specs.ts` 给 hub 挂 venv site-packages 的补丁（hub
  不再需要三方包，只留一条指向判据的注释）；**D3** 判据 = 进程级
  `tests/remote/test_hub_import_graph.py`（只挂 `nn-training/` + 悬空 numpy/torch ⇒ `import hub.server`
  必须成功，且闭包不含那两个模块；先红后绿）；**D4** 修正 §m4-hub-torch 的措辞：torch/numpy 只住
  **trainer**，两个容器模块是 **L0 叶子**，由 trainer 延迟消费。
- **备选与否决**：① 把 `pack_code_zip`/`pack_ts_code_zip` 拆到 stdlib 叶子、让 `code_snapshot` 完全不进
  `hub_client`（结构性彻底断开）—— 否**本次不做**（要新模块 + `remote_dag` LAYERS + 三条拆分守卫 + LOC
  预算重排；D1 已达成「hub 闭包零三方依赖」这个目标）· 留作后续「B」；② 用模块 `__getattr__` 令
  `hub_client._land_*` 别名惰性可见以保住测试里的旧名字 —— 否（PEP 562 的隐式魔法，AGENTS §13「simple
  beats clever」）；改为测试直接从容器模块 import（3 个文件）；③ 保留 specs.ts 补丁「以防万一」—— 否
  （闭包已证零三方依赖；补丁让 hub 能 import 任意三方包，反而掩盖边界）。
- **后果**：hub 可在**只有 `nn-training/`**（无 venv site-packages）的环境里起来；`remote.hub_client` 顶层
  少两条边，DAG 层号不变（两容器仍 L0、hub_client 仍 L1）。全文 → `plan/wire-diet.plan.md` §13（该处
  2026-10-14 的 ⚠ 更正里「hub 能跑 torch」一句按本条作废）。

## §2026-10-13-goalnn-y5hit2-design（2026-10-13，y5-hit2 接力腿：y5-hit 现文件逐字＋起点换 it500）

- **背景**（用户令）：复制 y5-hit 为 y5-hit2，bc 用 y5-hit.it500，其它不变。y5-hit 盘后状态：
  hitRate 0.396→0.836、kills 守住、中经 it100 恐惧塌（17 clears）后收回；现文件已被展成
  iters 0/max 24/eval 400（代码为准，纠过一次 100/12/100 的过期假设）。
- **决定**：D1=`y5-hit2`（y5-hit 现文件逐字，差集仅名义键＋起点 it500〈sha `961bacde390cbf02`，
  同代直连，opt fresh〉；§15.5 新账本 tmp/y5-hit2＋backup weights/y5-hit2）；
  D2=门沿用（hitRate 0.45 成 floor，kills≥3.5/D01≤85 活线；fail⇒杀）。
- **备选与否决**：① 按我最初设计的 100/12/100 写 —— 否（现文件 0/24/400 是执行中的事实，
  "不变"指相对现状；已纠）；② 换配方/加变量 —— 否（纯接力，无新变量）；③ 用 it500 前的峰点 ——
  否（用户点名 it500；末端横盘，无更优点）。
- **后果**：`nn-training/curricula/y5-hit2.jsonc`（逐字 diff 绿）；全文 → `docs/nn/experiments.md` §102；
  开课人点（agent 不得启动训练）。

## §2026-10-13-goalnn-y5far-design（2026-10-13，y5-far/y5-pos 防近腿：直接双税 vs 站位税，保命中）

- **背景**（用户令）：y5-hit.it470 开局 near4 0.667（人类 0.345，2 倍）但命中反超，要求保命中防贴脸/包围；
  y5-hit2 改名 y5-far（未开课无账本）；另设一条并行比较腿验其它激励。剂量见 §104（near −4.37/encl −1.23
  现状；corner −2.72/threat −4.01 现状）。
- **决定**：D1=`y5-far`（near×2＋encl×2；起点 it500 sha `961bacde…`；seed_rotate 补回 150〈复制 bug〉；
  staging 500/24/200 沿用；新账本 tmp/y5-far＋backup weights/y5-far，§15.5）；
  D2=`y5-pos`（corner×2＋threat×1.5，near/encl 留原值；其余与 y5-far 逐字同；新账本 §15.5）；
  D3=门双腿一字同（hitRate≥0.75＋nearSq≤110/千＋kills≥3.5＋encl≤100/千；D01≤85/fear 护栏；fail⇒杀）；
  D4=双分支预注册（far 动↔直接有效；pos 动↔站位更安全；双动看剂量；双不动另案）；
  D5=并行就绪（零共享，容量人定）。
- **备选与否决**：① near×3（y1 量级）—— 否（73% 已逼冻结线，冠军底子也别一次加满；×2＋护栏先行）；
  ② threat 不动（只加 corner）—— 否（包围蕴含火线暴露，threat×1.5 与 corner 同向，束内 paired）；
  ③ dist 加回拉远 —— 否（刚删的彩票，不走回头路；远＝另一种极端）；④ hurt 加码 ——
  否（phase-1b 排队的是去 hurt（y1h），反向操作等 y1h 先判）。
- **后果**：`y5-far.jsonc`＋`y5-pos.jsonc`（全键 diff 绿，证据 `tmp/y5far-ev-check.py`）；
  全文 → `docs/nn/experiments.md` §104；开课人点（可并行，agent 不得启动训练）。

## §2026-10-13-goalnn-y5far-verdict（2026-10-13，y5-far 恐惧塌杀＋y5-pos 偏离续跑：near×2 too hot 实锤）

- **背景**：y5-far（near×2/encl×2）it20 峰 213 clears → it68 rollout win 破 0.2 → it75 KL 0.47
  spike＋eval 悬崖（1 clear）→ it76+ 双死（win 0.006；贪心 stuck→3000/cells→1/shots→0）；
  it40 已挂 3/4。y5-pos（corner×2/threat×1.5）it218 win 0.743，eval 72→281 clears，
  kills→4.23，hitRate 守住，D01 104→66，near/encl −21%/−23%；it40 字面挂 3/4（hitRate 差 0.002）。
- **决定**：D1=y5-far **杀＋立刻 halt**（it218 空烧中）；D2=y5-pos **§6.4 偏离续跑**
  （字面挂线但唯一活口＋端点涨＋税在动；重判 it300/自然收官）；D3=paired 判：
  站位胜、直接死（near×2 73% killEV 75 轮杀冠军；y1 机制 ×2 复现；§94 再添一证）。
- **备选与否决**：① y5-pos 按字面杀 —— 否（杀健康腿保门面；偏离已记）；② y5-far 抢救（降税续跑）——
  否（§15.5 不跨改动续跑；双死无可救，教训入库即可）；③ near×1.5 重开一腿 ——
  否（y5-pos 站位线已走通，不分散算力；剂量曲线以后如需再议）。
- **后果**：杀/续全是人点（控制台；y5-far 先 halt 停烧）；全文 → `docs/nn/experiments.md` §105。

## §2026-10-13-goalnn-eval-contamination-audit（2026-10-13，y5-far/y5-pos eval 混入 arena 局＋我漏 seed_rotate＋tmp/y5-hit 消失）

- **背景**：备 y5-near 全键 diff 时发现：y5-far/y5-pos 现文件 eval "2000-2003"×50（我落盘的是单关），
  且四份我经手文件漏 `seed_rotate`（y4/y5/y5-far/y5-pos；y5-near 有）。另 tmp/y5-hit 整目录消失。
- **查明**：① 2001+ 无 stageJson ⇒ 纯本地直跑必 crash（实证 `stage.tiles` null），远端走 ARENA 解析 ⇒
  y5-far/y5-pos eval＝100 c05＋300 arena；同 seed 跨 ID 不同局为铁证。② seed_rotate 缺席＝rollout
  旧固定局数语义，只伤采样效率，不伤 eval verdict；且 seed_rotate 进 corpus fp，**在跑腿加不得**
  （加即 corpus 变，热加载拒）。③ eval 改回单关**允许热应用**（corpus_fp 明示排除 eval_*，
  属判据/预算类，下一 iter 生效，不断腿）——已核对 `biz/corpus_fp.py:26-70` 与热加载分类学。
- **决定**：D1=verdict 全部按 s2000 重读（y5-far 塌成立更干净；y5-pos 由成长修正为健康横盘，
  §105 部分收回，记 §106）；D2=**建议把 y5-pos eval 改回单关**（"2000"×200，与 y5-near 同深，
  热应用不断腿；改配人点，改后行自动纯洁）；seed_rotate 在跑腿**不动**（corpus 拒收）；
  D3=y5-near 保持干净单关（未动，“2000”×200＋seed_rotate 在）；D4=tmp/y5-hit 之死暂记（panel＋
  权重在，可重算；§96–103 数已归档）；D5=校验脚本从此全键覆盖（已在 y5far/y5hit2 两案执行）。
- **备选与否决**：① 现在把 y5-pos eval 改回单关 —— 否（corpus 拒收会断腿；不断序列比纯洁重要，
  过滤读等价）；② 重写 §105 —— 否（只增不改；收回记 §106）；③ 补跑 y5-hit 恢复 log ——
  否（500 权重＋panel 都在，真要用时再算，不预烧）。
- **后果**：全文 → `docs/nn/experiments.md` §106；三个待人令见正文问题。

## §2026-10-13-goalnn-eval-authorship-correction（2026-10-13，用户手动编辑为准：混合 eval 是设计，§106 定性收回）

- **背景**：用户明示 y5-pos/far 课程文件系其手动编辑、以其为准。§106 把 "2000-2003" 定性为污染
  是错的——它是用户的混合 eval 设计（100 c05＋300 arena）。"改回单关"建议（旧 D2）撤销。
- **决定**：D1=§106 定性收回（改称混合设计；s2000-only 数字与重读法继续有效，记 §107）；
  D2=y5-far/y5-pos 文件冻结（不再提改配；y5-far 塌 verdict、y5-pos 健康横盘 verdict 不受影响）；
  D3=归属澄清（seed_rotate 缺席：y5-far/y5-pos 系用户编辑；y4/y5 系我原始遗漏，done legs 只记）；
  D4=y5-near 保持我的版本（干净单关＋seed_rotate；与 y5-pos rollout 口径差已披露，eval 可比）。
- **备选与否决**：① 重写 §106 —— 否（只增不改；收回记 §107）；② y5-near 跟随改混合 eval ——
  否（未开腿，干净单关对 near 判决最锐；待人令见问题）。
- **后果**：全文 → `docs/nn/experiments.md` §107；y5-near eval 口径待人令（见正文问题）。

## §2026-10-13-goalnn-y5near-mixed-eval（2026-10-13，y5-near 跟混合 eval＋y5-hit 日志归档确认）

- **背景**（用户令×2）：① y5-near eval 跟混合设计（"2000-2003"×50）；② tmp/y5-hit 系用户清理的。
- **决定**：D1=y5-near eval 改混合（除名义/起点/near×1.5/iters150/max12 外与现 y5-pos 全键逐字同，
  校验绿）；门读 headline，s2000 为真值附读。D2=y5-hit 日志归档确认（§96–102 数封存为只读；
  panel＋500 权重在，可重算）。D3=y5-near 开课仍待人点。
- **备选与否决**：① y5-near 保持干净单关 —— 否（用户令跟随；headline 逐行可比的价值大于纯洁，
  s2000 切片保留真值）；② 恢复 tmp/y5-hit —— 否（用户清的；权重＋panel 足够）。
- **后果**：全文 → `docs/nn/experiments.md` §108；开课人点。

## §2026-10-13-goalnn-y5pos-kill（2026-10-13，y5-pos 杀：it250 双连破 kills 线＋腰斩，漂移式死亡）

- **背景**：it245→255 eval kills 3.55→3.02→2.98（<3.5 双连破）＋kills −16%（y 系掐线 −15%）＋
  rollout win 0.55→0.24（10 轮）＋dead 9→25＋cells 59→45＋shots→9.2；hitRate 0.88/D01 61/
  KL/熵全稳——漂移死非爆炸死。it129 dip 史作对照（未破线＋带内振荡，已收回；本次出带＋加速）。
- **决定**：D1=y5-pos **杀（人点 halt）**；D2=y5-near 为 lineage（it27 健康，kills 4.14–4.35 全超父腿，
  near×1.5 无恐惧，剂量判待 it40）；D3=y5-pos.it215 文件永存（后悔药：任何时点可重开分支）。
- **备选与否决**：① 等 it260 确认 —— 否（三线齐响＋出带加速，等即看血；"塌了别等"成文纪律）；
  ② 降税抢救续跑 —— 否（§15.5；漂移死救不回，y1 前例）；③ 连 y5-near 同杀 ——
  否（子腿健康且超父，株连无据）。
- **后果**：halt 人点；全文 → `docs/nn/experiments.md` §109。

## §2026-10-13-goalnn-y5term-design（2026-10-13，y5-term 终局腿：y5-near 加 wWin＋5.0/timeout−5.0）

- **背景**（用户令）：以 y5-near 为基础加过关奖和超时税；其它（用户调过的 iters500/混合eval/
  seed缺席/max12）不动；BC 仍用 y5-pos.it215。剂量：y5-near.it25 clear +23.20 vs timeout +23.41
  （苟免费实锤）vs gameover −12.38；wWin +5.0（a2 同值同比）⇒ clear ≈28.2；timeout −5.0 ⇒ ≈18.4；
  自杀检查 30 点 margin ⇒ 不设 lives 税（倒挂即加，watch）。
- **决定**：D1=`y5-term`（现 y5-near 逐字＋wWin 项/键＋terminal{timeout −5.0}；起点 it215
 （sha `f69385b36a1bcd11`，与 y5-near 同起点隔离终局效应）；kickstart 关，opt fresh；
  新账本 tmp/y5-term＋backup weights/y5-term，§15.5）；D2=门（y5-near 四线＋终端线 it40
  timeout≤15%＋clears 不掉；fail⇒杀）；终点五线（cap 500）。
- **备选与否决**：① 加 lives 税 —— 否（margin 30 点，倒挂是 watch 项不是预设）；② wWin 按 killEV
  全额（+12）—— 否（a2 同比 40% 已够拉开 10 点 gap；终局大额＝方差炸弹）；③ 用 y5-near 后期权重
  当起点 —— 否（用户点名 it215；同起点才隔离终局效应）；④ 顺手把 seed_rotate 补回 ——
  否（用户调过的不动；y5-near 现状无它，逐字优先）。
- **后果**：`nn-training/curricula/y5-term.jsonc`（逐字 diff 绿）；全文 → `docs/nn/experiments.md` §110；
  与 y5-near 并行就绪，人点（agent 不得启动训练）。

## §2026-10-13-goalnn-y45-close（2026-10-13，y4/y5 结课报告落盘：跑满非杀；y1 系删除系列自 y4 起）

- **背景**（用户令）：y1 课程全删（y1/y1k/y1f/y1d/y1h/y1a.jsonc 已删；其 tmp 停在 it5/43/40/3，
  皆 halt 态，无在跑腿丢文件）；本系列自 y4 起；给已停 y4/y5 写结课报告。
- **决定**：D1=y4/y5 文件各追加尾部结课报告节（规格头不动；load 复验绿）：
  y4（病灶动终点平命中税）/y5（买谨慎不买本事），数与 §101/§96 一致；
  D2=在跑腿状态确认（y5-near it34 win 0.540 续跑中；y5-pos it260 垂死待 halt；y5-far it218 空烧待 halt）；
  D3=y4/y5 文件冻结（结课后不动）。
- **备选与否决**：① 报告写进头注 —— 否（规格头冻结，尾部追加零风险）；② 顺手 halt 在跑腿 ——
  否（halt 是人点；只报状态不动手）；③ 补 y1 系结课报告 —— 否（用户删了即归档意愿，不追）。
- **后果**：全文 → 两课程文件尾部节；y5-far/y5-pos halt 仍待人点。

## §2026-10-13-goalnn-y5legs-close（2026-10-13，y5-pos/y5-hit/y5-far 结课报告落盘）

- **背景**（用户令）：已停三腿写结课报告。y5-pos it260（杀令已下，仍在烧需 halt）、
  y5-hit 超 500（非杀，跑满）、y5-far it218（杀令已下，空烧中）。
- **决定**：D1=三文件各追加尾部结课报告节（规格头不动；load 复验绿）：
  y5-pos（漂移死，hitRate 至死守住；混合口径+s2000 注）/y5-hit（命中 0.40→0.84＋恐惧可恢复；
  tmp 已清，数以归档为准）/y5-far（爆后塌，s2000 同塌）；D2=终局数更新
  （y5-pos.it260：49 clears/kills 2.20/shots 7.1 破 fear 线——杀令再确认；y5-far.it215 尸体确认）。
- **备选与否决**：① 报告写头注 —— 否（规格冻结，尾部零风险）；② 等 halt 后再写 ——
  否（数已定，halt 是独立人动作）；③ 恢复 tmp/y5-hit —— 否（用户清的；已归档）。
- **后果**：全文 → 三文件尾部节＋`docs/nn/experiments.md` §111；halt 仍待人点。

## §2026-10-15-goalnn-offline-delta-readside（2026-10-15，离线 delta 权重：读侧落地 + 两版后缀 + 不重传）

- **背景**（用户报障）：半/全离线段（`kind=run`）的逐轮回传走 R1 —— 权重是 **int16 delta 容器**
  （`BWD1`）；hub 只做原样收发 ⇒ 容器被逐字节落进活动指针 `tmp/<课>/weights.json`、回传树、
  deliver 镜像与 `nn-training/weights/<课>/` 归档。本机消费者（trainer 推给 sampler 节点、本机
  PPO 装载、TS eval runner）只认整份 `nn-weights-json` ⇒ 每个节点 `JSON.parse` 报
  `Unexpected identifier "BWD1"`；云机下一轮的 init 也成了容器（现场 `tmp/y5-term`，五份同字节
  `sha 5826ea351d59b4a4`，基准 `d4b63457…` 在位）。
- **决定**（用户三条口径逐条落地）：D1=**hub 不变**、**消费者自己判断** —— 读之前调
  `remote/weights_standard.ensure_standard_weights`（就地落地；幂等，整份只读 4 字节；
  **转一次一直复用**）；D2=**两版都保留、后缀区分**：整份 `.json`、delta `.delta`，同目录同主名
  成对，内容才是权威（`BWD1` 魔数）；D3=**基准只认精确命中**（内容寻址缓存 / 同目录兄弟 / traj 树 /
  归档 / 链式递归；找不到**响亮拒** `ProtocolError` + 修法，绝不猜基准）；D4=**已经持有就不再传**：
  生产者把整份与 delta 都写进 `blob_cache`（后者新增），云机每轮 init 用自己产物链的落地件
  （`standard_weights_copy`：整份进旁路缓存，产物本体保持 delta ⇒ deliver 腿与 hub 侧判据不变）。
- **备选与否决**：① hub 落地（收到容器就转换/改名）—— 否（用户口径 hub 无责；且会动到 hub 的
  指纹/冲突判据面）；② 就地修复现场（把指针/归档/镜像都换成整份）—— 否（那些字节同时是 hub 侧
  duplicate/conflict 与账本指纹的输入，就地改会让「续跑锚点被忽略」「重投判冲突」悄悄失效）；
  ③ 本地做 identity 档（没有精确基准就按本机链凑）—— 否（本机没有 hub 那层身份闸，落错基准 =
  形状对、数值错的静默降质）；④ 让 `.delta` 也进归档/面板扫描面 —— 否（面板按 `*.json` 扫，
  delta 不该被当成可装载权重列出来）。
- **后果**：新增 `remote/weights_standard.py`（+ `tests/remote/test_weights_standard.py` 9 格、
  `tests/remote/test_init_no_retransfer.py` 4 格「已持有 ⇒ 零传输」含反证）；接线：trainer 每轮入口
  （`loop_round_steps.step_prepare_iter`，早于 wver）+ 各推权重点（`dispatch`/`eval_dispatch`/
  `queue_local`/`batch_runner`/`eval_course_once`）+ `eval_a_once`（TS 交接）+ 发布/导出
  （`loop_remote_job`/`loop_export`）+ 云机（`plan_run._run_iteration`）+ 任务面
  （`download._resolve_weights` 五源返回前）+ `train_core`（发出去的 delta 也入本地缓存）；
  `hub/store_offline` 的归档根实现与 `remote/artifacts` 合并（reuse 账本曾报重复组 +1）。
  全文 → `docs/nn/remote-transport.md` §98。**未结**（下一刀）：hub 侧落位名字仍按原样（其
  `weights.json` 里是 delta 字节）· 云机 **opt 链**同口径落地 · 全新机器接续线段的锚点形态。

## §2026-10-15-goalnn-offline-opt-delta-readside（2026-10-15，离线 delta 的 opt 那一半：读侧落地 + `opt.tar`/`opt.delta` 成对）

- **背景**（§2026-10-15-goalnn-offline-delta-readside 的续刀，同一场事故）：同一条离线段
  （`kind=run`）的**动量**也是 delta 回传的（R3/M4，`BOD1`，`remote/opt_delta`），而上一刀只把
  `weights_standard` 接进了消费者 —— **opt 那一半当时没修**。现场
  （`tmp/y5-term/remote-jobs/offline/c2ad414cd2ced1dd/it-237/opt.tar`）：头 4 字节 `BOD1`、
  300 945 B、`base_sha=15bea9c1…`；喂给消费者（`extract_tar_bytes` = `unpack_opt_tar` 的唯一实现）
  当场 `ReadError: invalid header`，而基准就在本机（`tmp/y5-term/it236/ppo_ckpt_remote.tar` +
  两个 job 目录的 `blob.opt` 逐字节命中）。hub 侧**有**落地（`land_opt_delta`）但那只是
  **published 腿**的回传受理；离线段没有 ⇒ 那台机器**自己下一轮**的 init 就是容器，整轮死在
  restore，且 `manifest.opt_sha` 还指向一个**本机缓存里没有**的地址（离线腿无下载通道）。
- **决定**（与权重那一刀**逐条同口径**，用户 2026-10-15 三条不另起新规）：D1=**读侧落地**
  （`remote/opt_standard`：`BOD1` → 整份 tar；幂等；**转一次一直复用**，缓存键 = 容器 sha16）；
  D2=**两版都留、后缀区分**：整份 `opt.tar`、容器 `opt.delta`，同主名成对，**内容才是权威**
  （魔数判形，`partial(delta_files.container_file, …)`）；D3=**基准只认逐字节精确命中**
  （`blob_cache/<base_sha>` O(1) 快路 → 同目录兄弟 → 兄弟轮目录 → traj 树（含
  `it*/ppo_ckpt_remote.tar`）→ 额外根；候选自己是容器时递归落地再比 sha），找不到 ⇒ `ProtocolError`
  + 两条修法（**绝不猜基准**：拿错动量 = 形状对、数值错的静默降质）；D4=**声明什么就必须拿到什么**
  （`landed_opt_sha` = 落地 → **种子进内容寻址缓存** → 返回整份 sha；`train_core` 只认
  `blob_cache/<sha>` 而离线段没有下载通道 ⇒ 不种子 = 声明一个拿不到的地址；种子过的那份又是下一轮
  落地基准的 O(1) 快路）。
- **备选与否决**：① 让 hub 在离线段也落地（收到容器就重建）—— 否（用户口径 hub 无责；hub 侧还要
  有基准与 torch/numpy，而级联的基准在链根，跨机常常不在）；② 把容器的基准改成「段起点整份」
  （任何一轮都能一步落地）—— 否（delta 会随漂移变大，正是 R3 要省的字节）；③ 消费者解不开时
  **静默退回 warm-start**（无动量）—— 否（D5：动量归零是精度回归，必须响亮）；④ 每个消费者各写
  一份重建 —— 否（复用账本会红：抽成 `opt_delta.rebuilt_opt_tar`，hub 与读侧**共用同一份**）。
- **后果**：新增 `remote/opt_standard.py` + `remote/delta_files.py`（**L0** 共用零件：魔数判形 /
  兄弟名 / traj 认根 / 候选扫描；不抽的话 `weights_standard` 与 `opt_standard` 会各留一份逐字节
  相同的实现 ⇒ 复用账本 14 → 18 组）+ `opt_delta.rebuilt_opt_tar`（**重建的唯一实现**：hub
  `land_opt_delta` 与读侧共用，差别只在 base 从哪来）；接线：`plan_run._run_iteration`（自己下一轮
  的 init）· `plan_handoff._seed_opt_blob_cache` / `_stored_opt_sha`（起点播种与声明）·
  `train_core` restore 段（消费者界面：容器先落地再装载，找不到基准**响亮拒**）；用例
  `tests/remote/test_opt_standard.py`（7 格，含**修复前的复现**：容器解不开 / 落地件解得出）+
  护栏同步（层号账本 `remote.delta_files` L0 / `remote.opt_standard` L1、`test_plan_handoff_split`
  白名单、`test_train_core_split` 依赖闭集）。现场验证（拷贝，不动原文件）：300 945 B → 604 160 B，
  `torch.load` 成功（42 组状态、`step=22752.0`、`lr=3e-4`），产物本体逐字节不动。全文 →
  `docs/nn/remote-transport.md` §99。**未结**（下一刀）：hub 侧落位名字仍按原样（要按后缀区分得让
  hub 读 manifest 的 `w_fmt`/`opt_fmt`）· **全新机器接续离线段**的锚点形态（`resume_anchor` 给的是
  hub 存的那份 delta ⇒ 换机首段需要先有整份；现在是响亮拒 + WARN）· 发布腿从「离线跑过的段」续跑
  时 `opt_init` 的动量来源（hub 侧没有 ckpt 目录 ⇒ 更倾向「无动量」而不是报错）。

## §2026-10-15-goalnn-hub-lands-delta（2026-10-15，回传 delta 的 **hub 侧就地落地**：口径反转 + `stage` 注入）

- **背景**：§2026-10-15-goalnn-offline-delta-readside 与 §…-opt-delta-readside 把「hub 无责、
  消费者自己判断」修在读侧（用户当时三条口径），并把「hub 收到就落地/改名」记成**被否决的备选①**。
  用户 2026-10-15 下午**改判**：「**只有 hub 和回传进程知道有 delta 这玩意儿**」—— delta 只活在
  「回传进程 + hub 落地器」之间，hub 收到就地在 hub 进程里重建为整份，此后 hub 树（回传树 / 交付
  镜像 / 活动权重指针 / 归档 / 续跑锚点）里一律整份，派发侧永远只发整份。读侧那两刀**不撤**
  （老树、人工导入的包、云机自己的产物链仍碰得到容器），本刀让**新回传不再产生容器**。
- **决定**：D1=落地器住 `hub/deltas.py`（**L2**）：`land_round`（按目录落地，失败**保留原件** +
  响亮）· `land_into_stage`（受理路径：先在暂存目录重建，再走首写锁定/409 那套），两个落地器
  （`remote.weights_standard`/`opt_standard`，L1）**函数体内惰性 import**（hub-server 的启动闭包
  不得拉 numpy/torch）；D2=**注入而不是 import** —— `hub/store_offline`（L1 叶子）收
  `stage=None` 参数，由接收边界 `hub/queue_resume`（L3）注入 `partial(land_into_stage, …)`
  （store 自己 import `hub.deltas` 会把整个 hub 栈顶高一层：层号 = 拓扑秩，
  `tests/test_remote_dag.py`；而「先重建、再记账」本来就是受理边界的职责）；D3=**名字跟着内容走**
  （整份 `weights.json`/`opt.tar`、原件 `*.delta`，两版都留）；幂等/冲突判据一律比**整份**，账本
  新增 `landed_fp`（与 `weights_fp` = 回传原件分开）；D4=**锚点只发整份**（还是容器的轮次跳过 +
  响亮一行），应答新增 `files`（递出的实际文件名），`weights_fp` 改成**递出文件的 sha**
  （原读 `row.json` 的自报指纹 = 容器的 sha，落地后就过期 ⇒ 客户端的「传输损坏」闸会弃用好锚点）；
  D5=**盘上的旧容器补落**：新增 `tools/hub_delta_sweep.py`（缺省 dry-run、`--apply` 才写、
  **只落地不删**）—— 「落不了才删那些轮」降级为兜底。
- **备选与否决**：① 让 `hub/store_offline` 自己 import `hub.deltas` —— 否（L1 叶子 import L2 ⇒
  整个 hub 栈涨一层：`store` L3、`queue_*` L4、`http_face` L6、`server` L8，账本与文档连带重排）；
  ② 把落地放到 store 的调用方路由（`hub/offline.py`，L3）—— 否（要在 store 之外重做一遍 base64
  解码，而 `stage` 参数只有一处调用点，注入更窄）；③ 落不了就地删掉那些 delta 轮 —— 否
  （删不可逆；只在**基准真的缺失**时才需要人工决定，工具响亮报出并保持原样）。
- **后果**：新增 `hub/deltas.py`（L2，账本登记）· `tests/hub/test_offline_backfeed.py` 新增一格
  （真 HTTP 走完三态：落地成功 / 缺基准保留原件 / 容器轮不当锚点）· `tests/remote/test_delta_files.py`
  （新：魔数对账 + L0 只 import stdlib + 名字与候选根）· `tools/hub_delta_sweep.py`；
  `remote/delta_files.any_container_file` 把四处重复的「任一族判据」收成一份；
  `hub/backfill_offline`/`queue_resume` 共用 `_round_is_complete`（两态名字）。现场
  （`tmp/y5-term`）已用工具补落六处（四份整份逐字节相同 sha `5741f71b65dc2207…`，`.delta` 兄弟
  留作回溯面）。全文 → `docs/nn/remote-transport.md` §100。**未结**：① 在跑的 hub/trainer 需重启
  才吃到本刀（现场两进程起于代码改动前）；② 归档里 `y5-hit2.it1.<ts>.json` 那一份基准已不在本机
  ⇒ 工具响亮报出、保持原样，删否待人裁。

## §2026-10-15-goalnn-delta-review-hardening（2026-10-15，暂存区复核后的六处加固）

- **背景**：§2026-10-15-goalnn-offline-delta-readside / §…-opt-delta-readside / §…-hub-lands-delta
  三刀在提交前复核时抓到六处（明细与现场 → `docs/nn/remote-transport.md` §100「评审加固」表）。
  其中两处是**行为回归**：① `hub/store_offline` 的幂等判据挂着 `not weights.json.is_file()` ⇒ 旧数据
  （`.json` 里装容器——现场 `tmp/y5-term` 的形状）**同一份字节**重传被判 `conflict 409`，而客户端把
  409 当硬拒（`remote/offline_deliver._reject_round`）⇒ 丢包/超时重传（常态）会把一轮正确产物永久
  挡在门外；② 读侧落地**就地**改写 `job_dir/init_weights.json`，而 `init_weights_fp` 仍是容器的 sha
  ⇒ 同一个 job 目录**第二次**进来 W0 那道「逐字节相符」的闸必然判「坏字节 —— 作废改走 blob」
  （删掉一份可用整份、真走过网的还白付一次 ~380KB 下行），与 `_resolve_weights` 自己那句
  「fencing 对账不受影响」直接矛盾。
- **决定**：① 幂等判据只认**内容**（`have == w_full or have == wj`），命中时 `_heal_round` 把
  「形态没到位」的名字补上（落地失败只剩 `.delta`、旧数据容器占着 `.json`）——补完那轮能当锚点，
  原件照旧旁存 `.delta`（两版都留、字节一个不丢）；覆盖**不同**字节仍是 conflict（不动）；
  ② 新增 `remote/download._is_landed_twin`（旁存 `.delta` 的 sha == 声明的 fp ⇒ 那是我们自己落的），
  W0 命中即用；③ `remote/opt_standard` 的防环真的传下去（`depth`/`seen` 原先只出现在形参里，
  `_MAX_CHAIN` 不可达 ⇒ 成环抛 `RecursionError`，调用方的 `except ProtocolError` 拦不住）；
  ④ 两个落地器的搜索面各加一格 `.weights-standard/*.json` / `.opt-standard/*.tar`（云机侧旁路缓存
  里的落地件**逐字节就是下一轮的 base**；链根会被 `biz/course_archive` 的「转存后删」清掉）；
  ⑤ `plan_run` 不再退回 `ctx.last_opt_sha`（那是回传出去那份 delta 自己的 sha = 拿不到的地址），
  找不到基准 ⇒ WARN + 无动量（与 `plan_handoff._stored_opt_sha` 同口径）；
  ⑥ `land_round` 的 `note` 累积 · `common/protocol.py` 那段与实现相反的注释（白名单**没有** `.delta`
  变体、`it` 仍是锚点轮）· `delta_files` 头注释的 import 面 · `_standard_bytes` 的死参。
- **备选与否决**：① 让 duplicate 分支干说一句幂等、自愈交给清扫工具 —— 否（`tools/hub_delta_sweep.py`
  是**人手动**跑的，而这条路径每轮都可能走到；补落动的只是同一内容的另一个名字，不碰不可变快照语义）；
  ② 落地件另起名字（`init_weights.standard.json`）以保住 W0 的内容地址语义 —— 否（rollout argv 与
  `build_ppo` 都按 `init_weights.json` 这个**名字**取件，改名 = 多处读点跟着改）；③ 给训练腿入口的
  落地加 try/except 降级 —— 否（拿错/猜基准 = 形状对、数值错的静默降质；见下面「有意的后果」）。
- **有意的后果**（写进 §100）：读侧落地接在**每轮入口**且**不吞异常** ⇒ 盘上那份容器找不到基准时
  `ProtocolError` 冒出、**整段训练腿当场停**。修法是让基准回到候选根（上一轮整份 / 归档 /
  `tools/hub_delta_sweep.py`），不是降级跑。唯一降级口径是未落地的 **opt**（动量）。
- **后果**：六处全部补了「先红后绿」的用例（`test_weights_standard` / `test_opt_standard` 各加
  「旁路缓存是候选根」+ opt「互为候选的容器不递归到死」· `test_init_no_retransfer` 加「同 job 目录
  重入」· `test_offline_backfeed` 加「落地失败过的那轮重传要自愈」「旧数据同字节重传 = duplicate」）；
  agent 侧门禁：受影响子集 292 → **298 passed**、`ruff check .` / `mypy .`（682 files）绿。

## §2026-10-15-goalnn-serve-revive-clears-runner-finished（2026-10-15，「收官后重开」不复位 runner 的收工态 ⇒ 假收官）

- **背景**（用户两次报障，第二次在改大 `max_hours` 之后）：`tmp/y5-term` 17:13 因 `max_hours` 真收官
  （账本有 `gate_verdict STOP`）→ 17:17:53 控制台「停止→开课」→ 17:18:25 serve 走「收官后重开」
  通道（`reopened_parked` → `_revive_hygiene` → `_enqueue`，日志原话「引擎热复用」）→ 17:18:40
  正常跑完 it239（本机 rollout + 远端 PPO + 归档）→ 17:20:28 `round_done` 指针 it240 → **17:21:00
  又收官一次**，且账本/日志里**没有任何判决**（无 `gate_verdict`、无熔断/止损日志），只有
  `loop_serve._settle_rounds` 的「已收官（2 轮，指针 it240）」。
- **根因**：`_revive_hygiene` 只清**引擎**的判决态（`_leg_abort`/`_remote_fail`/`_consec_fail`）与
  `runner.contexts`，**刻意不清** `runner.finished`（旧注释：「真硬边界/预算的语义……重开一门真收官的课
  不在本刀范围」）。而 `LoopRunner.planner` 的**第一行**就是 `if self.finished: return []`
  （`trainer/loop_runner.py:125-126`，**静默、无日志、无原因**）⇒ 队列被判 `QUEUE_DONE` ⇒
  `_settle_rounds` 再走一遍收官副作用（`run_complete` 横幅「正常收官」+ 云机 PAUSE + 本地停采 + 停车）。
  那一轮之所以还是跑成了，是因为重开的初始队列由 `plan_course` 直接填（不经 `runner.planner`）——
  队列一空，收工态立刻生效。**只有重启 trainer（新 runner）才能恢复**，这正是用户观察到的"重启就好了"。
- **决定**：① `_revive_hygiene` 清 `runner.finished=False` / `finish_reason=""`（重开 = 人的续跑意图）；
  ② `_settle_rounds` 的「已收官」日志追加「｜收工判据：<`finish_reason`>」，让收工原因**永不再静默**。
- **备选与否决**：① 只加日志不改行为（重启是可用绕法）—— 否（每次 停→开 白挨一轮，且与
  `loop_lifecycle.py:512-515` 亲自给用户的「改大 iters/配置后 停→开 继续」指引矛盾）；
  ② 把清点放进 `_enqueue` 而不是 `_revive_hygiene` —— 否（`_revive_hygiene` 就是「复活前清判决态」的
  唯一入口，已有引擎三件；放两处必然分叉）；③ 保留 `finished` 而改 `planner` 按盘重算 —— 否
  （`finished` 只是内存加速器；清掉后第一次问 planner 会按盘上预算/账本重推一次，等价且改动更小。
  真跑满的课被重开 ⇒ 白问一拍后立刻再置 True）。
- **后果**：`nn-training/tests/trainer/test_serve_aborted_revival.py` 两格（旧「有意不动」断言按新语义改写
  + 新增 `test_revive_hygiene_makes_the_reopened_course_actually_work_again`，钉「重开后 planner 真的
  还派 13 步」）；`tests/trainer/` 636 passed · ruff/mypy 绿。**未结**：`finish_reason` 仍是**内存态**
  （跨进程不可回放）⇒ 把它落到 `run_complete.cause` 并让控制台横幅说人话的事，仍归
  `plan/run-complete-cause-banner.plan.md`（本次新增的那行日志就是它要的仪器）。

## §2026-10-15-goalnn-run-complete-cause（2026-10-15，收官原因落账 + 横幅说人话：「改大 iters」只留给 iters 跑满）

- **背景**（用户两次报障 + 一次指令）：同日 `tmp/y5-term` 两次收官的屏上都只有模板文案「正常收官
  （itX/Y）」：① 17:13 真原因是 `max_hours: 12` 到顶 —— 它只写在**上一行** `gate_verdict` 里；
  ② 17:21 是假收官（热复用 runner 的陈旧 `finished`，原因只在内存）；两次伴随的固定指引都劝
  「改大 iters」，而①真正的闸是 `max_hours`（用户照做白挨一轮）。用户指令：「把课程收官的具体原因在
  dashboard 横幅里标明（说人话）」+「把 plan 实现掉」（`plan/run-complete-cause-banner.plan.md`）。
- **决定**：① **写侧**：`run_complete` 追加两个**可选**键 `cause`（ASCII 枚举，与 `gate_verdict` 同口径：
  `iters`/`max_hours`/`course_gate`/`breaker`/`stop_loss`/`kickstart_burn`/`paired_kill`/`leg_abort`/
  `unknown`）与 `detail`（人话短句、带数字）；**唯一写点** = 各停车判据自身（熔断/止损/干烧/配对杀臂/
  I2 停腿/预算硬断 + `_leg_abort` 的唯一读点兜底），**唯一读点** = `loop_lifecycle.finish_course`
  （读一次即清；空 ⇒ 跑满记 `iters`、无限课程记 `unknown`）；`reason` 只**尾部追加**详情，旧词头与
  旧尾串逐字不变。② **读侧**：`loop-complete.ts` 透传两键；老账本退一步看**恰好上一行**的
  `gate_verdict(STOP)` ⇒ 归 `max_hours` 并把术语原文**改写成说人话**；两行都没有 ⇒ 不带 cause、
  文案逐字回落。③ **文案单源** `course-status.ts::describeRunComplete()`（pill/矩阵与告警坞共用），
  **指引按原因分叉**：只有 `iters` 才说「改大 iters」；`max_hours` 说「改 max_hours，且预算按课程首启
  累计、重启不续命」。
- **备选与否决**：① 只在 trainer 侧改 `reason` 文案（不落结构化键）—— 否（存量已收官的轮次不自愈，
  且读侧要解析中文自由文本才能分叉指引）；② 只在读侧反推（不写键）—— 否（账本本体继续误导 `grep`
  的人，且原因要靠解析中文）；③ 读侧往上**找**最近的判决行（不限上一行）—— 否（把「只看尾行」变成
  「扫历史」，成本与误报面都涨；上一行不是判决 ⇒ 回落不带原因）；④ `cause` 写中文 —— 否（它要跨读取器
  对账，人话放 `detail`）；⑤ 把「预算到顶就自动续期」也做了 —— 否（那是**预算语义**问题，`first_run_start_ts`
  跨重启累计是刻意的防「重启续命」，不在本刀）。
- **后果**：nn 侧 `tests/trainer` + `tests/worker` + 守门 **1922 pass**、ruff/mypy 绿；dashboard 侧
  `typecheck` + `bun test` **1593 pass** + 三份 bundle 重建、根 `bun run check` **2561 pass**；
  用例：`test_loop_park.py`（三格 cause 组装 + 可选键 schema）· `server-api-loop-complete.test.ts` 四格 ·
  `training-pills.test.ts` / `web-course-status.test.ts`（`max_hours` 不许再劝「改大 iters」）。
  全文 → `docs/nn/console.md` §38。**未结**：老账本只能靠「上一行」兜底且只覆盖 `max_hours` 一种；
  段末（半离线 J2）那条轴仍不在 `run_complete` 里。

## §2026-10-15-goalnn-y5follow3-design（2026-10-15，三腿接续：encl/early/flank 各补最缺）

- **背景**（用户令）：term300/crown175/pace160 死活对照排名＋分腿发现（E1 开局税 crown 12x
  vs pace 死局 0 伤；W7 蹲死 pace 3.1x vs crown 反向；encl 三腿 1.6–1.8x 稳定）。
- **决定**：D1=三新课（全键 diff 绿；共享新种子 20261201，三路可比；起点核对沿标准口径）：
  `y5-encl`（term300 起：wEncl 8.6→17.2 一项，y5-far 剂量先例；门：encl＋命耗≤6%＋反watch
  timeout≤5%，cap 150）；`y5-early`（crown175 起：＋wEarlyDmg 0.06×dmgFirst600 一项，
  h5a 剂量/放置先例；门：四线＋命耗≤2%＋E1 漂移条款，cap 120）；
  `y5-flank`（pace160 起：＋wBackHits 0.4×backHits 一项，v9#45 列存在，侧击不奖；
  首剂 ~8% killEV；门：四线＋命耗≤2.5%＋B3 readout，cap 120）。
- **备选与否决**：① 三腿同加 encl —— 否（encl 只给最缺的 term；crown/pace 各有特异药）；
  ② pace 加 corner —— 否（重税刚性风险＋crown 反向，蹲死亚型先聚类）；③ postHit 定价 ——
  否（人类分离度未回）；④ 拾取加码 —— 否（时序配对先行）；⑤ toll 提前开 ——
  否（开课门 pace gameover≥10% 字面未触发 4%，排队）。
- **口径订正**（§6.4）：§y5ab-reseed consequences 写的"开课核对 manifest.it == 201"错了——
  课程轮从 1 起（crown/pace 实开即如此，bc 轮号无关）；本批三文件头注已用标准口径。
  旧三腿处置建议：term 使命完成可 halt（195→260 大振荡＋站位全残）；crown/pace 继续跑。
- **后果**：三文件已落盘；§15.5 新 out/traj/backup＋opt fresh；开课三连点人来
  （先核对 tmp/y5-{encl,early,flank} 不存在）。

## §2026-10-15-goalnn-y5bdh-design（2026-10-15，flank.it80 三接续：back/dist/hide 补两病根）

- **背景**（用户令）：病根二（贴脸 30%/包围 10%＋侧 0.68/背 0.21，跨场同病）；起点 flank.it80
  （win 0.9375/near 111 差 1/kills 4.85/B3 0.1937/A3 3.517/killEV 14.55；三腿共享新种子 20270101）。
- **决定**：D1=三新课（全键 diff 绿；iters 500/max 24 用户令）：
  `y5-back`（wBackHits 0.4→1.0［16%］＋wSideHits 0.2［11%，per-hit 5:1］＋wHit 0.6→0.2
  ［51%→17% 保底］；wAcc 81% 最大头用户没点不动；门 B3＋命耗≤3%）；
  `y5-dist`（wDist 0.6［15%，x20 形状/f 先例］＋wNearSq 42→48［37%，死亡谷边缘＋wobble 线］
  ＋wEncl 8.6→12.9［×1.5 退半步，×2 在 encl 腿垂死］；门 A3 anti-lottery＋wobble＋命耗≤3%）；
  `y5-hide`（公式不动；wThreat 120→150［29%］＋wHurt 110→150［8%］；门 threat/hurt readout
  ＋命耗≤3%＋自杀 watch timeout≤4%）。
- **备选与否决**：① wDist 用 enemyDist 采样列 —— 否（分布无数据，盲定价；用 x20 aimHitDistSum 形状）；
  ② near/encl 跟 flank 线（42/8.6）不动 —— 否（用户令调高；near 进死亡谷边缘已配 wobble 线，
  encl 用×1.5 不跟×2）；③ threat/hurt 顶满（180/200）—— 否（刚性税首剂不顶满，看 +40 动不动）；
  ④ wAcc 动 —— 否（用户没点；81% 最大头另案）。
- **前科披露**（头注内）：y5-hit 删 wDist 彩票史（1.8＋远射奖没反应；本次生存语境≠命中语境，
  anti-lottery 门配齐）；B5 farHits 双零奖空气风险（A3 动为生效判据）；encl×2 垂死（dist 退半步）；
  near×2 死刑（dist 配 wobble 线）。"yt-hide"系用户笔误，正名 y5-hide。
- **后果**：三文件已落盘；§15.5 新 out/traj/backup＋opt fresh；开课三连点人来。
  欠账：early-kill＋B3 无效＋encl-kill 三条待补（与本条同批落盘时一并记——本次一并记入：
  early 按门双 fail⇒杀已 halt；B3 首剂 0.4 无效（0.192→0.186）；encl it105 0.690 确认死亡，
  ×2 too hot，§6.4 收回"续至 cap"建议）。

## §2026-10-15-goalnn-y5bdh-nofuse（2026-10-15，新三课熔断全拆）

- **背景**（用户令）：y5-back/dist/hide 不设任何熔断。核查：三文件本就没有
  kickstart_burn/paired_kill/gates 块；唯一自动件是继承来的 `ent_break 0.25`
 （F4 ENT 熔断阈值；0.25 照抄亲爹 flank，亲爹照抄 pace……逐代继承无人过问）。
- **决定**：三文件 `ent_break 0.25→0.0`（熵 ≤0 永不成立）＋补
  `ent_break_max_winrate 0.0`（winRate ≥0 恒成立→守卫短路，F4 ENT 永不触发）双保险；
  头注同步。门（fail⇒杀）是人判标准，保留——熔断指自动执行，不指门。
- **备选与否决**：① 只改其中一键 —— 否（单键各有极端反例，双键正交兜底，多两行少一桩事故）；
  ② ent_break_consec 调大代替 —— 否（动预算不如动阈值，语义脏）。
- **后果**：三文件已改（JSON 解析绿）；kl-break/stop-loss 不在课程文件作用域
  （控制台/全局默认，真要全关得动控制台，本次不动）。未来新腿：ent_break 不许再无脑继承，
  要么显式 0.0/0.0（无熔断），要么写明阈值理由。

## §2026-10-15-goalnn-y5close-batch（2026-10-15，y5 七腿结课报告＋toll 删除）

- **背景**（用户令）：y5 七腿全停（near/term/crown/pace 批量 halt；encl/early 门杀；flank cap 收官）。
- **决定**：D1=七文件各追加尾部结课报告节（规格头不动；load 复验绿）：
  near（交卷＋A/B/C 起点供给）/term（flat 税形象＋大振荡）/crown（四线全过＋c06 起点 it195）/
  pace（梯度＋峰 0.9475）/encl（×2 too hot 门杀）/early（开局税死门杀）/flank（稳定冠＋B3 无效）；
  D2=删 `nn-training/curricula/y5-toll.jsonc`（未开课、无 tmp、无权重；plain rm，非 git rm）。
- **备选与否决**：① 报告写头注 —— 否（规格冻结，尾部零风险，y45-close 口径）；② toll 留档 ——
  否（用户令删；设计思想由 pace+lives 对照需求保留在 bdh 条，文件本身未产生证据，删无可惜）；
  ③ 动权重/tmp —— 否（只动课程文件；权重永存，tmp 是证据）。
- **后果**：七文件 load 绿；toll 无残留；在跑只剩 y5-back/dist/hide（新三课，用户令无熔断）。

## §2026-10-16-goalnn-cloud-eval-lands-delta（云机 eval 腿的读侧落地 + `wver` 同源）

- **背景**（用户报障）：云机 `y5-back` it60 的 `[eval-cloud]` 400 局每局
  `JSON Parse error: Unexpected identifier "BWD1"`，`serve-pool` fallback=23 触发熔断 ⇒
  整轮 `served=0`；同一份权重走 rollout 腿完全正常（fallback=0）。
- **根因**：离线段逐轮回传走 R1 ⇒ 产物里 `it-NNN/weights.json` 装的就是 int16 delta 容器
  （现场日志自证：整份 385 943 B / store 本体 delta 145 737 B）。rollout 腿在
  `plan_run.py:174` 先 `standard_weights_copy` 再装载，**eval 腿没有这一步**
  （`plan_handoff._eval_job_builder.build` 原样交出 ⇒ `offline_eval.py:540` ⇒
  `tools/sim/export-eval-game.ts:1418` 的 `JSON.parse` 撞魔数）。性质 =
  `docs/nn/remote-transport.md` §98 的接线点名单漏了第三条（eval 腿）。
  次生：`wver`（字节 sha256）算在容器上 ⇒ 与本机/节点评的同一 W(it) 配不上对
  （现场同一轮两个指纹：rollout `22bb69c74abb…` vs eval `e8aa57e46473…`）。
- **决定**：D1=装配点落地（`_land_weights` ∶ `standard_weights_copy` 到旁路缓存
  `.weights-standard/`，产物本体保持 delta ⇒ deliver 字节/hub 判据/账本指纹不动；scratch 拷整份）；
  D2=**落地早于算 `wver`**（`build` 交的就是落地件 ⇒ 与本机/节点同源，账本恢复可配对）；
  D3=缺基准（基准逐字节找不到）⇒ 接住 `ProtocolError`、记一行、交回空路径、本轮不评
  （评估是旁路：`run_cloud_eval` 按「权重不在盘上」早退，不抛、不拖训练）。
- **备选与否决**：① 改在 `run_cloud_eval`（消费点）落地 —— 否（走 scratch 时 path 在 /dev/shm，
  `delta_files.search_roots` 只扫 `path.parent`/`parent.parent` ⇒ 基准扫不到 store，还得额外传
  `extra_roots`，落地件也落回 /tmp）；② 两处都加 —— 否（幂等但同一件事两份实现、日志两行，
  违反最小修复；代价是 `run_cloud_eval` 的手动/测试直调入口没有兜底，已知取舍）；
  ③ 让 hub 落位时改名 —— 否（§98 定案「hub 无责」，消费者自己判断，未结项沿用）。
- **后果**：`plan_handoff` 的 import 闭集白名单 +1 `remote.weights_standard`（L1→L5 向下，
  `tests/test_remote_dag.py` 绿）；新增 2 格用例（`test_offline_eval_wiring.py`），
  三文件 39 用例 + ruff/mypy 绿；全文 `docs/nn/remote-transport.md` §101。
  两件待知会：① 改的是云机侧 python，在跑的段走 code cache ⇒ 换段/换机才生效；
  ② `wver` 变了 ⇒ 云机此前按容器 sha 落账的行会重评一次（那些读数本就废）。

## §2026-10-15-goalnn-y20open-design（2026-10-15，转场 y20 三腿：dist/stack/obs）

- **背景**（用户令）：立即转场 y20（c05 磨到头：dist it270 0.9525；中期目标 c20 90%）。
  关卡 `ladder-c20-lives1`（用户写 ladder-c20，按全场惯例取 lives1）；起点 y5-dist.it270
  （sha16 `65ec5b9224028b35`）；三腿共享新种子 20270201；iters 300/max 48（c20 局慢，
  用户未指定，先定后议）。
- **决定**：D1=三新课（全键 diff 绿）：
  `y20-dist`（y5-dist 配方逐字复刻：wDist 0.6/near 48/encl 12.9/梯度 wWin/timeout −5）；
  `y20-stack`（dist 底＋back 三键［wBackHits 1.0/wSideHits 0.2/wHit 0.2］＋hurt 150；
  wThreat 120 HOLD 回退 hide 的 150——D03 人 87 vs 机 51，threat 总量税误伤人类"进"；
  wEncl 12.9/wDist 0.6/wNearSq 48 HOLD——A06/A05-A07 人类证据＋W03 既定注）；
  `y20-obs`（x27-obs 配方零差复刻；优化器换 y 系退火；kickstart 关［同架构直连，
  x27 的 kickstart 是跨架构专需］）。
- **备选与否决**：① stack 照抄 hide 150 threat —— 否（D03 人类证据，回退 120；罚"待"足量）；
  ② stack encl 回退 8.6（W03 人 122.9>机 95.1）—— 否（W03 既定注＋dist 支撑有效，维持）；
  ③ wDist 加码（A05/A07 人类偏远）—— 否（A3 不动之谜未解，维持 0.6＋readout）；
  ④ y20-obs 沿 x27 单段优化器 —— 否（起点已收敛，退火对；奖励复刻不动优化器）。
- **前科披露**：x27-obs 已结课冻结（C 类四次阴性＋G0 miss，§85）——本次 c20 新场＋dist 起点
  重测（非同语境复跑），门配杀线；同样总账零 ⇒ 判死确认不再重测。y20 门全暂定线
  （锚 dist-190 c20 0.616/15.87；it0 基线偏离超 10% 按比例换算）。
- **后果**：三文件已落盘；§15.5 新 out/traj/backup＋opt fresh；开课三连点人来。

## §2026-10-16-goalnn-y5bdh-close（2026-10-16，y5-back/dist/hide 三腿结课）

- **背景**（用户令）：y5 三腿（back/dist/hide）全停。
- **决定**：三文件各追加尾部结课报告节（规格头不动；load 复验绿）：
  back（变量无效确认：B3 0.1937→0.1874 反降，前判"倾向杀"升级；终点 0.93/四线全靠站位＋命中）/
  dist（迟到但到：A3 3.517→3.674＋win 峰 0.9525 ⇒ anti-lottery"有效"命中，收回 wDist 无效前判；
  near/encl 双大胜；纯本机 400 局 settled）/
  hide（全场峰 0.9597/422 局补局 settled：threat −18%/hurtW −16% 维持；gameover 3.55%，
  命耗线 3% 差 0.55pp 诚实注记；A3 一字不动预期内）。
- **备选与否决**：① back 按"倾向"再续 —— 否（B3 反降＋全程未超基线，确认无效）；
  ② dist kills 行缺失重跑 —— 否（win/clears/终点权重齐，kills 缺一行不影响 verdict）；
  ③ hide 命耗线 3.55% 判 fail —— 否（差 0.55pp＋峰值＋税维持，收官不认 fail，注记）。
- **后果**：y5 全系列结课（近/远/冠/速/包/早/翼＋back/dist/hide 共 10 腿，均有尾部报告）；
  在跑只剩 y20 三腿。
## §2026-10-16-goalnn-offline-segment-roll（离线「无限轮」的下一半：J2 段末自动滚（含回灌腿）+ J4 静态拒因前置）

- **来源**：`plan/offline-open-ended-iters-and-loud-export.plan.md` v2 §3.2/§3.4（B/D 腿）·
  评审 `plan/offline-open-ended-iters-and-loud-export.review-bf.md`（P0-1/P0-2/P0-3 + P1-2/P1-3/P1-4/P1-8）。
  §97 落了 J1（`iters<=0` ⇒ 开区间段）与 J3（导出成败权威落点）；本条补的是**下一半** ——
  段末之后「不用人」以及「点下去之前就说清楚为什么导不了」。
- **定案一（J2 三态）**：段末判据从两态 `end_it_reached` 扩成三态 `seg_end_kind`：
  `open`（无限课的段末）/ `course`（课末，封课）/ `segment`（有限课程被 `--run-iters` 截短的一段）。
  判据由**云机从计划只读取出**（`remote/plan_run._seg_end_kind`），hub 只消费
  （`hub/task_pack.pack_seg_end_kind` 从包里 `plan.json` **反推**当兜底），控制台按它决定
  要不要转交 `run_complete`。v1 的「`end_it_reached` ∧ 非开区间 ⇒ 课末」会把第三格误判成课末
  （课当场被封死），那是这条判据存在的全部理由。
- **定案二（盖章不动 + 事件驱动）**：`completed_pack_sha` 是「这份包别再发了」的**唯一**机制
  ⇒ 段末**照旧盖章**（`_end_seal_ok` 的 run_id/plan_sha 对账一字未动），改的是**触发时机**：
  段末事件在 `hub/offline._post_offline_result` 里主动请控制台出下一段（与 claim 无关 ⇒ 解掉
  「completed 挡死 claim、而重导要有需求才有包」的死循环）。已知导出失败（J3 的
  `export-status.json` 窗口锚）在窗口内**不重复触发**（否则这条新腿会绕过「失败就不烧快照」的纪律）；
  三态**未知**也不触发（宁可少滚一次，不在课末白烧一次导出）。
- **定案三（回灌腿，评审 P0-1 选型①）**：「上报 → 回灌 → 出包」三步先后固定，
  `autoOfflineHandoff` **先回灌、再判包**：`python -m remote.deliver_zip --from-run <hub 已收目录>`
  把段末产物并进本机账本/指标（幂等：轮次只补缺、账本行按 `it` 去重、已到段末 ⇒ `imported:false`）。
  **不由 hub 构造下一段计划**（否决：要新造授权/合法性口径，且新节点会拿到与 `start_it` 不匹配的权重
  ⇒ 静默错血缘），也**不接受「保留全手动」**（那 §0 的目标就得照实改成「半自动」）。
- **定案四（T6 收紧）**：控制台转交 `run_complete` 的判据收紧为「跑满 ∧ run_id/末轮号对得上 ∧
  `seg_end_kind` 不是 `open`/`segment`」；`""`（旧 hub/旧云机）按旧行为转交。段末另立一个读数
  （导入回执 `segmentComplete` + 「本段完成（开区间段）——下一段由段末事件自动滚」）。
- **定案五（J4 静态拒因前置 + `--run-max-iters`）**：`exportGuard` 重建 ★M4 删掉的
  `declaredCourseIters`（住 `stack/courses.ts`：课程域共享判据的家、不断环；D14 口径 = 只读
  `curricula/<课>.jsonc`），并读账本末轮：`剩余 = iters - last_completed`（与 python 同一锚：
  启动期 `self._start_it` = `last_completed+1`、轮内 `it-1` = `last_completed`）⇒
  `<=0`「没有剩余轮次」· `>500`「> 硬上界」**在点击那一刻**拒（不 spawn、不烧 code-snapshot、不 push）；
  `iters<=0` 与不可知**不判**。另补 `--run-max-iters`（`common/manifest.py` 的注释一直说它存在，
  而全仓没有这个 argparse）：**只能降不能抬**，控制台同名常量读 python 源对账。
- **备选与否决**：① 云机自延长段（跑到 `end_it` 自己 `+= 500`）—— 否（等于「计划不设终点」，
  且 `end_it_reached` 永不触发 ⇒ hub 永远不盖章 ⇒ 另一个节点能把同一份包再跑一遍）；
  ② 「不盖章」换「同一 sha 不再服务」—— 否（要多造一条持久判据，而盖章本来就是那条判据）；
  ③ 抬 `RUN_MAX_ITERS_HARD_CAP` 到 2000+ —— 否（拆掉「防手写/损坏计划把节点按一整天」的闸）；
  ④ 段末种类**未知**时按段末处理 —— 否（课末会白烧一次导出 = 一次 code-snapshot + 一次 git push）。
- **后果**：hub 侧 6 格新用例 + 云机三态判定表 9 格 + 回灌腿 4 格（`tests/{hub,remote}`）+
  控制台 J2/T6/回灌/静态预判/常量对账 8 格（`dashboard/tests/*`）全绿；
  `docs/nn/remote-transport.md` §102 给出**契约表**（谁定终点 / 谁推进账本 / 谁触发出包 / 失败怎么停）。
  **真机未验**：DoD-2/3/6 要一段真云机跑满才成立，闭合判据留在 `docs/nn.progress.md` §3.1 条目 32
  —— 在此之前「无限轮」的代码面闭环，端到端按「未验」对待。

## §2026-10-16-goalnn-component-error-registry（组件错误注册中心：五处散落的错误 → 一张登记表 → 首页入口 + `/errors` 详情页）

- **来源**：`plan/component-error-registry.plan.md` v3（用户 2026-10-15 诉求：「让远端 ppo 云机、自主 rollout/eval/ppo
  云机，本地进程 trainer/hub/cloudflared 的错误日志能够在 dashboard 注册并查看…避免训练各个组件静默失败无人处理」）·
  评审 `plan/component-error-registry.review-bf.md`（3 P0 + 4 P1 + 5 P2，处置见 plan §12）。**D 批次**（`dashboard/**`）
  与 **P 批次**（`nn-training/**`：E3 改走既有 410 通道补 `detail` + E4/E5 上报腿）**均已落地** —— P 批次按 §7-Rk1
  单开窗口：改 `nn-training/**` 会翻训练 code zip，云机 worker 下次校验 `CodeChangedError` ⇒ 退出码 86 自愈重拉一次。
- **定案一（五处 → 一张表）**：`ErrorEvent` 是唯一形状（类型住 `web/view/error-types.ts` —— 零 import；计算住
  `server/api/error-{fingerprint,scan,registry}.ts`）。四条腿**各带闸**：腿 A 本机组件日志尾 `size:mtime` 签名、
  腿 B 已见 jid 记忆化（`fail.json` 首写胜出）、腿 C 云机上报只在自己的 **10s 独立节拍**上探 hub、腿 D 读既有池视图
  SWR ⇒ **`/api/state` 请求路径零新增探测零网络**（N9 / R1 结构闸）。技术正文：`docs/nn/console.md` §39。
- **定案二（`fp` 不含 `scope`）**：指纹 = `shortHash(source | component | normalize(message))`（归一去掉 hex / 数字
  序列 / 时刻 / `tmp/` 之前的绝对路径前缀）。把课名算进指纹会让同一条错误在 10 门课上折成 10 条 ⇒ 首页计数虚高、
  详情页十行同一条，正是要治的刷屏在详情页上的复刻。
- **定案三（两个时刻分开 · 两个 ack kind）**：账本 `firstSeenAt`（**不可变**，只做「这个 fp 我见过」的种子）vs
  内存 episode `firstAt`（**可重置**，ack 身份）；ack = `component-error`（逐条：`fp` × 内存 `firstAt`）与
  `component-errors`（首页汇总条：subject `'all'`，eventId = 最新未 ack 错误的时刻）。二者混用 ⇒ 一次「知道了」
  永久静音；分开 ⇒ 持续态不重复打断、**消失后复发必重弹**、新错误进来必重弹。
- **定案四（账本可重定向）**：`tmp/component-errors.jsonl` 住 `core/paths.ts::errorLedgerPath()`（`BCITY_ERROR_LEDGER`），
  与 `tmpLogsDir()` / `consoleLogPath()` / `loopControlPath()` 同规 —— 否则用例会把指纹写进**操作员真账本**。
  只在「新 `fp` 首次出现」时 append 一行；重启重放只当种子、**不继承** `firstAt`（代价：未消失的错误重弹一次，显式接受）。
- **定案五（入口与详情页）**：首页 = 告警坞**第 9 类汇总条目**（0 或 1 条，逐条清单是详情页的事），为此把
  `AlertAction.kind` 闭集放宽出唯一一个**无副作用**的 `'link'`（渲染成真 `<a href>`、**不挂 `onClick`**）；详情页 =
  **第 3 个独立页 / 第 4 份 bundle**（`/errors`，照 `/log` 范式，只读面照渲）；`detail` **不进** 3s/4s 轮询投影，按 `fp` 懒取。
- **定案六（P 批次：云机的两条腿怎么回本机）**：① **E3 复用既有 410 通道**（`set_error(detail=…)` → 410 体
  `fail_detail` → `remote/push_dispatch.push_failure_record()` 改读，缺字段回退原常量）⇒ **无新端点 / 无新凭据 /
  无阻塞**；② **E4/E5 走 `POST /admin/worker-error`**（形状照抄 `worker-prefetch`：同一枚 token、缺 `worker`/`reason`
  ⇒ 400、64KB ⇒ 413；hub 侧只是 TTL 120s + 200 条的**内存环** —— 权威账本只在控制台）；③ 上报走 `remote/error_report.py`
  的 **daemon 线程 + 2s 超时**：`report_error` **立即返回**、失败即丢、不重试（G9）；④ 读面游标 = **hub 的到达时钟
  `at`**，不是上报方的事件钟 `ts`（跨机时钟不可比）。上报点只有三个 pull 失败分支 + 离线段失败；**取消 / 热替换 /
  确定性拒绝一律不报**（设计内生命周期，F15）。技术正文：`docs/nn/remote-transport.md` §103。
- **备选与否决**：① 复用 `/jobs/{id}/fail` 收瞬时错误 —— 否（那条腿「首写胜出 + 只收确定性失败」，会污染训练侧读到
  的那条失败原因）；② 在 `/jobs/*` 加 `detail` 字段 —— 否（要给取活协议动租约/游标面，410 通道是纯加法）；
  ③ 让 `/errors` 进主 bundle —— 否（独立页与 `/log` 同规）；④ 固定 / 纯账本 ack 键 —— 否（只增不减的 `TC_ALERT_ACKS`
  会把第二次同因事件永久吃掉）；⑤ 指纹住 `web/view/**` —— 否（`node:crypto` 撞分层禁词闸与 bundle 禁词闸，评审 P0-1）；
  ⑥ 把取消 / 热替换也报成错误 —— 否（设计内生命周期，报成错误就是灌噪音，F15）。
- **后果**：控制台 `tests/server-api-error-registry.test.ts`（新）· `web-alert-dock.test.ts` 第 9 类 · `web-ssr-errors-page.test.ts`
  （新）· `web-view-routes.test.ts` 的 link href 白名单改**数据驱动** · `web-ssr-readonly.test.ts` 夹具补 `errors: null`；
  云机 `nn-training/tests/{hub/test_admin_worker_error,remote/test_error_report,remote/test_worker_server_error_detail}.py`
  （25 格：含 F15 守门与「1s 慢 hub 仍立即返回」）+ `remote/error_report.py`（新，L0，已入 `tests/helpers/remote_dag.py` 层账）。
  **未验**：真机端到端（要一段真云机跑满才成立，闭合判据在 `docs/nn.progress.md`）。
  与 `tmpLogsDir()` / `consoleLogPath()` / `loopControlPath()` 同规（P0-3）—— 否则用例会把指纹写进**操作员真账本**。
  账本只在「新 `fp` 首次出现」时 append 一行；重启重放它当种子，**不继承** `firstAt`（代价：未消失的错误重弹一次，显式接受）。
- **定案五（入口与详情页）**：首页 = 告警坞**第 9 类汇总条目**（0 或 1 条，逐条清单是详情页的事），为此把 `AlertAction.kind`
  闭集放宽出唯一一个**无副作用**的 `'link'`（渲染成真 `<a href>`、**不挂 `onClick`**）；详情页 = **第 3 个独立页 /
  第 4 份 bundle**（`/errors`，照 `/log` 范式，只读面照渲）；`detail` **不进** 3s/4s 轮询投影，按 `fp` 懒取。
- **备选与否决**：① 复用 `/jobs/{id}/fail` 收瞬时错误 —— 否（那条腿有「首写胜出 + 只收确定性失败」语义，
  塞进去会污染训练侧读到的那条失败原因）；② 在 `/jobs/*` 加 `detail` 字段 —— 否（要给取活协议动租约/游标语义面，
  410 通道是纯加法）；③ 让 `/errors` 进主 bundle —— 否（独立页与 `/log` 同规）；④ 固定 / 纯账本 ack 键 —— 否
  （只增不减的 `TC_ALERT_ACKS` 会把第二次同因事件永久吃掉）；⑤ 指纹住 `web/view/**` —— 否（`node:crypto` 撞分层禁词闸
  与三份 bundle 禁词闸，评审 P0-1）；⑥ 把取消 / 热替换也报成错误 —— 否（设计内生命周期，报成错误就是灌噪音，F15）。
- **后果**：`dashboard/tests/server-api-error-registry.test.ts`（新：P0-1/P0-2/P0-3 三条守门 + G8 跨 episode 试金石）·
  `web-alert-dock.test.ts` 补第 9 类（含 `link` 渲染分支、汇总条 ack 分工、复现重弹）· `web-ssr-errors-page.test.ts`（新）·
  既有闸同批改：`web-view-routes.test.ts` 的 link href 白名单改为**数据驱动**（原先硬编码 `/eval|/log/` ⇒ 加第 3 个独立页必红）、
  `web-ssr-readonly.test.ts` 夹具补 `errors: null`（沿用 selfDisk F11 那条「夹具必须把无告警造成事实」的口径）。
  技术正文 `docs/nn/console.md` §39；**未落地**：P1/P2 两条云机腿 + 真机端到端（见 plan §5/§6 的批次划分）。

## §2026-10-16-goalnn-deliver-import-placement（产物导入落位：去掉目录改名，改逐轮 merge —— 解 Windows `WinError 5` 必败）

- **来源**：`plan/offline-backfeed-resilience.plan.md` §8.2/§9-12（§10 的 **A+B** 两片）· 现场
  `reports/y20-dist2-offline-backfeed-20261016.md` §3.8 · 评审 `plan/offline-backfeed-resilience.review-bf.md`
  F6/F11（根因措辞与顺序证据）。
- **定案一（去目录改名）**：`import_deliver_zip` 不再「解到 `<dest>/<run>.tmp-<pid>` 再整体 `rename`」——
  Windows 的 `MoveFile` 要求源目录**整树无句柄**，而仓库常驻进程/监视器会给 `tmp/**` 下新建的目录**按序**挂句柄
  （探针实测：前 31 个子目录改名成功、从 it-151 起必败；等 90s / 0.5s×60 退避 / `os.replace` 全无效）
  ⇒ 改为逐件写盘，**全程不出现目录改名/替换**。中转目录仍只用于**解压**，`finally` 里必删。
- **定案二（「不留半截」的接手方式）**：单轮内**权重最后拷**——半截轮对 `_scan_iters`（只认 `weights.*`）
  不可见，下一次导入会把它补齐。旧代码靠「原子改名」拿到的性质由这个顺序接手，**不引入新的完整性标记字段**。
- **定案三（读数源 = 包）**：`metrics.jsonl`/`eval_log.jsonl` 的并账从**包**里读（`_merge_carried_*` 的
  `src_root` + 显式 `run_id`），不是从导入后的目录读——逐轮 merge 后 `final/metrics.jsonl` 可能停在上一次
  导入的水位（只读它会让后到的包的曲线静默进不了课程账本）。
- **备选与否决**：① `os.replace` 代替 `os.rename` —— 否（对目录语义同样 `WinError 5`，实测）；
  ② 落位仍用改名、只在失败时 `copytree` 兜底 —— 否（两条路径两种语义，而失败在本机是 100%，不是边角）；
  ③ 「先把持句柄的常驻进程停掉再导入」 —— 否（未定位到是谁；代价大于收益）。
- **后果**：`remote/deliver_zip.py` 新增 `_same_bytes`/`_round_files`/`_land_rounds`/`_land_anchors`；
  `tests/remote/test_deliver_zip.py` 用**反探针**钉住（把对目录的 `rename`/`replace` 打成 `PermissionError`
  ——拦掉也必须照常导成功）。**未落地**：控制台用户可见的报错表（plan §6.2-4，G 片）。

## §2026-10-16-goalnn-deliver-first-write-wins-import（本地导入/回灌同规：同名轮先到者为准 + 缺件愈合 + `--replace` 逃生门）

- **来源**：同上（plan §8.2/§8.3 的 **D5**；评审 F6：`import_from_run` 当时是「存在即整轮跳过」的
  **第二条路径**，与导入腿的「先 rmtree 再落」语义相反）。
- **定案一（一份判据）**：导入腿与回灌腿共用一个 `_land_rounds`：缺件 ⇒ 拷（**愈合**）、同字节 ⇒ duplicate、
  异字节 ⇒ **保留盘上先到的那一份** + 响亮一行 + `skipped_rounds`。口径对齐 hub 的
  `store_offline_artifact`（duplicate / `_heal_round`）——hub 侧早就是内容判据，本地侧今天是相反的。
- **定案二（锚点先到者为准，`state.json` 例外）**：`state/plan/manifest/artifacts.zip/两账本` 缺件才拷；
  `state.json` 当「新包自述 `last_it` 更靠后 ∧ 本次零冲突」时采纳——否则它的 `last_it`/`last_weights_fp`
  会指向被跳过的轮（自述与盘上事实不符比陈旧更贵）。
- **定案三（返回体）**：`iters/last_it/ckpt` = **盘上真实**（合并后；`ckpt` 必须真实存在，控制台拿它跑 evalA），
  新增 `package_iters`/`package_last_it`/`kept_rounds`/`skipped_rounds`/`healed_files`（additive，老读方忽略）。
- **备选与否决**：① 默认覆盖（= 今天的语义）—— 否（同名轮是不可变快照，2026-10-11 的同一条口径：响比静默好）；
  ② 「异字节跳过」做成静默 —— 否（人裁决要有入口 ⇒ `--replace`）；③ 把 `delivered.json` 的 `artifacts` 元素
  类型改成 dict 以记 `confirmed_at` —— 本次未动，但**明令禁止**（老读方 `isinstance(it,int)` 会静默丢掉全部
  已投递记账 ⇒ 全量重投 + 抽屉永不删；要加就加**并列键**，见 plan §4.2-3）。
- **后果**：`--zip` 新增 `--replace`；`tests/remote/test_deliver_zip.py` 新增 9 条（DoD-7a/b/c + 缺件愈合 +
  读数 additive + CLI 逃生门），既有「重导同名 run」用例改写为新语义（逐轮 merge，不再整目录替换）；
  nn python 门禁全绿（ruff+mypy+pytest tests/ e2e/：4535 passed / 10 skipped）。
  **未落地**：控制台 message 里的「新增 X 轮、跳过 Y 轮」（plan §8.2-4，G 片）与 §4 的删除/自愈面。

## §2026-10-16-goalnn-offline-legs-revive（两条观测/补传腿都不许「一停到底」）

- **来源**：`plan/offline-backfeed-resilience.plan.md` §2.2-2 / §4.2-1/2（评审 F1/F2）· 现场报告
  `reports/y20-dist2-offline-backfeed-20261016.md`（「补传 27 轮 0 送达」「进度冻在 it125」）。
- **定案一（补传腿：401 停用要能复活）**：`_disable` 加 `DISABLE_COOLDOWN_SEC = 600` 冷却 + 到点
  `_maybe_revive()` 解封并**逼一次真探活**；探活**失败**的负缓存从 60s 收到 `PROBE_FAIL_TTL_SEC = 15`
  ——「隧道刚回来」这段最该补投的时间不该被一个平坦的 60s 负缓存盖掉。当拍仍不重试（D9 闭锁口径不变）。
- **定案二（打点腿：连不上 = 降频，不是停）**：连丢 `PROGRESS_FAIL_LIMIT` 拍改进降频窗
  `PROGRESS_COOLDOWN_SEC = (240, 480, 900)`（封顶），窗过自动再试，**下一个成功复位计数与结局**；
  段末结局仍是 `unreachable`（= 最后一次没成），只有 `revoked` 真停。
- **定案三（409 `expired`/`taken` ⇒ 重新领租约）**：`install_progress_pinger(..., reclaim=…)` 交给
  `offline_boot._reclaim_lease`（同机重领自己的课，hub 侧 `mine` 优先）；领不到才带着原因停。
- **备选与否决**：① 指数退避到年级 —— 否（F2：抖动窗口按秒算）；② 409 一律停（旧口径）—— 否（现场就是
  租约 TTL 到期/被抢了一次，而整段动线跟着哑）；③ 401 每轮重试 —— 否（bad token 重试无意义，还会被
  D9 封 IP）。
- **后果**：`tests/common/test_offline_task_queue.py` 的 pinger 用例改写（连丢 ⇒ 降频 + 窗过恢复 + reclaim），
  `tests/remote/test_offline_deliver.py` 加冷却复活用例。**未做（评审 F1 的第 0 步）**：拿云机
  `[deliver]` 行与 hub `/admin/offline` 读数把「27 轮 0 送达」定性成 C1/C3 还是 C4/C5 —— 在此之前
  本片是**按覆盖面**设计的修法，不是按定性结果。

## §2026-10-16-goalnn-drawer-carries-ledgers（抽屉带读数账本；账本「变了才拷」；容量读数进 status）

- **来源**：plan §3.2/§3.3（DoD-2a/2b/2c，评审 D8）· §14（抽屉在网络盘上的写放大与配额）。
- **定案一（抽屉 = 能被直接吃的产物根）**：锚点名单追加 `metrics.jsonl` / `eval_log.jsonl` ——现场
  「权重能救、读数救不回」（抽屉里只有 `it-NNN/weights.json` + `opt.tar`，导入后曲线与 eval 全空）。
- **定案二（变化才拷）**：只有这两个账本按 `(size, mtime_ns)` 变化才重拷（判据与 `_ledger_its()` 的缓存
  失效**逐字同源**）；其余四件仍每拍重写——`state.json` **必须**与轮次同步（`run_id`/`last_it` 是导入/
  恢复腿的判据）。
- **定案三（读数由写方量、读方零 IO）**：`drawer_bytes/ledger/round_avg/budget` 进 `status()`，
  `budget = 待投轮数 × 单轮均字节 + 两账本`（§14 的容量行）；`status()` 每拍被调而抽屉在网络盘上 ⇒
  绝不在读面 `rglob`。子进程状态面（白名单）同步扩。
- **备选与否决**：① 每拍全量重写两个账本 —— 否（MB/轮 的网络盘写放大）；② 只拷「首个待投轮之后的尾段」
  —— 否（要维护一个水位 = 新状态源，而追加型账本的 `(size, mtime_ns)` 已经够准）；③ 在
  `status()` 里现量抽屉 —— 否（IO 花在展示上）。
- **后果**：`tests/remote/test_offline_deliver.py` 三条 + e2e 一条（把抽屉当产物根跑 `import_from_run` ⇒
  曲线读数与 eval 行并入课程账本），`deliver_worker._write_status` 白名单扩 6 个键。

## §2026-10-16-goalnn-rejected-ttl-and-reset（内容拒收不再是永久判决：TTL + 上限 + 人工重置）

- **来源**：plan §15（**D6**）：`rejected` 跨会话按权重指纹认领 ⇒ 一次**误判**（hub 侧 409 被读成内容
  问题、账本行取错）会让那一轮**永久不再回传**，而权重一直好好地躺在本地盘上。
- **定案一（年龄面）**：每笔拒收落盘 `at`/`tries`；加载时 `tries ≥ REJECTED_MAX_TRIES(3)` 或
  `now - at > REJECTED_TTL_SEC(24h)` ⇒ 不认（那一轮回到待投集）。旧账本没有 `at` ⇒ **一律复活**：
  一条没有年龄的证据不足以支撑「永不再试」，而重试一轮的代价只是再推 ~1.9MB。
- **定案二（只有拒收的一拍也要落盘）**：`_push_backlog` 的 `skip` 现在也置脏 ⇒ 记录即时落盘。
  否则「本拍没有成功投递」会让记录只住内存，下一次落盘静默抹掉（连 TTL 都轮不到）。
- **定案三（人工出口）**：`reset_rejected(根, run_id=…, it=…)`（+ `python -m remote.offline_deliver
  --artifacts <根> [--it N]`）清掉记录行，并在文件里写**按 it** 的重置水位 `rejected_reset_at`；写方
  落盘时丢掉水位之下的记录 ⇒ 活着的补传子进程不会把刚清掉的行写回来（水位按 TTL 清，防无限积累）。
  目录复用时按 `run_id` 拒接（不许拿别的 run 的记录开刀）。
- **备选与否决**：① 全局水位 —— 否（只重置一环会把**其余**环次的拒收一并放掉）；② 重置只在进程退出后
  生效 —— 否（段末/长轮期间恰恰最需要）；③ 把 `--reset-rejected` 加在 `deliver_zip` —— 否
  （`delivered.json` 的路径/指纹/水位口径都住在 `offline_deliver`，另开一处 = 同一语义两份实现）。
- **后果**：9 条用例（TTL 过期复活 / TTL 内仍跳过 / 记满次数复活 / 旧账本无年龄 / 年龄面计数 / 只有拒收
  的一拍落盘 / 重置在活会话里不被写回 / 只重置指定轮 / CLI）。

## §2026-10-16-goalnn-import-checklist-and-name（导入：文件名 `@` 附注、落位计数上屏、包体检）

- **来源**：plan §6.2-1/3 + DoD-5a/5c（G 片）· §19（同一语义只留一份实现）。
- **定案一（文件名放宽）**：`deliver-<课>@<run_id>-it<N>.zip`（§5.2 的导出/一键包留历史名）的**课程段 = 
  `@` 之前**；python `course_from_filename` 与 ts `validateDeliverUpload` 两处同改（两边各有用例钉住
  「报错里指到的那门课就是课程段」）。
- **定案二（包体检是报告不是门）**：`deliver_zip` 返回 `checks: [{id, ok, detail, +结构化字段}]`
  （entries/shape/continuity/rows-fp/weights/state/state-fp/anchors/body-limit）+ `checks_ok` + `partial`。
  任何一项不过**都不阻断导入**（先落权重才是这半个功能的主价值）；成功路径上也上屏（DoD-5c）。
- **定案三（结构化读数与 `detail` 并存）**：`continuity.missing` / `rows-fp.{no_rows,bad_fp}` /
  `state.{state_last_it,package_last_it}` / `anchors.missing` —— 控制台与用例按**数据**判，不挂措辞
  （本仓点名过的反模式，见 `tests/helpers/text_asserts.py`）。
- **定案四（§6.2-2「自动识别课程」取「只报错 + 指引」）**：包内**没有**课程信号（`IMPORT_META.json` 属 F 片
  未落地；`state.json` 只有 run_id；`manifest.json` 只有 `course_fp`）⇒ 自动识课今天不可达，新增
  `courseFromPackage=1` 请求字段等于**猜**；现有报错已给两个课名 + 明确动作（切课或改名），`@` 放宽后
  不再误伤。待 F 片落地 `IMPORT_META.course` 后再按 §6.2-2 的优先级实现（并在 DECISIONS 另记一条）。
- **后果**：`import.ts` 的结论串带「新增 X 轮、跳过 Y 轮（已有不同字节，先到者为准）」「补齐缺件 N 件」
  「体检 N/N 全绿 / 未过 …」「**半段**」，`detail` 整屏展开体检 9 行；python 4 条 + ts 3 条用例。

## §2026-10-16-goalnn-backup-drawer-module（抽屉写方单独成家 + 层级账本重排）

- **来源**：`tests/test_python_loc_budget.py`（单文件代码行 < 1000，**只许收紧**）· 
  `tests/helpers/remote_dag.py`（层号 = 拓扑秩 = `1 + max(依赖)`）。
- **定案（按「独立所有者 + 独立触发条件」拆）**：抽屉的**拷贝策略**（拷哪些件 / 何时跳 / 失败算什么 /
  读数）搬进 `remote/backup_drawer.py`（`BackupDrawer` + `BACKUP_ANCHOR_NAMES` + `DrawerReadings`）；
  `offline_deliver` 只留「算哪些轮次待投」（它的判据）与「读数记进 status」。
- **层级重排（诚实的代价）**：`remote.backup_drawer` **L1**（靠 L0 的 `artifacts` + `offline_deliverable`）
  ⇒ `remote.offline_deliver` **L1 → L2**、`remote.deliver_proc`/`deliver_worker` **L2 → L3**（其余不变，
  层号集合仍稠密）。
- **备选与否决**：① 抬 LOC 上限 —— 否（用户口径：>1000 行 = 设计问题）；② 让抽屉只 import `common.*`
  （把产物名搬进 `common/` 当 L0）—— 否（同名常量两处 = 又一份会漂的真相）；③ 把抽屉整块挪进
  `offline_deliverable`（L0 + 豁免）—— 否（那是 standalone 运输单元，顶层只许 stdlib，混进来会砸引导契约）。
- **后果**：抽屉写方**惰性建**（`deliver_proc` 的父侧镜像是在构造**之后**才补上 `backup_root`，在
  `__init__` 里建好就是那个调用点上静默不备份）；`_drawer_for_write()` 取调用时的 `self.log` 同理。
  方法名 `write` → `backup_rounds`（避免与 `common.log`/`worker.train.loop_util` 的同名面凑成 ≥3 模块，
  `tests/test_reuse_ledger.py` 当场会红）。

## §2026-10-17-goalnn-backfeed-e-f-g-h-close（E/F/G/H 四片收口：判据化重试与删除 · 一键包 · 报错表 · 停课导入）

- **来源**：`plan/offline-backfeed-resilience.plan.md` §4（E）· §5（F）· §6.2（G）· §7（H）
  + `plan/offline-backfeed-resilience.review-bf.md`（F1/F2/F4/F9/F10/F11）。
- **E（§4 自愈重试 + 租约重领 + 判据化删除）**：不新增机制，把 tick 内已有的「试」收紧并记住 ——
  ① 探活负缓存从 60s 收到 **15s**（`PROBE_FAIL_TTL_SEC`，封顶仍 ≤60s，别把平坦重试换成退避）；
  ② `next_try_sec`/`last_error`/`retry_n` 进 `status()`（**给人看，不做退避判决**）；
  ③ `_disable`（401/403）加**冷却复活**（`DISABLE_COOLDOWN_SEC`，到点清 reason + 重探活）；
  ④ 409 `expired`/`taken` ⇒ 补传腿照落（hub 侧本无租约门）+ 打点腿**重新 claim**（`_reclaim_lease`）
  —— `revoked` 不动（那是人的意志）；⑤ 抽屉删除 = **回传成功（`POST 200 → _save_ledger()`）才删**，
  `delivered.json` 不可信 ⇒ 一拍不删；`BACKUP_KEEP_ROUNDS`/`BACKUP_KEEP_HOURS` 接口留而默认 0。
- **E 的孤儿口径（F4 落地选择）**：账本是 run 级、抽屉是课级 ⇒ 换 run_id 后老段轮次在新账本里**无证据**
  ⇒ 走**内容对账**（本地有同一份字节 = 可重建的冗余 ⇒ 可删），剩下真孤儿**保留并计数**
  （`backup_orphans`）——「不许默默不删」两条口径各一条用例（DoD-3e）。备选「孤儿一律不删」被否：
  抽屉只涨不落，而它能被内容对账证明可重建。
- **F（§5 一键包）**：新增 `package_pending(cfg, log, *, courses=None)`，判据表四行（本地根齐 → 全量+三件+
  两账本 / 只有抽屉齐 → 抽屉全量 / 已收官 → `artifacts.zip` 直接复制 / 两边都不齐 → 响亮报缺哪一件、返回空表、
  绝不抛）；包内加 `IMPORT_META.json`（`magic/课程/run/it_from/it_to/generated_at/source`），
  历史名 `deliver-<课>@<run>-it<N>.zip` —— 与既有 `package_deliverable` 共用判据函数（不留第二份带账本的口径）。
- **G（§6.2 报错表 / 自动识课 / 留证保留）**：`import.ts` 的失败收口加一张**现象 → 人话 + 出路**表
  （句柄占住 / 目标已存在 / 非 zip / 拿错包 / 形状 / 课名冲突 / 盘满 / 超时 / 兜底），`detail` 一律带
  「挂在的门 + 出路 + 末尾八行 + **日志绝对路径**」，铁律 = 结论句里不出现裸 `WinError`/`Traceback`；
  留证目录保最近 3 份（删时响亮一行）。**§6.2-2 自动识课的定案改判**：`IMPORT_META.json` 已落地 ⇒
  优先级 `IMPORT_META.course` > `state.run_id` > `manifest.course_fp` > 文件名，冲突**不猜**（python 侧
  拒收并给两个课名 + 动作），控制台**不静默切课**、只把「包内自述：课程/run/来源」上屏
  （上一轮取「只报错 + 指引」的那个前置条件已消失，扩展上一条 `§2026-10-16-goalnn-import-checklist-and-name`）。
- **H（§7 停课导入）**：导入**全程不看**开课标记/在不在训（写进 docstring + 用例）；唯一与课状态有关的一步
  只是 T6 段末横幅转交，拿不到 hub 摘要就**不转交且说出来**（`bannerNote = 段末横幅未落账：hub 不可达`）。
  为此 `fetchHubOfflineResults` 的返回体从「摘要或 null」改成 **`{results, hubUp}`**（「hub 不可达」与
  「hub 活着但没这一段」是两件事，挤进一个 null 就只能编一个原因）。
- **H 配套（F9 落地选择①）：`tmp/<课>/deliver/` 是课程的长期资产**。`tools/tmp-clean.py` 只收它的**兄弟**
  （`LONG_TERM_KEEP`，递归到资产所在那一层）；备选②（把长期资产移出 `tmp/`）被否 —— 那要改导入/回灌/
  控制台三处路径口径，而豁免在收敛侧一处即可，且危险窗（课已停、`training_running()` 闸放开、人正在导入）
  正是 `tmp/<课程>/` 被判成 run root 时。导入/回灌的 docstring 同步写明这条语义。
- **同批加的闸（事故驱动的，非 plan 项）**：`refuses_zero_retention()` —— `--keep-runs 0 --keep-days 0 --apply`
  拒绝执行（要 `--force`）。**事故**：2026-10-17 一次探针用 `mod.TMP = <临Dir>` 注入，而 `plan` 的
  `tmp_dir` 默认参数在**定义时**绑定 ⇒ 探针实际在**真 tmp/** 上跑了一趟 `--apply`，1907 项 / 38GB
  （各课本地 `it<N>/`、账本、日志）一次抹掉且**不可恢复**（`rmtree` 不进回收站；唯一幸存的是三个
  `PROTECTED` 顶层，以及**本次豁免的 `deliver/`**—— §7-3 的豁免在真实数据上当场自证）。两处据此修死：
  ① `plan(tmp_dir=None)` 改调用时解析（默认参数陷阱）；② 零保留 + `--apply` 需 `--force`；
  ③ 用例改按**目标集合全等**判（不再是否定式子串），并新增「资产在更深一层也要保住」的用例。
- **备选与否决**：指数退避（15s→300s）—— 否（对「抖动 60s 后恢复」这个主场景是降级；若将来要，只能
  限定为**日志节流**）· 抽屉删除加保留窗口 8 轮/24h —— 用户 2026-10-16 已否（回传成功即删）·
  新增 `courseFromPackage=1` 请求字段让控制台按包内课程导 —— 否（`IMPORT_META` 落地后 python 侧已有
  权威对账口，多一个会被误用的开关不如把冲突说清楚）· 把长期资产移出 `tmp/` —— 见上。
- **后果**：`tmp-clean` 的删除目标从「整个 run root」变成「刨掉资产后的各块」（无资产时仍是**一次
  `rmtree`**，与旧行为逐字节一致）；文本断言基线 FORM 1470 → **1480**（+10 操作员读数行 −6 已改成
  非文本判据，逐笔分摊写在 `tests/test_source_text_assert_budget.py` 模块头）。

## §2026-10-17-goalnn-hub-memory-slim（hub 递送流式化 + result 有界 + 账本真增量 + 课程表可淘汰 + /admin/mem）

- **来源**：`plan/hub-server-memory-slim.plan.md`（L1/L2/L3）+ 事故现场：hub（`python -m hub.server`
  --port 8787 --traj-root tmp --discover）连跑 35h 后 **WS 6.24GB / priv 10.04GB / peak 7.71GB**，
  而活状态实测全空（7 门课 · `pending_n=0` · 三份账本合计 81KB · 线程 64/句柄 656 正常）。
  确证实验：打 20 次 16MB net-probe（320MB）⇒ RSS **零增长** ⇒ 堆已被撑到高位且不再向 OS 要/还
  ⇒ 真因是**历史峰值被堆锁住（棘轮）**，峰值来自「整个文件 `read_bytes()` 成 bytes 再递出去」。
- **L1 递送流式化**：`hub/http_face.py` 抽出 `_send_headers` + `_send_body`（传输语义只此一份），
  三种喂法 `_bytes`（内存 bytes）/ `_bytes_file`（盘上区间，`readinto` 复用一块 ⇒ 峰值恒 = SEND_CHUNK）
  / `_bytes_files`（多件分帧）。改造点：`_serve_path` · `/payload`（Range 分支不再切第二份）·
  blob 批量（不再 `join`）· `/offline/task-pack` · 锚点件。字节流与旧实现**逐字节等价**。
- **L2 大块读收口**：`common.fs.same_bytes`（分块比较，替掉 `read_bytes() == blob`）·
  `sha256` 一律走 `common.hashing.sha256_file`（**不新造**，它本来就是分块的唯一实现）·
  `POST /jobs/{id}/result` 从**无界** `_read_raw_body()` 改成 `_read_capped_body(RESULT_BODY_MAX=64MB)`
  ——取值依据：实测 `result.json` 最大 260KB（6 份样本）⇒ ~250× 余量；复核方法写在常量注释里
  （量账本里 `record_result_recv` 的 p99，别只看盘上剩下的）。
- **L3 常驻侧 + CPU**：`_read_ledger` 补上 H6 缺的那一半 `_ledger_off`（记住已解析字节位 ⇒ 只**读新增**；
  半截行留在 offset 外；文件被截短自动退回全量）——旧实现只在「size 未变」时省 IO，一变就全文重解析，
  长跑每拍 O(账本)，hub 35h 累计 74713s CPU 有一半是这个。`discover` 补 `drop_course`
  （课程表原先**只增不减**）：判据只认「**课程目录没了**」，且**在飞（内存租约）/ 有人接管不摘**；
  新增 `GET /admin/mem`（RSS + 逐课账本行数 + 六张常驻表规模）。
- **备选与否决**：① 用 psutil 读内存 —— 否（hub 的 import 闭包只许标准库，
  `tests/remote/test_hub_import_graph.py` 钉着）；② 「停课（删开课标记）也摘表」—— **否**（停课后
  控制台要看得见、云机要能取包、人要能导段，摘表只带来 404；派发已由 `_serves_course` 挡住）；
  ③ 淘汰判据加「超 fresh 窗口」—— 否（卡住的课会被误当死课）；④ 水位自重启（plan L4）—— **暂不做**
  （先有 L1–L3，否则是拿重启掩盖；真机判据 = 改完 1 小时后 `/admin/mem` 的 rss 仍 ≤150MB）。
- **后果**：递送路径的峰值从「文件等长（Range ×2）」降到「≤256KB」；账本读从 O(账本) 降到 O(新增)；
  课程表可淘汰 ⇒ tmp 被清之后内存里的账本不再永久驻留。守卫随之更新（成员数 121→124、
  `_init_ledger` 字段 +`_ledger_off`、`_read_raw_body` 调用点 3→2、`SEND_TIMEOUT` 读点 `_bytes`→`_send_body`、
  `store_offline` 白名单 +`common.hashing`）。

## §2026-10-17-goalnn-y21-quartet-design（y20 三腿全负后的四个新方向：减法/死亡链/中盘/退火）

- **背景**（用户令 2026-10-17）：y20 三腿（wall/far/escape）中段读out 全负（§112），失败局互查
  （§113）判明跨腿独立、形状同一 =「正常开局 + 中途崩溃」（败局 4151 拍/kills 8.93 vs 胜局
  7810/20.00）。三腿全是在国王配方上「再添一项状态存在税」，且共同副作用松手化
  （stop +50~100‰、shots/kills 掉、win −11~22pp）⇒ 加法方向饱和。用户从候选中点四：
  减法对照 / 死亡链定价 / 中盘里程碑 / 恐惧税退火，要求各成 y21-xxx 课程、头注写清试验目的。
- **批判吸收纪律**（用户令）：y 系网络结构已换代——x 期及更早裁决**不作授权、只作线索**；
  一切标定/可达性用 y20 现役数据重测（tmp/y21-calib*.mjs：it0=stack2-it200 混合 eval 200 局
  + 三腿末 10 点；数字写进各头注）。
- **决定**：四个单变量课程，同起点 stack2-it200（sha16 `1a7821240d3c47b9`）、同新种子 20270205
  （20270204 及之前全用过，§15.1；四路互相可比 + 与 y20 三腿同起点跨代可比）、其余设置逐字比 y20-stack2：
  · `y21-nohurt` = 删 wHurt 项+参（y1h 从未判决）；分支：四读数任一显著回补 ⇒ 减法成立 /
    全平 ⇒ hurt 于国王底无边际，减法线止。
  · `y21-chain` = ＋`- wChain*damageWhileLow`（0.06；重测 98.7/局、P(>0)=0.775、占承伤 47.6%
    ⇒ 与 x24 wBurst 稀疏判阴的关键差异用 y20 数据重证；增量 ≈5.9 分/局）。
  · `y21-tempo` = ＋里程碑 edge-trigger 8/12/16 = 6/9/12（x20「空气」裁决不越代继承；
    y20 重测 P(k≥8)=0.910/败局 0.714 可达；胜-败差分 17.1 分 ≈ 5.7 人头）。
  · `y21-temper` = param_schedule wHurt 150→15、wThreat 120→12 linear until_iter=150
    （y 系首用该机制；10% 残值保底、与 nohurt 端点区分；残值 ≈0.8 分/局）。
  门：四腿同持 y20 三线（win ≥0.70 / kills ≥16.5 / gameover ≤30% + timeout ≤5% 零容忍）
  + 杀速保险 / D01 ≤85 / fear-watch；各自附专属 readout/watch 与双分支（头注）。
- **备选与否决**：① 一条腿打包多方向 —— 否（归因不可分，y20 三腿教训 = 单变量才可判）；
  ② chain 用 damageBursts（x24 同款列）—— 否（y20 重测仍稀疏 0.30/局，密度不达标）；
  ③ tempo 复用 x20 剂量不重标 —— 否（旧网裁决无授权；以 y20 可达性重标并预注册莽化 watch）；
  ④ temper 直降到 0 —— 否（与 nohurt 端点重合、丢失最小排序信号；取 10% 残值）；
  ⑤ 起点用 y20 三腿末权重 —— 否（三腿读out 负、未终判；国王 stack2-it200 是干净最强基线）；
  ⑥ 四腿沿用 y20 三腿种子 20270204 —— 否（§15.1 防记忆化；新种子 20270205）。
- **验证**（落盘时已绿）：`tmp/y21-check.py`（单变量隔离逐字对账 + validate_reward + 数值对账：
  nohurt 删税后 hurt 零效应 / chain 单价精确 −0.06/点 / tempo 台阶 0,+6,0,+9,+12 / temper
  it1=150→it150=15→恒定、中点 82.95 精确）+ `test_jsonc_courses_load` 绿。
- **后果**：四文件就绪（`nn-training/curricula/y21-*.jsonc`）；开课 = **人在控制台点**（四连点人定）；
  §15.5 新 out/traj/backup + opt fresh；终判前不动任何既有腿。

## §2026-10-17-goalnn-y20legs-close（y20-wall/far/escape 三腿停腿结课）

- **背景**：三腿 2026-10-17 06:04–07:03 起跑，用户 12:38–12:40 掐停（wall/far 止于 it211、
  escape 止于 it181＋it182 停在 pending）。终判依据 = §112 中段配对面板读out（wall 目标未达成＋
  杀速亮灯；far 距离轴双不动＝空气判据成立；escape 滞留税反向＋杀速双掉＝杀腿双条件成立）
  ＋§113 失败形状，且三腿全程 eval 峰值均为起点 it0（0.685）——从未超越国王。
- **决定**：三腿停办；结课报告节回写各自课程文件（状态行 ＋ 跑量/轨迹/readout/判决/遗产/证据）；
  `docs/nn/experiments.md` §114 汇总；权重冻结 `nn-training/weights/y20-{wall,far,escape}/`
  （止于 it211/it211/it181）供复核。
- **备选与否决**：① 让三腿跑到 cap 500 —— 否（判据在 it160–190 已足量，尾段只添算力不添结论；
  escape 双条件早成立）；② 三腿各再开一步「修正腿」—— 否（目标轴本身死，修正无从修；方向饱和，
  改从减法/新价格轴走 = y21 四腿）；③ 用三腿末权重做 y21 起点 —— 否（末权重未过起点，非干净基线；
  y21 已定国王 it200）。
- **后果**：y20 三腿实验线闭环（全负 ＋ 松手化副作用到停腿未收回：「加状态存在税」判饱和/负）；
  结课报告 load 复验绿；y21 四腿四连点由人在控制台开（本条目记录时尚未开）。

## §2026-10-17-goalnn-opt-delta-f16-underflow（y21-temper 熔断根因 = opt delta-f16 下溢成 v=0 动量炸弹）

- **背景**：y21-temper it11 KL 2.5432 暴增、policy/value loss 同爆、win 0.689→0.25，it13 触发 F4 熔断（KL≥0.075 三连）。
- **诊断**（全文/证据 → `docs/nn/experiments.md` §115）：跨机 opt 走 `remote/opt_delta.delta-f16`
  （`(new−base).to(float16)` 无缩放，~6e-8 以下归零）⇒ 罕见弱特征参数的 `exp_avg_sq` 增量永远被抹平，
  hub 权威 opt 出现 **v≡0 而 m≠0**（真 Adam 不可能态）⇒ 特征全批零梯度的轮里每步 `lr·m/ε` 几何和 ≈ 1.35e5·m，
  单轮把单个 stem 权重推 −32.6（实测 m=2.415e-4，`1.5e-4·m/1e-8·9.0=32.6` 精确闭合）。跨腿/跨代同指纹：
  y20-wall it210 opt 同弹（索引精确命中）、y20 四腿 stem 单点跳>2 共 28/49/20/20 轮。**与恐懼税退火无关**
  （跳变只在 stem、无退火腿同样中弹）。
- **决定**：① temper 判「外因致死」，不判退火方向死；② 修复传输下溢（hub 重建后 / worker 装载后对
  `exp_avg_sq` 消毒设下限，√v_floor≫ε=1e-8；备选 Adam eps 上调作保险），按 §7 先写红测试；③ 修复后以新课程文件
  重开（y21-temper2 = 新实验，§15.5 新 out/traj + 本条目续记）；④ 在跑三腿继续（修复落地后对其后续轮自动生效），
  y20 结课判决不改（方向轴判负不因死单元改判）。
- **备选与否决**：不修直接重开——否（雷区仍在，掷骰子）；从 it10 续跑——否（权威 opt 链已污染、续跑需改课程=新实验，
  不如干净重开，仅损失 13 轮）；改 opt 传输格式（i16 缩放 / log 域 / 逐张量 scale）——暂缓（属传输大改，先落下限消毒）。
- **后果**：所有跨机轮的 Adam 动量可信度打问号（本机权威副本也被 f16 重建污染——违反 wire-diet「回传降质、权威不降质」
  的口径）；修复前开的新腿都可能再抽到雷。

## §2026-10-17-goalnn-opt-underflow-sanitize-and-temper2（opt delta-f16 下溢消毒落地 + y21-temper2 重开）

- **落地**（`plan/opt-delta-f16-underflow-fix.plan.md` v2）：`remote/opt_delta.sanitize_adam_sq` 在
  worker 装载口（`train_core._load_opt_state`）与重建落地（`apply_opt_delta` post-pass）把
  `exp_avg_sq` 抬回 `(|m|/K)²`，K=10。传输格式 `BOD1`/`delta-f16` 不动；base 不消毒（两侧逐字相同
  是 delta 语义前提）。P1 增加可选计数日志，读侧 `rebuilt_opt_tar` 同样受益。
- **推导修正**：健康态上界不是单步尖峰的 3.16，而是 `(1−β1)/√(1−β2)/√(1−β1²/β2)=7.27`（β1=0.9,
  β2=0.999）⇒ 健康态 `v≥m²/52.8`；`m²/100` 下限只留 1.9× 余量，仍能对健康态按位 no-op。
  触发后单步 ≤10·lr；96 步轮和实测 0.0107（v=0 现码 32.601，step=960；step=0 时 bias correction 会到 98.119）。
- **测试**：T1a 真 Adam step≈960 红→绿；T1b 钉 f16 传输下溢（v 增量 ~2.9e-11 → payload 0）；
  T2 helper（幂等/7.27 边界/元素计数/非 Adam/amsgrad 跳过）；T3 `_load_opt_state` + AST 接线；
  P1 日志只断 `len(lines)==1`（不抬源码文本断言预算）。`tests/remote` 1356 绿、`bun run check` 绿。
- **temper2 重开**：`curricula/y21-temper2.jsonc` = y21-temper 的逐字规格（seed 20270205、起点
  `1a7821240d3c47b9`），但 `name/out/traj/backup_dir/backup_prefix` 全改 `*2` —— 不能只改课程名/头注，
  否则复用 `tmp/y21-temper/` 的取证现场。同 seed 与 §15.1 的关系：这是**修 bug 后的配对复现**，
  不是闭环调参的语料复用（评估→更新环不拿这份语料选点）。
- **生效/回滚**：P0/P1 都随集群代码快照（按会话冻结）；必须重启当前 snapshot anchor（本次 = hub，
  `tmp/.code-snapshot/snapshot.json`）才生效。快照打整棵工作区 ⇒ 重启前把无关的
  `trainer/dispatch.py`/`queue_local.py` 改动隔离/提交，否则三腿续跑会同时换 dispatch 行为。
- **备选与否决**：改传输格式（i16 缩放/log 域/逐张量 scale）——暂缓（数据面大改；消毒已闭合爆炸）；
  Adam eps 上调——备选，未做；P0-only——未取（P1 保留给盘上权威件 + 读侧重建两个真实消费者）。
- **后果**：受影响参数的 v 被钉在 `m²/100`、步长上限 10·lr，负增量也下溢 ⇒ v 只升不降（棘轮）；
  这是“封顶爆炸”不是“恢复真 Adam 动量”。temper2 与 temper 是“同课程 + 修传输”的对比，不是逐位复制；
  landed opt 的 `opt_sha` 链自 hub 重启轮起换语义（链内自洽）。修复前开的新腿仍可能带疤痕。
- **待办（人）**：重启 snapshot anchor → 确认三腿下一轮 P0/P1 消毒行 / KL 不弹 → 控制台开
  y21-temper2 → 头 20 轮 stem 单点跳≤2 后回写 `docs/nn/experiments.md` §116。

（DECISIONS 尾部 · 全文 → `docs/nn/experiments.md` §116）
## §2026-10-17-goalnn-single-resident-trainer（常驻写者收成一个 serve：准入闸 + 心跳判死；卡死只告警不自动重启）

- **来源**：`plan/single-resident-trainer.plan.md` §1–§2 + `plan/single-resident-trainer.review-bf.md`
  （P0-1 消费面 / P0-2 写点覆盖 / P0-3 预采子进程 / P1-F3 env 口 / P1-F6 pid 身份）。
- **S1 闸（`worker/trainer_gate.py`）**：`trainer/run_rl.py` 背四种角色，而常驻写者只允许
  `run_rl_cluster.py --serve`；判据 = **没有任何一次性标志** ⇒ 视为常驻 ⇒ 拒启（与 `--course` 无关），
  拒启消息必给替代（`--force` 是锁抢先、过不了闸）。逃生舱 `--allow-detached-trainer` 放行 +
  自报 `tmp/trainer-detached.json`（环形 5 条）⇒ 控制台 `warn` 横幅。
- **S2 写侧取「独立看门狗线程」**（新 `worker/trainer_heartbeat.py`；先例 `worker/eval_heartbeat.py`），
  不是 plan 首选的「每拍 + 两处 tick」：serve 是**单线程**，一次 `sup.step()` 之内可合法阻塞到 +3600s
  （预采 join / PPO 让位 / 引擎冷启动），写点挂在拍上会把**健康进程**写成陈旧 ⇒ 误杀。线程每 5s 原子写
  （`NN_TRAINER_HEARTBEAT` 可重定向），STALE=300s ⇒ 最长合法间隔 < STALE/2。
- **S2 消费面（定案 B，否决 A 自动重启）**：`healthy` = 账本 pid 活 ∧ 心跳不陈旧；`stale` ⇒ 告警坞一条
  `err`（`trainer-stall`，指引「组件卡 trainingLoop 停止→启动」），**不自动重启**。理由：① 全仓没有
  任何组件是自动重启的，为 trainer 单造一类机制要自己回答 pid 复用 / 写盘中途 / in-flight PPO 让位；
  ② 卡死的是**活**进程，杀掉比晚 5 分钟重启贵（一根 20 分钟 PPO 尾巴被砍 = 重跑一轮）；
  ③ 恢复路径已存在（`trainingLoop` 在 `COMPONENT_KILL_TREE` 里），只是没人知道要按。**退路**：将来要做
  自动重启，应是**通用监督器功能**（所有组件同一口径），不是 trainer 专属旁路。
- **不可知一律不判死（四条，都有用例）**：文件缺失/坏 JSON · `hb.pid ≠ 账本 pid`（冒烟预演占**同一个**
  `trainingLoop` 槽）· 判据上线前启动的 serve（`HEARTBEAT_SINCE_MS` 一次性兼容窗）· 启动宽限 120s。
- **被否的备选**：用 `progress_ts` 陈旧触发重启 —— 否（进度不是活性：一个 20 分钟的 PPO 等待会被当成
  卡死，比卡死更贵）；每拍写心跳 —— 见上；`--force` 当闸的逃生舱 —— 否（锁抢先与准入是两件事，
  闸在锁之前，混用会让「过了锁」被读成「允许常驻」）。
- **后果**：`/api/state` 多一个 `trainerHeartbeat`（pid/ts/steps/waiting/逐课 state + verdict，只读盘 ⇒
  控制台重启不丢）；`specs.ts`/`api/views.ts` 里 `trainingLoop` 的「存活即健康」写死 true 全部换成同一判据。

## §2026-10-17-goalnn-entry-split-s3（入口分家 S3：run_rl 退成 CLI 壳；一次性腿独立成模块；逃生舱退役）

- **来源**：`plan/single-resident-trainer.plan.md` §3（S1/S2 之后的一步）+ 用户裁决 2026-10-17
  （「S3 拆入口后 `--allow-detached-trainer` 怎么办」⇒ **彻底删掉常驻分支**）。
- **形态**：`run_rl_cluster.py --serve`（唯一常驻写者）· `run_rl.py`（CLI 壳：解析 → `--echo-config`
  dump / 分流 / 拒启）· `once_shot.py`（新；冒烟 / 导出 / 预采的唯一实现）· `collect_only.py`
  （预采执行体，入口仍是 `run_rl.py`）。壳里**没有** `run_training` 调用/import（AST 判据钉住），
  唯一保留的训练调用点在 `once_shot.py`（冒烟是真跑一轮）。
- **四条腿的契约逐条保持**（搬运不是重构）：冒烟**取**课锁（真跑一轮，作废点全在收官/权重层，
  没有「零写 traj」依据 ⇒ §3.1 选项 A）· 导出**不取**锁（只读快照，与 serve 并行是设计目标）
  且**仍过开课闸**（「停课的课程不能导出」此前由常驻分支顺带守着，§3.3 唯一不能丢的一条）·
  预采只带 `--course` + `--collect-only-config`（P0-3 的动作覆盖通道）· `--echo-config` 不是腿
  （解析面 dump，在分流前短路）。锁策略提成纯判据 `takes_course_lock(args)`。
- **逃生舱退役**：`--allow-detached-trainer` + 自报 `tmp/trainer-detached.json` + 服务端读面
  `dashboard/src/server/api/detached-trainers.ts` + 告警坞 `detachedTrainerAlerts` 四件一起删。
  理由：常驻分支删掉后旁路无处可去，横幅数据源会永远为空；留一个「能解析但什么都不做」的旗标
  比删掉更坏（操作员会以为绕过了闸）。判据随之从「拦住旁路」变成「没有旁路可走」。
- **被否的备选**：① 保留逃生舱、把常驻实现搬进独立模块 —— 否（那就是把唯一写者的纪律留一个
  常设破口，而 S1/S2 的前提已成立）；② 保留旗标但只打印「请用 serve」—— 否（假动作比拒绝更坏）；
  ③ 给 `once_shot.py` 加 `__main__`（plan §3.1 的原始形态）—— 否（会变成第二份 argparse：
  mode/rl-config/课程覆盖要再写一遍，而预采子进程的命名空间重建正是靠「只有一份解析面」）。
- **后果**：`run_rl.py` 只剩解析与共享原语（锁助手 / `update_kwargs` 原地不动 —— serve、run_bc
  与一串护栏用例都 import 它们，评审 P1-F4）；console 侧的 `trainingLoop` 组件与
  train-smoke / export 两条腿**一字未动**（它们本来就走一次性口）。

## §2026-10-17-goalnn-y21legs-close（y21 nohurt/chain/tempo/temper 停腿结课：opt 下溢污染、方向未判、v2 重开）

- **背景**：opt delta-f16 下溢（§115/§116）扩散到全部旧 y21 腿——三长腿 stem 单点跳最高 39.8、
  KL 尖峰 0.532，nohurt 无 KL 尖峰但 stem 19.3；it210 eval 三腿 0.60/0.55/0.6475 均低于起点 0.685；
  temper 早于 it13 熔断。修复已落地（`adb33432`），但已死通道不可复活。
- **决定**：① 旧四腿全部停腿（nohurt it210、chain it213、tempo it212、temper it13），权重冻结、不回放；
  ② 不把旧结果当作 nohurt/chain/tempo 方向负判，不改 temper 的「外因致死」判；
  ③ 四腿以新实验重开（`y21-{nohurt2,chain2,tempo2,temper2}`，新 name/out/traj/backup，
  同 seed 20270205/同 stack2-it200 起点）；④ 取代 underflow 条目的「在跑三腿继续」。
- **备选与否决**：① 让三腿跑完 500 —— 否（死通道污染无法归因；继续只买污染样本）；
  ② 原地续跑/改名复用 —— 否（覆盖 tmp/y21-* 取证现场、违反 §15.5）；③ 只重开 chain/tempo ——
  否（nohurt 也有 19.3 死通道，三方向干净对照必须同批）；④ 回放旧权重做 warm-start ——
  否（未过起点且带死通道）。
- **后果**：旧四腿实验线结课（全为污染/外因，非方向判决）；v2 四腿 2026-10-17 21:55 控制台开课；
  权重冻结供复核。v2 是修复后的第一批干净语料；快照已于 21:50 重打（commit 0493af29，含修复），v2 21:55 开课。

## §2026-10-18-goalnn-export-must-not-cancel-live-job（撤销权只归派活方 + 等待侧同看一份账本：导出进程不许撤在线腿的活）

- **背景**（2026-10-16 04:44–07:51，y5-hide）：在线腿发布了 PPO job，云机认领后失联；06:17 一个
  「导出全离线任务包」进程（新 runId）构造 `TrainingLoop` ⇒ 跑 M2 腿启动清理 ⇒ 把上一 runId 的
  **pending** 活写成 `job_cancelled`。后果三重：派发池（按账本净态）不可派；`/jobs/{id}/status`
  （按目录 + hub 内存）仍答 `pending`；等待侧只信 `/status`（M1 看门狗只认 404）⇒ 空等 3h+。
  根因：**job 只有一份账本，却有两套互不相同的推导；而「撤销权」没跟「派活权」绑定。**
- **决定**：① **A 撤销权收口**——`trainer/dispatch_queue.dispatches_jobs(args)` 是「本进程会不会
  派活/等回传」的**唯一实现**，`TrainingLoop.__init__` 的 M2 清理加它当闸，`takes_course_lock`
  改成读同一个谓词（「导出腿不取锁」与「导出腿不撤单」从此一个判据）；`cancel_jobs_of_previous_runs`
  本身不改。② **B 等待侧认账本**——`_remote_ppo_lost` 在 30s 节拍上先读本地账本**净态**，命中即走
  既有 lost 收尾（撤单 + 连败预算 + 丢会话 ⇒ 下一拍重发同 jid）；净态折叠的唯一实现抽成
  `worker.bc_ledger.job_net_event`（`cancel_job` 的内联循环改用它）。③ **撤单来源白名单**——只对
  `M2_CANCEL_REASON_PREFIX`（写者与读者共用的常量）那一类自愈。④ **C status 与池同源**——
  `store_ledger._ledger_net_state` 公共化 + `queue_claims` 同名转发，`_get_status` 在 `leased`/
  `frozen` 之后插 `cancelled` 档（既有取值不变），`_wait_state_note` / `wait_job` 消费侧把
  `cancelled` 当**可重试**路（撤单重发，**不** ABORT 停腿）。
- **备选与否决**：① 等待加总超时——否（第二条命，且超时只能放弃不能重发）；② 导出进程复用在线腿
  runId——否（`RUN_ID` 是进程级常量，为打包动它是全局行为变更）；③ 清理前问 hub「是不是别人在等」
  ——否（无此 API，等于造第二事实源）；④ 导出腿走独立入口不构造 `TrainingLoop`——否（打包要复用
  轮内计划/权重/预算，重构级）；⑤ M2 搬去「首次 publish 那一刻」——否（清理时机是安全属性：新腿
  开课前就该 reap 上一代目录，且 `__init__` 是腿启动的唯一确定点）；⑥ B 对所有 `job_cancelled`
  一视同仁地自愈——否（人工 `/admin/cancel-job` 与 `cancel_unsettled_jobs` 写同一份账本，无差别
  自愈=替操作员反悔，且顺手 `unfreeze_job` 把人工/熔断冻结一起解）；⑦ 用 `scan_jobs` 的「曾经
  终局」集合判撤单——否（只增不减 ⇒ 重发后一拍一撤，~90s 三连败自伤停腿）。
- **后果**：导出/打包进程再也无法影响在线腿的活；同类误伤现在**自愈**（腿自己撤单并重发同 jid，
  无需人工停课开课）；`/status` 不再对已撤的活撒谎（排障与等待打点同时受益）。代价是多一个
  status 取值 `cancelled`（旧读方落默认分支）与一次 per-30s 的账本全量读（`job_net_event`）。
  plan/export-must-not-cancel-live-job.plan.md 的 A/B/C 全部落地；全文 docs/nn/remote-transport.md §104。

## §2026-10-18-goalnn-y22-phaseA（y22 阶段 A 四条腿：先修优化器，判据换成"余量"）

- **背景**（2026-10-18）：y21 四腿（chain2/nohurt2/temper2/tempo2）配对读数为
  **Δwin −8.9 ～ −13.5pp**、斜率 −6.5 ～ −8.1pp/100 轮（t≈−3），**且四条腿的奖励账本 Φ 也同向降
  5–16 分** ⇒ 不是 reward hacking。用 stack2 的公式给人类语料记 Φ = **+102.7** vs NN **+80.0**
  ⇒ **奖励排序是对的**，问题在优化器不爬坡。另：胜负本质是一笔**生死账**——
  打完 20 杀要 `20/杀速` 拍，血条能撑 `219/(承伤/千拍)` 拍；NN = 7576 vs 7300（**余量 0.96×**），
  人类同座位 = 4975 vs 9820（**1.97×**）。⇒ 在优化器学会爬坡前，任何奖励改动都被漂移吃掉。
- **决定**：开 y22 阶段 A 四条腿（`nn-training/curricula/y22-{null,lr,batch,gamma}.jsonc`），
  起点 `y20-stack2.it200.pool800`（sha16 `1a7821240d3c47b9`）、seed **20270206**（四腿共享）、
  各 500 轮 cap、单变量：① `null` 零改动（把"共同漂移"钉死）；② `lr` 表两格减半
  （0.00015→0.000075 / 0.0001→0.00005）；③ `target_transitions` 24576→49152（+ max_hours 48→72，
  预算非变量）；④ `gamma` 0.998→0.9995（有效视野 500→2000 拍）。
  **判据换口径**：主读数 = **余量**（`20/杀速` ÷ `219/(承伤/千拍)`，目标 ≥1.3），
  win 仅作副门（≥0.685 = 起点值）；Φ 斜率 ≤ −0.05/轮即红。规格全文 `plan/y22-winrate-ladder.plan.md`，
  落盘自检 `tmp/y22-check.py`（ALL GREEN：奖励逐字 = stack2、差异集逐腿 == 声明变量、四关 stage_json 可用）。
- **备选与否决**：① 直接继续调奖励——否（y21 四腿已证在 −7pp/100 漂移下测不出 <7pp 的效应）；
  ② 直接上人类 BC（`demo_bc_coef` 0→0.2）——否，但保留为阶段 B（`demo_bank.npz` 仍是 v4 旧代
  ⊥ 当前 schema v5，要先重导 160 局才能喂；且未证实优化器能爬之前，BC 锚只会被 PPO 冲掉）；
  ③ 再加一条"存在税"——否（y20 wall/far/escape、y21 nohurt/chain 全负）；
  ④ 用 414xxx 人类座位当评估面板——否（阶段 B 要拿它当 BC 语料，同语料重磨 = 记忆化，§15.1）；
  ⑤ 把 lr+batch 合成一条腿——否（省一半算力，但读不出归因，这正是 y21 四腿的教训）。
- **后果**：阶段 A 跑完按"哪条腿 Φ 斜率 ≥ 0"选底座；四条全负 ⇒ 问题不在这些旋钮，
  下一步打 critic/起点（回到 stack2.it120 一类更早的同代权重）。阶段 B/C 的奖励腿在 A 出结果前不开。

## §2026-10-18-goalnn-demo-move-only（y22 B′：demo BC 只训 move，fire CE 全局删除）

- **背景**：fire 的 mask 语义是「当前打得出去吗」，label 是「有没有按住」，冷却期两者互斥；
  43744 个可训练 fire 样本里 hold 只有 **806（1.84%）**，28448/29254（**97.2%**）的 hold
  被 `_masked_ce` 整样本跳过 ⇒ fire CE 实际只教 release。move 的 5 类掩码全合法
  （101153/101153）。
- **决定**：`worker/ppo/engine.py` 的 demo BC **全局删掉 fire CE**，只留 move CE；
  **不加** `demo_heads` 课程键（不为已知坏项新建机制）。恢复成本 = 调用点加回一行。
- **测试**：`test_demo_fire_labels_do_not_reach_fire_head` 翻转 fire 标签，断言**全部参数逐位相同**，
  且 `move_head.weight` 相对初始权重变化。原「move_head 必须不同」的写法是错的——删 fire 后
  `_dact[:,1]` 不再被读，两次运行逐位相同。
- **对照**：不拿 §33 冒充单变量（语料 61/38564 vs 160/101153、起点、轮数、种子、schema 全不同）；
  正式消融用父提交 worktree 跑 fire CE on 的 C 臂，与 move-only M 臂同数据/起点/种子/轮数。
- **后果**：任何 demo 腿不再有 fire BC；fire 头只由 PPO 训练。开腿前的 `entropy_move`/`entropy_fire`
  分头遥测、`stopFireTicks`/空停联合口径、holdout bank、`gate-halt=halt` 等前置见
  `plan/y22-demo-move-only.plan.md`（R2）；§118 的哨兵结论已更正为仪表无效。
## §2026-10-18-goalnn-eval-gap-honesty（评估缺口不许伪装成读数：保真 / scratch 隔离 / 处置 / 第四态 / 判决准入）

- **背景**：y22-lr it5 的 `eval_log.jsonl` 是四条兄弟腿里**唯一** `dropped>0`、也**唯一**高于起点
  的读数（0.7098 vs 副门 0.685，兄弟腿 0.605–0.6425）——83 局缺口把胜率配比抽歪，差点被当成
  「lr 减半立刻见效」。逐层锚点：① 失败被压成字符串（`rc=… ({(stderr or stdout)[-160:]})`，
  bun 把 `error: ENOENT … open '<path>'` 打在栈**前面** ⇒ 83 条 meta 只剩栈尾，errno 被切掉、
  `is_transient_error` 恒 False）；② 本机腿 2 次预算、无退避、无改派、无熔断（8 秒烧完 83 局）；
  ③ eval scratch 住 `it<N>/`，轮转（`keep_iters=3`）连在飞的评估一起删；④ 判决层
  （`gate_inputs._row_from_summary`）从不读 `dropped`；⑤ `trainer-cluster.log` 的课程前缀不可信
  （`_PREFIX` 是模块全局，eval 的 daemon 线程活过 `prefix_scope`，镜像还会写进别的课）。
  全文 plan/eval-gap-honesty.plan.md（含评审 F1–F11 处置与诚实边界）。
- **决定**：① **D1 保真**——新 `worker/local_eval_error.LocalEvalProcessError`（errno 归一化的
  单一实现）保留 stderr 头尾两段 + `errno_code`/`msg_head`；`common.distribution.is_transient_error`
  按**鸭子协议**认 `transient`（`common/` 是下层，不得反向 import `worker/`）；meta 的 `reason`
  改取头部并**纯新增** `errno`/`msg_head` 两键。
  ② **D1b scratch 隔离**——eval scratch 迁到 `<traj>/_eval-<it<N>>/`（冻结快照 + `local-eval/`）；
  `_rotate_cleanup` 对它**只删**「已有 `dropped==0` summary 且超 `keep_iters`」的轮（没有 summary
  的轮永不删）；`eval_replays_once` 同时认新旧两种 glob（旧课程目录仍要能解析）。
  ③ **D2 处置**——per-leg 尝试计数（`attempts[task]==0` 的 never_dispatched 判据一字不动）；
  瞬断预算 `EVAL_TASK_ATTEMPTS_TRANSIENT=4`；瞬断回队退避 `min(2**(lt-1), 8)`；共享熔断
  `local_stopped`（N 条本机线程；**结算即清零**，与节点腿同款）；非瞬断失败**一手改派**给确实
  还活着的节点线程（判线程存活，不是 `nodes_ok` 非空）。
  ④ **D3 第四态**——新增 `close_reason="machine-fault"`，只在 `workers-gone` 同一前提下细分；
  判据 `lost >= MACHINE_FAULT_MIN_LOST(8)` ∧ 最大节点占比 ≥ `MACHINE_FAULT_SHARE(0.8)` ∧
  该节点是 `local` 或已停派；`dropped/lost/never_dispatched` 三键与分解公式一字不动。
  ⑤ **D4 判决准入**——`EvalRow` 纯新增 `dropped`/`close_reason`；`_row_from_summary` 单漏斗拒收
  （machine-fault 硬拒 + 缺口占 `games+dropped` 超 `GATE_DROP_FRAC_MAX=2%` 拒，与
  `BASELINE_DROP_FRAC_MAX` 同值同分母）；CLI 与训练循环各打一行「拒收 N 条缺口读数」，拒收不静默。
  ⑥ **D5 账**——`log(course=)` 显式课程归属（行标签与**镜像文件**都归对课；缺省 = 旧行为逐字节
  不变）；dashboard 只透传 `closeReason` 一个字段（无新交互）。
- **备选与否决**：① 把 `local` 降级成纯远端 —— 否（本机是四条腿里最快的腿，问题在处置不在参与）；
  ② 按 `close_reason != settled` 一刀切拒收 —— 否（云腿 summary 从不传 `close_reason` ⇒ 缺 1 局
  就整行拒、趋势窗口凭空断档；**量的诚实**才是判据）；③ `_PREFIX` 改 `threading.local()` —— 否
  （牵动 `open_course_sink` 的课程归属契约；eval 是唯一跑在 prefix 作用域外的日志源）；
  ④ 写 `eval-topup.json` —— 否（`_drain_pending_eval` 已在补同一批局，多一个文件只多一条读侧契约）；
  ⑤ summary 补写/覆盖 —— 否（账本 append-only；补评 = 既有 drain + `(iter,wver)` 聚合）；
  ⑥ 改 `eval_games_per_stage` 把 400 局「修」成 200 —— 否（双轨是设计，改它就是改语料 §15.5）；
  ⑦ 动 `tools/sim/export-eval-game.ts` —— 否（在 codehash 集里，改它要动远端 bundle）；
  ⑧ 按 `keep_iters` 一刀切删 `_eval-it<N>/` —— 否（会删在飞的评估，正是本次事故嫌疑）。
- **后果**：下一次同类故障**带 errno 可钉死**，且 scratch 在结构上不再与轮转同生共死；瞬断不再
  烧穿预算、真失败自动换机器、持续故障熔断一行说明；`dropped>0` 的读数要么带量要么不进判决。
  y22-lr it5 的 83 局按 plan §7 人工补评（`eval_a_once --iter 5 --ckpt <归档>`，wver 逐位相同 ⇒
  确定性重放），补完前该读数不得进任何判决。门禁：nn 全量 python gate ×2 绿、dashboard typecheck
  + 1667 用例 + 三份 bundle、`bun run check` 2537、`bun run build` 全绿。
  ⚠ 课程头注「×50/关 ⇒ 锚点 50 + 轮转 50 = 400 局/轮（it0 才是 200）」的补记 pending
  —— y22 四门 `.jsonc` 不在本 checkout。

## §2026-10-18-goalnn-course-metrics-backup — 课程读数在 dashboard 侧自动转存 + 冷存（内存解压读）

- **背景**：2026-10-17 `tmp-clean.py --apply` 把 1907 项 / 38GB 一次抹掉，含全部课程账本
  （`training_log` / `eval_log` / `dist-agent-meta` / `.pool-actuals-cache`），读数不可恢复
  （§377 的零保留闸只挡「整片抹掉」，挡不住常规 keep-runs/keep-days 收敛）。
- **决策（用户四条口径）**：① 清理闸**不拒绝删**——先自动镜像转存，成功即照删；② 每课一个备份目录；
  ③ 备份目录源活动 >14 天自动打包 `<课>.tar.gz`；④ 冷存读**内存解压、不落盘**（只有显式
  `restore` 落盘）。库 = `dashboard/data/course-metrics/`（env `BCITY_COURSE_METRICS` 可换盘）。
- **实现要点**：
  - **写侧**：`course-metrics/mirror.ts` 字节级增量（stat 快路 + 64B 锚点 + 只抄完整行），
    重写型按 sha256；复位先 `rename` 进 `history/<ts>/`（永不静默覆盖）；
    单写者 `.lock` 用 `open('wx')` 原子创建 + rename 抢占。
  - **清理侧**：`tmp-clean --apply` 先调 `dashboard/scripts/course-metrics.ts sync --budget 0`，
    再逐目标核对覆盖（append: bytes==size；rewrite: size+sha）；未覆盖只跳该目标，退出码仍 0；
    `--no-mirror` 是唯一直删出口。
  - **冷存**：`compact.ts` 仅在 `tmp/<课>` 不存在 ∧ `lastSourceActivity` >14d ∧ 无等价包时打包；
    索引 `<课>.tar.gz.index.json` **先落盘再删热目录**；世代只留 2；`history/` 只留 8。
  - **读侧**：`api/course-source.ts` 三态 live⇒mirror⇒archive；冷存用 **`Bun.spawn` 异步**
    `tar -xzO` + 256MiB LRU（存 promise 去重），**禁 `spawnSync`**（单进程事件循环 + Node
    `maxBuffer` 1MiB 会直接失败）。派生缓存只对 live 写回 tmp。
  - **动作闸**：mirror/archive-only 课的课程级改状态动作（preset/start/stop/evalA/export…）
    在分发层统一回 **409 无活体**；`setCourse`（只读视图）放行。
  - **池历史**：接入**热备份**（同课 tmp 优先、不双计）；冷存件不参与（聚合是同步路径，与异步
    解压冲突）——残留见 plan §10。
  - **签入前自审补丁（同日，把 §5 判据当反探针重跑）**：① stat 快路必须三条件齐（源 size/mtime
    ∧ **备份侧尺寸**），否则稳态每拍仍读 1MiB 尾窗，且镜像被删/改短发现不了（旧副本进 history +
    `mirror-missing` 复位）；② `tar --version` 自检一次，失败即关 archive 源（`verify` 转红）；
    ③ `restore --force` 不覆盖 `.run_rl.lock` 活体课；④ `evalLadderStop` 补进动作闸；
    ⑤ 重打包后旧 memo 条目的字节账连同条目一起作废。
- **备选与否决**：① 读侧不落盘却用 `spawnSync` —— 否（37MB 成员会停整页 2–4s，Node 语义还在
  1MiB 处杀子进程）；② 成员存在性靠 `tar -tzf` —— 否（gzip 不可寻址，等价每请求整包解压）；
  ③ 转存扫描「tmp 全树」—— 否（会多抄 `deliver/` 的 95MB 副本、把 `pytest-tmp/**` 当课程）；
  ④ 冷存阈值看「备份目录 mtime」—— 否（sync 用源 mtime 回写 ⇒ 重建目录当场判冷 ⇒ 每拍重打包）；
  ⑤ 读侧恢复自动写回 tmp —— 否（隐藏状态；`restore` 保持显式人工动作）；⑥ 训练侧双写 —— 否
  （零接触训练语义，§15.5 不触发）。
- **后果**：tmp 被清后曲线/轮次/对比/池历史（热）仍在 + 「备份数据」徽标；两周无源活动自动降温，
  刷新仍可读。**不承诺**：防盘坏（同盘；仓库外导出是人工纪律）、可续训（不含权重）、reopen 合并、
  actuals 缓存未覆盖的轮（`it<N>/per-game.json` 不备份）。
- **门禁**：`bun run check` 绿、dashboard typecheck + 1684 用例 + 四份 bundle、nn python gate 绿
  （新增 `test_tmp_clean_needs_mirror.py` 7 例）。评审 F1–F15 全并入 plan v3 + `review-bf`。

## §2026-10-19-goalnn-rollout-end-event — 指标行在「采集收官」即刻上屏（新增 `rollout_end` 账本事件，不提前写 `iteration`）

- **背景**：控制台首页走势图与指标表的唯一数据源 = `training_log.jsonl` 里 `event=="iteration"` 的行。
  而 `record_iteration` 在一轮 13 步里排第 ⑪，前面压着 `ppo` —— 远端模式要等云机跑完梯度步
  + 权重回传（实测本机 134s/轮，差网络数十分钟）。于是**采集早已结束**，操作员却要等
  W(it+1) 回来才看到 it 那一点，画面表现为"卡住"。
- **决策**：训练侧在**采集收官窗口**（第 ⑥ 步 `eval_dispatch`，`_log_report` 之后）立刻落一行
  **新事件 `rollout_end`**；控制台 `iters.buildIterRows` 同时消费 `iteration` 与 `rollout_end`。
  同 iter **按信息量取胜**（不是纯时序）：`iteration` 行带着 `rollout_end` 的全部字段再加
  PPO / 传输账 ⇒ 无论先后，它都覆盖 `rollout_end`。
- **为什么另起事件名（★ 本条的全部理由）**：`iteration` 是**断点续跑的推进判据**
  （`worker/resume.py` 的 `last_completed_iter` 只认它）。若在 PPO 之前写它，进程一旦死在
  「采集完 → 梯度步提交前」，续跑会**跳过这一轮**——采集白烧，账本还自证跑过了。
  另起事件名 ⇒ 续跑 / 门禁照旧走 `LedgerView.apply_event` 的 else 分支忽略它，
  **只有控制台指标读面**消费它。
- **字段口径（写侧）**：`rollout_end` **只带采集侧事实** —— `iter` / `time` / `winRate` /
  `outcomes` / `score_mean|std` / `dim_means` / `intentCounts` / `baseIntegrity` / `samples` /
  `ticks` / `rollout_sec`（窗口墙钟）/ `pure_collect_sec` / `dist_phase_sec`（权重下发也属采集侧）
  + 队列模式附加的 `missing` / `expectedGames` / `dist`。
  **PPO 侧键一个都不写**（`kl` / `entropy` / `policy` / `value` / `mean_ret` / `lr` /
  `ppo_sec` / `ppo_cloud_sec` / `wire`）——缺键 = 空 ⇒ 读面按 NaN 缺口处理，
  **绝不写 0 冒充**（0 会在走势图上多画一个假零点）。
- **幂等**：`LedgerView.rollout_ends` 记轮号；同 it 重复进入第 ⑥ 步（让步后重入 / 重放）不再写。
- **备选与否决**：① **把 `iteration` 提前写到 rollout 结束** —— 否（见上，会让续跑跳轮，这是最贵的一类错）；
  ② 控制台自己扫 `it<N>/**/manifest.json` 合成行 —— 否（绕开账本 SSOT，且拿不到 `samples` / 采集墙钟）；
  ③ 另开一份 `rollout_log.jsonl` —— 否（同一条流水被劈成两个文件 ⇒ 双事实源，冷存/轮转各漏一半）。
- **后果**：远端腿上「采集收官 → PPO 回传」这段空窗期，走势图/指标表**当下**就多出 it 的那一点
  （rollout 读数齐全，PPO 列是空缺口并在 iter 列标「PPO 中」）；本轮 `iteration` 行落账后自动无缝替换。
- **门禁**：`nn-python-gate.sh` 绿（新增 `tests/worker/test_rollout_end_event.py` 3 例）·
  dashboard `bun run test`（新增 `server-iters-rollout-end` 3 例 + `web-rollout-end-pending` 2 例）·
  `bun run typecheck` 绿。


## §2026-10-19-goalnn-test-path-isolation — 测试零读写真实工作目录：路径 env 全量预载隔离 + `REPO_ROOT/tmp` 硬编码清零

- **背景（用户定调）**：「测试应该用自己的 fixtures，禁止读写真实工作目录」。实测违规面：
  `dashboard/data/course-metrics/`（热备份库，20 门课）被 `pool-history.addHotMirrorCandidates()`
  当成**课程候选**读进聚合 ⇒ 池历史/贡献数用例在真实库存在时随机红（断言里直接冒出
  `y23-acc-off` 这种真课名）；`auto-offline-handoff.test.ts` 更把 `__ahand-*__` 假课**写**进
  仓根 `tmp/`；`local-worker.test.ts::loadRealConfig()` 读常量 `CONFIG_PATH`（= 线上真
  `rl-config.json`），把断言挂在本机配置上。同命令同树对照确认：**与任何功能改动无关**，
  纯粹是隔离缺口。
- **根因**：`core/paths.ts` 的惰性 env 重定向是**自愿参加**的——测试记得设才隔离；而
  `src/` 里有 **31 处** `path.join(REPO_ROOT, 'tmp', …)` 硬编码（bundles / eval-games /
  state-view / specs…），**绕开**重定向 ⇒ 即使设了 env 也照旧读写真实目录。
- **决策**：
  1. **新增 `core/paths.ts::tmpCourseDir(course)`**（`= join(tmpLogsDir(), course)`），
     把 31 处硬编码全部收敛到它（外加 `--traj-root` / heapsnapshot / `--control-file`
     三处非课程路径改用 `tmpLogsDir()` / `loopControlPath()`）。默认行为零变化。
  2. **新增 `tests/helpers/isolate-paths.ts` 预载**：进程级（早于任何测试模块 import）把
     全部 `BCITY_*` **运行时**路径 env 指进一个 `mkdtempSync` 隔离根，并在根里播种一份
     **最小种子 rl-config**（无节点、无课程）——忘了设 env 的用例拿到的是空夹具，
     而不是线上真配置或 ENOENT。挂在 `scripts/run-tests.ts` 的 `--preload`（与 no-proxy 并列）。
  3. **守卫**：`tests/test-path-isolation.test.ts` —— ① 全部运行时路径必在隔离根内且不在
     仓根 tmp / `dashboard/data` / `nn-training` 之下；② 备份库为空；③ **源码级围栏**：
     `src/**/*.ts` 里除 `core/paths.ts` 的定义外零处 `REPO_ROOT, 'tmp'`。
     `tests/gate-composition.test.ts` 加钉「启动器带 isolate-paths 预载」。
- **备选与否决**：① **继续自愿参加、逐个测试补 env** —— 否（本次 7 条红就是这么来的，
  漏一个红一个，且新增测试天然继续漏）；② 预载只重定向、**不播种 rl-config** —— 否
  （`loadConfig` 不兜底，一批「读配置」用例当场 ENOENT，红从 7 条变 20 条，与要修的污染无关）；
  ③ 把 `BCITY_CURRICULA_DIR` 也隔离 —— **否（本轮明确不做）**：`nn-training/curricula/` 是
  **入库源数据**（与 `src/` 同级的只读事实源），重定向它会让「读课程配置」的用例整体失去对象；
  它与 `tmp/` 这类**运行时**工作目录不是一类。要动得单独一刀，连同所有依赖它的用例一起搬进夹具
  （守卫里有反面断言钉住这个边界）；④ 用 fs 层 hook 拦截真实目录访问 —— 否（Bun 下
  ESM 命名导入绑定早于 hook，且会把「读源数据」一起打死）。
- **迁移的测试**：`auto-offline-handoff` / `server-api-task-bundle` / `console-eval-rounds-api` /
  `course-lifecycle` / `training-multi-course` / `training-shared-trainer` 的路径断言改用
  `tmpCourseDir()` / `tmpLogsDir()`；`server-api-error-registry` 的 P0-3 不再读仓根真账本
  （那本身就是一次真实目录读），改为断言隔离生效；`loop-control` 的「默认落在 tmp/」改为
  **先删 env 再断言**（预载之下原断言恒红）；`local-worker` 的 `loadRealConfig` 改名
  `loadTestConfig` 并改读 `configPath()`。
- **后果**：dashboard 全量 **1712 pass / 0 fail**（此前同树同命令 7 条红，其中 4 条直接
  由真实库/真实 tmp 数据决定）；fs 层追踪确认整个套件对 `repo/tmp`、`dashboard/data`、
  `repo/archive` **零读零写**。**不承诺**：`curricula/`、`nn-training/ladder/`、
  `nn-training/.venv/`（工具链）仍未隔离——它们是只读源数据/工具链，不是运行时工作目录。
- **门禁**：`bun run typecheck` 绿 · `bunx oxlint` 0 警告 · `oxfmt --check` 净 ·
  `bun run test` 1712 绿 · `bun src/server/build.ts` 四份 bundle 过。

## §2026-10-19-goalnn-replay-export-decision-k — 重放导出必须透传课程的 decision_k/decision_events；对账不一致一律拒绝交付

- **现场（用户报障）**：控制台「导出 replay」勾了 y24-k4 it25 的 kills=0/1/2 共 22 局
  （eval_log 里这 22 局全是 `gameover`），导到 `replays/exports` 却有一大批是胜利（clear）。
- **根因**：`worker/eval_replays_once.py::_export` 调 `run_local_eval_game()` 时**没有透传**
  课程的 `decision_events` / `decision_k`，于是静默走缺省 `False` / `10`。而 y24-k4 的**唯一
  实验变量就是 `decision_k: 4`** ⇒ 重放跑的是另一个策略。实测同一 (权重, 关, seed)
  （stage 2000 / seed 860013）：账本 `gameover`/1268 ticks/1 杀；K=4 重放**逐字段一致**；
  K=10 重放 `stage_clear`/7241 ticks/20 杀。manifest 里那 48 处 mismatch
  （22/22 局 ticks、19 局 kills、7 局 outcome）就是同一件事的指纹。
  主循环派发处（`trainer/eval_dispatch.py:807-809` / `trainer/dispatch.py:778` /
  `remote/offline_eval.py:546`）都透传这两项——**漏一处就是 train/deploy 静默分裂**，
  `run_local_eval_game` 的 docstring 点名过这类。
- **决策**：
  1. 导出器从课程 namespace 读 `decision_events` / `decision_k` 并透传，且把口径写进日志
     （`[replay-exp] it<N> 语义口径: decision_k=… decision_events=… max_ticks=… difficulty=…`）；
  2. **确定性契约对账不一致 ⇒ 一个文件都不交付**。原先 `mismatches` 只在完成文案里带一句
     「· N 局对账不一致」，`kind` 仍是 `done` ⇒ 错的录像被自动写进用户目录。
     新增 `jobOutcome` 的 `mismatch` 档：按**局数**汇总 + 点名漂了哪些字段 + 拒绝交付；
     「重新保存」同样拒绝（宁可不给，也不把「账本说败、录像说胜」的东西递出去）。
- **备选与否决**：① **只修透传、不改交付判据** —— 否（这次的 48 处 mismatch 在旧判据下
  照样静默交付，用户是靠肉眼发现而不是靠系统）；② 只交付对得上的那些局 —— 否
  （静默丢局比整体拒绝更难排查）；③ 把 `decision_k` 写进 manifest 让前端展示 —— 已有日志行，
  不再加第二处事实源。
- **后果**：同 22 局重放，`mismatch=0`、22/22 与账本逐字段一致（全 `died`）。
  **不承诺**：其它未透传项——本轮已逐项比对 `run_local_eval_game` 的全部入参，
  派发处与导出处只差这两项（`policy` / `replay_dir` 是导出器自己的语义，不算漏）。
- **门禁**：nn-python-gate 绿（4728 passed，`test_eval_replays_once.py` +3 例：
  decision_k / decision_events / K=10 缺省逐字节不变）· dashboard `bun run test` 1713 绿
  （`web-app-replay-modal.test.ts` +1 例：mismatch ⇒ 拒绝交付）· typecheck / oxlint / oxfmt 净。

## §2026-10-19-goalnn-rollout-end-json-null — `rollout_end` 行的 PPO 侧读数过 JSON 变 null：渲染层全链路「无读数显 —」

- **现场（用户报障）**：dashboard 首页报「该卡片加载失败：Cannot read properties of null
  (reading 'toFixed')」，发生在 y25-price it1 rollout 结束那一刻。浏览器栈：
  `phaseSecsTitle ← MainTable 的 Array.map`。
- **根因**：`buildIterRows` 给 `rollout_end` 行的 PPO 侧字段写 `Number.NaN`（"缺口不是 0"
  约定），而 **`JSON.stringify` 把 `NaN` 序列化成 `null`** ⇒ `/api/state` 吐出去的是
  `{"ppoSec":null,"ppoCloudSec":null,"kl":null,…}`。客户端 `phaseSecs` 的
  `r.ppoCloudSec != null ? r.ppoCloudSec : r.ppoSec` 两条都是 `null` ⇒ `null.toFixed(0)` 抛，
  PanelErrorBoundary 收下整张主表。**it0 基线行一向是同形状的 null，但它被
  `heroMainRows` 的 `iter > 0` 挡在主表外** —— 所以这个坑一直到 pending 行（iter>0）出现才炸。
- **决策**：读面全链路按「无读数」处理，**显 `—`，绝不画 0**（0 会被读成「训练用了 0 秒」）：
  1. `view/format.ts` 新增 `hasNum(v)`（`null`/`undefined`/`NaN` 统一判空）与
     `fmtNum(v, digits)`；`fmtPct` 改走同一判据；
  2. `phaseSecs` 入参放宽到 `number | null | undefined`，`PhaseSecs.ppo/net` 改为可空，
     PPO 两侧都缺 ⇒ `null`；`fmtPhaseSecs` / `phaseSecsTitle` 用 `fmtNum` 渲染；
  3. `winTone` / `klTone` / `retTone` 接受可空并返回中性档——旧签名 `(v: number)` 下
     `null > 0.05` 恒 false ⇒ **染成绿（"好"）**，比崩溃更隐蔽；
  4. Hero 主表与指标页表的 `scoreMean/kl/entropy/meanRet/lr/kills/loot/avgTicks` 各格
     与本地 `fmtPerGame` 全部改走 `fmtNum` / 可空口径。
- **备选与否决**：① **服务端改吐 `null`（而不是 NaN）** —— 否（`metricSeries`/`sparkPoints`/
  TrendChart 的"缺口"语义建立在 `Number.isFinite` 上，`Number.isFinite(null)` 恰好为 false，
  改 null 也能当缺口；但那样要把 `Series.vals` 改成 `Array<number|null>` 牵连一整片类型，
  收益只是"服务端更诚实"，读面照样得判空 ⇒ 本轮不动，留给后续）；② 客户端收到后统一
  归一成 NaN —— 否（没有单一入口，且归一层自己也会漏）；③ 只修 `phaseSecs` 一处 —— 否
  （kl/entropy/meanRet/lr/scoreMean 五列同样是 `null.toFixed`，逐个爆）。
- **后果**：`rollout_end` 行在首页/指标页正常渲染：rollout 侧有读数，PPO 侧显 `—` + iter 列
  「PPO 中」徽标。**不承诺**：`Series.vals` 的类型仍是 `number[]`（线上实际可能是 null，
  `Number.isFinite` 恰好兼容）——这是已知的类型谎言，见备选①。
- **门禁**：dashboard `bun run test` 1719 pass / 0 fail（新增：
  `web-view-format` 4 例、`web-view-rows` 1 例、`web-app-hero-overview` 1 例端到端渲染）
  · typecheck 绿 · oxlint 0 警告 · oxfmt 净 · `bun src/server/build.ts` 四份 bundle 过
  · 用线上真实 `/api/state` 载荷复验 Hero / MetricsTable 均不再抛。
## §2026-10-19-goalnn-error-denoise-classify — 错误采集去噪 / 分类 / 活动度：访问日志看状态码、续行并入 detail、全局组件只扫一次

- **现场**：`/errors` 的活跃错误里多数是云隧道运维噪声 + 访问日志误报——`parseLogLine` 的
  `LOG_ERR_RE` 命中路径里的 `errors`/`error` 子串（`/admin/errors`、`/admin/worker-error` 的 200 行），
  以及 traceback 续行（`During handling…` / `Exception occurred…`）各成一条。真问题（依赖缺失、
  多进程、500、503、隧道中断）被淹没。评审与设计见 `plan/error-page-denoise-and-triage.plan.md` v2
  （findings `plan/error-page-denoise-and-triage.review-bf.md`）。
- **决策**：
  1. **访问日志按状态码判级别**：`parseLogLine` 识别行首 `"METHOD path HTTP/x.y"`，`<500` ⇒ info、
     `>=500` ⇒ error。识别**不依赖行尾状态码**（尾窗会截断长 query）——缺失状态码按 info。
     采集层不再加第二套「跳过访问日志」判据，避免把 hub 500 一起跳掉。
  2. **traceback 续行不单独成事件**：只跳过无 `[HH:MM:SS]` 前缀且命中 traceback 续行集
     （`Traceback (` / `File "` / `During handling…` / `The above exception…` /
     `Exception occurred during processing of request`）的行；`errorDetailBlock` 仍把整块并入命中行。
     **不按「缩进行」一刀切**（多行 JSON 里的真实错误也可能缩进）。
  3. **`classification` 是服务端规则表**（`server/api/error-classify.ts`，顺序即优先级）：
     `dependency` / `multi-process` / `capacity` / `remote-failure` / `process-lifecycle` /
     `transient-network` / `unknown`。枚举**不含 `misreport`**：误报在采集层就没了，没有写者。
     标签/处置提示住 `web/view/error-hints.ts`（客户端安全），规则与措辞分离。
  4. **`activity`**：源文件 mtime 静默 > 24h ⇒ `historical`，事件时间取文件 mtime 而不是 `now`。
     历史事件仍进登记表与 `/api/errors`（「显示历史」开关），但**不进首页告警坞、不计未 ack**——
     去开课闸后 185 门旧课的尾窗错误不会全变「此刻活跃」。
  5. **采集与课程解耦**：全局组件（selfNode/hubServer/localWorker/cloudflared）按解析后的**绝对路径
     每拍只扫一次**（缓存键 `组件|路径`）；`trainingLoop` 逐课扫；**无 `training-enabled.txt` 闸**。
     每文件最多 20 个不同 `fp`；缓存键不在本拍扫描集即淘汰。组件日志路径改走 `tmpLogsDir()`
     （默认值不变，测试可重定向）。
- **备选与否决**：① **跳过全部访问日志**——否（hub 500 会从「误报」变「永久不可见」，而它是 §1.2 的
  真问题类别）；② **只加 mtime 有界就重开全量扫描**——否（2026-10-17 事故的大头是全局日志被 185 门课
  重复缓存，mtime 不解决重复）；③ **分类放客户端**——否（同一事件在日志腿/上报腿会得到两套判定）；
  ④ **保留 `misreport` 枚举**——否（无写者，且账本重放不读 classification）；⑤ **按课程继续扫全局日志**——
  否（同一文件 ×185 份缓存 = 400MB / RSS 958MB 的直接原因）。
- **后果**：`/errors` 默认视图不再出现访问日志/traceback 续行；5xx 访问行成为 `remote-failure` 事件；
  全局日志事件数不再随课数倍增。**不承诺**：单文件超过 20 类错误时只保留前 20 类（同 fp 仍计次）；
  历史事件仍占 `/api/errors` 投影配额（`ERROR_SUMMARY_MAX`）。
- **门禁**：dashboard `bun run typecheck` 绿 · `bun run test` **1743 pass / 0 fail**（新增
  `server-api-error-classify` 9 例、`server-api-error-scan` 6 例、`web-log-view` 访问日志 1 例）·
  `oxlint` 0 警告 · `oxfmt --check` 净 · `bun src/server/build.ts` 四份 bundle 过 · 根 `bun run check`
  **2560 pass / 3 skip / 0 fail** · `bun run build` 过。

## §2026-10-19-goalnn-error-page-shell-and-resolve — `/errors` 包主页外壳（仍独立 bundle）+ 瞬时错误两拍撤销 / resolved 审计行

- **现场**：`/errors` 是独立 SSR 页 + 第 4 份 bundle，没有侧栏/顶栏，与其它页割裂；同时瞬时网络错误
  在日志尾窗里停留期间会一直占着活跃表与告警坞，源恢复后也不消失。用户口径：错误页包进主页框架、
  波动错误自行恢复后自动撤销、错误不与课程绑定。
- **决策**：
  1. **S1 = 包壳，不进主 bundle**：`ErrorsApp` 内包 `Shell` + `NavSidebar` + `Topbar`；仍由
     `server.ts` 整页 SSR + `errors.js`。**重申**父 plan `component-error-registry` §2 N5 / Q2
     「让 `/errors` 进主 bundle 路由 = 不做」；本轮不反转它（进主 bundle 会把 `/errors` 的 SSR 从
     `getErrorSummary()` 变成全量 `buildStateView`，并让 `/api/errors` 与 `/api/state` 两套轮询并存）。
  2. **S2 = 两拍撤销 + resolved 环**：瞬时两类（`transient-network`/`process-lifecycle`）连续 2 拍
     缺席才移出活跃表：第 1 拍保留（`misses=1`），第 2 拍进内存 `resolved` 环（≤50 条 / 24h，
     `resolvedAt=now`）。非瞬时类保持「消失即结束」。重现清掉同 fp 的 resolved 副本并按新 episode 重弹。
  3. **账本行类型**：`kind:'seen'`（新 fp 首次出现，含 classification/activity）+ `kind:'resolved'`
     （自动撤销时追加，含 resolvedAt）。`replayErrorLedger` 跳过 resolved 行；账本仍 append-only，
     同 fp 的 seen 行仍只有一行。`/api/errors` 增加 `resolvedItems`，页面底部「最近已恢复」单列。
  4. **告警坞去噪**：`componentErrorAlerts` 只数「真问题 · active」；噪声/历史不参与计数与 ack 键，
     噪声数量只在 detail 里折叠报一句。排序/折叠口径不动（父 plan N5 的边界）。
- **备选与否决**：① **进主 bundle 路由**——否（反转父 plan 的显式非目标；SSR 更重；数据面归属未定）；
  ② **瞬时错误不再复用日志签名缓存**——否（文件没变就不再读盘 ⇒ 仍存在的错误一拍后消失，
     下一次无关写入又捞回来 ⇒ episode 抖动、告警坞反复重弹）；③ **把 `resolvedAt` 写进 seen 行**——
     否（append-only 不可回写，且破坏「同 fp 只一行 seen」的契约）；④ **直接把噪声从登记表删掉**——
     否（审计需要历史；用户口径是分类保留）。
- **后果**：`/errors` 首帧含侧栏/顶栏，导航激活在「错误」；瞬时错误在源安静两拍后从活跃表消失并出现在
  「最近已恢复」，账本有审计行；告警坞不再被隧道抖动反复打断。**不承诺**：`resolved` 环是内存投影，
  控制台重启后清空（账本行仍在，但当前没有历史恢复读面）；「源恢复」不由成功哨兵判定，只看错误是否
  不再出现在尾窗。
- **门禁**：同上（`web-ssr-errors-page` 新增外壳/分组/已恢复 3 例、`web-alert-dock` 新增噪声/历史 2 例、
  `server-api-error-registry` 新增撤销/账本 4 例）；四份 bundle 过（`errors.js` 仍独立）。

## §2026-10-19-goalnn-a1-blocked-replay-v3 — A1 被 God-AI score gate 拒绝（回退）；回放帧先升 v3 保住 hold/fireBeforeTurn

- **现场**：`plan/nn-capability-upgrade.plan.md` 的 A1 要修“子弹方向 ≠ 开火时刻已提交朝向”。
  按 L1 实现两版：① `tryFire` 内先 `applyTurnCooldown`，被拒仍按旧朝向发射；
  ② 玩家路径先提交转身，被拒则本拍不发射。两版都让 God-AI 的转身同拍开火失效。
- **证据**：`tests/godai-score-gate.test.ts`（v7，classic/hard/chaos，20 seed/关）：
  ① classic 10 关低于地板（S7/S13/S19/S22/S23/S27/S28/S31/S32/S34，均值 0.65–0.82，
  truth 0.85–0.97）；② 仍 7 关低于地板（S10/S13/S21/S22/S28/S32/S34，均值 0.66–0.90）。
  这不是随机噪声，是“侧射/转身同拍开火”本身在 God-AI 战术里的贡献。
- **决策**：
  1. **A1 回退，不签入**；God-AI golden 保持原样。不得用延长冷却、改掩码、给 AI 加补偿来“修绿”。
  2. A1 若要重启，必须单独立项回答“God-AI 的转身同拍开火是否可放弃”；
     若只服务 NN，则用显式能力开关（默认关）而不是改全局引擎语义，且开关要进快照/回放契约。
  3. **P0-1 独立先做**：回放帧升 v3（单字节重排，`bits 0–2` 方向、`bit3` 开火、`bit6` hold、
     `bit7` fireBeforeTurn）；单流只要含任一 v3 位就写 v3，v1/v2 继续兼容读。
     这样 B′ 不会在“录制侧写不进两位 / 读回恒 false”上静默失败。
- **备选与否决**：① 直接接受 A1 的分数下降并下调地板 —— 否（地板是 God-AI v7 的 truth 派生，
  下调等于把回归写成通过）；② 只对敌人开 A1 —— 否（玩家侧才是 NN 要修的那一侧）；
  ③ v3 双字节/tick —— 否（方向只需 3 bit，白扔的 bit 正好装两位，体积不变）。
- **后果**：当前 HEAD 无 A1，`bun run check` 的 score gate 三难度全绿；B′ 仍被 A1 阻塞，
  但回放 v3 已就绪。旧 v1/v2 `.replay` 可继续导入/播放；新录单流若带 NN 两位则必须是 v3。
- **门禁**：`bun run check`（2554 tests；含 replay v1/v2/v3 round-trip、单流 v3、旧 v2 解码）；
  `bun run build` 待跑。

## §2026-10-19-goalnn-nn-only-hold-fire3 — A1 改为 NN-only 输入能力位：God-AI 保留侧射，B′ 拿得到“开火时刻朝向权威”

- **背景**：上一条 A1 被 God-AI score gate 拒绝，但 B′ 的 `fire=1/2` 与 `hold` 需要
  “先打再转 / 先转再打”的确定性。全局改 `tryFire` 会连 God-AI 一起改，已证明不可接受。
- **决策**：把差异收进输入契约，而不是引擎全局语义：
  1. `InputLike.usesHoldFire3?(): boolean`，**默认 false**；`Input` / `GodAIInput` 不实现
     ⇒ 旧路径逐字节不变，golden 不需要新纪元。
  2. `SimulationPlayer` 的 v3 分支：`hold=1` 只转向不前进，并显式调
     `applyTurnCooldown`（零速车会跳过 step⑦，不显式裁决就是免费瞄准）；
     `fireBeforeTurn=true` 先按旧朝向开火再转身；默认先转身，**转身被冷却拒绝则本拍不发射**。
  3. `applyTurnCooldown` 改为 public 且返回是否接受，供 B1/回放复用；旧调用点忽略返回值。
  4. v3 `ReplayInput` 自动返回 true（v1/v2 回放仍走旧路径）；`NNInput` 在 B1 接线时返回 true。
- **备选与否决**：① 全局 A1 + 接受 God-AI 分数下降 —— 否（已实测 7–10 关跌破地板）；
  ② 另加 `world.rules` 开关 —— 否（规则开关会进快照/回放契约，输入能力位更小且天然只影响
  声明它的输入源）；③ 只在训练脚本里模拟 —— 否（回放/评估会与训练分裂）。
- **后果**：God-AI 的侧射保留；NN 腿获得与 B1 一致的动作语义；`fire=1` 的 BC 样本仍≈0
  （人类输入不声明 v3），符合 plan §11.1 的预期。
- **门禁**：`tests/hold-fire3.test.ts` 4 例 · `bun run check` 2547 pass / 0 fail ·
  `bun run build` 过 · God-AI score gate 三难度全绿 · `freeze:check` 仍
  `FROZEN-SIGNATURE OK`（God-AI 无行为变化，golden 不动）。

## §2026-10-19-goalnn-b1-v6-head-switch — B1 动作空间三头以 `hold_head` 权重键切换，默认仍 5/2（拒绝半迁移）

- **背景**：B′ 的 B1 要把 `move(5)+fire(2)` 换成 `hold(2)+move(4)+fire(3)`。整条训练链
  （PPO rollout/update、BC dataset、shard `actions/masks`、`export-*` 工具、语料身份）都按
  7 位 mask 与 (move,fire) 布局写读；一次全量迁移必须与语料重导同批完成。
- **决策**：采用**运行时按权重自述切换**，而不是把全局常量直接翻成 4/3：
  1. TS `ModelLike` 以 `hold_head.*` 存在 ⇒ `usesHoldFire3=true`（`StudentModel` 载入时判）；
     `NNInput` 走 v6 分支，`computeMasks(world, v6=true)` 返回 move4/fire3（fire 两槽共用
     cooldown 闸，与 plan §4-B1 的「只有 1 位信息」一致）。
  2. Python `common.schema` 追加 `HOLD_DIM/MOVE4_DIM/FIRE3_DIM/MASK_V6_DIM` 与
     `MOVE_LABEL_SEMANTICS_V6`；`StudentNet(action_v6=True)` 才建 `hold_head`，arch 写
     `actionV6`；`model_build` 的 logit 归一与 `mirror_x` 的 4 路左右翻转同步覆盖。
  3. 仍是 5/2 的默认路径**逐字节不变**（旧权重、旧 shard、God-AI 回放全部照常）。
- **为何不做「一次翻完」**：PPO update 按 `mask[:, :MOVE_DIM]` 切 7 位、dataset 按同一布局切
  两段、四个 export 工具各自 argmax 2 头——只翻 schema 会让这些点**静默读错位**（比红更糟）。
  故本刀只交付契约+运行时；下一步要么整链迁移+重导语料，要么保持 v6 不训练。
- **备选与否决**：① 直接全局 4/3 —— 否（半迁移静默错配）；② 每个 shard 自带布局字节 ——
  否（同一份 mask 会有两套读者，正是 C5 要防的双事实源）；③ v6 模型静默回落 5/2 ——
  否（PPO 解 3 元组会当场 ValueError，这是有意的响亮失败）。
- **后果**：旧模型继续可服务；v6 权重可被 TS 部署与 Python BC/推理加载，但**尚未有 v6 语料**。
  `export-*` 工具遇到 v6 权重仍按旧布局读——在生成 v6 语料前不得把它们指向 v6 权重。
- **门禁**：`bun run check` 2557 pass / 0 fail · `bun run build` 过 · `freeze:check` FROZEN-SIGNATURE OK ·
  torch↔TS `student-golden-v6.json` 四头对账 ≤1e-4 · Python `test_action_head_hygiene` /
  `test_student_model` / `test_dataset_mirror` 全绿。

## §2026-10-19-goalnn-obs-v6-b2b5 — B′ 观测/遥测一次 bump 到 v6（SCALAR 46 / EXTRA 57 / MAJOR 6 / metrics v12）：能力位进观测，口径只留一个

- **背景**：plan/nn-capability-upgrade §4-B2–B5 要在同一次 schema bump 里交付 prevAction /
  lastHit / 敌朝向 / leadHit / 威胁拆族 / 窗口三列——任何一项单独做都要付一遍全语料重导。
- **决策**：
  1. **prevAction 由 `SimulationPlayer` 写入 `World.player`**（请求值 = 冷却前、地形前的输入），
     不是写进 `NNInput`：回放/BC 重导时没有 `NNInput`，只写在那里会让 BC 与 rollout 分布分家。
  2. **lastHit* 由 `CombatSystem` 写入**（真实扣血 + 星盾消耗两处），护盾 bounce 不写；
     方向 = 弹的**飞行方向**（不取反）；同拍多源取**最大伤害发**（不是最后一发）。
  3. **威胁拆族用「加列」不「改列」**：`0–3` 保持「只含敌弹」的旧语义，敌车炮口进 `37–40`；
     包夹度 `8` 改读两族合计（粗读数语义不变）。护盾期两族置零，冰冻期**不置零**。
  4. **窗口只留一个谓词**：`windowKind()` 基于粘性 `ShieldWindow` 分类（freeze / propShield /
     respawnShield / starShieldGrace），B5 三列与 idx47/idx51 豁免列都从它派生（交集，非第三套口径）。
     `deathDerivedShieldTicks` 只判读、不进奖励/分母——复活盾是「死了才拿到的无敌」。
  5. **lastHit* 不进 `tickHash`**（纯观测，不参与回放对账），改由快照往返全字段断言覆盖。
- **备选与否决**：① prevAction 写 `NNInput` —— 否（BC 缺该对象，分布分家）；② 直接把 `0–3`
  改成「敌弹+敌车」合计 —— 否（旧读数语义被改写，历史课程不可比且无法用差列还原）；
  ③ 另写一套 B5 窗口判定 —— 否（C5 要防的双事实源）；④ lastHitDir 存「攻击者相对方位」——
  否（与弹方向互为取反，取反那一层不可查）。
- **后果**：旧权重/旧 shard/旧 eval 报告在 MAJOR 6 下响亮拒绝（须重灌语料）；`metrics.npy`
  行宽 76→79、eval 行新增三键（旧报告缺键 = 省略）；腿 B 起点迁移的 extra 槽位随 SCALAR_DIM 移动。
  B6（窗口命中奖励）仍是数据级改动，本批未接公式。
- **全文**：`docs/nn/engineering.md` §78。
- **门禁**：`bun run check` 2577 pass / 0 fail · `bun run build` 过 · `freeze:check` OK ·
  `nn-training` pytest 全量绿（含 1926 条 worker/trainer）。

## §2026-10-19-goalnn-post-thaw-sentinel — B4 哨兵 `postThawDamageRate` 的口径：解冻帧起 60 拍、重新冻结即关窗、分母用 `player_damage`

- **背景**：B4 让冰冻期不再置零威胁（信息不删），需要一条「解冻即挨打」的读数防止把 D2 修成 D2'
  （plan/nn-capability-upgrade §4-B4 / 评审 Bf9）。计划只给了名字与「解冻后 60 拍内 player_damage 份额」，
  没定三件事：窗口起点、再冻结怎么算、分母用什么。
- **决策**：
  1. **解冻帧** = `freezeTimer` 由 >0 变 ≤0 的那一拍（`Simulation` 在拍首递减）；
     窗口 `[thawFrame, thawFrame + 60)`（1 s，左闭右开）——同帧伤害算「解冻后」。
  2. **重新冻结 ⇒ 窗关**：冻结期的伤害不是「解冻即挨打」，留在窗口里会污染这个哨兵。
  3. **分母 = 整局 `player_damage` 之和**（与 `playerDamageTaken` 同源），零承伤 ⇒ 0（不产生 NaN）。
  4. 只产出读数（`_eval_report.json` 的 `postThawDamage` / `postThawDamageRate`），**不做门判定**；
     门基线 = 新 schema null 腿同期值（本机无该基线）。
- **备选与否决**：① 窗口从「下一拍」起算 —— 否（解冻当拍开火已是真实威胁，丢掉最危险的一帧）；
  ② 再冻结不清窗 —— 否（等于把冻结期挨打算成解冻代价）；③ 分母用 `player_hit` 次数 ——
  否（星盾/致死路径不发 `player_damage`，份额会在「被秒杀」的对局里失真）；④ 用 `player_hits`
  为分子 —— 否（计划写死 player_damage，且次数丢伤害权重）。
- **后果**：eval 报告多两个键（旧报告缺键 = 无读数）；metrics 列不变（79 列），无需重灌语料。
- **全文**：`docs/nn/engineering.md` §78。
- **门禁**：`bun run check` 2577 pass / 0 fail · build 过 · `freeze:check` OK。

## §2026-10-19-goalnn-a1-nn-only-semantics-and-god-side-shot-residual — A1 只修 NN 侧：转身被拒即不发弹；God-AI 侧射登记为永久已知残留

- **背景**：落地评审（`reports/nn-capability-review-20261019.md` §3 D2/D3）发现两处「后人按 plan 字面理解会与代码对不上」的坑：
  ① plan §3-A1 验收 case 3 写「冷却未满 ⇒ 子弹朝**旧方向**且该拍不移动」，而 NN-only 落点的**最终设计**
  （plan §3「A1 的 NN-only 落点」末行）与实现（`src/game/SimulationPlayer.ts:88-101`）都是
  **转身被拒 ⇒ 本拍不发弹**——plan 自身两处字面冲突，代码取了后者。
  ② plan 原意是「修引擎 bug（侧射）」，但 A1 被 God-AI score gate 拒绝后改走 NN-only
  ⇒ God-AI 的侧射**永久保留**；该认识此前只散落在证据里，未登记为已知残留。
- **决策**：
  1. **A1 的权威语义 = 开火时刻的已提交朝向，且「请求转向但冷却拒绝 ⇒ 本拍不发弹」**
     （**不是**「沿旧朝向出弹」）。理由：请求了转向却没转成，此时开火等于打没瞄的方向；
     「沿旧朝向出弹」已由 `fire=1` 单独提供（`fireBeforeTurn`）。
     ⇒ plan §7 / A1 验收 case 3 的字面（「子弹朝旧方向」）**作废**，以本节为准。
  2. **God-AI 侧射 = 永久已知残留**：`Input`（人类）与 `GodAIInput` 都不实现 `usesHoldFire3`
     ⇒ 永远走旧引擎路径，保留「转身同拍开火（子弹方向 = 本拍请求方向，拍末朝向可能尚未翻转）」。
     **不得在后续任何改动里顺手『统一』它**（那正是两次让 God-AI 跌破地板的路）；
     若要重开必须**单独立项**（回答「God-AI 的转身同拍开火是否可放弃」）。
- **迁移对照记账**：NN v6 路径的 `shots/千` 会因「转身被拒不发弹」比 stack2 略低
  （12 拍冷却 vs 10 拍决策窗，拒绝不罕见）。这是**新架构相对 stack2 的额外一层行为差**，
  B1 通电后首腿必须**先对 stack2 用 `shots/千` 校准这条差**，不要算到 hold / leadHit 头上。
- **证据（God-AI 跌幅，供后人重开时免再摸底）**：`tests/godai-score-gate.test.ts`（v7，classic，20 seed/关）——
  · 旧朝向 L1（`tryFire` 内先裁决、被拒仍按旧朝向发射）：S7/S13/S19/S22/S23/S27/S28/S31/S32/S34 共 **10 关**跌破，
    均值 0.65–0.82（truth 0.85–0.97）；
  · 跳过发射 L1'（先提交转身、被拒则本拍不发射）：S10/S13/S21/S22/S28/S32/S34 共 **7 关**跌破，均值 0.66–0.90。
- **备选与否决**：① 沿旧朝向出弹（旧 A1 case 3）—— 否（与「开火时刻朝向权威」自相矛盾，且 `fire=1` 已覆盖该语义）；
  ② 把 God-AI 侧射也修掉 —— 否（已实测跌破地板）；③ 让 plan 字面胜出而不改代码 —— 否（验收用例会被写成必然红）。
- **落点**：`src/game/SimulationPlayer.ts:76-103`（v3 分支）· `src/game/Input.ts:22-34`（能力位默认 false/undefined）。
- **门禁**：本次为**文档登记**，无代码行为改动；`bun run check` 绿不变。

## §2026-10-19-goalnn-a1-global-fire-facing-and-god-gates-retired — A1 全局落地（子弹方向 == 开火时刻的已提交朝向，人类/敌人/God 同规）；God-AI 表现门禁退役

- **用户指令（2026-10-19）**：「这是游戏底层的基础机制，要统一！修！修完再看 god ai 在 hard 难度下 35stages×20seeds 的表现变化。」
  → 测完：「god ai 原来的高胜率，是靠作弊实现的。既然现在胜率下降那么多，对于它的门禁就没有意义了，都禁用掉吧。」
- **决策**：
  1. **A1 改为全局引擎规则**（不再只服务 NN）：`SimulationCombat.tryFire()` 在 `passesFireGate` 之后、
     取 `DIR_VECTORS[tank.dir]` **之前**调 `applyTurnCooldown(tank, w)`
     ⇒ **开火时刻的已提交朝向成为权威**。被冷却拒绝的转身先回退 `dir`、再出膛 ⇒ 子弹**沿旧朝向**飞
     （不再侧射，也不再"被拒不发弹"）。人类/敌人/God/护卫/诱饵**一律同规**。
     幂等：`dir === prevMoveDir` 时 `applyTurnCooldown` 早退 ⇒ step⑦ 那次调用变 no-op ⇒ **移动行为不变**。
     NN 的 v3 路径（`usesHoldFire3`）不受影响：其内部已显式裁决，`tryFire` 的二次调用是 no-op；
     `hold` / `fire=1`（先打再走）/ `fire=2` 语义照旧（见上一节 + `tests/hold-fire3.test.ts`）。
  2. **God-AI 表现门禁退役**：`tests/godai-score-gate.test.ts`（per-stage/aggregate score 地板）`test.skip`。
     理由 = 旧 truth 是在**侧射作弊行为**上采集的（§233/§276/§293/§304 历次重钉），不再是有效的回归基线。
     **harness（`score-gate-core.ts` / `score-gate-worker.ts`）与 `tools/diag/recapture-score-truth.ts` 保留**，
     重开需先有经刻意标定的 God-AI 基线。
  3. **`freeze:check` 保持启用**（它是**确定性**门，AGENTS §2.3 —— 不是表现门）⇒ 按新纪元三件套
     **更新 golden**（`tools/det-golden.v1.sha256`：`c2c25cdb…` → `0ff1ba66…`，21 组合签名全变）。
- **实测（hard，35 stages × 20 seeds，改前/改后，CRN 同种子）**：
  | | score(arithmetic) | SUITE | 胜率 | 最弱关 |
  |---|---|---|---|---|
  | 改前 | **0.6473** | 0.5602 | **78%** | Battlement 0.284 / 20% |
  | 改后 | **0.4938** | 0.4189 | **59%** | Riverbed 0.212 / 10% |
  ⇒ God-AI 的"高胜率"里有一大块是侧射提供的（**−19pp**）。**不在本刀顺手补偿**（God-AI 调参 = 另一个立项）。
- **新纪元 60-seed 三难度基线（eval-suite v7，A1 后）**：hard SUITE **0.4123** / win **58%** ·
  classic **0.6421** / **83%** · chaos **0.3786** / **53%**。
- **连带退役/重标（A1 改变了敌方行为 ⇒ 旧断言/金标不再成立）**：
  · `tests/godai-travel-fire.test.ts` §218 两条 —— 钉的正是被移除的侧射契约（`it.skip`）；
  · `tests/midlane-sticky.test.ts` §164 `sticky=90 seed10 clears`（`it.skip`）；
  · `tests/navbreak-carve-dig.test.ts` §162 `navBreakStuck=0 clears`（`it.skip`）；
  · `tests/sim/metrics-v11-hotlane.test.ts` 三条遥测金标 —— **重标数值、判据结构不变**（新纪元换局）。
- **supersede**：`§2026-10-19-goalnn-nn-only-hold-fire3` 里「God-AI 的侧射保留」**作废**（本刀全局修了）；
  `§2026-10-19-goalnn-a1-blocked-replay-v3` 的「A1 回退」**由本条取代**；
  上一节「God-AI 侧射 = 永久已知残留」**作废**。
- **修复复现用例**：`tests/fire-facing.test.ts`（legacy 路径：冷却内请求新方向 + 开火 ⇒ 子弹沿**旧**朝向；
  冷却已满 ⇒ 沿**新**朝向）—— 改动前红、改动后绿（AGENTS §7）。
- **落点**：`src/game/SimulationCombat.ts:282-292`（`tryFire` 内新增一次 `applyTurnCooldown`）。
- **门禁**：`bun run check` **2597 pass / 8 skip / 0 fail** · `freeze:check` 用新 golden
  `FROZEN-SIGNATURE OK` · `tests/hold-fire3.test.ts` 4 例 · `tests/fire-facing.test.ts` 2 例。

## §2026-10-19-goalnn-student-scalardim-from-weights — 融合宽度按 `fc.weight` 真实行宽反推（schema bump 改不了生产权重 ⇒ 不许硬取 `SCALAR_DIM`）；另：B′ 吞吐增量实测 0.19%

- **事故**：B′（`plan/nn-capability-upgrade.plan.md` §4-B2）把 `SCALAR_DIM` **40 → 46**，
  **fixture 跟着改了行宽**（`student-golden.json` 是 h=16 ⇒ `fc.weight` `[128, 62]` = 128×(16+46)），
  但**生产权重是训练产出的，改不了** —— `y25-price` 系列 `arch.scalar_dim=40`、`fc.weight` `[128, 104]`。
  而 `StudentModel.scalarDim` 是**硬取 `SCALAR_DIM` 的类字段**（`src/nn/infer.ts:317`）
  ⇒ forward 按 `fusedLen = h + 46 = 110` 步进读一张 104 宽的表：
  · **从第 0 行起就错位**（不是"尾部越界"——每行读到的都是下一行的前 6 个数）；
  · o ≥ 121 的行直接越界 ⇒ `undefined` ⇒ NaN ⇒ 被 **`RELU = x > 0 ? x : 0`**（`infer.ts:57`）
    **静默吞成 0**：**不崩、不 NaN、输出有限但完全错误**（实测 `hidden` 最大偏差 **7481**，
    与按 104 行宽的参照实现对比）。
  ⇒ B′ 落地后（10-19 白天）起，**所有 v5 权重（含基准权重 `y25-price.it95`）在 TS 侧推理输出的都是垃圾**，
  且没有任何一处报错。
- **决策**：`StudentModel.scalarDim` **不再硬取常量**，改由 `fc.weight` 的真实行宽反推
  （`行宽 = fcW.length / headHidden`，`scalarDim = 行宽 − h`），并加三步**响亮拒绝**：
  ① 行宽非整数或 ≤ h ⇒ throw；② `arch.scalar_dim`（若存在）与反推值不符 ⇒ throw；
  ③ 反推值 > `SCALAR_DIM` ⇒ throw（编码器喂不满）。
  ⇒ 旧权重按 40 维融合、**只读 `scalars[0..39]`**（B′ 新增 6 位被正确忽略），新权重按 46 维，
  **两侧都自洽，且不需要"权重迁移"**。
- **为何不是别的做法**：① 就地迁移旧权重 → 4.2 万个 ckpt 文件，且要重跑所有历史对账，不划算；
  ② 让 TS 拒绝加载 v5 权重 → 直接断掉"基准权重"这条对照线，B′ 的迁移起点就没了；
  ③ 加一个 `arch.scalar_dim` 分支 → 多一个事实源（权重形状本身已经是权威）。
  ⇒ 反推行宽 = **单一事实源**，且对"权重与声明不符"这类问题天然能抓到。
- **落点**：`src/nn/infer.ts:317`（字段改可变）+ `:436-462`（反推 + 三重校验）。
- **回归用例**：`tests/nn/student-scalar-compat.test.ts`（6 例）。核心断言手法（不需要参照实现）：
  **行宽 = h+40 的权重，改 `scalars[40..45]` 输出必须逐字节不变**；行宽 = h+46 的权重则必须变化
  （防"一律截断到 40"的反向 bug）。
- **附：B′ 全链路耗时 A/B（plan §6 Bf10/S17，评审担忧不成立）** —— `y25-price.it95`、
  ladder-c20-lives1 4 关 × 2 seed = 8 局 / 5654 决策点：`computePolicyExtra`（EXTRA 57，
  含 leadHit 4 向射线 + threat 分族）**3.1 µs/决策**、`encoder.encode`（含 SCALAR 46 写入）
  22.2 µs ⇒ **吞吐下降 0.19%** ≪ 10% 阈值 ⇒ **B′ 一次做完，不拆批**。
  ⚠ 同时修了 `tools/sim/spatial-probe-bench.ts` 的口径 bug：它无条件跑 `spatialTowerForward`
  并计进"新增" ⇒ 对未开塔的权重虚报 **+16.9%**（塔是 v4 腿 B 存量，不是 B′ 新增）。
  现在只在 `model.spatialTower === true` 时才测，并直出 `verdict` 字段。
- **门禁**：`bun run check` **2612 pass / 8 skip / 0 fail**（2597 基线 + 本次新增/修复 15 条）。

## §2026-10-20-dashboard-open-course-not-live-gated — openCourse 移出「无活体」动作闸：它是活体的创造者，不是消费者

- **报障**（用户 2026-10-20）：控制台开课 `y26-shotmix` 报
  `❌ 无活体：y26-shotmix 只有备份数据（tmp 已无该课），不能执行 openCourse`。
- **根因**：`dashboard/src/server/api/route.ts` 的 `COURSE_MUTATING_ACTIONS`（2026-10-18
  课程读数备份那一刀引入的 409 闸）把 `openCourse` 也列了进去。可 `openCourse`
  （`actions/course-lifecycle.ts`）恰恰是**唯一会建出活体工作区**的动作（`tmp/<课>/` 的
  账本 + `remote-jobs/` + 开课标记），不是活体的消费者。于是 tmp 被清理过、只剩备份数据的课
  **死锁**：越需要开课，越被「没有活体」拒绝；而 UI 侧（课程 select / 管理页 / 开课弹窗）
  按设计仍把这门课显示出来（备份课可见可读、可切视图），按钮点了必然 409。
- **决定**：把 `openCourse` 从 `COURSE_MUTATING_ACTIONS` 移除。它的前置检查本来就在动作内部
  （`assertCourseExists` 课程文件存在性 + 锁身份 + 意图文件健康），闸不代劳。plan
  `course-metrics-persistence` §0/§8 明确支持同名课重开（读侧 live 优先、显示当次 run，旧数据
  留在备份库）——闸与它矛盾的是代码，不是 plan。
- **备选与否决**：① 保留闸、要求先 `course-metrics restore` 再开课 —— 否（把一步能做完的事
  拆成两步人工仪式，且「恢复备份」会复活旧 run 的账本与当次 run 混写，比 live 优先更脏）；
  ② 让 `openCourse` 在闸里按「tmp 目录是否存在」判 —— 否（tmp 不存在正是它要处理的正常态，
  等于没闸）；③ UI 对备份课不画开课键 —— 否（治标：API 直调仍 409，且与「重开」语义相悖）。
- **回归用例**：`dashboard/tests/course-lifecycle.test.ts` ⑥（造旧 run 镜像 → 删 tmp →
  `routeAction('openCourse')` 必须 200 且活体工作区被建回；改动前红 = 409、改动后绿）
  ＋ `dashboard/tests/course-metrics-read.test.ts` 动作闸用例补 `actionRequiresLive('openCourse')
  === false` 哨兵（挡「有人再把它加回集合」这一类复发）。
- **落点**：`dashboard/src/server/api/route.ts:154`（集合）＋ `:149-162`（注释写明为什么）。
- **门禁**：dashboard typecheck + **1754 用例**（+1 新用例）· `bun run check` **2625 pass / 8 skip /
  0 fail** · `bun run build` 过。

## §2026-10-20-course-select-ghost-run-dirs — 课程 select 的 `*.run` 幽灵课：名字白名单写进实现，历史残留进隔离区

- **报障**（用户 2026-10-20）：左下角课程 select 里冒出一串 `ab-v6-*.jsonl.run` /
  `v6-*.jsonl.run` 和 `repro-course` 之类的杂项。
- **根因（三段链路各漏一道）**：
  ① `course-metrics/meta.ts::isCourseName` 的注释明写「`*.run/**` 也有临时账本，它们不是课程」，
     实现却只排除了 `pytest-tmp` / `backups` / `deliver` / `_` 前缀——**漏了 `.run` 后缀**；
  ② `sourceCourses` 的判据是「目录里四个备份文件命中任意一个」⇒ `tmp/<name>.jsonl.run/`
     带着 `eval_log.jsonl` / `dist-agent-meta.jsonl` 被当成课程转存进库；
  ③ `api/courses.ts::backupCourses` 只用 `hasHotMirror`（存在 `mirror-meta.json`）收课，
     **没有过名字白名单** ⇒ 库里一旦有脏目录，`discoverCourses` 就把它们当备份课捞回 select；
     `sanitizeViewCourse` 也只做路径字符校验，`?course=<x>.run` 同样放行。
  `repro-course` 则是人工 repro 时在 `tmp/` 下建的临时课目录（有 `training_log`），被 ② 转存后
  经 ③ 常驻——名字本身合法，只能人工隔离。
- **决定**：① `isCourseName` 增加 `.run` 后缀拒绝（扫描面 / 库内列表 / 冷存 / 课程发现同一口径）；
  ② `backupCourses` 与 `coldCourses` 补 `isCourseName` 过滤（历史脏目录不等清库就不可见）；
  ③ `sanitizeViewCourse` 同口径（`?course=` 不能绕过 select 的白名单）；
  ④ 既有 13 个脏目录移到 `dashboard/data/course-metrics/_quarantine-20261020/`（`_` 前缀被白名单
     挡住；**不删除**，保留 A/B 评估日志的可回溯性）。
- **备选与否决**：① 只清库不改代码 —— 否（下一次 A/B 的 `.run` 目录又会转存回来）；
  ② 只过滤 select、不过滤 mirror 写侧 —— 否（库继续膨胀、`backup` 的 tar 变脏、冷存也会打包幽灵课）；
  ③ 把「有 `training_log.jsonl`」当唯一判据 —— 否（`repro-course` 也有，治不了这一类；
  且历史备份可能只剩 eval 账本，会把真课误伤）。名字白名单是第一道，锚点判据不重复加。
- **回归用例**：`dashboard/tests/course-metrics-mirror.test.ts`（`*.run/**` 的 eval 尾账不进库）
  ＋ `dashboard/tests/course-metrics-read.test.ts`（库内历史残留 `*.run` / `_*` 不进
  `discoverCourses`、`sanitizeViewCourse` 返回空；无 curricula 的正常备份课仍被发现）。
  两条均先红后绿。
- **落点**：`dashboard/src/server/course-metrics/meta.ts:73`（后缀判据）·
  `dashboard/src/server/api/courses.ts:20-31 / 47-55`（发现与 `?course=` 白名单）·
  `dashboard/src/server/course-metrics/archive.ts:115`（冷存列表同口径）。
- **门禁**：dashboard typecheck + **1756 用例**（+2）· `bun run check` **2625 pass / 8 skip / 0 fail** ·
  `bun run build` 过。

## §2026-10-20-rollout-node-codehash-training-side-crlf — 节点门「版本指纹对不上」的真凶在训练机侧（集内文件 CRLF 污染）

- **报障**（用户 2026-10-20）：rollout 集群的 mac 已更新到 git commit `ddbda71`，仍显示版本指纹
  对不上、不参与 rollout/eval（训练机日志逐轮 `node mac: codeHash mismatch (local=1095afee
  remote=e64efa38) — remote pull cannot converge (uncommitted training-tree changes), restart
  suppressed (dirty-tree:1) — excluded this round`）。
- **根因（差的是训练机，不是节点）**：codeHash = **集内文件的工作区原始字节**哈希
  （`tools/agent/codehash-files.txt` 展开，TS/Python 双侧同式）。训练机工作区里
  `src/game/Input.ts` 是 **CRLF**（386 CRLF / 386 LF），索引 blob 是 LF——`core.autocrlf=input`
  ＋ `.gitattributes` 的 `* text=auto eol=lf` 让 git 比较时先归一 ⇒ **`git status` 完全看不出**
  （这正是 2026-09-09「mac 事故」同款隐形污染）。⇒ 训练机期望值 `1095afee…`，而**任何干净
  checkout 都是 `e64efa38…`**（mac ping 上报的正是后者，逐字节核对一致）⇒ 节点永远红。
  更糟的一环：`dirty_hash_files()` 非空 ⇒ dispatch 的 dirty-tree 护栏**拒发**远端
  pull/restart（「remote pull cannot converge」）⇒ 连「让节点自己收敛」这条退路都被堵死。
- **判据（可复用，三步定位）**：① 先比「训练机期望 hash」与「节点 ping hash」，别先怀疑节点；
  ② `common.distribution.dirty_hash_files()` 非空 ⇒ **先查训练机**（远端 pull 永远不收敛）；
  ③ 把集内文件按 CRLF→LF 归一后重算 hash，若等于节点上报值 ⇒ 污染侧确定是训练机
  （本次即此路坐实：mac 全程正确，`agentVersion=ddbda71f` 与本地 HEAD 一致）。
- **决定**：① 集内文件的**工作区字节必须 == 索引字节**——训练机自检 `dirty_hash_files()` 非空时，
  不得给远端发升级/重启，先修训练机（否则一次「升级波」打空，还把护栏变成静默排除）；
  ② 修复动作 = **`rm` + `git checkout -- <file>`**，**禁用 `git checkout-index -f`**（实测它的
  stat 缓存短路，CRLF 文件压根不会被重写，写完仍是 CRLF）；③ 写盘后 `git update-index --refresh`
  / `--really-refresh` **修不好** stat 缓存（`git status` 仍报 M），只有 `git checkout --` 会
  （重写文件＋重新 stat，实测 45 个文件逐一验证）。
- **备选与否决**：① 重启 mac 的 agent／在 mac 上再 pull —— 否（mac 已干净；且 dirty-tree 护栏
  本就拒绝远端 pull，重启与 pull 都治不了训练机侧的期望值）；② 放宽节点门（忽略 codeHash／加
  容差／把指纹改成 commit 号）—— 否（门的存在正是为了不让旧代码节点混进同代语料，
  2026-09-15 x3-power it30「epoch 全员 mismatch → 600s 零局」就是反向教训；掺 commit 号更会让
  无关提交把全节点判 stale，见 §2026-09-17-goalnn-unified-node-gate）；③ 把训练机的期望 hash 当
  真理、逼节点对齐 —— 否（期望值就是从工作区算出来的，污染侧永远是训练机本身）。
- **附带处置**：全仓字节级体检（同 `dirty_hash_files` 判据）发现同一类潜伏污染 **45 个文件**
  （`nn-training/**`，均在 codehash 集外）⇒ 已按同法归一（语义零变化，只换行尾）；
  `DECISIONS.md` ＋ 5 个 dashboard 文件有**真实未提交改动**（并行会话在写）⇒ 未动。
- **落点**：`src/game/Input.ts`（训练机工作区）· 判据 `nn-training/common/distribution.py::
  compute_code_hash` / `dirty_hash_files` · 门 `nn-training/trainer/dispatch.py:203/238/263`。
- **结果**：训练机期望值 = 节点上报值 = `e64efa38…`；`dirty_hash_files()` = `[]`；
  11:27 起 mac 回场（rollout `byNode` 出现 `mac`，eval 出现 `node=mac`），a95/a97 一并恢复。
- **防复发（下一步）**：① 集内文件一旦被 Windows 侧工具写回，必须保持 LF（Windows 文本模式写
  文件默认 CRLF ⇒ 写集内文件前先确认工具的换行策略）；② 建议把「`dirty_hash_files()` 非空即红」
  做成 pre-commit / 开训前自检门（现在是运行时才 WARN，且表现为静默排除）。

## §2026-10-20-eol-guard-commit-time-lf-normalization — 「commit 时把文件改为 LF」= 把**工作树**拉回索引字节（不是让 git 提交时归一）

- **用户指令**（2026-10-20）：配置本机 commit 时自动把文件改为 LF，问是否能避免 mac 节点门事故。
- **先纠正前提**（实测）：**git 的提交时归一早就在做，且早就是对的**——`.gitattributes` 的
  `* text=auto eol=lf` ＋ 本机 `core.autocrlf=input` ⇒ 索引里永远是 LF（事故当天
  `src/game/Input.ts` 的索引 blob 就是 13641 字节 LF，而工作区是 14027 字节 CRLF）。
  实测 `eol=lf` **压过** `core.autocrlf=true`（临时仓库实验：checkout 写出 CR=0）⇒ git 本身
  不是污染源。**坏的是工作树**，而 codeHash 算的正是**工作区原始字节** ⇒ 「提交时归一」治不了
  它；能治它的是「**把工作树拉回索引字节**」。
- **决定**：① **本机 user 级 git 配置**：`core.autocrlf=input`（已有）+ `core.eol=lf`（新）+
  `core.safecrlf=warn`（新）——`safecrlf` 实测在 `git add` 时喊「CRLF will be replaced by LF」，
  是**早信号**；不用 `=true`（实测 `git add` 直接 fatal，会把提交卡死在看不懂的报错上）。
  ② **pre-commit 钩子新增一步「eol 归一」**（`tools/githook/normalize-lf.ts`）：commit 时把
  **codehash 集 + staged 文件**里**纯行尾污染**的工作树文件重写为**索引字节**（LF）。
  ③ 判据锁死（`classifyDrift`）：工作区字节按 CRLF→LF 归一后必须**逐字节等于索引 blob**
  ⇒ 只动行尾、不动内容 ⇒ 他人的在途编辑（真实内容差异）**一字不碰**、只报告。
  ④ 二进制（集内 `prebuilt/**` 的 .wasm/.dll/.dylib/.so）永不参与。
- **与钩子铁律的关系**：钩子原有铁律是「除 oxfmt 精确格式化外对工作树**零写操作**」——本次
  新增的写入面被**前置条件**（归一后 == 索引 blob）锁死，与 oxfmt 那条例外同性质（只动自己
  确认安全的内容），已在钩子头注释里显式改写铁律并说明理由。
- **为什么这能覆盖事故**：事故当天 09:42 污染、11:16 有一次提交（ddbda71）⇒ 有这道闸，
  集内文件会在那次提交时被拉回 LF，**mac 不会被判 stale**。闸放在 dirty 清单**之前**：
  归一后「仅因行尾而不一致」的文件不再算未暂存改动。
- **备选与否决**：① 只设 `core.autocrlf` / `core.safecrlf` 就收工 —— 否（索引早就是 LF，
  坏的是工作树，配置层动不到它）；② 提交时把**全部**跟踪文件重写成 LF —— 否（与 oxfmt 同样
  的风险面：会吃掉未暂存的真实编辑；本条只修「归一后 == 索引」的那一类）；
  ③ 在**训练侧**（dispatch）自动修复集内污染 —— 未实施（训练链路的写工作树行为变更，另议；
  运行时的 `dirty_hash_files()` WARN 仍在，且已被本条的前置闸大幅缩短窗口）。
- **落点**：`tools/githook/normalize-lf.ts`（新）· `tools/githook/pre-commit`（新增一步 ＋
  铁律改写 ＋ selftest 接线）· `tests/normalize-lf.test.ts`（新，9 例）· 本机 user 级 git 配置。
- **回归**：① 归一器 `--selftest`（分类三分支 ＋ 端到端修复 ＋「真实改动不动」）；
  ② 钩子 `--selftest`（含归一器自检）绿；③ `tests/normalize-lf.test.ts` 9 例（分类 ＋ 修复
  ＋ 覆盖面接线 ＋「钩子调它且在 dirty 清单之前」）；④ **事故级端到端**：把集内文件
  `src/utils/RNG.ts` 写成 CRLF ⇒ `dirty_hash_files` 报它、期望 codeHash 由 `e64efa38…` 变
  `f0f774df…` ⇒ 归一后工作树回到索引字节、dirty 清空、期望值复原 `e64efa38…`；真实内容差异
  用例确认一字未动。
- **门禁**：`bun run check` 绿（tsc ＋ check-decisions ＋ agents-budget ＋ 根套件）；
  `tools/githook/pre-commit --selftest` 绿。

## §2026-10-20-process-lifecycle-not-course-gated — 组件启停与课程正交：`stop` 闸的是 `stopCourse`，不是共享进程

- **报障**（用户 2026-10-20）：课程的 `tmp/<课>/` 被清理后，停止 trainer / hub 报
  `❌ 无活体：y26-dmg 只有备份数据（tmp 已无该课），不能执行 stop`。课程数据没了，
  进程却停不下来。
- **根因**：`COURSE_MUTATING_ACTIONS` 用「动作名像改状态」当判据，把**组件级** `stop`
  （`stopComponent(key, course)`）与**课程级** `stopCourse` 混为一谈；`start` / `preset` /
  `smoke` / `setMode` 也一起被误闸。trainer / hub / 隧道 / 本机 worker 都是
  `SHARED_COMPONENTS`（一个进程服务所有课程，账本槽恒 `''`），它们的启停根本不消费
  `ctx.course` 的工作区；课程 tmp 只是它们碰巧在用的数据。plan/course-metrics-persistence
  §3.3 的闸本来针对「起课/停课/导出/导入/评估」这些**课程级**动作，实现把同名的组件
  `stop` 一起收进来，与 `openCourse` 被误闸（§2026-10-20-dashboard-open-course-not-live-gated）
  是同一类错误：闸住了不该闸的。
- **决定**：`COURSE_MUTATING_ACTIONS` 只保留**真正读写 `tmp/<课>/`** 的动作：
  `smokeTrain` / `stopCourse` / `setCoursePaused` / `eval*` / `redoPpo` / `archiveCourse` /
  `exportTaskBundle` / `cloud-halt` / `cloud-resume` / `releaseCourseHold` / `unfreeze-job` /
  `autoOfflineHandoff`。移除 `start` / `stop` / `preset` / `smoke` / `setMode`——
  前四者是共享进程生命周期/探针，`setMode` 写的是全局 `rl.*`（平台配置），都不需要课程活体。
  注释里把判据从「动作名」改成「是否读写 `tmp/<课>/`」，并写明 plan 里的 `stop` 指
  `stopCourse`。
- **备选与否决**：① 只特判 trainer/hub 的 `stop` —— 否（`start` / `preset` / `smoke` 与
  其它共享组件会以同样方式复发；按动作类型豁免才是根因修复）；② 闸里按
  `isSharedComponent` 判断 —— 否（动作本身不带 component，且 `preset`/`setMode` 根本不是
  组件）；③ 把课程 tmp 目录自动重建/恢复 —— 否（停进程不该有数据副作用；课程重开是
  `openCourse` 的事）。
- **回归**：`dashboard/tests/course-metrics-read.test.ts` 钉住
  `actionRequiresLive({start,stop,preset,smoke,setMode}) === false`，并用仍需要活体的
  `redoPpo` 验证 409 闸仍在；`dashboard/tests/course-lifecycle.test.ts` ⑦ 端到端：
  备份课（tmp 已清）`routeAction('stop', {component:'trainingLoop'})` 返回 **200**，
  不再是 409。两条先红后绿（红时拿到的就是用户那条原文 409）。
- **门禁**：dashboard typecheck ＋ **1757 用例** · `bun run check` **2634 pass / 8 skip / 0 fail** ·
  `bun run build` 过。

## §2026-10-20-hub-crash-commit-limit-and-infra-auto-restart — hub「崩溃」实为提交限额击穿下的**被外部终止**；基础设施组件改为自动重启

- **报障**（用户 2026-10-20）：hub 进程在 ~14:40 崩溃了。
- **定位（证据链，五步坐实「谁杀的」）**：
  ① **死亡时刻**：看护器标记 `[console] 2026-10-20T06:44:32.071Z hub-server (作业中枢) 意外退出
     (PID 23232)`（UTC ⇒ 本地 **14:44:32** 发现）；hub 自己最后一条访问日志 **14:44:22**
     ⇒ 死于 14:44:22–32 之间。
  ② **不是自身异常退出**：日志尾**只有正常访问行**，无 traceback、无 SystemExit；hub 的
     stdout/stderr 合并进同一个日志文件（`run-python.ts`）⇒ 「没有输出」是真的没有。
  ③ **不是原生崩溃**：Windows Application 日志 14:30–15:00 **无** Application Error 事件。
  ④ **真凶**：Windows **System** 日志 Event 2004「low virtual memory condition」@ **14:44:24**
     （explorer.exe 14.5GB / Code.exe 7.1GB / vmmemWSL 1.7GB）⇒ **提交限额 37.9GB 被击穿**，
     hub 是**受害者**（它自己 RSS 只有 37MB，不在事件榜前三）。
  ⑤ **08:04 那次是另一回事**：当日 Event 2004 只有 14:44/14:49 两条；08:04 那次标记对应的
     hub 最后一条日志是**前一晚 23:31** ⇒ 整个控制台+hub 栈夜里就停了，hub 是跟栈一起没的。
     ⇒ 两起不同因，别混成一条故事。
- **影响与放大器**：训练主链**未断**（远端 PPO 走云端 hub `100.124.208.62:8787`，trainer
  全程在跑）；断的是本机 hub 的腿（控制台 admin 轮询、云机 worker 领活、在飞 job
  `95cab515f03877b8`(y26-null) 随**内存 job 表**一起消失 ⇒ 重启后查该 job = 404，trainer 补发
  unfreeze/cancel-job 也 404）。**真正的损失是放大器**：看护器**只记不重启**（文案就是
  「用控制台「启动」恢复」）⇒ hub 实停 **1 小时 29 分**（14:44:22 → 16:13:36 才恢复应答）。
- **决定**：① **共享基础设施组件意外退出 ⇒ 自动重启**（新 `dashboard/src/server/auto-restart.ts`）：
  只管 **hubServer / selfNode**；**trainingLoop 是课程状态**（门判决/ALL DONE 后悄悄续跑 =
  违背 §385 的判死判决）不自动续跑。**四道闸**（任一不满足 ⇒ 不重启，理由写进日志）：
  非设计内停车 / **宽限 12s 后账本条目仍在**（操作员手动 stop 会 `clearAnyComponent` ⇒
  条目消失 = 最可靠的「操作员要它停」判据，胜过用时间窗口猜意图）/ 组件仍不健康 /
  频次闸（10 分钟 ≤3 次，超闸只报警不动手）。启动复用动作路径（`stepHubServer`/`stepSelfNode`）
  ⇒ 账本/换代接管/端口回收语义一致。
  ② **死因文案点破形态**：`buildExitMarker` 在「有日志、但尾段无任何异常输出」时明说
  「更像**被外部终止**（系统内存耗尽 / 被杀 / 断电），而非自身异常退出；请对照系统事件日志
  确认」——原文案「非正常退出，请检查下列日志」会把操作员引到进程自己的日志（那里什么都没有）。
  ③ cloudflared / localWorker **暂不**纳入自动接管（启动路径各带隧道选项 / venv 解析）。
- **备选与否决**：① 只加内存/调页文件就收工 —— 否（那是缓解不是修复：hub 仍是「一死等人」，
  且页文件要重启才生效）；② 自动重启**所有**组件 —— 否（同上：课程状态不自动续跑）；
  ③ 宽限期内靠时间窗口猜操作员意图 —— 否（意图是动作，账本是动作的果）。
- **落点**：`dashboard/src/server/auto-restart.ts`（新）· `dashboard/src/server/exit-watchdog.ts`
  （接线 + 文案）· `dashboard/tests/auto-restart.test.ts`（新，14 例）。
- **门禁**：dashboard typecheck + **1771 用例**（+14）· `bun run check` 绿。
- **未做（机器层面，留给运维/用户）**：提交限额本身要抬——当前 commit limit **37.9GB**
  （RAM 29.6GB + 页文件约 8GB；页文件在 E: 且**当前 0 字节占用**、系统级自动管理关闭）。
  建议把 E: 的页文件设为定值 24–32GB（E: 尚余 103GB）⇒ 提交限额升到约 55–60GB；
  **改完需重启才生效**（本机在训练中，未擅自动）。另：explorer.exe 当时独占 14.5→17.6GB
  （经典泄漏，重启 Explorer 即可清）、VS Code 7.8GB。

## §2026-10-20-auto-restart-covers-all-managed-components — 自动重启扩到全部受管组件（trainingLoop / cloudflared 纳入）

- **用户指令**（2026-10-20）：「trainingLoop 和 cloudflared 也需要自动重启。」
- **口径修正（把判据从「哪些组件」换成「哪种退出」）**：本条**撤销**上一条里
  「trainingLoop 是课程状态 ⇒ 不自动重启」的取舍。真正的分界不是组件身份，而是**退出性质**：
  - **设计内停车**（`gate_verdict` / `circuit_break` / `ALL DONE`）⇒ **不重启**——那是判决，
    不是崩溃；悄悄续跑会推翻人的判死决定（§385）。看护器对 trainingLoop 已有该判据
    （`recentPlannedStop` / `tailNormalCompletion`），其余组件本就没有设计内停车语义。
  - **意外退出**（崩溃 / 被外部杀死）⇒ 重启。**全部受管组件**同此待遇：
    selfNode / hubServer / cloudflared / localWorker / trainingLoop。
  「操作员手动停过」仍由**账本清账**保护（stop 会 `clearAnyComponent` ⇒ 条目消失 ⇒ 不复活）。
- **启动动作复用操作员的「启动」路径**（`startComponent`）——不另写一套：单实例锁检查
  （trainer 的 `run_cluster` 锁；python 侧对死持有者按「陈旧锁接管」放行）、端口回收、
  **隧道 URL 回写**（cloudflared 每次起来 URL 都变，只有 `stepCloudflared` 会回写账本）、
  **trainer 重启后的云端停机态复位**（`markCloudHaltRecovered` ⇒ hub `/admin/workers/resume`
  ⇒ 崩溃时 §385 的停机指令在 12s 后自动解除，不留「停了不恢复」的死局）全在那条路径上；
  抄一份必然漂（抄 `restartProc` 就会漏掉 URL 回写与停机态复位）。
- **健康探针与看护器判死同源**：`restartSpecFor(key, '').healthy()`（看护器的 `classifyExit`
  用的是同一份）⇒ 不会出现「看护器说死了、自动重启说活着」的双口径。
- **瞬时失败有限重试**：busy 互斥 / 端口未释放 / 锁未清都是瞬时的 ⇒ 最多试
  `AUTO_RESTART_ATTEMPTS`（3）次、间隔 8s；每次重试**重过一遍闸**（期间被别人拉起 ⇒ 立即停手；
  账本被清 ⇒ 停手）。崩溃循环由频次闸兜底（10 分钟 ≤3 次，超闸只报警）。
- **与 §385 云端停机的关系**：崩溃时看护器仍发停机指令（省 GPU 配额，语义不变），但 trainer
  12s 后自动重启 ⇒ 停机态随之解除 ⇒ 训练自动续上；**设计内停车**则停机态保留（等人）。
- **落点**：`dashboard/src/server/auto-restart.ts`（重写）· `dashboard/tests/auto-restart.test.ts`
  （16 例）。
- **门禁**：dashboard typecheck + **1773 用例**（+2）· `bun run check` 绿。

## §2026-10-20-goalnn-y26-leg-a-policyextra — y26 线切腿 A：`policyExtra=true` 让 POLICY_EXTRA(57) 第一次真正进网络

- **背景**：`docs/nn/experiments.md §119` 查明 y26 六腿模型全是 `policyExtra=false` 档
  （`move_head` 宽 128 ⇒ `src/nn/infer.ts:510-515` 把 `extraDim` 定为 0、`forward` 第三参
  `extra` 全程不被读；Python `PPOStudent.policy_features` 同判据）。⇒ B2–B4 新增 26 个观测位里的
  **20 个（整个 extra 增量：leadHitD×4 / threat 分族 / enemyFacing / lastHitDir / prevAction 方向
  one-hot）写进每个 shard 却从未进入策略**。这不是“训练没学会”，是结构上不可能生效。
- **决定**：y26 线改走**腿 A**（`policyExtra=true`，头输入 185 = hidden 128 + extra 57）。
  **不**把那些位搬进 scalar。
- **否决「搬进 scalar」的理由**：`extra.npy` 已是 57 维且每个 shard 都有 ⇒ 切档**零重导**；
  搬进 scalar 要 `SCALAR_DIM 46→66` = `OBS_SCHEMA_MAJOR` 7 + obs-encoder/schema/指纹/全语料重导
  + demo/BC 语料作废，代价大一个量级，且与 `§2026-10-19-goalnn-obs-v5-critic-superset` 那条
  “extra 走独立张量”的分层决定相冲突。
- **起点怎么造**：`worker/scripts/init_spatial_leg.py` 新增族 **legacy-v6 → 腿 A v6**。
  判据只看张量形状（`_is_v6_source()`：move4/fire3/hold2 且 `hold_head.weight` 在），补列用
  **`drop0`（extra 57 列全 0）** 而不是 0.01 噪声，并**跳过 `_migrate_action_heads`**——
  后者是为 5/2 旧源写的：v6 源（4 行）会被它判非法 ⇒ move/fire 整头随机 init，且 `old_move_w[0]`
  （v6 的 up 行）会被当成 STOP 拿去重建 hold 头，三层静默错位。legacy **5/2** 源仍按原样拒绝。
- **等价性（为什么这不算“换了起点”）**：extra 补 0 ⇒ it0 与 `y26-v6.it0` 行为等价，实测
  ① torch 四头 max|Δ| ≤ 1e-4 且 argmax 相同（185 vs 128 宽累加次序不同，末位 ~2e-5，
  逐位相等做不到也不该要求）；② 真局 `export-eval-game --stage 7 --seed 860001` 两版
  **89 项读数除 `weightsSha` 外逐字相同**；③ `extra=0.5` 时 `move_head/hold_head.weight.grad[:,128:]`
  非零 ⇒ 57 维第一步就能学；④ `build_ppo(y26-v6x.it0)` ⇒ `policy_extra=True/policy_input_dim=185`、
  严格装载 `missing=[]`；rollout 实跑一局 ⇒ shard 带 `extra(114,57)`/`mask(114,9)`/`a_hold`。
- **不做承诺**：腿 A 只改“extra 能不能进网络”，**不动 Φ**。`§119` 的“一手普通弹定价 −0.10 分 /
  平均过关 −0.37 分”那个病仍在 ⇒ 新腿 `y27-clear` 的判据写成**同轮配对 vs `y26-clear`**
  （同 `paired_rotate_seed` ⇒ 同 stage/seed 流），**不用绝对 0.715**（那是 v5 系列的单点峰值，
  22 点均值 64.4 / sd 5.0）。
- **落点**：`nn-training/worker/scripts/init_spatial_leg.py`（新族 + 跳过行迁移）·
  `nn-training/weights/y26-v6x/y26-v6x.it0.json` · `nn-training/curricula/y27-clear.jsonc`（规格草稿，
  **开课 = 控制台点**）· `nn-training/tests/worker/test_init_spatial_leg.py`（新用例）·
  `tests/fixtures/student-golden-v6x.json` + `tests/nn/student-infer.test.ts`（腿 A + v6 的 TS↔torch 对账）。
- **门禁**：`bun run check` **2637 pass / 8 skip / 0 fail**；`bun run build` 过；
  `nn-py-safe pytest`（init_spatial_leg + student_model + shard_action_space_v6 + action_head_hygiene）
  **28 pass**。
- **后续（各自单独立项）**：迁移成本重修（BC 蒸馏替掉 `hold=4 方向均值 + bias 2.0`）；
  `metrics v13` 补 `holdTicks`/`fireBeforeTicks`；extra 消融读数（四头 `[:,128:]` 清零跑同面板）。

## §2026-10-20-goalnn-y27-triad — y27 四腿（clear + shot / lead / window）：按测出的 Φ 份额配剂量激活三张休眠能力

- **命名**：原 `y26-clear-x` 改名 **`y27-clear`**（架构腿：`policyExtra=true` 的基线；奖励 = `y26-clear` 逐字，
  故 `y27-clear` vs `y26-clear` 的配对差 = **纯架构效应**）。三条能力腿都以它为基线。
- **为什么是这三条腿**（`docs/nn/experiments.md §119` 的休眠能力清单）：
  `hold` 已活（22.5%）；`prevAction/leadHitD/enemyFacing` 是**输入**，只能靠“奖励让它们有用”来激活；
  `fire=1` 在竞技场 0/54 611、冰/盾期密度只有均匀度的 91%（人类 128%）。
  ⇒ 三条腿各对准一个**可测的行为读数**，而不是“再多跑一条”。
- **剂量全部按 it0 / anchor 200 局实测的 Φ（77.3）算，不拍脑袋**：

  | 腿 | 单变量 | ΔΦ | 占 Φ | 对准的读数 |
  |---|---|---|---|---|
  | `y27-shot` | `wHit 0.2 → 0.8` | +23.5 | **30.3%** | 出手/千；`a_fire==1` 占比 |
  | `y27-lead` | `wDist 0.6 → 6.0` | +18.4 | **23.7%** | A10 命中距离；D01 承伤 |
  | `y27-window` | formula + `wWinHit*hitsInFreezeShieldWindow`，`wWinHit 3.0` | +13.7 | **17.7%** | 窗内命中密度；win |

- **否决的剂量（都算过、都不过 15% 线）**：`wMiss 0.35→0.10` 只有 +3.6（4.6%）；
  `wAcc 15.3→6.0` 是 −6.6（8.5%）；`wWinHit 2.0` 只有 +9.2（11.9%）⇒ 取 3.0（＝“窗内一发 ≈ 一个击杀”）。
- **`y27-shot` 的功效换算**（这是本腿的立论）：一手普通弹的期望从
  `0.722×0.2 − 0.278×0.35 − 0.20(分母税) = **−0.10**` 变成 `0.722×0.8 − 0.278×0.35 − 0.20 = **+0.27**`
  ⇒ 梯度方向从“别打”翻成“打”，这是 `fire=1` 唯一可能被采样到并被付钱的路径。
- **`y27-lead` 是 y26-dist 的可证伪重开**：同一杠杆（wDist ×10）在 extra 死档上判负（A10 没动）；
  现在 `leadHitD/enemyFacing` 真进了网络 ⇒ 若这轮 A10 还是不动，就不是“缺特征”，而是**这条杠杆本身无效**。
- **三条腿的互相排斥与耦合**已写进各自头注：`shot` 会把 A01 压低，而 `lead` 用 A01 ≥0.75 守门
  ⇒ 两腿同时把 A01 压破线时**两条都判负**，不许互相“救”。
  `window` 的杀腿 ⑤（窗口拍涨 >30% = 追护盾刷窗口）专门抓 plan §4b 说的“窗口奖励 vs 道具低价”张力。
- **本轮明确不做的三件事**（用户 2026-10-20 拍板）：
  1. **不做 BC 蒸馏三头** —— 三头是 RL 可学的（`hold` 已证）；蒸馏原本的目标是**收回迁移丢的 11pp hitRate**
     （空射 +70%），不是“教能力”。不做 = 接受起点这笔亏，让 `y27-*` 四腿用配对差读出来（见下条 Q&A）。
     另：`demo 语料 fire 可训练子集里 hold 只占 1.84%`（`docs/nn/experiments.md §118`）⇒ 对 fire 头蒸馏本来就不靠谱。
  2. **不加 `holdTicks`/`fireBeforeTicks` 两列**（`metrics v13`） —— 分析时用脚本读 shard：
     `node tmp/agg-shards.cjs tmp/y27-shot/it<N>/dist`（本会话写的，任何 `dist/**` 都能跑）。
     代价：**能力使用率不在 `eval_log` 里**，只能事后抽查某个 it 的 shard（不是逐轮序列）。
  3. **暂不做 extra 消融** —— 见下条 Q&A 的后果说明；`y27-clear` 的结课报告要按该口径写清楚“能证什么、不能证什么”。
- **落点**：`nn-training/curricula/y27-{clear,shot,lead,window}.jsonc`（四条都过
  `load_course + validate_reward`，errors=0；`y27-window` 的 `hitsInFreezeShieldWindow` 已在
  `biz/reward_library.py:214` 的 METRICS idx77 里，B5 就是为 B6 落的这列）。
- **开工顺序**：人在控制台开 ×4（同起点 `y26-v6x.it0`、同 `paired_rotate_seed 20270206`）。
  `y27-clear` 必须先活着 —— 另外三条腿的判决全靠它的**同轮**读数。

## §2026-10-21-goalnn-y27-steady-pricing — y27 四腿之后不再守单变量：一门 `y27-steady` 同时修「死一局仍赚 / 开火恒负 / 挨打免费」并把三条休眠能力一次接线

- **背景**：`docs/nn/experiments.md §120` 证了机制在动（shot 出手 +17%、lead 采样 A10 +0.48、
  window 窗内密度 0.831→1.002），但四腿胜率全线下滑；用户 2026-10-21 明确**不要求单变量**，
  只要求「快速稳定提高胜率 + 新能力都生效且互相兼容 + 参数量级算实」。
- **三处病根**（60 局 `y27-clear` shard 实算）：负局 Φ **+24.34**（`lives_exhausted` 终局额 = 0）；
  单发期望 **−0.164**（P\* = **1.050** ⇒ 永不值得开火）；挨打 231 点只值 **6.9 Φ**。
- **结构判断（新读数）**：胜负局的**每局承伤相同**（231.7 vs 231.2），差别在速率
  （胜 30.98/千拍 vs 负 62.89/千拍，7478 vs 3676 拍）⇒ 承伤按事件定价、存活靠终局、速度只当胜局内次序。
- **决定**：新建 `nn-training/curricula/y27-steady.jsonc`（22 参数：12 重定价 + 2 新增 + 8 冻结）；
  `wWin` 改二元 48.0、`wSpeed 3.0` 单列、`wWinHit 1.6`、`wDist 4.0`、`wDmg 0.12`、`wAcc 2.0`、
  `wHit 0.32` / `wMiss 0.30`（P\* = 0.532）、`terminal` 两输同价 −35。正项 94.2 → 138.1（+46.6%）、
  胜负差 +72.15 → +163.92（×2.27）、负局 Φ 首次为负（−17.34）、过程 Φ/千拍判别比 1.95× → 4.1×。
- **起点改 `y27-lead.it90`**（greedy 0.690，n=400；extra 已非零）而不是 `y26-v6x.it0`：省掉四腿各花的
  10–20 iter 全零暖机；代价是 value 头要按新价重标定 ⇒ 预注册判据**不判 it5**。
- **否决的备选**：`wDmg 0.03→0.10` 单变量（= §120 ⑦ 的建议；只 −16 Φ，压不住 shot 的 +25.7 Φ 挤兑）；
  继续抬 `wHit 0.8` / `wWinHit 3.0`（shot 负局仍赚、window 命中率泄漏到 0.752 已实测）；
  `wDist 6.0`（采样 +0.48 / 部署 +0.17，抬剂量只加大漂移）；`wAcc` 归零（留 2.0 = 税 0.030 作弱信号）。
- **判据（预注册，别跑完再改）**：A 价格接线（单发 >0 / P\*∈[0.50,0.56] / 负局**均值** Φ<0 / 胜负差 ≥1.5σ）；
  B 胜率（it0 ≥0.66 复核；it40 ≥0.70 且 it40/50/60 均值 ≥0.69；**it60 <0.66 杀腿**）；
  C 能力（shots/千 ≥9.4 且 P≥0.60 / A10 ≥3.8 格 / 窗内密度 ≥1.05 / fire=1 不为 0 / 死亡率 ≤起点）。
- **落点**：`nn-training/curricula/y27-steady.jsonc`（identity `cc5f51de1abb58a6`，errors=0）·
  `nn-training/rl-config.json` `courses.y27-steady.rollout_src=local` ·
  全文与逐项价目表 → `docs/nn/experiments.md §121`；分析脚本 `tmp/y27-steady-design.py`（临时件，不入库）。
- **边界**：不动观测表、不碰 base 族 12 维（用户口径：预留观测）、不改动作空间 / decision_k /
  target_transitions；加新指标列（承伤速率）留给下一腿。
- **2026-10-21 追加（同日，用户指令）**：① **改名** `y27-steady` → `y28-steady`、`y27-steady-nowinhit` →
  `y28-nowinhit`（文件内 name/out/traj/backup 同步，identity 不变：`cc5f51de1abb58a6` /
  `ac67e941bfe7e924`）；② **三臂并行**，新增第三腿 **`y28-dmg-half`**（`wDmg 0.12→0.06`，identity
  `267a52fe8465f8af`）。三臂 = 2 轴设计的三个格：对照（原表）/ nowinhit（削"打中"的窗内溢价）/
  dmg-half（削"挨打"的价），同起点 `y27-lead.it90`、同 `paired_rotate_seed 20270206`、同面板、
  同 **300 iter / 18h** ⇒ 任意两臂可逐座位配对。第三腿的依据 = §122 ⑤ 的反向读数：y26-dmg 把
  wDmg ×4 后 **dmg/局 +3%（没降）/ idle/千 ×2.5 / kills/千 −11% / 尾段 −5.9pp（z=−3.0）** ⇒
  承伤价买的是恐惧而非回避；减半后相对剂量 3.6% ≈ 旧表 0.03 的 3.95%（赢过 0.12 的档），而单发
  +0.450 / P\* 0.247 / 有效命中价 0.983 **逐字不变**（与开火轴正交）。否决 `wDist 6.0`（按发付钱
  +0.13/发 ≈ 0.6× shotmix，会把 2.0× 推到 2.6×）、删 back/side（与 nowinhit 同源）、训练侧 λ/entropy
  （证据薄且三臂需一条解释"价"的腿）。三份 `validate_reward` 0 error，逐字段对账只差点名项；
  全文 → `docs/nn/experiments.md §124` + `y28-dmg-half.jsonc` 头注。

## §2026-10-21-goalnn-y26-y27-close — y26/y27 十腿结课：唯一成立的是 wHit 0.35；两条预注册判据（乱射红线 / 单点 win 门）被数据判为坏口径

- **范围**：y26 六腿（null/clear/shotmix/dmg/dist/early）+ y27 四腿（clear/shot/window/lead）。
  十份结课报告**写进各自课程头注**（`── 结课报告（2026-10-21 …`，幂等 MARK，只动注释）；
  十个 `reward_spec().identity()` 回写前后逐个相等（`d5…/fa…/33…/ea…` 与 §120 记的 course_formula_hash 一致）。
- **口径**：`tmp/y2627-close.py` —— 窗口 = 逐局聚合后各点中位（方向门 it30–50、判决门 it80–120）；
  判决 = **同轮逐座位 McNemar**（键 `iter|stage|seed`），逐迭代均值只作附证；十条腿均**无 `run_complete`**
  （控制台停腿/收工），报告按最后完成迭代截断。
- **判决**：成立 1（`y26-shotmix`：尾段 +5.8pp，z=4.8；dmg/杀 −10.7%；行为主判三条全过）·
  弱正未过门 1（`y26-clear`：+2.2pp，z=2.9，门要 +5pp）· 灰区 1（`y27-lead`：A10 +0.79、D01 −6.5%、
  尾段 +5.9pp z=3.8，但 P 0.685 触发红线）· **判负 7**（null −13pp vs y25；dmg 买到瘫痪；dist 死档学不到；
  early 抬总价推不动；y27-clear 接线通≠有用 −2.3pp；shot 更准不是更多 −8.3pp；window 能力达标但胜负解耦 −11.1pp）。
- **两条判据被数据判为坏口径**（不是“事后改门”，是量到了它的失败模式）：
  ① **“乱射红线”（misses/局 ≤ null+40%/+60%）**：出手 +15% 必然把脱靶绝对量推上去 ⇒ shotmix 被误杀，
     而它 kills/局 +5%、dmg/杀 −10.7%、win +5.8pp。以后判乱射看 **kills/局 + dmg/杀**，不看脱靶绝对量。
  ② **“win 单点 < 0.50/0.55/0.60 ⇒ 杀腿”**：n=200–400 的 se 是 2.3–3.5pp ⇒ **十条腿全部**触发过自己的单点门，
     而 lead 的 it35 0.480 后来长到 0.690（全系列最高）。以后判决只用窗口 + 配对；单点门不作数。
- **方法学结论**（写进 §122，y27-steady 已按此设计）：结构决定可学性（同一 wDist ×10 在死档上 A10 +0.10、
  在 extra 接入后 +0.79）；接线通 ≠ 有用；`wHit` 的剂量-反应非单调（0.35 正 / 0.8 负，因 `wAcc` 分母税未动）；
  罚“结果”买不到回避（wDmg ×4 → idle ×2.5、尾段 −5.9pp）。
- **落点**：§122 总表与逐条结论 · 十份课程头注 · `tmp/y2627-close.py` / `tmp/mk-y2627-close.py`（临时件，不入库）。
- **不再做**：不重跑这十腿（判负的机制都已定位到价目表或结构，重跑同一剂量只会复现）；能力侧的下一次机会
  全部押在 `y27-steady`（§121）与它的后续半剂量腿。
## §2026-10-20-goalnn-ppo-worker-transfer-isolation — PPO worker 的结果回传腿搬进独立子进程（`--result-upload process`）

- **用户指令（2026-10-20）**：「云机自主 worker 上，已经把网络传输进程与 PPO 进程独立，两者互不
  阻塞。ppo worker 也需要同样处理，请检查。」检查结论 = **确实没做**：自主腿的补传住在
  `DelivererProcess` → `remote/deliver_worker.py`（2026-10-07，`plan/offline-deliver-isolation` §P1），
  而 PPO worker（`remote/worker.py`）的结果回传仍是**进程内后台线程**
  （`remote/result_upload.py::ResultUploader`）。同一个解释器里做 `json.dumps`（线上体 ~1.6MB）+
  gzip + HTTP，正是 §69 那次现场（PPO 单步 `0.186 → 5.26 / 7.40 s`）的同一条「网络腿抢 GIL/核」通道。
- **决策**：给 `--result-upload` 加 `process` 档：父侧代理 `remote/upload_proc.py::UploaderProcess`
  （与 `ResultUploader` 同接口 `UploaderLike`）+ 子进程入口 `remote/upload_worker.py`；子进程只做
  「收票 → 调 `job_lifecycle.post_result` → 交回落定行与传输账」。**库缺省仍是 `async`**，
  云机入口 `notebook_runtime.run_pull_worker` 显式开 `--result-upload process`。
- **为什么不换缺省**：全仓 10+ 处 `monkeypatch.setattr(remote.worker, "post_result", …)` 是**进程内**
  接缝；进程模式下真正 POST 的是子进程 ⇒ 换缺省会让那批用例被迫起真子进程（慢 + 假红）。能力由
  入口显式开，是 2026-10-07 补传腿那条「缺省 process」的对偶取舍（那条的入口是离线包，接缝少）。
- **为什么不重启子进程而是降级**（与 `DelivererProcess` 的有意差异）：票全在父侧内存里，重启也得把
  它们重发一遍；而「重发」是幂等 POST（hub 409/200）⇒ 直接**sticky 降级**到进程内线程，省一次
  boot 握手与管道重建。降级那一段确实重新与训练线程抢 GIL ⇒ 必须**响亮一行**（不是静默优化）。
- **绝不丢**：`close()` = drain → 关 stdin → 等余量 → 收线程 → **残票父侧同步补发**；子进程半路死
  （OOM/被杀）走同一条路。子进程起不来的**主路径**是在飞的 `code.zip`/旧树里没有那个模块 ——
  与补传腿同形：降级是预期形态，不是异常。
- **记账不撒谎**：真正 POST 的进程不是打 job 行的那个 ⇒ 子进程用 `wire._wire_take` 取走自己的桶、
  随落定行交回；父侧 `job_lifecycle.record_upload_wire` 重放进同一份每 job 账 ⇒ job 行的 `out=`
  与 `overlap=` 与线程模式同义。这一跳**必须住 `job_lifecycle`**（`worker.py` 里不许有 `_wire_add`
  调用点，`test_job_lifecycle_split` 有闸——首版就撞上了）。
- **被否决的备选**：① 缺省就切 `process`（接缝全废，见上）；② 把整条传输腿（含预取下载）一起搬
  ——预取与上一轮 PPO 重叠是**刻意设计**（`remote/prefetch.py` 头部），搬它要另立「暂存目录单写者」
  设计，另案；③ 复用 `deliver_worker` 的 ctl 文件协议（它是磁盘产物腿，结果体在内存里，套上去
  只是多一层 spool 文件与清理）；④ 子进程自己重写一套重试/409/403（必然与 `post_result` 分叉）。
- **已知边界**：子进程的 `_BULK` 调度器是它自己那份（单请求无争用 ⇒ 退化为直发；父侧控制面不再与
  回传共享排程，与 `deliver_worker` 同族取舍）；降级/补发那份只有秒数没有字节（`sync_fallback`
  计数会露出来）。
- **落点**：`nn-training/remote/upload_proc.py`（新）· `remote/upload_worker.py`（新）·
  `remote/result_upload.py`（`UploaderLike` + `OUTCOME_PREFIX` + `off_critical_path` + 模式域）·
  `remote/wire.py::_wire_take` · `remote/job_lifecycle.py::record_upload_wire` · `remote/job_round.py`
  （协议 + `off_critical_path`）· `remote/worker.py` · `remote/notebook_runtime.py` ·
  `tests/helpers/remote_dag.py`（秩：`upload_proc` L1 / `upload_worker` L4）·
  `tests/remote/test_result_upload_proc.py`（12 例）。
- **门禁**：nn python gate **2551 passed / 2 skipped**（余 2 条 `test_offline_deliver` 的 40s 耗时预算
  超限在干净 HEAD 上同样红 = 环境）· ruff / mypy（714 源文件）绿 · `bun run check` 绿。
- **全文** → `docs/nn/remote-transport.md` §107；计划 → `plan/ppo-worker-transfer-isolation.plan.md`。

## §2026-10-20-goalnn-ppo-worker-downlink-isolation — PPO worker 的**下行腿**（预取 payload 下载 + 解包）也搬进子进程（用户指令）

- **背景**：§2026-10-20-goalnn-ppo-worker-transfer-isolation 把**上行**（结果回传）搬进子进程后，
  用户追加同一句话的后半段：「把 PPO worker 的下行腿（payload 下载/预取解包）也搬进子进程」。
  下行此前住在训练进程的后台填充线程（`job_round._prefetch_fill`：`peek` + `download_payload`(P2)
  + `unpack_payload`(单线程 lzma，单份 300MB 级)），而它**刻意**与上一轮 PPO 重叠
  （`remote/prefetch.py` 头部 §0：串行会把 GPU 饿死在传输上）⇒ 重叠的价值必须保留，代价（抢同一个
  GIL / 同一颗核）必须消掉。
- **决定**：新增 `remote/payload_worker.py`（子进程，L5：跑**唯一实现** `_prefetch_fill` +
  `PrefetchStore`）+ `remote/payload_proc.py`（父侧 `PayloadProcess` / 工厂 `make_prefetcher`，L1）；
  `worker.py --prefetch-mode {process,thread}`，**缺省 process**（测试钉 `NN_PREFETCH_MODE=thread`）。
  四条纪律：① **暂存区单写者 = 子进程**（`take` 走请求-应答，父侧 `rename` 与子进程 `prune`
  不可能夹住同一份）；② 未解包的命中项 zip 走 base64（落文件会让父侧读一半被 prune 删）；
  ③ 起不来/中途死 ⇒ **sticky 降级**到进程内 `PrefetchStore` + 填充线程（`__getattr__` 逐字转发，
  `job_round` 旧分支一字不改）；④ **跨进程 P1 协同**：`bulk_sched.set_p1_observer` / `foreign_p1`
  —— P2 的「P1 在等就不开工 + 可当场打断」原本靠同一个调度器实例，搬进程后必须显式广播。
- **为什么不顺手改库缺省（上行是 `async`、下行是 `process`）**：上行的 10+ 处测试 patch
  `remote.worker.post_result` 是**进程内接缝**，换缺省会假红；下行的接缝全在 `PrefetchStore` /
  `_prefetch_fill` 上，`tests/conftest.py` 钉一个 env 就够。判据是「这个缺省会不会把别人的接缝
  变成空操作」，不是「两个代理要长得一样」。
- **共用的部分真的抽了出去**：`repo_root` / `spawn_module` / `reap_child` / `pump_lines` 落进新的
  `remote/child_proc.py`（L0），两个代理不再各持一份（复用账本 `exact_groups` 回 14，
  `clone_groups` 17→15；`same_name_ge3` 81→88 的 7 个名字逐条理由写在 `tests/test_reuse_ledger.py`）。
- **顺带修掉的两条慢用例**：`tests/remote/test_offline_deliver.py` 的两条用真死端口
  `http://127.0.0.1:1` 演「hub 关机」——在会丢弃 SYN 的环境里 connect 会挂到 `probe()` 自己的 20s
  上限（实测 40s / 60s），被耗时预算判红而断言全绿。改用 `_RefusingHub`（`net_http.urlopen` 替身抛
  `ConnectionRefusedError`，= 生产里 `_urllib_opener` 抛的那一种），语义等价、与内核行为解耦。
- **落地**：`remote/payload_proc.py` · `remote/payload_worker.py` · `remote/child_proc.py` ·
  `remote/prefetch.py`（模式常量 + 行前缀）· `remote/bulk_sched.py`（`set_p1_observer` / `foreign_p1`）·
  `remote/job_round.py`（`pf_proc` 分派 + `stop_fill`）· `remote/worker.py`（`make_prefetcher` /
  `--prefetch-mode` / 收尾 close / P1 广播）· `tests/helpers/remote_dag.py`（秩：`payload_proc` L1 /
  `payload_worker` L5 / `child_proc` L0）· `tests/conftest.py` · `tests/remote/test_prefetch_proc.py`（14 例）·
  `tests/remote/test_offline_deliver.py`（两条用例的替身）。
- **门禁**：ruff / mypy 绿 · nn python gate **4781 passed / 2 skipped**（`e2e/test_auto_handoff_e2e.py`
  的 1 条在本机恒红，已在干净 HEAD 的 worktree 复现 = 环境丢回环包）· `bun run check` 绿（2633 例）·
  `check-decisions` 绿。
- **全文** → `docs/nn/remote-transport.md` §108；计划 → `plan/ppo-worker-transfer-isolation.plan.md` §5。

## §2026-10-20-goalnn-claim-timeout-outlives-console-leg — `claim` 的客户端超时必须 > hub 在应答里花掉的控制台预算（8s vs 8s = 白丢租约 + 一拖一自锁）

- **报障**（用户 2026-10-20）：「`e2e/test_auto_handoff_e2e.py::test_auto_loop_runs_the_claimable_batch_in_order`
  在本机恒红 —— 修！」（上一刀 `§2026-10-20-goalnn-ppo-worker-downlink-isolation` 把它记成
  「沙箱丢回环包」的环境问题；那个判读**不完整**：环境只是放大器，真因是下面这条超时撞车。）
- **根因**：hub 的 `/offline/claim` 成功分支在回 200 **之前**同步跑 `_ask_console_freshness`
  （故意：作废必须发生在响应返回前，否则云机随后取包会拿到旧包），预算是
  `hub/task_pack.py::TASK_PACK_TRIGGER_TIMEOUT_SEC` = 8s；而云机侧的 `claim_course` 用
  `PING_TIMEOUT` = 8.0 当超时 —— **两个 8 撞在同一个数上**，控制台挂住时客户端必然先到点。
  claim 是**写**：hub 已经发出租约、hold 已落盘，客户端只看到 `net`（纪律：照旧跑、无租约）
  ⇒ 没人交还那份 hold，一拖一（按 worker）把本盘其余课全挡在 `busy` 上等 900s。实测：
  e2e 三门课只跑成第一门，日志里 `端点连不上（TimeoutError）` 与 hub 的 `offline-claim … 200`
  同一秒出现。
- **被否决的备选**：把 hub 的控制台腿挪到后台线程/响应之后。否决理由：claim 与随后取包之间的
  竞态现在是靠「响应返回前旧包已作废」关掉的，挪走等于重新打开它（云机从旧起点烧几十轮，
  比慢 8s 贵得多）。**要动的是客户端对「写」的超时预算，不是 hub 的同步语义。**
- **决定**：`remote/offline_boot.py` 新增 `CLAIM_TIMEOUT = 30.0`（20s 余量留给同一分支里的包
  `sha256`）并作为 `claim_course` 的缺省；只读探活/清单仍用 `PING_TIMEOUT`（8s）。
  一般规则：**写请求的客户端超时必须 ≥ 服务端允许自己花掉的时间**；两个数相等 = 踩线。
- **测试侧同一条链的放大器**：e2e 的 `_Hub` 缺省不再吃 `tests/conftest.py` 钉的死端口
  `http://127.0.0.1:9`（回 RST 还是丢 SYN 取决于主机 ⇒ 每次触发 8s 还是 2ms 看天气），改为自带
  `_NoAutoHandoffConsole`（一律 404 = 「控制台不在/太旧」的确定性降级）。该用例 25s（3×8s）→ <1s。
- **落地**：`remote/offline_boot.py`（`CLAIM_TIMEOUT` + `claim_course` 缺省）·
  `e2e/test_auto_handoff_e2e.py`（`_NoAutoHandoffConsole` + `_Hub` 缺省接线 + `close()` 收它）·
  `tests/common/test_offline_task_queue.py::test_claim_timeout_outlives_the_hub_console_leg`
  （契约用例：两个常量任何一边被调走都当场红）。
- **门禁**：`pytest e2e/test_auto_handoff_e2e.py` **14/14 绿**（此前 1 红）· nn python gate
  **4781 passed / 2 skipped**（ruff + mypy 全绿）· `tests/test_reuse_ledger.py` 绿。
- **全文** → `docs/nn/remote-transport.md` §109。

## §2026-10-20-goalnn-port-probe-bounded — 端口探测必须带上界：`portListen` 的 connect 在丢 SYN 的主机上永不返回

- **触发**（用户 2026-10-20）：「`portOwnedBy（真实监听进程） > spec.ownsResource …（真监听）` -- fix it」
  （= `dashboard/tests/training-port-reclaim.test.ts` 里修完 `freePort()` 之后剩下的最后一条红）。
- **根因**：`dashboard/src/core/proc.ts::portOwnedBy(pid, port)` 在 `portOwnerPids(port)` 为空时退回
  `portListen(port)` 再确认一次；`portListen` = `Bun.connect`，它默认「没人监听 ⇒ 立刻 ECONNREFUSED」。
  在**丢回环 SYN** 的主机上这个前提不成立：探针实测 `Bun.connect` 到闭端口 >6s 不返回、
  `portOwnedBy(pid, 闭端口)` >5s 挂死（`portListen` 在 `bind` 探测下 3ms、`lsof` 51ms）。
  影响面不止这一个函数：`portListen` 是所有「就绪 / 归属 / 空闲」判断的代理
  （`specs.ownsResource`、`slots.allocateSlot`、`stack/hub` 就绪复核、`stack/smoke`、`launch/cli` 占用扫描）。
- **决定**：`core/net.ts::portListen` 加**上界** `PORT_PROBE_TIMEOUT_MS = 500`（第三参可覆盖）：
  `Promise.race([probe, 超时])`，超时按 `false`；超时之后才连上的那一份连接仍然 `end()`（不许漏 socket），
  并清 timer。判据依据：回环上真在监听的 socket 由内核直接回 SYN-ACK（不等 `accept`），握手 µs 级
  —— 500ms 是三个数量级的余量，而「没人听」「内核丢 SYN」两个真相的正确答案都是 `false`。
- **被否决**：① 只在测试里打桩 `portOwnedBy`（绕过被测函数 = 删掉覆盖面）；② 另写一个“快速失败”专用
  探针（同一个真相做成两份）；③ 超时当 `true`（`owners` 为空已经说明没人持有，判 true 会让监督器把
  僵尸认成新进程）。也不改 `Bun.connect` 的用法本身（它仍是“能连上就是在听”的正确判据）。
- **读数（同一台 Linux 沙箱）**：该测试文件 12 pass / 3 fail → **15 pass / 0 fail（1.7s）**；
  `bun run test` 全量 1783/3（157.4s）→ **1786 pass / 0 fail（21.6s）**；`typecheck` / `oxlint`（0 警告）/
  `oxfmt --check` / `bun dashboard/src/server/build.ts` 四份 bundle 全绿。
- **落地**：`dashboard/src/core/net.ts`（`PORT_PROBE_TIMEOUT_MS` + `portListen` 上界）·
  `dashboard/tests/training-port-reclaim.test.ts`（`freePort` → bind 探测 `portFree`）。
- **全文** → `docs/nn/console.md` §41。

## §2026-10-21-goalnn-prefetch-p1-fire-and-forget — 下行腿的 `id` = **兑现凭据**：fire-and-forget 的 `p1` 广播**不应答**（WARN 刷屏的根因）

- **报障**（用户 2026-10-21）：PPO worker 日志里刷
  `WARN 预取应答行没有 id（忽略）：{"id": null, "ok": true}` —— 每个 job 6~8 条。
- **根因**（父子两侧协议错半拍，§2026-10-20-goalnn-ppo-worker-downlink-isolation 搬下行腿时带进来的）：
  `id` 是父子之间的**兑现凭据**（父侧 `_request` 编 id → 子进程回 `@@pf@@ {"id": …}` → 父侧按 id 派发），
  但协议里**唯一**的 fire-and-forget 命令 `p1`（P1 进出等待的广播，父侧挂在
  `bulk_sched.set_p1_observer` 上，**绝不能**在关键下载里等应答）**不带 id**；
  子进程此前**每条命令都回** ⇒ `p1` 的回执 `id` 是 `null` ⇒ 父侧 `_on_reply` 按「协议坏了」
  **响亮** ⇒ 每个 P1 进出等待一条 WARN。一个 job 有 3~4 次 P1 传输（payload 下载 / blob init /
  blob opt / 结果回传）⇒ 6~8 条。**不是故障，是噪声**（真故障会被这层埋掉）。
- **决定**（两侧各守一半，写成协议的一条硬约定）：
  ① 子进程 `remote/payload_worker.py::_reply` **只在命令带 `id` 时才写应答行**（无 `id` ⇒
  fire-and-forget ⇒ 不回；顺带省掉每次 P1 进出等待白醒父侧读者线程一次 —— 这轮搬进程就是为了
  抢 GIL）；② 父侧 `remote/payload_proc.py::_on_reply` 收到无 `id` 的应答行**不是故障**
  （没有兑现凭据 ⇒ 没人等）⇒ **计数**（`status()["unclaimed"]`，版本错配的信号）**不刷屏**；
  `id` 不是整数才是真故障 ⇒ 仍 WARN。
- **被否决**：① 父侧给 `p1` 编个 id（那样应答行带着一个**没人等**的 id 回到父侧，靠「没人等 ⇒ 丢」
  兜住，子进程仍要为每条 fire-and-forget 命令 print+flush 一行 —— 协议上撒谎，且没消掉噪声）；
  ② 父侧直接忽略所有无 `id` 的应答行（不计数）：那就看不出「子进程版本错配」这个真信号；
  ③ 把 WARN 降级成 `DEBUG`（本仓无 DEBUG 级；且真故障仍会被噪声淹没）。
- **落地**：`remote/payload_worker.py`（`_reply` 带 `rid` 参数 + `main` 传 id · 模块头协议段）·
  `remote/payload_proc.py`（`_on_reply` 分流 + `status()["unclaimed"]` + `note_p1` 协议注记）·
  `tests/remote/test_prefetch_proc.py`（假子进程逐字对齐真子进程「无 id 不回」+ 三条新用例）。
- **门禁**：`tests/remote/` + 复用账本 + 文本断言护栏 + 分层账本 **1440 绿** ·
  ruff / mypy 绿 · nn python gate 全量绿。
- **全文** → `docs/nn/remote-transport.md` §110。

## §2026-10-21-goalnn-est-latest-and-dispatch-safety — 采集 est 取**最近一轮** + 派局压 0.92；**不承诺 `batches == 1`**

- **触发**（用户 2026-10-21「处理评审发现的问题」）：`plan/rollout-corpus-supply.plan.md` v2 的 A0 写成
  「`learned or trailing` + trailing 取近 3 轮 min ⇒ 批数 3 → 1」。复核代码后判定三处要改。
- **根因**：① `loop_volume.py` 是 `trailing or learned` ⇒ trailing 恒有值 ⇒ **本轮自校准被短路**；
  ② 分关 trailing 是 **3 轮 pooled 均值**，局长变短时结构性偏高（现场 652 vs 实际 530，+23%）；
  ③ 但 est 的主成分是**逐轮跳变**（it62 取 `{476,523,693,657}` → it63 取 `{652,674,664,548}`，上升），
  对一个 ±40% 跳变的量取 min 会把误差放大到最保守一侧（最坏 +60% 局数），直接打穿自采的 CPU 秒账。
- **决定**：① 优先级 `learned → trailing`（`volume_alloc.pick_stage_est`）；② 新增 `agg="latest"`
  只取最近一轮（`resume.trailing_stage_samples_per_game`，本地链与 `loop_export` 同口径）；
  ③ 派局 est 乘 **0.92** 定点化下压（`volume_alloc.pull_est_low`），**只喂派局、不喂硬顶**；
  ④ 已知残余：趋势误差 >8% 的关仍会出第二批 ⇒ DoD 写 **`batches ≤ 2`**，`== 1` 是 stretch。
- **被否决**：①「近 3 轮 min」（放大跳变，见根因③）；②「每人 +1 局」（`ceil` 已向上取整，双重余量
  会越 DoD 的 `Σgames ≤ 1.15 × 理论`）；③ 只改本地链（云机 spec 的 `est_s_by_stage` 走同一函数，
  两侧口径分裂 = 同一轮两份反解）；④ 改 `plan_continuous_batch` 的软停/硬顶语义（本改动只动输入）。
- **身份**：分配规则**不进** `corpus_identity_fp`（用户 2026-10-07 拍板 ②，见 `loop_volume.py` 注释）
  ⇒ 不构成新实验；但 seed 计划与局数会变 ⇒ **在 it 边界声明断点**，跨断点不可逐位比较。
- **落地**：`worker/volume_alloc.py`（`pick_stage_est` / `pull_est_low`）· `worker/resume.py`（`agg`）·
  `trainer/loop_volume.py`（优先级 + `est_used`/`est_src` 进 iteration 事件）· `trainer/loop_export.py`。
- **门禁**：`tests/worker/test_rollout_est.py`（新）· `tests/worker/test_rollout_volume.py`
  （`test_continuous_autocalibrates_and_pulls_est_low`：`[12,12]` vs 旧口径 `[12,4]`）·
  `tests/worker/test_volume_alloc.py` · `tests/worker/test_volume_plan_block.py` ·
  `tests/trainer/test_loop_export_split.py` · `tests/worker/test_iter_topup.py` · `e2e/test_volume_e2e.py` 全绿。
- **全文** → `plan/rollout-corpus-supply.plan.md` §1.3 / §3.1 / §3.5。

## §2026-10-21-goalnn-effslots-diagnosis — E 低的成因是**重复执行 + 远端忙碌**，不是本地 lane；只测不改

- **触发**（用户 2026-10-21「处理评审发现的问题」）：v2 把「有效并行度只有 ~11 槽」归因成
  「本地 lane 优先 ⇒ 自机吃掉 69%」，并把 A2′ 写成「改本地 lane 的优先级」。
- **根因**：代码事实相反 —— `dispatch.py:622-628` 在远端失联超阈值时把 `local_cap[0]` 抬到
  `cap_full`（现场 8），日志里 `local_slots=0`；自机 29 局 ÷ 8 槽 = **3.6 波 ≈ 8.7s**，正好是那个 9s 墙钟
  ⇒ 自机是**满产能**，不是被闸门偏爱。真正的缺口：远端 12 槽在 9s 里只出 13 局（**~29% 忙碌**）+ 同一行
  日志的 `fanout_settled=9 + fanout_inflight=7`（+`main_by_fanout=4`）⇒ **重复执行 ≥16 次 ≈ 21% 槽秒**。
- **决定**：① **不动**本地 lane 优先级（动了只会把活推给更慢的节点，墙钟更长）；② **不封顶**竞速副本
  （它是空槽能被填上的机制），要动先拿读数；③ 本轮只把读数做出来：`[dist] round done` 增 `dup=`、
  `[dist] round eff` 增 `E=`，`combined["dist"]` 增 `raceDrops/dupRate/slotsOnline/sumGameSec/effSlots`；
  ④ E 的口径固定为 **Σ局服务时长（`elapsedSec`）/ 采集墙钟**（Little's law），分子不用训练机侧 wall。
- **被否决**：① 给本地 lane 加优先级/降并发（成因判错，见根因）；② 用「训练机侧 wall」当分子
  （把取包与网络等待算成占槽 ⇒ E 虚高）；③ 在日志里现算（口径会散在脑子里，v2 就是这么读反的）。
- **落地**：`common/distribution.py`（`dup_rate` / `effective_slots`）· `trainer/dispatch.py`（累加 + 两行读数
  + 结构化字段）· `tests/common/test_rollout_eff.py`（新）·
  `tests/trainer/test_rollout_dispatch_resilience.py`（接线断言）。
- **门禁**：上述用例 + `tests/worker/test_rollout_volume.py` + nn python gate 全绿。
- **全文** → `plan/rollout-corpus-supply.plan.md` §1.1 / §3.4 / §13.2。

## §2026-10-21-export-bundle-stall — 导出腿：基准搜索要有**总**预算，锁要按课分且有**持久**上界

- **触发**（用户 2026-10-21「处理评审发现的问题」）：2026-10-21 现场三课导出全被挡；一个 detached 导出
  进程以 343 MB/s 累计读到 8.5 TB 且永不退出，全局单键 `bundle:export` 把别的课一并焊死。
- **根因**（两条独立缺陷）：① `_standard_bytes` 递归落地候选时，`_MAX_CHAIN`/`_MAX_CANDIDATES` 是
  **每次调用各一份**，232 个互为候选的 delta 展开成阶乘；② 导出互斥是全局单键 + 只认进程退出（8 小时上界
  且 release 不杀进程）；③ 附带：状态文件只有 `at`、没有新鲜度/到期判据 ⇒ 11:41 的 `ok:true` 一直演成功。
- **决定**：① 一次顶层解析共享 `_SearchContext`（同一路径只读一次 / 失败按路径负缓存 / 成功按内容 sha
  正缓存 / 唯一读字节 2 GiB 与唯一候选 20000 双预算），撞顶抛 `SearchBudgetExceeded` 且**父层不得吞**；
  ② 失败族带 `retryable=False`，`round_failure` 据此不重试（判据是属性，不是中文文案）；
  ③ 互斥键按课（`bundle:export:<课>`），`busy` 与持久 `running` **同源**成 `exportBusyReason`；
  ④ 30 分钟硬上界 + 超时 `killPidTree`（先验命令行身份，验不了不杀）+ 终态 + release + 恢复旧包；
  ⑤ 状态四态：python 启动写 `running:true`，控制台读面派生 `stale`（作废锚 = stale 文件名 UTC 戳与线上包
  mtime 取大，**不是** rename 保留的 mtime）与 `expired`（超时 / pid 不在），hub 一律映射 `unknown`。
- **被否决**：①「调小 `_MAX_CHAIN`/`_MAX_CANDIDATES`」（掩盖乘积，不是修）；② v1 的「只加成功 memo」
  （本次基准缺失 ⇒ 零命中）；③ 给导出套 `busySince` 的 5 分钟 TTL（合法导出会跑更久）；
  ④ 把 `running` 在读面直接降成 `false`（看门狗分不清「还在跑」与「运行态已到期」，会把已产出的包误写成
  失败）；⑤ 超时只 release 不 kill（僵尸继续烧盘）；⑥ 盲杀 PID（复用会杀无关进程）；
  ⑦ hub 把 `running` 当 `ok:false`（导出还在跑就停试 900s）。
- **2026-10-21 补（用户口径：「等 30 分钟不可接受」）**：PID 复用不再靠 30 分钟上界硬等——导出腿加
  `exportPidState(pid, course): 'match' | 'mismatch' | 'unknown'`（命令行三态）作为**身份判活**：
  `running && pidAlive && 状态写下 ≥5s` 时查一次（Linux 直读 `/proc/<pid>/cmdline` 不 fork；
  其余平台 15s 缓存，Windows 一次 `pwsh`），`mismatch` ⇒ `expired` ⇒ **点一下就能重导**。
  两个方向的保守刚好相反：`unknown` 不解锁也不杀，`mismatch` 只解锁不杀，`match` 才杀。
  仍**不修**共享 `pidAlive`（48 个调用点：trainer 监督 / hub / worker / stop / eval）——
  原语级修法（PID + 启动时刻 / `pidfd` / 心跳文件）是另一条腿，且本环境只能验到 Linux 一半。
  被否决：① 直接把 `pidAlive` 改成双因子（全局语义变更、Windows 无法验证）；② `unknown` 也解锁
  （同课并发写同一个 `.tmp`，破不变量 2）；③ 缩短 30 分钟上界（真正卡住的导出合法地可以跑很久）。
- **身份**：不动 `--export-bundle` 的锁与账本语义，不动 delta 包格式与「本机不猜基准」纪律 ⇒ 不是新实验。
- **落地**：`remote/weights_standard.py` · `trainer/loop_round_steps.py` · `remote/bundle.py` · `hub/task_pack.py` ·
  `server/bundles/{export,marks}.ts` · `core/net.ts` · `server/actions/auto-offline-handoff.ts` · `server/api/route.ts` ·
  `server/server.ts` · `web/app/lib/api-client.ts` · `web/app/panels/BundleRowActions.tsx`。
- **门禁**：`nn-training` 侧 97 用例（weights_standard / loop_round / export_status / auto_handoff）+ dashboard
  91 用例（task-bundle / auto-offline-handoff，含 PID 复用三态）+ 两个 typecheck + 三份 web bundle 全绿。
- **全文** → `plan/export-bundle-stall.plan.md`（评审 `plan/export-bundle-stall.review-bf.md`）。
## §2026-10-21-goalnn-online-worker-bun-for-self-collect — 在线 worker 盘**自采模式**下允许装 bun（`§2026-09-25-goalnn-online-worker-no-bun` 的**限定例外**）

- **触发**（用户 2026-10-21 裁决，原话「如果改成自采集，可以装！」）：`plan/rollout-corpus-supply.plan.md`
  腿 C（**GPU 实例自采语料**，`kind=iter`）需要该盘自己跑 TS rollout，而执行体
  `nn-training/worker/iter_rollout.py::resolve_bun` 明确要求**节点上有 bun**（找不到 ⇒ 响亮拒绝，
  且原话「本 worker 所属的盘不跑 rollout」）。腿 C 的收益（免语料网络 + 免网络波动 + 供给随 worker 线性）
  **以 bun 可装为前提**。
- **裁决**：`§2026-09-25-goalnn-online-worker-no-bun` 的「在线 worker 盘不装 bun」**降级为「默认不装」** ——
  **自采模式**（该盘自己跑 rollout）时装 bun；**纯训练 worker 逐字不变**（仍不装、不付公网下载与启动延迟）。
- **实现的硬约束（四条，照做即可）**：

  | # | 约束 | 出处 |
  |---|---|---|
  | ① | **复用现成装机函数**，不许另写：采样节点已有 `remote/rollout_node.py::_ensure_bun`（找 `bun` / `/root/.bun/bin/bun` / `~/.bun/bin/bun` ⇒ 缺失则 `curl -fsSL https://bun.sh/install | bash`，timeout 600s；`bun install --frozen-lockfile` 失败不致命） | 退役过的 `ensure_bun()` **不许复活**（`tests/remote/test_tailscale_boot_bun.py::test_retired_bun_helpers_do_not_come_back` 钉着） |
  | ② | **顺序**：公网下载必须**发生在代理/隧道切换之前** | `remote/rollout_node.py:18` 的既有纪律（「公网下载一律在 `platform_net_env()` 里或发生在 ensure 之前」）⇒ 装机点在 tailnet/cloudflared 引导**之前** |
  | ③ | **护栏改写而不是删掉**：六条守卫从「在线 worker 一律不许装 bun」改为「**仅当自采模式开启时**才许装」 | `tests/remote/test_tailscale_boot_bun.py`（守卫**随口径一起改**，不允许只改断言或只改实现） |
  | ④ | **TS 代码树不用新做**：worker 已有 `remote/download.py::_ensure_ts_code` | `ts_code_cache/<sha>/` = **bun 的 cwd**（另一棵树，`download.py:17`）⇒ 自采只差 **bun** 这一个装机件 |

- **被否决**：① 直接废掉「在线 worker 不装 bun」这条纪律（⇒ 纯训练 worker 也要下载 bun、付公网安装的失败面与
  启动延迟，而它**永远不会用**）；② 在 worker 盘另装一份 bun 的副本（⇒ 两份真相 + 版本漂移；日志里已有
  `bun patch differs (1.4.3 vs 1.4.2) — allowed (yellow)` 这类容差，别再扩大）；③ 把 bun 塞进 payload /
  代码快照（⇒ 把「可执行文件」混进内容寻址的代码树，破坏「代码 = 源码」的边界）。
- **影响面**：**仅在腿 C 落地时生效**（`plan/rollout-corpus-supply.plan.md` §5.3）；在此之前**逐字不变**。
- **落点**：`plan/rollout-corpus-supply.plan.md` §5.3 · 实现时改 `remote/tailscale_boot.py` /
  `remote/notebook_boot.py` 的引导顺序 + `tests/remote/test_tailscale_boot_bun.py` 的守卫口径 ·
  `docs/nn/remote-transport.md`（顶部新增 §）· `.workbuddy/memory/2026-10-21.md`（一行）。


## §2026-10-21-goalnn-y29-resume-from-it300 — 不续旧课：从 `y28-steady.it300` 重开两臂（摘 `wWinHit` + `wDmg` 减半），因为 lr 档已打满而尾段斜率仍显著

- **背景**：`y28-steady` 跑满 300 轮，anchor **0.695 → 0.815**（同座配对 +12.0pp / McNemar p=0.0088）。
  用户 2026-10-21 判定「胜率喜人、还在上升、可以继续」，并令 `y28-dmg-half`（停在 it62）**停跑**，
  改开两条从 it300 起的新腿。
- **「还在上升」的实测口径**（`reports/y28-steady-20261021.md §2.1`）：分段斜率
  it60–150 **−0.85±0.38（t=−2.2）** · it150–235 +0.17 · **it235–300 +0.99±0.33（t=3.0）** ·
  it265–300 +0.95（t=1.0，不显著）⇒ **是台阶后高位横盘，不是线性爬升**，外推收益要按衰减估。
  （同一节的旧结论「尾段已平」是只用端点配对 p=0.464 得出的，已订正。）
- **决定**：
  1. `y29-steady` = `y28-steady` 的价目表**摘掉 `wWinHit`**（双臂已证零胜率回报：窗内命中 −14.2% 但
     it300 配对 Δ−1.5pp / p=0.798），起点 `y28-steady.it300`（sha16 `c5030c2e3e869605`），
     `iters 200 / max_hours 8`，lr 档重排（it1–120 用 1.5e-4 → 之后 1e-4，因为 y28 全程已在最低档）；
  2. `y29-dmg-half` = 逐字同上，只 `wDmg 0.12 → 0.06`（相对剂量 3.59% ≈ 旧表 0.03 的 3.95%，
     即 y26-dmg 里"赢过 0.12"的那一档）；
  3. `paired_rotate_seed 20270206 → 20270207`（本腿不是 y28 的续臂；同流会让 it1..300 与 y28 的 300 轮
     `(stage,seed)` 全重复，§15.1）。**评估面板 anchor 860001-860050 不变**（跨代可作方向参照）。
- **★ 必须记住的一条**：`y29-steady` 的 reward identity = **`ac67e941bfe7e924`，与 `y28-nowinhit` 完全相同**
  ⇒ 两腿价目表逐字一致，差别只有起点/lr 档/seed 流。**不得**把「y29-steady 涨了」归因于价目表改动。
- **否决的备选**：① 原地 `--run-iters +200` 续旧课（在 1e-4 最低档上磨，且 `out/traj` 复用会让新旧
  两段混在一个账本里）；② 沿用 `y28-dmg-half` 的 it62 当 dmg 对照（起点分布不同 —— 它从 it90 起、
  没经历过 0.12 的 300 轮塑形）；③ 换评估面板（会失去与 y28 的跨代参照）。
- **判据（预注册）**：主判 = 两臂 it150–200 的 11 个点按座位聚合后 McNemar（|Δ|≥5pp 且 p<0.05 = 有影响；
  |Δ|<3pp = 分不出）；**不设 it40–60 绝对阈门**（§124 的 B2/B3 判负而末态 +12pp 的教训）。
  杀腿：`kills/千 <2.40` · `timeout >5%` · it100 `anchor_wr <0.72` · `dmg/局 >1.10×起点 且 Δwin ≤0`。
- **已知低估偏差**：两臂同从 `wDmg 0.12` 塑形过的 it300 起 ⇒ dmg-half 测的是「从怕疼的策略出发把价减半」，
  前 30–50 轮可能无差；判据窗口因此放 it150–200。
- **落点**：`nn-training/curricula/y29-{steady,dmg-half}.jsonc` · `docs/nn/experiments.md §125` ·
  `tmp/y29-validate.py`（日志同名 `.log`）· `.workbuddy/memory/2026-10-21.md`。
  **开课 = 人在控制台点**；agent 不起训练。

## §2026-10-21-goalnn-ppo-wallclock-batch1 — PPO worker 墙钟第一批：身份命中链**从未通电**（接线 + 顺序）、`/start` 线程化、两次同步合并、设备行、一机 N worker

- **背景**：`plan/ppo-worker-wallclock.plan.md` 三轮评审（ds / Beam / `plan/ppo.review-bf.md`）后落地第一批。
  现场日志（job `d749659e732144f2`）里 `init` 与 `opt` 每轮都 `src=download`（1.2s/轮），而身份映射的设计
  是零字节 —— 实测命中率 **0%**，不是预期的「~5% 未命中」。
- **根因两处（缺一不可）**：
  1. **接线**：`hub/schedule.py:166-180` 的 claim 响应顶层**没有** `course_fp`（它在 `manifest` 里），
     而 `job_round` 的落定回调只读顶层 ⇒ `course_cache.settle()` 收到空串 ⇒ 早退 ⇒ `latest.json` 永不写。
     修法：`course_cache.course_fp_of(job)`（先顶层、再 `manifest`，单点可测）。
  2. **顺序**：`train_core` 先 `resolve_blobs_batch`（**非惰性**，当场 `download_blobs` 并落盘）再判身份
     ⇒ 即使命中，opt 的 0.60MB / 0.7s 也已经付掉、下回来的字节被丢弃。修法：`hit()` 上移 +
     `blob_wanted(..., skip={BLOB_OPT})`（纯参数；`ref` 不在 skip 里）。
- **链语义变更（已在 `docs/nn/remote-transport.md` §94 的 identity 档内）**：修复后训练起点 = 本机 f32，
  不再等于 hub 落盘的量化重建字节 ⇒「训练用的权重」与「评估/发布的权重」正式分家。
  **不在跑着的腿中间热切**：开腿时按 §6.3b 三件套（新 `--out`/`--traj` + 60-seed 三难度基线 + golden）。
  本批只做接线与顺序，**没有**任何腿被切换。
- **顺带修**：`blob_hits` 只认 `src=="cache"` ⇒ 身份命中（`course_cache`）漏记；改为一并计入。
- **`/start` 线程化（W2）**：形态 = 线程发 + `job_ready()` **之前** `join(3.5s)` + 超时**先 ready 再同步补发**。
  否决了 v3 的 `join(15)`：`job_started` 自身 HTTP timeout 15s ⇒ join 必然超时，而 ready 是**重复抑制**信号，
  压后 15s 等于放大「备份副本重算一整轮」的窗口；也否决了「线程内重试一次」（它会与同步补发叠成第三次并发 POST）。
  `computing_at`（掉队判据唯一时基）的落库时刻与同步版相同（都早于 ready）⇒ 阈值一行未动。
- **W3（同步合并）**：`apply_value_anchor_pull` / `policy_new_rms_of` 返回 **0 维张量**（不再 `float()`），
  折进已有的 `sync_scalars` ⇒ 每步 3 次全设备同步 → 1 次。数值逐位不变（同一个 float32→double 转换）；
  键集由 `tests/worker/test_ppo_scalar_sync.py` 钉住。**否决**「每 epoch 只算一次」——那两个数每步都进 stats。
- **W0-a / W0-b**：job 级「设备」行（`n=` / 卡名 / `cap=` / `dp=` / `threads=`，CPU 有 guard）；
  per-step 墙钟**累加常开**、汇总行只在 `PPO_CUDA_DIAG=1` 时打（稳态日志零变化）；产物段（save/pack/identity）
  同开关计时（它不在 `ppo=` 里，是 `other=` 归因不闭合的那一块）。
- **一机 N worker（W-DP 起法）**：启动 argv 在仓内 `remote/notebook_runtime.py`（`:236-259`），
  不是控制台功能（控制台 `localWorker` 是**开发机**单实例）⇒ 新增 `worker_restart_argv(instance, workers)` +
  `cfg["workers"]`（缺省 1 = 逐字节旧行为）：每实例 `--device cuda:<i>`、各自 `--out`（token 随之各自一份）、
  **共享 `--cache-dir`**（否则身份命中率 ~1/N）、线程 = `cpu_count()//N`。
  **否决**「engine.py 的 `--threads` 缺省 8 导致超配」：云机 argv 写死 `--threads "0"`（torch/OMP 缺省），
  那个 8 只对 standalone CLI 生效。
- **`course_cache._blob` 读命中 touch mtime**：身份对在命中时**不经** `_cache_blob`（不走下载），
  不 touch 就按「生产时间」定生死；补一行 `os.utime` 后 §8-Q7（是否给 `blob_cache` 单独预算）**结案：不需要**。
- **未做（等真机）**：W3.5 CUDA graph（需 profiler 读数 + `dp=0` 形态）、W-DP 三组 A/B/C、W5 显存探针。
  profiler 插桩也未做（会改 `ppo_update` 的循环结构）。
- **落点**：`remote/course_cache.py` · `remote/job_round.py` · `remote/train_core.py` · `remote/notebook_runtime.py` ·
  `worker/ppo/engine.py` · `tests/remote/*` · `tests/worker/test_{ppo_scalar_sync,value_anchor,policy_new_rms}.py`。

## §2026-10-21-goalnn-offline-deliver-not-stuck — 补传「失败可以、不许卡住」；陈旧 hold 的恢复宽限**一代只给一次**；降级档替代拆件

- **背景**：`plan/offline-deliver-stall.plan.md` v2（评审 `plan/offline-deliver-stall.review-bf.md` P0×5/P1×8）。
  两个现场：① `y28-nowinhit` 05:53–05:55 逐轮成功（非 eval 轮 322KB），05:57 起该轮体涨到 1.24MB
  （`eval_rows` 400 局 ×2,355B）后 6 分钟零送达、积压 13→14 轮；② `y28-steady` 跑完 6.9h 后
  `/offline/hold` 仍 `state=live / progress_ago=300.0 / expires_in=600.0`（两次采样逐位相同）。
- **根因 B 改判（评审 P0-1，这是本条最重要的一条）**：那组读数是 `_dispatch_load` 的**恢复宽限**
  （`HOLD_RESTORE_GRACE_SEC=300`，只改内存、不落盘）的指纹：`900-300=600`，两项都 600。
  真打点的 `ago` 不可能两次都恰好 300.0。⇒ **不是「训完还在打点」**，是「每重启一次 hub 就把陈旧
  hold 抬回 `now-300`」；重启周期 <600s 就能永久焊住 busy 闸。
- **决定**：
  1. **宽限一代一次**：派发记录加 `hold_restored_for_at`（落盘）；只有 `hold.at` 更新的接管才再吃一次。
     **不写 `updated_at`**（它是 `note_progress` 的 60s 落盘节流基准，改它会吞掉重启后第一拍打点）。
  2. **传输失败不污染可达性**（D-4）：`_post` 异常只作废**探活成功缓存**（`_probe_dirty`），
     `_reachable`/15s 负缓存/`unreachable` 日志一字不动（旧写法一次数据失败 = 下一拍连 `/ping` 都不发）。
  3. **失败不挡后面**（G2）：`_sync` 遇 `stop` 不再 `break`，续推下一轮，每拍最多 `MAX_FAILS_PER_PASS=2`
     次传输失败（一次失败已付一个超时窗口）。**`pending()` 的语义永不收窄**（D-3：它是备份抽屉与
     idle 退出的唯一判据；「只发一部分」只能是优先级/降级，不能是过滤）。
  4. **超时按件缩放 + 段末预算**（D-6）：`clamp(20s, bytes/4000Bps, 60s)`；`sync(budget_sec=)` 起跑前
     判剩余量并把单件超时夹到它（下限 5s）⇒ 90s 的段末 flush 不会被执行成 4×60s（否则
     `deliver_proc._stop_child` 直接 `terminate()`，D-1 末轮补发当场失效）。段末最新一轮排最前。
  5. **降级档（Q4 的自动版）**：带 `eval_rows` 的体没过去 ⇒ 下一拍只发 weights/opt/账本行，
     评估读数登记进 `owed_reposts`（落盘），靠 hub 既有 `duplicate` 分支补账 ⇒ **零契约改动**。
- **否决的备选**：① **拆四件**（`?parts=opt/metrics/eval`）—— hub 的 `weights_json` 必填（`?parts=opt`
  单发必 400）、按件记账（`delivered.json` 是按轮的）、同一 `it-NNN` 单写者都要一起改（评审 P1-3），
  而降级档已拿到同一收益的 80%；② **锚点 + `pending()` 改窄** —— 评审 P0-2：改窄会让非锚点轮
  「既不上传也不进抽屉」，且锚点（= eval 轮）恰好是最大的体，优先级方向存疑；③ **给 `eval_rows` 瘦身** ——
  白名单必须保留 `(iter,wver,stage,seed)` 去重键（`worker/eval_rows.py:390-401`），否则 400 行全落哨兵键
  只剩一行（评审 P0-5）；④ **worker 训完停打点（B3）** —— 本条已证明该形态不需要它，真要做先补读数。
- **被推翻的旧判断**：`remote/offline_boot.py` 那句「租约只在显式 release / TTL / **hub 重启**时消失」
  **是错的**（重启是复活）；已就地更正。`HTTP_TIMEOUT_SEC` 的注解「上传体 ~1.9MB」按实测（322KB/1.24MB）重写。
- **代价（诚实账）**：① 段末最多 2 次尝试/拍（原来是 4）——一条坏链路下对**小件**的追赶会慢一拍；
  ② 降级档让「评估读数」在坏链路下晚到（但它本来就会丢，现在至少不会拖住权重）；
  ③ 未做拆件/瘦身 ⇒ 抖动期 1.24MB 那档仍可能长期失败（只是不再挡住别的轮次）。
- **落点**：`remote/offline_deliver.py` · `remote/deliver_ledger.py`（新，LOC 预算搬出 `delivered.json`
  记账面，L1）· `remote/deliver_worker.py`（预算透传 + 状态面）· `hub/queue_offline.py`（`_dispatch_write`
  无锁内核 + 代次水位）· `hub/task_pack.py`（文档）· `tests/{remote,hub}/*` · `docs/nn/remote-transport.md §111`。
- **验收**：9 条新用例先在 `HEAD` 工作树（`git worktree add --detach`）跑成**红**，改动后全绿；
  `bun run pygate` 4829 passed / 2 skipped（Windows 专用回落）；`bun run check` 见 §9。

## §2026-10-22-goalnn-offline-realtime-anchor-and-slim — 实时体只发**锚点轮**、逐局评估行过白名单、`perGame` 走包（修订 §2026-10-21 的三条否决）

- **背景**：用户对 `plan/offline-deliver-stall.plan.md` §4 的推迟表提出质疑（「为什么不做？」），
  并点名要 ②锚点 + ③瘦身（`eval_rows` 与 `perGame`）；**①拆四件仍不做**（维持上一条的理由：
  hub `weights_json` 必填、按件记账、`it-NNN` 单写者三件要一起改）。
- **修订说明（重要）**：上一条把这 ②③ 记进了「否决的备选」，本轮**部分推翻**——推翻的是
  「不做」，**没有**推翻它们当初被否决的**理由**。两条旧理由都被遵守：
  - 「`pending()` 永不收窄」（D-3）**仍然成立**：本轮收窄的是**实时体的候选集**
    （`pending_anchors()`），`pending()`、抽屉、`status()`、idle 对账**全部照吃全量**。
  - 「白名单必须保住 `(iter,wver,stage,seed)` 去重键」**仍然成立**：白名单逐字包含它们
    （外加 `event`），并新加一条「同一批（瘦过的）行并两次 ⇒ 行数不变」用例。
- **决定**：
  1. **逐局评估行过白名单**（`EVAL_ROW_WIRE_KEYS`）：实时体只留「hub 去重要用的 5 键 + 控制台
     逐局表/聚合列（`win/cleared/outcome/ticks/kills/score/quality/playerDamageTaken/
     playerDeaths/puGotTank/powerUpsCollected/dims/time/node`）」。分析列（metrics v8–v13、
     分敌种画像、`moveHist/decisions/stopRuns/idleTicks`）**不上实时体**，但它们逐字留在
     `eval_log.jsonl` 里，由抽屉与离线包整份带走。`eval_summary` 行**不投影**（控制台指标表/
     门判/开课回执全认它）。
  2. **`perGame`/`perGameSecs` 不上实时体**（Q2 的答案）：`row` 里唯一随局数线性长的字段
     （34 局 ≈ 45KB）；hub 落它的唯一去处 `<课程>/it<N>/per-game.json` 本来就是「随包到达」的
     读数面。浅拷摘键，本地 `metrics.jsonl` 一字不动；`status()['pergame_deferred']` 报摘了几局。
  3. **实时体只发锚点轮**（`pending_anchors()`）：锚点 = 有 A 层评估读数的轮次（与
     `_eval_rows_for` 同源判据），间隔 = `eval_every`（D-2，不加新旋钮）；段末/收线**额外带最新
     那一轮**（D-1）。没有 A 层评估的腿（`eval_log.jsonl` 缺失/为空）⇒ **退回全量**（数据条件，
     不是旋钮：没有锚点时「只发锚点」= 一轮也不发）。子进程 idle 退出判据同步改成
     `pending_anchors()` 空 + 无重投。
- **代价（诚实账）**：① 非锚点轮的**训练账本行**不再实时到达 ⇒ 控制台指标表在两次 eval 之间
  不动，要等抽屉/离线包导入；② `perGame` 与完整逐局列同理（逐局弹窗在包到之前是空的）；
  ③ hub 侧 `resume_anchor` 的最新"同轮齐全"轮次可能落后 ≤`eval_every-1` 轮 ⇒ 中断重跑会重算那几轮。
  这三条都是「带宽换新鲜度」的显式代价，不是回归。
- **为什么现在可以做**：§111 修完之后失败不再卡住，于是「一次尝试背多少字节」成了唯一的余量；
  这三条把最坏一档从 1.24MB 压到「锚点轮 ~400KB / 非锚点轮 ~277KB」，且不新增任何协议分支。
- **落点**：`remote/deliver_wire.py`（**新**，L0：`EVAL_ROW_WIRE_KEYS`/`slim_eval_rows`/
  `wire_row`/`read_eval_iters`/`pick_anchors`——同一个 LOC 预算把腿本体顶到 1000 行）·
  `remote/offline_deliver.py`（`pending_anchors` 入口 + `status` 三个新键）·
  `remote/deliver_worker.py`（idle 判据 + 状态面三个新键）·
  `tests/remote/test_offline_deliver.py`（+6 条）· `tests/helpers/remote_dag.py`（`remote.deliver_wire: 0`）·
  `docs/nn/remote-transport.md §112`。
- **验收**：6 条新用例在 `HEAD` worktree 上 **4 红 2 绿**（另两条是护栏：去重幂等、无锚点退回全量），
  改动后 6 条全绿；`nn-training/tests/remote` 1437 全绿；`bun run pygate` / `bun run check` 见 plan §9.6。

## §2026-10-22-goalnn-offline-full-weights-backup — 打包出口用**注入的 `full_for`** 接上整份（复用 `<store>/.weights-standard/`），不改 `hub/deltas.py`、不搬模块

- **背景**：`plan/offline-full-weights-backup.plan.md` v3（评审 `plan/offline-full-weights-backup.review-bf.md`
  P0×5 / P1×7）。现场：y28-steady 的离线包里 232 份 `it-*/weights.json` **全是** 149KB 的 `BWD1`
  容器（`delta-i16`），本机一份都解不开 ⇒ 控制台只评估到 it63。
- **根因**：delta 只该活在「回传进程 ↔ hub 落地器」之间（`hub/deltas.py` 模块头），而云机产物目录
  存的就是**回传件**（`plan_run._weight_bytes` → `checkpoint`，`artifacts.py:405` 的行 fp 也是它）；
  三个打包出口（`artifacts.zip` / `LATEST.zip` / `write_deliver_zip`）直接拷字节 ⇒ 落地器没机会跑。
  `base_sha` 指云机本轮实际载入的 init ⇒ **只能在云机上落**。
- **决定**：
  1. **注入，不搬家**：`finalize(full_for=…)` / `write_deliver_zip(full_for=…)` / `package_pending(full_for=…)`，
     实现住 L6 的 `plan_run._pack_full_for`。**否决**「搬 `hub/deltas.py` → `remote/land_stage.py`」：
     `remote/artifacts` 是 L0 且被 `hub.store_offline`(L1) import，接落地器(L1) 就是向上边，
     连带 `remote.offline_deliver`/`deliver_worker`/`plan_run` 全要重编号（`tests/test_remote_dag.py`）。
  2. **复用已在盘上的 sidecar**：云机每轮开跑前就 `standard_weights_copy(prev_weights, store.root)`
     写 `<store>/.weights-standard/<sha16>.json`，而 `prune_caches` 只扫 `cache_root` 三棵内容寻址缓存
     （`download.py:452`）⇒ 历史轮打包 = 一次 `is_file()`；只有末轮现场落（基准走 `blob_root` 的
     `_cas_hit`，O(1)）。**否决** v2 的「`full-last` 当默认档」：既然命中 sidecar 几乎免费，
     分档的成本只剩包体，而段末只打一次包。
  3. **只准 `standard_weights_copy`，严禁 `ensure_standard_weights`**：后者对「叫 `.json` 却装着容器」
     的输入是**就地改写**（旁存 `.delta` + 覆盖 `.json`）⇒ 会同时破「产物根不可变」、改变 deliver 腿
     发出的字节、并让同轮重传撞 hub 的 409。
  4. **两态判据由布局所有者提供一份**：`weights_fps(root, it, prefer)` 生成器（`remote/artifacts`，L0），
     三个读者共用 —— `rows-fp`、`state-fp`（`deliver_zip._weight_fp_matches`）、`_row_for`
     （`offline_deliver`）。行里的 fp 语义**不变**（仍是回传件 sha）。
  5. **两版都进包**（整份顶名 + 原件旁存），与 hub 树逐字同形；包体每轮 +394,685B（裸字节，
     238 轮 ≈ +93.9MB，压缩率未实测）。**否决**「只替换原件」（省 1.6 倍但破坏同形，且体检拿不到原件对账）。
  6. **`LATEST.zip` 不落**（v2 的 W3 删掉）：每轮重写的便利件，加落地等于往训练热路径叠 I/O。
- **代价（诚实账）**：① 包体上界 +93.9MB（opt 也一起落，P1-3）；② 盘上已被清掉基准的轮次仍是原件
  （包照出 + 一行 WARN，不阻断）；③ 「任意历史轮」只覆盖当过 init 的轮（sidecar 没有清理路径，
  但盘可回收）；④ **notebook 那个「中途取回」cell 暂不注入**（`package_pending` 只做透传）：
  blob 根解析函数现成（`plan_handoff._blob_roots_of(work_dir)`），只是那条腿没接线（P1-5）；
  真正的顾虑是抽屉-only 那支不共享搜索预算（每次 `standard_weights_copy` 都是新 `_SearchContext`）
  ⇒ 失败路径会变成 O(n²) 扫读，而那是人点一下坐着等的。段末自动腿与「跑完再点」已覆盖
  （`package_deliverable` 复制的是 `finalize` 刚打的包）；要做先量墙钟（plan §7-Q5）。
- **落点**：`remote/artifacts.py`（`_round_entries`/`weights_fps`/`finalize(full_for=)`）·
  `remote/offline_deliverable.py`（`write_deliver_zip`/`_full_map`/`package_pending`）·
  `remote/deliver_zip.py`（`_weight_fp_matches`）· `remote/offline_deliver.py`（`_row_for`）·
  `remote/plan_run.py`（`_pack_full_for` + `blob_root`）· `tests/remote/test_pack_full.py`（新，5 例）·
  `docs/nn/remote-transport.md §113`。**`hub/*` 一个字节没动。**
- **验收**：5 条新用例绿（含「不给 `full_for` 逐字旧行为」「打包前后产物根逐字节相同」
  「落地包导入后 `rows-fp`+`state-fp` 都绿」「落地后 `_row_for` 仍认得出账本行」）；
  `nn-training/tests/{remote,hub,worker,common}` 全绿；`tests/test_remote_dag.py` 与
  `tests/test_python_loc_budget.py` 绿（`offline_deliver.py` 999/1000）。换机端到端、包体实测与
  W0 三项读数 **未做**（需现场），见 plan §5/§7。

## §2026-10-22-goalnn-notebook-pack-full-wiring — 「中途取回」cell 接上整份：`package_pending_full` + 落地失败连续 3 轮停试（修订同日 §2026-10-22-goalnn-offline-full-weights-backup 的「代价 ④」）

- **背景**：用户对 §2026-10-22-goalnn-offline-full-weights-backup 的「notebook cell 暂不注入」提出
  两条事实：① 云机上每一轮的**整份**权重本来就存在（`train_core` 写出、`_cache_produced_weights`
  进内容寻址缓存、`.weights-standard` 每轮把上一轮落成整份）；② 网络盘抽屉是**逐字节拷贝**、
  不是另一套格式。复核：这两条都成立（抽屉拷的是产物字节，而产物里 `it-NNN/weights.json` 是回传件
  —— 抽屉自己不带整份，但它拷的 `artifacts.zip` 带；这条差别记在 docs §114）。⇒ 没有理由不接。
- **决定**：
  1. **新模块 `remote/land_provider.py`（L2）** 是整份提供者的**唯一定义**：`provider()` 把
     `standard_weights_copy` / `standard_opt_copy` 包成打包侧的 `full_for(root, it)` 契约，
     weights 与 opt 各自独立成败；`blob_roots_of` 同处定义（`plan_handoff._blob_roots_of` 转发）。
  2. **契约从 `full_for(it)` 改成 `full_for(root, it)`**：`package_pending` 逐课程换 `src_root`，
     同一份提供者要能给多课程复用；三条出口（`finalize` / `write_deliver_zip` / `package_pending`）
     与 `plan_run._pack_full_for` 全部改到这一份。
  3. **notebook 入口 = `offline_boot.package_pending_full`**：交付面 `package_pending` 仍是 L0、
     定义唯一；boot 是 standalone（延迟 import 允许）⇒ 由它 import `land_provider` 并绑
     `blob_roots=<cfg.work_dir 推>`。notebook 引导格/raw 兜底/`sys.modules.pop` 三处名单加
     `land_provider.py`；老 notebook 缺它 ⇒ ImportError → 退回旧行为 + 一行说明（不抛）。
  4. **失败封口 `MAX_FAILURES=3`**：基准链断时逐轮扫会 O(n²)，而这是人点一下坐着等的 cell ⇒
     同一件连续失败 3 轮即停试（响亮一行），之后该件不再尝试（成功一次即重置计数）。
- **实测（W0-2，本机，40 轮 × 100k 参数 ≈391KB/轮）**：sidecar 全命中时纯落地 **0.01s**；
  整包 **2.84s**（71ms/轮）；无 sidecar、blob 全在 3.43s；**基准链断 2.16s**（40 轮，含约 2s zip）
  ⇒ 成本几乎全在 zip 压缩，落地不是瓶颈，O(n²) 已被封住。
- **否决的备选**：① 在 `offline_deliverable` 里默认落地 —— L0 import L1 是向上边
  （`test_remote_dag` 当场红）；② 只改 cell 文本就地拼提供者 —— notebook 文本是契约，拼装逻辑
  会漂；③ 不做失败封口只靠「人点」—— 断链时 238 轮逐轮扫读，等待不可估。
- **落点**：`remote/land_provider.py`（新，L2）· `remote/offline_boot.py`（`package_pending_full` /
  `_landing_provider` / `_load_land_provider`）· `remote/plan_handoff.py`（`_blob_roots_of` 转发）·
  `remote/plan_run.py`（`_pack_full_for` 改用 provider）· `remote/artifacts.py` +
  `remote/offline_deliverable.py`（契约 `(root, it)`）· `nn-training/ipynb/battle.offline.ipynb`
  （三处名单 + cell 入口）· `tests/helpers/remote_dag.py`（`remote.land_provider: 2`）·
  `tests/remote/test_pack_full.py`（+4：真 delta + blob 缓存落地 / 失败停试 / notebook 端到端 /
  模块缺失退化）· `docs/nn/remote-transport.md §114`。
- **验收**：`test_pack_full` 10 例全绿（含真容器落地与端到端一键包）；`test_remote_dag` /
  `test_python_loc_budget` / `test_source_text_assert_budget`（基线未抬）/ `test_reuse_ledger` /
  `test_offline_{notebook,deliverable_split,boot}` / `test_plan_run_split` 共 165 passed。

## §2026-10-22-course-batched-rollout-eval — 多课程排程：**按课分批 + 采集优先**（rollout 恒高于 eval，取代步级公平轮转）

- **背景（用户 2026-10-22 口径）**：单进程多课程 supervisor 的 `_pick` 是 R2c 定案的**步级公平
  轮转**（从上次选中的下一门课开始找第一个可跑的）⇒ 13 步队列逐步跨课交错：A 课的 `rollout`
  窗口里插进 B 课的 `eval_dispatch`/`eval_join`，也就是现场看到的「rollout/eval 全部混在一起」。
- **决定**：
  1. **按课分批**：选中一门课后把它**本 it** 的任务连续跑完再换课（`Supervisor._last_course`
     粘住；边界 = `_finish_round`（该 it 收尾）/ `WAIT` / 资源票被挡 / `ABORT`）。轮粒度下
     顺序仍为 a1,b1,a2,b2（旧用例不变）。
  2. **采集恒高于 eval**：新增任务族表 `worker/loop_tasks.KIND_FAMILY` + `task_family()`
     （`rollout`/`volume_topup` = `collect`，`eval_dispatch`/`eval_join` = `eval`，其余 = `step`）
     作为**唯一来源**；**没在分批时**挑课的族序 = collect → step → eval，族内仍按轮转序
     （末位课程不饿死）。
  3. **分批的唯一例外**：本课排在 eval 步、而别的课有 collect 可跑 ⇒ 本课让位——「始终」二字的
     落点。**eval 也只让位给 collect**，不让位给 `step`（否则别课的 PPO/落账会插进本课中途，
     分批就漏了；而采集要尽早回账，那是关键路径）。
  4. **被资源池挡住的课在 `_pick` 里直接跳过**（旧写法是选中它再由 `step` 返回 None）：分批后
     粘住的课会一直是首选，不跳过就变成两拍才给别的课一次机会（那是饿死，不是排队）。
- **否决的备选**：① 保持步级轮转（就是问题本身）；② 全局两段批（先把所有课的 rollout 跑完再跑
  所有课的 eval）——某课的 PPO/落账会被推到所有课采集之后，且与「跑完一课某 it 再下一课」冲突；
  ③ 采集无条件抢占粘住宿主的**非评估**步——把准备/PPO/落账无限推后，语料并没有更快到账。
- **代价（诚实账）**：轮粒度 + 显式 `cache_courses=1` 两门课时「轮间驱逐」现在真的会发生（两课
  不再同时处于轮内）；上限默认 = 开课数，生产不受影响。`tests/trainer/test_serve_wiring.py` 的
  P1 用例改成「a 在 WAIT 的轮内、b 建栈 ⇒ 宁可超预算也不抽栈」（与 2026-10-02 事故判据同义）。
- **落点**：`nn-training/worker/loop_scheduler.py`（`_candidates`/`_take`/`_pick`/`step`/
  `_finish_round`/`_abort`）· `nn-training/worker/loop_tasks.py`（`KIND_FAMILY`/`task_family`）·
  `nn-training/tests/worker/test_loop_scheduler.py`（原「步级公平轮转」断言 → 分批；+批次边界 +
  采集优先）· `tests/worker/test_loop_tasks.py`（族表）· `e2e/test_loop_supervisor_integration.py`
  （两条跨课顺序断言）· `docs/nn/training-stack.md §37`。
- **验收**：上述四份用例全绿（36 passed）；`bash tools/githook/nn-python-gate.sh` 全绿。

## §2026-10-22-sibling-episode-reuse — 并行课共享「同一局」：到同族课程的工作目录里直取产物，**不做缓存**

- **背景（用户 2026-10-22，两段口径 + 一条否决）**：① 现在经常同时开多条并行课（y30 四腿），用
  相同起始权重；起点权重与 level 相同的课，**it0 评估**局局相同 ⇒ 可复用。② 若这些课的
  `paired_rotate_seed` 也相同，那么同一次迭代抽到的 **rollout** (stage,seed) 也相同 ⇒ 语料可复用。
  ③ 并行课延迟开课时前面课程的语料可能已被 `keep_iters` 清空 —— 找不到就自行重采。
  ④ **不做缓存**：缓存会让内存/硬盘爆炸。
  （曾把这件事误读成「it0 eval ≡ it1 rollout」——两侧种子池/执行模式不同，那种互相当账是伪造读数；
  复用轴是**跨课程**，不是跨迭代。）
- **决定**：
  1. **复用面 = 盘上已有的产物**（`nn-training/worker/sibling_reuse.py`），零新增存储：无缓存根、
     无索引、无 LRU。
  2. **兄弟课判据** = `episode_identity_fp` 相同（`biz/corpus_fp.episode_identity_fp` = 语料身份
     减去 `reward`；与 `corpus_identity_fp` 共用唯一构造点 `_identity_payload`，现有指纹逐字节不变），
     排除自己，上限 `MAX_SOURCES=100`（用户 2026-10-22：复用面就是全库同族，扫不全等于白算）。合法性：per-tick 奖励由训练侧读 `metrics.npy` 现算
     ⇒ 同一局 obs/action/metrics 与奖励公式无关，换课重算奖励是**精确**的。
  3. **局判据**：rollout 只认目录名 `rl_s{stage}_seed{seed}` + manifest `wver` **逐字节同**
     （`paired_rotate_seed` 不必单独比：种子流不同就撞不上同一对），且同波有 `_rl_report.json`、
     有 `metrics.npy` 且**无** `reward.npy`（预置奖励是别的课算的，不借）；eval 只认同
     `(wver16, stage, seed)` 的 `event:"eval"` 行（带 `source` 的 B/C 批行不取）。
  4. **取用方式**：rollout 命中把那一局的 shard 拷进本课 `w{idx}/rl_s*_seed*` 并 **D14 改戳成本课**、
     写 `reused.from`（派发前过滤那条腿用 `rw{i}/`，避开本机槽正在写的 `w{i}`）；eval 命中把那一行
     **移植**进本课账本（只换 iter/节点/时间/来源，读数逐字段保留，`reusedFrom` 记出处）。
     找不到 ⇒ 本课自采（不降级、不伪造）。
  5. **只替代计算、不替代账**（R6）：shard 照落本课 traj、照被 `completed_pairs` 数到；评估行照落
     本课 `eval_log.jsonl` 并进 `landed` 收工口径；`node=reuse` 不入采样机健康账（它不是机器）。
     命中局 `elapsedSec` 只算拷贝墙钟，源局耗时另列 `reusedSourceElapsedSec`。
  6. **接缝**：① `trainer/dispatch` 在**派发前**调 `reuse_rollouts`——命中局出队（不派节点、不重算），
     shard 拷进本课 traj 后由 `resumed_manifests` 从盘上收进聚合（与「重启撞上已落盘局」同一条路），
     所以没本机槽（`local_slots=0`）的集群轮也不重算；② `trainer/queue_local.run_one`/`run_local_rollout`
     用本轮索引（`reuse_index`，同 `wver` 只建一次）做同样的直取；③ `trainer/eval_dispatch`
     （`record(row=…)` 支持整行移植）。
  7. **开关**：`--reuse-siblings 0` / env `NN_REUSE_SIBLINGS=0` = 关（逐字节旧行为）；测试的全局
     隔离夹具把它钉成 0（否则单测会读操作员在跑的课）。
- **否决的备选**：① **内容寻址缓存**（先写一份、以后取）—— 用户否决：内存/磁盘占用不可控；
  ② 引用源课 traj 路径而不拷贝 —— `keep_iters` 会清、跨目录不稳；③ 用完整 `corpus_identity_fp`
  做判据（含 reward）⇒ y30 四腿永不命中，等于没做；④ 只按 `(wver, stage, seed)` 手写判据 ——
  漏关卡/难度/决策粒度就会拿别人的读数当自己的。
- **代价（诚实账）**：每轮扫一遍候选工作目录（按目录名匹配，命中才读 manifest；有 `MAX_SOURCES`
  上限）；命中一局 = 一次 shard 目录拷贝（本来就落盘）。奖励不同的腿在 PPO 首步后就分岔，it1 起
  rollout 自然 hit=0；跨机器取产物（节点集群）**未做**。
- **落点**：`nn-training/worker/sibling_reuse.py`（新）· `biz/corpus_fp.py`（拆 `_identity_payload` +
  `episode_identity_fp`）· `trainer/queue_local.py` · `trainer/dispatch.py` · `trainer/eval_dispatch.py` ·
  `worker/cli.py`（`--reuse-siblings`）· `tests/conftest.py`（测试隔离）· `tests/helpers/remote_dag.py`
  （新模块 **L4**：D14 穿模要拿 L3 的 `worker.cmd`）· `tests/worker/test_sibling_reuse.py`（13 例）·
  `tests/trainer/test_rollout_dispatch_resilience.py`
  （派发前过滤用例）· `tests/trainer/test_eval_dispatch_resilience.py`（借行不派发用例）·
  `docs/nn/training-stack.md §38` · `plan/episode-reuse.plan.md`。
- **验收**：`tests/worker/test_sibling_reuse.py` 13 passed；`tests/trainer`（eval/rollout 子集）47 passed；
  `bash tools/githook/nn-python-gate.sh` 全绿；`bun run check` 绿。

## §2026-10-22-fixed-offpool-eval-seeds（2026-10-22，用户口径「in-loop eval 改固定池外种子、不轮换」）

- **背景**：A 层 in-loop eval 原是「锚点 50（固定）+ 轮转 50（每 3 轮换段）」双轨。轮转段每轮换一批局
  ⇒ ① 不同课程（哪怕同 level）各轮 it 的胜率/击杀/耗时**不在同一批局上**，逐点不可比；② 轮转轨要
  「同 it 间隔不是 3 的倍数」才转得起来（`--eval-every 3` 直接退化成固定段）；③ 旧池 860001 起落在
  rollout 抽签区间 `[1, 2**30)` 内 ⇒ 评估局可能与训练局撞同一 `(stage, seed)`（读数不独立）。
- **备选与否决**：① **保留轮转、只再加一段跨课程对齐的固定种子** —— 否，「每轮换局」本身就是不可比的
  来源，且轮转的存在理由（防记忆化）在**池外**语料下不成立：训练空间之外没有可被梯度记住的局。
  ② **每次 run 随机抽一个大基址** —— 否，读数要跨 run / 跨机器复现；随机基址让「同一批语料」事后无从核对。
  ③ **把 B/C 层（梯段批 / 判据批）语料一起迁** —— 否，那层有自己的历史读数与段语义
  （`trainer/batch_plan.py::EVAL_SEED0 = 860001` 有单测钉死），迁移 = 另起一次实验，超出本次口径。
- **决定**：A 层语料改为**固定池外池**（`worker/eval_track.py::EVAL_SEEDS_BASE = 2_000_000_001`，200 个；
  `2**30 < base < 2**31` ⇒ 与 rollout 抽签区间永不相交、且不碰 32 位有符号假设），且 `a_eval_seed_list`
  **不含 it**（`EVAL_SEEDS[:n]`；`n=50` 翻成日常档 100 ⇒ 成本与精度与双轨总量持平）。双轨连同
  `anchor_wr` / `rotor_wr` / `overfit_gap_pp` 与过拟合报警一起退场；门改读 `winRate`（固定池 ⇒ 该读数
  本身就是逐点可比的读数）。B/C 层语料不动，与 A 层分属两个种子空间（`eval2g` vs `eval860k`）。
- **违反后果**：拿「每轮换局」的读数去比不同课程的 it ⇒ 差异里混着卷面难度，结论必错；把评估种子放回
  `[1, 2**30)` ⇒ 评估与训练可能撞同一局，读数不独立。
- **代价（诚实账）**：锚点 vs 轮转那个**过拟合探测器**随双轨一起退场（它测的是「熟题分高、生题分低」，
  池外固定池下没有第二批局可比）；留下的是**选择噪声**（同一批 100 局上反复选 ckpt ⇒ winner's curse），
  靠 100 局（SE≈5pp）+ 配对比较 + 逐轮同语料来控。
- **全文**（背景 / 备选与否决 / 落地清单 / 验收）→ `docs/nn/training-stack.md` §39

## §2026-10-23-goalnn-anchor-full-rounds（2026-10-23，用户口径「首轮和 eval 轮发整份 weight 和 opt，其它轮发 delta」）

- **背景**：云机腿每轮的 opt 一直是**整份**（~509 KB 线上），占整轮 637 KB 的 **79%**；weight 早就是
  delta（127 KB）。opt 的 delta 容器（`BOD1`）早就存在（published 腿在用），离线段只是没有「什么时候
  发整份」的判据。500 轮 / `eval_every=5` 的账：现状 311 MB，本形态 ~198 MB（−36%），磁盘反而
  615→154 KB/非锚点轮。顺带发现一个漏传：补传体把 `perGame` 摘了，而它是云机逐局明细的**唯一**实时通道。
- **决定**：形态在**写盘那一刻**定（`remote/round_forms.RoundForms`，L2），判据唯一 = 「首轮 ∪
  `it % eval_every == 0`」（`deliver_wire.is_full_round`）。锚点轮：弹 `w_fmt`（worker 回整份）+ opt 落地成
  整份；其余：`opt.tar` 就地写成 `BOD1` 容器。基准 = **盘上最近一份整份**（不是上一轮）。补传体
  `wire_row(..., keep_per_game=True)`。
- **否决的备选**：① **基准 = 上一轮回来的那份整份** —— hub 侧 sha 永远对不上（重打 tar 随 mtime 变），
  第一轮 delta 之后整条链解不开；② **容器改名成 `opt.delta`** —— `opt.tar` 是 deliver 腿唯一的字节源，
  改名 = 补传一个字节 opt 都不发；③ **在 `ArtifactStore` 里直接 import 落地器/packer** —— L0 向上边；
  ④ **`eval_every=1`（`CourseSpec` 缺省）也装** —— 每轮都是锚点，权重退回整份 **净亏 ~267 KB/轮**；
  ⑤ **接上 `deliver_parts.full_parts_for`**（投递侧按档位展开）—— 它会把非锚点轮重新展开成整份，
  本决策的省量当场归零且无人察觉（今天它没有生产调用点，两个 docstring 已钉死）。
- **违反后果**：少写整份 ⇒ eval 轮不能单独装载（`resume_anchor` 只认整份，门判据与控制台读数都指向
  那一轮），链一断就是整段；用「重建出来的字节」当基准 ⇒ hub 拒绝落地，整段只能靠 sweep 人工补。
- **代价（诚实账）**：非锚点轮解回要多走一步（基准在锚点，不在上一轮；实测 152→154 KB/轮，可忽略）；
  写盘时多一次 pack（两个 tar 的 `torch.load`，~15ms/轮，只在 4/5 的轮）；非锚点轮 hub 落地出的整份
  带 ≤4 轮 f16 残差（~1e-4）。**本轮没做**：云机真跑验收（30 轮 + hub 可装载 + ≤70% 体量）——本机
  没有云机腿，上线后首个课重测。
- **落点**：`nn-training/remote/round_forms.py`（新，L2）· `remote/deliver_wire.py`（`is_full_round` /
  `note_opt_wire`）· `remote/artifacts.py`（`after_write` 注入点）· `remote/plan_handoff.py`（装配）·
  `remote/plan_run.py`（按轮设形态位）· `remote/offline_deliver.py`（S6 + 形态读数）· `remote/delta_files.py`
  （`form_of`）· `hub/store_offline.py`（账本 `opt_form`）· `tests/helpers/remote_dag.py`（层号）·
  `tests/remote/test_round_forms.py` · `tests/remote/test_offline_deliver.py` · `tests/hub/test_offline_backfeed.py` ·
  `plan/anchor-full-rounds.plan.md` · `docs/nn/remote-transport.md §115`。
- **验收**：`tests/remote/test_round_forms.py` 16 passed；`tests/hub/test_offline_backfeed.py`、
  `tests/remote/test_offline_deliver.py` 子集、`tests/test_remote_dag.py` 全绿；`bun run check` 绿。

## §2026-10-23-goalnn-hold-stale-fallback（2026-10-23，用户口径「接管掉线应该是自动的」）

- **背景**：y31-null（`rollout_src=node` + `target_transitions: 24576`）在云机 hold 静默 113 分钟后，
  每轮 `_remote_iter` 的互斥守卫 `SystemExit` ⇒ **整门课下线**（`loop_serve` ABORT），只有人改
  `rl-config.json` 才能回来。采集裁决点（`worker/loop_round.resolve_collect_mode`）只有
  「被接管 / 整轮上云 / 本机采样」三格，没有「云机曾经接管、现已掉线」，也没有「node 这条路对本课
  结构性不可用」。
- **决定**：补两格自愈，且**降级只是一个正交标记**：`lapsed`（**仅** hub 显式 `state=="stale"`）⇒
  `local` + `fallback="hold_stale"`；`node_volume_conflict`（`node` ∧ `target_transitions>0`，
  纯配置事实，与 hold 历史无关）⇒ `local` + `fallback="node_unavailable"`。`collect_mode` 仍只有
  三值——`_node_rollout = mode != COLLECT_LOCAL` 决定本机跑不跑 PPO，降级轮必须回本机 PPO。
  配置冲突不再 `SystemExit`：`_remote_iter` 返回 `ROUND_SKIP`，由既有梯子（`RETRY_BACKOFF_SEC=30s`
  + `resolve_failure` 5 连击）收口；开课处只 WARN 不拒启（serve 每次启动都会开课，拒启=一重启就
  把在跑的课打下线）。可见性零新通道：日志一行 + `iteration.collect_fallback`（只降级轮写）+
  控制台 `collectFallback`（`rollout_src` ⊕ hub `hold.state`，且要求 `state` 真给过 ⇒ 新 `stateExplicit`）。
- **否决的备选**：① **新增第四个采集枚举**（`COLLECT_FALLBACK_LOCAL`）—— 打穿 `_node_rollout`，
  变成「本机采样 + 本机不跑 PPO」（iter 在涨、权重不变且不报错）；② **用控制台缓存
  `tmp/loop-control.json` 判掉线** —— 它是探 hub 时整份重写的缓存，「条目过期」只等于「控制台没探过
  hub」，trainer↔hub 断网而云机正常时本机开跑 = 双跑；③ **只做 `lapsed` 不做结构性那一格** ——
  交还/强制解除会清零 hold，`lapsed` 永不触发而 node 轮本机 build 不出 spec、云机也领不到 ⇒ 永久停摆；
  ④ **开课拒绝该配置（400）** —— 违反用户口径（逼人改配置），且会让 trainer 重启把在跑的课打下线；
  ⑤ **`ROUND_SKIP` 复用 `same_iter` 的 retry** —— 0 退避 ⇒ 毫秒级烧完 5 次再下线；⑥ **新开
  `CONFLICT_SKIP_LIMIT`** —— 第二份阈值，与既有梯子分叉；⑦ **降级后自动切回 node** —— 切回要在轮
  边界做而 hold 随时可夺；现状「云机取包即夺 hold ⇒ 本机下一轮 `COLLECT_HELD`」已把退让做对了，
  配置留给人（运行时覆盖，**不写回** `rl-config.json`）。
- **违反后果**：把降级塞进采集枚举 ⇒ 本机采样却不训（最难查的一类：日志全绿）；拿文件缓存判掉线 ⇒
  与活着的云机双跑（读数被两份采样混在一起）；只认 hold 历史 ⇒ 交还后的课永久停摆；`SystemExit`
  留在 `_remote_iter` ⇒ 一次配置错 = 整课下线，必须人工干预。
- **代价（诚实账）**：hold 只在**轮边界**问一次 ⇒ 云机中途夺回时本机最迟本轮结束才退让（本轮已采
  shard 归本机批，账本可区分；不承诺中途停）；控制台**看不到**结构性那一格（不读课程 jsonc 的
  `target_transitions`），只能靠日志 + 账本归因；`node_volume_conflict` 是**能力判据**不是真理——
  `iter_job.build_iter_spec` 将来接上 volume 时返回 `""` 即自动解除。
- **落点**：`worker/loop_round.py`（`CollectPlan`/`resolve_collect`/`ROUND_SKIP`）· `trainer/loop_hold.py`
  （`hub_hold_doc`/`hub_hold_state`/`course_hold_facts`）· `trainer/loop_transport.py`（`node_volume_conflict`）·
  `trainer/{loop_round_steps,loop_remote_drive,loop_runner,loop_lifecycle,loop_serve}.py` · `worker/events.py` ·
  `dashboard/src/server/api/overview.ts` + `web/view/{course-overview,course-status}.ts` · 用例见 
  `tests/{worker,trainer}/` 五份 + `dashboard/tests/server-api-overview.test.ts` · 全文 → `docs/nn/remote-transport.md §116`。
- **验收**：`bun run check` 2617 pass / 0 fail · dashboard typecheck + 1799 pass / 0 fail · nn 子集 70 passed ·
  e2e supervisor 11 passed · ruff + mypy ✓ · `bun run build` ✓。

## §2026-10-23-push-mode-notebooks（2026-10-23，用户口径「worker 注册一次，hub 主动来拉」）

- **背景**：两条链方向相反。`battle.cloudflared`（GPU）已是「hub 推 job、hub 拉结果」，缺的只是
  自动注册（登记靠人手把隧道 URL 抄进 `rl-config`）；`battle.offline` 是 pull（worker 领任务、
  worker 回传产物），大流量正好穿在用户说的「hub 入站隧道不稳定」那一侧。直接的修法是把离线链
  翻成 hub 拉取式 push：worker 只做一次很小的注册/注销，之后 hub 用自己的出站连接经 worker 稳定
  的隧道推任务包、拉进度/每轮产物/段末结果。设计全文 `plan/push-mode-notebooks.plan.md`，
  评审（5 个 P0 + 10 个 P1）已逐条写回该 plan。
- **决定**（本次落地 W0–W2；W3–W6 的语义也一并定下）：
  1. **能力键是唯一持久事实，`kind` 只是注册 wire 值**。`gpu_push` / `offline_push` 写进
     `rl-config.nodes[]`；`kind` 白名单 `sampler|gpu_push|offline_push`，不落盘。**`kind` 缺席
     ≠ `sampler`** —— 缺席时**一个能力键都不动**（老采样客户端每会话注册一次，把它当成声明会
     静默删掉控制台手工挂的 `gpu_push`）；只有显式 `kind` 才「先清全部能力键、再写本次那一个」。
  2. **push 节点的探活面是 `GET /ping`（Bearer），采样节点是 `GET /v1/ping`**，按 `kind` 分流
     （`distribution.push_node_ping` 新增）；注册应答的**三条 200 出口**（含最常命中的 `unchanged`）
     都回显 `kind` + `capabilities`，worker 用它区分「新 hub 听懂了」与「老 hub 静默忽略」。
  3. **注册客户端只有一份**（`remote/push_register.py`，从 `rollout_node` 下沉，同名 re-export）：
     采样 / GPU / 离线三条腿共用退避梯子（2→120s，`auth` 立即放弃）与 `manual_snippet`；
     `Registry.ensure()` 只在 url/authKey 变化时发请求，退出 `unregister()`。
  4. **node_id 按角色稳定**：`gpu-<env>` / `offline-<env>`（与 `rollout-<env>` 同口径，CFG 可覆盖）。
     不落 `/tmp` 的随机 hex —— hub 的 unregister **不删条目**，随机 id 会让节点列表随 VM 重建线性增长。
  5. **push 启动 = 代码优先**：`registry_url or hub_url` 的 `/code`（有界 `code_wait_sec`，缺省 600s）
     → GitHub clone → 最后才是 push-first（`allow_push_first` 可关）。push-first 打头会死锁：
     没代码 ⇒ 只有 bootstrap server；没登记 ⇒ hub 不知道 URL ⇒ 不推 job ⇒ code.zip 永远不来。
     **push 与 pull 分支拆开**：pull 仍是 1h 等 `/code`、401/403 直接 FATAL（非目标：pull 不变）。
  6. **离线 push 的 hub 侧是「拉」**：pump 线程按 `it` 拉 progress/round/final，ingest 与 HTTP 腿
     共用同一份函数（不 loopback POST）；**body 的权威是 assignment**（course/run_id 由 hub 校验，
     不一致 409 + `conflict`）；缺包 ⇒ 派发器调共享的「请导包」而不是跳过；outbox 每轮写**完整
     body**（不做服务端按需重建）+ 迟到 eval 行（rev+1）+ metrics 批，ack **带 rev**。
- **否决的备选**：① 继续 worker→hub 回传、只优化 hub 隧道 —— 正是用户说的不稳定方向；
  ② `kind` 缺省当 `sampler` —— 一次普通注册抹掉 GPU 能力键（P0-2）；
  ③ 新开 `remote/deliver_build.py` 抽 body 构造 —— 会把它顶到 L2、`offline_deliver` 升 L3 并级联
  到 `plan_handoff`/`run_loop`（`test_every_layer_number_equals_its_topological_rank` 当场红），
  而缺的只有 result body ⇒ 放进已有的 `deliver_parts`；
  ④ 第三份注册客户端 —— 三套退避梯子必然漂开；
  ⑤ push-first 当默认 —— 注册死锁；
  ⑥ hub 拉到 body 后 loopback POST 给自己的 `/offline/artifact` —— 大 body 二次复制、耦合端口/token；
  ⑦ ack 无条件删 `it<=upto_it` —— 迟到 eval 行没有落点；
  ⑧ 离线 push 默认 `mode="push"` —— 老 hub/未开 `--push` 会静默空转（v1 只做 opt-in）。
- **违反后果**：把 `kind` 缺省当声明 ⇒ 控制台手工能力键被静默清掉、GPU 派发器一台都看不见；
  push kind 打 `/v1/ping` ⇒ 每台 422（或都通），注册永远不成；push 走共享 `/code` 循环 ⇒ pull 从
  「等 1 小时」变成「10 分钟 FATAL」；`--outbox` 与 `--no-deliver` 互相抵消 ⇒ **没有报错**地一轮
  产物都不出去（训练照跑、`/state` 永远空、最后被 hold stale 判掉线）。
- **代价（诚实账）**：稳定 node_id 让「两台同角色并发」互相顶 URL（需要并发时显式给 `node_id`）；
  push 模式多一份 outbox 权重副本（稳态 1–2 轮，WARN 2GiB / 上限 4GiB）；冷启动 hub 还没导
  `code.zip` 时 `source=github` 是预期（两个源的 commit 可能不同，日志打 sha12 备查）；
  真机收益（换方向后大流量成功率）没有数值证据 —— 按用户口径采信云机房 cloudflared 稳定，未复测。
- **落点**：`nn-training/common/{protocol,distribution}.py` · `hub/admin.py` · `remote/push_dispatch.py` ·
  `remote/push_register.py`（新）· `remote/rollout_node.py`（改为 re-export）· `remote/notebook_runtime.py` ·
  `ipynb/battle.cloudflared.ipynb` · `tests/{hub,remote}/` 五份 + `tests/helpers/remote_dag.py`（`push_register`=L0）·
  全文 → `docs/nn/remote-transport.md §117`；设计 → `plan/push-mode-notebooks.plan.md`。
- **验收**：`bash tools/githook/nn-python-gate.sh`（ruff+mypy+pytest）· `bun run check` · `bun run build`；
  注册面/注册客户端/notebook 契约共 40+ 用例（含真 hub loopback）。W4–W6（worker 服务端、
  hub 派发器、韧性、观测 API）未落地；**W3 outbox 地基已落地** → `§2026-10-23-push-mode-outbox`。

## §2026-10-23-push-mode-outbox（2026-10-23，W3 落地：push 腿的本地 outbox）

- **背景**：push 模式没有「节点 → hub」的 POST（hub 经隧道来拉），补传腿的产物必须落成文件。设计全文
  `plan/push-mode-notebooks.plan.md` §6.4a（评审 P0-1/P0-3/P1-1/P1-3/P1-10）；本条只记**落地时新定 /
  收紧**的四处语义（其余按 plan 写）。
- **决定**：
  1. **`--outbox` 与 `--no-deliver` 的叠合有明确语义**：「只关 HTTP 补传，outbox 必须活」。
     `make_deliverer` / `open_run_context` 的 outbox 分支**先于** `hub_url/token` 早退与 `deliver`
     判定；`run_loop.main` 在 `--outbox` 下**不回填**任务包里的 `hub_url`。理由：两道闸原本各管一边，
     push 段两个都命中 ⇒ **没有报错**地一轮产物都不出去（训练照跑、`/state` 永远空、最后被 hold
     stale 判掉线）。用例要正反两面：body 真的出现 **且** 全程没构造过 `OfflineDeliverer`。
  2. **ack 带 rev，且「hub 看到的是旧版」= 不删**：`ack(upto_it, result, revs)` 只删 `revs[it] == 当前 rev`
     的 body；`revs` 里没有这一轮也不删（删了就是替 hub 假装收到）。`result=true` 才删 `final.json`。
     无条件删 `it <= upto_it` 已再次否决（迟到评估行的唯一落点会被吃掉）。
  3. **地板 `floor_it`**（落地时补的判据）：`it <= floor_it` 的轮不物化，值 = 派发时 hub 的 cursor
     （任务包的 resume 锚点）。**缺省 0 = 全部物化**：地板给高了的代价是**永久丢那几轮**，给 0 只是多发
     几份 hub 会按内容去重的重复体 —— 错的方向必须选不丢数据的那个。地板第一次写进 `state.json` 就定终身
     （重启不带参不会被冲回 0）。W4 的任务入口负责把 cursor 传进来（届时才接线，默认值语义不变）。
  4. **统计是每轮一份完整体 `metrics/itNNNN.json`**（`build_metrics_body` 的输出 `{run_id, rows, course?}`），
     不是 `.jsonl`：hub 侧拿它直接递给 `store_offline_metrics`（与 `?parts=metrics` 同一个入口）。`oversize`
     的轮不写 body 但**统计照写**（y31-null 的教训：没权重的轮连 row 一起丢 ⇒ 控制台整段空白）。
  5. **装配只此一处**：`deliver_parts.build_round_body` / `build_result_body`（`row_for` / `rounds_with_rows`
     也从 `OfflineDeliverer` 原样抽出）——HTTP 腿与 outbox 腿逐字节一致是**构造性质**（用例仍对账一次，
     防的是有人绕开装配器）。`deliver_outbox` 对 `deliver_parts` 用 **Protocol 注入**（不是 import）：
     import 会让它变 L2、把 `deliver_proc` 顶到 L4 并级联 `plan_handoff`/`run_loop` 重排；注入后它是 L1。
- **否决的备选**：① 无条件删 `it<=upto_it`（同一轮在 ack 后到达的评估行永久丢）；② 服务端按需重建 body
  （boot 版本与训练包版本会漂开，字节一致没有保证）；③ 按产物目录里「有什么发什么」= 每次任务重发全部历史
  轮（94 轮 × 1.8MB 压在计费会话的隧道上）；④ 给 `deliver_outbox` 升 L2（级联重排整张层号表，见上）；
  ⑤ 再写一份 `print` 当日志缺省（`tests/test_reuse_ledger.py` 会把逐字节重复的实现当场标红）。
- **违反后果**：`--outbox` 被 `--no-deliver` 抵消 ⇒ 一轮产物都不出去且**无报错**；ack 不看 rev ⇒ 迟到评估行
  永久丢、控制台读数整段空白；`floor_it` 给高 ⇒ 那几轮**永久丢**（不出现在任何一条腿上）；让 outbox 腿 import
  `deliver_parts` ⇒ 层号守卫红 + 依赖方向反转。
- **代价（诚实账）**：outbox 多一份临时权重副本（稳态 1–2 轮；WARN 2GiB / MAX 4GiB，超 MAX 由 worker 服务端
  停止接新单，当前任务照跑）；子进程降级到进程内时 body 构造（gzip+base64 ~1.2MB/轮）回到训练进程的
  GIL 上——这是既有 HTTP 腿同款的降级代价，不是新增。
- **落点**：`remote/deliver_outbox.py`（新，L1）· `remote/{deliver_parts,deliver_proc,deliver_worker,plan_handoff,run_loop,offline_deliver}.py` ·
  `common/protocol.py`（`OFFLINE_PUSH_OUTBOX_{WARN,MAX}_BYTES`）· `tests/{helpers/remote_dag,remote/test_deliver_outbox,remote/test_run_loop_outbox,remote/test_offline_deliver_proc}.py` ·
  全文 → `docs/nn/remote-transport.md §118`；设计 → `plan/push-mode-notebooks.plan.md` §6.4a。
- **验收**：`bash tools/githook/nn-python-gate.sh`（ruff + mypy + pytest 全量）· `bun run check` · `bun run build`；
  新增 19 个用例（13 outbox + 4 run_loop + 2 真子进程），其中逐字节对账 2 条、真子进程 2 条。

## §2026-10-23-push-mode-w4（2026-10-23，W4 落地：离线 push 的 worker 侧）

- **背景**：W3 给了「产物怎么落盘」，W4 回答「谁在服务、谁在训练」。新增
  `remote/offline_push_face.py`（L2 纯 HTTP 面）· `remote/offline_push.py`（L3 supervisor）·
  `remote/offline_push_task.py`（训练子进程入口）· `remote/push_tunnel.py`（L0 quick tunnel 三件）；
  `offline_boot.run_one_course` 加 `push_mode`；`run_loop/plan_run` 加 `--stop-file`。设计全文
  `plan/push-mode-notebooks.plan.md` §6（评审 P1-3/P1-9）；本条只记落地时**新定/收紧**的语义。
- **决定**：
  1. **`push_mode` 住进 `run_one_course`，不另写一条训练链**（P1-9 的原始动机就是「别让 push 分支
     成为半成品」）。它只换输入来源：包 = 任务包给的、锚点 = 任务包给的、outbox = 任务包给的 ⇒ 不探
     hub、不取包、不取锚点、不装 HTTP 打点、不打交付 zip。最容易出错的几段（两个产物根、半截目录闸、
     code.zip 引导、argv 装配）两条腿逐字共用。用例把「不探 hub」钉成**抛异常的替身**（漏接一条就是
     一次静默出站请求，而 P0-1 的病根正是「静默」）。
  2. **一单一个 outbox**（`<work>/tasks/<task_id>/outbox`）。所有任务共用一份会让 A 课的轮留在 B 课
     的 `state.json` 里，hub 按 `it` 拉就拿到别人的权重 —— 串课不可能发生由**目录结构**保证。
  3. **受理前五道闸**：`Content-Length` 有界（2GiB，超限 413 且不落半截）· 防 zip-slip（绝对路径 /
     `..` / 符号链接一律拒）· `proto == offline_push_v1` · `pack_sha256` 逐字节对账 · 包名课程 ==
     `task.course`。整包校验通过前 `tasks/<id>` 根本不存在（先落 `incoming/` 再 rename）。重投同一
     `task_id` = 202 duplicate（hub 超时重试不重复 spawn）。
  4. **ack 只有一个实现**：从类方法抽出 `deliver_outbox.ack_outbox()`（模块函数）。服务进程不跑补传
     腿（腿在训练子进程里），只能读同一份 `state.json` —— 两处各写「删哪些」就是两套 rev 语义。跨进程
     **刻意不锁**：唯一竞态是「子进程刚写的新轮还没进服务进程账本」，那轮会在 `pending()` 重现并重新
     物化（幂等，代价一份重复 body）；反向顺序（旧快照覆盖新账本）才会丢数据。
  5. **supervisor 只装配、训练在子进程**。状态面（`/state` 是 hub 的版本门与 busy 判据）必须一直答得上
     话，而训练会吃满 CPU、注册进度钩子、解 `code.zip`；一次 OOM/`SystemExit` 不该把状态面一起带走。
     `busy` = 在跑 **或** 跑完但 final 未 ack；`result=true` 的 ack 才收回 `idle`。
  6. **取消 = stop 文件 + 轮边界**（`plan_run._drive` 在本轮开工之前查，先于预算闸）：已落盘的轮一条
     不丢、当前轮不成半截，`state="cancelled"` 随 final 回传（hub 据此 release/换段，不当失败）。
     supervisor 写 stop 后有界等 120s 再 terminate。
  7. **notebook 的 push 代码来源必须早于 `_load_boot`**：push 要完整仓库代码（4 个引导文件不够 import
     `remote.offline_push`）—— hub `/code`（有界 `code_wait_sec`，401/403 转下一源）→ GitHub clone →
     本机任务包 `code.zip`，全失败 FATAL；`mode` 缺省仍是 `pull`（旧路径逐字节不动）。
  8. **quick tunnel 三件下沉 `remote/push_tunnel.py`(L0)**：GPU push 的 `run_push_worker` 改为调用同一份
     （安装/起隧道/取 URL 只此一处）。代价 = 那条腿的假钟要绑 `push_tunnel.time`（用例已按新家改）。
- **否决的备选**：① 另写 `run_push_course`（与 pull 腿逐项漂开，P1-9 的原病）；② 共用一份 outbox
  （串课）；③ 训练跑在 supervisor 进程的线程里（状态面随训练一起死）；④ 受理时先建 `tasks/<id>` 再校
  验（坏包会变成一单「已受理」）；⑤ ack 留在类方法里、服务端另写一份文件删除判据（两套 rev 语义）；
  ⑥ 把 face 内联进 notebook cell（不可测，且跨端契约没有类型/测试面）。
- **违反后果**：共用 outbox ⇒ 串课（权重接到别的课程账本）；受理前不校验 sha256 ⇒ 坏包跑成一段
  「跑完了但没有产物」；ack 不看 rev ⇒ 迟到评估行永久丢；训练在 supervisor 进程 ⇒ 一次崩溃把
  `/state` 一起带走（hub 只能判掉线）；push 代码来源晚于 `_load_boot` ⇒ ImportError 或半成品引导。
- **落点**：`remote/{offline_push_face,offline_push,offline_push_task,push_tunnel}.py`（新）·
  `remote/{offline_boot,run_loop,plan_run,plan_handoff,notebook_runtime,deliver_outbox}.py` ·
  `common/protocol.py`（`OFFLINE_PUSH_PROTO` + 七个端点 + 2GiB 上限 + 三件名）·
  `ipynb/battle.offline.ipynb`（CFG/代码来源/push 分支/markdown）·
  `tests/{helpers/remote_dag,remote/test_offline_boot_push,remote/test_offline_push_face,remote/test_offline_push_supervisor,remote/test_offline_notebook,remote/test_notebook_runtime}.py` ·
  全文 → `docs/nn/remote-transport.md §119`；设计 → `plan/push-mode-notebooks.plan.md` §6。
- **验收**：`bash tools/githook/nn-python-gate.sh`（ruff + mypy + pytest 全量）· `bun run check`；
  新增 37 个用例（11 push_mode 接线 + 17 真 HTTP 面 + 7 supervisor/子进程 + 2 notebook 契约）。
  W5（hub 派发器）与 W6（韧性/观测）未落地；worker 服务端与训练入口的语义已在 plan 就位。

## §2026-10-24-push-mode-w5（2026-10-24，W5 落地：hub 侧离线 push 派发器）

- **背景**：W4 立在 worker 侧（HTTP 面 + supervisor + 训练子进程 + `push_mode`），W5 补方向相反的一半：
  hub 用自己的**出站连接**去离线 worker 的隧道派单、按轮拉产物。新增 `hub/offline_push.py`(L3 派发器) ·
  `hub/offline_push_assign.py`(L0 在途账本) · `hub/offline_ingest.py`(L1 共享入账面) ·
  `remote/offline_push_client.py`(L0 客户端)；接线 `hub/boot.py --offline-push` +
  `GET /admin/offline-push-workers`。设计全文 `plan/push-mode-notebooks.plan.md` §6.6/§6.7（评审 P0-3/P0-4/
  P0-5/P1-7）；本条只记落地时**新定/收紧**的语义。
- **决定**：
  1. **登记表复用 `PushWorkers`，版本门挂在 `/state` 上（P1-7）**。子类只换 `capability="offline_push"`；
     在线/忙闲/热重载/退避/节拍循环（`run_tick_loop`，从 GPU 派发器抽出）全部同一份实现。多出来的
     唯一一件事是 `probe_proto`：`/offline/push/state` 的 `proto` 必须是 `offline_push_v1`，`proto_ok`
     三态里派发只看 `True`。版本门**不能**挂 `/ping`：旧 worker 的 `/ping` 逐字正常，只有新端点 404。
  2. **入账面抽成 `hub/offline_ingest.py`，两条入口共用**（云机 POST 与 push 拉取）。校验（首写锁定 /
     指纹 / 409 冲突 / 评估行并入）与「段末盖章 + 滚下一段」是事故高发面，不许一条有一份；HTTP handler
     只剩「鉴权 → 读 bounded body → 定位课程」。
  3. **权威 = assignment，不是体（P0-5）**。push 路径显式传 `run_id`：体自报的 `run_id`/`course` 有值且
     不符 ⇒ 409 + `conflict`，**不落盘、不推进 cursor**，并取消 worker 上那一单；体缺 `run_id` ⇒ 用
     assignment 的（权威补位，旧 outbox 没这个键）。
  4. **在途 assignment 落盘**（`<traj_root>/offline-push.<port>.json`，端口隔离）。hub 重启后
     `_resume_pumps` 只做「重新 claim（换 token）+ 继续拉」，**不重新上传任务包** —— 重派会换
     `task_id`，worker 已跑完的轮次就成孤儿（没人 ack，最终撞 outbox 容量闸）。存 `cursor_it` 与
     `revs`：后者让 hub 看得出「我入的是 rev=0，worker 上现在 rev=1」（迟到的评估行）。
  5. **缺包 ⇒ 请导包，不是跳过（P0-4）**。派发器接 `begin_pending_export` + `trigger_pack_export`
     （与 claim 缺包分支同一份实现）；`stale_reason` 的旧包单列一条「请重导」腿（旧包比没包更危险）。
  6. **每单一条 pump 线程 + 自带 ack**。几十 MB 逐轮 body 不阻塞调度拍；每拍最多拉 8 轮；心跳（60s）
     **只续 TTL**，活性仍只认 `progress_offline`；ack 只看 `rev`（`ack_outbox` 的语义，迟到评估行不删）。
  7. **段末 final 走同一条入账腿**：`final_ready` ⇒ 拉 result → `ingest_offline_result` → release →
     ack(result=true) → 丢账本；worker 报 `failed/cancelled/done` 而无 result ⇒ 先收完已完成的轮再收摊。
- **否决的备选**：① 让 worker 反向 POST 到 hub（push 的全部动机是免入站，评审 P0-1）；② 在 pump 线程里
  直接调 `store_offline_*`（两条入口两份校验，P0-5 的入口）；③ 把版本门挂在 `/ping` 的字段上（旧 worker
  应答正常 ⇒ 门形同虚设）；④ hub 重启后重传任务包（换 `task_id` ⇒ 孤儿 outbox）；⑤ 一个 `acked_it` 数字
  代替 `revs`（表达不了「同轮 rev+1」）；⑥ 缺包跳过、等人工导包（push-only 部署里那门课永远没有包）。
- **违反后果**：pump 拉体阻塞调度拍 ⇒ 所有课一起停摆；不校验 assignment ⇒ 串课/串 run 的权重静默入账；
  ack 不看 rev ⇒ 迟到评估行永久丢；缺包静默跳过 ⇒ 该课永不推进；重启不续跑 ⇒ 几十轮产物成孤儿。
- **验收**：`bash tools/githook/nn-python-gate.sh`（ruff + mypy + pytest 全量）绿；新增 12 个用例
  （7 派发器 loopback/恢复/版本门/缺包/冲突/权威 + 5 assignment 账本）。
## §2026-10-24-gpu-push-ahead（2026-10-24，用户指令「让 GPU push 允许在 worker 忙时预推下一份 job，利用 worker 侧现有接单队列消除传输空转」）

- **背景**：push 腿的派发只挑 `busy=false` 的机器，而 `run_one` 要把 payload（3–10MB，慢链路几十秒）
  传完，worker 才开始跑下一份 ⇒ 每次换 job 都是一段「传完才开算」的 GPU 空转。worker 侧本来就有
  一条有界接单队列（`remote/worker_server` 的 `WORKER_QUEUE_MAX=8`），只是正常调度从不提前用它。
- **决定**：
  1. **预推只在「一个空闲机都没有」时发生**（`pick_push_worker(..., ahead=N)` 第二轮）：忙机若自报
     `queued < ahead` 且 `在飞 < concurrency + ahead` 才接。顺序口径（注册序、空闲优先）逐字节不变。
     缺省 `PUSH_AHEAD_DEFAULT=1`，`hub-server --push-ahead 0` 是回退档。
  2. **备份副本永不预推**：备份是保险不是吞吐，提前推一份备份 = 提前烧一张 GPU 的可能。
  3. **worker 必须能回答「我在队里」**：`WorkerServerState.get()` 认 `_pending`，`/job/{id}/result` 对
     排队单回 202 `{"status":"queued"}`。否则 hub 收到 404（`_await_result` 把它读成「这台不要这份活」）
     ⇒ 当场回落换台，预推白传还把健康 worker 记进 avoid。`submit_job` 因此多回一个 `status`（纯加法）。
  4. **两把时钟分开**：`computing_at` 只在第一次看到 running 时打（否则 `job_priority` 在
     `STRAGGLER_SEC=180s` 后把排队读成掉队、给排队单开多余备份）；「推送后无结果」预算从开算起算，
     排队另有一把 `queue_deadline`（同 `timeout_sec`）且只在「最后一眼还在队里」时生效 ——
     采样间隔跳过 running 那一拍的 job 不被队龄误杀，隧道断了的排队单仍被兜住。
  5. **一拍内的名额必须就地累加**（`_dispatch` 的 `counts`）：`_snapshot` 只回答拍开始时，不就地记账
     多课程会全落到第一台机器上（`concurrency=1` 也能一次收三份——预推会放大这个错）。
- **否决的备选**：① 忙机排第一轮（多课程时同一台吃掉全部课程，落点口径变了）；② 在 worker 侧对排队单
  继续回 404、让 hub 重推（几十 MB 白传 + 误拉黑健康节点）；③ 预推时照旧打 `computing_at`（排队被读成
  掉队，调度器自己制造备份副本）；④ 用固定 grace 代替排队时钟（没有判据，慢链路与长 job 都会被误杀）；
  ⑤ 一次给每台排满队列（`WORKER_QUEUE_MAX` 是给多课程共享留的，预推吃满就等于换 job 前先堵死它）。
- **违反后果**：退回 409/404 语义 ⇒ 预推变成「白传 + 回落」的抖动源；提前打 `computing_at` ⇒ 排队单被
  判掉队、双开 GPU；排队时间计入无结果预算 ⇒ 长 job 后面的排队单被误杀、重派到别的机器再跑一遍。
- **落点**：`common/protocol.py`（`PUSH_AHEAD_DEFAULT` + `pick_push_worker(ahead=)`）·
  `remote/push_dispatch.py`（`push_ahead` / 就地计数 / `_mark_started` / `_await_result` 两把时钟）·
  `remote/worker_server.py`（排队单可读）· `remote/push_client.py`（`status`）· `hub/boot.py`（`--push-ahead`）。
  全文 → `docs/nn/remote-transport.md §121`。
- **验收**：`bash tools/githook/nn-python-gate.sh`（ruff + mypy + pytest 全量）· `bun run check`；
  新增 3 个用例（纯函数预推四闸 + 真 worker_server 的预推闭环（假时钟）+ 回退档）+ e2e 排队单可读性断言。
## §2026-10-23-kickstart-v6-ref-cache（2026-10-23，y32-nohp / y32-tempo 两腿 it1 各 5/5 连炸后的修复）

- **背景**：两条腿的每个 PPO job 都在 `grad` 段报**同一个**确定性错误
  `ValueError: too many values to unpack (expected 3)`（内容决定性 ⇒ 重领同一份字节炸同一处，
  hub 5/5 后 `aborted`）。根因：B1 v6 落地（`2f40560e`）把 `ppo_update` **主循环**迁到 4-tuple
  （`mv, fr, hd, val = out`），但漏了**同一函数里的另外两处**：
  ① `ref_cache` 预计算段 `_rm, _rf, _ = ref_model(...)`（`worker/ppo/engine.py:480` 旧号）——
  v6 ref 返回 4 头，解包当场炸；② demo 混批段 `_dm, _, _ = model(...)`——v6 + `demo_bc_coef>0`
  同样炸（潜伏，当日未触发）。v6 落地后 y26/y27/y28/y29/y30/y31 全部 `kickstart_ref: false`
  ⇒ ①**从未被执行过**，直到 y32 是第一对「v6 起点（`y31-fire.it370`，`arch.actionV6=true`）+
  `kickstart_ref: true`」的腿。日志侧指纹：`kickstart ref 已加载` 之后**没有**
  `[ppo] kickstart ref 预计算完成`（该行在预计算循环之后）⇒ 死在预计算，不在训练步。
- **决定**：① 头数判据用 `len(ref_out)`（**不**读模型属性：`DataParallel` 包装下属性代理不可靠），
  并与数据的 `a_hold` 是否存在**交叉校验**，不同源 ⇒ `ValueError`（含「不同源」字样）；
  ② 掩码切片随之分派：v6 `[move4|fire3|hold2]`（`MOVE4_DIM`/`FIRE3_DIM`），legacy `[move5|fire2]`；
  ③ v6 的 kickstart KL **三头都锚**（`hold` 进 KL），legacy 路径逐位不变；④ demo 段取 `model(...)[0]`。
- **否决的备选**：① **把 y32 两腿的 `kickstart_ref` 关掉**（绕开而非修复，且 kickstart 正是这两腿
  与 y31 的唯一差别之一）；② **按 `ref_model.action_v6` 判**（DataParallel 包装后属性代理是否可用取决于
  torch 版本；`len(out)` 是 forward 的直接事实）；③ **只改解包、不动切片**——v6 ref 的 fire 会被
  legacy `[5:7]` 切到 hold 的两列，loss 照降、静默错位（比崩更糟）；④ **v6 跳过 hold KL**
  （`hold` 是 v6 唯一新增的自由度，漏锚 = kickstart 对它完全不生效，那就不是「BC-anchored」）。
- **违反后果**：任一 v6 + kickstart 的腿在 it1 必停（hub `consecutive=5/5` → `aborted`），
  且 worker 侧表现为「准备完成后无任何 PPO 读数」，容易被读成超时/网络问题；
  只改解包不改切片则会训出一个 hold 头完全自由、fire 头吃错列的模型。
- **落点**：`nn-training/worker/ppo/engine.py`（ref 预计算 / `ref_cache` 注解 / KL 项 / demo 取头）·
  `nn-training/tests/worker/test_ppo_kickstart_cache.py`（+4 例：v6 不崩且 per-chunk 缓存一次 /
  hold 进 KL / 两个方向的「不同源」响亮拒绝）。
- **验收**：`test_ppo_kickstart_cache.py` 8 passed · `test_ppo_common.py` + `test_student_model.py`
  17 passed · `test_ppo_demo_mix.py` + `test_ppo_scalar_sync.py` 15 passed · ruff + mypy 单文件绿。
- **⚠ 生效条件**：`code.zip` 是**会话冻结**的集群快照（`remote/code_snapshot.py::published_code_zip`
  「快照在 ⇒ 用它」，`ensure_cluster_snapshot` 只在 hub/trainer **启动**时打包）⇒ 改本地代码
  **不会**自动到 worker，必须**重启 hub / 控制台**让新快照锚定，再重开腿。`ppo/engine.py` **不在**
  `tools/agent/codehash-files.txt` 集内（`nn-training/**` 被明文排除）⇒ 本修复不触发节点 stale 重启波。

## §2026-10-23-kickstart-burn-scope（2026-10-23，两条改行为腿 18:06/18:23 双双被自己的熔断收官）

- **触发事实**：`y32-tempo` it31（18:06:28）与 `y32-nohp` it41（18:23:12）先后打
  `CRITICAL KICKSTART-BURN`（基线 90.2%，最新 79.5% / 84.0%），各写 `training PAUSED` + 收官
  ⇒ trainer 不再发布 PPO job ⇒ **云端 worker 连续 2 小时领不到任务**（`tmp/hub-server.out`
  18:23:13 → 20:26:05 无任何 `POST /jobs/priority`；只重启 trainer 即恢复，hub 全程无辜）。
  两条腿当时带的是 `kickstart_ref: true`——**该开关会同时启用结果面干烧熔断**
  （`worker/loop_guards_leg.py::_kickstart_burn` 首行 `if not kickstart_ref: return False`）。
- **三条执行面缺陷**（判据本体 `worker/kickstart_burn.py` 一个字未改，改的是「何时允许判」与
  「判中了停不停」）：
  1. **`kk` 已退火到期仍在判**。判据的定义域是「锚主导更新把起点洗回去」；`kk=1.0×0.5^(it−1)`
     到 it30 已 < 1e-9。现场：`kickstart 系数已衰减到期（kk=0.0000）` 之后 1 秒仍打 CRITICAL。
  2. **平台开关 `gate_mode=notify` 时仍本地停车**。`trainer/loop_guards.py::_sync_cloud_halt`
     的契约原话是「门判决只作用于远端云机，TrainingLoop 永不停车」，notify 分支也只守了
     「不下发 cloud halt」；而腿簇自己 `training PAUSED` ⇒ 日志出现
     「notify → 只提示」紧接「training PAUSED」的自相矛盾。
  3. **`auto`/`paired` 解析不出唯一对端时回退 `baseline`**。`worker/kickstart_burn.py` 头注
     自己的回测：baseline 参照在「训练本身在飘」时零假设假阳性 **49.3%**，paired 0.56%。
     改行为类腿（抬血价 / 砍正项 / 落后税）**必然先掉胜率** ⇒ 缺 `kickstart_burn` 块 = 定时炸弹。
- **决定**：
  ① 缰绳不活跃（`kk ≤ NEGLIGIBLE_COEF`）⇒ **不判**，清空 streak 并说一次（`_rein_active`，
     系数唯一来源仍是 `trainer/loop_transport.kickstart_coef`，**不重写公式**）；
  ② `gate_mode == "notify"` ⇒ **只提示不停腿**（CRITICAL 只喊一次，`_burn_notified`；仍写
     `gate_verdict`（带 `notify, 不停腿` 字样）与仍下发云端达令；streak 保留 ⇒ 开关拨到 halt
     后下一次判定立即停）；
  ③ `mode != baseline` 且解析不出唯一对端 ⇒ **不判**（不写 `kickstart_burn` 台账，WARN 一次）。
     **要用起点基线必须显式 `mode: baseline`**。
- **否决的备选**：① 「缺 `kickstart_burn` 块就拒开课」（`load_course` 抛错）——把配置不完备
  升级成开不了课，且历史上 y4 之后无人开缰绳，代价大于收益；② 「把 `MODE_AUTO` 缺省改成
  `baseline`」——正是缺陷 3 本身；③ 「只改 notify 不改 ①②」——两条腿的实测跌幅已经证明
  baseline 判据对改行为腿无效（跌幅是设计的一部分），光改开关只会换成「halt 模式下照样炸」。
- **违反后果**：把 `kickstart_ref: true` 用在改行为类腿上，且没有显式 `mode: baseline` 或
  `peer` ⇒ 该腿在首次「连续 3 点低于起点 5pp」时被停（这次是 it31 / it41），
  **表现为 worker 无活可领**，极易被读成 hub / 网络故障。
- **落点**：`nn-training/worker/loop_guards_leg.py`（`_rein_active` + 三处闸）·
  `nn-training/tests/worker/test_kickstart_burn_guard.py`（新，7 例）·
  `test_kickstart_plan.py`（4 例随契约变更改写）· `tests/trainer/test_loop_guards_split.py`
  （借用方法/入边/出边三张闭集表 +1 项，功能用例改显式 baseline）。
- **验收**：新 7 例绿，且**回滚实测**（`git show HEAD:` 换回旧文件）`_kickstart_burn(15)` 返回
  `True` ⇒ 用例是真红不是假红；`tests/worker/` **1307 passed**；`tests/trainer/test_loop_guards_split.py`
  **29 passed**；ruff / mypy 单文件绿。
- **⚠ 生效条件**：`worker/loop_guards_leg.py` 跑在 **trainer 进程**里 ⇒ **必须重启 trainer**
  （`nn-training/**` 不在 `tools/agent/codehash-files.txt` 内，不触发节点 stale 重启波；
  但 trainer 内存里是启动那刻的代码）。改课程文件同样只对**下一次**启动生效
  （`kickstart_ref` 在 `biz/hot_reload.py::RESTART_ONLY_FIELDS`）——本次事故里 17:10 改文件、
  18:06 第一条腿照旧被停，就是这条。
