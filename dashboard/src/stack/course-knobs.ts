/** course-knobs.ts — **课程机器侧旋钮**（rl-config `courses.<课>.*`，2026-09-19 / R3-5）。
 *
 *  为什么需要这一层：trainer 收敛成**一个进程服务所有课程**（`run_rl_cluster.py --serve`）之后，
 *  「这门课怎么跑」不能再是那个进程的命令行参数（一个进程服务 N 门课，命令行只有一份）。
 *  它搬到这里 —— 机器侧旋钮住 rl-config，**永不进 `curricula/*.jsonc`**（课程文件字节 =
 *  `course_fp` 语料血缘 / 熔断口径，D14；往里加一个旋钮，熔断会把同一份语料读成新语料）。
 *
 *  读面在 python：`rl/loop_serve.py::apply_course_machine_overrides` 在开课时施加（白名单 +
 *  逐键打印生效值）；本模块是**写面**（控制台唯一写法）。
 *
 *  | 键 | 谁关心 | 为什么需要 |
 *  |---|---|---|
 *  | `gate_halt_mode` | RL 门禁 | 门禁失败语义（halt = 打进停机态） |
 *
 *  ★ **2026-09-21：删掉了 `remote_degrade_after`**（plan/accident.plan.md §3「无 fallback」）。
 *  单一 PPO 路径下「远端连败就降级到本机算」这个档位不存在：loop 自己没有计算能力，
 *  无人认领就**等着**（状态响亮报「等待认领中」），永不自己算。留一把会写进 rl-config 的
 *  死旋钮 = 给操作员一个不会发生的承诺 ⇒ 连同 UI/透传一起删，残留值进 legacy 清理名单。
 *
 *  ★ **2026-09-19：删掉了 `remote_transport` 与 `remote_hub_url`**（用户口径「课程任务与
 *  worker 节点互相正交」）。它们把「这门课走哪条传输路 / 打哪个 hub」变成课程属性：同一门课
 *  换个机器跑就得改课程配置，而任何 worker 都该能接任何课程的活。传输裁决现在是**部署事实**：
 *  `rl.hub_push`（缺省开）+ 登记在册的 `nodes[].gpu_push` + hub 队列。旧值不读、并在启动
 *  训练时由 `pruneLegacyCourseKnobs` 清掉。
 */

import { loadConfig, saveConfig } from '../core/config'
import { log } from '../core/log'
import type { CourseConf, RlConfig } from '../core/types'

/** 控制台会写的旋钮（未给的键**不动**——不写 = 沿用现有值，绝不「顺手清空」）。 */
export interface CourseMachineKnobs {
  /** 门禁失败语义。 */
  gateHaltMode?: string
}

/** 本课的机器侧旋钮**当前值**（未配 = 字段缺席——「没配」与「配成空串」是两件事，读面不猜）。 */
export function courseMachineKnobs(cfg: RlConfig, course: string): CourseMachineKnobs {
  const b = cfg.courses?.[course]
  if (!b) return {}
  const out: CourseMachineKnobs = {}
  if (b.gate_halt_mode !== undefined) out.gateHaltMode = b.gate_halt_mode
  return out
}

/** 写本课的机器侧旋钮 → 落盘 rl-config，返回是否**真的改了**。
 *
 *  幂等是刻意的：控制台每次「启动」都会调它，无变化时不该重写配置文件——rl-config 的 mtime
 *  是 hub 热重载（`--push-config`）的依据之一。
 */
export function writeCourseMachineKnobs(
  cfg: RlConfig,
  course: string,
  knobs: CourseMachineKnobs,
): { cfg: RlConfig; changed: boolean } {
  const courses = { ...cfg.courses }
  const cur: CourseConf = { ...courses[course] }
  const snap = (c: CourseConf): string => JSON.stringify([c.gate_halt_mode ?? null])
  const before = snap(cur)
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

// ────────────────────────── legacy 清理（2026-09-19） ──────────────────────────

/** 已废的课程级键（不再被任何读者读；旧预设/旧开课弹窗写过）。
 *
 *  `remote_degrade_after`（2026-09-21 / §3）：单一 PPO 路径下没有「降级本机」这个档位，
 *  python 侧已从 `COURSE_MACHINE_OVERRIDE_KEYS` 白名单移除 ⇒ 它**已无读者**，留着只会
 *  让操作员以为「我配过降级」。 */
const LEGACY_COURSE_KEYS = [
  'push_node_url',
  'remote_transport',
  'remote_hub_url',
  'hub_push',
  'remote_degrade_after',
] as const

/** 已废的**本机伪节点**标记（R3-7：伪节点退出控制台，那条「一键本机 push」也删了）。
 *  留着它只有坏处：`nodes[]` 里一条指向 `127.0.0.1:<push 端口>` 的 gpu_push 条目会被
 *  当成真执行面（python 的 auto 现在**全取**登记节点）。 */
const LEGACY_NODE_FLAG = 'local_push' as const

/** 清理 legacy 的传输耦合键与伪节点条目（幂等；返回删掉了什么，供日志/测试断言）。
 *
 *  为什么必须**删**而不只是「停止读取」：这些键描述的是「哪门课走哪条路 / 哪台机器 / 怎么降级」，
 *  在新模型里是被部署决定的（`rl.hub_push` + 登记节点 + hub 队列）。留一条残留的
 *  `push_node_url` 或 `local_push` 节点：前者已无读者但会误导操作员「我配过执行面」，
 *  后者**仍有读者**（python `_gpu_push_nodes` 全取登记节点）⇒ 会把训练指向一条没人服务的
 *  本机地址，而表面「训练正常」。看得见的坏过静默的，静默的必须清掉。
 *
 *  时机 = 启动训练前（`prepareCourseForSharedTrainer`）；写盘只在真删了东西时发生
 *  （rl-config 的 mtime 是 hub 热重载的输入之一，无变化不重写）。
 */
export function pruneLegacyCourseKnobs(cfg: RlConfig): { cfg: RlConfig; removed: string[] } {
  const removed: string[] = []
  const courses = { ...cfg.courses }
  for (const [course, raw] of Object.entries(cfg.courses ?? {})) {
    // 类型表里这些键已删 ⇒ 逐键删除只能走 Record 视图（残留值仍可能在旧 rl-config.json 里）。
    const block: Record<string, unknown> = { ...(raw as Record<string, unknown>) }
    let hit = false
    for (const key of LEGACY_COURSE_KEYS) {
      if (key in block) {
        delete block[key]
        hit = true
        removed.push(`courses.${course}.${key}`)
      }
    }
    if (hit) courses[course] = block as CourseConf
  }
  const nodes = Array.isArray(cfg.nodes) ? [...cfg.nodes] : []
  const keptNodes = nodes.filter((n) => {
    const flag = (n as unknown as Record<string, unknown>)[LEGACY_NODE_FLAG]
    if (flag !== true) return true
    removed.push(`nodes[${n.id ?? '?'}].${LEGACY_NODE_FLAG}`)
    return false
  })
  if (removed.length === 0) return { cfg, removed }
  const next: RlConfig = { ...cfg, courses, nodes: keptNodes }
  saveConfig(next)
  log(
    `[knobs] 清理 legacy 传输耦合：${removed.length} 项（${removed.slice(0, 6).join('、')}` +
      `${removed.length > 6 ? '…' : ''}）——课程与 worker 节点正交，传输裁决住 rl.hub_push + 登记节点`,
  )
  return { cfg: next, removed }
}
