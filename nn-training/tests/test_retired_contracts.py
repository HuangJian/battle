"""退役契约扫描的**唯一驱动**（清单与扫描器都在 `tests/retired_contracts.py`，2026-10-09）。

这里只做两件事：跑一遍单点扫描器、守住扫描面没缩水。**不要**在本文件里加第二条自扫逻辑——
`plan/nn-training-test-debt-cleanup.plan.md` §2-T2 的全部意义就是「全仓只有一个扫描点」。
"""

from __future__ import annotations

from pathlib import Path

from tests.retired_contracts import (
    MIN_SURFACE_FILES,
    RETIRED_CONTRACTS,
    code_only,
    prod_sources,
    scan,
)

ROOT = Path(__file__).resolve().parents[1]


def test_no_retired_contract_token_is_back_in_production_code() -> None:
    """退役腿的标识在生产**代码**里零命中（`run` 作为来源枚举值留着，不在本表里）。"""
    hits = scan(ROOT)
    assert hits == [], f"退役契约又回到生产代码里：{hits}（清单见 tests/retired_contracts.py）"


def test_the_scan_surface_has_not_silently_shrunk() -> None:
    """扫描面缩水 = 判据变永真（2026-09-30 刀 4 漏了 `biz/`，静默少扫 64 个模块）。"""
    srcs = prod_sources(ROOT)
    assert len(srcs) >= MIN_SURFACE_FILES, (
        f"生产扫描面只剩 {len(srcs)} 个文件（基线 {MIN_SURFACE_FILES}）—— 有目录搬到扫描面外了？"
    )


def test_the_scan_sees_code_not_comments() -> None:
    """`code_only` 的口径：注释/字符串里的名字不算复活（否则「文档写得越诚实越红」）。"""
    sample = 'def f():\n    """_remote_run_segment 是历史。"""\n    x = "_run_wait_sec"\n    return x\n'
    assert "run_wait_sec" in sample and "run_wait_sec" not in code_only(sample)


def test_the_contract_list_is_not_empty() -> None:
    """清单空 = 扫描器恒绿（本表的唯一失效方式是「没人往里加」；这条把空表变成红的）。"""
    assert RETIRED_CONTRACTS and all(c.tokens and c.why for c in RETIRED_CONTRACTS)
