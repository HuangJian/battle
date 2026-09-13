/**
 * web-view-spark.test.ts — sparkline：坐标数 = 点数 + 末点圆点、恒定序列满幅平线、空序列 / NaN 占位；hero 胜率走势 SVG（tc-trend__svg）
 *
 * 分层：src/web/view/spark.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出；另含 training-console-preact.test.ts
 * 的同模块 describe（2026-09-14 补回：首轮拆分文件被第二轮同名文件覆盖，3 个用例丢失）。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api, render, view } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

describe('console sparkline (ui/view)', () => {
  it('正态序列：polyline 坐标数 = 数据点数，含末点圆点', () => {
    const svg = view.sparkline([1, 2, 3, 4, 5])
    expect(svg).toContain('<svg')
    expect(svg).toContain('<polyline')
    // 5 个坐标对（每对 x,y；[\d.] 同时匹配整数与小数）
    const pairs =
      svg
        .split('<polyline')[1]!
        .split('/>')[0]!
        .match(/[\d.]+,[\d.]+/g) ?? []
    expect(pairs.length).toBe(5)
    expect(svg).toContain('<circle')
  })

  it('恒定序列：满幅平线（y 折半）+ 灰色（无形状可循）', () => {
    const svg = view.sparkline([7, 7, 7, 7])
    // 全部 y 相同 = height/2
    const ys = [...svg.matchAll(/,([\d.]+) /g)].map((m) => m[1])
    expect(new Set(ys).size).toBeLessThanOrEqual(1)
    expect(svg).toContain('#94a3b8')
  })

  it('空序列与非有限值：占位符 / NaN 点被跳过', () => {
    expect(view.sparkline([])).toContain('muted')
    const svg = view.sparkline([1, Number.NaN, 3])
    const pairs =
      svg
        .split('<polyline')[1]!
        .split('/>')[0]!
        .match(/[\d.]+,[\d.]+/g) ?? []
    expect(pairs.length).toBe(2)
  })

  it('hero 渲染胜率趋势走势图（有数据课程）', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('tc-hero')
    if (s.metrics.available && s.metrics.iters.length > 0) {
      // 大胜率走势 SVG（tc-trend__svg），含坐标轴网格线
      expect(html).toContain('tc-trend__svg')
      expect(html).toContain('<line') // 网格线
    }
  })
})

describe('view sparkPoints / sparkline', () => {
  it('正态序列：坐标数 = 点数，末点圆点', () => {
    const sp = view.sparkPoints([1, 2, 3, 4, 5])
    expect(sp).not.toBeNull()
    const svg = view.sparkline([1, 2, 3, 4, 5])
    expect(svg).toContain('<svg')
    expect(svg).toContain('<polyline')
    const pairs =
      svg
        .split('<polyline')[1]!
        .split('/>')[0]!
        .match(/[\d.]+,[\d.]+/g) ?? []
    expect(pairs.length).toBe(5)
    expect(svg).toContain('<circle')
  })

  it('恒定序列：满幅平线（y=height/2）+ 灰色', () => {
    const svg = view.sparkline([7, 7, 7, 7])
    const ys = [...svg.matchAll(/,([\d.]+) /g)].map((m) => m[1])
    expect(new Set(ys).size).toBeLessThanOrEqual(1)
    expect(svg).toContain('#94a3b8')
  })

  it('空序列与非有限值：占位 / NaN 点跳过', () => {
    expect(view.sparkline([])).toContain('muted')
    expect(view.sparkPoints([])).toBeNull()
    const svg = view.sparkline([1, Number.NaN, 3])
    const pairs =
      svg
        .split('<polyline')[1]!
        .split('/>')[0]!
        .match(/[\d.]+,[\d.]+/g) ?? []
    expect(pairs.length).toBe(2)
  })
})
