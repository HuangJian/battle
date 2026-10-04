/** offline-eval-backfill.ts — 离线课「云机没回传 eval」时，hub 端用 LAN 集群补评
 *  （plan/offline-eval-backfill.plan.md）。
 *
 *  用户指令（2026-10-04）：「离线课程，如果云机未回传 eval 结果（云机可能未启用 eval 以节省
 *  CPU/墙钟），则在 +3 it 的权重回传后在 hub 端使用 LAN 集群跑 eval。注意课程完成指定轮数
 *  收官时，如果最后一轮需要 eval，也要执行。」
 *
 *  为什么住控制台（而不是 hub/python）：启动 evalA 的唯一入口（spawn + `eval:A` 互斥键 +
 *  日志）在 `./eval-a-run`；hub 是独立 python 进程，同层不得 import trainer（无 spawn/互斥/
 *  日志持有者）。本模块只做「谁在什么时候点这一枪」——评估链本身与按钮 / 开课基线 / 导入后
 *  评估逐字同源（`trainer/eval_a_once.py`：同语料、同节点池、同账本 / 去重）。
 *
 *  语义（plan §2.2，逐条可机械验证）：
 *    · 评估点 = `N >= 1` ∧ `N % eval_every == 0`；课程无语料（`eval_games_per_stage <= 0`）
 *      整门跳过（与 `CloudEvalPlan.enabled` 同式）；
 *    · **云机没给读数** = 课程 `eval_log.jsonl` 里不存在 `(iter=N, wver=W16)` 的
 *      `eval_summary` 行（W16 = 回传轮 `row.json.weights_fp` 前 16 位；缺则按权重文件
 *      sha256 现算）——只有 summary 才算读数，逐局行不顶（evalA 只补缺局并结算，正合需要）；
 *    · **+3 宽限** = 该 run 已回传 `max(its) >= N + 3`（给云机自己的 eval 结果留 3 轮赶回
 *      来的时间；已回来 ⇒ S3 跳过）；
 *    · **收官** = `end_it_reached` 时最后一个评估点（`floor(it_end / eval_every) * eval_every`）
 *      缺读数 ⇒ **不等宽限立即补评**（后面没有轮次可以等）；非评估点的收官轮不评；
 *    · 权重必须在回传树 `<tmp>/<课>/remote-jobs/offline/<run>/it-NNN/weights.json`（首选）
 *      或交付镜像 `<tmp>/<课>/deliver/<run>/it-NNN/weights.json`（都有 ⇒ 用回传树）；
 *    · **串行幂等**：本进程 evalA 互斥键为单槽（已有在跑 ⇒ 本拍什么都不做）；每拍至多启动
 *      一个（候选按 `(N, run)` 升序）；补评成功后下一拍由账本 summary 自然去重；失败按
 *      `(课, run, N, W16)` 指数退避重试（10min 起、2h 封顶、不设硬上限）。
 *
 *  逃生阀 `BCITY_NO_OFFLINE_EVAL_BACKFILL`（测试/应急；与 `BCITY_NO_AUTO_BASELINE_EVAL` 同规）。
 *  本模块**不碰 python**、不写训练账本——只读 hub 观测面 / 课程配置 / 回传产物 / eval 账本。
 */

import { createHash } from 'crypto'
import { closeSync, openSync, readFileSync, readSync, statSync } from 'fs'
import path from 'path'
import { loadConfig } from '../core/config'
import { readJsoncFile } from '../core/jsonc'
import { log, warn } from '../core/log'
import { curriculaDir, tmpLogsDir } from '../core/paths'
import type { OfflineAdminView } from '../web/view'
import { busy } from './actions'
import { getHubAdmin } from './api'
import { EVAL_A_BUSY_KEY, launchEvalA, type EvalALaunch } from './eval-a-run'

// ────────────────────────── 常量（判据的唯一编号） ──────────────────────────

