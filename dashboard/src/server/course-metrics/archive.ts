/** archive.ts — 冷存包的**读侧**：成员索引（不解包判存在）+ 异步内存解压 + 有界 LRU。
 *
 *  两条硬约束（评审 F1/F2）：
 *   ① **异步**：`Bun.spawn` + `Response(...).arrayBuffer()`，绝不 `spawnSync`（dashboard 是单进程，
 *      sync 一次 37MB gzip 会把 `/api/state` 与三条节拍一起停住；Node 语义的 `maxBuffer` 还会
 *      在 1MiB 处直接杀子进程）；
 *   ② **存在性不靠解包**：gzip 不可随机寻址，`tar -tzf` 等价整包解压。打包时写
 *      `<课>.tar.gz.index.json`，读侧只读它（`resolveCourseFile` 在请求路径上）。
 */

import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'fs'
import path from 'path'
import { anchorHex, countNewlines } from './engine'
import { libraryRoot } from './mirror'
import {
  ARCHIVE_INDEX_SUFFIX,
  MIRROR_FILES,
  type ArchiveIndex,
  type MirrorMeta,
  isCourseName,
  memberName,
  parseArchiveIndex,
  parseMirrorMeta,
} from './meta'

const DEFAULT_MEMO_MB = 256

export function memoCapBytes(): number {
  const raw = Number(process.env.BCITY_COURSE_METRICS_MEMO_MB ?? '')
  return (Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_MEMO_MB) * 1024 * 1024
}

export function tarBin(): string {
  return process.env.BCITY_TAR || 'tar'
}

// ────────────────────────── tar 自检（评审 F10） ──────────────────────────
//
// 为什么一次性自检而不是逐请求重试：`tar` 的真身取决于谁启动服务（Git GNU tar 1.35 vs
// `C:\Windows\System32\tar.exe` = bsdtar），`-xzO` / 成员名 / 退出码语义不完全一致。
// 冷存包是**最终副本**，解压失败若 fail-soft 成空态，就是这次事故要消灭的「静默消失」。
// 故：首次用到时探一次，失败 ⇒ ERROR 一行 + **关闭 archive 源**（读侧回 source:'none'，
// 而不是每次请求再 spawn 一个必败的子进程），人可用 `restore`/`cat` 换 `BCITY_TAR` 后重试。

let tarProbe: Promise<boolean> | null = null
let tarUsable = true

/** 冷存源此刻是否可用（自检失败后为 false；未知时先按可用处理）。 */
export function archiveSourceEnabled(): boolean {
  return tarUsable
}

/** 测试接缝：忘掉自检结果（改 `BCITY_TAR` 后重新探）。 */
export function __resetTarProbe(): void {
  tarProbe = null
  tarUsable = true
}

async function ensureTarUsable(): Promise<boolean> {
  if (!tarProbe) {
    tarProbe = (async (): Promise<boolean> => {
      try {
        const proc = Bun.spawn([tarBin(), '--version'], {
          stdout: 'pipe',
          stderr: 'pipe',
          windowsHide: true,
        })
        const [err, code] = await Promise.all([
          new Response(proc.stderr as ReadableStream).text(),
          proc.exited,
        ])
        if (code !== 0) throw new Error(err.trim() || `退出码 ${code}`)
        return true
      } catch (e) {
        tarUsable = false
        console.error(
          `[course-metrics] tar 自检失败（${tarBin()}）：${e instanceof Error ? e.message : String(e)}` +
            ' —— 冷存读已关闭（包仍在盘上；换 BCITY_TAR 后重启，或用 restore/cat 手工读）',
        )
        return false
      }
    })()
  }
  return tarProbe
}

export function archivePathFor(course: string): string {
  return path.join(libraryRoot(), `${course}.tar.gz`)
}

export function archiveIndexPathFor(course: string): string {
  return `${archivePathFor(course)}${ARCHIVE_INDEX_SUFFIX}`
}

/** 读索引（sync，零解包）。尺寸对不上 ⇒ 视为失效（宁可不宣称 archive）。 */
export function readArchiveIndex(course: string): ArchiveIndex | null {
  try {
    const idx = parseArchiveIndex(JSON.parse(readFileSync(archiveIndexPathFor(course), 'utf8')))
    if (!idx || idx.course !== course) return null
    const st = statSync(archivePathFor(course))
    if (st.size !== idx.size) return null
    return idx
  } catch {
    return null
  }
}

/** 冷存包里是否有该成员（resolver 用；不 spawn）。 */
export function archiveHasMember(course: string, rel: string): boolean {
  if (!tarUsable) return false
  const idx = readArchiveIndex(course)
  return !!idx && idx.members.includes(memberName(course, rel))
}

