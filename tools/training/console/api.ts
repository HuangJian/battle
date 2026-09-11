/** api.ts — 控制台 API：状态快照（GET /api/state）+ 动作路由（POST /api/*）。
 *
 *  快照即页面数据源：组件表（进程/健康/日志尾）、节点表（rl-config + 实时 ping）、
 *  模式开关（rl-config rl.* 键 + console-state）、课程数据与训练指标（iters.ts）。
 *  所有动作经 actions.ts（busy 互斥在那一层）；本层只做解析/分发/HTTP 语义映射：
 *  ActionError → 409，参数错误 → 400，动作失败（业务）→ 200 + ok:false。
 */

import { closeSync, existsSync, openSync, readFileSync, readdirSync, readSync, statSync } from 'fs'
import path from 'path'
import { CURRICULA_DIR, LOG_DIR, NN_TRAINING, REPO_ROOT } from '../paths'
import { httpOk, pidAlive } from '../net'
import { loadRegistry } from '../registry'
import { loadConfig } from '../config'
import { COMPONENT_LABELS, loadConsoleState, markCloudHaltRecovered, triggerCloudHalt } from './actions'
import { readIterMetrics } from './iters'
import { aggregateNodeHistory, emptyHistory, poolStatus } from './pool-history'
import { enqueueProbeRun } from './evalboard'
export { buildEvalBoardView } from './evalboard'
import type { Component, RlConfig } from '../types'
// 视图类型单一源：ui/view.ts（api.ts 不再定义本地视图类型）
import { parsePhaseFromLog, stripIsoPrefix } from '../ui/view'
import type {
  ComponentView,
  ConsoleStateView,
  LogPayload,
  MetricsView,
  NodeHistoryRow,
  NodeLocalView,
  NodeView,
  PhaseInfo,
  PoolView,
  SelfStatus,
} from '../ui/view'
import {
  ActionError,
  busy,
  smokeComponent,
  smokeTrain,
  setMode,
  setNodeConcurrency,
  setNodeEnabled,
  startComponent,
  startPreset,
  stopAll,
  stopComponent,
  type ActionResult,
  type StartCtx,
} from './actions'

// ────────────────────────── 快照类型（单一源 ui/view.ts，此处仅透传导出） ──────────────────────────

export type {
  ComponentView,
  ConsoleStateView,
  LogPayload,
  MetricsView,
  ModeView,
  NodeView,
} from '../ui/view'

// ────────────────────────── 配置读取（§361③：rl-config.json 瞬时读损坏兜底） ──────────────────────────

const CFG_RETRY_MS = 1000
let cfgLastOk: RlConfig | null = null
let cfgBadUntil = 0
const EMPTY_CONFIG = { version: 1, nodes: [], rl: {} } as unknown as RlConfig

/**
 * 读 rl-config.json + 损坏兜底。控制台自身 saveConfig 用 writeFileSync 非原子写
 * （2026-09-06 §339 同款竞态家族）——轮询恰落在写盘窗口会读到半截/空 JSON，loadConfig
 * 抛错会让 /api/state 整条 500 → 前端「刷新失败，正在重试」banner 假阳性（§361③根因）。
 * 失败回退上次成功配置（内存缓存；1s 坏窗内直接命中缓存，不再反复解析半截文件）；
 * 从未成功过则回退空配置（UI 降级为空节点/组件列表，不 500）。
 */
export function loadConfigSafe(): RlConfig {
  if (Date.now() < cfgBadUntil && cfgLastOk) return cfgLastOk
  try {
    const cfg = loadConfig()
    cfgLastOk = cfg
    cfgBadUntil = 0
    return cfg
  } catch {
    cfgBadUntil = Date.now() + CFG_RETRY_MS
    return cfgLastOk ?? EMPTY_CONFIG
  }
}

// ────────────────── 视图课程参数校验（局域网只读防线 2：GET ?course= 只读覆盖） ──────────────────

/** 校验 GET 端点（/api/state /api/pool /api/log/<key> /log/<key>）的 ?course= 参数：
 *  只放行真实存在的课程（tmp/<course> 目录或 curricula/<course>.jsonc）且形态限
 *  [A-Za-z0-9._-]——杜绝路径穿越（?course=../xxx）与任意路径读取。非法/空 → 返回 ''，
 *  视图静默回退操作员课程（服务端不报错；LAN 只是多看了一眼）。 */
