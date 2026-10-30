# Review — `plan/notebook-userscript.plan.md`

> **评审日期**：2026-10-29 · **方式**：静态复核（读仓库代码 + 把 `D:\github\Kaggle-Notebook-Editor.mhtml`
> 的两个 HTML 帧解码后逐条比对 + 门禁链路推演）。**未实现、未开浏览器**——所有「实测」结论都标了来源，
> 没验过的写「未验证」，不拿推测当证据。
>
> **阅读顺序**：§0 总评 → §2 八条阻断项（开工前必须处理）→ §3 重要缺口 → §4 协议修订稿 →
> §8 接手前 30 分钟的证伪清单。§1 是对原 plan 证据的复核表，只在对不上号时才需要看。

---

## 0. 总评

| 维度 | 判定 | 一句话依据 |
|---|---|---|
| 问题定义 / 边界 | **好** | 跨域双帧、虚拟化、日志只进不出三条硬约束都识别对了；§11 被否的备选没有一条是错的 |
| 证据质量 | **高** | 命令 id、cell 结构、`\r\n`、`Stop session`、`collapse-all` 陷阱全部复核属实（§1） |
| 功能可行性 | **有条件可行** | 六项都能做；但「长行 / 清空 / 多标签」三个真实场景会撞上协议洞（B3/B4） |
| 按现稿直接开工 | **不建议** | 会先红在门禁上（B1/B2），再红在三个协议洞上（B3/B4/B6） |
| 交付成本 | 中大型（W0–W6） | W4（定时器）风险最高、增益最低（Kaggle 原生 Schedule 已占位），建议放最后且默认关 |

**结论**：方向和证据都够硬，**但先补 §2 的八条再动 W1**。八条里五条是「一两行改动 + 一条回归测试」，
两条（B3/B4）要改协议字段，一条（B6）要改选择器。修完这份 plan 就从「写得漂亮」变成「能直接派给 agent 执行」。

**我明确同意、不要在修订时动掉的设计**：

- 「保存失败绝不清空」+「不假装点击成功」——这是整份 plan 最值钱的两条；
- 命令阶梯每一步都有**可观测后置条件**（§4.4），且 `run-cell` 不强断言排队——克制得对；
- 用**虚拟键**复用日志页、**不**把 notebook 日志做成 `Component`（§4.6）——生命周期语义判断正确，
  已核实 `LogPageOptions.components` 只要 `{key,label,status}` 三个字符串（`dashboard/src/web/view/log-view.ts:18`），
  `fetchLog(key: string)` 也不认枚举（`dashboard/src/web/app/lib/api-client.ts:84`）；
- 不走 `fetch` / 不碰 hub / 不改训练代码（§2.4、§11）——理由成立，复核后仍成立。

---

## 1. 复核表：原 plan 的「实测」vs 我复核到的事实

存档解码方式：`email` 解析 mhtml → 取 `text/html` 且长度 > 1KB 的两帧（外层 36 KB / 内层 226 KB）→
正则计数。**mhtml 是 quoted-printable 的**，所以直接 `grep 'data-jp-item-name="run"'` 会得 0（实际是
`=3D"run"`）——原 plan 的结论能对上，说明它当时是解码后读的，这点没问题。

