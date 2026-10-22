/** store.ts — EvalBoard 数据契约（plan/rl-eval-system.md §3）。
 *
 * 逐局记录 schema v1 + 唯一写入路径 `appendRow`（幂等）+ 可比性断言
 * `assertComparable`（报错不静默）+ 字段覆盖率 `coverageReport`。
 *
 * 落盘：`dashboard/data/evalboard/games/YYYY-MM.jsonl`（月分区，append-only，
 * 只追加、不可变；重跑 = 新 batch_id，旧记录不删）。内存零 gameplay 状态——
 * 本模块是纯账本 IO，无 Simulation 依赖。
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync } from 'fs'
import path from 'path'

// ────────────────────────── §2.6 / §3.2 常量 ──────────────────────────

/** schema 版本（§3.2）。 */
export const EVAL_SCHEMA = 1
/** 逐局行 metrics 口径版本（哨兵 S8 校验用）。 */
export const EVAL_METRICS_VERSION = 1
/** B/C 层（梯段批 / 判据批）段基址（§2.2/§2.3）：段 = 100 个连续 seed。
 *
 *  跨语言镜像 = `nn-training/trainer/batch_plan.py::EVAL_SEED0`（同值；改一侧必须同步另一侧，
 *  否则 TS 侧规划出来的批与 Python 侧执行用的批跑的不是同一批局）。
 *  ⚠ 这一层**不在**本次「A 层迁池」的范围内：B/C 语料有自己的历史读数与段语义，不动。 */
export const EVAL_SEED0 = 860001
/** A 层（in-loop eval）语料池基址（2026-10-22）：**训练池外**的固定池。
 *
 *  = `nn-training/worker/eval_track.py::EVAL_SEEDS_BASE`（跨语言常量，改一侧必须同步另一侧）。
 *  取值条件：`2**30 < base < 2**31`——rollout 抽签在 [1, 2**30) ⇒ 与训练种子永不相交；
 *  上界 2**31 给 32 位有符号假设留安全带（仿真侧 RNG 是 `seed >>> 0` 的 mulberry32）。
 *  池**不含 it** ⇒ 每轮、每门同 level 的课评同一批 (stage,seed) ⇒ it 之间/课程之间逐点可比。 */
export const A_EVAL_SEED0 = 2000000001
/** A 层池长（= Python `EVAL_SEEDS` 长度 200）。 */
export const A_EVAL_SEED_LEN = 200
/** 每段局数（§2.3）。 */
export const SEGMENT_LEN = 100
/** 段数（§2.3：16 段稀释 winner's curse）。 */
export const SEGMENT_COUNT = 16
/** 单批局数/关（D2）。 */
export const BATCH_GAMES = 100
/** 累积窗批数（§2.3：连续 4 批 = 400 局，窗是判定主体）。 */
export const WINDOW_BATCHES = 4
/** 窗局数。 */
export const WINDOW_GAMES = BATCH_GAMES * WINDOW_BATCHES

/** 数据层（§2.1）。 */
export type EvalSource = 'A' | 'B' | 'C'
/** 种子空间（§2.2：禁止跨界相减）。`eval2g` = A 层现役池外固定池，`eval860k` = B/C 层段 0
 *  （也是旧 A 层读数所在的空间——换池前后分属两个空间，不得相减）。 */
export type SeedSpace = 'eval2g' | 'eval860k' | 'probe0' | 'stage1_60'
/** 策略（§3.2）。 */
export type EvalPolicy = 'nn' | 'god'

// ────────────────────────── §3.2 逐局记录 schema v1 ──────────────────────────

