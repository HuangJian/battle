/** workers.ts — GPU push worker 登记（回写 rl-config `nodes[]` + 让 hub 立刻重载）。
 *
 *  形状（2026-09-18 用户口径：「dashboard 提供 worker 登记入口，回填 rl-config.json，
 *  hub 周期 ping 检测联通」）：
 *    · **配置是唯一事实源**——登记 = upsert `nodes[]` 里那条 `gpu_push` 条目；hub 按
 *      mtime 热重载它（不必重启），本动作只在写完顺手叫 hub 立即拾取一次（best-effort）。
 *    · **探活分两层且各有其位**：登记时面板直探一次 `{url}/ping`（当场告诉操作员通不通，
 *      不通**不拦登记**——云机还没开机是完全正常的中间态），此后由 hub 的周期探活承担
 *      常态判定（面板的「hub 侧」那一列）。
 *
 *  ★ 2026-09-19：**课程指针收口逻辑整体删除**（`coursesPointingAt` / `retargetCourses` /
 *  移除时的 orphan 清理）。它们服务的是 `courses.<课>.push_node_url` —— 那个键把「哪门课钉到
 *  哪台机器」做成课程属性，与「课程任务 ↔ worker 节点正交」矛盾，本轮已从类型表与 python
 *  读面一并拿掉（残留值由 `pruneLegacyCourseKnobs` 在启动训练时清）。节点的增删改现在只动
 *  `nodes[]` 一处。
 *
 *  鉴权键写的是 rl-config 的明文（与 worker_server `--token` 同源）——与既有节点编辑
 *  （setNodeConcurrency / 启动弹窗填 endpoint）同一风险面，不引入新的暴露。
 */

import { loadConfig, saveConfig } from '../../core/config'
import { pidAlive } from '../../core/net'
import { loadRegistry } from '../../core/registry'
import type { NodeConf, RlConfig } from '../../core/types'
import { hubReloadPushWorkers, probePushWorker } from '../../stack/hub-admin'
import { normalizePushUrl } from '../../stack/push-config'
import { rlConfigSmoke } from '../../stack/smoke'
import { validWorkerId } from '../../web/view'
import { ActionError, ActionResult, done, guard, release } from './result'

/** 登记与移除共用一把锁：同一次动作序列内不让两个写盘互相覆盖（load→改→save 非原子）。 */
const WORKER_LOCK = 'worker:register'

export interface RegisterWorkerInput {
  id: string
  url: string
  authKey: string
  concurrency?: number
  /** 缺省 true（新登记默认启用）。 */
  enabled?: boolean
}

/** 让 hub 立刻重读配置（best-effort：配置已落盘，失败只是晚一拍生效）。
 *  逐个候选试，第一个成功的就够——多 hub 时没必要每个都叫醒（它们各自下一拍也会重载）。 */
async function nudgeHubReload(cfg: RlConfig): Promise<boolean> {
  const token = String(cfg.rl?.remote_token ?? '')
  const urls = new Set<string>()
  try {
    for (const ent of Object.values(loadRegistry().hubServers ?? {})) {
      if (!ent || typeof ent.pid !== 'number' || !pidAlive(ent.pid)) continue
      if (typeof ent.url === 'string' && ent.url) urls.add(ent.url.replace(/\/+$/, ''))
    }
  } catch {
    /* 账本不可读 → 不叫醒（下一拍热重载兜底） */
  }
  for (const url of urls) {
    if (await hubReloadPushWorkers(url, token)) return true
  }
  return false
}

