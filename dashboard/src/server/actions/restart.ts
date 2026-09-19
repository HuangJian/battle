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
import {
  cloudflaredSpec,
  hubServerSpec,
  localWorkerSpec,
  selfNodeSpec,
  trainerServeSpec,
  workerServeSpec,
} from '../../stack/specs'

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
    // 共享组件（hub / 隧道 / 本机 worker）的旧形状条目（`hubServers[<课>]` / `localWorkers[<课>]`）
    // **拒重建**：它们与共享实例服务同一件事，用共享 spec 把一门课的名字重新拉起一个进程 =
    // 两个 hub 读同一棵 job 目录（双派发 / 双租约 / 结果回错家）或多份 worker 抢同一份 hub
    // 队列里的活，正是本仓最怕的串味。旧实例只能被显式换代接管
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
    // 共享本机 worker（2026-09-19）：一个进程服务所有课程，spec 与**课程无关**
    // （领到哪门课的 job 就干哪门课的活）——所以重建就是重建同一份 spec。
    case 'localWorker':
      if (c) {
        warn(
          `[console] localWorker[${c}] 是旧形状的每课 worker（共享实例已接管该角色）——` +
            '不再重建；启动共享实例时会自动停止并清账',
        )
        return null
      }
      return localWorkerSpec(cfg, venv)
    // 共享 trainer（2026-09-19 / R3-5）：一个进程服务所有课程，spec 与**课程无关**
    // （课程由 `--traj-root` 发现，机器侧旋钮住 rl-config）——所以重建就是重建同一份 spec。
    case 'trainingLoop':
      if (c) {
        // 旧形状的每课条目（`trainingLoops[<课>]`）**拒重建**：它与共享实例服务同一件事，
        // 用共享 spec 把一门课的名字重新拉起一个进程 = 两套调度器抢同一批 traj
        // （正是单实例锁要防的那件事）。旧实例只能被**显式换代接管**
        // （hub.ts::supersedeLegacyInstances，在启动共享 trainer 时收掉）。
        warn(
          `[console] trainingLoop[${c}] 是旧形状的每课 trainer（共享实例已接管该角色）——` +
            '不再重建；启动共享 trainer 时会自动停止并清账',
        )
        return null
      }
      return trainerServeSpec(cfg, venv)
  }
}
