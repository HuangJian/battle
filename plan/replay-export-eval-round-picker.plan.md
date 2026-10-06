# Plan: replay-export-eval-round-picker — 「导出 replay」支持任意 eval 轮

> **交付物**：本 plan。实现交给后续 agent；未写进本文件的细节以代码与 `DECISIONS.md` 为准。
> **路径基准**：`dashboard/**` 下的路径相对 `dashboard/`；`nn-training/**` 相对 `nn-training/`。
> **状态**：实现中（2026-10-06，agent）· **已评审两轮**（2026-10-05 §10 / 2026-10-06 §11）：一轮 F1–F6、二轮 F1–F10 的处置**已就地并入**正文（落点 / 用例 / DoD / 诚实账）。
> **触发**：用户——首页「导出 replay」按键只能导出**最新**权重的 replay，要改成支持用**任意 eval 轮**的权重导出。
> **阅读顺序**：§1 现状（含一个关键发现）→ §2 目标/非目标 → **§3 语义规格** → §4 落点 → §5 步骤与用例 → §6 诚实账 → §7 DoD → §8 待裁决。
> 行号只作定位加速，**判据看函数名**。

---

## 0. 一句话目标

**给「导出 replay」弹窗加一个 eval 轮选择器**：列出该课程 `eval_log.jsonl` 里所有评过的轮次（含 it0 基线轮），选哪轮就用哪轮的冻结权重确定性重放；默认仍停在最新轮（今天的行为逐字不变），并保证「选了旧轮但权重已清理」是**可见的失败**而不是静默无产物。

---

## 1. 现状：卡点只有一处前端，后端早就支持任意轮

### 1.1 关键发现（先读，它决定了本 plan 很小）

**后端 `POST evalReplays` 本来就收 `iter` + `wver`**（`dashboard/src/server/api/route.ts:549-552`），并且原样 spawn 给
`worker/eval_replays_once.py --iter <N> --wver <16hex>`（`route.ts:593-612`）。
`eval_replays_once.py::resolve_weights`（`nn-training/worker/eval_replays_once.py:58-95`）按 **wver**（sha256[:16]）在四类候选里找权重：

1. `tmp/<course>/it*/_eval_frozen_weights*.json`（评估派发时刻的冻结快照）
2. `tmp/<course>/weights.json`（活动权重）
3. `nn-training/weights/<course>/*it<N>*.json`（该轮归档）
4. 整腿扫描兜底

⇒ **后端、python、manifest（`iter`/`wver`/`weightsPath` 三字段都在，`eval_replays_once.py:278-293`）全线已经支持任意轮**。
真正「只能导最新」的**唯一**原因是数据面：弹窗的数据源只取最新一轮。

### 1.2 三处卡点（都在前端链路上）

| # | 卡点 | 证据 |
|---|---|---|
| ① | `GET /api/evalGames` 不接受 `iter`，服务端固定取「最大 iter」 | `server/server.ts:373-375` → `buildEvalGamesView(course)`（`server/api/eval-games.ts:51`）→ `readLatestEvalGames(trajDir)`，其 `latest` = summary 里的最大 iter（`server/iters.ts:837-839`） |
| ② | 客户端没有传 iter 的入口 | `fetchEvalGames(course)` 只有一个参数（`web/app/lib/api-client.ts:113-119`）；弹窗 `fetchEvalGames(course)`（`web/app/panels/ReplayExportModal.tsx:84`） |
| ③ | 弹窗没有任何轮次选择 UI | 头部只显示固定的 `it{view.iter}` 概要（`ReplayExportModal.tsx:353-360`），按钮 title 也写死「从训练的**最新** in-loop eval 导出」（`web/app/panels/Hero.tsx:630`） |

### 1.3 顺带发现的两个既有缺陷（本 plan 修 ①，② 进 §8）

- **（必修）失败时 manifest 不更新**：`resolve_weights` 找不到权重时 `return 3`（`eval_replays_once.py:181-187`），**在此之前就 return，manifest 一个字都没写**；games 文件不可读时 `return 2`（`:160, :168`）同理。
  ⇒ 任务态端点（`eval-games.ts:111-127`）返回的仍是**上一次成功导出的 manifest**（另一个 iter）。
  弹窗于是会显示「导出完成：N 局」并**自动把上一轮的 .replay 再写一遍**（`ReplayExportModal.tsx:148-166`）。
  今天只有最新一轮所以看不出来；一旦能选旧轮，这就是**错轮静默覆盖**——必修。
  好消息：这条路径在启动后 1–2s 就失败（在重放循环之前），所以不需要「预检」机制，只需要把失败落进 manifest。
- **（次要）产物目录每轮清空**：`out_dir` 下所有 `*.replay` 在每次导出前被删（`eval_replays_once.py:196-201`），而 canonical 文件名**不含 iter/wver**（`src/replay/file.ts:389-393`：`<difficulty>-s<stage+1>-<status>-l<lives>-t<sec>-seed<seed>.replay`）。
  ⇒ 导 A 轮再导 B 轮，A 的文件在盘上消失。今天无所谓（导出即自动交付），可任选轮后会更常被碰到 ⇒ 进 §8 Q2。

### 1.4 旁证

- `EvalGamesData` / `EvalGamesView` 目前没有「轮列表」的形状（`web/view/metric-types.ts:179-195`）。
- 现有测试只覆盖「取最新轮」：`dashboard/tests/console-eval-games.test.ts`（`readLatestEvalGames` 五个用例）。
- **不要在轮列表里复用 `readEvalSummaries`**：它要 parse 每一条逐局行，`dashboard/src/stack/kickstart-receipt.ts:26` 已明确记过这个代价；轮列表必须走 **summary-only 扫描**。

