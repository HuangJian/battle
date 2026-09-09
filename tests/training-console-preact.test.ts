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
import { CopyButton } from '../tools/training/ui/components/CopyButton'
import { PanelErrorBoundary } from '../tools/training/ui/components/PanelErrorBoundary'
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
