"""R2d：日志**行路由**（`biz/log.py` 的 `prefix_scope` / `open_course_sink`）。

单进程服务多门课不能换 `sys.stdout`（两个 Tee 套起来会把每行复制进两份课日志），所以课程
归属改成行级：`log()` 在前缀作用域内带 `[课]` 前缀，并**同一行**镜像到该课自己的日志文件。
无前缀时行为必须与改造前逐字节相同（单课程入口的 `Tee` 路径不受影响）。
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.log import (
    LogRouter,
    close_course_sink,
    close_course_sinks,
    format_line,
    get_prefix,
    log,
    open_course_sink,
    prefix_scope,
)
from common.runtime import Runtime

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


def test_explicit_course_overrides_global_prefix_and_sink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T12（plan/eval-gap-honesty §6）：`log(course=)` 不经 `_PREFIX`——标签与**镜像目标**
    都归显式课程。这是 eval 后台线程的现场：它活过 `prefix_scope`，读到的是别的课的前缀
    （实测 4 条 `it5 DONE` 里 3 条是错的）。
    """
    close_course_sinks()
    f_a = tmp_path / "a.log"
    f_b = tmp_path / "b.log"
    assert open_course_sink("a", f_a) is not None
    assert open_course_sink("b", f_b) is not None
    try:
        with prefix_scope("b"):
            log("scoped line")  # 走 _PREFIX ⇒ b
            log("explicit line", course="a")  # 显式 ⇒ a
            log("other line", course="b")  # 显式 ⇒ b
    finally:
        close_course_sinks()
    out = capsys.readouterr().out
    printed = out.splitlines()
    assert any(ln.endswith("[a] explicit line") for ln in printed)
    assert any(ln.endswith("[b] scoped line") for ln in printed)
    a_lines = f_a.read_text(encoding="utf-8")
    b_lines = f_b.read_text(encoding="utf-8")
    # 镜像目标按 **显式 course** 走：a 的文件里不许出现另外两行
    assert a_lines.count("explicit line") == 1
    assert a_lines.count("scoped line") == 0 and a_lines.count("other line") == 0
    assert b_lines.count("scoped line") == 1 and b_lines.count("other line") == 1
    assert b_lines.count("explicit line") == 0


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

    from common.log import default_router

    sinks = default_router().sinks
    sinks["a"] = Boom()
    with prefix_scope("a"):
        log("still visible")  # 镜像失败只吞
    assert "still visible" in capsys.readouterr().out
    sinks.pop("a", None)


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


def test_mirror_failure_is_reported_once(capsys: pytest.CaptureFixture[str]) -> None:
    """镜像写失败**不再静默**：控制台照常，但同一门课只喊一次 WARN（不许每行刷）。

    为什么钉住它：以前这里是裸 `pass` ⇒「该课日志在丢行」与「这课本来就没日志」长得一模一样，
    只能事后比对才发现少了半天。喊多了又会把控制台刷成噪音，所以按 `mirror:<课>` 去重。
    """
    from common.log import LogRouter

    class Boom:
        def write(self, _s: str) -> None:
            raise RuntimeError("disk full")

        def flush(self) -> None:
            raise RuntimeError("disk full")

    router = LogRouter()
    router.sinks["a"] = Boom()
    with router.prefix_scope("a"):
        router.log("line-1")
        router.log("line-2")

    out = capsys.readouterr().out.splitlines()
    echoed = [ln for ln in out if ln.endswith(" line-1") or ln.endswith(" line-2")]
    assert len(echoed) == 2, out
    warns = [ln for ln in out if "[log] WARN" in ln]
    assert len(warns) == 1, out
    # 文案用切分对账（不是子串断言）：课程与失败原因各就各位。
    head, _, cause = warns[0].partition("（")
    assert head.endswith("课 a 的日志镜像写失败")
    assert cause.startswith("RuntimeError: disk full")


