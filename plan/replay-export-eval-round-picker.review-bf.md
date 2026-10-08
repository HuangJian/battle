# replay-export-eval-round-picker.review-bf.md — 第二轮评审

> 评审人：Buffy · 2026-10-06 · 基准 HEAD `9f5426d4`（goal-nn）· 方法：把 plan 的每条判据 / 落点 / 用例
> 拿到工作树真代码上逐条对账（只读 grep + 静态追踪；**未跑门禁、未改代码**）。
> **范围**：不复验 §10 已处置的 F1–F6（抽查见 §3），只报**仍未闭合或新发现**的问题。
> **结论**：方向、切口与 §10 处置整体成立、可实施。但 DoD/用例面有 **3 处会「全绿交付但要求的行为不存在」的缺口
> （F1–F3）**、归因判据有 **1 个 hard-kill 漏口（F4）**，其余为需定死的二义与小项（F5–F10）。
> 建议 F1–F4 并入 §5/§7 再开工；F5–F10 随实现就地定死。

| # | 级别 | 一句话 |
|---|---|---|
| F1 | P0 | 失败 manifest 的**写入方**（python）零用例：§5 用例 17/18 只钉 TS 解析层，**python 不改也能全绿**；DoD 也没有 python 门禁行 |
| F2 | P1 | DoD 漏 `bun dashboard/src/server/build.ts`——本次动 4 个 `web/**` 文件，pre-commit 不跑 bundle 构建 |
| F3 | P1 | 「未动 `src/**` ⇒ 根 `bun run check` 无需重跑」不成立：DECISIONS.md 在根 check 的判据面内（`tools/check-decisions.ts`） |
| F4 | P1 | 归因判据 `manifest.iter === selectedIter` 挡不住「**同一轮的上一次成功 manifest**」：硬杀（SIGKILL/OOM/子进程没起来）时弹窗会把旧产物当本次结果，交付失败还被静默吞 |
| F5 | P2 | 轮列表过滤谓词没定死；`reused_wver` 回填轮会「列表里带 games、点开零行、无法勾选」 |
| F6 | P2 | §5 步骤 2 的 winRate 推导句式自相矛盾（缺 wins/games 时无从取 `wins/games`） |
| F7 | P2 | §3.2「该轮无评估记录」文案没进 §5 步骤 4 与用例（traceability 断链） |
| F8 | P2 | 导出中切轮未定：选择器不禁用 ⇒ 复位后重导吃 busy 409 文案 |
| F9 | P3 | `<select>` 的既有类是 `select.tc-sel`（theme.css:222），不是 `.tc-segmented__btn` |
| F10 | P2 | 文档落点应为 `docs/nn/console.md` §32（本 feature 的决策档案在那），不是 `docs/nn/training-stack.md` |

---

## 1. 必修（绿着交付 / 判据有洞）

### F1（P0）失败 manifest 的写入方没有任何 Python 用例——只实现 TS 解析层即可全绿

**plan 说**：§5 步骤 5「抽 `write_fail_manifest`；三处提前 return 前调用」，用例 17/18 在
**新建的 `dashboard/tests/console-replay-manifest.test.ts`** 里喂 **fixture** manifest，钉 `readReplayManifest` 的解析与降级。

**事实**：

1. 本仓有 `nn-training/tests/worker/test_eval_replays_once.py`（纯函数风格：`resolve_weights` / `_manifest_for` /
   `_sha16` 各一组，逐字节写假权重算 sha16）。`write_fail_manifest` 与这些同构，**可直接单测**（schema 键集、
   `ok:false`、`failReason`、三处调用点）。
2. 按 plan 现有的用例集实施：**只改 TS 侧、python 一侧一行不改**，17/18 也全绿（fixture 是测试自己造的）——
   恰恰是 §1.3 那个「必修」缺陷（失败不落 manifest）没有任何用例会让它红。违反 AGENTS §7
   「没写失败测试就不算修好」：这条缺陷的复现面在 **python 写盘**，不在 TS 解析。
