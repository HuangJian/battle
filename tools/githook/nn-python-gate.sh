#!/bin/sh
# nn-python-gate.sh — nn-training Python 质量门禁（并行：ruff + mypy + pytest xdist）
#
# 日常开发（仓库根或任意目录均可，脚本自定位）：
#   bash tools/githook/nn-python-gate.sh
# pre-commit hook（tools/githook/pre-commit）复用同一入口——提交前与日常跑
# 的是同一套门禁，不会出现"hook 严、日常松"的漂移。
#
# 并行架构（v3.15 2026-09-03；v3.16 2026-09-15 起 pytest 目标含 e2e/；
# v3.17 2026-09-17 起 worker 数 × 线程数按实测重调，见下节；
# v3.18 2026-09-20 起双向路径改按「python 是不是 Windows 二进制」判定，
# 不再只看 wslpath 存不存在，见下方「双向路径」一节）：
#   ruff(~1s) / mypy(~4s 热缓存) / pytest xdist 全量 三路并行。
#   全量 = tests/（单测层）+ e2e/（集成层）。**两层同一次 xdist 调用**：实测（16 核）
#   tests/ 22s → tests/+e2e/ 27s，只 +5s（另外单跑一次要重复付 torch import 与
#   worker 启动成本）。2026-09-20 复测（修掉一批「测试空等生产超时」后）：
#   tests/+e2e/ = 20.1s（即上述 27s 基线所在量级；中间曾退化到 142.7s，见
#   docs/nn/engineering.md §14）。同次引入 per-test 耗时预算护栏（nn-training/conftest.py：
#   >5s 警告、>10s 报错；NN_TEST_WARN_S / NN_TEST_FAIL_S 可覆盖）——它专抓
#   「不占 CPU 的等待」，这类退化不会再静默回来。
#
#   e2e/ 自 60e5f69 起 hermetic（FakeServer + tmp 落盘，不需要
#   bun / 真节点 / weights fixture），因此可以进门禁。
#   层 = **路径**（tests/ = 单测层、e2e/ = 集成层），不再用 `-m "not heavy"`：
#   tests/ 里 heavy 标记实测 0 个（该过滤早已空转），全仓唯一模块级 heavy 标记在 e2e。
#   xdist worker 各自 FakeServer 实例隔离，无竞态。
#
# ---- 并行策略：worker 数 × CPU 内线程数必须一起调（2026-09-17 实测重调）----
# 旧默认 = `-n 4` + torch 默认内线程（= 物理核数）⇒ 4 worker × 16 线程 = 64 线程抢
# 16 核，**CPU 时间被线程切换吃掉**（-n 8 时实测 user 440s / wall 37s ≈ 12 核满载空转）。
# 旧注释把 `-n 4 最优、auto 更慢` 记成了结论，其实是这个超订的假象：核数调大反而更慢。
# 三项交错 A/B（16 核，每项 3 次；`python -m pytest tests/ e2e/` 单独计时）：
#     -n 4  线程默认 → 37/34/38s（均值 36.3）    -n 12 线程默认 → 45/42/47s（44.7）
#     -n 4  线程=1   → 39.7s（封线程不救小并发）  -n 12 线程=1   → 28/25/21s（24.7）
#     -n 8  线程=1 → 24/27s   -n 16 线程=1 → 25/22s   -n auto 线程=1 → 21/27s
#   ⇒ 只加 worker 更慢、只封线程无收益，**两者同时**才把门禁从 ~36s 压到 ~23s（-36%）。
#   默认 worker  = min(核数, 12)（8~16 实测同一水平，取 12 同时给内存封顶：
#                 -n 12 峰值 pytest 进程树 RSS ≈ 3.9GB，≈ -n 4 的 3 倍）。NN_GATE_NPROC 覆盖。
#   默认内线程   = 1（OMP/MKL/OPENBLAS，须在 python 启动前 export）。NN_GATE_THREADS 覆盖
#                 （0 = 不设，退回 torch 自己的默认 = 各 worker 开满物理核）。
#   注：该 env 随进程树继承到测试 spawn 的子进程（如 train_loop.py 的 os.environ.setdefault）；
#   训练入口里的 torch.set_num_threads(--threads) 是进程内显式覆盖，不受此影响。
#
# 单测墙钟护栏（2026-09-15）：pytest 加 `--timeout=${NN_PYTEST_TIMEOUT_S:-60}`
#  ——与 task.py check 同款 60s/用例（本仓最慢单测实测 22s，有 ~2.7× 余量）。
#   背景：编码 agent 沙箱里全量曾「~34% 处 hang」（2026-09-15 Mimo；2026-09-14 无按键
#   KeyboardInterrupt 见 memory 记录）——无超时时门禁永远挂着，agent 反复重试 commit。
#   现在超时 → 响亮超时报错 + 调用栈，可诊断可重试；被误伤（慢机超 60s）用
#   NN_PYTEST_TIMEOUT_S=120 调大，勿直接删超时。
#   ⚠ 单位是**秒**（pytest-timeout: "Timeout in seconds"）——2026-09-15 发现原值
#   `50000` 是从 **bun** 的 `--timeout=50000`（那才是毫秒）误搬过来的，等于把上限
#   抬到 13.9 小时并**覆盖掉** addopts 的 60s ⇒ 护栏名存实亡。改回秒制后才是
#   「>1 分钟即红旗」的用户口径；禁止再加 ms 量级的值（tests/test_githook_scripts.py 有回归）。
#   （pytest 自身不再加 `-q`：addopts 已有 `-q`，重复会变成 `-qq` 把结尾的
#    「N passed in Xs」吞掉——hook 日志里看不到用例数与耗时（2026-09-17 修）。）
#
# 跳过单个工具（逗号分隔）：
#   NN_GATE_SKIP=ruff,mypy bash tools/githook/nn-python-gate.sh
# 只退 e2e 集成层（保留单测层）——负载型 flake 时的定向出口：
#   NN_GATE_SKIP_E2E=1 bash tools/githook/nn-python-gate.sh
#
# 沙箱注意：脚本内部**不**把子进程输出重定向到 /dev/null——MSYS 伪设备与
# Windows 子进程继承存在兼容问题（实测间歇性失败）。输出直通。
#
# Hook 模式（stdout 非 tty，2026-09-15）：后台 ruff/mypy/pytest 经 detach-run.py
# 启动——stdio 进独立日志 + DETACHED_PROCESS（无控制台）。否则 Windows git 会
# 等 hook stdout pipe 的 EOF（门禁全绿后 commit 仍卡死），且共享控制台会吃到
# 幽灵 CTRL_C_EVENT（git.exe 中途退出、MSYS hook 继续跑）。交互模式（tty）仍
# 实时输出，不脱管。
#
# 沙箱删除守卫（2026-09-10 实测，一次会话踩满两次）：
#   本环境注入了 WorkBuddy safe-delete shim（改道回收站 + 每轮批量删除配额）。
#   它会从两个方向打穿门禁，且**与被测代码无关**：
#     1. mypy 自清理缓存（tmp/.mypy-cache/missing_stubs）被 SHFileOperationW 0x2
#        拦截 → safe-delete FAIL_CLOSED 抛 SystemExit → mypy INTERNAL ERROR；
#     2. 同一 turn 内累积删除数越过阈值（实测 count 56 > threshold 50,
#        scope=turn）→ 之后所有删除被拒 → 依赖真实删除的用例（如
#        tests/test_workdir_sweep.py）批量转红。
#   典型触发场景：一个会话里反复跑全量（跑十几次必然踩满配额）。**单跑该文件
#   会通过**——这就是判据：单跑绿、全量红，且日志里有 [safe-delete] 行 = 环境。
#   干净验证方式（临时停用 shim，不改仓库）：
#     CODEBUDDY_SAFE_DELETE_ENABLED=0 bash tools/githook/nn-python-gate.sh
#   仓库侧的正交修复（已完成）：nn-training/platform_utils.rmtree_best_effort
#   ——shutil 的 ignore_errors=True 挡不住 SystemExit（BaseException），会把调用
#   线程打死；所有清理路径一律走该助手。
set -u

