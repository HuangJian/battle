# EvalBoard × Phase-0 逐敌种画像 —— 现状/目标与分期（2026-09-19）

> 立项背景：T5 判决（分敌种信用）的主端点是「power 曝光归一命中/千 tick」，需要
> `hitsByKind`/`killsByKind`/`exposureByKind`/`firstHitKind`/`firstKillKind`/`killOrder`/
> `killerKinds` 七列；而 EvalBoard（B 层）账本里**没有**这些列 —— grep
> `hitsByKind` 在 `nn-training/rl/**` 与 `dashboard/src/evalboard/**` = 0 命中。
> 用户 2026-09-19 拍板「中方案」：把 Phase-0 列接进账本，并让判决批走 B 层
> （控制台/趋势/长期留存），而不是每来一个判决就临时造一个 JSONL。
>
> 本文是**施工说明**（现状→目标→分期→不变量），非判决依据；口径类结论落 DECISIONS。

## 1. 现状（写路径与账本，均已核实到行）

逐局账本 = `dashboard/data/evalboard/games/YYYY-MM.jsonl`（append-only，唯一事实来源，
`dashboard/data/` 整树 gitignore + 人工备份纪律，见该目录 README）。

| 层 | 驱动器 | 语料 | 逐局行产出点 |
|---|---|---|---|
| A | `rl/eval_dispatch.py::EvalDispatcher`（`eval_a_once.py` 手动同口径） | 课程 `eval_stages` + `eval_games_per_stage` + `a_eval_seed_list`（双轨锚点 50 + 轮转 50 / `EVAL_SEEDS[:n]`） | `record()`（`eval_dispatch.py:326`）→ `tmp/<course>/eval_log.jsonl` |
| B/C | `rl/batch_eval.py::BatchEvalRunner` | `dashboard/src/evalboard/ladder.json` rungs + 段 `seg=k%16`（`seed0OfSegment`），100 局/段 | unit 内 row 字典（`batch_eval.py:663`）→ 同 eval_log |
| m1 | `tools/sim/m1-eval.ts` → `rl/eval_ingest.py::write_m1_game_rows` | godai scorecard | `eval_ingest.m1_game_row`（`:41`） |

入库：`dashboard/src/evalboard/ingest.ts::ingestEvalRow` 把 raw 行映射成
`store.ts::EvalGameRow`（schema 1）→ `appendRow`。确定性口径由
`GAMEPLAY_FIELDS`（§3.4 双跑逐字节一致字段集）与 `REQUIRED_FIELDS`/`coverageReport`
（P0 覆盖率 100% 或明示豁免）承载。

关键事实：**报告层已经有这七列** —— `tools/sim/export-eval-game.ts` 顶层自
2026-09-19 起携带（阶段 0 census），`_eval_report.json` 与节点 pack manifest 同源，
`rl/eval_local.py::run_local_eval_game` 直接返回该报告 dict ⇒ 节点局与本机局都已具备，
缺的只是「row 字典 → ingest → schema」这条搬运链。

## 2. 目标

1. **P1（本批）**：Phase-0 七列贯通 A/B/C/m1 四条写路径 → `eval_log.jsonl` →
   `ingest.ts` → `EvalGameRow`，进 `GAMEPLAY_FIELDS`（确定性字段，双跑必须一致）。
2. **P2**：B 层支持**判决批**（verdict batch）：语料 = 课程关卡文件 stages[]（自定义关
   stage id 2000+i，与 B 层同基址）× 池外 seed 段 × **多 ckpt**（N 权重同批同种子配对），
   落 `games/`，可在控制台看、可长期留存。
3. **P3**：判决工具（`tools/sim/paired-pd.ts` / `phase0-fingerprints.ts`）支持从 store
   读（或 store→JSONL 导出），不再要求调用方先跑一次临时 eval-course-ckpt JSONL。

## 3. 设计要点与分叉（P2/P3；用户 2026-09-19 已拍板：新增语料注册表 + 新增判决批类型，
判决工具读 store 的适配层留 P3）

- **语料注册**：`ladder.json` 的 rungs 语义是「arena 阶梯几何 + 段推进」，把课程关卡塞进去
  会污染阶梯语义 ⇒ 倾向**新增语料注册表**（`corpora.jsonc`）或让 `requests.jsonl` 直接
  引用关卡文件路径 + seed 段。
- **多 ckpt 批**：现有批键 = `(course, rung_from, ckpt)`（一批一权重）。改造它影响
  A/B/C 全链与去重键 ⇒ 倾向**新增批类型**（新 `trigger: 'verdict'` + `ckpts[]`），
  不动现有键空间与去重语义。
- **判决工具读 store**：store 行的字段名与 `EvalCourseRow`（JSONL）不同（`ckpt_sha16`
  vs `label`、`stage_id` + `rung` vs `stageId`/`stageName`、`win/cleared` 同语义）。
  倾向新增一个薄适配层（store row → EvalCourseRow 形状）供现有工具直接吃。
- **种子空间**：`ingest.ts` 现在把非 860001-860100 的种子一律标 `probe0`
  （`seedSpace`），池外判决段（400600+）落 `probe0` ⇒ 与既有 `probe0` 行**同空间可比**，
  但 `probe_key` 含 `rung/难度/maxTicks/mapHash/seedSpace`，判决语料需自成一类
  `rung`（或 `probe_key` 分量）以免与历史 probe 行混算（§3.5 禁止跨界相减由
  `assertComparable` 兜底）。

## 4. 不变量（三期都不许破）

