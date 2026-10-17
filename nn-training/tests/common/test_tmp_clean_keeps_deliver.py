"""`tools/tmp-clean.py` 的运行目录收敛**不得**删掉课程的产物目录（plan §7-3 / DoD-6b）。

为什么这条值得一个专门用例：`tmp/<课程>/` 含 `it<N>/` ⇒ 被判成 run root，收敛动作是
`rmtree` 整目录；而 `tmp/<课程>/deliver/<run_id>/` 是**导入/回灌落下的课程产物**（课程的长期
资产，不是临时物）。最危险的窗口恰好是「课已停（`training_running()` 的闸放开）、人正在导入」
——没有这条用法，一次例行 tmp 收敛就能把刚收进来的 27 轮读数删掉，而那是不可再生的。

本文件按**文件路径**加载仓根脚本（不依赖 nn-training 包布局，与
`test_pid_probe_windows_safe.py::_load_tmp_clean_probe` 同一个理由）。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

NN_ROOT = Path(__file__).resolve().parents[2]
TOOLS = NN_ROOT.parent / "tools"


def _load_tmp_clean() -> Any:
    """加载仓根 `tools/tmp-clean.py`（`if __name__ == "__main__"` 保护 ⇒ 加载无副作用）。"""
    spec = importlib.util.spec_from_file_location("_tmp_clean_under_test", TOOLS / "tmp-clean.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # 禁止写字节码：否则每次跑测试都在仓根 `tools/` 下撒一个 `__pycache__/`（脏工作区）。
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = prev
    return mod


def _mkdir(p: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        f = p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")


def _reasons(items: list[tuple[Path, str, int]]) -> list[str]:
    return [str(p) for p, _why, _size in items]


def _later() -> float:
    """比「此刻」靠后 5 秒的 now（配 keep_days=0 ⇒ 盘上一切都是陈的）。

    **不用 `time.time()` 自己**：Windows 上 `time.time()` 的粒度粗到 ~15.6ms，而文件 mtime
    是百纳秒级——同一个刚写完的目录里，`mtime` 完全可能**大于**当拍的 `time.time()`，
    于是 `mtime < now` 为假 ⇒ 计划空 ⇒ 用例变成「什么都不删」的假绿。
    """
    return time.time() + 5.0


def test_run_root_convergence_keeps_deliver(tmp_path: Path) -> None:
    """课已停 + 早于保留窗 ⇒ 收敛它的 `it<N>/`，但 `deliver/` 一个字节都不许进删除名单。"""
    mod = _load_tmp_clean()
    tmp = tmp_path / "tmp"
    course = tmp / "x1-rebirth-a2"
    _mkdir(
        course,
        {
            "it1/per-game.json": '{"it":1}',
            "it1/weights.json": "w1",
            "logs/train.log": "…",
            # 课程产物目录（导入落下的）：它的名字**不是** it<N> ⇒ 只能靠豁免保住
            "deliver/run-1/it-001/weights.json": "p1",
            "deliver/run-1/metrics.jsonl": '{"it":1}\n',
            "deliver-uploads/2026-10-01T00-00-00-deliver-x1.zip": "zip",
        },
    )
    items, stats = mod.plan(_later(), keep_runs=0, keep_days=0, tmp_dir=tmp, running=False)
    # 判据 = **整个目标集合逐一相等**（不是「名单里没有它」那种否定式转圈）：删了什么、留了什么
    # 一次列完 —— 临时物（it1/ · logs/ · 留证）照旧收敛，而 `deliver/` 连子项都不会出现。
    got = {os.path.relpath(p, tmp) for p in _reasons(items)}
    want = {
        os.path.join("x1-rebirth-a2", "it1"),
        os.path.join("x1-rebirth-a2", "logs"),
        os.path.join("x1-rebirth-a2", "deliver-uploads"),
    }
    assert got == want, got
    assert stats["run"] == 1


def test_plan_targets_are_children_when_asset_present(tmp_path: Path) -> None:
    """有资产时**收敛到子项一层**：删除目标是兄弟，不是那个 run root 本身（否则 rmtree 连坐）。"""
    mod = _load_tmp_clean()
    tmp = tmp_path / "tmp"
    course = tmp / "y20-obs2"
    _mkdir(course, {"it3/w.json": "w", "deliver/run-2/it-002/weights.json": "p2"})
    items, _stats = mod.plan(_later(), keep_runs=0, keep_days=0, tmp_dir=tmp, running=False)
    names = _reasons(items)
    assert str(course) not in names
    assert str(course / "it3") in names
    # 落盘后验（不只信名单）：真 apply 一遍，资产仍在、临时物没了
    for p, _why, _size in items:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    assert (course / "deliver" / "run-2" / "it-002" / "weights.json").is_file()
    assert not (course / "it3").exists()
    assert course.is_dir(), "豁免了资产 ⇒ 这个目录本身不该被删掉"


def test_no_asset_keeps_old_behaviour_byte_for_byte(tmp_path: Path) -> None:
    """没有 `deliver/` 的运行目录：删除目标仍是**目录本身**（与旧行为逐字节一致，不多一层）。"""
    mod = _load_tmp_clean()
    tmp = tmp_path / "tmp"
    stale = tmp / "p3-vk1"
    _mkdir(stale, {"it1/per-game.json": "{}", "logs/run.log": "…"})
    items, _stats = mod.plan(_later(), keep_runs=0, keep_days=0, tmp_dir=tmp, running=False)
    assert _reasons(items) == [str(stale)]


def test_training_active_skips_run_roots_but_still_protects_asset(tmp_path: Path) -> None:
    """训练在跑 ⇒ 运行目录整体跳过（旧闸不变）：这时的删除名单里连兄弟都没有。"""
    mod = _load_tmp_clean()
    tmp = tmp_path / "tmp"
    course = tmp / "p4-fast"
    _mkdir(course, {"it5/w.json": "w", "deliver/run-3/it-003/weights.json": "p3"})
    items, stats = mod.plan(_later(), keep_runs=0, keep_days=0, tmp_dir=tmp, running=True)
    assert _reasons(items) == []
    assert stats["run"] == 0
    assert (course / "deliver" / "run-3" / "it-003" / "weights.json").is_file()


@pytest.mark.parametrize(
    "keep_runs,keep_days,apply,force,refuse",
    [
        (0, 0, True, False, True),  # 零保留 + --apply = 整片抹除（事故现场）
        (0, 0, True, True, False),  # 人明写 --force ⇒ 放行
        (0, 0, False, False, False),  # dry-run 的断言工具，合法
        (0, 14.0, True, False, False),
        (3, 0, True, False, False),
        (-1, -1, True, False, True),  # 负数同属「零保留」口径
    ],
)
def test_zero_retention_apply_needs_force(
    keep_runs: int, keep_days: float, apply: bool, force: bool, refuse: bool
) -> None:
    """零保留的 `--apply` 必须被闸住（2026-10-17 实测：一次传错参数抹掉 1907 项 / 38GB）。

    闸只放在 `--apply` 上：零保留作为 **dry-run** 的断言工具是用例自己在用的（上面几个用例），
    闸错地方会把它们一起堵死。
    """
    assert mod_refuses(keep_runs, keep_days, apply, force) is refuse


def mod_refuses(keep_runs: int, keep_days: float, apply: bool, force: bool) -> bool:
    """薄壳：每次现加载（模块加载无副作用，且避免测试间共享可变模块状态）。"""
    return bool(_load_tmp_clean().refuses_zero_retention(keep_runs, keep_days, apply, force))


def test_nested_temp_dir_still_keeps_the_asset(tmp_path: Path) -> None:
    """资产在**更深一层**时也要保住（实测教训：只刨一层会连坐——`tmp/__probe__/<课>/deliver/`）。"""
    mod = _load_tmp_clean()
    tmp = tmp_path / "tmp"
    nested = tmp / "probe-root" / "x1-rebirth-a2"
    _mkdir(nested, {"deliver/run-1/it-001/weights.json": "p", "it2/w.json": "w"})
    items, _stats = mod.plan(_later(), keep_runs=0, keep_days=0, tmp_dir=tmp, running=False)
    assert {os.path.relpath(p, tmp) for p in _reasons(items)} == {
        os.path.join("probe-root", "x1-rebirth-a2", "it2")
    }
    for p, _why, _size in items:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    assert (nested / "deliver" / "run-1" / "it-001" / "weights.json").is_file()
    assert not (nested / "it2").exists()


def test_imported_only_course_is_a_tempdir_and_asset_survives(tmp_path: Path) -> None:
    """只在 `deliver/` 里放过产物、本地没跑过训练的课程目录：按 tempdir 收敛，资产同样豁免。

    这是 §7-1③ 的**真形状**：`deliver/<run_id>/it-003/` 是带横线的名字 ⇒ `_is_run_root` 认不出
    它，于是整门课目录只算一个「工具临时目录」。旧行为在 `keep_days` 之后把它整个删掉。
    """
    mod = _load_tmp_clean()
    tmp = tmp_path / "tmp"
    course = tmp / "y20-dist2"
    _mkdir(course, {"deliver/seg-1/it-003/weights.json": "p", "deliver-import.log": "log"})
    items, stats = mod.plan(_later(), keep_runs=0, keep_days=0, tmp_dir=tmp, running=False)
    assert stats["run"] == 0, "没有 it<N>/ ⇒ 它不是一个 run root（别把这条用例的前提写错）"
    assert {os.path.relpath(p, tmp) for p in _reasons(items)} == {
        os.path.join("y20-dist2", "deliver-import.log")
    }


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
