/** view.ts — 训练控制台共享视图层（客户端安全：禁 import node: / fs / Bun（服务端 API） /
 *  console 的 api|actions 层）。
 *
 *  单一事实源：视图类型（ConsoleStateView / PoolView / NodeHistory / IterRow …）与
 *  全部纯函数（sparkPoints / fmtTs / fmtPct / sortRows / filterGroups / shouldFollow …）。
 *  服务端（api.ts / iters.ts / pool-history.ts）`import type` 本文件；客户端组件直接 import。
 *  方向约束：console/ui/** 可 import ui/**，反向禁止（tests 有禁词断言）。
 */

// ────────────────────────── 组件 / 节点 / 模式视图类型 ──────────────────────────

/** 卡数据源陈旧度（颜色+形状双编码：ok=绿圆 / refresh=黄半圆 / err=红方）。 */
export type StaleState = 'ok' | 'refresh' | 'err'

export interface ComponentView {
  key: string
  label: string
  /** running = 进程存活；stopped = 无存活进程；exited = 登记仍在但进程已死。 */
  status: 'running' | 'stopped' | 'exited'
  pid: number | null
  url: string | null
  course: string | null
  mode: string | null
  healthy: boolean | null
  /** 日志文件相对 nn-training/ 的路径。 */
  log: string | null
  logTail: string[]
  busy: boolean
  /** 需要展示的密钥型字段（仅 cloudflared：rl.remote_token，供用户复制贴给远端）。
   *  局域网只读与回环同权展示——只读是动作边界，不是数据边界（2026-09-09 用户指令）。 */
  secret?: string
  /** 非正常退出原因（§380 消费；服务端填充由该条目实施方完成）。 */
  error?: string | null
}

export interface NodeView {
  id: string
  url: string
  gpuPush: boolean
  enabled: boolean
  concurrency: number
  /** /v1/ping 实时探测；null = 未探测（disabled 时跳过）。 */
  online: boolean | null
  codeHash: string | null
  cpus: number | null
  busy: boolean
  /** 上一轮贡献数（全局最新轮下该节点成功局数；-1 = 无池数据）。 */
  lastContrib: number
}

export interface NodeLocalView {
  id: 'local'
  /** 本机直跑槽数（rl.local_slots）；显式 0 = 直跑未启用（仍出芯片，slots=0）；
   *  配置缺失/非法（NaN）时整个 localNode 缺省不出。 */
  slots: number
  /** 上一轮贡献数（与节点同口径：全局最新轮下 local 成功局数；-1 = 无池数据）。 */
  lastContrib: number
}

/** URL 展示整形（§361①）：协议 + 域名 + 尾 4 位。cloudflared 隧道域名过长，
 *  卡片内溢出换行——截断展示 + CopyButton 复制全量（title 仍给完整 URL）。 */
export function shortUrl(url: string): string {
  const m = url.match(/^(https?:\/\/[^/]+)/)
  if (!m) return url
  const host = m[1]!
  return url.length <= host.length + 4 ? url : `${host}…${url.slice(-4)}`
}

export interface ModeView {
  trainerPpo: 'pull' | 'push' | 'local'
  stream: number
  doubleBuffer: number
  precollectEarly: number
}

export interface MetricsView {
  available: boolean
  iters: IterRow[]
  error?: string
}

export interface ConsoleStateView {
  time: string
  course: string
  courses: string[]
  components: ComponentView[]
  nodes: NodeView[]
  /** 本机直跑节点（§361⑤：pill 行只读展示；无池/无槽位时缺省）。 */
  localNode?: NodeLocalView | null
  modes: ModeView
  metrics: MetricsView
  /** 当前训练阶段（顶栏图标用）。 */
  phase: PhaseInfo
  /** 局域网只读视图（服务端按请求来源 stamp；true = 本页只读——动作按钮禁用 + 只读角标）。
   *  缺省（SSR/测试直构）时客户端回退 location.hostname 判定。 */
  readOnly?: boolean
}

// ────────────────────────── 日志视图类型 ──────────────────────────

