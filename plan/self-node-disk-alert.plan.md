# Plan: self-node-disk-alert — 低盘从「静默」变「响亮」（4–5 GB 预警档 + 告警坞条目）

> **状态**：**已实施**（2026-10-09；含一轮评审 F1–F11 处置，见 §11）。
> **批次决议（Q1）**：**A+D 同期落地**——agent 上报与 dashboard 消费**同一批签入**，磁盘事实**单源 = agent**。
> 原 Q1① 的「D 先行 + dashboard 本地兜底阈值」**整段撤销**（评审 F1：A 落地前 ping 上一个磁盘数都没有 ⇒
> D 先行的 `selfDisk` 恒为 `null`，告警永不出现）。  
> **动机（2026-10-08 实测事故）**：self 节点 08:13:40→09:55:14 共 **1h41m** 不参与 rollout/eval，  
> 原因是 D: 可用空间跌破 agent 的 2048 MB 硬地板 ⇒ 每个作业回 `503 low disk` ⇒ trainer 连软失败 9 次后  
> **本轮停派**。全程 `node self: offline` **0 次**、控制台**没有任何告警**、`diskFreeMB` **从未上屏**  
> ——故障在操作员视野里完全不存在。本案把它变成看得见、看得懂、可确认的一条告警。  
> **一句话**：给 `diskFreeMB()` 加一层 **预警档（4096 MB）**，把档位经 `/v1/ping`+`/v1/status` 报出来，  
> 在 dashboard 告警坞出一条分级条目；**拒收地板 2048 一个字不动**。

---

## 0. 口径冻结：两个阈值，不是一件事

| 名字                       | 值               | 语义                               | 谁执行       | 本案动不动            |
| ------------------------ | --------------- | -------------------------------- | --------- | ---------------- |
| **拒收地板** `DISK_FLOOR_MB` | **2048**        | 低于它，agent **拒绝接受任何作业**（HTTP 503） | agent（强制） | **不动**（见 §4.1 ★） |
| **预警档** `DISK_WARN_MB`   | **4096**（4 GiB） | 低于它，**只是告诉操作员**——agent 继续正常收活    | 纯观测       | **新增**           |

⇒ 「把告警提前到 4–5 GB」= **加一层预警**，**不是**把拒收线抬到 4–5 GB。后者是净伤害，理由见 §4.1。

---

## 1. 现状（带代码证据：数据已经在浏览器里，只是没上屏、没告警）

| 环节                  | 现状                                                                      | 证据                                                                                             |
| ------------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 阈值                  | **只有一个**硬地板，裸字面量 `2048`                                                 | `tools/agent/sampler-agent.ts:1969` `if (free !== null && free < 2048) → 503`                  |
| 阈值量的是什么盘            | **与 trainer 同一个 D:**（`<repo>/tmp/dist-agent` 的 statfs）                  | 同文件 `:756-766` `diskFreeMB()` + `:105` `WORK_DIR = join(REPO_ROOT,'tmp','dist-agent')`         |
| `diskFreeMB()` 的导出面 | 只在 `/v1/status` 出现一次                                                    | 同文件 `:2213`（`/v1/ping` 的响应体 **没有** 磁盘字段，见 `:2221+`）                                            |
| 服务端已抓               | **早就抓了**：`fetchSelfStatus` → `SelfStatus.diskFreeMB`                    | `dashboard/src/server/api/pool.ts:112-146`（`:136` 读 `diskFreeMB`）· `web/view/pool-types.ts:86` |
| 客户端已到手              | `NodeStats` 读 `pool.selfStatus`，但**只渲染 3 个字段**                          | `web/app/panels/NodeStats.tsx:291-301` → `workers · inflight · done`                           |
| 「磁盘」上屏处             | **0 处**（全仓 `grep -rn "diskFree" dashboard/src/web --include=*.tsx` 零命中） | 实测                                                                                             |
| 告警坞                 | **7 类条目，无一条与磁盘有关**                                                      | `web/view/alerts.ts`（`err`/`warn`/`info`/`history`，排序见 `:44-51`）                               |

**结论**：`diskFreeMB` 是一条**取到了、传上来了、然后被丢掉**的死字段。  
本次事故期间它一直在 1200–1900 之间——**只要有人看一眼就该知道要出事**，但没人看得到。

---

## 2. 两条缺口

### 2.1 R1 — 阈值只有「拒收」，没有「预警」

`:1969` 是一个**单档**判定：≥2048 全放行、<2048 全拒绝。  
⇒ agent 在 2047 MB 时才开始回 503，此时**唯一的表现**是「作业被拒 + 反复重排」，  
而这在 trainer 日志里看起来**和「节点很忙」一模一样**（都是 503；`common/distribution.py:317`  
把 503 一律当 `TRANSIENT_HTTP_STATUS`，日志写成 `requeued (attempt 1/3, busy)`）。  
⇒ 没有任何一条路径能让人**在跌破 2048 之前**知道它在往下掉。

