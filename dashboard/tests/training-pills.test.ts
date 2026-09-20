/**
 * training-pills.test.ts — 顶部**在训课程** pill 行（用户 2026-09-20 口径）。
 *
 *  用户口径原文：「开课操作：顶部课程 select 选择某课程，点击「训练」按键；正在训练的所有课程，
 *  都在顶部显示为一个 pill，概览显示 it 数和状态（参考节点 pill），有停止按键，点击 pill 后切换
 *  显示其趋势图和指标表，并高亮 pill；点击停止按键后，停止课程，从顶部区域移除」。
 *
 *  本文件钉四件事：
 *   ① **判据**（服务端）：`trainingCourses` = **已开课**（`tmp/<课>/training-enabled.txt`），
 *      **不是**「有账本」（历史课全都有账本：那就是「一启动 21 门课在训」的根因）；
 *   ② **推导**（视图层纯函数）：每门课 → it 指针 + 一句状态 + tone；队列视图里没有它时
 *      报「视图不可用」而不是编一个「空闲」（缺状态 ≠ 没卡住）；
 *   ③ **上屏**（SSR）：pill 行渲染课名/it/状态/■ 停课；当前查看的那门高亮；没有在训课程时
 *      整行不渲染（空行会被读成「有东西没加载出来」）；位置 = **顶栏内**（P0 重设计后
 *      课程选择器住侧栏，pill 不再与它同行；开课键紧挨选择器）—— 见下面那条布局用例；
 *   ④ **动作接线**：点 pill 切查看课程（趋势图 + 指标表跟着走）、■ 停课带**显式课程名**
 *      （点 A 课的 ■ 必须停 A——兜底成「当前查看课程」在没同步时会停错一门）。
 *
 *  环境重定向：见 `./helpers/console-fixture.ts`（rl-config / console-state 全在临时目录）。
 */

