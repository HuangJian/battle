/**
 * notebook-op.ts — notebook 伴生脚本的**操作日志**接收端（`POST /api/notebookOp`）。
 *
 * 与 `notebook-log.ts`（cell 输出搬运）刻意分开：
 *   - 搬运协议是有状态的（epoch / offset / seq / writer 租约），插一行操作文本会污染 cell 游标，
 *     而且 `recoverNotebookSession` 会把「meta 之外的字节」截掉 ⇒ 操作日志**不能**写进 `.log`；
 *   - 操作日志要的是 append-only 结构化行（`<session>.ops.jsonl`）：排查时按 op 名 grep，
 *     审计时逐行看「哪个页签、什么时候、点了什么、结果如何」。
 *
 * 三条纪律：
 *   ① **零状态**：不参与租约/epoch，不碰 meta —— 写失败就是 500，不会破坏搬运会话；
 *   ② **脱敏与限长在写盘前**：复用搬运端的 `redactSecrets`，detail 截到 `OP_MAX_DETAIL`；
 *   ③ **单文件有界**：超 `OP_MAX_BYTES` 轮转成 `.1.ops.jsonl`（只留一代）+ 7 天 prune，
 *      与 `.log` 同寿命（`pruneNotebookLogs` 一并删）。
 */
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
} from 'fs'
import path from 'path'
import {
  redactSecrets,
  resolveNotebookSession,
  validateNotebookSession,
  type NotebookSite,
} from './notebook-log'
import type { LogPayload } from '../../web/view'

export const NOTEBOOK_OP_MAX_BODY_BYTES = 32 * 1024
export const OP_MAX_BYTES = 2 * 1024 * 1024
export const OP_MAX_DETAIL = 400
export const OP_MAX_OP_NAME = 64

/** 站点白名单（`/api/notebookCmd*` 也用它校验 query，别各写一份）。 */
export const NOTEBOOK_SITES = ['kaggle', 'colab', 'aistudio', 'modelscope'] as const
const OP_NAME_RE = /^[A-Za-z0-9:_.-]{1,64}$/
const TAG_RE = /^[a-z0-9-]{0,32}$/i

export interface NotebookOp {
  v: 1
  site: NotebookSite
  session: string
  /** 页签标签（多开时分得清）。 */
  page: string
  writer: string
  at: number
  /** `cmd:flush` / `schedule:add` / `boot` …（审计口径 = 这串名字）。 */
  op: string
  detail?: string
}

export interface NotebookOpPaths {
  dir: string
  ops: string
  backup: string
}

export type NotebookOpParse =
  { ok: true; op: NotebookOp } | { ok: false; status: 400; reason: string }

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/** 解析 + 校验一条操作日志；非法字段给人读原因（路由层原样 400 回显）。 */
export function parseNotebookOp(raw: unknown): NotebookOpParse {
  const o = asRecord(raw)
  if (!o) return { ok: false, status: 400, reason: 'body 必须是 JSON 对象' }
  if (o.v !== 1) return { ok: false, status: 400, reason: 'v 必须是 1' }
  const site = o.site
  if (typeof site !== 'string' || !(NOTEBOOK_SITES as readonly string[]).includes(site)) {
    return { ok: false, status: 400, reason: `site 非法：${String(site)}（kaggle/colab/aistudio）` }
  }
  const session = typeof o.session === 'string' ? o.session : ''
  const invalid = validateNotebookSession(session)
  if (invalid) return { ok: false, status: 400, reason: `session：${invalid}` }
  const op = typeof o.op === 'string' ? o.op : ''
  if (!OP_NAME_RE.test(op)) {
    return {
      ok: false,
      status: 400,
      reason: `op 非法（[A-Za-z0-9:_.-]{1,64}）：${op.slice(0, 80)}`,
    }
  }
  const page = typeof o.page === 'string' ? o.page : ''
  if (!TAG_RE.test(page)) return { ok: false, status: 400, reason: `page 非法（[a-z0-9-]{0,32}）` }
  const writer = typeof o.writer === 'string' ? o.writer : ''
  if (!TAG_RE.test(writer))
    return { ok: false, status: 400, reason: `writer 非法（[a-z0-9-]{0,32}）` }
  const at = typeof o.at === 'number' && Number.isFinite(o.at) ? o.at : 0

  const detailRaw = typeof o.detail === 'string' ? o.detail : ''
  // 拍成单行（JSONL 一行一条）：换行 → ⏎，制表 → 空格（与客户端同口径）
  const detail = redactSecrets(detailRaw.replace(/[\r\n]+/g, ' ⏎ ').replace(/\t/g, ' ')).text.slice(
    0,
    OP_MAX_DETAIL,
  )

  return {
    ok: true,
    op: {
      v: 1,
      site: site as NotebookSite,
      session,
      page,
      writer,
      at,
      // op 名也过一遍脱敏：调用方可能把参数拼进了名字（客户端已归一，这里是第二道）
      op: redactSecrets(op).text,
      ...(detail ? { detail } : {}),
    },
  }
}

