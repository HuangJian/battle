/**
 * notebook-log.ts — notebook 伴生脚本的日志接收端（plan/notebook-userscript.plan.md §4.5/§4.6）。
 *
 * 三条纪律（都在协议里，不是为了好看）：
 *   ① **ack 顺序**：追加文本 → 写 meta（临时文件 + rename）→ 才回 200；客户端只在 200 后前进 offset。
 *   ② **破坏性操作两阶段**：clear / rotate 先写意图位，再动文件，最后清意图位；启动恢复把没做完的做完。
 *   ③ **请求路径全同步**：handler 读完 body 之后调用的 `appendNotebookChunk` 里**没有 await**
 *      （Bun 单线程事件循环 ⇒ 天然串行，meta 的读-判-写不会交错）。往这里加 await = 破坏协议保证。
 *
 * 与本模块配套的路由是 `POST /api/notebookLog`（搬运）与 `GET /api/notebookLogs`（会话清单）；
 * 日志页走既有 `/log/notebook:<session>`（虚拟键），不新建页面、不把 notebook 变成受管 Component。
 */
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  truncateSync,
  writeFileSync,
} from 'fs'
import path from 'path'
import { notebookLogsDir, REPO_ROOT } from '../../core/paths'
import type { LogPayload } from '../../web/view'
import { readLogTail } from './logs'

// ────────────────────────── 常量（协议的一部分） ──────────────────────────

/** 端点自读的 body 上限（不信任 Content-Length；48 KiB text 的 JSON 转义最坏 ≈6×）。 */
export const NOTEBOOK_MAX_BODY_BYTES = 512 * 1024
/** 单条 text 上限（UTF-8 字节，归一后由客户端切分）。 */
export const NOTEBOOK_MAX_TEXT_BYTES = 48 * 1024
/** 探测 JSON 上限（命令 id 只留前 40 条）。 */
export const NOTEBOOK_MAX_PROBE_BYTES = 8 * 1024
/** 单会话单段上限；超了轮转成 `.1.log`（只保留 1 份 ⇒ 单会话上限 16 MiB）。 */
export const NOTEBOOK_MAX_LOG_BYTES = 8 * 1024 * 1024
/** 写者租约：Chrome 隐藏标签最坏 1 次/分钟 ⇒ 180s = 3 倍余量。 */
export const NOTEBOOK_LEASE_MS = 180_000
/** 会话日志保留期。 */
export const NOTEBOOK_PRUNE_AGE_MS = 7 * 24 * 60 * 60 * 1000
/** prune 的内存节流（最多 1 小时跑一次；进程启动首拍也会跑）。 */
const PRUNE_THROTTLE_MS = 60 * 60 * 1000
/** 导航状态点：最近 30s 有写入 = running。 */
const RUNNING_WINDOW_MS = 30_000

const SITES = ['kaggle', 'colab', 'aistudio'] as const
export type NotebookSite = (typeof SITES)[number]
export type NotebookMode = 'append' | 'reset' | 'clear' | 'probe'
const MODES: readonly NotebookMode[] = ['append', 'reset', 'clear', 'probe']

/** session：`[a-z0-9][a-z0-9._-]{0,63}`（客户端先归一，服务端仍校验并 400 回显）。 */
const SESSION_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/
/** Windows 保留名（大小写不敏感）。 */
const WINDOWS_RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i

// ────────────────────────── 类型 ──────────────────────────

export interface NotebookChunk {
  v: 2
  site: NotebookSite
  session: string
  notebook?: string
  /** 每次页面加载随机；服务端把它当 fencing token。 */
  writer: string
  epoch: number
  cell?: string
  cellIndex?: number
  seq: number
  offsetStart: number
  offsetEnd: number
  mode: NotebookMode
  text: string
  dropped: number
  at: number
}

interface CellState {
  seq: number
  /** 客户端归一文本里已接收的末尾（UTF-16 码元）；脱敏会让文件字节与之脱钩。 */
  chars: number
  /** 已脱敏的末尾 ≤64 字符。 */
  tail: string
  index?: number
}

