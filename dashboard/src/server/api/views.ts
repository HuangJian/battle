/** views.ts — 组件视图与节点视图组装（状态探测 → 视图对象）。 */
import { httpOk, pidAlive } from '../../core/net'
import { componentScope, entryForCourse, loadRegistry, scopeOf } from '../../core/registry'
import type { Component, RlConfig } from '../../core/types'
import type { ComponentView, NodeView } from '../../web/view'
import { COMPONENT_LABELS, busy, componentBusy } from '../actions'
import { ALL_COMPONENTS, HEALTHY_PORTS } from './component-meta'
import { logTail, resolveComponentLog } from './logs'

/** cloudflared 健康判定（2026-09-18）：隧道进程在 ≠ 链路可用。
 *  hub（origin）不通 → 一律 false（黄点）；hub 通时再看隧道 /ping。
 *  无隧道 URL 时以 hub 探测为准。 */
export function cloudflaredHealthy(
  hubOk: boolean | null,
  tunnelOk: boolean | null,
): boolean | null {
  if (hubOk === false) return false
  if (tunnelOk !== null) return tunnelOk
  return hubOk
}

/** **共享/单例组件**的健康探测结果（key → 通不通；缺席 = 探不了/没在跑）。
 *
 *  与课程无关（hub / 隧道 / 采集 agent 都是单实例）且贵（hub 1.5s、隧道 2.5s 超时）
 *  ⇒ 进机群级 SWR 缓存（`snapshot-refresher.getFleetProbes`）。**结构**（存活/日志/作用域）
 *  留在 `componentViews` 里现算：动作后的第一帧要看到「刚启的进程 = running」，
 *  但不必为它的健康探测等一个超时窗口。 */
export type ComponentHealthMap = ReadonlyMap<Component, boolean | null>

/** 探一次共享/单例组件的健康（不落缓存；落缓存由机群级 SWR 负责）。 */
export async function computeComponentHealth(
  cfg: RlConfig,
): Promise<Map<Component, boolean | null>> {
  const reg = loadRegistry()
  const out = new Map<Component, boolean | null>()
  await Promise.all(
    ALL_COMPONENTS.map(async (key): Promise<void> => {
      // 共享/单例槽恒 `''`（selfNode 是扁平单例，course 参数对它无效）。
      const e = entryForCourse(reg, key, scopeOf(key, ''))
      if (!pidAlive(e?.pid)) return // 没在跑 = 无健康可言（结构层显示 stopped/exited）
      const probe = HEALTHY_PORTS[key]?.(cfg, '')
      if (probe) {
        out.set(
          key,
          await httpOk(
            probe,
            key === 'selfNode'
              ? (cfg.nodes.find((n) => n.id === 'self')?.authKey ?? '')
              : cfg.rl.remote_token,
            1500,
          ),
        )
        return
      }
      if (key === 'cloudflared') {
        // 隧道在、hub 不通（origin refused）→ 黄点：进程活着但链路不可用。
        // 先探本课 hub（1500ms），不通直接 false，不再等隧道外网超时。
        const hubProbe = HEALTHY_PORTS.hubServer?.(cfg, '')
        const hubOk = hubProbe ? await httpOk(hubProbe, cfg.rl.remote_token, 1500) : null
        const tunnelOk =
          hubOk !== false && e?.url
            ? await httpOk(`${e.url}/ping`, cfg.rl.remote_token, 2500)
            : null
        out.set(key, cloudflaredHealthy(hubOk, tunnelOk))
        return
      }
      if (key === 'trainingLoop' || key === 'localWorker') {
        // 存活即健康：trainingLoop 就绪以日志产出为准（iters 指标）；localWorker 是
        // 出站轮询者（没有 HTTP 端点可探），存活即它在轮询。
        out.set(key, true)
      }
    }),
  )
  return out
}

export async function componentViews(
  cfg: RlConfig,
  course: string,
  /** 共享组件健康（机群级缓存）；缺席 = 本函数自己探一次（直连调用方/单测）。 */
  health?: ComponentHealthMap,
): Promise<ComponentView[]> {
  const h = health ?? (await computeComponentHealth(cfg))
  const reg = loadRegistry()
  return ALL_COMPONENTS.map((key): ComponentView => {
    // 展示路径：严格按槽取条目（旧扁平键已在 P5 移除，R2）。共享组件（hub/隧道）
    // 恒读 `''` 槽——任何课程页看到的都是**同一个**共享实例，并带上 shared 标记。
    const e = entryForCourse(reg, key, scopeOf(key, course))
    const alive = pidAlive(e?.pid)
    const status: ComponentView['status'] = e ? (alive ? 'running' : 'exited') : 'stopped'
    // 健康只在**在跑**时有意义（停了的组件没有「健康」这回事，陈旧探测不得上屏）。
    let healthy: boolean | null = status === 'running' ? (h.get(key) ?? null) : null
    if (status === 'running' && (key === 'trainingLoop' || key === 'localWorker')) {
      // 存活即健康（无 HTTP 端点可探）：**结构**事实，不经探测缓存——
      // 否则刚启的进程要等一拍才变绿（缓存里还是「那时它不在跑」）。
      healthy = true
    }
    // 运行时动态查找（§374）：静态映射 ≠ 实际落盘文件（cloudflared 动态文件名、
    // 课程子目录日志、tmp 清理后重建）——组件表日志/尾行与 /log/<key> 页同源。
    const logRel = resolveComponentLog(key, cfg, course)
    return {
      key,
      label: COMPONENT_LABELS[key],
      status,
      pid: e?.pid ?? null,
      url: e?.url ?? null,
      course: e?.course ?? null,
      mode: e?.mode ?? null,
      healthy,
      log: logRel,
      logTail: logRel ? logTail(logRel) : [],
      /** §380：非正常退出原因（exit-watchdog 记录），UI 显示"已退出"处展示。 */
      error: e?.error ?? null,
      // 与动作实际加的 busy 键同源（按课键控组件带 course）；否则页面显示「未忙碌」
      // 而服务端 409（2026-09-14 事故：trainingLoop 启动永远返回 409）。
      busy: componentBusy(key, scopeOf(key, course)),
      // 作用域（单例/共享/按课程）——卡片分组的唯一判据（R3-3）。服务端算一次，
      // 客户端不许自己按 key 猜（第二份名单 = 漂开 = 某个组件从 UI 上消失）。
      scope: componentScope(key),
      // cloudflared 卡展示隧道 auth key（复制用）；其余组件无密钥字段
      ...(key === 'cloudflared' ? { secret: cfg.rl.remote_token } : {}),
    }
  })
}

