"""tests/test_remote_hotswap.py —— 远程 worker 代码热替换护栏 + job 目录清理。

背景（2026-09-11 review + 线上事故修复）：worker 是常驻进程，首 job 才 import 代码进
sys.modules；本地改代码后 hub 重打 code.zip（sha 变），后续 job 解压新代码、
sys.path.insert(0, 新目录)，但 import 只查 sys.modules → **跑的还是旧代码且零报错**。

热替换重启契约（2026-09-11 修复，替代 os.execve）：
  - 旧实现：notebook 里 worker_loop 跑在 kernel 进程内，换代码时 os.execve 原地替换
    kernel 镜像 → 单元格输出流断（sys.stdout 的 ipykernel 重定向对象丢失）、ZMQ 执行
    服务不再应答、Jupyter 判定 kernel 死；用户按停止 → SIGINT → kernel 重启 → 云端
    会话报废。
  - 新实现：worker 是**监督器**（supervise_worker / 新版 main()）拉起的子进程，代码
    变更时以 HOT_RELOAD_EXIT 退出，监督器用同一套参数重新拉起 → fresh 进程里
    sys.modules 必然为空，新代码一定生效；监督器（= kernel）不 execv、输出流不断。

本文件锁死（免 torch：只测协议层/调度层，不碰 PPO）：
  1. `CodeChangedError` **不**是 `ProtocolError` —— 后者会被 worker_loop
     "skip (not retried)" 永久跳过，hub 侧干等 1800s 触发 R9 连败停腿；
  2. worker_loop 捕获热替换后先 release 租约；有 restart_argv（有监督器）→ 以
     HOT_RELOAD_EXIT 退出交监督器重启；无（裸直调）→ 干净返回已处理数并提示人工重启；
  3. `_request_reload` 只认显式 argv（notebook 的 sys.argv 是 kernel 参数），空则
     返回 False 交由调用方降级；
  4. `supervise_worker`：子进程退 HOT_RELOAD_EXIT → 同一套参数重新拉起（fresh 进程
     加载新代码）、输出逐行转发；非 86 退出码原样上浮不重拉；
  5. `prune_job_dirs` 按 mtime 保留最近 N 个 job 目录，跳过 code_cache，删除失败不抛。
"""

from __future__ import annotations

import os
import sys
import time as _time
from pathlib import Path

import pytest

import platform_utils
from remote import worker as W
from remote.protocol import CodeChangedError, ProtocolError
from remote.worker import prune_job_dirs


def test_code_changed_error_is_not_protocol_error() -> None:
    """热替换绝不能落进 ProtocolError 的 'skip (not retried)' 分支。"""
    e = CodeChangedError("a" * 64, "b" * 64)
    assert not isinstance(e, ProtocolError)
    assert e.loaded_sha == "a" * 64 and e.job_sha == "b" * 64
    assert "a" * 64 not in str(e)  # 消息里只放前 12 位摘要，不刷屏
    assert "代码已变更" in str(e)


def test_request_reload_requires_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    """restart_argv 为空 → False（无监督器，不退出）；非空 → 以 HOT_RELOAD_EXIT 退出。"""
    logs: list[str] = []
    assert W._request_reload(None, log=logs.append) is False
    assert any("restart_argv" in m for m in logs)

    # 非空 = 有监督器：必须干净退出（SystemExit），绝不 execv —— execv 会打掉
    # notebook 的 kernel（2026-09-11 线上事故，见 docstring）
    with pytest.raises(SystemExit) as ei:
        W._request_reload(["--poll", "http://x", "--token-file", "t"], log=logs.append)
    assert ei.value.code == W.HOT_RELOAD_EXIT == 86


def test_worker_loop_hotswap_exits_for_supervisor_respawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """有监督器（restart_argv 传入）→ release 租约 + 以 HOT_RELOAD_EXIT 退出交监督器。"""
    polls: list[dict | None] = [{"job_id": "j-hot", "manifest": {"job_id": "j-hot"}}, None]
    monkeypatch.setattr(W, "poll_job", lambda *a, **k: polls.pop(0), raising=True)

    def _raise_hotswap(*a, **k):
        raise CodeChangedError("a" * 64, "b" * 64)

    monkeypatch.setattr(W, "run_job", _raise_hotswap, raising=True)

    released: list[str] = []
    monkeypatch.setattr(W, "release_job", lambda *a, **k: released.append(str(a[2])), raising=True)

    logs: list[str] = []
    with pytest.raises(SystemExit) as ei:
        W.worker_loop(
            "http://hub",
            "tok",
            work_dir=Path("/tmp/x"),
            poll_sec=0.0,
            once=True,
            restart_argv=["--poll", "http://hub", "--token", "tok"],
            log=logs.append,
        )
    assert ei.value.code == W.HOT_RELOAD_EXIT
    assert released == ["j-hot"]  # 先还租约，别让 hub 干等
    joined = "\n".join(logs)
    assert "代码已变更" in joined and "退出码" in joined
    assert "REJECTED" not in joined  # 关键：没落进 ProtocolError 的 skip 分支