export interface NotebookMeta {
  v: 2
  site: NotebookSite
  session: string
  notebook?: string
  epoch: number
  activeWriter: string
  writerAt: number
  /** 当前段文件的**已提交**字节数（回滚基准）。 */
  bytes: number
  clearing: boolean
  rotating: boolean
  cells: Record<string, CellState>
  lastAt: number
}

export interface NotebookPaths {
  dir: string
  log: string
  backup: string
  meta: string
  /** 探测 JSONL（`probe.jsonl`，与同名会话日志同目录）。 */
  probe: string
}

export interface NotebookAck {
  ok: true
  epoch: number
  duplicate?: boolean
  bytes?: number
  redacted?: number
}

export interface NotebookConflict {
  ok: false
  reason: string
  epoch: number
  leaseRemainingMs?: number
  cell?: { seq: number; chars: number; tail: string }
}

export interface NotebookStoreOptions {
  /** 仅测试注入（默认 8 MiB）。 */
  maxLogBytes?: number
}

export type NotebookResult =
  { status: 200; body: NotebookAck } | { status: 400 | 409 | 413; body: NotebookConflict }

// ────────────────────────── 校验 ──────────────────────────

/** 合法 session 名；非法返回人读原因（null = 合法）。 */
export function validateNotebookSession(session: string): string | null {
  if (session.includes('..')) return 'session 不能含 `..`（路径穿越）'
  if (session.startsWith('.')) return 'session 不能以 `.` 开头'
  if (!SESSION_RE.test(session)) return 'session 必须匹配 [a-z0-9][a-z0-9._-]{0,63}'
  if (WINDOWS_RESERVED.test(session)) return 'session 是 Windows 保留名（con/nul/aux/prn/com1…）'
  return null
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function intField(o: Record<string, unknown>, key: string): number | null {
  const v = o[key]
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null
}

/** 解析 + 校验一条协议 v2 消息；任何字段非法都给人读原因（400/413 回显到面板）。 */
export function parseNotebookChunk(
  value: unknown,
): { ok: true; chunk: NotebookChunk } | { ok: false; status: 400 | 413; reason: string } {
  const o = asRecord(value)
  if (!o) return { ok: false, status: 400, reason: 'body 必须是 JSON 对象' }
  if (o.v !== 2) return { ok: false, status: 400, reason: 'v 必须是 2' }

  const site = o.site
  if (typeof site !== 'string' || !(SITES as readonly string[]).includes(site)) {
    return { ok: false, status: 400, reason: `site 必须是 ${SITES.join('|')}` }
  }
  const session = o.session
  if (typeof session !== 'string') return { ok: false, status: 400, reason: 'session 缺失' }
  const sessionError = validateNotebookSession(session)
  if (sessionError) return { ok: false, status: 400, reason: sessionError }

  const mode = o.mode
  if (typeof mode !== 'string' || !(MODES as readonly string[]).includes(mode)) {
    return { ok: false, status: 400, reason: `mode 必须是 ${MODES.join('|')}` }
  }

  const writer = o.writer
  if (typeof writer !== 'string' || !/^[A-Za-z0-9._:-]{1,64}$/.test(writer)) {
    return { ok: false, status: 400, reason: 'writer 必须是 1..64 位 [A-Za-z0-9._:-]' }
  }

  const epoch = intField(o, 'epoch')
  if (epoch === null) return { ok: false, status: 400, reason: 'epoch 必须是 ≥0 的整数' }
  const seq = intField(o, 'seq')
  if (seq === null) return { ok: false, status: 400, reason: 'seq 必须是 ≥0 的整数' }
  const at = intField(o, 'at')
  if (at === null) return { ok: false, status: 400, reason: 'at 必须是 ≥0 的整数' }

  const text = o.text
  if (typeof text !== 'string') return { ok: false, status: 400, reason: 'text 必须是字符串' }
  const textBytes = Buffer.byteLength(text, 'utf8')
  if (textBytes > NOTEBOOK_MAX_TEXT_BYTES) {
    return { ok: false, status: 413, reason: `text ${textBytes}B > ${NOTEBOOK_MAX_TEXT_BYTES}B` }
  }

  const dropped = o.dropped === undefined ? 0 : intField(o, 'dropped')
  if (dropped === null) return { ok: false, status: 400, reason: 'dropped 必须是 ≥0 的整数' }

  const notebook = o.notebook
  if (notebook !== undefined && typeof notebook !== 'string') {
    return { ok: false, status: 400, reason: 'notebook 必须是字符串' }
  }

  const cell = o.cell
  if (mode === 'append' || mode === 'reset') {
    if (typeof cell !== 'string' || !/^[A-Za-z0-9._:-]{1,128}$/.test(cell)) {
      return { ok: false, status: 400, reason: `${mode} 必须带合法 cell` }
    }
  } else if (cell !== undefined && typeof cell !== 'string') {
    return { ok: false, status: 400, reason: 'cell 必须是字符串' }
  }

  const offsetStart = intField(o, 'offsetStart')
  const offsetEnd = intField(o, 'offsetEnd')
  if (offsetStart === null || offsetEnd === null) {
    return { ok: false, status: 400, reason: 'offsetStart/offsetEnd 必须是 ≥0 的整数' }
  }
  if ((mode === 'append' || mode === 'reset') && offsetEnd !== offsetStart + text.length) {
    return { ok: false, status: 400, reason: 'offsetEnd 必须等于 offsetStart + text.length' }
  }

  const cellIndex = o.cellIndex === undefined ? undefined : intField(o, 'cellIndex')
  if (cellIndex === null) return { ok: false, status: 400, reason: 'cellIndex 必须是 ≥0 的整数' }

  const chunk: NotebookChunk = {
    v: 2,
    site: site as NotebookSite,
    session,
    ...(notebook === undefined ? {} : { notebook }),
    writer,
    epoch,
    ...(cell === undefined ? {} : { cell }),
    ...(cellIndex === undefined ? {} : { cellIndex }),
    seq,
    offsetStart,
    offsetEnd,
    mode: mode as NotebookMode,
    text,
    dropped,
    at,
  }
  if (mode === 'probe' && textBytes > NOTEBOOK_MAX_PROBE_BYTES) {
    return { ok: false, status: 413, reason: `probe ${textBytes}B > ${NOTEBOOK_MAX_PROBE_BYTES}B` }
  }
  return { ok: true, chunk }
}

// ────────────────────────── 有界 body 读取（解析前中止） ──────────────────────────

export type BoundedJson =
  { ok: true; value: unknown } | { ok: false; status: 400 | 413; reason: string }

/**
 * 流式读取并限量：超限立即 cancel + 413，**不把超大 body 读进内存**。
 * 不看 `Content-Length`（chunked 没有）；Bun 全局 `maxRequestBodySize` 仍是 128 MiB，
 * 所以这道检查必须在本端点自己做（plan §4.5 实测修正）。
 */
export async function readBoundedJson(
  req: Request,
  cap = NOTEBOOK_MAX_BODY_BYTES,
): Promise<BoundedJson> {
  if (!req.body) return { ok: false, status: 400, reason: 'body 为空' }
  const reader = req.body.getReader()
  const chunks: Uint8Array[] = []
  let total = 0
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      if (!value) continue
      total += value.byteLength
      if (total > cap) {
        await reader.cancel().catch(() => undefined)
        return { ok: false, status: 413, reason: `body 超过 ${cap} 字节` }
      }
      chunks.push(value)
    }
  } catch {
    return { ok: false, status: 400, reason: 'body 读取失败' }
  }
  const buf = Buffer.concat(
    chunks.map((c) => Buffer.from(c)),
    total,
  )
  try {
    return { ok: true, value: JSON.parse(buf.toString('utf8')) as unknown }
  } catch {
    return { ok: false, status: 400, reason: 'body 不是合法 JSON' }
  }
}

