"""tests/remote/test_rollout_node.py —— 采样节点运行时（plan/rollout-node-auto-register v2）。

覆盖 S2 的纯函数面 + 注册 HTTP 面（注入 urlopen，不碰真网络）：
`resolve_link` 默认表 · `registry_url` 与 `resolve_hub_url` 单一实现 · 载荷缺省不写
`concurrency`（F2）· 去重 · 错误分类 · register/unregister 三态。
"""

from __future__ import annotations

import json
import os
import urllib.error
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from remote import rollout_node as rn
from remote import rollout_tunnel as rt
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
    down = rn._status(8443, "tok", urlopen=_opener(exc=urllib.error.URLError("down")))
    assert down is None
    # 非 dict 响应体（旧/坏 agent）⇒ None（不编一个状态出来）
    odd = rn._status(8443, urlopen=_opener(_Resp([1, 2])))
    assert odd is None


def test_probe_errors_are_transport_only_not_our_own_shape_bug() -> None:
    """`_NODE_PROBE_ERRORS` 的边界（P0-7 第二十一批）：「探不活」只许是传输面的结论。

    `_ping` 的结论是「节点死了 ⇒ 重启 agent」——把 `AttributeError`（回执形状变了）也读成
    不健康就是一个重启环。`_status` 同理，后果轻些（摘要变「不可知」）。
    反向判据：把两个 `except` 改回 `Exception` ⇒ 下面两条 DID NOT RAISE。
    """
    # 判据全在**返回值**上（G5：断言表达式不出现私有名）
    for exc in (ConnectionResetError("tunnel dropped"), TimeoutError("idle"), ValueError("bad")):
        empty_status = rn._status(8443, urlopen=_opener(exc=exc))
        empty_ping = rn._ping(8443, urlopen=_opener(exc=exc))
        assert empty_status is None and empty_ping is None, exc
    not_json = rn._status(8443, urlopen=_opener(_Resp("not json")))
    assert not_json is None, "200 但 body 不是 JSON 也只能是「不可知」"

    # 我们自己的 bug：直接穿出去（两处都得抛，`_ping` 那条最贵）
    with pytest.raises(AttributeError):
        rn._status(8443, urlopen=_opener(exc=AttributeError("resp lost .read")))
    with pytest.raises(AttributeError):
        rn._ping(8443, urlopen=_opener(exc=AttributeError("resp lost .read")))


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


# ---------------------------------------------------------------- GitHub 不通时的下载路


def test_slot_count_uses_all_physical_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    """采样节点不预留核：8 **物理**核起 8 个 worker（训练侧 `cpu_worker_slots` 的留 2 核不适用）。

    口径必须是物理核（= agent 自己的 `CPUS = physicalCores()`）：两套口径并存就会出现
    「笔记本按 4 开池、agent 自报 2 核」的超额认购（2026-11-11 kaggle：父进程 HTTP 被饿到
    30~60s，连带 ping/权重握手成批超时）。HT sibling 只共享执行单元与 L1/L2，每个 worker
    是 ~75–100MB 的重型进程 ⇒ 按物理核铺才是「满核」。
    """
    from common import platform_utils

    monkeypatch.setattr(platform_utils, "effective_cores", lambda: 8)
    monkeypatch.setattr(platform_utils, "physical_cores", lambda: 4)
    slots = rn._slot_count(0)[0]
    assert slots == 4
    slots = rn._slot_count(3)[0]
    assert slots == 3
    # 容器配额比物理核更小（cgroup 限流）⇒ 取小，别按机器型号报数
    monkeypatch.setattr(platform_utils, "effective_cores", lambda: 2)
    # 保留一处直接读：`test_private_assert_budget` 的自检要求本文件基线 > 0（扫描面活性锚）。
    assert rn._slot_count(0)[0] == 2


# ------------------------------------------------ 空 URL 硬闸 / banner / 通道姿势

def test_empty_url_never_posts_to_hub() -> None:
    """★ 空 URL 是本地失败：零 HTTP、kind=nourl、不写 reg_state。"""
    calls: list = []
    out = rn._try_register_once(
        url="",
        auth="ak",
        node_id="n",
        workers=1,
        label="l",
        hub_url="http://hub:8787",
        hub_token="tok",
        log=lambda _m: None,
        urlopen=_opener(_Resp({}), calls=calls),
    )
    assert out == {
        "ok": False,
        "status": 0,
        "action": None,
        "body": {},
        "capabilities": [],
        "error": "no tunnel url",
        "kind": "nourl",
    }
    assert calls == []


