"""D9 闭锁语义（2026-09-17 两次收紧）：

  A. **先验 token**：封禁只拒**无效**鉴权尝试，合法 token 永远放行（改序）；
  B. **回环永不封禁**：`127.0.0.0/8` / `::1` / `::ffff:127.0.0.1` 上的失败**不计数、不封禁**
     （用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）。

事故链（现场日志 `tmp/x1-rebirth/hub-server.out`）——A 与 B 都是它的解药：

  1. `10:10:33` 本机组件用**不匹配**的 token 连打 5 次 `GET /ping` → 401 累计满 5；
  2. 旧 `_auth_ok` 先查 `is_blocked` 再验 token ⇒ 之后**连带正确 token 的**请求也全部 403
     （封禁连坐：console 健康检查、训练循环、worker 拉活一起被封）——A 修；
  3. 封禁只住**进程内存**，唯一解药是重启；而旧实例还活着占着端口 ⇒ 新实例被
     `_port_guard.ensure_port_free` 拒绝 ⇒ 「必须重启才能解封，重启却被自己占的端口挡死」
     = 死锁，只能人工杀进程——B 从源头消掉「回环被连坐」这一类（cloudflared 回源把隧道
     流量也全归成 127.0.0.1，它的失败同样永不封禁）。

测试分层：回环分支只能走**真 HTTP**（`_boot_server` 绑 127.0.0.1）；非回环分支走
**store 直调 + `_auth_ok` 桩**（`HubHandler.__new__` 不跑 BaseHTTPRequestHandler.__init__，
只注入 handler 真正读的字段），这样两种来源的语义都被真代码路径覆盖。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

# 复用现有测试基建（随机端口的真 hub-server）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from test_remote_ppo import (  # type: ignore
    _boot_server,
    _http,
    _mini_manifest,
    normalize_manifest,
)

from remote.hub_server import HubHandler, _is_loopback  # isort: skip

BAD = "wrong-token"
REMOTE = "203.0.113.7"  # TEST-NET-3：非回环、永不会真的出现


def _ping(base: str, token: str) -> int:
    st, _ = _http(base, token, "/ping")
    return int(st)


# ────────────────────────── _is_loopback 判定 ──────────────────────────


def test_is_loopback_variants() -> None:
    for ip in ("127.0.0.1", "127.5.5.5", "::1", "::ffff:127.0.0.1", "::FFFF:127.0.0.1", "localhost"):
        assert _is_loopback(ip) is True, ip
    for ip in ("100.124.208.62", "10.0.0.1", "0.0.0.0", "", "203.0.113.7", "::2", "1270.0.0.1"):
        assert _is_loopback(ip) is False, ip


# ────────────────────────── 回环分支（真 HTTP，B） ──────────────────────────


def test_loopback_failures_never_count_or_block(tmp_path: Path) -> None:
    """回环上无效鉴权照常 401，但**不计数、不封禁**（12 次远超 5 次阈值）。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        for i in range(12):
            assert _ping(base, BAD) == 401, f"回环第 {i + 1} 次失败应仍是 401（永不 403）"
        assert store.is_blocked("127.0.0.1") is False
        assert store._auth_fail == {}, "回环失败不得计数"
        assert store._auth_blocked_until == {}, "回环失败不得产生封禁记录"
    finally:
        srv.shutdown()
        th.join()