### 2.2 R2 — 磁盘事实到不了告警坞

告警坞的数据源是 `stateView`（`web/app/app.tsx:520-534` 的 `buildAlerts({...})` 全部读 `stateView?.*`）。  
而 `diskFreeMB` 只存在于 **`/api/pool` 的 `selfStatus`**（`pool.ts:112`），  
它被 `NodeStats`（抽屉面板）独占消费 ⇒ **告警坞拿不到它**。  
并且 `stateView` 的装配链上**没有**任何一个环节携带磁盘事实：  
`computeFleetProbes`（`snapshot-cache.ts:112-174`）只出 `nodes/slowById/contribById/localContrib/pushProbes/componentHealth/contributionBrief`，  
而 `nodeProbeResults`（`views.ts:158-185`）**把 ping 响应体的其余字段全丢了**——只留 `online/codeHash/cpus`（`:173-175`）。

⇒ 两条链各自缺一小块：**agent 不说档位**、**stateView 不带磁盘**。

---

## 3. 目标 / 非目标

### 3.1 目标

| #      | 目标                             | 判据                                                                                                                           |
| ------ | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| **G1** | agent 提供**分级**磁盘事实（不止一个裸 MB 数） | `/v1/ping` 与 `/v1/status` 都带 `diskLevel ∈ {ok,warn,critical}` + `diskFreeMB` + `diskWarnMB` + `diskFloorMB` + `diskLevelSince`（进档时刻；F2 的 ack 事件身份） |
| **G2** | 告警坞出**一条**分级条目，全局可见、不切课可见      | `warn`→severity `warn`；`critical`→severity `err`（排序天然置顶，`alerts.ts:44-51`）                                                   |
| **G3** | 条目**说清后果与出口**                  | detail 必须含：当前自由 MB / 两条阈值 / **「跌破地板后 agent 对全部作业回 503，trainer 连续瞬时失败达阈值即停派 self（dist 与 eval 两条链路）」**/ 处置一句（清 `tmp/` 陈旧课程分片）。**F3**：后果句由数字判（`freeMB < diskFloorMB`），不由档位判 |
| **G4** | 防疲劳：档位内**不反复打断**，升档/复发**必然重弹**    | ack 事件身份 = **档位 × 进档时刻**（`self-disk\|self\|<level>@<diskLevelSince>`）：档位内不重弹；换档换键 ⇒ 重弹；**回到 ok 后再跌破 = 新 episode ⇒ 重弹**（F2：纯档位键会被只增不减的 `TC_ALERT_ACKS` 永久吃掉） |
| **G5** | 边界不抖                           | 回差（hysteresis）：入 `warn` <4096，出 `warn` ≥4608；入 `critical` <2048，出 ≥2560                                                      |
| **G6** | 磁盘数**上屏**（不再当死字段）              | `NodeStats` 头部徽标加 `disk <n>MB · <档位>`                                                                                        |
| **G7** | 零新增探测                          | 复用既有 ping（`nodeProbeResults` 已逐节点 ping，`views.ts:158`）与既有 `/v1/status`（`pool.ts:117`）                                        |

### 3.2 非目标

| #      | 不做                                                                | 理由                                                    |
| ------ | ----------------------------------------------------------------- | ----------------------------------------------------- |
| **N1** | **抬高拒收地板到 4–5 GB**                                                | ★ 见 §4.1——轨迹证明训练中盘会**长期停在 1.2–1.8 GB**，抬线 = 节点永久拒收    |
| **N2** | agent 自动清理 `tmp/`                                                 | 删文件是操作员的决定；agent 自作主张删训练产物是**不可逆**的（且违反「一个写者」直觉）      |
| **N3** | 浏览器通知 / 声音 / 桌面弹窗                                                 | 先看告警坞够不够；真要「更响」再加，别一次上三层机制                            |
| **N4** | 改 `ALERT_DOCK_DEFAULT_VISIBLE`（`alerts.ts:54`，=2）                 | `err` 已天然置顶；动折叠口径是既有裁决（banner-global-plan N3）         |
| **N5** | 改 trainer 侧 `TRANSIENT_HTTP_STATUS`（`common/distribution.py:317`） | 「`low disk` 被当背压反复重派」是**另一条**独立缺口（另案，见 §9-P1）；本案不碰训练侧 |
| **N6** | 远程节点**主动上报**或自动停采                                                 | 只做「报数 + 报警」；调度决策仍是 trainer 的事。★**报告事实的只有 self 节点**（`selfDisk`）：远端节点的低盘后果只表现为 503，没有本机那种「整段不参与」的可见后果 ⇒ 行级事实照报（ping 顺带覆盖全机群），告警只做 self；要扩到远端需先有「那次停派看得见」（P4 同族） |

