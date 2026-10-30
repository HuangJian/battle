# Plan: notebook-userscript — Kaggle/Colab/AI Studio 训练页伴生脚本

> **交付物**：本 plan（设计稿，**未实现**；v3 = 2026-10-29 处理 `plan/notebook.review-luna.md` 之后，
> v2 = 处理 `plan/notebook.review-ds.md` 之后）。
> **触发（用户 2026-10-29）**：训练在浏览器里用 Kaggle / Colab / AI Studio 页面跑，网页功能不够：
> ① 定期把运行中 cell 的 log 存到本机 dashboard；② 存完能清空 log 防内存爆炸；③ 定时启停运行时 +
> 执行指定 cell；④ 折叠展开全部/单个 cell；⑤ 滚动到顶/底；⑥ 定位 notebook 的配置区域（CFG cell）。
> **已定三项**（用户确认）：脚本放**本仓库新子项目 `userscript/`**；「配置区域」= **notebook 里的 CFG cell**；
> 第一版**只做 Kaggle**，Colab / AI Studio 只留适配器骨架。
> **评审处置**：`plan/notebook.review-ds.md` 的 8 条阻断项 + 14 条缺口 → **§12**；
> `plan/notebook.review-luna.md` 的 3 条 P0 + 7 条 P1 → **§13**（含 4 处实测量出来的修正：Bun 请求体上限、
> 后台租约、前缀哈希成本、探测 URL）。
> 本文所有「实测」都标了来源，未验证的明确写「未验证」（不拿推测当证据）。
> **阅读顺序**：§1 可行性 → §2 实测证据 → §3 六需求规格 → §4 架构与协议 → §5 实施 → §6 测试 →
> §7 DoD/门禁 → §8 风险 → §9 未决 → §10 给接手 agent 的一句话 → §11 被否决的备选 →
> §12 评审处置（review-ds）→ §13 评审处置（review-luna）。

---

## 0. 一句话目标

**在不改训练代码、不要求云端能回连本机的前提下，用浏览器当传输通道：把 cell 输出定期落到本机
dashboard，并给笔记本页补上「按需清空、定时执行、折叠、跳转」这一层操作面板。**

---

## 1. 可行性结论（先给答案）

| # | 需求 | 判定 | 依据（实测） | 关键限制 |
|---|---|---|---|---|
| 1 | 定期保存运行中 cell 的 log 到 dashboard | ✅ 可行 | Kaggle 的 cell 输出就在 DOM 里（`<pre>` 全文）；页面与本机 dashboard 同机，脚本可直接 POST 回环地址 | 只截「脚本运行期间 DOM 里还在的输出」；后台标签页定时器被节流 |
| 2 | 保存后清空 log | ✅ 可行 | `notebook:clear-all-cell-outputs` / `notebook:clear-cell-output` 在命令表里 | 清空不可逆，必须「先确认保存成功」才允许清；单 cell 命令作用于 active cell（§3.2 有交互闸） |
| 3 | 定时启停运行时 + 执行指定 cell | 🟡 有条件可行 | 停止 = 外层页 `aria-label="Stop session"`；执行 = 先选中 cell，再 `notebook:run-cell` | 标签页必须开着；Kaggle 没有独立开机键；Stop 可能有确认弹窗 |
| 4 | 折叠/展开 全部或单个 cell | ✅ 可行 | `notebook:hide-all-cell-code` / `show-all-cell-code`；单 cell 有 `.jp-InputCollapser` | 虚拟化：未渲染的 cell 不在 DOM，单 cell 操作要先滚到它 |
| 5 | 滚动到页面顶/底 | ✅ 可行 | 笔记本滚动容器是 `.jp-WindowedPanel-outer` | 滚动发生在 iframe 内，不在外层页 |
| 6 | 定位 CFG cell | ✅ 可行 | CFG cell 是 `CFG = {` 开头的普通 code cell；命令表/滚动容器都可驱动 | CodeMirror 只渲染可视行，需按视口步进扫描；标记必须在 cell 顶部附近 |

**一句话结论**：六项都能做。工程难点是四个——**Kaggle 的笔记本在跨域 iframe 里**、**cell 是虚拟化渲染的**、
**日志必须增量搬运且失败绝不清空**、**长行/清空代际/多标签写者会让朴素协议静默丢账**。§4 各有对策；
其中第四点已按评审升级为协议 v2（§4.5），**不是**「先写再补」。

---

## 2. 实测证据（不靠猜）

### 2.1 Kaggle 编辑页是「外层 React + 内层 JupyterLab iframe」双层结构

存档 `D:\github\Kaggle-Notebook-Editor.mhtml`（2026-10-29 抓取，URL 形如
`https://www.kaggle.com/code/<owner>/<slug>/edit/run/<runId>`）解出 220+ 个 part，其中两个 HTML 帧：

| 层 | Content-Location | 关键事实 |
|---|---|---|
| 外层 | `https://www.kaggle.com/code/...` | 会话状态与按钮都在这层：`aria-label="Stop session"`、`aria-label="Factory reset"`、`aria-label="Stop execution"`（Cancel Run）、`aria-label="Run current cell"`；状态文案 `Draft Session (5h:55m)`；右栏有 `Session options` 面板与 Kaggle 原生 `Schedule a notebook to run` |
| 内层 | `https://kkb-production.jupyter-proxy.kaggle.net/static/assets/jupyterlab-v4/jupyterlab-index-*.html?session=<JWT>` | 真正的 notebook：JupyterLab v4。`<iframe name="notebook-editor-cells" title="Notebook editor cells">`，容器 `[data-testid="notebook-editor-jl-frame-render-tid"]` / `#frame-container-element` |

⇒ **跨域 iframe**：外层脚本读不到 cell，内层脚本读不到会话按钮。必须双实例 + `postMessage` 桥（§4.2）。

### 2.2 JupyterLab 的 cell / 输出 / 命令（内层帧实测）

- 根：`.jp-Notebook`（同时带 `jp-WindowedPanel`、`jp-mod-scrollPastEnd`）；滚动容器 `.jp-WindowedPanel-outer`，
  内容层 `.jp-WindowedPanel-inner`，虚拟化视口 `.jp-WindowedPanel-viewport`。
- cell：`.jp-Cell[data-uuid][data-windowed-list-index]`，code cell 带 `.jp-CodeCell`；
  `data-windowed-list-index` = notebook 里的 0 基序号；`data-uuid` = 稳定身份（虚拟化后仍是同一个）。
- 输入：`.jp-InputArea` + `.jp-CodeMirrorEditor`（CM6，`.cm-content`，只渲染可视行）；
  折叠开关是 `.jp-InputCollapser` / `.jp-OutputCollapser`（内部 `.jp-Collapser-child`）。
- 输出：`.jp-OutputArea` → `.jp-OutputArea-child` → `.jp-RenderedText[data-mime-type="application/vnd.jupyter.stdout"] > pre`。
  **实测一条 stream 输出就是单个 `<pre>`，全文在内，行尾是 `\r\n`**；cell 带 `jp-mod-outputsScrolled` 时输出区只是加滚动条，不丢文本。
- **stderr 不在本存档里**（只有 `text/markdown` + `application/vnd.jupyter.stdout`）——这是**覆盖缺口**，不是「不存在」：
  Python traceback、`logging` 默认 handler、Kaggle 告警都走 `application/vnd.jupyter.stderr`。
  §3.1 的采集必须按 DOM 顺序收 stdout + stderr（+ 可选 `text/plain` 的 execute_result）。
- **行不是天然的分块边界**：实测 17.6 KB 的 stdout 段里存在**两条日志之间没有任何换行**的相邻
  （`…alive (0 min)[06:23:04]…`）；该段最长行 233 字符、229 个 CRLF。tqdm 更会只用 `\r` 推进。
  ⇒ 分块不能假设「一行 ≤ N 字节」（§3.1）。
- 工具栏与 run：`<jp-toolbar class="jp-cell-menu jp-cell-toolbar">` 只有 **1 个**（2 个 cell），
  但它**不是**跑 cell 的按钮所在。实测 run 项是
  `div[data-jp-item-name="run"] > jp-button[data-command="runmenu:run"]`
  （`aria-label="Run this cell and advance (Shift+Enter)"`），住在**笔记本工具栏的 responsive popup**
  （`jp-Toolbar-responsive-popup lm-mod-hidden`）里；**没有**单 cell 的 clear-output 按钮。
  `notebook:run-cell` 不在工具栏（命令面板的 `<li>` 上有，菜单里也可能有）。
  ⇒ run 始终作用于 **active cell**，"按 cell 找 run 按钮"不存在 ⇒ `runCell` 必须**先选中**（§4.4）；
  夹具也要忠实复刻（§6.3）。
- 笔记本工具栏的 `data-jp-item-name="collapse-all"` 是 **Collapse All Headings**（`toc:toggle-collapse`），
  **不是**「折叠全部代码」——别按名字猜。
- 命令面板在 DOM 里已经铺好全部命令：`li[data-command="<id>"]`（面板隐藏也能查到）。实测存在的 id：

| 用途 | command id |
|---|---|
| 折叠/展开全部代码 | `notebook:hide-all-cell-code` / `notebook:show-all-cell-code` |
| 折叠/展开全部输出 | `notebook:hide-all-cell-outputs` / `notebook:show-all-cell-outputs` |
| 折叠/展开当前 cell | `notebook:hide-cell-code` / `notebook:show-cell-code`（输出同理） |
| 清空全部输出 | `notebook:clear-all-cell-outputs` |
| 清空当前 cell 输出 | `notebook:clear-cell-output` |
| 运行 | `notebook:run-cell` / `notebook:run-all-cells` / `notebook:run-all-above` / `notebook:run-all-below` |
| 选择/移动 | `notebook:select-all` / `notebook:move-cursor-up` / `notebook:move-cursor-down` |
| 内核 | `notebook:interrupt-kernel` / `notebook:restart-kernel` / `notebook:restart-clear-output` |

- **虚拟化实证**：存档里只有 2 个 cell（`data-windowed-list-index=0` 的 markdown + `=1` 的 code），
  而 notebook 明显不止两格 ⇒ 不在视口里的 cell 压根不在 DOM。
- **未验证**：空闲存档里没有 `jp-NotebookKernelStatus-busy`（只有 `jp-NotebookKernelStatus` / `-fade` / `-info`）；
  `clear-all-cell-outputs` 是否弹确认框也没证据。二者都只能作**可选**后置条件（§4.4），靠探测模式确认。

### 2.3 dashboard 侧已有的面（改动面比看起来小）

- 路由表是唯一事实源（`dashboard/src/server/route-table.ts`）：`/log/*` GET（日志页）、`/api/log/*` GET（日志载荷）、
  `/api/*` POST（全部动作都进 `routeAction`）。`buildRouteTable` 在启动期校验「表里有 ⇒ handler 必有」。
- 日志页是**组件制**：`componentLogPayload(key, lines)` → `renderLogPage(payload, {components, follow, lines})`；
  客户端轮询 `/api/log/<initial.component>`。`LogPageOptions.components` 的类型只是
  `Array<{key,label,status}>`（字符串），**可以塞虚拟键**——这是 §4.6 免新建页面的关键。
  但注意两件事（评审 S1）：
  1. 导航渲染用的是 `componentName(c.key)`，**`label` 根本没被读**（`dashboard/src/web/app/log-app.tsx:315`）；
     `componentName` 对未知 key 回退成 key 本身（`dashboard/src/web/view/component-roles.ts:67`）
     ⇒ 不处理就会显示 `notebook:kaggle-…` 原文。修法：`componentName` 加 `notebook:` 前缀映射。
  2. `componentLogPayload(key: Component, …)` 的 `key` 是**联合类型**（`src/core/types.ts:160`）⇒ 虚拟键必须在
     `logPageRoute` / `apiLogRoute` 里**先分支**，不能直接喂给 `componentLogPayload`。
- **POST 有回环门控**：`server.ts` 的 `guard` 用 `isReadonlyAction(method, requestIP)`，局域网只能 GET。
  浏览器在本机 ⇒ 请求源 IP 是 127.0.0.1，能过；从别的机器开 Kaggle ⇒ 被 403（§8 风险）。
- **没有 CORS 头**：跨源页面直接 `fetch` 会被浏览器拦。用 `GM_xmlhttpRequest` 绕过（§4.5），不走 fetch。
- **`routeAction` 对每个动作落一行 `[action]` 决策日志**：高频日志搬运复用它 = 把 dashboard 自己的日志刷爆。
  所以新开一个静态 POST 端点，不进动作层。
- **`guard` 每个请求首行 `markConsoleActivity()`**（`server.ts:368`）：刷新器的「没人看就停刷」闸
  （`snapshot-refresher.ts:228`，2 分钟）就靠它。5s 一条的日志 POST 会把「有人在看」永久点亮 ⇒
  **静默废掉 2026-10-25 的既有优化**（评审 B8）。新路由必须用**不记活动**的 guard 变体。
- `tmpLogsDir()` 可被 `BCITY_TMP_LOGS_DIR` 重定向（测试用），`tmp/` 已在 `.gitignore`；新路径必须走
  `tmpLogsDir()`（`paths.ts` 里记着 31 处硬编码 `tmp/` 的前车之鉴）。
- 门禁细节：`dashboard/tests/server-route-table.test.ts` 断言「除 `deliverUpload` 与 `/api/*` 外的 `/api/*`
  只能是 GET」**且**「任何通配之后不许再出现静态路径」⇒ 新端点要**插在 `/api/log/*` 之前**，并同批把
  `/api/notebookLog` 登记进 POST 白名单（`/api/notebookLogs` 仍是 GET，不进）。

### 2.4 为什么用浏览器搬运，而不是让 cell 自己上传

cell 跑在 Kaggle 云端，**回不到本机 127.0.0.1**；要直连就得先有隧道（hub 已有这门基建，但只给
worker 用）。浏览器是唯一「天然直连本机 dashboard、又不需要改训练代码」的通道：
页面在本机打开 ⇒ `GM_xmlhttpRequest` → `127.0.0.1:8900`。
代价是只能拿到「DOM 里还在的输出」（§8-R3），换来的是**零侵入**：不改 cell、不碰 hub、不影响训练。

---

## 3. 六个需求的规格（先定死再写码）

### 3.1 定期保存 cell log

- **范围**：所有 **code cell** 的输出（不只是「正在跑的那个」）。合并成一条**按时间排序的流**写进同一会话文件；
  多 cell 交错是已知限制（段头带 cell 身份，§4.5/§4.6）。
