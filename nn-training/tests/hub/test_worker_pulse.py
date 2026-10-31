"""test_worker_pulse.py — worker 静默哨兵（`hub/worker_pulse.py`，2026-10-31 事故的热修）。

事故现场：kaggle 云机在 `09:51:58` 之后再没有一条请求，hub 侧**一个字都没说**——只能事后
翻 `hub-server.out` 的 cadence 反推「死在 09:51:42 那份 claim 之后 18 秒」。本文件钉住
补上的那半条腿：

  ① **武装门槛**：本 episod 至少 `min_touches` 次请求才报 ⇒ push worker（hub 推、它只在回结果
     时出现）与一次性冒烟请求不会被误报成「死了」；
  ② **一段静默只报一次**（`alarmed` 位）；**恢复补一条** `recover`，且带**静默时长**——
     那是事后校准死亡窗口的唯一读数；
  ③ **遗忘窗口**：长期不出现的 worker 从表里掉出去（表不随进程寿命无限长）；
  ④ **上报体契约**：`component="hub-watchdog"` / silence ⇒ `severity="err"` / reason 带秒数与
     末次请求时刻（控制台按 `reason` 折叠，数字必须在这里）；
  ⑤ **接线**：`HubHandler.do_GET` / `do_POST` 会把带 `X-Worker-Id` 的请求打进哨兵
     （用 `object.__new__` 造 handler + 实例覆盖哨兵 ⇒ 不碰类属性、不开 socket）。
"""

from __future__ import annotations

import sys
from email.message import Message
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import WORKER_ID_HEADER
from hub.http_face import HubHandler
from hub.worker_pulse import (
    WORKER_PULSE_MIN_TOUCHES,
    WORKER_PULSE_SILENCE_SEC,
    WorkerPulse,
    error_body,
)


class _Clock:
    """假钟（与 `tests/hub/test_worker_prefetch.py::_Clock` 同规：手动推进，零墙钟）。"""

    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _armed(clock: _Clock, wid: str = "kaggle-c") -> WorkerPulse:
    """造一个「已经有轮询节奏」的哨兵：默认门槛次数 × 每 5s 一次。"""
    p = WorkerPulse(now_fn=clock)
    for _ in range(WORKER_PULSE_MIN_TOUCHES):
        p.touch(wid)
        clock.advance(5.0)
    return p


def test_silence_reported_once_then_recover() -> None:
    """① 一段静默只报一次；`touch` 回来补一条 recover（静默时长 = 断档）。"""
    clock = _Clock()
    p = _armed(clock)
    assert p.sweep() == []

    clock.advance(WORKER_PULSE_SILENCE_SEC + 1)
    ev = p.sweep()
    assert [e["kind"] for e in ev] == ["silence"], ev
    assert ev[0]["worker"] == "kaggle-c"
    assert ev[0]["silent"] >= WORKER_PULSE_SILENCE_SEC
    assert ev[0]["touches"] == WORKER_PULSE_MIN_TOUCHES

    # 同一段静默：不再重复报（ring 里的那条已经在，重复等于刷屏）
    clock.advance(60)
    assert p.sweep() == []

    # 恢复：请求回来 ⇒ 下拍发 recover，且静默时长覆盖整段断档
    p.touch("kaggle-c")
    rec = [e for e in p.sweep() if e["kind"] == "recover"]
    assert len(rec) == 1
    assert rec[0]["silent"] > WORKER_PULSE_SILENCE_SEC


def test_single_shot_requests_never_arm() -> None:
    """② 只来一两次的 worker（push / 冒烟）不武装 ⇒ 永远不报静默。"""
    clock = _Clock()
    p = WorkerPulse(now_fn=clock)
    p.touch("push-worker")
    clock.advance(600)
    p.touch("push-worker")  # 断档 > 阈值 ⇒ 新 episod，touches 重新计 1
    clock.advance(600)
    assert p.sweep() == []


