"""worker_server 的端口级原子实例锁（第二道闸，2026-09-17，与 hub_server 同族）。

背景：`remote/worker_server.py::serve_forever` 原先只有端口守卫（`_port_guard.ensure_port_free`）
——那是「探测 → bind」的 TOCTOU，两个 starter 同时探测会双双通过；Windows 的 SO_REUSEADDR 还
允许两个 socket 同时 bind 同一端口（后启动者不报错，静默变成「永远收不到连接的僵尸」）。
worker_server 的僵尸比 hub 的更贵：HUB 会把 job POST 进一个没人应答的监听端口，表现为
推送静默卡死。修复 = bind 前加同一把锁（拿锁 → 端口探测 → bind 三道闸），陈旧锁能在核验
身份后自动接管。

覆盖：① 锁路径按 (kind, port) 键控；② 接线顺序（锁必须在端口探测之前，防「helper 写了忘接线」）；
③ 真进程顺序双启：第二个被锁拒绝且非零退出，第一个照常服务。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

NN_TRAINING = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NN_TRAINING))

from remote._instance_lock import default_instance_lock_path
from tests.subproc_util import (
    PORT_TAKEN_MARKER,
    PortStolenError,
    retry_on_port_stolen,
    spawn_bound_port,
)

TOKEN = "t0k"


# ────────────────────────── 单测：锁名与接线 ──────────────────────────


def test_worker_lock_path_is_kind_and_port_keyed() -> None:
    a = default_instance_lock_path("worker_server", 8790)
    assert a.endswith(".worker_server.8790.lock")
    assert Path(a).parent == NN_TRAINING
    assert a != default_instance_lock_path("worker_server", 8791)
    assert a != default_instance_lock_path("hub_server", 8790)


def test_lock_acquired_before_port_probe_in_source() -> None:
    """接线门禁：`serve_forever` 必须**先拿锁再探端口**——顺序反了就等于没有第二道闸。"""
    src = (NN_TRAINING / "remote" / "worker_server.py").read_text(encoding="utf-8")
    lock_at = src.index("acquire_instance_lock(")
    probe_at = src.index('ensure_port_free("127.0.0.1", port)')
    assert lock_at < probe_at, "锁必须在端口探测之前拿到"
    assert "release_instance_lock" in src, "拿到的锁必须在退出时释放（atexit）"


# ────────────────────────── 集成：真进程双启 ──────────────────────────


def test_real_second_worker_server_refused_by_lock(tmp_path: Path) -> None:
    lock = tmp_path / ".worker_server.lock"
    # 端口竞态由 helper 消化：它只在**这个子进程**自报 listening 后才交出端口，
    # 否则换端口重试（裸「探端口 → 起子进程」在 xdist 并行下会撞「禁止双监听」→ 假红）
    first = spawn_bound_port(lambda port: _argv(port, lock, tmp_path), cwd=str(NN_TRAINING))
    port, p1 = first.port, first.proc
    argv = _argv(port, lock, tmp_path)
    try:
        assert _wait_ready(port, lock, p1, None), f"第一个实例未就绪：{first.tail()}"
        assert lock.exists(), "服务在跑时必须持有锁文件"

        # bytes + 显式 utf-8 解码（AGENTS §17.6）：本机用户级 PYTHONIOENCODING=UTF-8 让子
        # 进程写 UTF-8，而 `text=True` 按控制台代码页（gbk）解码 → 中文输出直接
        # UnicodeDecodeError、`stdout` 变成 None（2026-09-17 实测）。
        p2 = subprocess.run(argv, cwd=str(NN_TRAINING), capture_output=True, timeout=60, check=False)
        out2 = p2.stdout.decode("utf-8", "replace")
        assert p2.returncode != 0, f"第二个实例必须响亮拒启，实际 rc={p2.returncode}"
        assert "已有实例在运行" in out2 and "拒绝启动" in out2, out2
        assert _ping_ok(port), "第二实例不得干扰第一实例的服务"
        assert p1.poll() is None, "第一实例必须还活着（不得被后来者顶掉）"
    finally:
        _kill(p1)


def test_simultaneous_worker_starts_leave_exactly_one(tmp_path: Path) -> None:
    """三启同时 → 恰好一个成为实例（端口的排他性由锁 + 端口守卫双保险）。

    三个进程必须抢**同一个**端口，所以用不了 `spawn_bound_port`（它只起一个）；端口若在探测
    后被外人抢走，三个都会死在端口守卫上（输出带 `PORT_TAKEN_MARKER`）——那不是被测行为不
    对，而是场景作废 ⇒ 抛 `PortStolenError` 让 `retry_on_port_stolen` 换端口重跑。
    """
    lock = tmp_path / ".worker_server.lock"

    def scenario(port: int) -> tuple[int, int, list[tuple[subprocess.Popen, Path]]]:
        argv = _argv(port, lock, tmp_path)
        procs: list[tuple[subprocess.Popen, Path]] = []
        for i in range(3):
            log = tmp_path / f"boot{i}.log"
            with open(log, "w", encoding="utf-8") as f:
                procs.append(
                    (
                        subprocess.Popen(argv, cwd=str(NN_TRAINING), stdout=f, stderr=subprocess.STDOUT),
                        log,
                    )
                )
        deadline = time.time() + 40
        alive = len(procs)
        while time.time() < deadline:
            alive = sum(1 for p, _ in procs if p.poll() is None)
            if alive <= 1:
                break
            # sleep-ok: 轮询步长（等的是「其余进程已退」这个状态，deadline 只当挂起兜底）
            time.sleep(0.3)
        if alive == 0:
            outs = "; ".join(_tail(lg) for _, lg in procs)
            if PORT_TAKEN_MARKER in outs:  # 端口被外人抢走 ⇒ 场景作废，换个端口重跑
                raise PortStolenError(outs)
            raise AssertionError(f"三启全灭——锁/端口守卫把唯一实例也拒了: {outs}")
        return port, alive, procs

    port, alive, procs = retry_on_port_stolen(scenario)
    try:
        assert alive == 1, f"必须恰好一个实例存活，实际 {alive}（静默双监听机会）"
        assert _wait_ready(port, lock, None, None), "存活的那个必须真的在服务"
        assert all(p.returncode != 0 for p, _ in procs if p.poll() is not None)
    finally:
        for p, _ in procs:
            _kill(p)


# ────────────────────────── 辅助 ──────────────────────────


def _argv(port: int, lock: Path, tmp_path: Path) -> list[str]:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return [
        sys.executable,
        "-m",
        "remote_worker_serve",
        "--port",
        str(port),
        "--token",
        TOKEN,
        "--work",
        str(work),
        "--lock-file",
        str(lock),
    ]


def _ping_ok(port: int, timeout: float = 2.0) -> bool:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/ping", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return bool(r.status == 200)
    except (urllib.error.URLError, OSError):
        return False


def _wait_ready(
    port: int,
    lock: Path,
    proc: subprocess.Popen | None,
    log: Path | None,
    budget: float = 40.0,
) -> bool:
    deadline = time.time() + budget
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        if lock.exists() and _ping_ok(port):
            return True
        # sleep-ok: 轮询步长（等的是「锁 + 端口都就绪」这个状态，budget 只当挂起兜底）
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


def _tail(log: Path, lines: int = 8) -> str:
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return f"<无法读取 {log}>"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