export interface EvalGameRow {
  schema: 1
  ts: string
  source: EvalSource
  batch_id: string
  batch_unit: { idx: number; of: number }
  run_id: string
  course: string
  iter: number
  ckpt_path: string
  ckpt_sha16: string
  init_sha16: string
  wver: string
  policy: EvalPolicy
  rung: string
  probe_key: string
  seedSpace: SeedSpace
  stage_id: string
  stage_name: string
  seed: number
  seed_segment: number
  engine: { git_commit: string; dist_codehash: string; engine_epoch: string }
  // T1
  outcome: string
  win: boolean
  cleared: boolean
  ticks: number
  kills: number
  enemyTotal: number
  // T2/T3
  playerHits: number
  playerDamageTaken: number
  playerShots: number
  enemyHits: number
  powerUpsCollected: number
  stuckTicks: number
  firstKillTick: number | null
  playerDeaths: number
  cellsVisited: number
  playerLevel: number
  puSpawnBomb: number
  puSpawnTank: number
  puSpawnFreeze: number
  puSpawnShield: number
  puSpawnStar: number
  puGotBomb: number
  puGotTank: number
  puGotFreeze: number
  puGotShield: number
  score: number
  // Phase 0 逐敌种画像（T5 分敌种信用；索引 = [basic, fast, power, armor]）
  hitsByKind: number[]
  killsByKind: number[]
  exposureByKind: number[]
  firstHitKind: string | null
  firstKillKind: string | null
  killOrder: string[]
  killerKinds: (string | null)[]
  metrics_version: number
  greedy: true
  // 派发侧元数据（不进一致性断言，§3.4）
  node_id: string
  elapsedSec: number | null
  phase: string
  milestone: boolean
}

/** gameplay 字段（确定性契约 §3.4：同 ckpt+seed+stage 逐字节一致；node/elapsed/phase/ts 除外）。 */
export const GAMEPLAY_FIELDS = [
  'outcome',
  'win',
  'cleared',
  'ticks',
  'kills',
  'enemyTotal',
  'playerHits',
  'playerDamageTaken',
  'playerShots',
  'enemyHits',
  'powerUpsCollected',
  'stuckTicks',
  'firstKillTick',
  'playerDeaths',
  'cellsVisited',
  'playerLevel',
  'puSpawnBomb',
  'puSpawnTank',
  'puSpawnFreeze',
  'puSpawnShield',
  'puSpawnStar',
  'puGotBomb',
  'puGotTank',
  'puGotFreeze',
  'puGotShield',
  'score',
  // Phase 0（2026-09-19 追加）：确定性 gameplay 遥测——同 ckpt+seed+stage 双跑
  // 必须逐字节一致（§3.4）。旧资产行无这些键，调用方用 PHASE0_FIELDS 明示豁免。
  'hitsByKind',
  'killsByKind',
  'exposureByKind',
  'firstHitKind',
  'firstKillKind',
  'killOrder',
  'killerKinds',
] as const

/**
 * Phase-0 列集（= GAMEPLAY_FIELDS 尾部七列）：P0 覆盖率硬指标要求 100% 或**明示**
 * 豁免——本批次之前落盘的 `games/*.jsonl` 行没有这七列，调用方用
 * `coverageReport(rows, PHASE0_FIELDS)` 表达“旧资产豁免”，别把豁免写死在函数里。
 */
export const PHASE0_FIELDS: readonly string[] = [
  'hitsByKind',
  'killsByKind',
  'exposureByKind',
  'firstHitKind',
  'firstKillKind',
  'killOrder',
  'killerKinds',
]

// ────────────────────────── §2.2 种子/段 ──────────────────────────

/** per-(course,rung) 批次序号 → 段（§2.2 作业规约：k 自增，seg = k % 16）。 */
export function segmentOf(k: number): number {
  return ((k % SEGMENT_COUNT) + SEGMENT_COUNT) % SEGMENT_COUNT
}

/** 段 → seed0（§2.3）。 */
export function seed0OfSegment(seg: number): number {
  return EVAL_SEED0 + SEGMENT_LEN * segmentOf(seg)
}

/** seed → 所属段（A 层池优先；B/C 池次之；空间外 -1）。
 *
 *  两池不相交（A 层在 2e9 段，B/C 池在 860001 段）⇒ 同一个 seed 只会落进一个池。
 *  B/C 段的段号与当年一致 ⇒ 历史行重入幂等。 */
export function segmentOfSeed(seed: number): number {
  const a = seed - A_EVAL_SEED0
  if (a >= 0 && a < A_EVAL_SEED_LEN) return Math.floor(a / SEGMENT_LEN)
  const b = seed - EVAL_SEED0
  if (b >= 0 && b < SEGMENT_LEN * SEGMENT_COUNT) return Math.floor(b / SEGMENT_LEN)
  return -1
}

/** seed → 种子空间（A 层池 `eval2g` / B/C 段 0 `eval860k` / 其余 `probe0`；§2.2 禁止跨界相减）。
 *
 *  A 层与旧 A 层读数分属两个空间（`eval2g` vs `eval860k`）⇒ 换池前后不会被当成同一批局相减。 */