- **触发**：`MutationObserver` 挂在 `.jp-Notebook` 子树（`childList` + `characterData`），**逐条 record 过滤**：
  目标在 `.jp-OutputArea` 里 ⇒ 把该 cell 的 `data-uuid` 记入 dirty；目标是 notebook / cell 列表本身 ⇒ 只刷新
  「已渲染 cell 集合」（虚拟化进出），不标 dirty；目标在 `.jp-InputArea` / `.cm-content` / `.jp-InputCollapser`
  ⇒ **忽略**（否则每敲一个键都把整个 cell 标脏，luna P1-5）。每 `shipIntervalSec`（默认 **5s**）只读 dirty
  cell 的输出文本，其余 cell 零成本。
- **归一**：`outputText()` 把 `\r\n` **和孤立的 `\r`** 都归一成 `\n`（tqdm 的进度用 `\r` 推进；不归一就会变成
  一条巨长的行）。**归一在算字节/切块之前**——客户端算的和服务端验的必须是同一个字符串。
- **采集顺序**：按 DOM 顺序遍历 `.jp-OutputArea-child`，收 stdout + stderr；mime 变化时写一行
  `# [stderr]` 标记（**不含** error/warn 字样，见下方「段头卫生」），保证 traceback 不会丢、交错顺序不乱。
- **增量状态**：每个 cell 只存 `{offset, tailHash}`，**不存全文**（评审 S6 / luna P1-5）：
  - `offset` = DOM 归一文本里「已落盘前缀」的末尾；`tailHash` = `hashRange(dom, max(0, offset-256), offset)`
    ——**尾部 256 字符的锚**，不是整段前缀。
  - 每拍：`dom.length >= offset` 且 `hashRange(dom, offset-256, offset) === tailHash` ⇒ 后缀 =
    `dom.slice(offset)`；否则（被清空 / 改写 / 长度回退）⇒ 整段 `reset`（`offset=0`）。
  - **为什么不再 hash 整个前缀**：`hashRange(dom,0,offset)` 是 O(历史长度)/拍，恰好在大输出 cell 上最贵；
    尾部锚是 O(256)/拍。代价是「前缀深处被改写、但长度不变且尾部 256 字符也不变」会漏检——Jupyter 的
    stream 输出只追加（`\r` 归一后 tqdm 也只增行），`clear_output` 必然让长度回退 ⇒ 实测路径上不可达；
    残余风险写进 §8-R3。**不靠 `startsWith` 单点判断**：前缀被改写时它看不出来。
  - 内存 O(cell 数)，不随日志总量涨（这正是「防内存爆炸」的一半）。
  - hash 用 FNV-1a 64-bit over UTF-16 code units，`hashRange` 直接扫区间**不切片**（§14.1 的分配纪律）。
    碰撞（1/2^64）的后果是漏掉一次原地改写，属可接受残余风险；`reset` 方向只重发、不丢字。
  - **仍有的 O(n)**：`outputText()` 必须整段 `textContent`（DOM 没有增量读接口）⇒ 每拍对 dirty cell 仍有
    一次 O(长度) 分配。三道笼子：① observer 只盯 `.jp-OutputArea`（输入区打字不标脏）；② 自动清空阈值
    1 MiB 本身就是上限；③ 单 cell 归一文本 > `largeCellBytes`（8 MiB）⇒ 降频到每 `largeCellIntervalSec`
    （30s）读一次，面板显示「大输出降频」。DoD 只承诺「扫描 O(新字节 + 256)」，**不承诺**「读 DOM 零成本」
    ——后者 DOM API 给不了，写在 §7 而不是藏着。
- **分块**（评审 B3）：单条 POST ≤ **48 KiB**（UTF-8 字节，归一后计算）。切分规则：
  1. 先按 `\n` 边界累加，凑满 48 KiB 就发；
  2. **单行本身超 48 KiB ⇒ 在码点边界硬切**（不切断 surrogate pair / emoji / 中文）。
  服务端只做**端点自读的上限**（512 KiB body，见 §4.5），不是客户端的切分依据；「单行超限不切行」这条**已删除**
  （它与「单条 ≤ 48 KiB」互相否定，且真实输出里存在无换行长段）。
- **预算与背压**（评审 S7）：每拍**全局**预算 `maxBytesPerTick = 1 MiB`（不是每 cell 3 块），串行 await。
  某 cell 积压 > `backlogDropBytes`（8 MiB）⇒ **跳到最新**：只保留末尾 `keepBytes`（1 MiB），
  `offset` 同步跳过去，下一条发 `reset` 并带 `dropped = <丢弃字节数>`；服务端在段头写
  `dropped=<n>`。**明确丢、不装没丢**；面板显示「积压 N KiB / 上次落盘 T」。
- **失败语义**：POST 失败只更新面板状态；**失败的那一块**不前进 offset/seq（下一拍用同一 seq 重发），
  同一批里**已成功**的块照常前进（串行发送，不会越序）。服务端按 `(session, cell, seq)` 幂等去重。
  400/413 属**永久失败** ⇒ 该 cell 停发并在面板红字报原因（不无限重试）；409 见 §4.5 的分支。
- **首拍**：`mode:'reset'` 发全量，服务端写一条 `# segment ...` 头；之后 `mode:'append'`。
- **可见性**：cell 被虚拟化移出 DOM 时保留 `{offset, tailHash}`（key 是 uuid）；重新渲染后继续比较。
  若 DOM 变短/改写 ⇒ 下次 reset（不重复落盘）。
- **后台**：标签页隐藏时定时器会被节流（Chrome 可到 1 次/分钟）。因为读的是「累计文本」而不是流，
  节流**只延迟、不丢字**（超过积压上限时按 `dropped` 显式丢，见上）；自动清空是否触发只看 §3.2 的交互闸
  （隐藏时通常没人看 ⇒ 允许清，正是省内存的时候）。

### 3.2 保存之后清空 log

两个层面，分开做：

| 层 | 动作 | 规则 |
|---|---|---|
| 页面 | 「保存并清空」 | 对**所有** dirty cell 做 **drain**（循环发到「该 cell 没有未发后缀」为止；上限 60s / 64 MiB），任一块失败 ⇒ 中止并提示，**绝不清**；只有真正排空才清 |
| 页面 | 自动清空 | 阈值触发：某 cell 输出 > `autoClearBytes`（默认 1 MiB）且**已成功排空**，才清该 cell |
| 页面 | 单个清空 | 面板「清空当前 cell（先保存）」；同上先排空后清 |
| dashboard | 清空该会话日志 | `mode:'clear'` 截断 + **删除 `.1.log` 备份段** + 写 `# cleared at <iso>`（评审 S2：只截当前段 = 磁盘没释放） |

- **drain ≠ 「3 块 200」**（评审 S3）：`flush` 是一个循环，直到 `offset` 追上 DOM 末尾；超限就报
  「还在追，暂不清空」并**拒绝**清空。
- **清空前的竞态**（评审 S4）：排空后**重新读一次 DOM**；若又有新文本 ⇒ 再 drain 一轮（最多 2 轮），
  然后才发 clear。清空后写一行 `# cleared at <iso> cell=<id>`（作为普通 append，`seq+1`）；
  **不估「丢了几行」**——无法知道，宁可如实写时间。
- **自动清空的闸**（评审 S5 / luna P1-4；不采纳「默认关」，那与用户诉求②冲突；也**不采纳**「目标就是
  active cell 就跳过」——active 时选中是 no-op，真正的风险是抢焦点）：
  1. **用户正在交互** ⇒ 跳过本拍：最近 10s 有键盘/指针/滚动事件，**或**当前有非折叠选区，**或**焦点在
     `.cm-content` 里。**单看 `document.hasFocus()` 不算**——用户把 notebook 留在前台走开时它是常量
     true，会让自动清空无限期跳过（luna P1-4 的一半是对的）；换成「有交互迹象」判据后，空闲 10s 就恢复；
  2. 该 cell 还有未排空积压 ⇒ 跳过；
  3. 闸 1 通过才允许「选中目标 → 清空」；目标本来就是 active 时跳过选中这一步。
     清空 = （必要时选中）→ `notebook:clear-cell-output` → 后置条件「该 cell 输出节点数 == 0」；
     用户正在别的 cell 打字时永不抢选区（闸 1）。
  - **延期必须可观测**：`deferredSince` 记第一次被闸 1 挡下的时刻；> `autoClearDeferWarnSec`（60s）面板显示
    「自动清空延期 Ns（你在操作 notebook）」；积压 > `autoClearForceBytes`（8 MiB）时转红色横幅 + 一键
    「立即保存并清空」。**不做「到点强抢焦点」**：清非 active cell 必须先选中（没有单 cell clear 按钮），
    抢选区比延期更糟；用户持续操作时的自动回收本来就不可能无损。
  - **能力边界写进 UI**：清空只减少「已落盘输出在 DOM 里的滞留」，**不承诺**浏览器总内存硬上限（Jupyter
    自己的模型/撤销栈不归我们管）。
- **运行中清空的无损边界**（luna §2「已确认边界」）：两轮重读只能缩小窗口，DOM clear 与 kernel 输出之间
  没有同步屏障 ⇒ **不承诺**运行中的 cell 在「最后一次读取 → 点 clear」之间新产生的输出无损。已知的
  POST/drain 失败仍**必须**中止清空（不把明确失败伪装成成功）。注：评审称「用户已明确不需要无损」，该原始
  表述在本 plan / 对话记录里无法核实，故这里按**设计边界**采纳，不当作授权依据（§13）。
- **清空后客户端状态**：该 cell `offset=0, tailHash=0`、本地 `seq` 继续递增；下一段非空输出以 `reset` 开始。
- **为什么阈值而不是「存完就清」**：用户还在看 cell 输出时每 5 秒清一次会没法调试；阈值保证只有失控输出
  （真正吃内存的那种）才被回收。

### 3.3 定时启动/关闭运行时 + 执行指定 cell

- **任务模型**（GM 存储，按站点分组；v1 只有本地时区）：
  `{id, at: 'HH:MM', days: 'daily'|'weekdays'|'1,3,5', action: 'run-cell'|'run-all'|'stop-session'|'interrupt',
    notebookKey, sessionAtCreate,
    target?: {uuid?: string, index?: number, fingerprint?: string}, enabled,
    occurrences: {'<localDate>T<HH:MM>': {state, by, at, reason?}}}`。
- **时间语义**（luna P1-7，逐条定死）：
  - 时区 = **设备本地时区**（不提供 per-job TZ）；创建页显示 `UTC±HH:MM` 与「下次触发 = …」。
  - `days` 编号 = ISO-8601：**1=周一 … 7=周日**（`Date.getDay()` 的 0=周日要映射）；`weekdays` = 1–5。
  - occurrence 键 = `<YYYY-MM-DD>T<HH:MM>`（本地墙钟，存在 job 的 `occurrences` 里）。同一键**最多执行一次** ⇒ 秋季回拨的重复
    01:30 只跑一次；春季跳过的 02:30 **不执行**（该墙钟不存在），下一拍照常，不改期。
  - 迟到：`run-*` ≤ 10 min 照常；`stop-session` ≤ `stopGraceMs`（10 min）照常，超过记 `skipped(late)`。
  - 恢复：刷新 / 睡眠唤醒后，`nextFireAt` 只依赖「当前墙钟 + occurrence 键」，不依赖内存里的计时器链。
- **occurrence 状态机（跨标签只执行一次）**：`pending → claimed → succeeded|failed|skipped`。
  1. 到点先写 `{state:'claimed', by:<writer>, at}` 到 GM 存储（共享、持久，晚到的标签能看见）；
  2. 抖动等待 `claimSettleMs`（默认 1000ms）后**再读一次**：不是自己 ⇒ 退让；仍是自己 ⇒ 执行（两个标签的
     `setTimeout` 通常在同毫秒级触发，1s 沉淀窗足以让后一次写覆盖前一次）；
  3. `lastFiredAt` 在 **claim 时**写（不是执行后）——页面崩在动作中也不会重复执行；`claimed` 超过 5 min
     没有结果 ⇒ 面板显示「结果未知」，不自动补跑。
  - **不是原子 CAS**（GM 存储没有 compare-and-swap）：这是 best-effort 互斥，靠「持久 claim + 1s 沉淀 +
    重读」把窗口压到可忽略；**不**用 `BroadcastChannel`（沙箱透传未验证）。
- **页面 / 目标身份**：`run-*` 绑定 `notebookKey`（`<owner>/<slug>`，跨 run 稳定）；`stop`/`interrupt` 绑定
  `sessionAtCreate`（含 runId）——切到另一个 run 时**不执行**（fail closed，面板写原因）。
  `target` 在创建时捕获 `{uuid, index, fingerprint}`（fingerprint = 前 200 字符的 FNV-1a）：触发时
  uuid 命中且指纹一致 ⇒ 用；uuid 缺失 ⇒ 用 index 命中且指纹一致；多处命中 / 指纹不符 / 找不到 ⇒ **不执行**。
  index 只作为 uuid 失效后的兜底，且必须指纹一致（cell 插入/删除后 index 会指到别的代码，luna P1-3）。
- **「启动运行时」**：Kaggle **没有**独立开机按钮，跑 cell 就是开机（会话懒启动）。所以 `run-cell` / `run-all`
  同时承担「启动 + 执行」；面板文案写「运行 cell（必要时自动开会话）」，不假装有开机键。
- **停止**：外层帧点 `button[aria-label="Stop session"]`；若 2s 内出现 `[role="dialog"]`，找其中
  文案匹配 `/stop|shut down|confirm|ok|确定|停止/i` 且非 cancel 的按钮点一次；找不到就报
  「已点击，未见确认框」——不假装成功。
- **停前先 flush**（评审 S11）：`stop-session` 执行前对 dirty cell 做一次 §3.2 的 drain（上限 30s）；
  frame 连不上也必须停，但面板/日志要如实写「停前未能保存」。
- **计时**：`setTimeout` 到点 + **30s watchdog** 兜底（后台节流/系统睡眠会漏拍）；`visibilitychange` 时重算。
- **迟到规则**（评审 S14i，把「永不补跑」写清）：`run-*` 迟到 ≤ 10 min 照常执行；
  `stop-session` 迟到 ≤ `stopGraceMs`（默认 **10 min**）照常执行，超过才放弃并记 skipped——
  watchdog 晚 20s 不该把该停的跳过。
