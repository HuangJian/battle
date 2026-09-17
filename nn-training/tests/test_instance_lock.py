"""remote/_instance_lock.py 单测 —— 启动守卫（第二道闸）：原子 PID 单实例锁。

覆盖：
  1. 取锁写 `PID|EXE|TS`、释放只清自己的；
  2. **同一程序**的第二个实例 → 拒启（身份指纹命中）；
  3. 陈旧锁（持有者已死）→ 自动接管（锁在、进程没了，不该永远启不来）；
  4. **PID 复用**（持有者活着但不是本程序）→ 核验身份后接管；
  5. 身份**读不到** → fail-closed 拒启（绝不静默双监听）；
  6. 默认锁路径按**端口**键控；
  7. 真进程集成：顺序双启 → 第二个被锁拒绝；**同时三启** → 恰好存活一个
     （2026-09-17 要关的就是这个「探测 → bind」TOCTOU + Windows SO_REUSEADDR 双绑窗口）。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

NN_TRAINING = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NN_TRAINING))

from remote import _instance_lock as il

# ────────────────────────── 单元：取锁 / 释放 ──────────────────────────


def test_acquire_writes_pid_lock_and_release_removes_it(tmp_path: Path) -> None:
    lock = tmp_path / ".hub_server.lock"
    assert il.acquire_instance_lock(str(lock), marker="hub_server") is True
    pid, exe, ts = il.read_lock(str(lock))
    assert pid == os.getpid()
    assert exe == sys.executable
    assert ts and ts > 0

    il.release_instance_lock(str(lock))
    assert not lock.exists(), "自己持有的锁必须被释放"


def test_release_never_removes_someone_elses_lock(tmp_path: Path) -> None:
    lock = tmp_path / ".hub_server.lock"
    dead = _dead_pid()
    lock.write_text(f"{dead}|{sys.executable}|1", encoding="utf-8")
    il.release_instance_lock(str(lock))
    assert lock.exists(), "锁已易主/属他人时绝不动别人的锁"


# ────────────────────────── 单元：拒启 / 接管 三分支 ──────────────────────────


def test_second_instance_of_same_program_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """身份指纹命中 → 拒启（**不得**接管正在运行的同程序实例）。

    用**真子进程**当持有者（不是本测试进程）：它的命令行含 `hub_server`（= 同程序指纹），
    正是真实双开现场的形状；也避免把「本进程命令行长什么样」写进断言（xdist / wall
    包装下 argc 形态各不相同）。
    """
    lock = tmp_path / ".hub_server.lock"
    holder = _spawn_lock_holder(lock)
    try:
        assert _wait_lock_owner(lock, holder.pid), "持有者子进程未能取到锁"
        assert il.acquire_instance_lock(str(lock), marker="hub_server", tag="hub-server") is False
        out = capsys.readouterr().out
        assert "已有实例在运行" in out and "拒绝启动" in out
        assert il.read_lock(str(lock))[0] == holder.pid, "拒启不得改动他人持有的锁"
    finally:
        _kill(holder)


def test_stale_lock_taken_over_when_holder_dead(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    lock = tmp_path / ".hub_server.lock"
    dead = _dead_pid()
    lock.write_text(f"{dead}|{sys.executable}|1", encoding="utf-8")
    assert il.acquire_instance_lock(str(lock), marker="hub_server", tag="hub-server") is True
    assert "陈旧锁" in capsys.readouterr().out
    assert il.read_lock(str(lock))[0] == os.getpid()


def test_pid_reuse_taken_over_after_identity_check(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """持有者**活着**但不是本程序（PID 被系统复用）→ 核验身份后接管。

    这是「锁在、PID 活着、却永远启不来」那一类死锁的正面解法：只看存活会永久拒启。
    """
    lock = tmp_path / ".hub_server.lock"
    lock.write_text(f"{os.getpid()}|{sys.executable}|1", encoding="utf-8")
    # 本测试进程的命令行不含 "hub_server" ⇒ 身份不符 ⇒ 应接管
    assert il.acquire_instance_lock(str(lock), marker="hub_server", tag="hub-server") is True
    out = capsys.readouterr().out
    assert "PID 复用" in out and "接管陈旧锁" in out
    assert il.read_lock(str(lock))[0] == os.getpid()


def test_unreadable_cmdline_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """身份读不到（无 /proc、wmic 不可用）→ 拒启，而不是猜「大概没事」放行。"""
    lock = tmp_path / ".hub_server.lock"
    lock.write_text(f"{os.getpid()}|{sys.executable}|1", encoding="utf-8")
    monkeypatch.setattr(il, "proc_cmdline", lambda _pid: None)
    assert il.acquire_instance_lock(str(lock), marker="hub_server", tag="hub-server") is False
    out = capsys.readouterr().out
    assert "身份未知" in out and "fail-closed" in out


def test_default_lock_path_is_kind_and_port_keyed() -> None:
    a = il.default_instance_lock_path("hub_server", 8787)
    b = il.default_instance_lock_path("hub_server", 8877)
    w = il.default_instance_lock_path("worker_server", 8787)
    assert a.endswith(".hub_server.8787.lock")
    assert w.endswith(".worker_server.8787.lock")
    assert a != b, "不同端口必须是不同的锁（多课程槽位并行）"
    assert a != w, "不同服务（同端口）必须是不同的锁"
    assert Path(a).parent == NN_TRAINING == Path(w).parent


def test_multiple_markers_any_hit_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """多指纹：同一个服务有多个合法入口（`-m remote_worker_serve` / 模块名拉起）时，
    任一枚命中就算同一程序，仍然拒启（不得因为指纹表没写全而误接管）。"""
    lock = tmp_path / ".worker_server.8790.lock"
    holder = _spawn_lock_holder(lock, marker="remote_worker_serve")
    try:
        assert _wait_lock_owner(lock, holder.pid)
        assert (
            il.acquire_instance_lock(
                str(lock), marker=("remote_worker_serve", "worker_server"), tag="worker-serve"
            )
            is False
        )
        assert "已有实例在运行" in capsys.readouterr().out
    finally:
        _kill(holder)


def test_unwritable_lock_fails_open(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只读 FS / 权限不足 → **fail-open** 且响亮告警：写不下锁不该变成启动拦路鬼
    （守卫是第二道闸，第一道端口守卫仍在）。"""

    def _boom(_p: str) -> int:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(il, "_create_exclusive", _boom)
    assert il.acquire_instance_lock(str(tmp_path / "x.lock"), marker="hub_server", tag="hub") is True
    out = capsys.readouterr().out
    assert "WARN" in out and "无法创建" in out