/** 节点**结构行**（rl-config + 内存 busy）：**便宜、无 IO**，故**随请求现算**。
 *
 *  ★ 为什么结构必须现算（2026-09-22）：动作后的第一帧就要看到新的 enabled/url/并发——
 *  否则操作员刚拨下的节点开关会在下一帧「自己弹回去」（面板读的是缓存里的旧行）。
 *  探测列（online/codeHash/cpus/slow/lastContrib）由 `mergeNodeProbes` 从缓存填。 */
export function nodeStructure(cfg: RlConfig): NodeView[] {
  return (cfg.nodes ?? []).map((n) => ({
    id: n.id,
    url: n.url,
    gpuPush: !!n.gpu_push,
    enabled: n.enabled,
    concurrency: n.concurrency,
    online: null,
    slow: false,
    codeHash: null,
    cpus: null,
    busy: busy.has(`node:${n.id}`),
    lastContrib: -1,
  }))
}

/** 单节点的探测结果（贵：未应答要等 1.5s 超时）。 */
export interface NodeProbeResult {
  online: boolean | null
  codeHash: string | null
  cpus: number | null
}

/** **并行**探测 enabled 节点（§365：串行会让 /api/state 在节点离线时拖到 N×4s ——
 *  2026-09-08 实测 5 启用节点 10.1s → 超过 Bun.serve 默认 idleTimeout 10s，服务端关连接
 *  → curl 空回复。Promise.all 保序，输出与串行一致。超时 1500ms = 健康探测口径，
 *  1.5s 不应答即视为离线（§366 预算）。停用节点不探（结果 = 缺席，由合并层补 null）。 */
export async function nodeProbeResults(cfg: RlConfig): Promise<Map<string, NodeProbeResult>> {
  const out = new Map<string, NodeProbeResult>()
  const rows = await Promise.all(
    (cfg.nodes ?? []).map(async (n): Promise<[string, NodeProbeResult] | null> => {
      if (!n.enabled) return null
      let online: boolean | null = null
      let codeHash: string | null = null
      let cpus: number | null = null
      try {
        const resp = await fetch(`${n.url}/v1/ping`, {
          headers: { Authorization: `Bearer ${n.authKey}` },
          signal: AbortSignal.timeout(1500),
        })
        online = resp.status === 200
        if (online) {
          const body = (await resp.json()) as { codeHash?: string; cpus?: number }
          codeHash = body.codeHash ? body.codeHash.slice(0, 12) : null
          cpus = typeof body.cpus === 'number' ? body.cpus : null
        }
      } catch {
        online = false
      }
      return [n.id, { online, codeHash, cpus }]
    }),
  )
  for (const r of rows) if (r) out.set(r[0], r[1])
  return out
}

/** 结构 ⊕ 探测 ⊕ 慢节点判定（纯函数）：展示行。缺席的探测 = `online: null`（＝未探）。 */
export function mergeNodeProbes(
  rows: readonly NodeView[],
  probes: ReadonlyMap<string, NodeProbeResult>,
  slowById?: ReadonlyMap<string, boolean>,
): NodeView[] {
  return rows.map((r) => {
    const p = probes.get(r.id)
    const online = p?.online ?? null
    return {
      ...r,
      online,
      codeHash: p?.codeHash ?? null,
      cpus: p?.cpus ?? null,
      // ping 失败但近期仍在成功结算（结算耗时表明算力受限）= 慢节点，
      // 不标「离线」（2026-09-11 用户指令：慢节点 chip 误报离线的根治）。
      slow: online === false && (slowById?.get(r.id) ?? false),
    }
  })
}

/** 节点视图（结构 + 探测拼在一起；直连调用方与单测用）。
 *  机群级缓存路径（`snapshot-cache.computeSlowSnapshot`）分开用上面三个——
 *  结构现算、探测取缓存，首帧才不会被 ping 压住。 */
export async function nodeViews(
  cfg: RlConfig,
  slowById?: ReadonlyMap<string, boolean>,
): Promise<NodeView[]> {
  return mergeNodeProbes(nodeStructure(cfg), await nodeProbeResults(cfg), slowById)
}