def test_try_register_once_posts_with_the_injected_opener() -> None:
    calls: list = []
    out = rn._try_register_once(
        url="https://a.trycloudflare.com",
        auth="ak",
        node_id="n",
        workers=1,
        label="l",
        hub_url="http://hub:8787",
        hub_token="tok",
        log=lambda _m: None,
        urlopen=_opener(_Resp({"action": "created"}), calls=calls),
    )
    assert out["ok"] is True and len(calls) == 1


def test_banner_skips_manual_snippet_when_url_empty() -> None:
    lines = rn._banner_lines(
        node_id="n",
        url="",
        auth="ak",
        workers=2,
        bun_version="1.4.4",
        code_hash="0123456789abcdef",
        hub_configured=True,
        reg_state={"url": "", "authKey": ""},
    )
    assert lines == [
        "=" * 68,
        "★ 未注册：隧道 URL 未取到 —— 不要手工登记（空 url 会被 hub 400 拒）。",
        "   节点每 60s 重试建隧道；真因看上面的 cloudflared 日志尾部。",
        "★ URL     : (未取到)",
        "★ authKey : ak",
        "  workers : 2  bun 1.4.4  codeHash=0123456789ab",
        "=" * 68,
    ]


def test_banner_prints_manual_snippet_when_url_present() -> None:
    lines = rn._banner_lines(
        node_id="n",
        url="https://a.trycloudflare.com",
        auth="ak",
        workers=2,
        bun_version="1.4.4",
        code_hash="0123456789abcdef",
        hub_configured=True,
        reg_state={"url": "", "authKey": ""},
    )
    assert lines[2] == (
        '  {"nodes":[{"id":"n","url":"https://a.trycloudflare.com","authKey":"ak"}]}'
    )


def test_direct_env_strips_proxies_keeps_no_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://platform:3128")
    monkeypatch.setenv("https_proxy", "http://platform:3128")
    monkeypatch.setenv("NO_PROXY", ".corp.example")
    env = rn._direct_env()
    assert env.get("HTTP_PROXY") is None and env.get("https_proxy") is None
    bypass = env["NO_PROXY"].split(",")
    assert bypass[:3] == ["localhost", "127.0.0.1", "::1"]
    assert bypass[-1] == ".corp.example"
    assert env["PATH"] == os.environ["PATH"]
    # 只给子进程用，不能污染本进程（注册请求还要走平台代理）。
    assert os.environ["HTTP_PROXY"] == "http://platform:3128"


def test_open_cloudflared_once_starts_even_with_nonempty_current_url() -> None:
    """★ B1：422 时 current_url 是 tailscale URL，但 cfp 为 None ⇒ 必须起 cf。"""
    calls: list[int] = []

    def _ensure() -> tuple[str, object]:
        calls.append(1)
        return "https://cf.trycloudflare.com", object()

    url, cfp, started = rn._open_cloudflared_once(
        current_url="http://100.64.0.5:8443", cfp=None, ensure=_ensure
    )
    assert url == "https://cf.trycloudflare.com"
    assert cfp is not None and started is True and calls == [1]


def test_open_cloudflared_once_reuses_live_process() -> None:
    proc = _Proc(None)
    url, cfp, started = rn._open_cloudflared_once(
        current_url="", cfp=proc, ensure=lambda: ("", None)
    )
    assert url == "" and cfp is proc and started is False


# ------------------------------------------------ cloudflared 自救 / 预检

class _Proc:
    def __init__(self, rc: int | None) -> None:
        self.rc = rc
        self.terminated = False

    def poll(self) -> int | None:
        return self.rc

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_open_cloudflared_once_restarts_when_process_exited() -> None:
    """死进程对象（poll 非 None）必须当作没有：否则静默死后再也建不起隧道。"""
    calls: list[int] = []

    def _ensure() -> tuple[str, object]:
        calls.append(1)
        return "https://new.trycloudflare.com", _Proc(None)

    url, cfp, started = rn._open_cloudflared_once(
        current_url="https://dead.trycloudflare.com", cfp=_Proc(7), ensure=_ensure
    )
    assert url == "https://new.trycloudflare.com"
    assert cfp is not None and cfp.poll() is None
    assert started is True and calls == [1]


