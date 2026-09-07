/**
 * training-console-preact.test.ts — Preact 化控制台的纯函数 / SSR / 分层边界 / /api/pool 契约。
 *
 * 依据 plan/Training-Console-Preact.md：
 *  - §7 交互逻辑纯函数化（shouldFollow / isDirty / nextRefreshInterval / filterGroups /
 *    sortRows / sparkPoints / cardVisible 语义）+ 扩展用例（fmtTs 同日跨日、ISO 剥离、
 *    localStorage 迁移失败保留语义 GLM-U6）；
 *  - R13 ③：src/** 无 .tsx、无 preact import；ui/** 与 console/ui/** 禁服务端 import；
 *  - /api/pool：结构 + selfStatus 占位 + 缓存 key 带 course（DS-E1）。
 */

import { describe, expect, it } from 'bun:test'
import { readdirSync, readFileSync } from 'fs'
import { join } from 'path'
import { REPO_ROOT } from '../tools/training/paths'
import { buildPoolView } from '../tools/training/console/api'
import { renderConsolePage, renderLogPage } from '../tools/training/console/render'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { PanelErrorBoundary } from '../tools/training/ui/components/PanelErrorBoundary'
import {
  cleanupNonTcKeys,
  filterGroups,
  fmtFullTs,
  fmtPct,
  fmtTs,
  isDirty,
  iterGroups,
  keywordMatch,
  latestRow,
  LEGACY_KEY_RULES,
  migrateLegacyKey,
  nextRefreshInterval,
  refreshLabel,
  shouldFollow,
  sortRows,
  sparkline,
  sparkPoints,
  statusFromRecent,
  stripIsoPrefix,
  type IterRow,
} from '../tools/training/ui/view'

// ────────────────────────── 纯函数：时间 / 文本 ──────────────────────────

describe('view 纯函数：时间与文本', () => {
  it('fmtTs：同日省略日期 HH:mm:ss；跨日 MM-DD HH:mm:ss（GLM-U3 精确化）', () => {
    const now = new Date(2026, 8, 7, 12, 0, 0).getTime()
    expect(fmtTs(new Date(2026, 8, 7, 9, 8, 5).getTime(), now)).toBe('09:08:05')
    expect(fmtTs(new Date(2026, 8, 6, 23, 59, 59).getTime(), now)).toBe('09-06 23:59:59')
    expect(fmtTs(new Date(2026, 7, 7, 9, 8, 5).getTime(), now)).toBe('08-07 09:08:05')
  })

  it('fmtFullTs：完整「YYYY-MM-DD HH:MM:SS」（历史锚点比较用）', () => {
    expect(fmtFullTs(new Date(2026, 8, 7, 9, 8, 5).getTime())).toBe('2026-09-07 09:08:05')
  })

  it('fmtPct / stripIsoPrefix', () => {
    expect(fmtPct(0.123)).toBe('12.3%')
    expect(fmtPct(null)).toBe('—')
    expect(stripIsoPrefix('2026-09-07T02:03:04.567Z link timeout')).toBe('link timeout')
    expect(stripIsoPrefix('2026-09-07T02:03:04Z s5: boom')).toBe('s5: boom')
    expect(stripIsoPrefix('2026-09-07T02:03:04 s5: boom')).toBe('s5: boom')
    expect(stripIsoPrefix('plain error')).toBe('plain error')
  })
})

// ────────────────────────── 纯函数：sparkline ──────────────────────────

describe('view sparkPoints / sparkline', () => {
  it('正态序列：坐标数 = 点数，末点圆点', () => {
    const sp = sparkPoints([1, 2, 3, 4, 5])
    expect(sp).not.toBeNull()
    const svg = sparkline([1, 2, 3, 4, 5])
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
    const svg = sparkline([7, 7, 7, 7])
    const ys = [...svg.matchAll(/,([\d.]+) /g)].map((m) => m[1])
    expect(new Set(ys).size).toBeLessThanOrEqual(1)
    expect(svg).toContain('#94a3b8')
  })

  it('空序列与非有限值：占位 / NaN 点跳过', () => {
    expect(sparkline([])).toContain('muted')
    expect(sparkPoints([])).toBeNull()
    const svg = sparkline([1, Number.NaN, 3])
    const pairs =
      svg
        .split('<polyline')[1]!
        .split('/>')[0]!
        .match(/[\d.]+,[\d.]+/g) ?? []
    expect(pairs.length).toBe(2)
  })
})

// ────────────────────────── 纯函数：指标行 / 排序 / 过滤 ──────────────────────────

