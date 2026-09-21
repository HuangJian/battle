/** kickstart-receipt.ts — 开课回执里的「起点-基线对照行」（plan/accident.plan.md §5.3，2026-09-21）。
 *
 *  **要治的那件事**：C 双臂从收敛权重（it175）以 `kk(1) = 1` 满额缰绳复活，it1 kl=0.90、
 *  锚主导更新连烧 30 轮；两臂 ×12h 只换来「无结论」。开课前**盘上就已经有**的那个事实——
 *  「本腿恢复的权重已经在课程 bc 权重那一档（甚至更低），还要拿满额锚去拉」——当时没有
 *  任何人被告知（操作员的动作是「点开课」，回执里只有 hub 模式与预设步骤）。
 *
 *  训练侧已在启动期打一行（`rl/loop_core._kickstart_baseline_row`）；本模块把**同一对数字**
 *  搬到操作员真正看得见的地方：控制台的开课回执（`server/actions/course-lifecycle.ts`）。
 *
 *  **同源纪律**（与 §5.2/§2A 同一条）：
 *   · 读数只有一套：`tmp/<课>/eval_log.jsonl`
 *       - 基线 = 文件里**最后一条 it0 行**的 `winRate`（= 课程 bc 权重的干净评估 =
 *         缰绳锚定的**同一份**权重；训练侧 `_maybe_dispatch_baseline_eval` 补派）
 *       - 起点 = 文件里**最后一条 it>0 行**的 `winRate`（= 本腿恢复用的权重在前一腿的末次读数）
 *     与 python `kickstart_burn.baseline_reading` 同义，取法也同（文件序末条，不是 max iter：
 *     续腿重评同一 iter 时那一条落在更后面 = 更新的事实）。
 *   · 阈值只有一套：噪声带/点数 = `courses.<课>.kickstart_burn.{margin_pp,points}`
 *     （python 读面 `kickstart_burn.burn_overrides`；缺席即本文件的镜像常量）。镜像常量由
 *     `tests/kickstart-receipt.test.ts` 对着 python 源码核对——漂了即红。
 *
 *  **观测永不阻断开课**（训练侧那条对照行同一纪律）：任何读取失败 → 一行说明，不抛。
 *  读面在**开课**这一下（弹窗动作，非轮询路径）：18MB 的账本走子串预滤（逐局行占体积 99%），
 *  不调 `server/iters.readEvalSummaries`——那个聚合要 parse 每一条逐局行。
 */

import { readFileSync } from 'fs'
import path from 'path'
import { readJsoncFile } from '../core/jsonc'
import { curriculaDir, tmpLogsDir } from '../core/paths'
import type { RlConfig } from '../core/types'

// ────────────────────────── 镜像常量（权威在 python，测试核对） ──────────────────────────

/** 干烧熔断的噪声带（pp）。权威：`nn-training/rl/kickstart_burn.py::BURN_MARGIN_PP`。 */
export const BURN_MARGIN_PP = 5.0
/** 干烧熔断的连续点数。权威：`nn-training/rl/kickstart_burn.py::BURN_POINTS`。 */
export const BURN_POINTS = 3
/** 「缺省初值大到该被警告」的阈值。权威：`nn-training/rl/loop_core.py::KICKSTART_DEFAULT_WARN`。 */
export const KICKSTART_DEFAULT_WARN = 0.5

/** 账本里干净评估汇总行的 `event` 字面量（与 python `read_trend_rows` 同一筛选键）。 */
const EVAL_SUMMARY_EVENT = '"eval_summary"'

// ────────────────────────── 账本读数 ──────────────────────────

export interface LedgerReadings {
  /** 账本文件是否存在（新腿还没跑过 = 合法状态，不是错误）。 */
  exists: boolean
  /** it0 行读数（最后一条）；null = 无 it0 行（bc 基线评估还没跑）。 */
  base: number | null
  /** it0 行条数（多腿各补派一次基线；>1 说明这课重启过）。 */
  baseRows: number
  /** it>0 行读数（最后一条）；null = 本课账本还没有评估点。 */
  last: number | null
  /** 最后一条 it>0 行的 iter。 */
  lastIter: number | null
}

