/** config.ts — rl-config.json 读取 / 写回 / 课程校验（rl/config.resolve_course 同规则）。 */

import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'fs'
import path from 'path'
import { configPath, curriculaDir } from './paths'
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

/** 写回 hub URL（隧道重建时）。
 *
 *  **2026-09-18 收敛为单隧道/共享 hub 后只有一个地址**：hub 与隧道都不再按课程分开，
 *  URL 是**全局事实** ⇒ 只写单键 `rl.remote_hub_url`。旧形状的 per-course 键
 *  `rl.remote_hubs[<课>]` 不再写（也不被读：python 侧的回填已删）——留着它会变成
 *  「指向已不存在的每课隧道」的第二事实源。 */
export function writeRemoteHubUrl(url: string): void {
  const cfg = loadConfig()
  const old = cfg.rl?.remote_hub_url
  if (url && url !== old) {
    cfg.rl = cfg.rl || ({} as RlConfig['rl'])
    cfg.rl.remote_hub_url = url
    writeFileSync(configPath(), JSON.stringify(cfg, null, 2), 'utf-8')
    console.log(`  remote_hub_url updated: ${old} -> ${url}`)
  }
}

/** 课程目录最近更新的 5 个课程（拼错课程名时的提示）。 */
export function printRecentCourses(): void {
  let files: string[] = []
  try {
    files = readdirSync(curriculaDir()).filter((f) => f.endsWith('.jsonc'))
  } catch {
    /* dir missing */
  }
  if (files.length === 0) {
    console.error(`课程目录无 .jsonc 文件: ${curriculaDir()}`)
    return
  }
  const recent = files
    .map((f) => ({ f, m: statSync(path.join(curriculaDir(), f)).mtimeMs }))
    .sort((a, b) => b.m - a.m)
    .slice(0, 5)
  console.error(`\n课程目录最近更新的 5 个课程 (${curriculaDir()}):`)
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
  if (existsSync(path.join(curriculaDir(), `${name}.jsonc`))) return
  if (existsSync(path.join(curriculaDir(), `${name}.bc.jsonc`))) return
  console.error(
    `\n课程 '${name}' 不存在（查找 ${path.join(curriculaDir(), name)}.jsonc / ` +
      `${name}.bc.jsonc，或传已存在的课程文件路径）。`,
  )
  printRecentCourses()
  process.exit(1)
}
