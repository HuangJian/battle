"""tests/hub/test_relay_ping_cache.py —— relay 模式下 `/v1/ping` 不再等一次往返。

**现场**（2026-11-18 23:00 DSW，relay 模式「能连上但不稳」的定量根因）：
hub 每轮派发前都要 ping（调用方预算 `common.distribution.STATUS_TIMEOUT_SEC=3s`），
而 relay 里一次 `call` 得等节点**下一次长轮询**（`POLL_WAIT_DEFAULT=20s`）才有人取走 ——
两层预算差一个量级 ⇒ 约一半探活**假失败**（节点被记成「这轮够不着」），那个 handler
线程还要空占到 15s 才放手。今夜 hub 日志：`/relay/dsw/v1/ping`
**186 ok / 192 ×「对端先走」/ 67 × 504**。

修法：判据换成两个**本地事实** —— ① 节点最近一次 `_poll` 有多久（通道心跳，节点自己发起，
不受隧道往返方差影响）② 上一次真 ping 的载荷（过期照用、后台刷一次）。本文件钉六条：

* 心跳新鲜 + 有载荷 ⇒ 200 且**一次真调用都不发**；
* 心跳过期 ⇒ 503 **快速失败**且零往返（别让调用方等 15s 才拿到同样的结论）；
* 载荷过期 ⇒ **先答旧值**、后台刷一次（单飞行：刷新没落地时再来的请求只顶答）；
* 没有载荷 ⇒ 老实走真调用，**并把这次的载荷存下来**（否则缓存永远空，短路白写）；
* 载荷比 `PING_SERVE_MAX_SEC` 还旧 ⇒ 不用化石台账（宁可等一次往返）；
* 单飞行开关本身：`claim` 第二次为 `False`，`note_ping` / `release` 都会放开。

**判据形态**：一律按「HTTP 状态 + 载荷字段 + `call` 被调了几次」判，不做措辞子串匹配
（文本断言护栏不抬格）；时间类断言只给宽松上限，别拿它当计时钟。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub import relay as relay_mod
from hub.relay import CHANNEL_STALE_SEC, PING_SERVE_MAX_SEC, PING_TTL_SEC, NodeRelay
from hub.server import _HubQueue, make_server

TOKEN = "sekret"


@pytest.fixture()
def node() -> Iterator[str]:
    """独占的节点名：`RELAY` 是进程级单例，用同一名字会串味（用完拔掉 box）。"""
    name = f"pingcache-{id(object()) & 0xFFFF:04x}-{time.perf_counter_ns() % 9973:04d}"
    yield name
    relay_mod.RELAY._boxes.pop(name, None)


@pytest.fixture()
def hub_port(tmp_path: Path) -> Iterator[int]:
    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield int(srv.server_address[1])
    finally:
        srv.shutdown()
        srv.server_close()


def _get(port: int, path: str) -> tuple[int, bytes]:
    """本机回环直取（relay 调用面只认回环，不需要 token）；4xx/5xx 也当读数返回。"""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=20.0) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), e.read()


def _boom(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("这一次本该命中缓存，却发了真调用（= 又等了一轮往返）")


def _fresh(relay: NodeRelay, node: str) -> None:
    """把通道心跳刷成「刚刚轮询过」（`poll` 是唯一刷新它的人，`wait_sec=0` 立刻回）。"""
    assert relay.poll(node, wait_sec=0.0) is None


def test_fresh_payload_answers_with_no_roundtrip(
    node: str, hub_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay = relay_mod.RELAY
    _fresh(relay, node)
    relay.note_ping(node, {"alive": True, "queued": 3})
    monkeypatch.setattr(relay, "call", _boom)  # 任何一次真调用都会当场炸

    status, body = _get(hub_port, f"/relay/{node}/v1/ping")

    assert status == 200, body
    payload = json.loads(body)
    assert (payload["alive"], payload["queued"]) == (True, 3)
    # 缓存年龄随答带走：排障时能一眼看出「这个 200 是几秒前的台账」
    assert isinstance(payload["relayAgeSec"], float) and payload["relayAgeSec"] >= 0.0


def test_stale_heartbeat_fails_fast_without_roundtrip(
    node: str, hub_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """节点不再轮询 ⇒ 503 立刻回；**不许**再发一次注定要等 15s 的调用。"""
    relay = relay_mod.RELAY
    _fresh(relay, node)
    relay.note_ping(node, {"alive": True})
    relay._boxes[node].last_poll = relay.now() - (CHANNEL_STALE_SEC + 5.0)
    monkeypatch.setattr(relay, "call", _boom)

    at = time.monotonic()
    status, body = _get(hub_port, f"/relay/{node}/v1/ping")
    took = time.monotonic() - at

    assert status == 503, body
    assert json.loads(body)["error"].startswith("relay 通道心跳过期")
    # timing-ok: 上界兜底（判据是不许「慢失败」；3s 只挡挂起，真走往返要等 ≤20s 的长轮询）
    assert took < 3.0, f"快速失败写成了慢失败：{took:.1f}s"


def test_expired_payload_is_served_then_refreshed_single_flight(
    node: str, hub_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """过期载荷**先答**（调用方零等待），后台只许起一个刷新；刷新落地后载荷变新值。"""
    relay = relay_mod.RELAY
    _fresh(relay, node)
    relay.note_ping(node, {"v": 1})
    relay._boxes[node].ping_at = relay.now() - (PING_TTL_SEC + 5.0)

    started, release = threading.Event(), threading.Event()
    seen: list[tuple[str, str, float]] = []

    def fake_call(
        _node: str,
        method: str,
        path: str,
        _headers: dict[str, str],
        _body: bytes,
        *,
        timeout: float,
    ) -> tuple[int, dict[str, str], bytes]:
        seen.append((method, path, timeout))
        started.set()
        # 呆真：只在用例放行时才返回。原来的 10s 上界会在 xdist 满载时**自己到期**
        # （一次调度停顿就能越过）⇒ busy 被清、后面的请求又起一次刷新 ⇒
        # `len(seen) == 2` 假红（2026-11-19 全量门禁）。上界只为用例崩掉时收尾。
        release.wait(120.0)
        return 200, {"Content-Type": "application/json"}, b'{"v": 2}'

    monkeypatch.setattr(relay, "call", fake_call)

    status, body = _get(hub_port, f"/relay/{node}/v1/ping")
    assert status == 200, body
    assert json.loads(body)["v"] == 1, "过期载荷也必须先答（这才是「不等往返」的意义）"
    assert started.wait(10.0), "后台刷新没被起起来"

    # 单飞行：刷新还挂在链路上时，后面来的请求只能顶答，不许再开线程
    for _ in range(3):
        status, body = _get(hub_port, f"/relay/{node}/v1/ping")
        assert status == 200 and json.loads(body)["v"] == 1, body
    assert len(seen) == 1, seen

    release.set()
    assert _wait_payload(relay, node, 2), relay.ping_snapshot(node)
    assert len(seen) == 1, seen
    # 刷新用的等待上限比调用方预算（3s）宽得多 —— 它不挡任何人，给足一个长轮询周期
    assert seen[0][0] == "GET" and seen[0][1] == "/v1/ping" and seen[0][2] > 20.0, seen[0]


def _wait_payload(relay: NodeRelay, node: str, want: object, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body, _age = relay.ping_snapshot(node)
        if body is not None and body.get("v") == want:
            return True
        # sleep-ok: 轮询步长（等的是「后台刷新已落地」这个谓词，兜底只挡挂起）
        time.sleep(0.02)
    return False


def test_missing_payload_falls_back_and_then_caches(
    node: str, hub_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有载荷 ⇒ 走真调用；**这次拿到的载荷要存下来**，下一条就不必再等往返。"""
    relay = relay_mod.RELAY
    _fresh(relay, node)
    calls: list[str] = []

    def fake_call(
        _node: str,
        _method: str,
        path: str,
        _headers: dict[str, str],
        _body: bytes,
        *,
        timeout: float,
    ) -> tuple[int, dict[str, str], bytes]:
        calls.append(path)
        return 200, {"Content-Type": "application/json"}, b'{"ok": 1, "n": 7}'

    monkeypatch.setattr(relay, "call", fake_call)

    status, body = _get(hub_port, f"/relay/{node}/v1/ping")
    assert status == 200 and json.loads(body) == {"ok": 1, "n": 7}, body
    status, body = _get(hub_port, f"/relay/{node}/v1/ping")
    assert status == 200, body
    payload = json.loads(body)
    assert (payload["ok"], payload["n"]) == (1, 7)
    assert set(payload) >= {"ok", "n", "relayAgeSec"}  # 多出来的 relayAgeSec = 这条是缓存答的
    assert calls == ["/v1/ping"], calls