| # | plan 断言 | 复核 | 证据 |
|---|---|---|---|
| 1 | 外层/内层双帧；iframe `name="notebook-editor-cells"`；`aria-label="Stop session"` | ✅ | 外帧 `Stop session`×1、`Stop execution`×2、`Factory reset`×1、`Draft Session`×1；`<iframe name="notebook-editor-cells" title="Notebook editor cells">` |
| 2 | 内层 `.jp-Cell[data-uuid][data-windowed-list-index]`；存档只有 2 个 cell | ✅ | 2 个 `data-uuid`：`59672cc7…`(markdown, idx 0) / `da0450c9-85fa-4e8c-962c-ff257ff3e362`(code, idx 1)；`.jp-WindowedPanel-outer`×1 |
| 3 | 命令面板里 160 条 `li[data-command]`，plan 列的 id 全在 | ✅ | 含 `notebook:clear-all-cell-outputs` / `notebook:clear-cell-output` / `hide-all-cell-code` / `show-all-cell-code` / `hide-cell-code` / `show-cell-code` / `run-cell` / `interrupt-kernel` … |
| 4 | 工具栏 `collapse-all` 是「Collapse All Headings」，不是折叠代码 | ✅ | item name 表里同时有 `collapse-all`、`notebook:collapse-all-headings`、`toc:toggle-collapse`；折叠代码是 `notebook:hide-all-cell-code` |
| 5 | 输出是单个 `<pre>`，行尾 `\r\n` | ✅ | 15,477 字符的输出段：LF=229、CR=229；且**有相邻两条日志之间没有任何换行**（`…alive (0 min)[06:23:04]…`），说明「按行」不能当唯一分块口径（见 B3） |
| 6 | 单 cell 工具栏「首项就是 `data-jp-item-name="run"`」 | ⚠️ **不完整** | 2 个 cell 只有 **1 个** `jp-cell-toolbar` / 1 个 `data-jp-item-name="run"` ⇒ JupyterLab 4 默认 `cellToolbarVisibility:'active'`，**工具栏只挂在 active cell**。plan 的 `runCell(c)` 缺「先选中」（B5） |
| 7 | `rollout.ipynb` cell 1：首行 `# @title`、`CFG = {` 在第 5 行 | ✅ | 逐行打出核对一致（`nn-training/ipynb/rollout.ipynb`） |
| 8 | `notebook:clear-all-cell-outputs` 无确认弹窗（隐含） | ⚠️ **未验证** | 原 plan 只给 `stop-session` 写了弹窗对策（§4.4/R5）。JupyterLab 的 Clear All Outputs 是否弹 dialog 依版本/设置而定；空闲存档证明不了 |
| 9 | `run-cell` 后置条件看 `.jp-NotebookKernelStatus-busy` | ⚠️ **未验证** | 空闲存档里只有 `jp-NotebookKernelStatus` / `-fade` / `-info`，没有 `-busy`；该类名存不存在要靠探测模式确认（§8.3） |
| 10 | 选择器只覆盖 `application/vnd.jupyter.stdout` | ✗ **覆盖缺口** | Python traceback 走 `application/vnd.jupyter.stderr`；这份存档恰好只有 stdout + markdown，所以原 plan 的实测**恰好没暴露**这条（B6） |
| 11 | dashboard 无 CORS 头；`guard` 回环门控；`isReadonlyAction` | ✅ | `dashboard/src/core/net.ts:278`；全仓无 `Access-Control` |
| 12 | 虚拟键 `notebook:<session>` 能直接出现在日志页导航并显示「Notebook · <session>」 | ⚠️ **一半** | 导航渲染用的是 `componentName(c.key)`，**`label` 根本没被读**（`dashboard/src/web/app/log-app.tsx:315`）；`componentName` 对未知 key 回退成 key 本身（`dashboard/src/web/view/component-roles.ts:67`）⇒ 会显示 `notebook:kaggle-…` 原文（S1） |
| 13 | 既有测试文件 `dashboard/tests/server-api-log.test.ts` | ✗ 文件名 | 实际是 `server-api-logs.test.ts`（复数） |
| 14 | 「根门禁不会自动覆盖 userscript」 | ✗ **反例** | 根 tsconfig 确实不含（对），但根**测试**只排 `dashboard/**`（`tools/run-root-tests.ts:23`），`test-silent` 的 `SKIP_RE` 也只排 dashboard（`tools/test-silent.ts:65`）⇒ userscript 的 `*.test.ts` 会被根套件枚举（B1） |
| 15 | `.gitignore` 的 `*.lock` 会挡 `userscript/bun.lock` | ✅ | 已有 `!dashboard/bun.lock` 先例（`.gitignore` 末段） |
| 16 | `docs/nn/console.md` 当前最大号 | ✅ | 顶部是 §43（新节 = §44；该文件倒序，新条目置顶） |

> 另：原 plan 说存档「解出 222 个 part」，我用 `email` 遍历是 220 个。计数口径差异，无意义，不列为问题。

---

## 2. 阻断项（开工前必须处理）

### B1 — 根门禁会把 userscript 的测试当成自己的（两套门禁、三处）

**现象**：`userscript/test/*.test.ts` 会被根 `bun run check` 与根 `bun run test` 枚举。

**证据**：

- `tools/run-root-tests.ts:23`：`'--path-ignore-patterns=dashboard/**'` —— 只排 dashboard；
- `tools/test-silent.ts:65`：`const SKIP_RE = /^(tmp|node_modules|dist|\.git|dashboard)([\\/]|$)/` —— 同样只排 dashboard；
- `tools/test-silent.ts:148` `isDashboardOnly` / `:303` 的「只改 dashboard ⇒ 跳过根全量」判据也认不出 userscript。

**影响**：不是「多跑几个测试」，而是让**根门禁反向依赖 userscript 的安装状态**——这正是
DECISIONS §2026-09-14-goalnn-dashboard-project 排除 dashboard 的同一条理由。新克隆只跑根 `bun install` 时，
`happy-dom` / `@happy-dom/global-registrator` 解析失败 ⇒ 根 `bun run check` 直接红。

**建议**：

1. `tools/run-root-tests.ts:23` 的 ignore 加上 userscript（Bun 1.4.4 的 help 写的是 "Glob patterns"，先用逗号形式验证一次）；
2. `tools/test-silent.ts:65` `SKIP_RE` 加 `userscript`，并把 `isDashboardOnly` 泛化成
   `isIndependentProjectOnly`（或并列一个 `isUserscriptOnly`）；
3. `tests/test-silent-scope.test.ts` 已有同构断言（它现在钉的就是 dashboard 那三条），照抄一组钉 userscript；
4. AGENTS §9 的「根 `bun run check` 只判根项目」这句话在新子项目落地后要跟着改，否则下一个人又会踩。