export function seedSpaceOf(seed: number): SeedSpace {
  if (seed >= A_EVAL_SEED0 && seed < A_EVAL_SEED0 + A_EVAL_SEED_LEN) return 'eval2g'
  if (seed >= EVAL_SEED0 && seed < EVAL_SEED0 + SEGMENT_LEN) return 'eval860k'
  return 'probe0'
}

/** probe_key 组装：`<rung>-<difficulty>-t<maxTicks>-<mapHash>-<seedSpace>`（§3.2）。 */
export function probeKeyOf(
  rung: string,
  difficulty: string,
  maxTicks: number,
  mapHash: string,
  seedSpace: SeedSpace,
): string {
  return `${rung}-${difficulty}-t${maxTicks}-${mapHash}-${seedSpace}`
}

// ────────────────────────── 路径 ──────────────────────────

export function gamesDir(dataRoot: string): string {
  return path.join(dataRoot, 'games')
}

/** ts → 月分区文件名（§3.6 留存策略粒度）。 */
export function monthFile(ts: string): string {
  const d = new Date(ts)
  const m = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
  return `${m}.jsonl`
}

// ────────────────────────── §3.4 去重 ──────────────────────────

/** 去重键（§3.4）：run_id / iter / wver / stage / seed。 */
export function dedupKeyOf(row: {
  run_id: string
  iter: number
  wver: string
  stage_id: string
  seed: number
}): string {
  return `${row.run_id}\u0001${row.iter}\u0001${row.wver}\u0001${row.stage_id}\u0001${row.seed}`
}

/** 载入某数据根下全部逐局行的去重键集（ingest 会话/备份对账用）。 */
export function loadDedupKeys(dataRoot: string): Set<string> {
  const out = new Set<string>()
  const dir = gamesDir(dataRoot)
  let files: string[] = []
  try {
    files = readdirSync(dir).filter((f) => f.endsWith('.jsonl'))
  } catch {
    return out
  }
  for (const f of files) {
    let text = ''
    try {
      text = readFileSync(path.join(dir, f), 'utf-8')
    } catch {
      continue
    }
    for (const line of text.split('\n')) {
      if (!line.trim()) continue
      try {
        const r = JSON.parse(line) as Partial<EvalGameRow>
        if (
          typeof r.run_id === 'string' &&
          typeof r.iter === 'number' &&
          typeof r.wver === 'string' &&
          typeof r.stage_id === 'string' &&
          typeof r.seed === 'number'
        ) {
          out.add(dedupKeyOf(r as EvalGameRow))
        }
      } catch {
        /* 坏行跳过（S8 在批次级另行拒绝） */
      }
    }
  }
  return out
}

/** 读某数据根下全部逐局行（统计/哨兵/网页用；坏行跳过）。 */
export function loadRows(dataRoot: string): EvalGameRow[] {
  const out: EvalGameRow[] = []
  const dir = gamesDir(dataRoot)
  let files: string[] = []
  try {
    files = readdirSync(dir).filter((f) => f.endsWith('.jsonl'))
  } catch {
    return out
  }
  for (const f of files) {
    let text = ''
    try {
      text = readFileSync(path.join(dir, f), 'utf-8')
    } catch {
      continue
    }
    for (const line of text.split('\n')) {
      if (!line.trim()) continue
      try {
        out.push(JSON.parse(line) as EvalGameRow)
      } catch {
        /* 坏行跳过 */
      }
    }
  }
  return out
}

// ────────────────────────── §3.4 唯一写入路径（幂等） ──────────────────────────

/** `appendRow` 结果。 */
export type AppendResult = 'appended' | 'duplicate'

/**
 * 唯一写入路径（§3.4）：幂等 append。`known` 为调用方持有的去重键集
 * （`loadDedupKeys` 一次载入，会话内复用）；命中即返回 'duplicate' 不落盘。
 * 月分区由 `row.ts` 决定。tmp 是缓冲、EvalStore 是归档——禁止的是两条写入
 * 代码路径，不是两个副本（附录 P1-1）。
 */
export function appendRow(dataRoot: string, row: EvalGameRow, known?: Set<string>): AppendResult {
  const key = dedupKeyOf(row)
  if (known?.has(key)) return 'duplicate'
  const dir = gamesDir(dataRoot)
  mkdirSync(dir, { recursive: true })
  appendFileSync(path.join(dir, monthFile(row.ts)), `${JSON.stringify(row)}\n`, 'utf-8')
  known?.add(key)
  return 'appended'
}