export function sanitizeViewCourse(raw: string | null): string {
  if (!raw) return ''
  if (!/^[A-Za-z0-9._-]+$/.test(raw)) return ''
  try {
    if (existsSync(path.join(REPO_ROOT, 'tmp', raw))) return raw
    if (existsSync(path.join(CURRICULA_DIR, `${raw}.jsonc`))) return raw
  } catch {
    /* 回退自动课程 */
  }
  return ''
}

// ────────────────────────── 课程发现 ──────────────────────────

/** 发现可监控课程：tmp/ 下含 training_log.jsonl 的目录（按日志 mtime 新→旧）+ curricula/*.jsonc 中尚未落盘的课程。 */
export function discoverCourses(max = 12): string[] {
  const out: Array<{ name: string; mtime: number }> = []
  const seen = new Set<string>()
  try {
    for (const ent of readdirSync(path.join(REPO_ROOT, 'tmp'), { withFileTypes: true })) {
      if (!ent.isDirectory()) continue
      const lp = path.join(REPO_ROOT, 'tmp', ent.name, 'training_log.jsonl')
      try {
        out.push({ name: ent.name, mtime: statSync(lp).mtimeMs })
        seen.add(ent.name)
      } catch {
        /* no log — not a course dir */
      }
    }
  } catch {
    return []
  }
  // 补充 curricula/ 中尚未跑过的课程（按 jsonc mtime 新→旧），使新 course 首次选择有 UI 路径
  try {
    for (const ent of readdirSync(path.join(REPO_ROOT, 'nn-training', 'curricula'), {
      withFileTypes: true,
    })) {
      if (ent.isDirectory() || !ent.name.endsWith('.jsonc')) continue
      const name = ent.name.replace(/\.jsonc$/, '')
      if (seen.has(name)) continue
      const cp = path.join(REPO_ROOT, 'nn-training', 'curricula', ent.name)
      out.push({ name, mtime: statSync(cp).mtimeMs })
    }
  } catch {
    /* no curricula dir — fall through */
  }
  return out
    .sort((a, b) => b.mtime - a.mtime)
    .slice(0, max)
    .map((c) => c.name)
}

// ───────────────────── 课程单一事实源（DECISIONS §351 bug 1） ─────────────────────

/** console-state 中课程字段的形态（loadConsoleState 的结构子集）。 */
export interface ConsoleCourseState {
  course: string
}

/** 生效课程：console-state 优先，空则回退最近活跃课程（与页面显示同源）。
 *  读路径不回写 console-state——保持「手动选择才持久化」语义。 */
export function effectiveCourse(state: ConsoleCourseState, discovered: string[]): string {
  return state.course || discovered[0] || ''
}

/** 动作上下文（routeAction 专用）：显式 body.course > effectiveCourse。
 *  保证「页面显示的课程 = 动作实际使用的课程」不变量。 */
export function actionCtx(body: PostBody): StartCtx {
  const state = loadConsoleState()
  return {
    course: str(body, 'course') || effectiveCourse(state, discoverCourses()),
    trainerPpo: state.trainerPpo,
  }
}
// ────────────────────────── 快照组装 ──────────────────────────

// 组件日志统一读 LOG_DIR（仓库根 tmp/，2026-09-08 双 tmp 统一）——绝对路径，
// 与组件启动写入路径（specs.ts log:）同源，不依赖控制台自身 cwd。
const COMPONENT_LOGS: Partial<Record<Component, (cfg: RlConfig, course: string) => string>> = {
  selfNode: () => path.join(LOG_DIR, 'sampler-agent.log'),
  hubServer: () => path.join(LOG_DIR, 'hub-server.out'),
  cloudflared: (_c) => path.join(LOG_DIR, 'cloudflared.log'),
  trainingLoop: (_cfg, course) => path.join(LOG_DIR, course || 'nocourse', 'training-loop.log'),
  workerServe: () => path.join(LOG_DIR, 'remote-worker-serve.log'),
}

const HEALTHY_PORTS: Partial<Record<Component, (cfg: RlConfig) => string>> = {
  selfNode: (c) => `http://127.0.0.1:${c.rl.agent_port}/v1/ping`,
  hubServer: (c) => `http://127.0.0.1:${c.rl.hub_port}/ping`,
  workerServe: (c) => `http://127.0.0.1:${c.rl.hub_port + 2}/ping`,
}

