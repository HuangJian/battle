/**
 * stop-cmd.ts — 下行命令轮询：让**其它**页在离线训练开始后停掉自己的会话。
 *
 * 背景（`DECISIONS §2026-11-09-goalnn-offline-start-report` 的下一阶段）：离线训练页
 * （`battle.offline.ipynb`）的训练 cell 一开跑，就报 `offline:cell-start … stopOthers=1`；
 * dashboard 收到后给**其它最近活跃的会话**各记一条 `stop-session` 命令（见
 * `dashboard/src/server/api/notebook-cmd.ts`）。浏览器跨页没法互停，所以各页自己来取：
 *
 *   GET /api/notebookCmds?site=&session=   → `{ok, cmds:[{id, action, at, by}]}`
 *   （停完会话）POST /api/notebookOp 落一行 `cmd:stop-session id=<id> ok|fail …`  ← 这行就是 ack
 *
 * 四条纪律：
 *   ① **不确定自己是谁就不发**：host 在 frame 握手前 `session()` 是空串 ⇒ 这一拍跳过（与 op 日志同口径）；
 *   ② **同一 id 只停一次**：内存集合挡住重复下发；ack 送出后服务端也会置 done（双保险）；
 *   ③ **失败也要说**：停不掉就 ack `fail` + 原因，绝不静默吞掉（页面上「刚被叫停却还在跑」是最坏的）；
 *   ④ **不拖累主流程**：网络错/坏 JSON 直接当这一拍没取到，下一拍再来（15s 一次，最多一条 GET）。
 */
import type { Runtime } from '../../runtime'
import type { NotebookSite } from '../protocol'

/** 默认轮询间隔：15s 够快（对方最多晚 15s 停），也不至于把 dashboard 打满。 */
export const STOP_CMD_POLL_MS = 15_000
/** 首次轮询延时：等 boot 的 op/日志上送先走一轮（只是一拍，不要拉长到「感觉没反应」）。 */
export const STOP_CMD_FIRST_MS = 4_000
const CMD_ID_RE = /^[A-Za-z0-9_-]{1,64}$/

export interface StopCmd {
  id: string
  action: string
  at: number
  by: string
}

export interface StopCmdResult {
  ok: boolean
  reason?: string
  detail?: string
}

export interface StopCmdDeps {
  runtime: Pick<Runtime, 'gmRequest'>
  dashboardBase: string
  site: NotebookSite
  /** 现算：host 的会话名要等 frame 握手后才知道；空串 ⇒ 这一拍不发请求。 */
  session: () => string
  /** 执行「停会话」：各页自己的实现（Kaggle 点外层 Stop，其余点会话菜单）。 */
  stop: () => Promise<StopCmdResult>
  /** 上送 op（ack 就是这一行）。 */
  onEvent: (op: string, detail?: string) => void
  intervalMs?: number
  firstDelayMs?: number
}

export interface StopCmdPoller {
  start(): void
  stop(): void
  /** 立刻取一次；返回本次真正执行了几条命令（测试/诊断用）。 */
  pollOnce(): Promise<number>
}

/** 解析取件响应：只要 `{ok:true, cmds:[…]}` 形状里的合法 stop-session 行，其余一律忽略。 */
export function parseStopCmds(body: unknown): StopCmd[] {
  if (typeof body !== 'object' || body === null) return []
  const rec = body as { ok?: unknown; cmds?: unknown }
  if (rec.ok !== true || !Array.isArray(rec.cmds)) return []
  const out: StopCmd[] = []
  for (const raw of rec.cmds) {
    if (typeof raw !== 'object' || raw === null) continue
    const c = raw as Record<string, unknown>
    if (typeof c.id !== 'string' || !CMD_ID_RE.test(c.id)) continue
    const action = typeof c.action === 'string' ? c.action : ''
    if (action !== 'stop-session') continue
    out.push({
      id: c.id,
      action,
      at: typeof c.at === 'number' && Number.isFinite(c.at) ? c.at : 0,
      by: typeof c.by === 'string' ? c.by.slice(0, 96) : '',
    })
  }
  return out
}

/** ack 行：`id=<id> ok` / `id=<id> fail <原因>`（服务端 `parseAckDetail` 同一口径）。 */
export function ackLine(id: string, result: StopCmdResult): string {
  const tail = result.ok
    ? result.detail
      ? ` ${result.detail}`
      : ''
    : ` ${result.reason ?? '失败'}`
  return `id=${id} ${result.ok ? 'ok' : 'fail'}${tail}`.replace(/[\r\n]+/g, ' ').slice(0, 400)
}

export function createStopCmdPoller(deps: StopCmdDeps): StopCmdPoller {
  const intervalMs = Math.max(1000, deps.intervalMs ?? STOP_CMD_POLL_MS)
  const firstDelayMs = Math.max(0, deps.firstDelayMs ?? STOP_CMD_FIRST_MS)
  const handled = new Set<string>()
  let timer: ReturnType<typeof setInterval> | null = null
  let first: ReturnType<typeof setTimeout> | null = null
  let busy = false
  let stopped = false

  const runOne = async (cmd: StopCmd): Promise<void> => {
    handled.add(cmd.id)
    let result: StopCmdResult
    try {
      result = await deps.stop()
    } catch (e) {
      result = { ok: false, reason: e instanceof Error ? e.message : String(e) }
    }
    deps.onEvent('cmd:stop-session', `${ackLine(cmd.id, result)}${cmd.by ? ` by=${cmd.by}` : ''}`)
  }

  const pollOnce = async (): Promise<number> => {
    if (busy || stopped) return 0
    const session = deps.session()
    // 还不知道自己的会话名（host 等 frame 握手）⇒ 不发请求：拿别人的命令去停自己是最坏的
    if (!session) return 0
    busy = true
    try {
      let body: unknown
      try {
        const r = await deps.runtime.gmRequest({
          method: 'GET',
          url:
            `${deps.dashboardBase}/api/notebookCmds` +
            `?site=${encodeURIComponent(deps.site)}&session=${encodeURIComponent(session)}`,
          headers: { 'X-Notebook-Companion': '1' },
          timeout: 5000,
        })
        if (r.status < 200 || r.status >= 300) return 0
        body = JSON.parse(r.responseText) as unknown
      } catch {
        return 0
      }
      const cmds = parseStopCmds(body).filter((c) => !handled.has(c.id))
      for (const cmd of cmds) {
        // 一条一条来：停会话本身要几秒，别在同一个 tick 里并发点两次
        await runOne(cmd)
      }
      return cmds.length
    } finally {
      busy = false
    }
  }

  return {
    start() {
      if (stopped || timer !== null || first !== null) return
      first = setTimeout(() => {
        first = null
        void pollOnce()
      }, firstDelayMs)
      timer = setInterval(() => {
        void pollOnce()
      }, intervalMs)
    },
    stop() {
      stopped = true
      if (first !== null) clearTimeout(first)
      if (timer !== null) clearInterval(timer)
      first = null
      timer = null
    },
    pollOnce,
  }
}
