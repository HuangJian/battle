<#
start-training.ps1 — 跨平台 torch(python) NN 训练统一启动器（PowerShell 版，与 start-training.sh 等价）

支持：Windows(Windows PowerShell / PowerShell 7) · Linux · macOS(PowerShell 7+)

为什么要有它：
  torch/numpy 只装在 nn-training/.venv 这个逐平台 venv 里，系统裸 `python` 没有
  torch —— 直接运行 `python train_bc.py`/`python train_loop.py` 必然报
  ModuleNotFoundError: torch，这就是「找不到 torch」的根因。本脚本是唯一入口：
  定位系统 python → (重新)建 venv → 依 requirements.txt 安装 torch+numpy（缺时
  才装，幂等）→ 把参数转发给所选训练脚本。

用法（与 bash 版逐项等价）：
  .\start-training.ps1                              # 默认跑 train_loop.py（连续 BC）
  .\start-training.ps1 -Check                         # 只验证 venv+torch 就绪并打印解释器路径，退出
  .\start-training.ps1 -Echo -Script smoke_test.py    # 只打印将执行的准确命令，不执行
  .\start-training.ps1 -Script train_bc.py --data-dir tmp/mix --arch student --epochs 25
  .\start-training.ps1 -Script train_rl.py --num-envs 4 --num-steps 2048
  .\start-training.ps1 -Script eval_bridge.py --data-dir <shards>
  .\start-training.ps1 -Force -TorchThreads 8

  注：-Script 之后的所有“未知”参数（--data-dir/--arch/--epochs 等）原样透传给
  目标脚本 —— 本脚本不自带参数校验（无 [CmdletBinding]），故不会把它们当绑定
  错误。真正被本脚本消费的：-Force -Check -Echo -Help -Detach -Script
  -TorchThreads；其余全部进入 $args 转发。

锁策略（同 bash 版）：本脚本不写锁文件。.train_loop.lock 由 train_loop.py 独占
管理（acquire_lock()/cleanup_lock()），避免 shell-PID / Python-PID 错配导致
Windows 双起。-Force 只在 pre-flight 跳过「已有训练运行」检查，真正清理 stale
锁仍交给 Python 端。

退出码：0=成功  2=用法错误  3=找不到系统 python  4=torch 装不上
#>

$ErrorActionPreference = 'Stop'

$Force = $false; $Check = $false; $Echo = $false; $Help = $false; $Detach = $false
$Script = 'train_loop.py'
$ScriptArgs = @()
$TorchThreads = 0

# ── CLI 解析（手写，与 bash 等价；未识别项透传）────────────────────────
# 同时接受 PowerShell 风格 (-Check) 与 POSIX 风格 (--check)：powershell -File
# 调用时参数按字面量进入 $args，不做原生参数绑定，故必须自行兼容两种前缀。
# 训练脚本自身参数均为 --xxx 长选项，不会被 ^--?name$ 误吞。
$i = 0
while ($i -lt $args.Count) {
  $a = $args[$i]
  switch -Regex ($a) {
    '^--?force$'         { $Force = $true }
    '^--?check$'         { $Check = $true }
    '^--?echo$'          { $Echo = $true }
    '^--?help$'          { $Help = $true }
    '^--?detach$'        { $Detach = $true }
    '^--?script$' {
      if ($i + 1 -lt $args.Count) { $i++; $Script = $args[$i] }
      else { Write-Host 'ERROR: --script requires a <name>.py'; exit 2 }
    }
    '^--?torch-threads$' {
      if ($i + 1 -lt $args.Count) { $i++; $TorchThreads = [int]$args[$i] }
    }
    default { $ScriptArgs += $a }
  }
  $i++
}

if ($Help) {
  Get-Content $PSCommandPath | Where-Object { $_ -match '^\s*#' } | ForEach-Object { ($_ -replace '^\s*# ?', '') }
  exit 0
}

# --script 必须是 nn-training/ 下的裸 .py 文件名
if ($Script -match '[/\\]') { Write-Host 'ERROR: --script must be a bare .py filename inside nn-training/'; exit 2 }
$ScriptDir = $PSScriptRoot
$ScriptPath = Join-Path $ScriptDir $Script
if (-not (Test-Path $ScriptPath)) { Write-Host "ERROR: script not found: $ScriptPath"; exit 2 }

# ── 平台检测 ─────────────────────────────────────────────────────────
$IsWindows = $false
if ($env:WINDIR) { $IsWindows = $true }
if (-not $IsWindows) {
  $un = (& uname -s 2>$null)
  if ($un -match 'MSYS|MINGW|CYGWIN') { $IsWindows = $true }
}
$VenvDir = Join-Path $ScriptDir '.venv'
if ($IsWindows) { $VenvPython = Join-Path $VenvDir 'Scripts\python.exe' }
else { $VenvPython = Join-Path $VenvDir 'bin\python' }
$LockFile = Join-Path $ScriptDir '.train_loop.lock'

# ── 定位系统 python ──────────────────────────────────────────────────
$SysPy = ''
if ($env:PYTHON) { $SysPy = $env:PYTHON }
if (-not $SysPy -and (Get-Command python3 -ErrorAction SilentlyContinue)) { $SysPy = 'python3' }
if (-not $SysPy -and (Get-Command python  -ErrorAction SilentlyContinue)) { $SysPy = 'python' }
if (-not $SysPy -and $IsWindows -and (Get-Command py -ErrorAction SilentlyContinue)) { $SysPy = 'py' }

function Invoke-SysPy {
  param([string[]]$PyArgs)
  if ($script:SysPy -eq 'py') { & py -3 @PyArgs }
  else { & $script:SysPy @PyArgs }
}

function Log([string]$Msg) { Write-Host "[start-training] $Msg" }

