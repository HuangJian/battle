/** course-lifecycle.ts — **开课 / 停课**：进程与课程解耦后的独立入口（2026-09-20 用户指令）。
 *
 *  背景（用户口径 2026-09-20）：「服务进程启动不应与课程绑定。进程启动时不要自动开启课程训练，
 *  需要增加独立的入口开启/停止课程训练」。
 *
 *  改造前：`启动训练` = selfNode → hubServer → **trainingLoop（顺带开课）**，课程准备
 *  （播权重 / 建账本 / 写旋钮 / 置 hub 模式）挂在启动 trainer 的第三步里。两个后果：
 *    ① 「起个进程」这件事必须先选一门课——操作员的动作语义被课程污染；
 *    ② **时序错位**：置 hub 模式在课程还不存在（hub 的课程表是**扫盘发现**，此刻
 *       `tmp/<课>/remote-jobs` 还没出现）时打过去，hub 回 400
 *       `需要合法 course（[]）与 mode(...)`——2026-09-20 实测的那条「失败 x20-steady」。
 *
 *  现在：进程步骤只起进程（见 `start.ts::startSharedTrainer`），课程生命周期住这里，
 *  **两件事各自有入口、各自有回执**：
 *    · 开课 = 写课程级旋钮 → 建发现事实（账本 + `remote-jobs/` + 权重播种）→ 解除暂停。
 *      进程没跑也能开（训练进程是**发现式**的：下一拍扫到这门课就入队）；
 *    · 停课 = **非破坏**：写暂停意图（`tmp/loop-control.json`）+ 删开课标记。
 *      队列与账本一个字不动，随时「开课」恢复（用户 2026-09-20 定案；不做「下架账本」
 *      ——那会让 iter/队列/账本等阅读面一起消失）。
 *      ★ 2026-10-02（用户口径）：顺手清理本课已无信息量的 rl-config 残留
 *      （`rollout_src='local'` 缺省档 + 空节点整条删；`pruneStoppedCourseConfig`），
 *      显式 `node`/`auto`/`run` 一个字不动。
 *
 *  ★M4（2026-10-07，plan/worker-type-dispatch-model §3-M4）：**课程不再区分在线/离线**——
 *  开课不接受 `trainMode`（弹窗那格已删）、停课也不再推 hub（那边没有模式可推）。
 *  「这门课归云机」= hub 的 **hold** 事实（自主 worker claim 成功且任务包在盘才建立；
 *  掉线 900s 自动解除），开课弹窗只剩 rollout 位置这一把活旋钮。
 */

import { appendFileSync, copyFileSync, existsSync, mkdirSync, writeFileSync, rmSync } from 'fs'
import path from 'path'
import { loadConfig, saveConfig } from '../../core/config'
import { curriculaDir, REPO_ROOT, tmpLogsDir } from '../../core/paths'
import { validateCourseName } from '../../core/slots'
import type { CourseConf, RolloutSrcMode } from '../../core/types'
import {
  COURSE_ENABLE_MARKER,
  courseEnableMarkerPath,
  isBcCourse,
  resolveArchivedSeedPath,
  seedWeightsFromBc,
} from '../../stack/courses'
import { pruneLegacyCourseKnobs, pruneStoppedCourseConfig } from '../../stack/course-knobs'
import { kickstartReceipt } from '../../stack/kickstart-receipt'
import { pairedSeedReceipt } from '../../stack/paired-seed-receipt'
import { remoteExecutionFace } from '../../stack/push-config'
import { hubCandidates, hubReleaseCourseHold } from '../../stack/hub-admin'
import { readLoopControl, setCoursePaused } from './loop-control'
import { loopControlPath } from '../../core/paths'
import { ActionError, ActionResult, done, guard, release } from './result'
import { runRlLockHolder } from './labels'
import { runBcLockHolder, runClusterLockHolder } from './start'