export interface LogPayload {
  component: string
  label: string
  log: string | null
  exists: boolean
  fileSize: number
  lines: string[]
  truncated: boolean
  /** 服务端取数时刻（页面「更新于」指示；§371 优化 2 可感知刷新）。 */
  updatedAt?: number
  /** 文件总行数（顶部「共 N 行」；服务端 >8MB 时为 null，UI 退化显示窗口行数）。 */
  totalLines?: number | null
}

export interface LogPageOptions {
  components: Array<{ key: string; label: string; status: string }>
  follow: boolean
  /** 尾行数；'all' = 读全部（§371 优化 1）。 */
  lines: number | 'all'
  /** 视图课程（?course= 只读覆盖；空 = 自动/操作员课程）。日志页轮询与组件导航透传。 */
  course?: string
}

// ────────────────────────── 日志页展示层纯函数（§367：直观/审美/交互） ──────────────────────────

export type LogLevel = 'error' | 'warn' | 'info'

export interface ParsedLogLine {
  /** [HH:MM:SS] 时间戳前缀；无则 null。 */
  ts: string | null
  /** [组件] 标签；无则 null。 */
  tag: string | null
  level: LogLevel
  /** 去前缀后的正文。 */
  text: string
}

const LOG_TIME_RE = /^\[(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*(?:\[([^\]]+)\]\s*)?/
const LOG_ERR_RE = /(error|fail|panic|fatal|exception|✗|❌|超时未完成|失败)/i
const LOG_WARN_RE = /(warn|warning|⚠|timeout|retry|重试|降级)/i

/** 切分日志行：[HH:MM:SS] 时间戳 + [组件] 标签 + 正文；按关键词判级别。训练 JSON 行不走此解析。 */
export function parseLogLine(line: string): ParsedLogLine {
  const m = LOG_TIME_RE.exec(line)
  const rest = m ? line.slice(m[0].length) : line
  let level: LogLevel = 'info'
  if (LOG_ERR_RE.test(rest)) level = 'error'
  else if (LOG_WARN_RE.test(rest)) level = 'warn'
  return { ts: m?.[1] ?? null, tag: m?.[2] ?? null, level, text: rest }
}

/** trainingLoop 的 JSON 日志行 → 结构化事件（event 徽章 + 精选字段）；非 JSON 返回 null。 */
export interface TrainingLogEvent {
  event: string
  /** 友好化 key:value（it/win/kl/job/time 等）。 */
  fields: Array<[string, string]>
  /** error 字段原文（iter_error 等；渲染为红色）。 */
  hasError?: string
}

export function parseTrainingEvent(line: string): TrainingLogEvent | null {
  if (!/^\s*\{/.test(line)) return null
  try {
    const r = JSON.parse(line) as Record<string, unknown>
    if (typeof r.event !== 'string') return null
    const pick = (keys: string[]): string | null => {
      for (const k of keys) {
        const v = r[k]
        if (v !== undefined && v !== null && typeof v !== 'object') return String(v)
      }
      return null
    }
    const fields: Array<[string, string]> = []
    const it = pick(['it', 'iter'])
    if (it != null) fields.push(['it', it])
    const win = pick(['winRate'])
    if (win != null) fields.push(['win', `${(Number(win) * 100).toFixed(1)}%`])
    for (const k of ['kl', 'mean_ret', 'entropy'] as const) {
      const v = r[k]
      if (typeof v === 'number' && Number.isFinite(v)) fields.push([k, v.toFixed(4)])
    }
    const job = pick(['job_id', 'runId'])
    if (job != null) fields.push(['job', job.length > 8 ? job.slice(0, 8) : job])
    const t = pick(['time'])
    if (t != null) fields.push(['time', t])
    const err = pick(['error'])
    return { event: r.event, hasError: err ?? undefined, fields }
  } catch {
    return null
  }
}

/** 文件体积人性化（日志页元信息）。 */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

// ────────────────────────── 训练指标类型（iters.ts 数据层的契约） ──────────────────────────

export interface IterActuals {
  games: number
  totalKills: number
  totalPU: number
  avgTicks: number
}

export interface EvalSummary {
  time: string
  games: number
  wins: number
  winRate: number | null
  clears: number
  clearRate: number | null
  dropped: number
  sec: number
  wver: string
  outcomes: Record<string, number>
  avgTicks: number | null
  totalKills: number | null
  totalPU: number | null
  scoreMean: number | null
  scoreStd: number | null
}

export interface IterRow {
  iter: number
  time: string
  winRate: number
  scoreMean: number
  scoreStd: number
  samples: number
  rolloutSec: number
  ppoSec: number
  kl: number
  entropy: number
  policyLoss: number
  valueLoss: number
  meanRet: number
  lr: number
  expectedGames: number
  halted: boolean
  topDims: string
  avgTicks: number
  accuracy: number
  loot: number
  kills: number
  actuals: IterActuals | null
  evalData: EvalSummary | null
}

// ────────────────────────── /api/pool 视图类型 ──────────────────────────

export type NodePoolStatus = 'healthy' | 'warn' | 'bad' | 'noping' | 'nodata' | 'disabled'

export interface NodeHistoryRow {
  id: string
  /** node = rl-config 节点；local = 本机直跑槽位。 */
  kind: 'node' | 'local'
  status: NodePoolStatus
  okN: number
  recentN: number
  /** 展示用规格：'8 核' / '2 槽' / '-'. */
  spec: string
  /** 版本是否与本机一致；null = 不可比（local/未起）。 */
  versionOk: boolean | null
  /** 短版本号（前 7 位）。 */
  version: string
  /** 训练机侧短 hash（前 7 位）——F5（plan/dist-codehash-stale-fix.md）：stale 诊断
   *  Pill 展示两侧 hash，一眼看出差异在哪一侧。 */
  versionLocal: string
  pingMs: number | null
  ok: number
  fail: number
  /** 上轮贡献合计（rollout + eval）。 */
  contrib: number
  /** F5（plan/dist-codehash-stale-fix.md）：上轮贡献按 mode 分桶——"只跑 eval 的
   * 节点"不再看起来在贡献 rollout。 */
  contribRollout: number
  contribEval: number
  lastIter: number
  globalMaxIt: number
  avgElapsedSec: number | null
  lastOkTs: string
  lastFailTs: string
  /** 已剥离 agent ISO 前缀（GLM-U3）。 */
  lastError: string
  recent: boolean[]
}

export interface ActiveFlowInfo {
  dir: string
  mtimeMs: number
  lines: number
}

export interface SelfStatus {
  workers: number
  inflight: number
  gamesDoneTotal: number
  diskFreeMB: number | null
  lastError: string | null
  uptimeSec: number | null
  resultCacheItems: number
  resultCacheBytes: number
  recentFailed: number
}

export interface PoolView {
  /** 服务端结果缓存的构建时刻（ms）。 */
  cachedAt: number
  course: string
  epochMs: number
  activeFlow: ActiveFlowInfo | null
  nodes: NodeHistoryRow[]
  local: NodeHistoryRow | null
  selfStatus: SelfStatus | null
  localHash: string
}

// ────────────────────────── EvalBoard 视图类型（plan/rl-eval-system.md §8；实现 → console/evalboard.ts） ──────────────────────────

export interface EvalGateView {
  main: boolean
  cost: boolean
  style: boolean
  credible: boolean
  pass: boolean
}

export interface EvalLadderRow {
  rung: string
  dimension: string
  lives: number
  god: { winRate: number | null; lifePrice: number | null; n: number; provisional: boolean | null }
  batches: number
  n: number
  latestWin: number | null
  windowWin: number | null
  deltaVsGod: number | null
  gate: EvalGateView | null
  partial: boolean
}

export interface EvalBatchRow {
  batch_id: string
  course: string
  rung_from: string
  status: string
  iter: number
  trigger: string
  units: { of: number; done: number[] }
  elapsed_sec: number | null
  created_ts: string
}

export interface EvalAlert {
  id: string
  severity: 'red' | 'yellow' | 'note' | 'star'
  message: string
  blocksVerdict?: boolean
  rejectBatch?: boolean
}

export interface EvalBoardView {
  cachedAt: number
  course: string
  ingested: number
  evalEvery: number | null
  abWarn: string | null
  ladder: EvalLadderRow[]
  alerts: EvalAlert[]
  batches: EvalBatchRow[]
  flips: Array<{
    rung: string
    from: string
    to: string
    delta: number
    paired: boolean
    flips: number | null
    rate: number | null
  }>
  rows: number
  spaceCalibrated: boolean
}

// ────────────────────────── 纯函数：时间 ──────────────────────────

const p = (x: number): string => String(x).padStart(2, '0')

/** 完整本机时区时间 'YYYY-MM-DD HH:MM:SS'（空格格式；历史锚点/字符串比较用）。 */
export function fmtFullTs(ms: number): string {
  const d = new Date(ms)
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function fmtTs(ms: number, now = Date.now()): string {
  const d = new Date(ms)
  const sameDay =
    d.getFullYear() === new Date(now).getFullYear() &&
    d.getMonth() === new Date(now).getMonth() &&
    d.getDate() === new Date(now).getDate()
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  if (sameDay) return hm
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}`
}

/** 完整 ISO（hover 详情用）。 */
export function fullIso(ms: number): string {
  return new Date(ms).toISOString()
}

/** 相对时间 'Xs 前' / '刚刚'。 */
export function fmtRel(ms: number, now = Date.now()): string {
  const diff = Math.max(0, Math.round((now - ms) / 1000))
  if (diff < 5) return '刚刚'
  if (diff < 60) return `${diff}s 前`
  if (diff < 3600) return `${Math.round(diff / 60)}m 前`
  return `${Math.round(diff / 3600)}h 前`
}

// ────────────────────────── 纯函数：数值/文本 ──────────────────────────

export function fmtPct(v: number | null | undefined): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—'
}

export function fmtBytes(b: number | null | undefined): string {
  if (typeof b !== 'number' || !Number.isFinite(b)) return '—'
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}

/** 剥离文本开头的 ISO 时间戳前缀（sampler-agent lastError 的 `new Date().toISOString()` 前缀）。 */
export function stripIsoPrefix(text: string): string {
  return text.replace(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?\s*/, '')
}

// ────────────────────────── 纯函数：sparkline ──────────────────────────

export const SPARK_W = 110
export const SPARK_H = 26

export interface SparkPoints {
  coords: string
  color: string
  lastX: number
  lastY: number
}

/** 归一化数据点（非有限值跳过；恒定序列满幅平线灰色）。 */
export function sparkPoints(
  values: number[],
  width = SPARK_W,
  height = SPARK_H,
): SparkPoints | null {
  const pts = values.filter((v) => Number.isFinite(v))
  if (pts.length === 0) return null
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const span = max - min
  const px = (i: number): number => +((i / (pts.length - 1)) * (width - 4) + 2).toFixed(1)
  const py = (v: number): number =>
    span === 0
      ? +(height / 2).toFixed(1)
      : +(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)
  const coords = pts.map((v, i) => `${px(i)},${py(v)}`).join(' ')
  return {
    coords,
    color: span === 0 ? '#94a3b8' : '#2f5fe0',
    lastX: px(pts.length - 1),
    lastY: py(pts[pts.length - 1]!),
  }
}

/** SSR/首屏用 SVG 字符串（客户端 <Sparkline> 组件与它同源，逐字节一致）。 */
export function sparkline(values: number[], width = SPARK_W, height = SPARK_H): string {
  const sp = sparkPoints(values, width, height)
  if (!sp) return '<span class="muted small">—</span>'
  return `<svg class="spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
<polyline points="${sp.coords}" fill="none" stroke="${sp.color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
<circle cx="${sp.lastX}" cy="${sp.lastY}" r="2.2" fill="${sp.color}"/>
</svg>`
}

// ────────────────────────── 纯函数：指标系列 ──────────────────────────

export interface Series {
  key: string
  label: string
  vals: number[]
  /** 与 vals 逐位对齐的迭代序号（时间正序）。 */
  iters: number[]
}

/** 走势图范围档位：全量 / 最近 30 轮 / 最近 10 轮。 */
export type TrendRange = 'all' | '30' | '10'

/**
 * 按范围档位截取序列。eval 走势在有限轮里常带 NaN 缺口，
 * 其「最近 N」语义 = 最近 N 个**有效**评估点（而非最近 N 轮迭代），
 * 避免窗口内全是 NaN 画空图；「全量」档同样只保留有效点。
 * 其它指标 = 最近 N 轮迭代（按 iter 截取）。
 */
export function sliceSeries(series: Series, range: TrendRange): Series {
  const { key, label, vals, iters } = series
  if (vals.length === 0) return { key, label, vals, iters }
  // eval：先过滤到有效评估点，再按档位截取。
  if (key === 'eval') {
    const pairs = vals.map((v, i) => ({ v, it: iters[i] })).filter((p) => Number.isFinite(p.v))
    const sliced = range === 'all' ? pairs : pairs.slice(-Number(range))
    return { key, label, vals: sliced.map((p) => p.v), iters: sliced.map((p) => p.it) }
  }
  if (range === 'all') return { key, label, vals, iters }
  return { key, label, vals: vals.slice(-Number(range)), iters: iters.slice(-Number(range)) }
}

/** 指标表列集（时间正序；actuals/eval 缺轮断点 = NaN 会被 sparkPoints 过滤）。
 *  返回全量时序（不做 20 轮截断），由 sliceSeries / TrendChart 按范围档位截取。 */
export function metricSeries(rows: IterRow[]): Series[] {
  const chrono = [...rows].sort((a, b) => a.iter - b.iter)
  const iters = chrono.map((r) => r.iter)
  return [
    { key: 'winRate', label: '胜率', vals: chrono.map((r) => r.winRate), iters },
    { key: 'scoreMean', label: '得分', vals: chrono.map((r) => r.scoreMean), iters },
    { key: 'kl', label: 'KL', vals: chrono.map((r) => r.kl), iters },
    { key: 'entropy', label: '熵', vals: chrono.map((r) => r.entropy), iters },
    {
      key: 'eval',
      label: 'eval 胜率',
      vals: chrono.map((r) => (r.evalData ? (r.evalData.winRate as number) : Number.NaN)),
      iters,
    },
    {
      key: 'kills',
      label: '击杀',
      vals: chrono.map((r) => (r.actuals ? r.actuals.totalKills : Number.NaN)),
      iters,
    },
    {
      key: 'pu',
      label: '道具',
      vals: chrono.map((r) => (r.actuals ? r.actuals.totalPU : Number.NaN)),
      iters,
    },
  ]
}

/** 训练阶段（顶栏图标用）。 */
export type TrainingPhase = 'rollout' | 'ppo' | 'idle'

export interface PhaseInfo {
  phase: TrainingPhase
  /** 当前阶段开始时刻（ms）；idle 时为 null。 */
  sinceMs: number | null
  /** 当前迭代序号；idle 时为 null。 */
  iter: number | null
}

/**
 * 从训练循环日志尾解析当前阶段。
 * 规则：日志最后一行含 "=== iteration N/M ===" → rollout（本轮刚开始）；
 *        含 "rollout itN:" 之后 → PPO 阶段（含 push/remote ppo/ppo itN 等）；
 *        其它 / 无日志 → idle。
 * 耗时 = 当前时间 - 日志时间戳（仅 rollout/ppo 有效）。
 */
export function parsePhaseFromLog(tail: string[]): PhaseInfo {
  if (tail.length === 0) return { phase: 'idle', sinceMs: null, iter: null }
  const last = tail[tail.length - 1]!
  // 日志格式：[HH:MM:SS] [run_rl] ...
  const tsMatch = last.match(/^\[(\d{2}):(\d{2}):(\d{2})\]/)
  let sinceMs: number | null = null
  if (tsMatch) {
    const h = Number(tsMatch[1])
    const m = Number(tsMatch[2])
    const s = Number(tsMatch[3])
    const d = new Date()
    d.setHours(h, m, s, 0)
    sinceMs = d.getTime()
  }
  const iterMatch = last.match(/=== iteration (\d+)\//)
  if (iterMatch) {
    return { phase: 'rollout', sinceMs, iter: Number(iterMatch[1]) }
  }
  const rolloutMatch = last.match(/rollout it(\d+):/)
  if (rolloutMatch) {
    return { phase: 'ppo', sinceMs, iter: Number(rolloutMatch[1]) }
  }
  // §380：发布 PPO job 后等待云 worker 期间，日志尾常停在 published job 行——
  // wait_job 不打印，若不加匹配阶段灯会退成 idle（数据明明在等 PPO）。
  const pubMatch = last.match(/published job \S+ it(\d+)/)
  if (pubMatch) {
    return { phase: 'ppo', sinceMs, iter: Number(pubMatch[1]) }
  }
  // push/remote ppo/ppo itN/weights archived 等 → 仍在 PPO 阶段
  if (/\[run_rl\] (push:|remote ppo|ppo it\d+|weights archived|export)/.test(last)) {
    const itMatch = last.match(/it(\d+)/)
    return { phase: 'ppo', sinceMs, iter: itMatch ? Number(itMatch[1]) : null }
  }
  return { phase: 'idle', sinceMs: null, iter: null }
}

export function lastFinite(vals: number[]): number | null {
  const f = vals.filter(Number.isFinite)
  return f.length > 0 ? f[f.length - 1]! : null
}

export function fmtValue(v: number | null): string {
  if (v === null) return '—'
  return Math.abs(v) < 10 ? v.toFixed(3) : v.toFixed(1)
}

// ────────────────────────── 纯函数：最新轮 / 色调（顶栏状态条 + 指标卡共用） ──────────────────────────

/** 最新一轮迭代（按 iter 最大；空 = null）。固定头部状态条的单一数据口径。 */
export function latestRow(iters: IterRow[]): IterRow | null {
  let best: IterRow | null = null
  for (const r of iters) if (!best || r.iter > best.iter) best = r
  return best
}

export type ValueTone = 'g' | 'y' | 'r'

export function winTone(v: number): ValueTone {
  if (v >= 0.3) return 'g'
  if (v >= 0.1) return 'y'
  return 'r'
}

export function klTone(v: number): ValueTone {
  if (v > 0.05) return 'r'
  if (v > 0.02) return 'y'
  return 'g'
}

export function retTone(v: number): ValueTone {
  if (v > -0.5) return 'g'
  if (v > -1.0) return 'y'
  return 'r'
}

// ────────────────────────── 纯函数：指标行过滤（DS-U1 13 列 + eval 子行） ──────────────────────────

export type IterFilter = 'all' | 'rollout' | 'eval'

/** 主行 + eval 子行 分组（时间倒序 = 新轮在前，与旧页一致）。 */
export function iterGroups(
  rows: IterRow[],
): Array<{ iter: number; main: IterRow; eval: EvalSummary | null }> {
  return [...rows]
    .sort((a, b) => b.iter - a.iter)
    .map((r) => ({ iter: r.iter, main: r, eval: r.evalData ?? null }))
}

export function filterGroups(
  groups: ReturnType<typeof iterGroups>,
  mode: IterFilter,
): Array<{ iter: number; main: IterRow; eval: EvalSummary | null }> {
  if (mode === 'all') return groups
  if (mode === 'eval') return groups.filter((g) => g.eval !== null)
  return groups.filter((g) => g.eval === null)
}

// ────────────────────────── 纯函数：通用排序 / 过滤 ──────────────────────────

export type SortDir = 'asc' | 'desc'

/** 通用行排序：字符串 localeCompare，数值/可 parseFloat 优先（data-v 语义保留）。 */
export function sortRows<T>(rows: T[], key: keyof T, dir: SortDir): T[] {
  if (rows.length <= 1) return rows
  const n = 1 // 逗号前保持较少的返回分支（纯函数）；实现见下
  void n
  const mult = dir === 'desc' ? -1 : 1
  return [...rows].sort((a, b) => {
    const av = a[key]
    const bv = b[key]
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * mult
    if (av === null || av === undefined) return 1
    if (bv === null || bv === undefined) return -1
    const an = typeof av === 'string' || typeof av === 'number' ? Number(av) : Number.NaN
    const bn = typeof bv === 'string' || typeof bv === 'number' ? Number(bv) : Number.NaN
    const cmp =
      !Number.isNaN(an) && !Number.isNaN(bn) ? an - bn : String(av).localeCompare(String(bv))
    return cmp * mult
  })
}

export function keywordMatch(row: Record<string, unknown>, keys: string[], kw: string): boolean {
  const q = kw.trim().toLowerCase()
  if (!q) return true
  return keys.some((k) => {
    const v = row[k]
    if (v === null || v === undefined) return false
    return String(v).toLowerCase().includes(q)
  })
}

// ────────────────────────── 纯函数：组件动作 pending 锁（§367 UI 交互） ──────────────────────────

/** 组件「启动/停止」按钮 pending 锁的释放判定：点击时把 (key -> 当时 status) 记入
 *  pending；状态已从点击时值切换（如 stopped→running / running→stopped/exited）即视为
 *  动作完成、可解锁。statusOf 查不到该组件（消失）或状态未变（如启动失败仍为 stopped）
 *  不解锁——前者由调用方兜底（失败 flash 后直接释放）。 */
export function pendingLockReleases(
  pending: Record<string, string>,
  statusOf: (key: string) => string | undefined,
): string[] {
  const out: string[] = []
  for (const [key, from] of Object.entries(pending)) {
    const cur = statusOf(key)
    if (cur !== undefined && cur !== from) out.push(key)
  }
  return out
}

// ────────────────────────── 纯函数：节点池状态 ──────────────────────────

export function statusFromRecent(recent: boolean[]): 'healthy' | 'warn' | 'bad' | 'nodata' {
  const n = recent.length
  if (n === 0) return 'nodata'
  const okN = recent.filter(Boolean).length
  if (okN / n >= 0.9) return 'healthy'
  if (okN / n >= 0.7) return 'warn'
  return 'bad'
}

// ────────────────────────── 纯函数：日志智能 follow ──────────────────────────

export const FOLLOW_THRESHOLD = 24

export function shouldFollow(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
  threshold = FOLLOW_THRESHOLD,
): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold
}

// ────────────────────────── 纯函数：dirty / 轮询节奏 ──────────────────────────

export const REFRESH_INTERVALS = [60, 180, 300, 600, 1800] as const
export type RefreshSec = (typeof REFRESH_INTERVALS)[number]

export function isDirty(pendingEdits: ReadonlyMap<string, string>): boolean {
  return pendingEdits.size > 0
}

/** 全局节奏轮转：1m → 3m → 5m → 10m → 30m → 暂停 → 1m。 */
export function nextRefreshInterval(cur: RefreshSec | 'pause'): RefreshSec | 'pause' {
  if (cur === 60) return 180
  if (cur === 180) return 300
  if (cur === 300) return 600
  if (cur === 600) return 1800
  if (cur === 1800) return 'pause'
  return 60
}

export function refreshLabel(cur: RefreshSec | 'pause'): string {
  if (cur === 'pause') return '暂停'
  if (cur < 60) return `${cur}s`
  if (cur % 60 === 0) return `${cur / 60}m`
  return `${Math.floor(cur / 60)}m${cur % 60}s`
}

// ────────────────────────── 纯函数：localStorage 迁移（GLM-U6） ──────────────────────────

export const TC_KEY_PREFIX = 'tc.'
export const TC_CARD_KEY = (id: string): string => `${TC_KEY_PREFIX}card.${id}`
export const TC_INTERVAL_KEY = (id: string): string => `${TC_KEY_PREFIX}interval.${id}`
export const TC_GLOBAL_INTERVAL = `${TC_KEY_PREFIX}globalInterval`
export const TC_METRICS_FILTER = `${TC_KEY_PREFIX}metrics.filter`
/** 局域网只读横幅关闭键（用户关闭后不再显示；tc. 前缀保证不被 cleanupNonTcKeys 误删）。 */
export const TC_RO_BANNER_DISMISSED = `${TC_KEY_PREFIX}ro.bannerDismissed`
/** hero 最新 6 轮区块视图（'main' 主行 / 'eval' 干净评估）。 */
export const TC_HERO_ITER_VIEW = `${TC_KEY_PREFIX}hero.iters`
export const TC_TREND_RANGE = `${TC_KEY_PREFIX}trend.range`
export const TC_TRAIN_MODE = `${TC_KEY_PREFIX}train.mode`
export const TC_TRAIN_TOGGLES = `${TC_KEY_PREFIX}train.toggles`
export const TC_NODE_VIEW = (view: 'ctl' | 'pool', key: string): string =>
  `${TC_KEY_PREFIX}node.${view}.${key}`

/** 旧页 localStorage 键 → 新键 + 合法值集（值不在集合内 = 不迁移、保留旧键 + warn）。 */
export interface LegacyKeyRule {
  old: string
  newKey: () => string
  legalValues: ReadonlySet<string>
}

export const LEGACY_KEY_RULES: LegacyKeyRule[] = [
  {
    old: 'pool.disableCollapsed',
    newKey: (): string => `${TC_KEY_PREFIX}card.nodes`,
    legalValues: new Set(['0', '1']),
  },
  {
    old: 'pool.iterFilter',
    newKey: (): string => TC_METRICS_FILTER,
    legalValues: new Set(['all', 'rollout', 'eval']),
  },
  {
    old: 'pool.refreshSec',
    newKey: (): string => TC_GLOBAL_INTERVAL,
    legalValues: new Set(['60', '180', '300', '600', '1800']),
  },
]

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
  key?(index: number): string | null
  readonly length?: number
}

/** 迁移一条旧键：合法 → 写新键 + 删旧键；非法 → 保留旧键 + 返回 false（调用方 console.warn）。 */
export function migrateLegacyKey(storage: StorageLike, rule: LegacyKeyRule): boolean {
  let raw: string | null = null
  try {
    raw = storage.getItem(rule.old)
  } catch {
    return true
  }
  if (raw === null) return true
  if (rule.legalValues.has(raw)) {
    try {
      storage.setItem(rule.newKey(), raw)
      storage.removeItem(rule.old)
    } catch {
      return false
    }
    return true
  }
  return false
}

/** 启动时白名单清理：遍历 storage，前缀非 tc.* 且不在遗留键集合内的键删除。
 *  返回被清理的键列表（便于测试断言）。 */
export function cleanupNonTcKeys(storage: StorageLike): string[] {
  const legacy = new Set(LEGACY_KEY_RULES.map((r) => r.old))
  const removed: string[] = []
  if (!storage.key) return removed
  for (let i = 0; i < (storage.length ?? 0); i++) {
    const k = storage.key(i)
    if (!k) continue
    if (k.startsWith(TC_KEY_PREFIX) || legacy.has(k)) continue
    try {
      storage.removeItem(k)
      removed.push(k)
    } catch {
      /* ignore */
    }
  }
  return removed
}

// ────────────────────────── 卡片注册表类型（console/ui/cards.ts 消费） ──────────────────────────

export interface CardVisibilityCtx {
  course: string
}

/** 传达一个可调整的卡片状态（App 持有）。 */
export interface CardState {
  collapsed: boolean
  maximized: boolean
}

/** 卡片面板共用 props（App 装配；卡片只需消费自己需要的字段）。
 *  定义在共享层以避免 cards ↔ panels 循环依赖（纯类型，编译期擦除）。 */
export interface PanelProps {
  stateView: ConsoleStateView | null
  onAction: (act: string, body: Record<string, unknown>) => void
  course?: string
  /** 卡未折叠（专属数据源 pool/log 据此刻暂停拉取，DS-U5）。 */
  active?: boolean
  /** 全局暂停（dirty L2 / 后台 tab / 用户暂停）。 */
  paused?: boolean
  maximizedCard?: string | null
  pendingEdits?: ReadonlyMap<string, string>
  onPending?: (key: string, value: string) => void
  onDiscardPending?: () => void
  onCommitConcurrency?: (id: string, value: string) => void
  onPoolState?: (st: StaleState, at: number) => void
  poolFreshNonce?: number
}
