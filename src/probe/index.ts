// ================================================================
// Human-opening probe — course index (`public/probe/index.json`)
//
// The probe serves ONE course per manifest file, so `?probe=<course>` has to
// decide WHICH file to fetch (`/probe/<course>.json`) — the course name is
// therefore interpolated into a URL path, and the name rules below are
// enforced rather than assumed. `public/probe/index.json` is the build
// artifact that lists the courses this build actually serves, scanned from
// `public/probe/*.json` by `tools/probe/flatten-manifest.ts`.
//
// Adding a course = its own `<course>.games.json` + regenerate (manifest AND
// index); nothing in the runtime is course-specific any more.
//
// Pure module: no DOM, no fs, no RNG.
// ================================================================

/** Served course index (build artifact, committed). */
export const PROBE_INDEX_PATH = '/probe/index.json'

/** Repo-relative directory the index lists — `public/` is served at the root. */
export const PROBE_DIR = 'public/probe'

/**
 * A legal course name: a lowercase slug starting with a letter or digit. The
 * pattern is anchored, so a `/`, `\`, leading `.` or whitespace cannot get
 * through — that is the guard against `?probe=../../secret` turning into a
 * fetch of an arbitrary path. `/^[a-z0-9][a-z0-9._-]*$/`
 */
export const PROBE_COURSE_RE = /^[a-z0-9][a-z0-9._-]*$/

/** `index` is the index file's own name — never a course. */
const RESERVED_COURSE = 'index'

export class ProbeCourseIndexError extends Error {
  constructor(message: string) {
    super(`probe course index: ${message}`)
    this.name = 'ProbeCourseIndexError'
  }
}

export interface ProbeCourseIndex {
  /** Course names, in the order the index declares them. */
  courses: string[]
}

/**
 * The manifest URL for a course name, or null when the name is not a legal
 * course name. Null (not a throw) because the caller is a boot path fed by a
 * hand-typed URL: it refuses loudly with the query in the message, which is
 * more useful than an exception carrying only the path.
 */
export function probeManifestPath(course: string): string | null {
  if (course === RESERVED_COURSE || !PROBE_COURSE_RE.test(course)) return null
  return `/probe/${course}.json`
}

/** Human-readable reason for a refused course name (used in refusals). */
export function courseNameRule(): string {
  return `course 名须匹配 ${String(PROBE_COURSE_RE)} 且不能是 "${RESERVED_COURSE}"`
}

function fail(msg: string): never {
  throw new ProbeCourseIndexError(msg)
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/**
 * Validate + parse the course index. Throws `ProbeCourseIndexError` on the
 * first violation — an index that silently drops an entry would hide a course
 * from the launcher, which is exactly the bug this schema guard exists to
 * catch (same discipline as `parseProbeManifest`).
 */
export function parseProbeCourseIndex(raw: unknown): ProbeCourseIndex {
  if (!isRecord(raw)) fail('根节点不是对象')
  const courses = raw.courses
  if (!Array.isArray(courses) || courses.length === 0) {
    fail('courses: 必须是非空数组（没有任何课程 = 索引没有意义）')
  }
  const seen = new Set<string>()
  for (const [i, c] of courses.entries()) {
    if (typeof c !== 'string' || c === '') fail(`courses[${i}]: 必须是非空字符串`)
    if (probeManifestPath(c) === null)
      fail(`courses[${i}]=${JSON.stringify(c)} 不是合法课程名：${courseNameRule()}`)
    if (seen.has(c)) fail(`courses[${i}]=${c} 重复`)
    seen.add(c)
  }
  return { courses: courses as string[] }
}

/** The index as a `JSON.parse` payload (throws `ProbeCourseIndexError`). */
export function parseProbeCourseIndexText(text: string): ProbeCourseIndex {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch (e) {
    fail(`不是合法 JSON: ${String(e)}`)
  }
  return parseProbeCourseIndex(raw)
}
