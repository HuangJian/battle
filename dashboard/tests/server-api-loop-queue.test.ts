/**
 * server-api-loop-queue.test.ts — 训练调度器每课队列视图的读取面（R2c-3）
 *
 * 分层：src/web/view/loop-queue.ts（纯解析/合并）+ src/server/api/loop-queue.ts（子进程 + TTL）
 *
 * **不跑 python**：`LoopQueueRunner` 是可注入接缝（与 `deliver_zip` 导入同惯例）——
 * python 那一侧的判据（`waiting_state` 的取值域、`build_rows` 的真读盘）由
 * `nn-training/tests/test_loop_plan_waiting.py` 钉死，这边只测控制台这一半：
 * argv 形状 / 结果翻译的每条失败分支 / TTL 与单飞 / 与 registry 在训事实的合并。
 */

import { afterEach, describe, expect, it } from 'bun:test'
import { api, view } from './helpers/console-fixture'

/** python 的 `--json` 输出（形状与 run_rl_cluster.build_rows 逐字段一致）。 */
const JSON_OUT = {
  courses: [
    {
      course: 'c4-dodge',
      it: 37,
      state: 'ready',
      current: 'ppo',
      pending: ['ppo', 'export_weights', 'eval_join'],
      inflight: [{ phase: 'ppo', round: '37', jid: 'job-abcdef123456', dispatch: 'push' }],
      facts: {
        it: 37,
        iteration_recorded: false,
        games_settled: 150,
        games_planned: 0,
        weights_landed: false,
        iterations: 36,
        last_verdict: 'OK',
        train_sec_total: 1234.5,
        soft_remediate_count: 1,
        kl_streak: 0,
      },
      waiting: { kind: 'inflight', text: '等远端回传：ppo@37（jid=job-abcdef12 via push）' },
    },
    {
      course: 'c5-tick',
      it: 8,
      state: 'done',
      current: '',
      pending: [],
      inflight: [],
      facts: { iterations: 8, last_verdict: 'OK', train_sec_total: 10, games_settled: 0 },
      waiting: { kind: 'idle', text: '本轮无待办（账本已结算 / 未开训）' },
    },
  ],
  pools: { local_ppo: { held: 1, capacity: 1 }, eval_local: { held: 0, capacity: 1 } },
}

function ok(stdout: string) {
  return { code: 0, stdout, stderr: '', timeout: false }
}

// 模块级缓存（TTL + 单飞）在用例之间必须归零：否则一个用例暖的假缓存会喂给下一个。
afterEach(() => {
  api.invalidateLoopQueue()
})

// ────────────────────────── 纯函数：解析 ──────────────────────────