// ────────────────────────── 脱敏（写盘前） ──────────────────────────

/** 已知凭据形态的**尽力过滤**（不是 DLP；>48 KiB 单行被硬切时可能跨块漏匹配）。 */
const SECRET_PATTERNS: readonly RegExp[] = [
  /gh[pousr]_[A-Za-z0-9_]{20,}/g,
  /github_pat_[A-Za-z0-9_]{20,}/g,
  /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g,
  /\bBearer\s+[A-Za-z0-9._~+/=-]{20,}/gi,
  /\b(ts_authkey|authkey|api[_-]?key|access[_-]?token|secret|password)\b\s*[:=→<-]{1,3}\s*["']?[A-Za-z0-9._~+/=-]{16,}/gi,
]
const QUERY_SECRET_RE = /([?&](?:session|token|auth|api_key|access_token)=)[^&\s"'`]+/gi
export const REDACTED = '«redacted»'

/** 返回脱敏后的文本 + 命中次数（响应里的 `redacted`、文件里的 `# redacted n` 都来自它）。 */
export function redactSecrets(text: string): { text: string; count: number } {
  let count = 0
  let out = text.replace(QUERY_SECRET_RE, (_m, prefix: string) => {
    count += 1
    return `${prefix}${REDACTED}`
  })
  for (const re of SECRET_PATTERNS) {
    out = out.replace(re, () => {
      count += 1
      return REDACTED
    })
  }
  return { text: out, count }
}

// ────────────────────────── 路径 / meta ──────────────────────────

/** 会话三件套路径（调用方必须已校验 site/session；这里再做一次目录前缀断言）。 */
export function notebookSessionPaths(
  dir: string,
  site: NotebookSite,
  session: string,
): NotebookPaths {
  const invalid = validateNotebookSession(session)
  if (invalid) throw new Error(`[notebook-log] ${invalid}`)
  const root = path.resolve(dir)
  const base = path.resolve(root, site, session)
  if (!base.startsWith(root + path.sep)) throw new Error('notebook 日志路径越界')
  return {
    dir: path.dirname(base),
    log: `${base}.log`,
    backup: `${base}.1.log`,
    meta: `${base}.meta.json`,
    probe: `${path.dirname(base)}${path.sep}probe.jsonl`,
  }
}

function fileSize(file: string): number {
  try {
    return statSync(file).size
  } catch {
    return 0
  }
}

function rmIf(file: string): void {
  try {
    rmSync(file)
  } catch {
    /* 不存在即可 */
  }
}

function iso(now: number): string {
  return new Date(now).toISOString()
}

function emptyMeta(site: NotebookSite, session: string): NotebookMeta {
  return {
    v: 2,
    site,
    session,
    epoch: 0,
    activeWriter: '',
    writerAt: 0,
    bytes: 0,
    clearing: false,
    rotating: false,
    cells: {},
    lastAt: 0,
  }
}

function sanitizeCells(raw: unknown): Record<string, CellState> {
  const o = asRecord(raw)
  if (!o) return {}
  const out: Record<string, CellState> = {}
  for (const [key, value] of Object.entries(o)) {
    const c = asRecord(value)
    if (!c) continue
    const seq = intField(c, 'seq')
    const chars = intField(c, 'chars')
    if (seq === null || chars === null) continue
    const index = intField(c, 'index')
    out[key] = {
      seq,
      chars,
      tail: typeof c.tail === 'string' ? c.tail : '',
      ...(index === null ? {} : { index }),
    }
  }
  return out
}

/** 读 meta；坏 JSON/缺文件都当默认（`existed` 用来区分「空 meta + 非空文件」的恢复分支）。 */
export function readNotebookMeta(
  paths: NotebookPaths,
  site: NotebookSite = 'kaggle',
  session = path.basename(paths.meta, '.meta.json'),
): { meta: NotebookMeta; existed: boolean } {
  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(paths.meta, 'utf8')) as unknown
  } catch {
    return { meta: emptyMeta(site, session), existed: false }
  }
  const o = asRecord(raw)
  if (!o) return { meta: emptyMeta(site, session), existed: true }
  const fileSite =
    typeof o.site === 'string' && (SITES as readonly string[]).includes(o.site)
      ? (o.site as NotebookSite)
      : site
  const fileSession = typeof o.session === 'string' ? o.session : session
  const meta = emptyMeta(fileSite, fileSession)
  if (typeof o.notebook === 'string') meta.notebook = o.notebook
  meta.epoch = intField(o, 'epoch') ?? 0
  meta.activeWriter = typeof o.activeWriter === 'string' ? o.activeWriter : ''
  meta.writerAt = intField(o, 'writerAt') ?? 0
  meta.bytes = intField(o, 'bytes') ?? 0
  meta.clearing = o.clearing === true
  meta.rotating = o.rotating === true
  meta.cells = sanitizeCells(o.cells)
  meta.lastAt = intField(o, 'lastAt') ?? 0
  return { meta, existed: true }
}

