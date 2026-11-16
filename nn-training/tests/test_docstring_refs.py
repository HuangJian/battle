"""注释 / docstring / 字符串里的 `path/to/file.py::name` 指针必须指向**存在**的文件。

为什么单独一条（`python.refactor.md` §9「注释腐烂且无人检查」）：`common/logutil.py` 曾经
大声引用一个不存在的 `biz/log.py`，还据此做出可证伪的**错误断言**（§P0-6）——那条注释没有
帮到读者，而是把人往错的方向指，直到有人逐行读才被识破。这类漂移不该靠人读：路径指针是
**可判定**的，判定它只要一次 `Path.is_file()`。

做这条门禁时（2026-11-16）同一个模式在 `biz/` → `worker/` 搬家之后还剩十几处，全部指向已经
不存在的文件（`biz/loop_guards_trip.py::TrainingGuardsTrip`、`biz/events.py::write_iteration`、
`biz/archive.py::WEIGHTS_BACKUP_DIR` …）。它们不是历史叙事，是**「去那个文件看契约」的指针**：
点进去只会得到 404，然后读者照错的模块去找，找到的第二份实现早已不是契约。

口径（为什么这么窄）：
  * **只查带目录的指针**（`worker/events.py::write_iteration`）。裸文件名（散文里的 `queue.py`）
    不算指针 —— 同名模块在仓里往往有多份，写信人只是提一句模块名。
  * **只查 `::` 形态**，不查 `path.py:123` 行号：行号天然随代码漂移（还出现在被引用的事故
    traceback 里，那是**历史证据**，改写它等于伪造现场），要求维护行号只会逼人删掉注释。
  * 解析只读源码文本（`tokenize` 的 COMMENT + STRING token），**不建 AST** —— 同样的判定，
    成本只有 1/4；文本里连一个指针形状都没有的文件直接跳过。
  * 只查生产包（与 G3/G4/G9 同一扫描面；`worker/scripts/` 与它们同款豁免）。

解析顺序（写信人的四种真实意图）：仓库根 → `nn-training/` 根 → **引用者所在目录** →
全仓后缀匹配（`train/x.py` 解析到 `worker/train/x.py`，这正是搬家后注释的本意）。
"""

from __future__ import annotations

import io
import os
import re
import tokenize
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent

#: 生产包；与 G3 / G4 / G9 同一扫描面。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)

#: `path/to/file.py::name`（**至少一层目录**；名字允许点号 ⇒ 也认 `Class.method`）。
POINTER = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.py)::([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")

#: 后缀索引不进的目录（`rglob` 会真的走进去 —— `.venv` / `node_modules` 比源码大几百倍）。
_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", "dist", "build"})


@cache
def _py_index() -> tuple[str, ...]:
    """`nn-training/` 下 `.py` 的 posix 路径（后缀匹配用；剪掉虚拟环境 / 依赖 / 字节码目录）。

    只扫 `nn-training/`：需要后缀匹配的都是包内相对写法（`train/x.py` ⇒ `worker/train/x.py`），
    仓库根 / `tools/` 的写法前三种解析已经命中。全仓扫一遍 0.65s，这里 2ms。
    """
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        out.extend((Path(dirpath) / f).as_posix() for f in filenames if f.endswith(".py"))
    return tuple(out)


def _resolve(ref: str, here: Path) -> Path | None:
    """按「仓库根 → `nn-training/` → 引用者目录 → 全仓后缀」解析，全不中 ⇒ `None`。"""
    for cand in (REPO / ref, ROOT / ref, here / ref):
        if cand.is_file():
            return cand
    tail = "/" + ref
    for s in _py_index():
        if s.endswith(tail):
            return Path(s)
    return None


def _pointers_of(src: str) -> set[tuple[str, str]]:
    """源码注释与字符串里提到的 `(路径, 名字)` 集合（`tokenize` 一把过，不建 AST）。"""
    refs: set[tuple[str, str]] = set()
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                refs |= set(POINTER.findall(tok.string))
    except (tokenize.TokenError, IndentationError):  # pragma: no cover - 语法错由别的门禁报
        pass
    return refs


@cache
def _dangling_pointers() -> dict[str, list[str]]:
    """`{引用者: ["path.py::name", …]}` —— 指针目标不存在的那种（空 ⇒ 注释与代码一致）。"""
    out: dict[str, list[str]] = {}
    for d in PROD_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            src = p.read_text(encoding="utf-8", errors="replace")
            # 文本初筛：连一个指针形状都没有就不用分词（330 个生产文件里只有 ~90 个要分词）。
            if POINTER.search(src) is None:
                continue
            for ref, name in sorted(_pointers_of(src)):
                if _resolve(ref, p.parent) is None:
                    out.setdefault(rel, []).append(f"{ref}::{name}")
    return out


def test_docstring_pointers_resolve() -> None:
    """每个 `file.py::name` 指针都要落在一个真实文件上（否则它是 404 + 误导）。"""
    dangling = _dangling_pointers()
    detail = "\n".join(
        f"  {rel}: " + "、".join(f"`{r}`" for r in refs) for rel, refs in sorted(dangling.items())
    )
    assert not _dangling_pointers(), (
        "这些注释 / 字符串的指针指向不存在的文件（`path/to/file.py::name`）：\n"
        f"{detail}\n"
        "  搬家 / 改名之后注释要跟着走：把路径改成现在的家（解析顺序：仓库根 → `nn-training/` → "
        "引用者目录 → 全仓后缀），或者把这句话改成不依赖路径的描述。"
    )