def test_slow_paced_worker_never_arms() -> None:
    """② 次数够但**跨度太大**（push worker 逐份回结果，45s 一份）⇒ 不武装、不误报。"""
    clock = _Clock()
    p = WorkerPulse(now_fn=clock)
    for _ in range(5):
        p.touch("push-worker")
        clock.advance(45.0)
    assert p.readout()["push-worker"]["touches"] < WORKER_PULSE_MIN_TOUCHES
    clock.advance(WORKER_PULSE_SILENCE_SEC + 1)
    assert p.sweep() == []


def test_forget_window_drops_entry() -> None:
    """③ 超过遗忘窗口的条目被清掉（不再报，也不再占表）。"""
    clock = _Clock()
    p = WorkerPulse(now_fn=clock, forget_sec=300.0)
    for _ in range(WORKER_PULSE_MIN_TOUCHES):
        p.touch("gone")
    clock.advance(400)  # > forget_sec：静默与遗忘同时命中，遗忘优先
    assert p.sweep() == []


def test_disabled_pulse_is_inert() -> None:
    """`silence_sec=0` = 关闭（`--worker-silence-sec 0` 的逃生口）：touch/sweep 都不动。"""
    clock = _Clock()
    p = WorkerPulse(now_fn=clock, silence_sec=0.0)
    assert not p.enabled
    for _ in range(10):
        p.touch("kaggle-c")
    clock.advance(9999)
    assert p.sweep() == []


def test_error_body_contract() -> None:
    """④ 上报体契约（控制台按 `reason` 折叠 ⇒ 数字必须在这里，不能只在 detail）。"""
    silence = error_body(
        {
            "kind": "silence",
            "worker": "kaggle-c",
            "silent": 137.0,
            "last": 1_700_000_000.0,
            "touches": 9,
        }
    )
    assert silence["component"] == "hub-watchdog"
    assert silence["severity"] == "err"
    assert silence["worker"] == "kaggle-c"
    assert silence["reason"].startswith("worker 静默 137s")

    recover = error_body({"kind": "recover", "worker": "kaggle-c", "silent": 620.0})
    assert recover["severity"] == "warn"
    assert recover["reason"].startswith("worker 恢复（静默 620s")


class _PingHandler(HubHandler):
    """`/ping` 的最小宿主：真 `HubHandler`（**继承**，不是改方法）+ 自己的哨兵表。

    为什么用子类而不是往实例上赋值方法：mypy 的 `method-assign` 会把「给实例塞一个函数当
    方法」判成错误（它确实也绕过了类型检查）；子类还能顺手把 `_worker_pulse` 换成本类独有
    的一份，进程级类属性（`HubHandler._worker_pulse`）因此不被测试污染。
    """

    _worker_pulse = WorkerPulse()

    def _auth_ok(self) -> bool:
        return True

    def _json(self, obj: Any, status: int = 200) -> None:
        return None


def _handler(wid: str) -> Any:
    """最小 handler：不跑 `__init__`（它会读 socket），只手装 `do_GET` 用到的那几样。"""
    h = object.__new__(_PingHandler)
    h.path = "/ping"
    h.headers = Message()
    if wid:
        h.headers[WORKER_ID_HEADER] = wid
    return h


def test_do_get_touches_the_pulse() -> None:
    """⑤ 接线：带 `X-Worker-Id` 的请求真的打进了哨兵（这是整条腿的注入点）。"""
    _PingHandler._worker_pulse.reset()  # 类属性是共享面：先清一次（本文件唯一改它的用例）
    h = _handler("kaggle-c")
    for _ in range(WORKER_PULSE_MIN_TOUCHES):
        HubHandler.do_GET(h)
    assert _PingHandler._worker_pulse.readout()["kaggle-c"]["touches"] == WORKER_PULSE_MIN_TOUCHES

    HubHandler.do_GET(_handler(""))
    assert list(_PingHandler._worker_pulse.readout()) == ["kaggle-c"]  # 无身份不打点
