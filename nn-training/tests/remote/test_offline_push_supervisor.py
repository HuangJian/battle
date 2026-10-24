"""tests/remote/test_offline_push_supervisor.py — supervisor + 子进程入口（W4）。

supervisor 的那几件事都**只影响「这台机器还能不能接活」**，不该拖训练陪跑 ⇒ 用例用注入替身
（隧道/注册/子进程）把装配判据钉住，不真起 cloudflared、不真连 hub。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.offline_push as sup
import remote.offline_push_task as task_mod
from remote.offline_push_face import PushFaceState


class _FakeReg:
    def __init__(self) -> None:
        self.node_id = "offline-test"
        self.kind = "offline_push"
        self.ensure_calls: list[tuple[str, str]] = []
        self.unregistered = 0
        self.give_up = False

    def ensure(self, url: str, key: str, *, force: bool = False) -> bool:
        self.ensure_calls.append((url, key))
        return True

    def unregister(self) -> None:
        self.unregistered += 1


class _Proc:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15


def test_redacted_cfg_strips_credentials_but_keeps_execution_keys() -> None:
    cfg = {
        "hub_token": "secret",
        "push_token": "secret2",
        "ts_authkey": "secret3",
        "device": "auto",
        "max_iters": 5,
        "eval_on_cloud": True,
        "course": ["a", "b"],
        "hub_url": "https://hub.example",
    }
    out = sup.redacted_cfg(cfg)
    assert set(out) & {"hub_token", "push_token", "ts_authkey"} == set()
    assert out["device"] == "auto" and out["max_iters"] == 5 and out["eval_on_cloud"] is True
    assert out["course"] == ["a", "b"]  # 多课程列表是执行参数，不是凭据


def test_run_push_without_cloudflared_returns_minus2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sup, "find_cloudflared", lambda *a, **kw: "")
    rc = sup.run_push(
        {"work_dir": str(tmp_path), "push_port": 0, "push_token": "t"},
        lambda _m: None,
        face_state=PushFaceState(work_dir=tmp_path / "face", log=lambda _m: None),
        registry=_FakeReg(),  # type: ignore[arg-type]
    )
    assert rc == -2


def test_run_push_registers_the_tunnel_then_unregisters_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sup, "find_cloudflared", lambda *a, **kw: "/bin/true")
    proc = _Proc()
    monkeypatch.setattr(
        sup,
        "start_quick_tunnel",
        lambda *_a, **_kw: (proc, "https://abc-def.trycloudflare.com"),
    )
    reg = _FakeReg()
    stop = threading.Event()
    stop.set()  # 一拍都不守候：注册之后立刻收摊
    logs: list[str] = []
    rc = sup.run_push(
        {
            "work_dir": str(tmp_path),
            "push_port": 0,
            "push_token": "t",
            "node_id": "offline-test",
            "session_budget_sec": 0,
        },
        logs.append,
        keepalive_stop=stop,
        face_state=PushFaceState(work_dir=tmp_path / "face", log=logs.append),
        registry=reg,  # type: ignore[arg-type]
        tick_sec=0,
    )
    assert rc == 0
    assert reg.ensure_calls and reg.ensure_calls[0][0] == "https://abc-def.trycloudflare.com"
    assert reg.unregistered == 1
    assert proc.returncode is not None  # 隧道被收线
    assert logs  # 守候/收摊的日志面仍在（但不把判据挂在措辞上）


def test_child_spawn_runs_a_real_process_and_reports_its_exit_code(tmp_path: Path) -> None:
    child = sup._Child()
    got: list[int] = []
    done = threading.Event()

    def on_exit(rc: int) -> None:
        got.append(rc)
        done.set()

    child.spawn(
        [sys.executable, "-c", "print('hello-child')"],
        log_path=tmp_path / "run.log",
        env=dict(os.environ),
        on_exit=on_exit,
        task_id="t1",
        log=lambda _m: None,
    )
    assert done.wait(30), "子进程 30s 内没退出"
    assert got == [0]
    assert (tmp_path / "run.log").read_text(encoding="utf-8") == "hello-child\n"
    assert child.running() is False


def test_child_stop_writes_the_stop_file_and_terminates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = sup._Child()
    child.spawn(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        log_path=tmp_path / "run.log",
        env=dict(os.environ),
        on_exit=lambda _rc: None,
        task_id="t2",
        log=lambda _m: None,
    )
    t0 = time.time()
    child.request_stop(stop_file=tmp_path / "stop", grace=1.0)
    assert (tmp_path / "stop").exists()
    # timing-ok: 上界兜底（grace=1s 后 request_stop 必须回来；卡死才是 bug）
    assert time.time() - t0 < 30


# ────────────────────────── 子进程入口的接线 ──────────────────────────


def _task_dir(tmp_path: Path) -> Path:
    import hashlib

    t = tmp_path / "tasks" / "0123456789abcdef"
    (t / "pack").mkdir(parents=True)
    (t / "resume" / "it-000").mkdir(parents=True)
    pack = t / "pack" / "task-c5-gae.zip"
    pack.write_bytes(b"PK-fake")
    sha = hashlib.sha256(pack.read_bytes()).hexdigest()
    (t / "task.json").write_text(
        json.dumps(
            {
                "proto": "offline_push_v1",
                "task_id": "0123456789abcdef",
                "course": "c5-gae",
                "run_id": "r1",
                "pack_name": "task-c5-gae.zip",
                "pack_sha256": sha,
                "it": 0,
                "end_it": 4,
            }
        ),
        encoding="utf-8",
    )
    (t / "cfg.json").write_text(json.dumps({"device": "cpu", "max_iters": 2}), encoding="utf-8")
    return t


def test_task_entry_wires_push_mode_without_any_hub_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import remote.offline_boot as boot

    captured: dict[str, object] = {}

    def fake_run(cfg, creds, log, stop, **kw):
        captured.update({"cfg": cfg, "creds": creds, **kw})
        return 0

    monkeypatch.setattr(boot, "run_one_course", fake_run)
    t = _task_dir(tmp_path)
    outbox = t / "outbox"
    rc = task_mod.main(
        [
            "--task-dir",
            str(t),
            "--outbox",
            str(outbox),
            "--stop-file",
            str(t / "stop"),
        ]
    )
    assert rc == 0
    assert captured["push_mode"] is True
    assert captured["course"] == "c5-gae"
    assert captured["creds"] == {}
    assert Path(str(captured["assigned_pack"])).name == "task-c5-gae.zip"
    assert Path(str(captured["push_outbox"])) == outbox
    assert Path(str(captured["assigned_resume"])) == t / "resume"
    assert captured["stop_file"] == str(t / "stop")
    assert captured["push_floor_it"] == 0  # 老任务包没有 resume_it ⇒ 不跳轮
    cfg = captured["cfg"]
    assert isinstance(cfg, dict) and cfg["device"] == "cpu"


def test_task_entry_passes_resume_it_as_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F7：任务包的 `resume_it`（hub 派发时的 cursor）→ `--floor-it` 的同一条链的起点。"""
    import remote.offline_boot as boot

    captured: dict[str, object] = {}

    def fake_run(_cfg, _creds, _log, _stop, **kw):
        captured.update(kw)
        return 0

    monkeypatch.setattr(boot, "run_one_course", fake_run)
    t = _task_dir(tmp_path)
    task = json.loads((t / "task.json").read_text(encoding="utf-8"))
    task["resume_it"] = 3
    (t / "task.json").write_text(json.dumps(task), encoding="utf-8")
    rc = task_mod.main(["--task-dir", str(t), "--outbox", str(t / "outbox")])
    assert rc == 0 and captured["push_floor_it"] == 3


def test_task_entry_is_loud_when_the_cfg_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = _task_dir(tmp_path)
    (t / "cfg.json").unlink()
    with pytest.raises(SystemExit) as e:
        task_mod.main(["--task-dir", str(t), "--outbox", str(t / "outbox")])
    assert str(e.value).startswith("[offline-push] 读不到训练参数")
