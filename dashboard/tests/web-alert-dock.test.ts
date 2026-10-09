/**
 * web-alert-dock.test.ts — 告警坞：条目派生 / 严重度排序 / 折叠切分 / SSR 结构。
 *
 * 分层：src/web/view/alerts.ts（纯函数）+ src/web/components/AlertDock.tsx（SSR 结构）
 *
 * 事故背景（P2b 收敛前）：首页最多同时堆 6 条**同权重**横幅（停机红 / 停机已恢复灰 /
 * 训练已完成灰 / PPO 排队超时红 / 课程编辑被拒红 / 只读蓝）。它们平级堆在 DNS 里，
 * 于是「红横幅排在蓝横幅下面」既是可能的、也是没人能管住的（顺序 = 代码书写顺序），
 * 而 6 条各占一行恰好在你最需要看主内容时把主内容推出首屏。
 *
 * 本用例钉住收敛后的三件事实：
 *   ① 每条横幅都变成了坞内条目，且**原文信息一字未删**（只是降了排版层级）；
 *   ② 排序由严重度决定，不由书写顺序决定；
 *   ③ 默认只出 2 条，其余折成「还有 N 条」；空的时候整坞不出现。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { AlertDock } from '../src/web/components/AlertDock'
import {
  ALERT_DOCK_DEFAULT_VISIBLE,
  alertDockSplit,
  alertDockSummary,
  buildAlerts,
  NO_TAKER_LABEL,
  parseLoopQueue,
  sortAlerts,
  STUCK_LABEL,
  type AlertInput,
  type AlertItem,
  type DiskFactsView,
} from '../src/web/view'

/** 一件都不告警的基线输入（每条用例只改它关心的那一项）。 */
const clean: AlertInput = {
  cloudHalts: undefined,
  viewing: 'c1',
  acks: [],
  loopCompletes: null,
  ppoQueueStall: null,
  courseEdit: null,
  readOnly: false,
  roDismissed: false,
  now: 1_700_000_000_000,
}

/** 条目夹具（补 `copyText`：`AlertItem` 必填，2026-10-03 G4）。 */
/** 本机磁盘水位夹具（plan/self-node-disk-alert）：五字段与 agent 上报同形。 */
const disk = (over: Partial<DiskFactsView> = {}): DiskFactsView => ({
  freeMB: 1212,
  level: 'warn',
  warnMB: 4096,
  floorMB: 2048,
  since: 1_700_000_000_000,
  ...over,
})

const item = (id: string, severity: AlertItem['severity']): AlertItem => ({
  id,
  severity,
  icon: '·',
  title: id,
  detail: '',
  role: 'status',
  actions: [],
  copyText: id,
})