def test_fossil_payload_is_not_used(
    node: str, hub_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """比 `PING_SERVE_MAX_SEC` 还旧的载荷一律不认 —— 别拿化石台账骗派发器。"""
    relay = relay_mod.RELAY
    _fresh(relay, node)
    relay.note_ping(node, {"v": 1})
    relay._boxes[node].ping_at = relay.now() - (PING_SERVE_MAX_SEC + 5.0)
    calls: list[str] = []

    def fake_call(
        _node: str,
        _method: str,
        path: str,
        _headers: dict[str, str],
        _body: bytes,
        *,
        timeout: float,
    ) -> tuple[int, dict[str, str], bytes]:
        calls.append(path)
        return 200, {"Content-Type": "application/json"}, b'{"v": 2}'

    monkeypatch.setattr(relay, "call", fake_call)

    status, body = _get(hub_port, f"/relay/{node}/v1/ping")
    assert status == 200 and json.loads(body)["v"] == 2, body
    assert calls == ["/v1/ping"], calls


def test_refresh_claim_is_single_flight(node: str) -> None:
    """开关本身：拿到手的那个负责刷、其余的立刻放弃；放回是认领者的义务（成败都要放）。"""
    relay = NodeRelay()
    assert relay.claim_ping_refresh(node) is True
    assert relay.claim_ping_refresh(node) is False
    # 载荷落地**不**代认领者放回：两条路都放会互相把对方的重入权打开 ⇒ 同一个节点两个刷新线程
    relay.note_ping(node, {"x": 1})
    assert relay.claim_ping_refresh(node) is False
    relay.release_ping_refresh(node)
    assert relay.claim_ping_refresh(node) is True
    # 从没轮询过的节点：`channel_age` 是 None（「没有心跳」而非「心跳很旧」），别当 0 秒
    assert NodeRelay().channel_age("never-seen") is None
