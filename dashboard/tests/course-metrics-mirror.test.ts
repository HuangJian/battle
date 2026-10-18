/**
 * course-metrics-mirror.test.ts — 备份库写侧（plan/course-metrics-persistence §3.2-A/§5 T1–T4/T3b）。
 *
 * 钉住五件事：
 *   ① 只抄完整行（半行留到下一拍，补齐后一次补全）；
 *   ② 稳态 stat 快路零正文读（第二拍 `bytes===0` 且 skipped）；
 *   ③ 源被截短 ⇒ 旧副本进 `history/`，绝不静默覆盖；
 *   ④ 扫描面白名单：`deliver/**`、`pytest-tmp/**` 不进库；
 *   ⑤ 锁：第二次拿不到；死 pid 的陈旧锁可抢占。
 */

import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'fs'
import os from 'os'
import path from 'path'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-course-metrics-'))
process.env.BCITY_TMP_LOGS_DIR = path.join(DIR, 'tmp')
process.env.BCITY_COURSE_METRICS = path.join(DIR, 'lib')
process.env.BCITY_ARCHIVE_DIR = path.join(DIR, 'archive')

import {
  anchorHex,
  completeEnd,
  decideAppend,
  decidePack,
} from '../src/server/course-metrics/engine'
import {
  acquireLock,
  libraryRoot,
  readMirrorMeta,
  syncAll,
} from '../src/server/course-metrics/mirror'

const TMP = path.join(DIR, 'tmp')
const LIB = path.join(DIR, 'lib')

function writeSource(course: string, text: string): string {
  const dir = path.join(TMP, course)
  mkdirSync(dir, { recursive: true })
  const p = path.join(dir, 'training_log.jsonl')
  writeFileSync(p, text)
  return p
}

beforeAll(() => {
  mkdirSync(TMP, { recursive: true })
  mkdirSync(LIB, { recursive: true })
})

afterAll(() => {
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* Windows 句柄未释放时保留临时目录 */
  }
})

describe('engine（纯函数）', () => {
  it('completeEnd 只认最后一个换行', () => {
    expect(completeEnd(Buffer.from('a\nb\nc'))).toBe(4)
    expect(completeEnd(Buffer.from('abc'))).toBe(0)
    expect(completeEnd(Buffer.from('a\n'))).toBe(2)
  })

  it('decideAppend：截短/快路/锚点/半行', () => {
    const prev = { bytes: 4, lines: 2, anchor: 'aa', srcMtime: 100, mode: 'append' as const }
    expect(decideAppend(prev, { size: 3, mtimeMs: 200 }, 'aa', 2).kind).toBe('reset')
    expect(decideAppend(prev, { size: 4, mtimeMs: 100 }, 'aa', 4).kind).toBe('skip')
    expect(decideAppend(prev, { size: 8, mtimeMs: 200 }, 'bb', 8).kind).toBe('reset')
    expect(decideAppend(prev, { size: 8, mtimeMs: 200 }, 'aa', 6)).toEqual({
      kind: 'append',
      from: 4,
      to: 6,
    })
    expect(decideAppend(prev, { size: 6, mtimeMs: 200 }, 'aa', 4).kind).toBe('skip')
  })

  it('decidePack：tmp 还在/未到 14 天/已有等价包 都不冷', () => {
    const base = { lastSourceActivity: 1_000, now: 2_000_000_000_000, days: 14 }
    expect(decidePack({ ...base, tmpExists: true, newestArchiveMtime: null })).toBe(false)
    expect(decidePack({ ...base, tmpExists: false, newestArchiveMtime: null })).toBe(true)
    expect(decidePack({ ...base, tmpExists: false, newestArchiveMtime: 1_000 })).toBe(false)
    expect(
      decidePack({
        ...base,
        now: 1_000 + 15 * 86_400_000,
        tmpExists: false,
        newestArchiveMtime: null,
      }),
    ).toBe(true)
  })

  it('anchorHex 取尾部至多 64B', () => {
    const buf = Buffer.alloc(100, 1)
    expect(anchorHex(buf, 100).length).toBe(128)
    expect(anchorHex(buf, 0)).toBe('')
  })
})

