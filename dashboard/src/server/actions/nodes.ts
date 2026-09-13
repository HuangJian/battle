/** nodes.ts — 节点编辑：启停与并发配额回写 rl-config.json。 */
import { loadConfig, saveConfig } from '../../core/config'
import { rlConfigSmoke } from '../../stack/smoke'
import { ActionError, ActionResult, done, guard, release } from './result'

// ────────────────────────── 节点编辑（回写 rl-config.json） ──────────────────────────

/** 启用/停用 rollout 节点。 */
export async function setNodeEnabled(id: string, enabled: boolean): Promise<ActionResult> {
  guard(`node:${id}`)
  try {
    const cfg = loadConfig()
    const n = cfg.nodes.find((x) => x.id === id)
    if (!n) throw new ActionError(`节点不存在: ${id}`)
    n.enabled = enabled
    saveConfig(cfg)
    return done(true, `节点 ${id} 已${enabled ? '启用' : '停用'}（rl-config.json 已回写）`)
  } finally {
    release(`node:${id}`)
  }
}

/** 修改节点并行采集数（1-64）。 */
export async function setNodeConcurrency(id: string, concurrency: number): Promise<ActionResult> {
  guard(`node:${id}`)
  try {
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 64)
      throw new ActionError(`并发数需为 1-64 的整数，收到: ${concurrency}`)
    const cfg = loadConfig()
    const n = cfg.nodes.find((x) => x.id === id)
    if (!n) throw new ActionError(`节点不存在: ${id}`)
    n.concurrency = concurrency
    saveConfig(cfg)
    const smoke = rlConfigSmoke(cfg)
    return done(
      smoke.passed,
      `节点 ${id} 并行数 = ${concurrency}（rl-config.json 已回写）`,
      smoke.passed ? undefined : [smoke.detail ?? ''],
    )
  } finally {
    release(`node:${id}`)
  }
}