describe('buildAlerts：七类告警各自成条目，原文不丢', () => {
  it('无任何告警 → 空数组（坞整块不出现，没有「暂无告警」占位）', () => {
    expect(buildAlerts(clean)).toEqual([])
  })

  it('停机中 → err 条，带「立即恢复」（主）+「知道了」', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: { c1: { at: 'T1', reason: '手动停机', status: 'halted' } },
    })
    expect(items.length).toBe(1)
    const a = items[0]!
    expect(a.severity).toBe('err')
    expect(a.role).toBe('alert')
    expect(a.title).toContain('停机中')
    expect(a.title).toContain('手动停机')
    // 动作两种语义必须分开：resume 真调 API，ack 只写本地
    expect(a.actions.map((x) => x.kind)).toEqual(['resume', 'ack'])
    expect(a.actions[0]!.act).toBe('cloud-resume')
    expect(a.actions[0]!.primary).toBe(true)
  })

  it('已恢复 → history 条（灰、温和留痕），只有「知道了」', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: {
        c1: {
          at: 'T1',
          reason: 'r',
          status: 'recovered',
          clearedAt: 'T2',
          clearReason: '恢复训练',
        },
      },
    })
    expect(items[0]!.severity).toBe('history')
    expect(items[0]!.role).toBe('status')
    expect(items[0]!.title).toContain('已恢复')
    expect(items[0]!.detail).toContain('恢复训练')
    expect(items[0]!.actions.map((x) => x.kind)).toEqual(['ack'])
  })

  it('停机条目的原文信息一字未删（收敛的是排版层级，不是信息量）', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: { c1: { at: 'T1', reason: 'r', status: 'halted' } },
    })
    const a = items[0]!
    // 旧横幅那句「停不掉则照常执行任务」是操作员决定要不要干预的依据，必须在。
    expect(a.detail).toContain('停不掉则照常执行任务')
    expect(a.detail).toContain('本地 hub/console 均正常')
  })

  it('ack 后不再出现（按事件身份：新一次停机是新 key）', () => {
    const halts = { c1: { at: 'T1', reason: 'r', status: 'halted' as const } }
    const first = buildAlerts({ ...clean, cloudHalts: halts })
    expect(first.length).toBe(1)
    const acked = buildAlerts({
      ...clean,
      cloudHalts: halts,
      acks: [first[0]!.actions[1]!.ackKey!],
    })
    expect(acked).toEqual([])
    // 新一次停机（at 变了）→ 新身份 → 重新出现
    const again = buildAlerts({
      ...clean,
      cloudHalts: { c1: { at: 'T2', reason: 'r', status: 'halted' } },
      acks: [first[0]!.actions[1]!.ackKey!],
    })
    expect(again.length).toBe(1)
  })

  it('别课的停机记录不进坞（停机不看当前课 = c6-chip 横幅关不掉那次事故）', () => {
    const items = buildAlerts({
      ...clean,
      viewing: 'c1',
      cloudHalts: { c2: { at: 'T1', reason: 'r', status: 'halted' } },
    })
    expect(items).toEqual([])
  })

  it('PPO 排队·无人取超时 → err；与 pill 同词，且与「卡住」划清界限（2026-10-02 口径对齐）', () => {
    const items = buildAlerts({
      ...clean,
      ppoQueueStall: { jobId: 'abcdef1234567890', waitedSec: 305, it: 37 },
    })
    expect(items[0]!.severity).toBe('err')
    expect(items[0]!.title).toContain('5 分5 秒')
    expect(items[0]!.detail).toContain('it37')
    // ★ 跨面同词：dock 的说法与 pill 对同一局面给出的状态字**同一个常量**——两处不得
    //   各起一个名字（「同一句话不说两遍」的落点；旧词「排队超时」不得回流）。
    expect(items[0]!.title).toContain(`PPO ${NO_TAKER_LABEL}`)
    expect(items[0]!.title).not.toContain('排队超时')
    // ★ 反向：dock 结构上不覆盖有持有者的卡死（不是同一个信号）——detail 必须点名 pill 的
    //   「卡住」是另一类，防两类被读成一类。
    expect(items[0]!.detail).toContain(STUCK_LABEL)
    // 没有 it 时回退 jobId 前缀（不能显示空身份）
    const noIter = buildAlerts({
      ...clean,
      ppoQueueStall: { jobId: 'abcdef1234567890', waitedSec: 305, it: null },
    })
    expect(noIter[0]!.detail).toContain('abcdef123456')
  })

  it('课程编辑被拒 → err；verdict 不是 rejected 时不出现', () => {
    const rejected = buildAlerts({
      ...clean,
      courseEdit: { verdict: 'rejected', fields: ['stages', 'difficulty'] },
    })
    expect(rejected[0]!.severity).toBe('err')
    expect(rejected[0]!.title).toContain('stages、difficulty')
    expect(rejected[0]!.detail).toContain('D14')
    for (const verdict of ['applied', 'restored'] as const) {
      expect(buildAlerts({ ...clean, courseEdit: { verdict, fields: [] } })).toEqual([])
    }
  })

  it('读写模式提示 → info（不是告警，排在错误之后）；关掉后不再出现', () => {
    const items = buildAlerts({ ...clean, readOnly: true })
    expect(items[0]!.severity).toBe('info')
    expect(items[0]!.actions[0]!.ackKey).toBe('ro-banner-dismissed')
    expect(buildAlerts({ ...clean, readOnly: true, roDismissed: true })).toEqual([])
  })

  it('训练完成停车 → history（设计内停车）；按课成列、标题带课名、可关闭可复制', () => {
    // 传入的就是快照里的 `LoopComplete` 原形（at/reason/iters）——`AlertInput` 收的就是它。
    const items = buildAlerts({
      ...clean,
      // 故意乱序传入：输出必须按课程名排序（确定性，不依赖对象键序）。
      loopCompletes: {
        b: { at: '2026-09-20T11:00:00Z', reason: 'iters 跑满', iters: 40 },
        a: { at: '2026-09-20T10:00:00Z', reason: 'iters 跑满', iters: 40 },
      },
    })
    expect(items.map((x) => x.id)).toEqual(['loop-complete:a', 'loop-complete:b'])
    expect(items.map((x) => x.title)).toEqual([
      '课程 a 训练已完成（iters 跑满）',
      '课程 b 训练已完成（iters 跑满）',
    ])
    const a = items[0]!
    expect(a.severity).toBe('history')
    expect(a.actions.map((x) => x.kind)).toEqual(['ack'])
    expect(a.actions[0]!.ackKey).toBe('loop-complete|a|2026-09-20T10:00:00Z')
    expect(a.copyText).toContain('课程 a')
    expect(a.copyText).toContain(a.title)
  })

  // ────────────────────────── T8：离线静默停摆（plan/auto-offline-handoff §3.9） ──────────────────────────

  it('静默停摆（pending-export）→ err 条：点名三条出路，且**不给**「强制解除接管」（本来就没接管）', () => {
    const items = buildAlerts({
      ...clean,
      offlineStalls: [
        {
          course: 'c5-gae',
          why: 'pending-export',
          holder: '',
          lastMtime: 0,
          flippedAt: 100,
          ageSec: 3600,
        },
      ],
    })
    expect(items.length).toBe(1)
    const a = items[0]!
    expect(a.severity).toBe('err') // 最典型的静默停摆：本机不采样 + 云机没跑 ⇒ 红
    expect(a.title).toContain('c5-gae')
    expect(a.title).toContain('云机没接手')
    expect(a.title).toContain('1 小时')
    // 三条出路逐条点名（U3 的出口就在这条告警里）
    expect(a.detail).toContain('云机重连继续')
    expect(a.detail).toContain('手工导入结果包')
    // ★M4：第三条出路不再是「交还自动池」（那个动作没了）——而是**本机什么都不用做**
    // （pending_export 不占闸，采样与协作派发照跑）。
    expect(a.detail).toContain('本机不需要做任何事')
    // ★M4：没有接管 ⇒ **不给** release 动作（点下去必 409 的键就是假承诺）——ack 是唯一出口。
    expect(a.actions.map((x) => x.kind)).toEqual(['ack'])
    expect(a.actions[0]!.ackKey).toBe(`offline-stall|c5-gae|100`)
  })

  it('running-stale（有租约但无进度）→ warn 条，标题带上持有人', () => {
    const items = buildAlerts({
      ...clean,
      offlineStalls: [
        {
          course: 'c5-gae',
          why: 'running-stale',
          holder: 'kaggle-tpu-7',
          lastMtime: 0,
          flippedAt: 0,
          ageSec: 2400,
        },
      ],
    })
    const a = items[0]!
    expect(a.severity).toBe('warn') // 可能只是长轮，橙
    expect(a.title).toContain('kaggle-tpu-7')
    expect(a.title).toContain('40 分钟')
  })

  // ── P2-1：租约两态文案（stale-holder = 可接管；revoked = 已撤租墓碑） ──

  it('stale-holder：租约静默超阈 ⇒ 文案点名「可接管」（自愈路径，不写成云机死透）', () => {
    const items = buildAlerts({
      ...clean,
      offlineStalls: [
        {
          course: 'c5-gae',
          why: 'running-stale',
          holder: 'kaggle-tpu-7',
          lastMtime: 0,
          flippedAt: 0,
          ageSec: 2400,
        },
      ],
      offlineLeases: {
        'c5-gae': {
          workerId: 'kaggle-tpu-7',
          silentSec: 812,
          stale: true,
          revoked: false,
          expiresIn: 0,
        },
      },
    })
    const a = items[0]!
    expect(a.detail).toContain('租约状态：持有者已静默 812s 超阈（可接管）')
    expect(a.detail).toContain('自动回收')
    expect(a.detail).toContain('下一拍自愈')
  })

  it('revoked：墓碑 ⇒ 文案点名「已撤租」+ 新盘可直接覆盖（不需要人工清理）', () => {
    const items = buildAlerts({
      ...clean,
      offlineStalls: [
        {
          course: 'c5-gae',
          why: 'running-stale',
          holder: 'kaggle-tpu-7',
          lastMtime: 0,
          flippedAt: 0,
          ageSec: 2400,
        },
      ],
      offlineLeases: {
        'c5-gae': {
          workerId: 'kaggle-tpu-7',
          silentSec: 0,
          stale: false,
          revoked: true,
          expiresIn: 0,
        },
      },
    })
    const a = items[0]!
    expect(a.detail).toContain('租约状态：已撤租（墓碑）')
    expect(a.detail).toContain('409 revoked')
    expect(a.detail).toContain('新盘 claim 可直接覆盖墓碑')
  })

  it('租约表缺失（旧 hub）⇒ 文案不编租约事实（原文照旧）', () => {
    const items = buildAlerts({
      ...clean,
      offlineStalls: [
        {
          course: 'c5-gae',
          why: 'running-stale',
          holder: 'kaggle-tpu-7',
          lastMtime: 0,
          flippedAt: 0,
          ageSec: 2400,
        },
      ],
    })
    const a = items[0]!
    expect(a.detail).not.toContain('租约状态')
  })

  it('hub 不可达/旧版（null/缺省）⇒ 一条都不画（不可知 ≠ 没停）', () => {
    expect(buildAlerts({ ...clean, offlineStalls: null })).toEqual([])
    expect(buildAlerts({ ...clean, offlineStalls: [] })).toEqual([])
  })

  it('七类全开 → 各自成条（含多课收官与离线停摆；收敛前是 6 个平级堆叠的横幅）', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: {
        c1: { at: 'T1', reason: 'r', status: 'halted' },
        // 别课的停机不进坞（只取当前课）——它由课程矩阵的徽标承载
        c2: { at: 'T2', reason: 'r', status: 'halted' },
      },
      loopCompletes: { 'c5-gae': { at: 'T', reason: 'r', iters: 1 } },
      ppoQueueStall: { jobId: 'j', waitedSec: 601, it: 1 },
      offlineStalls: [
        {
          course: 'c5-gae',
          why: 'pending-export',
          holder: '',
          lastMtime: 0,
          flippedAt: 100,
          ageSec: 3600,
        },
      ],
      courseEdit: { verdict: 'rejected', fields: [] },
      readOnly: true,
    })
    expect(items.map((a) => a.id)).toEqual([
      'halt-c1',
      'loop-complete:c5-gae',
      'ppo-queue-stall',
      'offline-stall-c5-gae',
      'course-edit-rejected',
      'read-only',
    ])
    // 严重度分布：4 err（停机 / 排队超时 / 停摆 / 编辑被拒）→ 1 info（只读）→ 1 history（训练完成）
    expect(sortAlerts(items).map((a) => a.severity)).toEqual([
      'err',
      'err',
      'err',
      'err',
      'info',
      'history',
    ])
  })

  it('只读提示的字段缺失文案：未识别字段（不显示空括号）', () => {
    const items = buildAlerts({ ...clean, courseEdit: { verdict: 'rejected', fields: [] } })
    expect(items[0]!.title).toContain('未识别字段')
  })
})

