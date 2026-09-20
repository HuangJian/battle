/** course-lifecycle.ts — **开课 / 停课**：进程与课程解耦后的独立入口（2026-09-20 用户指令）。
 *
 *  背景（用户口径 2026-09-20）：「服务进程启动不应与课程绑定。进程启动时不要自动开启课程训练，
 *  需要增加独立的入口开启/停止课程训练」。
 *
 *  改造前：`启动训练` = selfNode → hubServer → **trainingLoop（顺带开课）**，课程准备
 *  （播权重 / 建账本 / 写旋钮 / 置 hub 模式）挂在启动 trainer 的第三步里。两个后果：
 *    ① 「起个进程」这件事必须先选一门课——操作员的动作语义被课程污染；
 *    ② **时序错位**：`setCourseMode` 在课程还不存在（hub 的课程表是**扫盘发现**，此刻
 *       `tmp/<课>/remote-jobs` 还没出现）时打过去，hub 回 400
 *       `需要合法 course（[]）与 mode(...)`——2026-09-20 实测的那条「失败 x20-steady」。
 *
 *  现在：进程步骤只起进程（见 `start.ts::startSharedTrainer`），课程生命周期住这里，
 *  **两件事各自有入口、各自有回执**：
 *    · 开课 = 写课程级旋钮 → 建发现事实（账本 + `remote-jobs/` + 权重播种）→ 解除暂停
 *      → 置 hub 模式（有界重试）→ 报执行面。进程没跑也能开（训练进程是**发现式**的：
 *      下一拍扫到这门课就入队）。
 *    · 停课 = **非破坏**：写暂停意图（`tmp/loop-control.json`）+ 该课 hub 置 offline。
 *      队列与账本一个字不动，随时「开课」恢复（用户 2026-09-20 定案；不做「下架账本」
 *      ——那会让 iter/队列/账本等阅读面一起消失）。
 */

import { appendFileSync, existsSync, mkdirSync, writeFileSync, rmSync } from 'fs'
import path from 'path'
import { loadConfig, saveConfig } from '../../core/config'
import { curriculaDir, tmpLogsDir } from '../../core/paths'
import { validateCourseName } from '../../core/slots'
import type { RolloutSrcMode, TrainMode } from '../../core/types'
import { isBcCourse, seedWeightsFromBc } from '../../stack/courses'
import { pruneLegacyCourseKnobs } from '../../stack/course-knobs'
import { remoteExecutionFace } from '../../stack/push-config'
import { trainModeKnobs } from '../../stack/specs'
import { type CourseMode, setCourseMode } from './course-mode'
import { setCoursePaused } from './loop-control'
import { ActionError, ActionResult, done, guard, release } from './result'
import { runRlLockHolder } from './labels'
import { runBcLockHolder } from './start'

/** 开课参数（全部是**课程级**：绝不写进 `rl.*` 那块所有课程共用的默认面）。 */
export interface OpenCourseOpts {
  /** 训练模式（缺省在线）：`offline` = 整段上云（写 `rollout_src=run` + `run_iters=-1`，
   *  并把该课 hub 置 offline）；`online` = 撤掉离线标记（**必删** `run_iters`，否则切回在线
   *  了却还在整段上云）。域换算只走 `stack/specs.ts::trainModeKnobs`。 */
  trainMode?: TrainMode
  /** rollout 执行位置（在线时可选）：写课程级覆盖 `courses.<课>.rollout_src`，
   *  不碰全局 `rl.rollout_src`（那是所有课程共用的默认面）。离线档忽略它。 */
  rolloutSrc?: RolloutSrcMode
  /** T7：远端连败是否 opt-in 降级本机 PPO（课程级旋钮 `remote_degrade_after`）。 */
  remoteDegrade?: boolean
  /** hub 模式推送的有界重试（缺省 3 次 × 2s）。hub 的课程表是**扫盘发现**，新建的
   *  `remote-jobs/` 要等它扫到才认这门课；测试注入 1 次避免空等。 */
  hubMode?: { attempts?: number; delayMs?: number }
}

