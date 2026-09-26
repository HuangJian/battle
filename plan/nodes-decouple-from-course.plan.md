# Plan: nodes-decouple-from-course — 节点统计与课程解耦 + 按天切换视图

> **交付物**：本 plan。**状态**：2026-09-26 评审后修订并进入实施（§7 记录本轮修订）。
> **阅读顺序**：§1 现状 → §2 需求与裁决 → §3 方案 → §4 边界 → §5 DoD → §6 已关闭项 → §7 修订记录。
> 行号只作定位加速，判据看函数名。

---

## 0. 一句话目标

**节点是机群级资产：合并 `tmp/**` 下所有训练流的 `dist-agent-meta.jsonl`，视图按「天」切换，
统计只用时间口径 —— 课程内序号 `it` 不出现在任何展示面。**

## 1. "绑定到课程"的准确机制（不是显式过滤，而是数据只来自一门课）

| # | 事实 | 出处（2026-09-26 复核） |
|---|---|---|
| S1 | `aggregateNodeHistory()` **无参**，候选 meta 按 mtime 降序后 —— **`sources = candidates.length > 0 ? [candidates[0]] : []`**，只聚合"最近活跃"那一门课 | `pool-history.ts:249`（注释动机：避免旧流数千条历史淹没新数据） |
| S2 | meta **每流一份**：`<traj root>/dist-agent-meta.jsonl`（rollout 记 `mode:"rollout"`、eval 记 `mode:"eval"`，**同册入账**） | `rl/eval_dispatch.py:163`、`rl/dispatch.py:189`、`rl/agent_meta.py`（唯一写面） |
| S3 | **UI 与缓存层早已声明"机器级"**：`api/pool.ts:13-20`；`console-types.ts:45`「课程与 worker 节点正交」 | 两处 |
| S4 | 现有对齐基准是**课程内序号 `it`**：`globalMaxIt` / `pickBaseIter` / `contribRollout\|contribEval` / `lastIter` | `pool-history.ts:121-175` |
| S5 | 现有时间维度只有三级：`POOL_EPOCH_MS`（受控清空锚点）、`hourAgoStr`（最近一小时）、滑动窗口 50 局。**没有"天"** | `:37,196-198` |
| S6 | 健康度"只由最近完成轮的贡献数判定"（`nodeHealth`，2026-09-20 已实现） | `web/view/console-types.ts:93-96` |
| S7 | 性能护栏：递归只下探两层；探测层冷算 2448–2552ms（历史事故：下探三层 1.39s/次 → 页面 6.7s） | `pool-history.ts:209-244`、`api/pool.ts:8` |
| S8 | **（评审订正）meta 行的 `ts` 是 Python `time.strftime(...)` 写的、训练机本地时间、无时区后缀** —— 见 §4.2 | `rl/dispatch.py:861,976`、`rl/eval_dispatch.py:304,480,609`、`rl/batch_runner.py:862` |
| S9 | `aggregateNodeHistory()` 有**两个**消费者：`api/pool.ts`（`PoolProbes` 探测缓存）与 `snapshot-cache.ts:105-118`（`FleetProbes`：喂 pill 的 `lastContrib` 与 `isSlowNode`） | 两处 |
| S10 | 池扫描根可被 `BCITY_POOL_DIR` 重定向（单测用），默认 `REPO_ROOT/tmp` | `core/paths.ts:tmpPoolDir` |
| S11 | 扫描面**不止课程**：独立 eval run 目录 `tmp/<name>.jsonl.run/dist-agent-meta.jsonl` 也会命中，且**无 `training_log.jsonl`** ⇒ 水位退化 | `rl/eval_course_once.py:160-170`、`rl/batch_runner.py:852` |
| S12 | 长驻本机腿仍逐局写 meta（`node:"local"`、带 `it`/`mode`） | `rl/dispatch.py:849,966` |

### 1.1 水位 vs 时间窗（2026-09-24 订正，保留）

* **"取一个全局 it 数当基准"不行**：`it` 是课程内序号，跨课不可比。
* **"逐课各取自己的完成水位、分别算完再合并"可行**：`lastCompletedIter(metaAbsPath)` 读的是**该 meta
  同目录的账本尾部**，天然逐流；现状只是"对唯一那个源"调用了一次。