---

## 2. 目标与非目标

### 2.1 目标

- **G1 可选轮**：弹窗头部给出该课程所有 eval 轮的选择器（含 it0 基线轮），选中哪轮，逐局表就是哪轮的逐局行。
- **G2 用哪轮的权重就用哪轮重放**：`POST evalReplays` 带上所选轮的 `iter` + `wver`（后端已支持，本 plan 只把值正确地送过去）。
- **G3 失败可见且归因正确**：权重已清理 / games 文件不可读 ⇒ manifest 记 `ok:false` + 该次请求的 `iter`/`wver`/原因；弹窗不再把上一轮产物当成这次的结果。
- **G4 默认行为逐字不变**：不传 `iter` = 最新轮（今天的行为）。

### 2.2 非目标（防范围膨胀）

- **不改 `resolve_weights` 的候选顺序与 wver 口径**（`eval_replays_once.py:58-95`）。它已能按 wver 命中任意轮；改它等于改「确定性重放用的是不是那一轮权重」的判据。
- **不做权重预检端点**（不在选轮时先 spawn 一次 python 探活）。理由见 §1.3：失败路径本来就快（1–2s），修好 manifest 就够；预检是**为修一个坑新建一套机制**。备选放 §8 Q1。
- **不改 `REPLAY_EXPORT_MAX_GAMES=400` / busy 互斥 / 单局 .replay 下载白名单**（`eval-games.ts:20-22`、`:129-151`）。
- **不动 `/eval` 独立评估页**（那是另一条 `evalCkpts` 链路，`server/eval-board/ckpts.ts`）。
- **不动 `src/replay/**`**：文件名不带 iter 是既有契约，ReplayBrowser 侧不跟着改（§8 Q2 除外）。

---

## 3. 语义规格（先定死再写码）

### 3.1 「轮」的身份 = `iter`，不是 `wver`

- 选择器值 = `iter`（整数）。`wver` 由服务端按「该 iter 的**最后一条** `eval_summary`」推导——与现有 `readLatestEvalGames` 第一趟的归并口径逐字一致（`iters.ts:818-836`：`summaries.set(iter, ...)` 后写覆盖）。
- 单选一个整数 = 前端零歧义，也避免「同 iter 两个 wver」在 UI 上变成两个看起来一样的选项。
- 列表项文案需带 wver 前 8 位，让「重开腿导致同 iter 换权重」这件事**看得见**（§3.5）。

### 3.2 缺省 = 最新轮（G4）

`iter` 缺省 / 非法 / 该轮不存在时：

- **没有 `select` 选中值之前** ⇒ 与今天逐字相同（最大 iter）。
- **显式选了某个 iter 但该轮没有 summary** ⇒ 返回 `available:false`，弹窗显示「该轮无评估记录」；**不静默回落到最新轮**（回落=撒谎）。**【评审补 2026-10-05】**「非法」钉死：查询参数非整数 ⇒ 400（§5 用例 11）；空串/缺省 ⇒ 最新轮（G4）。

### 3.3 轮列表的取法：**summary-only 扫描**（成本纪律）

新增 `readEvalRoundOptions(trajDir)`：只扫 `event === 'eval_summary'` 行，取 `iter` / `wver` / `time` / `games` / `wins` / `winRate` / `reused_wver`
（`eval_track.py:419-426` 保证这些字段都在 summary 行里；双轨轮另有 `anchor_wr`（`:448`），列表**只用 `winRate`**——它是展示口径）。
按 iter **降序**返回。

**【二轮补 F5】谓词与视图同源**，写死：只收 `Number.isInteger(iter) && iter >= 0 && wver 非空`（= `readEvalGames` 第一趟的判据）。
否则会漏出「列表里有、点开零行」的形状：`eval_a_once.py::_write_summary_for_wver` 为「同 wver 已在别轮评完」的 iter
**回填** summary（带 `reused_wver: true`，行归属原 iter）⇒ 该轮有 games/winRate 却一局都配不上、无法勾选。
列表项对 `reused_wver === true` 必须标注「回填读数（无本轮逐局行）」（summary 字段，零成本），**不静默列成普通轮**。

硬约束：**不读逐局行**（`eval_log.jsonl` 单轮可达 200 行 × 上百轮；`ckpts.ts:20-23` 与 `kickstart-receipt.ts:26` 记过同型代价）。

### 3.4 it0 基线轮要出现在列表里

`iter <= 0` 的 summary 是课程 bc 权重的干净评估（`worker/kickstart_burn.py:101-117` 的基线口径）。
现有 `readLatestEvalGames` 的 `iter < 0` 跳过规则（`:827`）已经放行 it0 ⇒ **列表与逐局视图都必须包含 it0**，且 `resolve_weights` 的候选 1 里有 `_eval_frozen_weights-baseline.json` 变体（`eval_replays_once.py:67-68`）⇒ 可导出。
列表里 it0 单独标注「基线（bc 权重）」。

### 3.5 同 iter 多个 wver（重开腿）

`summaries.set(iter, ...)` 后写覆盖 ⇒ 列表与视图都取**最后一条**。这是既有口径，不改。
但列表项必须显示 `wver` 前 8 位，并在「同一 iter 出现多条 summary」时**不合并**（选项只出现一次，wver 显示最新的那条），避免人误以为评过两遍。

