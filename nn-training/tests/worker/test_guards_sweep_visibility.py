"""`worker.loop_guards_sweep` 的孤儿波次清理：失败不致命，但**必须与「没东西可清」区分开**。

`_rotate_cleanup` 末尾那次 `sweep_failed_wave_dirs(...)` 原先写在 `except BaseException: pass`
里，两个后果叠在一起：

* `swept 0`（本轮确实没有可清的孤儿波次）与「清理器自己抛了」在日志里长得一模一样 ——
  而清理器的失败通常意味着**磁盘在涨**，那是几小时后才会以别的方式爆出来的事；
* `BaseException` 顺手吞掉 **Ctrl-C**：批量 rmtree 是本轮最慢的动作之一，按了中断却在下一轮
  接着跑，中断看起来「没生效」。
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from worker import loop_guards_sweep
from worker.loop_guards_sweep import TrainingGuardsSweep


def _probe(tmp_path: Path) -> TrainingGuardsSweep:
    probe = object.__new__(TrainingGuardsSweep)
    probe.args = types.SimpleNamespace(keep_iters=1, remote_job_root="")
    probe._traj_root = tmp_path
    probe._traj_dir = tmp_path / "run"
    probe._traj_dir.mkdir()
    return probe


def test_sweep_failure_is_reported_but_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines: list[str] = []

    def _explode(*a: Any, **k: Any) -> int:
        raise OSError("磁盘只读")

    monkeypatch.setattr(loop_guards_sweep, "sweep_failed_wave_dirs", _explode)
    # `log` 是模块级的（`from common.log import log`）：换掉它就能看到这一行到底有没有打。
    monkeypatch.setattr(loop_guards_sweep, "log", lines.append)
    _probe(tmp_path)._rotate_cleanup(it=5)  # 不抛即契约

    joined = "\n".join(lines)
    assert "workdir-sweep it5 skipped" in joined, f"失败必须有自己的一行，实际：{lines!r}"
    assert "OSError" in joined, "成因要带上：『清理器炸了』与『没有可清的』不是一回事"
    assert "磁盘只读" in joined


def test_keyboard_interrupt_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """收窄到 `Exception`：Ctrl-C 必须穿出去（批量 rmtree 期间按的「中断」不能在下一轮复活）。"""

    def _interrupted(*a: Any, **k: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(loop_guards_sweep, "sweep_failed_wave_dirs", _interrupted)
    monkeypatch.setattr(loop_guards_sweep, "log", lambda _m: None)
    with pytest.raises(KeyboardInterrupt):
        _probe(tmp_path)._rotate_cleanup(it=6)
