/**
 * web-app-coursematrix.test.ts — 课程矩阵面板 SSR（P2：合并「并行课程总览」+「训练调度器」）
 *
 * 分层：src/web/app/panels/CourseMatrix.tsx（SSR，无 DOM）
 *
 * 这个文件是原 `web-app-course-overview.test.ts` 与 `web-app-loopqueue.test.ts` 里**面板断言**
 * 的汇合点——两张表合成一张，断言也就只能落在一处。原则：**只改结构断言，口径与文案断言原样保留**
 * （见 docs/dashboard-redesign.md §6 P2）。凡是从旧文件搬过来的断言，都在用例里注明了它守的是什么，
 * 免得下一个人以为可以顺手删。
 *
 * 纯函数（join 规则 / 状态判定 / 接管列）在 `web-course-matrix.test.ts`；本文件只管
 * 「操作员能从屏幕上读出什么」。点击行为 SSR 渲染不出来（`preact-render-to-string` 丢弃
 * 事件处理器），故动作接线用**源码接线断言**兜底。
 */

import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync } from 'fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import type {
  ArchivedCourseView,
  CourseOverviewRow,
  HubHoldView,
  LoopQueueView,
  ParallelOverviewView,
} from '../src/web/view'
import { parseLoopQueue, withPausedFacts, withTraining } from '../src/web/view'

// ────────────────────────── 夹具 ──────────────────────────

/** hub 侧一行。 */
function ovRow(patch: Partial<CourseOverviewRow> & { course: string }): CourseOverviewRow {
  return {
    training: false,
    iter: null,
    // ★M4：接管（hold）是「谁在跑这门课」的唯一真源——旧 `offline` 布尔已退役。
    hold: null,
    pendingExport: null,
    hubSeen: true,
    queuePending: 0,
    inflight: 0,
    offlineRounds: 0,
    offlineLastIter: null,
    offlineLastMtime: 0,
    frozen: [],
    halt: false,
    inflightDetail: [],
    stuckSec: null,
    peeked: null,
    nextJob: null,
    ...patch,
  }
}

const NOW_SEC = Math.floor(Date.now() / 1000)

function ovView(
  rows: CourseOverviewRow[],
  patch: Partial<ParallelOverviewView> = {},
): ParallelOverviewView {
  return {
    hubUrl: 'http://127.0.0.1:18787',
    hubOnline: true,
    activeCourses: 2,
    activeWorkers: 3,
    halt: false,
    recentDispatch: 'c4',
    offlineProgress: null,
    rows,
    ...patch,
  }
}

/** 接管夹具（★M4）。 */
function hold(workerId: string, state: 'live' | 'stale' = 'live'): HubHoldView {
  return { workerId, state, lastProgressAt: 0, at: 0, expiresIn: 870 }
}

/** 五态各一行（覆盖全部状态档 + 两个冲突态）。 */
function defaultOverview(): ParallelOverviewView {
  return ovView([
    // 在训（hub 也认识它）
    ovRow({ course: 'c4', training: true, iter: 42, hubSeen: true, queuePending: 2, inflight: 1 }),
    // 接管中（云机）+ 段内已回传 3 轮（账本里没有这些行）
    ovRow({
      course: 'c5',
      training: true,
      iter: 7,
      hold: hold('tpu-1'),
      hubSeen: true,
      offlineRounds: 3,
      offlineLastIter: 9,
      offlineLastMtime: NOW_SEC - 60,
    }),
    // ★冲突：在训但 hub 没注册 → rollout 白跑
    ovRow({ course: 'ghost', training: true, iter: null, hubSeen: false }),
    // ★冲突：hub 在线且认识它，但没有进程 → job 堆着没人消费（合并前它长得像「停」）
    ovRow({ course: 'stalled', training: false, iter: 3, hubSeen: true }),
    // 真的没在训（hub 也不认识它）
    ovRow({ course: 'dead', training: false, iter: null, hubSeen: false }),
  ])
}

/** 训练侧一行（形状与 `run_rl_cluster.build_rows` 一致）。 */
function lqRaw(patch: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    course: 'c4',
    it: 37,
    state: 'ready',
    current: 'ppo',
    pending: ['ppo', 'eval_join'],
    inflight: [],
    facts: {},
    waiting: { kind: 'ready', text: '无外部等待，下一步 ppo' },
    ...patch,
  }
}

