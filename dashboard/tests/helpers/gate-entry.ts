/**
 * gate-entry.ts — `bun test` 的**唯一入口**（`dashboard/bunfig.toml` 的 `[test] preload`）。
 *
 * ## 为什么需要这一层（2026-10-25，用户报「bun test 报错、bun run test 没问题」）
 *
 * 本套件依赖**文件级隔离**：同一个进程里所有测试文件共享 global 与模块注册表，某个用例改过的
 * env（`BCITY_RL_CONFIG` / 临时 `curricula` 等）会漏给后面每一个文件。实测同一份代码：
 *
 *   · `bun test`                          → **1747 pass / 76 fail / 1 error**
 *   · `bun test --isolate`（或门禁的 --parallel）→ **1838 pass / 0 fail**
 *
 * 而 Bun 1.4 的 `bunfig.toml [test]` **没有** `isolate` / `parallel` / `timeout` 键（写了静默
 * 忽略；见 oven-sh/bun#38215、#38728），所以裸 `bun test` 无法靠配置变对——它只能被**转交**给
 * 门禁（`scripts/run-tests.ts`），保证「随手敲的命令」与 `bun run test` 同一读数、同一退出码。
 *
 * ## 机制
 *
 *   · 已在门禁里（`BCITY_TEST_GATE=1`，由 `scripts/run-tests.ts` 设）或已是 worker
 *     （`BUN_TEST_WORKER_ID`）⇒ **原样放行**，不转交（否则无限套娃）。
 *   · 否则读 `/proc/self/cmdline` 取**用户真正敲的**参数。preload 里的 `process.argv` 已被
 *     Bun 改写成「当前测试文件」，不可用于转发（实测：`bun test --coverage` 与裸跑看到的
 *     argv 完全一样）。
 *   · 真参数原样转交给门禁（`tests` 之外的 positional 也转发 ⇒ `bun test <file>` 只跑那个
 *     文件），stdio 继承、退出码照抄。
 *   · `/proc` 读不到（非 Linux）⇒ 不转交，并**自己**加载两份预载（至少把 env 隔离挂上），
 *     同时打一行提示：那条路得不到文件级隔离，全量跑会红——请用 `bun run test`。
 *
 * ## 不做什么
 *
 *   · 不改测试、不改门禁组成（`scripts/run-tests.ts` 仍是唯一携带者；
 *     `tests/gate-composition.test.ts` 继续钉它）。
 *   · 不在隔离里"顺手"再跑一次：门禁的 worker 每个文件一个进程，本文件对所有 worker 都是
 *     空操作（只做 `[test] preload` 的登记）。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/** dashboard 根（本文件住 `dashboard/tests/helpers/`）。 */
const DASH = dirname(dirname(dirname(fileURLToPath(import.meta.url))))

/** 门禁标记（`scripts/run-tests.ts` 在 spawn 子进程时设）。有它 ⇒ 已在隔离里。 */
const GATE_ENV = 'BCITY_TEST_GATE'

/** 用户真正敲的参数（`bun test <...>` 里 `test` 之后的部分）；读不到 = null。 */
function originalArgs(): string[] | null {
  try {
    // Linux：/proc/<pid>/cmdline 是 NUL 分隔的原始 argv（µs 级、不 fork）。
    const argv = readFileSync('/proc/self/cmdline', 'utf-8')
      .split('\0')
      .filter((s) => s.length > 0)
    const i = argv.findIndex((a) => a === 'test')
    return i >= 0 ? argv.slice(i + 1) : null
  } catch {
    return null
  }
}

/** 已在隔离里：门禁标记（启动器或其子进程）或 Bun 自己的 worker。 */
const isolated = process.env[GATE_ENV] === '1' || Boolean(process.env.BUN_TEST_WORKER_ID)

if (!isolated) {
  const args = originalArgs()
  if (args) {
    const r = Bun.spawnSync([process.execPath, join(DASH, 'scripts', 'run-tests.ts'), ...args], {
      cwd: DASH,
      stdio: ['inherit', 'inherit', 'inherit'],
      env: { ...process.env, [GATE_ENV]: '1' },
    })
    process.exit(r.exitCode ?? 1)
  }
  // 兜底（非 Linux）：至少把 env 隔离挂上；文件级隔离拿不到，全量跑仍会红。
  process.stderr.write(
    '[bun test] 读不到原始命令行（非 Linux），无法转交门禁；已加载 env 隔离预载。\n' +
      '[bun test] 全量请用 `bun run test`（文件级隔离是必须的：同进程共享 global ⇒ 76 red）。\n',
  )
  await import('./no-proxy')
  await import('./isolate-paths')
}