/** 库内冷存课（只列当前世代 `<课>.tar.gz`；旧世代保留但不进读面）。 */
export function coldCourses(): string[] {
  const root = libraryRoot()
  let names: string[] = []
  try {
    names = readdirSync(root)
  } catch {
    return []
  }
  const out: string[] = []
  for (const name of names) {
    if (!name.endsWith('.tar.gz')) continue
    const course = name.slice(0, -'.tar.gz'.length)
    if (!isCourseName(course)) continue
    if (readArchiveIndex(course)) out.push(course)
  }
  return out.sort()
}

// ────────────────────────── memo（有界 LRU，存 Promise 去重） ──────────────────────────

interface MemoEntry {
  size: number
  mtimeMs: number
  files: Map<string, Buffer>
  bytes: number
}

const memo = new Map<string, MemoEntry>()
const inflight = new Map<string, Promise<Buffer | null>>()
let memoBytes = 0
let evictions = 0
let spawns = 0

export function archiveMemoStats(): {
  entries: number
  bytes: number
  capBytes: number
  evictions: number
  spawns: number
  inflight: number
} {
  return {
    entries: memo.size,
    bytes: memoBytes,
    capBytes: memoCapBytes(),
    evictions,
    spawns,
    inflight: inflight.size,
  }
}

export function __clearArchiveMemo(): void {
  memo.clear()
  inflight.clear()
  memoBytes = 0
  evictions = 0
  spawns = 0
}

function touch(key: string): MemoEntry | undefined {
  const hit = memo.get(key)
  if (!hit) return undefined
  memo.delete(key) // 重新插入 = LRU 末尾
  memo.set(key, hit)
  return hit
}

function evictIfNeeded(): void {
  const cap = memoCapBytes()
  while (memoBytes > cap && memo.size > 0) {
    const [key, entry] = memo.entries().next().value as [string, MemoEntry]
    memo.delete(key)
    memoBytes -= entry.bytes
    evictions++
  }
}

async function extractRaw(course: string, member: string): Promise<Buffer | null> {
  let proc: ReturnType<typeof Bun.spawn>
  try {
    // cwd = 库根 + 相对包名：**绝不把盘符路径交给 `-f`**（GNU tar 把 `C:` 当远程主机，
    // 2026-10-18 实测报 `Cannot connect to C: resolve failed`）。
    proc = Bun.spawn([tarBin(), '-xzO', '-f', `${course}.tar.gz`, member], {
      cwd: libraryRoot(),
      stdout: 'pipe',
      stderr: 'pipe',
      windowsHide: true,
    })
  } catch (e) {
    console.error(
      `[course-metrics] 无法启动 ${tarBin()}：${e instanceof Error ? e.message : String(e)}`,
    )
    return null
  }
  spawns++
  try {
    const [buf, errText, code] = await Promise.all([
      new Response(proc.stdout as ReadableStream).arrayBuffer(),
      new Response(proc.stderr as ReadableStream).text(),
      proc.exited,
    ])
    if (code !== 0) {
      console.warn(`[course-metrics] 冷存解压失败（${member}）：${errText.trim().slice(0, 300)}`)
      return null
    }
    return Buffer.from(buf)
  } catch (e) {
    console.warn(
      `[course-metrics] 冷存解压异常（${member}）：${e instanceof Error ? e.message : String(e)}`,
    )
    return null
  }
}

/** 读一个成员（异步、内存解压、不落盘）。不存在/损坏 ⇒ null（fail-soft，调用方显示空态）。 */
export async function readArchiveMember(course: string, rel: string): Promise<Buffer | null> {
  if (!(await ensureTarUsable())) return null
  const archivePath = archivePathFor(course)
  let st
  try {
    st = statSync(archivePath)
  } catch {
    return null
  }
  const key = `${archivePath}::${rel}`
  const hit = touch(archivePath)
  if (hit && hit.size === st.size && hit.mtimeMs === st.mtimeMs) {
    const b = hit.files.get(rel)
    if (b) return b
  }
  // 包被重打包（size/mtime 变了）：旧条目整条作废，字节账也一起减掉——
  // 否则 `memoBytes` 会只增不减，把 LRU 提前逐空（账要诚实，逐出才可解释）。
  if (hit && (hit.size !== st.size || hit.mtimeMs !== st.mtimeMs)) {
    memo.delete(archivePath)
    memoBytes -= hit.bytes
  }
  const pending = inflight.get(key)
  if (pending) return pending
  const p = (async (): Promise<Buffer | null> => {
    const buf = await extractRaw(course, memberName(course, rel))
    if (!buf) return null
    let entry = touch(archivePath)
    if (!entry || entry.size !== st.size || entry.mtimeMs !== st.mtimeMs) {
      entry = { size: st.size, mtimeMs: st.mtimeMs, files: new Map(), bytes: 0 }
      memo.set(archivePath, entry)
    }
    const before = entry.files.get(rel)?.byteLength ?? 0
    entry.files.set(rel, buf)
    entry.bytes += buf.byteLength - before
    memoBytes += buf.byteLength - before
    evictIfNeeded()
    return buf
  })()
  inflight.set(key, p)
  try {
    return await p
  } finally {
    inflight.delete(key)
  }
}