/** 读账本里的两个读数（起点 / 基线）。文件缺失、坏行、非数值读数一律跳过。 */
export function readLedger(logPath: string): LedgerReadings {
  const out: LedgerReadings = {
    exists: false,
    base: null,
    baseRows: 0,
    last: null,
    lastIter: null,
  }
  let text: string
  try {
    text = readFileSync(logPath, 'utf8')
  } catch {
    return out
  }
  out.exists = true
  for (const line of text.split('\n')) {
    // 预滤：一场一行的 `eval` 事件占文件体积 99%，先按子串跳过再 JSON.parse
    // （18MB 账本 ≈ 1.4 万 summary 行 + 1.4 万逐局行）。
    if (!line.includes(EVAL_SUMMARY_EVENT)) continue
    let r: Record<string, unknown>
    try {
      r = JSON.parse(line) as Record<string, unknown>
    } catch {
      continue // 半截行（进程被杀在写中途）——跳过
    }
    if (r.event !== 'eval_summary') continue
    const it = r.iter
    const wr = r.winRate
    if (!Number.isInteger(it) || typeof wr !== 'number' || !Number.isFinite(wr)) continue
    if ((it as number) <= 0) {
      out.base = wr
      out.baseRows += 1
      continue
    }
    out.last = wr
    out.lastIter = it as number
  }
  return out
}

// ────────────────────────── 旋钮与阈值（课程文件 + rl-config） ──────────────────────────

export interface KickstartKnobs {
  /** 缰绳是否开（课程 `kickstart_ref`；缺席 = 关）。 */
  ref: boolean
  /** kk 初值（课程 `kickstart_init` > rl-config `rl.kickstart_kl` > CLI 缺省 1.0）。 */
  init: number
  /** 初值来源（**必须说清**：C 事故就是「拿缺省 kk=1 复活」）。 */
  source: string
  /** 课程显式声明了初值却没开缰绳 ⇒ python `apply_course` 启动期拒启（回执先说一句）。 */
  contradiction: boolean
}

/** 本课的 kickstart 旋钮（读课程 jsonc；读不了按缺省——观测不阻断）。 */
export function kickstartKnobs(course: string, cfg: RlConfig | null): KickstartKnobs {
  let file: Record<string, unknown> = {}
  try {
    file = readJsoncFile(path.join(curriculaDir(), `${course}.jsonc`)) as Record<string, unknown>
  } catch {
    /* 课程文件缺失/解析失败：全部按缺省（真正的解析失败判据在 python 开课/`resolveCourseBc`） */
  }
  const ref = file.kickstart_ref === true
  const declaredRaw = file.kickstart_init
  const declared =
    typeof declaredRaw === 'number' && Number.isFinite(declaredRaw) ? declaredRaw : null
  const cfgKk = cfg?.rl?.kickstart_kl
  const cfgNum = typeof cfgKk === 'number' && Number.isFinite(cfgKk) ? cfgKk : null
  const init = declared ?? cfgNum ?? 1.0
  // 来源串统一「<值从哪来> —— <为什么>」（回执里插在 `来源：` 之后，不再套一层括号）。
  const source =
    declared !== null
      ? `课程 kickstart_init=${declared}`
      : cfgNum !== null
        ? `rl-config rl.kickstart_kl=${cfgNum} —— 课程未写 kickstart_init`
        : '缺省 1.0 —— 课程未写 kickstart_init、rl-config 未设 rl.kickstart_kl'
  return {
    ref,
    init,
    source,
    contradiction: declared !== null && declared > 0 && !ref,
  }
}

/** 本课的干烧熔断阈值（`courses.<课>.kickstart_burn.*`；缺席 = python 常量）。 */
export function burnThresholds(
  course: string,
  cfg: RlConfig | null,
): { marginPp: number; points: number } {
  const kb = cfg?.courses?.[course]?.kickstart_burn
  const m = kb?.margin_pp
  const p = kb?.points
  return {
    marginPp: typeof m === 'number' && Number.isFinite(m) ? m : BURN_MARGIN_PP,
    points: typeof p === 'number' && Number.isInteger(p) && p > 0 ? p : BURN_POINTS,
  }
}

// ────────────────────────── 对照行（纯函数，可单测） ──────────────────────────

export interface BaselineInput {
  knobs: KickstartKnobs
  readings: LedgerReadings
  burn: { marginPp: number; points: number }
  /** 账本路径（只在「暂无」那几行里露出来给操作员看；由调用方注入 ⇒ 本函数保持纯）。 */
  logPath: string
}

const pct = (v: number): string => `${(v * 100).toFixed(1)}%`
const pp = (v: number): string => `${v >= 0 ? '+' : ''}${v.toFixed(1)}pp`
const num = (v: number): string => `${v}`

