# Plan: dashboard-compare-trends — 首页趋势图「比较」弹窗（多课程同图对比）

> **交付物**：本 plan。实现交给后续 agent；未写进本文件的细节以代码与 `DECISIONS.md` 为准。
> **路径基准**：`dashboard/**` 相对 `dashboard/`；其余相对仓库根。
> **状态**：评审修订完成（v2，2026-10-08）。v1 的 10 处事实错误已修、8 处规格空洞已补 —— 逐条见 §9。
> **触发（用户 2026-10-08）**：dashboard 首页趋势图区域右上角加一个「比较」按键 → 弹窗里用**一张大图**
> 显示所有正在训练课程的胜率；弹窗支持 ①切图形选项（胜率/击杀/承伤/胜局耗时/胜局残血/道具 ×
> 全部/rollout/eval × 起止 it）②增删课程（含历史训练课程）③页面自动刷新时弹窗图也自动刷新。
> **阅读顺序**：§1 现状与卡点 → §2 目标/非目标 → **§3 语义规格（先定死）** → §4 落点 → §5 步骤与用例 →
> §6 诚实账 → §7 DoD/门禁 → §8 待裁决 → §9 评审修订记录。
> 行号只作定位加速，**判据看函数名**。

---

## 0. 一句话目标

**在首页趋势图控制条右上角开一个「比较课程」入口，弹窗里用一张共享 y 轴的大图叠加 N 门课程（默认=在训课）
的同一指标曲线，指标/口径/起止 iter 可切、课程可增删（含历史课）、弹窗打开期间按顶栏节奏自动刷新。**

---

## 1. 现状：数据面缺一个「跨课程」端点，图形组件只支持两条序列

### 1.1 已有的三块砖（都能直接用，不要重写）

| 砖 | 位置 | 现状 |
|---|---|---|
| 逐轮指标行 | `dashboard/src/server/iters.ts::readIterMetrics(trajDir)`（`iters.ts:1110`） | **返回 `{ rows: IterRow[] }`（不是裸数组）**；读 `tmp/<课>/training_log.jsonl`（+ `eval_log.jsonl` 汇总）。内部 `sort(iter 降序)` 后 `slice(0, MAX)`（`MAX = 500`，`iters.ts:1113`/`:1225`/`:1264`）⇒ **只保最新 500 轮、且是倒序**。逐轮 actuals 有 `.pool-actuals-cache.json`（门闩 = `time` 字段） |
| 指标序列抽取 | `dashboard/src/web/view/series.ts::metricSeries(rows)`（`series.ts:68`） | **纯函数、client-safe**（`view/` 禁 IO，服务端已在 import）⇒ 服务端可直接调用它。**共 19 条**：主 12（`winRate`/`scoreMean`/`kl`/`entropy`/`kills`/`pu`/`avgTicks`/`winTicks`/`winHp`/`dmgPerKill`/`lossTicks`）+ eval 7（`eval`/`evalTicks`/`evalKills`/`evalPu`/`evalWinTicks`/`evalWinHp`/`evalDmgPerKill`/`evalLossTicks`）—— 弹窗只用其中 12 条（6 档 × 主/eval） |
| 画图 | `dashboard/src/web/components/TrendChart.tsx` | 只支持 **主序列 + 一条 eval 叠加**；x 轴按**下标**映射（`px(i) = PAD_L + i/(n-1)*plotW`）⇒ 多课（iter 网格不同）**画不了**；`pathFrom(vals, n, px, py)` 是**模块私有函数**（`TrendChart.tsx:58`）⇒ W2 需 `export`（不要复制第二份） |
| 范围/口径档位 | `view/series.ts`（`TrendRange` = all/30/10、`TrendSource` = all/rollout/eval、`TREND_SOURCE_OPTIONS`、`isTrendSource`） | 现成；弹窗的 source 档位**复用同一份**（不新造第二套词） |
| 课程清单 | `ConsoleStateView.courses`（`api/courses.ts::discoverCourses(500, archivedSet)`，含 tmp 下有账本的**历史课**）、`trainingCourses`（= 开课标记，`api/state-view.ts::buildStateView` 计算）、`archived`（封存读面） | Hero 已拿到整个 `stateView` ⇒ 弹窗的课程/指标数据自足，`app.tsx` 只需透传一个 `refreshSec`（§4.2） |
| 顶栏刷新节奏 | `app.tsx:124`（`refreshInterval`，`REFRESH_INTERVALS = [60,180,300,600,1800]`、默认 300，`view/interaction.ts:122`）；`app.tsx:131` + `:236-243` 维护 `documentVisible`（visibilitychange） | 弹窗节奏**跟它**；可见性**弹窗自持**（Hero 不收这个 prop） |

