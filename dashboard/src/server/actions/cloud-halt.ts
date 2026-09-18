/** cloud-halt.ts — 云端停机与恢复（停云端省 GPU 配额，本地进程不动）。 */
import { httpOk } from '../../core/net'
import { entryForCourse, loadRegistry, scopeOf } from '../../core/registry'
import { sharedHubUrl } from '../../core/slots'
import type { Component, RlConfig } from '../../core/types'
import { loadConsoleState, saveConsoleState } from './console-state'

// ────────────────────────── 云端停机 / 恢复（§385 复审：停云端省 GPU 配额，本地进程不动） ──────────────────────────

/** hub 管理端点（Bearer 同 worker）。hub 不可达/鉴权失败 → false（不抛）。
 *
 *  共享 hub（2026-09-18）：地址是**唯一**的（一个进程服务所有并行课程），但 halt/resume
 *  的**语义**必须按课程——达令以 `?course=` 下发，只停那一门课的云机（进程级一个布尔会
 *  让 A 课的门禁 ABORT 把 B 课的云机一起停掉）。空课程 = 全课程（旧语义，/admin/status
 *  这类全局读取也走这条）。 */
export function hubAdminOk(cfg: RlConfig, pathSuffix: string, course = ''): Promise<boolean> {
  const qs = course
    ? `${pathSuffix.includes('?') ? '&' : '?'}course=${encodeURIComponent(course)}`
    : ''
  return httpOk(`${sharedHubUrl(cfg)}${pathSuffix}${qs}`, cfg.rl.remote_token, 5000)
}

/** 取某组件在某课程下的登记条目（严格按课；旧扁平键已移除，R2）。
 *  槽位归一：共享组件（hub/隧道）恒看 `''` 槽，调用方传的课程只当视图语境。 */
export function entryOf(key: Component, course = ''): ReturnType<typeof entryForCourse> {
  return entryForCourse(loadRegistry(), key, scopeOf(key, course))
}

/** 云端停机（§386，幂等）：置停机态——hub 置 halt（任务仍正常分发，达令随任务同发）
 *  + console-state 记 halted（红横幅）。只影响"停机命令"，本地进程一律不动。 */
export async function triggerCloudHalt(
  cfg: RlConfig,
  reason: string,
  course = '',
): Promise<{ ok: boolean; message: string }> {
  const state = loadConsoleState()
  const prev = state.cloudHalts?.[course]
  if (prev?.status === 'halted') {
    return { ok: true, message: '已是停机中状态（幂等跳过）' }
  }
  const ok = await hubAdminOk(cfg, '/admin/workers/halt', course)
  if (!ok) {
    return { ok: false, message: '停机指令下发失败（hub 不可达或拒绝）' }
  }
  saveConsoleState({
    cloudHalts: {
      ...state.cloudHalts,
      [course]: { at: new Date().toISOString(), reason, status: 'halted' },
    },
  })
  return {
    ok: true,
    message: `训练已停止：${reason}（已发停机命令；云机先尝试停机，停不掉则继续干活）`,
  }
}

/** 停机条件消失 → 恢复：hub 复位 halt + 标记 recovered（§386：记录保留→灰横幅历史）。
 *  云机继续工作（停不掉的会话）此时恢复常态；TrainingLoop 重启也会自动走这里。 */
export async function markCloudHaltRecovered(
  cfg: RlConfig,
  clearReason: string,
  course = '',
): Promise<{ ok: boolean; message: string }> {
  const state = loadConsoleState()
  const prev = state.cloudHalts?.[course]
  if (!prev || prev.status === 'recovered') {
    return { ok: true, message: '无停机记录或已恢复（幂等跳过）' }
  }
  const ok = await hubAdminOk(cfg, '/admin/workers/resume', course)
  saveConsoleState({
    cloudHalts: {
      ...state.cloudHalts,
      [course]: {
        ...prev,
        status: 'recovered',
        clearedAt: new Date().toISOString(),
        clearReason,
      },
    },
  })
  if (!ok) {
    return { ok: false, message: '恢复指令失败（hub 不可达或拒绝）；停机记录已标已恢复' }
  }
  return { ok: true, message: '云端停机已解除（曾停机记录保留在灰横幅）' }
}