describe('全局收官 + 全条目可关闭可复制（2026-10-03 plan/dashboard-banner-global G1–G5）', () => {
  /** 全类目开满：每类至少一个条目（G3/G4 的穷举驱动集）。 */
  const allOn = (): AlertInput => ({
    ...clean,
    cloudHalts: {
      c1: { at: 'T1', reason: 'r', status: 'halted' },
      c9: { at: 'T3', reason: 'r', status: 'recovered', clearedAt: 'T4' },
      c2: { at: 'T2', reason: 'r', status: 'halted' }, // 别课停机：不进坞（N1）
    },
    loopCompletes: {
      b: { at: '2026-09-20T11:00:00Z', reason: 'iters 跑满', iters: 40 },
      a: { at: '2026-09-20T10:00:00Z', reason: 'iters 跑满', iters: 40 },
    },
    ppoQueueStall: { jobId: 'j1', waitedSec: 601, it: 7 },
    offlineStalls: [
      {
        course: 'c5-gae',
        why: 'pending-export',
        holder: '',
        lastMtime: 0,
        flippedAt: 100,
        ageSec: 3600,
      },
      {
        course: 'c6-chip',
        why: 'running-stale',
        holder: 'kaggle-tpu-7',
        lastMtime: 42,
        flippedAt: 0,
        ageSec: 2400,
      },
    ],
    courseEdit: { verdict: 'rejected', fields: ['reward'], at: '2026-10-03 10:00:00' },
    selfDisk: disk({ level: 'critical', freeMB: 1212 }),
    readOnly: true,
  })

  // 停机「已恢复」与「停机中」互斥（同一课同一时刻只有一态），不能塞进同一份输入；
  // 其余每类都已在 allOn 里（含本机磁盘，plan/self-node-disk-alert）。两份输入拼起来 = 八类 / 十条全覆。
  const recovered = (): AlertInput => ({
    ...clean,
    cloudHalts: {
      c1: { at: 'T1', reason: 'r', status: 'recovered', clearedAt: 'T2', clearReason: '恢复训练' },
    },
  })
  const allItems = (): AlertItem[] => [allOn(), recovered()].flatMap((i) => buildAlerts(i))

  it('G1：视图课程 A、B 收官 ⇒ 不切课也能看到 B（且可分辨）', () => {
    const items = buildAlerts({
      ...clean,
      viewing: 'A',
      loopCompletes: { B: { at: 'T', reason: 'iters 跑满', iters: 3 } },
    })
    expect(items.map((a) => a.id)).toEqual(['loop-complete:B'])
    expect(items[0]!.title).toContain('课程 B')
  })

  it('G3：全部条目都有 kind=ack（穷举 buildAlerts 产出集，不手写类目清单）', () => {
    const items = allItems()
    expect(items.length).toBeGreaterThanOrEqual(10) // 守卫：穷举集非空（防空跑；含磁盘那条）
    for (const a of items) {
      const acks = a.actions.filter((x) => x.kind === 'ack')
      expect(acks.length).toBeGreaterThanOrEqual(1)
      expect(acks[0]!.ackKey).toBeTruthy()
    }
  })

  it('G4：全部条目都有三行 copyText（title/detail/元信息），且含条目 id', () => {
    for (const a of allItems()) {
      const lines = a.copyText.split('\n')
      expect(lines.length).toBe(3)
      expect(lines[0]).toBe(a.title)
      expect(lines[1]).toBe(a.detail)
      const meta = lines[2]!
      expect(meta.startsWith('（')).toBe(true)
      expect(meta.endsWith('）')).toBe(true)
      expect(meta).toContain(`条目 ${a.id}`)
      expect(meta).toContain(`严重度 ${a.severity}`)
    }
  })

  it('G5：同课两次收官（at 不同）⇒ 两个键；ack 第一次后第二次仍在', () => {
    const done = (at: string): AlertInput => ({
      ...clean,
      loopCompletes: { a: { at, reason: 'r', iters: 1 } },
    })
    const first = buildAlerts(done('T1'))
    const key1 = first[0]!.actions[0]!.ackKey!
    expect(buildAlerts({ ...done('T1'), acks: [key1] })).toEqual([])
    const again = buildAlerts({ ...done('T2'), acks: [key1] })
    expect(again.length).toBe(1)
    expect(again[0]!.actions[0]!.ackKey).not.toBe(key1)
  })

  it('G3/G5：「知道了」后条目真的消失（逐类各验一条；只读提示走 roDismissed 除外）', () => {
    const cases: AlertInput[] = [
      { ...clean, cloudHalts: { c1: { at: 'T1', reason: 'r', status: 'halted' } } },
      {
        ...clean,
        cloudHalts: { c1: { at: 'T1', reason: 'r', status: 'recovered', clearedAt: 'T2' } },
      },
      { ...clean, loopCompletes: { a: { at: 'T', reason: 'r', iters: 1 } } },
      { ...clean, ppoQueueStall: { jobId: 'j1', waitedSec: 601, it: 7 } },
      {
        ...clean,
        offlineStalls: [
          {
            course: 'c5-gae',
            why: 'pending-export',
            holder: '',
            lastMtime: 0,
            flippedAt: 100,
            ageSec: 3600,
          },
        ],
      },
      { ...clean, courseEdit: { verdict: 'rejected', fields: ['reward'], at: 'T' } },
    ]
    for (const inp of cases) {
      const items = buildAlerts(inp)
      expect(items.length).toBe(1) // 守卫：每类单独成条（防空跑）
      const key = items[0]!.actions.find((x) => x.kind === 'ack')!.ackKey!
      expect(buildAlerts({ ...inp, acks: [key] })).toEqual([])
    }
  })

  it('G5：停机 ack 键逐字节不变（升级不重弹；单表冻结值）', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: { c1: { at: 'T1', reason: 'r', status: 'halted' } },
    })
    // 旧格式键（cloudHaltAckKey = alertAckKey 委托；kind 不得改名）
    expect(items[0]!.actions[1]!.ackKey).toBe('halted|c1|T1')
  })

  it('G6：关闭只在本地（ack 是唯一新增出口；没有一个条目新增 act）', () => {
    for (const a of allItems()) {
      for (const act of a.actions) {
        if (act.kind === 'ack') continue
        // 既有 resume 动作的 act 名只允许是既有 2 个（不新增服务端消警路径）
        expect(act.act).toBeTruthy()
        expect(['cloud-resume', 'releaseCourseHold']).toContain(act.act!)
      }
    }
  })
})

