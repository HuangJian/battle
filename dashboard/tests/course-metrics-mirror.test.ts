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

  it('扫描面白名单：`*.run/**` 的 eval 尾账不进库（它们不是课程）', () => {
    // 2026-10-20 用户报障：左下角课程 select 里冒出一串 `ab-v6-*.jsonl.run` / `v6-*.jsonl.run`。
    // 根因：`isCourseName` 的注释写着 `*.run/**` 不是课程，实现却漏了后缀判据 ⇒ 独立评估
    // 目录的 `eval_log.jsonl` / `dist-agent-meta.jsonl` 被当成课程转存，课程发现再把它们当
    // 备份课捞回选择器。注：这类目录**有** eval 账本、**没有** `training_log.jsonl`。
    const run = path.join(TMP, 'ab-eval.jsonl.run')
    mkdirSync(run, { recursive: true })
    writeFileSync(path.join(run, 'eval_log.jsonl'), '{"event":"eval_summary"}\n')
    writeFileSync(path.join(run, 'dist-agent-meta.jsonl'), '{"win":1}\n')
    syncAll({ budgetBytes: 0 })
    expect(existsSync(path.join(LIB, 'ab-eval.jsonl.run'))).toBe(false)
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

/**
 * ticker 的**日志形状**（2026-10-20 用户口径「这个 log 一直在刷屏，请删除」）。
 *
 * 备份库写侧是 60s 一拍的节拍：每拍一行 `[mirror] 课 N ｜ 新增 … ｜ 未变 … ｜ memo … ｜ Nms`
 * ⇒ 控制台被它刷满，而那行全是**常态读数**（课数/新增/未变/耗时/memo），既不指向任何动作，
 * 出问题也定位不到具体文件。⇒ 常态静默。
 *
 * 真正要看的信号各有各的行（**不能跟着汇总一起删掉**）：
 *   · 复位 / 失败 / 冷存失败 = 逐条 warn（`[mirror] 复位 …` / `[mirror] 失败 …` / `[mirror] 冷存失败 …`）；
 *   · 「预算用尽」原先只活在那行汇总里 ⇒ 单独升成 warn（备份库正在落后，是要人管的）。
 *
 * 这里是**源码级**断言（节拍闭包直接跑真 IO，不适合单测；形状问题用读源码钉住——
 * 与 tests/pre-commit-staged-scope.test.ts 同一手法）。
 */
describe('mirror ticker 的日志形状（常态静默 / 异常必响）', () => {
  const SRC = readFileSync(
    path.join(import.meta.dir, '..', 'src', 'server', 'course-metrics-mirror.ts'),
    'utf8',
  )

  it('常态汇总行已删（不再有「课 N ｜ 新增 …」那一行）', () => {
    expect(SRC).not.toContain('[mirror] 课 ')
    expect(SRC).not.toContain('｜ 新增 ')
  })

  it('逐条异常 warn 仍在（复位 / 失败 / 冷存失败）', () => {
    expect(SRC).toContain('[mirror] 复位 ')
    expect(SRC).toContain('[mirror] 失败 ')
    expect(SRC).toContain('[mirror] 冷存失败 ')
  })

  it('「预算用尽」升成 warn（原先只活在被删的汇总行里）', () => {
    expect(SRC).toContain('[mirror] 预算用尽')
    const warn = SRC.indexOf('[mirror] 预算用尽')
    expect(warn).toBeGreaterThan(-1)
    // 必须走 warn 而不是 log（log 是「常态播报」那一档）
    expect(SRC.slice(Math.max(0, warn - 200), warn)).toContain('console.warn')
  })
})
