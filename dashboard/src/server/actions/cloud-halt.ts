/** cloud-halt.ts — 云端停机与恢复（停云端省 GPU 配额，本地进程不动）。 */
import { httpOk } from '../../core/net'
import { entryForCourse, loadRegistry } from '../../core/registry'
import { slotPort } from '../../core/slots'
import type { Component, RlConfig } from '../../core/types'
import { loadConsoleState, saveConsoleState } from './console-state'

// ────────────────────────── 云端停机 / 恢复（§385 复审：停云端省 GPU 配额，本地进程不动） ──────────────────────────

/** hub 管理端点（Bearer 同 worker）。hub 不可达/鉴权失败 → false（不抛）。
 *  course 决定槽位端口（S17 的多课 halt 化在 P5 接上：现在已按槽位取端口，
 *  不再硬编码 slot0）。 */
export function hubAdminOk(cfg: RlConfig, pathSuffix: string, course = ''): Promise<boolean> {
  const port = slotPort(cfg, course, 'hub')
  if (!port) return Promise.resolve(false)
  return httpOk(`http://127.0.0.1:${port}${pathSuffix}`, cfg.rl.remote_token, 5000)
}

/** 取某组件在某课程下的登记条目（严格按课；旧扁平键已移除，R2）。 */
export function entryOf(key: Component, course = ''): ReturnType<typeof entryForCourse> {
  return entryForCourse(loadRegistry(), key, course)
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
      ...(state.cloudHalts ?? {}),
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
      ...(state.cloudHalts ?? {}),
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
