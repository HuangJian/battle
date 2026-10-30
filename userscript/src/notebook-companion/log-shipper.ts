/**
 * log-shipper.ts — 增量搬运（plan §3.1/§4.5）。纯逻辑：不碰 DOM、不碰 GM_*（由调用方注入）。
 *
 * 三条不变量：
 *   ① 只发「已确认包含在服务端前缀里」的后缀：offset 只有在 200 后才前进；
 *   ② append 的 `offsetStart` 必须连续；409 的 `{seq, chars, tail}` 是 resume 握手的全部信息；
 *   ③ 永久错误（400/413）停发该 cell；网络错误只停这一拍，下一拍用同一 seq 重发。
 */
import { chunkText, hashRange, tailByBytes, utf8Bytes } from '../utils'
import type { NotebookChunk, NotebookResponse, NotebookSite } from './protocol'
import type { CellRef } from './sites/types'

export interface CellCursor {
  /** 已落盘前缀末尾（客户端归一文本的 UTF-16 偏移）。 */
  offset: number
  /** 尾部 256 字符的 FNV-1a（不是整段前缀；O(256)/拍）。 */
  tail: string
}

export interface ShipState {
  epoch: number
  cursors: Map<string, CellCursor>
  seqs: Map<string, number>
  /** writer-conflict 被动期（毫秒时间戳）：到期前不发。 */
  passiveUntil: number
  /** 永久失败的 cell → 原因（面板红字）。 */
  stopped: Map<string, string>
  lastError: string | null
  lastShipAt: number | null
}

export function createShipState(epoch = 0): ShipState {
  return {
    epoch,
    cursors: new Map(),
    seqs: new Map(),
    passiveUntil: 0,
    stopped: new Map(),
    lastError: null,
    lastShipAt: null,
  }
}

export interface CellDiff {
  mode: 'append' | 'reset'
  text: string
  offsetStart: number
  dropped: number
  /** 发送成功后应写入游标的 offset。 */
  nextOffset: number
}

/** 尾锚 diff：命中 ⇒ 只发后缀；长度回退/尾部变/无游标 ⇒ 整段 reset。 */
export function diffCell(
  dom: string,
  cursor: CellCursor | undefined,
  backlogDropBytes: number,
  keepBytes: number,
): CellDiff {
  const tailOk =
    cursor !== undefined &&
    dom.length >= cursor.offset &&
    hashRange(dom, Math.max(0, cursor.offset - 256), cursor.offset) === cursor.tail
  if (tailOk) {
    const suffix = dom.slice(cursor.offset)
    if (utf8Bytes(suffix) > backlogDropBytes && utf8Bytes(suffix) > keepBytes) {
      const kept = tailByBytes(dom, keepBytes)
      return {
        mode: 'reset',
        text: kept,
        offsetStart: dom.length - kept.length,
        dropped: utf8Bytes(suffix) - utf8Bytes(kept),
        nextOffset: dom.length,
      }
    }
    return {
      mode: 'append',
      text: suffix,
      offsetStart: cursor.offset,
      dropped: 0,
      nextOffset: dom.length,
    }
  }
  return { mode: 'reset', text: dom, offsetStart: 0, dropped: 0, nextOffset: dom.length }
}

export interface ShipDeps {
  site: NotebookSite
  session: string
  notebook?: string
  writer: string
  now: number
  send(chunk: NotebookChunk): Promise<{ status: number; body: NotebookResponse | null }>
  /** 每拍全局预算（字节）；默认 1 MiB。 */
  budgetBytes?: number
  maxChunkBytes?: number
  backlogDropBytes?: number
  keepBytes?: number
}

export interface ShipReport {
  sentBytes: number
  chunks: number
  errors: string[]
  passive: boolean
  stopped: string[]
}

export interface ShipInput extends ShipDeps {
  state: ShipState
  /** 只传 dirty cell（调用方已按 observer 过滤）。 */
  cells: Array<{ ref: CellRef; text: string }>
}

/**
 * 一拍：串行发送（不并行）。任一「非 200」按协议分支；永久错误写 `state.stopped`。
 * 返回本拍报告（面板/测试共用）。
 */