/** 课程参数快速失败。
 *
 *  ★ **不调 `core/config.ts::validateCourseArg`**：那个入口的最后一手是 `process.exit(1)`
 *  ——对长驻的控制台进程而言就是自杀（点一下开课，整个控制台消失）。开课是页面动作，
 *  失败必须是可捕获的 `ActionError`。判据与它保持一致（路径 / `.jsonc` / `.bc.jsonc`）。
 */
function assertCourseExists(course: string): void {
  if (existsSync(course)) return
  if (existsSync(path.join(curriculaDir(), `${course}.jsonc`))) return
  if (existsSync(path.join(curriculaDir(), `${course}.bc.jsonc`))) return
  throw new ActionError(
    `课程 '${course}' 不存在（curricula/${course}.jsonc 或 curricula/${course}.bc.jsonc）——` +
      '先在 nn-training/curricula 下建课程文件',
  )
}

// ────────────────────────── 开课标记（发现判据的显式闸） ──────────────────────────

/** 「已开课」标记文件名（**必须与 python 侧 `remote/protocol.py::COURSE_ENABLE_MARKER` 同名**）。
 *
 *  为什么需要它（2026-09-20 用户报障）：「共享 trainer 是发现式的」+「tmp/ 下堆着几十门历史课
 *  的账本」⇒ 进程一启动就把**所有历史课**一起拉进训练（实测：起 trainer 后控制台列出 21 门
 *  「正在训练」）。用户口径：「课程开训需要用户手动开启」⇒ 课程表 = 账本 ∧ **开课标记**；
 *  训练侧与 hub 的发现判据都加这一道闸（`rl/loop_plan.enabled_courses`、`_course_dir_live`）。
 *  标记与账本同住课程目录：一个判据、一处位置，开/停课各是一次文件操作（不涉及共享 JSON 的
 *  读-改-写竞态），且控制台重启不丢「哪几门开着」。 */
export const COURSE_ENABLE_MARKER = 'training-enabled.txt'

/** 本课的开课标记路径（`<traj-root>/<课>/training-enabled.txt`）。 */
export function courseEnableMarkerPath(course: string): string {
  return path.join(tmpLogsDir(), course, COURSE_ENABLE_MARKER)
}

/** 这门课是否已开课（标记存在）——与控制台总览/按钮同判据。 */
export function courseEnabled(course: string): boolean {
  return !!course && existsSync(courseEnableMarkerPath(course))
}

// ────────────────────────── 课程准备（发现事实） ──────────────────────────

/** **开课前的课程准备**：把「这门课存在且可被调度」这件事写到盘上。
 *
 *  三件事各解决一个具体的坑（每一件都是「不做就会静默地不对」的那类）：
 *
 *   ① **权重播种**（RL 才有；BC 无 warm-start）：`tmp/<课>/weights.json` 不在则从 BC 种子复制。
 *      共享 trainer 不接受每课的权重参数，它按 traj 目录自己找（与 `run_rl.py` 同约定）。
 *   ② **发现事实**：`tmp/<课>/training_log.jsonl` 必须存在。训练侧的课程表**就是**这个文件
 *      （`rl/loop_plan.discover_courses` 与控制台 `discoverCourses` 同一判据），而共享 trainer 是
 *      「先起进程、后加课」的模型 —— 不建它，这门新课永远不会被发现（症状极难查：进程活着、
 *      队列正常、就是这门课一轮都不跑）。空账本 = 合法状态（第 1 轮从头开始）。
 *   ③ **hub 发现判据**：`tmp/<课>/remote-jobs/` 目录存在且新鲜（`hub_server._course_dir_live`）。
 *      训练侧第一次发布 job 时才会建它 —— 而控制台**开课时**就要推 hub 模式，不先建出来，
 *      那次推送必然拿到 `需要合法 course（[]）`（2026-09-20 实测）。空目录就是 hub 认课的锚点，
 *      与训练侧第一次发布时建的是同一个目录，不是新造的第二事实源。
 *
 *  ★ 只做「事实」，不写旋钮（旋钮见 `writeCourseConfigForOpen`）——两件事各自的失败语义不同：
 *  旋钮写坏了是配置问题，事实没建出来是「这门课根本不存在」。
 */