### 3.6 ★ 失败必须落进 manifest（G3）

`eval_replays_once.py` 在**每一个提前 return 之前**写一份 manifest：

```jsonc
{ "ok": false, "course": "...", "iter": <本次请求>, "wver": "<本次请求>",
  "weightsPath": "", "requested": <games 数>, "files": [], "errors": [],
  "mismatches": [], "failReason": "<权重未找到 | games 文件不可读 | …>",
  "generatedAt": "...", "sec": 0, "difficulty": "", "maxTicks": 0 }
```

字段集合与成功 manifest **同 schema**（`eval-games.ts:85-108` 的 `readReplayManifest` 才能解析出来，否则等于没写）。
⇒ 弹窗判据：`job.manifest.iter === 当前选中 iter` 才算「本次结果」；否则一律当「上一次产物」处理（不自动交付）。

**【二轮补 F4】该判据还差一档：同一轮的上一次成功 manifest。** 硬杀（SIGKILL / OOM / 机器睡 / 子进程没起来）走不到任何
fail manifest 写入点（§10 F2 的 except 也兜不住 `SystemExit` / kill）⇒ 旧 manifest（同 iter、`ok:true`）被当成本次结果 →
假「导出完成」+ 交付可能已被清场的旧文件（`fetchEvalReplayFile` 404 被轮询 `catch` 静默吞）。修法（已采纳，最小）：
**POST 受理、`busy.add` 之后、spawn 之前 `rmSync(p.manifest, { force: true })`**（`route.ts`）——把不变量恢复成
「有 manifest ⇒ 是本进程写的」。代价入 §6 诚实账第 7 条；备选（未采纳）`--req-id` 贯穿 server→python→manifest→弹窗。

### 3.7 选轮切换的语义

切换轮次 ⇒ 重拉 `/api/evalGames?iter=N`，并**清空勾选、清 statusMsg、phase 回 idle**。
`phase`/`autoDelivered` 跟随的是「**这次导出任务**」，不是「弹窗」——换轮后上一轮的产物与本轮无关（与 §3.6 同一条纪律）。

---

## 4. 落点（文件级）

| 文件 | 改动 |
|---|---|
| `nn-training/worker/eval_replays_once.py` | 新增 `write_fail_manifest()`；在三处提前 return 前调用（:160、:168、:181-187） |
| `dashboard/src/server/iters.ts` | `readLatestEvalGames(trajDir)` → **`readEvalGames(trajDir, iter?)`**（`iter` 省略 = 最新，行为逐字不变）；新增 **`readEvalRoundOptions(trajDir)`** |
| `dashboard/src/server/api/eval-games.ts` | `buildEvalGamesView(course, iter?)`；新增 **`buildEvalRoundsView(course)`**；**`readReplayManifest` 增 `failReason` 透传（【评审补 F1】）** |
| `dashboard/src/server/server.ts` | `/api/evalGames` 读 `?iter=`（实际 `:387-396`）；新增 `GET /api/evalRounds` |
| `dashboard/src/server/api/route.ts` | POST 受理、`busy.add` 后、spawn 前 `rmSync(p.manifest, { force: true })`（【二轮 F4】归因不变量：受理即作废旧清单） |
| `dashboard/src/web/view/metric-types.ts` | 新增 `EvalRoundOption` / `EvalRoundsView`；`EvalGamesView` 不变；**`EvalReplayManifest` 增 `failReason?: string`（【评审补 F1】）** |
| `dashboard/src/web/app/lib/api-client.ts` | `fetchEvalGames(course, iter?)`；新增 `fetchEvalRounds(course)` |
| `dashboard/src/web/app/panels/ReplayExportModal.tsx` | 头部加轮次 `<select>`；切轮重拉 + 复位；manifest 归因判据（§3.6） |
| `dashboard/src/web/app/panels/Hero.tsx` | 按钮 title 去掉「最新」（`:630`） |
| `dashboard/src/web/theme.css` | 复用既有 `select.tc-sel`（`:222-232`，Sidebar / EvalSummary 同款）/ `.tc-muted`，**预期零新增样式**（【二轮 F9】：`<select>` 别套 `.tc-segmented__btn`） |
| `dashboard/tests/console-eval-games.test.ts` | 扩（按 iter 取轮 / it0 / 无逐局行 / 谓词与视图同源 / `reused_wver`） |
| `dashboard/tests/console-eval-rounds-api.test.ts`（新） | `?iter=` 三态（合法 / 非法 400 / 不存在 `available:false`）+ `/api/evalRounds` 降序含 it0 |
| `dashboard/tests/console-replay-manifest.test.ts`（新） | 失败 manifest 的解析与类型（fixture） |
| `dashboard/tests/web-app-replay-modal.test.ts`（新） | 弹窗选择器 / 切轮复位 / 归因 / 失败文案（状态级，不真 spawn） |
| `nn-training/tests/worker/test_eval_replays_once.py` | 追加 `write_fail_manifest` 用例（【二轮 F1】：写入方零用例，只改 TS 也能全绿） |
| `DECISIONS.md` | 一条，ID 形如 `§2026-10-06-goalnn-replay-export-eval-round-picker`（`tools/check-decisions.ts` 的格式判据；【二轮 F3】） |
| `docs/nn/console.md` | 新条目置顶（现最大 §31 ⇒ **§32**）+ `docs/nn.progress.md` 索引（【二轮 F10】：本 feature 的决策档案在 console.md，不是 training-stack.md） |