### 1.2 三处卡点

| # | 卡点 | 证据 |
|---|---|---|
| ① | **没有跨课程指标端点**：`/api/state` 只算**当前查看课程**的 `metrics`（`api/state-view.ts::buildStateView`），一次一门课 | 要画 N 门课 ⇒ 必须新端点 |
| ② | `TrendChart` 是**两序列**组件且 x 轴按下标 | `TrendChart.tsx:124`；多课 iter 起点/步长不同（有的从 it30 resume，有的 it0 起）⇒ 按 iter **数值**建轴的组件缺位 |
| ③ | 首页趋势图区右上角**没有入口**，且 Hero 的刷新靠外层 `/api/state` 轮询（默认 300s，用户可调） | `Hero.tsx` 的 `tc-trendctl` 行（`:825` 附近）与 `head == null` 早退分支（`:798-819`）；`app.tsx:229-233`（`usePolling(refreshState)`）⇒ 弹窗要有**自己的**轮询，不能蹭 300s |

### 1.3 三条硬约束（别踩）

- **读面纪律**：封存课只有 `archive/courses/<课>/archive-manifest.json`（**不扫盘、不解压**；`api/archive.ts` 头注）⇒ 封存课**没有逐轮账本可比**（§3.5 / §8-①）。
- **样式纪律**：`tests/web-style-discipline.test.ts` 扫 `src/web/**/*.tsx`：①SVG 属性禁 camelCase（Preact 客户端 diff 原样 `setAttribute`，`TrendChart.tsx` 头注记了血案）；②除 `TrendChart.tsx` 的 3 处计算值外**不许 `style=`**（字号走 `--fs-*` 阶梯）⇒ 新图表组件必须照 `TrendChart.tsx` 的短横线写法、布局只走 class。
- **可测性纪律**：`tests/helpers/console-fixture.ts` 用 `core/paths.ts` 的惰性 env 重定向（`BCITY_TMP_LOGS_DIR` / `BCITY_CURRICULA_DIR` / `BCITY_RL_CONFIG` / `BCITY_CONSOLE_STATE`）⇒ **新端点读账本必须走 `tmpLogsDir()`**（写死 `REPO_ROOT/tmp` 的话单测无法造夹具、只能读真机器数据）。注意 `sanitizeViewCourse` 的第一支是**不可重定向**的 `REPO_ROOT/tmp`（`api/courses.ts`），第二支 `curriculaDir()` 可重定向 ⇒ 夹具课 = 「重定向 curricula 里声明一个 `.jsonc`」+「重定向 tmpLogs 里放账本」两件套。

---

## 2. 目标 / 非目标

**目标**
1. 首页趋势图控制条右上角一个「比较课程」按钮 → 弹窗（Esc / 遮罩关闭，与 `OpenCourseModal` 同结构）。
2. 一张大图叠加 N 门课（N ≤ 8）的**同一指标**，共享 y 轴与 iter 轴；图例 = 课程名 + 色。
3. 图形选项全在弹窗内：指标 6 档 × 口径 3 档（全部/rollout/eval）× 起止 it。
4. 课程可增删：候选 = `stateView.courses`（含历史课）+ 当前课程；已选 = chip + ×。
5. 弹窗打开期间自动刷新（自带轮询），且**页面 `/api/state` 刷新不影响弹窗选课**。

**非目标**（明确不做，防止膨胀）
- 不判定「哪门课更好」——不做跨课配对/显著性/排名（那是 `/eval` 与配对裁判的地盘）。
- 不把多课曲线塞进 Hero 的小图（Hero 六格保持单课）。
- 不改 `readIterMetrics` 口径、不改 `metricSeries` 的既有序列定义。
- 不画封存课（§8-① 拍板后才动）。
- 不做导出/分享链接（后续再说）。

---

## 3. 语义规格（先定死）

### 3.1 指标档位（6 项，顺序 = 用户给的顺序）

