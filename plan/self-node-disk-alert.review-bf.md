# self-node-disk-alert.review-bf.md — 第一轮评审

> 评审人：Buffy · 2026-10-09 · 基准 HEAD `2f5afc0e`（goal-nn）· 方法：把 plan 的每条事实行 / 落点 /
> 判据 / 用例拿到工作树真代码上逐条对账（只读 grep + 静态追踪；**未跑门禁、未改代码**）。
> **范围**：整份 plan（§0–§10），重点 §1 现状表、§4 语义规格、§5 落点、§6 W 序列、§8 DoD、§9 待拍板。
> **结论**：方向、口径分层（预警档 ≠ 拒收地板）与 §4.1 那条否决**成立且是本案最有价值的部分**；
> 现状对账**几乎逐条为真**（见 §4）。但 **Q1①「D 先行」在计划里不可实现（F1，P0）**、
> **档位级 ack 键与永不过期的 acks 组合会让 warn 档只响一次（F2，P0）**——
> 两者都会「全绿交付但要求的行为不存在」。另有三处判据/落点缺口（F3–F5）。
> 建议 F1–F5 并入 §4/§5/§8 再开工；F6–F10 随实现就地定死。

| # | 级别 | 一句话 |
|---|---|---|
| F1 | **P0** | Q1①「D 先行」的数据通路**在 A 落地前一个 MB 数都没有**：ping 路径全是新字段，D 先行的 `selfDisk` 恒为 `null`，告警永不出现；计划提到的「本地兜底阈值」没有对应落点 |
| F2 | **P0** | 档位级 ack 键（`self-disk\|self\|<level>`）**没有时间成分**，而 `TC_ALERT_ACKS` 是**只增不减**的 localStorage 表 ⇒ 同一浏览器里 warn 档**一辈子只响一次**，正是本案要消灭的「静默」 |
| F3 | P1 | `critical` 档在回差带 **[2048,2560) MB 里断言「已在拒收作业」，而 agent 此刻正常收活**（门是 `< 2048`）——告警会说假话；重启后同一读数还会从 critical 掉成 warn |
| F4 | P1 | G6 上屏**没有测试落点**，且 W-D3 的「删 NodeStats 那行 ⇒ 上屏用例红」引用的用例**不存在**（没有任何 dashboard 测试渲染 NodeStats） |
| F5 | P1 | N1 守门用例（「自由 3000MB ⇒ 作业被接受」）**没有可写的接口**：2048 判定内联在 `/v1/task`，`diskFreeMB()` 打真实 statfs，测试无法注入 |
| F6 | P2 | `NodeView` 不在 `views.ts` 而在 `dashboard/src/web/view/console-types.ts:62`；且只做 self 告警与「顺带覆盖全机群」的落点自相矛盾（需写明理由或登记预备案） |
| F7 | P2 | 「第 8 类条目」这个称呼**已经有主**（course-startup 自称第 8 类）；ack 键串 §4.5 内部自相矛盾；`AlertAckKind` 是闭集 union，落点表漏 `interaction.ts` |
| F8 | P2 | 文档落点只写「当前最大号 +1」：`docs/nn/console.md` **现最大 §35**（2026-10-08）⇒ 应钉死 **§36**（并注明与同期 plan 撞号以先合入者为准） |
| F9 | P2 | detail 里写死「实测 1h41m」「连 9 次」——后者是**可配置缺省**（`nodeSoftFailStreak` = `nodeFailStreak`(3) × 3，`dispatch.py:178/493`）；UI 文案只报事实与出口更稳 |
| F10 | P3 | 对账小误差：测试文件 763 行（非 764）· `computeFleetProbes` 出 8 字段（漏 `ppoWorkerLive`）· §10「NodeStats 读它」措辞不准 |

---

## 1. 必修（会「全绿交付但要求的行为不存在」）

### F1（P0）D 先行没有数据来源：ping 上的磁盘字段全部是 A 批次的产物

**plan 说**（§4.4 / §5-D / §6-W-D1）：`agent /v1/ping` →(`nodeProbeResults`)→ `FleetProbes.nodes` →
`state-view` 的 `selfDisk` → `alerts.ts`；§9-Q1① 主张「D 先行（dashboard 侧带本地兜底阈值，立刻有告警）」。

