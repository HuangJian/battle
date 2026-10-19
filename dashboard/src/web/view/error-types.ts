/** error-types.ts — 组件错误登记表的**契约**（服务端产出 / 客户端消费）。
 *
 *  ★ 本文件在**客户端 bundle 里**（`web/view/**` 的目录纪律：只允许类型与纯函数，无 IO、
 *  无 `node:` 模块、无 Bun 全局 —— 见 `index.ts` 头注与 `tests/architecture-layering.test.ts`）。
 *  **指纹计算不在这里**：它要 `node:crypto` ⇒ 住服务端 api 层的 `error-fingerprint.ts`
 *  （plan/component-error-registry §4.1 P0-1；同类事故见 docs/nn/console.md §1443）。
 *  ⇒ 头注里**别写出那个路径字面**：本目录的禁词闸扫的是**文本**（注释也照扫，2026-10-16 实测
 *  红过一次）——用「服务端 api 层」指代即可。
 *
 *  为什么要有这张表（plan §2 R1）：错误今天散在五处（本机组件日志尾 / hub 的 `fail.json` /
 *  账本 `job_failed` / 节点 `lastError` / 退出看护），**没有任何一处能回答「现在有哪些错、
 *  同类出现几次、谁还没处理」**。`ErrorEvent` 就是那个「一处」的形状。
 */

/** 错误从哪条腿进来的（与采集四条腿一一对应；客户端的「来源分布」文案用它）。 */
export type ErrorSource = 'local-log' | 'hub-job' | 'worker-report' | 'node'

/** 严重度：只两档。`err` = 真错误；`warn` = 值得看一眼（机器级停滞、瞬时失败、上报腿）。 */
export type ErrorSeverity = 'err' | 'warn'

/** 错误分类（与 severity 正交）：决定默认视图是否折叠、处置提示、告警坞是否打断。
 *
 *  ★ 为什么没有 `misreport`（评审 F6）：误报在**采集层**就被过滤了（Phase 0），
 *  分类器永远看不到它们；留一个没有写者的枚举只会让人以为「历史行会被重新分类」，
 *  而账本重放根本不读 classification。 */
export type ErrorClassification =
  | 'transient-network'
  | 'process-lifecycle'
  | 'dependency'
  | 'capacity'
  | 'remote-failure'
  | 'multi-process'
  | 'unknown'

/** 事件活动度：`historical` = 源文件已静默 > 24h（历史课/停机前的旧日志）。
 *
 *  为什么必须有（评审 F2）：去掉开课闸后，185 门历史课的尾窗旧错误会被逐行采集，
 *  而 `collectLocalLogEvents` 原本给每一行盖 `now` ⇒ 旧错误全变「此刻活跃」，
 *  首页告警坞按 `firstAt` 反复重弹。`historical` 让「可见」与「活跃」分开。 */
export type ErrorActivity = 'active' | 'historical'

/** 详情页行上的跳转锚点：`log` → `/log/<component>`；`job` → 课程矩阵该课的行；`node` → `/nodes`。 */
export interface ErrorRef {
  kind: 'log' | 'job' | 'node'
  target: string
}

export interface ErrorEvent {
  /** 指纹（稳定标识）：渲染 key、去重身份、ack 身份都用它。**不含 `scope`**（见下）。 */
  fp: string
  source: ErrorSource
  /** 组件键（`trainingLoop`/`hubServer`/`cloudflared`/`localWorker`/`selfNode`）或腿名
   *  （`hub-job`/`ppo-worker`/`offline-worker`/`node`）。 */
  component: string
  /** 课程名 / job_id / node id；无则 `''`。**取最近一次命中的那一个**（同一错误跨课折叠）。 */
  scope: string
  severity: ErrorSeverity
  /** **本 episode** 首次出现时刻（内存值、可重置 —— ack 身份的一部分，见 §4.7）。 */
  firstAt: number
  /** 最近一次出现时刻。 */
  lastAt: number
  /** episode 内累计次数（窗口内读数取 max，拍间不累加 —— 见 error-registry 的注释）。 */
  count: number
  /** 分类（采集时由服务端 api 层的规则表判定；规则不变量见 plan §3.2）。 */
  classification: ErrorClassification
  /** 活动度（按源文件 mtime；见 `ErrorActivity`）。默认视图只显示 `active`。 */
  activity: ErrorActivity
  /** 自动撤销时刻（只出现在 `resolvedItems` 投影里；活跃事件恒为 undefined）。 */
  resolvedAt?: number
  /** 一行摘要（≤ `ERROR_MESSAGE_MAX` 字符）。 */
  message: string
  /** traceback / 上下文（≤ `ERROR_DETAIL_MAX` 字符；本机组件腿 = 命中行所在的**整块**）。 */
  detail: string
  ref: ErrorRef
}