3. §7 DoD 只有 `cd dashboard && …` 一行；`nn-training/**` 的 python 门禁（AGENTS §5：一律经
   `bash tools/githook/nn-py-safe.sh …`；§9：`bun run check` 不覆盖 nn-training）整段缺席。

**建议**（并入 §5/§7）：

- 在 `test_eval_replays_once.py` 加 2–3 条纯函数用例：`write_fail_manifest` 落盘的键集与成功 manifest 同 schema
  （用 `readJsonl`/`json.loads` 直接断言 `{"ok","course","iter","wver","requested","files","errors","mismatches","failReason",…}`）、
  `reason` 透传、`files/errors/mismatches` 为数组（否则 `readReplayManifest` 的 `Array.isArray` 兜底会让它们变 `[]`，
  等于没写）。
- DoD 加一行：`bash tools/githook/nn-py-safe.sh -m pytest -q tests/worker/test_eval_replays_once.py`（在 `nn-training/` 下）。
- 若采纳 F2（兜底 except），同批补「`reason` 前缀 `crash:`、rc=4」的用例。

### F2（P1）DoD 漏 dashboard bundle 构建——本次动了 4 个 `web/**` 文件

**plan 说**：§7 只列 `cd dashboard && bun run typecheck && bun run test` + 根 check 豁免 + 两个既有守卫测试。

**事实**：AGENTS §9 明文「动过 `dashboard/src/web/**` ⇒ `bun dashboard/src/server/build.ts` 三份 bundle 也过」。
本次修改 `metric-types.ts` / `api-client.ts` / `ReplayExportModal.tsx` / `Hero.tsx` **全在 `web/**`**。
`build.ts` 不是形式主义：它跑 Bun.build + **禁词断言**（客户端不得含 `node:`/`fs`/`Bun.`/`api/actions`）+
**gzip 体积硬门禁 150KB**（`build.ts:1-14`、`BUNDLE_GZIP_BUDGET`）。
而 pre-commit 的 dashboard 门禁块（`tools/githook/pre-commit:340-385`，逐字读过）**只跑 typecheck + `bun run test`**，
没有 `build.ts` / `build:ui` 调用 ⇒ 没人会替你跑，离线交付前也不会暴露。

**建议**：§7 加一行 `bun dashboard/src/server/build.ts`（或 `cd dashboard && bun run build:ui`），并写「新增 UI 文案若触到禁词/超预算，此步会拦」。

### F3（P1）「未动 `src/**` ⇒ 根 `bun run check` 无需重跑」不成立

**plan 说**：§7「未动 `src/**` ⇒ 根项目 `bun run check` 无需重跑；若 §8 Q2 被采纳而动了 `src/replay/file.ts` ⇒ 根 `bun run check` 与 `bun run build` 都要绿」。

**事实**：根 `check` = `tsc --noEmit` **+ `bun tools/check-decisions.ts`** + `bun tools/check-agents-budget.ts` +
`bun tools/run-root-tests.ts`（package.json:26，逐字读过）。本 plan 明确要写 DECISIONS 条目 ⇒ **check-decisions 是判据面**：
新条目 ID 必须形如 `§YYYY-MM-DD-<branch>-<slug>`（`check-decisions.ts` 的 `DATE_ID_RE`，branch 去连字符 ⇒ 本腿 = `goalnn`），
撞号 / 格式错 / 丢号直接 fail。
另外 §4「DECISIONS.md 一条」没写 ID 形态，实施者可能写成语义相同的旧式编号而吃红。

**建议**：§7 把该行改成「未动 `src/**` ⇒ 根 tsc / 根 bun test 免跑；**但 DECISIONS.md 在
`bun tools/check-decisions.ts` 的判据面内 ⇒ 至少跑 `bun tools/check-decisions.ts`**」；§4 给样例 ID
（如 `§2026-10-06-goalnn-replay-export-eval-round-picker`，写前 tail 查当日已有 ID）。

### F4（P1）归因判据漏「同一轮的上一次成功 manifest」：硬杀形状会把旧产物当本次结果

