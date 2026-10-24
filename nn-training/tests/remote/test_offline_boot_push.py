"""tests/remote/test_offline_boot_push.py — `push_mode` 的接线判据（W4 / P1-9）。

W4 的核心不是「多一条腿」，而是**同一段代码换一种输入来源**：包/锚点/outbox 由 hub 的任务包
给，于是这条腿不得再探 hub、不得取包、不得取锚点、不得装 HTTP 打点、不得打交付 zip。
本文件把「不得」这一半钉成**抛异常的替身**（不是「没调用」的弱断言——漏接一条就会静默发
出站请求，而那正是 push 模式要逃离的那一侧）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.offline_boot as offline_boot
from tests.remote.test_offline_local_first import (  # type: ignore
    _artifacts,
    _local,
    _pack,
)


def _forbid(*_a, **_kw):
    raise AssertionError("push 腿不许碰 hub（探活/取包/取锚点都是 HTTP 出站）")


def _push_base(tmp_path: Path, outbox: Path, **extra) -> dict:
    base = {
        "course": "c5-gae",
        "work_dir": str(tmp_path / "w"),
        "local_root": str(_local(tmp_path)),
        "download_dir": str(tmp_path / "out"),
        "device": "cpu",
        "live_backfeed": True,  # 故意开着：push 分支必须自己盖掉它
        "eval_on_cloud": False,
        **extra,
    }
    return base


def _run_push_course(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pack: Path,
    outbox: Path,
    resume: Path | None = None,
    stop: Path | None = None,
    in_loop=None,
    cfg: dict | None = None,
) -> tuple[list[str], list[str]]:
    calls: list[str] = []

    def fake_code(p, log, code_dir=None, **kw):
        calls.append("ensure_code")

    def fake_ts(p, log, **kw):
        calls.append("ensure_ts_tree")

    monkeypatch.setattr(offline_boot, "obtain_pack", _forbid)
    monkeypatch.setattr(offline_boot, "hub_candidates", _forbid)
    monkeypatch.setattr(offline_boot, "probe_hub", _forbid)
    monkeypatch.setattr(offline_boot, "fetch_resume", _forbid)
    monkeypatch.setattr(offline_boot, "ensure_code", fake_code)
    monkeypatch.setattr(offline_boot, "ensure_ts_tree", fake_ts)
    seen: list[str] = []
    logs: list[str] = []

    def fake_run_loop(argv: list[str]) -> int:
        seen.extend(argv)
        if in_loop is not None:
            in_loop()
        return 0

    rc = offline_boot.run_one_course(
        _push_base(tmp_path, outbox, **(cfg or {})),
        {},
        logs.append,
        None,
        course="c5-gae",
        push_mode=True,
        assigned_pack=pack,
        assigned_resume=resume,
        push_outbox=outbox,
        stop_file=str(stop or (tmp_path / "stop")),
        run_loop_main=fake_run_loop,
    )
    assert rc == 0
    assert calls == ["ensure_code", "ensure_ts_tree"], calls
    return seen, logs


# ────────────────────────── argv 的形状 ──────────────────────────


def test_build_run_argv_outbox_never_carries_a_hub_url(tmp_path: Path) -> None:
    """`--outbox` 与 `--hub-url` **互斥**：同时给就变回 HTTP 补传（而 push 包里没有 token）。"""
    argv = offline_boot.build_run_argv(
        {"course": "c5-gae", "live_backfeed": True},
        None,
        tmp_path,
        "https://hub.example",
        str(tmp_path / "tok"),
        outbox=tmp_path / "ob",
        stop_file=str(tmp_path / "stop"),
    )
    assert argv.count("--outbox") == 1 and argv.count("--no-deliver") == 1
    assert argv.count("--hub-url") == 0 and argv.count("--hub-token-file") == 0
    assert argv[argv.index("--stop-file") + 1] == str(tmp_path / "stop")


def test_build_run_argv_without_outbox_keeps_the_http_leg(tmp_path: Path) -> None:
    argv = offline_boot.build_run_argv(
        {"course": "c5-gae", "live_backfeed": True},
        None,
        tmp_path,
        "https://hub.example",
        str(tmp_path / "tok"),
    )
    assert argv[argv.index("--hub-url") + 1] == "https://hub.example"
    assert argv.count("--outbox") == 0


# ────────────────────────── push 腿的输入来源 ──────────────────────────


def test_push_course_uses_the_assigned_pack_and_resume_without_any_hub_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """包/锚点都来自任务包；**任一 hub 调用都会让替身抛异常**（不是「没断言到」）。"""
    pack = _pack(tmp_path / "task-c5-gae.zip")
    resume = tmp_path / "resume"
    (resume / "it-002").mkdir(parents=True)
    outbox = tmp_path / "ob"
    argv, logs = _run_push_course(
        tmp_path, monkeypatch, pack=pack, outbox=outbox, resume=resume
    )
    assert argv[:2] == ["--bundle", str(pack)], argv  # 本机没有产物 ⇒ 包就是这段的计划来源
    assert argv[argv.index("--resume-dir") + 1] == str(resume)
    assert argv[argv.index("--outbox") + 1] == str(outbox)
    # 「没有探 hub」由 `_forbid` 直接钉住（任一 hub 调用都会 AssertionError），不再看日志。
    assert logs  # 日志面仍在（排障用），但判据不挂在措辞上


def test_push_course_with_local_artifacts_does_not_replay_the_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一段的中途重启：本机产物才是权威，包只作代码/TS 来源（不回放计划/清单）。"""
    _artifacts(_local(tmp_path))
    pack = _pack(tmp_path / "task-c5-gae.zip")
    argv, logs = _run_push_course(tmp_path, monkeypatch, pack=pack, outbox=tmp_path / "ob")
    assert argv.count("--bundle") == 0, argv
    assert logs