- **安全闸**：任务触发时在面板显示 10s 倒计时 + 「取消」；`stop-session` 的确认放在**创建任务时**
  （勾选「到点会停止会话」= `confirmStopOnCreate`），**不**放在每次触发——后台标签页弹不出 `confirm()`，
  若那时直接 skipped，定时关机在最常见的「跑着训练、标签页在后台」场景下永远不会执行。
  隐藏时按预授权执行并记录；可见时仍给 10s 取消窗。
- **明确不承诺**：浏览器关掉/标签页被丢弃后不会执行。Kaggle 原生 `Schedule a notebook to run`（存档里就在右栏）
  才是「关页面也能跑」的正解，plan 文档与面板都要写这句。

### 3.4 折叠/展开（全部 / 单个）

- **全部代码**：`notebook:hide-all-cell-code` / `notebook:show-all-cell-code`；
  **全部输出**：`notebook:hide-all-cell-outputs` / `notebook:show-all-cell-outputs`。
  这是**模型级**命令，对未渲染的 cell 也生效——虚拟化不影响「全部」的语义。
- **单个**：直接点该 cell 的 `.jp-InputCollapser` / `.jp-OutputCollapser`；无效果（DOM 变化即后置条件不成立）⇒
  退化为「选中该 cell（点 `.jp-InputArea`）→ 命令 `notebook:hide-cell-code` / `show-cell-code`」。
- **折叠形态**（用户 2026-10-30 追加）：折叠后每段**保留顶部两行**，代码段蓝色、Markdown 段琥珀色，
  右上角分别带 `CODE` / `MD` 标签。`hide/show-*-cell-code` 会把输入整段藏掉（留不下两行）⇒ 折叠改由脚本
  自己的样式接管（`#bnc-cell-fold-style` + `data-bnc-fold`）：`全部` 写 notebook 根的
  `data-bnc-fold="all"`（虚拟化后渲染的 cell 也生效）；`单个` 写 cell 的 `on|off`，`off` 在全局折叠时
  覆盖成展开；样式用 `max-height: calc(2lh + 4px)` 裁出两行。折叠态的每个 cell 右上角有真实的
  「展开」按钮（`bnc-unfold`，同时充当 CODE/MD 标签），点一下只展开这一段；MutationObserver 在
  虚拟化重渲染/新 cell 进 DOM 时把按钮补回来。切换折叠前先发
  `notebook:show-all-cell-code` / `show-cell-code` 还原 Jupyter 原生折叠，避免两种折叠叠在一起。
  只加脚本私有属性，不改站点 DOM 结构。
- **目标 cell**：默认 `.jp-Cell.jp-mod-selected`，没有则 `.jp-mod-active`；面板给「当前 cell」字样，不猜用户意图。
  未渲染的目标先 `ensureRendered(index)`（§3.6 的同一套步进滚动）。

### 3.5 滚动到顶/底

- 容器：`.jp-WindowedPanel-outer`；`scrollTop = 0` / `scrollHeight`，再补发一次 `scroll` 事件（虚拟化据此重算）。
  容器不唯一（不同布局/版本可能是 `.jp-Notebook` 或文档本身）⇒ 按候选列表逐个试，**读回 `scrollTop` 验证**
  （overflow 非 auto/scroll 的容器赋值无效，不算成功）；全失败再对首/末个已渲染 cell `scrollIntoView`。面板状态
  打好给实际命中的容器（`scrollTop → jp-WindowedPanel-outer@0`），否则无法定位「点了没反应」。
- 平滑可选（`behavior:'smooth'`），但**后置条件**用 `scrollTop` 判定，不等动画结束。
- 外层页也提供「滚动外层到顶/底」（`window.scrollTo`）——Kaggle 侧栏/输出面板很长时有用，与 notebook 滚动分两个按钮。

### 3.6 定位 CFG cell

- **标记**：内置启发式先跑（某行以 `CFG` 开头且含 `{`，覆盖 `CFG = {` / `CFG={` / 多余空格），
  再按面板配置的**字面子串**顺序匹配（默认 `CFG = {` → `CFG={` → `# @title`）。
  仓库 `nn-training/ipynb/rollout.ipynb` 实测：cell 1（0 基）第一行 `# @title ...`，`CFG = {` 在第 5 行。
- **兜底必须自曝**（评审 S10）：命中 `# @title` 时面板写明「按 `# @title` 命中，**可能不是 CFG cell**」；
  因为 Colab 风格里几乎每个 cell 都可能有 `# @title`。
- **算法**：
  1. 先扫已渲染 cell：取 `.cm-content` 的 `textContent`（折叠时仍可读；**不用 `innerText`**，隐藏元素返回空）；命中即停。
  2. 未命中 ⇒ 从 `scrollTop=0` 开始按 **0.8 × 视口高**步进，每步等 **2 帧**（一帧不够 CM6 渲染）或等
     `MutationObserver`；命中即 `scrollIntoView({block:'center'})`。
  3. 上界 **400 步** + 面板「取消」；到底仍未命中 ⇒ 回顶部，报「未找到标记」并提示改标记（不静默失败）。
- **为什么能成**：步长 < 视口 ⇒ 每个 cell 的顶部至少完整进入视口一次；CM6 会渲染进入视口的行的顶部若干行。
  **限制**：标记若在超长 cell 的深处（> 一屏）可能扫不到——写进 §8-R3，并允许把标记改成 cell 顶部那行 `# @title`。
- 命中后加脚本私有的高亮 class（注入 `<style>`，1.5s 后移除），不改站点 DOM 结构。记住上次命中的 index（GM 存储）。

---

## 4. 架构

### 4.1 位置与边界：`userscript/` 是独立 bun 项目

```
userscript/
  package.json            # battle-notebook-companion；scripts: check/typecheck/lint/test/build
  bunfig.toml  tsconfig.json  happydom.ts
  scripts/build-userscript.ts   # 照抄 grease-monkey-scripts 的「元数据块 + debug/prod 两遍」构建
  scripts/test-silent.ts
  src/runtime.ts           # Runtime 适配器：GM_xmlhttpRequest / GM_getValue / GM_setValue /
                           #   GM_deleteValue / GM_registerMenuCommand / GM_setClipboard /
                           #   addEventListener / location —— 所有平台 API 只在这里
  src/utils.ts             # 纯函数（escape、clamp、hashRange、按码点切分）
  src/notebook-companion/
    index.user.ts          # ==UserScript== 元数据 + 启动入口（薄）
    config.ts  protocol.ts  selectors.ts
    app/{boot,bridge,scheduler,log-shipper,commands,probe}.ts
    ui/panel.ts  ui/styles.ts
    sites/{types,kaggle,colab,aistudio}.ts
  test/{runtime.ts, fixtures/kaggle-{outer,frame}.html, *.test.ts}
  dist/                    # 构建产物（.gitignore 已忽略 dist/，不入库）
```

**边界规则**（AGENTS §3）：`userscript/` **不得 import `src/` 或 `dashboard/src/`**；与 dashboard 只通过 HTTP 契约
（§4.5/§4.6）对话。反向也禁止 dashboard 引它。新子项目 + 新 devDeps + 第二个 typed POST 端点 → 一条 DECISIONS。

**依赖**：运行时零依赖（面板手写 DOM + ShadowRoot，不引 Preact）；devDeps =
`@types/bun` + `happy-dom` + `@happy-dom/global-registrator` + `oxlint`（与根同版本；否则 `bun run lint`
会退化成联网 `bunx`）。`bun fmt` 是 Bun 内置（≥1.4.4），不需要依赖。

**门禁边界（评审 B1/B2，必须在 W0 一起落地）**：

- 根 `tsconfig.json` 只 include `src/tests/tools` ⇒ **typecheck 不覆盖** userscript（对），但
  **根测试会枚举它**（错）：
  - `tools/run-root-tests.ts:23` 只排 `dashboard/**`；
  - `tools/test-silent.ts:65` 的 `SKIP_RE` 只排 dashboard，`isDashboardOnly` 也只认 dashboard。
  - 新克隆只跑根 `bun install` 时，`happy-dom` 解析失败 ⇒ 根 `bun run check` 直接红（根 devDeps **没有** happy-dom）。
- **实测口径**（Bun 1.4.4）：`--path-ignore-patterns` **重复传两个 flag 生效**；**逗号分隔不生效**
  （`--path-ignore-patterns=a,b` 一个都不排）。所以 `run-root-tests.ts` 要**追加第二个 flag**，不是拼逗号。
- 改法：`SKIP_RE` 加 `userscript`；`isDashboardOnly` 泛化为 `isIndependentOnly`（`dashboard` + `userscript`），
  旧名保留一行别名；`tests/test-silent-scope.test.ts` 照抄一组同构断言钉住。
- pre-commit：`ROOT_TS_STAGED` / `FREEZE_STAGED` 都要 `grep -v '^userscript/'`；再加一个 `userscript` 门禁块
  （仿 dashboard 块）：`US_STAGED` 非空 ⇒ `cd userscript && bun run typecheck && bun run test`，
  逃生口 `SKIP_USERSCRIPT_GATE=1`，报错按 `userscript/` 前缀归因。根 hook 的 `bun fmt --staged` 与
  `oxlint` 仍会扫到它（风格对齐仓库：无分号、单引号、printWidth 100）。
- `.gitignore` 的 `*.lock` 会挡 `userscript/bun.lock` ⇒ 加 `!userscript/bun.lock`（照 `dashboard/bun.lock` 的先例）。

### 4.2 双实例：宿主帧 + 笔记本帧

Kaggle 的 cell 在跨域 iframe，外层读不到 ⇒ 按 URL 分角色：

| 角色 | `@match` | 职责 |
|---|---|---|
| host | `https://www.kaggle.com/code/*` | 面板 UI、会话状态/停止、定时器、session id、探测 |
| frame | `https://*.jupyter-proxy.kaggle.net/*` | cell 采集、输出 diff、命令执行、滚动/折叠/定位 |
| single | Colab / AI Studio | host+frame 合一（同文档，无桥；v1 **不加** `@match`，见 §5-W5） |

- iframe 定位：`iframe[name="notebook-editor-cells"]`，兜底 `iframe[src*="jupyter-proxy.kaggle.net"]`；
  host 从 `iframe.src` 读 origin，用 `iframe.contentWindow.postMessage(msg, origin)` 定向发送。
- **握手不用 GM token**（评审 S8）：`bnc.bridge.token` 是**全脚本共享**的一个 key ⇒ 两个 Kaggle 标签会互相覆盖，
  frame 先于 host 加载还会读到上一页的旧值。改为 **per-attach nonce**：
  1. host 在 iframe `load`（以及 `src` 变化）时生成随机 `nonce`，`postMessage({__bnc:1, kind:'attach', nonce}, origin)`；
  2. frame 只接受 `event.origin` 在白名单（`https://www.kaggle.com`）的 `attach`；记下 `nonce`/`parentOrigin`，
     回 `{__bnc:1, kind:'ready', nonce, href, site}`；
  3. 之后 `cmd`（host→frame）/ `result` / `status`（5s 心跳）都带同一 `nonce`；host 断心跳 > 15s ⇒ 面板置
     「笔记本未连接」、按钮全禁用（不把失败伪装成成功）。
  - **重复 attach 是常态**（实测 2026-10-30）：host 在「iframe 出现」与「iframe load」各发一次，首个 nonce 随
    load 失效 ⇒ frame 对**每次** attach 都回 `ready`，host 只认**当前** nonce；不要假设一页只握一次手。
  - **没有**共享存储、没有跨标签竞态；iframe 重载 = 新的 `load` = 新 `nonce`（无状态）。
    nonce 只经**定向** `postMessage` 抵达该 frame（其他标签/页面没有它的 WindowProxy），不落存储。
  - 不用 `MessageChannel`：port 在 Tampermonkey 沙箱里是否原样透传**未验证**，而 nonce 方案同样解决
    多标签/时序问题且只用 `postMessage`（§12）。
- **frame 的 session 兜底**（评审 S9）：`document.referrer` 通常就是外层页 URL（`…/code/<owner>/<slug>/edit/run/<id>`），
  先据此算 session 并**立即开始搬运**（搬运这条腿不依赖桥）；host attach 后若给出不同 session，则切换并向新文件发
  `reset`。referrer 被策略抹掉时，等 attach 最多 10s，再退到 `kaggle-local-<8hex>`（面板可见，不静默）。
- frame 直接向 dashboard POST 日志（文本在它那边，少一跳）；host 只显示「最近一次成功时间/失败原因」。
- host 的 SPA 导航（`/edit` → `/edit/run/<id>`）会换 session：监听 `popstate` + 1s URL 轮询；变化时清状态、
  重握手、把新 session 推给 frame（frame 切文件并 reset）。
- 面板节点被站点 React 重渲染摘掉时（评审 S14f）：`MutationObserver(document.body, {childList:true})`，
  `!document.body.contains(host)` 就重挂（防抖 500ms）。

### 4.3 站点适配器契约（`sites/types.ts`）

```ts
export interface CellRef { id: string; index: number; kind: 'code' | 'markdown' | 'raw' }

export interface FrameAdapter {
  readonly site: SiteId
  readonly ready: boolean
  cells(): CellRef[]                       // 只含已渲染的
  outputText(c: CellRef): string           // 归一出 \r\n / \r → \n 的输出全文（含 stderr）
  sourceText(c: CellRef): string           // 编辑器文本（标记搜索用）
  ensureRendered(index: number): Promise<CellRef | null>   // 步进滚动直到出现
  select(c: CellRef): Promise<CmdResult>   // 点 .jp-InputArea 并等 jp-mod-selected
  runCell(c: CellRef): Promise<CmdResult>  // select → 执行（§4.4）
  clearCellOutput(c: CellRef): Promise<CmdResult>
  toggleCell(c: CellRef, part: 'input' | 'output'): Promise<CmdResult>
  command(id: NotebookCommand, args?: { cell?: CellRef }): Promise<CmdResult>
  scroll(to: 'top' | 'bottom'): CmdResult
  scrollOuter(to: 'top' | 'bottom'): CmdResult
  flash(c: CellRef): void
}

export interface SessionAdapter {          // host / single
  readonly site: SiteId
  sessionState(): 'running' | 'idle' | 'stopped' | 'unknown'
  stopSession(): Promise<CmdResult>
  sessionId(): string                      // §4.5 的 session 名
}
```

`CmdResult = { ok: true; detail?: string } | { ok: false; reason: string }`——**失败必须可描述**，面板 show
原始 reason（不吞异常）。

