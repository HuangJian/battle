/** train.ts — 本地 CPU 训练脚本启动器 + CLI（DECISIONS §349：原 start.ts train 模式
 *  的全部能力；原 nn-training/start-training.sh/.ps1 的能力在此逐项等价）。
 *  职责（与旧双平台启动器逐项等价）：
 *    - venv+torch 未就绪 → 委派 bootstrap.py（安装逻辑只在 bootstrap.py 一份）；
 *    - torch 线程 env（任何 torch import 之前设置，含 OMP_PROC_BIND §17 定案）；
 *    - --script 解析（根裸名 / 子包路径 / 旧扁平名别名，DECISIONS §324）+ 路径守卫；
 *    - pre-flight：train_loop.py 锁检查（--force 跳过）；
 *    - --kill-previous：按脚本名清杀旧 python 训练进程（Bun 原生实现，替代
 *      pgrep/CIM 双平台分支——见 killPreviousTrainers）；
 *    - 冒烟门禁：启动前跑轻量冒烟（venv torch import + 权重文件契约 + BCV2 回环）；
 *    - 前台/后台（--detach）执行，退出码原样返回。
 *
 *  平台纪律：不调用 pgrep/netstat/pwsh 等平台命令；进程清杀走 /proc（POSIX）与
 *  Windows 命令行快照（Bun spawn wmic 一次性——wmic 在全部主流 Windows 仍内置，
 *  不属于 shell 语法分支，且失败时静默降级为"跳过清杀"并告警）。
 *
 *  CLI（直跑本文件）：`bun tools/training/train.ts [--script <name>.py] [args...]
 *    [--force] [--kill-previous] [--detach] [--torch-threads N] [--check] [--echo]`。
 *  未知参数原样透传给训练脚本。训练组件的日常管理走控制台；本 CLI 是"never
 *  raw python"规则（AGENTS §5.6）的无头执行通道（CI / 脚本 / 一次性脚本）。
 */

import { closeSync, existsSync, openSync, readdirSync, readFileSync } from 'fs'
import path from 'path'
import { LOG_DIR, NN_TRAINING, fmtStamp } from './paths'
import { pidAlive, shapeLoopbackNoProxy } from './net'
import { ensureVenv, resolveTorchThreads, resolveVenvPython, torchThreadEnv } from './venv'
import { loadConfig } from './config'
import { fail, info, initLog, log, ok } from './log'
import { containerSmoke, summarizeSmoke, weightsSmoke } from './smoke'

/** --script 旧扁平名别名（DECISIONS §324，2026-09-04；与旧启动器同一映射）。 */
const LEGACY_ALIAS: Record<string, string> = {
  'train_bc.py': 'train/bc.py',
  'train_goal_bc.py': 'train/goal_bc.py',
  'train_intent_probe.py': 'train/intent_probe.py',
  'eval_bridge.py': 'scripts/eval_bridge.py',
  'eval_intent_m5.py': 'scripts/eval_intent_m5.py',
  'gen_self_inj.py': 'scripts/gen_self_inj.py',
  'init_scratch_weights.py': 'scripts/init_scratch_weights.py',
  'validate_export.py': 'scripts/validate_export.py',
  'train_rl.py': 'run_rl.py',
}

export interface TrainOptions {
  script: string
  scriptArgs: string[]
  force: boolean
  killPrevious: boolean
  echo: boolean
  check: boolean
  detach: boolean
  torchThreads: number
}

/** 解析 --script：别名归一 + 路径守卫（相对 nn-training/，拒绝绝对/盘符/越级）。 */
export function resolveTrainScript(raw: string): string {
  let s = raw || 'train_loop.py'
  if (LEGACY_ALIAS[s]) {
    log(`alias: ${s} -> ${LEGACY_ALIAS[s]} (DECISIONS §324)`)
    s = LEGACY_ALIAS[s]!
  }
  if (
    s === '' ||
    s.includes('\\') ||
    s.startsWith('/') ||
    /^[A-Za-z]:/.test(s) ||
    s.split(/[\\/]/).includes('..')
  ) {
    fail(`--script must be a .py path inside nn-training/ (no leading /, drive, \\, or ..): ${s}`)
    process.exit(2)
  }
  const abs = path.join(NN_TRAINING, s)
  if (!existsSync(abs)) {
    fail(`script not found: ${abs}`)
    process.exit(2)
  }
  return s
}

