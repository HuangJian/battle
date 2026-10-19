# Plan: error-page-denoise-and-triage — 错误页去噪与真问题分类处理（v2）

> 状态：v2 设计稿（2026-10-19）。v1 的评审见 `plan/error-page-denoise-and-triage.review-bf.md`
> （F1–F13）。v2 **逐条并入**评审结论，并把含糊处锁成决策 D1–D8；实现按本文执行。
> 触发：`/errors` 页里 12/13 条活跃错误是云隧道运维噪声、1 条采集器误报；历史账本 84 个指纹里
> 至少 7 条噪声，真问题被淹没。
> 前置：`plan/component-error-registry.plan.md`（登记表契约）。**D1 明确不改它的 N5/Q2**
> （`/errors` 仍是独立页 / 第 4 份 bundle），只在其视图层包上主页外壳。

## 0. 一句话目标

**把「组件错误」从「所有含 error 关键词的行」升级为「按分类的真问题清单」：采集层去掉访问日志
与堆栈续行误报；每个事件带稳定 `classification` 与 `activity`；`/errors` 包进主页框架、默认只看
真问题、历史课错误单独成组；网络类错误连续两拍不再出现即自动撤销并记入「已恢复」；采集不再受
开课闸限制。**

## 1. 现状证据（2026-10-19）

### 1.1 误报来源（机制已在代码上验证）

| # | 来源 | 例 | 根因 |
|---|---|---|---|
| M1 | hubServer 访问日志 | `"GET /admin/errors?since=0 … 200"` | `LOG_ERR_RE` 命中路径里的 `errors` 子串 |
| M2 | hubServer 访问日志 | `"POST /admin/worker-error … 200"` | 同上，路径含 `error` |
| M3 | hubServer 堆栈续行 | `During handling of the above exception, another exception occurred:` | `LOG_ERR_RE` 命中 `exception` |
| M4 | hubServer 堆栈续行 | `Exception occurred during processing of request from …` | 同上 |
| M5 | 云隧道 | DNS 超时 / 源站不可达 / context canceled / 边缘关闭 / TCP 重置 / http2 流关闭 | 真实运维事件，但瞬时，不应与代码缺陷同权 |

**评审校正**：`File "…"` 行今天**本来就不产事件**（`parseLogLine` 为 `info`）；真正要治的续行是
M3/M4 两类。访问日志判定只认「行首结构」与状态码，见 D4。

### 1.2 真问题（需分类处理，不是误报）

| 类别 | 代表 | 影响 |
|---|---|---|
| hub 服务端异常 | 500 internal、请求体截断、响应发送停滞 | 任务上报/大 payload 链路不稳定 |
| 节点多进程 | `bootId` 在同一端口变化 | 两个 agent 同时监听，任务路由混乱 |
| worker 依赖缺失 | `ModuleNotFoundError: numpy`、`libcublas.so.* not found` | code zip worker / GPU 训练无法启动 |
| 节点容量 | HTTP 503 `busy` | 节点过载，任务排队 |
| 节点网络 | Cloudflare 530、`IncompleteRead` | 隧道/网络链路不稳定 |

### 1.3 补充需求（用户 2026-10-19）

| # | 需求 | 本 plan 处置 |
|---|---|---|
| S1 | `/errors` 包在主页框架内 | **D1**：独立页 + 第 4 份 bundle 不变，`ErrorsApp` 包 `Shell`/`NavSidebar`/`Topbar`；不进主 bundle（尊重父 plan N5） |
| S2 | 网络波动恢复后自动撤销 | **D3**：瞬时类连续 2 拍不出现 ⇒ 移出活跃表、进 `resolved` 环、账本记 `kind:'resolved'` 行；非瞬时保持「消失即结束」 |
| S3 | 错误不与课程绑定 | **D2**：去开课闸；全局组件按绝对路径**只扫一次**；`trainingLoop` 逐课扫；历史课事件标 `activity:'historical'`，不进告警坞/默认视图 |

### 1.4 证据限制

本工作树 `tmp/` 已清理（无 `component-errors.jsonl` / `hub-server.out` / `cloudflared.log`），
v1 §1 的「13 条活跃 / 84 指纹 / 7 条误报」**无法复现**。实现时以 §3 规则表 + 夹具为准；
原始行由操作员补进 `plan/` 附录即可，不阻塞本 plan。

## 2. 目标 / 非目标

### 2.1 目标

