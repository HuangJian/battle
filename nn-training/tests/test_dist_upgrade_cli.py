"""test_dist_upgrade_cli — 节点升级指令一次性入口（TS 工具复用的那份守卫）。

契约：spec → 逐节点 `dist_common.request_upgrade_guarded`（**不允许**本文件自行
实现护栏逻辑——单源就在这里）；结构性错误退出码 2；`dry_run` 不发任何 POST。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dist_common
import dist_upgrade_cli

NODE = {"id": "mac", "url": "http://192.168.0.88:8443", "authKey": "k", "pingHash": "a" * 64}
SPEC = {"expected_hash": "b" * 64, "branch": "goal-nn", "nodes": [NODE]}


def test_spec_validation_errors() -> None:
    for bad in (
        {},  # expected_hash 缺
        {"expected_hash": "x"},  # nodes 缺
        {"expected_hash": "x", "nodes": []},  # nodes 空
        {"expected_hash": "x", "nodes": [{"id": "mac"}]},  # url 缺
        {"expected_hash": "x", "nodes": [NODE], "dirty": "not-a-list"},
        {"expected_hash": "x", "nodes": [NODE], "timeout": "soon"},
        {"expected_hash": "x", "nodes": [NODE], "cooldown_sec": "soon"},  # F3
        {"expected_hash": "x", "nodes": [NODE], "cooldown_sec": -1},
    ):
        with pytest.raises(ValueError):
            dist_upgrade_cli.run_spec(bad)


def test_current_node_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple] = []

    def fake(*a, **k):
        called.append((a, k))
        return True, "x"

    monkeypatch.setattr(dist_common, "request_upgrade_guarded", fake)
    out = dist_upgrade_cli.run_spec({**SPEC, "nodes": [{**NODE, "pingHash": "b" * 64}], "dirty": []})
    assert out["results"] == [{"id": "mac", "ok": False, "reason": "current"}]
    assert called == []  # hash 相同 ⇒ 一个 POST 都不该发


def test_stale_node_maps_to_shared_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(
        nid, url, auth, branch, ping_hash, timeout=20.0, dirty=None, expected_hash="",
        cooldown_sec=None,
    ):
        seen.update(
            nid=nid, url=url, auth=auth, branch=branch, ping=ping_hash, dirty=dirty,
            exp=expected_hash, cooldown=cooldown_sec,
        )
        return True, "restart-requested"

    monkeypatch.setattr(dist_common, "request_upgrade_guarded", fake)
    out = dist_upgrade_cli.run_spec({**SPEC, "dirty": ["src/x.ts"], "cooldown_sec": 30})
    assert out["results"] == [{"id": "mac", "ok": True, "reason": "restart-requested"}]
    assert out["dirty"] == ["src/x.ts"]
    # 显式 dirty 透传（调用方每轮已检测则复用，不重复探测）；期望 hash 必须显式传（F1）。
    assert seen["dirty"] == ["src/x.ts"] and seen["exp"] == "b" * 64 and seen["branch"] == "goal-nn"
    # F3：spec.cooldown_sec 透传到守卫（缺省 None ⇒ 由 dist_common 决定 env/常量）。
    assert seen["cooldown"] == 30.0


def test_dirty_none_probes_really(monkeypatch: pytest.MonkeyPatch) -> None:
    probed: list[int] = []

    def probe() -> list[str]:
        probed.append(1)
        return ["src/y.ts"]

    monkeypatch.setattr(dist_common, "dirty_hash_files", probe)
    monkeypatch.setattr(dist_common, "request_upgrade_guarded", lambda *a, **k: (False, "dirty-tree:1"))
    out = dist_upgrade_cli.run_spec({**SPEC, "dirty": None})
    assert probed, "dirty=null 必须由本进程探测（字节级判据单源在 dist_common）"
    assert out["dirty"] == ["src/y.ts"]
    assert out["results"][0]["reason"] == "dirty-tree:1"


def test_self_node_skips_dirty_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """self/回环节点：不探 dirty（同 dist_common 语义），且分支必须置空（禁 pull）。"""
    seen: dict = {}

    def fake(nid, url, auth, branch, *a, **k):
        seen["branch"] = branch
        return True, "restart-requested"

    monkeypatch.setattr(dist_common, "request_upgrade_guarded", fake)
    out = dist_upgrade_cli.run_spec(
        {
            "expected_hash": "b" * 64,
            "branch": "goal-nn",
            "nodes": [{"id": "self", "url": "http://127.0.0.1:8443", "pingHash": "a" * 64}],
        }
    )
    # 判定 self 的是 dist_common.is_self_node（真函数，未打桩）——这正是「复用」的意义。
    assert dist_common.is_self_node("http://127.0.0.1:8443", "self") is True
    assert out["results"][0]["reason"] == "restart-requested"
    assert seen["branch"] == "goal-nn"  # 分支由 dist_common 内部按 self 置空，调用方不需要知道


def test_dry_run_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []

    def fake(*a, **k):
        called.append(1)
        return True, "x"

    monkeypatch.setattr(dist_common, "request_upgrade_guarded", fake)
    out = dist_upgrade_cli.run_spec({**SPEC, "dry_run": True, "dirty": ["src/z.ts"]})
    assert called == []
    assert out["results"][0]["reason"] == "dirty-tree:1"


def test_cli_end_to_end_stdin_stdout(tmp_path: Path) -> None:
    py = sys.executable
    spec = {**SPEC, "dry_run": True, "dirty": []}
    proc = subprocess.run(
        [py, str(ROOT / "dist_upgrade_cli.py")],
        input=json.dumps(spec).encode("utf-8"),
        capture_output=True,
        cwd=ROOT,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = json.loads(proc.stdout.decode("utf-8"))
    assert out["results"] == [{"id": "mac", "ok": False, "reason": "planned"}]


def test_cli_bad_spec_exit_2() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "dist_upgrade_cli.py")],
        input=b"{}",
        capture_output=True,
        cwd=ROOT,
        timeout=60,
    )
    assert proc.returncode == 2
    assert json.loads(proc.stdout.decode("utf-8"))["error"]


# ---------------- 扫描模式（cfg_path）：判 stale 这一步必须复用 dist_common ----------------

CFG = {
    "nodes": [
        {"id": "stale", "url": "http://10.0.0.1:8443", "authKey": "k", "enabled": True},
        {"id": "current", "url": "http://10.0.0.2:8443", "enabled": True},
        {"id": "off", "url": "http://10.0.0.3:8443", "enabled": False},
    ]
}


def _write_cfg(tmp_path: Path, cfg: dict) -> str:
    p = tmp_path / "rl-config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


def _ping_a(url: str, auth: str = "", timeout: float = 3.0) -> dict:
    """所有节点都报同一个 hash（当前/陈旧取决于 expected）。"""
    return {"codeHash": "a" * 64}


def _recorder(posts: list[str]) -> Callable[..., bool]:
    """替换 request_upgrade 的 POST 记登器（返回 True = agent 已接受）。"""

    def _fake(url: str, auth: str, branch: str, timeout: float = 20.0) -> bool:
        posts.append(url)
        return True

    return _fake


def test_scan_pings_itself_and_only_stale_is_upgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫描模式自己 ping（调用方不再 ping）——判据是 dist_common 那一层。"""
    cfg_path = _write_cfg(tmp_path, CFG)
    dist_common.reset_restart_state()
    pinged: list[str] = []

    def fake_ping(url: str, auth: str = "", timeout: float = 3.0) -> dict | None:
        pinged.append(url)
        if "10.0.0.1:" in url:
            return {"codeHash": "a" * 64, "agentVersion": "abc"}
        return {"codeHash": "b" * 64, "agentVersion": "def"}

    monkeypatch.setattr(dist_common, "node_ping", fake_ping)
    monkeypatch.setattr(dist_common, "dirty_hash_files", lambda: [])
    posted: list[str] = []
    monkeypatch.setattr(dist_common, "request_upgrade", _recorder(posted))
    out = dist_upgrade_cli.run_scan(
        {"cfg_path": cfg_path, "branch": "goal-nn", "dirty": [], "expected_hash": "b" * 64}
    )
    by_id = {r["id"]: r for r in out["results"]}
    # enabled 节点全被 ping（current 也要 ping——那一步正是在判 stale）；disabled 不碰。
    assert sorted(pinged) == ["http://10.0.0.1:8443", "http://10.0.0.2:8443"]
    assert by_id["stale"]["ok"] is True and by_id["stale"]["reason"] == "restart-requested"
    assert by_id["stale"]["pingHash"] == "a" * 64  # 调用方要靠它写 memo
    assert by_id["current"]["reason"] == "current"
    assert posted == ["http://10.0.0.1:8443"]  # 只对 stale 发 POST


