/** loop-queue.ts — 训练调度器（单例）每课队列视图的读取面（R2c-3 控制台接线）。
 *
 *  数据源 = `nn-training/run_rl_cluster.py --json`（训练侧**只读**入口：不训练、不发布、
 *  不等待）。为什么走子进程而不是在 TS 里读账本重算：那一份判据（`pending_tasks` /
 *  `RoundFacts` / `waiting_state`）已经存在且被 python 侧用例钉住，在控制台重写一遍 =
 *  第二份真相，两边会以不同的速度演化（R2b 否决 `loop-state.json` 的同一条理由）。
 *
 *  代价与预算：一次冷算 = python 解释器冷启 + 扫 N 课账本/journal/shard 目录（本机实测
 *  ~sub-second）。它**不在请求路径的必等部分**（懒算 + TTL + 单飞 + SWR，与 hub 观测面同规），
 *  且调度器视图的粒度是「一轮」（分钟级），故 TTL 取 10s（比 hub 的 5s 宽）：
 *  每 10s 最多一次子进程，换「每课在等什么」这个今天只能翻 N 份日志才答得出的问题。
 *
 *  ★ 2026-09-22（动作后第一帧不冷算）：缓存换成 `core/swr-cache`——**陈旧先给旧值、重算丢后台**
 *  ——并且默认执行体改成**异步**子进程（`runRunPythonAsyncScript`）。两条缺一不可：
 *    · 此前动作用 `invalidateLoopQueue()` **硬清**（TTL 未到也丢），下一帧必须等一次
 *      python 冷启（暂停/恢复/启停按钮点下去要先卡一下）；
 *    · 而用 `spawnSync` 当重算体，「后台」是假的：事件循环被按住，旧值的响应照样发不出去。
 *  「动作结果即时上屏」不受影响：暂停**意图与生效回执**每帧都从控制文件现读
 *  （`readPauseFacts` → `withPausedFacts`，见 `buildLoopQueueView`），不经这份缓存；
 *  python 侧的事实（队列/在等什么）本来就只有「一轮」级的变化，陈旧 ≤TTL + 一次后台重算。
 *
 *  容错：读失败（解释器缺失 / 超时 / 输出不可解析）**不抛**——`/api/state` 不该被一个
 *  观测面带崩（与 hub 总览 / 隧道 A/B 同口径），视图带 `error` 上屏，UI 显空态 + 原因。
 */

import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import { createSwrCache } from '../../core/swr-cache'
import {
  type LoopQueueView,
  parseLoopQueue,
  trainingFromQueue,
  withPausedFacts,
  withTraining,
} from '../../web/view'
import { type RunPythonResult, runRunPythonAsyncScript } from '../run-python'
import { readPauseFacts } from '../actions/loop-control'

/** 调度器视图的 TTL（10s：事实变化的粒度是「一轮」，冷算要起一个 python）。 */
export const LOOP_QUEUE_TTL_MS = 10_000
/** 子进程墙钟上限（正常 <2s；给足余量，超时按读失败处理）。 */
export const LOOP_QUEUE_TIMEOUT_MS = 20_000

/** 读一次原始输出的执行体（测试注入点：**不跑 python**）。同步执行体照样可用
 *  （`await` 一个非 promise 是常量代价）——注入点不因默认执行体改异步而变。 */
export type LoopQueueRunner = () => RunPythonResult | Promise<RunPythonResult>

function defaultLoopQueueRunner(): Promise<RunPythonResult> {
  return runRunPythonAsyncScript(
    'nn-training/run_rl_cluster.py',
    ['--traj-root', path.join(REPO_ROOT, 'tmp'), '--json'],
    { timeoutMs: LOOP_QUEUE_TIMEOUT_MS },
  )
}

/** 把一次子进程结果翻译成视图：**永远返回一个视图**（失败时 rows 空 + error）。
 *
 *  纯函数（无 IO）——容错的每一条分支都值得单测，而它们与「子进程能不能起」无关。
 */
