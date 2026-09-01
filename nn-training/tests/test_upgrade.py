"""test_upgrade.py — dist_common 主动升级机制（M8）单元测试。

编排层 ping 发现 agent codeHash stale → POST /v1/restart 指示 git pull + 重启。
用真实本地 HTTP mock agent 验证：
  1) stale 节点被指示升级（pullBranch 正确、auth 头正确）；
  2) codeHash 一致的节点不被重启；
  3) 不可达节点优雅跳过（不崩、不阻塞）；
  4) request_upgrade 失败（非 200/202）返回 False。

运行（经统一启动器）：-Script test_upgrade.py；全过 0，否则 1。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import dist_common

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


class MockAgent:
    """可配置的 mock agent：codeHash 按构造参数区分（stale/current 行为）。"""

    def __init__(self, code_hash: str = "stale-hash-abc") -> None:
        self.code_hash = code_hash
        self.restart_calls: list[dict] = []
        self._handler_cls = self._make_handler()
        self.server = HTTPServer(("127.0.0.1", 0), self._handler_cls)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
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


def make_cfg(mock_url: str, enabled: bool = True) -> dict:
    return {
        "nodes": [
            {"id": "stale", "url": mock_url, "authKey": "KEY_STALE", "enabled": enabled},
            # current 节点：单独 mock（codeHash 匹配 expected）——用第二个 MockAgent。
        ]
    }


def test_request_upgrade() -> None:
    mock = MockAgent()
    try:
        ok = dist_common.request_upgrade(mock.url(), "KEY_X", "intent-ai")
        check(ok, "request_upgrade stale node → True")
        check(len(mock.restart_calls) == 1, "POST /v1/restart 恰好一次")
        if mock.restart_calls:
            c = mock.restart_calls[0]
            check(
                c["body"].get("pullBranch") == "intent-ai",
                f"pullBranch=intent-ai, got {c['body']!r}",
            )
            check(c["auth"] == "Bearer KEY_X", "Authorization: Bearer KEY_X")
        # 非 200/202 → False。
        ok2 = dist_common.request_upgrade(
            f"http://127.0.0.1:{mock.port + 1}", "K", "x", timeout=1.0
        )
        check(ok2 is False, "request_upgrade 失败节点 → False（不抛）")
    finally:
        mock.close()


def test_upgrade_stale_nodes() -> None:
    dist_common.reset_restart_state()
    EXPECTED = "current-hash-xyz"
    stale = MockAgent(code_hash="stale-hash-abc")  # ≠ expected → 应升级
    current = MockAgent(code_hash=EXPECTED)  # == expected → 不升级
    cfg = {
        "nodes": [
            {"id": "stale", "url": stale.url(), "authKey": "K1", "enabled": True},
            {"id": "current", "url": current.url(), "authKey": "K2", "enabled": True},
            {"id": "down", "url": "http://127.0.0.1:1", "authKey": "K3", "enabled": True},
            {"id": "disabled", "url": stale.url(), "authKey": "K4", "enabled": False},
        ]
    }
    try:
        # dirty=[] 显式注入干净工作区（真实仓库可能是脏的——护栏用例单独测）。
        res = dist_common.upgrade_stale_nodes(
            cfg,
            expected_hash=EXPECTED,
            branch="intent-ai",
            status_timeout=1.0,
            restart_timeout=3.0,
            dirty=[],
        )
        by_id = {r["id"]: r for r in res}
        # stale 节点（codeHash 与 expected 不同）→ 被升级。
        check(by_id["stale"]["upgraded"] is True, "stale 节点 → 指示升级")
        check(
            by_id["stale"]["reason"] == "restart-requested",
            f"stale reason, got {by_id['stale']['reason']}",
        )
        # current 节点（codeHash == expected）→ 不升级。
        check(
            by_id["current"]["upgraded"] is False and by_id["current"]["reason"] == "current",
            "current 节点不升级",
        )
        # 不可达节点 → 跳过。
        check(
            by_id["down"]["upgraded"] is False and by_id["down"]["reason"] == "unreachable",
            "不可达节点优雅跳过",
        )
        # disabled 节点不参与。
        check("disabled" not in by_id, "disabled 节点不参与")
        # stale 收到 /v1/restart；current 未收到。
        check(
            len(stale.restart_calls) == 1,
            f"stale 收到 1 次 restart，got {len(stale.restart_calls)}",
        )
        check(len(current.restart_calls) == 0, "current 未收到 restart")
    finally:
        stale.close()
        current.close()


def test_request_upgrade_guarded_dedup() -> None:
    """跨代去重（修复「重启过的进程被再次远控重启」）：同节点同 agent codeHash 只
    杀一次；节点 hash 变化（pull 生效/手动更新）后恢复资格。"""
    dist_common.reset_restart_state()
    mock = MockAgent(code_hash="stale-hash-abc")
    try:
        ok1, r1 = dist_common.request_upgrade_guarded(
            "n1", mock.url(), "K", "goal-nn", "stale-hash-abc", dirty=[]
        )
        check(ok1 and r1 == "restart-requested", f"首次 → restart-requested, got {r1}")
        check(len(mock.restart_calls) == 1, "首次恰好 1 次 POST")
        # 同一节点、同一 agent codeHash 再请求 → dedup，不杀进程。
        ok2, r2 = dist_common.request_upgrade_guarded(
            "n1", mock.url(), "K", "goal-nn", "stale-hash-abc", dirty=[]
        )
        check(ok2 is False and r2 == "dedup", f"同 hash 二次 → dedup, got {r2}")
        check(len(mock.restart_calls) == 1, "dedup 不再发 POST")
        # 节点 hash 变化（pull 生效 / 手动更新成功但仍 stale）→ 恢复重启资格。
        mock.code_hash = "still-stale-but-new"
        ok3, r3 = dist_common.request_upgrade_guarded(
            "n1", mock.url(), "K", "goal-nn", "still-stale-but-new", dirty=[]
        )
        check(ok3 and r3 == "restart-requested", f"hash 变化后 → 可再次重启, got {r3}")
        check(len(mock.restart_calls) == 2, "第二次重启恰好发出")
        # 不同节点互不影响（去重按 nid 隔离）。
        ok4, r4 = dist_common.request_upgrade_guarded(
            "n2", mock.url(), "K", "goal-nn", "still-stale-but-new", dirty=[]
        )
        check(ok4 and r4 == "restart-requested", f"另一节点首杀不受影响, got {r4}")
        check(len(mock.restart_calls) == 3, "n2 的首杀发出")
    finally:
        mock.close()


def test_request_upgrade_guarded_dirty_tree() -> None:
    """脏工作区护栏（修复「手动更新重启的进程被远控杀掉」）：期望 codeHash 含未提交
    改动时远端 pull 永不收敛 → 拒绝对远端节点下发 restart；self/回环节点豁免
    （代码同源，纯重启拾取工作区代码）且强制 pullBranch=""（共享工作区禁 pull）。
    注意：mock agent 绑 127.0.0.1 会被 is_self_node 判为 self——monkeypatch 模拟
    真实远端（非回环主机）与 self 两种身份。"""
    dist_common.reset_restart_state()
    remote = MockAgent(code_hash="stale-hash-abc")
    self_mock = MockAgent(code_hash="stale-hash-abc")
    real_is_self = dist_common.is_self_node
    dist_common.is_self_node = lambda url, nid="": nid == "self"
    try:
        dirty = ["src/types.ts", "src/game/SimulationCombat.ts"]
        ok, r = dist_common.request_upgrade_guarded(
            "remote", remote.url(), "K", "goal-nn", "stale-hash-abc", dirty=dirty
        )
        check(ok is False and r == "dirty-tree:2", f"远端脏树 → 拒发, got {r}")
        check(len(remote.restart_calls) == 0, "远端脏树 → 不杀进程")
        # self 节点：dirty 仍放行，纯重启。
        ok2, r2 = dist_common.request_upgrade_guarded(
            "self", self_mock.url(), "K", "goal-nn", "stale-hash-abc", dirty=dirty
        )
        check(ok2 and r2 == "restart-requested", f"self 脏树 → 纯重启放行, got {r2}")
        if self_mock.restart_calls:
            check(
                self_mock.restart_calls[0]["body"].get("pullBranch") == "",
                "self 重启 pullBranch 强制为空（禁 pull）",
            )
        # dirty=None 自动检测：不抛异常、reason 合法即可（真实仓库状态不确定）。
        ok3, r3 = dist_common.request_upgrade_guarded(
            "auto", remote.url(), "K", "goal-nn", "auto-hash", dirty=None
        )
        check(isinstance(ok3, bool) and isinstance(r3, str), f"dirty=None 自动检测不抛, got {r3}")
    finally:
        dist_common.is_self_node = real_is_self
        remote.close()
        self_mock.close()


def test_upgrade_stale_nodes_dirty_tree() -> None:
    """upgrade_stale_nodes 在脏工作区下对远端节点全部拒发（零 restart POST）。"""
    dist_common.reset_restart_state()
    EXPECTED = "current-hash-xyz"
    stale = MockAgent(code_hash="stale-hash-abc")
    cfg = {"nodes": [{"id": "stale", "url": stale.url(), "authKey": "K1", "enabled": True}]}
    real_is_self = dist_common.is_self_node
    dist_common.is_self_node = lambda url, nid="": False  # 模拟非回环远端节点
    try:
        res = dist_common.upgrade_stale_nodes(
            cfg,
            EXPECTED,
            "goal-nn",
            status_timeout=1.0,
            restart_timeout=3.0,
            dirty=["src/nn/rl-reward-toy.ts"],
        )
        by_id = {r["id"]: r for r in res}
        check(by_id["stale"]["upgraded"] is False, "脏树 → 不升级")
        check(
            by_id["stale"]["reason"] == "dirty-tree:1",
            f"reason=dirty-tree:1, got {by_id['stale']['reason']}",
        )
        check(len(stale.restart_calls) == 0, "脏树 → 零 restart POST")
    finally:
        dist_common.is_self_node = real_is_self
        stale.close()


def test_parse_porcelain() -> None:
    """_parse_porcelain：修改/暂存/未跟踪/改名（含引号路径）/空行。"""
    text = (
        " M src/types.ts\n"
        "M  src/nn/rl-reward-toy.ts\n"
        "?? tools/agent/restart-guard.ts\n"
        'R  old_name.ts -> "new name.ts"\n'
        "\n"
    )
    got = dist_common._parse_porcelain(text)
    check(
        got
        == [
            "src/types.ts",
            "src/nn/rl-reward-toy.ts",
            "tools/agent/restart-guard.ts",
            "new name.ts",
        ],
        f"porcelain 解析, got {got!r}",
    )


def test_dirty_hash_files_smoke() -> None:
    """dirty_hash_files 冒烟：真实仓库上不抛异常、返回 list。"""
    d = dist_common.dirty_hash_files()
    check(isinstance(d, list), f"dirty_hash_files 返回 list（不抛）, got {type(d).__name__}")


def test_codehash_manifest_expansion() -> None:
    """SSOT 清单 codehash-files.txt 展开：目录条目递归、文件条目直接纳入，relPath 全
    正斜杠且无重复；本次事故的 3 个关键文件必须在集内。"""
    entries = dist_common._collect_code_hash_files()
    rels = [rel for rel, _content in entries]
    check(len(rels) > 10, f"清单展开非空（got {len(rels)} files）")
    # 2026-09-01 事故：Python 侧加了这 3 个文件、TS 侧漏同步 → 节点被永久误判 stale。
    for need in (
        "tools/agent/restart-guard.ts",
        "src/types.ts",
        "src/game/SimulationCombat.ts",
        "tools/agent/sampler-agent.ts",
    ):
        check(need in rels, f"清单含 {need}")
    check(any(r.startswith("src/nn/") for r in rels), "src/nn/ 目录条目递归纳入")
    check(all("\\" not in r for r in rels), "relPath 全 posix 正斜杠")
    check(len(rels) == len(set(rels)), "文件集无重复")


def test_codehash_bilingual_contract() -> None:
    """双语 codeHash 契约：TS 侧（节点）与 Python 侧（训练机）算出的 hash 必须逐字节
    一致。任一侧漂移（文件集/内容/排序/归一化）都会红。bun 不可用则跳过。"""
    bun = shutil.which("bun")
    if bun is None:
        print("  SKIP bun 不可用，跳过双语契约比对", flush=True)
        return
    agent = REPO / "tools" / "agent" / "sampler-agent.ts"
    try:
        proc = subprocess.run(
            [bun, str(agent), "--print-code-hash"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        check(False, f"bun --print-code-hash 执行失败: {e}")
        return
    if proc.returncode != 0:
        check(False, f"bun --print-code-hash 非零退出: {proc.stderr.strip()}")
        return
    # --print-code-hash 输出带 [HH:MM:SS] 时间戳前缀，取最后一个 token。
    ts_hash = proc.stdout.strip().split()[-1]
    py_hash = dist_common.compute_code_hash()
    check(
        len(ts_hash) == 64 and ts_hash == py_hash,
        f"双语 codeHash 一致 (TS={ts_hash[:12]}… Python={py_hash[:12]}…)",
    )


def main() -> None:
    print("== test_upgrade ==")
    test_request_upgrade()
    test_upgrade_stale_nodes()
    test_request_upgrade_guarded_dedup()
    test_request_upgrade_guarded_dirty_tree()
    test_upgrade_stale_nodes_dirty_tree()
    test_parse_porcelain()
    test_dirty_hash_files_smoke()
    test_codehash_manifest_expansion()
    test_codehash_bilingual_contract()
    print(f"== {'PASS' if not FAILS else 'FAIL'} ({len(FAILS)} failures) ==")
    for m in FAILS:
        print("  -", m)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