/** 登记/更新一台 GPU push worker（upsert `nodes[]` 的 `gpu_push` 条目 + 课程指针收口）。 */
export async function registerPushWorker(input: RegisterWorkerInput): Promise<ActionResult> {
  guard(WORKER_LOCK)
  try {
    const id = String(input.id ?? '').trim()
    if (!validWorkerId(id)) {
      throw new ActionError(
        `worker id 非法: ${id || '(空)'}（只接受 1-40 位字母/数字/._-；它会进日志与 job 归属标记）`,
      )
    }
    const authKey = String(input.authKey ?? '').trim()
    if (!authKey) throw new ActionError('authKey 不能为空（worker_server --token）')
    let url: string
    try {
      url = normalizePushUrl(String(input.url ?? ''))
    } catch (e) {
      throw new ActionError(e instanceof Error ? e.message : String(e))
    }
    const conc = input.concurrency === undefined ? 1 : Number(input.concurrency)
    if (!Number.isInteger(conc) || conc < 1 || conc > 64) {
      throw new ActionError(`并发数需为 1-64 的整数，收到: ${input.concurrency}`)
    }
    const enabled = input.enabled !== false

    const cfg = loadConfig()
    const nodes: NodeConf[] = Array.isArray(cfg.nodes) ? [...cfg.nodes] : []
    const idx = nodes.findIndex((n) => n.id === id)
    if (idx >= 0) {
      const prev = nodes[idx]!
      if (!prev.gpu_push) {
        throw new ActionError(
          `id ${id} 已是 rollout 节点（非 gpu_push）——换一个 id，或先移除该节点再登记为 push worker`,
        )
      }
      nodes[idx] = { ...prev, url, authKey, concurrency: conc, enabled, gpu_push: true }
    } else {
      nodes.push({ id, url, authKey, concurrency: conc, enabled, gpu_push: true })
    }
    cfg.nodes = nodes
    saveConfig(cfg)

    const smoke = rlConfigSmoke(cfg)
    const probe = await probePushWorker(url, authKey)
    const nudged = await nudgeHubReload(cfg)
    const detail = [
      `rl-config.json 已回写（nodes[] 的 gpu_push 条目 ${id}）`,
      `hub 热重载：${nudged ? '已立即拾取' : '未叫醒（hub 下一拍自动拾取）'}`,
      smoke.passed ? '' : (smoke.detail ?? ''),
    ].filter(Boolean)
    return done(
      smoke.passed && probe.online,
      probe.online
        ? `worker ${id} 已登记（${url}，并发 ${conc}）——ping 通`
        : `worker ${id} 已登记（${url}，并发 ${conc}），但 /ping 不通` +
            (enabled
              ? '：确认 worker_server 在跑、隧道可达、authKey 与 --token 一致（hub 会持续探活）'
              : '（当前停用，不探活）'),
      detail,
    )
  } finally {
    release(WORKER_LOCK)
  }
}

/** 移除一台已登记的 push worker（只动 `nodes[]` 一处）。 */
export async function removePushWorker(id: string): Promise<ActionResult> {
  guard(WORKER_LOCK)
  try {
    const cfg = loadConfig()
    const nodes: NodeConf[] = Array.isArray(cfg.nodes) ? [...cfg.nodes] : []
    const idx = nodes.findIndex((n) => n.id === id)
    if (idx < 0) throw new ActionError(`worker 不存在: ${id}`)
    const victim = nodes[idx]!
    if (!victim.gpu_push)
      throw new ActionError(`${id} 不是 push worker（rollout 节点请用节点 pill 停用）`)
    nodes.splice(idx, 1)
    cfg.nodes = nodes
    saveConfig(cfg)
    const nudged = await nudgeHubReload(cfg)
    return done(true, `worker ${id} 已移除（rl-config.json 已回写）`, [
      '未登记即不再是候选：没有节点时执行面自动落到「等 worker 拉取」（不影响其它课）',
      `hub 热重载：${nudged ? '已立即拾取' : '未叫醒（hub 下一拍自动拾取）'}`,
    ])
  } finally {
    release(WORKER_LOCK)
  }
}

/** 手动叫醒 hub 重读 rl-config（改完配置文件、或想确认 hub 侧登记表已刷新时用）。 */
export async function reloadPushWorkers(): Promise<ActionResult> {
  guard(WORKER_LOCK)
  try {
    const cfg = loadConfig()
    const ok = await nudgeHubReload(cfg)
    if (!ok) {
      return done(
        false,
        '没有 hub 在应答（未启动 / 未带 --push / 不可达）——配置已落盘，hub 起来后按 mtime 自动拾取',
      )
    }
    return done(true, '已让 hub 重读 rl-config（push worker 登记表已刷新）')
  } finally {
    release(WORKER_LOCK)
  }
}
