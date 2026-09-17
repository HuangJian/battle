/** run-python.ts — 控制台跑 python **一次性脚本**的唯一入口（解释器解析 + 环境 + 两种模式）。
 *
 *  为什么要有这个文件（而不是每个动作各写一遍）：控制台有若干「跑一次就退出」的 python
 *  一侧能力——`evalA`（A 层评估）、任务包导出（`run_rl.py --export-bundle`）、产物导入
 *  （`remote.deliver_zip`）。它们的解释器解析坑**完全相同**且都踩过：
 *
 *  * uv 造的 venv 里 `.venv\Scripts\python.exe` 是 **trampoline**（真身是它另起的基础
 *    解释器），直启它要么拿不到第三方包、要么杀掉它留下孤儿（`core/venv.ts` 有完整说明）；
 *  * 真身直启时第三方包**只能靠 PYTHONPATH 挂 site-packages**——漏了就是
 *    `ModuleNotFoundError: pydantic`（2026-09-12 实测）。
 *
 *  两条模式：
 *  * `spawnRunPython`：**detach**（长任务：评估、导出）——stdout/stderr 进日志文件，
 *    调用方立刻拿 pid 返回，界面靠日志/产物文件看进展；
 *  * `runRunPythonSync`：**同步**（短任务：导入 zip 这类「调用方需要它的结果」的活）——
 *    捕获 stdout/stderr，调用方解析（见 `deliver_zip.IMPORT_JSON_MARK`）。
 */

import { spawnSync } from 'child_process'
import { existsSync } from 'fs'
import path from 'path'
import { NN_TRAINING, REPO_ROOT } from '../core/paths'
import { spawnBg, type SpawnBgResult } from '../core/proc'
import { resolveVenvPython } from '../core/venv'

export interface RunPythonEnv {
  python: string
  env: Record<string, string>
}

/** 解释器 + 环境（venv 入口优先——它自含依赖；uv 跳板真身靠 PYTHONPATH 补）。
 *
 *  `PYTHONPATH` 同时带上 `nn-training/` 本身：一侧脚本里大量 `import rl.*` / `remote.*`
 *  是按包名导入的（真身直启时 cwd 不在 sys.path 里）。
 */
export function resolveRunPython(): RunPythonEnv {
  const resolved = resolveVenvPython()
  const venvEntry =
    process.platform === 'win32'
      ? path.join(NN_TRAINING, '.venv', 'Scripts', 'python.exe')
      : path.join(NN_TRAINING, '.venv', 'bin', 'python3')
  const python = existsSync(venvEntry) ? venvEntry : resolved.python
  const parts = [resolved.sitePackages, NN_TRAINING].filter(Boolean)
  const prev = process.env.PYTHONPATH || process.env.PYTHONHOME || ''
  const env: Record<string, string> = {}
  if (parts.length > 0) {
    const joined = parts.join(path.delimiter)
    env.PYTHONPATH = prev ? `${joined}${path.delimiter}${prev}` : joined
  }
  return { python, env }
}

/** 后台跑一个 python 脚本（detach；stdout/stderr 进 `logFile`）。返回 pid/logFile。 */
export function spawnRunPython(scriptRel: string, args: string[], logFile: string): SpawnBgResult {
  const { python, env } = resolveRunPython()
  const script = path.isAbsolute(scriptRel) ? scriptRel : path.join(REPO_ROOT, scriptRel)
  return spawnBg([python, '-u', script, ...args], {
    cwd: NN_TRAINING,
    env,
    log: logFile,
  })
}

export interface SyncRunResult {
  code: number | null
  stdout: string
  stderr: string
  /** 超时（进程被强杀）——与「跑完但退出非零」是两码事，必须分开报。 */
  timeout: boolean
}

/** 同步跑一个 python **模块**（`-m`；短任务），捕获输出。异常不抛，由返回码表达。
 *
 *  stdout 一律按 UTF-8 解码（§17.6：zh-CN Windows 的控制台代码页是 gb2312，
 *  子进程输出中文时用默认编码解码会炸成 `stdout: null`——本仓踩过）。
 */
export function runRunPythonSyncModule(
  module: string,
  args: string[],
  opts: { timeoutMs?: number } = {},
): SyncRunResult {
  const { python, env } = resolveRunPython()
  const r = spawnSync(python, ['-m', module, ...args], {
    cwd: NN_TRAINING,
    env: { ...process.env, ...env },
    timeout: opts.timeoutMs ?? 300_000,
    maxBuffer: 8 * 1024 * 1024,
  })
  return {
    code: r.status,
    stdout: r.stdout ? r.stdout.toString('utf8') : '',
    stderr: r.stderr ? r.stderr.toString('utf8') : '',
    timeout: Boolean(r.error && (r.error as NodeJS.ErrnoException).code === 'ETIMEDOUT'),
  }
}
