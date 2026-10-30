/**
 * notebook-recovery.test.ts — 崩溃窗口（plan/notebook-userscript §4.6 / §6.2）。
 *
 * 注入式故障：直接改盘上状态（= 进程崩在两步之间），再发下一条消息，断言恢复按协议收敛：
 *   · append 已写、meta 未提交 ⇒ 尾巴截回 meta.bytes（客户端会重发同一块，不重复）；
 *   · clear 截断后崩 ⇒ 启动把 clear 做完（epoch 已是新值）；
 *   · rotate 改名后崩 ⇒ 启动把轮转做完；
 *   · 空 meta + 非空文件 / 文件比 meta 短 ⇒ epoch++ + 可见 `# recovered` 段头。
 */
import { afterAll, beforeEach, describe, expect, it } from 'bun:test'
import { appendFileSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import {
  appendNotebookChunk,
  notebookSessionPaths,
  readNotebookMeta,
  writeNotebookMeta,
  type NotebookChunk,
  type NotebookMeta,
} from '../src/server/api/notebook-log'

const T0 = 1_790_000_000_000
const SESSION = 'kaggle-owner-rollout-run1'
let dir = ''
const temps: string[] = []

beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), 'bncr-'))
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

const paths = () => notebookSessionPaths(dir, 'kaggle', SESSION)
const meta = (): NotebookMeta => readNotebookMeta(paths(), 'kaggle', SESSION).meta

describe('notebook 恢复：未提交的尾巴', () => {
  it('append 已写、meta 未提交 ⇒ 下次请求把尾巴截回 meta.bytes，重发不重复', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    const committed = meta().bytes
    // 模拟「文本已 append、回包前进程崩」：文件比 meta.bytes 长。
    appendFileSync(paths().log, 'UNCOMMITTED', 'utf8')
    expect(statSync(paths().log).size).toBeGreaterThan(committed)

    const r = appendNotebookChunk(dir, chunk({ text: 'def', seq: 2, offsetStart: 3 }), T0 + 1000)
    expect(r.status).toBe(200)
    const file = readFileSync(paths().log, 'utf8')
    expect(file).not.toContain('UNCOMMITTED')
    expect(file.endsWith('abcdef')).toBe(true)
    expect(meta().cells['cell-a']).toMatchObject({ seq: 2, chars: 6 })
  })
})

describe('notebook 恢复：两阶段破坏性操作', () => {
  it('clear 截断后崩（clearing 意图位）⇒ 下次请求把 clear 做完并写 recovered 头', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    const m = meta()
    m.clearing = true
    m.epoch += 1
    writeNotebookMeta(paths(), m)
    appendFileSync(paths().log, 'stale-write', 'utf8')

    const r = appendNotebookChunk(
      dir,
      chunk({ mode: 'reset', text: 'fresh', seq: 2, epoch: m.epoch }),
      T0 + 1000,
    )
    expect(r.status).toBe(200)
    const file = readFileSync(paths().log, 'utf8')
    expect(file).toContain('# cleared at ')
    expect(file).toContain('recovered=1')
    expect(file).not.toContain('stale-write')
    expect(meta().clearing).toBe(false)
  })

  it('rotate 改名后崩（rotating 意图位）⇒ 下次请求把轮转做完', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'old-segment' }), T0)
    const m = meta()
    m.rotating = true
    writeNotebookMeta(paths(), m)
    writeFileSync(paths().backup, 'previous-backup', 'utf8')

    const r = appendNotebookChunk(dir, chunk({ text: 'next', seq: 2, offsetStart: 11 }), T0 + 1000)
    expect(r.status).toBe(200)
    expect(meta().rotating).toBe(false)
    expect(readFileSync(paths().backup, 'utf8')).toContain('old-segment')
    expect(readFileSync(paths().log, 'utf8')).toContain('next')
  })
})

describe('notebook 恢复：外部截断 / 缺 meta', () => {
  it('缺 meta + 非空文件 ⇒ epoch++ + `# recovered … missing-meta`（旧账不静默续写）', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    rmSync(paths().meta)
    const r = appendNotebookChunk(
      dir,
      chunk({ text: 'def', seq: 2, offsetStart: 3, epoch: 0 }),
      T0 + 1000,
    )
    expect(r.status).toBe(409)
    if (!r.body.ok) expect(r.body).toMatchObject({ reason: 'stale-epoch', epoch: 1 })
    const file = readFileSync(paths().log, 'utf8')
    expect(file).toContain('reason=missing-meta')
    expect(meta().epoch).toBe(1)
    expect(meta().cells).toEqual({})
  })

  it('文件比 meta.bytes 短（无意图位）⇒ epoch++ + `# recovered … external-truncate`', () => {
    appendNotebookChunk(dir, chunk({ mode: 'reset', text: 'abc' }), T0)
    const m = meta()
    m.bytes = 10_000
    writeNotebookMeta(paths(), m)
    const r = appendNotebookChunk(
      dir,
      chunk({ text: 'def', seq: 2, offsetStart: 3, epoch: 0 }),
      T0 + 1000,
    )
    expect(r.status).toBe(409)
    if (!r.body.ok) expect(r.body).toMatchObject({ reason: 'stale-epoch', epoch: 1 })
    expect(readFileSync(paths().log, 'utf8')).toContain('reason=external-truncate')
    expect(meta().cells).toEqual({})
  })
})
