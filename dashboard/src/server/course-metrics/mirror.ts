/** mirror.ts — 备份库的**写侧 IO 薄壳**：扫描 → 增量转存 → 复位归档 → 原子 meta。
 *
 *  纪律（plan §3.2-A / 评审 F3/F8/F9）：
 *   · 扫描面 = 白名单（`sourceCourses`），**不递归** `deliver/**` / `*.run/**` / `pytest-tmp`；
 *   · 稳态零正文读：stat 快路命中 ⇒ 不读一个字节；
 *   · 只抄到源的**最后一个完整行**（半行留给下一拍）；
 *   · 复位绝不静默覆盖：旧副本先 `rename` 进 `history/<ts>/`；
 *   · 单课/单文件失败只记数不抛；锁拿不到抛 `LockBusyError`（调用方决定跳过还是重试）。
 */

import { createHash } from 'crypto'
import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  readSync,
  renameSync,
  rmSync,
  statSync,
  utimesSync,
  writeFileSync,
  writeSync,
} from 'fs'
import os from 'os'
import path from 'path'
import { pidAlive } from '../../core/net'
import { archiveCoursesDir, courseMetricsDir, tmpLogsDir } from '../../core/paths'
import {
  anchorHex,
  budgetLeft,
  completeEnd,
  countNewlines,
  decideAppend,
  decideRewrite,
  stampFor,
  wantsCourse,
} from './engine'
import {
  ANCHOR_BYTES,
  HISTORY_KEEP,
  MAX_REWRITE_BYTES,
  MIRROR_FILES,
  REWRITE_FILE,
  type MirrorFileMeta,
  type MirrorFileName,
  type MirrorMeta,
  emptyMeta,
  isCourseName,
  parseMirrorMeta,
  relCandidates,
} from './meta'

const LOCK_NAME = '.lock'
const LOCK_STALE_MS = 10 * 60_000

export function libraryRoot(): string {
  return courseMetricsDir()
}

export function courseMirrorDir(course: string): string {
  return path.join(libraryRoot(), course)
}

export function mirrorMetaPath(course: string): string {
  return path.join(courseMirrorDir(course), 'mirror-meta.json')
}

// ────────────────────────── 锁（原子创建） ──────────────────────────

export class LockBusyError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'LockBusyError'
  }
}

interface LockBody {
  pid: number
  host: string
  startedAt: number
}

function readLockBody(p: string): LockBody | null {
  try {
    const v = JSON.parse(readFileSync(p, 'utf8')) as Partial<LockBody>
    if (typeof v.pid !== 'number' || typeof v.startedAt !== 'number') return null
    return { pid: v.pid, host: typeof v.host === 'string' ? v.host : '', startedAt: v.startedAt }
  } catch {
    return null
  }
}

/** 拿单写者锁（ticker ∥ CLI ∥ tmp-clean 转存）。返回释放函数；拿不到抛 `LockBusyError`。 */
export function acquireLock(root = libraryRoot(), now = Date.now()): () => void {
  mkdirSync(root, { recursive: true })
  const lockPath = path.join(root, LOCK_NAME)
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const fd = openSync(lockPath, 'wx')
      try {
        writeSync(
          fd,
          JSON.stringify({
            pid: process.pid,
            host: os.hostname(),
            startedAt: now,
          } satisfies LockBody),
        )
      } finally {
        closeSync(fd)
      }
      let released = false
      return () => {
        if (released) return
        released = true
        try {
          if (readLockBody(lockPath)?.pid === process.pid) rmSync(lockPath, { force: true })
        } catch {
          /* 已被别人收走 */
        }
      }
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== 'EEXIST') throw e
      const cur = readLockBody(lockPath)
      const age = cur ? now - cur.startedAt : Number.POSITIVE_INFINITY
      const sameHost = !cur || cur.host === os.hostname()
      // 死 pid / 同机超龄 / 外机超龄 ⇒ 陈旧；否则真的有人在写。
      const stale = !cur || age > LOCK_STALE_MS || (sameHost && !pidAlive(cur.pid))
      if (!stale) throw new LockBusyError(`备份库被 pid=${cur?.pid ?? '?'} 占用（${age}ms）`)
      try {
        renameSync(lockPath, `${lockPath}.stale.${stampFor(now)}`)
      } catch {
        throw new LockBusyError('陈旧锁抢占失败（别人抢先）')
      }
    }
  }
  throw new LockBusyError('拿不到备份库锁（两次尝试）')
}

