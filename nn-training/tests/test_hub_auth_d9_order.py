"""D9 闭锁语义（2026-09-17 两次收紧）：

  A. **先验 token**：封禁只拒**无效**鉴权尝试，合法 token 永远放行（改序）；
  B. **回环永不封禁**：`127.0.0.0/8` / `::1` / `::ffff:127.0.0.1` 上的失败**不计数、不封禁**
     （用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）；
  C. **隧道来源还原**（2026-09-17 第三批）：回环对端 + `CF-Connecting-IP`（合法、非回环）
     ⇒ 归因给该 IP 计数/封禁——把 B 带来的「隧道入口无封禁」代价补回来。

  注意 A/B/C 是一条链：A 让「惩罚无效鉴权」不再连坐合法流量（封禁面从整个来源 IP 缩到
  「无效尝试」）⇒ B 才敢把回环整段豁免 ⇒ C 才敢把归因从「TCP 对端」换成「真实客户端」。

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
    _next,
    normalize_manifest,
)

from remote.hub_server import (  # isort: skip
    CF_SOURCE_HEADER,
    HubHandler,
    _is_loopback,
    as_hub,
    attributed_source,
)

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
        body = _next(base)
        assert body["job_id"] == jid, f"worker 拉活必须成功: {body!r}"
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

    def __init__(self, store: Any, token: str, ip: str, auth: str, cf: str | None = None) -> None:
        # handler 读的字段 2026-09-18 从 `store` 改名为 `hub`（多课程调度面）。
        # 这里包成**单课程队列**（`as_hub`）而不是直接塞 store：本文件要靠「在 store 上
        # 预热失败计数/封禁态，再让真 `_auth_ok` 读到同一份状态」来断言——单课程队列
        # 的鉴权面正是借那一份 store 的，所以语义与改造前完全一致。
        self.hub = as_hub(store)
        self.client_address = (ip, 41234)
        self.headers = {"Authorization": auth}
        if cf is not None:
            self.headers[CF_SOURCE_HEADER] = cf
        self.path = "/ping"
        self.server = SimpleNamespace(token=token)
        self.sent: list[tuple[int, object]] = []

    def _json(self, obj: object, status: int = 200) -> None:
        self.sent.append((status, obj))

    def _log_blocked(self, _ip: str, peer: str = "", via: str = "peer") -> None:
        """节流审计行：测试里无需打印（签名需与真实现同形——`_auth_ok` 带 kwargs 调用）。"""
        return None


def _stub_handler(store: Any, token: str, ip: str, auth: str, cf: str | None = None) -> _Stub:
    return _Stub(store, token, ip, auth, cf)


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


# ────────────────────────── 归因来源（B：隧道入口来源还原） ──────────────────────────
#
# 背景：cloudflared 回源把隧道流量全归成 127.0.0.1，而回环永不封禁 ⇒ 隧道入口此前**无任何
# 计数/封禁**（改序后的必然代价）。B 方案：**只在「对端是回环」时**采信边缘注入的
# `CF-Connecting-IP`，把它当作归因来源；直连（tailnet）对端能自己写头，故只认 TCP 对端。
# 论证与「头可伪造时也不会更差」的代价分析见 `hub_server.attributed_source` 的 docstring。


def test_attributed_source_matrix() -> None:
    """归因规则矩阵（隧道 / 直连 / 伪造 / 垃圾值）。"""
    # 隧道：回环对端 + 合法公网 IPv4/IPv6 头 → 归因给该 IP
    assert attributed_source("127.0.0.1", "203.0.113.7") == ("203.0.113.7", "cf")
    assert attributed_source("::1", "2001:db8::1") == ("2001:db8::1", "cf")
    assert attributed_source("::ffff:127.0.0.1", " 203.0.113.7 ") == ("203.0.113.7", "cf")
    # 回环对端但没有头 / 头是垃圾 / 头写的还是回环值 → 回退对端（= 本机组件，仍豁免）
    assert attributed_source("127.0.0.1", "") == ("127.0.0.1", "peer")
    assert attributed_source("127.0.0.1", "not-an-ip") == ("127.0.0.1", "peer")
    assert attributed_source("127.0.0.1", "127.0.0.1") == ("127.0.0.1", "peer")
    assert attributed_source("127.0.0.1", "203.0.113.7, 10.0.0.1") == ("127.0.0.1", "peer")
    # 直连（tailnet / 公网）：**一律用 TCP 对端**，对端自称的头不予采信
    assert attributed_source("100.124.208.62", "203.0.113.7") == ("100.124.208.62", "peer")
    assert attributed_source("198.51.100.9", "") == ("198.51.100.9", "peer")


def test_access_log_carries_attributed_source(capsys: Any, tmp_path: Path) -> None:
    """访问日志（`log_message`）在隧道流量上必须补出真实来源。

    它才是 x1-rebirth 事故里最大的阻雾：回源流量全写成 `[hub-server 127.0.0.1]`，
    排障时分不清「本机组件」与「隧道里的陌生人」。"""
    store = _boot_server(tmp_path)[1]
    line = '"GET /code HTTP/1.1" 200 -'

    # 隧道：回环对端 + CF 头 → 补 src=/via=cf
    HubHandler.log_message(  # type: ignore[arg-type]
        cast(HubHandler, _stub_handler(store, "sekret", "127.0.0.1", "Bearer sekret", cf="203.0.113.7")),
        line,
    )
    out = capsys.readouterr().out
    assert "127.0.0.1 src=203.0.113.7 via=cf" in out, out

    # 本机组件（回环、无头）→ 不加噪（与旧格式逐字一致）
    HubHandler.log_message(  # type: ignore[arg-type]
        cast(HubHandler, _stub_handler(store, "sekret", "127.0.0.1", "Bearer sekret")), line
    )
    out = capsys.readouterr().out
    assert "[hub-server 127.0.0.1]" in out and "via=cf" not in out, out

    # 直连对端自带头 → 不采信（只写对端）
    HubHandler.log_message(  # type: ignore[arg-type]
        cast(
            HubHandler,
            _stub_handler(store, "sekret", "100.124.208.62", "Bearer sekret", cf="203.0.113.7"),
        ),
        line,
    )
    out = capsys.readouterr().out
    assert "[hub-server 100.124.208.62]" in out and "via=cf" not in out, out


def test_access_log_survives_missing_headers(capsys: Any, tmp_path: Path) -> None:
    """早期错误路径（请求行都解析失败）时 `self.headers is None`：日志不得再抛一次异常。"""
    store = _boot_server(tmp_path)[1]
    h = _stub_handler(store, "sekret", "127.0.0.1", "Bearer sekret")
    h.headers = None  # type: ignore[assignment]
    HubHandler.log_message(cast(HubHandler, h), '"GET /bad HTTP/1.1" 400 -')  # type: ignore[arg-type]
    assert "[hub-server 127.0.0.1]" in capsys.readouterr().out


def test_tunnel_source_gets_counted_and_banned(tmp_path: Path) -> None:
    """B 的核心：隧道（回环对端 + CF 头）连续 5 次无效 → **按归因 IP 封禁**，第 6 次 403。

    修复前：归因只认对端 ⇒ 回环被豁免 ⇒ 隧道入口永不计数（本用例红）。"""
    store = _boot_server(tmp_path)[1]
    for i in range(1, 6):
        h = _stub_handler(store, "sekret", "127.0.0.1", f"Bearer {BAD}", cf="203.0.113.7")
        assert _auth_ok(h) is False
        assert h.sent == [(401, {"error": "unauthorized"})], f"第 {i} 次应 401"
    assert store.is_blocked("203.0.113.7") is True, "隧道来源应被封（按归因 IP）"
    assert store.is_blocked("127.0.0.1") is False, "回环本身永不被封"

    h = _stub_handler(store, "sekret", "127.0.0.1", f"Bearer {BAD}", cf="203.0.113.7")
    _auth_ok(h)
    assert h.sent == [(403, {"error": "ip blocked"})], "第 6 次应 403"


def test_tunnel_valid_token_still_passes_while_attributed_banned(tmp_path: Path) -> None:
    """封禁只拒无效尝试：同一隧道来源的**合法 token**（Kaggle worker）照常 200。"""
    store = _boot_server(tmp_path)[1]
    for _ in range(5):
        _auth_ok(_stub_handler(store, "sekret", "127.0.0.1", f"Bearer {BAD}", cf="203.0.113.7"))
    assert store.is_blocked("203.0.113.7") is True

    h = _stub_handler(store, "sekret", "127.0.0.1", "Bearer sekret", cf="203.0.113.7")
    assert _auth_ok(h) is True
    assert h.sent == []



def test_local_component_without_cf_header_still_exempt(tmp_path: Path) -> None:
    """回归护栏：本机组件（回环、无 CF 头）照旧不计数、不封禁 —— 2026-09-17 用户口径。"""
    store = _boot_server(tmp_path)[1]
    for _ in range(12):
        h = _stub_handler(store, "sekret", "127.0.0.1", f"Bearer {BAD}")
        assert _auth_ok(h) is False
        assert h.sent == [(401, {"error": "unauthorized"})]
    assert store._auth_blocked_until == {}


def test_direct_peer_cannot_declare_itself(tmp_path: Path) -> None:
    """直连对端自带 CF 头不算数：归因=对端自己，5 次即封它自己（不得被头钓走）。"""
    store = _boot_server(tmp_path)[1]
    for _ in range(5):
        _auth_ok(
            _stub_handler(
                store, "sekret", "100.124.208.62", f"Bearer {BAD}", cf="203.0.113.7"
            )
        )
    assert store.is_blocked("100.124.208.62") is True
    assert store.is_blocked("203.0.113.7") is False, "直连声明的头不得把别人封掉"


def test_forged_loopback_header_is_harmless(tmp_path: Path) -> None:
    """伪造头（假设不成立时的最坏情形）**不会更差**：

      * 伪造回环值 ⇒ 归因回退对端（回环）⇒ 免封（= 改假设前的现状）；
      * 伪造别人的 IP ⇒ 只拒那个 IP 的**无效**尝试，带合法 token 的请求（如 tailnet
        上的真 worker）照常放行 ⇒ 不构成对合法对端的 DoS。"""
    store = _boot_server(tmp_path)[1]
    for _ in range(6):
        h = _stub_handler(store, "sekret", "127.0.0.1", f"Bearer {BAD}", cf="127.0.0.1")
        assert _auth_ok(h) is False
        assert h.sent == [(401, {"error": "unauthorized"})]
    assert store._auth_blocked_until == {}, "伪造回环值不得产生任何封禁"

    # 受害者 = tailnet 上的真 worker：被人伪造其 IP 打了 5 次无效，仍持合法 token 照常放行
    victim = "100.124.208.62"
    for _ in range(5):
        _auth_ok(_stub_handler(store, "sekret", "127.0.0.1", f"Bearer {BAD}", cf=victim))
    assert store.is_blocked(victim) is True
    ok_h = _stub_handler(store, "sekret", victim, "Bearer sekret")
    assert _auth_ok(ok_h) is True, "被封 IP 的合法 token 必须放行（先验 token 的改序使然）"
    assert ok_h.sent == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
