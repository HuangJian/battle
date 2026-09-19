/** batches.ts — 批次台账（plan/rl-eval-system.md §3.7，同时是触发队列）。
 *
 * console 的 POST 只 append 一行 `status=pending`（§6.7）；主路径 runner 下窗
 * 轮询拾取（天然继承断点续跑与台账，console 零新协议）。
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import path from 'path'

export type BatchStatus = 'pending' | 'running' | 'done' | 'aborted'
export type BatchTrigger = 'main' | 'standalone' | 'auto-ladder' | 'verdict'
/** 批次类型：`ladder`（默认，阶梯语料由 ladder.json 驱动）/ `verdict`（判决语料，P2）。 */
export type BatchKind = 'ladder' | 'verdict'

/** 判决批的权重项（多 ckpt 同批同种子配对）。 */
export interface BatchCkpt {
  label?: string
  path: string
}

export interface EvalBatch {
  batch_id: string
  /** 缺省 = 'ladder'（旧台账行无此键 ⇒ 不得把它当必填）。 */
  kind?: BatchKind
  /** 判决批：语料 id（corpora.json）。 */
  corpus?: string
  /** 判决批：N 个权重（顺序即配对语义）。 */
  ckpts?: BatchCkpt[]
  course: string
  rung_from: string
  ckpt: string
  requester: string
  created_ts: string
  status: BatchStatus
  units: { of: number; done: number[] }
  k_seq: number
  window_seq: number
  trigger: BatchTrigger
  iter: number
  node_dist: Record<string, number>
  elapsed_sec: number | null
  /** B/C 执行语义（maybe_dispatch_batch 读）：policy god = C 层基线（权重无关）。 */
  policy?: 'nn' | 'god'
  /** 阶梯位置（门控推进的 ladder_pos，与课程同生命周期，§4.3/§11-3 倾向 registry——
   * v1 随台账，registry 迁移待 §11-3 落定）。 */
  ladder_pos?: number
  init_sha16?: string
  /** 回填精度（P4）：只跑规划单元中 rung 在此表的（默认全跑，§4.3 门控语料）。 */
  only_rungs?: string[]
}

export function batchesPath(dataRoot: string): string {
  return path.join(dataRoot, 'batches.jsonl')
}

/** 读全部批次（坏行跳过）。 */
export function loadBatches(dataRoot: string): EvalBatch[] {
  const p = batchesPath(dataRoot)
  if (!existsSync(p)) return []
  const out: EvalBatch[] = []
  for (const line of readFileSync(p, 'utf-8').split('\n')) {
    if (!line.trim()) continue
    try {
      out.push(JSON.parse(line) as EvalBatch)
    } catch {
      /* 坏行跳过 */
    }
  }
  return out
}

/**
 * 入队（§6.7）：console POST 唯一动作。重跑 = 新 batch_id（§3.4 不可变）。
 * 同 course+rung+ckpt 的 pending 批次已存在则直接返回它（防重复入队）。
 */
export function enqueueBatch(
  dataRoot: string,
  spec: Pick<EvalBatch, 'course' | 'rung_from' | 'ckpt' | 'requester' | 'trigger' | 'iter'> &
    Partial<
      Pick<
        EvalBatch,
        'units' | 'k_seq' | 'window_seq' | 'policy' | 'ladder_pos' | 'init_sha16' | 'only_rungs'
      >
    >,
): EvalBatch {
  mkdirSync(dataRoot, { recursive: true })
  const existing = loadBatches(dataRoot).find(
    (b) =>
      b.status === 'pending' &&
      b.course === spec.course &&
      b.rung_from === spec.rung_from &&
      b.ckpt === spec.ckpt,
  )
  if (existing) return existing
  const now = new Date().toISOString()
  const batch: EvalBatch = {
    batch_id: `b-${now.replace(/[-:.]/g, '').slice(0, 15)}-${Math.floor(Math.random() * 0xffff)
      .toString(16)
      .padStart(4, '0')}`,
    course: spec.course,
    rung_from: spec.rung_from,
    ckpt: spec.ckpt,
    requester: spec.requester,
    created_ts: now,
    status: 'pending',
    units: spec.units ?? { of: 2, done: [] },
    k_seq: spec.k_seq ?? 0,
    window_seq: spec.window_seq ?? 0,
    trigger: spec.trigger,
    iter: spec.iter,
    node_dist: {},
    elapsed_sec: null,
    ...(spec.policy ? { policy: spec.policy } : {}),
    ...(spec.ladder_pos !== undefined ? { ladder_pos: spec.ladder_pos } : {}),
    ...(spec.init_sha16 ? { init_sha16: spec.init_sha16 } : {}),
    ...(spec.only_rungs ? { only_rungs: spec.only_rungs } : {}),
  }
  appendFileSync(batchesPath(dataRoot), `${JSON.stringify(batch)}\n`, 'utf-8')
  return batch
}

