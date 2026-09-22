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

import { appendFileSync, existsSync, mkdirSync, readdirSync, renameSync, statSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import { pidAlive, sleep } from '../../core/net'
import { courseLogDir } from '../../stack/specs'
import { busy, release } from '../actions/result'
import { tailLines } from '../actions/labels'
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

/** 作废任务包的归档目录（**挪走而非删除**：导出失败时它还是唯一能跑的东西）。 */
export function stalePackDir(course: string): string {
  return path.join(REPO_ROOT, 'tmp', course, 'stale-packs')
}

/** 把现有任务包挪去归档（作废），返回 `{invalidated, archived, message}`。**永不抛**。
 *
 *  为什么要作废而不只是「重新导出一份」：任务包是**代码快照**（`code.zip` + `ts_code.zip` +
 *  commit），而 hub 的 `GET /offline/task-pack` 递的就是**盘上那一刻的文件**。若重启离线课只
 *  在后台重新导出、旧包还躺在盘上，云机在导出窗口（几分钟）里探到的仍是**旧代码的包**——
 *  它会拿旧代码跑完整段，而且看起来完全正常（用户 2026-09-22 指令：「重启离线课程时，需要把
 *  最新代码重新打进任务包，因为代码可能已经发生了变化」）。
 *
 *  作废 = 把文件**掉**成 `tmp/<课>/stale-packs/task-<课>.zip.stale-<时间戳>`：此后
 *  `/offline/task-pack` 会 404（host 的下载按钮同理），而云机的等包循环本来就按
 *  「404 → 稍后重试」处理（`offline_boot.obtain_pack`，默认等 30 分钟）⇒ 它会一直等到新包
 *  导出完成，而不是默默退回到旧代码。
 *
 *  失败（权限/盘满）不抛也不阻断开课：返回 `invalidated:false` + 原因，由调用方写进回执
 *  ——那种情况下盘上仍有一份旧包，而「旧包已过期」这件事必须在回执里说出来。
 */
export function invalidateTaskBundle(course: string): {
  invalidated: boolean
  archived: string
  message: string
} {
  const src = taskBundlePath(course)
  if (!existsSync(src)) {
    return { invalidated: false, archived: '', message: '本课还没有任务包（云机会等新导出的那份）' }
  }
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const dest = path.join(stalePackDir(course), `${taskBundleFileName(course)}.stale-${stamp}`)
  try {
    mkdirSync(path.dirname(dest), { recursive: true })
    renameSync(src, dest)
    return {
      invalidated: true,
      archived: dest,
      message: `旧任务包已作废（代码可能已变）→ ${path.relative(REPO_ROOT, dest)}`,
    }
  } catch (e) {
    return {
      invalidated: false,
      archived: '',
      message: `挪走旧任务包失败（${e instanceof Error ? e.message : String(e)}）——盘上仍是旧代码的包`,
    }
  }
}

/** 归档里**最新**的那份旧包（没有 = null）。`watchExportExit` 用它做失败兜底。 */
export function latestStalePack(course: string): string | null {
  const dir = stalePackDir(course)
  if (!existsSync(dir)) return null
  try {
    const pre = `${taskBundleFileName(course)}.stale-`
    const fits = readdirSync(dir)
      .filter((n) => n.startsWith(pre))
      .map((n) => path.join(dir, n))
    if (fits.length === 0) return null
    fits.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)
    return fits[0]
  } catch {
    return null
  }
}

/** 把一份归档包**恢复**到线上路径（作废的逆操作）。**永不抛**。
 *
 *  为什么需要它：作废是「先挪走、再导出」，于是**导出没成功**就成了「这门课没有包」——
 *  而旧包是那时唯一还能跑的东西（用户点「训练」= 想立刻有东西能跑）。恢复保住这条下限，
 *  云机不会为一个起不来的导出白等 30 分钟。
 */
export function restoreTaskBundle(
  course: string,
  archived: string,
): { restored: boolean; message: string } {
  const live = taskBundlePath(course)
  try {
    if (existsSync(live)) return { restored: false, message: '线上已有新包（无需恢复）' }
    if (!archived || !existsSync(archived))
      return { restored: false, message: '归档里没有可恢复的包' }
    renameSync(archived, live)
    return { restored: true, message: `已恢复旧包 ${path.relative(REPO_ROOT, live)}` }
  } catch (e) {
    return {
      restored: false,
      message: `恢复旧包失败（${e instanceof Error ? e.message : String(e)}）——请手动从 ${path.relative(REPO_ROOT, archived)} 取回`,
    }
  }
}