export async function shipOnce(input: ShipInput): Promise<ShipReport> {
  const { state, cells, send, now } = input
  const budget = input.budgetBytes ?? 1024 * 1024
  const maxChunk = input.maxChunkBytes ?? 48 * 1024
  const backlog = input.backlogDropBytes ?? 8 * 1024 * 1024
  const keep = input.keepBytes ?? 1024 * 1024
  const report: ShipReport = { sentBytes: 0, chunks: 0, errors: [], passive: false, stopped: [] }

  if (now < state.passiveUntil) {
    report.passive = true
    return report
  }

  for (const { ref, text } of cells) {
    const id = ref.id
    if (state.stopped.has(id)) continue
    const dom = text
    const diff = diffCell(dom, state.cursors.get(id), backlog, keep)
    const pieces = chunkText(diff.text, maxChunk)
    if (pieces.length === 0) {
      state.cursors.set(id, {
        offset: diff.nextOffset,
        tail: hashRange(dom, Math.max(0, diff.nextOffset - 256), diff.nextOffset),
      })
      continue
    }

    let offset = diff.offsetStart
    for (let i = 0; i < pieces.length; i++) {
      const piece = pieces[i]!
      const mode = diff.mode === 'reset' && i === 0 ? 'reset' : 'append'
      const seq = (state.seqs.get(id) ?? 0) + 1
      const chunk: NotebookChunk = {
        v: 2,
        site: input.site,
        session: input.session,
        ...(input.notebook === undefined ? {} : { notebook: input.notebook }),
        writer: input.writer,
        epoch: state.epoch,
        cell: id,
        ...(ref.index >= 0 ? { cellIndex: ref.index } : {}),
        seq,
        offsetStart: offset,
        offsetEnd: offset + piece.length,
        mode,
        text: piece,
        ...(mode === 'reset' ? { dropped: diff.dropped } : {}),
        at: now,
      }

      let res: { status: number; body: NotebookResponse | null }
      try {
        res = await send(chunk)
      } catch (e) {
        state.lastError = e instanceof Error ? e.message : String(e)
        report.errors.push(state.lastError)
        return report // 网络错误：本拍停；该块未确认 ⇒ 下一拍同 seq 重发
      }

      if (res.status === 200 && res.body?.ok) {
        state.epoch = res.body.epoch
        state.seqs.set(id, seq)
        offset = chunk.offsetEnd
        state.cursors.set(id, {
          offset,
          tail: hashRange(dom, Math.max(0, offset - 256), offset),
        })
        state.lastShipAt = now
        state.lastError = null
        report.sentBytes += utf8Bytes(piece)
        report.chunks += 1
        if (report.sentBytes >= budget) return report
        continue
      }

      const body = res.body
      const reason = body && !body.ok ? body.reason : `http-${res.status}`
      if (res.status === 409 && body && !body.ok && body.reason === 'writer-conflict') {
        state.passiveUntil = now + (body.leaseRemainingMs ?? 180_000) + 2000
        report.passive = true
        return report
      }
      if (res.status === 409 && body && !body.ok && body.reason === 'stale-epoch') {
        state.epoch = body.epoch
        state.cursors.delete(id) // 下一条从 reset 开始（明确告知，不静默丢）
        report.errors.push('stale-epoch')
        return report
      }
      if (res.status === 409 && body && !body.ok && body.reason === 'offset-mismatch') {
        const cursor = body.cell
        const tailHit =
          cursor !== undefined &&
          dom.length >= cursor.chars &&
          dom.slice(cursor.chars - cursor.tail.length, cursor.chars) === cursor.tail
        if (tailHit && cursor) {
          // resume：服务端已有前缀，续写；不重发历史
          state.cursors.set(id, {
            offset: cursor.chars,
            tail: hashRange(dom, Math.max(0, cursor.chars - 256), cursor.chars),
          })
          state.seqs.set(id, cursor.seq)
          report.errors.push('offset-resume')
        } else {
          state.cursors.delete(id)
          report.errors.push('offset-reset')
        }
        return report
      }
      if (res.status === 400 || res.status === 413) {
        state.stopped.set(id, reason)
        report.stopped.push(id)
        report.errors.push(reason)
        state.lastError = reason
        return report
      }
      state.lastError = reason
      report.errors.push(reason)
      return report
    }
  }
  return report
}
