"""test_multi_course_locks.py — 多课程 per-course 单实例锁（P0-W2 / P1c）。

plan: `plan/multi-course-parallel-training.md`（S1/S2、§1.2、P1c）。

P0 时的现状：`run_rl.py` / `train_loop.py` 各持一把**全局**锁
（`.run_rl.lock` / `.train_loop.lock`），第二门课程直接拒启——多课并行训练的第一道
硬阻塞（2026-09-06 双 trainer 事故的护栏，护栏本身不能删，只能按课程实例化）。

P1c 后：锁文件名按课程派生（`.run_rl.<course>.lock`），无 `--course` 的老调用
沿用旧文件名（默认行为零变化）；`--force` 只接管**本课**的锁，绝不跨课抢夺。
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

from tests.subproc_util import run_utf8
from train.loop_util import (
    acquire_lock,
    cleanup_lock,
    course_key_from_path,
    course_lock_path,
    validate_course_name,
)

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


# ────────────────────────── per-course 锁路径映射（P1c） ──────────────────────────


def test_course_lock_path_is_course_keyed() -> None:
    """课程名进锁文件名；同 kind 不同课程互不相同。"""
    a = course_lock_path(str(NN_ROOT), "s1", "run_rl")
    b = course_lock_path(str(NN_ROOT), "s-dodge", "run_rl")
    assert a != b
    assert a.endswith(".run_rl.s1.lock")
    assert b.endswith(".run_rl.s-dodge.lock")
    tl = course_lock_path(str(NN_ROOT), "s1", "train_loop")
    assert tl.endswith(".train_loop.s1.lock")


def test_no_course_keeps_legacy_lock_name() -> None:
    """无 --course 的老调用沿用旧文件名——默认行为零变化（plan §0.5-4）。"""
    assert course_lock_path(str(NN_ROOT), "", "run_rl").endswith(".run_rl.lock")
    assert course_lock_path(str(NN_ROOT), "", "train_loop").endswith(".train_loop.lock")


def test_course_name_traversal_rejected() -> None:
    """课程名含 `..` 或越界字符 → 响亮拒绝（锁文件不得写出目录）。"""
    with pytest.raises(ValueError):
        validate_course_name("..")
    with pytest.raises(ValueError):
        validate_course_name("../evil")
    with pytest.raises(ValueError):
        validate_course_name("a/b")
    with pytest.raises(ValueError):
        course_lock_path(str(NN_ROOT), "../evil", "run_rl")
    assert validate_course_name("s-dodge_2.b") == "s-dodge_2.b"


def test_train_loop_cli_accepts_course_flag() -> None:
    """CLI 层：`train_loop.py --course x --help` 可达（P0 时为 unrecognized arguments）。

    没有 `--course` 参数时，CLI 层永远到不了 per-course 锁路径——这条断言就是
    「参数已接线」的证据（F-B4）。
    """
    proc = run_utf8(
        [sys.executable, str(NN_ROOT / "train_loop.py"), "--course", "s1", "--help"],
        cwd=str(NN_ROOT),
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "--course" in proc.stdout


# ────────────────────────── run_rl 锁原语（stale 接管 + 同课拒启，跨平台） ──────────────────────────


def _dead_pid() -> int:
    """确定已退出的 PID（spawn 后立即 wait）——stale 锁的确定性持有者。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_runrl_stale_lock_taken_over(tmp_path: Path) -> None:
    """锁持有人已死 → 自动接管（stale 清理后重持，不拒启）。

    回归：`run_rl._runrl_pid_alive` 曾把 Windows 的 ctypes.windll 分支写成无条件
    路径——Linux 上凡遇**已存在**的锁文件（无论持有者死活）一律 AttributeError，
    陈旧锁永不清理、同课双开变成崩溃而非响亮拒启（2026-09-13 s1/s-dodge 双课
    验收实测：P1 验收遗留的 stale 锁让第二次启动当场崩）。"""
    from run_rl import _acquire_run_rl_lock, _cleanup_run_rl_lock

    p = str(tmp_path / ".run_rl.course-a.lock")
    Path(p).write_text(f"{_dead_pid()}|python|0", encoding="utf-8")
    try:
        assert _acquire_run_rl_lock(p) is True
    finally:
        _cleanup_run_rl_lock(p)


def test_runrl_same_course_refused_while_holder_alive(tmp_path: Path) -> None:
    """同课双开且持有人活着 → 响亮拒启（返回 False），不是异常崩溃。"""
    from run_rl import _acquire_run_rl_lock, _cleanup_run_rl_lock

    p = str(tmp_path / ".run_rl.course-a.lock")
    Path(p).write_text(f"{os.getpid()}|python|0", encoding="utf-8")
    try:
        assert _acquire_run_rl_lock(p) is False
    finally:
        _cleanup_run_rl_lock(p)


# ────────────────────────── 命名空间键 = 文件 stem（P1c 修正） ──────────────────────────


def test_course_key_is_file_stem_not_inner_name() -> None:
    """锁/账本/槽位/日志的命名空间键 = 课程文件 stem，与 TS launcher 的 peekCourse 同键。

    回归：s-dodge.jsonc 内部 `name` 是 `s-dodge-mix`——若锁用内部名，
    TS 预检（查 .run_rl.s-dodge.lock）与 PY 实际锁（.run_rl.s-dodge-mix.lock）
    对不上，双课 preflight/kill 全部错位。键必须是 stem。
    """
    assert course_key_from_path(str(NN_ROOT / "curricula" / "s-dodge.jsonc")) == "s-dodge"
    assert course_key_from_path(str(NN_ROOT / "curricula" / "s5-open20.jsonc")) == "s5-open20"
    assert course_key_from_path("") == ""  # 无课程 → 老调用（默认行为零变化）
    lock = course_lock_path(str(NN_ROOT), course_key_from_path("curricula/s-dodge.jsonc"), "run_rl")
    assert lock.endswith(".run_rl.s-dodge.lock")
    with pytest.raises(ValueError):
        course_key_from_path("..")  # stem 上跳 → 响亮拒绝
    with pytest.raises(ValueError):
        course_key_from_path("curricula/bad name!.jsonc")  # 越界字符 → 响亮拒绝


# ────────────────────────── 并发 push 串行化（P1c F-C5） ──────────────────────────


def test_push_lock_serializes_concurrent_push(tmp_path: Path) -> None:
    """repo 级 push 锁：首个持有者成功，第二把（另一课）被拒绝——并发 push 被串行化。

    同一实现（acquire_lock），只是 tag 不同；持锁者释放后下一课可进。
    """
    p = str(tmp_path / ".git_push.lock")
    assert acquire_lock(p, tag="git push") is True
    assert acquire_lock(p, tag="git push") is False  # 第二课跳过 Loud 日志路径
    cleanup_lock(p)
    assert acquire_lock(p, tag="git push") is True  # 释放后可进
    cleanup_lock(p)
