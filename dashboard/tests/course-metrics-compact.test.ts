/**
 * course-metrics-compact.test.ts — 冷存（plan §3.2-B、§5 T11/T11b/T12/T12b/T13）。
 *
 * 钉住：tmp 仍在就不冷（防每拍重打包）· 索引先落盘 · 包内成员可内存读 ·
 * 成员判定不解包（spawn 计数 0）· 世代只留 2 · memo 超限逐出后重读仍正确。
 */

import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
  utimesSync,
  writeFileSync,
} from 'fs'
import os from 'os'
import path from 'path'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-cm-compact-'))
process.env.BCITY_TMP_LOGS_DIR = path.join(DIR, 'tmp')
process.env.BCITY_COURSE_METRICS = path.join(DIR, 'lib')
process.env.BCITY_ARCHIVE_DIR = path.join(DIR, 'archive')

import {
  archiveHasMember,
  archiveMemoStats,
  coldCourses,
  readArchiveIndex,
  readArchiveText,
  __clearArchiveMemo,
  __resetTarProbe,
} from '../src/server/course-metrics/archive'
import { compactAll, compactCourse } from '../src/server/course-metrics/compact'
import { syncAll } from '../src/server/course-metrics/mirror'

const TMP = path.join(DIR, 'tmp')
const LIB = path.join(DIR, 'lib')

function ageDir(dir: string, days: number): void {
  const t = (Date.now() - days * 86_400_000) / 1000
  for (const f of [
    'training_log.jsonl',
    'eval_log.jsonl',
    'dist-agent-meta.jsonl',
    '.pool-actuals-cache.json',
  ]) {
    try {
      utimesSync(path.join(dir, f), t, t)
    } catch {
      /* 文件不在 */
    }
  }
}

function makeCourse(course: string, body = '{"event":"iteration","iter":1}\n'): void {
  const dir = path.join(TMP, course)
  mkdirSync(dir, { recursive: true })
  writeFileSync(path.join(dir, 'training_log.jsonl'), body)
  writeFileSync(path.join(dir, 'eval_log.jsonl'), '{"event":"eval_summary","iter":1,"wver":"w"}\n')
  writeFileSync(path.join(dir, 'dist-agent-meta.jsonl'), '{"node":"local","it":1}\n')
  writeFileSync(
    path.join(dir, '.pool-actuals-cache.json'),
    '{"1":{"games":1,"totalKills":1,"totalPU":0,"avgTicks":10,"time":"t","schemaV":4,"killRate":1,"dmgPerKillPct":0.1,"avgResidualHpPct":0.1,"totalPUSpawn":0}}',
  )
}

beforeAll(() => {
  mkdirSync(TMP, { recursive: true })
  mkdirSync(LIB, { recursive: true })
})

afterAll(() => {
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* keep temp on Windows lock */
  }
})