/** 补评轮询间隔：60s（慢决策——每拍至多启动一个 evalA；与快照 5s 节奏解耦）。 */
export const OFFLINE_EVAL_BACKFILL_INTERVAL_MS = 60_000
/** +3 宽限：评估点 N 的权重回传后，等该 run 回传推进到 `max(its) >= N + 3` 仍无读数才补评。 */
export const OFFLINE_EVAL_GRACE_IT = 3
/** 失败重试指数退避：10min 起、2h 封顶（「失败」= 启动后账本仍无 summary / 启动本身报错）。 */
export const OFFLINE_EVAL_BACKOFF_BASE_MS = 10 * 60_000
export const OFFLINE_EVAL_BACKOFF_MAX_MS = 2 * 60 * 60_000

/** 逃生阀：整段禁用补评（测试不开真子进程；应急时人工让位）。 */
export function offlineEvalBackfillDisabled(): boolean {
  return Boolean(process.env.BCITY_NO_OFFLINE_EVAL_BACKFILL)
}

// ────────────────────────── 课程配置（只读 eval 语料两键） ──────────────────────────

/** 课程里补评要用的两个字段（与 `CloudEvalPlan` 同口径）。 */
export interface CourseEvalConfig {
  /** 每几轮一个评估点（缺省 1，与 `CloudEvalPlan.due()` 同式）。 */
  evalEvery: number
  /** 每关每轮种子数（>0 才有语料；=0 整门跳过）。 */
  gamesPerStage: number
}

/** 读课程 `eval_every` / `eval_games_per_stage`；课程文件缺失/坏/无语料 → null（跳过，不猜）。 */
export function readCourseEvalConfig(
  course: string,
  root: string = curriculaDir(),
): CourseEvalConfig | null {
  try {
    const raw = readJsoncFile(path.join(root, `${course}.jsonc`))
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
    const rec = raw as Record<string, unknown>
    const games = toInt(rec.eval_games_per_stage)
    if (games <= 0) return null
    return { evalEvery: Math.max(1, toInt(rec.eval_every) || 1), gamesPerStage: games }
  } catch {
    return null
  }
}

function toInt(v: unknown): number {
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? Math.trunc(n) : 0
}

// ────────────────────────── 纯选择：哪些 (run, N) 该补评 ──────────────────────────

/** 一个 run 的调度事实（`its` = hub 已收到的回传轮；`itEnd`/`endItReached` 来自段末摘要）。 */
export interface OfflineRunSchedule {
  its: number[]
  endItReached?: boolean
  itEnd?: number | null
}

/** 一个待补评的评估点（reason：宽限到 / 收官立即）。 */
export interface OfflineEvalCandidate {
  run: string
  iter: number
  reason: 'grace' | 'final'
}

/** 纯函数：按 [+3 宽限 | 收官] 两条规则选出该补评的评估点（不含账本/权重/退避过滤）。
 *
 *  每 run 独立宽限（别的 run 回传得远不算）；结果按 `(iter, run)` 升序——调用方每拍只启动
 *  第一个通过全部过滤的候选，积压自然按轮次从小到大补齐。 */
export function selectOfflineEvalRounds(
  runs: Record<string, OfflineRunSchedule>,
  evalEvery: number,
  grace = OFFLINE_EVAL_GRACE_IT,
): OfflineEvalCandidate[] {
  const every = Math.max(1, toInt(evalEvery) || 1)
  const g = Math.max(0, toInt(grace))
  const out: OfflineEvalCandidate[] = []
  const seen = new Set<string>()
  const push = (run: string, iter: number, reason: 'grace' | 'final'): void => {
    const key = `${run}|${iter}`
    if (seen.has(key)) return
    seen.add(key)
    out.push({ run, iter, reason })
  }
  for (const run of Object.keys(runs)) {
    const input = runs[run]!
    const its = new Set<number>()
    let maxIt: number | null = null
    for (const v of input.its) {
      if (!Number.isInteger(v) || v < 1) continue
      its.add(v)
      maxIt = maxIt === null ? v : Math.max(maxIt, v)
    }
    // 宽限：max(its) >= N + 3 才轮到 N（评估点才有候选，其余轮次一律跳过）。
    if (maxIt !== null) {
      for (let n = every; n + g <= maxIt; n += every) push(run, n, 'grace')
    }
    // 收官：最后一个评估点缺读数时立即补（不再等 +3——后面没有轮次可以等）。
    const itEnd =
      typeof input.itEnd === 'number' && Number.isInteger(input.itEnd) ? input.itEnd : null
    if (input.endItReached === true && itEnd !== null) {
      const n = Math.floor(itEnd / every) * every
      if (n >= 1 && its.has(n)) push(run, n, 'final')
    }
  }
  out.sort((a, b) => a.iter - b.iter || (a.run < b.run ? -1 : a.run > b.run ? 1 : 0))
  return out
}