describe('parseLoopQueue（--json 的宽容解析）', () => {
  it('归一化：轮次/在飞集/资源池/在等什么，字段名与 python 一致', () => {
    const v = view.parseLoopQueue(JSON_OUT)!
    expect(v.rows.map((r) => r.course)).toEqual(['c4-dodge', 'c5-tick'])
    const r = v.rows[0]!
    expect(r.it).toBe(37)
    expect(r.state).toBe('ready')
    expect(r.current).toBe('ppo')
    expect(r.pending).toEqual(['ppo', 'export_weights', 'eval_join'])
    expect(r.inflight[0]).toMatchObject({ phase: 'ppo', round: '37', jid: 'job-abcdef123456' })
    expect(r.facts).toMatchObject({ iterations: 36, gamesSettled: 150, lastVerdict: 'OK' })
    // 「在等什么」整句**逐字保留**（那是卡片唯一的产出，不得被 TS 重写）
    expect(r.waiting).toEqual({
      kind: 'inflight',
      text: '等远端回传：ppo@37（jid=job-abcdef12 via push）',
    })
    expect(v.rows[1]!.waiting.kind).toBe('idle')
    expect(v.pools.local_ppo).toEqual({ held: 1, capacity: 1 })
    // 解析层不知道进程状态：在训由服务端用 registry 事实补
    expect(v.rows.every((r) => r.training === false)).toBe(true)
  })

  it('未知 wait kind → 退化成 ready 的显示语义，但保留文案（python 侧新增一类不该丢句）', () => {
    const v = view.parseLoopQueue({
      courses: [{ course: 'x', waiting: { kind: 'brand-new-kind', text: '新等待：…' } }],
    })!
    expect(v.rows[0]!.waiting.kind).toBe('ready')
    expect(v.rows[0]!.waiting.text).toBe('新等待：…')
  })

  it('课程种类 kind：缺省/未知一律按 rl 渲染（python 比控制台旧时不得凭空空贴 BC 标签）', () => {
    // 保守方向是**单侧**的：误判成 RL 只是少一个徽标；误判成 BC 会给一门真 RL 课贴上 BC 标签，
    // 并对外宣称「一轮 = 一个任务」（而它其实有 13 步）——假承诺比缺标签贵。
    expect(view.parseLoopQueue(JSON_OUT)!.rows.every((r) => r.kind === 'rl')).toBe(true)
    const bc = view.parseLoopQueue({ courses: [{ course: 'bc-x', kind: 'bc' }] })!.rows[0]!
    expect(bc.kind).toBe('bc')
    const weird = view.parseLoopQueue({ courses: [{ course: 'x', kind: 'BC' }] })!.rows[0]!
    expect(weird.kind).toBe('rl') // 大小写不符不是「大概也是 BC」（判据只认 python 的确切取值）
  })

  it('形状不符 / 字段缺失 → null 或缺省（python 比控制台新一版不该把整页带崩）', () => {
    expect(view.parseLoopQueue(null)).toBeNull()
    expect(view.parseLoopQueue('nope')).toBeNull()
    expect(view.parseLoopQueue({})).toBeNull() // 无 courses 数组 = 不是这个入口
    const v = view.parseLoopQueue({ courses: [{}] })!
    expect(v.rows).toEqual([]) // 无课程名的行直接丢（不是「一门课叫空串」）
    const one = view.parseLoopQueue({ courses: [{ course: 'x' }] })!.rows[0]!
    expect(one).toMatchObject({ it: 0, state: 'ready', current: '', pending: [], inflight: [] })
    expect(one.waiting).toEqual({ kind: 'ready', text: '' })
    expect(one.facts.gamesSettled).toBe(0)
  })
})

// ────────────────────────── 纯函数：在训事实合并 ──────────────────────────

describe('withTraining（python 事实 × registry 在训事实）', () => {
  it('逐行打上在训标记并统计在训数（缺了它会把「停了的课」读成「等外部」）', () => {
    const merged = view.withTraining(view.parseLoopQueue(JSON_OUT)!, ['c5-tick'])
    expect(merged.rows.map((r) => [r.course, r.training])).toEqual([
      ['c4-dodge', false],
      ['c5-tick', true],
    ])
    expect(merged.trainingCount).toBe(1)
  })
})

// ────────────────────────── 结果翻译（每条失败分支） ──────────────────────────

describe('viewFromRunResult（读失败不抛，进视图的 error）', () => {
  it('正常输出 → 视图；退出码非零 → 带最后一行 stderr 的 error', () => {
    expect(api.viewFromRunResult(ok(JSON.stringify(JSON_OUT))).rows.length).toBe(2)
    const bad = api.viewFromRunResult({
      code: 1,
      stdout: '',
      stderr: 'Traceback…\nModuleNotFoundError: No module named rl',
      timeout: false,
    })
    expect(bad.rows).toEqual([])
    expect(bad.error).toContain('退出码 1')
    expect(bad.error).toContain('ModuleNotFoundError')
  })

  it('超时与「输出不是 JSON」各自有独立文案（两者修法不同）', () => {
    expect(
      api.viewFromRunResult({ code: null, stdout: '', stderr: '', timeout: true }).error,
    ).toContain('超时')
    expect(api.viewFromRunResult(ok('<html>proxy error</html>')).error).toContain('不可解析')
    expect(api.viewFromRunResult(ok('{"nope":1}')).error).toContain('形状不符')
  })

  it('空 pid 等极端：退出码 null 不写成 undefined', () => {
    const v = api.viewFromRunResult({ code: null, stdout: '', stderr: '', timeout: false })
    expect(v.error).toContain('退出码 null')
  })
})

