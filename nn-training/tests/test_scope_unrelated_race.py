"""R1-10 负向范围用例：**别把 eval 侧的长尾竞速一起删了**。

2026-09-22 的 P3 删的是 **hub 的竞速广播判定**（`race_decision` / `race_mode` / `--race`）。
仓库里还住着**另一套同名机制**：`rl/queue_local.py` 的 eval 长尾竞速（`pick_tail_race` /
`race_tier_ok` / `pick_race_target`，消费者 `rl/eval_dispatch.py`）。两套机制名字像、目的像、
**不该同生共死**——P3 的删除清单是逐符号列出的，本文件就是那条边界的机械门禁：后人大扫除
时顺手删掉 eval 侧这一套，会在**这里**红，而不是在几个旬之后的评测里以「长尾任务又排到
天亮」的形式悄悄复发。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.queue_local as Q


def test_eval_tail_race_symbols_still_exist() -> None:
    """三样符号仍在（且可调用）——P3 的删除范围**不**包含它们。"""
    for name in ("pick_tail_race", "race_tier_ok", "pick_race_target"):
        assert hasattr(Q, name), f"eval 长尾竞速的 {name} 被误删了（R1-10 的边界）"
        assert callable(getattr(Q, name))


def test_eval_dispatch_still_consumes_the_tail_race() -> None:
    """消费者还在接线（删了符号但断了调用点同样是回归）。"""
    src = (ROOT / "rl" / "eval_dispatch.py").read_text(encoding="utf-8")
    assert "queue_local" in src
    assert any(sym in src for sym in ("pick_tail_race", "race_tier_ok", "pick_race_target"))
