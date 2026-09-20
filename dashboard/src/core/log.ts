/** log.ts — 控制台 + 文件双写日志（dashboard/src/** 共享）。
 *
 *  两个入口，分工不同：
 *    * `initLog(tag)`（`launch/cli.ts`）——一次性 python 启动，每次一个新文件。
 *    * `initConsoleLog()`（`server/server.ts`）——**长驻控制台**：稳定文件名，
 *      每次启动追加一行会话头。组件级决策（启/停/重启/判死）必须能从文件里翻出来
 *      ——2026-09-20 事故：hub 被人工停掉后盘上只有一行"停服"的空白（日志断在半分钟前），
 *      从证据里**分不出**「人工停的」与「自己死的」，只能去问人。
 */

import { appendFileSync, mkdirSync, renameSync, statSync } from 'fs'
import path from 'path'
import { START_LOG_DIR, consoleLogPath } from './paths'

/** 会话日志轮转阈值（字节）：超过即在**启动时**改名 `console.log.1`（只留一代）。
 *  决策行低频，但长驻进程的日志不能无界（与快照保留策略同精神：有界是纪律）。 */
export const CONSOLE_LOG_ROTATE_BYTES = 8 * 1024 * 1024

export const RED = '\x1b[0;31m'
export const GREEN = '\x1b[0;32m'
export const CYAN = '\x1b[0;36m'
export const GRAY = '\x1b[0;90m'
export const YELLOW = '\x1b[0;33m'
export const NC = '\x1b[0m'

let logFile = ''

/** 打开本次运行的日志文件（每次 start.ts 运行一个，按课程/模式命名）。 */
export function initLog(tag: string): void {
  mkdirSync(START_LOG_DIR, { recursive: true })
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  logFile = path.join(START_LOG_DIR, `${tag || 'start'}-${ts}.log`)
  appendFileSync(logFile, `=== training-start ${new Date().toISOString()} ===\n`, 'utf-8')
}

/** 本次运行日志文件路径（未初始化时为空串）。 */
export function currentLogFile(): string {
  return logFile
}

/** 打开**控制台会话**日志（稳定文件名；见模块头）。返回路径供调用方提示操作员。
 *
 *  轮转只在启动时做（运行中改文件名 = 正在追加的 fd 指向旧文件，徒增混乱）；
 *  容量/轮转可注入：测试用极小阈值验行为。任何 IO 失败都不抛——**日志写不进绝不能
 *  搞停控制台**（组件决策的代价不该由操作员承担）。
 */
export function initConsoleLog(
  file: string = consoleLogPath(),
  opts: { rotateBytes?: number } = {},
): string {
  const cap = opts.rotateBytes ?? CONSOLE_LOG_ROTATE_BYTES
  try {
    mkdirSync(path.dirname(file), { recursive: true })
    // 超过阈值才轮转；`console.log.1` 只留一代（旧的直接覆盖）。
    const size = statSync(file, { throwIfNoEntry: false })?.size ?? 0
    if (cap > 0 && size > cap) renameSync(file, `${file}.1`)
  } catch {
    /* 轮转失败不阻断（最多继续追加到同一份文件） */
  }
  logFile = file
  append(`=== console session ${new Date().toISOString()} pid=${process.pid} ===`)
  return file
}

/** 追加一行到会话日志（**best-effort**：失败静默——日志不是关键路径）。 */
function append(line: string): void {
  if (!logFile) return
  try {
    appendFileSync(logFile, line + '\n', 'utf-8')
  } catch {
    /* 盘满/权限：控制台照常跑 */
  }
}

function write(prefix: string, msg: string, toErr = false): void {
  const ts = new Date().toLocaleTimeString('sv-SE')
  const line = `[${ts}] ${prefix}${msg}`
  if (toErr) console.error(line)
  else console.log(line)
  append(line)
}

export function log(msg: string): void {
  write('', msg)
}
export function error(msg: string): void {
  write('', msg, true)
}
export function ok(msg: string): void {
  write('  ✅ ', msg)
}
export function warn(msg: string): void {
  write('  ⚠️  ', msg)
}
export function fail(msg: string): void {
  write('  ❌ ', msg)
}
export function info(msg: string): void {
  write('  ℹ️  ', msg)
}
