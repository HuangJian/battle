/** registry.ts — 受管进程 PID 账本（按组件分文件，天然无并行注册竞态，§339）。

 *  账本文件：tmp/training-start/registry.json（单文件足够——本工具链
 *  所有 spawn 走监督器串行登记，不再有 hub-start 并行阶段互相覆盖的问题）。
 *  legacy：hub-start 时代的 tmp/hub-start/registry.<name>.json 在
 *  load/clear 时一并消费，保证旧账本里的进程也能被 --kill 收编。
 *
 *  ── 多课程形状（plan multi-course-parallel-training §1.4，P1b） ──
 *  课程 = 并行单元，故 hubServer/cloudflared/trainingLoop/workerServe 四条按课程键控：
 *    `hubServers: Record<course, Entry>` 等；`selfNode` 保持单例（agent 全局一份）。
 *  旧扁平单键（`hubServer`/…）**已在 P5 移除读写**（R1 读兼容 + R2 删键）：
 *  `loadRegistry()` 每次加载都会把扁平键**一次性搬迁**进 per-course 表再删键
 *  （`migrateFlatCourseEntries`）——不搬就删 = 线上旧进程永久失监督，静默失监督是事故。
 *  写入端**只走** `saveCourseComponent` / `clearCourseComponent`（+ `saveAnyComponent`
 *  门面）；`saveComponent`/`clearComponent` 只剩 selfNode（`SingletonComponent`，
 *  类型层面挡掉课程组件——约定 + P0 门禁②守枚举路径）。
 */

import { readFileSync, unlinkSync, writeFileSync } from 'fs'
import path from 'path'
import { LOG_DIR, START_LOG_DIR, consoleStatePath } from './paths'
import { log } from './log'
import type {
  Component,
  LegacyFlatRegistry,
  Registry,
  RegistryEntry,
} from './types'

/** 账本路径（每次调用现取）。默认 tmp/training-start/registry.json；
 *  BCITY_REGISTRY_FILE 显式指定时用它——单测把账本指向临时目录，
 *  绝不让测试写进线上账本（2026-09-09 事故：exit-watchdog 测试的默认真实 io
 *  把 fixture PID 1/7 覆写了运行中的 registry.json，控制台全组件误报「已退出」）。 */
function registryPath(): string {
  return process.env.BCITY_REGISTRY_FILE ?? path.join(START_LOG_DIR, 'registry.json')
}

/** hub-start 旧账本目录（legacy 迁移读取）。 */
const LEGACY_DIR = path.join(LOG_DIR, 'hub-start')
const LEGACY_COMPS: Component[] = ['selfNode', 'hubServer', 'cloudflared', 'trainingLoop']

/** 单例组件（agent 全局一份，不按课程键控）。 */
export const SINGLETON_COMPONENTS = ['selfNode'] as const
export type SingletonComponent = (typeof SINGLETON_COMPONENTS)[number]
/** 按课程键控的组件（顺序即遍历顺序：hub 先于 trainer——M7）。 */
export const COURSE_COMPONENTS = [
  'hubServer',
  'cloudflared',
  'workerServe',
  'trainingLoop',
] as const
export type CourseComponent = (typeof COURSE_COMPONENTS)[number]

type PluralKey = 'hubServers' | 'cloudflareds' | 'workerServes' | 'trainingLoops'
const PLURAL: Record<CourseComponent, PluralKey> = {
  hubServer: 'hubServers',
  cloudflared: 'cloudflareds',
  workerServe: 'workerServes',
  trainingLoop: 'trainingLoops',
}

export function isCourseComponent(key: Component): key is CourseComponent {
  return (COURSE_COMPONENTS as readonly string[]).includes(key)
}

/** 账本条目 + 归属（`course` 空串 = 旧无课程条目）。 */
export interface WatchedEntry {
  key: Component
  course: string
  entry: RegistryEntry
}

// ────────────────────────── 读 ──────────────────────────

