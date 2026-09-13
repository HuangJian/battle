/** logs.ts — 日志读取：字节容错解码、日志尾、组件日志定位与载荷。 */
import { closeSync, existsSync, openSync, readFileSync, readSync, readdirSync, statSync } from 'fs'
import path from 'path'
import { loadConfig } from '../../core/config'
import { LOG_DIR, NN_TRAINING, REPO_ROOT } from '../../core/paths'
import { entryForCourse, loadRegistry } from '../../core/registry'
import type { Component, RlConfig } from '../../core/types'
import type { LogPayload } from '../../web/view'
import { COMPONENT_LABELS, loadConsoleState } from '../actions'
import { ALL_COMPONENTS, COMPONENT_LOGS } from './component-meta'
import { discoverCourses, effectiveCourse } from './courses'

// ────────────────────────── 日志字节容错解码（§373） ──────────────────────────
// python 子进程（hub/worker/run_rl）在 zh-CN Windows 下可能以 GBK(stdout) 写日志，
// 整段按 UTF-8 解码会产生「˲ʱ󣩡」式乱码。逐行严格 UTF-8 解码，失败行用
// GB18030（GBK 超集）重解——纯 UTF-8 文件零影响，只有真正 GBK 行走兜底。

function decodeLogBytes(u8: Uint8Array): string {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(u8)
  } catch {
    return new TextDecoder('gb18030').decode(u8)
  }
}

/** 按 \n 字节切行并逐行容错解码（窗口读的 buf 含被切半的 UTF-8/GBK 尾字节也不影响其它行）。 */
function splitLogBytes(buf: Uint8Array): string[] {
  const out: string[] = []
  let start = 0
  for (let i = 0; i < buf.length; i++) {
    if (buf[i] === 10) {
      out.push(decodeLogBytes(buf.subarray(start, i)))
      start = i + 1
    }
  }
  if (start < buf.length) out.push(decodeLogBytes(buf.subarray(start)))
  return out
}

export function logTail(nnRel: string, n = 5): string[] {
  let raw: Uint8Array
  try {
    raw = readFileSync(path.isAbsolute(nnRel) ? nnRel : path.join(REPO_ROOT, 'nn-training', nnRel))
  } catch {
    return [] // 文件缺失/暂时不可读 = 无日志尾（正常态，非错误）
  }
  const out: string[] = []
  for (const line of splitLogBytes(raw)) {
    if (!line) continue
    out.push(line.length > 200 ? line.slice(0, 200) : line)
  }
  return out.slice(-n) // 只取尾 n 行（原语义）
}

// ────────────────────────── 日志查看（§348 补 2） ──────────────────────────

/** 组件日志解析：静态映射存在 → 用之；否则账本 entry.log → 否则运行时动态查找（§374）：
 *  cloudflared 每次 spawn 生成 cloudflared-<ts>.log（动态文件名），trainingLoop 日志在
 *  tmp/<course>/ 下，清理 tmp 或换课程后静态路径会失效——按组件语义扫 tmp 找最近活跃文件。
 *  全失败返回静态路径（让 UI 显示「日志文件不存在」占位，而非 404）。 */
export function resolveComponentLog(key: Component, cfg: RlConfig, course: string): string | null {
  const mapped = COMPONENT_LOGS[key]?.(cfg, course)
  if (mapped && existsSync(mapped)) return mapped
  const entryLog = entryForCourse(loadRegistry(), key, course)?.log
  if (entryLog && existsSync(entryLog)) return entryLog
  const found = findLatestLog(key, course)
  if (found) return found
  return mapped ?? entryLog ?? null
}

/** 组件日志文件名匹配（LOG_DIR 一层放文件；trainingLoop 在 LOG_DIR/<course>/ 子目录）。 */
const LOG_NAME_MATCH: Record<Component, (name: string) => boolean> = {
  selfNode: (n) => n.startsWith('sampler-agent') && n.endsWith('.log'),
  hubServer: (n) => n.startsWith('hub-server'),
  cloudflared: (n) => n.startsWith('cloudflared') && n.endsWith('.log'),
  trainingLoop: (n) => n === 'training-loop.log',
  workerServe: (n) => n.startsWith('remote-worker-serve') && n.endsWith('.log'),
}

/**
 * 运行时动态查找组件日志（§374）：trainingLoop 扫 LOG_DIR 各课程目录的 training-loop.log；
 * 其余扫 LOG_DIR 一层匹配文件，mtime 最新者为准。只扫一层 + 定点 stat，不做全文递归
 * （§366 教训：扫描慢路径会拖垮请求）。dir 参数化（§381）：真实路径用 LOG_DIR，
 * 单测注入临时目录获得确定性。
 */
