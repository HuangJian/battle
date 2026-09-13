/** restart.ts — 变更检测重启（监督器回调：由 (key, course) 精确重建 ProcSpec）。 */
import { loadConfig } from '../../core/config'
import { entryForCourse, isCourseComponent, loadRegistry } from '../../core/registry'
import type { Component, ProcSpec } from '../../core/types'
import { resolveVenvPython } from '../../core/venv'
import { isBcCourse } from '../../stack/courses'
import {
  bcLoopSpec,
  cloudflaredSpec,
  hubServerSpec,
  selfNodeSpec,
  trainingLoopSpec,
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
  const entry = entryForCourse(loadRegistry(), key, course)
  if (!entry) return null
  const cfg = loadConfig()
  const venv = resolveVenvPython()
  const c = entry.course ?? course
  // 课程键控组件缺 course 且非无课程语义 → 拒绝重建（响亮告警在调用方）
  if (isCourseComponent(key) && course && !entry.course) return null
  switch (key) {
    case 'selfNode':
      return selfNodeSpec(cfg)
    case 'hubServer':
      return hubServerSpec(cfg, c)
    case 'cloudflared':
      return cloudflaredSpec(cfg, { ...entry, course: c, slot: entry.slot ?? 0 })
    case 'workerServe':
      return workerServeSpec(cfg, venv, c)
    case 'trainingLoop':
      // BC 课程 → run_bc 编排器 spec（2026-09-13；entry 区分 rl/bc 入口）
      if (isBcCourse(c)) {
        return bcLoopSpec(cfg, {
          course: c,
          ppo: entry.mode,
          pushNodeUrl: entry.pushNodeUrl,
          venv,
        })
      }
      return trainingLoopSpec(cfg, {
        course: c,
        ppo: entry.mode,
        pushNodeUrl: entry.pushNodeUrl,
        venv,
      })
  }
}
