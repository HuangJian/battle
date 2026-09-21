import { describe, it, expect } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  PROBE_DIR,
  PROBE_INDEX_PATH,
  ProbeCourseIndexError,
  courseNameRule,
  parseProbeCourseIndex,
  parseProbeCourseIndexText,
  probeManifestPath,
} from '../src/probe/index'
import { courseIndexJson, courseNamesIn, stableStringify } from '../tools/probe/flatten-manifest'
import { PROBE_COURSE, PROBE_MANIFEST } from './probe-fixture'

// ============================================================
// Human-opening probe — course index (`public/probe/index.json`)
//
// The course name is interpolated into the manifest URL, so it is a security
// boundary as well as a lookup: every illegal spelling below would otherwise
// make `?probe=<name>` fetch a path of the visitor's choosing.
//
// The index itself is GENERATED (a scanned list of the manifests sitting in
// the served, gitignored `public/probe/`), so the scan is tested against a
// throwaway directory rather than a committed copy.
// ============================================================

describe('course name → manifest path', () => {
  it('maps a course to its own manifest file', () => {
    expect(probeManifestPath('x20-opening')).toBe('/probe/x20-opening.json')
    expect(probeManifestPath('y30')).toBe('/probe/y30.json')
    expect(probeManifestPath('c20-opening.v2')).toBe('/probe/c20-opening.v2.json')
  })

  it('refuses anything that is not a lowercase slug', () => {
    const bad = [
      '',
      '..',
      '.',
      '.hidden',
      '../evil',
      'a/b',
      'a\\b',
      'X20',
      'x 20',
      '-x',
      '_x',
      'x:',
      'x20-opening/..',
      '%2e%2e',
    ]
    for (const name of bad) {
      expect(probeManifestPath(name)).toBeNull()
    }
  })

  it('refuses the reserved index name — it would fetch the index itself', () => {
    expect(probeManifestPath('index')).toBeNull()
    expect(courseNameRule()).toContain('index')
  })
})

describe('course index parsing (no silent defaults)', () => {
  it('accepts an index naming the fixture course', () => {
    expect(parseProbeCourseIndexText(JSON.stringify({ courses: [PROBE_COURSE] })).courses).toEqual([
      PROBE_COURSE,
    ])
  })

  it('fails loud on a shape that would hide a course', () => {
    expect(() => parseProbeCourseIndex([])).toThrow(ProbeCourseIndexError)
    expect(() => parseProbeCourseIndex({})).toThrow(/courses/)
    expect(() => parseProbeCourseIndex({ courses: [] })).toThrow(/非空/)
    expect(() => parseProbeCourseIndex({ courses: [7] })).toThrow(/字符串/)
    expect(() => parseProbeCourseIndex({ courses: ['index'] })).toThrow(/合法课程名/)
    expect(() => parseProbeCourseIndex({ courses: ['../evil'] })).toThrow(/合法课程名/)
    expect(() => parseProbeCourseIndex({ courses: ['a', 'a'] })).toThrow(/重复/)
    expect(() => parseProbeCourseIndexText('{ nope')).toThrow(ProbeCourseIndexError)
  })
})

describe('course scan (what the index is derived from)', () => {
  it('keeps course files, drops the index and anything unreachable', () => {
    const names = courseNamesIn('unused', () => [
      'index.json',
      'b-course.json',
      'a-course.json',
      'Bad-Name.json',
      'notes.md',
      'no-extension',
    ])
    expect(names).toEqual(['a-course', 'b-course'])
  })

  it('builds the canonical index text from a served directory', () => {
    const dir = mkdtempSync(join(tmpdir(), 'probe-index-'))
    try {
      writeFileSync(join(dir, 'index.json'), '{"courses":[]}')
      writeFileSync(join(dir, 'b-course.json'), '{}')
      writeFileSync(join(dir, 'a-course.json'), '{}')
      writeFileSync(join(dir, 'Bad-Name.json'), '{}')
      expect(courseIndexJson(dir)).toBe(
        stableStringify({ courses: ['a-course', 'b-course'] }) + '\n',
      )
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('a course and its manifest agree on the name (the URL-segment check)', () => {
    // The name is the URL segment AND the manifest's own claim — a mismatch is
    // exactly the `course-mismatch` refusal the boot path raises.
    expect(probeManifestPath(PROBE_COURSE)).toBe(`/probe/${PROBE_COURSE}.json`)
    expect(PROBE_MANIFEST.course).toBe(PROBE_COURSE)
  })

  it('the index is served from the same directory as the courses', () => {
    expect(PROBE_INDEX_PATH).toBe('/probe/index.json')
    expect(PROBE_DIR).toBe('public/probe')
  })
})
