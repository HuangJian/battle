"""hub 共享 `/code` 的取件口：**集群代码快照优先**（plan/cluster-code-snapshot §3.5）。

判据（plan §7.1 #9）：
 ① 有快照 ⇒ 快照就是答案，`course` 参数**不再影响结果**（消灭「取哪门课取决于发现顺序」）；
 ② 无快照/坏快照 ⇒ 回落旧行为（课程指定的 / 第一份真存在的）逐字节不变；
 ③ 都没有 ⇒ `None`（路由层 404，且措辞点名快照）。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from hub.queue import _HubQueue
from hub.store import _JobStore
from remote import code_snapshot as cs


def _store(root: Path, course: str) -> _JobStore:
    traj = root / course
    traj.mkdir(parents=True, exist_ok=True)
    return _JobStore(traj / "remote-jobs", traj / "training_log.jsonl")


def _legacy_code_zip(root: Path, course: str, payload: bytes) -> Path:
    p = root / course / "remote-jobs" / "code.zip"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def _write_snapshot(d: Path, payload: bytes = b"snapshot-code") -> Path:
    """手造一份合法快照（元数据 + 内容寻址 zip），返回 zip 路径。"""
    d.mkdir(parents=True, exist_ok=True)
    zp = d / "code.deadbeef1234.zip"
    zp.write_bytes(payload)
    (d / cs.META_NAME).write_text(
        json.dumps(
            {
                "magic": cs.SNAPSHOT_MAGIC,
                "proto": cs.SNAPSHOT_PROTO,
                "zip": zp.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "packed_at_epoch": 1.0,
                "anchor": {
                    "kind": "hub",
                    "pid": os.getpid(),
                    "started_at_epoch": 1.0,
                    # 读面只做**廉价** pid 存活口径（不在取件热路上起命令行子进程）⇒
                    # 指纹字段只需是同形的字符串。
                    "cmdline_sha12": "deadbeefcafe",
                },
            }
        ),
        encoding="utf-8",
    )
    return zp


@pytest.fixture
def hub(tmp_path: Path) -> tuple[_HubQueue, Path]:
    root = tmp_path / "traj"
    _legacy_code_zip(root, "a", b"legacy-a")
    _legacy_code_zip(root, "b", b"legacy-b")
    return _HubQueue({"a": _store(root, "a"), "b": _store(root, "b")}, order=["a", "b"]), root


def test_snapshot_wins_and_course_arg_stops_mattering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub
) -> None:
    q, _root = hub
    zp = _write_snapshot(tmp_path / "snap")
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(tmp_path / "snap"))
    assert q.shared_code_zip("a") == zp
    assert q.shared_code_zip("b") == zp
    assert q.shared_code_zip("") == zp
    assert q.shared_code_zip("不存在的课") == zp


def test_falls_back_to_legacy_scan_without_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub
) -> None:
    q, root = hub
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(tmp_path / "empty"))  # 目录都不存在
    assert q.shared_code_zip("b") == root / "b" / "remote-jobs" / "code.zip"
    assert q.shared_code_zip("") == root / "a" / "remote-jobs" / "code.zip"  # 发现顺序
    assert q.shared_code_zip("unknown") == root / "a" / "remote-jobs" / "code.zip"


def test_corrupt_snapshot_falls_back_to_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub
) -> None:
    """坏快照（sha 不符）⇒ 与「没有快照」同路，而不是把坏字节递出去。"""
    q, root = hub
    zp = _write_snapshot(tmp_path / "snap")
    zp.write_bytes(b"tampered")
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(tmp_path / "snap"))
    assert q.shared_code_zip("b") == root / "b" / "remote-jobs" / "code.zip"


def test_no_snapshot_no_legacy_means_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """都没有 ⇒ `None`，且经**真 handler** 的 404 措辞要点名「快照未建立」。

    为什么走 handler 而不是读 `blob.py` 源码：判据要落在**行为**上（真假源码文本对不上设计），
    所以复用 `tests/hub/test_hub_routes_split.py` 的 `_Probe`（真 `HubHandler` 子类，只把
    写出去的方法换成记录）——同一份路由接线，不另造一个假 handler。
    """
    from tests.hub.test_hub_routes_split import _Probe

    root = tmp_path / "traj"
    q = _HubQueue({"a": _store(root, "a")}, order=["a"])
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(tmp_path / "empty"))
    assert q.shared_code_zip("a") is None
    rec = _Probe("/code", hub=q).auth(True)
    rec._get_shared_code()
    assert len(rec.calls_json) == 1 and rec.calls_json[0][1] == 404
    err = rec.calls_json[0][0]
    assert isinstance(err, dict) and str(err["error"]).startswith(
        "no shared code zip — 集群代码快照未建立"
    )