def test_422_and_cf_failure_stops_without_second_tailscale_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B1：cf 全败时清空 URL，不能拿旧 tailscale URL 再发一次必然 422 的注册。"""
    code_dir = tmp_path / "code"
    entry = code_dir / "tools" / "agent" / "sampler-agent.ts"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fake\n", encoding="utf-8")
    posts: list[str] = []

    class _Agent:
        returncode = None

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def _interrupt(*_a: object) -> None:
        raise KeyboardInterrupt

    def _fake_register(**kw: object) -> dict:
        posts.append(str(kw["url"]))
        return {
            "ok": False,
            "status": 422,
            "action": None,
            "body": {},
            "capabilities": [],
            "error": "unreachable",
            "kind": "server",
        }

    monkeypatch.setattr(rn, "probe_cloud_env", lambda: "test")
    monkeypatch.setattr(rn, "resolve_link", lambda *_a: rn.LINK_TAILSCALE)
    monkeypatch.setattr(rn, "_slot_count", lambda _n: (1, "test"))
    monkeypatch.setattr(rn, "registry_url", lambda *_a: "http://hub:8787")
    monkeypatch.setattr(rn, "ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_net_probe", lambda _log: ("inherit", "direct"))
    monkeypatch.setattr(rn, "_open_tailscale", lambda *_a: ("http://100.64.0.5:8443", True))
    monkeypatch.setattr(rn, "_ensure_cloudflared_url", lambda *_a, **_k: ("", None))
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Agent())
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_proc_bun_version", lambda *_a, **_k: "1.4.4")
    monkeypatch.setattr(rn, "_try_register_once", _fake_register)
    monkeypatch.setattr(rn.time, "sleep", _interrupt)

    rn.run(
        {
            "code_dir": str(code_dir),
            "link": "tailscale",
            "node_id": "n",
            "hub_url": "http://hub:8787",
            "hub_token": "tok",
            "agent_port": 8443,
            "workers": 1,
        },
        lambda _m: None,
        lambda key, default: {"TS_AUTHKEY": "ak", "HUB_TOKEN": "tok"}.get(key, default),
    )
    assert posts == ["http://100.64.0.5:8443"]


def test_empty_url_multiple_ticks_never_posts_or_backs_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B4：空 URL 在监护循环里多拍都不进注册，也不翻退避。"""
    code_dir = tmp_path / "code"
    entry = code_dir / "tools" / "agent" / "sampler-agent.ts"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fake\n", encoding="utf-8")
    clock = {"now": 1000.0, "sleeps": 0}
    logs: list[str] = []
    posts: list[str] = []
    gates: list[str] = []
    real_once = rn._try_register_once

    def _register(*_a: object) -> dict:
        posts.append("called")
        raise AssertionError("空 URL 不得进入 register()")

    def _gate(**kw: Any) -> dict:
        gates.append(str(kw["url"]))
        return real_once(**kw)

    def _time() -> float:
        return clock["now"]

    def _sleep(sec: float) -> None:
        clock["sleeps"] += 1
        clock["now"] += sec
        if clock["sleeps"] >= 4:
            raise KeyboardInterrupt

    monkeypatch.setattr(rn, "probe_cloud_env", lambda: "test")
    monkeypatch.setattr(rn, "resolve_link", lambda *_a: rn.LINK_CLOUDFLARED)
    monkeypatch.setattr(rn, "_slot_count", lambda _n: (1, "test"))
    monkeypatch.setattr(rn, "registry_url", lambda *_a: "http://hub:8787")
    monkeypatch.setattr(rn, "ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_net_probe", lambda _log: ("inherit", "direct"))
    monkeypatch.setattr(rn, "_ensure_cloudflared_url", lambda *_a, **_k: ("", None))
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Proc(None))
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_proc_bun_version", lambda *_a, **_k: "1.4.4")
    monkeypatch.setattr(rn, "register", _register)
    monkeypatch.setattr(rn, "_try_register_once", _gate)
    monkeypatch.setattr(rn.time, "time", _time)
    monkeypatch.setattr(rn.time, "sleep", _sleep)

    rn.run(
        {
            "code_dir": str(code_dir),
            "link": "cloudflared",
            "node_id": "n",
            "hub_url": "http://hub:8787",
            "hub_token": "tok",
            "agent_port": 8443,
            "workers": 1,
            "max_session_hours": 1,
        },
        logs.append,
        lambda key, default: {"TS_AUTHKEY": "ak", "HUB_TOKEN": "tok"}.get(key, default),
    )
    assert posts == []
    assert gates == [""]  # 只有首拍一次的本地硬闸；监护多拍不再调用
    assert clock["sleeps"] == 4
    assert any(line.startswith("首拍未注册") for line in logs)
    assert not [line for line in logs if line.startswith("注册失败")]