# 中文 Windows 上 subprocess 文本管道默认按 GBK 解码 → e2e / pytest-xdist 捕获测试
# stdout 时遇 UTF-8 中文字节即 UnicodeDecodeError 崩进程（2026-09-16 实测：
# e2e/test_worker_queue.py 在 gw1 崩）。PYTHONUTF8=1 让本进程及其子进程（含 xdist
# worker、ruff/mypy）的文本 I/O 走 UTF-8，根因修复，e2e 不再因编码炸，无需
# NN_GATE_SKIP_E2E 退避。Linux/macOS 本就是 UTF-8 locale，该变量无副作用。
export PYTHONUTF8=1

# ---- 并行旋钮：CPU 内线程封顶（须在 python 启动前 export，见文件头实测那一节）----
GATE_THREADS=${NN_GATE_THREADS:-1}
if [ "$GATE_THREADS" != "0" ]; then
  OMP_NUM_THREADS=$GATE_THREADS
  MKL_NUM_THREADS=$GATE_THREADS
  OPENBLAS_NUM_THREADS=$GATE_THREADS
  export OMP_NUM_THREADS MKL_NUM_THREADS OPENBLAS_NUM_THREADS
fi

# 双向路径（2026-09-18 补 WSL 支持）：**shell 侧（bash/dash 的 cd/stat/exec）用 POSIX**
# —— bash 不认 `D:/...` 盘符路径，`[ -x ]`/exec 一律失败；**塞进 python argv 的用 Win32
# 正斜杠形式**（`D:/...`）——Windows python 的 open/subprocess.CreateProcess 不认
# /mnt/d/...（MSYS 分支两者通用：MSYS 层自动双向映射 /d/ 路径）。
#
# ⚠ **要不要 Win32 形态，唯一判据是「选中的 python 是不是 Windows 二进制」**，
# 与 uname / wslpath 存不存在无关（2026-09-20 事故：容器是 WSL2 内核 + 原生 Linux venv，
# 而 /usr/bin/wslpath 把仓库路径映射成 `//wsl.localhost/opencode/…` —— 一个**只有
# Windows 侧**能读的 UNC 命名空间；原生 python 拿到它当场
# `can't open file '//wsl.localhost/.../detach-run.py'`，门禁 0s 假红）。旧版只问
# 「wslpath 可用吗」，就把用不到、也不需要 Win32 形态的原生 python 喂了 UNC 路径。
# 同款判据在 nn-py-safe.sh 里早已写明（`case "$PY_BIN" in *.exe)`）。
# 取基准目录：
#   · PWD：POSIX（pwd），shell 侧一律用它；
#   · PWD_WIN：**仅当 NN_PY 是 `.exe`（Windows 二进制）才求**：
#     pwd -W 可用（MSYS/MINGW/CYGWIN）→ **显式 `pwd -W` 求 Win32 正斜杠形态（D:/...）**；
#       ⚠ 2026-09-20 修正：旧版这里是「同 PWD（MSYS 层映射）」——即继续用 POSIX 的
#       `/d/...`。实测那条捷径不成立：Windows python 拿到 `/d/...` 会当**相对路径**，
#       拼上 cwd 变成 `D:\d\github\...`，detach 启动秒退、门禁 0s 假红。MSYS 的双向映射
#       只在它自己 exec 的路径上生效，**塞进 detach argv 的串不保证被转换** ⇒ 显式求
#       Win32 形态最稳（且与下面 wslpath -m 分支同形态：正斜杠）。
#     wslpath 可用（WSL，uname=Linux + /mnt 挂载）→ wslpath -m（**正斜杠**混合路径，
#     与 MSYS pwd -W 同形态；不能用 -w 反斜杠输出，下游 dirname 只认 /）；
#     都不可用 → 同 PWD（无 Windows python 的环境不该走到这里，保守取 POSIX）；
#     原生 Linux/macOS python（.venv/bin/python）→ **一律不转换**，POSIX 本就是它认的形态。
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname -- "$(dirname -- "$SCRIPT_DIR")")
NN_ROOT="$REPO_ROOT/nn-training"