**事实**：

1. 现树里 `/v1/ping` 的响应体**没有任何磁盘字段**（`sampler-agent.ts:2221+`：ok/pid/bootId/instanceGuard/
   codeHash/bunVersion/agentVersion/cpus/rolloutEngine/nodeVersion/evalSupport/stageJsonSupport/
   decisionEventsSupport/decisionKSupport/bcSupport…）——`diskFreeMB()` 只在 `/v1/status` 出现一次（`:2213`）。
   所以 D 批次的 `nodeProbeResults`（`views.ts:158-186`，body 断言在 `:174`）解析到的是**不存在**的字段。
2. ⇒ D 先行的 `selfDisk` **恒为 `null`**，`alerts.ts` 一条都不出；W-D0 的三条「必红」用例在 W-D1 之后
   只会因为**用例自己喂的 fixture** 而变绿 —— 产品侧仍然零告警。这正是「测试绿而行为不存在」。
3. 计划里唯一指向出路的是 §6 前言那句「W-D1/D2 的**本地兜底阈值**」——但 **W-D1/W-D2 正文里没有任何**
   「本地阈值 / 兜底来源」的步骤或落点行（§5 的 D 行也只写 state-view 走 `getFleetProbes`）。
4. 现树里唯一在手的磁盘数是 `/api/pool` 的 `selfStatus.diskFreeMB`（`pool.ts:112-146`，`:136` 解析；
   类型 `pool-types.ts:86`），由**单条目全局 SWR** `poolProbeCache`（`pool.ts:57-72`）承载，
   生产者在 `computePoolProbes`（`pool.ts:283-312` 的 `fetchSelfStatus`）。**plan §2.2 自己也已经写明
   「它被 NodeStats 独占消费 ⇒ 告警坞拿不到它」** —— 与 Q1① 的「立刻有告警」直接冲突。

**建议**（三选一，写进 §4.4 + §5 + W-D1）：

- **(a) 走既有 PoolProbes（推荐）**：`PoolProbes` 加 `selfDisk`（在 `computePoolProbes` 里由
  `selfStatus.diskFreeMB` + 本地常量 2048/4096 判档），`state-view` **只读缓存值**。
  ⚠ 两个硬约束：`fetchSelfStatus` 是**模块私有**（`pool.ts:112` 无 `export`）且有 **4000ms 超时**；
  而 `state-view.ts:118-137` 那段有明文的 R1 结构闸「这里只读缓存值，不裸调聚合」——**不得**在
  `/api/state` 请求路径上 inline 调 `/v1/status`。
- **(b) 走客户端**：把 `pool.selfStatus` 从 `NodeStats`（`app.tsx:650` 抽屉）提到 app 级，喂
  `buildAlerts`；代价是多一条客户端数据流（与 §4.4「不新增第二个缓存层」的自我约束相抵，需写明理由）。
- **(c) 撤掉 Q1①**：A+D 一期落地。A 本来就是一次 codeHash 升级波，「先合 D 立刻有告警」的收益只在
  D 真能看到数时成立；若走 (a)/(b)，也要把 W-Z 从「双**阈值**窗口」改成「双**来源**窗口」并写明
  收口后 level 以谁为准（ping 还是 status）。

### F2（P0）档位级 ack 键 + 永不过期的 acks ⇒ warn 档只响一次

**plan 说**（§4.5 / G4）：ack 键 = 档位级 ⇒「档位内不重复打断、升档必重弹」；理由是与收官/停机的
**事件级**键是不同语义。

**事实**：

1. ack 表是 `localStorage` 里的 `TC_ALERT_ACKS`，**加载即用、只 append、从不清理**
   （`app.tsx:151-161`；全仓没有第二处读写它）——没有 TTL、没有「条件消失即删键」。
2. 现有**每一个**键都带事件身份（时刻/reason/jobId），见 `AlertAckKind`（`interaction.ts:33-42`）与各
   调用点（`alerts.ts` 的 `alertAckKey('loop-complete', course, done.at)` / `('ppo-stall', jobId, it)` /
   `('course-startup', course, reason)` …）——它们**天然过期**：下一次事件是新键。
