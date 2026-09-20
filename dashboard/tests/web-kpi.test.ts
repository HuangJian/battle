/**
 * web-kpi.test.ts — KPI 条：六格派生（view/kpi.ts 纯函数）+ SSR 结构。
 *
 * 分层：src/web/view/kpi.ts + src/web/components/KpiStrip.tsx
 *
 * 事故背景（P2b 之前）：回答「现在什么情况」要滚动读数——胜率在趋势图右上角、
 * iter 在 Hero 表首行、在训课程数在总览卡表头、节点在线数在 pill 行、队列深度在调度器表头。
 * KPI 条把这六个**已经存在于快照里**的读数提到首屏同一行（它是索引，不是第二套真相）。
 *
 * 本用例钉住三条纪律：
 *   ① 六格恒定（快照在就给满六格，没有数据也出格子 + 说明——不许整块消失，C13）；
 *   ② 「不知道 ≠ 0」（hub 无应答时队列是 `—`，不是 0）；
 *   ③ 口径写进 title（数字对不上别处的原因从来是口径没说清）。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { KpiStrip } from '../src/web/components/KpiStrip'
import { KPI_UNKNOWN, kpiTiles, type KpiTile } from '../src/web/view/kpi'
import type { ConsoleStateView, IterRow } from '../src/web/view'

const NOW = 1_700_000_000_000

/** 迭代行（只给用例关心的字段；其余照 IterRow 契约给中性值）。 */
function iter(patch: Partial<IterRow> & { iter: number }): IterRow {
  return {
    time: '10:00',
    winRate: 0.2,
    scoreMean: 0,
    scoreStd: 0,
    samples: 20,
    rolloutSec: 60,
    ppoSec: 30,
    pureCollectSec: null,
    ppoCloudSec: null,
    distPhaseSec: null,
    kl: 0.005,
    entropy: 1,
    policyLoss: 0,
    valueLoss: 0,
    meanRet: 0,
    lr: 1e-4,
    expectedGames: 20,
    halted: false,
    topDims: '',
    avgTicks: 500,
    accuracy: 0,
    loot: 0,
    kills: 1,
    actuals: null,
    evalData: null,
    ...patch,
  } as IterRow
}

/** 最小快照（只给六格用到的字段；其余留空）。 */
function state(patch: Partial<ConsoleStateView> = {}): ConsoleStateView {
  return {
    time: '2026-09-20T10:00:00Z',
    course: 'c1',
    courses: ['c1'],
    components: [],
    nodes: [],
    modes: { stream: 1, doubleBuffer: 0, precollectEarly: 0 },
    metrics: { available: true, iters: [] },
    phase: { phase: 'idle', sinceMs: null, iter: null },
    ...patch,
  }
}

const tile = (tiles: KpiTile[], id: KpiTile['id']): KpiTile => {
  const t = tiles.find((x) => x.id === id)
  if (!t) throw new Error(`缺 KPI 格：${id}`)
  return t
}

describe('kpiTiles：六格恒定 + 顺序固定', () => {
  it('空快照（null）→ 无格（整条由组件给「读盘中」说明，不静默）', () => {
    expect(kpiTiles(null, NOW)).toEqual([])
  })

  it('有快照即六格，顺序固定（练得怎么样 → 跑到哪了 → 算力/调度）', () => {
    const tiles = kpiTiles(state(), NOW)
    expect(tiles.map((t) => t.id)).toEqual(['win', 'eval', 'phase', 'courses', 'fleet', 'sched'])
    // 每格都必须有值、有副读数、有口径（不许空字符串——空串就是没解释）
    for (const t of tiles) {
      expect(t.value.length).toBeGreaterThan(0)
      expect(t.sub.length).toBeGreaterThan(0)
      expect(t.title.length).toBeGreaterThan(10)
    }
  })
})

