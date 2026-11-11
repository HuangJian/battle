/**
 * notebook-cmd.ts — notebook 伴生脚本的**下行命令**通道（dashboard → 其它 notebook 页）。
 *
 * 为什么要它：离线训练页（`battle.offline.ipynb`）勾了「启动后停止其它 ppo worker」时，
 * 训练 cell 一开跑就报 `offline:cell-start … stopOthers=1`。2026-11-09 那版只把开关写进
 * op 日志（`DECISIONS §2026-11-09-goalnn-offline-start-report`：本阶段只上报）；本条把它接成
 * **真命令**——浏览器跨页没法互停，所以由 dashboard 当中转：
 *
 *   ① 收到 `offline:cell-start` 且 `stopOthers=1` ⇒ 给**其它最近活跃会话**各记一条
 *      `stop-session`（`<dir>/<site>/<session>.cmds.json`）；
 *   ② 各页每 15s `GET /api/notebookCmds?site=&session=` 取自己的待办；
 *   ③ 页面停完会话后落一行 op `cmd:stop-session id=<id> ok|fail …` ——**这一行就是 ack**，
 *      收到即把命令置 done（不另开端点：op 日志本来就是这个通道的证据面）。
 *
 * 三条纪律：
 *   · **只停「其它 PPO worker 页」**：发起会话自己永远不在目标里；目标集合 = 10 分钟内有 op 写入
 *     （`.ops.jsonl` 的 mtime）**且日志尾巴里有 worker 标记**（`[worker]` / `[worker-supervisor]`）
 *     的会话——只搬运日志的普通页、离线页、rollout 节点都不该被停（2026-11-11 用户口径：
 *     「只停 PPO worker 页，不误停正在搬运日志的普通页」）；
 *   · **幂等 + 有界**：同一目标在本条命令 TTL 内已有 pending 就不再记；单次最多 5 个目标；
 *     每个会话的命令文件只留最近 `CMD_MAX_RECORDS` 条；
 *   · **fail closed**：站/会话非法、路径越界一律抛；页面没 ack 的命令到 TTL 自动失效，
 *     不会在几小时后突然把某个页停掉。
 */
import { randomUUID } from 'crypto'
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  readSync,
  readdirSync,
  renameSync,
  statSync,
  writeFileSync,
} from 'fs'
import path from 'path'
import { validateNotebookSession, type NotebookSite } from './notebook-log'
import { NOTEBOOK_SITES, type NotebookOp } from './notebook-op'

/** 目标集合的「活」窗口：这么久没写过 op 的会话不算在跑，不派命令。 */
export const STOP_OTHERS_MAX_IDLE_MS = 10 * 60_000
/** 单次 `stopOthers` 最多派几个目标（防止一次把满屏页签全停掉，也便于人读日志）。 */
export const STOP_OTHERS_MAX_TARGETS = 5
/** 命令有效期：过期的 pending 不再下发（页面几小时后醒来也不会被停）。 */
export const CMD_TTL_MS = 15 * 60_000
/** 每个会话的命令文件保留条数（几十条足够人工回溯）。 */
export const CMD_MAX_RECORDS = 50
/**
 * 「这个会话是 PPO worker 页」的日志标记（只有命中才停）。
 *
 * 为什么看日志：`worker.py` / `bc_job.py` / `download.py` / `http.py` 的每一行都带
 * `[HH:MM:SS] [worker] ` 前缀（supervisor 是 `[worker-supervisor]`），而普通页（日志搬运、
 * 离线训练页、rollout 节点）不会打这个前缀。真实盘上已验：AI Studio worker 会话命中、
 * `…-battle-offline-…` 会话不命中。
 */
export const PPO_WORKER_LOG_MARKERS = ['[worker]', '[worker-supervisor]'] as const
/** 只看日志尾巴这么多字节（worker 每个 job 都在写，64KB 足够；不为了分类把 8MB 日志读进内存）。 */
export const PPO_WORKER_LOG_TAIL_BYTES = 64 * 1024

export type NotebookCmdAction = 'stop-session'
export type NotebookCmdStatus = 'pending' | 'ok' | 'fail'

export interface NotebookCmd {
  v: 1
  id: string
  site: NotebookSite
  session: string
  action: NotebookCmdAction
  /** 入队时刻（ms）。 */
  at: number
  /** 发起者 `site:session`（谁让它停的；审计用）。 */
  by: string
  status: NotebookCmdStatus
  /** ack 的附加原因（fail 时是人话原因）。 */
  reason?: string
}