def test_close_failure_is_reported_once(capsys: pytest.CaptureFixture[str]) -> None:
    """句柄关不上也喊一次（同一课只喊一次），且绝不把异常带到训练收尾路径。"""
    from common.log import LogRouter

    class BadClose:
        def write(self, _s: str) -> None:
            pass

        def flush(self) -> None:
            pass

        def close(self) -> None:
            raise OSError("already gone")

    router = LogRouter()
    router.sinks["c7"] = BadClose()
    router.close_sink("c7")  # 不抛
    router.sinks["c7"] = BadClose()
    router.close_sink("c7")

    warns = [ln for ln in capsys.readouterr().out.splitlines() if "[log] WARN" in ln]
    assert len(warns) == 1, warns
    head, _, cause = warns[0].partition("（")
    assert head.endswith("课 c7 的日志句柄关闭失败")
    assert cause.startswith("OSError: already gone")


# ───────────────── 线程边界（2026-11-20）：隔离与继承，两条都要钉 ─────────────────


def test_prefix_does_not_leak_across_threads() -> None:
    """`ContextVar` 的**隔离**半边：一个线程置的前缀不会出现在别的线程里。

    旧实现是模块级字符串，于是「A 课线程置的前缀」会被 B 课线程读到（现场：4 条 `it5 DONE`
    里 3 条前缀是错的）。这里钉的是现在的语义：**新线程从空上下文开始**。
    """
    router = LogRouter()
    seen: dict[str, str] = {}

    def child() -> None:
        seen["start"] = router.get_prefix()
        router.set_prefix("from-child")
        seen["after"] = router.get_prefix()

    with router.prefix_scope("from-main"):
        t = threading.Thread(target=child)
        t.start()
        t.join()
        assert router.get_prefix() == "from-main", "子线程的写法不许回灌父线程"

    assert seen == {"start": "", "after": "from-child"}


def test_runtime_threads_inherit_the_course_prefix(capsys: pytest.CaptureFixture[str]) -> None:
    """`Runtime.thread` 的**继承**半边：`prefix_scope` 里派生的线程，行仍带 `[课]`。

    为什么这条必须存在：`contextvars` 不自动进新线程，而 `Runtime.thread` 是本仓 75 处派生
    线程的**所有者**（§122）。没有传播的话，`prefix_scope(课)` 里起的 weights-push / rescan /
    采样线程发出来的每一行都会静默丢掉课标签 —— 旧实现（进程级字符串）反而是带标签的。
    """
    router = LogRouter()
    seen: list[str] = []
    rendered: list[str] = []

    def child() -> None:
        router.log("from-child")
        # 同一渲染函数算一遍作为期望值：全等比较（不是「文本里有没有」）。
        rendered.append(router.format_line("from-child"))
        seen.append(router.get_prefix())

    with router.prefix_scope("c9-dodge"):
        rt = Runtime(scope="log-ctx")
        try:
            rt.thread(child, name="child").join()
        finally:
            rt.close()

    assert seen == ["c9-dodge"]
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "from-child" in ln]
    assert lines == rendered, lines


def test_runtime_submit_and_map_inherit_the_course_prefix() -> None:
    """池的两个 Runtime 入口（`submit` / `map`）同样传播——绕过它们直接投 `rt.pool()` 则不传播。"""
    router = LogRouter()
    with router.prefix_scope("c9-dodge"):
        rt = Runtime(scope="log-ctx")
        try:
            got = rt.submit(router.get_prefix).result()
            mapped = rt.map(lambda _i: router.get_prefix(), [0, 1])
        finally:
            rt.close()
    assert got == "c9-dodge"
    assert mapped == ["c9-dodge", "c9-dodge"]


def test_sinks_are_per_router_instance(tmp_path: Path) -> None:
    """课日志句柄住在 `LogRouter` 上：两个 router 的 sink 集合互不相见（显式所有权）。"""
    a, b = LogRouter(), LogRouter()
    fh = a.open_sink("c1", tmp_path / "a.log")
    assert fh is not None
    try:
        assert set(a.sinks) == {"c1"}
        assert b.sinks == {}, "另一个 router 不得看到别人的课日志句柄"
        b.log("not-mirrored", course="c1")  # b 没有 c1 的 sink ⇒ 只上控制台
        assert (tmp_path / "a.log").read_text(encoding="utf-8") == ""
    finally:
        a.close_sinks()