| # | 目标 | 判据 |
|---|---|---|
| G1 | 访问日志不再误报 | 2xx/3xx/4xx 访问行 0 事件；5xx 产 1 条、`classification='remote-failure'`；单测钉住 |
| G2 | 堆栈续行不单独成事件 | M3/M4 行 0 事件；detail 仍含完整 traceback；`File` 行行为不变 |
| G3 | 每个事件带 `classification` | 字段进 `ErrorEvent`/`ErrorListItem`/账本；规则表见 §3；`unknown` 兜底 |
| G4 | `/errors` 默认只看真问题 | 默认隐藏 `transient-network`/`process-lifecycle` 与 `activity:'historical'`；两个开关可放行；按 classification 分组 |
| G5 | 真问题有处置指引 | `classificationHint()` 一行建议；展开 detail 时显示 |
| G6 | 不破坏既有契约与门禁 | 根 `bun run check` 绿；dashboard typecheck/test 绿；现有 SSR/registry 用例仍绿 |
| G7 | 页面共享主页框架 | `/errors` SSR 首帧含侧栏/顶栏/主题（`Shell`），页面内导航与其它页一致 |
| G8 | 瞬时错误自动撤销 | 瞬时类连续 2 拍缺席 ⇒ 活跃表移除、`resolved` 环 + 账本 resolved 行；非瞬时不变 |
| G9 | 采集与课程解耦 | 无开课闸；全局组件只扫一次；历史课错误以 `historical` 出现；`?course=` 不影响列表 |

### 2.2 非目标

| # | 不做 | 理由 |
|---|---|---|
| N1 | 不改训练语义/门禁/派发 | 只动观测面 |
| N2 | 不自动修复或重试 | 登记与分类，不是自愈 |
| N3 | 不删历史账本 / 不清理 tmp / 不回写旧行 | 不可逆；旧行读时忽略即可 |
| N4 | 不把云隧道瞬时错误升级为代码缺陷 | 标记分类即可 |
| N5 | 不改 `AlertDock` 排序/折叠 | 只把「计数口径」限制为真问题（见 D6） |
| N6 | 不把 `/errors` 的数据面并入 `/api/state` 轮询 | `/api/errors` 仍独立 |
| N7 | **不进主 bundle 路由** | 父 plan N5/Q2；D1 只包外壳 |
| N8 | 不为历史课重扫 `deliver/`、`it<N>/` | 与错误登记无关 |
| N9 | 不引入新的运行时依赖 | 规则表是纯函数 |

## 3. 数据契约与分类规则（D5）

### 3.1 类型（`web/view/error-types.ts`）

```ts
export type ErrorClassification =
  | 'transient-network'
  | 'process-lifecycle'
  | 'dependency'
  | 'capacity'
  | 'remote-failure'
  | 'multi-process'
  | 'unknown'

/** 事件的活动度：active = 源仍在近期写；historical = 源文件静默 > 24h。 */
export type ErrorActivity = 'active' | 'historical'
```

- `ErrorEvent` / `ErrorListItem` 增加必填 `classification: ErrorClassification`、`activity: ErrorActivity`；
  `ErrorEvent` 增加可选 `resolvedAt?: number`（只出现在 resolved 投影）。
- `ErrorSeverity` 仍两档，与 classification 正交。
- `ErrorsPagePayload` 增加 `resolvedItems: ErrorListItem[]`；`ErrorPayload` 同样带 `resolvedItems`。
- `misreport` **不进枚举**（评审 F6：Phase 0 之后没有写者）；历史账本里的旧值不被读取。

### 3.2 规则表（`server/api/error-classify.ts`，按序首命中）

| 序 | 正则（不区分大小写） | classification |
|---|---|---|
| 1 | `ModuleNotFoundError|ImportError|No module named|libcublas|libcudnn|cannot open shared object|numpy` | `dependency` |
| 2 | `bootId|address already in use|EADDRINUSE|multiple .*agent|duplicate agent` | `multi-process` |
| 3 | `\b503\b|busy|overload|queue full|no (available )?worker|all workers busy` | `capacity` |
| 4 | `internal server error|\b500\b|request body truncated|response send stalled|payload .*truncat|unexpected end of (stream|json)` | `remote-failure` |
| 5 | `context canceled|client disconnected|broken pipe|SIGTERM|SIGINT|signal: terminated|process exited|shutdown|restart` | `process-lifecycle` |
| 6 | `dial tcp|i/o timeout|deadline exceeded|tls handshake|no such host|network is unreachable|connection reset|connection refused|incompleteread|http2.*(stream|goaway)|cloudflare|origin is unreachable|\b(521|522|523|524|530)\b` | `transient-network` |
| 7 | （兜底） | `unknown` |

