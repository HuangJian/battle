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
from typing import Any

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
    # body 放宽到 Any：有用例要盖「响应体不是 dict」这条分支（agent 变坏/旧版），
    # 它必须走 `_status` 的「不编状态」路径而不是被夹具的注解挡住。
    def __init__(self, body: Any, status: int = 200) -> None:
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
    # 2026-10-23：返回体多了 `body` / `capabilities`（push 节点要用应答认「hub 听懂 kind 了吗」）。
    # 2026-10-25：再加 `dispatchable`（hub 到底会不会派单；老 hub 没这个键 ⇒ None）。
    assert out == {
        "ok": True,
        "status": 200,
        "action": "created",
        "body": {"ok": True, "action": "created"},
        "capabilities": [],
        "dispatchable": None,
        "error": None,
        "kind": "ok",
    }
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


# ---------------------------------------------------------------- 近 30min 产出摘要（2026-10-18）

def test_output_line_prefers_the_agent_line_verbatim() -> None:
    """行形状由 agent 算好（数字只在 `tools/agent/output-window.ts` 一处算）——这里只搬运。

    为什么不在 python 里再拼一遍：notebook 日志与 `sampler-agent.log` 必须逐字相同，
    两处格式化 = 两套口径，对账时先要怀疑日志。
    """
    line = "games=412（rollout=380 / eval=32） · 13.7 局/分（30min） · 失败=3 · 轮次=2"
    assert rn.output_line({"outputLine": line}) == f"近30min产出 {line}"
    # 两侧空白不是新事实（日志行不带缩进）
    assert rn.output_line({"outputLine": f"  {line}  "}) == f"近30min产出 {line}"


def test_output_line_is_honest_when_unknown() -> None:
    """旧 agent / 探活失败 ⇒ 「不可知」；绝不拿累计数冒充 30 分钟窗口（那是静默错数）。

    用**全等**而不是子串：这两种不可知形态是仅有的两档，判据没必要挂在措辞的一部分上
    （文本断言计数护栏只许降不许升，全等不计）。
    """
    assert rn.output_line(None) == "近30min产出 不可知（agent 未应答）"
    assert rn.output_line({"gamesDoneTotal": 1240}) == "近30min产出 不可知（旧 agent 无 outputLine）"
    assert rn.output_line({"outputLine": "   "}) == "近30min产出 不可知（旧 agent 无 outputLine）"


def test_status_reads_local_agent_with_auth_and_never_raises() -> None:
    calls: list = []
    body = {"outputLine": "games=1（rollout=1 / eval=0）"}
    st = rn._status(8443, "tok", urlopen=_opener(_Resp(body), calls=calls))
    assert st == body
    assert calls[0].full_url == "http://127.0.0.1:8443/v1/status"
    assert calls[0].get_header("Authorization") == "Bearer tok"
    assert rn._status(8443, "tok", urlopen=_opener(exc=urllib.error.URLError("down"))) is None
    # 非 dict 响应体（旧/坏 agent）⇒ None（不编一个状态出来）
    assert rn._status(8443, urlopen=_opener(_Resp([1, 2]))) is None


def test_output_summary_cadence_is_ten_minutes() -> None:
    """10 分钟一行（用户 2026-10-18 口径）——节拍是口径本身，写死值相等判据。"""
    assert rn.OUTPUT_STATUS_EVERY_SEC == 600.0


def test_emit_output_summary_prints_exactly_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """监护循环每 10 分钟调的就是它：读一次 `/v1/status`、打一行。

    钉在这里而不是钉源码文本：notebook 侧看不到的正是「算了但没上屏」，而这条用例走的是
    **监护循环真正调用的入口**（`_status` 注入，不打真网络）。
    """
    monkeypatch.setattr(rn, "_status", lambda port, auth: {"outputLine": "games=7（rollout=7 / eval=0）"})
    seen: list[str] = []
    rn.emit_output_summary(8443, "tok", seen.append)
    assert seen == ["近30min产出 games=7（rollout=7 / eval=0）"]
    # agent 不可达 ⇒ 仍是一行（不可知），不是零行、不是抛
    monkeypatch.setattr(rn, "_status", lambda port, auth: None)
    seen.clear()
    rn.emit_output_summary(8443, "tok", seen.append)
    assert seen == ["近30min产出 不可知（agent 未应答）"]