describe('① 采样胜率：最新真实轮 + 与上一轮差值', () => {
  it('两轮以上 → 值取最新轮，Δ 对上一轮（带 it 号）', () => {
    const tiles = kpiTiles(
      state({
        metrics: {
          available: true,
          iters: [iter({ iter: 1, winRate: 0.2 }), iter({ iter: 2, winRate: 0.25 })],
        },
      }),
      NOW,
    )
    const t = tile(tiles, 'win')
    expect(t.value).toBe('25.0%')
    expect(t.sub).toBe('Δ +5.0pp vs it1')
    expect(t.tone).toBe('warn') // 25% 落在 winTone 的黄档（>=10% 且 <30%）
  })

  it('只一轮 → 不编 Δ，说「首轮」', () => {
    const t = tile(
      kpiTiles(
        state({ metrics: { available: true, iters: [iter({ iter: 7, winRate: 0.31 })] } }),
        NOW,
      ),
      'win',
    )
    expect(t.value).toBe('31.0%')
    expect(t.sub).toBe('首轮（it7）')
    expect(t.tone).toBe('ok') // >=30% 绿
  })

  it('it0（bc 基线合成行）不算采样轮（它的 rollout 字段是 NaN 缺口）', () => {
    const tiles = kpiTiles(
      state({ metrics: { available: true, iters: [iter({ iter: 0, winRate: Number.NaN })] } }),
      NOW,
    )
    const t = tile(tiles, 'win')
    expect(t.value).toBe(KPI_UNKNOWN)
    expect(t.sub).toContain('暂无迭代记录')
    expect(t.tone).toBe('off')
  })

  it('负数差值带负号（退步也要看得出来）', () => {
    const t = tile(
      kpiTiles(
        state({
          metrics: {
            available: true,
            iters: [iter({ iter: 1, winRate: 0.3 }), iter({ iter: 2, winRate: 0.22 })],
          },
        }),
        NOW,
      ),
      'win',
    )
    expect(t.sub).toBe('Δ -8.0pp vs it1')
    expect(t.tone).toBe('warn')
  })
})

describe('② eval 胜率：同一轮的 gap（过拟合信号）', () => {
  const evalRow = (it: number, wr: number, rollout: number): IterRow =>
    iter({
      iter: it,
      winRate: rollout,
      evalData: {
        time: '10:00',
        games: 20,
        wins: Math.round(wr * 20),
        winRate: wr,
        clears: 0,
        clearRate: null,
        dropped: 0,
        sec: 100,
        wver: 'w1',
        outcomes: {},
        avgTicks: null,
        avgWinTicks: null,
        totalKills: null,
        totalPU: null,
        avgResidualHp: null,
        avgLossTicks: null,
        dmgPerKill: null,
        scoreMean: null,
        scoreStd: null,
      },
    })

  it('取最新带评估的轮；gap 用**同一轮**的采样胜率算', () => {
    const t = tile(
      kpiTiles(
        state({
          metrics: {
            available: true,
            iters: [
              evalRow(3, 0.4, 0.5), // 同轮 gap = -10pp（eval 低于采样 = 过拟合信号）
              iter({ iter: 4, winRate: 0.62 }), // 更新的轮但没有 eval
            ],
          },
        }),
        NOW,
      ),
      'eval',
    )
    expect(t.value).toBe('40.0%')
    expect(t.sub).toBe('对采样 -10.0pp · 8/20 局')
    expect(t.tone).toBe('ok')
  })

  it('无干净评估 → 出格子（不消失）+ 说明为什么没有 + 灰档', () => {
    const t = tile(
      kpiTiles(state({ metrics: { available: true, iters: [iter({ iter: 3 })] } }), NOW),
      'eval',
    )
    expect(t.value).toBe(KPI_UNKNOWN)
    expect(t.sub).toContain('尚无干净评估')
    expect(t.tone).toBe('off')
    expect(t.title).toContain('还没有一轮带干净评估的迭代')
  })
})

describe('③ 当前阶段：值 = 轮次，副读数 = 阶段 + 已走', () => {
  it('采集阶段 → itN + 采集 + 已走时长（nowMs 减 sinceMs）', () => {
    const t = tile(
      kpiTiles(
        state({
          metrics: { available: true, iters: [iter({ iter: 12 })] },
          phase: { phase: 'rollout', sinceMs: NOW - 125_000, iter: 12 },
        }),
        NOW,
      ),
      'phase',
    )
    expect(t.value).toBe('it12')
    expect(t.sub).toBe('采集 · 已走 2m 5s')
    expect(t.tone).toBe('ok')
  })

  it('PPO 阶段 → PPO', () => {
    const t = tile(
      kpiTiles(
        state({
          metrics: { available: true, iters: [iter({ iter: 3 })] },
          phase: { phase: 'ppo', sinceMs: NOW - 30_000, iter: 3 },
        }),
        NOW,
      ),
      'phase',
    )
    expect(t.sub).toBe('PPO · 已走 30s')
  })

  it('空闲 → 出格子说「空闲」，不是消失', () => {
    const t = tile(
      kpiTiles(state({ metrics: { available: true, iters: [iter({ iter: 3 })] } }), NOW),
      'phase',
    )
    expect(t.value).toBe('it3')
    expect(t.sub).toContain('空闲')
    expect(t.tone).toBe('off')
  })

  it('完全无迭代 → 值为 —（快照在但还没跑过）', () => {
    const t = tile(kpiTiles(state(), NOW), 'phase')
    expect(t.value).toBe(KPI_UNKNOWN)
  })
})