export async function readArchiveText(course: string, rel: string): Promise<string | null> {
  const buf = await readArchiveMember(course, rel)
  return buf ? buf.toString('utf8') : null
}

/** 冷存包的 `mirror-meta`（读侧判 actuals/行数用；解压一次后进 memo）。 */
export async function readArchiveMeta(course: string): Promise<MirrorMeta | null> {
  const txt = await readArchiveText(course, 'mirror-meta.json')
  if (!txt) return null
  try {
    return parseMirrorMeta(JSON.parse(txt))
  } catch {
    return null
  }
}

/** 重建索引（打包后写；索引缺失时由调用方显式触发一次，不在请求路径上跑）。 */
export async function rebuildArchiveIndex(course: string): Promise<ArchiveIndex | null> {
  if (!(await ensureTarUsable())) return null
  const archivePath = archivePathFor(course)
  let st
  try {
    st = statSync(archivePath)
  } catch {
    return null
  }
  let proc: ReturnType<typeof Bun.spawn>
  try {
    proc = Bun.spawn([tarBin(), '-tzf', `${course}.tar.gz`], {
      cwd: libraryRoot(),
      stdout: 'pipe',
      stderr: 'pipe',
      windowsHide: true,
    })
  } catch {
    return null
  }
  const [out, code] = await Promise.all([
    new Response(proc.stdout as ReadableStream).text(),
    proc.exited,
  ])
  if (code !== 0) return null
  const members = out
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
  const idx: ArchiveIndex = {
    version: 1,
    course,
    size: st.size,
    mtimeMs: st.mtimeMs,
    members,
    packedAt: Date.now(),
  }
  writeFileSync(archiveIndexPathFor(course), JSON.stringify(idx))
  return idx
}

/** 冷存对账：成员尺寸/行数/哈希与包内 `mirror-meta.json` 对账（`verify` 用）。 */
export async function verifyCold(): Promise<
  Array<{ course: string; rel: string; reason: string }>
> {
  const issues: Array<{ course: string; rel: string; reason: string }> = []
  if (!(await ensureTarUsable())) {
    // 冷存包存在但读不了 ⇒ 必须红（不能因为「tar 不在」就宣称对账通过）。
    for (const course of coldCourses()) {
      issues.push({ course, rel: 'tar', reason: `tar 自检失败（${tarBin()}）：冷存包无法对账` })
    }
    return issues
  }
  for (const course of coldCourses()) {
    const meta = await readArchiveMeta(course)
    if (!meta) {
      issues.push({ course, rel: 'mirror-meta.json', reason: '包内 meta 缺失/不可解析' })
      continue
    }
    for (const [rel, fm] of Object.entries(meta.files)) {
      const buf = await readArchiveMember(course, rel)
      if (!buf) {
        issues.push({ course, rel, reason: '成员缺失' })
        continue
      }
      if (buf.byteLength !== fm.bytes) {
        issues.push({ course, rel, reason: `尺寸 ${buf.byteLength} ≠ meta ${fm.bytes}` })
        continue
      }
      if (fm.mode === 'rewrite') {
        const sha = new Bun.CryptoHasher('sha256').update(buf).digest('hex')
        if (sha !== fm.sha256) issues.push({ course, rel, reason: 'sha256 不符' })
      } else {
        if (countNewlines(buf) !== fm.lines) issues.push({ course, rel, reason: '行数不符' })
        if (anchorHex(buf, buf.byteLength) !== fm.anchor) {
          issues.push({ course, rel, reason: '锚点不符' })
        }
      }
    }
  }
  return issues
}

/** 冷存课在库内的文件尺寸（status 用）。 */
export function coldArchiveBytes(course: string): number | null {
  try {
    return statSync(archivePathFor(course)).size
  } catch {
    return null
  }
}

/** 冷存包里是否存在白名单成员（打包 sanity 用；不入读路径）。 */
export function expectedMembers(course: string): string[] {
  return [
    memberName(course, 'mirror-meta.json'),
    ...MIRROR_FILES.flatMap((f) => [memberName(course, f), memberName(course, `traj/${f}`)]),
  ]
}

export function coldArchiveExists(course: string): boolean {
  return existsSync(archivePathFor(course))
}