def test_dead_tunnel_process_rebuilds_and_reregisters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """2026-11-08 现场：cloudflared 静默死、URL 字串不变 ⇒ 必须重建 + 重注册。"""
    code_dir = tmp_path / "code"
    entry = code_dir / "tools" / "agent" / "sampler-agent.ts"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fake\n", encoding="utf-8")
    clock = {"now": 1000.0, "sleeps": 0}
    logs: list[str] = []
    posts: list[str] = []
    built: list[str] = []
    tunnel = _Proc(None)  # 首拍拿到时还活着，注册成功后静默死掉（现场形态）
    ensured = iter(
        [
            ("https://dead.trycloudflare.com", tunnel),
            ("https://live.trycloudflare.com", _Proc(None)),
        ]
    )

    def _ensure(*_a: object, **_k: object) -> tuple[str, object]:
        url, proc = next(ensured)
        built.append(url)
        return url, proc

    def _register(**kw: object) -> dict:
        posts.append(str(kw["url"]))
        tunnel.rc = 7  # 注册成功后隧道进程死掉
        return {
            "ok": True,
            "status": 200,
            "action": "updated",
            "body": {},
            "capabilities": [],
            "error": None,
            "kind": "ok",
        }

    def _time() -> float:
        return clock["now"]

    def _sleep(sec: float) -> None:
        clock["sleeps"] += 1
        clock["now"] += sec
        if clock["sleeps"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(rn, "probe_cloud_env", lambda: "test")
    monkeypatch.setattr(rn, "resolve_link", lambda *_a: rn.LINK_CLOUDFLARED)
    monkeypatch.setattr(rn, "_slot_count", lambda _n: (1, "test"))
    monkeypatch.setattr(rn, "registry_url", lambda *_a: "http://hub:8787")
    monkeypatch.setattr(rn, "ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_net_probe", lambda _log: ("inherit", "direct"))
    monkeypatch.setattr(rn, "_ensure_cloudflared_url", _ensure)
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Proc(None))
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_tunnel_url", lambda _p: "")
    monkeypatch.setattr(rn, "_proc_bun_version", lambda *_a, **_k: "1.4.4")
    monkeypatch.setattr(rn, "_try_register_once", _register)
    monkeypatch.setattr(rn, "unregister", lambda *_a, **_k: {"ok": True, "error": None})
    monkeypatch.setattr(rn.time, "time", _time)
    monkeypatch.setattr(rn.time, "sleep", _sleep)

    rn.run(
        {
            "code_dir": str(code_dir),
            "link": "cloudflared",
            "node_id": "n",
            "hub_url": "http://hub:8787",
            "hub_token": "tok",
            "agent_port": 8443,
            "workers": 1,
            "max_session_hours": 1,
        },
        logs.append,
        lambda key, default: {"HUB_TOKEN": "tok"}.get(key, default),
    )

    assert built == [
        "https://dead.trycloudflare.com",
        "https://live.trycloudflare.com",
    ]
    assert posts == [
        "https://dead.trycloudflare.com",
        "https://live.trycloudflare.com",
    ]