第一版只实现 `kaggle.ts`（含 host/frame 两个适配器）；`colab.ts` / `aistudio.ts` 仅
`detect()` + 全部方法返回 `{ok:false, reason:'adapter not implemented'}`，**不猜测选择器**。
Colab/AI Studio 落地流程：面板「探测」导出候选结构（§4.8）→ 用真实页面补 fixture → 再实现。

### 4.4 命令执行阶梯 + 后置校验（不靠「点了就算成功」）

`command(id)` 按序尝试，每级都有**可观测后置条件**，不成立就降级：

| 级 | 手段 | 说明 |
|---|---|---|
| 1 | `window.jupyterapp?.commands?.execute(id)` | JupyterLab 4 **默认不暴露**（需 `--expose-app-in-browser`，Kaggle 没开）；有就用，没有不报错 |
| 2 | `[data-command="<id>"]` 可见元素 `.click()` | 工具栏按钮属于这种；`notebook:run-cell` 不在工具栏，`runmenu:run` 也可能在 responsive popup（`lm-mod-hidden`）里 |
| 3 | **任何**隐藏的 `[data-command="<id>"]`（命令面板 `<li>`、responsive popup 的 `jp-button`）`.click()` | 存档实证：面板关闭时 `<li>` 仍在 DOM；`runmenu:run` 就藏在 `lm-mod-hidden` 的 popup 里 |
| 4 | 点开命令面板（`[aria-label="Open command palette"]`）→ 重试 3 | 3 失败才用（会闪一下 UI） |
| 5 | 返回 `{ok:false, reason}` | 面板 toast + 诊断行；**绝不静默** |

点击助手 `clickCommandEl(el)`：`jp-button` 这类自定义元素优先点 `el.shadowRoot?.querySelector('button')`，
再退到 `el.click()`（host 的 click 不一定会转发到 shadow 里的 `<button>`）；`li[data-command]` 直接点。
这一步是「点了但没效果」的常见根因，后置条件会把它抳下来。

后置条件（每类命令一条，失败就试下一梯级）：

- `hide-all-cell-code`：可见输入区计数 `V` 从 >0 变 0（`isVisible` 可注入）；`show-*` 从 0 变 >0。
- `clear-all-cell-outputs`：`.jp-OutputArea-child` 计数 == 0；若 2s 内出现 `[role="dialog"]`，按停止弹窗的同法确认一次
  （**未验证**是否真的弹，所以按「有就处理」写）。
- `hide-cell-code` / `clear-cell-output`：**先选中目标**（命令作用于 active cell），再看目标状态。
- `run-cell`：**先选中目标**（run 只作用于 active cell；run 按钮只有一份，且可能在隐藏的 responsive popup 里），
  再执行 `notebook:run-cell`（面板项）；面板项缺失/无效果 ⇒ `runmenu:run`（工具栏/溢出菜单）；2s 内看到内核 busy
  （`.jp-NotebookKernelStatus-busy`，**未验证**该类名是否在 Kaggle 版本里存在）**或**输出节点变化 ⇒ `ok`；
  都没有 ⇒ `ok:true, detail:'queued?'`（运行有可见效果，不以 DOM 断言当开关）。
- `stop-session`：外层按钮点完 + 会话状态文案 15s 内从 `Running|Draft Session` 变 `Stopped|starting` 才算 ok。

### 4.5 日志回传协议（页面 → dashboard）v2

`POST http://127.0.0.1:8900/api/notebookLog`，JSON：

```jsonc
{
  "v": 2,
  "site": "kaggle",                  // 白名单 kaggle|colab|aistudio
  "session": "kaggle-<owner>-<slug>-<runId>",   // [a-z0-9._-]{1,64}
  "notebook": "rollout",             // 展示用（可选）
  "writer": "w-7f3a9c",              // 每次页面加载随机；多标签时是身份
  "epoch": 3,                        // 服务端代际；0 = 未知（仅 reset 允许）
  "cell": "da0450c9-…",              // append/reset 必填；clear/probe 可省
  "cellIndex": 1,
  "seq": 12,                         // 同一 (session, cell) 单调
  "offsetStart": 1048576,            // 本块在归一文本里的起始（UTF-16 码元）；append 必须 == 服务端 chars
  "offsetEnd": 1097728,              // offsetStart + text.length；服务端校验（防错位拼接）
  "mode": "append" | "reset" | "clear" | "probe",
  "text": "...",                     // 归一后 ≤ 48 KiB（UTF-8 字节；服务端 >48 KiB 即 413）
  "dropped": 0,                      // 仅 reset：跳到最新时显式丢弃的字节数
  "at": 1790000000000
}
```

- 响应 `{ok:true, epoch, duplicate?, bytes?, redacted?}` / 400（字段非法）/ 403（非回环）/ 409（代际、写者
  或偏移冲突）/ 413（超限）/ 500。
- **409 响应体**：`{ok:false, reason, epoch, leaseRemainingMs?, cell?: {seq, chars, tail}}`——resume 握手只要这三
  个数；`tail` 已脱敏（被脱敏过的尾巴会比对失败 ⇒ 退化为 reset，方向安全）。
- **连续性**：`offsetStart` 是 append 的硬前置（必须等于服务端存的 `chars`）——比 seq 去重更能抓「错位拼接」；
  不等 ⇒ 409 `offset-mismatch`，客户端拿 409 的游标重新对齐。
- **头**：必须带 `Content-Type: application/json` **且** `X-Notebook-Companion: 1`。自定义头让跨源网页
  发不了这个请求（无 CORS 头会先撞 preflight），而 `GM_xmlhttpRequest` 可以带——比现有端点白送一层 CSRF 防护。
- **体积（实测修正，luna P0-2）**：Bun 1.4.4 的 `Bun.serve` **没有** 256 KiB 默认上限——实测 2 MiB 流式
  body 照收，129 MiB 才 413（默认 `maxRequestBodySize` = **128 MiB**）。所以「Bun 层天花板」是假的：
  - 新 handler **自己**做有界读：`req.body.getReader()` 累加字节，超过 `MAX_BODY_BYTES = 512 KiB`（48 KiB
    text 经 JSON 转义最坏 ≈ 6×）立即 `reader.cancel()` + 413；**不信任 `Content-Length`**（chunked 没有）。
  - 解析后再验 `Buffer.byteLength(text,'utf8') ≤ 48 KiB`、`probe` ≤ 8 KiB；任一超限 ⇒ 413，且**不写盘、
    不续租、不标记 console activity**。
  - **不动全局 `maxRequestBodySize`**：`/api/deliverUpload` 是 multipart zip，全局调小会改它的语义（现在
    要 128 MiB 才发现超限）。
- **代际（epoch）**：服务端 `clear` 时 `epoch++` 并落盘；`append` 带旧 epoch ⇒ 409 `stale-epoch` + 新 epoch，
  客户端清 offset/hash、下一条发 `reset`——**明确告知，不静默丢**（评审 B4-2）。
- **写者租约（writer lease）**（评审 B4-3 / luna P1-1）：同一 session 只允许一个**活动写者**。
  服务端记 `activeWriter + writerAt`；活动写者每条成功请求续租（租约 **180s**）。
  - **为什么不是 30s**：Chrome 88+ 对隐藏标签（无音频）在 5 分钟后把定时器钳到 **1 次/分钟**，30s 租约
    会在正常后台运行时必然失租 ⇒ 第二标签接管 ⇒ 用完整 DOM `reset` 把已落盘历史再写一遍。180s = 3 倍
    最坏正常间隔；`visibilitychange` 时补一拍心跳。
  - **fencing token = `writer` 本身**：writer id 每次页面加载随机生成；接管后旧 writer 的任何写入都满足
    `writer !== activeWriter` ⇒ 409。评审建议的「单调 fence 代数」在这里没有新信息，不额外加字段。
  - 另一个标签首次 `reset` 时租约仍在 ⇒ 409 `writer-conflict` + `leaseRemainingMs`，它转**被动**
    （停发、面板写「另一个标签在负责搬运日志」），`leaseRemainingMs + 2s` 抖动后重试。
  - **接管不重写历史（resume 握手）**：409 里带该 cell 的 `{seq, chars, tail}`（`chars` = 服务端记的已接收
    UTF-16 偏移，`tail` = 末尾 ≤64 字符且已过 `redactSecrets`）。客户端若 `dom.length >= chars` 且
    `dom.slice(chars - tail.length, chars) === tail` ⇒ 本地 offset 置 `chars`、`seq+1`、发 `append`（续写）；
    否则才 `reset`，且段头写 `takeover=reset`（**可见**的重复风险，不静默）。
  - **不采用「按 writer 分槽」**：两个标签捕的是同一份 DOM，分槽只会把每一行写两遍（评审建议的反例，见 §12）。
- **心跳**：空 `text` 的 `append` = 只续租、**不写盘、不推进 `lastSeq`**（否则心跳会占掉下一块的 seq，
  真 append 会被当重复丢掉）；后台标签被节流时也能保住租约。心跳**只在 frame ready 且本拍无错误时**发——
  一个读不到输出的坏标签不该靠心跳占着租约，把能工作的第二个标签挡在门外。

| 情况 | 服务端 | 客户端 |
|---|---|---|
| `append`，epoch 相符，writer 是活动写者 | 追加；`lastSeq=seq`；续租 | 前进 offset/seq |
| `append`，`offsetStart ≠ chars` | 409 `offset-mismatch` + `{seq, chars, tail}` | 用 409 的游标做 resume 判定 |
| `append`，epoch 不符（落后或超前） | 409 `stale-epoch` + 当前 epoch | 清 offset/hash；下一条 `reset` |
| `append`/`reset`，writer 不是活动写者且租约内 | 409 `writer-conflict` + `leaseRemainingMs` | 转被动；到期先按 409 的 `{seq, chars, tail}` 试 resume，tail 不中才 reset |
| `reset`，writer 是活动写者或租约已过 | 写 `# segment` + 文本；设活动写者；`lastSeq=seq`；epoch 对齐 | 同成功 |
| `reset`，全新 writer（`seq` 从 1 开始）且租约已过 | **接受**（不能被 `seq<=lastSeq` 当重复丢掉） | 同成功 |
| `seq <= lastSeq`（活动写者的重放） | `{ok:true,duplicate:true}`，不写盘 | 同成功（幂等重放） |
| `clear`（活动写者或租约已过） | `epoch++`、截断、删 `.1.log`、写 `# cleared at`、清 `lastSeq`、**设活动写者** | 清 offset/hash；下一条 `reset` |
| 单条 > 48 KiB 或字段非法 | 413 / 400（带 `reason`） | 面板红字 + 停止该 cell 的后续发送 |
| 收到 409/413 | —— | **必须可见**（面板 + `bnc.last`），不许静默 |

分块（客户端）：

```text
normalize(dom) = \r\n 或 \r → \n
suffix := dom.slice(offset) 当 dom.length >= offset 且 hashRange(dom, offset-256, offset) === tailHash；否则整段 reset
chunks := 按 \n 累加到 ≤48 KiB；单行 >48 KiB ⇒ 按 UTF-8 码点硬切
每拍发 min(全局预算 1 MiB, 剩余)，串行 await；永久错误（400/413）即停
flush  := 循环发送直到该 cell 剩余为空（上限 60s / 64 MiB）；超限 ⇒ 报告「未排空」并禁止清空
jump    := 积压 >8 MiB ⇒ 只留末尾 1 MiB，offset 跳过去，reset 带 dropped=<n>
```

- **session 名**：Kaggle `kaggle-<owner>-<slug>-<runId>`（无 runId 用 `-edit`）；客户端先
  `toLowerCase()` + 非法字符替换成 `-` + 截断 64；服务端仍校验，400 必须回显到面板（否则就是静默丢日志）。
  校验口径：`[a-z0-9][a-z0-9._-]{0,63}` **且** 不含 `..`、不以 `.` 开头、不是 Windows 保留名
  （`con`/`nul`/`aux`/`prn`/`com1`…）——`..` 能匹配朴素正则时会 `join` 出上级目录，必须单独拒。
- **GM 配置**：`@connect 127.0.0.1` + `@connect localhost`；dashboard 基址面板可改，但**主机名必须**
  是 `127.0.0.1` / `localhost` / `::1`（DoD 的「不发 POST 到非回环地址」与「基址可改」不能自相矛盾）。
- **段头卫生**：`# segment|cleared|redacted` 这些行不带 error/warn 关键词——dashboard 的
  `parseLogLine` 对无时间戳行按关键词判级（`dashboard/src/web/view/log-view.ts:121`）。

### 4.6 dashboard 落点（复用现有日志页，不新建页面）

| 文件 | 改动 |
|---|---|
| `dashboard/src/core/paths.ts` | `notebookLogsDir() = path.join(tmpLogsDir(), 'notebook-logs')`（单一声明处） |
| `dashboard/src/server/api/notebook-log.ts`（新） | 纯函数 + 磁盘：`readBoundedJson(req, cap)` / `parseNotebookChunk` / `appendNotebookChunk(dir, chunk, now)` / `recoverNotebookSession(dir, site, session)` / `notebookLogPayload(dir, key, lines)` / `listNotebookSessions(dir)` / `clearNotebookSession(dir, site, session)` / `redactSecrets(text)` / `pruneNotebookLogs(dir, now)` |
| `route-table.ts` | `{path:'/api/notebookLog', methods:['POST']}` + `{path:'/api/notebookLogs', methods:['GET']}`，**都插在 `/api/log/*` 之前**（静态先于通配） |
| `server.ts` | 两个 handler（POST 先 `readBoundedJson`，超限 413 且零副作用）；`logPageRoute` / `apiLogRoute` 在组件查表前先试**虚拟键**；`guard(fn, {activity:false})` 变体；日志页 `components` 追加当前虚拟键；`componentName` 前缀映射 |
| `dashboard/src/web/view/component-roles.ts` | `componentName`：`notebook:<session>` → `Notebook · <session>`（未知 key 的兜底不变） |
| `dashboard/tests/server-route-table.test.ts` | POST 白名单加 `/api/notebookLog`（注释写清「第二个 typed POST」）；`/api/notebookLogs` 仍 GET |
| `dashboard/tests/notebook-log.test.ts`（新） | 见 §6 |

**虚拟键**：`/log/notebook:<session>`（裸 `notebook` = 最近一个会话）。`LogPayload.component` 就是这个字符串，
客户端轮询 `/api/log/notebook:<session>` 自然走通。**必须在 `as Component` / `componentLogPayload` 之前分支**
（`componentLogPayload` 的 key 是联合类型，§2.3）。导航里的状态点：最近 30s 有写入 ⇒ `running`（绿），
否则 `stopped`（灰）；展开面板的「打开日志页」按钮直接链到 `/log/notebook:<session>`。