def test_loopback_valid_token_always_200_and_business_alive(tmp_path: Path) -> None:
    """事故现场回归：回环上刷了一堆失败后，合法 token 的 console 健康检查与业务面照常。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        manifest = normalize_manifest(_mini_manifest())
        jid = manifest["job_id"]
        store.publish(jid, manifest, b"PK\x03\x04fake")

        for _ in range(6):
            _http(base, BAD, "/ping")

        assert _ping(base, "sekret") == 200, "console 健康检查（回环 + 合法 token）必须 200"
        st, body = _http(base, "sekret", "/jobs/next")
        assert st == 200 and body["job_id"] == jid, f"worker 拉活必须 200: {st} {body!r}"
        st, _ = _http(base, "sekret", f"/jobs/{jid}/status")
        assert st == 200, "训练主循环状态查询必须 200"
    finally:
        srv.shutdown()
        th.join()


def test_token_still_required_on_loopback(tmp_path: Path) -> None:
    """回环豁免的是**封禁**，不是鉴权：无/坏 token 永远拿不到 200。"""
    base, _store, srv, th = _boot_server(tmp_path)
    try:
        for _ in range(6):
            assert _ping(base, "") == 401, "缺 token 绝不放行"
            assert _ping(base, BAD) == 401, "坏 token 绝不放行"
    finally:
        srv.shutdown()
        th.join()


# ────────────────────────── 非回环分支（store 直调） ──────────────────────────


def test_remote_ip_bans_after_five_invalid_attempts(tmp_path: Path) -> None:
    store = _boot_server(tmp_path)[1]
    for i in range(1, 6):
        store.auth_failure(REMOTE)
        if i < 5:
            assert store.is_blocked(REMOTE) is False, f"第 {i} 次不应封禁"
    assert store.is_blocked(REMOTE) is True, "第 5 次无效鉴权应封禁"
    assert store.blocked_remaining(REMOTE) > 3500


def test_remote_ip_valid_auth_resets_counter(tmp_path: Path) -> None:
    """合法鉴权清零计数：与合法组件共用来源 IP 的坏客户端不会慢性累积到封禁。"""
    store = _boot_server(tmp_path)[1]
    for _ in range(4):
        store.auth_failure(REMOTE)
    assert store._auth_fail.get(REMOTE) == 4
    store.auth_success(REMOTE)
    assert store._auth_fail == {}, "合法鉴权后计数应清零"
    assert store.auth_failure(REMOTE) == 1, "清零后重新从 1 计"
    assert store.is_blocked(REMOTE) is False


def test_loopback_is_blocked_guard_defensive(tmp_path: Path) -> None:
    """即便内存态里混进了回环封禁记录（旧版本残留/手工注入），也不生效。"""
    store = _boot_server(tmp_path)[1]
    store._auth_blocked_until["127.0.0.1"] = time.time() + 3600
    assert store.is_blocked("127.0.0.1") is False, "回环永不被封（防御性）"


# ────────────────────────── handler 顺序（桩，非回环，A） ──────────────────────────


class _Stub:
    """最小 handler 桩：只提供 `_auth_ok` 真正读的字段（不跑 BaseHTTPRequestHandler.__init__，
    也就不会真的发包）。逻辑零复制——断言的是 `HubHandler._auth_ok` 本体。"""

    def __init__(self, store: Any, token: str, ip: str, auth: str) -> None:
        self.store = store
        self.client_address = (ip, 41234)
        self.headers = {"Authorization": auth}
        self.path = "/ping"
        self.server = SimpleNamespace(token=token)
        self.sent: list[tuple[int, object]] = []

    def _json(self, obj: object, status: int = 200) -> None:
        self.sent.append((status, obj))

    def _log_blocked(self, _ip: str) -> None:  # 节流审计行：测试里无需打印
        return None


def _stub_handler(store: Any, token: str, ip: str, auth: str) -> _Stub:
    return _Stub(store, token, ip, auth)


def _auth_ok(stub: _Stub) -> bool:
    """调**真** `HubHandler._auth_ok`（桩只充当字段容器）。"""
    return HubHandler._auth_ok(cast(HubHandler, stub))


def test_valid_token_allowed_while_remote_ip_blocked(tmp_path: Path) -> None:
    """A 的核心断言：封禁期内**合法 token 必须放行**（修复前 = 403）。"""
    store = _boot_server(tmp_path)[1]
    for _ in range(5):
        store.auth_failure(REMOTE)
    assert store.is_blocked(REMOTE) is True

    h = _stub_handler(store, "sekret", REMOTE, "Bearer sekret")
    assert _auth_ok(h) is True, "封禁只拒无效尝试，合法 token 必须放行"
    assert h.sent == [], "合法请求不得收到 403"


def test_blocked_remote_ip_invalid_token_gets_403(tmp_path: Path) -> None:
    store = _boot_server(tmp_path)[1]
    for _ in range(5):
        store.auth_failure(REMOTE)
    h = _stub_handler(store, "sekret", REMOTE, f"Bearer {BAD}")
    assert _auth_ok(h) is False
    assert h.sent == [(403, {"error": "ip blocked"})]
    assert store._auth_fail == {}, "封禁期内的无效尝试不得再累计"


def test_invalid_attempts_end_in_ban_on_sixth(tmp_path: Path) -> None:
    """未封禁 → 前 5 次 401（第 5 次触发封禁），第 6 次起 403。"""
    store = _boot_server(tmp_path)[1]
    for i in range(1, 6):
        h = _stub_handler(store, "sekret", REMOTE, f"Bearer {BAD}")
        assert _auth_ok(h) is False
        assert h.sent == [(401, {"error": "unauthorized"})], f"第 {i} 次应 401"
    h = _stub_handler(store, "sekret", REMOTE, f"Bearer {BAD}")
    _auth_ok(h)
    assert h.sent == [(403, {"error": "ip blocked"})], "第 6 次应 403（封禁已生效）"


def test_loopback_never_reaches_ban_through_handler(tmp_path: Path) -> None:
    """回环走 handler 也永不 403、永不产生封禁记录（B 的 handler 侧断言）。"""
    store = _boot_server(tmp_path)[1]
    for _ in range(8):
        h = _stub_handler(store, "sekret", "127.0.0.1", f"Bearer {BAD}")
        assert _auth_ok(h) is False
        assert h.sent == [(401, {"error": "unauthorized"})]
    assert store._auth_blocked_until == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
