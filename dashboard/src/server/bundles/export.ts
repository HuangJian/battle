/** export.ts — 任务包导出（`task-<课程>.zip`）：把「整段剩余」交给云机的入口。
 *
 *  这条腿的全部智能在 python 一侧（`trainer/run_rl.py --export-bundle` 复用发布链造 manifest +
 *  计划 + 代码快照）。控制台只做三件事：**拼参数**、**起一次**、**把文件交给浏览器**。
 *  刻意不在这里重造包内容——「拿一个不含 args 的 CLI 去重造它等于造第二份真相」
 *  （`remote/bundle.py` 的模块注释里写着这条）。
 *
 *  为什么一次导出 = 整段剩余（`--run-iters -1`）：离线包的意义就是「交出去就别管了」，
 *  而包里的计划必须有终点（跑到哪停是任务定义的一部分）。分段导出留给以后要分段的人：
 *  改这一个常量即可（`exportRunIters`）。
 */

import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  statSync,
  writeFileSync,
} from 'fs'
import path from 'path'
import { REPO_ROOT, tmpCourseDir } from '../../core/paths'
import { declaredCourseIters } from '../../stack/courses'
import {
  bundleMetaMagic,
  bundleMetaSuffix,
  taskBundleStatusFileName,
  taskBundleStatusMagic,
  taskBundleStatusProto,
} from './marks'
import { killPidTree, pidAlive, pidCommandLine, sleep } from '../../core/net'
import { warn as logWarn } from '../../core/log'
import { courseLogDir } from '../../stack/specs'
import { busy, release } from '../actions/result'
import { tailLines } from '../actions/labels'
import { spawnRunPython } from '../run-python'

/** 一次导出的段长：`-1` = 到课程末尾（见文件头注释）。 */
export const exportRunIters = -1

/** 一次导出的**硬上界**（毫秒）。正常 10s~2min；30 分钟对齐云机等包的窗口
 *  （`offline_boot.obtain_pack`，见 `invalidateTaskBundle` 的注释）。
 *
 *  为什么不是 5 分钟：导出合法地会跑很久（打包 4.4MB + 重代码快照）；`busySince` 那套
 *  单动作 TTL 不适用于导出。30 分钟是**看门狗**，不是常规期望。 */
export const EXPORT_TIMEOUT_MS = 30 * 60 * 1000
/** 看门狗轮询间隔（毫秒）。 */
export const EXPORT_POLL_MS = 2000

/** 导出的互斥键：**按课程分键**（2026-10-21，plan/export-bundle-stall §3.1(1)）。
 *
 *  全局单键会把「A 课在导」变成「B 课也导不了」——两门课的包落在不同目录，产物零共享。
 *  同课互斥仍在（包互相覆盖 `tmp.replace`，见 `bundle.py:212/218`）。 */
export function taskBundleBusyKey(course: string): string {
  return `bundle:export:${course}`
}

export function taskBundleFileName(course: string): string {
  return `task-${course}.zip`
}

/** 产出 zip 的落点：`tmp/<课程>/task-<课程>.zip`（课程目录下，人一眼能找到）。 */
export function taskBundlePath(course: string): string {
  return path.join(tmpCourseDir(course), taskBundleFileName(course))
}

export function taskBundleLogPath(course: string): string {
  return path.join(courseLogDir(course), 'export-bundle.log')
}

/** 任务包**旁挂元数据**路径（`<包路径>.meta.json`）。
 *
 *  跨语言契约：python 侧 `remote/bundle.py::bundle_meta_path` 按同一条规则拼（`+ 后缀`），
 *  导出时原子写；`tests/auto-offline-handoff.test.ts` 读 python 源对账，防两边写岔
 *  （写岔的后果是判据**静默**退化回旧 mtime 口径，不报错）。
 */
export function taskBundleMetaPath(course: string): string {
  return `${taskBundlePath(course)}${bundleMetaSuffix}`
}