export interface NotebookCmdTarget {
  site: NotebookSite
  session: string
  /** `.ops.jsonl` 的 mtime（活窗口判据）。 */
  mtime: number
}

export function isNotebookSite(value: string): value is NotebookSite {
  return (NOTEBOOK_SITES as readonly string[]).includes(value)
}

/** 命令文件路径（`<dir>/<site>/<session>.cmds.json`，与 `.ops.jsonl` 同目录）。 */
export function notebookCmdsPath(dir: string, site: NotebookSite, session: string): string {
  const invalid = validateNotebookSession(session)
  if (invalid) throw new Error(`[notebook-cmd] ${invalid}`)
  if (!isNotebookSite(site)) throw new Error(`[notebook-cmd] site 非法：${site}`)
  const root = path.resolve(dir)
  const base = path.resolve(root, site, session)
  if (!base.startsWith(root + path.sep)) throw new Error('notebook 命令路径越界')
  return `${base}.cmds.json`
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function parseCmd(raw: unknown): NotebookCmd | null {
  const o = asRecord(raw)
  if (!o) return null
  if (o.v !== 1) return null
  const site = typeof o.site === 'string' ? o.site : ''
  if (!isNotebookSite(site)) return null
  const session = typeof o.session === 'string' ? o.session : ''
  if (validateNotebookSession(session) !== null) return null
  const id = typeof o.id === 'string' ? o.id : ''
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) return null
  if (o.action !== 'stop-session') return null
  const at = typeof o.at === 'number' && Number.isFinite(o.at) ? o.at : 0
  const status = o.status === 'ok' || o.status === 'fail' ? o.status : 'pending'
  const by = typeof o.by === 'string' ? o.by.replace(/[^\x20-\x7e]/g, '').slice(0, 96) : ''
  const reason =
    typeof o.reason === 'string' ? o.reason.replace(/[\r\n]+/g, ' ').slice(0, 200) : undefined
  return {
    v: 1,
    id,
    site,
    session,
    action: 'stop-session',
    at,
    by,
    status,
    ...(reason ? { reason } : {}),
  }
}