### B2 — pre-commit 没有 userscript 闸，反而把它当根项目 TS

**证据**：`tools/githook/pre-commit:314`
`ROOT_TS_STAGED=$(grep -E '\.(ts|js|mjs|cjs|tsx|jsx)$' "$STAGED_LIST" | grep -v '^dashboard/' || true)`。

**影响**：

- 只改 `userscript/` 的提交会触发根 typecheck（无害，`tsconfig` 不含它）+ 根 `bun test`（B1 未修时红）+
  `freeze`（~4s 白烧，det 闭包不包含它）；
- 更糟的是**反方向**：userscript 自己**没有任何提交期门禁**——只有根 oxlint 扫 staged 文件。typecheck / 单测
  只在人手动 `cd userscript && bun run check` 时才跑，和「门禁自动判定」的既定口径不一致。

**建议**：仿 `dashboard` 块（`pre-commit:412-457`）加一个 `userscript` 块：`US_STAGED=$(grep '^userscript/' …)`，
非空才跑 `bun run typecheck` + `bun run test`，tsc 报错路径按 `userscript/` 前缀归因；逃生口
`SKIP_USERSCRIPT_GATE=1`（与 `SKIP_DASHBOARD_GATE=1` 对称）；`ROOT_TS_STAGED` 加 `grep -v '^userscript/'`。

### B3 — 分块只按 `\n` 切 + 服务端 256 KiB 上限 = 长流永久 413，并且永远不清空

**现象（内部矛盾 + 真实数据）**：

- §3.1 写「单次 POST ≤ 48 KiB（按 `\n` 切行累计）」，§6.1 又写「单行超限**不切行**（单独一大块）」——
  这两句本身就互相否定；
- 真实输出里**存在没有换行的长段**：抽查那条 15.5 KB 的 stdout，两条日志之间根本没有分隔符
  （`…alive (0 min)[06:23:04]…`），说明「一行 ≤ 某个长度」这个假设不成立；
- tqdm / 进度条用 `\r` 推进，整段可能**一个 `\n` 都没有**。

**影响链**：单条 > 256 KiB ⇒ 服务端 413 ⇒ 按 §3.1 的失败语义「不前进 seq、不更新 lastText、不清空」⇒
**永久重试**：日志全丢、`lastText` 一直涨、`autoClearBytes` 永远不满足（连它本来要治的内存问题也一起恶化）。

**建议**：

1. 切分单位改成三种边界的并集：`\n`、`\r`、以及硬切 **48 KiB**（硬切必须走 UTF-8 码点边界，不要按 `Buffer`
   随便切——原 plan 已经有「emoji/中文不被切坏」的用例意识，把它落到硬切分支上）；
2. 「单行超限不切」这条**删掉**：客户端对自己的承诺应是「任何一条 ≤ 48 KiB」，服务端的 256 KiB 只作为
   防御性天花板；
3. `outputText()` 归一 `\r\n → \n` 之后**再**算字节数（否则客户端算的和服务端验的不是同一个字符串）；
4. 测试：造一条 1 MiB、全无换行的 payload ⇒ 必须切成 N 块（每块 ≤ 48 KiB）且全部 200、拼接后逐字节相等。

### B4 — seq / clear 没有「代际」，也没有落盘 → 三处静默坏账

**三个独立问题，同一个根因**（协议里只有单调 `seq`，没有「这个文件现在属于哪一代」）：

1. **DoD 与设计不符**：§7 写「dashboard 重启后 seq 去重仍生效」，但 §4.6 的 `<session>.meta.json` 字段只有
   `site/notebook/lastAt/bytes/cells`——**没有 `lastSeq`**。内存态去重一重启就归零，DoD 目前不可实现。
2. **dashboard 侧 clear 会把文件切成残段**：`mode:'clear'` 截断文件后，页面的客户端**不知情**（只有页面→
   dashboard 的单向通道），下一拍仍然发**后缀** `append` ⇒ 文件从「日志中间」开始，前面没有头。
3. **同一 session 两个写者**：同一个 `runId` 页面开两个标签（或开着旧标签又开新标签），两条链共用
   `(session, cell)` 的 `seq`；后写者的 `seq <= lastSeq` 被当重复**静默丢弃**。R9 只解决了「不同 run 混写」，
   没解决「同一 run 多写者」。

**建议（协议 v2，见 §4）**：

- 服务端持**代际 `epoch`**（随 `clear` 自增）并落盘；
- 客户端带 `writer`（每次页面加载一个随机 id）与 `epoch`；
- `append` 携带旧 `epoch` ⇒ 服务端回 **409 `stale-epoch`**；客户端据此把 `lastText` 清空、下一条发 `reset`
  ——**明确告知，不静默丢**；