- `classifyError({source, component, message, detail?})`：先看 `message`，再拼 `detail`（截 4000）；
  `source==='node'` 时 `capacity`/`transient-network` 同表判定，不另造口径。
- 处置提示（`web/view/error-hints.ts`，纯函数，客户端可用）：
  `dependency` → 安装缺失依赖后重启 worker；`multi-process` → 查端口与重复 agent；
  `remote-failure` → 查对应 job 的 payload/日志；`capacity` → 降并发或扩容；其余空串。

### 3.3 活动度（D2）

- 事件的源文件 `mtime < now - 24h` ⇒ `activity:'historical'`；`firstAt`/`lastAt` 用**文件 mtime**
  （不谎报「刚刚」）。否则 `active`，`lastAt = now`。
- `historical` 事件仍在登记表与 `/api/errors` 里（需求 S3：不被隐藏），但：
  - `ErrorsApp` 默认不显示（开关「显示历史」）；
  - `componentErrorAlerts` 不计入、不触发 ack 键变化。

### 3.4 撤销（D3）

- `RegistryState` 增加 `misses: Map<fp, number>` 与 `resolved: ErrorEvent[]`（上限 50，保留 24h）。
- `applySweep`：本拍缺席的 **瞬时类**（`transient-network`/`process-lifecycle`）保留 1 拍
  （`misses+1`），第 2 拍移出活跃表 ⇒ 进 `resolved` 环、`resolvedAt=now`、账本追加 resolved 行；
  非瞬时类缺席 1 拍即丢（今天语义不变）。
- 同一 `fp` 重现 ⇒ 清掉同 fp 的 resolved 副本、按新 episode 重弹。
- `resolved` 只是**内存投影**（重启清空）；账本 resolved 行用于审计，`replayErrorLedger`
  跳过 `kind==='resolved'`。`/api/errors` 返回 `resolvedItems`，页面显示「最近已恢复」。

## 4. 实施阶段

### Phase 0 — 采集层去噪（D4）

**改动文件**
- `web/view/log-view.ts`：识别访问日志行（行首 `METHOD path HTTP/x.y` + 可选状态码）。
  状态码 `<500` 或缺失 ⇒ `info`；`>=500` ⇒ `error`（4xx 不当错误）。
- `server/api/error-scan.ts`：`collectLocalLogEvents` 跳过**无 `[HH:MM:SS]` 前缀**且命中
  traceback 续行集（`During handling of`、`The above exception`、`  File "`、`Traceback (`);
  不按「缩进行」一刀切。`scanLocalLogEvents` 读尾用 `maxLineLen:'all'`（机器解析档）。

**验收**
- 新 `dashboard/tests/server-api-error-scan.test.ts`：M1/M2 0 事件；5xx 1 事件且 `remote-failure`；
  M3/M4 0 事件、detail 含 traceback；`File` 行行为不变。
- `web-log-view.test.ts` 补访问日志 200/500/截断三例。

### Phase 1 — 分类字段落地（D5）

**改动文件**
- `web/view/error-types.ts`（类型）、`web/view/error-hints.ts`（新）、`web/view/index.ts`（导出）。
- `server/api/error-classify.ts`（新，规则表）。
- `server/api/error-scan.ts`：`collectLocalLogEvents` / `failRecordEvent` / `scanNodeEvents` 产出时填字段。
- `server/api/error-registry.ts`：`workerReportEvent`、`applySweep`、`buildErrorSummary`、
  `appendErrorLedger` 传播/写入；`replayErrorLedger` 忽略 resolved 行。
- 指纹不变（`errorFingerprint` 不掺 classification/activity）。

**验收**
- 新 `dashboard/tests/server-api-error-classify.test.ts`：§3.2 每类至少 1 条真样本 + 优先级
  （`numpy` 不得判 transient；`503 busy` 不得判 remote-failure）+ `unknown`。
- `getErrorRegistry()` 每项字段合法；`buildErrorSummary` 投影带字段。

### Phase 2 — `/errors` 分组与过滤（D6）