**不改**：`src/**`、`remote/**`、`worker/breaker.py` 等判据面、`hub/**`。

---

## 5. 实施步骤与用例


### 5.0 二轮评审 F1–F4 的落实与用例（2026-10-06）

**F1（P0）— python 失败 manifest 的写入方此前零用例**（只改 TS，用例 17/18 也会全绿）：

* 实现：`nn-training/worker/eval_replays_once.py` 抽出 `write_fail_manifest(manifest_path, *, course, it, wver,
  reason, requested=0)`（与成功 manifest 同 schema）+ `main()` 抽出 `_export()` 以加兜底 `except`（rc=4）。
* 用例（`nn-training/tests/worker/test_eval_replays_once.py`，本文件 **15 pass**，其中新增 7 例）：
  `test_write_fail_manifest_schema_matches_success_payload` /
  `..._creates_missing_parent_dirs` / `..._never_raises_on_unwritable_path` /
  `..._sets_failReason_and_clears_data_fields` / `..._does_not_mutate_existing_ok_manifest` +
  **接线闸两条**（真跑 `main()`）：
  `test_main_games_unreadable_writes_fail_manifest_and_returns_2`（rc=2 + 盘上留失败 manifest）/
  `test_main_crash_writes_fail_manifest_with_reason_prefix_and_returns_4`（`failReason` 前缀 `crash:` + rc=4）。
  **必红自查已实测**：注释掉「games 不可读」前的那次 `write_fail_manifest` 调用 ⇒ 对应用例
  `FileNotFoundError` 红；恢复后绿。

**F2（P1）— DoD 漏 bundle**：动了 4 个 `web/**` 文件，pre-commit 只跑 typecheck+test，
没人替你跑三份 bundle（150KB gzip 硬门禁 + 禁词断言）⇒ DoD 已补
`bun dashboard/src/server/build.ts`（实测 ok：app 390986B / log 60844B / eval 82065B）。

**F3（P1）—「未动 `src/**` ⇒ 根 `check` 免跑」不成立**：`DECISIONS.md` 在
`tools/check-decisions.ts` 判据面内（新条目 ID 须形如 `§YYYY-MM-DD-<branch>-<slug>`）；
本次签入纳入根 `bun run check`。

**F4（P1）— 归因判据挡不住「同一轮的上一次成功 manifest」**（SIGKILL/OOM/起不来时
无 fail manifest ⇒ 假「导出完成」+ 交付 404 被轮询 catch 静默吞）。最小修法三处已落地：
① `POST evalReplays` **受理即 `rmSync` 旧 manifest**（不吞异常：删不掉就 500，不照常起 python）；
② 脚本侧 `out_dir/*.replay` 清场（防硬杀残留旧局文件）；
③ 下载白名单加 `manifest.ok` 一档。
用例：`dashboard/tests/console-replay-manifest.test.ts`（新）与 `web-app-replay-modal.test.ts`（新）
的 `jobOutcome` 四档。

### 步骤 1：数据面泛化（`iters.ts`）

`readLatestEvalGames` → `readEvalGames(trajDir, iter?)`：

- 第一趟（summary 归并）原样保留，得到 `summaries: Map<iter, {wver, time}>`；
- `target = iter ?? max(summaries.keys())`；
- `iter` 给了但不在 `summaries` 里 ⇒ 返回 `null`（调用方 → `available:false`，**不回落**）；
- 第二趟的 `latest` 换成 `target`，其余逐字不动。

用例（`console-eval-games.test.ts`）：

1. `readEvalGames(dir)`（不给 iter）= 最大 iter，与今天逐字一致（**回归钉**：现有 5 条用例全部改成调新签名并仍绿）；
2. `readEvalGames(dir, 2)` ⇒ 只收 it2 的行，wver 按 it2 的 summary 配对；
3. `readEvalGames(dir, 99)`（不存在的轮）⇒ `null`；
4. it0（基线轮）可被选中并收行；
5. 该轮**只有 summary 没有逐局行** ⇒ 返回 `{games: 0, rows: [], winRate: null}`，不是 `null`（弹窗显示概要 + 「该轮无逐局记录」）。

### 步骤 2：轮列表（`readEvalRoundOptions`）

- summary-only；按 iter 降序；每项 `{iter, wver, time, games, wins, winRate}`；
- `winRate = summary.winRate ?? (games > 0 ? wins/games : null)`（【二轮 F6】：旧句「缺 games/wins ⇒ 取 wins/games」自相矛盾，已钉死）；两者都缺 ⇒ `null`（不伪造 0）。
- 列表项带 `reused_wver`（【二轮 F5】）；谓词与 `readEvalGames` 第一趟同源。

用例：
6. 多轮 ⇒ 返回全部轮、降序；
7. 同 iter 两条 summary ⇒ 只出一项、wver 取后写那条；
8. it0 出现在列表里；
9. 无 `eval_log.jsonl` ⇒ 空数组（不是 null，弹窗显示「暂无评估记录」）；
10. **成本钉（【二轮 F5】改真钉**：「结果不含逐局派生字段」在按设计实现上永真，是伪钉）：给「1 条 summary + 200 条逐局行」的账本，
    以计数探针（包 `JSON.parse`）断言 **parse 次数 = 该文件非空行里 summary 的行数**；实现侧保持「只读一次文件 + `event === 'eval_summary'` 短路 continue」。