- `meta.json` 落 `{epoch, cells:{<cellId>:{lastSeq, bytes}}}`，重启后照常去重；
- 多写者：同 `epoch` 下 `lastSeq` 是**全局单调**还是**按 writer 分槽**要选一个。建议按 writer 分槽
  （`cells[id].writers[writer]`），否则第二个标签永远被当重复；两个 writer 写同一文件会出现交错，
  但至少不丢——面板上标「本会话有 N 个写者」。

### B5 — `runCell(cell)` 缺「先选中」，而且夹具与真实页面不符（会假绿）

**证据**：存档里 2 个 cell、**1 个** cell 工具栏、**1 个** `data-jp-item-name="run"`。JupyterLab 4 默认
`cellToolbarVisibility:'active'`，工具栏只挂在 active cell 上（`jp-mod-selected`×1 / `jp-mod-active`×1）。

**影响**：

- `notebook:run-cell` 作用于 **active cell**；「点目标 cell 的 run 按钮」在没有工具栏的 cell 上点不到任何东西；
- §4.4 阶梯第 2 级（「cell 工具栏的 run 就是这种」）和 §6.1 的用例「`runCell` 点到 `.jp-cell-toolbar` 的 run」
  会**在夹具上绿、在 Kaggle 上不工作**——最坏的一类假绿（原 plan 自己最反对的那种）。

**建议**：

1. `runCell(c)` = `ensureRendered(c)` → **选中**（点 `.jp-InputArea` 或用 `notebook:move-cursor-*` 步进）→
   等 `jp-mod-selected` 落到目标 → 执行；选中会滚动视图，定时任务可接受，手动点击要在面板上写明；
2. W0 的夹具必须**忠实**：3 个 cell、只有 active cell 有 `jp-cell-toolbar`，并补一条断言
   「非 active cell 也能跑起来」——它恰好能钉住「先选中」这一步不被后人删掉。

### B6 — 只采 stdout ⇒ traceback 全丢

**证据**：§2.2 的选择器表只列 `application/vnd.jupyter.stdout`；存档里恰好只有 stdout + markdown
（`RenderedText mime: ['application/vnd.jupyter.stdout','text/markdown']`），所以实测没暴露。

**影响**：Python 异常栈、`logging` 默认的 stderr handler、Kaggle 的告警都走
`application/vnd.jupyter.stderr`——训练日志里最值钱的部分。用户「存 log」的第一诉求就是出问题能回看。

**建议**：`outputText()` 按 **DOM 顺序**遍历 `.jp-OutputArea-child`，收 stdout + stderr（可选再加
`text/plain` 的 execute_result），段头标 mime；夹具补一个 stderr 子节点（真实采样，或明确标注 synthetic），
并加一条「stdout 与 stderr 交错时顺序不乱」的用例。

### B7 — probe 的 POST 打到了一条 GET 路由

**现象**：§4.8 说探测结果「POST 一条 `mode:'probe'` 到 `/api/notebookLogs`」；§4.6 把 `/api/notebookLogs`
登记成 **GET**。

**影响**：POST 会落到 `/api/*` → `routeAction` → 未知动作 + 一行 `[action]` 决策日志——正是 §2.3 想避开的
「把控制台日志刷爆」。

**建议**：probe 也走 `POST /api/notebookLog`（`mode:'probe'` 由 handler 分流到 `probe.jsonl`），或单开
`POST /api/notebookProbe`。别让「探测」这条边缘路径成为协议里唯一破例的动词。

### B8 — 5 秒一次的日志 POST 会永久点亮「有人在看」

**证据**：`dashboard/src/server/server.ts:368` 每个请求首行 `markConsoleActivity()`；
`dashboard/src/server/api/snapshot-refresher.ts:228` `IDLE_REFRESH_SKIP_MS = 120_000`，`consoleWatched()` 据此
判「没人看就停刷」。

**影响**：只要 notebook 在推日志（5s 一拍），控制台就永远「有人看」⇒ 2026-10-25 那条
「没请求 = 没人开页 ⇒ 后台不该每 5s 出去探一轮」的优化被整条打掉（探测继续主动出网）。这不是功能 bug，
是**把一条既有决策悄悄废掉**——最容易被评审漏掉的那类。

**建议**：给新路由一个不记活动的 guard 变体（`guard(fn, { activity: false })`），并把「日志搬运不算观看」
写进 DECISIONS 同一条正文里。

---

## 3. 重要缺口（不阻断开工，但会返工 / 踩坑）

### S1 — 虚拟键的显示名走 `componentName`，会把 key 原样贴上屏

`dashboard/src/web/app/log-app.tsx:315`：`{componentName(c.key)}`——`LogPageOptions.components[].label` 在导航里
**没被使用**（只有 `initial payload.label` 用在页面标题）。`componentName` 对未知 key 回退成 key
（`component-roles.ts:67`），所以导航条会显示 `notebook:kaggle-huangjian2026-rollout-358870479`。
**改法**：在 `COMPONENT_ROLES` 加一条前缀规则，或让 `componentName` 识别 `notebook:<x>` → `Notebook · <x>`；
`statusDot(c.status)` 那边传 `'running'`/`'idle'` 即可复用现有样式。