describe('mirror sync（真临时目录）', () => {
  it('只抄完整行；第二拍零正文读；补齐半行后补全', () => {
    const src = writeSource('c-half', 'a\nb\nc')
    const r1 = syncAll({ budgetBytes: 0 })
    expect(r1.failed).toEqual([])
    const dest = path.join(LIB, 'c-half', 'training_log.jsonl')
    expect(readFileSync(dest, 'utf8')).toBe('a\nb\n')
    expect(readMirrorMeta('c-half').files['training_log.jsonl']?.lines).toBe(2)

    const r2 = syncAll({ budgetBytes: 0 })
    expect(r2.bytes).toBe(0)
    expect(r2.skipped).toBeGreaterThan(0)

    writeFileSync(src, 'a\nb\ncd\n')
    const r3 = syncAll({ budgetBytes: 0 })
    expect(r3.bytes).toBe(3) // "cd\n"
    expect(readFileSync(dest, 'utf8')).toBe('a\nb\ncd\n')
    expect(readMirrorMeta('c-half').files['training_log.jsonl']?.lines).toBe(3)

    // ★ 稳态**零正文读**（plan §3.2-A1/T1）：账本尾已完整 ⇒ 落 ``stat`` 快路，
    //   不是「抄 0 字节」而是源文件一个字节都没读（先算尾窗再判 skip 会把每拍变成 1MiB 读）。
    //   重写型除外（sha 必须看内容）；本课只有 training_log ⇒ 整拍应为 0。
    expect(r3.sourceBytesRead).toBeGreaterThan(0)
    const r4 = syncAll({ budgetBytes: 0 })
    expect(r4.bytes).toBe(0)
    expect(r4.sourceBytesRead).toBe(0)
  })

  it('源被截短 ⇒ 旧副本进 history，绝不追加到旧前缀', () => {
    const src = writeSource('c-reset', 'x\ny\n')
    syncAll({ budgetBytes: 0 })
    writeFileSync(src, 'z\n')
    const r = syncAll({ budgetBytes: 0 })
    expect(r.resets.some((x) => x.course === 'c-reset')).toBe(true)
    expect(readFileSync(path.join(LIB, 'c-reset', 'training_log.jsonl'), 'utf8')).toBe('z\n')
    const hist = readdirSync(path.join(LIB, 'c-reset', 'history'))
    expect(hist.length).toBe(1)
    expect(
      readFileSync(path.join(LIB, 'c-reset', 'history', hist[0]!, 'training_log.jsonl'), 'utf8'),
    ).toBe('x\ny\n')
  })

  it('备份文件被删/改短 ⇒ 从 0 重抄，绝不续写出缺前缀的镜像', () => {
    const src = writeSource('c-missing', 'a\nb\nc\n')
    syncAll({ budgetBytes: 0 })
    const dest = path.join(LIB, 'c-missing', 'training_log.jsonl')
    // 模拟人手误删/半拷：只剩前两行的残件
    writeFileSync(dest, 'a\nb\n')
    const r = syncAll({ budgetBytes: 0 })
    expect(r.resets.some((x) => x.course === 'c-missing' && x.reason === 'mirror-missing')).toBe(
      true,
    )
    expect(readFileSync(dest, 'utf8')).toBe('a\nb\nc\n')
    expect(readMirrorMeta('c-missing').files['training_log.jsonl']?.bytes).toBe(6)
    expect(existsSync(path.join(LIB, 'c-missing', 'history'))).toBe(true)
    void src
  })

  it('重写型：同内容不重写，内容变了整份替换', () => {
    const dir = path.join(TMP, 'c-pool')
    mkdirSync(dir, { recursive: true })
    const p = path.join(dir, '.pool-actuals-cache.json')
    writeFileSync(p, '{"1":{"games":2}}')
    syncAll({ budgetBytes: 0 })
    const dest = path.join(LIB, 'c-pool', '.pool-actuals-cache.json')
    const first = readFileSync(dest, 'utf8')
    const r2 = syncAll({ budgetBytes: 0 })
    expect(r2.rewrites).toBe(0)
    expect(readFileSync(dest, 'utf8')).toBe(first)
    writeFileSync(p, '{"1":{"games":3}}')
    const r3 = syncAll({ budgetBytes: 0 })
    expect(r3.rewrites).toBe(1)
    expect(readFileSync(dest, 'utf8')).toBe('{"1":{"games":3}}')
  })

  it('扫描面白名单：deliver / pytest-tmp 不进库', () => {
    const c = path.join(TMP, 'c-white')
    mkdirSync(path.join(c, 'deliver', 'hash'), { recursive: true })
    writeFileSync(path.join(c, 'deliver', 'hash', 'eval_log.jsonl'), '{"event":"eval"}\n')
    const t = path.join(TMP, 'pytest-tmp', 'a', 'b')
    mkdirSync(t, { recursive: true })
    writeFileSync(path.join(t, 'training_log.jsonl'), '{"event":"iteration"}\n')
    syncAll({ budgetBytes: 0 })
    expect(existsSync(path.join(LIB, 'c-white', 'deliver'))).toBe(false)
    expect(existsSync(path.join(LIB, 'pytest-tmp'))).toBe(false)
  })

  it('锁：第二次拿不到；死 pid 陈旧锁可抢占', () => {
    const root = libraryRoot()
    const release = acquireLock(root, Date.now())
    expect(() => acquireLock(root, Date.now())).toThrow()
    release()
    // 死 pid + 超龄 ⇒ 抢占成功；旧锁改名留痕
    writeFileSync(
      path.join(root, '.lock'),
      JSON.stringify({ pid: 999_999, host: os.hostname(), startedAt: Date.now() - 11 * 60_000 }),
    )
    const release2 = acquireLock(root, Date.now())
    release2()
    expect(existsSync(path.join(root, '.lock'))).toBe(false)
  })
})
