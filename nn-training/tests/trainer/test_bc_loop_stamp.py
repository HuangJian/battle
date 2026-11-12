"""bc_loop 解析 epoch 行前剥掉训练器的时间戳（2026-11-12）。

训练器现在每行自带 `[HH:MM:SS] `（见 `tests/worker/test_bc_log_stamp.py`），而
`trainer/bc_loop.py` 的本地 BC 腿按 `line.startswith("[epoch")` 与行首正则解析
账本 / eval 边界。不剥就会**静默停写** `bc_epoch`（本地训练看起来正常，账本与
面板却没有新行）——所以这一条必须有用例钉住。
"""

import re

from trainer.bc_loop import _strip_stamp

EPOCH = (
    "[11:30:53] [epoch   7/60] train_loss=0.1 val_loss=0.2 "
    "acc move=0.9 fire=0.8 value=28.2363 lr=1.00e-03"
)


def test_strip_stamp_makes_epoch_line_parseable() -> None:
    bare = _strip_stamp(EPOCH)
    assert bare.startswith("[epoch")
    # 与 bc_loop 的生产正则同形（v6 行带 value；该正则是行首锚定 + re.match）。
    m = re.match(
        r"\[epoch\s+(\d+)/(\d+)\] train_loss=([\d.]+) val_loss=([\d.]+) "
        r"acc move=([\d.]+) fire=([\d.]+)(?: value=[\d.]+ )?lr=([\d.eE+]+)",
        bare,
    )
    assert m is not None and (m.group(1), m.group(2)) == ("7", "60")


def test_strip_stamp_leaves_plain_lines_alone() -> None:
    assert _strip_stamp("[train] samples total=1") == "[train] samples total=1"
