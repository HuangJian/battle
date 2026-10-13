"""R2d 操作面：控制文件契约（`trainer/loop_control.py`）。

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
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_control import (
    Control,
    ControlApplier,
    applied_path,
    consume_redo,
    control_path,
    parse_control,
    parse_redo,
    read_control,
    read_redo,
    read_redo_applied,
    redo_path,
    write_applied,
    write_redo_applied,
)


@pytest.fixture(autouse=True)
def _loop_control_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """控制面（暂停意图 + 回执）是**控制台与本机共享的活状态**：一律重定向进 tmp_path。

    不重定向就会读/写仓根 `tmp/loop-control*.json`（操作员此刻的暂停列表会参与判定，
    回执也会把测试进程的 pid 写进活状态）——2026-10-02 实测：全量测试把 `["c5"]`
    写进过真回执。
    """
    monkeypatch.setenv("NN_LOOP_CONTROL", str(tmp_path / "loop-control.json"))
    monkeypatch.setenv("NN_LOOP_CONTROL_APPLIED", str(tmp_path / "loop-control.applied.json"))


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


# ------------------------------------------------- ★M2：`held`（hub 事实的缓存）


def test_held_missing_or_old_file_is_an_empty_set_not_an_error() -> None:
    """旧控制台写的文件没有 `held` / 形状不对 ⇒ 空集（退回 hub 直问），**不算错**。

    这条是 F13 的第二条前提（「旧 trainer × 新文件」的反向）：新 trainer 不得因为一个
    它不认识的键而拒绝整份意图（那会把 `paused` 一起屏蔽掉）。
    """
    assert parse_control({"paused": [], "held": None}).held == frozenset()
    assert parse_control({"paused": []}).held == frozenset()
    assert parse_control({"paused": [], "held": "c5"}).held == frozenset()
    assert parse_control({"paused": [], "held": "c5"}).error  # 形状不对要说出来（但不影响 paused）


def test_held_is_judged_live_in_place_with_hub_stale_window() -> None:
    """★ 就地 900s 自判活（与 hub 的 `hold_state` **同一把尺子**）。

    为什么必须在本机再判一次：缓存是控制台某一时刻拍的快照，云机可能已经掉线——
    拿旧快照当事实 = 「本机不跑 + 云机也不跑」的静默停摆（那正是旧模型的病）。
    """
    now = 1_760_000_000.0
    c = parse_control(
        {
            "paused": [],
            "held": [
                {"course": "fresh", "last_progress_at": now - 10},
                {"course": "edge", "last_progress_at": now - 900},  # 恰好窗内
                {"course": "stale", "last_progress_at": now - 901},
                {"course": "zero", "last_progress_at": 0},  # 零进度 = 不算活着
                {"course": "missing"},
            ],
        },
        now=now,
    )

    assert c.held == frozenset({"fresh", "edge"})
    # 非法课名 / 坏条目一律丢掉（缓存坏了不该让整份意图作废）
    bad = parse_control({"paused": [], "held": [{"course": "../etc", "last_progress_at": now}]})
    assert bad.held == frozenset() and "不合法" in bad.error
    # 超窗的课**不进** held：本机照常跑（保守方向 = 继续训练）
    assert "stale" not in c.held


def test_read_control_uses_wall_clock_for_held(tmp_path: Path) -> None:
    """读盘路径也自判活（`now` 缺省 = 墙钟）——同机时钟前提写在这里。"""
    p = tmp_path / "loop-control.json"
    p.write_text(
        json.dumps(
            {
                "paused": ["p1"],
                "held": [
                    {"course": "c5", "last_progress_at": time.time() - 5},
                    {"course": "c6", "last_progress_at": time.time() - 10_000},
                ],
            }
        ),
        "utf-8",
    )

    c = read_control(str(p))

    assert c.paused == frozenset({"p1"}) and c.held == frozenset({"c5"})


def test_control_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NN_LOOP_CONTROL")  # 夹具已重定向；本用例专测**缺省**路径
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
    assert body["held"] == []  # ★M2：缺省空表（旧调用方逐字不变）
    assert list(tmp_path.iterdir()) == [f]  # 原子写不留 tmp 残渣


def test_applier_receipt_carries_the_held_cache_it_read(tmp_path: Path) -> None:
    """★M2：回执里的 `held` = 训练侧**实际读到并认下**的那批（缓存是否被读到的唯一凭据）。"""
    sup = FakeSup("a", "b")
    f = tmp_path / "applied.json"
    applier = ControlApplier(applied_file=str(f))

    fresh = {"course": "a", "last_progress_at": time.time() - 3}
    applier.apply(sup, parse_control({"paused": [], "held": [fresh]}))
    body = json.loads(f.read_text(encoding="utf-8"))
    assert body["held"] == ["a"] and body["paused"] == []
    # 缓存变旧（超窗）⇒ 回执照样刷新成空表（否则控制台会以为它还在被读）
    stale = {"course": "a", "last_progress_at": time.time() - 10_000}
    applier.apply(sup, parse_control({"paused": [], "held": [stale]}))
    assert json.loads(f.read_text(encoding="utf-8"))["held"] == []


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
    monkeypatch.delenv("NN_LOOP_CONTROL_APPLIED")  # 夹具已重定向；本用例专测**缺省**路径
    assert applied_path().endswith(str(Path("tmp") / "loop-control.applied.json"))
    monkeypatch.setenv("NN_LOOP_CONTROL_APPLIED", str(tmp_path / "a.json"))
    assert applied_path() == str(tmp_path / "a.json")


def test_apply_on_an_empty_scheduler_is_harmless() -> None:
    """发现模式下「刚起、一门课都没有」是合法稳态：施加意图不得报错、不得产出噪音。"""
    sup = FakeSup()

    assert ControlApplier().apply(sup, parse_control({"paused": ["c5"]})) == []


# ────────────────────────── ★M3「重发本轮 PPO」意图（plan/ppo-job-lifecycle-ownership §4） ──
#
# 与暂停/恢复同一套「意图文件 + 幂等回执」通道，但去重键不同：这份意图的 `at` 是**动作
# 的去重键**（同一份意图只执行一次）。控制台半边在 `dashboard/tests/redo-ppo.test.ts`。

def test_parse_redo_shape_matrix() -> None:
    """形状：`it`/`at` 可缺省（0）；非对象 / 非整数 / 非数字 ⇒ 空 + 原因（保守：宁可不动作）。"""
    assert parse_redo({"version": 1, "it": 12, "at": 1.5}) == parse_redo(
        {"it": 12, "at": 1.5}
    )
    intent = parse_redo({"it": 12, "at": 1.5})
    assert (intent.found, intent.it, intent.at, intent.error) == (True, 12, 1.5, "")

    assert parse_redo([]).error == "redo 意图根不是对象"
    assert parse_redo({"it": "x"}).error == "redo.it 不是整数"
    assert parse_redo({"at": "x"}).error == "redo.at 不是数字"
    # `it` 缺省 = 0（当前轮）；`at` 缺省 = 0（read_redo 会退回文件 mtime）。
    empty = parse_redo({})
    assert (empty.found, empty.it, empty.at) == (True, 0, 0.0)


def test_read_redo_falls_back_to_mtime_when_at_is_missing(tmp_path: Path) -> None:
    """手写 `{"it":188}` 也得能用：`at` 缺省 ⇒ 拿文件 mtime 当去重键（改一次文件 = 新意图）。"""
    assert read_redo(tmp_path).found is False
    p = redo_path(tmp_path)
    p.write_text(json.dumps({"it": 188}), encoding="utf-8")
    intent = read_redo(tmp_path)
    assert intent.found and intent.it == 188
    assert intent.at == pytest.approx(p.stat().st_mtime)
    # 坏文件：永不抛（读侧保守，训练侧只记一行）。
    p.write_text("{ not json", encoding="utf-8")
    bad = read_redo(tmp_path)
    assert bad.found is False and bad.error


def test_consume_redo_is_idempotent_per_at(tmp_path: Path) -> None:
    """★ 去重键 = `at`：同一份意图只执行一次；刷新 `at` = 再来一次。"""
    p = redo_path(tmp_path)
    p.write_text(json.dumps({"it": 0, "at": 100.0}), encoding="utf-8")

    intent, err = consume_redo(tmp_path, 7)
    assert err == "" and intent is not None and intent.at == 100.0
    assert write_redo_applied(tmp_path, it=7, at=intent.at) == ""
    assert read_redo_applied(tmp_path) == (7, 100.0)
    # 同一份意图：已消费 ⇒ 不再返回（否则 `_remote_ppo_step` 每拍都会再撤一次刚重发的活）。
    assert consume_redo(tmp_path, 7) == (None, "")
    # 刷新 at ⇒ 新意图。
    p.write_text(json.dumps({"it": 0, "at": 101.0}), encoding="utf-8")
    again, _ = consume_redo(tmp_path, 7)
    assert again is not None and again.at == 101.0


def test_consume_redo_waits_for_the_targeted_iter(tmp_path: Path) -> None:
    """正数 `it` 只对那一轮生效（不抢跑到别的轮）；`it<=0` = 当前轮，任何轮都命中。"""
    p = redo_path(tmp_path)
    p.write_text(json.dumps({"it": 9, "at": 100.0}), encoding="utf-8")
    assert consume_redo(tmp_path, 7) == (None, "")  # 还没轮到
    intent, _ = consume_redo(tmp_path, 9)
    assert intent is not None  # 对上了
    p.write_text(json.dumps({"it": -1, "at": 200.0}), encoding="utf-8")
    assert consume_redo(tmp_path, 3)[0] is not None  # it<=0 ⇒ 当前轮


def test_read_redo_applied_tolerates_garbage(tmp_path: Path) -> None:
    """回执读面：缺/坏 ⇒ `(0, 0.0)`（永不抛——它只服务于「读到没有」的展示与去重）。"""
    assert read_redo_applied(tmp_path) == (0, 0.0)
    ap = tmp_path / "redo-ppo.applied.json"
    ap.write_text(json.dumps({"it": "x", "at": None}), encoding="utf-8")
    assert read_redo_applied(tmp_path) == (0, 0.0)
    ap.write_text("[1,2]", encoding="utf-8")
    assert read_redo_applied(tmp_path) == (0, 0.0)
    ap.write_text(json.dumps({"it": 3, "at": 2.5}), encoding="utf-8")
    assert read_redo_applied(tmp_path) == (3, 2.5)