3. `self-disk|self|warn` 没有时间成分 ⇒ 操作员点过一次「知道了」之后，**这个浏览器此后再也不会看到
   warn 档**（含「清盘 → 两天后又跌破」这种最常见复发）。丢掉的恰好是本案的目标：**早期预警**。
   critical 档因为换键还能响一次，于是实际行为退化成「只有已经拒收时才会提醒」——比现状好，但
   §4.1 论证出来的 ≈10h 处置窗口被 ack 一次性吃掉了。

**建议**（写进 §4.5，并加用例）：二选一或并用：

- **(a) 事件身份 = 进档时刻**：agent 增报 `diskLevelSince`（进当前档的时刻），ack 键带它
  （`alertAckKey('self-disk','self',`${level}@${since}`)`）。
- **(b) 恢复即清键**：dashboard 在 `selfDisk.level === 'ok'` 时把 `self-disk|*` 从
  `alertAcks` 里删掉（新 episode = 新键）。这是纯函数 + 一个 `setAlertAcks` 效果，可在
  `web-alert-dock.test.ts` 里钉死。
- 必补用例：`ack(warn) → 档位回 ok → 再跌破 warn ⇒ 必须重弹`（现行方案下这条**必红**）。

### F3（P1）critical 的判据与「已在拒收作业」的事实，在回差带里打架

**plan 说**：§4.2 回差 `critical: freeMB < floor; 回出 >= floor + hyst (2560)`；§4.5 title
= `本机磁盘 <n>MB — 已在拒收作业`、detail 逐字含「503」；§8 DoD 钉「2559→仍 critical」。

**事实**：拒收门是 `if (free !== null && free < 2048) → 503`（`sampler-agent.ts:1968-1969`）。
⇒ 在 **[2048, 2560) MB** 区间：`classifyDiskFree` 因 `prevLevel==='critical'` 仍报 `critical`
（DoD 用例「2559→仍 critical」正是这个区间），而 agent **正在正常接活**。告警却断言「已在拒收作业」。
更糟的是这个错法**不可复现**：agent 重启后 `prevLevel` 归零，同一个 2500 MB 立刻变 `warn`
——同一条读数在两次现场里说法相反。

**建议**：

- 回差**只当防抖用**，**后果句由数字判**：告警已同时持有 `freeMB` 与 `diskFloorMB`，UI 直接按
  `freeMB < diskFloorMB` 决定是否渲染「已跌破地板（对全部作业回 503）」。这样 critical 档在
  [2048,2560) 只会说「逼近地板」，永不撒谎。
- 或者干脆只对 `warn` 做回差，`critical` 严格用 `< floor`（回差留 0）——此时 §4.2 的
  「入 <2048 / 出 ≥2560」删掉，DoD 的「2559→仍 critical」相应用例改写。
- 另需在 §4.2 写明 `prevLevel` **住在哪里**（agent 进程内模块变量？）与「重启后档位重算」的语义。

### F4（P1）G6 上屏零测试落点，「必红自查」引用了一个不存在的用例

**plan 说**：§6-W-D3「必红自查：删 `NodeStats` 那行 ⇒ 上屏用例红」；§5 的测试落点只有
`dashboard/tests/web-alert-dock.test.ts`（「本关注点唯一测试线」）。

**事实**：`NodeStats` 在 `dashboard/tests/` 里**只出现在注释里**（`server-api-pool-swr.test.ts:76`、
`server-pool-history.test.ts:706/989`），没有任何用例渲染它。面板是可测的——同目录有
`web-app-nodepills.test.ts` 这类渲染型用例，照抄即可。
⇒ 按现计划实施，`NodeStats.tsx:291-301` 的徽标改动**没有任何用例会红**，W-D3 的自查是空转。