**plan 说**：§3.6「弹窗判据：`job.manifest.iter === 当前选中 iter` 才算『本次结果』」；§10 F2 建议给
`main()` 加兜底 except 把未捕获异常也写成 fail manifest。

**事实链**（逐行读过）：

1. POST 受理时服务端**不碰 manifest**（`route.ts:580-586` 只写 gamesFile、截断 log；manifest 路径 `p.manifest` 直到 python 落盘才第一次被写）。
2. 因此「进程没到写 manifest 就死」的一切形状（SIGKILL / OOM / 机器睡眠 / venv python 起不来 / import 阶段炸）
   留下的是**上一次的 manifest**——若上一次成功导出的正是**同一轮**，`manifest.iter === selectedIter` 成立 ⇒
   弹窗走 `m.ok && files.length>0` 分支：「导出完成：N 局」+ `deliver(m.files)`（`ReplayExportModal.tsx:149-166`）。
3. 而这一次的 run 已经/可能把 `out_dir/*.replay` 清掉（python 清场在 `:196-201`，在重放循环之前）⇒
   `fetchEvalReplayFile` 404 抛错（`api-client.ts:190-202`）→ `deliver` 抛进轮询 `tick` 的 `try{}catch{}`（「轮询失败下轮再试」）
   **静默吞掉**；「重新保存」按钮的 onClick 更是没有 try/catch ⇒ unhandled rejection。用户看到的是「完成」+ 空目录。
4. F2 的兜底 except 只能覆盖 **Python 层异常**（`main()` 内），覆盖不了 kill / 启动失败 / `SystemExit`：
   漏洞形状原样存在。它也不是「跨轮场景被归因判据兜住」那档——**同轮**这一档 §3.6 没有名字。

**建议**（二选一，推荐 A）：

- **A（最小、把不变量恢复成「有 manifest ⇒ 是本进程写的」）**：POST 受理、`busy.add(REPLAY_EXPORT_BUSY_KEY)` 之后、
  spawn 之前 `rmSync(replayExportPaths(course).manifest, { force: true })`。此后三处读点的「上一轮产物」判定只剩
  「无 manifest」一档（弹窗已有「导出进程已退出但无产物清单——看日志尾」文案）。
  代价：新导出硬崩时，上一次的 manifest 没了 ⇒ 旧 .replay 不可再下载（out_dir 若没被新 run 清到还留着文件）。
  这是明买：**用「上一次可重新保存」换「本次不撒谎」**，写进 §6 诚实账。
- **B（更精确，多一个字段/参数）**：server 生成 `reqId`（uuid），POST 响应带回；spawn 传 `--req-id`；python 写进
  manifest；弹窗只认 `manifest.reqId === 本次 POST 的 reqId`。动 route.ts + py + 两个类型 + 弹窗，比 A 贵但不受清场影响。
- 无论选哪个，**用例 15 的断言要扩**：不只「`iter` 不同不得自动交付」，还要「**同一 iter 的旧成功 manifest** 也不得自动交付」。

---

## 2. 需定死（二义 / 追踪缺口 / 小项）

### F5（P2）轮列表的过滤谓词没定死；`reused_wver` 回填轮会「列表有、点开空、不能勾选」

**plan 说**：§3.3 只规定「只扫 `event === 'eval_summary'` 行，取 iter/wver/time/games/wins/winRate」；
§5 步骤 2 只补了「缺 games/wins 时 winRate 取 wins/games」（见 F6）。

**事实**：`eval_a_once.py::_write_summary_for_wver`（`:96-172`，docstring 逐字：「行归属仍是原 iter，summary 只是本 iter 的
读数入口」）会为「同 wver 已在别轮评完」的 iter **回填 summary，带 `reused_wver: True`，而逐局行留在原 iter**。
⇒ 这样一轮在列表里带着 `games=N · winRate=X%`，选中后 `readEvalGames(dir, iter)` 按 `(iter, wver)` 配对
**一局也配不上**（行 iter ≠ 本 iter）⇒ 空表 + 「该轮无逐局记录」，且**无法勾选导出**。
plan 的用例 5（「只有 summary 没有逐局行」）正是这个形状，但被当成显示兜底，没有当成「这一类轮要不要进列表/怎么标注」。

