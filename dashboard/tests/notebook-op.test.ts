/**
 * notebook-op.test.ts — 操作日志端点（`POST /api/notebookOp`）的行为契约。
 *
 * 要点：**结构化 append-only**（`.ops.jsonl`）、写盘前脱敏/截断、单文件有界可轮转、
 * 与 `.log`（cell 搬运）完全解耦（不改 meta、不占 epoch），并且能在日志页尾巴上看得到。
 */
import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import {
  appendNotebookChunk,
  notebookLogPayload,
  pruneNotebookLogs,
  REDACTED,
  type NotebookChunk,
} from '../src/server/api/notebook-log'
import {
  appendNotebookOp,
  notebookOpsFilePath,
  OP_MAX_DETAIL,
  parseNotebookOp,
  readNotebookOpsTail,
  withNotebookOps,
  type NotebookOp,
} from '../src/server/api/notebook-op'

const T0 = 1_790_000_000_000
const temps: string[] = []
const tempDir = (): string => {
  const d = mkdtempSync(path.join(tmpdir(), 'bnco-'))
  temps.push(d)
  return d
}
afterAll(() => {
  for (const d of temps) rmSync(d, { recursive: true, force: true })
})

const op = (o: Partial<NotebookOp> = {}): NotebookOp => ({
  v: 1,
  site: 'kaggle',
  session: 'kaggle-owner-slug-edit-ab12',
  page: 'ab12',
  writer: 'host-ab12',
  at: T0,
  op: 'cmd:flush',
  ...o,
})

const raw = (o: Record<string, unknown> = {}): Record<string, unknown> => ({
  ...op(),
  ...o,
})

describe('操作日志：解析与校验', () => {
  it('合法一条 ⇒ 原样接受（detail 可选）', () => {
    const r = parseNotebookOp(raw({ detail: 'ok' }))
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.op).toMatchObject({ op: 'cmd:flush', detail: 'ok', page: 'ab12' })
  })

  it('写盘前脱敏：detail 里的凭据形态被替换成占位符', () => {
    const r = parseNotebookOp(raw({ detail: 'token=ghp_abcdefghijklmnopqrstuvwx' }))
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.op.detail).toContain(REDACTED)
      expect(r.op.detail).not.toContain('ghp_abcdef')
    }
  })

  it('detail 拍单行并截断（JSONL 一行一条）', () => {
    const r = parseNotebookOp(raw({ detail: `a\nb\tc${'x'.repeat(OP_MAX_DETAIL + 20)}` }))
    if (!r.ok) throw new Error('应当解析成功')
    expect(r.op.detail).not.toContain('\n')
    expect(r.op.detail).not.toContain('\t')
    expect(r.op.detail?.length).toBe(OP_MAX_DETAIL)
  })

  it('非法字段 ⇒ 400 + 人读原因（不写盘）', () => {
    const bad: Array<Record<string, unknown>> = [
      raw({ v: 2 }),
      raw({ site: 'bogus' }),
      raw({ session: '../escape' }),
      raw({ op: 'has space' }),
      raw({ op: '' }),
      raw({ page: 'A B' }),
      raw({ writer: 'w/1' }),
    ]
    for (const body of bad) {
      const r = parseNotebookOp(body)
      expect(r.ok).toBe(false)
      if (!r.ok) expect(r.status).toBe(400)
    }
    expect(parseNotebookOp(null).ok).toBe(false)
    expect(parseNotebookOp([1, 2]).ok).toBe(false)
  })
})

