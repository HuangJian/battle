"""tests/remote/test_rollout_node.py —— 采样节点运行时（plan/rollout-node-auto-register v2）。

覆盖 S2 的纯函数面 + 注册 HTTP 面（注入 urlopen，不碰真网络）：
`resolve_link` 默认表 · `registry_url` 与 `resolve_hub_url` 单一实现 · 载荷缺省不写
`concurrency`（F2）· 去重 · 错误分类 · register/unregister 三态。
"""

from __future__ import annotations

import json
import urllib.error
from email.message import Message
from io import BytesIO

import pytest

from remote import rollout_node as rn
from remote import tailscale_boot

# ---------------------------------------------------------------- resolve_link

@pytest.mark.parametrize(
    ("env", "cfg_link", "has_key", "want"),
    [
        ("colab", "auto", False, "tailscale"),  # Colab 优先（用户口径），没有 key 也让引导去响
        ("colab", "", False, "tailscale"),
        ("colab", "cloudflared", True, "cloudflared"),  # 强压
        ("kaggle", "auto", True, "tailscale"),
        ("kaggle", "auto", False, "cloudflared"),
        ("aistudio", "", False, "cloudflared"),
        ("local", "AUTO", False, "cloudflared"),
        ("kaggle", "tailscale", False, "tailscale"),  # 强压（引导失败后由 run 回落）
    ],
)
def test_resolve_link_table(env: str, cfg_link: str, has_key: bool, want: str) -> None:
    assert rn.resolve_link(env, cfg_link, has_key) == want


# ---------------------------------------------------------------- registry_url

def test_registry_url_is_the_single_address_parser() -> None:
    """转调 `tailscale_boot.resolve_hub_url`（F13：不再抄第二份「逐字一致」）。"""
    cases = [
        ("100.64.0.5", 0, ""),
        ("100.64.0.5:9999", 0, ""),
        ("", 8787, "http://hub.tailnet.ts.net:8787"),
        ("", 8787, "http://<TS_IP>:8787"),  # 模板值 = 未填
        ("100.1.2.3", 8787, ""),
        ("", 0, ""),
    ]
    for hub_ip, hub_port, cfg_url in cases:
        assert rn.registry_url(hub_ip, hub_port, cfg_url) == tailscale_boot.resolve_hub_url(
            cfg_hub_url=cfg_url, hub_ip=hub_ip, hub_port=hub_port
        )
    # 具体口径锚点（裸 IP + 默认端口 / 整条 URL 原样）
    assert rn.registry_url("100.64.0.5", 0, "") == "http://100.64.0.5:8787"
    assert rn.registry_url("", 0, "https://hub.example.ts.net:8443/") == "https://hub.example.ts.net:8443"


# ---------------------------------------------------------------- payload / 去重

def test_payload_omits_concurrency_by_default() -> None:
    """F2：缺省**不写** `concurrency` 键（派发读 `or ping.cpus`；写 1 = 静默单核化）。"""
    p = rn.build_register_payload("rollout-colab", "http://100.64.0.5:8443", "k")
    assert p == {
        "id": "rollout-colab",
        "url": "http://100.64.0.5:8443",
        "authKey": "k",
        "managed": True,
    }
    assert set(p) == {"id", "url", "authKey", "managed"}


def test_payload_includes_self_reported_slots_and_label() -> None:
    p = rn.build_register_payload("n", "http://x:1", "k", workers=94, label="colab-t")
    assert p["concurrency"] == 94
    assert p["label"] == "colab-t"
    # 0 / 负数 / 垃圾 = 不写（不额外限流）
    for bad in (0, -3, "x"):
        assert set(rn.build_register_payload("n", "http://x:1", "k", workers=bad)) == {
            "id",
            "url",
            "authKey",
            "managed",
        }


def test_should_register_only_on_change() -> None:
    assert rn.should_register(None, "http://a", "k1") is True
    st = {"url": "http://a", "authKey": "k1"}
    assert rn.should_register(st, "http://a", "k1") is False
    assert rn.should_register(st, "http://b", "k1") is True
    assert rn.should_register(st, "http://a", "k2") is True


# ---------------------------------------------------------------- classify_error

def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://h", code, "err", Message(), BytesIO(b"{}"))


def test_classify_error() -> None:
    assert rn.classify_error(_http_error(401)) == "auth"
    assert rn.classify_error(_http_error(403)) == "auth"
    assert rn.classify_error(_http_error(422)) == "server"
    assert rn.classify_error(_http_error(500)) == "server"
    assert rn.classify_error(urllib.error.URLError("boom")) == "net"
    assert rn.classify_error(TimeoutError()) == "net"
    assert rn.classify_error(ValueError("bad")) == "unknown"


# ---------------------------------------------------------------- register / unregister

class _Resp:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._b = json.dumps(body).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._b

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _opener(resp: _Resp | None = None, exc: BaseException | None = None, calls: list | None = None):
    def _open(req, timeout=None):
        if calls is not None:
            calls.append(req)
        if exc is not None:
            raise exc
        return resp

    return _open


def test_register_success_posts_to_the_admin_endpoint() -> None:
    calls: list = []
    out = rn.register(
        _opener(_Resp({"ok": True, "action": "created"}), calls=calls),
        "http://hub:8787",
        "tok",
        {"id": "n"},
        lambda m: None,
    )
    assert out == {"ok": True, "status": 200, "action": "created", "error": None, "kind": "ok"}
    assert len(calls) == 1
    req = calls[0]
    assert req.full_url == "http://hub:8787/admin/nodes/register"
    assert req.get_header("Authorization") == "Bearer tok"


def test_register_failure_is_classified_never_raised() -> None:
    out = rn.register(_opener(exc=_http_error(401)), "http://h", "tok", {}, lambda m: None)
    assert out["ok"] is False and out["kind"] == "auth" and out["status"] == 401

    out = rn.register(_opener(exc=_http_error(422)), "http://h", "tok", {}, lambda m: None)
    assert out["ok"] is False and out["kind"] == "server" and out["status"] == 422

    out = rn.register(_opener(exc=urllib.error.URLError("down")), "http://h", "tok", {}, lambda m: None)
    assert out["ok"] is False and out["kind"] == "net" and out["status"] == 0


def test_register_without_hub_or_token_is_a_clean_refusal() -> None:
    out = rn.register(_opener(_Resp({})), "", "", {}, lambda m: None)
    assert out["ok"] is False and out["kind"] == "net"


def test_unregister_posts_id_and_reports_status() -> None:
    calls: list = []
    out = rn.unregister(
        _opener(_Resp({"ok": True, "action": "unregistered"}), calls=calls),
        "http://hub:8787",
        "tok",
        "rollout-colab",
        lambda m: None,
    )
    assert out["ok"] is True and out["action"] == "unregistered"
    assert calls[0].full_url == "http://hub:8787/admin/nodes/unregister"
    assert json.loads(calls[0].data.decode("utf-8")) == {"id": "rollout-colab"}

    out = rn.unregister(_opener(exc=_http_error(404)), "http://h", "tok", "n", lambda m: None)
    assert out["ok"] is False and out["status"] == 404
