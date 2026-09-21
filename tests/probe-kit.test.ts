import { describe, it, expect } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { InputRecorder } from '../src/replay/InputRecorder'
import { ProbeController } from '../src/game/ProbeController'
import { buildSessionPack } from '../src/probe/session'
import { writeStoreZip } from '../src/probe/zip'
import {
  REPO_ROOT,
  emptyReport,
  gameLinks,
  inspectPack,
  parseArgs,
  renderSheet,
  resolvePackPath,
  toolPath,
} from '../tools/probe/kit'
import type { ProbeVerdict } from '../src/probe/verdict'
import type { InputLike } from '../src/game/Input'
import type { Direction } from '../src/constants'
// The course corpus is GENERATED (a probe course is a session, not a repo
// artifact) — see tests/probe-fixture.ts.
import { PROBE_MANIFEST as MANIFEST, PROBE_MANIFEST_TEXT as MANIFEST_TEXT } from './probe-fixture'

// ============================================================
// Human-opening probe — operator kit (plan v7 §T7)
//
// The kit is the agent's half of T7: an empty manifest-derived verdict table
// + the seven launch links before the run, and (after the operator downloads a
// pack) the shared annotation, the filled table, and the review that catches
// transcription slips before they reach the training side.
// ============================================================

const IDLE: InputLike = {
  getMoveDirection: () => null as Direction | null,
  isFiring: () => false,
  wasItemPressed: () => false,
  endFrame: () => {},
  reset: () => {},
}

/** Record a probe run headlessly and return the serialized .replay text. */
function recordRun(game: number, ticks: number): string {
  const world = new World()
  const recorder = new InputRecorder()
  const controller = new ProbeController({ world, recorder })
  controller.bootFromText(`?probe=x20-opening&game=${game}`, MANIFEST_TEXT)
  const sim = new Simulation(world, IDLE)
  for (let i = 0; i < ticks; i++) {
    sim.tick()
    recorder.recordFrame(IDLE, null)
    controller.noteTick()
  }
  controller.finishRun('gameover')
  return controller.replayTextOf(game) ?? ''
}

function verdictFor(game: number, band: ProbeVerdict['best']['band'], reason = ''): ProbeVerdict {
  const entry = MANIFEST.games[game]
  return {
    game,
    stage: entry.stage,
    seed: entry.seed,
    attempts: 1,
    best: { band, reason },
    kills: 0,
    deathTick: 40,
  }
}

/** A well-formed pack for the given games, with verdicts as supplied. */
function packFor(games: number[], verdicts: ProbeVerdict[] = [], courseSha?: string): Uint8Array {
  return buildSessionPack({
    meta: {
      course: MANIFEST.course,
      courseSha: courseSha ?? MANIFEST.courseSha,
      startedAt: '2026-09-21T10:00:00.000Z',
      games: games.map((g) => ({
        game: g,
        stage: MANIFEST.games[g].stage,
        seed: MANIFEST.games[g].seed,
      })),
    },
    replays: games.map((g) => ({ file: `${g}.replay`, text: recordRun(g, 40) })),
    verdicts,
  }).bytes
}

