/**
 * web-app-course-overview.test.ts — 并行课程总览 + push worker 登记面板（2026-09-18）
 *
 * 分层：src/web/app/panels/CourseOverview.tsx + WorkerRegistry.tsx（SSR，无 DOM）
 *
 * 断言的是「操作员能从屏幕上读出什么」：每门课一行四态可分辨、hub 行给出调度判据、
 * worker 行的两个探活列各自有记号、登记表单字段齐全且空值即禁用保存。
 * 动作接线（面板 → onAction → 路由）用**源码接线断言**兜底：SSR 渲染不出点击。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import type { ParallelOverviewView, PushWorkerRegistryView } from '../src/web/view'

const overview = (patch: Partial<ParallelOverviewView> = {}): ParallelOverviewView => ({
  hubUrl: 'http://127.0.0.1:18787',
  hubOnline: true,
  raceActive: false,
  activeCourses: 2,
  activeWorkers: 3,
  halt: false,
  recentDispatch: 'c4',
  rows: [
    {
      course: 'c4',
      training: true,
      iter: 42,
      offline: false,
      hubSeen: true,
      queuePending: 2,
      inflight: 1,
    },
    {
      course: 'c5',
      training: true,
      iter: 7,
      offline: true,
      hubSeen: true,
      queuePending: 0,
      inflight: 0,
    },
    {
      course: 'ghost',
      training: true,
      iter: null,
      offline: false,
      hubSeen: false,
      queuePending: 0,
      inflight: 0,
    },
    {
      course: 'old',
      training: false,
      iter: 3,
      offline: false,
      hubSeen: true,
      queuePending: 0,
      inflight: 0,
    },
  ],
  ...patch,
})

describe('CourseOverview（并行课程总览）', () => {
  it('每课一行：在训 / 离线 / hub 未注册 / 停止 —— 四态可分辨（文案 + 修饰类）', async () => {
    const { CourseOverview } = await import('../src/web/app/panels/CourseOverview')
    const html = renderToString(
      h(CourseOverview, { overview: overview(), course: 'c4', onSelectCourse: () => {} }),
    )
    expect(html).toContain('tc-cov__badge--on') // c4 在训
    expect(html).toContain('tc-cov__badge--off') // c5 离线（只收回传）
    expect(html).toContain('tc-cov__badge--warn') // ghost 在训但 hub 未注册 → 醒目
    expect(html).toContain('tc-cov__badge--idle') // old 停止
    expect(html).toContain('在派发 2 / worker 3')
    expect(html).toContain('最近派发 c4') // hub 轮转游标
    expect(html).toContain('it42')
    expect(html).toContain('队列 2 · 在飞 1')
  })

  it('行是按钮（可切查看课程），当前查看那一行带 --cur 且 aria-current', async () => {
    const { CourseOverview } = await import('../src/web/app/panels/CourseOverview')
    const html = renderToString(
      h(CourseOverview, { overview: overview(), course: 'c5', onSelectCourse: () => {} }),
    )
    expect((html.match(/<button type="button" class="tc-cov__row/g) ?? []).length).toBe(4)
    expect(html).toContain('tc-cov__row tc-cov__row--cur')
    expect(html).toContain('aria-current="true"')
    expect(html).toContain('title="切到查看 c4"')
  })

  it('hub 行：无应答 → 明确说「hub 无应答」且队列列退化为 —（不编数字）', async () => {
    const { CourseOverview } = await import('../src/web/app/panels/CourseOverview')
    const html = renderToString(
      h(CourseOverview, {
        overview: overview({
          hubUrl: null,
          hubOnline: false,
          raceActive: false,
          halt: false,
          rows: overview().rows.map((r) => ({ ...r, queuePending: 0, inflight: 0 })),
        }),
        course: 'c4',
        onSelectCourse: () => {},
      }),
    )
    expect(html).toContain('hub 无应答')
    expect(html).toContain('队列 —')
    expect(html).not.toContain('队列 0 · 在飞 0')
  })

  it('竞速 / 停机徽标只在置位时出现', async () => {
    const { CourseOverview } = await import('../src/web/app/panels/CourseOverview')
    const off = renderToString(
      h(CourseOverview, { overview: overview(), course: 'c4', onSelectCourse: () => {} }),
    )
    expect(off).not.toContain('tc-cov__race')
    expect(off).not.toContain('tc-cov__halt')
    const on = renderToString(
      h(CourseOverview, {
        overview: overview({ raceActive: true, halt: true }),
        course: 'c4',
        onSelectCourse: () => {},
      }),
    )
    expect(on).toContain('tc-cov__race')
    expect(on).toContain('tc-cov__halt')
  })

  it('无总览 / 无行 → 不渲染（没东西可说时不留空壳）', async () => {
    const { CourseOverview } = await import('../src/web/app/panels/CourseOverview')
    const props = { course: 'c4', onSelectCourse: () => {} }
    expect(renderToString(h(CourseOverview, { ...props, overview: null }))).toBe('')
    expect(renderToString(h(CourseOverview, { ...props, overview: overview({ rows: [] }) }))).toBe(
      '',
    )
  })
})

// ────────────────────────── worker 登记面板 ──────────────────────────

const registry = (patch: Partial<PushWorkerRegistryView> = {}): PushWorkerRegistryView => ({
  hubUrl: 'http://127.0.0.1:18787',
  mounted: true,
  workers: [
    {
      id: 'gpu1',
      url: 'https://a.trycloudflare.com/very/long/path',
      enabled: true,
      concurrency: 2,
      local: false,
      online: true,
      busy: false,
      hubOnline: true,
    },
    {
      id: 'local-push',
      url: 'http://127.0.0.1:18797',
      enabled: true,
      concurrency: 1,
      local: true,
      online: false,
      busy: null,
      hubOnline: false,
    },
    {
      id: 'off',
      url: 'http://127.0.0.1:1',
      enabled: false,
      concurrency: 1,
      local: false,
      online: null,
      busy: null,
      hubOnline: null,
    },
  ],
  ...patch,
})

describe('WorkerRegistry（push worker 登记入口）', () => {
  it('每行两个探活列各自有记号：hub 在线绿 / hub 离线红 / 停用灰 + 本机标记', async () => {
    const { WorkerRegistry } = await import('../src/web/app/panels/WorkerRegistry')
    const html = renderToString(
      h(WorkerRegistry, { registry: registry(), onAction: async () => ({ ok: true }) }),
    )
    expect(html).toContain('tc-dot--on') // gpu1：hub 认为在线
    expect(html).toContain('tc-dot--dead') // local-push：hub 认为离线
    expect(html).toContain('tc-dot--empty') // off：停用
    expect(html).toContain('tc-wreg__local') // 本机回落节点标记
    expect(html).toContain('×2')
    // URL 截断展示（完整 URL 在 title 里）
    expect(html).toContain('title="https://a.trycloudflare.com/very/long/path"')
    expect(html).toContain('aria-label="移除 worker gpu1"')
  })

  it('hub 未挂载派发 → 醒目提示（登记会落配置但 hub 不会真推）', async () => {
    const { WorkerRegistry } = await import('../src/web/app/panels/WorkerRegistry')
    const html = renderToString(
      h(WorkerRegistry, {
        registry: registry({ mounted: false, workers: [] }),
        onAction: async () => ({ ok: true }),
      }),
    )
    expect(html).toContain('hub 未挂载派发')
    expect(html).toContain('还没有登记 push worker')
  })

  it('空登记 + 已挂载：说明「没登记时训练侧按 pull 走」', async () => {
    const { WorkerRegistry } = await import('../src/web/app/panels/WorkerRegistry')
    const html = renderToString(
      h(WorkerRegistry, {
        registry: registry({ workers: [] }),
        onAction: async () => ({ ok: true }),
      }),
    )
    expect(html).toContain('hub 已挂载派发')
    expect(html).toContain('训练侧按 pull 走')
  })

  it('登记表单（默认收起；独立组件可直接断言）：字段齐全 + 空值即禁用保存', async () => {
    const { WorkerRegistry, WorkerForm, workerFormReady } =
      await import('../src/web/app/panels/WorkerRegistry')
    // 收起态：只在按钮的 aria-expanded 上体现
    const collapsed = renderToString(
      h(WorkerRegistry, { registry: registry(), onAction: async () => ({ ok: true }) }),
    )
    expect(collapsed).toContain('aria-expanded="false"')
    expect(collapsed).not.toContain('worker url')
    // 展开态 = 表单组件本体
    const form = renderToString(h(WorkerForm, { onSubmit: async () => true }))
    for (const label of ['worker id', 'worker url', 'worker authKey', 'worker 并发数']) {
      expect(form).toContain(`aria-label="${label}"`)
    }
    expect(form).toContain('placeholder="https://xxx.trycloudflare.com"')
    // 空值 → 保存禁用（格式校验在服务端：面板只做「填没填」这一层）
    expect(form).toContain('disabled')
    expect(workerFormReady({ id: 'g', url: 'u', authKey: 'k', concurrency: '1' })).toBe(true)
    expect(workerFormReady({ id: '  ', url: 'u', authKey: 'k', concurrency: '1' })).toBe(false)
    expect(workerFormReady({ id: 'g', url: 'u', authKey: 'k', concurrency: ' ' })).toBe(false)
  })

  it('只读视图：动作按钮仍可点，悬停给只读提示（只读是动作边界，不是灰败）', async () => {
    const { WorkerRegistry } = await import('../src/web/app/panels/WorkerRegistry')
    const html = renderToString(
      h(WorkerRegistry, {
        registry: registry(),
        onAction: async () => ({ ok: true }),
        readOnly: true,
      }),
    )
    expect(html).toContain('title="只读模式：登记/移除仅限本机 localhost"')
    expect(html).toContain('aria-label="移除 worker gpu1"')
  })
})

// ────────────────────────── 接线（SSR 渲染不出点击，用源码断言兜底） ──────────────────────────

describe('接线：面板动作字符串与路由注册同源', () => {
  const panel = readFileSync(
    path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', 'WorkerRegistry.tsx'),
    'utf-8',
  )
  const app = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'app.tsx'), 'utf-8')
  const route = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'server', 'api', 'route.ts'), 'utf-8')

  it('三个动作名在面板与路由两侧都存在（拼错 = 静默 404/未匹配）', () => {
    for (const act of ['registerPushWorker', 'removePushWorker', 'reloadPushWorkers']) {
      expect(panel).toContain(`'${act}'`)
      expect(route).toContain(`'${act}'`)
    }
    // 启停复用既有节点动作（不发明第二个入口）
    expect(panel).toContain("'setNodeEnabled'")
  })

  it('app.tsx 挂载了两个新面板，且课程 select 不再被 hub 状态禁用', () => {
    expect(app).toContain('<CourseOverview')
    expect(app).toContain('<WorkerRegistry')
    expect(app).toContain('onSelectCourse={selectCourse}')
    // 旧锁定（§367）已随单 hub 多课程解除：select 上不得再出现 disabled
    const sel = app.slice(
      app.indexOf('id="courseSel"'),
      app.indexOf('</select>', app.indexOf('id="courseSel"')),
    )
    expect(sel).not.toContain('disabled')
    expect(app).not.toContain('hubRunning')
  })
})
