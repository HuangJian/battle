"""worker 的离线能力自报（2026-09-19 离线训练模式）。

用户口径：离线模式「也支持带特别标识的云端 worker 在线领取」。标识 = **能力声明**
（「我能自己跑完整段」`kind="run"`），不是课程绑定——带标 worker 照样领在线课。

本文件钉**跨层契约的两半**：
  ① worker 侧：`--offline` → `/jobs/next` 带 `X-Battle-Offline: 1`（缺省**不带**，逐字不变）；
  ② 端到端：真 hub（进程内 HTTP）+ 真 `poll_job` —— 离线课对无标 poller 不可见、对带标可见。

漏了哪一半的代价都是**静默**的：漏发头 = 离线课永远没人领（看着像「节点都不在线」），
漏放行 = 离线课成了谁都领不到的坟墓（看着像「还没轮到」）。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import remote.worker as W
from remote.hub_server import _HubQueue, make_server
from remote.protocol import (
    AUTH_HEADER,
    COURSE_ENABLE_MARKER,
    COURSE_MODE_OFFLINE,
    OFFLINE_CAP_HEADER,
    OFFLINE_CAP_VALUE,
    WORKER_ID_HEADER,
)

TOKEN = "sekret"


# ---------------------------------------------------------------- ① 头


def test_poll_job_sends_capability_header_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """`offline_ok=True` ⇒ 带上能力头（值取协议常量，不在 worker 里再写一份字面量）。"""
    seen: list[dict] = []

    def _fake_request(base, token, path, timeout=30.0, data=None, method=None, headers=None):
        seen.append({"path": path, "headers": dict(headers or {})})
        return 200, b'{"job_id": null, "halt": false}'

    monkeypatch.setattr(W, "_request", _fake_request)
    assert W.poll_job("http://hub", "t", worker_id="host:1", offline_ok=True) is None
    assert seen[0]["headers"][OFFLINE_CAP_HEADER] == OFFLINE_CAP_VALUE
    assert seen[0]["headers"][WORKER_ID_HEADER] == "host:1"


def test_poll_job_sends_no_capability_header_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧调用方（不传 offline_ok）不发头 —— hub 侧按无能力处理，行为逐字节不变。"""
    seen: list[dict] = []

    def _fake_request(base, token, path, timeout=30.0, data=None, method=None, headers=None):
        seen.append(dict(headers or {}))
        return 200, b'{"job_id": null, "halt": false}'

    monkeypatch.setattr(W, "_request", _fake_request)
    assert W.poll_job("http://hub", "t") is None
    assert OFFLINE_CAP_HEADER not in seen[0]


def test_worker_loop_forwards_offline_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """接线断言：主循环把 `offline_ok` 透传给 poll_job（漏了它 = 功能静默失效）。"""
    seen: list[dict] = []

    def _fake_poll(*a, **k):
        seen.append(k)
        return None  # once=True ⇒ 立刻干净退出

    monkeypatch.setattr(W, "poll_job", _fake_poll, raising=True)
    n = W.worker_loop(
        "http://hub", "tok", work_dir=Path("/tmp/x"), poll_sec=0.0, once=True, offline_ok=True
    )
    assert n == 0
    assert seen[0]["offline_ok"] is True

    seen.clear()
    W.worker_loop("http://hub", "tok", work_dir=Path("/tmp/x"), poll_sec=0.0, once=True)
    assert seen[0]["offline_ok"] is False, "缺省必须无能力（保守方向）"


