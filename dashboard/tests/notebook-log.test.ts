/**
 * notebook-log.test.ts — notebook 日志接收端（plan/notebook-userscript §4.5/§4.6/§6.2）。
 *
 * 分层：`src/server/api/notebook-log.ts`（纯协议 + 磁盘；dir/now/上限可注入）。
 * 这里钉住「静默丢账」的每一道闸：seq 去重、meta 持久化、epoch、写者租约、offset 连续性、
 * 两阶段 clear、脱敏、8 MiB 轮转、路径白名单、prune 与清单排序。
 */
import { afterAll, beforeEach, describe, expect, it } from 'bun:test'
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  utimesSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import {
  NOTEBOOK_LEASE_MS,
  appendNotebookChunk,
  listNotebookSessions,
  notebookSessionPaths,
  parseNotebookChunk,
  pruneNotebookLogs,
  redactSecrets,
  validateNotebookSession,
  type NotebookChunk,
} from '../src/server/api/notebook-log'

const T0 = 1_790_000_000_000
const SESSION = 'kaggle-owner-rollout-run1'
const SESSION2 = 'kaggle-owner-rollout-run2'

let dir = ''
const temps: string[] = []

beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), 'bnc-'))
  temps.push(dir)
})

afterAll(() => {
  for (const d of temps) rmSync(d, { recursive: true, force: true })
})

function chunk(o: Partial<NotebookChunk> = {}): NotebookChunk {
  const text = o.text ?? 'hello\n'
  const offsetStart = o.offsetStart ?? 0
  const base = {
    v: 2 as const,
    site: 'kaggle' as const,
    session: SESSION,
    writer: 'w1',
    epoch: 0,
    cell: 'cell-a',
    cellIndex: 1,
    seq: 1,
    mode: 'append' as const,
    offsetStart,
    offsetEnd: offsetStart + text.length,
    text,
    dropped: 0,
    at: T0,
  }
  return { ...base, ...o, text, offsetStart, offsetEnd: o.offsetEnd ?? offsetStart + text.length }
}

const paths = (session = SESSION) => notebookSessionPaths(dir, 'kaggle', session)
const logText = (session = SESSION) => readFileSync(paths(session).log, 'utf8')
const metaObj = (session = SESSION) =>
  JSON.parse(readFileSync(paths(session).meta, 'utf8')) as {
    epoch: number
    bytes: number
    cells: Record<string, { seq: number; chars: number; tail: string }>
  }

describe('notebook-log：append / reset / clear 的字节与段头', () => {
  it('reset 写 `# segment` 头 + 原文；append 只追加后缀；ack.bytes == 文件长度', () => {
    const r1 = appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'a\nb\n' }), T0)
    expect(r1.status).toBe(200)
    expect(r1.body.ok).toBe(true)
    const head = logText()
    expect(head).toContain('# segment cell=cell-a idx=1 mode=reset')
    expect(head).toContain(' at=')
    expect(head.endsWith('a\nb\n')).toBe(true)
    expect(statSync(paths().log).size).toBe(head.length)

    const r2 = appendNotebookChunk(dir, chunk({ text: 'c\n', seq: 2, offsetStart: 4 }), T0 + 1000)
    expect(r2.status).toBe(200)
    expect(logText()).toBe(`${head}c\n`)
  })

  it('重复 seq 幂等：不再写盘，回 duplicate（活动写者）', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    const before = logText()
    const first = appendNotebookChunk(dir, chunk({ text: 'd', seq: 2, offsetStart: 3 }), T0 + 1)
    expect(first.status).toBe(200)
    const afterFirst = logText()
    const again = appendNotebookChunk(dir, chunk({ text: 'd', seq: 2, offsetStart: 3 }), T0 + 2)
    expect(again.status).toBe(200)
    expect(again.body).toMatchObject({ ok: true, duplicate: true })
    expect(logText()).toBe(afterFirst)
    expect(afterFirst).toBe(`${before}d`)
  })

  it('meta 落盘：seq/chars/tail 可跨进程重读（重启后去重仍生效）', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    appendNotebookChunk(dir, chunk({ text: 'def', seq: 2, offsetStart: 3 }), T0 + 1)
    const meta = metaObj()
    expect(meta.cells['cell-a']).toMatchObject({ seq: 2, chars: 6, tail: 'def' })
    const dup = appendNotebookChunk(dir, chunk({ text: 'def', seq: 2, offsetStart: 3 }), T0 + 2)
    expect(dup.body).toMatchObject({ duplicate: true })
  })
})

