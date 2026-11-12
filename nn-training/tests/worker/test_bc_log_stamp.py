"""云端 BC 日志逐行带时间戳（2026-11-12 用户口径）。

云机的 BC 日志长这样：`[11:30:51] [worker] job …: BC start …` 之后跟着一整片
**没有时间**的 `[train]` / `[epoch …]` 行——没法把「第 7 个 epoch 什么时候打完」
与 hub 事件对齐。根因：worker 在**本进程内**调 `train/bc.py::train`，训练器的
`print` 直接落 stdout（`[worker]` 行的时间戳是 worker 自己加的）。

修法：训练器（`worker/train/bc.py` / `bc_paddle.py` / `device.py`）统一走
`common.log.say()`——每行一个 `[HH:MM:SS] ` 前缀，正文一字不改。本文件钉两件事：
① `say` 的格式；② 设备退化那条 WARNING 既保留原措辞也带上时间戳（云端日志面板
按 `[train]` 抓它，措辞是既有产物）。
"""

import re

import pytest

from common.log import say
from worker.train.bc_core import fire_weight_log_lines
from worker.train.device import resolve_bc_device

STAMP = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")


def test_say_stamps_each_line(capsys: pytest.CaptureFixture[str]) -> None:
    say("[train] samples total=10")
    out = capsys.readouterr().out
    assert STAMP.match(out), out
    assert out.endswith("[train] samples total=10\n")


def test_device_warning_keeps_wording_and_gets_stamp(
    capsys: pytest.CaptureFixture[str],
) -> None:
    resolve_bc_device("cuda-dp", cuda_probe=lambda: (True, 1))
    out = capsys.readouterr().out
    assert STAMP.match(out), out
    assert "[train] WARNING" in out and "退化" in out


def test_v6_fire_weight_line_does_not_claim_pos_weight() -> None:
    """v6 的 fire 是 3 类，`fire_pos_weight` 只是哨兵：日志不得再说「正例加权开启 / pos=」。"""
    lines = fire_weight_log_lines(
        action_v6=True,
        weight_exp=0.3,
        fire_pos_weight=-1.0,
        fire_weighted=True,
        fire_counts={0: 1000, 1: 5, 2: 0},
    )
    text = "\n".join(lines)
    assert "正例加权" not in text
    assert "pos=" not in text
    assert "no-shot=1000 before-turn=5 after-turn=0" in text
    assert "inverse-freq" in text
    assert lines[-1].startswith("[train] weight_exponent=0.30")


def test_legacy_fire_weight_line_unchanged() -> None:
    """legacy 二分类路径的文案是历史产物，一字不改。"""
    line = fire_weight_log_lines(
        action_v6=False,
        weight_exp=1.0,
        fire_pos_weight=12.5,
        fire_weighted=True,
        fire_counts={0: 111651, 1: 0},
    )[0]
    assert line == "[train] fire_pos_weight=12.50（正例加权开启） fire counts neg=111651 pos=0"