def test_worker_loop_hotswap_no_supervisor_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    """无监督器（restart_argv=None 的裸直调）→ 降级为提示人工重启并返回，不退出进程。"""
    polls: list[dict | None] = [{"job_id": "j-hot", "manifest": {"job_id": "j-hot"}}, None]
    monkeypatch.setattr(W, "poll_job", lambda *a, **k: polls.pop(0), raising=True)

    def _raise_hotswap(*a, **k):
        raise CodeChangedError("a" * 64, "b" * 64)

    monkeypatch.setattr(W, "run_job", _raise_hotswap, raising=True)

    released: list[str] = []
    monkeypatch.setattr(W, "release_job", lambda *a, **k: released.append(str(a[2])), raising=True)

    logs: list[str] = []
    n = W.worker_loop(
        "http://hub",
        "tok",
        work_dir=Path("/tmp/x"),
        poll_sec=0.0,
        once=True,
        restart_argv=None,
        log=logs.append,
    )
    assert n == 0  # 没处理成任何 job
    assert released == ["j-hot"]
    joined = "\n".join(logs)
    assert "无监督器" in joined and "请手动重启" in joined


def test_supervise_worker_reenrolls_on_hot_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """子进程退 86（热替换）→ 同一套参数重新拉起；两代输出都转发到本进程 stdout。"""
    state = tmp_path / "spawns"
    script = (
        "import os, pathlib\n"
        "p = pathlib.Path(os.environ['SV_STATE'])\n"
        "n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "print(f'gene-{n + 1}', flush=True)\n"
        # 第一代请求热替换；第二代正常结束
        "raise SystemExit(86 if n == 0 else 0)\n"
    )
    monkeypatch.setenv("SV_STATE", str(state))

    relayed: list[str] = []

    def _print(*a, **k):
        relayed.append("".join(str(x) for x in a))

    monkeypatch.setattr("builtins.print", _print)  # 捕获监督器转发，不吃掉测试输出
    logs: list[str] = []
    rc = W.supervise_worker(
        ["--poll", "http://x"],
        cmd=[sys.executable, "-u", "-c", script],
        log=logs.append,
    )
    assert rc == 0  # 热替换被重演消化，不把 86 带给调用方
    assert state.read_text() == "2"  # 只重拉一次
    joined = "".join(relayed)
    assert "gene-1" in joined and "gene-2" in joined  # 两代输出都转发了
    assert any("重新拉起" in m or "代码已变更" in m for m in logs)


def test_supervise_worker_passthrough_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 86 退出码原样上浮，不重拉（计数文件证明只跑了一代）。"""
    state = tmp_path / "spawns"
    script = (
        "import os, pathlib\n"
        "p = pathlib.Path(os.environ['SV_STATE'])\n"
        "n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "raise SystemExit(7)\n"
    )
    monkeypatch.setenv("SV_STATE", str(state))
    logs: list[str] = []
    rc = W.supervise_worker(
        ["--poll", "http://x"],
        cmd=[sys.executable, "-u", "-c", script],
        log=logs.append,
    )
    assert rc == 7
    assert state.read_text() == "1"  # 一代即终结


def test_worker_loop_exits_on_hub_halt(monkeypatch: pytest.MonkeyPatch) -> None:
    """§385 复审：hub 下发达令 → worker 干净退出（省 GPU 配额、不 claim job）。"""
    polls = [{"halt": True}]
    monkeypatch.setattr(W, "poll_job", lambda *a, **k: polls.pop(0), raising=True)
    logs: list[str] = []
    n = W.worker_loop(
        "http://hub", "tok", work_dir=Path("/tmp/whatever"), poll_sec=0.0, log=logs.append
    )
    assert n == 0  # 没处理任何 job 就退
    joined = "\n".join(logs)
    assert "云端停机达令" in joined and "省 GPU 配额" in joined


def test_poll_job_surfaces_halt(monkeypatch: pytest.MonkeyPatch) -> None:
    """§385 复审：/jobs/next 的 {"halt": true} 被 poll_job 原样上浮，不丢成无 job。"""
    monkeypatch.setattr(
        W, "_request", lambda *a, **k: (200, b'{"halt": true, "job_id": null}'), raising=True
    )
    assert W.poll_job("http://hub", "tok") == {"halt": True}
    monkeypatch.setattr(W, "_request", lambda *a, **k: (200, b'{"job_id": null}'), raising=True)
    assert W.poll_job("http://hub", "tok") is None
    monkeypatch.setattr(
        W,
        "_request",
        lambda *a, **k: (200, b'{"job_id": "j1", "manifest": {"a": 1}}'),
        raising=True,
    )
    assert W.poll_job("http://hub", "tok") == {"job_id": "j1", "manifest": {"a": 1}}


def test_prune_job_dirs_keeps_recent_and_skips_code_cache(tmp_path: Path) -> None:
    """保留最近 N 个（含在跑的），code_cache 永不删。"""
    work = tmp_path / "remote-worker"
    work.mkdir()
    (work / "code_cache").mkdir()
    for i, name in enumerate(["j1", "j2", "j3", "j4"]):
        d = work / name
        d.mkdir()
        (d / "_result.json").write_text("{}", encoding="utf-8")
        os.utime(d, (_time.time() + i, _time.time() + i))  # 保证 mtime 有序

    logs: list[str] = []
    removed = prune_job_dirs(work, keep=2, log=logs.append)

    assert removed == 2
    left = sorted(p.name for p in work.iterdir())
    assert left == ["code_cache", "j3", "j4"]  # 最近 2 个 job + 代码缓存


def test_prune_job_dirs_tolerates_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rmtree 抛（含 SystemExit：沙箱删除守卫）→ 跳过并记日志，不打断训练。"""
    work = tmp_path / "w"
    work.mkdir()
    for name in ("j1", "j2", "j3"):
        (work / name).mkdir()

    def _boom(*a, **k):
        raise SystemExit("sandbox delete guard")

    monkeypatch.setattr(platform_utils, "rmtree_best_effort", _boom, raising=True)
    logs: list[str] = []
    assert prune_job_dirs(work, keep=1, log=logs.append) == 0
    assert any("跳过" in m for m in logs)
