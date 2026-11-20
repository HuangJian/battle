"""test_stream_alpha.py — P1 的 α 单腿（`volume.sched="alpha"`）。

只测纯逻辑：不跑 bun、不读钟（`started_at`/`now` 由用例传入）。钉住：
  * 冷启动在 2n 次后若还有局在飞，返回 `[]`（软停），不许标 no_data 提前终局；
  * 判据无 est；quota 模式逐字不变；
  * 近分交错；cap 在进入 α 时冻结；T_fill 只在全关首达配额时有定义。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from tests.helpers.stream_spec import STAGES, build_spec, stage_of, volume_block
from worker.stream_collect import StreamCollector


def _collector(*, ests: dict[int, int] | None = None, sched: str = "alpha") -> StreamCollector:
    vol = volume_block(ests=ests)
    vol["sched"] = sched
    col = StreamCollector.from_spec(
        build_spec(vol=vol), started_at=100.0,
        log=lambda _m: None,
    )
    assert col is not None
    return col


def _settle(col: StreamCollector, argv: list[str], samples: int, t: float) -> None:
    col.observe(argv, samples, wall_sec=1.0, now=t)


def test_default_sched_is_alpha_when_the_key_is_absent() -> None:
    """生产缺省：volume 块没有 `sched` 时按 alpha，不是旧 quota。"""
    vol = volume_block()
    vol.pop("sched", None)
    col = StreamCollector.from_spec(
        build_spec(vol=vol), started_at=100.0, log=lambda _m: None
    )
    assert col is not None and col.sched == "alpha"


def test_cli_default_sched_is_alpha_and_quota_is_explicit() -> None:
    from worker.cli import build_argparser

    ap = build_argparser("per-tick", {})
    assert ap.parse_args([]).volume_sched == "alpha"
    assert ap.parse_args(["--volume-sched", "quota"]).volume_sched == "quota"


def test_cold_start_soft_waits_instead_of_ending_after_two_per_stage() -> None:
    col = _collector()
    first: list[list[str]] = []
    for _ in range(2 * len(STAGES)):
        argv = col.next_game()
        assert argv, "冷启动 2n 之内必须有活"
        first.append(argv)
    assert col.next_game() == [], "2n 局都还在飞 ⇒ 软停（[]），不是终局（None）"
    assert not col.alpha_started and not col.no_data

    for i, argv in enumerate(first):
        _settle(col, argv, 100, 101.0 + i)
    # 所有关都有结算 ⇒ 进 α；此时不该再返回软停
    assert col.next_game(), "冷启动结算后应继续派 α 局"
    assert col.alpha_started


def test_alpha_never_reads_est() -> None:
    a = _collector(ests={s: 0 for s in STAGES})
    b = _collector(ests={s: 99999 for s in STAGES})

    def drive(col: StreamCollector) -> list[int]:
        order: list[int] = []
        for i in range(6):
            argv = col.next_game()
            assert argv
            _settle(col, argv, 100, 101.0 + i)
        for i in range(12):
            argv = col.next_game()
            if argv is None:
                break
            assert argv
            order.append(stage_of(argv))
            _settle(col, argv, 100, 200.0 + i)
        return order

    assert drive(a) == drive(b), "est 设 0 / 99999 不得改变 α 的派发序列"


def test_alpha_counts_in_flight_games_on_a_wide_pool() -> None:
    """★ 宽池不许叠派：222 槽的自主云机现场（g5-pilot it74：计划 4–5 seed/关 ⇒ ~140 局，
    实派 435 局、过采 250051）。

    病在 `_next_alpha` 只看「collected < quota」——槽位一空就问下一局，**在飞的局不算账**，
    于是 222 个槽位在配额早就够了的情况下还在同一批关上加派，直到最后一局结算才停；
    多出来的那批（≈ 一整个池宽）全是白烧的算力/墙钟。quota 档有这条闸（`got + 在飞 × est
    ≥ quota`），α 档当初以「不读 est」为名一起丢了它 —— 但「在飞也算账」不需要 est，
    用**实测** samples/局就够（`samples_s`，一局结算就有）。

    用例形状 = 生产形状：三个槽位以外的空位一次问到底，结算还是**批量**回来的。
    """
    col = _collector()  # 3 关 × quota 300，每局 100 样本 ⇒ 理论 3 局/关 = 9 局
    slots = 12  # 机器比缺口宽（现场 222 vs 需求 ~140）
    inflight: list[list[str]] = []
    while True:
        while len(inflight) < slots:
            argv = col.next_game()
            if not argv:
                break
            inflight.append(argv)
        if not inflight:
            break
        for argv in inflight:  # 一批一起结算（宽池的形态：同一拍回来好几局）
            _settle(col, argv, 100, 101.0 + col._dispatched)
        inflight = []
    assert col._dispatched <= 10, (
        f"宽池叠派：派了 {col._dispatched} 局，配额只需要 9 局"
        f"（在飞 {col.games}/已结算 {col.settled}）"
    )
    assert col.volume()["shortfall_by_stage"] == {}, "省算力不许省成短采"


def test_alpha_cap_is_frozen_on_entry() -> None:
    col = _collector()
    cold = [col.next_game() for _ in range(2 * len(STAGES))]
    for i, argv in enumerate(cold):
        assert argv
        _settle(col, argv, 100, 101.0 + i)
    # 冷启动之后的下一局也要**结算**（宽池闸把「在飞」算账 ⇒ 派出去就得跑完，不能悬着）
    first = col.next_game()
    assert first
    _settle(col, first, 100, 101.5)
    caps = {s: col._alpha_cap(s) for s in STAGES}
    for i in range(3):
        argv = col.next_game()
        if argv is None:
            break
        assert argv
        _settle(col, argv, 100, 200.0 + i)
    frozen = {s: col._alpha_cap(s) for s in STAGES}
    assert frozen == caps, "cap 必须在进入 α 时冻结"


def test_t_fill_none_until_every_stage_reaches_quota() -> None:
    col = _collector()
    while True:
        argv = col.next_game()
        if argv is None:
            break
        assert argv
        _settle(col, argv, 100, 101.0 + col._dispatched)
        if len(col.first_quota_at) == 2:
            assert col.volume()["t_fill_sec"] is None
    vol = col.volume()
    assert len(col.first_quota_at) == len(STAGES)
    assert vol["t_fill_sec"] is not None and vol["t_fill_sec"] > 0
    assert vol["t_drain_sec"] is not None and vol["t_drain_sec"] >= vol["t_fill_sec"]


def test_no_data_stage_is_reported_and_never_infinite() -> None:
    col = _collector()
    cold = [col.next_game() for _ in range(2 * len(STAGES))]
    for i, argv in enumerate(cold):
        assert argv
        _settle(col, argv, 0 if stage_of(argv) == 2001 else 100, 101.0 + i)
    for i in range(100):
        argv = col.next_game()
        if argv is None:
            break
        assert argv
        _settle(col, argv, 0 if stage_of(argv) == 2001 else 100, 200.0 + i)
    assert col.next_game() is None
    vol = col.volume()
    assert 2001 in vol["no_data_stages"]
    assert 2001 in vol["shortfall_by_stage"]
    assert vol["t_fill_sec"] is None


def test_invalid_sched_is_loud() -> None:
    vol = volume_block()
    vol["sched"] = "alhpa"
    with pytest.raises(ProtocolError):
        StreamCollector.from_spec(build_spec(vol=vol))
