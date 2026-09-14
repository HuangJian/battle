/** console-state.ts — 控制台状态读写（trainer 模式 / 当前课程 / 停机记录）。 */
import { mkdirSync, readFileSync, writeFileSync } from 'fs'
import path from 'path'
import { consoleStatePath } from '../../core/paths'

// ────────────────────────── 控制台状态（trainer 模式 / 当前课程） ──────────────────────────

// 路径惰性取自 paths.consoleStatePath()（同 registry.ts 的 BCITY_REGISTRY_FILE）：
// 默认线上路径，单测置 env 重定向到临时目录，绝不写脏线上 console-state.json。
// 惰性求值很关键——ES import 会被提升到测试文件的 env 赋值之前，模块初始化时
// 抓取的常量会忽略重定向（本仓踩过：2026-09-12 线上 console-state.json 被
// cloud-halt.test.ts 的 fixture（{at:'T3',...}）污染，hub 真实 halt 记录丢失）。

export interface CloudHaltInfo {
  /** 停机时刻（ISO）。 */
  at: string
  /** 触发原因（训练停车/异常退出的判词，或手动）。 */
  reason: string
  /** 停机状态：halted=停机中（红横幅）· recovered=已恢复（灰横幅，历史保留）。 */
  status: 'halted' | 'recovered'
  /** recovered 时刻（§386：恢复后记录保留→灰横幅"曾有停机但已恢复"）。 */
  clearedAt?: string
  /** 恢复原因（手动恢复 / TrainingLoop 重启=停机条件消失）。 */
  clearReason?: string
}

export interface ConsoleState {
  /** trainer 基建编排模式：pull=remote+隧道（云机 poll）· push=remote 无本地隧道 ·
   *  local=本机独立 localWorker（pull 本机 hub，与云端 worker 同一份代码）。 */
  trainerPpo: 'pull' | 'push' | 'local'
  /** 当前课程（组件启动的 jobRoot/日志目录来源）。 */
  course: string
  /** 控制台当前课程（P5-W1 additive）——读取优先它，写入与 `course` 同值。
   *  旧写入只有 `course`，加载时回填（R4：additive、无迁移阻塞）。 */
  activeCourse: string
  /** 每课云端停机记录（S17：键 = 课程名；'' = 无课程）。§386：halted=红横幅；
   *  recovered=灰横幅历史，不复位删除，确保"曾停机"可见。旧单键 `cloudHalt`
   *  在加载时一次性折叠进本表（键取当时的 `course`）。 */
  cloudHalts?: Record<string, CloudHaltInfo>
}

const DEFAULT_STATE: ConsoleState = { trainerPpo: 'pull', course: '', activeCourse: '' }

export function loadConsoleState(): ConsoleState {
  try {
    const raw = JSON.parse(readFileSync(consoleStatePath(), 'utf-8')) as Partial<ConsoleState> & {
      cloudHalt?: CloudHaltInfo
    }
    const merged = { ...DEFAULT_STATE, ...raw } as ConsoleState
    merged.activeCourse = raw.activeCourse || merged.course
    if (raw.cloudHalts === undefined) {
      // 一次性迁移：旧单键 → per-course 表（键 = 当时课程）。已写新表后不再复活。
      merged.cloudHalts = raw.cloudHalt ? { [merged.course || '']: raw.cloudHalt } : {}
    }
    merged.cloudHalts = merged.cloudHalts ?? {}
    delete (merged as unknown as Record<string, unknown>).cloudHalt
    return merged
  } catch {
    return { ...DEFAULT_STATE }
  }
}

export function saveConsoleState(patch: Partial<ConsoleState>): ConsoleState {
  const next = { ...loadConsoleState(), ...patch }
  try {
    mkdirSync(path.dirname(consoleStatePath()), { recursive: true })
    writeFileSync(consoleStatePath(), JSON.stringify(next, null, 2), 'utf-8')
  } catch {
    /* 非致命——内存态仍生效到本进程 */
  }
  return next
}
