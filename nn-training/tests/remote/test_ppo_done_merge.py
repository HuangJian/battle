"""PPO 收尾行合并进 wire 总账行（2026-10-28）。

纪律来源：`remote/train_core._ppo_done_log` 不再单列 `job … PPO done in …｜steps=…｜chunks=…｜kl=…`，
而是把 steps/chunks/kl（+ engine 折叠读数）写入 `_WIRE[jid]["ppo_line"]`，由 `remote/wire._wire_flush`
拼到传输总账行尾。判据：**训练侧不得再为 PPO 收尾单独打一行**，否则 log 节食失败（用户 2026-10-28 报障：
epoch 行 + PPO done 行 + wire 行三行叠着刷屏）。

判据形态：全部走**结构化字段**（拆 `k=v` 段 / 前后缀契约 / 字段名集合），一条 `assert "…" in 文本`
都不撒 —— `tests/test_source_text_assert_budget.py` 的形态口径对本文件零命中（2026-10-28）。

hermetic：不碰真网络、不跑真 PPO；用假 chunks + LogBundle 直接驱动 `_ppo_done_log`。
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from common.log_bundle import SEP, LogBundle
from remote import train_core
from remote import wire as wire_mod

#: 总账行里必须出现的**字段名**（值随耗时变，所以只钉在场，不钉数值）。
_WIRE_FIELDS = frozenset(
    {"payload", "result", "合计", "in", "out", "ppo", "failed", "other", "wall", "overlap"}
)


def _setup_wire(jid: str) -> None:
    wire_mod._WIRE.pop(jid, None)
    # payload + result 两段，模拟 job 传输已完成（_wire_flush 需要 segs 才打得出总账行）。
    wire_mod._wire_add(jid, "payload", 1024 * 1024, 1.0)
    wire_mod._wire_add(jid, "result", 200 * 1024, 2.0)


def _fake_chunks(n_per: int, n: int) -> list[dict]:
    return [{"obs": np.zeros((n_per, 4), dtype=np.float32)} for _ in range(n)]


def _seg_fields(line: str) -> dict[str, str]:
    """把 `k=v｜k=v` 拆成字段表（`LogBundle.text()` 的返回形态）。"""
    out: dict[str, str] = {}
    for part in line.split(SEP):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key] = value
    return out


def _kv_tokens(text: str) -> dict[str, str]:
    """把空白分隔的 `k=v` token 拆成字段表（wire 总账行是空格分隔的）。"""
    out: dict[str, str] = {}
    for tok in text.split():
        if "=" in tok:
            key, value = tok.split("=", 1)
            out[key] = value
    return out


def test_ppo_done_no_longer_emits_a_separate_line() -> None:
    """_ppo_done_log 不再独立 emit；读数只进 `_WIRE[jid]["ppo_line"]`（由 wire flush 拼尾）。"""
    jid = "c" * 16
    _setup_wire(jid)
    train_b = LogBundle(None)
    chunks = _fake_chunks(10, 3)  # 30 步
    try:
        ppo_sec = train_core._ppo_done_log(
            jid, time.time() - 2.0, train_b, chunks, 30, {"kl": 0.0051}
        )
        assert ppo_sec == 2.0, ppo_sec
        # 没有单独 emit ⇒ train_b 仍攒着（pending 为真）。
        assert train_b.pending() is True
        # 读数进了 wire 账的 ppo_line 槽（读的是拆开的字段，不是措辞）。
        line = wire_mod._WIRE[jid]["ppo_line"]
        assert line is not None, "PPO 收尾读数必须写入 _WIRE[jid]['ppo_line']"
        fields = _seg_fields(line)
        assert fields["steps"] == "30/30（训练/池子）", fields
        assert fields["chunks"] == "3", fields
        assert float(fields["kl"]) == pytest.approx(0.0051), fields
    finally:
        wire_mod._WIRE.pop(jid, None)


def test_ppo_line_appended_to_wire_summary_tail() -> None:
    """_wire_flush 的总账行末尾必须带上 PPO 读数（不破坏原有字段）。"""
    jid = "d" * 16
    _setup_wire(jid)
    train_b = LogBundle(None)
    chunks = _fake_chunks(10, 3)
    try:
        train_core._ppo_done_log(jid, time.time() - 2.0, train_b, chunks, 30, {"kl": 0.0051})
        ppo_line = wire_mod._WIRE[jid]["ppo_line"]  # flush 会 pop 掉整个账，先留副本
        assert ppo_line
        lines: list[str] = []
        wire_mod._wire_flush(jid, lines.append)
        # ① 只打一行：PPO 读数不许另起一行。
        assert len(lines) == 1, lines
        body = lines[0]
        # ② PPO 读数逐字拼在**行尾**，且只出现一次。
        assert body.endswith(f" {ppo_line}"), body
        assert body.count(ppo_line) == 1, body
        # ③ 去掉尾巴后仍是完整的总账行：job 头 + 各段 + 合计 + phases 全在。
        head = body[: -len(ppo_line) - 1]
        assert head.startswith(f"job {jid}: wire payload="), head
        got = _kv_tokens(head)
        assert set(got) >= _WIRE_FIELDS, got
    finally:
        wire_mod._WIRE.pop(jid, None)
