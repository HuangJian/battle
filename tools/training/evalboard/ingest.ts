/** ingest.ts — A 层自动入账钩子（plan/rl-eval-system.md §3.4/§10.1 T0.3/T0.5）。
 *
 * 单一写入路径：`ingest()` 是唯一入口，幂等（按 run_id/iter/wver/stage/seed 去重）。
 * tmp 是缓冲、EvalStore 是归档。既接 per-tick 链路（eval_log.jsonl），也接 m1 链路
 * （eval_m1 补逐局行落盘后）；T0.4 字段贯通前的旧行缺字段由 `coverageReport` 明示，
 * 不静默补编造数据（缺失 gameplay 字段填 0/null 并进覆盖率报告）。
 */

import { appendRow, dataRootOk, loadDedupKeys, segmentOfSeed, type EvalGameRow } from './store'

/** ingest 上下文（调用方=训练进程：归属 + 引擎身份 + 批次）。 */
export interface IngestCtx {
  run_id: string
  course: string
  batch_id: string
  batch_of: number
  /** stage 数字 → rung（课程外评估用 stage 名直写）。 */
  rungOfStage: (stage: number | string) => string
  stageNameOf?: (stage: number | string) => string
  engine: EvalGameRow['engine']
  source: EvalGameRow['source']
  ckpt_path: string
  ckpt_sha16: string
  init_sha16: string
  difficulty: string
  maxTicks: number
  mapHashOfStage?: (stage: number | string) => string
}

/** eval_log.jsonl / eval_m1 逐局行的宽松形态（两链路字段并集；缺失合法）。 */
export interface RawEvalRow {
  event?: string
  iter?: number
  wver?: string
  stage?: number | string
  seed?: number
  node?: string
  outcome?: string
  win?: boolean | number
  cleared?: boolean | number
  ticks?: number
  score?: number
  kills?: number
  enemyTotal?: number
  enemyHits?: number
  hitRate?: number
  powerUpsCollected?: number
  playerDamageTaken?: number
  playerHits?: number
  playerDeaths?: number
  playerShots?: number
  playerLevel?: number
  cellsVisited?: number
  firstKillTick?: number | null
  stuckTicks?: number
  elapsedSec?: number
  policy?: string
  [k: string]: unknown
}

const toNum = (v: unknown, dft = 0): number =>
  typeof v === 'number' && Number.isFinite(v) ? v : dft
const toBool = (v: unknown): boolean => v === true || v === 1

/**
 * 单行映射（纯函数，可单测）：raw + ctx → schema v1 行。
 * T0.4 前缺失的字段填 0/null（coverageReport 会点名，不伪造）。
 */
export function ingestEvalRow(raw: RawEvalRow, ctx: IngestCtx, ts?: string): EvalGameRow {
  const stage = raw.stage ?? 0
  const seed = toNum(raw.seed, 0)
  const rung = ctx.rungOfStage(stage)
  const seedSpace = seed >= 860001 && seed <= 860100 ? ('eval860k' as const) : ('probe0' as const)
  const mapHash = ctx.mapHashOfStage?.(stage) ?? 'unknown'
  return {
    schema: 1,
    ts: ts ?? new Date().toISOString(),
    source: ctx.source,
    batch_id: ctx.batch_id,
    batch_unit: { idx: 0, of: ctx.batch_of },
    run_id: ctx.run_id,
    course: ctx.course,
    iter: toNum(raw.iter, 0),
    ckpt_path: ctx.ckpt_path,
    ckpt_sha16: ctx.ckpt_sha16,
    init_sha16: ctx.init_sha16,
    wver: typeof raw.wver === 'string' ? raw.wver : '',
    policy: raw.policy === 'god' ? 'god' : 'nn',
    rung,
    probe_key: `${rung}-${ctx.difficulty}-t${ctx.maxTicks}-${mapHash}-${seedSpace}`,
    seedSpace,
    stage_id: String(stage),
    stage_name: ctx.stageNameOf?.(stage) ?? String(stage),
    seed,
    seed_segment: segmentOfSeed(seed),
    engine: ctx.engine,
    outcome: typeof raw.outcome === 'string' ? raw.outcome : 'unknown',
    win: toBool(raw.win),
    cleared: toBool(raw.cleared),
    ticks: toNum(raw.ticks, 0),
    kills: toNum(raw.kills, 0),
    enemyTotal: toNum(raw.enemyTotal, 20),
    playerHits: toNum(raw.playerHits, 0),
    playerDamageTaken: toNum(raw.playerDamageTaken, 0),
    playerShots: toNum(raw.playerShots, 0),
    enemyHits: toNum(raw.enemyHits, 0),
    powerUpsCollected: toNum(raw.powerUpsCollected, 0),
    stuckTicks: toNum(raw.stuckTicks, 0),
    firstKillTick:
      typeof raw.firstKillTick === 'number' && Number.isFinite(raw.firstKillTick)
        ? raw.firstKillTick
        : null,
    playerDeaths: toNum(raw.playerDeaths, 0),
    cellsVisited: toNum(raw.cellsVisited, 0),
    playerLevel: toNum(raw.playerLevel, 0),
    puSpawnBomb: 0,
    puSpawnTank: 0,
    puSpawnFreeze: 0,
    puSpawnShield: 0,
    puSpawnStar: 0,
    puGotBomb: 0,
    puGotTank: 0,
    puGotFreeze: 0,
    puGotShield: 0,
    score: toNum(raw.score, 0),
    metrics_version: 1,
    greedy: true,
    node_id: typeof raw.node === 'string' ? raw.node : 'unknown',
    elapsedSec: typeof raw.elapsedSec === 'number' ? raw.elapsedSec : null,
    phase: 'rollout',
    milestone: false,
  }
}

/** 批量 ingest（幂等）：返回 {appended, duplicate}。 */
export function ingestRows(
  dataRoot: string,
  raws: RawEvalRow[],
  ctx: IngestCtx,
): { appended: number; duplicate: number } {
  if (!dataRootOk(dataRoot)) throw new Error(`EvalStore 数据根不可用: ${dataRoot}`)
  const known = loadDedupKeys(dataRoot)
  let appended = 0
  let duplicate = 0
  for (const raw of raws) {
    const r = appendRow(dataRoot, ingestEvalRow(raw, ctx), known)
    if (r === 'appended') appended++
    else duplicate++
  }
  return { appended, duplicate }
}
