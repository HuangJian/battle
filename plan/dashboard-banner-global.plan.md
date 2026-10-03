# Plan: dashboard-banner-global — 收官横幅全局可见 + 全部横幅可关闭可复制（评审修订版）

> **交付物**：本 plan。实现已按本文件落地（W0–W4，2026-10-03）。
> **状态**：评审修订（2026-10-03）。原稿两条用户指令见 §0；评审 11 条处置见 §11 处置表。
> **触发（用户 2026-10-02）**：①「一个课程训练收官的横幅信息，现在只在切换到该课程时才能看到，
> 应改为全局可见」；②「dashboard 所有横幅信息，都应该可关闭可复制」。
> **阅读顺序**：§1 现状矩阵（**7 类 / 8 条目**，先看清各自口径）→ §2 两条根因 → §3 目标/非目标 →
> **§4 语义规格（先定死）** → §5 落点 → §6 实施与用例 → §7 预期效果 → §8 DoD/门禁 →
> §9 预注册门槛（开放问题已闭环）→ §10 一句话 → §11 评审处置表。
> 行号只作定位加速，**判据看函数名**。
> **本 plan 是 dashboard 侧**（TS/Preact），与 `plan/eval-final-round-and-dropped.plan.md`（trainer 侧，Python）
> **不同子系统、不同门禁**；两者有一处交叉收益，见 §4.4。

---

## 0. 一句话目标

**让「训练已收官」这类终态信息不必切课就能看见，并让告警坞里的每一条都具备「可关闭（按事件身份）+ 可复制（一键带走全文）」。**

## 1. 现状（告警坞已把横幅收成一处，所以改动面比看起来小）

`view/alerts.ts`（纯函数，建条目 + 排序 + 折叠）→ `components/AlertDock.tsx`（渲染 + 折叠开关 + 动作绑定）
→ `app/app.tsx:514-535`（把 `stateView` 的字段喂进去、把 `onAct`/`onAck` 绑到通道）。

**七类横幅 / 八个条目**（读自 `alerts.ts` 全文 + `app.tsx` 接线；`web-alert-dock.test.ts:56` 的
「七类告警」是同一口径——原稿矩阵漏了 T8，此处补正）：

| # | 类 | 条目 | 严重度 | 可关闭 | 可复制 | 可见范围 | 依据 |
|---|---|---|---|---|---|---|---|
| 1 | 云端停机中 | `halt-<课>` | `err` | ✅ `ack`（事件身份键） | ❌ | **仅当前课程** | `cloudHaltAlerts` + `visibleCloudHalts` |
| 2 | 停机已恢复（留痕） | `rec-<课>` | `history` | ✅ `ack` | ❌ | 仅当前课程 | `cloudHaltAlerts` |
| 3 | **训练已完成（收官）** | `loop-complete:<课>` | `history` | **❌ `actions: []`** | ❌ | **仅当前课程** ★ | `loopCompleteAlerts` |
| 4 | PPO 任务排队超时 | `ppo-queue-stall` | `err` | **❌** | ❌ | 全局（无课维度） | `ppoStallAlerts` |
| 5 | **离线课程静默停摆（T8）** | `offline-stall-<课>` | `err`/`warn` | **❌**（只有 `resume`） | ❌ | 全局（按课成列） | `offlineStallAlerts` |
| 6 | 课程文件语料身份改动被拒 | `course-edit-rejected` | `err` | **❌** | ❌ | 仅当前课程（读面就是单课） | `courseEditAlerts` |
| 7 | 只读模式提示 | `read-only` | `info` | ✅ `ack`（**会话级固定键** `ro-banner-dismissed`） | ❌ | 全局 | `readOnlyAlerts` |

⇒ **「可复制」是 0/7 全缺**；**「可关闭」缺 4 类**（3/4/5/6）；**收官横幅的可见范围**是唯一被用户点名的缺陷。

**可复制的零件已经现成**：`components/CopyButton.tsx`（`⧉` icon 模式 + 「已复制」1.2s 反馈 + 失败不抛）
——本 plan **不新建组件**，只接线。

