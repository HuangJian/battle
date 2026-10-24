"""tests/remote/test_push_register.py —— 节点自动注册的共享客户端（plan/push-mode-notebooks §4.3）。

两件事必须钉死：

1. **`kind` 缺席即旧字节不变**（评审 P0-2）：采样节点每会话注册一次，若它带上 kind 就会清掉
   控制台手工挂的能力键 —— 所以 `build_register_payload(..., kind="")` 的键集必须与老版本一致。
2. **`capabilities` 是「hub 听懂了」的证据**（评审 P1-6）：老 hub 的 200 没有这个键 ⇒ `old_hub`，
   WARN **只打一次**（worker 重启命中 `unchanged` 也回显了 capabilities 之后，不再假警报）。

全部走注入的 `urlopen`，不碰真网络。
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any

from remote.push_register import (
    RETRY_LADDER,
    Registry,
    build_register_payload,
    default_node_id,
    manual_snippet,
    register,
    should_register,
    unregister,
)


class _Resp:
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


def _http_error(code: int) -> urllib.error.HTTPError:
    from email.message import Message

    return urllib.error.HTTPError(
        url="http://h/x", code=code, msg="x", hdrs=Message(), fp=None
    )


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------- 纯函数


def test_payload_without_kind_is_byte_compatible() -> None:
    """老形状（采样节点）不许因为这次改动多出任何键。"""
    p = build_register_payload("rollout-colab", "http://h:1", "k")
    assert set(p) == {"id", "url", "authKey", "managed"}
    assert set(build_register_payload("n", "http://h:1", "k", workers=0)) == set(p)


def test_payload_carries_kind_and_concurrency_only_when_given() -> None:
    p = build_register_payload("gpu-colab", "https://t", "sek", workers=2, label="gpu", kind="gpu_push")
    assert p["kind"] == "gpu_push" and p["concurrency"] == 2 and p["label"] == "gpu"


def test_should_register_only_on_change() -> None:
    st = {"url": "http://a", "authKey": "k1"}
    assert should_register(st, "http://a", "k1") is False
    assert should_register(st, "http://b", "k1") is True
    assert should_register(st, "http://a", "k2") is True
    assert should_register(None, "http://a", "k1") is True


def test_default_node_id_is_stable_per_role() -> None:
    """P1-5：按角色稳定（与 `rollout-<env>` 同口径），不用每次现生成——hub 的 unregister
    不删条目，随机 id 会让节点列表随 VM 重建线性增长。"""
    assert default_node_id("gpu_push", "colab") == "gpu-colab"
    assert default_node_id("offline_push", "kaggle") == "offline-kaggle"
    assert default_node_id("gpu_push", "") == "gpu-local"


def test_manual_snippet_carries_the_capability_key() -> None:
    gpu = manual_snippet("gpu-colab", "https://t", "sek", "gpu_push")
    off = manual_snippet("offline-kaggle", "https://o", "sek", "offline_push")
    assert json.loads(gpu) == {
        "id": "gpu-colab",
        "url": "https://t",
        "authKey": "sek",
        "gpu_push": True,
        "enabled": True,
    }
    assert json.loads(off)["offline_push"] is True


# ---------------------------------------------------------------- 注册 HTTP 面


def test_register_surfaces_body_and_capabilities() -> None:
    calls: list = []
    out = register(
        _opener(_Resp({"ok": True, "action": "created", "capabilities": ["gpu_push"]}), calls=calls),
        "http://hub:8787/",
        "tok",
        {"id": "n"},
        lambda _m: None,
    )
    assert out["ok"] is True and out["status"] == 200 and out["action"] == "created"
    assert out["capabilities"] == ["gpu_push"]
    assert out["body"]["action"] == "created"
    req = calls[0]
    assert req.full_url == "http://hub:8787/admin/nodes/register"
    assert req.get_header("Authorization") == "Bearer tok"


def test_register_never_raises_and_classifies() -> None:
    out = register(_opener(exc=_http_error(401)), "http://h", "tok", {}, lambda _m: None)
    assert out["ok"] is False and out["kind"] == "auth" and out["capabilities"] == []
    out = register(_opener(exc=urllib.error.URLError("down")), "http://h", "tok", {}, lambda _m: None)
    assert out["ok"] is False and out["kind"] == "net" and out["status"] == 0
    out = register(_opener(exc=_http_error(422)), "http://h", "tok", {}, lambda _m: None)
    assert out["ok"] is False and out["kind"] == "server" and out["status"] == 422
    assert register(_opener(_Resp({})), "", "", {}, lambda _m: None)["ok"] is False


def test_unregister_posts_id() -> None:
    calls: list = []
    out = unregister(
        _opener(_Resp({"ok": True, "action": "unregistered"}), calls=calls),
        "http://hub",
        "tok",
        "gpu-colab",
        lambda _m: None,
    )
    assert out["ok"] is True
    assert json.loads(calls[0].data.decode("utf-8")) == {"id": "gpu-colab"}


# ---------------------------------------------------------------- Registry


def test_registry_dedupes_and_reregisters_on_url_change() -> None:
    calls: list = []
    clock = _Clock()
    reg = Registry(
        hub="http://hub",
        hub_token="tok",
        node_id="gpu-colab",
        kind="gpu_push",
        log=lambda _m: None,
        urlopen=_opener(_Resp({"action": "created", "capabilities": ["gpu_push"]}), calls=calls),
        now_fn=clock,
    )
    assert reg.ensure("https://a", "sek") is True
    assert reg.capabilities == ["gpu_push"] and reg.info()["registered"] is True
    # 同一 url/token ⇒ 一个字节都不发（主循环每 15s 调一次）
    assert reg.ensure("https://a", "sek") is True
    assert len(calls) == 1
    # 隧道换名 ⇒ 重注册
    assert reg.ensure("https://b", "sek") is True
    assert len(calls) == 2
    assert json.loads(calls[1].data.decode("utf-8"))["url"] == "https://b"


def test_registry_backoff_gates_retries() -> None:
    calls: list = []
    clock = _Clock()
    reg = Registry(
        hub="http://hub",
        hub_token="tok",
        node_id="gpu-colab",
        kind="gpu_push",
        log=lambda _m: None,
        urlopen=_opener(exc=urllib.error.URLError("down"), calls=calls),
        now_fn=clock,
    )
    assert reg.ensure("https://a", "sek") is False
    assert len(calls) == 1 and reg.next_at == clock.t + RETRY_LADDER[0]
    clock.t += 1.0
    assert reg.ensure("https://a", "sek") is False
    assert len(calls) == 1, "退避窗口内不许发请求"
    clock.t += RETRY_LADDER[0]
    assert reg.ensure("https://a", "sek") is False
    assert len(calls) == 2, "到点必须重试"


def test_registry_gives_up_on_auth_error() -> None:
    calls: list = []
    reg = Registry(
        hub="http://hub",
        hub_token="bad",
        node_id="gpu-colab",
        kind="gpu_push",
        log=lambda _m: None,
        urlopen=_opener(exc=_http_error(403), calls=calls),
        now_fn=_Clock(),
    )
    assert reg.ensure("https://a", "sek") is False
    assert reg.give_up is True
    assert reg.ensure("https://a", "sek") is False
    assert len(calls) == 1, "auth 类错误重试无意义（只会刷 hub 日志）"


def test_registry_flags_old_hub_once() -> None:
    """老 hub 的 200 没有 capabilities ⇒ old_hub + 一行 WARN（只一次）。"""
    msgs: list[str] = []
    reg = Registry(
        hub="http://hub",
        hub_token="tok",
        node_id="gpu-colab",
        kind="gpu_push",
        log=msgs.append,
        urlopen=_opener(_Resp({"action": "created"})),
        now_fn=_Clock(),
    )
    assert reg.ensure("https://a", "sek") is True
    assert reg.old_hub is True
    warns = [m for m in msgs if m.startswith("WARN")]
    assert len(warns) == 1
    # 第二次（重启/换 URL）不再重复喊
    assert reg.ensure("https://b", "sek", force=True) is True
    assert len([m for m in msgs if m.startswith("WARN")]) == 1


def test_registry_unregister_is_best_effort() -> None:
    calls: list = []
    reg = Registry(
        hub="http://hub",
        hub_token="tok",
        node_id="gpu-colab",
        kind="gpu_push",
        log=lambda _m: None,
        urlopen=_opener(_Resp({"ok": True, "action": "unregistered"}), calls=calls),
        now_fn=_Clock(),
    )
    assert reg.unregister() is False, "没注册过 ⇒ 不发请求"
    assert calls == []
    reg.ensure("https://a", "sek")
    assert reg.unregister() is True
    assert json.loads(calls[-1].data.decode("utf-8")) == {"id": "gpu-colab"}
    assert reg.info()["registered"] is False
    assert reg.unregister() is False, "close 幂等"


def test_registry_registers_against_a_real_hub(tmp_path, monkeypatch) -> None:
    """真 hub（真 route 表 / 真鉴权 / 真落盘）：注册写能力键 → 回显 → 注销。

    单测的假 `urlopen` 钉的是状态机；这条钉的是「请求真的能走到 hub 的 register 并落进
    `rl-config.json`」——分派表少一行时前者全绿而云机永远 404。
    """
    import threading

    from common import distribution
    from hub.server import _JobStore, make_server

    cfg = tmp_path / "rl-config.json"
    cfg.write_text(json.dumps({"version": 1, "nodes": []}), encoding="utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(cfg))
    monkeypatch.setattr(
        distribution, "push_node_ping", lambda url, key, timeout=3.0: {"ok": True, "busy": False}
    )
    hub = _JobStore(tmp_path / "jobs", tmp_path / "ledger.jsonl")
    srv = make_server(hub, 0, "sekret", host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        reg = Registry(
            hub=base,
            hub_token="sekret",
            node_id="gpu-colab",
            kind="gpu_push",
            log=lambda _m: None,
        )
        assert reg.ensure("https://tunnel.example", "push-secret") is True
        node = json.loads(cfg.read_text(encoding="utf-8"))["nodes"][0]
        assert node["gpu_push"] is True and node["authKey"] == "push-secret"
        assert node["managed"] is True and node["enabled"] is True
        assert reg.capabilities == ["gpu_push"]

        # 同一 url/token ⇒ unchanged（并且仍回显 capabilities，不是老 hub 假警报）
        assert reg.ensure("https://tunnel.example", "push-secret", force=True) is True
        assert reg.old_hub is False and reg.info()["action"] == "unchanged"

        assert reg.unregister() is True
        node = json.loads(cfg.read_text(encoding="utf-8"))["nodes"][0]
        assert node["enabled"] is False and node.get("unregistered_at")
        assert node["gpu_push"] is True, "注销只改 enabled，不抹能力键"
    finally:
        srv.shutdown()
        srv.server_close()


def test_registry_without_hub_token_warns_and_does_not_send() -> None:
    calls: list = []
    msgs: list[str] = []
    reg = Registry(
        hub="",
        hub_token="",
        node_id="gpu-colab",
        kind="gpu_push",
        log=msgs.append,
        urlopen=_opener(_Resp({}), calls=calls),
        now_fn=_Clock(),
    )
    assert reg.ensure("https://a", "sek") is False
    assert calls == []
    assert msgs, "未配置时必须说一句（不能静默）"