describe('probe kit — prep mode (§T7 agent deliverable)', () => {
  const sheet = renderSheet(emptyReport(MANIFEST), MANIFEST, 'http://localhost:8956')

  it('lists the seven launch links the browser resolver accepts', () => {
    const links = gameLinks(MANIFEST, 'http://localhost:8956')
    expect(links).toHaveLength(7)
    expect(links[0]).toBe('http://localhost:8956/?probe=x20-opening&game=0')
    expect(links[6]).toBe('http://localhost:8956/?probe=x20-opening&game=6')
    for (const link of links) expect(sheet).toContain(link)
  })

  it('derives one table row per manifest game, all empty', () => {
    expect(emptyReport(MANIFEST).rows).toHaveLength(7)
    for (const g of MANIFEST.games) {
      expect(sheet).toContain(`| ${g.game} | ${g.stage}/${g.seed} | ${g.tag} |`)
    }
    // The band column is empty (an em dash), not accidentally pre-filled.
    expect(sheet).toContain('| 0 | 2000/414009 | calibration | — | — | — | — | — | — | — |')
  })

  it('carries the protocol rules and the band legend from the one band table', () => {
    // The three-way rule and the negative arm's refutability are stated up front.
    expect(sheet).toContain('⇒ **可动**')
    expect(sheet).toContain('⇒ **不可解**')
    expect(sheet).toContain('判读不是证明')
    expect(sheet).toContain('校准局 game 0 不通过 ⇒ 本次解读整体作废')
    // The legend comes from BAND_TABLE, including the negative arm's target.
    expect(sheet).toContain('| 无解 (`unsolvable`) | ≤3 |')
    expect(sheet).toContain('再试一次 (`retry`)')
    expect(sheet).toContain('不聚合')
  })

  it('tells the operator what to hand back, and claims nothing about results', () => {
    expect(sheet).toContain('bun tools/probe/kit.ts <下载的 pack.zip>')
    // No results section before anything has been run (the protocol sentence up
    // top mentions 「开局可动」 as vocabulary — the claim is the aggregation line).
    expect(sheet).not.toContain('## 结论')
    expect(sheet).not.toContain('- seed 聚合')
  })
})

describe('probe kit — pack mode (§T7 wrap-up)', () => {
  it('fills each row from the pack: annotation outcome + the verdict line', () => {
    const report = inspectPack(packFor([0, 3], [verdictFor(0, 'solvable')]), MANIFEST)
    expect(report.courseShaOk).toBe(true)
    expect(report.captured).toEqual([0, 3])
    expect(report.judged).toEqual([0])

    const row0 = report.rows[0]
    expect(row0.outcome).toBe('lose') // a 40-tick idle recording does not clear
    expect(row0.endReason).toBe('replay_end')
    expect(row0.band).toBe('solvable')
    expect(row0.contribution).toBe('solvable')

    const row3 = report.rows[3]
    expect(row3.band).toBeNull()
    expect(row3.contribution).toBeNull()
  })

  it('names captured-but-unjudged and judged-but-uncaptured games', () => {
    const report = inspectPack(packFor([1], [verdictFor(2, 'tough')]), MANIFEST)
    const messages = report.findings.map((f) => f.message)
    expect(messages.some((m) => m.includes('game 1') && m.includes('未判读'))).toBe(true)
    expect(messages.some((m) => m.includes('game 2') && m.includes('没有录像'))).toBe(true)
  })

  it('flags a band that contradicts the recording it is attached to', () => {
    // The replay is a defeat; a `solvable` band says it cleared.
    const report = inspectPack(packFor([0], [verdictFor(0, 'solvable')]), MANIFEST)
    expect(
      report.findings.some((f) => f.level === 'warn' && f.message.includes('与录像结局')),
    ).toBe(true)
  })

  it('refuses a courseSha from a different build (what the browser cannot check)', () => {
    const report = inspectPack(packFor([0], [], 'f'.repeat(64)), MANIFEST)
    expect(report.courseShaOk).toBe(false)
    expect(
      report.findings.some((f) => f.level === 'error' && f.message.includes('courseSha')),
    ).toBe(true)
  })

  it('rejects a pack whose session table disagrees with the manifest', () => {
    const bytes = buildSessionPack({
      meta: {
        course: MANIFEST.course,
        courseSha: MANIFEST.courseSha,
        startedAt: '2026-09-21T10:00:00.000Z',
        games: [{ game: 0, stage: 9999, seed: 1 }],
      },
      replays: [],
      verdicts: [],
    }).bytes
    const report = inspectPack(bytes, MANIFEST)
    expect(report.findings.some((f) => f.level === 'error' && f.message.includes('与清单'))).toBe(
      true,
    )
  })

  it('rejects a non-probe zip loudly', () => {
    const bytes = writeStoreZip([
      { name: '0.replay', data: new TextEncoder().encode(recordRun(0, 20)) },
    ])
    expect(() => inspectPack(bytes, MANIFEST)).toThrow(/session\.json/)
  })

  it("enforces the protocol's one hard rule on a hand-edited pack", () => {
    // `buildSessionPack` refuses to WRITE this, so it can only arrive by hand —
    // which is exactly why the reader must check it too.
    const bad = JSON.stringify({
      game: 0,
      stage: 2000,
      seed: 414009,
      attempts: 1,
      best: { band: 'unsolvable', reason: '   ' },
      kills: 0,
      deathTick: 40,
    })
    const bytes = writeStoreZip([
      {
        name: 'session.json',
        data: new TextEncoder().encode(
          JSON.stringify({
            course: MANIFEST.course,
            courseSha: MANIFEST.courseSha,
            startedAt: '2026-09-21T10:00:00.000Z',
            games: [{ game: 0, stage: 2000, seed: 414009 }],
          }),
        ),
      },
      { name: '0.replay', data: new TextEncoder().encode(recordRun(0, 30)) },
      { name: 'verdicts.jsonl', data: new TextEncoder().encode(`${bad}\n`) },
    ])
    const report = inspectPack(bytes, MANIFEST)
    expect(
      report.findings.some((f) => f.level === 'error' && f.message.includes('必须填写理由')),
    ).toBe(true)
    expect(report.judged).toEqual([]) // the invalid line is not counted as a judgment
  })
})

