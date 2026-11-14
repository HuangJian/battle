"""G6：`# noqa: F401` / `# type: ignore` 的存量只减不增（`python.refactor.md` §8）。

两类抑制符都是**关掉编译器/检查器**的语法，数量只会随「先写快一点」增长。`# type: ignore`
的「有没有用」现在由 mypy 自己判（`warn_unused_ignores=true`，见 `DECISIONS.md`
§2026-11-14-warn-unused-ignores-and-pinned-platform：打开当天清掉 111 条无用 ignore，
存量 271/98 → 163/68）；`# noqa: F401` 则被拿来给 re-export 壳模块续命。
没有门禁时，两种抑制都会继续堆积。这里按**文件计数**冻结在
`tests/golden/suppression_baseline.json`：只拦增长，另加一条「基线只许调小」防止额度回流。

口径：用 `tokenize` 只读真正的 `COMMENT` token，所以字符串/文档里引用的 `# noqa` 字样不算
（`tests/` 里有若干门禁用例在字符串里写这些标记）。`*.py` 全仓扫描，跳过虚拟环境 / `tmp/` /
`weights/` / `ipynb/`。两类分开统计、分开冻结。
"""

from __future__ import annotations

import io
import json
import re
import tokenize
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "suppression_baseline.json"

#: 扫描面排除目录（相对 `nn-training/` 的任一路径段命中即跳过）。
SKIP_DIRS = frozenset({".venv", "tmp", "weights", "ipynb", "__pycache__", "node_modules"})

#: `noqa` 指令：裸 `noqa` / `noqa: F401` / `noqa: E402,F401`（ruff 的代码列表）。
_NOQA = re.compile(r"#\s*noqa(?::\s*([A-Z0-9, ]+))?")
#: `type: ignore` 指令，含带错误码的 `type: ignore[attr-defined]` 形态。
_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore(\[[^\]]*\])?")

#: 两类抑制符的键名（也是基线 JSON 的两个顶层键）。
NOQA_F401 = "noqa_f401"
TYPE_IGNORE = "type_ignore"


def _iter_py() -> list[Path]:
    out = []
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        out.append(p)
    return sorted(out)


@cache
def _scan() -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {NOQA_F401: {}, TYPE_IGNORE: {}}
    for p in _iter_py():
        rel = p.relative_to(ROOT).as_posix()
        src = p.read_text(encoding="utf-8", errors="replace")
        # 快速前置过滤：没有这两个子串的文件不可能含目标注释（全仓 tokenize 一遍 ~5s，
        # 超过 conftest 的单用例 5s 警告阈值）。过滤只跳过“必然为 0”的文件，不改变计数。
        if "noqa" not in src and "type:" not in src:
            continue
        n_noqa = n_ignore = 0
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            m = _NOQA.match(tok.string)
            if m and "F401" in [c.strip() for c in (m.group(1) or "").split(",")]:
                n_noqa += 1
            if _TYPE_IGNORE.match(tok.string):
                n_ignore += 1
        if n_noqa:
            counts[NOQA_F401][rel] = n_noqa
        if n_ignore:
            counts[TYPE_IGNORE][rel] = n_ignore
    return counts


@cache
def _baseline() -> dict[str, dict[str, int]]:
    data: dict[str, dict[str, int]] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def test_no_new_suppressions() -> None:
    actual = _scan()
    baseline = _baseline()
    lines: list[str] = []
    for kind in (NOQA_F401, TYPE_IGNORE):
        had = baseline.get(kind, {})
        now = actual.get(kind, {})
        for rel, n in sorted(now.items()):
            if n > had.get(rel, 0):
                lines.append(f"  {kind} {rel}: {n} 处（基线 {had.get(rel, 0)}）")
    assert not lines, (
        "新增了抑制符（`# noqa: F401` / `# type: ignore`）：\n"
        + "\n".join(lines)
        + "\n  能修就修：未被使用的 import 直接删（re-export 用 `__all__` 表达意图）；"
        "`type: ignore` 换成具体错误码或补类型。确属第三方边界的，才在"
        " `tests/golden/suppression_baseline.json` 对应文件上 +1，并在提交信息里说明。"
    )


def test_baselines_only_shrink() -> None:
    """减少后必须同步调小基线，否则旧额度会一直被放行。"""
    actual = _scan()
    baseline = _baseline()
    lines: list[str] = []
    for kind in (NOQA_F401, TYPE_IGNORE):
        for rel, base in sorted(baseline.get(kind, {}).items()):
            n = actual.get(kind, {}).get(rel, 0)
            if n < base:
                lines.append(f"  {kind} {rel}: 基线 {base}，实际 {n}")
    assert not lines, (
        "以下文件的抑制符已经减少，但 `tests/golden/suppression_baseline.json` 还是旧数字：\n"
        + "\n".join(lines)
        + "\n  把基线数改成实际值（归零的条目直接删掉），让额度不再回流。"
    )


def test_scan_ignores_strings_and_other_lint_codes() -> None:
    """口径自检：字符串里的标记、其他 noqa 代码、其他 type 注释都不计数。"""
    src = (
        'X = "# noqa: F401"\n'
        "import os  # noqa: E402\n"
        "y = 1  # type: check\n"
        "z = 1  # type: ignore[attr-defined]\n"
        "w = 1  # noqa: E402,F401\n"
    )
    n_noqa = n_ignore = 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        m = _NOQA.match(tok.string)
        if m and "F401" in [c.strip() for c in (m.group(1) or "").split(",")]:
            n_noqa += 1
        if _TYPE_IGNORE.match(tok.string):
            n_ignore += 1
    assert n_noqa == 1
    assert n_ignore == 1


def test_scan_sees_the_known_surface() -> None:
    """扫描入口自检：两类都有存量、已知文件在册、排除目录没混进来。"""
    baseline = _baseline()
    # 锚点选**还没退役**的转发壳：`remote/offline_boot.py` 的模块级 `__getattr__`（P1-11
    # 清单里仍在）。此前锚 `trainer/dispatch_queue.py`，它的 re-export 面 2026-11-14 归零后
    # 这条自检就红了 —— 锚点不能选「刚被修好的文件」，否则门禁一被治好就自证失效。
    assert baseline[NOQA_F401].get("remote/offline_boot.py", 0) > 0, (
        "扫描入口坏了（re-export 壳没数到）"
    )
    # 下面两条是**扫描健康度地板**（不是额度）：`type: ignore` 真被清到接近零之前，几十个文件里
    # 仍有存量 ⇒ 数出来太小就说明扫描入口坏了。2026-11-14 开 `warn_unused_ignores` 清掉 111 条
    # 无用 ignore（271 → 163，98 → 68 文件）后，地板跟着从 200/80 降到 150/60。
    assert sum(baseline[TYPE_IGNORE].values()) >= 150
    assert len(baseline[TYPE_IGNORE]) >= 60
    assert not any(
        part in SKIP_DIRS
        for rel in (*baseline[NOQA_F401], *baseline[TYPE_IGNORE])
        for part in rel.split("/")
    )
