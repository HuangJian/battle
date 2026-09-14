/** preset.ts — 模式预设与开关（stream / double_buffer / trainer pull|push|local）。 */
import { loadConfig, saveConfig, validateCourseArg } from '../../core/config'
import { configurePushEndpoint } from '../../stack/push-config'
import type { Component } from '../../core/types'
import { rlConfigSmoke } from '../../stack/smoke'
import { ConsoleState, saveConsoleState } from './console-state'
import { ActionError, ActionResult, done, guard, release } from './result'
import { startComponent, StartCtx } from './start'

// ────────────────────────── 模式预设与开关 ──────────────────────────

export interface PresetOpts {
  /** Push：worker_server / cloudflared endpoint。留空 = 复用 rl-config 中 enabled 且 ping 通的 gpu_push。 */
  pushEndpoint?: string
  /** Push：worker_server Bearer token（显式填写时必填；复用 config 时用节点 authKey）。 */
  pushAuthKey?: string
}

/** 按 trainer 模式顺序拉起组件组合：
 *  pull = selfNode→hubServer→cloudflared→trainer；
 *  push = （ping 门 + 回写 rl-config）→ selfNode→trainer
 *         （云机自起 cloudflared；hub 直推 code.zip/job，**不启本地 hubServer/cloudflared**）；
 *  local = trainer。
 *  任一步失败即中断（已完成的组件保留，页面可单独停止）。 */
export async function startPreset(
  mode: ConsoleState['trainerPpo'],
  course: string,
  opts: PresetOpts = {},
): Promise<ActionResult> {
  guard(`preset:${mode}`)
  try {
    if (!course) throw new ActionError('需要 course（先在顶部设置课程）')
    validateCourseArg(course)
    saveConsoleState({ trainerPpo: mode, course })
    let pushNote = ''
    if (mode === 'push') {
      // ① ping 门（留空则复用 config 已启用 gpu_push）② 必要时回写 rl-config ——
      // 失败抛 ActionError，**绝不启动** trainingLoop。
      const { url, reused } = await configurePushEndpoint(
        course,
        opts.pushEndpoint ?? '',
        opts.pushAuthKey ?? '',
      )
      pushNote = reused
        ? `; 复用 rl-config gpu_push (${url}) 已 ping 通；无本地 hub-server/cloudflared`
        : `; push endpoint 已验证并回写 rl-config (${url})；无本地 hub-server/cloudflared`
    }
    const order: Component[] =
      mode === 'pull'
        ? ['selfNode', 'hubServer', 'cloudflared', 'trainingLoop']
        : mode === 'push'
          ? ['selfNode', 'trainingLoop']
          : ['trainingLoop']
    const ctx: StartCtx = { course, trainerPpo: mode }
    const detail: string[] = []
    for (const k of order) {
      const r = await startComponent(k, ctx)
      detail.push(`${k}: ${r.message}${r.detail && !r.ok ? ` — ${r.detail[0] ?? ''}` : ''}`)
      if (!r.ok) return done(false, `${mode} 预设启动中断于 ${k}`, detail)
    }
    return done(
      true,
      `已按 ${mode} 模式启动 ${order.length} 个组件 (course=${course})${pushNote}`,
      detail,
    )
  } catch (e) {
    if (e instanceof ActionError) throw e
    // configurePushEndpoint 的 ping/校验失败 → ActionError（响亮，不启动）
    throw new ActionError(e instanceof Error ? e.message : String(e))
  } finally {
    release(`preset:${mode}`)
  }
}

/** 模式开关：rl.* 键回写 rl-config.json（run_rl 真实键，下次 trainer 启动生效）；
 *  trainer.ppo 持久化到 console-state.json（pull/push/local 基建编排选择）。 */
export async function setMode(key: string, value: string): Promise<ActionResult> {
  guard(`mode:${key}`)
  try {
    if (key === 'trainer.ppo') {
      if (!['pull', 'push', 'local'].includes(value))
        throw new ActionError(`未知 trainer 模式: ${value}`)
      saveConsoleState({ trainerPpo: value as ConsoleState['trainerPpo'] })
      return done(true, `trainer 模式 = ${value}（下次启动 trainer 生效）`)
    }
    const cfg = loadConfig()
    cfg.rl = cfg.rl || {}
    const num = Number(value)
    if (value !== '0' && value !== '1') throw new ActionError(`${key} 只接受 0/1，收到: ${value}`)
    switch (key) {
      case 'rl.stream':
        cfg.rl.stream = num
        break
      case 'rl.double_buffer':
        cfg.rl.double_buffer = num
        break
      case 'rl.precollect_early':
        cfg.rl.precollect_early = num
        break
      default:
        throw new ActionError(`未知模式开关: ${key}`)
    }
    saveConfig(cfg)
    const smoke = rlConfigSmoke(cfg)
    return done(
      smoke.passed,
      `${key} = ${value}（已回写 rl-config.json，下次 trainer 启动生效）`,
      smoke.passed ? undefined : [smoke.detail ?? ''],
    )
  } finally {
    release(`mode:${key}`)
  }
}
