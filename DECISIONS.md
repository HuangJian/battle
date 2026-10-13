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