/** 回执里的对照行（**纯函数**：数字进、文字出，全部分支可断言）。 */
export function composeBaselineLines(inp: BaselineInput): string[] {
  const { knobs: k, readings: r, burn, logPath } = inp
  const out: string[] = []

  // ① 缰绳 + 初值来源（C 事故的病灶：人以为在勒缰绳，其实拿的是缺省 kk=1）
  if (k.contradiction) {
    out.push(
      '★ 本课会在启动期被**拒启**（python `apply_course` 自洽闸）：声明了 kickstart_init ' +
        '但 kickstart_ref 未开 —— 初值没有消费方；先改课程文件再开课。',
    )
  }
  if (k.ref) {
    const burnNote = `干烧熔断：连续 ${burn.points} 个评估点低于基线 ${burn.marginPp}pp 即停腿。`
    out.push(
      k.init > 0
        ? `kickstart 缰绳：开（课程 kickstart_ref）；kk 初值 ${num(k.init)}（来源：${k.source}）——${burnNote}`
        : `kickstart 缰绳：课程说开，但 kk 初值 0（来源：${k.source}）⇒ 锚**实际不生效**`,
    )
  } else {
    out.push('kickstart 缰绳：关（课程未设 kickstart_ref）——本腿不受 bc 锚；下面一行仍是监控参照')
  }

  // ② 对照行（起点 vs 基线）
  const gap = r.base !== null && r.last !== null ? (r.last - r.base) * 100 : null
  const baseText = r.base !== null ? `基线 ${pct(r.base)}（it0 = 课程 bc 权重）` : null
  const lastText =
    r.last !== null ? `起点 ${pct(r.last)}（账本末次评估 it${r.lastIter} = 本腿恢复的权重）` : null
  if (!r.exists) {
    out.push(`起点-基线对照：暂无（账本还没建：${logPath}）`)
  } else if (baseText === null && lastText === null) {
    out.push(`起点-基线对照：暂无（账本里既无 it0 行也无 it>0 评估点：${logPath}）`)
  } else if (baseText === null) {
    out.push(
      `起点-基线对照：${lastText}；基线缺（账本无 it0 行——本腿还没跑过 bc 基线评估，` +
        `训练侧会在首轮后补派）`,
    )
  } else if (lastText === null) {
    out.push(`起点-基线对照：${baseText}；起点缺（账本暂无 it>0 评估点 = 新起点）`)
  } else {
    out.push(`起点-基线对照：${lastText} vs ${baseText} → 差 ${pp(gap as number)}`)
  }

  // ③ ★ 响亮确认：只在本腿真的要锚（ref 且 kk 有效且够大）时才喊——C 例正是「差 0.5pp 配 kk=1」
  if (k.ref && k.init >= KICKSTART_DEFAULT_WARN && gap !== null) {
    if (gap < -burn.marginPp) {
      out.push(
        `★ 起点已低于基线 ${(-gap).toFixed(1)}pp（噪声带 ±${burn.marginPp}pp）却配 kk=${num(k.init)}：` +
          `本腿开跑就在基线下方，干烧熔断从第一个评估点就起算（连续 ${burn.points} 点 ⇒ ` +
          `停腿告警「疑似回锚/塌陷」）——确认这是有意为之再开课。`,
      )
    } else if (Math.abs(gap) < burn.marginPp) {
      out.push(
        `★ 起点与基线只差 ${Math.abs(gap).toFixed(1)}pp（噪声带 ±${burn.marginPp}pp）却配 kk=${num(k.init)}：` +
          `满额锚会先把起点拉回它自己那一档（C 事故：it1 kl=0.90 连烧 30 轮，两臂 ×12h 无结论）` +
          `——确认这是有意为之再开课。`,
      )
    }
  }
  return out
}

/** 开课回执用的一整段（IO + 组装；**永不抛**——观测不阻断开课）。 */
export function kickstartReceipt(course: string, cfg: RlConfig | null): string[] {
  try {
    const logPath = path.join(tmpLogsDir(), course, 'eval_log.jsonl')
    return composeBaselineLines({
      knobs: kickstartKnobs(course, cfg),
      readings: readLedger(logPath),
      burn: burnThresholds(course, cfg),
      logPath,
    })
  } catch (e) {
    return [`（kickstart 对照行生成失败：${e instanceof Error ? e.message : String(e)}）`]
  }
}
