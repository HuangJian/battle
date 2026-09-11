/** evalboard-board.test.ts ↔ tools/training/console/evalboard.ts（P3 看板端到端合成）。 */
import { describe, expect, it, beforeAll, afterAll } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { appendRow, type EvalGameRow } from '../tools/training/evalboard/store'
import { enqueueBatch } from '../tools/training/evalboard/batches'
import {
  buildEvalBoardView,
  buildEvalCkptsView,
  enqueueProbeRun,
  iterFromCkpt,
} from '../tools/training/console/evalboard'
import { enemyBreakdown, rungLabel } from '../tools/training/ui/view'

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
    expect(q.req_id.startsWith('q-')).toBe(true)
    const q2 = enqueueProbeRun({
      course: 'e2e',
      rung_from: 'c4l1',
      ckpt: 'w',
      requester: 't',
      iter: 30,
    })
    expect(q2.deduped).toBe(true)
    // runner 物化（测试内模拟 consume_requests 建批；console 只写请求）。
    enqueueBatch(root, {
      course: 'e2e',
      rung_from: 'c4l1',
      ckpt: 'w',
      requester: 't',
      trigger: 'standalone',
      iter: 30,
    })

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

    // R5 iter 矩阵：God 行 + it30 学生行；God 只填胜率列。
    const god = v.iterRows.find((r) => r.kind === 'god')
    expect(god).toBeTruthy()
    expect(god!.cells['c4l1.winRate']).toBeCloseTo(0.64, 5)
    expect(god!.cells['c4l1.meanKills']).toBeUndefined()
    const it30 = v.iterRows.find((r) => r.kind === 'iter' && r.iter === 30)
    expect(it30).toBeTruthy()
    expect(it30!.cells['c4l1.winRate']).toBeCloseTo(0.5, 5)
    expect(it30!.n).toBe(800)
    expect(it30!.screening).toBe(true)
    expect(it30!.batchIds).toContain('b-e2e-0')
  })
})

describe('R1 rungLabel / D-b iterFromCkpt', () => {
  const brief = {
    name: 'arena-13x13-4enemies',
    enemies: ['basic', 'basic', 'basic', 'basic'],
    enemyCount: 4,
    hasBase: false,
    hasTerrain: false,
  }
  it('全同敌型 · 无地形无基地', () => {
    expect(
      rungLabel({ rung: 'c4l1', dimension: '基准', lives: 1, starLevel: 1, stageBrief: brief }),
    ).toBe('c4l1 · 4敌(全basic) · 1命 · 1★ · 无地形 · 无基地｜基准')
  })
  it('多型敌 · 有地形 · 有基地(可摧毁)', () => {
    const enemies = [...Array(18).fill('basic'), 'fast', 'fast']
    expect(
      rungLabel({
        rung: 's1l3b1',
        dimension: '基地防守',
        lives: 3,
        starLevel: 1,
        stageBrief: { ...brief, enemies, enemyCount: 20, hasBase: true, hasTerrain: true },
      }),
    ).toBe('s1l3b1 · 20敌(18basic+2fast) · 3命 · 1★ · 有地形 · 有基地(可摧毁)｜基地防守')
  })
  it('enemyBreakdown 首现序分组', () => {
    expect(enemyBreakdown(['fast', 'basic', 'fast'])).toBe('2fast+1basic')
  })
  it('iterFromCkpt 解析 weights.it<N>.*.json，无 it ⇒ null', () => {
    expect(iterFromCkpt('tmp/c4-margin/weights.it30.20260911.json')).toBe(30)
    expect(iterFromCkpt('D:\\go\\weights.it7.abc.json')).toBe(7)
    expect(iterFromCkpt('tmp/c4-margin/weights.json')).toBeNull()
  })
})

describe('R7 buildEvalCkptsView（只读元数据）', () => {
  it('返回腿列表 + 元数据文件；不含内容/张量字段', () => {
    const v = buildEvalCkptsView('c4-margin')
    expect(Array.isArray(v.legs)).toBe(true)
    expect(Array.isArray(v.files)).toBe(true)
    for (const f of v.files) {
      expect(f.path).not.toContain('\\') // 仓库相对路径统一 / 分隔
      expect(typeof f.sizeBytes).toBe('number')
      expect(typeof f.mtime).toBe('number')
    }
    expect(JSON.stringify(v)).not.toContain('"tensors"')
    // 有 c4-margin 腿时，iter 解析正确（文件名 c4-margin.it<N>.<ts>.json）。
    const leg = v.legs.find((l) => l.leg === 'c4-margin')
    if (leg && leg.count > 0) {
      const withIter = v.files.filter((f) => f.iter !== null)
      expect(withIter.length).toBeGreaterThan(0)
      for (const f of withIter) expect(f.iter).toBeGreaterThan(0)
    }
  })
})

describe('R4-G1 runner_state 心跳读取', () => {
  it('心跳文件 → view.runnerState 映射（snake → camel）', () => {
    writeFileSync(
      path.join(root, 'runner_state.json'),
      JSON.stringify({
        window_open: false,
        updated_ts: 123,
        batch_id: 'b-heartbeat',
        unit_idx: 2,
        unit_of: 3,
        rung: 'c6l1',
        remaining_units: 1,
        last_window_closed_ts: 456,
        engine_epoch: 'e0',
      }),
    )
    const v = buildEvalBoardView('e2e', true)
    expect(v.runnerState).not.toBeNull()
    expect(v.runnerState?.windowOpen).toBe(false)
    expect(v.runnerState?.batchId).toBe('b-heartbeat')
    expect(v.runnerState?.unitIdx).toBe(2)
    expect(v.runnerState?.unitOf).toBe(3)
    expect(v.runnerState?.rung).toBe('c6l1')
    expect(v.runnerState?.remainingUnits).toBe(1)
    expect(v.runnerState?.lastWindowClosedTs).toBe(456)
    expect(v.runnerState?.engineEpoch).toBe('e0')
    rmSync(path.join(root, 'runner_state.json'), { force: true })
    expect(buildEvalBoardView('e2e', true).runnerState).toBeNull()
  })
})
