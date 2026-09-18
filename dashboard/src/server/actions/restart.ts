/** restart.ts — 变更检测重启（监督器回调：由 (key, course) 精确重建 ProcSpec）。 */
import { loadConfig } from '../../core/config'
import { warn } from '../../core/log'
import {
  entryForCourse,
  isCourseComponent,
  isSharedComponent,
  loadRegistry,
  scopeOf,
} from '../../core/registry'
import type { Component, ProcSpec } from '../../core/types'
import { resolveVenvPython } from '../../core/venv'
import { isBcCourse } from '../../stack/courses'
import {
  bcLoopSpec,
  cloudflaredSpec,
  hubServerSpec,
  localWorkerSpec,
  selfNodeSpec,
  trainingLoopSpec,
  workerServeSpec,
} from '../../stack/specs'
import { sharedHubUrl } from '../../core/slots'

// ────────────────────────── 变更检测重启（监督器回调） ──────────────────────────

/** 按账本元数据 + 当前 rl-config 重建组件 spec（监督器 restartProc 的数据源）。
 *
 *  **fail-closed（M5）**：只认 `(key, course)` 精确命中到的条目；不带 fallback、
 *  不用 console-state 的当前课程猜——A 课进程被 B 课配置拉起是多课时代最危险的
 *  串味（会把 A 的 hub 换成 B 的 jobRoot）。返回 null = 该组件没有可重建的 spec。
 *  旧账本条目在 loadRegistry 的一次性回填里已补上 `course`，故不会因缺字段失监督。 */
export function restartSpecFor(key: Component, course = ''): ProcSpec | null {
  // 槽归一化：共享组件（hub/隧道）恒看 `''` 槽——调用方传的是「正在查看的课程」
  // （UI 语境），不是组件的归属。
  const entry = entryForCourse(loadRegistry(), key, scopeOf(key, course))
  if (!entry) return null
  const cfg = loadConfig()
  const venv = resolveVenvPython()
  const c = entry.course ?? course
  // 课程键控组件缺 course 且非无课程语义 → 拒绝重建（响亮告警在调用方）。
  // 共享组件排除在外：它们的槽**就是**空串，那不是「缺字段」。
  if (!isSharedComponent(key) && isCourseComponent(key) && course && !entry.course) return null
  switch (key) {
    case 'selfNode':
      return selfNodeSpec(cfg)
    // 共享组件（hub/隧道）的旧形状条目（`hubServers[<课>]`）**拒重建**：它们与共享实例
    // 服务同一件事，用共享 spec 把一门课的名字重新拉起一个进程 = 两个 hub 读同一棵 job 目录
    // （双派发 / 双租约 / 结果回错家），正是本仓最怕的串味。旧实例只能被显式换代接管
    // （hub.ts::supersedeLegacyInstances，在启动共享实例时收掉）。
    case 'hubServer':
    case 'cloudflared':
      if (c) {
        warn(
          `[console] ${key}[${c}] 是旧形状的每课实例（共享实例已接管该角色）——` +
            '不再重建；启动共享 hub 时会自动停止并清账',
        )
        return null
      }
      return key === 'hubServer' ? hubServerSpec(cfg) : cloudflaredSpec(cfg, entry)
    case 'workerServe':
      return workerServeSpec(cfg, venv, c)
    case 'localWorker':
      return localWorkerSpec(cfg, venv, c)
    case 'trainingLoop': {
      // local 模式（本机独立 worker）的 pull 目标是本机 hub——与 start.ts 同一条
      // 推导（rebuild 必须逐字段等于原 spec，否则监督重启会把 hub 打回配置里的隧道）。
      const hubUrl = entry.mode === 'local' ? sharedHubUrl(cfg) : undefined
      // BC 课程 → run_bc 编排器 spec（2026-09-13；entry 区分 rl/bc 入口）
      if (isBcCourse(c)) {
        return bcLoopSpec(cfg, {
          course: c,
          ppo: entry.mode,
          pushNodeUrl: entry.pushNodeUrl,
          hubUrl,
          venv,
        })
      }
      return trainingLoopSpec(cfg, {
        course: c,
        ppo: entry.mode,
        pushNodeUrl: entry.pushNodeUrl,
        hubUrl,
        venv,
        // T7：监督重启必须复现启动时的 opt-in（默认关）。
        remoteDegrade: !!entry.remoteDegrade,
      })
    }
  }
}
