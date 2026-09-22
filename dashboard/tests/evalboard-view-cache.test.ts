/** evalboard-view-cache.test.ts ↔ dashboard/src/server/eval-board/view.ts 的**按课程**视图缓存：
 *  输入没变就不重算；某门课的数据变了就**立即**重算（不等 TTL）。 */
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'bun:test'
import { appendFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { appendRow, gamesDir, type EvalGameRow } from '../src/evalboard/store'
import { enqueueBatch } from '../src/evalboard/batches'
import { appendRequest } from '../src/evalboard/requests'
import { invalidateEvalBoard } from '../src/server/eval-board/auto-ladder'
import { evalDataRoot, trackedLadder } from '../src/server/eval-board/ladder-data'
import {
  buildEvalBoardView,
  viewCache,
  viewInputFiles,
  viewInputSignature,
  VIEW_BACKSTOP_MS,
} from '../src/server/eval-board'

let DIR = ''
let ROOT = ''
let prevData: string | undefined
let prevTmp: string | undefined

beforeAll(() => {
  DIR = mkdtempSync(path.join(tmpdir(), 'evb-viewcache-'))
})

afterAll(() => {
  rmSync(DIR, { recursive: true, force: true })
})

beforeEach(() => {
  ROOT = mkdtempSync(path.join(DIR, 'root-'))
  mkdirSync(gamesDir(ROOT), { recursive: true })
  mkdirSync(path.join(ROOT, 'tmp'), { recursive: true })
  prevData = process.env.EVALBOARD_DATA
  prevTmp = process.env.BCITY_TMP_LOGS_DIR
  process.env.EVALBOARD_DATA = ROOT
  process.env.BCITY_TMP_LOGS_DIR = path.join(ROOT, 'tmp') // 空 tmp：入账路径不碰仓根 tmp/
  invalidateEvalBoard()
})

/** 每个用例收尾恢复环境（避免污染同进程的其它测试文件）。 */
const restoreEnv = (): void => {
  if (prevData === undefined) delete process.env.EVALBOARD_DATA
  else process.env.EVALBOARD_DATA = prevData
  if (prevTmp === undefined) delete process.env.BCITY_TMP_LOGS_DIR
  else process.env.BCITY_TMP_LOGS_DIR = prevTmp
}

function row(course: string, seed: number): EvalGameRow {
  return {
    schema: 1,
    ts: '2026-09-10T00:00:00.000Z',
    source: 'B',
    batch_id: `b-${course}`,
    batch_unit: { idx: 0, of: 1 },
    run_id: course,
    course,
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
    seed,
    seed_segment: 0,
    engine: { git_commit: 'g', dist_codehash: 'd', engine_epoch: 'e0' },
    outcome: 'stage_clear',
    win: true,
    cleared: true,
    ticks: 1000,
    kills: 4,
    enemyTotal: 4,
    metrics_version: 1,
    greedy: true,
  } as EvalGameRow
}

const key = (course: string): string => course

describe('视图缓存：输入没变就不重算', () => {
  it('第二次读命中：内层数组实例不变（= 真的没重算），ingested 归 0', () => {
    appendRow(ROOT, row('cA', 860001))
    const first = buildEvalBoardView('cA')
    const second = buildEvalBoardView('cA')
    expect(second.ladder).toBe(first.ladder) // 内层数组同一实例
    expect(second.iterRows).toBe(first.iterRows)
    expect(second.cachedAt).toBe(first.cachedAt)
    expect(second.ingested).toBe(0)
    expect(second).not.toBe(first) // 外层是浅拷贝（只为把 ingested 归 0）
    restoreEnv()
  })

  // 注：「过了 30s/5min 仍是命中、过了兵底才重算」的时钟语义由原语用例钉
  // （`tests/fingerprint-cache.test.ts` 用可控时钟，这里不 sleep）——本文件只钉接线。

  it('eval_log 有新行 → 入账后立即重算，ingested 如实报数', () => {
    appendRow(ROOT, row('cA', 860001))
    expect(buildEvalBoardView('cA').rows).toBe(1)
    mkdirSync(path.join(ROOT, 'tmp', 'cA'), { recursive: true })
    writeFileSync(
      path.join(ROOT, 'tmp', 'cA', 'eval_log.jsonl'),
      `${JSON.stringify({
        event: 'eval',
        iter: 30,
        wver: 'v2',
        stage: 0,
        seed: 860050,
        outcome: 'stage_clear',
        win: 1,
      })}\n`,
      'utf-8',
    )
    const second = buildEvalBoardView('cA')
    expect(second.ingested).toBe(1)
    expect(second.rows).toBe(2)
    expect(buildEvalBoardView('cA').ingested).toBe(0) // 再读命中：这次没读入新行
    restoreEnv()
  })

  it('fresh=1 仍然强制重算；invalidateEvalBoard() 仍然整张清掉', () => {
    appendRow(ROOT, row('cA', 860001))
    const first = buildEvalBoardView('cA')
    expect(buildEvalBoardView('cA', true).ladder).not.toBe(first.ladder)
    invalidateEvalBoard()
    expect(viewCache.peek(key('cA'))).toBe(false)
    expect(buildEvalBoardView('cA').ladder).not.toBe(first.ladder)
    restoreEnv()
  })

  it('兵底比任何轮询间隔都长（它只能当安全网，不能当 TTL 用）', () => {
    // 轮询口径：/eval 页与首页摘要 300s、盯批循环 15s。兵底短于轮询 = 每轮又白付一次聚合。
    expect(VIEW_BACKSTOP_MS).toBeGreaterThan(300_000)
    expect(VIEW_BACKSTOP_MS).toBeGreaterThan(15_000)
    // 兵底到点**重算一次**的行为在原语用例里钉（那里能注入可控时钟）
  })

  it('缓存自己记账：命中/重算看得见（诊断入口）', () => {
    appendRow(ROOT, row('cA', 860001))
    viewCache.clear()
    buildEvalBoardView('cA')
    buildEvalBoardView('cA')
    const s = viewCache.stats()
    expect(s.misses).toBe(1)
    expect(s.hits).toBe(1)
    restoreEnv()
  })
})

describe('视图缓存：只重算变化的课程', () => {
  it('本课程新行 → 立即重算（不等兜底）', () => {
    appendRow(ROOT, row('cA', 860001))
    const first = buildEvalBoardView('cA')
    expect(first.rows).toBe(1)
    appendRow(ROOT, row('cA', 860002))
    const second = buildEvalBoardView('cA')
    expect(second.ladder).not.toBe(first.ladder)
    expect(second.rows).toBe(2)
    restoreEnv()
  })

  it('**别的**课程新行 → 本课程命中缓存（互不牵连）', () => {
    appendRow(ROOT, row('cA', 860001))
    appendRow(ROOT, row('cB', 860001))
    const a1 = buildEvalBoardView('cA')
    const b1 = buildEvalBoardView('cB')
    appendRow(ROOT, row('cB', 860002)) // 只有 cB 变
    expect(buildEvalBoardView('cA').ladder).toBe(a1.ladder) // cA 原样命中
    const b2 = buildEvalBoardView('cB')
    expect(b2.ladder).not.toBe(b1.ladder)
    expect(b2.rows).toBe(2)
    restoreEnv()
  })

  it('聚合视图（course=""）用全课程指纹：任一门课变都会重算', () => {
    appendRow(ROOT, row('cA', 860001))
    const all1 = buildEvalBoardView('')
    expect(buildEvalBoardView('').ladder).toBe(all1.ladder)
    appendRow(ROOT, row('cB', 860001))
    expect(buildEvalBoardView('').ladder).not.toBe(all1.ladder)
    restoreEnv()
  })
})

describe('视图缓存：每个输入变了都立即重算（行为断言，不是只断签名）', () => {
  it('批次台账', () => {
    appendRow(ROOT, row('cA', 860001))
    const first = buildEvalBoardView('cA')
    expect(first.batches.length).toBe(0)
    enqueueBatch(ROOT, {
      course: 'cA',
      rung_from: 'c4l1',
      ckpt: 'w',
      requester: 't',
      trigger: 'standalone',
      iter: 30,
    })
    const second = buildEvalBoardView('cA')
    expect(second.batches.length).toBe(1)
    expect(second.ladder).not.toBe(first.ladder)
    restoreEnv()
  })

  it('请求文件（ladderState 由此而来）', () => {
    appendRow(ROOT, row('cA', 860001))
    expect(buildEvalBoardView('cA').ladderState).toBe(null)
    appendRequest(ROOT, {
      kind: 'ladder_start',
      requester: 't',
      course: 'cA',
      iter: 30,
      threshold: 0.2,
      start_rung: 'c4l1',
      ckpt: 'w',
    })
    expect(buildEvalBoardView('cA').ladderState).not.toBe(null)
    restoreEnv()
  })

  it('runner 状态文件', () => {
    appendRow(ROOT, row('cA', 860001))
    expect(buildEvalBoardView('cA').runnerState).toBe(null)
    writeFileSync(
      path.join(evalDataRoot(), 'runner_state.json'),
      JSON.stringify({ window_open: true, updated_ts: 1 }),
      'utf-8',
    )
    expect(buildEvalBoardView('cA').runnerState?.windowOpen).toBe(true)
    restoreEnv()
  })

  it('space_calibration 出现', () => {
    appendRow(ROOT, row('cA', 860001))
    expect(buildEvalBoardView('cA').spaceCalibrated).toBe(false)
    writeFileSync(path.join(evalDataRoot(), 'space_calibration.json'), '{}', 'utf-8')
    expect(buildEvalBoardView('cA').spaceCalibrated).toBe(true)
    restoreEnv()
  })

  it('阶梯工作副本（god 基线）', () => {
    appendRow(ROOT, row('cA', 860001))
    const first = buildEvalBoardView('cA')
    const rung = trackedLadder()[0]!
    writeFileSync(
      path.join(evalDataRoot(), 'ladder.json'),
      JSON.stringify({
        rungs: [
          { id: rung.id, god: { winRate: 0.42, lifePrice: 1.5, n: 100, provisional: false } },
        ],
      }),
      'utf-8',
    )
    const second = buildEvalBoardView('cA')
    expect(second.ladder).not.toBe(first.ladder)
    expect(second.ladder.find((r) => r.rung === rung.id)?.god.winRate).toBe(0.42)
    restoreEnv()
  })
})

describe('视图输入清单（唯一一份）', () => {
  it('清单含全部输入标签（仓内两个文件只断存在，不动仓内文件）', () => {
    const labels = viewInputFiles('cA', ROOT).map(([l]) => l)
    expect(labels).toEqual([
      'batches',
      'requests',
      'requests-done',
      'ladder-work',
      'ladder-canon',
      'runner-state',
      'space-calib',
      'eval-log',
      'eval-log-traj',
      'curricula',
    ])
    // 无课程（聚合视图）时不含课程相关项
    expect(viewInputFiles('', ROOT).map(([l]) => l)).not.toContain('curricula')
    expect(viewInputFiles('', ROOT).map(([l]) => l)).not.toContain('eval-log')
  })

  it('改动清单里**任一**临时根文件 → 指纹必变（漏项会被这条抓住）', () => {
    const inRoot = new Set([
      'batches',
      'requests',
      'requests-done',
      'ladder-work',
      'runner-state',
      'space-calib',
      'eval-log',
      'eval-log-traj',
    ])
    appendRow(ROOT, row('cA', 860001)) // 让行指纹有内容
    for (const [label, p] of viewInputFiles('cA', ROOT)) {
      if (!inRoot.has(label)) continue
      const before = viewInputSignature('cA', ROOT)
      mkdirSync(path.dirname(p), { recursive: true })
      appendFileSync(p, 'x\n', { flag: 'a' }) // 不存在则创建（size/mtime 必变）
      expect(`${label}:${viewInputSignature('cA', ROOT) !== before}`).toBe(`${label}:true`)
    }
    restoreEnv()
  })

  it('行指纹按课程隔离：只影响对应课程（与聚合视图）', () => {
    appendRow(ROOT, row('cA', 860001))
    const sigA = viewInputSignature('cA', ROOT)
    const sigB = viewInputSignature('cB', ROOT)
    const sigAll = viewInputSignature('', ROOT)
    appendRow(ROOT, row('cB', 860001))
    expect(viewInputSignature('cA', ROOT)).toBe(sigA) // cB 变了，cA 的指纹不动
    expect(viewInputSignature('cB', ROOT)).not.toBe(sigB)
    expect(viewInputSignature('', ROOT)).not.toBe(sigAll)
    restoreEnv()
  })
})