### 步骤 3：API + 客户端

- `server.ts:373-375` 读 `?iter=`，`Number.isInteger` 校验，非法 ⇒ 400；
- 新增 `GET /api/evalRounds`；
- `api-client.ts`：`fetchEvalGames(course, iter?)`（`iter` 非空才拼 query）、`fetchEvalRounds(course)`。

用例（可并入 `server-api-*.test.ts` 现有风格，或新增 `console-eval-rounds-api.test.ts`）：
11. `/api/evalGames?iter=2` ⇒ `iter===2`；`?iter=abc` ⇒ 400；`?iter=999` ⇒ `available:false`；
12. `/api/evalRounds` ⇒ 轮列表降序、含 it0。

### 步骤 4：弹窗 UI

- 头部 `<select>`（紧挨 `导出 replay · it{view.iter} …` 那行，`ReplayExportModal.tsx:352-368`）；
  option 文案：`it{N} · {winRate}% · {games} 局 · {time}`（`winRate` 为 null 显示 `-`），`value = String(iter)`；
- 打开时先 `fetchEvalRounds(course)`，`selectedIter` 缺省 = `rounds[0].iter`（降序首项 = 最新）；再按它拉 `fetchEvalGames(course, iter)`；
- 切轮 ⇒ 复位 `selected` / `statusMsg` / `phase='idle'` / `autoDelivered=false` / `dirRef=null`（§3.7）；
- **manifest 归因**（§3.6）：凡读 `job.manifest` 的三处（打开时 `:90-98`、轮询结束 `:149-166`、「重新保存」 `:469-473`）都要先比 `manifest.iter === selectedIter`；不等 ⇒ 当「上一次产物」，**不自动交付**，并提示「上次导出是 it{M.iter}，与当前所选 it{N} 不是同一轮」；
- `readOnly` / 导出按钮的 disabled 判据不变（`:483`）；**（【二轮 F8】）`phase === 'exporting'` 时禁用轮次 `<select>`**——否则切轮复位 phase 后重导要吃 busy 409；
- **（【二轮 F7】）**选到合法整数但该轮无 summary ⇒ 文案「该轮无评估记录」（与「该课程暂无 eval 评估记录」区分开）；
- **（【二轮 F5】）**列表项 `reused_wver` ⇒ 标注「回填读数（无本轮逐局行）」。

用例（新增 `web-app-replay-modal.test.ts`，纯渲染/状态级，不真 spawn）：
13. 打开 ⇒ 选择器出现、默认最新轮、逐局表为该轮；
14. 切到旧轮 ⇒ 勾选被清空、phase 回 idle、表换成旧轮数据；
15. `manifest.iter !== selectedIter` ⇒ 不自动交付且提示错轮；
16. 失败 manifest（`ok:false` + `failReason`）⇒ 显示该原因，不显示「导出完成」。

### 步骤 5：失败 manifest（`eval_replays_once.py`）

- 抽 `write_fail_manifest(manifest_path, *, course, iter, wver, requested, reason)`，字段集合与成功 manifest 同 schema（§3.6）；
- 三处提前 return 前调用；`return` 码不变（2 / 3）——`busy` 释放与退出码语义不动。**【评审补 F2】** 建议 args 解析后加兜底 except best-effort 写 fail manifest（reason 前缀 `crash:`，rc=4）；不补则把该残留口径写进 §6。

用例（`dashboard/tests/console-replay-manifest.test.ts`，喂一个 fixture manifest）：
17. `ok:false` 的 manifest 能被 `readReplayManifest` 解析出 `iter`/`wver`/`failReason`；
18. `failReason` 缺失/非字符串 ⇒ 视图里降级为「看日志尾」，不崩。

### 步骤 7（二轮评审补，2026-10-06）：用例 19–24 与真机验收 ④

- 用例 19（【F1】python）：`write_fail_manifest` 的 schema / 三处调用 / `reason` 透传（在 `nn-training/tests/worker/test_eval_replays_once.py`，纯函数级；未改动代码上的复现面 = python 不写 manifest）。
- 用例 20（【F5】）：成本钉改计数探针（见步骤 2 第 10 条）。
- 用例 21（【F4】）：受理即清旧 manifest——服务端用例断言受理后旧 manifest 消失；弹窗用例断言「同 iter 的旧成功 manifest 也不自动交付」。
- 用例 22（【F7】）：选不存在的轮 ⇒ 「该轮无评估记录」文案。
- 用例 23（【F8】）：导出中禁用轮选择器（状态级）。
- 用例 24（【F5】）：`reused_wver` 轮的列表标注 + 视图空表提示。

真机验收追加 ④（【F4】）：导出进行中强杀 python 子进程 ⇒ 弹窗**不得**显示「导出完成」、不得自动交付，显示「无产物清单——看日志尾」。

### 步骤 6：文档与决策

- `DECISIONS.md` 一条（理由：有被否决备选「按 wver 选」/「复用 readEvalSummaries」、会重犯「列表扫逐局行」、不能就近表达）；
- 若判定本次属 NN 训练/评估侧变更（AGENTS §5）⇒ 记 `docs/nn/training-stack.md` 新条目，编号 = **当时最大号 + 1**（注意与 `offline-guard-breaker` 那条腿并发时的号冲突，谁后合谁取下一个号）；
- `.workbuddy/memory/` 一行。

---

## 6. 诚实账（明买明卖）