/** train_loop.py 的 pre-flight：锁文件持有人存活 → 已在训练，退出 0（--force 跳过）。 */
export function preflightTrainLoopLock(force: boolean, script: string): void {
  if (force || script !== 'train_loop.py') return
  const lockFile = path.join(NN_TRAINING, '.train_loop.lock')
  if (!existsSync(lockFile)) return
  try {
    const raw = readFileSync(lockFile, 'utf-8')
    const oldPid = Number.parseInt(raw.split('|')[0] ?? '', 10)
    if (Number.isInteger(oldPid) && pidAlive(oldPid)) {
      log(`训练已在运行（PID ${oldPid}），已退出。（--force 强制重启）`)
      process.exit(0)
    }
    // stale 锁：交给 train_loop.py 的 acquire_lock() 清除
  } catch {
    /* unreadable lock — let python side handle */
  }
}

interface ProcCmdline {
  pid: number
  name: string
  cmdline: string
}

/** 进程命令行快照（Bun 原生尽力实现）：POSIX 读 /proc；Windows 用 wmic 单次调用
 *  （wmic 为 OS 内置组件而非 shell 命令；不可用时返回空 → 调用方跳过清杀并告警）。 */
function listPythonProcesses(): ProcCmdline[] {
  const out: ProcCmdline[] = []
  if (process.platform === 'win32') {
    try {
      const r = Bun.spawnSync(
        [
          'wmic',
          'process',
          'where',
          "name like 'python%'",
          'get',
          'ProcessId,CommandLine',
          '/format:csv',
        ],
        { stdout: 'pipe', stderr: 'ignore' },
      )
      for (const line of r.stdout.toString().split(/\r?\n/)) {
        // CSV: Node,CommandLine,ProcessId
        const m = line.match(/^(?:[^,]*),(.+),(\d+)$/)
        if (m) out.push({ pid: Number(m[2]), name: 'python', cmdline: m[1] ?? '' })
      }
    } catch {
      /* wmic unavailable — empty */
    }
    return out
  }
  try {
    const dir = '/proc'
    for (const ent of readdirSync(dir)) {
      if (!/^\d+$/.test(ent)) continue
      try {
        const comm = readFileSync(path.join(dir, ent, 'comm'), 'utf-8').trim()
        if (!comm.startsWith('python')) continue
        const cmdline = readFileSync(path.join(dir, ent, 'cmdline'), 'utf-8')
          .split('\0')
          .filter(Boolean)
          .join(' ')
        out.push({ pid: Number(ent), name: comm, cmdline })
      } catch {
        /* process vanished */
      }
    }
  } catch {
    /* not a POSIX system */
  }
  return out
}

/** --kill-previous：按脚本名清杀上一轮训练进程。仅匹配 python* 进程、命令行含
 *  脚本名（词边界），排除自身/父进程。bun 在途局子进程不杀——自然结算落盘。 */
export async function killPreviousTrainers(script: string): Promise<void> {
  const pat = new RegExp(
    `(?<![A-Za-z0-9_])${script.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![A-Za-z0-9_])`,
  )
  const procs = listPythonProcesses().filter(
    (p) =>
      p.pid !== process.pid &&
      p.pid !== process.ppid &&
      p.name.startsWith('python') &&
      pat.test(p.cmdline),
  )
  if (procs.length === 0) {
    log(`kill-previous: no previous trainer matched (${script})`)
    return
  }
  for (const p of procs) {
    log(`kill-previous: stopping pid=${p.pid} (${p.name})`)
    try {
      process.kill(p.pid, 'SIGKILL')
    } catch {
      /* already dead */
    }
  }
  await new Promise((r) => setTimeout(r, 1000))
}