**建议**：§5 增加落点 `dashboard/tests/web-app-nodestats.test.ts`（或并入既有渲染型用例），
钉「`ok` 只显示 MB、`warn`/`critical` 带档位字样」。另：§5「`pool.ts` 与 `NodeStats` 因此不用改协议」
对**线上协议**成立，但徽标要显示档位就必须让 `SelfStatus`（`pool-types.ts:84-94`）与
`fetchSelfStatus` 的解析（`pool.ts:126/136`）多收 `diskLevel`（+ `diskWarnMB`/`diskFloorMB`）——
这一行要补进落点表（否则只能硬编码 MB 阈值，违反 §4.3「看板永不硬编码 MB」）。

### F5（P1）N1 守门用例写不出来：2048 的判定不可注入

**plan 说**：§8 DoD「N1 守门：`<4096 且 >=2048` 时 agent 仍正常接受作业（用例：自由 3000MB ⇒ 作业被接受）」。

**事实**：该判定内联在 `/v1/task` 处理器里（`sampler-agent.ts:1968`），输入来自 `diskFreeMB()` 的
**真实 `statfsSync(WORK_DIR)`**（`:756-766`）。`classifyDiskFree` 是纯函数，但**它不决定准入**——
测它绿不代表门没被抬。⇒ 这条守门用例按现计划只能在注释里存在（AGENTS §7「没写失败测试就不算修好」）。

**建议**：A 批次顺手把准入判定抽成纯函数（例：`decideTaskAdmission(freeMB): 'accept' | 'low-disk'`，
内部用 `DISK_FLOOR_MB`），`:1968-1969` 改调它，根套件单测吃 `3000 ⇒ accept` / `2047 ⇒ low-disk`。
这正是「把拒收地板抬到 4096」这个最容易搞错的改动唯一的守门（计划 §6-W-A4 也承认它最重要）。

---

## 2. 需定死（不致命，但会被下一个接手的人踩）

### F6（P2）落点归属与范围口径

- `NodeView` 类型**不在** `views.ts`：它在 `dashboard/src/web/view/console-types.ts:62`，
  而 `views.ts:5` 是 `import type { ComponentView, NodeView } from '../../web/view'`。
  §5-D 的「节点视图 | 同上 `NodeView`」要改成 console-types（这一行现在只写了 `selfDisk`）；
  同时 `nodeProbeResults` 的 body 断言（`views.ts:174`）与 `nodeStructure` 的初值（`views.ts:131-144`）
  也要同步加字段（新字段缺省 `null`）。
- `NodeView` 是**客户端可见**类型 ⇒ 「顺带覆盖 a95/a96/a97/mac」意味着节点行也带档位，但 §3.2-N6 只做
  self 告警。这是**有意的**（远端节点低盘只会表现为 503，没有本机停派那种可见后果），但计划必须写明，
  否则读者会以为「全机群可视」= 多机告警。建议在同一行登记一条预备案（与 P4 同族）。

### F7（P2）命名与键串

- 「**第 8 类**」已被占用：`alerts.ts` 的 course-startup 段自称「第 8 类」（`plan/course-startup-recover.plan.md:274`
  同样这么叫），而 `web-alert-dock.test.ts:59/342` 把 `buildAlerts` 的 7 个函数叫「七类」。
   新条目请写成「第 8 个条目函数」或另起名字，并在落点表点名。
- §4.5 一行内自相矛盾：正文写 ack 键 = `self-disk|<level>`（两段），同行给的是
  `alertAckKey('self-disk','self',level)` ⇒ 实际串是 `self-disk|self|<level>`（`interaction.ts:42` 的三段格式）。写死一种。
- `AlertAckKind` 是**闭集 union**（`interaction.ts:33-42`），加 `'self-disk'` 必须改它；
  §5 落点表**没有 `dashboard/src/web/view/interaction.ts`** 这一行。

### F8（P2）文档号钉死

§8 只写「`docs/nn/console.md` 新节（当前最大号 +1）」。现树最大是 **§35**（2026-10-08，
`worker-type-dispatch-model` 那节）⇒ 本案写 **§36**；并注明同时落地的 plan 之间撞号以**先合入者**为准
（`rollout-concurrency-adaptive` 那份 plan 已经因为撞号改过一次号）。落点选 console.md 是对的
（dashboard 侧决策档案确在此，不是 training-stack）。

