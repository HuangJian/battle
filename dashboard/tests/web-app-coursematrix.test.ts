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
 * 纯函数（join 规则 / 状态判定 / 段内红线）在 `web-course-matrix.test.ts`；本文件只管
 * 「操作员能从屏幕上读出什么」。点击行为 SSR 渲染不出来（`preact-render-to-string` 丢弃
 * 事件处理器），故动作接线用**源码接线断言**兜底。
 */

import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync } from 'fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import type { CourseOverviewRow, LoopQueueView, ParallelOverviewView } from '../src/web/view'
import { parseLoopQueue, withPausedFacts, withTraining } from '../src/web/view'

// ────────────────────────── 夹具 ──────────────────────────

/** hub 侧一行。 */
function ovRow(patch: Partial<CourseOverviewRow> & { course: string }): CourseOverviewRow {
  return {
    training: false,
    iter: null,
    offline: false,
    hubSeen: true,
    queuePending: 0,
    inflight: 0,
    offlineRounds: 0,
    offlineLastIter: null,
    offlineLastMtime: 0,
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
    raceActive: false,
    activeCourses: 2,
    activeWorkers: 3,
    halt: false,
    recentDispatch: 'c4',
    offlineProgress: null,
    rows,
    ...patch,
  }
}

/** 五态各一行（覆盖全部状态档 + 两个冲突态）。 */
function defaultOverview(): ParallelOverviewView {
  return ovView([
    // 在训（hub 也认识它）
    ovRow({ course: 'c4', training: true, iter: 42, hubSeen: true, queuePending: 2, inflight: 1 }),
    // 离线（只收回传）+ 段内已回传 3 轮（账本里没有这些行）
    ovRow({
      course: 'c5',
      training: true,
      iter: 7,
      offline: true,
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
    course?: string
    onAction?: (act: string, body: Record<string, unknown>) => void
  } = {},
): Promise<string> {
  const { CourseMatrix } = await import('../src/web/app/panels/CourseMatrix')
  return renderToString(
    h(CourseMatrix, {
      overview: props.overview === undefined ? defaultOverview() : props.overview,
      loopQueue: props.loopQueue === undefined ? queueView([lqRaw()], ['c4']) : props.loopQueue,
      course: props.course ?? 'c4',
      onSelectCourse: () => {},
      onAction: props.onAction,
    }),
  )
}

// ────────────────────────── 合并后的状态列（新增信号） ──────────────────────────

describe('课程矩阵：只列在训课程（2026-09-20 用户指令）', () => {
  it('在训三态同屏可分辨：在训 / 离线 / 在训·hub 未注册', async () => {
    const html = await render()
    expect(html).toContain('在训')
    expect(html).toContain('离线（只收回传）')
    expect(html).toContain('在训 · hub 未注册')
    // 徽章档位：在训 g / 离线 a / 冲突 y（fixture 里三行都在训）
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

  it('段内列：只给离线且已有产物的课；超 1 小时变醒目（云机挂了 vs 在跑）', async () => {
    const html = await render()
    expect(html).toContain('段内 3 轮')
    expect((html.match(/tc-mx__seg\b/g) ?? []).length).toBe(3) // 每个上屏的行都有这一格
    expect((html.match(/段内 \d+ 轮/g) ?? []).length).toBe(1) // 只有 c5 有读数
    expect(html).not.toContain('tc-mx__seg--stale') // 1 分钟前 = 在跑

    const stale = await render({
      overview: ovView([
        ovRow({
          course: 'c5',
          training: true, // 在训（否则这一行不上屏，段内列也就无从验证）
          offline: true,
          hubSeen: true,
          offlineRounds: 3,
          offlineLastMtime: NOW_SEC - 7200,
        }),
      ]),
      loopQueue: null,
    })
    expect(stale).toContain('tc-mx__seg--stale')
    expect(stale).toContain('可能挂了')
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

  it('竞速 / 停机徽标只在置位时出现', async () => {
    const off = await render()
    expect(off).not.toContain('tc-mx__chip--warn')
    const on = await render({
      overview: defaultOverview(),
      loopQueue: null,
    })
    const raced = await render({
      overview: ovView(defaultOverview().rows, { raceActive: true, halt: true }),
      loopQueue: null,
    })
    expect(on).not.toContain('竞速')
    expect(raced).toContain('竞速')
    expect(raced).toContain('停机中')
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
      error: 'run_rl_cluster.py 退出码 1：ModuleNotFoundError',
    }
    const empty = await render({ overview: null, loopQueue: failed })
    expect(empty).toContain('只读视图不可用')
    expect(empty).toContain('ModuleNotFoundError')
    const stale = await render({ loopQueue: { ...queueView([lqRaw()], ['c4']), error: '超时' } })
    expect(stale).toContain('上一拍读失败')
    expect(stale).toContain('超时')
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
    expect(withAct).toContain('>切离线<')
    expect(await render()).not.toContain('tc-mx__ops')
  })

  it('hub 开关的能力边界：只给 hub 在线且认识它的课（否则点下去一定 400）', async () => {
    const acts: Array<[string, Record<string, unknown>]> = []
    const html = await render({ onAction: (a, b) => acts.push([a, b]) })
    // 默认夹具里 hubSeen **且在训**的课：c4 / c5 两门 → 两个 hub 开关
    // （未在训的 stalled 不上屏 ⇒ 它那个开关也就没有地方可以画）
    expect((html.match(/tc-btn tc-btn--sm" aria-label="hub：/g) ?? []).length).toBe(2)
    expect(html).toContain('>恢复在线<') // c5 已离线
    expect(acts).toEqual([]) // SSR 不模拟点击
  })

  it('两个开关各有归属前缀：hub：写课程表 / 本地：写暂停意图（不许读成「一个开关管两件事」）', async () => {
    const html = await render({ onAction: () => {} })
    expect(html).toContain('role="group" aria-label="hub：切离线"')
    expect(html).toContain('role="group" aria-label="本地：暂停"')
    expect(html).toContain('aria-label="hub：切离线 c4"')
    expect(html).toContain('aria-label="本地：暂停 c4"')
    expect(html).toContain('tc-mx__opsep') // 两半之间必须有分隔线
    expect(html).toContain('写 hub 课程表') // hub 侧开关写的是课程表
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

  it('动作走 route 表：setCourseMode（hub 侧）与 setCoursePaused（本地）都在，且作废调度器缓存', () => {
    const route = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'api', 'route.ts'),
      'utf-8',
    )
    const server = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'server', 'server.ts'), 'utf-8')
    expect(panel).toContain("'setCourseMode'")
    expect(panel).toContain("'setCoursePaused'")
    expect(route).toContain("case 'setCourseMode'")
    expect(route).toContain("case 'setCoursePaused'")
    expect(app).toContain('onAction={doAction}')
    expect(server).toContain('invalidateLoopQueue()')
  })
})
