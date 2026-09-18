/** component-meta.ts — 受管组件元数据表：日志路径与健康探测 URL（唯一声明处）。 */
import path from 'path'
import { LOG_DIR } from '../../core/paths'
import { sharedHubUrl, slotPort } from '../../core/slots'
import type { Component, RlConfig } from '../../core/types'
import { courseLogDir } from '../../stack/specs'

// ────────────────────────── 快照组装 ──────────────────────────

// 组件日志统一读 LOG_DIR（仓库根 tmp/，2026-09-08 双 tmp 统一）——绝对路径，
// 与组件启动写入路径（specs.ts log:）同源，不依赖控制台自身 cwd。
// M6：hub/workerServe 日志 per-course（spec 侧 specs.ts + 本 resolver 两半必须同步，
// 否则组件表日志尾扫到别课的文件）。无课程时沿用旧单课路径。
export const COMPONENT_LOGS: Partial<Record<Component, (cfg: RlConfig, course: string) => string>> =
  {
    selfNode: () => path.join(LOG_DIR, 'sampler-agent.log'),
    // 共享 hub/隧道 ⇒ 日志也唯一（不再 per-course；与 specs.ts 的 log: 同源）
    hubServer: () => path.join(LOG_DIR, 'hub-server.out'),
    cloudflared: (_c) => path.join(LOG_DIR, 'cloudflared.log'),
    localWorker: (_c, course) =>
      path.join(course ? courseLogDir(course) : LOG_DIR, 'local-worker.log'),
    trainingLoop: (_cfg, course) => path.join(courseLogDir(course), 'training-loop.log'),
    workerServe: (_c, course) =>
      path.join(course ? courseLogDir(course) : LOG_DIR, 'remote-worker-serve.log'),
  }

/** 组件健康探测 URL（端口一律经槽位算术；course 决定槽位）。 */
export const HEALTHY_PORTS: Partial<Record<Component, (cfg: RlConfig, course: string) => string>> =
  {
    selfNode: (c) => `http://127.0.0.1:${c.rl.agent_port}/v1/ping`,
    hubServer: (c) => `${sharedHubUrl(c)}/ping`,
    workerServe: (c, course) => `http://127.0.0.1:${slotPort(c, course, 'push')}/ping`,
  }

export const ALL_COMPONENTS: Component[] = [
  'selfNode',
  'hubServer',
  'localWorker',
  'cloudflared',
  'trainingLoop',
  'workerServe',
]

/** 逐行容错的日志尾（§361③）：单行损坏/读取异常只丢该行，不再让整个 /api/state 500。 */