/** 控制台当前课程（唯一用于**旧账本回填**的兜底；读不到 → 空串）。 */
function consoleCourse(): string {
  try {
    const s = JSON.parse(readFileSync(consoleStatePath(), 'utf-8')) as { course?: unknown }
    return typeof s.course === 'string' ? s.course : ''
  } catch {
    return ''
  }
}

/** 一次性搬迁 + 回填（F-A4 / R2）：把旧扁平单键条目搬进 per-course 表，再删掉扁平键。
 *
 *  旧条目缺 `course` 时取 console-state 的当前课程、缺 `slot` 时取 0——旧单课时代恒为
 *  slot0，故补值是确定性的正确值；不补则 `entryForCourse`/`restartSpecFor` 的
 *  fail-closed（无 course → 查不到，M5）会让线上正在跑的旧进程**永久失去监督**——
 *  静默失监督是事故，自愈才是本迁移的目的。搬迁结果落盘一次，之后不再重复（幂等）。
 *
 *  per-course 表已有同课条目时**保留新条目**（新写入路径的数据更新），只丢陈旧扁平键。 */
function migrateFlatCourseEntries(reg: Registry): boolean {
  const legacy = reg as Registry & LegacyFlatRegistry
  const fallback = consoleCourse()
  let changed = false
  for (const key of COURSE_COMPONENTS) {
    const e = legacy[key]
    if (!e || typeof e.pid !== 'number') continue
    const course = typeof e.course === 'string' ? e.course : fallback
    const plural = PLURAL[key]
    const map = (reg[plural] ??= {})
    if (!map[course]) {
      map[course] = { ...e, course, slot: e.slot ?? 0 }
      log(
        `[registry] 旧账本搬迁: ${key} (PID ${e.pid}) → ${plural}[${JSON.stringify(course)}] slot=${e.slot ?? 0}（一次性迁移 R2）`,
      )
    }
    delete legacy[key]
    changed = true
  }
  return changed
}

export function loadRegistry(): Registry {
  const reg: Registry = {}
  try {
    Object.assign(reg, JSON.parse(readFileSync(registryPath(), 'utf-8')) as Registry)
  } catch {
    /* not started */
  }
  // 旧扁平键的唯一入口：registry.json 的历史键 + hub-start 分文件账本（一次性收编旧进程）。
  // 两者都在 `migrateFlatCourseEntries` 里搬进 per-course 表后删除——此处只汇拢，不做读兼容。
  const legacy = reg as Registry & LegacyFlatRegistry
  for (const name of LEGACY_COMPS) {
    if (name === 'selfNode' ? reg.selfNode : legacy[name]) continue
    try {
      const e = JSON.parse(
        readFileSync(path.join(LEGACY_DIR, `registry.${name}.json`), 'utf-8'),
      ) as RegistryEntry
      if (e && typeof e.pid === 'number') {
        if (name === 'selfNode') reg.selfNode = e
        else legacy[name] = e
      }
    } catch {
      /* absent */
    }
  }
  if (migrateFlatCourseEntries(reg)) {
    try {
      saveRegistry(reg)
    } catch {
      /* best-effort：回填落盘失败不阻断本次读取 */
    }
  }
  return reg
}

/** 严格按课取条目（**重建/监督**路径；查不到 = undefined，绝不用全局状态猜——M5）。
 *  课程组件恒读 per-course 表（course 为空串时读 `course=''` 槽——无课程≠猜课程）；
 *  单例组件读同名扁平键。旧扁平键已由 `migrateFlatCourseEntries` 搬空，这里不再兜底（R2）。 */
export function entryForCourse(
  reg: Registry,
  key: Component,
  course = '',
): RegistryEntry | undefined {
  return isCourseComponent(key) ? reg[PLURAL[key]]?.[course] : reg[key]
}

/** 有序三元组 `(key, course, entry)`——**枚举账本的唯一路径**（门禁②）。
 *  顺序：selfNode → 每课程内 hubServer/cloudflared/workerServe/trainingLoop
 *  （同课 hub 先于 trainer，M7；课程名排序保证稳定）。旧扁平键不再枚举（R2 已搬迁）。 */
