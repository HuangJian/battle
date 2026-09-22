/** evalboard-rows-incremental.test.ts ↔ dashboard/src/server/eval-board/rows.ts 的
 *  账本行增量读：未动零 IO、只增长读尾巴、被改写整片重解析。 */
import { afterEach, beforeEach, describe, expect, it } from 'bun:test'
import { appendFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { appendRow, gamesDir, loadRows, type EvalGameRow } from '../src/evalboard/store'
import { loadRowsCached, resetRowsCache } from '../src/server/eval-board/rows'

let DIR = ''
let ROOT = ''

beforeEach(() => {
  DIR = mkdtempSync(path.join(tmpdir(), 'evb-rows-'))
  ROOT = path.join(DIR, 'data')
  mkdirSync(gamesDir(ROOT), { recursive: true })
  resetRowsCache()
})

afterEach(() => {
  resetRowsCache()
  rmSync(DIR, { recursive: true, force: true })
})

const row = (seed: number, month = '2026-09', source: 'A' | 'B' = 'A'): EvalGameRow =>
  ({
    schema: 1,
    ts: `${month}-01T00:00:00.000Z`,
    source,
    batch_id: source === 'A' ? 'A-c1' : 'b-1',
    run_id: 'c1',
    course: 'c1',
    iter: 10,
    wver: 'w1',
    policy: 'nn',
    rung: 'c4l1',
    stage_id: '0',
    seed,
    engine: { git_commit: 'g', dist_codehash: 'd', engine_epoch: 'e' },
    win: seed % 2 === 0,
    ticks: 100,
  }) as unknown as EvalGameRow

/** 按文件写入（不走 appendRow：要能精确控制分片名与内容）。 */
const writeShard = (name: string, rows: EvalGameRow[]): void =>
  writeFileSync(
    path.join(gamesDir(ROOT), name),
    rows.map((r) => `${JSON.stringify(r)}\n`).join(''),
    'utf-8',
  )

/** 与 store.loadRows 的内容对比（行序按 JSON 字符串归一，避开分片遍历顺序差异）。 */
const sameRows = (a: EvalGameRow[], b: EvalGameRow[]): boolean =>
  JSON.stringify(a.map((r) => JSON.stringify(r)).sort()) ===
  JSON.stringify(b.map((r) => JSON.stringify(r)).sort())

describe('loadRowsCached（账本行增量读）', () => {
  it('内容与 store.loadRows 一致；未变动时是**同一个实例**（零 IO 零解析）', () => {
    writeShard('2026-08.jsonl', [row(1, '2026-08'), row(2, '2026-08')])
    writeShard('2026-09.jsonl', [row(3)])
    const first = loadRowsCached(ROOT)
    expect(sameRows(first, loadRows(ROOT))).toBe(true)
    expect(first.length).toBe(3)
    expect(loadRowsCached(ROOT)).toBe(first) // 同一实例 ⇒ 没有重读/重解析
  })

  it('分片只增长 → 旧行原样保留、新行追加（不重解析旧行）', () => {
    const a = row(1)
    const b = row(2)
    writeShard('2026-09.jsonl', [a])
    const first = loadRowsCached(ROOT)
    appendRow(ROOT, b)
    const second = loadRowsCached(ROOT)
    expect(second).not.toBe(first)
    expect(sameRows(second, loadRows(ROOT))).toBe(true)
    expect(second.length).toBe(2)
    expect(second[0]).toBe(first[0]) // 旧行的**对象实例**都没换（说明没重新解析）
    expect(second[1]).toMatchObject({ seed: 2 })
    expect(a).toMatchObject({ seed: 1 }) // 写盘的那份是序列化副本，实例不同属预期
  })

  it('分片被截断 → 那一片整片重解析，旧行不再存在', () => {
    writeShard('2026-09.jsonl', [row(1), row(2), row(3)])
    expect(loadRowsCached(ROOT).length).toBe(3)
    writeShard('2026-09.jsonl', [row(9)]) // 体积变小 = 截断
    const after = loadRowsCached(ROOT)
    expect(after.length).toBe(1)
    expect(after[0]).toMatchObject({ seed: 9 })
    expect(sameRows(after, loadRows(ROOT))).toBe(true)
  })

  it('分片同尺寸原地改写 → 整片重解析（偏移不可信）', () => {
    writeShard('2026-09.jsonl', [row(1), row(2)])
    expect(loadRowsCached(ROOT).length).toBe(2)
    writeShard('2026-09.jsonl', [row(7), row(2)]) // 同字节数、只改了首行
    const after = loadRowsCached(ROOT)
    expect(after.length).toBe(2)
    expect(after.map((r) => r.seed).sort()).toEqual([2, 7])
    expect(loadRowsCached(ROOT)).toBe(after)
  })

  it('新分片出现 → 追加其行，行序按分片名（月份序）', () => {
    writeShard('2026-09.jsonl', [row(3)])
    expect(loadRowsCached(ROOT).map((r) => r.seed)).toEqual([3])
    writeShard('2026-08.jsonl', [row(1), row(2)])
    expect(loadRowsCached(ROOT).map((r) => r.seed)).toEqual([1, 2, 3])
  })

  it('分片被删 → 它的行随之消失', () => {
    writeShard('2026-08.jsonl', [row(1)])
    writeShard('2026-09.jsonl', [row(2)])
    expect(loadRowsCached(ROOT).length).toBe(2)
    rmSync(path.join(gamesDir(ROOT), '2026-08.jsonl'))
    expect(loadRowsCached(ROOT).map((r) => r.seed)).toEqual([2])
  })

  it('末尾半行不入账，补齐后入账（写方可能在行中间 flush）', () => {
    writeShard('2026-09.jsonl', [row(1)])
    expect(loadRowsCached(ROOT).length).toBe(1)
    const half = `${JSON.stringify(row(2)).slice(0, 20)}`
    appendFileSync(path.join(gamesDir(ROOT), '2026-09.jsonl'), half)
    expect(loadRowsCached(ROOT).length).toBe(1)
    appendFileSync(
      path.join(gamesDir(ROOT), '2026-09.jsonl'),
      `${JSON.stringify(row(2)).slice(20)}\n`,
    )
    expect(loadRowsCached(ROOT).map((r) => r.seed)).toEqual([1, 2])
    expect(sameRows(loadRowsCached(ROOT), loadRows(ROOT))).toBe(true)
  })

  it('坏行跳过（与 store.loadRows 同规）', () => {
    appendFileSync(path.join(gamesDir(ROOT), '2026-09.jsonl'), 'not json\n')
    appendRow(ROOT, row(1))
    appendFileSync(path.join(gamesDir(ROOT), '2026-09.jsonl'), '{oops\n')
    const rows = loadRowsCached(ROOT)
    expect(rows.length).toBe(1)
    expect(sameRows(rows, loadRows(ROOT))).toBe(true)
  })

  it('空根 → 空数组（不抛）；不同根互不串味', () => {
    expect(loadRowsCached(ROOT)).toEqual([])
    const other = path.join(DIR, 'other')
    mkdirSync(gamesDir(other), { recursive: true })
    writeFileSync(
      path.join(gamesDir(other), '2026-09.jsonl'),
      `${JSON.stringify(row(1))}\n`,
      'utf-8',
    )
    expect(loadRowsCached(other).length).toBe(1)
    expect(loadRowsCached(ROOT)).toEqual([])
  })
})
