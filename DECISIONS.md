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

> **2026-11-14 修订**：**决定 C（并发取小）已被 §2026-11-14-goalnn-dispatch-slots-and-keepalive 取代**
> ——派工线程要覆盖隧道往返与回传（`wall 9.3s` / `svc 2.3s`），按核数取小让节点 64% 时间空转。
> 决定 A/B（探活预算、超时不摘权重账本）仍成立。

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

## §2026-11-13-nn-python-audit-round4-hardening（nn-training 第四轮审计：先清 lint 债与版本矩阵，再把函数形状 / L0 纯度做成门禁）

- **决定**：`B023` / `SIM115` 从 ruff 全局 `ignore` 移除并清掉 40 处；`common/log.py` 的时间戳
  改走 `common/logutil.stamp()`（删掉第二份格式化）；JSONL 写者加 `close_jsonl_writers()` +
  `atexit` 有界收尾；CI 矩阵加 3.10 下界并用 `uv sync --python` 真用矩阵值；新增
  `tests/test_python_shape_budget.py` 与 `tests/test_python_version_matrix.py`。
- **被否决**：把 `requires-python` 收紧到 `>=3.12`（现役 venv 是 3.10，会让它变成「不受支持」；
  且 `fs.extract_tar_bytes` 的 `<3.12` 分支从未被 CI 走过）；继续全局 ignore `B023`
  （闭包捕获循环变量是真 bug 类，实测 25 处）；一次性重写 `dispatch._run` / `publish_job`
  （无设计评审、无真跑验证的高风险改动）。
- **后果**：ruff/mypy 零新增豁免；函数 >200 行 / >8 参数、`common/` 模块级可变容器、
  `global` 语句全部「只许降不许升」；训练/评估行为不变（`pytest tests/ e2e/` 5986 例全绿，
  3 例按平台跳过）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `python.refactor.md` §「处理状态（2026-11-13）」

## §2026-11-13-nn-python-audit-round4-publishspec-and-l0-objects（发布签名收成 PublishSpec；L0 三处状态对象化）

- **决定**：`publish_job` 的 55 个字段搬进 `PublishSpec`（frozen + kw_only），签名只剩
  `spec` / `log` / 过渡 `**fields`（旧 kwargs 在入口构造对象，两条路二选一）；`common` 的
  `_COURSE_SINKS` / `_JSONL_WRITERS` / `_WEIGHTS_PUSHED` 分别收进 `LogRouter`（ContextVar 前缀）
  / `JsonlLedger` / `WeightsLedger`，模块函数保留为默认实例的薄包装并支持 `router=` / `ledger=`。
- **被否决**：一次性改完 20 个调用点（测试与 e2e 里的 kwargs 形态仍合法，先保留过渡适配器，
  避免同一笔改动同时动生产与 15 个用例文件）；把 `log` 也塞进 spec（它是出口不是发布内容，
  塞进去会让 frozen dataclass 的比较语义带上函数对象）；给 `traj_dir` 造用途（原函数就没读过它）。
- **后果**：新增发布字段只改 `PublishSpec` + 绑定块；`PublishSpec` 与字段 kwargs 同时给会
  `TypeError`；L0 两实例互不串味有专项用例；`common/` 容器门禁名册从 6 条降到 3 条。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `python.refactor.md` §「处理状态（2026-11-13）」

## §2026-11-13-nn-python-audit-round4-publish-spec-callsites（publish_job 只收 PublishSpec：删过渡适配器，26 个调用点改传对象）

- **决定**：`publish_job` 签名收紧为 `(spec: PublishSpec, *, log)` —— 删掉 `**fields` 过渡适配器；
  生产 / e2e / 测试共 26 个调用点全部改为 `publish_job(PublishSpec(...)[, log=...])`。`**dict`
  形态的测试辅助函数把 `log` 从 dict 构造里剥出、在调用处单独传。
  `tests/remote/test_publish_spec.py` 改为断言签名只有 `["spec","log"]`、kwargs 形态直接
  `TypeError`、同 spec 两次发布幂等。
- **被否决**：保留过渡适配器继续双轨（两处真相，每次加字段要同步两条路）；把 `log` 放进
  `PublishSpec`（出口回调不是发布内容）；为省行数改掉测试里的 `log` 默认值（会掩盖「谁在打日志」）。
- **后果**：`publish_job` 不再接受字段 kwargs，传错立刻 `TypeError`；新增字段只改 `PublishSpec`
  + 绑定块；`tests/trainer/test_bc_loop.py` 的 `fake_publish` 改收 `spec` 位置参数；
  `trainer/bc_loop.py` 的调用改成紧凑形态，LOC 回到 998（预算 1000）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `python.refactor.md` §「处理状态（2026-11-13）」

## §2026-11-13-nn-python-audit-round4-dispatch-phases（dispatch._run 按相位拆 5 方法 + DispatchState）

- **决定**：`RolloutDispatcher._run`（1184 行）按既有 ①②③ 注释切成 `_ping_gate` /
  `_prepare_weights` / `_run_queue` / `_settle_round`，`_run` 只做编排；相位配置别名与产物
  收进 `DispatchState`；`DispatchState` 与节点准入循环一起搬进 `trainer/dispatch_state.py`
  （`admit_rollout_nodes(st, probe_results, cfg_nodes, dedup_streak)`），`dispatch.py` LOC 回到预算内。
- **被否决**：把 15 个局部别名作为参数逐个传递（相位间 30+ 参数）；一次性把 `worker` 闭包也
  方法化（499 行 + 40 个闭包变量，需单独设计与真跑验证）；为通过形状门禁上调基线
  （那是把新增长函数登记成存量）。
- **后果**：`_run` ~30 行、`_ping_gate` ~120 行、`_prepare_weights` ~30 行、`_settle_round` ~85 行；
  `_run_queue` 仍 893 行（含 worker 499 行），`trainer/dispatch.py` 的长函数计数仍为 2（未上调基线）。
  dispatch 韧性用例 24/24 绿；`_ping_gate` 必须在调 helper 前把 codeHash/bootId 等写进 `st`
  （否则 helper 读到空串会把所有节点判掉）。

—— 全文（背景 / 备选与否决 / 证据 / 后果）→ `python.refactor.md` §「处理状态（2026-11-13）」

## §2026-11-13-nn-python-audit-round4-trainspec-and-cli-table（训练核收 TrainSpec；92 个 CLI flag 收进数据表）

- **决定**：`remote/train_core.py` 新增 frozen + kw_only `TrainSpec`（27 字段）；`run_training_core` 与
  `_run_training_core_paddle` 签名都收成 `(base_url, token, spec)`，Paddle 双生读同一对象（忽略
  `backend`）；`worker.run_job` 只构造一次 `spec = TrainSpec(...)`。`worker/cli.py::build_argparser`
  的 92 个 `ap.add_argument(...)` 改成 `_flag_rows()` 返回的 `(flags, kwargs)` 元组表 + 一个装配循环
  （650 → 171 行）。
- **被否决**：继续用 `**kwargs`/dict 传 27 个字段（守卫失去逐名字段对账）；为 CLI 表另立模块
  （表闭包读 `mode` / `_d` / `game_watch`，搬出去只会多传三个参数）；上调形状或文本断言基线
  （断言合并后净增 0）。
- **后果**：`test_train_core_split.py` 钉住「核形参 = (base_url, token, spec)」「`spec` 关键字集合 ==
  `TrainSpec` 字段集合」「Paddle 双生同签名」；旧/新 parser 在 3 种 mode × rl_args 覆盖值下逐 action
  与 `parse_args` 全等；`worker.py` LOC 贴线，构造收成 `spec = ...` + 单行调用后才回到 <1000。
  全量 5995 passed / 3 skipped，ruff / mypy 绿。

- **后续（同日）**：`_flag_rows` 从 `build_argparser` 内提升到模块级（`(mode, rl_args)`），
  `build_argparser` 只剩 7 行装配；`game_watch` 保持函数内 import，调用面不变。

## §2026-11-13-nn-python-audit-round4-queue-workers（_run_queue 拆成 QueueState + 四段消费者）

- **决定**：`_run_queue`（871 行）拆成 `_queue_plan`（79 行，续跑/复用/早退）、`_queue_setup`
  （144 行，保留段/长驻池/共享容器）与 `trainer/queue_worker.py`；消费者按相位拆成
  `_claim`（134）/ `_execute`（133）/ `_settle_success`（119）/ `_settle_failure`（144）/
  `queue_worker`（26）/ `run_queue`（168），共享状态收进 `QueueState`，499 行 `worker` 闭包消失。
- **被否决**：整段搬进新文件（871 行只是换家，形状门禁仍红）；把局部函数留在闭包里（AST 按外层
  函数计长，`_run_queue` 不会变短）；上调 `LONG_FUNCS_BASELINE`（把新长函数登记成存量）。
- **后果**：`dispatch.py` 长函数 2 → 0、LOC 958 → 473；`queue_worker.py` 所有函数 ≤168 行。
  `log` / `run_local_rollout` / `record_agent_meta` 经 `QueueState` 注入，测试对 `trainer.dispatch`
  的 monkeypatch 接缝不变。`test_dist_common_poll` 的 rollout 接线扫描面跟随到新家（按腿取并集 +
  409/标记断言）。`_settle` 与 `remote/result_upload.py` / `remote/upload_proc.py` 同名 ≥3 触发复用
  账本，改名 `_settle_attempt`。验证：ruff、mypy 913、全量 5995 收下（5992 passed / 3 skipped）。

## §2026-11-13-nn-python-audit-round4-ppo-update（ppo_update 拆成 PPOContext + 七段函数）

- **决定**：`worker/ppo/engine.py::ppo_update`（598 行 / 17 参）保留 17 参签名作为
  `trainer/stream.py` backend 契约的薄适配器（~85 行），运行时状态收进 `PPOContext`，
  阶段拆成 `_prepare`（profiler / tensored / XLA 诊断 / demo / ref cache / 续跑）、
  `_train_step`（一次 minibatch 的 forward/loss/backward/step，177 行）、`_record_diag`、
  `_maybe_heartbeat`、`_finish_epoch`、`_finish`、`_aggregate`。XLA `xla_mark_step` 边界、
  诊断读数时机、`np.random.permutation` / demo 采样顺序、ref cache 每 chunk 一次、profiler
  采样窗口与 `_step_walls` 记录条件都逐字搬移；`_Diag` / `_Demo` 只承载原局部变量。
- **被否决**：抽 `PPOLoop` 类（只是把闭包换成 `self`，跨阶段共享的仍是同一批张量）；
  `LossTerms` 注册表（kl / kickstart / demo / value_anchor 共享前向结果与 RNG，拆对象只会多传
  上下文，且新增损失项仍需改 `_train_step`）；改 17 参公开签名及全部调用点（训练后端契约，
  收益与风险不成比例）；上调形状/文本断言基线（本轮净增 0）。
- **后果**：`ppo_update` 598 → ~85 行，`engine.py` 无新增 >200 行或 >8 参函数（最长
  `_train_step` 177 行）；`_record_diag` 显式接收 `wall_s`，不再跨函数读 `_step_t0`。
  `test_xla_step_diag.py` 的两条源码接线断言按新函数边界重锚（仍要求 mark 后立刻取时、
  物化夹在转换与循环之间）。验证：ruff、mypy、全量 5992 passed / 3 skipped。

## §2026-11-13-nn-python-audit-round4-iter-rollout（run_iter_rollout 拆成 _IterRun + 六段）

- **决定**：`worker/iter_rollout.py::run_iter_rollout`（604 行 / 9 参）保留原签名，入口只做
  `_IterRun` 构造与阶段调用；批级状态收进 `_IterRun`，阶段拆成 `_setup_run`（路径 / bun /
  并发口径 / 日志）、`_setup_scratch`（轮级 scratch + 批镜像 + 产出根）、`_setup_pool`
  （长驻池）、`_drive_games`（流式调度 + 带超时 wait + 机器级停滞重投）、`_run_games`
  （pool 收尾 / 中断现场）、`_finish_run`（对账 / 聚合 / 返回）。`_samples_of` 改为
  `(exec_jd, argv)` 的模块函数，`_clean_stuck` 独立承担卡局半截产出清理。
- **被否决**：把整个函数改写成 `self` 类（`__init__` 会新增一个 9 参宽函数，且所有局部引用
  都要重写）；把 `_drive_games` 再按「单次尝试 / 重投」拆开（每次重建 executor，并要额外搬运
  `pending` / `settled_n` / `supply_open` 等跨尝试状态）；为凑 LOC 把 setup 搬去新模块
  （拆分只是换家，且破坏本模块与 `worker.serve_pool` 的层内聚）。
- **后果**：`run_iter_rollout` 604 → ~50 行；最长新函数 `_drive_games` 172 行；源 LOC 回到
  1000 以下。`retry_cap` 原先是 `try` 前的纯局部量，拆分时必须进 `_IterRun`，否则
  `NN_ROLLOUT_ROUND_RETRY_MAX` 的退出阀静默失效（`test_reap_stall_round_retry_is_unbounded_unless_operator_caps_it`
  当场抓到）。`_setup_pool` 必须以 `rctx.pool` 为复用源（新建 `_IterRun.pool` 是 `None`）；
  删掉这行会让补差批重建池，`test_round_ctx` 两例立刻红。流式调度、池生命周期、scratch 回退、
  轮末逐位对账与日志顺序均未改。
  验证：ruff、mypy、rollout 相关 187 例、全量 5992 passed / 3 skipped。

## §2026-11-13-nn-python-audit-round4-eval-dispatch（EvalDispatcher.run 拆成 _EvalRun + 五段）

- **决定**：`trainer/eval_dispatch.py::EvalDispatcher.run`（589 行）保留公开方法与恒 `False`
  返回契约，`run` 只做 `_EvalRun` 构造与阶段调用；派生状态（语料/去重/复用/快照/阈值/节点
  列表）收进 dataclass `_EvalRun`，阶段拆成 `_eval_prepare` / `_eval_register` /
  `_eval_admit` / `_eval_start` / `_eval_close`。`_meta_ok` / `_record_failed_meta` 随
  `EvalRound` 登记移进 `_eval_register`，`_on_weight_ready` 留在 `_eval_admit`；异常与
  `finally` 的 unregister / release_owner 仍在 `run`。
- **被否决**：把状态挂到 `self`（同一 dispatcher 实例会跨轮残留，且隐藏状态）；按节点拆
  `_eval_admit`（ping 循环的顺序、日志与 `alive` 保序是判据的一部分）；把收工对账再拆成独立
  文件（本模块已贴 LOC 预算，拆文件只是换家）；保留通用方法名 `_prepare/_register/_admit/
  _start/_close`（同名散落账本 110→111）。
- **后果**：`run` 589 → ~77 行；最长新方法 `_eval_admit` 193 行；方法名统一 `_eval_*`
  前缀，复用账本回到 110。`EvalDispatcher.run` 的调用点、`ed.log` 的 monkeypatch 接缝、
  节点门/权重 POST/收工 summary 顺序均未改。验证：ruff、mypy、eval 相关 272 例、全量
  5992 passed / 3 skipped。

## §2026-11-14-nn-python-audit-round4-train-core（run_training_core 与 Paddle 双生各拆成 _CoreRun + 五阶段）

- **决定**：`remote/train_core.py` 的两条核都保留公开签名 `(base_url, token, spec)`，入口只做
  `_CoreRun` 构造与阶段调用。torch 核拆成 `_torch_model` / `_torch_opt` / `_torch_ref` /
  `_torch_ppo` / `_torch_artifacts`；Paddle 核拆成 `_paddle_model` / `_paddle_opt` /
  `_paddle_ref` / `_paddle_ppo` / `_paddle_artifacts`。两条腿共用的框架无关状态与结果面
  （`_CoreRun`、`_load_ppo_data`、`_core_result`、`_agg_block`）下沉到已有的
  `remote/train_common.py`（L2）；`train_common` 不反向 import `train_core`，`_CoreRun.spec`
  用 `Any` 切断类型环。Paddle 侧复用 `_load_demo_bank` 与 `_load_ppo_data`，不再复制 demo
  装载与 shard 分块。
- **被否决**：把 torch/paddle 阶段搬到新模块再抬 `train_core` 的层号（`worker` 等下游要连锁
  抬层，只为 LOC 破坏拓扑秩）；把 Paddle 核留在 `run_training_core` 内做分支（第一段就
  `import torch`，AI Studio 无 torch 镜像会直接死）；把 `_CoreRun` 拆成 torch/paddle 两份
  dataclass（字段与生命周期几乎相同，只会制造第二份契约）；上调 LOC/形状/文本断言基线。
- **后果**：`run_training_core`（原 torch 核）与 `_run_training_core_paddle`（387 行）→ 两个
  入口各 24/30 行；最长新函数 `_torch_model` 126 行、`_torch_opt` 122 行；`train_core.py` 源 LOC
  1098 → 929（预算 <1000），长/宽函数基线仍为 2/5。`paddle_env.apply` 仍在 `import numpy /
  paddle` 之前，Paddle 的「Adam 无 step」记账仍只认引擎 `steps_out`，两条腿的结果字典与
  wire 字段逐项未改。测试锚点从入口跟到阶段：设备 `_torch_model`、消毒 `_torch_opt`、四处
  `job_body_error` 分别落在 `_torch_opt/_torch_ref/_load_demo_bank/_torch_ppo`，delta 七调用
  扫 `_torch_artifacts` + `_paddle_artifacts`，`_wire_block` 唯一实现断言改读 `train_common`。
  验证：ruff、ruff format、mypy、train_core 相关 84 例、全量 5992 passed / 3 skipped。

## §2026-11-14-nn-python-audit-round4-action-space（动作空间收成 common/contracts 单一契约）

- **决定**：新增 stdlib-only 的 `common/contracts.py`：`ActionSpace`（`LEGACY` / `V6`）与
  `HeadLayout`（move/fire/hold 宽度 + 三个掩码切片）。PPO torch 核在 `_prepare` 用
  `ActionSpace.from_batch` 对数据边界嗅一次，存进 `PPOContext.space/layout`；Paddle 核在
  `tensored_chunks` 后同样定一次。ref 预计算用 `from_forward_arity` 校验模型头数，两条腿
  的逐 chunk `a_hold` / `len(out)` 判据与手写 `:5 / 5:7 / :4 / 4:7 / 7:9` 切片全部改为读
  契约。`common.schema` 仍是张量布局的唯一来源，contracts 只派生切片并在 import 时校验
  7/9 宽度自洽。
- **被否决**：把枚举塞进 `common/schema.py`（TS 共享 schema 不该承担 Python 运行时分支）；
  继续用 `model.action_v6` 判 ref 头数（DataParallel 下属性代理不可靠，历史注释已记录）；
  在 `common` 里加 `RlShard` TypedDict（L0 不得 import numpy，`np.ndarray` 会破坏
  code.zip 的 stdlib-only 前提；shard 类型化留到 worker 侧再做）；保留引擎内的数字切片
  （正是 P0-4 的静默错位源）。
- **后果**：动作空间判据从数据侧 2 处 + 模型侧 1 处收敛到契约的 3 个入口；掩码切片不再由
  每个引擎各自维护。legacy/v6 的头数、切片、demo move CE 与 wire 字段逐项未改；新增
  `tests/common/test_action_space_contract.py` 钉住存在性判据、arity 严格性与切片覆盖。
  验证：ruff、mypy 915 文件、PPO/契约相关 50 例。

## §2026-11-14-nn-python-audit-round4-print-gate（G3：生产包 print 只减不增）

- **决定**：新增 `tests/test_no_print_in_prod.py`，用 AST 统计 `common/biz/hub/remote/train/trainer/worker`
  七个生产包的 `print(...)` 调用，按**文件计数**冻结在 `tests/golden/print_baseline.json`
  （当前 289 处 / 77 文件）。只拦增长，不要求本轮清零；`tools/` 与 `worker/scripts/`
  是一次性脚本，按文档豁免；`tests/` 不在扫描面内。
- **被否决**：用 `grep "print("` 计数（注释/文档串里的 print 会造成假阳性）；一次把 289 处
  全换掉（会同时改输出格式与测试断言，超出 G3 的“冻结新增”范围）；把 `worker/scripts/`
  也纳入（它们是手工探针，没有 logger 运行前提）。
- **后果**：P0-6 的日志迁移可以分批进行，但新代码不能再绕过 `common.log`/logger 增加
  `print`；删除 print 不需要改基线（只拦增长）。自检用例钉住扫描入口确实命中
  `hub/boot.py` / `common/log.py`，并确认豁免面没有混进基线。

## §2026-11-14-nn-python-audit-round4-torch-load-safe（P1-10：torch.load 显式 weights_only）

- **决定**：生产包 8 处 `torch.load(...)` 全部显式传 `weights_only=True`，不再依赖 torch 2.7
  的版本默认值；新增 `tests/test_torch_load_safe.py` AST 门禁，拦 `torch.load(...)` 与
  `from torch import load; load(...)` 两种写法，`tools/` 一次性脚本豁免。
- **被否决**：只改注释/文档（降级 torch 即回到 pickle RCE）；一次性换成 `common.torch_pickle`
  白名单（它解决的是“写出可被安全读取的字节”，读侧仍需 `weights_only` 这个开关，且会扩大
  本次范围）；把 `tests/` 里的 `torch.load` 也强转（测试独立构造样本，且 2.7 默认已安全）。
- **后果**：`weights_only=True` 会拒绝非张量/非基本类型的 pickle 对象；本仓这些路径读的都是
  `state_dict` / optimizer 张量树，已由 `paddle_parity` 与 `tests/remote/test_opt_*` 覆盖。
  以后新增读取点若确实需要完整 pickle，必须在门禁里显式开例外并说明攻击面，而不是删掉
  `weights_only`。

## §2026-11-14-nn-python-audit-round4-common-asserts（P1-9 / G7）

- **决定**：`common/schema.py` 的 5 处模块级 `assert` 改成显式 `RuntimeError`；两套布局
  不变量收进 `_check_layout(name, layout, dim)`。新增 `tests/test_common_module_asserts.py`
  扫 `common/**` 函数/类作用域外的 assert，并直接调用 `_check_layout` 验证坏输入抛错。
- **被否决**：只把 assert 换成 `if not ...: assert ...`（同样在 `-O` 下消失）；把校验挪进
  import 时执行的测试（校验仍需在生产 import 时生效）；保留 assert 并禁止 `python -O`
  （靠调用方纪律，不是代码级保证）。
- **后果**：`common` 的布局错误在 `python -O` 下也会立即失败，而不是带着错布局继续训练。
  函数体内的调试期 assert 不受 G7 影响（P1-9 允许保留但要求注明）。

## §2026-11-14-nn-python-audit-round4-net-http-cache（P0-5：net_http 懒单例）

- **决定**：`common/net_http.py` 的 `_NO_PROXY_OPENER` 与 `_RACING_OPENERS` 两个模块级
  可变缓存改成 `@functools.cache`（函数本体只负责 `build_opener`）；`global` 语句随之删除。
  `tests/test_python_shape_budget.py` 的 L0 可变名册与 `GLOBAL_BASELINE` 同步删掉 net_http 条目。
- **被否决**：保留 dict + 加锁（opener 是无状态缓存，锁只会把读路径串行化）；把 opener 做成
  显式注入对象（调用点遍布 `urlopen`/`urlopen` 相关热路径，收益不抵改动面；`cache` 已是
  stdlib 的线程安全缓存）；继续留在 G2 名册（名册的 `test_l0_allowlist_is_still_needed`
  会判过期）。
- **后果**：`common/net_http.py` 不再有模块级可变容器或 `global`；缓存无法显式清空，但
  `cached` 只有 2 个键、进程生命周期内 opener 本就应复用。G2 名册只剩 `distribution` /
  `platform_utils` 两组存量。

## §2026-11-14-nn-python-audit-round4-broad-except-gate（G4：宽泛异常处理增长）

- **决定**：新增 `nn-training/tests/test_broad_except_budget.py`，AST 扫生产包
  （`common/biz/hub/remote/train/trainer/worker`，`worker/scripts/` 豁免）里的
  `except Exception` / `except BaseException` / 裸 `except:`，按文件计数冻结在
  `tests/golden/except_baseline.json`（446 处 / 144 文件）。只拦增长；另加一条「基线只许调小」，
  某文件减少后不改基线就红，额度不会回流。`except (OSError, ValueError)` 等具体异常不计数。
- **被否决**：启用 ruff `BLE001` + per-file-ignores（446 处分布在 144 个文件 ⇒ 一份 144 行的
  忽略表，且 ruff 不会因为某文件减少而报「基线过期」；改 ruff select 还会牵动全量门的既有告警面）；
  直接把 G4 写成 `*_boundary` 白名单（现存 446 处里绝大多数不在边界上，白名单会先红成一片，
  等于要求一次性清零，违背 §8「冻结存量 + 禁止新增」）；同时冻结静默 `except ...: pass`
  （另一类问题，口径要区分「处理器类型」与「处理器体」，留到真正开始 P0-7 收敛时再加）。
- **后果**：新代码在内部路径上只能捕具体异常；确实位于进程边界时需 `logger.exception` 留痕。
  P0-7 的清理仍然没开始，但门禁把「越清越多」的旧循环切断了。

## §2026-11-14-nn-python-audit-round4-suppression-gate（G6：抑制符只减不增）

- **决定**：新增 `nn-training/tests/test_suppression_budget.py`，`tokenize` 只读 `COMMENT` token，
  统计 `# noqa: F401` 与 `# type: ignore`（含 `[code]` 形态），按文件冻结在
  `tests/golden/suppression_baseline.json`（25 处 / 11 文件；271 处 / 98 文件）。只拦增长，另加
  「基线只许调小」；排除 `.venv`/`tmp`/`weights`/`ipynb`/`__pycache__`/`node_modules`。
  扫全仓 `*.py`（生产 + 测试），因为两类抑制都在 tests 里最多。
- **被否决**：用 grep 计 `# noqa`（`tests/test_source_text_assert_budget.py` 等门禁用例在**字符串**里
  引用这些标记，grep 会把字样算成真抑制）；只统计生产包（98 个文件里 75 个在 `tests/`，
  而 tests 正是 `type: ignore` 增长最快的地方）；一并统计裸 `# noqa`（口径只对 G6 点名的两类
  负责，裸 noqa 的语义是全关，要单独一条规则）。
- **后果**：新增抑制符必须先解释；`trainer/dispatch_queue.py`（7 处 re-export）之类存量仍在，
  清理时基线随之调小。`mypy` 的 `warn_unused_ignores=false` 未动 —— 这仍意味着存量里可能有无用
  ignore，但门禁至少保证了「不再变多」。

## §2026-11-14-nn-python-audit-round4-private-assert-gate（G5：私有断言只减不增）

- **决定**：新增 `nn-training/tests/test_private_assert_budget.py`，AST 扫 `tests/**` 的每条 `assert`，
  判据表达式里出现私有属性（`x._private`，不含 `__dunder__`）或私有键下标（`d["_k"]`）即计数；
  按文件冻结在 `tests/golden/private_assert_baseline.json`（703 条 / 126 文件）。只拦增长 +
  「基线只许调小」。门禁自身的断言不读私有名 ⇒ 不占额度。
- **被否决**：把私有属性**赋值**（385 处）与 `from x import _name`（146 处）一起冻结（P0-8 的
  三种形态里，只有断言把实现细节钉成事实 API；赋值/导入的清理要连带改接缝，等 G5 存量真降下来
  再开第二条）；用 `grep "\\._"` 计数（注释/字符串里的 `._` 会假阳性，且无法区分断言与其他语句）。
- **后果**：新用例不能再靠读私有状态通过；`tests/remote/test_rollout_node.py`（43）与
  `tests/worker/test_rollout_volume.py`（46）这类大户仍是存量，迁移到 `seams` 公开查询时基线随之调小。

## §2026-11-14-nn-python-audit-round4-distribution-state（P0-5：distribution 的进程级状态对象化）

- **决定**：`common/distribution.py` 的三份模块级可变状态搬进两个显式对象 ——
  `RequestRegistry`（锁 / 在飞响应集合 / 收工标记集合，配 `begin_abort` / `clear_abort` /
  `is_aborted` / `track` / `untrack`）与 `RestartMemo`（跨代去重 memo，`seen` + `reset`）。
  方法名刻意避开 `register` / `unregister` / `clear`：`tests/test_reuse_ledger.py` 的「同名 ≥3 模块」
  账本会把新名字算成欠账（实测 `unregister` / `clear` 各把一格从 2 推到 3 ⇒ 账本 110 → 112）。
  模块顶层只留 `_REQUESTS` / `_RESTARTS` 两个默认实例；公开函数签名与语义逐字不变。
  `tests/test_python_shape_budget.py::L0_MUTABLE_ALLOWED` 删除 `common/distribution.py` 条目
  （名册只剩 `common/platform_utils.py::_UNREAPED`）。新增 `tests/common/test_dist_state.py`
  证明两个实例互不可见（这才是对象化的收益：测试要隔离就 new 一个，不必清模块私有集合）。
- **被否决**：给每个公开函数加 `state=` 参数（`abort_active_requests` / `_request` /
  `request_upgrade_guarded` 等十余个调用点全要穿参，收益是纯测试便利，代价是生产签名变长——
  与 `LogRouter` / `JsonlLedger` / `WeightsLedger` 的既有先例相反）；把 `UPGRADE_BRANCH` 与
  `set_upgrade_branch` 的 `global` 一起搬进对象（`UPGRADE_BRANCH` 是**文档点名的进程级锁存**，
  多处 `docs/` 与 TS 注释按该名叙述，本轮只收容器；`global` 仍在 G2 的 `GLOBAL_BASELINE` 里单项计账）。
- **后果**：`common/` 的模块级可变容器只剩 `platform_utils._UNREAPED` 一个；测试不再触碰
  `distribution._ACTIVE` / `_ABORTED_TAGS` / `_RESTART_SEEN`（现为 `_REQUESTS` / `_RESTARTS` 的公开属性）。
  状态仍是进程级（收工断连与跨代去重本来就是进程级语义），但**所有权**从此写在类上而不是模块词典里。

## §2026-11-14-nn-python-audit-round4-distribution-io-gate（G8：L0 出站副作用只减不增）

- **决定**：新增 `nn-training/tests/test_distribution_io_budget.py`，AST 按**点分调用名**
  数 `common/distribution.py` 的出站副作用：`net_http.racing_urlopen`（网络出口）、
  `urllib.request.Request`、`run_capture`（git ls-files）、`subprocess.run`（git cat-file），
  按原语名冻结（各 1，共 4）。只拦增长；某键减少未同步基线 = 红（额度不回流）。
  `_request(...)` 这类**内部漏斗**不计：它是同一处出口的复用，数它会把「调用点多」误报成「出口多」。
- **被否决**：只数总数（总 4 处不变但把 git 换成 HTTP 也算「没涨」——方向错了，B8 要搬就搬到 `remote/`）；
  把裸 `run` / `Request` 一律计入（任何对象的 `.run()` 都会撞进来）；用 `.py` 路径字面量定位源码
  （会撞 `tests/helpers/text_asserts.py` 口径 B 的已知限边，让源码文本断言基线无谓抬格——这里用
  `importlib` + `__file__` 定位，判据是整数计数而不是文本）。
- **后果**：`common/distribution.py` 的 IO 面从此有账（4 处），B8 把线路搬去 `remote/` 时逐项归零、
  删键即可；此前「往 L0 加一处 urlopen」不受任何门禁阻拦。

## §2026-11-14-nn-python-audit-round4-hub-boot-logging（P0-6 / B2 首批：hub 进程入口日志统一）

- **决定**：`hub/boot.py` 的 26 处 `print(..., flush=True)` 全部改走 `common/logutil`
  （`log_line` 的工厂 `tag_logger`）：`_log = tag_logger("hub-server", lambda: time)`、
  `_PUSH_LOG` / `_OFFLINE_PUSH_LOG` 两个派发器 tag；消息里冗余的 `[hub-server] ` 前缀随之删掉
  （tag 由 `log_line` 加）。`ensure_cluster_snapshot(log=…)` 那个 `lambda m: print(m, flush=True)`
  直接改成 `log=_log`（快照模块产出的 `[code-snapshot] …` 现在也带上时间戳）。
- **被否决**：只把 `time.strftime` 的字面量换成 `stamp()`（还有 21 处根本没有时间戳，混排不变，
  门禁计数也不降）；保留 `[hub-server] ` 前缀让 `log_line` 再加一次 tag（`[ts] [hub-server] [hub-server] …`）；
  顺手把 `hub/http_face.py` / `hub/admin.py` 一起迁（`http_face` 的 tag 是**按请求源 IP** 拼的
  `[hub-server 127.0.0.1]`，`tests/hub/test_hub_auth_d9_order.py` 直接断言这段文本 ⇒ 那一批要按
  「tag 里带变量」另设计，混在本次只会把两类语义搅在一起）。
- **行为变更**（有意）：原来无时间戳的 21 行现在带 `[HH:MM:SS]`，原来手拼时间戳的 5 行格式
  不变（`stamp()` 就是 `[HH:MM:SS]`）。读方全是 substring/regex（`tests/subproc_util`
  的 `listening on .*?:(\d+)`、控制台日志 tail），tests/hub 590 例全绿。
- **后果**：`tests/golden/print_baseline.json` 去掉 `hub/boot.py: 26` ⇒ 257 → 231 处、67 → 66 文件；
  G3 自检改锚 `hub/store_offline.py`（原锚已归零）。

## §2026-11-14-nn-python-audit-round4-store-offline-logging（P0-6 / B2 第二批：离线段回传日志）

- **决定**：`hub/store_offline.py` 的 12 处 `print("[hub-server] …", flush=True)` 改走
  `_log = tag_logger("hub-server", lambda: time)`（`common/logutil`），消息里手写的前缀删掉。
  与上一批（`hub/boot.py`）同一族：`hub-server.out` 的时间轴只留一份实现。
- **被否决**：复用 `hub/boot.py` 的 `_log`（跨模块 import 一个私有名会把两个入口绑在一起，
  且 `hub/store_offline` 是混入层、要维持 `ALLOWED_IMPORTS` 里那条向下边的最小面）；
  在混入里接一个 `log=` 形参（`OfflineRoundsMixin` 的调用面遍布 `hub/store` 与三个路由，
  收益是纯测试便利，代价是所有构造点加参数）。
- **连带改动**：`tests/hub/test_hub_job_store_split.py::ALLOWED_IMPORTS["hub.store_offline"]`
  登记 `common.logutil`（stdlib-only 的 L0 格式模块 ⇒ 层号不变）；G3 门禁补一条
  「基线只许调小」（与 G4/G5/G6/G8 同款），源码文本断言基线 1146 → 1147（+1）。
- **后果**：print 存量 231 → 219 处、66 → 65 文件；`hub/` 只剩 `admin.py` / `http_face.py`
  等按请求拼 tag 的面没迁（那一批要另设计「tag 带变量」的出口）。

## §2026-11-14-nn-python-audit-round4-admin-logging（P0-6 / B2 第三批：/admin 人工操作行）

- **决定**：`hub/admin.py` 的 8 处 `print(f"[{time.strftime('%H:%M:%S')}] [hub-server] …")` 改走
  `_log = tag_logger("hub-server", lambda: time)`。这批**输出逐字节不变**（`stamp()` 的输出就是
  `[HH:MM:SS]`），只是把「手拼时间戳」这份第二实现删掉——所以不需要改任何用例，也不需要像前两批
  那样声明行为变更。
- **被否决**：把 `hub/http_face.py` 一起做（它的 tag 带请求源 IP，`[hub-server 127.0.0.1]`，
  要另设计「tag 带变量」的出口 + 改 `test_hub_auth_d9_order.py` 的文本断言；混进来会让这一刀
  从「零行为变化」变成「有行为变化」）；把 `strftime` 全部清掉（`admin.py:1017` 那处是 **JSON 里的
  ISO 时间戳**，不是日志行，`stamp()` 不适用）。
- **后果**：print 存量 219 → 211 处、65 → 64 文件；`hub/` 的日志时间戳实现只剩 `common/logutil` 一处
  （除 `http_face` 的按 IP 拼 tag 那条线）。

## §2026-11-14-nn-python-audit-round4-hub-print-zero（P0-6 / B2 收口：`hub/` 整包日志单源）

- **决定**：`hub/` 剩下的 25 处 `print` 全部收口，只留 **1** 处 —— `hub/tunnel_ab_probe.py` 的
  `print(json.dumps(results))`：那是**一次性探针 CLI 的机器读交付物**，不是日志行，改走 `log_line`
  会往 JSON 前面塞时间戳，等于破坏读方契约。
  两种出口按「tag 是否带变量」分工：固定 tag 用 `_log = tag_logger("<tag>", lambda: time)`
  （`hub-server` / `smoke` / `backfill` / `ab`）；tag 里带请求源的行（`hub-server <ip> src=… via=cf`）
  直接调 `log_line(<tag>, msg)` —— 动态 tag 不包工厂，`logutil` 的 `[tag]` 位本来就够。
- **行为变更（有意，逐条）**：`hub/queue_discover` / `queue_offline` / `queue_resume` / `queue_scope`
  / `backfill_offline` / `tunnel_ab_probe` 里原本**没有时间戳**的行现在带 `[HH:MM:SS]`；
  `hub/offline` / `queue_claims` / `schedule` / `task_pack` / `smoke_loopback` / `tunnel_ab_probe`
  里手拼 `time.strftime` 的行格式**逐字节不变**（`stamp()` 就是 `[HH:MM:SS]`）。
- **被否决**：给 `tag_logger` 加 `tag_fn=` 之类的动态 tag 参数（`log_line` 已经是那个 API，
  再加一层只是名字不同）；把 JSON 那行也「顺手统一」（破坏机器读面）；只做「有手拼时间戳」
  的那几处（7 处没时间戳的行会留在同一个文件里继续混排，门禁计数也不降）。
- **连带改动**：`tests/hub/test_hub_queue_split.py` / `test_hub_routes_split.py` 的
  `ALLOWED_IMPORTS` 为受影响混入登记 `common.logutil`（stdlib-only 的 L0 ⇒ 层号不变）；
  `hub/tunnel_ab_probe.py` 原有的本地 `_log(msg)` 包装删除（否则与 tag logger 同名，mypy `no-redef`）。
- **后果**：print 存量 211 → **179** 处、64 → 53 文件；`hub/` 的日志时间戳只剩 `common/logutil` 一处实现。

## §2026-11-14-nn-python-audit-round5-remote-log-defaults（P0-6 / B2：`remote/` 的 `log=` 默认回调单源）

- **决定**：`remote/` 里 13 个文件、43 处「自己拼 `[HH:MM:SS] [tag] msg`」的日志出口全部改走
  `common/logutil`。两种形态分别处理：
  * `log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True)` 这种**形参默认值**
    → 模块级 `_log = tag_logger("<tag>", lambda: time)` + `log=_log`（默认值在 def 时求值，与原先每调用
    新建 lambda 的时机差异不可观测：`tag_logger` 的 clock 是取值函数，不是绑定时刻）。
  * `def _log(msg)` / `def _child_log(msg)` 这类包装 → body 换成 `log_line("<tag>", msg)`（不额外立工厂）。
  tag 按模块语义固定：`worker` / `hub` / `worker-serve` / `push` / `bc` / `payload` / `upload` /
  `offline-push`；`remote/worker.py` 需要两个（`worker` + `worker-supervisor`）。
- **行为变更（有意）**：`payload` / `upload` / `offline-push` 三处原本**没有时间戳**，现在带 `[HH:MM:SS]`
  （读方全是 substring 匹配，`tests/remote` 1881 例全绿）；其余 40 处输出**逐字节不变**（`stamp()` 就是
  `[HH:MM:SS]`，`tests/remote/test_wire_report.py` 那套正则照常解析）。
- **刻意不迁（写进基线、不是漏）**：
  * `remote/worker_proc.py` 两个默认回调 —— 该模块是**纯 stdlib 的 L0**（`remote_dag` 秩 0），import
    `common.*` 会当场破层号；
  * `remote/deliver_zip.py` / `run_loop.py` / `offline_deliver.py` 的 `IMPORT_JSON_MARK` / 结果行 ——
    机器读协议面，前面加时间戳等于改契约；`log=lambda m: print(m)` 这种**纯转发**（不加格式化）也保留，
    因为那正是「父侧已定格式，子侧不许再包一层」的意思。
- **连带改动**：三个 split 守卫的 `ALLOWED_IMPORTS`（`test_bc_job_split` / `test_hub_http_split` /
  `test_job_lifecycle_split`）登记 `common.logutil`；G3 自检的「基线文件数 ≥ 50」活性下限降到 25
  （那是扫描器坏没坏的判据，不是额度；文件数已从 63 掉到 44）。
- **后果**：print 存量 179 → **138** 处、53 → 44 文件；`remote/` 88 → 47。手拼日志时间戳的出口
  只剩 `remote/worker_proc.py`（L0 约束）与 `biz/` / `worker/` 的少量残留。

## §2026-11-14-nn-python-audit-round6-log-router-visibility（日志层不许自己静默）

- **决定**：`common/log.py::LogRouter` 里两处裸 `except Exception: pass` 改成「吞掉但喊一次」：
  镜像写失败（`_mirror`）与课日志句柄关闭失败（`close_sink`）各按 `mirror:<课>` / `close:<课>`
  去重喊一行 WARN。**异常类型不收紧**——日志路径的契约仍是「日志不得反向杀死训练」，收紧成
  `(OSError, ValueError)` 会让一个 sink 的编程错误直接崩掉训练进程，代价远大于收益。
- **为什么**：`pass` 让两种状态长得一模一样 —— 「这门课的日志文件正在丢行」和「这门课本来就没开
  日志」。真实的现场是：控制台一切正常、课日志少了半天，只有事后比对才发现。丢信息的是**可观测性**，
  不是功能。
- **去重而非每行喊**：日志坏掉时每行一条 WARN 会把控制台刷成噪音，把真正的问题淹没；按「课 + 失败
  种类」喊一次就够定位（重复出现说明没修）。
- **出口**：WARN 走 `common/logutil.log_line("log", …)`（含 `[HH:MM:SS]`），不再直接 `print`；
  `open_sink` 那条同族告警一并改走 `log_line` ⇒ `common/log.py` 的 print 由 3 降到 2（只剩
  `log()` 与 `say()` 两个真正的出口）。
- **顺带**：`worker/stream_collect.py` 的两处 `log=` 默认回调（`[stream]`）迁到
  `common.logutil.tag_logger`。`worker/iter_rollout.py` 的 `[iter]` **刻意没迁**：该文件已 999/1000 行，
  加一个 import + 一个模块级 `_log` 就撞 LOC 预算 —— 先拆再迁（§P0-2），不为了迁移而抬预算。

## §2026-11-14-g9-silent-except-gate-and-loop-util-retirement（捕了不做，也是事故）

- **决定 1（门禁 G9）**：新增 `tests/test_silent_except_budget.py` + `tests/golden/silent_except_baseline.json`，
  按文件冻结「宽泛捕获 + 体内只有一个 `pass`」（`except Exception/BaseException/裸 except:`，64 处 / 38 文件）。
  只拦增长 + 基线只许调小。**与 G4 分开**：G4 管「捕得宽」，G9 管「捕了不做」——`except Exception` 里
  写一行 `log_line(...)` 是处理，写 `pass` 是假装没发生，两者的现场可观测性完全不同。
- **决定 2（P1-11 死代码）**：删除 `worker/train/loop_util.py` 里随「本地 BC 连续训练入口」退役
  （`plan/single-resident-trainer` §1.4，2026-10-17）而失效的全部循环工具：`_emit` / `_TrainTee` /
  `heartbeat` / `auto_export_corpus` / `parse_val_loss_from_output`，以及只有它们用的 import 与常量
  （321 → 109 行）。**保留** `acquire_lock` / `cleanup_lock` / `course_lock_path` / `course_key_from_path` /
  `validate_course_name`（`trainer/` 与 `biz` 侧的课程锁仍全用它）与 `_pid_alive`（Windows 安全薄壳：
  `tests/common/test_pid_probe_windows_safe.py` 按名枚举四份壳必须委托唯一实现）。
- **为什么现在删**：文件头早就写着「去留归 S3 判」，而 S1 落地至今已一个月、`grep` 全仓零调用。
  死代码的代价不只是行数：它自带 `subprocess` / `threading` / `shutil` 的 import 面（读者会以为还有
  人跑子进程），还把 5 处 `except Exception: pass` 挂在生产面基线上，给 G4/G9 制造「本来就是这么多」
  的错觉。
- **代价与边界**：`docs/nn/remote-transport.md` / `engineering.md` 里关于 `loop_util` 心跳与 Tee 的
  叙述是**事故与决策记录**，不改（记录写的是当时事实）；`common/pid_probe.py` 里「2026-09-17 隐患」的
  引用同理。要考虑「现在还有没有这条路径」的人，应读 `python.refactor.md` 的状态表（已更新）。
- **顺带**：G3 134 处 / 42 文件、G4 440 处 / 143 文件（删掉的 1 处 print、6 处宽捕获同步从基线移除）；
  文本断言源码面基线 +7（新门禁用 AST 扫 `*.py`，整个文件的 assert 都落进口径 B —— 与 G3/G4/G5/G6
  同一已知限边，已在 `tests/test_source_text_assert_budget.py` 的注释表里逐笔记账）。

## §2026-11-14-job-control-notices-leave-a-trace（控制面失败：不致命 ≠ 不必说）

- **决定**：`remote/job_lifecycle.py` 的五个控制面通知（`job_started` / `job_ready` / `abandon_job` /
  `release_job` / `heartbeat`）与 `acquire_job` 的 `on_drop` 回调，从 `except Exception: pass` 改成
  `except Exception as e:` + 一行 `_log(...)`。**失败仍然不抛**（租约过期是设计好的兜底，行为一字未改），
  只是把「兜底正在被触发」说出来：行里带 job id、异常 repr、以及这条失败的**实际后果**。
- **为什么不是一律喊 WARN**：这些路径的错误语义是「尽力而为」，把它们升级成致命会改行为；但静默的代价是
  另一回事 —— `start` 没送到 ⇒ hub 侧 `computing_at` 不起算（掉队阈值晚起算）；`ready` 没送到 ⇒ 别的
  worker 优先级不降（P0 小包的全部作用）；`release` 没送到 ⇒ 这份活干等 30min 才回池、别的 worker 领不走；
  `heartbeat` 连续失败 ⇒ 租约走向过期，最坏是「活干完了 hub 派给了别人」。这些状态在控制台上此前**完全不可见**。
- **频度**：心跳周期 60s（`common/protocol.HEARTBEAT_SEC`）⇒ 持续不可达时一条/分钟，可接受；其余四个是
  每 job 至多一次的事件，不需要去重计数器（不给这条路径引入模块级状态）。
- **判据（行为用例）**：`tests/remote/test_job_control_visibility.py` 逐条钉「调用不抛 + 恰好一行提到该 job
  + 行里带得上异常类型」，文案用 `partition`/`startswith`/`endswith` 对账，不挂整句措辞。
- **基线**：G9 静默面 64 → **58 处 / 37 文件**（`job_lifecycle` 的 6 条清零，条目已删）。

## §2026-11-14-start-beacon-never-silently-misses（`computing_at` 打不上 = hub 会再派一份）

- **决定**：打完上次的 `job_started` 之后，把**同一个打点**在别处的三份拷贝一并改成留痕：
  `remote/train_core.py` 的 `_torch_ppo`（`on_ppo_start()`）与 `_paddle_ppo`
  （`spec.on_ppo_start()`）、`remote/push_dispatch.py::_mark_started`（`self.hub.start_job`）。
  **行为不变**（仍不抛）—— 只把「掉队阈值的时基没打上」说出来。
- **为什么这三处必须一起改**：`computing_at` 是 `store_scheduling` 里 `computing_elsewhere_since`
  的唯一时基。推模式下 GPU 机从不连 hub，`_mark_started` 是**唯一**会打这个点的地方；训练腿里
  `on_ppo_start` 是 worker 侧的那一发。任何一处静默失败都等价于「hub 以为没人算」，下场是多开
  一份备份 —— 而备份本身是合法策略，于是**现场唯一可见的症状是「备份数量莫名其妙变多」**，
  归因链断在最需要的地方。
- **判据**：`tests/remote/test_hub_push_dispatch.py::test_start_beacon_failure_is_logged`
  （假 hub 抛 `OSError` ⇒ 恰好一行、`endswith`/`startswith` 对账，不挂整句措辞）。训练腿的两处
  没有可调用的运行时入口（要真 torch / 真课程），由代码评审 + G9 基线兜（条目已删 ⇒ 退回静默即红）。
- **基线**：G9 55 处 / 35 文件（本轮 `train_core` 2 处 + `push_dispatch` 1 处清零）。
- **顺带**：`remote/job_round.py::_StartBeacon` 的注释原先写「`job_started` 本身是 `try/except pass`」，
  已改成「自己不抛、失败打一行」—— 注释描述的行为变了就得跟着改，否则下一刀会按错的假设动手。

## §2026-11-14-retire-dispatch-queue-reexport-surface（第二个 import 面砍掉，留一条并写明为什么）

- **决定**：`trainer/dispatch_queue.py` 的公共 re-export 面从 7 条削到 1 条。删掉的六条
  （`_record_agent_meta` · `pick_race_target` · `pick_tail_race` · `race_tier_ok` ·
  `register_inflight` · `run_rollout`）与附带的 `bun_version` 薄包装，消费方一律 direct import
  真家：`common.proc`（`bun_version` / `version_mm as mm`）· `worker.agent_meta`
  （`record_agent_meta as _record_agent_meta`）· `trainer.queue_local`（`run_rollout` + 竞速四件套）。
  历史名字留在**消费方**命名空间里（`batch_runner.bun_version`、`eval_dispatch.mm`），
  `monkeypatch.setattr(mod, "bun_version", …)` 这类接缝一条没动。
- **为什么 `bun_version` 包装可以删**：`common.proc.bun_version` 的缺省 `fallback` 本来就是 `"?"`，
  包装只是把默认值再抄一遍；`fallback` / `require_zero` 的两种历史口径仍在 `common.proc` 里按原样保留。
- **刻意留下的那一条**：`trainer/batch_plan.py` 用 `from trainer.dispatch_queue import mm`。它不是便利，
  而是 `tests/test_layering.py`「trainer/ 只有编排」那条的**唯一依据** —— batch_plan 除了这条边只依赖
  `common.*`。把它改成 `common.proc.version_mm` 会让门禁立刻红（且红得对：batch_plan 是纯逻辑，
  住错了包）。**删它的前提**是先决定 batch_plan 的归属（搬 `biz/`，涉及 150+ 处引用与 5 个测试文件；
  或留在 trainer/ 并显式豁免），不是顺手改一行。
- **记录这次暴露的东西**：一个纯逻辑模块之所以能在编排包里待着，靠的是**re-export 面伪造出来的一条
  依赖边**。这正是审计 §0.1「债务在边界不存在」的活样本；门禁本身没瞎，是它被一条转发养成了绿灯。
- **G6 基线**：`noqa_f401` 25 → **19 处 / 11 文件**（`dispatch_queue` 7 → 1）；
  `tests/common/test_common_layer.py` 的 `bun_version` 定义点从 3 处降到 2 处（私有断言基线 3 → 2）。
- **顺带**：`tests/trainer/test_batch_{runner,plan}_split.py` 的顶层 import 闭集已登记变动
  （`dispatch_queue` → `common.proc` + `worker.agent_meta`）。

## §2026-11-14-batch-plan-belongs-to-biz（把「假装是编排」的纯逻辑送回 B 层）

- **决定**：`trainer/batch_plan.py` → **`biz/batch_plan.py`**，`from trainer.dispatch_queue import mm`
  改回 `from common.proc import version_mm as mm`。`trainer/dispatch_queue.py` 的最后一条 re-export
  随之归零（G6 `noqa_f401` 18 处 / 10 文件）。
- **为什么是 `biz/` 而不是留在 `trainer/` 加豁免**：它的输入就是游戏领域的东西 —— 课程关卡文件的
  `stages[]`、`dashboard/src/evalboard/ladder.json`、语料目录；`biz/` 里已经住着 `course*` /
  `ladder_ledger` / `corpus_fp` / `reward*` 同族。豁免是「让门禁学会闭眼」，搬家是「让门禁继续睁眼」，
  而这正是审计 §0.1 说缺的那种改动。
- **为什么不是 `worker/`**：`worker/` 的口径是「支持本地 torch 训练的全部代码」，它一行 torch 都不碰；
  而且 `worker/` 的模块在 `tests/helpers/remote_dag.py` 有层号账本，搬进去要多登记一次「它为什么在
  L2」，收益为零。
- **代价与护栏**：`biz/` 那条「不得 import `remote|worker|hub|trainer`」现在直接盖住它（它只依赖
  `common.*`，本来就该受这条管）；`TRAINER_ORCHESTRATION` 快照少一员 —— 那条门禁的「少一个也红」
  正是本轮的第一推动力；消费方 8 个文件改 import 路径（`batch_eval` 的门面 `X as X` 保留，既有
  `from trainer.batch_eval import plan_units` 调用点一行不改）。
- **守卫留在原地的理由**：`tests/trainer/test_batch_plan_split.py` 不跟着搬 —— 它钉的两条边
  （`batch_eval` 门面恒等、`batch_runner` 入边调用点）都在 `trainer/`，搬走会让「谁在消费它」读不出来。
- **教训（写给下一次）**：三条门禁（G9、G6、G3）都栽在同一件事上 —— **自检锚点选了一个正要被治好的
  文件**，于是「门禁被治好」被误判成「扫描入口坏了」。锚点要选**还没退役**的样本（G9 现锚
  `common/log.py` + `worker/forensics.py`，G6 现锚 `remote/offline_boot.py`）。

## §2026-11-14-pump-lines-splits-its-two-failures（行泵：EOF 的回声要静，自己的 bug 要响）

- **决定**：`remote/child_proc.py::pump_lines` 拆成两条异常路径 —— **读流抛**（管道被子进程退出截断）
  静默不变；**回调抛**逐行兜住（不再让整段 `except` 带走泵线程）、继续读、收尾报一行
  「N 行被回调丢弃（第一次 <异常>）」。两个调用方（`upload_proc` / `payload_proc` 的 `_read_loop`）
  把各自的 `self._log` 传进来；模块新增 `_log = tag_logger("child", lambda: time)` 作缺省。
- **为什么值得改（不是洁癖）**：行泵是回传腿与下行腿**唯一**的输出读口，协议行（`@@upload@@` /
  `@@pf@@`）就在同一条流上。旧写法下，回调里任何一处 bug（新字段解析、`KeyError`、记账分支）都会
  让泵线程**静默停摆**：调用方既看不到后续日志、也收不到应答，只能干等到超时，而现场没有任何一行
  说明「泵死了」。这类故障的形状是「腿卡住」，归因成本极高。
- **行为变化的两处细节**：① 回调抛之后**继续读**（旧行为是彻底停）；② 收尾**一次性**报数（不逐行刷屏，
  因为一个坏解析器会对每一行都抛）。坏行不进账 ⇒ 协议层看到的是「缺一行」，比「线程没了」可诊断得多。
- **为什么读流那一侧仍然宽捕 + `pass`**：那条例外的成因（`ValueError: I/O operation on closed file` /
  `OSError`）就是「子进程退了」的回声，真正的信号是 EOF 本身，调用方据此走降级 —— 这里留痕只会把
  一次正常收线说成故障。两种异常的**成因**不同，所以口径也必须不同；这正是 G9 门禁（只数宽捕 + `pass`）
  抓不到、但 `python.refactor.md` §P0-7 一直在说的那类「宽捕掩盖语义差异」。
- **G4 抬格 1 笔**：回调边界只能宽捕（用户传入的解析代码抛什么类型这里不知道），记账写在
  `tests/test_broad_except_budget.py` 文件头的表里：441 处 / 143 文件。G9 静默面不变（55 处 / 35 文件）：
  同一函数里被留下的那条宽捕 + `pass` 是**对的**，不该为了好看而消失。
- **判据**：`tests/remote/test_child_proc_pump.py` 两条 —— 回调对第 2/4 行抛时好行一条不丢、恰好一行
  报数（`endswith` + `startswith` 对账）；读流截断时**零**日志（静默是对的，别把正常收线变成告警）。

## §2026-11-14-warn-unused-ignores-and-pinned-platform（打开「无用 ignore」检查，并把平台钉死）

- **决定**：mypy 配置 `warn_unused_ignores` 由 `false` 翻成 `true`，**同一笔**里加 `platform = "linux"`。
  翻开关当场指出的 111 条无用 `# type: ignore`（54 个文件）一并删掉（含 3 条「整条删了会新红」的回修）。
  G6 的 `type: ignore` 存量 271 处 / 98 文件 → **163 / 68**（`tests/golden/suppression_baseline.json`），
  扫描健康度地板随之下调 200/80 → 150/60（是地板不是额度）。
- **为什么值得改**：开着 `false` 时，`# type: ignore` 一落笔就再也不会被质疑 —— 它本来是「这里类型系统
  管不到」的**证据**，过期之后却成了「这里类型系统管不到」的**假证**。实测 41% 的 ignore 已经什么都不压：
  下一个人照着它跳过三行代码，改的人以为自己做了件安全的事。翻开关才有资格说「剩下 163 条都是承重的」。
- **为什么必须成对钉平台**：`warn_unused_ignores` 判的是「按哪套 stubs 分析」。`os.sysconf` /
  `os.fork` / `ctypes.windll` 在 Linux stubs 里不存在 ⇒ `[attr-defined]` 是**必需**；在 Windows stubs 里
  存在 ⇒ 同一条注释被判**多余**。只翻开关 = 同一个仓库在两台机器上只能有一台绿（本仓开发机是 Windows，
  门禁跑 Linux）。钉 `platform = "linux"` 让判定与门禁同源。
- **被否决的备选**：① 维持 `false` 只做人工普查 —— 普查没有截止条件，就是不做；② 按平台写两套
  `# type: ignore`（`sys.platform` 分支 + 各写一次）—— ignore 是行级的，两个平台的分支写在两行上，
  各自只需一个码，但会把「跨平台」写进代码形状里，比钉配置贵得多。
- **实操教训（写进 `python.refactor.md` §P1-4）**：多码 ignore（`[attr-defined, assignment]`）在 false
  下是「无害的多余」，在 true 下被逼着二选一 —— 只留本平台真实触发的那个码；以及 mypy 报某条
  ignore「unused」不等于**整条**可以删（`[import-not-found, import-untyped]` 里可能只有一半过期），
  删完必须再跑一遍 mypy。

## §2026-11-14-colab-unassign-must-say-when-it-fails（停机调用失败要留下「机器没停」）

- **决定**：`remote/worker_proc._release_cloud_machine` 的 Colab 兼容路径拆成两段 —— **探测**
  （`import google.colab.runtime` / `import IPython` + `get_ipython() is None`）仍静默；**调用**
  `runtime.unassign()` 失败则留一行：异常 `repr` + 「机器未释放」+ 人能执行的动作（等 keepalive 读哨兵 /
  手工断开宿主会话）。G9 该文件 2 → 1（55 → 54），G4 同笔 +1（442，记账在
  `tests/test_broad_except_budget.py` 表头）。
- **为什么（不是洁癖）**：旧写法把两件事罩在同一条宽捕 `pass` 下，而日志**先**打印
  「检测到 Colab kernel 环境 → 直接调用 runtime.unassign()」。`unassign()` 抛了之后没有下文 ⇒ 读日志的人
  得到的结论是「停机已经安排好了」，实例却还在计费 —— §386 的全部意义正是「worker 退出 ≠ 停机省钱」，
  这条静默把该结论反过来写了一遍。
- **为什么探测必须留静默**：非 Colab（Kaggle / CI / 本机）根本没有 `google.colab`；worker 跑在
  supervisor 拉起的**子进程**里时 `get_ipython()` 永远是 `None`。这两件是**常态**，留痕等于每次停机都
  喊一次狼来了，反而训练读者忽略真正的告警。
- **判据**：`test_remote_hotswap.py` 两条 —— 失败路径必须恰有一次 `unassign` 调用 + 日志含 `unassign() 失败`
  与异常文本（`503`）+ 给出动作；成功路径**不得**出现「失败」（留痕只跟失败走）。

## §2026-11-14-eval-heartbeat-failures-are-visible（心跳写不进去要说一声）

- **决定**：EvalBoard runner 心跳的三层都改成「失败仍不抛，但按 key 喊一次」——
  ① `worker/eval_heartbeat.write_state` 的落盘 `OSError`（`warn_once(f"eval_heartbeat:{落点}", …)`）；
  ② `trainer/loop_lifecycle._evalboard_idle` 开窗（key `eval_heartbeat:open`）；
  ③ `trainer/loop_dispatch._evalboard_yield` 关窗（key `eval_heartbeat:close`）。
  G9 54/35 → **52/33**（两个文件从基线消失）。
- **为什么**：静默的后果不是「少写一个 JSON」。`dashboard/data/evalboard/runner_state.json` 是 console
  判断「训练空闲 / 忙碌中已等 N 分钟」的**唯一**输入；写不进去 ⇒ 屏幕上永久停在旧值，而训练侧一切
  正常。读者会去查训练、查 hub、查 GPU，唯一不会去查的是「那个文件根本没写」。这正是 G9 口径里
  「失败即信息丢失」那一类，不是「该有替代路径」那一类。
- **为什么必须去重**：开窗/关窗是**每轮**都写的，热路径逐次喊会把控制台刷成噪音；`LogRouter.warn_once`
  按键只喊一次，落盘失败的 key 还带路径（不同课/不同落点各喊一次）。
- **为什么保留「不抛」**：心跳是观测面，绝不能反过来打断训练主链 —— 这条契约不变，改的只是「留不留痕」。
- **判据**：`tests/trainer/test_loop_heartbeat_visibility.py` 三条（正常路径记录到
  `window_open=False` + 关窗时刻；两条失败路径各恰好一行、且认领/开窗照做）+ 
  `test_eval_heartbeat.py::test_write_failure_is_reported_once_and_never_raises`（落点是文件 ⇒
  mkdir 失败，两次写只喊一次，且不抛）。两条都**不读私有字段**（G5）：走延迟 import 的 DI seam 验入参。

## §2026-11-14-deliver-forwarder-reuses-pump-lines（补传腿的转发线程不再自己写一遍行泵）

- **决定**：`remote/deliver_proc.DelivererProcess._forward` 改为 `pump_lines(stream, on_line, log=self.log)`，
  `on_line` 先判 `boot ok` 置 `_boot`、再 `self.log(msg)`（顺序与旧实现逐字一致）。同时补上
  `pump_lines` 自己的一处漏：**报数那行也是往 stdout 写**，出口关了会抛，而它在 try 之外 ⇒
  把整条行泵带走。现在 `except (OSError, ValueError)` 到此为止（与 `LogRouter.warn_once` 同一口径）。
  G9 52/33 → **51/33**（`deliver_proc` 3 → 2），G4 442 → **441**（该文件 8 → 7）。
- **为什么**：这段手写循环与 `pump_lines` 是同一件事的第二个实现，且复制的是**修之前**的那一版：
  读流截断（EOF 的回声，该静）与回调自己抛（我们的 bug，该响）共罩一条 `pass`。后果具体：
  `self.log` 最终往 stdout 写，父进程 stdout 被关/管道断掉时 `print` 抛 `ValueError` ⇒ 转发线程停摆，
  而 `_boot` **只有这条线程会置位** ⇒ 子进程明明已经打了 `boot ok`，父侧仍然只看到
  「等 `boot ok` 超时（5s）」并 sticky 降级到进程内线程模式。诊断成本极高：现场既没有那行 `boot ok`，
  也没有任何一行说「转发线程死了」。
- **为什么不各自修一遍**：行泵的口径（哪一侧静、哪一侧响、怎么报数）已经写进 `pump_lines` 的
  docstring 与 `tests/remote/test_child_proc_pump.py`；第二份实现迟早再漂一次。搬过来 = 口径单源。
- **判据**：`test_offline_deliver_proc.py::test_forward_keeps_watching_after_log_callback_raises`
  （日志回调对每一行都抛，流里 `boot ok` 在第二行 ⇒ `_boot` 仍必须置位；旧实现在第一行就死了，先红后绿）
  + `test_child_proc_pump.py::test_summary_log_that_itself_raises_does_not_escape`（喊话本身失败不逃出）。

## §2026-11-15-ledger-values-compare-ledger-to-ledger（落账值对落账值，别拿相对容差碰原始 float）

- **根因（2026-11-15，用户点名查的偶发红）**：`meta["best_smoothed_val_loss"]` 写的是
  `round(best_smoothed, 4)`，而 `out["best_smoothed_val_loss"]` 是未舍入的原始 float；两条双生用例
  （`test_bc_paddle` / `test_bc_epoch_resume`）用 `pytest.approx(原始值)` 对账 —— 默认容差是
  **相对** 1e-6，在 25 量级只给 **2.6e-5** 余量，而 4dp 舍入的最大误差是 **5e-5**。于是「原始值的
  尾巴是否恰好落在 4dp 网格外」决定这条断言红不红 —— 而 epoch 曲线逐进程不同（paddle 首次调用的
  状态差异），所以它是**偶发**，不是环境问题。实测：12 个新进程各跑一次，旧写法 **3/12 红**
  （现场值 `25.586440000000003 → 25.5864`），新写法 0/12。
- **决定**：两边都取 4dp 再比 —— `meta[...] == round(out[...], 4)`。这是**精确相等**，不需要任何
  容差，比原来的 `approx` 更严，也不再有触发条件。
- **为什么不是「把容差放大到 abs=1e-4」**：那只是让边界更远（仍是拍一个够不够的数）；落账口径本来
  就是 4dp，两边对齐后误差恒为 0 —— 对账的判据应该是「同一个量」，而不是「差不多」。
- **另一侧同样修**：`meta["best_val_loss"] == round(out["best_val_loss"], 4)`（同一类量、同一类
  舍入），`history[...]` 与原始 float 的对账继续用 `abs=1e-4`（那是数据面精度的既有约定）。
- **判据**：`tests/worker/test_bc_epoch_resume.py::test_ledger_value_must_be_compared_at_ledger_precision`
  —— 把现场值钉下来：`raw != round(raw, 4)`（前提）、`round(raw, 4) != approx(raw)`（这就是那个
  flake）、`round(raw, 4) == round(raw, 4)`（新口径）。谁把对账改回 `approx(原始值)` 这条就红。
- **附带发现**：`bc_core.select_best_epoch` 在「所有窗口都非有限」时返回 `0`（不是 `None`）——
  `NaN < inf` 恒 False ⇒ `best_at` 留在初始值。本机 12+ 次训练没观察到 NaN（6 样本 shard 上
  val_loss 会剧烈跳动但有限），所以**不是**这次 flake 的成因；已作为独立改动于同日修掉，见
  §2026-11-15-nonfinite-window-is-not-a-candidate。

## §2026-11-15-nonfinite-window-is-not-a-candidate（NaN 窗口不是候选，别让选取给出 epoch 0）

- **决定**：`worker/train/bc_core.py` 新增 `is_improvement(candidate, best)`（有限且严格更小），
  `select_best_epoch` 与两条训练循环（`bc.py` / `bc_paddle.py` 双生）都用它；`select_best_epoch`
  在没有有限窗口时返回 **None**（= 「交最后一轮」），不再是 0。
- **为什么**：`NaN < inf` 恒 False，于是旧实现里「所有完整窗口都含 NaN」会让 `best_at` 停在初值
  **0** 并原样返回。0 不是合法 epoch（该函数 1-based），而调用方对 `None` 有一套既定语义
  （「不足窗口轮次 ⇒ 交最后一轮」）。于是出现过这种分叉：训练循环那条 `if smoothed < best_smoothed`
  同样不成立 ⇒ `best_state is None` ⇒ 走 fallback 交最后一轮（比如 6）；按 `history` 重算的
  `select_best_epoch` 却给 0 ⇒ 交付 epoch 与账本对不上，且 `assert delivered >= SMOOTH_WINDOW`
  直接红。
- **为什么判据要单列一个函数**：选权重这条判据有两个消费者 —— 纯函数（可被测试/对账脚本按
  history 重算）与训练循环（流式）。它们各写一遍比较，就会在 NaN / inf 这种边界上漂；
  `is_improvement` 让「有没有改善」只有一个定义，双生两侧也只剩一个 import 差异。
- **为什么不把 NaN 当成「很差」**：`float("inf")` 才是「很差」，NaN 是「这个数算不出来」——
  把它排进候选顺序里等于给它编了一个名次；跳过才是诚实的（inf 也不该当选）。
- **判据**：`tests/worker/test_bc_early_stop.py`
  `test_select_best_epoch_skips_non_finite_windows`（前 5 个窗口吃 NaN、只有最后一个有限 ⇒ 选 10，
  且**不是 0**；旧实现返回 0，先红后绿）与
  `test_select_best_epoch_with_only_non_finite_windows_is_none`（全 NaN / 全 inf ⇒ None）。
- **落盘面（同日补齐，见 §2026-11-15-non-finite-json-is-null）**：当时我写这里说「全 NaN 时 meta
  会写出 `NaN`」——**这条判断是错的**：两条训练核都做了 `meta = sanitize_json(meta)`，`NaN` 早已落成
  `null`。真正的洞是 **±Infinity**（旧 `sanitize_json` 只判 `isnan`），已修。

## §2026-11-15-non-finite-json-is-null（weights.json 里不许有 NaN / Infinity 字面量）

- **决定**：`worker/train/bc_core.sanitize_json` 由「NaN → None」扩成「**非有限**（NaN / +Inf /
  -Inf）→ None」。两条训练核（`bc.py` / `bc_paddle.py`）本来就把整份 meta 过这个函数，所以
  `best_val_loss` / `best_smoothed_val_loss` / `history.*` 一并变成标准 JSON 的 `null`。
- **为什么**：`json.dump` 默认写出 `NaN` / `Infinity` / `-Infinity` 三个**非标准**字面量；TS 端
  `JSON.parse` 遇到直接抛错 —— 失败形态是「文件打开就崩」，不是「数值不准」。`weights.json` 是
  TS 运行时与 dashboard 都读的落盘面。
- **先确认了「NaN 那条早就没有」**：`test_bc_epoch_resume.py::test_bc_train_writes_null_when_val_loss_is_not_finite`
  用**真实切分**造 NaN 验证集（先 `make_loaders` 取 val 下标，只把那些行写成 NaN ⇒ 训练行梯度干净、
  权重有限、文件照写），跑完后用 `json.loads(raw, parse_constant=...)` 拒绝非标准字面量：NaN 场景
  在改动**之前**就是 `null`（`meta = sanitize_json(meta)` 的既有功劳），所以这半条是**补用例**、
  不是补代码。
- **为什么用 `parse_constant` 而不是 grep 文本**：权重是 base64，随便就会撞出 `"NaN"` 子串（第一版
  断言就是这么假红的——它指着 base64 里的一段说「这里有个 NaN」）。判「标准 JSON」只能靠解析器。
- **判据**：`tests/worker/test_bc_early_stop.py::test_sanitize_json_turns_every_non_finite_into_none`
  （NaN/±inf 以及嵌套结构 → None；旧实现 inf 原样通过，先红后绿）+
  上面那条端到端（NaN 验证集 → 文件里那两个字段是 `null`，且整份文件能被严格解析）。
- **未动**：`out`（返回值，进程内）与归档文件名仍带原始 `nan`/`inf`（文件名形如 `..._valnan.json`）；
  返回值不是落盘面，调用方（`trainer/bc_loop` / `remote/bc_job`）读 file 且对 `None` 有 `or 0.0`
  兜底。文件名只是可读性瑕疵，没有消费者解析它。

## §2026-11-15-json-writes-sanitize-nonfinite（非有限值在**写入口**收口，不在几十个调用点收）

- **决定**：`sanitize_json`（非有限 float → `None`）从 `worker/train/bc_core.py` 提到
  **`common/stats.py`**（与 `num_or_none` 同族：都是「观测字段的口径」），并在全仓两个 JSON
  **写入口**上应用：`common/fs.atomic_write_json` 与 `JsonlLedger.append`。`bc_core` 留转发名
  （`bc.py` / `bc_paddle.py` 的调用点一行不改）。
- **为什么在写入口**：`json.dumps` 会把 NaN / ±Infinity 写成**非标准**字面量，TS 端 `JSON.parse`
  直接抛错 —— 失败形态是「文件/账本打开就崩」。而 NaN/inf 在训练侧是**真实形状**（val_loss 发散、
  gnorm 爆炸、A-eval 的 delta 算不出来）。逐个 `json.dump` 调用点加过滤是 O(调用点) 且新代码必漏；
  两个 Chokepoint 是 O(1)，且新写者也自动继承。
- **为什么不改 `num_or_none`**：它的语义是「数值化」，非有限值原样透传是**刻意的**
  （`tests/common/test_stats.py::test_non_finite_is_still_a_number` 钉着），判定面（
  `loop_guards_trip._is_nonfinite` → 熔断）要吃 NaN。落盘面与判定面口径不同，这正是本条的理由。
- **代价（已知）**：行/文件的编码多走一次递归（metrics 行是小字典，实测全量 6060 用例无感）。
  顺带把 tuple 变成 list（`json.dumps` 本来就输出 list，逐字节不变）。
- **判据**：`tests/common/test_json_nonfinite.py` —— 两个写入口各一条（写 NaN/±inf 后**严格解析**
  回来必须是 `null`；`parse_constant` 是 `json.loads` 对那三个字面量的唯一钩子，grep 文本会假红）、
  一条口径单源（`bc_core.sanitize_json is common.stats.sanitize_json`）、两条纯函数边界。
  前两条在改动前确认**红**（临时还原写入口跑的：2/7 failed）。
- **未收口（另列）**：绕过这两个入口的直写仍在 —— `worker/data/weights_io.save_weights_json`
  （调用方已自带 sanitize；且它对非有限**权重**是拒绝写出，语义不同）、`remote/deliver_wire.py`
  的自定义编码（已自带 `math.isfinite` 过滤）、`remote/offline_boot.py` 的生成物拼接。它们各有
  自己的口径，不属于「漏过滤」。

## §2026-11-15-narrow-catch-is-the-third-disposition（窄捕是第三种处置，不是「留着 pass」）

- **背景**：G9（宽捕 + 只 `pass`）在清理「控制面尽力而为」路径时的两种处置是 ①留痕 ②带理据留基线。
  2026-11-15 第八批（`remote/lifecycle.py` 4 处 + `remote/colab_bc.py` 4 处）确认了**第三种**：捕获面
  本来就只该是具体的 —— 遗言读数 `/proc`（OSError）/ NVML（RuntimeError）/ `statvfs`（OSError）、探测腿
  `subprocess.run`（OSError / SubprocessError）。此时 `except (OSError, ValueError): pass` 不属「宽」
  也不属「静默」：它让**本模块自己的 bug**（AttributeError / KeyError / 拼错的名字）照常抛出，这正是
  门禁要的那半条命。判据从「语法形状」升级为「这次失败**能由哪种异常解释**」。
- **代价（实测抓到）**：窄捕当场暴露了两个被宽捕掩盖的东西 ——
  ① `tests/remote/test_lifecycle.py` 的 `LC.os` 假 `os` 少了 `stat`，`readings` 一直在**静默降级**
  （磁盘项恒为空）而用例看不见；补 `stat=os.stat` 后读数恢复；
  ② `_rss_gb` 的 `Path` 拆解、`shutil.disk_usage` 的各自失败面从此分得清。
- **例外（不窄捕，改留痕）**：`remote/colab_bc.load_state` 读不了（现场只看到「进度莫名归零」，与
  disk 满/半行 JSON 长得一样）与 `remote/lifecycle` 的 **SIGTERM 自投失败**（进程活着而 hub 当这台已
  下线 = 重复计费）。这两处是「信息丢失」而不是「读不到」，宽的意义在于**异常族未知**，窄会漏掉真正的
  成因；所以保留宽度、只加一行。第三方的 `mount_drive` 同样保留宽捕 + 日志（`drive.mount` 抛什么这里
  不知道，且义务是「失败仍继续」）。
- **读数**：G9 51/33 → **44/31**（扫描地板 50 → 40，算术 = 51−7）；G4 441/143 → **433/142**
  （lifecycle 4→0、colab_bc 5→1；抬/降都记在 `tests/test_broad_except_budget.py` 表头）。
- **判据**：`tests/remote/test_lifecycle.py` +3 条行为用例（缺 `/proc` 与读不到盘 ⇒ 读数仍成形且 `rss_gb`
  为 None；NVML 抽风 ⇒ 只丢 `gpu`；自投失败 ⇒ 不抛 + 恰一行 + 上报仍先发）。文本形态断言 1500 → 1501
  （理由记在 `tests/test_source_text_assert_budget.py` 的 `BASELINE_FORM` 上方）。

## §2026-11-15-no-wall-clock-upper-bound-for-"does-not-block"

- **判据**：「X 不阻塞调用方」这类契约**不能**写成「耗时 < 常数」。2026-11-15 的活例：
  `tests/remote/test_error_report.py` 用 `elapsed < 0.25s`（慢 hub 1s）钉 `report_error` 不阻塞，
  在 16 路 fork 满载时同一份代码量到 **2.019s** 而红 —— 红灯说的是「机器忙」，不是「代码同步
  等了网络」。同一晚它又绿了两次，于是这条断言同时具备「假红」与「掩盖」两种毒性（人学会无视它）。
- **替代判据（事件）**：把「发送」那一发换成可控桩（进去置位 → 测试放行 → 打完置位）。
  ① 调用方**返回时**桩仍在里面（`finished` 未置位）⇒ 证明没等网络；② 放行后 `finished` 置位
  ⇒ 证明「不阻塞」不是「什么都不发」；③ 调用方若同步实现，`done` 永不到达，`wait(10s)` 把它
  变成一次可读的红。**唯一的墙钟是 10s 的挂起兜底（liveness），负载只改变先后、不改变对错。**
- **验证**：临时把 `report_error` 的线程改成就地 `_send()`（同步实现）⇒ 新用例红（10s 后
  `done` 未置位），还原后绿。即判据**仍抓得住**原来的回归，只是不再因机器忙而红。
- **口径**：`tests/test_no_sleep_as_sync.py` 的 `# timing-ok` 标注随之删除（该文件现在没有墙钟
  上界判据了），模块 docstring 里「墙钟判据只有一处上界」那句同步改写 —— 注释与判据不能各说各话。

## §2026-11-15-wall-clock-contract-bounds-are-a-transition-marker

- **决定**：`# timing-ok: 契约上界` 只允许**过渡**存在。阈值 ≤ 0.5s 且语义是「立即返回 / 不许阻塞」
  的上界断言，一律改成与负载无关的判据；批量扫过一遍（8 处），并把这四种形状写进
  `tests/test_no_sleep_as_sync.py` 文件头第三条规则。
- **四种形状**（可复用）：
  ① **事件门** —— 阻塞的那件事换成「进去置位 → 测试放行 → 打完置位」的桩，返回时桩仍在里面
  = 没等它；② **调用计数** —— 「问一句就答」= 恰一次，轮询实现会 N 次；③ **炸弹** —— 把
  `time.sleep` / 同步物化那条路换成 `pytest.fail`；④ **状态** —— `is_alive()` / `inflight_it()`
  / `not finished` 这类事实。共同点：判据里没有「多久算快」。
- **为什么**：`elapsed < N` 量的是「机器够不够快」，而契约说的是「调用方等没等」。2026-11-15
  同一条 `elapsed < 0.25s` 在 16 路 fork 满载时量到 2.019s、单跑 0.08s —— 假红教人无视门禁，
  比没有断言更糟。逐个替换后，真回归仍红：把 `report_error` / `CloudEvalRunner.submit` 的
  后台腿临时改回同步，新用例都会红（10s liveness 兜底把它变成可读的失败，而不是挂死）。
- **保留的三处 `契约上界`**：`test_rollout_scratch` ×2（阈值按被测自己的 `DRAIN_CEILING_SEC`
  推导，实测 5× 余量）、`e2e/test_autonomous_round_flow_e2e`（桩每轮固定 0.07s，阈值 1s）。
  审计过、不假红；新加的不走这条。

## §2026-11-15-network-error-surface-is-a-named-tuple

- **决定**：`remote/http.py` 导出 `NET_ERRORS`（`OSError` / `http.client.HTTPException` /
  `WireSlowError` / `RetryableError` / 防御性 `RuntimeError`），`remote/job_lifecycle.py` 的十条
  控制面通知改用它做窄捕（G4 11 → 1；剩下那处是 `on_drop` 回调边界，异常族由调用方决定）。
- **为什么这不算放松**：宽捕把两类东西抹平了 —— 「连不上」（网络层，租约兜底是设计好的语义）与
  「我们自己的 bug」（`AttributeError` 拼错字段 / `TypeError` 签名漂了 / `KeyError` 形状变了）。
  后者的现场表现是「hub 不可达，本轮照跑」，于是真因永远进不了 hindsight。
- **为什么 `RetryableError` 在内、`ProtocolError` 在外**：前者就是本仓对「瞬时网络失败」的分类
  （5xx / 抖动 / 传输损坏），重试即可能修好；后者是 4xx/字段级**确定性拒绝**，调用方要能分辨它
  去走「首写胜出 / 放弃」那条路 —— 吞掉它等于把确定性失败降级成网络抖动。
- **判据**：`test_job_control_visibility.py::test_programmer_error_is_not_disguised_as_a_network_failure`
  （五条通知逐条：`_request` 抛 `AttributeError` 必须穿出去）。实测把 `job_started` 改回
  `except Exception` ⇒ 该参数化用例红，日志里写着「hub 不可达」，还原后绿。
- **代价（已知）**：控制面通知里若有真正的编程错，worker 这一轮会崩而不是继续 —— 这是刻意的
  （AGENTS §G4 的原始理由：宽捕顺带吞掉「真正的编码 bug」）。崩溃点带完整 traceback，而旧行为
  只留一行「没送到」。

## §2026-11-15-net-errors-tuple-lands-in-errors-leaf-composed-per-transport

- **决定**：`NET_ERRORS` 的**基底**（`OSError` / `RetryableError` / 防御性 `RuntimeError`）落
  `common/errors.py`，由 `common/protocol.py` 以 `NET_ERRORS as NET_ERRORS` 门面转发；传输层各自
  追加自己会撞上的形状 —— `remote/http.py` 追加 `http.client.HTTPException` + `WireSlowError`，
  `remote/hub_http.py` 追加 `http.client.HTTPException`（同批把薄壳 10 处 `except Exception` 全改窄，
  G4 423 → 413、G9 44 → 42）。
- **为什么不把 `HTTPException` 放进基底**：`common/errors.py` 的「**零 import** 叶子」是硬契约
  （`test_protocol_split.py::test_errors_is_a_leaf_module`，两边都向下依赖它才不会成环）。给它加
  `import http.client` 能把门禁改绿，但那是为省一行去拆一条结构不变式。`BadStatusLine` /
  `IncompleteRead` 本来就是**传输**撞上的形状 ⇒ 该由传输壳点名。
- **为什么经 `common.protocol` 门面**：`remote/hub_http.py` 的依赖闭集是刻意的（薄壳 = 「可注入的
  单点」，导入期不攥住网络栈）。窄捕需要一条共享的错误面，就借已有的门面，而不是给薄壳开新依赖边
  （`http.client` 一个 stdlib 名字例外，已在该测试的 `ALLOWED_IMPORTS` 旁记账）。
- **判据**：`test_hub_http_split.py::test_probe_does_not_disguise_a_programmer_error_as_transient`
  （`_request` 抛 `AttributeError` ⇒ `probe_job_result` 必须穿出去）。实测把那一处改回
  `except Exception` ⇒ `DID NOT RAISE AttributeError` 红，还原后绿。
- **代价（已知）**：同一条元组在两处组装，若将来新增成员要记得两处都追加。接受：两处各自负责的
  层不同（`remote/http.py` 还有 `WireSlowError`），合并回基底就得先破叶子契约。

## §2026-11-15-probe-return-values-are-conclusions-not-a-catch-all-boundary

- **决定**：`common/distribution.py` 的探活 / 探测 / 取消出站点窄捕到 `NET_ERRORS`，探活类再并上
  `ValueError`（`PING_ERRORS`）：`node_ping` / `push_node_ping` / `request_upgrade` / `cancel_task` /
  `probe_weights_cached` / `weights_cached_on_node` / `_poll_result`。G4 413 → 404、G9 42 → 41。
- **判据**：这些函数返回 `None` / `False` 不是「这里没什么可报」而是**结论**——「这台节点本轮不可用」，
  下游据此把它排除出分发、份额落回本地。宽捕于是把「连不上」与「我们自己的 bug」变成同一个结论，
  且后者不留痕（同族事故：`hub_http` 探针把 `AttributeError` 读成 `PROBE_TRANSIENT`，轮询环永远
  「还没好」）。
- **为什么探活类要多一个 `ValueError`**：`200` 但 body 不是 JSON（`JSONDecodeError` /
  `UnicodeDecodeError` 都是 `ValueError`）确实属于「这台节点此刻不可用」，与连不上同档；而看字段的
  错（`AttributeError` / `KeyError`）仍照抛 —— 多出来的那个成员只放过「读不出来」，不放过「形状变了」。
- **留下的两处**（第三条边界，不是偷懒）：`abort_active_requests` 的 `_closer` 线程对**任意**
  `HTTPResponse` 尽力 `close()`（异常族由对端实现决定，且它在 daemon 线程里，抛出去也没人接）；
  `fetch_task` 对「非 `DistError` 的确定性失败」的兜底（解包错种类不定，且要保留真实 message，
  注释里写着「不标 transient」）。
- **判据落地**：`tests/common/test_dist_ping_errors.py`（垃圾回执 → `None`；`AttributeError` /
  `KeyError` 穿出）与 `test_dist_common_poll.py::test_poll_result_lets_a_programmer_error_through`。
  实测把对应处理器改回 `except Exception` ⇒ 前者 `DID NOT RAISE`、后者抛 `deadline exceeded`，还原绿。

## §2026-11-15-notebook-cells-do-not-call-every-failure-an-unreachable-hub

- **决定**：`remote/notebook_runtime.py` 的 7 处宽捕收窄（`NET_ERRORS` / `PROBE_ERRORS` /
  `PackageNotFoundError` / `OSError`），并修掉 `_run_probe` 的 `except BaseException`（现在
  `KeyboardInterrupt` 穿出去）。G4 404 → 397、G9 41 → 40。
- **为什么**：这七处的旧文案是「FATAL: hub 不可达（X）——检查隧道 URL 是否过期」。它把**所有**失败
  都说成网络问题 ⇒ 真因是本模块的 `AttributeError` / `KeyError` 时，最后一线索反而把人指向隧道
  （一段言之凿凿的错方向，比带 traceback 的崩溃贵得多）。网络层才该说网络层。
- **`_probe_cuda/_paddle` 的 Ctrl-C**：旧代码 `except BaseException` 把 `KeyboardInterrupt` 变成
  「按未知处理」，而这两条探测最长 60s × 2 核 —— 按了 Ctrl-C 看着它继续跑，比崩掉更糟。
  收窄成 `(OSError, subprocess.SubprocessError)`（起不来 / 超时）后两者语义分明。
- **`md.version` 与 `chmod`**：前者只该吞 `PackageNotFoundError`（「版本未知」不许替本模块的 bug
  说话）；后者只吞 `OSError`（Colab/Kaggle 的 FUSE 挂载点不一定支持 chmod，权限位是纵深防御）。
- **判据**：`tests/remote/test_notebook_runtime.py` +4 —— 不可达仍 `-2`、程序员错穿出、探不起来回落
  CPU、Ctrl-C 不被吞；逐条改回宽捕实测红（`DID NOT RAISE`）。

## §2026-11-15-degrading-a-probe-is-not-the-same-as-staying-silent

- **决定**：`worker/forensics.py` 的 5 处 `except Exception: pass` 改成 `_note(errors, …)`：
  值仍然降级（`rss_mb()` 还是 `(0.0, 0.0)`、量不到的盘仍然不写键），但**成因进快照的
  `probe_errors`**（`{字段: "异常类型: 文本"}`）。G9 该文件 5 → 0；同笔给 `rss_mb()` 补一个
  进程边界兜底（G4 +1，记账在 `test_broad_except_budget.py` 表头）。
- **为什么 G9 的第二条处置在这里成立**：这个模块的**唯一读者**是事故后的人（OOM / 写盘失败不留堆栈，
  快照是临终状态）。而 `rss_mb: 0` 与「探针自己失败了」在 jsonl 里逐字相同 ⇒ 那份文件答不了它唯一
  该答的问题（「上一轮到底有没有 OOM」）。留一行成因，成本是一个 dict 键，收益是判读从「猜」变「读」。
- **为什么不顺手窄捕**：平台探针（`ctypes`/PSAPI、`/proc`、`shutil.disk_usage`、torch 驱动）的异常族
  本来就由对方决定，而且本模块的硬契约是「绝不反杀训练」——宽捕在这里是对的，静默不是。
- **判据**：`tests/worker/test_commit_journal.py` +4 —— 探针失败留成因、探针自己炸也不抛、量不到的盘
  留名、全好时 schema 逐字不变（不出现 `probe_errors`）。把 `_note` 退回 `pass` 实测 2 条红。

## §2026-11-15-probe-evidence-must-reach-the-line-people-actually-read

- **决定**：`worker/forensics.log_snapshot` 的人读日志行在有降级字段时追加
  `probe_errors=<字段名,…>`（只点名字，异常全文留在 jsonl）。`_log_line(tag, snap)` 独立成函数
  以便用例直接钉这行（它是交付物，不是实现细节）。
- **为什么**：前一条决定让快照**文件**说得清「这个 0 是量到的还是量不到的」，但现场最先被看到的
  是控制台上那行 `[forensics] it103: rss=0MB ...`。只改 jsonl ⇒ 值班的人在事故发生当天仍然读不到
  证据，要等事后有人想起来翻文件。
- **代价**：干净路径的行短一个字都不多（无降级就不加后缀），坏路径多一段字段名列表。
- **判据**：`tests/worker/test_commit_journal.py` +2 —— 降级时该后缀恰好出现一次、干净时不得出现。
  把后缀那行改回 `pass` 实测红。

## §2026-11-15-scan-entry-floors-are-derived-from-the-baseline

- **决定**：存量基线类门禁（G4 宽捕 / G9 静默）的「扫描入口自检」地板不再写死绝对值，
  改为 `tests/helpers/budget.sanity_floor(baseline)` = 基线总和的 **9 成**（向下取整）——
  两个门禁共用这一份实现（此前各有一份、且各带一个手改数字）。
- **为什么**：这条断言要回答的只有「AST 还数得到东西吗」，与「存量多少」无关。写死绝对值时，
  每次**合法**清理基线都得顺手回填一个与之无关的数字（2026-11-15 一天里改了三回：423 → 397 →
  398、51 → 44 → 40 → 35），三次都可能写错，而「这次该写多少」无判据可依。派生式随基线自动
  收紧，人工回填这一步直接消失。
- **为什么是 9 成而不是 5 成**：这条断言的代价是「一次误红」，收益是「扫描面坏掉立刻知道」。
  10% 的余量只够容纳「一两个文件完全掉出扫描面」，再多就该红。
- **判据**：`tests/test_budget_floor.py`（4 条：比例、永不是恒等式、随基线下降、空基线为 0）
  + 实测把 `PROD_DIRS` 里的 `worker` 去掉（模拟「扫描面写错」）⇒ 两个门禁都红，还原后绿。

## §2026-11-15-every-budget-gate-shares-one-derived-sanity-floor

- **决定**：五条存量门禁的自检地板全部改用 `tests/helpers/budget`（基线九成）：
  G4 宽捕、G9 静默、G5 私有断言（原 `>= 500`，基线 703）、G6 抑制符（原 `>= 150`，基线 163）、
  文本断言（原 `(400, 200)`，基线 1501 / 1154）；G3 print 门禁此前**没有**条数地板（只有文件数与
  锚点），现补一条。
- **为什么不只是「少改几行」**：这些断言要回答的都是「扫描面还数得到东西吗」。写死绝对值时有两个
  后果：① 每次合法清理基线都要回填一个无判据可依的数字（2026-11-15 一天里改了三回）；
  ② 数字会随着清理一路走低，最后低到「扫描面只剩一半也过」。派生式同时修掉两条。
- **细则**：`sanity_floor(table)` 给「{文件: 整数}」一张表的门禁；`sanity_floor_of(total)` 给按类别
  分表（G6）或多个口径（文本断言）的门禁 —— 都是同一个比例，只有一份实现。
  **文件数地板仍是绝对值**：拿「文件数」和它自己的九成比是恒等式，没有意义。
- **一个顺手抓到的东西**：G6 原来那条地板比的是**基线跟自己**（恒真）⇒ 扫描面坏掉它也不会红；
  现在比的是 `_scan()` 的结果。
- **判据**：`tests/test_budget_floor.py` +1（两个入口同口径）；实测把 `worker` 从 G6 的扫描面排除
  ⇒ 该门禁两条红；把 `worker` 从 G4 的 `PROD_DIRS` 去掉 ⇒ 两条红。还原后全绿。

## §2026-11-15-visible-degradation-covers-the-wrapper-not-just-the-inner-call

- **决定**：G9 第十四批收敛三处「先吞后说」——
  ① `trainer/batch_runner._heartbeat`（批评估心跳）：`except Exception: pass` → `warn_once`
  + 带成因；② `worker/loop_guards_sweep._rotate_cleanup` 的孤儿波次清理：
  `except BaseException: pass` → `except Exception` + 一行 `skipped: <repr>`；
  ③ `remote/hub_client.git_head`：先 `pass` 再抛固定文案 → `raise … from e`，退出码非 0 与
  空输出各自具名。G9 35 → **32**。
- **为什么 ①不是第六批的重复**：第六批修的是 `write_state` **内部**落盘失败（O(1) 那条也不抛）。
  这一处包的是「写心跳」这件事的整块，包括 `from worker.eval_heartbeat import …` —— 打包/路径
  问题会让整块变成静默，而它在 console 上的表现与「心跳根本没调」逐字相同。
- **为什么 ②必须收窄而不只是加日志**：`BaseException` 把 **Ctrl-C** 一起吞掉，而批量 rmtree
  是本轮最慢的动作之一 —— 按了中断却在下一轮接着跑，中断「看起来没生效」。这种形状在仓库里
  已有先例（第十二批 `notebook_runtime._run_probe`）。
- **为什么 ③算「信息丢失」而不算风格**：`git 没装 / cwd 没了 / 30s 超时 / 不是 git 仓库` 四种
  成因的处置完全不同，压成一句「不在 git 工作区」等于把诊断成本全推给现场。
- **判据**：三处各自改回旧行为 ⇒ 6 条新用例红（心跳 1 / 波次清理 2 / git_head 3）。全量
  6096 passed / 2 skipped；mypy 933 文件。

## §2026-11-15-uptime-signals-and-a-dead-git-head-twin

- **决定**：G9 第十五批 —— ① `worker/terminal_stats.write_terminal_stats` 写盘失败改
  `warn_once(WARN_KEY, …)`；② `worker/serve_pool.begin_round` 的 `w.kill()` 失败逐次留一行；
  ③ **删掉** `remote/job_fs._git_head`（连同 `__all__` 条目与 `remote/worker.py` 的转发块）。
  G9 32 → **29**，G4 398 → **397**。
- **为什么 ③不是「同 ②一样加日志」**：它没有生产调用者 —— `remote/code_snapshot.py` 有自己的
  `_git_head`（走 `run_capture` 防 Windows 闪窗，那条纪律见 details §20），job_fs 这份只是
  `remote/worker.py` 转发名养着的死双生。给死代码加日志是给维护成本再加一层。
- **为什么 ①要按 key 去重**：它每轮都会走到；坏了就是每轮都坏，逐轮喊会把日志刷成噪音。
  ② 则在轮边界最多几次，逐次留痕更有用（能数出「这个轮死了几个 worker」）。
- **顺带**：G9 门禁的绝对文件数下限（`>= 25`）删除。存量清到 22 个文件后，它就是又一处
  「清理到哪手改到哪」的回填；扫描面活性已由派生根数地板（基线九成）覆盖。
- **判据**：①② 还原成旧行为 ⇒ 2 条新用例红；③ 由既有 `test_moved_names_…` /
  `test_worker_forwards_every_moved_name` 覆盖（名字从 `MOVED_NAMES` 移除后仍绿）。全量
  6098 passed / 2 skipped；mypy 933 文件。

## §2026-11-15-forwarded-names-must-stay-live

- **决定**：新增门禁 `tests/test_worker_forward_names_are_live.py` —— `remote/worker.py` 里每个
  `from remote.X import (name as name)` 转发名，在**除定义模块与它自己那行声明之外**的 `.py` 里
  必须至少被提到一次；只活在转发声明里的名字 = 死别名，门禁判红（提示删掉，或加进 `ALLOWED`
  并写理由 —— 今天 `ALLOWED` 为空）。
- **为什么**：`remote/job_fs._git_head`（上一批）就是这样活了一年的 —— 全仓零调用，只因 worker
  的转发名还在就没人删，直到有人差点给它加日志。一次人工 grep 能发现，但**没有东西阻止下一个**；
  这条纪律与 G5/G9 同形：冻结不了就判据化。
- **口径**：只认 `name as name`（改名转发不算）；`worker.py` 自己**算在扫描面里**（`PPO_BACKENDS`
  喂 argparse、`YIELD_AFTER_SEC_DEFAULT` 喂让路判据都是活引用），但 `NAME as NAME,` 声明行会被
  先剥掉 —— 否则每个名字都「在 import 里提到过自己」而永远判活。
- **判据**：临时给 `remote/job_fs.py` 加 `ZZ_PROBE_SENTINEL` 并从 worker 转发 ⇒ 门禁红（并点名
  该名字与定义模块）；还原后绿。另有一条口径自检（`JOB_DIR_KEEP` / `pack_opt_tar` 必须被判活）
  与一条入口自检（转发名 ≥ 90，由派生地板给出）。
- **现状**：103 个转发名，**零死名**（人工普查的结论与门禁一致）。

## §2026-11-15-forward-gate-covers-every-facade

- **决定**：上一节的门禁从「`remote/worker.py` 一张表」推广成**全仓门面**：
  `tests/test_forward_names_are_live.py` 自动收录所有含 ≥3 个 `name as name` 转发的文件
  （今天 15 个：`remote/worker` 103 名、`common/protocol` 84、`worker/config` 48 …），
  逐名判活。
- **为什么推广**：一张表只管得住那一个文件；同样的死别名在别的门面一样会长。推广后立刻抓到
  真例：`common/protocol.GOAL_SCRIPT` / `INTENT_SCRIPT` —— 定义模块（`common/manifest.py`）自己
  还在用，但**门面上这两个别名零读者**，已删。
- **一个差点自证的坑**（值得记）：第一版判活用「全仓文本含该名字」，而门禁文件自己的说明里
  点名了 `GOAL_SCRIPT` 作例子 ⇒ 它自己成了「还有人引用」的证据，两个死名被判活。修法：门禁
  自身排除出扫描面（`SELF`）。**判据文件的示例文字不得成为判据的输入**。
- **性能**：一次分词建 `{标识符: 文件集合}` 倒排索引（`@cache`），逐名字判活 O(1)；
  三个用例合计 ~4s（不做索引时是 17s）。
- **判据**：① 真例红→绿（`GOAL_SCRIPT`/`INTENT_SCRIPT` 删前红、删后绿）；② 合成探针
  `ZZ_PROBE_SENTINEL` 从 `common/manifest` 转发进 **`trainer/batch_eval.py`**（另一个门面）⇒ 红
  并点名；③ 口径自检（`JOB_DIR_KEEP` / `pack_opt_tar` / `ProtocolError` 必须被判活）+
  入口地板（门面 ≥ 13、转发名 ≥ 270，均为派生地板）。

## §2026-11-15-g9-remaining-29-classified-26-contractual-3-leaks

- **决定**：G9 剩余 **29 处 / 22 文件**逐条定性完毕（全文见审计档 `python.refactor.md` 附录 C）：
  **A 契约静默 26 处（不再动）· B 漏网 3 处（待窄捕）· C 已留痕 0 处**。
  - **A**：日志层 `Tee` 的三处 sink（递归风险）、进程收尾级联的最后一层（`close→wait→terminate→
    wait→kill`，`child_proc` / `deliver_proc` / `rollout_node` / `worker_proc`）、返回 dict 的
    能力探测（`torch_xla` 三项）、观测回调（`bulk_sched._notify_p1`、`error_report._send`、
    `offline_deliver._guard`、`cloud_pool._log_line`）、返回值即结论的探针（`push_bootstrap.wait_ping`、
    `push_register` 读 error body、`deliver_worker._watch_stdin`、`child_proc` 读流截断）、
    平台降级（`payload_worker` / `upload_worker` 的 `reconfigure`、Windows 控制台不接受≠错）、
    以及**被既有契约用例钉死**的 `trainer/loop_transport._course_cf_tunnel`（配置读不到 ⇒ 缺省）。
  - **B**：`remote/push_client._probe_sha`（200 但 body 非 JSON 被读成「问不到」⇒ 重复上传）、
    `remote/worker.run_job` 的结果复用（校验器 bug 被读成「缓存损坏」⇒ 静默重算 PPO）、
    `trainer/bc_loop.append_weights_md_row`（git 取不到 vs 命令拼错同形）。
- **为什么 A 类“不再动”要写成决定**：这 26 处每一处都**看起来**像「降级即静默」的旧债，不写下来
  下一个人会重审一遍，或更糟 —— 给收尾级联的最后一层加日志（在已经不受控的地方再加一次可能
  失败的操作）、给日志层内部加日志（递归）。判据是「再加一层能不能更好」，不是「静默是否难听」。
- **为什么不给 A 类加白名单/noqa**：G9 基线只许调小，A 类冻结即够；表达「这是契约」的正确位置
  是**代码注释**（就近），不是一份需要同步维护的清单。
- **现状与下一步**：B 三处都是「窄捕不改语义」的形状（`NET_ERRORS`+`ValueError` /
  `(OSError, ValueError, ProtocolError)` / `(OSError, SubprocessError)`），各配一条「程序员错误
  必须穿出」用例即可。

## §2026-11-15-b-class-narrowed-probe-cache-and-sha

- **决定**：把附录 C 里 B 类三处宽捕收窄（G9 29 → **26** 处 / 19 文件，G4 397 → **394**）：
  ① `remote/push_client._probe_sha` → 模块级 `PROBE_ERRORS = (*NET_ERRORS, ValueError)`；
  **200 但 body 不是 JSON 照抛**。② `remote/worker.run_job` 的结果复用 →
  `except (OSError, ValueError, ProtocolError)`。③ `trainer/bc_loop.append_weights_md_row` 取 sha →
  `except (OSError, subprocess.SubprocessError)`，取不到仍写 `"n/a"`。
- **为什么 ①必须抛**：`_probe_sha` 的 `None` 被上层读成「问不到 ⇒ 沿用上次命中 / 保守上传」。
  节点回执形状变了（200 + 非 JSON）是**确定性**问题，读成 `None` 的结果是每轮白传 `code.zip`
  （1.69MB + opt 1MB），链路越慢越必然超时 —— 真因永不出现。与 `common/distribution` 探针、
  `hub_http` 薄壳同一条判据。
- **为什么 ②重要**：`validate_result` 自己的 bug（TypeError）原被读成「缓存坏了」⇒ **静默重算
  一次 PPO**（几十分钟 + 权重与上次不同），日志里只有「缓存无效」。
- **为什么 ③算漏网**：`"n/a"` 是显式降级（不是静默），但宽捕让「git 不在 PATH / 超时」与
  「命令拼错」同形，而这一列是模型档案的**溯源列**。
- **判据**：三处各自撤回 `except Exception: pass` ⇒ 4 条新用例红（非 JSON 被读成问不到 /
  `TypeError` 被吞 / `TypeError` 记成 n/a / 校验器崩了被当缓存坏）；恢复后全绿。全量
  6113 passed / 2 skipped；mypy 936 文件。
- **附带发现（测试纪律）**：`LogRouter.warn_once` 去重是**进程级**的。断言「同一 key 只喊一次」
  的用例必须注入**同一个** `LogRouter()` 实例 —— 每次 `lambda: LogRouter()` 都 new 一个等于把
  去重关掉（断言退化成「喊了几次就是几次」）。这条是「全量 forkdist 分片一变就随机红」的成因。
- **2026-11-15 补（A 类就地注明）**：附录 C 的 26 处 A 类逐条在**各自的 `pass` 上方**写了
  `# 契约静默（G9 冻结，非旧债）：<理由>`，并由
  `tests/test_silent_except_budget.py::test_frozen_silent_sites_say_why_in_place` 双向钉住
  （缺理由 ⇒ 红；注释对不上静默宽捕 ⇒ 红）。**不新增 noqa / 清单文件 / 例外表**：清单离代码远，
  改代码的人看不到它，这正是同一批 `except Exception: pass` 被反复重审的成因。
  标记**不减轻** G9 计数 —— 它是「理由已就近表达」的证据，不是豁免凭证。
- **2026-11-16（§9 注释指针门禁）**：新增 `tests/test_docstring_refs.py` —— 注释 / docstring /
  字符串里「带目录的 `file.py::name`」必须落在真实文件上（`tokenize` 只读 COMMENT + STRING，
  解析顺序：仓库根 → `nn-training/` → 引用者目录 → 包内后缀）。落地时 13 处指针仍指着 `biz/` →
  `worker/` 搬家前的旧家，全部改成现在的家（含把「三处行构造点」修正成现在的两处）。
  **被否决的两个口径**：① 连 `path.py:123` 行号一起查 —— 行号天然随代码漂移，而且它出现在被
  引用的事故 traceback 里（那是历史证据，改写等于伪造现场），查它只会逼人删注释；
  ② 连裸文件名（散文里的 `queue.py`）一起查 —— 同名模块在仓里常有多份，写信人只是提一句模块名，
  查它会把 100+ 处叙事写成红。只查「带目录 + `::`」既覆盖「去那个文件看契约」的真实指针，
  存量又是可清零的（13 → 0，无基线文件）。
- **2026-11-16（§9 模块 docstring 上限门禁）**：新增 `tests/test_module_doc_budget.py` +
  `tests/golden/module_doc_baseline.json` —— 模块 docstring 超过 50 行按文件冻结（13 文件），
  只拦增长 + 基线只许调小。**被否决：一次清零（把三个 `__init__.py` 的 113/99/90 行搬进
  `docs/nn/`）** —— 那几段是分层契约（谁能 import 谁、哪一层能改状态），必须能在 import 那一刻
  读到；搬走等于把契约挪到读者不会去的地方，与 §9「注释离代码太远」自己矛盾。门禁只做止损：
  新模块不许一出生就超 50 行，已超的不许更长；顺手删短就顺手把基线调小。
  口径只数**模块**级 docstring（函数/类的不算：它们的长度由 G1 的函数形状与可读性另管）。
- **2026-11-16（G2 名册清零：`UnreapedProcs`）**：`common/platform_utils.py` 的模块级
  `_UNREAPED: list[Any]` 搬进 `UnreapedProcs`（`keep` / `sweep` / `__len__`），三个公开函数
  `keep_unreaped` / `sweep_unreaped` / `unreaped_count` 签名不变、多一个 `store=` 注入位，
  `L0_MUTABLE_ALLOWED` 清空。`common/` 至此零模块级可变容器（`common/__init__.py` 契约 #4）。
  为什么不留 `list` + 锁：那是一本**进程级账本**，锁只是让并发写不打架，测试里仍然无法隔离；
  对象的 `store=` 让用例自己造账本（新用例断言注入的那个不碰默认实例）。
  名册本体不删：`test_l0_allowlist_is_still_needed` 靠它继续拦「过期豁免」，将来真有拆不动的
  存量时加条目仍要在表头写日期与理由。
- **2026-11-16（动作空间判据单源）**：`worker/ppo/np_core.py` 的两处 `"a_hold" in d`
  （掩码宽校验、episode 字段集）改走 `ActionSpace.from_batch(d).is_v6`；新增门禁
  `tests/common/test_action_space_contract.py::test_only_one_place_decides_v6` —— 生产面除
  `common/contracts.py` 外不许再自己判 `a_hold`（`ast.Compare(In)` 与 `.get("a_hold")` 都算）。
  为什么值得一条门禁：第二处判据拼错一个字母 ⇒ 静默走 legacy ⇒ 9 宽掩码按 7 宽切、hold 头
  永不训练，loss 照降；这类错误没有任何运行时现象。`_RL_SHARD_SPEC` 里的 `"a_hold": (...)` 是
  键名声明不是判据，所以门禁只认 `in` / `.get` 形态（不是「出现即红」）。
- **2026-11-16（B8 第一刀：`common/codehash.py`）**：`common/distribution.py`（1622 → 1409 行）
  的代码身份面整体搬到 `common/codehash.py`：`compute_code_hash` / `CODE_HASH_MANIFEST` /
  `_collect_code_hash_files` / `_skip_codehash_{dir,file}` / `code_hash_report` /
  `compute_engine_epoch` / `check_code_hash` / `dirty_hash_files` / `_parse_porcelain` /
  `_git_{index,cat}_blobs`。仓库根锚点同时单源成 `common/paths.py::REPO_ROOT`（原先在
  distribution 里自己上溯 3 层，`common/codehash.py` 若照抄就是第二份）。
  **不留 `X as X` 门面**（与前几次搬家不同）：调用点 16 文件全部改成 `common.codehash.X`，
  打桩面（`monkeypatch.setattr(common.codehash, ...)`）跟着走 —— 门面会让「codeHash 在哪」
  永远有两个答案，而这次搬迁的动机正是「一个 1600 行的杂物间里没人找得到它」。
  随之收紧的判据：`tests/test_distribution_io_budget.py` 的扫描面从「一个模块」扩成
  `L0_IO_MODULES = (distribution, codehash)` 并按「模块 + 原语」记账 —— 搬家不能把副作用
  从账本上搬没。
- **2026-11-16（仓根锚点单源：`common/paths.py`）**：`REPO_ROOT` 原来有 13 份「自己数层数」的副本
  （`biz/ hub/ remote/ trainer/ worker/` 各自 `Path(__file__).resolve().parents[2]`）。这不是重复代码，
  是重复**算术**：`hub/store_offline.py` 从 `remote/hub/` 搬到 `hub/` 那次少了一层，代码不报错，只是
  所有相对路径指到仓库父目录（靠一个工装用例才抓住）。所以口径定为「仓根只在 `common/paths.py`
  算一次，类型是 `Path`」，13 个模块改 import；`trainer/batch_eval.py` 的 `from biz.batch_plan import
  REPO_ROOT as REPO_ROOT` 自别名一并删（`X as X` 门面正是这轮 P1-11 在清的债）。
  **入口脚本例外**：`train/train_bc_v6.py`、`trainer/eval_*_once.py`、`worker/eval_replays_once.py`、
  `worker/ppo/bench.py`、`worker/models/*`、`worker/data/*` 要在 import `common` **之前**算出根来插
  `sys.path`，那种自举只能在本地算（写错是 ImportError，不是静默类错误）—— 它们按文件冻进
  `tests/test_repo_root_anchor.py` 的 BASELINE，只许调小。
- **2026-11-16（hold 判活窗下沉 `common/hold_window.py`）**：「接管后多久没进度算掉线」是**两侧共用**的
  判据（hub 判 hold 活性 / 训练侧读控制文件时就地自判活），原先只有 `hub/task_pack.py` 一份，训练侧靠
  函数内 `from hub.task_pack import hold_progress_stale_sec` 取 —— 一个阈值就造出一条「纯逻辑模块碰网络」
  的边。搬到 L0 后两侧都只依赖 `common/`。**没有**合并 `AUTO_HANDOFF_PENDING_SEC`（导包窗口，巧合同值）、
  也没有合并 `hub/store_leases.py` 的 BC 进度租约窗：语义不同，plan §1.5.2-P0-1 明令禁合并。
- **2026-11-16（`loop_control` 归属：`trainer/` → `worker/`）**：它只 import `common.*`（纯逻辑），此前被
  算作「编排」全靠 `from trainer.dispatch_queue import REPO_ROOT` —— 为取一个路径常量而存在的一条边，
  与 `biz/batch_plan.py` 那笔同一型。仓根收口删掉这条边后，`test_trainer_holds_only_orchestration_modules`
  立刻红（这正是那条门禁的用途）。归属按既有口径定：纯控制面契约住 `worker/`（`worker/gate_halt.py` 同族），
  搬家顺带正过来一条反向边（`worker/loop_hold` 曾 import `trainer.loop_control`）。同步改了
  `TRAINER_ORCHESTRATION`、`remote_dag.LAYERS`（`worker.loop_control: 0`）、`test_production_isolation`
  的路径钉与 dashboard/docs 里的指针。
- **2026-11-16（`worker/ppo/engine.py` 的转发面：11 名 → 1 名）**：`RolloutBackend` 契约（`worker/backend.py`）
  要求 `per-tick` 后端模块上有 `load_episode_from_shard`，`trainer/stream.py` 运行期取 —— 这条是契约，不是
  旧名字。其余 7 个（`compute_gae` / `discover_*` / `load_shard*` / `load_episodes_common` /
  `_RL_SHARD_SPEC`）的唯一消费方是两个 worker 测试的属性取用，改成 `from worker.ppo.np_core import …`
  后删除，并把守护测试翻转（断言 engine 上**没有**这些影子）。**反面教训**：契约成员不能用
  `y = X.y` 赋值发布 —— 静态契约扫描器只认 `from X import y`，赋值会判 `<unresolved>` 而
  `test_backend_contract_runtime` 红；那一条 `noqa` 留着，并在 import 里写清来路。
- **2026-11-16（`trainer/` 的 "re-exported for tests" 面归零）**：`trainer/{run_rl,eval_dispatch,loop,dispatch}.py`
  上 12 条 `# noqa: F401` 的注释都写着「for tests / 兼容外部引用」，但那些读者只有 `e2e/test_run_rl.py` 与
  两个单测。停机条件本来是「tests 全量改完后删除」，一直没兑现是因为没人量过谁在用。这批把 5 个
  `worker.resume` 名 + `run_rollout_queue` + `find_archive_weights` / `report_winrate_safe` /
  `select_delayed_eval_it` + `resolve_tail_join_sec` 的消费方改到真家，`trainer/loop.py` 的
  `run_inspect` / `log_iter_error` 因全仓零消费直接删。G6 基线 18 处 / 10 文件 → 6 处 / 6 文件。
  口径不变：**转发面只在有停机条件时成立**；测是读者，就该跟测试一起迁走。
- **2026-11-16（P1-5 第一批：`common/settings.py` + G13「env 直读只减不增」）**：审计说「130 处 env
  直读 ⇒ 建 `Settings` 层」。先量后动：AST 数出生产面 **94 处 / 44 个变量**，被 >1 个模块读的只有 5 个，
  真「同一根旋钮两个默认值」的只有 2 个（`EVALBOARD_DATA`：`trainer/batch_store` 与
  `worker/eval_heartbeat` 各解一次，而 `worker/` 不能 import `trainer/`；`BATTLE_HUB_TOKEN`：
  `remote/run_loop` 与 `remote/deliver_worker` 各抄一遍 strip 归一化）。所以这批**不**建穿透签名的
  `Settings` 对象（那是给 44 个旋钮加一层转发面），只建 `common/settings.py` 收多读者旋钮，形态沿用
  `common/hold_window`：常量 + 调用时读 env 的纯函数 + `env=` 注入（不给测试塞 `monkeypatch.setenv`）。
  **G13** `tests/test_env_reads_budget.py` 按文件冻结直读次数（63 / 32，只许调小）+ 旋钮单家判据；
  豁免 `STANDALONE_BOOT_MODULES`（它们不许 import `common.*`，本地解是有意为之）。
- **2026-11-16（P1-11 第三批：拆 `ppo/common` → `np_core` 的再导出门牌）**：`worker/ppo/common.py` 再导出
  29 个 `np_core` 名字，其中 10 个全仓零读者；其余 19 个只有 5 个文件消费，而 `worker/ppo/__init__.py`
  的便捷名表**已经声明**这些名字住 `np_core` ⇒ 这层是第三份门牌，撤名字时看不见第二入口。5 处
  `from worker.ppo.common import …` 改直取 `np_core`（`remote/train_core` 那条是延迟 import，`np_core`
  免 torch，顺带少拖一次）；`common` 只留自己真用的四个名字，`# noqa: F401` 消失 ⇒ G6 6 → 5 文件。
  **判据换代**：`test_ppo_common.py` 原来断言「四个再导出面仍在、但必须是同一对象」——白名单会替壳背书；
  改成结构判据「`common` 里 `from … np_core import X` 的 X 必须在本模块体内被引用」，整类壳被禁。
- **2026-11-16（P1-11 第四批：`worker/data/{dataset,weights_io}.py` 的 split 残壳）**：两处都是
  2026-09-26「把免 torch 的那一半搬出去、留层再导出以便调用点不改」的产物。量出：`dataset` 的 5 名里
  4 名（`_MOVE_FLIP` / `BULLET_MIRROR_LUT` / `ENEMY_MIRROR_LUT` / `_flip_direction`）**全仓零读者**；
  `weights_io` 的 6 名里唯一有外部读者的是 `latest_weights_path`（`eval_bridge`，改直取 `weights_meta`），
  另有两个（`latest_weights_path` / `validate_weights_meta`）本就是文档里的字。收成真 import ⇒ G6 5 → 3 文件。
- **2026-11-16（P1-12：`compute_gae` 向量化，逐字节不变）**：GPU/CPU 上 GAE 原本每个
  transition 都做 4 次 `float()` 装箱 + 1 次 `pow`。改法只向量化**无递推的那半**（γ_step、δ、
  carry），tick 循环里留一行 `last = δ[t] + carry[t]·last` —— 实测 2.2~4.3×（N=128→4096）。
  审计档原建议「按 done 分段后 `np.cumsum` 折扣前缀积」**被否**：它要乘一遍再除一遍，舍入顺序
  与逐项递推不同（`adv` 虽落 float32 但按 float64 累积），golden 签名会变；且 `P_t` 长局上下溢。
  `γ^Δt` 也**不用 `np.power`**（整数指数走 numpy 自己的快路径，末位不保证一致）⇒ 按 distinct Δt
  建 Python `pow` 查表。回归用例把旧循环逐字留作独立实现做逐位对账。
- **2026-11-16（P1-12：`append_jsonl` 去掉写者线程）**：账本每行原走「队列 + 写者线程 +
  `Future` ack」，实测 161 µs/行，其中写盘只占 28 µs、**99 µs 是跨线程 ack 的等锁延迟**
  （cProfile 的第一大项就是 `_thread.lock.acquire`）。改成「每路径一把锁 + 就地追加」：
  40 µs/行（4.0×），「返回即已写完」的契约不变，异常直接抛给调用方。否决两条：常开句柄
  （7 µs/行，但破坏「写者不持有句柄」且 Windows 上会撞 `os.replace`，本机无法证伪）与
  批量/异步（把「返回即可读」变成「稍后可见」）。顺带 `close()` 的排空/`timeout`、空闲
  退场常量与那条跨线程回传用的宽捕一起消失（G4 `common/fs.py` 1 → 0）。
- **2026-11-16（P1-4：`Any` 先上预算门禁，不批量换 `object`）**：审计原方案是「`Any` 换成
  `object`（惰性）」。实测两轮：全量 236 处 `dict[str, Any]` → `dict[str, object]` 触发
  **406 个 mypy 红点 / 93 文件**；缩到「单文件 ≤2 处」的 50 个文件仍是 **102 红 / 29 文件**，
  回滚到还剩 42 个红点时它们全在调用方（`object` 不可下标、`dict` 的值型不变式）。
  ⇒ 这些 dict 缺的是**字段表**（`TypedDict` / dataclass / 只读面 `Mapping[str, object]`），
  收益只能按数据边界逐个拿。故本批只落 **G14**（`tests/test_any_budget.py`：1176 处 / 182 文件
  按文件冻结、只减不增、`Any as A` 改名绕过单独拦），先把「只涨不跌」掐掉。
- **2026-11-16（P1-4 第一处：`demo_bank` 上 `TypedDict`）**：`demo_bank` 原是全链裸 `dict` +
  字符串键，`remote/train_common` 的字段安全阀自带一份手抄名单（`need = {...}`），与
  `engine_paddle` 的 `if "extra" in demo_bank` 各说各话 —— 缺 `extra` 时它是**静默跳过**，
  BC 项少一半而日志正常。新增 `worker/ppo/demo.py::DemoBank`（L0，只 import numpy/typing）：
  五键具名；`DEMO_BANK_FIELDS` 由 `__required_keys__` 派生，安全阀与类型只有一份名单。
  消费面四处改注（`decode_demo_bank` / `check_demo_bank` / `_CoreRun` / 两侧 `ppo_update`）。
  判据 `tests/remote/test_demo_bank_contract.py`（字段表 == 导出端五个 / 安全阀同源 / 缺 extra 拒收）。
- **2026-11-16（P1-6 第一刀：线程必须有名字 + G15）**：55 处 `Thread(` 里最后 10 处补上 `name=`
  （`batch-worker:<node>-<i>` / `batch-supervise:<node>` / `batch-watch` / `queue-local:<node>` /
  `queue-worker:<node>` / `stream-collector` / `push-bootstrap` / `smoke-hub-server` / `tunnel-ab-hub`），
  新增**零容忍门禁** `tests/test_thread_names.py`（AST 要求 `Thread(...)` 带 `name=` 关键字；
  位置参数不算）。`Runtime`（executor 生命周期 / 有界共享池）不在本批，属 B6，见 §P1-6。
  顺带：`trainer/batch_runner.py` 顶在 1000 行 LOC 预算上，三处 spawn 收进 `_spawn_thread(...)`
  ⇒ 1000 → 997，没有开新豁免。
- **2026-11-16（bugfix：torch 侧 demo BC 缺 `extra`，两后端输入分叉）**：`demo_bank` 有五个字段，
  `engine_paddle` 一直把 `extra` 物化进 `demo_t`，torch 侧 `_setup_demo` 只建 obs/scalars/actions/masks
  ⇒ 同 bank 两侧喂给模型的输入不同。`policy_extra=True` 的 `StudentNet` 在 `_require_extra` 处
  拒绝 `extra=None`（明确「不静默用 0」）⇒ torch「demo 开 + 带 extra 模型」腿每次更新直接炸；
  legacy 模型看不出差别。修 `_setup_demo` 与 Paddle 同口径（存在即物化）；先红后修用例
  `test_demo_bc_feeds_the_bank_extra_to_a_policy_extra_model`。详见 `docs/nn/training-stack.md` §77。
- **2026-11-17（B8：`remote/offline_boot.py` 改生成物，12 段源码 `.pyfrag` + 拼接器）**：审计 §0.4-12 /
  §7.6 要求「3554 行改成生成物：源码拆多文件、构建时拼成单文件发行」。动前的约束是硬的：
  `offline_boot` 是 **standalone 运输单元**（notebook 引导格从 GitHub raw **按文件名**拉它，那时
  `code.zip` 还没进 `sys.path`）⇒ 拆成可 import 的模块会直接破掉运输契约，所以拆的只能是**源码**：
  `remote/_offline_boot/*.pyfrag` 12 段（按**顶层块边界**切，每段都能独立 `ast.parse`，最大 441 行），
  `tools/build_offline_boot.py` 按 `PARTS` 逐字节拼回发行物（6 行「不要手改」文件头 + 12 行分段头 + 段间 11 个空行
  ⇒ 与拆分前 **+29 行、其余逐字节相同**，一次性切分脚本先对账过 `"".join(parts) == 原文件`）。
  **分片后缀刻意是 `.pyfrag` 不是 `.py`**：生产树的静态门禁（print / 宽捕获 / `Any` / 函数形状 / LOC /
  层级账本 / 抑制符）全部按 `rglob("*.py")` 扫**发行物**，分片叫 `.py` 会把同一行字数两遍 ⇒ 只剩两条
  坏路（抬基线，本仓禁；或给每个门禁加排除名单 = 让门禁看不见代码）。`.pyfrag` 让「被检查的东西」与
  「被运输的东西」是同一个文件。代价写进明处：分片不被 mypy/ruff 解析，一致性与语法靠「重建 → mypy +
  用例」兜住（分段头编号漂号也会被守卫当场抓住）。
  **LOC 豁免因此改成有条件的**：`offline_boot.py` 之所以还豁免，不再是「拆不开」，只剩「运输契约要
  单文件」 ⇒ `tests/test_python_loc_budget.py` 新增逐分片 LOC 判据（同一个 `loc_of`；实测往
  `12_entry.pyfrag` 塞 1000 行 ⇒ 红），下一个「懒得拆」的巨段长不出来。
  守卫 `tests/remote/test_offline_boot_is_generated.py` 四条（提交物 == `build()` · 分片目录无表外分片 /
  无 `.py` · 分段头与位置一致 · 每段以换行收尾），四向证伪实测红（手改生成物 / 加表外分片 / 混进
  `.py` / 分段头漂号）。`test_module_doc_budget`（53 行）与 G3/G4/G14 读数**一个都没动**。
  全文 → `docs/nn/engineering.md` §118。
- **2026-11-17（B6 第一刀：`Runtime` = 线程池的唯一所有者 + G16）**：全仓 10 处
  `with ThreadPoolExecutor(...) as ex:` 就地建池（生产包 9 + `tools/e2a_readout` 1），四个缺口同时存在 —— 没有所有者（池的生死绑在
  词法作用域上）、没有上限（`min(8, len(nodes))` ×2 / `len(items)` / `n_slots` 叠加无人报数）、
  没有取消点（`halt_event` 到不了池边）、线程名各写各的（10 处只有 1 处带 `thread_name_prefix`）。
  新增 `common/runtime.py`（L0，stdlib-only）的 `Runtime`，做成 **`ThreadPoolExecutor` 的同形替身**
  （`with Runtime(scope=…, workers=N) as ex:` 后 `ex.submit/map` 照旧，转发默认池 `"main"`）：
  池按名存在 Runtime 里、`close()` 一次收完；线程名 `<scope>-<pool>`（scope 必填）；取消点 =
  `request_stop()` / `check()` / `sleep()` / `submit|map` 拒绝新活（已开跑的不打断 —— Python 线程
  杀不掉）；`describe()` 首次让「开了几个线程、超没超」可见。
  **三条有意不做**：① 不静默节流（`workers=N` 是操作员的决定，静默砍并发 = 拿吞吐换好看的数字，
  而上限只约束隐式请求 `workers=None` 并只报数）；② 不读 env（G13 只许调小，且并发旋钮不许再
  种一遍「同一根旋钮两个默认值」）；③ 不替调用方接 `halt_event`（6 个模块的 halt 语义一起改要真跑，
  `stop=` 已吃 `Event`，接线是调用方一行）。`RuntimeCancelledError` 不并进 `common.errors`（失败分类
  ≠ 取消）。门禁 **G16** `tests/test_executor_ownership.py`（零容忍无基线：生产包只许 `common/runtime.py`
  构造池 + `Runtime(` 必须带 `scope=` + 迁移处数下限），落地时迁移前红（9 违规 / 0 处使用）、迁移后绿。
  两笔基线有意抬并逐笔记账：SOURCE 1159→1160（G16 两向护栏被口径 B 计入，与 G3/G4/G13/G14 同一限边）、
  同名散落 110→113（`__enter__`/`__exit__` 协议名 + `describe`；exact 14 / clone 17 未动 ⇒ 无实现被复制）。
  全文 → `docs/nn/engineering.md` §119。
- **2026-11-17（B6 第二刀：PPO 的三条显式随机流 `PPORng` + ckpt 存 bit generator）**：审计 §P1-7 /
  §10-B6。原先切块重排 / 每 epoch 的 minibatch 顺序 / demo 抽样共吃**全局 `np.random`**，ckpt 存的是
  全局 MT19937 状态 ⇒「同一 seed ⇒ 同一条轨迹」实际依赖「进程里没别人抽随机数」这条不可执行的前提。
  现在：`common/runtime.py`（L0，stdlib-only）加 `substream(seed, name)`（blake2b·128bit，不引 numpy）
  + `Runtime(seed=)`/`Runtime.substream()`；新 `worker/ppo/rng.py::PPORng` 持 **三条**流
  （`chunk`/`epoch`/`demo`，加/删一处抽样不移动另外两条）；ckpt 改存 `state.json["rng_streams"]`
  （每条流的 `bit_generator.state`），旧 ckpt 缺这段 ⇒ `restore()` 返回 False + 响亮 WARN（不半份续跑、
  也不装成精确续跑）；`chunk_episodes(shuffle=True)` 缺 rng ⇒ `ValueError`（不留全局后门）；四条训练线
  + `remote/train_core`（种子改用完整 hex）+ `trainer/stream`（`scope=f"stream-{iter_id}"`：同 iter 可复现、
  跨轮独立）显式传流。**有意代价**：PCG64 ≠ MT19937 ⇒ 与历史轨迹数值断代（写进模块 docstring，不当 bug）；
  `train_core` 那行全局 `np.random.seed` 保留给本进程其他历史抽样，PPO 已不读它。门禁
  `tests/worker/test_ppo_rng_streams.py`（含 AST 零容忍：`worker/ppo/**` 不许消费全局 numpy 随机）+
  `REQUIRED_UPDATE_KWARGS` 加 `rng`（四条线的签名自动对账）+ 账本 `worker.ppo.rng` L0、
  `checkpoint_paddle` 2→1、`engine_paddle` 3→2。读数：6181 收下 / 2 skipped · mypy 956 · ruff 全绿；
  本机无 paddle ⇒ `paddle_parity` 八用例仍未跑。全文 → `docs/nn/engineering.md` §120。
- **2026-11-17（B6 收官：时钟进 `Runtime`，量 `clock()`/`monotonic()` 两读数）**：审计 §P1-6 / §7.4。
  生产面 **95 文件 / 517 处** `time.time()/monotonic()/perf_counter()` 全就地读 —— 要固定「现在几点」
  只能 monkeypatch 模块里的 `time`（改全局，同进程用例一起变），于是超时/过期/租约这些最该测的逻辑
  基本不测。现在 `Runtime(clock=, monotonic=)` 收两个零参可调用（缺省 `time.time` / `time.monotonic`），
  调用点 `rt.clock()` / `rt.monotonic()`，`describe()["clock"]` 报可调用名。**两个而非一个**：墙钟只做
  时间戳（会被 NTP 拉回）、单调钟只做时长/超时（绝不回跳），收成一个 `now()` 就是邀请跨机器时差才显形
  的错。**不建 `Clock` 类型**：既有约定已是 `clock: Callable[[], float] = time.time`（5 个模块在用），
  再造同名 Protocol 会与 `common/logutil.Clock` 撞名。`sleep()` 仍是真等待（注入不改时间流逝）。
  首个迁移点 `common/distribution.post_weights_parallel` 的 4 处计时走 `ex.monotonic()`，顺带被
  `test_python_shape_budget` 逼出 **`PushHooks`**（`log`/`on_alive`/`monotonic` 三根可选参数收成一个
  frozen dataclass ⇒ 签名 9→7，§P0-3「>7 参数 = 缺对象」），于是注入是**真能用**的：用例断言秒数
  `[0.5, 2.5]` 而不是「日志里有 0.xs」。存量 482 处 / 92 文件按文件冻结进
  `tests/golden/wall_clock_baseline.json`（只许调小；`common/runtime.py` 与 `remote/offline_boot.py` 豁免），
  新门禁 **G17** `tests/test_wall_clock_budget.py`（冻结 + 只降 + 扫描自检 + **两向护栏**：seam 点分调用 ≥4）。
  读数：6188 收下 / 2 skipped · mypy 957 · ruff 全绿；**paddle 3.3.1 实跑** `tools/paddle_parity.py`
  六条命令全绿（8 个 PPO 用例含 `ckpt_resume` 的 `rng_streams` 往返 + BC 三段），此前「本机无 paddle」
  的结论作废。全文 → `docs/nn/engineering.md` §121（paddle 读数在 §120 末）。

- **2026-11-17（B6 真正的收尾：裸线程收进 `Runtime.thread()`，配 G18 存量闸）**：审计 §P1-6 另一半。
  池那一半同日结了（G16 零容差），裸 `threading.Thread(...)` 还剩 **50 处 / 35 文件** —— 与池同源的问题
  但没有 `shutdown` 可接管：名字各写各的（G15 只判「有没有 `name=`」）、**没人 join**（退出靠 daemon 硬切，
  在飞的活静默没掉）、`describe()` 看不出某线程属于哪一轮。`Runtime.thread(target, *args, name=, …)`
  是所有者：名字统一 `<scope>-<name>`、登记在本域、`describe()["threads"]` 报是否还活、
  `join_threads()` 返回仍活着的名字、`close(wait=True)` 一并 join。**不替 target 包 `check()`**（往别人线程体里
  塞可能抛异常的取消点会改写各家退出语义 —— 「跑完这轮再走」vs「立刻走」），取消点仍由当事人自己调。
  **不建隐藏单例**：剩下 48 处多在模块级函数、手上没有 Runtime，要搬得把 runtime 从调用方传下来（§P1-4 接缝）;
  为省一行而造模块级 Runtime 只是把「无主线程」换成「无主 Runtime」。首批迁移 `CloudEvalRunner` +
  `LocalLane`（顺手把三条同名 `rollout-local` 改成 `worker-<slot>`）。门禁 **G18**
  `tests/test_thread_ownership_budget.py` + `tests/golden/thread_construction_baseline.json`：按文件冻结、
  只许调小、扫描自检 9 成地板、两向护栏（`rt.thread(...)` ≥1）；豁免只有 `common/runtime.py`（所有者本人）
  与 `remote/*_boot.py`（引导期单文件）。全文 → `docs/nn/engineering.md` §122。
- **2026-11-17（§122 第二批：五个类各自持 `Runtime`）**：`DelivererProcess`（`deliver-proc-out/-err`）·
  `UploaderProcess`（`upload-proc-out` 先起、`-in` 后起 —— `boot ok` 只可能被 reader 读到，顺序是原注释
  里的契约，迁移不许改）· `PushDispatcher`（`hub-push-tick` + `hub-push-<jid>`；空 jid 退化成 `-job`，
  旧代码的 `hub-push-` 本来也不算名字，而 `thread()` 拒空名 —— 不为一个坏 jid 抛异常）·
  `HeartbeatPublisher`（`trainer-heartbeat-write`）· `EvalExecutors`（`eval-exec-<key>-<i>`：节点池伸缩，
  同名槽位在转储里等于没名字；`close_pools` 用 `join_threads(0.0)` 只清登记不等线程，因为调用点可能在
  仲裁锁里）。**`Runtime.thread()` 起新线程时丢掉已退出的登记**：这两个长驻对象会一批批换线程，
  不收的话 `describe()["threads"]` 只涨不落。存量 48 → **40 处 / 30 文件**（`rt.thread(...)` 11 处），
  G18 基线同步调小。全文 → `docs/nn/engineering.md` §122。
- **2026-11-17（§122 第三刀：`Runtime.thread(start=False)` —— 顺序也是契约）**：把 `EvalExecutors`
  的槽位线程收进 `Runtime` 时，默认形态（起线程即登记）与它就是**先挂进池子、再开跑**的顺序冲突：
  一个已经能领牌的 worker 在池里还看不见 ⇒ `_resize_locked` 当成「池子空」再孵一条同节点线程
  ⇒ 同一节点两条 worker，公平发牌退化成 `A,A,B,B`（满载实测 3/10 次红；`start=False` 后 6/6 绿，
  该判据是 2026-11-04 决策的验收面，**不许**放宽）。决定：`thread()` 加 `start: bool = True`，
  `False` = 只登记不开跑，何时开跑由当事人定（所有者仍负责名字 / 登记 / 收尾）。**不**放宽
  `test_two_eval_rounds_share_dispatch_round_robin` 的 A,B,A,B —— 那是用户口径 1 的验收判据。
  迁移下一个类之前先读它的启动顺序有没有别处依赖。全文 → `docs/nn/engineering.md` §122。

- **2026-11-17（§122 第三批：十一个类 + 两个派发器的 `start` 合成一份）**：收编 `hub/offline_push`
  （`-tick` / `-pump-*`）· `remote/push_dispatch`（`-tick`）· `deliver_outbox`（`-loop`）· `job_round._StartBeacon`
  （`-run`）· `offline_deliver`（`-drain-loop`）· `offline_push._Child`（`-tee-*` / `-wait-*`）· `payload_proc`
  （`-out`）· `payload_worker._Engine`（`-fill`）· `result_upload`（`-run`）· `trainer/loop_eval`（`-it<轮>`）·
  `worker/eval_gate`（`-<name|round>-<序号>`）· `worker/serve_pool._Worker`（`serve-worker-<pid>-read`）。三条判据：
  ① **mixin 没有 `__init__` 就不许造类属性 Runtime**（`TrainingEval._eval_rt` 走实例属性懒建 —— 类属性 = 所有课程
  共享一条 Runtime = 隐藏单例，正是 §122 拒绝的形态）；② **调用方给的 `name=` 是语义名不是唯一名**（`EvalGate`
  并发多轮时加序号，否则转储里三条约同名线程读不出是哪一轮）；③ **两个派发器的 `start()` 逐字节相同** ⇒ 按重用
  账本抽共用（`remote.push_dispatch.start_tick_thread()` + `TickLoopHost`），既让 `exact_groups` 回到 14，也把
  `hub/offline_push.py` 从 1046 行压回 **997**（1000 行 LOC 预算）。顺带两条基线往小改：G15 地板 40 → 3（那条只防
  扫描入口写歪，扫描器正确性由同文件的合成源码用例盯），`any_baseline` 的 `push_dispatch` 11 → 10（`Callable[[], Any]`
  收成 `Callable[..., dict]`）。存量 40 → **27 处 / 21 文件**（`rt.thread(...)` 24 处）。读数 6194 收下 / 2 skipped ·
  mypy 958 · ruff 全绿 · 全量 ×6（5 绿，第 4 次一条墙钟上界的计时 flake，单跑全绿）。全文 → `docs/nn/engineering.md` §123。

- **2026-11-17（§P1-6 第四批：模块级扇出清零，G18 转零容忍）**：第三批剩下的 27 处全在模块级函数里，
  §122 当初留账的理由是「手上没有 Runtime」。这一批证明每处都有自然的域：拥有一轮/一个单元/一个 job 的
  函数里建**局部** `Runtime`（随函数收尾而散，不造模块级单例 —— 那只是把「无主线程」换成「无主 Runtime」），
  有 `self` 的挂实例（`BatchEvalRunner._rt`），有 `q` 的挂本轮状态。三处「迁移改变语义」的坑写进
  §124：① `rt.thread` 只收位置参数（`args=c` 的元组要 `*c`，否则 target 里抛 TypeError 且 `done` 永不置位）；
  ② `start()` 已代调，原来的「先造列表再统一 start」必须删（否则 `threads can only be started once`）；
  ③ `remote/push_bootstrap.py` 整份进 notebook cell、顶层零仓内 import ⇒ 进 G18 的 `EXEMPT`（连
  `common.runtime` 都不能拿）。**G18 转零容忍**：基线清空、旧的 9 成地板换成「扫到 >200 文件 + 所有者本人那两处
  还数得出」（存量归零后「数到多少处」证明不了扫描器活着）、两向护栏下限 1 → 40（实测 49 处）。存量
  **51 → 0 处**（四批）。读数：6194 收下 / 2 skipped · mypy 958 · ruff 全绿 · 全量 ×3（1 次端口回收计时 flake，
  单跑全绿）。全文 → `docs/nn/engineering.md` §124。

- **2026-11-18（B5 第四块：manifest / result 校验改用 pydantic 模型，`common/` 放行 pydantic 一个包）**：
  `python.refactor.md` §P0-4 / §7.1 要的是「字段级错误 + 类型进 mypy 视野」，手写 `isinstance` 链给不了。
  `Manifest` 与 `CloudResult` 就此成为字段级校验的**唯一实现**，`normalize_manifest` / `validate_result`
  降为薄包装（默认值补齐 + 保留未知键；三方对账与逐 kind 形状仍留在 `validate_result`——它要 manifest、
  还要写回 `r`）。三条「不许改变收/拒集合」的口径：`extra="allow"`（未知键保留，`model_dump()` 会吃掉它们）·
  严格标量（`True` 不冒充 int、`"5"` 不冒充 number，但 `int` 仍是 `float` 的合法替身**且类型不变**——
  manifest 进 jsonl，`1` 与 `1.0` 是不同文本）· 错误文案关键词逐字保留（`proto=` / `kind=` / `mode='bc'` /
  `role=` / `缺失必填字段` / `result 缺失字段 x`）。
  **代价（本条才是决策）**：`Manifest` 只能住 `common/`（搬去 `biz/` = L0 反向 import），于是 L0 的
  「stdlib-only」契约①第一次有例外。裁定放行**且仅放行** `pydantic`：它本就是 `pyproject.dependencies`
  里的声明依赖、纯 Python、随 `code.zip` 装 ⇒ 与那条契约要防的「云机没有 torch/numpy/trainer」不同类。
  闭集落在三处：`common/__init__` 契约①（指向 §128）· `test_common_layer::THIRD_PARTY_ALLOWED` ·
  `test_manifest_split::ALLOWED_IMPORTS`——加第二个第三方包必须先改这三处，不能顺手。
  读数：6210 收下 / 2 skipped · mypy 959 · ruff 绿 · G14/G5 不动 · 模块 docstring 与文本断言基线**都没抬**。
  全文 → `docs/nn/engineering.md` §128。

- **2026-11-18（P1-9 第二刀：G7 扩到全生产面，函数级 assert 转冻结存量）**：模块级 assert 是
  「`python -O` 一关就消失的正确性根基」，但 G7 自 2026-11-14 起只扫 `common/**`。扩面后抓到仅剩
  两处 —— `trainer/loop_round_steps.py` 的步骤表同步、`worker/eval_track.py` 的「评估池必须落在
  rollout 抽签区间之外」—— 都改成 import 期显式 `raise RuntimeError`（消息里带上两侧集合/常量）。
  函数体里那 62 处**不清零**：P1-9 原裁定是「热路径的 assert 保留但注明」，清零等于把它们改成
  永远执行的 `if … raise`（热路径代价 + 语义从调试期变成生产期）⇒ 按文件冻结进
  `tests/golden/assert_baseline.json`，只许调小、归零删条目（豁免 `worker/scripts/` 与
  `hub/smoke_loopback.py` —— 冒烟脚本的 assert 就是结论）。同文件补第三条门禁：可执行命令面
  （`tools/githook/*.sh` / `Makefile` / CI / `package.json` scripts）不得出现 `python -O`。
  G7 文件随之改名 `tests/test_common_module_asserts.py` → `tests/test_module_asserts.py`（它已不只
  管 `common/`）。全文 → `docs/nn/engineering.md` §129。

- **2026-11-18（B8 第二刀：调度策略离开线协议，`common/scheduling.py`）**：`python.refactor.md` §5.1
  点名 `protocol.py` 把「线协议常量」与「调度策略」混在一起——策略会变、协议不该变，改一个掉队阈值
  不该让评审去读线格式。做法是**逐字节搬家**（不重写）：32 名（优先级五档 + `job_priority` + 两个
  阈值 + `CLAIM_MODE_*` / push 节奏·冷却·告警 + `push_worker_id_of` / 节点能力 `CAP_*`·`NODE_*` +
  三个形状函数 + `pick_push_worker` / `push_job_wants_hub_push`）整段剪进 `common/scheduling.py`，
  `protocol.py` 只留 `X as X` 门面（房规 `force-single-line`，与 `common.manifest` 门面同形）⇒
  所有历史 import 一行不改、hub 与 `remote/push_dispatch` 的调用点无需动。新模块依赖面 = stdlib，
  **不** import `common.protocol`（依赖方向是「协议不解释策略」）。`protocol.py` 1120 → 969 行。
  守卫 `tests/common/test_scheduling_split.py` 把四问（定义唯一 / 依赖闭集无环 / 转发同一对象 /
  不抄线协议常量）收进一条用例、逐问带标签——合并的直接好处是文本断言预算口径 B 只 +1 而不是 +4，
  实测 SOURCE 预算**未动**（1165）。全文 → `docs/nn/engineering.md` §130。

- **2026-11-18（P1-5 第二批：`REMOTE_PUSH_NODE` 收进 `common/settings.py`）**：按 AST 重扫全生产面后，
  「≥2 个模块各解一次」的旋钮只剩这一个（`PATH` 不算：两处读的是系统变量）。三处读法里有一处还包了
  `str(... or "")`，也就是同一根旋钮的归一化写了三份。收成一个 `push_node_from_env()`（读 + 去空白；
  未设/空 ⇒ 空串 = 显式无覆盖），并登记进 `MIGRATED_ENV` 让 G13 的单家判据开始盯它。**刻意不做**
  `rstrip("/")`：那是拼 URL 的调用方的细节，旋钮只负责「读出来的字符串 같不一致」。存量
  63/32 → **60/30**，两个文件条目归零即删。全文 → `docs/nn/engineering.md` §131。

- **2026-11-18（P0-7 第十六批：`loop_transport` 的五处宽捕是五处自欺）**：`trainer/loop_transport.py`
  五个读 rl-config 课级覆盖的函数各罩一条 `except Exception: <用缺省>`。它们从来没在捕「配置读不到」
  ——`load_dist_config` 的契约就是永不抛（文件缺失 / 坏 JSON / BOM 都返回 `None` 并在内部喊一行）
  ——捕的是本模块自己的 bug（配置块从对象变列表 ⇒ `AttributeError`、键改名 ⇒ `TypeError`），处置
  却是「静默用缺省」：配置改了却没生效，而日志里一个字都没有。修法是**形状总函数**而不是更窄的
  except：`_config_block` / `_course_stem` / `_course_value` 三个纯读取助手（形状不对 `warn_once`），
  于是整段没有可捕的东西；`run_iters` 的非数字值仍窄捕 `(TypeError, ValueError)`（真配置错误 ⇒
  按 0 + 一行）。**顺带改口径**：三条用例原来用 `side_effect = RuntimeError/OSError` 测「静默」，
  那是把宽捕当契约 —— 改成真实失败形态（`load_dist_config → None`）+ 两条「程序员错穿出去」的
  反向判据（把 `return_value` 改回 `side_effect` 即红）。G4 397 → **385**（该文件 9 → 1，剩那处是
  `_with_node_failover` 的单节点边界）、G9 26 → **25**。全文 → `docs/nn/engineering.md` §132。

- **2026-11-18（P0-7 第十七批：`hub/task_pack.py` 的观测面捕什么要写清楚）**：三个读包助手
  （`pack_index_part_sha` / `pack_index_meta` / `pack_seg_end_kind`）与三个打控制台的调用点
  （触发重导 ×2、问导出状态 ×1）原为 `except Exception`。前者是**观测面**（离线清单 + 段末判据），
  必须捕；但宽捕把本模块的 `AttributeError`（索引 JSON 从对象变列表）读成「这包没有计划」⇒
  云机拿到空段末判据、静默回退旧行为，而包其实是好的；后者把本模块的 bug 记成「控制台不可达 →
  降级为手动导出」（有人会去查隧道）。改成两个**具名异常集**：`_PACK_UNREADABLE`（OSError /
  BadZipFile / EOFError / KeyError / NotImplementedError / ValueError = 真读不出来）与
  `_CONSOLE_NET_ERRORS`（OSError / HTTPException / ValueError = 传输失败）；`AttributeError` /
  `TypeError` 穿出。判据 `tests/hub/test_offline_task_pack.py` +3。G4 385 → **379**（138 文件）。
  全文 → `docs/nn/engineering.md` §133。

- **2026-11-18（P0-7 第十八批：`hub/offline_push.py` 的读面边界 + 「查询升公开」而非抬 G5 基线）**：
  该文件 10 处 `except Exception` 混了四种东西：① 三个**契约永不抛**的读面（`traj_root()` 返回
  `Path | None`、`pack_index_meta()` 已是总函数、`_release`/`_anchor` 按 key 直取）⇒ 直接删 `try`，
  按契约的返回值写；② 「盘上那份派发记录读不出来」⇒ 新具名集 `_STORE_READ_ERRORS`
  （OSError / ValueError / KeyError）；③ `still_mine` 的 **fail-open**（读不到 ⇒ 「还是我的」）；
  ④ 真边界 2 处（pump 主循环的单轮兜底、`_claim` 的网络腿）留宽捕并留痕。删 `try` 的关键收益：
  `Path.cwd()` 原本是 `except Exception` 的**危险缺省** —— hub 读面一坏就静默往启动目录落包。
  返工记账（G5）：新增的三条判据原打 `_default_root` / `_still_mine` / `_resume_ok`，撞
  `private_assert_baseline.json`「只许降」。修法是**把只读查询升成公开名**（`still_mine` /
  `resume_ok`：不调网络、不改状态），`_default_root` 改从公开构造路径验（`root=None` 能构造 ⇒
  落到 cwd；`traj_root` 抛 `AttributeError` ⇒ 构造失败）—— 抬基线会把这批的诚实换掉。判据
  `tests/hub/test_offline_push_dispatch.py` +3。G4 379 → **371**（138 文件）、G9 25 不变。
  全文 → `docs/nn/engineering.md` §134。

- **2026-11-18（P0-7 第十九批：四条 fail-open 读面的具名集）**：`hub/http_face.py` 4→2、
  `hub/store_offline.py` 3→0、`worker/sibling_reuse.py` 3→1、`remote/bc_job.py` 4→2（G4 371→**362**）。
  共同判据一句话：**这个变量坏了，能不能被读成那个结论？** ① 请求体读：`rfile` 形状变了
  （`AttributeError`）原来答成 400「read body failed」= 把我们的 bug 说成客户端发错了 ⇒
  `_BODY_READ_ERRORS = (OSError, ValueError)`（`ValueError` 必留：两个助手都把
  `int(Content-Length)` 包在同一条 try 里）。② 回传 wire 解码：实测路面是
  `binascii.Error`(ValueError) / `EOFError`（截断 gzip，**不是** `BadGzipFile`）/ `zlib.error`
  （魔数对但 deflate 坏，`gzip.decompress` 不包装）/ `BadGzipFile`(OSError) ⇒ `_WIRE_DECODE_ERRORS`；
  两条「观测面不拖垮回传」改成 `_LAND_ERRORS`（盘错 + 行形状错）。③ 兄弟课程：`load_course` 只捕
  文件面 `(OSError, ValueError, KeyError)`；`episode_identity_fp(other)` 对**过了 pydantic 的**
  `CourseConfig` 是全函数 ⇒ try 整段删（旧的 `except Exception: continue` 让训练照跑、复用默默死掉）。
  ④ BC 两条回传腿：`_BC_HTTP_ERRORS = (OSError, RetryableError, ProtocolError)` = `_request` 的全部
  路面 ⇒ `AttributeError` 不再被记成「网络不通」而白等两轮退避。留下的 5 处都就地注释了为什么只能宽
  （handler 顶层分 400/500、torch 探针、调用方给的任意课程对象）。判据 4 处全部从公开入口/可观察结果
  验，四组反向判据改回宽捕各实测红。全文 → `docs/nn/engineering.md` §135。

- **2026-11-18（P0-7 第二十批：补传腿的「不可达 / 没送达」不许当垃圾桶）**：
  `remote/offline_deliver.py` 12 → **6**（G4 362 → **356**）。这一批的错法是一句话被合写成两句：
  **「hub 连不上」是传输事实**（补传必须忍，跳过本轮即可），**「这一发没成」是我们自己的判断**
  （不能忍）。① `probe()` / `_post()` 改捕 `_DELIVER_NET_ERRORS = (OSError, http.client.HTTPException,
  RetryableError)` = `_opener` 的全部路面 —— 旧宽捕把 `_headers()` 的 `KeyError`（配置键改名）与
  `_timeout_for` 的 `TypeError` 记成「hub 不可达 ⇒ 下轮再试」，于是坏配置伪装成网络抖动、永远重试；
  ② 构造器里 `sanitize_run_id` 只捕 `ProtocolError`（它唯一的契约失败）—— 旧宽捕把它的 bug 读成
  「run_id 空 ⇒ 补传静默关闭」；③ `backup_pending` 的 `pending()` 与 `_prune_drawer` 的
  `_ledger_its()` 收窄成 `(OSError, ValueError, KeyError)`（盘 + 账行形状）；④ `_err_text` 删 try
  （`decode("utf-8","replace")` 按定义不抛）；⑤ `_guard` 的 `BaseException` → `Exception`（后台线程
  不该吞 `SystemExit` / `KeyboardInterrupt`；内层 `pass` 是 G9 冻结的「日志写不出去 ≠ 线程该死」）。
  留下的 6 处全部就地写明只能宽的理由。判据 `tests/remote/test_offline_deliver.py` +3（全走公开面：
  断连仍不可达 / 本模块 bug 穿出 / 非法 run_id 是唯一构造失败 + 备份判据面）。
  全文 → `docs/nn/engineering.md` §136。

- **2026-11-18（P0-7 第二十一批：探活面的结论最贵）**：`remote/rollout_node.py` 8 → **4**
  （G4 356 → **352**）。判据从「捕得宽不宽」换成「结论有多贵」：`_ping` 的回答是「节点死了 ⇒
  重启 agent」，旧宽捕让一个回执形状的 `AttributeError` 也答成不健康 ⇒ **重启环**；`_status` 同集
  （后果轻些：摘要变「不可知」）；`_probe_https` 用同一集（姿势选择落到错候选）。三处共用
  `_NODE_PROBE_ERRORS = (*NET_ERRORS, http.client.HTTPException, ValueError)`（`ValueError` = 200 但
  body 不是 JSON）。`_install_bun_mirror` 的返回值是「镜像装没装上」，所以只吞「网/盘/坏 zip」
  （`_BUN_INSTALL_ERRORS` = OSError / RuntimeError / BadZipFile / EOFError）——把自家 bug 记成
  「镜像不行」等于让镜像路径永久静默失效。留下的 4 处：`_terminate` 的两级 kill（G9 冻结的收尾
  边界）、`_slot_count` 的 platform_utils 兜底（异常类型已写进 `note`）、`_open_tailscale`（异常族
  由 `tailscale_boot.ensure` 定义）。判据 `tests/remote/test_rollout_node.py` +2；反向把集改回
  `(Exception,)` 实测 `DID NOT RAISE`。全文 → `docs/nn/engineering.md` §137。

- **2026-11-18（P0-7 第二十二批：进程内判据不许宽捕）**：`remote/push_dispatch.py` 6 → **3**
  （G4 352 → **349**）。这一批立了一条分界线：**这个 try 包住的代码能不能自己坏？** 能（网络 /
  盘 / 对端进程 / 第三方 API）⇒ 宽捕是边界；不能（纯内存、同进程的两个我们自己的对象）⇒ 宽捕
  只能是垃圾桶。① `_job_priority_of` 读 hub 本地判据表：`PRIORITY_HIGHEST` 的语义是「没人在做
  这一份」⇒ 备份不派；旧宽捕把判据表换形状的 `AttributeError` 读成那件事 ⇒ **备份永不派发且
  无日志**，现在整段删 try（边界在 `run_tick_loop`）。「老 hub / 替身没有该方法」那条兼容分支
  保留，判据同时钉住两条。② `_http` 收窄成 `_PUSH_HTTP_ERRORS`（`NET_ERRORS` +
  `http.client.HTTPException` + `ValueError`），4xx/5xx 仍算「有答」；HTTPError 里读体失败收窄成
  `(OSError, http.client.HTTPException)`（状态码已经是结论）。判据
  `tests/remote/test_hub_push_dispatch.py` +1。全文 → `docs/nn/engineering.md` §138。

- **2026-11-18（P0-7 第二十三批：引导形态的两类边界）**：`remote/offline_boot.py` 19 → **16**
  （G4 349 → **346**；**生成物** ⇒ 改 `remote/_offline_boot/07_queue.pyfrag` 后跑
  `tools/build_offline_boot.py` 重建，`test_offline_boot_is_generated` 守字节一致）。standalone
  契约（notebook 从 raw 单拉、那时 `code.zip` 还没解开）让 `_progress_label` /
  `_should_log_progress` 的 `except Exception` 里混了两类**真边界**与一类垃圾桶：① **没这个包**
  ⇒ `ImportError`（常态，退回最小标签 / 恒打）；② **跨版本** —— 解开的 `code.zip` 可能是老快照，
  老 `should_log_progress` 签名不同 ⇒ `TypeError` / `ValueError`（宁可恒打，也不吞进度行）；
  ③ 我们自己读错（`AttributeError` / `KeyError`）现在穿出去 —— 否则「进度行全消失」永远被读成
  「引导形态本来就这样」，真因一次也不会进日志。判据 `tests/remote/test_offline_boot.py` +1；
  改回 `except Exception` 实测 `DID NOT RAISE`。全文 → `docs/nn/engineering.md` §139。

- **2026-11-18（P0-7 第二十四批：引导态的控制面读面）**：`remote/offline_boot.py` 16 → **13**
  （G4 346 → **343**；生成物，改分片后 `tools/build_offline_boot.py` 重建）。新点：本文件
  **不得 import 本仓代码**（standalone 运输单元）⇒ 不能复用 `common.errors.NET_ERRORS`，就地组装
  standalone 版 `_BOOT_NET_ERRORS = (OSError, http.client.HTTPException, ValueError)`，供
  `_post_json`（连不上仍 `(0, {})` + 一行日志）与 `_http_error_parts`（HTTPError 正文读不出来仍
  「（无正文）」、状态码优先）。`hub_candidates` 收窄成 `(ImportError, ValueError, TypeError)`
  （兄弟文件没拉下来 / `hub_port` 脏值）：`resolve_hub_url` 是**纯字符串**函数（没地址返空串、不抛），
  旧宽捕把它读成「没有 tailnet 候选」⇒ 云机改走公网 URL 并**白起隧道**且日志看不出少了什么。
  判据 `tests/remote/test_offline_boot.py` +2。全文 → `docs/nn/engineering.md` §140。
  累计第十六~二十四批：G4 **397 → 343**（139 → 137 文件）。

- **2026-11-18（P0-7 第二十五批：拉起子进程的那条边）**：`remote/deliver_proc.py` 7 → **4**、
  `remote/child_proc.py` 7 → **6**（G4 343 → **339**）。这两个文件都在回答「子进程起不来时该怪谁」：
  ① `_spawn` / `spawn_module` 收窄到 Popen 的真失败面 `(OSError, ValueError,
  subprocess.SubprocessError)` —— 我把 `env`/`sys.path` 拼错（`AttributeError`）报成「Popen 失败」，
  现场就会去查权限/路径/资源，而真因永远不露头；② `_new_state_path` 只捕 `sanitize_run_id` 唯一
  的契约失败 `ProtocolError`；③ `backup_pending` 收窄成盘 + 账行形状 `(OSError, ValueError,
  KeyError)`（与 §136 的 `OfflineDeliverer.backup_pending` 同一个集）。留下的 3 处是 `_stop_child`
  的两级收尾（G9 冻结）与读面，均就地注明。
  反面记录：`trainer/stream._load_wave` 同型（「坏 shard」混了我们的 `AttributeError`），但它住在
  `run_rollout_stream` 的闭包里、没有公开接缝 ⇒ **按 §7「先有判据再改」的口径暂不动**（宁可留着宽捕，
  也不留一个没人能验证的收窄）。判据 `tests/remote/test_offline_deliver_proc.py` +1、
  `tests/remote/test_child_proc_pump.py` +1。全文 → `docs/nn/engineering.md` §141。

- **2026-11-18（G5 第一批：先用已有公开面，再谈新建 seams）**：`tests/hub/test_hub_queue_split.py`
  私有断言 16 → **11**（G5 702 → **697**）。修之前先查了「公开查询是否真的不存在」—— `_HubQueue`
  早有 `queue_state()` / `active_worker_count()` / `course_of()`，用例只是习惯了伸手：`_stores` 表 →
  `queue_state()["courses"]`、`_workers` 登记 → `active_worker_count()`；`_locate_cache` / `_cursor` /
  `_leases` 三处白盒读已被公开面覆盖（`course_of` / `qs["cursor"]` / `result_token_ok` + `epoch_of`），
  删掉重复断言而不是另造接缝。
  留下的 11 处**不迁**并在就地写明：它们是**被测对象本体**（`__mro__` / `__bases__` 分簇结构对账、
  `STATE_WRITERS` 跨域写入面、`_adopt_solo` 搬状态、`_job_dir` 哨兵契约），改成公开面等于把被验证的
  东西挖掉。口径：G5 只降「耦合」，不降「被测对象」。全文 → `docs/nn/engineering.md` §142。
- **2026-11-18（G5 第二批）**：`tests/hub/test_hub_job_store_split.py` 15 → **9**（G5 697 → **691**）。
  与第一批同一口径：`lease_worker` / `result_token_ok` / `inflight` / `scheduling_facts` /
  `scheduling_epoch` / `wire_stats` 都是现成公开读数，覆盖了 `_leases` / `_lease_owners` /
  `_last_heartbeat` / `_claimed` / `_epoch` / `_wire` 六处白盒读。`inflight()` 顺带替代「有没有心跳」
  ——活租约 = 心跳晚于 claim，比读字典键更贴原意。留下的 9 处是本体（`__mro__` / 常量 / `LAYERS` /
  增量读缓存）。全文 → `docs/nn/engineering.md` §142。
- **2026-11-18（P0-6 第三批：日志行格式的第三、第四份副本）**：`biz/course.py` 的三条课程日志与
  `biz/ladder_ledger.py` 的五条 CLI 消息改走 `common/logutil.log_line`（G3 `print` 134 → **126**，
  42 → 41 文件）。`ladder_ledger` 的第五条（`graduated`）**原本没有时间戳** —— 同一次输出的行格式
  不一致正是「两份实现」的活标本，顺势补齐（本次唯一的可见行为变化）。
  `ladder_ledger.updated_at` 的 `%Y-%m-%d %H:%M:%S` 是**数据字段**不是日志行，换成本模块的
  `%H:%M:%S` 会破坏台账消费方 ⇒ 不动。全文 → `docs/nn/engineering.md` §143。
- **2026-11-18（B6 第一刀：BC 的 epoch 打乱不再用全局 numpy 流）**：`worker/train/goal_bc.py` 的
  `np.random.seed(args.seed)` + `np.random.permutation(...)` 改成
  `np.random.default_rng(list(common.runtime.substream(seed, "goal-bc-epoch")))`。原因：全局播种的
  「可复现」只在进程里没别人动 `np.random` 时成立 —— 对 BC 意味着同一命令重跑 episode 顺序就变，
  loss 曲线对不上。`torch.manual_seed` 保留（种子权重初始化是另一条流）。
  判据 `tests/worker/test_goal_bc_epoch_rng.py` 3 条（同 seed 同序 / 不吃全局流 / 外部抽样不干扰），
  其中「不吃全局流」在旧实现上实测为红。B6 残留 6 处 `np.random.seed` 已逐个记下理由（云核 /
  dataloader 全局态）。全文 → `docs/nn/engineering.md` §144。
- **2026-11-18（G5 第三批）**：`tests/hub/test_hub_discover_eviction.py` 9 → **3**（G5 691 → **685**）。
  摘课判据改问 `courses()` / `halt_of()` / `queue_state()["cursor"]`；留下的 3 处是**纯提速缓存**
  （`_peeked` / `_locate_cache`，无处可问但清了只是下次探测多花几毫秒）与 `_dispatch`/`_leases`
  的「落盘两个不动」对账。口径同前两批：只降耦合，不降被测对象。全文 → `docs/nn/engineering.md` §145。
- **2026-11-18（G5 第四批）**：`tests/hub/test_priority_schedule.py` 9 → **3**（G5 685 → **679**）。
  `job_root` / `stale_holder` / `lease_worker` / `courses` / `queue_state()["cursor"]` 全是既有公开面。
  留下的 3 处读的是**别的查询不覆盖的字段**（租约截止时刻、token→owner 映射、承诺痕迹归属人），
  不是同义改写 —— 口径仍是「只降耦合，不降被测对象」。全文 → `docs/nn/engineering.md` §146。
- **2026-11-18（度量脚本入库：`tmp/` → `nn-training/tools/audit/`）**：`py_audit.py` / `py_clones.py`
  从 gitignored 的 `tmp/` 移进仓库（`python.refactor.md` 附录 A 的明确要求）。理由：文档里每个
  基线读数都靠它们，而它们**不可复跑**（换机器/清 tmp 就只剩数字）。路径改 `Path(__file__)`
  推导，不依赖 cwd；顺手清 ruff（未用 import、空转循环、未使用循环变量）。
  口径：这一步只保证「可复跑」，**没有**把它们接成门禁 —— 每个读数都变成断言会把「看一眼」
  变成「跑一遍 CI」，需要时另开一刀并给判据。全文 → `docs/nn/engineering.md` §147。
- **2026-11-18（G5 第五批）**：`tests/hub/test_hub_leases.py` 6 → **2**（G5 679 → **675**）。
  `job_root` / `result_token_ok` / `claimable_job_ids()`（领取后立刻可领 = 没租约）/ 紧跟的
  `heartbeat(...) is True` 覆盖了 owner、到期与心跳三件事。留下的 2 处是**否定断言**（错 token
  心跳后截止时刻与心跳时刻「一动没动」）—— 公开面只说「现在能不能领」，说不出「一刻没动」。
  全文 → `docs/nn/engineering.md` §148。
- **2026-11-18（G5 第六批：采集链的公开读数）**：`tests/worker/test_rollout_volume.py`
  私有断言 46 → **2**（G5 675 → **631**）。这条用例的 46 条断言里绝大多数只是**读进度**
  （配额目标/已采、波次、初波局数、熔断、分关账、greedy、报告合并），却逐条伸进
  `_volume_*` / `_greedy_*` / `_report`。新增 `VolumeView`（frozen dataclass）+
  `TrainingVolume.volume_view()` 作为这一簇唯一的只读观测面：字段名取 `worker.events`
  iteration 行的语义（不是内部布局镜像），`report`/`stage_stats`/`greedy_stats` 是活引用
  （只读契约，不复制 dict）。留下的 2 处是 `CommitJournal.pending()`（独立的 WAL 对象，
  它自己已有公开查询；要迁得先给训练循环一个「未闭合提交」读面，另开一刀）。
  三条私有**方法调用**（`_iteration_pairs` / `_volume_stages` / `_volume_stage_ests_map`）
  移出断言表达式：驱动被测入口不是「读私有状态」。成员闭集守卫同步 +1。全文 →
  `docs/nn/engineering.md` §149。
- **2026-11-18（G5 第七批：节点工具链分家）**：`tests/remote/test_rollout_node.py` 私有断言
  43 → **8**、`test_rollout_node_notebook.py` 5 → **4**（G5 631 → **595**）。那 43 条里有 38 条
  其实在测「bun / cloudflared 从哪来」—— 镜像候选、测速排序、下载重试、持久缓存、
  `--version` 校验。它们只碰 `cfg`/`log`/文件系统，于是整段搬进 `remote/node_toolchain.py`
  （L1），45 个用例改对公开函数断言。搬家逐字：18 个函数 AST 归一化后只剩 docstring 与
  下划线前缀的差异。横幅的 `bun_version` 不再搬一份实现，改调 `common.proc.bun_version`
  （`test_reuse_ledger` 当场抓到同名 ≥3 会从 113 涨到 114；复用后不变，并顺手给了 10s 超时）。
  隧道用例同步搬去 `test_rollout_tunnel.py`（被测对象早就在那里）。
  留下的 8 处都是驱动 `_status`/`_slot_count`/`_start_cloudflared`/`_open_tailscale` 的调用。
  全文 → `docs/nn/engineering.md` §150。
- **2026-11-18（G5 第八批：in-loop 评估链的公开读数）**：`tests/worker/test_eval_timing.py`
  私有断言 32 → **0**（G5 595 → **563**，125 文件）。32 条里绝大多数只是读派发状态
  （在飞线程 / local gate / 已交棒的尾巴 / 补账队列 / 顺延位 / 在跑轮集合），却逐条伸进
  `trainer/loop_eval.py` 的五个私有槽位。新增 `EvalTimingView`（frozen dataclass）+
  `TrainingEval.eval_timing()`：`round_thread` / `gate` / `tail` / `join_sec` /
  `backfill`（元组快照，队列会被 pop）/ `pending_m` / `open_threads`。顺手给
  `_eval_thread` / `_eval_gate` 补类级 `None` 默认值（裸构造脚手架在「还没派发」时能读）。
  成员闭集守卫同步 14 → 15、import 闭集 + `dataclasses`。全文 → `docs/nn/engineering.md` §151。
- **2026-11-18（G5 第九批：推模式派发用例走公开结果面）**：`tests/remote/test_hub_push_dispatch.py`
  私有断言 17 → **0**（G5 563 → **546**，124 文件）。`hub._stores[c].get_result/store_result/
  wire_stats/scheduling_facts` 全部换成队列已有的公开门面（`hub.get_result` / `store_result` /
  `wire_stats` / `job_status(...)["computing_at"]`），派发器在飞账改读 `disp.tick()["inflight"]`
  （本来就是 `/admin/push-workers` 的快照）。不是为测试新开口；造场景的 `_publish` 与
  `_inflight[...] =` 不在 G5 口径内，保持原样。全文 → `docs/nn/engineering.md` §152。
- **2026-11-18（G5 第十批：干烧熔断执行面的读数）**：`tests/worker/test_kickstart_burn_guard.py`
  私有断言 20 → **0**（G5 546 → **526**，123 文件）。新增 `BurnGuardView`（frozen dataclass）+
  `TrainingGuardsLeg.burn_view()`：`streak` / `stop_cause` / `stop_detail` / `notified` /
  `fallback_logged`；判据本体没动。测试替身的 `_halt_calls` 改成公开 `halt_calls`（替身自己的
  记录表，不是生产状态）；`assert leg._kickstart_burn(...)` 这类驱动被测入口的私有方法调用
  移出 assert 表达式。守卫的 stdlib 闭集 + `dataclasses`。全文 → `docs/nn/engineering.md` §153。
- **2026-11-18（G5 第十一批：门禁执行面的读数）**：`tests/trainer/test_loop_gate_soft_remediate.py`
  私有断言 19 → **0**（G5 526 → **507**，122 文件）。新增 `GuardStateView`（frozen dataclass）+
  `TrainingGuards.guard_state()`：`cloud_halted` / `leg_abort` / `soft_remediate_count`。
  `_apply_verdict` / `_gate_halt_mode` 这类驱动被测入口的私有方法调用移出 assert 表达式。
  守卫三处同步：`STAYS` + `guard_state`、顶层名字闭集 + `GuardStateView`、stdlib + `dataclasses`。
  全文 → `docs/nn/engineering.md` §154。
- **2026-11-18（G5 第十二~十五批：四处执行面读数）**：G5 507 → **449**（119 文件）。
  四笔同口径——执行面状态给公开读数，驱动被测入口的私有方法调用移出 assert：
  ① `test_ppo_profile_gate.py` 12 → 0：`_profile_steps` 转正为 `profile_steps`（纯判定是契约，
  不是内部件）；② `test_remote_failure_policy.py` 15 → 1：`RemoteFailState` +
  `TrainingRemoteFail.remote_fail_state()`（连败数 / 停腿），留 1 处轮级 `_consec_fail` 另开一刀；
  ③ `test_baseline_eval.py` 16 → 0：`BaselineEvalView` + `baseline_eval_state()`（落账 wver / 在飞线程），
  份额门复用 `eval_timing().gate`；④ `test_train_ledger_wiring.py` 16 → 0：`ResumeState` +
  `TrainingLifecycle.resume_state()`（启动继承十二项 + 账本摘要），停腿复用 `guard_state()`。
  三个拆分守卫同步成员闭集 / stdlib 闭集。全文 → `docs/nn/engineering.md` §155。
- **2026-11-18（G5 第十六~十七批：调用移出 assert 与两处传输/补传读数）**：G5 449 → **400**（116 文件）。
  ① `test_kickstart_plan.py` 15 → 0、`test_ppo_watchdog.py` 13 → 0：命中的私有名都只是驱动入口
  （`_kickstart_burn` / `_remote_ppo_step` / `_bump_remote_fail`），调用赋给局部变量再断言；
  `_leg_abort` 改读 `remote_fail_state().leg_aborted`。② `test_wire_reroll.py` 12 → 2：
  `remote/wire.py` 新增 `best_rate()`（读与写分离；注入点仍必须是 `wire._BEST_RATE`），
  留 2 处 `_WIRE` / `_BULK` 拆分契约的结构断言。③ `test_deliver_mode.py` 11 → 0：
  `DeliverState` + `DeliverStateMixin.deliver_state()`（`delivered` / `metrics_sent` /
  `metrics_stat` / `metrics_unsupported` / `mode`），`status()` 的 operator 字典面不变；
  读数按 LOC 预算（腿本体 1008 行）下沉成 `remote/deliver_state.py`（L1，拓扑账本同步）。
  全文 → `docs/nn/engineering.md` §156。
- **2026-11-18（G5 第十八批：worker 名册的读数）**：`tests/worker/test_remote_serve_pool.py`
  私有断言 10 → **0**（G5 400 → **390**，115 文件）。5 条建池前置断言把
  `iter_rollout._make_pool(...)` 的调用移出 assert；另外 5 条读 `_workers` 的断言改读新增的
  `ServePool.workers()` —— 返回**活引用元组**（锁内快照），因为「跨轮复用不重建」与
  「未就绪不杀」两条契约要看同一个 worker 的身份和它的 `proc.poll()`，计数读数不够。
  全文 → `docs/nn/engineering.md` §157。
- **2026-11-18（G5 第十九批：自检判据与测试助手的名字）**：G5 390 → **376**（113 文件）。
  ① `test_payload_selfcheck.py` 7 → 0：`_old_heuristic_misjudges` / `_payload_reader_selfcheck`
  是重打循环内部的两条判据（生产自己调），不是契约 ⇒ 不为用例造公开 API，只把调用移出 assert。
  ② `test_warmup_criterion.py` 7 → 0：`tests/helpers/warmup._needed` 是测试助手自己的判据，
  整份用例测的就是它 ⇒ 改成公开 `needed`（助手 API，不涉生产）。全文 → `docs/nn/engineering.md` §158。
- **2026-11-18（G5 第二十批：派发器的线程读数）**：`tests/hub/test_offline_push_dispatch.py`
  私有断言 7 → **0**（G5 376 → **369**，112 文件）。替身字段 `_late_body` → `late_body`；
  `_adopt_existing` / `_settle_assignment` / `_settle_retry` 的调用移出 assert；新增
  `OfflinePushDispatcher.pump_count()` 回答「拉取线程退干净没有」（账本先空、线程后退出，
  与 `tick()["assignments"]` 不是同一时刻的同一件事）。全文 → `docs/nn/engineering.md` §159。
- **2026-11-18（G5 第二十一批：notebook 运行时的探测入口）**：`tests/remote/test_notebook_runtime.py`
  私有断言 8 → **0**（G5 369 → **361**，111 文件）。`_resolve_backend` / `_run_probe` /
  `_tpu_holders` / `_hub_base_of` 都是被测入口本身（前两个还是夹具注入点 / 被 monkeypatch），
  改名带不来契约收益 ⇒ 一律把调用移出 assert，判据本体不动。
  全文 → `docs/nn/engineering.md` §160。
- **2026-11-18（G5 第二十二批：greedy 槽位的读数）**：`test_paired_kill.py` 6 → 0、
  `test_greedy_rollout_chain.py` 6 → 0（G5 361 → **349**，109 文件）。paired kill 全是驱动入口
  （调用移出 assert）；greedy 链的「跨轮清零」改读新增的 `GreedyView` +
  `TrainingVolume.greedy_view()`（`plan` / `stats` / `report` / `waves`），用例脚手架换成继承
  `TrainingVolume` 的最小 `_NodeLoop`；volume 拆分守卫成员闭集 12 → 13。
  全文 → `docs/nn/engineering.md` §161。
- **2026-11-18（G5 第二十三批：补传视图补上拒收面）**：`tests/remote/test_offline_deliver.py`
  私有断言 6 → **0**（G5 349 → **343**，108 文件）。`DeliverState` 增加
  `rejected`（升序轮次）与 `ledger_readable`（抽屉删除的硬门槛，DoD-3c），
  D6 的 TTL/人工重置用例改读它；`_reject_round(...)` 的调用移出 assert。
  全文 → `docs/nn/engineering.md` §162。
- **2026-11-18（G5 第二十四批：D9 闭锁的读数）**：`tests/hub/test_hub_auth_d9_order.py`
  私有断言 8 → **0**（G5 343 → **335**，107 文件）。新增 `AuthState`（frozen dataclass）+
  `_AuthGuard.auth_state()`：`failures` / `blocked_until`，两份 dict 锁内复制（请求线程持续改写，
  测试要的是某一刻的一致快照）；`_JobStore` 继承 `_AuthGuard`，既有入口可直接读；
  `hub/queue_auth.py` 按鉴权四则同一条「单课程借 store」路径转发。`test_hub_queue_split.py`
  同步四处账本（门面闭集 34 → 35 / declared 38 → 39、DOMAINS、DOMAIN_METHODS 135 → 136、
  实现名 137 → 138）。全文 → `docs/nn/engineering.md` §163。
- **2026-11-18（G5 第二十五批：课程 store 与即时闸的公开读面）**：
  `tests/hub/test_auto_handoff.py` 私有断言 8 → **0**（G5 335 → **327**，106 文件）。
  新增 `QueueScopeMixin.course_store(course)`（课程 → store 的只读查询，未知课程 None）与
  `LedgerMixin.is_cancelled(job_id)`（进程内即时闸的当前成员，不按账本对账）；可领池断言
  改用既有门面 `hub.claimable_job_ids(course)`，租约断言改用 `lease_worker(jid) == ""`。
  守卫同步：`DOMAIN_METHODS` 136 → 137、实现名 138 → 139、store `MIXIN_METHODS` 71 → 72。
  全文 → `docs/nn/engineering.md` §164。
- **2026-11-18（G5 第二十六批：收工读数）**：`tests/trainer/test_loop_park.py` 6 → **1**
  （G5 327 → **322**，106 文件）。新增 `TrainingLifecycle.stop_reason()`（只读、不清账）与
  `collect_child()`（活引用），`finish_course` 内部同步改用；`_budget_hard_cut` 的调用移出
  assert。留 1 处 `_node_rollout` 初值断言 —— 它是**构造契约**（AttributeError 事故），
  不是执行面读数。lifecycle 拆分守卫成员闭集 7 → 9。全文 → `docs/nn/engineering.md` §165。
- **2026-11-18（G5 第二十七批：复用既有接缝）**：`test_loop_gate_nopark.py` 5 → 0、
  `test_remote_ppo_phases.py` 5 → 0（G5 322 → **312**，104 文件）。零生产改动：停机旗标读
  `guard_state().cloud_halted`、连败/停腿读 `remote_fail_state()`，两处驱动调用移出 assert。
  收编旧用例是接缝建设的一部分 —— 否则「视图没人用」与「视图没建」在基线上无法区分。
  全文 → `docs/nn/engineering.md` §166。
- **2026-11-18（G5 第二十八批：驱动入口）**：`test_boot_wire_guard.py` 4 → 0、
  `test_offline_eval_wiring.py` 5 → 0（G5 312 → **303**，102 文件）。零生产改动：
  `offline_boot` 是生成物（改名要重建发行物，收益为零），`_sync()` / `_post_artifact()` 的
  返回值就是判据 ⇒ 调用赋给局部变量后再断言。全文 → `docs/nn/engineering.md` §167。
- **2026-11-18（G5 第二十九批：租约表的两个读数）**：`test_dead_lease_sweep.py` 5 → 0、
  `test_remote_iter.py` 4 → 1（G5 303 → **295**，101 文件）。新增 `has_lease()`（有记录、
  不回收）与 `lease_state()`（四态判据的公开读数）；调度承诺三表改读既有
  `scheduling_facts()`；`_leg_abort` 读 `remote_fail_state()`，`_kill_and_reap` 调用移出
  assert。留 1 处 `_zero_shard_streak`（测试替身承载、生产直接读写的槽位）。
  store 拆分守卫 `MIXIN_METHODS` 72 → 74。全文 → `docs/nn/engineering.md` §168。
- **2026-11-18（G5 第三十批：节点监护的驱动入口）**：`test_rollout_node.py` 8 → **1**
  （G5 295 → **288**，101 文件）。`_status` / `_slot_count` / `_start_cloudflared` /
  `_open_tailscale` 的调用全部移出 assert；刻意留 1 处 `_slot_count` 作为
  `test_scan_sees_the_known_surface` 的扫描面活性锚（该自检要求本文件基线 > 0）。
  全文 → `docs/nn/engineering.md` §169。
- **2026-11-18（G5 第三十一批：纯判据与多课程 hub）**：`test_multi_course_hub.py` 5 → 1，
  `test_eval_a_once.py` 4 → 0、`test_eval_course_once.py` 3 → 0、
  `test_eval_dispatch_resilience.py` 4 → 0、`test_stage_rotation_wiring.py` 3 → 0
  （G5 288 → **270**，97 文件）。鉴权计数读 `auth_state()`、旧 store 旗标经 `course_store()`
  读 `halt_workers`；其余全是纯判据/驱动入口 ⇒ 先赋值再断言。留 1 处 `hub._job_dir`
  （队列门面里它就是 `_job_dir`，无另一份公开名）。同批修正门禁自身地板：
  `test_scan_sees_the_known_surface` 的 `len(baseline) >= 100` 改为按扫到的测试文件数
  （`>= 150`，实测 554）——旧地板在清到 97 个文件后从「防扫描坏了」变成「不许清干净」。
  全文 → `docs/nn/engineering.md` §170。
- **2026-11-18（G5 第三十二批：notebook 与信号面的小尾巴）**：`test_ppo_redo.py` 3 → 0、
  `test_seg_end_kind.py` 4 → 0、`test_rollout_node_notebook.py` 4 → 0、
  `test_lifecycle.py` 3 → 0（G5 270 → **256**，93 文件）。驱动调用移出 assert；
  notebook 命名空间里的 `_fetch_archive` 先取结果；bound method 先赋 `handler`；
  测试替身的 `"_block"` 键改公开 `"block"`。全文 → `docs/nn/engineering.md` §171。
- **2026-11-18（G5 第三十三批：三处状态读数）**：`test_bc_dp.py` 4 → 0、`test_reward_golden.py`
  3 → 0、`test_stream_alpha.py` 3 → 0、`test_admin_cancel_job.py` 3 → 0、
  `test_offline_deliver_async.py` 3 → 0、`test_offline_task_queue.py` 3 → 1、
  `test_bulk_sched.py` 3 → 1（G5 256 → **236**，88 文件）。新增 `RewardFn.backend`
  （`formula` / `builtin`）、`DelivererProcess.child()`、`StreamCollector.alpha_started`；
  其余为公开门面替换与纯判据调用移出 assert。留 `_TASK_PACK_TRIGGERS`（清单即契约）与
  `_p1_waiting`（计数器对称性白盒）各 1。全文 → `docs/nn/engineering.md` §172。
- **2026-11-18（G5 第三十四批：轮级连击与假引擎死腿态）**：`test_loop_round.py` 5 → 0、
  `test_serve_aborted_revival.py` 5 → 3（G5 236 → **229**，87 文件）。轮级 `_consec_fail`
  复用已有的 `resume_state().consec_fail`；假引擎加 `leg_aborted` 公开读数（`_leg_abort`
  保留给 `_revive_hygiene` 按槽位清账）；`_revive_hygiene` 的 `SimpleNamespace` 槽位契约
  3 条刻意保留。全文 → `docs/nn/engineering.md` §173。
- **2026-11-18（G5 第三十五批：租约到期读数与三处纯判据）**：`test_priority_schedule.py`
  3 → 0、`test_batch_runner_split.py` 3 → 0、`test_tools/test_forkdist.py` 3 → 0、
  `test_biz/test_course_spec_split.py` 3 → 2（G5 229 → **219**，84 文件）。新增
  `LeaseMixin.lease_expiry()`（纯读：这次操作有没有动过租约表）；持有人/承诺痕迹改读
  `lease_worker()` / `scheduling_facts()`；纯判据调用移出 assert。store 拆分守卫
  `MIXIN_METHODS` 74 → 75。`_block_dict` 两处身份契约刻意保留。全文 →
  `docs/nn/engineering.md` §174。
- **2026-11-18（G5 第三十六批：心跳读数、哨兵快照与十处纯判据）**：12 个文件各 2 → 0
  （G5 219 → **195**，72 文件）。新增 `LeaseMixin.heartbeat_at()`（错 token 心跳不许动
  心跳锚）与 `HubHandler.pulse_readout()`（哨兵只读快照；`pulse_events` 会推进状态）；
  其余为纯判据调用移出 assert。store 守卫 `MIXIN_METHODS` 75 → 76。转发名 / 身份 /
  兼容参数 / 归属缓存本体等 6 个文件刻意保留。全文 → `docs/nn/engineering.md` §175。
- **2026-11-18（G5 第三十七批：生命周期读数与增量读面）**：16 个文件各 1 → 0，
  `test_rollout_volume.py` 2 → 1（G5 195 → **178**，56 文件）。新增
  `common.runtime.ThreadOwner.running`（三条后台线程腿共用一份实现——三处各抄会被
  逐字节重复审计记成新增债）、`UploaderProcess.degraded`、`PrefetchStore.handoff_path`、
  `StreamCollector.plan_cap`、`_JobStore.ledger_offset`；其余为纯判据调用移出 assert 与
  既有读数替换。store 守卫 `MIXIN_METHODS` 76 → 77。`test_rollout_volume` 保留 1 处
  `_commit_journal()`（生产同名钩子 + 扫描面活性锚）。全文 → `docs/nn/engineering.md` §176。
- **2026-11-18（G5 第三十八批：派发器在飞快照）**：`test_rollout_end_event.py` /
  `test_offline_deliver_proc.py` / `test_push_priority_dispatch.py` 各 1 → 0
  （G5 178 → **175**，53 文件）。新增 `PushDispatcher.inflight_records()`（同步状态副本，
  防止把快慢读成先后）；`ctl_offset` 改读 `status()`（它本就并入子进程状态面）；断言里的
  `_traj_dir` 改读脚手架路径。全文 → `docs/nn/engineering.md` §177。
- **2026-11-18（G5 第三十九批：三处既有状态的公开读数）**：`test_rollout_dispatch_resilience.py` /
  `test_prefetch_proc.py` / `test_push_register.py` 各 1 → 0（G5 175 → **172**，50 文件）。
  新增 `common.distribution.aborted_tags()`（全表收工态副本）、`PayloadProcess.local_store`
  （降级后的进程内实现）、`PushRegister.retry_step`（退避档位）。全文 →
  `docs/nn/engineering.md` §178。
- **2026-11-18（长函数存量第一刀：replay 导出）**：`worker/eval_replays_once.py::_export`
  202 → **157** 行（拆出 `_load_games` / `_read_replay_ledger`，失败语义与返回码逐字不变），
  长函数存量 **37 → 36**，形状基线删除该文件条目——`python.refactor.md` 附录 B
  「真·下一批」第 1 项开始还债。全文 → `docs/nn/engineering.md` §179。
- **2026-11-18（长函数存量第二刀：rollout 派发）**：`trainer/rollout_phase.py::dispatch_rollout_phase`
  207 → **191** 行（拆出 `_stream_extra_kwargs`：stream 腿的 backend/update_kwargs
  注入，纯取值、不碰闭包），长函数存量 **36 → 35**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §180。
- **2026-11-18（长函数存量第三刀：离线租约领取）**：`hub/offline.py::_post_offline_lease`
  211 → **168** 行（拆出 `_offline_claim_refusal`：claim 被拒的三条 409 回执，
  文案/返回码逐字不变），长函数存量 **35 → 34**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §181。
- **2026-11-18（长函数存量第四刀：worker 服务端）**：`remote/worker_server.py::make_worker_server`
  213 → **182** 行（拆出 `_decode_job_body`：`POST /job` 体解析与 428 补传判定，
  错误文案/状态码逐字不变），长函数存量 **34 → 33**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §182。
- **2026-11-18（长函数存量第五刀：补传器构造）**：`remote/offline_deliver.py::OfflineDeliverer.__init__`
  218 → **191** 行（拆出 `_init_background_state` / `_init_probe_state` 两组状态位，
  赋值次序不变），长函数存量 **33 → 32**，形状基线删除该文件的长函数条目
  （宽参数条目保留）。全文 → `docs/nn/engineering.md` §183。
- **2026-11-18（长函数存量第六刀：Paddle PPO 更新）**：`worker/ppo/engine_paddle.py::ppo_update`
  230 → **181** 行（拆出 `_prepare_demo` / `_build_ref_cache` / `_zero_stats`，
  数值口径与循环体不变），长函数存量 **32 → 31**，形状基线删除该文件的长函数条目
  （宽参数条目保留）。全文 → `docs/nn/engineering.md` §184。
- **2026-11-18（长函数存量第七刀：run_loop CLI）**：`remote/run_loop.py::main`
  233 → **86** 行（拆出 `_build_parser()`：整张 argv 定义表，描述/默认值/help 逐字保留），
  长函数存量 **31 → 30**，形状基线删除该文件条目。全文 → `docs/nn/engineering.md` §185。
- **2026-11-18（长函数存量第八刀：评估结算）**：`worker/eval_track.py::settle_eval_summary`
  238 → **180** 行（拆出 `_scan_ledger` + `_LedgerTally` 读数对象，行过滤/去重口径
  逐字不变），长函数存量 **30 → 29**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §186。
- **2026-11-18（长函数存量第九刀：动态节点回场）**：`trainer/queue_local.py::rescan_nodes`
  242 → **184** 行（拆出 `_rescan_stale_code` / `_rescan_push_weights`，codeHash 门与
  权重重握手口径逐字不变），长函数存量 **29 → 28**，形状基线删除该文件的长函数条目
  （宽参数条目保留）。全文 → `docs/nn/engineering.md` §187。
- **2026-11-18（长函数存量第十刀：离线产物落账）**：`hub/store_offline.py::store_offline_artifact`
  243 → **181** 行（拆出 `_decode_offline_artifact` / `_write_offline_round` /
  `_artifact_land_note`），长函数存量 **28 → 27**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §188。
- **2026-11-18（长函数存量第十一刀：BC 周期评估）**：`worker/bc_eval.py::dispatch_bc_eval`
  244 → **163** 行（拆出 `_build_eval_sources` / `_run_bc_eval_one`，busy 退避与聚合
  口径逐字不变），长函数存量 **27 → 26**，形状基线删除该文件的长函数条目
  （宽参数条目保留）；新 helper 不新增 `Any`，该文件 `Any` 存量 14 → 12
  （G14 基线同步调小）。全文 → `docs/nn/engineering.md` §189。
- **2026-11-18（长函数存量第十二刀：回环冒烟）**：`hub/smoke_loopback.py::main`
  249 → **177** 行（拆出 `_smoke_parser` / `_check_round_wire` / `_assert_smoke_final`，
  计量与断言文案逐字不变），长函数存量 **26 → 25**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §190。
- **2026-11-18（长函数存量第十三刀：intent 探针）**：`worker/train/intent_probe.py::main`
  261 → **171** 行（拆出 `_probe_parser` / `_build_report`，口径与输出逐字不变），
  长函数存量 **25 → 24**，形状基线删除该文件的长函数条目（宽参数条目保留）。
  全文 → `docs/nn/engineering.md` §191。
- **2026-11-18（长函数存量第十四刀：BC 云端作业）**：`remote/bc_job.py::_run_bc_job`
  263 → **184** 行（拆出 `_check_corpus_lineage` / `_run_bc_echo` / `_load_bc_trainer` /
  `_bc_train_namespace`），长函数存量 **24 → 23**，形状基线删除该文件的长函数条目
  （宽参数条目保留）；`test_bc_job_split` 的延迟 import 断言改锚 `_load_bc_trainer`。
  全文 → `docs/nn/engineering.md` §192。
- **2026-11-18（长函数存量第十五刀：多课程服务循环）**：`trainer/loop_serve.py::serve`
  275 → **186** 行（拆出 `_discover_tick` / `_publish_heartbeat` / `_finish_serve`），
  长函数存量 **23 → 22**，形状基线删除该文件的长函数条目（宽参数条目保留）。
  全文 → `docs/nn/engineering.md` §193。
- **2026-11-18（长函数存量第十六刀：单轮执行）**：`remote/plan_run.py::_run_iteration`
  276 → **162** 行（拆出 `_round_volume` / `_apply_greedy_budget` / `_iter_manifest`），
  长函数存量 **22 → 21**，形状基线删除该文件的长函数条目（宽参数条目保留）。
  全文 → `docs/nn/engineering.md` §194。
- **2026-11-18（长函数存量第十七刀：greedy 采集）**：`trainer/loop_volume.py::_volume_collect_greedy`
  286 → **187** 行（拆出 `_dispatch_greedy_wave` / `_greedy_collected` / `_greedy_plan_games` /
  `_greedy_scan`），长函数存量 **21 → 20**；`LONG_FUNCS_BASELINE` 的 `trainer/loop_volume.py`
  2 → 1（`_volume_collect_continuous` 327 仍在余量里），`CLUSTER` 守卫 13 → 15，
  `_StubLoop` 补两个同源转发。
  全文 → `docs/nn/engineering.md` §195。
- **2026-11-18（长函数存量第十八刀：hub 入口）**：`hub/boot.py::main` 327 → **148** 行
  （拆出 `_hub_parser` / `_build_hub_queue`），长函数存量 **20 → 19**，形状基线删除
  该文件条目。同日记录三处**结构跳过**（`rollout_node.run` / `bc_dispatch.dispatch_bc_corpus` /
  `loop_volume._volume_collect_continuous`）：均为需先造上下文对象的闭包式编排，
  理由见 §196，不是欠账消失。全文 → `docs/nn/engineering.md` §196。
- **2026-11-18（长函数存量第十九刀：杠杆扫描 tools）**：`tools/lever_scan.py::main`
  204 → **164** 行（拆出 `_print_panel`：§1 面板体检 + §2 税基尺寸表），长函数存量
  **19 → 18**，形状基线删除该文件条目。生产侧余量均为闭包式编排，先收工具面。
  全文 → `docs/nn/engineering.md` §197。
- **2026-11-18（长函数存量第二十刀：TPU 探针 tools）**：`tools/tpu-probe.py::main`
  207 → **151** 行（拆出 `_tpu_parser`：整张 argv 表），长函数存量 **18 → 17**，
  形状基线删除该文件条目；notebook 脚本 cell 由 `sync_tpu_probe_nb.py` 重新生成。
  全文 → `docs/nn/engineering.md` §198。
- **2026-11-18（长函数存量第二十一刀：空间探针 tools）**：`tools/spatial-probe.py::main`
  232 → **152** 行（拆出 `_probe_linear_bufa` / `_probe_region16`），长函数存量
  **17 → 16**，形状基线删除该文件的长函数条目（宽参数条目保留）。
  全文 → `docs/nn/engineering.md` §199。
- **2026-11-18（长函数存量第二十二刀：Wave2 判据 tools）**：`tools/wave2_judge.py::main`
  270 → **189** 行（拆出 `_load_judge_rows` / `_print_health` / `_print_point_deltas` /
  `_print_tail`），长函数存量 **16 → 15**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §200。
- **2026-11-18（长函数存量本轮收口：37 → 15）**：共 22 刀纯搬移，每刀独立提交并过
  全量门禁。余 15 个均为闭包/局部状态编排或超长报表（`rollout_node.run` 312、
  `bc_dispatch.dispatch_bc_corpus` 316、`offline_boot.run_one_course` 323、
  `loop_volume._volume_collect_continuous` 327、`worker.worker_loop` 334、
  `notebook_runtime.run_push_worker` 336、`bc_paddle.train` 372、`job_round.run_one_round`
  396、`offline_eval.run_cloud_eval` 401、`hub_client.publish_job` 418、`worker.run_job`
  439、`stream.run_rollout_stream` 442、`loop_remote_job._remote_ppo_publish` 444、
  `bc.train` 456、`paired_power.main` 625），纯搬移无干净切面；逐条理由与下一步接法
  → `docs/nn/engineering.md` §201。**不是欠账消失**：门禁读数仍是 15。
- **2026-11-18（长函数存量第二十三刀：配对功效报表 tools）**：`tools/paired_power.py::main`
  625 → **35** 行（按 §1–§11 报表段拆出 11 个 `_print_*` helper，打印与判据逐字不变），
  长函数存量 **15 → 14**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §202。
- **2026-11-18（长函数存量第二十四刀：采样节点会话对象 remote）**：`remote/rollout_node.py::run`
  312 → **175** 行（新增 `_Tunnel` / `_Session` 两个上下文对象，原 `_get_postures` /
  `_open_cloudflared` 闭包与注册 / 重启 / 隧道自愈块搬进方法；`run` 保留监护循环骨架与
  try/finally 收线），长函数存量 **14 → 13**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §203。
- **2026-11-18（长函数存量第二十五刀：BC 派发槽位上下文 worker）**：
  `worker/bc_dispatch.py::dispatch_bc_corpus` 316 → **185** 行（152 行的 `slot_worker`
  闭包搬成 `_Corpus.run_slot`，计数改 `_Stats` 字段表，`_is_busy_hint` 提为模块级），
  长函数存量 **13 → 12**，形状基线删除该文件的长函数条目（宽参数条目保留）。
  全文 → `docs/nn/engineering.md` §204。
- **2026-11-18（长函数存量第二十六刀：连续配额循环 / 收官 trainer）**：
  `trainer/loop_volume.py::_volume_collect_continuous` 327 → **48** 行（新增 `_Continuous`
  常量快照，循环与收官分别搬进 `_volume_continuous_loop` / `_volume_continuous_settle`），
  长函数存量 **12 → 11**，形状基线删除该文件条目；测试侧 `_StubLoop` 与
  `test_loop_volume_split.CLUSTER` 同步登记。
  全文 → `docs/nn/engineering.md` §205。
- **2026-11-18（长函数存量第二十七刀：推送守候两条分支 remote）**：
  `remote/notebook_runtime.py::run_push_worker` 336 → **99** 行（already_serving 分支与
  主分支的守候循环搬成 `_wait_existing_serving` / `_wait_started_serving`），
  长函数存量 **11 → 10**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §206。
- **2026-11-18（长函数存量第二十八刀：云评估逐局与轮内重投 remote）**：
  `remote/offline_eval.py::run_cloud_eval` 401 → **182** 行（新增 `_eval_preflight` /
  `_EvalPrep` / `_EvalRound`，逐局闭包搬成 `run_one`，轮内重投与结算搬成
  `run_rounds` / `finish_round`），长函数存量 **10 → 9**，形状基线删除该文件条目；
  命名避开同名散落账本（`run_round` → `run_rounds`、`settle` → `finish_round`）。
  全文 → `docs/nn/engineering.md` §207。
- **2026-11-18（长函数存量第二十九刀：每 job 一轮的会话对象 remote）**：
  `remote/job_round.py::run_one_round` 396 → **75** 行（新增 `_Round`，旁路装配 / 执行 /
  四档异常与收尾分别为 `start` / `_execute` / `run`），长函数存量 **9 → 8**，
  形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §208。
- **2026-11-18（长函数存量第三十刀：Paddle BC 训练循环 worker）**：
  `worker/train/bc_paddle.py::train` 372 → **138** 行（新增 `_Train`，
  `run_epochs` / `finalize` 两段搬出；`args` 作方法参数以免新增 `Any` 字段），
  长函数存量 **8 → 7**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §209。
- **2026-11-18（长函数存量第三十一刀：Torch BC 训练循环 worker）**：
  `worker/train/bc.py::train` 456 → **145** 行（`_resolve_class_weights` + `_Train`：
  `run_epochs` / `_emit_epoch` / `finalize_bc`），长函数存量 **7 → 6**；
  形状基线删除该文件条目，并清掉三条已归零的历史条目（`train_core` / `dispatch` /
  `eval_dispatch`）。全文 → `docs/nn/engineering.md` §210。
- **2026-11-18（长函数存量第三十二刀：离线单课编排 remote）**：
  `remote/offline_boot.py::run_one_course` 323 → **183** 行（改生成源
  `_offline_boot/06_run.pyfrag`，拆出 `_prepare_roots` / `_choose_pack` /
  `_probe_hub` / `_deliver`；ctx 用 `SimpleNamespace` 以保持独立导入），
  长函数存量 **6 → 5**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §211。
- **2026-11-18（长函数存量第三十三刀：发布编排 remote）**：
  `remote/hub_client.py::publish_job` 418 → **23** 行（按原 `# 4)`/`# 5)` 段切三相：
  `_publish_facts` / `_publish_stage` / `_publish_commit`；中间形状 `PublishFacts` /
  `PublishStaged` 下沉 `remote/publish_spec.py`，本地 55 行绑定块删除），
  长函数存量 **5 → 4**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §212。
- **2026-11-18（长函数存量第三十四刀：worker 轮询壳 remote）**：
  `remote/worker.py::worker_loop` 334 → **196** 行（while 体一行未动；壳外四段搬进新模块
  `remote/worker_session.py`：`hub_plan` / `acquire_retry` / `yield_self_check` /
  `close_worker`；接缝件 `_setup_bulk_yield` / `_ensure_pf_store` / `_start_services` /
  `_poll_halt` 与 `make_uploader` 调用点因 `W.*` patch 点留在宿主），长函数存量 **4 → 3**，
  形状基线 `remote/worker.py` 2 → 1。
  全文 → `docs/nn/engineering.md` §213。
- **2026-11-18（长函数存量第三十五刀：作业壳 remote）**：
  `remote/worker.py::run_job` 439 → **134** 行（七段相位搬进新模块
  `remote/job_shell.py`：`precheck_job` / `land_materials` / `bc_or_none` /
  `resolve_job_weights` / `run_iter_phase` / `check_mode_and_d14` / `echo_smoke_result`，
  状态住 `JobState`；热替换护栏与训练核调用留在宿主），长函数存量 **3 → 2**，
  形状基线删除 `remote/worker.py` 条目。同批把 `_bulk_yield_params` /
  `_setup_bulk_yield` / `epoch_mult_of` 下沉 `worker_session.py`（worker LOC 996 → 807）。
  全文 → `docs/nn/engineering.md` §214。
- **2026-11-18（长函数存量第三十六刀：流式迭代 trainer）**：
  `trainer/stream.py::run_rollout_stream` 442 → **112** 行（嵌套闭包搬进同文件的
  `_StreamRun`：collector 回调 / wave 装载 / drain / eval 闸 / 预采播种 / 收尾；
  输入与运行时状态收进 `_StreamCtx`；PPO RNG 派生留在宿主函数体，模块顶层仍零 torch），
  长函数存量 **2 → 1**，形状基线删除该文件条目。
  全文 → `docs/nn/engineering.md` §215。
- **2026-11-19（长函数存量第三十七刀：发布相位 trainer）**：
  `trainer/loop_remote_job.py::TrainingRemoteJob._remote_ppo_publish` 444 → **192** 行
  （七段相位拆成本类 `_remote_*` 助手：guard_plan / transport / course_and_shards /
  code_snapshot / kickstart_demo / pack_inputs / after_publish / export_bundle），
  长函数存量 **1 → 0**——`LONG_FUNCS_BASELINE` **清空**（存量清零，此后一律按新增判）。
  全文 → `docs/nn/engineering.md` §216。
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

## §2026-11-13-goalnn-notebook-idle-stop（云机 worker 页空闲即停会话：hub 不可达 / 队列静默）

- **决定**：伴生脚本新增面板开关「空闲自动停会话（省配额）」（**全局**键 `bnc:idleStop`，默认关）：每 60s
  问一次 dashboard 新增的只读端点 `GET /api/hubQueue`（逐课合成 `pending`/`inflight`/`pendingUnheld`/
  `heldLive`，与总览同一条 `liveHub()` 腿）；整条队列静默（`pendingUnheld=0 ∧ inflight=0`，自主盘再加
  `heldLive=0`）满 10 分钟 ⇒ `empty`，连续 3 拍问不到 ⇒ `unreachable`；判决成立后走 60s 倒计时（可取消），
  再按角色停会话（Kaggle host `stopNow()`：先 flush 再点外层 Stop；Colab/AI Studio/魔搭走本页 `stopSession`）。
  每一步落 op（`idleStop:on/off/miss/quiet/due/cancel/stop`）。
- **被否决**：① 页面直接问 hub（伴生脚本的 `@connect` 只有回环，隧道域名/tailnet IP 不可枚举，且 hub token
  不该进浏览器）；② 以「cell 结束」为判据（worker 启动后空转与训练结束后静默同形，cell 状态读不到也不可靠）；
  ③ 一拍就停（两轮之间训练侧还没发下一份 job ⇒ 白掐一次会话）；④ 按页存开关（Kaggle 面板在外层页，里层
  不生效）；⑤ 把在飞当「没活」（本机正在算的那份就是 inflight，缺它 = 掐掉自己的 job）；⑥ 只看
  `pending`/`inflight`、不看课程 live hold（自主盘整课在本机跑时 hub 队列全静默，开训 10 分钟后会掐掉在训
  会话；协作盘反过来要剔除被 hold 压住的 pending）。
- **后果**：云机会话在「没活可干」时自己收摊，不再空烧配额；hub/队列的读面完全不变（新增端点只读、`activity:false`
  不进空闲闸）。旧版 dashboard 没有该端点 ⇒ 页面按「问不到」计拍，3 拍后同样停会话（行为对旧版仍是「省配额」）。

—— 全文（背景 / 通道 / 边界 / 验证）→ `docs/decisions/details/goalnn.md` §2026-11-13-goalnn-notebook-idle-stop

## §2026-11-13-goalnn-offline-bc-kind-dir（自主云机领不到 BC 课：kind 判据改读权威 curricula + `pending_export` 只属于 RL）

- **决定**：hub 的 BC/RL 判据从「`<discover_root>/../curricula` 找 `<课>.bc.jsonc`」改成读
  `hub.store_offline.CURRICULA_DIR`（与归档/控制台同一份）；`begin_pending_export` 对 BC 课返回
  `bc_course`（**不写** `pending_export`），缺包 claim 映射 409 `bc_course`；`offline_stalled`
  忽略 BC 课的历史 `pending_export` 记录。
- **被否决**：保留 `<discover_root>/../curricula` 并加「不存在再回退」——把「curricula 放哪」
  重新变成隐式部署优先级；在 `queue_offline` 另抄一个 `CURRICULA_DIR`——与归档/控制台那份常量
  必然漂移；在 claim 入口新增 hub 域方法做 BC 判断——白改成员计数/门面清单，且缺包腿之后的
  BC 门仍然够不着。
- **后果**：控制台起的 hub（`--traj-root <repo>/tmp`）下每门 BC 课的清单行是 `kind=bc` ⇒
  自主云机走 `run_bc_course`（代码 bootstrap + `/jobs/{id}/claim`），不再每拍读回
  `pending_export` 空转；盘上历史记录被读面忽略（不报假停滞）。**hub 需重启**才生效。

—— 全文（背景 / 根因链 / 验证）→ `docs/decisions/details/goalnn.md` §2026-11-13-goalnn-offline-bc-kind-dir

## §2026-11-13-goalnn-bc-device-auto-and-mmap-warning（BC 云机两条 warning：`auto` 设备未解析 / memmap 只读数组喂 torch）

- **决定**：① 设备归一化的唯一实现下沉到 `remote.bc_job.normalize_job_device(device, backend, *,
  paddle_probe=…)`；worker 的 `run_job` 与离线引导的 `_run_bc_once` 都调它 —— BC 腿在
  `worker_loop` **之前**把 `auto` 解析掉（TPU 机上落 cpu：BC 训练核无 XLA 路径，`_bc_device`
  会拒 tpu）。② `NNDataset.__getitem__` 对只读数组逐帧 `np.array` 复制（lazy 的 obs/scalars/extra
  是 `mmap_mode="r"` 只读视图），消掉 torch collate 的「NumPy array is not writable」UserWarning。
- **被否决**：① BC 腿直接调 `notebook_runtime.resolve_device`——它会把 auto 解析成 `tpu`，随后
  `_bc_device` 响亮拒单（BC 完全跑不了），TPU 机的正确落点是 cpu；② 只屏蔽那条 UserWarning——
  torch 明确要求「copy the array or make it writable」，且未来版本可能直接拒收；③ 换自定义
  collate——多一份 batch 组装语义（mask 分片/scalars）要维护，收益只是省一次 memcpy。
- **后果**：设备落点与 worker 兜底结果**逐值相同**（torch：cuda/cpu；paddle：问 paddle），
  但不再经过「未经解析」告警分支；lazy 语料每样本多一次 ~11 KiB memcpy（eager 路径零成本）。

—— 全文（背景 / 备选与否决 / 验证）→ `docs/decisions/details/goalnn.md` §2026-11-13-goalnn-bc-device-auto-and-mmap-warning

## §2026-11-13-goalnn-bc-tpu-xla（BC 上 TPU：训练核接 XLA 路径，`auto` 解析 CUDA → TPU → CPU）

- **决定**：① `worker/train/bc.py` 接 `worker/ppo/np_core` 的 XLA 助手：训练步走
  `optimizer_step`（`xm.optimizer_step`）+ 每批 `xla_mark_step`；新增 `_setup_xla`
  （编译缓存 → 指纹/速度自检 → **非 TPU 的 XLA 后端拒跑**，与 PPO `train_core` 同一套判据）；
  `_resolve_bc_device` 把 `tpu`/`xla` 映成 `xla_device()` 句柄。② 权重路径全部走 CPU 副本：
  `best_state` 用 `to_cpu_batch` 一次批量 D2H、每 epoch 回传/ckpt 用 `HostModule`、参数计数
  挪到 `.to(dev)` 之前；XLA 上 masked CE/acc/value-MSE 改**静态形状**（布尔索引会按有效样本数
  每批重编译）。③ `remote/bc_job._bc_device` 透传 tpu/xla；离线引导 `_run_bc_once` 的 `auto`
  改用 RL 腿同一份 `resolve_device`（`use_multi_gpu=False`：BC 多卡仍须显式 cuda-dp）。
- **被否决**：① BC 腿继续调 `normalize_job_device`（`auto` 永不落 tpu——TPU 机永远跑 CPU）；
  ② 直接 `torch.device("xla")`（部分 torch_xla 版本拿不到带序号的句柄，PPO 已踩过）；
  ③ 让 `HostModule`/批量 D2H 只在收尾用、每 epoch 逐张量 `.cpu()`（42 参数 ≈10s/次 × 500 epoch）；
  ④ 不改 `_masked_ce` 的布尔索引（XLA 图签名随批内有效样本数漂移 ⇒ 每批重编译）。
- **后果**：TPU 云机上的 BC 真正跑在 XLA 设备上；设备解析/指纹/拒跑与 PPO 同源。CPU/CUDA 路径
  逐位不变（所有 XLA 分支都有 `is_xla` 门）。边界：单设备 XLA（8 核 SPMD 见
  §2026-11-13-goalnn-bc-tpu-spmd-8core）；每批 `.item()` 仍是一次设备同步（诊断读数要它）。

—— 全文（背景 / 备选与否决 / 验证 / 边界）→ `docs/decisions/details/goalnn.md` §2026-11-13-goalnn-bc-tpu-xla

## §2026-11-13-goalnn-bc-tpu-spmd-8core（BC 吃满 TPU v5e-8 的 8 核：单进程 SPMD 数据并行）

- **决定**：`worker/train/bc.py` 在 XLA 多核（`global_device_count > 1`）时开 **单进程 SPMD**：
  `_xla_spmd_mesh` 先 `xr.use_spmd()`（**必须先于任何 runtime 初始化**——`global_device_count()`
  本身就会建 PJRT client，先数设备会走 force 分支并在 `xla_device()` 处崩）再建 1-D `data` mesh
  （核数用 SPMD 的 `global_runtime_device_count()`）；
  `_shard_model` 把参数**复制**到各核，`_shard_batch` 把 obs/scalars/extra/标签/掩码/return 按
  dim0 切到 `data` 轴；`make_loaders(drop_last=<SPMD>)` 保证整批可整切、`_assert_spmd_batch`
  要求 `--batch` 是核数倍数；SPMD 下训练步走**裸 `opt.step()`**（复制参数上的梯度已由 XLA
  归约，再调 `xm.optimizer_step` 会二次归约、放大 world_size 倍）。单核/不可用 ⇒ 原单设备路径。
- **被否决**：① `xmp.spawn` / `torchrun` 多进程数据并行——BC 的 job 生命周期（单份
  stdout / resume / on_epoch 回传 / result）都在 `worker_loop` 一个进程里，多进程要重做 8 份
  rank 收口；② SPMD 下仍调 `xm.optimizer_step`——`reduce_gradients` 可能再 all-reduce 一次；
  ③ 不 drop_last、靠 ragged tail 切分——`mark_sharding` 不做非整切分；④ 切分参数（tensor
  parallel）——73K 参数的小模型上收益为负，复制+数据并行才是正解。
- **后果**：v5e-8 的 8 核全部参与前向/反向；**global batch 语义不变**（mb=256 ⇒ 每核 32，
  LR/优化步数不变）。代价：尾批丢弃（每 epoch ≤ mb−1 个样本，g2b 877k 语料 ≈0.03%）；
  每步多一次跨核 all-reduce（小模型上是主要新增开销，但仍远小于 8× 吞吐收益）。
  CPU/CUDA 路径逐位不变（`drop_last` 缺省 False、mesh=None）。`world_size()` 在无复制时恒为 1，
  核数判据用 `global_device_count`。

—— 全文（背景 / 备选与否决 / 验证 / 边界）→ `docs/decisions/details/goalnn.md` §2026-11-13-goalnn-bc-tpu-spmd-8core

## §2026-11-13-goalnn-inference-rules-course-block（评估链推理规则：课程 `inference_rules` 块 + 部署面英文名）

- **背景**：顶钢空枪门（历史 ID B1）确认 +6.00pp 后需要可部署的开关，但训练链必须恒关——规则会改写 PPO/DAgger 的动作分布，训练语料不再对应模型自身能力。旧方案 `--b1-gate` 是单点 CLI 且规则 ID 不直观。
- **决定**：课程文件新增 `inference_rules` 块（默认全关；`extra="forbid"` 拼错即拒），当前唯一键 `skip_steel_only_shots`。只由 eval 链消费：`eval_course_once` → `args.skip_steel_only_shots` → 本机 `run_local_eval_game` / 远端 `fetch_task`（`?skipSteelOnlyShots=1`）→ agent 拼 `--skip-steel-only-shots`；节点能力位 `skipSteelOnlyShotsSupport`（旧 agent fail-closed）；任务键后缀 `:skipSteelShots`；报告归因 `inferenceRules.skipSteelOnlyShots`。规则 ID（B1/A3…）只作历史实验索引；配置/CLI/报告/节点参数一律用英文描述名。
- **被否决**：① 评估链默认开——+6pp 只在 ep80/hard 上确认，其他 checkpoint/难度未验证，且历史读数会静默换口径；② 只保留 `export-eval-game` 手动开关——课程 judge/dashboard 永远用不到；③ 统一 `--inference-rules` bitmask——当前只有一条规则，先用课程块，等第二条规则过确认再抽象。
- **后果**：课程不写块 = 行为逐字节不变；该块不进 `corpus_identity_fp`；训练 rollout 命令不读该开关（行为测试 + TS 源码门禁钉住）。代价：开启批次只派给有 `skipSteelOnlyShotsSupport` 的新 agent，旧节点被 fail-closed 排除。

—— 全文 → `docs/nn/training-stack.md` §74

## §2026-11-13-goalnn-break-bricks-v1-negative（拆砖免绕路 v1：判负降 dormant）

- **背景**：部署面已支持课程 `inference_rules.break_bricks_on_path`（默认关；本机/远端/agent/能力位/任务键全链透传）。v1 定义 = 所选 move 方向被砖挡（hold）或 2 格内有砖（advance）且策略没开火时，朝该方向强制拆墙；基地保护圈砖不拆。
- **决定**：175 对 screening（两臂 B1 开，ep80 × classic35-safe-base × seeds 2000002001-2005）control **46.86%** → rule **43.43%**，Δ **−3.43pp**；McNemar b=37/c=31 **p=0.545**；按关 bootstrap 95% CI **[−12.0, +5.14]**；kills 15.41→14.90。win 无改善 + CI 跨 0 ⇒ **不做确认/部署，B2 v1 降 dormant**；B1×B2 组合不再做。
- **被否决**：① 以 pilot 继续冻结 20-seed 确认（点估计为负，先验方向不成立）；② 直接部署（违反“win 无显著改善一律不部署”）；③ 删除部署面（保留为默认关的实验开关，后续变体需新预注册）。
- **后果**：`break_bricks_on_path` 默认 false，课程不写 = 行为逐字节不变；训练链仍不认该开关（源码门禁 + 行为测试）。B2 变体（如只在“砖是唯一阻挡且目标在砖后”时触发）需新预注册 + 新筛选段。

—— 全文 → `docs/nn/training-stack.md` §74

## §2026-11-14-goalnn-dispatch-slots-and-keepalive（派工槽位不再取小 + 节点 workers ×2 + 长连接池 + 磁盘阈值可配）

- **现场（gcs，2 物理核，2026-11-14 00:29）**：30min 599 局（rollout 219 / eval 380）＝ 20 局/分；节点侧 `svc 2.16s / wall 9.33s` ⇒ 槽位占用 36%。派发日志 `concurrency=2` 而配置是 4——`node_slots = min(config, cpus)` 把操作者旋钮吃掉一半。传输：新建连接 0.77s / 复用 0.32s，结果包 163KB ⇒ 瓶颈不是带宽。
- **决定**：① `node_slots(configured, cpus, workers)` = 显式 config 优先，缺省 `ping.workers → cpus`；`/v1/ping` 增报 `workers`；rollout/eval/rescan/batch 四处共用（**修订 §2026-11-11 决定 C**）。② 节点 `CFG.workers=0` auto = `min(有效核, 物理核) × 2` + `workers 自检` 行（`★ workers 超额认购`：ping ≥1.5×基准且 ≥1s）。③ 派工顺序按 50 局 `wallSec` 窗口升序，**只排序不减槽**（`policy.nodeOrderBySpeed`，K=50 / 冷启动 5 局；窗口每轮现读 `dist-agent-meta.jsonl` 尾部，不驻留内存）。④ `common/http_pool.py` thread-local keep-alive，**每请求刷新 timeout**，**白名单重发**（收工态与 `/v1/task` 不重发）。⑤ `CFG.disk_floor_mb/disk_warn_mb` 可配（默认 2048/4096 不抬高）+ agent 两个 CLI + 10min 周期行 + 档位切换即时行（同一次 `/v1/status`）。
- **被否决**：`min(config, workers)`（节点没重启时等于没修）；速度加权减槽 / 中央列表调度；批量派发 + 跨局 gzip（实测 +0.5% / −2.3%）；「传输失败一律重发」（sync `/v1/task` 在 agent 侧不幂等，会重复开局）；把拒收地板默认抬到 4~5GB（§2026-10-09 已否）。
- **后果**：派工线程可超过节点池容量（多出的是准入排队，不是故障），护栏 = `[dist]` 超额告警行 + `admission.waiting`；`workers=物理核×2` 在 2 核机上是 §2026-11-11 事故同型配置，护栏 = `★ workers 超额认购` 行 + `CFG.workers=物理核` 回退；eval 与 rollout 共用一条并发旋钮（用户裁定），抢槽风险由 `eval_slot_cap` 让位兜底并记在风险 R8。三个回退旋钮都不改代码：`CFG.workers` / `nodeOrderBySpeed=0` / `NODE_HTTP_POOL=0`。
- **验收**：`tests/common/test_http_pool.py` 8 条 + `tests/trainer/test_node_speed.py` 6 条 + `test_node_probe_budgets.py` 7 条 + `tests/remote/test_rollout_node.py` + `tests/agent/disk-level.test.ts` 17 条；`bun run check` 2780 passed、`bun run build` 过、`tests/{common,trainer,remote}` 全绿、`nn-python-gate` 绿。

—— 全文（现场读数 / DoD / 风险 / 上线顺序）→ `plan/node-throughput-and-keepalive.plan.md`；链路细节 → `docs/nn/remote-transport.md` §152

## §2026-11-14-goalnn-g4-special-course（g4-special：替换 g4-precision 的生存/特殊兵种臂）

- **背景**：g4-precision 100 轮中期判负（胜率 −3.1pp vs it290；空射税只买到“少开枪”，远距/背击 gate fail）；g4-mobility 的胜率增益全部来自命耗尽 −1.59pp，机动机制（stuck/wall/freeze/brick）没动。用户裁定：每个关卡敌人种类/数量固定 ⇒ **不做 wKillPower/wKillArmor**；课程名 **g4-special**，不升序号；bc = `g4-mobility.it125`。
- **决定**：新建 `nn-training/curricula/g4-special.jsonc`（500 it / 24h）。奖励 = g3 收入骨架 + mobility 生存四件套（threat/hurt/postHit/encl），**不新增任何价格项**；删除 precision 的 miss/back/side/far 与 mobility 的 stuck/wall/freeze/brick；低血/爆发税不重蹈（y21-chain2 判低血税“空气”，x24-stack5/x26-obs 判 wBurst 稀疏税判阴，damageWhileLow/damageBursts 只作读数）。训练侧 `rotation_mode: pinned` 钉 13 个失败贡献最高的关（2006/2007/2008/2010/2011/2013/2018/2019/2020/2024/2026/2030/2034），每轮 2 关余额；eval 仍全 35 关 ×20 seed。主判：it400–500 胜率 ≥83.5%，lives_ex ≤10.5%，hard13 ≥72.0%；守卫 stop ≤5% / cells ≥140 / max_ticks ≤6% / shots ≥70。
- **被否决**：① 按兵种击杀定价（固定关集下等于给必经事件加价）；② 继续给 g4-precision 加价或续跑（§15.5）；③ 加大 stuck/wall 税修超时尾（g4-mobility 已证机制不动，瓶颈是能力不是价格）；④ 用 it125 单点 11.0% 当基线（同 arm it120 是 14.57%；用 110–130 五点窗口 12.26%）。
- **后果**：g4-precision 由人在控制台停，本改动不操作训练开关；g4-special 新 out/traj/backup，禁与 g4-mobility/precision 续跑；bc 已在 `nn-training/weights/in-use/` 留备份（§379）。课程加载 + `test_jsonc_courses_load` 已过；开课仍由人在控制台点。

—— 全文（价目校准 / 判据 / 风险）→ `nn-training/curricula/g4-special.jsonc`

## §2026-11-14-goalnn-node-transport-attribution（节点池计数按课程/用途分桶 + drop 原因 + `NO_PROXY` 让 LAN 直连）

- **现场（2026-11-14 评审，实测而非推断）**：① 原 plan 的根因「同步 `/v1/task` 是 close-delimited」**不成立**——Bun 1.4.4 对流式 `Response` 自己就发 `Transfer-Encoding: chunked`（`curl -D -` + `resp.chunked=True/will_close=False`），显式加该头输出逐字节无差别；拿真 `common.http_pool.request` 打**逐字节复刻**的 sync task 形状三连发 = `reuse=2 connect=1 drop=0`。② 真正每请求重连的是 **LAN 三台**：`NO_PROXY` 里写着 `192.168.0.0/16`，但 `proxy_bypass()` 在 Windows 读注册表、不读 env ⇒ a96/a97/a98 全走 `127.0.0.1:7890`，代理对每条响应回 `Connection: close`（同一 `/v1/status`：经代理 3 发 0.282s / 直连 0.109s）。③ `[dist] round done` 的池计数是**进程级 + 读后清零**，而一个 serve 进程并发跑 10+ 课程（`y26-clear`/`g4-special`/`o2-gq`/`z2-*` …）⇒ 原 plan 的「connect=79 ≈ 65 局」是错误归因。④ kaggle/dsw 隧道本身 keep-alive 正常（`Connection: keep-alive`，`num_connects=0`）。
- **决定**：① `http_pool` 计数按 **`<course>/<kind>` 桶**存，`stats_suffix(bucket)` 与 `pool_round_suffix()`（rollout+eval 两段）打到 `[dist] round done`；② `drop` 拆原因 `will_close|error|abort|redirect|idle`（「掉在哪一跳」是这条链上唯一值钱的读数）；③ `net_http.env_no_proxy` 读 `NO_PROXY`（`*`/精确/`.suffix`/IPv4 CIDR），`bypass_proxy` = 回环 + 它，`urlopen`/`racing_urlopen`/池三处同判据；**不硬编码私网段**（尊重 operator 的字面配置）；④ kind 由 URL 推断（`/v1/task|result` 按 `mode=eval` 分 rollout/eval），eval/batch 腿用 `set_request_tag(tag, kind=…)` 覆盖；⑤ idle 老化 `NODE_HTTP_POOL_IDLE_SEC` **默认 0（不老化）**——常量等现场读数，不拍 120s。
- **被否决**：给 agent 加 `Transfer-Encoding: chunked`（空操作，前提已被证伪）；`SAMPLER_TASK_CHUNKED` 开关与 `taskChunkedSupport` 能力位（把 Bun 的既成事实写进协议 = 噪声）；硬编码 `10./172./192.168.` 绕开代理（与 §2026-11-11 的「判据只看 host，不做 LAN 例外」冲突，且换台机器就变味）；HTTP/2 多路复用与跨 it 进程池（缓议，理由改为「先查清连接为什么掉」）；把 idle 超龄写成 120s。
- **后果**：LAN 三台恢复 keep-alive 且少一跳；日志行新增两段 `pool[rollout] … pool[eval] …`（旧字段位置不变）；`NODE_HTTP_POOL=0` 的回退腿也绕开 `NO_PROXY` 网段（两条腿同政策）；**已知 latent**：https 目标经代理时池路径建的是 `HTTPConnection` + `set_tunnel`（缺 TLS 包装）⇒ 会得到「plain HTTP to HTTPS port」，生产未触发（控制台把 `.trycloudflare.com` 塞进 `NO_PROXY`，走直连），记在 §153 待修。
- **验收**：`tests/common/test_http_pool.py` 13 条 + `tests/common/test_net_http_env_no_proxy.py` 8 条；`nn-python-gate` 6054 passed；现场读数：a96/a97/a98 三连发 = `connect=1 reuse=2 drop=0`。

—— 全文（现场读数 / DoD / 分步）→ `plan/task-stream-chunked-keepalive.plan.md`；链路细节 → `docs/nn/remote-transport.md` §153

## §2026-11-14-goalnn-safe-base-single-layer（classic35-safe-base 基地环单层化：只改环 8 格，环外恢复 classic35）

- **背景/决定**：整格钢把 5 个 13×13 源格连带环外 12 格变成钢，S9 Twin Towers 等原本没有口袋的关被挤成出生点口袋（用户 2026-11-14 指出）。改为半格钢码 `(5,11)=20 BR / (6,11)=9 BOTTOM / (7,11)=19 BL / (5,12)=8 RIGHT / (7,12)=10 LEFT`；26×26 只差环 8 格（brick→steel），环外与 classic35 逐格一致。
- **验收**：`bun tmp/verify-safe-base.ts` 35/35（diff=8、环外 0、可达 0.0%）；`test_bc_eval_course_env` 加载通过。滚动的 g4-mobility / g4-special 的 eval/rollout 语料变了，跨单层化前后的点不可横比（§15.5）。
- **被否决**：整格钢（S9 口袋）；`tiles26`（Python `StageSpec.grid` 必须 13×13，`stage_json` 只下发 grid；半格码已能精确表达）。
- **后果**：g4-open 以新地形重测的 `it380` 700 局为基线 B；旧地形 it380 的 84.43%/低杀 18 只作规划参照。

—— 全文（地形差异 / 生成器 / 评测影响）→ `plan/g4-opening-hybrid.plan.md` §4.5

## §2026-11-14-goalnn-shared-modules-from-god（`src/nn` 不再直接依赖 `src/ai/god`：公共件下沉）

- **背景/决定**：用户 2026-11-14 要求 `src/nn` 不要从 `src/ai/god` 取公共件。A*（`src/utils/pathfind.ts`）、基地环（`src/utils/base-ring.ts`）、`ThreatBudget`（`src/ai/threat-budget.ts`）、turn gate（`src/utils/turn-gate.ts`）、冰面滑行（`src/utils/ice-glide.ts`）、中央突破判定（`src/utils/central-breach.ts`）下沉；`src/ai/god` 旧路径保留 re-export，现有 God-AI 调用点不改。`GoalExecutor` 的 `shouldFireInDirImpl` 改走 `GodAIInput.shouldFireInDir` 公共方法（同实现，少一层直接 import）。
- **验收**：`grep "from '../ai/god" src/nn` 无命中；`bun run typecheck` / `bun run check` 绿（2809 pass）。
- **被否决**：把 `OpeningController` 放进 `src/ai` 绕过边界——问题在公共件没有公共位置，不是 controller 的位置。
- **后果**：`src/utils/pathfind.ts` 现在是 A* 唯一实现；`src/ai/god/pathfind.ts` 只是壳。新增跨层使用者应 import 共享层，不要再往 `src/ai/god` 加。
## §2026-11-14-goalnn-sampler-service-clock-in-exporter（结果容器计时前移到导出器，删掉 agent 侧全量重压）

- **现场（真实测量，非推断）**：用 `~/dev/github/battle-rollout/tmp/dist-agent/weights-rollout-0d824e10c0f30142.json`（11-14 18:57 生产权重）+ 课程 `z9-r4b` 的 `max_ticks=12000 / K=10` 跑 5 局真实 rollout + 1 局 eval。BCV2 容器 pack **87–287 KB**、frame **3.1–12.6 MB / 13 entries**；`stampServiceSec` 中位 **13.2–43.6 ms/局**（eval 仅 0.24 ms），其中 gzip 占 10–30 ms。8 workers 推算占主线程 5.4–17.8%；2s 窗口 10ms 定时器最大漂移 **32.8 ms**（空闲 0.1 ms）。原注释「~1MB / 十几 ms，相对整局可忽略」被证伪。原始读数 `tmp/sampler-perf/bench-results.txt`（一次性脚本，不入库）。
- **决定**：agent 每局把接单时刻经 `--service-started-at-ms` 透给导出器；`tools/sim/pack-container.ts::serviceElapsedSec` 成为 `manifest.elapsedSec` 单一来源（参数缺席 = 回退导出器 `t0`，本机直跑口径逐字节不变）；5 个导出器（rl / eval / goal / intent / bc）全部改用它；`serveResult` 退化为「同一个 buffer 既进缓存又对外」，不再解包重压。`stampServiceSec` 保留为回滚档与单测标的，结果出口不得再调用（源码级钉子）。`pack-container.ts` 入 `codehash-files.txt`。**gzip level 6 保持不变**（用户裁定）。
- **被否决（均有实测）**：① 异步 zlib 把压缩挪到 libuv（只搬走阻塞、不省 CPU，云机会与子进程 NN 抢核）；② 容器加明文 trailer 让 agent O(1) patch（要改 `common/distribution.unpack_container` + trainer 侧，协议面更大）；③ P1 全组——`readFileSync` 0.02–0.09 ms、mkdir+write+read+rm 0.49–0.80 ms、report 写盘 0.07–0.11 ms、`new Uint8Array` 0.035 ms、`statfsSync` 0.003 ms——全是噪声级，却要翻 codeHash + 改结构钉子 ⇒ 不做；④ persist worker stderr 未消费属健壮性，另案，不夹带进本次性能改动。
- **后果**：`elapsedSec` 口径从「agent 结果就绪」变为「子进程打包完成」，差 = readFileSync + 微任务 ≪ 0.1 s，而字段本身 `toFixed(1)` ⇒ 对 `race_metrics.effective_slots` 的 E 判据无实质影响。真机 A/B：rollout / eval 两容器除 `elapsedSec` 外 manifest 与 13 个 entries **逐字节一致**；出口 14.285 ms → **0.001 ms**。⚠ `sampler-agent.ts` + 5 导出器 + `pack-container.ts` 全在 codeHash SSOT ⇒ **触发全集群升级波**；混跑由 codeHash 门挡住（同 commit 部署是既有惯例）。
- **验收**：`tests/agent/result-stamp.test.ts` 6 例（serviceElapsedSec 纯函数、serveResult 原样返回、源码级回归钉子）；`bun run check` / `bun run build` 绿。
- **修订（同日 22:00，用户裁定「P1 剩余各项若合最佳实践，即使墙钟无改善也可改」）**：判据从「实测收益」改为「工程最佳实践 + 不破坏契约」，补做 4 项，均在 `sampler-agent.ts`：① 结果出口零拷贝——`/v1/task` 与 `/v1/result` 缓存命中改 `new Response(bodyView(cached.buf))`、同步流改 `controller.enqueue(served)`，去掉 3 处整包 `new Uint8Array(buf)` 复制；`bodyView()` 用 `buf.buffer as ArrayBuffer` 造零拷贝视图（Node `Buffer<ArrayBufferLike>` 不满足 `BodyInit`，直接传会 TS2345），真实容器实测 byte-equal 且共享底层，结构钉子同步更新；② `runGame` 生命周期同步 FS 全改 `fs.promises`（mkdir/read/rm），并用 `read + catch` 替掉 `existsSync → read` 的 TOCTOU，finally 清理吞异常以免盖掉主结果；③ persist worker 的 stderr 管道加消费者并留 4KB 尾部诊断（写满 64KB 会把 worker 永久堵在 write 上，只能等 600s 超时）；④ stdout 由「逐行 `buf.slice(nl+1)`」改成按 chunk `lastIndexOf('\n')` 一次切行，消掉 O(N²) 复制。**仍不做**：跳过 `_rl_report.json`/`_eval_report.json` 落盘（B4 哨兵 + `queue_local.py`/`iter_rollout.py`/`workdir_sweep.py` 等是真实消费方，属契约不是死写）；`statfsSync` 缓存与权重 miss 负缓存（把「当前事实」变成带 TTL 的模块状态，与磁盘告警回差同拍、磁盘回查语义冲突）。新增 `tests/agent/persist-pool.test.ts` 3 条源码级 IO 卫生钉子。

## §2026-11-15-goalnn-self-codehash-reference（rollout 节点门基准 = self 自报的 codeHash）

- **动机（用户 2026-11-15 点名）**：hub 工作区有未提交改动时 `compute_code_hash()` 算的是「谁都没在跑的那份」；节点门拿它当 expected ⇒ 全部远端节点判 `codeHash mismatch` 被排除（本地改代码 = 全集群瘫痪，只剩本机腿）。
- **决定**：rollout 派工（`trainer/dispatch._run`，rescan 共用同一参数）的参考 hash 改走 `common.distribution.reference_code_hash(cfg_nodes, probes)`：**self 节点 `/v1/ping` 自报的 codeHash 优先**（节点 id = self 或回环 URL），self 缺失/未报才回落 `compute_code_hash()`。self 的 agent 报的是实际在跑的代码，与远端同一把尺子。
- **被否决**：① 不设门（真 stale 节点会静默跑旧代码）；② 多数节点投票当基准（少数派会被判 stale，且引入跨轮投票状态）；③ 只在 `dirty_files` 时跳门（那是工作区事实，不是「self 在跑哪份」的证据）。
- **后果**：self 未重启时基准 = 旧代码 ⇒ 集群按旧代码继续跑（用户口径：宁跑不瘫）；提交+push+重启 self 后基准自动跟进。eval/batch 腿仍用工作区现算（本次未改）。`dirty_files` 仍只用于「远端 pull 不可收敛 ⇒ 抑制 restart」，语义不变。
- **验收**：`tests/trainer/test_rollout_dispatch_resilience.py` 3 条（prefers self / fallback / dirty workspace keeps cluster，`slotsOnline=2`）；`dispatch.py` LOC 仍 999；`bun run check` + `bun run pygate` 绿。

## §2026-11-15-goalnn-autonomous-cloud-overrides（课程级 `autonomous_cloud_overrides`：GPU/TPU 各一套语料采集策略）

- **背景**：同一门课程会**先后在 GPU / TPU 云机交替训练**（用户 2026-11-15 更正）——设备不同，每轮能采的局数/窗口不同；只写一套顶层 target 会让另一类设备欠采样。同时有覆盖块时 notebook `CFG.sample_mult` / `CFG.epoch_mult` **不再生效**（用户同日更正 ②），否则同一档被乘两次。
- **决定**：课程新增 `autonomous_cloud_overrides: {gpu: 档, tpu: 档}`，每档自包含 `target_transitions`（StrictInt>0）+ `rotation_mode`（even/pinned）+ `rotation_per_it`（even≥1，pinned≥0，pinned 需 `rotation_pinned`）。选档在**执行面** `remote/plan_run._sampling_profile` 按 `ctx.device` 做（cuda*→gpu，tpu/xla*→tpu）；缺档首轮前 `ProtocolError`，不回落。选中档 ⇒ 两个倍数强制 1.0 并打一行「忽略 CFG 倍数」；`worker/cloud_sampling.plan_for_profile` 用档 target + 档 rotation 重解 `volume`（按 `est_s_all` 抬 topup cap），后续 `volume_for_round` / `pairs_for` / `rotation_event` 全走档计划。块**不进** `flat_overrides` / `corpus_identity_fp`；每轮产物 `sampling_profile` 进账本。课程前置：`mode=per-tick` + 顶层 `target_transitions>0` + 顶层 `rotation_mode` 非空。
- **被否决**：① 只写 per-device `rotation_per_it` 而沿用顶层小 target（漏乘，TPU 档仍欠采样）；② 课程级 `effective_sampling` 双真源（计划与课程各一套，改一处静默失效）；③ 开课 `target % mb` 警告（`chunk_episodes` 丢尾，`mb` 可被 `ppo_schedule` 逐段改）；④ 允许 `even + per_it=0`（等于关轮换却写 even，静默）；⑤ 用 int 收 bool（`true` 会变 1 混过校验）。
- **后果**：不写块 = 行为逐字节不变；块生效时 notebook 倍数是**死旋钮**（日志留痕）。GPU/TPU 档实际 pairs 不同 ⇒ 各自独立 run/traj，不能横比；块本身不是语料身份（同 `sample_mult`）。CPU/未知设备不选档，倍数照旧。`n % mb` 尾部丢弃仍存在（本轮不修）。
- **验收**：`tests/biz/test_autonomous_cloud_overrides.py`、`tests/worker/test_cloud_sampling.py`、`tests/remote/test_plan_run_cloud_sampling.py`；`bun run pygate` 绿。

—— 全文（课程形状 / 执行面 / 风险）→ `docs/nn/training-stack.md` §76

—— 全文（现场 / 风险）→ `nn-training/common/distribution.py::reference_code_hash` 与 `trainer/dispatch.py` 注释

## §2026-11-15-goalnn-conv-wasm-clang20-retest（clang20 下档位/ReLU 复测：现役组合即局部最优，不动）

- **现场（用户 2026-11-15 点名复跑 `conv-wasm-clang20-retry.md`）**：本机 clang 20.1.0 按 `wasmBuildFlags()` 重编 8px 得到**与现役 `conv.wasm` 逐字节相同**的产物（`6e506781…` / 7350 B）⇒ 测量对象就是生产内核。三问全部为「否」：① clang20 下 **16px 比 8px 慢 5%**（clang22 上 8→16 是断崖提速，方向相反）；② **无分支 ReLU（fabs/mulbool/ltzero）都不提速**（clang20 的 `x > 0 ? x : 0` 本来就出 `f32x4.pmax` 16 处、`replace_lane` 0 处，改写只增 mul/add）；③ **`-mrelaxed-simd` 产出字节相同的 wasm**（clang22 上 4.75 ms 的收益来自绕开 gt/select 退化，clang20 无此退化可绕）。档位补扫 4/12/20/24/32 分别 2.59×/1.117×/1.116×/1.112×/1.123×，8px 是双侧局部最优。native 那一臂同样：8/12/20/24/32 px 分别 1.053/1.151/2.352/1.085/1.235×，16px 最优。
- **决定**：**保持现役，不改 `src/nn/conv/*`、不重编 prebuilt**（判定标准「内核 >5% 且端到端同向」无变体满足）。同时确认生产实跑的是 **native**（prebuilt + `NN_NATIVE_LIB`），wasm 只是回落臂；本机 native 比 wasm 快 **1.45~1.52× 端到端**（rollout 1511~1522 vs 2181~2315 ms；eval 1466~1510 vs 2208~2228 ms），内核层 1.63×（1.65 vs 2.73 ms/forward），且两次 rollout 的 13 个 `.npy` **逐字节相同**、manifest 只差 `feat` 字段 ⇒ 后端选择不影响轨迹与语料。
- **被否决**：① 统一到 16px（clang20 上更慢，且 clang20 的 8px 已无退化）；② 预先改成 fabs 以防「未来后端退化」（慢 2.4%，退化应由代码生成门禁抓）；③ `-mrelaxed-simd`（零字节差异、改 NaN 语义）；④ FMA（本机实测 native px16 `-mfma -ffp-contract=fast` 只 **+8.4%** 内核 / 端到端 −7.5%，远低于 macOS 文档的 +23%；且会被运行期 attestation 整条关掉——见同条「FMA 补测」）。
- **FMA 补测（用户 2026-11-15 二次点名「训练侧全 native、wasm 只留浏览器」）**：本机只加收缩 flags、其余按生产配方，native px16 `-mfma -ffp-contract=fast` = **0.9228× 时间（+8.4% 吞吐，92/100 轮）**，`vmulps 151→35`/`vaddps 158→42`/`vfmadd 0→166`；`-ffp-contract=fast` 单开（无 `-mfma`）产出**字节相同**的 DLL（AVX1 无 FMA 指令）；`fma8` 1.0203×、`fma24` 1.1532×、`avx2+fma` 0.9206×（无额外收益）⇒ px16 仍最优。数值上 `bufA` 66.7% 元素差 1e-7 量级；**端到端同局轨迹不变**（s16/seed860001 + 860002–860013 共 13/13 局动作、分数、obs/mask 全同，仅 `lp_*`/`value` 末位不同）——即 FMA 只改 PPO 梯度数值，不改采样轨迹。但仍然不落地：① 运行期 attestation 实测打印「attestation 失败 → 回落 wasm」（要么改成按目标入库 golden 自证，要么白丢 native 1.63×）；② arm64 换 `-ffp-contract=fast` 会自动融合（`fmla 0→60`），x64↔arm64 是否逐位相同本机不可验；③ `native-parity` 门会红 + x64 基线抬到 Haswell（2013+）+ `data_fp` 变更新纪元。
- **更新（2026-11-16）：本节「不落地」已被推翻** —— 用户口径是「所有训练提速都是有价值的」。三道坎逐条解除（attestation 改容差、parity 门改容差、`data_fp` 实为 `(dir,wver,stage,seed)` 的 sha 不含字节故不漂），FMA 已落地，见 **§2026-11-16-goalnn-conv-fma**。
- **口径教训（可复用）**：共享桌面（Chrome + 训练 worker 常驻，总 CPU 20~25%）上，「每变体独立进程取中位」**不可用**——A/A 对照（四个相同二进制）都能差 7~16%。改成**成对交替比值**（每轮紧邻两臂、奇偶轮换先后、取比值中位）后偏置降到 **0.4%**（自编 native px16 vs 现役 DLL = 1.0039×）。以后在本机做内核 A/B 一律用这个口径，别用 `tools/perf/conv-ab.ts`（`IN_CH=18` 已过时，见交接文档 §3.2）。
- **后果**：`src/nn/` 不变 ⇒ 无 codeHash 升级波。剩下的墙钟杠杆不在内核：每局固定开销 ~215 ms / 1.5 s（14%，一局一进程，`export-rl-rollout.ts` 头部禁止在该文件加池），以及**当前迭代墙钟主要卡在 PPO worker 缺位**（`job … pending 已等 370s；hub 在线 worker = 0`，而 `pure_collect_sec=12.6`）—— 抠内核 3% 远不如补训练 worker。
- **验收**：合成内核 10 个变体 × 3 组输入哈希全对（`4870538e41f3`/`94fe41d85c0a`/`68cd276a2c3c`）；native↔wasm 端到端 shard 逐字节相同；`--check-prebuilt` 保持 fresh。完整数据 → `conv-wasm-clang20-retry.md` §10（harness 在 `tmp/retry/`，gitignored）。

## §2026-11-15-goalnn-opening-null-move（v6 opening `move=null`：直接采用适配器修复，不保留开关）

- **背景/决定**：v6 `OpeningController.move=null` 旧适配写成 `hold=1 + move=0(up)`，按 `SimulationPlayer` 的 turn-only 语义会在转向冷却允许时把坦克转向 up。修复放在适配器：新增 `src/nn/opening-input.ts::encodeOpeningAction()`，v6 null → **当前已提交朝向**（`prevMoveDir ?? dir ?? 'up'`）+ `hold=1`；不碰 `SimulationPlayer` 的 hold/move 正交语义，也不改 OpeningController。**用户 2026-11-15 拍板直接采用，默认即新行为；不设 `OPENING_HOLD_CURRENT` / `--opening-hold-current` / `--opening-null-turn`，旧路径从生产代码移除。**
- **证据**：探针 6 关/30 个 null tick：v6 null 转向 4→0、位移 0→0；legacy 两臂均 0。g5-safe2.it185 700 局 eval：总体 81.86%→80.71%（−1.14pp），opening 13 关 80.77%→77.69%（−3.08pp，McNemar p=0.302），非 opening 440 局逐局相同；opening clearTick −270、stuck −52、baseWallIntact +0.15、playerDamageTaken +8.8。
- **被否决**：在 `SimulationPlayer` 加「hold 忽略 move」（用户已否决非正交语义，影响所有 v6 动作）；在 OpeningController 返回当前朝向（controller 不该知道朝向/冷却）；只修 rollout 不修 eval（口径分叉）；**保留旧行为/运行时开关**（用户明确「不用留开关」，旧 `null→up+hold` 路径移除）。
- **后果**：所有 eval/rollout 默认走新行为；采用后是新 baseline，跨采用前后的 eval/训练点不可横比。A/B 负点估计不作为否决（机制正确性优先），但记录在新 baseline 里；若采用后出现回归，回滚方式是 git revert 本次改动，而不是运行时 flag。

—— 全文（映射表 / A/B / 分关读数）→ `plan/opening-null-move.plan.md` §9

## §2026-11-15-goalnn-replay-corpus-p1p2（replay 语料运输：请求级 corpus_fmt + 两段 wire + worker pre-pass）

- **背景/决定**：35 关按 7 关轮换的根因是 payload 体积。采用**请求级 `corpus_fmt=full|replay`**：replay 只产 `replay.json` + `replay_meta.json` + 非 obs 标签，obs/scalars/extra 由 worker 用纯 JS 转换器重建。full 仍逐字节旧路径；`kind=iter`/eval 强制 full。转换器由 trainer 预构建（`bun build --target=node`）随 kind=ppo payload 根下发，worker 只跑不建；世界只由 `ReplayInput` 驱动，不重跑 OpeningController、不用 `a_*` 驱动。
- **关键契约**：`replay_meta` 带 `nSamples/decisionTicksList/obsHash/tickHashes/codeSha/initTick/opening`；`decisionTicksList` **相对 replay 帧序号**（state_init 的绝对游戏时钟会错位）；`payload_slim` 在 replay 时 DROP obs/scalars/extra 且格式不符响亮拒收；`corpus_fmt` + `replay_converter_sha256` 进 job 幂等键（full 省略分量，旧 job_id 不变）；worker pre-pass 为 shard 级事务（`<shard>.conv-tmp` 硬链接输入 → 转换+校验+完成标记 → 目录换入；失败原目录不动）。
- **本机证据（2026-11-15）**：1015 局（35 关 × 29 seed，hard，1200 tick）= 121,702 决策 / 1,216,991 tick；Bun 进程内、Node bundle、WSL Linux Node 三端 obs/scalars/extra **0 mismatch**。Node 转换 12.7 µs/tick；WSL（/mnt/d 9p）57 µs/tick。录制开销：60 局 full 14,454 ms vs replay 14,601 ms（+1.02%）；recordFrame+hash 1.2 ms/局。60 样本 shard 体积：full_raw 10,976 B / full_diet 9,200 B / replay_diet 3,928 B。
- **被否决**：① replay-only（worker 重算前向/metrics 在 2 vCPU 上净亏）；② packer 事后看 `replay.json` 猜格式（请求显式声明）；③ 在 sampler 端转换（同机无 wire 收益，白烧 CPU）；④ 只做逐文件原子写（崩溃留半成品；改为 shard 级事务）；⑤ 用 `a_*` 标签驱动世界（opening 接管段 `a_*` ≠ 执行动作）。
- **后果/未完成**：默认 full，生产课尚未切 replay；同机 `replay+eager` vs `full+eager` 的真 PPO 数值对账、2 vCPU 云机写盘/重哈希墙钟、hub 显式 capability 仍未验。完整方案 → `plan/replay-corpus.plan.md`；代码 → `tools/sim/replay-to-shard.ts`、`nn-training/remote/replay_corpus.py`、`tools/agent/sampler-agent.ts`。

## §2026-11-16-goalnn-ppo-lazy-loader（PPO lazy mmap + 逐批上卡：`--ppo-loader` 独立开关）

- **背景/决定**：eager 的 `load_episodes → chunk_episodes` 先 `np.concatenate` 全量再 fancy-index ⇒ 峰值 ≈3× obs（491k = 5.65 GB obs ⇒ 11–17 GB），且 `tensored_chunks` 把全部 chunk 常驻 VRAM。新增 `--ppo-loader=eager|lazy`（默认 eager，逐字节旧路径）；lazy = obs 逐 shard `mmap_mode="r"` + 其余字段（≈200 MB）常驻 + 全局 `np.random.permutation`（与 eager 同一条 RNG 流）+ 显式 `row_map`（`row_shard`/`row_local`）+ 每 minibatch 只 fancy-index mb 行、`tensored_batch` 逐批 `.to(device)`；kickstart ref 改为 chunk 首次使用时现算（ref 冻结，数值逐位相同）。分派唯一入口 = `worker/ppo/lazy_corpus.load_ppo_corpus`；`ppo_loader` 走课程 CLI → `build_ppo_job`（eager **省略** manifest 键）→ `train_core`；Paddle 后端响亮拒收 lazy。
- **对账契约**：shard 顺序/行序 = eager 的 `discover_rl_shards` + concatenate 顺序；quota 截断在 GAE 之前；adv/ret 归一含被丢尾部；`drop_last`/单 shard 不裁尾/全局置换顺序与 eager 相同；demo bank 的 np RNG 逐步同序；逐批转换不引入 `channels_last`（eager 当前也未传 `memory_format`，两路同源，代码赢）。
- **本机证据（2026-11-16）**：`tests/worker/test_ppo_lazy_loader.py` 9 条——eager/lazy 同 seed 的 row_map/行序/GAE/adv・ret/minibatch/demo RNG/kickstart/最终权重逐字节一致；`per_stage_quota` 截断在 GAE 前；drop_last 与单 shard；v6 可选列；混装拒收；lazy 路径不调用 `tensored_chunks`（逐批 8 行上卡）；491k/mb=1024 峰值公式 ≈1.0 GiB ≤2 GiB；合成语料 gather 冷/热 ≈693/654 MB/s。
- **被否决**：① ~~默认直接切 lazy~~（**2026-11-17 撤销**：装载段实测 + 缺省已切，见 §2026-11-17-goalnn-ppo-lazy-default）；② 让 lazy 依赖 `np_core.load_episodes` 的返回类型（会造成 worker 层成环）；③ 把 lazy 塞进 `np_core`（LOC 预算 + numpy-only 核心职责）；④ Paddle 侧静默回退 eager（manifest 与日志各说各话）。
- **后果/未完成**：生产默认仍 eager；35×20 真语料的 scan+GAE/H2D/update 端到端墙钟、4–6 GB VRAM 真机、冷/热 page cache 仍未验。完整方案 → `plan/replay-corpus.plan.md` §5/P3；代码 → `nn-training/worker/ppo/lazy_corpus.py`、`worker/ppo/engine.py`、`remote/train_core.py`。

## §2026-11-16-goalnn-conv-fma（native features 内核乘加融合：x64 `-mfma` + 全目标 `-ffp-contract=fast`，逐位门 → 容差门）

- **背景/决定**：用户口径「所有训练提速都是有价值的，FMA 能上尽量上」。native 内核（`src/nn/conv/conv.c`）原先 `-ffp-contract=off` 的唯一理由是 native↔wasm 逐位一致，代价 = 丢掉乘加融合。落地：`nativeFlags()` 全目标 `-ffp-contract=fast`；x64 三个目标 vector 加 `-mfma`（arm64 的 FMA 属基础 ISA，`fast` 一条就够：实测 `fmla 0→60`）；`-fno-fast-math` 保持不变（重结合/近似除法仍禁）；wasm 仍 `-ffp-contract=off`（指令集无 FMA，产物字节不随编译器版本漂）。`NATIVE_ABI` 不升（签名/权重布局没变）。
- **闸门口径变更（本条真正的代价）**：native 与 wasm 从「逐字节相同」降级为「1e-6 量级相同」，于是三个逐位门改成容差门：① 运行期首用 attestation（`ATTEST_TOLERANCE = 1e-5 × max(1, max|wasm 张量|)`，见 `conv_native_adapter.ts`）；② `tests/native-parity.test.ts`（共享库 + 参考 CLI，容差比较器独立重实现）；③ `tests/native-prebuilt.test.ts` 的 WSL 真机 dlopen linux-x64 产物。容差选型有实测依据：`bufA` 元素跨 1e-3…152 五个数量级，逐元素相对误差在近零元素上放大到 3%（无判别力），绝对误差则随量级缩放（实测 6e-5 @ max 152 ⇒ 4e-7 相对）；分母取张量 max 后，「错内核」的相对差是 1（poison 库用例仍是 3.16>3.16e-5）⇒ 两侧各留 ~10^2 / 10^5 倍余量。**同节点同输入的重跑仍逐字节确定**：语料复现性靠这个，不靠跨后端逐位。
- **本机证据（2026-11-16，clang 20.1.0）**：内核 ffi 微基准成对 100 轮 = **0.9228× 时间（+8.4% 吞吐）**，`vmulps 151→35 / vaddps 158→42 / vfmadd 0→166`；端到端 A/B（s16/seed860001，同权重交替 5 轮）1448 → 1340 ms（**−7.5%**）。**生产路径（入库 prebuilt + 真 attestation，零旁路）13 个种子 860001–860013**（weights `ac039c6f`，hard，12000 tick 上限）对 `NN_NATIVE=0` wasm 臂：`a_move/a_fire/a_hold/obs/mask/scalars` **13/13 局全 0 元素不同**，outcome/score/ticks 相同；native/wasm 单局墙钟比 0.59–0.67×。prebuilt：win32-x64 16384→15360 B（`vfmadd` 50 条）、linux-arm64/darwin-arm64 `fmla 60`、**wasm 仍 `6e506781…` 字节未变**；WSL 真机 linux-x64 产物最大差 1.43e-6；`--check-prebuilt` fresh，`bun run check` 2963 pass / 7 skip / 0 fail，`bun run build` 绿。
- **顺带修的缺口**：`nativeStaleReason()` 此前只比源码 sha + 产物 sha，**不比 flags** —— 只改编译参数的改动（正是本条）会让 `bun tools/agent/native-build.ts` 直接 reuse 旧库（实测踩中：先 reuse 了非 FMA 的旧 DLL）。现在 flags 不一致即判「盘上 flags 已变」，与文件头「指纹校验：源码/flags/产物 sha 全对才算新鲜」的原意对齐。
- **代价/风险（写明）**：① x64 CPU 基线从 Sandy Bridge(2011) 抬到 **Haswell(2013)**：更老的 CPU 首次调用即 SIGILL，进程级 catch 拦不住 ⇒ 节点保命开关 = `NN_NATIVE=0`（回 wasm），已写进 adapter 头注 ①b；② arm64 的融合是 clang 自动收缩（不是显式 `-mfma`），本机无 arm64 ⇒ 真值证据只能来自节点自己的容差 attestation（产物里 `fmla 60` 已核实）；③ 历史 shard 的 `lp_*`/`value` 末位与今后不同——`data_fp` 只对 `(name, wver, stage, seed)` 求 sha（`common/manifest.py::data_fp_entries`）故不判异血缘，但逐字节 diff 会看到 1e-7 差异。
- **被否决**：① 用「per-target golden 哈希」替代容差 attestation（arm64/darwin 本机跑不了，golden 只能覆盖本机目标 ⇒ 退化成「只有一台机器有真证据」）；② `-mavx2` / `-march=native`（基线代价 >> 收益，AVX2 只多 ~2%）；③ 在 `conv.c` 里 CPUID + `__attribute__((target))` 多版本分派（freestanding 下要手写 CPUID，为 8% 做内核手术且没有老 CPU 可验）；④ 只给 x64 开 FMA、arm64 保持逐位（语料按架构分叉，而 arm64 只要一条 flag）。
- **后果/未完成**：`docs/nn.progress.md` 未决 #10（arm64 要不要为 FMA 开新纪元）就此结案——选「开」，但走容差闸门而非重做基线（任务侧/奖励/关卡不动，13/13 局动作与分数不变）；arm64 真机吞吐（§14/§15 估 FMA 上限 +32%）仍未测 → **2026-11-16 已补测：a95 真机为 −30~35%（§2026-11-16-goalnn-conv-variant）**；`tools/det-golden.v1.sha256`（God-AI 确定性）不受影响，未动。

## §2026-11-16-goalnn-conv-variant（x64 发两份内核产物：基线 + `-fma`，运行时子进程试跑选；arm64 真机验 FMA）

- **背景**：FMA 落地（§2026-11-16-goalnn-conv-fma）把 x64 基线抬到 Haswell(2013)，更老的 CPU 首用即 SIGILL。用户 2026-11-16 提问「能不能保留两个 prebuilt，不支持 FMA 的用无 FMA 的那份」，并指出 a95（Android arm64）节点已 USB 连着本机可做真机验证。
- **决定（x64 双产物）**：矩阵从 6 个 native 目标变 **9 个**：`<platform>-x64`（`-mavx -msse4.2`，Sandy Bridge 2011+）与 `<platform>-x64-fma`（`+ -mfma`，Haswell 2013+）各一条，**独立目录 + 独立 manifest 条目**（不覆盖、不混装）。arm64 仍只一条（FMA 属基础 ISA，`-ffp-contract=fast` 自动融合）。
- **决定（怎么选）**：新 `src/nn/conv/native-variant.ts` —— **在子进程里 `dlopen` 变体产物 + 调一次 `cf_student_features`，退出码 0 才算本机可用**；结果落盘 `tmp/nn-native-variant.json`（键 = 平台/架构 + 探针版本 + 产物 sha），一台机器只付一次 ~40–120 ms。不解析 `/proc/cpuinfo` / `sysctl` / 注册表：三套平台三套解析会漂，且字符串说有 `fma` 不代表 OS 真让你用。探针不过 ⇒ 变体连候选路径都不进（`nativeLibCandidates()` 里直接没有它）；本机构建（`hostTarget`）固定基线 flags；`rollout-runner` 按 `targetId` 把库镜进 bundle 同名目录（变体不能混装）；`NativeStatus.variant` 让「这局到底用了哪份」可查。
- **证据（本机，win32-x64）**：默认解析 `win32-x64-fma`（日志 `variant=fma`）；`NN_NATIVE_LIB=<基线>` 走基线且 attest 通过 ⇒ 两条都在生产路径被实测。生产 A/B（s16/seed860001，同权重，6 对交替）fma 2180/2200/2375/2392/2445/2518 ms vs 基线 2556/2690/2720/2728/2793/2881 ms（成对中位比 ≈0.86×）；两臂 outcome/score/ticks 完全相同（只有 `lp_*`/`value` 末位差）。WSL 真机：`linux-x64` 基线与 wasm **逐字节 0 差**、`linux-x64-fma` 最大差 1.43e-6。
- **证据（a95 / arm64 真机，2026-11-16）**：**两臂都取代码库的产物，设备上零编译**（用户点名「不要现编，用代码库里的老版本」）——参考臂 = `git show HEAD:src/nn/conv/prebuilt/linux-arm64/conv_native.so`（clang 20、`-ffp-contract=off`、`fmla 0`），被测臂 = 现役同名产物（同 clang 20、`fast`、`fmla 60`），harness = 设备自带 python3 + ctypes。结果：加载与调用成功（bionic 上真能 dlopen 入库产物，此前只有设计声明）；真实权重 × 3 组随机输入 worst `bufA` 9.155e-5 @ 张量 max 198.7（相对 4.6e-7，容差线 1.99e-3 ⇒ 余量 21×）、`pooled` 4.77e-7 ⇒ **ATTEST=PASS**；**内核吞吐 6.42–6.70 ms → 4.36–4.49 ms（−30~35%，3 次重跑 0.697/0.651/0.683×）**，远高于 x64 的 +8.4%。两臂只差 `-ffp-contract`，没有编译器版本这类额外变量。
- **被否决**：① 解析 CPU 特性字符串（平台各一套、虚拟化会骗人）；② 在本进程里试跑变体（SIGILL 拦不住 = 节点整局挂）；③ 只留一条产物（要么抬基线、要么白丢 8%）；④ `-march=native` / `conv.c` 内 CPUID 多版本分派（freestanding 手写 CPUID，为 8% 做内核手术）；⑤ 给 arm64 也加变体（没有「不支持 FMA 的 arm64」可兼容）。
- **后果**：prebuilt 从 6 目标变 9 目标（每平台多 ~15–17 KB），`codeHash` 变更 ⇒ 节点需 pull；探针结论绑定产物 sha，重编 prebuilt 会自动重探；探针/缓存写不进去只影响速度（每次进程多一次 spawn），不影响正确性。全文 → `docs/nn/runtime-opt.md` §45。

## §2026-11-16-goalnn-replay-rebuild-perf（replay→语料重建：编码器/哈希/落盘五处热点，产出逐字节不变）

- **背景/决定**：用户要求对「replay 重建训练语料」全流程做 profile 并极限提速。Bun CPU profile（100 µs 采样 × 153k tick）+ 分段实测（冷热分开）后，重建的稳态成本 = 仿真 tick ~49% · ObsEncoder ~20% · obs sha256 ~6.5% · npy 落盘 ~9% · tickHash ~5% · parse/setup ~3%。改动五处，全部是**纯派生缓存的等价替换**（不改玩法、不改 obs 语义、不改任何文件字节）：① `ObsEncoder.encodeDistEnemy`：可通行表（按 `TileMap.revision` 失效）+ 四邻索引表（越界烘进表）+ 访问戳取代 `dist.fill(-1)` + 归一化 LUT，取代每决策 2 704 次 `% / BOARD` 与 676 次 `Math.round(255·d/dRef)`；② `ObsEncoder.encode`：ch0–ch5（地形 + 基地）整块缓存（同 revision 命中时一次 `set` 取代 676×switch 重扫）；③ `worldTickHash`：地形文本 memo（`WeakMap<TileMap>`，revision 失效）+ `CanonBuilder` 改**增量 FNV**（逐字段推进同一状态，取代 `parts[] + join('|') + fnv1a` 的整串构造与 rope 拍平）；④ `npy.ts` 新增 `writeNpyFile`（头 + 数据两段 `write`，不 `Buffer.concat` 拷整块）；⑤ `replay-to-shard.ts`：obsHash 改**一次** `sha256(obsFlat)`（规格本就是 `sha256(concat(obs_i))`，省 N 次原生 `update`）、决策 tick 用单调游标代替 `Set.has`、三张张量直写。
- **证据（本机 Ryzen 7 5800H / Bun 1.4.4，4 个 replay shard = 19 169 tick / 1 919 样本，min-of-N）**：`ObsEncoder.encode` 23.64 → **9.39 µs/op**；`worldTickHash` 34.36 → **11.54 µs/op**；重建（verify-only）10.57 → **6.22 µs/tick（−41%）**；含落盘 10.91 → **7.32 µs/tick（−33%）**。**identity 零容差**：与 `git worktree add --detach HEAD` 的原始树对同一批 shard 各跑一遍，obs/scalars/extra **12/12 文件 sha256 完全相同**；`tickHash` 链与 `obsHash` 均对**录像里记录的值**校验通过（哈希改动被录像钉住，不是自证）。`bun run freeze:check` OK（God-AI 确定性签名 `ea915876…` 不变）、`bun run check` 2969 pass / 7 skip / 0 fail、lint 0 error。
- **被否决**：① 只做端到端 ablation 归因（每段重跑同一循环，后跑的段白吃 JIT ⇒ 出现「encode 段比 tick 段还短」的伪指标；改用静止世界微基准 A/B）；② 把地形 memo 放 `TileMap` 字段（污染快照/深比较；encoder 实例字段 + revision 键是最小面）；③ 跳过 `consumeEvents()`（省不了多少，且改变事件缓冲语义）；④ 让 replay 记录敌方 AI 意图以跳掉 AI 重算（要改 replay 格式 + RNG 流，属新纪元且作废旧语料）；⑤ 非 sha256 的快速哈希（identity 规格写死 sha256）。
- **后果/未完成**：剩下的墙钟就是仿真本身（~49%，其中敌方 AI ≈26%），本次不动玩法代码。`plan/replay-corpus.plan.md` §1.4/§6.1 的「21.4 µs/tick / 254 µs/样本」是**云机旧口径**，按本机比例应再低 ~30–40%，云机复测待办。代码 → `src/nn/obs-encoder.ts`、`src/replay/tickHash.ts`、`src/nn/npy.ts`、`tools/sim/replay-to-shard.ts`；基准 → `tools/sim/replay-to-shard.bench.ts`（`--gen / --ablate / --micro / --rounds [--write]`）。

## §2026-11-17-sim-tickhash-id-space（replay→shard tickHash 偶发偏离：跨进程实体 id 复用，restore 后预留 id 空间）

- **背景**：`plan/tickhash-flake.plan.md` 记录 700-shard 写盘池在 7/10 worker 下约 1/700 出现 `tickHash mismatch at checkpoint tNNNN`（shard 随机、中局窗口），同 shard 隔离跑/同进程重复跑/verify-only 池全绿；当时头号嫌疑是 `Math.pow`/`Math.hypot` 的 JIT 分层差异。
- **复现（3/3 确定性）**：池驱动落 JSONL（worker/seq/shard/ok）后，把失败 worker 的前缀在**单进程**按同序重放：**fresh 进程 5/5 失败**，同进程第 2/3 遍通过；失败前先 `advance-ids 1`（多吃一个 id）即 **5/5 通过**；`BUN_JSC_useJIT=0` 仍 5/5 失败 ⇒ 与 JIT/并发/写盘无关，是进程内 id 空间状态。
- **根因**：`restoreWorld` 装入的快照实体 id 来自**另一个进程**（导出/银行），而本地 `genId()` 是模块级计数器、从 1 起且 restore 不推进。worker 先跑过若干 shard 后本地计数器爬到快照 id 区间，新生成实体（子弹/敌人/道具/地雷）就**重发**快照里仍活着的 id。两个活实体同 id 时：`ownerId === tank.id`、guard-AI 的 id 键、`activeCommanderId` 会认错实体；`worldTickHash` 的**首现重映射**把两个实体折进同一个 canonical slot，hash 随分配历史变化 ⇒ 同一 replay 在不同 worker 前缀下算出不同链。
- **直接证据**：失败/通过两遍在 t2368 首次分叉；该 tick 的完整 `WorldSnapshot` diff **只有 9 个 id/ownerId 字段不同**；对两份快照各自 `worldTickHash` 得到 `69844575` vs `0d9f195c`，拆 canonical payload 后首个差异正是重映射撞号（`1 vs 7`）。
- **修法**：`src/game/ids.ts` 成为 `genId` 唯一来源并新增 `reserveNextId(maxId)`；`WorldSerializer.restoreWorld` 开头 `reserveSnapshotIds(snap)` 扫描 player/player2/tanks/allies/bullets（含 `ownerId`）/powerUps/mines/`activeCommanderId`，把全局计数器单调抬到快照最大 id 之上。`World.ts` 保留 `export { genId }` 以免动既有 import 面，也不引入 World↔WorldSerializer 运行时环。
- **验证**：修复后同一 worker 前缀 fresh 进程 **5/5 通过**；**7 worker 写盘池 700 shard × 5 轮 = 3500/3500**，每轮 `samples=503963 ticks=5035728` 与顺序口径逐项相等（wall 10.7/12.8/25.0/12.3/115.8 s，末轮为盘噪声，写盘口径取 best-of-N）；7 worker verify-only 700/700。新增 `tests/snapshot-id-space.test.ts` 3 例：**关掉 reserve 时 3/3 红、开启后 3/3 绿**（单次 foreign restore 预留、worker 前缀连续 restore 不撞、bijective 重编号下 tickHash 不变）。
- **被否决**：① H1 跨 shard 模块级 cache（失败前缀可复现，但 `advance-ids 1` 即消失 ⇒ 不是 cache 内容）；② H2 写盘扰动（verify-only 也走同一 restore，且 no-JIT 仍失败）；③ H4 哈希 memo/增量实现（同快照重复算稳定，且两遍唯一差异是 id 字段）；④ H5 记录侧 tickHash 错（隔离重放能通过，录像链本身自洽）；⑤ H6 JIT/硬件 bit flip（no-JIT 5/5 复现、失败点确定性）；⑥ 失败后重试（掩盖问题，违反零重试纪律）。
- **代价/风险**：不改玩法语义、不改 replay 格式、不改 tickHash 规格；restore 多一次 O(entities) 扫描（每 shard 一次，可忽略）。`genId` 仍是进程级隐藏计数器（原豁免不变）——现在它只会单调增长，跨进程快照的 id 空间在 restore 时被显式预留。旧 replay/快照无需迁移（id 只要求唯一，不要求从 1 连续）。
- **后果**：`plan/replay-corpus.plan.md` §1.4 的「写盘池 ≤3」解除，生产写盘池可回到全核宽度；`plan/tickhash-flake.plan.md` 收尾。转换器保留 env-gated 诊断装置（`CONV_TRACE` 每 tick hash、`CONV_SNAP_*` 窗口快照），默认零开销。

## §2026-11-17-goalnn-ppo-lazy-default（P3 收尾：PPO 装载器缺省切 lazy）

- **背景**：`plan/replay-corpus.plan.md` P3/D6 的懒装载器与等价测试 2026-11-16 已落地，但缺省仍是 `eager`（当时的否决理由①=未做真机 35×20 墙钟/VRAM 验证）。用户 2026-11-17 指令：按 plan P3 落地 lazy loader、压训练轮墙钟。
- **决定**：三处缺省同值切 `lazy` —— `worker/cli.py --ppo-loader`（`_d("ppo_loader","lazy")`；生产真源 = rl-config `rl.ppo_loader`，本机该键未写）、`worker/ppo/engine.py` 独立 CLI、`biz/course_spec.py::CourseConfig.ppo_loader`（文档值，「显式才覆盖」）。**协议缺省不动**：`common/manifest.py` 与 `remote/train_core.py` 仍 `"eager"`，发布端只在 `!= "eager"` 时写键 ⇒ 旧 manifest 逐字节旧路径、lazy 永远显式声明；回滚 = `--ppo-loader eager` / `rl.ppo_loader:"eager"`（课程里的显式值优先）。Paddle 后端仍**响亮拒收** lazy（实验臂必须显式 eager，不静默退回）。
- **证据（本机 2026-11-17，`tools/perf/ppo-loader-bench.py`：40 shard × 502 = 20,080 样本 / obs 220 MB / mb=1024）**：scan+GAE eager **2.14 s** vs lazy **0.20 s**；随机 gather 90 MB **0.09 s（1,002 MB/s）**；每 mb 转 torch（CPU）**6 ms / 8 批**；`gather(rows)` 与 eager `concatenate()[rows]` **逐字节相同**。等价测试 10 例绿（chunk / 最终权重逐字节 + 新增「缺省必须是 lazy」钉子）。
- **仍未被本机证明**：35×20 真语料的 GPU H2D / update 墙钟与显存余量（本机无 GPU；RSS 探针缺 psutil ⇒ 留空不编数）——判据在 plan §6.6 五档 verify（`verify-fl` vs `verify-fe` 即装载器 A/B），回滚一个 flag。
- **否决**：① 继续等 GPU 真机再切（装载段已有量级差，且 lazy 峰值与语料总量无关，等下去只是继续付 ~3× obs 峰值）；② 只切课程缺省、不动 CLI（rl-config 缺键时仍 eager ⇒ 生产不生效，账实不符）；③ 连协议缺省一起切（旧 manifest 会从 eager 变 lazy，等于**静默**改变历史包语义）；④ paddle 静默退回 eager（manifest 与日志各说各话，2026-11-16 已否决，维持）。
- 全文 → `docs/nn/remote-transport.md` §156；代码 `nn-training/worker/{cli,ppo/engine}.py` · `biz/course_spec.py` · `tools/perf/ppo-loader-bench.py` · `tests/worker/test_ppo_lazy_loader.py`。

## §2026-11-17-goalnn-replay-f16-symmetric（取消 f16 单边偏置 + `corpus_fmt` 翻成生产缺省 replay）

- **背景**（2026-11-17 补记，决定当日已实施于 `c170ba26`）：五臂真机 A/B（`reports/replay-corpus-five-arm-20261117.md`）里 `fe`/`fe2`/`fl` 三臂逐字节相同，而 `re`/`l`（replay 语料）与它们的 it1 语料**同源**（samples 65026=65026、30 shard、kept 24577、rotation/volume_stage_stats 逐字相同）却在 it2 就分叉（终态权重 max|Δ| 9.5e-3、changed 18940/73668）。
- **根因**：`remote/payload_slim._member` 里 `REPLAY_DROP_FILES` 排在 f16 **之前**，而 `scalars`/`extra` 同时出现在 `RL_F16_FIELDS` 与 `REPLAY_DROP_FILES` 里 ⇒ **full 语料这两张被量化成 f16，replay 语料由转换器以 f4 重造**。同一份 rollout 的两种表示精度不等价 —— 这不是「误差多大」的问题，是「两臂必须被同等对待」的问题。plan §7 没抓到是因为那次对账是**离线比两份 npy**，绕过了 `payload_slim`（f16 只在上线传输时发生）。
- **决定**：① `common/shard.py:139` 的 `RL_F16_FIELDS` 收窄成 `("lp_move","lp_fire")`（`scalars`/`extra` 移出）。`lp_*` 两臂都不过 DROP ⇒ f16 对称，保留；代价实测（xz 后）payload **+474 KB / +10.5%**（4.50 → 4.98 MB），下载 +0.3 s（obs 才是压缩大头，「翻倍」的直觉估法不成立）。② 默认值翻成 `replay`：`worker/cli.py --corpus-fmt`、`rl-config.json` 的 `rl.corpus_fmt`、`biz/course_spec.CourseSpec.corpus_fmt`（后者是文档值，`model_fields_set` 决定显式才生效）。③ **默认不是全局**：`trainer/loop_round_steps._resolve_corpus_fmt`（:363）按「rollout 与 PPO 是否同一台机器」裁决，`rollout_src=node`（kind=iter）与 `--export-bundle`（kind=run）**强制回 full** 并留一行日志（显式请求被降级必须可解释）；`worker/cmd.build_rollout_cmd(node_side=True)` 是同一规则的第二道闸。该函数可重入（每轮都调）⇒ 从 `args._corpus_fmt_req` 的**请求**起算，不从上一轮结果起算，否则一次降级会把整门课永久钉在 full。
- **护栏**：`tests/remote/test_payload_slim.py::test_f16_set_never_intersects_replay_drop_set`（:239）断言两表**不相交**（把根因铸成断言，而不是逐字段查精度）；`trainer/stream.py` 的本机 PPO 兜底读盘遇 replay 显式 `SystemExit`（该腿自 `plan/accident.plan.md §3` 起 `args.stream` 恒 0，正常跑不到，但绝不静默炸在「找不到 obs.npy」）。
- **迁移口径**：历史权重链的「语料精度」**一次性断代** —— 2026-11-17 之前的 full 语料是 f16 版 `scalars/extra`，之后是 f32。跨这条线的权重比较里 `changed` 是**下界**（微差被 i16 权重容器吃掉），不据此判「没学到东西」。已训练权重本身不受影响（不 bump shard schema）。
- **被否决**：① 只改转换器去对齐 f16（replay 侧没有原值，转出来的就是 f32，量化回去等于人为造差）；② 整个取消 f16（`lp_*` 两臂对称，取消只是白丢压缩）；③ 只翻默认不加「同机回 full」闸（同机腿零 wire 收益却白烧 CPU，且节点盘上不再有 obs ⇒ 同机读 shard 的腿会断）；④ 新老语料混算不声明（跨断代点的 `changed` 会被误读成「没变化」）。
- 全文 → `docs/nn/remote-transport.md` §155；代码 → `nn-training/common/shard.py` · `remote/payload_slim.py` · `trainer/loop_round_steps.py` · `worker/cmd.py`。

## §2026-11-17-goalnn-prebuilt-reproducible（prebuilt 逐字节可复现 + 过期判据改「剥注释摘要」）

- **背景**（2026-11-17 补记，实施于 `0534585e`）：起因是「改 C 注释要不要重跑 9 目标交叉编译」——原判据是**源码原始字节 sha256**，改一个注释字节就要重编全套，为注释付这个代价不值，于是 3 个 C 文件里「native↔wasm 逐位一致」的错话一直留着。查这件事时挖出更严重的一条：**「源码不变 ⇒ 二进制不变」这个前提根本不成立** —— 同一份 `conv.c` + 同一份 flags 连编两次差 **11 字节**。
- **根因**：**产物文件名被编进了产物**。临时名 `<out>.tmp-<pid>` 的 pid 进了 `.rdata`（实测能直接搜到 `conv_native.dll.tmp-20780` 这类 ASCII），PE 头 `TimeDateStamp`（偏移 `0x80` 与 `0x3c0c`）是第二个；`-Brepro` 只压得住后者。拆解实验：固定输出名逐字节相同 / 换输出名立刻不同 / 同 basename 换目录仍相同 ⇒ 唯一不确定源 = basename 里的 pid。
- **决定**：① pid 从**文件名**挪到**目录名**（旧 `<dir>/conv_native.dll.tmp-5680` → 新 `<dir>/.build-5680/conv_native.dll`），`tmpSibling`/`commitTmp`/`dropTmp` 覆盖 4 处（共享库 / 参考 CLI / wasm / prebuilt 9 目标）；② 参考 CLI 补 `reproducibleLinkArgs()`（coff `-Brepro` / macho `-no_uuid`）—— 它链系统 libc，用不了 `nativeLinkArgs` 那套 freestanding 参数，所以**从来没拿到过** `-Brepro`；③ 过期判据换成 `SOURCE_DIGEST_SCHEME='c-strip-comments+lf/v1'`（`tools/agent/native-build.ts:160`）：剥注释 + 归一 LF 后取摘要，旧 manifest 无 `digest` 时回落原始 sha（重建一次即迁移）。已证「剥注释重编 = 逐字节同产物」：改注释 `--check-prebuilt` rc=0、改一个代码字节立刻 rc=1。
- **被否决**：① 保留原始 sha 判据、每次改注释都重跑 `--cross`（数秒 + 大段 manifest diff，换来的只是「不用想」）；② 手写简单注释剥离器（正则扛不住字符串里的 `//`）；③ 把 `sources[].sha256` 写回 manifest（改注释后必然与盘上不符，且已无消费者）。
- **后果/坑**：`codeHash` 集内文件字节一变 ⇒ 节点判 stale，**跑批前先重启 hub**（`code.zip` 快照在 hub 启动时锚定）。⚠ **Windows：正被映射的 .dll 不能 rename 覆盖（EPERM）** ⇒ hub/worker/dashboard 活着时重建 prebuilt 会失败，且**只失败在它加载的那个变体**（x64 是 `fma`）—— 停掉占用方再重跑。
- 全文 → `reports/build-repro-20261117.md`；代码 → `tools/agent/native-build.ts` · `src/nn/conv/native-prebuilt.ts` · `conv.c`/`conv_cli.c`/`conv_wasm.h` 的注释。

## §2026-11-17-goalnn-replay-p2-verdict-c（P2 gate 判据改「实测噪声底 ∪ 2 ulp」，判 PASS）

- **背景**：`plan/replay-corpus.plan.md` P2 的判据原是「逐位相同、无容差」，依据是 s1 里 `fe` vs `fe2` 两轮 44/44 张量 bit-identical（把噪声底当成 0）。A2 之后 s2 复跑仍非逐位 ⇒ 先判 FAIL；s3（同 fmt 第三次独立跑）随后证明**噪声底不是 0**。
- **根因**：`src/nn/conv/conv_native_adapter.ts:347` 的 `ATTEST_TOLERANCE=1e-5` 是**相对容差**（注释自承比观测值宽 ~20×），rollout 每局在 native（FMA，一次舍入）与 wasm（两次舍入）之间**动态选**，两者差 1 ulp 也被放行 ⇒ 同配置两次独立采集**构造上就不逐位相同**。「逐位无容差」在混合后端下不可达，不是被测对象有问题。
- **决定**：判据改 C —— `容差(项) = max(实测噪声底(项), 2×ulp32(|x|))`，逐项判 `|Δ| ≤ 容差`；判决里**分开报告**「≤ 实测底」与「靠预算兜住」两种，后者单列不藏。噪声底取**两对**独立重跑逐项 max（`fe-s2↔fe-s3`、`re-s2↔re-s3`），并先过有效性闸：四臂 it1 的 `shards=30` / `data_fp=75f03448f325` 全同 ⇒ 噪声对与 A/B 对是**同一批局**。2 ulp 预算必须留 —— 有限对样本包不住下一次抽样的尾部。
- **判决**：P2 **PASS**。44 张量 **41 个逐位相同**，余 3 个各差 1–2 个元素、每个正好 **1 ulp（2.3842e-07）**；`value`/`entropy` 逐位；`policy` 1.649e-10 ≤ 底；`kl` 4.123e-11（**0.2 ulp**，超底 1.17×，靠预算兜住）；`mean_ret` 落在相邻量化档（7.761e-11 与底同值 ⇒ 离散台阶，非连续量）。**并且**：钉死后端（`NN_NATIVE=0`）后零容差直比，两路 `obs`/`scalars`/`extra` 三张 **sha256 逐字节相同**（`tmp/replay-verify/ab/`）⇒ P2 的原始命题成立，残余 2.38e-07 **100% 来自后端选择**、与 `corpus_fmt` 无关。运行时另有零容差闸常年守着：`tools/sim/replay-to-shard.ts:290-295` 的 `obsHash`/`nSamples`/`decisionTicks`/tickHash 链/终局状态五项，不符即 `throw` 不写半成品。
- **不做**：不为「逐位」去钉后端（`NN_NATIVE=0` 实测慢 **3.5×**，`elapsedSec` 1.6 → 5.7，直接吃采样吞吐）。真要清理走 `-ffp-contract=off` 重编（根治，但要重编 wasm + 6 个平台 DLL，改完 `ATTEST_TOLERANCE` 才真收窄），与下一次 `src/nn/**` 正常改动合并在一个重启窗口里做。
- **被否决**：① 维持逐位判据并「重采到逐位」（重采掩盖真差，且构造上永远采不到）；② 用 per-target golden 哈希替代容差 attestation（arm64/darwin 本机跑不了，退化成「只有一台机器有真证据」）；③ 48h 后把已知不匹配转为可接受（时间不是证据）。
- **后果**：`plan/replay-corpus.plan.md` P2 勾绿，DoD 与新增 §6.6.2 同步；`conv.ts`/`conv_native_adapter.ts`/`conv_wasm_adapter.ts`/`conv_ts.ts` 里「逐字节/逐位一致」的错话改成容差等价（`bun test tests/native-parity.test.ts` 11 pass）。
- 全文 → `reports/p2-verdict-c-20261117.md` · `docs/nn/remote-transport.md` §157。

## §2026-11-17-goalnn-replay-pilot-verdict（三臂 pilot：刷新频率有效，冻结大池最差；P5「扩量」前提被否）

- **背景**：`plan/replay-corpus.plan.md` §6.5 的三臂 pilot —— 在**等总样本（≈491,520）、等 mb、等 epochs（1,920 步）**前提下，只改「数据刷新频率 / on-policy 滞后」。
  三臂：A 7 关/it × 20 it（20 次刷新）· B 35 关/it × 4 it（4 次）· C 35 关/it × 1 it（1 次冻结）。全量报告 → `reports/replay-pilot-verdict-20261117.md`。
- **决定**：**接受「刷新频率对样本效率有可测正效应」这一方向**，并按预注册规则判 **B 臂 No-Go**（连续 3 个样本锚点 < A −2pp）。
  **同时否定 P5 原定的「10×/20× 扩量」路线**——扩量（35 关一次采满、一轮训完）正是 C 臂的做法，而 C 是三臂里最差的（C−A = −6.86pp，CI [−11.29, −2.29]）。
- **证据（kaggle Tesla T4 ×2，三臂同 runId、同起点 sha `bec039d88e2dcc9e`、同 700 局面板）**：
  it0 三臂逐字相同 **471/700 = 67.3%**；终点/峰值 A 77.7%（it15）/ 75.0%（it20）· B 72.4% · C 68.1%。
  B−A 四个锚点 = **+3.00 / −3.57 / −6.57 / −2.57pp**（唯一 CI 排除 0 的是 −6.57 [−11.00, −2.14]）；C−B（干净对照，同为 35 关）= **−4.29pp** [−9.00, +0.29]。
  **排除替代解释**：PPO 计算时间三臂 431/423/428 s、步数都是 1,920（排除算力）；kl 全部 0.0006–0.0027、entropy 0.41–0.50（排除崩塌）；样本量极差 4,096 = 0.83%，比效应小一个数量级。
- **被否决**：① 把 C 的 68.1% 当「大池不行」的定理（1 个训练 seed、1 个终点，CI 还含 0）；② 用 A 的 35.9 min 墙钟当「高频刷新不能上产」的结论（差额是**每轮固定开销**，不是 PPO 算力）；③ 继续按 P5 扩量（把池子再加大只会更像 C）。
- **后果 / 下一步**：P5 改为三件事 —— ① 补 **3 个独立训练 seed** 复跑 **A vs C 两个端点**（不是全跑）；② 压每轮固定开销（A 每轮 PPO 21 s，其余 ≈104 s 是打包/发布/等 worker/回传），这条决定高频刷新能否上产；③ 若要把它做成生产自动旋钮，需正文 §D9 的 it 内样本轴调度（`it_equiv`），本 plan 不做。
- **不做**：不据此改任务侧（关卡/命数/敌数/max_ticks）；不把 B/C 的读数写进任何「配方已证」的结论（§6.3 统计边界：CI 只覆盖 eval-seed 噪声）。

## §2026-11-17-goalnn-replay-followup-legs（四条后续腿：继续降池 +7pp 唯一超散布；A 的「平台」是抽样低点）

- **背景**：pilot 判决（§2026-11-17-goalnn-replay-pilot-verdict）留下两个问题 ——「池子还能不能再降」「A 到 491k 后是不是平台」。四条腿并行跑完（同机 T4×2、同进程、同 700 局面板）：`replay-dose2`（池 12,288 × 40 it）· `replay-a-cont`（A 的 it20 续训 20 轮）· `replay-a-lr2`（lr 2e-4）· `replay-a-s2`（同配方只换 `paired_rotate_seed`）。全文 → `reports/replay-pilot-verdict-20261117.md` 附节。
- **决定**：① **接受「继续降池」为下一步**（D2 终点 **82.0%**，vs A 75.0% = **+7.00pp** [+3.00,+11.29]，且曲线单调不回落）；② **撤回「A 是平台」的判断**（CONT 从 A 的 it20 继续训到 **81.3%**）；③ **不采纳 lr 2e-4**（+3.14pp 落在实测散布内），但记为「与 D2 组合再测」的候选；④ **新增一条判读纪律：单 run 内 <4pp 的差不作结论**。
- **证据（kaggle T4 ×2，四条腿均 `replay+lazy`、同 runId、同面板）**：
  | 腿 | 终点 | Δ vs A | 说明 |
  |---|---:|---:|---|
  | A（基线） | 75.0% | — | 曲线 67.3→77.7→**75.0**（先涨后掉） |
  | D2（池减半） | **82.0%** | **+7.00pp** [+3.00,+11.29] | 单调 67.3→74.0→77.0→79.4→82.0；**高于 g5-safe 80.94% 平台** |
  | CONT（续训） | 78.9%（it15 **81.3%**） | +3.86pp（基线不同） | 从 A 的 it20 起，+6.3pp 到峰值 |
  | LR2（lr 2e-4） | 78.1% | +3.14pp [−1.00,+7.43] | kl 0.0016–0.0051、ent 0.42–0.50，前期更快 |
  | S2（换批局） | 79.0% | +4.00pp [−0.29,+8.43] | **散布标尺**：同配方换数据即差 4pp |
  **机制**：五腿 PPO 算力 423–446 s、步数 1,920（含 D2 的 40×48）⇒ 差异不是算力。
- **被否决**：① 用 LR2 的 +3.1pp 判「高 lr 有效」（<4pp 散布，且 kl 未进危险区也不代表有效）；② 用 A 的 75.0% 当配方上限（S2/CONT 都更高）；③ 把 pilot 的 B−A −6.57pp 当铁证（同量级散布存在）。
- **顺带发现的 harness 缺陷（未修，需单独立项）**：`replay-a-cont` 声明 `iters=20` 却跑了 **21 轮**（`run_complete` 在 it20 写完后又跑 it21，同一 `run_start`），且 it20 的 eval **重复派发 469 局**（1,169 行含重复 `(stage,seed)`）。影响：多烧一轮、最后一点不再是 700 局、`run_complete.cause=iters` 语义失真。本报告已去重并用 it15 作主读数，但缺陷本身未修。
- **后果 / 下一步**：P5 改为「继续降池（6,144 × 80 it）+ D2 与 lr 的组合腿」，并在开跑前**先修 iters 边界**（否则每条腿都多烧一轮、末点读数不可信）；配方推广仍需 ≥3 个独立训练 seed（换 runId = 换 trainer 进程）。

## §2026-11-17-nntrain-iters-boundary-and-eval-drain（iters 边界超一轮 + 收官 drain 重派在飞轮）

- **背景**：`replay-a-cont` 声明 `iters=20` 却跑了 **21 轮**（`run_complete` 写两次「it20/20」「it21/20」），且 it20 的 eval 落了 **1,169 行**（700 + 469 条重复 `(stage,seed)`）。22:05 复查发现 `replay-a-lr2` 同样在跑 it21 ⇒ **系统性**，非单课偶发。
- **根因（两条独立）**：① 预算判满只在 `LoopRunner.planner`，而预采/队列可能在「判满之前」就把下一轮排进来 ⇒ 那一轮照样被执行；② 收官 drain 的「未覆盖」判据只看 `eval_summary`，而**在飞的轮还没写 summary** ⇒ 收官那一刻必然判成未覆盖再派一次。
- **决定**：① `LoopRunner._pending_steps` 加**第二道预算闸**（`budget_exhausted(it, budget)` ⇒ 空表 + `finished` + 一行日志「拦下预算外的 itN」），判据与 `planner` 同源；② 新增 `EvalArbiter.active_its(course_fp)` 与纯函数 `drop_inflight_rounds`，drain 前把在飞轮剔出候选并单独记一行。**判据的语义改成「这轮是不是已经有人在做」，而不是「账上有没有结论」**——后者在收官瞬间必然为假。
- **证据**：`test_pending_steps_refuses_over_budget_round` 回退实现即红（`_pending_steps("c", 21)` 返回 13 个 Task，实测已复现）；修复后绿，并有正向对照 `test_pending_steps_allows_the_last_budgeted_round`（it20=预算内必须放行）。`test_eval_drain_dedup.py` 5 例钉住去重语义（在飞剔除、旧缺口保留、umbrella 空集、`active_its` 默认空）。相关五文件 85 pass。
- **被否决**：① 只改 `planner`（预采/队列已能绕过它，是本次的实际漏点）；② 在 `run_step` 里拦（四态里没有「不执行这一轮」的终态，拦不成）；③ 靠事后去重（读数还能救，但多烧的一轮与 `run_complete` 双写仍然污染账本）。
- **后果**：修复需**重启 trainer** 生效（进程内加载）⇒ runId 变（`98b8d9b9…` → `375bfcf6…`），跨重启的两批课不能互相比 `job_seed`。已收官的七门课先 `stopCourse` 再重启，避免复活时被重新排队。

## §2026-11-18-goalnn-replay-batch2（第二批三腿：D2 的 82% 是高抽样，真实平台 77–79%；**语料调度这条路到头**）

- **背景**：承接 `§2026-11-17-goalnn-replay-followup-legs` 的「继续降池」决定，跑三条单变量腿：`replay-d35`（池不变、窗口 7→35 关）· `replay-dose4`（池 12,288→6,144 × 80 it）· `replay-dose2-lr2`（+lr 2e-4）。本批 runId `375bfcf6…`（修 iters 边界后重启），与第一批只按 eval 面板配对。全文 → `reports/replay-pilot-verdict-20261117.md` 附二。
- **决定**：① **撤回「D2 的 82.0% 破了 g5-safe 80.94% 平台」**——三条独立腿全部落在 77.0–77.7%，终点 vs D2 = −4.29 / −4.43 / −5.00pp 且四条 CI 不含 0；结合 S2 实测的 4pp 散布，**D2 的 82.0% 是 +4~5pp 的高抽样，真实平台 77–79%**。② **停止语料调度方向的调参**：池大小（6k/12k/24.5k/123k/491k）、每轮关数（7/35）、刷新次数（1/4/20/40/80）已全部扫过，落在同一簇。③ **新纪律：新腿只跑 25 万样本（约 10 轮 × 24.5k）就够判**——实测后半程（245k→491k）平均只值 **+0.37pp**，八条腿里四条是负的。
- **证据**：D35 77.7% · D4 77.6%（峰值 80.0% @it60）· DLR 77.0%；参考 D2 82.0% / S2 79.0% / CONT 78.9% / LR2 78.1% / A 75.0% / B 72.4% / C 68.1%。
  单变量读数：**广度**（D35）−4.3pp ⇒ 窄窗口不劣（**「多样性 vs 更新率」仍未分离**，因为 D2 本身是高抽样）；**池大小**（D4）−4.4pp ⇒ 不再降池；**lr 组合**（DLR）−5.0pp ⇒ 小池 + 高 lr 互相抵消（同一 lr 在 24.5k 池上是 +3.1pp）。
- **被否决**：① 继续按「降池」推进（D4 已证无益且更贵）；② 用 D2 的 82.0% 当配方上限写进任何结论；③ 把 6,144 的 it60 峰值 80.0% 当拐点（单点、单 seed，且终点回落到 77.6%）。
- **后果 / 下一步**：瓶颈不在样本量（25 万饱和）、不在算力（PPO 423–446 s 全同）、不在熵/kl（0.0002–0.0051）。要破 80.94% 只能动**奖励塑形 / 观测 / 架构**三类之一，**不是**语料调度。开新腿前按「25 万样本」定预算。

## §2026-11-18-goalnn-corpus-iso-score-surface（语料调度处在「等分面」上：换路径不换高度；轮换不产生遗忘）

- **背景**：用户提出核心质疑 —— replay→npy 的原始动机是「每 it 覆盖全 35 关 + seeds≥5 保多样性，避免偏置语料被过拟合并被后续迭代洗掉」，但八条腿实测显示「每 it 只 7 关轮换」「每 it 语料减半」都不影响质量。需要一个机制解释，而不是「反正没差」。
- **决定**：把结论定为 **77–79% 是一个等分面（iso-score surface）**，语料调度只改变到达路径、不改变高度。据此：① **停止在语料调度上花算力**；② 明确 replay 基础设施的价值**只在传输与可行性**（s2t 12×、35×20 可行），不在「覆盖/多样性改善质量」；③ 后续只动**奖励塑形 / 观测 / 架构**。
- **证据（四条，全部可从 `reports/replay-pilot-verdict-20261117.md` 附三复算）**：
  1. **轮换不产生遗忘**：A（7 关/轮×20）终点逐关分数与「距上次训练几轮」相关 **r=+0.09**，lag0 73% / lag4 **77%** ⇒「互相拉扯被洗掉」不成立。
  2. **同分不同策**：任意两腿在同 700 局上的通过/不通过一致率只有 **65–71%**，而「各自 ~78%、彼此独立」的基线 ≈ **66%** ⇒ 胜负几乎无关联。**连 S2（同配方只换一批局）也只有 65.1%**。
  3. **强项在换、总分不变**：逐关剖面跨腿相关 **r=0.50–0.72**，各腿最差关各不相同（2034/2030/2026/2032）。
  4. **饱和**：245,760→491,520 平均增益 **+0.37pp**（八条腿，四条为负）。
- **机制**：① 35 关是同一规则下的布局变体（20 敌/3 命/同奖励/同观测），**单模态** ⇒ 7 关已张成该分布，加覆盖率边际信息≈0；多样性只在多模态任务上值钱。② 刷新频率的收益在 ~20 次饱和，唯一悬崖在最左端（C：1 次刷新 68.1%）。
- **被否决**：① 「多试几组池大小/关数就能找到更好的点」（五个维度已扫遍，同层）；② 「先把覆盖做满再谈别的」（覆盖不是约束）；③ 「轮换导致遗忘，所以要在每轮都覆盖全关」（r=+0.09 直接否掉）。
- **后果**：新腿预算按 **25 万样本**（≈10 轮 × 24.5k）；任何「换采样」提案先默认驳回，除非能说明它改变了**学什么**而不是**看哪些局**。

## §2026-11-18-goalnn-reward-term-diagnosis（7,000 局奖励诊断：排序对、五税空转、终局定价反了）

- **背景**：语料调度被判「等分面」（`§2026-11-18-goalnn-corpus-iso-score-surface`）后，下一个候选是奖励。用户要求先用**盘上既有数据**诊断，不新跑批。
- **证据（7,000 局 = 10 条腿终点 eval，同一 700 局面板；`tmp/reward-term-analysis.py` 逐项复算，全文 `reports/reward-terms-vs-win-20261118.md`）**：
  1. **排序正确**：Φ 判别赢/输 **AUC = 0.985**；同 `(stage,seed)` 的 10,581 对「一赢一输」里只有 **1.0%** 反转；「该项在输局更划算（ΔΦ<0）」的项 **为零**。⇒ **不重排奖励**。
  2. **五项纯税空转**：`wThreat −11.46 / wEncl −5.99 / wIdle −4.25 / wStuck −4.24 / wWall −3.36` 在**赢局照收**，合计 **−29.3 分/局**（占赢局 Φ 的 8.3%），而与本局胜负的相关只有 **r = 0.02–0.14**。历史侧证：g5-safe 收课时已记「税一直在收、行为没变」，safe2 加到 4.5× 仍无变化。
  3. **终局定价反了**：`base_destroyed = −35` 比 `lives_exhausted = −45` 更轻，**只值 2.3 个击杀**；而 **8.4% 的局（412/4,900）是基地被拆**。极端案例：17 杀 0 伤 0 死但基地被拆 ⇒ Φ **+362**；20 杀通关但承伤 1,566 死 5 次 ⇒ Φ **+2**。
  4. 判别力集中在两项：`wKill`（ΔΦ +95.2，r=0.79）与 `wWin`（+80.0，r=1.00）合计 **61.6%**；**击杀的权重是通关的 3.75 倍**。
- **决定**：开两条干预腿（各 **25 万样本 ≈ 10 轮**，不跑满 491k）：**T1 砍税**（删/缩那五项，至少 `wThreat`+`wEncl`）与 **T2 修终局**（`base_destroyed` −35 → **−90**）。**不**重排奖励、**不**加新税、**不**动 `wKill`。
- **被否决**：① 重排奖励（AUC 0.985 已足够）；② 继续加税（两次历史实测无效）；③ 动 `wKill`（判别力主力，风险最高）；④ 只凭本报告下结论（相关性 ≠ 因果，T1/T2 才是干预）。
- **局限**：全部是终点策略分布；某项「无判别力」也可能是它**已经完成任务**（`wIdle`/`wWall` 大概率属这类）——那删它没有收益也没有坏处。T2 有「变龟缩」风险，用 25 万样本先看 78% 有没有动。

## §2026-11-18-goalnn-rollout-relay-rendezvous（采样节点「只出网」接入：hub 会合 relay，cloudflared 失败自动回落）

- **背景（ModelScope / 阿里云 8C32G 真机 2026-11-18）**：`rollout.ipynb` 在这台机器上**建不出任何 quick tunnel** —— `预检: api.trycloudflare.com:443 inherit=fail direct=fail ｜ 边缘7844 tcp=fail`，cloudflared 停在 `Requesting new quick Tunnel on trycloudflare.com...` 16s 后 `rc=1`，三次阶梯（http2/direct/auto）一模一样；而同一台机器 `github.com:443` 119ms / `registry.npmmirror.com:443` 20ms 通，**hub 自己的 `*.trycloudflare.com` 也通**（443 tcp ok 417ms、未带令牌时回 401）。⇒ 缺的是「hub→节点入站」这半条，不是没网，也不是脚本姿势：7844 是 Cloudflare 写死的唯一边缘端口，cloudflared 的边缘拨号**不读** `HTTP(S)_PROXY`（PR #1514 仍未合入），换协议/换 edge IP/填 `CFG.net_proxy` 都到不了。
- **决定**：新增 `link=relay` —— 节点**出站长轮询** hub，hub 把它「本该发给节点 URL 的请求」放进队列，节点在本机 `127.0.0.1:<agent_port>` 执行后交回执。`auto` 下它是 **cloudflared/tailscale 都建不起来时的最后一档**（`CFG.link=cloudflared` 显式强压时不回落）。
  - 节点按 **hub 告知的本机端口**注册 `http://127.0.0.1:<hub 端口>/relay/<node>`（`_poll` 应答带 `local_port`；trainer 与 hub 同机）⇒ `common.distribution` / trainer / eval / dashboard 的调用面**零改动**。
  - 鉴权：节点侧 `_poll`/`_reply` 用 `HUB_TOKEN`（与其余端点同一把锁）；调用侧**只接受本机回环且 `via=="peer"`**（cloudflared 回源对端也是 127.0.0.1，靠归因头区分）—— 否则任何人知道 node id 就能借 hub 打本机。
  - 一切等待有界：调用方 timeout（按路径 15/45/150s）· `_poll` wait ≤60s · 待办上限 32（满了丢最老）· 回执 TTL 180s；**迟到回执按 rid 丢弃**（rid 已不在 `calls` 里 ⇒ `reply` 返回 False），绝不让上一条的答案串给下一条。
- **通道所有者 = `remote/rollout_relay.py::RelayChannel`**（起/等端口/失败清理/监护重建）；`rollout_node` 只持有它并维护 `link`/`url` 两个变量 —— 通道状态（线程/端口/URL）一旦有两份写者，「重建后忘清注册 URL」就是下一个事故。监护判据 = 轮询线程全停 ⇒ `supervise()` 重建 + 清 `reg_state["url"]` 强制重注册（回环端口可能变）。
- **顺带的归属调整**：`remote/rollout_node.py` 已顶在 1000 行 LOC 预算（门禁红），把「预检/姿态」四项（`direct_env`/`probe_https`/`probe_tcp`/`net_probe`）下沉到 `remote/rollout_tunnel.py`，`rollout_node` 保留同名 re-export（`X as X`），测试里的 `rn._net_probe` / `rn._direct_env` 用法不变。
- **证据**：`tests/hub/test_relay.py`（往返含请求体逐字节 / 空拍 / 超时后迟到回执丢弃 / 路由只收回环与 CF 归因 403）· `tests/remote/test_rollout_relay.py` 6 例（hub→agent→hub 全链路 / `local_port` 与注册 URL / agent 4xx 原样回执 vs 端口不通 502 / `RelayChannel` 起通道 / 老 hub 不回 `local_port` 时放弃且不留半开通道 / 线程全停后 `supervise` 重建）· `tests/remote/test_rollout_node.py`（`resolve_link` 强压表 +2）· 分层账本登记 `hub/relay`/`remote/rollout_relay` 均 L0；`nn-python-gate.sh` 全绿（ruff + mypy 941 文件 + pytest 6191 passed）。
- **被否决**：① named tunnel / 域名（用户排除，且仍要走 7844）· ② 第三方免费隧道（把 1/3 算力押在一个免费服务 + 它的域名可达性上，且新增运行时依赖）· ③ 在 hub 上开 CONNECT 代理给 cloudflared 用（家宽上行与公网暴露面都更差，且**建隧道那一步 443 仍不通**）· ④ 把 rollout/eval 派发改成原生 pull（要动「谁发起」语义与 agent 的任务参数面，是主链路手术；relay 复用的是同一条线上契约）。
- **代价/边界**：每请求多一跳节点↔hub RTT（国内 200–400ms），对 3s ping 门与 20s `/v1/result` 轮询够用；带宽不增加（权重本来就经 hub 家宽出、节点回包）。`nodes[].url` 在控制台显示为 `http://127.0.0.1:<port>/relay/<id>`，语义是「只在这台 hub 上有效」。
- **未验证（记账，不假装有）**：真机复测未做（需 ModelScope 重跑 + hub 重启到带 `/relay/` 的版本）；relay 通道下 `emit_output_summary` 的 `/v1/status` 读数与「relay 线程死亡重建」在真机上的恢复时间未测。
- **全文** → `docs/nn/remote-transport.md` §158；代码 → `hub/relay.py` · `hub/http_face.py::_relay_route` · `remote/rollout_relay.py`（`RelayLink` + `RelayChannel`）· `remote/rollout_node.py` · `remote/rollout_tunnel.py`（预检下沉）。

## §2026-11-18-goalnn-relay-unreachable-forensics（relay 连不上 hub 时日志不能自证：预检 + 竞速 connect + 死因字面化）

- **背景（`node_id=dsw` 真机 2026-11-18，`link=relay`）**：`hub_url` 是**正确**的公网 quick tunnel，节点却稳定失败 —— `relay 轮询失败（URLError）——2s 后重试` 同秒 6 条、每条静默 **35s**，40s 后报 `relay 未就绪（40s 内 hub 未应答长轮询）`，再 `★ 未注册：隧道 URL 未取到`，每 60s 重建一次挂 3 轮。
- **三条实测证据（本机，非推断）**：① hub 隧道 `/relay/dsw/_poll?wait=2` 不带令牌回 `401` / **1.2s** ⇒ 路由在、hub 已升级、鉴权在工作；② `tmp/hub-server.out`（223MB）grep `/relay/` = **0 次** ⇒ 节点轮询**一条都没到过 hub**；③ 对照地址 `100.124.208.62:8787`→`401`/0.1s、`127.0.0.1:8900`→`404`/**0.0s** ⇒ DNS 失败/端口拒绝/404/530 **全是秒级**，只有地址黑洞或代理吊死才 35s。旁证：同机当天能自建 quick tunnel 并 `节点登记` ⇒ 到 CF 边缘通。
- **35s 的来源（这次拿到直接读数）**：hub 域名 DNS 返回 `2606:4700::6810:e784(v6), 2606:4700::6810:e684(v6), 104.16.230.132(v4), 104.16.231.132(v4)` —— **先 2 个 IPv6 再 2 个 IPv4**；`create_connection` 的 timeout 是**每地址**的 ⇒ 串行 connect 墙钟 = N×timeout（20s poll + 15s connect ≈ 35s）。
- **决定（`remote/rollout_relay.py` 四处）**：① `_err_text()` 把 `URLError` 翻成 timeout / refused / DNS / TLS / HTTP code，日志补 `hub=host:port` 与实测耗时，首条全文、之后每 10 条汇总（治 6 线程同拍刷屏）；② 两条路由**都竞速**（`racing_opener(no_proxy=True)` / `racing_opener()`），`_route_order()` 上次成功优先，`HTTPError` 视为应答不换路由，全崩才抛 `RelayUnreachable`（继承 `URLError`，`reason` 带逐路由结论）；③ 新增 `probe_hub()`（6s 内出 `(ok, 一行, facts{host,port,dns,routes})`），`RelayChannel.open()` 起线程**前**打这行并 `pin_route()`；④ `_banner_lines` 的 relay 分支改说「真因看预检那行 / relay 只需出网」，不再把人指向不存在的 cloudflared 日志。
- **两处易错细节**：**端口判定禁用「有没有 `https://`」**（`http://1.2.3.4:8787` 会被读成 80）⇒ `_host_port()` 走 `urlsplit` + 显式 `parts.port` 优先；**回环/`NO_PROXY` 目标只跑 `direct`**（否则 LAN 代理把必然通的回环请求吊满 6s，实测某用例 6.12s 超阈值）。
- **证据**：`tests/remote/test_rollout_relay.py` 6 → **14** 例 · `test_rollout_node.py` +1 · `test_source_text_assert_budget.py` 口径 A `1498 → 1502`（先把能结构化的结构化，只留确需看文本的 4 条）· `nn-python-gate.sh` 全绿（ruff + mypy + **6202 passed, 10 skipped** / 150s）。
- **被否决**：① 只改日志文案（真因是竞速与路由，文案只能让它下次更快被读出来）；② 把 `hub_url` 换成 tailnet 地址（节点到不了，且不是本次真因）；③ 在 relay 里加「自动重解析 DNS 换 IP」的重试层（`racing_opener` 已经按地址族并行拨，再加一层只会掩盖问题）；④ 顺手修 `rollout.ipynb` 的 `NO_PROXY` 窄口（`direct` 腿已天然绕代理，改动面大于收益）。
- **边界**：quick tunnel 域名**每次 hub 重启都变**，relay 节点的 `hub_url` 是死值，换域名须重跑 cell（现在 6s 就能看到 `timeout`/`DNS`，不再等 40s 的一句「hub 未应答」）；`NO_PROXY` 只含 `CFG.hub_ip` 的窄口**未修**（记账）。
- **全文** → `docs/nn/remote-transport.md` §159；代码 → `remote/rollout_relay.py`（`_err_text` / `_route_open` / `_open` / `pin_route` / `probe_hub` / `RelayChannel.open`）· `remote/rollout_node.py`（横幅 relay 分支 + 环境行打 hub 主机）· `ipynb/rollout.ipynb`（CFG 注释：`hub_port` 8787 ≠ dashboard 8900、relay 必须填公网地址、新增「relay 起不来先看哪一行」小节）。
- **复盘（同日 15:23 真机复跑，`commit=bcce16b`，见 §159.1）**：
  - **旧格式日志 ≠ 新代码没生效**：`grep "2s 后重试"` 全仓 rc=1 ⇒ 那几行只能来自**另一个还活着的旧进程**（notebook 重启不杀 `rollout-node` 子进程）。⇒ 横幅加 `pid=`，新增 `_warn_if_node_id_taken()`（`<tmp>/battle-rollout-<id>.pid`，别人活着就打「先 kill 它」），**只告警不退出**；判活**禁** `os.kill(pid,0)`（Windows 上等于 TerminateProcess）。
  - **`HTTPError` 一律算「路由通」是 bug**：实测 LAN 代理对黑洞地址回 `502/5047ms`，旧实现会 `ok=True` 并把代理腿钉住，之后每拍长轮询都吃 502。⇒ `GATEWAY_CODES={502,504,52x,530}` 记 `gateway-<code>`，不算通也不进 `_pick_route`；`503` 排除（hub 自己的熔断说明链路通）。
  - **`timeout` 仍分不清三件事** ⇒ 预检失败时追加**逐 IP 裸 TCP**（并行、2s），判词分 `dns/blackhole/tls/unroutable`，结构化出口 `facts["hint"]`；两条路由的 HTTPS 预检改**并行**（串行 12s → 6s）。
  - **hub 侧同时段复测仍正常**（隧道 `401/1.08s`；hub 日志 18 条 `/relay/` 全是本机测试，节点 0 条）⇒ DSW 的出网对该组 anycast IP 是黑洞，修法在承载侧（`CFG.net_proxy` / 非 CF 入口 / 换机器），不在代码侧。
  - **验收**：relay 单测 14→17、node +2；文本断言口径 A **不抬格**（措辞断言换成 `kind`/返回值结构判据）。


## §2026-11-18-goalnn-node-reclaim-on-cell-rerun（cell 重跑 = **先清场再起**：SIGINT 收旧节点 + 同进程 relay 遗留线程一起收）

- **用户口径（2026-11-18 原话）**：「重复执行 `rollout.ipynb` 的 cell，应该把之前的所有进程杀掉，再重新起新进程。」上一轮只做了「告警」（`_warn_if_node_id_taken`），实测证明**不够**：`§goalnn-relay-unreachable-forensics` 的复盘里，DSW 现场那几行旧格式日志只能来自**另一个还活着的旧进程**（notebook 重启不杀子进程），而告警只是把它写进日志给人看。
- **决定 1（跨进程，`common/node_report.reclaim_node_id`）**：`node_id` 的 pidfile 从「裸 pid」升级为 **`pid|身份`**，`run()` 启动第一步**先收掉**上一个 owner（`0` = 没有）。**先 `SIGINT` 不是客气**：对方的 `except KeyboardInterrupt` 会走 `finally`（`unregister` + 收 bun agent / cloudflared），而 SIGTERM/SIGKILL 没有这个待遇 —— 会留下占着 `agent_port` 的孤儿，新实例起来就用不了。`RECLAIM_GRACE_SEC=8` 还不退才 `SIGKILL`；Windows 无可靠 SIGINT（`CTRL_C_EVENT` 要同控制台）⇒ 直接 `taskkill /F /T`（连子进程；走 `run_capture` 免得闪黑窗），且**不白等**那 8s。
- **决定 2（同进程，`remote/rollout_relay.stop_stale_links`）**：上一次 cell 若被**第二个 Ctrl-C** 打在 `finally` 上，`stop()` 没跑完 ⇒ daemon 轮询线程活下来，与本次的通道**抢同一个 hub 队列**（任务随机分给两边、回执各走各的 rid，表面成功、一半结果来自旧代码）。daemon 线程只能同进程收 ⇒ `RelayLink.start()` 起线程**前**先扫 `_LIVE_LINKS` 叫停遗留链接（只设停止位、`join` 上限 1s，硬等只会拖住新通道）。
- **身份核验（为什么要，而不是「pid 相同就杀」）**：pid 会被系统回收，给陌生进程递 SIGINT 最坏能打断一台机器上无关的训练 / kernel。`proc_identity()` = Linux `/proc/<pid>/stat` 第 22 字段（启动戳，快、不起子进程）→ 没有 `/proc` 的平台回落 `common.instance_lock.proc_cmdline`（Windows wmic/CIM、macOS 读不到即空）。**对不上就不动手**，只打一行说明；旧格式裸 pid（身份为空）同样只告警 —— 本次启动把它重写成新格式，下一次重跑即生效。
- **验收（真机 + 单测）**：本机 Windows 实测 ① 真子进程 `pid=24552` 被 `taskkill /F /T` 收掉、1.5s 返回、pidfile 改写成本进程；② 身份写错 / 旧格式裸 pid 两种反证**都不动手、子进程还活着**。单测：`test_rollout_node.py` 5 例（先 SIGINT / SIGINT 被无视才 SIGKILL / pid 复用不动手 / 旧格式只告警 / 自己的 pid 与死 owner 静默）+ `test_rollout_relay.py` 2 例（扫 `_LIVE_LINKS` / `start()` 先清场）。门禁 `NN_GATE_SKIP_E2E=1` → **6079 passed, 10 skipped**（首轮两条 `test_hub_push_dispatch` / `test_remote_iter` 红，单跑绿 ⇒ xdist 抖动，复跑全绿）。
- **风险与边界**：① 目标是**上次会话的 kernel** 时，SIGINT 等于替它「中断那个 cell」—— 这正是用户要的，但**不能**在别人正跑训练时误伤 ⇒ 全部动作以身份核验为前置；② `SIGKILL` 分支会留孤儿子进程（日志明说「手动 kill 后重跑」）；③ 同一个 kernel 里 cells 无法真正并发（Jupyter 排队）⇒ 决定 2 只覆盖「被双 Ctrl-C 打断」这条窄路；④ 之前记账的 `rollout.ipynb` `NO_PROXY` 窄口**仍未修**。
- **代码** → `common/node_report.py`（`proc_start_stamp` / `proc_identity` / `read_node_owner` / `write_node_owner` / `reclaim_signal` / `reclaim_kill` / `reclaim_node_id`；`rollout_node.py` 已顶 1000 行预算）· `remote/rollout_relay.py`（`_LIVE_LINKS` / `stop_stale_links` / `RelayLink.start|stop`）· `remote/rollout_node.py`（启动第 2 步清场）· `ipynb/rollout.ipynb`（排查表：自动清场两行日志 + 身份核验口径）· 文 → `docs/nn/remote-transport.md` §159.2。

## §2026-11-18-goalnn-relay-tls-layer（`TCP 通而 HTTPS 不通` 再切一刀：裸 TLS 握手 + 对照主机，判词分四类）

- **背景（`node_id=dsw` 第三次真机 2026-11-18 18:46，`commit=48f6ae4`）**：上一轮新增的逐 IP 裸 TCP 第一次给出了决定性读数 —— `TCP=104.16.230.132:443 ok,104.16.231.132:443 ok,2606:4700::6810:e684:443 unreachable,2606:4700::6810:e784:443 unreachable`，而两条 HTTPS 路由仍全 `timeout`。两件事当场钉死：① v4 两个地址 **TCP 全通** ⇒ 不是黑洞/DNS/「这台机器上不了网」；② v6 两个地址 `unreachable` ⇒ DSW 无 v6 路由，属正常，不该当故障修。
- **问题**：`TCP 通 + HTTPS 无应答` 这一句仍同时覆盖三种**互斥**成因，而修法成本差一个量级 —— ①握手就被丢（DPI 按 SNI 过滤 / 路径 MTU 黑洞）⇒ 换隧道域名即可；②握手成功、HTTP 不回（隧道死了域名还在 / hub 进程没跑 / 反代挂住）⇒ 去 hub 机器；③中间人顶证书 ⇒ 走代理并信根证书。
- **决定 1（`remote/rollout_relay._tls_verdict` / `_tls_sweep`）**：TCP 通了才切**第四层：裸 TLS 握手**（不发 HTTP，`TLS_PROBE_SEC=3.0`，CF 边缘正常 ~150ms，多地址并行）。`verdict ∈ {ok:<版本>, timeout, err:cert:…, err:tls:…, err:conn:<errno>}`。只在 `https://` 上跑 —— `http://1.2.3.4:8787` 的裸握手必然失败，把它写成 `tls-blocked` 只会把人送去换机器。
- **决定 2（对照主机 `CTRL_HOST="api.trycloudflare.com"`）**：同一 CF 边缘、同一套泛域名证书，但**不带隧道域名那段随机前缀**；与 hub 的 TLS 探测**并行**（对照不该再吃一份预算），端口恒用 443（问的是「本机到 CF 边缘的 TLS 通不通」）。判定表：hub 无应答 + 对照也卡 ⇒ `tls-blocked`（走代理/非 CF 入口/换机器）；hub 无应答 + 对照成功 ⇒ **`sni-blocked`（重建 quick tunnel 换域名，最便宜的一档）**；hub 握手成功 ⇒ `app-stall`（问题在 hub 侧）；证书/TLS 错误 ⇒ `tls-error`（MITM）。对照四地址结论一致时压成 `ok:TLSv1.3（4 个地址一致）`，否则单行会被淹。
- **真机验收（本机 Windows，两条都实测）**：① 公网隧道健康 ⇒ `ok=True`、`inherit/direct=HTTP 401`、**不切 TLS 层**；② 本机监听只 accept 不说话（人为构造「TCP 通、握手不回话」）⇒ `TLS=127.0.0.1 timeout（握手无应答）；对照 api.trycloudflare.com=ok:TLSv1.3（4 个地址一致）`、`hint=sni-blocked`。⇒ 同一台机器上「hub 域名的握手被丢」与「到 CF 边缘的握手是好的」确实能分开。
- **对 18:46 那次的判读（诚实版）**：落在 `tls-blocked` / `sni-blocked` 之间，**必须等 DSW 跑出带 `TLS=` 与 `对照 …` 的新日志才能定**；旧判词「换域名或走代理」两边都指得对，但成本差一个量级，所以这一层值得加，不是文案美化。
- **证据**：`tests/remote/test_rollout_relay.py` 17 → **23** 例（+4：`tls-blocked` 对照也卡 / `sni-blocked` 对照成功 / `app-stall` 握手成功 / `_tls_verdict` 两条真路径 —— 只 accept 不说话 → `timeout`、accept 后立刻 close → `err:tls*`）。判据全部钉 `facts["hint"]` / `facts["tls"]` / `facts["tls_ctrl"]`，**不钉子串** ⇒ 文本形态断言口径**不抬格**（护栏仍 1502；第一版写了 `assert "TLS=" in line` 被护栏拦下，改成结构化断言）。
- **边界**：裸握手不验应用层，`ok` 只说明「能握手」；对照主机只覆盖 CF 这条路径，hub 换非 CF 入口（自建反代 / 内网直连）时对照要跟着换，否则 `sni-blocked` 会误报。`__all__` 已导出 `TLS_PROBE_SEC` / `CTRL_HOST`。
- **全文** → `docs/nn/remote-transport.md` §159.3；代码 → `remote/rollout_relay.py`（`TLS_PROBE_SEC` / `CTRL_HOST` / `_tls_verdict` / `_tls_sweep` / `_fail_hint` 四类新分支 / `probe_hub` 第四层）· `tests/remote/test_rollout_relay.py`（`_stub_layers` 打桩 + 4 例）。

## §2026-11-18-goalnn-online-worker-bun-for-replay — 两块在线盘**开机供给 bun**（落持久缓存）：replay → npy 的转换在 worker 侧跑

- **用户口径（2026-11-18 原话）**：「两块在线盘环境启动后，需要先安装 bun，以执行 replay 到 npy 的转换。
  需要像 rollout 一样，定义安装文件的持久化位置，避免每次新会话都下载。」
- **为什么**：`corpus_fmt=replay`（2026-11-17 起默认）把 `replay.json` 下给 worker，obs/scalars/extra 由
  `remote/replay_corpus` 在 worker 进程里重建，运行时顺序 **Bun 优先**、Node 兜底（plan D5）⇒ 开机没 bun
  就每个会话都退到 Node（1.15× 慢，两套运行时口径）。
- **决定**（`§2026-09-25-…-online-worker-no-bun` 的例外再扩一格，沿 `§2026-10-21-…-bun-for-self-collect`
  四条硬约束）：① 装机**复用** `remote/rollout_node.ensure_bun`（⇒ `_ensure_bun` + `_bin_cache_dir`），
  不另写一份；② 供给点**早于任何 worker 子进程**（`run_notebook` 第一件事 + `notebook_boot._push`
  因 push 先 spawn 而自己补一次）；③ 持久位置是**数据**（`CFG.bin_cache_dir` / `BATTLE_BIN_CACHE`，
  否则按平台持久根 → `.battle-bin/bun`）；④ 装不上**不致命**（回落 node，仅记一行）。
- **读数（同日补）**：转换开场打 `replay 转换运行时：<bun|node>（<路径>）· 档位=primary|fallback`（回落附
  原因）+ 回落档**额外**一条 `WARN … 比 bun 慢约 1.15×（22.1→25.4 µs/tick）…修法：持久缓存 / corpus_fmt=full`
  （**每轮都打、不设 warned 位**：去重会让长会话在日志滚过后重新静默）；收尾行补 `单 shard 均值/最长`，
  bundle 行带运行时 —— 原先只有末尾 `runtime=node`，读者得先知道「本该是 bun」才看得出回落。
- **被否决**：cell 内联装（两份装机必漂 + 在线 cell 禁安装机制）· 在 `resolve_js_runtime` 里 JIT 装
  （把公网下载塞进 job 关键路径）· 只在 worker 进程里装（装完还得重启才进 PATH）。
- **护栏**：`tests/remote/test_tailscale_boot_bun.py`（一份实现 / 早于 spawn / 持久位置是数据 / 不致命）+
  `test_worker_replay_prepass.py::test_logs_runtime_and_per_shard_elapsed`；文本断言口径 A/B 不抬格。
- **全文** → `docs/nn/remote-transport.md` §160；代码 → `remote/{rollout_node,notebook_runtime,notebook_boot,replay_corpus}.py` ·
  `ipynb/battle.{tailscale,cloudflared}.ipynb`。

## §2026-11-18-goalnn-relay-cf-blocked-verdict

- **现场**：DSW relay 预检 `TCP` 全 ok、`TLS` 全 timeout、唯一对照 `api.trycloudflare.com` 也 timeout ⇒
  判词 `tls-blocked`（「走代理 / 换非 CF 入口 / 换机器」）。**这一步是误判**：对照与 hub 同属
  `trycloudflare.com` 后缀，按这个后缀掐时对照必然一起卡 ⇒ 最便宜的修法（换个域名，同一边缘）被抹掉。
- **裁定**：预检带**两条用途不同的对照**（同预算内并行）——`CTRL_HOST = cloudflare.com`（同边缘、不同注册域）
  与 `CTRL_HOST_NET = registry.npmmirror.com`（非 CF）。hub 卡 + 前者通 ⇒ `sni-blocked`（换域名最便宜）；
  前者也卡 + 后者通 ⇒ 新判词 **`cf-blocked`**（只有 CF 被掐 ⇒ 换非 CF 入口 / 代理，**换机器无用**）；
  两个都卡 ⇒ `tls-blocked`（本机 TLS 出网整体被掐）。对照问不到（DNS 空）时如实降级，不升格成「整家 CF」。
- **被否决**：保留单对照并对 `tls-blocked` 的措辞加一句「也可能是按域名掐」（读者要在两种修法间自己猜，
  等于把 §159.3 刚消灭的猜的那一步又请回来）· 把 `github.com` 当非 CF 对照（DSW 上它常要走 gh-proxy 镜像，
  拿它当「本机出网正常」的证据会把 `tls-blocked` 误报成 `cf-blocked`）。
- **护栏**：`tests/remote/test_rollout_relay.py` 21 → 25 例，判据只钉 `facts["hint"]` / `tls_ctrl` / `tls_net`，
  文本断言仍 1502（+0）。
- **全文** → `docs/nn/remote-transport.md` §161；代码 → `remote/rollout_relay.py` · notebook 排查表 → `ipynb/rollout.ipynb`。

## §2026-11-18-goalnn-paddle-ppo-lazy（Paddle 后端支持 PPO lazy loader：共用分派 + 逐批上卡 + ref 惰性预计算）

- **背景/决定**：生产缺省 `ppo_loader=lazy`（§2026-11-17-goalnn-ppo-lazy-default）后，Paddle worker 仍对 lazy
  job **响亮拒收**（`remote/paddle_env.check_ppo_loader`）⇒ `g6-speed-c1` 等课程在 Paddle 节点上直接失败
  （`job … REJECTED: paddle 后端不支持 ppo_loader='lazy'`）。本条把 P3 lazy 接到 Paddle 双生，**取代**
  §2026-11-16-goalnn-ppo-lazy-loader 与 §2026-11-17-goalnn-ppo-lazy-default 里「Paddle 拒收 lazy」那一条。
- **实现**：`remote/train_common.load_ppo_episodes` 成为**两条训练核共用的唯一分派点**（eager 逐字节旧
  `np_core.load_episodes`；lazy 只在 manifest 显式声明时走 mmap）；Paddle 核改用它，并把装载器自身的
  `.lazy` 传给 `engine_paddle.ppo_update(lazy=…)`（不重读 manifest，避免「说 lazy 实际 eager」的错账）。
  引擎 lazy 路径跳过 `require_corpus`/全量 `tensored_chunks`，每步 `tensored_batch` 只搬当前 minibatch；
  kickstart ref 改为 chunk 首次使用时现算（`_ref_logprobs` 抽出，与 eager 共用同一份头数/动作空间校验）。
  删除 `paddle_env.check_ppo_loader` 与整条拒收路径。
- **证据（本机 2026-11-18，paddle 3.3.1）**：`tests/worker/test_ppo_lazy_loader.py` +3 条 —— 分派器按
  manifest 透传 `loader/mb/per_stage_quota`（缺键仍 eager）；Paddle eager/lazy 同 seed 的**最终权重逐字节
  一致**且统计量 `abs=1e-12`；lazy 只逐批搬 `mb` 行且 `tensored_chunks` 被调用即失败（2 chunks × 2 epochs
  = 4 次 `(8,C,B,B)`）。`tests/remote/test_train_core_split.py` 用 `__code__.co_names` 钉住 Paddle 核引用
  `load_ppo_episodes`、不再引用 `load_episodes`/`check_ppo_loader`。`bash tools/githook/nn-python-gate.sh`
  全绿：ruff / mypy 941 文件 / **6232 passed, 10 skipped**。
- **层号**：`remote.train_common` L2 → **L3**（延迟 import `worker.ppo.lazy_corpus`(L2)，拓扑秩 = 1+2）；
  读它的 `remote.train_core`(L4) 不变（账本已同步）。
- **被否决**：① Paddle 静默退回 eager（manifest 与日志各说各话，2026-11-16 已否决）；② 把装载分派留在
  `train_core`（有效 LOC 981，且两核共用判据会重新分叉）；③ 复制 torch `tensored_batch` 的
  `memory_format` 分支（Paddle 无此参数；逐批 `paddle.to_tensor` 已覆盖语义）。
- **后果/未完成**：Paddle GPU 真机（AI Studio）的 35×20 H2D/update 墙钟与显存余量尚未测；判据 = 同一 job
  在 torch / Paddle 节点的 `wire` 行与 `loader=lazy peak=…` 读数。回滚 = job 显式 `--ppo-loader=eager`
  （Paddle 侧旧路径逐字节不变）。全文 → `docs/nn/remote-transport.md` §162。

## §2026-11-18-goalnn-relay-alt-port-fallback（中间层只掐 443/8443 ⇒ 预检失败后**换端口**自救，不换域名不换机器）

- **现场**：判词已收敛到 `sni-blocked`（同边缘的 `cloudflare.com` 能握手），但用户在 DSW 上直接 `curl`
  拿到决定性读数：`https://<hub>:443 = 000`、`https://<hub>:80 = 301`（80 层没拦，CF 强制跳 HTTPS）、
  **`2053/2083/2087/2096` 全回 `401`**（那是 hub 自己的鉴权在回话 ⇒ TLS+HTTP 整链通）、`8443 = 000`、
  tailnet 控制面 `000`。
- **裁定**：中间层不是掐机器、不是掐 CF、不是掐域名整体，而是**在 443/8443 上按 SNI 掐这个域名** ⇒
  唯一要改的是**端口**。`probe_hub` 在端口级失败（`blackhole / sni-blocked / tls-blocked / cf-blocked /
  tls-error`）之后，对 CF 官方的五个备用 HTTPS 端口各发一次**真的**预检（并行）；有 `ok`/`http-*` ⇒ 判词
  改写 **`port-blocked`** + `facts["alt_port"]`，`RelayChannel.open` **当场改写 `hub_url`** 再起线程
  （改写结果留在 `_args`，线程死亡重建不重复扫描）。`app-stall` 不扫（那是 hub 自己有问题，换端口一样不回）。
- **为什么用真请求而不是裸握手**：`gateway-5xx` 表示端口通但 CF 够不到上游，换过去只会从「超时」变成「530」；
  只有拿到 `http-*` 才算端到端可用。
- **被否决**：① 换自有域名 + 命名隧道（成本高一个数量级，且要动 CF 侧；实测同域换端口就够）；
  ② 走 80 端口（`301` 证明 80 未拦，但 quick tunnel 没有「关掉强制 HTTPS」的开关）；③ tailnet 保底
  （`E=000`，DSW 上同样死）；④ 只写文档让人手改 `hub_url`（下次换机器又要人手改一遍）。
- **护栏**：`tests/remote/test_rollout_relay.py` 25 → **29** 例；文本断言口径不抬格（`"x" not in facts`
  改成 `set.isdisjoint`，护栏仍 1502）。
- **全文** → `docs/nn/remote-transport.md` §159.4；代码 → `remote/rollout_relay.py`
  （`ALT_HTTPS_PORTS` / `_port_sweep` / `with_port`）。

## §2026-11-18-goalnn-relay-client-gone-is-warn（`/relay/…/v1/result` 的 `WinError 10053` 是「对端先走」，不进错误注册表）

- **现场**：端口回退生效后 DSW 节点真连上了，hub 一分钟报 16 条
  `ERROR /relay/dsw/v1/result: [WinError 10053] An established connection was aborted by the software
  in your host machine`，全被组件错误注册表按「未处理错误」上报。
- **裁定**：`10053`（`ConnectionAbortedError`）/`10054`（`ConnectionResetError`）/`BrokenPipeError`
  语义统一是**这条连接没了** —— relay 的 `/v1/result` 在 hub 侧要让 hub 等节点回执（45s）才写响应，
  调用方 urllib 预算更短时必然撞上。这是**链路抖动**，不是 hub 故障 ⇒ 判据用**异常类型**（不用
  Windows 文案），`do_GET`/`do_POST` 各加一条 `except ConnectionError`：**不记 ERROR、不回 500、
  不再抛**，只落一行节流的 `WARN … 对端提前断开（累计 N 次，非 hub 错误）`（首条 + 每 10 条一条）。
- **上一版为什么连 traceback 都进了注册表**：`except Exception` 里先记 ERROR，再
  `_json({"error":"internal"}, 500)` —— 写在死 socket 上**又抛一次**，那一次的栈才是注册表里那条。
  对端都没了，500 没有收件人。
- **被否决**：① 把 hub 侧 `/v1/result` 等待从 45s 降到几秒（会把健康的慢局变成 504）；
  ② 全局 `except ConnectionError: pass`（真错误会被一起咽掉，反证用例专门钉这一条）。
- **护栏**：`tests/hub/test_hub_client_gone.py` 3 例（真起 `make_server` + `SO_LINGER=0` 发 RST ⇒
  只有 WARN、无 ERROR、stderr 无 traceback、下一条请求仍 200 / `RuntimeError` 仍 500 / 节流 1·10·20）。
  ⚠ `linger` 结构 Windows 是 `HH`、POSIX 是 `ii`，写错会让用例假绿。
- **全文** → `docs/nn/remote-transport.md` §159.5；代码 → `hub/http_face.py`
  （`CLIENT_GONE_LOG_EVERY` / `HubHandler.note_client_gone`）。

## §2026-11-18-goalnn-relay-ping-from-heartbeat（relay 模式的探活**不再等一次往返**：通道心跳 + 载荷缓存）

- **现场**：端口回退 + `对端先走` 降噪后节点在 DSW 连续工作了，但用户读数仍「稳定性不佳」，
  并给了决定性反证：直连（`--noproxy '*'`）连发三次同一请求 `401 1.26s / 401 3.52s / curl(28) 6.00s 0 字节`
  ⇒ 链路通（401 = hub 鉴权在回话）但**延迟从 1.3s 漂到超时**，「改走直连就更稳」当场作废。
  hub 日志 22:40–23:20 定量：`/relay/dsw/v1/ping` **186 ok / 192 ×「对端先走」/ 67 ×504**；
  节点自报 `polls=499 served=438 errors=83`（16.6% 轮询失败）；530 突发与 hub 侧 `task 504` 扎堆时间吻合。
- **裁定**：病灶是**设计**把「一次本地事实查询」实现成了「一次分布式往返」—— 调用方预算
  `STATUS_TIMEOUT_SEC=3s`，而 relay 一次 `call` 要等节点下一次长轮询（`POLL_WAIT_DEFAULT=20s`）⇒
  约一半探活**注定假失败**，handler 线程还空占 15s。判据换成两个本地事实：
  ① `channel_age`（节点每次 `_poll` 刷新的**通道心跳**，> `CHANNEL_STALE_SEC=60s` ⇒ **503 快速失败**）
  ② 上一次成功 `/v1/ping` 的**载荷缓存**（`PING_TTL_SEC=30s` 过期即后台刷一次、单飞行；
  `PING_SERVE_MAX_SEC=120s` 之外老实走真调用，不拿化石台账喂 `codeHash`/`bootId` 门）。
- **为什么 TTL 是 30s 不是 600s**：载荷里带 `codeHash`（派发门，a98 = 两版本混跑）与 `bootId`
  （单实例门），滞后必须短；下界又被「刷新本身要等一次长轮询」夹住 ⇒ 30s 是两头夹出来的，
  120s 只是**隧道黑洞**那一档的兜底。已知代价（relay 模式下这两个门最多滞后 120s）写进文档。
- **被否决**：① 让节点把轮询间隔缩到 3s（隧道请求 ×7，`_poll` 今夜已 1903 条/3h，稀缺资源不能这么烧）；
  ② 把 hub 侧探活预算抬到 20s（注册会被拖成 20s+，且调用方还是超时）；③ 只靠直连/换路（用户读数已否）。
- **易踩**：回落路径**也得把载荷存下来**（否则缓存永远空，短路白写）；单飞行标记只由认领者
  `finally` 放回（`note_ping` 顺手放会让两条路互相打开重入权）。
- **护栏**：`tests/hub/test_relay_ping_cache.py` **6 例**（零往返 / 503 快速失败 / 先答后刷 + 单飞行 /
  回落即缓存 / 化石不用 / 开关语义），文本断言口径不抬格（`"relayAgeSec" in payload` 改成
  `set(payload) >= {...}`，护栏仍 1502）。
- **全文** → `docs/nn/remote-transport.md` §159.6；代码 → `hub/relay.py`
  （`CHANNEL_STALE_SEC` / `PING_TTL_SEC` / `PING_SERVE_MAX_SEC` / `channel_age` / `ping_snapshot`）·
  `hub/http_face.py`（`_serve_relay_ping` / `_refresh_relay_ping` / `_cache_relay_ping`）。


## §2026-11-19-goalnn-relay-lanes-follow-workers（relay 的道数必须盖住 agent 的 worker 数：6 条道把 16 worker 的节点腰斩）

- **现场（hub 日志 `tmp/hub-server.out` 09:40–11:24 + trainer 读数）**：节点自报
  `relay polls=606 / served=539 / errors≈19`，`served/polls=89%` ⇒ 每条长轮询几乎总带着活；
  而节点侧 `workers=16`、trainer 给 dsw 的 `concurrency=12`，**relay 只有 6 条道**
  （`POLL_WORKERS=6`）。trainer 实测 `dsw wall=6.6–7.9s/局`，同时段 hub burst 峰值
  `95–108 局/分`（`v1/task` 200）—— 6 条道 × 单局算力 ~3.7s 正是 97 局/分，**两条独立读数对上了**：
  传输层在算力层前面当天花板，16 个 worker 只用到 ~37%，trainer 看到的一半 wall 是排队。
- **裁定**：道数不是「探活/控制面的缓冲」而是**并发上限**（一条长轮询线程一次只驮一条请求，
  `_forward` 在它自己的线程里跑完才回去取下一拍）。`RelayChannel` 必须接收 agent 的 worker 数，
  `workers = max(POLL_WORKERS, agent_workers)`：dsw 16、8C 小机器保底 6。控制面（ping）走
  hub 的心跳缓存短路，不占道；weights 只发生在轮次边界，另有富余道。
- **被否决**：① 抬高 `CFG.workers`（算力层不是瓶颈，抬了只会加排队）；② 给 `_forward` 加
  线程池 + 队列（多一层背压，且队列里的请求照样吃调用方预算，收益与直接加道相同）；
  ③ 让 trainer 把 dsw 的 `concurrency` 降到 6（等于承认传输层决定算力，方向反了）。
- **同批修的两处**：回执投递单发 → **3 次短退避重试**（4xx 不重试：确定性失败；CF 502/530
  现场 10:31:52 一次丢三条，丢一条 = 一整局白跑，hub 侧 `CALL_TTL=180s` 内重发幂等）；
  hub 访问日志 `print()`（正文/换行两次 write）→ **单次 write**（relay 行成片粘成
  `… 200 -[11:24:05] …`，而这份日志正是排查 relay 的唯一入口）。
- **护栏**：`tests/remote/test_rollout_relay.py` +4（道数跟随 workers / 502 重试两次后成功 /
  4xx 不重试 / 三次失败只记一行）· `tests/hub/test_hub_auth_d9_order.py` +1（一行一次 write）。
- **未验证**：真机需重启 DSW 节点（节点代码随 clone 更新）；hub 需重启才吃到日志原子写。
- **全文** → `docs/nn/remote-transport.md` §163；代码 → `remote/rollout_relay.py`
  （`POLL_WORKERS` / `REPLY_TRIES` / `RelayChannel(workers=)` / `_deliver`）·
  `remote/rollout_node.py`（`RelayChannel(..., workers=workers)`）· `hub/http_face.py`
  （`HubHandler._log_lock`）。

## §2026-11-19-goalnn-relay-keepalive（relay 的 poll/reply 走长连接：hub 升 HTTP/1.1 + 复用 `common/http_pool`）

- **现场**：一条道每局固定付两笔与算力无关的往返 —— 回执新建一条连接、下一次长轮询再新建一条。
  urllib 不发 keep-alive，而 hub 是 `BaseHTTPRequestHandler` 的默认 `HTTP/1.0`（每条响应后必关），
  所以客户端的池也只能一拍一建。隧道单程 ~0.5–0.7s（预检 `_poll?wait=0` 1074ms），
  在 `wall=6.6–7.9s/局` 里白扔 10–20%，还被读成「hub pending 里等道」（§163）。
- **裁定**：① `HubHandler.protocol_version = "HTTP/1.1"`（前提：每个响应都带 `Content-Length`，
  写头入口只有 `respond_json` / `_send_headers` 两处）；② relay 的 `_poll`/`_post_reply` 走
  `common.http_pool.request`（thread-local、每请求 timeout、urllib 同语义异常），
  `direct` 腿用新增的 `no_proxy=True`（池键含代理 ⇒ 两条路由各自一条连接）；
  ③ 道线程退场 `http_pool.close_thread()`（否则 hub 侧每条 1.1 连接永久挂一个 handler）。
  路由回落与「4xx/5xx 算路由通、不切换」的判据原样保留；预检继续走一次性 `_route_open`。
- **顺手**：对端提前断开（`ConnectionError`）的两个分支补 `close_connection = True` ——
  1.1 下 handler 会回头读下一请求，死 socket 会读出一段 `socketserver` traceback
  （`test_hub_client_gone` 的红就是这条）。`stats_line()` 末尾加池读数 `reuse/connect`。
- **被否决**：① 在 1.0 上手写 `Connection: keep-alive`（事实标准，还要自管 `close_connection`
  与响应定长 = 手写半个 1.1）；② 另写 relay 专用客户端/httpx（第二份代理/竞速/超时真相；池本来
  就是为此存在的，`NODE_HTTP_POOL=0` 还是同一条逃生口）；③ HTTP/2 多路复用（换协议的代价远大于
  这一格收益）。
- **护栏**：`tests/hub/test_hub_keepalive.py`（同一连接连发两次 200 + `Content-Length`）·
  `tests/remote/test_rollout_relay.py::test_lane_reuses_one_tcp_connection`（服务端看到的客户端端口
  恒为 1；降回 1.0 时 6 拍 = 6 个端口，已实测红）· `tests/common/test_http_pool.py` +2
  （`no_proxy` 强制直连且与代理腿各占一条连接 / `close_thread` 关连接不清统计）。
  门禁：`tests/hub` 615 passed · `tests/remote + tests/common` 2434 passed / 1 skipped ·
  ruff / mypy · LOC 与文本断言预算未动（1502 / 1092）。
- **未验证**：真机需**重启 hub**（协议版本是类属性）并重跑 DSW cell（节点代码随 clone 更新）。
- **全文** → `docs/nn/remote-transport.md` §164；代码 → `hub/http_face.py`（`protocol_version` /
  `close_connection`）· `common/http_pool.py`（`no_proxy` / `close_thread`）·
  `remote/rollout_relay.py`（`_send` / `_poll` / `_post_reply` / `stats_line`）。
- **⚠ ①③ 已被撤销**：同日下午首次上真机即把 DSW 节点的隧道打黑 20 分钟 ⇒ `§2026-11-19-goalnn-hub-stays-http10`。
- **补充（同日，`_send` 的预算口径）**：`timeout` 改为**跨路由总预算**（每段拿 `剩余 - 12s × 后续路由数`，
  下限 12s）。原因：被打黑时每拍 70.4s = 35s × **两条路由**（原实现逐路由一份 timeout），6 条道
  一起空转、失败计数翻倍。均分被否决（长轮询自己要 20s，均分会把健康空拍切成假超时）。
  护栏：`test_send_budget_is_one_timeout_across_routes`；全文 → `docs/nn/remote-transport.md` §166。

## §2026-11-19-goalnn-hub-stays-http10（hub 的 origin 面永不复用连接：1.1 被撤销，节点侧池化保留）

- **事故**：hub 12:34 重启后带 1.1。12:41 cloudflared 报 `Unable to reach the origin service …
  HTTP/1.x transport connection broken: malformed HTTP status code "HTML>"`（= 把上一条响应体的
  残片当成下一响应的状态行），12:42 `use of closed network connection`，**12:44:50 之后 hub 再没
  收到任何 `via=cf` 请求**；节点侧从 12:47 起每拍固定 70.4s（= 35s × 两条路由）全 timeout，
  `errors` 10 → 80，产出冻结在 380 局。同期 hub 健康（11 线程，jobs/admin 正常）、隧道也活着
  （从本机 `:2083/healthz` 1.2s 到 hub）⇒ 死的只有「cloudflared → hub」那条被复用的连接。
- **机理**：origin 复用下只要有一条响应没写完整（`_send_body` 停滞、头部已发之后抛异常），
  下一条响应就会被当成残片解析 ⇒ 连接**永久**失同步；1.0 的「响应完就关」让半写无法穿越。
  节点那一侧当时跑的是 HEAD（无池化代码），真正上线的东西只有 hub 的 1.1。
- **裁定**：① `HubHandler` 保持 `HTTP/1.0`（不设 `protocol_version`，理由写进类注释）；
  ② 节点侧照旧走 `common/http_pool` —— 被复用的对端是 **Cloudflare edge**，省的是节点↔edge 的
  TLS 握手（~0.5s/局），origin 是不是 1.1 只省本地回环的 0.2ms 级开销；
  ③ 恢复姿势：重启 hub（丢 1.1）+ 重启 cloudflared / 重跑 cell（丢被毒死的隧道连接，不自愈）。
- **护栏**：`tests/hub/test_hub_origin_close.py`（`resp.will_close` 为真 + `conn.sock is None` +
  `Content-Length` 定长）；`tests/remote/test_rollout_relay.py` 的复用用例改用假 1.1 hub 并注明
  「生产里被复用的对端是 edge」。
- **被否决**：① 保留 1.1 并给每条连接加「N 拍后主动关」（延迟失同步而不是消除它，且每条道
  仍然会周期性踩到半写窗口）；② 只在 hub 侧对 `via=cf` 请求强制 `close_connection`（要信
  `CF-Connecting-IP` 头，而同一个 handler 还要服务 Tailscale 直连，判据比收益脆）。
- **全文** → `docs/nn/remote-transport.md` §165。

## §2026-11-19-goalnn-eval-pool-follows-arbiter（eval 池每次都现取 `eval_arbiter()` 单例 + 线程寿命只由 stop 定）

- **症状**：全量门禁（xdist）里 e2e 的 eval 用例**每次红不同一条**、单跑恒绿：`test_it_eval_deferred`
  （I7 eval round never dispatched）、`test_it_eval_post_ppo_weights`（teardown「I10 2 eval games landed
  (got 0)」）、`test_it_mixed_cluster_rollout_pauses_new_eval_grants_but_keeps_inflight`、`test_eval_local_gate`
  （读不到 `eval-local/dist-agent-meta.jsonl`）。同一秒现场：`dispatch 6 … -> [(node,2)]` 紧跟
  `收工（worker 线程归零，未全落盘 0/6）【workers-gone：剩余待派 6（从未派出 6）、在飞 0】`；
  本机腿版本则是 30s 零局 + `window`。
- **根因**：`EvalExecutors.__init__` 把 `eval_arbiter()` 的**实例**缓存进 `self._arb`，而
  `trainer/eval_dispatch.py` 每轮现取单例再 `register(rnd)`。`tests/trainer/test_eval_dispatch_resilience.py`
  与 `test_eval_drain_dedup.py` 的 fixture 调 `reset_eval_arbiter()` **换掉进程单例** ⇒ 池线程守着旧仲裁器
  （永远看不到新登记的轮）⇒ `take()` 恒 `None`、一张牌发不出去。单跑不在同一个 worker、单例没被换过，所以恒绿。
- **裁定**：① `EvalExecutors` 默认**每次现取** `eval_arbiter()`（显式注入的替身照旧钉住）；
  ② 池线程把 `take() is None`（空闲自缩那条）当重问信号而不是退出信号 —— 旧写法会让
  「正卷尾退出的线程」被 `_resize_locked` 当成 `is_alive()` 的可用槽（TOCTOU）：`stop` 被清、不补新线程，
  随后 `active()` 归零 ⇒ `supervise_round` 同秒判 `workers-gone`；线程寿命只由 `stop`/`_closed` 决定；
  ③ `supervise_round` 在 `workers-gone` 与窗口到期两条收工路各打一行现场读数（`pending/inflight` +
  每节点 `active/slots/routed` + `local: active/slots/ok/spawned`），把「没牌」与「没线程」分开。
- **护栏**：`tests/worker/test_eval_pool_permits.py::test_pool_follows_a_replaced_arbiter_singleton`
  （换单例后 `ex._arb is eval_arbiter()`，显式注入仍钉住）、`::test_pool_thread_survives_brief_idle_so_a_new_round_still_has_a_consumer`
  （空闲一拍后 `active()==2`，`close_pools()` 后退干净）——两条都在旧行为上确认过红。
- **被否决**：① 给 `supervise_round` 加「启动宽限」（治标：宽限过后照样零局，且把真因藏起来）；
  ② 让 `reset_eval_arbiter()` 不换实例、只清场（会一并拿掉两条测试用例之间的隔离能力）；
  ③ 在 `_resize_locked` 里加「线程已准备退出」标志位（也能修 TOCTOU，但要在两个循环各加一份收尾协议，
  比「线程不自杀」复杂）。
- **全文** → `docs/nn/remote-transport.md` §167。

## §2026-11-19-goalnn-cloudflared-origin-ipv4（cloudflared 回源一律写 IPv4 字面量；换端口的改写必须交给注册）

- **症状**：DSW 采样节点 relay 模式连续多轮接不上 hub。同一段日志里两件事同屏：
  `relay 就绪（:2096 长轮询能通）` + `relay 轮询失败：HTTP 502`（3 分钟内 170 次）；
  `注册失败: URLError: handshake operation timed out` ⇒ `★ 未注册`。此前五轮都在换域名 / 换机器 / 调代理。
- **根因①（hub，502 的唯一来源）**：`cloudflared tunnel --url http://localhost:8787`。Go 把 `localhost`
  解析成 `::1` 后只拨 IPv6 回环，而 hub 只绑 `127.0.0.1` ⇒ 每条回源 `dial tcp [::1]:8787: refused`，
  CF 返回 **502**。取证：`127.0.0.1:8787/ping` = 401（hub 活着）、`[::1]:8787` = 连接被拒、
  经隧道的 10 发并发 = **8×502 / 2×401**、cloudflared 日志 1080 条 `Unable to reach the origin service`、
  `530` 与 cloudflared `Connection terminated` 同秒。⚠ `curl localhost` 会回落 v4 ⇒ 本机复现不出来。
- **根因②（节点，永远注册不上）**：`probe_hub` 判 `port-blocked` 后 `RelayChannel` 只改自己的
  `_args["hub_url"]`，`run()` 算出的 `hub_url` 仍原样喂给 `register()` ⇒ 长轮询在 `:2096`、注册恒打 `:443`
  （被中间层按 SNI 掐）⇒ `handshake timed out`。
- **根因③（诊断面）**：`_send` 把 502/504/52x/530 当「应答」——不换路由、还把坏路由记成「上次成功」。
  同一文件里的预检早就把这些码归成「不通」，两份判据互相矛盾。
- **裁定**：① 所有 spawn cloudflared 的地方（`dashboard/src/stack/hub.ts` · `specs.ts` ·
  `remote/rollout_node.py` · `push_tunnel.py` · `push_bootstrap.py`）origin 一律写 `http://127.0.0.1:<port>`，
  **禁 `localhost`**；② 新增 `RelayChannel.hub_url`（当前地址的唯一真相），`_open_relay` 里一句
  `hub_url = relay.hub_url or hub_url` 让首注册 / 240s 重注册 / 横幅 / 注销四处全部跟随
  （**不给三个调用点各包一层**：该文件 LOC=999 正压预算线，且单一真相本就该只有一个赋值点）；
  ③ `_send` 把网关码按
  传输失败处理（换路由、不记 route、全崩时死因行带 `server`/`cf-ray`/响应体前 120 字）；
  ④ 换端口时钉 `direct`（扫描只验过直连那条）。
- **护栏**：`tests/remote/test_rollout_relay.py` +3（网关码换路由 / 死因行点名 CF / `hub_url` 跟随改写）·
  `tests/remote/test_rollout_node.py` +1（**整条 `run()`** + 假通道换端口 ⇒ 注册与注销都打 `:2096`）与
  `_start_cloudflared` 参数断言（`http://127.0.0.1:8443`）· `dashboard/tests/training-multi-course.test.ts`
  断言 `http://127.0.0.1:<hub>` 且不得出现 `//localhost:`。
- **真机复测**：新隧道 `:443`/`:2096` 各 6 发并发 **0 个 502**；带 token `/ping` 两端口 `200`；
  `/relay/dsw/_poll?wait=0` → `{"req": null, "local_port": 8787}`。
- **被否决**：① 换域名 / 换机器 / 配代理（三个都治不了「回源拨错地址」）；② 让 hub 再绑 `::1`
  （能救当下，但把「地址该写谁」这条规矩藏起来，Linux 上 `Bun.serve` 只绑 v4 时还会再犯）；
  ③ 把备用端口扫描改成多路由（扫的是端口是否被掐，路由是另一维，混在一起判词会糊）。
- **根因④（第二现场）**：改完上述三处、DSW 重新部署后仍满屏 502 —— 进程还是那条
  `--url http://localhost:8787`（72 条 `dial tcp [::1]:8787: refused`）。控制台判据
  `edgeReady || 穿隧道 ping` 短路或 ⇒ **edge 在线就复用、从不核对 origin** ⇒ 改对了代码没换进程。
- **裁定（M2，含一次自我否决）**：先定的「origin/选项不等 ⇒ 重启」**当天即被否决回退** —— 那等于
  主动杀掉 edge 健康、正服务全网的隧道去换域名，而 quick tunnel 域名是一次性凭据（CF 不能重新领取
  同一 hostname）⇒ 所有写死 hub url 的 notebook / 节点同时失联且不可逆（用户强烈指令：禁止自作
  主张重启）。最终：`tunnelReuse()` —— **edge 在线一律复用**，漂移只 `warn`；只有 edge 掉线才重建。
- **护栏**：`dashboard/tests/tunnel-reuse-origin.test.ts`（9 例，含「漂移 ⇒ 仍复用」与「复用分支
  `killPid` 只许一次」）。真机：`:443/:2096/:2083` 带 token 200、**0 个 502**。
  全文 → `docs/nn/remote-transport.md` §168。

## §2026-11-20-dashboard-home-multi-course-trends（首页走势图直接多课同图：全局一组对比课 + 显式序列键批量端点）

- **背景**：用户 2026-11-20 要求「首页配置的走势图也直接支持配置多个课程同图展示」——原来只有
  「比较课程」浮窗能多课同图，来回开关太繁琐。本条**取代** `plan/dashboard-compare-trends.plan.md`
  的非目标「不把多课曲线塞进 Hero 的小图」（用户需求优先）。
- **裁定**：
  ① **全局一组对比课**（当前课恒第一色 + 最多 3 门额外课），作用于所有可见走势图；不做每图各配一组——
  色序跨图必须一致，否则同色在不同图代表不同课。
  ② 当前课序列由 `/api/state.metrics.iters` 客户端现算；额外课走 `GET /api/trendSeries?courses=&keys=&from=&to=`，
  按**显式序列键**批量取，复用比较端点的课程级账本缓存（行为指标从缓存行现算，换统计量不重读盘）。
  ③ 范围档位在**每门课自己的序列**上客户端截取，稀疏 eval 保留 NaN 缺口（不连假趋势）；切档位不重新读盘。
  ④ 配置存 `tc.trend.compare = {on, courses}`（只存额外课，顺序 = 色序），走「走势配置」草稿，点「完成」才落盘。
  ⑤ `MultiTrendChart` 扩展 `yBounds` / `label` / `compact` / 受控 `hoverIter`：首页小图用「一课一行」提示框，
  y 轴规则由 `homeYBounds` 单一出处给（cap03/zero/scaled）。
- **被否决**：① 每图各配课程集（颜色身份跨图不一致）；② 把 N 门课塞进 `/api/state`（3s 轮询反复读 N 份账本）；
  ③ 每张图各打一次 `/api/compareTrends`（映射复制、请求数随图数上涨）；④ 服务端做范围截取（切档位重拉，
  且与单课图语义分叉）。
- **补充（同日第二轮用户反馈）**：
  ① 首页小图 hover 提示框改画在**图上方**的 HTML 浮层（`tc-mchart__tip`，`left` 按准星 x 计算后钳回图内），
  不遮折线；大图（比较/放大浮窗）仍用 SVG 内提示框——空间足够且要贴着多条线对齐。
  ② 放大入口恢复为图区**左下角独立按钮**（`TrendZoomButton`，单课格与多课格共用）；多课格放大只画**当前课**
  （同一浮窗），对比课的数在 hover 提示里看；行为指标走统计曲线放大。
  ③ 数据源为「eval」时 hover **只吸附 eval 点**（`nearestEvalIter`：只收 eval 序列的有限值点，NaN 缺口不算点）；
  本图没有有效 eval 点才回退全部 iter。首页多课格、比较浮窗、统计放大浮窗统一走这条。
- **护栏**：`web-home-compare.test.ts`（配置/映射/稀疏截取/合流/颜色/y 下界/`HomeCompareCell` SSR/配置弹窗/
  Hero 源码纪律）· `server-api-compare-trends.test.ts`（`parseTrendSeriesQuery` + `buildTrendSeriesView` +
  共用缓存）· `web-trend-config-modal.test.ts`（复选框计数）· `web-compare-trends.test.ts`（`nearestEvalIter`）·
  `web-style-discipline.test.ts`（首页提示框 `left` 是唯一新增的计算值豁免）· 第三/四轮：`HomeCompareLegend`
  SSR（`aria-pressed`/tooltip 文案）+ `HomeCompareCell.hiddenCourses`（隐藏不画线、格头读数仍在、全隐藏空态）。
- **补充（同日第三/四轮）**：图例项改成按钮，点击把所有可见图上的该课曲线同步摘掉/恢复；`aria-pressed` 与
  删除线+灰字同源。隐藏名单全局按课名、临时（不落盘）：不改选课/不重拉，格头当前课读数照报；配置「完成」清空；
  全隐藏时图区提示恢复。图例并进走势控制条（与「走势配置」同排），说明语只做课程名 tooltip；被否决：每图各存一份、持久化。
  切换当前课时，新当前课若在对比里 ⇒ 把它摘掉、旧当前课补进集合并落盘（集合不缩，唯一额外课时对比不熄灭；不专门保色位）。

## §2026-11-20-goalnn-defense-reward-integrals（守家奖励必须是「累计/积分」形态：g7 的滑动均值与终局存量都学不动）

- **背景**：`g7-base`（c1 的 26 项 + 3 项基地奖励）vs `g7-ctrl`（同起点/面板/形状，去掉那 3 项）跑到 it190：
  通过率 37.29% → base **41.71%** / ctrl 35.57%；基地被毁 385 → **357**（预注册 ≤294 未达）。
  **决定性证据 = 把三项公式反套到两课每局**：base 防守分 58.2→**58.8**、ctrl 58.2→52.5，
  胜局/负局判别度 +114/+25 → **+106/+25**（没变）；毁局 cellsVisited 96.8→92.5（依旧「没去拦」）。
- **决定**：新开两课 `g7-base2` / `g7-ctrl2`（同起点 c1@it740、同面板 classic35、同 24576·1024·4、同 500 轮），
  g7-base2 只把这 4 项基地项换掉，其余 26 项与 c1 逐字相同：
  ① `+ wWallKeep*baseWallIntact`（26，不动，208）；
  ② 压力项 `- wPress*min(pressCap, mean − 0.10)` → **`- wPressInt*min(cap, basePressureSum − 0.10·basePressureSamples)`**（积分形态，216）；
  ③ `- wThShot*min(5, max(0, thShotsAtBaseCum − 3.5))`（14/发 → **50/发**，250）；
  ④ **新增** `- wThLane*min(37.5, max(0, thCostDirect + thCostDirectAimed − 0.006·ticks))`（300）——
     用 metrics **v13 早已落盘、从未进过任何公式**的两列：每决策步「正压在鹰的两轴上、弹道无遮挡」的敌车数。
- **理由**：奖励是势函数 `Φ`，`r = Φ[i+1] − Φ[i]`。**滑动均值**形态下每步 delta ∝ 1/t ⇒ 训练越久梯度越小
  （v13 给 `thCost*` 写的「必须累计」注释就是这个坑）；**终局存量**形态 telescoping 后只剩终局差 ⇒ 与 outcome
  共线，不携带「何时该回防」。改成累计量后每步 delta 恒等于当拍暴露，才是可学的信用分配信号。
  实测把 4 项套回既有 700 局：胜局 p50 **+124** / 毁局 p50 **−196** ⇒ **判别差 +261（中位 +320）**，是 g7 的 2.6×。
- **被否决**：① **BC/监督探针**（用户口径，2026-11-20）：败局语料只知道「这样会失败」，学不会「怎么防住」；
  等 NN 通过率 >70% 再考虑录人类语料做 BC。② 只把 g7 的 `wPress` 系数调大：形态没变，放大的是前期噪声、
  后期仍是 1/t。③ 新增「墙存量时间积分」指标：要动 `export-observations` + metrics 版本 + codehash
  （⇒ 重建 code.zip + 重启 hub，quick tunnel 域名会换、所有节点要改 `hub_url`），而 `thCostDirect` 已够用。
  ④ 四项都按 300 满额堆满：合计 974 = 3.2×wWin，z8 先例（满量程 3×wWin 的线性项把胜率打到 .547→0）⇒
  保留封顶、并新增「送死守卫」（`lives_exhausted ≤ 60/700`）与「龟缩守卫」（`cellsVisited ≥ 110`）。
- **护栏**：`g7-ctrl2` 同时开（同相位对照）；预注册含**机制判据**（毁局 `thCostDirect+aimed` ≤14 且
  `thShotsAtBaseCum` ≤5）——没有它，胜率涨了也分不清守家与面板适应；纯课程侧改动 ⇒ 不动 TS、不动 metrics 版本、
  不改 codehash（**无需重启 hub / 不碰 tunnel**）。
- **起点与命名**：两课都从 **c1@it740** 起（不用 `g7-base.it210`）。依据：it210 的 base **39.43%**
  （it190 的 41.71% 已回落）、ctrl **40.43%**（反超 base 1.0pp）；毁局 `thCostDirect` 在 it0/190/200/210 =
  41.2/43.8/41.5/43.2 全程平 ⇒ 没有可继承的守家技能，"41.7% 当暖启动"是把一个幸运 checkpoint 当资产。
  另外从 it210 起跑会污染对照腿（它已被旧三项奖励训过）并切断与 g7 的同迭代三向比对。命名沿用 `g7-` 前缀
  （`g7-base2` / `g7-ctrl2`，用户 2026-11-20 口径：不急着升序列），在跑的两课不动。
- **全文**：`docs/nn/experiments.md §146`（g7 判决与病因）· `nn-training/curricula/g7-base2.jsonc` 文件头（实付表与判据）。
## §2026-11-20-goalnn-base2-verdict-two-levers（加重惩罚不是解：base2 机制零位移且通过率 −6pp；改从「信用视界 / 采样密度」各开一腿）

- **判决**：`g7-base2`（4 项基地项，判别差 +261）跑到 it120：通过率 33.71% vs `g7-ctrl2` 39.71%
  （同迭代 Δ **−6.0pp**，p=0.019；末 3 点 −4.81pp，CI −8.5~−1.1），**毁局 lane 41.2→41.5、
  对基地开火 6.58→7.05 一步没动**，四项**实付**自己也变差（−90.4→−95.8，ctrl2 −88.0）。
- **关键量化**：四项的**组内 std = 胜局 168 / 毁局 200**，而"胜局 vs 毁局"的全部击杀收入差只有 **173**
  （胜局 20 杀 = 300 恒定，std 0）⇒ 加进去的是**与学习信号等量、且策略不可控**的噪声 ⇒ SNR 腰斩。
  这是"为什么更差"的机制解释，也是"再加项只会更糟"的判据。
- **决定**：同一份奖励不变，各开一条**单旋钮**腿（同起点 c1@it740 / 同面板 classic35 / 同 24576·1024·4）：
  ① `g7-lam98` = 只把 `lam 0.95 → 0.985`（有效视界 1/(1−γλ)：19→59 决策步 ≈ 190→590 拍）——
     验「信号可学但信用视界够不到"出发拦截"」；γ 不动（折扣半衰期 346 步仍长于新视界）。
  ② `g7-hot11` = 只把训练关收窄到 11 个高基毁关（基毁 ≥14/20 且胜 ≥3/20：
     2003/2004/2005/2007/2014/2019/2021/2027/2031/2032/2033），用 `rotation_mode: pinned` +
     `rotation_per_it: 0`，**不动 `stages`** ⇒ `course.stages` 仍 35 关、评估面板 700 局逐局可比。
- **被否决**：① 继续加/加权奖励项（组内 std 已经把信号淹了，加重只会更糟）；② 给 `wWallKeep` 之外
  再挂终局项（终局量 telescoping 后只剩结局代理，与 outcome 共线）；③ 用 `stage_select` 挑关
  （按位置重编号成 2000..2010 ⇒ 评估侧 ≥2011 拿 None，面板静默缩水）；④ 直接动 TS 新增
  「玩家离基地距离 × 基地受威胁」的每拍指标（要改 metrics 版本 + codehash ⇒ 重建 code.zip + 重启 hub，
  quick tunnel 域名会换、所有节点要改 `hub_url`）—— 留到这两腿判死之后再谈。
- **判死条件**：it120 时毁局 lane 仍 ≥40 ⇒ 在现有观测/动作空间下"靠奖励塑形教守家"判死，转 BC/人类语料。
- **全文**：`docs/nn/experiments.md §147` · 课头 `nn-training/curricula/g7-lam98.jsonc` / `g7-hot11.jsonc`。

## §2026-11-20-dashboard-compare-list-global（多课对比列表全局唯一，不随当前课增删）

- **推翻**同日早先 `§2026-11-20-dashboard-home-multi-course-trends` 中「切当前课时把旧当前课补进 courses」
  那一条（`preserveHomeCompareOnCourseSwitch`）。用户 16:0x 报障：两门课刚开到 it1，切课就把彼此
  自动塞进多课对比——对比列表应当是用户显式配的那一份，不是切课副产品。
- **决定**：`courses` 是**全局唯一**一份，只在「走势配置 · 完成」由用户增删；切当前课只改变显示层
  第一条线画谁（`[course, ...compareExtras]` 去重），**不写回配置、不落 localStorage**。
  色位仍按课名固定（`resolveHomeCompareColors`），同课跨图/跨切课不换色。
- **实现**：删掉 `preserveHomeCompareOnCourseSwitch` 与 Hero 的 `prevCourseRef` effect；测试改钉
  「切课不写回 courses / 不自动塞课」。
- **全文**：`dashboard/tests/web-home-compare.test.ts`。

## §2026-11-20-pill-queue-head-not-computing（队首待领 jid 不得读成「PPO·在算」）

- **报障**：两门课 it1 rollout 完、等 PPO worker 接活（worker 未上线），顶部 pill 却写「PPO·在算」。
- **根因**：`courseStatus` 的 `local-inflight` 分支把「训练侧 inflight + hub 无在飞」一律判成
  「本机在算」；job 其实正躺在 hub **队首**等领取，那不是计算。
- **决定**：加 `atQueueHead` 判据——训练侧 jid 命中 hub `nextJob` 时**不走**本机在算，
  落到既有的「PPO·无人取 / PPO·预取」。既有用例（jid 不在队首、含远端队列有活）仍报在算，不动。
- **全文**：`dashboard/tests/training-pills.test.ts`。

## §2026-11-20-home-compare-current-must-be-listed（当前课不在列表 ⇒ 只画当前课）

- **推翻**上一条 `§2026-11-20-dashboard-compare-list-global` 里「当前课在显示层临时并入第一条线」
  那句。用户 16:1x 强口径：当前课程不在多课对比列表里，就只显示当前课程，不要叠对比曲线。
- **决定**：`courses` 是**含当前课**在内的全局叠线列表（最多 6 门）。`compareActive` 增加
  `currentInHomeCompare(config, course)` 判据——当前课未勾进列表 ⇒ 首页退回单课图。
  配置弹窗候选不再排除当前课：它是列表里的普通一员，勾上才叠线，移除释放色位。
  切当前课不改列表（上一条这点保留）。
- **实现**：Hero 不再 `[course, ...compareExtras]`，直接 `courses: compareCourses`（列表原样）；
  `requestExtras` 排除当前课（当前课序列客户端现算，不走端点）；图例 entries 同源于列表。
- **全文**：`dashboard/tests/web-home-compare.test.ts`。
## §2026-11-20-goalnn-dp-unwrap-before-artifacts（产物段必须先剥 DataParallel：`module.` 前缀会让权重 delta 全量拒发）

- **事故**：`g7-lam98` / `g7-hot11` 的 it1 在 `kaggle-c`（**2×T4，cuda-dp**）上训练成功后全量被拒：
  `weights delta：新权重缺 44 个参数（['blocks.0.dw.bias', …]）——拒发（结构不一致）`，`skip (not retried)`，
  训练侧进 30s 一次的 `gate_verdict` 死循环。
- **根因**：`remote/train_core._torch_artifacts` 里 `raw_model = model`，而 `st.model` 在 `cuda-dp` 下
  **已经是被 `torch.nn.DataParallel` 包过的对象**（`_restore` 末尾落的）⇒ `HostModule` 取到的
  `state_dict()` 键全带 `module.` 前缀 ⇒ 与 init 权重（`blocks.0.dw.weight` 等）**零交集** ⇒
  `pack_weights_delta` 判「缺 44 个」。
- **为什么潜伏**：只有 ≥2 卡的节点才走 DP 分支；今天之前跑 PPO 的都是单卡 DSW/T4。**单卡从不触发**。
- **决定**：`worker/ppo/common.unwrap_module()` 剥壳（DP/DDP，最多 8 层），产物段一律用它；
  剥壳只改键名（DP 与底层模块共享参数对象，数值等价）。**不**用「把该节点标成不可跑 PPO」绕过——
  那等于让任何多卡节点都跑不了 PPO，且下次换机器再犯。
- **被否决**：① 关掉节点的 `cuda-dp`（DP 是吞吐旋钮，不是错源；关掉只掩盖 bug）；
  ② 在 `save_weights_json` 里统一 `removeprefix("module.")`（会在真名以 `module.` 开头的模型上误伤，
  且治不了 `_ppo_save` 的 ckpt）；③ 新增一条「源码文本断言」守调用点（`test_source_text_assert_budget`
  只许降不许升，抬格是欠债）⇒ 改为行为测试 + 调用点内联注释点明事故。
- **护栏**：`nn-training/tests/worker/test_dp_artifact_unwrap.py`（剥壳 / 嵌套 / DP 前缀是病灶 /
  `HostModule` 剥壳后键集与数值一致）。
- **部署**：`code.zip` 是**发布时按工作区重打包**的内容寻址快照（加课程 jsonc 都会换 sha）⇒
  **改完不需要重启 hub**，下一轮 publish 自动带上；节点侧 `code_sha` 变了会重新解包。

## §2026-11-20-gate-workers-capped-by-commit-headroom（门禁 worker 数按**提交余量**封顶，余量充足时一个不减）

- **报障**：跑 `bun run pygate` 吃满内存；两个开发环境同时跑会 OOM。要求「不拖慢时钟」地降内存。
- **实测（本机 8 核 / Win / xdist，采样器 5Hz 采全树 PrivateUsage）**：单份门禁峰值提交 **6.58GB**
  / RSS 5.88GB；每 worker 峰值 673~859MB。分层：`--collect-only` 单独就 378MB（每个 worker
  都要收集并 import 全量用例——Windows 无 fork，xdist 只能这样起）；跑起来后每个 worker 再累积
  100~480MB，且**是弥散增长**（逐用例 delta 最大 177MB，没有可摘的大户；`_heapmin` 与
  `gc.collect` 都验证过：这不是「堆没还」，是真留存）。
- **根因**：峰值 ≈ worker 数 × 每 worker 内存。每 worker 的形状改不动（地板是 import、增长弥散），
  **唯一旋钮是 worker 数**。
- **决定**：`nn-python-gate.sh` 在算完核数后，再问一次 `available_commit_bytes()`
  （Windows = `GlobalMemoryStatusEx().ullAvailPageFile`；Linux = `MemAvailable`；macOS = None），
  经 `common.platform_utils.memory_budget_workers(cores, free, per_worker_mb, reserve_mb)` **单向封顶**：
  余量够 ⇒ 原样（**时钟不变**）；不够 ⇒ 装得下几个开几个；探不到 ⇒ 维持核数（fail-open，并打一行说明）。
  预算 1024MB/worker（实测 859MB 向上取整）、预留 1024MB，`NN_GATE_MEM_PER_WORKER_MB` /
  `NN_GATE_MEM_RESERVE_MB` 可按机器重标。
- **被否决**：① 默认就按「给另一个环境留一份」砍半（如 reserve=cores×预算）——余量充足时也慢 12%，
  违背「不拖慢时钟」；② 减少 worker 以外的旋钮：`_heapmin`/`gc.freeze`/Arena 调参全部实测无效
  （见上），改每 worker 形状要先砍 import 地板，做不到；③ 用「torch 池 ‖ 免 torch 池」拆分降内存——
  文件头已实测更慢（33s → 40/41s）。
- **已知边界**：两个环境**同一瞬间**启动仍可能各自取满核数（单份峰值就是 6.5GB）；先起后起的那份
  会看到已被吃掉的余量并自动变窄。跨进程预留文件属于另一个设计点，本次不做。
- **验证**：单跑 `NN_GATE_MEM_PER_WORKER_MB=4096`（模拟内存紧张）⇒ 打印「8 → 3」、峰值提交
  6515 → **3973MB**（-39%）、墙钟 98 → 181s；默认参数下**不封顶**、峰值 6515MB、墙钟与改动前同量级。
  口径守卫：`tests/common/test_platform_utils_cores.py`（纯函数四例）+
  `tests/test_githook_scripts.py::test_gate_caps_workers_by_available_commit`（只许减、fail-open、逃生口）。

## §2026-11-20-gate-worker-growth-is-native-fragmentation（门禁每 worker 那 100~480MB 是原生碎片，不是泄漏）

- **问题**：单个 worker 的提交量从「收集完 378MB」涨到 673~859MB，要找「谁留住了」。
  （上一批收益已落地：门禁 worker 数按提交余量封顶；见上一节。）
- **逐项实测（同一次全量、`-n 8`）**：
  - **Python 对象**：`gc.get_objects()` 两次快照差 ≈ **+60MB/worker**（dict +21 / function +13 /
    type +9 / list +5 …），其余全在原生侧。
  - **存活张量**：`pytest_sessionfinish` 时 `isinstance(o, torch.Tensor)` 计数 **= 0**（total 0MB）
    ⇒ 不是「有人把张量/梯度留在全局里」。
  - **已释放但没还给系统？** `msvcrt._heapmin()` 返回 0、`HeapCompact(GetProcessHeap())` 只回收
    **3.6MB** ⇒ 也不能靠「定时收紧堆」追回。
  - **逐用例 delta**：大全在真跑训练步的用例（AST 扫描类 177MB 那种是瞬时的，跑完就还）。
    文件级终值（全新进程、180MB 地板）：`test_bc_v6_data` **+437MB**、`test_ppo_lazy_loader`
    +294、`bc_epoch_resume` +145、`log_diet` +142、`ppo_common` +138、`rl_model` +123、
    `spatial_leg_smoke` +105、`p0_auc_probe` +102。
  - **成因定位**：一次 64 条的 `forward+backward`（17×26×26 输入）单独就吃掉 ~216MB 瞬时 + ~90MB
    不回退；`DNNL_PRIMITIVE_CACHE_CAPACITY=0` 只省 5MB ⇒ 是 MKL-DNN 卷积侧的 workspace/池与
    堆碎片，env 与 trim 都碰不到，**随「每个 worker 做了多少卷积」线性增长**。
- **决定（唯一还能改的形状：测试自己分配多少）**：把「只验回传字段/形状」的训练类用例的 batch
  缩到语义够用：`test_bc_v6_data`（loader 用例 32→8、train 参数 64→16）、`test_bc_policy_heads`
  与 `test_bc_value_only`（32→8）。**不动任何断言**；同一口径的先例是 `test_bc_epoch_resume.py`
  的「样本减到 1 个 batch，语义断言不变」。实测：`test_bc_v6_data` 文件终值 617 → **396MB**，
  全量门禁峰值提交 6515 → **6300MB**（-3.3%，墙钟 98~123s 噪声内）。
- **被否决**：① 关 `torch.backends.mkldnn`（换卷积实现 = 改数值，KL/CE 的精确断言就废了）；
  ② 按用例 `gc.collect()`（原生内存不受影响，白付墙钟）；③ 继续缩 `test_ppo_lazy_loader`
  （它断言的就是「每批转移次数 = chunks × epochs」，缩大会把判据一起缩）。
- **天花板（写清楚，免得下个人再找）**：全量门禁的每 worker 增长总计只有 ~435MB，测试侧再怎么
  省也超不过这笔；**真正的旋钮是 worker 数**（上一节的提交余量闸），内存闸变窄时每 worker 反而
  会长得更高（实测 3 worker 时单进程 1086MB）。
- **全仓扫描补记（2026-11-20 同批）**：把 `tests/` + `e2e/` 里所有 `batch=` / `batch_size=` 旋钮
  （≥16 的全部逐条看判据）过了一遍，最终只动了 4 个文件 7 处：`test_bc_v6_data`（32→8、64→16）、
  `test_bc_policy_heads` / `test_bc_value_only`（32→8）、`test_bc_xla`（16→8，两条 SPMD 断言里的
  `(16,…)` 同步改成 `(8,…)`——**判据宽度不变**，8 仍满足「批能被 8 核整除」）。
  **有实测理由放过的两处**：`test_shard_split`（`batch_size=64` 只走 `make_loaders`，文件终值
  192MB ≈ torch 地板 180MB，且批构成就是它的断言面）；`test_ppo_lazy_loader`（把 parity 用例的
  `mb` 8→4 只省 41MB：348→307MB，该文件的 348MB 主要是**三个 `PPOStudent` + 一次 `ppo_update`**
  本身，不是 batch；再缩就要缩判据）。
  全量复测：峰值提交 **6515 → 5998MB（-7.9%）**，墙钟 120.9s（基线 123s，噪声内），pytest 只剩
  已知的 `test_commit_journal` RSS 三条偶发红。
- **非 batch 大户扫描（2026-11-20 同批，逐用例**峰值**采样）**：给整棵门禁加了 20ms 采样线程，
  6583 条用例的「峰值增量 / 峰值后留存」全部落表。结论分两类：
  - **可修（已修，Python 侧缓存常驻，与 batch 无关）**：
    ① `tests/test_module_asserts.py::_parse` 挂着 `@cache` ⇒ 650 个生产文件的 **AST 常驻**
    （实测该用例在 worker 里峰值 +189MB 且**一分不还**，整个 worker 底盘 219MB）。改成
    `source_scan.parse`（AST 用完即释放、文本仍走缓存）⇒ 同一条 worker 的占用 **219 → 62MB**
    （峰值 +189 → +29）。这正是 `tests/helpers/source_scan.py` 头部 2026-09-29 记过的净亏损反模式。
    ② `tests/test_forward_names_are_live.py` 的 `_texts/_index/_py_files/_facades` 缓存（全仓
    `.py` ≈30MB 文本 + 标识符索引）在模块跑完后仍留着 ⇒ 加 module 级 autouse fixture 在
    teardown `cache_clear()`：模块内仍共享（速度不变），模块跑完把 **137MB** 还回去
    （该 worker 结束提交量 190 → 53MB）。
  - **看着大但改不动（有实测理由）**：`e2e/test_volume_e2e.py` 九条 124~248MB 峰值，但**留存
    全 ≈0MB**，且它们都是各自 worker 里**第一条**碰 `trainer.dispatch` 全链路的用例（一次性
    import/夹具，8 worker ⇒ 8 份）——缩 quota/帧数只会改掉「几波收敛 / 过冲 ≤1 波」这条判据
    本身。其余留存大户（129/90/81/76/72/71/66/48MB）全是训练类用例的原生 churn（存活张量 = 0、
    `HeapCompact` 只回收 3.6MB 那两条已把「泄漏 / 堆没还」排除，见上一节）。
  - **累计复测**：峰值提交 6515 → 6300 → 5998 → **5949MB（-8.7%）**；墙钟 120~134s（机器噪声带，
    本批的墙钟代价 ~2s：跨用例重解析 650 份源码 ~0.7s/次×3）。
- **`@cache` 全量盘点（2026-11-20 同批）**：把 `tests/` + `e2e/` 里所有 `@cache` / `@lru_cache` 与模块级
  大字典（`_MODULES_CACHE` / `_REPO_CACHE` / 三个图集合…）逐个**单文件跑完量进程提交量**（pytest 地板
  ~30MB）：其余全部落在 **36~60MB**，即比地板多 5~30MB 的「每文件计数小字典」级别——与先前两处
  （AST 189MB、标识符索引 137MB）差一个数量级，**不再动**。
  唯一还在跨模块持有全仓数据的是 `tests/helpers/source_scan` 的文本/事实缓存（7 个扫描模块叠在同一
  worker 里实测结束 123MB，清掉它 72→42MB / 63→48MB，即 **15~30MB**）；但它是**共享提速**缓存，
  按模块清会让后续每个扫描模块重解析 650 份源码（~0.7s/次）——30MB 换 ~7s 墙钟不值，**决定保留**。
- **门槛（写下来供下一个人判断）**：只处理「单个 worker 里 ≥100MB 且纯测试侧」的缓存；十几 MB 级
  一律不动（复杂度与墙钟风险大于收益）。

## §2026-11-20-forensics-rss-probe-must-declare-ctypes-signatures（RSS 探针崩在「别人改了共享 ctypes 签名」上）

- **报障**：`bun run pygate` 里 `tests/worker/test_commit_journal.py` 三条快照用例偶发红
  （`probe_errors={'rss': …}`、`rss=0.0MB`），单跑绿、全量才红，且**同一进程内保质**（同 pid 连红）。
- **现场（全量 + 采样插件抓到的原文）**：
  `ctypes.ArgumentError: argument 1: OverflowError: int too long to convert`
  @ `worker/forensics.py::_rss_mb_windows` 的 `psapi.GetProcessMemoryInfo(handle, …)`。
- **根因**：`ctypes.windll` 是**每进程一份**的 `LibraryLoader`，函数对象也是共享的。
  **paddle**（`paddle/base/core.py:147`）为拿自己的进程句柄写了
  `ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p` —— 这一改在全进程生效。
  此后 `GetCurrentProcess()` 返回的是 `0xFFFFFFFFFFFFFFFF`（不再是未声明 restype 时按 `c_int`
  截断出的 -1），而旧实现把它**直接**塞进未声明 `argtypes` 的 `GetProcessMemoryInfo`（按 `c_int`
  转换）⇒ 溢出。**为什么看着像偶发**：只有先 import 过 paddle 的 worker 中招（`test_bc_paddle` /
  `test_paddle_ppo_*` 与它们同 worker 的那些），且一旦中招就不再恢复。
- **决定**（不是「测试放宽」）：`_rss_mb_windows` 显式声明 `GetProcessMemoryInfo` 的
  `argtypes=[c_void_p, c_void_p, c_ulong]` / `restype=c_bool`，并把伪句柄包成
  `ctypes.c_void_p(handle)` —— 后者对「别人改过 restype」与「没人改过」两种状态都成立
  （`c_void_p(-1)` 与 `c_void_p(0xFFFF…)` 是同一个值）。同族先例：`hub/admin.py:rss_bytes`
  早就在注释里记过这条，「只差谁先把这句话抄到 forensics 里」。
- **为什么不去修 paddle**：那是第三方 wheel 里的进程级副作用，改不动；本仓能守住的是**自己的探针
  不依赖别人的签名状态**（取证是诊断手段，不该被无关库的初始化毒化）。
- **护栏**：`tests/worker/test_commit_journal.py::test_rss_probe_survives_a_foreign_module_rewriting_kernel32`
  —— 用 monkeypatch 把 `GetCurrentProcess.restype` 改成 `HANDLE`（与 paddle 同款），断言
  `snapshot()` 仍有真读数且不带 `probe_errors['rss']`。改前**确定性红**（报错原文与现场逐字一致），
  改后绿；全量门禁 0 failed（103s），带采样插件的全量里**一条探针失败痕迹都没有**。
## §2026-11-20-eval-round-spec（P0-3 的参数对象：装载值可以收成一个对象，装配点只能有一个）

- **背景**：`python.refactor.md` §P0-3 里最大的宽签名 = `worker/eval_arbiter.EvalRound.__init__`
  的 **40 个关键字参数**。这些值本来就住在派发器配置与 `_EvalRun` 里，构造时又被摊开一次 ⇒
  加一个装载值要改四处，漏一处就是运行期 `AttributeError`。
- **决定**：① `RoundSpec`（frozen + kw_only）是唯一装载对象，`EvalRound(spec)` 只收它；轮内可变
  状态仍由 `__init__` 现建（执行层读的 `rnd.total` / `rnd.pending` 一个没变）。② 装配点唯一 =
  `trainer/eval_dispatch._round_spec`（值住哪儿就哪儿装配，不在 `eval_arbiter` 里再摊一次）。
  ③ 注入回调按形状声明（`RowBuilder`/`LogFn`/`MetaOkHook`/`FailedMetaHook`），`Any` 21 → 16。
  ④ 同文件 `EvalRound.settle_task` 的 9 个结果位（含从没人读的 `wall_sec`）→ `SettleOutcome`，
  死参直接删。⇒ `worker/eval_arbiter.py` 宽签名 **2 → 0**、全仓 132 → 130。
- **被否决**：① 把 `EvalRound` 自己做成 `@dataclass`（40 字段全在类体）—— 综合出的 `__init__`
  不是 AST 里的 `FunctionDef`，形状门禁数不到它 = **让门禁看不见代码**；② `for f in fields(spec):
  setattr(...)` 复制 —— mypy 推不出属性类型，要么补 40 行类级注解要么全仓退成 `Any`；
  ③ 再按语义切三个小对象 —— `rnd.eval_dir` 这类读点 230 处，改路径 = 改执行层契约；
  ④ 保留 `wall_sec`「以后可能有人读」—— 死参就是死参。
- **护栏**：`tests/worker/test_eval_round_spec.py`（6 条：单对象构造 / 字段表冻结 / frozen+kw_only /
  状态现建 / 装配点唯一 / 生产调用只传一个位置参数；后两条对 HEAD 实测红：旧 40 参、旧调用
  `kw=40`）。`WIDE_FUNCS_BASELINE` 删条目（不是留 0）、`any_baseline` 21 → 16。
- **全文**：`docs/nn/engineering.md §229`。
- **§2026-11-20-batch-unit-ledger**：`trainer/batch_runner.py` 拆出 `trainer/batch_lanes.py`（通道机器
  `_UnitLanes` / `_UnitPlan` / 两个背压常量），`BatchEvalRunner._settle_unit` 的 15 个位置参数 →
  **一个 `UnitLedger`**（frozen + kw_only，装配点 `_UnitLanes.ledger(threads=…)`）。
- **为什么先拆再收**：`batch_runner.py` 的 LOC 是 **999/1000**（G-LOC 天花板），加数据类即超预算 ——
  与 `worker/iter_rollout.py` 同一前置条件。机器（状态 + 主循环）与收尾本来就该分住：搬家后
  `batch_runner` LOC 336 / `batch_lanes` 694，全仓宽签名 **130 → 129**。
- **代价 = patch 锚点换家**，按「谁裸名读、谁必须绑定」重分：`_record_agent_meta` /
  `run_local_eval_game` / `BUSY_BACKOFF_CAP_SEC` / `STUCK_GRACE_SEC` 的锚点改为
  `trainer.batch_lanes`；`log` **两家都绑**（收尾行在 runner、通道行在 lanes）；`bun_version` 不动
  （只有 `_open_unit` 读）。守卫**改锚点不删守卫**，并新增两条反向断言：`br.STUCK_GRACE_SEC` /
  `bl.bun_version` 这种「名字在、没人读」的交叉转发也红（patch 打哪家都可能空转）。
- **被否决**：① 把 `UnitLedger` 塞进 999 行文件（超预算）；② 让 `batch_lanes` 反向 import
  `trainer.batch_runner` 取 `log`（成环 + patch 锚点歧义）—— 改 `LaneOwner` Protocol；③ `_settle_unit`
  收一个裸 `dict`（丢类型与 frozen，形状门禁失明）。
- **顺手修的守卫盲区**（同族陷阱第 4 次：换方法 → 换类 → **换模块**）：`test_batch_plan_split` 的入边
  归属者表已扫三个模块（否则执行侧两条入边静默退化成 4/6）；wver 实参 / 三个行构造守卫 /
  `GATE_SITES` 全部改读新家。基线按实测簿记（G4 6→3+3 · G7 2→1+1 · G17 30→7+23 · G5 3→1 ·
  G19 SOURCE 1210→1221，性质未变）。
- **全文**：`docs/nn/engineering.md §230`。

## §2026-11-20-run-setup-spec（`RunSetup`：三个宽签名收成一个旋钮袋，`device` 缺省按入口取）

- **背景**：`python.refactor.md` §7 的「下一批」——`remote/plan_handoff.open_run_context`(35)、
  `remote/run_loop.run_standalone`(29)、`remote/plan_run.run_plan_job`(25) 与 `RunContext.__init__`(29)
  是同一条链上的四个宽签名（装配点 + 两条入口 + 状态对象）。已有 `PublishSpec` / `TrainSpec` /
  `RoundSpec` / `UnitLedger` 的先例。
- **决定**：引入 `remote/plan_handoff.RunSetup`（frozen + kw_only，37 字段）：
  - 三条入口都只收它（`run_plan_job` 另收上一份产物 `first_result`）；
  - **除身份位外全部带缺省**——它是**旋钮袋**，不是 `RoundSpec` 那种全必填（各调用方只传自己那几簇，
    `run_standalone` 最小形态 = `RunSetup(artifacts_dir=…)`）；
  - 规范化（`int` / `float` / 放大倍数 / 轮换 / `Path`）从 `RunContext.__init__` 上移到
    `RunSetup.__post_init__`（frozen ⇒ `object.__setattr__`），`RunContext.__init__` 只剩 4 参；
  - 身份位（plan / manifest / job_dir / work_dir）由入口回填，`_require_run_identity` 是**唯一**校验点。
- **`device` 的缺省不统一**：`RunSetup.device = None` 表「按入口取缺省」，因为两条腿历史缺省不同
  （`run_standalone` = `"cuda"`、装配点 = `"cpu"`）。悄悄统一 = 一条腿突然不用 GPU 了。
- **顺手删死参**：`run_plan_job(job_id=…)` 函数体 0 读点（AST 实测）。`first_result` 留在形参
  （它是上一段的产物，不是旋钮）。
- **账**：全仓宽签名 129 → 125（`WIDE_FUNCS_BASELINE` 三条删除）；`Any` 基线三处下调
  （11→9 / 10→8 / 3→2）；同名 ≥3 模块 142 → 143（已在 `tests/test_reuse_ledger.py` 记账：门面转发同型）。
- **否决**：① 只收 `RunContext.__init__` 不动两个入口（调用点仍是 33 个 kwarg，等于没减宽）；
  ② 加一层 builder（`dataclasses.replace` 已经是「只覆盖几个旋钮」的语义）。
- **守卫**：`tests/remote/test_run_setup_spec.py`（字段表闭集 / frozen + kw_only / 三条入口形参表 /
  身份守卫恰一处 / 两条入口都走装配点 / `RunContext.__init__` 恰 5 参）。

## §2026-11-20-rescan-spec（全仓最宽的 `rescan_nodes`(27 参) → `RescanSpec`；字段是引用不是快照）

- **背景**：`trainer/queue_local.rescan_nodes` 是全仓最宽的签名（27 参，其中 19 个位置参数），
  它的 docstring 自己记着两次事故：实参逐位对应，第 19 个错位过一次 ⇒ 线程启动即抛 ⇒
  `rollout-rescan` 报废 ⇒ 运行中上线的节点永远不被发现（症状只有「新节点不干活」）。
  唯一调用点是一串 27 项的 `kwargs=dict(...)`。
- **决定**：`RescanSpec`（frozen + kw_only，27 字段）住定义点 `trainer/queue_local.py`；
  `rescan_nodes(spec)` 只收一个对象，函数体逐字保留（顶部解包）；装配点
  `trainer/queue_worker` 改成 `kwargs=dict(spec=RescanSpec(...))`（仍全关键字，错位只可能落在一个键）。
- **为什么抄 `QueueState` 的字段表而不是直接收 `q`**：`queue_worker` → `queue_local` 是单行道，
  反向 import `QueueState` 就是环。类型照抄其注解（`lock: threading.Lock`、`all_settled:
  threading.Event`、`iter_id: str`），`Any` 一位没加。
- **frozen 不等于快照**：`spawned_ids` / `alive` / `streaks` / `extra_threads` 与主循环**共享**同一份
  容器（rescan 要登记新节点、补孵线程）——这一点写进类 docstring。
- **账**：全仓宽签名 125 → 124（`WIDE_FUNCS_BASELINE` 的 `trainer/queue_local.py` 3 → 2）；
  LOC 756/1000；`Any` / exact / clone / 同名四条基线未动。
- **守卫**：`tests/trainer/test_rescan_spec.py`（字段闭集 / frozen + kw_only / 形参恰 `["spec"]` /
  唯一调用点按关键字传 `spec`，探针 `tests/helpers/run_setup_probe.thread_kwarg_keys`）。

## §2026-11-20-job-exec-spec（`_execute_job`(14 参) → `JobExecSpec`：线程调用点只收位置参数）

- **背景**：push 模式下每个 job 由 `Runtime.thread(_execute_job, …)` 起线程，而 `Runtime.thread(...)`
  只收位置参数（写关键字会语法错误）⇒ 调用点逐位传 11 个实参。这与 §232 的 `rescan_nodes`（27 参 /
  19 位置，第 19 个错位过一次）是同一条失效面。
- **决定**：`JobExecSpec`（frozen + kw_only，14 字段）住 `remote/worker_server.py`；
  `_execute_job(spec)` 只收一个对象（函数体逐字保留 + 顶部解包；正文要重新绑定 `log`，故解包成
  `spec_log`）。生产调用点 `thread(_execute_job, JobExecSpec(...))`；两处直接调用的测试同样构造 spec。
- **账**：全仓宽签名 124 → 123（`WIDE_FUNCS_BASELINE` 删 `remote/worker_server.py`）；
  `Any` 未加（`log: Callable[[str], None]`）。
- **守卫**：`tests/remote/test_job_exec_spec.py`（字段闭集 / frozen + kw_only / 形参恰 `["spec"]` /
  线程调用点位置实参形状恰 `["JobExecSpec_call"]`）。
- **坑**：`tests/test_subproc_util.py` 的「起真服务进程」守卫按字面量 `"remote.worker_server",` 认人，
  只读形状的守卫文件必须把它写成模块常量（否则被误判成 spawn 了服务）。

## §2026-11-20-log-prefix-context-propagation（`contextvars` 前缀在派生线程里会丢：`Runtime` 三个入口派生时拷上下文）

- **背景**：`common/log.py` 的前缀/课 sink 已在 2026-11-13 收进 `LogRouter`（`ContextVar` +
  实例级 sink），`common/**` 的模块级可变容器已清零（`L0_MUTABLE_ALLOWED = {}`）。但 `ContextVar`
  **不自动进新线程** ⇒ `prefix_scope(课)` 里派生的线程读到空前缀，`[课]` 标签静默消失
  （旧实现是进程级字符串，那些行反而带标签）。
- **决定**：在 `common.runtime.Runtime.thread()` / `submit()` / `map()` **派生那一刻**
  `contextvars.copy_context()`，用 `ctx.run(partial(...))` 执行（`Context.run` 不收 kwargs）。
  语义按拷贝：子线程读得到父上下文，写回不去。
- **不做**：`rt.pool(...)` 返回 stdlib `ThreadPoolExecutor`，绕过 `submit()`/`map()` 直接投的任务
  不传播（`concurrent.futures` 无钩子；包一层要换返回类型，1 处生产调用不值）——写进 docstring。
- **守卫**：`tests/common/test_log_router.py` 四条（跨线程隔离 / `Runtime.thread` 继承 /
  `submit`+`map` 继承 / sink 实例隔离）。
- **账**：五条基线（`Any`/宽签名/同名/exact/clone）与文本断言基线均未动。

## §2026-11-20-upgrade-branch-latch（`UPGRADE_BRANCH` 的 `global` 换成 `BranchLatch`；`net_http` 的 opener 缓存故意不对象化）

- **背景**：`python.refactor.md` P0-5 表剩两行，复核后发现对象**已不存在**：`net_http._RACING_OPENERS`
  在 B8 换成 `@functools.cache racing_opener(no_proxy)`；`distribution` 的 `_RESTART_SEEN` /
  `UPGRADE_BRANCH` 2026-11-14 对象化、2026-11-19 整簇搬去 `remote/node_upgrade.py`（`fea6cded`）。
- **决定一**：`remote/node_upgrade.UPGRADE_BRANCH`（`str | None` + `global`）→ `BranchLatch` 对象
  （`_BRANCH` 默认实例，`set()`/`get()`，`None` 入口收成 `""`）。公开函数签名不变，4 个消费者零改动；
  `GLOBAL_BASELINE` 删 `"remote/node_upgrade.py": 1`。
- **决定二**：`net_http` 的两个 `functools.cache` opener **不对象化**——它们无状态（`build_opener`
  结果不随环境变），缓存只省构造；包成类只增间接。审计原话「测试间串味」已不成立（键是入参）。
- **守卫**：`tests/remote/test_node_upgrade.py::test_branch_latches_are_independent_and_default_to_empty`；
  既有 `test_dist_upgrade_cli` 守「`current_upgrade_branch()` 零参 / `upgrade_branch_or` 不复活」。
- **不动**：`worker/eval_exec._EXECUTORS`、`worker/eval_arbiter._ARBITER`、`hub/http_face` 类属性、
  `biz/reward_context` 的 2 条 `global`（各自一轮独立重构）。

## §2026-11-20-eval-cell-ownership（eval 线程池与仲裁器的单例改成 `Cell` 持有；`retire=` 放构造参数）

- **背景**：`worker/eval_exec._EXECUTORS`（`global` ×2 + 锁外惰性判断）与
  `worker/eval_arbiter._ARBITER`（`global` 换实例）是两个 L2 进程级单例，`GLOBAL_BASELINE` 靠
  按文件记账守。进程里确实只有一份，问题在形状：写点不封闭、惰性判断在锁外、换实例无归属。
- **决定**：新增 `common/cell.py::Cell`（`get()` / `replace()`，值在构造时就造好），两个模块各留
  一个持有者；公开入口 `eval_executors()` / `eval_arbiter()` / `reset_eval_*()` 签名不变。
  `GLOBAL_BASELINE` 删 `worker/eval_exec.py: 2`、`worker/eval_arbiter.py: 1`。
- **不做惰性构造**：带锁版本要么让 `test_lock_reentrancy` 的目标面棘轮 +1（锁内调注入构造器），
  要么丢 exactly-once（锁外调，第二份被丢弃=旧 bug 变体）。构造廉价无副作用 ⇒ 模块加载时造。
- **退休钩子放构造参数** `retire=`：放 `replace(old, close=…)` 会让两处 `reset_*` 变成形状相同的
  三行，`test_reuse_ledger` 的结构克隆基线 17 → 18。声明在拥有值的那一行，只写一次。
- **守卫**：`tests/common/test_cell.py` 五条（同对象直到 replace / 退休旧值只一次 / 无 retire 时
  只丢引用 / 两个 Cell 互不相见）。
## §2026-11-20-goalnn-base-defense-structural-verdict（三条杠杆都零位移 ⇒ "靠奖励塑形教守家"判死；行为不存在，加权/放宽视界/加密度都不解决）

- **判决**：`g7-lam98`（λ 0.95→0.985，有效视界 190→590 拍）与 `g7-hot11`（11 关 pinned，每关密度 ×3.2）
  各跑到 it101/103：**毁局 `thCostDirect+aimed` 43.2 / 45.4**（起点 41.2，预注册 ≤30，判死线 ≥40 **提前触发**）、
  毁局对基地开火 6.72（≤5.5 未达）、胜率与参照腿无差异（Δ −2.7~+3.9pp，p 0.13–0.91）。
- **三条互不相关的杠杆、同一个零结果**：奖励形态与量级（3 项 → 4 项，判别差 +261）→ lane 41.2→41.5；
  信用视界（λ）→ 43.2；采样密度（11 关 ×3.2）→ 45.4。λ 的副作用按预期出现（kl 0.0073 vs 0.0054，+35%，
  远低于 0.02 停线）⇒ 视界确实变了，只是变了也没用。
- **结构性证据**：四项基地"实付"在**没有任何基地项的 `g7-ctrl2` 上也是 −89.7**，与拿它当奖励的两腿
  （−77.5 / −88.7）同量级 ⇒ 这四个数主要是 outcome 代理，不是策略可控量。**策略在 100 轮里从未产生
  "提前回防"这一行为**；PPO 只能放大已出现的行为 ⇒ 加权、放宽视界、提高密度都不改变它。
- **决定**：停止"靠奖励塑形教守家"这条线（不再加第 4 个旋钮）。四个 checkpoint × 四腿的读数与脚本
  （`tmp/g7-4leg-compare.py`）留档，供以后任何"再试试塑形"的提议直接引用。
- **被否决**：① 再加一个奖励项/再调一次权重（组内 std 168–200 已与学习信号等量，见 §…base2 条）；
  ② 用人类语料做 BC（用户口径：等通过率 >70% 再录；且败局语料只教"会失败"）；
  ③ 现在就去改 TS 新增「玩家离基地距离 × 基地受威胁」的每拍指标（动 metrics 版本 + codehash
  ⇒ 重建 code.zip + 重启 hub；quick tunnel 域名会换）—— 留作被点名的下一刀，不是顺手做。
- **留下的方向**（下一次真要动守家时从这里挑）：**God-AI 教师引导**（AGENTS §0.2「教师只帮起步」允许；
  拿 God 在"基地受威胁"局面跑出的轨迹做 BC/DAgger 预训练，把"回防"动作塞进策略支撑集 —— 这与"录人类语料"
  是两件事，不需要等 >70%）。
- **全文**：`docs/nn/experiments.md §148`。
## §2026-11-20-goalnn-early-signal-snr（换早信号前先算 SNR：现有指标里没有能预测胜负的"早"信号；g8 用 breach 赌时序 + 新鲜对照）

- **背景**：§148 追查发现威胁出现时玩家在 20 格外、direct→被毁只剩 380 拍，环墙早 900 拍挨打
  ⇒ 拟把主项从 `thCostDirect` 换成 `thCostBreach`。
- **先算再换**：单变量 SNR（it0，700 局）= 判别差 / 平均 std。结果 **direct −0.98 · wall-lost −0.84 ·
  shots −0.70 · press −0.24 · breach −0.05**。胜局环墙总量 57.4 vs 毁局 59.5 —— **breach 几乎不预测胜负**。
  早信号里没有"又早又可分"的项；换轴是**拿判别力换提前量**（四项合计判别差 +261 → +179）。
- **决定**：仍做这一刀（用户口径"先按 1"），但把它写成**明确的赌**：
  `g8-breach`（④ 换 breach 轴，24/unit、免罚 0.012/拍、封顶 6 units = 144；①②③ 不动）
  + `g8-ctrl`（与 g7-ctrl2 逐字相同的无基地项对照，**这次要新鲜对照**：ctrl2 是单卡跑的，
  现在 PPO 在 2×T4 `cuda-dp` 上，且 ctrl2 自己末段漂 8pp）。
- **被否决**：① 第二个槽位也开早信号变体（同一注押两次，且没有对照就无法归因）；
  ② 不设封顶让 breach 敞口定价（bd p90 108 units × 24 ≈ 2600，z8 的量级事故）；
  ③ 直接把早/晚两项叠加（组内 std 已经 180+，叠加只是加噪声）。
- **判死线**（it120）：毁局 breach 均值仍 ≥55 ⇒ "换早信号"判死，转 option 2（新增「玩家离基地距离 ×
  基地受威胁」的每拍指标，动 codehash + 重启 hub）或 God-AI 教师。
- **全文**：`docs/nn/experiments.md §149`（含 SNR 表）。
## §2026-11-20-goalnn-four-axis-ladder（把 direct 与 directAimed 拆开定价：加总的打法是稀释，四轴阶梯 SNR 1.68）

- **背景**：`g8-breach` / `g8-ctrl` 立课后用户改口径：改名 **`g7-breach`**、**删掉对照**、
  新开 **`g7-def`**，要求"把 thBreach / thBreachAimed / thDirect / thDirectAimed 都用上"。
- **发现**：四条轴拆开看差别极大（it0，700 局，SNR = 判别/平均 std）：
  `Breach +0.03 · BreachAimed −0.15 · Direct −0.62 · **DirectAimed −1.41**`。
  胜局 `DirectAimed` 中位 **0**、毁局 **17**。旧课用 `Direct + DirectAimed` **加总** ⇒ 强轴被弱轴稀释。
- **决定**：`g7-def` 把四轴**分开定价**成阶梯（越接近致命越贵）：
  免罚带 0.008 / 0.003 / 0.0015 / 0.0005 per tick，cap = 毁局 p90（43 / 25 / 35 / 26），
  权重 0.93 / 2.0 / 2.6 / 4.5 ⇒ 满额 **40 / 50 / 91 / 117**（块合计 298，每项 < wKill 300）。
  四轴块 SNR **1.68**；含 ①②③ 后判别 +233、整条 Φ 判别 **+590**（三个设计里最强）。
- **形态**：只能用 metrics v13 的**累计成本流** `thCost*`（idx85–88）。`thBreach/thDirect`（idx79–82）
  是每拍瞬时值，写进 Φ 会在进入/离开跳一次、持续暴露期间每步 r 恒 0（v13 注释里记着的坑）。
- **被否决**：① 继续用 `Direct+DirectAimed` 加总（今天证伪）；② 只把 breach 一项上价（SNR −0.05，
  等于加噪）；③ 保留 `g8-ctrl`（用户：没意义——机制项本身就是判据，胜率只作旁证）。
- **判死线**（it120）：毁局 `DirectAimed` 均值仍 ≥15（起点 17.1）⇒ 四轴阶梯也判死，
  转 God-AI 教师或新增「玩家离基地距离 × 基地受威胁」的每拍指标（动 codehash + 重启 hub）。
- **全文**：`docs/nn/experiments.md §150`。
## §2026-11-20-goalnn-def-ent-exploration（g7-breach 删除 + g7-def-ent 立课：只剩"探索不够"这个假设没被否）

- **背景**：`g7-def`（四轴阶梯）立好后，`g7-breach` 成为它的**严格子集**（块 SNR −0.05 vs 1.68、
  端到端判别 +536 vs +590）。用户 2026-11-20 22:04 口径：**删掉 g7-breach**，本轮只跑 def + ent。
- **决定**：新开 **`g7-def-ent`** = `g7-def` 的奖励**逐字不动**，只把 `ppo_schedule[].ent_coef`
  从引擎缺省 **0.01 提到 0.03**（3×）。两门同开 ⇒ **单旋钮 A/B**：谁在机制项上动，就知道瓶颈在哪一侧。
- **理由**：前四次塑形（3 项 / 4 项 / λ0.985 / 11 关密度）全部零位移；§148 追查证明"回防"动作
  在 rollout 里几乎没出现（威胁时玩家在 20 格外、71% 的对局不回防）。PPO 只能放大已出现的行为
  ⇒ "探索不够"是唯一还没被否掉的假设，且它是**课程一行配置**，代价最低。
- **被否决**：① 保留 g7-breach 当消融（只解释功劳归属，不产能力，占 4 小时槽位）；
  ② 同时改 ent_coef + 奖励（两变量混杂，谁动都说不清）；③ 直接上 God-AI 教师/新指标（先让最便宜的一刀跑完）。
- **判死线**（it120）：两门在 `DirectAimed` 上配对差 <1pp 且都 ≥15 ⇒ 探索也不是卡点，
  转 DECISIONS §2026-11-20-goalnn-base-defense-structural-verdict 里留的两条路。
- **全文**：`docs/nn/experiments.md §151`。
## §2026-11-21-goalnn-five-shapings-flat（五次塑形全部零位移：敌方状态定价这条路关闭，只剩教师或新指标）

- **判决**：`g7-def`（四轴阶梯，Φ 判别 +590）与 `g7-def-ent`（同奖励 + ent_coef 0.03）各跑到 it340：
  **机制项按速率看是平的** —— 毁局 `DirectAimed`/千拍 4.35 → 4.04~4.35、早轴/千拍 15.15 → 15.0~15.2
  （总量上升全是"毁局变长 3926→4300 拍"的伪影）。预注册 ≤10 在 35 个 checkpoint 上一次没到（最低 15.3）。
- **两面新证据**：① **判别力 ≠ 可学**：四轴阶梯把"奖励对结局的判别力"抬到 +590（历史最高），机制照旧不动
  ⇒ 判别力是判读量，不是可学性；② **探索不是卡点**：`def-ent` 熵 0.53→**0.82**（旋钮生效），
  机制不动、胜率还略低（配对 Δ def−def-ent **+1.90pp**，26/35 点为正）⇒ 加成噪声。
- **44.29% 的弧线怎么读**：`def` it120 比起点高 7pp，且**逐关普遍**（22/35、净 +49 局、p=0.016）⇒ 不是几关的抖动；
  但① 同刻机制零位移、② it340 反向普遍退化（8/35、净 −30、p=0.036）、③ 无基地项的 ctrl2 自己摆过 35.6–43.7%
  ⇒ 判为**权重轨迹慢振荡**，不是学到的守家能力。判读纪律：机制不动时，胜率弧线只作潜力旁证。
- **副作用**：cellsVisited 121.4 → 105.7 / 111.8（毁局 96.8 → 82.9 / 77.3）、`def` 末点 k/千 2.17 破线
  ⇒ 惩罚让策略变保守，不是回防。
- **决定**：停两课。"给敌方状态定价"关闭，剩两条路 —— **God-AI 教师 BC**（AGENTS §0.2 允许教师帮起步，
  不动 codehash）或 **新增「玩家离基地距离 × 基地受威胁」每拍指标**（动 metrics 版本 + codehash + 重启 hub）。
- **全文**：`docs/nn/experiments.md §152`。
## §2026-11-21-goalnn-god-bc-v1（教师 BC 起步课 g8-god-bc：init=it120、三路 DAgger、60 epoch 每 5 轮择优）

- **背景**：五次奖励塑形全部零位移（§2026-11-21-goalnn-five-shapings-flat）⇒ 转"把行为灌进去"。
  教师实测（0★）：God 78.57% / kills 17.96 vs NN 13.15 / 四轴暴露 63–96% / **到鹰中位 13.0 格（更远）**、
  威胁帧 20.8 vs 40.5 帧/局 ⇒ 教师教的是**更早更快清场**，不是"回防"（§153）。
- **决定**：开 `g8-god-bc`（`kind: bc`）：
  · **init = `g7-def.it120`**（每一条面板指标都 ≥ it740：44.29% vs 37.29%、基毁 347 vs 385、kills 14.35 vs 13.15；
    而 it740 是钢基面板产物，§148 证明它把"远离基地"训成了习惯）
  · **语料三路**（全 `export-dagger-labels --action-v6`）：dagger-120（学生=it120）/ dagger-740（更弱 ⇒ 危机帧更密）/
    dagger-hot（Δ(God−NN) 最大十关 × seed 6-21）
  · `epochs 60 / lr 1e-4 / batch 256 / value_coef 0 / mirror_p 0.5`；`eval.every_epochs 5 × games_per_stage 6`
    （种子 1..6，**不用正式面板挑 ckpt**，避免选择偏差）
- **为什么不是四路**：教师自跑那一路（`collect-godai.ts` / `export-godai-labels.ts`）只写 legacy 2 头
  （move5+fire2），与 `action_v6` 不同构；要用得先给 `export-godai-labels.ts` 补 `--action-v6`（留 v2）。
- **附带修复**：`collect-godai.ts` 补 `--player-level`（`hard.playerStartLevel = 1` ⇒ 之前只能采 1★，
  与 0★ 面板不一致；同 God 局评估踩过的坑）。
- **预注册**：(i) 全池 60 seed ≥ **44.29%**（超过 init；跌破 37.29% 判崩）；(ii) **kills/千拍 ≥3.0**（init 2.65 / 教师 3.29）；
  (iii) 毁局 `DirectAimed`/千 < **4.35**（历史最低 3.93）。判死后先降 lr 到 5e-5 / 加 dagger-740 比例，**别先加语料**。
- **全文**：`nn-training/curricula/g8-god-bc.bc.jsonc` 头部与尾部。
## §2026-11-21-goalnn-bc-shard-name-collision（BC shard 目录名必须带路前缀：发布端按目录名去重，580→370）

- **事故**：`g8-god-bc` 的三路语料都用采集器默认目录名 `dagger_s{N}_seed{M}` ⇒ 发布端
  `iter_bc_shard_dirs` 按 **目录名** 去重（同名保留 manifest 最早一份）⇒ **580 局被砍成 370**
  （dagger-120 剩 71 / dagger-740 剩 139 / dagger-hot 160）。"用弱学生补危机帧"的意图被削掉大半。
- **修法**：三路各加前缀 —— `dag120_` / `dag740_` / `dagh_`（`sub.name` 改名，幂等，已做 580 个）。
  校验：`iter_bc_shard_dirs(..., data_root=..., name_pattern="*_s*_seed*")` 现在返回 **580**（210/210/160），
  样本 453,671，`data_fp = 01e33b899190f97b…`（改名会改 fp，**改完必须重新校验再开课**）。
  `tmp/g8-god-bc-collect.sh` 末尾已固化这一步，未来重采不会再踩。
- **附带的两条现场事实**（用户排查）：
  ① 课程只写 `corpus.games_per_stage: 0` 而漏 `source: "fixed"` ⇒ 走默认 collect：`round_seeds()` 空 ⇒ todo 空
     ⇒ `bc_collect.py:110` 误判"语料已齐"⇒ `bc_loop.py:300` 用 `bc_s*_seed*` 扫不到 `dagger_s0_seed*` ⇒
     抛「无完整 BC shard」⇒ 重试 5 次 abort，hub 队列里一个 job 都没有。**fixed 源必须显式写 `source`**。
  ② serve 在检测到课程文件变化后会"自动复活"，但**复用原 runtime/引擎**（内存里的旧配置）⇒ 改了 JSON 必须
     **停课 → 重新开课**强制重建 runtime，否则照旧报 0 shard。
- **全文**：`nn-training/curricula/g8-god-bc.bc.jsonc` 头部（三路 shard 前缀约束 + data_fp）。

## §2026-11-20-p05-closure（P0-5 债表结账：表行补正、`global` 计数改为实测 11 处、`common/**` 之外另列清单）

- **背景**：要求「把 `hub/http_face` 的三个类属性搬进显式对象」—— 2026-11-19（B7 前半，§217/§218）
  已经是 `HubState`（六字段 + `tests/hub/test_hub_state.py` 两条守卫）。§P0-5 的表行与本章
  第 47 行互相矛盾，且这是第三轮撞上陈旧行，故把「补代码」改成「结账」。
- **决定一（记录）**：审计表 P0-5 整张标已清零；补正两处数字——类属性是**五份**（含守卫抓到的
  `_REJECT_WARN_AT`）、`global` 实测 **11 处**（原记 14；`common/**` 0 处，`biz/reward_context` ×2 按
  裁定保留）。
- **决定二（不动）**：`common/**` 之外还有 15 处模块级可变容器（连接池 / 活 link 表 / 推送缓存 /
  一次性告警集合 / resume 扫描缓存 / 身份账本 / XLA 与吞吐探针 / rollout 源复用索引 等），
  收成对象要先定「谁拥有、何时清、跨进程怎么复现」，与 §236 同量级 ⇒ 只在 `python.refactor.md`
  §P0-5 末小节列清单，不顺手改。
- **决定三（留给下一轮）**：是否把 G2（`L0_MUTABLE_ALLOWED`）扩到 L2/L3，需要单独决策——会引入
  15 条只许变小的基线，其中 `remote/tailscale_boot._BEST_RATE` 受零依赖约束不可动。

## §2026-11-21-control-pool-object（`_CONTROL_CONNS` → `ControlPool`；三档关闭时机是本轮的正题）

- **背景**：`remote/control_pool.py` 的 `_CONTROL_CONNS` 是 §237 盘点的 15 处模块级可变容器里
  后果最重的一处 —— 连接是有 fd 的资源，而旧形状（模块 dict + 四个模块函数）答不出「谁负责关」。
- **决定一**：`ControlPool` 持池，`request` / `drop` / `close` / `count` 挂对象；模块只留默认实例
  `_POOL` + 四个薄包装 ⇒ `remote/http.py` 的调用点零改动。
- **决定二（关闭时机，三档刻意不同）**：① 失败即作废并 `close()`（fd 立刻还回去，旧实现靠 refcount
  隐式回收）；② `close()`/`reset()` 整池关闭，**只在没有在飞请求时调**（池跨线程共享，关别人的连接
  会把正常请求变成异常）；③ 进程退出不挂 `atexit`（会打断正在退出的在飞请求，fd 交 OS）。
  `close()` 失败静默跳过（作废本身不该变成新异常）。
- **决定三**：生产路径不调 `reset()`；用例隔离优先自建 `ControlPool()`，走真链路的用例仍用
  `reset()`（唯一能碰到默认池的缝，比给 `http._request` 加 `pool=` 参数便宜）。
- **门禁教训**：类体单行注解赋值 `self._conns: dict[...] = {}` 会与 `hub/store_wire._init_wire`
  撞结构克隆（17→18）⇒ 类型声明放类体（同 `WireMeterMixin._lock`）、`__init__` 里只赋值。
  旧替身 `_FakeConn` 没有 `close()` ⇒ 关闭路径必须对缺 `close` 的对象宽容。

## §2026-11-21-live-links-registry（`_LIVE_LINKS` → `LiveLinks`；锁外调 `stop()` 是硬约束）

- **背景**：`remote/rollout_relay._LIVE_LINKS` 记本进程活着的 relay 轮询链（cell 重跑清场靠它）。
  旧 `if self in _LIVE_LINKS: remove` 在并发送尾时会 `ValueError`，且用例只能替换模块变量。
- **决定一**：`LiveLinks`（`add` / `discard` / `snapshot` / `count`，一把锁只护登记）+ 默认实例
  `_LIVE` + `live_links()` 读面；`RelayLink.start()` 自登记、`stop()` 自注销。`discard` 幂等。
- **决定二（锁纪律）**：`stop_stale_links` **先 `snapshot()`、在锁外调 `link.stop()`**——`stop()`
  会回头 `discard(self)`，持锁调它就是非可重入锁自锁（`test_lock_reentrancy` 的形状）。写进 docstring。
- **决定三**：登记项类型用 Protocol（`_LiveLink`: `alive()` / `stop()`），用例替身无需是 `RelayLink`；
  容器仍用 `list`（`snapshot()` 保序，日志/断言都按登记顺序读）。
- **不动**：`alive()` 判据、`stop()` 的整体超时预算、`start()`「先清场再起线程」的顺序（2026-11-18
  DSW 事故的结论）。
- **守卫**：登记表幂等与实例隔离 / `start()` 自登记 + `stop()` 自注销；既有 cell 重跑用例改为注入
  `LiveLinks([stale, fresh])`，判据不变。

## §2026-11-21-warn-once（三份「一次性告警」集合 → `WarnOnce`；刻意不给 `clear()`、不加锁）

- **背景**：`trainer/loop_transport._RETIRED_SOURCE_NOTED` / `_RESTORE_UNSUPPORTED_NOTED` 与
  `worker/gate_halt._WARNED` 是三份同形状的模块级 set，调用点把「查」与「加」拆成两步。
- **决定一**：`common/warn_once.py::WarnOnce`，`mark(key) -> bool`（首次真）+ `seen(key)`（只查）
  + `__len__`；三个调用点各留一个实例，判据与文案逐字不变。
- **决定二**：**不给 `clear()`** —— 重来就换新桶（同 `HubState` / `LogRouter` 的用例隔离规约）；
  也避免 `clear` 跨过 `test_reuse_ledger` 的同名 ≥3 模块线（实测会 143 → 144）。
- **决定三**：**不加锁**（对比 §239 的 `LiveLinks`）：只 `add` 不 `remove`，最坏多打一行日志；
  加锁反而要回答「锁内能否调日志」。
- **顺序语义**：`gate_halt` 必须「先查文件存在、再 `mark`」——不存在的路径不得烧 key（已写进
  docstring + 用例 `len(seen) == 1`）。
- **守卫**：`tests/common/test_warn_once.py` 3 条；`tests/worker/test_gate_halt.py` 的 `seen`
  参数类型从 `set[str]` 改为 `WarnOnce`。

## §2026-11-21-resume-scan-cache（`_SCAN_CACHE` → `ScanCache`；失效时机两条并排列出）

- **背景**：`worker/resume.completed_pairs` 的热路径缓存住模块级 dict 里，失效策略一半在 key
  （目录签名）一半在调用点的 `if len(...) >= MAX: clear()`。
- **决定一**：`ScanCache`（`lookup` / `remember` / `forget_all` / `__len__`，`max_entries=`），
  类头写明**两条失效时机**：签名变 ⇒ 新 key；条目满 ⇒ **整表清空**（不是 LRU——命中率只由最近
  2 秒问过的同组 key 决定，淘汰越简单越可预测）。生产写者仍只有 `completed_pairs`，读面 `scan_cache()`。
- **决定二**：方法名不叫 `get` / `put` / `clear`：`lookup` 说明是记忆化查询、`remember` /
  `forget_all` 各自说明语义；顺带避开同名 ≥3 模块判据（首版 `put`/`clear` 会把 143 推到 145）。
- **决定三**：不加锁（与旧实现一致）：只 lookup/remember，dict 单次读写原子，同 key 并发写的值相同。
- **守卫**：`tests/worker/test_scan_cache.py` 4 条（未记住落空 / 满则整表清空 / forget_all 与上限 /
  实例互不相见）；`e2e/test_run_rl.py` 改用 `scan_cache().forget_all()`，判据不变。

## §2026-11-21-http-state-objects（`remote/http` 的节流表与懒建 opener 收成对象；「转发名是快照」的例外随之消失）

- **背景**：`remote/http.py` 有两份进程级状态：`_POLL_WARN_AT`（非 200 告警节流 dict，窗口是调用点
  里的 `<= 60.0` 字面量）与 `_opener`（`global _opener` 懒建单例）。后者的形状逼出一个妥协：
  `tests/remote/test_http_split.py` 的 `REBOUND_NAMES` 让 `_opener` 只查「名字在」不断言 `is`
  ——转发名会停在 import 那一刻。
- **决定一**：`WarnThrottle(window_sec=WARN_THROTTLE_SEC)`（`allow(key, now)` / `forget_all` / `__len__`）。
  窗口变**构造参数**，于是「每条都放行」（0 秒）与「一分钟一条」都是数据而不是调用点字面量；
  `allow` 一步完成「查 + 记」，不再靠两行代码的自觉。不加锁（与旧 dict 一致：最坏两条线程各打一行，
  比在日志路径持锁便宜）。
- **决定二**：`ProxyOpeners`（`get()` 懒建一次 / `reset()` 是「下次重建」的唯一入口）。env 只在
  首次 `get()` 定型这条旧语义**逐字保留**（Colab 2026-09-16 实测），因此重建必须显式 `reset()`，
  不做「env 变了自动重建」。
- **决定三**：模块内部也走访问器（`warn_throttle()` / `openers()`），取用路径只有一条 ——
  用例替换公开名字即可接管，新增用例不必对私有名打桩（`test_job_identity_collision` 的两处
  `monkeypatch.setattr(worker_mod, "_POLL_WARN_AT", {})` 因此改成替换 `http.warn_throttle`，
  私有打桩基线 3 → 1）。
- **决定四**：`worker.py` 删掉两条旧转发，改转发 `WARN_THROTTLE_SEC` / `WarnThrottle` /
  `ProxyOpeners` / `openers` / `warn_throttle`；`GLOBAL_BASELINE` 的 `remote/http.py: 1` 归零，
  `MOVED_NAMES` 恢复对全部名字的 `is` 恒等断言（`REBOUND_NAMES` 删除）。
- **守卫**：`tests/remote/test_http_state.py` 6 条（懒建一次 / `reset()` 重认 env（读
  `ProxyHandler.proxies` 的行为判据）/ 默认持有者一份 / 窗口内一次 + 跨窗放行 + 不同 key 互不影响 /
  0 秒窗口 + `forget_all` / 缺省 60s）；`test_http_split.py` 新增「零 `global`」「顶层零可变容器字面量」；
  `test_worker_state_contract.py` 的三个宿主清单改口径（`_OPENERS`/`_WARNS` 进实例表并标 `INTERNAL_ONLY`）。
- **行为差异**：无（键、窗口、文案、env 定型时机逐字未变）。详见 `docs/nn/engineering.md` §242。

## §2026-11-21-rate-reference（`remote/wire` 的参照速率 + 迟滞计数收成一个 `RateReference`；清场必须两半一起清）

- **背景**：坏签判据的参照是 `_BEST_RATE` 与 `_stale_samples` 两个 `global` 重绑的模块标量。两者
  同生共死却分开存放：旧夹具只重绑 `_BEST_RATE = 0.0`，迟滞计数留在原地 ⇒ 下一个用例可能带着
  「已经慢了几个」开局；重绑也让 `worker._BEST_RATE` 成为 import 那一刻的快照。
- **决定一**：`RateReference`（`sample(rate, nbytes)` / `level()` / `floor()` / `clear_samples()`），
  默认实例 `_RATE` + 读面 `rate_reference()`；`_note_rate` / `_ref_rate` / `best_rate` / `_min_rate`
  保留为薄包装（被 `remote/http` 与 `remote/worker` 直用/转发）。语义（阈值、衰减、迟滞、地板）
  逐字未变；`floor()` 把 `max(WIRE_MIN_RATE, 参照/4)` 的名字从函数体里提出来。
- **决定二**：`stale_samples` / `decay` 是构造参数，缺省取模块常量 ⇒ 「每个慢样本都衰减」与
  「永不衰减」都是数据而不是去改常量。
- **决定三**：方法名避开高频名。首版 `note` / `forget_all` 把同名 ≥3 模块计数推到 145（基线 143），
  改成 `sample` / `clear_samples`（全仓唯一）。
- **守卫**：新增 `tests/remote/test_rate_reference.py` 7 条（小 body 不采样 / 单个慢样本不覆盖 /
  连乘但下限是本次样本 / 恢复瞬时且迟滞清零 / 地板 / 清场两半一起清 / 旋钮是构造参数）；
  `test_wire_split` 的注入点用例改为对象恒等 + 公开 `sample()`；`GLOBAL_BASELINE` 的
  `remote/wire.py` 归零。详见 `docs/nn/engineering.md` §243。
