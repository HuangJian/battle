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
 *  两条模式（**都不按住事件循环**）：
 *  * `spawnRunPython`：**detach**（长任务：评估、导出）——stdout/stderr 进日志文件，
 *    调用方立刻拿 pid 返回，界面靠日志/产物文件看进展；
 *  * `runRunPythonAsyncModule/Script`：**异步捕获**（短任务：导入 zip、只读调度器视图这类
 *    「调用方需要它的结果」的活）——子进程交给 libuv，事件循环照常服务其它请求/刷新器，
 *    结果回来后再 resolve（见 `deliver_zip.IMPORT_JSON_MARK`）。
 *
 *  ★ 2026-09-22：`spawnSync` 变体（`runRunPythonSyncModule` / `runRunPythonSyncScript`）**已删除**——
 *   它们把整个事件循环按住：导入一个 zip 的十几秒里，控制台的第 http 请求、5s 快照刷新器、
 *   其它查看者全部排队（「导入时整个控制台卡住」），而 SWR 的「重算丢后台」也名存实亡
 *   （旧值响应要等 spawnSync 返回才发得出去）。控制台跑 python **只有**上面两种模式，
 *  门禁 `tests/server-no-blocking-python.test.ts` 钉住（src/server/** 不许再出现 spawnSync）。
 */

import { spawn } from 'child_process'
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

export interface RunPythonResult {
  code: number | null
  stdout: string
  stderr: string
  /** 超时（进程被强杀）——与「跑完但退出非零」是两码事，必须分开报。 */
  timeout: boolean
}

/** 异步跑一个 python **模块**（`-m`；短任务），捕获输出。异常不抛，由返回码表达。
 *
 *  stdout 一律按 UTF-8 解码（§17.6：zh-CN Windows 的控制台代码页是 gb2312，
 *  子进程输出中文时用默认编码解码会炸成 `stdout: null`——本仓踩过）。
 */
export function runRunPythonAsyncModule(
  module: string,
  args: string[],
  opts: { timeoutMs?: number } = {},
): Promise<RunPythonResult> {
  return capture(['-m', module, ...args], { ...opts, ...resolveRunPython() })
}

/** **异步**跑一个 python **脚本文件**（仓库相对或绝对路径；短任务），捕获输出。
 *
 *  与模块变体只差入口形式：只读型脚本（`run_rl_cluster.py --json`）是文件不是包模块，
 *  而解释器解析 / env / UTF-8 解码这些坑一模一样——故共用同一条实现，不另写一份。
 */
export function runRunPythonAsyncScript(
  scriptRel: string,
  args: string[],
  opts: { timeoutMs?: number } = {},
): Promise<RunPythonResult> {
  const script = path.isAbsolute(scriptRel) ? scriptRel : path.join(REPO_ROOT, scriptRel)
  return capture(['-u', script, ...args], { ...opts, ...resolveRunPython() })
}

/** 异步捕获子进程输出（超时 = 杀进程 + `timeout: true`，与「跑完但退出非零」分开报）。 */
function capture(
  tail: string[],
  opts: { timeoutMs?: number; python: string; env: Record<string, string> },
): Promise<RunPythonResult> {
  return new Promise<RunPythonResult>((resolve) => {
    let settled = false
    const done = (r: RunPythonResult): void => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(r)
    }
    const child = spawn(opts.python, tail, {
      cwd: NN_TRAINING,
      env: { ...process.env, ...opts.env },
    })
    const out: Buffer[] = []
    const err: Buffer[] = []
    let timeout = false
    const timer = setTimeout(() => {
      timeout = true
      child.kill('SIGKILL')
    }, opts.timeoutMs ?? 300_000)
    timer.unref?.()
    child.stdout?.on('data', (d: Buffer) => out.push(d))
    child.stderr?.on('data', (d: Buffer) => err.push(d))
    // 起不来（解释器/venv 缺失 = ENOENT）不抛，按「跑完但没成功」表达（与 detach 路同口径：
    // 读失败不把请求路径炸掉，由调用方把原因翻成人话）。
    child.on('error', (e) => done({ code: null, stdout: '', stderr: e.message, timeout: false }))
    child.on('close', (code) =>
      done({
        code,
        stdout: Buffer.concat(out).toString('utf8'),
        stderr: Buffer.concat(err).toString('utf8'),
        timeout,
      }),
    )
  })
}
