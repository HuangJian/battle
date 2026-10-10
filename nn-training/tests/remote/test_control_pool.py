"""test_control_pool.py — 控制面**连接池 + 短预算重抽**（plan/aistudio-transfer-hardening §3.5，B2/B3）。

现场（2026-10-09，aistudio 走 Cloudflare quick tunnel）：控制面 `p0_p50=1.1s` 而
`p0_p95=30.3s`，取消环每 1.5s 一个包 ⇒ 每个包都要付一次建连/TLS；而慢起来的时候
**一次干等 30s**，把「链路坏了」伪装成「还没回来」。

两件事一起做才有效：

* **B2 池化**：控制面（小包、高频）复用连接，省每请求一次握手。省的是**建连**不是字节；
* **B3 短预算 + 立刻换连接重抽**：30s 的不可见等待切成 6s 的**可见失败**，随即换一条连接再试。

本文件钉五件事：

1. **只服务控制面**：bulk（payload/code/blob/result）永不进池——「换连接 = 重抽一次签」是
   bulk 侧对抗慢链路的**唯一**手段（§22），池化会把那条能力抹掉；
2. **回环不进池**（本机 hub 走 `net_http.urlopen` 绕代理，不能改）；
3. **异常即丢弃重建**：池化的另一半是失败处理，坏连接不能留在池里被下一次复用；
4. **代理判据与 `_get_opener` 同源**：池化若绕开代理，非回环的控制请求会直接连不通；
5. **预算安全性**：6s × 2 次失败之后，心跳（60s）/租约（300s）仍有余量。
"""

from __future__ import annotations

import inspect
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.control_pool as cp
import remote.http as http_mod
import remote.job_lifecycle as jl
from common.protocol import HEARTBEAT_SEC
from remote.job_lifecycle import CONTROL_TIMEOUT_SEC


class _Resp:
    """够用的响应替身（`status` + `read()`）。"""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


def _no_proxy(monkeypatch) -> None:
    """清掉**整组**代理 env（本容器里这些是有值的，不清就走不到直连分支）。

    ★ 清的是全套而不是 http/https 四个：`build_opener()` 在没给 `ProxyHandler` 时会自己
    补一个默认的（`ProxyHandler(None)` → `getproxies()`），那个默认读的是**全量** env
    （含 `all_proxy` / `no_proxy`）⇒ 只清四个名字时 `_get_opener()` 里仍会挂上
    `{'all': …, 'no': …}`，“没配代理”的前提根本没成立（2026-10-11 实测踩到）。
    """
    for k in (
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ):
        monkeypatch.delenv(k, raising=False)


#: 替身连接抛的**异常对象**（按请求序号）：判「抛的是不是最后一次」靠**同一性**，不靠措辞。
_FAIL_ERRS: list[OSError] = [OSError(f"替身连接第 {i} 次请求失败") for i in range(8)]


def _install_fake_conns(monkeypatch, *, fail_on: tuple[bool, ...] = ()):
    """把 `http.client` 的两种连接换成替身，返回 `(建连记录, 请求记录)`。

    `fail_on[i]` = 第 i 次 `request` 抛 `_FAIL_ERRS[i]`（模拟坏连接；同一性可判）。
    """
    made: list[tuple[str, int | None]] = []
    reqs: list[tuple[str, str]] = []

    class _FakeConn:
        def __init__(self, host: str, port: int | None = None, **_kw) -> None:
            made.append((host, port))

        def request(self, method: str, target: str, body=None, headers=None) -> None:
            i = len(reqs)
            reqs.append((method, target))
            if i < len(fail_on) and fail_on[i]:
                raise _FAIL_ERRS[i]

        def getresponse(self) -> _Resp:
            return _Resp(200, b'{"ok":1}')

    monkeypatch.setattr(cp.http.client, "HTTPConnection", _FakeConn)
    monkeypatch.setattr(cp.http.client, "HTTPSConnection", _FakeConn)
    return made, reqs


def _clean_pool() -> None:
    cp.reset()


# ───────────────── ① 复用 ─────────────────


def test_repeated_control_calls_reuse_one_connection(monkeypatch) -> None:
    """同一个 host 的两次控制面请求 ⇒ 只建一条连接（这就是 B2 省下的那笔税）。"""
    _clean_pool()
    _no_proxy(monkeypatch)
    made, reqs = _install_fake_conns(monkeypatch)
    url = "http://hub.invalid:500/jobs/peek?n=1"
    for _ in range(2):
        st, body = cp.request(url, method="GET", headers={}, timeout=5.0)
        assert (st, body) == (200, b'{"ok":1}')
    assert len(made) == 1, f"两次请求建了 {len(made)} 条连接（池没生效）：{made}"
    assert len(reqs) == 2, f"请求次数不对：{reqs}"
    assert reqs[0][1] == "/jobs/peek?n=1", "直连用相对路径（代理才要绝对形式）"