* 停摆的课会把水位贡献永久挂在合计里 ⇒ **时间窗**回答"这份数据有多新／这段窗口干了多少活"。
  **水位与时间窗各司其职，不互替。**
* ⇒ 关键定位（R5）：**水位从「展示口径」降级为「数据过滤器」**——继续用于排除**进行中那一轮**的行，
  但不再占用任何 UI 列。
* **补充订正**：健康度取「**最新完成的整轮**」的 rollout/eval 数据，比贡献量与并发数；"最新完成"
  按**完成时刻**判（**不是**比 `it` 数值大小）。

## 2. 需求与裁决

### 2.1 用户裁决（2026-09-24）

| 待议项 | 裁决 |
|---|---|
| "全部"视图是否保留 it 口径 | **不保留**（`it` 不进任何展示面） |
| `?course=` 按课程过滤 | **不保留**（彻底移除 `/api/pool` 的 `?course=`） |
| 多 hub / 多 traj_root | **无此计划**（不设计、不留占位） |
| contrib 是否仍分 rollout/eval | **分**（两列保留，改成窗口口径） |
| 健康度 | 取「最新完成整轮」的 rollout/eval，比贡献量与并发数（现成 `nodeHealth`） |

### 2.2 需求

* **R1 数据源解耦**：合并 `tmp/**` 下**所有训练流**（课程目录 + 独立 eval run 目录）的 meta 行。
* **R2 按天切换**：今天 / 昨天 / 最近 7 天 / 全部（并接受任意 `days=N`）。
* **R3 单一时间口径**：API 展示字段与 UI 里**不再有 `it`**；贡献 = **窗口内**成功局数（分 rollout/eval）。
* **R4 `POOL_EPOCH_MS` 语义保留**：受控清空锚点仍是累计起点，与窗口取**交集**。
* **R5 水位只作过滤器**：逐流"最近完成轮"用于排除进行中那一轮的行（不进展示、不进 API 字段）。
* **R6 不改**：`ts` 的写入格式、meta 的行 schema、`dist-agent-meta.jsonl` 的落点。

## 3. 方案

### 3.1 数据层（`server/pool-history.ts`）——**一次算全量，切天是纯投影**

```ts
// ① 采样：合并所有流（替换 `sources = [candidates[0]]`）
//    预筛：整份文件 mtime < POOL_EPOCH_MS 才跳过 —— **钉在 epoch**，与"看哪天"无关
//    （见 §4.3：预筛若随窗口变，"切天零重算"就不成立）。
export function aggregateNodeHistory(): HistoryAggregate        // 签名不变（机器级、无参）

// ② 逐流水位（§1.1）：**每个源各自一份** it 分布，绝不合并成一张全局图
//    （同一 it 在两门课里是两回事；合并会让"最新完成轮"拿到跨课相加的数）
interface FlowState {
  src: ActiveFlow
  itByNode: Map<string, Map<number, { rollout: number; eval: number }>>  // ★ 逐流
  baseIt: number                    // pickBaseIter(completedIt, maxItOfFlow, hasRowsAtFlow)
  completedAtMs: number | null      // ★§3.4：账本最后一个 iteration 事件的 time（本地 ms）
  contribAtBase: Map<string, number> // 该流在 baseIt 的 rollout+eval 成功局数
}
// 解析该流的行：ts >= epochStr 且 it <= baseIt（= 已完成轮）才落桶
//   —— `it <= baseIt` 是**数据卫生**，不是展示口径（R5）

// ③ 落桶：`byDay: Map<'YYYY-MM-DD'(本地), Map<node, DayBucket>>`
export interface DayBucket {
  ok: number; fail: number
  rollout: number; eval: number          // 窗口内成功局数（分 mode）
  results: boolean[]                     // 最近 ≤10 条结算结果（时间升序）→ 窗口内完成率
  elapsed: number[]; wall: number[]      // 最近 ≤50 成功局样本（pushWindowSample 上限）
  lastOkTs: string; lastFailTs: string   // 最近成功/失败（窗口内取最大）
  lastError: string; lastErrorTsMs: number | null  // 最近失败原因 + 其毫秒（近一小时由投影判）
  lastOkTsMs: number | null; lastOkElapsedSec: number | null  // isSlowNode 输入
  lastTs: string
}

// ④ 投影（纯函数、零 IO、可单测）
export interface NodeWindow { key: string; label: string; fromDay: string; toDay: string;
                              startMs: number; endMs: number }
export function resolveWindow(spec: string, nowMs: number, epochMs: number): NodeWindow
export function projectWindow(agg: HistoryAggregate, w: NodeWindow): WindowAggregate
```