describe('操作日志：落盘 / 轮转 / 尾巴', () => {
  it('append ⇒ 一行一条 JSONL；tail 按人话回放（坏行原样保留）', () => {
    const dir = tempDir()
    appendNotebookOp(dir, op({ op: 'boot' }), T0)
    const { bytes } = appendNotebookOp(dir, op({ op: 'cmd:runCell', detail: 'ok' }), T0 + 1000)
    expect(bytes).toBeGreaterThan(0)
    const file = notebookOpsFilePath(dir, 'kaggle', 'kaggle-owner-slug-edit-ab12')
    const lines = readFileSync(file, 'utf8').trim().split('\n')
    expect(lines).toHaveLength(2)
    expect(JSON.parse(lines[0]!).op).toBe('boot')

    const tail = readNotebookOpsTail(dir, 'kaggle', 'kaggle-owner-slug-edit-ab12', 10)
    expect(tail).toHaveLength(2)
    expect(tail[1]).toContain('cmd:runCell')
    expect(tail[1]).toContain('ab12/host-ab12')
    expect(tail[1]).toContain('— ok')
    expect(tail[1]).toMatch(/^\[op \d{4}-\d{2}-\d{2}T/)

    // 文件不存在 ⇒ 空数组（日志页不报错）
    expect(readNotebookOpsTail(dir, 'kaggle', 'kaggle-nobody-edit', 10)).toEqual([])
  })

  it('超过上限 ⇒ 轮转成 .1.ops.jsonl（只留一代，文件有界）', () => {
    const dir = tempDir()
    const opts = { maxBytes: 400 }
    for (let i = 0; i < 6; i++) appendNotebookOp(dir, op({ op: `cmd:x${i}` }), T0 + i, opts)
    const file = notebookOpsFilePath(dir, 'kaggle', 'kaggle-owner-slug-edit-ab12')
    const backup = file.replace(/\.ops\.jsonl$/, '.1.ops.jsonl')
    expect(statSync(file).size).toBeLessThanOrEqual(400)
    expect(statSync(backup).size).toBeGreaterThan(0)
    // 只留一代：再轮转一次也不产生 `.2.*`
    for (let i = 0; i < 6; i++) appendNotebookOp(dir, op({ op: `cmd:y${i}` }), T0 + 100 + i, opts)
    expect(statSync(backup).size).toBeGreaterThan(0)
  })

  it('不碰搬运会话的 meta：先写 chunk 再写 op，meta.bytes 不变（不会被当成多余字节截掉）', () => {
    const dir = tempDir()
    const chunk: NotebookChunk = {
      v: 2,
      site: 'kaggle',
      session: 'kaggle-owner-slug-edit-ab12',
      writer: 'w1',
      epoch: 0,
      cell: 'cell-a',
      cellIndex: 1,
      seq: 1,
      mode: 'reset',
      offsetStart: 0,
      offsetEnd: 6,
      text: 'hello\n',
      dropped: 0,
      at: T0,
    }
    appendNotebookChunk(dir, chunk, T0)
    const metaPath = path.join(dir, 'kaggle', 'kaggle-owner-slug-edit-ab12.meta.json')
    const before = JSON.parse(readFileSync(metaPath, 'utf8')) as { bytes: number }
    appendNotebookOp(dir, op(), T0 + 1)
    const after = JSON.parse(readFileSync(metaPath, 'utf8')) as { bytes: number }
    expect(after.bytes).toBe(before.bytes)
  })

  it('日志页尾巴：notebook 载荷接上「操作日志」段（带横幅，不被误当 cell 输出）', () => {
    const dir = tempDir()
    const chunk: NotebookChunk = {
      v: 2,
      site: 'kaggle',
      session: 'kaggle-owner-slug-edit-ab12',
      writer: 'w1',
      epoch: 0,
      cell: 'cell-a',
      cellIndex: 1,
      seq: 1,
      mode: 'reset',
      offsetStart: 0,
      offsetEnd: 6,
      text: 'hello\n',
      dropped: 0,
      at: T0,
    }
    appendNotebookChunk(dir, chunk, T0)
    appendNotebookOp(dir, op({ op: 'schedule:add', detail: 'run-cell 04:00' }), T0 + 1)
    const base = notebookLogPayload(dir, 'notebook', 100)
    const withOps = withNotebookOps(dir, 'notebook', base)
    expect(withOps?.lines.join('\n')).toContain('hello')
    expect(withOps?.lines.join('\n')).toContain('操作日志（最近 1 条')
    expect(withOps?.lines.join('\n')).toContain('schedule:add')
    // 没有 op ⇒ 载荷原样（不多一段空横幅）
    expect(withNotebookOps(dir, 'notebook:missing', base)).toBe(base)
  })

  it('prune：过期会话连 .ops.jsonl 一起删（与 .log 同寿命）', () => {
    const dir = tempDir()
    appendNotebookOp(dir, op(), T0)
    const file = notebookOpsFilePath(dir, 'kaggle', 'kaggle-owner-slug-edit-ab12')
    expect(statSync(file).isFile()).toBe(true)
    const chunk: NotebookChunk = {
      v: 2,
      site: 'kaggle',
      session: 'kaggle-owner-slug-edit-ab12',
      writer: 'w1',
      epoch: 0,
      cell: 'cell-a',
      cellIndex: 1,
      seq: 1,
      mode: 'reset',
      offsetStart: 0,
      offsetEnd: 2,
      text: 'x\n',
      dropped: 0,
      at: T0,
    }
    appendNotebookChunk(dir, chunk, T0)
    // 盘子上的 mtime 是真实的「现在」，所以判过期要拿真实时间 + 8 天（不能用夹具的 T0）
    const eightDays = 8 * 24 * 3600_000
    expect(pruneNotebookLogs(dir, Date.now() + eightDays, 7 * 24 * 3600_000, 0)).toBe(1)
    expect(() => statSync(file)).toThrow()
  })
})
