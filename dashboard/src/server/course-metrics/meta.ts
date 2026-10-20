/** meta.ts — 课程指标备份库的存储契约（`mirror-meta.json` / 冷存索引 / 文件白名单的唯一定义）。
 *
 *  为什么把契约单独一层：`engine.ts`（纯函数）、`mirror.ts`（IO）、`compact.ts`（打包）、
 *  `archive.ts`（读）与 CLI 都要用同一套文件名单与游标语义；任何一处手写字符串都会在
 *  「转存了但读不到」时才发现（plan/course-metrics-persistence §3.1/§3.2）。
 *
 *  ★ 白名单是**扫描面**的唯一真相（评审 F3）：`deliver/` 下也有 `eval_log.jsonl` 副本（实测 95MB），
 *  `pytest-tmp/**` / `*.run/**` 也有临时账本。它们**不是**课程，进库既浪费又会让课程发现长出幽灵课。
 */

/** 备份集 = 恰好这四个文件（plan §2）。顺序只用于稳定输出，不承载语义。 */
export const MIRROR_FILES = [
  'training_log.jsonl',
  'eval_log.jsonl',
  'dist-agent-meta.jsonl',
  '.pool-actuals-cache.json',
] as const
export type MirrorFileName = (typeof MIRROR_FILES)[number]

/** 重写型（整份 JSON 替换）——其余三个是 append-only。 */
export const REWRITE_FILE: MirrorFileName = '.pool-actuals-cache.json'

/** 重写型单件上限（>8MB 拒绝 + WARN；覆盖不全时清理侧会跳过该目标而不是硬删）。 */
export const MAX_REWRITE_BYTES = 8 * 1024 * 1024

/** 锚点字节数（与 `pool-history.ts::readAnchor` 同口径）。 */
export const ANCHOR_BYTES = 64

/** `history/` 复位归档保留条数（每次复位可能拷一份 37MB eval_log）。 */
export const HISTORY_KEEP = 8

/** 冷存世代包保留个数。 */
export const GENERATION_KEEP = 2

/** 冷存索引后缀：挂在包名之后 ⇒ `<课>.tar.gz.index.json`。 */
export const ARCHIVE_INDEX_SUFFIX = '.index.json'

export interface MirrorFileMeta {
  /** 已转存字节（append 型 = 已确认的完整行边界；重写型 = 当前文件大小）。 */
  bytes: number
  /** 已转存完整行数（append 型累加；重写型 = 文件内 `\n` 数）。 */
  lines: number
  /** 已转存区域的尾部 64B 十六进制（append 型锚点；0 字节时为空串）。 */
  anchor: string
  /** 源文件最后一次被看到时的 mtimeMs（= 「最后源活动」的组成）。 */
  srcMtime: number
  mode: 'append' | 'rewrite'
  /** 重写型的 sha256（append 型不填）。 */
  sha256?: string
}

export interface MirrorMeta {
  version: 1
  course: string
  /** 键 = 源相对路径（`<文件>` 或 `traj/<文件>`）。 */
  files: Record<string, MirrorFileMeta>
  /** `max(files[*].srcMtime)`：冷存打包判据（plan §3.2-B）。 */
  lastSourceActivity: number
}

export interface ArchiveIndex {
  version: 1
  course: string
  /** 索引对应的包大小/修改时间（对不上 ⇒ 索引失效，宁可不宣称 archive）。 */
  size: number
  mtimeMs: number
  /** 包内成员名（`<课>/<文件>` / `<课>/traj/<文件>` / `<课>/mirror-meta.json` / history）。 */
  members: string[]
  packedAt: number
}