**改动文件**
- `web/app/errors-app.tsx`：按 classification 分组；默认视图 `真问题`
  （隐藏瞬时两类 + historical）；两个开关「显示噪声」「显示历史」；头部计数
  `N 条未处理 / M 条已恢复 / K 条噪声已折叠 / H 条历史`；行上加分类标签。
- `web/render.tsx`：状态壳不变（SSR 首帧仍渲染列表）。
- `web/view/alerts.ts`：`componentErrorAlerts` 只数真问题 + active；噪声/历史写入 detail 一行。
- `server/server.ts`：`/api/errors` 返回 `resolvedItems`。

**验收**
- `web-ssr-errors-page.test.ts`：分组标题、默认不渲染噪声/历史行、开关文案存在、
  `resolvedItems` 渲染「已恢复」块。
- `web-alert-dock.test.ts`：只有噪声/历史 ⇒ 0 条；真问题计数不含噪声；复发不重弹。

### Phase 3 — 处置指引（G5）

**改动文件**
- `web/view/error-hints.ts` + `errors-app.tsx` 展开区渲染 hint。
**验收**：SSR/客户端同文案；无 hint 不占位。

### Phase 4 — 包进主页框架（D1，S1）

**改动文件**
- `web/app/errors-app.tsx`：`<Shell sidebar={<NavSidebar activePath="/errors" .../>}
  topbar={<Topbar page="errors" stateView={null} … />}>` 包住现有内容。
- `web/render.tsx`：不变（`ErrorsApp` 内部包壳）。
- **不动** `routes.ts` 的 `PageKey`/`bootstrapPage`/`pageForPath`、`app.tsx`、`build.ts`
  （N7：不进主 bundle）。

**验收**
- SSR 首帧含 `tc-shell`/`tc-side`/`tc-top` 与 `/errors` 的导航激活态；`/errors.js` 仍是 bundle。
- 只读（LAN）仍渲染，`NavSidebar` 显示锁标识。

### Phase 5 — 瞬时错误自动撤销（D3，S2）

**改动文件**
- `server/api/error-registry.ts`：`misses`/`resolved`、`applySweep` 两拍判据、
  `appendResolvedLedger`、`getResolvedErrors`、`commitSweep` 返回 resolved 变化。
- `server/server.ts` / `web/app/errors-app.tsx`：resolvedItems 投影与页面块。
- `docs/nn/console.md:89`：账本从「每 fp 一行」改为「seen 行 + 可选 resolved 行」。

**验收**
- 纯函数用例：瞬时事件拍 1 出现、拍 2 缺席 ⇒ 仍在；拍 3 缺席 ⇒ 进 resolved 且账本有
  `kind:'resolved'` 行；非瞬时拍 2 即消失且无 resolved 行；重现清 resolved。
- 现有「同 fp 只一行 seen」用例保留，新增 resolved 行断言。

### Phase 6 — 采集与课程解耦（D2，S3）

**改动文件**
- `server/api/error-scan.ts`：
  - 扫描面 = **全局组件一次**（`selfNode`/`hubServer`/`localWorker`/`cloudflared`，
    缓存键 `global:<abs>`）+ **每门课的 `trainingLoop`**（缓存键 `course:<abs>`）；无开课闸。
  - `logScanCache` 每拍淘汰不在扫描集的键；每文件最多 20 个不同 `fp`（超出折叠计数）。
  - 事件 `activity` 按文件 mtime；历史课不盖 `now`。
- `server/api/component-meta.ts` / `stack/specs.ts`：全局日志与课日志路径改用 `tmpLogsDir()`，
  默认值不变（测试重定向才生效，避免读真实 `tmp/`）。
- `server/api/error-registry.ts`：`liveHubUrl` 去掉 `effectiveCourse`（`hubCandidates` 已忽略课程）。
- `web/app/errors-app.tsx`：不读 `?course=`（现状保持）。

**验收**
- 夹具：两门课 + 一份全局日志 ⇒ 全局事件只出现一次（不是 ×2）；无 `training-enabled.txt`
  的课的错误仍进登记表；旧 mtime 的课标 `historical` 且不进默认视图/告警坞。
- 内存：185 门夹具（或真实树）连跑 3 拍，事件数 ≤ 全局组件数 + 20×课数；RSS 基线记录在
  commit message/plan 附录（不写死数字）。
- `?course=` 切换不影响 `/api/errors`（回归）。

### Phase 7 — 文档与记忆

- `DECISIONS.md`：D1（包壳而非进主 bundle，重申父 plan N5）、D2（全局去重 + historical）、
  D3（两拍撤销 + resolved 账本行）、D4（访问日志按状态码）、D5（分类规则）。
