/** nodes.ts — 节点编辑：启停、并发配额与批量升级。 */
import { readFileSync } from 'fs'
import path from 'path'
import { loadConfig, saveConfig } from '../../core/config'
import { REPO_ROOT } from '../../core/paths'
import { rlConfigSmoke } from '../../stack/smoke'
import { computeCodeHash } from '../../../../tools/agent/codehash-files'
import { requestNodeUpgradesAsync, upgradeLogLines } from '../../../../tools/lib/node-upgrade'
import { ActionError, ActionResult, done, guard, release } from './result'

/** 远控 pull 要用的分支 = 当前工作分支（取不到 ⇒ 空，agent 侧只重启不拉代码）。
 *  直接读 `.git/HEAD` 而不是 spawn git：服务端禁同步子进程（事件循环是共享资源）。 */
function currentGitBranch(): string {
  try {
    const head = readFileSync(path.join(REPO_ROOT, '.git', 'HEAD'), 'utf8').trim()
    return /^ref:\s*refs\/heads\/(.+)$/.exec(head)?.[1] ?? ''
  } catch {
    return ''
  }
}

/** 从传入 id 里挑出**可批量升级**的节点：去重后只留配置里存在、已启用、非 push 的采样节点。
 *  抽成纯函数是为了把「谁能被升级」这条判据钉住，不用真发重启请求就能测。 */
export function selectUpgradeTargets<
  T extends { id: string; enabled?: boolean; gpu_push?: unknown; offline_push?: unknown },
>(nodes: readonly T[], ids: readonly string[]): T[] {
  const wanted = new Set(ids)
  return nodes.filter((n) => wanted.has(n.id) && n.enabled && !n.gpu_push && !n.offline_push)
}

/** 「全部升级」：把前端按 ping 筛出的在线采样节点统一更新代码并重启。
 *
 *  ★ 走显式节点模式（不传 cfgPath ⇒ CLI 不扫全量配置），只动传进来的 id；
 *  `pingHash` 故意留空 ⇒ 跳过 CLI 的 `current` 短路，让「已是当前版本」的节点也重启；
 *  `dirty: []` ⇒ 本地工作区脏不再挡路（codehash 已以 self 节点部署版本为准，2026-11-21）。
 *  远端 agent 自己的脏树 / grace / 跨代去重护栏仍然保留——降的是本地这道过时门。 */
export async function upgradeOnlineNodes(ids: string[]): Promise<ActionResult> {
  guard('node:upgrade-all')
  try {
    if (!Array.isArray(ids) || ids.length === 0) throw new ActionError('没有可升级的在线采样节点')
    const cfg = loadConfig()
    const targets = selectUpgradeTargets(cfg.nodes, ids)
    if (targets.length === 0) throw new ActionError('选中的节点已停用或是 push 节点，不能批量升级')

    const branch = currentGitBranch()
    // 异步下发：CLI 逐节点发请求可能要几分钟，同步版会堵死整个 dashboard 事件循环。
    const out = await requestNodeUpgradesAsync({
      repoRoot: REPO_ROOT,
      expectedHash: computeCodeHash(),
      branch,
      dirty: [],
      dryRun: false,
      nodes: targets.map((n) => ({
        id: n.id,
        url: n.url,
        authKey: n.authKey,
        pingHash: '',
      })),
    })
    const sent = out.results.filter((r) => r.reason === 'restart-requested').length
    const skipped = out.results.length - sent
    const branchNote = branch ? `分支 ${branch}` : '未取到分支（节点只重启，不拉代码）'
    return done(
      out.ok,
      out.ok
        ? `已下发 ${sent} / ${targets.length} 个节点（跳过 ${skipped}：冷却窗内 / 守护拒绝 / 不可达）· ${branchNote}`
        : `升级指令下发失败：${out.error ?? '未知原因'}`,
      upgradeLogLines('[upgrade-all]', out),
    )
  } finally {
    release('node:upgrade-all')
  }
}

// ────────────────────────── 节点编辑（回写 rl-config.json） ──────────────────────────

/** 启用/停用 rollout / push 节点。 */
export async function setNodeEnabled(id: string, enabled: boolean): Promise<ActionResult> {
  guard(`node:${id}`)
  try {
    const cfg = loadConfig()
    const n = cfg.nodes.find((x) => x.id === id)
    if (!n) throw new ActionError(`节点不存在: ${id}`)
    n.enabled = enabled
    saveConfig(cfg)
    // push 类节点（gpu_push / offline_push）停用后 hub 会**立刻**停止向它派单/拉结果，并把
    // 它抢占的课程释放回池子（2026-10-24 用户口径，hub/offline_push.py::_worker_registered）。
    // 这句只是把后果说出来 —— 判据在 hub 侧，控制台不重复实现。
    const push = Boolean(n.gpu_push || n.offline_push)
    const tail = push
      ? `；hub 将${enabled ? '恢复' : '停止'}向它派单/拉取结果${enabled ? '' : '并释放它抢占的课程'}`
      : ''
    return done(true, `节点 ${id} 已${enabled ? '启用' : '停用'}（rl-config.json 已回写${tail}）`)
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