describe('④ 在训课程：n/N + 「另有 M 门在别处训练」', () => {
  it('当前课不在训、别处 2 门在训 → 明说「另有 2 门」', () => {
    const t = tile(
      kpiTiles(
        state({ course: 'c1', courses: ['c1', 'c2', 'c3', 'c4'], trainingCourses: ['c2', 'c3'] }),
        NOW,
      ),
      'courses',
    )
    expect(t.value).toBe('2/4')
    expect(t.sub).toBe('另有 2 门在别处训练')
    expect(t.tone).toBe('ok')
  })

  it('当前课在训且只有它 → 「含当前查看的课」（不说「另有 0 门」）', () => {
    const t = tile(
      kpiTiles(state({ course: 'c1', courses: ['c1', 'c2'], trainingCourses: ['c1'] }), NOW),
      'courses',
    )
    expect(t.sub).toBe('含当前查看的课')
    // 当前课在训 + 另一门也在训 → 「另有 1 门」
    const two = tile(
      kpiTiles(state({ course: 'c1', courses: ['c1', 'c2'], trainingCourses: ['c1', 'c2'] }), NOW),
      'courses',
    )
    expect(two.sub).toBe('另有 1 门在别处训练')
  })

  it('一门都不在训 → 0/N（灰）+ 说清没有在训的课', () => {
    const t = tile(kpiTiles(state({ courses: ['c1', 'c2'], trainingCourses: [] }), NOW), 'courses')
    expect(t.value).toBe('0/2')
    expect(t.sub).toBe('没有在训的课程')
    expect(t.tone).toBe('off')
  })

  it('旧视图无 trainingCourses 清单 → 回退调度器口径且不猜逐课归属', () => {
    const t = tile(
      kpiTiles(
        state({
          courses: ['c1', 'c2'],
          loopQueue: { blockedCourses: [], pools: {}, rows: [], trainingCount: 1 },
        }),
        NOW,
      ),
      'courses',
    )
    expect(t.value).toBe('1/2')
    expect(t.sub).toBe('在训 1 门（旧视图无逐课名单）')
  })

  it('没有任何课程 → 分母 0 + 说明', () => {
    const t = tile(kpiTiles(state({ courses: [] }), NOW), 'courses')
    expect(t.value).toBe('0/0')
    expect(t.sub).toBe('未发现任何课程')
  })
})

describe('⑤ 算力：启用节点在线 x/y + 本机槽位', () => {
  const node = (
    id: string,
    patch: Record<string, unknown> = {},
  ): ConsoleStateView['nodes'][number] =>
    ({
      id,
      url: `https://${id}`,
      gpuPush: true,
      enabled: true,
      concurrency: 1,
      online: true,
      slow: false,
      codeHash: null,
      cpus: null,
      busy: false,
      lastContrib: 0,
      ...patch,
    }) as ConsoleStateView['nodes'][number]

  it('全部在线 + 本机 4 槽 → ok', () => {
    const t = tile(
      kpiTiles(
        state({
          nodes: [node('a'), node('b')],
          localNode: { id: 'local', slots: 4, lastContrib: 0 },
        }),
        NOW,
      ),
      'fleet',
    )
    expect(t.value).toBe('2/2')
    expect(t.sub).toBe('2/2 在线 · 本机 4 槽')
    expect(t.tone).toBe('ok')
  })

  it('停用的节点不进分母（它们本来就不该在线）', () => {
    const t = tile(
      kpiTiles(state({ nodes: [node('a'), node('b', { enabled: false, online: false })] }), NOW),
      'fleet',
    )
    expect(t.value).toBe('1/1')
    expect(t.tone).toBe('ok')
  })

  it('有节点掉线 → warn；慢节点单独标出（慢 ≠ 离线）', () => {
    const t = tile(
      kpiTiles(
        state({
          nodes: [
            node('a'),
            node('b', { online: false }),
            node('c', { online: false, slow: true }),
          ],
        }),
        NOW,
      ),
      'fleet',
    )
    expect(t.value).toBe('1/3')
    expect(t.sub).toContain('1 慢')
    expect(t.tone).toBe('warn')
  })

  it('没有远端节点 → 值 —（不是 0/0）+ 灰档', () => {
    const t = tile(kpiTiles(state(), NOW), 'fleet')
    expect(t.value).toBe(KPI_UNKNOWN)
    expect(t.sub).toBe('本机直跑未启用')
    expect(t.tone).toBe('off')
  })

  it('本机 0 槽 → 说出来（直跑未启用 ≠ 不知道）', () => {
    const t = tile(
      kpiTiles(
        state({ nodes: [node('a')], localNode: { id: 'local', slots: 0, lastContrib: -1 } }),
        NOW,
      ),
      'fleet',
    )
    expect(t.sub).toContain('本机直跑未启用（0 槽）')
  })
})