---

## 4. 语义规格（先定死再写码）

### 4.1 ★ S1 — 为什么「4–5 GB」只能是**预警档**，不能是拒收线

实测磁盘轨迹（`grep disk_free= tmp/trainer-cluster.log`，x22-kr5/kr10 双课并行）：

| 时刻              | 自由 MB             |
| --------------- | ----------------- |
| Oct7 16:32      | 4881              |
| Oct7 18:34      | 4532              |
| Oct8 08:13      | **1746**（已破 2048） |
| Oct8 08:34      | 1320              |
| Oct8 09:03      | 1200              |
| Oct8 09:30      | 1302              |
| Oct8 09:44      | 1212              |
| Oct8 09:59（清盘后） | **44056**         |

两条推论：

1. **训练中盘会长期停在 1.2–1.8 GB**（Oct8 08:13→09:44 横跨 1.5h，**始终**在 1200–1750 之间）。  
   ⇒ 若把**拒收**地板抬到 4096：节点会在**几乎全部训练时段**拒绝一切作业——比本次故障**更糟**  
   （本次至少破了线才坏；抬线是不破线就一直坏）。**这是本案最重要的一条否决。**
2. **健康水位实测 ≈ 4.5–4.9 GB**（Oct7 16:32/18:34），**排水率 ≈ −205 MB/h**  
   （(4532−1746) MB ÷ (08:13−前一日18:34) ≈ 13.6h）。  
   ⇒ 取 **4096 MB** 作为预警：距地板 2048 有 **≈10 h** 处置窗口；且**高于**健康水位不会常亮。  
   ⇒ 取 5120 窗口拉到 ~15 h，但 4532 这种健康值会**贴着线**（易常亮）⇒ 默认 4096，  
   留 `--diskWarnMB` 旋钮给运维调（P2）。

⚠ **不确定性**：上述 205 MB/h 是**日均**口径，中途可能含人工清理（那会让真实排水更快、窗口更短）。  
故 detail 里**只报事实**（当前 MB + 阈值），**不承诺**「还有 N 小时」——不做会漂的推算。

### 4.2 S2 — 分级与回差（唯一实现住 agent）

```
classifyDiskFree(freeMB, prevLevel, { floor = 2048, warn = 4096, hyst = 512 })
  critical : freeMB < floor          ; 回出 >= floor + hyst      (2560)
  warn     : freeMB < warn           ; 回出 >= warn + hyst       (4608)
  ok       : 其余

// 进档时刻（F2）：`prevLevel` 住 agent 进程内模块变量，换档时刷新；
// 重启后 prevLevel='ok' 重算（语义：档位是**当前事实**，不是持久状态）。
diskLevelSince: 进档那一刻的 Date.now()（随 ping/status 上报）
```

- **回差 512 MB = 12.5%**：排水率 205 MB/h ⇒ 512 MB ≈ 2.5h，足以跨过重算抖动；  
  且 `<` + `>=` 严格不重叠 ⇒ **无抖振**（用例钉：4095→warn、4607→仍 warn、4608→ok）。
- ★ **F3（评审）——回差只做防抖，后果由数字说**：回差带 [2048,2560) MB 里 agent **正在正常收活**
  （门是 `< 2048`），而档位仍报 `critical` ⇒ **任何「已在拒收作业」式文案不得由档位推出**，
  只能由 `freeMB < diskFloorMB` 推出（告警已同时持有这两个数，用 agent 自己报的 floor，不硬编码）。
- `classifyDiskFree` 是**纯函数**，导出供根套件单测（不 boot agent）。放 `sampler-agent.ts` 内即可  
  （新文件要同步 `codehash-files.txt` 文件集，能不加就不加 —— AGENTS §13「能小就小」）。

### 4.3 S3 — 阈值单一事实源 = agent

- `DISK_FLOOR_MB = 2048`：把 `:1969` 的裸字面量**提成常量**（值不变，行为零漂移）。
- `DISK_WARN_MB = 4096`：新增。
- 上报**同时给绝对值和档位**：`diskFreeMB` / `diskLevel` / `diskWarnMB` / `diskFloorMB` / `diskLevelSince`。  
  理由：看板**永不硬编码 MB**——阈值以后改了（或被 `--diskWarnMB` 覆盖），UI 自动跟随。
- `DISK_HYST_MB = 512`：回差也提成常量（`:1969` 与 ping/status 三处共用同一组口径）。

### 4.4 S4 — 数据通路（零新增探测）

