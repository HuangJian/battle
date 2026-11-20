"""版本矩阵门禁：`.python-version` / `requires-python` / mypy / ruff / CI 必须自洽。

**为什么要有它**（`python.refactor.md` §P1-2 的实测）：四者曾经各说各话 ——
`.python-version`=3.12、`requires-python`=>=3.10,<3.14、mypy=3.12、实际 venv=3.10.21，
而 CI 只在 3.12 上跑 ⇒「支持 3.10」从来没被验证过。`common/fs.extract_tar_bytes`
专门写了 `<3.12` 分支，但 CI 永远走不到那一支。

本用例钉死三件事：

1. 所有声明值都落在 `requires-python` 区间内（元数据不许自相矛盾）；
2. CI 矩阵**真的覆盖区间两端**（下界 + `.python-version`）；
3. CI 用矩阵值建环境（`uv sync --python …`），版本维度不是个装饰性标签。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str | Path) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _version(s: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d+)\.(\d+)", s)
    assert m, f"版本号解析失败：{s!r}"
    return int(m.group(1)), int(m.group(2))


def _requires_python() -> tuple[tuple[int, int], tuple[int, int]]:
    """`>=x.y,<z.w` → ((x, y), (z, w))；其他形态直接红（别让区间语义漂）。"""
    m = re.search(r'^requires-python\s*=\s*"([^"]+)"', _text("pyproject.toml"), re.MULTILINE)
    assert m, "pyproject.toml 缺少 requires-python"
    spec = m.group(1)
    lo = re.search(r">=\s*(\d+\.\d+)", spec)
    hi = re.search(r"<\s*(\d+\.\d+)", spec)
    assert lo and hi, f"requires-python 只支持 `>=x.y,<z.w` 形态：{spec}"
    return _version(lo.group(1)), _version(hi.group(1))


def _in_range(v: tuple[int, int], bounds: tuple[tuple[int, int], tuple[int, int]]) -> bool:
    lo, hi = bounds
    return lo <= v < hi


def _pinned() -> tuple[int, int]:
    return _version(_text(".python-version"))


def _mypy_target() -> tuple[int, int]:
    m = re.search(
        r'^\[tool\.mypy\][^[]*?python_version\s*=\s*"(\d+\.\d+)"',
        _text("pyproject.toml"),
        re.MULTILINE,
    )
    assert m, "pyproject.toml 的 [tool.mypy] 缺少 python_version"
    return _version(m.group(1))


def _ruff_target() -> tuple[int, int]:
    m = re.search(r'^target-version\s*=\s*"py(\d)(\d+)"', _text("pyproject.toml"), re.MULTILINE)
    assert m, "pyproject.toml 的 [tool.ruff] 缺少 target-version"
    return int(m.group(1)), int(m.group(2))


def _ci() -> str:
    return (ROOT.parent / ".github/workflows/nn-training.yml").read_text(encoding="utf-8")


def _ci_versions() -> list[str]:
    m = re.search(r"python:\s*\[([^\]]+)\]", _ci())
    assert m, "CI 的 matrix 里没有 python 版本维度（版本矩阵只跑一个版本）"
    return [v.strip().strip("'\"") for v in m.group(1).split(",") if v.strip()]


# ── 护栏本体 ───────────────────────────────────────────────────────────────


def test_pinned_version_is_supported() -> None:
    bounds = _requires_python()
    assert _in_range(_pinned(), bounds), (
        f".python-version={_pinned()} 不在 requires-python {bounds} 内 —— "
        "要么改钉住的版本，要么改 requires-python"
    )


def test_mypy_target_is_supported() -> None:
    bounds = _requires_python()
    assert _in_range(_mypy_target(), bounds), (
        f"mypy python_version={_mypy_target()} 不在 requires-python {bounds} 内"
    )


def test_ruff_target_matches_lower_bound() -> None:
    lo, _ = _requires_python()
    assert _ruff_target() == lo, (
        f"ruff target-version={_ruff_target()} != requires-python 下界 {lo} —— "
        "语法检查必须与「声明支持的最老版本」一致"
    )


def test_ci_covers_both_ends() -> None:
    versions = {_version(v) for v in _ci_versions()}
    lo, _ = _requires_python()
    assert lo in versions, f"CI 没跑下界 {lo}（现有：{sorted(versions)}）"
    assert _pinned() in versions, (
        f"CI 没跑 .python-version={_pinned()}（现有：{sorted(versions)}）"
    )


def test_ci_versions_are_supported() -> None:
    bounds = _requires_python()
    bad = [v for v in _ci_versions() if not _in_range(_version(v), bounds)]
    assert not bad, f"CI 在要求区间之外跑：{bad}（requires-python={bounds}）"


def test_ci_actually_uses_the_matrix_version() -> None:
    # 用 startswith/split 而非子串包含：后者会被「文本断言预算」计成一笔措辞债。
    sync_steps = [
        line.strip() for line in _ci().splitlines() if line.strip().startswith("run: uv sync")
    ]
    assert sync_steps, "CI 里找不到 `uv sync` 步骤"
    argv = sync_steps[0].split("--python", 1)
    assert len(argv) == 2, f"uv sync 没接 --python：{sync_steps[0]}"
    assert argv[1].strip().startswith('"${{ matrix.python }}"'), (
        "uv sync 的 --python 没接矩阵值 ⇒ 版本维度只是标签，不会真的换解释器"
    )
