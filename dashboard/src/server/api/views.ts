/** views.ts — 组件视图与节点视图组装（状态探测 → 视图对象）。 */
import { httpOk, pidAlive } from '../../core/net'
import { componentScope, entryForCourse, loadRegistry, scopeOf } from '../../core/registry'
import type { RlConfig } from '../../core/types'
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

export async function componentViews(cfg: RlConfig, course: string): Promise<ComponentView[]> {
  const reg = loadRegistry()
  return Promise.all(
    ALL_COMPONENTS.map(async (key): Promise<ComponentView> => {
      // 展示路径：严格按槽取条目（旧扁平键已在 P5 移除，R2）。共享组件（hub/隧道）
      // 恒读 `''` 槽——任何课程页看到的都是**同一个**共享实例，并带上 shared 标记。
      const e = entryForCourse(reg, key, scopeOf(key, course))
      const alive = pidAlive(e?.pid)
      const status: ComponentView['status'] = e ? (alive ? 'running' : 'exited') : 'stopped'
      let healthy: boolean | null = null
      const probe = HEALTHY_PORTS[key]?.(cfg, course)
      if (status === 'running' && probe) {
        healthy = await httpOk(
          probe,
          key === 'selfNode'
            ? (cfg.nodes.find((n) => n.id === 'self')?.authKey ?? '')
            : cfg.rl.remote_token,
          1500,
        )
      } else if (status === 'running' && key === 'cloudflared') {
        // 隧道在、hub 不通（origin refused）→ 黄点：进程活着但链路不可用。
        // 先探本课 hub（1500ms），不通直接 false，不再等隧道外网超时。
        const hubProbe = HEALTHY_PORTS.hubServer?.(cfg, course)
        const hubOk = hubProbe ? await httpOk(hubProbe, cfg.rl.remote_token, 1500) : null
        const tunnelOk =
          hubOk !== false && e?.url
            ? await httpOk(`${e.url}/ping`, cfg.rl.remote_token, 2500)
            : null
        healthy = cloudflaredHealthy(hubOk, tunnelOk)
      } else if (status === 'running' && (key === 'trainingLoop' || key === 'localWorker')) {
        // 存活即健康：trainingLoop 就绪以日志产出为准（iters 指标）；localWorker 是
        // 出站轮询者（没有 HTTP 端点可探），存活即它在轮询。
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
    }),
  )
}

/** 节点视图（rl-config + enabled 节点并行 ping）。
 *  并行是硬要求：不可达节点各自等 AbortSignal.timeout(1500)，串行会让 /api/state
 *  在节点离线时拖到 N×4s（2026-09-08 实测 5 启用节点 10.1s → 超过 Bun.serve 默认
 *  idleTimeout 10s，服务端关连接 → curl 空回复；DECISIONS §365）。Promise.all 保序，
 *  输出与串行一致。超时 1500ms：健康探测口径，1.5s 不应答即视为离线（§366 预算）。 */
export async function nodeViews(
  cfg: RlConfig,
  slowById?: ReadonlyMap<string, boolean>,
): Promise<NodeView[]> {
  return Promise.all(
    cfg.nodes.map(async (n): Promise<NodeView> => {
      let online: boolean | null = null
      let codeHash: string | null = null
      let cpus: number | null = null
      if (n.enabled) {
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
      }
      return {
        id: n.id,
        url: n.url,
        gpuPush: !!n.gpu_push,
        enabled: n.enabled,
        concurrency: n.concurrency,
        online,
        // ping 失败但近期仍在成功结算（结算耗时表明算力受限）= 慢节点，
        // 不标「离线」（2026-09-11 用户指令：慢节点 chip 误报离线的根治）。
        slow: online === false && (slowById?.get(n.id) ?? false),
        codeHash,
        cpus,
        busy: busy.has(`node:${n.id}`),
        lastContrib: -1,
      }
    }),
  )
}
