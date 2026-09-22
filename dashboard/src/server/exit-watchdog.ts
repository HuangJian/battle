/** exit-watchdog.ts — 受管进程非正常退出的显式日志（§380：TrainingLoop 不许静默失败）。
 *
 * 问题：进程自行退出/被外部杀死时，console 只把状态标成 `exited`，不记录原因——
 * 监督器对死进程是「跳过」（reload.ts），日志文件里只有 spawn 捕获的裸 traceback，
 * UI 只显示空洞的「已退出」。2026-09-08 vk1 事故：TrainingLoop 因缺 BC 参考 boot
 * 崩溃，只有翻日志才看到 FileNotFoundError（用户：不要静默失败，要写 log）。
 *
 * 本模块由 console 独立 4s 周期驱动（server.ts）：
 *   - `nextExitFailures`：扫描登记条目，**连续两帧**观测到同一死 pid 才判定为退出
 *     （排除监督器 kill→换 pid 的瞬时窗口）；pid 复活则清零帧计数。
 *   - `recordExitFailure`：往组件日志文件追加「[console] … 意外退出」标记（含日志尾），
 *     并把原因写入 registry 条目的 `error`/`exitAt`（/api/state 可查、UI 可显）。
 *   - 幂等：条目已带 error 不再重复写；正常 stop 会 clearComponent（条目消失）→ 不触发。
 */

import { appendFileSync, readFileSync } from 'fs'
import { dirname, join } from 'node:path'
import { loadConfig } from '../core/config'
import { warn } from '../core/log'
import { pidAlive } from '../core/net'
import { portOwnerPids } from '../core/proc'
import {
  clearAnyComponent,
  loadRegistry,
  registryTriples,
  saveAnyComponent,
  type WatchedEntry,
} from '../core/registry'
import type { Component, ProcSpec, Registry, RegistryEntry } from '../core/types'
import { readLogTail, resolveComponentLog, discoverCourses, effectiveCourse } from './api'
import { COMPONENT_LABELS, loadConsoleState, restartSpecFor, triggerCloudHalt } from './actions'

/** 监督/看护的归属键：`key|course`（多课下同一组件有多份进程）。 */
export function watchIdOf(item: { key: Component; course: string }): string {
  return `${item.key}|${item.course}`
}

/** 扫描登记条目，返回「确证退出」的条目（两帧锁）。isAlive 可注入（测试用）。
 *  枚举走 registryTriples（三元组，含 course）——不直接枚举账本对象。 */
export function nextExitFailures(
  reg: Registry,
  deadSeen: Set<string>,
  isAlive: (pid: number) => boolean = pidAlive,
): WatchedEntry[] {
  const out: WatchedEntry[] = []
  for (const item of registryTriples(reg)) {
    const { entry } = item
    const id = watchIdOf(item)
    if (typeof entry.pid !== 'number' || entry.error) continue
    if (isAlive(entry.pid)) {
      deadSeen.delete(id) // 复活/换 pid：帧计数清零
      continue
    }
    if (!deadSeen.has(id)) {
      deadSeen.add(id) // 第一帧：仅记
      continue
    }
    deadSeen.delete(id) // 第二帧：确证
    out.push(item)
  }
  return out
}

export interface FailureLogIO {
  append?: (abs: string, text: string) => void
  warnFn?: (text: string) => void
  /** 回写条目（多课：同一条目回原课程槽位；默认 saveAnyComponent）。 */
  save?: (key: Component, course: string, entry: RegistryEntry) => void
}

/** 组件展示名 + 归属课程后缀（多课日志/横幅里必须能看出是哪门课）。 */
export function labelOf(key: Component, course = ''): string {
  return `${COMPONENT_LABELS[key]}${course ? `[${course}]` : ''}`
}