/** 冒烟门禁（本地训练）：venv torch import（ensureVenv 已保证）+ BCV2 容器回环 +
 *  权重文件契约（--bc/--out/--resume 显式指定的 .json）。 */
export function trainSmoke(scriptArgs: string[]): boolean {
  const items = [containerSmoke()]
  for (let i = 0; i < scriptArgs.length; i++) {
    const a = scriptArgs[i] ?? ''
    if (
      (a === '--bc' || a === '--out' || a === '--resume') &&
      scriptArgs[i + 1]?.endsWith('.json')
    ) {
      items.push(weightsSmoke(scriptArgs[i + 1]!, false))
    }
  }
  log('运行启动冒烟测试...')
  return summarizeSmoke(items)
}

export interface TrainLaunchResult {
  /** 前台模式：子进程退出码。 */
  exitCode: number
  /** detach 模式：新进程 PID。 */
  pid?: number
  /** 实际执行的命令（--echo 打印）。 */
  cmd: string[]
  env: Record<string, string>
}

/** 启动训练（前台 exec 语义 / detach 分离语义）。 */
export function launchTraining(opts: TrainOptions): TrainLaunchResult {
  const script = resolveTrainScript(opts.script)
  preflightTrainLoopLock(opts.force, script)

  // venv+torch（缺了委派 bootstrap.py；失败退出码 4 对齐旧启动器）
  if (!ensureVenv()) process.exit(4)
  const { python, sitePackages } = resolveVenvPython()
  const cfg = (() => {
    try {
      return loadConfig()
    } catch {
      return null
    }
  })()
  const threads = resolveTorchThreads(opts.torchThreads, cfg?.rl?.torch_threads)
  const env = {
    ...torchThreadEnv(threads),
    // venv site-packages 挂 PYTHONPATH：真身解释器直启时第三方包可见（§339 陷阱 3）
    ...(sitePackages ? { PYTHONPATH: `${sitePackages}${path.delimiter}${NN_TRAINING}` } : {}),
  }

  const scriptAbs = path.join(NN_TRAINING, script)
  const torchVer = (() => {
    const r = Bun.spawnSync([python, '-c', 'import torch; print(torch.__version__)'], {
      cwd: NN_TRAINING,
      env: { ...process.env, ...env },
      stdout: 'pipe',
      stderr: 'ignore',
    })
    return r.stdout.toString().trim() || '?'
  })()
  log(`venv  python : ${python}`)
  log(`torch version: ${torchVer}  (OMP threads=${threads})`)
  log(`script        : ${scriptAbs}`)
  if (opts.scriptArgs.length > 0) log(`args          : ${opts.scriptArgs.join(' ')}`)

  const cmd = [python, '-u', scriptAbs, ...opts.scriptArgs]

  // 只打印、不执行
  if (opts.echo) {
    console.log(cmd.map((c) => JSON.stringify(c)).join(' '))
    process.exit(0)
  }
  // 校验模式：给 agent「本机到底有没有 torch」的第一手答案
  if (opts.check) {
    log('torch 可用。启动训练: bun tools/training/train.ts --script <name>.py [args]')
    log(`或直接用解释器: ${python} -u ${scriptAbs}`)
    process.exit(0)
  }

  // 冒烟门禁（check/echo 模式之后、真正启动之前）
  if (!trainSmoke(opts.scriptArgs)) {
    fail('启动冒烟未通过——放弃启动')
    process.exit(1)
  }

  // --kill-previous（排除自身/父进程；bun 在途局不杀）
  if (opts.killPrevious) void killPreviousTrainers(script)

  // Windows + 显式 --detach：分离启动（detached；日志按时间戳落盘）
  if (opts.detach) {
    const tag = script.replace(/\.py$/, '').replace(/[\\/]/g, '-')
    const outLog = path.join(LOG_DIR, `${tag}-${fmtStamp()}.out.log`)
    const errLog = path.join(LOG_DIR, `${tag}-${fmtStamp()}.err.log`)
    info(`detaching（后台隐藏窗口，stdout/stderr 落盘）: ${outLog}`)
    const outFd = openSync(outLog, 'a')
    const errFd = openSync(errLog, 'a')
    const proc = Bun.spawn({
      cmd,
      cwd: NN_TRAINING,
      env: { ...process.env, ...env },
      stdin: 'ignore',
      stdout: outFd,
      stderr: errFd,
      detached: true,
      windowsHide: true,
    })
    closeSync(outFd)
    closeSync(errFd)
    proc.unref()
    ok(`训练进程已分离启动 (PID ${proc.pid})`)
    return { exitCode: 0, pid: proc.pid, cmd, env }
  }

  // 默认路径：前台 —— 信号直达 python，Ctrl-C 可干净停止；退出码原样返回
  log(`启动: ${cmd.join(' ')}`)
  const proc = Bun.spawnSync({
    cmd,
    cwd: NN_TRAINING,
    env: { ...process.env, ...env },
    stdout: 'inherit',
    stderr: 'inherit',
    stdin: 'inherit',
  })
  return { exitCode: proc.exitCode ?? 1, cmd, env }
}

