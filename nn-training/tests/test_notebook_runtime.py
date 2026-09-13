"""remote/notebook_runtime.py — 云端 notebook 运行时单元测试（2026-09-13）。

覆盖面（全部 hermetic——torch/IPython/urllib/subprocess/time 均注入 fake，不碰真
GPU/网络/子进程；work_dir 经 cfg 注入 tmp_path，绝不写仓库外 /tmp）：
  * resolve_device：torch 缺失 → cpu；无 CUDA 无 TPU → cpu；CUDA 多卡 +
    use_multi_gpu → cuda-dp；CUDA 单卡 → cuda；TPU（find_spec 注入）→ tpu +
    PJRT_DEVICE 环境位 + 占用者诊断；显式 device 覆盖。
  * _tpu_holders：/proc/*/fd 解析（readlink 命中 / 非vfio 跳过 / cmdline 解析）。
  * run_pull_worker：ping 401 → -2（不重启）；ping OK → supervise_worker 参数
    快照（--poll/--token-file 内容/--max-idle-sec = max(idle_floor, (hours-1)h)）；
    rc 透传；KeyboardInterrupt → 0。
  * run_push_worker：cloudflared 缺失且自动安装失败 → -2；serve 起不来 → -1
    （进程被 kill）；隧道 URL 提取上报；serve 干净退出 → 0。
  * run_notebook：rc 透传（超限停）；非零退避重启到上限（30/60/90s 序列）；
    -2/未知 mode 单次即停；0 不重启；KeyboardInterrupt 穿透但保活停机柄仍置位。
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.metadata
import importlib.util
import io
import os
import sys
import threading
import types
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest

import remote.notebook_runtime as nbr

# ------------------------------------------------------------------ fakes


class _FakeResp:
    """urlopen 的最小上下文管理器（.status/.read）。"""

    def __init__(self, status: int = 200) -> None:
        self.status = status

    def read(self) -> bytes:
        return b"{}"

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False


class _FakeClock:
    """假时钟：每次 .time() 推进 step 秒；.sleep() 记录并推进。"""

    def __init__(self, step: float = 2.0) -> None:
        self.now = 1000.0
        self.step = step
        self.sleeps: list[float] = []

    def time(self) -> float:
        self.now += self.step
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s

    def strftime(self, fmt: str) -> str:
        return "00:00:00"  # _log_default 时间戳（假钟下固定）


class _FakeProc:
    """Popen 的最小替身：poll 序列 + kill 标记。"""

    def __init__(self, poll_seq: list[int | None], pid: int = 4242) -> None:
        self._polls = list(poll_seq)
        self.pid = pid
        self.returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        if self._polls:
            self.returncode = self._polls.pop(0)
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _fake_torch(cuda_available: bool, n_gpu: int = 1, version: str = "2.5.0") -> types.ModuleType:
    m = types.ModuleType("torch")
    m.__version__ = version  # type: ignore[attr-defined]
    m.cuda = SimpleNamespace(  # type: ignore[attr-defined]
        is_available=lambda: cuda_available,
        device_count=lambda: n_gpu,
        get_device_name=lambda i: f"FakeGPU{i}",
    )
    return m


def _base_cfg(tmp_path: Path, **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "mode": "pull",
        "hub_url": "http://127.0.0.1:1",
        "hub_token": "tok",
        "push_port": 1,
        "push_token": "tok",
        "cloudflared_path": "",
        "device": "auto",
        "device_resolved": "cpu",  # run_pull/push_worker 直接消费（run_notebook 会先解析）
        "use_multi_gpu": False,
        "max_session_hours": 1,
        "poll_interval_sec": 5,
        "idle_floor_sec": 90,
        "max_worker_restarts": 2,
        "work_dir": str(tmp_path / "work"),
    }
    cfg.update(over)
    return cfg


def _fake_urlopen_401(req: Any, timeout: float = 0) -> _FakeResp:
    raise urllib.error.HTTPError(
        "http://hub/ping", 401, "unauthorized", http.client.HTTPMessage(), io.BytesIO(b"")
    )


# ------------------------------------------------------------------ resolve_device


def test_resolve_device_torch_missing_returns_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)  # import torch → ImportError
    assert nbr.resolve_device(_base_cfg(tmp_path), lambda m: None) == "cpu"


def test_resolve_device_no_cuda_no_tpu_returns_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=False))
    # torch_xla 未安装（本机即为真——find_spec 不注入）
    assert nbr.resolve_device(_base_cfg(tmp_path), lambda m: None) == "cpu"


def test_resolve_device_cuda_single_and_multi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True, n_gpu=2))
    assert nbr.resolve_device(_base_cfg(tmp_path), lambda m: None) == "cuda"  # 只用第 0 张
    cfg_multi = _base_cfg(tmp_path, use_multi_gpu=True)
    assert nbr.resolve_device(cfg_multi, lambda m: None) == "cuda-dp"  # >1 卡 + 开关


def test_resolve_device_cuda_explicit_override_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True, n_gpu=2))
    cfg = _base_cfg(tmp_path, device="cuda", use_multi_gpu=True)
    # 显式 "cuda" ≠ "cuda-dp"：显式指定优先，不悄悄包 DP
    assert nbr.resolve_device(cfg, lambda m: None) == "cuda"


def test_resolve_device_tpu_sets_pjrt_and_reports_holders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=False))
    monkeypatch.setattr(
        nbr, "_tpu_holders", lambda: [(123, "/dev/vfio/0", "python -m remote.worker")]
    )
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: (object() if name == "torch_xla" else real_find_spec(name)),
    )
    real_version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda dist: "1.9" if dist == "torch_xla" else real_version(dist),
    )
    monkeypatch.delenv("PJRT_DEVICE", raising=False)
    logs: list[str] = []
    assert nbr.resolve_device(_base_cfg(tmp_path), logs.append) == "tpu"
    assert os.environ.get("PJRT_DEVICE") == "TPU"  # 只影响子进程的位必须打上
    assert any("被占用" in m and "其它进程" in m for m in logs)  # 占用者诊断可见
    monkeypatch.delenv("PJRT_DEVICE", raising=False)


def test_resolve_device_tpu_present_but_cpu_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=False))
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: (object() if name == "torch_xla" else real_find_spec(name)),
    )
    assert nbr.resolve_device(_base_cfg(tmp_path, device="cpu"), lambda m: None) == "cpu"


# ------------------------------------------------------------------ _tpu_holders


def test_tpu_holders_parses_fd_links(monkeypatch: pytest.MonkeyPatch) -> None:
    links = ["/proc/123/fd/4", "/proc/999/fd/5", "/proc/777/fd/1"]
    monkeypatch.setattr(nbr.glob, "glob", lambda pattern: links)
    real_open = open

    def fake_readlink(p: Any) -> str:
        table = {links[0]: "/dev/vfio/0", links[1]: "", links[2]: "/dev/accel/0"}
        v = table.get(p)
        if v is None:
            raise OSError("bad link")
        return v

    def fake_open(path: Any, *a: Any, **kw: Any):  # type: ignore[no-untyped-def]
        if path == "/proc/123/cmdline":
            return io.BytesIO(b"python\0-m\0remote.worker")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(nbr.os, "readlink", fake_readlink)
    monkeypatch.setattr("builtins.open", fake_open)
    # vfio 命中（cmdline 解析）收录；非 vfio（空串 / accel）跳过
    assert nbr._tpu_holders() == [(123, "/dev/vfio/0", "python -m remote.worker")]


def test_tpu_holders_no_proc_no_crash() -> None:
    # Windows / 非 Linux：glob 空列表 → 空 holders（本机即为真）
    assert nbr._tpu_holders() == []


# ------------------------------------------------------------------ run_pull_worker


def test_pull_ping_401_returns_minus2_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_401)
    calls: list[list[str]] = []

    def fake_supervise(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    rc = nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None)
    assert rc == -2
    assert calls == []  # token 致命错误不进 worker


def test_pull_happy_path_argv_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    seen: dict[str, Any] = {}

    def fake_supervise(argv: list[str]) -> int:
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    rc = nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None)
    assert rc == 0
    argv = seen["argv"]
    # max_idle = max(idle_floor_sec=90, (1h 会话 - 1)h = 0) = 90
    assert argv[argv.index("--max-idle-sec") + 1] == "90"
    assert argv[argv.index("--poll") + 1] == "http://127.0.0.1:1"
    assert argv[argv.index("--device") + 1] == "cpu"
    token_file = Path(argv[argv.index("--token-file") + 1])
    assert token_file.exists() and token_file.read_text(encoding="utf-8") == "tok"
    assert str(tmp_path / "work") in str(token_file)  # token 文件落在注入的 work_dir


def test_pull_keyboard_interrupt_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))

    def fake_supervise(argv: list[str]) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    assert nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None) == 0


def test_pull_rc_passthrough(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    monkeypatch.setattr("remote.worker.supervise_worker", lambda argv: 5)
    assert nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None) == 5


def test_pull_max_idle_floor_with_long_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    seen: dict[str, Any] = {}
    def fake_supervise(argv: list[str]) -> int:
        seen.setdefault("argv", argv)
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    # 9h 会话：(9-1)h = 28800 > floor 90 → 取 28800
    nbr.run_pull_worker(_base_cfg(tmp_path, max_session_hours=9), lambda m: None)
    argv = seen["argv"]
    assert argv[argv.index("--max-idle-sec") + 1] == "28800"


# ------------------------------------------------------------------ run_push_worker


def test_push_cloudflared_missing_and_install_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr.subprocess, "getoutput", lambda cmd: "")
    monkeypatch.setattr(
        nbr.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no net"))
    )
    assert nbr.run_push_worker(_base_cfg(tmp_path), lambda m: None) == -2


def test_push_serve_not_ready_returns_minus1_and_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=10.0)
    monkeypatch.setattr(nbr, "time", clock)  # 只重绑本模块引用，不污染全局 time
    monkeypatch.setattr(nbr.subprocess, "getoutput", lambda cmd: "/usr/bin/cloudflared")
    created: list[_FakeProc] = []

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        proc = _FakeProc(poll_seq=[0]) if "--port" in cmd else _FakeProc(poll_seq=[None])
        created.append(proc)
        return proc

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(RuntimeError("refused")),
    )
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), lambda m: None
    )
    assert rc == -1
    assert created[0].killed is True  # 起不来的 serve 被 finally 回收


def test_push_tunnel_url_detected_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=5.0)  # 步长必须 < 隧道 60s 窗口——60 会让循环体零执行
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(nbr.subprocess, "getoutput", lambda cmd: "/usr/bin/cloudflared")
    serve = _FakeProc(poll_seq=[None, None, 0])

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        if "--logfile" in cmd:
            # 伪 cloudflared：往 --logfile 写隧道 URL（真进程行为）
            Path(cmd[cmd.index("--logfile") + 1]).write_text(
                "2026-09-13 INF | https://abc-def.trycloudflare.com", encoding="utf-8"
            )
        return serve if "--port" in cmd else _FakeProc(poll_seq=[None])

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    logs: list[str] = []
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), logs.append
    )
    assert rc == 0  # serve 干净退出
    assert any("https://abc-def.trycloudflare.com" in m for m in logs)  # URL 被提取上报


def test_push_serve_exits_with_rc_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=5.0)
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(nbr.subprocess, "getoutput", lambda cmd: "/usr/bin/cloudflared")
    serve = _FakeProc(poll_seq=[None, 3])

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        return serve if "--port" in cmd else _FakeProc(poll_seq=[None])

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), lambda m: None
    )
    assert rc == 3  # serve 退出码透传（等循环 poll 命中）
    assert serve.killed is False  # 已退出，无需 kill


# ------------------------------------------------------------------ run_notebook


def test_notebook_rc_passthrough_and_keepalive_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = threading.Event()
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    monkeypatch.setattr(nbr, "run_pull_worker", lambda cfg, log: 7)
    # max_worker_restarts=0：单次失败即停（不真睡退避）
    rc = nbr.run_notebook(_base_cfg(tmp_path, keepalive_stop=stop, max_worker_restarts=0))
    assert rc == 7
    assert stop.is_set()  # finally 语义：无论 rc 如何保活线程都要停


def test_notebook_crash_backoff_retries_to_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=1.0)
    monkeypatch.setattr(nbr, "time", clock)
    stop = threading.Event()
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    calls: list[int] = []

    def fake_runner(cfg: dict[str, Any], log: Any) -> int:
        calls.append(1)
        return 5

    monkeypatch.setattr(nbr, "run_pull_worker", fake_runner)
    rc = nbr.run_notebook(_base_cfg(tmp_path, keepalive_stop=stop, max_worker_restarts=3))
    assert rc == 5
    assert len(calls) == 4  # 首发 + 3 次重启
    assert clock.sleeps == [30, 60, 90]  # 退避 30s×attempt（封顶 300 不触达）


def test_notebook_minus2_stops_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=1.0)
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    calls: list[int] = []
    def fake_runner(cfg: dict[str, Any], log: Any) -> int:
        calls.append(1)
        return -2

    monkeypatch.setattr(nbr, "run_pull_worker", fake_runner)
    rc = nbr.run_notebook(_base_cfg(tmp_path))
    assert rc == -2
    assert len(calls) == 1  # 配置致命：单次即停
    assert clock.sleeps == []  # 不退避


def test_notebook_unknown_mode_single_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    assert nbr.run_notebook(_base_cfg(tmp_path, mode="sideways")) == -2


def test_notebook_keyboard_interrupt_still_stops_keepalive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = threading.Event()
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")

    def fake_runner(cfg: dict[str, Any], log: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(nbr, "run_pull_worker", fake_runner)
    with pytest.raises(KeyboardInterrupt):
        nbr.run_notebook(_base_cfg(tmp_path, keepalive_stop=stop))
    assert stop.is_set()  # 中断穿透，但 finally 清理保活


def test_notebook_zero_rc_no_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    calls: list[int] = []
    def fake_runner(cfg: dict[str, Any], log: Any) -> int:
        calls.append(1)
        return 0

    monkeypatch.setattr(nbr, "run_pull_worker", fake_runner)
    assert nbr.run_notebook(_base_cfg(tmp_path)) == 0
    assert len(calls) == 1  # 干净退出不重启


# ------------------------------------------------------------------ sha12


def test_sha12_matches_sha256_prefix() -> None:
    raw = b"battle-code-zip-bytes"
    assert nbr.sha12(raw) == hashlib.sha256(raw).hexdigest()[:12]
    assert len(nbr.sha12(raw)) == 12
