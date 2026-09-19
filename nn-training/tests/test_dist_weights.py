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
import time

import pytest

import dist_common


@pytest.fixture(autouse=True)
def _reset_weights_push_cache():
    dist_common.weights_push_cache_reset()
    yield
    dist_common.weights_push_cache_reset()


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
    assert [n["id"] for n in alive] == ["self", "a97"]  # 入参顺序
    assert sorted(seen) == ["http://a97", "http://mac", "http://self"]
    # 日志按完成顺序（as_completed）；内容点名成功/失败
    joined = "\n".join(logs)
    assert "weights[rollout] -> self (kept)" in joined
    assert "weights POST to mac failed" in joined and "excluded" in joined
    assert "weights[rollout] -> a97 (purged)" in joined


def test_post_weights_parallel_empty(monkeypatch) -> None:
    def fail(*a, **k):
        raise AssertionError("must not call post_weights on empty node list")

    monkeypatch.setattr(dist_common, "post_weights", fail)
    assert dist_common.post_weights_parallel([], "r", "s", b"", timeout=1.0) == []


def test_weights_push_cache_reuse_partition_and_forget(monkeypatch) -> None:
    """volume 同 it 补波：首波成功节点进 reuse，不再 POST；forget 后回到 need。"""
    dist_common.weights_push_cache_reset()
    calls: list[str] = []

    def fake_post(url, auth_key, iter_id, sha, weights_bytes, timeout=120.0, kind="rollout"):
        calls.append(url)
        return "purged"

    monkeypatch.setattr(dist_common, "post_weights", fake_post)
    nodes = [
        {"id": "self", "url": "http://self", "key": "k"},
        {"id": "mac", "url": "http://mac", "key": "k"},
    ]
    wver = "a" * 64
    # 初波：全部 need
    reuse, need = dist_common.partition_weights_nodes(nodes, wver)
    assert reuse == [] and need == nodes
    alive = dist_common.post_weights_parallel(nodes, "r.1", wver, b"{}", timeout=5.0)
    assert [n["id"] for n in alive] == ["self", "mac"]
    assert dist_common.weights_already_pushed(wver, "self")
    assert dist_common.weights_already_pushed(wver, "mac")
    # 补波：全部 reuse → 调用方跳过 POST
    reuse, need = dist_common.partition_weights_nodes(nodes, wver)
    assert [n["id"] for n in reuse] == ["self", "mac"]
    assert need == []
    # 新 wver（PPO 更新后）→ 全量重发
    wver2 = "b" * 64
    reuse, need = dist_common.partition_weights_nodes(nodes, wver2)
    assert reuse == [] and need == nodes
    # ping/codeHash exclude → forget → 该节点回到 need
    dist_common.forget_weights_node("mac")
    reuse, need = dist_common.partition_weights_nodes(nodes, wver)
    assert [n["id"] for n in reuse] == ["self"]
    assert [n["id"] for n in need] == ["mac"]
    dist_common.weights_push_cache_reset()


def test_post_weights_parallel_notes_cache(monkeypatch) -> None:
    dist_common.weights_push_cache_reset()

    def fake_post(url, auth_key, iter_id, sha, weights_bytes, timeout=120.0, kind="rollout"):
        if "bad" in url:
            raise dist_common.DistError(500, "x")
        return "kept"

    monkeypatch.setattr(dist_common, "post_weights", fake_post)
    nodes = [
        {"id": "ok", "url": "http://ok", "key": "k"},
        {"id": "bad", "url": "http://bad", "key": "k"},
    ]
    wver = "c" * 64
    alive = dist_common.post_weights_parallel(nodes, "r", wver, b"x", timeout=5.0)
    assert [n["id"] for n in alive] == ["ok"]
    assert dist_common.weights_already_pushed(wver, "ok")
    assert not dist_common.weights_already_pushed(wver, "bad")
    dist_common.weights_push_cache_reset()


def test_post_weights_parallel_on_alive_fires_per_success(monkeypatch) -> None:
    """边分发边开采：每个成功节点立刻回调，不必等全部 POST 结束。"""
    dist_common.weights_push_cache_reset()
    spawned: list[str] = []
    order: list[str] = []

    def fake_post(url, auth_key, iter_id, sha, weights_bytes, timeout=120.0, kind="rollout"):
        order.append(f"post:{url.rsplit('/', 1)[-1]}")
        if "slow" in url:
            time.sleep(0.15)
        if "fail" in url:
            raise dist_common.DistError(500, "x")
        return "purged"

    monkeypatch.setattr(dist_common, "post_weights", fake_post)
    nodes = [
        {"id": "fast", "url": "http://fast", "key": "k", "c": 1},
        {"id": "slow", "url": "http://slow", "key": "k", "c": 1},
        {"id": "fail", "url": "http://fail", "key": "k", "c": 1},
    ]
    t0 = time.monotonic()
    alive = dist_common.post_weights_parallel(
        nodes,
        "r",
        "d" * 64,
        b"x",
        timeout=5.0,
        on_alive=lambda nd: spawned.append(nd["id"]),
    )
    dt = time.monotonic() - t0
    assert sorted(spawned) == ["fast", "slow"]
    assert "fail" not in spawned
    assert [n["id"] for n in alive] == ["fast", "slow"]
    # 并行：总墙钟应接近最慢成功节点（0.15s），远小于串行 0.15+ 其它
    assert dt < 0.4
    dist_common.weights_push_cache_reset()


def test_rollout_collect_sec_user_caliber_2026_09_19() -> None:
    """用户口径：权重开始分发 → 样本齐可交 PPO。"""
    assert dist_common.rollout_collect_sec(100.0, 145.0) == 45.0
    assert dist_common.rollout_collect_sec(None, 10.0) is None
    assert dist_common.rollout_collect_sec(10.0, None) is None
    # 含与采集重叠的分发墙钟：起点在分发开始，不是全节点 ready
    t_start, t_done_all, t_settle = 0.0, 50.0, 80.0
    assert dist_common.rollout_collect_sec(t_start, t_settle) == 80.0
    assert dist_common.rollout_collect_sec(t_done_all, t_settle) == 30.0  # 旧口径（作废）