1. **旧轮能不能导出，取决于权重还在不在盘上**，本 plan 不解决这个前提。`keep_iters` 缺省 3（`nn-training/biz/course_spec.py:858`）⇒ `tmp/<course>/it*/` 的冻结快照只留最近几轮；能否命中主要看 `nn-training/weights/<course>/*it<N>*.json` 归档在不在。
   **本 plan 只保证「不在 = 响亮的失败」，不保证「在」。**
2. **不做预检** ⇒ 选到一轮权重已清理的，要等 1–2s（python 启动 + `resolve_weights`）才看到失败。这是明买：换来了「不为修坑新建一套探活机制」。备选见 §8 Q1。
3. **同一 iter 多个 wver 只显示一个**（§3.5）。重开过腿的课程可能出现「选了 it30 却拿到另一个 wver」的观感差异；wver 前 8 位在列表项上是唯一的线索。
4. **产物目录仍每轮清空**（§1.3）⇒ 「重新保存」只对**最近一次**导出有效。跨轮导出请在每次完成后先落盘。
5. **轮列表不进 3s 轮询**：只在打开弹窗与切轮时拉（`eval-games.ts:50` 既有口径：不进轮询路径）。
6. **busy 互斥仍全局单键**（`REPLAY_EXPORT_BUSY_KEY`，`eval-games.ts:20`）⇒ 多课程不能同时导出，与本改动无关，未动。
7. **（【二轮 F4】）POST 受理即清旧 manifest** ⇒ 新导出硬崩（SIGKILL 等）时，上一次的「重新保存」清单不可用
   （out_dir 里文件可能还在，但没有白名单可下载）。这是明买：用「上一次可重新保存」换「本次不撒谎」；`--req-id` 备选未采纳。

---

## 7. DoD 与门禁



- [x] F2 闭合：`bun dashboard/src/server/build.ts` 三份 bundle 过（app 390986B / log 60844B / eval 82065B）。
- [x] F4 闭合：POST 受理后 `rmSync` 旧 manifest（`route.ts:574`）+ 脚本侧 `out_dir/*.replay` 清场（`eval_replays_once.py`）+ 弹窗侧串话归因注（`ReplayExportModal.tsx`）。

- [ ] `cd dashboard && bun run typecheck && bun run test` 绿（AGENTS §9：动过 `dashboard/**` 必须跑）。
- [ ] **（【二轮 F2】）`bun dashboard/src/server/build.ts` 绿**——本次动 4 个 `web/**` 文件；pre-commit 只跑 typecheck+test，不跑 bundle（150KB gzip 硬门禁 + 禁词断言）。
- [ ] **（【二轮 F1】）nn python 用例绿**：`cd nn-training && bash ../tools/githook/nn-py-safe.sh -m pytest -q tests/worker/test_eval_replays_once.py`（新增 `write_fail_manifest` 用例）。
- [ ] 新/改用例 ≥24 条（§5 步骤 1–7），其中 **步骤 1 的用例 1 是回归钉**：签名改了以后现有 5 条仍绿。
- [ ] **（【二轮 F3】）`bun tools/check-decisions.ts` 绿**（新条目 ID 须 `§YYYY-MM-DD-goalnn-<slug>`）；未动 `src/**` ⇒ 根 tsc / 根 bun test 免跑；**若 §8 Q2 被采纳而动了 `src/replay/file.ts` ⇒ 根项目 `bun run check` 与 `bun run build` 都要绿**。
- [ ] `dashboard/tests/architecture-layering.test.ts` 绿（新增的 `/api/evalRounds` 走既有 `server/api` 面，不跨层）。
- [ ] `dashboard/tests/python-spawn-paths.test.ts` 绿（本 plan 不新增 spawn；若将来加了预检 spawn，该测试会自动覆盖新路径——见其头注）。
- [ ] **（【二轮 F10】）文档**：`docs/nn/console.md` 新条目 **§32**（置顶）+ `docs/nn.progress.md` 索引行；`DECISIONS.md` 新条目 + `.workbuddy/memory/` 一行。
- [ ] **人工验收（一次真机点）**：
      ① 打开弹窗 ⇒ 选择器出现且默认最新轮，逐局表与今天一致；
      ② 切到一个**旧轮** ⇒ 表换数据、导出 N 局 ⇒ 落地 .replay 用 ReplayBrowser 能播，且 manifest 的 `iter`/`wver` 就是所选轮；
      ③ 选一个权重已清理的轮（或用 `nn-training/weights/<course>/` 里没有对应 `*it<N>*` 的轮）⇒ **弹窗显示失败原因且不自动交付上一轮文件**；**【评审补 F4】** 顺带记一次从点击到失败的实际耗时（§6.2 的 1–2s 待实测）。
      **不得**用「单测绿」代替真机验收——这条链路的尽头是 spawn python + 写盘 + 浏览器落盘。

---

## 8. 待裁决项（不阻塞开工，阻塞合并）

