/** eval-games.ts — 导出 replay：任意 in-loop eval 轮的逐局视图与确定性重放任务。 */
import { existsSync, readFileSync } from 'fs'
import path from 'path'
import { STAGES } from '../../../../src/config/stages'
import { isArenaId, resolveArenaStage } from '../../../../src/nn/arena-ladder'
import { CUSTOM_STAGE_BASE } from '../../../../src/nn/config-stage'
import { REPO_ROOT } from '../../core/paths'
import type {
  EvalGamesData,
  EvalGamesView,
  EvalReplayJobView,
  EvalReplayManifest,
  EvalRoundsView,
} from '../../web/view'
import { busy } from '../actions'
import { readEvalGames, readEvalRoundOptions } from '../iters'
import { errResp } from './route'

// ────────────────────────── 导出 replay（最新 in-loop eval 逐局视图 + 确定性重放任务） ──────────────────────────

export const REPLAY_EXPORT_BUSY_KEY = 'eval:replays'
/** 单次导出上限（每局 = 一次完整确定性重放；400 局 × ~2-5s / 4 并行 ≈ 数分钟）。 */
export const REPLAY_EXPORT_MAX_GAMES = 400

/** stage id → 人类可读关名（课程自定义关 / arena / 真实关）。
 *  注意判定顺序：isArenaId = id ≥ 1000 恒含自定义关段（2000+），自定义关必须先判。 */
function stageDisplayName(stage: number): string {
  if (stage >= CUSTOM_STAGE_BASE) return `自定义关 ${stage}`
  if (isArenaId(stage)) return resolveArenaStage(stage)?.name ?? `arena ${stage}`
  if (stage >= 0 && stage < STAGES.length) return STAGES[stage]?.name ?? `s${stage}`
  return `s${stage}`
}

function emptyEvalGamesView(course: string): EvalGamesView {
  return {
    course,
    available: false,
    iter: -1,
    wver: '',
    time: '',
    games: 0,
    wins: 0,
    winRate: null,
    clears: 0,
    outcomes: {},
    rows: [],
  }
}

/** `?iter=` 解析（§3.2 钉死）：缺省 / 空串 ⇒ undefined（最新轮，今天的行为）；
 *  非整数 ⇒ null（调用方 400；不静默当缺省——「非法」与「没选」是两件事）。 */
export function parseEvalIterParam(raw: string | null): number | undefined | null {
  if (raw === null || raw === '') return undefined
  const n = Number(raw)
  return Number.isInteger(n) ? n : null
}

/** GET /api/evalGames：一轮 in-loop eval 概要 + 逐局行（「导出 replay」弹窗数据源）。
 *  `iter` 缺省 = 最新轮；给了但该轮不存在 ⇒ `available:false`（**不回落**——回落 = 拿别的轮的读数冒充）。
 *  逐次读 eval_log.jsonl（弹窗打开时一次 + 手动刷新，不进 3s 轮询路径）。
 *
 *  数据集形态（契约面，2026-10-06 换轮架构）：
 *    - summary 行 = eval_log.jsonl 里 `event === 'eval_summary'` 且 `iter` 命中目标轮的那一行；
 *      命中不了 ⇒ null ⇒ `available:false`；
 *    - 概要与逐局行都归**该轮的 (iter, wver)**（`iters.ts::readEvalGames` 两趟扫描）；
 *    - **本接口不发可导出行**：导出清单住 `manifest.files`（与控制台 POST 受理后的产物同源）。
 *      选轮走 `GET /api/evalRounds`（summary-only，成本低），不用本接口。 */
export function buildEvalGamesView(course = '', iter?: number): EvalGamesView {
  if (!course) return emptyEvalGamesView(course)
  const trajDir = path.join(REPO_ROOT, 'tmp', course)
  if (!existsSync(path.join(trajDir, 'eval_log.jsonl'))) return emptyEvalGamesView(course)
  let data: EvalGamesData | null = null
  try {
    data = readEvalGames(trajDir, iter)
  } catch {
    data = null
  }
  if (!data) return emptyEvalGamesView(course)
  return {
    course,
    available: true,
    ...data,
    rows: data.rows.map((r) => ({ ...r, stageName: stageDisplayName(r.stage) })),
  }
}

/** GET /api/evalRounds：该课程的全部可导出 eval 轮（选择器数据源；summary-only 扫描）。
 *  与 /api/evalGames 同一份账本但**只读 summary 行**（逐局行一列不读——成本纪律见
 *  iters.ts::readEvalRoundOptions 的注释）。无 eval_log ⇒ 空列表（弹窗显示「暂无评估记录」）。 */
export function buildEvalRoundsView(course = ''): EvalRoundsView {
  if (!course) return { course, rounds: [] }
  const trajDir = path.join(REPO_ROOT, 'tmp', course)
  try {
    return { course, rounds: readEvalRoundOptions(trajDir) }
  } catch {
    return { course, rounds: [] }
  }
}

