"""`common.fs.append_jsonl` 的单写者契约（2026-11-02，z4-polish it60 缺局）。

**事故形态**：两轮 eval 可以重叠（`trainer/loop_eval.py::_sweep_eval_tail` 刻意不 join：
it55 的尾局还在落账，it60 已开评），而每轮各持一把 `jsonl_lock`、各自 `open(..., "a")`。
Windows 上两个追加句柄**不是**原子的 O_APPEND（各写各的句柄偏移）：后写者覆盖前写者
刚写的行首 ⇒ 账本留下 119 字节的断头行（`"killerKinds"…` 开头），summary 重扫少一局
且把成因报成 `undispatched`。同一形态也会写坏 `dist-agent-meta.jsonl`。

**契约**：同一路径的任何时刻**至多一个追加句柄**。判据不赌 OS 竞态时序——打桩
`open` 把「句柄同时开着」的窗口放大并统计并发数，因此可复现（AGENTS §7：先红后修）。
"""

from __future__ import annotations

import builtins
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import common.fs
import worker.agent_meta


class _TrackedFile:
    """`with open(...) as f` 的透明代理：退出时关闭真句柄并递减并发计数。"""

    def __init__(self, f: Any, on_exit: Callable[[], None]) -> None:
        self._f = f
        self._on_exit = on_exit
        self._done = False

    def __enter__(self) -> Any:
        self._f.__enter__()
        return self._f

    def __exit__(self, *exc: Any) -> Any:
        try:
            return self._f.__exit__(*exc)
        finally:
            if not self._done:
                self._done = True
                self._on_exit()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._f, name)


def _track_append_handles(monkeypatch, path: Path, *, hold_sec: float = 0.02) -> dict:
    """打桩 `open`：目标路径的追加句柄计数（active/max/opens），并拖长持有窗口。

    `hold_sec` 是**判据的一部分**：真实事故里「两个句柄同时开着」的窗口只有微秒级；
    放大到毫秒级只改变观测窗口，不改变「是否有两个写者同时在写同一路径」这个事实。
    """
    real_open = builtins.open
    state = {"active": 0, "max": 0, "opens": 0}
    guard = threading.Lock()
    target = Path(path)

    def _release() -> None:
        with guard:
            state["active"] -= 1

    def fake_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        f = real_open(file, mode, *args, **kwargs)
        if "a" in str(mode) and Path(file) == target:
            with guard:
                state["active"] += 1
                state["opens"] += 1
                state["max"] = max(state["max"], state["active"])
            time.sleep(hold_sec)  # sleep-ok: 夹具模拟——放大「两个追加句柄同时开着」的写盘窗口
            return _TrackedFile(f, _release)
        return f

    monkeypatch.setattr(builtins, "open", fake_open)
    return state


def test_same_path_never_has_two_append_handles(tmp_path, monkeypatch) -> None:
    """核心判据：多线程并发追加同一账本，任何时刻至多一个追加句柄（旧实现 max≥2）。"""
    path = tmp_path / "eval_log.jsonl"
    state = _track_append_handles(monkeypatch, path)
    n_threads, per_thread = 6, 5
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []

    def run(tid: int) -> None:
        try:
            barrier.wait(timeout=10)  # 让 6 个写者在同一时刻起跑（复现重叠轮）
            for i in range(per_thread):
                common.fs.append_jsonl(path, {"t": tid, "i": i, "pad": "x" * 200})
        except BaseException as e:  # 线程里的异常要带回主线程断言
            errors.append(e)

    threads = [threading.Thread(target=run, args=(k,)) for k in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "追加调用挂死（写者线程没给 ack）"
    assert errors == [], errors
    assert state["max"] == 1, (
        f"同一账本出现 {state['max']} 个并发追加句柄（{state['opens']} 次 open）"
        "——Windows 上会互相覆盖行首"
    )

    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == n_threads * per_thread, "并发追加重叠导致行丢失/损坏"
    for tid in range(n_threads):
        seq = [r["i"] for r in rows if r["t"] == tid]
        assert seq == list(range(per_thread)), f"线程 {tid} 的行序被写者打乱：{seq}"


def test_write_error_reaches_caller_and_writer_keeps_running(tmp_path, monkeypatch) -> None:
    """写失败必须回到调用方（`_record_with_retry` 靠它重试），且写者线程不得随之死掉。"""
    path = tmp_path / "nested" / "ledger.jsonl"
    real_open = builtins.open
    calls = {"n": 0}

    def flaky_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if Path(file) == path and "a" in str(mode):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(13, "被别的句柄占着")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", flaky_open)
    with pytest.raises(OSError):
        common.fs.append_jsonl(path, {"i": 0})
    common.fs.append_jsonl(path, {"i": 1})
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"i": 1}], "一次写失败后写者必须继续服务后续行"


def test_record_agent_meta_swallows_write_errors(tmp_path) -> None:
    """meta 是观测面：写不进去（这里是目标是个目录 ⇒ IsADirectoryError）不得影响结算。"""
    bad = tmp_path / "meta-is-a-dir"
    bad.mkdir()
    worker.agent_meta.record_agent_meta(bad, {"node": "n1", "ok": True})