/** 导出**成败状态**的旁挂件名与落点（与包**同目录**：`tmp/<课程>/export-status.json`）。
 *
 *  跨语言契约：python 侧 `remote/bundle.py::EXPORT_STATUS_NAME` / `export_status_path()`
 *  （同一个进程在**退出层**原子写：临时文件 + `replace`）；`tests/auto-offline-handoff.test.ts`
 *  读 python 源对账（与 `BUNDLE_META_SUFFIX` 同一手法），防两边写岔。
 *
 *  为什么必须由 python 侧落盘（2026-10-15，plan/offline-open-ended-iters-and-loud-export §3.3 J3）：
 *  导出是 **detached 子进程**（`core/proc.ts` 的 `detached + unref`）⇒ 控制台**拿不到退出码**；
 *  且控制台重启后子进程仍在跑 ⇒ 内存状态不可靠。落盘是唯一跨重启的锚。
 *
 *  落点用 `path.dirname(taskBundlePath(course))` 而不是自己拼 `tmp/<课>/`：与 python 的
 *  `Path(out).parent`（`out` = `--export-bundle` 的取值）**同源**，包搬到别处时不会分家。
 */
export function taskBundleStatusPath(course: string): string {
  return path.join(path.dirname(taskBundlePath(course)), taskBundleStatusFileName)
}

/** 状态文件的形状（python 写全量，控制台只读这几项）。
 *
 *  四态（2026-10-21，plan/export-bundle-stall §3.3）：`ok` / 失败 / `running` / 不可知
 *  （`stale` 早于包作废、`expired` 超时或 pid 不在）。「不可知/进行中」永远不许渲染成成功。
 */
export interface TaskBundleStatus {
  /** `true` = 这次导出真的产出了包，且状态没有过期。 */
  ok: boolean
  /** 导出进程仍在跑（启动时写 `running:true`；过期或 pid 不在 ⇒ false）。 */
  running: boolean
  /** 状态早于本课任务包作废/新包生成 ⇒ 不可知（不是成功）。 */
  stale: boolean
  /** `running` 但超过 30 分钟或 pid 已不在 ⇒ 不可知（不是成功）。 */
  expired: boolean
  /** `running` 时的已跑秒数（其余为 0）。 */
  elapsedSec: number
  exitCode: number
  /** **第一手**的原因（写它的人就是那个进程）。控制台的 `tailFailureReason` 只当没有状态文件时的兜底。 */
  reason: string
  /** 写入时刻（epoch 秒）：hub 用它当「不再重试」的窗口锚（进程内计数会随重启清零）。 */
  at: number
  iters: number
  runIters: number
  pid: number
}

/** 状态 `at` 是秒级浮点；包证据是 ms。比较时留 1 秒容差（文件名戳截到秒）。 */
const STATUS_TIME_SLACK_MS = 1000

/** 从 stale 文件名里解析**作废时刻**（`toISOString` 的 UTC、秒级）。 */
function stalePackStampMs(stamp: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})$/.exec(stamp)
  if (!m) return 0
  return Date.UTC(
    Number(m[1]),
    Number(m[2]) - 1,
    Number(m[3]),
    Number(m[4]),
    Number(m[5]),
    Number(m[6]),
  )
}

/** 本课「包证据」的最新时刻（ms）：线上包 mtime 与 stale 文件名戳取最大。
 *
 *  ⚠ 不能用 `statSync(stale).mtimeMs`：`renameSync` 保留原 mtime，那是包**生成**时间，
 *  不是作废时间。文件名戳才是作废时刻（UTC；按本地时间解析会在 UTC+8 差 8 小时）。
 */
export function latestPackEvidenceMs(course: string): number {
  let ms = 0
  try {
    ms = statSync(taskBundlePath(course)).mtimeMs
  } catch {
    /* 没有线上包 */
  }
  try {
    const pre = `${taskBundleFileName(course)}.stale-`
    for (const name of readdirSync(stalePackDir(course))) {
      if (!name.startsWith(pre)) continue
      const stamp = stalePackStampMs(name.slice(pre.length))
      if (stamp > ms) ms = stamp
    }
  } catch {
    /* 没有 stale-packs */
  }
  return ms
}

