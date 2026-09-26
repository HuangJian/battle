/** archive.ts — 课程**封存**读面（plan/course-archive.plan.md §3.4 / §4 S3）。
 *
 *  数据源只有一个：`archive/courses/<课>/archive-manifest.json`——**不扫 tmp、不递归、
 *  不解压**。为什么这条纪律必须是硬的（§3.6-6 / R5）：
 *
 *  * 控制台每拍都会算 `/api/state`，课程一多、`it<N>/` 目录一大，任何递归扫描都会线性拖慢首屏
 *    （`iters.ts::readIterActuals` 已经踩过这个坑）；
 *  * 封存档案的文本件是 gzip（`DECISIONS §2026-09-26-course-archive-compress`）——一旦读面
 *    要解压，就把「列一下有哪些封存课」变成「解压 N 份 eval_log」。故 manifest 与
 *    `ARCHIVE.md` **永不压缩**，读面因此是纯 `JSON.parse` 一个几十 KB 的文件。
 *
 *  坏档案（半截写入 / 手改坏）**跳过而不是抛出**：观测面坏掉只该让封存分组显空态，
 *  不该把整页 /api/state 带崩（与 `loopQueue` / `overview` 各自 catch 的同一条纪律）。
 */

import { readdirSync, readFileSync } from 'fs'
import path from 'path'
import { archiveCoursesDir } from '../../core/paths'
// 视图类型住 web/view（单一源）——server 侧只消费，不自定义一份（与 state-view 同纪律）。
import type { ArchivedCourseView } from '../../web/view'

export type { ArchivedCourseView }

function _num(v: unknown, dflt = 0): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : dflt
}

function _str(v: unknown, dflt = ''): string {
  return typeof v === 'string' ? v : dflt
}

/** 一份 manifest（已解析）→ 视图。字段缺失一律取安全缺省，不抛。 */
export function manifestToView(course: string, doc: Record<string, unknown>): ArchivedCourseView {
  const keys = (doc.keys ?? {}) as Record<string, unknown>
  const reads = (doc.reads ?? {}) as Record<string, unknown>
  const range = Array.isArray(doc.it_range) ? doc.it_range : []
  const weights = Array.isArray(doc.weights) ? doc.weights : []
  return {
    course: _str(doc.course, course),
    archivedAt: _str(doc.archived_at),
    form: _str(doc.form, '?'),
    parent: _str(doc.parent),
    itRange: [_num(range[0]), _num(range[1])],
    finalIt: _num(keys.final_it),
    keyIters: Array.isArray(keys.opt_key_iters)
      ? keys.opt_key_iters.filter((v): v is number => typeof v === 'number')
      : [],
    shardsKept: doc.shards_kept === true,
    codec: _str(doc.codec, 'none'),
    verdict: _str(doc.verdict),
    bytesTotal: _num(doc.bytes_total),
    bytesRawTotal: _num(doc.bytes_raw_total),
    filesTotal: _num(doc.files_total),
    weights: weights
      .filter((w): w is Record<string, unknown> => typeof w === 'object' && w !== null)
      .map((w) => ({
        it: _num(w.it),
        src: _str(w.src, 'archive'),
        path: _str(w.path),
      })),
    reads: {
      evalLog: _str(reads.eval_log),
      trainLog: _str(reads.train_log),
    },
  }
}

/** 封存课列表（按课程名排序）。目录不存在 ⇒ 空数组（没封存过不是错误）。 */
export function readArchived(dir: string = archiveCoursesDir()): ArchivedCourseView[] {
  let names: string[]
  try {
    names = readdirSync(dir, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
  } catch {
    return [] // 目录不存在 / 不可读：没封存过或读面没挂载，都不是错误
  }
  const out: ArchivedCourseView[] = []
  for (const name of names.sort()) {
    const p = path.join(dir, name, 'archive-manifest.json')
    try {
      const doc = JSON.parse(readFileSync(p, 'utf8')) as Record<string, unknown>
      if (doc && typeof doc === 'object') out.push(manifestToView(name, doc))
    } catch {
      /* 坏档案（半截写入/手改坏）跳过——不把整页 /api/state 带崩 */
    }
  }
  return out
}

/** 封存课名集合（`discoverCourses` 的排除判据；每次调用重读——封存是低频人的动作，
 *  但**不要在进程里缓存**：缓存会让「刚封存完课程还留在 select 里」重现）。 */
export function archivedCourseSet(dir: string = archiveCoursesDir()): Set<string> {
  return new Set(readArchived(dir).map((a) => a.course))
}

/** 单课是否已封存。 */
export function isArchived(course: string, dir: string = archiveCoursesDir()): boolean {
  return archivedCourseSet(dir).has(course)
}