### S2 — `mode:'clear'` 不删 `session.1.log`

§4.6 的轮转是 `<session>.1.log` 保留 1 份。clear 只截断当前文件时，旧尾巴还在盘上 ⇒「清空」不释放空间，
用户点完还会看到磁盘占用没变。**改法**：clear 同时删掉备份段，或在面板上明说「只清当前段」。

### S3 — 「保存并清空」的 flush 判据必须是「排空」，不是「3 块 200」

§3.2 写「把所有 dirty cell 刷完且全部收到 200 再清」，但 §3.1 的每拍预算是「每 cell 最多 3 块」。这两句之间
缺一句：**flush = 循环直到该 cell 没有未发后缀**（带上限，超限就报「还在追，暂不清」）。否则「3 块都 200」
被当成刷完 ⇒ 剩下的尾巴在清空时直接蒸发——正好违反「失败绝不清空」的精神。

### S4 — 清空与「运行中输出」的竞态：最新几行最可能丢

「flush 完 → 清空」之间有窗口（网络往返 + 命令执行），运行中的 cell 在这个窗口里新写的行一定会被清掉，
而且它们**不在** `lastText` 里。**改法**：清空前**重新读一次** DOM；与上次成功落盘的文本不同就再 flush 一轮
（最多 2 次），然后才发 clear；文件里写一行 `# cleared at <iso>（可能丢失 <n> 行）` 让事后可判。

### S5 — 自动清空会抢 active cell（焦点 / 滚动 / 正在输入）

`notebook:clear-cell-output` 作用于 active cell ⇒ 必须先把目标设成 active。用户在另一个 cell 里敲代码时，
面板每 5 秒把选区挪走一次 = **键盘输入进错 cell**。**改法**（三选一，建议全做）：

1. 默认 `autoClearBytes: 0`（只手动），跑通观察一周再开；
2. 目标 cell 就是 active cell 时**跳过**本拍；
3. 笔记本有焦点（`document.activeElement` 在 iframe 内、或最近 N 秒有键盘事件）时跳过。

### S6 — `lastText` 是全量副本，内存翻倍（可选优化）

DOM 里已经有一份文本，再存一份 `lastText` = 2×。改成 `{len, hash64}` + 需要时重读 DOM 校验前缀，
内存从 O(总量) 降到 O(cell 数)。优先级不高（自动清空已经把单 cell 压在 1 MiB 内），列在这里是因为
plan 的目标之一是「防内存爆炸」。

### S7 — 背压：28.8 KiB/s/cell 追不上 tqdm

48 KiB × 3 / 5s = **28.8 KiB/s**。真实训练 cell（tqdm + 每轮 dump）轻松超过一个数量级 ⇒ 队列越积越长、
`autoClearBytes` 永远不满足、页面里 `lastText` 和 DOM 一起涨。**改法**：

- 面板显示「积压 N KiB / 上次落盘 T」；
- 每拍预算改成**全局**（如 1 MiB/拍），不是每 cell 3 块；
- 积压超过阈值（如 8 MiB）时主动「跳到最新」：清 `lastText`，下一条发 `reset` 并在段头写
  `# lost <n> bytes`——**明确丢，不装没丢**。

### S8 — 握手 token 放 GM 存储：多标签互踩 + 时序竞态

`bnc.bridge.token` 是**全脚本共享**的一个 key：

- 两个 Kaggle 标签：后加载的 host 覆盖 token，另一个标签的 frame 握手全失败（面板「未连接」，但日志也停了）；
- frame 先于 host 加载时读到的是**上一次页面**的 token，若 host 还没写新值就白跑一轮（plan 没写重试）。

**改法**：用 **`MessageChannel`**——host 在 iframe 的 `load` 后 `postMessage({kind:'attach'}, origin, [port2])`，
frame 收到 `event.ports[0]` 开始通信。port 天然只连接这一对端点（不需要 token，也不需 `event.source` 身份比较——
后者在 Tampermonkey 沙箱里对 WindowProxy 的包装是否稳定，没人验证过）。子帧重载 = 重新 attach，无状态。

### S9 — frame 的 session 名依赖 host，桥断了日志就停

§4.2 的握手才把 `session` 发给 frame。host 没起来 / 面板关掉 / 桥挂了 ⇒ frame 不知道往哪写，**日志整段落空**。
**改法**：frame 用 `document.referrer`（= `https://www.kaggle.com/code/<owner>/<slug>/edit/run/<id>`）解析出
session 名作兜底；握手到达后再用 host 的版本覆盖（两者同源同形状，覆盖是幂等的）。这样「搬运日志」这条腿
不依赖桥，「下命令」才依赖。

### S10 — CFG 的 `# @title` 兜底会静默命中错 cell

Colab 风格里**每个 cell** 都可能有 `# @title`。默认标记表把 `# @title` 放最后，本仓库 `rollout.ipynb` 恰好合用，
但换个 notebook 就会跳到标题 cell 而用户以为「定位到了 CFG」。**改法**：命中兜底标记时面板必须写明
「按 `# @title` 命中，可能不是 CFG cell」；更好的判据是「cell 内同时出现 `CFG` 与 `{`」优先。

