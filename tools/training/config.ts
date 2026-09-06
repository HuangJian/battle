/** config.ts — rl-config.json 读取 / 写回 / 课程校验（rl/config.resolve_course 同规则）。 */

import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'fs'
import path from 'path'
import { CONFIG_PATH, CURRICULA_DIR } from './paths'
import type { RlConfig } from './types'

export function loadConfig(): RlConfig {
  return JSON.parse(readFileSync(CONFIG_PATH, 'utf-8')) as RlConfig
}

/** 写回 rl-config.json 的 rl.remote_hub_url（隧道 URL 变更时）。 */
export function writeRemoteHubUrl(url: string): void {
  const cfg = loadConfig()
  const old = cfg.rl?.remote_hub_url
  if (url && url !== old) {
    cfg.rl = cfg.rl || ({} as RlConfig['rl'])
    cfg.rl.remote_hub_url = url
    writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2), 'utf-8')
    console.log(`  remote_hub_url updated: ${old} -> ${url}`)
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

/** 课程参数快速失败：先按路径、再按 curricula/<name>.jsonc。拼错在启动任何
 *  基础设施之前响亮报错（DECISIONS §340 补充 2）。 */
export function validateCourseArg(name: string): void {
  if (!name || existsSync(name)) return
  if (existsSync(path.join(CURRICULA_DIR, `${name}.jsonc`))) return
  console.error(
    `\n课程 '${name}' 不存在（查找 ${path.join(CURRICULA_DIR, name)}.jsonc，或传已存在的课程文件路径）。`,
  )
  printRecentCourses()
  process.exit(1)
}
