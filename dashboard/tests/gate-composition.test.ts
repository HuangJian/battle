import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * gate-composition.test.ts — 门禁命令的**组成**不能静默退化：删掉 flag 后测试照样全绿，
 * 只是慢 6×（预载）或直接变错（隔离）——所以把这两条钉在这里。
 *
 * 2026-10-01 实测（119 文件 / 1232 用例，同机）：
 *   · 缺 `--preload ./tests/helpers/no-proxy.ts`：代理机器上 Bun 把回环与探测流量交给
 *     HTTP(S)_PROXY（且不认 `127.*` 这类通配写法），「hub 不可达」类用例每个白等 1.5–4s
 *     ⇒ 全目录墙钟 11.4s vs 1.8s；
 *   · 缺 `--parallel`：文件同进程共享 global ⇒ 58 个用例因交叉污染红（不是慢，是错）。
 *
 * 两条都由 `package.json` 的 test 脚本承载，而 pre-commit 与
 * `.github/workflows/dashboard.yml` 都调 `bun run test` —— 单一挂载点，钉它一处即可。
 */
const DASH = resolve(import.meta.dir, '..')
const pkg = JSON.parse(readFileSync(resolve(DASH, 'package.json'), 'utf8')) as {
  scripts: Record<string, string>
}

describe('dashboard 门禁命令组成', () => {
  it('`bun run test` 带 --parallel（文件级隔离，缺了就是 58 red）', () => {
    expect(pkg.scripts.test).toContain('--parallel')
  })

  it('`bun run test` 带 no-proxy 预载，且该文件真实存在（拼错 ⇒ bun 直接 preload not found）', () => {
    expect(pkg.scripts.test).toContain('--preload ./tests/helpers/no-proxy.ts')
    expect(existsSync(resolve(DASH, 'tests/helpers/no-proxy.ts'))).toBe(true)
  })

  it('本进程确实被预载改写（NO_PROXY 含 `*`）；裸 `bun test <file>` 会在这一条红', () => {
    // 生产同源的 shapeLoopbackNoProxy 会往 `*` 后面追加精确回环主机 ⇒ 断言"含 *"而不是等于。
    expect(process.env.NO_PROXY ?? '').toContain('*')
    expect(process.env.no_proxy ?? '').toContain('*')
  })
})