// ────────────────────────── 账本索引（增量读 eval_log.jsonl） ──────────────────────────

/** `tmp/<课>/eval_log.jsonl` 的**增量**读数索引：只认 `(iter, wver16)` 的 `eval_summary`。
 *
 *  每拍全量读账本会随课程变长（逐局行几百行/次评估）白烧 IO；这里用文件大小游标只读追加段，
 *  文件变短（截断/轮换/替换）即从头重建。半行（写入竞态）留在 tail 等下一拍补全。 */
export class EvalLedgerIndex {
  private offset = 0
  private tail = ''
  private readonly keys = new Set<string>()

  constructor(private readonly file: string) {}

  /** 只读自上次以来的追加段（首次 = 全文件）；文件不存在 ⇒ 无事发生。 */
  refresh(): void {
    let size: number
    try {
      size = statSync(this.file).size
    } catch {
      return
    }
    if (size < this.offset) {
      this.offset = 0
      this.tail = ''
      this.keys.clear()
    }
    if (size === this.offset) return
    let fd: number | null = null
    try {
      fd = openSync(this.file, 'r')
      const buf = Buffer.allocUnsafe(size - this.offset)
      let got = 0
      while (got < buf.length) {
        const n = readSync(fd, buf, got, buf.length - got, this.offset + got)
        if (n <= 0) break
        got += n
      }
      const text = this.tail + buf.toString('utf-8', 0, got)
      this.offset += got
      const lines = text.split('\n')
      this.tail = lines.pop() ?? ''
      for (const line of lines) this.ingest(line)
    } catch {
      /* 读失败 = 本拍没有新读数（下一拍重试）；绝不抛给 ticker */
    } finally {
      if (fd !== null) {
        try {
          closeSync(fd)
        } catch {
          /* 已关闭 */
        }
      }
    }
  }

  /** 该 `(iter, wver16)` 是否已有 summary。必须先 `refresh()`。 */
  has(iter: number, wver16: string): boolean {
    return this.keys.has(`${iter}:${wver16}`)
  }

  private ingest(line: string): void {
    if (!line.trim()) return
    let rec: unknown
    try {
      rec = JSON.parse(line)
    } catch {
      return
    }
    if (!rec || typeof rec !== 'object') return
    const r = rec as Record<string, unknown>
    if (r.event !== 'eval_summary') return
    const iter = r.iter
    const wver = r.wver
    if (typeof iter !== 'number' || !Number.isInteger(iter) || iter < 0) return
    if (typeof wver !== 'string' || wver.length < 16) return
    this.keys.add(`${iter}:${wver.slice(0, 16)}`)
  }
}

// ────────────────────────── 权重发现（回传树 → 交付镜像） ──────────────────────────

/** 一个可评估轮的权重文件 + 它进账本用的 W16。 */
export interface RoundCkpt {
  path: string
  key16: string
}

/** 权重文件 sha256 的缓存（免去每拍重算同一份文件的指纹）。 */
const fpCache = new Map<string, { size: number; mtimeMs: number; key16: string }>()

/** 找该 `(run, iter)` 的回传权重：回传树优先，交付镜像兜底；都没有 → null（等下次补传/导入）。
 *
 *  W16 与 evalA 同定义（权重文件字节的 sha256 前 16 位）：`row.json.weights_fp`（hub 入口
 *  已对账过实收字节）在场就用它，缺席再读文件现算（带 mtime/size 缓存）。 */
export function roundCkptFor(
  tmpRoot: string,
  course: string,
  run: string,
  iter: number,
): RoundCkpt | null {
  const dirName = `it-${String(iter).padStart(3, '0')}`
  for (const file of [
    path.join(tmpRoot, course, 'remote-jobs', 'offline', run, dirName, 'weights.json'),
    path.join(tmpRoot, course, 'deliver', run, dirName, 'weights.json'),
  ]) {
    const ck = ckptAt(file)
    if (ck) return ck
  }
  return null
}