export function viewFromRunResult(r: RunPythonResult): LoopQueueView {
  const empty: LoopQueueView = { blockedCourses: [], pools: {}, rows: [], trainingCount: 0 }
  if (r.timeout) {
    return { ...empty, error: `只读调度器视图超时（>${LOOP_QUEUE_TIMEOUT_MS / 1000}s）` }
  }
  if (r.code !== 0) {
    const tail = (r.stderr || '').trim().split('\n').slice(-1)[0] ?? ''
    return {
      ...empty,
      error: `run_rl_cluster.py 退出码 ${r.code ?? 'null'}${tail ? `：${tail}` : ''}`,
    }
  }
  let raw: unknown
  try {
    raw = JSON.parse(r.stdout.trim())
  } catch {
    return { ...empty, error: 'run_rl_cluster.py --json 输出不可解析（stdout 不是 JSON）' }
  }
  const view = parseLoopQueue(raw)
  if (!view) return { ...empty, error: '--json 形状不符（缺 courses 数组）' }
  return view
}

const cache = createSwrCache<LoopQueueView>(LOOP_QUEUE_TTL_MS)

/** 调度器视图（懒算 + TTL + 单飞 + SWR）：并发请求共享同一次子进程。
 *
 *  读失败（含执行体抛异常）**不抛**：翻成带 `error` 的空视图（`viewFromRunResult` 的每一
 *  条失败分支 + 起不了子进程）——`/api/state` 不该被一个观测面带崩。 */
export async function getLoopQueueView(
  run: LoopQueueRunner = defaultLoopQueueRunner,
): Promise<LoopQueueView> {
  return cache.get(async (): Promise<LoopQueueView> => {
    try {
      return viewFromRunResult(await run())
    } catch (e) {
      return {
        blockedCourses: [],
        pools: {},
        rows: [],
        trainingCount: 0,
        error: `调度器视图读取失败：${e instanceof Error ? e.message : String(e)}`,
      }
    }
  })
}

/** **动作后的软作废**（`snapshot-refresher.invalidateAfterAction` 调用）：保留当前视图
 *  供读路径立即返回，重算丢后台（见文件头）。
 *
 *  暂停/恢复的**即时反馈**不靠它：意图与生效回执每帧从控制文件现读（`readPauseFacts`），
 *  所以这里保留旧 python 事实不会让按钮「看起来没反应」。 */
export function refreshLoopQueue(): void {
  cache.refresh()
}

/** **硬作废**（下一次读**必须**等新重算）：改了输入（课程/账本）或要隔离测试夹具时用。
 *  生产路径已无调用点（动作走 `refreshLoopQueue`）——留在导出面上是**给门禁**用的：
 *  用例靠它把模块级缓存归零，避免一个用例暖的假视图喂给下一个。 */
export function invalidateLoopQueue(): void {
  cache.clear()
}

/** 组装：调度器视图 + **在训事实**（registry）+ **暂停意图/生效回执**（控制文件），逐行合并。
 *
 *  四个事实源各给一半：python 说「这一轮卡在哪」，registry 说「这门课此刻有没有人在跑」，
 *  控制文件说「操作员想让它跑吗」，回执文件说「训练进程实际把它停着没」——缺了最后两个，
 *  暂停按钮点下去毫无反馈（要等下一拍 python 读到才显示），也看不出「点了但进程没跑」
 *  这种**待生效**状态。
 *
 *  两个控制面事实都从**文件**读（不是从 python 的输出）：意图就是控制台自己写的那份，
 *  回执是训练进程自己写的，读盘零代价，也不引入「python 要多报字段」的耦合。
 */
export async function buildLoopQueueView(
  schedulerAlive: boolean,
  run: LoopQueueRunner = defaultLoopQueueRunner,
  facts: { intent: string[]; applied: string[] } = readPauseFacts(),
): Promise<LoopQueueView> {
  const view = await getLoopQueueView(run)
  // 「在训」由**这一份**视图自己推（`trainingFromQueue`）：调度器存活（registry）∧ 该课未收官
  // （python 的队列状态）。别再往外要一个 `training: string[]`——那第二份名单会与这里漂开。
  const training = trainingFromQueue(view, schedulerAlive)
  return withPausedFacts(withTraining(view, training), facts.intent, facts.applied)
}
