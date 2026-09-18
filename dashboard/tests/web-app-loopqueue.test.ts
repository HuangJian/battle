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
import { type LoopQueueView, parseLoopQueue, withTraining } from '../src/web/view'

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
function queue(raw: Record<string, unknown>[] = [row()], training: string[] = []): LoopQueueView {
  const v = parseLoopQueue({
    courses: raw,
    pools: { local_ppo: { held: 1, capacity: 1 }, eval_local: { held: 0, capacity: 1 } },
  })!
  return withTraining(v, training)
}

async function render(q: LoopQueueView | null, course = 'c4-dodge'): Promise<string> {
  const { LoopQueue } = await import('../src/web/app/panels/LoopQueue')
  return renderToString(h(LoopQueue, { loopQueue: q, course, onSelectCourse: () => {} }))
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

  it('卡片是 RL 区专属（BC 课不出：BC 没有 RL 训练循环）', () => {
    const i = app.indexOf('<LoopQueue')
    const guard = app.slice(Math.max(0, i - 300), i)
    expect(guard).toContain('stateView?.isBc ? null :')
  })

  it('视图类型带 loopQueue（服务端填、客户端读，缺失即空态）', () => {
    expect(types).toContain('loopQueue?: LoopQueueView | null')
  })

  it('面板不直连服务端（分层铁律由 architecture-layering 用例兜底，这里挡住「顺手 import」）', () => {
    expect(panel).not.toContain('server/')
  })
})
