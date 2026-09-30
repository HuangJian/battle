"""python 源文件的 **LOC 预算**护栏（用户口径 2026-09-28）：单文件 < 1000 行。

**为什么要有它**：S5 那一轮（`plan/nn-training-refactor.md` §5.7）把九个 1000+ 行的「神模块」
拆成 19 个可独立依赖的模块 —— 但那是一次性的人力侦察，没有任何东西拦着下一个模块长到
1400 行（`biz/gate_check.py` 从 1412 拆到 622 之前就一直是那个形状）。本测试把
「>1000 行 = 设计问题，不是笔误」变成门禁：**每次门禁都在问同一个问题**。

**口径**（用户钦定，与 `tmp/measure_loc_budget.py` 一致）：

    LOC = 物理行 − 空行 − 纯注释行 − docstring 行

  · docstring = 模块 / 类 / 函数**首语句**的字符串（AST 判定，多行算整段）；
  · 语句级多行字符串（长 `log()` / `raise` 文案）**算代码** —— 它们是行数，不是注解；
  · `>= 1000` 即红（`< 1000` 合规）。

**豁免**（用户 2026-09-28 裁定）：

  · `tests/` 与 `e2e/` 两层**整体**不设限 —— 测试的体量是「覆盖了多少场景」的函数，
    压它等于少测（同日的实测：`tests/test_remote_iter.py` 1309、`tests/test_remote_ppo.py`
    1258 在这一口径下超限，但它们的行数来自场景枚举，不是结构冗余）；
  · `remote/offline_boot.py` —— standalone 运输单元（notebook 从 GitHub raw 按名单拉取，
    顶层不得 `import remote.*`），§5.7.3 实测「取包面闭包 47/86 节点、心跳簇 29 节点」
    ⇒ 拆不开，只能留档豁免。

**防漂移**（三条，与 `test_githook_scripts.py` 同风格：把「静默失效」变成红）：

  1. 扫描必须真扫到货（`git` 调用失败 / cwd 不对 / 豁免面写宽 ⇒ 空跑也算绿）；
  2. 豁免目录名只许命中测试层（`nn-training/tests/`、`nn-training/e2e/`）——
     防止哪天有人把 `tests` 当通配符用，顺手豁免掉生产目录；
  3. 豁免**不是永久的**：`offline_boot.py` 降到阈值下就该把豁免收回（否则它会作为
     「合法的历史遗留」长存，正是 §5.7.3 里反复点名的那种东西）。
"""

from __future__ import annotations

import ast
import subprocess
from functools import lru_cache
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 阈值（用户口径，2026-09-28）。**只许收紧，改动必须记 DECISIONS。**
LOC_LIMIT = 1000
#: 路径里出现这些片段 ⇒ 整体豁免（测试层的体量用「覆盖场景数」衡量，不用行数）。
EXEMPT_DIR_PARTS = frozenset({"tests", "e2e"})
#: 单文件豁免（登记即承诺：`test_exempt_file_is_still_worth_exempting` 会盯着它仍有理由）。
EXEMPT_FILES = frozenset({"nn-training/remote/offline_boot.py"})
#: 豁免目录名只许命中这两层（防把 `tests` 当通配符用）。
EXEMPT_DIR_ROOTS = ("nn-training/tests/", "nn-training/e2e/")
#: 扫描锚：这些**非豁免**文件必须在扫描结果里（防「扫了个寂寞」也算绿）。
SCAN_ANCHORS = (
    "nn-training/remote/worker.py",
    "nn-training/trainer/dispatch.py",
    "nn-training/common/protocol.py",
)


def _python_files() -> tuple[str, ...]:
    """全仓 `.py` 相对路径（**跟踪的 + 未跟踪未被忽略的**，POSIX 分隔符）。

    用 git 而不是 `os.walk`：`nn-training/tmp/` 那类被 ignore 的临时/备份副本（实测 600+ 份）
    不该进预算；而未跟踪的新源码（提交前）必须进 —— 否则新写的巨文件要等下一个人肉发现。
    """
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return tuple(sorted(p for p in proc.stdout.split("\0") if p))


def _is_exempt(rel: str) -> bool:
    return rel in EXEMPT_FILES or bool(EXEMPT_DIR_PARTS & set(PurePosixPath(rel).parts))


def _doc_rows(tree: ast.AST) -> set[int]:
    """模块 / 类 / 函数首语句若是字符串，其**整段**行号（多行 docstring 全在内）。"""
    rows: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                first = body[0]
                lo, hi = first.lineno, first.end_lineno  # typeshed 里是 Optional
                if lo is not None and hi is not None:
                    rows.update(range(lo, hi + 1))
    return rows


def loc_of(text: str) -> int:
    """源码文本 → LOC（口径见模块 docstring）。语法坏的文件按「非空非注释」计
    —— 语法由 ruff / mypy 报，这里不重复报。"""
    lines = text.splitlines()
    try:
        docs = _doc_rows(ast.parse(text))
    except SyntaxError:
        docs = set()
    return sum(
        1
        for i, line in enumerate(lines, 1)
        if i not in docs and line.strip() and not line.lstrip().startswith("#")
    )


