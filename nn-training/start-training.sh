#!/usr/bin/env bash
# start-training.sh — 跨平台 torch(python) NN 训练统一启动器
#
#   macOS · Linux · WSL · Windows (Git Bash / MSYS2 / Cygwin)
#
# 为什么要有它：
#   torch / numpy 只安装在 nn-training/.venv 这个逐平台的 venv 里，
#   系统裸 `python`/`python3` 解释器**没有 torch** —— 直接运行
#   `python train_bc.py` / `python train_loop.py` 必然报
#   ModuleNotFoundError: torch，这就是「找不到 torch」的根因。
#   本脚本是唯一入口：定位系统 python → 若 venv+torch 未就绪则委派 bootstrap.py
#   （探测 GPU → 选 torch 变体 → uv sync → 装后自检）→ 把参数转发给训练脚本。
#
# 用法：
#   ./start-training.sh                                # 默认跑 train_loop.py（连续 BC）
#   ./start-training.sh --check                          # 只验证 venv+torch 存在并打印解释器路径，退出
#   ./start-training.sh --echo --arch variant          # 只打印将执行的准确命令，不执行
#   ./start-training.sh --script train_bc.py --data-dir tmp/mix --arch student --epochs 25
#   ./start-training.sh --script train_rl.py --num-envs 4 --num-steps 2048
#   ./start-training.sh --script eval_bridge.py --data-dir <shards>
#   ./start-training.sh --rounds 5 --epochs-per-round 60 --lr 1e-3    # 转发给 train_loop.py
#   ./start-training.sh --force --torch-threads 8
#   ./start-training.sh --kill-previous --detach --script run_rl.py  # 一键带杀重启 RL
#
# 锁策略（沿袭原版）：本脚本不写锁文件。.train_loop.lock 由 train_loop.py 独占
# 管理（通过 acquire_lock()/cleanup_lock()），避免 shell-PID / Python-PID 错配
# 导致的 Windows 双起。--force 只在脚本侧 pre-flight 跳过「已有训练运行」检查，
# 真正清理 stale 锁仍交给 Python 端。--kill-previous（R6 新增）：启动前按
# --script 名清杀旧的 python 训练进程（pgrep -f 匹配；排除自身/$PPID，仅杀
# 命令行含 python 的进程防误伤）——专为无锁的 run_rl.py 一键带杀重启；bun 在途
# 局子进程不杀，自然结算落盘供断点续跑回收。
#
# 退出码：0=成功  2=用法错误  3=找不到系统 python  4=torch 装不上
#
# 注：不用 set -u（nounset）。macOS 自带 bash 3.2 在 set -u 下展开空数组
# "${arr[@]}" 会报 unbound variable，破坏对 macOS 的跨平台支持；用
# pipefail 保证管道失败可见，空数组按需用 "${arr[@]+...}" 惯用写法。

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
LOCK_FILE="$SCRIPT_DIR/.train_loop.lock"

# ── 平台检测 ─────────────────────────────────────────────────────────
IS_WINDOWS=0
if [ -n "$WINDIR" ]; then IS_WINDOWS=1; fi
if command -v uname >/dev/null 2>&1; then
  case "$(uname -s 2>/dev/null)" in
    MSYS*)  IS_WINDOWS=1 ;;
    MINGW*) IS_WINDOWS=1 ;;
    CYGWIN*) IS_WINDOWS=1 ;;
  esac
fi

# venv 内 python 路径：Windows = Scripts/python.exe，Unix = bin/python
if [ "$IS_WINDOWS" = "1" ]; then
  VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
else
  VENV_PYTHON="$VENV_DIR/bin/python"
fi

# ── 辅助函数 ──────────────────────────────────────────────────────────
log() { echo "[start-training] $*"; }

# POSIX -> Windows 原生路径（MSYS / Git-Bash 下 exec 原生 python.exe /
# pwsh.exe 必需）。避免 MSYS 自动路径转换把 /d/... 二次扭曲成
# D:\d\... 这类病态形式。优先用 cygpath；不可用时退化到手动 /c/x -> c:\x。
to_win_path() {
  local p="$1"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$p"
  else
    p="${p#/}"                                   # 去前导 /
    local drive="${p%%/*}"; p="${p#*/}"
    printf '%s:\\%s' "$drive" "${p//\//\\}"
  fi
}

# ── 解析 CLI ─────────────────────────────────────────────────────────
FORCE=0; CHECK=0; ECHO=0; HELP=0; DETACH=0; KILLPREV=0
SCRIPT="train_loop.py"
SCRIPT_ARGS=()
TT_CLI=""

