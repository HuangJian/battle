/** gate-halt.ts — 门禁停机模式**平台级单开关**（意图 + 回执；2026-10-01）。
 *
 *  「门禁触发时停不停」问的是**有没有人在盯盘**——是操作员此刻的状态，不是某门课的属性：
 *  课程级三写面（每课 argv / `courses.<课>.gate_halt_mode` / `<traj>/gate-halt-mode.txt`）
 *  会让同一实验的两条腿门禁行为不同（配对序列不可比）。升成平台级后：
 *
 *  ```
 *  tmp/gate-halt.json          控制台**写**、训练侧（supervisor）每轮判定**读**
 *  { "version": 1, "mode": "halt|notify", "until": <epoch 秒|null>, "by": "console", "at": … }
 *  ```
 *
 *  · `until`：**null / 缺失 = 不过期**（仅 notify 有意义）；到点由**训练侧读时求值**回落 halt
 *    （不需要任何进程定时翻牌）。
 *  · **缺省 halt**：文件缺失/坏 ⇒ 训练侧一律 halt（保守方向见 python 侧契约）。
 *  · 回执 `tmp/gate-halt.applied.json` 由训练侧写（逐课 `effective_mode` + `source`），
 *    控制台的「实际生效」栏只读它——意图文件回答不了「训练真读到了吗」。
 *
 *  **python 侧是同一份契约的唯一实现**（`nn-training/worker/gate_halt.py`：解析/裁决/原子写/
 *  回执）——本模块只做控制台这一侧：写意图、读两栏、给 UI 时长档。两边默认路径必须一致
 *  （`core/paths.ts::gateHaltPath()` ↔ python `NN_GATE_HALT`，本侧 `BCITY_GATE_HALT`）。
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'fs'
import path from 'path'
import { gateHaltAppliedPath, gateHaltPath } from '../core/paths'

export type GateHaltMode = 'halt' | 'notify'

/** notify 不带时长时的服务端缺省（小时）。缺省 8h：人走了忘切也比「不限时」安全。
 *
 *  UI 的档位列表是**另一份**（`web/view/gate-halt.ts::GATE_HALT_DURATIONS`——那份不得
 *  import 本模块：这里 import 了 `fs`，链进客户端 bundle 会在构建期炸）；UI 恒显式给出
 *  选择值，这里只服务脚本/curl 这类不给 `untilHours` 的调用方。 */
export const DEFAULT_GATE_HALT_HOURS: number | null = 8

/** 平台意图（读出来的形状；`until` null = 不过期）。 */
export interface GateHaltIntent {
  mode: GateHaltMode
  until: number | null
  by: string
  at: number | null
}

/** 回执里**一门课**的一行（字段名就是 python 写出的 snake_case——同一份 wire 契约，
 *  不做第二套命名，免得两侧各译一次译出分歧）。 */
export interface GateHaltAppliedEntry {
  effective_mode: string
  source: string
  until: number | null
  at: number | null
}

/** 控制台两栏的数据源（`/api/state.gateHalt`）。 */
export interface GateHaltView {
  /** 平台意图（缺省档：文件不存在 ⇒ null = 训练侧走缺省 halt）。 */
  intent: GateHaltIntent | null
  /** 训练侧回执（逐课实际生效值；缺/坏 ⇒ 空表，不是页面故障）。 */
  applied: Record<string, GateHaltAppliedEntry>
  /** 意图文件读不了时的人读原因（页面显因；判定不受它影响）。 */
  error?: string
}

function _msg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** 读平台意图。文件不存在 ⇒ `{intent: null}`（**不是错误**）；坏形状 ⇒ + error（绝不猜成 notify）。 */
export function readGateHaltIntent(): { intent: GateHaltIntent | null; error?: string } {
  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(gateHaltPath(), 'utf8'))
  } catch (e) {
    if ((e as NodeJS.ErrnoException)?.code === 'ENOENT') return { intent: null }
    return { intent: null, error: `门禁意图文件读失败：${_msg(e)}` }
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { intent: null, error: '门禁意图文件根不是对象' }
  }
  const o = raw as Record<string, unknown>
  const mode = o.mode === 'notify' || o.mode === 'halt' ? o.mode : null
  if (mode === null) return { intent: null, error: `门禁意图 mode 非法：${String(o.mode)}` }
  const until = typeof o.until === 'number' ? o.until : null
  return {
    intent: {
      mode,
      until,
      by: typeof o.by === 'string' ? o.by : '',
      at: typeof o.at === 'number' ? o.at : null,
    },
  }
}

/** 原子写平台意图（`tmp` + `rename`：训练侧要么读到旧的、要么读到新的，永不读半个）。
 *  返回错误文案（空 = 成功）——写失败只上屏，不是训练的故障。 */
export function writeGateHaltIntent(
  mode: GateHaltMode,
  until: number | null,
  by = 'console',
): string {
  const body =
    JSON.stringify({ version: 1, mode, until, by, at: Date.now() / 1000 }, null, 2) + '\n'
  const p = gateHaltPath()
  const tmp = path.join(path.dirname(p), `.${path.basename(p)}.${process.pid}.tmp`)
  try {
    mkdirSync(path.dirname(p), { recursive: true })
    writeFileSync(tmp, body, 'utf8')
    renameSync(tmp, p)
    return ''
  } catch (e) {
    return _msg(e)
  }
}

/** 读训练侧回执的 `courses` 段（缺/坏 ⇒ 空表：观测面坏不得把整页带崩）。 */
export function readGateHaltApplied(): Record<string, GateHaltAppliedEntry> {
  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(gateHaltAppliedPath(), 'utf8'))
  } catch {
    return {}
  }
  const courses =
    typeof raw === 'object' && raw !== null && !Array.isArray(raw)
      ? (raw as Record<string, unknown>).courses
      : null
  if (typeof courses !== 'object' || courses === null || Array.isArray(courses)) return {}
  const out: Record<string, GateHaltAppliedEntry> = {}
  for (const [course, v] of Object.entries(courses as Record<string, unknown>)) {
    if (typeof v !== 'object' || v === null || Array.isArray(v)) continue
    const e = v as Record<string, unknown>
    out[course] = {
      effective_mode: typeof e.effective_mode === 'string' ? e.effective_mode : 'halt',
      source: typeof e.source === 'string' ? e.source : '?',
      until: typeof e.until === 'number' ? e.until : null,
      at: typeof e.at === 'number' ? e.at : null,
    }
  }
  return out
}

/** 两栏视图（`/api/state` 一处组装：意图 + 回执，读失败各自降级）。 */
export function buildGateHaltView(): GateHaltView {
  const { intent, error } = readGateHaltIntent()
  const applied = readGateHaltApplied()
  return { intent, applied, ...(error ? { error } : {}) }
}

/** 意图 → 人读一行（UI 与日志共用一处措辞）。 */
export function describeGateHaltIntent(intent: GateHaltIntent | null): string {
  if (!intent) return 'halt（缺省：没有平台意图文件）'
  if (intent.mode === 'halt') return 'halt（触发即下发停机令）'
  return intent.until === null
    ? 'notify（只提示，不限时）'
    : `notify（只提示，到 ${new Date(intent.until * 1000).toLocaleString()} 自动回落 halt）`
}
