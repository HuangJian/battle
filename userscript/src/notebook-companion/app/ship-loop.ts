/**
 * ship-loop.ts — frame 侧搬运循环（W2 的「能跑」面；纯逻辑 + 注入的 adapter/runtime）。
 *
 * 每拍只读 dirty cell；失败只影响该块（offset 不前进）；`saveAndClear` 是 drain → 重读 → clear，
 * **任一块失败绝不清空**（plan §3.2 / 评审 S3）。
 */
import type { Runtime } from '../../runtime'
import { normalizeNewlines, utf8Bytes } from '../../utils'
import type { BncConfig } from '../config'
import { createShipState, shipOnce, type ShipReport, type ShipState } from '../log-shipper'
import type { NotebookChunk, NotebookResponse, NotebookSite } from '../protocol'
import { parseKaggleSession, withPageTag } from '../sites/kaggle'
import { parseColabSession } from '../sites/colab'
import { parseAistudioSession } from '../sites/aistudio'
import {
  COLAB_OBSERVER,
  KAGGLE_OBSERVER,
  createOutputObserver,
  type OutputObserver,
} from '../observer'
import type { FrameAdapter } from '../sites/types'

export interface ShipLoopOptions {
  doc: Document
  href: string
  referrer?: string
  config: BncConfig
  runtime: Runtime
  adapter: FrameAdapter
  site?: NotebookSite
  /** 本标签页的短标签：多开同一 notebook 时各自的日志互不覆盖（boot 从 sessionStorage 取）。 */
  pageTag?: string
  writer?: string
  now?: () => number
  onReport?: (report: ShipReport, state: ShipState) => void
  onError?: (message: string) => void
}

export interface ShipLoop {
  readonly state: ShipState
  readonly session: string
  /** host 掌握外层 notebook URL：用它替换 frame 自己推出的会话基（修 kaggle-unknown-edit）。 */
  setSessionBase(base: string): void
  readonly dirty: Set<string>
  tick(): Promise<ShipReport | null>
  flushAll(deadlineMs?: number): Promise<{ ok: boolean; bytes: number; reason?: string }>
  saveAndClear(): Promise<{ ok: boolean; reason?: string; bytes: number }>
  clearDashboardSession(): Promise<{ ok: boolean; reason?: string }>
  autoClearIfNeeded(): Promise<{ cleared: string[]; deferred: boolean }>
  /** 周期性「保存并清空」：到间隔且空闲才做（config.autoSaveIntervalSec；0 = 关闭）。 */
  autoSaveIfNeeded(): Promise<{ saved: boolean; bytes: number; reason?: string }>
  /** 面板切换「自动保存并清空」：立即生效，周期从此刻重新起算。 */
  setAutoSave(enabled: boolean, intervalSec: number): void
  start(): void
  stop(): void
  lastInteractionAt(lastAt: number): void
}

const MAX_DRAIN_BYTES = 64 * 1024 * 1024
/** 409 的三个自愈分支：下一轮会重发（游标前进/归零），不该让整次保存失败。 */
const TRANSIENT_ERRORS = new Set(['offset-resume', 'offset-reset', 'stale-epoch'])
/** 连续这么多轮「没发出字节且输出长度不变」⇒ 真 offset 冲突。 */
const STALL_ROUNDS = 3

function randomWriter(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
  return `w-${(c?.randomUUID?.() ?? Math.random().toString(36)).replace(/[^a-z0-9]/gi, '').slice(0, 10)}`
}

/**
 * 创建一个 frame 搬运循环。**不自动 start**：boot 决定何时起（测试可以手动 tick）。
 */