```
agent /v1/ping  (:2221+)  ──┐
                            ├─► nodeProbeResults  (views.ts:158)  ──► FleetProbes.nodes (snapshot-cache.ts:61)
agent /v1/status (:2213)  ──┘        ↑ 扩 NodeProbeResult 带 diskLevel/diskFreeMB
                                          │
                        state-view.ts:128 那段 getFleetProbes try ──► ConsoleStateView.selfDisk
                                          │
                        app.tsx:520 buildAlerts({... selfDisk}) ──► alerts.ts 新条目 ──► AlertDock
```

- **为什么走 `/v1/ping` 而不是新探 `/v1/status`**：`nodeProbeResults` **已经**逐节点 ping  
  （`views.ts:158`，`computeFleetProbes` 里并行起跑，`snapshot-cache.ts:161-165`）。  
  往 ping 响应体**加字段**是纯加法（`:2224` 既有注释：旧消费方忽略未知字段）⇒ **零新增 RTT**，  
  且**顺带覆盖 a95/a96/a97/mac**（它们跑的是同一个 agent），磁盘问题在全机群可视。  
  `statfsSync` 是一次本地 syscall，µs 级，放进 ping 无成本问题。
- **`/v1/status` 一并带**（`:2213` 已有 `diskFreeMB`，补 `diskLevel` 三兄弟）——`pool.ts` 的  
  `fetchSelfStatus` 与 `NodeStats` 因此不用改协议。
- **不新开第三个磁盘来源**：`FleetProbes` 是既有的机群级 SWR（`snapshot-cache.ts:69` 同规），  
  磁盘事实挂它，不另起缓存层（同 `state-view.ts:121-125` 的 R1 结构闸）。

### 4.5 S5 — 条目形状与 ack（G2/G3/G4）

