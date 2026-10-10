# Plan: self-node-ledger-pid-desync — 账本 pid 不许再冒充「进程已退出」

> **状态**：**待评审**（2026-10-10 立）。范围 = `dashboard/**` 四个文件 + 一个 `ProcSpec` 可选字段；**不碰** agent 侧、不碰训练侧。
> **一句话**：把「账本 pid 死了」从读面的**判决依据**降级为**开销提示**，并给「换代」补上第二个事实源
> （组件自报 pid）——今天那条「agent 活得好好的、面板却说已退出」的假红，根因就是这三件事同时成立。
> **动机（2026-10-10 现场）**：self 节点 agent 11:47:31 自重启换代（pid 8340 → 21300），控制台账本没跟上，
> 于是组件卡红点 `exited`、`/api/pool.selfStatus=null`（节点统计页「agent 未启动」+ 磁盘徽标消失），
> 而**同一页**的 `nodes.self.online=true`、ping 2ms、达标 10/10、`selfDisk.level=ok` 全是好的。
> 操作员看到的是「采集节点挂了」——一个**伪结论**。且控制台上**没有任何按钮能修**（见 §8）。

---

## 0. 现象与证据（2026-10-10 12:40 现场，只读实测）

| 读面                             | 值                                                          | 真假        |
| ------------------------------ | --------------------------------------------------------- | --------- |
| `GET /api/state.components[selfNode]` | `{"status":"exited","pid":8340,"healthy":null}`            | **假**（红点「已退出」） |
| `GET /api/pool.selfStatus`      | `null` ⇒ 面板「agent 未启动」+ `SelfDiskBadge` 消失               | **假**      |
| `GET /api/state.nodes[self]`    | `online:true` · ping **2ms** · `codeHash` 与本机一致            | 真         |
| `GET /api/pool.nodes[self]`     | `status:"healthy"` · 达标 **10/10** · `versionOk:true` · `lastContrib 172` | 真         |
| `GET /api/state.selfDisk`       | `{freeMB:30263, level:"ok"}`                               | 真         |

**真身**：agent 跑在 **PID 21300**（`bun.exe`，今日 11:47:31 起）。三个独立来源一致：
`tmp/dist-agent/agent.lock` = `{"pid":21300,"bootId":"8f10c23a",...}` · `/v1/ping` · `/v1/status`。
账本里是 **8340**，`tasklist` 查无此进程。⇒ 只有「账本」这一个读数是坏的，而它恰好是三个读面的唯一输入。

---

## 1. 根因链（四段，逐段带位置）

### ① 触发：agent 自重启换 pid（设计内行为）

`tmp/sampler-agent.log:11:47:31` → `restart: parent pid 8340 -> child pid 21300`；`:11:47:36 listening … pid=21300 bootId=8f10c23a`。
路径 = `/v1/restart` 交接：`tools/agent/sampler-agent.ts:1930-1962`（先 `activeServer.stop(true)` 释放端口，再 spawn detached 子进程）。
**这段没问题**，是 agent 的单实例换代语义（`agent-child.pid` / `instance guard` 都在）。

**触发者是谁（2026-10-11 补，评审 F8）**：`/v1/restart` 的**唯一**调用方全在控制台**之外** ——
`trainer/dispatch.py:179` 的 M8 主动升级（ping 发现 `codeHash` stale ⇒ `common/distribution.py:550`
`request_upgrade_guarded` POST `/v1/restart`）、`nn-training/tools/dist_upgrade_cli.py`、手工 curl。
控制台自己**从不** POST 它（`server/actions/restart.ts` 是监督器的 spec 重建回调，不是 HTTP 重启）。
⇒ 两件事同时成立：① 控制台**靠本地记账永远看不到新 pid**（换代不是它发起的）；
② 这个 desync 会**反复**发生（每次落地一个进 `codehash-files.txt` 的提交 ⇒ self agent 变 stale ⇒ 被重启）。
这正是 S3（监督路径自收敛）不可替代的论据。

### ② 账本没写：控制台「看到活着」就早退了

`dashboard/src/stack/hub.ts:118-123`：

```ts
if (await selfNodeHealthy(cfg)) { ok(`self-node 已在运行 (port ${cfg.rl.agent_port})`); return }  // ← 不写账本
...
saveComponent('selfNode', { pid: r.pid, entry: SELF_NODE_ENTRY, startedAt: Date.now() })          // :130 只有自己 spawn 才记
```