const ALL_COMPONENTS: Component[] = [
  'selfNode',
  'hubServer',
  'cloudflared',
  'trainingLoop',
  'workerServe',
]

/** 逐行容错的日志尾（§361③）：单行损坏/读取异常只丢该行，不再让整个 /api/state 500。 */
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
  const entryLog = loadRegistry()[key]?.log
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
export async function componentViews(cfg: RlConfig, course: string): Promise<ComponentView[]> {
  const reg = loadRegistry()
  return Promise.all(
    ALL_COMPONENTS.map(async (key): Promise<ComponentView> => {
      const e = reg[key]
      const alive = pidAlive(e?.pid)
      const status: ComponentView['status'] = e ? (alive ? 'running' : 'exited') : 'stopped'
      let healthy: boolean | null = null
      const probe = HEALTHY_PORTS[key]?.(cfg)
      if (status === 'running' && probe) {
        healthy = await httpOk(
          probe,
          key === 'selfNode'
            ? (cfg.nodes.find((n) => n.id === 'self')?.authKey ?? '')
            : cfg.rl.remote_token,
          1500,
        )
      } else if (status === 'running' && key === 'cloudflared') {
        healthy = e?.url ? await httpOk(`${e.url}/ping`, cfg.rl.remote_token, 2500) : null
      } else if (status === 'running' && key === 'trainingLoop') {
        healthy = true // 存活即健康（就绪以日志产出为准，见 iters 指标）
      }
      // 运行时动态查找（§374）：静态映射 ≠ 实际落盘文件（cloudflared 动态文件名、
      // 课程子目录日志、tmp 清理后重建）——组件表日志/尾行与 /log/<key> 页同源。
      const logRel = resolveComponentLog(key, cfg, course)
      return {
        key,
        label: COMPONENT_LABELS[key],
        status,
        pid: e?.pid ?? null,
        url: e?.url ?? null,
        course: e?.course ?? null,
        mode: e?.mode ?? null,
        healthy,
        log: logRel,
        logTail: logRel ? logTail(logRel) : [],
        /** §380：非正常退出原因（exit-watchdog 记录），UI 显示"已退出"处展示。 */
        error: e?.error ?? null,
        busy: busy.has(`start:${key}`) || busy.has(`stop:${key}`) || busy.has(`smoke:${key}`),
        // cloudflared 卡展示隧道 auth key（复制用）；其余组件无密钥字段
        ...(key === 'cloudflared' ? { secret: cfg.rl.remote_token } : {}),
      }
    }),
  )
}

/** 节点视图（rl-config + enabled 节点并行 ping）。
 *  并行是硬要求：不可达节点各自等 AbortSignal.timeout(1500)，串行会让 /api/state
 *  在节点离线时拖到 N×4s（2026-09-08 实测 5 启用节点 10.1s → 超过 Bun.serve 默认
 *  idleTimeout 10s，服务端关连接 → curl 空回复；DECISIONS §365）。Promise.all 保序，
 *  输出与串行一致。超时 1500ms：健康探测口径，1.5s 不应答即视为离线（§366 预算）。 */
export async function nodeViews(cfg: RlConfig): Promise<NodeView[]> {
  return Promise.all(
    cfg.nodes.map(async (n): Promise<NodeView> => {
      let online: boolean | null = null
      let codeHash: string | null = null
      let cpus: number | null = null
      if (n.enabled) {
        try {
          const resp = await fetch(`${n.url}/v1/ping`, {
            headers: { Authorization: `Bearer ${n.authKey}` },
            signal: AbortSignal.timeout(1500),
          })
          online = resp.status === 200
          if (online) {
            const body = (await resp.json()) as { codeHash?: string; cpus?: number }
            codeHash = body.codeHash ? body.codeHash.slice(0, 12) : null
            cpus = typeof body.cpus === 'number' ? body.cpus : null
          }
        } catch {
          online = false
        }
      }
      return {
        id: n.id,
        url: n.url,
        gpuPush: !!n.gpu_push,
        enabled: n.enabled,
        concurrency: n.concurrency,
        online,
        codeHash,
        cpus,
        busy: busy.has(`node:${n.id}`),
        lastContrib: -1,
      }
    }),
  )
}