function queueView(
  courses: Record<string, unknown>[],
  training: string[],
  pause: { intent?: string[]; applied?: string[] } = {},
): LoopQueueView {
  const v = parseLoopQueue({
    courses,
    pools: { local_ppo: { held: 1, capacity: 1 }, eval_local: { held: 0, capacity: 1 } },
  })!
  return withPausedFacts(withTraining(v, training), pause.intent ?? [], pause.applied ?? [])
}
async function render(
  props: {
    overview?: ParallelOverviewView | null
    loopQueue?: LoopQueueView | null
    /** 已封存课程（`stateView.archived`）——缺省 = 旧视图/未封存过。 */
    archived?: ArchivedCourseView[] | null
    course?: string
    onAction?: (act: string, body: Record<string, unknown>) => void
  } = {},
): Promise<string> {
  // ★M4：`modeIntents` / `courseRolloutSrc` 两个入参随模式语义退役（见面板内的注释）。
  const { CourseMatrix } = await import('../src/web/app/panels/CourseMatrix')
  return renderToString(
    h(CourseMatrix, {
      overview: props.overview === undefined ? defaultOverview() : props.overview,
      loopQueue: props.loopQueue === undefined ? queueView([lqRaw()], ['c4']) : props.loopQueue,
      course: props.course ?? 'c4',
      onSelectCourse: () => {},
      onAction: props.onAction,
      archived: props.archived,
    }),
  )
}

// ────────────────────────── 合并后的状态列（新增信号） ──────────────────────────

describe('课程矩阵：只列在训课程（2026-09-20 用户指令）', () => {
  it('在训三态同屏可分辨：在训 / 接管中（云机） / 在训·hub 未注册', async () => {
    const html = await render()
    expect(html).toContain('在训')
    expect(html).toContain('接管中（云机）')
    expect(html).toContain('在训 · hub 未注册')
    // 徽章档位：在训 g / 接管 a / 冲突 y（fixture 里三行都在训）
    expect(html).toContain('tc-badge--g')
    expect(html).toContain('tc-badge--a')
    expect(html).toContain('tc-badge--y')
  })

  it('★ 未在训的行不上屏（含「hub 已注册·无进程」这种未在训的冲突行），但计数上屏', async () => {
    // 「hub 已注册·无进程」（stalled）以前是这张表的头等信号，但它是**未在训**的行（training=false）
    // ⇒ 按用户口径不上屏。代价就在这里，所以这条用例必须存在：它守住「滤掉的是未在训的行」
    // 而不是「凡是冲突都滤掉」（ghost 行在训且冲突，它必须留着）。
    const html = await render()
    // 行名渲染体是 `<b>{course}</b>`（标题在 aria-label/title 上）——未上屏 = 没有这个行名
    expect(html).not.toContain('<b>stalled</b>')
    expect(html).not.toContain('<b>dead</b>')
    expect(html).toContain('未在训 2 门未列')
    // 悬停里逐门点名 + 各自状态（「没上屏」不等于「不存在」——历史课会积几十门）
    expect(html).toContain('stalled —— hub 已注册 · 无进程')
    expect(html).toContain('dead —— 未在训')
  })

  it('冲突行整行标出（data-conflict），并在悬停里说清后果', async () => {
    const html = await render()
    // 在训且 hub 没注册：行留着且整行标出
    expect(html).toContain('data-conflict="hub-unregistered"')
    expect(html).toContain('PPO job 永远不会被派发')
  })

  it('★ 0 门在训 ⇒ 空态显因（不整块消失，也不谎称「没有课程」）', async () => {
    // 有课、一门都没在训（overview 也要显式给，否则默认夹具里那三行都是在训的）
    const html = await render({ overview: null, loopQueue: queueView([lqRaw()], []) })
    expect(html).toContain('当前没有在训课程')
    expect(html).toContain('开课走侧栏')
    expect(html).not.toContain('tc-mx__table') // 表格本身不渲染（不是渲染一张空表）
    // 读面不可用与「没有课在训」是两件事（前者 = 不可知）
    const noQueue = await render({
      overview: ovView([ovRow({ course: 'stalled', iter: 3, hubSeen: true })]),
      loopQueue: null,
    })
    expect(noQueue).toContain('不可知')
    expect(noQueue).toContain('未在训 1 门未列')
  })

  it('★ 回归闸：hub 无应答时不得把「读不到课程表」读成「hub 未注册」', async () => {
    // 合并前的总览卡就犯了这个错——表头写着「hub 无应答」，行里却叫人用 --course 重启 hub。
    const html = await render({
      overview: ovView([ovRow({ course: 'c4', training: true, hubSeen: true })], {
        hubUrl: null,
        hubOnline: false,
      }),
    })
    expect(html).toContain('hub 无应答')
    expect(html).not.toContain('在训 · hub 未注册')
    expect(html).not.toContain('--course')
    expect(html).not.toContain('data-conflict')
  })
})

// ────────────────────────── 每一列的内容（旧两张表的断言汇总） ──────────────────────────

