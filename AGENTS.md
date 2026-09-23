# AGENTS.md — Battle City Web

> **Operating manual.** 规则只留指令；why / 事故史 / 配方 / 枚举明细 → **`docs/agents.details.md` 同号 §**。
> creed → `MANIFEST.md` · 决策索引 → `DECISIONS.md` · 调参日志 → `docs/*.progress.md` · 计划 → `plan/`。
> 旧版 35K，注入时在 ~9.2K 字符处截断（§5 之后全丢 = 规矩只被读一半）；**看不到 §17 ⇒ 这是截断副本，去读文件**。

## 0. The One-Sentence Mission

> Open the browser, play for five minutes, leave with a smile. (MANIFEST §1) — 拿不准时它说了算。

### 0.2 训练目标最高纪律 — God 不是天花板

- **四条禁令**（不得用教师表现做）：① 判关卡「还有没有可学的」 ② 定/评 DoD 阈值 ③ 评关卡难度或优化目标 ④ 设计难度梯度。
- 关卡难度**唯一口径 = 人类熟练玩家体验**；NN 胜率低 ⇒ **训练侧问题，不降难度**；「超越教师」是起点不是终点，教师胜率**不是天花板**。
- **任务侧不可动**（命数/敌数/关卡形态/终局标准）· **训练侧可动**（奖励塑形/GAE λ/value 头/算法）。

## 1. Read These Before Writing Any Code

`MANIFEST.md`（§13「三关」终审）→ `DECISIONS.md`（只扩展不违背）→ 活跃 plan（`plan/mvp.md` §10 的 MVP DoD 对每个改动都成立；
`plan/Snapshot-Management-Framework.md`、`plan/presentation-upgrade.md` 的 DoD = 验收标准）→ 本文件。
plan 与 MANIFEST 冲突 ⇒ MANIFEST 赢，先记进 DECISIONS 再动手。`docs/presentation-audit.md` 只读方法不读事实。

## 2. Architecture Invariants — Non-Negotiable

违反即 bug，**测试绿也算**（含义 + 灰区豁免 → details §2）。

- **2.1 One Author** — 只有 `Simulation` 可改 `World`，其余只读。
- **2.2 No Hidden State** — 玩法状态只住 `World`，不进单例 / 模块变量 / 闭包。
- **2.3 Determinism** — 固定步长；随机只走 `world.rng`；Simulation 禁 `Math.random()`；输入可录 ⇒ 回放逐帧一致。
- **2.4 Data Over Code** — 坦克/关卡/主题/难度 = `src/config/` 数据行；加坦克 = 加一行，不硬编码实体行为。
- **2.5 Presentation Is Disposable** — 粒子/相机/动画不进 `World`；`PresentationLayer.reset()` 重建。
- **2.6 Zero-Asset** — 精灵手写 SVG 经 `SpriteCache` 预光栅化；音效 Web Audio 合成；位图只能扩展不能替换。
- **2.7 Three Gates** — 更好玩 + 架构更简 + 原作精神，三者齐才收（MANIFEST §13）。

## 3. Repository Map

- **分层**：`src/game/` 仿真（`World` 唯一写者）· `src/ai/` · `src/presentation/` 只读渲染/UI · `src/config/` 数据行 ·
  `src/{snapshot,replay,audio,utils,assets,perf}/` 支撑 · `tests/` 镜像关注点（目录树 → details §3）。
- **canvas 只画战场**（416×416，DPR 走 offscreen）；HUD/菜单/浮层是 HTML/CSS，不得搬回 canvas。**坦克精灵朝上**（渲染按方向旋转）；`genId()` 是实体 ID 唯一来源。
- **`dashboard/` = 训练控制台**（独立 bun 项目，:8900）：只 import 各目录 `index.ts` 桶；仓库内**唯一**允许跨项目 import 的边界
  （只读消费 `src/` 契约与 `tools/agent/codehash-files`），反向禁止；自带 `node_modules` + 入库 `bun.lock`（门禁见 §9）。

## 4. Development Workflow — Executing a Plan Autonomously