**多课读面已有成熟范式**：`cloudHalts: Record<course, CloudHaltView>`、`trainingCourses: string[]`、
`courseFacts: CourseFactView[]`、`overview: ParallelOverviewView`（`view/console-types.ts`）
⇒ 收官横幅按课成列**不是新形状**，是与既有字段对齐。

### 1.1 一条不该被顺手改掉的既有裁决

`visibleCloudHalts(cloudHalts, viewing)`（`view/interaction.ts`）**故意**只弹当前课程的停机横幅——
2026-09-14 事故后的定案（`tests/cloud-halt-banner.test.ts` 头注）：「首页横幅只弹当前视图课程的停机记录，
其它课程由多课总览徽标承载」。**本 plan 不动它**（用户点名的只有收官横幅），
但 §4.2 会说明「为什么收官横幅该是例外」——两者语义不同，不是同一条规则的两处实现。

---

## 2. 两条根因（带代码证据）

### 2.1 R1 — 收官横幅的**读面**是单课单值，收官横幅是这条链上唯一的单课例外

| 环节 | 现状 | 证据 |
|---|---|---|
| 服务端派生 | `computeSlowSnapshot(cfg, course, probes)` **只读请求课程那一份账本**：`readLogTail(tmp/<course>/training_log.jsonl)`，且有 `if (course && loopAlive)` 前置闸 | `server/api/snapshot-cache.ts`（`course` 是**入参**） |
| 视图契约 | `loopComplete?: LoopComplete | null` —— **单值**，挂在「当前查看课程」的状态视图上 | `view/console-types.ts` |
| 装配 | `state-view.ts` 原样从慢快照解构透传（无聚合） | `state-view.ts:36` |
| 客户端 | `AlertInput.loopComplete?: LoopComplete | null`（单值）；`loopCompleteAlerts` 只产 **1 条** | `view/alerts.ts` |
| 接线 | `<AlertDock items={buildAlerts({ … loopComplete: stateView?.loopComplete … viewing: viewCourse })}` | `web/app/app.tsx:514-525` |

**为什么这在收官场景特别刺眼**（与停机横幅的本质差别）：

- 停机横幅是**可恢复的瞬时态**，而且恢复动作（`立即恢复`）本来就只作用于那门课 ⇒ 只弹本课是自洽的；
- 收官是**终态且不会自己消失**：`loopCompleteFromLedgerTail` 严格只认账本**尾行**，
  直到 resume/新 `run_start` 把它顶掉才为 `null`（`server/api/loop-complete.ts`），
  且它**没有恢复按钮**（`actions: []`）⇒ **你不切到那门课，就永远看不到「它已经跑完了」**。
  现场：h4-aim-k25 与 h4-aim-k10 在 09:46–09:47 相继收官（`tmp/trainer-cluster.log`），
  横幅只在切到对应课程时才出现。

**并且它现在还分不清是哪门课**：`title: 训练已完成（${reason}）` —— **标题里没有课程名**。
⇒ 一旦改成多课成列，不加课名就会出现多条一模一样的「训练已完成」，比不可见更难排查。

**★ 评审补正（比原稿更简的两个事实）**：

1. `trainingLoop` 是**共享组件**（`core/registry.ts`：`SHARED_COMPONENTS` 含它，`scopeOf('trainingLoop', 任意课)`
   恒 `''`；一个 trainer 进程服务所有并行课程）⇒ 原稿「每课的闸 `if (course && loopAlive)`」里的
   `loopAlive` **不是逐课事实，而是全局一个事实**（处理器进程在不在跑）。聚合不需要逐课探组件，
   只需要一次 registry + pid 判活。
2. 因此聚合**不放** `computeSlowSnapshot`——那是**按课程键控**的缓存（`slowSnapshots: Map<course,…>`，
   `snapshot-refresher.ts`）；放进去会让同一份全课聚合按「请求课程」各缓存一份，切课即重算、并发各算一遍。
   聚合放 `state-view.ts`（请求路径上的**便宜结构**，与 `courseEdit` 单课读面并列），见 §4.1。

### 2.2 R2 — 关闭与复制从未进过条目模型：只有「有 ackKey 的条目」能关，没有任何条目能复制

