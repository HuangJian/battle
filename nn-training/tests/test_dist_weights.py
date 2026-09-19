"""test_dist_weights.py — 权重下发 kept 短路径 + 并行 POST 回归（2026-09-19）。

背景：x20-rebirth it19 rollout 208s 复盘——串行 6 节点 POST ~50s + 补波 kept 仍整包
上传。本文件钉死：
  ① 探针命中 → 不发 body、直接 kept；
  ② 探针 404（旧 agent）→ 完整 POST，语义不变；
  ③ 探针报未缓存 → 完整 POST；
  ④ post_weights_parallel 并行收集、失败排除、顺序稳定。
"""

from __future__ import annotations

import gzip
import json

import dist_common


class _Call:
    def __init__(self, method: str, url: str, headers: dict, data: bytes | None):
        self.method = method
        self.url = url
        self.headers = headers
        self.data = data


def _make_request_stub(script: list[tuple[int, bytes]], calls: list[_Call]):
    """按序回放 (status, body)；记录每次 _request 的入参。"""

    def _request(url, auth_key, timeout, data=None, headers=None, method=None):
        calls.append(_Call(method or ("POST" if data is not None else "GET"), url, headers or {}, data))
        if not script:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        status, body = script.pop(0)
        return status, body

    return _request


def test_probe_hit_skips_body(monkeypatch) -> None:
    calls: list[_Call] = []
    script = [(200, json.dumps({"ok": True, "cached": True}).encode())]
    monkeypatch.setattr(dist_common, "_request", _make_request_stub(script, calls))

    mode = dist_common.post_weights(
        "http://node",
        "tok",
        "run.19",
        "a" * 64,
        b'{"w":1}',
        kind="rollout",
    )
    assert mode == "kept"
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.endswith("/v1/weights/cached")
    assert calls[0].data is None
    assert calls[0].headers.get("X-Weights-Sha256") == "a" * 64
    assert calls[0].headers.get("X-Kind") == "rollout"
    assert not script, "探针命中后不得再发 POST body"


def test_probe_unsupported_falls_back_to_full_post(monkeypatch) -> None:
    """旧 agent 无 /v1/weights/cached → 404 → 完整 gzip POST，返回 kept/purged。"""
    calls: list[_Call] = []
    weights = b'{"w":2}'
    sha = "b" * 64
    script = [
        (404, b'{"error":"not found"}'),
        (204, b""),  # agent: 同 sha 幂等 kept（204 无 body）
    ]
    monkeypatch.setattr(dist_common, "_request", _make_request_stub(script, calls))

    mode = dist_common.post_weights("http://node", "tok", "run.19", sha, weights)
    assert mode == "kept"
    assert len(calls) == 2
    assert calls[0].method == "GET"
    assert calls[1].method == "POST"
    assert calls[1].url.endswith("/v1/weights")
    assert calls[1].data is not None
    body = calls[1].data
    assert body is not None
    assert gzip.decompress(body) == weights
    assert calls[1].headers.get("X-Weights-Sha256") == sha
    assert calls[1].headers.get("Content-Encoding") == "gzip"


def test_probe_uncached_full_post_purged(monkeypatch) -> None:
    calls: list[_Call] = []
    script = [
        (200, json.dumps({"ok": True, "cached": False}).encode()),
        (200, json.dumps({"ok": True, "cache": "purged"}).encode()),
    ]
    monkeypatch.setattr(dist_common, "_request", _make_request_stub(script, calls))

    mode = dist_common.post_weights("http://node", "tok", "run.19", "c" * 64, b"{}")
    assert mode == "purged"
    assert len(calls) == 2
    assert calls[1].method == "POST"
    body = calls[1].data
    assert body is not None
    assert gzip.decompress(body) == b"{}"


def test_probe_network_error_falls_back(monkeypatch) -> None:
    calls: list[_Call] = []

    def _request(url, auth_key, timeout, data=None, headers=None, method=None):
        calls.append(_Call(method or "?", url, headers or {}, data))
        if url.endswith("/v1/weights/cached"):
            raise OSError("connection reset")
        return 200, json.dumps({"cache": "purged"}).encode()

    monkeypatch.setattr(dist_common, "_request", _request)
    mode = dist_common.post_weights("http://node", "tok", "run.19", "d" * 64, b"x")
    assert mode == "purged"
    assert [c.method for c in calls] == ["GET", "POST"]


def test_probe_weights_cached_shapes(monkeypatch) -> None:
    def _ok(url, auth_key, timeout, data=None, headers=None, method=None):
        return 200, json.dumps({"cached": True}).encode()

    monkeypatch.setattr(dist_common, "_request", _ok)
    assert dist_common.probe_weights_cached("http://n", "k", "e" * 64) is True
    assert dist_common.probe_weights_cached("http://n", "k", "") is None

    def _boom(url, auth_key, timeout, data=None, headers=None, method=None):
        raise TimeoutError("t")

    monkeypatch.setattr(dist_common, "_request", _boom)
    assert dist_common.probe_weights_cached("http://n", "k", "e" * 64) is None


def test_post_weights_parallel_order_and_failures(monkeypatch) -> None:
    nodes = [
        {"id": "self", "url": "http://self", "key": "k1", "c": 8},
        {"id": "mac", "url": "http://mac", "authKey": "k2", "c": 8},
        {"id": "a97", "url": "http://a97", "key": "k3", "c": 7},
    ]
    seen: list[str] = []
    logs: list[str] = []

    def fake_post(url, auth_key, iter_id, sha, weights_bytes, timeout=120.0, kind="rollout"):
        seen.append(url)
        if "mac" in url:
            raise dist_common.DistError(503, "busy")
        return "kept" if "self" in url else "purged"

    monkeypatch.setattr(dist_common, "post_weights", fake_post)
    alive = dist_common.post_weights_parallel(
        nodes,
        "run.19",
        "w" * 64,
        b"{}",
        timeout=5.0,
        kind="rollout",
        log=logs.append,
    )
    assert [n["id"] for n in alive] == ["self", "a97"]
    assert sorted(seen) == ["http://a97", "http://mac", "http://self"]
    # 日志按配置顺序回放；失败节点点名排除
    assert logs[0].startswith("[dist] weights[rollout] -> self (kept)")
    assert "mac" in logs[1] and "excluded" in logs[1]
    assert logs[2].startswith("[dist] weights[rollout] -> a97 (purged)")


def test_post_weights_parallel_empty(monkeypatch) -> None:
    def fail(*a, **k):
        raise AssertionError("must not call post_weights on empty node list")

    monkeypatch.setattr(dist_common, "post_weights", fail)
    assert dist_common.post_weights_parallel([], "r", "s", b"", timeout=1.0) == []