# ────────────────────────── 集成：真进程双启 ──────────────────────────


def test_real_second_instance_refused_by_lock(tmp_path: Path) -> None:
    port = _free_port()
    lock = tmp_path / ".hub_server.lock"
    argv = _hub_argv(port, lock, tmp_path)
    log1 = tmp_path / "first.log"
    with open(log1, "w", encoding="utf-8") as f:
        p1 = subprocess.Popen(argv, cwd=str(NN_TRAINING), stdout=f, stderr=subprocess.STDOUT)
    try:
        assert _wait_ready(port, lock, p1, log1), f"第一实例未就绪：{_tail(log1)}"

        p2 = subprocess.run(
            argv, cwd=str(NN_TRAINING), capture_output=True, text=True, timeout=60, check=False
        )
        assert p2.returncode != 0, f"第二个实例必须响亮拒启，实际 rc={p2.returncode}"
        assert "已有实例在运行" in p2.stdout and "拒绝启动" in p2.stdout, p2.stdout
        assert _ping_ok(port), "第二实例不得干扰第一实例的服务"
    finally:
        _kill(p1)


def test_simultaneous_starts_leave_exactly_one_instance(tmp_path: Path) -> None:
    """三个 starter 同时启动 → 恰好一个成为实例，其余非零退出。

    这正是端口守卫**单独**挡不住的窗口（探测都能过、Windows 还能双绑成僵尸）；
    锁把它关掉：任一道闸拦下都是响亮拒启，不会有静默共存者。
    """
    port = _free_port()
    lock = tmp_path / ".hub_server.lock"
    argv = _hub_argv(port, lock, tmp_path)
    procs: list[tuple[subprocess.Popen, Path]] = []
    for i in range(3):
        log = tmp_path / f"boot{i}.log"
        with open(log, "w", encoding="utf-8") as f:
            procs.append((subprocess.Popen(argv, cwd=str(NN_TRAINING), stdout=f, stderr=subprocess.STDOUT), log))
    try:
        deadline = time.time() + 30
        alive = 3
        while time.time() < deadline:
            alive = sum(1 for p, _ in procs if p.poll() is None)
            if alive == 1:
                break
            if alive == 0:
                raise AssertionError(
                    "三启全灭——锁/端口守卫把唯一实例也拒了: "
                    + "; ".join(_tail(lg) for _, lg in procs)
                )
            time.sleep(0.3)
        assert alive == 1, f"必须恰好一个实例存活，实际 {alive}（静默双监听机会）"
        assert _wait_ready(port, lock, None, None), "存活的那个必须真的在服务"
        # 落败者非零退出（响亮失败，绝不静默变僵尸）
        assert all(
            p.returncode != 0 for p, _ in procs if p.poll() is not None
        ), "落败进程必须非零退出"
    finally:
        for p, _ in procs:
            _kill(p)