- `AlertAction` 只有两种 kind：`'resume' | 'ack'`（`alerts.ts`）⇒ **没有「复制」这个动作**；
- `AlertItem` 没有可复制文本字段（`id/severity/icon/title/detail/role/actions`）；
- `AlertDock` 渲染动作时只按 `kind` 分发 `onAck` / `onAct` ⇒ `actions: []` 的条目**渲染不出任何按钮**
  （类 3/4/6 因此天然不可关闭；类 5 只有「交还自动池」，能点但关不掉）；
- 现有 ack 通道只有一张表 `TC_CLOUDHALT_ACK`，`app.tsx` 把非 `ro-banner-dismissed` 的键一律丢给
  `ackCloudHalt(key)`（即写进**停机**那张 localStorage 表）⇒ 新条目的 ack 键若**另开一张表**，
  就要双表并读、双写路径与一次性迁移（评审否决，见 §4.3）；
- 只读提示用的是**固定键** `ro-banner-dismissed`—— 对「会话级属性」是对的，
  但对**事件**（收官/停机/编辑被拒/停摆）是错的：第二次同因事件会被旧 ack 吃掉。所以事件类键必须**事件身份**。

---

## 3. 目标 / 非目标

### 3.1 目标

| # | 目标 | 判据 |
|---|---|---|
| **G1** | **收官横幅全局可见**：任一课程收官，**不切课**也能在告警坞看到 | 用例：视图课程 = A、`loopCompletes` 含 B ⇒ 条目含 B 且标题带 `课程 B` |
| **G2** | 收官横幅**按课成列**、可分辨、确定性排序 | 多课同时收官 ⇒ N 条，标题各带课名；`id` 含课程（不撞）；两次渲染顺序一致 |
| **G3** | **7/7 类全部条目可关闭** | 用例：穷举全部条目输入 ⇒ 每条至少一个 `kind='ack'`（驱动自 `buildAlerts` 实际产出集，不手写清单） |
| **G4** | **7/7 类全部条目可复制** | 每条 `AlertItem` 都有 `copyText`（必填、纯函数派生、可单测）；`AlertDock` 每条渲染 `CopyButton`（`icon` 模式） |
| **G5** | 关闭键按**事件身份**（新一次事件重新弹） | 用例：同课程两次收官（`at` 不同）⇒ 两个键；ack 第一次后第二次仍在 |
| **G6** | 关闭**不越界**：只影响告警坞，不影响课程矩阵/总览徽标与任何服务端状态 | 守卫：ack 只写 localStorage（纯客户端），`onAct` 通道零新增；`AlertAction` 闭集不变 |
| **G7** | SSR/水合不破 | 新增按钮不引入非确定性初值；`localStorage` 仍由调用方读出传入（`alerts.ts` 既有约定，注释守卫） |

### 3.2 非目标

| # | 不做 | 理由 |
|---|---|---|
| **N1** | 改 `visibleCloudHalts` 的「只弹当前课」裁决 | 2026-09-14 定案 + 回归用例钉住（§1.1）；用户也没要求 |
| **N2** | 把横幅**持久化**到服务端（历史告警列表、已读回执同步多端） | 那是新功能（要 DB/迁移），本轮只要「本次会话内可关」 |
| **N3** | 改横幅的**严重度/排序/折叠**口径 | `sortAlerts` / `alertDockSplit` 是既有裁决（默认 2 条 + 折叠），本 plan 不动 |
| **N4** | 复制内容做成 Markdown 表格 / 带截图 | 过重；`copyText` 只要「人能贴进日志/报告的一段纯文本」 |
| **N5** | 动服务端任何训练侧逻辑 | 本 plan 是纯 dashboard 变更（`plan/eval-final-round-and-dropped.plan.md` 才是 trainer 侧） |
| **N6** | 给收官横幅加「立即恢复」类动作 | 收官是设计内停车，恢复路径是控制台的「停止→启动」；给它造第二个恢复入口会造第二份真相 |

---

## 4. 语义规格（先定死再写码）

### 4.1 S1 — 收官横幅按课成列（`loopCompletes`，G1/G2）

**语义**：视图契约把单值 `loopComplete` **替换**为 `loopCompletes?: Record<course, LoopComplete> | null`；
告警层为**每门**产出一条，标题带课名、`id` 含课名。**单值字段不保留**——server 与 web 是同一批构建
（三份 bundle 一起出），不存在版本错位；真正会红的只有测试夹具，随本批更新。

