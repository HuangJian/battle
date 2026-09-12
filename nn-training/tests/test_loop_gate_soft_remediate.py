"""G4(plateau) 的 REMEDIATE 不得下发 cloud halt（2026-09-13 P0 止血）。

事故背景：`CLOUD_HALT_VERDICTS` 把 `REMEDIATE` 一并算作"要停云机"，而 G4 plateau 的
REMEDIATE 语义只是"**边际收益枯竭**"，平台期每 5 轮必然复现 ⇒ 云端 PPO worker 被反复杀：

  * `c6-pickup3`：it35/40/45/50/55/60 共 **6 次** cloud halt
  * `c6-bonus`  ：it25…it70 共 **10 次** cloud halt（占 74 轮里的 45 轮）

⇒ 两条腿后半程都在"PPO worker 反复被杀"的环境下训练，且 G4 用**训练内** win_rate 判，
   看不见配对口径下的退化（c4-dodge 同一个判错轴教训）。

修复：`TrainingGuards.NO_CLOUD_HALT_KINDS` —— 当 REMEDIATE **只**由提示类门
（kind=plateau）触发时跳过停机达令；G7/G13/PAUSE/ABORT 等硬判决照常停。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.loop_guards import TrainingGuards


def _fake(hub: str = "http://hub", token: str = "tok", jsonl: Path | None = None) -> TrainingGuards:
    obj = TrainingGuards.__new__(TrainingGuards)
    obj.args = SimpleNamespace(remote_hub_url=hub, remote_token=token)
    obj._jsonl_path = jsonl
    obj._cloud_halted = False
    return obj


def _reading(gid: str, kind: str, released: bool = True) -> SimpleNamespace:
    r = SimpleNamespace(id=gid, kind=kind, fired=True, released=released, reason="test")
    # _apply_verdict 落账时走 RuleReading.to_dict()，这里给个等价替身。
    r.to_dict = lambda: {
        "id": gid,
        "kind": kind,
        "fired": True,
        "released": released,
        "reason": "test",
    }
    return r


def _res(verdict: str, readings: tuple) -> SimpleNamespace:
    return SimpleNamespace(
        verdict=verdict,
        reason="test",
        route="REMEDIATE",
        readings=readings,
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


def test_plateau_only_remediate_does_not_halt_cloud(
    tmp_path: Path, halt_calls: list[bool]
) -> None:
    """★ 核心回归：只有 G4(plateau) released ⇒ 记录 verdict 但**不发**停机达令。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    readings = (
        _reading("G1", "wins_mastery", released=False),
        _reading("G2", "skill_floor", released=False),
        _reading("G4", "plateau", released=True),  # ← 唯一 released
        _reading("G7", "course_valid", released=False),
    )
    assert TrainingGuards._apply_verdict(fake, 25, _res("REMEDIATE", readings)) is False
    assert halt_calls == []  # 关键：没有停机达令
    assert fake._cloud_halted is False
    # verdict 仍然落账（复盘取证不能少）
    txt = (tmp_path / "tl.jsonl").read_text(encoding="utf-8")
    assert '"verdict": "REMEDIATE"' in txt


def test_hard_gate_remediate_still_halts_cloud(tmp_path: Path, halt_calls: list[bool]) -> None:
    """G7(course_valid)/G13(duty) 这类硬门触发的 REMEDIATE 照常停机。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    readings = (_reading("G7", "course_valid", released=True),)
    assert TrainingGuards._apply_verdict(fake, 25, _res("REMEDIATE", readings)) is False
    assert halt_calls == [True]
    assert fake._cloud_halted is True


def test_mixed_plateau_and_hard_still_halts(tmp_path: Path, halt_calls: list[bool]) -> None:
    """plateau + 硬门同时 released ⇒ 保守停机（宁可停，不可漏）。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    readings = (
        _reading("G4", "plateau", released=True),
        _reading("G7", "course_valid", released=True),
    )
    TrainingGuards._apply_verdict(fake, 25, _res("REMEDIATE", readings))
    assert halt_calls == [True]


def test_empty_readings_keeps_legacy_halt_behaviour(
    tmp_path: Path, halt_calls: list[bool]
) -> None:
    """读不到 readings（老格式/异常）⇒ 维持既有停机行为，不因读不到而漏停。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    assert TrainingGuards._apply_verdict(fake, 3, _res("REMEDIATE", ())) is False
    assert halt_calls == [True]


def test_notify_mode_never_halts_even_for_hard_gate(
    tmp_path: Path, halt_calls: list[bool]
) -> None:
    """★ notify 模式（控制台开关）：连 G7 这类硬门也只提示、不停机。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake.args.gate_halt_mode = "notify"
    fake._traj_root = Path(tmp_path)  # 无标志文件 ⇒ 回退启动参数
    readings = (_reading("G7", "course_valid", released=True),)
    assert TrainingGuards._apply_verdict(fake, 7, _res("REMEDIATE", readings)) is False
    assert halt_calls == []
    assert fake._cloud_halted is False


def test_flag_file_overrides_startup_arg(tmp_path: Path, halt_calls: list[bool]) -> None:
    """标志文件优先于启动参数 ⇒ 训练途中切换**立即生效**（不必重启）。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake.args.gate_halt_mode = "halt"
    fake._traj_root = Path(tmp_path)
    (tmp_path / "gate-halt-mode.txt").write_text("notify\n", encoding="utf-8")
    assert TrainingGuards._gate_halt_mode(fake) == "notify"
    readings = (_reading("G7", "course_valid", released=True),)
    TrainingGuards._apply_verdict(fake, 7, _res("REMEDIATE", readings))
    assert halt_calls == []
    # 切回 halt 立即恢复停机
    (tmp_path / "gate-halt-mode.txt").write_text("halt\n", encoding="utf-8")
    assert TrainingGuards._gate_halt_mode(fake) == "halt"


def test_bad_flag_file_falls_back_to_arg(tmp_path: Path) -> None:
    """标志文件内容非法 / 不可读 ⇒ 回退启动参数，再回退 halt（保守，绝不误判不停机）。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake.args.gate_halt_mode = "notify"
    fake._traj_root = Path(tmp_path)
    (tmp_path / "gate-halt-mode.txt").write_text("garbage\n", encoding="utf-8")
    assert TrainingGuards._gate_halt_mode(fake) == "notify"  # 非法 → 启动参数
    fake.args.gate_halt_mode = "whatever"
    assert TrainingGuards._gate_halt_mode(fake) == "halt"  # 都非法 → 默认 halt


def test_pause_and_abort_not_affected_by_soft_logic(
    tmp_path: Path, halt_calls: list[bool]
) -> None:
    """PAUSE/ABORT 与 readings 无关，永远停机。"""
    for v in ("PAUSE", "ABORT"):
        fake = _fake(jsonl=tmp_path / "tl.jsonl")
        readings = (_reading("G4", "plateau", released=True),)
        TrainingGuards._apply_verdict(fake, 1, _res(v, readings))
    assert halt_calls == [True, True]
