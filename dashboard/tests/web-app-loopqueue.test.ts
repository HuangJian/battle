/**
 * web-app-loopqueue.test.ts — 训练调度器卡片（单例）SSR（R2c-3）
 *
 * 分层：src/web/app/panels/LoopQueue.tsx（SSR，无 DOM）
 *
 * 断言的是「操作员能从屏幕上读出什么」：每课一行（在训/未在训、指针、下一步、待办深度）、
 * **在等什么**四态可辨、资源池与排队事实、读失败显因不静默。
 * 动作接线（点行切课程、app.tsx 挂载）用**源码接线断言**兜底：SSR 渲染不出点击。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { STOPPED_TITLE } from '../src/web/app/panels/LoopQueue'
import {
  type LoopQueueView,
  parseLoopQueue,
  pauseBadge,
  pauseLabel,
  pauseState,
  pauseTitle,
  withPausedFacts,
  withTraining,
} from '../src/web/view'

/** 一课的原始行（形状与 run_rl_cluster.build_rows 一致，便于按需改）。 */
function row(patch: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    course: 'c4-dodge',
    it: 37,
    state: 'ready',
    current: 'ppo',
    pending: ['ppo', 'eval_join'],
    inflight: [],
    facts: { iterations: 36, games_settled: 150, games_planned: 0 },
    waiting: { kind: 'ready', text: '无外部等待，下一步 ppo' },
    ...patch,
  }
}

/** 组装一份视图（走真实解析 + 合并路径，不手拼 LoopQueueView）。 */
function queue(
  raw: Record<string, unknown>[] = [row()],
  training: string[] = [],
  pause: { intent?: string[]; applied?: string[] } = {},
): LoopQueueView {
  const v = parseLoopQueue({
    courses: raw,
    pools: { local_ppo: { held: 1, capacity: 1 }, eval_local: { held: 0, capacity: 1 } },
  })!
  return withPausedFacts(withTraining(v, training), pause.intent ?? [], pause.applied ?? [])
}

async function render(
  q: LoopQueueView | null,
  course = 'c4-dodge',
  onAction?: (act: string, body: Record<string, unknown>) => void,
): Promise<string> {
  const { LoopQueue } = await import('../src/web/app/panels/LoopQueue')
  return renderToString(h(LoopQueue, { loopQueue: q, course, onSelectCourse: () => {}, onAction }))
}