describe('冷存打包与读', () => {
  it('tmp 仍在 ⇒ 不打包（防每拍重打包）', async () => {
    makeCourse('c-live')
    ageDir(path.join(TMP, 'c-live'), 30)
    syncAll({ budgetBytes: 0 })
    const r = await compactAll({ days: 14 })
    expect(r.packed).toEqual([])
    expect(existsSync(path.join(LIB, 'c-live'))).toBe(true)
  })

  it('打包 → 索引在、热目录退役、成员内存可读、存在性判定不解包', async () => {
    makeCourse('c-cold', '{"event":"iteration","iter":7}\n')
    ageDir(path.join(TMP, 'c-cold'), 20)
    syncAll({ budgetBytes: 0 })
    rmSync(path.join(TMP, 'c-cold'), { recursive: true, force: true })
    const r = await compactAll({ days: 14 })
    expect(r.failed).toEqual([])
    expect(r.packed.length).toBe(1)
    expect(existsSync(path.join(LIB, 'c-cold'))).toBe(false)
    expect(existsSync(path.join(LIB, 'c-cold.tar.gz'))).toBe(true)
    expect(existsSync(path.join(LIB, 'c-cold.tar.gz.index.json'))).toBe(true)
    expect(coldCourses()).toContain('c-cold')
    const idx = readArchiveIndex('c-cold')
    expect(idx?.members).toContain('c-cold/mirror-meta.json')
    expect(idx?.members).toContain('c-cold/training_log.jsonl')

    __clearArchiveMemo()
    expect(archiveMemoStats().spawns).toBe(0)
    expect(archiveHasMember('c-cold', 'training_log.jsonl')).toBe(true)
    expect(archiveMemoStats().spawns).toBe(0) // 判定只读索引

    const text = await readArchiveText('c-cold', 'training_log.jsonl')
    expect(text).toBe('{"event":"iteration","iter":7}\n')
    const spawns = archiveMemoStats().spawns
    const again = await readArchiveText('c-cold', 'training_log.jsonl')
    expect(again).toBe(text)
    expect(archiveMemoStats().spawns).toBe(spawns) // memo 命中，不重复解压
  })

  it('世代只留 2；新包顶上、旧包改名', async () => {
    makeCourse('c-gen', '{"event":"iteration","iter":1}\n')
    ageDir(path.join(TMP, 'c-gen'), 30)
    syncAll({ budgetBytes: 0 })
    rmSync(path.join(TMP, 'c-gen'), { recursive: true, force: true })
    await compactAll({ days: 14 })
    // 重开/重打包：热目录重建（内容变了）后直接 compactCourse 三次
    for (let i = 2; i <= 4; i++) {
      const hot = path.join(LIB, 'c-gen')
      mkdirSync(hot, { recursive: true })
      writeFileSync(path.join(hot, 'training_log.jsonl'), `{"event":"iteration","iter":${i}}\n`)
      writeFileSync(
        path.join(hot, 'mirror-meta.json'),
        JSON.stringify({
          version: 1,
          course: 'c-gen',
          files: {},
          lastSourceActivity: Date.now() + i * 1000,
        }),
      )
      await compactCourse('c-gen')
    }
    const gens = readdirSync(LIB).filter((n: string) =>
      /^c-gen\.\d{8}T\d{6}(-\d+)?\.tar\.gz$/.test(n),
    )
    expect(gens.length).toBe(2)
    expect(existsSync(path.join(LIB, 'c-gen.tar.gz'))).toBe(true)
  })

  it('tar 自检失败 ⇒ 冷存源关闭（读侧不宣称 archive，也不逐请求重 spawn）', async () => {
    __clearArchiveMemo()
    __resetTarProbe()
    process.env.BCITY_TAR = path.join(DIR, 'no-such-tar-binary')
    try {
      expect(await readArchiveText('c-cold', 'training_log.jsonl')).toBeNull()
      // 自检失败后 resolver 不再宣称 archive（课程回 'none'，不是「空读数却 available」）。
      expect(archiveHasMember('c-cold', 'training_log.jsonl')).toBe(false)
      // 已失败不再重试：连一次解压 spawn 都不该起（自检本身不计入 `spawns`）。
      expect(archiveMemoStats().spawns).toBe(0)
      await readArchiveText('c-cold', 'training_log.jsonl')
      expect(archiveMemoStats().spawns).toBe(0)
    } finally {
      delete process.env.BCITY_TAR
      __resetTarProbe()
    }
    expect(await readArchiveText('c-cold', 'training_log.jsonl')).toBeTruthy()
  })

  it('memo 超限按 LRU 逐出，重读仍正确', async () => {
    process.env.BCITY_COURSE_METRICS_MEMO_MB = '0.00005' // ~52B：第二个成员必然溢出
    __clearArchiveMemo()
    const a = await readArchiveText('c-cold', 'training_log.jsonl')
    const b = await readArchiveText('c-cold', 'eval_log.jsonl')
    expect(a).toBeTruthy()
    expect(b).toBeTruthy()
    expect(archiveMemoStats().evictions).toBeGreaterThan(0)
    const a2 = await readArchiveText('c-cold', 'training_log.jsonl')
    expect(a2).toBe(a)
    delete process.env.BCITY_COURSE_METRICS_MEMO_MB
  })
})
