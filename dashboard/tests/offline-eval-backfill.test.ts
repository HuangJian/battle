/**
 * offline-eval-backfill.test.ts — 离线课「云机没回传 eval」的 hub 端补评契约
 * （plan/offline-eval-backfill.plan.md §4）。
 *
 * 分层：纯选择（`selectOfflineEvalRounds`）/ 增量账本索引（`EvalLedgerIndex`）/ 课程配置读取 /
 * 权重发现（`roundCkptFor`）/ 编排（`createOfflineEvalBackfill`，依赖全注入 **不开真子进程**）/
 * 接线守卫（server.ts 源码）+ 默认 hub 链路（假 hub 的 `/admin/offline.results` 经 `getHubAdmin`
 * 直达收官补评——overview 的新字段行为级验收）。
 *
 * 夹具：`BCITY_TMP_LOGS_DIR` / `BCITY_CURRICULA_DIR` 惰性重定向 + 显式 `tmpRoot`/`curriculaRoot`
 * 注入（双保险）；假 hub 经临时 `BCITY_REGISTRY_FILE` 账本条目定位。真实 tmp/ 整轮零读零写。
 */

import { afterAll, afterEach, describe, expect, it } from 'bun:test'
import { createHash } from 'crypto'
import { appendFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import type { OfflineAdminView, OfflineResultView, OfflineRunView } from '../src/web/view'
import type { EvalALaunch } from '../src/server/eval-a-run'
import {
  createOfflineEvalBackfill,
  EvalLedgerIndex,
  readCourseEvalConfig,
  roundCkptFor,
  selectOfflineEvalRounds,
  type OfflineRunSchedule,
} from '../src/server/offline-eval-backfill'
import { api, loadConfig } from './helpers/console-fixture'

const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-offline-backfill-'))
const TMP = path.join(scratch, 'tmp')
const CUR = path.join(scratch, 'curricula')
mkdirSync(TMP, { recursive: true })
mkdirSync(CUR, { recursive: true })
process.env.BCITY_TMP_LOGS_DIR = TMP
process.env.BCITY_CURRICULA_DIR = CUR

afterAll(() => rmSync(scratch, { recursive: true, force: true }))

// ────────────────────────── 夹具小工具 ──────────────────────────

let seq = 0
/** 造一门独立课程（每用例一个名字 ⇒ 文件互不串扰）。 */
function freshCourse(over: Record<string, unknown> = {}): string {
  const name = `ob-${++seq}`
  writeFileSync(
    path.join(CUR, `${name}.jsonc`),
    JSON.stringify({ eval_every: 5, eval_games_per_stage: 50, ...over }, null, 2),
  )
  mkdirSync(path.join(TMP, name), { recursive: true })
  return name
}

/** 回传树里落一轮权重；返回文件路径。 */
function backfeedWeights(
  course: string,
  run: string,
  iter: number,
  bytes = `w-${course}-${run}-${iter}`,
): string {
  const dir = path.join(
    TMP,
    course,
    'remote-jobs',
    'offline',
    run,
    `it-${String(iter).padStart(3, '0')}`,
  )
  mkdirSync(dir, { recursive: true })
  const file = path.join(dir, 'weights.json')
  writeFileSync(file, bytes)
  return file
}

/** 交付镜像里落一轮权重（导入路径的布局）；返回文件路径。 */
function mirrorWeights(course: string, run: string, iter: number, bytes: string): string {
  const dir = path.join(TMP, course, 'deliver', run, `it-${String(iter).padStart(3, '0')}`)
  mkdirSync(dir, { recursive: true })
  const file = path.join(dir, 'weights.json')
  writeFileSync(file, bytes)
  return file
}

/** 往课程账本追加若干 JSON 行。 */
function appendLedger(course: string, lines: unknown[]): void {
  appendFileSync(
    path.join(TMP, course, 'eval_log.jsonl'),
    lines.map((l) => JSON.stringify(l) + '\n').join(''),
  )
}

/** 权重文件的 W16（与 evalA 同定义：字节 sha256 前 16 位）。 */
function w16Of(file: string): string {
  return createHash('sha256').update(readFileSync(file)).digest('hex').slice(0, 16)
}

function runView(its: number[]): OfflineRunView {
  return { its: [...its].sort((a, b) => a - b), count: its.length, lastMtime: 0 }
}

function resultView(over: Partial<OfflineResultView> = {}): OfflineResultView {
  return { itEnd: 0, state: 'complete', endItReached: false, receivedAt: 0, ...over }
}

function adminOf(
  progress: Record<string, Record<string, OfflineRunView>>,
  results: Record<string, Record<string, OfflineResultView>> = {},
): OfflineAdminView {
  return { progress, results, stalled: [] }
}

/** 假 launch：只记录调用（绝不起子进程）。 */
function recorder(fail = false): {
  calls: Array<{ course: string; ckpt: string; iter: number }>
  launch: (course: string, ckpt: string, iter: number) => EvalALaunch
} {
  const calls: Array<{ course: string; ckpt: string; iter: number }> = []
  return {
    calls,
    launch: (course, ckpt, iter) => {
      calls.push({ course, ckpt, iter })
      return fail ? { ok: false, message: 'boom' } : { ok: true, message: 'fake' }
    },
  }
}

// ────────────────────────── 纯选择 ──────────────────────────

const S = (its: number[], extra: Partial<OfflineRunSchedule> = {}): OfflineRunSchedule => ({
  its,
  ...extra,
})

describe('selectOfflineEvalRounds（+3 宽限 / 收官两条规则）', () => {
  it('宽限边界：max(its) >= N+3 才轮到 N（差一轮不评）', () => {
    expect(selectOfflineEvalRounds({ r: S([5, 7, 8]) }, 5)).toEqual([
      { run: 'r', iter: 5, reason: 'grace' },
    ])
    expect(selectOfflineEvalRounds({ r: S([5, 7]) }, 5)).toEqual([])
  })

  it('非评估点一律跳过（eval_every=5 只认 5/10/15…）', () => {
    expect(selectOfflineEvalRounds({ r: S([1, 2, 3, 4, 5, 6, 7, 8]) }, 5)).toEqual([
      { run: 'r', iter: 5, reason: 'grace' },
    ])
  })

  it('收官：it_end 之下最后一个评估点缺读数 ⇒ 立即（reason=final，不等 +3）', () => {
    // it_end=11（非 5 的倍数）：最后评估点 = 10，且 10 已在 its —— 宽限还没到（10+3>11）也必须评。
    const cands = selectOfflineEvalRounds({ r: S([5, 10], { endItReached: true, itEnd: 11 }) }, 5)
    expect(cands).toEqual([
      { run: 'r', iter: 5, reason: 'grace' },
      { run: 'r', iter: 10, reason: 'final' },
    ])
    // 收官轮本身不是评估点（it_end=21）：21 永不出现；最后评估点 20 已在 its ⇒ 由 final 补。
    const cands2 = selectOfflineEvalRounds({ r: S([20, 21], { endItReached: true, itEnd: 21 }) }, 5)
    expect(cands2.every((c) => c.iter % 5 === 0)).toBe(true)
    expect(cands2).toContainEqual({ run: 'r', iter: 20, reason: 'final' })
    // 未自报跑满（end_it_reached=false）⇒ 没有 final 分支，20 属别的 run 的宽限才轮到。
    const cands3 = selectOfflineEvalRounds({ r: S([20], { endItReached: false, itEnd: 20 }) }, 5)
    expect(cands3.some((c) => c.reason === 'final')).toBe(false)
  })

  it('每 run 独立宽限：别的 run 回传得远不算', () => {
    const cands = selectOfflineEvalRounds({ a: S([5, 7]), b: S([8]) }, 5)
    expect(cands).toEqual([{ run: 'b', iter: 5, reason: 'grace' }])
  })
})

// ────────────────────────── 增量账本索引 ──────────────────────────

describe('EvalLedgerIndex（eval_log.jsonl 增量索引）', () => {
  it('追加 summary 后 has=true；无关行不改变；文件变短（截断）⇒ 重建', () => {
    const file = path.join(scratch, 'idx', 'eval_log.jsonl')
    mkdirSync(path.dirname(file), { recursive: true })
    const a = 'a'.repeat(16)
    writeFileSync(file, JSON.stringify({ event: 'eval_summary', iter: 5, wver: a }) + '\n')
    const idx = new EvalLedgerIndex(file)
    idx.refresh()
    expect(idx.has(5, a)).toBe(true)
    expect(idx.has(5, 'b'.repeat(16))).toBe(false)
    // 无关行（逐局 eval）不产生新读数
    appendFileSync(file, JSON.stringify({ event: 'eval', iter: 5, win: true }) + '\n')
    idx.refresh()
    expect(idx.has(5, a)).toBe(true)
    // 追加新 summary ⇒ 增量可见
    const c = 'c'.repeat(16)
    appendFileSync(file, JSON.stringify({ event: 'eval_summary', iter: 10, wver: c }) + '\n')
    idx.refresh()
    expect(idx.has(10, c)).toBe(true)
    // 截断（替换成更短的文件）⇒ 从头重建，旧键消失
    writeFileSync(file, JSON.stringify({ event: 'eval_summary', iter: 5, wver: a }) + '\n')
    idx.refresh()
    expect(idx.has(10, c)).toBe(false)
    expect(idx.has(5, a)).toBe(true)
  })

  it('半行（写入竞态）不算，补上换行后才算', () => {
    const file = path.join(scratch, 'idx', 'half.jsonl')
    const d = 'd'.repeat(16)
    const line = JSON.stringify({ event: 'eval_summary', iter: 7, wver: d })
    writeFileSync(file, line) // 无换行 = 半行
    const idx = new EvalLedgerIndex(file)
    idx.refresh()
    expect(idx.has(7, d)).toBe(false)
    appendFileSync(file, '\n')
    idx.refresh()
    expect(idx.has(7, d)).toBe(true)
  })
})

// ────────────────────────── 课程配置 / 权重发现 ──────────────────────────

describe('readCourseEvalConfig', () => {
  it('读 eval_every / eval_games_per_stage；缺省与拒收同 CloudEvalPlan 口径', () => {
    const c1 = freshCourse()
    expect(readCourseEvalConfig(c1, CUR)).toEqual({ evalEvery: 5, gamesPerStage: 50 })
    const c2 = freshCourse({ eval_every: undefined })
    expect(readCourseEvalConfig(c2, CUR)?.evalEvery).toBe(1)
    const c3 = freshCourse({ eval_games_per_stage: 0 })
    expect(readCourseEvalConfig(c3, CUR)).toBeNull()
    expect(readCourseEvalConfig('no-such-course', CUR)).toBeNull()
  })
})

describe('roundCkptFor', () => {
  it('回传树优先于交付镜像；row.json 的 weights_fp 优先于现算', () => {
    const c = freshCourse()
    const mirror = mirrorWeights(c, 'run1', 5, 'mirror-bytes')
    expect(roundCkptFor(TMP, c, 'run1', 5)).toEqual({
      path: mirror,
      key16: w16Of(mirror),
    })
    const back = backfeedWeights(c, 'run1', 5, 'backfeed-bytes')
    expect(roundCkptFor(TMP, c, 'run1', 5)).toEqual({ path: back, key16: w16Of(back) })
    // row.json 声明指纹（hub 入口已对账过实收字节）——它就是权威 W16
    writeFileSync(
      path.join(path.dirname(back), 'row.json'),
      JSON.stringify({ weights_fp: 'f'.repeat(64) }),
    )
    expect(roundCkptFor(TMP, c, 'run1', 5)).toEqual({ path: back, key16: 'f'.repeat(16) })
    expect(roundCkptFor(TMP, c, 'run1', 6)).toBeNull()
  })
})

// ────────────────────────── 编排 ──────────────────────────

/** 造一个拍器（依赖全注入；fetchOfflineAdmin 用给定 admin 或 null）。 */
function backfill(
  admin: OfflineAdminView | null,
  opts: {
    launch?: (course: string, ckpt: string, iter: number) => EvalALaunch
    isEvalABusy?: () => boolean
    now?: () => number
  } = {},
) {
  return createOfflineEvalBackfill({
    fetchOfflineAdmin: async () => admin,
    tmpRoot: TMP,
    curriculaRoot: CUR,
    launch: opts.launch ?? recorder().launch,
    isEvalABusy: opts.isEvalABusy ?? (() => false),
    now: opts.now ?? Date.now,
    logLine: () => {},
    logWarn: () => {},
  })
}

describe('createOfflineEvalBackfill（编排）', () => {
  it('云机没评 + 宽限已到 ⇒ 用回传树权重启动一次 evalA', async () => {
    const c = freshCourse()
    const ckpt = backfeedWeights(c, 'run1', 5)
    const { calls, launch } = recorder()
    const tick = await backfill(adminOf({ [c]: { run1: runView([5, 7, 8]) } }), { launch }).run()
    expect(tick).toEqual({
      skipped: null,
      launched: { course: c, run: 'run1', iter: 5, reason: 'grace', ckpt },
    })
    expect(calls).toEqual([{ course: c, ckpt, iter: 5 }])
  })

  it('账本已有 (N, W16) summary ⇒ 跳过；只有别的 wver ⇒ 仍补评', async () => {
    const c = freshCourse()
    const ckpt = backfeedWeights(c, 'run1', 5)
    const { calls, launch } = recorder()
    const admin = adminOf({ [c]: { run1: runView([5, 7, 8]) } })
    // 别的 wver 的 summary 不顶本轮读数 ⇒ 照样补评
    appendLedger(c, [{ event: 'eval_summary', iter: 5, wver: '0'.repeat(16) }])
    expect((await backfill(admin, { launch }).run()).launched?.iter).toBe(5)
    expect(calls).toHaveLength(1)
    // 本轮 (N, W16) 的 summary 落账 ⇒ 跳过（幂等）
    appendLedger(c, [{ event: 'eval_summary', iter: 5, wver: w16Of(ckpt) }])
    expect((await backfill(admin, { launch }).run()).launched).toBeNull()
    expect(calls).toHaveLength(1)
  })

  it('row.json 的 weights_fp 是账本查询键（与现算 sha 不同也按 row 判）', async () => {
    const c = freshCourse()
    const ckpt = backfeedWeights(c, 'run1', 5, 'bytes-A')
    writeFileSync(
      path.join(path.dirname(ckpt), 'row.json'),
      JSON.stringify({ weights_fp: 'e'.repeat(64) }),
    )
    const { calls, launch } = recorder()
    appendLedger(c, [{ event: 'eval_summary', iter: 5, wver: 'e'.repeat(16) }])
    const tick = await backfill(adminOf({ [c]: { run1: runView([5, 8]) } }), { launch }).run()
    expect(tick.launched).toBeNull()
    expect(calls).toEqual([])
  })

  it('收官评估点缺读数 ⇒ 不等 +3 立即补评（reason=final）', async () => {
    const c = freshCourse()
    const ckpt = backfeedWeights(c, 'run1', 20)
    const { calls, launch } = recorder()
    const admin = adminOf(
      { [c]: { run1: runView([20]) } },
      { [c]: { run1: resultView({ itEnd: 20, endItReached: true }) } },
    )
    const tick = await backfill(admin, { launch }).run()
    expect(tick.launched).toEqual({ course: c, run: 'run1', iter: 20, reason: 'final', ckpt })
    expect(calls).toEqual([{ course: c, ckpt, iter: 20 }])
  })

  it('只走交付镜像（无回传树）⇒ 用 mirror 权重路径补评', async () => {
    const c = freshCourse()
    const ckpt = mirrorWeights(c, 'run1', 5, 'mirror-only')
    const { calls, launch } = recorder()
    const tick = await backfill(adminOf({ [c]: { run1: runView([5, 8]) } }), { launch }).run()
    expect(tick.launched?.ckpt).toBe(ckpt)
    expect(calls).toEqual([{ course: c, ckpt, iter: 5 }])
  })

  it('权重还没回传 ⇒ 跳过（不启动、不算失败）', async () => {
    const c = freshCourse()
    const { calls, launch } = recorder()
    const tick = await backfill(adminOf({ [c]: { run1: runView([5, 7, 8]) } }), { launch }).run()
    expect(tick).toEqual({ skipped: null, launched: null })
    expect(calls).toEqual([])
  })

  it('evalA 忙 ⇒ 本拍什么都不做且不记退避（下一拍立刻可评）', async () => {
    const c = freshCourse()
    const ckpt = backfeedWeights(c, 'run1', 5)
    const { calls, launch } = recorder()
    let busyNow = true
    const bf = backfill(adminOf({ [c]: { run1: runView([5, 8]) } }), {
      launch,
      isEvalABusy: () => busyNow,
    })
    expect(await bf.run()).toEqual({ skipped: 'busy', launched: null })
    expect(calls).toEqual([])
    busyNow = false
    expect((await bf.run()).launched?.ckpt).toBe(ckpt)
    expect(calls).toEqual([{ course: c, ckpt, iter: 5 }])
  })

  it('启动失败 ⇒ 指数退避（未到点不再启动，到点重试）', async () => {
    const c = freshCourse()
    backfeedWeights(c, 'run1', 5)
    const { calls, launch } = recorder(true)
    let t = 1_000_000
    const bf = backfill(adminOf({ [c]: { run1: runView([5, 8]) } }), { launch, now: () => t })
    expect(await bf.run()).toEqual({ skipped: null, launched: null })
    expect(calls).toHaveLength(1)
    t += 60_000 // +1min：退避未到（10min）
    expect((await bf.run()).launched).toBeNull()
    expect(calls).toHaveLength(1)
    t += 10 * 60_000 // 越过第一次退避
    expect((await bf.run()).launched).toBeNull() // 仍然失败（fake 恒失败）
    expect(calls).toHaveLength(2)
  })

  it('逃生阀 BCITY_NO_OFFLINE_EVAL_BACKFILL ⇒ 恒不启动', async () => {
    const c = freshCourse()
    backfeedWeights(c, 'run1', 5)
    const { calls, launch } = recorder()
    process.env.BCITY_NO_OFFLINE_EVAL_BACKFILL = '1'
    try {
      const tick = await backfill(adminOf({ [c]: { run1: runView([5, 8]) } }), { launch }).run()
      expect(tick).toEqual({ skipped: 'disabled', launched: null })
      expect(calls).toEqual([])
    } finally {
      delete process.env.BCITY_NO_OFFLINE_EVAL_BACKFILL
    }
  })

  it('hub 不可达 ⇒ 跳过', async () => {
    const { calls, launch } = recorder()
    const tick = await backfill(null, { launch }).run()
    expect(tick).toEqual({ skipped: 'no-admin', launched: null })
    expect(calls).toEqual([])
  })

  it('课程无语料（eval_games_per_stage=0）⇒ 整门跳过', async () => {
    const c = freshCourse({ eval_games_per_stage: 0 })
    backfeedWeights(c, 'run1', 5)
    const { calls, launch } = recorder()
    const tick = await backfill(adminOf({ [c]: { run1: runView([5, 8]) } }), { launch }).run()
    expect(tick.launched).toBeNull()
    expect(calls).toEqual([])
  })
})

// ────────────────────────── 接线守卫 + 默认 hub 链路 ──────────────────────────

describe('接线', () => {
  it('server.ts：60s ticker + unref + 启动先跑一拍', () => {
    const src = readFileSync(
      path.join(import.meta.dir, '..', 'src', 'server', 'server.ts'),
      'utf-8',
    )
    expect(src).toContain('OFFLINE_EVAL_BACKFILL_INTERVAL_MS')
    expect(src).toContain('runOfflineEvalBackfill()')
    expect(src).toContain('offlineBackfillTimer.unref')
  })
})

/** 假 hub（只回固定 JSON）；只验控制台读法。 */
function fakeHub(opts: { queue: unknown; offline: unknown }): { url: string; stop: () => void } {
  const srv = Bun.serve({
    port: 0,
    fetch(req) {
      const p = new URL(req.url).pathname
      if (p === '/admin/queue') return Response.json(opts.queue)
      if (p === '/admin/push-workers')
        return Response.json({ dispatcher: {}, registry: { workers: [] } })
      if (p === '/admin/offline') return Response.json(opts.offline)
      return new Response('{}', { status: 404 })
    },
  })
  return { url: `http://127.0.0.1:${srv.port}`, stop: () => srv.stop(true) }
}

const SCRATCH: string[] = []
afterAll(() => {
  for (const d of SCRATCH) rmSync(d, { recursive: true, force: true })
})

/** 让控制台把基址解析到假 hub：账本里放一条活着的 hub 条目（pid = 本进程）。 */
function withLiveHub(url: string): () => void {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-ob-hub-'))
  SCRATCH.push(dir)
  const file = path.join(dir, 'registry.json')
  writeFileSync(file, JSON.stringify({ hubServers: { c: { pid: process.pid, course: 'c', url } } }))
  const prev = process.env.BCITY_REGISTRY_FILE
  process.env.BCITY_REGISTRY_FILE = file
  return () => {
    if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
    else process.env.BCITY_REGISTRY_FILE = prev
  }
}

afterEach(() => {
  api.invalidateHubAdmin()
})

describe('默认 hub 链路（overview.offlineResults 行为级验收）', () => {
  it('/admin/offline.results 的收官判据经 getHubAdmin 直达补评（零新增网络）', async () => {
    const c = freshCourse()
    const ckpt = backfeedWeights(c, 'run1', 20)
    const hub = fakeHub({
      queue: { courses: { [c]: { mode: 'offline', pending_n: 0, inflight: [] } }, order: [c] },
      offline: {
        progress: { [c]: { run1: { its: [20], count: 1, last_mtime: 0 } } },
        results: {
          [c]: { run1: { it_end: 20, state: 'complete', end_it_reached: true, received_at: 1 } },
        },
        stalled: [],
      },
    })
    const restore = withLiveHub(hub.url)
    try {
      api.invalidateHubAdmin()
      const cfg = loadConfig()
      expect(cfg.rl?.remote_token).toBeTruthy() // 假 hub 不管鉴权，但请求要带 token（同线上形状）
      const { calls, launch } = recorder()
      // 这里**不注入** fetchOfflineAdmin：走生产缺省（loadConfig + getHubAdmin）
      const bf = createOfflineEvalBackfill({
        tmpRoot: TMP,
        curriculaRoot: CUR,
        launch,
        isEvalABusy: () => false,
        logLine: () => {},
        logWarn: () => {},
      })
      const tick = await bf.run()
      expect(tick.launched).toEqual({ course: c, run: 'run1', iter: 20, reason: 'final', ckpt })
      expect(calls).toEqual([{ course: c, ckpt, iter: 20 }])
    } finally {
      restore()
      hub.stop()
    }
  })
})