- **聚合落点 = `state-view.ts`**（不在按课程键控的慢快照里，见 §2.1 补正 2），逐课调用**现成的**纯函数
  `loopCompleteFromLedgerTail`（不改），单课口径零漂移。
- **`loopAlive` 是全局事实**：`entryForCourse(loadRegistry(), 'trainingLoop', '')` 在册且 `pidAlive`。
  进程不在跑 ⇒ `loopCompletes = {}`（收官后进程仍存活 ⇒ 条目留着，与今日行为一致）。
- **课程清单 = 已开课课 ∪ 查看课程**（`courses.filter(courseEnabled)` ∪ `{course}`）：
  **绝不扫 `tmp/` 目录**；「只遍历已开课课」沿用 `harvestTrainingCourseActuals` 的同款闸与理由
  （tmp 下几十门历史课的账本永远在盘上，逐拍全扫是浪费）。查看课程**无条件并入** ⇒ 保持今天
  「切到那门课就能看到它」的行为零回归（即使该课已停课）。
- **纯函数边界**：新增 `collectLoopCompletes(courses, loopAlive, readTail)`（放 `server/api/loop-complete.ts`），
  逐课 try/catch——单课读失败只少一门、下一拍重试；课程名**显式排序**（禁止 `Object.keys` 顺序当契约）。
- **条目形状**：`id: 'loop-complete:<course>'`；`severity: 'history'`（今天就是）；
  `title: 课程 <course> 训练已完成（<reason>）`（沿用 `halted` 条的 `课程 X ` 前缀模式，**不**往 title 塞 iters——
  `reason` 本身可能已含 it）；`detail` 保留今天那句一字未删（不加前缀，课名由 title 承载）。
- **排序**：多课收官按**课程名**排序（确定性；「按 `at` 升序」与课程名序不可兼得，选确定性与可测性）。
  `at` 只用于 ack 身份。

### 4.2 S2 — 可见范围：为什么收官横幅是「只弹本课」裁决的**例外**（N1 的边界）

- 停机横幅「只弹本课」的理由是**动作自洽**（`立即恢复` 只对该课有效）+ **可恢复**；
- 收官横幅**没有动作**、**不会自愈**（尾行不被顶掉就一直显示）⇒ 用「只弹本课」等于
  **让终态信息在多课场景下不可达**。
- ⇒ 规则写成可复述的一句：**「有恢复动作的瞬时态按课过滤；无动作的终态全局成列。」**
  未来新增横幅按这句判（写进 `alerts.ts` 模块头注）。

### 4.3 S3 — 关闭：统一的事件身份 ack 键，**一张表、零回归**（G3/G5/G6）

**语义**：所有事件类条目都有 `kind='ack'` 动作，键 = `alertAckKey(kind, subject, eventId)`；只读提示是
**会话级属性**，保留既有字面键 `ro-banner-dismissed`（app 分派按它走专用分支，不塞进事件键空间）。

- 新函数放 `view/interaction.ts`（与 `cloudHaltAckKey` 同族）：`alertAckKey(kind, subject, eventId) => "<kind>|<subject>|<eventId>"`。
  `cloudHaltAckKey` 改为**委托实现** ⇒ 停机两条的键串**逐字节不变**（升级不丢已读）。
- **存储 = 既有那张表**（`TC_ALERT_ACKS`，常量改名、**值冻结** `tc.cloudHalt.ack`）：JSON 数组 +
  旧单值兜底（`parseAlertAcks`，由 `parseCloudHaltAcks` 改名）。**不新开第二张表**——键是不透明字符串，
  双表 = 双读双写 + 一次性迁移；单表 = 今天的读侧/写侧电路原样（评审处置 C1/C2）。
- **事件身份**逐条定死（不许用固定键）：

  | 条目 | `kind` | `subject` | `eventId` |
  |---|---|---|---|
  | 训练已完成 | `loop-complete` | 课程名 | `at`（收官时刻） |
  | 云端停机 / 已恢复 | `halted` / `recovered` | 课程名 | `at` / `clearedAt`（**与今天逐字节同键**） |
  | PPO 排队超时 | `ppo-stall` | `jobId` | `it`（`it` 缺省用 `''`） |
  | 离线静默停摆 | `offline-stall` | 课程名 | `why='pending-export'` → `flippedAt`；否则 `lastMtime` |
  | 课程编辑被拒 | `course-edit` | 查看课程 | `at`（缺省回退 `fields.join(',')`，仅夹具会走到） |
  | 只读模式 | —（保留字面键） | — | `ro-banner-dismissed`（会话级固定语义） |