export function registryTriples(reg: Registry): WatchedEntry[] {
  const out: WatchedEntry[] = []
  if (reg.selfNode) out.push({ key: 'selfNode', course: '', entry: reg.selfNode })
  const courses = new Set<string>()
  for (const key of COURSE_COMPONENTS) {
    for (const c of Object.keys(reg[PLURAL[key]] ?? {})) courses.add(c)
  }
  for (const c of [...courses].sort()) {
    for (const key of COURSE_COMPONENTS) {
      const e = reg[PLURAL[key]]?.[c]
      if (e) out.push({ key, course: c, entry: e })
    }
  }
  return out
}

/** 账本里全部登记的条目（legacy 合并后）——三元组形态，遍历统一走这里。 */
export function registryComponents(): WatchedEntry[] {
  return registryTriples(loadRegistry())
}

/** 某课程的全部条目（按 COURSE_COMPONENTS 顺序）。 */
export function courseComponents(course: string): Array<[CourseComponent, RegistryEntry]> {
  const reg = loadRegistry()
  const out: Array<[CourseComponent, RegistryEntry]> = []
  for (const key of COURSE_COMPONENTS) {
    const e = reg[PLURAL[key]]?.[course]
    if (e) out.push([key, e])
  }
  return out
}

// ────────────────────────── 写 ──────────────────────────

export function saveRegistry(reg: Registry): void {
  writeFileSync(registryPath(), JSON.stringify(reg, null, 2), 'utf-8')
}

/** 登记/更新一个**按课程**键控的组件条目（P1b 起唯一的多课写入路径）。 */
export function saveCourseComponent(
  key: CourseComponent,
  course: string,
  entry: RegistryEntry,
): void {
  const reg = loadRegistry()
  const plural = PLURAL[key]
  reg[plural] = { ...(reg[plural] ?? {}), [course]: { ...entry, course } }
  saveRegistry(reg)
}

/** 清除一个按课程键控的条目（其余课程/组件登记不受影响）。 */
export function clearCourseComponent(key: CourseComponent, course: string): void {
  const reg = loadRegistry()
  const plural = PLURAL[key]
  const map = reg[plural]
  if (!map || !(course in map)) return
  delete map[course]
  saveRegistry(reg)
}

/** 登记或更新**单例**组件条目（selfNode；类型层面拒绝课程组件——多课一律
 *  `saveCourseComponent`）。 */
export function saveComponent(name: SingletonComponent, entry: RegistryEntry): void {
  const reg = loadRegistry()
  reg[name] = entry
  saveRegistry(reg)
}

/** 清除**单例**组件条目（停止后；其余组件登记不受影响）。 */
export function clearComponent(name: SingletonComponent): void {
  const reg = loadRegistry()
  if (!reg[name]) return
  delete reg[name]
  saveRegistry(reg)
}

/** 登记组件条目：课程组件恒走 per-course 键（course 为空串 = 无课程槽，语义明确）；
 *  单例走扁平键（监督器重启回灌用）。 */
export function saveAnyComponent(name: Component, course: string, entry: RegistryEntry): void {
  if (isCourseComponent(name)) saveCourseComponent(name, course, entry)
  else saveComponent(name, entry)
}

/** 清除一个组件的登记：课程组件恒走 per-course 键，单例走扁平键。 */
export function clearAnyComponent(name: Component, course = ''): void {
  if (isCourseComponent(name)) clearCourseComponent(name, course)
  else clearComponent(name)
}

export function clearRegistry(): void {
  try {
    unlinkSync(registryPath())
  } catch {
    /* absent */
  }
  // legacy：hub-start 分文件账本一并清（已收编消费后不再重复判活）。
  for (const name of LEGACY_COMPS) {
    try {
      unlinkSync(path.join(LEGACY_DIR, `registry.${name}.json`))
    } catch {
      /* absent */
    }
  }
  try {
    unlinkSync(path.join(LEGACY_DIR, 'registry.json'))
  } catch {
    /* absent */
  }
}
