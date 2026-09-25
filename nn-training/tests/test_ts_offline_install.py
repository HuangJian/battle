"""tests/test_ts_offline_install.py —— Tailscale 离线安装包优先（Colab/Kaggle/AI Studio）。

云机出口慢/不稳时，`curl install.sh` 常失败；官方静态 tarball 可事先上传。
安装顺序必须是：已上传包 → 在线 install.sh。包路径解析要有单测，避免 notebook
内联与 `tailscale_boot.install` 两边各写一份后漂移。
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path

import pytest

import remote.tailscale_boot as ts


def _make_tarball(path: Path, bin_dir_name: str = "tailscale_1.102.4_amd64") -> Path:
    """造一个只含 tailscale/tailscaled 可执行占位的官方风格 tarball（strip-components=1）。"""
    root = path / bin_dir_name
    root.mkdir(parents=True)
    for name in ("tailscale", "tailscaled"):
        p = root / name
        p.write_text("#!/bin/sh\necho stub\n", encoding="utf-8")
        p.chmod(0o755)
    tb = path / f"{bin_dir_name}.tgz"
    with tarfile.open(tb, "w:gz") as tf:
        tf.add(root, arcname=bin_dir_name)
    return tb


def test_find_offline_tarball_prefers_explicit_path(tmp_path: Path) -> None:
    tb = _make_tarball(tmp_path)
    found = ts.find_offline_tarball(str(tb), search_roots=[str(tmp_path / "empty")])
    assert found == str(tb)


def test_find_offline_tarball_scans_search_roots(tmp_path: Path) -> None:
    up = tmp_path / "content"
    up.mkdir()
    tb = _make_tarball(up)
    found = ts.find_offline_tarball("", search_roots=[str(up)])
    assert found == str(tb)


def test_install_from_offline_tarball_skips_curl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tb = _make_tarball(tmp_path)
    prefix = tmp_path / "ts-prefix"
    called: list[str] = []

    def _fake_run(cmd, **kwargs):
        called.append(" ".join(str(c) for c in cmd))
        if cmd and cmd[0] == "tar":
            import subprocess as sp

            return sp.run(cmd, **kwargs)
        raise AssertionError(f"不应调用: {cmd}")

    logs: list[str] = []
    ts.install(logs.append, tarball=str(tb), prefix=str(prefix), run=_fake_run)
    assert (prefix / "tailscale").is_file()
    assert (prefix / "tailscaled").is_file()
    assert any("离线" in m or "tarball" in m.lower() or str(tb) in m for m in logs)
    # PATH 已前插 prefix
    assert str(prefix) in (os.environ.get("PATH") or "").split(os.pathsep)
    assert not any("install.sh" in c for c in called)


def test_install_falls_back_to_online_when_no_tarball(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def _fake_run(cmd, **kwargs):
        called.append(cmd if isinstance(cmd, str) else " ".join(map(str, cmd)))
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(ts.shutil, "which", lambda *_a, **_k: None)
    logs: list[str] = []
    ts.install(
        logs.append,
        tarball="",
        prefix=str(tmp_path / "pfx"),
        search_roots=[str(tmp_path / "none")],
        run=_fake_run,
    )
    assert any("install.sh" in c for c in called), f"无离线包时必须回退在线安装, called={called}"


def test_ensure_passes_tarball_from_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cfg["ts_tarball"] 必须传到 install（否则 notebook 填了 CFG 也没用）。

    ★ `ensure()` 里**已经没有任何 bun 分支**（2026-09-25 重裁：这两块盘不跑 rollout，不装 bun），
    所以本用例不再需要短接 bun 探测 —— 但**真实子进程一律打成响亮失败**这条规矩留着：
    以后再往 `ensure()` 里加网络/安装分支，会在这里当场红，而不是在沙箱里静默空等半小时
    （此前的版本因为 `ensure()` 真去 `curl https://bun.sh/install` 而空等 ~51s）。
    """
    seen: dict = {}

    def _fake_install(log, tarball="", **kw):
        seen["tarball"] = tarball

    def _no_subprocess(*a, **k):
        raise AssertionError("本用例不得起真实子进程（安装/daemon 都要被打桩）")

    monkeypatch.setattr(ts, "install", _fake_install)
    monkeypatch.setattr(ts.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(ts.subprocess, "run", _no_subprocess)
    monkeypatch.setattr(ts, "state", lambda: "Running")
    monkeypatch.setattr(ts, "start_daemon", lambda log, order="": "already-running")
    monkeypatch.setattr(ts, "self_ip", lambda: "100.64.0.9")
    ts.ensure({"ts_authkey": "k", "ts_tarball": "/data/tailscale.tgz"}, lambda m: None)
    assert seen.get("tarball") == "/data/tailscale.tgz"