* `HistoryAggregate`（缓存的那一份，与窗口无关）：`byDay` / `sources` / `epochMs` /
  `lastContrib: Map<node, number>`（§3.4 的跨课结果）/ `latestRound`（诊断：胜出流 + 轮 + 完成时刻）。
* `WindowAggregate`（投影结果）：`hist: Map<node, NodeHistory>` / `sources` / `epochMs` / `window` / `latestRound`。
  **所有展示字段（含 `avgElapsedSec` / `avgWallSec` / 完成率 / 最近成功失败 / 最近错误）都是窗口口径** ——
  切天就是换个窗口看同一份数据（唯一例外：`lastContrib` 是"最新完成轮"、与窗口无关）。
* `ActiveFlow` 从"过滤依据"升级为**诊断**：`sources: ActiveFlow[]`（本次合并了哪些流、各多少行），
  节点页脚注显示"数据来自 N 个训练流"。
* **删除**展示侧的 `globalMaxIt` / `lastIter` / `lastIterOk` / `contribRollout` / `contribEval`
  （裁决：「`lastIter*` 从类型里删」，不是只从 UI 撤）；`baseIt` 只存在于聚合内部与 `latestRound`。
  判据：删完 `NodeHistory` / `NodeHistoryRow` / `PoolView` 上**不再有任何"以 it 为口径"的展示字段**
  —— 编译期就能抓到漏改。**注意还要动第二个消费者**（S9）：`snapshot-cache.ts` 用的
  `agg.globalMaxIt` / `h.lastIterOk` 一并在本轮改成 `agg.lastContrib` / `projectWindow(agg, 'all')`。

### 3.2 接口层（`server/api/pool.ts`）

* `GET /api/pool?days=today|yesterday|7|all`（也接受 `days=N`，N ≥ 1 = 最近 N 天含今天）；
  **缺省 `today`**；**移除 `?course=`**（R3/裁决，见 §4.4 的牵连面清单）。
* `PoolProbes`（`api/pool.ts:47-58`）**不动**：仍缓存全量 `agg`；`days` 只在 `assemblePoolView`
  之后投影 ⇒ 切天不触发探测重算。
* `Web/NodeHistoryRow`（`web/view/pool-types.ts`）字段口径重整：
  `contribRollout` → `winRollout`、`contribEval` → `winEval`（**窗口**口径，分两列）；
  `lastContrib` 保留为**最新完成轮**口径（`nodeHealth` 的输入，§3.4）；删掉 `contrib`（合计，已被两列取代）/
  `lastIter` / `globalMaxIt`。`PoolView.activeFlow` → `PoolView.sources` + `PoolView.window`。
* **状态列改名（消除"两个健康度"）**：表格状态列吃的是 `poolStatus`（**完成率**，`pool-history.ts:355-362`），
  改名为 **「成功率」**（阈值不变 ≥90% / ≥70% / <70%），与 pill 的 `nodeHealth`（**产能**：
  健康/缓慢/离线）**分列分名**；两个徽章各自 hover 写清问题域：
  *「成功率 = 交给它的活干成了吗」* / *「产能 = 这一轮它交了多少活」*。
* **`poolStatus` 口径裁决（评审关闭）**：**函数体不动**，但它的输入 `recent` 变成**窗口内**的最近 ≤10 条结算
  ⇒ 表头/脚注必须写明"**成功率随所选窗口变**"（今天窗口结算少时会跳）。

