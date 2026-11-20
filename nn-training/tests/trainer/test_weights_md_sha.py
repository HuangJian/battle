"""`trainer.bc_loop.append_weights_md_row` 的溯源列：**git 取不到 ⇒ "n/a"，我们拼错了 ⇒ 炸**。

这一列记的是「这份权重是哪个 commit 训出来的」，几周后回看模型档案时它是唯一的线索。
降级成 `"n/a"` 是对的（git 不在 PATH / 超时 / 仓库没有 `.git`），但原先把**命令本身拼错**
（`TypeError` / `IndexError`）也一起吞成 `"n/a"` —— 那是我们的 bug，必须在第一次跑就炸。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trainer import bc_loop


def _row(tmp_path: Path) -> str:
    return (tmp_path / "WEIGHTS.md").read_text(encoding="utf-8")


def _append(tmp_path: Path) -> None:
    bc_loop.append_weights_md_row(
        tmp_path,
        tmp_path / "bc-weights-it1.json",
        epochs=2,
        samples=(10, 100),
        best_val=0.5,
        accs=(0.6, 0.7),
        note="ok",
    )


def test_sha_column_records_the_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bc_loop.subprocess, "check_output", lambda *a, **k: b"abc1234\n")
    _append(tmp_path)
    assert "| abc1234 |" in _row(tmp_path)


@pytest.mark.parametrize(
    "err",
    [
        FileNotFoundError("git 不在 PATH"),
        subprocess.TimeoutExpired(cmd="git rev-parse", timeout=15),
    ],
)
def test_git_being_unavailable_degrades_to_na(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: BaseException
) -> None:
    """git 缺失 / 超时是**允许的降级**：行照写，溯源列记 `n/a`（列本身是显式的）。"""

    def _boom(*_a: object, **_k: object) -> bytes:
        raise err

    monkeypatch.setattr(bc_loop.subprocess, "check_output", _boom)
    _append(tmp_path)
    assert "| n/a |" in _row(tmp_path)


def test_a_wrong_command_line_is_not_a_degrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """参数拼错（`TypeError`）与「git 不可用」是两件事：前者要在第一次跑就炸。"""

    def _boom(*_a: object, **_k: object) -> bytes:
        raise TypeError("check_output() got an unexpected keyword argument '_POPEN_NO_WINDOW'")

    monkeypatch.setattr(bc_loop.subprocess, "check_output", _boom)
    with pytest.raises(TypeError):
        _append(tmp_path)