| 档位 | main key（`metricSeries`） | eval key | 格式化 | y 轴下界规则 |
|---|---|---|---|---|
| 胜率 | `winRate` | `eval` | 百分比（`fmtPct` 复用） | `min(dataMin, 0.3)`（沿用 Hero `yFloor=0.3`） |
| 击杀 | `kills` | `evalKills` | 百分比（歼灭率；缺敌数时是每局平均，形状可比、量级不可比 ⇒ 图例不改口径，title 注明） | `min(dataMin, 0)` |
| 承伤 | `dmgPerKill` | `evalDmgPerKill` | 百分比 | `min(dataMin, 0)` |
| 胜局耗时 | `winTicks` | `evalWinTicks` | 整数 | `max(0, min×0.8, max×0.5)`（沿用 `Hero.tsx::winTicksYMin` 的公式） |
| 胜局残血 | `winHp` | `evalWinHp` | 百分比 | `min(dataMin, 0)` |
| 道具 | `pu` | `evalPu` | 2 位小数 | `min(dataMin, 0)` |

⇒ **y 轴规则搬到 view 层纯函数 `compareYBounds(metric, seriesList)` 单一出处**（返回 `{ yFloor?, yMin? }`，语义与 `TrendChart` 的两个 prop 一一对应）；Hero 的 `winTicksYMin` 后续可改调它（本 plan 不改 Hero 既有行为）。

### 3.2 口径档位（3 项）

复用 `TREND_SOURCE_OPTIONS`：
- `all`：每课画两条（rollout **实线** + eval **同课色虚线**）——多课场景下不能再给 eval 上统一橙色（橙是「eval 口径色」，8 门课全橙 = 认不出课），改为**课色 + 虚线**表示 eval 口径；
- `rollout` / `eval`：每课一条（课色实线）。

### 3.3 起止 it
两个数字输入（`from` / `to`，空 = 全量），按 **iter 数值**过滤（`iter >= from && iter <= to`），与 Hero 的「最近 N」是**两套语义**，各自独立、互不影响。输入 400ms 防抖后才重拉；非法（`from > to`、负数、非整数、非数字）→ 就地提示「起止 it 不合法」并**沿用上一次合法值**，不发请求。

### 3.4 x 轴
`x = (iter - xMin) / (xMax - xMin)`，`xMin/xMax` = 当前可见课程的 iter 全域（跨课共享）⇒ 起点不同的课在图上左右错开，**这是事实不是缺陷**（resume 的课本就从 it50 起）。

### 3.5 课程增删
- **候选**：`stateView.courses`（`discoverCourses(500)`：tmp 下有账本的课 = 在训 + 历史）∪ 当前查看课程；去重。搜索框按子串过滤。
- **默认选中**：`stateView.trainingCourses`（开课标记）∪ 当前课程，去重后取前 6；一门都没有 ⇒ 取 `courses` 前 2。
- **上限 8 门**：超出时「+ 课程」按钮禁用并提示「最多 8 门（读账本成本随门数线性上涨）」。
- **移除**：每个 chip 一个 ×；至少保留 1 门（0 门 ⇒ 空态「至少选一门课程」）。
- **封存课**（`stateView.archived`）：候选列表里显示为灰行「封存 · 无逐轮账本」，**不可点**（§8-①）。
- **持久化**：localStorage（与 `TC_TREND_RANGE` 同处 `view/legacy-keys.ts`）：`TC_COMPARE_COURSES`（逗号分隔）、`TC_COMPARE_METRIC`、`TC_COMPARE_SOURCE`、`TC_COMPARE_FROM`、`TC_COMPARE_TO`；**首帧不读**（hydrate 纪律，见 §3.11）。

### 3.6 自动刷新（**跟首页全局刷新间隔**，用户 2026-10-08 裁决）
- 节奏 = 顶栏那个旋钮 ⇒ Hero 新增 prop `refreshSec`，`app.tsx` 把它已有的 `refreshInterval`（`:124`）传下来。**不新增第二个节奏开关**。
- 可见性：**弹窗自持**一份与 `app.tsx:131/236-243` 同款的 `documentVisible` state（Hero 没有这个 prop，也不要为它加 prop —— 视觉/可见性是本组件自己的运行事实）。
- `usePolling({ enabled: open && visible, intervalSec: refreshSec })`（`web/app/lib/usePolling.ts`：setTimeout 链式、天然防重叠；`enabled` 翻 true 时立即首拉）；关闭弹窗 ⇒ 停链。
- 选项变化（指标/口径/课程/起止）⇒ 立即重拉（effect 依赖变更，不靠等下一拍）。
- **fingerprint 门闩**：响应带 `fingerprint`（各课 `training_log.jsonl` + `eval_log.jsonl` 的 `mtimeMs:size` 聚合串）；客户端值未变 ⇒ **不 setState**（避免每拍重绘、hover 准星被清掉）。
- **请求竞态**：每次拉取带自增序号，过期响应直接丢（`from/to` 快切 + 轮询重叠时不许旧数据盖新数据）。

