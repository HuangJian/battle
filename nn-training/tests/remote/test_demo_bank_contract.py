"""demo bank 的字段契约（`worker.ppo.demo.DemoBank`）—— 类型与运行期安全阀必须是同一份名单。

为什么需要这条：这条链原来全靠裸 `dict` + 字符串键（`bank["obs"]` / `if "extra" in bank`）。
最贵的失败形态不是 `KeyError`，而是 **`extra` 被静默跳过** —— BC 项少掉一半，而日志里
一切正常（`engine_paddle` 的 `if "extra" in demo_bank` 就是这个形状）。现在字段表在
`worker/ppo/demo.py`，`check_demo_bank` 的 `need` 从它派生；本用例钉三件事：
  1. 必需键就是导出端（`tools/build_demo_bank.py`）写的这五个 —— 改名字必须同时改这里；
  2. `check_demo_bank` 用的名单**派生自** `DemoBank`（不是另抄一份，抄了就会分叉）；
  3. 解码出来的键集与字段表一致，缺 `extra` 的老 bank 被**响亮拒收**而不是降级。
"""

from __future__ import annotations

import io

import numpy as np
import pytest

from common.errors import ProtocolError
from remote.train_common import check_demo_bank, decode_demo_bank
from worker.ppo.demo import DEMO_BANK_FIELDS, DemoBank

#: 导出端（`tools/build_demo_bank.py` 的 `np.savez_compressed(...)`）写的五个字段。
EXPORTED_FIELDS = {"obs", "scalars", "extra", "actions", "masks"}


class _PrepRecorder:
    """`prep_b.add(section, line)` 的最小替身（只记一行读数）。"""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def add(self, section: str, line: str) -> None:
        self.lines.append((section, line))


def _bank_bytes(*, drop: str | None = None) -> bytes:
    # 逐字段写出来（`**dict` 过不了 numpy 的重载：`savez` 的 kwargs 值类型是联合体，
    # mypy 会挑到 `bool` 那支）；`drop` 只允许丢掉 `extra` —— 那正是要复现的老 bank。
    obs = np.zeros((4, 16, 26, 26), dtype=np.uint8)
    scalars = np.zeros((4, 40), dtype=np.float32)
    extra = np.zeros((4, 37), dtype=np.float32)
    actions = np.zeros((4, 2), dtype=np.int64)
    masks = np.ones((4, 7), dtype=np.uint8)
    buf = io.BytesIO()
    if drop is None:
        np.savez_compressed(
            buf, obs=obs, scalars=scalars, extra=extra, actions=actions, masks=masks
        )
    else:
        assert drop == "extra", drop
        np.savez_compressed(buf, obs=obs, scalars=scalars, actions=actions, masks=masks)
    return buf.getvalue()


def test_required_keys_are_the_exported_fields() -> None:
    """字段表 == 导出端名单（改名 / 增删字段都必须同时改这里，否则拒收面与类型会分叉）。"""
    assert set(DemoBank.__required_keys__) == EXPORTED_FIELDS
    assert DemoBank.__optional_keys__ == frozenset()
    assert DEMO_BANK_FIELDS == EXPORTED_FIELDS


def test_check_demo_bank_uses_the_typed_field_list() -> None:
    """安全阀的名单派生自字段表：解码一个完整 bank 能过，读数行带上 N 与 src。"""
    bank = decode_demo_bank(_bank_bytes())
    prep = _PrepRecorder()
    check_demo_bank("jid-1", bank, 0.02, 8, "upload", prep)
    assert prep.lines == [("demo bank", "N=4 coef=0.02 per_mb=8 src=upload")]


def test_missing_extra_is_rejected_not_silently_skipped() -> None:
    """缺 `extra`（老 bank）必须拒收 —— 静默跳过等于 BC 项少一半而日志照常。"""
    bank = decode_demo_bank(_bank_bytes(drop="extra"))
    assert set(bank) == EXPORTED_FIELDS - {"extra"}
    with pytest.raises(ProtocolError) as ei:
        check_demo_bank("jid-2", bank, 0.02, 8, "upload", _PrepRecorder())
    assert "extra" in str(ei.value)
