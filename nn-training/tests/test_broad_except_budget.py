"""G4：生产包不得新增 `except Exception` / `except BaseException` / 裸 `except:`
（`python.refactor.md` §8、§P0-7）。

存量按**文件计数**冻结在 `tests/golden/except_baseline.json`：只拦增长，不要求一次清零
（仓内门禁文化：冻结存量 + 禁止新增）。口径是 AST 里的 `ExceptHandler`，只看处理器类型
是否 `Exception` / `BaseException` / 省略；`except (OSError, ValueError)` 这类具体异常不计数，
注释与 docstring 里提到也不算。`tools/` 与 `worker/scripts/` 是一次性脚本，按 G3 同一先例豁免；
`tests/` 不在扫描面内。

为什么需要这条：P0-7 的错误分类（`ProtocolError` / `RetryableError` / `OSError`）只在少数关键
路径上生效，其余地方一律 `except Exception` 兜底，把「确定性失败」和「瞬时失败」抹成同一分支，
顺带吞掉 `MemoryError` 与真正的编码 bug。没有门禁时，新代码会继续用最省事的写法，清理永远
追不上新增。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "except_baseline.json"

#: 生产包；与 G3 同一扫描面。`tools/` 不在内，`worker/scripts/` 在下方按前缀豁免。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)

#: 计数的处理器类型名。裸 `except:`（`node.type is None`）单独计，语义上等价于
#: `except BaseException`，是最宽的一种。
BROAD_NAMES = frozenset({"Exception", "BaseException"})


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id in BROAD_NAMES


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
                if isinstance(node, ast.ExceptHandler) and _is_broad(node)
            )
            if n:
                out[rel] = n
    return out


@cache
def _baseline() -> dict[str, int]:
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def test_no_new_broad_except_handlers() -> None:
    actual = _scan()
    baseline = _baseline()
    growth = {
        rel: (n, baseline.get(rel, 0))
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    }
    detail = "\n".join(f"  {rel}: {n} 处（基线 {base}）" for rel, (n, base) in growth.items())
    assert not growth, (
        "生产包新增了宽泛的异常处理器（`except Exception` / `except BaseException` / 裸 `except:`）：\n"
        f"{detail}\n"
        "  内部路径只捕具体异常（`ProtocolError` / `RetryableError` / `OSError` / `ValueError`）；"
        "确实位于进程边界时，也要 `logger.exception` 留痕并返回明确失败码。"
    )


def test_baseline_only_shrinks() -> None:
    """基线里的文件计数只许持平或下降；修完后应顺手把基线数改小（否则门禁会一直放行旧额度）。"""
    actual = _scan()
    baseline = _baseline()
    stale = {
        rel: (base, actual.get(rel, 0))
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    }
    detail = "\n".join(f"  {rel}: 基线 {base}，实际 {n}" for rel, (base, n) in stale.items())
    assert not stale, (
        "以下文件的宽泛异常处理器已经减少，但 `tests/golden/except_baseline.json` 还是旧数字：\n"
        f"{detail}\n"
        "  把基线数改成实际值（或删掉归零的条目），让额度不再回流。"
    )


def test_scan_sees_the_known_production_surface() -> None:
    """扫描入口自检：基线非空、实际扫描确实命中已知文件、豁免面没有混进来。"""
    baseline = _baseline()
    actual = _scan()
    assert baseline.get("remote/train_core.py", 0) > 0
    assert baseline.get("common/log.py", 0) > 0
    assert actual.get("remote/train_core.py", 0) > 0, (
        "扫描入口坏了（AST 没数到已知 except Exception）"
    )
    assert sum(actual.values()) >= 400, "生产面计数异常偏低，扫描可能漏了包"
    assert len(baseline) >= 100
    assert not any(rel.startswith(EXEMPT_PREFIXES) for rel in baseline)
    assert not any(rel.startswith("tools/") for rel in baseline)


def test_specific_handlers_and_regex_are_not_counted() -> None:
    """口径自检：具体异常、`except*`、注释里的字样都不算。"""
    src = """
try:
    pass
except (OSError, ValueError):
    pass
except ProtocolError:
    pass
# except Exception: 只是注释
"""
    tree = ast.parse(src)
    broad = [
        node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and _is_broad(node)
    ]
    assert broad == []

    bare = ast.parse("try:\n    pass\nexcept:\n    pass\n")
    assert (
        sum(1 for node in ast.walk(bare) if isinstance(node, ast.ExceptHandler) and _is_broad(node))
        == 1
    )