def test_a_different_host_gets_its_own_connection(monkeypatch) -> None:
    """池键是 `(scheme, host, port)`：换 host 不复用（别把控制请求发给错的那一端）。"""
    _clean_pool()
    _no_proxy(monkeypatch)
    made, _reqs = _install_fake_conns(monkeypatch)
    cp.request("http://a.invalid:500/jobs/peek", method="GET", headers={}, timeout=1.0)
    cp.request("http://b.invalid:500/jobs/peek", method="GET", headers={}, timeout=1.0)
    assert made == [("a.invalid", 500), ("b.invalid", 500)], f"池键漏了 host：{made}"


# ───────────────── ② 异常即丢弃（池化的另一半）────────────────


def test_broken_connection_is_dropped_and_rebuilt(monkeypatch) -> None:
    """第一次就坏 ⇒ 丢连接重抽一次，第二次成功；池里留的是**新的**那条。"""
    _clean_pool()
    made, reqs = _install_fake_conns(monkeypatch, fail_on=(True, False))
    st, body = cp.request("http://hub.invalid:500/jobs/peek", method="GET", headers={}, timeout=1.0)
    assert (st, body) == (200, b'{"ok":1}')
    assert len(made) == 2, f"坏连接没被丢弃重建：{made}"
    assert len(reqs) == 2
    assert cp.conn_count() == 1, "重抽后池里应只剩那条好的"


def test_exhausted_attempts_raise_and_leave_the_pool_empty(monkeypatch) -> None:
    """两次都坏 ⇒ 抛**最后一个**异常，且池是空的（绝不留坏连接给下一次）。"""
    _clean_pool()
    made, reqs = _install_fake_conns(monkeypatch, fail_on=(True, True))
    with pytest.raises(OSError) as ei:
        cp.request("http://hub.invalid:500/jobs/peek", method="GET", headers={}, timeout=1.0)
    assert ei.value is _FAIL_ERRS[1], f"抛的不是**最后一次**的异常：{ei.value!r}"
    assert len(reqs) == cp.CONTROL_ATTEMPTS, f"尝试次数应等于 CONTROL_ATTEMPTS：{reqs}"
    assert cp.conn_count() == 0, "坏连接留在池里了"


# ───────────────── ③ 代理判据不许分叉 ─────────────────


def test_proxy_env_is_honoured_exactly_like_the_opener(monkeypatch) -> None:
    """★ 池化若绕开代理，非回环的控制请求会直接连不通（Colab userspace 实测需要 ProxyHandler）。"""
    _clean_pool()
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:8080")
    made, reqs = _install_fake_conns(monkeypatch)
    cp.request("http://hub.invalid:500/jobs/peek?n=1", method="GET", headers={}, timeout=1.0)
    assert made == [("proxy.invalid", 8080)], f"没连到代理：{made}"
    assert reqs[0][1] == "http://hub.invalid:500/jobs/peek?n=1", (
        "HTTP 正向代理必须用**绝对形式**的请求行（代理不知道目标主机）"
    )


def _opener_proxies(monkeypatch) -> dict[str, str]:
    """重建一次 `_get_opener()` 并读出它实际装上的代理（**行为**判据，不是源码措辞）。

    ★ 先把 `urllib.request.getproxies` 钉成空：`build_opener()` 在没有显式 `ProxyHandler` 时
    会自己补一个默认的（`ProxyHandler(None)` → `getproxies()`），而它除了 env 还读**平台
    全局设置**（Windows 注册表）——本机实测有 `http(s)://127.0.0.1:7890`，于是“没配代理”
    这个前提在这类机器上永远不成立（清几个 env 名字清不掉它）。本用例验的是**两侧读同一套
    env 名**，所以机器全局代理必须排除在外，否则它测的是这台机器的配置。（残留差异已知：
    平台全局代理只有 urllib 侧认，池侧只认 env——池的契约就是 env 名表。）
    """
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(http_mod, "_opener", None)  # 进程内懒建单例，强制重建
    # `handlers` 是 OpenerDirector 的**运行时**属性（typeshed 里没声明）⇒ getattr 取，
    # 不用 `# type: ignore` 消音（编译器是评审，AGENTS §5）。
    for h in getattr(http_mod._get_opener(), "handlers", ()):
        if isinstance(h, urllib.request.ProxyHandler):
            return dict(getattr(h, "proxies", {}) or {})
    return {}


