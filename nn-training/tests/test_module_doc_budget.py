"""G11：模块 docstring 上限 **50 行**，超出部分按文件冻结存量（只许调小）。

出处：`python.refactor.md` §9「注释腐烂且无人检查」的第 3 条建议 ——
「模块 docstring 上限 ~50 行，超出的设计说明移到 `docs/nn/*.md` 并在 docstring 留链接」。

为什么不直接一次清零：这条规矩的目标是**可读性**，不是行数本身。生产包里 13 个超限模块中有
三个是包的 `__init__.py`（`biz` 113 / `common` 99 / `trainer` 90）—— 那几段写的是**分层契约**
（谁能 import 谁、哪一层能改状态），它们最该被**在 import 的那一刻**读到；搬进 `docs/nn/`
等于把契约挪到读者不会去的地方，正是 §9 自己反对的「注释离代码太远」。所以这条门禁只拦
**增长**：新模块不许一出生就写 50 行以上，已超限的模块也不许更长；要减就顺手把基线调小。

与 G9 的「契约静默」标记同理：**基线不是豁免证，是债务表**。13 个条目每减一行都该同步改
`tests/golden/module_doc_baseline.json`（`test_module_doc_baseline_only_shrinks` 会盯着）。

扫描入口自检不另设一条：扫描坏掉（数不出 docstring）时那 13 个基线条目会全部变成「比基线短」
⇒ `test_module_doc_baseline_only_shrinks` 当场红。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "module_doc_baseline.json"

#: 生产包；与 G3 / G4 / G9 / §9 指针门禁同一扫描面。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)

#: 上限（行，含首尾引号行）。§9 的「~50」在这里落成一个确定的数。
CEILING = 50


@cache
def _doc_lines() -> dict[str, int]:
    """`{模块: 模块 docstring 行数}` —— 只收**超限**的那些（没有 docstring 的不在里面）。"""
    out: dict[str, int] = {}
    for d in PROD_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - 语法错由别的门禁报
                continue
            doc = ast.get_docstring(tree, clean=False)
            if doc is None:
                continue
            n = doc.count("\n") + 1
            if n > CEILING:
                out[rel] = n
    return out


@cache
def _baseline() -> dict[str, int]:
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


@cache
def _growth() -> dict[str, tuple[int, int]]:
    """`{模块: (现在, 基线)}` —— 超过基线的（新超限模块算「基线 0」）。"""
    base = _baseline()
    return {
        rel: (n, base.get(rel, 0))
        for rel, n in sorted(_doc_lines().items())
        if n > base.get(rel, 0)
    }


@cache
def _stale() -> dict[str, tuple[int, int]]:
    """`{模块: (基线, 现在)}` —— 已经比基线短了，但基线文件还是旧数（额度会回流）。"""
    now = _doc_lines()
    return {
        rel: (n, now.get(rel, 0)) for rel, n in sorted(_baseline().items()) if now.get(rel, 0) < n
    }


def test_no_module_docstring_grows_past_the_ceiling() -> None:
    """新模块不许一出生就写 > 50 行；已超限的模块不许更长（把设计说明写进 `docs/nn/*.md`）。"""
    grown = _growth()
    detail = "\n".join(f"  {rel}: {n} 行（基线 {base}）" for rel, (n, base) in grown.items())
    assert not _growth(), (
        f"模块 docstring 超过 {CEILING} 行（`python.refactor.md` §9：上限 ~50 行）：\n"
        f"{detail}\n"
        "  设计说明搬去 `docs/nn/*.md`，docstring 里留一句链接；留在 docstring 的应该是"
        "「这个模块为什么存在、谁不该 import 它」这类**读代码前就要知道**的话。"
    )


def test_module_doc_baseline_only_shrinks() -> None:
    """基线里的数字只许持平或下降；删短了就把基线一起改小（否则额度回流给下一个模块）。"""
    stale = _stale()
    detail = "\n".join(
        f"  {rel}: 基线 {base} 行，现在 {n if n else '没超限'}" for rel, (base, n) in stale.items()
    )
    assert not _stale(), (
        "以下模块的 docstring 已经比基线短，但 `tests/golden/module_doc_baseline.json` 还是旧数：\n"
        f"{detail}\n"
        "  把基线改成实际行数（或不超限了就删掉该条目），别把额度留给下一次加长。"
    )
