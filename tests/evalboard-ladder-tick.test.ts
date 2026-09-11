/** evalboard-ladder-tick.test.ts ↔ console/evalboard.ts R4 自动爬梯（tick/推导/启停）。 */
import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { enqueueBatch, loadBatches, updateBatch } from '../tools/training/evalboard/batches'
import { pendingRequests } from '../tools/training/evalboard/requests'
import { appendRow, loadRows, type EvalGameRow } from '../tools/training/evalboard/store'
import {
  activeLadderTasks,
  ladderProgress,
  ladderStateFor,
  ladderTick,
  ladderTickAll,
  startLadder,
  stopLadder,
} from '../tools/training/console/evalboard'

let root = ''
let prev = ''
beforeAll(() => {
  prev = process.env.EVALBOARD_DATA ?? ''
  root = mkdtempSync(path.join(tmpdir(), 'evalladder-'))
  process.env.EVALBOARD_DATA = root
})
afterAll(() => {
  if (prev) process.env.EVALBOARD_DATA = prev
  else delete process.env.EVALBOARD_DATA
  rmSync(root, { recursive: true, force: true })
})

const ORDER = ['c4l1', 'c6l1', 'c8l2', 'c10l2', 'c14l3', 'c20l3', 's1l3b0', 's1l3b1']

function brow(over: Partial<EvalGameRow> = {}): EvalGameRow {
  return {
    schema: 1,
    ts: '2026-09-11T00:00:00.000Z',
    source: 'B',
    batch_id: 'b-x',
    batch_unit: { idx: 0, of: 2 },
    run_id: 'run1',
    course: 'lt',
    iter: 1,
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
    puGotStar: 0,
    score: 1,
    metrics_version: 1,
    greedy: true,
    node_id: 'self',
    elapsedSec: 1,
    phase: 'rollout',
    milestone: false,
    ...over,
  } as EvalGameRow
}

describe('启停与任务推导', () => {
  it('start → 活跃；重复 start 去重；stop → 非活跃', () => {
    expect(activeLadderTasks('lt')).toEqual([])
    const s = startLadder({
      course: 'lt',
      iter: 1,
      threshold: 0.2,
      start_rung: 'c4l1',
      ckpt: 'w',
      requester: 't',
    })
    expect(s.deduped).toBe(false)
    expect(activeLadderTasks('lt').length).toBe(1)
    expect(activeLadderTasks('lt')[0]?.ckpt).toBe('w')
    expect(
      startLadder({
        course: 'lt',
        iter: 1,
        threshold: 0.2,
        start_rung: 'c4l1',
        ckpt: 'w',
        requester: 't',
      }).deduped,
    ).toBe(true)
    stopLadder({ course: 'lt', iter: 1, reason: 'test' })
    expect(activeLadderTasks('lt')).toEqual([])
  })
  it('无 ladder 请求时 tickAll 直接返回', () => {
    expect(ladderTickAll(['lt'])).toEqual({ tasks: 0, enqueued: [] })
  })
})