/** 导出**进程退出后**的兜底：没产出新包就把最新的归档包恢复正常。
 *
 *  只看「线上路径有没有文件」——新包是原子 replace 写出来的，所以要么在、要么不在，
 *  不用比对时间戳（导出中途失败 / 子进程被杀都落进「不在」这一支）。
 */
export function restorePackIfMissing(course: string): string | null {
  if (existsSync(taskBundlePath(course))) return null
  const stale = latestStalePack(course)
  if (!stale) return null
  const r = restoreTaskBundle(course, stale)
  if (!r.restored) return null
  try {
    appendFileSync(
      taskBundleLogPath(course),
      `\n[作废兜底] 本次导出没产出新包 → ${r.message}\n`,
      'utf-8',
    )
  } catch {
    /* 日志写不进去不影响恢复本身 */
  }
  return r.message
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

/** 导出前的门（纯函数；返回非空 = 拒启原因）。
 *
 *  ★ 2026-09-22（课程错误隔离 + 随时导出）：**不再以「训练在跑（run_rl 锁被占）」拒导**——
 *  导出是只读快照（run_rl `--export-bundle` 不取 per-course 锁、不推进账本），
 *  开课后随时可导出/重导（离线整段由此可反复以最新进度为起点出包）。
 *  保留的拒绝理由：**没有起点权重**（包里必须有个起点，python 侧同判据）。
 */
export function exportGuardReason(arg: { weightsExists: boolean }): string | null {
  if (!arg.weightsExists)
    return '没有起点权重（tmp/<课程>/weights.json）——先跑至少一轮，包里得有个起点'
  return null
}

/** 导出前的门（IO 壳：看权重在不在）。 */
export function exportGuard(course: string): string | null {
  return exportGuardReason({
    weightsExists: existsSync(path.join(REPO_ROOT, 'tmp', course, 'weights.json')),
  })
}

/** 导出一次性进程的 argv（纯函数，便于测试）。 */
export function taskBundleArgs(course: string): string[] {
  return [
    'nn-training/run_rl.py',
    '--course',
    course,
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
  /** 旧包是否已作废（+归档路径）：回执据此说清「云机在导出完成前取不到包」。 */
  invalidated?: boolean
  archived?: string
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
  // ★ 2026-09-22：导出 = **重新打一份带当前代码的包** ⇒ 先把旧包作废（掉进 stale-packs）。
  //   放在 exportGuard **之后**：没起点权重这类拒启不该先把旧包挪走（那会平白让
  //   云机取不到包，而导出的前提本来就没满足）。
  const stale = invalidateTaskBundle(course)
  const argv = taskBundleArgs(course)
  try {
    const r = spawnRunPython(argv[0], argv.slice(1), taskBundleLogPath(course))
    busy.add(TASK_BUNDLE_BUSY_KEY)
    void watchExportExit(r.pid, course)
    return {
      ok: true,
      message:
        (stale.invalidated ? `${stale.message}；` : '') +
        `任务包导出已启动（PID ${r.pid}，整段剩余）；完成后用「下载」拿到 ` +
        `${taskBundleFileName(course)}（日志 ${path.relative(REPO_ROOT, taskBundleLogPath(course))}）` +
        (stale.invalidated
          ? '。导出完成前 hub 的 /offline/task-pack 会 404，云机会等新包来（不会退回旧代码）'
          : ''),
      info: taskBundleInfo(course),
      invalidated: stale.invalidated,
      archived: stale.archived,
    }
  } catch (e) {
    // 起不来 ⇒ 立刻把刚挪走的旧包放回去（否则这门课在盘上就没有包可递了；见 restoreTaskBundle）。
    const back = stale.invalidated ? restoreTaskBundle(course, stale.archived) : null
    return {
      ok: false,
      message:
        `导出启动失败：${e instanceof Error ? e.message : String(e)}` +
        (back ? `；${back.message}` : ''),
      detail: tailLines(taskBundleLogPath(course)),
    }
  }
}

/** 等导出进程退出就解锁（每 2s 看一眼 pid；控制台进程自己重启则键随进程消失）。
 *
 *  退出后还有一道兜底：**没产出新包就把旧包恢复**（见 restorePackIfMissing）——作废 + 导出失败
 *  不能变成「这门课没有包」，那是把人锁在云机上干等。 */
async function watchExportExit(pid: number, course: string): Promise<void> {
  for (let i = 0; i < 8 * 60 * 30 && pidAlive(pid); i++) await sleep(2000)
  release(TASK_BUNDLE_BUSY_KEY)
  restorePackIfMissing(course)
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