/** 临时文件 + rename（Windows 上覆盖已存在文件实测可用）；坏半截不会成为正式 meta。 */
export function writeNotebookMeta(paths: NotebookPaths, meta: NotebookMeta): void {
  mkdirSync(paths.dir, { recursive: true })
  const tmp = `${paths.meta}.tmp-${process.pid}-${Math.random().toString(36).slice(2, 8)}`
  writeFileSync(tmp, `${JSON.stringify(meta)}\n`, 'utf8')
  renameSync(tmp, paths.meta)
}

function appendHeader(paths: NotebookPaths, line: string): number {
  appendFileSync(paths.log, line, 'utf8')
  return fileSize(paths.log)
}

/**
 * 启动/请求恢复（每个会话一次，O(1) + stat）：
 *   - `bytes` 小于文件 ⇒ 上次「已 append、未提交 meta」⇒ 截回 bytes（客户端会重发）；
 *   - `clearing` / `rotating` ⇒ 把没做完的破坏性操作做完；
 *   - 空 meta + 非空文件 / 文件比 meta 短 ⇒ 外部截断 ⇒ epoch++ + 可见段头（不静默续写）。
 */
export function recoverNotebookSession(
  paths: NotebookPaths,
  now: number,
  site: NotebookSite = 'kaggle',
  session = '',
): NotebookMeta {
  mkdirSync(paths.dir, { recursive: true })
  if (!existsSync(paths.log)) writeFileSync(paths.log, '')
  const state = readNotebookMeta(paths, site, session)
  const meta = state.meta
  const size = fileSize(paths.log)

  if (meta.clearing) {
    writeFileSync(paths.log, '')
    rmIf(paths.backup)
    meta.cells = {}
    meta.clearing = false
    meta.bytes = appendHeader(paths, `# cleared at ${iso(now)} recovered=1\n`)
    meta.lastAt = now
    writeNotebookMeta(paths, meta)
    return meta
  }
  if (meta.rotating) {
    if (existsSync(paths.log)) {
      rmIf(paths.backup)
      renameSync(paths.log, paths.backup)
    }
    writeFileSync(paths.log, '')
    meta.rotating = false
    meta.bytes = 0
    writeNotebookMeta(paths, meta)
    return meta
  }
  if (!state.existed) {
    if (size > 0) {
      meta.epoch += 1
      meta.cells = {}
      const header = `# recovered at ${iso(now)} reason=missing-meta\n`
      appendFileSync(paths.log, header, 'utf8')
      meta.bytes = size + Buffer.byteLength(header)
      writeNotebookMeta(paths, meta)
    }
    return meta
  }
  if (size > meta.bytes) {
    truncateSync(paths.log, meta.bytes)
  } else if (size < meta.bytes) {
    meta.epoch += 1
    meta.cells = {}
    const header = `# recovered at ${iso(now)} reason=external-truncate\n`
    appendFileSync(paths.log, header, 'utf8')
    meta.bytes = size + Buffer.byteLength(header)
    writeNotebookMeta(paths, meta)
  }
  return meta
}

