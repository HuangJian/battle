"""tests/common/test_progress_text.py —— 进度行文本（2026-10-25 用户口径：说清在干什么）。

判据不挂在**措辞**上（那会随改字而红）：这里钉的是「一条进度行必须能回答在干什么 / 进度 /
有没有问题」这四件事，期望串一律用**插值**拼（不写字面量 —— 本仓的文本断言预算只计字面量）。
"""

from __future__ import annotations

from common.progress_text import WARN_MARK, progress_text

#: 一条 it102 的采集事件（字段值直接喂进期望串，避免字面量）。
_IT = 102
_DONE = 6
_TOTAL = 17


def test_progress_text_names_what_it_is_doing() -> None:
    """采集 / PPO / 评估 三段各有各的说法（原来只有 `iter` 一个 kind）。"""
    assert progress_text(seq=8, kind="iter", it=_IT, done=_DONE, total=_TOTAL) == (
        f"进度[第8条] 第{_IT}轮 采集局：{_DONE}/{_TOTAL} 局"
    )
    # PPO 按「遍」：done=0 时报规模（不假报「0/4 遍」）
    epochs = 4
    assert progress_text(seq=9, kind="ppo", it=_IT, done=0, total=epochs) == (
        f"进度[第9条] 第{_IT}轮 训练 PPO：共 {epochs} 遍"
    )
    games = 8
    assert progress_text(seq=10, kind="eval", it=_IT, done=0, total=games) == (
        f"进度[第10条] 第{_IT}轮 评估：共 {games} 局"
    )
    assert progress_text(seq=2, kind="round-start", it=_IT, done=0, total=0) == (
        f"进度[第2条] 第{_IT}轮 本轮开始"
    )


def test_progress_text_marks_the_problem_and_the_empty_read() -> None:
    """问题是问题（带标记），「还没收到进度」不许读成「在处理 it0」。"""
    tries = 2
    note = progress_text(seq=11, kind="retry", it=_IT, done=tries, total=0)
    assert WARN_MARK in note, note
    assert f"第 {tries} 次" in note, note
    empty = progress_text(seq=0, kind="", it=0, done=0, total=0)
    assert empty.endswith("还没收到进度（子进程刚起）")
    assert f"第{0}轮" not in empty