// ────────────────────────── meta ──────────────────────────

export function readMirrorMeta(course: string): MirrorMeta {
  try {
    const parsed = parseMirrorMeta(JSON.parse(readFileSync(mirrorMetaPath(course), 'utf8')))
    if (parsed) return parsed
  } catch {
    /* 缺失/损坏 ⇒ 从零重建（复位路径会保住旧副本） */
  }
  return emptyMeta(course)
}

function writeMirrorMeta(course: string, meta: MirrorMeta): void {
  const p = mirrorMetaPath(course)
  mkdirSync(path.dirname(p), { recursive: true })
  const tmp = `${p}.tmp`
  writeFileSync(tmp, JSON.stringify(meta))
  renameSync(tmp, p)
}

// ────────────────────────── 源扫描（白名单） ──────────────────────────

export interface SourceFile {
  rel: string
  name: MirrorFileName
  path: string
}

export interface SourceCourse {
  course: string
  files: SourceFile[]
}

function isArchivedCourse(course: string): boolean {
  try {
    return existsSync(path.join(archiveCoursesDir(), course, 'archive-manifest.json'))
  } catch {
    return false
  }
}

/**
 * tmp 下**备份集**的源清单：顶层四件优先，`traj/` 只在顶层缺该件时补。
 * 只扫一层目录 + 定点 `stat`，不递归（评审 F3）。
 */
