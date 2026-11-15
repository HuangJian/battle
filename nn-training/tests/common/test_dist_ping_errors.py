"""探活/探测出站点的捕获面：**网络面**返回「不可用」，**程序员错**穿出去（2026-11-15，G4 窄捕）。

为什么要单独一条：这些函数的失败返回值（`None` / `False`）不是「没答」，而是**业务结论**——
`node_ping → None` = 这台节点被排除出本轮分发、份额落回本地。宽捕把「连不上」（设计好的语义）
与本模块自己的 bug（回执形状变了 ⇒ `AttributeError`、字段拼错 ⇒ `KeyError`）抹成同一分支 ⇒
现场表现是「全部节点突然不可用」＋一行真因都没有（同族事故：`remote/hub_http.py` 的探针把
`AttributeError` 读成 `PROBE_TRANSIENT`，轮询环永远「还没好」）。
"""

from __future__ import annotations

import pytest

import common.distribution as dist


def _patch_request(monkeypatch, fn) -> None:
    monkeypatch.setattr(dist, "_request", fn)


def test_node_ping_treats_garbage_reply_as_node_unusable(monkeypatch) -> None:
    """200 但 body 不是 JSON ⇒ 与「连不上」同档（`None`），不许崩。"""

    def fake(_url, _auth, _timeout, **_kw):
        return 200, b"<html>captive portal</html>"

    _patch_request(monkeypatch, fake)
    assert dist.node_ping("http://node", "tok") is None


def test_node_ping_lets_a_programmer_error_through(monkeypatch) -> None:
    """`_request` 抛的不看形状的错（`AttributeError` / `KeyError`）必须穿出去。

    改回 `except Exception` ⇒ 本用例红（返回 None，「节点不可用」被当成正常结论）。
    """

    def buggy(_url, _auth, _timeout, **_kw):
        raise AttributeError("'list' object has no attribute 'status'")

    _patch_request(monkeypatch, buggy)
    with pytest.raises(AttributeError):
        dist.node_ping("http://node", "tok")


def test_cancel_task_lets_a_programmer_error_through(monkeypatch) -> None:
    """`cancel_task` 同理：取消请求的失败面是网络，不是「取消逻辑自己炸了」。"""

    def buggy(_url, _auth, _timeout, **_kw):
        raise KeyError("key")

    _patch_request(monkeypatch, buggy)
    with pytest.raises(KeyError):
        dist.cancel_task("http://node", "tok", "r.1:rollout:0:1")
