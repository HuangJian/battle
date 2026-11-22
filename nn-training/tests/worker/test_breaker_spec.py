"""`BreakerSpec` 的形状守卫（`python.refactor.md` §P0-3，批 D2，2026-11-22）。

批 D2 第二刀：`worker/breaker.breaker_update`(2 位置 + 10 关键字 → 1 个 `BreakerSpec`)。
调用面 19 处（`loop_guards_trip` 1 · `train_ledger` 1 · 熔断用例 17）。

坏点：**五条相邻阈值**分属两族 —— KL 族 `kl_break` / `kl_consec`，ENT 族 `ent_break` /
`ent_consec` / `ent_max_winrate`。互换不会抛：KL 规则拿熵阈值判、ENT 规则拿 KL 阈值判，结果是
「该停的没停」或「健康轮被误熔断」，而两者在日志里都只是同一句「熔断」（2026-09-06 p4-onset
it8 误熔断 = DECISIONS §339 的形态）。`kl_streak` / `ent_streak` 同理：互换后两条规则的连击
互相垫账，两轮之内就把一条规则推到 `consec`。

除形状外还钉住三件事：两族阈值**互不挪用**、`enabled=False` 整条短路、`ent_peak=None` 退回
绝对电平（旧行为）。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import cast

import pytest

from worker.breaker import (
    ENT_BREAK,
    ENT_BREAK_CONSEC,
    ENT_BREAK_MAX_WINRATE,
    KL_BREAK,
    KL_BREAK_CONSEC,
    BreakerSpec,
    breaker_update,
)

MISSING = dataclasses.MISSING


def _table(cls: type) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for f in dataclasses.fields(cls):
        if f.default is not MISSING:
            out.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            out.append((f.name, f.default_factory()))
        else:
            out.append((f.name, MISSING))
    return out


#: 旧 `breaker_update` 的 12 个形参（前 2 个位置）。
FIELDS: list[tuple[str, object]] = [
    ("kl_streak", MISSING),
    ("ent_streak", MISSING),
    ("kl", MISSING),
    ("entropy", MISSING),
    ("win_rate", MISSING),
    ("kl_break", KL_BREAK),
    ("kl_consec", KL_BREAK_CONSEC),
    ("ent_break", ENT_BREAK),
    ("ent_consec", ENT_BREAK_CONSEC),
    ("ent_max_winrate", ENT_BREAK_MAX_WINRATE),
    ("ent_peak", None),
    ("enabled", True),
]


def _spec(**over: object) -> BreakerSpec:
    base: dict[str, object] = {
        "kl_streak": 0,
        "ent_streak": 0,
        "kl": 0.05,
        "entropy": 1.5,
        "win_rate": 0.3,
    }
    base.update(over)
    return BreakerSpec(**cast(dict, base))


def test_field_table_matches_the_old_signature() -> None:
    assert _table(BreakerSpec) == FIELDS


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(BreakerSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, rows[-1].name, False)


def test_entry_takes_exactly_one_spec() -> None:
    assert list(inspect.signature(breaker_update).parameters) == ["spec"]


def test_old_positional_form_is_rejected() -> None:
    call = cast(Callable[..., object], breaker_update)
    with pytest.raises(TypeError):
        call(0, 0, kl=0.05, entropy=1.5, win_rate=0.3)


def test_kl_and_ent_thresholds_do_not_leak_across_rules() -> None:
    """KL 阈值再高也不许让 ENT 规则停下，反之亦然（两族阈值互不挪用）。"""
    # KL 这条路只有 kl_break 生效：kl 高但连击不够 ⇒ 不停。
    assert breaker_update(_spec(kl=0.9, kl_break=0.075))[2] is None
    # ENT 这条路只看 ent_break：把 KL 阈值调到 0 也不影响它。
    trip = breaker_update(_spec(kl=0.0, kl_break=0.0, entropy=0.1, ent_consec=1))[2]
    assert trip is not None, "ENT 规则该停"
    assert trip.startswith("entropy<="), trip


def test_disabled_short_circuits_every_rule() -> None:
    _kl, _ent, trip = breaker_update(
        _spec(kl=99.0, entropy=0.0, enabled=False, ent_consec=1, kl_consec=1)
    )
    assert trip is None


def test_none_peak_falls_back_to_absolute_level() -> None:
    """`ent_peak=None` = 冷启动首轮：退回绝对电平（旧行为），不受「相对跌幅」影响。"""
    trip = breaker_update(_spec(entropy=0.1, ent_peak=None, ent_consec=1))[2]
    assert trip is not None
