"""tests/remote/test_offline_bc_course.py — BC 课程级 drain（2026-11-12）。

与 `test_offline_bc_leg.py`（空档兜底腿）互补：这里钉的是**主路径**——

  * `_run_batch` 见到 `kind=bc` 就把整门课交给 `run_bc_course`，不 `claim_course`；
  * `run_bc_course` 反复领**本课**的 BC job，收到完成的课程行才收工；
  * 课程被别的盘独占 / 类型不是 bc / 没 hub ⇒ 一枪不发；
  * `_run_bc_once` 用**本地谓词**把 `course` 透传给 `worker_loop`（请求仍不带课程）；
  * `acquire_job(accept=…)` 真的会把别的课的候选筛掉——课程只来自响应。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from remote import job_lifecycle as jl
from remote import offline_boot as ob
from remote import worker as worker_mod


def _ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ob, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(ob, "worker_id_of", lambda work, log: "w1")


def _row(**kw: Any) -> dict:
    base = {"course": "bc1", "kind": "bc", "state": "ready", "bc_owner": "", "bc_job": ""}
    base.update(kw)
    return base


def test_run_auto_puts_bc_and_rl_in_the_same_batch_with_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同等对待的接线：同一份清单里的 BC 行与 RL 行一起进 `_run_batch`（带 kind）。"""
    seen: dict[str, Any] = {}

    def fake_resolve(cfg: dict, creds: dict, log: Any, **kw: Any) -> tuple[list, list, list]:
        return (
            [
                {"course": "rl1", "pack_sha256": "aa" * 32, "kind": "rl"},
                {"course": "bc1", "pack_sha256": "", "kind": "bc"},
            ],
            [],
            [],
        )

    def fake_batch(*a: Any, **kw: Any) -> int:
        seen["courses"] = list(a[4])
        seen["kinds"] = kw.get("kinds")
        raise SystemExit("batch-ran")

    monkeypatch.setattr(ob, "resolve_courses", fake_resolve)
    monkeypatch.setattr(ob, "worker_id_of", lambda work, log: "w1")
    monkeypatch.setattr(ob, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(ob, "_run_batch", fake_batch)
    with pytest.raises(SystemExit, match="batch-ran"):
        ob._run_auto({}, {"HUB_TOKEN": "t"}, lambda _m: None, None)
    assert seen["courses"] == ["rl1", "bc1"]
    assert seen["kinds"] == {"rl1": "rl", "bc1": "bc"}


def test_run_batch_routes_a_bc_course_to_the_bc_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_bc(cfg: dict, creds: dict, log: Any, stop: Any, *, course: str) -> int:
        seen.append(course)
        return 0

    def no_lease(*a: Any, **kw: Any) -> Any:
        raise AssertionError("BC 课程不该 claim_course")

    monkeypatch.setattr(ob, "run_bc_course", fake_bc)
    monkeypatch.setattr(ob, "claim_course", no_lease)
    rc = ob._run_batch(
        {}, {}, lambda _m: None, None, ["bc1"], multi=False, kinds={"bc1": "bc"}
    )
    assert rc == 0
    assert seen == ["bc1"]


def test_run_bc_course_bootstraps_code_before_the_first_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冷启动：没有任务包时也要能从 hub `/code` 取到同一权威源的代码。"""
    _ready(monkeypatch)
    monkeypatch.setattr(ob, "_bc_code_ready", lambda: False)
    rows = [_row(), _row(state="completed")]
    monkeypatch.setattr(ob, "fetch_task_list", lambda *a, **kw: [rows.pop(0)])
    seen: dict[str, Any] = {}

    def fake_ensure(pack: Any, log: Any, **kw: Any) -> None:
        seen.update(pack=pack, **kw)

    monkeypatch.setattr(ob, "ensure_code", fake_ensure)
    monkeypatch.setattr(ob, "_run_bc_once", lambda *a, **kw: 1)
    assert ob.run_bc_course({}, {"HUB_TOKEN": "t"}, lambda _m: None, None, course="bc1") == 0
    assert seen["pack"] is None
    assert seen["hub"] == "http://hub" and seen["token"] == "t"


def test_run_bc_course_stops_on_a_completed_course_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready(monkeypatch)
    monkeypatch.setattr(
        ob, "fetch_task_list", lambda *a, **kw: [_row(state="completed")]
    )
    touched: list[int] = []

    def boom(*a: Any, **kw: Any) -> int:
        touched.append(1)
        return 1

    monkeypatch.setattr(ob, "_run_bc_once", boom)
    assert ob.run_bc_course({}, {"HUB_TOKEN": "t"}, lambda _m: None, None, course="bc1") == 0
    assert touched == []


def test_run_bc_course_drains_until_the_course_is_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready(monkeypatch)
    rows = [_row(), _row(state="completed")]
    monkeypatch.setattr(ob, "fetch_task_list", lambda *a, **kw: [rows.pop(0)])
    seen: list[str] = []

    def run_once(cfg: dict, hub: str, token: str, log: Any, *, course: str) -> int:
        seen.append(course)
        return 1

    monkeypatch.setattr(ob, "_run_bc_once", run_once)
    assert ob.run_bc_course({}, {"HUB_TOKEN": "t"}, lambda _m: None, None, course="bc1") == 0
    assert seen == ["bc1"]


def test_run_bc_course_leaves_a_course_owned_by_another_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready(monkeypatch)
    monkeypatch.setattr(
        ob, "fetch_task_list", lambda *a, **kw: [_row(bc_owner="w2", bc_job="j1")]
    )
    monkeypatch.setattr(
        ob, "_run_bc_once", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不该领"))
    )
    assert ob.run_bc_course({}, {"HUB_TOKEN": "t"}, lambda _m: None, None, course="bc1") == 0


def test_run_bc_course_rejects_a_rl_row_with_the_same_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready(monkeypatch)
    monkeypatch.setattr(ob, "fetch_task_list", lambda *a, **kw: [_row(kind="rl")])
    monkeypatch.setattr(
        ob, "_run_bc_once", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不该领"))
    )
    assert ob.run_bc_course({}, {"HUB_TOKEN": "t"}, lambda _m: None, None, course="bc1") == 0


def test_run_bc_course_without_hub_or_token_is_a_quiet_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ob, "hub_candidates", lambda cfg, creds: [])
    assert ob.run_bc_course({}, {}, lambda _m: None, None, course="bc1") == 0
    monkeypatch.setattr(ob, "hub_candidates", lambda cfg, creds: ["http://hub"])
    assert ob.run_bc_course({}, {}, lambda _m: None, None, course="bc1") == 0


def test_run_bc_course_propagates_a_job_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready(monkeypatch)
    monkeypatch.setattr(ob, "fetch_task_list", lambda *a, **kw: [_row()])
    monkeypatch.setattr(ob, "_run_bc_once", lambda *a, **kw: -1)
    assert ob.run_bc_course({}, {"HUB_TOKEN": "t"}, lambda _m: None, None, course="bc1") == 1


def test_run_bc_once_passes_the_course_to_worker_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_loop(*a: Any, **kw: Any) -> int:
        seen.update(kw)
        return 1

    monkeypatch.setattr(worker_mod, "worker_loop", fake_loop)
    monkeypatch.setattr(ob, "CODE_DIR", str(tmp_path))
    # 显式 cpu：本用例只钉形参透传，别让设备探测（子进程）拖慢它。
    assert ob._run_bc_once(
        {"device": "cpu"}, "http://hub", "t", lambda _m: None, course="bc1"
    ) == 1
    filt = seen["job_filter"]
    assert filt({"course": "bc1"}) is True
    assert filt({"course": "rl1"}) is False
    assert seen["once"] is True and seen["role"] == "offline"


def test_run_bc_once_resolves_auto_device_before_the_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★ 2026-11-13 现场（TPU 云机）：`auto` 原样传进 `worker_loop` ⇒ worker 只做兜底归一化
    （「--device 'auto' 未经解析，worker 兜底为 cpu」）。

    引导层必须在 `worker_loop` 之前解析 `auto`（与 RL 腿同一份 `resolve_device`，按
    CUDA → TPU → CPU 的序，`use_multi_gpu=False` 保持 BC「多卡显式 cuda-dp」的纪律）。
    """
    seen: dict[str, Any] = {}

    def fake_loop(*a: Any, **kw: Any) -> int:
        seen.update(kw)
        return 1

    monkeypatch.setattr(worker_mod, "worker_loop", fake_loop)
    monkeypatch.setattr(ob, "CODE_DIR", str(tmp_path))
    from remote import bc_job as bc_job
    from remote import notebook_runtime as nr

    monkeypatch.setattr(bc_job, "resolve_ppo_backend", lambda _b: "torch")

    def fake_resolve(cfg: dict, _log: Any) -> str:
        seen["resolve_cfg"] = dict(cfg)
        return "tpu"

    monkeypatch.setattr(nr, "resolve_device", fake_resolve)
    assert ob._run_bc_once({"device": "auto"}, "http://hub", "t", lambda _m: None) == 1
    assert seen["device"] == "tpu"
    assert seen["resolve_cfg"]["use_multi_gpu"] is False, (
        "BC 的多卡必须显式 cuda-dp（auto 不得悄悄开 DataParallel）"
    )


def test_run_bc_once_keeps_an_explicit_tpu_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """显式 `CFG.device='tpu'` 不再经过探测/归一化（TPU 是用户点名要的）。"""
    seen: dict[str, Any] = {}

    def fake_loop(*a: Any, **kw: Any) -> int:
        seen.update(kw)
        return 1

    monkeypatch.setattr(worker_mod, "worker_loop", fake_loop)
    monkeypatch.setattr(ob, "CODE_DIR", str(tmp_path))
    from remote import notebook_runtime as nr

    def _boom(_cfg: dict, _log: Any) -> str:
        raise AssertionError("显式 tpu 不该再探测设备")

    monkeypatch.setattr(nr, "resolve_device", _boom)
    assert ob._run_bc_once({"device": "tpu"}, "http://hub", "t", lambda _m: None) == 1
    assert seen["device"] == "tpu"


def test_acquire_job_filters_candidates_to_the_requested_course(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jl,
        "peek_jobs",
        lambda *a, **kw: (
            [{"job_id": "a", "course": "rl1"}, {"job_id": "b", "course": "bc1"}],
            False,
        ),
    )
    monkeypatch.setattr(jl, "request_priority", lambda *a, **kw: {"priorities": {}})
    seen: dict[str, Any] = {}

    def fake_claim(base: str, token: str, jid: str, **kw: Any) -> dict:
        seen["jid"] = jid
        return {
            "job_id": jid,
            "manifest": {"job_id": jid},
            "lease_token": "l",
            "course": "bc1",
        }

    monkeypatch.setattr(jl, "claim_job", fake_claim)
    got = jl.acquire_job(
        "http://hub",
        "t",
        worker_id="w1",
        role="offline",
        accept=lambda c: str(c.get("course") or "") == "bc1",
    )
    assert seen["jid"] == "b", "别的课的候选必须先被筛掉，否则会领串门"
    assert got is not None and got["job_id"] == "b"


def test_acquire_job_does_not_claim_when_no_candidate_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jl,
        "peek_jobs",
        lambda *a, **kw: ([{"job_id": "a", "course": "rl1"}], True),
    )
    monkeypatch.setattr(
        jl, "claim_job", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不该 claim"))
    )
    got = jl.acquire_job(
        "http://hub",
        "t",
        worker_id="w1",
        role="offline",
        accept=lambda c: str(c.get("course") or "") == "bc1",
    )
    assert got == {"halt": True}
