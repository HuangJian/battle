"""rl/workdir_sweep.py — pure-function + sweep tests (no torch, no bun).

§374 同步（2026-09-08）：local_slots 直跑每局一个 it{N}/w{idx}/ 波次目录，失败/废弃
局（无 _rl_report.json）无人清理。本测试验证 plan/sweep 只删失败波次、完整波次与
非波次内容（dist/、weights.json 等）原样保留。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from platform_utils import sandbox_delete_blocked
from rl.workdir_sweep import plan_failed_wave_dirs, sweep_failed_wave_dirs


def _complete_wave(iter_dir: Path, name: str) -> None:
    """成功局：_rl_report.json + 完整 shard + rollout.log（PPO 语料，永不删）。"""
    d = iter_dir / name
    (d / "rl_s0_seed1").mkdir(parents=True)
    (d / "rl_s0_seed1" / "obs.npy").write_bytes(b"\x00" * 16)
    (d / "rl_s0_seed1" / "manifest.json").write_text(json.dumps({"wver": "x"}), encoding="utf-8")
    (d / "_rl_report.json").write_text("{}", encoding="utf-8")
    (d / "rollout.log").write_text("log", encoding="utf-8")


def _failed_wave(iter_dir: Path, name: str) -> None:
    """失败/废弃局：部分 shard（无 manifest）+ rollout.log，无 _rl_report.json。"""
    d = iter_dir / name
    (d / "rl_s0_seed2").mkdir(parents=True)
    (d / "rl_s0_seed2" / "obs.npy").write_bytes(b"\x00" * 16)
    (d / "rollout.log").write_text("log", encoding="utf-8")


def _non_wave_content(iter_dir: Path) -> None:
    (iter_dir / "dist").mkdir(parents=True)
    (iter_dir / "dist" / "self").mkdir()
    (iter_dir / "weights.json").write_text("{}", encoding="utf-8")
    (iter_dir / "ppo_ckpt").mkdir()


def test_plan_failed_wave_dirs_only(tmp_path: Path) -> None:
    _complete_wave(tmp_path, "w0")
    _complete_wave(tmp_path, "w3")
    _failed_wave(tmp_path, "w1")
    _failed_wave(tmp_path, "w2")
    _non_wave_content(tmp_path)
    got = {p.name for p in plan_failed_wave_dirs(tmp_path)}
    assert got == {"w1", "w2"}


def test_plan_excludes_wave_lookalikes(tmp_path: Path) -> None:
    # w / wave0 / w00x / W0 —— 都不是 run_local_rollout 的 w<idx> 命名，不匹配
    for name in ("w", "wave0", "w00x", "W0", "w_1"):
        _failed_wave(tmp_path, name)
    assert plan_failed_wave_dirs(tmp_path) == []


def test_plan_report_only_wave_is_kept(tmp_path: Path) -> None:
    # 有 _rl_report.json 但 shard 不全（report-only 波次）：保守保留（resume 口径以
    # report 为完整标记，宁可留死重也不误删已结算局）。
    d = tmp_path / "w0"
    d.mkdir()
    (d / "_rl_report.json").write_text("{}", encoding="utf-8")
    assert plan_failed_wave_dirs(tmp_path) == []


def test_plan_missing_dir_is_empty(tmp_path: Path) -> None:
    assert plan_failed_wave_dirs(tmp_path / "nope") == []
    assert sweep_failed_wave_dirs(tmp_path / "nope") == 0


def test_sweep_removes_only_failed(tmp_path: Path) -> None:
    _complete_wave(tmp_path, "w0")
    _failed_wave(tmp_path, "w1")
    _failed_wave(tmp_path, "w2")
    _non_wave_content(tmp_path)
    logs: list[str] = []
    n = sweep_failed_wave_dirs(tmp_path, log=logs.append)
    if ((tmp_path / "w1").exists() or (tmp_path / "w2").exists()) and sandbox_delete_blocked(
        tmp_path
    ):
        # 删除没落地 + 探针确认被拦：沙箱 safe-delete 配额拦截（环境）则 skip，
        # 否则是真回归（探针删得掉），继续走断言红。and 短路保证探针只在已失败
        # 路径跑，绿路径零开销。
        pytest.skip("沙箱 safe-delete 配额耗尽拦截删除（环境，非回归）——换 turn 重跑即绿")
    assert n == 2
    assert (tmp_path / "w1").exists() is False
    assert (tmp_path / "w2").exists() is False
    # 完整波次 + 非波次内容原样保留
    assert (tmp_path / "w0" / "_rl_report.json").exists()
    assert (tmp_path / "w0" / "rl_s0_seed1" / "obs.npy").exists()
    assert (tmp_path / "dist" / "self").is_dir()
    assert (tmp_path / "weights.json").exists()
    assert (tmp_path / "ppo_ckpt").is_dir()
    assert len(logs) == 2
    assert "w1" in logs[0] and "w2" in logs[1]


def test_sweep_idempotent(tmp_path: Path) -> None:
    _failed_wave(tmp_path, "w0")
    first = sweep_failed_wave_dirs(tmp_path)
    if (first != 1 or (tmp_path / "w0").exists()) and sandbox_delete_blocked(tmp_path):
        pytest.skip("沙箱 safe-delete 配额耗尽拦截删除（环境，非回归）——换 turn 重跑即绿")
    assert first == 1
    assert sweep_failed_wave_dirs(tmp_path) == 0