function ckptAt(weightsFile: string): RoundCkpt | null {
  let st: ReturnType<typeof statSync>
  try {
    st = statSync(weightsFile)
  } catch {
    return null
  }
  if (!st.isFile()) return null
  try {
    const row = JSON.parse(
      readFileSync(path.join(path.dirname(weightsFile), 'row.json'), 'utf-8'),
    ) as Record<string, unknown>
    const fp = row?.weights_fp
    if (typeof fp === 'string' && fp.length >= 16)
      return { path: weightsFile, key16: fp.slice(0, 16) }
  } catch {
    /* 缺/坏 row.json ⇒ 现算（导入产物的交付镜像就没有 row.json） */
  }
  const cached = fpCache.get(weightsFile)
  if (cached && cached.size === st.size && cached.mtimeMs === st.mtimeMs)
    return { path: weightsFile, key16: cached.key16 }
  try {
    const key16 = createHash('sha256').update(readFileSync(weightsFile)).digest('hex').slice(0, 16)
    fpCache.set(weightsFile, { size: st.size, mtimeMs: st.mtimeMs, key16 })
    return { path: weightsFile, key16 }
  } catch {
    return null
  }
}

// ────────────────────────── 编排（可注入依赖，单测不开真子进程/网） ──────────────────────────

export interface OfflineEvalBackfillDeps {
  /** hub 观测面（`/admin/offline` 的 progress + results）；null = hub 不可达/旧版。 */
  fetchOfflineAdmin?: () => Promise<OfflineAdminView | null>
  /** 启动一次 evalA（缺省 = `./eval-a-run` 的真启动；测试注入替身）。 */
  launch?: (course: string, ckpt: string, iter: number) => EvalALaunch
  /** evalA 互斥键是否被占（缺省 = 同源 `busy` 集合）。 */
  isEvalABusy?: () => boolean
  now?: () => number
  logLine?: (msg: string) => void
  logWarn?: (msg: string) => void
  /** 测试接缝：课程目录 / 课程 tmp 根（缺省 = `paths.ts` 的惰性取值）。 */
  curriculaRoot?: string
  tmpRoot?: string
}

export interface BackfillLaunch extends OfflineEvalCandidate {
  course: string
  reason: 'grace' | 'final'
  ckpt: string
}

export interface BackfillTick {
  /** 本拍无需动作的原因；null = 看过候选（可能都没过过滤）。 */
  skipped: 'disabled' | 'busy' | 'no-admin' | null
  /** 本拍启动的那一个（null = 本拍没启动）。 */
  launched: BackfillLaunch | null
}

export interface OfflineEvalBackfill {
  /** 跑一拍：至多启动一个 evalA（见文件头语义）。**永不抛**。 */
  run: () => Promise<BackfillTick>
}

/** 生产缺省：经 `getHubAdmin` 读 hub（共享 5s SWR 缓存 + 单飞，零新增网络探测）。 */
async function defaultFetchOfflineAdmin(): Promise<OfflineAdminView | null> {
  const cfg = loadConfig()
  const admin = await getHubAdmin(cfg, '')
  if (!admin.offline) return null
  return {
    progress: admin.offline,
    results: admin.offlineResults ?? {},
    stalled: admin.offlineStalled ?? [],
  }
}

function backoffMs(attempt: number): number {
  const raw = OFFLINE_EVAL_BACKOFF_BASE_MS * 2 ** Math.max(0, attempt - 1)
  return Math.min(raw, OFFLINE_EVAL_BACKOFF_MAX_MS)
}

