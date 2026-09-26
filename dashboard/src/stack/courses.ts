/** courses.ts — 课程元信息读取（BC 种子路径等）。
 *
 *  plan：`plan/multi-course-parallel-training.md`（P2-W3、DoD F-B6）。
 *
 *  为什么独立成模块：`hub.ts::stepTrainingLoop` 与 `console/actions.ts` 都要按课程
 *  解析 BC 种子路径，但 actions  imports hub（单向）——解析函数放这里，双方都从
 *  这里 import，不断环。`actions.ts` 重导出同名函数，老调用方零改动。
 */

import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync } from 'fs'
import path from 'path'
import { readJsoncFile } from '../core/jsonc'
import { archiveCoursesDir, curriculaDir, REPO_ROOT, weightsArchiveDir } from '../core/paths'

/** 课程 BC 种子路径（§384）：读课程 jsonc 的 `bc` 字段（相对仓库根解析）。
 *
 *  **三种情形分开处理**（2026-09-17 事故：原来三种一律静默回退 legacy，于是
 *  「课程文件语法读不了」被报成「初始权重缺失且 BC 产物不存在: tmp/ep60/…」——
 *  错误信息指向完全无关的文件，真因被藏起来，排查绕远）：
 *   ① 文件不存在（未知课程）→ legacy 硬编码（老课程兼容，行为不变）；
 *   ② **文件在、解析失败** → **抛错**并点名文件与原始报错。不许回退：回退要么拿
 *      错误的种子开腿（§384 原事故），要么报出误导性的下游错误（本次事故）；
 *   ③ 解析成功但无 `bc` 键 → legacy 硬编码（老课程兼容）。
 *
 *  解析器用 `core/jsonc.ts`（与 python `rl/jsonc.py` 同一语义）——**不再手搓
 *  「只剥整行 `//`」的弱实现**，那正是本次事故根因。 */
export function resolveCourseBc(course: string): string {
  const legacy = path.join(REPO_ROOT, 'tmp/ep60/battle2-p1bc/run/weights.json')
  const file = path.join(curriculaDir(), `${course}.jsonc`)
  let parsed: { bc?: unknown }
  try {
    parsed = readJsoncFile(file) as { bc?: unknown }
  } catch (e) {
    if ((e as NodeJS.ErrnoException)?.code === 'ENOENT') return legacy
    throw new Error(
      `课程文件解析失败: ${file}——${e instanceof Error ? e.message : String(e)}` +
        '（修 jsonc 语法；解析失败不回退 legacy 种子，见 dashboard/src/core/jsonc.ts 头注）',
    )
  }
  const bc = parsed.bc
  // 绝对路径原样返回（跨盘符的 path.relative 会产出绝对路径；Windows 上
  // path.join(repo, 'C:\\...') 会把盘符拼成非法中间段——§2026-09-13 回归）。
  if (typeof bc === 'string' && bc.length > 0)
    return path.isAbsolute(bc) ? bc : path.join(REPO_ROOT, bc)
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

// ────────────────────────── 封存起点（G4-①） ──────────────────────────

/** 归档件名：`<prefix>.it<N>.<YYYYMMDD-HHMMSS>.json`（python `backup_weights`）。 */
const ARCHIVED_WEIGHT_RE = /\.it(\d+)\.\d{8}-\d{6}\.json$/

/** 归档权限校验：路径必须**真的**在权重归档根之内（防 manifest 被手改成越界路径）。 */
function insideWeightsArchive(p: string): boolean {
  const rel = path.relative(weightsArchiveDir(), p)
  return rel !== '' && !rel.startsWith('..') && !path.isAbsolute(rel)
}

/** 封存课的某个关键轮 → **实际**权重文件绝对路径（plan/course-archive.plan.md §3.5 / G4-①）。
 *
 *  客户端只给 `{sourceCourse, it}`（**不给路径**）：路径由服务端从
 *  `archive/courses/<课>/archive-manifest.json` 自己解析——绝不信任客户端传的路径，
 *  manifest 也可能被手改，故解析结果还要过 `insideWeightsArchive`。
 *
 *  优先级：① manifest 里该 it 的**具体** `path`（无 glob 字符且文件在）；② 否则在该课归档
 *  目录里按 `*.it<it>.*.json` glob（与 python `resolve_archived_weight` 同口径：同名多份取
 *  时间戳最大那份）。都找不到 ⇒ null（调用方响亮拒绝，不退回 BC——那会静默拿错起点）。 */
export function resolveArchivedSeedPath(sourceCourse: string, it: number): string | null {
  if (!/^[A-Za-z0-9._-]+$/.test(sourceCourse) || !Number.isInteger(it)) return null
  const manifest = path.join(archiveCoursesDir(), sourceCourse, 'archive-manifest.json')
  try {
    const doc = JSON.parse(readFileSync(manifest, 'utf8')) as { weights?: unknown }
    const weights = Array.isArray(doc.weights) ? doc.weights : []
    const entry = weights.find(
      (w): w is { it?: unknown; path?: unknown } =>
        typeof w === 'object' && w !== null && (w as { it?: unknown }).it === it,
    )
    // manifest 里存的是仓根相对的 `nn-training/weights/<课>/<file>.json`。归档布局是**该课
    // 目录下平铺** ⇒ 只取 basename 再拼回 `<权重根>/<课>/`：既能在单测重定向下工作，
    // 也天然让「手改 manifest 指向越界路径」失效（拼不出 weights 根之外）。
    const base = typeof entry?.path === 'string' ? path.basename(entry.path) : ''
    if (base && !base.includes('*') && !base.includes('?')) {
      const abs = path.join(weightsArchiveDir(), sourceCourse, base)
      if (insideWeightsArchive(abs) && existsSync(abs)) return abs
    }
  } catch {
    // 没档案 / 坏 manifest ⇒ **它不是封存课**，不该作起点来源（不做 glob 兜底——否则
    // 任意一门有归档权重的活体课都能被当「封存起点」，与「从封存课取」的语义不符）。
    return null
  }
  const dir = path.join(weightsArchiveDir(), sourceCourse)
  let best: string | null = null
  try {
    for (const name of readdirSync(dir)) {
      const m = ARCHIVED_WEIGHT_RE.exec(name)
      if (m && Number(m[1]) === it && (best === null || name > best)) best = name
    }
  } catch {
    return null
  }
  return best ? path.join(dir, best) : null
}
