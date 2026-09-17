/**
 * web-wire-panel-wiring.test.ts — 「传输」视图接线（app.tsx ↔ WirePanel）
 *
 * 为什么用源码断言（仓库既有同款：web-train-launch-wiring / web-ssr-readonly）：
 *  抽屉 tab 是 `DrawerTabKey` 联合 + 两处字面量数组（tabs 声明、nav 入口）+ 一处
 *  内容分派。少改任何一处都不会报错，只会让面板**存在但永远打不开**（或 tab
 *  打开是空白）——tsc 抓不到，单测渲染也抓不到（抽屉默认关闭）。
 *
 * 同批守一条口径：下行字节对 push 模式必须走 worker 侧 `result_bytes`
 * （`wire.downBytes` 为 null 是 pull 专用口径）——写错就会全屏「—」。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { WirePanel } from '../src/web/app/panels/WirePanel'
import type { ConsoleStateView, IterRow } from '../src/web/view'

const DASHBOARD_ROOT = join(import.meta.dir, '..')
const APP = join(DASHBOARD_ROOT, 'src', 'web', 'app', 'app.tsx')
const PANEL = join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', 'WirePanel.tsx')

describe('app.tsx 「传输」tab 接线', () => {
  const src = readFileSync(APP, 'utf8').replace(/\s+/g, ' ')

  it('DrawerTabKey 含 wire，且 tabs 与 nav 两处都注册了入口', () => {
    expect(src).toContain("type DrawerTabKey = 'metrics' | 'nodes' | 'log' | 'wire'")
    // tabs 声明（Drawer 属性）：只要求存在该条目，不绑定它在数组里的位置
    expect(src).toContain("{ key: 'wire', label: '传输' }")
    // nav 直连入口（否则只能靠抽屉里切，入口不可见 = 等于没有）
    expect(src).toContain("['wire', '传输']")
  })

  it('wire tab 的内容分派到 WirePanel（漏了就是空白页）', () => {
    // ⚠ 不要写死括号/换行：pre-commit 的 oxfmt 会重排 JSX，把 `? ( <X/> )` 收成
    // `? <X/>`——写死形状的断言会在别人提交时才第一次变红（本测试首版就踩了这个）。
    expect(src).toContain("drawerTab === 'wire' ?")
    expect(src).toContain('<WirePanel stateView={stateView} course={viewCourse} />')
    expect(src).toContain("import { WirePanel } from './panels/WirePanel'")
  })
})

describe('WirePanel 口径', () => {
  const panel = readFileSync(PANEL, 'utf8')

  it('push 模式下行必须回退 worker.result_bytes（hub 侧 down 口径为 pull 专用）', () => {
    expect(panel).toContain('w.downBytes !== null')
    expect(panel).toContain('w.worker?.result_bytes')
  })

  it('缺数据显空态 + 重跑命令，不编造 0', () => {
    // 空态文案里带可复制的重跑命令（操作员不必回来翻文档）
    expect(panel).toContain('remote/tunnel_ab_probe.py')
    expect(panel).toMatch(/尚无传输账|还没有探针结果/)
  })
})

// ────────────────────────── 渲染（只读 fields 极小子集，故不完整构造视图） ──────────────────────────

/** WirePanel 只读 `metrics.iters[].wire` 与 `tunnelAb` 两处，其余字段与它无关。 */
function viewWith(iters: IterRow[], tunnelAb: ConsoleStateView['tunnelAb']): ConsoleStateView {
  return { metrics: { available: true, iters }, tunnelAb } as ConsoleStateView
}

function iterRow(iter: number, wire: IterRow['wire']): IterRow {
  return { iter, time: `t${iter}`, wire } as IterRow
}

describe('WirePanel 渲染', () => {
  it('本轮账：字节/秒/吞吐/协议/瘦身 都上屏；下行对 push 走 worker.result_bytes', () => {
    const html = renderToString(
      h(WirePanel, {
        course: 'c',
        stateView: viewWith(
          [
            iterRow(7, {
              upBytes: 1_203_724,
              upSec: 4.8,
              packSec: 3.2,
              downBytes: null, // push 口径
              downSec: null,
              blobsMiss: 0,
              protocol: 'http2',
              edgeIp: '4',
              slim: true,
              rolloutSrc: 'local',
              worker: { result_bytes: 1_041_907, blob_hits: 1 },
            }),
          ],
          null,
        ),
      }),
    )
    expect(html).toContain('本轮 it7')
    expect(html).toContain('1.1 MB') // 上行 fmtBytes
    expect(html).toContain('4.80s')
    expect(html).toContain('2.0 Mbps') // 1_203_724*8/4.8/1e6
    expect(html).toContain('http2')
    expect(html).toContain('瘦身 开')
    // 下行来自 worker.result_bytes（hub 侧 down 为 null）而不是「—」
    expect(html).toContain(fmtBytesIsPresent(1_041_907))
  })

  it('无 wire 的账本 → 空态文案，不渲染「本轮」块', () => {
    const html = renderToString(
      h(WirePanel, { course: 'c', stateView: viewWith([iterRow(1, null)], null) }),
    )
    expect(html).toContain('尚无传输账')
    expect(html).not.toContain('本轮 it')
  })

  it('A/B 结果：腿/方向/p50/p90/max/吞吐 + 倍率徽章都在', () => {
    const html = renderToString(
      h(WirePanel, {
        course: 'c',
        stateView: viewWith([], {
          available: true,
          runs: [
            {
              file: 'tunnel-ab-2.json',
              mtime: Date.now() - 60_000,
              bytes: 2_097_152,
              rounds: 5,
              rows: [
                {
                  leg: 'http2',
                  dir: 'up',
                  stat: { n: 5, p50Sec: 4.66, p90Sec: 5.9, maxSec: 6.49, p50Mbps: 3.6 },
                },
                {
                  leg: 'quic',
                  dir: 'up',
                  stat: { n: 5, p50Sec: 23.42, p90Sec: 25.4, maxSec: 43.54, p50Mbps: 0.7 },
                },
              ],
              speedup: { up: 5.026, down: null },
            },
          ],
        }),
      }),
    )
    expect(html).toContain('tunnel-ab-2.json')
    expect(html).toContain('2MiB × 5 发')
    expect(html).toContain('4.66s')
    expect(html).toContain('23.42s')
    expect(html).toContain('http2 上行 p50 快 5.0×')
  })
})

/** 1,041,907 B 的 fmtBytes 文案（避免在断言里硬编码格式化细节）。 */
function fmtBytesIsPresent(b: number): string {
  return b < 1024 * 1024 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`
}