### 3.7 空态 / 错误 / 边界（都要看得见原因）
| 情形 | 表现 |
|---|---|
| 某课无账本 / 账本不可读 | 该课进 `unavailable[]`，图上不画，图例下方列「无数据：<课>」 |
| 某课该指标全 NaN（如无胜局 ⇒ 胜局耗时为空） | 该课在图例里标「无有效点」，不画 |
| 全部课程都无点 | 图区显示「该指标在所选课程上暂无有效点」 |
| 请求失败 | 图区上方 `InlineNotice` 显示错误，保留上一帧数据 |
| `courses` 参数为空 / 全非法 | 400 + `{ok:false,message}`（LAN 只读防线：逐个过 `sanitizeViewCourse`） |

### 3.8 数据契约（字段级；JSON 不能带 NaN）

```ts
// dashboard/src/web/view/compare-trends.ts（client-safe：零 IO、零 server import）
export const COMPARE_MAX_COURSES = 8
export type CompareMetric = 'winRate' | 'kills' | 'dmgPerKill' | 'winTicks' | 'winHp' | 'pu'
export interface CompareSeriesData { key: string; label: string; vals: Array<number | null>; iters: number[] }
export interface CompareCourseSeries { course: string; series: CompareSeriesData[]; points: number }
export interface CompareUnavailable { course: string; reason: string }
export interface CompareTrendsView {
  metric: CompareMetric
  source: TrendSource
  from: number | null
  to: number | null
  courses: CompareCourseSeries[]
  unavailable: CompareUnavailable[]
  fingerprint: string
  time: string
}
```

- **NaN 必须变成 `null` 过线**（`JSON.stringify(NaN) === 'null'` 是隐式行为，显式做）：客户端拿到 `null` 再还原成 `NaN` 参与断笔（`TrendChart.pathFrom` 同款语义）。
- `points` = 该课返回序列里 **有限值**的个数（客户端据此标「无有效点」）。
- `unavailable` 的 `reason` 用短中文（「无账本」/「账本不可读」），不做错误码体系（这是观测面）。

### 3.9 查询参数契约（`GET /api/compareTrends`）

| 参数 | 形态 | 非法时 |
|---|---|---|
| `courses` | `a,b,c`（逗号分隔，逐个 trim；去重；**顺序 = 色序 = 图例序**） | 逐个过 `sanitizeViewCourse`：**形态/存在性非法者剔除**；剔除后为空 → 400 `{ok:false,message:'courses 参数无有效课程'}` |
| `metric` | 6 档之一（`isCompareMetric`） | 400 `metric 非法` |
| `source` | `all`/`rollout`/`eval`（`isTrendSource`） | 400 `source 非法` |
| `from` / `to` | 空串 = null；否则非负整数 | 400 `起止 it 须为非负整数`；`from > to` → 400 |

- 门数 > `COMPARE_MAX_COURSES` ⇒ 取前 8（**不报错**：上限是 UI 约束，守成 400 会让手拼 URL 的人读不出原因）。
- 解析与校验是纯函数 `parseCompareQuery(raw)` → `{ok:true, params} | {ok:false, message}`（单测直接打它，不架 HTTP）。

### 3.10 缓存契约（端点内部）

- 课程级缓存 `Map<course, { fp, at, series: Map<key, Series> }>`：`series` = `metricSeries(rows)` 的 19 条（整表缓存，换指标不打盘）。
- **指纹** = `mtimeMs:size`（`training_log.jsonl` + `eval_log.jsonl`，缺失记 `-`）；**TTL 10s**；指纹不变且未过期 ⇒ 不读日志正文。
- 测试出口：`__clearCompareCache()` / `__compareCacheSize()`（导出理由与 `__clearXxx` 先例同：缓存是内部优化，断言要有把手）。
- 缓存只吃「读账本」这一层；请求级过滤（metric/source/from/to）永远现算。

### 3.11 组件几何 / hover / 色板 / 持久化