// ────────────────────────── 慢部件快照缓存（§366：页面加载 <1s） ──────────────────────────
// 节点 ping（超时 1.5s）、组件健康探测（1.5-2.5s）、池历史聚合（磁盘）全部移出请求路径：
// 后台刷新器每 SNAPSHOT_REFRESH_MS 重算一次，buildStateView 只读缓存。请求侧开销只剩
// 配置/课程/指标（实测 ~150ms）；新鲜度 ≤1 个刷新周期（5s），对监控面板不可见。
// 动作（启/停/切课）经 invalidateSlowSnapshot 置空缓存 → 下一次 buildStateView 冷算即时反映。
// 局域网只读（§…）：缓存按课程键控——LAN 切换查看课程时按课程各自冷算/刷新，互不串数据。
// 组件/节点视图本身全局（进程状态），但 trainingLoop 日志尾与阶段是课程相关的，故整体键控。

export interface SlowSnapshot {
  components: ComponentView[]
  nodes: NodeView[]
  localNode: NodeLocalView | null
  phase: PhaseInfo
}

const SNAPSHOT_REFRESH_MS = 5000
/** 非操作员课程（LAN 查看者切到的课程）快照的存活窗口：超过该时长未被查看即丢弃，
 *  避免后台刷新器为无人看的课程持续做节点 ping（§366 慢探测预算）。 */
const SNAPSHOT_VIEW_TTL_MS = 60_000

interface SlowSnapEntry {
  snap: SlowSnapshot
  at: number
  lastAccess: number
}

const slowSnapshots = new Map<string, SlowSnapEntry>()
const snapshotInFlight = new Map<string, Promise<SlowSnapshot>>()

/** 重算指定课程的慢部件快照（不落缓存；落缓存由 getSlowSnapshot 负责）。 */
async function computeSlowSnapshot(cfg: RlConfig, course: string): Promise<SlowSnapshot> {
  const [components, nodes] = await Promise.all([componentViews(cfg, course), nodeViews(cfg)])
  // 节点上一轮贡献数（池历史聚合；无数据 = -1）。
  const contribById = new Map<string, number>()
  // local 节点：只由配置决定（配置缺失/非法才缺省）。池历史不可用只是贡献数拿不到（-1），
  // 不能因此把 local 节点整块吞掉——此前它在 try 里，aggregateNodeHistory() 一抛就丢了。
  const slots = Number(cfg.rl.local_slots)
  let localNode: NodeLocalView | null =
    Number.isInteger(slots) && slots >= 0 ? { id: 'local', slots, lastContrib: -1 } : null
  try {
    const { hist, globalMaxIt } = aggregateNodeHistory()
    for (const [id, h] of hist) contribById.set(id, globalMaxIt >= 0 ? h.lastIterOk : -1)
    const localH = hist.get('local')
    if (localNode) localNode.lastContrib = globalMaxIt >= 0 ? (localH?.lastIterOk ?? 0) : -1
  } catch {
    /* 池历史不可用 → 贡献数保持 -1（local 节点仍按配置显示） */
  }
  for (const n of nodes) n.lastContrib = contribById.has(n.id) ? contribById.get(n.id)! : -1
  // 当前训练阶段（训练循环日志尾解析）。
  const logTail = components.find((c) => c.key === 'trainingLoop')?.logTail ?? []
  const phase = parsePhaseFromLog(logTail)
  return { components, nodes, localNode, phase }
}

/** 取指定课程的快照：5s 内新鲜命中缓存；否则按课程单飞重算（并发共享同一次计算）。 */
export function getSlowSnapshot(cfg: RlConfig, course: string): Promise<SlowSnapshot> {
  const now = Date.now()
  const ent = slowSnapshots.get(course)
  if (ent) ent.lastAccess = now
  if (ent && now - ent.at < SNAPSHOT_REFRESH_MS) return Promise.resolve(ent.snap)
  const inflight = snapshotInFlight.get(course)
  if (inflight) return inflight
  const p = computeSlowSnapshot(cfg, course).then(
    (snap) => {
      slowSnapshots.set(course, { snap, at: Date.now(), lastAccess: Date.now() })
      snapshotInFlight.delete(course)
      return snap
    },
    (e) => {
      snapshotInFlight.delete(course)
      throw e
    },
  )
  snapshotInFlight.set(course, p)
  return p
}

/** 动作后置空缓存（全部课程）：下一次 buildStateView 冷算，动作结果即时上屏
 *  （不主动后台刷新，避免与请求竞争）。 */
export function invalidateSlowSnapshot(): void {
  slowSnapshots.clear()
}