### 3.3 视图层

* 节点页（`app.tsx:599` 段）加 Segmented（用现成组件 `web/components/SegmentedControl.tsx`，
  类名 `.tc-segmented`，样式 `theme.css:757`）：**今天 / 昨天 / 7 天 / 全部**；切换只改前端 state +
  重取 `/api/pool?days=`。
* 列口径：
  * 「上轮贡献」→ **「窗口内局数（rollout / eval 两列）」**；
  * 「完成率」→ 改名 **「成功率」**，仍按"最近 10 条结算"，但**限制在窗口内**（不足 10 条按实际条数并显示 n）；
  * 「最近成功/失败」保留绝对时间戳；「平均耗时/机侧墙钟」改为**窗口内**均值（脚注更新）。
* `NodePills.tsx` 的 `lastContrib`（来自 `/api/state` 的 `FleetProbes`）与节点表格（来自 `/api/pool`）
  仍走**两个端点**，但**共用同一份 `aggregateNodeHistory()` 的 `lastContrib`**（§3.4），避免两套口径。
* 删掉任何"按课程看节点"的入口（若有）。

### 3.4 健康度：**取「最新完成的整轮」，比贡献量与并发数**

**判据本身是现成的，不动**：`nodeHealth(contrib, concurrency)`（`web/view/console-types.ts:93-96`）：

| 状态 | 判据 |
|---|---|
| `offline` | `contrib ≤ 0`（含 `-1` = 无池数据）⇒ 这一轮它没产出 |
| `healthy` | `contrib ≥ concurrency` ⇒ 满负荷产出 |
| `slow` | `0 < contrib < concurrency` ⇒ 在产出但没吃满并发 |

**本 plan 要改的是 `contrib` 的来源**（"最新完成的整轮"怎么选）：

```ts
// 逐流：水位轮 + 它的完成时刻（在 FlowState 里，见 §3.1）
// 跨课：按完成时刻取最新 —— **不是**按 it 数值比大小（it 是课程内序号，§1.1）
const candidates = flows.filter(f => f.completedAtMs != null)
const latest = candidates.sort((a, b) => b.completedAtMs! - a.completedAtMs!)[0] ?? null
agg.lastContrib = latest && latest.baseIt >= 0
  ? new Map(allNodes.map(n => [n, latest.contribAtBase.get(n) ?? 0]))
  : new Map()   // 空 = 无池数据（消费方 → -1）
```

* **"停摆课不进候选"不用额外过滤**：停摆课的 `completedAtMs` 天然更旧，**按完成时刻排序即自然落选**
  （这正是 §1.1 反对"按 it 比大小"的原因——按 it 会把停摆课的高序号误当最新）。所有流的历史局数
  仍按天落桶、不被丢。
* **完成时刻怎么取**：`training_log.jsonl` 的 `iteration` 事件自带 `time`
  （`rl/events.py::write_iteration`，Python `strftime("%Y-%m-%d %H:%M:%S")` 本地）—— 比"该轮 meta 最后一行的 ts"
  准（后者是"最后结算"，与"轮完成"差一个 PPO 的时间）。读不出 `time` → 用该 meta 的 mtime 兜底 + 记一行诊断。
* **`contrib` 是那一轮的 rollout + eval 成功局数**（`NodeView.lastContrib` 的既有语义，`-1` = 无池数据）。
  分列展示仍按 §3.3（`winRollout`/`winEval` 是**窗口**口径；`lastContrib` 是**最新完成轮**口径）。
* **不做**：不引入任何"新鲜度阈值"（`HEALTHY_FRESH_MS` 一类常量**删掉**）。轮有多旧只作脚注信息。
* **与 `isSlowNode` 的分工**：`isSlowNode` 仍是"ping 失败但仍在结算"的**可达性**口径（`:383-391`），
  保持现状（输入取 `projectWindow(agg, 'all')`）。
* ⚠ **并存提示**：服务端 `poolStatus`（成功率）与 `nodeHealth`（产能）**不是同一个指标**，
  本 plan 只修 `lastContrib` 的来源，**不改 `poolStatus` 公式**（§3.2 已说明其输入随窗口）。