describe('⑥ 调度：队列深度 + 在飞 + 等票', () => {
  const overview = (patch: Partial<NonNullable<ConsoleStateView['overview']>> = {}) => ({
    hubUrl: 'http://127.0.0.1:8787',
    hubOnline: true,
    raceActive: false,
    activeCourses: 2,
    activeWorkers: 2,
    halt: false,
    recentDispatch: null,
    rows: [
      {
        course: 'c1',
        training: true,
        iter: 5,
        offline: false,
        hubSeen: true,
        queuePending: 3,
        inflight: 1,
        offlineRounds: 0,
        offlineLastIter: null,
        offlineLastMtime: 0,
      },
      {
        course: 'c2',
        training: false,
        iter: 7,
        offline: false,
        hubSeen: true,
        queuePending: 2,
        inflight: 4,
        offlineRounds: 0,
        offlineLastIter: null,
        offlineLastMtime: 0,
      },
    ],
    offlineProgress: null,
    ...patch,
  })

  const loopOk = { blockedCourses: [], pools: {}, rows: [], trainingCount: 1 }

  it('hub 在线 → 队列跨课求和，在飞也求和', () => {
    const t = tile(kpiTiles(state({ overview: overview(), loopQueue: loopOk }), NOW), 'sched')
    expect(t.value).toBe('5')
    expect(t.unit).toBe('待领 job')
    expect(t.sub).toBe('在飞 5')
    expect(t.tone).toBe('ok')
  })

  it('训练侧只读视图读不到 → hub 读数仍给，但明说调度侧缺（不静默丢一半）', () => {
    const t = tile(kpiTiles(state({ overview: overview() }), NOW), 'sched')
    expect(t.value).toBe('5') // hub 侧事实仍然有效
    expect(t.sub).toBe('在飞 5 · 调度器视图未读')
  })

  it('有课在等资源票 → warn + 点名列课', () => {
    const t = tile(
      kpiTiles(
        state({
          overview: overview(),
          loopQueue: {
            blockedCourses: ['c2'],
            pools: { ppo: { held: 1, capacity: 1 } },
            rows: [],
            trainingCount: 1,
          },
        }),
        NOW,
      ),
      'sched',
    )
    expect(t.sub).toBe('在飞 5 · 等票 1 门')
    expect(t.tone).toBe('warn')
    expect(t.title).toContain('c2')
  })

  it('队列是 0 且都正常 → 显示 0（确定没有 ≠ 不知道）', () => {
    const t = tile(
      kpiTiles(
        state({
          overview: overview({
            rows: [
              {
                course: 'c1',
                training: true,
                iter: 1,
                offline: false,
                hubSeen: true,
                queuePending: 0,
                inflight: 0,
                offlineRounds: 0,
                offlineLastIter: null,
                offlineLastMtime: 0,
              },
            ],
          }),
        }),
        NOW,
      ),
      'sched',
    )
    expect(t.value).toBe('0')
    expect(t.tone).toBe('ok')
  })

  it('hub 无应答 → 值是 —（不是 0！）+ warn + 说清为什么', () => {
    const t = tile(
      kpiTiles(
        state({
          overview: overview({
            hubOnline: false,
            hubUrl: null,
            rows: [],
            activeCourses: 0,
            activeWorkers: 0,
          }),
        }),
        NOW,
      ),
      'sched',
    )
    expect(t.value).toBe(KPI_UNKNOWN)
    expect(t.sub).toBe('hub 无应答（队列不可知）')
    expect(t.tone).toBe('warn')
    expect(t.title).toContain('不是 0')
  })

  it('视图里根本没有 overview → 也是 —（未读 ≠ 0）', () => {
    const t = tile(kpiTiles(state({ overview: null }), NOW), 'sched')
    expect(t.value).toBe(KPI_UNKNOWN)
    expect(t.sub).toBe('hub 视图未读（队列不可知）')
  })
})