- `docs/nn/console.md`：错误页使用说明 + 账本新行类型。
- `.workbuddy/memory/YYYY-MM-DD.md` 记一笔。

## 5. 风险与反探针

| 风险 | 缓解 | 反探针 |
|---|---|---|
| 去噪正则误杀真错误 | 续行只按 traceback 集；访问行只按行首结构 | 去掉续行过滤 ⇒ 事件数变多 ⇒ 红 |
| 5xx 被永久隐藏 | 只跳 <500 | 把 5xx 也跳过 ⇒ 用例红 |
| 去闸后内存回潮 | 全局一次 + 每文件 20 fp + 缓存淘汰 | 恢复嵌套循环 ⇒ 全局事件数 ×课数 ⇒ 红 |
| 历史错误变「此刻活跃」 | activity + 文件 mtime | 去掉 activity ⇒ 历史课事件进告警坞 ⇒ 红 |
| 撤销抖动 | 两拍 + 重现清 resolved | 一拍撤销 ⇒ 用例红 |
| 账本契约破坏 | seen/resolved 两类行 + replay 跳过 resolved | 写第二行 seen ⇒ 现有用例红 |
| 分类规则漂移 | 单一规则表 + 逐类夹具 | 删任一条规则 ⇒ 对应用例红 |

## 6. DoD

- [ ] 根 `bun run check` 绿；`cd dashboard && bun run typecheck && bun run test` 绿；
      `bun run build` 过（本 plan 只动 dashboard，根构建应零变化）。
- [ ] Phase 0–6 新增/更新用例全绿，且每条判据有反探针（§5）。
- [ ] `/errors` SSR 首帧含 `Shell` 侧栏/顶栏；仍是 `/errors.js`（不进主 bundle）。
- [ ] 默认视图不出现 M1–M5 类噪声与 historical 行；开关可放行。
- [ ] 瞬时错误连续 2 拍缺席 ⇒ 活跃表消失 + `resolvedItems` 出现 + 账本 resolved 行。
- [ ] 无 `training-enabled.txt` 的课程错误可见；全局组件事件不随课数倍增。
- [ ] `ErrorEvent`/`ErrorListItem`/`/api/errors` 全带 `classification` 与 `activity`。
- [ ] 账本旧行读取不报错；旧行不写回。
- [ ] 无新增 Simulation `Math.random()` / 模块级玩法状态 / canvas UI（不碰仿真层）。
- [ ] 决策已记入 `DECISIONS.md`，memory 已追加。

## 7. 验证命令

```bash
cd dashboard && bun run typecheck && bun run test
bun run check
bun run build
```

## 8. 验收记录（2026-10-19 实现后）

- **185 门夹具**（`dashboard/tests/server-api-error-scan.test.ts`「185 门课 × 3 拍」）：
  1 份全局日志 + 185 份 trainer 日志 ⇒ 事件 186 条（全局件只 1 条，不 ×185）；
  `logScanCacheEntryCount() === 186`（一条缓存 = 一个日志文件）；第 2/3 拍签名未变 ⇒ 事件数与
  条目数都不涨；清掉 100 门课 ⇒ 条目当拍降到 86。这比「RSS ±10%」可复现：删掉「全局按绝对
  路径去重」或「扫描面淘汰」任一条，该用例当场红。
- **真树 30 分钟 RSS 采样未在本机执行**：设计时工作树 `tmp/` 已清理（§1.4），没有可复现的
  185 门活日志。操作员在有活训练的机器上补一次：打开控制台 30 分钟、每 60s 读一次 `/api/mem`，
  同时看缓存条目数 ≈ 全局组件数 + 有日志的课程数（不是 ×课程数）。这是回归观测，不改代码。
- 门禁：dashboard `typecheck` / `test` 全绿；根 `bun run check` / `bun run build` 绿（计数见
  commit message 与 `DECISIONS.md` 同日条目）。

## 9. 一句话给接手 agent

**先 Phase 0 止血，再分类；去开课闸与全局去重必须同批（F1），历史活动度先于去闸（F2）；
瞬时撤销只承诺「两拍 + resolved 环」，不承诺「源恢复」；账本只追加，不回写。**

判据优先做成可复现的用例（185 门夹具的条目数/事件数），不要只留「RSS ±10%」这种一次性测量——
后者只能当回归观测，不能当门禁。