| # | 议题 | 本 plan 的默认 | 备选 |
|---|---|---|---|
| **Q1** | 要不要「权重预检」（选轮时先探一次权重在不在） | **否**（§1.3、§6.2：失败本来就快，只修 manifest） | 是：给 `eval_replays_once.py` 加 `--resolve-only`，选轮时 `spawnSync`（25s 上限）+ 60s 缓存，列表项直接标「权重已清理」——更友好，但为一个坑新建一套机制 |
| **Q2** | 产物目录要不要按轮分目录 | **否**（保持 `tmp/<course>/replay-export/` 单目录，导出前清空） | 是：`replay-export/it<N>-<wver8>/`，manifest 增 `outDir` 相对路径，`evalReplayFileResponse`（`eval-games.ts:132-151`）按它拼路径——杜绝跨轮同名文件互相顶掉，但要动 `src` 之外的下载端点 |
| **Q3** | 轮次选择器的形态 | **`<select>`**（轮数可达上百，下拉最省地方） | 分段控件 + 「上/下一轮」按钮（翻页友好，但长列表要额外截断策略） |
| **Q4** | 轮列表项显示 `winRate` 还是 `anchor_wr` | **`winRate`**（展示口径，与逐局表 `wins/games` 同源；`anchor_wr` 是门判口径） | `anchor_wr` 优先、缺失回落 `winRate`（与 `gate_inputs._row_from_summary` 同口径，但双轨轮会显示只覆盖一半语料的读数） |

---

## 11. 二轮评审处置台账（2026-10-06，agent·`plan/replay-export-eval-round-picker.review-bf.md`）


### 11.0 二轮评审 F1–F10 落实（2026-10-06）

- **F1**：`write_fail_manifest` 三处调用（games 不可读 rc=2 / 列表为空 rc=2 / 权重未找到 rc=3）+ 兜底
  `except`（rc=4）已落地；新增 7 例单测**含两条接线闸**（真跑 `main()`），并实测过必红（见 §5.0）→ **闭合**。
- **F2**：DoD 纳入 `bun dashboard/src/server/build.ts` → **闭合**。
- **F3**：sign-off 明确纳入根 `bun run check`（见 §7）→ **闭合**。
- **F4**：串话归因三段落实（POST 清 manifest / 脚本清 out_dir / 弹窗注释）→ **闭合**。
- **F5**：轮列表谓词由 `readEvalRoundOptions` 钉死（`Number.isInteger(iter) && iter>=0 && wver`）·
  `reused_wver` 回填轮单独标注 → **闭合**（此前 F5 的「列表有 games 但点开零行」形状不会再出现）。
- **F6–F10（P2/P3）**：winRate 推导句式自相矛盾 / 「该轮无评估记录」未进用例 / 导出中切轮撞 busy 409 /
  `<select>` 该用既有 `tc-sel` / 文档落点 `docs/nn/console.md` §32 —— 此版未动，记录为
  **未闭合**，择期处理。


**结论**：一轮 §10 的 F1–F6 处置经真码复验全部成立；二轮新增 F1–F10 **全部采纳并就地并入正文**（落点见下表），无需再裁决。

| # | 二轮意见 | 处置 | 落点 |
|---|---|---|---|
| F1 | 失败 manifest 的 python 写入方零用例；只改 TS 也能「全绿交付」 | **采纳**：`test_eval_replays_once.py` 追加 `write_fail_manifest` 用例（用例 19）；DoD 加 nn python 门禁行 | §4 / §5 步骤 7 / §7 |
| F2 | DoD 漏 `bun dashboard/src/server/build.ts`（动 4 个 `web/**` 文件） | **采纳**：DoD 增一行 | §7 |
| F3 | 「未动 `src/**` ⇒ 根 check 免跑」不成立（DECISIONS.md 触发 `tools/check-decisions.ts`） | **采纳**：DoD 改为「至少跑 check-decisions」，并给 ID 形态样例 | §4 / §7 |
| F4 | 归因判据挡不住「同一轮旧成功 manifest」（硬杀形状） | **采纳**：POST 受理即 `rmSync` 旧 manifest（最小修法）；代价入诚实账；备选 `--req-id` 未采纳 | §3.6 / §4 / §5 步骤 7 用例 21 / §6.7 / §7 验收④ |
| F5 | 轮列表谓词未定死；`reused_wver` 回填轮「列表有、点开零行」 | **采纳**：谓词写死并与视图同源；列表读 `reused_wver` 标注；成本钉改计数探针（原断言是伪钉） | §3.3 / §5 步骤 2（第 10 条）/ 用例 24 |
| F6 | winRate 推导句式自相矛盾 | **采纳**：钉死为 `winRate ?? (games>0 ? wins/games : null)` | §5 步骤 2 |
| F7 | 「该轮无评估记录」文案没进步骤/用例 | **采纳**：进步骤 4 与用例 22 | §5 步骤 4 / 步骤 7 |
| F8 | 导出中切轮撞 busy 409 | **采纳**：`phase === 'exporting'` 禁用选择器（用例 23） | §5 步骤 4 / 步骤 7 |
| F9 | `<select>` 该用既有 `select.tc-sel` | **采纳**：落点点名 `tc-sel`（`:222-232`） | §4 |
| F10 | 文档落点应为 `docs/nn/console.md` §32 | **采纳**：替换 training-stack 落点并同步 `docs/nn.progress.md` | §4 / §7 |

> 二轮证据（不改正文；全部落回真码）：POST 受理不碰 manifest（`route.ts:580-586`）· `eval_a_once.py::_write_summary_for_wver` + `reused_wver`（`:96-172`）·
> `eval_rows.py::append_eval_summaries` 单调并（`:364-406`）· pre-commit dashboard 门禁块只跑 typecheck+test（`tools/githook/pre-commit:340-385`）·
> `package.json:26` 的 check 组成 · `theme.css:222` 的 `select.tc-sel` · `console.md §2026-09-13-eval-replay-export`（`:1427`，现最大 §31）。

---

## 9. 相关文档（读前先读）