### F9（P2）写进 UI 的文案里别放会漂的数字

- 「连 9 次瞬时失败」是**缺省值**：`soft_streak_max = nodeSoftFailStreak ?? fail_streak_max * 3`，
  而 `fail_streak_max = nodeFailStreak ?? 3`（`dispatch.py:178` / `:493`）⇒ 9 可被 policy 改。
  文案建议写「连续瞬时失败达阈值即停派本节点（缺省 9 次）」。
- 「实测 1h41m」长期留在每条告警的 detail 里会变成噪音（半年后没人知道那是什么）。
  §4.1 自己立的规矩是「只报事实、不做会漂的推算」——同一个道理：**UI 报当前 MB + 两条阈值 + 后果 + 出口**，
  事故时长留在 plan / DECISIONS / `docs/nn/console.md`。§8 的「逐字含 503/停派/1h41m」随之改成
  「逐字含 503 与『停派本节点』」，事故时长不进 UI 字符串。

---

## 3. 对账小误差（不影响结论）

- `web-alert-dock.test.ts` 现 **763** 行（计划写 764）。
- `computeFleetProbes` 现有 **8** 个字段：`nodes/slowById/contribById/localContrib/pushProbes/
  componentHealth/contributionBrief/**ppoWorkerLive**`（计划列了 7 个）；行号 `:121` 起（计划写 112-174）；
  `FleetProbes.nodes` 在 `:63-64`（计划写 :61）。
- `state-view.ts` 那段 try 在 **~131** 行（计划写 128）——范围对，无影响。
- §10 那句「`NodeStats.tsx:291` 读**它**（diskFreeMB）却只渲染 3 个别的字段」措辞不准：
  NodeStats 读的是 `pool.selfStatus`（`:291`），**从没读过 `diskFreeMB`**（全 `dashboard/src/web` 零命中，
  这点计划是对的）。建议改成「读到了 `selfStatus`、却把其中的 `diskFreeMB` 丢了」。

---

## 4. 已核对**成立**的部分（不用改）

- **拒收地板内联在 `:1969` 且值 = 2048** ✓（`sampler-agent.ts:1968-1969`）；`diskFreeMB()` 量的是
  `WORK_DIR`（`<repo>/tmp/dist-agent`，`:105`）✓；`/v1/status` 带 `diskFreeMB` ✓（`:2213`）。
- **`/v1/ping` 不查盘** ⇒ 事故期间 `node self` 一直 **online**、组件卡（`httpOk` 探 `/v1/ping`，
  `views.ts:44-51`）**照绿** ⇒ P4 的成立性 ✓（「盘炸了但绿点」确实会发生）。
- **告警坞 7 个条目函数、无一条与磁盘有关** ✓（`alerts.ts:153-163`）；`ALERT_DOCK_DEFAULT_VISIBLE = 2`（`:54`）✓；
  `withCopy`（`:92`）✓；`ALERT_SEVERITY_RANK`（`:46-51`）✓；`role` 取值 `'alert'|'status'`、`AlertAction.kind`
  闭集 `'resume'|'ack'` ✓；`alerts.ts` 是纯函数（无 IO / 无 localStorage）✓。
- **`NodeStats` 只渲染 workers/inflight/done** ✓（`NodeStats.tsx:291-301`）；
  **`diskFree` 在 `dashboard/src/web` 零 .tsx 命中** ✓（实测 grep）。
- **codeHash 风险判断成立** ✓：`tools/agent/codehash-files.txt` 确实纳入 `tools/agent/sampler-agent.ts`
  （「agent 本体 + `/v1/restart` 护栏」原句在集内），且「排除原则」逐字点名
  `dashboard/** · nn-training/** · docs/** · plan/** · tests/**` ⇒ D 批次零集群影响、A 批次翻全集群 ✓。
  「新文件要同步 codehash-files.txt，能不加就不加」的取舍 ✓（放 `sampler-agent.ts` 内即零清单改动）。