describe('课程矩阵：七列都有读数（旧「总览」+「调度器」的断言在这里汇合）', () => {
  it('课程列：一行一门课，当前查看那行标 --cur + aria-current；切课入口带悬停', async () => {
    const html = await render({ course: 'c5' })
    // 三个切课按钮（= 在训的行：c4 / c5 / ghost）；按钮只包住课程名，不是整行（否则表里的字选不中）
    expect((html.match(/class="tc-mx__pick"/g) ?? []).length).toBe(3)
    expect(html).toContain('tc-mx__row tc-mx__row--cur')
    expect(html).toContain('aria-current="true"')
    expect(html).toContain('title="切到查看 c4"')
    expect(html).toContain('title="c5（当前查看）"')
  })

  it('iter 列：训练侧指针优先；只有 hub 侧账本尾行时也能显示（不是 `—`）', async () => {
    const both = await render() // c4：hub it42 / 训练侧 it37
    expect(both).toContain('it37')
    expect(both).toContain('来自训练侧队列')
    const hubOnly = await render({ loopQueue: null })
    expect(hubOnly).toContain('it42') // c4 的 hub 侧账本尾行
    expect(hubOnly).toContain('hub 侧账本尾行')
  })

  it('本轮列：下一步 + 待办深度（BC 与 RL 的悬停文案各自说清各自的口径）', async () => {
    const html = await render()
    expect(html).toContain('ppo')
    expect(html).toContain('待办 2')
    expect(html).toContain('待办 2 步（顺序即依赖顺序）：ppo → eval_join')
    expect(html).toContain('下一步：ppo')
  })

  it('队列·在飞列：hub 侧读数；hub 无应答时退化为 `—`（不编 0）', async () => {
    const html = await render()
    expect(html).toContain('队列 2 · 在飞 1')
    const down = await render({
      overview: ovView([ovRow({ course: 'c4', training: true, hubSeen: true })], {
        hubUrl: null,
        hubOnline: false,
      }),
    })
    // 单元格本身只有 `—`（列头已经写了「队列 · 在飞」），判据在悬停里
    expect(down).toContain('>—</td>')
    expect(down).not.toContain('队列 0')
    expect(down).toContain('没有任何 hub 在应答')
  })

  it('接管列：只给**有接管**的课；产物超 1 小时变醒目（云机挂了 vs 在跑）', async () => {
    const html = await render()
    expect(html).toContain('tpu-1 · 最近 —') // 进度信号未上报 ⇒ 相对时间是 `—`（不是 1970）
    expect((html.match(/tc-mx__seg\b/g) ?? []).length).toBe(3) // 每个上屏的行都有这一格（`—`）
    expect((html.match(/tpu-1 · 最近/g) ?? []).length).toBe(1) // 只有 c5 有接管读数
    expect(html).not.toContain('tc-mx__seg--stale') // 1 分钟前 = 在跑

    const stale = await render({
      overview: ovView([
        ovRow({
          course: 'c5',
          training: true, // 在训（否则这一行不上屏，接管列也就无从验证）
          hold: hold('tpu-1'),
          hubSeen: true,
          offlineRounds: 3,
          offlineLastMtime: NOW_SEC - 7200,
        }),
      ]),
      loopQueue: null,
    })
    expect(stale).toContain('tc-mx__seg--stale')
    expect(stale).toContain('云机可能挂了')
  })

  it('★2026-09-22 / ★M4：被接管的课走「云机回传」维度——不显示本地「推进中/队列 0」，也不冒充本地指针', async () => {
    const html = await render()
    // iter 列 = 云机回传的最新 it（默认夹具 c5：offlineLastIter=9，ov.iter=7 被覆盖）
    expect(html).toContain('>it9<')
    // 本轮列 = 云机
    expect(html).toContain('云机 it9')
    // 在等什么列 = 云机运行中 · 已回传 N 轮（不提本地 13 步表的词）
    expect(html).toContain('云机运行中 · 已回传 3 轮')
    // 队列·在飞列 = 接管中·不派发（不摆会误读的「队列 0 · 在飞 0」）
    expect(html).toContain('接管中·不派发')
    // 接管列给持有者（两列口径互补，不删）
    expect(html).toContain('tpu-1 · 最近 —')
  })

  it('「在等什么」：四态各自有修饰类；表头汇总等回传的**在训**课数', async () => {
    const html = await render({
      loopQueue: queueView(
        [
          lqRaw({
            course: 'a',
            inflight: [{ phase: 'ppo', round: '37', jid: 'job-1', dispatch: 'push' }],
            waiting: { kind: 'inflight', text: '等远端回传：ppo@37（jid=job-1 via push）' },
          }),
          lqRaw({ course: 'b', waiting: { kind: 'collect', text: '采集中：已落 78 局' } }),
          lqRaw({ course: 'c', pending: [], waiting: { kind: 'idle', text: '本轮无待办' } }),
          lqRaw({ course: 'd', waiting: { kind: 'ready', text: '无外部等待，下一步 ppo' } }),
        ],
        ['a', 'b', 'c', 'd'],
      ),
    })
    expect(html).toContain('tc-mx__wait--inflight')
    expect(html).toContain('tc-mx__wait--collect')
    expect(html).toContain('tc-mx__wait--idle')
    expect(html).toContain('等远端回传：ppo@37（jid=job-1 via push）')
    expect(html).toContain('采集中：已落 78 局')
    // ready 不上色（它是常态）——但句子里必须有下一步是谁
    expect(html).toContain('无外部等待，下一步 ppo')
    // 表头汇总等回传的**在训**课数（未在训的不算「在等外部」）
    expect(html).toContain('1 课等回传')
  })

  it('两侧不同步时（hub 说在训、训练侧说没进程）：行仍上屏但淡一档（这行最该看清）', async () => {
    // 过滤后 `--stopped` 只剩这一条触发路径（两侧不一致）——而它恰恰是最该看清的一种：
    // 盯着一张「在训」的名单时，「其实没有进程在推进它」必须看得出来。
    const html = await render({
      overview: ovView([ovRow({ course: 'c4', training: true, iter: 3, hubSeen: true })]),
      loopQueue: queueView([lqRaw()], []),
    })
    expect(html).toContain('tc-mx__row--stopped')
    expect(html).toContain('在训') // 状态列取 hub 侧（ov.training 优先）
    // 反之：训练侧说在训、hub 侧没这行 ⇒ 也在训（任何一侧说在训就上屏）
    const lqOnly = await render({
      overview: ovView([]),
      loopQueue: queueView([lqRaw()], ['c4']),
    })
    expect(lqOnly).toContain('<b>c4</b>')
    expect(lqOnly).not.toContain('tc-mx__row--stopped')
  })
})

