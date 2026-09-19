/** course-knobs.ts — **课程机器侧旋钮**（rl-config `courses.<课>.*`，2026-09-19 / R3-5）。
 *
 *  为什么需要这一层：trainer 收敛成**一个进程服务所有课程**（`run_rl_cluster.py --serve`）之后，
 *  「这门课怎么连云」不能再是那个进程的命令行参数（一个进程服务 N 门课，命令行只有一份）。
 *  它搬到这里 —— 与 `push_node_url`、本机并发配额同一条规矩：**机器侧旋钮住 rl-config，
 *  永不进 `curricula/*.jsonc`**（课程文件字节 = `course_fp` 语料血缘 / 熔断口径，D14；
 *  往里加一个传输旋钮，熔断会把同一份语料读成新语料）。
 *
 *  读面在 python：`rl/loop_serve.py::apply_course_machine_overrides` 在开课时施加（白名单 +
 *  值域校验 + 逐键打印生效值）；本模块是**写面**（控制台唯一写法）。
 *
 *  | 键 | 谁关心 | 为什么需要 |
 *  |---|---|---|
 *  | `remote_transport` | RL / BC 的传输裁决 | 过去的 `--remote-transport pull` 进程级钉子：云机 pull 会话会被残留在 `push_node_url` 里的节点劫走（2026-09-17 事故） |
 *  | `remote_hub_url` | pull / local | local 模式要打**本机 hub**（且不能动 rl.remote_hub_url——那是 pull preset 隧道 URL 的家） |
 *  | `remote_degrade_after` | T7 降级 | 远端连败是否 opt-in 降级本机（0 = 关，默认） |
 *  | `gate_halt_mode` | RL 门禁 | 门禁失败语义（halt = 打进停机态） |
 */

import { loadConfig, saveConfig } from '../core/config'
import type { CourseConf, RlConfig } from '../core/types'

/** 传输裁决（RL 值域 = `auto|pull|push|hubpush`；BC 另有 `local`——见 python 侧白名单校验）。 */
export type RemoteTransport = NonNullable<CourseConf['remote_transport']>

/** 控制台会写的旋钮（未给的键**不动**——不写 = 沿用现有值，绝不「顺手清空」）。 */
export interface CourseMachineKnobs {
  remoteTransport?: RemoteTransport
  /** 本课 pull/hubpush 打哪个 hub（local 模式 = 本机 hub）。 */
  remoteHubUrl?: string
  /** T7：远端连败降级本机的阈值（0 = 关）。 */
  remoteDegradeAfter?: number
  /** 门禁失败语义。 */
  gateHaltMode?: string
}

/** 本课的机器侧旋钮**当前值**（未配 = 字段缺席——「没配」与「配成空串」是两件事，读面不猜）。 */
export function courseMachineKnobs(cfg: RlConfig, course: string): CourseMachineKnobs {
  const b = cfg.courses?.[course]
  if (!b) return {}
  const out: CourseMachineKnobs = {}
  if (b.remote_transport !== undefined) out.remoteTransport = b.remote_transport
  if (b.remote_hub_url !== undefined) out.remoteHubUrl = b.remote_hub_url
  if (b.remote_degrade_after !== undefined) out.remoteDegradeAfter = b.remote_degrade_after
  if (b.gate_halt_mode !== undefined) out.gateHaltMode = b.gate_halt_mode
  return out
}

/** 写本课的机器侧旋钮 → 落盘 rl-config，返回是否**真的改了**。
 *
 *  幂等是刻意的：控制台每次「启动」都会调它（把当前模式表达成旋钮），无变化时不该重写
 *  配置文件——rl-config 的 mtime 是 hub 热重载（`--push-config`）的依据之一。
 */
export function writeCourseMachineKnobs(
  cfg: RlConfig,
  course: string,
  knobs: CourseMachineKnobs,
): { cfg: RlConfig; changed: boolean } {
  const courses = { ...cfg.courses }
  const cur: CourseConf = { ...courses[course] }
  const snap = (c: CourseConf): string =>
    JSON.stringify([
      c.remote_transport ?? null,
      c.remote_hub_url ?? null,
      c.remote_degrade_after ?? null,
      c.gate_halt_mode ?? null,
    ])
  const before = snap(cur)
  if (knobs.remoteTransport !== undefined) cur.remote_transport = knobs.remoteTransport
  if (knobs.remoteHubUrl !== undefined) cur.remote_hub_url = knobs.remoteHubUrl
  if (knobs.remoteDegradeAfter !== undefined) cur.remote_degrade_after = knobs.remoteDegradeAfter
  if (knobs.gateHaltMode !== undefined) cur.gate_halt_mode = knobs.gateHaltMode
  if (snap(cur) === before) return { cfg, changed: false }
  courses[course] = cur
  const next: RlConfig = { ...cfg, courses }
  saveConfig(next)
  return { cfg: next, changed: true }
}

/** 便捷写法：读 → 合并旋钮 → 落盘（返回落盘后的 config 与是否变更）。 */
export function applyCourseMachineKnobs(
  course: string,
  knobs: CourseMachineKnobs,
): { cfg: RlConfig; changed: boolean } {
  return writeCourseMachineKnobs(loadConfig(), course, knobs)
}
