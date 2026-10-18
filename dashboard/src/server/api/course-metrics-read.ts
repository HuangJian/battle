/** course-metrics-read.ts — 三态课程指标读取（live / mirror / archive）。
 *
 *  `state-view`、`overview`、`compare-trends`、`ledger` 共用这一入口，避免每个读面各自
 *  拼一套「tmp 没有就……」。冷存走内存解压（异步），live/mirror 走同一份路径解析器。
 *
 *  ★ 缓存写回只在 **live**：mirror/archive 若 `mkdir -p tmp/<课>` 会让冷存判据永远不成立
 *  （评审 F4），也违背「读侧不写 tmp」。
 */

import type { IterRow, PairedReferee } from '../../web/view'
import {
  buildIterRows,
  buildPairedReferee,
  parseActualsCacheText,
  parseEvalSummariesText,
  parseEvalWinsText,
  readIterMetrics,
  readPairedReferee,
} from '../iters'
import { type CourseSource, resolveCourseDir, readCourseText } from './course-source'

export interface CourseMetricsRead {
  rows: IterRow[]
  pairedReferee: PairedReferee | null
  source: CourseSource
}

export async function readCourseMetrics(course: string): Promise<CourseMetricsRead> {
  if (!course) return { rows: [], pairedReferee: null, source: 'none' }
  const dir = resolveCourseDir(course)
  if (dir.source === 'live') {
    return {
      rows: readIterMetrics(dir.dir).rows,
      pairedReferee: readPairedReferee(dir.dir),
      source: 'live',
    }
  }
  if (dir.source === 'mirror') {
    return {
      rows: readIterMetrics(dir.dir, { writeCache: false }).rows,
      pairedReferee: readPairedReferee(dir.dir),
      source: 'mirror',
    }
  }
  if (dir.source === 'archive') {
    const [trainingLog, evalLog, cacheJson] = await Promise.all([
      readCourseText(course, 'training_log.jsonl'),
      readCourseText(course, 'eval_log.jsonl'),
      readCourseText(course, '.pool-actuals-cache.json'),
    ])
    if (!trainingLog) return { rows: [], pairedReferee: null, source: 'archive' }
    const evalSummaries = evalLog ? parseEvalSummariesText(evalLog, course) : new Map()
    const evalWins = evalLog ? parseEvalWinsText(evalLog) : new Map()
    const actualsCache = cacheJson ? parseActualsCacheText(cacheJson) : new Map()
    const { rows } = buildIterRows({
      trainingLogText: trainingLog,
      evalSummaries,
      evalWins,
      actualsCache,
      // 逐轮 per-game 不备份（§0 诚实账）：冷存只认缓存里的 actuals。
      roundActuals: () => null,
    })
    return { rows, pairedReferee: buildPairedReferee(evalWins), source: 'archive' }
  }
  return { rows: [], pairedReferee: null, source: 'none' }
}
