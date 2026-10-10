"""tests/hub/test_node_register.py —— 采样节点自动注册的 hub 面（plan/rollout-node-auto-register v2 S3）。

覆盖：鉴权 401 · id/url/authKey/concurrency/label 校验 · 本机表节点保护 409 · ping 门 422 与
`skipPing` · **F1 状态机**（新条目 enabled=true；既有条目保留控制台 enabled/concurrency；
干净收工（`unregistered_at`）⇒ 再注册恢复）· unchanged 不写盘 · 原子写后 JSON 合法且只动
`nodes[]` · unregister 语义 · GET 脱敏。

文件面走 env `BCITY_RL_CONFIG`（fixture 自带的 tmp 配置），不碰本机那份未入库的 rl-config；
网络面 monkeypatch `common.distribution.node_ping`。
"""

from __future__ import annotations

import json
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from common import distribution
from hub.admin import AdminRoutes


class _Stub(AdminRoutes):
    """最小宿主（与 `test_hub_admin_split._Stub` 同规）：鉴权/响应记录/请求体可控。"""

    def __init__(self, body: dict | None = None, *, authed: bool = True) -> None:
        self.sent: list[tuple[str, Any, int]] = []
        self._authed = authed
        self.client_address = ("100.64.0.9", 12345)
        self.path = "/admin/nodes"
        self.headers = Message()
        if body is None:
            self.rfile = BytesIO(b"")
        else:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.headers["Content-Length"] = str(len(raw))
            self.rfile = BytesIO(raw)

    def _auth_ok(self) -> bool:
        if not self._authed:
            self._json({"error": "unauthorized"}, 401)
            return False
        return True

    def _json(self, obj: object, status: int = 200) -> None:
        self.sent.append(("json", obj, status))

    def _read_raw_body(self) -> bytes | None:
        try:
            return self.rfile.read(int(self.headers.get("Content-Length", "0")))
        except Exception:
            return None

    @property
    def last(self) -> tuple[str, Any, int]:
        return self.sent[-1]


@pytest.fixture(autouse=True)
def cfg_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "rl-config.json"
    path.write_text(
        json.dumps({"version": 1, "nodes": [], "rl": {"agent_port": 8443}}), encoding="utf-8"
    )
    monkeypatch.setenv("BCITY_RL_CONFIG", str(path))
    return path


@pytest.fixture
def ping_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(distribution, "node_ping", lambda url, key, timeout=3.0: {"ok": True})