1. **Decode**：交付物、触及的不变式（§2）、约束它的 DECISIONS 条目；规格含糊 ⇒ 取三关（§2.7）+ MANIFEST §12。
2. **Audit before build**：有审计文档先读；非平凡重构无文档 ⇒ 先在 `docs/` 写现状/目标短记。
3. **Implement** 守 §5 并持续跑门禁（§9）→ **Verify** plan DoD + MVP DoD（plan/mvp.md §10）：能跑 · 无 TS/运行期错 · 60 FPS · 能整合 · 可重启 · 无隐藏状态。
4. **Record** 决策进 `DECISIONS.md`（§6）+ memory 一行（§11）→ **Hand off green**：`bun run check` 过才交付。
5. **调试重跑只用失败子集**（DECISIONS §120）：`bun tools/diag/run-forensics.ts --from-json <corpus>`；只有语料变了才全量。

## 5. Code Conventions

### Hard rules (NEVER)

- **Never `git stash`** —— 任何子命令/flag（两次毁过对象库）；A/B 用 `git worktree add --detach <dir> HEAD`。
- **永不 `git push`**（推是人的事）· git 路径不含 `..` · 不给 git 命令接 `2>/dev/null` · 不用 `||` 串两个 git 写 · 改名/删除用 `mv`/`rm`（**非** `git mv`/`git rm`）。
- **Never start the dev server / 开浏览器**验证改动（验证 = 门禁；UI 例外 → details §5.5）。
- **训练操作唯一入口 = 控制台**（`bun run dashboard` :8900）或同源 API；绕过 = 漏课程开关 / 暂停解禁 / hub 三件套。
- **训练 backend 无参可调**：PPO 恒为「发布 → 等 worker 认领」；本机算在控制台起本机 worker，零 worker 就等，**永不**就地降级。
- **起 NN 训练禁裸 `python`**：无头 → `bun dashboard/src/launch/cli.ts --script <x>.py`；日常组件管理 → 控制台。
- **NN 训练变更/评估/教训 → `docs/nn/*.md` 顶部**（`## §<该文件当前最大号+1>`；索引 `docs/nn.progress.md`）；改架构前先读该档。
- **提交走临时消息文件** `git commit -F tmp/<ascii-file>`，完成后 `git log -1 --pretty=fuller` 核对（`-m` 会静默失败）。
- **未跟踪的 `*.md` 不得 `git add`**（也禁 `git add -A` / `git add .`）；只提交已跟踪的与人点名的那份。
- **长任务不 sleep 等待**：后台跑 + 干别的活；必须等时用有界 marker-grep 循环（命中即退）。

### Language & tooling

- TS `strict`（编译器是评审：禁 `any` / `@ts-ignore` 消音）· Bun 一把梭 · Vite target `es2020` · 只用 oxlint + oxfmt。
- **nn python / pytest 一律 `bash tools/githook/nn-py-safe.sh …`；严禁裸 `python -m pytest`**（删除保护沙箱拦 python 内的删/写 ⇒ pytest 静默挂死）。60s/用例上限，`-m pytest` 另套 480s 外墙钟。

### Commands (canonical)

```
bun run check   # green 的定义（只判根项目；组成见 §9 / details §5.2）
bun run build   # oxlint && tsc && vite build（shipping gate）
bun run test    # 按本地 git 改动跑，只打印失败  |  typecheck / lint / format / dev(:8956) / setup(装 hook)
bun run freeze:check  # God-AI det 签名 vs golden（~4s）红 ⇒ 新纪元三件套（§6.3b）；freeze:l2 可达性审计
cd dashboard && bun install && bun run typecheck && bun run test   |   bun run dashboard（:8900）
```

- `bun run test` 何时跑/跳、`HEAVY_TESTS` 名单的判据与复测法 → details §5.3。
- `bun test` 必带 `--parallel --timeout=50000`；位置参数是子串过滤，排除目录只能用 `--path-ignore-patterns`。

### Style & placement