describe('tick 推进与止损（纯函数 + 请求级）', () => {
  it('progress：空 → 未停；达标 → next；低于阈值 → 筛查级停止', () => {
    const p0 = ladderProgress('lt', 1, 0.2, [], [], ORDER)
    expect(p0.reachedRung).toBeNull()
    expect(p0.stopped).toBe(false)
    const rows = [brow({ batch_id: 'b-d', rung: 'c4l1', win: true })]
    const batches = [
      {
        batch_id: 'b-d',
        course: 'lt',
        rung_from: 'c4l1',
        ckpt: 'w',
        requester: 't',
        created_ts: 't',
        status: 'done' as const,
        units: { of: 2, done: [0, 1] },
        k_seq: 0,
        window_seq: 0,
        trigger: 'auto-ladder' as const,
        iter: 1,
        node_dist: {},
        elapsed_sec: 1,
      },
    ]
    const p1 = ladderProgress('lt', 1, 0.2, rows, batches, ORDER)
    expect(p1.reachedRung).toBe('c4l1')
    expect(p1.reachedWin).toBe(1)
    expect(p1.nextRung).toBe('c6l1')
    expect(p1.stopped).toBe(false)
    const bad = [brow({ batch_id: 'b-d', rung: 'c4l1', win: false, outcome: 'gameover' })]
    const p2 = ladderProgress('lt', 1, 0.2, bad, batches, ORDER)
    expect(p2.stopped).toBe(true)
    expect(p2.stoppedReason).toContain('筛查级')
    expect(p2.stoppedReason).toContain('不作 verdict')
  })
  it('tick：首关入队 → 去重 → 达标推进 → 失败停止', () => {
    startLadder({
      course: 'lt2',
      iter: 2,
      threshold: 0.2,
      start_rung: 'c4l1',
      ckpt: 'w2',
      requester: 't',
    })
    const t1 = ladderTick(['lt2'])
    expect(t1.tasks).toBe(1)
    expect(t1.enqueued.length).toBe(1)
    const reqs = pendingRequests(root).filter((r) => r.kind === 'enqueue' && r.course === 'lt2')
    expect(reqs.length).toBe(1)
    expect(reqs[0]?.rung_from).toBe('c4l1')
    expect(reqs[0]?.trigger).toBe('auto-ladder')
    // 重复 tick 不造重复请求（请求仍在排队）。
    expect(ladderTick(['lt2']).enqueued).toEqual([])
    stopLadder({ course: 'lt2', iter: 2 })
  })
  it('tick：rung1 达标（done 批行）→ 自动入队 rung2；失守 → 停并不再入队', () => {
    startLadder({
      course: 'lt3',
      iter: 5,
      threshold: 0.2,
      start_rung: 'c4l1',
      ckpt: 'w3',
      requester: 't',
    })
    // 模拟 runner 跑完 c4l1（全胜 done 批）。
    const b1 = enqueueBatch(root, {
      course: 'lt3',
      rung_from: 'c4l1',
      ckpt: 'w3',
      requester: 't',
      trigger: 'auto-ladder',
      iter: 5,
    })
    updateBatch(root, b1.batch_id, { status: 'done', elapsed_sec: 60 })
    const known = new Set<string>()
    for (let i = 0; i < 10; i++) {
      appendRow(
        root,
        brow({
          batch_id: b1.batch_id,
          course: 'lt3',
          iter: 5,
          rung: 'c4l1',
          seed: 860001 + i,
          win: true,
        }),
        known,
      )
    }
    const t2 = ladderTick(['lt3'])
    expect(t2.enqueued.length).toBe(1)
    const r2 = pendingRequests(root).filter((r) => r.kind === 'enqueue' && r.course === 'lt3')
    expect(r2.some((r) => r.rung_from === 'c6l1' && r.trigger === 'auto-ladder')).toBe(true)
    // c6l1 全败 done → 止损：不再入队，状态为筛查级停止。
    const b2 = enqueueBatch(root, {
      course: 'lt3',
      rung_from: 'c6l1',
      ckpt: 'w3',
      requester: 't',
      trigger: 'auto-ladder',
      iter: 5,
    })
    updateBatch(root, b2.batch_id, { status: 'done', elapsed_sec: 60 })
    for (let i = 0; i < 10; i++) {
      appendRow(
        root,
        brow({
          batch_id: b2.batch_id,
          course: 'lt3',
          iter: 5,
          rung: 'c6l1',
          seed: 870001 + i,
          win: false,
          outcome: 'gameover',
        }),
        known,
      )
    }
    expect(ladderTick(['lt3']).enqueued).toEqual([])
    const rows = loadRows(root).filter((x) => x.course === 'lt3')
    const st = ladderStateFor('lt3', rows, loadBatches(root))
    expect(st?.reachedRung).toBe('c6l1')
    expect(st?.stopped).toBe(true)
    expect(st?.stoppedReason).toContain('筛查级')
    stopLadder({ course: 'lt3', iter: 5 })
  })
})

describe('ladderStateFor 视图推导', () => {
  it('无任务 ⇒ null；手动停止 ⇒ stopped + reason', () => {
    expect(ladderStateFor('nostate', [], [])).toBeNull()
    startLadder({
      course: 'ls',
      iter: 3,
      threshold: 0.25,
      start_rung: 'c4l1',
      ckpt: 'w',
      requester: 't',
    })
    const active = ladderStateFor('ls', [], [])
    expect(active?.stopped).toBe(false)
    expect(active?.iter).toBe(3)
    expect(active?.threshold).toBe(0.25)
    expect(active?.reachedRung).toBeNull()
    stopLadder({ course: 'ls', iter: 3, reason: 'manual' })
    const stopped = ladderStateFor('ls', [], [])
    expect(stopped?.stopped).toBe(true)
    expect(stopped?.stoppedReason).toBe('manual')
  })
})
