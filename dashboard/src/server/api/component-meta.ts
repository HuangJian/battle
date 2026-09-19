/** component-meta.ts — 受管组件元数据表：日志路径与健康探测 URL（唯一声明处）。 */
import path from 'path'
import { LOG_DIR } from '../../core/paths'
import { sharedHubUrl } from '../../core/slots'
import type { Component, RlConfig } from '../../core/types'
import { courseLogDir } from '../../stack/specs'

// ────────────────────────── 快照组装 ──────────────────────────

// 组件日志统一读 LOG_DIR（仓库根 tmp/，2026-09-08 双 tmp 统一）——绝对路径，
// 与组件启动写入路径（specs.ts log:）同源，不依赖控制台自身 cwd。
// 共享组件（hub / 隧道 / 本机 worker）日志唯一；trainer 有**每课镜像**（serve 的行路由写到
// tmp/<课>/training-loop.log），故它按课程解析。spec 侧 `log:` 与本 resolver 两半必须同步，
// 否则组件表日志尾会扫到别课的文件。无课程时沿用旧单课路径。
export const COMPONENT_LOGS: Partial<Record<Component, (cfg: RlConfig, course: string) => string>> =
  {
    selfNode: () => path.join(LOG_DIR, 'sampler-agent.log'),
    // 共享 hub/隧道 ⇒ 日志也唯一（不再 per-course；与 specs.ts 的 log: 同源）
    hubServer: () => path.join(LOG_DIR, 'hub-server.out'),
    cloudflared: (_c) => path.join(LOG_DIR, 'cloudflared.log'),
    // 共享本机 worker（2026-09-19）：日志也唯一（不再按课程分目录；与 specs.ts 的 log: 同源）
    localWorker: () => path.join(LOG_DIR, 'local-worker.log'),
    trainingLoop: (_cfg, course) => path.join(courseLogDir(course), 'training-loop.log'),
    // 本机伪节点（remote_worker_serve）**不在受管组件里**（2026-09-19）：它只服务冒烟预演，
    // 由预演自起自停（`stack/push.ts`）——没有卡片、没有账本、没有日志页入口。
  }

/** 组件健康探测 URL（端口一律经槽位算术；course 决定槽位）。 */
export const HEALTHY_PORTS: Partial<Record<Component, (cfg: RlConfig, course: string) => string>> =
  {
    selfNode: (c) => `http://127.0.0.1:${c.rl.agent_port}/v1/ping`,
    hubServer: (c) => `${sharedHubUrl(c)}/ping`,
  }

/** 受管组件（控制台能启/停/冒烟/监督的那些）——**卡行与日志页入口都从它派生**。
 *  `workerServe`（本机伪 GPU 节点）**刻意不在列**（2026-09-19 用户指令）：它只是 trainer
 *  冒烟预演的一次性配角，用户只关心「冒烟过没过」，不会去手动启停一个伪节点。 */
export const ALL_COMPONENTS: Component[] = [
  'selfNode',
  'hubServer',
  'localWorker',
  'cloudflared',
  'trainingLoop',
]

/** 逐行容错的日志尾（§361③）：单行损坏/读取异常只丢该行，不再让整个 /api/state 500。 */