/** 后台刷新器：立即暖一次 + 每 intervalMs 重算（unref，不阻止进程退出）。服务端启动时
 *  调用一次。操作员课程每拍必刷（保持原语义）；近期被查看（SNAPSHOT_VIEW_TTL_MS 内）的
 *  其它课程跟随刷新；超时未看的课程丢弃，不再占用探测预算。 */
export function startSnapshotRefresher(intervalMs: number = SNAPSHOT_REFRESH_MS): void {
  const run = async (): Promise<void> => {
    try {
      const cfg = loadConfigSafe()
      const operatorCourse = effectiveCourse(loadConsoleState(), discoverCourses())
      const now = Date.now()
      const targets = new Set<string>([operatorCourse])
      for (const [c, ent] of slowSnapshots) {
        if (c === operatorCourse) continue
        if (now - ent.lastAccess > SNAPSHOT_VIEW_TTL_MS) {
          slowSnapshots.delete(c)
          continue
        }
        targets.add(c)
      }
      await Promise.all([...targets].map((c) => getSlowSnapshot(cfg, c)))
    } catch {
      /* 下一拍重试 */
    }
  }
  void run()
  const t = setInterval(() => void run(), intervalMs)
  t.unref?.()
}

/** 完整状态快照（页面轮询的数据源）。慢部件（节点 ping/组件探测/池历史）走快照缓存。
 *  courseOverride 为只读视图课程（?course=，已 sanitize）；空则回退操作员课程。
 *  只读覆盖不写 console-state——LAN 切换查看课程绝不影响训练。 */
export async function buildStateView(courseOverride?: string): Promise<ConsoleStateView> {
  const cfg = loadConfigSafe()
  const state = loadConsoleState()
  const courses = discoverCourses()
  const course = courseOverride || effectiveCourse(state, courses)
  const { components, nodes, localNode, phase } = await getSlowSnapshot(cfg, course)
  let metrics: MetricsView = { available: false, iters: [] }
  if (course && existsSync(path.join(REPO_ROOT, 'tmp', course, 'training_log.jsonl'))) {
    try {
      metrics = {
        available: true,
        iters: readIterMetrics(path.join(REPO_ROOT, 'tmp', course)).rows,
      }
    } catch (e) {
      metrics = { available: false, iters: [], error: e instanceof Error ? e.message : String(e) }
    }
  }
  return {
    time: new Date().toISOString(),
    course,
    courses,
    components,
    nodes,
    localNode,
    modes: {
      trainerPpo: state.trainerPpo,
      stream: Number(cfg.rl.stream ?? 0),
      doubleBuffer: Number(cfg.rl.double_buffer ?? 0),
      precollectEarly: Number(cfg.rl.precollect_early ?? 0),
    },
    metrics,
    phase,
    cloudHalt: state.cloudHalt ?? null,
  }
}

// ────────────────────────── /api/pool（§3.3 数据端点：独立慢节奏 + 服务端 30s TTL 缓存） ──────────────────────────

const POOL_TTL_MS = 30_000
/** 缓存 key 带 course（DS-E1）：切课程天然 miss 重算，30s 窗口内不错课程数据。 */
const poolCache = new Map<string, { at: number; view: PoolView }>()

// 本机 codeHash 惰性 memo（R9）：sampler-agent 是重模块，lazy import + 5s TTL；失败降级空串。
let localHashMemo = ''
let localHashAt = 0
async function localCodeHash(): Promise<string> {
  if (localHashMemo && Date.now() - localHashAt < 5000) return localHashMemo
  try {
    const mod = (await import('../../agent/sampler-agent')) as {
      collectCodeHashEntries: () => { relPath: string; content: Buffer }[]
      computeCodeHashFromFiles: (e: { relPath: string; content: Buffer }[]) => string
    }
    localHashMemo = mod.computeCodeHashFromFiles(mod.collectCodeHashEntries())
  } catch {
    localHashMemo = ''
  }
  localHashAt = Date.now()
  return localHashMemo
}

