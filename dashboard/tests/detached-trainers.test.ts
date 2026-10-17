/**
 * detached-trainers.test.ts — 「有独立 trainer 在跑」的读面（plan/single-resident-trainer §1.5）。
 *
 * 写侧是 python 准入闸的逃生舱（`--allow-detached-trainer` ⇒ `tmp/trainer-detached.json`）；
 * 本文件钉住读面的**三条纪律**（都是防误报的）：
 *   ① 不可知 ≠ 没事：文件不在 / 坏 JSON ⇒ `null`（坞不出条目），只有「文件在、条目被过滤光」才 `[]`；
 *   ② pid 必须活着（逃生舱进程早退出了，旧条目不许继续上屏——那条横幅会被人拿去停进程）；
 *   ③ 24h 窗口（文件是环形表，旧条目可能指向一个已被复用的 pid）。
 */

import { describe, expect, it } from 'bun:test'
import { mkdtempSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { DETACHED_TTL_MS, readDetachedTrainers } from '../src/server/api/detached-trainers'

const NOW = 1_700_000_000_000 // ms
const at = Math.floor(NOW / 1000) // 写侧是 epoch **秒**（python `time.time()`）

function withFile(content: string | null): string {
  const dir = mkdtempSync(join(tmpdir(), 'detached-'))
  const p = join(dir, 'trainer-detached.json')
  if (content !== null) writeFileSync(p, content)
  return p
}

const rec = (over: Record<string, unknown> = {}): string =>
  JSON.stringify([
    { pid: 4242, course: 'c1', entry: 'run_rl', at, argv: 'run_rl.py --course c1', ...over },
  ])

const read = (content: string | null, alive = () => true) =>
  readDetachedTrainers({ path: withFile(content), now: NOW, alive })

describe('独立 trainer 读面（server/api/detached-trainers）', () => {
  it('文件不在 ⇒ null（不可知 ≠ 没事）', () => {
    expect(read(null)).toBeNull()
  })

  it('坏 JSON / 非数组 ⇒ null（写侧原子写，这里是防御）', () => {
    expect(read('{ not json')).toBeNull()
    expect(read('{"pid": 1}')).toBeNull()
  })

  it('活着 ∧ 窗口内的条目 ⇒ 逐字段透出', () => {
    const got = read(rec())
    expect(got).not.toBeNull()
    expect(got!.length).toBe(1)
    expect(got![0]).toMatchObject({ pid: 4242, course: 'c1', at, entry: 'run_rl' })
    expect(got![0]!.argv).toContain('--course c1')
  })

  it('pid 已死 ⇒ 过滤掉（旧条目不许上屏）', () => {
    expect(read(rec(), () => false)).toEqual([])
  })

  it('超过 24h 窗口 ⇒ 过滤掉（环形表里的老条目）', () => {
    const old = rec({ at: Math.floor((NOW - DETACHED_TTL_MS) / 1000) - 1 })
    expect(read(old)).toEqual([])
    const fresh = rec({ at: Math.floor((NOW - DETACHED_TTL_MS) / 1000) + 1 })
    expect(read(fresh)!.length).toBe(1)
  })

  it('形状不全的条目跳过，其余照常透出（不因一条坏记录废掉整张表）', () => {
    const got = read(
      JSON.stringify([
        { pid: 'nope', at },
        { pid: 7, course: 'c2', entry: 'run_bc', at, argv: 'run_bc.py --course c2' },
      ]),
    )
    expect(got!.map((r) => r.pid)).toEqual([7])
  })
})