function fakeRow(iter: number, evalData: IterRow['evalData'] = null): IterRow {
  return {
    iter,
    time: '2026-09-07 09:00:00',
    winRate: 0.1,
    scoreMean: 1,
    scoreStd: 0.1,
    samples: 100,
    rolloutSec: 60,
    ppoSec: 120,
    kl: 0.02,
    entropy: 0.3,
    policyLoss: 0.1,
    valueLoss: 0.2,
    meanRet: 0.1,
    lr: 0.0001,
    expectedGames: 100,
    halted: false,
    topDims: '',
    avgTicks: 900,
    accuracy: 0.5,
    loot: 0.1,
    kills: 5,
    actuals: null,
    evalData,
  }
}

describe('view 指标行分组 / 过滤 / 排序（DS-U1 13 列 + eval 子行语义）', () => {
  const evalData = {
    time: 't',
    games: 10,
    wins: 4,
    winRate: 0.4,
    clears: 1,
    clearRate: 0.1,
    dropped: 0,
    sec: 60,
    wver: 'w',
    outcomes: {},
    avgTicks: 800,
    totalKills: 40,
    totalPU: 5,
    scoreMean: 0.5,
    scoreStd: 0.1,
  }
  const rows = [fakeRow(3), fakeRow(4, evalData), fakeRow(5)]

  it('iterGroups 时间倒序（新轮在前）', () => {
    expect(iterGroups(rows).map((g) => g.iter)).toEqual([5, 4, 3])
  })

  it('filterGroups：all 全保 / rollout 只主行 / eval 只子行', () => {
    const groups = iterGroups(rows)
    expect(filterGroups(groups, 'all')).toHaveLength(3)
    expect(filterGroups(groups, 'rollout')).toHaveLength(2)
    const ev = filterGroups(groups, 'eval')
    expect(ev).toHaveLength(1)
    expect(ev[0]!.iter).toBe(4)
  })

  it('latestRow：空 → null；取最大 iter（固定头部状态条口径）', () => {
    expect(latestRow([])).toBeNull()
    expect(latestRow([fakeRow(3), fakeRow(9), fakeRow(5)])!.iter).toBe(9)
  })

  it('sortRows：数值优先 + null 沉底 + 字符串 localeCompare', () => {
    const rows = [{ k: 3 }, { k: 1 }, { k: 2 }]
    expect(sortRows(rows, 'k', 'asc').map((r) => r.k)).toEqual([1, 2, 3])
    expect(sortRows(rows, 'k', 'desc').map((r) => r.k)).toEqual([3, 2, 1])
    const withNull = [{ k: null }, { k: 5 }]
    expect(sortRows(withNull, 'k', 'asc').map((r) => r.k)).toEqual([5, null])
    const strs = sortRows([{ s: 'b' }, { s: 'a' }], 's', 'asc')
    expect(strs.map((r) => r.s)).toEqual(['a', 'b'])
  })

  it('keywordMatch：空词放行；大小写不敏感；缺失字段安全', () => {
    expect(keywordMatch({ a: 'Hello' }, ['a'], '')).toBe(true)
    expect(keywordMatch({ a: 'Hello' }, ['a'], 'HELLO')).toBe(true)
    expect(keywordMatch({ a: 5 }, ['a'], '5')).toBe(true)
    expect(keywordMatch({ a: 'x' }, ['b'], 'x')).toBe(false)
  })

  it('statusFromRecent：≥90% 健康 / ≥70% 波动 / <70% 异常 / 空 = 无数据', () => {
    expect(statusFromRecent([])).toBe('nodata')
    expect(statusFromRecent([true, true, true, true, true, true, true, true, true, true])).toBe(
      'healthy',
    )
    expect(statusFromRecent([true, true, true, true, true, true, true, true, false, false])).toBe(
      'warn',
    )
    expect(statusFromRecent([false, false])).toBe('bad')
  })
})

// ────────────────────────── 纯函数：dirty / follow / 节奏 ──────────────────────────

describe('view 交互纯函数（§7 评审 E5 替代 DOM 测试）', () => {
  it('isDirty：pendingEdits 非空即 true', () => {
    expect(isDirty(new Map())).toBe(false)
    expect(isDirty(new Map([['conc:self', '8']]))).toBe(true)
  })

  it('nextRefreshInterval 轮转 3→5→30→暂停→3；refreshLabel 文案', () => {
    expect(nextRefreshInterval(3)).toBe(5)
    expect(nextRefreshInterval(5)).toBe(30)
    expect(nextRefreshInterval(30)).toBe('pause')
    expect(nextRefreshInterval('pause')).toBe(3)
    expect(refreshLabel('pause')).toBe('暂停')
    expect(refreshLabel(5)).toBe('5s')
  })

  it('shouldFollow：贴底跟随 / 上滚不跟随（阈值 24）', () => {
    expect(shouldFollow(500, 100, 600)).toBe(true)
    expect(shouldFollow(0, 100, 600)).toBe(false)
    expect(shouldFollow(476, 100, 600)).toBe(true)
    expect(shouldFollow(475, 100, 600)).toBe(false)
    expect(shouldFollow(100, 100, 200, 0)).toBe(true)
  })
})