describe('notebook-log：epoch / 租约 / offset', () => {
  it('append 带旧 epoch ⇒ 409 stale-epoch + 当前 epoch', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    const r = appendNotebookChunk(
      dir,
      chunk({ text: 'd', seq: 2, offsetStart: 3, epoch: 9 }),
      T0 + 1,
    )
    expect(r.status).toBe(409)
    expect(r.body).toMatchObject({ ok: false, reason: 'stale-epoch', epoch: 0 })
  })

  it('写者租约：第二写者在租约内 409 writer-conflict；过期后可接管', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc', writer: 'w1' }), T0)
    const blocked = appendNotebookChunk(
      dir,
      chunk({ text: 'd', seq: 2, offsetStart: 3, writer: 'w2' }),
      T0 + 1000,
    )
    expect(blocked.status).toBe(409)
    if (!blocked.body.ok) {
      expect(blocked.body.reason).toBe('writer-conflict')
      expect(blocked.body.leaseRemainingMs ?? 0).toBeGreaterThan(0)
    }

    const taken = appendNotebookChunk(
      dir,
      chunk({ text: 'd', seq: 2, offsetStart: 3, writer: 'w2' }),
      T0 + NOTEBOOK_LEASE_MS + 1,
    )
    expect(taken.status).toBe(200)
    expect(logText().endsWith('d')).toBe(true)
  })

  it('offsetStart 不连续 ⇒ 409 offset-mismatch + 服务端游标（seq/chars/tail）', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    const r = appendNotebookChunk(dir, chunk({ text: 'z', seq: 2, offsetStart: 1 }), T0 + 1)
    expect(r.status).toBe(409)
    if (!r.body.ok) {
      expect(r.body.reason).toBe('offset-mismatch')
      expect(r.body.cell).toEqual({ seq: 1, chars: 3, tail: 'abc' })
    }
  })
})

describe('notebook-log：分块连续性 / 清除 / 轮转', () => {
  it('1 MiB 无换行：按 48 KiB 连续 append，拼接逐字节相等', () => {
    const piece = 'x'.repeat(48 * 1024)
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: piece }), T0)
    appendNotebookChunk(dir, chunk({ text: piece, seq: 2, offsetStart: piece.length }), T0 + 1)
    appendNotebookChunk(dir, chunk({ text: piece, seq: 3, offsetStart: piece.length * 2 }), T0 + 2)
    const file = logText()
    expect(file.endsWith(piece + piece + piece)).toBe(true)
    expect(metaObj().cells['cell-a']?.chars).toBe(piece.length * 3)
  })

  it('clear：epoch++、截断、删 `.1.log`、写 `# cleared`；旧 epoch 的下一条 append 被拒', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    writeFileSync(paths().backup, 'old-backup', 'utf8')
    const r = appendNotebookChunk(
      dir,
      chunk({ mode: 'clear', cell: undefined, seq: 9, offsetStart: 0, offsetEnd: 0, text: '' }),
      T0 + 1,
    )
    expect(r.status).toBe(200)
    expect(r.body).toMatchObject({ ok: true, epoch: 1 })
    expect(existsSync(paths().backup)).toBe(false)
    expect(logText()).toContain('# cleared at ')
    expect(metaObj()).toMatchObject({ epoch: 1, cells: {} })

    const stale = appendNotebookChunk(
      dir,
      chunk({ text: 'x', seq: 10, offsetStart: 0, epoch: 0 }),
      T0 + 2,
    )
    expect(stale.status).toBe(409)
    expect(stale.body).toMatchObject({ reason: 'stale-epoch', epoch: 1 })
  })

  it('超 8 MiB（测试注入小上限）⇒ 轮转成 `.1.log`，新段从头写', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'a'.repeat(80) }), T0, {
      maxLogBytes: 300,
    })
    const r = appendNotebookChunk(
      dir,
      chunk({ text: 'b'.repeat(200), seq: 2, offsetStart: 80 }),
      T0 + 1,
      { maxLogBytes: 300 },
    )
    expect(r.status).toBe(200)
    expect(existsSync(paths().backup)).toBe(true)
    expect(readFileSync(paths().backup, 'utf8')).toContain('a'.repeat(80))
    expect(logText()).toContain('b'.repeat(200))
    expect(logText()).not.toContain('a'.repeat(80))
  })
})

