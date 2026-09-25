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
import { readJsoncFile } from '../../core/jsonc'
import { curriculaDir, tmpLogsDir } from '../../core/paths'
import { validateCourseName } from '../../core/slots'
import type { RolloutSrcMode, TrainMode } from '../../core/types'
import { isBcCourse, seedWeightsFromBc } from '../../stack/courses'
import { pruneLegacyCourseKnobs } from '../../stack/course-knobs'
import { kickstartReceipt } from '../../stack/kickstart-receipt'
import { pairedSeedReceipt } from '../../stack/paired-seed-receipt'
import { remoteExecutionFace } from '../../stack/push-config'
import { trainModeKnobs } from '../../stack/specs'
import { type CourseMode, setCourseMode } from './course-mode'
import { readLoopControl, setCoursePaused } from './loop-control'
import { loopControlPath } from '../../core/paths'
import { launchTaskBundleExport } from '../bundles'
import { ActionError, ActionResult, done, guard, release } from './result'
import { runRlLockHolder } from './labels'
import { runBcLockHolder, runClusterLockHolder } from './start'

/** 开课参数（全部是**课程级**：绝不写进 `rl.*` 那块所有课程共用的默认面）。 */
export interface OpenCourseOpts {
  /** 训练模式（缺省在线）：`offline` = 整段上云（写 `rollout_src=run` + `run_iters=-1`，
   *  并把该课 hub 置 offline）；`online` = 撤掉离线标记（**必删** `run_iters`，否则切回在线
   *  了却还在整段上云）。域换算只走 `stack/specs.ts::trainModeKnobs`。 */
  trainMode?: TrainMode
  /** rollout 执行位置（在线时可选）：写课程级覆盖 `courses.<课>.rollout_src`，
   *  不碰全局 `rl.rollout_src`（那是所有课程共用的默认面）。离线档忽略它。 */
  rolloutSrc?: RolloutSrcMode
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

/** 课程文件声明的 `iters`（终点轮数）；读不到 / 没声明 → null。
 *
 *  与 python 侧 `run_rl` 的整段守卫同口径（`rl/loop_remote.py`：`--run-iters<0` 需要课程声明
 *  iters——没有终点就不叫整段）：离线（整段上云）模式 = 跑到课程末尾，没有有限终点节点会
 *  一直跑下去。开课预校验在这里读课程文件，**在 trainer 接触坏配置之前**就把配备错拦下。
 *  JSONC 解析借 `core/jsonc.ts::readJsoncFile`（唯一 JSONC 解析器，别再手搓，见该文件头）。 */
export function declaredCourseIters(course: string): number | null {
  const files = [
    path.join(curriculaDir(), `${course}.jsonc`),
    path.join(curriculaDir(), `${course}.bc.jsonc`),
  ]
  for (const f of files) {
    if (!existsSync(f)) continue
    try {
      const obj = readJsoncFile(f)
      if (obj && typeof obj === 'object' && 'iters' in obj) {
        const iters = (obj as { iters?: unknown }).iters
        return typeof iters === 'number' ? iters : null
      }
    } catch {
      return null // 课程文件解析失败 = 配影视作「未声明」（assertCourseExists 已保证存在）
    }
  }
  return null
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
 *  rollout 位置覆盖。返回人读说明 + 最终模式。
 *
 *  ★ 2026-09-21（§3）：不再写 `remote_degrade_after`——单一 PPO 路径下没有「降级本机」这个
 *  档位（loop 没有计算能力），残留值由 `pruneLegacyCourseKnobs` 清掉。
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

/** 本课的两个锁持有人事实 + 「是否真冲突」的判定（开课的前置检查只吃这一个真相）。
 *
 *  ★ **必须带身份看，不能只看「锁活着」**（2026-09-20 用户报障：「❌ x20-steady 未开课：
 *  run_rl 锁被 PID 18364 持有」）——那个 PID 就是控制台自己起的**共享 trainer**
 *  （`run_rl_cluster.py --serve`）：它服务多课，每开一门课就取该课自己的 per-course 锁
 *  （单进程多课模型的正常持有）。把这种持有当成冲突 ⇒ **每次开课都被自己人拒**，
 *  而拒的同时盘上已经写过开课标记（见下面 ① 的顺序注释）= 一句假回执。
 *
 *  判据：该课锁的持有人 == **共享 trainer 的进程级锁**（`nn-training/.run_cluster.lock`）
 *  持有人 ⇒ 同一个进程 ⇒ 正常状态，不拦；持有者活着但不是它 ⇒ 真冲突（有人在手工跑
 *  `run_rl.py --course <本课>`，与共享 trainer 抢同一批 traj）⇒ 拦。陈旧锁（持有人已死）
 *  在 `runRlLockHolder` / `runBcLockHolder` 里已经归 null ⇒ 不拦。
 */
export interface CourseRunnerFacts {
  /** 该课 per-course 锁的存活持有人（**含共享 trainer 自己**）；null = 无锁/持有人已死。 */
  holder: number | null
  /** 共享 trainer 进程级锁（`nn-training/.run_cluster.lock`）的存活持有人；null = 未在跑。 */
  cluster: number | null
  /** **真冲突**（按课的另一份 runner）的 PID；null = 不拦。 */
  conflict: number | null
}

export function courseRunnerFacts(course: string, bc: boolean): CourseRunnerFacts {
  const holder = bc ? runBcLockHolder(course) : runRlLockHolder(course)
  const cluster = runClusterLockHolder()
  return { holder, cluster, conflict: holder && holder !== cluster ? holder : null }
}

/** **开课**：把一门课放进训练（进程没跑也能放——训练进程是发现式的，下一拍就入队）。 */
export async function openCourse(course: string, opts: OpenCourseOpts = {}): Promise<ActionResult> {
  guard(`course-open:${course}`)
  try {
    const c = String(course ?? '').trim()
    if (!c) throw new ActionError('需要课程（开课是按课程记的，见顶部课程选择）')
    validateCourseName(c)
    assertCourseExists(c)
    const bc = isBcCourse(c)
    // ① **先做全部会拒绝的检查**（零副作用），再进写面。
    //    2026-09-20 实测的顺序坑：检查原本排在写面**之后** ⇒ 被拒的那一次照样写了课程旋钮 +
    //    **开课标记**（而标记就是训练侧/hub 的「在训」闸！）⇒ 回执照说「未开课」，盘上却已
    //    经是开课状态（连 hub 派发闸都开了），而暂停意图还留着。控制台的回执与盘上事实必须
    //    同向：拒绝就应该什么都没发生。
    //    · 暂停意图文件必须**可读可解析**：`setCoursePaused` 对坏文件是保守拒绝（不覆盖，
    //      可能有人在手改/另一份工具在写）——而它是写面里唯一会拒绝的一步。坏文件是
    //      「已知事实」，体检放这里才叫「拒绝 = 零副作用」。
    const control = readLoopControl()
    if (control.error) {
      return done(false, `${c} 未开课：控制文件有问题，未改动`, [
        `${control.error}（${loopControlPath()}）`,
        '本次开课**未写任何东西**——先修好意图文件（或删掉它）再开课。',
      ])
    }
    const runners = courseRunnerFacts(c, bc)
    if (runners.conflict) {
      const runningPid = runners.conflict
      // 真冲突：另一份**按课** runner 在跑（不是共享 trainer 自己）。到此处**一个字都没写**
      // ——拒绝就应该是「什么都没发生」（下面的写面全在它后面）。
      const lockName = `${bc ? 'run_bc' : 'run_rl'}.${c}.lock`
      return done(
        false,
        `${c} 未开课：另一份按课 runner（PID ${runningPid}）正在跑（${bc ? 'run_bc' : 'run_rl'}）`,
        [
          '它与共享 trainer 抢同一批 traj（两套调度器会各跑一半课程、互相覆盖权重）。',
          `先停掉那一份再开课（停 trainer 会按身份核验释放 .${lockName}；` +
            `确实已死可手工删除 nn-training/.${lockName}）——本次开课**未写任何东西**。`,
          `（不是共享 trainer 自己握的锁：后者与控制台自己起的进程同源，会被本检查放行）`,
        ],
      )
    }
    // ★ 2026-09-22 事故预校验：离线（整段上云）= 跑到课程末尾，课程必须声明有限 iters——
    //   没有终点节点会一直跑下去（python 侧 `loop_remote` 的同款 SystemExit 曾把共享 trainer
    //   整个弄崩）。在这里读课程文件、**在 trainer 接触坏配置之前**响亮拒绝（零副作用，
    //   与上方其它预检同区）。
    if (opts.trainMode === 'offline') {
      const iters = declaredCourseIters(c)
      if (iters === null || iters <= 0) {
        throw new ActionError(
          `课程 ${c} 声明 iters=${iters ?? '缺失'}≤0，离线（整段上云）要求 iters>0——` +
            '没有终点就不叫整段，节点会一直跑下去。请改「在线」模式开课，' +
            '或在课程文件里声明 iters>0 后再开离线。本次开课未写任何东西。',
        )
      }
    }
    // ② 写面，三步的**顺序就是契约**（旋钮 → 解暂停 → 事实）：
    //    · **会抛的一步排最前**：`saveConfig` 的容量/槽位守卫会抛（`core/config.ts`）——
    //      它在最前 ⇒ 抛出的那一次盘上零变化（连暂停意图都没动）。
    //    · 解暂停排第二：① 已体检过意图文件（坏文件在那里就拒了）⇒ 到这里不会再拒；
    //      即便真拒，留下的也只是「未开课 + 旋钮已写」，不是「在训」假象。
    //    · **开课标记排最后**：它是训练侧/hub 的「在训」闸，必须在一切之后才落 ——
    //      前面任何一步失败都不许留下「已开课」的盘上事实（用户 2026-09-20 报障的正是
    //      「回执说未开课、盘上却已开课」这种三种口径并存）。
    const cfg = loadConfig()
    const pruned = pruneLegacyCourseKnobs(cfg)
    const knobs = writeCourseConfigForOpen(c, opts)
    const resume = setCoursePaused(c, false)
    const notes = [
      ...knobs.notes,
      ...(pruned.removed.length > 0
        ? [`已清理 legacy 传输配置 ${pruned.removed.length} 项（课程与 worker 节点正交）`]
        : []),
      `暂停意图：${resume.ok ? resume.message : `未改动（${resume.message}）`}`,
      ...prepareCourseForOpen(c).notes,
      // §5.3 起点-基线对照行（plan/accident.plan.md）：C 事故里「本腿恢复的权重已经在 bc
      // 权重那一档、却拿满额锚去拉」这个事实，开课前盘上就有——放在回执里，操作员点开课时
      // 直接看见。与训练侧 `loop_lifecycle._kickstart_baseline_row` 同源同数（同一份账本、
      // 同一取法；S4 第十九刀前它住 loop_core）。
      ...kickstartReceipt(c, cfg),
      // §2.5 配对 rotateSeed 核对：两臂同 V 靠课程文件保证，这一屏把「对端是谁、各臂账本
      // 末次 run_start 是不是这把 V」摆在开课那一刻（错配跑 80 轮 = 一整天算力）。
      ...pairedSeedReceipt(c),
      // 共享 trainer 已经握着本课的按课锁 = 正常状态（它服务多课，开一门取一门）——
      // 说明白，免得操作员把它当成「双开」而在日志里找不存在的冲突。
      ...(runners.holder && runners.holder === runners.cluster
        ? [
            `共享 trainer（PID ${runners.holder}）已在服务本课：按课锁由它自己取，` +
              '这是已开课状态下的**正常持有**，不是冲突。',
          ]
        : []),
    ]
    // hub 模式：离线档 = 只让带标 worker 领整段；在线 = 恢复实时派发。
    const trainMode = opts.trainMode === 'offline' ? 'offline' : 'online'
    const hub = await pushHubMode(c, trainMode, opts.hubMode)
    const face = remoteExecutionFace(loadConfig())
    const hubNote = hub.ok
      ? `hub 该课模式 = ${trainMode}`
      : `hub 尚未认下这门课（${hub.message}）——意图已记录，起 hub 时会按意图回灌`
    // ★ 2026-09-22：离线开课 = **自动生成任务包**（随时可导：导出是只读快照、不与训练抢
    // per-course 锁）。任务包不手工搬运——hub 的 `GET /offline/task-pack` 直接按课上架
    // 这份 zip，Kaggle/Colab 离线 worker 探到 hub 即可拉取（离线课状态列据此显示）。
    // BCITY_NO_AUTO_TASK_BUNDLE：测试逃生阀（课程生命周期用例不开真导出子进程）；
    // 导出本身的正确性由 server-api-task-bundle 套件覆盖。
    // ★ 离线开课（含「停课 → 重新开课」这种重启）= **重新打一份带当前代码的包**：
    //   `launchTaskBundleExport` 会先把旧包作废（挪进 `tmp/<课>/stale-packs/`）再起导出，
    //   否则代码变过之后云机在导出窗口里探到的仍是旧代码的包，而它会拿旧代码跑完整段。
    //   作废后 hub 的 `/offline/task-pack` 404，云机的等包循环会一直等到新包写好。
    const autoExport =
      trainMode === 'offline' && !process.env.BCITY_NO_AUTO_TASK_BUNDLE
        ? launchTaskBundleExport(c)
        : null
    return done(
      true,
      `已开课 ${c}（训练模式 ${trainMode === 'offline' ? '离线（整段上云）' : '在线'}）` +
        '——已进入调度课程表' +
        `${hub.ok ? '' : `；${hubNote}`}`,
      [
        `本课（${c}）执行面：${face.text}${face.detail ? `（${face.detail}）` : ''}`,
        ...notes,
        hubNote,
        ...(autoExport
          ? autoExport.ok
            ? [
                `任务包自动导出：${autoExport.message}`,
                ...(autoExport.invalidated
                  ? [
                      '★ 旧任务包已作废（代码可能已变）→ 已归档到 tmp/<课>/stale-packs/；' +
                        '导出完成前云机取不到包（/offline/task-pack 404），它会等新包——这是预期行为，不是故障',
                    ]
                  : []),
              ]
            : [`任务包自动导出未能启动（${autoExport.message}）——可稍后在课程行手动「导出任务包」`]
          : []),
        ...(trainMode === 'offline'
          ? ['离线课重启 = 重新导出任务包（代码可能已变）：停课后重新开课即重打一份带当前代码的包']
          : []),
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
