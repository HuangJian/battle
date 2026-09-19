"""R2d：日志**行路由**（`rl/log.py` 的 `prefix_scope` / `open_course_sink`）。

单进程服务多门课不能换 `sys.stdout`（两个 Tee 套起来会把每行复制进两份课日志），所以课程
归属改成行级：`log()` 在前缀作用域内带 `[课]` 前缀，并**同一行**镜像到该课自己的日志文件。
无前缀时行为必须与改造前逐字节相同（单课程入口的 `Tee` 路径不受影响）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.log import (
    close_course_sink,
    close_course_sinks,
    format_line,
    get_prefix,
    log,
    open_course_sink,
    prefix_scope,
)

STAMPED = re.compile(r"^\[\d\d:\d\d:\d\d\] ")


def test_unanchored_line_is_byte_identical_to_before() -> None:
    """没有前缀 ⇒ 与改造前同一形状（`[HH:MM:SS] msg`，不带课程标签）。"""
    close_course_sinks()
    line = format_line("hello")
    assert STAMPED.match(line)
    assert line.endswith("] hello") and "[c4" not in line


def test_prefix_is_scoped_and_restored(capsys: pytest.CaptureFixture[str]) -> None:
    close_course_sinks()
    log("outer")
    with prefix_scope("c4-dodge"):
        log("inner")
        assert get_prefix() == "c4-dodge"
    log("outer2")
    out = capsys.readouterr().out.splitlines()
    assert "outer" in out[0] and "[c4-dodge]" not in out[0]
    assert "[c4-dodge] inner" in out[1]
    assert "[c4-dodge]" not in out[2]
    assert get_prefix() == ""


def test_scope_restores_prefix_even_on_exception() -> None:
    close_course_sinks()
    with pytest.raises(RuntimeError), prefix_scope("c4"):
        raise RuntimeError("boom")
    assert get_prefix() == ""


def test_lines_are_mirrored_to_that_courses_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """前缀命中 ⇒ 同一行也进该课日志；别课/无前缀的行不串进去。"""
    close_course_sinks()
    f_a = tmp_path / "a.log"
    f_b = tmp_path / "b.log"
    assert open_course_sink("a", f_a) is not None
    assert open_course_sink("b", f_b) is not None

    with prefix_scope("a"):
        log("for-a")
    log("for-nobody")
    with prefix_scope("b"):
        log("for-b")
    close_course_sinks()

    a_text = f_a.read_text(encoding="utf-8")
    b_text = f_b.read_text(encoding="utf-8")
    assert "[a] for-a" in a_text and "for-b" not in a_text and "for-nobody" not in a_text
    assert "[b] for-b" in b_text and "for-a" not in b_text
    # 控制台仍看到全部（镜像不是替换）
    out = capsys.readouterr().out
    assert "for-a" in out and "for-nobody" in out and "for-b" in out


def test_missing_or_bad_sink_never_breaks_logging(capsys: pytest.CaptureFixture[str]) -> None:
    close_course_sinks()
    assert open_course_sink("a", "") is None  # 空路径 = 不注册（无 out_log 的老调用）

    class Boom:
        def write(self, _s: str) -> None:
            raise OSError("disk gone")

        def flush(self) -> None:
            raise OSError("disk gone")

    from rl.log import _COURSE_SINKS

    _COURSE_SINKS["a"] = Boom()
    with prefix_scope("a"):
        log("still visible")  # 镜像失败只吞
    assert "still visible" in capsys.readouterr().out
    _COURSE_SINKS.pop("a", None)


def test_reopen_same_course_replaces_handle(tmp_path: Path) -> None:
    """同一课重复注册 ⇒ 先关旧的（不泄漏句柄、不写两份）。"""
    close_course_sinks()
    first = tmp_path / "one.log"
    second = tmp_path / "two.log"
    h1 = open_course_sink("a", first)
    h2 = open_course_sink("a", second)
    assert h1 is not None and h2 is not None and h1 is not h2
    assert h1.closed
    with prefix_scope("a"):
        log("only-in-two")
    close_course_sink("a")
    assert "only-in-two" in second.read_text(encoding="utf-8")
    assert first.read_text(encoding="utf-8") == ""
    close_course_sink("a")  # 幂等
