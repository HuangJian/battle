import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { InputRecorder } from '../src/replay/InputRecorder'
import { ProbeController } from '../src/game/ProbeController'
import { parseProbeManifestText } from '../src/probe/manifest'
import { parseReplayFile } from '../src/replay/file'
import { verifyReplayText } from '../tools/replay/verify-replay'
import { annotatePack } from '../tools/probe/annotate'
import { buildSessionPack } from '../src/probe/session'
import { readStoreZip, writeStoreZip } from '../src/probe/zip'
import type { InputLike } from '../src/game/Input'
import type { Direction } from '../src/constants'

// ============================================================
// Human-opening probe — custom-stage recordings (plan v7 §T5b / §T6)
//
// `tools/replay/verify-replay.ts` rebuilds the world with
// `STAGES[meta.stage] ?? STAGES[0]` — for a 2000+ probe stage that silently
// falls back to classic stage 0. These tests are the regression the plan asks
// for: a probe recording must still reproduce its tick-hash chain, and the
// offline annotator must read the pack back.
// ============================================================

const MANIFEST_TEXT = readFileSync(
  join(import.meta.dir, '..', 'public/probe/x20-opening.json'),
  'utf8',
)
const MANIFEST = parseProbeManifestText(MANIFEST_TEXT)

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

/** Parse a recorded replay (throws on a malformed envelope). */
function parseRecorded(text: string) {
  const parsed = parseReplayFile(text)
  if ('error' in parsed) throw new Error(parsed.error)
  return parsed.replay
}

describe('probe recording — custom stage (T5b)', () => {
  it('stamps metadata.stage with the manifest stage id, not the index', () => {
    const text = recordRun(0, 20)
    expect(parseRecorded(text).metadata.stage).toBe(2000)
    expect(parseRecorded(text).metadata.stageName).toBe('ladder-c20-lives1-abcd')
  })

  it('reproduces its tick-hash chain through verify-replay.ts', () => {
    const text = recordRun(2, 250)
    const result = verifyReplayText(text, '2.replay')
    expect(result.hashVerified).toBe(true)
    expect(result.verdict).toBe('OK')
    expect(result.firstHashMismatch).toBeNull()
  })

  it('the chain is non-trivial (at least one checkpoint was compared)', () => {
    const text = recordRun(1, 250)
    expect(parseRecorded(text).tickHashes?.length ?? 0).toBeGreaterThan(0)
  })

  it('no declared outcome ⇒ no invented terminal expectation (verify-replay fix)', () => {
    // A browser envelope carries no status, and parseReplayFile defaults the
    // parsed type to 'clear'. The verifier must NOT read that default as "this
    // run cleared" — otherwise every defeat recording reports DESYNC.
    const text = recordRun(3, 250)
    const result = verifyReplayText(text, '3.replay')
    expect(result.expectedType).toBeUndefined()
    expect(result.verdict).toBe('OK')
  })

  it('a declared clear expectation is still enforced', () => {
    // The filename convention wins when present: this run did not clear, so a
    // `-clear-` name must stay DESYNC.
    const text = recordRun(3, 250)
    const result = verifyReplayText(text, 'hard-s01-clear-000123-414021.replay')
    expect(result.expectedType).toBe('clear')
    expect(result.verdict).toBe('DESYNC')
    expect(result.reason).toContain('expected stage clear')
  })
})

describe('probe annotation (T6)', () => {
  const replay = recordRun(3, 1200)

  function packFor(text: string): Uint8Array {
    return buildSessionPack({
      meta: {
        course: MANIFEST.course,
        courseSha: MANIFEST.courseSha,
        startedAt: '2026-09-21T10:00:00.000Z',
        games: [{ game: 3, stage: 2002, seed: 414021 }],
      },
      replays: [{ file: '3.replay', text }],
      verdicts: [],
    }).bytes
  }

  it('annotates a pack: one header line per game, typed event lines', () => {
    const { lines, games } = annotatePack(packFor(replay), MANIFEST)
    expect(games).toBe(1)
    const header = lines[0] as Record<string, unknown>
    expect(header.type).toBe('header')
    expect(header.game).toBe(3)
    expect(header.stage).toBe(2002)
    expect(header.seed).toBe(414021)
    expect(header.lives).toBe(1)
    expect(typeof header.ticks).toBe('number')
    expect(['clear', 'lose']).toContain(header.outcome as string)
    expect(typeof header.endReason).toBe('string')

    // Every non-header, non-sample line carries a tick and a known type.
    for (const line of lines.slice(1)) {
      const l = line as Record<string, unknown>
      expect(typeof l.tick).toBe('number')
      if (l.type === 'sample') continue
      expect(['kill', 'hit', 'pickup', 'death', 'baseHit']).toContain(l.type as string)
    }
  })

  it('samples only the opening 300t + the 600t tail', () => {
    const { lines } = annotatePack(packFor(replay), MANIFEST)
    const samples = lines.filter((l) => (l as Record<string, unknown>).type === 'sample')
    expect(samples.length).toBeGreaterThan(0)
    const endTick = (lines[0] as Record<string, unknown>).endTick as number
    for (const s of samples) {
      const tick = (s as Record<string, unknown>).tick as number
      expect(tick % 10).toBe(0)
      expect(tick <= 300 || tick >= endTick - 600).toBe(true)
    }
    const opening = samples.filter((s) => ((s as Record<string, unknown>).tick as number) <= 300)
    const tail = samples.filter((s) => ((s as Record<string, unknown>).tick as number) >= 600)
    expect(opening.length).toBeGreaterThan(0)
    expect(tail.length).toBeGreaterThan(0)
  })

  it('a sample carries the world shape the plan specifies', () => {
    const { lines } = annotatePack(packFor(replay), MANIFEST)
    const sample = lines.find((l) => (l as Record<string, unknown>).type === 'sample') as Record<
      string,
      unknown
    >
    expect(sample).toHaveProperty('enemiesByKind')
    expect(sample).toHaveProperty('bullets')
    expect(sample).toHaveProperty('baseHp')
    expect(sample).toHaveProperty('player')
  })

  it('rejects a pack with no session.json', () => {
    const bytes = writeStoreZip([{ name: '0.replay', data: new TextEncoder().encode(replay) }])
    expect(() => annotatePack(bytes, MANIFEST)).toThrow(/session\.json/)
  })

  it('the pack round-trips through the zip reader', () => {
    const names = readStoreZip(packFor(replay)).map((e) => e.name)
    expect(names).toEqual(['session.json', '3.replay', 'verdicts.jsonl'])
  })
})