/** selfNode 存活时拉 /v1/status；失败/未启动 → null（UI 显示「agent 未启动」占位，不伪造）。 */
async function fetchSelfStatus(cfg: RlConfig): Promise<SelfStatus | null> {
  const reg = loadRegistry()
  if (!pidAlive(reg.selfNode?.pid)) return null
  const auth = cfg.nodes.find((n) => n.id === 'self')?.authKey ?? ''
  try {
    const resp = await fetch(`http://127.0.0.1:${cfg.rl.agent_port}/v1/status`, {
      headers: auth ? { Authorization: `Bearer ${auth}` } : {},
      signal: AbortSignal.timeout(4000),
    })
    if (!resp.ok) return null
    const b = (await resp.json()) as {
      workers?: number
      gamesDoneTotal?: number
      inflight?: unknown[]
      diskFreeMB?: number | null
      lastError?: string
      uptimeSec?: number
      resultCache?: { items?: number; bytes?: number }
      recentFailed?: number
    }
    return {
      workers: Number(b.workers ?? 0),
      inflight: Array.isArray(b.inflight) ? b.inflight.length : 0,
      gamesDoneTotal: Number(b.gamesDoneTotal ?? 0),
      diskFreeMB: typeof b.diskFreeMB === 'number' ? b.diskFreeMB : null,
      lastError: b.lastError ? stripIsoPrefix(String(b.lastError)).slice(0, 200) : null,
      uptimeSec: typeof b.uptimeSec === 'number' ? b.uptimeSec : null,
      resultCacheItems: Number(b.resultCache?.items ?? 0),
      resultCacheBytes: Number(b.resultCache?.bytes ?? 0),
      recentFailed: Number(b.recentFailed ?? 0),
    }
  } catch {
    return null
  }
}

function nodeHistoryRow(
  n: {
    id: string
    url: string
    authKey: string
    enabled: boolean
    concurrency: number
    gpu_push?: boolean
  },
  h: ReturnType<typeof emptyHistory>,
  agg: { globalMaxIt: number },
  ping: Record<string, unknown> | null,
  pingMs: number | null,
  localHash: string,
): NodeHistoryRow {
  const disabled = !n.enabled
  let status: NodeHistoryRow['status']
  let spec = '-'
  let version = ''
  let versionOk: boolean | null = null
  if (disabled) {
    status = 'disabled'
  } else if (ping) {
    const codeHash = String(ping.codeHash ?? '')
    version = codeHash ? codeHash.slice(0, 7) : ''
    versionOk = codeHash.length > 0 ? codeHash === localHash : null
    spec = ping.cpus ? `${ping.cpus} 核` : '?核'
    status = poolStatus(h) // 在线但无结算历史 → 'nodata' 灰显
  } else {
    status = h.recent.length === 0 ? 'noping' : poolStatus(h)
  }
  return {
    id: n.id,
    kind: 'node',
    status,
    okN: h.recent.filter(Boolean).length,
    recentN: h.recent.length,
    spec,
    versionOk,
    version,
    versionLocal: localHash.slice(0, 7),
    pingMs: disabled ? null : pingMs,
    ok: h.ok,
    fail: h.fail,
    contrib: h.lastIterOk,
    contribRollout: h.contribRollout,
    contribEval: h.contribEval,
    lastIter: h.lastIter,
    globalMaxIt: agg.globalMaxIt,
    avgElapsedSec: h.avgElapsedSec,
    lastOkTs: h.lastOkTs,
    lastFailTs: h.lastFailTs,
    lastError: h.lastError,
    recent: h.recent,
  }
}

/** 池汇总（首屏不入流：扫描 tmp 太重，客户端异步拉；R7）。courseOverride 为只读视图课程
 *  （?course=，已 sanitize）；空则回退操作员课程。缓存 key 已带 course（DS-E1）。 */