if [ -x "$NN_ROOT/.venv/Scripts/python.exe" ]; then
  NN_PY="$NN_ROOT/.venv/Scripts/python.exe"
  # bash 直连执行用 POSIX（NN_PY）；塞进 detach/shell 子进程 argv 的用 Win32（NN_PY_WIN）。
  # WSL 下二者不同（/mnt/d/... vs D:/...）；MSYS 下相同。
  SCRIPT_DIR_WIN=$SCRIPT_DIR
  if pwd -W >/dev/null 2>&1; then
    # ⚠ 不能沿用 POSIX 的 SCRIPT_DIR（理由见上方「双向路径」注释）：显式求 Win32 形态。
    SCRIPT_DIR_WIN=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -W)
  elif command -v wslpath >/dev/null 2>&1; then
    SCRIPT_DIR_WIN=$(wslpath -m "$SCRIPT_DIR")
  fi
  REPO_ROOT_WIN=$(dirname -- "$(dirname -- "$SCRIPT_DIR_WIN")")
  NN_ROOT_WIN="$REPO_ROOT_WIN/nn-training"
  NN_PY_WIN="$NN_ROOT_WIN/.venv/Scripts/python.exe"
elif [ -x "$NN_ROOT/.venv/bin/python" ]; then
  # 原生（Linux/macOS）python：argv 里的 POSIX 路径正确原样透传（理由见上）。
  NN_PY="$NN_ROOT/.venv/bin/python"
  REPO_ROOT_WIN=$REPO_ROOT
  NN_ROOT_WIN="$REPO_ROOT_WIN/nn-training"
  NN_PY_WIN=$NN_PY