/** 课程目录名白名单（同时用于 tmp 扫描与库内目录；防路径穿越与幽灵目录）。 */
export function isCourseName(name: string): boolean {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(name)) return false
  // 测试/探针/库自身子目录不是课程（`pytest-tmp` 下有形如课程的临时账本）。
  if (name === 'pytest-tmp' || name === 'backups' || name === 'deliver') return false
  // `tmp/<name>.jsonl.run/` = 独立评估输出目录（有 eval_log/dist-agent-meta，没有
  // training_log）——**不是课程**。2026-10-20 报障：这些目录被转存后，课程发现把
  // `ab-v6-*.jsonl.run` 当成备份课捞进左下角 select。
  if (name.endsWith('.run')) return false
  return !name.startsWith('_')
}

/** 源相对路径候选（顶层布局优先，`traj/` 次之——与 `eval-board/ingest.ts` 同序）。 */
export function relCandidates(name: MirrorFileName): string[] {
  return [name, `traj/${name}`]
}

export function isRewriteRel(rel: string): boolean {
  return rel === REWRITE_FILE || rel === `traj/${REWRITE_FILE}`
}

export function emptyMeta(course: string): MirrorMeta {
  return { version: 1, course, files: {}, lastSourceActivity: 0 }
}

function isFileMeta(v: unknown): v is MirrorFileMeta {
  if (v == null || typeof v !== 'object') return false
  const m = v as Record<string, unknown>
  return (
    typeof m.bytes === 'number' &&
    Number.isFinite(m.bytes) &&
    m.bytes >= 0 &&
    typeof m.lines === 'number' &&
    Number.isFinite(m.lines) &&
    m.lines >= 0 &&
    typeof m.anchor === 'string' &&
    typeof m.srcMtime === 'number' &&
    Number.isFinite(m.srcMtime) &&
    (m.mode === 'append' || m.mode === 'rewrite') &&
    (m.sha256 === undefined || typeof m.sha256 === 'string')
  )
}

/** 校验并归一 `mirror-meta.json`。任何一处不合法 ⇒ null（调用方从零重建，不猜）。 */
export function parseMirrorMeta(raw: unknown): MirrorMeta | null {
  if (raw == null || typeof raw !== 'object') return null
  const m = raw as Record<string, unknown>
  if (m.version !== 1 || typeof m.course !== 'string' || !isCourseName(m.course)) return null
  const filesRaw = m.files
  if (filesRaw == null || typeof filesRaw !== 'object' || Array.isArray(filesRaw)) return null
  const files: Record<string, MirrorFileMeta> = {}
  for (const [rel, v] of Object.entries(filesRaw as Record<string, unknown>)) {
    if (!isValidRel(rel)) return null
    if (!isFileMeta(v)) return null
    files[rel] = v
  }
  const last = typeof m.lastSourceActivity === 'number' ? m.lastSourceActivity : 0
  return { version: 1, course: m.course, files, lastSourceActivity: last }
}

/** 相对路径只允许白名单文件（`<文件>` 或 `traj/<文件>`）。 */
export function isValidRel(rel: string): boolean {
  return (
    (MIRROR_FILES as readonly string[]).includes(rel) ||
    (rel.startsWith('traj/') && (MIRROR_FILES as readonly string[]).includes(rel.slice(5)))
  )
}

export function parseArchiveIndex(raw: unknown): ArchiveIndex | null {
  if (raw == null || typeof raw !== 'object') return null
  const v = raw as Record<string, unknown>
  if (v.version !== 1 || typeof v.course !== 'string' || !isCourseName(v.course)) return null
  if (
    typeof v.size !== 'number' ||
    !Number.isFinite(v.size) ||
    typeof v.mtimeMs !== 'number' ||
    typeof v.packedAt !== 'number' ||
    !Array.isArray(v.members)
  ) {
    return null
  }
  const members = v.members.filter((x): x is string => typeof x === 'string')
  if (members.length !== v.members.length) return null
  return {
    version: 1,
    course: v.course,
    size: v.size,
    mtimeMs: v.mtimeMs,
    members,
    packedAt: v.packedAt,
  }
}

/** 冷存包里的成员名（`<课>/<rel>`）。 */
export function memberName(course: string, rel: string): string {
  return `${course}/${rel}`
}