console.log 实证：`[12:33:34] start x24-stack/selfNode → ok: self-node 已在运行 (port 8443)`、`[12:35:39]` 又一次
—— 两次都没刷新账本。**控制台明知 8443 在应答（还验了 authKey），却不把「谁在应答」记进账本。**

### ③ 自愈失效：端口归属探测在本机拿不到 PID

`server/exit-watchdog.ts:323-380` 就是为这件事写的（文件头：「判定一条确证死 pid 是否只是**进程换代**」）：

```ts
const newPid = (io.ownerPidOf ?? ((it, stale) => ownerPidOf(it.key, it.course, stale)))(item, entry.pid)
...
if (newPid && newPid !== entry.pid) { … repair … }
else if (!warnedGhost.has(onceKey)) { … 「跳过意外退出标记（同一 pid 只报一次）」… }   // :371-378 —— 只告警，不修
```

`ownerPidOf`（`exit-watchdog.ts:274-278`）→ `portOwnerPids(port)`（`core/proc.ts:55-70`）：Windows 走
`netstat -ano`，取 `LISTENING` 行尾列 PID，**过滤 `pid > 0`**。而 2026-10-10 现场本机 `netstat -ano` 的 LISTENING 行
**PID 全是 0**（实测 9/9：8443×2、7890×3、8787×2、8900、…；用 bun 复现同一条解析路径，结果一致）。

> **修订（2026-10-11，评审 F13）**：这条**不是本机属性**，是**逐 socket、随时机**的。同日复跑
> `netstat -ano | grep LISTENING` 得到**混合值**：`7890×2 / 8787 / 7786` = `0`，而 `15864 / 24792 / 16876` 有真值。
> ⇒ 结论不变（更该不依赖单一来源），但「PID 全是 0」不能当常量读。
⇒ `portOwnerPids` 返回 `[]` ⇒ `newPid = null` ⇒ 落到 `:371-378`，**账本永不修正**。

console.log 里那行就是它，一字不差：

```
[11:47:36] ⚠️ [console] self-node (采集节点) 账本 PID 8340 已消失，但服务仍在应答
           → 视为进程换代，跳过意外退出标记（同一 pid 只报一次）
```

对照 10-09 那次是**修成功**的：`[23:35:25] … → 账本 pid 修正为 8340（不计意外退出）`。
⇒ 「netstat 能不能给出归属」在本机**是环境相关的**，而 `ownerPidOf` **没有兜底**：
`core/proc.ts:98-104` 的 `portOwnedBy` 早就写了同款纪律（「清单空 ⇒ 探测不可用 ⇒ 不据此判死」），
`ownerPidOf` 漏了这一条。

### ④ 假读数三处，全是同一个陈旧 pid 的函数

| 位置 | 代码 | 后果 |
| --- | --- | --- |
| `api/views.ts:89-92` | `const alive = pidAlive(e?.pid); status = e ? (alive ? 'running' : 'exited') : 'stopped'` | 卡片红点「已退出」 |
| `api/views.ts:40` | `if (!pidAlive(e?.pid)) return`（健康探测的前置闸） | `healthy` 恒 null |
| `api/pool.ts:112-115` | `if (!pidAlive(reg.selfNode?.pid)) return null` | `selfStatus` 恒 null ⇒ 「agent 未启动」+ 磁盘徽标消失 |

---

## 2. 语义规格（先定死，再写码）