/**
 * 判决批入队（P2）：语料 id + N 个 ckpt。与 `enqueueBatch` 同台账、同去重纪律
 * （同语料同 ckpt 序列的 pending 批已存在则返回它，不重复建批）。
 * 台账行的 `course`/`rung_from`/`ckpt` 置空串：判决批的身份是 `corpus`+`ckpts`，
 * 不要用假 course 去骗旧读方的键（键空间分离靠 `kind` 判别）。
 */
export function enqueueVerdictBatch(
  dataRoot: string,
  spec: {
    corpus: string
    ckpts: BatchCkpt[]
    requester: string
    iter: number
    policy?: 'nn' | 'god'
    init_sha16?: string
    only_rungs?: string[]
  },
): EvalBatch {
  mkdirSync(dataRoot, { recursive: true })
  const labels = spec.ckpts.map((c) => c.label || c.path).join(',')
  const existing = loadBatches(dataRoot).find(
    (b) =>
      b.status === 'pending' &&
      b.kind === 'verdict' &&
      b.corpus === spec.corpus &&
      (b.ckpts ?? []).map((c) => c.label || c.path).join(',') === labels,
  )
  if (existing) return existing
  const now = new Date().toISOString()
  const batch: EvalBatch = {
    batch_id: `b-${now.replace(/[-:.]/g, '').slice(0, 15)}-${Math.floor(Math.random() * 0xffff)
      .toString(16)
      .padStart(4, '0')}`,
    kind: 'verdict',
    corpus: spec.corpus,
    ckpts: spec.ckpts,
    course: '',
    rung_from: '',
    ckpt: '',
    requester: spec.requester,
    created_ts: now,
    status: 'pending',
    // of 由 Python 侧展开后回写（= ckpts × 关卡数）。
    units: { of: 0, done: [] },
    k_seq: 0,
    window_seq: 0,
    trigger: 'verdict',
    iter: spec.iter,
    node_dist: {},
    elapsed_sec: null,
    ...(spec.policy ? { policy: spec.policy } : {}),
    ...(spec.init_sha16 ? { init_sha16: spec.init_sha16 } : {}),
    ...(spec.only_rungs ? { only_rungs: spec.only_rungs } : {}),
  }
  appendFileSync(batchesPath(dataRoot), `${JSON.stringify(batch)}\n`, 'utf-8')
  return batch
}

/** 取最早的 pending 批次并标 running（runner 下窗轮询拾取；断点续跑看 units.done）。 */
export function claimPending(dataRoot: string): EvalBatch | null {
  const batches = loadBatches(dataRoot)
  const target = batches.find((b) => b.status === 'pending')
  if (!target) return null
  target.status = 'running'
  rewriteBatches(dataRoot, batches)
  return target
}

/** 更新一批次（状态/进度/耗时；原地重写台账——台账是队列不是账本，§3.4 不可变只约束 games/）。 */
export function updateBatch(
  dataRoot: string,
  batch_id: string,
  patch: Partial<Pick<EvalBatch, 'status' | 'units' | 'node_dist' | 'elapsed_sec'>>,
): EvalBatch | null {
  const batches = loadBatches(dataRoot)
  const target = batches.find((b) => b.batch_id === batch_id)
  if (!target) return null
  Object.assign(target, patch)
  rewriteBatches(dataRoot, batches)
  return target
}

function rewriteBatches(dataRoot: string, batches: EvalBatch[]): void {
  mkdirSync(dataRoot, { recursive: true })
  writeFileSync(
    batchesPath(dataRoot),
    batches.map((b) => JSON.stringify(b)).join('\n') + (batches.length > 0 ? '\n' : ''),
    'utf-8',
  )
}