// ────────────────────────── 表头 / 页脚 / 空态 ──────────────────────────

describe('课程矩阵：表头两半的机群级读数与空态', () => {
  it('表头：单例 + hub 地址/在派发/worker/游标 + 训练侧在训 N/M + 池票占用', async () => {
    const html = await render()
    expect(html).toContain('tc-mx__singleton')
    expect(html).toContain('单例')
    expect(html).toContain('hub 127.0.0.1:18787')
    expect(html).toContain('在派发 2 / worker 3')
    expect(html).toContain('最近派发 c4')
    expect(html).toContain('在训 1/1') // 训练侧只有 c4 一行
    expect(html).toContain('local_ppo 1/1')
  })

  it('停机徽标只在置位时出现', async () => {
    const off = await render()
    expect(off).not.toContain('tc-mx__chip--warn')
    const on = await render({
      overview: defaultOverview(),
      loopQueue: null,
    })
    const halted = await render({
      overview: ovView(defaultOverview().rows, { halt: true }),
      loopQueue: null,
    })
    expect(on).not.toContain('停机中')
    expect(halted).toContain('停机中')
  })

  it('排队等资源：表头点名被池挡住的课 + 页脚说明容量 1 的语义', async () => {
    const v = queueView([lqRaw()], ['c4'])
    const html = await render({ loopQueue: { ...v, blockedCourses: ['c4'] } })
    expect(html).toContain('排队等资源：c4')
    expect(html).toContain('本机重资源跨课排队（容量 1）')
  })

  it('页脚：有课在等外部时点名它（jid 截断显示）', async () => {
    const html = await render({
      loopQueue: queueView(
        [
          lqRaw({
            inflight: [{ phase: 'ppo', round: '37', jid: 'job-abcdef123456', dispatch: 'push' }],
            waiting: { kind: 'inflight', text: '等远端回传：ppo@37' },
          }),
        ],
        ['c4'],
      ),
    })
    expect(html).toContain('正在等 ppo@37')
    expect(html).toContain('jid=job-abcdef12')
    expect(html).toContain('其余课照常推进')
  })

  it('读失败且无行：显因不静默（只读视图不可用 + 原因）；有缓存时说明「上一拍读失败」', async () => {
    const failed: LoopQueueView = {
      blockedCourses: [],
      pools: {},
      rows: [],
      trainingCount: 0,
      error: 'trainer/run_rl_cluster.py 退出码 1：ModuleNotFoundError',
    }
    const empty = await render({ overview: null, loopQueue: failed })
    expect(empty).toContain('只读视图不可用')
    expect(empty).toContain('ModuleNotFoundError')
    // ★P1-11：`stale` 在场 = 读失败但**旧行还在**（last-known-good）⇒ 说「上一拍读失败（显示缓存）」；
    // 无 `stale`（冷启动失败/超窗）⇒ 说「未知」，不拿空表冒充缓存。
    const stale = await render({
      loopQueue: {
        ...queueView([lqRaw()], ['c4']),
        error: '超时',
        stale: { since: Date.now(), reason: '超时' },
      },
    })
    expect(stale).toContain('上一拍读失败')
    expect(stale).toContain('超时')
    const noKeep = await render({ loopQueue: { ...queueView([lqRaw()], ['c4']), error: '超时' } })
    expect(noKeep).toContain('调度器视图未知')
  })

  it('两侧都没东西可说 → 不渲染（没东西可说时不留空壳）', async () => {
    expect(await render({ overview: null, loopQueue: null })).toBe('')
    expect(await render({ overview: ovView([]), loopQueue: queueView([], []) })).toBe('')
  })
})

// ────────────────────────── 操作列：两个开关的归属 ──────────────────────────

