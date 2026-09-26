/**
 * server-api-course-archive.test.ts — 课程**封存读面**契约（plan/course-archive.plan.md §4 S3 / §3.6）。
 *
 * 三条要钉住的性质：
 *  ① 读面**只读 `archive-manifest.json`**：不扫 tmp、不递归、不解压（封存档案的文本件是
 *     gzip）——坏档案跳过而不是把 /api/state 带崩；
 *  ② **已封存课必须从 `discoverCourses()` 排除**：这是 2026-09-26 评审发现的 G2 缺口——
 *     封存按 N4 保留 `curricula/<课>.jsonc`，而发现逻辑会把「curricula 里尚未落盘的课」
 *     回填进课程表 ⇒ 不显式按 manifest 排除，封存课会一直留在课程 select 里；
 *  ③ 封存课同时**不进「在训」列**（marker 已删 ⇒ 判据自然为假，这里一并钉住）。
 *
 * 夹具：`BCITY_ARCHIVE_DIR` / `BCITY_TMP_LOGS_DIR` / `BCITY_CURRICULA_DIR` 三个惰性
 * 重定向（与 console-fixture 的 rl-config/console-state 同一惯例）——真实 `tmp/` 与
 * `archive/` **整轮零读零写**。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

import { api } from './helpers/console-fixture'
import { resolveArchivedSeedPath } from '../src/stack/courses'

const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-archive-'))
const ARCHIVE_DIR = path.join(scratch, 'archive', 'courses')
const TMP_DIR = path.join(scratch, 'tmp')
const CURRICULA_DIR = path.join(scratch, 'curricula')
const WEIGHTS_DIR = path.join(scratch, 'weights')
for (const d of [ARCHIVE_DIR, TMP_DIR, CURRICULA_DIR, WEIGHTS_DIR])
  mkdirSync(d, { recursive: true })
// 惰性取值 ⇒ import 之后设也生效（四个 getter 每次调用都读 env）
process.env.BCITY_ARCHIVE_DIR = ARCHIVE_DIR
process.env.BCITY_TMP_LOGS_DIR = TMP_DIR
process.env.BCITY_CURRICULA_DIR = CURRICULA_DIR
process.env.BCITY_WEIGHTS_ARCHIVE_DIR = WEIGHTS_DIR

afterAll(() => rmSync(scratch, { recursive: true, force: true }))

/** 造一门活体课程目录（有 training_log.jsonl 才会被 discoverCourses 认作课程）。 */
function liveCourse(course: string): void {
  const d = path.join(TMP_DIR, course)
  mkdirSync(d, { recursive: true })
  writeFileSync(path.join(d, 'training_log.jsonl'), '{"event":"iteration","iter":1}\n')
}

/** 造一个课程文件（N4：封存**不删**它——这正是回填要防的）。 */
function curriculum(course: string): void {
  writeFileSync(
    path.join(CURRICULA_DIR, `${course}.jsonc`),
    '{ "course": "%s" }\n'.replace('%s', course),
  )
}

/** 造一个封存档案（只有 manifest 一件就够读面用）。 */
function archive(course: string, over: Record<string, unknown> = {}): void {
  const d = path.join(ARCHIVE_DIR, course)
  mkdirSync(d, { recursive: true })
  writeFileSync(
    path.join(d, 'archive-manifest.json'),
    JSON.stringify({
      course,
      archived_at: '2026-09-26 10:00:00',
      parent: 'x20-steady',
      form: 'A+B',
      it_range: [0, 182],
      keys: { final_it: 182, opt_key_iters: [150, 182], judge_iters: [105] },
      weights: [{ it: 150, src: 'archive', path: `nn-training/weights/${course}/*.it150.*.json` }],
      shards_kept: false,
      codec: 'gzip',
      reads: { eval_log: 'eval_log.jsonl.gz', train_log: 'training_log.jsonl.gz' },
      verdict: '',
      bytes_total: 12_000_000,
      bytes_raw_total: 320_000_000,
      files_total: 42,
      ...over,
    }),
  )
}

