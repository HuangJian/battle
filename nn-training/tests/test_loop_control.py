"""R2d 操作面：控制文件契约（`rl/loop_control.py`）。

控制台（TS 侧 `server/actions/loop-control.ts`）写、训练侧读 —— 两侧唯一的耦合就是这份 JSON
的形状，所以这里逐条钉住「什么算合法」「坏掉时往哪边保守」，以及**只在意图变化时**产出日志
（训练侧每拍读一次，不能刷屏）。

保守方向是这套东西的核心性质：**读不到 / 解析失败 ⇒ 当作没有暂停意图**（继续训练）。
反过来「把坏文件当暂停」会让控制面一坏就整条腿停摆 —— 那是比不暂停严重得多的故障。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.loop_control import (
    Control,
    ControlApplier,
    applied_path,
    control_path,
    parse_control,
    read_control,
    write_applied,
)

# --------------------------------------------------------------- 解析


def test_parse_accepts_the_documented_shape() -> None:
    c = parse_control({"version": 1, "paused": ["c5", "c4-dodge"], "note": "控制台"})

    assert c.found and c.error == ""
    assert c.paused == frozenset({"c5", "c4-dodge"})


def test_parse_empty_or_missing_paused_means_nothing_paused() -> None:
    assert parse_control({"version": 1}).paused == frozenset()
    assert parse_control({"version": 1, "paused": None}).paused == frozenset()
    assert parse_control({"version": 1, "paused": []}).paused == frozenset()


@pytest.mark.parametrize("raw", [None, [], "c5", 3])
def test_parse_rejects_a_non_object_root(raw: object) -> None:
    c = parse_control(raw)

    assert c.paused == frozenset() and c.error and not c.found


def test_parse_rejects_a_non_list_paused() -> None:
    c = parse_control({"paused": "c5"})

    assert c.paused == frozenset() and "不是数组" in c.error


@pytest.mark.parametrize("bad", ["../etc", "a/b", "c5 tick", "", "c5\\x", "c5:"])
def test_parse_ignores_illegal_course_names(bad: str) -> None:
    """非法名既不能进 `paused`（否则调度器里根本没有这门课），也不能让整份意图作废。"""
    c = parse_control({"paused": [bad, "c5"]})

    assert c.paused == frozenset({"c5"})  # 合法的那门照常生效
    assert "非法课程名" in c.error


def test_illegal_name_is_not_a_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`..` / 分隔符一律拒收 —— 控制文件的内容不该能当路径用（即便今天只用来查字典）。"""
    c = parse_control({"paused": ["..", "a/../b"]})

    assert c.paused == frozenset()


# --------------------------------------------------------------- 读盘


def test_read_missing_file_is_empty_intent_not_an_error(tmp_path: Path) -> None:
    c = read_control(str(tmp_path / "nope.json"))

    assert c == Control() and not c.found and c.error == ""


def test_read_broken_json_conservatively_keeps_training(tmp_path: Path) -> None:
    p = tmp_path / "loop-control.json"
    p.write_text("{ 这不是 JSON", encoding="utf-8")

    c = read_control(str(p))

    assert c.paused == frozenset()  # ★ 保守：坏文件 ≠ 全停
    assert c.error.startswith("控制文件读失败")


def test_read_ignores_extra_keys(tmp_path: Path) -> None:
    """控制台可以自由加注释字段（如 `updatedAt`）——训练侧只看 `paused`。"""
    p = tmp_path / "loop-control.json"
    p.write_text(json.dumps({"paused": ["c5"], "updatedAt": 123, "who": "console"}), "utf-8")

    assert read_control(str(p)).paused == frozenset({"c5"})


def test_control_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert control_path().endswith(str(Path("tmp") / "loop-control.json"))
    monkeypatch.setenv("NN_LOOP_CONTROL", str(tmp_path / "x.json"))
    assert control_path() == str(tmp_path / "x.json")


# --------------------------------------------------------------- 施加


class FakeQueue:
    def __init__(self, state: str = "ready") -> None:
        self.state = state


class FakeSup:
    """调度器替身：只记状态与调用（`pause`/`resume` 的语义由 `loop_scheduler` 的用例钉）。"""

    def __init__(self, *courses: str) -> None:
        self.courses = {c: FakeQueue() for c in courses}
        self.calls: list[tuple[str, str, str]] = []

    def pause(self, course: str, reason: str = "") -> None:
        self.courses[course].state = "paused"
        self.calls.append(("pause", course, reason))

    def resume(self, course: str) -> None:
        self.courses[course].state = "ready"
        self.calls.append(("resume", course, ""))