- **几何**：`MultiTrendChart` 宽 = 容器 100%（`viewBox="0 0 720 H"`，`width="100%"`，不动画）；PAD_L 46 / PAD_R 10 / PAD_T 10 / PAD_B 20；x 轴刻度取 `xMin/xMax` 两端 + 中点（整数 it）；y 轴 3 条网格线（`yFloor`/中/顶）。
- **退化**：全部点为空 ⇒ 空态文案（不画轴）；`xMax === xMin`（单轮）⇒ 该轮画在 50% 处（`xOf` 内部 `span = 0 ? 0.5 : norm`）。
- **hover**：`nearestIter(allIters, target)` 纯函数（合并全部课 iter，取最近；target 由鼠标 x 反算）→ tooltip（HTML，非 SVG）列出**每课在该 iter 的值**（缺 = `—`）+ iter 号；无 hover 时 tooltip 不渲染。索引须带上「最近 iter 的 x」给竖线用。
- **色板**：`COMPARE_COLORS`（8 个定值，冷/暖交替、与 `--accent` 蓝和 eval 橙可区分；**不新增 CSS 变量**——颜色是数据不是主题）+ `colorOf(i)` 取模循环；`COMPARE_COLORS.length >= COMPARE_MAX_COURSES` 由单测钉住。
- **持久化**：5 个键见 §3.5；**恢复在 mount 后的 effect**（`useState` 初始化一律走默认值，与 `Hero` 的 `TC_TREND_RANGE` 同款；`tests/web-ssr-readonly.test.ts` 的同款纪律）；恢复时与 `stateView.courses ∪ current` 求交（选过的课可能已封存/删除）；`from/to` 恢复后仍要过 `parseIterRange`。
- **弹窗结构**：`OpenCourseModal` 同款（`.tc-modal-mask` 点击关 / `.tc-modal` 内 `stopPropagation` / `if (!open) return null` / Esc 监听在 open 期间挂 `keydown`）。
- **关闭即停**：`usePolling` 的 `enabled=false`；不保留数据（下次打开重新首拉——账本是活的，缓存留给服务端）。

---

## 4. 落点（逐文件）

### 4.1 新增

| 文件 | 职责 |
|---|---|
| `dashboard/src/web/view/compare-trends.ts` | **纯函数 + 类型 + 常量**（client-safe，无 IO）：§3.8 的类型、`COMPARE_METRIC_OPTIONS`/`isCompareMetric`/`COMPARE_METRIC_SPECS`（key 映射 + fmt 档）、`compareYBounds`/`sliceIterRange`/`parseIterRange`/`parseCompareQuery`、`defaultCompareCourses`/`addCompareCourse`/`removeCompareCourse`、`visibleCompareSeries`、`COMPARE_COLORS`/`colorOf`、`nearestIter`、`compareFmt`。`export *` 进 `view/index.ts` |
| `dashboard/src/server/api/compare-trends.ts` | `parseCompareQuery`（复用 view 的纯函数）+ `buildCompareTrendsView(params)`：逐课 `sanitizeViewCourse` → `tmpLogsDir()/<课>` 存在性 → `readIterMetrics(trajDir).rows` → `metricSeries` → 按 metric/source 取键 → `sliceIterRange` → `null` 化；课程级缓存（§3.10）。**不进 `/api/state`** |
| `dashboard/src/web/components/MultiTrendChart.tsx` | N 条线的 SVG 大图：iter 数值 x 轴、共享 y、`COMPARE_COLORS` 取色、hover 十字准星 + tooltip、NaN 断笔；复用 `TrendChart` 导出的 `pathFrom` |
| `dashboard/src/web/app/panels/CompareTrendsModal.tsx` | 弹窗：头部（指标/口径 `SegmentedControl` + 起止 it 输入）· 课程 chip 区（× 移除 + 候选列表 + 搜索）· 图区（`MultiTrendChart` + 图例 + 空态/错误）· 底部（节奏说明 + 关闭）。内部 `usePolling` + 可见性 state + localStorage 恢复 |
| `dashboard/tests/web-compare-trends.test.ts` | 纯函数全量（档位表 / y 规则 / 切片 / 解析 / 选课助手）+ 组件 SSR（空态、多课图例与 path 数、unavailable 行、封存候选行不可点、≤8 禁用、`MultiTrendChart` 数值轴与断笔） |
| `dashboard/tests/server-api-compare-trends.test.ts` | 夹具两课（重定向 `BCITY_CURRICULA_DIR` 声明 + `BCITY_TMP_LOGS_DIR` 账本）⇒ 参数校验（非法课/非法 metric/source/from；空 courses → 400）、from/to 过滤、eval 缺值 = null、无账本课进 `unavailable`、缓存命中（`__compareCacheSize`）、指纹随文件而变 |

