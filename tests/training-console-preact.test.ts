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
import {
  emptyHistory,
  isSlowNode,
  isSlowNodeRows,
  parseTsMs,
} from '../tools/training/console/pool-history'
import { renderConsolePage, renderLogPage } from '../tools/training/console/render'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { CopyButton } from '../tools/training/ui/components/CopyButton'
import { PanelErrorBoundary } from '../tools/training/ui/components/PanelErrorBoundary'
import { NodeEditPill, NodePills } from '../tools/training/console/ui/panels/NodePills'
import {
  cleanupNonTcKeys,
  filterGroups,
  fmtFullTs,
  fmtPct,
  fmtTs,
  formatBytes,
  isDirty,
  iterGroups,
  keywordMatch,
  latestRow,
  LEGACY_KEY_RULES,
  migrateLegacyKey,
  nextRefreshInterval,
  parseLogLine,
  parsePhaseFromLog,
  parseTrainingEvent,
  pendingLockReleases,
  refreshLabel,
  shortUrl,
  shouldFollow,
  sortRows,
  sparkline,
  sparkPoints,
  statusFromRecent,
  stripIsoPrefix,
  type ConsoleStateView,
  type IterRow,
  type NodeView,
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

describe('组件动作 pending 锁（§367：启/停 点击先 disable，状态切换完成再 enable）', () => {
  it('pendingLockReleases：状态从点击时值切换才解锁；未变 / 组件消失不解锁', () => {
    // 当前状态：a=启动完成(running) / b=停止完成(stopped) / c=启动失败(still stopped)
    const statusOf = (k: string): string | undefined =>
      ({ a: 'running', b: 'stopped', c: 'stopped' })[k]
    // 启动完成：stopped → running
    expect(pendingLockReleases({ a: 'stopped' }, statusOf)).toEqual(['a'])
    // 停止完成：running → stopped
    expect(pendingLockReleases({ b: 'running' }, statusOf)).toEqual(['b'])
    // 启动失败：状态仍是 stopped → 不解锁（由失败回调直接释放）
    expect(pendingLockReleases({ c: 'stopped' }, statusOf)).toEqual([])
    // 组件从 stateView 消失（statusOf 返回 undefined）→ 不解锁
    expect(pendingLockReleases({ ghost: 'stopped' }, statusOf)).toEqual([])
    // 混合：已切换的解锁，未变/消失的保留
    expect(
      pendingLockReleases({ a: 'stopped', b: 'running', c: 'stopped', ghost: 'x' }, statusOf),
    ).toEqual(['a', 'b'])
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
    avgWinTicks: null,
    totalKills: 40,
    totalPU: 5,
    avgResidualHp: null,
    avgLossTicks: null,
    dmgPerKill: null,
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

  it('nextRefreshInterval 轮转 1m→3m→5m→10m→30m→暂停→1m；refreshLabel 文案', () => {
    expect(nextRefreshInterval(60)).toBe(180)
    expect(nextRefreshInterval(180)).toBe(300)
    expect(nextRefreshInterval(300)).toBe(600)
    expect(nextRefreshInterval(600)).toBe(1800)
    expect(nextRefreshInterval(1800)).toBe('pause')
    expect(nextRefreshInterval('pause')).toBe(60)
    expect(refreshLabel('pause')).toBe('暂停')
    expect(refreshLabel(60)).toBe('1m')
    expect(refreshLabel(300)).toBe('5m')
    expect(refreshLabel(1800)).toBe('30m')
  })

  it('parsePhaseFromLog：iteration 头 → rollout；rollout itN/push/ppo → ppo；空/未知 → idle', () => {
    // 空日志 / 无时间戳行 → idle
    expect(parsePhaseFromLog([])).toEqual({ phase: 'idle', sinceMs: null, iter: null })
    expect(parsePhaseFromLog(['random noise line'])).toEqual({
      phase: 'idle',
      sinceMs: null,
      iter: null,
    })
    // === iteration N/M === → rollout（取 N）
    expect(parsePhaseFromLog(['[10:00:00] [run_rl] === iteration 7/12 ==='])).toEqual({
      phase: 'rollout',
      sinceMs: new Date().setHours(10, 0, 0, 0),
      iter: 7,
    })
    // rollout itN: → ppo（rollout 已结束，进入 PPO）
    expect(parsePhaseFromLog(['[10:01:30] [run_rl] rollout it7: win=0.5'])).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 1, 30, 0),
      iter: 7,
    })
    // push / ppo itN / weights archived → ppo
    expect(parsePhaseFromLog(['[10:02:00] [run_rl] push: it7 done'])).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 2, 0, 0),
      iter: 7,
    })
    expect(parsePhaseFromLog(['[10:03:00] [run_rl] ppo it7: kl=0.01'])).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 3, 0, 0),
      iter: 7,
    })
    // §380：published job（wait_job 等待云 worker 期间日志尾常停在此行）→ ppo
    expect(
      parsePhaseFromLog(['[10:04:00] published job abcd1234 it7: shards=150 data_fp=abc…']),
    ).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 4, 0, 0),
      iter: 7,
    })
    expect(parsePhaseFromLog(['[10:04:00] [run_rl] weights archived'])).toEqual({
      phase: 'ppo',
      sinceMs: new Date().setHours(10, 4, 0, 0),
      iter: null,
    })
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
    expect(html).toContain('<title>炼丹炉</title>')
    expect(html).toContain('rel="icon"')
    expect(html).toContain('window.__INITIAL__')
    expect(html).toContain('自动（最近活跃课程）')
    expect(html).toContain('tc-cc') // 组件小卡驱动
    expect(html).toContain('/app.js')
    expect(html).not.toContain('location.reload()') // 无整页 reload（§4.5）
    expect(html).not.toContain('<script>alert')
    // 标题行置顶（§382：课程 select 并入标题行中部，指标 chips 已移除）+ 一屏仪表盘结构
    expect(html).toContain('tc-topbar__course')
    expect(html).toContain('tc-hero')
    expect(html).toContain('tc-comps')
    expect(html).toContain('tc-npill')
    // worker_server（冒烟瞬态）不进渲染体
    expect(html).not.toContain('<span class="tc-cc__name">workerServe')
    // 详情抽屉 / 弹窗 SSR 首帧不渲染（tc-drawer 类名在 CSS，用渲染体判定）
    expect(html).not.toContain('<aside class="tc-drawer"')
    // 弹窗本体不渲染（勿用 '启动 TrainingLoop' 裸子串——它与未运行时训练卡启动键的
    // aria-label '启动 TrainingLoop (trainer)' 撞词，训练态一停就误报）
    expect(html).not.toContain('class="tc-modal-mask"')
  })
})

