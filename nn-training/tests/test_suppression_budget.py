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

from tests.helpers.budget import sanity_floor_of

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
        # 2026-11-20：`type:` 太宽（几乎每个类型注释都带它）⇒ 换成与 `_count_src` 同形的
        # `_TYPE_IGNORE` 正则；全仓 1029 个 `.py` 里只剩 91 个需要 tokenize（7.0s → 0.9s）。
        if "noqa" not in src and not _TYPE_IGNORE.search(src):
            continue
        n_noqa, n_ignore = _count_src(src)
        if n_noqa:
            counts[NOQA_F401][rel] = n_noqa
        if n_ignore:
            counts[TYPE_IGNORE][rel] = n_ignore
    return counts


def _count_src(src: str) -> tuple[int, int]:
    """一段源码里的（`noqa: F401` 条数, `type: ignore` 条数）—— 全仓扫描与自检共用这一份。"""
    n_noqa = n_ignore = 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        m = _NOQA.match(tok.string)
        if m and "F401" in [c.strip() for c in (m.group(1) or "").split(",")]:
            n_noqa += 1
        if _TYPE_IGNORE.match(tok.string):
            n_ignore += 1
    return n_noqa, n_ignore


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
    assert _count_src(src) == (1, 1)


def test_scan_sees_the_known_surface() -> None:
    """扫描入口自检：两类都有存量、已知文件在册、排除目录没混进来。"""
    baseline = _baseline()
    # `noqa: F401` 面 2026-11-19 **归零**（最后三处：`hub/smoke_loopback.py` 的死 import 直接删、
    # `remote/offline_boot.py` 的探测改 `importlib.import_module`、`worker/ppo/engine.py` 的
    # re-export 改 `x as x`）⇒ 不再有「活文件」当锚点。旧锚（`remote/offline_boot.py`）正是被
    # 治好的那个文件 —— 锚点不能选刚被修好的，否则门禁一被治好就自证失效（2026-11-14 已踩过一次）。
    # 现在锚的是**扫描器本身**：同一段合成源码必须被数出 1 条，入口坏了（正则 / 过滤 / tokenize）
    # 这条当场红；真实存量仍由上面两条「只许降」的用例守着。
    assert not baseline[NOQA_F401], (
        "noqa: F401 已全仓归零，基线里不该再留条目（留了就是额度回流）"
    )
    assert _count_src("import os  # noqa: F401\n")[0] == 1, "扫描入口坏了（合成样本没数到）"
    # 下面是**扫描健康度地板**（不是额度）：`type: ignore` 真被清到接近零之前，几十个文件里
    # 仍有存量 ⇒ 数出来太小就说明扫描入口坏了。条数地板 = 基线九成（派生，见
    # `tests/helpers/budget.py`）⇒ 清理基线时不必回填数字；文件数地板仍是绝对值
    # （「十成里的几成文件」是自指，没有意义），只在 re-export 壳成批退役时需要看一眼。
    # 比的是**实际扫描**与基线的九成（拿基线比自己比是恒等式，等于没写这条）。
    actual = _scan()
    assert sum(actual[TYPE_IGNORE].values()) >= sanity_floor_of(
        sum(baseline[TYPE_IGNORE].values())
    ), (
        f"type: ignore 计数 {sum(actual[TYPE_IGNORE].values())} 低于基线九成"
        " —— 扫描面可能坏了"
    )
    assert len(baseline[TYPE_IGNORE]) >= 60
    assert not any(
        part in SKIP_DIRS
        for rel in (*baseline[NOQA_F401], *baseline[TYPE_IGNORE])
        for part in rel.split("/")
    )