describe('操作列：两个开关并列且归属分明（§7 O4）', () => {
  it('有动作通道才渲染操作列；缺省时一个都不多渲染（不假装能控）', async () => {
    const withAct = await render({ onAction: () => {} })
    expect(withAct).toContain('tc-mx__ops')
    expect(withAct).toContain('aria-label="hub：强制解除接管 c5"')
    expect(await render()).not.toContain('tc-mx__ops')
  })

  it('「强制解除接管」的能力边界：只给 hub 在线 ∧ 认识它 ∧ 真有接管的课（否则点下去一定 404/409）', async () => {
    const acts: Array<[string, Record<string, unknown>]> = []
    const html = await render({ onAction: (a, b) => acts.push([a, b]) })
    // ★M4：默认夹具里真有接管的只有 c5（hold live）；c4 在两半都说在训、但没有接管
    //   ⇒ 不画（一个点下去必 409 的键就是假承诺，与「只读不禁用」那条不同：那是权限边界，
    //   这里是能力边界）。
    expect((html.match(/aria-label="hub：强制解除接管 /g) ?? []).length).toBe(1)
    expect(html).toContain('aria-label="hub：强制解除接管 c5"')
    expect(html).not.toContain('aria-label="hub：强制解除接管 c4"')
    expect(acts).toEqual([]) // SSR 不模拟点击
  })

  it('动作归属前缀不混：hub：写 hub 的接管 / 本地：写暂停意图（不许读成「一个开关管两件事」）', async () => {
    const html = await render({ onAction: () => {} })
    expect(html).toContain('role="group" aria-label="hub：强制解除接管"')
    expect(html).toContain('role="group" aria-label="本地：暂停"')
    expect(html).toContain('aria-label="hub：强制解除接管 c5"')
    expect(html).toContain('aria-label="本地：暂停 c4"')
    expect(html).toContain('tc-mx__opsep') // 两半之间必须有分隔线
    // hub 侧写的是 **hub 的接管**（`release_hold=1` → 立墓碑 + 清 hold）——不是本机的任何状态
    expect(html).toContain('release_hold=1')
    expect(html).toContain('队列与账本保留') // 本地侧开关改的是训练进程推进
  })

  it('待生效的悬停说清「进程每拍读一次」——不能一句「处理中」糊过去', async () => {
    const html = await render({
      loopQueue: queueView([lqRaw()], ['c4'], { intent: ['c4'] }),
      onAction: () => {},
    })
    expect(html).toContain('训练进程每拍读一次，下一拍生效')
  })

  it('暂停按钮方向由**意图**定：点了未生效说「取消暂停」，已生效说「恢复」', async () => {
    const pending = await render({
      loopQueue: queueView([lqRaw()], ['c4'], { intent: ['c4'] }),
      onAction: () => {},
    })
    expect(pending).toContain('取消暂停')
    expect(pending).toContain('待生效')
    const paused = await render({
      loopQueue: queueView([lqRaw()], ['c4'], { intent: ['c4'], applied: ['c4'] }),
      onAction: () => {},
    })
    expect(paused).toContain('>恢复<')
    expect(paused).toContain('已暂停')
    // 事实徽标只在「意图 ≠ 事实」或「已暂停」时出现
    const running = await render({ onAction: () => {} })
    expect(running).not.toContain('tc-mx__pausebadge')
  })

  it('BC 行与 RL 行并列：BC 有种类徽标 + 「一轮 = 一个任务」的悬停，RL 行不冒充', async () => {
    const bc = lqRaw({
      course: 'bc-c4-v3',
      kind: 'bc',
      it: 2,
      current: 'round',
      pending: ['round'],
      inflight: [{ phase: 'bc', round: '2', jid: 'job-bc-123456789', dispatch: 'hubpush' }],
      waiting: { kind: 'inflight', text: '等远端回传：bc@2（jid=job-bc-123456789 via hubpush）' },
    })
    const html = await render({
      overview: null,
      loopQueue: queueView([bc, lqRaw()], ['bc-c4-v3', 'c4']),
    })
    expect(html).toContain('>BC<')
    expect(html).toContain('等远端回传：bc@2（jid=job-bc-123456789 via hubpush）')
    expect((html.match(/>BC</g) ?? []).length).toBe(1) // 只有一行是 BC
    expect(html).toContain('BC 课一轮 = 一个任务')
    expect(html).toContain('跑完这一轮 BC')
    expect(html).toContain('bc_epoch')
    // 页脚点名在等回传的那门课（BC 的 phase 就是 bc）
    expect(html).toContain('正在等 bc@2')
  })
})

// ────────────────────────── 行内任务包操作（2026-09-23 用户指令） ──────────────────────────

describe('任务包行内操作：「导出」一个键即取回 + 导入改真按键', () => {
  // 用户 2026-09-23 口径：「歧义太大！去掉「下载」链接，点击导出按键就是下载已经生成的任务包；
  // 导入也要改成按键形式，文本设为「导入训练结果」」。
  it('只有一个「导出任务包」键：没有「下载」链接、也没有指向 /api/taskBundle 的 <a>', async () => {
    const html = await render({ onAction: () => {} })
    expect(html).toContain('导出任务包')
    expect(html).not.toContain('>下载<')
    expect(html).not.toContain('/api/taskBundle?')
    // 导出键必须是 <button>（旧形状是 <label> 包隐藏 input：看着像键、语义不是键）
    expect(html).toContain('aria-label="导出任务包 c5"')
    expect(html).toMatch(/<button[^>]*aria-label="导出任务包 c5"/)
  })

  it('导入是**真按键**，文案「导入训练结果」（文件 input 隐藏且不是可见控件）', async () => {
    const html = await render({ onAction: () => {} })
    // 注：`[\s\S]*?` 而不是 `[^>]*` —— title 里含字面 `>`（`deliver-&lt;课>.zip`）。
    expect(html).toMatch(
      /<button[\s\S]*?aria-label="导入训练结果 c5"[\s\S]*?>导入训练结果<\/button>/,
    )
    expect(html).toMatch(/<input[^>]*type="file"[^>]*hidden/)
    // 旧形状（`<label class="tc-btn">导入<input …></label>`）不得保留
    expect(html).not.toMatch(/<label[^>]*tc-btn/)
    expect(html).not.toContain('导入产物')
  })

  it('SSR（effect 不跑）显「未导出」：不编大小、也不假装有包', async () => {
    const html = await render({ onAction: () => {} })
    expect(html).toContain('未导出')
    expect(html).not.toContain('tc-mx__bundleinfo')
  })

  it('★M4：任务包操作给**在训课**（任何一门在训课都可能被自主 worker 领走）', async () => {
    // 旧判据是「hub 标离线 ∨ 意图离线」——两个源都随模式语义退役。今天的事实是：claim 遇缺包
    // 时 hub 会请控制台导包（`pending_export`），所以对每一门在训课，导包都是合法动作。
    const html = await render({
      overview: ovView([ovRow({ course: 'c4', training: true, hubSeen: true })]),
      onAction: () => {},
    })
    expect(html).toContain('aria-label="导出任务包 c4"')
    expect(html).toContain('aria-label="导入训练结果 c4"')
    // 未在训的课不挂（停课连认课标记都没了，云机领不走）
    const offline = await render({
      overview: ovView([ovRow({ course: 'c4', training: false, hubSeen: true })]),
      loopQueue: null,
      onAction: () => {},
    })
    expect(offline).not.toContain('导出任务包')
    expect(offline).not.toContain('导入训练结果')
  })
})

// ────────────────────────── ★M4：接管（hold）的渲染与唯一人工出口 ──────────────────────────
//
// 旧一组用例守着「意图 / hub 模式 / rl-config 三源漂移」（2026-09-23/24 两次真机事故）。
// ★M4 把三个源全拆了——今天不可能再有「没对齐」这种形状：接管只有一个真源（hub 的 hold），
// 每课行随 `/admin/queue` 一起来。取而代之的是下面两条：接管在屏上怎么说、人能做什么。

describe('★M4 接管：状态列 / 队列格 / 接管列同屏一致，唯一的人工出口是「强制解除接管」', () => {
  it('接管中的课：三列同屏说同一件事（接管中（云机） · 接管中·不派发 · holder）', async () => {
    const html = await render({ onAction: () => {} })
    expect(html).toContain('接管中（云机）')
    expect(html).toContain('接管中·不派发')
    expect(html).toContain('tpu-1 · 最近 —')
    // 旧三颗模式钮一个都不在（它们的前提——课程有在线/离线模式——已经没了）
    expect(html).not.toContain('切离线')
    expect(html).not.toContain('切换成在线')
    expect(html).not.toContain('交还自动池')
  })

  it('「强制解除接管」只在**有接管**的行上给（hub 在线 ∧ 认识它 ∧ hold 在）', async () => {
    const html = await render({ onAction: () => {} })
    expect(html).toMatch(
      /<button[\s\S]*?aria-label="hub：强制解除接管 c5"[\s\S]*?>强制解除接管<\/button>/,
    )
    // 同一个面板里没有接管的行（c4、ghost）不给这颗钮——点下去必 409 的键就是假承诺
    expect(html).not.toContain('aria-label="hub：强制解除接管 c4"')
    // 没有动作通道时不渲染任何动作（与其它行内动作同判据）
    //   注：按**按钮**判——「强制解除接管」这几个字也在接管列的悬停里（那是读数，不是动作）。
    expect(await render()).not.toContain('aria-label="hub：强制解除接管')
  })

  it('接管掉线（stale）：状态列说「接管掉线」+ 徽标说「已恢复协作」（本机跑不跑一眼可见）', async () => {
    const html = await render({
      overview: ovView([
        ovRow({ course: 'c5', training: true, hold: hold('tpu-1', 'stale'), offlineRounds: 2 }),
      ]),
      loopQueue: null,
      onAction: () => {},
    })
    expect(html).toContain('接管掉线')
    expect(html).toContain('已恢复协作')
    // 掉线后队列列回到真相（派发已恢复，不能继续拿「接管中」把它藏掉）
    expect(html).not.toContain('接管中·不派发')
  })
})

// ────────────────────────── §4.1 毒包熔断上屏 ──────────────────────────

describe('毒包熔断（§4.1：冻住的 job **不在 pending 里**，只能单独说）', () => {
  /** 在训 + 一份被冻的 job（C 事故的形状：it58 领 40 次零回传）。 */
  const frozenRow = (patch: Partial<Parameters<typeof ovRow>[0]> = {}) =>
    ovView([
      ovRow({
        course: 'x20-clutch',
        training: true,
        iter: 57,
        hubSeen: true,
        frozen: [{ jobId: 'it58-0c1f', reclaims: 3, worker: 'node-a', ts: NOW_SEC - 600 }],
        ...patch,
      }),
    ])

  it('逐条上屏：课程 · job · 零回传次数 · 最后认领者（只给队列深度看不出这件事）', async () => {
    const html = await render({ overview: frozenRow() })
    expect(html).toContain('毒包熔断 · x20-clutch · it58-0c1f')
    expect(html).toContain('零回传 3 次 · 最后认领 node-a')
    expect(html).toContain('aria-label="毒包熔断冻结"')
    expect(html).toContain('解冻') // 唯一的可逆口在屏上（只读是服务端 403，不是涂灰）
  })

  it('没有冻的 job → 不出这一块（不是留个空壳横幅）', async () => {
    expect(await render({ overview: defaultOverview() })).not.toContain('毒包熔断')
  })

  it('无身份的认领者照实说「（无身份）」——不许编一个人出来', async () => {
    const html = await render({
      overview: frozenRow({ frozen: [{ jobId: 'j1', reclaims: 5, worker: '', ts: 0 }] }),
    })
    expect(html).toContain('零回传 5 次 · 最后认领（无身份）')
  })
})

// ────────────────────────── 接线（SSR 渲染不出点击，用源码断言兜底） ──────────────────────────

describe('接线：面板挂载、跨区分流与动作路由同源', () => {
  const app = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'app.tsx'), 'utf-8')
  const panel = readFileSync(
    path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', 'CourseMatrix.tsx'),
    'utf-8',
  )
  const types = readFileSync(
    path.join(DASHBOARD_ROOT, 'src', 'web', 'view', 'console-types.ts'),
    'utf-8',
  )

  it('app.tsx 挂了 <CourseMatrix>，两侧事实与切课路径都接上', () => {
    expect(app).toContain('<CourseMatrix')
    expect(app).toContain('overview={stateView?.overview ?? null}')
    expect(app).toContain('loopQueue={stateView?.loopQueue ?? null}')
    expect(app).toContain('onSelectCourse={selectCourse}')
  })

  it('★ 跨课程表**两区通用**：不得再被「当前查看的课是 BC」门控', () => {
    // 这条曾经是反的：`isBc ? null` 一包，选中一门 BC 课 ⇒ 整张表消失。
    // 两张表分开时，BC 课在最需要看「hub 在给它派活吗 / 有人在推进它吗」的地方不存在。
    expect(app).not.toMatch(/isBc\s*\?\s*null\s*:\s*\(\s*<PanelErrorBoundary>\s*<CourseMatrix/)
  })

  it('★ 两个旧面板已下线（“各写半边表”的日子不得回来）', () => {
    // 存在性断言而不是读取断言：这两个文件被重新加回来时，这张表就又有了一半真相在外面。
    for (const n of ['CourseOverview', 'LoopQueue']) {
      expect(
        existsSync(path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', `${n}.tsx`)),
        `${n}.tsx 不该再存在（两个半张表已合并为 CourseMatrix）`,
      ).toBe(false)
    }
    // 旧的两套行样式也不得回流（两套行样式随各自面板一起删；注意 CSS 注释里**提到**了旧类名
    // 解释它们为什么被删，所以查的是「有没有以它们开头的**规则**」而不是这几个字）
    const css = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'web', 'theme.css'), 'utf-8')
    expect(/^\.tc-cov/m.test(css)).toBe(false)
    expect(/^\.tc-loopq/m.test(css)).toBe(false)
    expect(css).toContain('.tc-mx__')
  })

  it('视图类型带 loopQueue（服务端填、客户端读，缺失即空态）', () => {
    expect(types).toContain('loopQueue?: LoopQueueView | null')
  })

  it('面板不直连服务端（分层铁律由 architecture-layering 用例兜底，这里挡住「顺手 import」）', () => {
    expect(panel).not.toContain('server/')
    expect(panel).not.toContain("from 'fs'")
  })

  it('§4.1 解冻按钮接的是 unfreeze-job；路由层有同一个口，且形状判据与动作层同源', () => {
    const route = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'api', 'route.ts'),
      'utf-8',
    )
    expect(panel).toContain("'unfreeze-job'")
    expect(route).toContain("case 'unfreeze-job'")
    // 关键：400 的判据不是路由自己再写一份正则（汄移就会变成「非法 job_id 拿 409 busy」）。
    expect(route).toContain('jobIdError(')
  })

  it('动作走 route 表：releaseCourseHold（hub 侧）与 setCoursePaused（本地）都在，且处置调度器/快照缓存', () => {
    const route = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'api', 'route.ts'),
      'utf-8',
    )
    const server = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'server', 'server.ts'), 'utf-8')
    // ★M4：hub 侧那颗钮从三颗模式钮换成了「强制解除接管」（课程不再有模式）。
    expect(panel).toContain("'releaseCourseHold'")
    expect(panel).toContain("'setCoursePaused'")
    expect(route).toContain("case 'releaseCourseHold'")
    expect(route).toContain("case 'setCoursePaused'")
    expect(app).toContain('onAction={doAction}')
    // 动作后走**单一处置入口**（2026-09-22）：课程级硬清 + 机群级/调度器软作废都收在它里面——
    // server.ts 不再逐个缓存手写作废（漏一个就退化成「动作后第一帧卡几秒」）。
    // 三个**硬清**函数（invalidateSlowSnapshot / invalidateHubAdmin / invalidateLoopQueue）在
    // server.ts 里一个都不许出现：那是**测试夹具归零**用的；用在动作/导入路径就是首帧冷算。
    // 产物导入（/api/deliverUpload）曾自己写两行硬清——它改的是**课程产物**（权重/账本/eval_log），
    // 却把机群级探测（节点 ping 1.5s / hub 2.5s）一起丢掉，实测下一次 /api/state 1575ms（暖 6ms）。
    expect(server).toContain('invalidateAfterAction()')
    expect(server).not.toContain('invalidateSlowSnapshot')
    expect(server).not.toContain('invalidateHubAdmin')
    expect(server).not.toContain('invalidateLoopQueue')
    const refresher = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'api', 'snapshot-refresher.ts'),
      'utf-8',
    )
    expect(refresher).toContain('refreshLoopQueue()')
    expect(refresher).toContain('refreshHubAdmin()')
  })
})