function rotate(paths: NotebookPaths, meta: NotebookMeta): void {
  meta.rotating = true
  writeNotebookMeta(paths, meta)
  if (existsSync(paths.log)) {
    rmIf(paths.backup)
    renameSync(paths.log, paths.backup)
  }
  writeFileSync(paths.log, '')
  meta.rotating = false
  meta.bytes = 0
  writeNotebookMeta(paths, meta)
}

function ensureCapacity(
  paths: NotebookPaths,
  meta: NotebookMeta,
  addBytes: number,
  maxLogBytes: number,
): void {
  if (meta.bytes + addBytes <= maxLogBytes) return
  rotate(paths, meta)
}

function tailOf(text: string): string {
  return text.length <= 64 ? text : text.slice(text.length - 64)
}

function conflict(
  status: 400 | 409 | 413,
  reason: string,
  meta: NotebookMeta,
  extra: Partial<NotebookConflict> = {},
): NotebookResult {
  return { status, body: { ok: false, reason, epoch: meta.epoch, ...extra } }
}

function leaseActive(meta: NotebookMeta, now: number): boolean {
  return meta.activeWriter !== '' && now - meta.writerAt < NOTEBOOK_LEASE_MS
}

// ────────────────────────── 主入口 ──────────────────────────

/**
 * 落一条消息。**同步**（无 await）：这是并发保证，不是实现细节（见文件头③）。
 * 返回 HTTP 语义（200/400/409/413），由路由层原样回包。
 */
