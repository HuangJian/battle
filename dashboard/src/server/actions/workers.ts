/** workers.ts — GPU push worker 登记（回写 rl-config `nodes[]` + 让 hub 立刻重载）。
 *
 *  形状（2026-09-18 用户口径：「dashboard 提供 worker 登记入口，回填 rl-config.json，
 *  hub 周期 ping 检测联通」）：
 *    · **配置是唯一事实源**——登记 = upsert `nodes[]` 里那条 `gpu_push` 条目；hub 按
 *      mtime 热重载它（不必重启），本动作只在写完顺手叫 hub 立即拾取一次（best-effort）。
 *    · **探活分两层且各有其位**：登记时面板直探一次 `{url}/ping`（当场告诉操作员通不通，
 *      不通**不拦登记**——云机还没开机是完全正常的中间态），此后由 hub 的周期探活承担
 *      常态判定（面板的「hub 侧」那一列）。
 *    · **指针一致性**：改 url / 删节点时，`courses.<课>.push_node_url` 里指向旧 URL 的
 *      指针一并收口——否则它会变成「指向一个 config 里不存在的 URL」，python 侧匹配 0 个
 *      节点后**静默回落 pull**，而操作员看到的是一切正常（2026-09-15 同一类事故的入口）。
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

/** rl-config 里跟这条 URL 绑定的课程指针（`courses.<课>.push_node_url`）。 */
function coursesPointingAt(cfg: RlConfig, url: string): string[] {
  const target = url.replace(/\/+$/, '')
  const out: string[] = []
  for (const [course, c] of Object.entries(cfg.courses ?? {})) {
    if (String(c?.push_node_url ?? '').replace(/\/+$/, '') === target) out.push(course)
  }
  return out
}

/** 把课程指针从 oldUrl 改写到 newUrl（改 worker 地址时保持「配置指向」不悬空）。 */
function retargetCourses(cfg: RlConfig, oldUrl: string, newUrl: string): string[] {
  const hit = coursesPointingAt(cfg, oldUrl)
  if (hit.length === 0) return hit
  cfg.courses = { ...cfg.courses }
  for (const course of hit) {
    cfg.courses[course] = { ...cfg.courses[course], push_node_url: newUrl }
  }
  return hit
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
    let retargeted: string[] = []
    if (idx >= 0) {
      const prev = nodes[idx]!
      if (!prev.gpu_push) {
        throw new ActionError(
          `id ${id} 已是 rollout 节点（非 gpu_push）——换一个 id，或先移除该节点再登记为 push worker`,
        )
      }
      const oldUrl = String(prev.url ?? '')
      nodes[idx] = { ...prev, url, authKey, concurrency: conc, enabled, gpu_push: true }
      if (oldUrl.replace(/\/+$/, '') !== url) retargeted = retargetCourses(cfg, oldUrl, url)
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
      retargeted.length > 0 ? `课程指针已改写: ${retargeted.join('、')}` : '',
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

/** 移除一台已登记的 push worker（同时收口指向它的课程指针）。 */
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
    const victimUrl = String(victim.url ?? '')
    nodes.splice(idx, 1)
    cfg.nodes = nodes
    const orphaned = coursesPointingAt(cfg, victimUrl)
    if (orphaned.length > 0) {
      cfg.courses = { ...cfg.courses }
      for (const course of orphaned) {
        const next = { ...cfg.courses[course] }
        delete next.push_node_url
        cfg.courses[course] = next
      }
    }
    saveConfig(cfg)
    const nudged = await nudgeHubReload(cfg)
    return done(true, `worker ${id} 已移除（rl-config.json 已回写）`, [
      orphaned.length > 0
        ? `已清掉指向它的课程 push_node_url: ${orphaned.join('、')}（留着会静默回落 pull）`
        : '无课程指向它',
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
