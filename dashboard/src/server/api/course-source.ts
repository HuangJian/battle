/** course-source.ts — 读侧的**唯一来源解析**（live ⇒ mirror ⇒ archive；plan §3.3）。
 *
 *  规则一句话：**读历史可回退备份，动作/活体只认 tmp**。本模块只做读，不写任何东西。
 *
 *  冷存为什么不落盘解压：`archive` 分支走 `course-metrics/archive.ts` 的异步 `Bun.spawn`
 *  + 内存 LRU（评审 F1）；成员存在性只读打包时写的 index（评审 F2）。
 */

import { readFileSync, statSync } from 'fs'
import path from 'path'
import { tmpLogsDir } from '../../core/paths'
import {
  archiveHasMember,
  archivePathFor,
  coldCourses,
  libraryRoot,
  listMirrorCourses,
  mirrorFileExists,
  readArchiveIndex,
  readArchiveText,
} from '../course-metrics'
import { readLogTail } from './logs'

export type CourseSource = 'live' | 'mirror' | 'archive' | 'none'

export interface CourseFileRef {
  /** live/mirror = 真实文件；archive = 包路径（内容必须经 `readCourseText`）。 */
  path: string | null
  source: CourseSource
  rel: string | null
}

/** 源相对路径候选（顶层优先、`traj/` 次之——与备份引擎同一顺序）。 */
function rels(name: string): string[] {
  return [name, `traj/${name}`]
}

function isFile(p: string): boolean {
  try {
    return statSync(p).isFile()
  } catch {
    return false
  }
}

/** 解析某个课程文件的来源。不存在 ⇒ `{source:'none'}`（调用方维持 fail-soft）。 */
export function resolveCourseFile(course: string, name: string): CourseFileRef {
  if (!course) return { path: null, source: 'none', rel: null }
  for (const rel of rels(name)) {
    const p = path.join(tmpLogsDir(), course, rel)
    if (isFile(p)) return { path: p, source: 'live', rel }
  }
  for (const rel of rels(name)) {
    if (mirrorFileExists(course, rel)) {
      return { path: path.join(libraryRoot(), course, rel), source: 'mirror', rel }
    }
  }
  for (const rel of rels(name)) {
    if (archiveHasMember(course, rel)) {
      return { path: archivePathFor(course), source: 'archive', rel }
    }
  }
  return { path: null, source: 'none', rel: null }
}

/** 课程目录（以 `training_log.jsonl` 为存在性锚——与今天所有读面同口径）。 */
export function resolveCourseDir(course: string): { dir: string; source: CourseSource } {
  const ref = resolveCourseFile(course, 'training_log.jsonl')
  if (ref.source === 'live') return { dir: path.join(tmpLogsDir(), course), source: 'live' }
  if (ref.source === 'mirror') return { dir: path.join(libraryRoot(), course), source: 'mirror' }
  return { dir: '', source: ref.source }
}

/** 该课任一备份集文件来自哪里（`training_log` 缺失时用于发现课程）。 */
export function courseSourceOf(course: string): CourseSource {
  for (const name of [
    'training_log.jsonl',
    'eval_log.jsonl',
    'dist-agent-meta.jsonl',
    '.pool-actuals-cache.json',
  ]) {
    const ref = resolveCourseFile(course, name)
    if (ref.source !== 'none') return ref.source
  }
  return 'none'
}

/** 备份库里的课程名（热 + 冷去重）。 */
export function listBackupCourses(): string[] {
  return [...new Set([...listMirrorCourses(), ...coldCourses()])]
}

/** 读整份文本。live/mirror 同步读；archive 异步内存解压（失败 ⇒ null）。 */
export async function readCourseText(course: string, name: string): Promise<string | null> {
  const ref = resolveCourseFile(course, name)
  if (ref.source === 'live' || ref.source === 'mirror') {
    try {
      return readFileSync(ref.path!, 'utf8')
    } catch {
      return null
    }
  }
  if (ref.source === 'archive' && ref.rel) return readArchiveText(course, ref.rel)
  return null
}

/** 尾读（行语义与 `readLedgerTail` 相同：不截断长行）。 */
export async function readCourseTail(
  course: string,
  name: string,
  maxLines = 600,
): Promise<string[]> {
  const ref = resolveCourseFile(course, name)
  if (ref.source === 'live' || ref.source === 'mirror') {
    return readLogTail(ref.path!, maxLines, 512 * 1024, 'all').lines
  }
  if (ref.source === 'archive' && ref.rel) {
    const text = await readArchiveText(course, ref.rel)
    if (!text) return []
    const lines = text.split('\n')
    const out = lines.filter((l) => l.length > 0)
    return out.slice(-maxLines)
  }
  return []
}

/** 文件级 stat（live/mirror 真实文件；archive 只有包级 size/mtime）。 */
export interface CourseFileStat {
  size: number
  mtimeMs: number
}

export function courseFileStat(course: string, name: string): CourseFileStat | null {
  const ref = resolveCourseFile(course, name)
  if ((ref.source === 'live' || ref.source === 'mirror') && ref.path) {
    try {
      const st = statSync(ref.path)
      return { size: st.size, mtimeMs: st.mtimeMs }
    } catch {
      return null
    }
  }
  if (ref.source === 'archive') {
    const idx = readArchiveIndex(course)
    return idx ? { size: idx.size, mtimeMs: idx.mtimeMs } : null
  }
  return null
}