/** 开课参数（全部是**课程级**：绝不写进 `rl.*` 那块所有课程共用的默认面）。 */
export interface OpenCourseOpts {
  // ★M2/M4：原来的 `trainMode`（训练模式）已随「课程不再区分在线/离线」退役——
  //  弹窗那格删了，开课也不再置 hub 模式（hub 侧连模式都没有了）。
  /** rollout 执行位置（可选）：写课程级覆盖 `courses.<课>.rollout_src`，
   *  不碰全局 `rl.rollout_src`（那是所有课程共用的默认面）。 */
  rolloutSrc?: RolloutSrcMode
  /** **起点权重来源**（plan/course-archive.plan.md §3.5 / G4-①）：指向一门**已封存**课的
   *  某个关键轮——开课时把该归档权重播种成 `tmp/<本课>/weights.json`（缺省 = BC 播种，
   *  行为不变）。
   *
   *  这里只收 `{sourceCourse, it}`（**绝不收路径**）：路径由服务端从
   *  `archive/courses/<课>/archive-manifest.json` 自解析并校验（见 `resolveArchivedSeedPath`）。
   *  解析不到 ⇒ 响亮拒绝（绝不退回 BC——那会静默拿错起点）。 */
  seedFrom?: { sourceCourse: string; it: number }
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

// ★M4：`declaredCourseIters()`（读课程文件声明的 iters）已删——它唯一的使用者就是
// 「离线开课要求 iters>0」那道预校验，而开课不再有模式。约束本体仍在：python 导出腿
// （`trainer/loop_export.py::_export_offline_bundle`）与 hub 的缺包自愈门都要求任务包
// 有计划终点，只是不再在开课这一拍重复一次。

// ────────────────────────── 开课标记（发现判据的显式闸） ──────────────────────────
//
// 判据本体（`COURSE_ENABLE_MARKER` / `courseEnableMarkerPath` / `courseEnabled`）住
// `stack/courses.ts`——回灌（`course-mode.ts`）也要读它，而本文件已 import `course-mode`
// （见文件头），判据留在这里会让回灌反向 import 成环。语义与「为什么需要它」的全文见该处。

// ────────────────────────── 课程准备（发现事实） ──────────────────────────

/** **开课前的课程准备**：把「这门课存在且可被调度」这件事写到盘上。
 *
 *  三件事各解决一个具体的坑（每一件都是「不做就会静默地不对」的那类）：
 *
 *   ① **权重播种**（RL 才有；BC 无 warm-start）：`tmp/<课>/weights.json` 不在则从 BC 种子复制。
 *      共享 trainer 不接受每课的权重参数，它按 traj 目录自己找（与 `trainer/run_rl.py` 同约定）。
 *   ② **发现事实**：`tmp/<课>/training_log.jsonl` 必须存在。训练侧的课程表**就是**这个文件
 *      （`trainer/loop_plan.discover_courses` 与控制台 `discoverCourses` 同一判据），而共享 trainer 是
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
export function prepareCourseForOpen(course: string, seedPath?: string): { notes: string[] } {
  const notes: string[] = []
  const bc = isBcCourse(course)
  // 课程 traj 根：生产下就是仓根 `tmp/`（与训练侧同布局），单测用 BCITY_TMP_LOGS_DIR 重定向
  const trajRoot = tmpLogsDir()
  if (!bc) {
    const weightsPath = path.join(trajRoot, course, 'weights.json')
    if (!existsSync(weightsPath)) {
      if (seedPath) {
        // 起点 = **封存档案**里的归档权重（G4-①）：seedPath 已由调用方从 manifest 解析并校验过。
        mkdirSync(path.dirname(weightsPath), { recursive: true })
        copyFileSync(seedPath, weightsPath)
        notes.push(
          `已从封存起点播种权重 tmp/${course}/weights.json ← ${path.relative(REPO_ROOT, seedPath)}`,
        )
      } else {
        seedWeightsFromBc(course, weightsPath) // 缺文件即抛 → 调用方响亮失败（不静默跑新权）
        notes.push(`已播种初始权重 tmp/${course}/weights.json`)
      }
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

/** 写本课的 rl-config 键（开课那条路径）。返回人读说明。
 *
 *  ★M2（plan/worker-type-dispatch-model §3-M2）：**不再有「离线档」**——「这门课归云机」
 *  不是 `rollout_src:'run'` + `run_iters:-1` 这对声明，而是 hub 的 **hold** 事实（云机 claim
 *  成功且有进度才建立；掉线 900s 自动解除）。
 *
 *  本函数只落显式选的 `rollout_src`（`node`/`auto` 仍是活的训练侧选项），并**就地清掉退役键**
 *  （`run_iters`，以及残留的 `rollout_src:'run'`）——退役值由训练侧容忍读兜住（映射 local +
 *  一行 WARN，见 `loop_transport._rollout_source`），但人一开课就把话说明白才是干净的读面。
 *  清完为空的课程节点整条删（与 `pruneStoppedCourseConfig` 的「空节点不留痕」同规）。
 *
 *  ★ 2026-09-21（§3）：不再写 `remote_degrade_after`——单一 PPO 路径下没有「降级本机」这个
 *  档位（loop 没有计算能力），残留值由 `pruneLegacyCourseKnobs` 清掉。
 *
 *  为什么这几把键住 `courses.<课>` 而不是 `rl.*`：`rl.*` 是所有课程共用的默认面 ——
 *  在弹窗里只选了这一门课却把 `rollout_src:'run'` 落进 `rl.*`，等于把全部课程一起拖进
 *  「这门课交给云机」（2026-09-19 那条注释记的就是这个坑）。 */
export function writeCourseConfigForOpen(
  course: string,
  opts: OpenCourseOpts,
): { notes: string[] } {
  // rollout 位置都没给就**不写盘**（历史行为下那是一次「内容不变的空写」，无语义——不重放它）。
  // ★M4：退役键的清理**不靠这个早退**（从前 `trainMode='online'` 是它的另一条入口，而模式
  //  已删）——今天只要点了开课就顺手清一遍退役键，以免盘上残留的 `run`/`run_iters` 把
  //  「本课归云机」这个已经不存在的语义继续留在读面里。
  const cfg = loadConfig()
  const row: Record<string, unknown> = { ...cfg.courses?.[course] }
  const notes: string[] = []
  let dirty = false
  if ('run_iters' in row) {
    delete row.run_iters
    dirty = true
    notes.push(`已清退役键 courses.${course}.run_iters（★M2：段长不再是「归云机」的声明）`)
  }
  if (row.rollout_src === 'run') {
    delete row.rollout_src
    dirty = true
    notes.push(`已清退役值 courses.${course}.rollout_src='run'（★M2：同上）`)
  }
  if (opts.rolloutSrc) {
    row.rollout_src = opts.rolloutSrc
    dirty = true
    notes.push(`rollout 位置覆盖：courses.${course}.rollout_src=${opts.rolloutSrc}`)
  }
  // 没删没写就不碰盘：rl-config 的 mtime 是 hub 热重载的输入之一（同 `pruneStoppedCourseConfig`）。
  if (dirty) {
    const courses = { ...cfg.courses }
    // 清完为空 ⇒ 整条节点删（「空节点不留痕」，与 `pruneStoppedCourseConfig` 同规）。
    if (Object.keys(row).length === 0) delete courses[course]
    else courses[course] = row as CourseConf
    saveConfig({ ...cfg, courses })
  }
  return { notes }
}

// ★M4：`pushHubMode`（开课/停课的 hub 模式推送 + 有界重试）与 `HubModeRetry` 一起删除
// ——hub 侧没有课程模式了（`/admin/courses?mode=` 退役），“重试到 hub 认这门课”这个
// 语义也随模式一起消失：认课本身由训练侧发布 job / 开课建 `remote-jobs/` 完成。

// ────────────────────────── 开课 / 停课 ──────────────────────────

/** 本课的两个锁持有人事实 + 「是否真冲突」的判定（开课的前置检查只吃这一个真相）。
 *
 *  ★ **必须带身份看，不能只看「锁活着」**（2026-09-20 用户报障：「❌ x20-steady 未开课：
 *  run_rl 锁被 PID 18364 持有」）——那个 PID 就是控制台自己起的**共享 trainer**
 *  （`trainer/run_rl_cluster.py --serve`）：它服务多课，每开一门课就取该课自己的 per-course 锁
 *  （单进程多课模型的正常持有）。把这种持有当成冲突 ⇒ **每次开课都被自己人拒**，
 *  而拒的同时盘上已经写过开课标记（见下面 ① 的顺序注释）= 一句假回执。
 *
 *  判据：该课锁的持有人 == **共享 trainer 的进程级锁**（`nn-training/.run_cluster.lock`）
 *  持有人 ⇒ 同一个进程 ⇒ 正常状态，不拦；持有者活着但不是它 ⇒ 真冲突（有人在手工跑
 *  `trainer/run_rl.py --course <本课>`，与共享 trainer 抢同一批 traj）⇒ 拦。陈旧锁（持有人已死）
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

// ★M4：`shouldAutoBaseline(trainMode)` 已删——它存在的唯一理由是「离线开课 = 本机不跑训练
// ⇒ it0 读数两条产路都不通，得在开课那一刻补一次」（2026-09-24）。课程去模式化后开课
// **恒为本机跑**，主循环的基线派发（`trainer/loop_baseline.py`）就在场上，不需要替代产路。

/** **开课**：把一门课放进训练（进程没跑也能放——训练进程是发现式的，下一拍就入队）。 */
export async function openCourse(course: string, opts: OpenCourseOpts = {}): Promise<ActionResult> {
  guard(`course-open:${course}`)
  try {
    const c = String(course ?? '').trim()
    if (!c) throw new ActionError('需要课程（开课是按课程记的，见顶部课程选择）')
    validateCourseName(c)
    assertCourseExists(c)
    const bc = isBcCourse(c)
    // ★ 起点解析（G4-①）也是**读**，和下面的检查同区（拒绝 = 零副作用）：封存起点解析不到
    //   就响亮拒，绝不退回 BC 播种（那会静默拿错起点）。
    let seedPath: string | undefined
    if (opts.seedFrom) {
      const found = resolveArchivedSeedPath(opts.seedFrom.sourceCourse, opts.seedFrom.it)
      if (!found) {
        throw new ActionError(
          `起点不可用：封存课 ${opts.seedFrom.sourceCourse} 的 it${opts.seedFrom.it} 在 ` +
            'nn-training/weights/ 下找不到归档权重（先确认它确实被封存过、该轮在关键轮里）。' +
            '本次开课未写任何东西。',
        )
      }
      seedPath = found
    }
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
    // ★M4：这里曾有「离线开课要求课程声明 iters>0」的预校验（任务包必须有终点）。开课不再
    //   有模式 ⇒ 它随 `declaredCourseIters` 一起删；同一个约束**仍在生它的地方**守着：
    //   导出任务包的那条腿（python `trainer/loop_export.py::_export_offline_bundle` 需要
    //   课程声明 iters，否则拒导），以及 hub 的缺包自愈门（没有包就建不了 hold）。
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
      ...prepareCourseForOpen(c, seedPath).notes,
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
    // ★M4（plan/worker-type-dispatch-model §3-M4，需求 2）：**开课不再指定模式**——没有
    //   hub 模式可推（hub 侧 `_modes` 连同 `/admin/courses?mode=` 一起退役），也不再在开课时
    //   自动导任务包。任务包改由**需求侧**触发：自主 worker claim 遇缺包 ⇒ hub 记
    //   `pending_export` 并 POST `/api/autoOfflineHandoff`（`actions/auto-offline-handoff.ts`）
    //   ⇒ 控制台导包。这样「包」只为真实提出需求的云机而造，而不是每次开课替它预造一份
    //   可能已经过期的快照（开课时那份包在代码/权重变动后就是旧包，规则表 ④' 会作废重导）。
    const face = remoteExecutionFace(loadConfig())
    return done(true, `已开课 ${c}——已进入调度课程表（本机采样；云机 claim 成功后才建立接管）`, [
      `本课（${c}）执行面：${face.text}${face.detail ? `（${face.detail}）` : ''}`,
      ...notes,
      // 机器侧旋钮在**开课时**施加（python `apply_course_machine_overrides`）：已经开着的课
      // 要等它重开才换旋钮——暂停该课 → 重启共享 trainer（或等引擎驱逐重开）。
      '★ 机器侧旋钮（降级本机等）在开课时施加：已开着的课要**停课 → 重新开课**（或重启共享 trainer）才换。',
      '★ 共享 trainer 没在跑也能开课——它一起就会扫到已开课的课程；进程与课程是两件事。',
    ])
  } catch (e) {
    if (e instanceof ActionError) throw e
    throw new ActionError(e instanceof Error ? e.message : String(e))
  } finally {
    release(`course-open:${course}`)
  }
}

/** **停课**：非破坏 —— 删开课标记（训练侧与 hub 认课的**唯一 opt-out**）+ 写暂停意图。
 *
 *  与「暂停」按钮的关系：那条也只写暂停意图（同一份契约），但**不动开课标记**——所以停课
 *  才是在训状态的开关（hub 侧不可领的唯一 opt-out，见 plan §1.3 状态表）。
 *  ★M4：停课**不再推 hub**（hub 没有课程模式了）——在跑 hold 不杀（云机上那份只能由操作员在
 *  云机侧停），已入队的 job 仍在队列里，恢复（开课）后从原处接着跑。
 *  课程表 / 账本 / 队列一律不动：恢复走「开课」。
 *
 *  ★ 2026-10-02（用户口径）：停课顺手**清残留**——`courses.<课>.rollout_src='local'`
 *  （缺省档不留痕）与清空的课程节点整条删（`course-knobs.ts::pruneStoppedCourseConfig`）。
 *  显式 `node`/`auto`/`run` 与任何训练语义键不动——这是停课唯一的配置写面。
 */
/** **强制解除接管**（plan §1.3 状态表的「强制解除」行）：人到控制台把一门被自主 worker 接管的
 *  课踢下来。
 *
 *  为什么必须有这颗钮：**live 的接管不可被顶**（不变量 3，`?takeover=1` 也不再能覆盖）——
 *  取代那条旧路的是两条出口：① 它自己的进度静默超阈（自动）；② 人来点这一颗（人工）。
 *  没有它，「云机卡死但还在心跳」就是一个只能等 TTL 的死局。
 *
 *  语义 = hub 的 `revoke_offline_lease`（立墓碑）：旧持有者下次心跳/打点收 409、新盘 claim
 *  直接覆盖；本机与本课协作派发下一拍即恢复（hold 不再是 live）。
 *  多候选基址逐个试（与旧 `pushMode` 同形：账本里活着的 hub 优先，末位槽位兜底）。
 */
export async function releaseCourseHold(course: string): Promise<ActionResult> {
  const c = String(course ?? '').trim()
  if (!c) return done(false, '需要课程（接管是按课记的）')
  // 与已删的 `pushMode` 同一套候选枚举：本模块已 import `loadConfig`。
  const cfg = loadConfig()
  const token = String(cfg.rl?.remote_token ?? '')
  const candidates = hubCandidates(cfg, c)
  let last = '没有可试的 hub 地址'
  for (const base of candidates) {
    const r = await hubReleaseCourseHold(base, token, c)
    if (r.ok) return done(true, `${c}：${r.message}`)
    last = r.message
  }
  return done(false, `${c} 未能解除接管：${last}`)
}

export async function stopCourse(course: string): Promise<ActionResult> {
  guard(`course-stop:${course}`)
  try {
    const c = String(course ?? '').trim()
    if (!c) throw new ActionError('需要课程（停课是按课程记的，见顶部课程选择）')
    validateCourseName(c)
    // ⓪ **先清残留**（2026-10-02 用户口径）：`rollout_src='local'` 缺省档 + 空节点整条删。
    //    排在最前：这是唯一可能抛的一步（`saveConfig` 容量/槽位守卫）——写不进去就整体中止，
    //    零副作用（标记/暂停/hub 都还没动）。
    const pruned = pruneStoppedCourseConfig(c)
    // ① **删开课标记**（训练侧/hub 靠它认「在训」）：不删它，调度器下一拍又把这门课拉起来
    //    （发现式进程只认盘上事实，控制台说什么都没用）。
    const marker = courseEnableMarkerPath(c)
    const hadMarker = existsSync(marker)
    rmSync(marker, { force: true })
    // ② 暂停意图（表内暂停，双保险：万一标记被手工建回来/还在旧进程的内存表里）
    const pause = setCoursePaused(c, true)
    // ★M4：原来的第 ③ 步「hub 该课置 offline」已删（hub 无模式）。开课标记是 hub 认课的
    //   唯一 opt-out（`role_blocked` 的停课腿），删它就够了。
    const notes = [
      ...(pruned.length > 0 ? [`rl-config 清理：${pruned.join('；')}`] : []),
      hadMarker
        ? '已删开课标记 training-enabled.txt（训练侧不再把这门课当在训，hub 也不可领）'
        : '本课本就没有开课标记',
      `暂停意图：${pause.message}`,
      '队列与账本一个字不动：已入队的 job 仍在队列里，恢复（开课）后从原处接着跑。',
      '账本/iter/指标仍可看（课程下拉不筛历史课）——这是**非破坏**停课，不是下架。',
      '★ 在云的接管（hold）不杀：云机上正在跑的那份只能由操作员在云机侧停；' +
        '想要它立刻让出，到课程矩阵点「强制解除接管」。',
    ]
    return done(
      pause.ok,
      pause.ok ? `已停课 ${c}（已删开课标记 + 本机调度暂停；hub 侧不再可领）` : '停课未生效',
      notes,
    )
  } catch (e) {
    if (e instanceof ActionError) throw e
    throw new ActionError(e instanceof Error ? e.message : String(e))
  } finally {
    release(`course-stop:${course}`)
  }
}