### 4.2 修改

| 文件 | 改动 |
|---|---|
| `dashboard/src/web/components/TrendChart.tsx` | ① `pathFrom` 加 `export`（单一出处）；② `:243` 过期注释「紫实线」→「橙虚线/橙线」 |
| `dashboard/src/web/app/panels/Hero.tsx` | ① `tc-trendctl`（`:825` 附近）右端加「比较课程」按钮（`.tc-trendctl__end`，`margin-left:auto`）；② `head == null` 早退分支（`:798-819`）同挂一个；③ 新增 prop `refreshSec`（缺省 300）透传弹窗；按钮 state + `<CompareTrendsModal>` 挂在 Hero 内 |
| `dashboard/src/web/app/app.tsx` | **只加一处 prop 传递**：`<Hero … refreshSec={refreshInterval} />`（`:561` 附近）。不搬动任何状态 |
| `dashboard/src/web/app/lib/api-client.ts` | 新增 `fetchCompareTrends(params)`（`GET /api/compareTrends?...`，与 `fetchEvalRounds` 同款写法） |
| `dashboard/src/server/api/index.ts` | `export * from './compare-trends'`（facade 纪律：调用方只 import 目录） |
| `dashboard/src/server/server.ts` | 路由 `GET /api/compareTrends`（插在 `/api/evalRounds` 那段附近，`:402` 上下），解析 → `parseCompareQuery` → 400 或 `json(buildCompareTrendsView(params))` |
| `dashboard/src/web/view/legacy-keys.ts` | 5 个 `TC_COMPARE_*` 键（与 `TC_TREND_RANGE` 同前缀） |
| `dashboard/src/web/theme.css` | `.tc-trendctl__end`、弹窗 chip/候选列表/图区样式（沿用既有 `tc-*` 命名；不新增设计语言） |

---

## 5. 步骤与用例

**W0 — 规格件（先写类型与纯函数，零 UI）**：`view/compare-trends.ts` 全量 + 5 个 localStorage 键。
用例：档位表 6 项顺序与 key 映射；`compareYBounds` 六档各自的下界（胜率 0.3 语义、胜局耗时 `max(0,mn*0.8,mx*0.5)`、无数据 = `{}`）；`sliceIterRange` 边界（空/单边/反区间/跨界）；`parseIterRange`（空串、负数、小数、`from>to`）；`isCompareMetric` 拒非法；`defaultCompareCourses`/`add`/`remove`（去重、上限、当前课并入）。

**W1 — 服务端端点**：`api/compare-trends.ts` + 路由 + `api-client.ts` + facade。
用例（`bun run test server-api-compare-trends`）：两门夹具课（iter 1..5 与 50..54）⇒ 同一响应里各带自己的 iter 网格；`from=2&to=4` 只回该区间；eval 缺值 = `null`（**不是 0**）；无账本课进 `unavailable`；`courses=../etc` 被剔后为空 → 400；metric/source/from 非法 → 400；第二次调用命中缓存（`__compareCacheSize()` = 1）；改文件后指纹变、数据变。

**W2 — 大图组件**：`MultiTrendChart.tsx`。
用例（SSR）：3 门课 ⇒ 3 条 `path` + 图例 3 项；iter 网格不同 ⇒ x 坐标按数值铺开（断言首位/末位 x 不同）；NaN 段不连笔（`d` 里段数）；`nearestIter` 纯函数（奇数/偶数点、取最近）。

**W3 — 弹窗**：`CompareTrendsModal.tsx` + Hero 按钮接线 + CSS。
用例（SSR + 纯函数级）：默认选课 = 在训 ∪ 当前课（≤6，无候选退 2）；加课到 8 门后按钮禁用；移除到 0 ⇒ 空态文案；封存课候选行不可点且注明原因；起止非法不发请求（`parseIterRange` 断言 + 弹窗渲染提示）。

**W4 — 刷新与持久化**：`usePolling` 接线 + fingerprint 门闩 + localStorage（**hydrate 后**恢复）。
用例：`fingerprint` 不变 ⇒ 不 setState（抽成纯函数 `shouldApplyCompareData(prev, next)` 断言）；首帧不读 localStorage（`useState` 初始化不含 `localStorage`，`web-ssr-readonly` 同款扫描断言写进新用例）；关闭弹窗 ⇒ 组件返回 null。