describe('sortAlerts：严重度决定顺序，书写顺序只在同级内保持', () => {
  it('err → warn → info → history（与输入顺序无关）', () => {
    const input = [item('h', 'history'), item('i', 'info'), item('e', 'err'), item('w', 'warn')]
    expect(sortAlerts(input).map((a) => a.id)).toEqual(['e', 'w', 'i', 'h'])
  })

  it('同级保持输入顺序（稳定排序：同一局面重复渲染不跳位）', () => {
    const input = [item('a', 'err'), item('b', 'err'), item('c', 'warn'), item('d', 'warn')]
    expect(sortAlerts(input).map((a) => a.id)).toEqual(['a', 'b', 'c', 'd'])
  })

  it('空数组 / 单条不炸', () => {
    expect(sortAlerts([])).toEqual([])
    expect(sortAlerts([item('a', 'err')]).map((a) => a.id)).toEqual(['a'])
  })
})

describe('alertDockSplit：默认 2 条 + 「还有 N 条」', () => {
  const five = [
    item('h', 'history'),
    item('i', 'info'),
    item('e1', 'err'),
    item('w1', 'warn'),
    item('e2', 'err'),
  ]

  it('默认折叠：先按严重度排，再取前 2 条', () => {
    const { visible, hidden } = alertDockSplit(five, false)
    expect(ALERT_DOCK_DEFAULT_VISIBLE).toBe(2)
    expect(visible.map((a) => a.id)).toEqual(['e1', 'e2'])
    expect(hidden.map((a) => a.id)).toEqual(['w1', 'i', 'h'])
  })

  it('展开后全部可见（hidden 空）', () => {
    const { visible, hidden } = alertDockSplit(five, true)
    expect(visible.length).toBe(5)
    expect(hidden).toEqual([])
  })

  it('条数 ≤ cap 时不折叠（没有「还有 0 条」这种话）', () => {
    const two = [item('a', 'err'), item('b', 'err')]
    expect(alertDockSplit(two, false).hidden).toEqual([])
    expect(alertDockSplit([], false)).toEqual({ visible: [], hidden: [] })
  })

  it('alertDockSummary：有隐藏才给文案，否则空串', () => {
    expect(alertDockSummary(5, 3)).toBe('3 条未展开（共 5 条）')
    expect(alertDockSummary(2, 0)).toBe('')
  })
})

