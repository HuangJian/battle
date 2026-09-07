/** venv.ts — venv 真实解释器解析 + bootstrap 委派（torch 安装决策只活在 bootstrap.py）。
 *
 *  §352（2026-09-07）门禁分层：**python 环境本身是硬要求**（venv 坏/解释器不可执行
 *  ⇒ 红），**torch 只是降级条件**（缺席 ⇒ 相关测试跳过 + 可见 warning）。

 *  - 解析 uv venv 跳板：.venv\Scripts\python.exe 是 trampoline，真正干活的是它另起的
 *    基础解释器子进程；只杀跳板会留下孤儿继续占端口。读 pyvenv.cfg 的
 *    executable/home 直取真实解释器，第三方包由 PYTHONPATH 挂 venv site-packages。
 *  - venv/torch 未就绪时委派 nn-training/bootstrap.py（探测 GPU → 选 torch 变体 →
 *    uv sync → 装后自检）——与原 start-training.sh/.ps1 的行为逐项等价。
 */

import { existsSync, readdirSync, readFileSync } from 'fs'
import path from 'path'
import { NN_TRAINING } from './paths'
import { fail, log } from './log'

/** 解析 venv 真实解释器 + site-packages（POSIX/Windows 布局都覆盖）。 */
export function resolveVenvPython(): { python: string; sitePackages: string } {
  const venv = path.join(NN_TRAINING, '.venv')
  let python =
    process.platform === 'win32'
      ? path.join(venv, 'Scripts', 'python.exe')
      : path.join(venv, 'bin', 'python3')
  try {
    const cfg = readFileSync(path.join(venv, 'pyvenv.cfg'), 'utf-8')
    const exe = /^executable\s*=\s*(.+)$/m.exec(cfg)?.[1]?.trim()
    const home = /^home\s*=\s*(.+)$/m.exec(cfg)?.[1]?.trim()
    if (exe && existsSync(exe)) python = exe
    else if (home) {
      const winPy = path.join(home, 'python.exe')
      const posixPy = path.join(home, 'bin', 'python3')
      if (existsSync(winPy)) python = winPy
      else if (existsSync(posixPy)) python = posixPy
    }
  } catch {
    /* no pyvenv.cfg → 用 venv 入口（非 uv 创建的 venv 不跳板） */
  }
  // site-packages：优先 Windows 布局 Lib\site-packages（本 venv 同时存在 POSIX 残留
  // lib\python3.x\，其中并无实际包）；POSIX 机器回退 lib/python3.x/
  let sitePackages = ''
  const winSp = path.join(venv, 'Lib', 'site-packages')
  if (existsSync(winSp)) {
    sitePackages = winSp
  } else {
    try {
      const libDir = path.join(venv, 'lib')
      const v = readdirSync(libDir).find((d) => d.startsWith('python3'))
      if (v) sitePackages = path.join(libDir, v, 'site-packages')
    } catch {
      /* 无 lib 目录 */
    }
  }
  return { python, sitePackages }
}

/** venv 里的 python 是否能 import torch+numpy（torch 就绪判定）。uv 跳板真身
 *  直启时第三方包靠 PYTHONPATH 挂 site-packages（与训练子进程同规）。 */
export function venvTorchReady(): boolean {
  const { python, sitePackages } = resolveVenvPython()
  if (!existsSync(python)) return false
  const r = Bun.spawnSync([python, '-c', 'import torch, numpy'], {
    cwd: NN_TRAINING,
    env: { ...process.env, ...(sitePackages ? { PYTHONPATH: sitePackages } : {}) },
    stdout: 'ignore',
    stderr: 'ignore',
  })
  return r.exitCode === 0
}

/** python 门禁状态（§352）：pyOk/torchReady 语义分离的单一探测点。
 *
 *  - `pyOk`      —— venv 解释器**存在且能真跑一条语句**（探针 `pass`）。venv 布局
 *                  错（Windows venv 在 Linux）或解释器坏都会暴露。这是**硬要求**：
 *                  假 → 调用方报错（绝不静默跳过）。
 *  - `torchReady`—— 同一解释器能 `import torch`。只是**降级条件**：假 →
 *                  调用方跳过 torch 相关测试并打 warning，门禁仍绿。
 *  - `reason`    —— pyOk=false 时的人类可读原因；`python` = 解析出的解释器路径。
 */