| # | 规则 | 依据 |
| --- | --- | --- |
| **S1** | **账本 pid = 开销提示，不是判决**。它只允许决定「探测超时给多长」，**不得单独**推出「未启动 / 已退出」。 | 陈旧账本已在生产上被证实（本案）；`fetched pid` 与 `living pid` 是两个不同的东西 |
| **S2** | **换代事实源优先级**：① 端口占用者（netstat/lsof）→ ② **组件自报 pid**（`/v1/ping` 响应里的 `pid`；`tmp/dist-agent/agent.lock` 是等效的离线候选，本版**不实现**，登记为预备案 = 评审 F10）→ ③ 放弃，只告警（**保留**今天的行为，但文案升级为「修不了，需人工」；去重键已天然是「每换代周期一次」= `watchId#stalePid`，不用改） | ① 在 Windows 上不可靠（PID 0，且逐 socket）；② 免费且权威（agent 自己写的） |
| **S3** | **`exited` 的充要条件**：条目存在 ∧ pid 已死 ∧ **健康探测不通（或该组件没有探测端点）**。 | 否则「有实例在服务」被渲染成「已退出」 |
| **S4** | **不变量不变**：修 pid 前仍必须过 `pidClaimedElsewhere`（`exit-watchdog.ts:286-292`）——**绝不把别的课程/组件的 pid 写进本条**。 | 2026-09-17 跨课错配前科 |
| **S5** | **零新增探测来源**：② 的来源就是既有 `/v1/ping`（`component-meta.ts:31` 已经在探它），不是新开一条链路。 | `plan/self-node-disk-alert` G7 同款纪律 |

---

## 3. 目标 / 非目标

### 3.1 目标

| # | 目标 | 判据 |
| --- | --- | --- |
| **G1** | 账本 pid 陈旧时，`selfStatus` 仍如实上屏 | 死 pid + `/v1/status` 200 ⇒ `selfStatus !== null`；面板显示 workers/inflight/done + 磁盘徽标 |
| **G2** | 组件卡不再因陈旧 pid 假红 | 死 pid + 探针 200 ⇒ `status==='running'`、`healthy===true` |
| **G3** | 账本能自愈（不限 selfNode） | `ownerPidOf=null` 且有自报 pid ⇒ `repair(item, 自报pid)`；且 `pidClaimedElsewhere` 仍然生效 |
| **G4** | 「已在运行」分支也记账本 | `stepSelfNode` 健康早退时，若自报 pid ≠ 账本 pid ⇒ `saveComponent` 更新 |
| **G5** | 真死仍然显示 `exited` | 死 pid + 探针不通 ⇒ 行为与今天**逐字一致**（不引入新假绿） |

### 3.2 非目标