@lru_cache(maxsize=1)
def _examined() -> tuple[tuple[str, str], ...]:
    """`(相对路径, 源码)` —— 非豁免且存在的文件，全进程只读一次。"""
    out: list[tuple[str, str]] = []
    for rel in _python_files():
        if _is_exempt(rel):
            continue
        path = REPO_ROOT / rel
        if path.is_file():
            out.append((rel, path.read_text(encoding="utf-8", errors="replace")))
    return tuple(out)


def oversize_files() -> list[tuple[str, int]]:
    """超预算的源文件 `[(相对路径, LOC)]`（降序）。

    先按物理行数粗筛（`LOC ≤ 物理行` 恒成立）⇒ 全仓只有 ~40 个文件需要 AST 解析，
    整个用例 <1s（门禁的 per-test 预算是 5s 警告 / 30s 失败）。
    """
    out: list[tuple[str, int]] = []
    for rel, text in _examined():
        if len(text.splitlines()) < LOC_LIMIT:
            continue
        n = loc_of(text)
        if n >= LOC_LIMIT:
            out.append((rel, n))
    out.sort(key=lambda row: -row[1])
    return out


# ── 护栏本体 ───────────────────────────────────────────────────────────────


def test_no_source_file_exceeds_loc_budget() -> None:
    """单文件 LOC < 1000（口径：非空 + 非纯注释 + 非 docstring）。"""
    over = oversize_files()
    detail = "\n".join(f"  {rel}: {n} 行（超 {n - LOC_LIMIT}）" for rel, n in over)
    assert not over, (
        f"以下 python 源文件 LOC ≥ {LOC_LIMIT}（空行/纯注释/docstring 已排除）：\n{detail}\n"
        "  拆法：按「独立所有者 + 独立触发条件」找模块级连通分量"
        "（手法与验收标准见 plan/nn-training-refactor.md §5.7）。\n"
        f"  测试层（{EXEMPT_DIR_ROOTS[0]} · {EXEMPT_DIR_ROOTS[1]}）与 standalone 运输单元"
        f" {'、'.join(sorted(EXEMPT_FILES))} 已豁免。"
    )


def test_metric_semantics() -> None:
    """口径逐条钉死 —— 改了 `loc_of` 就必须在这里改期望值（免得预算被悄悄调宽）。"""
    assert loc_of('"""模块 docstring\n第二行\n"""\n') == 0  # 模块 docstring 不算
    assert loc_of("import os\n# 注释行\n\n\n") == 1  # 空行/纯注释不算，import 算
    assert loc_of('def f() -> None:\n    """doc\n    续行\n    """\n    pass\n') == 2  # def + pass
    assert loc_of('log(\n    "多行"\n    "文案"\n)\n') == 4  # 多行字符串算代码（是行数，不是注解）
    assert loc_of("x = 1  # 行内注释\n") == 1  # 行内注释不整行抵掉


def test_scan_finds_the_repository() -> None:
    """防「扫了个寂寞」：git 调用 / cwd / 豁免面写宽导致的空跑必须是红。"""
    rels = {rel for rel, _ in _examined()}
    assert len(rels) >= 200, (
        f"只扫到 {len(rels)} 个非豁免 .py（REPO_ROOT={REPO_ROOT}）——扫描入口坏了"
    )
    assert not [a for a in SCAN_ANCHORS if _is_exempt(a)], "锚必须是非豁免文件，否则它永远扫不到"
    missing = [a for a in SCAN_ANCHORS if a not in rels]
    assert not missing, f"锚文件没被扫到：{missing}（豁免面漂了？）"


def test_exempt_dirs_are_test_layers_only() -> None:
    """`tests` / `e2e` 这两个豁免片段只许命中测试层 —— 不许当通配符豁免生产目录。"""
    hits = [rel for rel in _python_files() if EXEMPT_DIR_PARTS & set(PurePosixPath(rel).parts)]
    leaked = sorted(r for r in hits if not r.startswith(EXEMPT_DIR_ROOTS))
    assert not leaked, (
        f"以下路径被目录豁免片段误伤（只允许 {EXEMPT_DIR_ROOTS}）：\n  " + "\n  ".join(leaked)
    )


def test_exempt_file_is_still_worth_exempting() -> None:
    """豁免会过期：`offline_boot.py` 一旦降到阈值下，就该把 `EXEMPT_FILES` 清空。"""
    for rel in sorted(EXEMPT_FILES):
        path = REPO_ROOT / rel
        assert path.is_file(), f"豁免的文件不存在了：{rel}（改名/搬走 ⇒ 同步 EXEMPT_FILES）"
        n = loc_of(path.read_text(encoding="utf-8", errors="replace"))
        assert n >= LOC_LIMIT, (
            f"{rel} 现在 LOC={n} < {LOC_LIMIT} ⇒ 豁免已无理由，把它从 EXEMPT_FILES 删掉"
            "（豁免只留给「拆不开且已超限」的文件）"
        )