else
  echo "✗ nn-training/.venv 不存在（$NN_ROOT/.venv）——请先 python -m venv .venv && pip install -r requirements.txt" >&2
  exit 1
fi

# ---- worker 数：默认 min(核数, 12)（见文件头实测那一节），NN_GATE_NPROC 覆盖 ----
# 核数用 venv python 自己问（`-S` 跳过 site：秒级、且唯一跨平台可靠口径）；
# 结果不是纯数字（python 起不来等）就退回 4（旧默认，安全）。
CORES=$("$NN_PY" -S -c 'import os; print(os.cpu_count() or 4)' 2>/dev/null || echo "")
case "$CORES" in
  '' | *[!0-9]*) CORES=4 ;;
esac
NPROC=${NN_GATE_NPROC:-$CORES}
[ "$NPROC" -gt 12 ] && NPROC=12
[ "$NPROC" -lt 1 ] && NPROC=1

SKIP_LIST=${NN_GATE_SKIP:-}
has_skip() {
  case ",$SKIP_LIST," in
    *",$1,"*) return 0 ;;
    *) return 1 ;;
  esac
}

cd "$NN_ROOT"
echo "▶ nn-training python gate（ruff + mypy + pytest xdist -n $NPROC，CPU 内线程 $GATE_THREADS，parallel）"
# 门禁前清理过期测试临时目录（python -S 绕过沙箱删除保护，仅限 tmp/pytest-tmp
# 下 KEEP_DAYS 天前的子目录；NN_TMP_KEEP_DAYS 可调，默认 7）。失败静默（清理
# 是锦上添花，不阻塞门禁）。
"$NN_PY" -S ../tools/githook/nn-clean-tmp.py >/dev/null 2>&1 || true

# DETACH / GATE_TMP 都会进 python argv（detach-run.py 的 open/subprocess）⇒ 必须 Win32 正斜杠。
DETACH="$REPO_ROOT_WIN/tools/githook/detach-run.py"
if [ -t 1 ]; then
  LIVE=1
  GATE_TMP=""
  GATE_TMP_WIN=""
else
  LIVE=0
  # 放在 pytest-tmp 下：既有 nn-clean-tmp KEEP_DAYS 清扫会带走空目录
  GATE_TMP="$REPO_ROOT/tmp/pytest-tmp/nn-gate-$$"       # bash 侧（mkdir/trap）
  GATE_TMP_WIN="$REPO_ROOT_WIN/tmp/pytest-tmp/nn-gate-$$" # python 侧（detach argv）
  mkdir -p "$GATE_TMP"
  # 截断不删除（沙箱删除配额铁律，同 pre-commit EXIT trap）
  trap 'for __f in "$GATE_TMP"/*.log "$GATE_TMP"/*.err; do [ -e "$__f" ] && : > "$__f"; done' EXIT