- **ack 的作用域**：只写 localStorage（G6）⇒ 不加任何 `onAct` 动作（`AlertAction` 闭集不变，
  避免服务端出现第二条「消警」路径）。
- `app.tsx:onAck`：`ro-banner-dismissed` 走会话键分支；其余一律 `ackAlert(key)`（原 `ackCloudHalt`，
  仍写同一张表）。

### 4.4 S4 — 复制：条目自带可复制纯文本（G4）

**语义**：`AlertItem` 新增 `copyText: string`（**必填**，由 `withCopy()` 逐条派生，纯函数可单测）：

```
<title>
<detail>
（课程 <course> · 条目 <id> · 严重度 <severity>）      ← 无课程时省略「课程」段
```

- 形态取「**能直接贴进日志或报告的一段纯文本**」：三行、无 Markdown、无表格（N4）；
  第三行含条目 id（O3 已裁决：排障时能定位到 `alerts.ts` 的哪一条）。
- `AlertDock` 每条渲染 `<CopyButton text={a.copyText} label="告警" icon />`（`icon` 模式不占宽，
  语义走 `title`/`aria-label`）⇒ **不新建组件**。
- 复制按钮与动作按钮**分区**（`.tc-dock__acts` 之外的新 `.tc-dock__copy`，位于条目尾部、
  动作区左侧）：复制是工具、动作是决策，**不抢主按钮位**（`severity=err` 时视觉优先级不倒置）。
- **交叉收益**：trainer 侧那份 plan 规定「收官 drain 必须在 `write_run_complete` **之前**」
  （`plan/eval-final-round-and-dropped.plan.md` §4.1）⇒ 横幅出现的时刻 = **收官 eval 已补齐**的时刻，
  操作员复制走的那段文字因此包含「收官已完成」的完整语境。**两份 plan 建议同批合入**（§8）。

### 4.5 S5 — 排序与折叠不受影响（N3）

`sortAlerts` 按严重度 + 输入序；`alertDockSplit` 默认 2 条。新增的复制按钮**不占条目计数**（按钮不是条目）；
多课收官可能一次多出 N 条 ⇒ 由既有折叠承接，**不提高 `ALERT_DOCK_DEFAULT_VISIBLE`**。

---

## 5. 落点（按函数名定位）

| 环节 | 文件 | 改动 |
|---|---|---|
| 视图契约 | `dashboard/src/web/view/console-types.ts` | `loopComplete` → `loopCompletes?: Record<string, LoopComplete> | null` |
| 服务端聚合 | `dashboard/src/server/api/loop-complete.ts` | 新增纯函数 `collectLoopCompletes(courses, loopAlive, readTail)` |
| 服务端装配 | `dashboard/src/server/api/state-view.ts` | 判活（共享 trainer）+ 课程清单（已开课 ∪ 查看课）+ 逐课读账本尾 → `loopCompletes` 入视图 |
| 慢快照 | `dashboard/src/server/api/snapshot-cache.ts` | **删除** `SlowSnapshot.loopComplete` 与派生行（账本尾读保留给 `courseEdit`） |
| 告警条目 | `dashboard/src/web/view/alerts.ts` | `AlertInput.loopCompletes`；`loopCompleteAlerts` 逐课成列 + 课名 + 确定性排序；**七类条目全部补 `ack`（只读除外）与 `copyText`**；模块头注写 §4.2 规则 |
| ack 键 | `dashboard/src/web/view/interaction.ts` | 新增 `alertAckKey`（`cloudHaltAckKey` 委托它）；`parseCloudHaltAcks` → `parseAlertAcks` |
| 存储键 | `dashboard/src/web/view/legacy-keys.ts` | `TC_CLOUDHALT_ACK` → `TC_ALERT_ACKS`（**值冻结** `tc.cloudHalt.ack`，注释说明） |
| 坞组件 | `dashboard/src/web/components/AlertDock.tsx` | 每条渲染 `CopyButton`（icon，`.tc-dock__copy` 分区）；动作渲染不变 |
| 接线 | `dashboard/src/web/app/app.tsx` | 传 `loopCompletes`；ack 状态/函数改名 `alertAcks`/`ackAlert`；分派不变（ro 专用分支 + 其余进表） |
| 样式 | `dashboard/src/web/theme.css` | 新增 `.tc-dock__copy`（贴 `.tc-dock__acts` 旁） |