describe('课程封存读面', () => {
  it('目录不存在 ⇒ 空数组（没封存过不是错误）', () => {
    expect(api.readArchived(path.join(scratch, 'nope'))).toEqual([])
  })

  it('manifest → 视图（字段映射 + 缺字段取安全缺省）', () => {
    archive('x-arch-1')
    const v = api.readArchived().find((a) => a.course === 'x-arch-1')!
    expect(v).toBeTruthy()
    expect(v.form).toBe('A+B')
    expect(v.parent).toBe('x20-steady')
    expect(v.itRange).toEqual([0, 182])
    expect(v.finalIt).toBe(182)
    expect(v.keyIters).toEqual([150, 182])
    expect(v.shardsKept).toBe(false)
    expect(v.codec).toBe('gzip')
    // 起点指向**归档**（不是已移走的 tmp 路径）
    expect(v.weights[0]!.src).toBe('archive')
    expect(v.weights[0]!.path).toContain('nn-training/weights/')
    // 可比入口是压缩件名 ⇒ 读面**不解压**也能把它交给 course_compare
    expect(v.reads.evalLog).toBe('eval_log.jsonl.gz')
  })

  it('坏 manifest 跳过而不是抛出（观测面坏掉不该带崩整页）', () => {
    const d = path.join(ARCHIVE_DIR, 'x-broken')
    mkdirSync(d, { recursive: true })
    writeFileSync(path.join(d, 'archive-manifest.json'), '{ not json')
    expect(() => api.readArchived()).not.toThrow()
    expect(api.readArchived().some((a) => a.course === 'x-broken')).toBe(false)
  })
})

describe('封存起点解析（G4-①）', () => {
  it('manifest 里有具体 path ⇒ 解析到该归档件', () => {
    const course = 'x-seed-1'
    const dir = path.join(WEIGHTS_DIR, course)
    mkdirSync(dir, { recursive: true })
    const file = 'rl-weights.it150.20260101-000000.json'
    writeFileSync(path.join(dir, file), '{}')
    archive(course, {
      weights: [
        {
          it: 150,
          src: 'archive',
          path: `nn-training/weights/${course}/${file}`,
          sha256: 'ab'.repeat(32),
          bytes: 2,
        },
      ],
    })
    expect(resolveArchivedSeedPath(course, 150)).toBe(path.join(dir, file))
  })

  it('manifest 只有 glob 提示 ⇒ 在归档目录 glob，同 it 多份取时间戳最大', () => {
    const course = 'x-seed-2'
    const dir = path.join(WEIGHTS_DIR, course)
    mkdirSync(dir, { recursive: true })
    writeFileSync(path.join(dir, 'w.it7.20260101-000000.json'), '{}')
    writeFileSync(path.join(dir, 'w.it7.20260102-000000.json'), '{}')
    archive(course, {
      weights: [{ it: 7, src: 'archive', path: `nn-training/weights/${course}/*.it7.*.json` }],
    })
    expect(resolveArchivedSeedPath(course, 7)).toBe(path.join(dir, 'w.it7.20260102-000000.json'))
  })

  it('解析不到 / 非法课名 ⇒ null（调用方响亮拒绝，不退回 BC）', () => {
    expect(resolveArchivedSeedPath('x-nope', 5)).toBeNull()
    expect(resolveArchivedSeedPath('../evil', 5)).toBeNull()
    expect(resolveArchivedSeedPath('x-seed-1', 999)).toBeNull()
  })
})

describe('已封存课不进活体视图（G2）', () => {
  it('★ 封存后课程不出现在 discoverCourses（curricula 回填也拦得住）', () => {
    // 活体 + 课程文件**都**在——正是「只删 tmp 不管用」的形状
    liveCourse('x-arch-2')
    curriculum('x-arch-2')
    expect(api.discoverCourses(500)).toContain('x-arch-2')

    archive('x-arch-2')
    expect(api.discoverCourses(500)).not.toContain('x-arch-2')
  })

  it('只有课程文件的新课仍然可见（排除不能误伤正常课程）', () => {
    curriculum('x-fresh-only')
    expect(api.discoverCourses(500)).toContain('x-fresh-only')
  })

  it('封存课也不进「在训」列（marker 已删 ⇒ 判据自然为假）', async () => {
    liveCourse('x-arch-3')
    archive('x-arch-3')
    const s = await api.buildStateView()
    expect(s.courses).not.toContain('x-arch-3')
    expect(s.trainingCourses ?? []).not.toContain('x-arch-3')
  })

  it('state view 带出封存分组（只读 manifest）', async () => {
    const s = await api.buildStateView()
    const got = (s.archived ?? []).map((a) => a.course)
    expect(got).toContain('x-arch-1')
    expect(got).toContain('x-arch-2')
  })
})
