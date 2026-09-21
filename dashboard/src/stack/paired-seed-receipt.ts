/** paired-seed-receipt.ts — 开课回执里的「配对 rotateSeed 核对行」（plan/accident.plan.md §2.5）。
 *
 *  **要治的形态**（2026-09-21 凌晨）：配对要求两臂同 rotateSeed，但值靠人手在命令行传——
 *  第一次就传漏/传错（1789926833 vs 1789926915，相差 82 秒抖动）⇒ 两臂跑在不同种子流上、
 *  配对失败返工。修法（§2.3）：V 写进**课程文件**（两门配对课各写同一把 `paired_rotate_seed`）。
 *
 *  **配对在本仓的机器含义 = 「声明了同一把 V」**（不需要第二份配对登记表：谁是兄弟是课程
 *  设计事实，住课程文件）。本模块把这个事实在**开课那一刻**摆出来：
 *    · 本课声明了哪把 V、同 V 的其它课程是谁；
 *    · 每门兄弟课账本末条 `run_start.rotateSeed` 是否就是那把 V（≠ ⇒ ★ 那一臂上次不在同一
 *      把 V 上；无账本 ⇒ 还没跑过）；
 *    · 声明了却没有对端 ⇒ ★ 配对无对端（不许按配对口径结算）。
 *
 *  与训练侧 `rl/paired.py` 同一口径、同一措辞（两侧都是「读 + 判 + 组装」，判据只有一份：
 *  「声明 = `paired_rotate_seed`」「对端 = 同 V 课程」「实测 = 账本末条 run_start」）。
 *
 *  **为什么这里只报不拦**：跨臂比对读的是**历史累积**的账本——对端刚开课还没写 run_start、
 *  或对端上一腿用的是旧 V，都会让「不等」成立而并没有错配；据此拒开课会挡住正当的腿。
 *  真正的硬闸在训练侧启动期（本课声明 ≠ 实际生效 ⇒ 拒启，见 `rl/loop_core.py`），
 *  而本屏的价值是：**别在凌晨点完开课就走，让错配跑 80 轮**。
 *
 *  读取失败一律降级为一行说明，**永不阻断开课**（与 §5.3 对照行同一纪律）。
 */

import { readFileSync, readdirSync } from 'fs'
import path from 'path'
import { readJsoncFile } from '../core/jsonc'
import { curriculaDir, tmpLogsDir } from '../core/paths'

/** 课程键名（与 python `rl/paired.py::PAIRED_KEY`、`CourseConfig.paired_rotate_seed` 同名）。 */
export const PAIRED_KEY = 'paired_rotate_seed'

/** 账本里要读的事件名（`training_log.jsonl` 的启动事件，与 python 侧同字面量）。 */
const RUN_START_EVENT = 'run_start'

/** 本课**显式声明**的配对 V；未声明 → null。 */
export function declaredPairedSeed(course: string): number | null {
  try {
    const raw = readJsoncFile(path.join(curriculaDir(), `${course}.jsonc`)) as Record<
      string,
      unknown
    >
    const v = raw[PAIRED_KEY]
    return typeof v === 'number' && Number.isFinite(v) ? v : null
  } catch {
    return null // 课程文件缺失/读不了 ⇒ 未声明（合法性由 python load_course 负责响亮）
  }
}

/** 课程目录里**声明了同一把 V** 的其它课程（升序；坏文件跳过——别让它拖垮核对）。 */
export function pairedCourses(seed: number, self = ''): { course: string; seed: number }[] {
  const out: { course: string; seed: number }[] = []
  try {
    for (const name of readdirSync(curriculaDir())) {
      if (!name.endsWith('.jsonc') || name.endsWith('.bc.jsonc')) continue
      const course = name.slice(0, -'.jsonc'.length)
      if (self && course === self) continue
      let text: string
      try {
        text = readFileSync(path.join(curriculaDir(), name), 'utf8')
      } catch {
        continue
      }
      if (!text.includes(PAIRED_KEY)) continue // 子串预滤：解析成本只花在候选上
      let v: unknown
      try {
        v = (readJsoncFile(path.join(curriculaDir(), name)) as Record<string, unknown>)[PAIRED_KEY]
      } catch {
        continue
      }
      if (typeof v === 'number' && Number.isFinite(v) && v === seed) out.push({ course, seed: v })
    }
  } catch {
    return []
  }
  return out.sort((a, b) => a.course.localeCompare(b.course))
}

/** 课账本里**最后一条** `run_start` 的 rotateSeed；无账本/无该事件 → null。 */
export function latestRunStartSeed(course: string): number | null {
  let text: string
  try {
    text = readFileSync(path.join(tmpLogsDir(), course, 'training_log.jsonl'), 'utf8')
  } catch {
    return null
  }
  let seed: number | null = null
  for (const line of text.split('\n')) {
    if (!line.includes(RUN_START_EVENT)) continue // 子串预滤：账本很大，只关心一种事件
    let r: Record<string, unknown>
    try {
      r = JSON.parse(line) as Record<string, unknown>
    } catch {
      continue
    }
    if (r.event !== RUN_START_EVENT) continue
    const v = r.rotateSeed
    if (typeof v === 'number' && Number.isFinite(v)) seed = v // 末条为准（续跑会再写一条）
  }
  return seed
}

export interface PairedInput {
  declared: number | null
  siblings: { course: string; seed: number }[]
  siblingSeeds: { course: string; seed: number | null }[]
}

/** 回执行（**纯函数**：数字进、文字出；全部分支可断言）。 */
export function composePairedLines(inp: PairedInput): string[] {
  const { declared, siblings, siblingSeeds } = inp
  if (declared === null) {
    return [
      '配对 rotateSeed：本课未声明 paired_rotate_seed ⇒ 按**单腿**口径' +
        '（配对课程应各写同一把 V；写进课程文件后不再经手指传）',
    ]
  }
  const out = [`配对 rotateSeed：V=${declared}（课程声明 paired_rotate_seed）`]
  if (siblings.length === 0) {
    out.push(
      `★ 声明了 V=${declared} 但**没有其它课程**声明同一把 V —— 配对无对端` +
        '（另一臂还没写上？还是本腿要被当单腿跑？）⇒ 补齐对端前不许按配对口径结算。',
    )
    return out
  }
  out.push(`同 V 课程 = ${siblings.map((s) => s.course).join(', ')}`)
  for (const s of siblingSeeds) {
    if (s.seed === null) out.push(`配对各臂：${s.course} —— 账本无 run_start（还没跑过）`)
    else if (s.seed === declared)
      out.push(`配对各臂：${s.course} 账本 run_start.rotateSeed=${s.seed} ✓ 同 V`)
    else
      out.push(
        `★ 配对各臂：${s.course} 账本 run_start.rotateSeed=${s.seed} ≠ V=${declared}` +
          ' —— 那一臂上一次不在同一把 V 上（陈旧账本 / 未按课程文件起跑？）' +
          '该臂的读数不能按配对口径结算。',
      )
  }
  return out
}

/** 开课回执用的一整段（IO + 组装；**永不抛**）。 */
export function pairedSeedReceipt(course: string): string[] {
  try {
    const declared = declaredPairedSeed(course)
    const siblings = declared === null ? [] : pairedCourses(declared, course)
    return composePairedLines({
      declared,
      siblings,
      siblingSeeds: siblings.map((s) => ({
        course: s.course,
        seed: latestRunStartSeed(s.course),
      })),
    })
  } catch (e) {
    return [`（配对 rotateSeed 核对失败：${e instanceof Error ? e.message : String(e)}）`]
  }
}