**存储**：`tmp/notebook-logs/<site>/<session>.log` + `<session>.meta.json`。
段头格式（`reset`/`claim` 时写；**不带** error/warn 关键词）：
`# segment cell=<uuid> idx=<n> mode=<reset|claim> at=<iso> dropped=<n> redacted=<n>`；
`clear` 写 `# cleared at <iso>`；脱敏命中时写 `# redacted <n>`。
单文件超 **8 MiB** 轮转为 `<session>.1.log`（保留 1 份，单会话上限 16 MiB）；`pruneNotebookLogs` 删
7 天未写的会话（`lastPruneAt` 内存节流，最多 1 小时跑一次；进程启动也跑一次）。
`mode:'clear'` 截断当前段 **并删除 `.1.log`**、写 `# cleared at <iso>`；`probe` 写 `probe.jsonl`（>1 MiB 轮转）。
**不**把 notebook 日志做成 `Component`：那会把它塞进启/停卡片与健康探测（错误的生命周期）。

**meta 落盘与崩溃恢复**（评审 B4-1 / luna P0-3，DoD「重启后去重仍生效」的前提）：

```jsonc
{ "v": 2, "site": "kaggle", "notebook": "rollout", "session": "kaggle-…",
  "epoch": 3, "activeWriter": "w-7f3a9c", "writerAt": 1790000000000,
  "bytes": 1048576,                       // 当前段文件的**已提交**长度（回滚基准）
  "clearing": false, "rotating": false,   // 破坏性操作的两阶段意图
  "cells": { "<cellId>": { "seq": 12, "chars": 1048576, "tail": "…", "index": 1 } },
  "lastAt": 1790000000000 }
```

- **ack 顺序**：`append` = 追加文本 → 写 meta（临时文件 + rename；Windows 实测可覆盖）→ 才回 200。
  客户端只在 200 后前进 offset；没收到 200 的块会被重发。
- **启动恢复**（每个会话一次，O(1) + stat）：
  - `fileSize > meta.bytes` ⇒ 上次「已 append、未提交 meta」⇒ `truncate(fileSize → meta.bytes)`。客户端本来
    就没收到 200，会重发同一块 ⇒ **不重复、不丢已确认数据**。
  - `meta.clearing` ⇒ 把 clear 做完（截断 + 删 `.1.log` + 清 cells/lastSeq；`epoch` 已是新值）。
  - `meta.rotating` ⇒ 把轮转做完（缺文件就建空文件；`bytes=0`）。
  - `fileSize < meta.bytes` 且无意图位 ⇒ 外部截断：`epoch++` + 清游标 + 写 `# recovered …` 段头（可见）。
- **`clear` 两阶段**：写 `{clearing:true, epoch:e+1}` → 截断 + 删备份 → 写 `{clearing:false, bytes:0,
  cells:{}}` → 回包。崩在中间 ⇒ 启动时把 clear 做完（重复 clear 只会再 epoch++，无数据损失）。
- **`rotate` 两阶段**：写 `{rotating:true}` → `mv <session>.log <session>.1.log`（覆盖旧 .1）+ 建空文件
  → 写 `{rotating:false, bytes:0}` → 回包。
- **保证范围**：**进程崩溃**可恢复（页缓存还在）；断电/掉盘不在保证内（不 fsync，写入量太大）。
- **坏 JSON 一律当空**（不阻塞新日志），但空 meta + 非空文件会走「外部截断」分支（epoch++，可见），不静默续写。
- **单会话串行化（luna P1-2）**：handler 从读完 body 到回包**全是同步代码**（无 `await`）⇒ Bun 单线程事件循环
  天然串行，`meta` 的读-判-写不会交错。这是**协议保证**，不是实现巧合：该段禁止加 `await`，并用「两个并发
  append（同 seq / 相邻 seq）」用例钉住。

**脱敏**（评审 S12，运行期日志里就有凭据；存档输出里出现过 `TS_AUTHKEY` / `authKey`）：
写盘前 `redactSecrets()` 过一遍，命中就替换成 `«redacted»` 并计数（响应 `redacted`，面板提示一次；
文件里写一行 `# redacted <n>`）。模式：`ghp_/gho_/ghu_/ghs_/ghr_…`、`github_pat_…`、`Bearer <token>`、
`(ts_authkey|authkey|api[_-]?key|access[_-]?token|secret|password)` 后跟 `[:=]` / `←` / `->` 的长值。
- 另加 **JWT**（`eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}`）与 **URL query**
  （`[?&](session|token|auth|api_key|access_token)=[^&\s]+`）两族——iframe URL 就是 `?session=<JWT>`。
- **诚实边界**：有限正则不是 DLP。分块按行界，token 在行内完整；只有 **>48 KiB 的单行被硬切**时，token 可能
  跨块而漏匹配（写进 README 与 §8-R11）。脱敏是「已知格式的尽力过滤」，不是「运行期凭据全净」。

**不做**通用 base64 脱敏（会误伤哈希/权重的可读性）；README 仍写明「日志可能含凭据，自行注意」。

**清空来源**：第一版只有页面面板（`mode:'clear'`，返回新 epoch）；dashboard 侧的按钮见 O4（暂不做）。

### 4.7 配置与存储（GM）

```jsonc
// bnc.config.v1
{ "dashboardBase": "http://127.0.0.1:8900", "enabled": true,
  "shipIntervalSec": 5, "maxChunkBytes": 49152, "maxBytesPerTick": 1048576,
  "backlogDropBytes": 8388608, "keepBytes": 1048576,
  "autoClearBytes": 1048576, "autoClearDeferWarnSec": 60, "autoClearForceBytes": 8388608,
  "largeCellBytes": 8388608, "largeCellIntervalSec": 30,
  "claimSettleMs": 1000, "stopGraceMs": 600000, "confirmStopOnCreate": true,
  "configMarkers": ["CFG = {", "CFG={", "# @title"] }
// bnc.jobs.v1.<site>   -> 任务数组（§3.3）
// bnc.last.v1          -> { lastConfigIndex, lastShipAt, lastError, writer, epoch }
// 没有 bnc.bridge.token（已删除；握手用 per-attach nonce）
```

所有 key 带 `bnc.` 前缀；读时做形状校验，非法值回默认（不抛）。`dashboardBase` 只接受回环主机名。
面板设置页可导出/导入 JSON（方便换机）。

### 4.8 面板 UI 与「探测」

- 右上角浮层（`position:fixed`，z-index 高），挂在 **ShadowRoot** 里（站点 CSS 不污染、我们也不污染站点）。
  收起状态是一个小胶囊（站点图标 + 连接点：绿=已连/黄=保存失败/灰=未连接）。
- 展开：会话名 + 站点 + 最近保存时间 + 写者状态（活动/被动）；按钮：**保存并清空** / 折叠全部 / 展开全部 /
  折叠当前 / 展开当前 / 滚到顶 / 滚到底 / 配置 cell / **打开日志页**；下面任务列表 + 设置折叠区。
- 只绑 `addEventListener`，不覆写站点 `onclick`；不修改站点 DOM（除临时高亮 class，1.5s 自删）。
- **探测模式**（`GM_registerMenuCommand('Notebook Companion: 探测页面结构')`）：输出一份 JSON 快照，
  包括 role、站点 origin + **路径形状**（`/code/<owner>/<slug>/edit/run/<runId>`，owner/slug/run 用占位符）、
  hit 到的选择器计数、`.jp-Cell` 数量、命令 id 列表、滚动容器标签/高度、内核状态用了哪些 class、
  `/api/state` 的 HTTP 状态（只看可达性，不解析）、`document.hasFocus()`。
  - **URL 先净化再进快照**（luna P1-6）：`iframe.src` / `href` / `referrer` 一律 `origin + pathname 占位`，
    **删掉 query/hash**——`jupyter-proxy` 的 `?session=<JWT>` 就在 query 里；同一份净化对象再给剪贴板。
  - **不自动 POST**：菜单命令只打开面板的「探测」视图（复制 + 预览）；「发送到 dashboard」是**第二次用户
    点击**，才 `POST mode:'probe'` 到 `/api/notebookLog`（handler 分流写 `tmp/notebook-logs/probe.jsonl`）。
  - 探测 JSON 截断到 8 KiB（命令 id 只留前 40 条），避免撞上 `text` 的 48 KiB 上限。
  ⚠ 原稿写成 POST `/api/notebookLogs`（那是 GET 路由）⇒ 会掉进 `routeAction` 的未知动作并刷一行决策日志（评审 B7）。

---

## 5. 实施步骤（W 提交，每步可独立验收；含「改坏必红」）

### W0 — 骨架 + 门禁边界 + 夹具（不改任何生产行为）

> **状态：已落地（2026-10-30）**。`cd userscript && bun run check` 绿（typecheck / oxlint / 18 用例 / 双 bundle），
> 根 `bun run check` 绿（userscript 不在根套件枚举内）。夹具的「必红自查」两条依赖 W3 用例（W0 时还不存在），
> W0 只验证了「删掉 `notebook:clear-all-cell-outputs` 的 `<li>` ⇒ 夹具用例变红」；`select()` 那条留给 W3。

- 建 `userscript/` 项目（package.json / tsconfig / bunfig / happydom / build / test-silent）；`bun install` 出 lock，
  `.gitignore` 加 `!userscript/bun.lock`。
- **门禁（B1/B2）与 `.gitignore` 同批**：
  - `tools/run-root-tests.ts` 追加第二个 `--path-ignore-patterns=userscript/**`（实测重复 flag 生效，别用逗号）；
  - `tools/test-silent.ts`：`SKIP_RE` 加 `userscript`，`isDashboardOnly` → `isIndependentOnly`（含 userscript）；
  - `tests/test-silent-scope.test.ts` 加同构断言（枚举结果里没有 `userscript/`；只改 userscript 的改动集被识别）；
  - `tools/githook/pre-commit`：`ROOT_TS_STAGED`/`FREEZE_STAGED` 排除 `^userscript/`，新增 userscript 门禁块
    （`SKIP_USERSCRIPT_GATE=1` 逃生口）；
  - AGENTS §9 的「根 `bun run check` 只判根项目」补一句：userscript 的测试必须显式排除，否则根门禁反向依赖它的安装状态。
    ⚠ AGENTS.md 有 ~9.2K 注入预算（`bun tools/check-agents-budget.ts`）：放不下就把解释搬进
    `docs/agents.details.md` §5.5，AGENTS 只留一行指针。
- 从 mhtml 提取 **脱敏**夹具：`kaggle-outer.html`（iframe + Stop session + 状态文案）与 `kaggle-frame.html`：
  **3 个 cell；run 按钮放笔记本工具栏（含 responsive popup 的隐藏形态），cell 本身不挂 run**（复刻实测）；
  一个 cell 含 stdout `<pre>` + **stderr 段**
  （真实采样或明确标注 synthetic）；`.jp-InputCollapser`；`.jp-WindowedPanel-outer`；命令面板里 8 条 `li[data-command]`。
  **删除 `?session=` JWT 与所有 token**，文件头注写来源与提取日期；原始 mhtml 不入库。
- **必红自查**：夹具里删掉 `notebook:clear-all-cell-outputs` 的 `<li>` ⇒ W3 的清空用例变红；把 `select()` 改成
  no-op ⇒ 「非 active cell 也先选中再 run」用例红（证明夹具真能钉住选中步骤）。

### Gate 0 — 浏览器实证（**人**，10 min；W1 之前必须过）

> agent 不开浏览器（AGENTS §5.5），所以这是一道**人工闸**。物料已备好：
> **`userscript/gate0/probe.user.js`**（探针，装进 Tampermonkey；metadata 与成品同口径： `@match` 外层 + 内层域、
> `@grant GM_xmlhttpRequest`、`@connect 127.0.0.1`、`@run-at document-start`、**不加** `@noframes`）+
> **`userscript/gate0/GATE0.md`**（可勾选清单：前置/安装/五条判据/判定/失败处置/记录表）。
> 探针只做三件事、每件打一行 `[gate0]` 日志：① 角色/注入 `role=… notebook=…`；② 握手 `attach →` / `ready ←`；
> ③ 回环 `GET /api/state → 200`。重挂不额外要一行——iframe load / SPA 换 URL 都会再走一次握手，数第二遍即可。
> 探针打印的 URL 只到 `origin + pathname`：`?session=` 是会话 JWT（含邮箱/uid），2026-10-30 首版实测
> 把它原样打进了 Console ⇒ 已改为 `safeHref()` 去 query/hash，任何探针/探测 JSON 都按这条纪律（§4.6）。
> 通过标准（全部勾上才放行 W1）：

- [ ] **注入**：内层帧真的跑了脚本（不是只在外层）——整套方案**唯一没被存档证实的物理前提**。
- [ ] **角色唯一**：host 逻辑不进 frame、frame 逻辑不进外层（`detect()` 的判据）。
- [ ] **握手**：host 用 `iframe.contentWindow.postMessage` 定向发出 attach，frame 收到并回 ready。
- [ ] **回环请求**：`GM_xmlhttpRequest` GET `http://127.0.0.1:8900/api/state` 拿到 200（dashboard 起着）。
- [ ] **重挂**：iframe reload / SPA 换 run 后能重新 attach（新 nonce）。

**实测（2026-10-30，用户机 Chrome + Tampermonkey，探针 v0.2）**：判据 ①②③ 通过 —— host 轮询到 iframe 后
attach，frame 回 `ready` 并在 `.jp-Notebook` 渲染后补 `status`（同一 nonce `mvu9kzyw-xrnk`），host 与 frame 的
`GET /api/state` 均 200；`.jp-Notebook` 是直查命中（不在 shadow root）。观察：host 会在「iframe 出现」与
「iframe load」**各 attach 一次**，首个 nonce 随 load 失效 ⇒ W3 的桥必须按**最后一次** attach 的 nonce 归一。
判据④已由首次 load 的同一条监听器证实（`attach（iframe load）` + 新 nonce + 新 ready）。

**判据⑤（SPA 换 run 重挂）不阻塞 W1**：它是我们自己的 1 s URL 轮询，不是物理前提；转为 W3 集成验收
（W1 全在 dashboard 侧，与它无关）。这条豁免按 AGENTS §6.4 记在这里，不静默降级。

**失败处置**：停在这里，不写 W1 集成面；先决定退化路径（用户手动在 frame 控制台粘贴同一份脚本仍走同一协议，
但不进 v1 交付）。W1 的纯函数与测试可以先写（与注入无关、失败也不浪费），但**不得**宣称端到端可用。