- 能写成函数就别写类；玩法状态不用单例；`utils/` 优先纯函数（Simulation 方法可改自己拥有的 World）。
- 影响玩法的随机只走 seeded `world.rng`；`Math.random()` 只在回灌不到 World 的表现层；保持包小，新依赖要在 `DECISIONS.md` 论证。
- 玩法 → `src/game/` · 视觉 → `src/presentation/`（除类型外不得 import `src/game/`）· 内容 → `src/config/` ·
  精灵 → `src/assets/sprites/*.svg` + 注册 `SPRITE_URLS` · 测试 → `tests/` 镜像关注点。

## 6. When in Doubt — Derive, Then Execute; Recording Is the Exception

- **6.1 识别**：plan 没定的设计点 / 两种合理实现没挑 / plan 与 MANIFEST 冲突 / 未定的可调值。
- **6.2 推导优先级**：MANIFEST → DECISIONS 先例 → 现有代码一致性 → FC 原作性 → plan 的理由。
- **6.3 只记过三问的**（有被否决备选 · 未来会重犯 · 不能就近表达）；其余由注释 / 测试断言 / commit 承担（模板 → details §6.3）。
- **6.3b God-AI 行为改动 = 新纪元三件套**：新 DECISIONS 条目 + 60-seed 三难度基线（eval-suite v7，`hard` 为主）+ golden 更新；`hard` 上调参，≥60 seed 才下结论。
- **6.4 Execute**：按决策实现；中途发现错了补一条带日期的说明再继续，绝不静默偏离。
- **6.5 只有这四类才问人**：MANIFEST 禁的 One-Author 破例 · 新运行时依赖/构建工具 · plan 没想过的公开手感变化 · 删改既无测试覆盖又无审计文档的系统；其余自己定并执行。

## 7. Bug-Fix Workflow — Reproduce With a Test Before You Fix

**没写失败测试就不算修好**：① `tests/` 里写最小确定性失败用例（固定 seed、无 `Math.random()`/墙钟；codec 用独立重实现），并在未改动代码上确认红 →
② 只做让它转绿的最小改动（顺手重构另开任务）→ ③ 新用例绿且 `bun run check` 绿。

## 8. Testing Conventions

`bun:test`；测试住 `tests/` 并按关注点镜像；codec/数据优先**独立重实现**对账（`tests/stages.test.ts` 抓得住 golden 测试漏的解码器回归）；
单测不开 DOM，除非被测系统要（`Simulation`/`World`/`TileMap`/`SnapshotManager` 纯逻辑，无头测）；快照/恢复用例断言**全字段**；
新增消耗 RNG 的逻辑 ⇒ 补「同 seed 两遍、World 逐字段相同」用例。

## 9. Quality Gates — Definition of Done

（多数由 pre-commit / `bun run check` 自动判定，失败指引见门禁输出 → details §9）

- [ ] `bun run check` 绿（只判根项目）· 动过 `dashboard/**` ⇒ `cd dashboard && bun run typecheck && bun run test` 也绿 · `bun run build` 过
      （动过 `dashboard/src/web/**` ⇒ `bun dashboard/src/server/build.ts` 三份 bundle 也过）。
- [ ] 无新增 Simulation `Math.random()` / 模块级可变玩法状态 / canvas UI（§2.3/§2.2/§2.5）。
- [ ] plan DoD 满足 · 新决策已记（§6）· bugfix 有复现用例（§7）· 60 FPS · 内存有界（保留策略 → plan/Snapshot-Management-Framework.md）。

## 10. Asset Pipeline

SVG 手写于 `src/assets/sprites/`（96×96 viewBox，坦克朝上），注册进 `src/assets/sprites/index.ts` → `SPRITE_URLS`；改生成器用 `node tools/gen-sprites.mjs` 重建整库。
消费链固定 `SpriteLibrary` → `SpriteCache` → `SpriteArtist`/`GameRenderer`，**不得**在渲染循环里绕开缓存 inline 加载（地形无缝 / 主题色 → details §10）。

## 11. Memory & Continuity

干完实质工作，追加 `.workbuddy/memory/YYYY-MM-DD.md`（append-only，缺则建）+ 持久约定进 `MEMORY.md`：做了什么 · 决策理由（链 DECISIONS）· 坑 · 下一步。它是补充，不替代交付物与回复。