export function createShipLoop(opts: ShipLoopOptions): ShipLoop {
  const site = opts.site ?? 'kaggle'
  const writer = opts.writer ?? randomWriter()
  // 会话键按站点解析：Colab 的 URL 是 /drive/<fileId>，AI Studio 是 /user/<uid>/<pid>/lab/
  // tree/<nb>，拿 Kaggle 那套只会得到 unknown
  const parseSession =
    site === 'aistudio'
      ? parseAistudioSession
      : site === 'colab'
        ? parseColabSession
        : parseKaggleSession
  let baseSession = parseSession(opts.referrer || opts.href)
  let session = opts.pageTag ? withPageTag(baseSession, opts.pageTag) : baseSession
  const now = opts.now ?? (() => Date.now())
  /** 上次周期保存的时刻；从创建起算，避免打开页面就清空一次。 */
  let lastAutoSaveAt = now()
  const state = createShipState(0)
  const dirty = new Set<string>()
  let observer: OutputObserver | null = null
  let timer: ReturnType<typeof setInterval> | null = null
  let lastInteraction = 0

  const cells = () => opts.adapter.cells().filter((c) => c.kind === 'code')
  const textOf = (id: string) => {
    const ref = cells().find((c) => c.id === id)
    return ref ? normalizeNewlines(opts.adapter.outputText(ref)) : ''
  }

  const send = async (
    chunk: NotebookChunk,
  ): Promise<{ status: number; body: NotebookResponse | null }> => {
    const res = await opts.runtime.gmRequest({
      method: 'POST',
      url: `${opts.config.dashboardBase}/api/notebookLog`,
      headers: { 'Content-Type': 'application/json', 'X-Notebook-Companion': '1' },
      data: JSON.stringify(chunk),
      timeout: 8000,
    })
    let body: NotebookResponse | null = null
    try {
      body = JSON.parse(res.responseText) as NotebookResponse
    } catch {
      body = null
    }
    return { status: res.status, body }
  }

  const runOnce = async (ids: string[]): Promise<ShipReport> => {
    const list = ids.map((id) => {
      const ref = cells().find((c) => c.id === id)
      return { ref: ref ?? { id, index: -1, kind: 'code' as const }, text: textOf(id) }
    })
    const report = await shipOnce({
      state,
      site,
      session,
      writer,
      now: now(),
      cells: list,
      send,
      budgetBytes: opts.config.maxBytesPerTick,
      maxChunkBytes: opts.config.maxChunkBytes,
      backlogDropBytes: opts.config.backlogDropBytes,
      keepBytes: opts.config.keepBytes,
    })
    if (report.passive) opts.onError?.('另一个标签在负责搬运（本标签被动）')
    if (state.lastError) opts.onError?.(state.lastError)
    opts.onReport?.(report, state)
    return report
  }

  const tick = async (): Promise<ShipReport | null> => {
    if (!opts.config.enabled) return null
    const ids = [...dirty]
    if (ids.length === 0) return null
    dirty.clear()
    return runOnce(ids)
  }

  const flushAll = async (
    deadlineMs = 60_000,
  ): Promise<{ ok: boolean; bytes: number; reason?: string }> => {
    const started = now()
    let bytes = 0
    /** 连续「没发出字节且游标没动」的轮数；输出还在长则归零（那是流式，不是冲突）。 */
    let stalled = 0
    let lastShape = ''
    for (let round = 0; round < 64; round++) {
      const ids = cells().map((c) => c.id)
      const report = await runOnce(ids)
      bytes += report.sentBytes
      // 另一个标签持有租约：本标签整轮不发 ⇒ 说清是「被动」，不能冒充 offset 冲突
      if (report.passive) {
        const left = Math.max(0, state.passiveUntil - now())
        return {
          ok: false,
          bytes,
          reason: `另一个页签持有搬运权（writer 冲突，约 ${Math.ceil(left / 1000)}s 后释放）：等它关掉或租约过期再保存`,
        }
      }
      // 409 的 resume/reset 与 stale-epoch 会在下一轮自动补发，不算「保存失败」
      const fatal = report.errors.find((e) => !TRANSIENT_ERRORS.has(e))
      if (fatal !== undefined) return { ok: false, bytes, reason: fatal }
      const stopped = [...state.stopped.values()][0]
      if (stopped !== undefined) return { ok: false, bytes, reason: stopped }
      if (bytes > MAX_DRAIN_BYTES)
        return { ok: false, bytes, reason: `drain 超 ${MAX_DRAIN_BYTES}B` }

      const lens = ids.map((id) => textOf(id).length)
      const behind = ids.filter((id, i) => (state.cursors.get(id)?.offset ?? 0) < (lens[i] ?? 0))
      if (behind.length === 0 && report.sentBytes === 0) return { ok: true, bytes }
      if (now() - started > deadlineMs)
        return { ok: false, bytes, reason: `drain 超时 ${deadlineMs}ms` }

      // 这一轮没发出字节但游标还在后面：输出可能刚在这拍的窗口里长出来（流式输出常见）
      // ⇒ 下一轮接着发；只有长度连续几轮不变才是真的 offset 冲突。
      const shape = lens.join(',')
      if (report.sentBytes === 0 && behind.length > 0) {
        stalled = shape === lastShape ? stalled + 1 : 0
        if (stalled >= STALL_ROUNDS) {
          return {
            ok: false,
            bytes,
            reason: `游标卡住：${behind.join(',')} 没有新字节也追不上（offset 冲突）`,
          }
        }
      } else {
        stalled = 0
      }
      lastShape = shape
    }
    return { ok: false, bytes, reason: 'drain 未排空（64 轮）' }
  }

  const saveAndClear = async (): Promise<{ ok: boolean; reason?: string; bytes: number }> => {
    const first = await flushAll()
    if (!first.ok)
      return { ok: false, bytes: first.bytes, reason: first.reason ?? '保存未完成，拒绝清空' }
    // 排空后重读一次：clear 与 kernel 输出之间没有同步屏障 ⇒ 最多两轮，缩小窗口。
    const second = await flushAll(10_000)
    if (!second.ok && second.reason !== 'drain 未排空（64 轮）') {
      return { ok: false, bytes: first.bytes + second.bytes, reason: second.reason }
    }
    const cleared = await opts.adapter.command('notebook:clear-all-cell-outputs')
    if (!cleared.ok) return { ok: false, bytes: first.bytes + second.bytes, reason: cleared.reason }
    for (const id of cells().map((c) => c.id)) state.cursors.delete(id)
    return { ok: true, bytes: first.bytes + second.bytes }
  }

  const clearDashboardSession = async (): Promise<{ ok: boolean; reason?: string }> => {
    const res = await send({
      v: 2,
      site,
      session,
      writer,
      epoch: 0,
      seq: 1,
      offsetStart: 0,
      offsetEnd: 0,
      mode: 'clear',
      text: '',
      dropped: 0,
      at: now(),
    })
    if (res.status !== 200 || !res.body?.ok) {
      return {
        ok: false,
        reason: res.body && !res.body.ok ? res.body.reason : `http-${res.status}`,
      }
    }
    state.epoch = res.body.epoch
    state.cursors.clear()
    return { ok: true }
  }

  const autoClearIfNeeded = async (): Promise<{ cleared: string[]; deferred: boolean }> => {
    const cleared: string[] = []
    let deferred = false
    for (const ref of cells()) {
      const text = textOf(ref.id)
      if (utf8Bytes(text) < opts.config.autoClearBytes) continue
      const cursor = state.cursors.get(ref.id)
      if (!cursor || cursor.offset < text.length) {
        deferred = true
        continue
      }
      // 闸 1：最近 10s 有交互（键盘/指针/滚动）就不抢选区；不做「到点强抢焦点」。
      if (now() - lastInteraction < 10_000) {
        deferred = true
        continue
      }
      const r = await opts.adapter.clearCellOutput(ref)
      if (r.ok) {
        cleared.push(ref.id)
        state.cursors.delete(ref.id)
      } else {
        deferred = true
      }
    }
    return { cleared, deferred }
  }

  /**
   * 周期性自动保存并清空：到间隔 + 近 10s 无交互 + 确有输出才做；
   * 没有输出就只重置计时；失败也等下一个周期（不每拍重试同一错误）。
   */
  const autoSaveIfNeeded = async (): Promise<{
    saved: boolean
    bytes: number
    reason?: string
  }> => {
    if (!opts.config.autoSaveEnabled) return { saved: false, bytes: 0 }
    const intervalMs = opts.config.autoSaveIntervalSec * 1000
    if (intervalMs <= 0) return { saved: false, bytes: 0 }
    const t = now()
    if (t - lastAutoSaveAt < intervalMs) return { saved: false, bytes: 0 }
    // 用户正在操作（键盘/指针/滚动）：不抢清空，下一拍再看
    if (t - lastInteraction < 10_000) return { saved: false, bytes: 0, reason: 'deferred' }
    if (!cells().some((c) => textOf(c.id) !== '')) {
      lastAutoSaveAt = t
      return { saved: false, bytes: 0 }
    }
    let r: { ok: boolean; bytes: number; reason?: string }
    try {
      r = await saveAndClear()
    } catch (e) {
      r = { ok: false, bytes: 0, reason: e instanceof Error ? e.message : String(e) }
    }
    lastAutoSaveAt = now()
    if (r.ok) return { saved: true, bytes: r.bytes }
    opts.onError?.(`自动保存并清空失败：${r.reason ?? '未排空'}`)
    return { saved: false, bytes: r.bytes, ...(r.reason ? { reason: r.reason } : {}) }
  }

  return {
    state,
    get session() {
      return session
    },
    setSessionBase(base: string) {
      const next = base.trim()
      // host 传来的基已由 parseKaggleSession 归一；这里只收安全字符，非法一律忽略
      if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(next) || next === baseSession) return
      baseSession = next
      session = opts.pageTag ? withPageTag(baseSession, opts.pageTag) : baseSession
      // 换键 = 换一份日志：游标/序号归零，重新从各 cell 当前输出开始写
      state.epoch = 0
      state.cursors.clear()
      state.seqs.clear()
      state.passiveUntil = 0
      state.stopped.clear()
      state.lastError = null
    },
    dirty,
    tick,
    flushAll,
    saveAndClear,
    clearDashboardSession,
    autoClearIfNeeded,
    autoSaveIfNeeded,
    setAutoSave(enabled: boolean, intervalSec: number) {
      opts.config.autoSaveEnabled = enabled
      opts.config.autoSaveIntervalSec = Math.min(24 * 3600, Math.max(0, Math.round(intervalSec)))
      lastAutoSaveAt = now()
    },
    lastInteractionAt(at: number) {
      lastInteraction = at
    },
    start() {
      if (timer !== null) return
      observer = createOutputObserver(
        opts.doc,
        (id) => dirty.add(id),
        () => {
          // 笔记本/虚拟化 cell 晚出现：没有游标的 cell 补标脏（首次整段 reset）；
          // 已有游标的虚拟化进出不重读全文（offset 键是 uuid，不动）。
          for (const ref of cells()) {
            if (!state.cursors.has(ref.id)) dirty.add(ref.id)
          }
        },
        site === 'colab' ? COLAB_OBSERVER : KAGGLE_OBSERVER,
      )
      // 打开页面时已有的输出不会触发 MutationObserver ⇒ 首拍必须把全部 code cell 标脏。
      for (const ref of cells()) dirty.add(ref.id)
      timer = setInterval(
        () => {
          void tick()
            .then(() => autoSaveIfNeeded())
            .then(() => autoClearIfNeeded())
        },
        Math.max(1, opts.config.shipIntervalSec) * 1000,
      )
      void tick()
    },
    stop() {
      observer?.disconnect()
      observer = null
      if (timer !== null) clearInterval(timer)
      timer = null
    },
  }
}
