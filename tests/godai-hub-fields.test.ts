import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Structural guardrail for the GodAIInput "self-hub" field lifecycle
 * (遗留 #1 disposition — DECISIONS §267).
 *
 * The hub holds ~170 mutable instance fields. Stage-boundary hygiene used to
 * be enforced only by convention: §3.3 centralized CACHE invalidation into
 * invalidatePerTickCaches()/invalidateStageCaches() and behavior-state
 * clearing into reset(), but nothing prevented a NEW field from silently
 * skipping all of them — a type-invisible stale-state bug across stage
 * restarts. This test makes that contract mechanical:
 *
 *   every declared instance field must either be
 *     (a) assigned/mutated inside constructor, invalidatePerTickCaches(),
 *         invalidateStageCaches() or reset(), or
 *     (b) listed in ALLOWLIST below with a class tag and reason.
 *
 * Adding a field without touching a lifecycle method or the allowlist fails
 * here, at PR time — not as a heisenbug in a replay six weeks later.
 *
 * Parsing is textual (oxfmt-stable formatting); the sanity floors below make
 * parser rot loud instead of vacuous.
 */

const SRC_PATH = join(import.meta.dir, '../src/ai/GodAIInput.ts')

interface Member {
  name: string
  kind: 'field' | 'method'
  text: string
}

function extractClassBody(src: string): string {
  const m = /^export class GodAIInput.*$/m.exec(src)
  if (!m) throw new Error('GodAIInput class declaration not found')
  let i = m.index
  while (src[i] !== '{') i++
  let depth = 0
  const start = i
  while (i < src.length) {
    if (src[i] === '{') depth++
    else if (src[i] === '}') {
      depth--
      if (depth === 0) return src.slice(start, i + 1)
    }
    i++
  }
  throw new Error('unbalanced braces in GodAIInput')
}

const MEMBER_RE =
  /^  (?:private |readonly |public )?(?:get |set )?([A-Za-z_$][\w$]*)\s*(\()?(\?)?\s*[:=]?/
const COMMENT_RE = /^\s*(\/\/|\*|\/\*)/

function stripComments(s: string): string {
  return s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')
}

function parseMembers(body: string): Member[] {
  const members: Member[] = []
  let cur: Member | null = null
  for (const ln of body.split('\n')) {
    if (!COMMENT_RE.test(ln)) {
      const mm = MEMBER_RE.exec(ln)
      if (mm && ln.startsWith('  ') && ln[2] !== ' ') {
        // Function-typed field ("controlledTank: (w) => …") has a paren right
        // after the name but is still a field — arrow marker disambiguates.
        const kind = mm[2] && !ln.includes('=>') ? 'method' : 'field'
        cur = { name: mm[1], kind, text: ln }
        members.push(cur)
        continue
      }
    }
    if (cur) cur.text += '\n' + ln
  }
  return members
}

/** Fields written (assigned or mutator-called) inside a method's body. */
function mutationsIn(members: Member[], methodName: string): Set<string> {
  const out = new Set<string>()
  for (const m of members) {
    if (m.name !== methodName || m.kind !== 'method') continue
    const t = stripComments(m.text)
    for (const r of [
      /this\.([A-Za-z_$][\w$]*)\s*=(?!=)/g,
      /this\.([A-Za-z_$][\w$]*)\.(?:fill|clear|set|copyWithin|push|pop|shift|unshift|sort)\(/g,
      /this\.([A-Za-z_$][\w$]*)\[/g,
    ]) {
      let mm: RegExpExecArray | null
      while ((mm = r.exec(t)) !== null) out.add(mm[1])
    }
  }
  return out
}

/** Lifecycle coverage = stage-boundary contract. endFrame deliberately NOT
 * included: per-tick outputs must ALSO survive the reset path. */
const LIFECYCLE = ['constructor', 'invalidatePerTickCaches', 'invalidateStageCaches', 'reset']

/** Fields exempt from stage-boundary hygiene, keyed by exemption class:
 *  A diag-counter   — pure observation, never feeds back into gameplay
 *                     (each entry's doc comment says so explicitly).
 *  B reuse-buffer   — §14.2 out-buffer, fully overwritten before read;
 *                     correctness lives at the write site, not the lifecycle.
 *  C keyed-companion— inert while its *Valid/*Computed flag is false; the
 *                     GUARDING FLAG is itself lifecycle-covered, so clearing
 *                     these payloads again would be redundant writes.
 *  D wiring/config  — write-once injection or constants (external writers
 *                     cited per entry).
 *  E monotonic      — scheduler phase counter; persistence across stages is
 *                     behaviorally neutral by design (any phase is valid). */
const ALLOWLIST: Record<string, { cls: string; why: string }> = {
  // ---- A: diag counters (doc comments: "Pure observation") ----
  _centroidChecks: { cls: 'A', why: '§223 diag, no gameplay effect' },
  _centroidTriggers: { cls: 'A', why: '§223 diag, no gameplay effect' },
  _centroidEscapes: { cls: 'A', why: '§223 diag, no gameplay effect' },
  _selfFireGuardBlocks: { cls: 'A', why: '§121 diag, read by ab-fire-guard' },
  _centerLineFireBlocks: { cls: 'A', why: '§193-A diag' },
  _predictiveFireBlocks: { cls: 'A', why: '§193-D diag' },
  branchCounts: { cls: 'A', why: 'branch telemetry, never feeds back' },
  _lastBranch: { cls: 'A', why: 'forensics label, read by simulation-runner' },
  _intentPrev: { cls: 'A', why: '§290 intent-tagger prev-intent (tagger.ts observation)' },
  _intentDuration: { cls: 'A', why: '§290 intent-tagger duration (tagger.ts observation)' },
  _intentUnknownLabels: {
    cls: 'A',
    why: '§290 intent-tagger unknown-branch skip counter (observer safety)',
  },
  _intentLog: {
    cls: 'A',
    why: '§290 intent-tagger sample ring (tagger.ts observation, reset()-cleared)',
  },
  _candidateOverride: {
    cls: 'A',
    why: '§294/M6 intent-executor candidate-subset override (default null = byte-identical)',
  },
  // ---- B: §14.2 reusable out-buffers ----
  _candVerdict: { cls: 'B', why: 'makeCandidateVerdict out-buffer' },
  _decisionCtx: { cls: 'B', why: 'DecisionContext scratch, rewritten per think' },
  _scanAligned: { cls: 'B', why: 'scan scratch array, rebuilt per use' },
  _scanResults: { cls: 'B', why: 'scanAheadImpl memo slots, overwritten pre-read' },
  _tankCellBuf: { cls: 'B', why: 'tankCellImpl shared Cell buffer' },
  _turnSnapScan: { cls: 'B', why: 'scanAheadImpl out-buffer (§3.2), all fields written pre-read' },
  // ---- C: keyed cache companions (guarding flag IS lifecycle-covered) ----
  _canMoveResult: { cls: 'C', why: 'bitmask payload of _canMoveComputed' },
  _playerCellCache: { cls: 'C', why: 'payload of _playerCellValid' },
  _selTargetBuf: { cls: 'C', why: '§125 payload of _selTargetValid' },
  _selTargetKeyCol: { cls: 'C', why: '§125 key of _selTargetValid' },
  _selTargetKeyRow: { cls: 'C', why: '§125 key of _selTargetValid' },
  _selTargetNull: { cls: 'C', why: '§125 negative-hit bit of _selTargetValid' },
  _carvePathCorridor: { cls: 'C', why: 'payload of _carvePathCacheValid' },
  _carvePathFromCol: { cls: 'C', why: 'key of _carvePathCacheValid' },
  _carvePathFromRow: { cls: 'C', why: 'key of _carvePathCacheValid' },
  _carvePathToCol: { cls: 'C', why: 'key of _carvePathCacheValid' },
  _carvePathToRow: { cls: 'C', why: 'key of _carvePathCacheValid' },
  _carvePathRev: { cls: 'C', why: 'terrain-rev key of _carvePathCacheValid' },
  _digPathCorridor: { cls: 'C', why: 'payload of _digPathCacheValid' },
  _digPathFromCol: { cls: 'C', why: 'key of _digPathCacheValid' },
  _digPathFromRow: { cls: 'C', why: 'key of _digPathCacheValid' },
  _digPathToCol: { cls: 'C', why: 'key of _digPathCacheValid' },
  _digPathToRow: { cls: 'C', why: 'key of _digPathCacheValid' },
  _digPathRev: { cls: 'C', why: 'terrain-rev key of _digPathCacheValid' },
  _navCache: { cls: 'C', why: 'payload of _navCacheValid' },
  _navPlayerCol: { cls: 'C', why: 'key of _navCacheValid' },
  _navPlayerRow: { cls: 'C', why: 'key of _navCacheValid' },
  _navTargetCol: { cls: 'C', why: 'key of _navCacheValid' },
  _navTargetRow: { cls: 'C', why: 'key of _navCacheValid' },
  _replanCache: { cls: 'C', why: 'payload of _replanCacheValid' },
  _replanPcCol: { cls: 'C', why: 'key of _replanCacheValid' },
  _replanPcRow: { cls: 'C', why: 'key of _replanCacheValid' },
  _replanTgtCol: { cls: 'C', why: 'key of _replanCacheValid' },
  _replanTgtRow: { cls: 'C', why: 'key of _replanCacheValid' },
  _pathCostEKey: { cls: 'C', why: '§171 ring memo key (monotonic genId ⇒ no cross-stage hit)' },
  _pathCostPKey: { cls: 'C', why: '§171 ring memo key' },
  _pathCostRev: { cls: 'C', why: '§171 ring memo terrain-rev key' },
  _pathCostVal: { cls: 'C', why: '§171 ring memo payload' },
  // ---- D: write-once wiring / config ----
  controlledTank: { cls: 'D', why: 'injected resolver, ctor option' },
  isGuardAI: { cls: 'D', why: 'external write: SimulationEnemies.ts (§187)' },
  _navReplanMax: { cls: 'D', why: 'constant bound (60)' },
  _replanMax: { cls: 'D', why: 'constant bound (60)' },
  _pickupReachMax: { cls: 'D', why: 'constant bound (60)' },
  // ---- E: monotonic scheduler ----
  _thinkCounter: { cls: 'E', why: '§233 thinkInterval phase; cross-stage continuity neutral' },
}

describe('godai-hub-fields (遗留 #1 structural guardrail)', () => {
  const src = readFileSync(SRC_PATH, 'utf8')
  const body = extractClassBody(src)
  const members = parseMembers(body)

  const fields = new Map<string, string>()
  for (const m of members) if (m.kind === 'field') fields.set(m.name, m.text)
  const methodNames = new Set(members.filter((m) => m.kind === 'method').map((m) => m.name))

  const covered = new Set<string>()
  for (const name of LIFECYCLE) for (const f of mutationsIn(members, name)) covered.add(f)
  const uncovered: string[] = []
  for (const f of fields.keys()) if (!covered.has(f)) uncovered.push(f)

  it('parser sanity: the hub really has ~170+ fields and the key methods', () => {
    expect(fields.size).toBeGreaterThanOrEqual(160)
    for (const required of ['constructor', 'endFrame', 'think', 'reset']) {
      expect(methodNames.has(required)).toBe(true)
    }
  })

  it('cache-invalidation registry exists and both groups are wired', () => {
    expect(methodNames.has('invalidatePerTickCaches')).toBe(true)
    expect(methodNames.has('invalidateStageCaches')).toBe(true)
    const find = (name: string) =>
      members.find((m) => m.name === name && m.kind === 'method')?.text ?? ''
    expect(find('endFrame')).toContain('this.invalidatePerTickCaches()')
    expect(find('reset')).toContain('this.invalidateStageCaches()')
  })

  it('every uncovered field carries a fresh, classified allowlist entry', () => {
    const missing = uncovered.filter((f) => !(f in ALLOWLIST))
    expect(
      missing.length === 0
        ? true
        : `fields not covered by any lifecycle method and absent from ALLOWLIST: ${missing.join(', ')}`,
    ).toBe(true)
  })

  it('allowlist has no stale entries (every entry matches a real field)', () => {
    const stale = Object.keys(ALLOWLIST).filter((f) => !fields.has(f))
    expect(
      stale.length === 0
        ? true
        : `stale allowlist keys (renamed/removed fields): ${stale.join(', ')}`,
    ).toBe(true)
  })
})
