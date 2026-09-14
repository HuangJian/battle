/** labels.ts — 组件标签、日志尾读取与 run_rl 单实例锁持有人查询。 */
import { existsSync, readFileSync } from 'fs'
import { pidAlive } from '../../core/net'
import { lockPathFor, validateCourseName } from '../../core/slots'
import type { Component } from '../../core/types'

// ────────────────────────── 组件标签与就绪谓词 ──────────────────────────

export const COMPONENT_LABELS: Record<Component, string> = {
  selfNode: 'self-node (采集节点)',
  hubServer: 'hub-server (作业中枢)',
  cloudflared: 'cloudflared (入站隧道)',
  localWorker: 'local-worker (本机 PPO worker)',
  trainingLoop: 'TrainingLoop (trainer)',
  workerServe: 'worker_server (本机伪 GPU 节点)',
}

export function tailLines(p: string, n = 8): string[] {
  try {
    return readFileSync(p, 'utf-8')
      .split('\n')
      .filter(Boolean)
      .slice(-n)
      .map((l) => l.slice(0, 240))
  } catch {
    return []
  }
}

/** run_rl 单实例锁持有人（与 train.ts preflight 同语义；null = 无存活持有人/无锁）。
 *  锁按课程实例化（plan §3.1）：课程名由调用方给，锁路径唯一来源 slots.ts::lockPathFor。 */
export function runRlLockHolder(course = ''): number | null {
  const lockPath = lockPathFor(validateCourseName(course), 'run_rl')
  if (!existsSync(lockPath)) return null
  try {
    const holder = Number.parseInt((readFileSync(lockPath, 'utf-8').split('|')[0] ?? '').trim(), 10)
    return Number.isInteger(holder) && holder > 0 && pidAlive(holder) ? holder : null
  } catch {
    return null
  }
}
