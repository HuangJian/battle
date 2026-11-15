"""EvalBoard 心跳失败必须看得见（`worker/eval_heartbeat.write_state` 的两个训练侧调用点）。

为什么单列一个文件：这两处（`TrainingDispatch._evalboard_yield` 关窗 /
`TrainingLifecycle._evalboard_idle` 开窗）原先都是 `except Exception: pass`。静默的后果不是
「少写一个 JSON」，而是 console 的「训练忙碌中 / 已等 N 分钟」**永久停在旧值**——训练侧一切正常，
屏幕上却像卡住，而这条链的读者正是靠那一个文件判断要不要干预。

口径与 G9 处置表一致：**失败仍不抛**（心跳绝不得反过来打断训练），但按 key 喊一次
（`default_router().warn_once`）：窗口开/关是每轮都写的，逐次喊会把日志刷成噪音。

判据都走**注入面**（`sys.modules["worker.eval_heartbeat"]`，这两个调用点本来就是延迟 import
的 DI seam）：记录到 `write_state` 的入参 = 「该记录的心跳有没有记」，而不是读混入的私有字段。
"""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path
from typing import Any

import pytest

from trainer.batch_runner import _heartbeat
from trainer.loop_dispatch import TrainingDispatch


def _recorder(calls: list[dict[str, Any]]) -> types.ModuleType:
    """假心跳模块（真 `ModuleType`，`sys.modules` 的契约）：记下每次 `write_state(**patch)` 的入参。"""

    def write_state(**patch: object) -> None:
        calls.append(dict(patch))

    mod = types.ModuleType("worker.eval_heartbeat")
    # 变量名挂属性（不用 `mod.write_state = …`：mypy 按 `ModuleType` 判 attr-defined；
    # 也不用 `setattr(mod, "write_state", …)` 常量形式：ruff B010 会拦）。
    for name, fn in (("write_state", write_state), ("now_ms", lambda: 42)):
        setattr(mod, name, fn)
    return mod


def _yield_probe() -> TrainingDispatch:
    probe = object.__new__(TrainingDispatch)
    probe._eb_window = threading.Event()
    probe._eb_thread = None
    return probe


def test_evalboard_yield_writes_closed_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常路径：关窗那一刻写 `window_open=False` + 关窗时刻 —— 这是 console 的读数来源。"""
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(sys.modules, "worker.eval_heartbeat", _recorder(calls))
    _yield_probe()._evalboard_yield()
    assert calls == [{"window_open": False, "last_window_closed_ts": 42}]


def test_evalboard_yield_reports_heartbeat_failure_once(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    probe = _yield_probe()
    # `None` ⇒ `from worker.eval_heartbeat import …` 抛 ImportError：这就是「包不可用」那条成因。
    monkeypatch.setitem(sys.modules, "worker.eval_heartbeat", None)
    probe._evalboard_yield()  # 不抛即前半段契约
    probe._evalboard_yield()

    out = capsys.readouterr().out
    assert out.count("关窗心跳没写成") == 1, f"同一 key 只许喊一次，实际：{out!r}"
    assert "console 会显示旧状态" in out, "要写清后果，否则读者不知道要不要管"


def test_evalboard_idle_reports_heartbeat_failure_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    """开窗一侧同理；且**认领单元**这一步照常走（心跳失败不许拖住派发）。"""
    from trainer.loop_lifecycle import TrainingLifecycle

    probe = object.__new__(TrainingLifecycle)
    probe._eb_window = threading.Event()
    probe._eb_thread = None
    probe.bun = None
    probe.args = types.SimpleNamespace(out=tmp_path)
    probe._traj_dir = tmp_path
    claimed: list[int] = []
    monkeypatch.setitem(sys.modules, "worker.eval_heartbeat", None)
    monkeypatch.setitem(
        sys.modules,
        "trainer.batch_eval",
        types.SimpleNamespace(maybe_dispatch_batch=lambda *a, **k: claimed.append(a[6])),
    )

    probe._evalboard_idle(3, None)
    probe._evalboard_idle(4, None)

    out = capsys.readouterr().out
    assert out.count("开窗心跳没写成") == 1, f"同一 key 只许喊一次，实际：{out!r}"
    assert claimed == [3, 4], "心跳只是记录，开窗与认领必须照做"


# ── 第三个调用点：批评估（`trainer/batch_runner._heartbeat`）──
#
# 它包的是「写心跳」这件事的**整块**（含 `from worker.eval_heartbeat import …`），所以比另外
# 两个点多兜一种成因：包导不进来。那在 console 上与「心跳根本没调」完全同形。


def test_batch_heartbeat_writes_the_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(sys.modules, "worker.eval_heartbeat", _recorder(calls))
    _heartbeat(it=7, unit_total=3)
    assert calls == [{"it": 7, "unit_total": 3}], "心跳入参必须原样落到 write_state"


def test_batch_heartbeat_reports_failure_once(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setitem(sys.modules, "worker.eval_heartbeat", None)
    _heartbeat(it=1)  # 不抛即契约：心跳绝不打断单元
    _heartbeat(it=2)

    out = capsys.readouterr().out
    assert out.count("批评估心跳没写成") == 1, f"同一 key 只许喊一次，实际：{out!r}"
    assert "runner_state 会停在旧值" in out, "要写清后果：console 那一栏读的就是它"
    assert "ModuleNotFoundError" in out, (
        "成因要带上（这里是包导不进来），否则又变成一句没有信息量的话"
    )