# ── bootstrap：确保 venv + torch 就绪（幂等）────────────────────────
$has = $false
if (Test-Path $VenvPython) {
  & $VenvPython -c 'import torch, numpy' 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { $has = $true }
  else { Log 'venv 存在但 torch 缺失 -> 将重装依赖' }
}

if (-not $has) {
  if (-not $SysPy) {
    Log 'ERROR: 找不到系统 Python。请安装 Python 3.10+，或设 $PYTHON 指向有效 python。'
    exit 3
  }
  if (-not (Test-Path $VenvPython)) {
    Log "creating venv: $SysPy -> $VenvDir"
    Invoke-SysPy -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { exit 3 }
  }
  Log 'installing pinned deps (torch + numpy) ...'
  & $VenvPython -m pip install --upgrade pip -q | Out-Null
  & $VenvPython -m pip install -r (Join-Path $ScriptDir 'requirements.txt')
  if ($LASTEXITCODE -ne 0) { exit 4 }
}

& $VenvPython -c 'import torch, numpy' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Log 'ERROR: torch 仍无法导入。查看上方 pip 输出（CPU index https://download.pytorch.org/whl/cpu 可能不可达）。'
  exit 4
}
$TorchVer = (& $VenvPython -c 'import torch; print(torch.__version__)' 2>$null)

# ── torch 线程 env（在任何 torch import 之前设置）────────────────────
$TT = $TorchThreads
if ($TT -le 0) {
  $n = 4
  if (Get-Command nproc -ErrorAction SilentlyContinue) { $n = [int](& nproc 2>$null) }
  elseif ($env:NUMBER_OF_PROCESSORS) { $n = [int]$env:NUMBER_OF_PROCESSORS }
  if ($n -gt 12) { $n = 12 }
  if ($n -lt 1) { $n = 1 }
  $TT = $n
}
$env:OMP_NUM_THREADS = "$TT"
$env:OPENBLAS_NUM_THREADS = "$TT"
$env:MKL_NUM_THREADS = "$TT"
$env:PYTHONUTF8 = '1'

Log "venv  python : $VenvPython"
Log "torch version: $TorchVer  (OMP threads=$TT)"
Log "script        : $ScriptPath"
if ($ScriptArgs.Count -gt 0) { Log "args          : $($ScriptArgs -join ' ')" }

# ── 只打印、不执行 ───────────────────────────────────────────────────
if ($Echo) {
  $cmd = "& '$VenvPython' -u '$ScriptPath' $($ScriptArgs -join ' ')"
  Write-Output $cmd
  exit 0
}

# ── 校验模式：给 agent「本机到底有没有 torch」的第一手答案 ──────────
if ($Check) {
  Log 'torch 可用。启动训练：  .\start-training.ps1 -Script <name>.py [args]'
  Log "或直接用解释器： & '$VenvPython' '<script>.py'"
  exit 0
}

# ── pre-flight：检测已有 train_loop 锁（仅默认目标 train_loop.py）──
if (-not $Force -and $Script -eq 'train_loop.py' -and (Test-Path $LockFile)) {
  $raw = (Get-Content $LockFile -Raw -ErrorAction SilentlyContinue)
  $OldPid = ($raw -split '\|')[0]
  if ($OldPid -match '^\d+$') {
    if (Get-Process -Id ([int]$OldPid) -ErrorAction SilentlyContinue) {
      Log "训练已在运行（PID $OldPid），已退出。（-Force 强制重启）"
      exit 0
    }
  }
  # stale 锁：交给 train_loop.py 的 acquire_lock() 清除
}

# ── 启动 ─────────────────────────────────────────────────────────────
# Windows + 显式 -Detach + 长训脚本（train_loop.py / run_rl.py）：
# Start-Process 隐藏窗口分离
# （ShellExecute 派生完全脱离控制台的进程，替代已弃用的 VBScript/wscript 方案）
# run_rl.py 无锁文件，等锁循环对它只是无害的 15s 空转后 exit 0
if ($Detach -and $IsWindows -and $Script -in @('train_loop.py', 'run_rl.py')) {
  Log 'detaching via Start-Process（后台隐藏窗口，stdout/stderr 落盘）...'
  $argStr = "-u `"$ScriptPath`""
  foreach ($x in $ScriptArgs) { $argStr += " `"$x`"" }
  # 写日记：detach 的 python stdout/stderr 原先随隐藏窗口丢弃——2026-08-24
  # it2/it3 静默跳轮事故中无任何可复盘痕迹。每次启动独立时间戳日志，不覆盖历史。
  $repoTmp = Join-Path (Split-Path $ScriptDir -Parent) 'tmp'
  if (-not (Test-Path $repoTmp)) { New-Item -ItemType Directory -Path $repoTmp | Out-Null }
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $tag = $Script -replace '\.py$', ''
  $outLog = Join-Path $repoTmp ("{0}-{1}.out.log" -f $tag, $stamp)
  $errLog = Join-Path $repoTmp ("{0}-{1}.err.log" -f $tag, $stamp)
  Start-Process -FilePath $VenvPython -ArgumentList $argStr -WindowStyle Hidden `
    -WorkingDirectory $ScriptDir `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
  Log "logs: $outLog"
  Log "logs: $errLog"
  for ($n = 0; $n -lt 30; $n++) { if (Test-Path $LockFile) { break }; Start-Sleep -Milliseconds 500 }
  exit 0
}

# 默认路径：前台执行 —— 命令完整透传，退出码由本脚本原样返回
Log "启动： & '$VenvPython' -u '$ScriptPath' $($ScriptArgs -join ' ')"
& $VenvPython -u $ScriptPath @ScriptArgs
exit $LASTEXITCODE