export function prepareCourseForOpen(course: string): { notes: string[] } {
  const notes: string[] = []
  const bc = isBcCourse(course)
  // 课程 traj 根：生产下就是仓根 `tmp/`（与训练侧同布局），单测用 BCITY_TMP_LOGS_DIR 重定向
  const trajRoot = tmpLogsDir()
  if (!bc) {
    const weightsPath = path.join(trajRoot, course, 'weights.json')
    if (!existsSync(weightsPath)) {
      seedWeightsFromBc(course, weightsPath) // 缺文件即抛 → 调用方响亮失败（不静默跑新权）
      notes.push(`已播种初始权重 tmp/${course}/weights.json`)
    }
  }
  const traj = path.join(trajRoot, course)
  mkdirSync(traj, { recursive: true })
  const ledger = path.join(traj, 'training_log.jsonl')
  if (!existsSync(ledger)) {
    appendFileSync(ledger, '')
    notes.push('已建课程账本 training_log.jsonl（共享 trainer 的课程发现判据）')
  }
  // hub 发现锚点（见上 ③）：空 `remote-jobs/` —— 训练侧发布第一份 job 前 hub 认不出这门课。
  const jobsDir = path.join(traj, 'remote-jobs')
  if (!existsSync(jobsDir)) {
    mkdirSync(jobsDir, { recursive: true })
    notes.push('已建 tmp/<课>/remote-jobs/（hub 的课程发现判据——否则置模式必然被拒）')
  }
  // ④ **开课标记**（见 COURSE_ENABLE_MARKER）：没有它，训练侧与 hub 都不会把这门课当成在训
  //（而「有账本」是所有历史课都满足的——那正是 2026-09-20 报障）。写内容而不是空文件：
  // 它是**证据**（谁在什么时候开的课），读面按存在性判。
  const marker = path.join(traj, COURSE_ENABLE_MARKER)
  if (!existsSync(marker)) {
    writeFileSync(marker, `${new Date().toISOString()} 开课（控制台 openCourse）\n`, 'utf-8')
    notes.push(`已写开课标记 ${COURSE_ENABLE_MARKER}（训练侧/hub 的「在训」判据）`)
  }
  return { notes }
}

/** 写本课的 rl-config 键（**唯一写面**）：训练模式（`rollout_src`/`run_iters`）、
 *  rollout 位置覆盖、降级本机（`remote_degrade_after`）。返回人读说明 + 最终模式。
 *
 *  为什么这几把键住 `courses.<课>` 而不是 `rl.*`：`rl.*` 是所有课程共用的默认面 ——
 *  在弹窗里只选了这一门课却把 `rollout_src:'run'` 落进 `rl.*`，等于把全部课程一起拖进
 *  整段上云（2026-09-19 那条注释记的就是这个坑）。 */
export function writeCourseConfigForOpen(
  course: string,
  opts: OpenCourseOpts,
): { notes: string[]; trainMode: TrainMode } {
  const notes: string[] = []
  const cfg = loadConfig()
  const courses = { ...cfg.courses }
  const row = { ...courses[course] }
  const trainMode: TrainMode = opts.trainMode === 'offline' ? 'offline' : 'online'
  if (opts.trainMode) {
    const knobs = trainModeKnobs(trainMode, opts.rolloutSrc ?? 'local')
    if (trainMode === 'offline') {
      // 两个键缺一不可：`run` 是声明，`run_iters` 是段长（`-1` = 到课程末）。
      row.rollout_src = knobs.rolloutSrc
      row.run_iters = knobs.runIters ?? -1
      notes.push('训练模式 离线：本课 rollout_src=run + run_iters=-1（整段上云）')
    } else {
      // 切回在线 = **撤掉离线标记**：段长必删（留着它 = 下一轮又被当成段长 + 本机采样 =
      // 半状态），课程级的 `run` 也删。别的课程级覆盖（有人显式写过 `rollout_src:'node'`）
      // 不归这里管 —— 除非这次显式选了新的 rollout 位置。
      delete row.run_iters
      if (row.rollout_src === 'run') delete row.rollout_src
      notes.push('训练模式 在线：已撤掉离线标记（run/run_iters）')
    }
  }
  if (opts.rolloutSrc && trainMode === 'online') {
    row.rollout_src = opts.rolloutSrc
    notes.push(`rollout 位置覆盖：courses.${course}.rollout_src=${opts.rolloutSrc}`)
  }
  if (opts.remoteDegrade !== undefined) {
    row.remote_degrade_after = opts.remoteDegrade ? 3 : 0
    notes.push(`远端连败降级本机：${opts.remoteDegrade ? '开（连败 3 次）' : '关（连败即 ABORT）'}`)
  }
  courses[course] = row
  saveConfig({ ...cfg, courses })
  return { notes, trainMode }
}

