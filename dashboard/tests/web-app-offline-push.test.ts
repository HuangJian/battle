/**
 * web-app-offline-push.test.ts — 离线 push 派发器读面（W6-3）
 *
 * 分两层：
 *   · `parseOfflinePushState`（纯函数）：hub 应答的宽容解析——坏行跳过、`rounds` 只数 `revs`、
 *     `proto_ok` 三态（缺键 = 还没问过，不能判成 false）；
 *   · `OfflinePushPanel`（SSR，无 DOM）：操作员能从屏幕上读出什么——挂载三态各说各的话、
 *     在途单的 phase/冲突、worker 的协议门/忙闲。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import {
  parseOfflinePushState,
  type OfflinePushAssignmentView,
  type OfflinePushView,
  type OfflinePushWorkerView,
} from '../src/web/view'

// ────────────────────────── 解析（hub 应答 → 视图） ──────────────────────────

describe('parseOfflinePushState（hub /admin/offline-push-workers 的宽容解析）', () => {
  it('缺 capability ⇒ null（这不是派发器的应答）；坏行跳过', () => {
    expect(parseOfflinePushState(null)).toBeNull()
    expect(parseOfflinePushState({ assignments: [] })).toBeNull()
    const body = {
      capability: 'offline_push',
      dispatched: 2,
      failed: 0,
      conflicts: 1,
      assignments: [
        { course: '' }, // 没有课名 → 跳过
        { course: 'c4', worker_id: 'w1', cursor_it: 5, revs: { '1': {}, '2': {} } },
        'nope',
      ],
      workers: {
        workers: [
          { id: '' }, // 没有 id → 跳过
          { id: 'w1', online: true, busy: false, proto_ok: true, proto: 'offline-push/1' },
          { id: 'w2', online: true, proto_ok: false, proto: 'legacy' },
          { id: 'w3', online: false }, // proto_ok 缺键 = 还没问过（三态里的未知）
          { id: 'w4', online: true, proto_ok: 'yes' }, // 非布尔 ⇒ 未知，不当 true
        ],
      },
    }
    const v = parseOfflinePushState(body)!
    expect(v.capability).toBe('offline_push')
    expect(v.dispatched).toBe(2)
    expect(v.conflicts).toBe(1)
    expect(v.assignments).toHaveLength(1)
    expect(v.assignments[0]).toMatchObject({ course: 'c4', workerId: 'w1', cursorIt: 5, rounds: 2 })
    expect(v.workers.map((w) => w.id)).toEqual(['w1', 'w2', 'w3', 'w4'])
    expect(v.workers.map((w) => w.protoOk)).toEqual([true, false, null, null])
  })
})

// ────────────────────────── 面板（SSR） ──────────────────────────

const assignment = (patch: Partial<OfflinePushAssignmentView> = {}): OfflinePushAssignmentView => ({
  course: 'c4',
  workerId: 'gpu1',
  taskId: 'abcdef0123456789',
  runId: 'run-1',
  phase: 'running',
  cursorIt: 7,
  rounds: 3,
  updatedAt: 0,
  conflict: '',
  ...patch,
})

const worker = (patch: Partial<OfflinePushWorkerView> = {}): OfflinePushWorkerView => ({
  id: 'gpu1',
  url: 'https://w1.trycloudflare.com/very/long/path',
  online: true,
  busy: false,
  queued: 0,
  done: 2,
  misses: 0,
  protoOk: true,
  proto: 'offline-push/1',
  lastErr: '',
  ...patch,
})

const view = (patch: Partial<OfflinePushView> = {}): OfflinePushView => ({
  hubUrl: 'http://127.0.0.1:18787',
  mounted: true,
  capability: 'offline_push',
  dispatched: 4,
  failed: 1,
  conflicts: 0,
  assignments: [assignment()],
  workers: [worker()],
  ...patch,
})

describe('OfflinePushPanel（离线 push 读面）', () => {
  it('在途单与 worker：phase / cursor / 协议门 / 忙闲各自有记号', async () => {
    const { OfflinePushPanel } = await import('../src/web/app/panels/OfflinePushPanel')
    const html = renderToString(
      h(OfflinePushPanel, {
        view: view({
          workers: [
            worker({ busy: true }),
            worker({ id: 'gpu2', protoOk: false, proto: 'legacy' }),
            worker({ id: 'gpu3', protoOk: null, online: false }),
          ],
        }),
      }),
    )
    expect(html).toContain('hub 已挂载离线派发')
    // assignment：课名 + 已入账最高一轮 + 阶段徽章 + 轮数
    expect(html).toContain('c4')
    expect(html).toContain('it 7')
    expect(html).toContain('running')
    expect(html).toContain('3 轮')
    // 忙 / 协议不符 / 在线且空闲 的语义档
    expect(html).toContain('忙')
    expect(html).toContain('proto 不符')
    expect(html).toContain('proto ?')
    expect(html).toContain('tc-dot--dead') // gpu2：协议门没过（红）
    expect(html).toContain('tc-dot--on') // gpu1：在线兼容（绿）
  })

  it('冲突单醒目：徽章 + 悬停原因', async () => {
    const { OfflinePushPanel } = await import('../src/web/app/panels/OfflinePushPanel')
    const html = renderToString(
      h(OfflinePushPanel, {
        view: view({
          assignments: [assignment({ course: 'c5', phase: 'settling', conflict: 'it3 入账冲突' })],
        }),
      }),
    )
    expect(html).toContain('冲突')
    expect(html).toContain('it3 入账冲突')
    expect(html).toContain('tc-dot--dead')
  })

  it('挂载三态与空态各说各的话：不可达 / 未挂载 / 读面未知 / 空表', async () => {
    const { OfflinePushPanel } = await import('../src/web/app/panels/OfflinePushPanel')
    const reachless = renderToString(h(OfflinePushPanel, { view: null }))
    expect(reachless).toContain('hub 不可达')
    expect(reachless).toContain('没有 hub 在应答')

    const unmounted = renderToString(h(OfflinePushPanel, { view: view({ mounted: false }) }))
    expect(unmounted).toContain('hub 未挂载离线派发')
    expect(unmounted).toContain('--offline-push')

    // `null` ≠ `false`：hub 在应答但这条端点没答上，是「未知」不是「没启用」
    const unknown = renderToString(h(OfflinePushPanel, { view: view({ mounted: null }) }))
    expect(unknown).toContain('派发器读面未知')
    expect(unknown).not.toContain('hub 未挂载离线派发')

    const idle = renderToString(
      h(OfflinePushPanel, { view: view({ assignments: [], workers: [] }) }),
    )
    expect(idle).toContain('此刻没有在途单')
    expect(idle).toContain('登记表是空的')
  })
})