def test_apply_pauses_and_resumes_idempotently() -> None:
    sup = FakeSup("a", "b")
    applier = ControlApplier()

    lines = applier.apply(sup, parse_control({"paused": ["a"]}))
    assert [c for c, _, _ in sup.calls] == ["pause"]
    assert sup.courses["a"].state == "paused" and sup.courses["b"].state == "ready"
    assert any("暂停课程 a" in ln for ln in lines)
    # 第二次读同一份意图：**不再调调度器、也不再产出日志**（每拍都读，不能刷屏）
    assert applier.apply(sup, parse_control({"paused": ["a"]})) == []
    assert len(sup.calls) == 1

    again = applier.apply(sup, parse_control({"paused": []}))
    assert [c for c, _, _ in sup.calls] == ["pause", "resume"]
    assert sup.courses["a"].state == "ready"
    assert any("恢复课程 a" in ln for ln in again)


def test_apply_leaves_untouched_courses_alone() -> None:
    """暂停一门课**不碰**其它课的队列状态（故障/操作域都是单课）。"""
    sup = FakeSup("a", "b")
    sup.courses["b"].state = "done"

    ControlApplier().apply(sup, parse_control({"paused": ["a"]}))

    assert sup.courses["b"].state == "done"


def test_apply_reports_a_broken_control_file_once() -> None:
    """坏文件只报一次（记住上次的错误签名）——否则每秒一行把训练日志淹掉。"""
    sup = FakeSup("a")
    applier = ControlApplier()
    broken = Control(error="控制文件根不是对象")

    first = applier.apply(sup, broken)
    assert len(first) == 1 and "保守：继续训练" in first[0]
    assert applier.apply(sup, broken) == []
    # 错误内容变了（换了一种坏法）⇒ 再报一次
    assert len(applier.apply(sup, Control(error="paused 不是数组"))) == 1
    # 修好之后：清掉错误记忆，且本拍无变化（无输出）
    assert applier.apply(sup, parse_control({"paused": []})) == []
    # 又坏回去 ⇒ 重新报一次（错误记忆已被「修好」这件事清掉）
    assert len(applier.apply(sup, broken)) == 1


# --------------------------------------------------------------- 回执（意图 ≠ 事实）


def test_write_applied_shape_is_readable_by_the_console(tmp_path: Path) -> None:
    """回执形状：`pid` 是控制台分辨「残留文件」（进程已死）与「真的在暂停着」的唯一依据。"""
    f = tmp_path / "applied.json"

    assert write_applied({"c5", "c4"}, str(f)) == ""
    body = json.loads(f.read_text(encoding="utf-8"))

    assert body["pid"] == os.getpid()
    assert body["paused"] == ["c4", "c5"]  # 排序：同一份意图永远同一个字节（便于对拍）
    assert body["version"] == 1 and isinstance(body["at"], float)
    assert list(tmp_path.iterdir()) == [f]  # 原子写不留 tmp 残渣


def test_write_applied_failure_is_reported_not_raised(tmp_path: Path) -> None:
    """回执写不进去**不能影响训练**（它是观测面）：返回错误文案而不是抛。"""
    assert write_applied([], str(tmp_path / "nope" / "x.json")) != ""


def test_applier_writes_the_receipt_on_first_apply_and_on_change(tmp_path: Path) -> None:
    sup = FakeSup("a", "b")
    f = tmp_path / "applied.json"
    applier = ControlApplier(applied_file=str(f))

    applier.apply(sup, parse_control({"paused": ["b"]}))
    assert json.loads(f.read_text(encoding="utf-8"))["paused"] == ["b"]

    applier.apply(sup, parse_control({"paused": []}))
    assert json.loads(f.read_text(encoding="utf-8"))["paused"] == []


def test_applier_does_not_rewrite_the_receipt_when_nothing_changed(tmp_path: Path) -> None:
    """意图未变 ⇒ **一次写盘都不发生**（每拍都读、但绝不心跳式写盘）。

    用「删掉文件再施加同一个意图」验证：文件仍然不存在 ⇒ 确实没写。
    """
    sup = FakeSup("a")
    f = tmp_path / "applied.json"
    applier = ControlApplier(applied_file=str(f))
    applier.apply(sup, parse_control({"paused": ["a"]}))
    f.unlink()

    assert applier.apply(sup, parse_control({"paused": ["a"]})) == []
    assert not f.exists()


def test_applied_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert applied_path().endswith(str(Path("tmp") / "loop-control.applied.json"))
    monkeypatch.setenv("NN_LOOP_CONTROL_APPLIED", str(tmp_path / "a.json"))
    assert applied_path() == str(tmp_path / "a.json")


def test_apply_on_an_empty_scheduler_is_harmless() -> None:
    """发现模式下「刚起、一门课都没有」是合法稳态：施加意图不得报错、不得产出噪音。"""
    sup = FakeSup()

    assert ControlApplier().apply(sup, parse_control({"paused": ["c5"]})) == []