## 12. Quick Reference

常量 / 坦克种类 / 地形字符 / 游戏状态的单一来源 = `src/constants.ts` + `src/config/` + `src/types.ts`（数值一览 → details §12）。**代码与本文冲突 ⇒ 代码赢**（改本文）。

## 13. The Rule Behind All the Rules

> Simple beats clever. Readable in six months is worth more than elegant today. (MANIFEST §10)

本文件 vs 直觉 ⇒ 本文件赢；vs MANIFEST ⇒ MANIFEST 赢；都没说 ⇒ 选让**游戏更小、架构更干净、玩家更开心**的，再记进 `DECISIONS.md`。

## 14. Performance Anti-Patterns — Hot-Path Rules

违反即 bug（测试绿也算）：调参跑上千局无头仿真，每 tick 的分配与冗余扫描被放大百万倍（示例 → details §14）。
**14.1** 每 tick 不分配数组 · **14.2** 热路径返回值不分配对象 · **14.3** `.sort()` 前判空 · **14.4** 地形保持字符串（改数字查表实测 -28%）·
**14.5** 不链 `.filter()`+`.sort()` · **14.6** `World.allTanks` 取一次传引用。

## 15. Closed-loop corpus discipline (RL / CMA-ES / sweep)

任何「评估 → 更新 → 再评估」的环都在自优化评估集：不轮换语料，分数涨而能力不涨（案例 → details §15）
**15.1** 每轮换语料：第 *it* 轮 `(stage, seed)` 不得与更早轮重复（抽 key 带 `runSeed, it`，resume-safe）；重磨固定集 = 记忆化，指标作废 ·
**15.2** 微语料是哨兵不是判决 · **15.3** 部署模式单独评估（采样 ≠ greedy；不同权重 60/60 相同对局 = 坍缩指纹）·
**15.4** 每次 update 必读 kl / entropy / value / gnorm（大批次是 KL 稳定器）· **15.5** 改语料/课程/奖励语义 = 新实验：新 `--out`/`--traj` + 一条 DECISIONS，**不得跨改动续跑** ·
**15.6** RL 默认 stream 模式，`--stream 0` 写理由且保持 stream 路径有人跑。

## 16. Long-Run Task Discipline — Budget, Parallelism & Observability

（配方与事故 → details §16）**16.1** >5min 的跑先在**真语料**量 1–2 epoch/batch 再外推 · **16.2** 日志一律进文件，禁 `| tail`/管道 ·
**16.3** 每 epoch/batch 打一行（loss/metric/elapsed），产权重的跑每 N epoch 存 checkpoint · **16.4** kill/等由测量定（跨 ~20s 两次 CPU 时间）·
**16.5** 可切分任务一律并行（分片 / 池 / `--parallel` / `-n` → details §16.5），串行写理由 · **16.6** 并行与串行逐字节对账 ·
**16.7** 日志只捕获一次、绿了删（`tools/githook/run-logged.sh <log> -- <cmd>`；`KEEP_LOG=1` 留证据）。

## 17. Editing Files on Windows — Text-Splicing Discipline

（案例与模板 → details §17）**17.1** 多 hunk 改动写脚本 + 逐 hunk `assert old.count(...) == expected`，全命中才写盘 ·
**17.2** hunk 文本不进 shell；超一行用文件工具写临时 `.py`，跑完删 · **17.3** 用托管 runtime 的绝对 python 路径（别写裸 `./.venv`）；脚本放仓库内 —— 原生 Python 的 `/tmp` = `D:\tmp` ·
**17.4** 写完验证落盘（语法 / 构建 / grep 锚点）· **17.5** 多行 hunk 锚唯一上下文，不替换会重复的裸行 · **17.6** 输出 CJK 重定向到文件或用 §17.1 通道显式
`encoding='utf-8'`（控制台是 gb2312；`PYTHONIOENCODING` 已用户级设好，别再 reconfigure）· **17.7** 只 `pwsh`，禁裸 `powershell`（DECISIONS §323）。
