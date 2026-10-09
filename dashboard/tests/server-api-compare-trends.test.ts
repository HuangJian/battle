/**
 * server-api-compare-trends.test.ts — 比较课程端点：参数校验 / iter 切片 / null-vs-0 / 缓存指纹。
 *
 * 夹具纪律（照 `console-fixture.ts` 的重定向惯例）：**env 必须在被测模块 import 之前设好**——
 * `core/paths.ts` 的 `tmpLogsDir()`/`curriculaDir()` 是惰性取值，端点读账本也走它们
 * （plan §1.3 的可测性纪律：写死 `REPO_ROOT/tmp` 的旧端点就做不到这件事）。
 * 课程夹具 = 两件套：`curricula/<课>.jsonc`（`sanitizeViewCourse` 的存在性第二支，可重定向）
 * + `tmpLogs/<课>/training_log.jsonl`（账本）。
 *
 * 用真 JSONL（不是 mock）：`readIterMetrics` → `metricSeries` 这条链是**被测行为的主体**，
 * 打桩它就等于只测了自己写的桩。
 */

import { afterAll, beforeEach, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-cmp-'))
const logs = path.join(scratch, 'logs')
const curricula = path.join(scratch, 'curricula')
mkdirSync(logs, { recursive: true })
mkdirSync(curricula, { recursive: true })
process.env.BCITY_TMP_LOGS_DIR = logs
process.env.BCITY_CURRICULA_DIR = curricula

const api = await import('../src/server/api')

afterAll(() => {
  rmSync(scratch, { recursive: true, force: true })
})

beforeEach(() => {
  api.__clearCompareCache()
})

/** 课程声明（`sanitizeViewCourse` 的第二支：`curricula/<课>.jsonc`）。 */
function declare(name: string): void {
  writeFileSync(path.join(curricula, `${name}.jsonc`), '{}')
}

function writeLog(name: string, lines: Array<Record<string, unknown>>): void {
  mkdirSync(path.join(logs, name), { recursive: true })
  writeFileSync(
    path.join(logs, name, 'training_log.jsonl'),
    `${lines.map((l) => JSON.stringify(l)).join('\n')}\n`,
  )
}

const iterLine = (iter: number, winRate: number): Record<string, unknown> => ({
  event: 'iteration',
  iter,
  time: `t${iter}`,
  winRate,
})

function mkCourse(name: string, iters: number[], wr: (it: number) => number): void {
  declare(name)
  writeLog(
    name,
    iters.map((it) => iterLine(it, wr(it))),
  )
}

const ok = <T>(r: { ok: true; params: T } | { ok: false; message: string }): T => {
  if (!r.ok) throw new Error(`解析失败：${r.message}`)
  return r.params
}

describe('parseCompareQuery（参数校验）', () => {
  it('非法/不存在的课名被剔除；剔空 ⇒ 400（不静默回退到别的课）', () => {
    mkCourse('cmp-a', [1, 2], () => 0.5)
    expect(api.parseCompareQuery({}).ok).toBe(false)
    expect(api.parseCompareQuery({ courses: '../etc' }).ok).toBe(false)
    expect(api.parseCompareQuery({ courses: 'ghost-course' }).ok).toBe(false)
    // 混合：非法被剔、合法保留（网络上手拼 URL 的常态）
    expect(ok(api.parseCompareQuery({ courses: '../etc,cmp-a' })).courses).toEqual(['cmp-a'])
  })

  it('metric / source 缺席 = 缺省；给了但非法 ⇒ 400（两条语义分开）', () => {
    mkCourse('cmp-a2', [1], () => 0.5)
    const d = ok(api.parseCompareQuery({ courses: 'cmp-a2' }))
    expect(d.metric).toBe('winRate')
    expect(d.source).toBe('all')
    expect(api.parseCompareQuery({ courses: 'cmp-a2', metric: 'nope' })).toEqual({
      ok: false,
      message: 'metric 非法',
    })
    expect(api.parseCompareQuery({ courses: 'cmp-a2', source: 'both' })).toEqual({
      ok: false,
      message: 'source 非法',
    })
  })

  it('from/to：非负整数；from > to ⇒ 400', () => {
    mkCourse('cmp-a3', [1], () => 0.5)
    expect(api.parseCompareQuery({ courses: 'cmp-a3', from: 'x' }).ok).toBe(false)
    expect(api.parseCompareQuery({ courses: 'cmp-a3', from: '-1' }).ok).toBe(false)
    expect(api.parseCompareQuery({ courses: 'cmp-a3', from: '5', to: '2' }).ok).toBe(false)
    const r = ok(api.parseCompareQuery({ courses: 'cmp-a3', from: '2', to: '' }))
    expect({ from: r.from, to: r.to }).toEqual({ from: 2, to: null })
  })

  it('超过 8 门取前 8（上限是 UI 约束，不是错误）', () => {
    const names = Array.from({ length: 9 }, (_, i) => `cmp-cap${i + 1}`)
    for (const n of names) declare(n)
    const p = ok(api.parseCompareQuery({ courses: names.join(',') }))
    expect(p.courses).toEqual(names.slice(0, 8))
  })

  it('去重（同一门课写两遍不占两个色位）', () => {
    declare('cmp-dup')
    expect(ok(api.parseCompareQuery({ courses: 'cmp-dup,cmp-dup' })).courses).toEqual(['cmp-dup'])
  })
})

describe('buildCompareTrendsView（数据面）', () => {
  it('各课带自己的 iter 网格；from/to 只回该区间', () => {
    mkCourse('cmp-a', [1, 2, 3, 4, 5], (it) => it / 10)
    mkCourse('cmp-b', [50, 51, 52], () => 0.5)
    const view = api.buildCompareTrendsView(
      ok(api.parseCompareQuery({ courses: 'cmp-a,cmp-b', metric: 'winRate', source: 'rollout' })),
    )
    expect(view.courses.map((c) => c.course)).toEqual(['cmp-a', 'cmp-b'])
    expect(view.courses[0]!.series[0]!.iters).toEqual([1, 2, 3, 4, 5])
    expect(view.courses[1]!.series[0]!.iters).toEqual([50, 51, 52])
    expect(view.courses[0]!.points).toBe(5)
    const sliced = api.buildCompareTrendsView(
      ok(
        api.parseCompareQuery({
          courses: 'cmp-a',
          metric: 'winRate',
          source: 'rollout',
          from: '2',
          to: '4',
        }),
      ),
    )
    expect(sliced.courses[0]!.series[0]!.iters).toEqual([2, 3, 4])
    expect(sliced.courses[0]!.series[0]!.vals).toEqual([0.2, 0.3, 0.4])
    expect(sliced.courses[0]!.points).toBe(3)
  })

  it('eval 缺失 = null（不是 0——0 会冒充一个真读数）', () => {
    mkCourse('cmp-a', [1, 2, 3], () => 0.5)
    writeLog('cmp-b', [iterLine(1, 0.3), iterLine(2, 0.4)])
    declare('cmp-b')
    writeFileSync(
      path.join(logs, 'cmp-b', 'eval_log.jsonl'),
      `${JSON.stringify({
        event: 'eval_summary',
        iter: 2,
        time: 'e2',
        wver: 'w1',
        winRate: 0.42,
        games: 4,
        wins: 2,
      })}\n`,
    )
    const view = api.buildCompareTrendsView(
      ok(api.parseCompareQuery({ courses: 'cmp-a,cmp-b', metric: 'winRate', source: 'all' })),
    )
    const a = view.courses[0]!
    const b = view.courses[1]!
    expect(a.series.map((s) => s.key)).toEqual(['winRate', 'eval'])
    expect(a.series[1]!.vals).toEqual([null, null, null]) // 无 eval 账本 ⇒ 全 null
    expect(b.series[1]!.vals).toEqual([null, 0.42]) // 有评估的轮给真值，其余 null
    expect(b.points).toBe(3) // 2 主口径 + 1 eval 有效点
  })

  it('source 过滤：rollout/eval 只回一条，且是真序列（不是截断的空壳）', () => {
    mkCourse('cmp-a', [1, 2], () => 0.5)
    const evalOnly = api.buildCompareTrendsView(
      ok(api.parseCompareQuery({ courses: 'cmp-a', metric: 'kills', source: 'eval' })),
    )
    expect(evalOnly.courses[0]!.series.map((s) => s.key)).toEqual(['evalKills'])
    const rolloutOnly = api.buildCompareTrendsView(
      ok(api.parseCompareQuery({ courses: 'cmp-a', metric: 'kills', source: 'rollout' })),
    )
    expect(rolloutOnly.courses[0]!.series.map((s) => s.key)).toEqual(['kills'])
  })

  it('无账本课进 unavailable（图不画它，图例下方给原因）；其余课照常', () => {
    mkCourse('cmp-a', [1, 2], () => 0.5)
    declare('cmp-empty')
    mkdirSync(path.join(logs, 'cmp-empty'), { recursive: true }) // 目录在，账本不在
    const view = api.buildCompareTrendsView(
      ok(
        api.parseCompareQuery({ courses: 'cmp-a,cmp-empty', metric: 'winRate', source: 'rollout' }),
      ),
    )
    expect(view.unavailable).toEqual([{ course: 'cmp-empty', reason: '无账本' }])
    expect(view.courses.map((c) => c.course)).toEqual(['cmp-a'])
    // 不可用课也要进指纹（它一开账本，指纹就得变——否则客户端门闩会把它挡在外面）
    expect(view.fingerprint).toContain('cmp-empty')
  })
})

describe('课程级缓存（TTL + 指纹）', () => {
  it('同指纹命中不重读；内容变 ⇒ 重读；越过 TTL ⇒ 重读（计数可证）', () => {
    mkCourse('cmp-a', [1, 2, 3, 4, 5], (it) => it / 10)
    const p = ok(api.parseCompareQuery({ courses: 'cmp-a', metric: 'winRate', source: 'rollout' }))
    const now = Date.now()
    const v1 = api.buildCompareTrendsView(p, now)
    expect(api.__compareCacheSize()).toBe(1)
    expect(api.__compareCacheCounters()).toEqual({ hits: 0, misses: 1 })
    const v2 = api.buildCompareTrendsView(p, now + 1_000)
    expect(api.__compareCacheCounters()).toEqual({ hits: 1, misses: 1 })
    expect(v2.courses[0]!.series[0]!.vals).toEqual(v1.courses[0]!.series[0]!.vals)
    expect(v2.fingerprint).toBe(v1.fingerprint)
    // 账本变小（指纹含 size，必变）⇒ 不命中，读新值
    writeLog('cmp-a', [iterLine(1, 0.9)])
    const v3 = api.buildCompareTrendsView(p, now + 2_000)
    expect(api.__compareCacheCounters()).toEqual({ hits: 1, misses: 2 })
    expect(v3.courses[0]!.series[0]!.vals).toEqual([0.9])
    expect(v3.fingerprint).not.toBe(v1.fingerprint)
    // 内容没变但 TTL 过期（注入 now）⇒ 重读
    api.buildCompareTrendsView(p, now + 60_000)
    expect(api.__compareCacheCounters()).toEqual({ hits: 1, misses: 3 })
  })

  it('指纹含课程名：两门课同内容也不是同一个指纹（客户端按它判「有没有变」）', () => {
    mkCourse('cmp-x', [1], () => 0.5)
    mkCourse('cmp-y', [1], () => 0.5)
    const a = api.buildCompareTrendsView(
      ok(api.parseCompareQuery({ courses: 'cmp-x', metric: 'winRate', source: 'rollout' })),
    )
    const b = api.buildCompareTrendsView(
      ok(api.parseCompareQuery({ courses: 'cmp-y', metric: 'winRate', source: 'rollout' })),
    )
    expect(a.fingerprint).not.toBe(b.fingerprint)
  })
})
