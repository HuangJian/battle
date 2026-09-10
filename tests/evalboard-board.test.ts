/** evalboard-board.test.ts ↔ tools/training/console/evalboard.ts（P3 看板端到端合成）。 */
import { describe, expect, it, beforeAll, afterAll } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { appendRow, type EvalGameRow } from '../tools/training/evalboard/store'
import { buildEvalBoardView, enqueueProbeRun } from '../tools/training/console/evalboard'

let root = ''
beforeAll(() => {
  root = mkdtempSync(path.join(tmpdir(), 'evalboard-e2e-'))
  process.env.EVALBOARD_DATA = root
})
afterAll(() => {
  rmSync(root, { recursive: true, force: true })
  delete process.env.EVALBOARD_DATA
})

function row(over: Partial<EvalGameRow> = {}): EvalGameRow {
  return {
    schema: 1,
    ts: '2026-09-10T00:00:00.000Z',
    source: 'B',
    batch_id: 'b-e2e',
    batch_unit: { idx: 0, of: 2 },
    run_id: 'run1',
    course: 'e2e',
    iter: 30,
    ckpt_path: 'w',
    ckpt_sha16: 'c'.repeat(16),
    init_sha16: 'i'.repeat(16),
    wver: 'v1',
    policy: 'nn',
    rung: 'c4l1',
    probe_key: 'c4l1-hard-t12000-h-eval860k',
    seedSpace: 'eval860k',
    stage_id: '2000',
    stage_name: 'open13-c4',
    seed: 860001,
    seed_segment: 0,
    engine: { git_commit: 'g', dist_codehash: 'd', engine_epoch: 'e0' },
    outcome: 'stage_clear',
    win: true,
    cleared: true,
    ticks: 1000,
    kills: 4,
    enemyTotal: 4,
    playerHits: 2,
    playerDamageTaken: 1,
    playerShots: 50,
    enemyHits: 10,
    powerUpsCollected: 0,
    stuckTicks: 5,
    firstKillTick: 200,
    playerDeaths: 0,
    cellsVisited: 30,
    playerLevel: 1,
    puSpawnBomb: 0,
    puSpawnTank: 0,
    puSpawnFreeze: 0,
    puSpawnShield: 0,
    puSpawnStar: 0,
    puGotBomb: 0,
    puGotTank: 0,
    puGotFreeze: 0,
    puGotShield: 0,
    score: 1,
    metrics_version: 1,
    greedy: true,
    node_id: 'self',
    elapsedSec: 1,
    phase: 'rollout',
    milestone: false,
    ...over,
  }
}

describe('看板合成端到端', () => {
  it('入队 → 400 局入账 → 过门 + S1 告警', () => {
    const q = enqueueProbeRun({
      course: 'e2e',
      rung_from: 'c4l1',
      ckpt: 'w',
      requester: 't',
      iter: 30,
    })
    expect(q.batch_id.length).toBeGreaterThan(0)
    const q2 = enqueueProbeRun({
      course: 'e2e',
      rung_from: 'c4l1',
      ckpt: 'w',
      requester: 't',
      iter: 30,
    })
    expect(q2.deduped).toBe(true)

    // God 基线工作副本。
    const lad = JSON.parse(readFileSync('tools/training/evalboard/ladder.json', 'utf-8')) as {
      rungs: Array<{ id: string; god: unknown }>
    }
    for (const r of lad.rungs) {
      if (r.id === 'c4l1') r.god = { winRate: 0.64, lifePrice: 1.5, n: 1600, provisional: null }
      if (r.id === 'c6l1') r.god = { winRate: 0.5, lifePrice: 2.0, n: 1600, provisional: null }
    }
    writeFileSync(path.join(root, 'ladder.json'), JSON.stringify(lad))

    // 4 批 × 100 局：c4l1 50% 胜（主门 0.5≥0.448，代价 2≤4.5）；
    // c6l1 命价 ≈23（S1 触发）。
    const known = new Set<string>()
    for (let b = 0; b < 4; b++) {
      for (let i = 0; i < 100; i++) {
        appendRow(
          root,
          row({
            batch_id: `b-e2e-${b}`,
            seed: 860001 + b * 100 + i,
            seed_segment: b,
            win: i < 50,
            outcome: i < 50 ? 'stage_clear' : 'gameover',
            playerHits: 1,
          }),
          known,
        )
        appendRow(
          root,
          row({
            batch_id: `b-e2e-${b}`,
            rung: 'c6l1',
            stage_id: '2001',
            seed: 860001 + b * 100 + i,
            seed_segment: b,
            win: i < 30,
            outcome: i < 30 ? 'stage_clear' : 'gameover',
            playerHits: 7, // 700/30 ≈ 23 > clamp(6,2,13.5)=6 → S1
          }),
          known,
        )
      }
    }
    const v = buildEvalBoardView('e2e', true)
    const c4 = v.ladder.find((l) => l.rung === 'c4l1')
    expect(c4?.n).toBe(400)
    expect(c4?.partial).toBe(false)
    expect(c4?.windowWin).toBeCloseTo(0.5, 5)
    expect(c4?.gate?.pass).toBe(true)
    expect(v.alerts.some((a) => a.id === 'S1' && a.message.startsWith('c6l1'))).toBe(true)
    expect(v.batches.length).toBeGreaterThan(0)
  })
})