## 4. 边界

### 4.1 `it` 的定位（R5）

| 用途 | 允许？ |
|---|---|
| 聚合内部：逐流取水位（① 过滤"进行中那一轮"；② 选"最新完成轮"算 `lastContrib`） | ✅ 必须 |
| API 展示字段 / UI 列 / 排序键 | ❌ 禁止 |
| 跨课程比较（如"两门课的 it 谁大谁新"） | ❌ 禁止（按**完成时刻**选，§3.4） |

`it` 相关返回值收敛进内部结构（不进 `PoolView`），类型注释写明"窗口模式与 it 口径互斥"。

### 4.2 时区（S8，评审订正）

* **事实**：`dist-agent-meta.jsonl` 的 `ts` 与 `training_log.jsonl` 的 `time` 都是
  Python `time.strftime(...)` 写出的**训练机本地时间、无时区后缀**（`"YYYY-MM-DDTHH:MM:SS"` 或空格分隔两种）。
  唯一带 UTC `toISOString()` 的是 sampler-agent 写进 `lastError` 的**字符串前缀**（`stripIsoPrefix` 处理的那份），
  与 meta 的 `ts` 字段无关。
* **推论**：按天分桶取**本地日 key** 是对的；`parseTsMs` 把无时区串按本地解析 ⇒ 直接用即可。
  **禁止**改成 `Date.UTC`（会把傍晚的局跨天错桶）。
* **单测**：仍应固定 `TZ`——理由不是"UTC 要换算"，而是"避免宿主机 TZ 影响本地日期键的边界用例"。

### 4.3 性能

* 预筛先于读取：**整份文件 mtime < `POOL_EPOCH_MS` 才跳过**（钉在 epoch，day-independent）。
  这样缓存的全量 `agg` 与"看哪天"无关，"切天零重算"成立（§5 有断言）。
* 单文件上限：超过阈值只读**尾部 N 行**（`readLedgerTail` 先例）——⭐ **与天窗口有取舍**：
  截断后更早的天数会缺数据，UI 必须标注"该流只统计最近 N 行"（诚实截断，不静默）。
* 全量聚合结果进 SWR 缓存；窗口投影微秒级。

### 4.4 epoch 与窗口 / `?course=` 牵连面

* epoch 与窗口取**交集**：`ts >= max(POOL_EPOCH_MS, windowStart)`；epoch 是"累计起点"、
  窗口是"视图窗口"，不得互相覆盖。（实现上：解析期已按 epoch 过滤，窗口按本地日 key 过滤 ⇒ 自然取交集。）
* 移除 `/api/pool` 的 `?course=` 要一并改：`server.ts:328` 路由、`web/app/lib/api-client.ts::fetchPool`
  签名、`NodeStats` 的 `course` prop 与过时注释「课程键控缓存」、`server.ts:19` 的路由文档、
  `api/courses.ts` 的 sanitize 注释清单。`PoolView.course` 保留为**操作员课程回显**（不再接受覆盖）。

## 5. DoD

- [ ] `aggregateNodeHistory` 合并**所有**流；`activeFlow` → `sources: ActiveFlow[]`；
- [ ] **逐流 `itByNode`**（同一 `it` 不跨课合并），水位过滤在解析循环内生效（`it <= baseIt_flow`），
      且 `it` **不出现在 `PoolView`**；
- [ ] `byDay` 本地时区分桶 + `resolveWindow` / `projectWindow(agg, w)` 纯函数；
      `DayBucket` 含 `results`/`lastOkTs`/`lastFailTs`/`lastError*`（窗口内完成率与错误列所需）；
- [ ] `/api/pool?days=` 生效、**`?course=` 已移除**；**切天不重算**（spy：`aggregateNodeHistory`
      调用次数不随 `days` 变）；
- [ ] 节点页 Segmented + 列口径改名（窗口内局数，rollout/eval 两列）+ 无 `it` 列 + `NodePills` 同源；
- [ ] **类型层删除**：`NodeHistory` / `NodeHistoryRow` / `PoolView` 上不再有 `lastIter*` / `globalMaxIt` /
      对齐轮 `contrib*`；`snapshot-cache.ts` 一并改完（漏改由 `tsc` 门禁抓）；
