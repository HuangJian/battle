/** phase.ts — 日志尾阶段解析（rollout / ppo / idle）与取值辅助。 */
export type TrainingPhase = 'rollout' | 'ppo' | 'idle'

export interface PhaseInfo {
  phase: TrainingPhase
  /** 当前阶段开始时刻（ms）；idle 时为 null。 */
  sinceMs: number | null
  /** 当前迭代序号；idle 时为 null。 */
  iter: number | null
}

/**
 * 从训练循环日志尾解析当前阶段。
 * 规则：日志最后一行含 "=== iteration N/M ===" → rollout（本轮刚开始）；
 *        含 "rollout itN:" 之后 → PPO 阶段（含 push/remote ppo/ppo itN 等）；
 *        其它 / 无日志 → idle。
 * 耗时 = 当前时间 - 日志时间戳（仅 rollout/ppo 有效）。
 */
export function parsePhaseFromLog(tail: string[]): PhaseInfo {
  if (tail.length === 0) return { phase: 'idle', sinceMs: null, iter: null }
  const last = tail[tail.length - 1]!
  // 日志格式：[HH:MM:SS] [run_rl] ...
  const tsMatch = last.match(/^\[(\d{2}):(\d{2}):(\d{2})\]/)
  let sinceMs: number | null = null
  if (tsMatch) {
    const h = Number(tsMatch[1])
    const m = Number(tsMatch[2])
    const s = Number(tsMatch[3])
    const d = new Date()
    d.setHours(h, m, s, 0)
    sinceMs = d.getTime()
  }
  const iterMatch = last.match(/=== iteration (\d+)\//)
  if (iterMatch) {
    return { phase: 'rollout', sinceMs, iter: Number(iterMatch[1]) }
  }
  const rolloutMatch = last.match(/rollout it(\d+):/)
  if (rolloutMatch) {
    return { phase: 'ppo', sinceMs, iter: Number(rolloutMatch[1]) }
  }
  // §380：发布 PPO job 后等待云 worker 期间，日志尾常停在 published job 行——
  // wait_job 不打印，若不加匹配阶段灯会退成 idle（数据明明在等 PPO）。
  const pubMatch = last.match(/published job \S+ it(\d+)/)
  if (pubMatch) {
    return { phase: 'ppo', sinceMs, iter: Number(pubMatch[1]) }
  }
  // push/remote ppo/ppo itN/weights archived 等 → 仍在 PPO 阶段
  if (/\[run_rl\] (push:|remote ppo|ppo it\d+|weights archived|export)/.test(last)) {
    const itMatch = last.match(/it(\d+)/)
    return { phase: 'ppo', sinceMs, iter: itMatch ? Number(itMatch[1]) : null }
  }
  return { phase: 'idle', sinceMs: null, iter: null }
}

export function lastFinite(vals: number[]): number | null {
  const f = vals.filter(Number.isFinite)
  return f.length > 0 ? f[f.length - 1]! : null
}

export function fmtValue(v: number | null): string {
  if (v === null) return '—'
  return Math.abs(v) < 10 ? v.toFixed(3) : v.toFixed(1)
}