def test_cloudflared_dead_process_triggers_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    procs = [_Proc(1), _Proc(None)]
    starts: list[object] = []

    def _start(*_a: object, **_k: object) -> _Proc:
        p = procs[len(starts)]
        starts.append(p)
        return p

    monkeypatch.setattr(rn, "_start_cloudflared", _start)
    monkeypatch.setattr(
        rn,
        "_wait_tunnel_url",
        lambda *_a, **_k: "" if len(starts) == 1 else "https://b.trycloudflare.com",
    )
    monkeypatch.setattr(rn, "_reset_cf_log", lambda *_a: None)
    monkeypatch.setattr(rn, "_dead_reason", lambda *_a: "x")
    monkeypatch.setattr(rn, "_dump_tail", lambda *_a, **_k: None)
    monkeypatch.setattr(rn.time, "sleep", lambda *_a: None)

    url, cfp = rn._ensure_cloudflared_url(
        {}, 8443, tmp_path / "cf.log", lambda _m: None, attempts=3
    )
    assert url == "https://b.trycloudflare.com"
    assert cfp is procs[1] and len(starts) == 2


def test_dead_url_is_not_accepted_even_when_log_has_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B5：日志先写出 URL、进程随即退出 ⇒ 该 URL 不得采用，继续下一 attempt。"""
    procs = [_Proc(1), _Proc(None)]
    starts: list[object] = []
    urls = iter(["https://dead.trycloudflare.com", "https://live.trycloudflare.com"])

    def _start(*_a: object, **_k: object) -> _Proc:
        p = procs[len(starts)]
        starts.append(p)
        return p

    monkeypatch.setattr(rn, "_start_cloudflared", _start)
    monkeypatch.setattr(rn, "_wait_tunnel_url", lambda *_a, **_k: next(urls))
    monkeypatch.setattr(rn, "_reset_cf_log", lambda *_a: None)
    monkeypatch.setattr(rn, "_dead_reason", lambda *_a: "x")
    monkeypatch.setattr(rn, "_dump_tail", lambda *_a, **_k: None)
    monkeypatch.setattr(rn.time, "sleep", lambda *_a: None)

    url, cfp = rn._ensure_cloudflared_url(
        {}, 8443, tmp_path / "cf.log", lambda _m: None, attempts=2
    )
    assert url == "https://live.trycloudflare.com"
    assert cfp is procs[1] and len(starts) == 2


def test_cloudflared_retry_is_bounded_and_ladder_returns_to_preferred(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    starts: list[dict] = []
    sleeps: list[float] = []

    def _start(*_a: object, **kw: object) -> _Proc:
        starts.append(dict(kw))
        return _Proc(1)

    monkeypatch.setattr(rn, "_start_cloudflared", _start)
    monkeypatch.setattr(rn, "_wait_tunnel_url", lambda *_a, **_k: "")
    monkeypatch.setattr(rn, "_reset_cf_log", lambda *_a: None)
    monkeypatch.setattr(rn, "_dead_reason", lambda *_a: "x")
    monkeypatch.setattr(rn, "_dump_tail", lambda *_a, **_k: None)
    monkeypatch.setattr(rn.time, "sleep", lambda s: sleeps.append(s))

    url, cfp = rn._ensure_cloudflared_url(
        {},
        8443,
        tmp_path / "cf.log",
        lambda _m: None,
        attempts=3,
        postures=("inherit", "direct"),
    )
    assert url == "" and cfp is None and len(starts) == 3
    assert [s["env_posture"] for s in starts] == ["inherit", "direct", "inherit"]
    assert [s["protocol"] for s in starts] == ["http2", "http2", None]
    assert [s["debug"] for s in starts] == [False, False, True]
    assert sleeps == [5.0, 10.0]


def test_net_probe_uses_proxy_aware_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rn, "_probe_https", lambda posture, **_k: posture == "inherit")
    monkeypatch.setattr(rn, "_probe_tcp", lambda *_a, **_k: True)
    logs: list[str] = []
    postures = rn._net_probe(logs.append)
    assert postures == ("inherit", "direct")
    assert logs == [
        "预检: api.trycloudflare.com:443 inherit=ok direct=fail ｜ 边缘7844 tcp=ok",
        "⇒ 采用 inherit 姿势",
    ]


def test_net_probe_falls_back_to_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rn, "_probe_https", lambda posture, **_k: posture == "direct")
    monkeypatch.setattr(rn, "_probe_tcp", lambda *_a, **_k: True)
    logs: list[str] = []
    postures = rn._net_probe(logs.append)
    assert postures == ("direct", "inherit")
    assert logs == [
        "预检: api.trycloudflare.com:443 inherit=fail direct=ok ｜ 边缘7844 tcp=ok",
        "⇒ inherit 不通、direct 可达 ⇒ 先试 direct 姿势",
    ]


def test_net_probe_reports_unreachable_but_still_returns_postures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rn, "_probe_https", lambda *_a, **_k: False)
    monkeypatch.setattr(rn, "_probe_tcp", lambda *_a, **_k: False)
    logs: list[str] = []
    postures = rn._net_probe(logs.append)
    assert postures == ("inherit", "direct")
    assert logs == [
        "预检: api.trycloudflare.com:443 inherit=fail direct=fail ｜ 边缘7844 tcp=fail",
        "⇒ 公网不可达：api.trycloudflare.com:443 两种姿势都不通（检查出网/DNS）；"
        "仍按 inherit 尝试",
    ]


def test_cf_cmd_carries_compat_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}

    def _popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return object()

    monkeypatch.setattr(rn, "ensure_cloudflared", lambda *_a, **_k: "/bin/cloudflared")
    monkeypatch.setattr(rn.subprocess, "Popen", _popen)
    proc = rn._start_cloudflared({}, 8443, tmp_path / "cf.log", lambda _m: None)
    assert proc is not None
    assert captured["cmd"] == [
        "/bin/cloudflared",
        "tunnel",
        "--url",
        "http://localhost:8443",
        "--no-autoupdate",
        "--protocol",
        "http2",
        "--edge-ip-version",
        "4",
        "--logfile",
        str(tmp_path / "cf.log"),
    ]
    assert captured["env"] is None


def test_cf_cmd_direct_posture_uses_child_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}

    def _popen(cmd, **kw):
        captured["env"] = kw.get("env")
        return object()

    monkeypatch.setenv("HTTP_PROXY", "http://platform:3128")
    monkeypatch.setattr(rn, "ensure_cloudflared", lambda *_a, **_k: "/bin/cloudflared")
    monkeypatch.setattr(rn.subprocess, "Popen", _popen)
    rn._start_cloudflared(
        {}, 8443, tmp_path / "cf.log", lambda _m: None, env_posture="direct"
    )
    env = captured["env"]
    assert env is not None
    assert env.get("HTTP_PROXY") is None
    assert env["NO_PROXY"].split(",")[0] == "localhost"
    assert os.environ["HTTP_PROXY"] == "http://platform:3128"


def test_open_tailscale_returns_url_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rn.tailscale_boot,
        "ensure",
        lambda _cfg, _log: {"ip": "100.64.0.5", "mode": "userspace"},
    )
    opened = rn._open_tailscale({}, "ak", 8443, lambda _m: None)
    assert opened == ("http://100.64.0.5:8443", True)

    def _boom(_cfg, _log):
        raise RuntimeError("no tailnet")

    monkeypatch.setattr(rn.tailscale_boot, "ensure", _boom)
    opened = rn._open_tailscale({}, "ak", 8443, lambda _m: None)
    assert opened == ("", False)


def test_start_agent_marks_the_process_as_supervised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """notebook 启动链必须让 agent 交给外层监护循环重启，而不是自己 spawn detached child。"""
    monkeypatch.delenv(rn.AGENT_SUPERVISED_ENV, raising=False)
    seen: dict[str, Any] = {}

    def _popen(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw
        return object()

    monkeypatch.setattr(rn.subprocess, "Popen", _popen)
    out = rn._start_agent(
        "/bin/bun",
        tmp_path / "sampler-agent.ts",
        tmp_path,
        8443,
        2,
        {},
        tmp_path / "agent.log",
    )
    assert out is not None
    assert seen["kw"]["env"][rn.AGENT_SUPERVISED_ENV] == "1"
    assert os.environ.get(rn.AGENT_SUPERVISED_ENV) is None, "不得把标记泄漏进训练机/本进程环境"


def test_planned_restart_exit_code_is_not_a_crash() -> None:
    """75 = 受监护模式下的 /v1/restart；监护循环只按这个码识别计划内重启。"""
    assert rn.AGENT_RESTART_EXIT_CODE == 75
    assert rn.AGENT_RESTART_EXIT_CODE != 0