describe('AlertDock SSR 结构', () => {
  // 本仓 web 测试不用 JSX（`.ts` 夹具 + `h()`）——与 `web-components.test.ts` 同款。
  const renderDock = (items: AlertItem[], cap?: number): string =>
    renderToString(
      h(AlertDock, {
        items,
        cap,
        onAct: () => undefined,
        onAck: () => undefined,
      }),
    )

  it('空坞不渲染任何节点（不留空壳）', () => {
    expect(renderDock([])).toBe('')
  })

  it('条目：角色 / 严重度修饰类 / 结论 + 依据两行', () => {
    const html = renderDock([
      {
        id: 'x',
        severity: 'err',
        icon: '⚠',
        title: '一句话结论',
        detail: '依据与指引',
        role: 'alert',
        actions: [],
        copyText: '一句话结论\n依据与指引\n（条目 x · 严重度 err）',
      },
    ])
    expect(html).toContain('class="tc-dock"')
    expect(html).toContain('tc-dock__item tc-dock__item--err')
    expect(html).toContain('role="alert"')
    expect(html).toContain('tc-dock__title">一句话结论')
    expect(html).toContain('tc-dock__detail">依据与指引')
  })

  it('默认只渲染 2 条 + 「还有 N 条 ▸」按钮（3 条时）', () => {
    const html = renderDock([item('a', 'err'), item('b', 'err'), item('c', 'err')])
    expect(html).toContain('tc-dock__more')
    expect(html).toContain('还有 1 条 ▸')
    expect(html.match(/tc-dock__item /g)?.length).toBe(2)
    // 折叠态是客户端 state、初值确定（SSR 与 hydrate 首帧一致）→ aria-expanded="false"
    expect(html).toContain('aria-expanded="false"')
  })

  it('条数 ≤ cap：不出现折叠按钮，也没有「收起」', () => {
    const html = renderDock([item('a', 'err'), item('b', 'warn')])
    expect(html).not.toContain('tc-dock__more')
    expect(html).not.toContain('收起')
  })

  it('动作渲染真按钮（主按钮带 tc-btn--primary）', () => {
    const html = renderDock([
      {
        id: 'x',
        severity: 'err',
        icon: '⚠',
        title: 't',
        detail: 'd',
        role: 'alert',
        copyText: 't\nd\n（条目 x · 严重度 err）',
        actions: [
          {
            kind: 'resume',
            label: '立即恢复',
            act: 'cloud-resume',
            body: { course: 'c' },
            primary: true,
          },
          { kind: 'ack', label: '知道了', ackKey: 'k' },
        ],
      },
    ])
    expect(html).toContain('tc-btn tc-btn--sm tc-btn--primary')
    expect(html).toContain('>立即恢复</button>')
    expect(html).toContain('>知道了</button>')
  })

  it('复制键：每条都有（icon 模式 / aria-label），且位于动作区**之前**（不抢主按钮位）', () => {
    const html = renderDock([
      {
        id: 'x',
        severity: 'err',
        icon: '⚠',
        title: 't',
        detail: 'd',
        role: 'alert',
        copyText: 't\nd\n（条目 x · 严重度 err）',
        actions: [{ kind: 'ack', label: '知道了', ackKey: 'k' }],
      },
    ])
    expect(html).toContain('aria-label="复制告警"')
    expect(html).toContain('tc-copy--icon')
    const copyIdx = html.indexOf('tc-dock__copy')
    const actsIdx = html.indexOf('tc-dock__acts')
    expect(copyIdx).toBeGreaterThan(-1)
    expect(actsIdx).toBeGreaterThan(-1)
    expect(copyIdx).toBeLessThan(actsIdx)
  })
})

