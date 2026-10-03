import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * gate-composition.test.ts — 门禁命令的**组成**不能静默退化：删掉 flag 后测试照样全绿，
 * 只是慢 6×（预载）或直接变错（隔离）—— 所以把这两条钉在这里。
 *
 * 2026-10-01 实测（119 文件 / 1232 用例，同机）：
 *   · 缺 `--preload ./tests/helpers/no-proxy.ts`：代理机器上 Bun 把回环与探测流量交给
 *     HTTP(S)_PROXY（且不认 `127.*` 这类通配写法），「hub 不可达」类用例每个白等 1.5–4s
 *     ⇒ 全目录墙钟 11.4s vs 1.8s；
 *   · 缺 `--parallel`：文件同进程共享 global ⇒ 58 个用例因交叉污染红（不是慢，是错）。
 *
 * 2026-10-03：脚本串**不再直接写这两条 flag** —— `--parallel` 的值要按机器的**物理核数**算
 * （JSON 串做不了算术，裸 `--parallel` 的缺省是逻辑核 16，会把机器的提交上限顶穿 ⇒ 假红）。
 * 组成下沉到 `scripts/run-tests.ts`；本文件改为钉「package.json 指向那个启动器」+「启动器里
 * 两条组成都在」。单一挂载点仍是 `bun run test`（pre-commit 与 `.github/workflows/dashboard.yml`
 * 都调它）。
 */
const DASH = resolve(import.meta.dir, '..')
const pkg = JSON.parse(readFileSync(resolve(DASH, 'package.json'), 'utf8')) as {
  scripts: Record<string, string>
}
/** 门禁组成的**唯一携带者**（2026-10-03 起）。 */
const LAUNCHER = resolve(DASH, 'scripts/run-tests.ts')
const launcher = readFileSync(LAUNCHER, 'utf8')

describe('dashboard 门禁命令组成', () => {
  it('`bun run test` 指向启动器（组成不再写在 JSON 串里）', () => {
    expect(pkg.scripts.test).toContain('scripts/run-tests.ts')
    expect(existsSync(LAUNCHER)).toBe(true)
  })

  it('启动器带 `--parallel=`（文件级隔离，缺了就是 58 red），且值来自物理核数', () => {
    expect(launcher).toContain('--parallel=')
    expect(launcher).toContain('physicalCores()')
  })

  it('启动器带 no-proxy 预载，且该文件真实存在（拼错 ⇒ bun 直接 preload not found）', () => {
    expect(launcher).toContain('--preload')
    expect(launcher).toContain('./tests/helpers/no-proxy.ts')
    expect(existsSync(resolve(DASH, 'tests/helpers/no-proxy.ts'))).toBe(true)
  })

  it('本进程确实被预载改写（NO_PROXY 含 `*`）；裸 `bun test <file>` 会在这一条红', () => {
    // 生产同源的 shapeLoopbackNoProxy 会往 `*` 后面追加精确回环主机 ⇒ 断言"含 *"而不是等于。
    expect(process.env.NO_PROXY ?? '').toContain('*')
    expect(process.env.no_proxy ?? '').toContain('*')
  })
})