/** 制造一个补评拍器（依赖全可注入 ⇒ 单测用假 hub / 假 launch / 假钟）。 */
export function createOfflineEvalBackfill(deps: OfflineEvalBackfillDeps = {}): OfflineEvalBackfill {
  const fetchAdmin = deps.fetchOfflineAdmin ?? defaultFetchOfflineAdmin
  const launch = deps.launch ?? ((course, ckpt, iter) => launchEvalA(course, ckpt, iter))
  const isBusy = deps.isEvalABusy ?? (() => busy.has(EVAL_A_BUSY_KEY))
  const now = deps.now ?? Date.now
  const logLine = deps.logLine ?? log
  const warnLine = deps.logWarn ?? warn
  const ledgers = new Map<string, EvalLedgerIndex>()
  /** 每 `(课,run,N,W16)` 的启动次数 / 下次允许启动时刻（退避）。 */
  const attempts = new Map<string, number>()
  const nextAt = new Map<string, number>()

  async function run(): Promise<BackfillTick> {
    if (offlineEvalBackfillDisabled()) return { skipped: 'disabled', launched: null }
    if (isBusy()) return { skipped: 'busy', launched: null }
    let admin: OfflineAdminView | null
    try {
      admin = await fetchAdmin()
    } catch {
      admin = null
    }
    if (!admin || Object.keys(admin.progress).length === 0)
      return { skipped: 'no-admin', launched: null }
    const curRoot = deps.curriculaRoot ?? curriculaDir()
    const tmpRoot = deps.tmpRoot ?? tmpLogsDir()
    for (const course of Object.keys(admin.progress).sort()) {
      const cfg = readCourseEvalConfig(course, curRoot)
      if (!cfg) continue
      const runs: Record<string, OfflineRunSchedule> = {}
      for (const [run, p] of Object.entries(admin.progress[course] ?? {})) {
        const res = admin.results[course]?.[run]
        runs[run] = {
          its: p.its,
          endItReached: res?.endItReached === true,
          itEnd: res?.itEnd ?? null,
        }
      }
      for (const cand of selectOfflineEvalRounds(runs, cfg.evalEvery)) {
        const ck = roundCkptFor(tmpRoot, course, cand.run, cand.iter)
        if (!ck) continue // 权重还没回传（等下次补传/导入）
        const ledgerFile = path.join(tmpRoot, course, 'eval_log.jsonl')
        let ledger = ledgers.get(ledgerFile)
        if (!ledger) {
          ledger = new EvalLedgerIndex(ledgerFile)
          ledgers.set(ledgerFile, ledger)
        }
        ledger.refresh()
        if (ledger.has(cand.iter, ck.key16)) continue // 云机（或上一拍补评）已给读数
        const key = `${course}|${cand.run}|${cand.iter}|${ck.key16}`
        const t = now()
        if ((nextAt.get(key) ?? 0) > t) continue // 退避中
        if (isBusy()) return { skipped: 'busy', launched: null }
        const r = launch(course, ck.path, cand.iter)
        if (!r.ok && isBusy()) return { skipped: 'busy', launched: null }
        const n = (attempts.get(key) ?? 0) + 1
        attempts.set(key, n)
        nextAt.set(key, t + backoffMs(n))
        if (!r.ok) {
          warnLine(
            `[backfill] evalA 启动失败 ${course} it${cand.iter}：${r.message}` +
              `（${Math.round(backoffMs(n) / 60000)}min 后再试）`,
          )
          return { skipped: null, launched: null }
        }
        const launched: BackfillLaunch = { ...cand, course, ckpt: ck.path }
        logLine(
          `[backfill] ${course} it${cand.iter} 无云机读数` +
            `${cand.reason === 'final' ? '（收官评估点）' : `（+${OFFLINE_EVAL_GRACE_IT} 轮宽限已到）`}` +
            `——hub 侧用 LAN 集群补评（evalA，权重 ${ck.path}）`,
        )
        return { skipped: null, launched }
      }
    }
    return { skipped: null, launched: null }
  }

  return { run }
}

// ────────────────────────── 生产单例（server.ts 的 60s ticker 调它） ──────────────────────────

let singleton: OfflineEvalBackfill | null = null

/** 跑一拍补评（生产入口；绝不抛——ticker 循环不因一拍失败而断）。 */
export async function runOfflineEvalBackfill(): Promise<BackfillTick | null> {
  singleton ??= createOfflineEvalBackfill()
  try {
    return await singleton.run()
  } catch (e) {
    warn(`[backfill] 一拍失败（不炸 ticker）：${e instanceof Error ? e.message : String(e)}`)
    return null
  }
}
