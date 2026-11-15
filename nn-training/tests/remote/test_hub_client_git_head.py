"""`remote.hub_client.git_head`：解析不了 HEAD 时，**成因要跟着结论一起出来**。

原实现是 `try: … except Exception: pass` 之后再抛一句「无法解析 git HEAD」。这句话对现场没有
信息量：git 不在 PATH、`cwd` 没了、30s 超时、仓库不是 git 仓库 —— 四种成因的处置完全不同
（装 git / 换个目录 / 查磁盘 / 先 `git init`），而它们被压成了同一行字。
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from remote import hub_client
from remote.hub_client import HubClientError, git_head


class _Done:
    """`subprocess.CompletedProcess` 的最小面（`returncode` / `stdout`）。"""

    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_git_head_returns_the_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub_client, "run_capture", lambda *a, **k: _Done(0, "abc123\n"))
    assert git_head() == "abc123", "通道机器要的是裸 commit 串（不带换行）"


def test_git_head_keeps_the_cause_when_git_itself_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git 不可执行 / 超时 ⇒ 结论不变，但 `__cause__` 必须带着真正的异常。"""

    def _boom(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="git rev-parse HEAD", timeout=30)

    monkeypatch.setattr(hub_client, "run_capture", _boom)
    with pytest.raises(HubClientError) as ei:
        git_head()
    assert isinstance(ei.value.__cause__, subprocess.TimeoutExpired), (
        "成因被吞掉了：现场只看到「不在 git 工作区」，永远想不到是超时"
    )
    assert "TimeoutExpired" in str(ei.value), "消息里也要有人能读的成因"


def test_git_head_reports_a_nonzero_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """「不是 git 仓库」是**另一条**成因（退出码），不该和「git 没装」混成一句。"""
    monkeypatch.setattr(hub_client, "run_capture", lambda *a, **k: _Done(128, ""))
    with pytest.raises(HubClientError) as ei:
        git_head()
    assert "128" in str(ei.value), f"要带上退出码，实际：{ei.value}"


def test_git_head_rejects_an_empty_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """退出码 0 但输出空（半死的仓库）也是解析失败 —— 不能把空串当 commit 传下去。"""
    monkeypatch.setattr(hub_client, "run_capture", lambda *a, **k: _Done(0, "   \n"))
    with pytest.raises(HubClientError):
        git_head()
