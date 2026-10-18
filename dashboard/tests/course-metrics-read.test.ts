/**
 * course-metrics-read.test.ts — 读侧回退（plan §3.3 / §5 T5–T8、T12）。
 *
 * 钉住：
 *   · tmp 被清后 `source==='mirror'` / `'archive'`，指标行仍在；
 *   · 读备份**不写 tmp**（派生缓存只在 live 落盘——评审 F4）；
 *   · 课程发现与 `?course=` 放行备份课；
 *   · 比较趋势能读备份课；
 *   · live 优先于备份（同名课重开不会被旧镜像压住）。
 */

import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  utimesSync,
  writeFileSync,
} from 'fs'
import os from 'os'
import path from 'path'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-cm-read-'))
process.env.BCITY_TMP_LOGS_DIR = path.join(DIR, 'tmp')
process.env.BCITY_CURRICULA_DIR = path.join(DIR, 'curricula')
process.env.BCITY_ARCHIVE_DIR = path.join(DIR, 'archive')
process.env.BCITY_COURSE_METRICS = path.join(DIR, 'lib')

import { readCourseMetrics } from '../src/server/api/course-metrics-read'
import { buildCompareTrendsView, parseCompareQuery } from '../src/server/api/compare-trends'
import { actionRequiresLive, routeAction } from '../src/server/api/route'
import { discoverCourses, sanitizeViewCourse } from '../src/server/api/courses'
import { archiveMemoStats, __clearArchiveMemo } from '../src/server/course-metrics/archive'
import { compactAll } from '../src/server/course-metrics/compact'
import { syncAll } from '../src/server/course-metrics/mirror'

const TMP = path.join(DIR, 'tmp')
const LIB = path.join(DIR, 'lib')

function iterLine(iter: number, winRate: number): string {
  return JSON.stringify({
    event: 'iteration',
    iter,
    time: `t${iter}`,
    winRate,
    score_mean: winRate,
    score_std: 0.1,
    samples: 8,
    dim_means: { accuracy: 0.5, loot: 0.1, progress: 0.2 },
    expectedGames: 8,
  })
}

function makeCourse(course: string, rows: number[]): void {
  const dir = path.join(TMP, course)
  mkdirSync(dir, { recursive: true })
  writeFileSync(
    path.join(dir, 'training_log.jsonl'),
    rows.map((it, i) => `${iterLine(it, (i + 1) / 10)}\n`).join(''),
  )
  writeFileSync(
    path.join(dir, 'eval_log.jsonl'),
    rows
      .map((it) =>
        JSON.stringify({
          event: 'eval_summary',
          iter: it,
          time: `e${it}`,
          wver: 'w1',
          winRate: 0.5,
          games: 4,
          wins: 2,
        }),
      )
      .join('\n') + '\n',
  )
  writeFileSync(path.join(dir, 'dist-agent-meta.jsonl'), '{"node":"local","it":1}\n')
  writeFileSync(path.join(dir, '.pool-actuals-cache.json'), '{}')
}

beforeAll(() => {
  mkdirSync(TMP, { recursive: true })
  mkdirSync(path.join(DIR, 'curricula'), { recursive: true })
  mkdirSync(path.join(DIR, 'archive'), { recursive: true })
})

afterAll(() => {
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* retain on Windows lock */
  }
})

describe('读侧回退', () => {
  it('tmp 清空后读热备份：source=mirror、行仍在、不写 tmp', async () => {
    makeCourse('r-hot', [1, 2, 3])
    syncAll({ budgetBytes: 0 })
    rmSync(path.join(TMP, 'r-hot'), { recursive: true, force: true })
    const m = await readCourseMetrics('r-hot')
    expect(m.source).toBe('mirror')
    expect(m.rows.map((r) => r.iter)).toEqual([3, 2, 1])
    expect(m.rows[0]!.winRate).toBeCloseTo(0.3)
    expect(existsSync(path.join(TMP, 'r-hot'))).toBe(false) // 读侧绝不 mkdir tmp
    expect(discoverCourses()).toContain('r-hot')
    expect(sanitizeViewCourse('r-hot')).toBe('r-hot')
  })

  it('live 优先：同名课重开后显示当次 run', async () => {
    makeCourse('r-hot', [9])
    const m = await readCourseMetrics('r-hot')
    expect(m.source).toBe('live')
    expect(m.rows.map((r) => r.iter)).toEqual([9])
  })

  it('冷存：source=archive、行一致、memo 命中不重复解压', async () => {
    makeCourse('r-cold', [1, 2])
    syncAll({ budgetBytes: 0 })
    rmSync(path.join(TMP, 'r-cold'), { recursive: true, force: true })
    const p = await compactAll({ days: 0 })
    // days=0 不满足 `> 0`，用极小正数让「30 天前」仍过线
    expect(p.packed.length).toBe(0)
    const old = Date.now() - 20 * 86_400_000
    for (const f of [
      'training_log.jsonl',
      'eval_log.jsonl',
      'dist-agent-meta.jsonl',
      '.pool-actuals-cache.json',
    ]) {
      const fp = path.join(LIB, 'r-cold', f)
      utimesSync(fp, old / 1000, old / 1000)
    }
    const metaPath = path.join(LIB, 'r-cold', 'mirror-meta.json')
    const meta = JSON.parse(readFileSync(metaPath, 'utf8')) as {
      files: Record<string, { srcMtime: number }>
      lastSourceActivity: number
    }
    meta.lastSourceActivity = old
    for (const f of Object.values(meta.files)) f.srcMtime = old
    writeFileSync(metaPath, JSON.stringify(meta))

    const packed = await compactAll({ days: 14 })
    expect(packed.packed.length).toBe(1)
    __clearArchiveMemo()
    const m = await readCourseMetrics('r-cold')
    expect(m.source).toBe('archive')
    expect(m.rows.map((r) => r.iter)).toEqual([2, 1])
    const spawns = archiveMemoStats().spawns
    const again = await readCourseMetrics('r-cold')
    expect(again.rows.length).toBe(2)
    expect(archiveMemoStats().spawns).toBe(spawns)
    expect(discoverCourses()).toContain('r-cold')
  })

  it('比较趋势：备份课出序列，指纹带来源', async () => {
    makeCourse('r-cmp', [1, 2, 3])
    syncAll({ budgetBytes: 0 })
    rmSync(path.join(TMP, 'r-cmp'), { recursive: true, force: true })
    const q = parseCompareQuery({ courses: 'r-cmp', metric: 'winRate', source: 'rollout' })
    if (!q.ok) throw new Error(q.message)
    const view = await buildCompareTrendsView(q.params)
    expect(view.courses.map((c) => c.course)).toEqual(['r-cmp'])
    expect(view.courses[0]!.series[0]!.vals).toEqual([0.1, 0.2, 0.3])
    expect(view.fingerprint).toContain('r-cmp')
    expect(view.fingerprint).toContain('mirror')
  })

  it('动作闸：备份课 409「无活体」；setCourse 不受限', async () => {
    process.env.BCITY_CONSOLE_LOG = path.join(DIR, 'console.log')
    process.env.BCITY_CONSOLE_STATE = path.join(DIR, 'console-state.json')
    expect(actionRequiresLive('preset')).toBe(true)
    expect(actionRequiresLive('setCourse')).toBe(false)
    const resp = await routeAction('preset', { course: 'r-cmp' })
    expect(resp?.status).toBe(409)
    const text = await resp!.text()
    expect(text).toContain('无活体')
  })
})
