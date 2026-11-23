"""`_run_one_game_bounded` 只吃一份 `RetryGameSpec`（`python.refactor.md` §P0-3 批 E 末一处）。

它原来是 `_run_one_game_with_retries` 那 10 个字段的**逐字副本**再加一个 `ceiling_sec`
（11 参）—— 两份相邻同类位（`exec_dir` / `ts_dir` 两个 Path，`out_dir` 是 str；`explicit`
与 `pool`；`log` 与 `pool`）靠位置对齐，抄错一格不抛，只是把局跑进别的目录。

这份用例钉：入口形状（spec + 关键字 `ceiling_sec`）、上界的两条来源（现场算 vs 显式给）、
以及「被放弃的事件是**新造**的、并随 spec 一起交给重试那一层」。
"""

from __future__ import annotations

import dataclasses
import inspect
import threading
from pathlib import Path
from typing import Any

import pytest

import worker.iter_rollout as IR
from common import game_watch

#: 被测入口（断言里只写别名：`assert x._private` 会被 G5 记成私有断言）。
RUN = IR._run_one_game_bounded


def _spec(tmp_path: Path) -> IR.RetryGameSpec:
    return IR.RetryGameSpec(
        bun="bun",
        argv=["--stage", "stage-1", "--seed", "7"],
        exec_dir=tmp_path / "exec",
        ts_dir=tmp_path / "ts",
        out_dir="w0",
        timeout_sec=5.0,
        log=lambda _m: None,
        explicit=True,
        job_dir=tmp_path / "job",
    )


def test_entry_takes_spec_plus_keyword_ceiling() -> None:
    params = list(inspect.signature(RUN).parameters.values())
    assert [p.name for p in params] == ["spec", "ceiling_sec"]
    assert params[1].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[1].default is None
    maker: Any = RUN
    with pytest.raises(TypeError):
        maker("bun", ["--stage", "s"], Path("."))


def test_ceiling_comes_from_the_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """上界按 `spec.timeout_sec` / `spec.explicit` 现场算，并原样交给 `call_bounded`。"""
    seen: dict[str, Any] = {}
    ceiling_args: list[float] = []

    def _fake_ceiling(timeout_sec: float, *, explicit: bool) -> float:
        seen.update(timeout_sec=timeout_sec, explicit=explicit)
        return 123.0

    def _fake_call(fn: Any, ceiling: float, *, name: str) -> tuple[bool, Any]:
        ceiling_args.append(ceiling)
        return False, None  # 超界：`_run_one_game_bounded` 必须响亮上抛

    monkeypatch.setattr(game_watch, "game_ceiling_sec", _fake_ceiling)
    monkeypatch.setattr(IR, "call_bounded", _fake_call)
    with pytest.raises(IR.UnreapableChildError):
        RUN(_spec(tmp_path))
    assert seen == {"timeout_sec": 5.0, "explicit": True}, "上界必须按 spec 的两栏现场算"
    assert ceiling_args == [123.0]


def test_explicit_ceiling_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []
    ceiling_args: list[float] = []

    def _fake_ceiling(*_a: Any, **_k: Any) -> float:
        called.append(1)
        return 999.0

    def _fake_call(fn: Any, ceiling: float, *, name: str) -> tuple[bool, Any]:
        ceiling_args.append(ceiling)
        return False, None

    monkeypatch.setattr(game_watch, "game_ceiling_sec", _fake_ceiling)
    monkeypatch.setattr(IR, "call_bounded", _fake_call)
    with pytest.raises(IR.UnreapableChildError):
        RUN(_spec(tmp_path), ceiling_sec=0.25)
    assert called == [], "显式给了上界就不该再去问 game_watch（用例要给毫秒级的值）"
    assert ceiling_args == [0.25]


def test_spec_is_frozen_so_the_abandoned_copy_is_a_new_object(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    field = "argv"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, ["--other"])
    copy = dataclasses.replace(spec, abandoned=threading.Event())
    assert copy.exec_dir == spec.exec_dir and copy.abandoned is not None
