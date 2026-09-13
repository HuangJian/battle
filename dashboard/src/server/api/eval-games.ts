/** eval-games.ts — 导出 replay：最新 in-loop eval 逐局视图与确定性重放任务。 */
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
} from '../../web/view'
import { busy } from '../actions'
import { readLatestEvalGames } from '../iters'
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

/** GET /api/evalGames：最新 in-loop eval 概要 + 逐局行（「导出 replay」弹窗数据源）。
 *  逐次读 eval_log.jsonl（弹窗打开时一次 + 手动刷新，不进 3s 轮询路径）。 */
export function buildEvalGamesView(course = ''): EvalGamesView {
  if (!course) return emptyEvalGamesView(course)
  const trajDir = path.join(REPO_ROOT, 'tmp', course)
  if (!existsSync(path.join(trajDir, 'eval_log.jsonl'))) return emptyEvalGamesView(course)
  let data: EvalGamesData | null = null
  try {
    data = readLatestEvalGames(trajDir)
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

/** manifest JSON → 类型化视图（解析失败/缺字段 = null，诚实显示「无产物」）。 */
function readReplayManifest(manifestPath: string): EvalReplayManifest | null {
  try {
    if (!existsSync(manifestPath)) return null
    const raw = JSON.parse(readFileSync(manifestPath, 'utf8')) as Partial<EvalReplayManifest>
    if (!raw || typeof raw !== 'object' || typeof raw.ok !== 'boolean') return null
    return {
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
  } catch {
    return null
  }
}

/** GET /api/evalReplayJob：导出任务态（running = busy 互斥；manifest = 上一轮产物）。 */
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
  if (!manifest || !manifest.files.some((f) => f.file === file)) {
    return errResp('该 .replay 不在最近一次导出清单中——先执行一次导出', 404)
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