def test_scan_expected_hash_defaults_to_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write_cfg(tmp_path, {"nodes": [CFG["nodes"][0]]})
    dist_common.reset_restart_state()
    monkeypatch.setattr(dist_common, "compute_code_hash", lambda: "a" * 64)
    monkeypatch.setattr(dist_common, "node_ping", _ping_a)
    posted: list[str] = []
    monkeypatch.setattr(dist_common, "request_upgrade", _recorder(posted))
    out = dist_upgrade_cli.run_scan({"cfg_path": cfg_path, "branch": "goal-nn"})
    assert out["results"][0]["reason"] == "current" and posted == []


def test_scan_seen_memo_suppresses_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """跨调用 memo（seen）预置回 _RESTART_SEEN ⇒ dedup 分支与常驻循环逐字一致。"""
    cfg_path = _write_cfg(tmp_path, {"nodes": [CFG["nodes"][0]]})
    dist_common.reset_restart_state()
    monkeypatch.setattr(dist_common, "node_ping", _ping_a)
    # 工作区脏不脏与用例语义无关（scan 不传 dirty ⇒ 守卫会字节级探测）——如果断言真的
    # 依赖「本仓此刻恰好没有未提交的 SSOT 文件」，那改 tools/agent/** 就会把这个用例弄红
    # （实测 2026-09-19 F2）。固定为「干净」。
    monkeypatch.setattr(dist_common, "dirty_hash_files", lambda: [])
    posted: list[str] = []
    monkeypatch.setattr(dist_common, "request_upgrade", _recorder(posted))
    # 第一次：真发（并返回 pingHash 供调用方持久化）。
    first = dist_upgrade_cli.run_scan(
        {"cfg_path": cfg_path, "branch": "goal-nn", "dirty": [], "expected_hash": "b" * 64}
    )
    assert first["results"][0]["reason"] == "restart-requested"
    # 第二次（新进程语义）：调用方把上次下发写进 memo 并预置回来 ⇒ 不再打扰节点。
    dist_common.reset_restart_state()
    seen = [
        {"id": "stale", "pingHash": first["results"][0]["pingHash"], "expectedHash": "b" * 64}
    ]
    second = dist_upgrade_cli.run_scan(
        {"cfg_path": cfg_path, "branch": "goal-nn", "dirty": [], "seen": seen, "expected_hash": "b" * 64}
    )
    assert second["results"][0]["reason"] == "dedup"
    assert posted == ["http://10.0.0.1:8443"]  # 只发过一次


