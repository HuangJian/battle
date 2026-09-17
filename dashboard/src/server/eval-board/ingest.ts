/** ingest.ts — W1 read-through 入账：引擎身份、课程 ↔ rung 映射、eval_log 行入库。 */
import { existsSync, readFileSync } from 'fs'
import path from 'path'
import { REPO_ROOT as EVAL_REPO_ROOT } from '../../../../tools/agent/codehash-files'
import { REPO_ROOT } from '../../core/paths'
import { computeEngineEpoch, gitCommit } from '../../evalboard/engine'
import { type IngestCtx, ingestRows, type RawEvalRow } from '../../evalboard/ingest'
import type { EvalGameRow } from '../../evalboard/store'
import { evalDataRoot, trackedLadder } from './ladder-data'

// ────────────────────────── W1 read-through 入账 ──────────────────────────

let engineMemo: EvalGameRow['engine'] | null = null
function engineOfConsole(): EvalGameRow['engine'] {
  if (!engineMemo) {
    // 惰性一次：tree walk 秒级，memo 后零成本。engine_epoch = sha256(codeHash)[0:16]，
    // dist_codehash 与节点门同值（2026-09-17 统一事实来源，诊断一眼可比）。
    try {
      const { engine_epoch, codeHash } = computeEngineEpoch()
      engineMemo = { git_commit: gitCommit(EVAL_REPO_ROOT), dist_codehash: codeHash, engine_epoch }
    } catch {
      engineMemo = { git_commit: 'unknown', dist_codehash: '', engine_epoch: 'unknown' }
    }
  }
  return engineMemo
}

let rungIdsMemo: Set<string> | null = null
function ladderRungIds(): Set<string> {
  if (!rungIdsMemo) {
    try {
      rungIdsMemo = new Set(trackedLadder().map((r) => r.id))
    } catch {
      rungIdsMemo = new Set()
    }
  }
  return rungIdsMemo
}

/**
 * 课程 → 阶梯 rung id（`c<count>l<lives>`）。
 *
 * A 层 eval 行的 `stage` 是课程自定义关 id（如 2000），与阶梯 rung id（`c4l1`）不同名；
 * 若按 `stage-<id>` 落账，这些行永远 join 不上阶梯表（console 侧 `x.rung === r.id`），
 * 面板恒空。课程几何（count/lives）与 rung 一一对应，故按 count/lives 反查。
 * 无对应 rung 的课程（如 c5-margin）返回 null → 调用方回退 `stage-<id>`。
 */
export interface CourseRungMeta {
  /** 匹配到的阶梯 rung id；无对应 rung（如 c5-margin）为 null。 */
  rung: string | null
  /** 课程**实际**跑关参数 —— probe_key 的三个可比性分量必须取自这里，不能硬编码。 */
  maxTicks: number
  mapHash: string
}

/**
 * 课程 → 阶梯 rung 元信息（rung id / maxTicks / mapHash）。
 *
 * `probe_key = <rung>-<difficulty>-t<maxTicks>-<mapHash>-<seedSpace>` 是 §3.5 的
 * **可比性键**，三个分量都必须来自课程实跑参数：
 * - `maxTicks` 取自课程 jsonc（历史课程是 2400，阶梯 rung 是 12000 —— 两者不可比，
 *   硬编码会让 A 行冒充 t12000 数据，跨档相减不被断言拦截）；
 * - `mapHash` 取自阶梯 rung（原实现缺省 `mapHashOfStage` → 全部落成 `unknown`，
 *   等于把"可比性键"退化成常量）。
 */
export function courseRungMeta(course: string): CourseRungMeta | null {
  if (!course) return null
  try {
    const p = path.join(REPO_ROOT, 'nn-training', 'curricula', `${course}.jsonc`)
    if (!existsSync(p)) return null
    const text = readFileSync(p, 'utf-8').replace(/\/\/.*$/gm, '')
    const count = Number(text.match(/"count"\s*:\s*(\d+)/)?.[1])
    const lives = Number(text.match(/"lives"\s*:\s*(\d+)/)?.[1])
    const maxTicks = Number(text.match(/"max_ticks"\s*:\s*(\d+)/)?.[1])
    const id = Number.isInteger(count) && Number.isInteger(lives) ? `c${count}l${lives}` : null
    const rung = id && ladderRungIds().has(id) ? id : null
    let mapHash = 'unknown'
    if (rung) {
      mapHash = trackedLadder().find((r) => r.id === rung)?.mapHash ?? 'unknown'
    }
    return { rung, maxTicks: Number.isInteger(maxTicks) ? maxTicks : 12000, mapHash }
  } catch {
    return null
  }
}

/** 便捷包装：只要 rung id（无对应 rung 返回 null）。 */
export function courseRungId(course: string): string | null {
  return courseRungMeta(course)?.rung ?? null
}

/** 课程 eval_log.jsonl（训练落盘处，tmp 缓冲）→ EvalStore。返回新入账行数。 */
export function ingestCourseEvalLog(course: string): number {
  if (!course) return 0
  // 真实布局优先；legacy `traj/` 候选保留兼容（两者都不在则 0）。
  const candidates = [
    path.join(REPO_ROOT, 'tmp', course, 'eval_log.jsonl'),
    path.join(REPO_ROOT, 'tmp', course, 'traj', 'eval_log.jsonl'),
  ]
  const evalLog = candidates.find((p) => existsSync(p))
  if (!evalLog) return 0
  const raws: RawEvalRow[] = []
  for (const line of readFileSync(evalLog, 'utf-8').split('\n')) {
    if (!line.trim()) continue
    try {
      const r = JSON.parse(line) as RawEvalRow
      if (r.event === 'eval') raws.push(r)
    } catch {
      /* 坏行跳过 */
    }
  }
  if (raws.length === 0) return 0
  // 课程 → 阶梯 rung 元信息（A 行 stage 是自定义关 id，必须映射；maxTicks/mapHash
  // 取自课程实跑参数，不得硬编码，否则 probe_key 这个可比性键失真）。
  const meta = courseRungMeta(course)
  const rung = meta?.rung ?? null
  const rungOfStage = (s: string | number): string => rung ?? `stage-${s}`
  const ctx: IngestCtx = {
    run_id: course,
    course,
    batch_id: `A-${course}`,
    batch_of: 1,
    rungOfStage,
    engine: engineOfConsole(),
    source: 'A',
    ckpt_path: '',
    ckpt_sha16: '',
    init_sha16: '',
    difficulty: 'hard',
    maxTicks: meta?.maxTicks ?? 12000,
    mapHashOfStage: () => meta?.mapHash ?? 'unknown',
  }
  // wver/iter 逐行透传：ingestEvalRow 从 raw 取 iter/wver（A 行自带）。
  const { appended } = ingestRows(evalDataRoot(), raws, ctx)
  return appended
}