describe('probe kit — §0 aggregation and the calibration gate', () => {
  it('one solvable seed is enough, and it outranks a no-path read', () => {
    const report = inspectPack(
      packFor([2, 5], [verdictFor(2, 'unsolvable', 'walled in'), verdictFor(5, 'slight')]),
      MANIFEST,
    )
    expect(report.seedVerdict).toBe('solvable')
    expect(renderSheet(report, MANIFEST, 'http://localhost:8956')).toContain('开局可动')
  })

  it("the operator's 无解 read lands as 不可解 (the negative arm), stated as refutable", () => {
    const report = inspectPack(packFor([0, 1], [verdictFor(0, 'unsolvable', 'no path')]), MANIFEST)
    expect(report.seedVerdict).toBe('unsolvable')
    const sheet = renderSheet(report, MANIFEST, 'http://localhost:8956')
    expect(sheet).toContain('人类判不可解')
    expect(sheet).toContain('可被任一后续通关推翻')
  })

  it('a plain failure to break through is still not a claim about existence', () => {
    const report = inspectPack(packFor([0, 1], [verdictFor(0, 'tough')]), MANIFEST)
    expect(report.seedVerdict).toBe('unknown')
    const sheet = renderSheet(report, MANIFEST, 'http://localhost:8956')
    expect(sheet).toContain('仍未知')
    expect(sheet).toContain('校准局：未通过')
  })

  it('a failed calibration game voids the whole reading, as an error', () => {
    const report = inspectPack(packFor([0], [verdictFor(0, 'tough')]), MANIFEST)
    expect(report.calibration).toBe('fail')
    expect(report.findings.some((f) => f.level === 'error' && f.message.includes('整体作废'))).toBe(
      true,
    )
  })

  it('a passing calibration game does not', () => {
    const report = inspectPack(packFor([0], [verdictFor(0, 'solvable')]), MANIFEST)
    expect(report.calibration).toBe('pass')
    expect(report.findings.some((f) => f.level === 'error' && f.message.includes('作废'))).toBe(
      false,
    )
  })

  it('leaves the calibration gate unknown while game 0 is unjudged', () => {
    const report = inspectPack(packFor([1], [verdictFor(1, 'tough')]), MANIFEST)
    expect(report.calibration).toBe('unknown')
  })

  it('a `retry` on the calibration game is not a failure — it is not a judgment', () => {
    // `retry` means "another attempt, not counted", so it aggregates to nothing
    // (contribution === null). Reading that as a failed calibration would void
    // the whole session — and raise an error — over an operator note to self.
    const report = inspectPack(packFor([0, 1], [verdictFor(0, 'retry')]), MANIFEST)
    expect(report.rows[0].band).toBe('retry')
    expect(report.rows[0].contribution).toBeNull()
    expect(report.calibration).toBe('unknown')
    expect(report.seedVerdict).toBe('unknown')
    const sheet = renderSheet(report, MANIFEST, 'http://localhost:8956')
    expect(sheet).toContain('校准局：未判读')
    expect(report.findings.some((f) => f.level === 'error' && f.message.includes('作废'))).toBe(
      false,
    )
  })
})