def test_scan_dry_run_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _write_cfg(tmp_path, CFG)
    dist_common.reset_restart_state()
    monkeypatch.setattr(dist_common, "node_ping", _ping_a)
    called: list[str] = []

    def _fake_scan(cfg: dict, *a: object, **k: object) -> list:
        called.append("x")
        return []

    monkeypatch.setattr(dist_common, "upgrade_stale_nodes", _fake_scan)
    out = dist_upgrade_cli.run_scan({"cfg_path": cfg_path, "dry_run": True, "expected_hash": "b" * 64})
    assert called == []  # dry 连扫描升级路径都不进
    assert {r["id"]: r["reason"] for r in out["results"]} == {"stale": "stale", "current": "stale"}


def test_scan_structural_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        dist_upgrade_cli.run_scan({"diff": 1})  # cfg_path 缺
    with pytest.raises(ValueError):
        dist_upgrade_cli.run_scan({"cfg_path": str(tmp_path / "nope.json")})
    with pytest.raises(ValueError):
        dist_upgrade_cli.run_scan({"cfg_path": _write_cfg(tmp_path, CFG), "seen": "nope"})


def test_scan_empty_nodes_is_noop(tmp_path: Path) -> None:
    out = dist_upgrade_cli.run_scan({"cfg_path": _write_cfg(tmp_path, {"nodes": []})})
    assert out["results"] == []