/** 构造「意外退出/已停车」标记文本（含日志尾），供落盘与测试断言。 */
export function buildExitMarker(
  key: Component,
  course: string,
  entry: RegistryEntry,
  tail: string[],
  now: string,
  planned?: string | null,
  failureReason?: string | null,
): string {
  const label = labelOf(key, course)
  // `failureReason` = 意外退出的**具体原因**（非设计内停车）——与 `planned`（已停车）两条
  // 通道互斥：planned 优先；都缺 = 泛化「意外退出」。保留「意外退出」字样（横幅/账本判别面）。
  const head = planned
    ? `[console] ${now} ${label} 已停车（PID ${entry.pid}）：${planned}`
    : failureReason
      ? `[console] ${now} ${label} 意外退出 (PID ${entry.pid})——原因：${failureReason}；` +
        '请检查下列日志；用控制台「启动」恢复：'
      : `[console] ${now} ${label} 意外退出 (PID ${entry.pid})——非正常退出，` +
        '请检查下列日志；用控制台「启动」恢复（原因尾段见上）：'
  const lines = [head, ...(tail.length ? tail.map((l) => `  | ${l}`) : ['  | (日志缺失或为空)'])]
  return lines.join('\n')
}

/** 记录一次确证退出：追加标记到组件日志 + console warn + 写 registry.error（幂等由调用方保证）。 */
export function recordExitFailure(
  key: Component,
  course: string,
  entry: RegistryEntry,
  logAbs: string | null,
  tail: string[],
  io: FailureLogIO = {},
  now: string = new Date().toISOString(),
  planned?: string | null,
  failureReason?: string | null,
): string {
  const marker = buildExitMarker(key, course, entry, tail, now, planned, failureReason)
  if (logAbs) {
    try {
      ;(io.append ?? ((p, t) => appendFileSync(p, t, 'utf-8')))(logAbs, `\n${marker}\n`)
    } catch {
      /* best-effort：日志写不进不阻断流程 */
    }
  }
  ;(io.warnFn ?? warn)(marker.replace(/\n/g, ' '))
  const error = planned
    ? `已停车：${planned}`
    : failureReason
      ? `意外退出 (PID ${entry.pid})——原因：${failureReason}`
      : `意外退出 (PID ${entry.pid})`
  ;(io.save ?? saveAnyComponent)(key, course, {
    ...entry,
    course: entry.course ?? course,
    error,
    exitAt: now,
  })
  return marker
}

/** 组件账本（training_log.jsonl）最近的「设计内停车」事件 → 停车原因；无 → null。
 *
 * §385：gate_verdict / circuit_break 事件意味着进程是**按判决停车**（G1/G5/G7/G13、
 * F4 熔断…），不是意外崩溃——exit-watchdog 不该标「意外退出」误导操作员翻崩溃日志。
 * 只在 withinMs 内的事件算数（门判决到进程退出是秒级，300s 余量足够；更早的旧判决
 * 与本次退出无关）。JSON 损坏行跳过；文件缺失/不可读 → null（维持原意外路径）。 */
export function recentPlannedStop(
  jsonlAbs: string | null,
  withinMs = 300_000,
  now = Date.now(),
): string | null {
  if (!jsonlAbs) return null
  let text = ''
  try {
    text = readFileSync(jsonlAbs, 'utf-8')
  } catch {
    return null
  }
  const lines = text.split('\n')
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]!.trim()
    if (!line) continue
    let r: { event?: unknown; time?: unknown; verdict?: unknown; reason?: unknown }
    try {
      r = JSON.parse(line) as typeof r
    } catch {
      continue
    }
    if (!r || typeof r !== 'object') continue
    if (r.event !== 'gate_verdict' && r.event !== 'circuit_break') continue
    const t = r.time
    if (typeof t !== 'string') continue
    const ts = Date.parse(t.replace(' ', 'T')) // "YYYY-MM-DD HH:mm:ss" 按本地时间解析
    if (Number.isNaN(ts)) continue
    if (now - ts > withinMs) return null // 最近的相关事件太旧 → 本次退出与门无关
    const verdict =
      r.verdict === undefined ? (r.event === 'circuit_break' ? 'ABORT' : '') : String(r.verdict)
    const reason = typeof r.reason === 'string' ? r.reason : ''
    return reason ? `${verdict}: ${reason}`.slice(0, 220) : verdict || '停车判决'
  }
  return null
}

