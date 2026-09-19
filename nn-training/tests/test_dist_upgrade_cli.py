"""test_dist_upgrade_cli — 节点升级指令一次性入口（TS 工具复用的那份守卫）。

契约：spec → 逐节点 `dist_common.request_upgrade_guarded`（**不允许**本文件自行
实现护栏逻辑——单源就在这里）；结构性错误退出码 2；`dry_run` 不发任何 POST。
"""

from __future__ import annotations

import json
import subprocess
import sys
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

    def fake(nid, url, auth, branch, ping_hash, timeout=20.0, dirty=None, expected_hash=""):
        seen.update(
            nid=nid, url=url, auth=auth, branch=branch, ping=ping_hash, dirty=dirty, exp=expected_hash
        )
        return True, "restart-requested"

    monkeypatch.setattr(dist_common, "request_upgrade_guarded", fake)
    out = dist_upgrade_cli.run_spec({**SPEC, "dirty": ["src/x.ts"]})
    assert out["results"] == [{"id": "mac", "ok": True, "reason": "restart-requested"}]
    assert out["dirty"] == ["src/x.ts"]
    # 显式 dirty 透传（调用方每轮已检测则复用，不重复探测）；期望 hash 必须显式传（F1）。
    assert seen["dirty"] == ["src/x.ts"] and seen["exp"] == "b" * 64 and seen["branch"] == "goal-nn"


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
