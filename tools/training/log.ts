/** log.ts — 控制台 + 文件双写日志（tools/training/** 共享）。 */

import { appendFileSync, mkdirSync } from 'fs'
import path from 'path'
import { START_LOG_DIR } from './paths'

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

function write(prefix: string, msg: string): void {
  const ts = new Date().toLocaleTimeString('sv-SE')
  const line = `[${ts}] ${prefix}${msg}`
  console.log(line)
  if (logFile) appendFileSync(logFile, line + '\n', 'utf-8')
}

export function log(msg: string): void {
  write('', msg)
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
