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

/** 零值读数里的错误关键词（文本 `失败=0` 形态 / JSON `"error":null` 形态）。
 *
 *  ★ 2026-10-20（用户报障「这是误报吧？」）：关键词扫描把**零值读数**当成了错误——
 *  sampler-agent 的例行进度行「近30min产出 games=2884 … · **失败=0** · 轮次=40 ·
 *  **待取失败=0** · 累计=21814」被扫成 error ⇒ 控制台横幅把它当错误报出来（实测两条假
 *  事件、count 10）。`失败=0` 说的是「零失败」，恰恰是**好消息**。
 *  ⇒ 判级别前把「关键词在左、值是零/空」的那一截涂掉（只涂关键词，保留 `=0` 便于阅读）。 */
const ZERO_VALUED_ERROR_KW_TEXT_RE =
  /(失败|fail(?:ed|s|ure)?|errors?|异常|崩溃|panic|fatal|exception)\s*[=:：]\s*0+(?:\.0+)?(?![0-9.])/gi
/** JSON 形态：键是错误关键词、值是空/零（`{"error":null}` / `{"error":""}`）。 */
const EMPTY_ERROR_KEY_JSON_RE = /"(error|errors|failure|failures|err)"\s*:\s*(?:null|0\b|"|\{\})/gi

/** 显式级别（结构化日志**自带** level ⇒ 以它为准，胜过关键词扫描）。
 *
 *  ① JSON 行：`{"level":"info",…}`（cloudflared 形状，level 总是第一个键）；
 *  ② 文本行：`<ISO 时间戳> INF|ERR|WRN …`（cloudflared 文本形状）。
 *
 *  ★ 同日实测的同族误报：cloudflared 的**信息行**里带 FAIL/failed 字样 ——
 *  `2026-10-20T02:13:20Z INF |  UDP Connectivity … FAIL  QUIC connection failed |`、
 *  `INF precheck component="UDP Connectivity" details="QUIC connection failed" … status=fail`、
 *  以及它们的 JSON 孪生 `{"level":"info",…,"message":"… FAIL …"}` ⇒ 6 个假事件。
 *  规则：**日志自己声明了级别，就别再拿关键词去推翻它。** */
const JSON_LEVEL_RE = /^\{"level"\s*:\s*"(error|err|warn|warning|info|debug|trace|fatal|panic)"/i
const TEXT_LEVEL_RE =
  /^\d{4}-\d{2}-\d{2}T[0-9:.]+Z?\s+(INF|INFO|ERR|ERROR|WRN|WARN|FTL|FATAL|DBG|DEBUG)\b/

/** 显式级别 → 归一的三档；没有声明 ⇒ null（交给关键词扫描）。 */
export function explicitLogLevel(text: string): LogLevel | null {
  const j = JSON_LEVEL_RE.exec(text)
  if (j) {
    const v = j[1]!.toLowerCase()
    if (v === 'error' || v === 'err' || v === 'fatal' || v === 'panic') return 'error'
    if (v === 'warn' || v === 'warning') return 'warn'
    return 'info'
  }
  const t = TEXT_LEVEL_RE.exec(text)
  if (t) {
    const v = t[1]!.toUpperCase()
    if (v === 'ERR' || v === 'ERROR' || v === 'FTL' || v === 'FATAL') return 'error'
    if (v === 'WRN' || v === 'WARN') return 'warn'
    return 'info'
  }
  return null
}

/** 涂掉「零值读数」里的错误关键词（只涂关键词本身，保留 `=0` / `:null` 这段可读性）。 */
function maskZeroValuedErrorKeywords(text: string): string {
  return text
    .replace(ZERO_VALUED_ERROR_KW_TEXT_RE, (m) => m.replace(/[A-Za-z一-鿿]+/g, ''))
    .replace(EMPTY_ERROR_KEY_JSON_RE, (m) => m.replace(/[A-Za-z]+/g, ''))
}

/** HTTP 访问日志的**行首结构**：`"GET /path HTTP/1.1" 200`（python http.server / uvicorn 同形）。
 *
 *  ★ 判级别必须看**状态码**而不是路径关键词（plan §1.1 M1/M2）：`/admin/errors` 里的
 *  `errors` 子串会把一条 200 当成错误。**不依赖行尾状态码**：尾窗读会截断长 query，
 *  状态码可能已经不在（评审 F5）——识别只看 `METHOD path HTTP/x.y`，缺失状态码按 info。 */
const HTTP_ACCESS_RE =
  /"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+\s+HTTP\/\d\.\d"(?:\s+(\d{3}))?/

/** 访问日志行 → 状态码；非访问行 → `undefined`；访问行但状态码被截断 → `null`。 */
export function httpAccessStatus(text: string): number | null | undefined {
  const m = HTTP_ACCESS_RE.exec(text)
  if (!m) return undefined
  return m[1] ? Number(m[1]) : null
}

/** 切分日志行：[HH:MM:SS] 时间戳 + [组件] 标签 + 正文；按关键词判级别。训练 JSON 行不走此解析。
 *
 *  访问日志例外：级别只看状态码（`>=500` ⇒ error，其余 info）。4xx 不算错误——
 *  否则 `/admin/errors?since=0` 这类带 `error` 的路径又会被误报（plan D4）。
 *
 *  ★ 两条降误报规则（2026-10-20 用户报障「这是误报吧？」，实测 6 个假事件）：
 *  ① **显式级别优先**：日志自带 level（JSON `"level":"info"` / 文本 `INF`）⇒ 以它为准，
 *     不拿关键词推翻它（`INF … FAIL …` 是信息行，不是错误）。
 *  ② **零值读数不是错误**：`失败=0` / `待取失败=0` / `"error":null` ⇒ 关键词先涂掉再扫。 */
export function parseLogLine(line: string): ParsedLogLine {
  const m = LOG_TIME_RE.exec(line)
  const rest = m ? line.slice(m[0].length) : line
  const access = httpAccessStatus(rest)
  let level: LogLevel = 'info'
  if (access !== undefined) level = access !== null && access >= 500 ? 'error' : 'info'
  else {
    const explicit = explicitLogLevel(rest)
    if (explicit) level = explicit
    else {
      const masked = maskZeroValuedErrorKeywords(rest)
      if (LOG_ERR_RE.test(masked)) level = 'error'
      else if (LOG_WARN_RE.test(masked)) level = 'warn'
    }
  }
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