// ────────────────────────── hub 模式（有界重试） ──────────────────────────

/** 把该课的 hub 模式推过去，**有界重试**：hub 的课程表是扫盘发现的，开课这一刻它可能
 *  还没扫到（400 需要合法 course）；等一两拍它就会认。重试耗尽也**不是失败** ——
 *  意图已落盘（`setCourseMode` 的契约），起 hub 时 `restoreCourseModes` 会按意图回灌。 */
export async function pushHubMode(
  course: string,
  mode: CourseMode,
  retry: { attempts?: number; delayMs?: number } = {},
): Promise<{ ok: boolean; message: string }> {
  const attempts = Math.max(1, retry.attempts ?? 3)
  const delayMs = Math.max(0, retry.delayMs ?? 2000)
  let last = await setCourseMode(course, mode)
  for (let i = 1; i < attempts && !last.ok; i++) {
    await Bun.sleep(delayMs)
    last = await setCourseMode(course, mode)
  }
  return { ok: last.ok, message: last.message }
}

// ────────────────────────── 开课 / 停课 ──────────────────────────

/** **开课**：把一门课放进训练（进程没跑也能放——训练进程是发现式的，下一拍就入队）。 */
export async function openCourse(course: string, opts: OpenCourseOpts = {}): Promise<ActionResult> {
  guard(`course-open:${course}`)
  try {
    const c = String(course ?? '').trim()
    if (!c) throw new ActionError('需要课程（开课是按课程记的，见顶部课程选择）')
    validateCourseName(c)
    assertCourseExists(c)
    // legacy 清理（幂等，全课范围）：旧键 / 伪节点条目不该只对「这次开的课」生效。
    const pruned = pruneLegacyCourseKnobs(loadConfig())
    const notes = [
      ...writeCourseConfigForOpen(c, opts).notes,
      ...(pruned.removed.length > 0
        ? [`已清理 legacy 传输配置 ${pruned.removed.length} 项（课程与 worker 节点正交）`]
        : []),
      ...prepareCourseForOpen(c).notes,
    ]
    // 每课去重锁：serve 开课时会按课取锁（RL=run_rl / BC=run_bc）——别的持有者会让它
    // **响亮拒开**这门课。在这里拦下来，操作员就从动作结果里看到原因，不用去日志里找。
    const bc = isBcCourse(c)
    const holder = bc ? runBcLockHolder(c) : runRlLockHolder(c)
    if (holder) {
      return done(false, `${c} 未开课：${bc ? 'run_bc' : 'run_rl'} 锁被 PID ${holder} 持有`, [
        ...notes,
        '先停掉在跑的那一份（或删除锁文件）再开课——它与共享 trainer 抢同一批 traj。',
      ])
    }
    // 解除暂停意图（停课 = 非破坏暂停，开课要把它去掉；否则「开了课但不推进」）。
    const resume = setCoursePaused(c, false)
    if (!resume.ok) {
      return done(false, `${c} 未开课：${resume.message}`, notes)
    }
    // hub 模式：离线档 = 只让带标 worker 领整段；在线 = 恢复实时派发。
    const trainMode = opts.trainMode === 'offline' ? 'offline' : 'online'
    const hub = await pushHubMode(c, trainMode, opts.hubMode)
    const face = remoteExecutionFace(loadConfig())
    const hubNote = hub.ok
      ? `hub 该课模式 = ${trainMode}`
      : `hub 尚未认下这门课（${hub.message}）——意图已记录，起 hub 时会按意图回灌`
    return done(
      true,
      `已开课 ${c}（训练模式 ${trainMode === 'offline' ? '离线（整段上云）' : '在线'}）` +
        '——已进入调度课程表' +
        `${hub.ok ? '' : `；${hubNote}`}`,
      [
        `本课（${c}）执行面：${face.text}${face.detail ? `（${face.detail}）` : ''}`,
        ...notes,
        `暂停意图：${resume.message}`,
        hubNote,
        // 机器侧旋钮在**开课时**施加（python `apply_course_machine_overrides`）：已经开着的课
        // 要等它重开才换旋钮——暂停该课 → 重启共享 trainer（或等引擎驱逐重开）。
        '★ 机器侧旋钮（降级本机等）在开课时施加：已开着的课要**停课 → 重新开课**（或重启共享 trainer）才换。',
        '★ 共享 trainer 没在跑也能开课——它一起就会扫到已开课的课程；进程与课程是两件事。',
      ],
    )
  } catch (e) {
    if (e instanceof ActionError) throw e
    throw new ActionError(e instanceof Error ? e.message : String(e))
  } finally {
    release(`course-open:${course}`)
  }
}