- **不改任务侧口径**：本工作只搬列/搬语料，不动关卡、命数、max_ticks、判据阈值。
- **不伪造数据**：旧报告/旧行缺键 = `None`/零，进覆盖率豁免清单（`PHASE0_FIELDS`），
  绝不补编造值（沿用 ingest.ts 既有纪律）。
- **确定性契约**：Phase-0 列是 gameplay 遥测，属 `GAMEPLAY_FIELDS`；`node/elapsedSec/
  phase/ts` 之外的差异即违约。
- **EvalStore 只在 `dashboard/data/evalboard`**（§3.1），判决临时产物不得写进 store。
- **顺序不可变**：P1 先落（P2 的落账要带 Phase-0 才成立）。

## 5. 实施与验证

- **P1 已落地（2026-09-19）**：`eval_census_fields` 单源 + 四写点接线 + ingest/store schema +
  `PHASE0_FIELDS` 豁免清单。证据：真实 `_eval_report.json` 七列齐全可抽；
  `nn-python-gate` 1263 绿 · dashboard typecheck + 513 绿 · 根 `bun run check` 1875 绿。
  口径记录：DECISIONS §2026-09-19-evalboard-phase0-census。
- P1（原计划）：`rl/eval_local.py::eval_census_fields`（单源，仿 `eval_loot_fields`）+ 4 个写点接线；
  `ingest.ts` 映射；`store.ts` schema/字段集。验证：`nn-training` pytest（新
  `test_eval_census_fields.py`）+ `dashboard` 套件（新 ingest 映射用例）+ 根 `bun run check`。
- **P2 已落地（2026-09-19，用户拍板「中方案」：新建语料注册表 + 新增判决批类型）**：
  见下节。P3（判决工具读 store 的适配层）未做 —— 判决工具当前仍吃自己的 JSONL。

## 6. P2 实施记录：判决批（verdict batch）

判决 = **语料（课程关卡文件 stages[] × 池外 seed 段）× N 个 ckpt 同批同种子配对**。
三段职责（两侧镜像，改一侧必须同步另一侧）：

| 环节 | 落点 | 契约 |
|---|---|---|
| 语料声明 | `dashboard/src/evalboard/corpora.json` + `corpora.ts`（读/校验/身份派生） | `{id, level, seed0, games_per_stage, policy?}`；坏行**响亮失败**（静默跳过 = 判决跑在空语料上而读数看似正常） |
| 入队 | `verdict-cli.ts` → `requests.jsonl`（`kind='verdict'`，`corpus` + `ckpts[]`） | console/CLI **只写请求**，台账由 runner 单写（§5.3 写者唯一性）；同键（语料 + ckpt **标签序**，顺序敏感）不重复入队 |
| 物化 + 执行 | `rl/batch_eval.py::{consume_requests, plan_verdict_units, units_for_batch, maybe_dispatch_batch}` · `dashboard/src/evalboard/{batches,requests}.ts` · `kick-once.py` | 批 = `kind='verdict'`，`course/rung_from/ckpt` 空串（**不**用假 course 骗旧读方的键，键空间靠 `kind` 分离）；unit = 一个 (ckpt × 关卡)，**权重在 unit 上**（批次级无权重）；unit 自带 `stageJson`（紧凑 separators，与 `eval-course-ckpt.ts` 同形，否则同关在 agent 侧裂成两个缓存键） |

语料登记：`v-ladder-c03-p400600` = `ladder-c03` × `seed0=400600` × 200 局/关
= 4 关 × 200 = 800 局（T5 判决池外段；与训练池 860001-860200 及已用池外段
400000/400200 不相交，§15.1 轮转纪律）。

**逐局配对**：每个 ckpt 在每个关跑**同一批种子**（`seed0 + 0..games_per_stage-1`）
⇒ 跨 ckpt 天然逐局配对，不需要事后求交集（§3.5④）。行内以 `wver`（全量 sha）
区分权重、`rung='<corpus id>#<stage name>'` 自成一类 probe 键（不与历史 probe 行混算）。

**God 批的一处既有硬伤（本批顺带修）**：agent 的 `/v1/task` **一律**按 `(kind, wver)`
查缓存桶（`weightsOf` 是全量 sha 精确查表，无 god 豁免），而 god 局原先不 POST 权重、
且 wver 传的是 12 位 `key16` ⇒ 节点必然 409。现在 god 也 POST 占位 `{}` 并把它的 sha
当 wver（`key16` 仍是行身份/断点续跑键，未变）。

**冒烟证据（2026-09-19，本机 self 节点）**：`kick-once.py` 一次 kick 一条判决批
（`smoke-ladder-c01`：4 关 × 2 局 × 2 ckpt = 16 局）→ `claimed … course=` →
unit 展开 → 2 局派给 `self` → 回包 → `tmp/smoke-ladder-c01/eval_log.jsonl` 行带齐
Phase-0 七列（`exposureByKind [1439,0,0,0]` 等）+ `source=B` + 真 `batch_id`。

**门禁**：nn-python-gate 1277 绿（新增 `tests/test_verdict_corpus.py` 10 例）·
dashboard typecheck + 530 绿（新增 `tests/evalboard-corpora.test.ts` 17 例）·
根 `bun run check` 1875 绿。

**未做/留给下一步**：① 判决读数进 store 仍要显式一步 `ingest-cli.ts --eval-log tmp/<corpus>/eval_log.jsonl --course <corpus id> --difficulty … --max-ticks …`（行内无 `difficulty/maxTicks/mapHash`，probe_key 靠 CLI 参数补；自动入账属 P3）；② 控制台尚无「发起判决批」按钮（现只有 CLI）；③ dist 路径的 ping/权重下发仍是串行。