export function scanLatestLog(dir: string, key: Component, course: string): string | null {
  let bestP: string | null = null
  let bestM = -1
  const consider = (p: string): void => {
    try {
      const m = statSync(p).mtimeMs
      if (m > bestM) {
        bestP = p
        bestM = m
      }
    } catch {
      /* stat race */
    }
  }
  const match = LOG_NAME_MATCH[key]
  try {
    if (key === 'trainingLoop') {
      if (course) consider(path.join(dir, course, 'training-loop.log'))
      for (const d of readdirSync(dir, { withFileTypes: true })) {
        if (d.isDirectory()) consider(path.join(dir, d.name, 'training-loop.log'))
      }
    } else {
      for (const d of readdirSync(dir, { withFileTypes: true })) {
        if (d.isFile() && match(d.name)) consider(path.join(dir, d.name))
      }
    }
  } catch {
    /* tmp unreadable */
  }
  return bestP
}

/** 对真实 LOG_DIR 的动态查找（scanLatestLog 的默认目录版）。 */
export function findLatestLog(key: Component, course: string): string | null {
  return scanLatestLog(LOG_DIR, key, course)
}

/** 从文件末尾读取至多 maxLines 行（readFileSync 整文件读对 GB 级增长日志是浪费；
 *  先 stat 再只读尾部字节窗口——日志页 2s 自动刷新，这是热路径）。
 *  maxLines='all'（§371 优化 1）：读整个文件（字节窗口放宽到 4MB 上限，行数不截）。 */
export function readLogTail(
  nnRel: string,
  maxLines: number | 'all' = 200,
  maxBytes = 512 * 1024,
): {
  lines: string[]
  exists: boolean
  fileSize: number
  truncated: boolean
  /** 文件总行数（顶部「共 N 行」）；>8MB 返回 null（UI 按截断窗口退化显示）。 */
  totalLines: number | null
} {
  const abs = path.isAbsolute(nnRel) ? nnRel : path.join(NN_TRAINING, nnRel)
  let fileSize = 0
  try {
    fileSize = statSync(abs).size
  } catch {
    return { lines: [], exists: false, fileSize: 0, truncated: false, totalLines: null }
  }
  const all = maxLines === 'all'
  const effBytes = all ? Math.max(maxBytes, 4 * 1024 * 1024) : maxBytes
  const window = Math.min(effBytes, fileSize)
  const buf = Buffer.alloc(window)
  try {
    const fh = openSync(abs, 'r')
    try {
      readSync(fh, buf, 0, window, fileSize - window)
    } finally {
      closeSync(fh)
    }
  } catch {
    return { lines: [], exists: true, fileSize, truncated: false, totalLines: null }
  }
  // 首行多半是被窗口切半的残行——丢弃（除非窗口覆盖了整个文件）。
  const partial = window < fileSize
  const lines = splitLogBytes(buf)
  if (partial) lines.shift()
  // 尾部空行折叠；过长行截断显示。
  const out = lines
    .filter((l) => l.length > 0)
    .slice(all ? undefined : -maxLines)
    .map((l) => (l.length > 500 ? `${l.slice(0, 500)}…` : l))
  // 顶部「共 N 行」要总行数：≤8MB 精确统计（字节计数换行 + 末尾残行），更大返回 null。
  let totalLines: number | null = null
  if (fileSize <= 8 * 1024 * 1024) {
    try {
      const whole = readFileSync(abs)
      let n = 0
      let idx = whole.indexOf(10)
      while (idx !== -1) (n++, (idx = whole.indexOf(10, idx + 1)))
      if (whole.length > 0 && whole[whole.length - 1] !== 10) n++
      totalLines = n
    } catch {
      totalLines = null
    }
  }
  return {
    lines: out,
    exists: true,
    fileSize,
    truncated: partial,
    totalLines,
  }
}

/** 日志页数据载荷（GET /api/log/<key> 与页面渲染共用）。courseOverride 为只读视图课程
 *  （?course=，已 sanitize）；空则回退操作员课程。 */
export async function componentLogPayload(
  key: Component,
  maxLines: number | 'all',
  courseOverride?: string,
): Promise<LogPayload | null> {
  if (!ALL_COMPONENTS.includes(key)) return null
  const cfg = loadConfig()
  const state = loadConsoleState()
  const course = courseOverride || effectiveCourse(state, discoverCourses())
  const nnRel = resolveComponentLog(key, cfg, course)
  if (!nnRel) return null
  const t = readLogTail(nnRel, maxLines)
  return {
    component: key,
    label: COMPONENT_LABELS[key],
    log: nnRel,
    exists: t.exists,
    fileSize: t.fileSize,
    lines: t.lines,
    truncated: t.truncated,
    totalLines: t.totalLines,
    updatedAt: Date.now(),
  }
}

/** 组件视图（健康探测按组件语义：端口服务 ping / 隧道 URL / 存活即健康）。
 *  探测并行（§366）：本地端口 1.5s、cloudflared 隧道 2.5s 超时，串行会叠加等待。 */