// ────────────────────────── §3.5 可比性断言 ──────────────────────────

export class IncomparableError extends Error {}

/**
 * 可比性断言（§3.5）：禁止相减的组合一律抛 `IncomparableError`，不静默。
 * ① 不同 probe_key；② 不同 engine_epoch（= sha256(codehash-files.txt 的 codeHash)[0:16]，
 * 2026-09-17 起与 rollout 门同源；无关提交不再让其变化）；③ 不同 seedSpace；
 * ④ 配对比较要求同 seed 集；⑤ 同窗内 seed_segment 互不相交。
 */
export function assertComparable(
  a: Pick<EvalGameRow, 'probe_key' | 'seedSpace' | 'engine'>,
  b: Pick<EvalGameRow, 'probe_key' | 'seedSpace' | 'engine'>,
  opts?: { pairedSeeds?: { a: number[]; b: number[] }; segments?: number[] },
): void {
  if (a.probe_key !== b.probe_key) {
    throw new IncomparableError(`probe_key differs: ${a.probe_key} vs ${b.probe_key}`)
  }
  if (a.seedSpace !== b.seedSpace) {
    throw new IncomparableError(
      `seedSpace differs: ${a.seedSpace} vs ${b.seedSpace} (§2.2 禁止跨界相减)`,
    )
  }
  if (a.engine.engine_epoch !== b.engine.engine_epoch) {
    throw new IncomparableError(
      `engine_epoch differs: ${a.engine.engine_epoch} vs ${b.engine.engine_epoch} (S10 引擎漂移)`,
    )
  }
  if (opts?.pairedSeeds) {
    const sa = [...opts.pairedSeeds.a].sort((x, y) => x - y)
    const sb = [...opts.pairedSeeds.b].sort((x, y) => x - y)
    const same = sa.length === sb.length && sa.every((v, i) => v === sb[i])
    if (!same) throw new IncomparableError('paired comparison requires identical seed sets (§3.5④)')
  }
  if (opts?.segments) {
    const seen = new Set<number>()
    for (const s of opts.segments) {
      if (seen.has(s))
        throw new IncomparableError(`seed_segment ${s} repeats within window (§3.5⑤)`)
      seen.add(s)
    }
  }
}

// ────────────────────────── §3.3 字段覆盖率 ──────────────────────────

/** schema v1 必备字段（GAMEPLAY_FIELDS + 身份/归属/引擎/派发元数据）。 */
export const REQUIRED_FIELDS: readonly string[] = [
  ...GAMEPLAY_FIELDS,
  'schema',
  'ts',
  'source',
  'batch_id',
  'batch_unit',
  'run_id',
  'course',
  'iter',
  'ckpt_path',
  'ckpt_sha16',
  'init_sha16',
  'wver',
  'policy',
  'rung',
  'probe_key',
  'seedSpace',
  'stage_id',
  'stage_name',
  'seed',
  'seed_segment',
  'engine',
  'metrics_version',
  'greedy',
  'node_id',
  'elapsedSec',
  'phase',
  'milestone',
]

/**
 * 覆盖率检查（P0 硬指标：schema 字段覆盖率 100% 或明示豁免清单）。
 * 返回每行缺失字段；`exempt` 为明示豁免（如旧资产行缺新字段）。
 */
export function coverageReport(
  rows: Array<Record<string, unknown>>,
  exempt: readonly string[] = [],
): Array<{ idx: number; missing: string[] }> {
  const required = REQUIRED_FIELDS.filter((f) => !exempt.includes(f))
  const out: Array<{ idx: number; missing: string[] }> = []
  rows.forEach((r, idx) => {
    const missing = required.filter((f) => r[f] === undefined)
    if (missing.length > 0) out.push({ idx, missing })
  })
  return out
}

/** 数据根是否可用（落盘目录可建即真）。
 * "EvalStore 绝不在 tmp"（§3.1）由默认数据根 `dashboard/data/evalboard`
 * 承载，不在此做路径形态审查（单测 scratch 目录也在 tmp 下）。 */
export function dataRootOk(dataRoot: string): boolean {
  try {
    mkdirSync(gamesDir(dataRoot), { recursive: true })
    return existsSync(gamesDir(dataRoot))
  } catch {
    return false
  }
}