// ────────────────────────── TTL + 单飞 ──────────────────────────

describe('getLoopQueueView（懒算 + TTL + 单飞）', () => {
  it('TTL 内复用：两次调用只起一次子进程', async () => {
    let calls = 0
    const run = () => {
      calls += 1
      return ok(JSON.stringify(JSON_OUT))
    }
    const a = await api.getLoopQueueView(run)
    const b = await api.getLoopQueueView(run)
    expect(calls).toBe(1)
    expect(a).toBe(b) // 同一个对象（缓存命中，不是重算出来的等值物）
    api.invalidateLoopQueue()
    await api.getLoopQueueView(run)
    expect(calls).toBe(2)
  })

  it('并发调用共享同一次子进程（单飞）', async () => {
    let calls = 0
    const run = () => {
      calls += 1
      return ok(JSON.stringify(JSON_OUT))
    }
    const [a, b, c] = await Promise.all([
      api.getLoopQueueView(run),
      api.getLoopQueueView(run),
      api.getLoopQueueView(run),
    ])
    expect(calls).toBe(1)
    expect(a).toBe(b)
    expect(b).toBe(c)
  })

  it('执行体抛异常（解释器/venv 缺失）也当成读失败，不把请求路径炸掉', async () => {
    const v = await api.getLoopQueueView(() => {
      throw new Error('spawn python ENOENT')
    })
    expect(v.rows).toEqual([])
    expect(v.error).toContain('ENOENT')
  })
})

// ────────────────────────── 组装进 /api/state ──────────────────────────

describe('buildLoopQueueView / buildStateView 注入', () => {
  it('buildLoopQueueView 把在训事实并进每一行', async () => {
    api.invalidateLoopQueue()
    const v = await api.buildLoopQueueView(['c4-dodge'], () => ok(JSON.stringify(JSON_OUT)))
    expect(v.rows.find((r) => r.course === 'c4-dodge')!.training).toBe(true)
    expect(v.trainingCount).toBe(1)
  })

  it('buildLoopQueueView 把控制面事实（意图 + 生效回执）并进每一行', async () => {
    api.invalidateLoopQueue()
    const v = await api.buildLoopQueueView(['c5-tick'], () => ok(JSON.stringify(JSON_OUT)), {
      intent: ['c4-dodge', 'c5-tick'],
      applied: ['c4-dodge'],
    })
    expect(v.rows.map((r) => [r.course, r.pausedIntent, r.pauseApplied])).toEqual([
      ['c4-dodge', true, true], // 意图 + 已生效
      ['c5-tick', true, false], // 意图写了但训练进程还没施加
    ])
  })

  it('buildStateView 带上 loopQueue（且不因读失败 500）', async () => {
    // 先用自己的假执行体暖缓存：`buildStateView` 走缺省执行体，TTL 内读的是这份缓存
    // ——于是本用例既不真起 python，又能断言「服务端确实把它挂上去了」。
    api.invalidateLoopQueue()
    await api.getLoopQueueView(() => ok(JSON.stringify(JSON_OUT)))
    const s = await api.buildStateView()
    expect(s.loopQueue?.rows.map((r) => r.course)).toEqual(['c4-dodge', 'c5-tick'])
    // 在训列 = registry 事实（本用例机器上没有真在跑的 trainingLoop ⇒ 全 false）
    expect(s.loopQueue!.trainingCount).toBe(0)
    expect(Array.isArray(s.trainingCourses)).toBe(true)
  })

  it('读失败时 state 仍可用：loopQueue 带 error 而不是 null（UI 显因，不静默）', async () => {
    api.invalidateLoopQueue()
    await api.getLoopQueueView(() => ({ code: 3, stdout: '', stderr: 'boom', timeout: false }))
    const s = await api.buildStateView()
    expect(s.loopQueue?.error).toContain('boom')
    expect(s.loopQueue?.rows).toEqual([])
  })
})
