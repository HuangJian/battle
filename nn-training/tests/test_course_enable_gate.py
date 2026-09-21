"""开课闸（2026-09-21 事故，plan/accident.plan.md §1）。

现象：CLI detached 直启绕过了控制台开课流程，连带漏掉三件套——开课标记
（dashboard 不可见）、暂停意图解禁（两臂显示「已暂停」）、hub 课程模式；agent 只好
手写控制台拥有的状态文件（`training-enabled.txt` / `loop-control.json`）。

契约：训练循环**只**认控制台写的开课标记；判据同源 = `rl.loop_plan.course_enabled`
（禁第二份"标记存在性检查"）。范围只卡训练循环——`--smoke` 预演、collect-only
（调用点在其后）、无课程的 legacy 路径都不受影响。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.protocol import COURSE_ENABLE_MARKER
from run_rl import _require_course_open


def _args(**over: object) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(course_obj=object(), course_name="x20-clutch", smoke=False)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_missing_marker_refuses_startup(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as e:
        _require_course_open(_args(), tmp_path)
    msg = str(e.value)
    assert "未开课" in msg
    assert COURSE_ENABLE_MARKER in msg  # 指路文案带上缺失的那个文件名
    assert "dashboard" in msg  # 指向唯一入口
    assert "x20-clutch" in msg  # 报出是哪门课


def test_marker_present_allows_startup(tmp_path: Path) -> None:
    (tmp_path / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    _require_course_open(_args(), tmp_path)  # 不抛即通过


def test_smoke_is_exempt(tmp_path: Path) -> None:
    """冒烟预演不受影响（计划 §1.2 的显式范围）。"""
    _require_course_open(_args(smoke=True), tmp_path)  # 无标记也不拦


def test_legacy_non_course_path_unchanged(tmp_path: Path) -> None:
    """无课程的老用法逐字节不变（开课标记只存在于课程 traj 目录下）。"""
    _require_course_open(_args(course_obj=None), tmp_path)
