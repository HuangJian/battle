# nn-training Makefile - Unified command entry for training / testing / cleanup
#
# Usage:
#   make check          Lint + typecheck + test-fast
#   make test           Full test suite
#   make test-fast      Fast layer only
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

.PHONY: check test test-fast smoke clean format lint typecheck
.PHONY: weights-prune weights-prune-apply weights-update-md

PYTHON        ?= python
VENV_DIR      ?= .venv
WEIGHTS_KEEP  ?= 3

ifeq ($(OS),Windows_NT)
PREFIX = powershell -NoProfile -Command
else
PREFIX =

endif

check: lint typecheck test-fast

test: test-fast
	$(PREFIX) $(PYTHON) -m pytest tests/ -v --timeout=50000

test-fast:
	$(PREFIX) $(PYTHON) -m pytest tests/ -q

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