def test_seed_restart_state_contract() -> None:
    dist_common.reset_restart_state()
    n = dist_common.seed_restart_state(
        [
            {"id": "mac", "pingHash": "a" * 64, "expectedHash": "b" * 64},
            {"id": "bad"},  # 缺 hash → 忽略
            "not-a-dict",  # 类型不对 → 忽略
        ]
    )
    assert n == 1
    # 第三项是「该次下发时刻」（F3 冷却窗靠它）：缺 atSec ⇒ 记作现在（旧语义 = 立即 dedup）
    got = dist_common._RESTART_SEEN["mac"]
    assert got[:2] == ("a" * 64, "b" * 64)
    assert isinstance(got[2], float) and abs(got[2] - time.time()) < 5


def test_scan_seen_atsec_expires_dedup_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3：memo 里的 `atSec` 早于冷却窗 ⇒ 允许再发一次；新 memo（无 atSec）⇒ 仍 dedup。

    旧行为：pull 失败/不支持远端升级的节点带着同一个 codeHash 回来 ⇒ memo 键永久命中，
    该节点再也收不到升级指令（跨调用持续压制）。
    """
    cfg_path = _write_cfg(tmp_path, {"nodes": [CFG["nodes"][0]]})
    monkeypatch.setattr(dist_common, "node_ping", _ping_a)
    monkeypatch.setattr(dist_common, "dirty_hash_files", lambda: [])  # 同上一用例：与工作区无关
    posted: list[str] = []
    monkeypatch.setattr(dist_common, "request_upgrade", _recorder(posted))

    # 一小时前下发过（memo 里带着当时的时刻）⇒ 冷却窗（缺省 600s）已过 ⇒ 再发一次
    dist_common.reset_restart_state()
    old = dist_upgrade_cli.run_scan(
        {
            "cfg_path": cfg_path,
            "branch": "goal-nn",
            "dirty": [],
            "expected_hash": "b" * 64,
            "seen": [
                {
                    "id": "stale",
                    "pingHash": "a" * 64,
                    "expectedHash": "b" * 64,
                    "atSec": time.time() - 3600,
                }
            ],
        }
    )
    assert old["results"][0]["reason"] == "restart-requested", old["results"]
    assert posted == ["http://10.0.0.1:8443"]

    # 刚刚下发过（同一 memo 键、时刻为现在）⇒ 窗内 dedup，不打扰节点
    dist_common.reset_restart_state()
    fresh = dist_upgrade_cli.run_scan(
        {
            "cfg_path": cfg_path,
            "branch": "goal-nn",
            "dirty": [],
            "expected_hash": "b" * 64,
            "seen": [
                {
                    "id": "stale",
                    "pingHash": "a" * 64,
                    "expectedHash": "b" * 64,
                    "atSec": time.time(),
                }
            ],
        }
    )
    assert fresh["results"][0]["reason"] == "dedup", fresh["results"]
    assert posted == ["http://10.0.0.1:8443"], "窗内不得再发 POST"
    dist_common.reset_restart_state()


def test_upgrade_branch_has_no_explicit_override_path() -> None:
    """评审更正（2026-09-26）：升级分支**只有一个来源**（训练机锁存）。

    此前是 `upgrade_branch_or(explicit)`，语义「显式值优先于锁存」——那是 2026-08-30 事故的
    载体（rl-config 里残留的 `'intent-ai'` 只要非空就盖掉锁存，把全部节点 reset 回旧代码）。
    读点已随 `policy.upgradeBranch` 删除；本次又把那个参数一并拿掉 ⇒ 坑在**签名层面**不再
    可能复现。这条用例盯的就是签名本身：谁想再引入「从配置/调用方传分支进来」这条路径，
    必须先改这条断言（= 一次有意识的决定，而不是顺手写一行 `or` 兜底）。
    """
    import inspect

    assert not hasattr(dist_common, "upgrade_branch_or"), "旧的「显式优先」入口不得复活"
    assert list(inspect.signature(dist_common.current_upgrade_branch).parameters) == []
    dist_common.set_upgrade_branch("goal-nn")
    try:
        assert dist_common.current_upgrade_branch() == "goal-nn"
        dist_common.set_upgrade_branch("")
        assert dist_common.current_upgrade_branch() == "", "锁存为空 = 不升级（调用方只告警）"
    finally:
        dist_common.set_upgrade_branch("")
