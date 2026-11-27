"""`trainer.bc_loop.append_weights_md_row` 的溯源列：2026-11-27 起恒为 `n/a`。

BC 与 RL 同跑 notebook、从 hub 下载 code.zip，训练机上没有 git 仓库可读；这里钉两件事：
列仍在（旧档案表格式不变）、但**不再起任何 git 子进程**（用 `subprocess.check_output`
打桩成炸弹，一旦有人把 git 调用加回来就炸）。
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


def test_sha_column_is_na_without_spawning_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_k: object) -> bytes:
        raise AssertionError("BC 注册行不得再起 git 子进程")

    monkeypatch.setattr(subprocess, "check_output", _boom)
    _append(tmp_path)
    row = _row(tmp_path)
    assert "| n/a |" in row
    assert "abc1234" not in row
