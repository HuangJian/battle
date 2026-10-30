/**
 * notebook-body.test.ts — 端点自读上限（plan/notebook-userscript §4.5 / 评审 luna P0-2）。
 *
 * Bun 1.4.4 的 `Bun.serve` 未设 `maxRequestBodySize` 时默认 128 MiB —— 「Bun 自动给 256 KiB
 * 上限」是假的。所以上限必须由本端点流式自读：超限立即 cancel + 413，**不先正 JSON.parse**。
 */
import { describe, expect, it } from 'bun:test'
import {
  NOTEBOOK_MAX_BODY_BYTES,
  NOTEBOOK_MAX_PROBE_BYTES,
  NOTEBOOK_MAX_TEXT_BYTES,
  parseNotebookChunk,
  readBoundedJson,
} from '../src/server/api/notebook-log'

function bodyOf(bytes: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(c) {
      for (const b of bytes) c.enqueue(b)
      c.close()
    },
  })
}

describe('notebook body：解析前中止', () => {
  it('无 Content-Length 的流式 body 超 512 KiB ⇒ 413（不是 parse 失败）', async () => {
    const piece = new Uint8Array(300 * 1024)
    const req = new Request('http://127.0.0.1:8900/api/notebookLog', {
      method: 'POST',
      body: bodyOf([piece, piece, piece]),
    })
    expect(req.headers.get('content-length')).toBeNull()
    const r = await readBoundedJson(req)
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(r.status).toBe(413)
      expect(r.reason).toContain(String(NOTEBOOK_MAX_BODY_BYTES))
    }
  })

  it('合法 JSON 小于上限照常解析（不误伤）', async () => {
    const req = new Request('http://127.0.0.1:8900/api/notebookLog', {
      method: 'POST',
      body: JSON.stringify({ v: 2, hello: 'world' }),
    })
    const r = await readBoundedJson(req)
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value).toMatchObject({ hello: 'world' })
  })

  it('坏 JSON 是 400（不是 413）', async () => {
    const req = new Request('http://127.0.0.1:8900/api/notebookLog', {
      method: 'POST',
      body: '{oops',
    })
    const r = await readBoundedJson(req)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.status).toBe(400)
  })
})

describe('notebook body：字段级上限', () => {
  const base = {
    v: 2,
    site: 'kaggle',
    session: 'kaggle-owner-rollout-run1',
    writer: 'w1',
    epoch: 0,
    cell: 'cell-a',
    cellIndex: 1,
    seq: 1,
    offsetStart: 0,
    offsetEnd: 0,
    mode: 'reset',
    text: '',
    dropped: 0,
    at: 1,
  }

  it('text > 48 KiB ⇒ 413', () => {
    const text = 'x'.repeat(NOTEBOOK_MAX_TEXT_BYTES + 1)
    const r = parseNotebookChunk({ ...base, text, offsetEnd: text.length })
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.status).toBe(413)
  })

  it('probe > 8 KiB ⇒ 413（探测 JSON 必须截断）', () => {
    const text = 'x'.repeat(NOTEBOOK_MAX_PROBE_BYTES + 1)
    const r = parseNotebookChunk({
      ...base,
      mode: 'probe',
      cell: undefined,
      text,
      offsetEnd: text.length,
    })
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.status).toBe(413)
  })

  it('offsetEnd ≠ offsetStart + text.length ⇒ 400（防错位拼接）', () => {
    const r = parseNotebookChunk({ ...base, text: 'abc', offsetEnd: 99 })
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.status).toBe(400)
  })
})
