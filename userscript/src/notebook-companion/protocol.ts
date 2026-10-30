/** protocol.ts — 页面 → dashboard 日志协议 v2（与 dashboard 侧各自实现，不跨项目 import）。 */

export type NotebookSite = 'kaggle' | 'colab' | 'aistudio'
export type NotebookMode = 'append' | 'reset' | 'clear' | 'probe'

export interface NotebookChunk {
  v: 2
  site: NotebookSite
  session: string
  notebook?: string
  /** 每次页面加载随机生成；服务端把它当 fencing token。 */
  writer: string
  epoch: number
  cell?: string
  cellIndex?: number
  seq: number
  /** 本块在归一文本里的起始（UTF-16 码元）；append 必须等于服务端 chars。 */
  offsetStart: number
  /** offsetStart + text.length。 */
  offsetEnd: number
  mode: NotebookMode
  text: string
  /** 仅 reset：跳到最新时显式丢弃的字节数。 */
  dropped?: number
  at: number
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
  /** resume 握手：客户端用它判断能否续写（tail 是脱敏后的末尾 ≤64 字符）。 */
  cell?: { seq: number; chars: number; tail: string }
}

export type NotebookResponse = NotebookAck | NotebookConflict

/** 与服务端 §4.5 同口径（客户端切分 48 KiB；body 上限是服务端自读的防线）。 */
export const MAX_TEXT_BYTES = 48 * 1024
export const MAX_PROBE_BYTES = 8 * 1024
export const MAX_BODY_BYTES = 512 * 1024
