/** logs.ts — 日志读取：字节容错解码、日志尾、组件日志定位与载荷。 */
import { closeSync, existsSync, openSync, readSync, readdirSync, statSync } from 'fs'
import path from 'path'
import { loadConfig } from '../../core/config'
import { NN_TRAINING, REPO_ROOT, tmpLogsDir } from '../../core/paths'
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

/** 组件日志的尾窗口字节数（plan/dashboard-pool-history-idle-cost §7）。
 *
 *  **为什么必须是有界读**（2026-10-07 实测）：旧实现 `readFileSync(整个文件)` 再逐行切 —— 本机
 *  `sampler-agent.log` 22.5MB / `hub-server.log` 56.2MB，而 `componentViews` 对**每个组件**都调它，
 *  于是每拍读 ~80MB 并裂成行数组（实测单次 `componentViews` = **2348ms / heapΔ 94.9MB /
 *  rssΔ 260.9MB**），只为了取最后 5 行。256KB 足以容纳任何正常日志的最后 5 行（需要单行 >51KB
 *  才会截到）——不设更小是为了对「长行日志」保持语义余量。 */
const LOG_TAIL_BYTES = 256 * 1024

export function logTail(nnRel: string, n = 5): string[] {
  const abs = path.isAbsolute(nnRel) ? nnRel : path.join(REPO_ROOT, 'nn-training', nnRel)
  let fileSize = 0
  try {
    fileSize = statSync(abs).size
  } catch {
    return [] // 文件缺失/暂时不可读 = 无日志尾（正常态，非错误）
  }
  if (fileSize === 0) return []
  // 尾部窗口读（与 `readLogTail` 同法）：小文件 window = fileSize ⇒ 与旧实现逐字相同。
  const window = Math.min(LOG_TAIL_BYTES, fileSize)
  const buf = Buffer.alloc(window)
  try {
    const fh = openSync(abs, 'r')
    try {
      readSync(fh, buf, 0, window, fileSize - window)
    } finally {
      closeSync(fh)
    }
  } catch {
    return []
  }
  const lines = splitLogBytes(buf)
  if (window < fileSize) lines.shift() // 首行多半是被窗口切半的残行
  const out: string[] = []
  for (const line of lines) {
    if (!line) continue
    out.push(line.length > 200 ? line.slice(0, 200) : line)
  }
  return out.slice(-n) // 只取尾 n 行（原语义）
}

// ────────────────────────── 日志查看（§348 补 2） ──────────────────────────

/** 账本（`training_log.jsonl`）尾部**原样**读取：机器解析专用（`latestIterFromLedgerTail`
 *  等），不做展示用的行长截断。
 *
 *  2026-09-20 实测（x20-steady 真实账本）：`iteration` 事件带 wire / model 遥测，单行
 *  >1.2KB ⇒ `readLogTail` 的 500 字符截断把 JSON 截成 `…`，`JSON.parse` 必失败 ⇒
 *  `courseIter` 对**每一门课**都返回 null（总览「轮次」列恒显 `—`），完成水位也读不到。
 *  展示面（日志页）仍走 `readLogTail` 的截断（那正是它的目的，别把长行塞进 DOM）。 */
export function readLedgerTail(absPath: string, maxLines = 600): string[] {
  return readLogTail(absPath, maxLines, 512 * 1024, 'all').lines
}

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
  localWorker: (n) => n.startsWith('local-worker') && n.endsWith('.log'),
  trainingLoop: (n) => n === 'training-loop.log',
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

/** 对（默认）日志根的动态查找（scanLatestLog 的默认目录版）。
 *
 *  扫描根走 `tmpLogsDir()` 而**不是**常量 `LOG_DIR`：两者在生产环境是同一个目录（默认值
 *  就是 LOG_DIR，行为零变化），但前者可被 `BCITY_TMP_LOGS_DIR` 重定向 —— 单测不重定向就会
 *  扫**真实** `tmp/`，于是本机正在训练的那门课的 `training-loop.log` 会盖过被测课程
 *  （2026-09-22：`server-api-course-switch.test.ts` 因此红——按课程解析退化成「别课最新
 *  日志」，正是那条用例要防的串数据）。
 */
export function findLatestLog(key: Component, course: string): string | null {
  return scanLatestLog(tmpLogsDir(), key, course)
}

/** 分块数行（内存恒定 1MiB）。等价于「按 `\n` 计数 + 末尾无换行再算一行」—— 与旧的全文件版
 *  逐字节等价，但不再把整个文件（≤8MiB）读进堆。不可读 ⇒ `null`（调用方按缺省退化显示）。 */
function countLines(abs: string): number | null {
  const CH = 1024 * 1024
  const buf = Buffer.alloc(CH)
  let n = 0
  let pos = 0
  let last = -1
  try {
    const fh = openSync(abs, 'r')
    try {
      for (;;) {
        const k = readSync(fh, buf, 0, CH, pos)
        if (k <= 0) break
        pos += k
        const chunk = buf.subarray(0, k)
        let i = chunk.indexOf(10)
        while (i !== -1) {
          n++
          i = chunk.indexOf(10, i + 1)
        }
        last = chunk[k - 1]
      }
    } finally {
      closeSync(fh)
    }
  } catch {
    return null
  }
  if (pos > 0 && last !== 10) n++
  return n
}

/** 从文件末尾读取至多 maxLines 行（readFileSync 整文件读对 GB 级增长日志是浪费；
 *  先 stat 再只读尾部字节窗口——日志页 2s 自动刷新，这是热路径）。
 *  maxLines='all'（§371 优化 1）：读整个文件（字节窗口放宽到 4MB 上限，行数不截）。 */
export function readLogTail(
  nnRel: string,
  maxLines: number | 'all' = 200,
  maxBytes = 512 * 1024,
  /** 行长上限（**展示**用截断，默认 500，带省略号）；`'all'` = 不截断（机器解析用，
   *  见 `readLedgerTail`）。截断会破坏 JSON —— 账本的 `iteration` 事件带 wire/model
   *  遥测，单行常超 500 字符。 */
  maxLineLen: number | 'all' = 500,
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
    .map((l) => (maxLineLen === 'all' || l.length <= maxLineLen ? l : `${l.slice(0, maxLineLen)}…`))
  // 顶部「共 N 行」要总行数：≤8MB 精确统计（**分块**字节计数 + 末尾残行），更大返回 null。
  // ★ 2026-10-07（plan/dashboard-pool-history-idle-cost §7）：旧实现 `readFileSync` 整个文件再
  //   逐字节找换行 ⇒ 每调用一次就分配「文件大小（≤8MB）+ 分块结果」，而调用点里有一批是
  //   **每拍**的（`snapshot-cache` 每 5s 读账本）。分块版内存恒定 1MiB、逐字节等价。
  let totalLines: number | null = null
  if (fileSize <= 8 * 1024 * 1024) totalLines = countLines(abs)
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