### W1 — dashboard 侧（先有接收端，人才能试）

1. `notebookLogsDir` + `notebook-log.ts`（纯函数与磁盘分离，dir/now 可注入）：协议 v2、epoch、writer 租约、
   meta 落盘、脱敏、轮转/清除/prune；
2. 路由两条（插在 `/api/log/*` 前）+ handler + `guard(fn, {activity:false})` + 虚拟键 payload + `componentName` 映射；
3. 测试（§6）+ 路由表白名单。
- **必红自查**：去掉 seq 去重 ⇒ 重发用例红；去掉 meta ⇒ 重启去重用例红；去掉 epoch 校验 ⇒ stale-epoch 用例红；
  去掉租约 ⇒ 双写者用例红；去掉 8 MiB 轮转 ⇒ 上限用例红；路径拼接不过白名单 ⇒ 穿越用例红；
  去掉 `X-Notebook-Companion` 校验 ⇒ 跨源用例红；去掉脱敏 ⇒ `ghp_…` 用例红；POST 记活动 ⇒ console idle 用例红。

### W2 — Kaggle frame 适配器：采集 + 回传

1. `detect()` + `waitForNotebook()`（MutationObserver，60s 上限）；cells/outputText（stdout+stderr）/dirty 追踪；
2. `log-shipper`：归一 → `{offset, tailHash}` 尾部锚 diff → 分块（码点安全）→ 全局预算 → 串行 POST →
   失败保留；`reset`/`dropped` 语义；referrer session 兜底；
3. 纯函数单测（归一/diff/分块/预算）+ DOM 用例（fixture 读输出、stderr 顺序、CRLF/CR、隐藏 cell 的状态保留）。
- **必红自查**：改成每拍发全量 ⇒ 「第二拍无新增」用例红；去掉 reset 检测 ⇒ 「清空后再输出」用例红；
  按字节硬切 ⇒ emoji 截断用例红；删掉 stderr ⇒ 顺序用例红；把全局预算改回每 cell 3 块 ⇒ 背压用例红。

### W3 — 命令层 + 桥 + 面板（P1 收尾 + P2 页面操作）

**交付次序**：先 1–3（桥 + 面板 + 保存/清空 ⇒ 日志与自动清空闭环 = MVP A），再 4–5（折叠/滚动/CFG = MVP B）。

1. `command()` 阶梯 + 后置条件；`ensureRendered`；`select`；`scroll`；
2. host/frame 握手（per-attach nonce）与 `cmd/result/status`；ShadowRoot 面板 + 重挂载；
3. 「保存并清空」全链路（drain → 重读 → clear → 标记）；「清空 dashboard 会话」；自动清空的交互闸 /
   积压闸 / 延期可见；
4. 折叠/展开（全部 + 单个）、滚动到顶/底、**CFG 定位**（从 W4 前移：低副作用，与滚动共用 `ensureRendered`）；
5. 用例：夹具驱动 click 记录；无效果夹具（点击无副作用）⇒ `{ok:false}`；桥：错 origin / 错 nonce ⇒ 拒绝；
   runCell 先选中（夹具里 run 只有一份）；CFG 步进扫描（命中/未命中/400 步上界/取消）。
- **必红自查**：删掉命令面板回退 ⇒ 折叠全部用例红；去掉后置条件 ⇒ 「点击无效仍报 ok」用例红；
  去掉 nonce 校验 ⇒ 跨源伪造用例红；去掉「先选中」⇒ 非 active cell 用例红；去掉交互闸 ⇒ 自动清空抢输入用例红。

### W4 — 定时器 + 会话停止（P3，风险最高，最后接）

1. `scheduler`（纯函数 `nextFireAt` / `shouldCatchUp`，注入 now）+ occurrence claim（1s 沉淀 + 重读）+
   30s watchdog + 倒计时取消；
2. host `stopSession`（站点确认弹窗、停前 drain、10 min grace、绑定 `sessionAtCreate`）；
3. 目标解析：uuid → index + fingerprint → **fail closed**；切 run / 换会话不执行；
4. 用例：DST 春跳/秋回、跨日、迟到策略、两个标签同 occurrence 只执行一次、target 指纹不符不执行。
- **必红自查**：把 `stop` 也设成任意迟到可补跑 ⇒ 用例红；去掉 claim 重读 ⇒ 双标签用例红；
  target 只认 index ⇒ 「cell 插入后错跑」用例红。

### W5 — Colab / AI Studio 骨架 + 探测模式

- `single` 角色 boot；两个适配器 `detect()` + 全部 `{ok:false}`；探测导出 JSON（含 `/api/sessions` 可达性）；
- **v1 不加** Colab / AI Studio 的 `@match`（评审 S13）：加了就会在那边出现一个全灰面板 = 承诺了「看起来支持」。
  README 写明「当前只 Kaggle；要试 Colab 请自己改元数据块的 `@match`」。
- **不**做伪选择器；不把未验证的能力写进面板。

### W6 — 文档与记忆

- `DECISIONS.md` 一条：`§2026-10-29-goalnn-notebook-userscript`（日期 ID 全小写、正文 ≤40 行）——
  新子项目边界 / 第二个 typed POST / 自定义头防 CSRF / guard 的 activity 例外 / 日志不清空闸与脱敏；
- `docs/nn/console.md` 新节 **§44**（当前最大 §43；该文件倒序，新条目置顶）+ `docs/nn.progress.md` 一行；
- `.workbuddy/memory/2026-10-29.md` **追加** + `MEMORY.md` 持久约定（AGENTS §11）；
- `userscript/README.md`：安装、构建、配置、已知限制（§8）、「运行期日志可能含凭据」。

---

## 6. 测试与夹具（agent 不能开浏览器，所以测试必须自证）

### 6.1 userscript 侧

| 文件 | 断言 |
|---|---|
| `test/normalize-chunk.test.ts` | `\r\n`/`\r` 归一；48 KiB 按行累计；**1 MiB 无换行 ⇒ 切成 N 块且每块 ≤48 KiB，拼接逐字节相等**；emoji/中文不被切坏 |
| `test/diff.test.ts` | 尾锚命中 = 只发后缀；尾部 256 字符变 = `reset`；长度回退 = `reset`；`offset` 跳变（积压丢弃）= reset + `dropped`；空 → 有 → 空 → 有；**尾锚只扫 256 字符（计数器钉住，不随 offset 涨）** |
| `test/observer.test.ts` | 在 `.cm-content` 打字**不**标 dirty；`.jp-OutputArea` 变标 dirty；折叠/展开只刷渲染集合；虚拟化进出不丢 offset |
| `test/shipper.test.ts` | 全局 1 MiB/拍；串行；`offsetStart` 连续（错位 ⇒ 不发）；失败块不前进、已成功块前进、重发同 seq 幂等；400/413 停发；409 stale-epoch ⇒ reset；409 writer-conflict ⇒ 被动；**409 + tail 命中 ⇒ 续写（不重发历史）**、tail 不中 ⇒ reset；空 append 只续租；积压 10 MiB ⇒ 只留末尾 1 MiB + `dropped` 标记 |
| `test/scheduler.test.ts` | 固定 `now` + 固定 TZ 下 `nextFireAt` 正确；1=周一…7=周日；days 不匹配不触发；迟到 ≤10min run 补跑；stop 在 grace 内执行、超过跳过；watchdog 补拍；同一 occurrence 键二次触发被 `claimed` 挡住；DST 春跳 02:30 不执行、秋回 01:30 只执行一次；target 指纹不符 / uuid 缺失且 index 指纹不符 ⇒ 不执行 |
| `test/kaggle-frame.test.ts` | fixture 里 `cells()` 拿到 3 个；`outputText` 收 stdout + stderr 且顺序不乱；折叠 cell 仍能读 `sourceText`；非 active cell `runCell` 先选中；`command('hide-all-cell-code')` 走面板项 |
| `test/kaggle-command-ladder.test.ts` | 只有隐藏 `li` 时能执行；有可见按钮时优先用它；两级都无效 ⇒ `{ok:false}`（注入 isVisible） |
| `test/bridge.test.ts` | 错 origin / 错 nonce ⇒ 忽略；握手后 cmd/result 对账；iframe 重载 = 新 nonce；无 host 时 referrer session 仍搬运 |
| `test/config.test.ts` | 非法存储值回默认；非回环 `dashboardBase` 被拒；导出/导入往返一致 |
| `test/scroll-config.test.ts` | 注入假滚动器（高度/步长/命中序列）：命中、未命中、先用 lastIndex、400 步上界、取消 |

**happy-dom 限制**：不做布局，所以所有「可见性/滚动/虚拟化」都通过可注入接口测
（`isVisible`、`scrollToIndex`、`viewportHeight`），DOM 用例只验证「选择器找对元素 + 事件发对」。
不要把依赖布局的东西写成 DOM 断言（会在 CI 里假绿）。

### 6.2 dashboard 侧

| 文件 | 断言 |
|---|---|
| `tests/notebook-log.test.ts` | append/reset/clear 的字节与段头；重复 seq 不重写；**重启后 meta 去重仍生效**；epoch 递增与 stale-epoch；writer 租约（活动写者续租 / 第二写者 409 / 租约过期可抢）；`offsetStart` 不连续 ⇒ 409 `offset-mismatch`；1 MiB 无换行逐字节相等；clear 删 `.1.log`；脱敏（含 JWT / `?session=`）；超 8 MiB 轮转；会话名/站点非法拒绝（含 `..`、`.` 开头、Windows 保留名）；路径穿越拒绝；7 天 prune；list 按 mtime 降序 |
| `tests/notebook-recovery.test.ts` | **注入式故障**：append 已写、meta 未提交 ⇒ 重启后尾巴被截回 `meta.bytes`，重发不重复；clear 截断后崩 ⇒ 启动把 clear 做完；rotate 改名后崩 ⇒ 启动把轮转做完；`fileSize < meta.bytes` 无意图位 ⇒ epoch++ + `# recovered` |
| `tests/notebook-body.test.ts` | 512 KiB 以上 body ⇒ 413 且在 parse 前中止（流式构造，无 Content-Length）；`text` > 48 KiB ⇒ 413；413 不写盘 / 不续租；`/api/deliverUpload` 不受影响（仍走原 handler） |
| `tests/notebook-concurrency.test.ts` | 两个并发 append：同 seq ⇒ 只写一次、两回包一真一 duplicate；相邻 seq ⇒ 两块都落下且顺序不乱；append 与 clear 同时到 ⇒ 结果符合「先到先决」不半写 |
| `tests/notebook-probe.test.ts` | 探测 JSON 的 `href`/`referrer`/`iframe.src` 只含 origin + 路径占位，**无 query/hash**（含 `?session=<JWT>` 的样例）；`probe` > 8 KiB ⇒ 413 |
| `tests/server-route-table.test.ts`（既有） | `/api/notebookLog` POST 与 `/api/notebookLogs` GET 都在 `/api/log/*` 之前；POST 白名单只多 `/api/notebookLog` |
| `tests/server-api-logs.test.ts`（既有，**复数**） | `notebookLogPayload` 的 `LogPayload` 形状；虚拟键在 `componentLogPayload` 之前分支 |
| `tests/console-activity.test.ts`（新或并入既有） | POST `/api/notebookLog` **不**推进 `consoleIdleMs`；GET 日志页仍推进 |
| `tests/web-log-view.test.ts`（既有） | `componentName('notebook:kaggle-x')` → `Notebook · …` |

> 原稿把文件名写成 `server-api-log.test.ts`——实际是 `server-api-logs.test.ts`（评审 §1 #13）。

### 6.3 夹具纪律

- 只用**最小结构**（grease-monkey-scripts AGENTS 的口径：不要把整页贴进测试）；单文件 < 10 KiB。
- **忠实优先于最小**：run 按钮只有一份且在笔记本工具栏（B5 的假绿就是这样产生的）；
  输出要同时有 stdout 与 stderr。
- 夹具里不出现真实用户/账号/token/JWT；外层 URL 用 `example`；文件名注明「提取自 mhtml 的时间与哪一段」。
- 夹具与真实页面的漂移：§4.8 探测模式 + §8-R1 的降级报告负责发现，不在 CI 里假装能测真实站点。

---

## 7. DoD 与门禁

- [ ] 六个需求在 Kaggle 上全部可操作；每个按钮失败时有可见 reason（不允许「点了没反应」）。
- [ ] **Gate 0 通过**（真实浏览器：注入 / 角色唯一 / 握手 / 回环 GET / 重挂）——没通过不动 W1 集成面。
- [ ] 日志：页面保存失败时不清空；**drain 未完成时拒绝清空**；dashboard 重启后 seq 去重仍生效；
      单会话磁盘 ≤ 16 MiB；7 天自清理；运行期凭据被脱敏。
- [ ] 协议：1 MiB 无换行输出 ⇒ 全部落盘、逐字节相等、无 413 循环；dashboard clear 后下一条必是 `reset`
      （文件里没有「半截开头」）；同一 run 开两个标签 ⇒ 一个活动一个被动且面板可见，不静默丢。
- [ ] **请求体有界**：512 KiB 以上 body ⇒ 413 且在 parse 前中止；`/api/deliverUpload` 仍是 128 MiB 语义；
      413 不写盘、不续租、不推进 console activity。
- [ ] **崩溃一致性**：append / clear / rotate 各在「数据已落盘、meta 未提交」处注入退出 ⇒ 重启后文件与 meta
      一致、重发不重复；`fileSize > meta.bytes` 的尾巴被回滚；`clearing` / `rotating` 意图被完成。
- [ ] **租约**：后台标签按 1 次/分钟发包不失租；接管走 resume（tail 命中）不重写历史，tail 不命中才
      `reset` 且段头 `takeover=reset` 可见；`writer` 即 fencing token（旧 writer 的写入一律 409）。
- [ ] 定时：倒计时可取消；stop 在 grace 内执行、超过跳过；**隐藏标签页也按预授权执行**（不静默 skipped）；
      后台标签页恢复后不误发。
- [ ] **跨标签只执行一次**：两个标签同 occurrence 只有一方 `claimed` 并执行；刷新 / 睡眠 / DST 边界按 §3.3 的
      明文规则工作；target 指纹不符或会话已变 ⇒ 不执行且原因可见。
