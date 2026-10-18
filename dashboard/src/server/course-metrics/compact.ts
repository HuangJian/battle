/** compact.ts — 冷存打包（14 天无源活动 ⇒ `<课>.tar.gz`，原热目录退役）。
 *
 *  判据与保留策略（plan §3.2-B v3 / 评审 F5）：
 *   · **tmp 有同名目录就不打包**（否则 sync 重建 → 按旧 srcMtime 判冷 → 每拍重打包）；
 *   · 已有等价包（包 mtime ≥ lastSourceActivity）⇒ 跳过（防抖动）；
 *   · 世代只留 2 个；
 *   · **索引先落盘、再删热目录**（顺序硬约束：否则冷存不可读）。
 */

import { existsSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'fs'
import path from 'path'
import { tmpLogsDir } from '../../core/paths'
import { archivePathFor, archiveIndexPathFor, tarBin } from './archive'
import { decidePack, generationName, parseArchiveFileName, stampFor } from './engine'
import { ARCHIVE_INDEX_SUFFIX, GENERATION_KEEP, isCourseName, type ArchiveIndex } from './meta'
import {
  acquireLock,
  courseMirrorDir,
  hasHotMirror,
  libraryRoot,
  readMirrorMeta,
  syncAll,
} from './mirror'

export function packDays(): number {
  const raw = Number(process.env.BCITY_COURSE_METRICS_PACK_DAYS ?? '')
  return Number.isFinite(raw) && raw > 0 ? raw : 14
}

async function runTar(
  args: string[],
  cwd: string,
): Promise<{ code: number; stdout: string; stderr: string }> {
  let proc: ReturnType<typeof Bun.spawn>
  try {
    // 相对包名 + cwd：GNU tar 把 `-f C:...` 当远程主机（Windows 盘符与 `host:path` 同形）。
    proc = Bun.spawn([tarBin(), ...args], {
      cwd,
      stdout: 'pipe',
      stderr: 'pipe',
      windowsHide: true,
    })
  } catch (e) {
    return { code: -1, stdout: '', stderr: e instanceof Error ? e.message : String(e) }
  }
  const [stdout, stderr, code] = await Promise.all([
    new Response(proc.stdout as ReadableStream).text(),
    new Response(proc.stderr as ReadableStream).text(),
    proc.exited,
  ])
  return { code, stdout, stderr }
}

/** 库内已有冷存包（当前世代 + 旧世代）的最新 mtime。 */
export function newestArchiveMtime(course: string): number | null {
  let newest: number | null = null
  let names: string[] = []
  try {
    names = readdirSync(libraryRoot())
  } catch {
    return null
  }
  for (const name of names) {
    const parsed = parseArchiveFileName(name)
    if (!parsed || parsed.course !== course) continue
    try {
      const m = statSync(path.join(libraryRoot(), name)).mtimeMs
      newest = newest == null ? m : Math.max(newest, m)
    } catch {
      /* ignore */
    }
  }
  return newest
}

export interface PackCandidate {
  course: string
  lastSourceActivity: number
}

/** 可打包的课（tmp 无同名目录、>days、无等价包）。 */
export function packCandidates(now = Date.now(), days = packDays()): PackCandidate[] {
  const root = libraryRoot()
  let names: string[] = []
  try {
    names = readdirSync(root, { withFileTypes: true })
      .filter((d) => d.isDirectory() && isCourseName(d.name))
      .map((d) => d.name)
  } catch {
    return []
  }
  const out: PackCandidate[] = []
  for (const course of names) {
    if (!hasHotMirror(course)) continue
    if (existsSync(path.join(tmpLogsDir(), course))) continue
    const meta = readMirrorMeta(course)
    const ok = decidePack({
      lastSourceActivity: meta.lastSourceActivity,
      now,
      days,
      tmpExists: false,
      newestArchiveMtime: newestArchiveMtime(course),
    })
    if (ok) out.push({ course, lastSourceActivity: meta.lastSourceActivity })
  }
  return out.sort((a, b) => a.lastSourceActivity - b.lastSourceActivity)
}

function uniqueGeneration(course: string, now: number): string {
  const base = generationName(course, stampFor(now))
  if (!existsSync(path.join(libraryRoot(), base))) return base
  for (let i = 1; ; i++) {
    const name = `${course}.${stampFor(now)}-${i}.tar.gz`
    if (!existsSync(path.join(libraryRoot(), name))) return name
  }
}

function pruneGenerations(course: string): void {
  let names: string[] = []
  try {
    names = readdirSync(libraryRoot())
  } catch {
    return
  }
  const gens = names
    .map((name) => ({ name, parsed: parseArchiveFileName(name) }))
    .filter((x) => x.parsed?.course === course && x.parsed.ts)
    .map((x) => x.name)
    .sort()
    .reverse()
  for (const name of gens.slice(GENERATION_KEEP)) {
    try {
      rmSync(path.join(libraryRoot(), name), { force: true })
      rmSync(path.join(libraryRoot(), name) + ARCHIVE_INDEX_SUFFIX, { force: true })
    } catch {
      /* ignore */
    }
  }
}

export interface PackResult {
  course: string
  bytes: number
  members: number
  generation: string | null
}

/** 把一门课的热备份打成冷存包；成功后删热目录。失败 ⇒ 原目录不动。 */
export async function compactCourse(course: string, now = Date.now()): Promise<PackResult> {
  if (existsSync(path.join(tmpLogsDir(), course))) {
    throw new Error(`tmp/${course} 仍存在（活体课不冷）`)
  }
  const root = libraryRoot()
  const archive = archivePathFor(course)
  const tmpPack = `${archive}.tmp`
  const tmpRel = `${course}.tar.gz.tmp`
  try {
    rmSync(tmpPack, { force: true })
  } catch {
    /* ignore */
  }
  const packed = await runTar(['-czf', tmpRel, course], root)
  if (packed.code !== 0) {
    rmSync(tmpPack, { force: true })
    throw new Error(`tar -czf 失败：${packed.stderr.trim().slice(0, 300)}`)
  }
  const listed = await runTar(['-tzf', tmpRel], root)
  if (listed.code !== 0) {
    rmSync(tmpPack, { force: true })
    throw new Error(`tar -tzf 失败：${listed.stderr.trim().slice(0, 300)}`)
  }
  const members = listed.stdout
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
  if (
    !members.includes(`${course}/mirror-meta.json`) ||
    !members.some((m) => m.startsWith(`${course}/`) && !m.endsWith('/'))
  ) {
    rmSync(tmpPack, { force: true })
    throw new Error('冷存包成员不完整（缺 meta 或四件）')
  }
  // 旧包先挪成世代；新包顶上。顺序：旧包改名 → 新包 rename → 写索引 → 删热目录。
  let generation: string | null = null
  if (existsSync(archive)) {
    generation = uniqueGeneration(course, now)
    renameSync(archive, path.join(root, generation))
    try {
      rmSync(archiveIndexPathFor(course), { force: true })
    } catch {
      /* ignore */
    }
  }
  renameSync(tmpPack, archive)
  const st = statSync(archive)
  const idx: ArchiveIndex = {
    version: 1,
    course,
    size: st.size,
    mtimeMs: st.mtimeMs,
    members,
    packedAt: now,
  }
  writeFileSync(archiveIndexPathFor(course), JSON.stringify(idx))
  rmSync(courseMirrorDir(course), { recursive: true, force: true })
  pruneGenerations(course)
  return { course, bytes: st.size, members: members.length, generation }
}

export interface CompactStats {
  packed: PackResult[]
  failed: Array<{ course: string; error: string }>
  skipped: number
  budgetHit: boolean
}

export interface CompactOptions {
  /** 本轮最多打包几门（ticker 传 1；CLI 不传 = 不限）。 */
  max?: number
  course?: string
  now?: number
  days?: number
  /** false = 调用方已持锁。 */
  lock?: boolean
}

export async function compactAll(opts: CompactOptions = {}): Promise<CompactStats> {
  const now = opts.now ?? Date.now()
  const days = opts.days ?? packDays()
  const max = opts.max ?? Number.POSITIVE_INFINITY
  const stats: CompactStats = { packed: [], failed: [], skipped: 0, budgetHit: false }
  const release = opts.lock === false ? () => {} : acquireLock(libraryRoot(), now)
  try {
    for (const c of packCandidates(now, days)) {
      if (opts.course && opts.course !== '*' && c.course !== opts.course) {
        stats.skipped++
        continue
      }
      if (stats.packed.length >= max) {
        stats.budgetHit = true
        break
      }
      try {
        // 打包前再同步一次：确保没有未转存字节（评审要求「先 sync 再 pack」）。
        syncAll({ course: c.course, budgetBytes: 0, now, lock: false })
        stats.packed.push(await compactCourse(c.course, now))
      } catch (e) {
        stats.failed.push({ course: c.course, error: e instanceof Error ? e.message : String(e) })
      }
    }
  } finally {
    release()
  }
  return stats
}