describe('notebook-log：脱敏 / 校验 / 路径 / prune / 清单', () => {
  it('脱敏：GitHub token / JWT / `?session=` 都不落盘，命中计数进响应', () => {
    const text =
      'x ghp_abcdefghijklmnopqrstuvwxyz0123 y ' +
      'eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0.signature123 ' +
      '?session=eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0.signature123\n'
    const r = appendNotebookChunk(dir, chunk({ mode: 'reset', text }), T0)
    expect(r.status).toBe(200)
    if (r.body.ok) expect(r.body.redacted ?? 0).toBeGreaterThanOrEqual(3)
    const file = logText()
    expect(file).not.toContain('ghp_')
    expect(file).not.toContain('?session=eyJ')
    expect(file).toContain('«redacted»')
  })

  it('脱敏是纯函数：命中返回计数，无命中 count=0 且原文不变', () => {
    expect(redactSecrets('plain text')).toEqual({ text: 'plain text', count: 0 })
    expect(redactSecrets('token: Bearer abcdefghijklmnopqrstuvwxyz').count).toBe(1)
  })

  it('session/site/字段校验：`..`、`.` 开头、Windows 保留名、大写、坏 offset 全拒', () => {
    for (const bad of ['..', '.hidden', 'con', 'nul', 'UPPER', 'a/b', '']) {
      expect(validateNotebookSession(bad)).not.toBeNull()
    }
    expect(validateNotebookSession(SESSION)).toBeNull()
    expect(validateNotebookSession('probe')).toBeNull()
    expect(validateNotebookSession('com1.log')).not.toBeNull()

    const bad = (o: Record<string, unknown>) =>
      parseNotebookChunk({ ...chunk({ mode: 'reset', text: 'abc' }), ...o })
    expect(bad({ site: 'github' }).ok).toBe(false)
    expect(bad({ session: '../evil' }).ok).toBe(false)
    expect(bad({ v: 1 }).ok).toBe(false)
    expect(bad({ offsetEnd: 2 }).ok).toBe(false)
    expect(bad({ mode: 'nope' }).ok).toBe(false)
    expect(bad({ cell: undefined }).ok).toBe(false)
    const tooBig = bad({ text: 'x'.repeat(48 * 1024 + 1) })
    expect(tooBig.ok).toBe(false)
    if (!tooBig.ok) expect(tooBig.status).toBe(413)
  })

  it('路径穿越：会话名非法时 `notebookSessionPaths` 直接抛（不 join 出上级目录）', () => {
    expect(() => notebookSessionPaths(dir, 'kaggle', '../evil')).toThrow()
    expect(() => notebookSessionPaths(dir, 'kaggle', 'con')).toThrow()
  })

  it('prune：删 7 天未写的会话（`.1.log`/meta 一起删），节流可注入', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    const old = new Date(T0 - 8 * 24 * 60 * 60 * 1000)
    utimesSync(paths().log, old, old)
    utimesSync(paths().meta, old, old)
    expect(pruneNotebookLogs(dir, T0, undefined, 0)).toBe(1)
    expect(existsSync(paths().log)).toBe(false)
    expect(existsSync(paths().meta)).toBe(false)
  })

  it('list：按 mtime 降序；跳过 `.1.log` 备份段', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'a' }), T0)
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'b', session: SESSION2 }), T0 + 5000)
    writeFileSync(paths().backup, 'backup', 'utf8')
    const list = listNotebookSessions(dir)
    expect(list.map((s) => s.session)).toEqual([SESSION2, SESSION])
    expect(list[0]?.site).toBe('kaggle')
    expect(list[0]?.bytes).toBeGreaterThan(0)
  })
})
