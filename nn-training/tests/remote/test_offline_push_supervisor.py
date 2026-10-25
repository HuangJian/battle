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
from remote.push_register import NO_DISPATCH_NOTE


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


def test_status_line_only_speaks_when_the_state_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户口径（2026-10-25）：每 15s 一行**不变**的 `任务状态: phase=running …` 是刷屏。

    状态行只在 (phase/course/busy/rc) 变化时才打，另有 `STATUS_HEARTBEAT_SEC` 兜底；
    进度行在内容没变时也不重复打（真机：同一批 `2/2` 连打两拍）。
    """
    monkeypatch.setattr(sup, "find_cloudflared", lambda *a, **kw: "/bin/true")
    proc = _Proc()
    monkeypatch.setattr(
        sup, "start_quick_tunnel", lambda *_a, **_kw: (proc, "https://abc-def.trycloudflare.com")
    )
    monkeypatch.setattr(sup, "probe_offline_dispatch", lambda *_a, **_kw: True)
    logs: list[str] = []
    stop = threading.Event()
    # 让循环真跑几拍：原先靠 `threading.Timer(2.4, stop.set)` 等真墙钟（sleep 1s 一拍 ⇒
    # 白烧 2.4s）。现在把拍子本身注入进来——拍数就是判据需要的「多拍」，与机器快慢无关。
    pinned: dict[str, int] = {"n": 0}

    def _fast_sleep(_s: float) -> None:
        pinned["n"] += 1
        if pinned["n"] >= 3:
            stop.set()

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
        registry=_FakeReg(),  # type: ignore[arg-type]
        tick_sec=0,
        sleep=_fast_sleep,
    )
    assert rc == 0
    assert pinned["n"] >= 3, "循环没走够拍（状态行去重需要多拍才可证伪）"
    n = sum(1 for m in logs if m.startswith(sup.STATUS_PREFIX))
    assert n == 1, f"状态没变却打了 {n} 条状态行（刷屏）：{logs}"


def test_run_push_says_so_when_the_hub_has_no_dispatcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """注册成功 ≠ 会派单（2026-10-24 真机第二连击）。

    hub 的 argv 没有 `--offline-push` 时，它会**收下**节点、写进 `nodes[]`、也能 ping 通本机，
    然后一个单都不派；云机那边只剩「守候中…」。所以注册成功之后必须再问一句派发器在不在，
    并把「确定没挂」翻成一行可操作的话（该怎么重启、为什么重启控制台）。
    """
    monkeypatch.setattr(sup, "find_cloudflared", lambda *a, **kw: "/bin/true")
    proc = _Proc()
    monkeypatch.setattr(
        sup, "start_quick_tunnel", lambda *_a, **_kw: (proc, "https://abc-def.trycloudflare.com")
    )
    monkeypatch.setattr(sup, "probe_offline_dispatch", lambda *_a, **_kw: False)
    logs: list[str] = []
    stop = threading.Event()
    stop.set()
    rc = sup.run_push(
        {"work_dir": str(tmp_path), "push_port": 0, "push_token": "t", "node_id": "offline-test"},
        logs.append,
        keepalive_stop=stop,
        face_state=PushFaceState(work_dir=tmp_path / "face", log=logs.append),
        registry=_FakeReg(),  # type: ignore[arg-type]
        tick_sec=0,
    )
    assert rc == 0
    assert NO_DISPATCH_NOTE in logs, "hub 没挂派发器时必须明说，别让人守着 idle 干等"


def test_run_push_stays_quiet_when_the_dispatcher_is_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """挂载正常时**不**报那句警告（确定否定才说话，否则刷屏）。"""
    monkeypatch.setattr(sup, "find_cloudflared", lambda *a, **kw: "/bin/true")
    proc = _Proc()
    monkeypatch.setattr(
        sup, "start_quick_tunnel", lambda *_a, **_kw: (proc, "https://abc-def.trycloudflare.com")
    )
    monkeypatch.setattr(sup, "probe_offline_dispatch", lambda *_a, **_kw: True)
    logs: list[str] = []
    stop = threading.Event()
    stop.set()
    sup.run_push(
        {"work_dir": str(tmp_path), "push_port": 0, "push_token": "t", "node_id": "offline-test"},
        logs.append,
        keepalive_stop=stop,
        face_state=PushFaceState(work_dir=tmp_path / "face", log=logs.append),
        registry=_FakeReg(),  # type: ignore[arg-type]
        tick_sec=0,
    )
    assert NO_DISPATCH_NOTE not in logs

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


def test_child_output_is_teed_into_the_supervisor_log(tmp_path: Path) -> None:
    """★ 2026-10-25（用户之问）：「为什么 push 腿看不到 pull 那样子的 rollout/ppo 日志？」

    以前 `stdout=log_f` ⇒ 子进程整段输出被关进 `run.log`，notebook 里只剩 supervisor 的状态行；
    而 pull 腿是同进程跑，明细天然落在单元输出里。现在 tee：同一份字节**同时**进 run.log 与
    supervisor 日志（逐字、不加前缀）。
    """
    child = sup._Child()
    done = threading.Event()
    lines: list[str] = []

    child.spawn(
        [sys.executable, "-c", "print('rollout 进度 it103 采集局 6/17 局'); print('ppo 训练 4 遍')"],
        log_path=tmp_path / "run.log",
        env=dict(os.environ),
        on_exit=lambda _rc: done.set(),
        task_id="t2",
        log=lines.append,
    )
    assert done.wait(30), "子进程 30s 内没退出"
    # 等 tee 线程把最后一行写完（子进程退出与 tee 收尾是两个事件）
    end = time.time() + 10.0
    while time.time() < end and not any(m.startswith("rollout ") for m in lines):
        # sleep-ok: 轮询步长（等的是 tee 线程把那行写完）
        time.sleep(0.05)
    assert any(m.startswith("rollout ") for m in lines), lines
    assert any(m.startswith("ppo ") for m in lines), lines
    assert len((tmp_path / "run.log").read_text(encoding="utf-8").splitlines()) >= 2


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


def test_task_entry_validates_task_metadata_before_training(tmp_path: Path) -> None:
    """子进程侧的元数据校验（plan §9.3 留的那条）：坏包在**开跑前**响亮退出。

    两态都不得进 `run_one_course`：① 课名缺了（拿空课名开跑会把产物写到错误的落点）；
    ② `task.json` 不是合法 JSON（服务端已校验过，但子进程是独立进程，坏文件只可能在两
    者之间被换掉/写坏）。
    """
    t = _task_dir(tmp_path)
    task = json.loads((t / "task.json").read_text(encoding="utf-8"))
    del task["course"]
    (t / "task.json").write_text(json.dumps(task), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        task_mod.main(["--task-dir", str(t), "--outbox", str(t / "outbox")])
    assert str(e.value).startswith("[offline-push] task.json 缺 course")

    (t / "task.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(SystemExit) as e2:
        task_mod.main(["--task-dir", str(t), "--outbox", str(t / "outbox")])
    assert str(e2.value).startswith("[offline-push] 读不到任务元数据")
