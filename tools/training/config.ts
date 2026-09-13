/** config.ts — rl-config.json 读取 / 写回 / 课程校验（rl/config.resolve_course 同规则）。 */

import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'fs'
import path from 'path'
import { CURRICULA_DIR, configPath } from './paths'
import { capacityError } from './slots'
import type { RlConfig } from './types'

export function loadConfig(cfgPath = configPath()): RlConfig {
  return JSON.parse(readFileSync(cfgPath, 'utf-8')) as RlConfig
}

/** 写回整份 rl-config.json（控制台模式开关/节点编辑；保持调用方传入对象原样落盘）。
 *
 *  多课程（plan P4-W1）：落盘前过 `capacityError` 加法校验——`Σ eff(course) ≤ 裸机
 *  容量`，超量 fail-fast 并点名超量课程（绝不把超量配额写到磁盘再靠运行时补救）。
 *  无 `courses` 块时为空操作（默认行为零变化，§0.5-4）。 */
export function saveConfig(cfg: RlConfig, cfgPath = configPath()): void {
  const cap = capacityError(cfg)
  if (cap) throw new Error(cap)
  writeFileSync(cfgPath, JSON.stringify(cfg, null, 2), 'utf-8')
}

/** 写回隧道 URL（隧道重建时）。
 *
 *  多课程（plan §3.3，P3）：URL 住 `rl.remote_hubs[course]`（每课一隧道）；
 *  单课键 `rl.remote_hub_url` 同步写一份作兼容读（notebook 手工路径/Q2 回退读它）。
 *  无课程时只写单键（默认行为零变化）。 */
export function writeRemoteHubUrl(url: string, course = ''): void {
  const cfg = loadConfig()
  const old = course ? cfg.rl?.remote_hubs?.[course] : cfg.rl?.remote_hub_url
  if (url && url !== old) {
    cfg.rl = cfg.rl || ({} as RlConfig['rl'])
    cfg.rl.remote_hub_url = url
    if (course) {
      cfg.rl.remote_hubs = { ...(cfg.rl.remote_hubs ?? {}), [course]: url }
    }
    writeFileSync(configPath(), JSON.stringify(cfg, null, 2), 'utf-8')
    console.log(`  remote_hub_url updated: ${old} -> ${url}${course ? ` (course=${course})` : ''}`)
  }
}

/** 课程目录最近更新的 5 个课程（拼错课程名时的提示）。 */
export function printRecentCourses(): void {
  let files: string[] = []
  try {
    files = readdirSync(CURRICULA_DIR).filter((f) => f.endsWith('.jsonc'))
  } catch {
    /* dir missing */
  }
  if (files.length === 0) {
    console.error(`课程目录无 .jsonc 文件: ${CURRICULA_DIR}`)
    return
  }
  const recent = files
    .map((f) => ({ f, m: statSync(path.join(CURRICULA_DIR, f)).mtimeMs }))
    .sort((a, b) => b.m - a.m)
    .slice(0, 5)
  console.error(`\n课程目录最近更新的 5 个课程 (${CURRICULA_DIR}):`)
  for (const { f, m } of recent) {
    console.error(
      `  ${f.replace(/\.jsonc$/, '').padEnd(20)} (${new Date(m).toLocaleString('sv-SE')})`,
    )
  }
}

/** 课程参数快速失败：先按路径、再按 curricula/<name>.jsonc（RL 课程）或
 *  curricula/<name>.bc.jsonc（BC 课程，2026-09-13）。拼错在启动任何
 *  基础设施之前响亮报错（DECISIONS §340 补充 2）。 */
export function validateCourseArg(name: string): void {
  if (!name || existsSync(name)) return
  if (existsSync(path.join(CURRICULA_DIR, `${name}.jsonc`))) return
  if (existsSync(path.join(CURRICULA_DIR, `${name}.bc.jsonc`))) return
  console.error(
    `\n课程 '${name}' 不存在（查找 ${path.join(CURRICULA_DIR, name)}.jsonc / ` +
      `${name}.bc.jsonc，或传已存在的课程文件路径）。`,
  )
  printRecentCourses()
  process.exit(1)
}
