"""test_yield_adaptive.py — 让路的**会话级自适应关闭**（plan/aistudio-transfer-hardening §3.2，A2）。

现场（2026-10-09，aistudio 走 Cloudflare quick tunnel）：控制面 `p0_p50=1.1s` 而
`p0_p95=30.3s`，一轮让路合计 7.0s = wire 传输时间的 38%。而 P0 **从不排队**（独立 socket，
见 `bulk_sched.slot()` 里的 `_control_waiting` 注释）⇒ 那个 30s 的尾部**不是被 bulk 挤的**，
让路买不回任何东西，只把传输拖长。

本文件钉四件事：

1. **判据是纯函数** `yield_worth_it` —— 阈值不埋进控制流，判据表可逐个钉；
2. **防误杀**（本 plan 最容易做错的一处）：分位数是**小样本**噪声敏感的量，会话早期 1–2 个
   坏样本就能把 p95 拉到 30s ⇒ 必须有 `min_samples`，且「还没测到」（分位为 0）一律不算证据；
3. **降级是「可观测地关」**：打一行带原因的日志 + 装配点真的把让路降到 never 语义
   （`after=∞, total<=0`），静默关闭会被后人读成 bug；
4. **装配是全局动作**（`_BULK` 是进程单例）⇒ 退出必须还原，否则同进程后续使用者继承。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_round as round_mod
import remote.worker as W
from remote.bulk_sched import (
    YIELD_GIVEUP_MIN_SAMPLES,
    YIELD_GIVEUP_P95_MS,
    yield_worth_it,
)

# ───────────────── ① 判据表（纯函数）─────────────────


def test_keeps_yielding_while_the_evidence_is_thin() -> None:
    """证据不足一律「继续让」：样本不够 / 还没测到分位数，都不构成「关」的理由。"""
    # 样本不够（会话早期：1 个坏样本就把 p95 拉到 30s）
    assert yield_worth_it(p50_ms=80.0, p95_ms=30_000.0, n=1)
    assert yield_worth_it(p50_ms=80.0, p95_ms=30_000.0, n=YIELD_GIVEUP_MIN_SAMPLES - 1)
    # 还没测到（分位为 0 = 一次控制面都没跑完）
    assert yield_worth_it(p50_ms=0.0, p95_ms=0.0, n=0)
    assert yield_worth_it(p50_ms=0.0, p95_ms=0.0, n=100), "分位为 0 是「没数据」不是「很快」"


def test_stops_only_when_the_tail_is_far_above_the_threshold() -> None:
    """样本够 + 尾部远超阈值 ⇒ 关；尾部在阈值内 ⇒ 继续让。"""
    assert not yield_worth_it(
        p50_ms=1_100.0, p95_ms=30_300.0, n=64
    ), "aistudio 现场就是这个形状：p50 1.1s / p95 30.3s ⇒ 让路买不回控制面速度"
    assert yield_worth_it(p50_ms=1_100.0, p95_ms=9_000.0, n=64)
    # 恰好卡在阈值上（≤ ⇒ 继续）
    assert yield_worth_it(p50_ms=100.0, p95_ms=YIELD_GIVEUP_P95_MS, n=YIELD_GIVEUP_MIN_SAMPLES)


def test_thresholds_are_wired_to_the_module_constants() -> None:
    """默认值 = 模块常量（改常量必须改行为，别让两处悄悄分叉）。"""
    n = YIELD_GIVEUP_MIN_SAMPLES
    assert yield_worth_it(p50_ms=1.0, p95_ms=YIELD_GIVEUP_P95_MS + 1.0, n=n) is False
    assert yield_worth_it(p50_ms=1.0, p95_ms=YIELD_GIVEUP_P95_MS + 1.0, n=n - 1) is True


# ───────────────── ② 装配点：可观测地关 + 退出还原 ─────────────────


def _seed_p0_ms(monkeypatch, ms_list: list[float]) -> None:
    """往会话级控制面分位表里灌样本（**假时钟**，不真等几十秒）。

    走的是 `BulkScheduler.control()` 这个**公开**上下文（与生产同一条记账路径），
    只把时钟换成假的——否则「p95=30s」要真等 30s。
    """
    now = {"t": 10_000.0}
    monkeypatch.setattr(W._BULK, "_clock", lambda: now["t"])
    for ms in ms_list:
        with W._BULK.control(label="probe"):
            now["t"] += ms / 1000.0


def _run_two_rounds(
    monkeypatch, tmp_path: Path, *, bulk_yield: str = "auto"
) -> tuple[list[str], list[dict], list[tuple[float, float | None]]]:
    """跑两轮 `worker_loop`（第二轮 `stop=True` 整条退出），返回 `(日志, configure_yield 调用)`。"""
    seen: list[str] = []

    def _fake_round(base_url, token, job, **_kw):
        seen.append(job["job_id"])
        if len(seen) == 1:
            return round_mod.RoundOutcome(jid=job["job_id"], ok=True, uploaded=True, stop=False)
        return round_mod.RoundOutcome(jid=job["job_id"], ok=False, uploaded=False, stop=True)

    n = {"i": 0}

    def _acquire(*_a, **_k):
        n["i"] += 1
        return {"job_id": f"j{n['i']}", "manifest": {}, "status": "ok", "lease_token": ""}

    calls: list[dict] = []
    prevs: list[tuple[float, float | None]] = []
    _orig = W._BULK.configure_yield

    def _spy(**kw):
        calls.append(dict(kw))
        prev = _orig(**kw)
        prevs.append(prev)
        return prev

    monkeypatch.setattr(W._BULK, "configure_yield", _spy)
    monkeypatch.setattr(W, "run_one_round", _fake_round)
    monkeypatch.setattr(W, "acquire_job", _acquire)
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *_a, **_k: None)
    logs: list[str] = []
    W.worker_loop(
        "http://hub", "tok", work_dir=tmp_path, poll_sec=0.01, bulk_yield=bulk_yield, log=logs.append
    )
    return logs, calls, prevs


def test_slow_control_plane_lowers_yield_to_never_and_says_why(monkeypatch, tmp_path: Path) -> None:
    """★ 功能性：p95 远超阈值 ⇒ 就地降到 never 语义 + 一行原因 + 退出还原。"""
    W._BULK.reset()
    _seed_p0_ms(monkeypatch, [1_100.0] * 19 + [30_300.0])  # n=20 ⇒ p95=30300ms
    logs, calls, prevs = _run_two_rounds(monkeypatch, tmp_path)

    lowered = [c for c in calls if c.get("after_sec") == float("inf")]
    assert lowered, f"没有降到 never 语义（after=∞）：{calls}"
    assert lowered[0]["total_budget_sec"] == 0, "总预算也要归零（否则「永不触发」靠 after=∞ 单条腿撑着）"
    joined = "\n".join(logs)
    assert "让路自适应关闭" in joined, f"必须可观测地关（静默不让路会被读成 bug）：{logs}"
    assert "--bulk-yield always" in joined, "关掉时要给出回退旋钮"

    # 装配是全局动作（`_BULK` 进程单例）⇒ 退出必须还原成**进来之前**的策略
    assert calls[-1] == {"after_sec": prevs[0][0], "total_budget_sec": prevs[0][1]}, (
        f"退出没还原让路策略（同进程后续使用者会继承 never）：{calls[-1]}"
    )


def test_a_fast_control_plane_never_triggers_the_downgrade(monkeypatch, tmp_path: Path) -> None:
    """控制面健康（p95 在阈值内）⇒ 一次都不降（降级是**一次性**的，误触发就没法回）。"""
    W._BULK.reset()
    _seed_p0_ms(monkeypatch, [80.0] * 20)
    logs, calls, _prevs = _run_two_rounds(monkeypatch, tmp_path)
    assert not any(c.get("after_sec") == float("inf") for c in calls), f"健康会话被误关：{calls}"
    assert not any("让路自适应关闭" in m for m in logs)


def test_bulk_yield_never_skips_the_adaptive_check_entirely(monkeypatch, tmp_path: Path) -> None:
    """`--bulk-yield never` 起步就不需要自适应（再关一次是无信息增量的日志噪声）。"""
    W._BULK.reset()
    _seed_p0_ms(monkeypatch, [1_100.0] * 19 + [30_300.0])
    logs, calls, _prevs = _run_two_rounds(monkeypatch, tmp_path, bulk_yield="never")
    # 起步装配本身就是 `after=inf`（`_bulk_yield_params("never")`），区分「装配」与「自适应降级」
    # 看的是**总预算**：装配传 `None`，降级传 0（见上面那条用例）。
    assert not any(c.get("total_budget_sec") == 0 for c in calls), "已经是 never 了不该再降一次"
    assert len(logs) >= 1, "起步就该有一行策略播报（让路档位必须可见）"


def test_the_check_reads_the_quantiles_stats_exposes(monkeypatch, tmp_path: Path) -> None:
    """装配点读的是 `stats()` 的**现成读数**（换键名 ⇒ 装配点当场炸，不是静默失准）。

    用「把 `stats()` 打空」而不是去源码里找字符串：装配点读不到 `p0_rt_ms_p95` 就必然
    `KeyError` 冒到 `worker_loop` 外面——这是**行为**判据，不是措辞判据。
    """
    monkeypatch.setattr(W._BULK, "stats", lambda: {})
    with pytest.raises(KeyError) as ei:
        _run_two_rounds(monkeypatch, tmp_path)
    # 契约是「**这三个键**必须真读到」（键名一改就静默失准）——**先炸哪一个**是取值顺序，
    # 不是契约（实测：按 p50 → p95 → count 取，先炸的就是 p50）。
    assert ei.value.args[0] in {"p0_rt_ms_p50", "p0_rt_ms_p95", "p0_count"}, (
        f"装配点读的不是 stats() 的分位读数：{ei.value}"
    )