### S11 — `stop-session` 前应当先 flush

定时停机的典型用法是「跑完这几个小时就关机」。关机瞬间 DOM 里还没搬走的最后几行就是结论行。
**改法**：`stop-session` 任务执行前先对 dirty cell 做一次 S3 的 flush（连不上 frame 也要停，但面板要如实写
「停前未能保存」）。这条和「失败绝不清空」是同一个价值取向。

### S12 — notebook 日志会包含密钥（存档里就有）

`rollout.ipynb` 的 CFG 里有私有仓 token / authKey；这次抽到的输出里已经出现 `凭据 TS_AUTHKEY ← …`、
`hub=已配置` 这类行。plan 的 R11 只覆盖**夹具**脱敏，没覆盖**运行期日志**。**改法**：至少
①README 写明「日志可能含凭据，`tmp/` 不入库但要自己注意」；②对 `ghp_`/`Bearer`/`authKey`/长 base64 做**脱敏后再落盘**
（宁可丢一点可读性）；③面板上给「本会话日志」一个「删除」按钮（S2 已要求删备份段）。

### S13 — Colab / AI Studio：要么别 `@match`，要么接受「灰按钮面板」

W5 说「single 角色 boot；两个适配器 `detect()` + 全部 `{ok:false}`」，但 `@match` 列表没定。两种做法语义不同：

- **不加 `@match`**：代码骨架留着，用户看不到任何东西（最干净，推荐 v1）；
- **加 `@match`**：那就会在 Colab 上出现一个全灰面板——plan §7 的 DoD 要求「按钮置灰且 tooltip 说明」，
  等于承诺了一个「看起来支持」的界面。

plan 现在两边都沾。**建议 v1 不加 `@match`**，等探测拿到真实结构再加，并把这句写进 README。

### S14 — 其它小项（逐条一两行）

| # | 问题 | 改法 |
|---|---|---|
| a | 静态路由只写「排在 `/api/*` 前」 | `server-route-table.test.ts:44` 的规则是**任何通配之后不许再出现静态路径** ⇒ 两条新路由要插在 `/api/log/*` 之前 |
| b | 白名单断言只改一处 | `server-route-table.test.ts:34` 的例外条件要同时加 `/api/notebookLog`（POST）与 `/api/notebookLogs`（GET）；后者仍是 GET，不该进 POST 白名单 |
| c | 协议里 `cell` 必填，但 dashboard 侧 clear 是会话级 | `cell` 改可选（`clear` 时省略 = 整会话） |
| d | `session` 白名单 `[a-z0-9._-]{1,64}` | 客户端 `toLowerCase()` + 非法字符替换后再发；服务端 400 必须回显到面板（否则就是静默丢日志） |
| e | CFG 步进扫描无上界 | 加最大步数（如 400）与「取消」；每步等 **两帧** 或等 `MutationObserver`，一帧不够 CM6 渲染 |
| f | 面板在 Kaggle React 树上 | 加 body 级 `MutationObserver`：面板节点被摘掉就重新挂上 |
| g | §4.1 的 runtime API 列表缺 `GM_setClipboard` | §4.8 用到了，`@grant` 与封装层都要列 |
| h | `# segment` 段头信息太少 | 带上 `cellIndex` / `cell` uuid / `mode` / 时间，多 cell 交错时才读得懂 |
| i | `stop` 的「迟到」定义 | 「永不补跑」要写成「迟到超过 grace（建议 10 min）才放弃，10s 内照常执行」；否则 watchdog 晚 20s 也会把该停的跳过 |
| j | 日志页的行级解析 | 抽查的真实输出 `[06:23:04] [rollout-node] …` 恰好命中 `parseLogLine` 的时间/标签规则——是个便宜的好处，段头别写成会被判成 error 的形状即可 |
| k | 交付记录 | AGENTS §11 要的是 `.workbuddy/memory/YYYY-MM-DD.md` **追加** + `MEMORY.md` 持久约定；plan W6 只写了「memory 一行」 |

---

## 4. 协议修订稿（建议直接替换 §4.5）

只列与原稿**不同**的字段与规则。

```jsonc
{
  "v": 2,
  "site": "kaggle",
  "session": "kaggle-huangjian2026-rollout-358870479",
  "notebook": "rollout",
  "writer": "w-7f3a9c",          // 每次页面加载随机；多标签/多帧时是身份
  "epoch": 3,                     // 服务端当前代际；客户端首次 POST 可带 0 让服务端回真值
  "cell": "da0450c9-…",          // clear 时可省
  "cellIndex": 1,
  "seq": 12,                      // 同一 (session, cell, writer) 单调
  "mode": "append" | "reset" | "clear" | "probe",
  "text": "...",                  // 任何模式下单条 ≤ 48 KiB（UTF-8 字节）
  "at": 1790000000000
}
```