def test_proxy_env_names_are_the_same_two_lists(monkeypatch) -> None:
    """★ `_proxy_for`（池）与 `http._get_opener`（urllib）必须认**同一套 env 名**——
    分叉 = 一边走代理一边不走（非回环的控制请求会直接连不通）。

    两侧都用**行为**验：池看 `_proxy_for` 的返回值，urllib 看重开出来的 opener 里装的
    `ProxyHandler.proxies`。
    """
    _no_proxy(monkeypatch)
    assert cp._proxy_for("http") == "" and cp._proxy_for("https") == ""
    for scheme in ("http", "https"):
        for suffix in (f"{scheme}_proxy", f"{scheme.upper()}_PROXY"):
            want = f"http://via-{suffix}:1"
            monkeypatch.setenv(suffix, want)
            assert cp._proxy_for(scheme) == want, f"`_proxy_for` 不认 {suffix}"
            assert _opener_proxies(monkeypatch).get(scheme) == want, (
                f"`_get_opener` 不认 {suffix}——两侧口径分叉"
            )
            monkeypatch.delenv(suffix)
    _no_proxy(monkeypatch)
    assert _opener_proxies(monkeypatch) == {}, "没配代理时不该挂 ProxyHandler 条目"


# ───────────────── ④ 分流：`http._request` 的三个条件 ─────────────────


def test_non_loopback_control_call_goes_through_the_pool(monkeypatch) -> None:
    """非回环 + 控制面 ⇒ 走池。"""
    seen: list[str] = []

    def _pool(url: str, **_kw) -> tuple[int, bytes]:
        seen.append(url)
        return 200, b"{}"

    monkeypatch.setattr(http_mod, "_control_pool_request", _pool)
    st, body = http_mod._request("http://hub.invalid:500", "tok", "/jobs/peek?n=1", timeout=5.0)
    assert (st, body) == (200, b"{}")
    assert len(seen) == 1, f"控制面没走池：{seen}"


def test_loopback_control_call_does_not_go_through_the_pool(monkeypatch) -> None:
    """回环（本机 hub）不进池：绕代理是 `net_http.urlopen` 的既有语义，不能改。"""
    seen: list[str] = []

    def _boom(url, **_kw):  # pragma: no cover - 走了就是错
        seen.append(url)
        raise AssertionError("回环不该走连接池")

    monkeypatch.setattr(http_mod, "_control_pool_request", _boom)

    class _FakeSock:
        status = 200

        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(http_mod.net_http, "urlopen", lambda *_a, **_k: _FakeSock())
    st, body = http_mod._request("http://127.0.0.1:500", "tok", "/jobs/peek?n=1", timeout=5.0)
    assert (st, body) == (200, b"{}")
    assert seen == []


def test_bulk_never_goes_through_the_pool(monkeypatch) -> None:
    """★ bulk 永不进池——「换连接 = 重抽一次签」是 bulk 对抗慢链路的唯一手段（§22）。"""
    seen: list[str] = []

    def _boom(url, **_kw):  # pragma: no cover - 走了就是错
        seen.append(url)
        raise AssertionError("bulk 不该走连接池")

    monkeypatch.setattr(http_mod, "_control_pool_request", _boom)

    class _FakeSock:
        status = 200

        def read(self) -> bytes:
            return b"payload"

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    class _FakeOpener:
        def open(self, *_a, **_k):
            return _FakeSock()

    monkeypatch.setattr(http_mod, "_get_opener", lambda: _FakeOpener())
    st, body = http_mod._request("http://hub.invalid:500", "tok", "/jobs/j1/payload", timeout=5.0)
    assert (st, body) == (200, b"payload")
    assert seen == []


# ───────────────── ⑤ B3：短预算 + 立刻换连接重抽（不退避）─────────────────