describe('Hero 训练状态区（§367：最新 6 轮完整指标）', () => {
  it('渲染最新 6 轮（iter 倒序截断）；超过 6 轮不溢出；无数据不出表', async () => {
    const { Hero } = await import('../tools/training/console/ui/panels/Hero')
    const mkView = (iters: IterRow[]): ConsoleStateView => ({
      time: 't',
      course: 'kb1',
      courses: [],
      components: [],
      nodes: [],
      modes: { trainerPpo: 'pull', stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      metrics: { available: true, iters },
      phase: { phase: 'idle', sinceMs: null, iter: null },
    })
    // 8 轮 → 只出 3..8（倒序前 6）
    const html = renderToString(
      h(Hero, {
        stateView: mkView(Array.from({ length: 8 }, (_, i) => fakeRow(i + 1))),
        onMore: () => {},
      }),
    )
    expect(html).toContain('最新 6 轮完整指标')
    for (const it of [8, 7, 6, 5, 4, 3]) expect(html).toContain(`<b>${it}</b>`)
    for (const it of [2, 1]) expect(html).not.toContain(`<b>${it}</b>`)
    // 不足 6 轮：全出，标题带实际行数
    const html2 = renderToString(
      h(Hero, { stateView: mkView([fakeRow(1), fakeRow(2)]), onMore: () => {} }),
    )
    expect(html2).toContain('最新 2 轮完整指标')
    expect(html2).toContain('<b>2</b>')
    expect(html2).toContain('<b>1</b>')
    // 无数据：hero 空态无表
    const html3 = renderToString(h(Hero, { stateView: mkView([]), onMore: () => {} }))
    expect(html3).not.toContain('tc-hero__iters')
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
    expect(html).toContain('/log.js') // 服务端可服务的 bundle 路径（§371：旧 /app-log.js 404）
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

describe('日志页展示层（§367：结构化解析 + 事件卡）', () => {
  it('parseLogLine：切分时间戳 + [组件] 标签 + 正文，按关键词判级别', () => {
    const l = parseLogLine('[13:49:32] [sampler-agent] task done key=abc buf.len=3980')
    expect(l.ts).toBe('13:49:32')
    expect(l.tag).toBe('sampler-agent')
    expect(l.level).toBe('info')
    expect(l.text).toBe('task done key=abc buf.len=3980')
    // 错误/警告级别
    expect(parseLogLine('[10:00:00] task failed: timeout').level).toBe('error')
    expect(parseLogLine('[10:00:01] retrying...').level).toBe('warn')
    expect(parseLogLine('plain line without prefix').ts).toBeNull()
    expect(parseLogLine('plain line without prefix').tag).toBeNull()
    expect(parseLogLine('plain line without prefix').level).toBe('info')
  })

  it('parseTrainingEvent：trainingLoop JSON 行 → event 徽章 + 精选字段；iter_error 带 error', () => {
    const it = JSON.stringify({
      event: 'iteration',
      iter: 7,
      winRate: 0.2933,
      kl: 0.004847568204240764,
      time: '2026-09-08 06:44:04',
      expectedGames: 150,
    })
    const e = parseTrainingEvent(it)
    expect(e).not.toBeNull()
    expect(e!.event).toBe('iteration')
    expect(e!.fields.find(([k]) => k === 'it')?.[1]).toBe('7')
    expect(e!.fields.find(([k]) => k === 'win')?.[1]).toBe('29.3%')
    expect(e!.fields.find(([k]) => k === 'kl')?.[1]).toBe('0.0048')
    expect(e!.hasError).toBeUndefined()

    const err = parseTrainingEvent(
      JSON.stringify({
        event: 'iter_error',
        iter: 13,
        error: 'HubClientError: wait_job: 超时（>1800.0s）未完成',
        time: '2026-09-08 08:24:31',
      }),
    )
    expect(err!.event).toBe('iter_error')
    expect(err!.hasError).toContain('超时')
    // 非 JSON / 无 event 字段 → null
    expect(parseTrainingEvent('plain text')).toBeNull()
    expect(parseTrainingEvent(JSON.stringify({ foo: 1 }))).toBeNull()
  })

  it('formatBytes 人性化：B/KB/MB', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.00 MB')
    expect(formatBytes(-1)).toBe('—')
  })

  it('renderLogPage：trainingLoop JSON 行渲染为结构化事件卡（event 徽章 + 字段），普通行带行号/时间戳列', () => {
    const p = {
      component: 'trainingLoop',
      label: 'trainingLoop (训练循环)',
      log: 'tmp/x/training_log.jsonl',
      exists: true,
      fileSize: 1024,
      lines: [
        JSON.stringify({ event: 'iteration', iter: 7, winRate: 0.2933, time: 't' }),
        '[13:49:32] [sampler-agent] task done key=abc',
        'a failed thing',
      ],
      truncated: false,
    }
    const html = renderLogPage(p, { components: [], follow: true, lines: 20 })
    expect(html).toContain('tc-logline--ev-iteration')
    expect(html).toContain('iteration')
    expect(html).toContain('tc-logline--error') // failed 行红色级别
    expect(html).toContain('tc-logline__no') // 行号 gutter
    expect(html).not.toContain('<script>alert')
  })

  it('renderLogPage（§371）：尾行 all 选项 + 常驻直达底部 FAB + 更新于指示 + all 截断文案', () => {
    const p = {
      component: 'selfNode',
      label: 'selfNode',
      log: 'tmp/sampler-agent.log',
      exists: true,
      fileSize: 888,
      lines: ['[13:49:32] [sampler-agent] task done', 'a doomed thing'],
      truncated: true,
      updatedAt: 1788820000000,
    }
    const html = renderLogPage(p, { components: [], follow: false, lines: 'all' })
    expect(html).toContain('value="all"') // 尾行下拉含 all
    expect(html).toContain('已截断·尾部窗口') // all 专用截断文案
    expect(html).toContain('更新于') // 取数时刻可感知（自动刷新=有动）
    expect(html).toContain('tc-logbtn') // 直达底部按钮并入工具栏（不再浮动 FAB）
    expect(html).toContain('已到底部') // 初始贴底态文案
    expect(html).not.toContain('<script>alert')
  })

  it('renderLogPage（§372）：共 N 行 = 文件总行数（totalLines）', () => {
    const p = {
      component: 'selfNode',
      label: 'selfNode',
      log: 'tmp/sampler-agent.log',
      exists: true,
      fileSize: 888,
      lines: Array.from({ length: 5 }, (_, i) => `line-${i}`),
      truncated: true,
      totalLines: 1234,
    }
    const html = renderLogPage(p, { components: [], follow: false, lines: 200 })
    expect(html).toMatch(/共\s*<b>1234<\/b>\s*行/) // 顶部显示文件总行数而非截断窗口 5
    expect(html).not.toMatch(/共\s*<b>5<\/b>\s*行/)
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
describe('§361：icon 复制键 / cloudflared endpoint 截断与复制 / local pill', () => {
  it('CopyButton icon 模式：按钮无可见「复制」文字（仅 ⧉/✓；语义走 title/aria）', () => {
    const html = renderToString(
      h(CopyButton, { text: 'https://abc.trycloudflare.com', label: '隧道', icon: true }),
    )
    expect(html).toContain('⧉')
    expect(html).not.toContain('⧉ 复制')
    expect(html).toContain('复制隧道') // title/aria 仍有复制语义
    // 非 icon 模式仍带「复制」字样
    const plain = renderToString(h(CopyButton, { text: 'x' }))
    expect(plain).toContain('复制')
  })

  it('cloudflared endpoint 截断展示 + 全量复制（title 留全量）', () => {
    const longUrl = 'https://abc-def.trycloudflare.com/abcdefgh/%2F%2F%2F%2F%2F'
    const s = {
      time: 't',
      course: 'c',
      courses: [],
      components: [
        {
          key: 'cloudflared',
          label: 'cloudflared (入站隧道)',
          status: 'running',
          pid: 1,
          url: longUrl,
          course: null,
          mode: null,
          healthy: true,
          log: null,
          logTail: [],
          busy: false,
          secret: 'tok_123456789012345',
        },
      ],
      nodes: [],
      modes: { trainerPpo: 'pull' as const, stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      metrics: { available: false, iters: [] },
      phase: { phase: 'idle' as const, sinceMs: null, iter: null },
      localNode: null,
    } as ConsoleStateView
    const html = renderConsolePage(s)
    expect(html).toContain(shortUrl(longUrl))
    expect(html).toContain(`title="${longUrl}"`) // 截断展示，hover 留全量
    expect(html).toContain('⧉') // icon 复制键在
  })

  it('local pill：只读展示（槽位 + 上轮贡献）', () => {
    const s = {
      time: 't',
      course: 'c',
      courses: [],
      components: [],
      nodes: [],
      modes: { trainerPpo: 'pull' as const, stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      metrics: { available: false, iters: [] },
      phase: { phase: 'idle' as const, sinceMs: null, iter: null },
      localNode: { id: 'local', slots: 3, lastContrib: 2 },
    } as ConsoleStateView
    const html = renderConsolePage(s)
    expect(html).toContain('tc-npill--local')
    expect(html).toContain('>local<')
    expect(html).toContain('3槽')
    expect(html).toContain('本机直跑')
  })
})

// ────────────────────────── 首页 EvalBoard 摘要（列 = 阶梯；学生仅 B 层） ──────────────────────────

describe('首页 EvalSummary（阶梯 God vs 学生 B 层）', () => {
  const mockRung = (
    id: string,
    over: Partial<import('../tools/training/ui/view').EvalLadderRow> = {},
  ): import('../tools/training/ui/view').EvalLadderRow => ({
    rung: id,
    dimension: '基准',
    lives: 1,
    starLevel: 1,
    stageBrief: {
      name: 'arena-13x13-4enemies',
      enemies: ['basic'],
      enemyCount: 1,
      hasBase: false,
      hasTerrain: false,
    },
    god: { winRate: null, lifePrice: null, n: 0, provisional: null },
    batches: 0,
    n: 0,
    latestWin: null,
    windowWin: null,
    aTrend: null,
    deltaVsGod: null,
    gate: null,
    partial: false,
    ...over,
  })

  it('SSR 首屏含摘要壳与抽屉入口（数据客户端拉，首帧 loading）', async () => {
    const { buildStateView } = await import('../tools/training/console/api')
    const html = renderConsolePage(await buildStateView())
    expect(html).toContain('tc-eval-summary')
    expect(html).toContain('EvalBoard 摘要')
    expect(html).toContain('完整评估看板')
    expect(html).toContain('加载评估摘要')
  })

  const mockIter = (
    over: Partial<import('../tools/training/ui/view').EvalIterRow> = {},
  ): import('../tools/training/ui/view').EvalIterRow => ({
    kind: 'iter',
    course: 'c4-margin',
    iter: 30,
    cells: {},
    n: 400,
    batchIds: ['b1'],
    screening: true,
    ...over,
  })
  const mockGod = (
    cells: Record<string, number | null> = {},
  ): import('../tools/training/ui/view').EvalIterRow => ({
    kind: 'god',
    course: 'c4-margin',
    iter: -1,
    cells,
    n: 0,
    batchIds: [],
    screening: false,
  })

  it('矩阵：行=iter（含 God），列=rung×指标；hover 表头 = rungLabel', async () => {
    const { EvalSummaryTable } = await import('../tools/training/console/ui/panels/EvalSummary')
    const { EVAL_METRIC_KEYS } = await import('../tools/training/ui/view')
    const ladder = [
      mockRung('c4l1', { god: { winRate: 0.64, lifePrice: 0.1, n: 1600, provisional: false } }),
      mockRung('c6l1', { dimension: '敌数' }),
    ]
    const html = renderToString(
      h(EvalSummaryTable, {
        ladder,
        iterRows: [
          mockGod({ 'c4l1.winRate': 0.64 }),
          mockIter({ iter: 30, cells: { 'c4l1.winRate': 0.5, 'c4l1.meanKills': 3.2 } }),
        ],
        course: 'c4-margin',
        metricKeys: [...EVAL_METRIC_KEYS],
      }),
    )
    expect(html).toContain('God')
    expect(html).toContain('it30')
    expect(html).toContain('64.0%')
    expect(html).toContain('50.0%')
    expect(html).toContain('3.20') // meanKills 两位小数
    expect(html).toContain('c4l1 平均击杀')
    // hover 标题含关卡画像（rungLabel：1★ / 无基地）
    expect(html).toContain('1★')
    expect(html).toContain('无基地')
    // 筛查级标注（A4）
    expect(html).toContain('筛查级')
    expect(html).toContain('c4-margin')
  })

  it('指标显隐：勾掉「平均道具」后该指标列消失', async () => {
    const { EvalSummaryTable } = await import('../tools/training/console/ui/panels/EvalSummary')
    const html = renderToString(
      h(EvalSummaryTable, {
        ladder: [mockRung('c4l1')],
        iterRows: [mockGod({}), mockIter({ cells: { 'c4l1.winRate': 0.5 } })],
        course: 'c4-margin',
        metricKeys: ['winRate'],
      }),
    )
    expect(html).toContain('c4l1 胜率')
    expect(html).not.toContain('c4l1 平均道具')
    expect(html).not.toContain('c4l1 平均击杀')
  })

  it('A12 空态：B 层 0 行 → 引导（不显示空数据行），并列出在途批', async () => {
    const { EvalSummaryTable } = await import('../tools/training/console/ui/panels/EvalSummary')
    const html = renderToString(
      h(EvalSummaryTable, {
        ladder: [mockRung('c4l1')],
        iterRows: [mockGod({})], // 只有 God 行 = B 层 0 行
        course: 'c6-margin',
        metricKeys: ['winRate'],
        batches: [
          {
            batch_id: 'b-20260911T014319-0806',
            course: 'c6-margin',
            rung_from: 'c4l1',
            status: 'pending',
            iter: 30,
            trigger: 'standalone',
            units: { of: 2, done: [] },
            elapsed_sec: null,
            created_ts: '2026-09-11T01:43:19.050Z',
            policy: 'nn',
          },
        ],
      }),
    )
    expect(html).toContain('尚无 B 层数据')
    expect(html).toContain('kick-once.py')
    expect(html).toContain('it30')
    expect(html).not.toContain('tc-tablewrap') // 不渲染空矩阵
  })
})

// ────────────────────────── /eval 独立页 + CSV（R8/R9） ──────────────────────────

describe('/eval 独立页 + CSV 导出（R8/R9）', () => {
  const rung: import('../tools/training/ui/view').EvalLadderRow = {
    rung: 'c4l1',
    dimension: '基准',
    lives: 1,
    starLevel: 1,
    stageBrief: {
      name: 'arena-13x13-4enemies',
      enemies: ['basic', 'basic', 'basic', 'basic'],
      enemyCount: 4,
      hasBase: false,
      hasTerrain: false,
    },
    god: { winRate: 0.64, lifePrice: 1.5, n: 1600, provisional: false },
    batches: 4,
    n: 400,
    latestWin: 0.5,
    windowWin: 0.5,
    aTrend: null,
    deltaVsGod: -0.14,
    gate: null,
    partial: false,
  }
  const view: import('../tools/training/ui/view').EvalBoardView = {
    cachedAt: 0,
    course: 'c4-margin',
    ingested: 0,
    evalEvery: null,
    abWarn: null,
    ladder: [rung],
    alerts: [],
    batches: [],
    flips: [],
    rows: 400,
    spaceCalibrated: false,
    iterRows: [
      {
        kind: 'god',
        course: 'c4-margin',
        iter: -1,
        cells: { 'c4l1.winRate': 0.64 },
        n: 0,
        batchIds: [],
        screening: false,
      },
      {
        kind: 'iter',
        course: 'c4-margin',
        iter: 30,
        cells: { 'c4l1.winRate': 0.5 },
        n: 400,
        batchIds: ['b1'],
        screening: true,
      },
    ],
    runnerState: null,
    ladderState: null,
  }

  it('renderEvalPage SSR：矩阵/触发区/导出/返回控制台 + eval.js bundle', async () => {
    const { renderEvalPage } = await import('../tools/training/console/render')
    const html = renderEvalPage({
      views: [view],
      options: { courses: ['c4-margin'], allCourses: ['c4-margin'], readOnly: false },
    })
    expect(html).toContain('EvalBoard 评估页')
    expect(html).toContain('/eval.js')
    expect(html).toContain('导出 CSV')
    expect(html).toContain('入队评估批')
    expect(html).toContain('返回控制台')
    expect(html).toContain('it30')
  })

  it('CSV：UTF-8 BOM + 拍平表头 + RFC4180 转义 + 空值不写 0', async () => {
    const { buildEvalCsv } = await import('../tools/training/ui/view')
    const rows: import('../tools/training/ui/view').EvalIterRow[] = [
      {
        kind: 'iter',
        course: 'c4-margin',
        iter: 30,
        cells: { 'c4l1.winRate': 0.5 },
        n: 100,
        batchIds: ['b1'],
        screening: true,
      },
    ]
    const csv = buildEvalCsv(rows, [
      { header: 'n', value: (r) => (r.n > 0 ? r.n : null) },
      { header: 'c4l1 / 胜率, "quoted"', value: (r) => r.cells['c4l1.winRate'] ?? null },
      { header: 'missing', value: () => null },
    ])
    expect(csv.startsWith('\uFEFF')).toBe(true)
    const firstLine = csv.slice(1).split('\r\n')[0]
    expect(firstLine).toBe('course / iter,n,"c4l1 / 胜率, ""quoted""",missing')
    expect(csv).toContain('c4-margin / it30,100,0.5,') // 末列空值 = 空串
    expect(csv).not.toContain('1e-') // 无科学计数
  })
})

// ────────────────────────── 节点 pill 行（慢节点/停用/启停 toggle，2026-09-11 用户指令） ──────────────────────────

describe('pool-history.isSlowNode（慢节点判定：1.5s ping 误报「离线」的根治）', () => {
  it('近期有成功结算且耗时不高 → false；结算慢（中位>8s）或超 10 分钟无结算 → true', () => {
    const now = Date.now()
    const mk = (
      over: Record<string, unknown>,
    ): import('../tools/training/console/pool-history').NodeHistory => ({
      ...emptyHistory(),
      ...over,
    })
    // 健康节点：刚结算过且快
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 30_000, avgElapsedSec: 1.2, lastOkElapsedSec: 1.5 })),
    ).toBe(false)
    // 慢节点：还在结算但单局要几十秒（Kaggle CPU 饱和 → 1.5s ping 必超时）
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 60_000, avgElapsedSec: 19.9, lastOkElapsedSec: 41.9 })),
    ).toBe(true)
    // 快节点但偶发沉默 5 分钟（轮间隙）：不判慢（耗时没超阈值）
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 5 * 60_000, avgElapsedSec: 1.2, lastOkElapsedSec: 1.5 })),
    ).toBe(false)
    // 历史从未结算过：不算慢节点（真离线照旧标离线）
    expect(isSlowNode(mk({ lastOkTsMs: null, avgElapsedSec: null, lastOkElapsedSec: null }))).toBe(
      false,
    )
    // 恰好在窗口边界内 + 耗时未知 → 不误判
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 9 * 60_000, avgElapsedSec: null, lastOkElapsedSec: null })),
    ).toBe(false)
  })

  it('独立重实现对拍：慢节点行样本 → isSlowNode(聚合) 与 isSlowNodeRows(逐行) 同判', () => {
    const now = Date.parse('2026-09-11T12:00:00')
    const rows = [
      { node: 'a95', ok: true, elapsedSec: 41.9, ts: '2026-09-11T11:52:21' },
      { node: 'self', ok: true, elapsedSec: 1.5, ts: '2026-09-11T11:52:21' },
      { node: 'a95', ok: false, ts: '2026-09-11T11:55:00' },
    ]
    // a95：7.6 分钟前成功过且耗时 41.9s > 8s → 慢节点
    expect(isSlowNodeRows(rows, 'a95', now)).toBe(true)
    // self：快 → 非慢
    expect(isSlowNodeRows(rows, 'self', now)).toBe(false)
    // 无记录节点 → 非慢（真离线口径）
    expect(isSlowNodeRows(rows, 'a98', now)).toBe(false)
  })

  it('窗口外交互：慢节点沉默超窗后回「离线」；健康快节点沉默同窗后也是「离线」', () => {
    const now = Date.now()
    const mk = (
      over: Record<string, unknown>,
    ): import('../tools/training/console/pool-history').NodeHistory => ({
      ...emptyHistory(),
      ...over,
    })
    // 慢节点最后一次成功在 31 分钟前：窗口外 → 真离线
    expect(isSlowNode(mk({ lastOkTsMs: now - 31 * 60_000, lastOkElapsedSec: 41.9 }))).toBe(false)
    // 同窗口内：慢节点（41.9s）成立
    expect(isSlowNode(mk({ lastOkTsMs: now - 29 * 60_000, lastOkElapsedSec: 41.9 }))).toBe(true)
    // 健康快节点沉默 31 分钟：窗口外 → 真离线（窗口不是慢节点的保护伞）
    expect(isSlowNode(mk({ lastOkTsMs: now - 31 * 60_000, lastOkElapsedSec: 1.5 }))).toBe(false)
  })

  it('parseTsMs：ISO（T 分隔）与空格分隔两种写法都能解析', () => {
    expect(parseTsMs('2026-09-11T11:52:21')).toBe(Date.parse('2026-09-11T11:52:21'))
    expect(parseTsMs('2026-09-11 11:52:21')).toBe(Date.parse('2026-09-11T11:52:21'))
    expect(parseTsMs(undefined)).toBeNull()
    expect(parseTsMs('garbage')).toBeNull()
  })
})

