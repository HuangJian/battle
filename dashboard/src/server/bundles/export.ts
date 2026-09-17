/** export.ts — 任务包导出（`task-<课程>.zip`）：把「整段剩余」交给云机的入口。
 *
 *  这条腿的全部智能在 python 一侧（`run_rl.py --export-bundle` 复用发布链造 manifest +
 *  计划 + 代码快照）。控制台只做三件事：**拼参数**、**起一次**、**把文件交给浏览器**。
 *  刻意不在这里重造包内容——「拿一个不含 args 的 CLI 去重造它等于造第二份真相」
 *  （`remote/bundle.py` 的模块注释里写着这条）。
 *
 *  为什么一次导出 = 整段剩余（`--run-iters -1`）：离线包的意义就是「交出去就别管了」，
 *  而包里的计划必须有终点（跑到哪停是任务定义的一部分）。分段导出留给以后要分段的人：
 *  改这一个常量即可（`exportRunIters`）。
 */

import { existsSync, statSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import { pidAlive, sleep } from '../../core/net'
import { courseLogDir } from '../../stack/specs'
import { busy, release } from '../actions/result'
import { runRlLockHolder, tailLines } from '../actions/labels'
import { spawnRunPython } from '../run-python'

/** 一次导出的段长：`-1` = 到课程末尾（见文件头注释）。 */
export const exportRunIters = -1

/** 导出进行中的互斥键。**释放靠进程退出**（不是请求返回）：导出要跑几分钟，
 *  在请求里 add/delete 等于没有锁——第二次点会起第二个 run_rl 去抢同一门课的锁。 */
export const TASK_BUNDLE_BUSY_KEY = 'bundle:export'

export function taskBundleFileName(course: string): string {
  return `task-${course}.zip`
}

/** 产出 zip 的落点：`tmp/<课程>/task-<课程>.zip`（课程目录下，人一眼能找到）。 */
export function taskBundlePath(course: string): string {
  return path.join(REPO_ROOT, 'tmp', course, taskBundleFileName(course))
}

export function taskBundleLogPath(course: string): string {
  return path.join(courseLogDir(course), 'export-bundle.log')
}

export interface TaskBundleInfo {
  course: string
  name: string
  path: string
  exists: boolean
  bytes: number
  mtimeMs: number
  log: string
}

export function taskBundleInfo(course: string): TaskBundleInfo {
  const p = taskBundlePath(course)
  let bytes = 0
  let mtimeMs = 0
  let exists = false
  try {
    const st = statSync(p)
    exists = st.isFile()
    bytes = st.size
    mtimeMs = st.mtimeMs
  } catch {
    /* 还没导出过 */
  }
  return {
    course,
    name: taskBundleFileName(course),
    path: p,
    exists,
    bytes,
    mtimeMs,
    log: taskBundleLogPath(course),
  }
}

/** 导出前的门（纯函数；返回非空 = 拒启原因）。**两条理由各有各的代价**：
 *
 *  * **训练在跑**：`run_rl` 有 per-course 单实例锁（双开会让两条腿各自写同一门课的
 *    traj/权重）——导出跑的是同一个入口，所以**不绕过锁**，而是把话说清楚。
 *  * **没有起点权重**：包里必须有个起点（`--export-bundle` 在 python 一侧也拒导）。
 */
export function exportGuardReason(arg: {
  lockHolder: number | null
  weightsExists: boolean
}): string | null {
  if (arg.lockHolder)
    return (
      `训练正在跑（run_rl 锁被 PID ${arg.lockHolder} 持有）——先停止训练再导出：` +
      '包里的起点权重就是当前进度，导出与训练并行会让两条腿从同一轮各自往下跑。'
    )
  if (!arg.weightsExists)
    return '没有起点权重（tmp/<课程>/weights.json）——先跑至少一轮，包里得有个起点'
  return null
}

/** 导出前的门（IO 壳：读锁 + 看权重在不在）。 */
export function exportGuard(course: string): string | null {
  return exportGuardReason({
    lockHolder: runRlLockHolder(course),
    weightsExists: existsSync(path.join(REPO_ROOT, 'tmp', course, 'weights.json')),
  })
}

/** 导出一次性进程的 argv（纯函数，便于测试）。 */
export function taskBundleArgs(course: string): string[] {
  return [
    'nn-training/run_rl.py',
    '--course',
    course,
    '--ppo',
    'remote',
    '--run-iters',
    String(exportRunIters),
    '--export-bundle',
    taskBundlePath(course),
  ]
}

export interface ExportLaunchResult {
  ok: boolean
  message: string
  detail?: string[]
  info?: TaskBundleInfo
}

/** 起一次导出（detach；日志 `logs/<课程>/export-bundle.log`）。**不注册组件**——
 *  它是一次性动作，进组件账本会被监督器当成「该重启的组件」。
 *
 *  互斥（`TASK_BUNDLE_BUSY_KEY`）在**子进程退出**时释放：轮询 `pidAlive`（不是 `busySince`
 *  那套 TTL——导出合法地会跑过 5 分钟，TTL 清扫会在中途解锁）。 */
export function launchTaskBundleExport(course: string): ExportLaunchResult {
  if (busy.has(TASK_BUNDLE_BUSY_KEY))
    return {
      ok: false,
      message: '上一次任务包导出还没跑完（产物 zip 会在过程中重写）——稍等或看日志',
    }
  const blocked = exportGuard(course)
  if (blocked) return { ok: false, message: blocked }
  const argv = taskBundleArgs(course)
  try {
    const r = spawnRunPython(argv[0], argv.slice(1), taskBundleLogPath(course))
    busy.add(TASK_BUNDLE_BUSY_KEY)
    void watchExportExit(r.pid)
    return {
      ok: true,
      message:
        `任务包导出已启动（PID ${r.pid}，整段剩余）；完成后用「下载」拿到 ` +
        `${taskBundleFileName(course)}（日志 ${path.relative(REPO_ROOT, taskBundleLogPath(course))}）`,
      info: taskBundleInfo(course),
    }
  } catch (e) {
    return {
      ok: false,
      message: `导出启动失败：${e instanceof Error ? e.message : String(e)}`,
      detail: tailLines(taskBundleLogPath(course)),
    }
  }
}

/** 等导出进程退出就解锁（每 2s 看一眼 pid；控制台进程自己重启则键随进程消失）。 */
async function watchExportExit(pid: number): Promise<void> {
  for (let i = 0; i < 8 * 60 * 30 && pidAlive(pid); i++) await sleep(2000)
  release(TASK_BUNDLE_BUSY_KEY)
}

/** 下载响应（`Content-Disposition` 用习惯文件名——人拿到手就是这个姓名）。
 *
 *  用 `Bun.file` 当 body（不是读进内存）：产物包几 MB 到几十 MB，流式发送零风险。
 *  文件在响应发出前已 stat 过（存在性由调用方判），中途被删只会表现为截断连接。
 */
export function taskBundleDownloadResponse(course: string): Response | null {
  const info = taskBundleInfo(course)
  if (!info.exists) return null
  return new Response(Bun.file(info.path), {
    headers: {
      'Content-Type': 'application/zip',
      'Content-Disposition': `attachment; filename="${info.name}"`,
      'Content-Length': String(info.bytes),
    },
  })
}
