/** courses.ts — 课程元信息读取（BC 种子路径等）。
 *
 *  plan：`plan/multi-course-parallel-training.md`（P2-W3、DoD F-B6）。
 *
 *  为什么独立成模块：`hub.ts::stepTrainingLoop` 与 `console/actions.ts` 都要按课程
 *  解析 BC 种子路径，但 actions  imports hub（单向）——解析函数放这里，双方都从
 *  这里 import，不断环。`actions.ts` 重导出同名函数，老调用方零改动。
 */

import { copyFileSync, existsSync, mkdirSync, readFileSync } from 'fs'
import path from 'path'
import { curriculaDir, REPO_ROOT } from './paths'

/** 课程 BC 种子路径（§384）：读课程 jsonc 的 `bc` 字段（相对仓库根解析）；
 *  文件缺失/解析失败/无 bc 键时回退 legacy 硬编码（旧课程兼容）。 */
export function resolveCourseBc(course: string): string {
  const legacy = path.join(REPO_ROOT, 'tmp/ep60/battle2-p1bc/run/weights.json')
  try {
    const raw = readFileSync(path.join(curriculaDir(), `${course}.jsonc`), 'utf-8')
    // JSONC 容尾逗号：oxfmt 给 curricula/*.jsonc 加的尾逗号是合法 JSONC、非法 JSON。
    // 不剥掉 → JSON.parse 抛错 → 静默回退 legacy 种子路径（§384 的事故正是这个
    // 静默回退：读不到课程 bc 就拿旧权重开腿）。剥完再解析，解析失败仍回退。
    const stripped = raw
      .split('\n')
      .filter((l) => !l.trimStart().startsWith('//'))
      .join('\n')
      .replace(/,(\s*[}\]])/g, '$1')
    const bc: unknown = (JSON.parse(stripped) as { bc?: unknown }).bc
    if (typeof bc === 'string' && bc.length > 0) return path.join(REPO_ROOT, bc)
  } catch {
    /* 回退 legacy */
  }
  return legacy
}

/** 课程种类判定（BC 整合 2026-09-13）：`.bc.jsonc` = BC 课程（编排器 run_bc.py），
 *  其余 `.jsonc` = RL 课程（run_rl.py）。控制台按此分流 spec / 冒烟 / 种子播种。 */
export function isBcCourse(course: string): boolean {
  if (!course) return false
  try {
    return existsSync(path.join(curriculaDir(), `${course}.bc.jsonc`))
  } catch {
    return false
  }
}

/** 按课程播种初始权重（console 与 hub 双路的实际 seeding 路径，DoD F-B6）。
 *
 *  `sha256(weightsPath) == sha256(课程 bc 声明的文件)` 由调用链保证——这里是唯一的
 *  复制点（此前提下两处手写 copy，各自可能漂）。BC 文件缺失 → 抛错（fail loud，
 *  与 console smokeTrain 对齐）：静默拿旧权重开腿是 §384 事故的重演，绝不回退。
 */
export function seedWeightsFromBc(course: string, weightsPath: string): string {
  const bcPath = resolveCourseBc(course)
  if (!existsSync(bcPath)) {
    throw new Error(`初始权重缺失且 BC 产物不存在: ${bcPath} (course=${course})`)
  }
  mkdirSync(path.dirname(weightsPath), { recursive: true })
  copyFileSync(bcPath, weightsPath)
  return bcPath
}
