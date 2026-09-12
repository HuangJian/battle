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
import { pidAlive, portListen, shapeLoopbackNoProxy } from './net'
import { ensureVenv, resolveTorchThreads, resolveVenvPython, torchThreadEnv } from './venv'
import { loadConfig } from './config'
import { allSlotPorts, lockName, lockPathFor, slotOf, slotPort, validateCourseName } from './slots'
import { fail, info, initLog, log, ok, warn } from './log'
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

/** 从透传参数里 peek 课程名（`--course <v>` / `--course=<v>`；**不消费**，
 *  原样继续透传给训练脚本）。路径形式的取值（`--course-file x.jsonc` / 带分隔符的
 *  `--course` 路径）返回空串：课程名由 python 侧从课程文件解析，launcher 不猜。 */
export function peekCourse(scriptArgs: string[]): string {
  for (let i = 0; i < scriptArgs.length; i++) {
    const a = scriptArgs[i] ?? ''
    if (a.startsWith('--course') && a.includes('=')) {
      const v = a.slice(a.indexOf('=') + 1)
      return v.includes('/') || v.includes('\\') ? '' : v.replace(/\.jsonc$/, '')
    }
    if (a === '--course') {
      const v = scriptArgs[i + 1] ?? ''
      return v.includes('/') || v.includes('\\') ? '' : v.replace(/\.jsonc$/, '')
    }
    if (a.startsWith('--course-file')) return '' // 路径形式：锁名由 python 侧解析决定
  }
  return ''
}

/** 锁文件持有人（pid 存活才认；stale 锁交给 python 侧自清）。 */
function lockHolderOf(lockPath: string): number | null {
  if (!existsSync(lockPath)) return null
  try {
    const oldPid = Number.parseInt(readFileSync(lockPath, 'utf-8').split('|')[0] ?? '', 10)
    return Number.isInteger(oldPid) && pidAlive(oldPid) ? oldPid : null
  } catch {
    return null // unreadable lock — let python side handle
  }
}

/** pre-flight：**按课程**的实例锁预检——本课已在跑则退出 0（--force 跳过）。
 *
 *  单实例护栏的权威在 python 侧（`run_rl.py::_acquire_run_rl_lock` /
 *  `train_loop.py::acquire_lock`）；本函数只是让无头通道早退、不白启 venv。
 *  锁名唯一来源 slots.ts::lockName（与 python `train.loop_util::course_lock_path`
 *  同构）。无课程时沿用旧全局锁名（默认行为零变化）。 */
export function preflightCourseLocks(force: boolean, script: string, course = ''): void {
  if (force) return
  const kind = script === 'train_loop.py' ? 'train_loop' : script === 'run_rl.py' ? 'run_rl' : null
  if (!kind) return
  const name = lockName(course, kind)
  const holder = lockHolderOf(lockPathFor(course, kind))
  if (holder) {
    log(
      `${script} 已在运行（PID ${holder}${course ? `, course=${course}` : ''}，锁 ${name}）` +
        '，已退出。（--force 强制重启；双课并行请带上 --course）',
    )
    process.exit(0)
  }
}

/** 端口预检（槽位化）：本课槽位的 hub/metrics/push 有占用时响亮提示。
 *  不阻停（trainer 不绑这些端口；hub 由控制台起）——静默才是最危险的形态。 */
export async function preflightSlotPorts(course: string): Promise<void> {
  if (!course) return
  let cfg
  try {
    cfg = loadConfig()
  } catch {
    return
  }
  const slot = slotOf(cfg, course)
  const occupied: string[] = []
  for (const kind of ['hub', 'metrics', 'push'] as const) {
    const port = slotPort(cfg, course, kind)
    if (await portListen(port)) occupied.push(`${kind}=${port}`)
  }
  if (occupied.length > 0) {
    warn(
      `[preflight] 课程 ${course}（槽位 ${slot}）端口已占用: ${occupied.join(' ')}` +
        '——本课 hub 若未启动，可能是人工进程或槽位配错（config 的 courses 块）',
    )
  }
  // allSlotPorts 是唯一的槽位端口清单（这里只用来提示总范围，便于人工排查）
  void allSlotPorts(cfg)
}

interface ProcCmdline {
  pid: number
  name: string
  cmdline: string
}

/** 纯匹配谓词（可单测，不用真实进程表）：该命令行是否属于 (script, course) 的训练进程。
 *
 *  course 语义（S14/R3）：带课 → 只匹配 `--course[ =]<course>`（词边界，避免
 *  `s1` 命中 `s10`）；不带课 → 只匹配**同样不带课**的命令行（不能把双课时代的
 *  B 课进程当“旧调用”杀掉）。 */
