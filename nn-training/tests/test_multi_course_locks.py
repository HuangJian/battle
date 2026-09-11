"""test_multi_course_locks.py — 多课程 per-course 单实例锁（P0-W2）。

plan: `plan/multi-course-parallel-training.md`（S1/S2、§1.2、P1c）。

现状（P0 红）：`run_rl.py` / `train_loop.py` 各持一把**全局**锁
（`.run_rl.lock` / `.train_loop.lock`），第二门课程直接拒启——多课并行训练的第一道
硬阻塞（2026-09-06 双 trainer 事故的护栏，护栏本身不能删，只能按课程实例化）。

目标（P1c）：锁文件名按课程派生（`.run_rl.<course>.lock`），无 `--course` 的老调用
沿用旧文件名（默认行为零变化）；`--force` 只接管**本课**的锁，绝不跨课抢夺。

打开节奏：本文件里 `@pytest.mark.skip` 标记的用例在 P1c 去掉标记后转绿。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from train.loop_util import acquire_lock, cleanup_lock

P1C = pytest.mark.skip(reason="P0 红：per-course 锁随 P1c 落盘")


def p1c_attr(name: str):
    """P0 骨架：per-course 锁 helper 随 P1c 落盘。

    用 getattr 而不是直接 import：P0 阶段该符号并不存在，直接 import 会让 mypy
    在门禁里报 `attr-defined`（红测试不得把门禁染红）。P1c 落盘时这几处换成静态
    `from train.loop_util import ...`，本 helper 一并删除。
    """
    from train import loop_util

    fn = getattr(loop_util, name, None)
    assert fn is not None, f"P1c 未落盘: train.loop_util.{name}"
    return fn


# ────────────────────────── 锁原语语义（现值即绿） ──────────────────────────


def test_two_course_lock_paths_coexist(tmp_path: Path) -> None:
    """双课程锁文件互不相干：两把同时持得住（per-course 实例化的最小前提）。"""
    a = str(tmp_path / ".run_rl.course-a.lock")
    b = str(tmp_path / ".run_rl.course-b.lock")
    assert acquire_lock(a) is True
    assert acquire_lock(b) is True
    assert os.path.exists(a) and os.path.exists(b)
    cleanup_lock(a)
    cleanup_lock(b)


def test_same_course_lock_path_refused(tmp_path: Path) -> None:
    """同一课程第二次持锁必须被拒绝（2026-09-06 双 trainer 事故回归）。"""
    p = str(tmp_path / ".run_rl.course-a.lock")
    assert acquire_lock(p) is True
    assert acquire_lock(p) is False
    cleanup_lock(p)


def test_force_takes_over_only_target_path(tmp_path: Path) -> None:
    """`--force` 只接管本课锁：别课锁文件不被触碰（跨课不误杀）。"""
    a = str(tmp_path / ".run_rl.course-a.lock")
    b = str(tmp_path / ".run_rl.course-b.lock")
    assert acquire_lock(a) is True
    assert acquire_lock(b) is True
    before = Path(b).read_text(encoding="utf-8")
    assert acquire_lock(a, force=True) is True  # 接管 A（自己的锁）
    assert Path(a).exists()
    assert Path(b).read_text(encoding="utf-8") == before  # B 的锁纹丝不动
    cleanup_lock(a)
    cleanup_lock(b)


# ────────────────────────── per-course 锁路径映射（P1c 转绿） ──────────────────────────


@P1C
def test_course_lock_path_is_course_keyed() -> None:
    """课程名进锁文件名；同 kind 不同课程互不相同。"""
    course_lock_path = p1c_attr("course_lock_path")

    a = course_lock_path(str(NN_ROOT), "s1", "run_rl")
    b = course_lock_path(str(NN_ROOT), "s-dodge", "run_rl")
    assert a != b
    assert a.endswith(".run_rl.s1.lock")
    assert b.endswith(".run_rl.s-dodge.lock")
    tl = course_lock_path(str(NN_ROOT), "s1", "train_loop")
    assert tl.endswith(".train_loop.s1.lock")


@P1C
def test_no_course_keeps_legacy_lock_name() -> None:
    """无 --course 的老调用沿用旧文件名——默认行为零变化（plan §0.5-4）。"""
    course_lock_path = p1c_attr("course_lock_path")

    assert course_lock_path(str(NN_ROOT), "", "run_rl").endswith(".run_rl.lock")
    assert course_lock_path(str(NN_ROOT), "", "train_loop").endswith(".train_loop.lock")


@P1C
def test_course_name_traversal_rejected() -> None:
    """课程名含 `..` 或越界字符 → 响亮拒绝（锁文件不得写出目录）。"""
    course_lock_path = p1c_attr("course_lock_path")
    validate_course_name = p1c_attr("validate_course_name")

    with pytest.raises(ValueError):
        validate_course_name("..")
    with pytest.raises(ValueError):
        validate_course_name("../evil")
    with pytest.raises(ValueError):
        validate_course_name("a/b")
    with pytest.raises(ValueError):
        course_lock_path(str(NN_ROOT), "../evil", "run_rl")
    assert validate_course_name("s-dodge_2.b") == "s-dodge_2.b"


@P1C
def test_train_loop_cli_accepts_course_flag() -> None:
    """CLI 层：`train_loop.py --course x --help` 可达（P0 红为 unrecognized arguments）。

    没有 `--course` 参数时，CLI 层永远到不了 per-course 锁路径——这条断言就是
    「参数已接线」的证据（F-B4）。
    """
    proc = subprocess.run(
        [sys.executable, str(NN_ROOT / "train_loop.py"), "--course", "s1", "--help"],
        cwd=str(NN_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "--course" in proc.stdout
