"""门判决 → 永不停车、只联动云端停机/恢复（DECISIONS §2026-09-11-gates-never-park-loop）。

回归保护（用户定案：trainingloop 永远不要停，该停的是远端云机）：
  * REMEDIATE/PAUSE/ABORT 落 `gate_verdict` 事件但**返回 False**（loop 继续跑）；
  * 停机达令只发给 hub（set_cloud_halt(True)）；HOLD/ADVANCE → resume；
  * STOP（预算到顶，走 _budget_hard_cut）不触发停机达令；
  * 无 hub（local/push，remote_hub_url 空）→ 短路零行为。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.loop_guards import TrainingGuards


def _fake(hub: str = "http://hub", token: str = "tok", jsonl: Path | None = None) -> TrainingGuards:
    # TrainingGuards 是 mixin（无 __init__）——__new__ 造实例后手工装状态，
    # 让 mypy 认得 self 类型；_apply_verdict/_sync_cloud_halt 走真实实现。
    obj = TrainingGuards.__new__(TrainingGuards)
    obj.args = SimpleNamespace(remote_hub_url=hub, remote_token=token)
    obj._jsonl_path = jsonl
    obj._cloud_halted = False
    return obj


def _res(verdict: str, reason: str = "test") -> SimpleNamespace:
    return SimpleNamespace(
        verdict=verdict,
        reason=reason,
        route=None,
        readings=(),
        override=None,
        seeds="unknown",
    )


@pytest.fixture
def halt_calls(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    calls: list[bool] = []

    def _fake_set(hub: str, token: str, halt: bool, log=None) -> bool:
        calls.append(halt)
        return True

    monkeypatch.setattr("rl.loop_guards.set_cloud_halt", _fake_set, raising=True)
    return calls


def test_remediate_never_parks_and_halts_cloud(
    tmp_path: Path, halt_calls: list[bool]
) -> None:
    """REMEDIATE：落账但不停车；向 hub 下发停机达令。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    assert TrainingGuards._apply_verdict(fake, 3, _res("REMEDIATE")) is False  # 不停车
    assert halt_calls == [True]
    assert fake._cloud_halted is True
    ev = json.loads((tmp_path / "tl.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert ev["event"] == "gate_verdict" and ev["verdict"] == "REMEDIATE"


def test_pause_and_abort_also_halts_cloud(halt_calls: list[bool], tmp_path: Path) -> None:
    for v in ("PAUSE", "ABORT"):
        fake = _fake(jsonl=tmp_path / "tl.jsonl")
        assert TrainingGuards._apply_verdict(fake, 1, _res(v)) is False
    assert halt_calls == [True, True]


def test_hold_after_halt_resumes_cloud(halt_calls: list[bool], tmp_path: Path) -> None:
    """停机条件消失（HOLD）→ 下发 resume；未停机时 HOLD 幂等不发。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake._cloud_halted = True
    TrainingGuards._sync_cloud_halt(fake, 5, "HOLD")
    assert halt_calls == [False]
    assert fake._cloud_halted is False

    halt_calls.clear()
    fresh = _fake()
    TrainingGuards._sync_cloud_halt(fresh, 5, "HOLD")
    assert halt_calls == []  # 没停机就不发


def test_stop_verdict_does_not_touch_cloud(halt_calls: list[bool], tmp_path: Path) -> None:
    """预算 STOP 走 _budget_hard_cut 真停车——不是停机达令，不发给云机。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    assert TrainingGuards._apply_verdict(fake, 9, _res("STOP", "max_hours 到顶")) is False
    assert halt_calls == []


def test_no_hub_short_circuits(tmp_path: Path, halt_calls: list[bool]) -> None:
    """local/push 无 hub → 短路，不抛、不发。"""
    fake = _fake(hub="", token="", jsonl=tmp_path / "tl.jsonl")
    TrainingGuards._apply_verdict(fake, 1, _res("REMEDIATE"))
    assert halt_calls == []
