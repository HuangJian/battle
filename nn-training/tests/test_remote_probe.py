"""R2c-3：非阻塞结果探针（`probe_job_result` / `poll_job` / `poll_result`）。

让位能不能装，全看这一个原语的三条性质：

1. **非阻塞**：一次请求就返回，绝不自己轮询（单进程调度器是同步调它的，一旦这里等，
   整条调度链就堵住了）；
2. **「还没好」与「永远好不了」分开**：202/404 = pending（让位，下一轮再问）；
   410 = 终局失败（抛 `JobFailedError` ⇒ 上层立刻停腿）。把后者当成前者正是 x3-step
   事故把「bun 缺失」写成 25 分钟超时的原因；
3. **两条链路共用一份分类**：hub 与节点侧只差一个端点路径（`/jobs` vs `/job`），
   除路径外语义必须一致——历史上两段各自写的轮询就是这么漂开的。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import remote.hub_client as hc
import remote.push_client as pc
from common.protocol import JobFailedError
from remote.hub_client import (
    PROBE_PENDING,
    PROBE_READY,
    PROBE_TRANSIENT,
    HubClientError,
    probe_job_result,
)


class _FakeHTTP:
    """底层 HTTP 的记账假件：`push()` 预置响应，`seen` 记录每次请求的 (路径, 超时)。

    请求多于预置响应时**响亮失败**——它正好能钉住「探针只问了一次」：若被测代码偷偷
    轮询，第二次请求会拿不到响应而报错。
    """

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []
        self.box: list[tuple[int, bytes]] = []

    def push(self, *items: tuple[int, bytes]) -> None:
        self.box.extend(items)


@pytest.fixture()
def http(monkeypatch: pytest.MonkeyPatch) -> _FakeHTTP:
    fake = _FakeHTTP()

    def fake_request(base: str, token: str, path: str, timeout: float = 30.0, **kw: object):
        fake.seen.append((path, f"{timeout:g}"))
        if not fake.box:
            raise AssertionError(f"假件被问了第 {len(fake.seen)} 次：预置响应已用尽")
        return fake.box.pop(0)

    monkeypatch.setattr(hc, "_request", fake_request)
    return fake


# ------------------------------------------------------------------ 三态分类


def test_200_is_ready_with_the_result(http: _FakeHTTP) -> None:
    http.push((200, b'{"agg": {"kl": 0.1}}'))
    res = probe_job_result("http://h", "t", "j1")
    assert res.state == PROBE_READY
    assert res.result == {"agg": {"kl": 0.1}}


@pytest.mark.parametrize("code", [202, 404])
def test_queued_statuses_are_pending(code: int, http: _FakeHTTP) -> None:
    """202/404 = 还没回（正常排队）——让位等下一轮，不是失败。"""
    http.push((code, b""))
    res = probe_job_result("http://h", "t", "j1")
    assert res.state == PROBE_PENDING and res.result is None


@pytest.mark.parametrize("code", [500, 502, 503])
def test_5xx_is_transient(code: int, http: _FakeHTTP) -> None:
    """隧道/边缘瞬时错误 = 「没答」，诊断信息留在 detail 里（退避日志要用）。"""
    http.push((code, b"boom"))
    res = probe_job_result("http://h", "t", "j1")
    assert res.state == PROBE_TRANSIENT and res.detail == f"HTTP {code}"


def test_network_error_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **kw: object):
        raise OSError("tunnel i/o timeout")

    monkeypatch.setattr(hc, "_request", boom)
    res = probe_job_result("http://h", "t", "j1")
    assert res.state == PROBE_TRANSIENT and "OSError" in res.detail


def test_410_is_terminal_and_raises(http: _FakeHTTP) -> None:
    """★ 终局：节点已判定跑不成 ⇒ 抛 JobFailedError（带上原因），**不是** pending。"""
    body = json.dumps({"error": "bun 未安装", "fail_kind": "ProtocolError"}).encode()
    http.push((410, body))
    with pytest.raises(JobFailedError) as ei:
        probe_job_result("http://h", "t", "j1")
    assert "bun 未安装" in str(ei.value)


def test_other_status_is_a_protocol_error(http: _FakeHTTP) -> None:
    http.push((401, b'{"error": "unauthorized"}'))
    with pytest.raises(HubClientError, match="HTTP 401"):
        probe_job_result("http://h", "t", "j1")


def test_non_object_body_is_a_protocol_error(http: _FakeHTTP) -> None:
    http.push((200, b"[1, 2]"))
    with pytest.raises(HubClientError, match="结果非对象"):
        probe_job_result("http://h", "t", "j1")


# ------------------------------------------------------------------ 非阻塞


def test_poll_job_asks_exactly_once(http: _FakeHTTP) -> None:
    """★ 非阻塞：一次请求就返回（调度器同步调它，自己轮询 = 堵住整条链）。"""
    http.push((404, b""), (200, b'{"ok": 1}'))
    assert hc.poll_job("http://h", "t", "j1") is None
    assert len(http.seen) == 1  # 第二次响应根本没被消费


def test_poll_job_returns_result_when_ready(http: _FakeHTTP) -> None:
    http.push((200, b'{"ok": 1}'))
    assert hc.poll_job("http://h", "t", "j1") == {"ok": 1}


def test_poll_job_uses_a_short_timeout(http: _FakeHTTP) -> None:
    """探针超时必须短（一次网络卡顿的代价是「再等一轮」，不是「永远等」）。"""
    http.push((404, b""))
    hc.poll_job("http://h", "t", "j1")
    assert float(http.seen[0][1]) <= 10.0


# ------------------------------------------------------------------ 两条链路同源


def test_push_poll_uses_the_node_endpoint(http: _FakeHTTP) -> None:
    """节点侧路径是 `/job/{jid}/result`（hub 侧多一个 s）——同源但端点不同。"""
    http.push((200, b'{"ok": 1}'))
    assert pc.poll_result("http://n", "t", "j1") == {"ok": 1}
    assert http.seen[0][0] == "/job/j1/result"
    http.push((200, b'{"ok": 1}'))
    assert probe_job_result("http://h", "t", "j1").result == {"ok": 1}
    assert http.seen[1][0] == "/jobs/j1/result"
