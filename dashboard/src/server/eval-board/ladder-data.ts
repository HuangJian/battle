/** ladder-data.ts — runner 心跳读取与阶梯正本 / 工作副本（god 基线覆盖）。 */
import { existsSync, readFileSync } from 'fs'
import path from 'path'
import { EVALBOARD_DATA_DIR, LADDER_CANON_PATH } from '../../core/paths'
import { type LadderRung, markProvisional } from '../../evalboard/ladder'
import type { EvalBoardView } from '../../web/view'

export function readRunnerState(): EvalBoardView['runnerState'] {
  const p = path.join(evalDataRoot(), 'runner_state.json')
  try {
    if (!existsSync(p)) return null
    const j = JSON.parse(readFileSync(p, 'utf-8')) as Record<string, unknown>
    const num = (v: unknown): number | null => (Number.isFinite(Number(v)) ? Number(v) : null)
    return {
      windowOpen: j.window_open === true,
      updatedTs: Number(j.updated_ts) || 0,
      batchId: typeof j.batch_id === 'string' ? j.batch_id : null,
      unitIdx: num(j.unit_idx),
      unitOf: num(j.unit_of),
      rung: typeof j.rung === 'string' ? j.rung : null,
      remainingUnits: Number(j.remaining_units) || 0,
      lastWindowClosedTs: num(j.last_window_closed_ts),
      engineEpoch: typeof j.engine_epoch === 'string' ? j.engine_epoch : '',
    }
  } catch {
    return null
  }
}

export function evalDataRoot(): string {
  return process.env.EVALBOARD_DATA ?? EVALBOARD_DATA_DIR
}

/** 入库阶梯正本（含可执行载荷；god 为空 = 基线未跑）。 */
export function trackedLadder(): LadderRung[] {
  const doc = JSON.parse(readFileSync(LADDER_CANON_PATH, 'utf-8')) as { rungs: LadderRung[] }
  return doc.rungs
}

/** 工作副本 god 覆盖（data/ladder.json；缺失则只用正本）。 */
export function ladderWithGod(): LadderRung[] {
  const base = trackedLadder()
  const work = path.join(evalDataRoot(), 'ladder.json')
  try {
    if (!existsSync(work)) return base
    const doc = JSON.parse(readFileSync(work, 'utf-8')) as { rungs: LadderRung[] }
    const godById = new Map(doc.rungs.map((r) => [r.id, r.god]))
    return markProvisional(
      base.map((r) => (godById.has(r.id) ? { ...r, god: godById.get(r.id)! } : r)),
    )
  } catch {
    return base
  }
}