describe('NodePills（慢/离线不折叠 · 停用折叠 · 启停 toggle）', () => {
  const mkNode = (over: Partial<NodeView>): NodeView => ({
    id: 'a1',
    url: 'http://a1',
    gpuPush: false,
    enabled: true,
    concurrency: 2,
    online: true,
    codeHash: null,
    cpus: 8,
    busy: false,
    lastContrib: 3,
    slow: false,
    ...over,
  })

  it('慢节点始终展开显示「慢」（琥珀点），不进折叠桶', () => {
    const n = mkNode({ id: 'a95', online: false, slow: true, lastContrib: 5 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).not.toContain('慢 1')
    expect(html).not.toContain('>离线<')
    expect(html).toContain('tc-dot--warn')
    expect(html).toContain('<b>a95</b>')
    expect(html).toContain('>慢</span>')
  })

  it('停用节点：灰点（tc-dot--empty），文案「停用」，与离线红点区分', () => {
    const n = mkNode({ id: 'a97', enabled: false, online: null })
    const pill = renderToString(
      h(NodeEditPill, {
        n,
        editing: false,
        draft: '',
        off: true,
        onEdit: () => {},
        onDraft: () => {},
        onSave: () => {},
        onToggle: () => {},
        onSmoke: () => {},
      }),
    )
    expect(pill).toContain('tc-dot--empty')
    expect(pill).not.toContain('tc-dot--dead')
    expect(pill).toContain('>停用</span>')
    expect(pill).toContain('tc-npill--disabled')
  })

  it('真离线（无近期贡献）：始终展开显示红点 + 「离线」', () => {
    const n = mkNode({ id: 'a98', online: false, slow: false, lastContrib: -1 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('>离线</span>')
    expect(html).not.toContain('离线 1')
  })

  it('启停为 toggle 开关：非编辑态 pill 上直接渲染 role=switch（aria 名 = 目标动作）', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [mkNode({ id: 'a1', enabled: true })],
        onAction: () => {},
        onMore: () => {},
      }),
    )
    // aria 压缩后按属性片段断言（preact-render-to-string 省略 boolean 值的 ="true"）
    expect(html).toMatch(/role="switch"/)
    expect(html).toContain('aria-checked="true"')
    // aria 名描述目标动作（enabled pill 上的开关点下去 = 停用）——接线契约：
    // Switch onClick → onChange(!checked) → onAction('setNodeEnabled', {id, enabled})
    expect(html).toContain('aria-label="停用 a1"')
    expect(html).toContain('tc-switch--on')
  })

  it('停用 pill 的开关同样在场（重新启用即点开）', () => {
    const n = mkNode({ id: 'a2', enabled: false, online: null })
    const pill = renderToString(
      h(NodeEditPill, {
        n,
        editing: false,
        draft: '',
        off: true,
        onEdit: () => {},
        onDraft: () => {},
        onSave: () => {},
        onToggle: () => {},
        onSmoke: () => {},
      }),
    )
    expect(pill).toMatch(/role="switch"/)
    expect(pill).toContain('aria-label="启用 a2"')
    expect(pill).not.toContain('tc-switch--on')
  })
})
