/** train-smoke.ts — 推送链路预演（BcLoop 语料 → 伪 GPU 节点 → 落位作废；TrainingLoop 冒烟）。 */
import { existsSync } from 'fs'
import path from 'path'
import { loadConfig, validateCourseArg } from '../../core/config'
import { killPid, pidAlive } from '../../core/net'
import { LOG_DIR, REPO_ROOT } from '../../core/paths'
import { launchSpec } from '../../core/proc'
import { clearAnyComponent, saveAnyComponent } from '../../core/registry'
import { monitorTouch } from '../../core/reload-touch'
import { slotOf } from '../../core/slots'
import { resolveVenvPython } from '../../core/venv'
import { isBcCourse, seedWeightsFromBc } from '../../stack/courses'
import { stepBcSmokeRehearsal, stepKaggleRehearsal } from '../../stack/hub'
import { startLocalWorkerServer } from '../../stack/push'
import { BC_LOOP_ENTRY, bcLoopSpec, TRAINING_LOOP_ENTRY, trainingLoopSpec } from '../../stack/specs'
import { entryOf } from './cloud-halt'
import { tailLines } from './labels'
import { ActionError, ActionResult, done, guard, release } from './result'

// ────────────────────────── 推送链路预演（原 start.ts push --smoke-only） ──────────────────────────

/** 推送链路端到端预演：本机伪 GPU 节点（remote_worker_serve echo）+ 真课程
 *  TrainingLoop（--smoke）发布真 job 并推送 → 伪节点 echo 回显 → 三重校验落位 →
 *  作废本轮干净退出（it 不前进、账本零污染，DECISIONS §340）。不跑真 PPO。
 *  完成/失败后都停掉预演用 TrainingLoop（--smoke 进程没有 echo 结果会一直等待）。 */
/** BC 冒烟预演（BcLoop）：语料 1 局 God-AI → kind=bc job → 本机伪 GPU 节点
 *  **真 BC 训练 1 epoch** → 回传落位 → 作废退出。与 RL smokeTrain 的差异：
 *  无 BC 种子播种（BC 无 warm-start）、无 echo（伪节点跑真训练）、
 *  三里程碑 = published job → weights landed → BC SMOKE PASS。 */
async function smokeTrainBc(course: string): Promise<ActionResult> {
  let servePid = 0
  try {
    const cfg = loadConfig()
    if (pidAlive(entryOf('trainingLoop', course)?.pid))
      return done(false, 'BcLoop 已在运行（可能是真训练）——预演会干扰在途 job，先停止')
    const venv = resolveVenvPython()

    // 1) 本机伪 GPU 节点
    const { pushUrl, servePid: pid } = await startLocalWorkerServer({ course, cfg, venv })
    servePid = pid

    // 2) 真 BC 课程 run_bc.py --smoke（REMOTE_PUSH_NODE 注入伪节点）
    const trainLog = path.join(LOG_DIR, course, 'training-loop.log')
    const spec = bcLoopSpec(cfg, {
      course,
      ppo: 'remote',
      smoke: true,
      pushNodeUrl: pushUrl,
      venv,
    })
    const r = launchSpec(spec)
    saveAnyComponent('trainingLoop', course, {
      pid: r.pid,
      course,
      slot: slotOf(cfg, course),
      entry: BC_LOOP_ENTRY,
      mode: 'remote',
      log: trainLog,
    })
    monitorTouch()

    // 3) 预演等三段里程碑，任何一段失败都停预演进程
    try {
      await stepBcSmokeRehearsal(course, r.pid)
    } catch (e) {
      if (pidAlive(r.pid)) {
        await killPid(r.pid)
        clearAnyComponent('trainingLoop', course)
      }
      return done(
        false,
        `BC 冒烟预演未通过: ${e instanceof Error ? e.message : e}`,
        tailLines(trainLog, 6),
      )
    }
    return done(
      true,
      'BC 冒烟预演全通过（采集→发布→真 BC 训练→回传→落位→作废；真训练零污染）',
      tailLines(trainLog, 4),
    )
  } catch (e) {
    return done(false, `BC 预演失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    if (servePid) {
      const { killPid } = await import('../../core/net')
      await killPid(servePid)
    }
    // 与 smokeTrain 的 guard 同键（按课）：旧代码 release('smoke:train') 与按课
    // guard 键不同源 ⇒ 该课程第一次预演后永久 409（2026-09-14 事故同源）。
    release(`smoke:train:${course}`)
  }
}

export async function smokeTrain(course: string): Promise<ActionResult> {
  // M2：busy 键按课（两课可同时冒烟；全局键会第二次 409）。P5 才全量 course-keyed，
  // smokeTrain 是提前的那一个（P3 全链路验收要双课同冒）。
  // guard / release 必须同键（见上）。
  const smokeKey = `smoke:train:${course}`
  guard(smokeKey)
  // BC 课程 → BcLoop 冒烟预演（真 BC 训练 1 epoch，无 echo）
  if (isBcCourse(course)) return await smokeTrainBc(course)
  let servePid = 0
  try {
    if (!course) throw new ActionError('需要 course（先在顶部设置课程）')
    validateCourseArg(course)
    const cfg = loadConfig()
    if (pidAlive(entryOf('trainingLoop', course)?.pid))
      return done(false, 'TrainingLoop 已在运行（可能是真训练）——预演会干扰在途 job，先停止')
    const venv = resolveVenvPython()
    const trajDir = path.join(REPO_ROOT, 'tmp', course)
    const weightsPath = path.join(trajDir, 'weights.json')
    if (!existsSync(weightsPath)) {
      try {
        seedWeightsFromBc(course, weightsPath)
      } catch (e) {
        return done(false, e instanceof Error ? e.message : String(e))
      }
    }

    // 1) 本机伪 GPU 节点
    const { pushUrl, servePid: pid } = await startLocalWorkerServer({ course, cfg, venv })
    servePid = pid

    // 2) 真课程 TrainingLoop --smoke（REMOTE_PUSH_NODE 注入伪节点）
    const trainLog = path.join(LOG_DIR, course, 'training-loop.log')
    const spec = trainingLoopSpec(cfg, {
      course,
      ppo: 'remote',
      smoke: true,
      pushNodeUrl: pushUrl,
      venv,
    })
    const r = launchSpec(spec)
    saveAnyComponent('trainingLoop', course, {
      pid: r.pid,
      course,
      slot: slotOf(cfg, course),
      entry: TRAINING_LOOP_ENTRY,
      mode: 'remote',
      log: trainLog,
    })
    monitorTouch()

    // 3) 预演等三段日志触发（发布 → 落位 → 作废退出），任何一段失败都停预演进程
    try {
      await stepKaggleRehearsal(course, r.pid)
    } catch (e) {
      if (pidAlive(r.pid)) {
        await killPid(r.pid)
        clearAnyComponent('trainingLoop', course)
      }
      return done(
        false,
        `推送链路预演未通过: ${e instanceof Error ? e.message : e}`,
        tailLines(trainLog, 6),
      )
    }
    return done(
      true,
      '推送链路预演全通过（发布→推送→echo→落位→作废；真训练零污染）',
      tailLines(trainLog, 4),
    )
  } catch (e) {
    return done(false, `预演失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    if (servePid) {
      const { killPid } = await import('../../core/net')
      await killPid(servePid)
    }
    release(smokeKey)
  }
}