export function appendNotebookChunk(
  dir: string,
  chunk: NotebookChunk,
  now: number,
  opts: NotebookStoreOptions = {},
): NotebookResult {
  const maxLogBytes = opts.maxLogBytes ?? NOTEBOOK_MAX_LOG_BYTES
  const paths = notebookSessionPaths(dir, chunk.site, chunk.session)
  mkdirSync(paths.dir, { recursive: true })
  if (!existsSync(paths.log)) writeFileSync(paths.log, '')
  const meta = recoverNotebookSession(paths, now, chunk.site, chunk.session)

  // probe 与日志会话分开落盘；不参与租约/epoch（探测是只读现场，不是写者）。
  if (chunk.mode === 'probe') {
    const probe = paths.probe
    if (!existsSync(probe)) writeFileSync(probe, '')
    const line = `${JSON.stringify({ at: now, site: chunk.site, session: chunk.session, text: chunk.text })}\n`
    if (fileSize(probe) + Buffer.byteLength(line) > 1024 * 1024) {
      rmIf(`${probe}.1`)
      renameSync(probe, `${probe}.1`)
      writeFileSync(probe, '')
    }
    appendFileSync(probe, line, 'utf8')
    return { status: 200, body: { ok: true, epoch: meta.epoch } }
  }

  if (chunk.mode === 'clear') {
    if (leaseActive(meta, now) && meta.activeWriter !== chunk.writer) {
      return conflict(409, 'writer-conflict', meta, {
        leaseRemainingMs: Math.max(0, NOTEBOOK_LEASE_MS - (now - meta.writerAt)),
      })
    }
    // 两阶段：先落意图（epoch 已是新值），再动文件，最后清意图位。
    const nextEpoch = meta.epoch + 1
    meta.clearing = true
    meta.epoch = nextEpoch
    meta.activeWriter = chunk.writer
    meta.writerAt = now
    meta.lastAt = now
    writeNotebookMeta(paths, meta)

    writeFileSync(paths.log, '')
    rmIf(paths.backup)
    meta.cells = {}
    meta.bytes = appendHeader(paths, `# cleared at ${iso(now)}\n`)
    meta.clearing = false
    writeNotebookMeta(paths, meta)
    return { status: 200, body: { ok: true, epoch: nextEpoch, bytes: meta.bytes } }
  }

  // append / reset：写者租约（fencing token = writer 本身）
  if (leaseActive(meta, now) && meta.activeWriter !== chunk.writer) {
    const cursor = chunk.cell ? meta.cells[chunk.cell] : undefined
    return conflict(409, 'writer-conflict', meta, {
      leaseRemainingMs: Math.max(0, NOTEBOOK_LEASE_MS - (now - meta.writerAt)),
      ...(cursor
        ? { cell: { seq: cursor.seq, chars: cursor.chars, tail: cursor.tail } }
        : { cell: { seq: 0, chars: 0, tail: '' } }),
    })
  }

  // 代际：append 必须与当前 epoch 一致；reset 对齐到服务端 epoch（新段无需旧代际）。
  if (chunk.mode === 'append' && chunk.epoch !== meta.epoch) {
    return conflict(409, 'stale-epoch', meta)
  }

  const cellId = chunk.cell!
  const prior = meta.cells[cellId]

  if (chunk.mode === 'append') {
    // 心跳：空 text 只续租，不写盘、不推进 seq（否则会占掉下一块的 seq）。
    if (chunk.text.length === 0) {
      meta.activeWriter = chunk.writer
      meta.writerAt = now
      meta.lastAt = now
      writeNotebookMeta(paths, meta)
      return { status: 200, body: { ok: true, epoch: meta.epoch, bytes: meta.bytes } }
    }
    if (prior && chunk.seq <= prior.seq) {
      meta.activeWriter = chunk.writer
      meta.writerAt = now
      meta.lastAt = now
      writeNotebookMeta(paths, meta)
      return {
        status: 200,
        body: { ok: true, epoch: meta.epoch, duplicate: true, bytes: meta.bytes },
      }
    }
    const chars = prior?.chars ?? 0
    if (chunk.offsetStart !== chars) {
      return conflict(409, 'offset-mismatch', meta, {
        cell: { seq: prior?.seq ?? 0, chars, tail: prior?.tail ?? '' },
      })
    }
  }

  const redacted = redactSecrets(chunk.text)
  const header =
    chunk.mode === 'reset'
      ? `# segment cell=${cellId} idx=${chunk.cellIndex ?? -1} mode=reset at=${iso(now)} dropped=${chunk.dropped} redacted=${redacted.count}\n`
      : ''
  const redactLine =
    chunk.mode === 'append' && redacted.count > 0 ? `# redacted ${redacted.count}\n` : ''
  const writeText = header + redacted.text + redactLine
  ensureCapacity(paths, meta, Buffer.byteLength(writeText, 'utf8'), maxLogBytes)
  appendFileSync(paths.log, writeText, 'utf8')
  meta.bytes = fileSize(paths.log)

  meta.cells[cellId] = {
    seq: chunk.seq,
    chars: chunk.offsetEnd,
    tail: tailOf(redacted.text),
    ...(chunk.cellIndex === undefined ? {} : { index: chunk.cellIndex }),
  }
  meta.activeWriter = chunk.writer
  meta.writerAt = now
  meta.lastAt = now
  if (chunk.notebook !== undefined) meta.notebook = chunk.notebook
  writeNotebookMeta(paths, meta)
  return {
    status: 200,
    body: {
      ok: true,
      epoch: meta.epoch,
      bytes: meta.bytes,
      ...(redacted.count > 0 ? { redacted: redacted.count } : {}),
    },
  }
}