// ────────────────────────── localStorage 迁移（GLM-U6 / DS-E9） ──────────────────────────

class MockStorage {
  map = new Map<string, string>()
  getItem(k: string): string | null {
    return this.map.get(k) ?? null
  }
  setItem(k: string, v: string): void {
    this.map.set(k, v)
  }
  removeItem(k: string): void {
    this.map.delete(k)
  }
  key(i: number): string | null {
    return [...this.map.keys()][i] ?? null
  }
  get length(): number {
    return this.map.size
  }
}

describe('localStorage 迁移与白名单清理', () => {
  it('合法值迁移：写新键 + 删旧键；返回 true', () => {
    const s = new MockStorage()
    s.setItem('pool.iterFilter', 'eval')
    const ok = migrateLegacyKey(s as never, LEGACY_KEY_RULES[1]!)
    expect(ok).toBe(true)
    expect(s.getItem('tc.metrics.filter')).toBe('eval')
    expect(s.getItem('pool.iterFilter')).toBeNull()
  })

  it('非法值 → 保留旧键 + 返回 false（GLM-U6：调用方 console.warn 语义）', () => {
    const s = new MockStorage()
    s.setItem('pool.iterFilter', 'bad-value')
    const ok = migrateLegacyKey(s as never, LEGACY_KEY_RULES[1]!)
    expect(ok).toBe(false)
    expect(s.getItem('pool.iterFilter')).toBe('bad-value')
    expect(s.getItem('tc.metrics.filter')).toBeNull()
  })

  it('旧键不存在 → true 无副作用', () => {
    const s = new MockStorage()
    expect(migrateLegacyKey(s as never, LEGACY_KEY_RULES[0]!)).toBe(true)
    expect(s.length).toBe(0)
  })

  it('cleanupNonTcKeys：清理白名单外残留，保留 tc.* 与遗留键', () => {
    const s = new MockStorage()
    s.setItem('tc.card.nodes', '1')
    s.setItem('stale.key', 'x')
    s.setItem('pool.iterFilter', 'all')
    const removed = cleanupNonTcKeys(s as never)
    expect(removed).toEqual(['stale.key'])
    expect(s.getItem('tc.card.nodes')).toBe('1')
    expect(s.getItem('pool.iterFilter')).toBe('all')
    expect(s.getItem('stale.key')).toBeNull()
  })
})

// ────────────────────────── SSR 渲染（render.tsx 包装替代 page.ts 断言） ──────────────────────────

describe('控制台 SSR（render.tsx renderConsolePage）', () => {
  it('首屏包含标题/锚点/卡结构，无整页 reload，无 script 注入', async () => {
    const { buildStateView } = await import('../tools/training/console/api')
    const s = await buildStateView()
    const html = renderConsolePage(s)
    expect(html).toContain('NN 训练控制台')
    expect(html).toContain('window.__INITIAL__')
    expect(html).toContain('自动（最近活跃课程）')
    expect(html).toContain('tc-cc') // 组件小卡驱动
    expect(html).toContain('/app.js')
    expect(html).not.toContain('location.reload()') // 无整页 reload（§4.5）
    expect(html).not.toContain('<script>alert')
    // 固定头部训练状态条 + 一屏仪表盘结构（DECISIONS §355）
    expect(html).toContain('tc-status')
    expect(html).toContain('tc-hero')
    expect(html).toContain('tc-comps')
    expect(html).toContain('tc-npill')
    // worker_server（冒烟瞬态）不进渲染体
    expect(html).not.toContain('<span class="tc-cc__name">workerServe')
    // 详情抽屉 / 弹窗 SSR 首帧不渲染（tc-drawer 类名在 CSS，用渲染体判定）
    expect(html).not.toContain('<aside class="tc-drawer"')
    expect(html).not.toContain('启动 TrainingLoop')
  })
})

describe('日志页 SSR（render.tsx renderLogPage，§348 补 2 语义保留）', () => {
  it('follow 默认开：id="follow" checked 属性（不按裸词断言）', async () => {
    const { buildStateView, componentLogPayload } = await import('../tools/training/console/api')
    const p = (await componentLogPayload('selfNode', 40))!
    const state = await buildStateView()
    const html = renderLogPage(p, {
      components: state.components.map((c) => ({ key: c.key, label: c.label, status: c.status })),
      follow: true,
      lines: 40,
    })
    expect(html).toContain('组件日志')
    expect(html).toContain('id="logbox"')
    expect(html).toContain('id="follow" checked')
    expect(html).toContain('/log/trainingLoop')
    expect(html).toContain('返回控制台')
    expect(html).toContain('/app-log.js')
    expect(html).not.toContain('<script>alert')
  })

  it('follow=false 无 checked；缺文件显示占位', async () => {
    const { componentLogPayload } = await import('../tools/training/console/api')
    const p = (await componentLogPayload('cloudflared', 20))!
    p.exists = false
    p.lines = []
    const html = renderLogPage(p, { components: [], follow: false, lines: 20 })
    expect(html).toContain('日志文件不存在')
    expect(html).not.toContain('id="follow" checked')
  })
})

