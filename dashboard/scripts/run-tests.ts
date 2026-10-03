/**
 * run-tests.ts — dashboard 门禁（`bun run test`）的启动器。
 *
 * 为什么需要它：`package.json` 的脚本串是**静态字符串**，做不了算术；而 `bun test --parallel`
 * 的裸缺省 = 运行时眼里的 CPU 核数（本机 16 = 含超线程），2026-10-03 实测会把机器的提交上限
 * 顶穿 ⇒ worker 崩溃 + 兄弟 worker 连环 abort 的**假红**。故按**物理核数**显式传
 * `--parallel=<n>`（口径唯一实现 = 仓根 `tools/lib/cores.ts::physicalCores`；dashboard 早已
 * 只读引用该模块，见 `src/core/venv.ts` 与 `.github/workflows/dashboard.yml` 的 paths）。
 *
 * 组成的两条硬要求（由 `tests/gate-composition.test.ts` 钉住 —— 删掉就是**静默退化**）：
 *   · `--parallel`：文件级隔离（去掉 ⇒ 58 个用例因跨文件 global 污染转红，不是慢是错）；
 *   · `--preload ./tests/helpers/no-proxy.ts`：代理机器上不预载 ⇒ 回环流量走 PROXY 白等（11.4s vs 1.8s）。
 */
import { spawnSync } from 'node:child_process'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import { physicalCores } from '../../tools/lib/cores'

/** dashboard 根（本文件住 `dashboard/scripts/`）。 */
const DASH = dirname(dirname(fileURLToPath(import.meta.url)))

const n = physicalCores()
const args = [
  'test',
  `--parallel=${n}`,
  '--timeout=50000',
  '--preload',
  './tests/helpers/no-proxy.ts',
  'tests',
  ...process.argv.slice(2),
]
console.log(`[gate] bun ${args.join(' ')}`)
const r = spawnSync('bun', args, { stdio: 'inherit', cwd: DASH })
process.exit(r.status ?? 1)