**分层不变**：`server/api/*` 与 `web/view/*` 之间只走类型；`alerts.ts` 仍是**纯函数**（无 IO、无 localStorage
——`acks` 由调用方读出传入，G7 依赖它）。

---

## 6. 实施（W 提交，每步独立可绿；每步含「改坏必红」自查）

### W0 — 先钉现状（不改生产行为）→ 已并入 W1–W3 的用例批

- 新用例全部落在既有 `dashboard/tests/web-alert-dock.test.ts`（它就是告警坞的关注点；
  评审处置 D5：不开第二条测试线）：
  - `收官横幅全局可见：视图课程 A、loopCompletes 含 B ⇒ 有 B 条目且标题带 课程 B`（现状**必红**：无此输入）；
  - `全部条目可关闭：穷举全部条目的 buildAlerts 产出 ⇒ 每条都有 kind='ack'`（现状**必红**：3/4/5/6 缺）；
  - `全部条目可复制：每条都有 copyText 且含 title/detail`（现状**必红**：字段不存在）。
- 服务端纯函数用例落 `dashboard/tests/server-api-loop-complete.test.ts`：
  `collectLoopCompletes` 的进程死/多课/单课读失败/确定性排序。

### W1 — S3：ack 键统一 + 全部事件条目可关闭（G3/G5）

1. `alertAckKey`（Kind 闭集）+ `parseAlertAcks` + `TC_ALERT_ACKS`（值冻结）；
2. `alerts.ts` 3/4/5/6 类条目补 `ack` 动作（键按 §4.3 表逐条定死）；
3. `app.tsx`：`ackCloudHalt` → `ackAlert`（同表）；分派保留 ro 专用分支；
4. **必红自查**：把任一条目的 `ackKey` 换成固定键 ⇒「两次事件两个键」用例变红；停机键换成新写法
   （如 kind 改 `cloud-halted`）⇒「停机 ack 键逐字节不变」用例变红。

### W2 — S1：收官横幅多课成列（G1/G2）

1. 契约替换 + `collectLoopCompletes` + `state-view` 装配 + `AlertInput.loopCompletes`；
   删除 `SlowSnapshot.loopComplete`；
2. `loopCompleteAlerts` 逐课成列，标题带课名，按课程名确定性排序；
3. **必红自查**：① 聚合课程清单退回「只读查看课」⇒ W0-① 变红；② 去掉标题课名 ⇒「多课可分辨」变红；
   ③ 排序改回 `Object.keys` 顺序 ⇒「两次渲染顺序一致」变红；④ 删查看课程无条件并入 ⇒「停课但收官的
   查看课仍显示」变红。

### W3 — S4：全条目可复制（G4）

1. `AlertItem.copyText` 必填 + `withCopy()` 统一派生（**不**在组件里拼，避免两处各拼一遍）；
2. `AlertDock` 渲染 `CopyButton`（icon）+ `.tc-dock__copy` 分区；`err` 档不做主按钮样式；
3. **必红自查**：删任一条目的 `copyText` ⇒ W0-③ 变红；SSR 断言复制键在 ERR 条目里也不是主按钮样式。

### W4 — 回归与边界

- 必须仍然绿：`cloud-halt-banner.test.ts`（N1：停机仍只弹本课；停机 ack 键字节不变）、
  `web-ssr-readonly.test.ts`（G7 水合；夹具字段随契约改 `loopCompletes`）、
  `server-api-snapshot-cache.test.ts`（删字段不影响缓存语义）、
  `server-api-loop-complete.test.ts`（单课纯函数原样）、`web-components.test.ts`（SSR 面夹具）。