| 情况 | 服务端 | 客户端 |
|---|---|---|
| `append`，`epoch` == 当前 | 追加，`lastSeq[cell][writer]=seq`，回 `{ok:true, epoch}` | 前进 `seq` 与 `lastText` |
| `append`，`epoch` < 当前 | **409 `{ok:false, reason:'stale-epoch', epoch}`** | 清 `lastText`，下一条发 `reset`（带新 epoch） |
| `reset`，任意 `epoch` | 写 `# segment` 头 + 全文，`epoch` 对齐，回 `{ok:true, epoch}` | 同 append |
| `seq <= lastSeq[cell][writer]` | `{ok:true, duplicate:true}`，不写盘 | 同成功（幂等重放） |
| `clear` | `epoch++`、截断、写 `# cleared at`、删备份段 | 更新本地 `epoch`，清 `lastText` |
| 单条 > 48 KiB 或字段非法 | 413 / 400（带 `reason`） | 面板红字 + 停止该 cell 的后续发送（不无限重试） |
| 客户端收到 409/413 | —— | **必须发一条可见状态**（面板 + 可选 `mode:'note'` 落盘），不许静默 |

分块（客户端）：

```text
suffix := domText 去掉 lastText 前缀（非前缀 ⇒ 整段 reset）
chunks := 按 [\n | \r] 成行累加，行 > 48KiB 时按 UTF-8 码点硬切
每拍发 min(预算, chunks 剩余)，串行 await，任一失败即停
flush := 循环发送直到该 cell 剩余为空（上限 N 轮；超限 ⇒ 报告「未排空」并禁止清空）
```

`meta.json`（落盘，重启后仍在）：

```jsonc
{ "site":"kaggle", "notebook":"rollout", "epoch":3,
  "cells": { "<cellId>": { "writers": { "w-7f3a9c": 12 }, "bytes": 1048576 } },
  "lastAt": 1790000000000 }
```

> **为什么值得现在改**：B4 的三个坏账（重启去重、清空残段、多标签静默）各自都能用大量补丁绕过去，
> 但加一个 `epoch` + `writer` 就同时闭合三条，而且不增加任何 UI。协议还没实现，现在是最便宜的时候。

---

## 5. 备选与补充评估

### 5.1 原 plan 没考虑的一条：内核 iopub WebSocket 读日志（值得在探测期验一下）

原 plan §11 否掉的是「用内核 WebSocket **执行** cell」——这条我同意（执行必须等价于用户点 Run）。
但**读日志**是另一回事：

- Jupyter Server 的 `GET /api/sessions` + `/api/kernels/<id>/channels`（WS）在 iframe 同源内可达，
  iopub 是**广播**，多一个监听端不抢 UI 的流；
- 拿到的是**原始 stream 消息**：含 stderr、不受 DOM 虚拟化 / `clear_output` / `outputsScrolled` 影响；
- 代价：WS 多路复用帧解码、内核重启要重新 attach、Kaggle 代理是否放行要实测。

**建议**：不换主方案（DOM 简单、和 UI 所见一致），但在 §4.8 的探测 JSON 里加一项「`/api/sessions` 是否 200、
channels 能否连上」。若 R3（日志不完整）真的在训练里咬人，这就是唯一的高保真补救腿；先花 20 分钟验证可达性，
比事后重做便宜。

### 5.2 桥：`MessageChannel` 替代共享 token（同 S8）

优势是三条一起解决：多标签、时序竞态、沙箱里 WindowProxy 身份比较的不确定性。缺点只有一条：
`postMessage` 的 port 不能跨 frame 重载存活——但重载本来就该重新 attach，不是问题。

### 5.3 W4（定时器）的取舍

- 增益最低：Kaggle 自带 `Schedule a notebook to run`，plan 也承认「关页面也能跑」是它的地盘；
- 风险最高：`stop-session` 不可逆，误触发 = 训练中断；
- 建议：**v1 只做「到点 stop」一种任务**（加 S11 的 flush-before-stop 与 grace 规则），`run-cell` 定时放到 v1.1；
  或者 W4 全量但默认 `enabled:false`，并保持 `confirmStop:true`。原 plan 的 O1/O2 已经把「先看真实使用」写对了，
  这里只是把「先看」的范围再收一档。

### 5.4 「让采样 agent tee 日志」为什么仍然不选（同意原 plan，但补一句）

保真度确实更高（进程内直接写、含 stderr、无 DOM 损耗），但：要改训练侧代码、只覆盖自家进程的输出、
与「零侵入」正面冲突。plan 的取舍成立。**唯一的例外**是 R3 成真时——那时它比 iopub 更容易落地。

---

## 6. DoD / 门禁修订清单（可直接勾）

在原 §7 的基础上增删：

- [ ] **B1**：根 `bun run check` / `bun run test` 在**没装** `userscript/node_modules` 的克隆上仍然绿；
      `tests/test-silent-scope.test.ts` 增一组同构断言钉住 userscript 不在根套件内。
