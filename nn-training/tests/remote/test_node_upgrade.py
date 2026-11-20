"""`remote/node_upgrade.py` —— 训练机对采样节点的远控升级 / 重启护栏（B8，2026-11-19）。

从 `tests/common/test_upgrade.py` 搬来（整簇随 `common.distribution` → `remote.node_upgrade`），
同时把原来的 `check()` + `FAILS` 脚本体改成真正的 pytest 断言 —— 旧写法在 pytest 下**判失败
也不红**（`check` 只往列表里记），搬家的同时把这个静默洞补上。

覆盖：
  1) stale 节点被指示升级（pullBranch 正确、auth 头正确）；
  2) codeHash 一致的节点不被重启；
  3) 不可达节点优雅跳过（不崩、不阻塞）；
  4) `request_upgrade` 失败（非 200/202）返回 False；
  5) 跨代去重（F1：dedup 键含期望 hash）与冷却窗（F3：窗过后允许重发）；
  6) 脏工作区护栏（远端拒发 / self 节点纯重启且 pullBranch 强制空）；
  7) `RestartMemo` 的实例隔离（进程级状态只有默认实例，测试自己 new 一份）。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import remote.node_upgrade as nu
from tests.helpers import net_seams
from tests.helpers.http_server import serve_fast


class MockAgent:
    """可配置的 mock agent：codeHash 按构造参数区分（stale/current 行为）。"""

    def __init__(self, code_hash: str = "stale-hash-abc") -> None:
        self.code_hash = code_hash
        self.restart_calls: list[dict] = []
        self._handler_cls = self._make_handler()
        self.server = HTTPServer(("127.0.0.1", 0), self._handler_cls)
        self.port = self.server.server_address[1]
        self.thread = serve_fast(self.server)
        self.thread.start()

    def _make_handler(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静音
                pass

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/v1/ping":
                    self._send_json(
                        {
                            "agentVersion": "test",
                            "codeHash": mock.code_hash,
                            "cpus": 4,
                            "bunVersion": "1.4.0",
                            "evalSupport": True,
                        }
                    )
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                u = urlparse(self.path)
                if u.path == "/v1/restart":
                    ln = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(ln).decode("utf-8") if ln else "{}"
                    mock.restart_calls.append(
                        {
                            "auth": self.headers.get("Authorization", ""),
                            "body": json.loads(body),
                        }
                    )
                    self._send_json({"ok": True})
                else:
                    self.send_response(404)
                    self.end_headers()

            def _send_json(self, obj):
                data = json.dumps(obj).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return H

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_request_upgrade_posts_the_branch_and_auth() -> None:
    """stale 节点 → `POST /v1/restart {pullBranch}` + Bearer；不可达 → False（不抛）。"""
    mock = MockAgent()
    try:
        ok = nu.request_upgrade(mock.url(), "KEY_X", "intent-ai")
        assert ok, "request_upgrade stale node → True"
        assert len(mock.restart_calls) == 1, "POST /v1/restart 恰好一次"
        call = mock.restart_calls[0]
        assert call["body"].get("pullBranch") == "intent-ai", call["body"]
        assert call["auth"] == "Bearer KEY_X", call["auth"]
        assert nu.request_upgrade(f"http://127.0.0.1:{mock.port + 1}", "K", "x", timeout=1.0) is False, (
            "request_upgrade 失败节点 → False（不抛）"
        )
    finally:
        mock.close()


def test_upgrade_stale_nodes_scans_and_upgrades_only_stale() -> None:
    """扫描：stale 升级、current 不动、不可达跳过、disabled 不参与。"""
    nu.reset_restart_state()
    expected = "current-hash-xyz"
    stale = MockAgent(code_hash="stale-hash-abc")  # ≠ expected → 应升级
    current = MockAgent(code_hash=expected)  # == expected → 不升级
    cfg = {
        "nodes": [
            {"id": "stale", "url": stale.url(), "authKey": "K1", "enabled": True},
            {"id": "current", "url": current.url(), "authKey": "K2", "enabled": True},
            # 死桩走 `net_seams`（当场拒绝）：写 `:1` 在本仓容器是 DROP ⇒ 白等满 status_timeout。
            {"id": "down", "url": net_seams.dead_loopback_url(), "authKey": "K3", "enabled": True},
            {"id": "disabled", "url": stale.url(), "authKey": "K4", "enabled": False},
        ]
    }
    try:
        # dirty=[] 显式注入干净工作区（真实仓库可能是脏的——护栏用例单独测）。
        res = nu.upgrade_stale_nodes(
            cfg,
            expected_hash=expected,
            branch="intent-ai",
            status_timeout=1.0,
            restart_timeout=3.0,
            dirty=[],
        )
        by_id = {r["id"]: r for r in res}
        assert by_id["stale"]["upgraded"] is True, "stale 节点 → 指示升级"
        assert by_id["stale"]["reason"] == "restart-requested", by_id["stale"]["reason"]
        assert by_id["current"]["upgraded"] is False and by_id["current"]["reason"] == "current", (
            "current 节点不升级"
        )
        assert by_id["down"]["upgraded"] is False and by_id["down"]["reason"] == "unreachable", (
            "不可达节点优雅跳过"
        )
        assert set(by_id) == {"stale", "current", "down"}, "disabled 节点不参与"
        assert len(stale.restart_calls) == 1, len(stale.restart_calls)
        assert len(current.restart_calls) == 0, "current 未收到 restart"
    finally:
        stale.close()
        current.close()


def test_request_upgrade_guarded_dedup() -> None:
    """跨代去重：同节点同 (agent codeHash, 期望 hash) 只杀一次；两栏任一变化 ⇒ 恢复资格。

    F1（plan/dist-codehash-stale-fix.md）：dedup 键纳入期望 hash——agent hash 没变但
    训练机期望值变了（本机 commit/切分支/改集内文件）⇒ 允许再发一次升级。
    """
    nu.reset_restart_state()
    mock = MockAgent(code_hash="stale-hash-abc")
    try:
        ok1, r1 = nu.request_upgrade_guarded(
            "n1", mock.url(), "K", "goal-nn", "stale-hash-abc", dirty=[], expected_hash="expect-1"
        )
        assert ok1 and r1 == "restart-requested", r1
        assert len(mock.restart_calls) == 1, "首次恰好 1 次 POST"
        ok2, r2 = nu.request_upgrade_guarded(
            "n1", mock.url(), "K", "goal-nn", "stale-hash-abc", dirty=[], expected_hash="expect-1"
        )
        assert ok2 is False and r2 == "dedup", f"同 (hash, 期望) 二次 → dedup, got {r2}"
        assert len(mock.restart_calls) == 1, "dedup 不再发 POST"
        # F1 核心回归：agent hash 没变但训练机期望 hash 变了 ⇒ 能收到第二次升级请求。
        ok3, r3 = nu.request_upgrade_guarded(
            "n1", mock.url(), "K", "goal-nn", "stale-hash-abc", dirty=[], expected_hash="expect-2"
        )
        assert ok3 and r3 == "restart-requested", r3
        assert len(mock.restart_calls) == 2, "期望 hash 变化后的重启恰好发出"
        ok4, r4 = nu.request_upgrade_guarded(
            "n1", mock.url(), "K", "goal-nn", "stale-hash-abc", dirty=[], expected_hash="expect-2"
        )
        assert ok4 is False and r4 == "dedup", f"同 (hash, 期望) 三次 → 仍 dedup, got {r4}"
        assert len(mock.restart_calls) == 2, "仍 dedup 不再发 POST"
        # 节点 hash 变化（pull 生效 / 手动更新成功但仍 stale）→ 恢复重启资格。
        mock.code_hash = "still-stale-but-new"
        ok5, r5 = nu.request_upgrade_guarded(
            "n1",
            mock.url(),
            "K",
            "goal-nn",
            "still-stale-but-new",
            dirty=[],
            expected_hash="expect-2",
        )
        assert ok5 and r5 == "restart-requested", r5
        assert len(mock.restart_calls) == 3, "第三次重启恰好发出"
        # 不同节点互不影响（去重按 nid 隔离）。
        ok6, r6 = nu.request_upgrade_guarded(
            "n2",
            mock.url(),
            "K",
            "goal-nn",
            "still-stale-but-new",
            dirty=[],
            expected_hash="expect-2",
        )
        assert ok6 and r6 == "restart-requested", r6
        assert len(mock.restart_calls) == 4, "n2 的首杀发出"
    finally:
        mock.close()


def test_request_upgrade_guarded_cooldown() -> None:
    """F3：去重是**冷却窗**而非永久压制；窗过后允许重发并重置时钟；落盘 memo 预置同理。"""
    nu.reset_restart_state()
    mock = MockAgent(code_hash="stale-hash-abc")
    args = ("n1", mock.url(), "K", "goal-nn", "stale-hash-abc")
    try:
        ok1, r1 = nu.request_upgrade_guarded(*args, dirty=[], expected_hash="E", cooldown_sec=60)
        assert ok1 and r1 == "restart-requested", r1
        ok2, r2 = nu.request_upgrade_guarded(*args, dirty=[], expected_hash="E", cooldown_sec=60)
        assert ok2 is False and r2 == "dedup", f"窗内 → dedup, got {r2}"
        assert len(mock.restart_calls) == 1, "窗内不再发 POST"
        # 窗过期（cooldown=0 等价于「已过 10 分钟」）⇒ 允许再发一次（自愈路径）。
        ok3, r3 = nu.request_upgrade_guarded(*args, dirty=[], expected_hash="E", cooldown_sec=0)
        assert ok3 and r3 == "restart-requested", r3
        assert len(mock.restart_calls) == 2, "重发恰好一次"
        # 重发后时钟重置：窗内再问又回到 dedup（防连环杀没被废掉）。
        ok4, r4 = nu.request_upgrade_guarded(*args, dirty=[], expected_hash="E", cooldown_sec=60)
        assert ok4 is False and r4 == "dedup", f"重发后窗内 → dedup, got {r4}"
        assert len(mock.restart_calls) == 2, "重发后时钟重置，窗内不发 POST"
        # 调用方持久化 memo（TS 落盘那条腿）预置 atSec：一小时前的下发 ⇒ 已过期 ⇒ 可重发。
        nu.reset_restart_state()
        nu.seed_restart_state(
            [
                {
                    "id": "n1",
                    "pingHash": "stale-hash-abc",
                    "expectedHash": "E",
                    "atSec": time.time() - 3600,
                }
            ]
        )
        ok5, r5 = nu.request_upgrade_guarded(*args, dirty=[], expected_hash="E")
        assert ok5 and r5 == "restart-requested", f"memo 里 1h 前的下发应已过期, got {r5}"
        assert len(mock.restart_calls) == 3, "过期 memo 后的重发发出"
        # 旧 memo（无 atSec）视作「刚刚下发」⇒ 保持旧的永久去重语义（向后兼容）。
        nu.reset_restart_state()
        nu.seed_restart_state([{"id": "n1", "pingHash": "stale-hash-abc", "expectedHash": "E"}])
        ok6, r6 = nu.request_upgrade_guarded(*args, dirty=[], expected_hash="E")
        assert ok6 is False and r6 == "dedup", f"旧 memo（无 atSec）→ dedup, got {r6}"
    finally:
        nu.reset_restart_state()
        mock.close()


def test_request_upgrade_guarded_dirty_tree(monkeypatch) -> None:
    """脏工作区护栏：远端拒发 `dirty-tree:<n>`；self 节点纯重启且 pullBranch 强制空。

    注意：mock agent 绑 127.0.0.1 会被 `is_self_node` 判为 self——用 monkeypatch 模拟
    真实远端（非回环主机）与 self 两种身份（与被测模块同一命名空间）。
    """
    nu.reset_restart_state()
    remote = MockAgent(code_hash="stale-hash-abc")
    self_mock = MockAgent(code_hash="stale-hash-abc")
    monkeypatch.setattr(nu, "is_self_node", lambda url, node_id="": node_id == "self")
    try:
        dirty = ["src/types.ts", "src/game/SimulationCombat.ts"]
        ok, r = nu.request_upgrade_guarded(
            "remote", remote.url(), "K", "goal-nn", "stale-hash-abc", dirty=dirty
        )
        assert ok is False and r == "dirty-tree:2", f"远端脏树 → 拒发, got {r}"
        assert len(remote.restart_calls) == 0, "远端脏树 → 不杀进程"
        # self 节点：dirty 仍放行，纯重启。
        ok2, r2 = nu.request_upgrade_guarded(
            "self", self_mock.url(), "K", "goal-nn", "stale-hash-abc", dirty=dirty
        )
        assert ok2 and r2 == "restart-requested", r2
        assert self_mock.restart_calls[0]["body"].get("pullBranch") == "", (
            "self 重启 pullBranch 强制为空（禁 pull）"
        )
        # dirty=None 自动检测：不抛异常、reason 合法即可（真实仓库状态不确定）。
        ok3, r3 = nu.request_upgrade_guarded(
            "auto", remote.url(), "K", "goal-nn", "auto-hash", dirty=None
        )
        assert isinstance(ok3, bool) and isinstance(r3, str), f"dirty=None 自动检测不抛, got {r3}"
    finally:
        remote.close()
        self_mock.close()


def test_upgrade_stale_nodes_dirty_tree(monkeypatch) -> None:
    """`upgrade_stale_nodes` 在脏工作区下对远端节点全部拒发（零 restart POST）。"""
    nu.reset_restart_state()
    stale = MockAgent(code_hash="stale-hash-abc")
    cfg = {"nodes": [{"id": "stale", "url": stale.url(), "authKey": "K1", "enabled": True}]}
    monkeypatch.setattr(nu, "is_self_node", lambda url, node_id="": False)  # 模拟非回环远端
    try:
        res = nu.upgrade_stale_nodes(
            cfg,
            "current-hash-xyz",
            "goal-nn",
            status_timeout=1.0,
            restart_timeout=3.0,
            dirty=["src/nn/rl-reward-toy.ts"],
        )
        by_id = {r["id"]: r for r in res}
        assert by_id["stale"]["upgraded"] is False, "脏树 → 不升级"
        assert by_id["stale"]["reason"] == "dirty-tree:1", by_id["stale"]["reason"]
        assert len(stale.restart_calls) == 0, "脏树 → 零 restart POST"
    finally:
        stale.close()


def test_restart_memos_are_independent() -> None:
    """`RestartMemo` 是两个独立实例（进程级状态住显式对象，测试自己 new 一份即可隔离）。"""
    a = nu.RestartMemo()
    b = nu.RestartMemo()
    a.seen["mac"] = ("ping", "expected", 1.0)
    assert a.seen == {"mac": ("ping", "expected", 1.0)}
    assert b.seen == {}
    b.reset()
    assert b.seen == {}
    assert a.seen.get("mac") == ("ping", "expected", 1.0)