CLI_ARGS=("$@")
I=0
while [ "$I" -lt "$#" ]; do
  A="${CLI_ARGS[$I]}"; I=$((I+1))
  case "$A" in
    --force)   FORCE=1 ;;
    --kill-previous|--KillPrevious|--killprevious) KILLPREV=1 ;;
    --check)   CHECK=1 ;;
    --echo)    ECHO=1 ;;
    --help)    HELP=1 ;;
    --detach)  DETACH=1 ;;
    --script)
      if [ "$I" -lt "$#" ]; then
        SCRIPT="${CLI_ARGS[$I]}"; I=$((I+1))
      else
        echo "ERROR: --script requires a <name>.py"; exit 2
      fi
      ;;
    --torch-threads|--TorchThreads|--torch_threads)
      if [ "$I" -lt "$#" ]; then TT_CLI="${CLI_ARGS[$I]}"; I=$((I+1)); fi
      ;;
    *) SCRIPT_ARGS+=("$A") ;;
  esac
done

if [ "$HELP" = "1" ]; then
  grep -E '^#' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

# --script 必须是 nn-training/ 下的裸 .py 文件名
case "$SCRIPT" in
  */*|*\\*) echo "ERROR: --script must be a bare .py filename inside nn-training/"; exit 2 ;;
esac
SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT"
if [ ! -f "$SCRIPT_PATH" ]; then
  echo "ERROR: script not found: $SCRIPT_PATH"; exit 2
fi

# ── Windows 原生路径（仅 MSYS 下需要，避免 MSYS 路径转换双重化）──
# 在 Windows 上把 venv/python 解释器与训练脚本路径、以及 DETACH 委托的
# .ps1 路径都转成 Windows 原生形式；最终 exec 再配合 MSYS_NO_PATHCONV=1
# 一次性禁止 MSYS 二次改写，彻底消除 /d/ -> D:\d\ 的扭曲。
if [ "$IS_WINDOWS" = "1" ]; then
  VENV_PYTHON_W="$(to_win_path "$VENV_PYTHON")"
  SCRIPT_PATH_W="$(to_win_path "$SCRIPT_PATH")"
  PS1_PATH_W="$(to_win_path "$SCRIPT_DIR/start-training.ps1")"
else
  VENV_PYTHON_W="$VENV_PYTHON"
  SCRIPT_PATH_W="$SCRIPT_PATH"
  PS1_PATH_W="$SCRIPT_DIR/start-training.ps1"
fi

# ── 定位系统 python ──────────────────────────────────────────────────
SYS_PY=""
if [ -n "$PYTHON" ] && command -v "$PYTHON" >/dev/null 2>&1; then SYS_PY="$PYTHON"; fi
if [ "$SYS_PY" = "" ] && command -v python3 >/dev/null 2>&1; then SYS_PY="python3"; fi
if [ "$SYS_PY" = "" ] && command -v python  >/dev/null 2>&1; then SYS_PY="python"; fi
if [ "$SYS_PY" = "" ] && [ "$IS_WINDOWS" = "1" ] && command -v py >/dev/null 2>&1; then SYS_PY="py"; fi

# 用系统 python 执行（Windows 的 py launcher 需带 -3）
run_sys_py() {
  if [ "$SYS_PY" = "py" ]; then "$SYS_PY" -3 "$@"
  else "$SYS_PY" "$@"
  fi
}

# ── bootstrap：确保 venv + torch 就绪（幂等）─────────────────────────
# 2026-09-04 重构：安装逻辑**只在 bootstrap.py 里存在一份**。这里只做「还缺不缺」
# 的判断，缺了就把整件事委派出去。此前本函数内联了 pip install -r requirements.txt，
# 与 bootstrap.py 的 uv sync 是两套并行实现 —— 改一处漏一处，且 requirements.txt
# 与 pyproject.toml 长期双份真相。requirements.txt 已删。
bootstrap() {
  if [ -f "$VENV_PYTHON" ] && "$VENV_PYTHON" -c "import torch, numpy" >/dev/null 2>&1; then
    return 0
  fi
  log "venv/torch 未就绪 -> 委派 bootstrap.py（探测 GPU → 选变体 → uv sync → 自检）"
  run_sys_py "$SCRIPT_DIR/bootstrap.py"
  if [ $? -ne 0 ]; then
    log "ERROR: bootstrap.py 失败（退出码非 0）。看上方输出。"
    exit 4
  fi
}

bootstrap
TORCH_VER="$("$VENV_PYTHON" -c "import torch; print(torch.__version__)" 2>/dev/null)"

# ── torch 线程 env（在任何 torch import 之前设置）────────────────────
TT="$TT_CLI"
if [ "$TT" = "" ]; then
  N="$(nproc 2>/dev/null)"
  [[ "$N" =~ ^[0-9]+$ ]] || N="4"
  [ "$N" -gt 12 ] && N="12"
  [ "$N" -lt 1 ] && N="1"
  TT="$N"
fi
export OMP_NUM_THREADS="$TT"
export OPENBLAS_NUM_THREADS="$TT"
export MKL_NUM_THREADS="$TT"
# §17 定案（2026-08-31，OMP 扫档）：OMP8 + PROC_BIND=close 消除 HT 缓存争用，PPO 提速
# -18%（1260→1033s，chunk 8.4→6.9s）。仅 OMP≤8 时设（close 绑核在低线程档才有收益）。
if [ "$TT" -le 8 ] 2>/dev/null; then export OMP_PROC_BIND=CLOSE; fi
export PYTHONUTF8=1

log "venv  python : $VENV_PYTHON"
log "torch version: ${TORCH_VER:-?}  (OMP threads=$TT)"
log "script        : $SCRIPT_PATH"
if [ "${#SCRIPT_ARGS[@]}" != "0" ]; then
  log "args          : ${SCRIPT_ARGS[*]}"
fi

# ── 只打印、不执行 ───────────────────────────────────────────────────
if [ "$ECHO" = "1" ]; then
  printf '%s' "$VENV_PYTHON_W" " -u " "$SCRIPT_PATH_W"
  for X in "${SCRIPT_ARGS[@]}"; do printf ' %q' "$X"; done
  printf '\n'
  exit 0
fi

# ── 校验模式：给 agent「本机到底有没有 torch」的第一手答案 ──────────
if [ "$CHECK" = "1" ]; then
  log "torch 可用。启动训练： ./start-training.sh --script <name>.py [args]"
  log "或直接用解释器： $VENV_PYTHON_W $SCRIPT_PATH_W"
  exit 0
fi

# ── pre-flight：检测已有 train_loop 锁（仅默认目标 train_loop.py）──
if [ "$FORCE" != "1" ] && [ "$SCRIPT" = "train_loop.py" ] && [ -f "$LOCK_FILE" ]; then
  OLD_PID="$(cat "$LOCK_FILE" 2>/dev/null || true)"
  OLD_PID="${OLD_PID%%|*}"
  if [[ "$OLD_PID" =~ ^[0-9]+$ ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    log "训练已在运行（PID $OLD_PID），已退出。（--force 强制重启）"
    exit 0
  fi
  # stale 锁：交给 train_loop.py 的 acquire_lock() 清除
fi

# ── --kill-previous：按 --script 名清杀上一轮训练进程 ────────────────
# pgrep -f 匹配命令行含本 <script>.py 的进程；排除自身($$)与父 shell($PPID)，
# 且只杀命令行含 python 的进程（防误伤把脚本名当参数的外层 runner/编辑器）。
# bun 在途局子进程不杀——自然结算落盘，断点续跑可回收。
if [ "$KILLPREV" = "1" ]; then
  if command -v pgrep >/dev/null 2>&1; then
    KILLED=0
    for PID_ in $(pgrep -f "$SCRIPT" 2>/dev/null); do
      [ "$PID_" = "$$" ] && continue
      [ "$PID_" = "$PPID" ] && continue
      CMD_="$(ps -p "$PID_" -o command= 2>/dev/null || true)"
      case "$CMD_" in
        *python*) ;;
        *) continue ;;
      esac
      if kill -9 "$PID_" 2>/dev/null; then
        log "kill-previous: killed pid=$PID_ ($SCRIPT)"
        KILLED=$((KILLED + 1))
      fi
    done
    if [ "$KILLED" -gt 0 ]; then
      sleep 1
    else
      log "kill-previous: no previous trainer matched ($SCRIPT)"
    fi
  else
    log "kill-previous: pgrep unavailable — skipped"
  fi
fi

# ── 启动 ─────────────────────────────────────────────────────────────
# Windows + 显式 --detach + 目标是 train_loop.py：委托给 ps1 的 Start-Process
# 隐藏窗口分离（替代旧 VBS；detach 行为单一定义在 ps1，避免两处维护）。
# bash 数组逐元素传给 -File，ps1 侧自行解析，无引号拼接问题。
if [ "$DETACH" = "1" ] && [ "$IS_WINDOWS" = "1" ] && [ "$SCRIPT" = "train_loop.py" ]; then
  log "detaching via PowerShell Start-Process（后台）..."
  PS_LAUNCH_ARGS=(--detach)
  if [ "${#SCRIPT_ARGS[@]}" != "0" ]; then
    PS_LAUNCH_ARGS+=("${SCRIPT_ARGS[@]}")
  fi
  pwsh -NoProfile -ExecutionPolicy Bypass -File "$PS1_PATH_W" "${PS_LAUNCH_ARGS[@]}"
  exit 0
fi

# 默认路径：前台 exec —— 信号直达 python，Ctrl-C 可干净停止
# MSYS_NO_PATHCONV=1 阻止 MSYS 对已经转成 Windows 原生的路径再做一次
# /d/ -> D:\ 转换（那样会变成病态的 D:\d\...）。相对参数（如 tmp/mix）
# 保持原样传给 Windows python，其本身能正确处理正斜杠相对路径。
log "启动： $VENV_PYTHON_W -u $SCRIPT_PATH_W ${SCRIPT_ARGS[*]}"
MSYS_NO_PATHCONV=1 exec "$VENV_PYTHON_W" "-u" "$SCRIPT_PATH_W" "${SCRIPT_ARGS[@]}"
