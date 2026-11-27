"""`remote.push_client._probe_sha`：`None` 只表示**问不到**，不表示「什么都算」。

`_probe_sha` 的返回值被 `_cached_or_last_hit` 读成三态：`True` 命中 / `False` 明说没有 /
`None` 问不到（沿用上次命中，最坏只是重传一次）。原先的 `except Exception: pass` 把
「200 但 body 不是 JSON」也归进 `None`：节点回执形状变了这种**确定性**问题会一直表现为
「偶尔问不到 ⇒ 每轮都白传 code.zip」，而真因从不出现。这条与 `common/distribution` 的探针、
`remote/hub_http` 的薄壳是同一条判据：**只吞「世界不合作」，不吞「我们的 bug」**。
"""

from __future__ import annotations

from typing import Any

import pytest

from common.ports import PortPair
from remote import push_client as pc


def _request_returning(status: int, body: bytes) -> Any:
    def _stub(*_a: object, **_k: object) -> tuple[int, bytes]:
        return status, body

    return _stub


def test_probe_reports_a_hit_and_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc, "WIRE", PortPair(_request_returning(200, b'{"cached": true}')))
    hit = pc._probe_sha("http://node", "tok", "/code-sha/z", 5.0)
    monkeypatch.setattr(pc, "WIRE", PortPair(_request_returning(200, b'{"cached": false}')))
    miss = pc._probe_sha("http://node", "tok", "/code-sha/z", 5.0)
    assert hit is True and miss is False, "三态：节点明说命中 / 明说没有"


def test_a_non_200_status_is_cannot_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    """404/500 是节点明说「这个端点现在不给答案」⇒ 三态的中间那一档。"""
    monkeypatch.setattr(pc, "WIRE", PortPair(_request_returning(404, b"")))
    got = pc._probe_sha("http://node", "tok", "/code-sha/z", 5.0)
    assert got is None


def test_a_network_error_is_cannot_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise OSError("隧道断了")

    monkeypatch.setattr(pc, "WIRE", PortPair(_boom))
    got = pc._probe_sha("http://node", "tok", "/code-sha/z", 5.0)
    assert got is None, "链路断了是「问不到」，不是「没有」"


def test_a_200_with_a_non_json_body_must_not_look_like_cannot_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """节点回执形状变了是**确定性**问题：读成 `None` 会让每轮都白传一次 code.zip。"""
    monkeypatch.setattr(pc, "WIRE", PortPair(_request_returning(200, b"<html>proxy</html>")))
    with pytest.raises(ValueError):
        pc._probe_sha("http://node", "tok", "/code-sha/z", 5.0)


def test_a_programmer_error_is_not_disguised_as_cannot_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_request` 签名变了（TypeError）不是「节点没答」—— 那是我们自己坏了。"""

    def _wrong_arity(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise TypeError("_request() got an unexpected keyword argument")

    monkeypatch.setattr(pc, "WIRE", PortPair(_wrong_arity))
    with pytest.raises(TypeError):
        pc._probe_sha("http://node", "tok", "/code-sha/z", 5.0)
