import { spawnSync } from 'node:child_process'
import { cpus } from 'node:os'
import { join } from 'node:path'

/**
 * test-silent.ts — 静默测试启动器（口径与根门禁一致：`--parallel --timeout=50000`）。
 *
 * 并行度保守折半：HT 机器上 ≈ 物理核；本套件只有几个文件，少开 worker 无害
 * （不为一个 4 文件的套件引入 pwsh 探测）。
 */
const cwd = join(import.meta.dir, '..')
const parallel = Math.max(1, Math.floor(cpus().length / 2))
const args = ['test', `--parallel=${parallel}`, '--timeout=50000', ...process.argv.slice(2)]
const r = spawnSync('bun', args, { cwd, encoding: 'utf8', windowsHide: true })
const out = `${r.stdout ?? ''}${r.stderr ?? ''}`
if (r.status === 0) {
  const pass = /(\d+)\s+pass/.exec(out)?.[0] ?? 'tests'
  console.log(`✓ ${pass} (${parallel} workers)`)
  process.exit(0)
}
console.log('✗ userscript tests failed')
console.log(out)
process.exit(r.status ?? 1)