export function sourceCourses(): SourceCourse[] {
  const root = tmpLogsDir()
  let names: string[] = []
  try {
    names = readdirSync(root, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
  } catch {
    return []
  }
  const out: SourceCourse[] = []
  for (const course of names.sort()) {
    if (!isCourseName(course) || isArchivedCourse(course)) continue
    const files: SourceFile[] = []
    for (const name of MIRROR_FILES) {
      for (const rel of relCandidates(name)) {
        const p = path.join(root, course, rel)
        try {
          if (!statSync(p).isFile()) continue
        } catch {
          continue
        }
        files.push({ rel, name, path: p })
        break
      }
    }
    if (files.length > 0) out.push({ course, files })
  }
  return out
}

// ────────────────────────── 读文件小工具 ──────────────────────────

/** `onBytes` = 源正文读计数（ticker 的「稳态零正文读」判据，plan §3.7/T1）。 */
function readHex(p: string, pos: number, len: number, onBytes?: (n: number) => void): string {
  if (len <= 0) return ''
  const buf = Buffer.alloc(len)
  let fh: number | null = null
  try {
    fh = openSync(p, 'r')
    const got = readSync(fh, buf, 0, len, pos)
    onBytes?.(Math.max(0, got))
    return buf.subarray(0, Math.max(0, got)).toString('hex')
  } catch {
    return ''
  } finally {
    if (fh !== null) closeSync(fh)
  }
}

/** 源当前**完整行**边界（从尾部按 1MiB 往回找最后一个 `\n`；找不到 ⇒ 0）。 */
export function completeEndOfFile(p: string, size: number, onBytes?: (n: number) => void): number {
  const CHUNK = 1024 * 1024
  let end = size
  let fh: number | null = null
  try {
    fh = openSync(p, 'r')
    while (end > 0) {
      const len = Math.min(CHUNK, end)
      const buf = Buffer.alloc(len)
      const got = readSync(fh, buf, 0, len, end - len)
      if (got <= 0) return 0
      onBytes?.(got)
      const idx = completeEnd(buf.subarray(0, got))
      if (idx > 0) return end - got + idx
      end -= got
      if (size - end > 8 * CHUNK) return 0 // 8MiB 无换行 = 病态，别把整文件读进内存
    }
    return 0
  } catch {
    return 0
  } finally {
    if (fh !== null) closeSync(fh)
  }
}

interface CopyResult {
  bytes: number
  lines: number
}

function copyRange(src: string, dest: string, from: number, to: number): CopyResult {
  const CHUNK = 1024 * 1024
  const buf = Buffer.alloc(CHUNK)
  let bytes = 0
  let lines = 0
  let rfh: number | null = null
  let wfh: number | null = null
  try {
    rfh = openSync(src, 'r')
    wfh = openSync(dest, 'a')
    let pos = from
    while (pos < to) {
      const len = Math.min(CHUNK, to - pos)
      const got = readSync(rfh, buf, 0, len, pos)
      if (got <= 0) break
      writeSync(wfh, buf, 0, got)
      lines += countNewlines(buf, 0, got)
      bytes += got
      pos += got
    }
    fsyncSync(wfh)
    return { bytes, lines }
  } finally {
    if (wfh !== null) closeSync(wfh)
    if (rfh !== null) closeSync(rfh)
  }
}

function moveToHistory(course: string, rel: string, now: number): void {
  const dest = path.join(courseMirrorDir(course), rel)
  if (!existsSync(dest)) return
  const base = path.join(courseMirrorDir(course), 'history', stampFor(now))
  let dir = base
  for (let i = 1; existsSync(dir); i++) dir = `${base}-${i}`
  mkdirSync(dir, { recursive: true })
  renameSync(dest, path.join(dir, path.basename(rel)))
  pruneHistory(course)
}

function pruneHistory(course: string): void {
  const root = path.join(courseMirrorDir(course), 'history')
  try {
    const dirs = readdirSync(root, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
      .sort()
      .reverse()
    for (const name of dirs.slice(HISTORY_KEEP)) {
      rmSync(path.join(root, name), { recursive: true, force: true })
    }
  } catch {
    /* history 不存在 */
  }
}

// ────────────────────────── sync ──────────────────────────

export interface SyncStats {
  courses: number
  files: number
  appended: number
  rewrites: number
  bytes: number
  lines: number
  skipped: number
  /** 本轮从**源**读进的正文字节（锚点 + 尾窗 + 抄写 + 重写型整件）。稳态应为 0（T1）。 */
  sourceBytesRead: number
  resets: Array<{ course: string; rel: string; reason: string }>
  failed: Array<{ course: string; rel: string; error: string }>
  budgetHit: boolean
}

export interface SyncOptions {
  /** 只同步这门课（CLI `--course`）；空/`*` = 全部。 */
  course?: string
  /** 本轮最多抄多少字节；0/缺省 = 不限（ticker 传 64MiB）。 */
  budgetBytes?: number
  now?: number
  /** false = 不拿锁（调用方已持有；ticker 的 sync+pack 同一拍）。 */
  lock?: boolean
}

export function emptySyncStats(): SyncStats {
  return {
    courses: 0,
    files: 0,
    appended: 0,
    rewrites: 0,
    bytes: 0,
    lines: 0,
    skipped: 0,
    sourceBytesRead: 0,
    resets: [],
    failed: [],
    budgetHit: false,
  }
}

/** 全量/单课增量转存。拿不到锁抛 `LockBusyError`；单个文件失败只进 `stats.failed`。 */
export function syncAll(opts: SyncOptions = {}): SyncStats {
  const root = libraryRoot()
  const now = opts.now ?? Date.now()
  const budget = opts.budgetBytes ?? 0
  const stats = emptySyncStats()
  const release = opts.lock === false ? () => {} : acquireLock(root, now)
  try {
    for (const sc of sourceCourses()) {
      if (!wantsCourse(opts.course, sc.course)) continue
      if (budgetLeft(budget, stats.bytes) <= 0) {
        stats.budgetHit = true
        break
      }
      stats.courses++
      syncCourse(sc, stats, { now, budget })
    }
  } finally {
    release()
  }
  return stats
}

function syncCourse(
  sc: SourceCourse,
  stats: SyncStats,
  ctx: { now: number; budget: number },
): void {
  const meta = readMirrorMeta(sc.course)
  let dirty = false
  for (const f of sc.files) {
    if (budgetLeft(ctx.budget, stats.bytes) <= 0) {
      stats.budgetHit = true
      break
    }
    try {
      const changed =
        f.name === REWRITE_FILE
          ? syncRewrite(sc.course, f, meta, stats)
          : syncAppend(sc.course, f, meta, ctx, stats)
      if (!changed) stats.skipped++
      else {
        stats.files++
        if (f.name === REWRITE_FILE) stats.rewrites++
        dirty = true
      }
    } catch (e) {
      stats.failed.push({
        course: sc.course,
        rel: f.rel,
        error: e instanceof Error ? e.message : String(e),
      })
    }
  }
  if (dirty) {
    meta.lastSourceActivity = Object.values(meta.files).reduce((m, f) => Math.max(m, f.srcMtime), 0)
    try {
      writeMirrorMeta(sc.course, meta)
    } catch (e) {
      stats.failed.push({
        course: sc.course,
        rel: 'mirror-meta.json',
        error: e instanceof Error ? e.message : String(e),
      })
    }
  }
}

function syncAppend(
  course: string,
  f: SourceFile,
  meta: MirrorMeta,
  ctx: { now: number; budget: number },
  stats: SyncStats,
): boolean {
  const st = statSync(f.path)
  const dest = path.join(courseMirrorDir(course), f.rel)
  let prev: MirrorFileMeta | undefined = meta.files[f.rel]
  let destSize = -1
  try {
    destSize = statSync(dest).size
  } catch {
    destSize = -1
  }
  // ★ 稳态 stat 快路（plan §3.2-A1 / 评审 F9）：**源没变 ∧ 备份侧尺寸等于游标** ⇒ 连尾窗都不读。
  //   先算 `completeEndOfFile` 再判 skip 会把每拍变成「每文件 1MiB 读」，稳态不再零成本；
  //   备份侧尺寸也进快路，是为了让「备份文件被删/改短」能被发现（否则永远跳过 → 静默缺字节）。
  if (
    prev &&
    prev.mode === 'append' &&
    st.size === prev.bytes &&
    st.mtimeMs === prev.srcMtime &&
    destSize === prev.bytes
  ) {
    return false
  }
  mkdirSync(path.dirname(dest), { recursive: true })
  // 备份文件被删/被改短 ⇒ 游标失效：旧副本（若还在）进 history，从 0 重抄。
  // 绝不能「从旧游标续写」——那会写出一份缺前缀的镜像，`verify` 与冷存都会跟着错。
  if (prev && prev.mode !== 'append') {
    // 同一路径从重写型变回 append 型（meta 曾手写/换型）：旧字节不能当 append 前缀。
    if (existsSync(dest)) moveToHistory(course, f.rel, ctx.now)
    stats.resets.push({ course, rel: f.rel, reason: 'mode-changed' })
    prev = undefined
  }
  if (prev && prev.mode === 'append') {
    if (destSize !== prev.bytes) {
      if (destSize > 0) moveToHistory(course, f.rel, ctx.now)
      stats.resets.push({ course, rel: f.rel, reason: 'mirror-missing' })
      prev = undefined
    }
  }
  const end = completeEndOfFile(f.path, st.size, (n) => {
    stats.sourceBytesRead += n
  })
  let anchor = ''
  if (prev && prev.mode === 'append' && prev.bytes > 0 && st.size >= prev.bytes) {
    const n = Math.min(ANCHOR_BYTES, prev.bytes)
    anchor = readHex(f.path, prev.bytes - n, n, (got) => {
      stats.sourceBytesRead += got
    })
  }
  let plan = decideAppend(prev, st, anchor, end)
  // 没有 meta 却有旧副本：绝不能「追加到未知前缀」（会重复字节）⇒ 先归档再从 0 抄。
  if (!prev && existsSync(dest) && statSync(dest).size > 0 && end > 0) {
    moveToHistory(course, f.rel, ctx.now)
    stats.resets.push({ course, rel: f.rel, reason: 'missing-meta' })
    plan = decideAppend(undefined, st, '', end)
  }
  if (plan.kind === 'reset') {
    moveToHistory(course, f.rel, ctx.now)
    stats.resets.push({ course, rel: f.rel, reason: plan.reason })
    prev = undefined
    plan = decideAppend(undefined, st, '', end)
  }
  if (plan.kind !== 'append') return false
  const got = copyRange(f.path, dest, plan.from, plan.to)
  stats.bytes += got.bytes
  stats.lines += got.lines
  stats.sourceBytesRead += got.bytes
  stats.appended++
  const t = st.mtimeMs / 1000
  try {
    utimesSync(dest, t, t)
  } catch {
    /* 时间戳失败不影响字节正确性 */
  }
  const n = Math.min(ANCHOR_BYTES, plan.to)
  meta.files[f.rel] = {
    bytes: plan.to,
    lines: (prev?.lines ?? 0) + got.lines,
    // 备份侧锚点：读的是**备份文件**，不计入源正文读。
    anchor: readHex(dest, plan.to - n, n),
    srcMtime: st.mtimeMs,
    mode: 'append',
  }
  return true
}

function syncRewrite(course: string, f: SourceFile, meta: MirrorMeta, stats: SyncStats): boolean {
  const st = statSync(f.path)
  if (st.size > MAX_REWRITE_BYTES) {
    throw new Error(`重写型文件 ${st.size}B 超上限 ${MAX_REWRITE_BYTES}B（拒绝镜像）`)
  }
  // 重写型**必须**整件读一遍算 sha（评审 F9：同尺寸同 mtime 的改写只有内容能看见）。
  const buf = readFileSync(f.path)
  stats.sourceBytesRead += buf.byteLength
  JSON.parse(buf.toString('utf8')) // 坏 JSON 不镜像（避免把半份缓存当事实）
  const sha = createHash('sha256').update(buf).digest('hex')
  const prev = meta.files[f.rel]
  const action = decideRewrite(prev, sha)
  const dest = path.join(courseMirrorDir(course), f.rel)
  mkdirSync(path.dirname(dest), { recursive: true })
  if (action === 'write') {
    const tmp = `${dest}.tmp`
    writeFileSync(tmp, buf)
    renameSync(tmp, dest)
    const t = st.mtimeMs / 1000
    try {
      utimesSync(dest, t, t)
    } catch {
      /* 同上 */
    }
  }
  meta.files[f.rel] = {
    bytes: st.size,
    lines: countNewlines(buf),
    anchor: anchorHex(buf, buf.length),
    srcMtime: st.mtimeMs,
    mode: 'rewrite',
    sha256: sha,
  }
  return action === 'write'
}

// ────────────────────────── 对账 / 状态（可测形状） ──────────────────────────

export interface VerifyIssue {
  course: string
  rel: string
  reason: string
}

function hashFile(p: string, size: number): string {
  const CHUNK = 1024 * 1024
  const buf = Buffer.alloc(CHUNK)
  const h = createHash('sha256')
  let fh: number | null = null
  try {
    fh = openSync(p, 'r')
    let pos = 0
    while (pos < size) {
      const got = readSync(fh, buf, 0, Math.min(CHUNK, size - pos), pos)
      if (got <= 0) break
      h.update(buf.subarray(0, got))
      pos += got
    }
    return h.digest('hex')
  } finally {
    if (fh !== null) closeSync(fh)
  }
}

/** 热备份逐文件对账：size / 行数 / 重写型 sha / append 型锚点。 */
export function verifyHot(): VerifyIssue[] {
  const issues: VerifyIssue[] = []
  let names: string[] = []
  try {
    names = readdirSync(libraryRoot(), { withFileTypes: true })
      .filter((d) => d.isDirectory() && isCourseName(d.name))
      .map((d) => d.name)
  } catch {
    return issues
  }
  for (const course of names) {
    const meta = readMirrorMeta(course)
    for (const [rel, fm] of Object.entries(meta.files)) {
      const p = path.join(courseMirrorDir(course), rel)
      let st
      try {
        st = statSync(p)
      } catch {
        issues.push({ course, rel, reason: '备份文件缺失' })
        continue
      }
      if (st.size !== fm.bytes) {
        issues.push({ course, rel, reason: `尺寸 ${st.size} ≠ meta ${fm.bytes}` })
        continue
      }
      if (fm.mode === 'rewrite') {
        const sha = hashFile(p, st.size)
        if (sha !== fm.sha256) issues.push({ course, rel, reason: 'sha256 不符' })
      } else {
        const n = Math.min(ANCHOR_BYTES, fm.bytes)
        const tail = readHex(p, fm.bytes - n, n)
        if (tail !== fm.anchor) issues.push({ course, rel, reason: '锚点不符' })
      }
    }
  }
  return issues
}

export interface MirrorStatusFile {
  rel: string
  srcBytes: number | null
  mirrorBytes: number
  lagBytes: number
  srcMtime: number
}

export interface MirrorStatusRow {
  course: string
  mode: 'hot' | 'cold'
  archiveBytes: number | null
  lastSourceActivity: number
  packInDays: number | null
  files: MirrorStatusFile[]
}

export function statusRows(now = Date.now(), packDays = 14): MirrorStatusRow[] {
  const root = libraryRoot()
  const byCourse = new Map<string, MirrorStatusRow>()
  const srcs = new Map(sourceCourses().map((s) => [s.course, s]))
  let names: string[] = []
  try {
    names = readdirSync(root, { withFileTypes: true })
      .filter((d) => d.isDirectory() && isCourseName(d.name))
      .map((d) => d.name)
  } catch {
    /* 库不存在 */
  }
  for (const course of names) {
    const meta = readMirrorMeta(course)
    const src = srcs.get(course)
    const files: MirrorStatusFile[] = Object.entries(meta.files).map(([rel, fm]) => {
      let srcBytes: number | null = null
      const sf = src?.files.find((f) => f.rel === rel)
      if (sf) {
        try {
          srcBytes = statSync(sf.path).size
        } catch {
          srcBytes = null
        }
      }
      return {
        rel,
        srcBytes,
        mirrorBytes: fm.bytes,
        lagBytes: srcBytes == null ? 0 : Math.max(0, srcBytes - fm.bytes),
        srcMtime: fm.srcMtime,
      }
    })
    byCourse.set(course, {
      course,
      mode: 'hot',
      archiveBytes: null,
      lastSourceActivity: meta.lastSourceActivity,
      packInDays:
        meta.lastSourceActivity > 0
          ? +(packDays - (now - meta.lastSourceActivity) / 86_400_000).toFixed(1)
          : null,
      files,
    })
  }
  return [...byCourse.values()].sort((a, b) => b.lastSourceActivity - a.lastSourceActivity)
}

/** 热备份目录是否存在（compact 判据用）。 */
export function hasHotMirror(course: string): boolean {
  return existsSync(mirrorMetaPath(course))
}

/** 库内热备份课程（有 `mirror-meta.json` 的目录）。 */
export function listMirrorCourses(): string[] {
  try {
    return readdirSync(libraryRoot(), { withFileTypes: true })
      .filter((d) => d.isDirectory() && isCourseName(d.name))
      .map((d) => d.name)
      .filter((name) => hasHotMirror(name))
  } catch {
    return []
  }
}

/** 某课是否已有热备份文件（读侧 mirror 态判据）。 */
export function mirrorFileExists(course: string, rel: string): boolean {
  try {
    return statSync(path.join(courseMirrorDir(course), rel)).isFile()
  } catch {
    return false
  }
}