describe('LoopQueue（训练调度器·单例）', () => {
  it('每课一行：在训/未在训、指针、下一步、待办深度，一切都有', async () => {
    const html = await render(
      queue([row(), row({ course: 'c5-tick', it: 8, current: '', pending: [] })], ['c4-dodge']),
    )
    expect(html).toContain('tc-loopq__singleton')
    expect(html).toContain('单例')
    expect(html).toContain('在训 1/2')
    expect(html).toContain('tc-loopq__badge--on') // c4 在训
    expect(html).toContain('tc-loopq__badge--idle') // c5 未在训
    expect(html).toContain('it37')
    expect(html).toContain('ppo')
    expect(html).toContain('待办 2')
    expect(html).toContain('local_ppo 1/1')
  })

  it('「在等什么」四态各自有修饰类：等回传 / 采集中 / 无待办 / 可直接推进', async () => {
    const html = await render(
      queue(
        [
          row({
            course: 'a',
            inflight: [{ phase: 'ppo', round: '37', jid: 'job-1', dispatch: 'push' }],
            waiting: { kind: 'inflight', text: '等远端回传：ppo@37（jid=job-1 via push）' },
          }),
          row({ course: 'b', waiting: { kind: 'collect', text: '采集中：已落 78 局' } }),
          row({ course: 'c', pending: [], waiting: { kind: 'idle', text: '本轮无待办' } }),
          row({ course: 'd', waiting: { kind: 'ready', text: '无外部等待，下一步 ppo' } }),
        ],
        ['a', 'b', 'c', 'd'],
      ),
    )
    expect(html).toContain('tc-loopq__wait--inflight')
    expect(html).toContain('tc-loopq__wait--collect')
    expect(html).toContain('tc-loopq__wait--idle')
    expect(html).toContain('等远端回传：ppo@37（jid=job-1 via push）')
    expect(html).toContain('采集中：已落 78 局')
    // ready 不加色（它是常态）——句子里必须有下一步是谁
    expect(html).toContain('无外部等待，下一步 ppo')
    // 表头汇总等回传的**在训**课数（未在训的不算「在等外部」）
    expect(html).toContain('1 课等回传')
  })

  it('未在训的行：淡一档 + 悬停说明「这是盘上事实推出的队列状态」', async () => {
    const html = await render(queue([row()], []))
    expect(html).toContain('tc-loopq__row--stopped')
    expect(html).toContain(STOPPED_TITLE)
    expect(html).toContain('未在训')
  })

  it('资源池排队与页脚：等票不是卡死；有课在等外部时点名它', async () => {
    const html = await render(
      queue(
        [
          row({
            inflight: [{ phase: 'ppo', round: '37', jid: 'job-abcdef123456', dispatch: 'push' }],
            waiting: { kind: 'inflight', text: '等远端回传：ppo@37' },
          }),
        ],
        ['c4-dodge'],
      ),
    )
    expect(html).toContain('正在等 ppo@37')
    expect(html).toContain('jid=job-abcdef12')
    expect(html).toContain('其余课照常推进')
  })

  it('排队等资源：表头点名被池挡住的课 + 页脚说明容量 1 的语义', async () => {
    const v = queue([row()], ['c4-dodge'])
    const html = await render({ ...v, blockedCourses: ['c4-dodge'] })
    expect(html).toContain('排队等资源：c4-dodge')
    expect(html).toContain('本机重资源跨课排队（容量 1）')
  })

  it('读失败：显因不静默；有缓存时说明「上一拍读失败」', async () => {
    const failed: LoopQueueView = {
      blockedCourses: [],
      pools: {},
      rows: [],
      trainingCount: 0,
      error: 'run_rl_cluster.py 退出码 1：ModuleNotFoundError',
    }
    const empty = await render(failed)
    expect(empty).toContain('只读视图不可用')
    expect(empty).toContain('ModuleNotFoundError')
    const stale = await render({ ...queue([row()], ['c4-dodge']), error: '超时' })
    expect(stale).toContain('上一拍读失败')
    expect(stale).toContain('超时')
  })

  it('无数据 / 无行 → 不渲染（没东西可说时不留空壳）', async () => {
    expect(await render(null)).toBe('')
    expect(await render(queue([]))).toBe('')
  })

  it('行是按钮（切到查看该课），当前查看那一行带 --cur + aria-current', async () => {
    const html = await render(
      queue([row(), row({ course: 'c5-tick' })], ['c4-dodge', 'c5-tick']),
      'c5-tick',
    )
    expect((html.match(/<button type="button" class="tc-loopq__row/g) ?? []).length).toBe(2)
    expect(html).toContain('tc-loopq__row tc-loopq__row--cur')
    expect(html).toContain('aria-current="true"')
    expect(html).toContain('切到查看 c4-dodge')
  })
})

// ────────────────────────── 暂停/恢复（意图 × 事实） ──────────────────────────

describe('暂停态：意图与事实分开（四态）', () => {
  const one = (intent: boolean, applied: boolean) => {
    const r = queue([row()], ['c4-dodge'], {
      intent: intent ? ['c4-dodge'] : [],
      applied: applied ? ['c4-dodge'] : [],
    }).rows[0]!
    return r
  }

  it('四种组合各是一个真实局面（running / pending / paused / resuming）', () => {
    expect(pauseState(one(false, false))).toBe('running')
    expect(pauseState(one(true, false))).toBe('pending')
    expect(pauseState(one(true, true))).toBe('paused')
    expect(pauseState(one(false, true))).toBe('resuming')
  })

  it('按钮方向由**意图**定：点了暂停未生效时说「取消暂停」，已生效说「恢复」', () => {
    expect(pauseLabel(one(false, false))).toBe('暂停')
    expect(pauseLabel(one(true, false))).toBe('取消暂停')
    expect(pauseLabel(one(true, true))).toBe('恢复')
  })

  it('待生效的悬停解释分两种：进程没跑 vs 还没轮到读（不能一句「处理中」糊过去）', () => {
    const live = queue([row()], ['c4-dodge'], { intent: ['c4-dodge'] }).rows[0]!
    expect(pauseTitle(live)).toContain('每拍读一次')
    const dead = queue([row()], [], { intent: ['c4-dodge'] }).rows[0]!
    expect(pauseTitle(dead)).toContain('没有存活的 trainingLoop 进程')
  })

  it('徽标只在「意图 ≠ 事实」或「已暂停」时出现（没事可说的行不挂徽标）', () => {
    expect(pauseBadge(one(false, false))).toBeNull()
    expect(pauseBadge(one(true, true))!.text).toBe('已暂停')
    expect(pauseBadge(one(true, false))!.text).toBe('待生效')
    expect(pauseBadge(one(false, true))!.text).toBe('恢复中')
  })

  it('意图只作用于被点名的课（别的行不受污染）', () => {
    const v = queue([row(), row({ course: 'c5-tick' })], ['c4-dodge', 'c5-tick'], {
      intent: ['c4-dodge'],
      applied: ['c4-dodge'],
    })
    expect(v.rows.map((r) => [r.course, r.pausedIntent, r.pauseApplied])).toEqual([
      ['c4-dodge', true, true],
      ['c5-tick', false, false],
    ])
  })
})

describe('SSR：暂停按钮与事实徽标', () => {
  it('有动作能力才渲染按钮；关掉时一个都不多渲染（LAN 只读不假装能控）', async () => {
    const withAct = await render(queue(), 'c4-dodge', () => {})
    expect(withAct).toContain('tc-loopq__pausebtn')
    expect(withAct).toContain('暂停')
    expect(await render(queue())).not.toContain('tc-loopq__pausebtn')
  })

  it('已暂停的行：按钮「恢复」+ 徽标「已暂停」（事实由训练进程回执给）', async () => {
    const html = await render(
      queue([row()], ['c4-dodge'], { intent: ['c4-dodge'], applied: ['c4-dodge'] }),
      'c4-dodge',
      () => {},
    )
    expect(html).toContain('恢复')
    expect(html).toContain('tc-loopq__pause--on')
    expect(html).toContain('已暂停')
  })

  it('只写了意图、尚未生效：按钮「取消暂停」+ 徽标「待生效」（虚线，不得与已暂停同色）', async () => {
    const html = await render(
      queue([row()], ['c4-dodge'], { intent: ['c4-dodge'] }),
      'c4-dodge',
      () => {},
    )
    expect(html).toContain('取消暂停')
    expect(html).toContain('tc-loopq__pause--pending')
    expect(html).toContain('待生效')
    expect(html).not.toContain('tc-loopq__pause--on')
  })
})

// ────────────────────────── BC 课与 RL 课并列（R3-4） ──────────────────────────

describe('BC 行与 RL 行并列在同一张卡里', () => {
  /** BC 课的一行：**单个轮任务** + 账本 `job_pending` 推出的在飞（与 python 同形）。 */
  const bcRow = (patch: Record<string, unknown> = {}) =>
    row({
      course: 'bc-c4-v3',
      kind: 'bc',
      it: 2,
      current: 'round',
      pending: ['round'],
      inflight: [{ phase: 'bc', round: '2', jid: 'job-bc-123456789', dispatch: 'hubpush' }],
      waiting: { kind: 'inflight', text: '等远端回传：bc@2（jid=job-bc-123456789 via hubpush）' },
      ...patch,
    })

  it('BC 行上屏「在等哪个 GPU job 回传」，RL 行照旧（并列而不是互相冒充）', async () => {
    const html = await render(queue([bcRow(), row()], ['bc-c4-v3', 'c4-dodge']))
    // BC 行：种类徽标 + 在等回传（这是它存在的理由）
    expect(html).toContain('tc-loopq__kind--bc')
    expect(html).toContain('等远端回传：bc@2（jid=job-bc-123456789 via hubpush）')
    expect(html).toContain('tc-loopq__wait--inflight')
    // RL 行不受影响：没有 BC 徽标（列表里只有一行是 BC）
    expect(html.match(/tc-loopq__kind--bc/g)).toHaveLength(1)
    // 页脚点名在等回传的那门课（BC 的 phase 就是 bc）
    expect(html).toContain('正在等 bc@2')
  })

  it('BC 行的悬停说清「一轮 = 一个任务」（不说「步」，也不提不存在的 verdict/KL）', async () => {
    const html = await render(queue([bcRow()], ['bc-c4-v3']))
    expect(html).toContain('BC 课一轮 = 一个任务')
    expect(html).toContain('跑完这一轮 BC')
    expect(html).toContain('bc_epoch')
    expect(html).not.toContain('13 步表是 RL 的') // 正面口径在 tooltip 里，不是给 RL 用的那句
  })

  it('RL 行的悬停保持原样（13 步顺序即依赖顺序）', async () => {
    const html = await render(queue([row()], ['c4-dodge']))
    expect(html).toContain('待办 2 步（顺序即依赖顺序）：ppo → eval_join')
    expect(html).toContain('下一步：ppo')
    expect(html).not.toContain('tc-loopq__kind--bc')
  })
})

// ────────────────────────── 接线（SSR 渲染不出点击，用源码断言兜底） ──────────────────────────

describe('接线：卡片挂载与视图字段同源', () => {
  const app = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'app.tsx'), 'utf-8')
  const panel = readFileSync(
    path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', 'LoopQueue.tsx'),
    'utf-8',
  )
  const types = readFileSync(
    path.join(DASHBOARD_ROOT, 'src', 'web', 'view', 'console-types.ts'),
    'utf-8',
  )

  it('app.tsx 挂了 <LoopQueue> 且与总览卡同一条切课路径', () => {
    expect(app).toContain('<LoopQueue')
    expect(app).toContain('loopQueue={stateView?.loopQueue ?? null}')
    expect(app).toContain('onSelectCourse={selectCourse}')
  })

  it('卡片是**跨课程**卡（BC 课也出）：它一次列出所有课，不该被「当前查看的课是 BC」门控', () => {
    // 这条曾经是**反的**（`isBc ? null` 门控）：后果是选中一门 BC 课 ⇒ 整张卡片消失，
    // 于是 BC 课在调度器视图里根本不存在——而 BC 课正是最需要看「在等哪个 GPU job 回传」的。
    // 判据用正则（只看结构，不受注释/缩进漂移影响）：门控包裹 `<LoopQueue>` 的那个形状不该再有。
    expect(app).not.toMatch(/isBc\s*\?\s*null\s*:\s*\(\s*<PanelErrorBoundary>\s*<LoopQueue/)
    expect(app).toContain('loopQueue={stateView?.loopQueue ?? null}')
  })

  it('视图类型带 loopQueue（服务端填、客户端读，缺失即空态）', () => {
    expect(types).toContain('loopQueue?: LoopQueueView | null')
  })

  it('面板不直连服务端（分层铁律由 architecture-layering 用例兜底，这里挡住「顺手 import」）', () => {
    expect(panel).not.toContain('server/')
  })

  it('app.tsx 把动作派发交给卡片（不传则只有只读徽标——操作面在 LAN 只读下自动消失）', () => {
    expect(app).toContain('onAction={doAction}')
  })

  it('动作走 route 表的 setCoursePaused，且动作后作废调度器缓存（否则要等 TTL 才上屏）', () => {
    const route = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'api', 'route.ts'),
      'utf-8',
    )
    const server = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'server', 'server.ts'), 'utf-8')
    expect(route).toContain("case 'setCoursePaused'")
    expect(server).toContain('invalidateLoopQueue()')
  })
})
