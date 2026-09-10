/** runner.ts — B 层批次编排·规划侧（plan/rl-eval-system.md §4.3/§6.4 纯函数部分）。
 *
 * 执行侧（集群派发/断点续跑/入账）是 Python `nn-training/rl/batch_eval.py`
 *（复用 `fetch_task(mode='eval')` / `run_local_eval_game`，不重写协议 §6.7）。
 * 本模块只做可单测的规划：每批 = 当前 rung + 下一 rung 各 100 局（同 seg 零样本
 * 前瞻）+ 每 3 批一次回归位（bc/init 钉死段 0，§2.2/K2-9）。
 *
 * iterId 命名空间（ §6.2 缓存键注意）：A 层 `{runId}.{it}ev`；B/C 批
 * `{runId}.b{batchShort}u{unitIdx}`——agent taskKey 无 policy 分量，命名空间
 * 隔离是 god/nn 不串键的唯一保证（A 层恒 nn，B/C 批内不混 policy）。
 */

import { createHash } from 'node:crypto'
import { BATCH_GAMES, seed0OfSegment, segmentOf } from './store'
import { stageJsonOf, type LadderRung } from './ladder'

/** 回归位周期（§4.3：每 3 批一次）。 */
export const REGRESSION_EVERY = 3
/** 回归位钉死段 0（§2.2 作业规约②：冻结权重 + 固定 seed = 跨时间天然配对）。 */
export const REGRESSION_SEG = 0
/** 自定义关派发 stage 号基址（与 CUSTOM_STAGE_BASE 同值，跨语言常量）。 */
export const BATCH_STAGE_BASE = 2000

export interface BatchUnit {
  unit_idx: number
  unit_of: number
  rung: string
  lives: number
  level: number
  difficulty: 'hard'
  maxTicks: number
  mapHash: string
  policy: 'nn' | 'god'
  /** 派发侧：stageJson + 指纹（agent resultCache 键分量，M1d）。 */
  stageJson: string
  stageJsonHash: string
  stageId: number
  seed0: number
  seeds: number[]
}

export interface BatchPlan {
  batch_id: string
  course: string
  /** per-(course,rung) 批次序号（§2.2：k 自增，seg = k % 16；不是全局 batch_id）。 */
  k: number
  seg: number
  units: BatchUnit[]
}

/** 100 局 seed 表（§2.3：段 = 100 个连续 seed）。 */
export function seedsOfSegment(seg: number, n: number = BATCH_GAMES): number[] {
  const seed0 = seed0OfSegment(seg)
  return Array.from({ length: n }, (_, i) => seed0 + i)
}

function unitOf(
  rung: LadderRung,
  unit_idx: number,
  unit_of: number,
  seg: number,
  policy: 'nn' | 'god',
): BatchUnit {
  const stageJson = stageJsonOf(rung)
  return {
    unit_idx,
    unit_of,
    rung: rung.id,
    lives: rung.lives,
    level: rung.level,
    difficulty: rung.difficulty,
    maxTicks: rung.max_ticks,
    mapHash: rung.mapHash,
    policy,
    stageJson,
    stageJsonHash: createHash('sha256').update(stageJson).digest('hex').slice(0, 16),
    stageId: BATCH_STAGE_BASE + rung.idx,
    seed0: seed0OfSegment(seg),
    seeds: seedsOfSegment(seg),
  }
}

/**
 * 批次规划（§4.3 门控推进语料）：
 * unit_0 = rung[ladderPos]，unit_1 = rung[ladderPos+1]（无下一关时不派），
 * 回归位（k % 3 === 2 时）= bc/init 起点权重 + 当前 rung 几何 + 段 0。
 * 每个 unit 单独占一个派发窗口（§4.3）；提交粒度 100 局/窗口，跨窗口断点续跑。
 */
export function planBatch(opts: {
  batch_id: string
  course: string
  ladder: LadderRung[]
  ladderPos: number
  k: number
  policy?: 'nn' | 'god'
}): BatchPlan {
  const { batch_id, course, ladder, ladderPos, k } = opts
  const policy = opts.policy ?? 'nn'
  const seg = segmentOf(k)
  const cur = ladder[ladderPos]
  if (!cur) throw new Error(`ladderPos ${ladderPos} 越界（阶梯 ${ladder.length} 关）`)
  const units: BatchUnit[] = []
  units.push(unitOf(cur, 0, 0, seg, policy)) // of 回填于下
  const next = ladder[ladderPos + 1]
  if (next) units.push(unitOf(next, 1, 0, seg, policy))
  if (k % REGRESSION_EVERY === REGRESSION_EVERY - 1) {
    units.push(unitOf(cur, units.length, 0, REGRESSION_SEG, policy))
  }
  const of = units.length
  for (const u of units) u.unit_of = of
  units.forEach((u, i) => (u.unit_idx = i))
  return { batch_id, course, k, seg, units }
}

/** B/C 批 iterId（命名空间隔离，见文件头）。 */
export function batchIterId(runId: string, batchShort: string, unitIdx: number): string {
  return `${runId}.b${batchShort}u${unitIdx}`
}

/** batch_id 短码（iterId 用；全局 batch_id 太长）。 */
export function batchShort(batch_id: string): string {
  return createHash('sha256').update(batch_id).digest('hex').slice(0, 8)
}
