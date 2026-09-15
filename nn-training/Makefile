# nn-training Makefile - Unified command entry for training / testing / cleanup
#
# Usage:
#   make setup          One-command env bootstrap (GPU detect -> torch variant -> uv sync)
#   make check          Lint + typecheck + 全量测试（== pre-commit 门禁，同一套）
#   make test           Full test suite（单测层 + 集成层）
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
# Note: Windows users without `make` can use `python task.py <target>` instead.

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
	python bootstrap.py

# check 直接委派给门禁脚本——单一实现，杜绝“日常命令”与“提交门禁”漂移。
check: python-gate

# 并行门禁（v3.15 2026-09-03）：ruff + mypy + pytest xdist -n 4 同时跑。
# 与 pre-commit hook 共用同一入口（tools/githook/nn-python-gate.sh）——
# 目标 = tests/（单测层）+ e2e/（集成层），NN_GATE_NPROC 可调 worker 数。
python-gate:
	bash ../tools/githook/nn-python-gate.sh

test:
	$(PREFIX) $(PYTHON) -m pytest tests/ e2e/ -n 4 -q

test-fast:
	$(PREFIX) $(PYTHON) -m pytest tests/ -n 4 -q

test-e2e:
	$(PREFIX) $(PYTHON) -m pytest e2e/ -n 4 -q

smoke:
	$(PREFIX) $(PYTHON) -c "import torch; print('torch', torch.__version__)"
	$(PREFIX) $(PYTHON) smoke_test.py

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
	$(PREFIX) $(PYTHON) weights_prune.py --keep $(WEIGHTS_KEEP) --dir weights/ --dry-run

weights-prune-apply:
	$(PREFIX) $(PYTHON) weights_prune.py --keep $(WEIGHTS_KEEP) --dir weights/ --apply

weights-update-md:
	$(PREFIX) $(PYTHON) weights_prune.py --dir weights/ --update-md
