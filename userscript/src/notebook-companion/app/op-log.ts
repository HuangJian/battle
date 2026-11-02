/**
 * op-log.ts — 操作日志上送：面板按钮 / 命令 / 调度事件 → dashboard `/api/notebookOp`。
 *
 * 为什么单独一条通道（不复用 `/api/notebookLog` 的搬运）：
 *   - 搬运是**有状态**的协议（epoch/offset/seq/租约，一个 writer 一段），插一行操作文本会污染
 *     cell 的游标；服务端 `recoverNotebookSession` 还会把「meta 之外多出来的字节」截掉；
 *   - 操作日志要的是**结构化 + append-only**（`<session>.ops.jsonl`）：排查时能按 op 名 grep，
 *     审计时能逐个定位「谁在什么时候点了什么」。
 *
 * 三条纪律：
 *   ① **绝不拖累主流程**：`log()` 只入队（同步、O(1)）；网络失败就留到下一拍，队列有界
 *      （满了丢最旧的，并在下一条里报 `dropped=N`）；
 *   ② **只有一次重试窗口**：不排队到天亮——页面关掉时队列就那么丢着，运维要的是「当时发生了什么」，
 *      不是补偿投递；
 *   ③ 上送内容**已在客户端裁剪**（op ≤64 字符、detail ≤400），服务端还会再脱敏一次。
 */
import type { Runtime } from '../../runtime'
import type { NotebookSite } from '../protocol'

export interface OpEntry {
  v: 1
  site: NotebookSite
  /** 会话键（不含 `notebook:` 前缀；与日志页同源）。 */
  session: string
  /** 本页标签（多开页签分得开）。 */
  page: string
  writer: string
  at: number
  op: string
  detail?: string
}

export interface OpLogDeps {
  runtime: Pick<Runtime, 'gmRequest'>
  dashboardBase: string
  site: NotebookSite
  /** 现算：host 的会话名要等 frame 握手后才知道。 */
  session: () => string
  page: string
  writer: string
  now?: () => number
  /** 入队后多久上送（默认 2.5s：把连续点击并成几条，不跟按钮抢网络）。 */
  flushMs?: number
  maxQueue?: number
}

export interface OpLog {
  log(op: string, detail?: string): void
  /** 立即上送（测试/卸载时用）。失败返回 false，剩余留在队列。 */
  flush(): Promise<boolean>
  pending(): number
  stop(): void
}

export const OP_MAX_DETAIL = 400
export const OP_MAX_OP = 64
export const OP_MAX_QUEUE = 64
const DEFAULT_FLUSH_MS = 2500
const OP_NAME_RE = /[^A-Za-z0-9:_.-]+/g

function cleanOp(op: string): string {
  return op.replace(OP_NAME_RE, '-').slice(0, OP_MAX_OP) || 'unknown'
}

/** detail 拍成单行（JSONL 一行一条；换行/制表符换可见字符，不丢信息结构）。 */
function cleanDetail(detail: string): string {
  return detail
    .replace(/[\r\n]+/g, ' ⏎ ')
    .replace(/\t/g, ' ')
    .slice(0, OP_MAX_DETAIL)
}

export function createOpLog(deps: OpLogDeps): OpLog {
  const now = deps.now ?? (() => Date.now())
  const flushMs = Math.max(250, deps.flushMs ?? DEFAULT_FLUSH_MS)
  const maxQueue = Math.max(1, deps.maxQueue ?? OP_MAX_QUEUE)
  const queue: OpEntry[] = []
  let dropped = 0
  let timer: ReturnType<typeof setInterval> | null = null
  let sending = false

  const send = async (entry: OpEntry): Promise<boolean> => {
    try {
      const r = await deps.runtime.gmRequest({
        method: 'POST',
        url: `${deps.dashboardBase}/api/notebookOp`,
        headers: { 'Content-Type': 'application/json', 'X-Notebook-Companion': '1' },
        data: JSON.stringify(entry),
        timeout: 5000,
      })
      return r.status >= 200 && r.status < 300
    } catch {
      return false
    }
  }

  const flush = async (): Promise<boolean> => {
    if (sending) return false
    sending = true
    try {
      while (queue.length > 0) {
        const entry = queue[0]!
        if (!(await send(entry))) return false
        queue.shift()
      }
      return true
    } finally {
      sending = false
    }
  }

  const log = (op: string, detail?: string): void => {
    const session = deps.session()
    // 会话名还没定（host 刚启动、frame 未握手）⇒ 宁可丢这一条，也不要写进一个假会话的日志
    if (!session) return
    if (queue.length >= maxQueue) {
      queue.shift()
      dropped += 1
    }
    const entry: OpEntry = {
      v: 1,
      site: deps.site,
      session,
      page: deps.page,
      writer: deps.writer,
      at: now(),
      op: cleanOp(op),
      ...(detail === undefined || detail === ''
        ? {}
        : { detail: cleanDetail(dropped > 0 ? `${detail}｜dropped=${dropped}` : detail) }),
    }
    if (detail === undefined && dropped > 0) entry.detail = `dropped=${dropped}`
    queue.push(entry)
    dropped = 0
    if (timer === null) {
      timer = setInterval(() => {
        void flush()
      }, flushMs)
    }
  }

  return {
    log,
    flush,
    pending: () => queue.length,
    stop() {
      if (timer !== null) clearInterval(timer)
      timer = null
      // 卸载前尽力投递一次（不等结果：页面正在卸载）
      void flush()
    },
  }
}
