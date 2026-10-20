"""B1 v6 的 shard 契约（2026-10-20）：`a_hold.npy` 存在性 ⇒ 9 宽掩码 + 三头。

**为什么单独成家**：`mask` 此前**完全没有形状校验** —— `_RL_SHARD_SHAPES` 只盯
`obs`/`scalars`/`extra`。7 宽 mask 配 v6 模型会一路静默读到训练完才表现异常：
第 4–6 列被当成 fire、hold 两列根本不存在，`masked_logsoftmax` 照算、loss 照降，
但学的是错的东西。这类失败没有任何一处会报错。

本文件钉四条：
1. **宽度自洽**：`a_hold` 在 ⇒ 9；不在 ⇒ 7；交叉即炸。
2. **可选字段**：legacy shard 缺 `a_hold.npy` 时**整键省略**（不是填 0 ——
   填 0 会让 `a_hold` 恒为 0，静默学一个"永不 hold"的策略）。
3. **legacy 零影响**：7 宽 mask 的旧 shard 照旧读得动（历史语料不废）。
4. **常量来源**：9 = 4+3+2，与 `computeMasks` 的字段顺序一致。

纯 numpy，零 torch。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.ppo import np_core


def test_mask_width_constants_match_action_spaces() -> None:
    """legacy 7（move5+fire2）/ v6 9（move4+fire3+hold2）—— 9 不是"7+2"凑的。"""
    assert np_core._MASK_DIM_LEGACY == 7
    assert np_core._MASK_DIM_V6 == 9
    assert np_core._MASK_DIM_V6 == 4 + 3 + 2


def test_legacy_mask_accepted() -> None:
    """① 7 宽 + 无 a_hold ⇒ 放行（历史 shard 继续有效）。"""
    np_core._check_mask_width(np.zeros((5, 7), dtype=np.int64), False, "shardA")


def test_v6_mask_accepted() -> None:
    """② 9 宽 + a_hold ⇒ 放行。"""
    np_core._check_mask_width(np.zeros((5, 9), dtype=np.int64), True, "shardB")


@pytest.mark.parametrize(
    ("shape", "is_v6"),
    [
        ((5, 7), True),  # v6 模型拿到 7 宽掩码：第 4–6 列被当 fire，hold 两列不存在
        ((5, 9), False),  # legacy 模型拿到 9 宽掩码：多出的两列被静默忽略
        ((5, 8), False),  # 只改了一半的宽度
    ],
)
def test_mask_width_mismatch_raises(shape: tuple[int, int], is_v6: bool) -> None:
    """③ 交叉即炸，且错误信息点明两侧实际值（看日志的人不用再翻代码）。

    用 `pytest.raises(match=)` 而不是 `assert "..." in msg`：后者是「判据挂在措辞上」的
    文本断言（`tests/test_source_text_assert_budget.py` 的护栏口径 A 命中）。
    `match` 只要求**正则能匹配**，仍锁住了「错误里必须带实际宽度和文件名」这件事。
    """
    with pytest.raises(ValueError, match=rf"mask\.npy.*a_hold\.npy.*\({shape[1]},\)"):
        np_core._check_mask_width(np.zeros(shape, dtype=np.int64), is_v6, "shardC")


def test_optional_fields_omit_when_missing(tmp_path: Path) -> None:
    """④ legacy shard 缺 `a_hold.npy` ⇒ **整键省略**（不是填 0）。

    填 0 的危险：`e.get("a_hold")` 会拿到 0 而不是 None ⇒ engine 走 v6 分支、
    对全 0 的 hold 标签算 logp ⇒ 静默学一个"永不 hold"的策略。所以必须缺键。
    """
    spec: dict[str, tuple[str, npt.DTypeLike]] = {"a_hold": ("a_hold.npy", np.int64)}
    out = np_core.load_shard_fields(str(tmp_path), spec, optional={"a_hold"})
    assert out.get("a_hold") is None


def test_optional_fields_loaded_when_present(tmp_path: Path) -> None:
    """⑤ v6 shard 有 `a_hold.npy` ⇒ 正常读到（int64、值原样）。"""
    np.save(tmp_path / "a_hold.npy", np.array([0, 1, 1, 0, 1], dtype=np.uint8))
    spec: dict[str, tuple[str, npt.DTypeLike]] = {"a_hold": ("a_hold.npy", np.int64)}
    out = np_core.load_shard_fields(str(tmp_path), spec, optional={"a_hold"})
    assert out["a_hold"].tolist() == [0, 1, 1, 0, 1]


def test_spec_declares_hold_columns_as_optional() -> None:
    """⑥ 两个新列必须登记在 spec 且标记 optional（漏标记 ⇒ legacy shard 直接 KeyError）。"""
    # 用 `.get(...)` 逐键比形状（`==` 判据，不是 `in` 文本断言）。
    assert np_core._RL_SHARD_SPEC.get("a_hold") == ("a_hold.npy", np.int64)
    assert np_core._RL_SHARD_SPEC.get("lp_hold") == ("lp_hold.npy", np.float32)
    assert frozenset({"a_hold", "lp_hold"}) == np_core._RL_SHARD_OPTIONAL
    # 派生表（只有 reward.npy 的 shard）必须自动带上，不抄第二份名单
    assert np_core._RL_SHARD_SPEC_REWARD.get("a_hold") == ("a_hold.npy", np.int64)
