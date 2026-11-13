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
> 已历六轮瘦身（五次正文下沉），行号必然位移；定位用 `grep -n '^## §<id>'`，指针走索引行末尾的
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
> **2026-10-11 第四轮瘦身**（`§2026-10-11-decisions-slim-round4`）：阈值降到 **>6 非空行**，搬 35 条，
> 4 678 → 4 479 行。**2026-11-11 第五轮瘦身**（`§2026-11-11-decisions-slim-round5`）：再按 **>6 非空行**
> 搬走 **256** 条（2026-10-12 起新增的成篇正文），全文落到
> `docs/decisions/details/{goalnn,godai,repo-governance}.md`（锚 `## §<id>`）；
> 11 839 → **≈8.1K** 行 / 1 205K → **≈760K** 字节（≈−31% 行 / ≈−37% 字节）。
> **2026-11-11 第六轮瘦身**（`§2026-11-11-decisions-slim-round6`）：**629** 条已带指针的索引节录压到 **≤2 行**，
> 全文位置不变；8 067 → **≈5.6K** 行 / 754K → **≈456K** 字节（再 ≈−31% 行 / ≈−39% 字节）。
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
  旧编号 §1–§384 冻结契约不动）+ union 合并（`.gitattributes`）+ 只追加（新条目只写末尾）+ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/repo-governance.md` §2026-09-08-decisions-governance

## §2026-09-09-goalnn-console-lan-readonly（2026-09-09，用户指令：控制台改局域网只读）
> **定案（局域网只读 + localhost 控制）**：训练控制台绑 0.0.0.0（局域网可达），POST /api/* 动作仅回环来源可执行（server.requestIP → isLoopbackAddress，fail closed：无法判定来源 = 拒绝，403）；GET 全开但 ?course= 只读课程覆盖（sanitizeViewCourse：真实课程 + 防路径穿越，绝不写 console-state）；客户端 isLocalHost 判定：本机切课程 = 查看 + POST setCourse 同步操作员课程，局域网切课程 = 仅查看 + 写 URL（?course= 可分享/刷新保持）；课程敏感动作统一带 body.course（所见即所控）。备选与否决：LAN 直通 POST —— 否，杀进程/改配置暴露给整网；每会话独立课程 —— 否，LAN 多 tab 本就共享视图。违反后果：局域网可启停组件/改 rl-config、?course= 路径穿越读任意文件。
> **（2026-09-09 修订，用户指令）**：撤销数据脱敏——cloudflared token 行与复制键局域网照常展示（原「非回环 redactSecrets 剥 auth key」作废）；只读是动作边界，不是数据边界。

## §2026-09-10-goalnn-rleval（2026-09-10，plan/rl-eval-system.md P0–P4 自主实施）

- **决定**：B/C 节点门 engine_epoch 严格拒派，A 层过渡期旧 agent 记日志放行（舰队升级完收紧）；W1 入账走控制台
  read-through（训练循环零改动）；A/B 按 eval_on_round 确定性分配；agent taskKey 无 policy 分量 ←→ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-10-goalnn-rleval`

## §2026-09-10-course-exit-gates（2026-09-10 晚 / 09-11 晨，c6 判否后自主实施 R1/R3/R7/R9）

- **背景**：c6-margin 跑满 50 轮 8550 局、eval 均值 25.4（老师 50、突破线 35）、**零学习信号**，且
  it50 因远端 PPO job 三次 1800s 超时而进程消失（docs/rl.progress.md §22）。根因与修正清单在 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-10-course-exit-gates`

## §2026-09-11-c6-review-disposition（2026-09-11，§12 复盘评审的处置：逐条核数据，非照单全收）

- **背景**：用户转来一份对 `plan/feasibility-map.md` §12（c6 复盘）的评审，要求谨慎评估、
  充分论证。评审指认三处事实错误（F1 kickstart 机制、F2 it50 口径、F3 R1 设施状态）、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-11-c6-review-disposition`

## §2026-09-11-goalnn-evalboard-idle-yield（2026-09-11，用户指令：evalboard 与训练 A-eval 解耦）

- **决定**：① 从 `rollout_phase` 去掉 B/C 领批；② TrainingLoop 每轮 `_join_eval` 后
  开 idle 窗 `_evalboard_idle`（`window_event` 置位）领最早 pending 批；③ 下一轮 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-11-goalnn-evalboard-idle-yield`

## §2026-09-11-nntrain-g13-duty-fix（2026-09-11，用户指令"处理所有问题"）

- **决定**：① G13 分母基线 = **首个完成迭代的结束时刻**（起步热身不计账）+ 完成迭代 ≥2 才判
  （防开门即响；无 duty 数据的旧调用方回退终身口径）；② 非评估轮也查 duty（only_kinds 过滤， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-11-nntrain-g13-duty-fix`

## §2026-09-11-nntrain-cloud-halt（2026-09-11，用户指令：停机=停云端省 GPU 限额、本地进程都不停、console 出横幅）

- **决定**：① hub 新增管理端点 /admin/workers/halt|resume|status（Bearer 同 worker 鉴权；volatile），置位后
  /jobs/next 下发 {"halt":true}；② worker 收到达令即干净退出（keepalive 停、cell 走完）；③ exit-watchdog 在 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-11-nntrain-cloud-halt`

## §2026-09-11-ppo-tpu-step-mark（2026-09-11，c6b-margin 首个 TPU job 单步 45~52s / eta 5.9h 根因定案）

- **决定**：根因 = torch_xla 惰性模式下 `.tolist()` materialize 只断言依赖子图，backward/
  optimizer 在途节点不 drain、跨步骤累积 → 图线性变大 → 单步耗时 ∝ 步数。修复 = ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/tpu-perf.md` §9「决策正文归档」· 锚 `### §2026-09-11-ppo-tpu-step-mark`

## §2026-09-11-remote-worker-hotswap-supervisor（2026-09-11，云端热更新事故修复：os.execve 打掉 notebook kernel，改监督器+子进程）

- **决定**：① 热替换统一改为「以退出码 HOT_RELOAD_EXIT=86 干净退出，由**监督器**用同一套参数
  重新拉起子进程」——fresh 进程 sys.modules 必然为空，新代码一定生效；② worker.py 新增 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-11-remote-worker-hotswap-supervisor`

## §2026-09-16-kaggle-kernel-no-torch（2026-09-16，Kaggle 完整引导后 kernel 内 import torch 无声杀会话）

- **背景**：Kaggle 上跑 `battle.tailscale` / `tailscale.debug` 完整引导（apt 装 tailscale + userspace daemon + 登录 + 代理 env 注入）后，**同一 kernel 进程里 `import torch` 稳定无声杀死整会话**——无 traceback、probe 前置行（nvidia-smi/字符串）能打印、随后 kernel 死→ 会话被回收（peer lastSeen 冻结）。而逐步拆开复刻（apt 装 / daemon / `tailscale up` 登录 / 代理 env 各组合：S1/S2/S4/D1-D3）**全部存活**——唯一稳定死点 = 完整引导后 kernel 进程内 import torch，机理未定位（平台倾向），不阻塞修复。
- **决定**：torch/CUDA 探测下沉子进程——`notebook_runtime._probe_cuda` 用 `sys.executable -c` 子进程探版本/卡数/卡名并回传 JSON；**kernel 本体永不 import torch**。子进程死 → 按无 CUDA 落 TPU/CPU，kernel 照常继续（"死也只死子进程"）。TPU 分支保持 find_spec + /dev 扫描（本就零 torch import）。与「内核绝不 import torch_xla」同一纪律延伸。**生效需重启 TrainingLoop**（code.zip 在 loop 启动时 `pack_code_zip` 一次性打包，notebook 引导从 hub `/code` 拉）。
- **违反后果**：任何人把 torch 引用放回 notebook cell / notebook_runtime 的 kernel 进程（如内联 `import torch` 探测设备），在 Kaggle 完整引导后会复现"整会话无声回收"；设备探测一律走子进程。

## §2026-09-11-c4dodge-course（2026-09-11，用户拍板：c4 残血/闪避后继腿，wChip 单变量 0.005→0.02）

- **决定**：新建 `nn-training/curricula/c4-dodge.jsonc`（派生 c4-margin，单变量 = wChip
  0.005→0.02，其余学习侧全锁；bc = c4-margin.it140；iters=160 + max_hours=10）。闪避目标 = ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-11-c4dodge-course`

## §2026-09-11-nntrain-cloud-halt-final（2026-09-11，用户五条语义：停机命令随任务同发、云机先试停机再干活、本地全不动、红灰双横幅）

- **决定**：① /jobs/next 恒带 halt:bool（有任务时与任务同批下发，空闲时单独送达）；② worker 停机过渡
  尝试一次 _release_cloud_machine（Colab unassign / 其余提示人工断开），**不退出、照常执行任务**； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-11-nntrain-cloud-halt-final`

## §2026-09-12-multi-course-key-is-stem（多课程命名空间键 = 课程文件 stem，不是内部 name）

- **决定**：命名空间键 = 课程文件 stem。`train/loop_util.py::course_key_from_path()` 为唯一推导点；
  `run_rl.py` 锁改调它；`train_loop.py --course` 文档写明传短名（与 `--course s-dodge` 同拼写）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-12-multi-course-key-is-stem`

## §2026-09-12-multi-course-p3b-supersedes-343（多课程：独占加超时租约重启用 §343；worker 侧有界 FIFO）

- **supersedes §343**：PPO job 分发从"竞速广播"改回**独占加超时**——`GET /jobs/next` 领取即设
  租约（owner + expiry + last_heartbeat **同时置**）并下发 `lease_token`；`claimable_job_ids` 排除 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-12-multi-course-p3b-supersedes-343`

## §2026-09-13-multi-course-course-keyed-singletons（多课程并行：课程 = 并行单元，一切单例按课实例化）

- **决定**：①锁/账本条目/端口/隧道/trainer/日志/监督/halt 全部 course-keyed，命名空间键
  = 课程文件 stem（§2026-09-12-multi-course-key-is-stem）；②端口算术唯一归宿 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-multi-course-course-keyed-singletons`

## §2026-09-12-c5-gae（2026-09-12，用户指令：先建 c5-gae，c5-tick 继续跑）

- **决定**：新建 `nn-training/curricula/c5-gae.jsonc`（c5-margin 派生，唯一变量 lam 0.99→0.95，
  wTick 锁 0.01 不回继承 c5-tick；kl_coef 末段 0.03 护栏非变量；不写 ent_coef；bc=c4-margin.it140； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-12-c5-gae`

## §2026-09-12-goal-layer-test（2026-09-12，用户拍板两套目标源；goal 硬 mask 实证为负）

- **背景**：c5-gae（λ=0.95）修好机制后执行器配对 +7pp 但封顶 ~46%（in-loop ~40%），40 轮
  平台。用户提出解冻 goal 头 → 澄清 goal≠intent（intent 骑 God 执行器已证伪；goal=空间目标 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn.progress.intent.md` §35「决策正文归档」· 锚 `### §2026-09-12-goal-layer-test`

## §2026-09-12-c5-gae-finale（2026-09-12，λ 修复 160 轮显著破平台；c6 转移稳定）

- **c5-gae 终判（it160 收官）**：λ=0.95 修好 value 头后，160 轮配对语料（seeds 0-99 vs
  起点 39%）**it145=57%（2p=0.010）/ it160=55%（2p=0.023）——统计显著 +16-18pp**，全项目 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-12-c5-gae-finale`

## §2026-09-12-rollout-flag-bug（2026-09-12，local rollout 3命1星污染事件；已修、已记录、暂不重训）

- **背景**：`nn-training/rl/cmd.py` `build_rollout_cmd` 用 `f"--{k}"` 拼 override 键
  （`lives_override`/`player_level` 下划线），而 `tools/sim/export-rl-rollout.ts` 只认连字符 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-12-rollout-flag-bug`

## §2026-09-13-reward-wdmg-dead-term（2026-09-13，wDmg 死项修复：击杀/承伤/死亡语义各归其位）

- **机制（代码核验）**：`src/game/SimulationCombat.ts:599` 口径下，玩家**非致命**命中推
  `player_damage`（累计入 `playerDamageTaken`），**致命**命中推 `player_hit`（`playerHits++`； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-13-reward-wdmg-dead-term`

## §2026-09-13-level-extraction（2026-09-13，关卡配置抽离 + D14 语料身份改语义哈希；用户拍板）

- **触发**：mid-run 编辑课程文件（iters 30→60）把 c6-chip 打成指纹脑裂——shard manifest 的
  course_fp 与 job envelope 的 course_fp 都在各自时刻重读文件字节（`cmd.course_fp_for_args` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-level-extraction`

## §2026-09-13-hot-reload（2026-09-13，课程热加载：非语料改动下一 iter 应用；语料改动拒绝+横幅+不泄漏云端；用户拍板）

- **机制**：trainer 每 iter（rollout 前，`loop_core.run` → `_hot_reload_course`）重读课程文件，
  以 `corpus_identity_fp` 分流（§2026-09-13-level-extraction 分类学的机制化）： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-hot-reload`

## §2026-09-13-bc-cloud-integration（2026-09-13，BC 训练整合进 云-HUB-LAN：云端第二任务类型 kind=bc；用户指令五步）

- **决策**：远程任务协议引入 `kind` 维度（"ppo" 缺省 wire 兼容 | "bc"），BC job 复用
  HUB job 全套（发布/租约/账本/回传/落位）与 LAN 节点协议（/v1/task ?mode=bc）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-13-bc-cloud-integration`

## §2026-09-13-multi-gpu-default（2026-09-13，云端多卡默认启用 DataParallel：PPO + BC；用户指令）

- **决策**：battle-rl.ipynb 单 cell 的 `use_multi_gpu` 默认 **True**——云端 2+ GPU 时
  PPO 与 BC 都真跑多卡（PPO 走 worker 既有 DP 分支；BC 由 bc.py 新增 `_resolve_bc_device` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-multi-gpu-default`

## §2026-09-13-eval-replay-export（2026-09-13，控制台「导出 replay」：in-loop eval 局的确定性重放导出）

- **决策**：训练控制台首页「最新 6 轮完整指标」行新增「导出 replay」——弹窗列出最新
  in-loop eval（= eval_summary 最大 iter，与指标表 eval 视图同口径）的全部逐局行 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-13-eval-replay-export`

## §2026-09-13-goalnn-max-ticks-rule（2026-09-13，D7 终局标准：max_ticks 取值规则一次性立案）