def test_main_offline_flag_reaches_worker_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI：`--offline` → `worker_loop(offline_ok=True)`（子进程分支就是 notebook 走的那条）。"""
    seen: dict[str, object] = {}

    def _fake_loop(*a, **k):
        seen.update(k)
        raise SystemExit(0)

    monkeypatch.setattr(W, "worker_loop", _fake_loop, raising=True)
    monkeypatch.setenv("REMOTE_WORKER_CHILD", "1")
    tok = tmp_path / "t"
    tok.write_text(TOKEN, encoding="utf-8")
    monkeypatch.setattr(
        W.sys,
        "argv",
        ["remote_worker", "--poll", "http://x", "--token-file", str(tok), "--offline"],
    )
    with pytest.raises(SystemExit):
        W.main()
    assert seen["offline_ok"] is True


def test_notebook_pull_worker_adds_offline_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """notebook 的 pull 分支：`CFG["offline_worker"]` ⇒ 监督器 argv 带 `--offline`。"""
    import remote.notebook_runtime as nbr

    captured: list[list[str]] = []

    @contextmanager
    def _fake_open(req, timeout=15.0):
        class _Resp:
            def read(self) -> bytes:
                return b"ok"

        yield _Resp()

    monkeypatch.setattr(nbr, "_hub_open", _fake_open, raising=True)
    # `supervise_worker` 是**函数内**导入的（`from remote.worker import …`）⇒ 补它的源模块
    def _fake_supervise(argv):
        captured.append(list(argv))
        return 0

    monkeypatch.setattr(W, "supervise_worker", _fake_supervise, raising=True)
    cfg = {
        "hub_url": "http://hub",
        "hub_token": TOKEN,
        "work_dir": "/tmp/x",
        "device_resolved": "cpu",
        "idle_floor_sec": 60,
        "max_session_hours": 2,
        "poll_interval_sec": 1,
    }
    assert nbr.run_pull_worker({**cfg, "offline_worker": True}, lambda _m: None) == 0
    assert "--offline" in captured[-1]
    assert nbr.run_pull_worker(cfg, lambda _m: None) == 0
    assert "--offline" not in captured[-1], "缺省不带标"


# ---------------------------------------------------------------- ② 端到端


def _boot(tmp_path: Path) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", hub, srv


def test_run_job_forwards_the_hub_course_into_the_backfeed() -> None:
    """worker 把 `/jobs/next` 里的课程键透进 `run_plan_job`（补传的归位键）。

    为什么用源码断言：链路中段是「跑完一整段的真 PPO」——单测里跑不起来，而这一跳断掉的
    表现极其隐。：多课程 hub 下每条补传都被 400「无法归属课程」拒掉，节点侧补传整体停用，
    而训练本身完全正常（只有控制台看不到段内进度）。端到端那条在 `e2e/test_offline_training_e2e.py`。
    """
    root = Path(__file__).resolve().parent.parent
    src = (root / "remote" / "worker.py").read_text(encoding="utf-8")
    assert 'hub_course=str(job.get("course") or "")' in src
    # 下游每一跳都真的接这个形参（漏一跳 = 云机上 TypeError，或值静默丢掉）
    run_loop_src = (root / "remote" / "run_loop.py").read_text(encoding="utf-8")
    assert run_loop_src.count('hub_course: str = ""') == 3  # run_plan_job/open_run_context/run_standalone
    assert "hub_course=hub_course" in run_loop_src
    assert "course=hub_course" in run_loop_src  # make_deliverer 那一跳
    assert "hub_course=args.hub_course" in run_loop_src  # CLI（全离线包那条腿）


def _publish_offline_course(root: Path, course: str, jid: str) -> None:
    """在盘上造一门**已开课**的离线课（开课标记 + `remote-jobs/` + jsonl + kind=run 的 job）。"""
    from remote.hub_server import _JobStore

    job_root = root / course / "remote-jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    jsonl = root / course / "training_log.jsonl"
    jsonl.touch()
    # 开课标记 = hub/训练侧的「在训」闸（2026-09-20）；不写它，hub 不登记这门课。
    (root / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    store = _JobStore(job_root, jsonl)
    store.publish(
        jid,
        {
            "job_id": jid,
            "kind": "run",
            "run_id": "r1",
            "it": 1,
            "course": course,
            "data_fp": "d",
            "init_weights_fp": "w",
            "commit": "c" * 40,
            "code_sha256": "e" * 64,
            "ts_code_sha256": "f" * 64,
        },
        b"PK\x03\x04fake",
    )


def test_offline_course_is_invisible_to_plain_worker_and_claimable_by_marked(
    tmp_path: Path,
) -> None:
    """真 HTTP 端到端：无标 poller 领不到离线课；带标 poller 领得到（同一份 job）。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _publish_offline_course(tmp_path, "c5-gae", "j" * 16)
        assert hub.discover() == ["c5-gae"]
        assert hub.set_mode("c5-gae", COURSE_MODE_OFFLINE) is True

        # 无标：hub 说「没有可领的 job」（离线课不实时派发，也不是谁都领得到的池子）
        assert W.poll_job(base, TOKEN, worker_id="plain") is None
        # 带标：同一份 job 立刻到手，并且响应自报归属课程（对账用）
        got = W.poll_job(base, TOKEN, worker_id="marked", offline_ok=True)
        assert got is not None and got["job_id"] == "j" * 16
        assert got["course"] == "c5-gae"
        assert got["manifest"]["kind"] == "run"
    finally:
        srv.shutdown()
        srv.server_close()