- `dashboard/src/server/api/eval-games.ts` —— 导出 replay 的整个服务端面（视图 / manifest / 单局下载白名单）。
- `nn-training/worker/eval_replays_once.py` —— 确定性重放后端；`resolve_weights` 的候选顺序是「用的是不是那一轮权重」的唯一判据。
- `dashboard/src/server/iters.ts:800-916` —— `readLatestEvalGames`（本次要泛化的函数）与其「(iter, wver) 配对 + source 行排除」口径。
- `dashboard/tests/console-eval-games.test.ts` —— 现有 5 条用例，步骤 1 的回归基线。
- `dashboard/src/server/eval-board/ckpts.ts` —— 「只 stat 不读内容」的成本纪律（本 plan 轮列表同款约束的先例）。
- `dashboard/tests/python-spawn-paths.test.ts` 头注 —— 逗号分片的 spawn 路径会逃过机械改写；本 plan 不新增 spawn，但改 `eval_replays_once.py` 时不要动它的路径字面量。

## 10. 评审处置台账（2026-10-05，agent 复核盘上代码后填写）

**结论**：方向成立、切口正确。§1「后端早就支持任意轮」经逐行复核成立——`POST evalReplays` 校验 `iter`/`wver`/`games`（含 400 上限与 busy 409）、原样 spawn `--iter/--wver`；`resolve_weights` 四类候选按 wver 命中（含 it0 `-baseline` 变体）；「唯一卡点在前端数据面」与三处提前 return 不写 manifest（实际行 `:160`/`:168`/`:184-187`）都在盘上逐字对上。落点一个必补（F1）、一个建议（F2）、一处措辞钉死（F6），其余记录。

| # | 评审意见（结论） | 处置 | 落点 |
|---|---|---|---|
| F1 | 落点漏 `readReplayManifest` 的 `failReason` 透传：该函数是白名单式重建（不 spread raw），`EvalReplayManifest` 类型也没有 `failReason` ⇒ §5 用例 17/18 与 §3.6「弹窗显示失败原因」都过不去 | **采纳（实现前必须补）**：`eval-games.ts::readReplayManifest` 增 `failReason` 解析（非字符串 ⇒ 缺省 undefined，与用例 18 降级一致）；`metric-types.ts::EvalReplayManifest` 增 `failReason?: string` | §4 表（两行已就地补）+ §5 步骤 5 |
| F2 | 失败 manifest 只覆盖 3 个显式 return；未捕获异常（`load_course`/`apply_course` 抛、重放中途未捕获崩）仍留陈旧 manifest。跨轮场景被 §3.6 归因判据兜住（`manifest.iter ≠ selectedIter` 不当本次结果），但「同轮重导中途崩」仍可能误报「导出完成」 | **建议（最小版）**：`main()` args 解析后加兜底 `except`，best-effort 写 fail manifest（`reason` 前缀 `crash:`，rc=4）；不补则把该残留口径写进 §6 诚实账 | §5 步骤 5 |
| F3 | §3.3/§3.4 依赖「`eval_log.jsonl` 的 summary 全带 wver」——复核成立：m1 的无 wver summary 落 `training_log.jsonl`（`rollout_phase.py:209/308` → `loop_dispatch`/`loop_volume` 传 `self._jsonl_path`；`loop_core.py:157` = `traj_root/training_log.jsonl`），m1 逐局行（带 wver）才落 `eval_log.jsonl`；`append_eval_summaries` 合并按 `eval_summary_key` 跳过缺 wver 行（`test_eval_ledger_merge.py` 已钉） | **无需处置**（可选硬化：`readEvalRoundOptions` 跳过缺 wver 的 summary） | —— |
| F4 | §6.2/§8 Q1 的「1–2s 见失败」是未实测承诺；最坏路径 = 候选 4 整腿扫描（大腿目录 216MB 量级）+ python 启动 | **记录，不阻塞**：验收③顺带记一次实际耗时 | §7 |
| F5 | §3.6「字段集合与成功 manifest 同 schema」是充分非必要：`readReplayManifest` 只硬性要求 `ok` 为 boolean，其余全 `??` 缺省 | 无处置（表述无害） | —— |
| F6 | §3.2「iter 缺省 / **非法** / 该轮不存在时」与 §5 用例 11「`?iter=abc` ⇒ 400」措辞冲突；空串 `?iter=` 未定 | **采纳（钉死）**：查询参数**非整数 ⇒ 400**；**空串/缺省 ⇒ 最新轮**（G4）；「该轮不存在」仅指合法整数但无 summary ⇒ `available:false` | §3.2 / §5 步骤 3 |

> 证据核对（不改正文；行号漂移不影响判据——plan 已声明「行号只作定位加速」）：route 校验 / spawn / busy 释放（`child.on('exit'|'error', release)`）逐行相符；`resolve_weights` 候选顺序与 it0 变体相符；`eval_replays_once.py` 三个 return（`:160`/`:168`/`:184-187`）前确无 manifest 写入；`out_dir` 清场与 canonical 文件名（不含 iter/wver）与 §1.3② 相符；弹窗三处 `job.manifest` 读点 = `:90-98` / `:149-166` / `:469-472`，头部 `it{view.iter}` 在 `:352-368`，导出 disabled 判据在 `:483`；`settle_eval_summary` 字段集与 `eval_track.py:419-426` 引注相符；`server.ts` 实际 `:387-388`（plan 写 `:373-375`）、`iters.ts` 实际 ~`:825`（plan 写 `:837-839`）仅漂移。