def test_push_course_without_a_pack_is_loud(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as e:
        offline_boot.run_one_course(
            _push_base(tmp_path, tmp_path / "ob"),
            {},
            lambda _m: None,
            None,
            course="c5-gae",
            push_mode=True,
            assigned_pack=tmp_path / "nope.zip",
            push_outbox=tmp_path / "ob",
        )
    assert str(e.value).startswith("[offline-push] 任务包缺失"), e.value


def test_push_course_skips_the_deliverable_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """交付面 = outbox；在慢挂点上再拷一个大 zip 正是 push 模式要移除的等待。"""
    calls: list[str] = []
    deliverable = offline_boot._load_deliverable()

    def _record_zip(*_a, **_kw):
        calls.append("zip")
        return None

    monkeypatch.setattr(deliverable, "package_deliverable", _record_zip)
    pack = _pack(tmp_path / "task-c5-gae.zip")
    _argv, logs = _run_push_course(tmp_path, monkeypatch, pack=pack, outbox=tmp_path / "ob")
    assert calls == []
    assert logs


# ────────────────────────── 本地打点 ──────────────────────────


def test_local_progress_sink_writes_seq_and_survives_reinstall(tmp_path: Path) -> None:
    """进度落文件、`seq` 跨重启续上（hub 按 `?since=N` 拉增量，冲回 1 会永久错过）。"""
    from common import progress_hook

    p = tmp_path / "progress.json"
    detach = offline_boot.install_local_progress_sink(p, lambda _m: None)
    assert progress_hook.report("round-done", it=3, done=1, total=2, force=True) is True
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["seq"] == 1 and doc["it"] == 3 and doc["kind"] == "round-done"
    detach()
    assert progress_hook.report("round-done", it=4) is False  # 撤销后不再写

    detach2 = offline_boot.install_local_progress_sink(p, lambda _m: None)
    progress_hook.report("round-start", it=4, force=True)
    assert json.loads(p.read_text(encoding="utf-8"))["seq"] == 2
    detach2()


def test_push_course_installs_the_local_sink_instead_of_the_http_pinger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """训练期间的上报口必须指向 `<outbox>/progress.json`，且退出后撤销注册。"""
    from common import progress_hook

    outbox = tmp_path / "ob"
    pack = _pack(tmp_path / "task-c5-gae.zip")
    seen: dict[str, object] = {}

    def in_loop() -> None:
        seen["reported"] = progress_hook.report("round-done", it=2, force=True)
        seen["file"] = (outbox / offline_boot.OFFLINE_PUSH_PROGRESS_NAME).exists()

    _run_push_course(tmp_path, monkeypatch, pack=pack, outbox=outbox, in_loop=in_loop)
    assert seen == {"reported": True, "file": True}, seen
    assert progress_hook.hook() is None  # 收线撤销：下一段/下一课不会报到别人账上


def test_progress_name_matches_the_protocol_constant() -> None:
    from common import protocol

    assert offline_boot.OFFLINE_PUSH_PROGRESS_NAME == protocol.OFFLINE_PUSH_PROGRESS_NAME


# ────────────────────────── run_loop 收 `--stop-file` ──────────────────────────


def test_run_loop_main_passes_stop_file_to_run_standalone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--stop-file` 不能在 CLI 这一层被静默丢掉（丢掉的形态是「取消永远不生效」）。"""
    import remote.run_loop as run_loop

    captured: dict[str, object] = {}
    monkeypatch.setattr(run_loop, "load_planned_manifest", lambda _root: ({"start_it": 0}, {}))
    monkeypatch.setattr(
        run_loop,
        "run_standalone",
        lambda **kw: (
            captured.update(kw),
            {"it_end": 0, "run_state": "noop", "artifacts": {"dir": "d", "zip": ""}},
        )[1],
    )
    stop = tmp_path / "stop"
    rc = run_loop.main(["--artifacts", str(tmp_path), "--stop-file", str(stop)])
    assert rc == 0
    assert captured["stop_file"] == str(stop), captured


def test_run_context_carries_stop_file(tmp_path: Path) -> None:
    """更窄的接线判据：`RunContext(stop_file=…)` 逐字保留（`plan_run._drive` 读它）。"""
    from remote.artifacts import ArtifactStore
    from remote.plan_handoff import RunContext

    store = ArtifactStore(tmp_path / "art", run_id="r1", log=lambda _m: None)
    ctx = RunContext(
        plan={},
        plan_sha256="p" * 64,
        manifest={},
        store=store,
        work_dir=tmp_path,
        stop_file=str(tmp_path / "stop"),
    )
    assert ctx.stop_file == str(tmp_path / "stop")