/** 这份 `ok:true` 是否描述**作废之前**的那次导出（是 ⇒ 降级成不可知）。 */
function statusIsStale(course: string, at: number): boolean {
  if (at <= 0) return false
  const floor = latestPackEvidenceMs(course)
  return floor > 0 && at * 1000 + STATUS_TIME_SLACK_MS < floor
}

/** 读导出状态（没有 / 坏 JSON / 魔数不符 ⇒ `null`，**永不抛**）。
 *
 *  `null` = **不可知**（还没跑完 / 被 kill / 旧版本），不是成功——调用方必须把这两件事分开说。
 *  `running` / `stale` / `expired` 都在这里收口，面板与 hub 走同一个读面。
 */
export function readTaskBundleStatus(course: string): TaskBundleStatus | null {
  let raw: string
  try {
    raw = readFileSync(taskBundleStatusPath(course), 'utf-8')
  } catch {
    return null
  }
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    const j = parsed as Record<string, unknown>
    if (j.magic !== taskBundleStatusMagic) return null
    const at = Number(j.at ?? 0) || 0
    const pid = Number(j.pid ?? 0) || 0
    const rawRunning = j.running === true
    const rawOk = j.ok === true
    const stale = !rawRunning && statusIsStale(course, at)
    // 「进行中」的到期判据必须**读得到**（不能只活在 watchExportExit 里：控制台重启即消失）。
    const expired =
      rawRunning &&
      (at <= 0 ||
        Date.now() / 1000 - at > EXPORT_TIMEOUT_MS / 1000 ||
        pid <= 0 ||
        !pidAlive(pid) ||
        stale)
    return {
      ok: rawOk && !stale && !expired,
      // `running` = **文件说进行中**（第一手），`expired` = 「但这份进行中已不可信」
      // （超时 / pid 不在）。两者分开：看门狗收尾要分辨「进程还在跑」与「运行态已过期」，
      // 渲染侧永远先看 `expired`（见 `exportStatusLine` / BundleRowActions 的顺序）。
      running: rawRunning,
      stale,
      expired,
      elapsedSec: rawRunning && at > 0 ? Math.max(0, Math.floor(Date.now() / 1000 - at)) : 0,
      exitCode: Number(j.exit_code ?? 0) || 0,
      reason: String(j.reason ?? '').slice(0, 600),
      at,
      iters: Number(j.iters ?? 0) || 0,
      runIters: Number(j.run_iters ?? 0) || 0,
      pid,
    }
  } catch {
    return null
  }
}

/** 控制台侧写一份终态（超时/被 kill/接管）。原子写；永不抛。 */
export interface TaskBundleStatusWrite {
  ok: boolean
  running: boolean
  reason: string
  pid?: number
  at?: number
  iters?: number
  runIters?: number
}

export function writeTaskBundleStatus(course: string, s: TaskBundleStatusWrite): void {
  const body = {
    magic: taskBundleStatusMagic,
    proto: taskBundleStatusProto,
    ok: s.ok,
    running: s.running,
    exit_code: s.ok ? 0 : 1,
    reason: s.reason.slice(0, 600),
    course,
    iters: s.iters ?? 0,
    run_iters: s.runIters ?? 0,
    pid: s.pid ?? 0,
    pack: taskBundlePath(course),
    at: s.at ?? Date.now() / 1000,
  }
  try {
    const side = taskBundleStatusPath(course)
    mkdirSync(path.dirname(side), { recursive: true })
    // 与 python 的 `.tmp` 分开：控制台只在进程已死/已杀之后写，仍避免两个写者交错同一个临时文件。
    const tmp = `${side}.console.tmp`
    writeFileSync(tmp, JSON.stringify(body, null, 1), 'utf-8')
    renameSync(tmp, side)
  } catch {
    /* 旁挂件不配把主腿拖红 */
  }
}

/** 一行**可读**的导出状态（面板与 hub 共用同一句口径）。
 *
 *  顺序是承重的：stale/expired（不可知）→ running（进行中）→ ok → 失败。
 *  前三态永远不许说「成功」——这是「失败要响亮」那条口径的落点。
 */
