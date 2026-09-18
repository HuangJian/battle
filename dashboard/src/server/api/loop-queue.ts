/** loop-queue.ts — 训练调度器（单例）每课队列视图的读取面（R2c-3 控制台接线）。
 *
 *  数据源 = `nn-training/run_rl_cluster.py --json`（训练侧**只读**入口：不训练、不发布、
 *  不等待）。为什么走子进程而不是在 TS 里读账本重算：那一份判据（`pending_tasks` /
 *  `RoundFacts` / `waiting_state`）已经存在且被 python 侧用例钉住，在控制台重写一遍 =
 *  第二份真相，两边会以不同的速度演化（R2b 否决 `loop-state.json` 的同一条理由）。
 *
 *  代价与预算：一次冷算 = python 解释器冷启 + 扫 N 课账本/journal/shard 目录（本机实测
 *  ~sub-second）。它**不在请求路径的必等部分**（懒算 + TTL + 单飞，与 hub 观测面同规），
 *  且调度器视图的粒度是「一轮」（分钟级），故 TTL 取 10s（比 hub 的 5s 宽）：
 *  每 10s 最多一次子进程，换「每课在等什么」这个今天只能翻 N 份日志才答得出的问题。
 *
 *  容错：读失败（解释器缺失 / 超时 / 输出不可解析）**不抛**——`/api/state` 不该被一个
 *  观测面带崩（与 hub 总览 / 隧道 A/B 同口径），视图带 `error` 上屏，UI 显空态 + 原因。
 */

import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import { type LoopQueueView, parseLoopQueue, withTraining } from '../../web/view'
import { type SyncRunResult, runRunPythonSyncScript } from '../run-python'

/** 调度器视图的 TTL（10s：事实变化的粒度是「一轮」，冷算要起一个 python）。 */
export const LOOP_QUEUE_TTL_MS = 10_000
/** 子进程墙钟上限（正常 <2s；给足余量，超时按读失败处理）。 */
export const LOOP_QUEUE_TIMEOUT_MS = 20_000

/** 读一次原始输出的执行体（测试注入点：**不跑 python**）。 */
export type LoopQueueRunner = () => SyncRunResult

function defaultLoopQueueRunner(): SyncRunResult {
  return runRunPythonSyncScript(
    'nn-training/run_rl_cluster.py',
    ['--traj-root', path.join(REPO_ROOT, 'tmp'), '--json'],
    { timeoutMs: LOOP_QUEUE_TIMEOUT_MS },
  )
}

/** 把一次子进程结果翻译成视图：**永远返回一个视图**（失败时 rows 空 + error）。
 *
 *  纯函数（无 IO）——容错的每一条分支都值得单测，而它们与「子进程能不能起」无关。
 */
export function viewFromRunResult(r: SyncRunResult): LoopQueueView {
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

let cached: { at: number; view: LoopQueueView } | null = null
let inFlight: Promise<LoopQueueView> | null = null

/** 调度器视图（懒算 + TTL + 单飞）：并发请求共享同一次子进程。 */
export async function getLoopQueueView(
  run: LoopQueueRunner = defaultLoopQueueRunner,
): Promise<LoopQueueView> {
  const now = Date.now()
  if (cached && now - cached.at < LOOP_QUEUE_TTL_MS) return cached.view
  if (inFlight) return inFlight
  const p = (async (): Promise<LoopQueueView> => {
    let view: LoopQueueView
    try {
      view = viewFromRunResult(run())
    } catch (e) {
      // 起不了子进程（解释器/venv 缺失）也算「读失败」，不是控制台故障
      view = {
        blockedCourses: [],
        pools: {},
        rows: [],
        trainingCount: 0,
        error: `调度器视图读取失败：${e instanceof Error ? e.message : String(e)}`,
      }
    }
    cached = { at: Date.now(), view }
    return view
  })()
  inFlight = p.finally(() => {
    inFlight = null
  }) as Promise<LoopQueueView>
  return inFlight
}

/** 动作后置空（与 `invalidateSlowSnapshot` / `invalidateHubAdmin` 同规）。 */
export function invalidateLoopQueue(): void {
  cached = null
}

/** 组装：调度器视图 + **在训事实**（registry 的 trainingLoop 存活表，与控制台总览同源）。
 *
 *  两个事实源各给一半事实、逐行合并：python 说「这一轮卡在哪」，registry 说「这门课此刻
 *  有没有人在跑」——缺了后者，「停了的课」会被读成「等外部」。
 */
export async function buildLoopQueueView(
  training: string[],
  run: LoopQueueRunner = defaultLoopQueueRunner,
): Promise<LoopQueueView> {
  return withTraining(await getLoopQueueView(run), training)
}
