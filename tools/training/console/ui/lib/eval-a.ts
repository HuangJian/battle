/** eval-a.ts — 课程 A 层评估启动（evalA）客户端辅助：ckpt 发现 + 动作封装。
 *  与 EvalBoard B 层（evalProbeRun）无关。 */

import { fetchEvalCkpts, postAction } from './api-client'
import type { EvalCkptFile } from '../../../ui/view'

export type { EvalCkptFile }

/** 按 iter 从 ckpt 列表解析权重路径（归档优先，活动权重兜底）。 */
export function ckptForIter(files: EvalCkptFile[], iter: number): string | null {
  const hit = files.find((f) => f.iter === iter && f.leg !== '(active)')
  if (hit) return hit.path
  const active = files.find((f) => f.iter === iter)
  return active?.path ?? null
}

/** 拉取课程 ckpt 列表（失败返回空数组）。 */
export async function loadCourseCkpts(course: string): Promise<EvalCkptFile[]> {
  if (!course) return []
  try {
    const v = await fetchEvalCkpts(course)
    return v.files
  } catch {
    return []
  }
}

/** 启动 evalA：课程设计评估（该 iter 权重 × 固定语料 → eval_log）。 */
export async function startEvalA(
  course: string,
  iter: number,
  ckpt: string,
): Promise<{ ok: boolean; message: string }> {
  const r = await postAction('evalA', {
    course,
    ckpt,
    iter,
    requester: 'metrics-ui',
  })
  return {
    ok: r.ok,
    message: r.ok ? `${r.message}（完成后自动回填）` : r.message,
  }
}