export function exportStatusLine(course: string): string {
  const status = readTaskBundleStatus(course)
  if (!status) return '导出状态：尚无记录（还没跑完 / 被 kill / 旧版本）——不可知，不等于成功'
  const when = status.at > 0 ? new Date(status.at * 1000).toLocaleString() : '时间未知'
  if (status.stale) return '导出状态不可知（状态早于任务包作废）——不等于成功'
  if (status.expired) return '导出状态不可知（上一次导出超时，或进程已不在）——不等于成功'
  if (status.running) {
    const mins = Math.max(1, Math.round(status.elapsedSec / 60))
    return `正在导出（PID ${status.pid}，已跑 ${mins} 分钟）`
  }
  return status.ok
    ? `上次导出成功（exit ${status.exitCode}，课程 iters=${status.iters}，${when}）`
    : `上次导出失败：${status.reason || `exit ${status.exitCode}`}（${when}）`
}

/** 旁挂元数据的形状（只有判据要的字段；python 写全量，控制台只读这几项）。 */
export interface TaskBundleMeta {
  /** 包内 `code.zip` 的 sha256（= 导出那一刻 `manifest.code_sha256` = 当时的快照 sha）。 */
  codeSha256: string
  tsCodeSha256: string
  commit: string
  it: number
}

/** 读旁挂元数据（没有 / 坏 JSON / 魔数不符 ⇒ `null`，**永不抛**）。 */
export function taskBundleMeta(course: string): TaskBundleMeta | null {
  let raw: string
  try {
    raw = readFileSync(taskBundleMetaPath(course), 'utf-8')
  } catch {
    return null
  }
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    const j = parsed as Record<string, unknown>
    if (j.magic !== bundleMetaMagic) return null
    return {
      codeSha256: String(j.code_sha256 ?? ''),
      tsCodeSha256: String(j.ts_code_sha256 ?? ''),
      commit: String(j.commit ?? ''),
      it: Number(j.it ?? 0) || 0,
    }
  } catch {
    return null
  }
}

/** 作废任务包的归档目录（**挪走而非删除**：导出失败时它还是唯一能跑的东西）。 */
export function stalePackDir(course: string): string {
  return path.join(tmpCourseDir(course), 'stale-packs')
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
    moveBundleMetaTo(course, `${dest}${bundleMetaSuffix}`)
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
    moveBundleMetaTo(course, taskBundleMetaPath(course), archived)
    return { restored: true, message: `已恢复旧包 ${path.relative(REPO_ROOT, live)}` }
  } catch (e) {
    return {
      restored: false,
      message: `恢复旧包失败（${e instanceof Error ? e.message : String(e)}）——请手动从 ${path.relative(REPO_ROOT, archived)} 取回`,
    }
  }
}

/** 把旁挂元数据挪到 *dest*（best-effort，**永不抛**）。
 *
 *  包被作废/恢复时 sidecar 必须跟着走：留一份孤儿不会立刻出错（判据先看包在不在），
 *  但「包回来了而 sidecar 是更早那份」会把判据指向上一份代码（plan §4.2）。
 */