// ────────────────────────── 封存分组（plan/course-archive.plan.md §4 S3） ──────────────────────────

/** 一条封存档案（字段与 python `biz/course_archive.py::_build_manifest` 同形）。 */
function archRow(patch: Partial<ArchivedCourseView> & { course: string }): ArchivedCourseView {
  return {
    archivedAt: '2026-09-26 10:00:00',
    form: 'A',
    parent: '',
    itRange: [0, 182],
    finalIt: 182,
    keyIters: [150, 182],
    shardsKept: false,
    codec: 'gzip',
    verdict: '',
    bytesTotal: 12_000_000,
    bytesRawTotal: 320_000_000,
    filesTotal: 42,
    weights: [],
    reads: { evalLog: 'eval_log.jsonl.gz', trainLog: 'training_log.jsonl.gz' },
    ...patch,
  }
}

describe('课程矩阵：封存分组（默认折叠、不占主表）', () => {
  it('无档案 ⇒ 不画封存分组（旧视图/未封存过零变化）', async () => {
    expect(await render()).not.toContain('tc-mx__arch')
  })

  it('有档案 ⇒ 计数 chip + 默认折叠（aria-expanded=false，行不上屏）', async () => {
    const html = await render({ archived: [archRow({ course: 'x20-noexplore' })] })
    expect(html).toContain('封存 1 门')
    // 默认折叠：Collapsible 带 --on，aria-expanded=false
    expect(html).toContain('tc-collapse--on')
    expect(html).toContain('aria-expanded="false"')
  })

  it('展开内容给出「形态 / it 区间 / 还能不能复算」——shards 边界必须显式说', async () => {
    const html = await render({
      archived: [
        archRow({ course: 'x20-noexplore', form: 'A+B', shardsKept: false }),
        archRow({ course: 'human-shards', form: 'A', shardsKept: true, itRange: [1, 40] }),
      ],
    })
    expect(html).toContain('封存 2 门')
    expect(html).toContain('x20-noexplore')
    expect(html).toContain('形态 A+B · it 0–182')
    // ★ 「不可复算」是边界声明，不是细节：不说清楚就会有人拿它去重跑 rollout
    expect(html).toContain('无可复算 shards（只可比）')
    expect(html).toContain('含 shards（可复算）')
    // 只读：档案清单里没有任何动作按钮（不假装能控）——唯一的按钮是那个折叠开关
    const from = html.indexOf('tc-mx__arch-list')
    const listPart = html.slice(from, html.indexOf('</ul>', from))
    expect(listPart).toContain('tc-mx__arch-row')
    expect(listPart).not.toContain('<button')
  })

  it('全部课程都封存（无在训行）⇒ 仍然画封存分组，不整块消失', async () => {
    const html = await render({
      overview: null,
      loopQueue: null,
      archived: [archRow({ course: 'x-all' })],
    })
    expect(html).toContain('封存 1 门')
    expect(html).toContain('x-all')
  })

  it('接线：app.tsx 把 stateView.archived 传进面板（源码断言）', () => {
    const app = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'app.tsx'), 'utf-8')
    expect(app).toContain('archived={stateView?.archived ?? null}')
  })
})