import { afterEach, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import type { ConsoleStateView, LoopQueueRow } from '../src/web/view'
import { api, render, view } from './helpers/console-fixture'

// ────────────────────────── 纯函数：pill 推导 ──────────────────────────

/** 一行队列行（只填本用例关心的事实，其余走真实缺省形状）。 */
function row(over: Partial<LoopQueueRow> & { course: string }): LoopQueueRow {
  return {
    kind: 'rl',
    training: true,
    it: 37,
    state: 'running',
    current: 'rollout',
    pending: [],
    inflight: [],
    facts: {
      iterations: 37,
      lastVerdict: null,
      trainSecTotal: 0,
      softRemediateCount: 0,
      klStreak: 0,
      gamesSettled: 0,
      gamesPlanned: 0,
    },
    waiting: { kind: 'collect', text: '采集中：已落 12 局' },
    pausedIntent: false,
    pauseApplied: false,
    ...over,
  }
}

describe('coursePills：把队列事实翻译成一行 pill', () => {
  it('在跑 + 采集中 ⇒ 绿点「采集中」+ it 指针（指针用账本下一轮，不是已结算轮数）', () => {
    const [p] = view.coursePills({
      courses: ['c6-chip'],
      rows: [row({ course: 'c6-chip' })],
      trainerRunning: true,
    })!
    expect(p).toMatchObject({ course: 'c6-chip', it: 37, status: '采集中', tone: 'g' })
  })

  it('「在等什么」的四态各自上屏（等回传 / 采集中 / 推进中 / 空闲）——不在 TS 里重算语义', () => {
    const status = (waiting: LoopQueueRow['waiting']): string =>
      view.coursePills({
        courses: ['c'],
        rows: [row({ course: 'c', waiting })],
        trainerRunning: true,
      })[0]!.status
    expect(status({ kind: 'inflight', text: '等远端回传：ppo_remote@37（jid=abc）' })).toBe(
      '等回传',
    )
    expect(status({ kind: 'collect', text: '采集中：已落 12 局' })).toBe('采集中')
    expect(status({ kind: 'ready', text: '无外部等待，下一步 ppo' })).toBe('推进中')
    expect(status({ kind: 'idle', text: '本轮无待办（账本已结算 / 未开训）' })).toBe('空闲')
  })

  it('确定性事实优先于「在等什么」：暂停 / 收官 / 中止各占一态', () => {
    const pills = view.coursePills({
      courses: ['p', 'd', 'a'],
      rows: [
        row({ course: 'p', pausedIntent: true, pauseApplied: true }),
        row({ course: 'd', state: 'done' }),
        row({ course: 'a', state: 'aborted' }),
      ],
      trainerRunning: true,
    })
    expect(pills.map((p) => [p.status, p.tone])).toEqual([
      ['已暂停', 'y'],
      ['已收官', 'gray'],
      ['已中止', 'r'],
    ])
  })

  it('已开课但共享 trainer 没跑 ⇒ 「待进程」（不是空闲、也不是故障：启动服务进程就会入队）', () => {
    const [p] = view.coursePills({
      courses: ['c'],
      rows: [row({ course: 'c' })],
      trainerRunning: false,
    })!
    expect(p).toMatchObject({ status: '待进程', tone: 'gray' })
    expect(p.title).toContain('启动「服务进程」')
  })

  it('队列视图里没有这门课 ⇒ 「视图不可用」+ it 未知（**不编** 第 0 轮 / 空闲）', () => {
    const [p] = view.coursePills({ courses: ['ghost'], rows: [], trainerRunning: true })!
    expect(p).toMatchObject({ it: null, status: '视图不可用', tone: 'gray' })
    expect(p.title).toContain('停课不受它影响')
  })

  it('一门课都没有 ⇒ 空行（pill 行整行不渲染）', () => {
    expect(
      view.coursePills({ courses: [], rows: [row({ course: 'x' })], trainerRunning: true }),
    ).toEqual([])
  })
})

// ────────────────────────── 服务端 stamp：在训判据 = 开课标记 ──────────────────────────

// 预热调度器读面缓存：`buildStateView` 默认走**真**执行体（起 python 子进程）——本文件只关心
// 「在训名单」这一份事实，用假执行体暖缓存后 TTL 内读的就是它（与 server-api-loop-queue.test.ts
// 同一手法：断言服务端接线，不起子进程）。
api.invalidateLoopQueue()
await api.getLoopQueueView(() => ({
  code: 0,
  stdout: JSON.stringify({ courses: [] }),
  stderr: '',
  timeout: false,
}))

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-pills-'))
process.env.BCITY_TMP_LOGS_DIR = path.join(DIR, 'traj')
mkdirSync(process.env.BCITY_TMP_LOGS_DIR, { recursive: true })

/** 造一门「盘上有账本」的课；`open` = 再写上开课标记（= 控制台点过「训练」）。 */
function course(name: string, open: boolean): void {
  const d = path.join(process.env.BCITY_TMP_LOGS_DIR!, name)
  mkdirSync(d, { recursive: true })
  writeFileSync(path.join(d, 'training_log.jsonl'), '')
  if (open) writeFileSync(path.join(d, 'training-enabled.txt'), '')
}

afterEach(() => {
  rmSync(process.env.BCITY_TMP_LOGS_DIR!, { recursive: true, force: true })
  mkdirSync(process.env.BCITY_TMP_LOGS_DIR!, { recursive: true })
})

describe('buildStateView：trainingCourses = 已开课（开课标记），不是「有账本」', () => {
  it('历史课（只有账本）不上在训名单；点过「训练」的课才上', async () => {
    // 2026-09-20 用户报障的回归：tmp/ 下堆着几十门历史课，每门都有账本/remote-jobs 残影——
    // 拿「有账本」当在训判据，进程一起来它们全部变成「正在训练」。
    course('x1-rebirth', false)
    course('x20-floor', false)
    course('c6-chip', true)
    // ★ 课程**显式传**：`courseLifecycle` 是「查看课程」的属性，而不传参时它是
    //   `effectiveCourse(state, courses)` **推**出来的（默认 = 最近活跃课）——那个默认值
    //   挂在真实工作目录的残影上（`--parallel` 下同进程的其它文件会写真实 tmp/ 与
    //   nn-training/*.lock），于是这个断言变成看环境的。本用例要钉的是「已开课的判据 =
    //   开课标记」，那就把课程钉死，别把结论挂在「谁的残影更新」上。
    const s = await api.buildStateView('c6-chip')
    expect(s.trainingCourses).toEqual(['c6-chip'])
    expect(s.courseLifecycle?.enabled).toBe(true)
  })

  it('一门都没开课 ⇒ 空名单（不是「全部历史课」）+ 生命周期也是未开课', async () => {
    // 名单与 `courseLifecycle.enabled` **同一个判据**（顶部「训练」按钮与 pill 行不会
    // 各说各话）；这里只造一门课，故「查看课程」就是它。
    course('x1-rebirth', false)
    const s = await api.buildStateView()
    expect(s.trainingCourses).toEqual([])
    expect(s.courseLifecycle?.enabled ?? false).toBe(false)
  })
})

// ────────────────────────── SSR：pill 行上屏 ──────────────────────────

/** 把真实 state view 换成「在训两门课 + 队列事实」的最小形状。 */
async function viewWithPills(): Promise<ConsoleStateView> {
  const base = await api.buildStateView()
  return {
    ...base,
    course: 'a',
    courses: ['a', 'b', 'hist'],
    trainingCourses: ['b', 'a'],
    loopQueue: {
      blockedCourses: [],
      pools: {},
      trainingCount: 2,
      rows: [
        row({
          course: 'a',
          it: 37,
          waiting: { kind: 'inflight', text: '等远端回传：ppo_remote@37' },
        }),
        row({ course: 'b', it: 5, kind: 'bc', waiting: { kind: 'idle', text: '本轮无待办' } }),
      ],
    },
  }
}

describe('顶部在训课程 pill 行（SSR）', () => {
  it('每门在训课一个 pill：课名 + it 数 + 状态 + ■ 停课；BC 课带徽标', async () => {
    const html = render.renderConsolePage(await viewWithPills())
    expect(html).toContain('class="tc-tpills"')
    expect(html).toContain('<b>a</b>')
    expect(html).toContain('it37')
    expect(html).toContain('等回传')
    expect(html).toContain('<b>b</b>')
    expect(html).toContain('it5')
    expect(html).toContain('tc-tpill__kind')
    expect(html).toContain('aria-label="停课 a"')
    expect(html).toContain('aria-label="停课 b"')
  })

  it('当前查看的那门高亮（--active），且只有它一门', async () => {
    const html = render.renderConsolePage(await viewWithPills())
    // 只数**元素上的 class**（`tc-tpill--active` 字样在页面内联的 <style> 里也出现一次：
    // 那是样式定义，不是第二个高亮的 pill）。
    expect(html.match(/class="tc-tpill tc-tpill--active"/g)?.length).toBe(1)
    expect(html.match(/class="tc-tpill"/g)?.length).toBe(1)
  })

  it('课程 select 的在训标记用「已开课」口径（与 pill 行同一个名单）', async () => {
    const html = render.renderConsolePage(await viewWithPills())
    expect(html).toContain('🔥 b（已开课）')
    expect(html).toContain('🔥 a（已开课）')
    expect(html).not.toContain('🔥 hist')
    // 旧的「正在训练：a、b」标签被 pill 行取代（用户：正在训练的课程显示为 pill）——
    // 断言类名而不是文案：`__INITIAL__` 里注入的**真实日志尾行**也可能含这几个字（它们
    // 与本用例无关，而类名是唯一的）（读页面 HTML 无法排除这份 JSON）。
    expect(html).not.toContain('tc-training-tag')
  })

  it('没有在训课程 ⇒ 整行不渲染（空行会被读成「有东西没加载出来」）', async () => {
    const base = await api.buildStateView()
    const html = render.renderConsolePage({
      ...base,
      trainingCourses: [],
      loopQueue: { blockedCourses: [], pools: {}, rows: [], trainingCount: 0 },
    })
    expect(html).not.toContain('class="tc-tpills"')
  })

  it('pill 行在顶栏内、开课键在侧栏课程选择器旁（提交后的 IA：选择器已在侧栏）', async () => {
    const html = render.renderConsolePage(await viewWithPills())
    // ★ 必须先切掉 <head>：整份 theme.css 内联在首帧里，而**旧布局**的类名
    //   （`tc-topbar__row` / `tc-topbar__course` / `.tc-topbar` 本身）仍留在样式表中 ——
    //   不切片的 indexOf 会被样式表满足，于是「pill 在顶栏行内」这类断言在 DOM 完全
    //   错位时照样是绿的（本仓已踩过三次：C15/C16/C18；合并时这条正是从假绿改过来的）。
    const doc = html.slice(html.indexOf('id="root"'))
    const side = doc.indexOf('class="tc-side"')
    const course = doc.indexOf('id="courseSel"')
    const open = doc.indexOf('>训练</button>')
    const top = doc.indexOf('class="tc-top"')
    const pills = doc.indexOf('class="tc-tpills"')
    const topEnd = doc.indexOf('</header>')
    expect(side).toBeGreaterThan(-1) // 前提：侧栏在场（否则下面的序判定会退化成比 -1）
    expect(course).toBeGreaterThan(-1)
    expect(open).toBeGreaterThan(-1)
    expect(top).toBeGreaterThan(-1)
    expect(pills).toBeGreaterThan(-1)
    expect(topEnd).toBeGreaterThan(-1)
    // 侧栏在顶栏之前，且：侧栏内 课程选择器 → 开课键；顶栏内 pill 行（不是又单开一行）
    expect(top).toBeGreaterThan(side)
    expect(course).toBeGreaterThan(side)
    expect(open).toBeGreaterThan(course)
    expect(pills).toBeGreaterThan(top)
    expect(pills).toBeLessThan(topEnd)
    // 读序：选课 → 训练 → 看哪几门在训（两者分居侧栏/顶栏，序由外壳的 DOM 序保证）
    expect(open).toBeLessThan(pills)
  })

  it('★ pill 组：无「在训」文字标签、且在顶栏**靠左**（全局读数仍贴最右）', async () => {
    // 2026-09-20 用户指令：「在训」（组前那个标签）去掉 + pill 行左对齐。
    // 左边是「我在看什么」，右边是「集群现在怎样」——这条钉子钉的是**序**，不是样式表。
    const html = render.renderConsolePage(await viewWithPills())
    const doc = html.slice(html.indexOf('id="root"'))
    const pills = doc.indexOf('class="tc-tpills"')
    const right = doc.indexOf('class="tc-top__right"')
    const head = doc.indexOf('class="tc-top"')
    expect(pills).toBeGreaterThan(head) // 前提：pill 组在顶栏内（否则下标比较无意义）
    expect(right).toBeGreaterThan(pills) // ★ pill 组在全局读数组之**前** = 靠左
    // 组内不得再出现文字标签（`aria-label` 不算：它在页面上不可见，是屏幕阅读器的组名）
    const group = doc.slice(pills, doc.indexOf('</header>'))
    expect(group).not.toContain('class="lbl"')
    expect(doc).toContain('aria-label="在训课程"')
  })

  it('顶部「训练」按键恒在（开课入口与进程启动解耦），停课不在顶栏', async () => {
    const html = render.renderConsolePage(await viewWithPills())
    expect(html).toContain('>训练</button>')
    expect(html).not.toContain('>停课</button>')
    expect(html).not.toContain('>开课</button>')
  })

  it('只读视图：pill 可点（切查看目标与课程 select 同权），但不停用任何按钮', async () => {
    const html = render.renderConsolePage({ ...(await viewWithPills()), readOnly: true })
    expect(html).toContain('class="tc-tpills"')
    expect(html).not.toMatch(/<button[^>]*disabled/)
  })
})
