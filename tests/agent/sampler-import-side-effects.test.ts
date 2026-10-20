/**
 * sampler-agent 的**导入副作用**不得触碰工作目录（2026-10-20 现场事故）。
 *
 * 事故链（errors 页 `selfNode` 的 `export-eval-game exited 1 … ENOENT` 追到这里）：
 *   ① 模块顶层在 import 时就跑 boot 收敛：`mkdirSync(WORK_DIR)` + `sweepWorkdir()`；
 *   ② 任何 import 本模块的进程（`bun run check` 的测试、诊断工具）都带着**空内存桶**
 *      去扫**真** `tmp/dist-agent` ⇒ `sweepWeightFilePlan` 认为所有权重都没人引用，
 *      每个 kind 只留最新 KEEP=4 份，其余删除；
 *   ③ 正在服务的 agent 内存桶仍指向被删文件 ⇒ 下一局子进程
 *      `readFileSync(weightsPath)` ENOENT（实测 2026-10-20 17:50 七局 eval 全灭：
 *      `weights-eval-350fc387cde7dc2a.json` 由 16:22 的 POST 写下、17:50 前被
 *      pre-commit 的测试 import 删掉）。
 *
 * 判据用**子进程** import（同进程内模块已缓存，模块体不会重跑），`SAMPLER_WORK_DIR`
 * 把工作目录指向夹具 —— 于是导入的副作用只会落在临时目录里，可安全断言。
 */
import { describe, expect, it } from 'bun:test'
import { spawnSync } from 'node:child_process'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
  utimesSync,
  writeFileSync,
} from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { REPO_ROOT } from '../../tools/agent/codehash-files'

const AGENT = join(REPO_ROOT, 'tools', 'agent', 'sampler-agent.ts')

/** 子进程里 import 模块（`import.meta.main` 为 false = 测试/工具路径）。 */
function importAgent(workDir: string): { status: number | null; stderr: string } {
  const url = pathToFileURL(AGENT).href
  const r = spawnSync(process.execPath, ['-e', `await import(${JSON.stringify(url)})`], {
    cwd: REPO_ROOT,
    env: { ...process.env, SAMPLER_WORK_DIR: workDir },
    encoding: 'utf8',
    timeout: 120_000,
    windowsHide: true,
  })
  return { status: r.status, stderr: r.stderr ?? '' }
}

function mkRoot(tag: string): string {
  const parent = join(REPO_ROOT, 'tmp', 'pytest-tmp')
  mkdirSync(parent, { recursive: true })
  return mkdtempSync(join(parent, `sampler-import-${tag}-`))
}

describe('sampler-agent 导入副作用（2026-10-20 权重被测试 import 删掉）', () => {
  it('import 不清理工作目录：超过 KEEP=4 的旧权重必须原样保留', () => {
    const dir = mkRoot('keep')
    const names: string[] = []
    try {
      const now = Date.now()
      for (let i = 0; i < 6; i++) {
        // 6 个不同的合法权重名（16 hex 前缀）；mtime 递减 ⇒ 任何 boot 清扫都会删最旧 2 份。
        const name = `weights-rollout-${String(i).repeat(16)}.json`
        const file = join(dir, name)
        writeFileSync(file, `fixture-${i}`)
        const t = new Date(now - (6 - i) * 10_000)
        utimesSync(file, t, t)
        names.push(name)
      }

      const run = importAgent(dir)
      expect(run.stderr).toBe('')
      expect(run.status).toBe(0)

      expect(
        readdirSync(dir)
          .filter((n) => n.startsWith('weights-'))
          .sort(),
      ).toEqual(names.sort())
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('import 不创建缺失的工作目录（目录只在真正起服务时出现）', () => {
    const root = mkRoot('absent')
    const dir = join(root, 'never')
    try {
      const run = importAgent(dir)
      expect(run.stderr).toBe('')
      expect(run.status).toBe(0)
      expect(existsSync(dir)).toBe(false)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
