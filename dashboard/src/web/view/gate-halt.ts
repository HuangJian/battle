/** gate-halt.ts — 门禁停机平台开关的**展示面**常量与助手（2026-10-01）。
 *
 *  数据契约在服务端 `stack/gate-halt.ts`（那份 import `fs`，**不得**被 web 侧 import——
 *  `tests/architecture-layering.test.ts` 钉的是 import 文本，而真正受伤的是客户端 bundle：
 *  把 `node:fs` 链进浏览器包，构建期就红）。所以这里只放纯展示件：
 *
 *  · 时长档位（切「提示」时的盯盘到点时间）；
 *  · 两栏文案格式化（意图的到点时间、训练侧回执的「实际生效」）。
 */

/** 盯盘时长档位（切 notify 时选）。`hours = null` = 不限时。 */
export const GATE_HALT_DURATIONS: { hours: number | null; label: string }[] = [
  { hours: 2, label: '2 小时' },
  { hours: 4, label: '4 小时' },
  { hours: 8, label: '8 小时' },
  { hours: null, label: '不限时（记得切回停机）' },
]

/** UI 的缺省档 = 8h。服务端另有一份「API 未给 untilHours 时按 8h」的缺省
 *  （`stack/gate-halt.ts::DEFAULT_GATE_HALT_HOURS`）——那是给脚本/curl 的，两者同值但语义不同：
 *  UI 恒会显式给出选择值。 */
export const DEFAULT_GATE_HALT_HOURS: number | null = 8

/** 意图的到点时间 → 人读一行（epoch 秒；null/0 = 不限时）。
 *
 *  **到点之后**必须说实话：过期后训练侧（读时求值）已经是 halt，而意图文件里那次 `notify`
 *  还在——只显示一个过去的日期会让操作员以为「还是提示」。`nowMs` 可注入（便于用例固定时钟）。
 */
export function gateHaltUntilText(until: number | null, nowMs: number = Date.now()): string {
  if (!until) return '不限时（记得自己切回停机）'
  const at = new Date(until * 1000).toLocaleString()
  if (until * 1000 <= nowMs) return `已到点（${at}）⇒ 训练侧按 halt；切回停机即归位`
  return `到 ${at} 自动回落 halt`
}

/** 回执行 → 「实际生效」一行（`effective_mode` + 来源；直到训练侧读过才存在）。 */
export function gateHaltAppliedText(
  entry: {
    effective_mode: string
    source: string
  } | null,
): string {
  if (entry === null) return ''
  return `实际：${entry.effective_mode}（来源 ${entry.source}）`
}