| 字段         | 值                                                                                                                                                                            |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`       | `self-disk-<level>`（`self-disk-warn` / `self-disk-critical`）                                                                                                                 |
| `severity` | `warn` 档 → `'warn'`；`critical` 档 → `'err'`                                                                                                                                   |
| `role`     | `'alert'`（需要打断；`critical` 必为 `alert`）                                                                                                                                        |
| `title`    | `本机磁盘 <n>MB（<档位>）`；**已跌破地板时**才追加「— 已在拒收作业」（F3：判据 = `freeMB < diskFloorMB`，不由档位推）                                                                                                                        |
| `detail`   | `<n>MB 可用（地板 <floor>MB / 预警 <warn>MB）· 跌破地板后 agent 对全部作业回 503，trainer 连续瞬时失败达阈值（缺省 9 次）即停派 self（rollout 与 eval 两条链路）· 处置：清 `tmp/` 下陈旧课程分片`（F9：不写死可配阈值与事故时长） |
| `actions`  | **只有 `ack`**（磁盘要人去清，没有「一键恢复」这种东西——不造第二个真相）                                                                                                                                   |
| `ackKey`   | `alertAckKey('self-disk', 'self', \`${level}@${since}\`)`（`since` = agent 报的 `diskLevelSince`；F2）⇒ **档位 × 进档时刻**：档位内不重复打断、换档必重弹、**回 ok 后再跌破 = 新 episode ⇒ 必重弹**                                                                           |
| `copyText` | 由既有 `withCopy`（`alerts.ts:92`）派生，不特殊化                                                                                                                                        |

- **不设 `resume` 动作**（`AlertAction.kind` 闭集 `'resume'|'ack'` 不放宽）。
- **档位 × episode 的 ack**：磁盘低是**持续态**（每次重算都真），但「跌落」是**事件**（会复发）。
  只用档位做键会被 `TC_ALERT_ACKS`（**只增不减的 localStorage 表**，`app.tsx:151-161`）永久吃掉 ⇒
  同一浏览器一辈子只响一次（F2）；只用时间戳做键则每次重算重弹 ⇒ 疲劳。取两者交集：
  `档位 + 进档时刻`——档位内不打断、换档/复发必重弹。与收官/停机的**事件级**键同精神（`alerts.ts:26-30`）。

### 4.6 S6 — 上屏（G6）

`NodeStats`（`web/app/panels/NodeStats.tsx:295-301`）头部徽标：

```
workers 8 · inflight 3 · done 1234 · disk 1212MB(warn)
```

档位用既有徽标色（`ok` 不显示档位字样，只显示 MB；`warn`/`critical` 才带字样与色）。

---

## 5. 落点（按函数名定位，行号只作加速）

| 批次    | 环节    | 文件                                                            | 改动                                                                                                 |
| ----- | ----- | ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| **A** | 阈值与分级 | `tools/agent/sampler-agent.ts`                                | 提出 `DISK_FLOOR_MB=2048`（值冻结）· 新增 `DISK_WARN_MB=4096` · 新增导出纯函数 `classifyDiskFree()` · `:1969` 改用常量 |
| **A** | 上报    | 同上 `/v1/ping`（`:2221+`）与 `/v1/status`（`:2213`）                | 各加 `diskFreeMB` / `diskLevel` / `diskWarnMB` / `diskFloorMB`                                       |
| **A** | 单测    | `tests/`（根套件，关注点镜像）                                           | `classifyDiskFree` 边界 + 回差 + 不抖                                                                    |
| **D** | 探测契约  | `dashboard/src/server/api/views.ts:148-152`                   | `NodeProbeResult` 加 `disk: DiskFactsView \| null`（**五个 wire 字段打包成一个事实对象**，不散成并列字段）；`parseDiskFacts()` 解析（F1/F4：缺任一 ⇒ null，不编事实） |
| **D** | 节点视图  | `dashboard/src/web/view/console-types.ts:62`（**`NodeView` 本体住这里**，评审 F6）+ `views.ts:188-206` `mergeNodeProbes`                 | 透传磁盘事实（行级档位，顺带覆盖全部节点）                                                                              |
| **D** | 视图契约  | `dashboard/src/web/view/console-types.ts`                     | 新增 `DiskLevel` 与 `DiskFactsView`（`{freeMB, level, warnMB, floorMB, since}`）+ `selfDiskBadge()` 徽标口径 + `ConsoleStateView.selfDisk`；`NodeView` 加行级 `disk`（同型）                           |
| **D** | 装配    | `dashboard/src/server/api/state-view.ts`（`:128-134` 那段 try 内） | 从 `getFleetProbes(cfg)` 取 self 节点的磁盘事实 → `selfDisk`；整段 try（探测坏不得带崩 `/api/state`）                   |
| **D** | ack 事件类 | `dashboard/src/web/view/interaction.ts:33-42`                                                                                             | `AlertAckKind` 闭集加 `'self-disk'`（F7）                                                                                     |
| **D** | 徽标取数  | `dashboard/src/server/api/pool.ts`（`:126/136` 解析）+ `dashboard/src/web/view/pool-types.ts:84-94`                                    | `SelfStatus` 加 `diskLevel/diskWarnMB/diskFloorMB/diskLevelSince`（F4：徽标要显示档位就得收它们）                                        |
| **D** | 告警条目  | `dashboard/src/web/view/alerts.ts`                            | `AlertInput.selfDisk`；新增 `selfDiskAlerts()`；`buildAlerts` 汇总处接上                                    |
| **D** | 接线    | `dashboard/src/web/app/app.tsx:520-534`                       | `selfDisk: stateView?.selfDisk` 传入 `buildAlerts`                                                   |
| **D** | 上屏    | `dashboard/src/web/app/panels/NodeStats.tsx:295-301`          | 徽标加 `disk <n>MB`（+ 档位字样）；**旧 agent 无档位 ⇒ 只显示 MB**（不编档位；F1 的降级行为）                                                                           |
| **D** | 用例    | `dashboard/tests/web-alert-dock.test.ts`（既有 763 行；D 批次用例另外新增徽标渲染与 SSR 夹具）  | 新增用例：`web-alert-dock.test.ts`（档位 → severity/role/title；**换档换键必重弹**；档位内不重弹；**回 ok 后再跌破必重弹**；回差带不说「已拒收」；穷举产出集仍全覆盖）· 新建 `web-app-nodestats.test.ts`（`SelfDiskBadge` 渲染：ok 只显 MB / warn·critical 带档位 / 旧 agent 降级）· `server-api-node-views.test.ts`（ping 缺磁盘字段 ⇒ `disk=null`，不编事实）· `web-ssr-readonly.test.ts` 夹具加 `selfDisk:null`（**F11**：活状态会让 SSR 用例随盘位抖动）                          |

**分层不变**：`alerts.ts` 仍是**纯函数**（无 IO、无 `localStorage`；`acks` 由调用方读出传入）。

---

## 6. 实施（W 提交，每步独立可绿；每步含「改坏必红」自查）

> ★ 批次顺序已拍板（Q1）：**A+D 同一批签入**（无中间态、无双来源窗口）。W-D1/D2 里原写的
> 「本地兜底阈值」**整段删除**——磁盘事实**只来自 agent**（ping + status），dashboard 不推算阈值、
> 不硬编码 MB（理由：评审 F1）。

### W-D0 — 先钉现状（必红）

`web-alert-dock.test.ts` 新增：

- `self 磁盘低于预警档 ⇒ 出 1 条 warn 级条目`（现状**必红**：`AlertInput` 无 `selfDisk`）；
- `self 磁盘低于地板 ⇒ 出 1 条 err 级条目且 role='alert'`（必红）；
- `档位内 ack 后不再出；换档后必重弹；回 ok 后再跌破必重弹`（必红）；
- `回差带 [2048,2560) 里不得说「已在拒收作业」`（必红：那条断言只能由 `freeMB < floorMB` 得出）。

### W-D1 — 契约搬运：ping → NodeProbeResult → FleetProbes → ConsoleStateView

1. `NodeProbeResult` / `NodeView` 加磁盘两字段；`nodeProbeResults` 解析；
2. `console-types.ts` 加 `selfDisk?: DiskFactsView | null`；
3. `state-view.ts` 在既有 `getFleetProbes` try 内取 self 行 → `selfDisk`（**只读缓存值**：不得在这里
   裸调 `/v1/status`——`fetchSelfStatus` 是 4s 超时且模块私有，而 `state-view` 有 R1 结构闸）；
4. **必红自查**：把 `selfDisk` 写死 `null` ⇒ W-D0 三条全红；把整段从 try 里挪出去 ⇒  
   「探测坏不带崩 `/api/state`」的既有用例红。


### W-D2 — 告警条目

1. `alerts.ts`：`selfDiskAlerts(input)` → 0/1 条；`withCopy` 复用；
2. `app.tsx` 接线；
3. **必红自查**：去掉 `role:'alert'` ⇒ critical 用例红；ackKey 改成事件级（带时间戳）⇒  
   「档位内不重复」用例红；ackKey 改成固定串 ⇒ 「升档必重弹」用例红。

### W-D3 — 上屏 + 回归

1. `NodeStats` 徽标；
2. 必须仍绿：`web-alert-dock.test.ts` 既有七类用例（尤其「穷举 `buildAlerts` 产出集」那条——  
   新条目会被它自动纳入）、`server-api-pool.test.ts`、`server-api-pool-swr.test.ts`、  
   `web-ssr-readonly.test.ts`（SSR 首帧）——**后者夹具必须显式 `selfDisk: null`**（F11：它吃的是
   `buildStateView()` 的活状态，不归零就会随本机盘位红/绿抖动，与 2026-09-20 收官抱红同根）；
3. **自查**：上屏用 `SelfDiskBadge` 组件用例钉（ok 只显 MB / warn·critical 带档位 / 旧 agent 降级）；
   「删 `NodeStats` 里那一行 ⇒ 用例红」这条**撤回**——没有任何 dashboard 用例渲染带数据的 `NodeStats`
   （它自己 fetch，`renderToString` 不跑 effect）；靠组件用例 + 代码审查兜底，不假称有接线用例。

### W-A — agent 侧（★ 触发 codeHash 升级波，见 §7）

1. `DISK_FLOOR_MB` / `DISK_WARN_MB` / `DISK_HYST_MB` + `classifyDiskFree()` + **`decideTaskAdmission()`**
   （F5：把 `free < 2048` 的**准入判定**抽成纯函数，`:1969` 改调它——否则 N1 守门用例无接口可写）；
2. ping + status 上报五字段（含 `diskLevelSince`）；
3. 根套件单测 `tests/agent/disk-level.test.ts`（回差 / 边界 / 准入守门 / 不抖）；
4. **必红自查**：把回差改成 0 ⇒ 「4607 不来回抖」用例红；把 `DISK_FLOOR_MB` 改成 4096 ⇒  
   `decideTaskAdmission(3000)` ⇒ `accept` 用例红（**这条就是 §4.1 那条否决的守门用例**）。

### W-Z — 单源收口（**撤销**）

Q1 拍板为 A+D 同期 ⇒ 从来没有「dashboard 本地兜底阈值」这个中间态，本步取消。
残留纪律（写进代码注释）：`selfDisk` 缺省 `null` 时**不出条目、不推算**（旧 agent 连接新控制台
只少一条告警，不得凭空造档位）。

---

## 7. ★ 风险：改 `sampler-agent.ts` 会翻 codeHash，触发全集群升级波

`tools/agent/sampler-agent.ts` **在 codeHash SSOT 清单里**（`tools/agent/codehash-files.txt` 纳入了它，  
且该文件「纳入原则」明写 `tools/agent/*.ts` 是为「agent 本体 + `/v1/restart` 护栏」而入集）。  
而 `dashboard/**` 与 `nn-training/**` 在**排除原则**里被点名 ⇒ dashboard 改动**零集群影响**。

| 批次               | 翻 codeHash？ | 影响                                                                          |
| ---------------- | ----------- | --------------------------------------------------------------------------- |
| **D**（dashboard） | **否**       | 无。随时可合、可回滚                                                                  |
| **A**（agent）     | **是**       | 所有节点 `codeHash mismatch` → rollout 门与 eval 门同时拒 → **训练停摆**，直到每台 worker 重新同步 |

⇒ 升级顺序（既有运维纪律，MEMORY 已记）：**先停 worker → 升 hub → 再升 worker**。  
⇒ 这就是为什么计划拆成 D/A：**D 先合可以先有告警**，A 挑一个不心疼的窗口做。

---

## 8. DoD 与门禁

- [ ] **G1** ping 与 status 都带五字段（含 `diskLevelSince`）；旧消费方（trainer 的 `node_gate_reason` 只读固定键）零影响。
- [ ] **G2** `warn`→`warn` / `critical`→`err`；全局可见（不依赖切课、不依赖抽屉打开）。
- [ ] **G3** detail 含后果句 + 处置句（逐字含「503」「停派」两个信息点；事故时长与可配阈值不进 UI，F9）。
- [ ] **G4** ack 事件身份 = 档位 × 进档时刻；档位内不重弹；换档必重弹；**回 ok 后再跌破必重弹**（F2）。
- [ ] **G5** 回差用例：4095→warn、4607→仍 warn、4608→ok、2047→critical、2559→仍 critical、2560→warn。
- [ ] **N1 守门**：`<4096 且 >=2048` 时 agent **仍正常接受作业**（用例走 `decideTaskAdmission(3000) ⇒ accept`，F5）。
- [ ] **F3 守门**：`critical` 且 `freeMB >= floorMB` 时，条目**不得**出现「已在拒收作业」（回差带 [2048,2560)）。
- [ ] `alerts.ts` 仍是**纯函数**；`AlertAction.kind` 闭集未放宽；`ALERT_DOCK_DEFAULT_VISIBLE` 未改。
- [ ] 门禁：
  ```bash
  cd dashboard && bun run typecheck && bun run test && bun run build:ui   # D 批次
  bun run check && bun run build                                          # 根
  bun run freeze:check                                                    # God-AI 签名未被误伤
  ```
- [ ] **文档**：新 DECISIONS 条目（阈值分层 + 两条被否决备选：抬地板 / 纯档位级 ack 键 / 纯事件级 ack 键）+  
  `docs/nn/console.md` **§36**（现最大 §35，2026-10-08）+ `docs/nn.progress.md` 一行 + memory 一行。
- [ ] A 批次单独走一次集群升级（停 worker → 升 hub → 升 worker），升级后确认各节点 `codeHash` 一致。

---

## 9. 待拍板 / 预注册门槛

### 已拍板（2026-10-09）

| #      | 问题          | 决议                                                                                                   |
| ------ | ----------- | ---------------------------------------------------------------------------------------------------- |
| **Q1** | D 与 A 的批次顺序 | **③ A+D 同期签入**——① D 先行**不可实现**（F1：A 落地前 ping 上一个磁盘数都没有，`selfDisk` 恒 `null`；「本地兜底阈值」无落点），② A 先行只多等一个升级波（本地自用也要在服务器重启 agent 后才生效） |
| **Q2** | 预警档取多少      | **① 4096**（4 GiB；距地板 ≈10h 窗口，高于健康水位）——② 5120 贴健康水位易常亮；③ `--diskWarnMB` 旋钮降为 P2（未触发） |

### 预注册门槛（触发才做）

| #      | 候选                                                                     | 门槛                              | 状态                                                 |
| ------ | ---------------------------------------------------------------------- | ------------------------------- | -------------------------------------------------- |
| **P1** | trainer 侧把 `low disk` 从 `TRANSIENT_HTTP_STATUS` 分流（不重派同一节点、不耗 attempt） | 再次发生「盘满 ⇒ 整轮重排」且有日志证据           | **未触发**（另案，N5）                                     |
| **P2** | `--diskWarnMB` CLI 旋钮                                                  | Q2 选 ③，或运维实测需要换阈值               | 未触发                                                |
| **P3** | `tmp/` 分片保留策略（治本：排水率 −205 MB/h）                                        | 本案上线后仍周期性触达 warn                | 未触发（**根因案**，与本案正交）                                 |
| **P4** | 组件卡 selfNode 健康点把 `critical` 视为异常                                      | 出现「磁盘炸了但 ping 200 ⇒ 组件卡绿点」的误读报告 | 未触发（现有 `componentHealth` 只有 bool，`views.ts:31-52`） |

---

## 10. 一句话给接手 agent

> **数据已经躺在浏览器里了**：`/v1/status` 的 `diskFreeMB` 被 `pool.ts:112` 取回、塞进 `SelfStatus`  
> （`pool-types.ts:86`）、`NodeStats.tsx:291` 读它却**只渲染 3 个别的字段** ⇒ 磁盘是**死字段**，  
> 告警坞（`alerts.ts`）7 类里也没有磁盘。本案只做三件事：  
> ① **agent 加一层预警档**（`DISK_WARN_MB=4096`，回差 512），把 `diskFreeMB/diskLevel/diskWarnMB/diskFloorMB`  
> 挂到**既有的** `/v1/ping`（`nodeProbeResults`/`views.ts:158` 已经每拍 ping，**零新增探测**，顺带覆盖全机群）  
> 与 `/v1/status`；  
> ② **dashboard 搬运**：`NodeProbeResult` → `FleetProbes` → `state-view.ts` 的 `selfDisk` → `alerts.ts`  
> 第 8 类条目（`warn`→`warn` / `critical`→`err`，档位级 ack），并在 `NodeStats` 把磁盘数上屏；  
> ③ **拒收地板 2048 一个字不动**——轨迹证明训练中盘会长期停在 1.2–1.8 GB，  
> **抬线 = 节点永久拒收**，比故障更糟（这是本案最容易搞错的一步，W-A4 有守门用例）。  
> **不要**：动 `TRANSIENT_HTTP_STATUS`；让 agent 自动删 `tmp/`；加 `resume` 动作；  
> 提高 `ALERT_DOCK_DEFAULT_VISIBLE`；用事件级 ack 键（会因为每次重算重弹而疲劳）。  
> **注意codeHash**：`sampler-agent.ts` 在 SSOT 里 ⇒ A 批次翻全集群 codeHash，必须走  
> 「停 worker → 升 hub → 升 worker」；`dashboard/**` 被 SSOT 排除 ⇒ D 批次零集群影响。  
> （Q1 已拍板 A+D 同期，升级波的操作步骤由操作员在各节点上走一次，代码本身不需先后。）

---

## 11. 评审处置（2026-10-09，`plan/self-node-disk-alert.review-bf.md`）

| # | 评审结论 | 处置 |
|---|---|---|
| **F1** | 「D 先行」没有数据来源（ping 上的磁盘字段全属 A 批次）⇒ 告警永不出现；「本地兜底阈值」无落点 | **采纳**：Q1 改拍 ③ A+D 同期；删除 D 先行的中间态与 W-Z（§6） |
| **F2** | 纯档位级 ack 键无时间成分 + `TC_ALERT_ACKS` 只增不减 ⇒ warn 档一辈子只响一次 | **采纳**：agent 增报 `diskLevelSince`，ack 事件身份 = 档位 × 进档时刻（§4.2/§4.5/G4）；用例钉「回 ok 后再跌破必重弹」 |
| **F3** | 回差带 [2048,2560) 里 `critical` 会断言「已在拒收作业」而 agent 正常收活 | **采纳**：后果句由 `freeMB < diskFloorMB` 判（数字由 agent 报，不硬编码）；新增 DoD 守门行 |
| **F4** | G6 上屏无测试落点；`SelfStatus`/`fetchSelfStatus` 需同步收新字段（否则只能硬编码 MB） | **采纳**：落点表补 `pool.ts` + `pool-types.ts` 行与 `web-app-nodestats.test.ts`；「删那一行必红」**撤回**（无带数据的 `NodeStats` 渲染用例），改用 `SelfDiskBadge` 组件用例 + 审查（§6-W-D3） |
| **F5** | N1 守门用例无可写接口（2048 判定内联 + 真实 statfs 不可注入） | **采纳**：A 批次抽 `decideTaskAdmission()` 纯函数，DoD 的守门用例改走它（§6-W-A） |
| **F6** | `NodeView` 本体住 `console-types.ts:62`（不在 `views.ts`）；只做 self 告警与「顺带覆盖全机群」的口径需写明 | **采纳**：落点表改正；远端节点低盘不进告警的理由（后果只表现为 503，无本机停派那种可见后果）写进 §3.2-N6，仍留 P4 预备案 |
| **F7** | 「第 8 类」称呼已有主（course-startup）；ack 键串两处不一致；`AlertAckKind` 闭集需改而落点表漏了它 | **采纳**：改叫「新一类（第 8 个条目函数）」；键串统一为 `self-disk\|self\|<level>@<since>`；落点表补 `interaction.ts` |
| **F8** | 文档号没钉死 | **采纳**：钉 `docs/nn/console.md` **§36**（现最大 §35；同期 plan 撞号以**先合入者**为准） |
| **F9** | UI 文案里的可配阈值（9 次）与事故时长（1h41m）会漂 | **采纳**：detail 只报事实与出口（MB/两条阈值/后果/处置）；事故时长留在 plan 与 DECISIONS |
| **F10** | 对账小误差（763 行、`computeFleetProbes` 8 字段、行号） | **采纳**：已就地改正（§5/§11）；结论不变 |
| **F11** | 实施期新发现：`web-ssr-readonly.test.ts` 的夹具吃 `buildStateView()` 活状态，控制台本机一旦跌破预警档就会把这条 SSR 用例抱红/绿 | **采纳**：该夹具显式 `selfDisk: null`（与 2026-09-20 收官抱红同根的处理），写进 §6-W-D3 |