**建议**：① 谓词写死为「`Number.isInteger(iter) && iter >= 0 && wver 非空`」（与 `readEvalGames` 第一趟同源），
加一条「列表项与视图判据一致」用例；② 列表扫描顺手读 `reused_wver`（summary 字段，零成本），该项渲染
「回填读数（无本轮逐局行）」或直接排除——**别让用户对着空表猜**；③ 用例补一条「reused_wver 轮」的形状。

### F6（P2）winRate 推导句式自相矛盾

§5 步骤 2 原文：「缺 `games`/`wins` 的旧 summary ⇒ `winRate` 取 `wins/games`，两者都缺 ⇒ `null`」——
缺 games/wins 时无从取 `wins/games`。钉死为：`winRate = summary.winRate ?? (games > 0 ? wins/games : null)`，
且列表与弹窗头部（`view.winRate` 走逐局行重算）两处口径不同源这件事写进列表项 title 或 §6（概要 games 可能 > 逐局行数，B/C source 行被视图排除）。

### F7（P2）「该轮无评估记录」文案没进步骤/用例

§3.2 要求「显式选了某个 iter 但该轮没有 summary ⇒ 弹窗显示『该轮无评估记录』」，但 §5 步骤 4 的 bullet 只写
选择器 / 默认轮 / 切轮复位 / manifest 归因，用例 13–16 也没有这条。实现者按步骤写完 = 这条不会发生
（现状 `!view.available` 一律渲染「该课程暂无 eval 评估记录」，`ReplayExportModal.tsx:382-384`）。
**建议**：步骤 4 补一行 + 用例「选不存在的轮 ⇒ 显示『该轮无评估记录』，与『暂无 eval 评估记录』区分」。

### F8（P2）导出中切轮：选择器不禁用 ⇒ 用户撞 busy 409

切轮会 `phase='idle'`（§3.7），但服务端 busy 仍被上一次导出占着；用户在新轮上再点导出 → 409
「已有 replay 导出在进行」。这是自造的一次困惑。
**建议**：步骤 4 写明「`phase === 'exporting'` 时禁用选择器」（与 busy 全局互斥同口径，最省），或切轮时保留
「上一轮导出仍在跑」的 statusMsg。

### F9（P3）`<select>` 用既有 `select.tc-sel`，不要引去 `.tc-segmented__btn`

§4 写「复用既有 `.tc-segmented__btn` / `.tc-muted`，预期零新增样式」——`tc-segmented__btn` 是**按钮**族；
`<select>` 的既有样式是 `select.tc-sel`（`theme.css:222-232`，`Sidebar.tsx:115/159/180/211`、`EvalSummary.tsx:322` 已在用）。
点名 `tc-sel` 才是真·零新增；§5 步骤 4 照做即可。顺带：`web-style-discipline.test.ts` 钉内联 style 与字号字面量，
新 UI 不得写 `style={{…}}`。

### F10（P2）文档落点：`docs/nn/console.md` §32，不是 `docs/nn/training-stack.md`

本 feature 的决策档案在 `docs/nn/console.md`：`### §2026-09-13-eval-replay-export（2026-09-13，控制台「导出 replay」…）`（:1427），
且该文件 `docs/nn/*.md` 的新条目置顶、**现最大 §31**（头部声明「倒序：新条目置顶、号大」）⇒ 新条目 = **§32**，
索引同步 `docs/nn.progress.md`。`docs/nn/training-stack.md` 讲训练栈（replay 字样只在无关上下文）。
python 侧（fail manifest）若判定要单独记，另开 training-stack 条目、各自取号——但**别把控制台特性记进训练栈档**。

---

## 3. 复验成立（§10 F1–F6 抽查 + plan 关键判据，全部落回真码）

