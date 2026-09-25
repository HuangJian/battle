"""tests/test_tailscale_boot_bun.py —— 节点引导必须先确保 bun（2026-09-25 事故）。

现象：课程切到「在线」后（走 `battle.tailscale.ipynb`），worker 每单都
`REJECTED: 节点上找不到 'bun'（kind=iter 需要 bun 跑 rollout）`，零下载空转。根因不是 bun
装不上，而是**这条链从来没装过** —— `battle.tailscale.ipynb` 的 cell 与 `remote/notebook_boot.py`
历史上零命中 `bun`，而 `battle.offline.ipynb` 的 cell 里一直有那段（且刻意放在装 tailnet 之前），
所以只有「换盘」才暴露。

钉两件事：
  1. `ensure_bun` 的四个分支：PATH 命中 / `~/.bun/bin` 命中 / 装成功后**前置 PATH** / 装失败只记日志不抛；
  2. **顺序**：`ensure()` 必须先 `ensure_bun` 再碰 tailscale —— bun 走公网 HTTPS，而 userspace
     tailscaled 的本地代理只转发 Tailscale IP，代理一改就再也装不上。
"""

from __future__ import annotations

import inspect
import os
import subprocess
from pathlib import Path

import pytest

from remote import tailscale_boot


@pytest.fixture
def no_bun(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把节点造成「没有 bun」的样子：PATH 命中不了、`~/.bun/bin` 也不存在。"""
    monkeypatch.setattr(tailscale_boot.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(tailscale_boot.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_uses_bun_from_path_without_installing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tailscale_boot.shutil, "which", lambda name, *a, **k: "/usr/local/bin/bun")
    monkeypatch.setattr(tailscale_boot.Path, "home", classmethod(lambda cls: tmp_path))
    calls: list[object] = []
    monkeypatch.setattr(tailscale_boot.subprocess, "run", lambda *a, **k: calls.append(a))
    assert tailscale_boot.ensure_bun(lambda m: None) == "/usr/local/bin/bun"
    assert calls == [], "已经有 bun 就不该再跑安装脚本"


def test_falls_back_to_the_installer_home_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """installer 落点不在 PATH 里也要认（installer 只改 shell rc，新进程 PATH 未必带它）。"""
    monkeypatch.setattr(tailscale_boot.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(tailscale_boot.Path, "home", classmethod(lambda cls: tmp_path))
    b = tmp_path / ".bun" / "bin"
    b.mkdir(parents=True)
    (b / "bun").write_text("#!/bin/sh\n", encoding="utf-8")
    logs: list[str] = []
    assert tailscale_boot.ensure_bun(logs.append) == str(b / "bun")
    assert any("就绪" in m for m in logs), logs


def test_installs_then_prepends_path(no_bun: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 值类型是 `list[str]`（argv），不是 `object`：下面 `" ".join(seen["argv"])` 要它可迭代
    # —— 原来写 `object` 时 mypy 报 arg-type（门禁只在文件被暂存时拦，所以一直没人碰它）。
    seen: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        target = no_bun / ".bun" / "bin"
        target.mkdir(parents=True, exist_ok=True)
        (target / "bun").write_text("#!/bin/sh\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(tailscale_boot.subprocess, "run", fake_run)
    got = tailscale_boot.ensure_bun(lambda m: None)
    assert got == str(no_bun / ".bun" / "bin" / "bun")
    assert "bun.sh/install" in " ".join(seen["argv"]), seen["argv"]
    assert os.environ["PATH"].startswith(str(no_bun / ".bun" / "bin")), (
        "installer 只改 shell rc ⇒ 当前进程的 PATH 必须自己前置，否则 resolve_bun 依然找不到"
    )


def test_install_failure_is_loud_but_not_fatal(no_bun: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """装不上不抛：能力缺失的唯一判定点仍是 worker 的零下载自检（不造第二条事实源）。"""
    monkeypatch.setattr(
        tailscale_boot.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 1, "", "curl: (6) could not resolve host"
        ),
    )
    logs: list[str] = []
    assert tailscale_boot.ensure_bun(logs.append) == ""
    assert any("rc=1" in m for m in logs), logs
    assert any("不可用" in m for m in logs), "装不上要留一句显眼的，否则现场只剩 worker 的 REJECT"


def test_install_oserror_is_logged_not_raised(no_bun: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(argv, **kw):
        raise OSError("bash 不存在")

    monkeypatch.setattr(tailscale_boot.subprocess, "run", boom)
    logs: list[str] = []
    assert tailscale_boot.ensure_bun(logs.append) == ""
    assert any("OSError" in m for m in logs), logs


def test_ensure_calls_ensure_bun_before_touching_tailscale() -> None:
    """顺序是硬约束：代理一改，公网安装就再也走不通（这正是本条事故的机理）。"""
    body = inspect.getsource(tailscale_boot.ensure)
    assert "ensure_bun(log)" in body, "ensure() 必须负责装 bun —— 它是所有调用方共用的入口"
    assert body.index("ensure_bun(log)") < body.index('shutil.which("tailscale")'), (
        "bun 必须在装 tailscale **之前** —— 顺序反了就是 2026-09-25 事故的重演"
    )
