/** backup.ts — 一键导出（plan/rl-eval-system.md §3.6/R3：无 git 兜底 ⇒ 强制备份纪律）。
 *
 * 用法：`bun tools/training/evalboard/backup.ts --data <dataRoot> --out <dir>`
 * 产出 `<out>/evalboard-<ts>.zip`（zip 命令需可用；yaratan里程碑冷备同格式）。
 * 导出后人工拷贝到仓库外（NAS/云盘），路径记当日 memory log。
 */

import { existsSync, mkdirSync } from 'fs'
import path from 'path'

export function backupName(now = new Date()): string {
  return `evalboard-${now.toISOString().replace(/[-:.]/g, '').slice(0, 15)}.zip`
}

/** staging 校验：数据根存在即允导（空账本也导，占位防"我以为备了"）。 */
export async function exportBackup(dataRoot: string, outDir: string): Promise<string> {
  if (!existsSync(dataRoot)) throw new Error(`数据根不存在: ${dataRoot}`)
  mkdirSync(outDir, { recursive: true })
  const dest = path.join(outDir, backupName())
  const proc = Bun.spawnSync(['zip', '-qr', dest, '.'], { cwd: dataRoot })
  if (proc.exitCode !== 0) {
    throw new Error(`zip 导出失败 rc=${proc.exitCode}: ${proc.stderr.toString().slice(-300)}`)
  }
  return dest
}

if (import.meta.main) {
  const argv = process.argv.slice(2)
  const at = (k: string): string => {
    const i = argv.indexOf(k)
    return i >= 0 ? (argv[i + 1] ?? '') : ''
  }
  const data = at('--data') || 'tools/training/data/evalboard'
  const out = at('--out') || 'tools/training/data/evalboard/backups'
  exportBackup(data, out)
    .then((d) => console.log(`[backup] ${d}`))
    .catch((e) => {
      console.error(`[backup] FAIL ${e instanceof Error ? e.message : String(e)}`)
      process.exit(1)
    })
}