- `web-alert-dock.test.ts` 本身：夹具 `clean.loopCompletes`、`item()` 补 `copyText`、
  「七类全开」类用例更新为含 `loopCompletes` 与 `offlineStalls`。
- 新增 SSR 断言：复制键在 SSR 首帧存在（`aria-label="复制告警"`）且**不带事件属性**；
  交互正确性仍属既有盲区（`preact-render-to-string` 丢弃事件处理器，见该文件尾注）——
  本项**不**假装能用 SSR 钉住 onClick。

---

## 7. 预期效果（带不确定性）

| 项 | 现状 | 目标 | 不确定性 |
|---|---|---|---|
| 收官横幅可见性 | 切到该课才看到（单课单值） | **全局按课成列**，不切课可见 | **无**（R1 已定位到字段与派生点） |
| 多课收官可分辨 | 标题无课名 | 标题带 `课程 <name>`，`id` 带课名 | 无 |
| 可关闭 | 3/7 类 | **7/7 类** | 无 |
| 可复制 | **0/7 类** | **7/7 类**（`copyText` 三行纯文本 + `CopyButton`） | 无 |
| 停机横幅 ack | 事件身份 ack | **逐字节不变、零迁移**（单表冻结值 + 委托键） | 无（升级不重弹） |
| 服务端成本 | 每快照读 1 份账本尾 | 每请求读 **已开课课数（∪ 查看课）** 的账本尾（每份 ≤1000 行窗口） | 打开课数 ≥10 时需复核；上限见 §9-P1 |
| 训练侧 | — | **零改动** | N5 |

---

## 8. DoD 与门禁

- [ ] **W1** 全部事件条目都有 `ack`；键按事件身份；停机键逐字节不变；旧 ack 表**未被覆盖**（同表同值）。
- [ ] **W2** `loopCompletes` 存在；单值 `loopComplete` 已删除且无残留引用；标题带课名；排序确定。
- [ ] **W3** 每条 `copyText`；`CopyButton`（icon）分区渲染；`err` 档样式不倒置。
- [ ] **红线复核**：`visibleCloudHalts` 未改（`cloud-halt-banner.test.ts` 绿）；`alerts.ts` 仍纯函数
      （无 `localStorage`/IO）；`ALERT_DOCK_DEFAULT_VISIBLE` 未改；未新增 `onAct` 动作。
- [ ] 门禁（dashboard 侧独立门禁，AGENTS §9）：
  ```bash
  cd dashboard && bun run typecheck && bun run test
  cd dashboard && bun run build:ui      # 三份 bundle（web/server/launch）
  bun run check                          # 根项目
  bun run build                          # 根 shipping gate
  ```
- [ ] **文档**：新 DECISIONS 条目（`§2026-10-03-goalnn-dashboard-alert-dock-global`：单表冻结值 +
      只读例外 + 聚合落层，含被否决备选）+ `docs/nn/console.md` 新节（当前最大号 + 1）+
      `docs/nn.progress.md` 对应行 + memory 一行。
- [ ] **与 trainer 侧 plan 同批合入**（§4.4 的交叉收益）：`plan/eval-final-round-and-dropped.plan.md`
      规定 drain 先于 `run_complete`；若只合本 plan，横幅出现的时刻语义不变但**不含收官 eval 语境**。

---

## 9. 预注册门槛与开放问题（评审后已闭环）

| # | 候选 | 门槛 | 达标做法 | 状态 |
|---|---|---|---|---|
| **P1** | 多课聚合的成本节流 | 已开课 ≥10 的会话里，快照耗时上升可测 | 已按「已开课 ∪ 查看课」限流（同 harvest 闸）；再不够可加「每拍轮转」 | 门槛保留 |
| **P2** | 复制内容带上 hub/组件健康快照 | 用户实际用过复制且抱怨信息不够 | 扩 `copyText`（**新字段**，不改现有三行结构） | 未触发 |
| **P3** | 已读回执多端同步 | 用户在两台机器上抱怨「又弹一次」 | 服务端存已读（N2，本轮明确不做） | 未触发 |
| **P4** | 收官横幅的恢复入口 | 用户明确要求在横幅上直接继续训练 | 开一个动作（会动 N6 的裁决，需先撤 N6） | 未触发 |

**开放问题（原三条，评审已裁）**

