"""tests/test_remote_hotswap.py —— 远程 worker 代码热替换护栏 + job 目录清理。

背景（2026-09-11 review）：worker 是常驻进程，首 job 才 import 代码进 sys.modules；
本地改代码后 hub 重打 code.zip（sha 变），后续 job 解压新代码、sys.path.insert(0,
新目录)，但 import 只查 sys.modules → **跑的还是旧代码且零报错**。

本文件锁死四条（免 torch：只测协议层/调度层，不碰 PPO）：
  1. `CodeChangedError` **不**是 `ProtocolError` —— 后者会被 worker_loop
     "skip (not retried)" 永久跳过，hub 侧干等 1800s 触发 R9 连败停腿；
  2. worker_loop 捕获热替换后走**自重启**分支：先 release 租约，再 `_self_restart`；
     自重启不可用（restart_argv=None）时不重试、不跳过——干净返回已处理数；
  3. `_self_restart` 只认显式 argv（notebook 的 sys.argv 是 kernel 参数，execv 会干掉
     kernel），为空则返回 False 交由调用方降级；
  4. `prune_job_dirs` 按 mtime 保留最近 N 个 job 目录，跳过 code_cache，删除失败不抛。
"""

from __future__ import annotations

import os
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


def test_self_restart_requires_explicit_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """restart_argv 为空 → False（不 execv）。非空则绝不能拿 sys.argv 兜底。"""
    logs: list[str] = []
    assert W._self_restart(None, log=logs.append) is False
    assert any("restart_argv" in m for m in logs)

    # 非空：必须 execve（打桩，别真把测试进程换掉）
    captured: dict[str, list[str]] = {}
    env_captured: dict[str, dict[str, str]] = {}

    def _fake_execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        captured["argv"] = argv
        env_captured["env"] = env

    monkeypatch.setattr(W.os, "execve", _fake_execve, raising=True)
    ok = W._self_restart(["--poll", "http://x", "--token-file", "t"], log=logs.append)
    # execve 被打桩后不替换进程，函数走到末尾返回 False
    assert ok is False
    argv = captured["argv"]
    assert argv[1:4] == ["-u", "-m", "remote.worker"]
    assert "--poll" in argv and "http://x" in argv  # 原样重放
    assert "nn-training" in env_captured["env"]["PYTHONPATH"].replace("\\", "/")


def test_worker_loop_hotswap_triggers_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """热替换 → release 租约 + _self_restart（不重试、不 skip）；重启失败则干净返回。"""
    polls: list[dict | None] = [{"job_id": "j-hot", "manifest": {"job_id": "j-hot"}}, None]
    monkeypatch.setattr(W, "poll_job", lambda *a, **k: polls.pop(0), raising=True)

    def _raise_hotswap(*a, **k):
        raise CodeChangedError("a" * 64, "b" * 64)

    monkeypatch.setattr(W, "run_job", _raise_hotswap, raising=True)

    released: list[str] = []
    monkeypatch.setattr(W, "release_job", lambda *a, **k: released.append(str(a[2])), raising=True)
    seen: list[list[str]] = []

    def _fake_restart(argv, log=None):
        seen.append(argv)
        return False  # 模拟自重启失败 → 走"请手动重启"降级

    monkeypatch.setattr(W, "_self_restart", _fake_restart, raising=True)

    logs: list[str] = []
    n = W.worker_loop(
        "http://hub",
        "tok",
        work_dir=Path("/tmp/whatever"),
        poll_sec=0.0,
        once=True,  # 单发：热替换分支会直接 return，不会回到轮询
        restart_argv=["--poll", "http://hub", "--token", "tok"],
        log=logs.append,
    )
    assert n == 0  # 没处理成任何 job
    assert released == ["j-hot"]  # 先还租约，别让 hub 干等
    assert seen == [["--poll", "http://hub", "--token", "tok"]]
    joined = "\n".join(logs)
    assert "代码已变更" in joined and "自重启" in joined
    assert "REJECTED" not in joined  # 关键：没落进 ProtocolError 的 skip 分支


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
        W, "_request", lambda *a, **k: (200, b'{"job_id": "j1", "manifest": {"a": 1}}'), raising=True
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
