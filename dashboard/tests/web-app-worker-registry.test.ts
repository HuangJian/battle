/**
 * web-app-worker-registry.test.ts — push worker 登记入口面板
 *
 * 分层：src/web/app/panels/WorkerRegistry.tsx（SSR，无 DOM）
 *
 * 断言的是「操作员能从屏幕上读出什么」：worker 行的两个探活列各自有记号、派发开关与登记表
 * 同屏（配了节点走哪条路一眼可见）、登记表单字段齐全且空值即禁用保存。
 * 动作接线（面板 → onAction → 路由）用**源码接线断言**兜底：SSR 渲染不出点击。
 *
 * 「并行课程总览」（原文件里的另一半）已随 P2 合并进课程矩阵，其断言迁到
 * `web-app-coursematrix.test.ts`；纯函数部分在 `web-course-matrix.test.ts`。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import type { PushWorkerRegistryView } from '../src/web/view'

// ────────────────────────── worker 登记面板 ──────────────────────────

const registry = (patch: Partial<PushWorkerRegistryView> = {}): PushWorkerRegistryView => ({
  hubUrl: 'http://127.0.0.1:18787',
  mounted: true,
  hubPush: true,
  workers: [
    {
      id: 'gpu1',
      url: 'https://a.trycloudflare.com/very/long/path',
      enabled: true,
      concurrency: 2,
      online: true,
      busy: false,
      hubOnline: true,
    },
    {
      id: 'gpu2',
      url: 'http://127.0.0.1:18797',
      enabled: true,
      concurrency: 1,
      online: false,
      busy: null,
      hubOnline: false,
    },
    {
      id: 'off',
      url: 'http://127.0.0.1:1',
      enabled: false,
      concurrency: 1,
      online: null,
      busy: null,
      hubOnline: null,
    },
  ],
  ...patch,
})

describe('WorkerRegistry（push worker 登记入口）', () => {
  it('每行两个探活列各自有记号：hub 在线绿 / hub 离线红 / 停用灰', async () => {
    const { WorkerRegistry } = await import('../src/web/app/panels/WorkerRegistry')
    const html = renderToString(
      h(WorkerRegistry, { registry: registry(), onAction: async () => ({ ok: true }) }),
    )
    expect(html).toContain('tc-dot--on') // gpu1：hub 认为在线
    expect(html).toContain('tc-dot--dead') // gpu2：hub 认为离线
    expect(html).toContain('tc-dot--empty') // off：停用
    // 不再有「本机回落节点」标记：本机 worker 与云机 worker 同权（课程与节点正交）
    expect(html).not.toContain('tc-wreg__local')
    expect(html).toContain('×2')
    // URL 截断展示（完整 URL 在 title 里）
    expect(html).toContain('title="https://a.trycloudflare.com/very/long/path"')
    expect(html).toContain('aria-label="移除 worker gpu1"')
  })

  it('派发开关（rl.hub_push，缺省开）与登记表同屏：配了节点走哪条路一眼可见', async () => {
    const { WorkerRegistry } = await import('../src/web/app/panels/WorkerRegistry')
    const on = renderToString(
      h(WorkerRegistry, { registry: registry(), onAction: async () => ({ ok: true }) }),
    )
    expect(on).toContain('aria-checked="true"')
    expect(on).toContain('aria-label="hub 中介派发"')
    const off = renderToString(
      h(WorkerRegistry, {
        registry: registry({ hubPush: false }),
        onAction: async () => ({ ok: true }),
      }),
    )
    expect(off).toContain('aria-checked="false"')
    // 只读视图：开关**照常可点**（与面板其余按钮同哲学——只读是动作边界，不是把区域
    // 画成灰的），悬停提示 + 服务端 403 兜底。
    const ro = renderToString(
      h(WorkerRegistry, {
        registry: registry(),
        onAction: async () => ({ ok: true }),
        readOnly: true,
      }),
    )
    expect(ro).toContain('aria-label="hub 中介派发"')
    expect(ro).toContain('只读模式')
    expect(ro).not.toMatch(/<button[^>]*aria-label="hub 中介派发"[^>]*disabled/)
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

  it('R3-2 模式开关：面板与路由两侧都有 setCourseMode，且 app 接了动作通道', () => {
    const mx = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', 'CourseMatrix.tsx'),
      'utf-8',
    )
    expect(mx).toContain("'setCourseMode'")
    expect(route).toContain("'setCourseMode'")
    // 面板拿到 onAction（否则开关渲染不出来，静默失效）
    expect(app).toContain('onAction={doAction}')
  })

  it('app.tsx 挂载了课程矩阵与推理 worker 登记，且课程 select 不再被 hub 状态禁用', () => {
    expect(app).toContain('<CourseMatrix')
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