// ────────────────────────── /api/pool 契约（DS-E1 / selfStatus 占位） ──────────────────────────

describe('/api/pool（buildPoolView）', () => {
  it('结构：nodes/local/cachedAt/activeFlow/selfStatus（null 或对象）', async () => {
    const pv = await buildPoolView()
    expect(typeof pv.cachedAt).toBe('number')
    expect(Array.isArray(pv.nodes)).toBe(true)
    for (const r of pv.nodes) {
      expect(r.kind).toBe('node')
      expect(r.id.length).toBeGreaterThan(0)
      expect(['healthy', 'warn', 'bad', 'noping', 'nodata', 'disabled']).toContain(r.status)
      expect(typeof r.ok).toBe('number')
      expect(typeof r.fail).toBe('number')
      expect(typeof r.contrib).toBe('number')
      expect(Array.isArray(r.recent)).toBe(true)
      expect(typeof r.lastError).toBe('string')
    }
    if (pv.local) {
      expect(pv.local.kind).toBe('local')
      expect(pv.local.id).toBe('local')
    }
    expect(pv.selfStatus === null || typeof pv.selfStatus.workers === 'number').toBe(true)
    expect(typeof pv.localHash).toBe('string')
  })

  it('缓存 key 带 course：同 course 命中（cachedAt 不变）；?fresh=1 bypass', async () => {
    const a = await buildPoolView(false)
    const b = await buildPoolView(false)
    expect(b.cachedAt).toBe(a.cachedAt)
    const c = await buildPoolView(true)
    expect(c.cachedAt).toBeGreaterThanOrEqual(a.cachedAt)
  })
})

// ────────────────────────── 分层边界（R13 ③ + 铁律 R1，静态扫描） ──────────────────────────

function walkFiles(dir: string, out: string[]): string[] {
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, ent.name)
    if (ent.isDirectory()) walkFiles(p, out)
    else if (ent.name.endsWith('.ts') || ent.name.endsWith('.tsx')) out.push(p)
  }
  return out
}

describe('分层边界（MANIFEST/plan §3.1 R1 + R13 ③）', () => {
  it('src/** 无 .tsx、无 preact import（主游戏零影响）', () => {
    const files = walkFiles(join(REPO_ROOT, 'src'), [])
    expect(files.filter((f) => f.endsWith('.tsx'))).toEqual([])
    for (const f of files) {
      const t = readFileSync(f, 'utf8')
      expect(t, f).not.toMatch(/from ['"]preact/)
      expect(t, f).not.toMatch(/from ['"]preact\/hooks/)
    }
  })

  it('ui/** 与 console/ui/** 不得 import 服务端模块（node:/fs/Bun./console(api|actions)）', () => {
    const dirs = [
      join(REPO_ROOT, 'tools', 'training', 'ui'),
      join(REPO_ROOT, 'tools', 'training', 'console', 'ui'),
    ]
    const banned = [/from ['"]node:/, /from ['"]fs['"]/, /\bBun\./, /console\/(api|actions)/]
    for (const dir of dirs) {
      for (const f of walkFiles(dir, [])) {
        if (f.includes('.build')) continue // 跳过构建产物目录
        const t = readFileSync(f, 'utf8')
        for (const re of banned) {
          expect(t.match(re), `${f} 违反分层铁律 ${re}`).toBeNull()
        }
      }
    }
  })
})

describe('PanelErrorBoundary SSR 隔离（DS-E3）', () => {
  it('单 panel render 崩溃 → 错误占位 + 兄弟节点正常，不整页断', () => {
    // 静态 ESM import（与 render.tsx 同一模块实例；options.errorBoundaries 已由其置位）
    const Boom: () => import('preact').ComponentChildren = () => {
      throw new Error('boom-panel')
    }
    const html = renderToString(
      h(
        'div',
        null,
        h(PanelErrorBoundary, null, h(Boom, null)),
        h('section', { id: 'sibling' }, '存活'),
      ),
    )
    expect(html).toContain('该卡片加载失败：boom-panel')
    expect(html).toContain('存活')
    expect(html).toContain('id="sibling"')
  })
})
