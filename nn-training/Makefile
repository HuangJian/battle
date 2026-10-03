# nn-training Makefile - Unified command entry for training / testing / cleanup
#
# Usage:
#   make setup          One-command env bootstrap (GPU detect -> torch variant -> uv sync)
#   make check          Lint + typecheck + 全量测试（== pre-commit 门禁，同一套）
#   make test           Full test suite（单测层 + 集成层；NPROC/THREADS/PYTEST_DISPATCH 可覆盖）
#   make test-fast      Unit layer only（tests/，无 e2e）
#   make test-e2e       Integration layer only（e2e/）
#   make smoke          Torch import check + smoke_test
#   make clean          Remove temporary artifacts (preserves weights/)
#   make format         Auto-format with ruff
#   make lint           Lint-only with ruff
#   make typecheck      Type-check with mypy
#   make weights-prune  Show what would be pruned (dry-run)
#   make weights-prune-apply  Actually prune weights/ keeping latest K per family
#   make weights-update-md    Regenerate weights/WEIGHTS.md inventory
#
# Note: Windows users without `make` can use `python tools/task.py <target>` instead.

.PHONY: check test test-fast test-e2e smoke clean format lint typecheck setup
# 层 = 路径（2026-09-15）：tests/ = 单测层、e2e/ = 集成层。旧 `-m "not heavy"` 已删——
# tests/ 里 heavy 标记实测 0 个，那个过滤早已空转（标记唯一的家 e2e/ 又从未被过滤）。
# e2e/ 自 60e5f69 起 hermetic（FakeServer + tmp 落盘，不需 bun / 真节点 / weights），
# 因此它进了门禁：`make check` 与 pre-commit 判定集完全一致，不会“hook 严、日常松”。
.PHONY: weights-prune weights-prune-apply weights-update-md

PYTHON        ?= python
VENV_DIR      ?= .venv
WEIGHTS_KEEP  ?= 3

# Windows 判定：OS=Windows_NT（cmd/PowerShell/Git Bash 常见）**或**存在 Windows
# 原生 venv 布局 $(VENV_DIR)/Scripts/python.exe —— 后者兜底 msys make 看不到 OS
# 的情况（2026-09-04 实测：Git Bash 下 msys make 的 $(OS) 为空，旧 ifeq 会错走
# Unix 分支，导致 PREFIX 丢失、PYTHON 退回系统 python）。
ifeq ($(OS),Windows_NT)
IS_WINDOWS := 1
else ifneq ($(wildcard $(VENV_DIR)/Scripts/python.exe),)
IS_WINDOWS := 1
endif

ifeq ($(IS_WINDOWS),1)
# DECISIONS §323（2026-09-04）：仓库内 PowerShell 调用一律 pwsh 7 —— 裸 powershell = 5.1
PREFIX = pwsh -NoProfile -Command
VENV_PY = $(VENV_DIR)/Scripts/python.exe
else
PREFIX =
VENV_PY = $(VENV_DIR)/bin/python
endif

# 有 venv 就用 venv 解释器 —— 系统裸 python 没有 torch（原 `PYTHON ?= python`
# 在新机器上必然踩 ModuleNotFoundError: torch）。
ifneq ($(wildcard $(VENV_PY)),)
PYTHON = $(VENV_PY)
endif

# 一条指令搭环境：探测 GPU → 选 torch 变体 → uv sync → 装后自检
setup:
	python tools/bootstrap.py

# check 直接委派给门禁脚本——单一实现，杜绝“日常命令”与“提交门禁”漂移。
check: python-gate

# 并行门禁（v3.15 2026-09-03；v3.17 2026-09-17 并行策略重调）：ruff + mypy +
# pytest xdist 同时跑。与 pre-commit hook 共用同一入口（tools/githook/nn-python-gate.sh），
# 目标 = tests/（单测层）+ e2e/（集成层）；该脚本自己定 worker 数 = min(核数, 12)、
# CPU 内线程 = 1（旋钮 NN_GATE_NPROC / NN_GATE_THREADS，实测数据见脚本头注）。
python-gate:
	bash ../tools/githook/nn-python-gate.sh

