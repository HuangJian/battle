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

import { appendFileSync } from 'fs'
import { loadConfig } from '../config'
import { warn } from '../log'
import { pidAlive } from '../net'
import { portOwnerPids } from '../proc'
import { loadRegistry, saveComponent } from '../registry'
import type { Component, ProcSpec, Registry, RegistryEntry } from '../types'
import { readLogTail, resolveComponentLog, discoverCourses, effectiveCourse } from './api'
import { COMPONENT_LABELS, loadConsoleState, restartSpecFor } from './actions'

/** 扫描登记条目，返回「确证退出」的组件（两帧锁）。isAlive 可注入（测试用）。 */
export function nextExitFailures(
  reg: Registry,
  deadSeen: Set<Component>,
  isAlive: (pid: number) => boolean = pidAlive,
): Array<{ key: Component; entry: RegistryEntry }> {
  const out: Array<{ key: Component; entry: RegistryEntry }> = []
  for (const key of Object.keys(reg) as Component[]) {
    const e = reg[key]
    if (!e || typeof e.pid !== 'number' || e.error) continue
    if (isAlive(e.pid)) {
      deadSeen.delete(key) // 复活/换 pid：帧计数清零
      continue
    }
    if (!deadSeen.has(key)) {
      deadSeen.add(key) // 第一帧：仅记
      continue
    }
    deadSeen.delete(key) // 第二帧：确证
    out.push({ key, entry: e })
  }
  return out
}

export interface FailureLogIO {
  append?: (abs: string, text: string) => void
  warnFn?: (text: string) => void
  save?: (key: Component, entry: RegistryEntry) => void
}

/** 构造「意外退出」标记文本（含日志尾），供落盘与测试断言。 */
export function buildExitMarker(
  key: Component,
  entry: RegistryEntry,
  tail: string[],
  now: string,
): string {
  const lines = [
    `[console] ${now} ${COMPONENT_LABELS[key]} 意外退出 (PID ${entry.pid})——非正常退出，` +
      '请检查下列日志；用控制台「启动」恢复（原因尾段见上）：',
    ...(tail.length ? tail.map((l) => `  | ${l}`) : ['  | (日志缺失或为空)']),
  ]
  return lines.join('\n')
}

/** 记录一次确证退出：追加标记到组件日志 + console warn + 写 registry.error（幂等由调用方保证）。 */
export function recordExitFailure(
  key: Component,
  entry: RegistryEntry,
  logAbs: string | null,
  tail: string[],
  io: FailureLogIO = {},
  now: string = new Date().toISOString(),
): string {
  const marker = buildExitMarker(key, entry, tail, now)
  if (logAbs) {
    try {
      ;(io.append ?? ((p, t) => appendFileSync(p, t, 'utf-8')))(logAbs, `\n${marker}\n`)
    } catch {
      /* best-effort：日志写不进不阻断流程 */
    }
  }
  ;(io.warnFn ?? warn)(marker.replace(/\n/g, ' '))
  ;(io.save ?? saveComponent)(key, { ...entry, error: `意外退出 (PID ${entry.pid})`, exitAt: now })
  return marker
}

/** 从 spec.cmd 取监听端口（--port N / --port=N）；无 → null。 */
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
  }
  return null
}

/** 端口当前占用者 pid（排除账本里那个已消失的旧 pid）。 */
export function ownerPidOf(key: Component, stalePid: number): number | null {
  const port = specPort(restartSpecFor(key))
  const owners = port ? portOwnerPids(port) : []
  return owners.find((p) => p !== stalePid) ?? owners[0] ?? null
}

export interface ClassifyIO {
  /** 健康复核（注入用于测试）；默认用 restartSpecFor(key).healthy()。 */
  healthyOf?: (key: Component) => Promise<boolean | null>
  /** 账本 pid 修正（注入用于测试）；默认 saveComponent。 */
  repair?: (key: Component, entry: RegistryEntry, newPid: number) => void
  /** 端口占用者（注入用于测试）；默认 specPort + portOwnerPids。 */
  ownerPidOf?: (key: Component, stalePid: number) => number | null
  warnFn?: (text: string) => void
}

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
  key: Component,
  entry: RegistryEntry,
  io: ClassifyIO = {},
): Promise<'alive' | 'exited'> {
  const spec = restartSpecFor(key)
  const healthy = io.healthyOf ?? (async () => (spec ? await spec.healthy() : null))
  let alive = false
  try {
    alive = (await healthy(key)) === true
  } catch {
    alive = false
  }
  if (!alive) return 'exited'

  const newPid = (io.ownerPidOf ?? ((k, stale) => ownerPidOf(k, stale)))(key, entry.pid)
  const say = io.warnFn ?? warn
  if (newPid && newPid !== entry.pid) {
    ;(io.repair ?? ((k, e, p) => saveComponent(k, { ...e, pid: p })))(key, entry, newPid)
    say(
      `[console] ${COMPONENT_LABELS[key]} 进程换代：账本 PID ${entry.pid} 已消失，服务仍在应答` +
        ` → 账本 pid 修正为 ${newPid}（不计意外退出）`,
    )
  } else {
    say(
      `[console] ${COMPONENT_LABELS[key]} 账本 PID ${entry.pid} 已消失，但服务仍在应答 → ` +
        '视为进程换代，跳过意外退出标记',
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
    healthyOf?: (key: Component) => Promise<boolean | null>
    ownerPidOf?: (key: Component, stalePid: number) => number | null
    save?: (key: Component, entry: RegistryEntry) => void
    warnFn?: (t: string) => void
  } = {},
): Promise<Component[]> {
  const healed: Component[] = []
  for (const key of Object.keys(reg) as Component[]) {
    const e = reg[key]
    if (!e?.error) continue
    const healthy =
      io.healthyOf ??
      (async () => {
        const spec = restartSpecFor(key)
        return spec ? await spec.healthy() : null
      })
    let alive = false
    try {
      alive = (await healthy(key)) === true
    } catch {
      alive = false
    }
    if (!alive) continue
    const pid = (io.ownerPidOf ?? ((k, stale) => ownerPidOf(k, stale)))(key, e.pid) ?? e.pid
    const { error: _err, exitAt: _at, ...rest } = e
    ;(io.save ?? saveComponent)(key, { ...rest, pid })
    ;(io.warnFn ?? warn)(
      `[console] ${COMPONENT_LABELS[key]} 服务已恢复应答 → 清除「${e.error}」标记`,
    )
    healed.push(key)
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
    for (const { key, entry } of hits) {
      if ((await classifyExit(key, entry)) === 'alive') continue // 仅换代，非退出
      const logRel = resolveComponentLog(key, cfg, course) ?? entry.log ?? null
      const tail = logRel ? readLogTail(logRel, 12).lines : []
      recordExitFailure(key, entry, logRel, tail)
      recorded++
    }
    return recorded
  } catch {
    return -1
  }
}

/** 两帧锁状态（模块级；单 console 进程生命周期内有效）。 */
const _deadSeen = new Set<Component>()
