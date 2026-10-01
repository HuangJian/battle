import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * `stack/specs.ts` 里写死的**哨兵路径必须实存**。
 *
 * 哨兵语义 = 「这个文件 mtime 变了 ⇒ 进程跑的是旧代码，重启它」（`core/reload.ts`
 * 的 `runningStaleCode` / `snap`，`core/sentinels.ts` 的 `sentinelsChangedSince`）。
 * 而 `snap()` 对**不存在的文件**返回 `null`：那条哨兵**永不触发**，一行日志都没有 ——
 * 本仓最典型的那种哑故障（守卫看着在、其实没在）。
 *
 * 事故（2026-09-30，nn-training 刀 1/2/3 之后被清出来）：`localWorker` 与 `trainingLoop`
 * 的哨兵里一直写着 `nn-training/remote/protocol.py`，而 `protocol.py` 早在
 * 2026-09-23（5624856）就下沉到 `common/` 了 ⇒ 此后改 `common/protocol.py` 都不会重启
 * worker，正是那句注释要防的事：「漏报 = worker 用旧协议跑新 job」。同一批里
 * `remote/worker.py` / `remote/hub_client.py` 还在原地，所以不是整批过期 —— 只有
 * **逐条验存**才抓得住。
 *
 * 口径：扫 `specs.ts` 源码里的**整条字面量**（`'nn-training/…py'` / `'tools/…'`）。
 * 动态拼出来的部分（`hubImplementationFiles()` 用 `readdirSync` 列目录）天然正确，不在
 * 扫描面内；`'tmp/local-worker'` 那种非仓库路径按前缀过滤掉。
 */

const DASH = resolve(import.meta.dir, '..')
const ROOT = resolve(DASH, '..')

/** 仓库内**文件**路径字面量：`nn-training/…` 或 `tools/…`（源码里一律 posix 相对路径）。
 *
 * 要求带扩展名 ⇒ `hubImplementationFiles()` 里那个 `'nn-training/hub'`（它是 `readdirSync`
 * 的**目录前缀**，不是哨兵）自然被排除；哨兵集里出现目录名本来就是哑的（目录 mtime 只在
 * 增删条目时变），所以下面还单留一条「是文件不是目录」的断言看着带扩展名的那些。
 */
const REPO_PATH = /'(nn-training|tools)\/([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)'/g

interface Declared {
  line: number
  literal: string
  abs: string
}

function declaredPaths(): Declared[] {
  const src = readFileSync(resolve(DASH, 'src/stack/specs.ts'), 'utf8')
  const found: Declared[] = []
  src.split('\n').forEach((text, i) => {
    for (const m of text.matchAll(REPO_PATH)) {
      const literal = `${m[1]}/${m[2]}`
      found.push({ line: i + 1, literal, abs: resolve(ROOT, literal) })
    }
  })
  return found
}

describe('stack/specs.ts 的哨兵路径', () => {
  const declared = declaredPaths()

  it('扫描面可信：找到 ≥ 5 条仓库内路径（扫描坏了先红，不静默通过）', () => {
    expect(declared.length).toBeGreaterThanOrEqual(5)
  })

  it('每条都真实存在（哨兵指向不存在的文件 = 永不触发的哑哨兵）', () => {
    const missing = declared
      .filter((d) => !existsSync(d.abs))
      .map((d) => `specs.ts:${d.line}: ${d.literal}`)
    expect(missing).toEqual([])
  })

  it('目标都是文件而不是目录（目录 mtime 只在增删条目时变，当哨兵是哑的）', () => {
    const dirs = declared
      .filter((d) => existsSync(d.abs) && !statSync(d.abs).isFile())
      .map((d) => `specs.ts:${d.line}: ${d.literal}`)
    expect(dirs).toEqual([])
  })
})