function moveBundleMetaTo(course: string, dest: string, srcOverride?: string): void {
  const src = srcOverride ? `${srcOverride}${bundleMetaSuffix}` : taskBundleMetaPath(course)
  if (!existsSync(src)) return
  try {
    renameSync(src, dest)
  } catch {
    /* 挪不动只是留个孤儿；判据仍安全（先看包在不在） */
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

/** 段长的**硬上界**（python 侧 `common/manifest.py::RUN_MAX_ITERS_HARD_CAP` 的同名常量）。
 *
 *  跨语言契约：`dashboard/tests/server-api-task-bundle.test.ts` 读 python 源逐字对账
 *  —— 两边漂开就是「预判说能导、真闸拒绝」（或者反过来：预放行一份必然失败的任务）。
 *  它从「拒导闸」变成「段长」（J1）后仍有两处读者：`worker/plan.build_plan`（真闸）
 *  与这里的静态预判（J4）。
 */
export const RUN_MAX_ITERS_HARD_CAP = 500

/** 导出前的门（纯函数；返回非空 = 拒启原因）。
 *
 *  ★ 2026-09-22（课程错误隔离 + 随时导出）：**不再以「训练在跑（run_rl 锁被占）」拒导**——
 *  导出是只读快照（run_rl `--export-bundle` 不取 per-course 锁、不推进账本），
 *  开课后随时可导出/重导（离线整段由此可反复以最新进度为起点出包）。
 *  保留的拒绝理由：**没有起点权重**（包里必须有个起点，python 侧同判据）。
 *
 *  ★ 2026-10-16（J4，plan/offline-open-ended-iters-and-loud-export §3.4）：补一类
 *  **不需要真导出就能判**的静态拒因——「无剩余轮次」与「轮数 > 硬上界」。它们原本只在
 *  python 的启动期/轮内栅里拒（`loop_lifecycle.export_refusal` / `worker/plan.build_plan`），
 *  而那意味着：点下去 → spawn → 烧一次 code-snapshot + 一次 `git push` + 约 150s 的轮内重试，
 *  最后才在日志里报一句「拒发」（现场：y5-hit2 三次尝试两次白烧）。
 *
 *  判据与 python **同一锚**（评审 P1-4 点名的差一）：
 *    · 启动期用 `self._start_it`（= `last_completed + 1`，`loop_lifecycle.py:361-367`）；
 *    · 轮内用 `it - 1`（`loop_export.py:209` 传 `build_plan(it=it-1)`）——两者是**同一个起点**
 *      的两种写法（`it` = `last_completed + 1`）。所以 TS 侧算 `剩余轮数 = iters - lastCompletedIt`，
 *      而不是 `iters - (lastCompletedIt + 1)`（后者会多拒掉恰好剩一轮的课）。
 *
 *  不可知的输入（`declaredIters` 缺省/null）⇒ **不判**：课程文件读不到/没声明就放行，
 *  真闸仍在 python 侧（预判只能多拦，不得多放也不得多拦）。
 */
export interface ExportGuardFacts {
  /** 起点权重在不在（`tmp/<课程>/weights.json`）。 */
  weightsExists: boolean
  /** 课程文件声明的 `iters`（`stack/courses.ts::declaredCourseIters`）；`null`/缺省 = 不可知。 */
  declaredIters?: number | null
  /** 本机已完成的最后一轮（`training_log.jsonl` 的 `iteration.iter` 最大值；0 = 还没有）。 */
  lastCompletedIt?: number
}

export function exportGuardReason(arg: ExportGuardFacts): string | null {
  if (!arg.weightsExists)
    return '没有起点权重（tmp/<课程>/weights.json）——先跑至少一轮，包里得有个起点'
  const iters = arg.declaredIters
  if (iters === null || iters === undefined) return null
  if (iters <= 0) return null // J1：课程无限 ⇒ 开区间段（终点 = 段长），不再是拒因
  const last = Math.max(0, Math.trunc(arg.lastCompletedIt ?? 0))
  const remaining = iters - last
  if (remaining <= 0) {
    return (
      `课程 iters=${iters} ∧ 本机已跑到 it${last}——**没有剩余轮次**，导出的包会是空的` +
      '（云机会把它当活领走、跑 0 轮）。要接着跑请把课程的 iters 调大（新的一段）' +
      '，或改用在线；只想把当前权重交出去，请走「导出权重/产物」'
    )
  }
  if (remaining > RUN_MAX_ITERS_HARD_CAP) {
    return (
      `本段要跑 ${remaining} 轮（课程 iters=${iters} − 已跑 it${last}）> 硬上界 ` +
      `${RUN_MAX_ITERS_HARD_CAP}——拒导（一份手写/损坏的计划不许把云机按一整天）。` +
      `把课程的 iters 降到 ≤${last + RUN_MAX_ITERS_HARD_CAP}（或给节点降段长）再导`
    )
  }
  return null
}

/** 本机已完成的最后一轮（账本 `tmp/<课程>/training_log.jsonl` 的 `iteration.iter` 最大值）。
 *
 *  为什么不用 `iters.ts::readIterMetrics`：那个读面为「各轮指标表」把整行铺开（带缓存），
 *  而这里只需要一个数——导出预判每点一次就跑一次。读不到 / 坏行 ⇒ 0（不判，不抛）。
 */
export function lastCompletedIt(course: string): number {
  let text: string
  try {
    text = readFileSync(path.join(tmpCourseDir(course), 'training_log.jsonl'), 'utf-8')
  } catch {
    return 0
  }
  let best = 0
  for (const line of text.split('\n')) {
    if (!line.trim()) continue
    let ev: unknown
    try {
      ev = JSON.parse(line)
    } catch {
      continue // 半截行（被 kill）：跳过，不毒死判据
    }
    if (!ev || typeof ev !== 'object') continue
    const row = ev as { event?: unknown; iter?: unknown }
    if (row.event !== 'iteration') continue
    const n = Number(row.iter)
    if (Number.isFinite(n) && n > best) best = Math.trunc(n)
  }
  return best
}

/** 导出前的门（IO 壳：看权重在不在 + 课程声明的 iters 与账本已跑到哪）。 */
export function exportGuard(course: string): string | null {
  return exportGuardReason({
    weightsExists: existsSync(path.join(tmpCourseDir(course), 'weights.json')),
    declaredIters: declaredCourseIters(course),
    lastCompletedIt: lastCompletedIt(course),
  })
}

/** 活动权重（`tmp/<课程>/weights.json`）的 mtime（ms）；读不到 → 0。
 *
 *  为什么需要它（2026-10-04）：任务包 = **导出那一刻**的（代码 + 起点权重 + 动量）快照，
 *  而在线训练的权重每轮都在变（`run_rl` 每轮落 `args.out` = 这个文件）。所以「包比活动
 *  权重新」= 包仍代表当前起点（可复用）；包更旧 = **训练已推进**，旧包会把云机拖回旧起点
 *  （离线腿的续跑锚点只认回传/导入的轮次，看不见本机在线轮）——必须重导。
 */
export function weightsMtimeMs(course: string): number {
  try {
    return statSync(path.join(tmpCourseDir(course), 'weights.json')).mtimeMs
  } catch {
    return 0
  }
}

/** `pack_code_zip` 会打进 `code.zip` 的源文件（相对 `nn-training/`）：`.py` + `.jsonc`。 */
const CODE_EXTS = ['.py', '.jsonc'] as const

/** 打包器排除的非点目录（★ 与 `remote/hub_client.py::pack_code_zip` 的 `_exclude_dirs`
 *  同表——`server-api-task-bundle` 套件读 python 源逐名对账，防两边漂开）。 */
export const CODE_EXCLUDE_DIRS = ['tmp', 'weights', '__pycache__', 'tests'] as const

/** 打包器排除的单件（同上对账）。 */
export const CODE_EXCLUDE_FILES = ['rl-config.json'] as const

/** 源码树里**最新**的一份 `code.zip` 源文件（`.py`/`.jsonc`）的 mtime（ms）；无文件 → 0。
 *
 *  为什么需要它（2026-10-04 用户口径）：「开课导出包之后，代码可能被改过、hub/trainer
 *  也可能带着新代码重启过」——包里的 `code.zip` 是**导出那一刻**的代码快照，拿旧包去跑
 *  云端就是跑旧代码。判据用「有没有源文件比包新」而不是比对 commit：`pack_code_zip`
 *  明说「含**未提交修改**」，HEAD 相同不代表字节相同，而 mtime 对改一行也敏感。
 *
 *  扫描范围/排除项与 `pack_code_zip` 同步（见上面两个常量）：排除项故意只做**等价**不做
 *  更严——多扫进来的文件只会造成「假过期 ⇒ 白重导一次」，漏扫才会造成「拿旧代码跑」（真正的
 *  事故），所以宁可放过列表（点目录一律排除：`.venv*` / 各种缓存 / `.git`）。
 */
export function newestCodeMtimeMs(root: string = REPO_ROOT): number {
  const base = path.join(root, 'nn-training')
  let newest = 0
  const walk = (dir: string): void => {
    let entries
    try {
      entries = readdirSync(dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const e of entries) {
      const name = e.name
      if (e.isDirectory()) {
        if (name.startsWith('.') || (CODE_EXCLUDE_DIRS as readonly string[]).includes(name))
          continue
        walk(path.join(dir, name))
        continue
      }
      if (!e.isFile()) continue
      if ((CODE_EXCLUDE_FILES as readonly string[]).includes(name)) continue
      if (!CODE_EXTS.some((x) => name.endsWith(x))) continue
      try {
        const mt = statSync(path.join(dir, name)).mtimeMs
        if (mt > newest) newest = mt
      } catch {
        /* 读不到就跳过（下一轮还有机会；不阻断判定） */
      }
    }
  }
  walk(base)
  return newest
}

/** 导出一次性进程的 argv（纯函数，便于测试）。 */
export function taskBundleArgs(course: string): string[] {
  return [
    'nn-training/trainer/run_rl.py',
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

/** 本课导出是否该被拒（**锁判据**：进程内 busy + 持久状态；不做业务校验）。 */
export function exportBusyReason(course: string): string | null {
  if (busy.has(taskBundleBusyKey(course)))
    return '上一次任务包导出还没跑完（产物 zip 会在过程中重写）——稍等或看日志'
  const st = readTaskBundleStatus(course)
  if (st?.running && !st.expired) {
    const mins = Math.max(1, Math.round(st.elapsedSec / 60))
    return `同课导出还在跑（PID ${st.pid}，已跑 ${mins} 分钟）——等它结束/超时，或先看日志`
  }
  return null
}

/** 导出的命令行身份（kill 前的安全联锁：PID 会被复用，盲杀可能杀掉无关进程）。 */
export function exportPidMatches(pid: number, course: string): boolean {
  const cmd = pidCommandLine(pid)
  if (!cmd) return false
  return (
    cmd.includes('run_rl.py') &&
    cmd.includes('--export-bundle') &&
    cmd.includes(taskBundleFileName(course))
  )
}

/** 看门狗日志：控制台日志 + 导出日志各一行（**不许静默解锁**）。 */
function exportLog(course: string, line: string): void {
  logWarn(`[export] ${course}: ${line}`)
  try {
    appendFileSync(taskBundleLogPath(course), `\n[watchdog] ${line}\n`, 'utf-8')
  } catch {
    /* 日志写不进去不影响收尾 */
  }
}

export interface ExportWatchOptions {
  /** 测试注入（生产 = `EXPORT_TIMEOUT_MS`）。 */
  timeoutMs?: number
  /** 测试注入（生产 = `EXPORT_POLL_MS`）。 */
  pollMs?: number
  /** 测试注入（生产 = `exportPidMatches`）。 */
  matchesPid?: (pid: number, course: string) => boolean
  /** 本次导出的起点（ms）：判断「这个包是不是本次产出的」。 */
  startedAtMs?: number
}

/** 起一次导出（detach；日志 `logs/<课程>/export-bundle.log`）。**不注册组件**——
 *  它是一次性动作，进组件账本会被监督器当成「该重启的组件」。
 *
 *  互斥 = 进程内 `busy`（按课分键）+ **持久状态**（跨控制台重启）。释放由
 *  `watchExportExit` 轮询 pid / 30 分钟上界完成；**不**用 `busySince` 那套 5 分钟 TTL。 */
export function launchTaskBundleExport(course: string): ExportLaunchResult {
  const blocked = exportBusyReason(course)
  if (blocked) return { ok: false, message: blocked }
  const guard = exportGuard(course)
  if (guard) return { ok: false, message: guard }
  // 上一次遗留的 running/stale（控制台重启过 / 外部 kill）：先落一条终态再接管，
  // 否则新导出的启动状态会被旧 running 挡住（launch 时 exportBusyReason 已判过期）。
  const prior = readTaskBundleStatus(course)
  if (prior?.expired || prior?.stale) {
    writeTaskBundleStatus(course, {
      ok: false,
      running: false,
      reason: prior.expired
        ? `上一次导出超时/进程已不在（PID ${prior.pid}），本次接管`
        : '上一次导出状态早于任务包作废，本次接管',
      pid: prior.pid,
    })
  }
  // ★ 2026-09-22：导出 = **重新打一份带当前代码的包** ⇒ 先把旧包作废（掉进 stale-packs）。
  //   放在 exportGuard **之后**：没起点权重这类拒启不该先把旧包挪走（那会平白让
  //   云机取不到包，而导出的前提本来就没满足）。
  const stale = invalidateTaskBundle(course)
  const argv = taskBundleArgs(course)
  try {
    const r = spawnRunPython(argv[0], argv.slice(1), taskBundleLogPath(course))
    busy.add(taskBundleBusyKey(course))
    void watchExportExit(r.pid, course, { startedAtMs: Date.now() })
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

/** 等导出进程退出就解锁（每 2s 看一眼 pid；30 分钟硬上界）。
 *
 *  为什么必须有上界：v1 的上界是 `8*60*30` 次 × 2s = **8 小时**，且 release 不杀进程——
 *  一个卡住的导出会烧盘到天荒地老，8h 后还会与第二个导出并发写同一个 `.tmp`。
 *
 *  为什么 kill 前验身份：PID 会被复用；盲杀可能杀掉无关进程（共享 trainer）。
 *  验不了就不杀（只 release + 日志）。杀用 `killPidTree`（Windows `taskkill /T /F`）。
 *
 *  退出后还有一道兜底：**没产出新包就把旧包恢复**（见 restorePackIfMissing）——作废 + 导出失败
 *  不能变成「这门课没有包」，那是把人锁在云机上干等。 */
export async function watchExportExit(
  pid: number,
  course: string,
  opts: ExportWatchOptions = {},
): Promise<void> {
  const timeoutMs = opts.timeoutMs ?? EXPORT_TIMEOUT_MS
  const pollMs = opts.pollMs ?? EXPORT_POLL_MS
  const matches = opts.matchesPid ?? exportPidMatches
  const startedAt = opts.startedAtMs ?? Date.now()
  while (pidAlive(pid) && Date.now() - startedAt < timeoutMs) await sleep(pollMs)

  const timedOut = pidAlive(pid)
  const mins = Math.max(1, Math.round(timeoutMs / 60000))
  let killed = false
  if (timedOut) {
    if (matches(pid, course)) {
      killed = await killPidTree(pid)
      exportLog(
        course,
        killed
          ? `导出超时（>${mins} 分钟），已终止 PID ${pid}`
          : `导出超时（>${mins} 分钟），未能终止 PID ${pid}`,
      )
    } else {
      exportLog(course, `导出超时（>${mins} 分钟），PID ${pid} 身份无法确认（命令行不匹配）——未杀`)
    }
  }

  const packMs = (() => {
    try {
      return statSync(taskBundlePath(course)).mtimeMs
    } catch {
      return 0
    }
  })()
  const produced = packMs > 0 && packMs >= startedAt
  const st = readTaskBundleStatus(course)
  if (produced) {
    // 包已经产出（原子 replace）⇒ 看门狗即使终止了进程，也不能把成功改写成失败。
    if (st?.running) writeTaskBundleStatus(course, { ok: true, running: false, reason: '', pid })
  } else if (st?.running || timedOut) {
    writeTaskBundleStatus(course, {
      ok: false,
      running: false,
      reason: timedOut
        ? `导出超时（>${mins} 分钟）${killed ? '，已终止' : '，未终止'} PID ${pid}`
        : `导出进程已退出但没有落状态（PID ${pid}；被 kill / 崩溃？）`,
      pid,
    })
  }

  release(taskBundleBusyKey(course))
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
