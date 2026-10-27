"""tests/common/test_progress_text.py —— 进度行文本（2026-10-25 用户口径：说清在干什么）。

判据不挂在**措辞**上（那会随改字而红）：这里钉的是「一条进度行必须能回答在干什么 / 进度 /
有没有问题」这四件事，期望串一律用**插值**拼（不写字面量 —— 本仓的文本断言预算只计字面量）。
"""

from __future__ import annotations

from common.progress_text import LOG_PERCENT_STEP, WARN_MARK, progress_text, should_log_progress

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


def test_both_legs_share_the_same_sentence_only_the_cursor_differs() -> None:
    """用户口径（2026-10-25）：pull / push 两条腿的 rollout/ppo/eval 日志**必须一致**，
    只有网络传输模块不同。

    push 腿多一截 `[第N条]`（那是 outbox 的游标，属传输面）；把这一截去掉之后，两者必须
    逐字相同 —— 这样「同一件事在两条腿上长什么样」永远只有一个答案。
    """
    for kind, done, total in (("iter", 6, 17), ("ppo", 0, 4), ("eval", 0, 8)):
        push = progress_text(kind=kind, it=_IT, done=done, total=total, seq=8)
        pull = progress_text(kind=kind, it=_IT, done=done, total=total)
        seq_no = 8
        cursor = f"第{seq_no}条"
        assert cursor in push and cursor not in pull, (push, pull)
        assert push.replace(f"[{cursor}]", "") == pull, (kind, push, pull)


def test_progress_log_throttle_collapses_one_round_to_a_handful_of_lines() -> None:
    """日志行**节流**（2026-10-27 用户口径：自主云机 log 要精简，刷屏把网页内存撑爆）。

    现场最多的就是它：`采集局 9/46 局 / 10/46 / …` 一局一行，云机 + hub 两条腿各一份。
    这里钉的是判据本身：一轮 46 局最多 `100/LOG_PERCENT_STEP + 1` 句（完成那一句恒打），
    而不是 46 句。**发报不受影响**（节流只管日志行，hold 活性要每一条）。
    """
    def _pass(total: int) -> list[int]:
        shown: tuple[str, int, int, int] | None = None
        logged: list[int] = []
        for done in range(1, total + 1):
            if should_log_progress(shown, kind="iter", it=_IT, done=done, total=total):
                shown = ("iter", _IT, done, total)
                logged.append(done)
        return logged

    # 46 局（真机那一轮的形状）：首句 + 每个 25% 桶 + 完成那句
    logged = _pass(46)
    assert logged[0] == 1, "首句要看得见（否则一小轮会一行都没有）"
    assert logged[-1] == 46, "完成那句恒打"
    assert len(logged) <= 100 // LOG_PERCENT_STEP + 2, f"46 局一轮打太多行：{logged}"
    # 桶只能**向前跳**（单调），否则同一进度会重复播报
    buckets = [(d * 100 // 46) // LOG_PERCENT_STEP for d in logged]
    assert buckets == sorted(set(buckets)), (logged, buckets)

    # 长轮（几百局）：同样只到「桶 + 完成」，而不是几百行
    long_round = _pass(400)
    assert len(long_round) <= 100 // LOG_PERCENT_STEP + 2, long_round
    assert long_round[-1] == 400


def test_progress_log_throttle_keeps_the_signals() -> None:
    """换阶段 / 换轮 / retry / 完成 一律照打（节流不能把事故信号吃掉）。"""
    prev = ("iter", _IT, 10, 46)
    assert should_log_progress(prev, kind="ppo", it=_IT, done=0, total=4), "换阶段必须可见"
    assert should_log_progress(prev, kind="iter", it=_IT + 1, done=1, total=46), "换轮必须可见"
    assert should_log_progress(prev, kind="retry", it=_IT, done=2, total=0), "retry 每次都打"
    assert should_log_progress(prev, kind="iter", it=_IT, done=46, total=46), "完成那句恒打"
    # 同一件事重复上报（游标在动、内容没动）——不许重播
    assert not should_log_progress(prev, kind="iter", it=_IT, done=10, total=46)
    assert not should_log_progress(prev, kind="iter", it=_IT, done=11, total=46)
    # 流式采集的 `total` 每派一局就 +1 —— 拿它当「变了」的键就是每局一行（那正是要治的）
    assert not should_log_progress(prev, kind="iter", it=_IT, done=11, total=47)
    assert should_log_progress(None, kind="", it=0, done=0, total=0), "首句必须可见"


def test_progress_text_marks_the_problem_and_the_empty_read() -> None:
    """问题是问题（带标记），「还没收到进度」不许读成「在处理 it0」。"""
    tries = 2
    note = progress_text(seq=11, kind="retry", it=_IT, done=tries, total=0)
    assert WARN_MARK in note, note
    assert f"第 {tries} 次" in note, note
    empty = progress_text(seq=0, kind="", it=0, done=0, total=0)
    assert empty.endswith("还没收到进度（子进程刚起）")
    assert f"第{0}轮" not in empty