def test_control_call_is_retried_once_and_bulk_is_not(monkeypatch) -> None:
    """控制面失败 ⇒ 立刻再来一次（换连接）；bulk 失败 ⇒ 只发一次（重抽权在上层）。"""
    calls: list[str] = []

    def _flaky(url, **_kw):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("第一次坏")
        return 200, b"{}"

    monkeypatch.setattr(http_mod, "_control_pool_request", _flaky)
    st, body = http_mod._request("http://hub.invalid:500", "tok", "/jobs/peek?n=1", timeout=5.0)
    assert (st, body) == (200, b"{}")
    assert len(calls) == cp.CONTROL_ATTEMPTS, f"控制面应尝试 {cp.CONTROL_ATTEMPTS} 次：{calls}"

    opens: list[int] = []

    class _DeadOpener:
        def open(self, *_a, **_k):
            opens.append(1)
            raise OSError("链路坏了")

    monkeypatch.setattr(http_mod, "_get_opener", lambda: _DeadOpener())
    try:
        http_mod._request("http://hub.invalid:500", "tok", "/jobs/j1/payload", timeout=5.0)
    except OSError:
        pass
    assert len(opens) == 1, f"bulk 不该在传输层自重试（重抽是 `_get_with_retry` 的事）：{opens}"


def test_control_timeouts_share_one_short_budget() -> None:
    """控制面端点共用 `CONTROL_TIMEOUT_SEC`（改一处改全部），且预算对心跳是安全的。"""
    for fn in (jl.peek_jobs, jl.job_status, jl.start_cancel_watcher, jl.request_priority):
        d = inspect.signature(fn).parameters["timeout"].default
        assert d == CONTROL_TIMEOUT_SEC, f"{fn.__name__} 的 timeout 缺省不是控制面预算：{d}"


def test_the_calls_without_a_timeout_parameter_use_the_same_budget(monkeypatch) -> None:
    """`heartbeat` / `release_job` / `report_job_failure` 没有 `timeout` 形参 ⇒ 只能**调**它们
    看 `_request` 拿到了什么（散成字面量就会各自漂，这条把它们钉回同一个常量）。"""
    seen: list[float] = []

    def _fake_request(*_a, **kw):
        seen.append(float(kw["timeout"]))
        return (200, b"{}")

    monkeypatch.setattr(jl, "_request", _fake_request)
    jl.heartbeat("http://hub.invalid:500", "tok", "j1")
    jl.release_job("http://hub.invalid:500", "tok", "j1")
    jl.report_job_failure("http://hub.invalid:500", "tok", "j1", "boom", log=lambda _m: None)
    assert seen == [CONTROL_TIMEOUT_SEC] * 3, f"三条腿的预算不一致：{seen}"
    # 安全性：2 次 6s 全败之后，60s 心跳周期内还剩得下重试；300s 租约更不用说。
    assert CONTROL_TIMEOUT_SEC * cp.CONTROL_ATTEMPTS < HEARTBEAT_SEC, (
        f"控制面预算 {CONTROL_TIMEOUT_SEC}s × {cp.CONTROL_ATTEMPTS} 已吞掉整个心跳周期"
        f"（{HEARTBEAT_SEC}s）——续租会被自己饿死"
    )


def test_a_failed_priority_ask_retried_and_says_how_many_times(monkeypatch) -> None:
    """★ 走到「不可达」时必须是**重试过**（换连接）：旧文案只写「失败」，会把「6s×2」
    读成「一次没接上」，进而误判要不要去和别人抢同一份活。"""
    attempts: list[int] = []

    def _dead(*_a, **_k):
        attempts.append(1)
        raise OSError("超时")

    monkeypatch.setattr(jl, "_request", _dead)
    logs: list[str] = []
    out = jl.request_priority("http://hub.invalid:500", "tok", log=logs.append)
    assert out == {"epoch": None, "priorities": {}, "reasons": {}}
    # ★ 这一层只调**一次**：重抽住在传输层（`http._request`，由
    #   `test_control_call_is_retried_once_and_bulk_is_not` 钉住次数）。这里再套一层循环
    #   就是两层相乘（2×2=4 次 × 6s），预算与「把 30s 切成可见失败」的账都失效。
    assert len(attempts) == 1, f"重抽不该在 job_lifecycle 层再来一遍：{attempts}"
    joined = "\n".join(logs)
    assert f"已重试 {cp.CONTROL_ATTEMPTS} 次" in joined, f"没写明重试次数：{logs}"


def test_the_pool_module_carries_no_repo_dependency() -> None:
    """依赖方向：`control_pool → {stdlib}`（判据 `control_path` 与装配都在外面）。"""
    from tests.helpers import remote_dag as dag

    dag.assert_remote_module("remote.control_pool")
    assert dag.LAYERS["remote.control_pool"] == 0, "它应当是零仓内依赖的叶子"