// ────────────────────────── CLI（直跑本文件） ──────────────────────────

interface Cli {
  opts: TrainOptions
  help: boolean
}

function usage(): void {
  console.log(`用法: bun tools/training/train.ts [--script <name>.py] [args...] [options]

  本地 CPU 训练脚本启动器（AGENTS §5.6 "never raw python" 的无头执行通道；
  训练组件的日常 启/停/冒烟/模式 管理走控制台 bun run train）。

  --script <name>.py   训练脚本（相对 nn-training/；缺省 train_loop.py；旧扁平名自动别名）
  --force              跳过 train_loop.py 单实例锁检查
  --kill-previous      按脚本名清杀上一轮训练进程
  --detach             分离启动（后台隐藏窗口，stdout/stderr 落盘）
  --torch-threads N    torch 线程档（缺省 rl-config rl.torch_threads，再缺省 CPU 数）
  --check              校验 venv+torch 可用即退出（打印解释器路径）
  --echo               只打印将执行的命令，不执行
  其余参数原样透传给训练脚本。`)
}

function parseCli(argv: string[]): Cli {
  const opts: TrainOptions = {
    script: 'train_loop.py',
    scriptArgs: [],
    force: false,
    killPrevious: false,
    echo: false,
    check: false,
    detach: false,
    torchThreads: 0,
  }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i] ?? ''
    switch (a) {
      case '--script': {
        const v = argv[++i] ?? ''
        if (!v) {
          console.error('ERROR: --script requires a <name>.py')
          process.exit(2)
        }
        opts.script = v
        continue
      }
      case '--force':
        opts.force = true
        continue
      case '--kill-previous':
      case '--killprevious':
        opts.killPrevious = true
        continue
      case '--echo':
        opts.echo = true
        continue
      case '--check':
        opts.check = true
        continue
      case '--detach':
        opts.detach = true
        continue
      case '--torch-threads':
      case '--torch_threads': {
        const v = Number(argv[++i])
        if (Number.isFinite(v)) opts.torchThreads = v
        continue
      }
      case '--help':
      case '-h':
        return { opts, help: true }
      default:
        opts.scriptArgs.push(a)
        continue
    }
  }
  return { opts, help: false }
}

function main(): void {
  const { opts, help } = parseCli(process.argv.slice(2))
  if (help) {
    usage()
    process.exit(0)
  }
  if (shapeLoopbackNoProxy()) info('检测到代理环境变量——已追加 NO_PROXY 直连回环')
  initLog('train-cli')
  const r = launchTraining(opts)
  process.exitCode = r.exitCode
}

// 仅直跑本文件时启动 CLI；被 import（测试/复用）不执行——避免测试导入即拉起训练。
if (import.meta.main) main()