# ---- 分层调试入口的并行旋钮：worker = **物理核数**（2026-10-03 起），CPU 内线程 = 1 ----
# 两者**必须一起**（2026-09-17 实测，16 核）：写死 `-n 4` + 默认线程 = 4×16 超订 ⇒
# 全量 36.3s；只加 worker 更慢（n=12 默认线程 44.7s）；封到 1 线程后 n=12/auto ≈ 23s。
# 完整数据与推理在门禁脚本头注；覆盖：`make test NPROC=8` / `make test THREADS=0`。
NPROC   ?= $(if $(PHYSICAL_CORES),$(PHYSICAL_CORES),auto)
THREADS ?= 1
export OMP_NUM_THREADS = $(THREADS)
export MKL_NUM_THREADS = $(THREADS)
export OPENBLAS_NUM_THREADS = $(THREADS)

# ---- 分发器（2026-09-29，docs/nn/engineering.md §50）：Linux = 「收集一次 + fork」----
# 判据与门禁同一条：① 有没有 `os.fork`（Windows 没有）② 内核是不是 Linux（macOS 上
# master 在 fork 前已经 import torch，libgomp/dyld 与 fork 的组合本仓未验证）。
# `NPROC` 缺省 = `platform_utils.physical_cores()`（物理核数，超线程不计入；cgroup 配额/
# 亲和掩码取小）——与门禁脚本 / tools/task.py / CI 同一口径；`NPROC=8` 仍然照给，
# `PYTEST_DISPATCH` 亦可整体覆盖（`NPROC=auto` 也留着：那是 forkdist 里现成的同义入口）。
# 16 核实测（tests/+e2e/）：wall 21.99 → 17.57s、user 233.9 → 121.6s。
UNAME_S := $(shell uname -s 2>/dev/null)
# 求值用 venv python（`-c` 下 sys.path[0] = cwd = nn-training ⇒ `common` 可 import）；
# 探测失败 ⇒ 空 ⇒ NPROC 回落 `auto`（forkdist 那边会响亮报错，不静默给错数）。
PHYSICAL_CORES := $(shell $(PYTHON) -c "from common.platform_utils import physical_cores as p; print(p())" 2>/dev/null)
ifeq ($(UNAME_S),Linux)
PYTEST_DISPATCH ?= -p tools.forkdist --forkdist $(NPROC)
else
PYTEST_DISPATCH ?= -n $(NPROC)
endif

test:
	$(PREFIX) $(PYTHON) -m pytest tests/ e2e/ $(PYTEST_DISPATCH) -q

test-fast:
	$(PREFIX) $(PYTHON) -m pytest tests/ $(PYTEST_DISPATCH) -q

test-e2e:
	$(PREFIX) $(PYTHON) -m pytest e2e/ $(PYTEST_DISPATCH) -q

smoke:
	$(PREFIX) $(PYTHON) -c "import torch; print('torch', torch.__version__)"
	$(PREFIX) $(PYTHON) tools/smoke_test.py

format:
	$(PREFIX) $(PYTHON) -m ruff format .
	$(PREFIX) $(PYTHON) -m ruff check --fix .

lint:
	$(PREFIX) $(PYTHON) -m ruff check .

typecheck:
	$(PREFIX) $(PYTHON) -m mypy . --config-file pyproject.toml

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -f *.log tmp/*.log tmp/*.out.log tmp/*.err.log dist-agent-meta.jsonl 2>/dev/null || true
	@echo "Cleaned. weights/ preserved."

# ---- weights pruning (NEW) ----
weights-prune:
	$(PREFIX) $(PYTHON) tools/weights_prune.py --keep $(WEIGHTS_KEEP) --dir weights/ --dry-run

weights-prune-apply:
	$(PREFIX) $(PYTHON) tools/weights_prune.py --keep $(WEIGHTS_KEEP) --dir weights/ --apply

weights-update-md:
	$(PREFIX) $(PYTHON) tools/weights_prune.py --dir weights/ --update-md
