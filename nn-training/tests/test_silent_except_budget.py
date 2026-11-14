"""G9：生产包不得新增「宽泛捕获 + 什么也不做」（`except …: pass`）
（`python.refactor.md` §P0-7、§8）。

存量按**文件计数**冻结在 `tests/golden/silent_except_baseline.json`：只拦增长，不要求一次清零。
口径 = AST 里 `ExceptHandler` 同时满足两件事：① 捕获面是宽泛的（`Exception` /
`BaseException` / 裸 `except:`，与 G4 同一份定义）；② 函数体**只有一个 `pass`**（连一行留痕都
没有）。捕获面具体（`except (OSError, ValueError)` / `except FileNotFoundError`）的不计 ——
那些类型本身就是文档；体内除 `pass` 还有别的语句的也不计 —— 那已经不是「静默」。

为什么要单独一条（G4 已经管住宽泛捕获）：**「捕了并处理」和「捕了假装没发生」是两种事故**。
2026-11-14 的实例就在日志层自己身上：`LogRouter` 镜像写失败与句柄关闭失败都是裸 `pass`，
于是「这门课的日志文件正在丢行」与「这门课本来就没开日志」在控制台上长得一模一样，只有事后
比对课日志才发现少了半天（见 `DECISIONS.md` §2026-11-14-…-log-router-visibility）。G4 拦得住
「新增 `except Exception`」，却拦不住把新分支写成 `pass` —— 这条补的就是后半句。

处置口径（不是一律喊 WARN）：
  * **该有替代路径的**（降级测量、可选依赖、陈旧文件清理）→ 保留 `pass` 但写清为什么静默，
    并让它留在本基线里；
  * **失败即信息丢失的**（日志落盘、账本写回、重试判定）→ 换成可见出口（`log_line` /
    `warn_once` / 计数器），顺手把基线调小；
  * 热点路径（每 tick / 每行日志）去重要按 key 去重，别把控制台刷成噪音。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "silent_except_baseline.json"

#: 生产包；与 G3 / G4 同一扫描面。`tools/` 不在内，`worker/scripts/` 在下方按前缀豁免。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)

#: 与 G4 同一份宽泛定义（`tests/test_broad_except_budget.py::BROAD_NAMES`）。
BROAD_NAMES = frozenset({"Exception", "BaseException"})

#: 自检锚点：扫描必须命中的两个已知文件（`common/log.py` 的镜像/关闭路径与
#: `worker/forensics.py` 的降级测量都还在基线里 —— 前者是「该有替代路径才静默」的典型，
#: 后者是「静默即降级」的合法样板；`remote/job_lifecycle.py` 那 6 处控制面 POST 已于
#: 2026-11-14 改成留痕，不再适合当锚点）。
_ANCHORS = ("common/log.py", "worker/forensics.py")


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id in BROAD_NAMES


def _is_silent(handler: ast.ExceptHandler) -> bool:
    """体内只有一个 `pass`（注释不算节点，所以「只有注释」也算静默）。"""
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


@cache
def _scan() -> dict[str, int]:
    out: dict[str, int] = {}
    for d in PROD_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            n = sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _is_silent(node)
            )
            if n:
                out[rel] = n
    return out


@cache
def _baseline() -> dict[str, int]:
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def test_no_new_silent_broad_except() -> None:
    actual = _scan()
    baseline = _baseline()
    growth = {
        rel: (n, baseline.get(rel, 0))
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    }
    detail = "\n".join(f"  {rel}: {n} 处（基线 {base}）" for rel, (n, base) in growth.items())
    assert not growth, (
        "生产包新增了「宽泛捕获 + 什么也不做」（`except Exception: pass`）：\n"
        f"{detail}\n"
        "  失败即信息丢失的路径要给可见出口（`common.logutil.log_line` / `LogRouter.warn_once`，"
        "热点按 key 去重）；确实该降级的，写清理由并把这条留在 `tests/golden/silent_except_baseline.json`。"
    )


def test_baseline_only_shrinks() -> None:
    """基线里的文件计数只许持平或下降；清理完随手把基线数改小（否则额度会一直放行旧写法）。"""
    actual = _scan()
    baseline = _baseline()
    stale = {
        rel: (base, actual.get(rel, 0))
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    }
    detail = "\n".join(f"  {rel}: 基线 {base}，实际 {n}" for rel, (base, n) in stale.items())
    assert not stale, (
        "以下文件的静默宽捕获已经减少，但 `tests/golden/silent_except_baseline.json` 还是旧数字：\n"
        f"{detail}\n"
        "  把基线数改成实际值（或删掉归零的条目），让额度不再回流。"
    )


def test_scan_sees_the_known_production_surface() -> None:
    """扫描入口自检：基线非空、实际扫描确实命中已知文件、豁免面没有混进来。"""
    baseline = _baseline()
    actual = _scan()
    for rel in _ANCHORS:
        assert baseline.get(rel, 0) > 0, f"基线丢了已知文件 {rel}"
        assert actual.get(rel, 0) > 0, "扫描入口坏了（AST 没数到已知 `except …: pass`）"
    assert sum(actual.values()) >= 50, "生产面计数异常偏低，扫描可能漏了包"
    assert len(baseline) >= 30
    assert not any(rel.startswith(EXEMPT_PREFIXES) for rel in baseline)
    assert not any(rel.startswith("tools/") for rel in baseline)


def test_specific_handlers_and_handled_cases_are_not_counted() -> None:
    """口径自检：具体异常、体内还有别的语句、`except*` 都不算。"""
    src = """
try:
    pass
except (OSError, ValueError):
    pass
except FileNotFoundError:
    pass
except Exception:
    log_line("x", "y")
"""
    tree = ast.parse(src)
    silent = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _is_silent(node)
    ]
    assert silent == []

    bare = ast.parse("try:\n    pass\nexcept:\n    pass\n")
    assert (
        sum(
            1
            for node in ast.walk(bare)
            if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _is_silent(node)
        )
        == 1
    )