export function replayExportPaths(course: string): {
  manifest: string
  outDir: string
  log: string
  gamesFile: string
} {
  return {
    manifest: path.join(REPO_ROOT, 'tmp', course, 'replay-export.json'),
    outDir: path.join(REPO_ROOT, 'tmp', course, 'replay-export'),
    log: path.join(REPO_ROOT, 'tmp', course, 'replay-export.log'),
    gamesFile: path.join(REPO_ROOT, 'tmp', course, 'replay-export-games.json'),
  }
}

/** manifest JSON → 类型化视图（解析失败/缺字段 = null，诚实显示「无产物」）。
 *  白名单式重建（不 spread raw）：新字段必须在这里显式透传，否则视图永远看不到它
 *  （failReason 就是 2026-10-06 补的一例）。
 *
 *  契约面（2026-10-06 换轮架构）：
 *    - manifest 存在 ⇒ 可导出行从 manifest.files 读（不再读 eval_log 的 summary）；
 *    - manifest 不存在 ⇒ 该轮无可导出行（不再「找最近一个 manifest」）；
 *    - manifest.ok === false ⇒ 该轮无可导出行 + failReason 透传至视图。
 *  因此 readReplayManifest 的语义在此换轮架构下 =「本次受理后落盘的 manifest」，
 *  而不再是「最近一次导出的 manifest」（旧模式下兼容用法仍保留，用于此接口向后兼容）。 */
export function readReplayManifest(manifestPath: string): EvalReplayManifest | null {
  try {
    if (!existsSync(manifestPath)) return null
    const raw = JSON.parse(readFileSync(manifestPath, 'utf8')) as Partial<EvalReplayManifest>
    if (!raw || typeof raw !== 'object' || typeof raw.ok !== 'boolean') return null
    const out: EvalReplayManifest = {
      ok: raw.ok,
      course: String(raw.course ?? ''),
      iter: Number(raw.iter ?? -1),
      wver: String(raw.wver ?? ''),
      weightsPath: String(raw.weightsPath ?? ''),
      difficulty: String(raw.difficulty ?? ''),
      maxTicks: Number(raw.maxTicks ?? 0),
      generatedAt: String(raw.generatedAt ?? ''),
      sec: Number(raw.sec ?? 0),
      requested: Number(raw.requested ?? 0),
      files: Array.isArray(raw.files) ? raw.files : [],
      errors: Array.isArray(raw.errors) ? raw.errors : [],
      mismatches: Array.isArray(raw.mismatches) ? raw.mismatches : [],
    }
    // 非字符串 / 空串 ⇒ 缺省 undefined（弹窗降级「看日志尾」；不把噪声渲染成原因）。
    if (typeof raw.failReason === 'string' && raw.failReason) out.failReason = raw.failReason
    return out
  } catch {
    return null
  }
}

/** GET /api/evalReplayJob：导出任务态（running = busy 互斥；manifest = 本次受理后落盘的 manifest）。
 *  2026-10-06 换轮架构后，此接口的 manifest 语义 = POST /api/replayExport 受理成功后落盘的 manifest，
 *  而不再是「最近一次导出的 manifest」。
 *  因此此接口返回的 manifest 是「本次导出结果」，弹窗由此判据渲染任务态。
 *  旧弹窗用法（此接口做轮选择）已废除——轮选择走 GET /api/evalRounds。
 */
export function buildEvalReplayJobView(course = ''): EvalReplayJobView {
  const running = busy.has(REPLAY_EXPORT_BUSY_KEY)
  let manifest: EvalReplayManifest | null = null
  let logTail: string[] = []
  if (course) {
    const p = replayExportPaths(course)
    manifest = readReplayManifest(p.manifest)
    try {
      if (existsSync(p.log)) {
        logTail = readFileSync(p.log, 'utf8').split(String.fromCharCode(10)).slice(-40)
      }
    } catch {
      /* log 不可读不致命 */
    }
  }
  return { course, running, manifest, logTail }
}

/** GET /api/evalReplayFile：**单局** .replay 下载（用户裁定 2026-09-13：不打 tar.gz，
 *  每局一个文件交由浏览器/目录选择器落盘）。file 白名单 = manifest.files 精确匹配 +
 *  文件名形态校验，杜绝路径穿越。返回 null = course 缺失。 */
export async function evalReplayFileResponse(
  course: string,
  file: string,
): Promise<Response | null> {
  if (!course) return null
  if (!/^[\w.-]+\.replay$/.test(file)) return errResp('file 名非法', 400)
  const p = replayExportPaths(course)
  const manifest = readReplayManifest(p.manifest)
  // F4 闭合（串话归因，下载侧）：此处的 manifest = POST /api/replayExport 受理成功后落盘的 manifest，
  // 再无「最近一次」的回退语义。manifest 不存在 / manifest.ok === false / 文件不在 files 白名单 ⇒ 404。
  // 旧文案（最近一次导出清单）已换成本轮语义。
  if (!manifest || !manifest.ok || !manifest.files.some((f) => f.file === file)) {
    return errResp('该 .replay 不在本轮导出清单中——请重新导出', 404)
  }
  const fp = path.join(p.outDir, file)
  if (!existsSync(fp)) return errResp('导出文件已清理——请重新导出', 404)
  return new Response(new Uint8Array(readFileSync(fp)), {
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Disposition': `attachment; filename="${file}"`,
    },
  })
}