# ────────────────────────── 辅助 ──────────────────────────


def _hub_argv(port: int, lock: Path, tmp_path: Path) -> list[str]:
    job_root = tmp_path / "jobs"
    job_root.mkdir(exist_ok=True)
    jsonl = tmp_path / "training_log.jsonl"
    jsonl.touch()
    return [
        sys.executable,
        "-m",
        "remote.hub_server",
        "--port",
        str(port),
        "--host",
        "127.0.0.1",
        "--token",
        "t0k",
        "--job-root",
        str(job_root),
        "--jsonl",
        str(jsonl),
        "--lock-file",
        str(lock),
    ]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ping_ok(port: int, token: str = "t0k", timeout: float = 2.0) -> bool:
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/ping", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return bool(r.status == 200)
    except Exception:
        return False


def _wait_ready(
    port: int,
    lock: Path,
    proc: subprocess.Popen | None,
    log: Path | None,
    budget: float = 30.0,
) -> bool:
    deadline = time.time() + budget
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        if lock.exists() and _ping_ok(port):
            return True
        time.sleep(0.25)
    return False


def _kill(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=10)


def _tail(log: Path, lines: int = 6) -> str:
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return f"<无法读取 {log}>"


def _dead_pid() -> int:
    """一个**确定已死**的 pid（起一个立即结束的子进程取它的 pid，并等它退出）。"""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=30)
    return p.pid


def _spawn_lock_holder(lock: Path, marker: str = "hub_server") -> subprocess.Popen:
    """起一个真子进程持有锁：其命令行含 `marker`（当作同程序指纹），自身驻留直至被杀。"""
    code = (
        "import sys, time;"
        f"sys.path.insert(0, {str(NN_TRAINING)!r});"
        "from remote._instance_lock import acquire_instance_lock;"
        f"print('HOLDER', acquire_instance_lock({str(lock)!r}, marker={marker!r}), flush=True);"
        "time.sleep(120)"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=str(NN_TRAINING),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_lock_owner(lock: Path, pid: int, budget: float = 20.0) -> bool:
    deadline = time.time() + budget
    while time.time() < deadline:
        if il.read_lock(str(lock))[0] == pid:
            return True
        time.sleep(0.1)
    return False