describe('微型走势：只给有时序的两格（其余不补假平线）', () => {
  it('胜率 / eval 有 spark；阶段、课程、算力、调度没有', () => {
    const tiles = kpiTiles(
      state({
        metrics: {
          available: true,
          iters: [iter({ iter: 1, winRate: 0.1 }), iter({ iter: 2, winRate: 0.3 })],
        },
      }),
      NOW,
    )
    expect(tile(tiles, 'win').spark).toEqual([0.1, 0.3])
    expect(tile(tiles, 'eval').spark).toEqual([]) // eval 无有效点 → 空（组件不画）
    for (const id of ['phase', 'courses', 'fleet', 'sched'] as const) {
      expect(tile(tiles, id).spark).toBeUndefined()
    }
  })
})

describe('KpiStrip SSR 结构', () => {
  // 交互契约：格子成了真按钮**当且仅当**既有 `to` 又有 `onNavigate`（一个点了没反应的
  // 按钮比 div 更坏）。app.tsx 两个都给，故真实页面里可点的格子都是 button。
  const renderStrip = (tiles: KpiTile[], onNavigate?: (p: string) => void): string =>
    renderToString(h(KpiStrip, { tiles, onNavigate }))

  it('空 tiles → 「读盘中」说明（不静默消失）', () => {
    const html = renderStrip([])
    expect(html).toContain('tc-kpi')
    expect(html).toContain('读盘中')
  })

  it('六格：标签 / 大号值 / 单位 / 副读数齐全', () => {
    const tiles = kpiTiles(
      state({ metrics: { available: true, iters: [iter({ iter: 4, winRate: 0.42 })] } }),
      NOW,
    )
    const html = renderStrip(tiles)
    expect(html).toContain('aria-label="关键指标"')
    expect(html.match(/tc-kpi__tile"/g)?.length).toBe(6)
    expect(html).toContain('tc-kpi__label">采样胜率')
    expect(html).toContain('tc-kpi__val--ok">42.0%')
    expect(html).toContain('tc-kpi__unit">待领 job')
    expect(html).toContain('首轮（it4）')
  })

  it('给了 onNavigate：有 to 的格子是真按钮，无 to 的是 div（id 稳定）', () => {
    const tiles = kpiTiles(state({ metrics: { available: true, iters: [iter({ iter: 1 })] } }), NOW)
    const html = renderStrip(tiles, () => undefined)
    for (const id of ['win', 'eval', 'fleet']) {
      expect(html).toContain(`<button type="button" id="kpi-${id}"`)
    }
    // 无 to 的格子（阶段 / 在训课程 / 队列）是 div（不是挂了 onClick 的 div）
    expect(html).toContain('<div id="kpi-phase"')
    expect(html).toContain('<div id="kpi-sched"')
  })

  it('没给 onNavigate：带 to 的格子退成 div（不能是个点了没反应的按钮）', () => {
    const html = renderStrip(
      kpiTiles(state({ metrics: { available: true, iters: [iter({ iter: 1 })] } }), NOW),
    )
    expect(html).not.toContain('<button')
    expect(html).toContain('<div id="kpi-win"')
  })

  it('微型走势只在有值时画 SVG（坏数据空数组不画）', () => {
    const withSpark = renderStrip(
      kpiTiles(
        state({
          metrics: {
            available: true,
            iters: [iter({ iter: 1, winRate: 0.1 }), iter({ iter: 2, winRate: 0.2 })],
          },
        }),
        NOW,
      ),
    )
    expect(withSpark).toContain('tc-kpi__spark')
    expect(withSpark).toContain('<polyline')
    const noSpark = renderStrip(kpiTiles(state(), NOW))
    expect(noSpark).not.toContain('<polyline')
  })

  it('口径写进 title（每格都要能回答「这个数从哪来」）', () => {
    const html = renderStrip(
      kpiTiles(state({ metrics: { available: true, iters: [iter({ iter: 2 })] } }), NOW),
    )
    expect(html).toContain('第 2 轮 rollout 采样胜率')
    expect(html).toContain('共享 trainer 在跑且这门课未收官')
    expect(html).toContain('分母不含停用节点')
  })
})

/**
 * ⚠ 测试盲区（与 `web-status-row.test.ts` / `web-alert-dock.test.ts` 同款）：
 * `preact-render-to-string` 丢弃全部事件处理器，本仓 web 测试无 DOM 夹具 ⇒
 * 「点 KPI 格子会 navigate 到对应页」这条交互**无法被断言**，只能靠读代码评审。
 * 能钉住的是：有 `to` 的格子确实是 `<button type="button">`（而不是 div + onClick）。
 */