/** 组件日志尾行是 ALL DONE → 正常完成（iters 跑满），返回停车原因；否则 null。
 *
 * 2026-09-12 c5-ent it80 事故：loop 跑满预设 iters 后打印 ALL DONE 正常退出
 *（exit 0），但 recentPlannedStop 只认账本 gate_verdict/circuit_break，ALL DONE
 * 不在识别范围 → 被误标「意外退出——非正常退出」，还连带触发了一次原因错误的
 * 云停机。hub.ts 早已用 tailSince().includes('ALL DONE') 判完成，此处同口径。
 *
 * 严格只认**尾行**：同一日志文件可能含上一轮的 ALL DONE（重启 append），旧完成
 * 行之后还有输出 = 新一轮又崩了，必须仍走意外路径，不得洗成完成。
 * 调用方约定：账本 recentPlannedStop 优先（门判决的原因更具体），本函数兜底。 */
export function tailNormalCompletion(tail: string[]): string | null {
  for (let i = tail.length - 1; i >= 0; i--) {
    const line = tail[i]!.trim()
    if (!line) continue
    return line.includes('ALL DONE') ? '正常完成（ALL DONE）' : null
  }
  return null
}

/** 从日志尾提取「意外退出的**具体原因**」（区别于设计内停车场；无 → null 维持泛化文案）。
 *
 *  2026-09-22 事故：run_rl 的 SystemExit abort 消息（如
 *  `[run_rl] --run-iters<0（跑到课程末尾）需要课程声明 iters——没有终点就不叫整段…`）
 *  是**裸一行、无时间戳前缀**，而它的正常操作行都带 `[HH:MM:SS] ` 前缀——这就是判别面。
 *
 *  识别两类形状（都纯文本、可注入测试）：
 *   1. python traceback 打到尾：末行是异常消息行，其**上一行**是
 *      `Traceback (most recent call last):` 头或 `  File "…"` 框架行 → 返回末行
 *      （如 `SystemExit: bc 缺`；只认这个邻接关系，旧 traceback 残留不误中）；
 *   2. 自尾向前找第一条**裸 `[run_rl]` / `[serve]` / `[loop]` 行**（无 `[HH:MM:SS]` 前缀）
 *      → SystemExit abort 消息 → 返回它。
 *  返回统一截断 140 字符（进横幅 title，保持可扫读）。 */
