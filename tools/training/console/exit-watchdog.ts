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
import { loadRegistry, saveComponent } from '../registry'
import type { Component, Registry, RegistryEntry } from '../types'
import { readLogTail, resolveComponentLog, discoverCourses, effectiveCourse } from './api'
import { COMPONENT_LABELS, loadConsoleState } from './actions'

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

/** 周期入口：读盘 → 扫描 → 落失败记录。返回本次记录数（-1 = 配置/状态读取失败）。 */
export function runExitCheck(): number {
  try {
    const cfg = loadConfig()
    const course = effectiveCourse(loadConsoleState(), discoverCourses())
    const hits = nextExitFailures(loadRegistry(), _deadSeen)
    for (const { key, entry } of hits) {
      const logRel = resolveComponentLog(key, cfg, course) ?? entry.log ?? null
      const tail = logRel ? readLogTail(logRel, 12).lines : []
      recordExitFailure(key, entry, logRel, tail)
    }
    return hits.length
  } catch {
    return -1
  }
}

/** 两帧锁状态（模块级；单 console 进程生命周期内有效）。 */
const _deadSeen = new Set<Component>()