**W5 — 门禁**：见 §7。

---

## 6. 诚实账（风险与已知的不足）

1. **成本随门数线性涨**：每门课一次 `readIterMetrics`（全量读 `training_log.jsonl`，大课几十 MB）⇒ 靠 ①课程级缓存（10s TTL + 日志指纹）②上限 8 门 ③只在弹窗打开时轮询 三条一起兜。**冷开一次 8 门课仍可能是秒级**（在控制台事件循环上同步读），弹窗首帧必须有 loading 态（不做假数据）；实现时以真课日志量一次（生产 8 门）为准，超预算就下调上限或改异步读——这是**实现期测量项**，不是拍脑袋的数字。
2. **最多只看最新 500 轮**：`readIterMetrics` 的 `MAX=500` 截断（O4 前就在），超长腿的早期轮在跨课图上不可见 ⇒ 弹窗图注写明。
3. **跨课曲线不是严格可比**：不同课的语料/起始权重/iter 起点可能不同 ⇒ 图只负责「同框看趋势」，**不给结论**（§2 非目标）。图注要写一句来源说明。
4. **eval 点在跨课图上更稀**：`eval` 序列本来就稀疏，8 门课叠一起可能每门只有 3–5 个点 ⇒ 全部档位下 eval 用点+虚线，且 tooltip 里显式区分口径。
5. **封存课画不了**：读面纪律的直接后果（§1.3 / §8-①）。
6. **与 Hero 的既有档位会「看起来重复」**：Hero 的 `range`（最近 N）与弹窗的 `from/to` 是两套语义（§3.3）。不合并——合并会让「最近 10 轮」在多课下失去意义（每门课的 N 不一样）。
7. **默认 300s 时「自动刷新」很慢**：顶栏默认间隔 300s ⇒ 弹窗每 5 分钟才补一拍。补偿：打开弹窗、切选项、增删课程都**立即**重拉；想更快就把顶栏间隔调到 60s（同一个旋钮）。弹窗底部写一行小字（当前节奏来源），避免误判「自动刷新坏了」。

---

## 7. DoD 与门禁

- [ ] 首页趋势图控制条右上角有「比较课程」按钮；点击出弹窗；Esc / 点遮罩可关。
- [ ] 弹窗默认把**在训课程**同图叠一张（共享 y 轴、iter 数值 x 轴），胜率档。
- [ ] 6 个指标档、3 个口径档、起止 it 全部可切且**立即重拉**；非法起止不改图。
- [ ] 课程可增（含历史课，≤8）可删（chip ×）；封存课不可点并注明原因。
- [ ] 弹窗打开期间按**首页全局间隔**自动刷新（默认 300s）；fingerprint 未变不重绘；关闭即停；打开/切选项即时重拉。
- [ ] 无数据 / 无有效点 / 请求失败 三种空态都有可见文案。
- [ ] 新增测试全绿；既有 `web-hero-trend-source` / `web-ssr-*` / `web-style-discipline` 未回归。

```bash
cd dashboard && bun run typecheck
cd dashboard && bun run test web-compare-trends server-api-compare-trends   # 启动器会透传子串过滤
cd dashboard && bun run test            # 全量（动过 dashboard/**）
bun run check                            # 根项目
bun dashboard/src/server/build.ts        # 动过 src/web/** ⇒ 三份 bundle
```

（`bun run test` = `scripts/run-tests.ts`，它把参数透传给 `bun test` 并代传 `--parallel=<物理核数> --timeout=50000`——不要直接写裸 `bun test <路径>`。）

---

## 8. 已裁决（用户 2026-10-08）

1. **封存课不可画** —— 候选列表灰显「封存 · 无逐轮账本」（守 `server/api/archive.ts` 的「只读 manifest、不扫盘不解压」纪律）。
2. **默认选课 = 在训课 ∪ 当前查看课，去重取前 6**（不选「记住上次选择」⇒ 少一个 localStorage 键，也少一类失效处理）。
3. **刷新节奏 = 跟首页全局刷新间隔**（60/180/300/600/1800；默认 300）⇒ Hero 加 `refreshSec` prop，不新增节奏开关；
   默认档下 5 分钟一拍的代价已写进 §6-7（打开/切选项仍即时重拉）。

---