describe('第 8 类：课程配置不可开课（2026-10-05，plan/course-startup-recover §3.3）', () => {
  /** 走真实解析路径造队列行（与 /api/state 同一份数据）——不手拼 LoopQueueRow。 */
  const rowsFor = (reason: string, ok = false): AlertInput['loopQueueRows'] =>
    parseLoopQueue({
      courses: [
        {
          course: 'c1',
          openable: { ok, reason: ok ? '' : reason },
          waiting: {
            kind: ok ? 'ready' : 'blocked',
            text: ok ? '无外部等待' : `课程起不来：${reason}`,
          },
        },
      ],
    })!.rows

  it('起不来的课 ⇒ 恰好一条 err：结论先行 + 文件指针 + 生效路径；只给「知道了」（无一键恢复）', () => {
    const items = buildAlerts({
      ...clean,
      loopQueueRows: rowsFor('SystemExit: kickstart_ref 没开'),
    })
    expect(items.length).toBe(1)
    const a = items[0]!
    expect(a.severity).toBe('err')
    expect(a.title).toContain('c1')
    expect(a.title).toContain('kickstart_ref 没开')
    expect(a.detail).toContain('nn-training/curricula/c1.jsonc')
    expect(a.detail).toContain('不需要重启 trainer')
    expect(a.detail).toContain('正在跑的课不受影响')
    // 恢复动作是「改课程文件」（代码编辑）——控制台没有、也不该有那个按钮（§2.2）。
    expect(a.actions.map((x) => x.kind)).toEqual(['ack'])
  })

  it('ok=true ⇒ 零条；读面不在手（旧控制台 / python 读失败）⇒ 零条（不编「起不来」）', () => {
    expect(buildAlerts({ ...clean, loopQueueRows: rowsFor('', true) })).toEqual([])
    expect(buildAlerts({ ...clean, loopQueueRows: null })).toEqual([])
    expect(buildAlerts(clean)).toEqual([])
  })

  it('ack 后消失；**reason 变了** ⇒ 新事件重新弹（改了但没修好必须再看见一次）', () => {
    const first = buildAlerts({ ...clean, loopQueueRows: rowsFor('旧原因') })
    const ackKey = first[0]!.actions[0]!.ackKey!
    expect(buildAlerts({ ...clean, loopQueueRows: rowsFor('旧原因'), acks: [ackKey] })).toEqual([])
    const second = buildAlerts({ ...clean, loopQueueRows: rowsFor('新原因：还是不行') })
    expect(second.length).toBe(1)
    expect(second[0]!.actions[0]!.ackKey).not.toBe(ackKey)
  })

  it('可见范围按课过滤：切到别的课不弹（它有恢复动作且会被自己修好的配置解除，与停机同族）', () => {
    const items = buildAlerts({
      ...clean,
      viewing: 'c2',
      loopQueueRows: rowsFor('boom'),
    })
    expect(items).toEqual([])
  })

  it('条目真的进坞（SSR：结论 + 依据两行都在 DOM 里）', () => {
    const items = buildAlerts({ ...clean, loopQueueRows: rowsFor('boom') })
    const html = renderToString(
      h(AlertDock, { items, onAct: () => undefined, onAck: () => undefined }),
    )
    expect(html).toContain('课程配置不可开课')
    expect(html).toContain('boom')
    expect(html).toContain('>知道了</button>')
  })
})

