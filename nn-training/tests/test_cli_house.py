"""`common/cli.py` 的约定守卫（plan/nn-training-reuse-and-deps.plan.md §S2 `common/cli` 全迁）。

**用户口径（2026-10-11）**：`--help` 文案与退出码**不是契约**，**只保 flags 语义**（参数名 / 含义 /
默认值逐字不变）⇒ 本文件钉三件事：

1. `run_main` 的三态行为（`None→0` · `int→该码` · `Ctrl-C→130`）—— 出口语义是新的契约；
2. 生产树里**没有裸 `argparse.ArgumentParser`**（构造点唯一化，否则「全迁」下次就退回原样）；
3. `if __name__ == "__main__":` 块里**不许**再出现旧出口（裸 `main()` / `sys.exit(main())`），
   且 house 构造点与出口的**采用数有下限**（只许涨：新入口必须走它）。

扫描逻辑住 `tests/helpers/cli_surface.py`（判据只读 AST，不读源码文本）。
"""

from __future__ import annotations

import pytest

from common.cli import house_parser, run_main
from tests.helpers import cli_surface

#: 采用数下限（2026-10-11 全迁当天实测：构造点 54 · 出口 62）——**只许涨**
MIN_HOUSE_CTOR_CALLS = 54
MIN_HOUSE_EXIT_CALLS = 62


# ───────────────────────────── 出口语义（新契约） ─────────────────────────────


def test_run_main_maps_none_to_zero() -> None:
    with pytest.raises(SystemExit) as ei:
        run_main(lambda: None)
    assert ei.value.code == 0


def test_run_main_keeps_an_explicit_code() -> None:
    with pytest.raises(SystemExit) as ei:
        run_main(lambda: 3)
    assert ei.value.code == 3


def test_run_main_maps_keyboard_interrupt_to_130() -> None:
    def interrupted() -> int | None:
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as ei:
        run_main(interrupted)
    assert ei.value.code == 130


def test_house_parser_keeps_std_argparse_behaviour() -> None:
    """空 argv 能解析、`-h` 照 argparse 语义退出 0（不注入自己的 formatter / 旋钮）。"""
    p = house_parser("说明")
    assert p.parse_args([]) is not None
    with pytest.raises(SystemExit) as ei:
        p.parse_args(["-h"])
    assert ei.value.code == 0


# ───────────────────────────── 唯一构造点（防回退） ─────────────────────────────


def test_no_bare_argument_parser_in_the_production_tree() -> None:
    assert cli_surface.bare_parser_sites() == []


def test_no_entry_leaves_the_old_way() -> None:
    assert cli_surface.old_style_exit_sites() == []


def test_house_constructors_are_actually_adopted() -> None:
    n = cli_surface.house_ctor_calls()
    assert n >= MIN_HOUSE_CTOR_CALLS, f"house 构造点采用数掉了（{n} < {MIN_HOUSE_CTOR_CALLS}）"


def test_house_exit_is_actually_adopted() -> None:
    n = cli_surface.house_exit_calls()
    assert n >= MIN_HOUSE_EXIT_CALLS, f"house 出口采用数掉了（{n} < {MIN_HOUSE_EXIT_CALLS}）"