- [ ] 不发 POST 到非回环地址；不从页面 `fetch`（只 `GM_xmlhttpRequest`）；不覆写站点 `onclick`；不永久改站点 DOM。
- [ ] 自动清空**不在最近 10s 有输入 / 有非折叠选区 / 编辑器聚焦时触发**（不会抢走选区），积压未排空时
      跳过；延期可见（60s 提示、8 MiB 红条 + 一键「立即保存并清空」）；有用例钉住。
- [ ] 探测 JSON 的 URL 只含 origin + 路径占位，**不含 query/hash**（JWT 不外泄）；默认只复制，POST 需二次点击。
- [ ] 脱敏覆盖 JWT / `?session=` / `TS_AUTHKEY`；>48 KiB 单行硬切导致的跨块漏检写进 README（诚实边界）。
- [ ] **根门禁不依赖 userscript 的安装状态**：新克隆只跑根 `bun install` 时 `bun run check` / `bun run test` 仍绿；
      `tests/test-silent-scope.test.ts` 钉住「枚举结果里没有 userscript/」。
- [ ] **只改 `userscript/` 的提交**会跑它自己的 typecheck + test（pre-commit 块），不改则跳过；
      报错按 `userscript/` 归因；`SKIP_USERSCRIPT_GATE=1` 可逃生。
- [ ] Colab/AI Studio 无 `@match`、无伪面板；探测 JSON 能导出（含 `/api/sessions` 可达性）。
- [ ] 门禁（三个包各自跑）：
  ```bash
  cd userscript && bun install && bun run check     # typecheck + lint + test + build
  cd dashboard && bun run typecheck && bun run test && bun run build:ui
  bun run check && bun run build                     # 根项目
  ```
- [ ] **人工验收（agent 不做）**，顺序即优先级：
      1. **注入**：三行脚本（`console.log(location.href)`，`@match https://*.jupyter-proxy.kaggle.net/*`）
         是否真的进到 `notebook-editor-cells` 那个 iframe——这是整套方案**唯一没被存档证实的物理前提**；
      2. **点得着**：Console 里 `document.querySelector('[data-command="notebook:hide-all-cell-code"]').click()`
         是否生效（若无效，再试 `jp-button` 的 `shadowRoot.querySelector('button').click()`）；
         `GM_xmlhttpRequest` 打一次 `127.0.0.1:8900` 的 GET；
      3. **两个未验证的后置条件**：`clear-all-cell-outputs` 是否弹确认框；内核忙态类名；
      4. 再逐项勾：面板出现 / 日志增长 / 「保存并清空」后页面变空且文件保留 / 折叠与滚动 / 「配置 cell」/
         1 分钟后的 `run-cell` 任务。
- [ ] 文档：DECISIONS 一条 + `docs/nn/console.md` §44 + `docs/nn.progress.md` 行 + memory 追加 + `MEMORY.md`。

---

## 8. 风险与降级（每条都有对策，不靠「应该没事」）

| # | 风险 | 影响 | 对策 / 降级 |
|---|---|---|---|
| R1 | Kaggle 升级 JupyterLab / 换编辑器，`jp-*` 或命令 id 漂移 | 按钮失效 | 命令阶梯 + 后置条件 + 面板显示 reason；探测模式一键导出结构；适配器版本常量 `KAGGLE_UI='jl4'` |
| R2 | iframe 源变化（`kkb-production` → 其他域） | frame 实例不注入 | `@match https://*.jupyter-proxy.kaggle.net/*` 宽匹配；探测上报 `iframe.src`；面板提示「未连接」 |
| R3 | DOM 只截到「还在的输出」：`clear_output`、内核端缓冲、CM 行虚拟化、长 cell 标记深于首屏、多 cell 交错 | 日志不完整 / 找不到 CFG | 面板显示「本段从 when 开始」；不清空闸；CFG 支持改标记并自曝兜底命中；重要日志仍建议 cell 内同时落文件 |
| R4 | 后台标签页节流、系统休眠 | 定时晚点 / 日志延迟 | 读累计文本（不丢字）+ 30s watchdog；迟到策略（run ≤10min，stop ≤10min grace）；面板显示「上次保存 12s 前」 |
| R5 | Stop session 的确认弹窗 / 页面语言 | 关不掉 | 按钮 aria-label 多选择器 + dialog 文本多语言匹配 + 15s 后置条件；失败如实报告 |
| R6 | 跨源 CORS / Private Network Access | POST 被拦 | 只用 `GM_xmlhttpRequest`（扩展上下文，不走 CORS/PNA）；`@connect 127.0.0.1`/`localhost`；不提供 fetch 回退 |
| R7 | 本地端点被任意网页写入 | 日志污染 | 回环门控 + `X-Notebook-Companion` 头（触发 preflight，无 CORS 头 ⇒ 网页发不出）+ 字段白名单/长度上限 + 会话名白名单 |
| R8 | 定时任务误触发（休眠恢复时间跳跃 / 时区） | 意外关机 | 创建 stop 任务时勾选确认（不退化成每次 `confirm()`）；触发时 10s 倒计时可取消；stop 只在 grace 内执行；本地时区显式显示 |
| R9 | 多标签 / 多会话同名 | 重复或混写 | 单写者租约（180s，盖过后台 1 次/分钟节流）+ 被动标签显式 409 + resume 接管不重写历史；session 含 runId；无 runId 用随机后缀 |
| R10 | 磁盘增长 | 占空间 | 8 MiB 轮转 + 7 天 prune + clear 删备份 + 面板 clear |
| R11 | 夹具/运行期日志含 JWT、账号、私有仓 token | 泄密 | 夹具只提取结构并脱敏；运行期写盘前 `redactSecrets`；README 明示；原始 mhtml 不入库 |
| R12 | Kaggle 服务条款 / 反自动化 | 账号风险 | 只做用户手动登录后的页面操作，不伪造 API、不刷请求；频率人工可感知（5s）；README 让使用者自判 |
| R13 | Tampermonkey 沙箱/注入行为与页面脚本不同（nonce 消息、`document.hasFocus`、剪贴板） | 桥或取焦点失效 | 全部走 `runtime.ts` 适配层；失败在面板可见；Gate 0 先证伪注入 |
| R14 | 大输出 cell 每拍 `textContent` 仍是 O(n)（DOM 无增量读），叠加自动清空延期时可能到几十 MiB | 页面卡顿 | 尾锚把校验降到 O(256)；observer 只盯输出区；>8 MiB 降频到 30s；自动清空阈值 1 MiB；面板显示积压/降频状态 |
| R15 | 进程在 append/clear/rotate 中途崩 | 重启后重复或丢掉一代 | ack 后才算提交；启动回滚未提交尾巴；`clearing`/`rotating` 意图位把破坏性操作做完（§4.6） |

---

## 9. 未决 / 预注册门槛

| # | 问题 | 处置 |
|---|---|---|
| O1 | 自动清空默认开还是关？ | 默认**阈值触发**（1 MiB）+ 交互闸 + 积压闸；若用户抱怨「想一直看」⇒ 阈值改 0 = 只手动。**不采纳评审的「默认关」**：它与用户诉求②冲突 |
| O2 | 定时「启动」是否要支持「整页刷新后自动跑」？ | 第一版不做（刷新后 GM 存储能恢复任务，但会话可能已断）；先看真实使用 |
| O3 | 日志是否要高亮/过滤/告警？ | 不做（dashboard 日志页已有搜索/等级过滤）；本脚本只负责搬运 |
| O4 | 是否给 dashboard 日志页加「清空」按钮？ | 第一版只在脚本面板做；若用户常从 dashboard 侧清空，再加一个只对虚拟键生效的动作 |
| O5 | Colab / AI Studio 何时接？ | 等用户用探测模式导出一份真实结构；在此之前**不加 `@match`**、不猜选择器 |
| O6 | 内核 iopub WebSocket 读日志（评审 §5.1） | 不换主方案（DOM 简单、与所见一致）；探测 JSON 只记 `/api/sessions` 是否 200。**R3 真咬人时**再做 channels attach（那是唯一高保真补救腿） |

---

## 10. 一句话给接手 agent

> 先过 **Gate 0**（人：真实 Kaggle 帧里脚本真的跑起来、能回环 GET），再做 **W1 dashboard 接收端**
> （`notebookLog` POST + `notebook:<session>` 虚拟日志页）与 **W2 Kaggle frame 采集**，最后才是命令/桥/面板（W3–W4）。
> **不要**假设 Kaggle 的 notebook 在外层页：cell 全在
> `kkb-production.jupyter-proxy.kaggle.net` 的 JupyterLab iframe 里；**不要**用外层
> `data-jp-item-name="collapse-all"`（那是标题折叠，真正的是 `notebook:hide-all-cell-code`）；
> **不要**在保存失败或 drain 未完成时清空；**不要**用 `fetch` 或 `window.jupyterlab`；
> **不要**让根测试枚举 `userscript/`（加第二个 `--path-ignore-patterns`，不是逗号）；
> **不要**给 `run-cell` 省掉「先选中」（工具栏只挂在 active cell）；
> 所有 DOM 断言用夹具 + 可注入的可见性/滚动接口，真实页面验证由人来做（AGENTS §5.5）。

---

## 11. 被否决的备选（含理由）

| 备选 | 为什么不选 |
|---|---|
| 只做外层页，不注入 iframe | 读不到 cell 输出与命令，四个需求直接没了 |
| 从外层用 `iframe.contentDocument` | 跨域，浏览器直接抛 SecurityError |
| 用 Jupyter 内核 WebSocket 直接执行 cell | 绕过 UI：输出不会渲染到用户眼前、执行计数/状态分叉；且「执行 cell」本应等于用户点 Run（**只读 iopub 另议**，见 O6） |
| 用 `window.jupyterlab.commands` | JupyterLab 4 默认不暴露（需启动参数），Kaggle 未开；只在阶梯第 1 级 opportunistically 用 |
| 把日志作为新的 `Component` 接进 dashboard 健康/启停体系 | 生命周期语义错（它不是受管进程），会把卡片/探测/启停都污染 |
| 复用 `/api/*` 动作面收日志 | `routeAction` 每个动作落一行决策日志，5s 一条会把控制台日志刷爆 |
| 页面直接 `fetch` dashboard | 无 CORS 头 ⇒ 被拦；`GM_xmlhttpRequest` 是唯一稳的通道 |
| 让 cell 自己把日志推到 hub | 要改训练代码 + 加日志 API + 额外网络面；浏览器通道零侵入，且能抓第三方输出 |
| 用 Kaggle 原生 Schedule 代替脚本定时 | 它确实更适合「关页面也跑」，但做不到「页内跑某 cell 后立刻关机/折叠/清空」；两者分工 |
| 第一版三站齐上 | Colab/AI Studio 的 DOM 未验证，猜选择器 = 交付一个看着能点、实际不工作的面板 |
| **seq 按 writer 分槽**（评审 B4 的建议） | 两个标签捕的是同一份 DOM ⇒ 分槽会把每一行写两遍（不是「至少不丢」，是「全重复」）。改用单写者租约 |
| **MessageChannel 传 port**（评审 S8 的建议） | port 在 Tampermonkey 沙箱里是否原样透传**未验证**；per-attach nonce 同样消除 GM token 的多标签/时序问题，且只用 `postMessage` |
| **自动清空默认关**（评审 S5 的建议） | 与用户诉求②「避免内存爆炸」冲突；改为「默开 + 交互闸 + 积压闸」 |
| **v1 只做「到点 stop」**（评审 §5.3） | 用户明确要求「定时启动/关闭 + 执行指定 cell」；W4 本来就是最后一步，任务由用户显式创建（天然 opt-in） |
| **逗号分隔 `--path-ignore-patterns`**（评审 B1 的建议） | 实测 Bun 1.4.4：逗号形式一个都不排；重复传 flag 才累加 |
| **全局调小 `maxRequestBodySize`**（luna P0-2 的备选） | 会把 `/api/deliverUpload` 的 multipart 一起改掉（实测默认 128 MiB）；且 48 KiB text 经 JSON 转义最坏 6× ⇒ 端点自读 + 512 KiB 上限更准 |
| **给每次租约再加单调 fence 代数**（luna P1-1） | `writer` id 已是 fencing token：接管后旧 id 的写入一律 409，多一个代数没有新信息 |
| **自动清空到点强抢焦点 / 强制清空**（luna P1-4 的隐含选项） | 清非 active cell 必须先选中；抢选区比延期更糟。改为「10s 交互闸 + 延期可见 + 一键」 |
| **探测默认 POST**（luna P1-6 的一半） | 先复制、二次点击才发送；URL 先删 query/hash |
| **用 `BroadcastChannel` 做跨标签派发**（luna P1-3 的备选） | 沙箱里是否透传未验证；持久化 occurrence claim（写→1s 沉淀→重读）已能挡住晚到标签 |

---

## 12. 评审处置（`plan/notebook.review-ds.md`，2026-10-29）