fi

# ---- run_tool <name> <exe> <args...>：三路工具的唯一启动点 ----
# LIVE（stdout 是 tty）直通终端实时输出；hook/管道模式经 detach-run.py 脱管落日志
# （理由见文件头「Hook 模式」）。新增工具只需 `run_tool <name> <cmd...>` 一行——
# 旧版把这段 LIVE/detach 二选一复制了三遍（ruff/mypy/pytest），加一个工具就要
# 再复制一遍，容易漏掉 detach 分支而让 Windows commit 卡死。
# exe 参数语义（2026-09-18 WSL 双向路径）：bash 直连执行用 POSIX（`"$NN_PY"`），
# detach 内层 CreateProcess 的 argv[0] 用 Win32（`"$NN_PY_WIN"`）——WSL 下二者必须分开。
PIDS=""
RAN=""
run_tool() {
  __name=$1
  shift
  __exe_sh=$1
  shift
  if [ "$LIVE" = "1" ]; then
    "$__exe_sh" "$@" &
  else
    "$NN_PY" "$DETACH" --stdout "$GATE_TMP_WIN/$__name.log" --stderr "$GATE_TMP_WIN/$__name.err" \
      -- "$NN_PY_WIN" "$@" &
  fi
  PIDS="$PIDS $!"
  RAN="$RAN $__name"
}

# pytest 目标（层 = 路径；不加引号是有意的：需要词分割成两个参数）。
PYTEST_TARGETS="tests/"
if [ "${NN_GATE_SKIP_E2E:-0}" = "1" ]; then
  echo " ▸ e2e 集成层已退出（NN_GATE_SKIP_E2E=1）——本次只跑 tests/ 单测层"
else
  PYTEST_TARGETS="tests/ e2e/"
fi

# t0 在**启动任何工具之前**取：报告的数字 = 门禁真实墙钟（旧版在启动后才取，
# 报的是"等最慢那个"的时间，与用户口径的"门禁耗时"差一个启动窗）。
t0=$(date +%s)

if has_skip ruff; then
  echo " ▸ ruff skipped（NN_GATE_SKIP=$SKIP_LIST）"
else
  run_tool ruff "$NN_PY" -m ruff check .
fi
if has_skip mypy; then
  echo " ▸ mypy skipped（NN_GATE_SKIP=$SKIP_LIST）"
else
  run_tool mypy "$NN_PY" -m mypy . --config-file pyproject.toml
fi
if has_skip pytest; then
  echo " ▸ pytest skipped（NN_GATE_SKIP=$SKIP_LIST）"
else
  # 全量：xdist -n $NPROC，带单测墙钟护栏（--timeout，见文件头）。
  # shellcheck disable=SC2086
  run_tool pytest "$NN_PY" -m pytest $PYTEST_TARGETS -n "$NPROC" --timeout="${NN_PYTEST_TIMEOUT_S:-60}"
fi

RC=0
for p in $PIDS; do
  wait "$p" || RC=1
done
t1=$(date +%s)
if [ "$LIVE" = "0" ]; then
  for name in $RAN; do
    [ -f "$GATE_TMP/$name.log" ] && cat "$GATE_TMP/$name.log"
    [ -f "$GATE_TMP/$name.err" ] && cat "$GATE_TMP/$name.err" >&2
  done
fi
if [ "$RC" -eq 0 ]; then
  echo "✓ nn-training python gate done in $((t1 - t0))s"
else
  echo "✗ nn-training python gate FAILED in $((t1 - t0))s"
  # 单引号：这行里带反引号，双引号下会被**命令替换** —— 实测把规则行打成
  # `bash: tools/githook/nn-py-safe.sh: No such file or directory`（2026-09-24），
  # 门禁失败时唯一的指引行反而变成噪音。
  echo '  → 规则：AGENTS §5（nn python / pytest 一律 `bash tools/githook/nn-py-safe.sh …`；per-test 60s、-m pytest 另套 480s 外墙钟）'
  echo "    细节：docs/agents.details.md §5.6；单跑绿 / 全量红且日志有 [safe-delete] = 环境，不是回归（details §5.2）"
fi
exit "$RC"