def _nodes(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = raw.get("nodes")
    assert isinstance(out, list), f"rl-config 里 nodes 应为列表，拿到 {type(out).__name__}"
    return out


def _register(body: dict) -> _Stub:
    st = _Stub(body)
    st._admin_nodes_register()
    return st


def _reg_default(**override: Any) -> _Stub:
    return _register({"id": "rollout-colab", "url": "http://100.64.0.5:8443", "authKey": "k1", **override})


# ---------------------------------------------------------------- 鉴权 / 文件面

def test_requires_bearer(cfg_path: Path) -> None:
    before = cfg_path.read_bytes()
    for call in ("_admin_nodes_list",):
        st = _Stub(authed=False)
        getattr(st, call)()
        assert st.last == ("json", {"error": "unauthorized"}, 401)
    st = _Stub({"id": "x", "url": "http://h:1", "authKey": "k"}, authed=False)
    st._admin_nodes_register()
    assert st.last[2] == 401
    assert cfg_path.read_bytes() == before, "未鉴权不得写盘"


def test_missing_config_is_a_clean_refusal(cfg_path: Path, ping_ok: None) -> None:
    cfg_path.unlink()
    st = _reg_default()
    assert st.last[2] == 409
    assert not cfg_path.exists(), "文件不存在时不凭空造配置"


# ---------------------------------------------------------------- 字段校验

@pytest.mark.parametrize("bad", ["", "a b", "a/b", "中文", "x" * 41, "a\\b"])
def test_id_charset_is_valid_worker_id(bad: str, ping_ok: None) -> None:
    st = _Stub({"id": bad, "url": "http://h:1", "authKey": "k"})
    st._admin_nodes_register()
    assert st.last[2] == 400 and st.last[1]["error"].startswith("id 非法")


def test_id_at_40_chars_is_accepted(cfg_path: Path, ping_ok: None) -> None:
    st = _reg_default(id="x" * 40)
    assert st.last[2] == 200 and st.last[1]["action"] == "created"


def test_url_requires_scheme(ping_ok: None) -> None:
    st = _Stub({"id": "n", "url": "100.64.0.5:8443", "authKey": "k"})
    st._admin_nodes_register()
    assert st.last[2] == 400 and st.last[1]["error"].startswith("url 必须是 http")


def test_authkey_required(ping_ok: None) -> None:
    st = _Stub({"id": "n", "url": "http://h:1", "authKey": "  "})
    st._admin_nodes_register()
    assert st.last[2] == 400 and st.last[1]["error"].startswith("authKey 不能为空")


@pytest.mark.parametrize("bad", [0, -1, 1025, "8", True, 2.5])
def test_concurrency_domain(bad: Any, ping_ok: None) -> None:
    st = _Stub({"id": "n", "url": "http://h:1", "authKey": "k", "concurrency": bad})
    st._admin_nodes_register()
    assert st.last[2] == 400 and st.last[1]["error"].startswith("concurrency 需为")


def test_label_domain(ping_ok: None) -> None:
    st = _Stub({"id": "n", "url": "http://h:1", "authKey": "k", "label": "has space"})
    st._admin_nodes_register()
    assert st.last[2] == 400 and st.last[1]["error"].startswith("label 非法")


# ---------------------------------------------------------------- ping 门

def test_ping_gate_blocks_write(cfg_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(distribution, "node_ping", lambda url, key, timeout=3.0: None)
    before = cfg_path.read_bytes()
    st = _Stub({"id": "n", "url": "http://h:1", "authKey": "k"})
    st._admin_nodes_register()
    assert st.last[2] == 422 and st.last[1]["error"].startswith("节点 ping 不通")
    assert cfg_path.read_bytes() == before


def test_skip_ping_bypasses_the_gate(cfg_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def _boom(url: str, key: str, timeout: float = 3.0):
        called.append(url)
        raise AssertionError("skipPing 不该调 node_ping")

    monkeypatch.setattr(distribution, "node_ping", _boom)
    st = _Stub({"id": "n", "url": "http://h:1", "authKey": "k", "skipPing": True})
    st._admin_nodes_register()
    assert called == []
    assert st.last[2] == 200 and st.last[1]["action"] == "created"


# ---------------------------------------------------------------- 本机表节点保护

def test_unmanaged_local_node_is_a_hard_409(cfg_path: Path, ping_ok: None) -> None:
    cfg_path.write_text(
        json.dumps(
            {
                "version": 1,
                "nodes": [{"id": "mac", "url": "http://mac:8443", "authKey": "x", "enabled": True}],
            }
        ),
        encoding="utf-8",
    )
    before = cfg_path.read_bytes()
    st = _Stub({"id": "mac", "url": "http://100.64.0.5:8443", "authKey": "k"})
    st._admin_nodes_register()
    assert st.last[2] == 409 and st.last[1]["error"].startswith("id mac 已被本机表节点占用")
    assert cfg_path.read_bytes() == before


# ---------------------------------------------------------------- F1 状态机 + upsert

def test_new_node_is_managed_enabled_and_keeps_other_keys(cfg_path: Path, ping_ok: None) -> None:
    st = _reg_default(concurrency=94, label="colab-t")
    assert st.last[1] == {"ok": True, "id": "rollout-colab", "action": "created"}
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert raw["version"] == 1 and raw["rl"]["agent_port"] == 8443  # 只动 nodes[]
    node = raw["nodes"][0]
    assert node == {
        "id": "rollout-colab",
        "url": "http://100.64.0.5:8443",
        "authKey": "k1",
        "managed": True,
        "enabled": True,
        "concurrency": 94,
        "label": "colab-t",
    }


def test_new_node_without_concurrency_omits_the_key(cfg_path: Path, ping_ok: None) -> None:
    """F2：缺省不写 `concurrency`（= 派发按 `ping.cpus`），绝不写 1。"""
    _reg_default()
    assert set(_nodes(cfg_path)[0]) == {"id", "url", "authKey", "managed", "enabled"}


def test_upsert_preserves_console_intent_and_restores_after_clean_shutdown(
    cfg_path: Path, ping_ok: None
) -> None:
    """F1 三路径：会话中停用不被顶；干净收工 ⇒ 再注册恢复；concurrency 归控制台。"""
    _reg_default(concurrency=94)

    # 控制台：停用 + 手工把并发调到 8（模拟控制台 saveConfig 整份回写）
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw["nodes"][0]["enabled"] = False
    raw["nodes"][0]["concurrency"] = 8
    cfg_path.write_text(json.dumps(raw), encoding="utf-8")

    # 路径 A：会话进行中的停用（无 unregistered_at）⇒ 重注册只换地址，enabled/concurrency 不动
    st = _Stub({"id": "rollout-colab", "url": "http://100.64.0.6:8443", "authKey": "k2"})
    st._admin_nodes_register()
    assert st.last[1]["action"] == "updated"
    node = _nodes(cfg_path)[0]
    assert node["enabled"] is False and node["concurrency"] == 8
    assert node["url"] == "http://100.64.0.6:8443" and node["authKey"] == "k2"

    # 路径 B：干净收工（unregister）⇒ 再注册恢复 enabled=true，concurrency 仍归控制台
    st = _Stub({"id": "rollout-colab"})
    st._admin_nodes_unregister()
    assert st.last[1]["action"] == "unregistered"
    node = _nodes(cfg_path)[0]
    assert node["enabled"] is False and bool(node.get("unregistered_at"))

    st = _Stub({"id": "rollout-colab", "url": "http://100.64.0.7:8443", "authKey": "k3"})
    st._admin_nodes_register()
    assert st.last[1]["action"] == "updated"
    node = _nodes(cfg_path)[0]
    assert node["enabled"] is True and node.get("unregistered_at") is None
    assert node["concurrency"] == 8

    # 路径 C：崩溃退出（无 unregistered_at、enabled 保持 true）⇒ 再注册照旧可用
    st = _Stub({"id": "rollout-colab", "url": "http://100.64.0.8:8443", "authKey": "k4"})
    st._admin_nodes_register()
    node = _nodes(cfg_path)[0]
    assert node["enabled"] is True and node["url"] == "http://100.64.0.8:8443"


def test_unchanged_does_not_rewrite(cfg_path: Path, ping_ok: None) -> None:
    _reg_default()
    before = cfg_path.read_bytes()
    st = _Stub({"id": "rollout-colab", "url": "http://100.64.0.5:8443", "authKey": "k1"})
    st._admin_nodes_register()
    assert st.last[1]["action"] == "unchanged"
    assert cfg_path.read_bytes() == before


def test_atomic_write_leaves_valid_json_and_no_tmp(cfg_path: Path, ping_ok: None) -> None:
    _reg_default()
    json.loads(cfg_path.read_text(encoding="utf-8"))  # 可解析
    leftovers = list(cfg_path.parent.glob("*.tmp"))
    assert leftovers == [], f"原子写不应留临时文件：{leftovers}"


# ---------------------------------------------------------------- unregister / list

def test_unregister_only_touches_managed(cfg_path: Path, ping_ok: None) -> None:
    st = _Stub({"id": "nope"})
    st._admin_nodes_unregister()
    assert st.last[2] == 404

    cfg_path.write_text(
        json.dumps({"nodes": [{"id": "mac", "url": "http://mac:1", "authKey": "x"}]}),
        encoding="utf-8",
    )
    st = _Stub({"id": "mac"})
    st._admin_nodes_unregister()
    assert st.last[2] == 404 and _nodes(cfg_path)[0].get("unregistered_at") is None


def test_list_redacts_authkey_and_filters_managed(cfg_path: Path, ping_ok: None) -> None:
    _reg_default()
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw["nodes"].append({"id": "mac", "url": "http://mac:1", "authKey": "secret"})
    cfg_path.write_text(json.dumps(raw), encoding="utf-8")

    st = _Stub()
    st._admin_nodes_list()
    body = st.last[1]
    assert body["count"] == 1
    assert body["nodes"][0]["id"] == "rollout-colab"
    # 清单只回这些键（`authKey` 不在其中 = 不回凭证）
    listed = {"id", "url", "label", "enabled", "concurrency", "unregistered_at"}
    assert set(body["nodes"][0]) <= listed