/** **停课**：非破坏 —— 暂停调度意图（本机不再推进） + 该课 hub 置 offline。
 *
 *  与「暂停」按钮的关系：那条也是写暂停意图（同一份契约），差别在**离线闸**——停课连
 *  远端派发也一起收（否则云机仍会领走队列里已入队的 job 跑完整段，而操作员以为停了）。
 *  课程表 / 账本 / 队列一律不动：恢复走「开课」。
 */
export async function stopCourse(course: string): Promise<ActionResult> {
  guard(`course-stop:${course}`)
  try {
    const c = String(course ?? '').trim()
    if (!c) throw new ActionError('需要课程（停课是按课程记的，见顶部课程选择）')
    validateCourseName(c)
    // ① **删开课标记**（训练侧/hub 靠它认「在训」）：不删它，调度器下一拍又把这门课拉起来
    //    （发现式进程只认盘上事实，控制台说什么都没用）。
    const marker = courseEnableMarkerPath(c)
    const hadMarker = existsSync(marker)
    rmSync(marker, { force: true })
    // ② 暂停意图（表内暂停，双保险：万一标记被手工建回来/还在旧进程的内存表里）
    const pause = setCoursePaused(c, true)
    // ③ hub 该课置 offline（远端也不再实时派发）
    const hub = await pushHubMode(c, 'offline')
    const notes = [
      hadMarker
        ? '已删开课标记 training-enabled.txt（训练侧不再把这门课当在训）'
        : '本课本就没有开课标记',
      `暂停意图：${pause.message}`,
      hub.ok
        ? 'hub 该课模式 = offline（不再实时派发；带标 worker 仍可领已入队的整段 job）'
        : `hub 尚未认下这门课（${hub.message}）——意图已记录，起 hub 时会按意图回灌`,
      '队列与账本一个字不动：已入队的 job 仍在队列里，恢复（开课）后从原处接着跑。',
      '账本/iter/指标仍可看（课程下拉不筛历史课）——这是**非破坏**停课，不是下架。',
    ]
    return done(
      pause.ok,
      pause.ok ? `已停课 ${c}（已删开课标记 + 本机调度暂停 + hub 置离线）` : '停课未生效',
      notes,
    )
  } catch (e) {
    if (e instanceof ActionError) throw e
    throw new ActionError(e instanceof Error ? e.message : String(e))
  } finally {
    release(`course-stop:${course}`)
  }
}