## 9. 评审修订记录（v1 → v2，2026-10-08）

**A 组：事实错误（10 处，全部已按代码核实）**

| # | v1 说法 | 事实（证据） | v2 处置 |
|---|---|---|---|
| A1 | `readIterMetrics` 返回 `IterRow[]` | 返回 **`{ rows: IterRow[] }`**，且 `sort(iter 降序)` 后 `slice(0, MAX=500)`（`iters.ts:1110/1113/1225/1264`） | §1.1 改契约；§6-2 记「最多最新 500 轮」 |
| A2 | `metricSeries` 有 18 条序列 | **19 条**（主 12 + eval 7，含 `lossTicks`/`evalLossTicks`） | §1.1 列全 |
| A3 | W2「沿用 `pathFrom` 思路」 | `pathFrom` 是 `TrendChart.tsx:58` **模块私有** | §4.2 改「加 `export` 复用」，不复制 |
| A4 | 弹窗用 `documentVisible`（未说来源） | 它是 `app.tsx:131` 的 state + `:236-243` 的 visibilitychange；Hero 不收 | §3.6/§4.1 改「弹窗自持」，app.tsx 只透 `refreshSec` |
| A5 | 门禁写 `bun test tests/...`（裸） | 项目口径是 `bun run test`（`scripts/run-tests.ts` 代传 `--parallel/--timeout` 并透传参数） | §7 与 §5 全部换成启动器写法 |
| A6 | 端点路径写作 `src/server/api/state-view.ts` 等简写行号 | `api/state-view.ts` 的行号会漂；课程清单真源是 `api/courses.ts::discoverCourses` | §1.1 改按函数名，不再钉行号 |
| A7 | 测试先例只提 `web-ssr-*` | 更贴切的是 `tests/web-hero-trend-source.test.ts`（TrendCell 三档形状的 SSR 断言写法） | §5/§7 引用它做写法基准 |
| A8 | 仓根放 `*.plan.md` | 约定是 `plan/*.plan.md`（仓根唯一遗留已移入） | 本文件路径 = `plan/dashboard-compare-trends.plan.md` |
| A9 | 未提 `COLOR_EVAL` 的真值 | `COLOR_EVAL = 'var(--eval-line, #ea580c)'`（橙）；`TrendChart.tsx:243` 注释写「紫实线」已过期 | §3.2 按橙写；§4.2 顺手改注释 |
| A10 | `stateView.course` 当 `string` 用 | 类型是 `string`，但 `stateView` 本身可空、`activeCourse` 是可选字段 | §3.5 默认选课取 `stateView?.course ?? ''`，恢复时与 `courses` 求交 |

**B 组：规格空洞（8 处，已补成 §3.8–§3.11）**

| # | 空洞 | 补在哪 |
|---|---|---|
| B1 | 响应字段与 NaN 过线规则未定 | §3.8（`vals: Array<number|null>` + `points` + `unavailable` 形状） |
| B2 | 查询参数形态与非法处置未定 | §3.9（逗号分隔、去重、剔非法、400 文案、超 8 取前 8） |
| B3 | x/y 几何与单点/空数据退化未定 | §3.11（PAD、刻度、`xMax==xMin` 画中点、全空不画轴） |
| B4 | hover 语义（跨课对齐）未定 | §3.11（`nearestIter` + 每课值的 tooltip） |
| B5 | 8 色色板的出处与顺序语义未定 | §3.11（`COMPARE_COLORS` + `colorOf` + 顺序=选择序） |
| B6 | 服务端缓存粒度/指纹/测试出口未定 | §3.10（整表缓存 + TTL 10s + 指纹 + `__clear/__size`） |
| B7 | localStorage 五键的恢复纪律未定 | §3.11（hydrate 后恢复、与 courses 求交、非法值丢弃） |
| B8 | 请求竞态与门闩的判定函数未定 | §3.6 + §5-W4（序号 latch、`shouldApplyCompareData` 纯函数） |

**C 组：代码侧顺带发现（不在本任务范围，只记录）**

- C1 `TrendChart.tsx:243` 注释「紫实线」与真值（橙）不符 ⇒ 本计划顺手修正（§4.2）。
- C2 `api/eval-games.ts` 等旧端点写死 `REPO_ROOT/tmp`（不随 `BCITY_TMP_LOGS_DIR` 重定向）⇒ 本计划的新端点**不重复**这个坑（§1.3）；旧端点是否迁移另开任务。