describe('本机磁盘水位（plan/self-node-disk-alert：告警坞新一类）', () => {
  it('ok ⇒ 不出条目；selfDisk 缺省/null ⇒ 不出条目（旧 agent 不编事实）', () => {
    expect(buildAlerts({ ...clean, selfDisk: disk({ level: 'ok' }) })).toEqual([])
    expect(buildAlerts({ ...clean, selfDisk: null })).toEqual([])
    expect(buildAlerts(clean)).toEqual([])
  })

  it('warn ⇒ 1 条 warn 级、role=alert、ack 键 = 档位×进档时刻', () => {
    const items = buildAlerts({ ...clean, selfDisk: disk() })
    expect(items.length).toBe(1)
    const a = items[0]!
    expect(a.severity).toBe('warn')
    expect(a.role).toBe('alert')
    expect(a.title).toContain('1212MB')
    expect(a.actions.map((x) => x.ackKey)).toEqual(['self-disk|self|warn@1700000000000'])
  })

  it('★ 回差带（评审 F3）：critical 但 freeMB >= floorMB ⇒ 不得说「已在拒收作业」', () => {
    const a = buildAlerts({ ...clean, selfDisk: disk({ level: 'critical', freeMB: 2500 }) })[0]!
    expect(a.severity).toBe('err')
    expect(a.title).not.toContain('拒收')
    expect(a.detail).toContain('尚未跌破地板')
  })

  it('跌破地板 ⇒ 红条 + 「已在拒收作业」；detail 含 503/停派与两条阈值（G3）', () => {
    const a = buildAlerts({ ...clean, selfDisk: disk({ level: 'critical', freeMB: 1212 }) })[0]!
    expect(a.severity).toBe('err')
    expect(a.title).toContain('已在拒收作业')
    expect(a.detail).toContain('503')
    expect(a.detail).toContain('停派')
    expect(a.detail).toContain('2048')
    expect(a.detail).toContain('4096')
  })

  it('★ 档位内 ack 后不再出；回 ok 后再跌破 / 升档 ⇒ 必重弹（评审 F2）', () => {
    const acked = [...clean.acks, 'self-disk|self|warn@1700000000000']
    expect(buildAlerts({ ...clean, acks: acked, selfDisk: disk() })).toEqual([])
    // 同一档位、新的进档时刻（跌 → 回 ok → 再跌）= 新 episode ⇒ 重弹
    expect(
      buildAlerts({ ...clean, acks: acked, selfDisk: disk({ since: 1_700_000_999_000 }) }).length,
    ).toBe(1)
    // 升档（换档换键）⇒ 必重弹
    const up = buildAlerts({
      ...clean,
      acks: acked,
      selfDisk: disk({ level: 'critical', freeMB: 1212 }),
    })
    expect(up.length).toBe(1)
    expect(up[0]!.severity).toBe('err')
  })

  it('★ 磁盘 err 排在停机 err 之前（同严重度稳定序：最会变糟的那条不被折叠）', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: { c1: { at: 'T1', reason: '手动停机', status: 'halted' } },
      selfDisk: disk({ level: 'critical', freeMB: 1212 }),
    })
    expect(items.map((a) => a.id)).toEqual(['self-disk-critical', 'halt-c1'])
  })
})

/**
 * ⚠ 测试盲区（与 `web-status-row.test.ts` 同款限制，必须让后来者知道）：
 * `preact-render-to-string` **丢弃全部事件处理器**（`h('button', {onClick}, 'x')`
 * 渲染出的 HTML 里没有 onClick），而本仓 web 测试**全是 SSR、无 DOM 夹具**。
 * 因此「点【还有 N 条】会展开 / 点动作会 invoke onAct」这类**交互**缺陷，任何断言都拦不住。
 * 这里能钉住的只有结构（条目数、类名、角色、折叠按钮的存在与文案）；
 * 交互正确性靠读代码评审。若要补，先引入 DOM 夹具（守零新依赖纪律，不要默默加）。
 */