- [ ] **§3.4 健康度**：`lastContrib` 的来源改成"跨课按**完成时刻**选出的最新完成轮"（新增
      `completedIterTs`，复用 `readLedgerTail` 那条读法）；`nodeHealth` 与 `isSlowNode` **均不改**；
      **不引入任何新鲜度阈值常量**；
- [ ] **§3.4 单测**：① 两课各自的水位轮中，**完成时刻更晚**的那个被选中（造 `completedAt` 与 `it` 大小
      **相反**的数据）② `lastContrib` = 该节点在该轮 rollout+eval 成功局数 ③ `nodeHealth` 三档
      ④ 所有流都取不到 `iteration` 时间戳时的退化路径明确（按 mtime 兜底 + 记一行）；
- [ ] 单测：① 两门课的行合并到同一节点 ② 窗口边界（固定 `TZ`，含跨天）③ epoch 与窗口取交集
      ④ 投影纯函数（同一 `agg` 切不同 days）⑤ **逐流水位只滤掉"进行中那一轮"**（造一条 it > baseIt
      的行，断言不进桶，且**另一门课同一 it** 的行照常进）⑥ 预筛（mtime < epoch 的整份文件**不被 read**，
      用 spy）⑦ 大文件只读尾部（阈值线）；
- [ ] 门禁：`cd dashboard && bun run typecheck && bun run test` 绿 + 根 `bun run check` 绿
      （动过 `dashboard/src/web/**` ⇒ 走 `bun dashboard/src/server/build.ts` 三份 bundle）；
- [ ] 文档：`docs/nn/console.md` 新条目（解耦 + 天窗口 + "it 只作内部过滤器"）；
- [ ] `DECISIONS.md` 一条（含被否决项："全局 it 基准"当跨课对齐口径 —— §1.1）。

## 6. 已关闭项（原待议，2026-09-24 + 2026-09-26 评审）

| 项 | 裁决 | 落点 |
|---|---|---|
| `lastIter*` 是否从类型里彻底删 | **删** | §3.1 / §3.2 / §5 |
| 停摆课的水位怎么算 | **按完成时刻排序自然落选**；历史局数仍按天落桶 | §3.4 |
| 两套健康度是否统一 | **都留，但分列分名**：表格列 **「成功率」**，pill 表 **"产能"** | §3.2 / §3.3 |
| `poolStatus` 是否随窗口变 | **函数不动、输入随窗口**；表头写明"成功率随所选窗口变" | §3.2 |
| meta `ts` 是 UTC 吗 | **不是**：训练机本地、无时区后缀；禁止 `Date.UTC` | §4.2 |
| 预筛与"切天零重算"冲突 | **预筛钉在 epoch**（day-independent） | §4.3 |
| `snapshot-cache` 是否消费者 | **是**（第二个 `aggregateNodeHistory` 调用点），本轮一并改 | §3.1 / §5 |

**口径索引**（三个数各是什么，别再混）：

| 名字 | 口径 | 用途 |
|---|---|---|
| **产能**（`nodeHealth`） | 最新完成轮的贡献局数 vs 节点并发 | pill 状态点 |
| **成功率**（`poolStatus`） | **窗口内**最近 ≤10 次结算的完成率 | 表格状态列 |
| **窗口内局数**（`winRollout/winEval`） | 所选「天」窗口内的成功局数 | 节点表统计列 |
| **最新完成轮贡献**（`lastContrib`） | 跨课按完成时刻选出的那一轮、该节点成功局数 | pill 判据输入 |

## 7. 修订记录

* **2026-09-26（评审）**：修 S8 时区前提（UTC → 训练机本地）、逐流 `itByNode` 结构、`DayBucket`
  补齐窗口投影所需字段、预筛钉 epoch 以保住"切天零重算"、`poolStatus` 口径、点名
  `snapshot-cache.ts` 第二消费者、`?course=` 牵连面、S9–S12 新增现状。