| 条目 | 判定 | 处置 / 理由 |
|---|---|---|
| B1 根门禁枚举 userscript | **采纳** | 已核实 `run-root-tests.ts:23` 与 `test-silent.ts:65` 都只排 dashboard；根 devDeps 无 happy-dom。修正评审的一处：**逗号不生效，重复 flag 才生效**（实测） |
| B2 pre-commit 无 userscript 闸 | **采纳** | 加块 + `ROOT_TS_STAGED`/`FREEZE_STAGED` 排除；否则只改 userscript 的提交会白烧根 typecheck/freeze，而它自己的门禁没人跑 |
| B3 分块/413 永久重试 | **采纳** | 归一 CR + 行界累加 + 码点硬切；删掉「单行不切」；服务端只做端点自读上限（v3 修正为 512 KiB body，§4.5） |
| B4 缺代际 / 未落盘 / 多写者 | **采纳根因，改方案** | epoch + meta 落盘采纳；**不采纳「按 writer 分槽」**（同 DOM 双写 = 全重复），改单写者租约 + 409 `writer-conflict` + 被动标签可见 |
| B5 runCell 缺「先选中」 | **采纳** | 实测 run 项 `data-command="runmenu:run"` 在笔记本工具栏（responsive popup），不在 cell 工具栏；命令仍作用于 active cell ⇒ 先选中。夹具也改为忠实复刻 |
| B6 只采 stdout | **采纳** | 本存档无 stderr（覆盖缺口）；按 DOM 顺序收 stdout + stderr，mime 变化加中性标记 |
| B7 probe POST 打到 GET 路由 | **采纳** | 改 `POST /api/notebookLog`，`mode:'probe'` 由 handler 分流 |
| B8 日志 POST 点亮「有人看」 | **采纳** | `guard({activity:false})` + 用例钉住 `consoleIdleMs`；DECISIONS 同条记「日志搬运不算观看」 |
| S1 虚拟键显示 `notebook:` 原文 | **采纳** | `componentName` 加前缀映射；`label` 在导航里确实没被读 |
| S2 clear 不删备份段 | **采纳** | clear 同时删 `.1.log` |
| S3 flush 判据 | **采纳** | drain 到「没有未发后缀」，超限拒绝清空 |
| S4 清空竞态 | **采纳** | 清前重读 DOM + 最多 2 轮；标记行只写时间，**不估丢失行数**（不知道） |
| S5 自动清空抢 active cell | **部分采纳** | 采纳焦点/积压闸；**驳回「默认关」**（与诉求②冲突）与「active cell 一律跳过」（active 时选中是 no-op，真正风险是抢焦点） |
| S6 `lastText` 内存翻倍 | **采纳（升级为 v1）** | 改 `{offset, tailHash}`（现为尾部 256 字符锚），O(cell 数)；S7 的「跳到最新」本来就需要 offset，合并成一个状态 |
| S7 背压 | **采纳** | 全局 1 MiB/拍 + 8 MiB 跳到最新 + `dropped` + 面板积压读数 |
| S8 握手 token 多标签/时序 | **采纳问题，驳回方案** | 删除 GM token，改 per-attach nonce；不用 port（沙箱行为未验证） |
| S9 frame 依赖 host | **采纳** | referrer 兜底 + attach 后切换；桥断了日志仍走 |
| S10 `# @title` 静默错命中 | **采纳** | 先 `CFG…{` 启发式；兜底命中必须自曝 |
| S11 stop 前不 flush | **采纳** | 停前 drain（30s 上限），失败如实写 |
| S12 运行期日志含凭据 | **采纳** | 写盘前 `redactSecrets` + 面板提示 + README；夹具脱敏仍保留 |
| S13 Colab/AI Studio `@match` | **采纳** | v1 不加 `@match`，不交付灰面板 |
| S14a 路由顺序 | **采纳** | 两条都插在 `/api/log/*` 之前 |
| S14b 白名单只改一处 | **采纳** | 只把 `/api/notebookLog` 加进 POST 白名单；`/api/notebookLogs` 仍 GET |
| S14c `cell` 必填与会话级 clear 矛盾 | **采纳** | `cell` 在 clear/probe 可省 |
| S14d session 白名单 | **采纳** | 客户端归一 + 服务端 400 回显面板 |
| S14e CFG 扫描无上界 | **采纳** | 400 步 + 取消 + 2 帧 |
| S14f 面板被 React 摘掉 | **采纳** | body MutationObserver 重挂（防抖） |
| S14g 缺 `GM_setClipboard` | **采纳** | runtime 列表 + `@grant` 补上；另补 `GM_deleteValue` |
| S14h 段头信息太少 | **采纳** | 段头带 cell uuid / index / mode / at / dropped；且避开 error/warn 关键词 |
| S14i stop「迟到」定义 | **采纳** | grace 10 min 内照常执行，超过才跳过 |
| S14j 日志页行级解析 | **采纳** | 段头形状避开 `parseLogLine` 的错误关键词；真实输出 `[06:23:04] …` 正好可被解析 |
| S14k 交付记录 | **采纳** | W6 改为 memory **追加** + `MEMORY.md` |
| §5.1 iopub | **部分采纳** | 只把 `/api/sessions` 可达性放进探测 JSON；不换主方案（O6） |
| §5.3 W4 取舍 | **驳回** | 用户明确要 run-cell 定时；任务显式创建即 opt-in |
| §6 DoD 增项 | **采纳** | 全部并入 §7（含「注入先验」排第一） |
| §7 W0–W6 增量 | **采纳** | 并入 §5 |
| 评审核查表 #12/#14/#16 | **采纳** | `label` 未读、根测试会枚举、console.md 当前最大 §43（新节 §44）均已核实并改写 |

---

## 13. 评审处置（`plan/notebook.review-luna.md`，2026-10-29）

> 阅读方式：评审没开浏览器、没跑 dashboard（它自己标了），所以「静态检查」类结论逐条回仓核实；
> 「设计判断」类按 §2.7 三关与用户六诉求取舍。下表「采纳」的条目都已改进正文（指到节号）。

| 条目 | 判定 | 处置 / 理由 |
|---|---|---|
| P0-1 先验注入（Gate 0 移到 W1 前） | **采纳** | 新增 §5「Gate 0」人工闸（三行脚本 + 5 条判据），W1 集成面必须在它之后；纯函数/测试可先行，但不得宣称可用。评审说得对：W1 接收端的价值依赖「frame 里真的跑脚本」 |
| P0-2 dashboard 请求体上限 | **采纳，改事实** | 实测 Bun 1.4.4：`Bun.serve` **没有** 256 KiB 默认；2 MiB 照收、129 MiB 才 413（默认 128 MiB）⇒ 原文的「Bun 层天花板」是错的。改为端点自读（`req.body` 流式累加，512 KiB 上限，不信任 `Content-Length`），parse 前 413；不动全局 `maxRequestBodySize`（§4.5） |
| P0-3 meta 原子替换 ≠ 日志事务 | **采纳** | 改为 ack 顺序（append → meta → 200）+ 启动回滚未提交尾巴 + `clearing`/`rotating` 意图位；clear/rotate 两阶段。保证范围 = 进程崩溃（不是断电）。补 `notebook-recovery` 故障注入用例（§4.6 / §6.2） |
| P1-1 30s 租约 vs 后台节流 | **采纳** | 租约 180s（Chrome 隐藏标签 1 次/分钟的 3 倍）；`writer` id 即 fencing token（不再加代数）；接管改 resume 握手（409 带 `{seq, chars, tail}`，tail 命中则续写，不命中才 reset 且 `takeover=reset` 可见）（§4.5） |
| P1-2 单会话串行化 | **采纳** | handler 读完 body 后全同步（禁 `await`），Bun 单线程 ⇒ 读-判-写不交错；补并发 append / append×clear 用例（§4.6 / §6.2） |
| P1-3 定时跨标签重复 + target 漂移 | **采纳** | occurrence 状态机 `pending→claimed→succeeded/failed/skipped`，claim 先落 GM、1s 沉淀后重读；`lastFiredAt` 在 claim 时写；run 绑 `notebookKey`、stop 绑 `sessionAtCreate`；target 用 uuid + 指纹，index 仅兜底且指纹必须一致，歧义 fail closed（§3.3） |
| P1-4 自动清空被焦点闸无限跳过 | **部分采纳** | 采纳「不能无限静默跳过」：把 `hasFocus()` 常量闸改成「最近 10s 有输入 / 有选区 / 编辑器聚焦」，空闲即恢复；延期 60s 可见、8 MiB 红条 + 一键「立即保存并清空」。**驳回「到点抢焦点强制清」**：清非 active cell 必须先选中，抢选区比延期更糟（§3.2） |
| P1-5 DOM 读 / 哈希成本 | **采纳** | 前缀全量 hash → 尾部 256 字符锚（O(256)/拍）；observer 逐 record 过滤（输入区打字不标脏）；>8 MiB 降频 30s；诚实写明 `textContent` 仍是 O(n)，DoD 不承诺零成本（§3.1 / §7） |
| P1-6 探测泄漏 JWT | **采纳** | 探测 URL 只留 origin + 路径占位（删 query/hash）；默认只复制，POST 需二次点击；redaction 加 JWT 与 URL query 两族；明示「有限正则 ≠ DLP」（§4.8 / §4.6） |
| P1-7 定时时间语义 | **采纳** | 本地时区 + 显示 UTC 偏移；1=周一…7=周日；occurrence 键定「最多一次」；DST 春跳不执行、秋回只一次；迟到规则沿用；切 run 不执行（§3.3） |
| §4 范围拆分（Gate 0 / MVP A-B-C） | **部分采纳** | 采用阶段拆分与 Gate 0；CFG 从 W4 前移到 W3（低副作用）；W3 内部先交付日志 + 自动清空（MVP A）再折叠/滚动/CFG（MVP B）；W4 = P3。**不砍需求、不改 W 编号**：六项都是用户点名要的，风险隔离靠次序而不是删功能 |
| §5 验收条款 8 条 | **采纳** | 全部并入 §6 用例表与 §7 DoD（body 上限、故障注入、租约迁移、occurrence 唯一、大输出、probe 净化、自动清空延期、best-effort 边界） |
| 「用户已明确运行中清空不保证无损」 | **无法核实；按设计边界采纳** | 本 plan / 对话记录里没有这句原始表述（评审转述）⇒ 不当作授权依据；只把「DOM clear 与 kernel 无同步屏障」作为技术边界写进 §3.2，且不放松「已知 POST/drain 失败必须拒绝清空」 |
| 30s watchdog / 10s 倒计时 | **保留** | 与 occurrence claim 互补：watchdog 补漏拍、claim 防重复，不冲突 |
| 「加 256 KiB 全局上限」的字面方案 | **驳回** | 见 P0-2：会改 deliverUpload 语义，且 JSON 转义最坏 6×；端点自读才有正确的边界 |

**未采纳的 3 处（附理由）**：① 全局 body 上限（改现有上传语义）；② 租约再叠单调代数（`writer` 已是 fence）；
③ 自动清空强制抢焦点（与「不抢选区」冲突，改为可见延期 + 一键）。

---

## 14. 落地状态（2026-10-30，W1–W5）

> 判据：`userscript/` 自己的 `bun run check`（typecheck + oxlint + 隔离测试 + 构建）全绿；
> dashboard 侧 W1 由 `dashboard/tests/notebook-*.test.ts` 钉住。**没开的浏览器验证不写成已验证**。

| 波次 | 状态 | 落地面 |
|---|---|---|
| Gate 0 | **人工通过** | 用户实机日志：host `attach` → frame `ready notebook=true`，回环 `GET /api/state → 200`；嵌入页 URL 只用于调试，没有写进仓库 |
| W1 | 绿 | `dashboard/src/server/api/notebook-log.ts`（epoch/租约/meta 恢复/脱敏/轮转/clear/prune）+ 两条路由 + 虚拟键 `notebook:<session>` / `notebook` + 4 份用例（append 幂等、409 tail 续写、恢复、body 413、接线顺序） |
| W2 | 绿 | `log-shipper.ts`（尾锚 diff/48 KiB 分块/单写者 409/预算/413 停发）· `sites/kaggle.ts`（cells/outputText/sourceText/命令阶梯/选中后置条件/session 停止）· `observer.ts`（只标脏输出区）· `ship-loop.ts`（tick / drain / saveAndClear / 自动清空闸） |
| W3 | 绿（未实机点验） | `app/bridge.ts`（per-attach nonce、origin/parent 白名单、command 超时）· `ui/panel.ts`（保存并清空/折叠/滚动/日志/CFG/停会话）· `app/commands.ts` · `app/boot.ts` 接线；**真实 Kaggle 上的按钮点击不在 Gate 0 范围内，未点验** |
| W4 | 纯函数绿，**未接线** | `schedule.ts`：`nextFireAt` / `dueOccurrence` / `occurrenceKey`，DST 用真实分钟扫描（春进跳过、秋回只一次），本地时区由调用方注入。定时器 UI 与 GM 任务持久化尚未接进 boot |
| W5 | 纯函数绿，**未接线** | `probe.ts`（URL 去 query/hash、只读计数、不抛）· `sites/stub.ts`（Colab/AI Studio 每个命令明确 `{ok:false}`）。探测模式入口与 `@match` 仍只开 Kaggle（v1 决策） |
| W6 | 本文档 | `DECISIONS.md` 追加实现条目；`docs/nn/console.md §44` 记录控制台侧；`.workbuddy/memory/2026-10-30.md` 续写 |

**遗留（不宣称完成）**：① W4 的定时创建/停止 UI 与 GM 持久化；② W5 的探测按钮与二次确认 POST；
③ 真实 Kaggle 上「保存并清空」的端到端点验（需要操作员开浏览器，Gate 0 只证明了注入/握手/回环）。
以上三项都已有纯函数/模块与用例，接线前不得当成可用功能。

**2026-10-30 修正（面板一直「连接中」）**：host 曾用 `window.postMessage` 发 attach —— 该 API 只投递给
**当前窗口**，不会广播到子帧；frame 的 ready/result 同错。改为 host 定向 `iframe.contentWindow`、frame
定向 `window.parent`，bridge 用例补 target 断言。另修两处窗口：首拍把打开页面时**已有**的 code cell
标脏（MutationObserver 不会为旧输出补事件）；`/log/notebook` 空会话的 404 从 `unknown component`
改成「还没有 notebook 会话 + 去哪看清单」。

**2026-10-30 二修（握手通了但 `notebook=false`）**：实机日志显示 attach/ready 已同 nonce 贯通，但
`.jp-Notebook` 在 ready 时还没渲染（JupyterLab 4 也可能把它放在 open shadow root 里）。适配器改用
direct → shadow root 的 `deepQuery` 并缓存 notebook 根；桥在 attach 后按 500ms 轮询，就绪时补一条
`status{notebook:true}`（30s 上界），host 收到后把「等待笔记本」切成「笔记本就绪」。cell 列表晚出现时，
observer 给没有游标的 cell 补标脏，保证首批输出仍会走一次 reset。

**2026-10-30 三修（多开页签 + 「打开日志」报未知组件）**：两个独立问题，都是操作员实机报障。
① 两条日志路由直接拿 `url.pathname` 切片当键，而客户端拼链接时做了 `encodeURIComponent` ⇒
`notebook%3A<session>` 不是虚拟键、被当组件查表 ⇒ `未知组件: notebook%3A…`。修法：键先过
`decodeLogKey()`（畸形 `%` 原样返回，交给查表 404），页面与 `/api/log/*` 两条路由都改。
② 会话名只由 owner/slug/runId 推出，同一 notebook 多开两个页签会抢同一份日志（协议 v2 把后到的
判成 `writer-conflict` 被动），两台 worker 的输出搅成一条流、分不出哪份是哪台。修法：frame 侧给
会话名加**页面标签**（`bnc:pageTag`，`sessionStorage` 在 iframe 里按顶层标签页分区 ⇒ 本页刷新不变、
换页签必不同；拿不到存储就每次加载随机），`session = <base>-<tag>`；host 从 bridge 的
`ready/status.session` 采纳**这一页**的键（不再拿外层 URL 猜），面板直接显示 `日志 notebook:<session>`。
旧的无标签会话不再续写，7 天 prune 自然清掉。