| # | 不做 | 理由 |
| --- | --- | --- |
| **N1** | 换掉 `netstat` 方案 / 引第三方进程表库（`GetExtendedTcpTable`、`pidusage` …） | 加运行时依赖要论证（AGENTS §5）；本案的正解是不**依赖**单一来源，不是换来源 |
| **N2** | 动 agent 的 handoff / 自重启语义，或让 agent 反向写控制台账本 | agent 与控制台是单向观测关系；写面单向是既有设计（`registry.ts` 头部「一个写者」） |
| **N3** | 改「无登记 = `stopped`」语义 | `stopped`（`clearComponent` 后）与 `exited`（条在 pid 死）是两件事，合并会丢掉「有人悄悄死了」这个信号 |
| **N4** | 给远端节点做同样的兜底 | 远端节点/**任何非 selfNode 组件**都**没有账本条目**（账本只登记控制台起过的本机受管进程）⇒ 「账本 pid 陈旧」这个症状在那一边不存在。要做需先有实测动机（2026-10-11 评审 F7 修正：原稿写「远端 agent 不自重启」是错的——它们确实会吃 trainer M8 的 `POST /v1/restart`，只是无账本可陈旧） |
| **N5** | 顺手改 `stopAllManaged` 的端口兜底（`core/proc.ts:140-160`，同受 PID 0 影响） | 同族但**另一个后果**（清场漏杀）；本案只治「显示」，免得一个 PR 两个语义 |
| **N6** | 把同形的另两处读面一并改掉：`api/overview.ts:60::sharedTrainerAlive`（返回 `pidAlive(ent?.pid)`）与 `state-view.ts:40` 的 `loopAlive`（喂收官横幅） | 它们确实是同形读面（2026-10-11 评审 F4），但**触发源不同**：`hubServer`/`trainingLoop` 的换代都经过控制台 ⇒ 无实测动机。
⇒ §2-S1 的口径**只针对 selfNode**；本表两条登记为预备案（触发条件 = 实测到一次这两条读面產出假结论） |

---

## 4. 改动清单（S0 → S4，逐条带验收）

### S0 — 复现（**先红**；AGENTS §7「没写失败测试就不算修好」）

四处都是纯函数 + 注入面，**不许联网、不许读真账本**（`exit-watchdog.test.ts:22-31` 已有
`BCITY_REGISTRY_FILE` 重定向先例，pool/views 侧照用）。

| # | 用例 | 现状 |
| --- | --- | --- |
| **T1** | `fetchSelfStatus`：账本 pid = 死 pid，`/v1/status` 返 200 ⇒ 返回值非 null | **红** |
| **T2** | `componentViews`：selfNode 条目 pid 死、探针 200 ⇒ `status==='running'` ∧ `healthy===true` | **红** |
| **T3** | `classifyExit`：`ownerPidOf→null`、健康 true、`probePidOf→30332` ⇒ `repair` 被调、pid=30332 | **红**（现有用例 `exit-watchdog.test.ts:506-521` 把「不修」写成了规格，本步要**改它的期望**并注明理由） |
| **T4** | `stepSelfNode`：健康 true、账本 stale ⇒ `save` 被调、pid = 自报 pid | **红** |

> T2/T4 需要给 `computeComponentHealth(cfg)` / `stepSelfNode(cfg)` 加**可选 io**（默认真实实现）。
> 这与本仓既有写法同源：`reclaimPort(cfg, io)`、`classifyExit(item, io)`、`portOwnedBy(pid, port, io)`。

### S1 — `pool.ts`：前置闸 → 超时预算（G1）

`fetchSelfStatus`（`api/pool.ts:112-155`）：删掉 `if (!pidAlive(...)) return null`，改为

```
探测照做；超时恒 4000（与既有实现逐字一致）   // 账本 pid 不参与任何判决——连"等多久"也不参与
```

> **修订（2026-10-11，评审 F1）**：原稿写「`pidAlive(账本pid) ? 4000 : 1200`」= **用被证实会陈旧的账本 pid 决定行为**
> （本案要消灭的那类错误的缩小版），且恰好把「账本陈旧」这一档（本案的修复目标 / S3 失效后的**唯一**读路）
> 的预算压小 3.3×；而它是**后台 SWR 探测**（`getPoolProbes` 的注释：请求路径上永不探测）⇒ 4s 换不到任何东西。
> 必补用例：桩在 **1500ms** 才应答 ⇒ `selfStatus !== null`（1200ms 预算下必红）。

并导出 + 加 io 注入（`{ probe }`）供 T1 使用。注释里写明**为什么不能拿 pid 判决**
（本案：账本是上一代进程的）。

### S2 — `views.ts`：`exited` 的充要条件（G2 / S3）

`computeComponentHealth`（`views.ts:31-75`）：

- `if (!pidAlive(e?.pid)) return` 改成：**只有 `HEALTHY_PORTS[key]` 存在的组件（= selfNode / hubServer 两个）
  在 pid 死时仍探一次**，超时与正常路径同值（1500ms —— 评审 F1 同理：不给陈旧档更小的预算）；
  其余（`cloudflared` / `trainingLoop` / `localWorker`）pid 死 ⇒ 原样 return。
  即 **「有探针」的定义 = `HEALTHY_PORTS[key] !== undefined`**。
  > **修订（2026-10-11，评审 F3）**：原稿只写「有探针的组件」，会把 `cloudflared` 按字面算进来——
  > 而它的健康是 **hub 派生**的（`cloudflaredHealthy`：hub 通 ∧ 条目无 url ⇒ `true`），
  > 于是「隧道进程已死 + hub 活着」会被渲染成 `running` ⇒ **破 G5**。钉子用例：
  > cloudflared 条目 pid 死 + hub 探通 ⇒ 仍 `status==='exited'`。
- `componentViews`（`views.ts:89-92`）：`alive` 改成 `pidAlive(e?.pid) || h.get(key) === true`。
  即 **「有实例在服务」也算 alive** ⇒ `status='running'`。`healthy` 语义不变（running 时才看探测值）。

> 成本核算：多探只发生在「**条目在、pid 死**」这一档；正常 stop 会 `clearAnyComponent` ⇒ 条目消失 ⇒
> `status='stopped'` 且**不探**（`e` 为空）。所以额外开销 = 每个「悄悄死掉/换代」的条目每拍一次
> **1.5s** 超时，与机群探测的 **5s** 重算周期（`SNAPSHOT_REFRESH_MS`；原稿写 3s = 评审 F11 的口径修正）同拍。
>
> ⚠ **连带纪律（评审 F2）**：今天「pid 死 ⇒ 直接 return」对测试是**隐性隔离网** ——
> 本仓有 ≥14 个调 `api.buildStateView()` / `componentViews()` 的用例**不重定向 `BCITY_REGISTRY_FILE`**
> （吃本机真账本：`web-ssr-console` / `web-ssr-readonly` / `web-ssr-log-page` / `web-ssr-hero-eval-toggle` /
> `web-app-evalsummary` / `web-view-spark` / `server-api-state-view` / `server-api-snapshot-cache` /
> `server-api-local-chips` / `server-api-course-{override,ctx,archive}` / `evalboard-disabled` /
> `gate-halt-platform` / `server-api-logs`）。改掉之后，本机一旦存在陈旧条目，它们会真发 loopback 请求，
> **结论随「本机此刻有没有 agent 在跑」变**（2026-09-09 / `self-node-disk-alert` 评审 F11 同族第三次）。
> ⇒ 交付物含「跑**全量**套件并把依赖真账本的那几条钉住（重定向或注入健康表）」，不是只跑改过的文件。

### S3 — 换代第二事实源（G3 / S2）

1. `core/types.ts`：`ProcSpec` 加**可选** `probePid?: () => Promise<number | null>`。
2. `stack/specs.ts::selfNodeSpec`：实现为 `GET http://127.0.0.1:<agent_port>/v1/ping`（Bearer = self authKey）
   ⇒ 取响应体 `pid`（**字段已存在**，实测 `{"ok":true,"pid":21300,...}`）。失败/超时 → null。
3. `server/exit-watchdog.ts::classifyExit`：`newPid` 为 null 时，**再问一次** `io.probePidOf ?? restartSpecFor(key,course)?.probePid`
   ⇒ 拿到且 ≠ 账本 pid ⇒ 走既有 `repair` 分支（**仍过 `pidClaimedElsewhere`**，S4）。
   `:371-378` 的 branch 保留为「两条来源都拿不到」的兜底，文案补一句「需人工介入」。

> 为什么把 `probePid` 挂在 spec 上而不是硬编码 selfNode：`classifyExit` 是**通用**换代判定，
> 按 key 分支会把「哪些组件能自报 pid」变成又一份名单（本仓最忌的第二份名单）。

### S4 — 「已在运行」也记账本（G4）

`stack/hub.ts::stepSelfNode`：`selfNodeHealthy(cfg)` 为真时，取 `spec.probePid()`；
若回值存在且 ≠ `entryForCourse(reg,'selfNode','')?.pid` ⇒ `saveComponent('selfNode', {…entry, pid})`
+ 一行 log（`self-node 账本 pid 修正 X → Y（换代接管）`）。

> 这一段与 S3 共用同一个 `probePid`，两条路径互为补充：S4 在**动作路径**上修（用户点「启动」立刻收敛），
> S3 在**监督路径**上修（没人点按钮也会收敛）。

---

## 5. 测试与门禁

- T1–T4 **先红后绿**；T3 的期望变更要写清「为什么原期望是错的」（原用例把「拿不到新 pid」当成了终态，
  而它其实是**探测失败**，不是**换代不存在**）。
- 新增一条钉子用例：`portOwnerPids` 返回 `[]` 时 `ownerPidOf` 必须返回 null（**不要**为了让测试过而改它的语义），
  真正的兜底住 `classifyExit` —— 这样「netstat 看不见」与「根本没有换代」两种情况仍然可区分。
- 门禁：`cd dashboard && bun run typecheck && bun run test`；`bun run check`（根项目）应零变化（未动 `src/`）。
- 全量套件按 AGENTS §5.3 跑法；`bun test` 带 `--parallel=<物理核> --timeout=50000`（launcher 已代劳）。

---

## 6. DoD

- [ ] S0 四条用例在**未改动代码**上确认红，改完转绿。
- [ ] 现场验收（本机真栈）：`/api/pool.selfStatus` 非 null；`components.selfNode.status === 'running'`；
      节点统计页出现 `workers/inflight/done` 与磁盘徽标；与 `nodes.self.online` 不再自相矛盾。
- [ ] 手工复现一次换代（`POST /v1/restart`）→ **≤2 个看护周期（≈8s）**账本 pid 自动跟上（S3），
      **且**点一次「启动」(S4) 也跟上。
      > **修订（2026-10-11，评审 F5）**：原稿写「4s 内」按字面**不可能通过** —— `runExitCheck` 先过
      > `nextExitFailures` 的**两帧锁**（连续两帧死 pid 才 `out.push`）才轮到 `classifyExit`，而看护是 4s 一拍
      > ⇒ 首次修复窗口 ≈ 2 拍。另记：`deadSeen.delete(id)` 在确证时执行 ⇒ S3 失效时**每 8s 重试一次**
      > （不是「一次」），这条进 §7 风险表的处置列。
- [ ] 真死路径不退化：`kill` 掉 agent 后，卡片仍变 `exited`（红点）+ `healthy=null`，`selfStatus=null`。
- [ ] 无新增 `Math.random()` / 模块级可变状态 / canvas UI（AGENTS §2）；无新运行时依赖。
- [ ] `cd dashboard && bun run typecheck && bun run test` 绿；`bun run build` 过。
- [ ] `DECISIONS.md` 记一条：**「账本 pid 不是存活判据」**（含本案现场）；memory 追加一行。

---

## 7. 风险 / 回滚

| 风险 | 量级 | 处置 |
| --- | --- | --- |
| 死 pid 条目每拍多付一次 1.2s 探针 | 每个死条目、每 3s 一拍，与 SWR 同拍 | 只对「有探针端点」的组件生效；`stopped`（无条目）不探 |
| 端口被**本机另一个** agent 实例占着 ⇒ 自报 pid 指错 | 低：agent 有单实例锁（`instance guard`），端口被占即意味「有实例在服务」 | 仍过 `pidClaimedElsewhere`（S4）；`bootId` 可作为后续加固的锚 |
| 自报 pid 被写进错条目 | 同上，跨课错配是本仓前科 | `pidClaimedElsewhere` **不放松** |
| 探针在 agent 忙时变慢 | 1.2s 超时 + 失败即 null（不改判） | 与既有 `httpOk` 同款 |

**回滚**：改动集中在 `api/pool.ts` / `api/views.ts` / `server/exit-watchdog.ts` / `stack/hub.ts` / `stack/specs.ts` / `core/types.ts`
—— 逐文件可独立回退；S1/S2 是显示层，S3/S4 是收敛层，**没有数据迁移、没有写面变更**。

---

## 8. 运维止血（不改码；**必须知道「UI 修不了」**）

- **可以**：把 `tmp/training-start/registry.json` 的 `selfNode.pid` 手工改成 21300（改完 `selfStatus` 立即恢复）。
  注意控制台会整份重写该文件，存在短赛跑窗口；且**下次换代又会陈旧** ⇒ 只是止血。
> **时态（评审 F12）**：本节只描述**改码前**的行为。S1 拆掉账本闸之后，「停止 → 启动」这条路
> 也能读回 `selfStatus`（`fetchSelfStatus` 不再看账本 pid）—— 下面的「不可以」届时应重写。

- **不可以**：控制台的「停止 → 启动」。`actions/stop.ts:56-68` 只 kill 账本里的死 pid（端口兜底同样因 PID 0
  找不到 21300）然后 `clearAnyComponent`；再「启动」时 `selfNodeHealthy()` 仍为真 ⇒ 早退不写账本
  ⇒ 卡片从「已退出」变成「stopped（无登记）」，`selfStatus` **依旧 null**。等于把假红换成假灰。

---

## 9. 未证实的观察（诚实标注，不含推论）

- **同一台机器上 `portOwnerPids` 曾经能用、现在不能**：`console.log:23:35:25` 修成了 8340，
  `console.log:11:47:36` 落到「认不出占用者」分支。两行之间发生了环境变化（console 于 10-09 23:47:11
  换过一次进程；agent 于 10-10 11:47:31 走 handoff 换过一代），**变化的确切成因未定位**
  （elevation / 内核态 / socket 归属不可见，三者都可能）。这**不影响**方案选型：无论根因是哪个，
  「单一来源判死」都是错的，S2 的第二事实源是充分且必要的修法。
- `netstat` 的 PID 0 现象**不是 selfNode 独有**（见 §1③ 的 2026-10-11 修订：混合值），
  所以本案的 S3 兜底对 `hubServer` 同样可用（该组件 spec 未实现 `probePid`，属**预备案**而非「漏做的一行」；
  见 §10 的 F10 处置）。

---

## 10. 评审处置（2026-10-11，`plan/self-node-ledger-pid-desync.review-bf.md`）

**F1–F14 全部采纳**（无驳回）。下表 = 每条评审的处置与落点；正文相应位置已就地修订
（§1③ 的 PID 0 措辞、§2-S2 的事实源、§4-S1 的超时、§4-S2 的「有探针」定义与成本、§6-DoD 的 8s、§8 的时态）。

| # | 级别 | 处置 |
| --- | --- | --- |
| **F1** | P0 | **采纳**：超时**恒 4000**（删掉 `pidAlive ? 4000 : 1200`——不给陈旧档更小预算，理由见 §4-S1 修订块）；
必补用例「桩在 1500ms 应答 ⇒ `selfStatus !== null`」 |
| **F2** | P0 | **采纳**：交付物含「跑**全量** dashboard 套件」，并逐条查明/钉住依赖真账本的用例（§4-S2 的连带纪律块）；
实施时以**实际跑绿**为准，不以「只跑改过的文件」交差 |
| **F3** | P1 | **采纳**：「有探针」钉死为 `HEALTHY_PORTS[key] !== undefined`；`cloudflared` 明确不走这条；加钉子用例 |
| **F4** | P1 | **采纳（取 (a)）**：S1 **只针对 selfNode**（理由：`hubServer` / `trainingLoop` 的换代路径都经过控制台，
不存在「控制台之外的触发源」）；`overview.ts::sharedTrainerAlive` 与 `state-view.ts:40` 的 `loopAlive` **列进非目标**，
并写明它们是同形读面、留给将来有实测动机时再做 |
| **F5** | P1 | **采纳**：DoD 改「≤2 个看护周期（≈8s）」+ 风险表补「S3 失效时每 8s 重试」 |
| **F6** | P1 | **采纳**：③ 档补三用例（两来源皆 null ⇒ `alive` ∧ 不修 ∧ 文案含「需人工」；同条目连续 N 拍只报一次；换 stale pid ⇒ 允许再报）；
**去重键不改**（`watchId#stalePid` 本身就是「每换代周期一次」——「升级为每周期只报一次」这件事今天已成立，只是没被写下来/钉住） |
| **F7** | P2 | **采纳**：N4 的理由换成「远端节点**没有账本条目** ⇒ 本 desync 不适用」（删掉不成立的「远端 agent 不自重启」——
它们确实会吃 trainer M8 的 `POST /v1/restart`，`dispatch.py:179` → `common/distribution.py:550`） |
| **F8** | P2 | **采纳**：§1① 补「**换代触发者在控制台之外**（trainer M8 stale-codeHash 主动升级 / `tools/dist_upgrade_cli.py` / 手工 curl）」
——它同时解释了「为什么反复发生」与「为什么控制台靠本地记账永远看不到新 pid」 |
| **F9** | P2 | **采纳**：`healRecoveredErrors` 的 `ownerPidOf(...) ?? e.pid` 同样接上第二事实源（否则它会把**陈旧 pid**
写回去并报「服务已恢复应答」——与 S1 反向） |
| **F10** | P2 | **采纳**：`agent.lock` 登记进 §2-S2 的候选清单（本版不实现）；`hubServer` 的 `probePid` 改为**预备案**（不再是「可选扩展」） |
| **F11** | P2 | **采纳**：3s → **5s**（`SNAPSHOT_REFRESH_MS`）；DoD 的 `bun run build` 标注「预期零变化，仅作保险」；
行号：`pool.ts` 闸位 `:114`、`classifyExit` `:323` 起、T3 原用例 `:506-521`（评审核对后确认 plan 原值正确） |
| **F12** | P2 | **采纳**：§8 加时态标注（本条只描述改码前） |
| **F13** | P2 | **采纳**：§1③/§9 的「PID 全是 0」降级为「**该时刻**该 socket 是 0」，并记录 2026-10-11 的混合值复测 |
| **F14** | P2 | **采纳**：「零新增探测来源 ≠ 零额外请求」写进 §4-S3：`healthy()` 已打过一次 `/v1/ping`，
`probePid()` 是同一拍里**第二发**（S4 的 `stepSelfNode` 尤其明显）——成本很低但别让读者以为零成本 |