export function tailFailureReason(tail: string[]): string | null {
  let last = -1
  for (let i = tail.length - 1; i >= 0; i--) {
    if (tail[i]!.trim()) {
      last = i
      break
    }
  }
  if (last < 0) return null
  const lastLine = tail[last]!.trim()
  // traceback：末行就是异常消息行（如 `SystemExit: …`），其**上一行**是
  // `Traceback (most recent call last):` 头或 `  File "…"` 框架行——只认这个邻接关系，
  // 旧 traceback 残留（上邻不是帧/头）不会误中。
  const above = last > 0 ? tail[last - 1]!.trim() : ''
  // above 已 trim 过前导空格：`  File "…"` 去掉缩进后是 `File "…"`。
  if (/^(?:File "|Traceback \(most recent call last\):)/.test(above)) {
    return lastLine.slice(0, 140)
  }
  // SystemExit abort 消息：裸 `[run_rl]`/`[serve]`/`[loop]` 一行、无 `[HH:MM:SS]` 时间戳前缀。
  for (let i = last; i >= 0; i--) {
    const line = tail[i]!.trim()
    if (!line) continue
    if (/^\[(?:run_rl|serve|loop)\] /.test(line)) return line.slice(0, 140)
  }
  return null
}

/** 从 spec.cmd 取监听端口（--port N / --port=N）；无 → null。 */
/** 从 `host:port` / `:port` / `port` 里取端口号（`--metrics` 的取值形态）。 */
function listenPortOf(value: string): number | null {
  const tail = value.includes(':') ? value.slice(value.lastIndexOf(':') + 1) : value
  const n = Number(tail)
  return Number.isInteger(n) && n > 0 ? n : null
}

export function specPort(spec: ProcSpec | null): number | null {
  if (!spec) return null
  for (let i = 0; i < spec.cmd.length; i++) {
    const a = spec.cmd[i]!
    if (a === '--port' && i + 1 < spec.cmd.length) {
      const n = Number(spec.cmd[i + 1])
      return Number.isInteger(n) && n > 0 ? n : null
    }
    if (a.startsWith('--port=')) {
      const n = Number(a.slice(7))
      return Number.isInteger(n) && n > 0 ? n : null
    }
    // cloudflared 没有 `--port`，它自己的监听端口写在 `--metrics 127.0.0.1:<p>`
    // （specs.ts cloudflaredSpec / hub.ts stepCloudflared）。不认它 ⇒ specPort 恒 null
    // ⇒ ownerPidOf 恒 null ⇒ classifyExit 永远只能打「跳过意外退出标记」而不修账本
    // ⇒ 陈旧条目永不收敛（2026-09-17 事故：09-14 的幽灵条目每 8s 刷屏，且真占住 slot 0）。
    if (a === '--metrics' && i + 1 < spec.cmd.length) {
      const n = listenPortOf(spec.cmd[i + 1]!)
      if (n !== null) return n
    }
    if (a.startsWith('--metrics=')) {
      const n = listenPortOf(a.slice('--metrics='.length))
      if (n !== null) return n
    }
  }
  return null
}

/** 端口当前占用者 pid（排除账本里那个已消失的旧 pid）。 */
export function ownerPidOf(key: Component, course: string, stalePid: number): number | null {
  const port = specPort(restartSpecFor(key, course))
  const owners = port ? portOwnerPids(port) : []
  return owners.find((p) => p !== stalePid) ?? owners[0] ?? null
}

/** 该 pid 是否被账本里**另一个条目**占用。
 *
 *  这是「端口被谁答」之外的第二问：同一个端口可能被**别的课程/组件**的进程占着
 *  （槽位撞车 / 同槽接管后旧条目没清）。此时把占用者的 pid 写进本条 = 跨课错配
 *  （比不修更糟）。返回占用方的展示名，null = 没人认领（= 大概率是本条自己的下一代）。
 */
export function pidClaimedElsewhere(reg: Registry, self: WatchedEntry, pid: number): string | null {
  for (const item of registryTriples(reg)) {
    if (item.key === self.key && item.course === self.course) continue
    if (item.entry.pid === pid) return labelOf(item.key, item.course)
  }
  return null
}

export interface ClassifyIO {
  /** 健康复核（注入用于测试）；默认用 restartSpecFor(key, course).healthy()。 */
  healthyOf?: (item: WatchedEntry) => Promise<boolean | null>
  /** 账本 pid 修正（注入用于测试）；默认 saveAnyComponent。 */
  repair?: (item: WatchedEntry, newPid: number) => void
  /** 端口占用者（注入用于测试）；默认 specPort + portOwnerPids。 */
  ownerPidOf?: (item: WatchedEntry, stalePid: number) => number | null
  /** 「该 pid 被别的条目认领了吗」（注入用于测试）；默认 pidClaimedElsewhere。 */
  claimedBy?: (item: WatchedEntry, pid: number) => string | null
  /** 陈旧条目清除（注入用于测试）；默认 clearAnyComponent。 */
  discard?: (item: WatchedEntry) => void
  warnFn?: (text: string) => void
}

/** 「端口被他人接管、本条已是幽灵」的告警去重集（键 = watchId#stalePid）。
 *
 *  没有它的话，认不出占用者的条目会**每个周期重复打同一行**（2026-09-17 实测：每 8s
 *  一条，把日志刷满）。已报告过的组合只报一次，避免把真信号淹掉。 */
const warnedGhost = new Set<string>()

/** 判定一条「确证死 pid」是否只是**进程换代**（2026-09-09 selfNode 误报事故）。
 *
 * 成因：组件自我重启 / 热重载 / 被监督器换掉后，账本记的仍是**上一代** pid；旧 pid
 * 消失会被两帧锁判成「意外退出」，而服务其实一直在应答（实测：agent 09:37:58 起来，
 * 09:38:03 就给上一代 PID 8100 打标记）。
 *
 * 判死前先用组件自己的健康检查复核：仍在应答 ⇒ 'alive'——**不写失败标记**，并把账本
 * pid 修正为端口当前占用者；检查不可用或确认不通 ⇒ 'exited'（走原失败记录路径）。
 */
export async function classifyExit(
  item: WatchedEntry,
  io: ClassifyIO = {},
): Promise<'alive' | 'exited'> {
  const { key, course, entry } = item
  const spec = restartSpecFor(key, course)
  const healthy = io.healthyOf ?? (async () => (spec ? await spec.healthy() : null))
  let alive = false
  try {
    alive = (await healthy(item)) === true
  } catch {
    alive = false
  }
  if (!alive) return 'exited'

  const newPid = (io.ownerPidOf ?? ((it, stale) => ownerPidOf(it.key, it.course, stale)))(
    item,
    entry.pid,
  )
  const say = io.warnFn ?? warn
  const onceKey = `${watchIdOf(item)}#${entry.pid}`
  if (newPid && newPid !== entry.pid) {
    const claimed = (io.claimedBy ?? ((it, p) => pidClaimedElsewhere(loadRegistry(), it, p)))(
      item,
      newPid,
    )
    if (claimed) {
      // 端口被**别人的**进程占着 ⇒ 本条是同槽接管后遗留的陈旧幽灵：清账，**不修 pid**
      // （修了就是把别人的进程认成自己的 —— 跨课错配，比不修更糟）。
      // 2026-09-17：09-14 的 cloudflared[x2-acbc] 条目正是靠这条被自动清掉的。
      ;(io.discard ?? ((it) => clearAnyComponent(it.key, it.course)))(item)
      if (!warnedGhost.has(onceKey)) {
        warnedGhost.add(onceKey)
        say(
          `[console] ${labelOf(key, course)} 账本 PID ${entry.pid} 已消失，端口现由 ${claimed} 持有` +
            ' → 判定为陈旧条目并清除（同槽位接管残留；改槽位请走 rl-config courses.<课>.slot）',
        )
      }
    } else {
      ;(io.repair ?? ((it, p) => saveAnyComponent(it.key, it.course, { ...it.entry, pid: p })))(
        item,
        newPid,
      )
      say(
        `[console] ${labelOf(key, course)} 进程换代：账本 PID ${entry.pid} 已消失，服务仍在应答` +
          ` → 账本 pid 修正为 ${newPid}（不计意外退出）`,
      )
    }
  } else if (!warnedGhost.has(onceKey)) {
    // 认不出占用者（specPort 也拿不到端口）：只报一次，不每周期刷屏。
    warnedGhost.add(onceKey)
    say(
      `[console] ${labelOf(key, course)} 账本 PID ${entry.pid} 已消失，但服务仍在应答 → ` +
        '视为进程换代，跳过意外退出标记（同一 pid 只报一次）',
    )
  }
  return 'alive'
}

/** 自愈：已标「意外退出」但服务已恢复应答的条目 → 清标记 + 修 pid（2026-09-09）。
 *
 * 误报一旦写进账本，nextExitFailures 会因「已有 error」永久跳过它，UI 就一直挂着
 * 「意外退出」——即使组件一直在正常服务。这里对仍健康的条目清除 error/exitAt，
 * 让状态自恢复（真退出的组件 healthy=false，不受影响）。 */
export async function healRecoveredErrors(
  reg: Registry,
  io: {
    healthyOf?: (item: WatchedEntry) => Promise<boolean | null>
    ownerPidOf?: (item: WatchedEntry, stalePid: number) => number | null
    save?: (key: Component, course: string, entry: RegistryEntry) => void
    warnFn?: (t: string) => void
  } = {},
): Promise<string[]> {
  const healed: string[] = []
  for (const item of registryTriples(reg)) {
    const { key, course, entry: e } = item
    if (!e.error) continue
    const healthy =
      io.healthyOf ??
      (async () => {
        const spec = restartSpecFor(key, course)
        return spec ? await spec.healthy() : null
      })
    let alive = false
    try {
      alive = (await healthy(item)) === true
    } catch {
      alive = false
    }
    if (!alive) continue
    const pid =
      (io.ownerPidOf ?? ((it, stale) => ownerPidOf(it.key, it.course, stale)))(item, e.pid) ?? e.pid
    const { error: _err, exitAt: _at, ...rest } = e
    ;(io.save ?? saveAnyComponent)(key, course, { ...rest, pid })
    ;(io.warnFn ?? warn)(
      `[console] ${labelOf(key, course)} 服务已恢复应答 → 清除「${e.error}」标记`,
    )
    healed.push(watchIdOf(item))
  }
  return healed
}

/** 周期入口：读盘 → 自愈 → 扫描 → 复核 → 落失败记录。返回本次记录数（-1 = 配置/状态读取失败）。 */
export async function runExitCheck(): Promise<number> {
  try {
    const cfg = loadConfig()
    const course = effectiveCourse(loadConsoleState(), discoverCourses())
    const reg = loadRegistry()
    await healRecoveredErrors(reg)
    const hits = nextExitFailures(loadRegistry(), _deadSeen)
    let recorded = 0
    for (const hit of hits) {
      const { key, entry } = hit
      if ((await classifyExit(hit)) === 'alive') continue // 仅换代，非退出
      // 日志按**条目自身的课程**取（operator 课程 ≠ 该条目课程时不能串）。
      // 共享 trainer（2026-09-19 / R3-5）**没有课程**：它的日志是进程 stdout
      // （`trainer-cluster.log`，进程级一份），按课推路径会指向 `nocourse/`——那里没东西。
      const logRel =
        key === 'trainingLoop' && !hit.course
          ? (entry.log ?? null)
          : (resolveComponentLog(key, cfg, hit.course || course) ?? entry.log ?? null)
      const tail = logRel ? readLogTail(logRel, 12).lines : []
      // §385：trainingLoop 账本有最近 gate_verdict/circuit_break → 设计内停车，
      // 标「已停车(原因)」而非「意外退出」；其余组件/无事件走原意外路径。
      // 2026-09-12：账本无判决但日志尾行 ALL DONE（iters 跑满正常完成）→ 同样
      // 是设计内停车（账本原因优先，tail 兜底）。
      //
      // 共享 trainer 的账本判据**不适用**：它的一个进程跑 N 门课，任一门课的
      // gate_verdict 都不是「这个进程该退」的理由（它的停车是**按课**的，进程照跑）。
      // 故只看 tail（日志尾），旧形状的每课条目才查账本。
      const planned =
        key === 'trainingLoop' && logRel
          ? ((hit.course ? recentPlannedStop(join(dirname(logRel), 'training_log.jsonl')) : null) ??
            tailNormalCompletion(tail))
          : null
      // §2026-09-22：设计内停车之外的**意外**退出，若日志尾能判出具体原因（SystemExit abort
      // 消息 / traceback 末行）就把原因带进 marker/registry.error/停机 reason——横幅不再是
      // 空洞的「意外退出 (PID …)」。
      const failureReason = planned ? null : tailFailureReason(tail)
      recordExitFailure(key, hit.course, entry, logRel, tail, {}, undefined, planned, failureReason)
      // §385 复审：TrainingLoop 一死（设计内停车或崩溃）→ 云端停机省 GPU 配额；
      // 本地 hubServer/console 一律不动。幂等由 triggerCloudHalt 守卫；
      // 停机命令按**该条目的课程**下发（多课下不得误停别课 hub）。
      if (key === 'trainingLoop') {
        const reason = planned
          ? `TrainingLoop 设计内停车：${planned}`
          : failureReason
            ? `TrainingLoop 意外退出 (PID ${entry.pid})——原因：${failureReason}`
            : `TrainingLoop 意外退出 (PID ${entry.pid})`
        try {
          await triggerCloudHalt(cfg, reason, hit.course)
        } catch {
          /* watchdog 永不被停机链路拖垮 */
        }
      }
      recorded++
    }
    return recorded
  } catch {
    return -1
  }
}

/** 两帧锁状态（模块级；单 console 进程生命周期内有效）。键 = `key|course`。 */
const _deadSeen = new Set<string>()
