"""tests/worker/test_stage_rotation_events.py —— 窗口的**账本读数**（plan/it-stage-ratation §9.1）。

为什么要有这一格：窗口此前只活在进程日志的 `describe()` 那**一行**里（滚走就没了）与控制台
读不到的 `volume_stage_stats` 键上 ⇒ 开着轮换的课在「指标」页与全关课程长得一模一样，
「某关连着几轮没被采到」这类账只能靠人翻 jsonl。

本文件钉两件事：

  1. `event_from_args` 的三态：缺席 ⇒ None（**不写键**）、even ⇒ mode/per_it/all/window 正确、
     pinned ⇒ pinned 原样进读数；
  2. **同源**：读数里的 `window` 与采集侧 `window_from_args` 逐元素相同（同一个 `(rotate_seed, it)`）
     —— 两处各算一次 = 控制台显示的窗口和实际采的窗口可能不是一回事。

写侧落键（`write_iteration` / `write_rollout_end`）在
`tests/worker/test_rotation_ledger_fields.py`。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.stage_rotation import event_from_args, window_from_args
from worker.volume_waves import parse_stages_arg


def _args(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "rotation_mode": "",
        "rotation_per_it": 0,
        "rotation_pinned": "",
        "rotate_seed": 4242,
        "stages": "0-34",
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_disabled_returns_none_so_no_key_is_written() -> None:
    """缺席 ⇒ None（调用方**不写键**，老账本行逐字节不变）。"""
    assert event_from_args(_args(), 7) is None


def test_even_payload_matches_the_collection_window() -> None:
    """★ 同源：读数里的 window 必须与采集侧同一份（同 args 同 it 两路逐元素相同）。"""
    args = _args(rotation_mode="even", rotation_per_it=12)
    ev = event_from_args(args, 3, rotate_seed=4242)
    assert ev == {
        "mode": "even",
        "per_it": 12,
        "pinned": [],
        "all": 35,
        "window": window_from_args(args, 3, parse_stages_arg("0-34"), rotate_seed=4242),
    }
    assert ev is not None and len(ev["window"]) == 11  # ceil(35/12) 轮里最后一窗不满
    assert ev is not None and ev["window"] == sorted(ev["window"])


def test_pinned_payload_carries_the_pinned_stages() -> None:
    """pinned 模式：读数要能回答「哪几关是恒采的」（否则 `12/35` 看不出难点关是否每轮都在）。"""
    args = _args(rotation_mode="pinned", rotation_per_it=2, rotation_pinned="3-4")
    ev = event_from_args(args, 1, rotate_seed=9)
    assert ev is not None
    assert ev["mode"] == "pinned" and ev["per_it"] == 2
    assert ev["pinned"] == [3, 4]
    assert {3, 4} <= set(ev["window"])
    # 自定义关（关号非连续）：`all` 数的是**关数**，不是关号上界（读面要显示 `4/10` 而不是 `4/2035`）
    ev2 = event_from_args(
        _args(
            rotation_mode="pinned",
            rotation_per_it=2,
            rotation_pinned="2033-2034",
            stages="2000-2004,2030-2034",
        ),
        1,
        rotate_seed=9,
    )
    assert ev2 is not None
    assert (ev2["all"], ev2["pinned"], len(ev2["window"])) == (10, [2033, 2034], 4)


def test_window_key_follows_rotate_seed_and_it() -> None:
    """窗口键 = 种子流那把钥匙：换 `rotate_seed` / `it` ⇒ 读数跟着变（resume 后取同一个数）。"""
    args = _args(rotation_mode="even", rotation_per_it=5)
    a = event_from_args(args, 1, rotate_seed=1)
    b = event_from_args(args, 1, rotate_seed=2)
    c = event_from_args(args, 2, rotate_seed=1)
    assert a is not None and b is not None and c is not None
    assert a["window"] != b["window"]
    assert a["window"] != c["window"]
    # 同键两次独立构造逐元素相同（resume-safe）
    assert event_from_args(args, 1, rotate_seed=1) == a


def test_broken_config_is_loud_not_none(tmp_path: Path) -> None:
    """配置非法 ⇒ `ValueError`（不是静默 None）：拿不到关集就无法表达窗口。

    落账点的「捕住 + 日志」在 `tests/worker/test_rotation_ledger_fields.py` 钉。
    """
    import pytest

    for broken in (
        _args(rotation_mode="even", rotation_per_it=2, stages=""),
        _args(rotation_mode="even", rotation_per_it=2, stages="oops"),
        _args(rotation_mode="weird", rotation_per_it=2),
        _args(rotation_mode="pinned", rotation_per_it=1, rotation_pinned="99"),
    ):
        with pytest.raises(ValueError):
            event_from_args(broken, 1)
    # 关闭态不解析关集（老路径一个函数都不调）⇒ 缺 --stages 也不报
    assert event_from_args(_args(stages=""), 1) is None


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