- [ ] **B2**：只改 `userscript/` 的提交会跑它自己的 typecheck + test；不改则跳过；报错按 `userscript/` 归因。
- [ ] **B3**：1 MiB 无换行输出 ⇒ 全部落盘、逐字节相等、无 413 循环。
- [ ] **B4**：dashboard 重启后重发同一 `seq` 不重复写；dashboard 侧 clear 之后下一条必是 `reset`
      （文件里没有「半截开头」）；同一 run 开两个标签不丢数据、面板显示写者数。
- [ ] **B6**：夹具里的 stderr 段能被采集；`outputText` 保留 stdout/stderr 的 DOM 顺序。
- [ ] **B8**：把 notebook POST 停掉后，控制台 2 分钟内回到 `idle`（`consoleIdleMs` 可观测）。
- [ ] **S3/S4**：「保存并清空」在运行中的 cell 上连续做 10 次，文件里没有「清空后再也不出现」的尾巴；
      段头写清可能丢失的行数。
- [ ] **S7**：吞吐下限写死一个可测的数（例：5 分钟内 10 MiB 输出不丢字，超出则文件里有 `# lost n bytes`）。
- [ ] **S9**：关掉 host（只在 iframe 里跑）时日志仍然在增长。
- [ ] **§4.7 与 DoD 对齐**：`dashboardBase` 主机名做白名单校验（127.0.0.1 / localhost / ::1），
      否则「不发 POST 到非回环地址」这条 DoD 与「基址可改」自相矛盾；`@connect` 列表是第二道闸。
- [ ] **人工验收顺序**：先只验「Tampermonkey 能不能注入 `*.jupyter-proxy.kaggle.net` 的 iframe」——
      这是整套方案**唯一没被存档证实的物理前提**（存档只能证明 URL 和 DOM 结构）。这一步 30 秒，
      失败则整个 W2 起不来；排在第一项，别排在最后。

---

## 7. 对 W0–W6 的增量改动

| 步 | 要加的 |
|---|---|
| W0 | 门禁排除（B1）与 pre-commit 块（B2）**和 `.gitignore` 一起**进 W0——否则 W0 一提交就把根门禁打红；夹具忠实于「只有 active cell 有工具栏」（B5） |
| W1 | 协议 v2（B4）+ `meta.json` 落 `epoch/lastSeq`；路由插在 `/api/log/*` 之前（S14a）；`notebookLogs` 仍只 GET；`componentName` 前缀映射（S1）；B8 的 guard 变体 |
| W2 | 分块三重边界（B3）；stdout+stderr（B6）；S9 的 referrer 兜底；S6/S7 的积压读数 |
| W3 | `runCell` 先选中（B5）；flush=排空（S3）；clear 前重读（S4）；`MessageChannel`（S8）；面板重挂载（S14f） |
| W4 | stop 前 flush（S11）；grace 规则（S14i）；自动清空「不碰 active cell」（S5）；默认关 |
| W5 | 明确**不加** `@match`（S13）；探测 JSON 加 iopub 可达性（5.1） |
| W6 | DECISIONS 用 `§2026-10-29-goalnn-notebook-userscript`（check-decisions 要求日期 ID 全小写，正文 ≤40 行为宜）；`docs/nn/console.md` §44；`.workbuddy/memory/2026-10-29.md` + `MEMORY.md` |

---

## 8. 接手前先做的三件事（30 分钟，先证伪）

1. **注入**：装一个只有 `console.log(location.href)` 的三行脚本，`@match https://*.jupyter-proxy.kaggle.net/*`，
   确认它真的进到了 `notebook-editor-cells` 那个 iframe（不是只在顶层）。失败 ⇒ 整套方案换路子，别写 W2。
2. **点得着**：在 Kaggle 页面的 Console 里手动
   `document.querySelector('[data-command="notebook:hide-all-cell-code"]').click()`，确认隐藏面板里的 `<li>`
   真的带点击处理器（存档只能证明它在 DOM 里）。同时手动 `GM_xmlhttpRequest` 打一次 `127.0.0.1:8900` 的 GET。
3. **两个未验证的后置条件**：`clear-all-cell-outputs` 是否弹确认框；内核忙态的类名到底是不是
   `.jp-NotebookKernelStatus-busy`。这两条都直接决定 §4.4 的后置条件表，测一次就够。

---

## 9. 结论

- **建议**：按 §2 修完再开 W1；W0 的门禁改动与 `.gitignore` 同批提交。修完之后这份 plan 可以直接派给 agent 执行。
- **评价**：证据 A（可复核、有存档、有陷阱识别）· 设计 B+（失败语义与后置条件写得比多数 plan 好，
  但协议层缺代际与背压）· 现稿可执行性 C+（八条阻断项拖住）→ 修完 B+。
- **最容易被忽略的一条**：B8。它不是功能 bug，而是把一条 2026-10-25 的既有决策（没人看就停刷）悄悄废掉；
  这类「新功能顺手改掉旧约束」的问题，测试不会红，只有读到 `server.ts:368` 才会发现。

---