/** 首页汇总条的轻量投影（`/api/state` 每 3s 一拍的载荷里**不放 detail**）。 */
export type ErrorListItem = Pick<
  ErrorEvent,
  | 'fp'
  | 'source'
  | 'component'
  | 'scope'
  | 'severity'
  | 'classification'
  | 'activity'
  | 'resolvedAt'
  | 'firstAt'
  | 'lastAt'
  | 'count'
  | 'message'
  // `ref` 进投影：`/errors` 页的跳转锚点要它，而它只是两个短字符串（不是长文本）。
  | 'ref'
>

export interface ErrorSummaryView {
  items: ErrorListItem[]
  /** 投影被截断（`items` 超过 `ERROR_SUMMARY_MAX`）⇒ UI 提示「还有更多，去 /errors」。 */
  truncated: boolean
}

/** 详情页数据面（`/api/errors`）：`events` 含 detail；`cursor` = 本次列表的时刻（增量读法）。
 *
 *  `resolvedItems` = 最近自动撤销的瞬时错误（内存环，重启清空；账本另有审计行）。 */
export interface ErrorPayload {
  events: ErrorEvent[]
  summary: ErrorSummaryView
  resolvedItems: ErrorListItem[]
  cursor: number
}

/** 云机上报的**线格式**（`POST /admin/worker-error` 的体；`remote/error_report.py` 是唯一写方）。
 *
 *  刻意**不**直接收成 `ErrorEvent`：上报体来自远端机器，字段不可信（缺归属字段的服务端会 400，
 *  但类型上仍要能表达「可能缺」）——解析集中在 `error-registry.ts::workerReportEvent()`，
 *  与 `parseDiskFacts` 同案（「不全就交回 null，不编事实」）。 */
export interface WorkerErrorReport {
  worker: string
  component: string
  course?: string
  job_id?: string
  severity?: string
  reason: string
  detail?: string
  ts?: number
}

/** `/errors` 页的 SSR 载荷与选项（与 `/log` 同规：独立页各自 bundle + 首帧服务端渲染）。
 *
 *  ★ 载荷里**只有列表投影**（无 detail）：detail 按 `fp` 懒取（`GET /api/errors?fp=`，见 §4.6 的
 *  P1-4 裁决）—— 一页几十条 × 4000 字符的长文本不该每 4s 全量搬一遍。 */
export interface ErrorsPagePayload {
  items: ErrorListItem[]
  /** 最近自动撤销的瞬时错误（缺省 = 旧载荷/没有恢复记录）。 */
  resolvedItems?: ErrorListItem[]
  /** 登记表上一次巡拍的落定时刻（0 = 还没跑过 ⇒ 页上不谎报「已检查」）。 */
  scannedAt: number
}

export interface ErrorsPageOptions {
  /** 只读视图（非环回地址访问）——**只读的边界是动作，不是读面**：本页照常渲染与 ack
   *  （ack 只写本地 localStorage，与 `/log`、`/api/log` 的只读口径一致）。 */
  readOnly: boolean
  scannedAt: number
}

/** `GET /api/errors?fp=<fp>` 的响应：单条的 traceback/上下文。 */
export interface ErrorDetailPayload {
  fp: string
  component: string
  message: string
  detail: string
  ref: ErrorRef
  classification: ErrorClassification
  activity: ErrorActivity
}

/** 投影条数上限：首页汇总条只报数，逐条在 `/errors`（上限不是渲染约束，是「别把 3s 载荷撑大」）。 */
export const ERROR_SUMMARY_MAX = 100

/** 单条 detail 的硬上限（与 `hub/http_face.py::_clip`、`hub/result.py` 的 4000 同量）。 */
export const ERROR_DETAIL_MAX = 4000

/** 单条 message 的硬上限（一行摘要）。 */
export const ERROR_MESSAGE_MAX = 200

/** 严重度序（排序用；`err` 在前）。与告警坞的 `ALERT_SEVERITY_RANK` **不是**一份东西：
 *  那里还有 info/history 两档，这里只有「真错误 / 值得看一眼」。 */
export const ERROR_SEVERITY_RANK: Record<ErrorSeverity, number> = { err: 0, warn: 1 }