export function pythonGateState(): {
  pyOk: boolean
  torchReady: boolean
  python: string
  reason: string | null
} {
  const { python } = resolveVenvPython()
  if (!existsSync(python)) {
    return {
      pyOk: false,
      torchReady: false,
      python,
      reason: `venv 解释器不存在（${python}）——在 nn-training/ 下运行 python3 bootstrap.py 重建`,
    }
  }
  const probe = Bun.spawnSync([python, '-c', 'pass'], {
    cwd: NN_TRAINING,
    stdout: 'ignore',
    stderr: 'pipe',
  })
  if (probe.exitCode !== 0) {
    const msg = probe.stderr.toString().trim().split('\n').slice(-1)[0] || '未知错误'
    return {
      pyOk: false,
      torchReady: false,
      python,
      reason: `venv 解释器不可执行（exit ${probe.exitCode}）：${msg}——布局不匹配时在 nn-training/ 下 python3 bootstrap.py --recreate 重建`,
    }
  }
  return { pyOk: true, torchReady: venvTorchReady(), python, reason: null }
}

function findSystemPython(): string | null {
  for (const name of process.platform === 'win32'
    ? ['python3', 'python', 'py']
    : ['python3', 'python']) {
    const found = Bun.which(name)
    if (found) return name
  }
  return null
}

/** 确保 venv+torch 就绪；缺了委派 bootstrap.py（GPU 探测/变体选择/uv sync/自检）。
 *  返回 false = 引导失败（调用方报错退出，退出码 4 对齐旧启动器约定）。 */
export function ensureVenv(): boolean {
  if (venvTorchReady()) return true
  const sysPy = process.env.PYTHON || findSystemPython()
  if (!sysPy) {
    fail('找不到系统 Python。请安装 Python 3.10+，或设 PYTHON 指向有效 python。')
    return false
  }
  log('venv/torch 未就绪 -> 委派 bootstrap.py（探测 GPU → 选变体 → uv sync → 自检）')
  const args = sysPy === 'py' ? [sysPy, '-3', 'bootstrap.py'] : [sysPy, 'bootstrap.py']
  // PYTHONUTF8=1：zh-CN Windows 控制台 cp936 会让 bootstrap 的 ⚠/✓ 输出炸
  // UnicodeEncodeError（§17.6）；强制 utf-8 与训练子进程同规。
  const r = Bun.spawnSync(args, {
    cwd: NN_TRAINING,
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
    stdout: 'inherit',
    stderr: 'inherit',
  })
  if (r.exitCode !== 0) {
    fail(`bootstrap.py 失败（退出码 ${r.exitCode}）。看上方输出。`)
    return false
  }
  if (!venvTorchReady()) {
    fail('torch 仍无法导入。查看上方 bootstrap.py 输出。')
    return false
  }
  return true
}

/** torch 线程数决策：--torch-threads 显式 > rl-config rl.torch_threads > CPU 数 clamp 1..12。 */
export function resolveTorchThreads(cliThreads: number, cfgThreads: number | undefined): number {
  if (cliThreads > 0) return cliThreads
  if (cfgThreads && cfgThreads > 0) return cfgThreads
  let n = navigator.hardwareConcurrency || 4
  if (n > 12) n = 12
  if (n < 1) n = 1
  return n
}

/** torch 线程 env（必须在任何 torch import 之前设置进子进程环境）。
 *  §17 定案（2026-08-31）：OMP≤8 时设 OMP_PROC_BIND=CLOSE 消除 HT 缓存争用（PPO 提速 -18%）。 */
export function torchThreadEnv(threads: number): Record<string, string> {
  const env: Record<string, string> = {
    OMP_NUM_THREADS: String(threads),
    OPENBLAS_NUM_THREADS: String(threads),
    MKL_NUM_THREADS: String(threads),
    PYTHONUTF8: '1',
  }
  if (threads <= 8) env.OMP_PROC_BIND = 'CLOSE'
  return env
}