export async function buildPoolView(fresh = false, courseOverride?: string): Promise<PoolView> {
  const cfg = loadConfig()
  const state = loadConsoleState()
  const course = courseOverride || effectiveCourse(state, discoverCourses())
  const key = `pool-view:${course}`
  const cached = poolCache.get(key)
  if (!fresh && cached && Date.now() - cached.at < POOL_TTL_MS) return cached.view

  const hash = await localCodeHash()
  const agg = aggregateNodeHistory()
  const probes = await Promise.all(
    cfg.nodes.map(async (n) => {
      if (!n.enabled)
        return { n, ping: null as Record<string, unknown> | null, ms: null as number | null }
      const started = Date.now()
      try {
        const resp = await fetch(`${n.url.replace(/\/$/, '')}/v1/ping`, {
          headers: { Authorization: `Bearer ${n.authKey}` },
          signal: AbortSignal.timeout(2500),
        })
        if (!resp.ok) return { n, ping: null, ms: Date.now() - started }
        return { n, ping: (await resp.json()) as Record<string, unknown>, ms: Date.now() - started }
      } catch {
        return { n, ping: null, ms: Date.now() - started }
      }
    }),
  )

  const nodes: NodeHistoryRow[] = []
  for (const { n, ping, ms } of probes) {
    const h = agg.hist.get(n.id) ?? emptyHistory()
    nodes.push(nodeHistoryRow(n, h, agg, ping, ms, hash))
  }

  const localH = agg.hist.get('local') ?? emptyHistory()
  const slots = Number(cfg.rl.local_slots)
  const local: NodeHistoryRow = {
    id: 'local',
    kind: 'local',
    status: poolStatus(localH),
    okN: localH.recent.filter(Boolean).length,
    recentN: localH.recent.length,
    // 显式 0 也显示「0 槽」（与 pill 芯片同口径）；配置缺失/非法（NaN）才 '-'。
    spec: Number.isInteger(slots) && slots >= 0 ? `${slots} 槽` : '-',
    versionOk: null,
    version: '',
    versionLocal: hash.slice(0, 7),
    pingMs: null,
    ok: localH.ok,
    fail: localH.fail,
    contrib: localH.lastIterOk,
    contribRollout: localH.contribRollout,
    contribEval: localH.contribEval,
    lastIter: localH.lastIter,
    globalMaxIt: agg.globalMaxIt,
    avgElapsedSec: localH.avgElapsedSec,
    lastOkTs: localH.lastOkTs,
    lastFailTs: localH.lastFailTs,
    lastError: localH.lastError,
    recent: localH.recent,
  }

  const view: PoolView = {
    cachedAt: Date.now(),
    course,
    epochMs: agg.epochMs,
    activeFlow: agg.activeFlow,
    nodes,
    local,
    selfStatus: await fetchSelfStatus(cfg),
    localHash: hash,
  }
  poolCache.set(key, { at: view.cachedAt, view })
  return view
}

// ────────────────────────── 动作路由 ──────────────────────────

interface PostBody {
  [key: string]: unknown
}

function str(b: PostBody, k: string): string {
  const v = b[k]
  return typeof v === 'string' ? v : ''
}

function okResp(r: ActionResult, status = 200): Response {
  return new Response(JSON.stringify(r), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  })
}

function errResp(message: string, status: number): Response {
  return okResp({ ok: false, message }, status)
}

