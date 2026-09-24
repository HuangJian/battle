"""tests/test_log_bundle.py —— 日志节食的攒行原语（`log_bundle.py`）。

钉的是**契约**（不是实现）：同 key 就地替换（装载进度只留最后那个值）、`final_only` 只在
阶段完成时出现、`beat` 只按墙钟节流且**不清空**、空内容不打空行、打完就不再心跳。

时钟是**注入**的（`clock=`）——心跳节流因此是纯函数式的，用例里一次 sleep 都没有。
"""

from __future__ import annotations

import pytest

from log_bundle import LogBundle


@pytest.fixture()
def rig() -> tuple[LogBundle, list[str], dict[str, float]]:
    """bundle + 输出行 + 可拨的钟。"""
    lines: list[str] = []
    clock = {"t": 1000.0}
    return LogBundle(lines.append, clock=lambda: clock["t"]), lines, clock


def test_same_key_replaces_in_place(rig: tuple[LogBundle, list[str], dict[str, float]]) -> None:
    """同一件事的后续读数覆盖前一个，**位置不变**（装载 128 → 256 → 336 只剩 336）。"""
    b, lines, _ = rig
    b.add("shards", 336)
    b.add("装载", "128/336 0s")
    b.add("装载", "256/336 1s")
    b.add("装载", "336/336 1s")
    b.add("IO+GAE", "238 eps 1s")
    assert b.emit("job j1: 准备完成 4.2s") is True
    assert lines == ["job j1: 准备完成 4.2s｜shards=336｜装载=336/336 1s｜IO+GAE=238 eps 1s"], lines


def test_beat_throttles_by_wall_clock_and_keeps_parts(
    rig: tuple[LogBundle, list[str], dict[str, float]],
) -> None:
    """心跳：`every` 秒一到才打一次；打完**不清空** —— 完成那一行仍带启动事实。"""
    b, lines, clock = rig
    b.add("workers", 220)
    b.add("进度", "1/336 1s")
    assert b.beat("kind=iter rollout") is False, "刚建就心跳 = 打重复行"
    clock["t"] += 59.0
    assert b.beat("kind=iter rollout") is False
    clock["t"] += 2.0
    b.add("进度", "120/336 61s")
    assert b.beat("kind=iter rollout") is True
    assert lines[-1] == "kind=iter rollout｜workers=220｜进度=120/336 61s", lines
    assert b.pending() is True, "心跳不许清空"
    # 完成那一行仍然带着启动事实 + 最终进度
    assert b.emit("kind=iter rollout done") is True
    assert lines[-1] == "kind=iter rollout done｜workers=220｜进度=120/336 61s"


def test_final_only_is_hidden_from_beats(
    rig: tuple[LogBundle, list[str], dict[str, float]],
) -> None:
    """`final_only`（如单局耗时分布）在跑完之前不存在 ⇒ 心跳不带、完成才带。"""
    b, lines, clock = rig
    b.add("进度", "10/336 5s")
    b.add("单局耗时", "p50=1.1s max=2.7s", final_only=True)
    clock["t"] += 61.0
    assert b.beat("rollout") is True
    assert lines[-1] == "rollout｜进度=10/336 5s"
    assert b.emit("rollout done") is True
    assert lines[-1] == "rollout done｜进度=10/336 5s｜单局耗时=p50=1.1s max=2.7s"


def test_only_final_parts_pending_means_no_beat(
    rig: tuple[LogBundle, list[str], dict[str, float]],
) -> None:
    """只有 `final_only` 内容时心跳**不打**（没什么可说的，别打空壳行）。"""
    b, lines, clock = rig
    b.add("配额", "12288/关", final_only=True)
    clock["t"] += 61.0
    assert b.beat("x") is False
    assert lines == []


def test_empty_emit_prints_nothing(rig: tuple[LogBundle, list[str], dict[str, float]]) -> None:
    """没有内容时**不打空行**（阶段什么都没发生时不该留下痕迹）。"""
    b, lines, _ = rig
    assert b.pending() is False
    assert b.emit("job j1: 准备完成 1s") is False
    assert lines == []


def test_after_emit_no_more_beats(rig: tuple[LogBundle, list[str], dict[str, float]]) -> None:
    """完成之后不再心跳（一个阶段只有一行收尾 + 期间若干心跳）。"""
    b, lines, clock = rig
    b.add("进度", "336/336 3s")
    assert b.emit("done") is True
    clock["t"] += 600.0
    assert b.beat("done") is False
    assert len(lines) == 1


def test_notes_are_deduped_and_keep_order(
    rig: tuple[LogBundle, list[str], dict[str, float]],
) -> None:
    """无键自由文本（看门狗口径这类）：同内容只留一份、保序。"""
    b, lines, _ = rig
    b.note("看门狗 软>5s 硬顶5s（plan 未指定用节点兜底）")
    b.note("看门狗 软>5s 硬顶5s（plan 未指定用节点兜底）")
    b.add("bun", "/root/.bun/bin/bun (1.4.2)")
    b.emit("kind=iter rollout")
    assert lines == [
        "kind=iter rollout｜bun=/root/.bun/bin/bun (1.4.2)"
        "｜看门狗 软>5s 硬顶5s（plan 未指定用节点兜底）"
    ], lines


def test_beat_uses_injected_now_argument(rig: tuple[LogBundle, list[str], dict[str, float]]) -> None:
    """`now=` 显式给时刻时不用时钟（调用方手里已经有一个时间戳时少取一次）。"""
    b, lines, clock = rig
    b.add("进度", "5/10 5s")
    assert b.beat("x", now=clock["t"] + 61.0) is True
    assert lines[-1] == "x｜进度=5/10 5s"
