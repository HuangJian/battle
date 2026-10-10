"""test_prefetch_adaptive.py — 预取的**会话级自停**（plan/aistudio-transfer-hardening §3.1，A1）。

现场（2026-10-09，aistudio 走 Cloudflare quick tunnel）：预取一次会话白传 4.24MB、**0 命中**，
还倒贴 0.25MB 抢占作废 + 23.9s 排队。它占着唯一那条 bulk 通道，关键下载的排队被推高。

本文件钉四件事：

1. **判据是纯函数** `prefetch_worth_it` —— 阈值不埋进控制流，判据表可逐个钉；
2. **防误杀**（本 plan 最容易做错的一处）：会话**首个** job 的预取命中率按构造 = 0
   （预取的是下一份，轮到它之前不可能被 `take()`）。所以判据既要有**最小样本**，也要有
   **白传字节门槛**——只用命中率会把完全健康的预取在前两轮关掉。
3. **白传的口径** = 「已下载但从未被 `take()` 的字节」+ 被 P1 挤走的半截，**不是**
   wire 的 `wasted`（那是重抽作废，另一码事）；
4. **自停是「可观测地关」**：打一行带原因的日志，且**连 peek 都不再发**（`peek` 本身也抢控制面）。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_round as JR
import remote.wire as wire_mod
from remote.prefetch import (
    PREFETCH_GIVEUP_MIN_SAMPLES,
    PREFETCH_GIVEUP_TAKEN_RATIO,
    PrefetchStore,
    prefetch_worth_it,
)

_PAY = b"x" * (2 * 1024 * 1024)  # 一份 ~2MB（现场 payload 量级）


def _summary(payload: bytes, jid: str) -> dict:
    import hashlib

    return {
        "job_id": jid,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_bytes": len(payload),
    }


# ───────────────── ① 判据表（纯函数）─────────────────


def test_verdict_is_keep_while_evidence_is_thin() -> None:
    """证据不足一律「继续」：样本不够 / 白传不够，都不构成「停」的理由。"""
    # 白传够了但样本不够（只下过 1 份——可能只是单份 payload 恰好偏大）
    assert prefetch_worth_it(prefetched=1, taken=0, white_bytes=8 << 20)
    # 样本够了但白传不够（每份都被吃掉了）
    assert prefetch_worth_it(prefetched=9, taken=0, white_bytes=1024)
    # 都没下过
    assert prefetch_worth_it(prefetched=0, taken=0, white_bytes=0)


def test_stops_only_when_waste_is_big_and_hits_are_poor() -> None:
    """三条同时成立才停：样本 ≥2 · 白传 ≥3MiB · 命中率 <1:3。"""
    assert not prefetch_worth_it(
        prefetched=2, taken=0, white_bytes=4 * 1024 * 1024
    ), "4MB 白传、0 命中 ⇒ 必须停（aistudio 现场就是这个形状）"
    # 命中率够 ⇒ 继续（哪怕白传超门槛：那是「有赚有赔」）
    assert prefetch_worth_it(prefetched=6, taken=2, white_bytes=8 * 1024 * 1024)
    # 恰好卡在阈值上（1/3 ⇒ 不小于 ⇒ 继续）
    assert prefetch_worth_it(
        prefetched=3,
        taken=1,
        white_bytes=4 * 1024 * 1024,
        min_taken_ratio=PREFETCH_GIVEUP_TAKEN_RATIO,
    )


def test_thresholds_are_wired_to_the_module_constants() -> None:
    """默认值 = 模块常量（改常量必须改行为，别让两处悄悄分叉）。"""
    n = PREFETCH_GIVEUP_MIN_SAMPLES
    assert prefetch_worth_it(prefetched=n, taken=0, white_bytes=4 << 20) is False
    assert prefetch_worth_it(prefetched=n - 1, taken=0, white_bytes=4 << 20) is True


# ───────────────── ② 白传口径 ─────────────────


def test_white_bytes_is_downloaded_minus_taken_plus_preempt(tmp_path: Path) -> None:
    """`white_bytes` = 已下载 − 已被 take **+** 被挤走的半截（挤走的从未入库）。"""
    s = PrefetchStore(tmp_path)
    s.store("j1", _PAY, _summary(_PAY, "j1"))
    s.store("j2", _PAY, _summary(_PAY, "j2"))
    assert s.stats()["prefetched"] == 2
    assert s.stats()["white_bytes"] == 2 * len(_PAY), "两份都没被 take ⇒ 全白传"

    s.take("j1")  # 命中一份
    assert s.stats()["white_bytes"] == len(_PAY), "被 take 的那份不再是白传"

    s.note_preempt_wasted(256 * 1024)
    assert s.stats()["preempt_wasted"] == 256 * 1024
    assert s.stats()["white_bytes"] == len(_PAY) + 256 * 1024


def test_take_of_an_unknown_jid_does_not_inflate_white_bytes(tmp_path: Path) -> None:
    """**核心防误杀**：「claim 到的 job ≠ 预取候选」是正常事件，不该被算成白传。

    旧判据用 `hits/misses` 当命中率，而 `take()` 对**每个** claim 都调 ⇒ 一份都没预取过时
    `misses` 照样涨 ⇒ 健康会话会在前两轮被判死。现在 `white_bytes` 只看真下过的字节。
    """
    s = PrefetchStore(tmp_path)
    assert s.take("never-prefetched") is None
    assert s.stats()["misses"] == 1  # 观测面照旧记
    assert s.stats()["white_bytes"] == 0, "没下过就不该有白传"
    assert prefetch_worth_it(**{k: s.stats()[k] for k in ("prefetched", "taken", "white_bytes")})


# ───────────────── ③ 自停是「可观测地关」+ 不再 peek ─────────────────


def test_self_stop_logs_a_reason_and_stops_even_issuing_peek(tmp_path: Path, monkeypatch) -> None:
    """白传超门槛 + 0 命中 ⇒ 打一行带原因的日志，并**在 peek 之前**就退出。

    `peek` 也在抢控制面（现场 `prefetch: peek 失败（TimeoutError）`），所以「省字节但不省
    排队」的自停不算自停。
    """
    s = PrefetchStore(tmp_path)
    s.store("a", _PAY, _summary(_PAY, "a"))
    s.store("b", _PAY, _summary(_PAY, "b"))
    assert s.stats()["white_bytes"] >= 4 * 1024 * 1024

    peeks: list[int] = []

    def _peek(*_a, **_k):
        peeks.append(1)
        return ([], False)

    monkeypatch.setattr(JR, "peek_jobs", _peek)
    monkeypatch.setattr(JR, "PREFETCH_ROUND_SEC", 0.01)
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)
    logs: list[str] = []
    stop = threading.Event()
    # 直接同步调用：`_prefetch_fill` 一轮就该 return（不用起线程）
    JR._prefetch_fill("http://hub", "tok", s, stop, depth=1, log=logs.append)

    assert peeks == [], f"自停后**一次 peek 都不该发**（peek 本身抢控制面）：{peeks}"
    joined = "\n".join(logs)
    assert "预取自停" in joined, f"必须可观测地关（静默不预取会被读成 bug）：{logs}"
    assert "--prefetch-depth" in joined, "关掉时要给出重开的旋钮"


def test_a_healthy_session_is_never_turned_off(tmp_path: Path, monkeypatch) -> None:
    """健康会话（预取一份、下一轮被吃掉）**永远不该**触发自停。"""
    s = PrefetchStore(tmp_path)
    peeks: list[int] = []
    logs: list[str] = []
    stop = threading.Event()
    monkeypatch.setattr(JR, "PREFETCH_ROUND_SEC", 0.01)
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)

    def _peek(*_a, **_k):
        peeks.append(1)
        stop.set()  # 只跑一轮就退出（`_prefetch_fill` 只靠 stop 结束循环）
        return ([], False)

    monkeypatch.setattr(JR, "peek_jobs", _peek)

    # 模拟「预取 → 立刻被 take」连续 5 轮：白传恒 ≈0
    for i in range(5):
        jid = f"j{i}"
        s.store(jid, _PAY, _summary(_PAY, jid))
        s.take(jid)
    assert s.stats()["white_bytes"] == 0
    assert prefetch_worth_it(prefetched=5, taken=5, white_bytes=0)

    # 首轮就该照常 peek（不因「现在 hits=0」而误杀）
    JR._prefetch_fill("http://hub", "tok", s, stop, depth=1, log=logs.append)
    assert len(peeks) >= 1, "健康会话不该被关掉（关掉 = 一轮 peek 都没有）"


# ───────────────── ④ 挤走的半截要进会话累计 ─────────────────


def test_preempt_waste_survives_the_per_round_wire_flush() -> None:
    """`_wire_flush` 会 **pop 掉整只桶**（wire.py:320）⇒ 挤走字节必须**在 flush 之前**登记。

    漏了这条，会话累计永远只有「本轮」那一笔 —— 判据永远看不见最大的浪费。
    """
    s = PrefetchStore(Path("."))
    # 真走 wire 桶（不 mock）：先记一笔挤走，再 flush —— 挤走字节必须在 pop **之前**被登记
    wire_mod._wire_note_preempt(JR.PREFETCH_WIRE_ID, 512 * 1024)
    JR._flush_prefetch_round(s, lambda _m: None)
    assert s.stats()["preempt_wasted"] == 512 * 1024
    # 再来一轮：累计（不是覆盖）
    wire_mod._wire_note_preempt(JR.PREFETCH_WIRE_ID, 512 * 1024)
    JR._flush_prefetch_round(s, lambda _m: None)
    assert s.stats()["preempt_wasted"] == 1024 * 1024, "会话累计，不是「只留本轮」"


def test_the_gate_reads_the_three_readings_the_store_exposes(monkeypatch) -> None:
    """装配点读的是 `stats()` 那三个键：**把 stats() 打空** ⇒ 装配点必然 `KeyError` 冒出来。

    （不去源码里找字符串：那是措辞判据，改个变量名就红而行为没变。这里判的是**行为**。）
    """
    s = PrefetchStore(Path("."))
    monkeypatch.setattr(PrefetchStore, "stats", lambda _self: {})
    stop = threading.Event()
    with pytest.raises(KeyError) as ei:
        JR._prefetch_fill("http://hub", "tok", s, stop, depth=1, log=lambda _m: None)
    assert ei.value.args[0] == "prefetched", f"装配点不再读 stats()['prefetched']：{ei.value}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
