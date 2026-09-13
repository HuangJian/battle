/** log-view.ts — 日志视图类型与展示层纯函数（行解析 / 训练事件解析）。 */
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