/** 解析/分发 POST /api/*。返回 null = 未匹配（调用方 404）。 */
export async function routeAction(action: string, body: PostBody): Promise<Response | null> {
  const ctx = actionCtx(body)
  try {
    switch (action) {
      case 'start': {
        const key = str(body, 'component') as Component
        if (!ALL_COMPONENTS.includes(key)) return errResp(`未知组件: ${key}`, 400)
        return okResp(await startComponent(key, ctx))
      }
      case 'stop': {
        const key = str(body, 'component') as Component
        if (!ALL_COMPONENTS.includes(key)) return errResp(`未知组件: ${key}`, 400)
        return okResp(await stopComponent(key))
      }
      case 'stopAll':
        return okResp(await stopAll())
      case 'cloud-halt': {
        // 手动下发停机命令（§386）：任务照常分发，云机先试停机停不掉继续干活。
        const reason = str(body, 'reason') || '手动下发停机命令'
        return okResp(await triggerCloudHalt(loadConfigSafe(), reason))
      }
      case 'cloud-resume': {
        // 停机条件消失（手动恢复）：hub resume + recovered（灰横幅保留历史）。
        const clearReason = str(body, 'reason') || '手动恢复'
        return okResp(await markCloudHaltRecovered(loadConfigSafe(), clearReason))
      }
      case 'smoke': {
        const key = str(body, 'component') as Component
        if (!ALL_COMPONENTS.includes(key)) return errResp(`未知组件: ${key}`, 400)
        return okResp(await smokeComponent(key, ctx))
      }
      case 'smokeTrain':
        return okResp(await smokeTrain(ctx.course))
      case 'preset': {
        const mode = str(body, 'mode')
        if (!['pull', 'push', 'local'].includes(mode)) return errResp(`未知预设: ${mode}`, 400)
        return okResp(await startPreset(mode as 'pull' | 'push' | 'local', ctx.course))
      }
      case 'setMode': {
        const key = str(body, 'key')
        const value = str(body, 'value')
        return okResp(await setMode(key, value))
      }
      case 'setCourse': {
        const { saveConsoleState, ActionError } = await import('./actions')
        const course = str(body, 'course')
        if (course) {
          // validateCourseArg 会 process.exit（CLI 语义）——控制台改抛 ActionError。
          const { existsSync } = await import('fs')
          const { CURRICULA_DIR } = await import('../paths')
          const pathMod = await import('path')
          if (
            !existsSync(course) &&
            !existsSync(pathMod.default.join(CURRICULA_DIR, `${course}.jsonc`))
          ) {
            throw new ActionError(
              `课程不存在: ${course}（curricula/ 下无同名 .jsonc，或传已存在的课程文件路径）`,
            )
          }
        }
        saveConsoleState({ course })
        return okResp({ ok: true, message: `当前课程 = ${course || '(空)'}` })
      }
      case 'setNodeEnabled': {
        const id = str(body, 'id')
        const enabled = body.enabled === true || body.enabled === 'true'
        return okResp(await setNodeEnabled(id, enabled))
      }
      case 'setNodeConcurrency': {
        const id = str(body, 'id')
        const n = Number(body.concurrency)
        if (!Number.isFinite(n)) return errResp(`并发数非法: ${body.concurrency}`, 400)
        return okResp(await setNodeConcurrency(id, Math.round(n)))
      }
      case 'nodeSmoke': {
        const id = str(body, 'id')
        if (busy.has(`node:${id}`)) return errResp('该节点冒烟进行中', 409)
        const cfg = loadConfig()
        const node = cfg.nodes.find((x) => x.id === id)
        if (!node) return errResp(`节点不存在: ${id}`, 400)
        if (!node.enabled) return okResp({ ok: false, message: '节点已停用——先启用再冒烟' })
        const cCourse = ctx.course
        const cPath = path.join(REPO_ROOT, 'tmp', cCourse, 'weights.json')
        const weightsPath = existsSync(cPath)
          ? cPath
          : path.join(REPO_ROOT, 'tmp/ep60/battle2-p1bc/run/weights.json')
        if (!existsSync(weightsPath))
          return okResp({ ok: false, message: '无可用权重文件——无法 rollout 冒烟' })
        busy.add(`node:${id}`)
        try {
          const raw = readFileSync(weightsPath)
          const bytes = new Uint8Array(raw.byteLength)
          bytes.set(raw)
          const { rolloutSmokeNode, weightsFingerprint } = await import('../smoke')
          const wver = await weightsFingerprint(weightsPath)
          const passed = await rolloutSmokeNode(node, bytes, wver)
          return okResp({
            ok: passed,
            message: passed ? `${id} rollout 冒烟通过` : `${id} 冒烟失败（明细见控制台服务日志）`,
          })
        } finally {
          busy.delete(`node:${id}`)
        }
      }
      case 'evalProbeRun': {
        // EvalBench §8/§6.7：只 append 一行 pending（触发队列）；派发期节点配置
        // 冻结——任一 node:* 动作进行中则 409（与 setNodeEnabled 共 busy 语义）。
        for (const k of busy) {
          if (k.startsWith('node:')) return errResp('节点配置调整中，稍后再触发评估批', 409)
        }
        if (busy.has('eval:probe')) return errResp('评估入队进行中', 409)
        const rungFrom = str(body, 'rung_from') || str(body, 'rung') || 'c4l1'
        const ckpt = str(body, 'ckpt')
        if (!ckpt) return errResp('缺少 ckpt（权重文件路径）', 400)
        const policy = str(body, 'policy') === 'god' ? 'god' : 'nn'
        busy.add('eval:probe')
        try {
          const r = enqueueProbeRun({
            course: ctx.course,
            rung_from: rungFrom,
            ckpt,
            requester: str(body, 'requester') || 'web',
            iter: Number(body.iter) || 0,
            policy: policy as 'nn' | 'god',
            ladder_pos: body.ladder_pos === undefined ? undefined : Number(body.ladder_pos),
            k_seq: body.k_seq === undefined ? undefined : Number(body.k_seq),
          })
          return okResp({
            ok: true,
            message: `评估批已入队 ${r.batch_id}${r.deduped ? '（已在队列，去重）' : ''}`,
          })
        } finally {
          busy.delete('eval:probe')
        }
      }
      default:
        return null
    }
  } catch (e) {
    if (e instanceof ActionError) return errResp(e.message, 409)
    return errResp(e instanceof Error ? e.message : String(e), 500)
  }
}
