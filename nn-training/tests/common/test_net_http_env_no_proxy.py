"""test_net_http_env_no_proxy — `NO_PROXY` 里点名的网段必须绕开环境代理（2026-11-14）。

**起因**：`urllib.request.proxy_bypass()` 在 Windows 读注册表、**不读 env**，于是本机
`NO_PROXY=…,192.168.0.0/16`（LAN 的三台采样节点）被判成「该走代理」。实测后果：同一个
a97 `/v1/status` 经代理回 `Connection: close`（3 请求 0.282s），直连无该头（0.109s）
⇒ 长连接池对这三台完全失效，而 agent 侧改任何 header 都救不了（见 plan §2.1）。

**口径**：判据是 operator 写下的字面（`*` / 精确 host / `.suffix` / IPv4 CIDR），不是硬编码
私网段——这样 §149「不做 LAN 例外」不被悄悄改掉，换台机器只改 `NO_PROXY`。
"""

from __future__ import annotations

import pytest

from common import net_http


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    # 本机真实 env 带 192.168.0.0/16 —— 每个用例都自己声明，绝不泄漏到别人身上。
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)


def test_host_of_normalizes() -> None:
    assert net_http.host_of("http://192.168.0.97:8443/v1/status") == "192.168.0.97"
    assert net_http.host_of("192.168.0.97:8443") == "192.168.0.97"
    assert net_http.host_of("[::1]:8900") == "::1"
    assert net_http.host_of("Example.COM.") == "example.com"
    assert net_http.host_of("") == ""


def test_star_bypasses_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "*")
    assert net_http.env_no_proxy("http://8.8.8.8:8443")


def test_exact_host_and_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,192.168.0.97,.corp.local,*.box.local")
    assert net_http.env_no_proxy("192.168.0.97")
    assert net_http.env_no_proxy("192.168.0.97:8443")
    assert net_http.env_no_proxy("http://host.corp.local:8443/x")
    assert net_http.env_no_proxy("http://a.box.local/")
    # 前导点/`*.` = 域后缀，**含裸域本身**（与 curl 的 `example.com,sub.example.com` 习惯一致；
    # 不是 `endswith` 字面量那套——那会让 `NO_PROXY=.corp.local` 漏掉 `corp.local`）。
    assert net_http.env_no_proxy("corp.local")
    assert not net_http.env_no_proxy("192.168.0.98")
    assert not net_http.env_no_proxy("notcorp.local")


def test_cidr_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,169.254.0.0/16,192.168.0.0/16")
    assert net_http.env_no_proxy("http://192.168.0.97:8443/v1/status")
    assert net_http.env_no_proxy("169.254.1.2")
    assert not net_http.env_no_proxy("10.1.2.3")
    assert not net_http.env_no_proxy("https://example.trycloudflare.com")


def test_lowercase_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("no_proxy", "192.168.0.0/16")
    assert net_http.env_no_proxy("192.168.0.96")


def test_empty_env_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "  ")
    assert not net_http.env_no_proxy("192.168.0.97")


def test_bypass_proxy_keeps_loopback() -> None:
    """§149 的既有语义不许丢：回环永远绕开代理（与 env 无关）。"""
    assert net_http.bypass_proxy("http://127.0.0.1:8443/v1/ping")
    assert net_http.bypass_proxy("http://localhost:8900")
    assert not net_http.bypass_proxy("https://example.trycloudflare.com")
