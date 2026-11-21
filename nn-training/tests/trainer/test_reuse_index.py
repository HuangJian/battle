"""`trainer/queue_local.ReuseIndex` —— 兄弟课「盘上现成局」索引的重建判据（P0-5，2026-11-21）。

目录快照的语义只有三条：同课程同 `wver` **只扫一次**；`wver`/课程变了就重建；重建后旧条目
不得残留（否则会把上一个代码版本的 shard 当现成局拷进来）。旧形状是 `_REUSE_INDEX` dict +
`_REUSE_SIG` 一格 list，三个写点分散；这里全部钉在对象的公开读面上。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer import queue_local as ql


def _args(course: str = "c91") -> SimpleNamespace:
    return SimpleNamespace(course_name=course)


def _row(tag: str) -> tuple[Path, Path, str]:
    return (Path(f"/w/{tag}"), Path(f"/w/{tag}/w0"), "stamp")


def test_same_course_and_wver_scans_once_and_returns_the_same_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _sources(_log, wver, _args):
        calls.append(wver)
        return {(1, 7): _row("a")}

    monkeypatch.setattr(ql, "rollout_sources", _sources)
    idx = ql.ReuseIndex()
    first = idx.rows("w1", _args())
    second = idx.rows("w1", _args())
    assert first is second, "同一轮必须返回同一份 dict（下游按引用读）"
    assert calls == ["w1"], "同 `wver` 不得重复扫盘"
    assert first == {(1, 7): _row("a")}


def test_new_wver_or_new_course_rebuilds_and_drops_stale_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    rows_by_wver = {"w1": {(1, 7): _row("old")}, "w2": {(2, 9): _row("new")}}

    def _sources(_log, wver, _args):
        return rows_by_wver[wver]

    monkeypatch.setattr(ql, "rollout_sources", _sources)
    idx = ql.ReuseIndex()
    assert idx.rows("w1", _args()).keys() == {(1, 7)}
    # 换权重 ⇒ 重建（旧轮条目必须消失：那是上一版代码/权重的 shard）
    after_wver = idx.rows("w2", _args())
    assert after_wver == {(2, 9): _row("new")}
    # 换课程（同权重）⇒ 也要重建
    calls: list[str] = []

    def _counting(_log, wver, _args):
        calls.append(wver)
        return {(1, 7): _row("c91")}

    monkeypatch.setattr(ql, "rollout_sources", _counting)
    assert idx.rows("w2", _args("c92")) == {(1, 7): _row("c91")}
    assert calls == ["w2"], "换课程也必须重扫（索引是「本课程工作目录里有什么」）"


def test_first_call_with_empty_signature_still_scans(monkeypatch: pytest.MonkeyPatch) -> None:
    """空课程名 + 空 `wver` 也必须扫（旧实现的初始签名 `("", "")` 会跳过首扫返回空索引）。"""
    calls: list[tuple[str, str]] = []

    def _sources(_log, wver, args):
        calls.append((wver, args.course_name))
        return {(3, 3): _row("empty-sig")}

    monkeypatch.setattr(ql, "rollout_sources", _sources)
    idx = ql.ReuseIndex()
    assert idx.rows("", _args("")) == {(3, 3): _row("empty-sig")}
    assert calls == [("", "")]


def test_clear_rows_forces_a_rescan(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _sources(_log, wver, _args):
        calls.append(wver)
        return {(1, 1): _row("x")}

    monkeypatch.setattr(ql, "rollout_sources", _sources)
    idx = ql.ReuseIndex()
    idx.rows("w1", _args())
    idx.clear_rows()
    idx.rows("w1", _args())
    assert calls == ["w1", "w1"], "清场后同 `wver` 也要重扫（签名与内容一起归零）"


def test_module_index_is_one_named_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    """模块级只有 `_REUSE` 这个持有者（旧的 `_REUSE_INDEX` / `_REUSE_SIG` 都已消失）。"""
    holders = {n for n, v in vars(ql).items() if isinstance(v, ql.ReuseIndex)}
    assert holders == {"_REUSE"}, f"索引持有者应当只有一个：{sorted(holders)}"
    assert not hasattr(ql, "_REUSE_SIG"), "签名槽应已收进对象"
    # 公开读面仍走 `reuse_index()`（调用点不改）：两次调用是同一份 dict
    monkeypatch.setattr(ql, "rollout_sources", lambda *_a: {(1, 1): _row("x")})
    a = ql.reuse_index("w1", _args())
    b = ql.reuse_index("w1", _args())
    assert a is b
    assert a == {(1, 1): _row("x")}
