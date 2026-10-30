/**
 * run-root-tests.ts — 根套件门禁（`bun run check`）的启动器。
 *
 * 为什么需要它：`package.json` 的脚本串是**静态字符串**，做不了算术；而 `bun test --parallel`
 * 的裸缺省 = 运行时眼里的 CPU 核数（本机 16 = 含超线程）—— 2026-10-03 实测 16 worker 会把
 * Windows 的提交上限顶穿（`RegisterWaitForSingleObject 1455 paging file is too small`）⇒
 * worker 崩 + 兄弟 worker 连环 abort，出一片**假红**。故门禁一律按**物理核数**起 worker
 * （口径唯一实现 = `tools/lib/cores.ts::physicalCores`；决议见 plan `gate-parallelism-physical-cores`）。
 *
 * 组成固定在这里（`--parallel=<物理核数> --timeout=50000` + **排除 `dashboard/**` 与
 * `userscript/**`**）：两者都是独立 bun 项目、由各自的门禁跑（DECISIONS
 * §2026-09-14-goalnn-dashboard-project；userscript 见 plan/notebook-userscript.plan.md §4.1）。
 * 命令行附加参数原样透传给 `bun test`。
 */
import { spawnSync } from 'node:child_process'

import { physicalCores } from './lib/cores'

const n = physicalCores()
const args = [
  'test',
  `--parallel=${n}`,
  '--timeout=50000',
  '--path-ignore-patterns=dashboard/**',
  '--path-ignore-patterns=userscript/**',
  ...process.argv.slice(2),
]
console.log(`[gate] bun ${args.join(' ')}`)
const r = spawnSync('bun', args, { windowsHide: true, stdio: 'inherit' })
process.exit(r.status ?? 1)
