/** evalboard-store.test.ts ↔ tools/training/evalboard/store.ts（§10.1 单测清单）。 */
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import {
  appendRow,
  assertComparable,
  coverageReport,
  dedupKeyOf,
  IncomparableError,
  loadDedupKeys,
  monthFile,
  REQUIRED_FIELDS,
  segmentOf,
  segmentOfSeed,
  seed0OfSegment,
  type EvalGameRow,
} from '../tools/training/evalboard/store'

function row(over: Partial<EvalGameRow> = {}): EvalGameRow {
  return {
    schema: 1,
    ts: '2026-09-10T00:00:00.000Z',
    source: 'B',
    batch_id: 'b1',
    batch_unit: { idx: 0, of: 2 },
    run_id: 'run1',
    course: 'c4-margin',
    iter: 30,
    ckpt_path: 'tmp/c/weights.json',
    ckpt_sha16: 'abcd1234abcd1234',
    init_sha16: 'init0000init0000',
    wver: 'wver1',
    policy: 'nn',
    rung: 'c4l1',
    probe_key: 'c4l1-hard-t2400-abc123-probe0',
    seedSpace: 'probe0',
    stage_id: 'arena-c4',
    stage_name: 'open13-c4',
    seed: 860001,
    seed_segment: 0,
    engine: { git_commit: 'c0ffee', dist_codehash: 'd0', engine_epoch: 'e0' },
    outcome: 'stage_clear',
    win: true,
    cleared: true,
    ticks: 1200,
    kills: 4,
    enemyTotal: 4,
    playerHits: 2,
    playerDamageTaken: 0,
    playerShots: 100,
    enemyHits: 20,
    powerUpsCollected: 1,
    stuckTicks: 0,
    firstKillTick: 300,
    playerDeaths: 0,
    cellsVisited: 40,
    playerLevel: 1,
    puSpawnBomb: 0,
    puSpawnTank: 0,
    puSpawnFreeze: 0,
    puSpawnShield: 0,
    puSpawnStar: 1,
    puGotBomb: 0,
    puGotTank: 0,
    puGotFreeze: 0,
    puGotShield: 0,
    score: 1.5,
    metrics_version: 1,
    greedy: true,
    node_id: 'self',
    elapsedSec: 3.2,
    phase: 'rollout',
    milestone: false,
    ...over,
  }
}

describe('segments (§2.2/§2.3)', () => {
  it('seg = k % 16，seed0 = 860001 + 100×seg', () => {
    expect(segmentOf(0)).toBe(0)
    expect(segmentOf(16)).toBe(0)
    expect(segmentOf(17)).toBe(1)
    expect(seed0OfSegment(0)).toBe(860001)
    expect(seed0OfSegment(15)).toBe(861501)
    expect(segmentOfSeed(860001)).toBe(0)
    expect(segmentOfSeed(860100)).toBe(0)
    expect(segmentOfSeed(860101)).toBe(1)
    expect(segmentOfSeed(42)).toBe(-1)
  })
})

describe('appendRow 幂等 (§3.4)', () => {
  it('同键重入不重复', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evalstore-'))
    try {
      const known = loadDedupKeys(dir)
      expect(appendRow(dir, row(), known)).toBe('appended')
      expect(appendRow(dir, row(), known)).toBe('duplicate')
      expect(appendRow(dir, row({ seed: 860002, seed_segment: 0 }), known)).toBe('appended')
      expect(known.size).toBe(2)
      expect(monthFile('2026-09-10T00:00:00.000Z')).toBe('2026-09.jsonl')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('去重键 = run_id/iter/wver/stage/seed', () => {
    const a = row()
    const b = row({ node_id: 'mac', elapsedSec: 9.9, ts: '2026-09-11T00:00:00.000Z' })
    expect(dedupKeyOf(a)).toBe(dedupKeyOf(b))
  })
})

describe('assertComparable (§3.5)', () => {
  const base = row()
  it('拒绝跨 probe_key / engine_epoch / seedSpace', () => {
    expect(() => assertComparable(base, row({ probe_key: 'other' }))).toThrow(IncomparableError)
    expect(() =>
      assertComparable(base, row({ engine: { ...base.engine, engine_epoch: 'e1' } })),
    ).toThrow(IncomparableError)
    expect(() => assertComparable(base, row({ seedSpace: 'eval860k' }))).toThrow(IncomparableError)
  })
  it('配对比较要求同 seed 集', () => {
    expect(() => assertComparable(base, base, { pairedSeeds: { a: [1, 2], b: [1, 3] } })).toThrow(
      IncomparableError,
    )
    assertComparable(base, base, { pairedSeeds: { a: [1, 2], b: [2, 1] } })
  })
  it('同窗内 seed_segment 互不相交', () => {
    expect(() => assertComparable(base, base, { segments: [0, 1, 1, 2] })).toThrow(
      IncomparableError,
    )
    assertComparable(base, base, { segments: [0, 1, 2, 3] })
  })
})

describe('coverageReport (§3.3)', () => {
  it('输出缺字段；完整行零缺失', () => {
    expect(coverageReport([row() as unknown as Record<string, unknown>])).toEqual([])
    const rep = coverageReport([{ win: true }])
    expect(rep.length).toBe(1)
    expect(rep[0].missing).toContain('probe_key')
    expect(rep[0].missing).toContain('engine')
    expect(REQUIRED_FIELDS).toContain('stuckTicks')
    expect(REQUIRED_FIELDS).toContain('puSpawnBomb')
  })
  it('明示豁免清单不计入缺失', () => {
    const r = row() as unknown as Record<string, unknown>
    delete r.stuckTicks
    expect(coverageReport([r], ['stuckTicks'])).toEqual([])
  })
})