- **§1.1 后端早就支持任意轮**：`route.ts:545-552` 校验 `iter`（`Number.isInteger` 且 ≥0）与 `wver`（16hex）、
  `:593-612` spawn 原样带 `--iter/--wver/--games/--out-dir/--manifest`；`resolve_weights`（`eval_replays_once.py:58-95`）
  四类候选按**内容 sha256[:16]** 命中；it0 `-baseline` 变体有既有用例（`test_eval_replays_once.py:34-42`）。plan 的
  「唯一卡点在前端数据面」成立。
- **§1.3（必修缺陷）**：三个提前 return 前确无 manifest 写入（实际行 py`:152` / `:161` / `:186`，plan 写
  `:160/:168/:181-187`，±5 行）；`out_dir` 清场在 `:196-201`；canonical 文件名确实不含 iter/wver
  （`src/replay/file.ts:389-393`，plan 引注逐字相符）。
- **§1.3②**：POST 受理不碰 manifest（`route.ts:580-586`）；`evalReplayFileResponse` 白名单 = `manifest.files` 精确匹配
  （`eval-games.ts:129-151`）⇒ 跨轮同名文件互相顶掉后旧清单不可下载，成立。
- **§3.4 it0**：`BASELINE_EVAL_ITER = 0`（`worker/eval_local.py:149`）；基线即为 `eval_log.jsonl` 里 `iter=0`
  且同 wver 的 summary（`eval_local.py:494-499`）；`readLatestEvalGames` 的 `iter < 0` 跳过放行 it0（`iters.ts:827`）⇒ 可列表、可导出。
- **§10 F1 处置正确**：`readReplayManifest` 是白名单式重建（不做 spread），`EvalReplayManifest`
  （`metric-types.ts:197-210`）无 `failReason` ⇒ 不补透传，用例 17/18 与「弹窗显示失败原因」都过不去。
- **§10 F3 证据**：`append_eval_summaries`（`eval_rows.py:364-406`）按 `(iter,wver)` **单调**并（`games` 不增不写）、
  缺 wver 行跳过 ⇒ 无 wver summary 不进 eval_log 的结论成立。
- **§1.4 成本纪律引注**：`ckpts.ts:15-24`「只 stat，不读内容（996 文件 / 434MB）」；`kickstart-receipt.ts:26`
  「18MB 账本…不调 `readEvalSummaries`——那个聚合要 parse 每一条逐局行」⇒ 轮列表 summary-only 的约束有先例。
- **测试面**：`dashboard/tests/console-eval-games.test.ts` 恰 5 条（:47/54/75/96/126），步骤 1 的回归钉可落地；
  `dashboard/tests/python-spawn-paths.test.ts` 存在（头注 = 逗号分片 spawn 的机械改写漏，与本 plan 口径一致）；
  `dashboard/tests/architecture-layering.test.ts` 存在（未见 endpoint 清单，plan 的 DoD 行无害）。
- **§6.1**：`keep_iters: int = 3`（`biz/course_spec.py:858`）逐字相符。
- **行号漂移标注正确**：§10 说 `server.ts` 实际 `:387-388`、`iters.ts` ~`:825` —— 盘上是 `server.ts:387-396`、
  `iters.ts:815-830`，属漂移，判据看函数名（plan 已声明）。

## 4. 未验证边界

- **未跑任何门禁/测试**（文档评审、无改动）；F1–F4 的行为链是静态追踪（读 route POST / python 提前 return /
  前端交付路径），实施时按 AGENTS §7 各先钉最小用例。
- §6.2 / §8 Q1 的「1–2s 见失败」仍未实测（§10 F4 已记录；最坏路径 = 候选 4 整腿 sha256 扫描）。
- 未实测「同一 iter 两个 wver / 云机 summary 并账」在生产账本里的实际分布；F5 的 `reused_wver` 形状是按代码
  路径存在性判定的（`eval_a_once.py` 是控制台 evalA 的唯一写者），未 grep 真实 `tmp/**/eval_log.jsonl` 核对。