export function isTrainerFor(cmdline: string, script: string, course = ''): boolean {
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  if (!new RegExp(`(?<![A-Za-z0-9_])${esc(script)}(?![A-Za-z0-9_])`).test(cmdline)) return false
  if (course) {
    return new RegExp(`--course(?:=|\\s+)${esc(course)}(?![A-Za-z0-9._-])`).test(cmdline)
  }
  return !/--course(?:=|\s+)\S/.test(cmdline)
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

/** --kill-previous：清杀**本课（script, course）**上一轮训练进程。仅匹配 python*
 *  进程、命令行含脚本名（词边界），排除自身/父进程。
 *
 *  course 匹配（plan S14/R3）：带课只杀 `--course[ =]X` 命中本课的进程；不带课只杀
 *  同样不带课的老调用——重启 A 课绝不掐掉 B 课（多课时代的致命事故面）。
 *  bun 在途局子进程不杀——自然结算落盘。 */
export async function killPreviousTrainers(script: string, course = ''): Promise<void> {
  const procs = listPythonProcesses().filter(
    (p) =>
      p.pid !== process.pid &&
      p.pid !== process.ppid &&
      p.name.startsWith('python') &&
      isTrainerFor(p.cmdline, script, course),
  )
  if (procs.length === 0) {
    log(`kill-previous: no previous trainer matched (${script}${course ? `, ${course}` : ''})`)
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

  // --echo：只打印命令、不执行，**不碰 venv/torch**。必须排在 ensureVenv() 之前 ——
  // 否则一次「纯打印」会触发 bootstrap.py 联网装 torch（pre-commit 门禁曾因此卡 40s+
  // 并以 exit 4 失败）。resolveVenvPython() 是纯路径解析（读 pyvenv.cfg），零副作用。
  if (opts.echo) {
    const { python } = resolveVenvPython()
    const echoCmd = [python, '-u', path.join(NN_TRAINING, script), ...opts.scriptArgs]
    console.log(echoCmd.map((c) => JSON.stringify(c)).join(' '))
    process.exit(0)
  }

  // 课程从透传参数 peek（不消费）；per-course 双锁预检 + 槽位端口提示。
  // 课程名 `..`/越界字符在启动任何基础设施之前响亮拒启（plan §1.1 小问题 2）：
  // 课程名会拼进锁文件名，上跳即写到目录外。
  let course = ''
  try {
    course = validateCourseName(peekCourse(opts.scriptArgs))
  } catch (e) {
    fail(e instanceof Error ? e.message : String(e))
    process.exit(2)
  }
  if (!course && opts.scriptArgs.some((a) => a === '--course' || a.startsWith('--course-'))) {
    info('课程以路径/jsonc 形式给出——实例锁预检交给 python 侧（锁名由课程 name 决定）')
  }
  preflightCourseLocks(opts.force, script, course)
  if (course) void preflightSlotPorts(course)

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

  // 校验模式：给 agent「本机到底有没有 torch」的第一手答案（--echo 已在上方提前返回）
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

  // --kill-previous（排除自身/父进程；bun 在途局不杀；只杀本课——S14/R3）
  if (opts.killPrevious) void killPreviousTrainers(script, course)

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

export interface Cli {
  opts: TrainOptions
  help: boolean
}

function usage(): void {
  console.log(`用法: bun tools/training/train.ts [--script <name>.py] [args...] [options]

  本地 CPU 训练脚本启动器（AGENTS §5.6 "never raw python" 的无头执行通道；
  训练组件的日常 启/停/冒烟/模式 管理走控制台 bun run train）。

  --script <name>.py   训练脚本（相对 nn-training/；缺省 train_loop.py；旧扁平名自动别名）
  --force              跳过本课单实例锁检查（只接管本课锁，绝不跨课抢占）
  --kill-previous      清杀本课上一轮训练进程（按 (script, --course) 匹配；
                       不带 --course 只杀同样不带课的老进程）
  --detach             分离启动（后台隐藏窗口，stdout/stderr 落盘）
  --torch-threads N    torch 线程档（缺省 rl-config rl.torch_threads，再缺省 CPU 数）
  --check              校验 venv+torch 可用即退出（打印解释器路径）
  --echo               只打印将执行的命令，不执行
  其余参数原样透传给训练脚本。

  多课程并行：请总是带上 --course <name>（锁/日志/traj 均按课程隔离）；
  不带 --course 的调用沿用旧全局锁 .run_rl.lock / .train_loop.lock。`)
}

/**
 * 纯参数解析（零副作用、不碰 venv/torch）——唯一被单元测试直接调用的表面积。
 *
 * 历史教训（2026-09-08）：CLI 的两条分支（`--check` 解释器探针、`--echo` 打印）
 * 曾用「spawn 真实 CLI」来测，结果 pre-commit 门禁 fallback 全量时触发
 * `ensureVenv()` → 联网装 torch，单用例 40s+ 且 exit 4。参数解析是纯函数，
 * 就该纯函数测；只有真要起训练的路径才允许碰 torch。
 */
export function parseCli(argv: string[]): Cli {
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
