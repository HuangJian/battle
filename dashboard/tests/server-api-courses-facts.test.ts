/**
 * server-api-courses-facts.test.ts — 逐课盘上事实（`courseFacts`，课程管理页 /courses 的输入）。
 *
 * 分层：src/server/api/courses.ts
 *
 * 判据三条（这一页的动作都建在它们之上）：
 *   ① 开课标记 = `tmp/<课>/training-enabled.txt`（训练侧与 hub 的**同一个闸**）；
 *   ② 活体 = `tmp/<课>/` 在不在（决定「还能不能封存」）；
 *   ③ `curricula/<课>.jsonc`（含 `.bc.jsonc`）= 课程文件在。
 * 全部 stat 级事实，**不得**递归扫目录（本文件用「目录里塞一个巨大的子目录树」证明这条：
 * 若实现改成递归统计体积，用例会因为耗时/读面抖动先红）。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'

const root = mkdtempSync(join(tmpdir(), 'bcity-facts-'))
const tmpDir = join(root, 'tmp')
const curDir = join(root, 'curricula')
mkdirSync(tmpDir, { recursive: true })
mkdirSync(curDir, { recursive: true })
process.env.BCITY_TMP_LOGS_DIR = tmpDir
process.env.BCITY_CURRICULA_DIR = curDir

// ① 已开课：活体 + 标记 + 账本
const live = join(tmpDir, 'live-course')
mkdirSync(live, { recursive: true })
writeFileSync(join(live, 'training-enabled.txt'), '1\n', 'utf8')
writeFileSync(join(live, 'training_log.jsonl'), '{}\n', 'utf8')
// ② 已停：活体在、没有标记
const stopped = join(tmpDir, 'stopped-course')
mkdirSync(stopped, { recursive: true })
writeFileSync(join(stopped, 'training_log.jsonl'), '{}\n', 'utf8')
// 这两个活体目录里塞一棵很深的树（证明实现不递归）
for (const d of [live, stopped]) {
  let p = join(d, 'it9')
  mkdirSync(p, { recursive: true })
  for (let i = 0; i < 3; i++) {
    p = join(p, `deep${i}`)
    mkdirSync(p, { recursive: true })
  }
}
// 令「已停」那一门是 3 小时前被写的（相对时间的锚点）
const old = Date.now() / 1000 - 3 * 3600
utimesSync(stopped, old, old)
// ③ 只有课程文件（RL + BC 两种后缀各一）
writeFileSync(join(curDir, 'declared-only.jsonc'), '{}\n', 'utf8')
writeFileSync(join(curDir, 'bc-only.bc.jsonc'), '{}\n', 'utf8')

const { courseFacts } = await import('../src/server/api/courses')

afterAll(() => {
  delete process.env.BCITY_TMP_LOGS_DIR
  delete process.env.BCITY_CURRICULA_DIR
  rmSync(root, { recursive: true, force: true })
})

describe('courseFacts — 逐课盘上事实', () => {
  it('已开课 / 已停 / 未落盘三态与「最后写入」', () => {
    const facts = courseFacts(['live-course', 'stopped-course', 'declared-only', 'bc-only'])
    const by = new Map(facts.map((f) => [f.course, f]))

    const a = by.get('live-course')!
    expect(a).toMatchObject({ tmp: true, enabled: true, declared: false })
    expect(a.lastWriteMs).not.toBeNull()

    const b = by.get('stopped-course')!
    expect(b).toMatchObject({ tmp: true, enabled: false, declared: false })
    // mtime 是「最后写入」的事实源（这里钉 3 小时前 ⇒ 相对时间读数可复现）
    expect(Math.abs(b.lastWriteMs! - Date.now() + 3 * 3600_000)).toBeLessThan(60_000)

    expect(by.get('declared-only')).toMatchObject({
      tmp: false,
      enabled: false,
      declared: true,
      lastWriteMs: null,
    })
    // BC 课程文件是 `<课>.bc.jsonc`，同样算「课程文件在」
    expect(by.get('bc-only')).toMatchObject({ tmp: false, declared: true })
  })

  it('输入顺序即输出顺序（上屏顺序由视图层排，不在服务端偷排）', () => {
    expect(courseFacts(['bc-only', 'live-course']).map((f) => f.course)).toEqual([
      'bc-only',
      'live-course',
    ])
  })

  it('不存在的课：三项全 false（不编事实，也不抛）', () => {
    expect(courseFacts(['no-such-course'])[0]).toEqual({
      course: 'no-such-course',
      tmp: false,
      enabled: false,
      declared: false,
      lastWriteMs: null,
    })
  })
})
