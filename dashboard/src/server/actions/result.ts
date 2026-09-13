/** result.ts — 动作层公共契约：ActionError / busy 互斥集合 / ActionResult 构造。 */
import { warn as logWarn } from '../../core/log'
import { isCourseComponent } from '../../core/registry'
import type { Component } from '../../core/types'

export class ActionError extends Error {}

/** 进行中的动作 key（`start:trainingLoop` / `node:mac` / `mode:rl.stream`）。 */
export const busy = new Set<string>()

/** busy 上锁时刻（自愈用，见 sweepStaleBusy）。只有经 guard 加的键才有记录；
 *  测试/外部手动塞进 busy 的键无记录，不参与 TTL 清扫（语义不变）。 */
export const busySince = new Map<string, number>()
/** 单动作 TTL：最长合法动作是 trainer 启动的 20s 等日志 + 停机恢复，冒烟最多跑一局
 *  仿真——5 分钟是宽裕上界。超过即视为「漏 release」，自动解锁。 */
const BUSY_TTL_MS = 5 * 60 * 1000

/** 清扫过期 busy 键：guard 释放错键/异常路径漏释放时，组件会被永久锁死（只能重启
 *  控制台）——2026-09-14 事故：start/stop/smoke 的 release 用旧无课程键，导致
 *  trainingLoop 每次启动后 `start:trainingLoop:<课>` 永久残留，之后一律 409。
 *  TTL 是第二道防线：即使再出现漏释放，5 分钟后自动恢复，无需重启进程。 */
function sweepStaleBusy(now = Date.now()): void {
  for (const [k, at] of busySince) {
    if (now - at <= BUSY_TTL_MS) continue
    busy.delete(k)
    busySince.delete(k)
    logWarn(`[console] busy 键 ${k} 超过 ${BUSY_TTL_MS / 1000}s 未释放——自动解锁（疑似漏 release）`)
  }
}

export function guard(key: string): void {
  sweepStaleBusy()
  if (busy.has(key)) throw new ActionError('动作进行中，请稍候')
  busy.add(key)
  busySince.set(key, Date.now())
}
export function release(key: string): void {
  busy.delete(key)
  busySince.delete(key)
}

/** 动作 busy 键（S10/P5-W3）：按课程键控的组件带 course——两课可同时启/停/冒烟同一
 *  组件类型，不再互相 409；selfNode（全局单例）与无课程调用沿用旧键（默认行为零变化）。
 *  导出给 api 层做组件 busy 展示——**必须与动作实际加的键同源**，否则页面显示
 *  「未忙碌」而服务端 409（2026-09-14 事故的同源根因）。 */
export function busyKey(op: string, key: Component, course = ''): string {
  return isCourseComponent(key) && course ? `${op}:${key}:${course}` : `${op}:${key}`
}

/** 组件是否正在执行动作（页面禁用按钮 = 服务端 409 判定，同源）。 */
export function componentBusy(key: Component, course = ''): boolean {
  return (
    busy.has(busyKey('start', key, course)) ||
    busy.has(busyKey('stop', key, course)) ||
    busy.has(busyKey('smoke', key, course))
  )
}

export interface ActionResult {
  ok: boolean
  message: string
  /** 逐项明细（冒烟子项 / 预设启动各步）。 */
  detail?: string[]
}

export function done(ok: boolean, message: string, detail?: string[]): ActionResult {
  return { ok, message, ...(detail && detail.length > 0 ? { detail } : {}) }
}