/** 会话的操作日志两件套路径（`<dir>/<site>/<session>.ops.jsonl`）。 */
export function notebookOpsPaths(
  dir: string,
  site: NotebookSite,
  session: string,
): NotebookOpPaths {
  const invalid = validateNotebookSession(session)
  if (invalid) throw new Error(`[notebook-op] ${invalid}`)
  const root = path.resolve(dir)
  const base = path.resolve(root, site, session)
  if (!base.startsWith(root + path.sep)) throw new Error('notebook 操作日志路径越界')
  const opsDir = path.dirname(base)
  const name = path.basename(base)
  return {
    dir: opsDir,
    ops: `${base}.ops.jsonl`,
    backup: `${opsDir}${path.sep}${name}.1.ops.jsonl`,
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

/** 一行人话（日志页把它和 cell 输出一起显示；结构字段留给 `.ops.jsonl`）。 */
export function formatNotebookOp(op: NotebookOp): string {
  const clock = new Date(op.at).toISOString()
  const tail = op.detail ? ` — ${op.detail}` : ''
  return `[op ${clock}] ${op.page || '-'}/${op.writer || '-'} ${op.op}${tail}`
}

/**
 * 落一条操作日志（**同步**，与搬运端同一套「单线程事件循环即串行」的假设）。
 * 返回写入后的文件字节数（测试/调用方对账用）。
 */
export function appendNotebookOp(
  dir: string,
  op: NotebookOp,
  now: number,
  opts: { maxBytes?: number } = {},
): { bytes: number } {
  const maxBytes = opts.maxBytes ?? OP_MAX_BYTES
  const paths = notebookOpsPaths(dir, op.site, op.session)
  mkdirSync(paths.dir, { recursive: true })
  const line = `${JSON.stringify({ ...op, at: op.at || now })}\n`
  const size = fileSize(paths.ops)
  if (size + Buffer.byteLength(line) > maxBytes) {
    rmIf(paths.backup)
    if (existsSync(paths.ops)) renameSync(paths.ops, paths.backup)
  }
  appendFileSync(paths.ops, line, 'utf8')
  return { bytes: fileSize(paths.ops) }
}

/** 尾部 N 行（解析失败的行原样保留，**不丢证据**）；文件不存在 ⇒ 空。 */
export function readNotebookOpsTail(
  dir: string,
  site: NotebookSite,
  session: string,
  maxLines: number,
): string[] {
  let text: string
  try {
    text = readFileSync(notebookOpsPaths(dir, site, session).ops, 'utf8')
  } catch {
    return []
  }
  const all = text.split('\n').filter((l) => l !== '')
  const tail = maxLines === Infinity ? all : all.slice(-Math.max(0, maxLines))
  return tail.map((line) => {
    try {
      const parsed = asRecord(JSON.parse(line))
      if (!parsed) return line
      return formatNotebookOp({
        v: 1,
        site: parsed.site as NotebookSite,
        session: String(parsed.session ?? session),
        page: String(parsed.page ?? ''),
        writer: String(parsed.writer ?? ''),
        at: typeof parsed.at === 'number' ? parsed.at : 0,
        op: String(parsed.op ?? '?'),
        ...(typeof parsed.detail === 'string' && parsed.detail ? { detail: parsed.detail } : {}),
      })
    } catch {
      return line
    }
  })
}

/** 会话的操作日志文件路径（`/api/notebookOps` 回显给操作员）。 */
export function notebookOpsFilePath(dir: string, site: NotebookSite, session: string): string {
  return notebookOpsPaths(dir, site, session).ops
}

/** 日志页尾巴的条数（信息量够定位、不把页刷成海量）。 */
export const OP_TAIL_LINES = 40

/**
 * 日志页载荷 + 操作日志尾巴：`notebookLogPayload` 只读 `.log`（cell 输出），
 * 这里把最近 `OP_TAIL_LINES` 条操作接在后面（带横幅，不会被误当 cell 输出）。
 *
 * 依赖方向：notebook-log.ts ⟶ 不 import 本文件（只用导出的 `resolveNotebookSession`），避免循环。
 */
export function withNotebookOps(
  dir: string,
  key: string,
  payload: LogPayload | null,
): LogPayload | null {
  if (!payload) return payload
  const session = resolveNotebookSession(dir, key)
  if (!session) return payload
  const ops = readNotebookOpsTail(dir, session.site, session.session, OP_TAIL_LINES)
  if (ops.length === 0) return payload
  const rel = path.relative(
    path.resolve(dir, '..', '..'),
    notebookOpsFilePath(dir, session.site, session.session),
  )
  return {
    ...payload,
    lines: [
      ...payload.lines,
      '',
      `# ── 操作日志（最近 ${ops.length} 条；来源 ${rel.replace(/\\/g, '/')}）──`,
      ...ops,
    ],
  }
}