- **决定**：**`max_ticks(count) = 600×count + 900`**（c01=1500 … c20=12900）。斜率 600 沿用
  roadmap 原式（c05-c07 实测该斜率恰好解除 2400 的截断）；固定项 900 = 接敌/穿场/生成节奏的 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-13-goalnn-max-ticks-rule`

## §2026-09-14-goalnn-dashboard-project（2026-09-14，用户指令：训练控制台独立为 `dashboard/` bun 项目；启动命令改 `bun run dashboard`）

- **决策**：`tools/training/**` **整棵**（core 领域模块 + console 网站 + ui 组件 + evalboard
  评估板 + `train.ts` python 启动器）搬到仓库根 `dashboard/`，成为一个**独立 bun 项目** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-14-goalnn-dashboard-project`

## §2026-09-15-goalnn-gate-pooled（2026-09-15，用户拍板）

- **背景**：多关课正式门跑 200/关（x2 两轮 2400 局）vs roadmap D4 pooled 400 局——量级差 6 倍，“x2 的 80%”与单关课的 80% 不是同一个量（对账结论）。
- **备选与否决**：分关数字进判据 —— 否，分关 SE≈8pp 全是噪声且会掩盖性误杀单关波动；纯 400 局 pooled —— 否，分关诊断能力归零，bc 式偏科（143→132→120）将不可见。
- **决定**：多关课门按 200/关跑足量，**判据只读 pooled**（点估计≥80% 且 Wald 95% LB≥77%，n=800 时 LB≈点估计−2.3pp）；**分关永远只诊断**（配哨兵报告，不进判据）。
- **违反后果**：分关进判据 ⇒ 噪声卡毕业；只跑 400 ⇒ 偏科瞎眼到毕业才爆雷。

## §2026-09-15-goalnn-xn-absorb（2026-09-15，用户拍板 B 案）

- **决定**：工厂模板吸收 xN 语义——c01-c03 多变体（C(4,1/2/3)=4/6/4 关）+
  干净奖励（与 x2/x3-start 逐字同构）；c04+ v2 词干逐字节不动； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-15-goalnn-xn-absorb`

## §2026-09-15-goalnn-local-ppo-worker（2026-09-15，本机 PPO 拆成独立 worker：控制台 `local` 预设 = hub-server + local-worker + trainer；用户指令）

- **背景（用户指令）**：「把本地 PPO 拆分为一个独立 worker，可以随时启停，与云端 worker 一致，
  同样支持 pull/push 模式。」此前 `--ppo local` 是**进程内**执行：`TrainingLoop`（run_rl）在自己的 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-15-goalnn-local-ppo-worker`

## §2026-09-15-goalnn-dynamic-rollout-volume（2026-09-15，采集配额量纲从「局」切「transitions」；plan/dynamic-rollout-volume.plan.md §2）

- **决定**：课程新增三键（全可选；**缺席 = 老行为逐字节不变**）：`target_transitions`（/轮，口径 = 已结算
  shard 的 `nSamples` 之和）、`est_ticks_per_game`（首轮反解局数用，之后由 jsonl trailing 均值覆盖）、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-15-goalnn-dynamic-rollout-volume`

## §2026-09-15-sandbox-precommit-immunity（2026-09-15，用户指令：杜绝编码 agent 删除保护沙箱反复拦截 pre-commit / 反复弹删除审批；hook 进程树级免疫 + 零删除纪律）

- **背景**：WorkBuddy / Mimo 类编码 agent 自带 python 运行时 + 删除保护沙箱，经两个载体注入：
  **bash 函数**（BASH_ENV 指向 safe-delete-bash-env.sh → 每个**子** bash 启动时把 rm/unlink/rmdir ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/repo-governance.md` §2026-09-15-sandbox-precommit-immunity

## §2026-09-15-goalnn-x3-power-negative（2026-09-15，x3-power 判负派生的三条口径令：判读禁令 / transitions=samples / wChip 破规程序）

- **背景**：含 power 关恒差 15-20pp 被归因「伤害摊薄零代价」，x3-power 用**杀/中信用比**
  陡峭化（wKill 3.0→4.0 / wHit 0.3→0.15，即 10:1→27:1）单变量代理 30 轮。终局同批配对 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-15-goalnn-x3-power-negative`

## §2026-09-15-goalnn-kl-cap-unwired（2026-09-15，x3-step 评审 P0-1：串行/远端路径 kl_cap 不接线）

- **决定**：**禁**在串行/远端课程、结算、progress 中把 `kl_cap` 写成生效硬顶或「咬合」；
  本路径生效旋钮只有 `kl_coef`（软惩罚）+ `lr` + F4 `KL_BREAK`。stream 路径才读 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-15-goalnn-kl-cap-unwired`

## §2026-09-15-goalnn-r9-default-abort（2026-09-15，T7：远端连败默认 ABORT，降级本机 opt-in） _(superseded by 2026-09-21「单一 PPO 路径」：`--remote-degrade-after` 与 `--ppo` 均已删除 —— `docs/nn/training-stack.md` §23)_

- **决定**：`--remote-degrade-after` **默认 0**（连败 3 次写 `gate_verdict: ABORT` 停腿）。
  N>0 为显式 opt-in：控制台启动弹窗「降级本机」（localStorage + registry 复现）→ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-15-goalnn-r9-default-abort`

## §2026-09-15-gate-trigger-scope（2026-09-15，用户提问「tools 改动有必要跑 src 的测试吗 / src 改动能不触发 dashboard 吗」⇒ 三处触发面重排）

- **决定**：① `test-silent` 删除 basename 映射与 `--strict`：**代码改动一律跑全量**（仍保留 heavy 排除、
  静默输出、失败单跑取详情，以及「跳过无关改动」= 纯文档/课程配置/dashboard-only）。② `FREEZE_STAGED` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-15-gate-trigger-scope`

## §2026-09-15-heavy-tests-criterion（2026-09-15，用户提问「根套件实测墙钟 ~6s 下，HEAVY_TESTS 名单还有意义吗」⇒ 判据改为实测 + 名单纠错）

- **决定**：① 名单只剩 `godai-score-gate`；② 判据与实测数字写进 `tools/test-silent.ts` 的 `HEAVY_TESTS`
  注释与 `docs/agents.details.md` §5.3；③ 新增 `tools/measure-suite.ts`（只读剖面 + 达标判定）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-15-heavy-tests-criterion`

## §2026-09-16-goalnn-x3-step-negative（2026-09-16，x3-step 授权照跑判负：工厂复合第二段禁作可测性治疗）

- **决定**：① 工厂复合第二段（放缰 + 降 lr 同施）**禁**再用作可测性治疗；可测性证明只认
  单旋钮 A/B 或 pd/指纹主端点。② `Δ≤pd` 为判决硬约束：pd<5% 时 +5pp 判线不可观测， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-16-goalnn-x3-step-negative`

## §2026-09-16-goalnn-run-noise-floor（2026-09-16，T6 四臂定案：run 噪声地板规则＋wChip 判死）

- **决定**：① **run 噪声地板规则**：单 run 总 SE≈2.2pp（对局 1.6＋run 1.5）；
  声称效应 <2pp 须 ≥2 独立 run（跨 run 预注册 pooling），否则结算标「未过噪声地板」； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-16-goalnn-run-noise-floor`

## §2026-09-16-goalnn-t5-credit-negative（2026-09-16，T5双run一致判负⇒奖励重定价路线关闭）

- **决定**：① 奖励侧重定价路线在 `ladder-c03` 关闭（幅度/方向/粒度均已证伪，
  不再开奖励腿）。② pd 尺修正：本分布 pd≈8% 为噪声地板，5–10% 灰度带无判别力、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-16-goalnn-t5-credit-negative`

## §2026-09-17-kaggle-cred-before-proxy（2026-09-17，Kaggle 引导期凭据必须在装 tailnet 代理之前读；诊断日志落文件）

- **决定**：① `notebook_boot.run()` 在 `ensure()` **之前**一次性读完三个凭据并下传（`_pull` 不再持有
  secret 句柄，签名级防回归）；② `tailscale_boot` 新增 `set_proxy_env()` / `platform_net_env()`： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-kaggle-cred-before-proxy`

## §2026-09-17-goalnn-remotewire-m0m2（2026-09-17，远程 PPO 传输计量 + 协议瘦身落地；退路与安全阀是硬要求）

- **决定**：① `wire` 子字典（iteration 事件 + worker result 回传）是**唯一**传输口径，键为 additive，
  旧行无键 = None，`validate_result` 不校验未知字段；`/admin/net-probe?bytes=N` 走鉴权、确定性填充。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-remotewire-m0m2`

## §2026-09-17-goalnn-rollout-on-cloud（2026-09-17，M3 rollout 上云：**人工指令覆盖计划的决策门**，

- **决定**：① 新 job `kind="iter"`（走 BC 开过的 kind 通道），manifest 追加必填 `ts_code_sha256` + `rollout`；
  rollout 规格 = 逐局 argv（白名单只放行 `tools/sim/export-rl-rollout.ts`，`--out`/`--weights` 必须 job 内 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-rollout-on-cloud`

## §2026-09-17-hub-restart-deadlock-hardening（2026-09-17，hub-server 重启死锁：D9 改序 + 回环永不封禁 + 原子实例锁 + 停止 trainer 即释放锁）

- **背景**：hub-server「崩溃后手动重启失败」，控制台只报「意外退出」，日志为 `端口 127.0.0.1:8787
  已被占用——拒绝启动（禁止双监听）`。根因链与现场日志、备选否决的完整版见 `docs/nn/remote-transport.md` §6。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-hub-restart-deadlock-hardening`

## §2026-09-17-goalnn-python-gate-parallel-policy（2026-09-17，python 门禁并行策略：worker 数 × CPU 内线程数必须成对调；"-n 4 最优" 作废）

- **决定**：门禁 worker = `min(核数, 12)`（`NN_GATE_NPROC` 覆盖）、CPU 内线程 = 1
  （`NN_GATE_THREADS` 覆盖，0 = 不设；须在 python 启动前 export OMP/MKL/OPENBLAS）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-17-goalnn-python-gate-parallel-policy`

## §2026-09-17-job-fail-report（2026-09-17，节点**确定性**失败必须带原因回传控制面：`POST /jobs/{id}/fail` + `/result` 410 终局）

- **背景（真缺口，用户 2026-09-17 提问暴露）**：云机确定性失败（bun 装不上 / TS 运行时取不到 /
  argv 非法）此前只落在**云机日志**里。pull 侧 `worker_loop` 的 `except ProtocolError` 只打一行 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-job-fail-report`

## §2026-09-17-goalnn-halfoffline-run（2026-09-17，半离线整段：云机领一次就自主跑完，产物可打包下载；hub 失联不影响）

- **背景（用户需求，2026-09-17 两次确认）**：现状是 **hub 拥有迭代循环**——每轮 publish 一个 job、
  节点跑一轮就回等；hub 一断，云机除了等无事可做。用户要的是：云机领到（课程 + 初始权重 + 代码） ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-halfoffline-run`

## §2026-09-17-goalnn-offline-task-bundle（2026-09-17，全离线任务包：hub 导出 → Kaggle/Colab 上传 → 云机自主跑完；包是搬文件不是配网络）

- **背景（用户需求，2026-09-17）**：「hub 支持打包导出训练任务（课程、初始权重、代码），以
  kaggle/colab **官方支持方式**上传云机后，云机全程自主完成训练，并以官方方式提供产物打包下载； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-offline-task-bundle`

## §2026-09-17-goalnn-offline-reconnect-delivery（2026-09-17，产物补传：中途能连上 hub 就自动恢复在线回传；不可影响训练是唯一硬约束）

- **背景（用户需求，2026-09-17）**：全离线任务包（上一条）交付了「hub 关机也能开工」，需求的后
  半句是「如果训练中途发现可以联通 hub，云机也能自动恢复产物在线回传」。补传 = 产物目录之外 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-17-goalnn-offline-reconnect-delivery`

## §2026-09-17-goalnn-console-task-bundle-exchange（2026-09-17，控制台「导出任务包 / 导入产物即评估」；控制台不重造包格式）

- **决定**：
  1. **智能在 python 一侧，控制台只做三件事**：拼 argv（`--course/--ppo remote/--run-iters -1/ ……

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
不实时派发、只收 it 权重/指标回传），但**全仓没有任何控制台代码调它**——面板上只有一个只读 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-goalnn-console-course-mode-toggle`

## §2026-09-19-goalnn-slot-cap-5-and-loud-rejection（2026-09-19，plan R3-1：课程上限 5 + 越界槽位响亮拒启）

**背景**：用户口径「允许同时训练多个课程，上限先设为 5」（2026-09-18），而实现是
`dashboard/src/core/slots.ts::SLOT_COUNT = 4`，且 `slotOf` 对**越界值也静默回落 0**。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-goalnn-slot-cap-5-and-loud-rejection`

## §2026-09-18-goalnn-multi-course-single-hub（2026-09-18，用户指令：多课程并行训练流程与操作重组；单进程服务所有课程 + hub 队列 + 分课程权重缓存）

- **背景**：多课程并行（`docs/multi-course-audit.md` / plan multi-course-parallel-training）当时定的形状是**课程 = 并行单元**：每门课各一套 hub-server / cloudflared / trainingLoop / localWorker / workerServe 进程，端口按槽位 `base + slot*10`，账本按课程键控。它解决了「第二课覆盖第一课登记」那类串账，但代价是进程数按课程线性增长（5 课 = 25 个进程），且 **hub 完全不知道「课程」这回事**（`--job-root`/`--jsonl` 都是每课程路径），所以没有跨课程的调度面：一门课积压 20 轮就独占自己的 hub，别的课的 worker 空转。
- **用户要求**（本次）：① 上限先设 5；② hubserver/trainingloop/selfNode/cloudflared **各只需一个进程**；③ hub 设 PPO 任务队列（push 按队列顺序推给空闲 worker，推送超时回落队首改推其它 worker；pull 按队列顺序分发给请求者）；④ 离线模式课程不实时派发 PPO、但接收 it 权重/指标回传；⑤ rollout 集群按课程缓存最近权重，避免同一份权重多次传递；⑥ 并行课程数 < PPO worker 数时用单课程那样的竞速；⑦ 面板重组（课程 select 不锁死、在训课程高亮等）；⑧ e2e 保证畅通。 ……

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
- **决定（设计稿 `plan/r2-loop-task-queue.md`）**： ……

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

- **决定**：伪节点**整体退出控制台**——不是换个轴（原计划 R3-6 否决④的「收敛为节点轴」也不再需要），而是从「受管」这件事里退出。它此后只以**冒烟预演的一次性配角**存在（`stack/push.ts` 起、跑完/失败即杀）。
  - **受管面全删**：`Component` 键、`COURSE_COMPONENTS`、`PLURAL`/`Registry.workerServes`、`ALL_COMPONENTS`、`restartSpecFor` 分支、`startComponent` 分支、`stopComponent` 端口表、`smokeComponent` 分支、`COMPONENT_LOGS`/`HEALTHY_PORTS`/`LOG_NAME_MATCH`、`WORKER_SERVE_ENTRY`/`workerServeSpec`（ProcSpec）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-goalnn-retire-local-fake-node`

## §2026-09-19-goalnn-course-worker-orthogonal（2026-09-19，用户口径：启动训练不选 pull/push 模式）

- **决定**：**传输不再是课程属性，也不再是启动选项**——它是部署事实，由「有没有登记节点」推出来。
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-goalnn-course-worker-orthogonal`

## §2026-09-19-goalnn-test-port-contention（2026-09-19，平台性存量红：e2e「探端口 → 起子进程 bind」的 TOCTOU）

- **决定**：把这段竞态收进测试侧的唯一出口 `tests/subproc_util.py::spawn_bound_port()` —— 在探测到的端口上起真服务进程，**成功判据 = 这个子进程自报 `listening on <host>:<port>`**（不是「端口上有人监听」：那可能是别人的服务，会让我们对着陌生 hub 跑完整用例）；撞端口的子进程带着 `PORT_TAKEN_MARKER` 退出 ⇒ 换端口重试（默认 5 次）并 **print 一行**（重试发生了要看得见，否则偶发红又会变成谜）。
- **备选与否决**：① 保持原样、给这条 flaky 用例加重试插件——否（重试会把真 bug 一起吞掉，而这里的真因是**可消除**的）；② 靠 `SO_REUSEADDR` 让后绑者也能绑上——否（`_port_guard` 的语义正是「禁止双监听」，放开等于把 2026-09-09 双实例事故请回来）；③ 用「端口有人监听」当成功判据 + 就绪短等——否（会静默地把陌生进程当成被测服务）；④ 让每个 xdist worker 在按 `PYTEST_XDIST_WORKER` 偏移的端口段里取号——否（窗口只变小不消失，还给测试引入与 worker 拓扑耦合的端口算术；真撞上时同样无从恢复）；⑤ 改 socket activation（父进程 bind 好再把 fd 交给子进程）——否（被测对象是 CLI 的 `--port`，改传 fd 就不再测控制台真实启动的那条 argv）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-19-goalnn-test-port-contention`

## §2026-09-19-goalnn-offline-training-mode（2026-09-19，离线训练模式：启动选在线/离线 + 云端整段执行 + 补传归位）

- **决定**："在线/离线"是**启动时的模式选项**（缺省在线），落成**课程级** rl-config 键 + hub 该课的模式：① 模式 → 键的换算只有**一处**（`dashboard/src/stack/specs.ts::trainModeKnobs`）：离线 ⇒ `courses.<课>.{rollout_src:'run', run_iters:-1}`（声明 + 段长），在线 ⇒ 撤掉离线标记（段长必删，课程级 `run` 也删，但**不**顺手清别的覆盖）；② 离线启动同时把该课 hub 模式置 offline（整段 job 只交给**带标** worker）；③ 「能领离线课」= worker **自报能力**（`X-Battle-Offline: 1`，`--offline`），不是课程绑定——带标 worker 仍领在线课；④ 补传的**归位键** = hub 在 `/jobs/next` 里下发的课程键（领活路径）或 `--hub-course`（全离线包路径，由 notebook 的 `CFG.course` 给），空 = 单课程 hub（此时不带这个键）。
- **为什么模式不写全局 `rl.rollout_src`**：那是所有课共用的默认面——用户在弹窗里只选了**这一门课**，落进全局就等于把全部课程一起拖进离线。故离线只写课程级键；`run` 也**绝不**进全局（`preset.ts` 用 `trainModeKnobs` 算出的全局面：算出 `run` ⇒ 置空不写）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-goalnn-offline-training-mode`

## §2026-09-19-goalnn-test-port-convergence（2026-09-19，测试侧取端口全量收敛 + 守卫扩到全测试面）

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
  `pure_collect_sec` = `last_settle − t_dist_start`（**含**与采集重叠的分发墙钟，端到端）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-19-rollout-pipeline-metric`

## §2026-09-19-volume-continuous-quota（2026-09-19，用户指令：退役离散补波 → 配额感知连续派发）

- **背景**：x20 分关样本缺口/wave_cap 复盘 —— 全局 est × 等 G0 盖不住 1 命下变体间
  产量方差（通关率波动 → 局均 nSamples 波动）。用户裁定：**完全去掉补波机制**，loop ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-19-volume-continuous-quota`

## §2026-09-19-console-node-wallsec（2026-09-19，用户指令：节点统计新增训练机侧平均墙钟）

- **决定**：双字段并列，互不覆盖。`wallSec` = **本 worker 本 attempt** 派发→结算墙钟
  （`t_task_start` 本地量，**勿读** `inflight_ts[task]`——竞速副本会覆盖）；成功结算时 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-console-node-wallsec`

## §2026-09-19-volume-purecollect-stale-merge（2026-09-19，bugfix：指标表 rollout 时间轮轮累加）

- **决定**：轮初 `self._report = {}`；continuous 收官只采纳本轮
  `adopt_volume_report(combined)`（无 batch 时返回**合法空 shape** `combine_reports([])`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-19-volume-purecollect-stale-merge`

## §2026-09-19-console-wallsec-undefined-guard（2026-09-19，bugfix：节点统计「机侧墙钟」显示 undefineds）

- **决定**：展示层 `secCell`（仅正有限数 → `Ns`，否则 `-`）；`fetchPool` 将
  `avgWallSec ?? null` 归一；服务端继续显式写字段。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-console-wallsec-undefined-guard`

## §2026-09-19-evalcourse-dist-eval（2026-09-19，eval-course-ckpt 开分布式评估：同节点门 + 同规 stageJson）

- **决定**：eval-course-ckpt 增混合分派路径，**缺省仍是原本地 chunked 路径（行为逐字不变）**。
  节点门与 rollout/m1-eval 同源：`evalSupport ∧ stageJsonSupport ∧ bun major.minor ∧ codeHash`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-evalcourse-dist-eval`

## §2026-09-19-evalboard-phase0-census（2026-09-19，Phase 0 逐敌种画像进 EvalStore schema（中方案 P1））

- **决定**：`rl/eval_local.py::eval_census_fields` 单源（**只认顶层**，缺键 = None
  不伪造），A/B/C/m1 四个写点接线；`ingest.ts` 映射（畸形计数列/非字符串归零或空）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-19-evalboard-phase0-census`

## §2026-09-19-evalboard-verdict-batch（2026-09-19，判决批走 B 层：语料注册表 + 多 ckpt 批类型（中方案 P2））

- **决定**：① 语料身份独立成注册表 `dashboard/src/evalboard/corpora.json`
  （`{id, level, seed0, games_per_stage, policy?}`，读/校验/身份派生在 `corpora.ts`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-19-evalboard-verdict-batch`

## §2026-09-19-eval-tools-node-upgrade（2026-09-19，一次性评估工具复用训练循环的节点升级守卫 + m1-eval 补节点门）

- **决定**：① 新增 `nn-training/dist_upgrade_cli.py`（stdin JSON spec → 逐节点调
  `dist_common.request_upgrade_guarded`，**单源**：护栏与 dirty 判据仍只在 Python 一处）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-19-eval-tools-node-upgrade`

## §2026-09-19-m1-eval-python-dispatch（2026-09-19，m1-eval 分派链回归 Python：TS 只写 spec/读行/打分）

- **决定**：上一轮的「仍留的重复」点名的就是 `m1-eval.ts` 自己的分派链（判门/rescan/尾竞速/权重下发
  ≈300 行）。用户裁定同一口径——**Python 侧已有实战版，别再在 TS 重建**。新增 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-19-m1-eval-python-dispatch`

## §2026-09-19-x20-snowball（2026-09-19，x20 后继腿：中盘激励，里程碑 bonus 单变量 + god-prefix 否决）

- **背景**：x20 结算（池外 it30 7.5%/6.10杀，无合格终点＋旧能力丢失）＋ 用户假说
  "通关靠捡道具滚雪球"。证实：it30 池外段通关局 1.22 pu/千tick vs 死亡局 0.64、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-19-x20-snowball`

## §2026-09-19-nn-decision-instant（2026-09-19，nn 决策时点归一到语料口径：NNInput 末帧决策 + nn 入列分派白名单）

- **决定**：① `m1-eval` 分派白名单加 `nn`（`--weights-dir` 解析出**最新**权重文件后上传，`kind='rollout'`）
  ——上一轮条目「nn 走 --weights-dir 自动发现、无文件可上传 ⇒ 留本机池」的**理由已被自身证伪**（解析出的 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-nn-decision-instant`

## §2026-09-19-eval-tier-channels（2026-09-19，B/C 层派发改为「每节点独立通道」：无阶段屏障、就绪即派单、失联重探、settled 满即断连）

**触发**：用户 2026-09-19 五条裁定（原话）：①「不要把分发权重和派发 eval 任务划分为串行的不同阶段！！！
一个节点权重分发成功后，立即！马上！right now！给它派发任务！！！不要等慢节点！」②「不要分什么 u0/u1/u2 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-eval-tier-channels`

## §2026-09-19-node-fault-taxonomy（2026-09-19，A/B/C 三层共用瞬断判据 + 409 wver-not-cached 自愈：把「节点故障」与「可刷新条件/背压」分开）

**触发**：用户 2026-09-19 审计后指名修 A1+A2+A3（训练侧 rollout/eval 与一次性评估同源的三个洞）。
证据链（`tmp/x20-rebirth/training-loop.log`，08:00–10:36，235 个 rollout 轮 / 20 个 eval 轮）： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-node-fault-taxonomy`

## §2026-09-19-rollout-midround-recover（2026-09-19，A 层 rollout 中途重探真正跑起来 + 轮内回场：`rescan_nodes` 从「每轮 0 次 pass」到 5s 首探/20s 周期 + 已停派节点回场）

**触发**：用户 2026-09-19 指名修 A4（审计发现）。**证据（两条独立线索都指向 0 次执行）**：
① `tmp/x20-rebirth/training-loop.log` 2.5h / **235 个 rollout 轮**里 `rescan` 日志 **0 行**，而同期 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-rollout-midround-recover`

## §2026-09-19-eval-gate-lanes（2026-09-19，C 层（训练干净评估）的门与收工形态：并行 ping + 逐条留痕 + POST 全败走本地 + 本机槽位先开工 + settled 满即断连）

**触发**：用户 2026-09-19 连续两条指令（「把 C 层收工空等 4–76s/轮榨掉：all_done 即断连 + 只等写行的赢家」、
「把 eval 层的病态分支与串行门一起修」），底稿是同日的 C 层审计（`tmp/x20-rebirth/training-loop.log`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-eval-gate-lanes`

## §2026-09-19-eval-weights-kind（2026-09-19，训练干净评估的权重 kind 独立成 'eval'——不再与训练 rollout 共用节点权重桶）

**触发**：用户 2026-09-19（「给训练评估独立 kind（我定名 eval），顺带一次现场验证」）。来源是同日审计的
**B6** 项：`rl/eval_dispatch.py` 的权重下发与局请求都走 `kind="rollout"`，与训练 rollout 的 churn 共用节点 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-eval-weights-kind`

## §2026-09-19-node-weights-disk-fallback（2026-09-19，节点侧权重查找在内存桶未命中时回查磁盘——根治「agent 重启后对盘上已有的权重答 409」）

**触发**：用户 2026-09-19「让节点侧的权重查找在内存桶未命中时回查磁盘，根治重启后的 409」。承接
`§2026-09-19-eval-weights-kind` 的已知局限 ①（kind 拆桶只消除**两条腿互相驱逐**，重启仍会 409）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-node-weights-disk-fallback`

## §2026-09-19-node-memory-restart-blindspots（2026-09-19，依赖节点内存态的三处重启盲区：取包丢失 404 判据 / `/v1/update` 假收敛 / 升级去重冷却窗）

**触发**：用户 2026-09-19「系统排查其它依赖节点内存态的地方（结果缓存、inflight 表）是否也有重启后行为
盲区」，随后裁定按 F1 → F3 → F2 修。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-19-node-memory-restart-blindspots`

## §2026-09-20-dashboard-shell-routing（2026-09-20，训练控制台信息架构重设计 P0：应用外壳 + 路由化详情页；**路由实现 = 单 bundle + 服务端路由 SSR + pushState**）

**背景**：控制台是单列纵向堆叠的 12 个平权面板（无分区/无导航），四类详情（指标/传输/节点统计/日志）
住在全屏模态抽屉里（**没有 URL**：看不到也分享不了「我在看这个视图」），课程选择器（整页主语）被 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-dashboard-shell-routing`

## §2026-09-20-console-process-course-decoupled（2026-09-20，用户指令：服务进程启动与课程解耦 + 开课/停课独立入口）

**背景（一次实测故障里其实躺着两件事）**：用户点「启动训练」（课程 `x20-steady`）失败，控制台输出： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-20-console-process-course-decoupled`

## §2026-09-20-course-enable-marker-and-training-pills（2026-09-20，用户报障 + 用户指令：课程开训必须手动开；在训课程显示为 pill）

**背景（上一条落盘当天就撞上的两个洞）**： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-course-enable-marker-and-training-pills`

## §2026-09-20-publish-lineage-filter（2026-09-20，用户报障：云机领到 ppo 任务后一直报错）

**症状（用户贴的云机日志）**：worker 领到 job 后逐份失败，刷屏三类错误： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-publish-lineage-filter`

## §2026-09-20-hub-dispatch-gate-and-stale-adoption（2026-09-20，同一故障的第二/第三层根因）

**为什么还有两层**：D14 那条修的是「坏包被造出来」；用户贴的日志里还有两个独立症状——
**worker 领到的是 11 门课的陈旧 job**（x20-floor 6.8h、x20-powered 15.2h、c6-chip 157.8h…）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-hub-dispatch-gate-and-stale-adoption`

## §2026-09-20-body-transfer-stall-guard（2026-09-20，用户报障：云机 claim 第二个 job 后几分钟无动静、无日志）

**背景**：云机领到 `it2` 的 PPO job（`a4b2e1e2d438c28a`）后卡在 payload 下载——
日志从 `claim ... downloading payload` 到 5 分钟后的 socket 超时**一行都没有**（用户原话： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-body-transfer-stall-guard`

## §2026-09-20-console-decision-log（2026-09-20，用户指令：控制台的启/停/重启/判死要写日志文件）

**背景**：上一节那条「同窗口 hub 停服」的复盘最后一公里——hub 被**人工**停掉后，盘上的证据
（组件日志 + 账本 + console-state）只能显示「日志断在半分钟前」「账本条目没了」「没有意外退出 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-console-decision-log`

## §2026-09-20-open-course-lock-identity-and-write-order（2026-09-20，用户报障：开课被「自己人」的锁拒掉）

**背景**：用户点开课，回执是 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-20-open-course-lock-identity-and-write-order`

## §2026-09-20-wire-slow-reroll（2026-09-20，用户报障：云机传输长时间卡在 6–9 KB/s；评审 plan/minimize-payload.plan.md 时的实测）

**背景**：上一节（body 停滞判据）让「卡住」变得可见之后，现场读数暴露出**真正的病**： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-wire-slow-reroll`

## §2026-09-20-node-health-by-completed-round-contrib（2026-09-20，用户指令：节点 pill 的贡献数取上一轮**完成**值，健康度按「贡献 vs 并发」重定义）

**决定**：
* **对齐基准轮 = 最近已完成轮**：完成水位 = 同一训练流目录 `training_log.jsonl` 里最后一个

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-node-health-by-completed-round-contrib`

## §2026-09-20-console-declutter（2026-09-20，用户指令：课程区只列在训课程 / 服务面不显示共享·单例 / 删掉关键指标区）

**背景**：控制台首屏纵向空间被三处「重复或索引式」的内容占掉——课程表里几十门未在训的历史课
把在训的那几行挤出屏幕；服务面每行重复一个「共享/单例」（一族里每行都同一个值）；KPI 条六格是 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-console-declutter`

## §2026-09-20-topbar-and-family-labels（2026-09-20，用户指令：顶栏去掉页问题/「在训」、pill 靠左；族标题改「服务」）

**背景**：同一轮减肥的第二批（承接 §2026-09-20-console-declutter）。用户扫顶栏从左到右点出的四处：
「总览 现在能不能跑？这轮跑到哪了？」、pill 组前的「在训」、pill 组的水平位置、组件区族标题。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-topbar-and-family-labels`

## §2026-09-20-console-naming-and-trend-source — 服务角色名 / 节点行不写字 / 走势数据源档位

**用户指令（同一天追加的一轮）**：① 节点 pill 不显示「缓慢/离线」字样，离线时显示状态描述和判据；
② trainingLoop pill 去掉「dispatch→拉取」描述；③ 顶部趋势图加「全部 / rollout / eval」toggle 切换数据源； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-console-naming-and-trend-source`

## §2026-09-20-preact-svg-attribute-spelling — 趋势图面积块变黑：Preact 客户端不归一 SVG 属性名

**用户报告**：从「指标」页切回「总览」，趋势线与横轴之间的**面积块显示为黑色**；**硬刷新后恢复**。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-20-preact-svg-attribute-spelling`

## §2026-09-20-eval-prefers-dist（2026-09-20，用户指令：评估慢是原罪，走分布式；`--no-dist` 下不为例）

**背景**：x20-steady 终点判决（`eval-course-ckpt.ts`，800 局）为求"确定性"用了
`--no-dist`（本机单跑 2.2局/s，约 6 分钟/800 局）。用户纠正：分布式本就能保证 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-20-eval-prefers-dist`

## §2026-09-20-boot-wire-guard（2026-09-20，收尾 §2026-09-20-wire-slow-reroll：引导期两个大 body 也在同一次连接抽签里，却跑在 code.zip 之前）

**决定**：护栏做成**引导期**的一份，住 `remote/tailscale_boot.py::fetch_guarded`：
分块读 256 KB + 进度行 + 停滞 45 s（`BOOT_IDLE_TIMEOUT_SEC`）+ 调用方给的墙钟预算 +

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-20-boot-wire-guard`

## §2026-09-21-goalnn-native-features-engine（2026-09-21，落地 `plan/rollout-eval-opt.plan.md` §4：native 内核进入**生产** features 路径）

**决定**：
1. **单一咽喉 + 选择链**：`src/nn/conv-wasm.ts::runStudentFeatures` = `native → wasm → TS`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-21-goalnn-native-features-engine`

## §2026-09-21-goalnn-native-prebuilt-distribution（2026-09-21，落地 `plan/rollout-eval-opt.plan.md` §2.5/T2：native 库改「训练机交叉编译 + 随仓库分发」）

**决定**：
1. **训练机交叉编译 6 目标入库**：`src/nn/native/prebuilt/<platform>-<arch>/conv_feats_native.{dll,so,dylib}` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-21-goalnn-native-prebuilt-distribution`

## §2026-09-21-goalnn-probe-negative-arm（2026-09-21，用户指令：负向臂——人类每局都给结论，直接用）

- **决定**：负向臂**不是新协议**，就是操作员手里已有的 `无解 + 理由` 按钮；聚合改三态，优先级
  **可解 > 无解 > 未知**（通关是构造性证据，优先于「没找到路」的判读），`无解` 由「未知」改为判 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-21-goalnn-probe-negative-arm`

## §2026-09-21-goalnn-probe-multi-course（2026-09-21，用户指令：别把文件名绑死为 x20，以后还有很多关卡要人类探针）

- **决定**：**一门课一个清单文件**。`?probe=<course>` 决定取 `/probe/<course>.json` —— 名字因此是
  **URL 片段**：用 `^[a-z0-9][a-z0-9._-]*$` 白名单 + 保留名 `index` 校验（挡掉 `?probe=../../…` 变任意 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-21-goalnn-probe-multi-course`

## §2026-09-21-god-bc-attractor-basin（2026-09-21，用户纠正 + 取证：God-BC 是陷阱，不是跳板）

**前科（实证，非观点）**：
1. `nn-training/curricula/x1-rebirth.jsonc` 头文件（2026-09-16 实测，2026-09-17 用户订正）： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-21-god-bc-attractor-basin`

## §2026-09-22-demo-mix-bc-aux（2026-09-22，demo 混 batch 接线：BC 辅 loss，不是 kickstart）

**否决的备选**：
- kickstart 锚到人类 BC-ref——否：ref 在 held-out 挂零（§120），KL 朝常量坍缩策略锚定 = 投毒。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/tpu-perf.md` §9「决策正文归档」· 锚 `### §2026-09-22-demo-mix-bc-aux`

## §2026-09-22-course-error-isolation-loud（2026-09-22，课程配置错误不得弄崩共享 trainer；控制台响亮报错）

2026-09-22 事故：开课 x20-demo-mix 选「离线（整段上云）」但课程 `iters=0`，run_rl 的
SystemExit（`--run-iters<0 需要课程声明 iters——没有终点就不叫整段`，BaseException）从一步级 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-22-course-error-isolation-loud`

## §2026-09-22-offline-bundle-autoready（2026-09-22，离线课开课自动出包 + 随时可导 + 行内操作 + 状态列修正）

承接 §2026-09-22-course-error-isolation-loud 的离线链路收口（用户三条指令叠加）：
①「离线课程开课后自动生成任务包，kaggle 配好接离线任务后经 hub 下载执行」；②「开课后 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-22-offline-bundle-autoready`

## §2026-09-22-goalnn-course-switch-cache-tier（2026-09-22，用户报障：多课程并行切课程要等几秒）

- **决定**：快照**分两层键控** —— **机群级**（节点 / 本机槽位 / push 群 / hub 观测面）单条目全局共用
  （`core/swr-cache.ts`：新鲜直接给、陈旧先给旧值再后台重算、`clear()` 后必须等重算）；**课程级** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-22-goalnn-course-switch-cache-tier`

## §2026-09-22-goalnn-transfer-scheduling-pull（2026-09-22，传输∥PPO 的优先级调度面：P1/P1.5/P2 落地）

- **背景**：多课程并行下网络传输耗时 ≈ rollout/PPO，串行 `claim → 下载 → PPO → 回传 → 轮询`
  把 GPU 饿死在传输上。plan `transfer-scheduling.plan.md`（同日两轮评审 R1/R2）给出解法的 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-22-goalnn-transfer-scheduling-pull`

## 2026-09-22-goalnn-bulk-single-channel — bulk 单通道 / 让路预算 / 软持有预取（plan/transfer-scheduling P0+P2）

**背景**：§2026-09-22-goalnn-transfer-scheduling-pull 只换了 pull 线的取活面；本条落地「传输那半场」
（`docs/nn/remote-transport.md` §29）：三条流分层（P0 控制面 / P1 关键 bulk / P2 预取），让 GPU 不再被传输饿死。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### 2026-09-22-goalnn-bulk-single-channel`

## §2026-09-22-goalnn-race-retired-priority-only（2026-09-22，落地 `plan/transfer-scheduling.plan.md` P3：竞速广播**判定**退役，取活只剩「peek → priority → claim」一条路；push 腿同表 + 备份副本）

**supersede §2026-09-17-goalnn-race-broadcast**（以及它在 `dashboard/src/**` 的落地物）。该条目的
**机制**被本条目取代：不再有「最新 job 广播给每个 worker、先回先胜」的判定，也不再需要 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-22-goalnn-race-retired-priority-only`

## §2026-09-22-goalnn-async-result-upload（2026-09-22，plan/transfer-scheduling **P2.5**：结果回传异步化 —— 把 `out` 从关键路径上摘下来；阶段账新增 `overlap=` 字段）

**决定**：`post_result` 不再同步阻塞主循环。结果**入队即返回**（`remote/result_upload.py::ResultUploader`，
有界队列 + 专用上传线程），主循环立刻去领下一份 job；`--result-upload {async,sync}` 缺省 `async`。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-22-goalnn-async-result-upload`

## §2026-09-23-goalnn-conv-single-source（2026-09-23，conv-optimize.plan.md §4.2 落地）

**决定**：单源 + 统一 ABI（native 的单 blob + 4 参 `cf_student_features`）；wasm 产物与 6 个 native 目标
同入 prebuilt manifest ⇒ `--cross` / `--check-prebuilt` 一次抓全「改了 conv.c 漏重编某个目标」。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-23-goalnn-conv-single-source`

## §2026-09-23-goalnn-agents-injection-budget（2026-09-23，用户指令：AGENTS.md 加预算门禁）

**决定**：挂 `bun run check` + pre-commit 一个**无条件**跑的小块（并在 pre-commit 里按文件归因：超预算
且 `AGENTS.md` 在暂存清单 ⇒ 拦；只在他人未暂存改动里 ⇒ 告警放行）。 ……

—— 全文（背景 / 阈值推导 / 度量口径 / 归因语义）→ `docs/agents.details.md` §0.1

## §2026-09-23-goalnn-common-primitives-layer（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 提可维护性」）

- **决定**：新增 **stdlib-only 的 `common/` 包**（hashing / proc / fs / text / logutil）作共享原语层；上层只留
  re-export / 薄包装（历史名字与 monkeypatch 接缝全保留）⇒ **零行为变化**（门禁 2230 → 2247 全绿）。层契约： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-common-primitives-layer`

## §2026-09-23-goalnn-layering-common-sink（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 提可维护性」）

- **决定**：把**模块级零上层依赖**的 `remote/protocol.py`（92 处 / 65 文件引用）与 `remote/game_watch.py`
  （14 处 / 12 文件）下沉为 `common/` 成员；`remote/` 保留**显式清单**门面（不用 `import *`）。确立分层 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-layering-common-sink`

## §2026-09-23-goalnn-godmodule-loop-transport（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 提可维护性」）

- **决定**：把 `loop_steps` 的**模块级函数簇（43–611 行）零逻辑改动**搬到新 `rl/loop_transport.py`
  （传输/发布策略的唯一实现），`loop_steps` 只留**显式清单门面** + 列全的 `__all__`（2328 → 1812 行）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-loop-transport`

## §2026-09-23-goalnn-godmodule-hub-admin（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **决定**：把 admin 控制面 **9 方法 / 218 行**（停机恢复 · 课程热切 · 队列与状态 · push-worker 清单 ·
  net-probe）拆成 `remote/hub/admin.py::AdminRoutes`，方向 `class HubHandler(AdminRoutes, ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-hub-admin`

## §2026-09-23-goalnn-godmodule-wire（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **决定**：`WIRE_*` 阈值 7 个 + 状态 3 份（`_WIRE` / `_BEST_RATE` / `_BULK`）+ 15 个自由函数
  （`set_bulk_log` · `_bulk_pace` · `_note_rate` · `_min_rate` · `_reroll_decision` · ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-wire`

## §2026-09-23-goalnn-godmodule-http（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **决定**：先把**公共底座**下沉为 `remote/http.py`：`_opener` + `_get_opener` · `BODY_*` 阈值 ·
  `_read_body` · `_POLL_WARN_AT` + `_warn_non_200` · `_request` · `_sched_headers` · ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-http`

## §2026-09-23-goalnn-godmodule-jobfs（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **决定**：把 `REPO_ROOT` · `JOB_DIR_KEEP` · `_persist_result` · `prune_job_dirs` ·
  `unpack_opt_tar` · `pack_opt_tar` · `unpack_payload_or_fail` · `_git_head` · `_ensure_commit` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-jobfs`

## §2026-09-23-goalnn-godmodule-bcjob（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **决定**：`_bc_fetch_resume` · `_bc_local_resume_dir` · `_bc_store_local_resume` ·
  `_bc_load_local_resume` · `_bc_post_epoch` · `_bc_device` · `normalize_ppo_device` · ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-bcjob`

## §2026-09-23-goalnn-godmodule-download（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **决定**：`_progress_logger` · `download_payload` / `download_code` / `download_ts_code` /
  `download_blob` · `_cache_blob` / `_resolve_blob` · `_ensure_ts_code`（**252 行**）搬进新 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-download`

## §2026-09-23-goalnn-godmodule-joblifecycle（2026-09-23，用户指令「重构 nn-training：降耦合 / 复用代码 / 可维护性」）

- **决定**：整块搬进新 `remote/job_lifecycle.py`（682 行）：`peek_jobs` · `request_priority` ·
  `claim_job` · `_priority_rank` · `acquire_job` · `job_started` · `job_ready` · `abandon_job` · ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-godmodule-joblifecycle`

## §2026-09-23-goalnn-remote-dag-ledger（2026-09-23，用户指令「给 remote/ 加一条全局无环守卫，把散在各子模块的『不得反向 import』断言收成一张 DAG 对账」）

- **决定**：新增 `tests/helpers/remote_dag.py`（**账本 + 判据实现，只有一处**）与
  `tests/test_remote_dag.py`（整图对账，18 例）；账本两个常量： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-remote-dag-ledger`

## §2026-09-23-goalnn-remote-ring-split（2026-09-23，用户指令「拆掉 remote/ 里唯一那个被登记的延迟环：把 run_loop 的 verify_plan_file / run_plan_job 下沉到 L1，让 worker 直接拿」）

- **决定**：引擎**整块**下沉到新模块 `remote/plan_run.py`（**L2**），`run_loop.py` 只留 CLI /
  独立续跑 / 门面（1451 → **491** 行）；「一轮怎么跑」由调用方**注入**（`worker` 传 `run_job_fn=run_job`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-23-goalnn-remote-ring-split`

## §2026-09-24-goalnn-godmodule-traincore（2026-09-24，用户指令「拆 worker.py 的宿主：按一条真实调用链把 run_job / worker_loop 里的簇切出去」）

- **决定**：按**一条真实调用链**切，共三块（`worker.py` 1814 → **1281**）：
  ① **训练核**（`run_job` 650-1076，427 行）→ 新 `remote/train_core.py::run_training_core`： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-godmodule-traincore`

## §2026-09-24-goalnn-godmodule-jobround（2026-09-24，用户指令「拆 worker_loop 的『每 job 一轮』成 remote/job_round.py（先定档 seam）」）

- **决定**：`worker_loop` 的「每 job 一轮」（L943-1119）下沉 `remote/job_round.py::run_one_round`；
  配套下沉 `_prefetch_fill`（69 行）· `settle_result`（原 `_result_settled` 闭包）· ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-godmodule-jobround`

## §2026-09-24-goalnn-hub-routes-split（2026-09-24，用户指令「拆 hub_server 的其余路由组：把 _get_* 12 / _post_* 11 收成通用助手」）

- **决定**：25 个路由方法按**域**分成四组混入——`hub/schedule.py`（取活/租约/打点）·
  `hub/result.py`（回传与终局）· `hub/blob.py`（字节服务）· `hub/offline.py`（离线段），与 `AdminRoutes` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-hub-routes-split`

## §2026-09-24-goalnn-worker-landing-trio（2026-09-24，用户指令「清掉 remote/job_fs._ensure_commit 死代码并重构 worker.py 余下的 run_job / worker_loop / main」）

- **决定**：`run_job` 的物料落地整段（payload 与 code.zip 的「取字节 + 摆好」）下沉
  `remote/download.py`，与原有 `_ensure_ts_code` 并列为**三兄弟**，各返回 `NamedTuple` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-worker-landing-trio`

## §2026-09-24-goalnn-hub-jobstore-mixins（2026-09-24，用户指令「拆 hub_server 的 _JobStore 状态类（先定档接缝与拆法）」）

- **决定**：`_JobStore` 按**域**拆成六个**混入**（`remote/hub/store_{ledger,wire,scheduling,leases,`
  `results,offline}.py`），组合类只留 `__init__` / `note_worker` 与**进程级状态** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-hub-jobstore-mixins`

## §2026-09-24-goalnn-hub-hubqueue-mixins（2026-09-24，用户指令「拆 _HubQueue 多课程调度面（先侦察那 28 个同名委托方法）」）

拆 `_HubQueue`（1033 行 / 76 方法）——**先侦察那 28 个同名委托方法**，侦察结论改变了整刀的切法。
- **先决条件（A 相）**：第七个混入要按课程构造/注解 `_JobStore`，而 `remote/hub/*` 不得 import……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-hub-hubqueue-mixins`

## §2026-09-24-goalnn-hub-entry-split（2026-09-24，用户指令「拆 hub_server 剩下的引导链与 HTTP 面，收口 S4」）

收口 `remote/hub_server.py`：**887 → 100 行**（累计 3017 → 100，**−97%**），且里面只剩 20 行代码。
- **三件东西，两个新家**：`hub/http_face.py`（**L5**，575 行：来源判定 + `HubHandler` + 通用助手）· ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-hub-entry-split`

## §2026-09-24-goalnn-loop-eval-chain-split（2026-09-24，用户指令「按同一条「真实调用链」手法拆 rl/loop_steps 的 TrainingSteps 本体（952 行 / 20 方法）」）

拆 `rl/loop_steps.py`：**940 → 666 行**；`TrainingSteps`（845 行 / 20 方法）里**唯一一条真正的
方法间调用链**（8 成员 / 275 行 / 5 个状态槽）搬到新模块 `rl/loop_eval.py::TrainingEval`……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-loop-eval-chain-split`

## §2026-09-25-goalnn-loop-volume-chain-split（2026-09-25，用户指令「全程自主，继续按同一手法拆 `rl/` 侧神模块」）

拆 `rl/loop_core.py`：**1386 → 931 行**（搬走的块共 462 行 —— 445 行方法 + 9 行分节注释；
留下的 8 行是旧位置的**指路注释**）；`TrainingLoop`（25 方法 / 1089 行）里**唯一一条真正的……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-volume-chain-split`

## §2026-09-25-goalnn-loop-lifecycle-chain-split（2026-09-25，用户指令「拆 loop_core 余下的生命周期链（7 成员 = 主循环骨架），先答清「拆出去后谁是宿主」」）

拆 `rl/loop_core.py`：**931 → 446 行**；`TrainingLoop` 本体里最后一条方法间调用链（7 成员 / 351 行）
连同 7 个**只能随它走**的模块级定义（150 行，闭包实测）搬到 `rl/loop_lifecycle.py::TrainingLifecycle`……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-lifecycle-chain-split`

## §2026-09-25-goalnn-loop-core-tail-split（2026-09-25，用户指令「拆 loop_core 剩下的基线评估链（2 成员）与叶子方法，让组合根成为纯组合类」）

拆 `rl/loop_core.py` 的**尾簇**：**446 → 240 行**，余下的 7 个叶子按**判据同源**分三簇，各自成模块（合计 355 行 = 128 + 96 + 131）。
`TrainingLoop` 至此是**纯组合类**——只剩 `__init__`（槽位）与 `_run_inspect`（唯一结构性例外）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-core-tail-split`

## §2026-09-25-goalnn-loop-export-cluster-split（2026-09-25，用户指令「继续按同一手法拆 `rl/loop_steps.py` 或 `loop_remote.py`」）

拆 `rl/loop_steps.py` 的**产物出包簇**：**667 → 541 行**，4 个成员 → 新模块 `rl/loop_export.py::TrainingExport`
（**210 行**）。同一对象、同一把锁、零行为变化，测试一行不改。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-export-cluster-split`

## §2026-09-25-goalnn-loop-remote-cluster-split（2026-09-25，用户指令「把 `loop_remote.py` 那 862 行连通分量按「判据同源」拆成多个混入」）

`rl/loop_remote.py` **997 → 35 行**（−96%）。13 个方法（862 行，一条连通分量）按**判据同源**切成四簇，
各成模块（111 / 551 / 112 / 291 行，方法体本身 69 / 470 / 76 / 247），组合根留在原文件且**零方法**。同一对象、同一把锁、零行为变化。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-remote-cluster-split`

## §2026-09-25-goalnn-loop-guards-cluster-split（2026-09-25，用户指令「拆 `rl/loop_guards.py` 或 `loop_steps.py` 剩下的叶子，先给侦察结论与刀口判据」）

`rl/loop_guards.py` **785 → 194 行**（−75%）。13 个方法按**判据同源**切成四簇，各成模块
（177 / 200 / 287 / 78 行），组合根留在原文件并留下**四个共享 sink + 三个判决词表常量**。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-loop-guards-cluster-split`

## §2026-09-25-goalnn-batch-store-interface（2026-09-25，用户指令「给 `batch_eval.py` 设计「批存储」接口，为拆那个 1785 行的文件做准备」）

**决定：`rl/batch_eval.py`（1785 行）的拆分前置件 = 一个 `BatchStore` 接口**（新模块 `rl/batch_store.py`）：
`<EVALBOARD_DATA>` 的台账 `batches.jsonl` 与两个请求文件**只有一个所有者**，`status` / `units` / `node_dist` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-store-interface`

## §2026-09-25-goalnn-batch-ledger-atomic-publish（2026-09-25，用户指令「先把非原子落盘那个缺陷按 §7 写了失败测试再修（不等 B2，独立一小刀）」）

**决定：`rl/batch_eval.write_batches` 从 `Path.write_text` 改为「同目录固定临时名 + `os.replace`」原子发布。**
不改 `batches.jsonl` 的字节格式，不改任何调用方，不碰 B2 的状态机改造。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-ledger-atomic-publish`

## §2026-09-25-goalnn-batch-store-b2-split（2026-09-25，B2：台账/请求面收进唯一所有者 `BatchStore`）

**决定：`rl/batch_eval.py` 拆它的第二步 B2 —— 台账与两个请求文件收进新模块 `rl/batch_store.py`
的 `BatchStore`**（`status` / `units` / `node_dist` 的每次变更 = 一个**具名转移** = 一次事务 = ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-store-b2-split`

## §2026-09-25-goalnn-batch-plan-b1-split（2026-09-25，B1：批语料规划 + 判据/门 纯搬出包）

**决定：`rl/batch_eval.py` 拆它的第一步 B1 —— 23 个成员（13 函数 + 10 常量）纯搬到新模块
`rl/batch_plan.py`**（1 常量 `DEFAULT_DATA_ROOT` 留给旧家，由再导出的 `REPO_ROOT` 派生）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-plan-b1-split`

## §2026-09-25-goalnn-batch-runner-phase-split（2026-09-25，B5a：`_run` 按**相位**切三段，机器体逐字节不动）

**决定：`BatchEvalRunner._run`（821 行）按相位拆成 `_open_unit` / `_run_channels` / `_settle_unit` /
`_log_provenance`，`_run` 退成 6 行相位叙述；机器体（615 行）**逐字节不动**，只多一个显式取值段； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-runner-phase-split`

## §2026-09-25-goalnn-batch-unrunnable-filter-loud-requeue（2026-09-25，B4 发现的既存缺陷：无可跑 unit 的批不再静默卡死）

**决定：`maybe_dispatch_batch` 在「规划成功但 `select_next_unit` 无待跑 unit」时，从**静默 `return None`**
改为**记日志 + `store.requeue`**。** 依据是**代码自身的先例**（§6.2 优先级第三条）——三条兄弟路径 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-unrunnable-filter-loud-requeue`

## §2026-09-25-goalnn-batch-eval-facade-b4（2026-09-25，B4：门面收尾 —— 把「门面是门面」变成机器可判的契约）

**决定：`rl/batch_eval.py` 的第四步 B4 收尾 —— ① 新守卫 `tests/trainer/test_batch_eval_facade.py` 把门面形态钉成契约
（模块级 `def` 闭集 = {`maybe_dispatch_batch`} · 自别名再导出表闭集且对象恒等 · 刻意不转发的名字响亮 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-eval-facade-b4`

## §2026-09-25-goalnn-batch-runner-b3-split（2026-09-25，B3：执行面纯搬出包 `rl/batch_runner.py`）

**决定：`rl/batch_eval.py` 拆分的第三步 B3 —— `BatchEvalRunner` + `dispatch_batch_bg`（连同它独占的
五个常量与 `_heartbeat`，共 8 个成员）纯搬到新模块 `rl/batch_runner.py`；五个常量与 `_heartbeat` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-runner-b3-split`

## §2026-09-25-goalnn-batch-lanes-objectify（2026-09-25，B5b：`_run_channels` 的通道机器收进 `_UnitLanes` —— 状态对象化）

**决定：把 677 行机器体按**状态所有者**收进新类 `_UnitLanes`（★ 住在**同一模块**
`rl/batch_runner.py`，不另开模块）：契约字段 29 → `self.*`（`__init__` 从 `_UnitPlan` 逐名接）、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-25-goalnn-batch-lanes-objectify`

## §2026-09-24-goalnn-train-mode-hot-switch（2026-09-24，plan/train-mode-hot-switch.plan.md 主体 L1 + L3.2）

**「在线/离线」收敛成唯一那颗开关**：`setCourseMode` ① 先落本机训练配置（唯一写面
`actions/train-mode.ts::applyTrainModeToConfig`，域换算仍走 `stack/specs.ts::trainModeKnobs`）……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-24-goalnn-train-mode-hot-switch`

## §2026-09-24-goalnn-x20-metrics-v8（2026-09-24，plan/x20-dodge-avoidance.plan.md §2）

给 metrics 向量永久追加危险暴露四列 `idx41–44`：`playerHpRatio`（hp/maxHp，clamp01，与 obs s19 同源）·
`dangerTicks`（累计 hpRatio<0.4 的 tick）· `threatTicks`（累计「在敌方弹道/炮口线上」的 tick）· ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-x20-metrics-v8`

## §2026-09-24-goalnn-halt-crash-vs-conservative（2026-09-24，plan/x20-dodge-avoidance.plan.md §4.1）

x20 闪避系列的熔断分两类：① **崩溃类**（`mean < 6.19` / `timeout > 5%` / `kl` 连 3 轮 ≥ 0.075）走
`gate-halt-mode=notify`（**只记录不自动停**）+ 人工盯盘；② **保守陷阱类**（`cellsVisited`/`move%`/`pass` ……

—— 全文（背景 / 备选与否决 / 代价 / 回收条件）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-24-goalnn-halt-crash-vs-conservative`

## §2026-09-24-job-identity-collision（2026-09-24，plan/job-identity-collision.plan.md）

**job 身份纳入课程维度，且归属唯一才认**。2026-09-24 用户报障「双课程并行，调度请求 409」，
根因是 `idempotency_key = (runId, it, init_weights_fp, data_fp)` **不含课程**：单 hub 多课程……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-24-job-identity-collision`

## §2026-09-24-goalnn-opt-blob-diet（2026-09-24，plan/opt-blob-diet.plan.md）

回传的 `opt` tar 从 `model.pt + opt.pt` 缩成**只 `opt.pt`**；初始权重改走内容寻址的 **`init` blob**
（`BLOB_NAMES` 加 `init`，`sha = manifest.init_weights_fp` —— **复用既有字段，manifest 字段集不变**）。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-24-goalnn-opt-blob-diet`

## §2026-09-24-goalnn-offline-local-first（2026-09-24，plan/offline-rerun-local-first.plan.md）

**重跑离线 cell 时，起点、计划、代码一律以「机器上已经跑过的那份产物」为准**。决策与实现都落在
`remote/offline_boot.py`（notebook 每次会话从 GitHub raw 刷新它），**不碰 `run_loop`/`bundle` 的导入语义**。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-24-goalnn-offline-local-first`

## §2026-09-24-offline-it0-baseline（2026-09-24，plan/offline-it0-baseline-eval.plan.md）

**离线腿（`rollout_src:'run'`）的 it0 读数由控制台在「离线开课」那一刻补派一次**：`launchEvalA(course, '', 0,
{baseline:true})` → `eval_a_once.py --baseline`，权重缺省取课程活动权重 `out`（= 任务包 `init_weights` 同一份 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-24-offline-it0-baseline`（变更记录 §42）

## §2026-09-25-goalnn-offline-switch-auto-bundle（2026-09-25，plan/offline-switch-auto-bundle.plan.md）

**「切离线」这颗开关必须顺手把任务包导出来；包真没有时 hub 替离线课推一次重导；云机侧一门课取不到包
就跳过继续下一门（末尾汇总、全跳过非零退出）**。用户报障 2026-09-25：在线课切成离线后云机 404 白等 28 分钟……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-offline-switch-auto-bundle`

## §2026-09-25-goalnn-offline-task-discovery（2026-09-25，plan/offline-task-discovery.plan.md）

**离线云机不再在 notebook 里写死课程名**：新增只读清单 `GET /offline/tasks`（课程 + 包 + 新鲜度 +
持有者 + 段内进度；`?include=all` 才带 `not_offline`）+ 硬租约三端点……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-offline-task-discovery`

## §2026-09-25-goalnn-role-routing（2026-09-25，plan/online-offline-role-routing.plan.md）

**「该由哪个盘执行」成为 job 的不变式**：`manifest.role ∈ {offline, online}` 在**发布时**定死
（`publish_job`，`kind` 的同一快照：`run ⇒ offline`；`iter/ppo/bc ⇒ online`；`MANIFEST_KINDS`/`KIND_ROLES`……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-role-routing`

## §2026-09-25-goalnn-cloud-cpu-ledger（2026-09-25，云机 rollout 卡死取证）

**云机上的「rollout 卡死」= 两条 CPU 腿同时开满 + 池的回退放大**（不是导出器坏了、也不是局真的
卡住：超时局 `rc=<仍在运行>`、尾行只到 `[features] native 启用`，是**活着的慢局**）。三处落地：……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-cloud-cpu-ledger`

## §2026-09-25-goalnn-offline-leg-retired（2026-09-25，plan/online-offline-role-routing.plan.md §7）

**「离线 = 本机发一份 `kind=run` 队列项、随后等 8h」这条腿退役** —— 离线场景**没有「一整段」这个
中间概念**（用户 2026-09-25 口径）：云机接手一门课就一直跑，直到 ① 跑完课程 ② 云机配额用尽……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-offline-leg-retired`

## §2026-09-25-goalnn-online-worker-no-bun（2026-09-25，plan/online-offline-role-routing.plan.md §9）

> 前身：同日 16:39 曾立过一条 `…-cloudflared-notebook-retired`（「把 cloudflared 盘退役」）——
> **那条是误判，已于当日全量撤回**（notebook 已恢复原状）。留下这个错形状，防止再犯。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-online-worker-no-bun`

## §2026-09-25-evala-baseline-bc（2026-09-25，it0 基线污染修复）

**背景**：x20-dodge-l1/L3 的 `eval_log` it0 各混入 200 局后期权重读数（L1 混 it43、L3 混 it51），
基线 low 被带偏 6pp（L1 it0 34.0 真值应为 40.0）。门控 verdict 不受影响（独立段），日常分析已更正。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-evala-baseline-bc`

## §2026-09-25-goalnn-rollout-reap-wedge（2026-09-25，云机 rollout 二次卡死取证）

**现场**：battle.offline.ipynb × x20-dodge-l3（96 核配额）。§23 的三件套都已生效（`workers=92`、
健康轮 336 局 3.29s、熔断会响），但两次各损失 15–24 分钟：开跑 5s 内 ≥23 局踩硬顶 ⇒ 熔断 ⇒……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-rollout-reap-wedge`

## §2026-09-25-goalnn-eval-round-retry（2026-09-25，eval 腿对齐机器级停滞口径）

**现场/来历**：`§2026-09-25-goalnn-rollout-reap-wedge` 把 rollout 腿的「机器级停滞」改成轮内重投，
但**eval 腿只改了一半**：`rl/eval_local.run_eval_runner_capture` 已经用进程组 + 有界回收（收不了尸……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-eval-round-retry`

## §2026-09-25-state-init-course-key（2026-09-25，课程键 `state_init` 进 CourseConfig）

> _(本条的 `rebase_counters` 字段与「中段交棒」措辞已在同日被 §2026-09-25-state-init-snapshot 修订：
> 起始分布改为**快照注入**，`rebase_counters` 删除。本条只保留「课程键怎么进配置层」那部分。)_ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-state-init-course-key`

## §2026-09-26-goalnn-refactor-merge（2026-09-26，本地 S4 重构分支并入 origin/goal-nn）

**背景**：本地 `goal-nn`（43 个纯重构提交，S4 拆 `rl/` 神模块）与已推送的 `origin/goal-nn`
（32 个语义提交）分叉，合并基准 `8448028`，以 `--no-ff` 并入。 ……

—— 全文（冲突清单 / 门禁证据）→ 合并 commit message

## §2026-09-25-state-init-snapshot（2026-09-25，rollout 起始分布 = 人类中段**世界快照**注入）

**来历**：六条价格腿全阴性后的第七条攻击面 = 只换 rollout 的起始**状态分布**、不动任何价格项
（plan/x20-state-init.plan.md）。首版设计是「人类**磁带**（逐 tick 输入）headless 快进 T tick……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-state-init-snapshot`

## §2026-09-25-goalnn-prefetch-p0-not-preempt — 抢占权专属 P1：控制面只让路 + P2 补齐开工门禁 + 挤走不占重试预算（plan/bulk-p2-preempt-fix）

**背景**：2026-09-24 现场（x20-dodge-l1 / x20-dodge-l3 双课程 + 单云 worker）：**预取零命中**，每个 job 都刷
`定期预取被高优 bulk 挤走 → 重试次数用完，放弃这份提前量`，且**每条** payload 下载都带……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-25-goalnn-prefetch-p0-not-preempt`

## §2026-09-25-local-rollout-serve-pool（2026-09-25，本机腿入池：trainer 自己的两条本机腿接 `--serve` 长驻池）

**决定**：① 要不要建池由**真 argv**（`build_rollout_cmd` 为第一个 pair 拼出的那份）决定，
`make_local_pool` 只转发给 `serve_pool.make_pool` 的白名单——脚本不在名单里（goal / intent 两种 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-25-local-rollout-serve-pool`

## §2026-09-25-clutch-null-kill（2026-09-25，paired-kill 杀错臂 + 重启键坏，两处修）

**决定**：① `courses.<课>.paired_kill.self_kill`（rl-config 执行面，缺席/写坏=True，即现状；
  只有显式 `false` 才关）：命中时判据照算、streak 照落账，只是不停车（`loop_guards._paired_kill` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-clutch-null-kill`

## §2026-09-25-state-init-routing（2026-09-25，起始分布派发：改路由不改放行名单）

**决定**：`state_init` 开 ⇒ 整轮直接交纯本机 `run_rollout`、向 pool 零派发，跟节点健康度无关
（`dispatch.run()` 入口，替代旧闸门；旧测试①按新契约改写）。additive-only 照旧。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-25-state-init-routing`

## §2026-09-26-goalnn-notorch-half（2026-09-26，python 门禁的「免 torch 半边」拆分纪律 + 静默绿变红守卫）

**背景**：nn-training 的 python 门禁把 ~3000 用例一次跑完；31 个测试文件因 `import torch`
（或运行期延迟 import）无法在**没有 torch 的机器/镜像**上跑。拆分的收益是**可移植性 / 覆盖**……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-26-goalnn-notorch-half`

## §2026-09-26-goalnn-contract-scan-layer（2026-09-26，运行期契约判据拆成「源码扫描层 + ground truth + 交叉校验」）

**背景**：`tests/worker/test_backend_contract.py`（P0-1 的捕获器：三套 PPO 后端必须满足
`RolloutBackend` 的 5 个成员 + `update` 必须接受 stream.py 无条件注入的 `ckpt_path` /……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-26-goalnn-contract-scan-layer`

## §2026-09-26-goalnn-notorch-final-16（2026-09-26，剩下 16 个收集失败文件逐条终判：再匀出 6 条）

**做完的**（都是「被同文件的 torch 邻居连坐」，搬到主题相同且免 torch 的孪生文件）：
`test_ppo_numerics` 的 3 条 GAE（numpy 口径）→ `test_np_core`；`test_shard_split` 的……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-26-goalnn-notorch-final-16`

## §2026-09-26-paired-kill-opt-in（2026-09-26，配对杀臂默认关火）

**决定**：`courses.<课>.paired_kill.enabled` 显式 `true` 才判（缺席/写坏=False；关了的守卫
连 streak 落账都不写）。C-w 设 `enabled: true`（课程要的中点杀臂仍在）+ C-0 保持 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-paired-kill-opt-in`

## §2026-09-26-nodes-decouple-from-course（2026-09-26，节点统计与课程解耦 + 按天窗口）

**决定**：① 数据源合并**所有**训练流（课程目录 + 独立 eval run 目录），逐流取完成水位
（账本 `iteration`）**只用于过滤进行中那一轮**；② 视图按**本地日**分桶、`?days=` 切窗口 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-26-nodes-decouple-from-course`

## §2026-09-26-course-archive-compress（2026-09-26，课程封存默认 gzip：重开 O3「不压缩」）

**决定**：
① **默认 gzip（level 6）逐件压缩 L0 文本件**（`*.jsonl` / `*.json` / `.console.log` / settle 行）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-26-course-archive-compress`

## §2026-09-26-course-archive-guard-and-startpoint（2026-09-26：封存硬闸补第二道新鲜度 + 起点 sha 可验）

**决定**：
① **新鲜度升为与 marker 无关的第二道闸**：目录新鲜（不论有无 marker）默认拒绝封存； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-26-course-archive-guard-and-startpoint`

## §2026-09-26-x20-steady-cont（2026-09-26，B续跑立项：八连阴后唯一阳性斜率 + H1/H2判决实验）

**决定**：开 `x20-steady-cont`（`nn-training/curricula/x20-steady-cont.jsonc`）——与 B
逐字同（奖励/位移/采集全同，validate 零错零警），起点 B-it175 纯 warm start ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-26-x20-steady-cont`

## §2026-09-26-hub-mode-restore-enabled-only（2026-09-26，用户报障：起 hub/trainer 等很久——回灌把停掉的历史课逐课重试）

**决定**：`restoreCourseModes` **只回灌已开课的课程**（判据 = 开课标记
`stack/courses.ts::courseEnabled`，与 hub 同源）。未开课的课进 `skipped`（**不是失败**）： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-09-26-hub-mode-restore-enabled-only`

## §2026-09-26-rl-config-scope（2026-09-26，`rl-config.json` 职责边界：只留「别处无处安放」，删配置不删代码）

**决定**：`nn-training/rl-config.json` 只保留五类里的 A（机器/环境级）、C（调度策略）、D（每课
机器侧旋钮活课）；B（课程文件已有同名键的全局缺省）只留必要兜底；E（废弃/死键）删。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-rl-config-scope`

## §2026-09-26-rl-config-b-class（2026-09-26，B 类兜底键删除：范围取「全部课程」超集 + `workers` 归机器级）

**触发（用户 2026-09-26）**：「在训练机跑 `rl_config_clean.py --matrix`，按全绿结果删掉多余 B 类兜底键」。
**前置事实**：训练机（= 本机，hub / sampler / trainer 都活在这个工作区）上操作员已于 17:49 把最后……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-rl-config-b-class`

## §2026-09-26-x20-series-close（2026-09-26，x20 全系列关账：八腿结论 + L1/收入侧判死 + 转向新纪元）

**决定**：
① L1/收入侧两方向判死：不开 wOpen=0.03，不升 (5.0,6.0)；② L2b 的 B2 升档（wThreat=60） ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-26-x20-series-close`

## §2026-09-26-goalnn-stop-k-is-training-side（2026-09-26，K=10 决策门周期属训练侧：可动）

**决定**：**K 是训练侧可动参数**。改 K 只改「策略每隔多少 tick 重新观察一次」，不改任务的胜负
条件与单位 ⇒ 收紧 K（如 10→5）属于训练侧调参，不触发 §0.2 禁令，也不需要新关卡/新任务口径。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-goalnn-stop-k-is-training-side`

## §2026-09-26-goalnn-stop-mdp-no-change（2026-09-26，B案不构成 MDP 变更声明）

**决定**：**B案不是 MDP 变更**。状态空间、转移、奖励、K=10 的均匀决策边界全部未动；变的只是
「同一个动作索引代表哪个物理命令」，而该映射在仿真里本来就存在（`moveDir=null ⇒ moving=false`）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-goalnn-stop-mdp-no-change`

## §2026-09-26-goalnn-stop-rng-principle（2026-09-26，RNG/特权观测不进 shard：原则存档）

**决定**：**特权观测一律不进 shard / 不进 obs**。判据①进 shard = 格式变更（`OBS_SCHEMA_MAJOR`
bump + 全量重转），且会让已训 value 头的输出量纲漂移（旧 checkpoint 不可比）；②敌开火倒计时 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-26-goalnn-stop-rng-principle`

## §2026-09-27-goalnn-eval-rows-split（2026-09-27，`rl/eval_local` 的 eval 行/账本簇下沉 `rl/eval_rows`）

**决定**：把 `rl/eval_local.py` 的「逐局 eval 行 schema + `eval_log` 账本 I/O」整块（**连续行 287–602，
逐字节不动**；17 个名：三字段抽取器 + `eval_row`/`eval_row_key`/`eval_row_keys` + 账本读/并/去重）搬到新模块 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-eval-rows-split`

## §2026-09-27-goalnn-bc-ingest-split（2026-09-27，S5 第四刀：`rl/bc_loop` 的 BC job 回传消费下沉 `rl/bc_ingest`）

**决定**：把「等一个远端 BC job 回传、并把回传入账」整块（两段跨度共 11 名，**逐字节不动**：
`IDLE_WARN_SEC`/`POLL_SEC`/`POLL_MAX_SEC` + `READY`/`PENDING`/`TRANSIENT` + `BcWait` + `ledger_bc_epoch` + ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-bc-ingest-split`

## §2026-09-27-goalnn-hub-http-split（2026-09-27，S5 第五刀：`remote/hub_client` 的 HTTP 往返分量下沉 `remote/hub_http`，把 HTTP 面做成可注入薄壳）

**决定**：把 `remote/hub_client.py` 里「把一次请求发到 hub、把回答读回来」那一簇整块搬到新模块
**`remote/hub_http.py`**（**两段跨度共 17 名，逐字节不动**，478 行；原 1688 → **1299 行**）。搬走： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-hub-http-split`

## §2026-09-27-goalnn-protocol-split（2026-09-27，S5 第六刀：`common/protocol` 的失败类型族与线格式下沉 `common.errors` + `common/wire_codec`）

**决定**：把 `common/protocol.py`（**1708 行**）里两簇**格式/契约**面整块搬出（**三段跨度，逐字节不动**）：
* **失败类型族** → **`common/errors.py`**（118 行）：`ProtocolError` · `RetryableError` · ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-protocol-split`

## §2026-09-27-goalnn-job-identity-split（2026-09-27，S5 第七刀：`common/protocol` 的 job 身份簇下沉 `common/job_identity`）

**决定**：把 `common/protocol.py`（**1482 行**）的 **job 身份簇**整块搬到新模块 **`common/job_identity.py`**
（134 行，**一段跨度共 4 名，逐字节不动**）：`idempotency_key`（D1 幂等键，含 2026-09-24 加的 `course_fp`）· ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-job-identity-split`

## §2026-09-27-goalnn-payload-split（2026-09-27，S5 第八刀：`common/protocol` 的语料归档面下沉 `common/payload`）

**决定**：把 `common/protocol.py`（**1390 行**）的**语料归档（payload）面**整块搬到新模块 **`common/payload.py`**
（213 行，**三段跨度共 10 名，逐字节不动**）：`PAYLOAD_NAME` / `PAYLOAD_LEGACY_NAMES` · `INIT_WEIGHTS_NAME` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-payload-split`

## §2026-09-27-goalnn-manifest-split（2026-09-27，S5 第九刀：`common/protocol` 的 manifest/rollout 校验面下沉 `common/manifest`）

**决定**：把 `common/protocol.py`（**1238 行**）的 **job manifest 契约面**整块搬到新模块 **`common/manifest.py`**
（547 行，**四段跨度共 38 名，逐字节不动**）：① `PROTO`；② 角色**词汇** `ROLE_OFFLINE`/`ROLE_ONLINE`/`ROLES`/ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-manifest-split`

## §2026-09-27-goalnn-config-faces-split（2026-09-27，S5 第十刀：`rl/config.py` 的文件面/类面/解析面下沉 `rl/config_file` + `rl/course_spec` + `rl/course_resolve`）

**决定**：把 `rl/config.py`（**1566 行**）按「独立所有者 + 独立触发条件」切成三面，三个新模块（**逐字节不动**）：
① **文件面** `RL_CONFIG_ENV` · `rl_config_path` · `read_rl_config_file` → **`rl/config_file.py`**（55 行，stdlib + `dist_common`）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-config-faces-split`

## §2026-09-27-goalnn-dist-clusters-split（2026-09-27，S5 第十一刀：`dist_common.py` 的 shard 面与权重下发账本下沉 `dist_shard` + `dist_weights_ledger`）

**决定**：把 `dist_common.py`（**1503 行**）里两个小簇按「独立所有者 + 独立触发条件」搬出（**逐字节不动**）：
① **shard 面**（7 段 7 名）——三份 shard 清单 + `BC_COLLECTOR` + `_shard_files_for` / `validate_result` / `write_shard` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-dist-clusters-split`

## §2026-09-27-goalnn-eval-yield-split（2026-09-27，S5 第十二刀：评估「让位/份额（尾巴）策略」两处一起切 → `rl/eval_yield.py`）

**决定**：按「独立所有者 + 独立触发条件」把评估的让位/份额（尾巴）策略做成**独立所有者**——从**两处一起**切：
① `rl/eval_local.py` 的 L110–L226（**117 行**：`EVAL_LOCAL_SLOTS_DEFAULT` / `EVAL_LOCAL_RELEASE_GRACE` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-eval-yield-split`

## §2026-09-27-goalnn-plan-handoff-split（2026-09-27，S5 第十三刀：`plan_run` 18 节点交接面下沉 → `remote/plan_handoff.py`）

**决定**：按 `plan/nn-training-refactor.md` §5.7.4 落刀——`remote/plan_run.py`（**1090 行**）的**交接面**
18 名（面日志 + 校验 2 · `RunContext` 1 · 取包播种 9 · 评估装配 3 · 常量 3）整块下沉为 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-plan-handoff-split`

## §2026-09-27-goalnn-offline-deliverable-split（2026-09-27，S5 第十四刀：offline_boot 交付面 + standalone 运输面 → `remote/offline_deliverable.py`）

**决定**：把 `remote/offline_boot.py`（1801）的**交付面**（13 节点 / 179 行干净闭集：交付三函
`package_deliverable`/`_partial_last_it`/`package_partial` + 课程路径链 5 名 + 4 常量 + `_COURSE_NAME_RE`） ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-offline-deliverable-split`

## §2026-09-27-goalnn-gate-faces-split（2026-09-27，S5 第十五刀：`gate_check` 求值器接口 —— 输入面 + 判决面成家）

**决策**：1412 行 `rl/gate_check.py`（`recon_god` 口径「1 组 31 节点巨团」；引用图实测 **47 节点 = 46 单团 + 1 孤立**
（`sum_train_samples`））一刀切两面、成两个新家（纯搬 + `X as X` 门面，S5 收官刀）： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-27-goalnn-gate-faces-split`

## §2026-09-28-goalnn-python-loc-budget（2026-09-28，用户指令「给 python 门禁加一个测试：python 源文件 loc 必须 < 1000 行（tests/e2e · offline_boot 除外，不包括注释/空行/doc）」）

- **决定**：新增 `nn-training/tests/test_python_loc_budget.py` 进 python 门禁——全仓 `.py`（git 跟踪 +
  未跟踪未忽略）的 **LOC = 物理行 − 空行 − 纯注释 − docstring** 必须 `< 1000`（**≥1000 即红**）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-28-goalnn-python-loc-budget`

## §2026-09-27-goalnn-r2-event-gate（2026-09-27，x2 事件门契约：γ 保 per-step + 交棒首段禁事件 + Δt≥3）

**决定**（三项，一次定死）：
① **γ 保持 per-step**（`nn-training/rl/config.py:974` γ=0.995），**不**切半 MDP `γ^Δt`。换算声明： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-27-goalnn-r2-event-gate`

## §2026-09-28-goalnn-console-metrics-harvest（2026-09-28，逐轮实际值抗轮转抄录：表列不再依赖「谁在屏幕上」）

**决定**：
① 控制台侧修：`snapshot-refresher` 新增抗轮转抄录（**独立 15s 节拍**），对**所有已开课**课 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-28-goalnn-console-metrics-harvest`

## §2026-09-28-goalnn-bc-retrial（2026-09-28，h5 四腿全阴后的 BC 翻案重测）

- **决定**：同语料原样重训 BC-ref2（镜像§32：kaggle cuda/60 epochs/val-split/从随机起，唯一变量＝语义修正）；
  held-out 重定义 255 对（c20-lives1，排除 61 demo 对，名单先 pin，原名单随 tmp 丢失不考古）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-bc-retrial`

## §2026-09-28-goalnn-b2-c20-qualify-2（2026-09-28，B2@c20 须先验密度，不自动继承）

- **背景**：h5e 处理量 ~0.5% 判事件无边际；R2 基线 c20 仅 1~4 次/局；c05/c20 开局同源且信用 c20 更难。
- **备选与否决**：B2@c20 直开 —— 否，预期读数与 h5e 同带，是换地方重复；
  先开 B1 信用窗 —— 否（暂缓：plan §4 在 idle 不动后第一分支是 B2，跳分支等于改排序，需另立决策，不在本条）。
- **决定**：c20 开事件腿须先验 onset 处理量 >5%（shard manifest 同口径）；否则 B2 归档，信用窗/DAgger 排队。
- **违反后果**：直开 ⇒ 再烧 80~100 轮横盘腿，读数与 h5e 同带。
- 全文 → `docs/nn/experiments.md` §46。

## §2026-09-28-goalnn-god-no-wait-3（2026-09-28，God 教不了等＋God-BC 等数据污染）

- **决定**：等的老师只认人类；God 数据禁用于一切教等用途（教打另议）；人类 DAgger 先估
  工具＋sessions 成本再立项；§32⑤ 的 God-bulk fallback 加同注。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-god-no-wait-3`

## §2026-09-28-goalnn-fusion-last-4（2026-09-28，融合只能是终端步：PPO 洗等）

- **决定**：顺序倒置——先爬梯（PPO 只打）→ c20 终端融合（CE-dose 彩票先、
  DAgger-at-c20 重器后，门即 c20 真门）→ 直接部署，其间不再 PPO； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-fusion-last-4`

## §2026-09-28-goalnn-retention-ppo-5（2026-09-28，35关继续PPO：留存三件套）

- **决定**：留存三件套（行为地板熔断＋小 kk 链＋信用回流），全现成零件，开训前 pin
  进课程文件；Router 分簇在后（plan §8 B4）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-retention-ppo-5`

## §2026-09-28-goalnn-c20-target-6（2026-09-28，c20 靶子错了：等≠桥，1命≠产品）

- **决定**：靶子重定为开局生存（动，不用等）＋残局转化；下一步按便宜排序
  （确认产品命数→人类真 low%→开局专项辅助目标），等只是候选药之一。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-c20-target-6`

## §2026-09-28-goalnn-god-out-7（2026-09-28，God 全退训练管线：同 seed 11.5%）

- **决定**：往后一切教师位置只认人类（Opening-BC 切片、人类 DAgger、C 重开的对局理解）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-28-goalnn-god-out-7`

## §2026-09-28-goalnn-persist-prewarm-breaker（2026-09-28，a95 贡献量 1/10 归因：池启动预热 + 熔断收窄为「只停补位」）

**现场**：a95（Android/Termux/proot，SM7250，rl-config concurrency 6）rollout 贡献 ≤ mac 的 1/10。
真机取证（adb 上机）：轮参与率 **567/5119**（`ping failed — excluded` 2029 次 + 半路回线 174 次）、……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-28-goalnn-persist-prewarm-breaker`

## §2026-09-28-goalnn-persist-homogeneous-serve（2026-09-28，长驻池改**同质进程**：单入口 + 每任务 mode token，删掉整套「腿」策略）

**问题**（用户提出）：池按导出器分「腿」（rollout 一类、eval 一类……），于是预热得**猜腿**
（按盘上权重 mtime）、猜错腿的那一波每局都退回一次性 spawn（a95 实测 **3.1s/局** vs 对腿 **1.39–1.55s/局**），……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-28-goalnn-persist-homogeneous-serve`

## §2026-09-28-goalnn-local-serve-pool-homogeneous（2026-09-28，Python 侧长驻池也同质：单入口 + 每任务 mode token，门槛与池的形状解耦）

**问题**（用户点名）：TS 侧 agent 池已改成同质入口（`tools/sim/serve-any.ts` + 每行 mode token，
§2026-09-28-goalnn-persist-homogeneous-serve），而 Python 侧那份池（`remote/serve_pool.py`，……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-28-goalnn-local-serve-pool-homogeneous`

## §2026-09-28-goalnn-gate-baseline-8（2026-09-28，门必须配平凡基线）

- **决定**：一切行为门（plan 课程/探针的通过线）在 pin 数字的同时，必须 pin 一个
  同口径平凡基线（重复上一步/多数类常数/单特征，共位计算），门过线且**显著超基线** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-28-goalnn-gate-baseline-8`

## §2026-09-29-goalnn-metrics-v9（2026-09-29，观测列先于定价；改谓词必须断代）

- **决定**：`METRICS_VERSION 8→9`、行宽 `45→54`，尾部追加 9 列：`backHits/sideHits/frontHitsExempt/`
  `farHits/geoFallback`（idx45–49）＋`onLaneTicks/onLaneExemptTicks/onLaneMoveTicks/onLaneHoldFireTicks` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-metrics-v9`

## §2026-09-29-goalnn-publish-metrics-version-gate（2026-09-29，行宽版本门进**发布端**：按 shard 自己的声明剔，不送必炸字节上 GPU）

- **决定**：发布端 `remote.hub_client.iter_shard_dirs(..., metrics_version=<本机 METRICS_VERSION>)` ——
  manifest 声明的 `metrics_version` ≠ 本机 ⇒ **不进 payload**（与 D14 血缘过滤、state_init 起始分布过滤同族语义： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-publish-metrics-version-gate`

## §2026-09-29-h4-nolane-open（2026-09-29，h4 移动穿越税新实验；§63 豁免待用户）

- **决定**：开 h4 平行双臂（h4-geo 去 explore／h4-nolane 加移动税，两臂公式只差一项）；
  同 bc（h3-geo.it45，379114B，sha256 `3a5cba10…e8ac` 已验）＋同 `paired_rotate_seed=1790491796` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-29-h4-nolane-open`

## §2026-09-30-h4-lane-wave1-verdict（2026-09-30，Wave 1 三臂判**无效**：控制臂自漂 ≥ 目标效应 + 功效不足；A2 的 ABORT 判**假阳性**）

- **背景**：E0 判红「绕后奖」→ 机制归到穿越税（§2026-09-29-h4-nolane-open）→ F1 臂身份锁（训练 `ladder-c04`
  （`count 4`/`max_ticks 3300`），评估 `ladder-c20-lives1`（`count 20`/`12900`）——两关 grid / enemy_spawns /……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-h4-lane-wave1-verdict`

## §2026-09-30-goalnn-kickstart-burn-paired（2026-09-30，止损参照物改为同网格配对差；`auto` 缺省 = 对已配对课的行为变更）

- **决定**：参照物两档。`baseline` = 旧口径（本腿 `it<=0` 末条）；`paired` = Δ = 本臂 − **对端同 it**
  读数（pp），尾部连续 `points` 点 Δ < −`margin_pp` ⇒ 触发。常量不变（`BURN_POINTS=3` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-30-goalnn-kickstart-burn-paired`

## §2026-09-30-goalnn-wave2-design（2026-09-30，Wave 1 三项遗留定案：漂移按共模消、剂量不重锚、几何只留 A1）

- **② 漂移按共模消**：主终点保持**同 iter 同 seed 配对差**（对照臂 = 同时训练的零奖励臂），漂移降为
  **每个评估点必报的诊断量**。依据（it20）：三臂自漂 +25.4 / +18.7 / +24.0%（绝对 +1.44pp），配对差 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-wave2-design`

## §2026-09-30-goalnn-wave2-dose-ladder（2026-09-30，Wave 2 = 剂量阶梯 {7.5%, 15%}；含括号外档位）

- **定案**：Wave 2（b 腿）= 零奖励对照 + **A1 几何两档剂量**：**f = 7.5%（wA1 265）** 与
  **f = 15%（wA1 529）**；判据 = 尾巴 3 点均值 × **300 局/点**（每条线一个数字 ⇒ 工具 §11 表）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-wave2-dose-ladder`

## §2026-09-30-goalnn-wave2-verdict（2026-09-30，Wave 2 三臂判决：两条剂量都不到绿线；7.5% 是唯一可靠正效应、15% 判零效应 ⇒ 剂量旋钮到顶）

- **主判定（预注册判据一字未改）**：主终点 rA2 = `onLaneTicks/(ticks+1)`，配对差 = 本臂 − `b0`（同 stage 同 seed）；
  判据量 = 尾巴 3 点（it30/35/40）Δrel 均值 × 300 局/点 @ 池外 seed0 `862001`（与日常段 860001–860200、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-wave2-verdict`

## §2026-09-30-goalnn-lever-scan（2026-09-30，塑形杠杆扫描：lane 族到顶、危险/卡死/固定窗/**停轴**退场；新价唯一候选 = 伤害形态）

- **背景**：Wave 2 判「lane 剂量轴到顶」⇒ 下一步换塑形杠杆、选型未定。用已有账本把候选逐个筛一遍
  （工具 `nn-training/tools/lever_scan.py` 九节，测试 `nn-training/tests/test_lever_scan.py`；全文 `docs/nn/experiments.md` §70）。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-lever-scan`

## §2026-09-30-goalnn-metrics-v10（2026-09-30，plan/metrics-v10-gap-columns.plan.md；只加观测、零公式、零训练腿）

metrics 向量尾部追加「差距四族」15 列（`idx54–68`）：输入级 `stopTicks`/`fireHeldTicks`/`idleTicks`(54–56) ·
距离 `enemyDist`(57，哨兵 −1)/`nearEnemy4Ticks`(58) · 伤害时间形态 `damageBursts`(59)/`maxDamage120`(60)/……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-metrics-v10`

## §2026-09-30-goalnn-damage-form-retired（2026-09-30，伤害「时间形态」E0 判红 ⇒ 家族退场；plan/damage-time-form.plan.md）

- **背景**：§70 Ⅷ ② 判「lane 剂量轴到顶」，唯一新价候选 = 伤害的**时间形态**（变「挨打的集中度」而非窗/总量/零伤）。
  定立项时先跑 **E0 判决表**（预注册 K1/K2/K3/K4，plan §5）——探针 `tmp/dmgform-e0.ts`（不入库，reported 级），……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-09-30-goalnn-damage-form-retired`

## §2026-10-01-goalnn-exempt-bakein（2026-10-01，豁免机制改「导出器拌入计数列」（决定 A）；plan/aim-dodge-levers.plan.md v10 增补批次）

- **背景**：v9 起豁免以「raw + 加法豁免列」对存在（`onLaneExemptTicks`、v10 `enclExempt1/2/3pTicks`）；一个豁免场景要为每个受影响族各加列（族×场景）。
  用户 2026-10-01 指令：找不随场景加列的通用表达，并拍板方案 A。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-exempt-bakein`

## §2026-10-01-goalnn-aim-dodge-metrics（2026-10-01，aim-dodge 杠杆 8 列落地：idx66–73 / dim 74 / 事件扩展 / 回写机制；plan/aim-dodge-levers.plan.md §11）

- **背景**：`docs/nn/experiments.md` §71 人类 vs NN 面板判出「射术 / 暴露管理」四族全程分不开，但 v9 无列可测；
  用户 2026-10-01 定稿 aim-dodge 批次（开火结果族 / 火线承伤族 / 暴露杠杆族）并**裁决保留 `aimBricks`**（经典关复用）。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-aim-dodge-metrics`

## §2026-09-29-goalnn-pytest-scan-cache（2026-09-29，最慢用例清算：全仓扫描「先廉价预筛、再 parse」+ 派生小结果才可缓存）

**决策**：仓库内**任何「全仓源码扫描」守卫**（分层 / 单一来源 / 注入点 / 退役标识 …）都必须先付一道
**廉价子串预筛**，再 `ast.parse` 或 `tokenize`；跨用例重复要的**派生小结果**（模块名集合 / 顶层定义面 ……

—— 全文（测速配方 / 十条对照 / 硬顶 0.5s 的实测依据 / 效果表）→ `docs/nn/engineering.md` §43

## §2026-09-29-goalnn-wait-gates-not-test-walls（2026-09-29，剩余 ~1s 用例群清算：「闸门时长」不得同时是用例的墙钟）

**决策**：测试侧提速时，凡遇到「墙钟 = 等满某个**为生产语义**写的闸门/地板/步长」，一律
**把那个量提成旋钮**（具名模块/类常量、参数或实例属性），**生产缺省逐字不变**，用例按自己的 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-wait-gates-not-test-walls`

## §2026-09-29-goalnn-fork-after-collect-runner（2026-09-29，Linux 门禁的 pytest 分发改成「收集一次 + fork」）

**决策**：nn-training 的 pytest 分发在 **Linux** 上改用「master 收集一次 → `os.fork()` 出 worker」
（`nn-training/tools/forkdist.py`，POSIX-only 插件，`--forkdist N` 显式开启，与 xdist 的 `-n` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-fork-after-collect-runner`

## §2026-09-29-goalnn-forkdist-entrypoints（2026-09-29，`task.py` / Makefile 的 pytest 分发也改 forkdist，CI 实测后继续 `-n 2`）

**决策**：把 §2026-09-29-goalnn-fork-after-collect-runner（当时**只接了门禁**、并把「推广到
`task.py` / Makefile / CI」列为未做）逐个入口实测后处置——**`task.py`（`check` / `test*`）与 `Makefile`（`test` / `test-fast` / `test-e2e`）采纳**： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-29-goalnn-forkdist-entrypoints`

## §2026-09-29-goalnn-single-instance-guard（2026-09-29，一个 node id 只准有一个 agent 在听：互斥在应用层，不靠内核 EADDRINUSE）

**决策**：「一个 node = 一个监听实例」是 codeHash 门的前提，必须由 agent 自己保证。起听**之前**做一次
互斥判定（`tools/agent/single-instance.ts` 纯函数，判定表见全文）：**HTTP 探活 `/v1/status` 优先于 pid ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-09-29-goalnn-single-instance-guard`

## §2026-09-29-goalnn-node-bootid-gate（2026-09-29，节点门多一道「本轮 bootId 一致」：训练侧用 ping 的 pid/bootId 判 stale 并排除）

**决策**（用户裁定，采纳 `plan/sampler-single-instance.plan.md` §8-Q2 的**备选**而非其原推荐）：训练侧
节点门在 codeHash/bun 之外多一条「**本轮 bootId 一致**」——新模块 `nn-training/rl/node_identity.py` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-09-29-goalnn-node-bootid-gate`

## §2026-09-30-goalnn-nn-training-module-reorg（2026-09-30，nn-training 按角色重组为六包：`common/biz/worker/remote/hub/trainer`；第一刀 hub 出包）

**决策**（用户 2026-09-29 拍板口径，2026-09-30 追加 Q5 并落地第一刀）：把 `nn-training/` 的文件从
「按历史拆分动机散落」重排为**六个按角色命名的包**，并把依赖方向写成**可机器校验的偏序**： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-module-reorg`

## §2026-09-30-goalnn-nn-training-l0-consolidation（2026-09-30，刀 2：`common/` 收口为**唯一 L0**；`dist_*` 去前缀）

**决策**：把散在 `nn-training/` 根下的 11 个 L0 模块全部收进 `common/`，其中三个 `dist_*` 去前缀： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-l0-consolidation`

## §2026-09-30-goalnn-nn-training-worker-package（2026-09-30，刀 3：节点侧执行体出包为**顶层 `worker/`**，坐在 `remote/` **下面**）

**决策**：`remote/iter_rollout.py` + `remote/serve_pool.py` → 顶层 **`worker/`** 包，位于 **L2**
（`remote/` 是 L3、`hub/`/`trainer/` 是 L4）。**就这两个**：其余候选（`job_fs` / `job_lifecycle` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-worker-package`

## §2026-09-30-goalnn-nn-training-biz-package（2026-09-30，刀 4：`rl/` 拆出**顶层 `biz/`** —— 纯逻辑与编排分家）

**决策**：`rl/` 里**不达传输/执行面**的 **64 个模块** → 顶层 **`biz/`** 包（L1，与
`models/` `ppo/` `data/` `train/` `scripts/` 并列）。判据**不是手感**，是刀 3 之前就存在的 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-biz-package`

## §2026-09-30-goalnn-nn-training-trainer-package（2026-09-30，刀 5 收官：编排整包改名 `rl/` → **顶层 `trainer/`**，`rl/` 从此不存在）

**决策**：`rl/` 剩下的 **37 个编排模块**（每个都直接或经包内传递可达 `remote|worker`）整包改名
**`trainer/`**（L4，与 `hub/` 同层、互不 import）；**成员一个没变**，只是整包换门牌，`rl/` 这个包 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-nn-training-trainer-package`

## §2026-09-30-goalnn-dashboard-sentinel-paths-must-exist（2026-09-30，`specs.ts` 哨兵路径必须实存 —— 孤儿哨兵修复 + 机械守卫）

**背景**：`dashboard/src/stack/specs.ts` 的 `localWorker` / `trainingLoop` 各有 `sentinel`（`core/reload.ts`
的 `snap()` 记 mtime，变了就重启进程；`core/sentinels.ts` 比对）。它是 `codehash-files.txt` 之外的**手工补面**，……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-09-30-goalnn-dashboard-sentinel-paths-must-exist`

## §2026-09-30-goalnn-worker-local-training-stack（2026-09-30，刀 6：`worker/` = 本地 torch 训练全栈；`biz/` 只留游戏业务）

**背景**：用户 2026-09-30 三句话逐步收紧口径 —— ①「纯训练的内容都放在 `worker/` 下，比如 `rl/bc/ppo` 这些与业务
（游戏）逻辑无关的训练代码」②「`worker/` 应该包含所有支持**本地 torch 训练**的所有代码，**云机 worker = local……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-worker-local-training-stack`

## §2026-09-30-goalnn-entry-scripts-into-packages（2026-09-30，刀 7：14 个入口脚本归位 —— `nn-training/` 顶层只剩 `conftest.py`）

**背景**：用户「nn-training 目录下还有几个 py 文件，把它们移动到合适的目录下」。按功能归位：六个训练/评估入口 →
`trainer/`；两个 worker 入口 → `remote/`；`rl_config_schema` → `worker/`；五个环境/运维脚本 → `tools/`；`conftest.py`……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-09-30-goalnn-entry-scripts-into-packages`

## §2026-10-01-goalnn-log-keeps-era-names（2026-10-01，`DECISIONS.md` 的旧路径口径：追加型日志**不就地改名**，头部落一条译注）

刀 7 两轮 `.md` 散文清理（§2026-09-30-goalnn-entry-scripts-into-packages 同日续 / 续二）后清点本文件：仍有
**决策**：全部**保持原文**，只在文件头（「正文在哪」段后）落一条**旧路径约定**译注（读者/agent 的进入点， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-log-keeps-era-names`

## §2026-10-01-goalnn-dashboard-gate-net-direct（2026-10-01，dashboard 门禁：测试进程出网直连 + 停课重试可注入 —— 全目录墙钟 11.4s → 1.8s，用例与断言逐条不变）

**症状**：dashboard `bun run test` 墙钟 11.4s（119 文件 / 1232 用例），而 per-file 时长总和 67.5s。
JUnit 逐用例（`--reporter=junit`）一看：几乎每个文件只有 1–3 个用例慢（1.5–4.0s），其余全在 0.01s……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-dashboard-gate-net-direct`

## §2026-10-01-goalnn-dashboard-tests-no-wallclock（2026-10-01，dashboard 测试不许有墙钟等待：等待由可观测事件触发，夹具主机不用 DNS）

**症状**：上一条（代理预载）修完后，逐用例时长里仍剩一批 1.5–4.0s 与大幅抖动——同一个
`buildStateView` 注入用例在 0.14–0.79s 之间来回跳。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-dashboard-tests-no-wallclock`

## §2026-10-01-goalnn-tests-mirror-package-tree（2026-10-01，nn-training 单测按源码包树镜像：`tests/<pkg>/` 子树，跨包守卫留根）

**背景**：六包重组（§51–§55・`plan/nn-training-module-reorg.plan.md`）明确留的后续刀——该 plan 写「`tests/` 目录本轮不动」。
用户口径（2026-10-01）：「nn-training 下的单元测试，也按照现在的项目源代码文件树结构，重新拆分组织」。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-01-goalnn-tests-mirror-package-tree`

## §2026-10-01-goalnn-gate-halt-platform-level（2026-10-01，门禁停机模式升为平台级单开关：`tmp/gate-halt.json` 意图 + 回执；课程级三写面退役）

**决策**（用户 2026-09-25 口径：「`gate-halt-mode` 应是平台级而非课程级，有人盯盘时切为提示，离开时切停机」）：
「门禁触发时停不停」从课程级散装旋钮升成**平台级单开关**——`tmp/gate-halt.json`（控制台写、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-01-goalnn-gate-halt-platform-level`

## §2026-10-01-goalnn-lease-orphan-reap（2026-10-01，孤儿租约早收：claim 后**零心跳**超 `ORPHAN_GRACE_SEC=180` 即回池；hub-only，协议面零改动）

**决策**：租约若**自 claim 起一次成功心跳都没有**且 `now - claimed_at > ORPHAN_GRACE_SEC` ⇒ 判为孤儿，
走**同一条** `_collect_expired_locked` 回收路径提前回池（不等满 `CLAIM_TTL_SEC=300`），并记账本事件 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-01-goalnn-lease-orphan-reap`

## §2026-10-02-goalnn-transfer-residual-yield（2026-10-02，让路税清算：条件让路 + 按传输累计上限 + 观测补面）

**决定**：让路条件化（**最老在途**控制请求 ≥ `YIELD_AFTER_SEC_DEFAULT=1.0s` 才让）+ 按传输累计 ≤5s；
库层缺省保持老语义（0.0 / None），由 worker 显式装配 `--bulk-yield {auto,always,never}` 且 `worker_loop`

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-02-goalnn-transfer-residual-yield`

## §2026-10-02-goalnn-eval-final-round-and-dropped（2026-10-02，收官轮 eval 缺失 + dropped 局静默丢失）

- **决定**：`finish_course(it, *, drain=True, block=True)` 收编收官 eval drain（serve 的 RL 传
  `block=False` 不冻其它课；BC 另一份实现按 kind 分派、不带 kwargs）；claim 集（`seen`，位置不动） ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-02-goalnn-eval-final-round-and-dropped`

## §2026-10-02-goalnn-course-pill-precision（2026-10-02，pill 精确化：hub 派发面接进 pill + 只读读面拿 iters 判收官）

- **决定**：hub 观测面纯加法（inflight 行 `claimed_ago`/`computing_ago`；顶层 `peeked_courses`
  = 60s 内被 `peek_jobs` 返回过候选的课程集；只被 `/admin/queue` 读，调度判据零引用）· 收官判据 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-02-goalnn-course-pill-precision`

## §2026-10-02-goalnn-worker-contribution（2026-10-02，并行 worker 贡献度：承接面归属 + 两个单位组永不合并 + 课程只作 breakdowns 轴）

- **决定**：① hub 承接面新增归属事件（**扩事件不新建账本**）：`accept_result` 成功写
  `job_result_accepted`（worker = 显式传入 ∪ 当前租约持有人，push 腿 `push:` 前缀），三条 409 路径 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-02-goalnn-worker-contribution`

## §2026-10-02-goalnn-e1-aim-open（2026-10-02，E1（aim 距离加权）开腿：剂量 κ 冻结 + 判据段 864001 + paired 止损显式对端；E2 终门现役复算 0.796；E3 复测门读数）

- **背景**：aim-dodge 8 列（dim 74）已落地（§2026-10-01-goalnn-aim-dodge-metrics）；E1–E4 的
  「开腿前必办」四项欠账在 2026-10-02 开课前补办。编排由用户拍板「**顺序波次、E1 先开三臂**」……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-02-goalnn-e1-aim-open`

## §2026-10-02-goalnn-e2-e3a-design（2026-10-02，E2/E3a 开腿设计冻结：f=7.5%/15% 两档 + MDE 实测 + 段B/C + 新 seed；E1 段A输入判无效）

- **背景**：E1 段A收官（864001 配对 900 局/臂，判据见 `h4-aim-c0.jsonc` 头注）：pooled dE[d|hit]
  k10 −0.037（反号）/ k25 +0.029（< kill 线 0.039）⇒ **机制无效**；Q 过（0.25/4.14 ≤ 5.99）；……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-02-goalnn-e2-e3a-design`

## §2026-10-02-goalnn-x20-advanced-design（2026-10-02，x20 kitchen-sink 腿：B 底包 + 新 5 项 human-gap 定价，单腿无对照）

- **背景**：用户直接指定（非 plan 推导）：基于 x20 最佳权重（it175），新 5 项全上，大样本、
  多 epoch、无 it 限制；只比最终杀敌数与过关率，不与历史课程单变量对比。底包 = B……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-02-goalnn-x20-advanced-design`

## §2026-10-02-goalnn-x20adv-h2-design（2026-10-02，x20-advanced hurt 重剂量对照臂：同起点 it175，wHurt 25.3→50.6）

- **背景**：it80 vs 人类复核 hurtR 反向（0.0693 vs 起点 0.0621）；用户指定加一档 hurt 重剂量对照。
- **决定**：新课 `x20-advanced-h2`，与主腿同包同预算、起点为主腿最优 it80（用户改起点；
  wHurt=**50.6**（f=15%，uniform gap pricing；it80 水平税 EV 3.51/局 ≈ 19% killEV，记账区上限内）。
  终判 @414000 段 800 局：hurtR 配对比较 + kills/通关率不劣于主腿终点（±3pp 诊断）。
- **落点**：`nn-training/curricula/x20-advanced-h2.jsonc`（validate_reward 零错误）·
  `nn-training/rl-config.json`（`x20-advanced-h2: {rollout_src: local}`）。

## §2026-10-03-goalnn-x20adv-plus-design（2026-10-03，二轮定价：新基线 a355，acc/hurt 加压，dist 维持，底包全留）

- **决定**：税用 §5.4 基线形、比值奖用 gap-value：**wAcc=13.6**（f=10%，acc 退化加压 rebalance）/
  **wDist=0.738 HOLD**（61% 走完，按 gap 重定价会给最后 0.7 格 3× 边际价，方向反，故维持 ~9% 份额）/ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-plus-design`

## §2026-10-03-goalnn-x20adv-plus-rebase（2026-10-03，plus 换基 it415：锚重算，w 全跟进）

- **决定**：同算法重算 ⇒ **wAcc=15.3 / wDist=0.738 HOLD / wHurt=77.5 / wEncl=8.60 /
  wCorner=25.0**（税 EV 15%/7.5%/7.5%；起点净 +1.44/局）；bc 指 it415 文件； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-plus-rebase`

## §2026-10-03-goalnn-x20legs-verdict（2026-10-03，三停腿收官：主腿/plus 成功加冕，h2 诊断成功归档）

- **决定（终判）**：① x20-advanced **成功**，加冕 **a580**（28.75%/10.723；亚军 it500）：
  it175（13.50%/7.619）→ 过关翻倍、杀敌 +41%；Edhit 走完 ~50%、encl/corner −37%/−30~45%、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20legs-verdict`

## §2026-10-03-goalnn-x20adv-3legs-design（2026-10-03，三加压腿 same-start q90 比收敛速度）

- **决定**：三腿与 plus 逐字同（起点 q90 + kickstart 锚 ref/init=0.1，预算 250 轮），各加压一轴：
  **acc 腿 wAcc 15.3→23.0**（×1.5；inflow 偏大已记档 level-vs-边际）/ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-3legs-design`

## §2026-10-04-goalnn-x20adv2-design（2026-10-04，下一阶段三腿：merge-max/dist-lastmile/acc-push，从 hu150 同起跑）

- **决定**：三腿同起点 hu150（32.50%/11.370，全场冠军）+ kickstart 锚 + paired 20261006 +
  预算 150 轮 + 早停：① **merge-max**（wAcc 23 + wHurt 110 + wEncl 25，三 escalations ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-04-goalnn-x20adv2-design`

## §2026-10-04-goalnn-x20adv-3legs-verdict（2026-10-04，三加压腿收官：峰全超 q90，250 轮过长，首达线设计失误）

- **决定（终判）**：三腿成功，加冕 **acc80 / hu150（全场最优） / en225**。
  收敛速度排名：acc 最快（it80见顶）> hurt 最高（it150）> encl 最慢（it225）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-04-goalnn-x20adv-3legs-verdict`

## §2026-10-03-goalnn-x20adv-encl-maxtax（2026-10-03，encl 腿顶格 25 + 胜率熔断）

- **决定**：`x20-adv-encl` wEncl 13.0 → **25.0**（f≈19%，税 EV 6.39/局，踩 20% 上限线；
  人类−q90 税差 ≈ 4.8/局）。配套硬熔断：日常胜率相对 q90 基线连续 2 点 < −3pp 即 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-encl-maxtax`

## §2026-10-03-goalnn-x20adv-plus-oldparams（2026-10-03，plus 旧参数复核：按 tick 率剥离 horizon，只动 wShot）

- **决定**：只动 **wShot 0.05→0.06**（NN 火多中少，加罚“少而准”，EV 动 0.44/局）；
  其余 6 个全留（wKill numeraire；wHit 加量只买量；wDmg×2 有 −2.8pp 实付代价且 hurt-70 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-03-goalnn-x20adv-plus-oldparams`

## §2026-10-02-goalnn-engine-pool-midround-pin（2026-10-02，引擎缓存上限不再钉死 5 + 轮内不许抽引擎 + `_node_rollout` 初值）

- **背景（事故）**：7 门课同时开课（h4-hurt-{c0,f75,f150} · h4-encl-{c0,f75,f150} · x20-advanced），
  而 `EnginePool` 数量上限是死值 `DEFAULT_CACHE_COURSES = 5` ⇒ 按 LRU 互逐。被逐的课下一拍……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-02-goalnn-engine-pool-midround-pin`

## §2026-10-02-nodes-lastcontrib-winner-must-have-completed-round（2026-10-02，节点「贡献 0 / 离线」误报的根治）

- **决定**：winner **只在真跑完过至少一轮的流**（`completedIt !== null`）里选；全部流都没跑完
  ⇒ `latestRound = null` + `lastContrib` 空表（消费方得 -1 = 「无池数据」），**不挑一个充数**。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-02-nodes-lastcontrib-winner-must-have-completed-round`

## §2026-10-02-goalnn-burn-rule-in-course-file（2026-10-02，止损块进课程文件：`kickstart_burn`/`paired_kill` 从 rl-config 迁入 `curricula/*.jsonc`，块不进 `corpus_fp`）

- **决定**：`biz/course_spec.py` 加 `KickstartBurnBlock`/`PairedKillBlock`（解析期强校验，脏值拒课）；
  5 个读函数改「课程块优先 + `legacy_*` 兼容回落」；`RESTART_ONLY_FIELDS` 加两键（不进 `HOT_FIELDS`）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-02-goalnn-burn-rule-in-course-file`

## §2026-10-02-goalnn-burn-rule-cut2（2026-10-02，止损块第二刀：删 rl-config 回落读面 + 六处迁移 + 存量清理）

- **决定**：① 删 `legacy_burn_mode/overrides`、`legacy_paired_kill_overrides/self_kill/enabled`
  六个回落读函数；`burn_mode/burn_overrides`/`paired_kill_*` 五函数去 `fallback` 参数（块缺席 = ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-02-goalnn-burn-rule-cut2`

## §2026-10-03-goalnn-auto-offline-handoff（2026-10-03，离线盘 claim 即接管：一拖一闸 + 派发状态落盘 + 三态意图表）

- **决定**：① 触发点在 **claim**（不是「盘上线」）：清单一律对未 pin 的在训课开放且**允许无包**；
  claim 无包 = 翻 mode + 控制台反向调用（`/api/autoOfflineHandoff`）导包 + 409；云机侧 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-goalnn-auto-offline-handoff`

## §2026-10-03-goalnn-auto-offline-handoff-t6（2026-10-03，T6 完成态链 + T3 面板钮 + T8 告警渲染落地）

- **决定**：
  ① **T6 判决在控制台、落账在 python**：`end_it_reached` 由云机自报（`plan_run._end_it_reached`： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-goalnn-auto-offline-handoff-t6`

## §2026-10-03-goalnn-switch-mode-drops-jobs（2026-10-03，切模式撤单落地：复用 `job_cancelled` 账本事件 + 只撤没被领的）

- **决定**：
  ① **落点重定位（T0a 的实质结论）**：原 plan §3 改点 2 钉 `remote/plan_run.py::run_plan_job`，但它 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-goalnn-switch-mode-drops-jobs`

## §2026-10-03-goalnn-gate-parallelism-physical-cores（2026-10-03，门禁/本地 worker 池并行度改按**物理核数**：新增 `physical_cores()` 第二口径）

- **决定**：
  ① **两个口径按消费域分工**（每一域仍只有一个答案）：`effective_cores()` 留作**训练运行时** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-03-goalnn-gate-parallelism-physical-cores`

## §2026-10-03-offline-pack-truncation-and-ref-blob（2026-10-03，用户报障×2：半截任务包被当成功 / 离线包缺 kickstart ref 字节）

- **决定**：① 两处孪生 `_read_body`（`remote/tailscale_boot.py`、`remote/http.py`）在 EOF 处
  比对「已收 vs Content-Length」，不足即按传输失败抛（引导期 `BootBodyError` 进同一次 fetch 的 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-offline-pack-truncation-and-ref-blob`

## §2026-10-03-goalnn-offline-seize（2026-10-03，用户裁决：pin 不再拦离线盘——离线优先、没有就抢第一个在训在线课）

- **决定**：
  ① **pin 语义坍缩**：`auto_eligible` 去掉 pin 项（在表 ∧ 开课标记在）——pin 仍落盘/重启不丢， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-03-goalnn-offline-seize`

## §2026-10-03-goalnn-cpu-slots-reserve2（2026-10-03，TPU 核数口径：Colab 配额=4 核不是 24；预留 4→2 统一；log 报三源出处）

- **决定**：① **用户校准：预留 4 → 2**，统一改 `max(cores−2, cores×0.8)`（`CPU_RESERVE=2`；
  Kaggle 96→94、Colab 4→3、16→14、8→6；`rollout.cloudflared.ipynb` 的内联回退同步）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/tpu-perf.md` §9「决策正文归档」· 锚 `### §2026-10-03-goalnn-cpu-slots-reserve2`

## §2026-10-04-goalnn-sympy-printing-guard（2026-10-04，Colab TPU：`import torch_xla` 抛

- **决定**：`worker/ppo/np_core.py` 新增 `_ensure_sympy_printing()`——**只在缺失时**补绑
  `sympy.printing`（幂等；sympy 不存在静默放过；绝不抛），并在**本进程第一次** `import ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/tpu-perf.md` §9「决策正文归档」· 锚 `### §2026-10-04-goalnn-sympy-printing-guard`

## §2026-10-04-goalnn-opt-blob-must-ride-the-pack（2026-10-04，自主段起点动量必须种子进

- **决定**：① 新增 `plan_handoff._seed_opt_blob_cache`（与 `_seed_ref_blob_cache` 同规）：
  按 产物 `it-N/opt.tar` → 包根 `opt.tar` → job `blob.opt` 找字节，种进 `blob_cache/<sha>`，

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-04-goalnn-opt-blob-must-ride-the-pack`

## §2026-10-04-goalnn-pack-freshness-on-handoff（2026-10-04，抢占在训在线课必须重导包：

- **决定**（判据 = 「包是否仍代表当前起点与当前代码」，全在控制台侧判）：
  ① 规则表 ⑥ 拆成 ⑥/⑥'：包的 mtime **不早于**「活动权重（`tmp/<课>/weights.json`）mtime」 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-04-goalnn-pack-freshness-on-handoff`

## §2026-10-03-goalnn-dashboard-alert-dock-global（2026-10-03，告警坞全局化：收官横幅按课成列 + 七类条目全可关可复制）

- **决定**：收官横幅读面扩为 `loopCompletes: Record<course, LoopComplete>`，聚合落 `state-view.ts`，判活用
  共享 `trainingLoop` 的全局存活事实，课程清单 = 已开课 ∪ 查看课，条目按课成列、标题带课名、按课名排序； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-03-goalnn-dashboard-alert-dock-global`

## §2026-10-03-goalnn-request-path-zero-aggregate（2026-10-03，控制台重载 ~10s + 常驻 1GB：请求路径零聚合 + 聚合增量入账）

- **决定**：三条一起做（缺一条只是把症状搬位置）——
  **R1** 贡献度缩略挂 `FleetProbes.contributionBrief`（`computeFleetProbes` 后台顺手产出；……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-03-goalnn-request-path-zero-aggregate`

## §2026-10-04-goalnn-cpu-slots-small-full（2026-10-04，并发口径小机器校准：CPU ≤ 4 核全给）

- **决定**：`common/platform_utils.cpu_worker_slots()`（rollout 与 eval 的**唯一口径**）新增
  `CPU_ALL_SLOTS_CORES = 4`：`n ≤ 4 ⇒ n`（全给）；否则照旧 `max(n − CPU_RESERVE, floor(n×0.8))`。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-04-goalnn-cpu-slots-small-full`

## §2026-10-04-goalnn-worker-readable-name（2026-10-04，worker 身份 = 环境+连接方式；环境判据 = 平台包可导入性；不做重名消解但留观测守卫）

- **决定**：① 判据唯一实现 `common/env_probe.py`（纯函数）：env = `kaggle_secrets` /
  `google.colab.userdata` 可导入性（顺序 kaggle→colab→aistudio→local）或 `/home/aistudio`； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-04-goalnn-worker-readable-name`

## §2026-10-04-goalnn-offline-eval-backfill（2026-10-04，离线课：云机没回传 eval 时 hub 端用 LAN 集群补评）

- **决定**：新 `dashboard/src/server/offline-eval-backfill.ts`（纯选择 + 增量账本索引 + 编排）；
  +3 宽限 = 该 run `max(its) >= N+3`；收官 = `end_it_reached` 时最后一个评估点 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-04-goalnn-offline-eval-backfill`

## §2026-10-04-goalnn-sampler-workers-physical-cores（2026-10-04，sampler `--workers` 缺省改**物理核**：池常驻内存封顶减半）

- **决定**：`physicalCores()`（`tools/lib/cores.ts`：min(真物理核, cgroup 配额, 亲和掩码)，Windows 走
  pwsh CIM 记忆化——与 §2026-10-03 门禁/本地池口径同源）为 `sampler-agent` 的**唯一核数口径**： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-04-goalnn-sampler-workers-physical-cores`

## §2026-10-04-goalnn-sampler-idle-reap-and-task-timeout-fix（2026-10-04，sampler 池：空闲回收 + 600s 超时定时器误杀修复）

- **决定**：
  ① **空闲回收**：`PERSIST_IDLE_MS = 30min`（长阈值；`--pool-idle-ms 0` 关）× `PERSIST_POOL_FLOOR = 4` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-04-goalnn-sampler-idle-reap-and-task-timeout-fix`

## §2026-10-05-evalboard-off（2026-10-05，evalBoard 缺省停用：四处接线一门开关 + 账本冻结）

- **决定**：功能开关 `BCITY_EVALBOARD`（`src/core/feature-flags.ts::evalboardEnabled`，**缺省 `0`**，
  `=1` 开）一处控四处接线（首页面板 / `/api/evalboard` / 30s ladder ticker / `/eval` 页）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-05-evalboard-off`

## §2026-10-05-course-startup-recover（2026-10-05，课程配置错被整课跳过：判据变即自动重试 + 只读 `blocked` 判据）

- **背景（用户报障）**：`x20-adv3-open-r2` 的课程文件自相矛盾（`kickstart_init>0` ∧ `kickstart_ref=false`）
  ⇒ 启动期 SystemExit、整课被共享 trainer 跳过；人改好文件 + 控制台「停→开」后日志零变化——唯一出路……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-05-course-startup-recover`

## §2026-10-05-goalnn-gate-fail-fast（2026-10-05，nn 门禁 fail-fast：任一腿先红即停其余腿）

- **决定**：门禁 v3.20 起按启动顺序（ruff → mypy → pytest）收账，任一腿非零即 `stop_tool`
  掉还没收账的腿再 wait 回收；启动仍三路并行（happy path 墙钟不变）。杀法按「选中的 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-05-goalnn-gate-fail-fast`

## §2026-10-05-goalnn-offline-online-status-switch（2026-10-05，用户报障×2：手动切在线不稳定 / 云机掉线后课程永久停摆）

- **背景**：报障一「切成在线不稳定」= pin 语义在 §2026-10-03-goalnn-offline-seize 坍缩后「我要在线」没有表达（切了被抢回）+ 撤单即时闸不认账本复活 + 本机腿把离线等待当收官；
  报障二「课程永久停摆」= 死租约冻结全池 / 无包腿烧满 `give_up` 后被新盘 skip / 导包窗口无主也不释放。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-05-goalnn-offline-online-status-switch`

## §2026-10-05-policy-spatial-head-s0（2026-10-05，POLICY_EXTRA 9 维 + C=8 空间塔：schema v4 落地与 S0′ 探针判决）

- **背景**：plan `plan/policy-spatial-head.plan.md`（v6 冻结）要求给走位/开火头加"看得见位置"的输入：
  腿 A = 独立 9 维方向直方图（`POLICY_EXTRA`），腿 B = C=8 空间塔（1×1 64→8 + ReLU + 4×4 分区均值 + FC 128→112）。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-05-policy-spatial-head-s0`

## §2026-10-06-goalnn-rollout-io-ceiling（2026-10-06，云机离线 rollout 第三次「整轮卡死十几分钟」：一局有了自己的墙钟上界，不可取消的阻塞不再把人质扣到整轮）

- **决定**：① 新增 `common.platform_utils.call_bounded(fn, timeout)`：真活跑在一条 **daemon** 线程里，
  调用线程只在预算内等，超界返回 `(False, None)` 且**绝不 join**（`concurrent.futures` 的线程不是 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-06-goalnn-rollout-io-ceiling`

## §2026-10-06-goalnn-round-end-scan-ceiling（2026-10-06，轮末扫盘也有上界：主线程上的全量盘 IO 不再让整轮对日志静默）

- **决定**：① 新增 `common/game_watch.SCAN_CEILING_SEC = 60.0`（**单列的第三个数**：扫盘没有子进程/
  重试/写盘 ⇒ 不能沿 `game_ceiling_sec`，那个含尝试次数与回收预算，会大到让挂死的扫盘也过关； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-06-goalnn-round-end-scan-ceiling`

## §2026-10-06-goalnn-pull-replica-any-unfinished（2026-10-06，用户报障：新 worker 上线「领不到」，要重启 trainer 才领得到；用户口径：只要没回传结果都算没完成，都能发出去竞速）

**决定**：未完成就发得出去 —— `claimable_job_ids`（∧无活租约）与 `inflight_job_ids(not_held_by=…)`（∧有
活租约）成为同一份「未完成」判据的两个视图（`_unfinished_pending` 是唯一筛子）；`peek` 每课程先看池 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-06-goalnn-pull-replica-any-unfinished`

## §2026-10-06-goalnn-cloud-worker-log-diet（2026-10-06，用户指令「删除云机 worker 刷屏 log」）

**决定**：这六行**删除**（不降频、不加开关），每 job 的观测出口收敛到既有的一行 wire 账
（`wait=…/yield=…` 调度账 + 各段字节/秒 + `phases in/out/ppo/other`）；**异常与真异常路径各自仍然响亮** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-06-goalnn-cloud-worker-log-diet`

## §2026-10-06-goalnn-test-console-isolation（2026-10-06，用户问「是不是你跑门禁触发的」⇒ 是；用户裁决「修」）

**决定**：测试**永不**指向在跑的控制台——`tests/conftest.py` 与 `e2e/conftest.py` 各挂一个 autouse fixture，
把测试期 `BCITY_CONSOLE_URL` 钉到 `http://127.0.0.1:9`（死端口：连接当场被拒 ⇒ 漏网也只是「控制台不可达」 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-06-goalnn-test-console-isolation`

## §2026-10-06-goalnn-test-state-isolation（2026-10-06，用户令「扫其它测试 → 生产泄漏」）

**决定**：测试**永不**写生产状态——`tests/conftest.py` 立唯一的 `PRODUCTION_STATE_PINS`
（`NN_GATE_HALT` / `NN_GATE_HALT_APPLIED` / `NN_LOOP_CONTROL` / `NN_LOOP_CONTROL_APPLIED` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-06-goalnn-test-state-isolation`

## §2026-10-06-goalnn-replay-export-eval-round-picker（2026-10-06，用户令「导出 replay 支持任意 eval 轮」）

- **决定**：① 选择器数据源 = 轮级账本（`iters.ts::readEvalRoundOptions`，summary-only 单趟，
  谓词与 `readEvalGames` 同源，`reusedWver` 单独标注）；② **归因不变量：`POST evalReplays` 受理即 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-06-goalnn-replay-export-eval-round-picker`

## §2026-10-06-psh-leg-a-verdict（2026-10-06，腿 A 终判：挂起 → "看见了但没转化"，K→5 立项 + 腿 B 可启动）

- **背景**：plan `plan/policy-spatial-head.plan.md` §5 口径，A-it150 对 A0-it150（`x21-psh-a.it150.20261006-093718.json` /
  `x21-psh-a0.it150.20261006-053532.json`），N=1600（800 @415000 + 800 @417000，同命令先后跑，配对 800/800 全中，……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-leg-a-verdict`

## §2026-10-06-psh-leg-b-start（2026-10-06，腿 B/B0 开课：起点机制与 B0 直接复制的不对称）

- **背景**：腿 A 终判挂起→"看见了但没转化"，满足 §5.5"挂起 + 方向改善 ⇒ 腿 B 可启动"。课程
  `x21-psh-b/b0.jsonc`（paired_rotate_seed=20261012 全新流，已立）经控制台同源 API `openCourse` 开课，……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-leg-b-start`

## §2026-10-06-psh-leg-b-burn-abort（2026-10-06，B 在 it16 被配对熔断误杀：冷爬坡判成烧干，切 baseline 重开）

- **决定**：B/B0 课程文件加 `kickstart_burn: {mode: "baseline"}`（B：floor=own-it0−5pp，对零起点 ≡ 永不触发，
  可证明；B0：只防真塌〈连 3 点 < 30.2%〉，且防"后期 B 反超杀死对照组"。对照臂永不自杀，x20-clutch-null 前例）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-leg-b-burn-abort`

## §2026-10-06-psh-leg-b-verdict（2026-10-06，腿 B 终判：关闭；梯度到达，真·持平；不切换）

- **背景**：plan §5 口径，B-it150 对 B0-it150（`x21-psh-b.it150.20261006-155320.json` /
  `x21-psh-b0.it150.20261006-172154.json`），N=1600（800 @415000 + 800 @417000，配对 800/800 全中，……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-leg-b-verdict`

## §2026-10-07-psh-poolouter（2026-10-07，池外 800 局确认 + 两课结课报告回写课程文件）

- **决定**：杀敌 D=**-0.11** CI [-0.80,+0.58]（含 0）+ 通关 D=**-1.12pp** CI [-5.51,+3.26]（破线）
  + acc≈0 ⇒ 与 it150 终判**同形关闭**（单段 MDE@800：0.98/6.4pp，口径一致）。关闭 verdict 维持， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-07-psh-poolouter`

## §2026-10-07-peak-crown（2026-10-07，四臂峰值同题排名：B0-it290 加冕，不改任何 verdict）

- **决定**：加冕 B0-it290（`x21-psh-b0.it290.20261007-004553.json`）为全纪元最强权重。
  三条如实声明：① 峰值自选 ⇒ 选择偏差（真优势小于测得值）；② B0 累计 ~290 轮 ≈ A 的两倍预算， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-07-peak-crown`

## §2026-10-07-b0-vs-hu150（2026-10-07，B0-it290 配对挑战 hu150：双显著胜，加冕全场最强权重）

- **决定**：B0−hu150 杀敌 D=**+0.63** CI [+0.13,+1.12]、通关 D=**+3.75pp** CI [+0.48,+7.02]——**双显著**。
  加冕 B0-it290 为全场最强权重（终判段口径；hu150 同段实测 31.25%/11.09，低于其账本口径 32.50%/11.370， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-07-b0-vs-hu150`

## §2026-10-07-k5-filing（2026-10-07，K→5 立案：起点 B0-it290 + K5/K10 同流配对；只写计划）

- **决定**：立 `plan/k5-rhythm.plan.md`（待批准）：两臂同起点（B0-it290 sha 见证）/同预算 150/同新流 20261013/
  同守卫（baseline+margin10），唯一差别 = K；判决沿用 §5 表；K 机制 audit（旋钮位置/半格对齐/obs 语义） ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-07-k5-filing`

## §2026-10-06-psh-extend300（2026-10-06，两臂续跑到 it300：斜率归零，"半山腰"假设被证伪）

- **决定**：延伸段证伪"欠训练" fork（+150 轮零斜率）⇒ **关闭 verdict 维持，不再加预算**；it300 配对终判
  不跑（30 个日常点一致，翻盘概率≈0，省配额）。塔分支结案：C=8 在 150–300 轮预算下无转化。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-06-psh-extend300`

## §2026-10-07-k5-decision-k-channel（2026-10-07，K→5 案 v2 评审修订 + 决策周期 K 通道全链落地 + 两课备好）

- **背景**：`plan/k5-rhythm.plan.md` 评审（2026-10-07）发现 2 P0 + 3 P1：守卫口径三处不一（表 margin10 /
  注释 margin_pp 100 / filing"margin10"）；评估 K 未预注册且全链无通道（K5 臂会被 OOD 按 10 拍评估）；……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/training-stack.md` §25「决策正文归档」· 锚 `### §2026-10-07-k5-decision-k-channel`

## §2026-10-06-goalnn-evala-serial-queue（2026-10-06，用户报障：「evalA 在某 it 执行过后，其它 it 再点按键无反应，需要刷新页面」）

- **决定**：排队住唯一启动点 `server/eval-a-run.ts`：忙时返回「已排队（前面 N 个）」、子进程退出自动
  接棒；`eval:A` 从第一单持有到**队列排空**（补评/导入侧看到的「忙」语义不变）；同 (课, iter, baseline) ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-06-goalnn-evala-serial-queue`

## §2026-10-06-goalnn-offline-leg-sampling-exclusion（2026-10-06，用户指令：offline worker 完成的轮算节点 PPO 贡献、不算 rollout/eval 贡献）

- **决定**：腿 = 课程账本里 `offline_artifact` 事件带的 it 集合（`pool-history.readOfflineLegs`）；
  剔除发生在**计数入口** `ingestLine`（日桶/课维度只有「天」粒度，事后扣减无 it 可依），行在……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-06-goalnn-offline-leg-sampling-exclusion`

## §2026-10-06-goalnn-eval-cloud-time-utc（2026-10-06，用户报障：offline 回传的 eval 时间是 UTC）

- **决定**：**读侧**转换（历史数据必须修正）：`server/iters.ts` 的 `readEvalSummaries` /
  `readEvalGames` 把含 cloud 的 `time` 按 UTC 解析后按本机时区写回**同格式**（本条落账时该函数名 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-06-goalnn-eval-cloud-time-utc`

## §2026-10-07-goalnn-offline-deliver-isolation（2026-10-07，补传腿忙等修复 + 独立进程：落地 plan/offline-deliver-isolation 的 P0+P1）

- **背景**：云机 `x21-psh-b` 530 起 PPO 单步 `0.186 → 5.26 → 7.40 s/step`（墙钟 ≈**40×** 于
  编译+追踪+拷贝之和 ⇒ 训练线程被抢 CPU/GIL，隧道恢复即自愈）。诊断 `plan/tpu-cpu-silent-downgrade.plan.md` §9/§10；……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-07-goalnn-offline-deliver-isolation`

## §2026-10-07-rollout-stage-balance（2026-10-07，分关采样平衡：est 上界初批 + 有界多批补差；不改身份，事件可见化）

- **背景**：动态采集的**全离线腿**（`kind=run`）一轮只跑一批，局数按**全局** est 反解 ⇒ 每关同一个局数，
  而达标线是**分关** quota。x21-psh-b it168 真机实测：缺 4405 样本、同时丢 3333 样本（关间 samples/局 差 1.75×）。……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-07-rollout-stage-balance`

## §2026-10-07-goalnn-serve-pool-readiness（2026-10-07，长驻池「假就绪」+ 重试路径裸文件 IO：真就绪判据 + 有界清理；清理超界改判「交整轮」）

- **背景**：Kaggle 离线课 `x21-psh-b` it253（job `031e17e61777d09c`）一轮 **355s**（正常 32s）：
  `94/94 就绪` 是假满（真含义 = 94 个进程没退出）⇒ 假就绪 worker 被当暖的发出去 ⇒ 5s/20s 超时回退一次性 ⇒……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-07-goalnn-serve-pool-readiness`

## §2026-10-07-goalnn-offline-graceful-exit（2026-10-07，自主 worker 收工即停：终态短窗口 + 保活收工即停）

- **决定**：① 新增 `TERMINAL_STATES={completed,not_offline}` + 纯函数 `all_terminal(rows, served)`；
  **判据并上「本会话已跑过这份包（同 sha）」**——现场那门是 `claimable` 行、只被 `_eligible` 的 served 过滤 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-07-goalnn-offline-graceful-exit`

## §2026-10-07-pool-history-large-stream-rescan（2026-10-07，池历史大流：文件大只决定「怎么读」，不决定「要不要重读」）

- **决定**：改重建判据 —— 拆出 `grewOrChanged`，大流条件改成 `src.size > LARGE_META_BYTES && grewOrChanged`
  （阈值分支保留 ⇒ truncated 读法与口径一字不动）。真代价 = 「只要本循环被进入（任一流变动 / 首见 / 腿指纹 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-07-pool-history-large-stream-rescan`

## §2026-10-07-logtail-bounded-read（2026-10-07，`logTail` 改尾部窗口读：空置 WS 280↔856MB 的真凶）

- **决定**：两条都改「有界读」——`logTail` 尾部窗口（`LOG_TAIL_BYTES = 256KB`，文件 ≤ 窗口时与旧实现
  逐字相同）；`totalLines` 分块计数（`countLines`，内存恒定 1MiB，与旧全文件版逐字节等价）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-07-logtail-bounded-read`

## §2026-10-07-offline-busy-gate-anchor（2026-10-07，离线「一拖一」闸两次自锁：锚点换 episode 起点 + 只算池内的课）

- **决定**：三处，全是换判据、不加机制。① `_busy_locked` 的交接窗口锚从 `claimed_at` 换成**本次
  交接的起点 `flipped_at`**（`claimed_at` 只作缺键兜底）——`flipped_at` 只在首翻时写、重复 claim ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-07-offline-busy-gate-anchor`

## §2026-10-08-crown-confirm（2026-10-08，N=3200 加冕赛：KR10-it150 双显著胜 B0-it290，王位易主）

- **决定**：杀敌 D=**+0.48** CI [+0.14,+0.82]、通关 D=**+2.53pp** CI [+0.20,+4.87]——**双显著**，
  四段方向一致（+0.36/+0.54/+0.45/+0.56）。**王位易主**：KR10-it150 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-08-crown-confirm`

## §2026-10-08-kr-verdict（2026-10-08，K-retune 终判：直关且死得更透；K5 训练侧永久关闭）

- **决定**：杀敌 D=**-3.87** CI [-4.34,-3.40]（上限 < 0 ⇒ **直关**，比旧 K5 腿的 -1.99 死得更透）；
  通关 D=**-20.75pp**（破安全门）；acc D=**+0.029** 全正（瞄准又好了，第三次"看得见打不着"）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-08-kr-verdict`

## §2026-10-08-k5-verdict（2026-10-08，K5 终判：直关；稀释假说死，关节奏分支）

- **决定**：杀敌 D=**-1.99** CI [-2.47,-1.50]（上限 < 0 ⇒ **直关**，显著变差覆盖一切）；通关 D=**-10.1pp**
  CI [-13.1,-7.1]（破安全门）；acc D=**+0.015** 全正（瞄准更好，杀敌照跌）⇒ **稀释假说死**： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-08-k5-verdict`

## §2026-10-08-diag-filing（2026-10-08，诊断重仿案立案：被包围/火线/背刺/远距四维；只写计划）

- **背景**：K10@K5 深拆要四维，终判行无逐 tick 记录、export 链无 decision-k。立
  `plan/diag-lane-instrument.plan.md`（待批准）：同权重同种子重仿 + 只读探针；口径冻结在先
  （enclR/laneR/backR/Edhit 定义 + 分母 + K wire）；先量速定 N（800 或预注册 200 子集）；
  K10 重仿与终判行逐种子全字段对账，不一致即停；探索性，永不进 verdict。
- **落点**：计划 → `plan/diag-lane-instrument.plan.md`。
## §2026-10-08-diag-verdict（2026-10-08，诊断重仿结论：K5 执行赢在"不被包+侧面磨"）

- **决定**：被包围 −2.0pp ✅、火线 −1.6pp ✅、背刺占比 −3.2pp ✅、侧击 +3.7pp ✅、远距 −0.05 含 0、
  danger/near4 持平、自洽锚杀敌 +1.09 ✅。画像：细执行不靠刺杀/射程，靠"少被包 + 侧面磨 + ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-08-diag-verdict`

## §2026-10-08-goalnn-dispatch-model（2026-10-08，派发模型重构收口：课程无模式 + 接管（hold）唯一真源 + BC 独占；plan/worker-type-dispatch-model M1–M6）

- **背景**：旧模型把「在线/离线」当**课程的一等概念**（`_modes` + pin + `authority_of` 五档 +
  `parked` 派发闸 + 离线租约），横跨 hub / 控制台 / trainer / 云机四层，三个后果已经出过现场：……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-08-goalnn-dispatch-model`

## §2026-10-08-goalnn-rollout-local-scratch（2026-10-08，rollout/eval 热路径 IO 落节点本地盘：共用一套 scratch，回落即今天的行为；立案 plan/rollout-local-scratch）

- **决定**：新 `common/scratch.py`（L0 叶子层）提供落点链（`NN_ROLLOUT_SCRATCH` > `/dev/shm` > `/tmp` >
  **`None`**；容量**与速度**两闸）+ 作业私有布局（`r/`、`eval/`）+ 内容寻址的共用 TS 家……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/runtime-opt.md` §13「决策正文归档」· 锚 `### §2026-10-08-goalnn-rollout-local-scratch`

## §2026-10-08-goalnn-eval-baseline-undispatched（2026-10-08，it0 基线 eval「从未派出」缺口：收工自报原因 + 缺口三分 + 基线自带本机门 + 大缺口重派；立案 plan/eval-baseline-undispatched）

- **决定**：① 收工自报 `close_reason ∈ settled/window/workers-gone`（单一实现 `worker/eval_yield.eval_close_reason`；
  `workers-gone` 只在**窗口还有余量**时成立 —— 否则窗口到期永远报不出来）+ `left_pending/inflight/spawned/窗口余`； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-08-goalnn-eval-baseline-undispatched`

## §2026-10-08-goalnn-eval-task-key-coursefp（2026-10-08，bugfix：eval 腿任务缺课程血缘 ⇒ 兄弟课程评估局在节点 resultCache 互串）

- **决定**：`EvalDispatcher.run` 计算一次 `course_fp_for_args(args)` 并透传 `fetch_task(..., course_fp=...)`；
  summary 复用同一读数（消除两处各算）。非课程路径 `course_fp=""` ⇒ 键与历史逐字节一致。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-08-goalnn-eval-task-key-coursefp`

## §2026-10-09-goalnn-x23cm-verdict（2026-10-09，x23 commitment 三整形臂终判：it150 全否决）

- **决定**：三整形臂全否决，归档不续跑；cm0 对照线继续 production 续跑。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-09-goalnn-x23cm-verdict`

## §2026-10-09-goalnn-kr10-dethrones-hu150（2026-10-09，KR10-it150 正面对决掀翻 hu150，易主）

- **决定**：冠军易主，**KR10-it150 为现役最优**；后续 production/终判基线改挂 KR10-it150，
  引用 hu150 为基线的结论一律重读（§79 的 q90 系内排名不受影响）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-09-goalnn-kr10-dethrones-hu150`

## §2026-10-10-goalnn-fore-crown（2026-10-10，fore-it335 拼池加冕 + stack 确认）

- **决定**：**fore-it335 加冕现役最优**；stack-it125 确认有效（叠加成立）；后继基线改挂
  fore-it335；x24-face/move/fore 单腿 verdicts 另行结算（本条只管加冕）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/experiments.md` §33「决策正文归档」· 锚 `### §2026-10-10-goalnn-fore-crown`

## §2026-10-09-goalnn-compare-trends-modal（2026-10-09，首页趋势图加「比较课程」：跨课程端点独立于 /api/state）

- **决定**：新增 `GET /api/compareTrends`（`?courses=a,b&metric=&source=&from=&to=`；≤8 门、逐个
  `sanitizeViewCourse` 剔除非法/不存在、剔空 ⇒ 400 而不静默回退）；课程级缓存 TTL 10s + 账本 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-09-goalnn-compare-trends-modal`

## §2026-10-09-goalnn-ppo-live-rows（2026-10-09，首页 PPO 区加「每台一行」：谁在算哪一轮 / 下一轮下好了没）

- **决定**：worker 每轮末经 `POST /admin/worker-prefetch` **只报 jid**（形状未变不发 / 失败静默退避 12 轮 /
  绝不重试）；hub 解析 `course`/`it` 并在 `/admin/queue` 的 `worker_prefetch` 出读面（TTL 60s；**纯观测**， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-09-goalnn-ppo-live-rows`

## §2026-10-09-goalnn-course-workers-removal（2026-10-09，`workers` 从课程文件退场：机器级键；kind=iter 的 wire 补一档「0 = 节点自定」）

- **决定**：`workers` 判定为**机器级**键 ⇒ 课程侧读面删除（`flat_overrides` / `RESTART_ONLY_FIELDS` /
  ladder 模板），字段与存量键永久保留，死键由**开课侧**一行告警点名；本机配额与离线段口径不动。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-09-goalnn-course-workers-removal`

## §2026-10-09-goalnn-self-node-disk-alert（2026-10-09，self 节点低盘从「静默」变「响亮」：预警档 4096 + 告警坞条目 + 徽标；**拒收地板 2048 冻结**）

- **决定**：agent 单源两层阈值——**预警 `DISK_WARN_MB=4096`（纯观测）** + **拒收地板
  `DISK_FLOOR_MB=2048`（冻结；准入判据抽 `decideTaskAdmission()` 纯函数）**，回差 512；五字段 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-09-goalnn-self-node-disk-alert`

## §2026-10-09-goalnn-test-debt-cleanup（2026-10-09，nn-training 测试债清理：**口径先于数字** —— 两个可执行计数口径 + 三条防回流护栏；T1 删除面收窄 6 条、T2 拆 a/b、T5 撤掉「≥40%」）

- **决定**：① **口径代码化**：`tests/helpers/text_asserts.py`（放在既有 `source_scan.py` 旁，不另开第三套读盘逻辑）
  —— 口径 A（形态：`"字面量" in <文本>`，含 capsys）与口径 B（+「被读的必须是 `.py` 源码」门）；实测 A=1488、B=1084 条； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-09-goalnn-test-debt-cleanup`

## §2026-10-10-goalnn-pytest-top16-round2（2026-10-10，pytest 最慢 16 条第二轮：控制面第三条 HTTP 缝 + 三处全仓扫描的口径内提速；全量 sum_min −16%、墙钟中位 −24%）

- **决定**：① 假 hub 的桩补齐**第三条**控制面缝（`report_prefetch`）：`tests/helpers/hub_seams.stub_round_http` + 4 处显式补桩
  —— 此后**新加控制面请求必须同步进该助手**（否则一批用例各白等 ~1.4s/轮；本轮这类共 14 条 / ~22s）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-10-goalnn-pytest-top16-round2`

## §2026-10-10-goalnn-deliverproc-final-flake（2026-10-10，补传腿重启的满载 flake：判据对齐「可判定」的证据 + 账本优先）

- **决定**：① 慢层用例的**杀点同步到磁盘证据**（账本记全 3 轮 + `result_done`；不是 hub 那边的计数），
  此后断言「result POST 恰好 1 次 · `final` 行只有 1 行 · 已投轮次各恰好 1 次」，重启后再摆第 4 轮 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-deliverproc-final-flake`

## §2026-10-10-goalnn-rollout-node-auto-register（2026-10-10，采样节点自注册：hub 成为 `rl-config.json` 第二写者）

- **决定**：`POST /admin/nodes/register|unregister` + `GET /admin/nodes`（Bearer = `HUB_TOKEN`）由 hub
  upsert `rl-config.json` 的 `nodes[]`：只动这一项 + 原子替换 + **无变化不写盘**；新条目打 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-rollout-node-auto-register`

## §2026-10-10-goalnn-metrics-v11（2026-10-10，metrics v11：热线族 **2 列** idx74–75 / dim 74→76 / 事件加 `bulletOwnerId`；plan/metrics-v11-hotlane.plan.md）

- **决定（列布局）**：尾部追加 2 列 —— `nearSqSum`(74)（每拍对每个**存活已激活**敌车按切比雪夫
  格距 d 累加 `(3−d)²`，**有效半径 2 格**）· `postHitLaneTicks`(75)（被击中后仍留在**该源火线轴**上 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-10-goalnn-metrics-v11`

## §2026-10-10-goalnn-v11-criterion1-split（2026-10-10，v11 两列判据 1 分流：nearSq 可定价，hotLane 维持诊断）

- **决定**：nearSqSum 判据 1 通过，定价解禁（首用 `x24-stack4`，替换 near4，B6 条件替换执行）；
  postHitLaneTicks 维持诊断列，不定价（v11 commit 限制维持）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-10-goalnn-v11-criterion1-split`

## §2026-10-10-goalnn-cluster-code-snapshot（2026-10-10，`code.zip` 打包时机从「每课首次 publish」上移到「集群会话启动」，锚 = 首个声明进程的存活期；用户指令）

- **决定**：新增 `remote/code_snapshot.py`；快照落 `<repo>/tmp/.code-snapshot/`，**内容寻址**
  （`code.<sha12>.zip` + 最后原子替换的 `snapshot.json`）；启动期 `ensure`（`hub/boot.py` · ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-cluster-code-snapshot`

## §2026-10-10-goalnn-cluster-code-snapshot-ts（2026-10-10，`ts_code.zip` 并入集群代码快照——P1，承接 §2026-10-10-goalnn-cluster-code-snapshot）

- **决定**：快照**一次打包产出两件**（`code.<sha12>.zip` + `ts_code.<sha12>.zip`，元数据加 `ts_zip` /
  `ts_sha256` / `ts_bytes`）；新消费口 `published_ts_code_zip()` 与 `published_code_zip()` **逐字同规** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-cluster-code-snapshot-ts`

## §2026-10-10-goalnn-s4-crown（2026-10-10，s4-it180 三腿拼池居首，加冕）

- **决定**：**s4-it180 加冕现役最优**；后继基线改挂；人机对照见三腿合并大表
  `tmp/merged-gap-trio-best-v2.txt`（s4：会停 49.2 + 用盾一半；撞墙/脱离/贴脸三硬骨头未动）。 ……

—— 全文 → `docs/nn/experiments.md` §84

## §2026-10-10-goalnn-cluster-code-snapshot-ui-hatch（2026-10-10，快照的可见性（组件卡 sha 徽章）与逃生开关——plan 的 P2 + §8.3；承接 §2026-10-10-goalnn-cluster-code-snapshot）

- **背景**：P0/P1 把 `code.zip` / `ts_code.zip` 的打包时机上移到**会话启动**（重启才换）之后，
  「本会话跑的到底是哪份代码」在控制台上**不可见**——而它正是这套冻结语义下最该被看见的一件事……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-10-goalnn-cluster-code-snapshot-ui-hatch`

## §2026-10-10-goalnn-v11-hotlane-unblock（2026-10-10，postHitLaneTicks 定价解禁：判据 1 分流通过）

- **决定**：`postHitLaneTicks` 定价解禁，首用 `x24-stack5`（wHotLane=800，−3.6/局；
  稀有行为单价大，中位局不交钱）。`nearSqSum` 解禁已在 §2026-10-10-goalnn-v11-criterion1-split。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-10-goalnn-v11-hotlane-unblock`

## §2026-10-11-goalnn-aistudio-transfer-hardening（2026-10-11，用户 2026-10-09 报障 + 现场 log；plan/aistudio-transfer-hardening）

- **决定**：只做传输层省税（A1 预取自停 / A2 让路自停 / A3 持久缓存根+容量上限 / B1 批量 blob /
  B2 控制面连接池 / B3 6s 预算 + 换连接重抽 / E1 准备段计时拆分）；**每条都必须可观测地关 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/remote-transport.md` §32「决策正文归档」· 锚 `### §2026-10-11-goalnn-aistudio-transfer-hardening`

## §2026-10-11-goalnn-ledger-pid-not-liveness（2026-10-11，账本 pid ≠ 存活判据；plan/self-node-ledger-pid-desync + 首轮评审 F1–F14）

- **决定**：**账本 pid 降级为提示，不再是判决**（S1 只针对 selfNode：`hubServer`/`trainingLoop` 的换代
  都经过控制台 ⇒ 无实测动机，登记为预备案）。(a) `fetchSelfStatus` 拆掉 pid 前置闸，超时**恒 4000**； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/console.md` §11「决策正文归档」· 锚 `### §2026-10-11-goalnn-ledger-pid-not-liveness`

## §2026-10-11-decisions-slim-round3（2026-10-11，用户指令：DECISIONS.md 瘦身 —— 正文拆分迁移进具体文档 + 整合相似内容与同一问题的先后进展）

- **决定**：2026-09-24 起、正文 **>12 非空行**的 **192** 条全文搬进 7 个主题档既有
  「决策正文归档」节的末尾（锚 `### §<id>`，与第一轮同址同规）；索引行压成「标题 + 原文节录 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-11-decisions-slim-round3`

## §2026-10-11-decisions-slim-round4（2026-10-11，用户指令：「切 >6 的条目」 —— 第四轮瘦身：正文下沉 + 家族谱系续链）

- **决定**：**阈值 = 「仍带成篇正文（无 `—— 全文` 指针）且正文 >6 非空行」**；本轮 35 条 + 两条记录条
  共 **37** 条正文下沉进 5 个主题档的「**第三轮归档**」块（接各档上一张「第二轮归档」表：
  eng 7 · con 3 · ts 7 · rt 1 · exp 19），索引行压成……
—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §20「决策正文归档」· 锚 `### §2026-10-11-decisions-slim-round4`

## §2026-10-11-goalnn-lock-single-source（2026-10-11，用户指令：三处同款锁收敛到 `instance_lock` 原语，消掉同样的窗口）

- **决定**：`common/instance_lock` 为**唯一实现**；新增 `marker=()`（空 = 不做身份核验：活着就拒启，
  死了/够旧的残缺才接管）· `force=True`（显式接管）· `announce=False`（抢不到就改走别的路的调用方， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §75

## §2026-10-11-goalnn-push-snapshot（2026-10-11，用户指令：`_dispatch` 两次状态读收敛成一次一致快照）

- **决定**：`PushDispatcher._snapshot() -> _TickView`（一张 `self._lock` 内取出 records/avoid，
  `worker_counts` 由同一份 records 派生）；`_dispatch` 入口取一次、每门课都用它；三个旧读取口 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §75

## §2026-10-11-goalnn-load-flake-pair（2026-10-11，两条满载 flake 的根因修复：hub 备份腿 TOCTOU + instance_lock 写入窗口）

- **决定**：① 备份副本的闸住 `_claim_locked`（`_facts_locked(...)["landed"]` ⇒ 拒发，与 `claimable_job_ids`
  / `_unfinished_pending` 同一条式子：`result/` ∨ `fail.json`）；② `common/instance_lock` 的残缺锁 + 新鲜 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/nn/engineering.md` §74

## §2026-10-11-goalnn-no-httpx-dep（2026-10-11，**不引入** `httpx`（T1）；plan/nn-training-reuse-and-deps §5.2）

- **决定**（用户最终裁决 2026-10-11；新运行时依赖属 AGENTS §6.5 点名须问人的四类）：**不引入** `httpx`。
  曾一度按「形态 B」落地过（硬依赖 + 云机侧补装：`remote/runtime_deps.ensure_runtime_deps()` 在 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `plan/nn-training-reuse-and-deps.plan.md` §5.1 · §5.2 · §5.3

## §2026-10-11-goalnn-rollout-topup-fastpath（2026-10-11，rollout 热路径四刀：探针上界**另立常量** + 补差批按机器宽度铺满 + 轮级 `RoundContext` + **取消搬回**）

- **决定**：
  - ① 探针上界**另立** `game_watch.PROBE_CEILING_SEC = 5.0`（本机实测 8MB 探针 0.009–0.015s / 690–1250 MB/s ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-11-goalnn-rollout-topup-fastpath

## §2026-10-11-goalnn-aborted-course-revival（2026-10-11，停腿课复活 + 复活卫生 + 拒收终局 + `/admin/cancel-job`；plan/aborted-course-revival + 首轮评审 F1–F8）

- **决定**（W1–W7；serve 侧零协议、hub 侧纯加法）：
  - W1/W2 `_settle_rounds` 把「已处理」与「已收官」分开记账（ABORTED 进 `done_hooked`，不写 ……

—— 全文（现场三源 / 根因链 / 评审响应表 / DoD）→ `plan/aborted-course-revival.plan.md` §1–§9 ·
`docs/nn/remote-transport.md` §82 · 评审 → `plan/aborted-course-revival.review-bf.md`

## §2026-10-11-goalnn-x24-stack7-fork（2026-10-11，stack6-it165 上并行两单变量腿：burst 连击税 + dist 远杀补贴）

- **决定**：fork 两腿（起点均为 stack6-it165，sha `4e0d3a6c76f26188`，it0 原样拷贝；
  §15.5 新实验：新 out/traj/opt fresh，不在 stack6 账本上续跑）—— ……

—— 全文（剂量/门/熔断/yardstick）→ 两课程文件头注 · 基线 `tmp/x24-stack6/eval_log.jsonl:it165`
  （0.5125/14.31，疑似峰已在门里声明）

## §2026-10-11-goalnn-x24-s56-close（2026-10-11，stack5/stack6 结课判杀 + gap 注两条订正）

- **决定**：两腿按门杀腿归档，结课报告写回各自课程文件尾部；stack6-it165 冻结为 stack7
  双腿 fork 点；`tools/gap-text-table.ts` NOTES 订正两条——D06「重炮」说证伪 ……

—— 全文 → `nn-training/curricula/x24-stack{5,6}.jsonc` 尾部结课报告

## §2026-10-12-goalnn-burst220-crown（2026-10-12，burst-it220 弑君+卫冕加冕 + stack7 双腿结课）

- **决定**：① burst-it220 加冕现役最优（用户裁定）：弑君战 1600 对 0.5550 vs s4-it180
  0.4913（+6.38pp，χ²cc 12.14，p≈0.0005，两段同向，kills +0.83）+ 卫冕战 1600 对 ……

—— 全文 → `nn-training/curricula/x24-stack7-{burst,dist}.jsonc` 尾部结课报告 ·
  证据 `tmp/burst-crown-4166|4174/rows.jsonl` + `tmp/dist-crown-4152|4156/rows.jsonl` +
  `tmp/panel-dist-{it0,it40,it300}.jsonl`

## §2026-10-12-goalnn-offline-start-checkpoint（2026-10-12，离线腿起点 checkpoint 的 `it` 错位：起点单源化 + 播种先于补传腿 + 补传 409 冲突闸；plan/offline-start-checkpoint-it-collision + 首轮评审 F1–F6）

- **决定**（D1=A 起点永不回传 / D2=首写锁定不动但区分 rollout-only / D3=A 断言抽函数在装配点 / D4=A 同族一起修）：
  S1 起点单源化（删 `bundle.py` 的铺设块 + 零读者的返回键 `it`；补「包根 `opt.tar` 回退」—— ……

—— 全文（现场证据 / 根因 A–D / S1–S9 / 验收门槛 / 评审修订表）→ `plan/offline-start-checkpoint-it-collision.plan.md` ·
`docs/nn/remote-transport.md` §83 · 评审 → `plan/offline-start-checkpoint-it-collision.review-bf.md`

## §2026-10-12-goalnn-obs-v5-critic-superset（2026-10-12，obs-v5：观测缺口一次补齐，**critic 输入集解禁**（推翻 engineering.md 的既有否决）；plan/obs-v5-vector-gap.plan.md + 首轮代码评审 F1–F10）

- **决定**（D1=A 单腿一次补齐 / D2=A value 全吃 pf / D3=A 只追加不重编号 / D4=A mirror 双侧同步）：
  ① **推翻**该否决：critic 输入集**不是**不可动契约——训练侧属 AGENTS §0.2 允许域，且根因正在于 ……

—— 全文（冻结规格 §2.1–§2.7 / 实施步骤 S0–S9 / 验收表 A·B / 预注册门 G0·G3·G5 / 评审记录）→
`plan/obs-v5-vector-gap.plan.md` · 代码落点 → `src/nn/{policy-extra,obs-encoder,infer,danger-metrics}.ts` ·
`nn-training/common/schema.py` · `nn-training/worker/models/student.py` · `nn-training/worker/scripts/init_spatial_leg.py`

## §2026-10-12-goalnn-x26-obs（2026-10-12，x25 改名 x26-obs＋batch 减半＋奖励复核去死税；§15.5 新实验）

- **决定**（D1=改名含全身份：课程文件＋权重目录＋it0 文件＋out/traj/backup 全换 x26，
  it0 经 mv 改名 sha16 `6f32a17362cbe5ab` 不变，meta 无输出路径故干净；`paired_rotate_seed` ……

—— 全文 → `nn-training/curricula/x26-obs.jsonc`（头注含 17 项复核表）· 证据 → x24 burst/dist
结课报告 · `docs/nn/experiments.md` §71 · DECISIONS §2026-10-12-goalnn-burst220-crown

## §2026-10-12-goalnn-x26-batch（2026-10-12，x26-obs 的并行 batch 对照腿；§15.5 新实验）

- **决定**（D1=并行变量取 batch 全量：`target_transitions` 24576→49152，其余键经脚本
  逐键对账一字不差——同起点 it0、同流 20261020、同奖励、同优化器、同 eval；D2=同流 ……

—— 全文 → `nn-training/curricula/x26-batch.jsonc`

## §2026-10-12-goalnn-x26-batch-retired（2026-10-12，x26-batch 未开课撤回；§6.4 偏离更正）

- **决定**：x26-batch 撤回（文件已 `rm`，从未发布）；x26-obs 单腿跑，batch 不再设对照。
  ladder 内无第二条需训练验证的 obs 方向（见本条后果）：ladder 可见族（tti/laneE/s39/ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-12-goalnn-x26-batch-retired

## §2026-10-12-goalnn-gpu-overlap-correction（2026-10-12，认错：单 GPU 双腿墙钟论错了；§6.4 更正）

- **决定**：① 上条结论（x26-batch 撤回）**维持**，但理由只剩一条：49k 加的是 GPU update
  相的活（真正的瓶颈），24k 已被 31＋22 门前课证明充分——不是“双腿拉长墙钟”。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-12-goalnn-gpu-overlap-correction

## §2026-10-12-goalnn-x26-tower（2026-10-12，x26 并行架构对照腿：腿 B；override r6 单腿）

- **决定**（D1=单变量＝架构＋必需起点：batch/奖励/流/优化器/eval 逐键同 x26-obs，
  脚本对账零差异；起点 `weights/x26-tower.it0.json` 与 x26-obs **同源 burst220**， ……

—— 全文 → `nn-training/curricula/x26-tower.jsonc`

## §2026-10-12-goalnn-leg-a2b-migration（2026-10-12，burst220 可迁腿 B：A→B 映射＋源族自动识别）

- **决定**（D1=加 `_LEG_A2B_SEGMENTS=((128,152,9),)`：fc 照旧 drop 10，头拷一段，
  value 不进表（全重 init）；D2=腿 B 源按旧头宽自动识别（151→三段表／137→A2B 表， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-12-goalnn-leg-a2b-migration

## §2026-10-12-goalnn-x26-double-kill（2026-10-12，it206 双杀：obs 检查线挂＋tower 平台）

- **决定**（用户拍板）：双杀（obs 按预注册“②挂即杀”fail-fast；tower 平台＋G0 无望，
  省 95 轮 GPU）。停腿人在控制台执行。修法不存在热补丁（改训练语义 = 新实验）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-12-goalnn-x26-double-kill

## §2026-10-12-goalnn-prefetch-stage-unpack（2026-10-12，把「解包」从关键路径搬到上一轮 PPO 上：预取线程解包 + 单点搬入 + 形状分派；plan/prefetch-stage-unpack + 首轮评审 P0-1..P2-6）

- **决定**：D1 = **预取线程解包**（`PrefetchStore.store(..., unpack=False)` 缺省**不解**，
  填充器只解 peek 顺序前 N 份、`--prefetch-unpack N` 缺省 1）；D2 = **搬入只在 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-12-goalnn-prefetch-stage-unpack

## §2026-10-12-goalnn-est-samples-retire（2026-10-12，`est_samples_per_game` 从课程/CLI 面退场：只留盘上派生量 + 无历史不猜）

- **决定**（`plan/rollout-estfree-worksteal.plan.md` v2 §4.2 E1–E6；切片 P0-a 已落地）：
  - **旋钮退场**：字段降**死键**（`DEAD_COURSE_KEYS`，开课侧一行告警）；删门禁 `_check_volume_keys`； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-12-goalnn-est-samples-retire

## §2026-10-12-goalnn-value-gating（2026-10-12，x26-obs it90 诊断的修复：value 门控＋x27 双腿）

- **决定**（D1=零起步：`init_spatial_leg` 加 `drop0` 模式，value 新列纯 0——0.01 噪声起步
  正是爆炸源；policy/fc 尾照旧随机的（良性 2–4x）；D2=后步乘性收缩 `w *= (1-ρ)`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-12-goalnn-value-gating

## §2026-10-12-goalnn-offline-leg-ppo-attribution（2026-10-12，自主腿跑完的轮计进 dashboard 的 PPO 组：两个身份字段 + 1 条 = 1 job 等价；plan/offline-leg-ppo-attribution）

- **决定**：D1 = 账本行落**两个**键，分工不同、**永不合并**：`worker` = claim 身份（客户端自报
  优先，否则用**本课 hold 镜像** `store.hold_meta["worker_id"]` 兜底——补传天然发生在 ……

—— 全文 → `docs/nn/console.md §37`；计划 → `plan/offline-leg-ppo-attribution.plan.md`

## §2026-10-12-prefetch-stage-budget-revert（2026-10-12，撤销 prefetch staged 的 256MiB 字节闸：它从没生效过，还把整档关掉了；实机读数证伪 plan/prefetch-stage-unpack 的 P1-2 与 D4）

- **背景**：D4（`§2026-10-12-goalnn-prefetch-stage-unpack`）给 staged 解包产物设了「单份上界 + 总预算
  256MiB」，超界 ⇒ 删 `.tmp`、保留 zip、一行日志。落地当天 colab 真机日志连打 ……

—— 全文 → `docs/nn/remote-transport.md §85`

## §2026-10-12-transfer-jitter-tolerance（2026-10-12，抖动是常态：坏签判据加迟滞+衰减、重抽无收益即停、控制面首败不退避；plan/transfer-jitter-tolerance）

- **背景**：kaggle ppo worker 真机 log，用户口径「网络抖动，是很常见的事情！」。同一会话内链路
  3 秒内 3,089 KB/s → 79/51 KB/s，而三条"错误"全是防线把常态当异常：① 参照速率是**会话高水位、 ……

—— 全文 → `docs/nn/remote-transport.md §86`

## §2026-10-12-transfer-jitter-tolerance-abc（2026-10-12，plan A/B/C 落地：payload 的 Range 断点续传 · 上传让路点 · 控制面死线与 P0 指标解耦）

- **背景**：§2026-10-12-transfer-jitter-tolerance 只治了「判据误报」与「无收益还抽」，把三件结构性
  的事留在 `plan/transfer-jitter-tolerance.plan.md`（A→B→C）。本条是那三项的落地：**抖动是常态 ……

—— 全文 → `docs/nn/remote-transport.md §87`

## §2026-10-13-goalnn-x27-frozen（2026-10-13，x27 双腿冻结 + obs-v5 感知增维退场：panel/G5/接线三证据）

- **决定**：D1=obs-v5 系列冻结（C 类先验"三次阴性"+本轮=四次阴性，退场；corr/wallDist 几何类转专场，
  空旷场无信号 A6 −0.93 为证）；D2=x27 双腿结课（it300 不跑，G0 大考免；x26-obs 主腿同停； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-x27-frozen

## §2026-10-13-goalnn-x28-d01w08-design（2026-10-13，x27 冻结后双腿：D01-nearSq×1.5 + W08-wBlock×4；panel 门 + 80 轮封顶）

- **决定**：D1=双腿并行（GPU 交叠白赚）：`x28-d01`（wNearSq 42.0）+ `x28-w08`（wBlock 440.0），
  起点同为 x27-obs.it100（sha16 `5e6eac2c51c75aad`，165 宽直装），新家族同流 20261022， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-x28-d01w08-design

## §2026-10-13-code-zip-role-trim（2026-10-13，`code.zip` 按角色裁剪：量法=云端入口 import 闭包，2.796 MB → 1.221 MB；`levels/` 是硬依赖不许排）

- **决定**：`pack_code_zip` 的 `_exclude_dirs` 增 `e2e` `hub` `ladder` `curricula` `tools` `trainer`，
  `_exclude_files` 增 `conftest.py`（根）。实测 **2.796 MB → 1.221 MB（−56%，214 文件）**； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-code-zip-role-trim

## §2026-10-13-ladder-template-trim（2026-10-13，ladder 模板库裁剪：删 20 门 ladder-cNN 课程 + 13 个只被它们引用的关卡；★ ladder 工厂能把它们静默生成回来）

- **决定**：删 33 个文件 —— `curricula/ladder-c01..c20.jsonc`（20）+
  `levels/ladder-c08..c20.jsonc`（13，删课后引用数归 0）。**保留**：`levels/ladder-c01..c07` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-ladder-template-trim

## §2026-10-13-ladder-factory-retire（2026-10-13，退役 `biz/ladder_factory.py`：它只是刚删的 ladder 模板库的生成器；顺带更正上一条的事实错误 + 修两条护栏）

- **决定**：删 `biz/ladder_factory.py`（447 行）+ `tests/biz/test_ladder_factory.py`（269 行）；
  其中**唯一不依赖工厂**的判据迁走：`test_arena_retired_single_source`（arena2/arena3 不许复活、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-ladder-factory-retire

## §2026-10-13-goalnn-x28-it40-verdict（2026-10-13，x28 双腿 it40 双杀：D05 死 null＋W08×4 强 null；40 轮止损生效）

- **决定**：D1=d01 腿杀（主 D05 均值 −1.1%、同对中位 p=0.818 死 null，门线 ≤65.6 未到；
  次 D09 p=0.093 方向对、不显著；D01 同对 p=0.042 **显著恶化**、通关 84→73—— ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-x28-it40-verdict

## §2026-10-13-goalnn-y1-restart（2026-10-13，新纪元 y 系列第一课：c01 从零爬梯；单腿；出生全税＋0 伤门）

- **决定**：D1=单腿 `y1`（level ladder-c01；起点 `weights/y1/scratch-init.json`，
  `init_scratch_weights.py` 加 `--policy-extra`（建 165 档，缺省行为不变）＋ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1-restart

## §2026-10-13-goalnn-y1simple-control（2026-10-13，y1 的并行价格体系对照：x1 极简复刻，同起点同流）

- **决定**：D1=`y1-simple`（reward 逐字 x1：公式＋参数＋terminal −5/−3；
  其余逐键同 y1：**字面同一份 scratch it0**、同流 20261023、同 level/iters/优化器/K/eval、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1simple-control

## §2026-10-13-goalnn-y1-it40-verdict（2026-10-13，y1 it40 门 fail＋it42 熵熔断 ABORT；死因探索死亡，价格无罪）

- **决定**：D1=y1 本配置杀（门 fail＋熔断双判决；跑满 80 无意义）；D2=病因定为**探索死亡**，
  出生价格无罪（D01 −99%＋138 clears＋开火不塌三条出罪）；D3=y1-simple 不动（门待其 it40， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1-it40-verdict

## §2026-10-13-goalnn-y1long-design（2026-10-13，y1/y1-simple it40 timeout 强税续跑：terminal timeout −12.0；自杀 tripwire 预注册）

- **决定**：D1=`y1-long`（y1.it40起点，sha16 `56344888e8974f3a`；terminal {}→{timeout −12.0}，
  余同 y1）；D2=`y1-long-simple`（simple.it40起点，sha16 `01c0cd679a99eb79`； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1long-design

## §2026-10-13-goalnn-y1long-maxticks（2026-10-13，timeout×4 落关卡 fork：ladder-c01-long 6000；上条 terminal 设计作废）

- **决定**：D1=fork（`levels/ladder-c01-long.jsonc`：与标准逐字节同，唯 name＋max_ticks 两处差；
  标准 c01 不动 ⇒ rule/test/human/旧读数/codezip-set 全安；删 fork 即回退，引用响亮报错）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1long-maxticks

## §2026-10-13-goalnn-y2-prep（2026-10-13，爬梯第二档 y2 预备：c02-long fork＋继承 y1 setup；bc 待 simple.it80，安全锁）

- **决定**：D1=fork（`levels/ladder-c02-long.jsonc`：脚本双断言通道，与标准逐字节同，
  唯 name＋max_ticks 6000 两处差；规则值→6000 = 2.86×，override 沿上条）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y2-prep

## §2026-10-13-goalnn-long-stale-worker（2026-10-13，y1-long 双腿跑在旧码执行器：双 rms 恒 0＋it1 头重置＋绳子疑似未装；x27 断档确认）

- **决定**：D1=腿不重启（it0 基线真权重所测＋行为连续；重开=第二次重置，更大混杂）；
  D2=遥测盲区 analytic 补位（本条直算方法；每门必读 checkpoint，不读日志 0.0）＋ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-long-stale-worker

## §2026-10-13-goalnn-kaggle-stale-process（2026-10-13，kaggle-c 是没重启过的长寿进程：trainer/hub 重启不管 worker；修法＝重启 worker 不是刷 hub）

- **决定**：D1=修法改为**重启 kaggle-c worker 进程**（新鲜加载当前码：rms＋leash＋165 档
  一次齐；165 对 165 直装无断点；回迁 pullback 已证）；hub 重打包**不需重做** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-kaggle-stale-process

## §2026-10-13-goalnn-cache-not-process（2026-10-13，更正上条：kaggle-c 是开课后新起的，旧的是缓存不是进程；修法＝清缓存＋simple-long tripwire 掐停）

- **决定**：D1=修法改为**清 kaggle-c 持久缓存**（删缓存根／版本锚升级二选一，hub/worker lane；
  重启进程单做无效——事实已证）；D2=y1-long-simple **tripwire 掐停**（checkpoint 直算 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-cache-not-process

## §2026-10-13-goalnn-rescind-halt（2026-10-13，收回 simple-long 掐停令：0.20 线无依据＋死亡是探索税；改症状门）

- **决定**：D1=掐停令收回（腿没停过，无需 resume；课程文件状态＋verdict 已改回在跑）；
  D2=tripwire 改症状门（greedy 翻转转坏／rollout 进行性崩／熵死／value>0.35 超全部先例）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-rescind-halt

## §2026-10-13-goalnn-task-revert-rerun（2026-10-13，timeout 恢复 1500＋y 系课程全删＋y1 按优先级配方重开＋y1k 对照；c01 诊断：dist 彩票 67%）

- **决定**：D1=清场（y1/y1-simple/y1-long×2/y2 五课程文件删〈未跟踪，我的〉＋fork 删
  〈halt 后〉＋tmp/y1* 运行态清 〈halt 后〉；weights/ 归档不动；已halt确认后执行）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-task-revert-rerun

## §2026-10-13-goalnn-y1f-design（2026-10-13，y1f phase-1 先开火腿：y1 删贴脸/火线两税＋加 explore 0.2，a2 边际抄法；优化器冻结）

- **决定**：D1=`y1f`（y1 逐字＋删 wNearSq 84/wThreat 80＋加 wExplore 0.2；同 s8（sha `5445bdd0f04d6762`）、
  同流 20261024、同 level/iters/batch/优化器/K/eval、kickstart 关、anchor 0.001；terminal {} 不动）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1f-design

## §2026-10-13-goalnn-y1d-design（2026-10-13，y1d 纯删对照腿：与 y1f 只差 wExplore，测胡萝卜必需性）

- **决定**：D1=`y1d`（y1f 逐字＋删 wExplore 一项；相对 y1＝删 near/threat；同 s8/流 20261024/
  优化器/K/门；新 out/traj tmp/y1d＋backup weights/y1d，§15.5）；D2=门与 y1f 一字同 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1d-design

## §2026-10-13-ppo-job-lifecycle-ownership（2026-10-13，PPO job 生命周期收口：lost 可判／重启按 runId 作废／重发入口／残留自动清理）

- **决定**（plan/ppo-job-lifecycle-ownership，五段按 M1→M2→M3→M5→M4 落地）：
  D1 **M1 lost 可判 + 看门狗自愈**：判据 = `/jobs/{id}/status` 的 404（未知 jid 现成 404 ⇒ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-ppo-job-lifecycle-ownership

## §2026-10-13-goalnn-body-transfer-guard-v2（2026-10-13，传输护栏 v2：`read1` 收字节 + 时间窗采样与 2 窗迟滞 + 失败尝试入账；plan/body-transfer-guard-v2.plan.md + 首轮评审 R1–R8）

- **决定**：**M1** 新唯一收字节口 `_recv_some(resp, limit)`（= `read1` 语义），`remote/http.py` 与
  `remote/tailscale_boot.py` 的 `_read_body` **两处同改**（后者单文件自包含，孪生是结构性的； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-body-transfer-guard-v2

## §2026-10-13-goalnn-y1h-design（2026-10-13，y1h A线调税腿：y1f只去wHurt，测挨打税是否按头）

- **决定**：D1=`y1h`（y1f 单变量删 wHurt；同 s8/流 20261024/优化器/K/门；新 out/traj tmp/y1h＋
  backup weights/y1h，§15.5）；D2=门与 y1f 一字同（shots≥10＋cells≥5＋kills≥0.3，D01 watch， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1h-design

## §2026-10-13-goalnn-y1a-design（2026-10-13，y1a B线BC迁移腿：a2.it45为师＋y1f配方＋缰绳）

- **决定**：D1=`y1a`（配方 y1f 逐字，变量只剩起点技能；bc 指转换产物
  `weights/y1a/a2it45-warmstart-v5.json`〈**待生成**，命令在头注：init_spatial_leg.py --legacy-arch， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1a-design

## §2026-10-13-goalnn-y1fd-it40-verdict（2026-10-13，y1f/y1d it40：y1f 三挂杀、y1d 偏离续命、explore 零效应、y1h 开）

- **决定**：D1=y1f **杀**（无异议）；D2=y1d **§6.4 偏离续到 it60**（字面挂两条，但唯一活口＋rollout 最好＋
  kills 过＋shots 降伴随 kills 守＝效率化非恐惧；改判线 it60：shots<5 或 kills<0.3 即杀， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1fd-it40-verdict

## §2026-10-13-goalnn-y1a-it5-hold（2026-10-13，y1a it5 满分保持＋analytic 关闭接线盲：三腿零三种死法）

- **决定**：D1=y1a **续**（守住，无动作，等 it10 看 D01 趋势）；D2=接线盲关闭：直算证 y1a 结构性零
  （legacy 空片；缰绳看 KL）＋y1f/y1d 新列真动（policy 0.099/0.074、value 0.287/0.212； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y1a-it5-hold

## §2026-10-13-goalnn-tax-start-rung（2026-10-13，x2–x6 加冕迁新网普查：c04 发病，y4 为第一加税级）

- **决定**：D1=**y4（c04）为第一加税级**（胜率 95→76 悬崖＋D01 翻倍＋连击翻 3 倍＋转化层全点火，
  c04–06 n=50 下 19pp 实）；D2=y2/y3 沿 phase-1 经济（免 near/threat；97%/95% 不值得冒恐惧）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-tax-start-rung

## §2026-10-13-goalnn-y45-design（2026-10-13，y4/y5 加税腿：x4/x5r 为师＋x27 逐字，300 轮人工盯盘无熔断）

- **决定**：D1=y4（c04＋x4-mig，sha `770b14b9e3d431a4`）＋y5（c05＋x5r-mig，sha `b17614921b32ff15`；
  不用 x5a：机制探针、主端点死，不当师傅）；配方/terminal/scheme/优化器/K 与 x27-obs 全逐字； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y45-design

## §2026-10-14-wire-diet-transport（2026-10-14，payload/result 传输瘦身：**回传字节可降质，本机计算的 opt 保持 f32**）

- **决定**：D1=**P1**——`metrics.npy` 改为打包端按课程公式算好 `reward.npy` 再发（**无损**，−27%；
  payload 头带 `{metrics_version, formula_fp}`，不符即 `ProtocolError`）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-wire-diet-transport

## §2026-10-13-goalnn-y5hit-design（2026-10-13，y5-hit 命中腿：y5.it300 为师＋wMiss 加压＋删 dist 彩票）

- **决定**：D1=`y5-hit`（y5.it300，sha `092ea1b13ebf9763`，legacy 同代直连，kickstart 关，opt fresh；
  wMiss 0.158→0.35〈边际 −0.05→−0.17〉＋删 wDist〈彩票排雷〉；wAcc/wHit/暴露全家 HOLD； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y5hit-design

## §2026-10-14-goalnn-wire-diet-m1m2（2026-10-14，wire-diet 落地 M1+M2：打包端先算 reward ＋ 字段 f16，**缺省关**）

- **决定**（§8-D1/D4/D5/D6 的执行，外加两处实施期推导）：
  ① P1 用**显式** `reward_spec` + `it`，**不读**模块级 holder（`biz.reward_context`）——打包点 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-wire-diet-m1m2

## §2026-10-14-goalnn-wire-diet-m0（2026-10-14，wire-diet 落地 M0/R0：`course_cache` 身份映射 + `manifest.opt_from`）

- **决定**：
  ① 身份映射住**持久根** `<cache_root>/course_cache/<course_fp>/{pending,latest}.json`，命中判据是 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-wire-diet-m0

## §2026-10-14-goalnn-wire-diet-m4-hub-torch（2026-10-14，用户裁决：M4/R3 的重建走 (a)「hub 引入 torch」，回传 opt delta f16）

- **背景**：M4（R3）要在 hub 侧把 `Δopt` 与 base 相加重建 `B_n`；今天 hub 只做原样字节透传（不解码 tar
  里的张量）⇒「hub 进程装不装 torch」是 `plan/wire-diet.plan.md` §7-2 / §10-2 挂起的**显式决策** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-wire-diet-m4-hub-torch

## §2026-10-14-goalnn-offline-segment-restart（2026-10-14，「停课 → 改大 iters → 开课 ⇒ 云机领到续段」：三处断点一次修掉）

- **来源**：用户 2026-10-14 报障（y5-hit 设 iters 100 收官 → 手改 500 想续跑 → 云机**一直不领任务**；
  重启课程 / 重启 trainer+hub / 手动删包重导都无效）+ 用户拍板期望行为 = **「手工停课，调大课程 iters ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-offline-segment-restart

## §2026-10-14-goalnn-wire-diet-m3-weights-delta（2026-10-14，R1：权重回传走 int16 单 scale delta；hub **不**引入 torch）

- **定案 1（单 scale）**：全模型共享一个 `scale = max|Δ| / 32767`。**否决**逐张量/逐参数 scale：
  ① plan 的 1.44e-4 相对 RMS 是在单 scale 下量的，换口径要重测；② 42 个 scale 加进头里（~700 B）会 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-wire-diet-m3-weights-delta

## §2026-10-14-goalnn-wire-diet-enable-all（2026-10-14，用户令：**不跑 A/B，wire-diet 缺省全部启用**）

- **原话**：「不需要 A/B，全部启用！！！」（承接 `plan/wire-diet.plan.md` §6 的「A/B 通过前 `--wire-diet`
  保持 0」；同一会话里最后一轮 M3/R1 刚落地）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-wire-diet-enable-all

## §2026-10-14-goalnn-offline-code-authority-and-width（2026-10-14，离线：工具态以包为权威 + 满铺宽度修退化）

- **现场**（y5-hit2 云机 [2/2]，一手日志）：① 冷启动「64 批 × 1 局补差」—— 96 核里只有 1 核干活
  （`满铺 width=1` + `serve_pool: spawned=1`），缺口 `{2000: 11644}` 数学上补不平（宽度 1 ⇒ 最多 64 局）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-offline-code-authority-and-width

## §2026-10-14-goalnn-offline-local-disk-first（2026-10-14，离线/rollout：**删掉网络盘测速探针** + eval 腿包上界 + `submit` 装配后移）

- **现场**（云机 `y5-hit` 自主 worker，一手日志）：02:48:49 → 02:51:08 **139s 静默**——`轮次补传检查` 的
  `ticks 5→6` 而 `cpu 只 +0.91s`（纯阻塞 IO，正常轮 +19~23s）；缺口末尾才打出 eval 腿 `_layout()` 的唯一一行， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-offline-local-disk-first

## §2026-10-14-goalnn-offline-local-disk-first-b（同日续：**产物根落本地盘** + 网络盘 `backup/` + 恢复腿）

- **上位**：上一条（A 腿 = 删探针）的 §3.2/§3.5；评审 P0-1（拍板-1 指错了开关）与 P1-3（恢复优先级）。
- **定案**： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-offline-local-disk-first-b

## §2026-10-14-goalnn-offline-local-disk-first-c（同日续：**备份抽屉的写方** + 段末交付面判据）

- **上位**：上两条（A 腿 = 删探针 · B1/C2 腿 = 两个根 + 恢复腿）；计划 §3.4 = C1/B4。
- **定案**： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-offline-local-disk-first-c

## §2026-10-14-goalnn-offline-local-disk-first-d（同日收尾：**持久面容量判据** B2 + 备份**对账** C3 + 离线版档案）

- **定案**：
  ① **B2 持久面容量判据**（`remote/offline_deliverable.py`）：开段前判 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-offline-local-disk-first-d

## §2026-10-15-goalnn-offline-open-ended-segment（2026-10-15，离线包支持无限轮：课程 `iters<=0` = 无限 ⇒ 发**开区间段**）

- **来源**：用户 2026-10-14 报障两次（「hub 导出 y5-hit / y5-hit2 的任务包一直失败」→「y5-hit2 改成
  `"iters": 2000` 还是失败！」）+ 用户口径 **「① 离线包应支持 iters 0（无限轮）；② 导出离线包失败应 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-offline-open-ended-segment

## §2026-10-15-goalnn-offline-export-status（2026-10-15，导出**成败**有权威落点：python 退出层写 `export-status.json`，控制台与 hub 都读它）

- **定案（落点与写点）**：
  ① 落点 = **与包同目录**（`tmp/<课>/export-status.json`）。python 侧 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-offline-export-status

## §2026-10-14-goalnn-wire-diet-hub-unplugged（2026-10-14，hub 与 numpy/torch 解耦：delta 容器只在 trainer 侧被消费）

- **决定**：**D1** hub_client 取消顶层 import 两个容器模块（收进 `publish_job` / `_pack_opt_init` /
  `verify_and_land` 的函数体，取值点内联）⇒ hub 的 import 闭包从此**不含** `remote.weights_delta` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-14-goalnn-wire-diet-hub-unplugged

## §2026-10-13-goalnn-y5hit2-design（2026-10-13，y5-hit2 接力腿：y5-hit 现文件逐字＋起点换 it500）

- **决定**：D1=`y5-hit2`（y5-hit 现文件逐字，差集仅名义键＋起点 it500〈sha `961bacde390cbf02`，
  同代直连，opt fresh〉；§15.5 新账本 tmp/y5-hit2＋backup weights/y5-hit2）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y5hit2-design

## §2026-10-13-goalnn-y5far-design（2026-10-13，y5-far/y5-pos 防近腿：直接双税 vs 站位税，保命中）

- **决定**：D1=`y5-far`（near×2＋encl×2；起点 it500 sha `961bacde…`；seed_rotate 补回 150〈复制 bug〉；
  staging 500/24/200 沿用；新账本 tmp/y5-far＋backup weights/y5-far，§15.5）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y5far-design

## §2026-10-13-goalnn-y5far-verdict（2026-10-13，y5-far 恐惧塌杀＋y5-pos 偏离续跑：near×2 too hot 实锤）

- **决定**：D1=y5-far **杀＋立刻 halt**（it218 空烧中）；D2=y5-pos **§6.4 偏离续跑**
  （字面挂线但唯一活口＋端点涨＋税在动；重判 it300/自然收官）；D3=paired 判： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y5far-verdict

## §2026-10-13-goalnn-eval-contamination-audit（2026-10-13，y5-far/y5-pos eval 混入 arena 局＋我漏 seed_rotate＋tmp/y5-hit 消失）

- **决定**：D1=verdict 全部按 s2000 重读（y5-far 塌成立更干净；y5-pos 由成长修正为健康横盘，
  §105 部分收回，记 §106）；D2=**建议把 y5-pos eval 改回单关**（"2000"×200，与 y5-near 同深， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-eval-contamination-audit

## §2026-10-13-goalnn-eval-authorship-correction（2026-10-13，用户手动编辑为准：混合 eval 是设计，§106 定性收回）

- **决定**：D1=§106 定性收回（改称混合设计；s2000-only 数字与重读法继续有效，记 §107）；
  D2=y5-far/y5-pos 文件冻结（不再提改配；y5-far 塌 verdict、y5-pos 健康横盘 verdict 不受影响）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-eval-authorship-correction

## §2026-10-13-goalnn-y5near-mixed-eval（2026-10-13，y5-near 跟混合 eval＋y5-hit 日志归档确认）

- **决定**：D1=y5-near eval 改混合（除名义/起点/near×1.5/iters150/max12 外与现 y5-pos 全键逐字同，
  校验绿）；门读 headline，s2000 为真值附读。D2=y5-hit 日志归档确认（§96–102 数封存为只读； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y5near-mixed-eval

## §2026-10-13-goalnn-y5pos-kill（2026-10-13，y5-pos 杀：it250 双连破 kills 线＋腰斩，漂移式死亡）

- **决定**：D1=y5-pos **杀（人点 halt）**；D2=y5-near 为 lineage（it27 健康，kills 4.14–4.35 全超父腿，
  near×1.5 无恐惧，剂量判待 it40）；D3=y5-pos.it215 文件永存（后悔药：任何时点可重开分支）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y5pos-kill

## §2026-10-13-goalnn-y5term-design（2026-10-13，y5-term 终局腿：y5-near 加 wWin＋5.0/timeout−5.0）

- **决定**：D1=`y5-term`（现 y5-near 逐字＋wWin 项/键＋terminal{timeout −5.0}；起点 it215
 （sha `f69385b36a1bcd11`，与 y5-near 同起点隔离终局效应）；kickstart 关，opt fresh； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y5term-design

## §2026-10-13-goalnn-y45-close（2026-10-13，y4/y5 结课报告落盘：跑满非杀；y1 系删除系列自 y4 起）

- **决定**：D1=y4/y5 文件各追加尾部结课报告节（规格头不动；load 复验绿）：
  y4（病灶动终点平命中税）/y5（买谨慎不买本事），数与 §101/§96 一致； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y45-close

## §2026-10-13-goalnn-y5legs-close（2026-10-13，y5-pos/y5-hit/y5-far 结课报告落盘）

- **决定**：D1=三文件各追加尾部结课报告节（规格头不动；load 复验绿）：
  y5-pos（漂移死，hitRate 至死守住；混合口径+s2000 注）/y5-hit（命中 0.40→0.84＋恐惧可恢复； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-13-goalnn-y5legs-close

## §2026-10-15-goalnn-offline-delta-readside（2026-10-15，离线 delta 权重：读侧落地 + 两版后缀 + 不重传）

- **决定**（用户三条口径逐条落地）：D1=**hub 不变**、**消费者自己判断** —— 读之前调
  `remote/weights_standard.ensure_standard_weights`（就地落地；幂等，整份只读 4 字节； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-offline-delta-readside

## §2026-10-15-goalnn-offline-opt-delta-readside（2026-10-15，离线 delta 的 opt 那一半：读侧落地 + `opt.tar`/`opt.delta` 成对）

- **决定**（与权重那一刀**逐条同口径**，用户 2026-10-15 三条不另起新规）：D1=**读侧落地**
  （`remote/opt_standard`：`BOD1` → 整份 tar；幂等；**转一次一直复用**，缓存键 = 容器 sha16）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-offline-opt-delta-readside

## §2026-10-15-goalnn-hub-lands-delta（2026-10-15，回传 delta 的 **hub 侧就地落地**：口径反转 + `stage` 注入）

- **决定**：D1=落地器住 `hub/deltas.py`（**L2**）：`land_round`（按目录落地，失败**保留原件** +
  响亮）· `land_into_stage`（受理路径：先在暂存目录重建，再走首写锁定/409 那套），两个落地器 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-hub-lands-delta

## §2026-10-15-goalnn-delta-review-hardening（2026-10-15，暂存区复核后的六处加固）

- **决定**：① 幂等判据只认**内容**（`have == w_full or have == wj`），命中时 `_heal_round` 把
  「形态没到位」的名字补上（落地失败只剩 `.delta`、旧数据容器占着 `.json`）——补完那轮能当锚点， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-delta-review-hardening

## §2026-10-15-goalnn-serve-revive-clears-runner-finished（2026-10-15，「收官后重开」不复位 runner 的收工态 ⇒ 假收官）

- **决定**：① `_revive_hygiene` 清 `runner.finished=False` / `finish_reason=""`（重开 = 人的续跑意图）；
  ② `_settle_rounds` 的「已收官」日志追加「｜收工判据：<`finish_reason`>」，让收工原因**永不再静默**。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-serve-revive-clears-runner-finished

## §2026-10-15-goalnn-run-complete-cause（2026-10-15，收官原因落账 + 横幅说人话：「改大 iters」只留给 iters 跑满）

- **决定**：① **写侧**：`run_complete` 追加两个**可选**键 `cause`（ASCII 枚举，与 `gate_verdict` 同口径：
  `iters`/`max_hours`/`course_gate`/`breaker`/`stop_loss`/`kickstart_burn`/`paired_kill`/`leg_abort`/ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-run-complete-cause

## §2026-10-15-goalnn-y5follow3-design（2026-10-15，三腿接续：encl/early/flank 各补最缺）

- **决定**：D1=三新课（全键 diff 绿；共享新种子 20261201，三路可比；起点核对沿标准口径）：
  `y5-encl`（term300 起：wEncl 8.6→17.2 一项，y5-far 剂量先例；门：encl＋命耗≤6%＋反watch ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-y5follow3-design

## §2026-10-15-goalnn-y5bdh-design（2026-10-15，flank.it80 三接续：back/dist/hide 补两病根）

- **决定**：D1=三新课（全键 diff 绿；iters 500/max 24 用户令）：
  `y5-back`（wBackHits 0.4→1.0［16%］＋wSideHits 0.2［11%，per-hit 5:1］＋wHit 0.6→0.2 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-y5bdh-design

## §2026-10-15-goalnn-y5bdh-nofuse（2026-10-15，新三课熔断全拆）

- **决定**：三文件 `ent_break 0.25→0.0`（熵 ≤0 永不成立）＋补
  `ent_break_max_winrate 0.0`（winRate ≥0 恒成立→守卫短路，F4 ENT 永不触发）双保险； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-y5bdh-nofuse

## §2026-10-15-goalnn-y5close-batch（2026-10-15，y5 七腿结课报告＋toll 删除）

- **决定**：D1=七文件各追加尾部结课报告节（规格头不动；load 复验绿）：
  near（交卷＋A/B/C 起点供给）/term（flat 税形象＋大振荡）/crown（四线全过＋c06 起点 it195）/ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-y5close-batch

## §2026-10-16-goalnn-cloud-eval-lands-delta（云机 eval 腿的读侧落地 + `wver` 同源）

- **决定**：D1=装配点落地（`_land_weights` ∶ `standard_weights_copy` 到旁路缓存
  `.weights-standard/`，产物本体保持 delta ⇒ deliver 字节/hub 判据/账本指纹不动；scratch 拷整份）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-cloud-eval-lands-delta

## §2026-10-15-goalnn-y20open-design（2026-10-15，转场 y20 三腿：dist/stack/obs）

- **决定**：D1=三新课（全键 diff 绿）：
  `y20-dist`（y5-dist 配方逐字复刻：wDist 0.6/near 48/encl 12.9/梯度 wWin/timeout −5）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-15-goalnn-y20open-design

## §2026-10-16-goalnn-y5bdh-close（2026-10-16，y5-back/dist/hide 三腿结课）

- **决定**：三文件各追加尾部结课报告节（规格头不动；load 复验绿）：
  back（变量无效确认：B3 0.1937→0.1874 反降，前判"倾向杀"升级；终点 0.93/四线全靠站位＋命中）/ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-y5bdh-close

## §2026-10-16-goalnn-offline-segment-roll（离线「无限轮」的下一半：J2 段末自动滚（含回灌腿）+ J4 静态拒因前置）

- **定案一（J2 三态）**：段末判据从两态 `end_it_reached` 扩成三态 `seg_end_kind`：
  `open`（无限课的段末）/ `course`（课末，封课）/ `segment`（有限课程被 `--run-iters` 截短的一段）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-offline-segment-roll

## §2026-10-16-goalnn-component-error-registry（组件错误注册中心：五处散落的错误 → 一张登记表 → 首页入口 + `/errors` 详情页）

- **定案一（五处 → 一张表）**：`ErrorEvent` 是唯一形状（类型住 `web/view/error-types.ts` —— 零 import；计算住
  `server/api/error-{fingerprint,scan,registry}.ts`）。四条腿**各带闸**：腿 A 本机组件日志尾 `size:mtime` 签名、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-component-error-registry

## §2026-10-16-goalnn-deliver-import-placement（产物导入落位：去掉目录改名，改逐轮 merge —— 解 Windows `WinError 5` 必败）

- **来源**：`plan/offline-backfeed-resilience.plan.md` §8.2/§9-12（§10 的 **A+B** 两片）· 现场
  `reports/y20-dist2-offline-backfeed-20261016.md` §3.8 · 评审 `plan/offline-backfeed-resilience.review-bf.md` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-deliver-import-placement

## §2026-10-16-goalnn-deliver-first-write-wins-import（本地导入/回灌同规：同名轮先到者为准 + 缺件愈合 + `--replace` 逃生门）

- **来源**：同上（plan §8.2/§8.3 的 **D5**；评审 F6：`import_from_run` 当时是「存在即整轮跳过」的
  **第二条路径**，与导入腿的「先 rmtree 再落」语义相反）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-deliver-first-write-wins-import

## §2026-10-16-goalnn-offline-legs-revive（两条观测/补传腿都不许「一停到底」）

- **来源**：`plan/offline-backfeed-resilience.plan.md` §2.2-2 / §4.2-1/2（评审 F1/F2）· 现场报告
  `reports/y20-dist2-offline-backfeed-20261016.md`（「补传 27 轮 0 送达」「进度冻在 it125」）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-offline-legs-revive

## §2026-10-16-goalnn-drawer-carries-ledgers（抽屉带读数账本；账本「变了才拷」；容量读数进 status）

- **来源**：plan §3.2/§3.3（DoD-2a/2b/2c，评审 D8）· §14（抽屉在网络盘上的写放大与配额）。
- **定案一（抽屉 = 能被直接吃的产物根）**：锚点名单追加 `metrics.jsonl` / `eval_log.jsonl` ——现场 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-drawer-carries-ledgers

## §2026-10-16-goalnn-rejected-ttl-and-reset（内容拒收不再是永久判决：TTL + 上限 + 人工重置）

- **来源**：plan §15（**D6**）：`rejected` 跨会话按权重指纹认领 ⇒ 一次**误判**（hub 侧 409 被读成内容
  问题、账本行取错）会让那一轮**永久不再回传**，而权重一直好好地躺在本地盘上。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-rejected-ttl-and-reset

## §2026-10-16-goalnn-import-checklist-and-name（导入：文件名 `@` 附注、落位计数上屏、包体检）

- **来源**：plan §6.2-1/3 + DoD-5a/5c（G 片）· §19（同一语义只留一份实现）。
- **定案一（文件名放宽）**：`deliver-<课>@<run_id>-it<N>.zip`（§5.2 的导出/一键包留历史名）的**课程段 = ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-import-checklist-and-name

## §2026-10-16-goalnn-backup-drawer-module（抽屉写方单独成家 + 层级账本重排）

- **来源**：`tests/test_python_loc_budget.py`（单文件代码行 < 1000，**只许收紧**）· 
  `tests/helpers/remote_dag.py`（层号 = 拓扑秩 = `1 + max(依赖)`）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-16-goalnn-backup-drawer-module

## §2026-10-17-goalnn-backfeed-e-f-g-h-close（E/F/G/H 四片收口：判据化重试与删除 · 一键包 · 报错表 · 停课导入）

- **E（§4 自愈重试 + 租约重领 + 判据化删除）**：不新增机制，把 tick 内已有的「试」收紧并记住 ——
  ① 探活负缓存从 60s 收到 **15s**（`PROBE_FAIL_TTL_SEC`，封顶仍 ≤60s，别把平坦重试换成退避）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-17-goalnn-backfeed-e-f-g-h-close

## §2026-10-17-goalnn-hub-memory-slim（hub 递送流式化 + result 有界 + 账本真增量 + 课程表可淘汰 + /admin/mem）

- **来源**：`plan/hub-server-memory-slim.plan.md`（L1/L2/L3）+ 事故现场：hub（`python -m hub.server`
  --port 8787 --traj-root tmp --discover）连跑 35h 后 **WS 6.24GB / priv 10.04GB / peak 7.71GB**， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-17-goalnn-hub-memory-slim

## §2026-10-17-goalnn-y21-quartet-design（y20 三腿全负后的四个新方向：减法/死亡链/中盘/退火）

- **决定**：四个单变量课程，同起点 stack2-it200（sha16 `1a7821240d3c47b9`）、同新种子 20270205
  （20270204 及之前全用过，§15.1；四路互相可比 + 与 y20 三腿同起点跨代可比）、其余设置逐字比 y20-stack2： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-17-goalnn-y21-quartet-design

## §2026-10-17-goalnn-y20legs-close（y20-wall/far/escape 三腿停腿结课）

- **决定**：三腿停办；结课报告节回写各自课程文件（状态行 ＋ 跑量/轨迹/readout/判决/遗产/证据）；
  `docs/nn/experiments.md` §114 汇总；权重冻结 `nn-training/weights/y20-{wall,far,escape}/` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-17-goalnn-y20legs-close

## §2026-10-17-goalnn-opt-delta-f16-underflow（y21-temper 熔断根因 = opt delta-f16 下溢成 v=0 动量炸弹）

- **决定**：① temper 判「外因致死」，不判退火方向死；② 修复传输下溢（hub 重建后 / worker 装载后对
  `exp_avg_sq` 消毒设下限，√v_floor≫ε=1e-8；备选 Adam eps 上调作保险），按 §7 先写红测试；③ 修复后以新课程文件 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-17-goalnn-opt-delta-f16-underflow

## §2026-10-17-goalnn-opt-underflow-sanitize-and-temper2（opt delta-f16 下溢消毒落地 + y21-temper2 重开）

- **落地**（`plan/opt-delta-f16-underflow-fix.plan.md` v2）：`remote/opt_delta.sanitize_adam_sq` 在
  worker 装载口（`train_core._load_opt_state`）与重建落地（`apply_opt_delta` post-pass）把 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-17-goalnn-opt-underflow-sanitize-and-temper2

## §2026-10-17-goalnn-single-resident-trainer（常驻写者收成一个 serve：准入闸 + 心跳判死；卡死只告警不自动重启）

- **来源**：`plan/single-resident-trainer.plan.md` §1–§2 + `plan/single-resident-trainer.review-bf.md`
  （P0-1 消费面 / P0-2 写点覆盖 / P0-3 预采子进程 / P1-F3 env 口 / P1-F6 pid 身份）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-17-goalnn-single-resident-trainer

## §2026-10-17-goalnn-entry-split-s3（入口分家 S3：run_rl 退成 CLI 壳；一次性腿独立成模块；逃生舱退役）

- **来源**：`plan/single-resident-trainer.plan.md` §3（S1/S2 之后的一步）+ 用户裁决 2026-10-17
  （「S3 拆入口后 `--allow-detached-trainer` 怎么办」⇒ **彻底删掉常驻分支**）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-17-goalnn-entry-split-s3

## §2026-10-17-goalnn-y21legs-close（y21 nohurt/chain/tempo/temper 停腿结课：opt 下溢污染、方向未判、v2 重开）

- **决定**：① 旧四腿全部停腿（nohurt it210、chain it213、tempo it212、temper it13），权重冻结、不回放；
  ② 不把旧结果当作 nohurt/chain/tempo 方向负判，不改 temper 的「外因致死」判； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-17-goalnn-y21legs-close

## §2026-10-18-goalnn-export-must-not-cancel-live-job（撤销权只归派活方 + 等待侧同看一份账本：导出进程不许撤在线腿的活）

- **决定**：① **A 撤销权收口**——`trainer/dispatch_queue.dispatches_jobs(args)` 是「本进程会不会
  派活/等回传」的**唯一实现**，`TrainingLoop.__init__` 的 M2 清理加它当闸，`takes_course_lock` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-18-goalnn-export-must-not-cancel-live-job

## §2026-10-18-goalnn-y22-phaseA（y22 阶段 A 四条腿：先修优化器，判据换成"余量"）

- **决定**：开 y22 阶段 A 四条腿（`nn-training/curricula/y22-{null,lr,batch,gamma}.jsonc`），
  起点 `y20-stack2.it200.pool800`（sha16 `1a7821240d3c47b9`）、seed **20270206**（四腿共享）、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-18-goalnn-y22-phaseA

## §2026-10-18-goalnn-demo-move-only（y22 B′：demo BC 只训 move，fire CE 全局删除）

- **决定**：`worker/ppo/engine.py` 的 demo BC **全局删掉 fire CE**，只留 move CE；
  **不加** `demo_heads` 课程键（不为已知坏项新建机制）。恢复成本 = 调用点加回一行。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-18-goalnn-demo-move-only

## §2026-10-18-goalnn-eval-gap-honesty（评估缺口不许伪装成读数：保真 / scratch 隔离 / 处置 / 第四态 / 判决准入）

- **决定**：① **D1 保真**——新 `worker/local_eval_error.LocalEvalProcessError`（errno 归一化的
  单一实现）保留 stderr 头尾两段 + `errno_code`/`msg_head`；`common.distribution.is_transient_error` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-18-goalnn-eval-gap-honesty

## §2026-10-18-goalnn-course-metrics-backup — 课程读数在 dashboard 侧自动转存 + 冷存（内存解压读）

- **背景**：2026-10-17 `tmp-clean.py --apply` 把 1907 项 / 38GB 一次抹掉，含全部课程账本
  （`training_log` / `eval_log` / `dist-agent-meta` / `.pool-actuals-cache`），读数不可恢复 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-18-goalnn-course-metrics-backup

## §2026-10-19-goalnn-rollout-end-event — 指标行在「采集收官」即刻上屏（新增 `rollout_end` 账本事件，不提前写 `iteration`）

- **背景**：控制台首页走势图与指标表的唯一数据源 = `training_log.jsonl` 里 `event=="iteration"` 的行。
  而 `record_iteration` 在一轮 13 步里排第 ⑪，前面压着 `ppo` —— 远端模式要等云机跑完梯度步 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-rollout-end-event

## §2026-10-19-goalnn-test-path-isolation — 测试零读写真实工作目录：路径 env 全量预载隔离 + `REPO_ROOT/tmp` 硬编码清零

- **决策**：
  1. **新增 `core/paths.ts::tmpCourseDir(course)`**（`= join(tmpLogsDir(), course)`）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-test-path-isolation

## §2026-10-19-goalnn-replay-export-decision-k — 重放导出必须透传课程的 decision_k/decision_events；对账不一致一律拒绝交付

- **现场（用户报障）**：控制台「导出 replay」勾了 y24-k4 it25 的 kills=0/1/2 共 22 局
  （eval_log 里这 22 局全是 `gameover`），导到 `replays/exports` 却有一大批是胜利（clear）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-replay-export-decision-k

## §2026-10-19-goalnn-rollout-end-json-null — `rollout_end` 行的 PPO 侧读数过 JSON 变 null：渲染层全链路「无读数显 —」

- **现场（用户报障）**：dashboard 首页报「该卡片加载失败：Cannot read properties of null
  (reading 'toFixed')」，发生在 y25-price it1 rollout 结束那一刻。浏览器栈： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-rollout-end-json-null

## §2026-10-19-goalnn-error-denoise-classify — 错误采集去噪 / 分类 / 活动度：访问日志看状态码、续行并入 detail、全局组件只扫一次

- **现场**：`/errors` 的活跃错误里多数是云隧道运维噪声 + 访问日志误报——`parseLogLine` 的
  `LOG_ERR_RE` 命中路径里的 `errors`/`error` 子串（`/admin/errors`、`/admin/worker-error` 的 200 行）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-error-denoise-classify

## §2026-10-19-goalnn-error-page-shell-and-resolve — `/errors` 包主页外壳（仍独立 bundle）+ 瞬时错误两拍撤销 / resolved 审计行

- **现场**：`/errors` 是独立 SSR 页 + 第 4 份 bundle，没有侧栏/顶栏，与其它页割裂；同时瞬时网络错误
  在日志尾窗里停留期间会一直占着活跃表与告警坞，源恢复后也不消失。用户口径：错误页包进主页框架、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-error-page-shell-and-resolve

## §2026-10-19-goalnn-a1-blocked-replay-v3 — A1 被 God-AI score gate 拒绝（回退）；回放帧先升 v3 保住 hold/fireBeforeTurn

- **现场**：`plan/nn-capability-upgrade.plan.md` 的 A1 要修“子弹方向 ≠ 开火时刻已提交朝向”。
  按 L1 实现两版：① `tryFire` 内先 `applyTurnCooldown`，被拒仍按旧朝向发射； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-a1-blocked-replay-v3

## §2026-10-19-goalnn-nn-only-hold-fire3 — A1 改为 NN-only 输入能力位：God-AI 保留侧射，B′ 拿得到“开火时刻朝向权威”

- **背景**：上一条 A1 被 God-AI score gate 拒绝，但 B′ 的 `fire=1/2` 与 `hold` 需要
  “先打再转 / 先转再打”的确定性。全局改 `tryFire` 会连 God-AI 一起改，已证明不可接受。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-nn-only-hold-fire3

## §2026-10-19-goalnn-b1-v6-head-switch — B1 动作空间三头以 `hold_head` 权重键切换，默认仍 5/2（拒绝半迁移）

- **背景**：B′ 的 B1 要把 `move(5)+fire(2)` 换成 `hold(2)+move(4)+fire(3)`。整条训练链
  （PPO rollout/update、BC dataset、shard `actions/masks`、`export-*` 工具、语料身份）都按 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-b1-v6-head-switch

## §2026-10-19-goalnn-obs-v6-b2b5 — B′ 观测/遥测一次 bump 到 v6（SCALAR 46 / EXTRA 57 / MAJOR 6 / metrics v12）：能力位进观测，口径只留一个

- **背景**：plan/nn-capability-upgrade §4-B2–B5 要在同一次 schema bump 里交付 prevAction /
  lastHit / 敌朝向 / leadHit / 威胁拆族 / 窗口三列——任何一项单独做都要付一遍全语料重导。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-obs-v6-b2b5

## §2026-10-19-goalnn-post-thaw-sentinel — B4 哨兵 `postThawDamageRate` 的口径：解冻帧起 60 拍、重新冻结即关窗、分母用 `player_damage`

- **背景**：B4 让冰冻期不再置零威胁（信息不删），需要一条「解冻即挨打」的读数防止把 D2 修成 D2'
  （plan/nn-capability-upgrade §4-B4 / 评审 Bf9）。计划只给了名字与「解冻后 60 拍内 player_damage 份额」， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-post-thaw-sentinel

## §2026-10-19-goalnn-a1-nn-only-semantics-and-god-side-shot-residual — A1 只修 NN 侧：转身被拒即不发弹；God-AI 侧射登记为永久已知残留

- **背景**：落地评审（`reports/nn-capability-review-20261019.md` §3 D2/D3）发现两处「后人按 plan 字面理解会与代码对不上」的坑：
  ① plan §3-A1 验收 case 3 写「冷却未满 ⇒ 子弹朝**旧方向**且该拍不移动」，而 NN-only 落点的**最终设计** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-a1-nn-only-semantics-and-god-side-shot-residual

## §2026-10-19-goalnn-a1-global-fire-facing-and-god-gates-retired — A1 全局落地（子弹方向 == 开火时刻的已提交朝向，人类/敌人/God 同规）；God-AI 表现门禁退役

- **用户指令（2026-10-19）**：「这是游戏底层的基础机制，要统一！修！修完再看 god ai 在 hard 难度下 35stages×20seeds 的表现变化。」
  → 测完：「god ai 原来的高胜率，是靠作弊实现的。既然现在胜率下降那么多，对于它的门禁就没有意义了，都禁用掉吧。」 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-a1-global-fire-facing-and-god-gates-retired

## §2026-10-19-goalnn-student-scalardim-from-weights — 融合宽度按 `fc.weight` 真实行宽反推（schema bump 改不了生产权重 ⇒ 不许硬取 `SCALAR_DIM`）；另：B′ 吞吐增量实测 0.19%

- **事故**：B′（`plan/nn-capability-upgrade.plan.md` §4-B2）把 `SCALAR_DIM` **40 → 46**，
  **fixture 跟着改了行宽**（`student-golden.json` 是 h=16 ⇒ `fc.weight` `[128, 62]` = 128×(16+46)）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-19-goalnn-student-scalardim-from-weights

## §2026-10-20-dashboard-open-course-not-live-gated — openCourse 移出「无活体」动作闸：它是活体的创造者，不是消费者

- **决定**：把 `openCourse` 从 `COURSE_MUTATING_ACTIONS` 移除。它的前置检查本来就在动作内部
  （`assertCourseExists` 课程文件存在性 + 锁身份 + 意图文件健康），闸不代劳。plan ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-dashboard-open-course-not-live-gated

## §2026-10-20-course-select-ghost-run-dirs — 课程 select 的 `*.run` 幽灵课：名字白名单写进实现，历史残留进隔离区

- **决定**：① `isCourseName` 增加 `.run` 后缀拒绝（扫描面 / 库内列表 / 冷存 / 课程发现同一口径）；
  ② `backupCourses` 与 `coldCourses` 补 `isCourseName` 过滤（历史脏目录不等清库就不可见）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-course-select-ghost-run-dirs

## §2026-10-20-rollout-node-codehash-training-side-crlf — 节点门「版本指纹对不上」的真凶在训练机侧（集内文件 CRLF 污染）

- **决定**：① 集内文件的**工作区字节必须 == 索引字节**——训练机自检 `dirty_hash_files()` 非空时，
  不得给远端发升级/重启，先修训练机（否则一次「升级波」打空，还把护栏变成静默排除）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-rollout-node-codehash-training-side-crlf

## §2026-10-20-eol-guard-commit-time-lf-normalization — 「commit 时把文件改为 LF」= 把**工作树**拉回索引字节（不是让 git 提交时归一）

- **决定**：① **本机 user 级 git 配置**：`core.autocrlf=input`（已有）+ `core.eol=lf`（新）+
  `core.safecrlf=warn`（新）——`safecrlf` 实测在 `git add` 时喊「CRLF will be replaced by LF」， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/repo-governance.md` §2026-10-20-eol-guard-commit-time-lf-normalization

## §2026-10-20-process-lifecycle-not-course-gated — 组件启停与课程正交：`stop` 闸的是 `stopCourse`，不是共享进程

- **决定**：`COURSE_MUTATING_ACTIONS` 只保留**真正读写 `tmp/<课>/`** 的动作：
  `smokeTrain` / `stopCourse` / `setCoursePaused` / `eval*` / `redoPpo` / `archiveCourse` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-process-lifecycle-not-course-gated

## §2026-10-20-hub-crash-commit-limit-and-infra-auto-restart — hub「崩溃」实为提交限额击穿下的**被外部终止**；基础设施组件改为自动重启

- **决定**：① **共享基础设施组件意外退出 ⇒ 自动重启**（新 `dashboard/src/server/auto-restart.ts`）：
  只管 **hubServer / selfNode**；**trainingLoop 是课程状态**（门判决/ALL DONE 后悄悄续跑 = ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-hub-crash-commit-limit-and-infra-auto-restart

## §2026-10-20-auto-restart-covers-all-managed-components — 自动重启扩到全部受管组件（trainingLoop / cloudflared 纳入）

- **用户指令**（2026-10-20）：「trainingLoop 和 cloudflared 也需要自动重启。」
- **口径修正（把判据从「哪些组件」换成「哪种退出」）**：本条**撤销**上一条里 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-auto-restart-covers-all-managed-components

## §2026-10-20-goalnn-y26-leg-a-policyextra — y26 线切腿 A：`policyExtra=true` 让 POLICY_EXTRA(57) 第一次真正进网络

- **决定**：y26 线改走**腿 A**（`policyExtra=true`，头输入 185 = hidden 128 + extra 57）。
  **不**把那些位搬进 scalar。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-goalnn-y26-leg-a-policyextra

## §2026-10-20-goalnn-y27-triad — y27 四腿（clear + shot / lead / window）：按测出的 Φ 份额配剂量激活三张休眠能力

- **命名**：原 `y26-clear-x` 改名 **`y27-clear`**（架构腿：`policyExtra=true` 的基线；奖励 = `y26-clear` 逐字，
  故 `y27-clear` vs `y26-clear` 的配对差 = **纯架构效应**）。三条能力腿都以它为基线。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-goalnn-y27-triad

## §2026-10-21-goalnn-y27-steady-pricing — y27 四腿之后不再守单变量：一门 `y27-steady` 同时修「死一局仍赚 / 开火恒负 / 挨打免费」并把三条休眠能力一次接线

- **决定**：新建 `nn-training/curricula/y27-steady.jsonc`（22 参数：12 重定价 + 2 新增 + 8 冻结）；
  `wWin` 改二元 48.0、`wSpeed 3.0` 单列、`wWinHit 1.6`、`wDist 4.0`、`wDmg 0.12`、`wAcc 2.0`、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-goalnn-y27-steady-pricing

## §2026-10-21-goalnn-y26-y27-close — y26/y27 十腿结课：唯一成立的是 wHit 0.35；两条预注册判据（乱射红线 / 单点 win 门）被数据判为坏口径

- **范围**：y26 六腿（null/clear/shotmix/dmg/dist/early）+ y27 四腿（clear/shot/window/lead）。
  十份结课报告**写进各自课程头注**（`── 结课报告（2026-10-21 …`，幂等 MARK，只动注释）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-goalnn-y26-y27-close

## §2026-10-20-goalnn-ppo-worker-transfer-isolation — PPO worker 的结果回传腿搬进独立子进程（`--result-upload process`）

- **用户指令（2026-10-20）**：「云机自主 worker 上，已经把网络传输进程与 PPO 进程独立，两者互不
  阻塞。ppo worker 也需要同样处理，请检查。」检查结论 = **确实没做**：自主腿的补传住在 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-goalnn-ppo-worker-transfer-isolation

## §2026-10-20-goalnn-ppo-worker-downlink-isolation — PPO worker 的**下行腿**（预取 payload 下载 + 解包）也搬进子进程（用户指令）

- **决定**：新增 `remote/payload_worker.py`（子进程，L5：跑**唯一实现** `_prefetch_fill` +
  `PrefetchStore`）+ `remote/payload_proc.py`（父侧 `PayloadProcess` / 工厂 `make_prefetcher`，L1）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-goalnn-ppo-worker-downlink-isolation

## §2026-10-20-goalnn-claim-timeout-outlives-console-leg — `claim` 的客户端超时必须 > hub 在应答里花掉的控制台预算（8s vs 8s = 白丢租约 + 一拖一自锁）

- **决定**：`remote/offline_boot.py` 新增 `CLAIM_TIMEOUT = 30.0`（20s 余量留给同一分支里的包
  `sha256`）并作为 `claim_course` 的缺省；只读探活/清单仍用 `PING_TIMEOUT`（8s）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-goalnn-claim-timeout-outlives-console-leg

## §2026-10-20-goalnn-port-probe-bounded — 端口探测必须带上界：`portListen` 的 connect 在丢 SYN 的主机上永不返回

- **决定**：`core/net.ts::portListen` 加**上界** `PORT_PROBE_TIMEOUT_MS = 500`（第三参可覆盖）：
  `Promise.race([probe, 超时])`，超时按 `false`；超时之后才连上的那一份连接仍然 `end()`（不许漏 socket）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-20-goalnn-port-probe-bounded

## §2026-10-21-goalnn-prefetch-p1-fire-and-forget — 下行腿的 `id` = **兑现凭据**：fire-and-forget 的 `p1` 广播**不应答**（WARN 刷屏的根因）

- **决定**（两侧各守一半，写成协议的一条硬约定）：
  ① 子进程 `remote/payload_worker.py::_reply` **只在命令带 `id` 时才写应答行**（无 `id` ⇒ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-goalnn-prefetch-p1-fire-and-forget

## §2026-10-21-goalnn-est-latest-and-dispatch-safety — 采集 est 取**最近一轮** + 派局压 0.92；**不承诺 `batches == 1`**

- **决定**：① 优先级 `learned → trailing`（`volume_alloc.pick_stage_est`）；② 新增 `agg="latest"`
  只取最近一轮（`resume.trailing_stage_samples_per_game`，本地链与 `loop_export` 同口径）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-goalnn-est-latest-and-dispatch-safety

## §2026-10-21-goalnn-effslots-diagnosis — E 低的成因是**重复执行 + 远端忙碌**，不是本地 lane；只测不改

- **决定**：① **不动**本地 lane 优先级（动了只会把活推给更慢的节点，墙钟更长）；② **不封顶**竞速副本
  （它是空槽能被填上的机制），要动先拿读数；③ 本轮只把读数做出来：`[dist] round done` 增 `dup=`、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-goalnn-effslots-diagnosis

## §2026-10-21-export-bundle-stall — 导出腿：基准搜索要有**总**预算，锁要按课分且有**持久**上界

- **决定**：① 一次顶层解析共享 `_SearchContext`（同一路径只读一次 / 失败按路径负缓存 / 成功按内容 sha
  正缓存 / 唯一读字节 2 GiB 与唯一候选 20000 双预算），撞顶抛 `SearchBudgetExceeded` 且**父层不得吞**； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-export-bundle-stall

## §2026-10-21-goalnn-online-worker-bun-for-self-collect — 在线 worker 盘**自采模式**下允许装 bun（`§2026-09-25-goalnn-online-worker-no-bun` 的**限定例外**）

- **触发**（用户 2026-10-21 裁决，原话「如果改成自采集，可以装！」）：`plan/rollout-corpus-supply.plan.md`
  腿 C（**GPU 实例自采语料**，`kind=iter`）需要该盘自己跑 TS rollout，而执行体 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-goalnn-online-worker-bun-for-self-collect

## §2026-10-21-goalnn-y29-resume-from-it300 — 不续旧课：从 `y28-steady.it300` 重开两臂（摘 `wWinHit` + `wDmg` 减半），因为 lr 档已打满而尾段斜率仍显著

- **决定**：
  1. `y29-steady` = `y28-steady` 的价目表**摘掉 `wWinHit`**（双臂已证零胜率回报：窗内命中 −14.2% 但 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-goalnn-y29-resume-from-it300

## §2026-10-21-goalnn-ppo-wallclock-batch1 — PPO worker 墙钟第一批：身份命中链**从未通电**（接线 + 顺序）、`/start` 线程化、两次同步合并、设备行、一机 N worker

- **背景**：`plan/ppo-worker-wallclock.plan.md` 三轮评审（ds / Beam / `plan/ppo.review-bf.md`）后落地第一批。
  现场日志（job `d749659e732144f2`）里 `init` 与 `opt` 每轮都 `src=download`（1.2s/轮），而身份映射的设计 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-goalnn-ppo-wallclock-batch1

## §2026-10-21-goalnn-offline-deliver-not-stuck — 补传「失败可以、不许卡住」；陈旧 hold 的恢复宽限**一代只给一次**；降级档替代拆件

- **决定**：
  1. **宽限一代一次**：派发记录加 `hold_restored_for_at`（落盘）；只有 `hold.at` 更新的接管才再吃一次。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-21-goalnn-offline-deliver-not-stuck

## §2026-10-22-goalnn-offline-realtime-anchor-and-slim — 实时体只发**锚点轮**、逐局评估行过白名单、`perGame` 走包（修订 §2026-10-21 的三条否决）

- **决定**：
  1. **逐局评估行过白名单**（`EVAL_ROW_WIRE_KEYS`）：实时体只留「hub 去重要用的 5 键 + 控制台 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-22-goalnn-offline-realtime-anchor-and-slim

## §2026-10-22-goalnn-offline-full-weights-backup — 打包出口用**注入的 `full_for`** 接上整份（复用 `<store>/.weights-standard/`），不改 `hub/deltas.py`、不搬模块

- **决定**：
  1. **注入，不搬家**：`finalize(full_for=…)` / `write_deliver_zip(full_for=…)` / `package_pending(full_for=…)`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-22-goalnn-offline-full-weights-backup

## §2026-10-22-goalnn-notebook-pack-full-wiring — 「中途取回」cell 接上整份：`package_pending_full` + 落地失败连续 3 轮停试（修订同日 §2026-10-22-goalnn-offline-full-weights-backup 的「代价 ④」）

- **决定**：
  1. **新模块 `remote/land_provider.py`（L2）** 是整份提供者的**唯一定义**：`provider()` 把 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-22-goalnn-notebook-pack-full-wiring

## §2026-10-22-course-batched-rollout-eval — 多课程排程：**按课分批 + 采集优先**（rollout 恒高于 eval，取代步级公平轮转）

- **决定**：
  1. **按课分批**：选中一门课后把它**本 it** 的任务连续跑完再换课（`Supervisor._last_course` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-22-course-batched-rollout-eval

## §2026-10-22-sibling-episode-reuse — 并行课共享「同一局」：到同族课程的工作目录里直取产物，**不做缓存**

- **决定**：
  1. **复用面 = 盘上已有的产物**（`nn-training/worker/sibling_reuse.py`），零新增存储：无缓存根、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-22-sibling-episode-reuse

## §2026-10-22-fixed-offpool-eval-seeds（2026-10-22，用户口径「in-loop eval 改固定池外种子、不轮换」）

- **决定**：A 层语料改为**固定池外池**（`worker/eval_track.py::EVAL_SEEDS_BASE = 2_000_000_001`，200 个；
  `2**30 < base < 2**31` ⇒ 与 rollout 抽签区间永不相交、且不碰 32 位有符号假设），且 `a_eval_seed_list` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-22-fixed-offpool-eval-seeds

## §2026-10-23-goalnn-anchor-full-rounds（2026-10-23，用户口径「首轮和 eval 轮发整份 weight 和 opt，其它轮发 delta」）

- **决定**：形态在**写盘那一刻**定（`remote/round_forms.RoundForms`，L2），判据唯一 = 「首轮 ∪
  `it % eval_every == 0`」（`deliver_wire.is_full_round`）。锚点轮：弹 `w_fmt`（worker 回整份）+ opt 落地成 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-23-goalnn-anchor-full-rounds

## §2026-10-23-goalnn-hold-stale-fallback（2026-10-23，用户口径「接管掉线应该是自动的」）

- **决定**：补两格自愈，且**降级只是一个正交标记**：`lapsed`（**仅** hub 显式 `state=="stale"`）⇒
  `local` + `fallback="hold_stale"`；`node_volume_conflict`（`node` ∧ `target_transitions>0`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-23-goalnn-hold-stale-fallback

## §2026-10-23-push-mode-notebooks（2026-10-23，用户口径「worker 注册一次，hub 主动来拉」）

- **决定**（本次落地 W0–W2；W3–W6 的语义也一并定下）：
  1. **能力键是唯一持久事实，`kind` 只是注册 wire 值**。`gpu_push` / `offline_push` 写进 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-23-push-mode-notebooks

## §2026-10-23-push-mode-outbox（2026-10-23，W3 落地：push 腿的本地 outbox）

- **决定**：
  1. **`--outbox` 与 `--no-deliver` 的叠合有明确语义**：「只关 HTTP 补传，outbox 必须活」。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-23-push-mode-outbox

## §2026-10-23-push-mode-w4（2026-10-23，W4 落地：离线 push 的 worker 侧）

- **决定**：
  1. **`push_mode` 住进 `run_one_course`，不另写一条训练链**（P1-9 的原始动机就是「别让 push 分支 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-23-push-mode-w4

## §2026-10-24-push-mode-w5（2026-10-24，W5 落地：hub 侧离线 push 派发器）

- **决定**：
  1. **登记表复用 `PushWorkers`，版本门挂在 `/state` 上（P1-7）**。子类只换 `capability="offline_push"`； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-24-push-mode-w5

## §2026-10-24-gpu-push-ahead（2026-10-24，用户指令「让 GPU push 允许在 worker 忙时预推下一份 job，利用 worker 侧现有接单队列消除传输空转」）

- **决定**：
  1. **预推只在「一个空闲机都没有」时发生**（`pick_push_worker(..., ahead=N)` 第二轮）：忙机若自报 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-24-gpu-push-ahead

## §2026-10-23-kickstart-v6-ref-cache（2026-10-23，y32-nohp / y32-tempo 两腿 it1 各 5/5 连炸后的修复）

- **决定**：① 头数判据用 `len(ref_out)`（**不**读模型属性：`DataParallel` 包装下属性代理不可靠），
  并与数据的 `a_hold` 是否存在**交叉校验**，不同源 ⇒ `ValueError`（含「不同源」字样）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-23-kickstart-v6-ref-cache

## §2026-10-23-kickstart-burn-scope（2026-10-23，两条改行为腿 18:06/18:23 双双被自己的熔断收官）

- **决定**：
  ① 缰绳不活跃（`kk ≤ NEGLIGIBLE_COEF`）⇒ **不判**，清空 streak 并说一次（`_rein_active`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-23-kickstart-burn-scope

## §2026-10-24-goalnn-offline-deliver-two-modes（2026-10-24，补传两档 + 漏轮统计身份）

- **决定**：
  1. 档位收敛为 `every`（≥36 KB/s，SAFETY 4/3）/ `anchors`（≥19 KB/s 或断连 ≥3/60 s）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-24-goalnn-offline-deliver-two-modes

## §2026-10-24-goalnn-offline-training-scale（2026-10-24，离线 notebook 训练放大）

- **决定**：新增两个**只在执行面生效**的倍数：`sample_mult`（每轮训练采样量）与
  `epoch_mult`（每轮 PPO 遍数），缺省 1.0 = 原样；notebook 非缺省才通过 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-24-goalnn-offline-training-scale

## §2026-10-24-goalnn-wire-gzip-mtime-determinism（2026-10-24，线上体去墙钟）

- **决定**：gzip 头的 `mtime` 固定为 0（`gzip.GzipFile(mtime=0)`——`gzip.compress` 的 `mtime`
  参数 3.11 才有，本包到 3.10）。mtime 是纯元数据，`gzip.decompress` 不读它 ⇒ 旧体/在途体照常解， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-24-goalnn-wire-gzip-mtime-determinism

## §2026-10-24-goalnn-backfeed-stage-off-temp-root（2026-10-24，回传落地不再扫系统临时区）

- **决定**：暂存区外面再套一层私有目录（`<hub-land-XXXX>/stage/…`）⇒ path 派生的两格只扫
  自己的暂存区；清理改删外层。生产 hub 的每轮 delta 回传同享这份收益。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-24-goalnn-backfeed-stage-off-temp-root

## §2026-10-24-push-mode-real-machine（2026-10-24，离线 push 真机首测：派发器没接开机路径）

- **决定**：① `--offline-push` **恒带**（`--push-config` 一并恒带，两条腿共用一份登记表）——
  无 `offline_push` 节点时调度拍是纯空转，不设开关；② 首拍注册收敛成 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-24-push-mode-real-machine

## §2026-10-24-push-mode-takeover-semantics（2026-10-24，接管四条口径：包就绪≠接管 / 一次一门 / 断线照跑 / 提议后过期重导）

- **决定**：`_dispatch` 只写 `phase=dispatching` 的 assignment（**不 claim**），`_pump` 在收到
  202（`duplicate` 亦然）之后调 `_claim`；claim 不到 ⇒ `cancel` 撤单但 `free_worker=False` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-24-push-mode-takeover-semantics

## §2026-10-25-offline-stall-age-zero-false-alarm（2026-10-25，「0 分钟没有新进度」是假警报：不可知 ≠ 0）

- **决定**：`anchor` 缺失 ⇒ 用**接管那一刻**算（`holder.age_sec`，静默时长的下界，真实可判）；
  控制台侧再兜一层：`ageSec <= 0` ⇒ 显示「（未知）」而不是「0 分钟」。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-offline-stall-age-zero-false-alarm

## §2026-10-25-progress-log-says-what-it-is-doing（2026-10-25，进度行说人话 + 停掉刷屏）

- **决定**：
  ① 新增 `common/progress_text.py`（**唯一实现**，hub 日志与云机日志共用）把一条事件翻成 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-progress-log-says-what-it-is-doing

## §2026-10-25-three-flakes-fixed-at-the-source（2026-10-25，修 flake：判据别等数量、别信 mtime、别把同步控制台腿当 10s 够用）

一天之内三处红都带着「随机」的味道，逐个挖到了根，都是**判据选错了事实**（不是偶发环境）：
1. **`e2e/test_auto_handoff_e2e.py::_http` 把超时吞成 `(0, {})`** ⇒ 现场 `assert 0 == 409`。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-three-flakes-fixed-at-the-source

## §2026-10-25-goalnn-bun-fmt（2026-10-25，用户指定换格式化器）

- **决定**：`bun run format` = `bun fmt`（根 + dashboard）；删两份 `oxfmt` devDependency 与
  `.oxfmtrc.json`，选项（含 `ignorePatterns = ["*.md", "dist", "node_modules"]`）迁进 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-goalnn-bun-fmt

## §2026-10-25-goalnn-bun-check（2026-10-25，用户指定换类型检查器）

- **决定**：`typecheck` / `build` / `check` 全改 `bun check --no-script`；两个 `package.json` 删掉
  `typescript` devDependency（hoisted + dashboard 各少一份锁条目）。**必须带 `--no-script`**：仓库有 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-goalnn-bun-check

## §2026-10-25-audit-p1p2p3-fixes-and-one-log-face（2026-10-25，审计三问的修 + 「日志只有一份措辞」）

审计见 `reports/y34-hitfix-push-103-105-audit-20261025.md`。用户指令：「三个问题 -> 修。另外，自主云机
不管 pull/push，其 rollout/ppo/eval 的输出日志应该是一致的，仅仅网络传输模块各自不同！！」 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-audit-p1p2p3-fixes-and-one-log-face

## §2026-10-25-push-node-enabled-false-silent（2026-10-25，注册成功但 hub 一个单不派：`enabled=false` 静默）

- **决定**：不改「云机不能自己顶掉停用」这条（那是控制台的所有权），改**可见性**：
  ① `hub/admin._nodes_ack` 的 200 体加 `enabled` / `dispatchable`； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-push-node-enabled-false-silent

## §2026-10-25-push-register-reenables（2026-10-25，push 云机**注册即启用**；人工停用仍有效）

- **决定**：`hub/admin._merge_managed_node` —— 既有 managed 条目在**本次注册声明了 push 角色
  （带 `kind`）**时，**无条件** `enabled = true`（覆盖人工停用）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-push-register-reenables

## §2026-10-25-stale-pack-waits-for-reexport（2026-10-25，注册到受理 163s：包「过期」是常态，别等控制台重导）

- **决定**：
  ① `_pack_for`：stale 时先取锚点 —— **有锚点就直接派**（打一行「用续跑锚点 itN 补齐，不等重导」）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-stale-pack-waits-for-reexport

## §2026-10-25-floor-it-off-by-one（2026-10-25，云机 it126 训完却「不回传」：`--floor-it` 高了一位，整轮被吞）

- **决定**：`resume_it` 缺省 = `max(0, pack_it - 1)`；`build_task_zip` 的 `floor` 同步改成
  `max(resume_it, pack_it - 1, 0)`（两处必须同一把尺子，否则一个算地板一个算 doc）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-25-floor-it-off-by-one

## §2026-10-26-result-ready-wakeup（2026-10-26，PPO 一出结果 hub 立刻来取：通知只叫醒，交付路径不变）

- **动机**：push 腿的结果由 hub 派发器的 `_await_result` **定时轮询**取回（`GET /job/{id}/result`，
  间隔 `PUSH_POLL_SEC`）。PPO 收口与下一拍之间那段纯等（最多一拍）没有任何进展，且它发生在 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-26-result-ready-wakeup

## §2026-10-26-goalnn-bun-native-sweep（2026-10-26，Bun 原生 API 落 `tools/` + `tests/`，并给开发回路加快道；实测：API 换用是口径统一，不是提速）

- **决定**：`tools/` + `tests/` 的 sha256 一律 `new Bun.CryptoHasher('sha256')`（18 文件 27 处，含
  `tools/probe-det-baseline.sh` 的 Windows 兜底内联）；新代码的树扫描用 `Bun.Glob`、文件读用 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-26-goalnn-bun-native-sweep

## §2026-10-26-goalnn-eval-local-fallback-only（2026-10-26，in-loop eval 的本机只做兜底）

- **决定**：① in-loop eval（per-tick A-eval / it0 基线 / 收官 drain / stream 旧路径）走
  `local_fallback_only=True`：只有 `len(nodes_ok) == 0`（无可用 eval 节点或全部权重 POST 失败） ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-26-goalnn-eval-local-fallback-only

## §2026-10-27-goalnn-paddle-ppo-twin（2026-10-27，AI Studio 只有 Paddle ⇒ PPO 双生，PoC 通过）

- **决定**：① 范围只承接 **PPO**（BC/rollout 不迁）；② 用「双生」而不是抽象层：新增
  `worker/models/student_paddle.py` / `worker/ppo/engine_paddle.py`，state_dict 与 `nn-weights-json` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-27-goalnn-paddle-ppo-twin

## §2026-10-27-goalnn-paddle-worker（2026-10-27，Paddle worker 接入 `kind=ppo`：格式统一 torch、hub 无感、可混跑）

- **决定**：① 新增 `common/torch_pickle.py`（无 torch 的 save/load zip 编解码）+ `worker/ppo/ckpt_convert.py`
  （纯 numpy 状态翻译）+ `worker/ppo/checkpoint_paddle.py`（torch 布局落盘）；Paddle 的 Adam 无 `step`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-27-goalnn-paddle-worker

## §2026-10-27-goalnn-stream-collect（2026-10-27，云机 rollout 改成**逐局流式采集**：采完一局就判差额最大的关，满额即停）

- **决定**：① 采集粒度换成**一局一拍**：新增 `worker/stream_collect.StreamCollector`（纯逻辑账本），
  判据 = `quota − collected` 最大的关先派，同额按 stage 升序；在飞的局按 est 折进预算（不叠派、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-27-goalnn-stream-collect

## §2026-10-27-goalnn-payload-zero-disk（2026-10-27，payload 零落盘：dispatch 腿收到就压，打包只剩拼接）

- **决定**：① `common.shard.write_shard(..., on_receive=)` 在**写盘之前**把 `(name, files, manifest)`
  交出去（挂「唯一落盘出口」而不是调用点 ⇒ 没有哪条写盘路能漏掉）；② `ShardSegments`（住 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-27-goalnn-payload-zero-disk

## §2026-10-27-goalnn-stage-rotation（2026-10-27，逐轮采样子集 + 关间轮换：模式 1/2 落地，模式 3 与 carry_over 后置）

- **决定**：① 三个**扁平标量**课程键 `rotation_mode`（`""`/`even`/`pinned`）· `rotation_per_it` · `rotation_pinned`
  （关号规格串，与 `--stages` 同一个 `parse_range`）——不引入嵌套模型（`flat_overrides` 只产出扁平 dest）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-27-goalnn-stage-rotation

## §2026-10-28-goalnn-stage-rotation-console（2026-10-28，窗口可观测性：账本读数 + 控制台上屏 + 课程前置校验）

- **决定**：① 账本新增 `rotation` 子字典（`{mode, per_it, pinned, all, window}`），
  `iteration` 与 `rollout_end` **激活时**都写（缺席 ⇒ 不写键，老行逐字节不变）；产出 = ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-28-goalnn-stage-rotation-console

## §2026-10-28-goalnn-cloud-stage-rotation（2026-10-28，云机 rollout 支持轮换 + notebook 三态开关）

- **决定**：① **配方进计划**（additive）：`plan["rotation"] = {mode, per_it, pinned}` + `volume.est_s_all`
  （全关 est 表，导出时刻钉住）；窗口键 = 计划既有 `rotate_seed`（与种子流同键）。② **唯一派生器** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-28-goalnn-cloud-stage-rotation

## §2026-10-28-goalnn-zero-disk-s3（2026-10-28，零落盘：sampler 回传的 shard **不落盘**，目录由内存账替代）

- **决定**：`common.shard.write_shard(..., materialize=False)` —— dispatch 腿**连目录都不建**。
  磁盘上的「目录」由三样内存账替代（`ShardSegments`）：`_parts`（已压好的段，打包原样拼接）· ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-28-goalnn-zero-disk-s3

## §2026-10-28-goalnn-course-env-override（2026-10-28，课程可覆盖敌数/命/星 + 从关卡挑关：不再为每门新课复制关卡 jsonc）

- **决定**：`"level"` 课程新增 `stage_select`（1-based **位置**串，顺序 = 新关序，id 仍 `2000+i`）/
  `enemies`（写回每关 `count`，`forces` 不动）/ `lives` / `star`（写回 `player`）；`max_ticks` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-28-goalnn-course-env-override

## §2026-10-28-goalnn-z2-ladder（2026-10-28，z0/z1 判死 → z2 阶梯：1 敌 1 命起，只加任务不加价）

- **决定**：新建 `curricula/z2-r1..r6` 六级阶梯（z0/z1 留档不续跑）。**唯一自变量 = 任务**：
  4 张预注册图（classic35 的 16/22/3/6：首敌 basic、无 water/ice、全连通、按 brick+steel 升序） ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-28-goalnn-z2-ladder

## §2026-10-30-notebook-userscript-w0（2026-10-30，第三个独立 bun 项目：userscript 骨架 + 根门禁边界）

- **决定**：`userscript/` 作为**第三个独立 bun 项目**；根侧显式排除，`tools/run-root-tests.ts` 追加第二个
  `--path-ignore-patterns=userscript/**`（Bun 1.4.4 实测：重复 flag 生效，**逗号分隔不生效**）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-30-notebook-userscript-w0

## §2026-10-30-notebook-userscript-w1-w5（2026-10-30，notebook 伴生脚本：服务端协议 v2 + frame 搬运 + 面板/桥）

- **决定**：① 协议 v2 用 `writer` 作 fencing token（每次页面加载随机）+ 单写者租约 + `epoch`；409 分支
  `writer-conflict` / `stale-epoch` / `offset-mismatch`（带 `{seq, chars, tail}` 续写），**不**按 writer 分槽； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-30-notebook-userscript-w1-w5

## §2026-10-29-pygate-wallclock（2026-10-29，pygate 墙钟：同一份扫描不许按 worker 重付 · 死端口须「当场被拒」）

- **决定**：① 判据共享**同一个**昂贵输入的用例合并成一条（断言各带消息；`reuse_ledger` / `lock`
  / `no_sleep`）；② 模块级重活改在**根 conftest `pytest_collection_finish`** 预热 —— forkdist 在 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-29-pygate-wallclock

## §2026-10-29-goalnn-slow-test-scan-scope（2026-10-29，三条 >5s 警告：只收窄用例成本，不动耗时预算口径）

- **决定**：只收窄用例自己的成本 —— (a) **用例扫全库时把扫描面钉到用例涉及的对象**（`test_sibling_reuse`
  的 autouse 夹具把 `sr.CURRICULA_DIR` 钉到本文件三门课：243 → 3，12 条用例 1.4~2.95s → 0.02s； ……

—— 全文（读数表 / 三条各自的账 / 顺带发现的 `_Indexer.visit_With` 判据盲区）→ `docs/nn/engineering.md` §92

## §2026-10-30-goalnn-top30-durations-round（2026-10-30，用户指令「再跑一轮前 30 条最慢用例的耗时清算」）

- **决定**：(a) 扫描实现只做**字节等价**提速（`_shape` 单趟直书 + `_split_lines` 正则切行；全仓 344 文件 /
  3824 函数四元组改前改后对账 **0 差异**，同进程交错 A/B −20~36%）；(b) torch 首次算子按 **≥3 判据**进 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-30-goalnn-top30-durations-round

## §2026-10-30-notebook-userscript-multi-page（2026-10-30，多开 Kaggle 页签：日志键要解码 + 每页一份会话名）

- **决定**：① 路径键先过 `decodeLogKey(pathname, prefix)`（畸形 `%` 原样返回，让查表去 404），
  `logPageRoute` / `apiLogRoute` 两条同改，客户端编码不变；② frame 侧给会话名加**页面标签** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-30-notebook-userscript-multi-page

## §2026-10-30-notebook-scheduler-w4（2026-10-30，notebook 伴生脚本 W4 接线：定时启停 / 执行 cell）

- **决定**：① 计时=**墙钟轮询**（20s + `visibilitychange` 立即补一拍），不建 setTimeout 链；迟到窗口按
  动作分（`run-*` 10 分钟、`stop/interrupt` = `config.stopGraceMs`）；② occurrence claim 落 GM ……

—— 全文（方法 · 满载/空载读数表 · 三类分类 · 两处 A/B 明细）→ `docs/nn/engineering.md` §93

## §2026-10-31-notebook-colab-single（2026-10-31，notebook 伴生脚本接 Colab：采样先于实现 + single 角色 + fail-closed）

- **决定**：
  1. **采样先于实现**：从存档抽出 `#top-toolbar` + `#notebook-main` 两棵子树入库为 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-single

## §2026-10-31-notebook-aistudio（2026-10-31，notebook 伴生脚本接 AI Studio：复用 JupyterLab 适配器 + 只认 notebook 帧）

- **决定**：
  1. **复用而非重写**：notebook 帧的 `.jp-Cell` / `.jp-InputArea` / `.jp-OutputArea` / 折叠器 / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-aistudio

## §2026-10-31-notebook-colab-stop（2026-10-31，Colab「停会话」改走中断：只断开不删文件没有入口）

- **决定**：`stopSession` = **中断当前执行**（`notebook:interrupt-kernel`；菜单项按实测属性
  `md-menu-item[command="interrupt"]` 定位）。仍**绝不**点「Disconnect and delete runtime」；成功时 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-stop

## §2026-10-31-notebook-colab-powerwash（2026-10-31，Colab「停会话」= 关运行时；推翻同日的「只中断」口径）

- **决定**：`stopSession` = 菜单栏 **Runtime →「Disconnect and delete runtime」**
  （实测 `command="powerwash-current-vm"`，在 `#runtime-menu` 里；工具栏「更多操作」没有这一项）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-powerwash

## §2026-10-31-notebook-stop-release（2026-10-31，停会话＝释放运行时：Kaggle 真实停止 / AI Studio 关内核 / 历史写明删了运行时文件）

- **决定**：① **Kaggle 不变**——`[aria-label="Stop session"]` → 处理确认框 → 轮询到 `Stopped` 才算成功
  （Kaggle 的 Stop session 结束会话、释放机器；既有用例已覆盖「状态不变必须失败」）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-stop-release

## §2026-10-31-notebook-colab-menu-press（2026-10-31，Colab 关运行时实机失败：菜单靠 mousedown 展开，旧实现只派发 click）

- **决定**：停会话改用真实三连 `clickAt`（mousedown/mouseup/click）。点完先等菜单**真的展开**
  （菜单项不再隐藏，或按钮 `aria-expanded="true"`），2s 内没开就再按一次然后**快速失败**（绝不点隐藏项）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-menu-press

## §2026-10-31-notebook-colab-hover-slot（2026-10-31，补充 §…-colab-menu-press：菜单项要先 hover 激活；确认键是 slot=primaryAction）

- **决定**：菜单项改用 `clickMenuEntry`（`mouseover → mousemove → mousedown → mouseup → click`，点最里层）；
  确认框改为「先找 `[slot="primaryAction"]`，找不到再按文案兜底」，只在标题匹配 disconnect/delete runtime 的弹框里点， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-hover-slot

## §2026-10-31-notebook-colab-dialog-scope（2026-10-31，确认键改为「限定当前弹框 + 点后验证关闭 + 候选重试」）

- **决定**：① `openDialog()` 只认「打开着的断运行时弹框」（role=alertdialog / `.mdc-dialog--open` / md-dialog，
  标题或 aria-label 匹配，`displayHidden` 判可见，`open === false` 算关闭）；② 候选按「当前弹框宿主 → 整个文档的 slot → ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-dialog-scope

## §2026-10-31-notebook-colab-dialog-candidates（2026-10-31，确认键候选扩到「任意叶子文案」+ 命中测试兜底 + 失败带诊断）

- **决定**：① 候选扩三层：当前弹框宿主 / 文档里的 `[slot="primaryAction"]` → 已知按钮标签（含 md-filled-tonal-button /
  md-outlined-button / mwc-button / paper-button / colab-button）→ **任意可见叶子元素的短文案**（Yes/OK/确定/删除/断开…， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-dialog-candidates

## §2026-10-31-notebook-colab-composed-click（2026-10-31，根因：合成事件缺 `composed`，点击穿不出影子边界）

- **决定**：`clickAt` 与 `clickMenuEntry` 的事件初始化一律加 `composed: true`；同时删掉上一轮「点完内层再点宿主」的兜底
  （composed 修好后内层点击会以宿主为 target 冒泡上去，再点一次反而会重复触发）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-composed-click

## §2026-10-31-notebook-colab-dialog-fixture（2026-10-31，夹具换成存档里的真实 mwc-dialog 结构）

- **决定**：从 `tmp/colab-delete-runtime.mhtml`（不入库）抽 `<mwc-dialog class="yes-no-dialog">` 整棵子树：
  去注释、压标签间空白、`open=""` 换成 `hidden=""`（测试里 `removeAttribute('hidden')` 打开）。夹具只增约 2KB； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-dialog-fixture

## §2026-10-31-notebook-colab-connect-wording（2026-10-31，Colab 断开文案补「Click to connect / Reconnect」）

- **决定**：stopped 判据加入 `click to connect` 与 `\breconnect\b`。用词边界是故意的：`Reconnecting` 不能算断开
  （机器可能正在重新拉起，算了就是谎报省配额）；资源文案（backend / RAM / Disk）仍判 running，认不出的继续 unknown。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-10-31-notebook-colab-connect-wording

## §2026-11-01-metrics-v13-base-threat（metrics v13：基地威胁四轴 6 列 + eval 探针四计数器 + N9 口径更正）

- **决定**：
  1. `METRICS_VERSION 12→13`、行宽 **79→85**，尾部追加 `thBreach / thBreachAimed / thDirect / thDirectAimed / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-01-metrics-v13-base-threat

## §2026-11-01-eval-midround-self-rescue（eval 轮中自救：本机中途兜底 + 软失败停派回场）

- **决定**（`trainer/eval_dispatch.py` 主循环 + `worker/eval_yield.py` 三个纯函数）：
  1. **轮中本机兜底**：消费者归零且队列还有活时，兜底模式按 `local_fallback_midround` 再孵一次本机 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-01-eval-midround-self-rescue

## §2026-11-01-goalnn-admission-queue-and-eval-yield（准入池不再 503 立刻拒：节点有界排队 + eval 让出在飞槽）

- **决定**：
  1. **节点侧有界排队**（`tools/agent/admission-queue.ts`，`sampler-agent` 唯一实例）：池满时进有界 FIFO ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-01-goalnn-admission-queue-and-eval-yield

## §2026-11-01-goalnn-r5-e2a-dist-enemy-ch16（2026-11-01，E2-A 单通道版：`OBS_CHANNELS 16→17` / `OBS_SCHEMA_MAJOR 7` / `ch16 distEnemy`；ch17 与 s46 本波不做）

- **决定**：本波只加 `ch16`（单变量纪律），`ch17` / `s46 bestPickupValue` 留 E2-B 另案；
  `SCALAR_DIM` 保持 46。语义护栏：最近敌 ≠ 距基地最近敌 29.0%（2002 36.2%）⇒ ch16 是 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-01-goalnn-r5-e2a-dist-enemy-ch16

## §2026-11-01-goalnn-course-formula-lines（2026-11-01，reward.formula 支持行数组；242 门课重排为多行）

**决策**：`reward.formula` 接受 `str | list[str]`。列表在**加载层**
（`biz/course_spec.py::RewardBlock._formula_lines`）收敛成 `"(" + "\n".join(项) + ")"`， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-01-goalnn-course-formula-lines

## §2026-11-01-goalnn-paddle-parity-v7-noise（Paddle 三端对账在 v7 后补绿：少量 v≈0 坐标的单步离群 + value 头的绝对/相对缝）

- **决定**：① `check` 的权重判据加**少量坐标离群**窄缝：超 `atol_weights` 的元素 **≤8**、
  最大 ≤**2·lr**、均值 ≤atol/10；满足时更新后前向放宽到 `atol_after + 2·d_w`； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-01-goalnn-paddle-parity-v7-noise

## §2026-11-01-goalnn-obs-v7-native-kernels-19ch（v7 观测把 TS 卷积内核 18→19 通道：修静默算错 + ABI 2 + 重编六平台 prebuilt）

- **决定**：`CF_IN_CH 18→19`（17 obs + 2 coord）· `CF_ABI 1→2` · `NATIVE_ABI=2` · wasm 适配器
  `ABI=2` · `native-build.ts --cross` 重编 6 平台 + wasm32（`--check-prebuilt` 绿）。wasm 入口 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-01-goalnn-obs-v7-native-kernels-19ch

## §2026-11-01-goalnn-z4-nav-rename-and-polish（z2-r5nav→z4-nav；新增并行打磨臂 z4-polish：只动 reward.params 六个数 / 五轴）

- **改名**：`z2-r5nav` → `z4-nav`（v7 纪元命名；课程配置逐字未动），起点文件同批改名并搬家：
  `nn-training/weights/z4-nav/z4-nav.it0.20261101-230656.json`（gitignored，本地存在）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-01-goalnn-z4-nav-rename-and-polish

## §2026-11-01-goalnn-z4-budget-eval-cadence（z4 两课预算 160→320、eval_every 25→10；it150 仍是唯一主判）

- **决定**：`z4-nav` 与 `z4-polish` 同步改为 `iters 320` / `max_hours 10.0` / `eval_every 10`
  （0,10,…,320 共 33 个点）。**it150 仍是唯一预注册主判**（与 `z3-p2002ctl` 的 800 局配对）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-01-goalnn-z4-budget-eval-cadence

## §2026-11-02-bnc-schedule-per-page-and-ops-log（定时任务按页分桶：别的 notebook 页面再也吃不掉你的任务；userscript 操作全量落 dashboard 操作日志）

- **决定**：① 任务表改**按页分桶** `bnc:tasks:<notebookKey>#<page>`（page = boot 的 pageTag，
  sessionStorage 跨刷新稳定、页签之间必然不同）⇒ 「每个页面只看得见本页面设置的任务」； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-02-bnc-schedule-per-page-and-ops-log

## §2026-11-02-bnc-run-cell-postcondition-retry（定时 run-cell「点了」不算执行：加执行后置条件 + 迟到窗口内重试）

- **决定**：① `runCell` 点击后必须有执行证据（cell 的 `[*]` 提示符 / `jp-mod-executing` / 提示符变号 /
  输出变化），且排队证据要**持续 250ms**（iframe 重载会清掉一闪而过的 `[*]`），2.5s 内看不到 ⇒ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-02-bnc-run-cell-postcondition-retry

## §2026-11-02-console-residual-hp-per-life（控制台「残血」口径订正：按单命满血归一 + 剩余命按满额计入，三命关胜局 200%+）

- **决定**：`残血 hp = (剩余命数 − 1) × 满血 + 当前命 hp`，**% = hp / 单命满血**（1 = 满一条命；
  三命全在 = 3）；`剩余命数 = startLives + puGotTank − playerDeaths`（胜局下限 1）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-02-console-residual-hp-per-life

## §2026-11-02-console-pill-word-grammar（在训 pill 状态词统一为 `环节·归属`：认领/开算/在算三件事分开，「推进中」「等回传」退役）

- **决定**：状态词统一 **`环节·归属`** 语法；`course-status.ts::PILL_WORD_GUIDE` 是唯一词表说明
  （pill 组首 `?` 悬停 = 文档同句）。PPO 环节按「发出去了没有 → 谁领了 → 开算没有」递进： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-02-console-pill-word-grammar

## §2026-11-03-goalnn-rollout-first-eval-queue（rollout 绝对优先、eval 全局排队吃间隙：进程级 eval 闸 + 让位去 floor + 窗口从权重就绪起算）

- **决定**：① 进程级 `worker/eval_gate.py`（FIFO 单飞 + rollout 插队 + 队头续跑 + 每轮新令牌）；
  ② `eval_slot_cap` 去 floor（`max(0, concurrency − inflight)`，由 `_grow_slots` 在间隙补线程）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-03-goalnn-rollout-first-eval-queue

## §2026-11-03-goalnn-eval-rows-stratified（云机评估行回传 cap：整轮放行 + 按关分层；聚合胜率本就不受影响）

- **决定**：`deliver_parts.eval_rows_cap(n_rows, hard=EVAL_ROWS_HARD)` = `max(400, min(n, 3000))`
  ⇒ 完整一轮不裁；超过 3000 行才 `select_eval_rows`：同键 `(iter, wver, stage, seed)` 取最后一条 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-03-goalnn-eval-rows-stratified

## §2026-11-03-goalnn-eval-analysis-aggregate（分析列全量聚合成一轮一行 `eval_agg` 随补传体回）

- **口径**（用户 2026-11-03）：逐局仍走白名单；另把**分析列全量**聚合，每个 it 传回 hub 一行。
- **行**：`{"event":"eval_agg","iter","wver","time","games","stages","nodes","analysis"}`； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-03-goalnn-eval-analysis-aggregate

## §2026-11-03-goalnn-wall-press-two-legs（撞墙两腿：v6 只掩 advance、wallPressTicks 入 METRICS；版本 13→15）

- **决定**：① 新增 `restrictAdvanceMask`（复用 `corridorCells(cap=1)`，与 `isWallPressTick` 的前脸
  条件同源）；在 RL rollout 的 L0/God 覆盖后、eval 的 goal 偏置后、`NNInput` 的 move argmax 后调用。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-03-goalnn-wall-press-two-legs

## §2026-11-03-goalnn-root-gate-tmp（根门禁排除 `tmp/**`：A/B worktree 不拖红干净 checkout）

- **决定**：`tools/run-root-tests.ts` 增加 `--path-ignore-patterns=tmp/**`；由
  `tests/test-silent-scope.test.ts` 同时钉住 `dashboard/**`、`userscript/**`、`tmp/**` 三个排除项。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-03-goalnn-root-gate-tmp

## §2026-11-03-goalnn-pickup-path-dist（`pickupDist` 原地改 BFS 路径距离；版本 15→16）

- **决定**：① **原地改** `nearestPickupDist`（列号/列名/行宽 90 全不变，不加新列）：4 邻接 BFS，
  「格可走」= **2×2 车身能覆盖它**（`utils/grid-search.ts::isPassable`，与 A\* / 关卡生成器同一份 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-03-goalnn-pickup-path-dist

## §2026-11-03-goalnn-pickup-bfs-dist（`pickupDist`(40) 改 BFS 路径距离：曼哈顿把墙后道具读成「就在隔壁」）

- **口径**：metrics idx40 `pickupDist` 由「最近存活拾取中心格的曼哈顿距」改为**BFS 路径距离**
  （玩家中心格 → 拾取中心格；格可走 = **2×2 车身能覆盖**，`utils/grid-search.ts::isPassable`： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-03-goalnn-pickup-bfs-dist

## §2026-11-04-goalnn-eval-gate-two-concurrent（eval 进程闸并发上限 1→2：三腿产能赤字，用户拍板；旧缺口留收官 drain）

- **决定**：`worker/eval_gate.py::MAX_CONCURRENT = 2`（`EvalGate(max_concurrent=…)` 可注入）：
  开跑条件 = 无 rollout ∧ 在跑数 < 2 ∧ 票在队列前 2 位（FIFO 决定谁先拿空槽，空槽按序补位）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-04-goalnn-eval-gate-two-concurrent

## §2026-11-04-goalnn-task-level-eval-arbitration（eval 任务级统一派发：多轮平等发牌 + 跨轮副本平铺 + rollout 只冻结新投递）

- **决定**：① 进程级 `worker/eval_arbiter.py`：轮只登记上下文（权重 / `eval_iter_id` /
  `course_fp` / 快照 / 超时 / 队列）+ 冻结感知 `RoundClock`；执行者 `take(node)` 领下一局 = ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-04-goalnn-task-level-eval-arbitration

## §2026-11-04-goalnn-eval-freeze-per-course-and-round-cap（eval 冻结按课程 + 本课单开轮上限）

- **决定**：① 冻结按课程作用域（`freeze/thaw/frozen_for(course_fp)`，`None` = 进程级），
  `take()` 过滤冻结课程的轮再发牌；采集侧 `step_rollout` 传 `course_fp_for_args(args)`。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-04-goalnn-eval-freeze-per-course-and-round-cap

## §2026-11-04-goalnn-cloud-pool-cross-round（云机 serve pool 跨轮保留 + 采集 α 单腿）

- **决定**：rollout/eval 的 `ServePool` 从「每轮建、轮末关」改为**同进程跨轮保留**；key 用
  `ts_code_sha256`（不目录路径），`take/put` 显式转移所有权，`begin_round()` 每轮清零读数/熔断 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-04-goalnn-cloud-pool-cross-round

## §2026-11-04-goalnn-alpha-default-sched（采集默认档改 alpha，quota 降为显式回退）

- **决定**：`volume.sched` 缺省/无键按 `alpha`；`worker/cli.py` 默认 `alpha`；计划导出缺省写
  `"alpha"`，显式 `quota` 也必须落键。alpha 不再只做实验轮，但 quota/alpha 切换仍必须新 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-04-goalnn-alpha-default-sched

## §2026-11-04-goalnn-trend-hides-unsettled-eval（趋势图不画 eval 未结束的轮；指标表照旧）

- **决定**：`IterRow.evalInFlight` = `eval_log` 已有该 it 的逐局行 ∧ 无 `eval_summary`
  （`iters.buildIterRows`，两个 map 本来就在手边）；`view/series.ts::metricSeries` 先过滤 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-04-goalnn-trend-hides-unsettled-eval

## §2026-11-04-goalnn-bc-epoch-backfill（BC epoch 回传：隧道 530 丢包后重放 + hub 按 epoch 幂等 + 迟到行不派 eval）

- **决定**：① `remote/bc_job.py` 的 `_bc_post_epoch` 返回 bool，失败 body 进进程内 backlog
  （上限 8，超限丢最老并告警），下一次回传成功后与训练收尾时重放（仍失败即停，不阻塞训练）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-04-goalnn-bc-epoch-backfill

## §2026-11-05-goalnn-bc-action-v6-single-source（BC 动作空间单源 + 语料指纹条件键）

- **决定**：`BcCourseConfig.action_v6` 顶层（`None` = 回退已废弃的 `train.action_v6`）；顶层与
  显式 train 值冲突 ⇒ 加载报错。`bc_corpus_identity_fp` **仅 `resolved_action_v6=True`** 时加入 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-05-goalnn-bc-action-v6-single-source

## §2026-11-05-goalnn-bc-v6-collect（v6 BC 采集：label_policy 权重、bc-v6 mode、能力位）

- **决定**：`corpus.label_policy` 是 v6 采集的标签权重路径；`label_policy_fp=sha256(file)` 只进
  v6 语料指纹，`resolved_action_v6 && games_per_stage>0` 必填。`bc_dispatch` 先校验权重是三头 v6， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-05-goalnn-bc-v6-collect

## §2026-11-05-goalnn-bc-resume-reopen（BC 课程级 resume 索引 + 预算扩大重开最后完成轮）

- **决定**：已完成轮在课程目标 epoch 变大时写一次 `bc_round_reopened{it, old_epochs,
  new_epochs}` 并发布新 jid。新 jid 的 resume 在自身文件缺失时回读课程级索引 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-05-goalnn-bc-resume-reopen

## §2026-11-05-goalnn-bc-round-job-reconcile（BC 盘上 job：隐藏目录 + hub 对账 + 缺失记账）

- **决定**：`find_round_job` 与 `prune_remote_jobs` 跳过 `.` 开头目录（不计数、不删除）。hub
  传输认领前用 `job_reconcile_state` 对账：404=`unknown`、200+failed/frozen/cancelled=`closed` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-05-goalnn-bc-round-job-reconcile

## §2026-11-06-goalnn-bc-loss-retention（BC 败局留用：课程配置目标占比 + 固定语料原样）

- **决定**：`corpus.source=collect|fixed`。collect 的 `ratio` 为 `target{win,loss,timeout}`
  目标占比（和=1、win>0、只裁不补、胜局不裁，`win` 是下限；按关卡默认；不可行记 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-06-goalnn-bc-loss-retention

## §2026-11-06-goalnn-bc-code-snapshot-freshness（BC 发布：快照过期只告警 + 控制台重打）

- **决定**：BC 发布在快照来源为会话快照时，按 `pack_code_zip` 同规则算工作区 sha；不一致写 WARN
  但不拒发，job 目录写 `code-freshness.json`（snapshot/workspace/stale/commit/ts）。面板显示最近 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-06-goalnn-bc-code-snapshot-freshness

## §2026-11-06-goalnn-bc-paddle-backend（BC 的 Paddle 双生：纯 Paddle 机也能跑 BC 课）

- **决定**：新增 `worker/train/bc_paddle.py`（只支持 `--arch student`；v6 三头 / `policy_extra` /
  `hold_acc` / 续训 / 早停 / class weight 与 `bc.py` 同语义）；纯判据上移 `bc_core.py` 两边共用。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-06-goalnn-bc-paddle-backend

## §2026-11-07-goalnn-iters-hot-reload-live（课程 `iters` 热更：判据活读 runner，收官后仍需停→开）

- **决定**：`LoopRunner._budget_iters()` 每拍取值：`self.iters > 0`（serve 级 `--iters` 显式覆盖）
  冻结；否则活读 `loop.args.iters`（0 = 不限）。`planner` 判满与 `finish_reason` 同走它。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-07-goalnn-iters-hot-reload-live

## §2026-11-07-goalnn-dead-lease-sweep（死租约周期清扫：`peek_jobs` 每拍收，回收只走既有入口）

- **决定**：`LeaseMixin.reap_dead_leases()` 持 `_lock` 一次枚举死租约并逐个走
  `_collect_expired_locked`（判据/计数/账本/冻结全复用）；`QueueClaimsMixin.sweep_dead_leases()` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-07-goalnn-dead-lease-sweep

## §2026-11-07-goalnn-rollout-tunnel-url-retry（空隧道 URL 不注册 + cloudflared 三段自救）

- **决定**：`_try_register_once()` 对空 URL 返回 `kind="nourl"` / `capabilities: []`，零 HTTP、
  不写 `reg_state`；`_banner_lines()` 空 URL 时不再打印必 400 的 `nodes[]` 片段。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-07-goalnn-rollout-tunnel-url-retry

## §2026-11-07-goalnn-c8-seed-level（C8：统计单位 = `(stage, seed)`；z11 旧 p 降为上界，run-average 与 endpoint 分开报）

- **决定**：统计单位 = `(stage, seed)`；窗口 = 所有比较腿都完整的 iter 交集；主读数 =
  每 seed 窗口均值 + 配对 sign/bootstrap，副读数 = 末个完整 iter 的逐局 win + McNemar； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-07-goalnn-c8-seed-level

## §2026-11-07-goalnn-c9-critic-calibration（C9：it233 critic 压缩/偏置；value-only = 冻结策略只训 value_head）

- **决定**：`--policy-coef` 默认 1.0（历史行为）；0 时在建 optimizer **之前**冻结除
  `value_head.*` 外全部参数，optimizer 只收 value 头，且要求 `--arch student` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-07-goalnn-c9-critic-calibration

## §2026-11-07-goalnn-stop-course-lock-prune（停课清本课锁 + serve 跑课前重取闸）

- **决定**：`stopCourse` 调 `pruneStoppedCourseLocks`——持有者已死/不可解析 ⇒ 删；持有者 ==
  共享 trainer（`run_cluster` 持有人）⇒ 删；**别的活进程**持有 ⇒ 一个字不动（fail-closed， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-07-goalnn-stop-course-lock-prune

## §2026-11-07-goalnn-w1-thcost-negative（P3-A 判负：事件当拍价动得了机制量，守家反而变差）

- **决定**：用户 19:46 拍板跑到 it100 补齐三窗，**判词以三窗为准，不提前定**。机制量用 it50 归档权重的 200 局/腿只读 rollout 探针（`--seeds 0-49`），同 `(stage,seed)` 配对，总量与**每决策步速率**两个口径都报。
- **结果（判负）**：三窗 Δwin it20 **+7.0pp**(p=.0087) → it50 **0.0**(p=1) → it100 **−9.5pp**(McNemar 50:88, χ²=10.46, **p=.0012**)；it100 Δbase **+9.25pp**、Δkills −1.65、Δcells −9.4。run-average（21 点/400 seed）Δ **+0.19pp**、CI [−1.04,+1.38] 跨零 ⇒ 全程无效应，早期优势是收敛速度红利（两腿 win 都从 .1825 爬到 .2500，null 在 it50 追平、it100 反超）。判据 ②（base −8pp）未过且反向、③ 地板击穿。**机制量**：ΣthCost 总量 4/4 关降（Δ −10.19，CI [−18.73,−1.89] 不跨零）按原口径通过，但速率口径仅 2/4 关、CI 跨零 ⇒ 下降主要来自**局更短**（samples −5.5%），不是每拍威胁率下降。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-07-goalnn-w1-thcost-negative

## §2026-11-07-goalnn-w1-valueonly（C9 预热落地：冻结策略只训 value 头，尺度修好、排序没修）

- **决定**：落 `nn-training/curricula/w1-valueonly.bc.jsonc`（`source=fixed` 指向 `tmp/p0-c9/rollouts`、
  `init_from` 与 `bc` 同指 z10.it233、`value_coef 1` / `policy_coef 0` / 5 epoch / eval 关闭）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-07-goalnn-w1-valueonly

## §2026-11-07-goalnn-w1-valueonly-ppo（C9 闸关闭：没崩，但「尺度对、排序错」的 critic 让守家显著变差）

- **决定**：`w1-valueonly-ppo.jsonc`，`bc=w1-valueonly.ep5`、`ent_coef 0.01`、`iters 20`、
  reward / lr / kl / gamma / lam / K / tt 逐字 = z11-null；判据预注册为 it20 `pick≥.40` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-07-goalnn-w1-valueonly-ppo

## §2026-11-08-goalnn-bc-ledger-fixed-land（BC 账本末轮 flush + fixed 源落位口径 + 拒收响亮化）

- **决定**：① READY/非冒烟分支审计前 flush 一次指标（幂等游标）；② 落位校验新增
  `data_root` / `fixed_source`，与发布端共用 fixed 根 + `*_s*_seed*` + D14 豁免；③ 落位失败写 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-08-goalnn-bc-ledger-fixed-land

## §2026-11-08-goalnn-w3-nofreeze-negative（z8 崩因判清：idle 罚无罪，bc-v6 起点有罪）

- **背景**：`z8-noidle` 在 0 冻死 11 关（`classic35-safe-base`）判负，四条根因候选：① `-wIdle` 不封顶
  ② 冷 value 头 ③ KL 三缺一 ④ 起点熵 .9123。本课把 ① 单独拿掉（reward = z11-null 逐字、无 idle 罚、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-08-goalnn-w3-nofreeze-negative

## §2026-11-08-goalnn-w3-ramparts-negative（单关 100% 曝光 50 轮零改善：曝光假设否证）

- **决定**：判在 **it50**（预算 1/4），预注册三门：20 杀率 ≥ .50 / 平均拍数 ≤ 12,000 / idle ≤ 6,000。
- **结果（三门全未达成）**：20 杀率 it0 **.35** → it50 **.34**；拍数 16,487 → **15,741**（只 −4.5%）； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-08-goalnn-w3-ramparts-negative

## §2026-11-08-goalnn-rollout-tunnel-silent-death（隧道静默死：判死归一 + 探针姿势，不加 force 重注册）

- **决定**：cloudflared 通道新增判死纯函数 `_tunnel_dead`——进程退出是确定性证据（每拍）；
  进程活着时要求「本地 agent 健康 + 公网 `/v1/ping` 连续 3 次失败（60s 节拍）」。判死后 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-08-goalnn-rollout-tunnel-silent-death

## §2026-11-08-goalnn-eval-metric-columns（eval 账本补全 27 列；切片 5 收掉豁免，`METRICS` 过桥）

- **决定**：补齐逻辑住 `worker/eval_rows.py::eval_extra_fields`，挂进 `eval_metric_fields`
  ⇒ `eval_row` 与 `batch_runner.record` 两个行构造点同时生效；`clearTick` 只统一**哨兵** ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-08-goalnn-eval-metric-columns

## §2026-10-19-godai-a2-committed-facing-fire-gate — A2 提交朝向开火门：God-AI 不再把 `_moveDir` 当子弹方向，回收 A1 缺口的 65%

- **决策（方案 A，已实施）**：
  1. 新增纯谓词 `turnWillCommit(w, p, dir)`（`src/ai/god/turn-gate.ts`）：**本拍子弹会不会沿 `dir` 出膛**。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-19-godai-a2-committed-facing-fire-gate

## §2026-10-19-godai-a3-turn-economy — A3 转身经济：不请求拿不到的转身（引擎会因此把坦克冻住），胜率 74%→79%

- **取数先行**（hard，8 关 × 3 seed，2026-10-19）：God-AI 每局接受 **240 次转身**，其中 **95.1% 在 12 拍内
  没换来任何一发**（转身是买走位的，但价款用「瞄准锁定」付）；更关键的是 **17.7% 的全部 tick** 在请求一个 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-19-godai-a3-turn-economy

## §2026-10-19-godai-audit-hard-700 — 700 局审计：瓶颈画像 + 四个方向的 A/B 全部未发货（负面结果登记）

- **结局构成**（hard 35×20 = 700 局，出厂参数，79.7% clear）：**丢基地 128 / 命耗尽 14** ⇒
  90% 的失败是基地失守，自身生存不是瓶颈。丢基地时玩家距基地 **p50 9 格 · p90 20 格**； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-19-godai-audit-hard-700

## §2026-10-20-godai-crosscheck-other-lineage — 与外部研究（godai-tuning-summary-20261020）的交叉核对

- **先核验血缘**：该文档引用的 7 个提交（bbf59ac / ceeb5d4 / 7e43987 / 79968d6 / 97a9402 /
  06acc5e / a139c68）**在本库全部不存在**，`campaign/god-tune` 也不在 origin 分支列表内 ⇒ ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-20-godai-crosscheck-other-lineage

## §2026-10-20-godai-mechfix-carvefire — 机制修复（非调参）：carveFire 的钢墙过度否决 + 道具候选不破砖

> 用户政策（2026-10-20）：「机制修复类，就算胜率下降也要做，做完研究为什么下降，再针对原因调优」。
- **缺陷 1 · carveFire 拿整条射线的钢否决眼前的砖**：`bs.steel` 表示**射线某处**有钢。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-20-godai-mechfix-carvefire

## §2026-10-20-godai-mechfix-tankwall — 坦克墙（机制修复，已发货）+ planCell 与 dig-compare 的核查结论

> 承接 §2026-10-20-godai-mechfix-carvefire 的机制修复清单（#1 坦克墙 / #2 planCell / #3 dig-compare）。
- **#1 坦克墙（SHIPPED，`navEnemyCellCost=2`）**：A* 只算地形，**完全不知道敌车存在** ⇒ 路径 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-20-godai-mechfix-tankwall

## §2026-10-20-godai-mechfix-fire-admission — 开火策略准入（按用户定规实现，实测代价大 ⇒ 默认 OFF）

- **决定**：代码保留（一个参数即可切档），**默认 OFF**；是否作为政策强制由用户决定。
  若要强制，建议只在小图/砖关启用（Oasis 这类开阔图显式豁免），需按 stage 属性判定。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-20-godai-mechfix-fire-admission

## §2026-10-21-godai-a7-dm-fallback — directMove 的 any-passable fallback：承诺逃逸（机制实现保留，默认 OFF）

**结论先行**：`directMove` 的 any-passable fallback **确实**在背离目标的方向上走出
12% 的 directMove tick，但它**没有第二个选项可选** —— 那里只有「背离主轴」与「背离副轴」 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-21-godai-a7-dm-fallback

## §2026-10-21-godai-a8-defense-cell — 集合点可站化：（12,23）在 35/35 关都是环砖，回防路由从来没通过

**结论先行**：`getDefaultDefensePosition()` 返回 `(BASE_POS.col, BASE_POS.row − defenseRowOffset)`
= **(12,23)**。实测 **35/35 关该格都是基地环砖**，`findPath` 在 **corridor 与 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-21-godai-a8-defense-cell

## §2026-10-21-godai-a9-base-lane-seal — 远程来袭弹的提前判线 + 封线（SHIPPED，classic restore 0）

**结论先行**：700 局审计说 90% 的失败是丢基地、致命弹来自半张图外的顺线点名（down 40% /
left 32% / right 28%，丢基地时玩家 p50 距基地 9 格、6 格内平均 0.23 个敌人）。原有的 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-21-godai-a9-base-lane-seal

## §2026-10-21-godai-a9-next-directions — A9 之后的三条候选方向：全部核验完毕（2 负 1 无杠杆）

**结论先行**：三条候选都做了实测。① 横向车道封线 **−1.0pp（负向，已回滚）**；
② 封线距离 14→10 **+0.57pp（n=700，噪声内，不发货）**，自适应 baseHp 版本未做； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-21-godai-a9-next-directions

## §2026-10-21-godai-a10-lead-fire — 预判开火（lead fire）：实装 + 实测负向 ⇒ 默认 OFF

**结论先行**：God AI **确实没有**这条能力（开火门 `shouldFireInDirImpl` → `scanAheadImpl` 只扫
当前几何，全 `src/ai/god` 无一处用 `t.vx/vy` 外推落点）。本轮把它实装成与 NN 观测 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-21-godai-a10-lead-fire

## §2026-10-27-godai-a28-fix — 失败思路复审：A28 的两次实现错误（修好 +2.24pp，SHIPPED）+ 12 条同批复核

**结论先行**：上一轮把 A28（追击时给「要打好几发」的目标加惩罚）判成「机制空转」
（700/700 逐局相同）是**实现错误**，不是想法错误。两处 bug： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-27-godai-a28-fix

## §2026-10-27-godai-off-knobs-census — 37 个历史 OFF 旋钮普查：24 个真开过、8 个是死开关、0 个能翻正

**结论先行**：按 `ARCHIVED_KNOB_GROUPS` 的文档开启值把 37 个旋钮全部跑了一遍
（hard 35×4 = 140 局 CRN 配对，可疑项再上 60 seeds）。与 A28 那批不同： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-27-godai-off-knobs-census

## §2026-10-27-godai-battlement-riverbed — 两关顽疾尸检：败因是"已开通的中路列 + 远距狙击"，预封线未过关（默认仍 1）

**结论先行**：Battlement / Riverbed 的失败 **100% 是丢基地**（hard 60 局：30/25 负，
0 局命数耗尽）。致命弹不是贴脸凿穿打出来的：丢基地那一刻基地 3 格内敌人中位数 **0**， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-27-godai-battlement-riverbed

## §2026-10-27-godai-a9-preseal-steelgate — A9 mode2 预封线「只在无钢中路」发货：Riverbed 58%→65%，hard 86.1% / chaos 83.2%

**用户裁定**：预封线只用于**中路没有钢铁障碍**的关卡。成立，已发货。
  ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-27-godai-a9-preseal-steelgate

## §2026-10-27-godai-seal-cell-rule — 封线格"不必最近基地"：几何成立、行为更差（默认仍 = 最近鹰）

**用户提出**：只要在那条走廊上，任意一格都能挡住炮弹，去**最快到达**的点就行，不必钉死"离基地最近"。
**几何上完全正确**（车身 16px、炮弹 6px，射手与鹰之间任意一格都挡得住），但三种实现全部更差： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-27-godai-seal-cell-rule

## §2026-10-27-godai-seal-footprint — 封线"从来不停"的真因：车是 2×2、选格只查 1 格（修好更差，默认仍 0）

**用户质疑**："到达点位就停住开火了，还移动干什么？" —— **它想停，但停不下来**。
  ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-27-godai-seal-footprint

## §2026-10-27-godai-seal-vs-melee — "远敌才封线、贴脸就搏击"：两条规则都实现，都中性（默认全 0）

**用户规则**：北边中路的远敌才需要封线挡弹；已经跑到基地边上的就近身搏击。拆成两个门控：
  ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-27-godai-seal-vs-melee

## §2026-10-27-godai-seal-antistuck — "贴脸还一直导航"的真因：卡在走不到的封线格上（防卡死 SHIPPED）

**用户追问**："都已经贴脸了，为什么还一直导航？直接并线背击！" 逐拍追踪 Checkers seed 8：
  ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/godai.md` §2026-10-27-godai-seal-antistuck

## §2026-11-09-goalnn-dashboard-behavior-metrics（首页/指标表接入 eval 逐局行为指标）

- **决定**：服务端 `collectEvalSummaries` 按 `(iter,wver)` 聚合原始值，用 `computeMetricStats`
  算 mean / median / min / max / p90 / p99，挂 `EvalSummary.behaviorMetrics`；客户端注册表 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-09-goalnn-dashboard-behavior-metrics

## §2026-11-09-goalnn-dashboard-behavior-metrics-all-sources（逐局指标全量 + rollout/eval 双源）

- **决定**：`BEHAVIOR_METRICS` = `METRICS_COLUMN_NAMES` 的 90 列 + eval 专属 `aliveTicks` /
  `postThawDamageRate` = 92 项；rollout 与 eval 各自聚合六统计量，趋势卡 / 放大浮窗 / 指标表共用一份注册表。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-09-goalnn-dashboard-behavior-metrics-all-sources

## §2026-11-09-goalnn-dashboard-behavior-metric-stats（逐指标统计量 + 浮窗单统计量初态）

- **决定**：`TC_TREND_STAT` 由单值改为 JSON map `{metricKey: stat}`（默认全部 `mean`）；配置面板每个逐局
  指标行一个统计量下拉，趋势卡按各自统计量取序列；旧版单值（如 `'p90'`）读取时铺到全部指标，坏 JSON / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-09-goalnn-dashboard-behavior-metric-stats

## §2026-11-09-goalnn-sampler-supervised-restart（采样节点远控重启交给 notebook supervisor）

- **决定**：`rollout_node._start_agent` 注入 `SAMPLER_SUPERVISED=1`。agent 收到 `/v1/restart` 后
  先完成可选 `pullBranch` 的 fetch/reset，再停监听、删锁、收 worker 池并以退出码 **75** 退出； ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-09-goalnn-sampler-supervised-restart

## §2026-11-09-goalnn-offline-start-report（离线训练开始：先只上报，不自动停 PPO）

- **决定**：本阶段只做上报，复用现有 `POST /api/notebookOp`（不新增 dashboard 端点/状态）；
  op 名 `offline:detected` / `offline:cell-start`，detail 带 cell / 等待时长 / 证据与 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-09-goalnn-offline-start-report

## §2026-11-09-goalnn-notebook-modelscope（魔搭 Web IDE：面板接入，日志搬运 fail closed）

- **决定**：把 `/notebook/main.html` 认成 `single` 站点 `modelscope`（面板挂在帧内，与 Colab/AI Studio
  同一条路），不接外层 `/code/workspace`（那里没有 notebook DOM）。适配器只做实测过的动作： ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-09-goalnn-notebook-modelscope

## §2026-11-10-stream-eval-drain-gate（stream eval 动态闸：队列清空解冻 / 回队重冻）

- **决定**：`RolloutDispatcher` 暴露 `on_queue_active`，与既有 `on_queue_drained` 一起交给
  `QueueEdgeGate`；stream 在全部任务已派出时 `end_rollout` 解冻本课 eval，超时任务回队且仍有 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-10-stream-eval-drain-gate

## §2026-11-10-goalnn-o2-3000-ticks（o2 系列终局拍数 = 3000：按人类体验口径前移，6000 只在 o1 有剂量依赖）

- **决定**：o2 系列课程 `max_ticks = 3000`（≈2.5× 熟练人类），写在各课程文件里（不关卡文件，
  关卡是所有腿共享的环境语义）；训练 rollout / A 层 in-loop eval / B/C 判决面板统一 3000， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-10-goalnn-o2-3000-ticks

## §2026-11-10-goalnn-o2-it0-params（o2-fast 开课前按 it0 失败结构重标 3 个奖励参数：wKill 15→18 / paceTicks 1200→1000 / paceWindow 1000→800）

- **决定**：只动 3 个参数，其余 19 项与 terminal 保持 o1 剂量：`wKill 15→18`（+20%，
  2 敌下第二杀承载整条胜利路径，15 是 1 敌口径）；`paceTicks 1200→1000`、 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-10-goalnn-o2-it0-params

## §2026-11-10-goalnn-o2-def（o2 并行第 2 腿「防守定价臂」：两行超额密度税打 158 个开局命尽局，未开课）

- **决定**：新课 `nn-training/curricula/o2-def.jsonc`（与 o2-fast 并行）。任务侧 / bc
  （o1-gq-fast.it256）/ 22 行奖励与参数 / PPO 缰绳 / eval 面板**逐字同** o2-fast；唯一 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-10-goalnn-o2-def

## §2026-11-11-goalnn-eval-wire-snapshot（控制台 eval 读数必须快照优先：云机实时体把白名单外的游戏列收进 `metricValues`）

- **决定**：`collectEvalSummaries` 的四个白名单外输入（`enemyTotal` / `powerUpsSpawned`（含
  `puSpawn*` 分项）/ `startLives` / `playerHpRatio`）统一走 `evalMetricValue`（快照 → 顶层）。 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-eval-wire-snapshot

## §2026-11-11-goalnn-blob-cache-touch-and-root-layout（自主段的两处自毁：blob 缓存读命中必须 touch；布局判据取「本会话见过几门课」）

- **背景**：用户报「自主云机 07:33 出错后一直没自行恢复，08:41 手动重跑 cell 才好」。日志里是两条
  故障咬在一起：① `it163` 的 PPO 在 `_fetch_ref` 处 `ProtocolError: 没有 hub 地址（base_url 为空） ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-blob-cache-touch-and-root-layout

## §2026-11-11-goalnn-offline-start-probe（离线训练「开始执行」探测两天 0 条：判据补强 + 读数出口）

- **决定**：① 证据改成**相邻两拍**的输出变化；② 新增看门狗——`others===0` 且连续 3 拍执行、等满 3s
  ⇒ `evidence=held` 上报（只写 op、不下发命令，排队仍不报，原有不变量不变）；③ `target` 连续 3 拍不在 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-offline-start-probe

## §2026-11-11-goalnn-offline-stop-others-downlink（`stopOthers` 接成真命令：dashboard 中转 `stop-session`）

- **决定**：三件事都不新增语义层——① dashboard 在 `POST /api/notebookOp` 里，见到
  `offline:cell-start … stopOthers=1` 就给**其它**最近 10 分钟有 op 写入的会话各记一条 `stop-session` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-offline-stop-others-downlink

## §2026-11-11-goalnn-greedy-offline-round（自主云机轮内补 greedy 采集：B 口径「挤进普通采样」，账本三键与 trainer 同形）

- **背景**：`o2-notax` 自主段 300 轮写着 `greedy_quota: 12%`，却从未执行（云机日志 `greedy`/`argmax` 0 次；
  账本只有 it1 的 `volume_stage_stats.greedy`）。根因**不是** `export_bundle` 守卫：自主腿的一轮由 ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-greedy-offline-round

## §2026-11-11-goalnn-rollout-node-liveness-budgets（云机 rollout 节点「经常断连 / 贡献度极低」：探活预算、权重握手、并发取小）

- **现场（kaggle 滚动机节点，2026-11-11）**：节点空载答 `/v1/ping` 要 **0.9~2.7s**（quick tunnel RTT），
  而派发/评估的探活预算是 **3s** ⇒ 07/08/09 三个小时各 `ping failed — excluded this round` ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-rollout-node-liveness-budgets

## §2026-11-11-goalnn-rollout-racing-connect（rollout 每轮 63s 的真因：串行 connect 的「每地址 timeout」；更正并收回上一条的预算修法）

- **现场**：`o2-loot3` / `o2-notax3` 每轮 rollout **63.6s / 62.8s**（预期 <40s）。分相计时：节点门
  **43.4s / 41.7s（≈68%）**、真采集只有 15.7s / 16.3s。门每轮跑 2~3 次（主采样波 + greedy 波 + 补波）， ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-rollout-racing-connect

## §2026-11-11-goalnn-race-metrics-split（`common/distribution.py` 再腾余量：竞速账 / 采集口径下沉 `common/race_metrics.py`）

- **决定**：把「竞速账 + 采集口径」整块搬到 **`common/race_metrics.py`**（75 LOC，stdlib-only）：
  `rollout_collect_sec` / `dup_rate` / `effective_slots` / `race_stats` / `by_node_of` / `dist_block` / ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-race-metrics-split

## §2026-11-11-decisions-slim-round5（2026-11-11，用户指令：给 DECISIONS.md 瘦身 —— 第五轮：正文下沉到 details/）

- **决定**：256 条全文按领域搬进 `docs/decisions/details/{goalnn,godai,repo-governance}.md`（锚 `## §<id>`），索引行压成「标题 + 原文节录（≤6 行）+ 指针」；编号集合不变。
  ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/repo-governance.md` §2026-11-11-decisions-slim-round5

## §2026-11-11-decisions-slim-round6（2026-11-11，用户指令：索引节录压到 1–2 行）

- **决定**：629 条已带 `—— 全文` 指针的索引节录压到 ≤2 行，全文位置与编号不变。
  ……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/repo-governance.md` §2026-11-11-decisions-slim-round6

## §2026-11-11-goalnn-o2-nomove-open（o2-pick 判负：撤掉方向无关的移动补贴 wMove，而不是继续抬拾取权重）

- **决定**：立 `nn-training/curricula/o2-nomove.jsonc`（**已立项 · 待开课**，agent 不启动训练）。
  从 `o2-pick` **逐字**继承，**只删一行** `+ wMove*min(ticks - idleTicks, moveCap)`（30→**29** 行）……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-o2-nomove-open

## §2026-11-11-goalnn-o2-polish-open（o2 胜率打磨：奖励压力已拉满 ⇒ 杠杆改走语料侧，钉短板关 + 抬 greedy 供给）

- **决定**：立 `nn-training/curricula/o2-polish.jsonc`（**已立项 · 待开课**，agent 不启动训练）。
  奖励 **29 行 / 61 参数逐字继承 o2-nomove**（已摘 wMove），改动**只在语料侧 13 键**……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-11-goalnn-o2-polish-open

## §2026-11-12-goalnn-bc-p0-open（God AI 语料 BC 单元探针：2敌/4关/800 局，判「能不能学」而非拿胜率）

- **决定**：立 `tmp/bc-p0-godai`（`export-godai-labels.ts --stage-json-file --wins 0 --near-miss-times 0`，
  800 局 / 123,992 决策样本）+ `train_bc.py --arch student`（legacy 5/2 头、`--policy-extra OFF`）；
  `extra.npy` 全 0 占位不进梯度。10:17 用户改令「不在本机训（无显卡），开 BC 课让云机
  GPU worker 接活」⇒ 本机三次 CPU 尝试全部停止。10:26 课程改名 **`g0-4s2e`**（g 系列起点，
  g<N>-<stages>s<enemies>e 命名规则）、epochs 80 + patience 10，eval 开 every 10 epoch ×
  4 关 × 50 局（seed 1..50 与训练 860001+ 不重叠）……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-12-goalnn-bc-p0-open

## §2026-11-12-goalnn-bc-eval-course-env（BC 周期 eval 继承课程环境：`eval.course_env`，与训练同源）

- **决定**：`BcEvalBlock.course_env: true` ⇒ eval 直接用课程解析后的 stage 集与四类覆盖
  （`stage_select`/`enemies`/`lives`/`star` + `max_ticks`），忽略 `levels`；与 `levels` 互斥、
  只支持 list 型 stages（range 型加载时拒）。`panel` 加 `source/stage_ids/enemies`；
  `g0-4s2e` 切 `course_env: true`（4 关 × 50 局 / 10 epoch，seed 1..50）……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-12-goalnn-bc-eval-course-env

## §2026-11-12-goalnn-bc-progress-pill（BC 课 pill：认领 ≠ 没开算 —— hub inflight 行补 `progress_ago`，BC 活性只看每 epoch 进度）

- **决定**：BC job 从不打 `/start`（bc 分叉早退）⇒ `computing_ago` 恒 null，旧的「卡住」判据（认领 >300s ∧ 无开算）
  必然误报。hub `/admin/queue` 的 inflight 行纯观测补 `progress_ago`（剔除认领首锚）；控制台以 `≤900s` 判 BC 在算，
  BC 的卡住门槛也用 900；词表按课程种类写 `PPO·` / `BC·`。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-12-goalnn-bc-progress-pill

## §2026-11-12-goalnn-bc-restart-resume（BC 重启不许重算：入账游标按账本自愈，面板分段优先 job 身份）

- **决定**：`BcWait` 的入账游标只是内存态 ⇒ 重启后把 hub 全量 `bc-metrics` 当新行（重复入账 +
  每 10 epoch 重跑一次 eval，g1c-4s2e 实测 5 边界各 200 局）。改成：新会话首次入账前按账本
  自愈（`job_pending` 到本 jid 之后的行 = 这个 job 的历史），且 eval 只派给本拍真入账的行；
  面板分段优先 `job_pending`（同 job 重启不切曲线，换 job 才切）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-12-goalnn-bc-restart-resume

## §2026-11-12-goalnn-console-bc-ep-pointer（控制台的 BC 课读数用 `ep`，不用 `it`）

- **决定**：BC 一段训练 = 一张 job，`it` 恒为 1 不配当进度 ⇒ `/api/state` stamp
  `bcProgress: {course: {epoch, total}}`（账本段内 `bc_epoch` 最大值 + `run_start.epochs`，
  与 BC 面板同一个段锥）；侧栏「最近训练」与顶部 pill 的读数词统一走 `pointerWord`：
  BC = `ep57`（悬停给总 epoch），RL = `it37` 不变。BC 面板表头同口径：`/api/bcEpochs`
  带同一个 `pointer`，写 `ep57/60`（取代「共 N epoch」的行数）。课程矩阵 / 课程管理页的指针列走
  `pointerCell`（BC `ep57` / RL `it37`），列头改名「指针」。**不动** `it` 语义（它是 hub 的 job
  身份/续训键）。

—— 全文（背景 / 备选与否决 / 回归 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-12-goalnn-console-bc-ep-pointer
## §2026-11-12-engine-bench-shape-drift（微基准空转：形状漂移让 rollout 引擎选择吃假数字）

- **决定**：v7 `IN_CH` 18→19 只有 `tools/agent/engine-bench.ts` 漏改 ⇒ 两后端形状守卫 `return false`、`BENCH-ARM unknown`、0.00x ms 假数字被永久缓存；立 `src/nn/conv/conv-shape.ts` 为形状单一来源，加基准自检非零退出 + `benchResultValid` / `benchSha` / mjs 源指纹 stamp 三道防线……

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/repo-governance.md` §2026-11-12-engine-bench-shape-drift

## §2026-11-12-goalnn-offline-bc-course-parity（BC 课程与 RL 课程在自主云机队列里同等对待）

- **决定**：`/offline/tasks` 每行带 `kind`；BC 课程与 RL 课程进同一个 `_run_batch` 按同一排序串行，
  BC 走课程级 `run_bc_course`（反复领本课 BC job，完成看账本收官、独占看 `bc-exclusive.json`）；
  `claim_offline` 对 BC 返回 `bc_course` 不建课程 hold；课程筛选只在客户端对 peek 响应做本地谓词，
  请求仍不带 course。BC 冷启动从 hub `/code` 取代码，不再依赖先跑 RL 任务包。
- **被否决**：继续 idle-only BC；把 `course` 加进取活链路的请求参数（违反
  `test_poll_path_has_no_course_input`）；给 BC 造假 task pack。
- **后果**：BC 与 RL 共享排序/预算/停机语义；`_idle_take_bc` 只剩清单空/全被持有时的兜底。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-12-goalnn-offline-bc-course-parity

## §2026-11-12-goalnn-bc-completed-epoch-pointer（BC 完成事件区分目标与实际：dashboard 用 `epochs_completed`，调度器仍读 `epochs`）

- **决定**：`bc_round_completed` 同时写 `epochs`（课程目标；续跑/预算扩大判据）与
  `epochs_completed`（实际完成数，早停更小）；dashboard 指针取 max(`bc_epoch`, `epochs_completed`)，
  旧账本缺新键时回落 `epochs`；`/api/bcEpochs.missing` 在 `slice(-100)` 前用完整行算。
- **被否决**：把 `epochs` 改成实际值（会动续跑/预算语义）；只读 `bc_epoch`（g1e 末行丢失仍停
  ep59/60）；在面板用切片后的 100 行算缺号（150 epoch 课误报 ep1..50）。
- **后果**：完成事件多一个只读字段；`recorded_epochs` 仍读 `epochs`，调度器行为不变；
  g1e 显示 `ep60/60`，悬停与黄 pill 保留「指标只到 ep59 / 缺 25,27,60」的诚实标注。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-12-goalnn-bc-completed-epoch-pointer

## §2026-11-12-goalnn-bc-final-eval-backfill（BC 末轮 eval 不许漏：触发点与入账游标解耦，重启按 `bc_eval` 补评）

- **决定**：`BcWait` 增独立的 `evaluated_epochs`（只由 `bc_eval` 种）；指标增量与 READY 共用
  `_eval_epoch_if_due`，READY 用 `result.metrics.epochs_completed` 兜底末边界；重启重放行也参与
  eval 候选（缺 `bc_eval` 才补评）；`late` 只由本拍新入账行种，重复行不得取消同批边界 eval。
- **被否决**：继续只认「本拍新入账行」（末行丢失时没有触发点）；把 `bc_epoch` 与 `bc_eval`
  合成一份游标（`bc_epoch` 在 ≠ eval 已写成）；READY 无条件按课程 `epochs` 评估（早停/预算扩大
  会评一个不存在的边界）。
- **后果**：末 epoch 权重回传后必有对应 eval（快照存在时）；重启只补缺失的边界，不重算已评边界；
  旧 result 缺 `epochs_completed` 时行为不变。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-12-goalnn-bc-final-eval-backfill

## §2026-11-13-goalnn-bc-corpus-lazy-mmap（BC 大语料逐 shard mmap + 训练前内存预检）

- **决定**：训练装载改 `lazy=True`（obs/scalars/extra 走 ShardArray，每 shard mmap），
  `NNDataset` 不再整份 `.astype` 复制；装载前用 `available_memory_bytes` +
  `check_corpus_memory` 在分配前拒绝，错误给出可用/需要/处置。
- **被否决**：先写一份拼接后的 mmap `.npy`——多占一份磁盘，Colab/Kaggle 工作盘不够；
  eager 装载只 warning——内核仍会重启 notebook；靠异常兜底——没有机会解释原因。
- **后果**：g2-classic35 旧峰值 ~19.7 GiB → lazy ~1.1 GiB；代价是每 epoch 从本地盘随机读
  9.4 GiB。`load_dataset` 默认仍 eager（小语料工具行为不变），只有训练路径默认 lazy。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `docs/decisions/details/goalnn.md` §2026-11-13-goalnn-bc-corpus-lazy-mmap

## §2026-11-13-paddle-import-warning-noise（Paddle 日志两条噪声：ccache 提示按文案屏蔽 + 布尔下标换 `nonzero`/`gather`）

- **决定**：`remote/paddle_env.silence_import_warnings()` 在 `import paddle` 前按**文案**
  `No ccache found\.` 屏蔽那一条 UserWarning（不抬 `GLOG_minloglevel`、不全局静音）；Paddle 侧
  布尔下标 `x[keep]` 全部换 `nonzero` + `gather`（`bc_paddle._kept` / `engine_paddle`），消掉
  `tensor.cc:111` 的 reshape 弃用警告。
- **被否决**：安装 ccache（本仓不经 `cpp_extension` 编译算子，装了只消警告不省时间）；
  `GLOG_minloglevel=2` / 全局 `simplefilter("ignore")`（连 `gpu_resources` 的 device NOTE 与业务
  UserWarning 一起吞）；只写文档保留提示（每次开跑都带红字，运维会当故障追）。
- **后果**：BC/PPO 训练日志不再出现这两条；`nonzero` 与 `x[keep]` 同序同值（`_masked_ce` 单测
  钉住），索引每 batch 只算一次、两个张量共用，设备同步量与布尔下标同量级。

—— 全文（定位过程 / 证据 / 边界）→ `docs/decisions/details/goalnn.md` §2026-11-13-paddle-import-warning-noise

## §2026-11-13-goalnn-hub-restart-job-reconcile（hub 重启后按盘找回未认领 job + trainer 优先有训练进度的 job）

- **决定**：① hub 的 `course_of()` 在课程表无归属时按盘
  `<root>/<course>/remote-jobs/<jid>` 找，唯一命中且该课有 `training-enabled.txt` ⇒ 按需
  `add_course` 登记（歧义仍拒答）；② trainer 的 `find_round_job` 对同一 `it` 的多个未收口 job
  先探测全部候选，优先认领盘上有结果/训练进度的（`result/` > `bc-resume`/`bc-metrics`/
  `epoch-weights` > 空壳），`unreachable` 仍立即保守认领。
- **被否决**：hub 重启时无条件重扫全部课程目录（陈旧实验目录会被复活、变成可派发）；
  trainer 只按 manifest 最新认领（正是本次事故：新空 job 永久遮住旧 job 的 epoch）；trainer
  向 hub 要「哪个 job 有租约」（租约在内存里，hub 一重启就没了）。
- **后果**：等 GPU 超过 discover 新鲜窗的 job，在 hub 重启后 `/status`、`/result`、
  `/epoch-weights` 仍可路由；重发布出的空 job 不再抢走旧 job 的认领权。可按需登记的课程仍需
  盘上有 `training-enabled.txt`，未开课目录不会因一次查询复活。

—— 全文（背景 / 备选与否决 / 验证 / 恢复步骤）→ `docs/decisions/details/goalnn.md` §2026-11-13-goalnn-hub-restart-job-reconcile