/** 会话级清空（供测试/运维；HTTP 走 `mode:'clear'`）。 */
export function clearNotebookSession(
  dir: string,
  site: NotebookSite,
  session: string,
  now: number,
): NotebookResult {
  return appendNotebookChunk(
    dir,
    {
      v: 2,
      site,
      session,
      writer: 'clear',
      epoch: 0,
      seq: 1,
      offsetStart: 0,
      offsetEnd: 0,
      mode: 'clear',
      text: '',
      dropped: 0,
      at: now,
    },
    now,
  )
}

// ────────────────────────── 清单 / 日志页载荷 ──────────────────────────

export interface NotebookSessionInfo {
  site: NotebookSite
  session: string
  notebook?: string
  logPath: string
  bytes: number
  mtime: number
  epoch: number
  lastAt: number
}

/** 全部会话（mtime 降序；跳过 `.1.log` 备份段）。 */
export function listNotebookSessions(dir: string): NotebookSessionInfo[] {
  const out: NotebookSessionInfo[] = []
  let sites: string[]
  try {
    sites = readdirSync(dir, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
  } catch {
    return out
  }
  for (const siteName of sites) {
    if (!(SITES as readonly string[]).includes(siteName)) continue
    const site = siteName as NotebookSite
    const siteDir = path.join(dir, site)
    let files: string[]
    try {
      files = readdirSync(siteDir)
    } catch {
      continue
    }
    for (const file of files) {
      if (!file.endsWith('.log') || file.endsWith('.1.log')) continue
      const session = file.slice(0, -'.log'.length)
      if (validateNotebookSession(session) !== null) continue
      const abs = path.join(siteDir, file)
      let mtime = 0
      try {
        mtime = statSync(abs).mtimeMs
      } catch {
        continue
      }
      const paths = notebookSessionPaths(dir, site, session)
      const { meta } = readNotebookMeta(paths)
      out.push({
        site,
        session,
        ...(meta.notebook === undefined ? {} : { notebook: meta.notebook }),
        logPath: abs,
        bytes: fileSize(abs),
        mtime,
        epoch: meta.epoch,
        lastAt: meta.lastAt,
      })
    }
  }
  return out.sort((a, b) => b.mtime - a.mtime)
}

/**
 * `/log/<key>` / `/api/log/<key>` 的键要从 **URL 编码**里解出来。
 *
 * 客户端把键拼进路径时做了 `encodeURIComponent`（`notebook:<session>` → `notebook%3A…`），
 * 而 `URL.pathname` 保留百分号编码 ⇒ 不解码就拿 `notebook%3A…` 去查组件表，好端端一个
 * 会话被报成「未知组件」（2026-10-30 多开 Kaggle 页面时点「打开日志」触发）。
 * 畸形 `%` 不抛：原样返回，让查表那边去 404。
 */
export function decodeLogKey(pathname: string, prefix: string): string {
  const raw = pathname.slice(prefix.length)
  try {
    return decodeURIComponent(raw)
  } catch {
    return raw
  }
}

/** 虚拟键：`notebook`（最近一个会话）或 `notebook:<session>`。 */
export function isNotebookLogKey(key: string): boolean {
  return key === 'notebook' || key.startsWith('notebook:') || key === 'probe'
}

/** 日志键没有可读载荷时的 404 文案：notebook 是「还没会话」，不是「未知组件」。 */
export function missingLogMessage(key: string): string {
  if (isNotebookLogKey(key)) {
    return `还没有 notebook 会话：${key}（先打开 Kaggle notebook 并确认伴生脚本面板显示「已连接」；会话清单见 /api/notebookLogs）`
  }
  return `未知组件: ${key}`
}

function resolveSession(dir: string, key: string): NotebookSessionInfo | null {
  const sessions = listNotebookSessions(dir)
  if (key === 'notebook') return sessions[0] ?? null
  const session = key.slice('notebook:'.length)
  if (validateNotebookSession(session) !== null) return null
  return sessions.find((s) => s.session === session) ?? null
}

/** `/log/notebook…` 的导航状态点（最近 30s 有写入 = running）。 */
export function notebookNavEntry(
  dir: string,
  key: string,
): { key: string; label: string; status: 'running' | 'stopped' } | null {
  const session = resolveSession(dir, key)
  if (!session) return null
  return {
    key: `notebook:${session.session}`,
    label: `Notebook · ${session.session}`,
    status: Date.now() - session.lastAt < RUNNING_WINDOW_MS ? 'running' : 'stopped',
  }
}

/** 虚拟键的日志载荷（与 `componentLogPayload` 同形状 ⇒ 日志页零改动）。 */
export function notebookLogPayload(
  dir: string,
  key: string,
  maxLines: number | 'all',
): LogPayload | null {
  const session = resolveSession(dir, key)
  if (!session) return null
  const t = readLogTail(session.logPath, maxLines)
  return {
    component: `notebook:${session.session}`,
    label: `Notebook · ${session.session}`,
    log: path.relative(REPO_ROOT, session.logPath),
    exists: t.exists,
    fileSize: t.fileSize,
    lines: t.lines,
    truncated: t.truncated,
    totalLines: t.totalLines,
    updatedAt: Date.now(),
  }
}

// ────────────────────────── prune ──────────────────────────

let lastPruneAt = 0

/** 删除 7 天未写的会话（内存节流 1 小时；返回删除数，测试可注入 now）。 */
export function pruneNotebookLogs(
  dir: string,
  now: number,
  maxAgeMs = NOTEBOOK_PRUNE_AGE_MS,
  throttleMs = PRUNE_THROTTLE_MS,
): number {
  if (now - lastPruneAt < throttleMs) return 0
  lastPruneAt = now
  let removed = 0
  for (const s of listNotebookSessions(dir)) {
    if (now - s.mtime < maxAgeMs) continue
    rmIf(s.logPath)
    rmIf(path.join(path.dirname(s.logPath), `${s.session}.1.log`))
    rmIf(path.join(path.dirname(s.logPath), `${s.session}.meta.json`))
    removed += 1
  }
  return removed
}

/** 默认根（路由层用它；测试直接传临时目录）。 */
export function defaultNotebookLogsDir(): string {
  return notebookLogsDir()
}
