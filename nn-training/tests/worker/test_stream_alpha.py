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
    assert not col._alpha_started and not col.no_data

    for i, argv in enumerate(first):
        _settle(col, argv, 100, 101.0 + i)
    # 所有关都有结算 ⇒ 进 α；此时不该再返回软停
    assert col.next_game(), "冷启动结算后应继续派 α 局"
    assert col._alpha_started


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


def test_alpha_cap_is_frozen_on_entry() -> None:
    col = _collector()
    cold = [col.next_game() for _ in range(2 * len(STAGES))]
    for i, argv in enumerate(cold):
        assert argv
        _settle(col, argv, 100, 101.0 + i)
    assert col.next_game()
    caps = {s: col._alpha_cap(s) for s in STAGES}
    for i in range(3):
        argv = col.next_game()
        if argv is None:
            break
        assert argv
        _settle(col, argv, 100, 200.0 + i)
    assert {s: col._alpha_cap(s) for s in STAGES} == caps, "cap 必须在进入 α 时冻结"


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