/** 读一个会话的命令（同 id 后写胜出；坏行丢弃，不抛）。 */
export function readNotebookCmds(dir: string, site: NotebookSite, session: string): NotebookCmd[] {
  let text: string
  try {
    text = readFileSync(notebookCmdsPath(dir, site, session), 'utf8')
  } catch {
    return []
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []
  const byId = new Map<string, NotebookCmd>()
  for (const raw of parsed) {
    const cmd = parseCmd(raw)
    if (cmd) byId.set(cmd.id, cmd)
  }
  return [...byId.values()]
}

/** 原子写（临时文件 + rename）——同会话的读改写不会看到半截 JSON。 */
function writeNotebookCmds(
  dir: string,
  site: NotebookSite,
  session: string,
  cmds: NotebookCmd[],
): void {
  const file = notebookCmdsPath(dir, site, session)
  mkdirSync(path.dirname(file), { recursive: true })
  const kept = cmds.slice(-CMD_MAX_RECORDS)
  const tmp = `${file}.tmp`
  writeFileSync(tmp, `${JSON.stringify(kept, null, 2)}\n`, 'utf8')
  renameSync(tmp, file)
}

/**
 * 记录一条命令（同 id 覆盖；超出 `CMD_MAX_RECORDS` 丢最旧）。
 * 返回写入后的命令（供调用方做证据行）。
 */
export function putNotebookCmd(dir: string, cmd: NotebookCmd): NotebookCmd {
  const all = readNotebookCmds(dir, cmd.site, cmd.session)
  const idx = all.findIndex((c) => c.id === cmd.id)
  if (idx >= 0) all[idx] = cmd
  else all.push(cmd)
  writeNotebookCmds(dir, cmd.site, cmd.session, all)
  return cmd
}

/** 仍应下发的命令：pending 且在 TTL 内（老到过期的 pending 视为失效，但不改写文件 —— 证据留着）。 */
export function pendingNotebookCmds(
  dir: string,
  site: NotebookSite,
  session: string,
  now: number,
  ttlMs = CMD_TTL_MS,
): NotebookCmd[] {
  return readNotebookCmds(dir, site, session).filter(
    (c) => c.status === 'pending' && now - c.at <= ttlMs,
  )
}

/**
 * 记 ack（op `cmd:stop-session id=<id> ok|fail …`）。找不到该 id ⇒ false（不造记录）。
 * 已经 ack 过的同 id 再 ack 一次：按最后一次为准（幂等，页面重试不会炸）。
 */
export function ackNotebookCmd(
  dir: string,
  site: NotebookSite,
  session: string,
  id: string,
  status: 'ok' | 'fail',
  reason?: string,
): boolean {
  const all = readNotebookCmds(dir, site, session)
  const hit = all.find((c) => c.id === id)
  if (!hit) return false
  putNotebookCmd(dir, { ...hit, status, ...(reason ? { reason } : {}) })
  return true
}

/** op `cmd:stop-session` 的 detail 形状：`id=<id> ok` / `id=<id> fail <原因>`。 */
export function parseAckDetail(detail: string): { id: string; status: 'ok' | 'fail' } | null {
  const m = /^id=([A-Za-z0-9_-]{1,64})\s+(ok|fail)\b/.exec(detail.trim())
  if (!m) return null
  return { id: m[1]!, status: m[2] === 'ok' ? 'ok' : 'fail' }
}

/** 会话的 cell 输出文件（`<dir>/<site>/<session>.log`；分类读它的尾巴）。 */
function sessionLogPath(dir: string, site: NotebookSite, session: string): string {
  const invalid = validateNotebookSession(session)
  if (invalid) throw new Error(`[notebook-cmd] ${invalid}`)
  if (!isNotebookSite(site)) throw new Error(`[notebook-cmd] site 非法：${site}`)
  const root = path.resolve(dir)
  const base = path.resolve(root, site, session)
  if (!base.startsWith(root + path.sep)) throw new Error('notebook 日志路径越界')
  return `${base}.log`
}

/** 读文件尾巴（有界：只读最后 `maxBytes`；文件不存在/读失败 ⇒ 空串）。 */
function readTailText(file: string, maxBytes: number): string {
  let size: number
  try {
    size = statSync(file).size
  } catch {
    return ''
  }
  if (size <= 0) return ''
  const start = Math.max(0, size - maxBytes)
  const len = size - start
  let fd: number
  try {
    fd = openSync(file, 'r')
  } catch {
    return ''
  }
  try {
    const buf = Buffer.alloc(len)
    const got = readSync(fd, buf, 0, len, start)
    // 尾读可能把首个多字节字符切成两半：只影响第一个字，不影响标记匹配
    return buf.subarray(0, got).toString('utf8')
  } catch {
    return ''
  } finally {
    closeSync(fd)
  }
}

/**
 * 这个会话是不是「PPO worker 页」——日志尾巴里有 worker 标记才算。
 * fail closed：日志缺席/读不到 ⇒ false（宁可不问，也不停一个说不准的页）。
 */
export function isPpoWorkerSession(
  dir: string,
  site: NotebookSite,
  session: string,
  opts: { tailBytes?: number } = {},
): boolean {
  const tail = readTailText(
    sessionLogPath(dir, site, session),
    opts.tailBytes ?? PPO_WORKER_LOG_TAIL_BYTES,
  )
  if (!tail) return false
  return PPO_WORKER_LOG_MARKERS.some((m) => tail.includes(m))
}

/** 这条 op 是不是「离线训练开始 + 本页要求停其它 PPO worker」。 */
export function parseStopOthers(op: Pick<NotebookOp, 'op' | 'detail'>): boolean {
  if (op.op !== 'offline:cell-start') return false
  return /(?:^|\s)stopOthers=1(?:\s|$)/.test(op.detail ?? '')
}

/** 活会话（`<dir>/<site>/*.ops.jsonl` 的 mtime 在 `maxIdleMs` 内；跳过 `.1.ops.jsonl` 备份段）。 */
export function liveNotebookSessions(
  dir: string,
  now: number,
  maxIdleMs = STOP_OTHERS_MAX_IDLE_MS,
): NotebookCmdTarget[] {
  const out: NotebookCmdTarget[] = []
  let sites: string[]
  try {
    sites = readdirSync(dir, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
  } catch {
    return out
  }
  for (const siteName of sites) {
    if (!(NOTEBOOK_SITES as readonly string[]).includes(siteName)) continue
    const site = siteName as NotebookSite
    let files: string[]
    try {
      files = readdirSync(path.join(dir, site))
    } catch {
      continue
    }
    for (const file of files) {
      if (!file.endsWith('.ops.jsonl')) continue
      // `.1.ops.jsonl` 是轮转备份段（不是活着的证据）
      const session = file.slice(0, -'.ops.jsonl'.length)
      if (session.endsWith('.1')) continue
      if (validateNotebookSession(session) !== null) continue
      const abs = path.join(dir, site, file)
      let mtime: number
      try {
        mtime = statSync(abs).mtimeMs
      } catch {
        continue
      }
      if (now - mtime > maxIdleMs) continue
      out.push({ site, session, mtime })
    }
  }
  return out.sort((a, b) => b.mtime - a.mtime)
}

export interface StopOthersResult {
  created: NotebookCmd[]
  /** 目标集合（含被跳过的原因，人读日志用）。 */
  candidates: number
  skippedPending: number
  /** 活着但不是 PPO worker 页（只搬日志的普通页 / 离线页 / rollout 节点）。 */
  skippedNotWorker: number
}

/**
 * 给**其它**活会话各派一条 `stop-session`（发起者自己除外）。
 *
 * 目标必须**同时**满足：10 分钟内有 op 写入（活着）∧ 日志尾巴里有 worker 标记（真的是 PPO
 * worker 页）。`requirePpoWorker: false` 只留给排障/测试（默认只停 worker 页）。
 *
 * 幂等：目标已有 TTL 内的 pending 命令 ⇒ 跳过（同一轮 cell-start 被重放也不会连点）。
 * 上限 `STOP_OTHERS_MAX_TARGETS`（按 mtime 新的优先）。
 */
export function enqueueStopOthers(
  dir: string,
  requester: { site: NotebookSite; session: string },
  now: number,
  opts: {
    maxTargets?: number
    maxIdleMs?: number
    ttlMs?: number
    id?: () => string
    requirePpoWorker?: boolean
    tailBytes?: number
  } = {},
): StopOthersResult {
  const maxTargets = Math.max(1, opts.maxTargets ?? STOP_OTHERS_MAX_TARGETS)
  const ttlMs = opts.ttlMs ?? CMD_TTL_MS
  const requirePpoWorker = opts.requirePpoWorker ?? true
  const newId = opts.id ?? (() => `cmd-${now.toString(36)}-${randomUUID().slice(0, 8)}`)
  const targets = liveNotebookSessions(dir, now, opts.maxIdleMs ?? STOP_OTHERS_MAX_IDLE_MS).filter(
    (t) => !(t.site === requester.site && t.session === requester.session),
  )
  const created: NotebookCmd[] = []
  let skippedPending = 0
  let skippedNotWorker = 0
  for (const t of targets) {
    if (created.length >= maxTargets) break
    if (
      requirePpoWorker &&
      !isPpoWorkerSession(
        dir,
        t.site,
        t.session,
        opts.tailBytes === undefined ? {} : { tailBytes: opts.tailBytes },
      )
    ) {
      skippedNotWorker += 1
      continue
    }
    const has = pendingNotebookCmds(dir, t.site, t.session, now, ttlMs).some(
      (c) => c.action === 'stop-session',
    )
    if (has) {
      skippedPending += 1
      continue
    }
    created.push(
      putNotebookCmd(dir, {
        v: 1,
        id: newId(),
        site: t.site,
        session: t.session,
        action: 'stop-session',
        at: now,
        by: `${requester.site}:${requester.session}`,
        status: 'pending',
      }),
    )
  }
  return { created, candidates: targets.length, skippedPending, skippedNotWorker }
}

/** `stop:others` 证据行的 detail（写进发起会话的 ops 日志，和 cell-start 同一条时间线）。 */
export function stopOthersEvidence(result: StopOthersResult): string {
  const names = result.created.map((c) => `${c.site}:${c.session}`).join(',')
  const tail =
    `${result.skippedPending > 0 ? ` skipped_pending=${result.skippedPending}` : ''}` +
    `${result.skippedNotWorker > 0 ? ` skipped_non_worker=${result.skippedNotWorker}` : ''}`
  return `targets=${result.created.length}/${result.candidates}${names ? ` ${names}` : ''}${tail}`.slice(
    0,
    400,
  )
}

/** 命令文件是否已存在（prune 时用来顺手清掉孤儿）。 */
export function notebookCmdsFileExists(dir: string, site: NotebookSite, session: string): boolean {
  return existsSync(notebookCmdsPath(dir, site, session))
}