- **§4.1 的算术与结论** ✓：(4532−1746)/13.65h ≈ **204 MB/h**；(4096−2048)/205 ≈ **10h**；(5120−2048)/205 ≈ **15h**；
  5120 会贴着健康水位 4532 ⇒ 常亮，故取 4096 ✓。回差分档用例（4095/4607/4608/2047/2559/2560）自洽 ✓。
- **「trainer 连 9 次后停派 self」** ✓（软失败 streak 缺省 = 3×3，`dispatch.py:178/493`；日志行 `:985`
  「连续 N 次瞬时失败」）。
- **`low disk` 被当背压** ✓：`TRANSIENT_HTTP_STATUS` 含 503（`distribution.py:317`），
  `is_transient_error` 明说「不得计入节点失败 streak」⇒ N5/P1 的定性正确。
- **§6-W-D3 点名的四个「必须仍绿」测试都存在** ✓（`server-api-pool.test.ts` / `server-api-pool-swr.test.ts` /
  `web-ssr-readonly.test.ts` / `web-alert-dock.test.ts`）；`bun run build:ui` 存在（= `bun src/server/build.ts`）✓。
- **「穷举 buildAlerts 产出集」那条用例确实是穷举而非手写清单** ✓（`web-alert-dock.test.ts:446-456`，
  断言 `kind=ack` 与 `ackKey` 非空、`length ≥ 9` 防空跑）。⚠ 但注意它**不会**因为少了一类而红——
  §6-W-D3 说「新增第 8 类必须被它自动纳入」是对的，别把它读成「会替我守住类目数」。

---

## 5. 可选增强（建议登记为预备案，不进本案 DoD）

- **后果面已有现成读数**：`/v1/status` 已经在报 `rejectedCount`（`sampler-agent.ts:2214`，
  `pool.ts` 也在读它）。它是**已发生的事实**，不像阈值是预测 ⇒ 若 warn 档上线后仍出现「停派了但没人注意」，
  第二条告警直接吃「rejectedCount > 0 / 本轮被拒 N 次」，零新阈值、零新探测。建议写进 §9 预注册门槛
  （触发条件 = warn 档上线后仍发生一次停派没人看见）。
- 顺带：`P3`（临时分片保留策略，治本 −205 MB/h）是真正的根因案，本案只是让故障可见——
  这一点 §9 已经写明，保持即可。

---

## 6. 实施期补记（2026-10-09，同日落地）

- **处置**：F1–F10 全部采纳并写进 `plan/self-node-disk-alert.plan.md` **§11 评审处置**；Q1 改拍 **A+D 同期**
  （D 先行的「本地兜底阈值」整段撤销），Q2 取 **4096**。
- **F11（实施期新发现，已采纳）**：`dashboard/tests/web-ssr-readonly.test.ts` 的夹具吃
  `api.buildStateView()` 的**活状态**——它把 `cloudHalts/loopCompletes/ppoQueueStall/courseEdit` 显式清零，
  却不可能预见到新加的 `selfDisk`。若不在夹具里显式 `selfDisk: null`，控制台本机一旦跌破预警档，
  这条**与磁盘无关**的只读用例就会变红（2026-09-20「一次正常收官抱红」同根）。已加。
- **F4 的诚实边界（实施时确认）**：本仓 web 用例全是 SSR、无 DOM 夹具（`preact-render-to-string` 不跑
  effect），而 `NodeStats` 自己 `fetch` ⇒ **拿不到带数据的整块渲染**。故：
  · 新增 `dashboard/tests/web-app-nodestats.test.ts` 渲染导出的 `SelfDiskBadge` 组件 + 钉纯函数口径；
  · 「`NodeStats` 里挂了这一行」由代码审查兜底，**不假称有接线用例**（原 W-D3 的「删那一行必红」已撤回）。
- **必红复核实测**（改坏 → 用例确实红 → 还原）：
  · `DISK_FLOOR_MB` 2048→4096 ⇒ 根套件 8 例红（含 N1 守门 `decideTaskAdmission(3000) ⇒ accept`）；
  · `belowFloor = d.level === 'critical'` ⇒ 回差带用例红（F3）；
  · ack 键退化为纯档位 ⇒ 「回 ok 后再跌破必重弹」用例红（F2）。