| # | 原问题 | 裁决 |
|---|---|---|
| **O1** | 停机换键会丢旧 ack ⇒ 升级后重弹？ | **不换键**：单表 + `cloudHaltAckKey` 委托 `alertAckKey` ⇒ 键逐字节不变。**零回归、零迁移**（C1/C2 处置） |
| **O2** | 多课收官一次多出 N 条是否按课分组？ | 按既有折叠承接，不改折叠口径（N3） |
| **O3** | `copyText` meta 是否含条目 id？ | **含**（排障定位到条目来源） |

---

## 10. 一句话给接手 agent

> **告警坞已经是唯一入口**（`view/alerts.ts` 纯函数 → `components/AlertDock.tsx` → `app.tsx:514-535`），
> 本轮只动三处模型，**不新建组件、不新建横幅、不新建表**：
> ① **收官横幅的读面是单课单值**——`StateView.loopComplete` 单值、慢快照按课程键控；
> 聚合要放 `state-view.ts`（便宜结构），判活用**共享** `trainingLoop`（`scopeOf` 恒 `''`）的全局事实，
> 课程清单 = 已开课 ∪ 查看课（同 harvest 闸，
> 绝不扫 `tmp/`），逐课复用 `loopCompleteFromLedgerTail`；条目按课成列、**标题带课名**。
> ② **可关闭**：3/7 类缺 ack。补 `alertAckKey(kind, subject, eventId)`（事件身份；只读提示保留
> 会话级字面键），**写回既有那张表**（`TC_ALERT_ACKS`，值冻结 `tc.cloudHalt.ack`；远离第二张表）。
> ③ **可复制**：0/7 类。`AlertItem` 加必填 `copyText`（三行纯文本，`withCopy` 统一派生、可单测），
> `AlertDock` 用**现成的** `CopyButton`（`icon` 模式）接线，与动作区**分区**。
> **不要**：改 `visibleCloudHalts` 的「只弹本课」（2026-09-14 定案 + 回归钉住）；
> 给停机键换 kind（那是净回归）；开第二张 ack 表；加服务端「消警」路径；
> 提高 `ALERT_DOCK_DEFAULT_VISIBLE`；给收官横幅造「立即恢复」（会造第二份真相）。
> **门禁**：`cd dashboard && bun run typecheck && bun run test && bun run build:ui` + 根 `bun run check` /
> `bun run build`；每条语义都有「改坏必红」；`alerts.ts` 必须**仍是纯函数**（localStorage 由调用方读出传入）。

---

## 11. 评审处置表（原稿 11 条 → 本版落点）

| # | 评审发现 | 处置 |
|---|---|---|
| A1 | 矩阵漏 T8（7 类不是 6 类） | §1 矩阵补第 5 行；G3/G4 改 7/7；用例穷举 `buildAlerts` 产出集 |
| A2 | W4 漏 `web-alert-dock.test.ts` 夹具/断言 | §6-W4 显式列出改动面；复制/G3 用例并入该文件 |
| A3 | `courseEdit` 事件身份没用现成的 `at` | §4.3 键表改用 `at`（回退仅夹具） |
| B1 | 聚合落层/缓存未定 | §2.1 补正 2 + §4.1：落 `state-view.ts`，不进按课程键控的慢快照；判活=共享 trainer |
| B2 | 双字段=第二份真相 | §4.1：**替换**单值字段，不保留 |
| C1 | 停机换键=无收益回归 | §4.3：键逐字节不变（委托实现），零回归；被否决备选入 DECISIONS |
| C2 | 双 ack 表 | §4.3：单表 + 值冻结，无迁移 |
| D1 | 排序自相矛盾 | §4.1：定死「按课程名」，`at` 只作 ack 身份 |
| D2 | detail「一字未删」与「前置课名」冲突 | §4.1：detail 原文不动，课名由 title 前缀承载 |
| D3 | G7 与 W4 SSR 盲区抵消 | §6-W4：点明 SSR 可钉「复制键存在」，交互仍属既有盲区 |
| D4 | 停摆条目 ack 身份未定 | §4.3 键表补 `offline-stall` 行 |
| D5 | 新测试文件与 W4 改旧文件重复 | §6：全部并入 `web-alert-dock.test.ts`（告警坞关注点） |