describe('probe kit — CLI plumbing', () => {
  it('parses flags and takes the first bare token as the pack', () => {
    expect(parseArgs([])).toEqual({
      pack: null,
      manifestPath: 'public/probe/x20-opening.json',
      base: 'http://localhost:8956',
      outDir: 'tmp/probe-kit',
    })
    expect(parseArgs(['pack.zip', '--base', 'http://x:1', '--out-dir', 'o'])).toEqual({
      pack: 'pack.zip',
      manifestPath: 'public/probe/x20-opening.json',
      base: 'http://x:1',
      outDir: 'o',
    })
  })

  it('resolves a bare file name against cwd, then the repo, then the download folder', () => {
    const dir = mkdtempSync(join(tmpdir(), 'kit-'))
    const home = join(dir, 'home')
    const repoCwd = join(dir, 'repo')
    const repoRoot = join(dir, 'root')
    mkdirSync(repoCwd, { recursive: true })
    mkdirSync(repoRoot, { recursive: true })
    mkdirSync(join(home, 'Downloads'), { recursive: true })
    try {
      // 1) cwd wins
      writeFileSync(join(repoCwd, 'pack.zip'), 'x')
      expect(resolvePackPath('pack.zip', repoCwd, home, repoRoot)).toBe(join(repoCwd, 'pack.zip'))
      // 2) a repo-root file is found from anywhere (the operator may run the
      //    tool by absolute path from the download folder)
      writeFileSync(join(repoRoot, 'third.zip'), 'x')
      expect(resolvePackPath('third.zip', repoCwd, home, repoRoot)).toBe(
        join(repoRoot, 'third.zip'),
      )
      // 3) the download folder is the last stop
      writeFileSync(join(home, 'Downloads', 'other.zip'), 'x')
      expect(resolvePackPath('other.zip', repoCwd, home, repoRoot)).toBe(
        join(home, 'Downloads', 'other.zip'),
      )
      // 4) missing everywhere
      expect(resolvePackPath('nope.zip', repoCwd, home, repoRoot)).toBeNull()
      // 5) an explicit path is taken as given, never searched
      expect(resolvePackPath('./nope.zip', repoCwd, home, repoRoot)).toBeNull()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('resolves a repo-relative path repo-relative, whatever the cwd is', () => {
    // The operator realistically runs this from ~/Downloads with an absolute
    // script path — a cwd-relative default manifest silently broke that flow.
    // The probe manifest itself can no longer be the probe: it is GENERATED
    // into a gitignored dir, so in a fresh clone it does not exist and this
    // assertion would pass only by accident of the local tree.
    const elsewhere = mkdtempSync(join(tmpdir(), 'kit-cwd-'))
    try {
      expect(toolPath('tools/probe/flatten-manifest.ts', elsewhere)).toBe(
        join(REPO_ROOT, 'tools/probe/flatten-manifest.ts'),
      )
      // a path that exists nowhere falls back to cwd resolution
      expect(toolPath('nope.json', elsewhere)).toBe(join(elsewhere, 'nope.json'))
      // an absolute path is taken as given
      expect(toolPath('/abs/x.json', elsewhere)).toBe('/abs/x.json')
    } finally {
      rmSync(elsewhere, { recursive: true, force: true })
    }
  })
})
