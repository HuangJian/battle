"""CI 工作流的「引用完整性」护栏。

防的正是 60e5f69 那类事故：该提交把集成套件从 `tests/` 搬到 `e2e/`，却没有同步
`.github/workflows/nn-training.yml`，于是 CI 末步 `uv run python test_run_rl.py`
（cwd = nn-training）指向一个不存在的文件，**每跑必挂**且无 `continue-on-error`
——一直挂到 2026-09-15 才被人发现（29 个提交之后）。

本测试把「工作流里引用的文件必须真实存在」变成断言：搬迁文件时忘了改 CI 会立刻红，
而不是让 red CI 静默躺在远端。

只用 stdlib（仓库红线：python 侧仅 torch/numpy + stdlib），因此裸正则解析 YAML，
不引入 pyyaml。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

NN_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = NN_ROOT.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nn-training.yml"

pytestmark = pytest.mark.skipif(
    not WORKFLOW.is_file(), reason=f"CI 工作流不存在（非完整仓库？）：{WORKFLOW}"
)

#: `uv run python <file>` —— 显式脚本调用。
_UV_RUN_PY = re.compile(r"uv run python\s+(\S+)")
#: `[uv run] pytest <args...>` —— 取该行所有位置参数。
_PYTEST_CALL = re.compile(r"pytest\s+([^\n#]+)")


def _looks_like_path(token: str) -> bool:
    """只把像路径的 token 纳入校验（排除 `-q` / `-n` / `2` 之类）。"""
    if token.startswith("-"):
        return False
    return token.endswith("/") or token.endswith(".py")


def _code_lines(text: str) -> str:
    """剔掉整行注释（`# ...`）——注释里引用历史路径是合法记录，不该当成 CI 的真实调用。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def referenced_paths(text: str) -> set[str]:
    """工作流文本 → 被引用的、应当存在于 nn-training/ 下的相对路径集合。"""
    refs: set[str] = set()
    code = _code_lines(text)
    for m in _UV_RUN_PY.finditer(code):
        refs.add(m.group(1).strip().strip("'\"`"))
    for m in _PYTEST_CALL.finditer(code):
        for tok in m.group(1).split():
            tok = tok.strip("'\"`")
            if _looks_like_path(tok):
                refs.add(tok)
    return refs


def test_workflow_referenced_paths_exist() -> None:
    """工作流引用的每个 pytest 目标 / python 脚本都必须在 nn-training/ 下存在。"""
    refs = referenced_paths(WORKFLOW.read_text(encoding="utf-8"))
    assert refs, "工作流里没解析出任何脚本/pytest 目标——解析器或工作流结构变了？"
    missing = sorted(p for p in refs if not (NN_ROOT / p).exists())
    assert not missing, (
        "CI 工作流引用了 nn-training/ 下不存在的路径（搬文件没同步改 CI？）："
        f"{missing}（全部引用：{sorted(refs)}）"
    )


def test_workflow_covers_both_layers() -> None:
    """层 = 路径：CI 必须既跑单测层（tests/）又跑集成层（e2e/）。"""
    refs = referenced_paths(WORKFLOW.read_text(encoding="utf-8"))
    assert "tests/" in refs, f"CI 未跑单测层 tests/（引用：{sorted(refs)}）"
    assert "e2e/" in refs, f"CI 未跑集成层 e2e/（引用：{sorted(refs)}）"


def test_workflow_has_no_stale_itest_skip_claim() -> None:
    """e2e 已 hermetic：工作流不得再声称集成层「需 bun + weights fixture，CI 里跳过」。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "require bun + weights fixture" not in text


def test_mypy_step_is_a_hard_gate() -> None:
    """mypy 必须是**零容忍硬门禁**（2026-09-15 清零后才成立，由本断言钉住）。

    历史形态：`continue-on-error: true` + 注释里钉了一个错误数快照（733b66f
    首次加 CI 时写的）。那个数字一路腐烂到与事实无关 —— 而**没有任何东西在检查
    它**，涨到 500 也照样绿。现在树是 0 错误（`Success: no issues found in 196
    source files`），零容忍比「计数基线」更强（换一个错误也算红），也省掉一份会
    腐烂的数字。故：既不许 continue-on-error，也必须有真跑 mypy 的那行。
    """
    code = _code_lines(WORKFLOW.read_text(encoding="utf-8"))
    assert "uv run mypy ." in code, "CI 必须真跑 mypy（硬门禁）"
    assert "continue-on-error" not in code, (
        "CI 不得用 continue-on-error 软化门禁 —— 那等于把会腐烂的基线数字请回来"
    )
