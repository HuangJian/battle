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

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_guards import TrainingGuards


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

    # `course`（2026-09-18）：共享 hub 上达令按课程下发——本文件只数 halt 序列，
    # 课程身份的专测住 tests/trainer/test_loop_gate_nopark.py。
    def _fake_set(hub: str, token: str, halt: bool, log=None, course="") -> bool:
        calls.append(halt)
        return True

    monkeypatch.setattr("trainer.loop_guards.set_cloud_halt", _fake_set, raising=True)
    return calls


@pytest.fixture(autouse=True)
def _gate_halt_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """门禁停机模式 2026-10-01 起是**平台文件**：本文件一律把它重定向进 tmp_path。

    不重定向就会写仓根 `tmp/`（那是本机的真工作区）——观测面的回执也该按夹具走。
    """
    monkeypatch.setenv("NN_GATE_HALT", str(tmp_path / "gate-halt.json"))
    monkeypatch.setenv("NN_GATE_HALT_APPLIED", str(tmp_path / "gate-halt.applied.json"))
    monkeypatch.setenv("NN_GATE_HALT_LEG", "local")


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
    """★ notify 模式（平台开关）：连 G7 这类硬门也只提示、不停机。

    平台文件不存在 ⇒ CLI 启动参数兜底（`source=cli`）；这是「文件缺失」唯一允许的兜底。
    """
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake.args.gate_halt_mode = "notify"
    readings = (_reading("G7", "course_valid", released=True),)
    assert TrainingGuards._apply_verdict(fake, 7, _res("REMEDIATE", readings)) is False
    assert halt_calls == []
    assert fake._cloud_halted is False


def _intent_file() -> Path:
    """本用例被重定向后的平台意图文件（`_gate_halt_files` 夹具设的 env）。"""
    return Path(os.environ["NN_GATE_HALT"])


def test_platform_file_beats_startup_arg_and_hot_switches(
    tmp_path: Path, halt_calls: list[bool]
) -> None:
    """★ 平台文件优先于启动参数，且训练途中改文件**立即生效**（不必重启）。

    同时钉住退役面：课程级 `<traj>/gate-halt-mode.txt` 盘上留着也**不生效**（旧值不许复活）。
    """
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake.args.gate_halt_mode = "halt"
    fake._traj_root = tmp_path
    (tmp_path / "gate-halt-mode.txt").write_text("notify\n", encoding="utf-8")
    _intent_file().write_text(json.dumps({"mode": "notify", "until": None}), encoding="utf-8")
    assert TrainingGuards._gate_halt_mode(fake) == "notify"
    readings = (_reading("G7", "course_valid", released=True),)
    TrainingGuards._apply_verdict(fake, 7, _res("REMEDIATE", readings))
    assert halt_calls == []
    # 平台文件切回 halt ⇒ 立即恢复停机（旧 txt 仍写着 notify，但不生效）
    _intent_file().write_text(json.dumps({"mode": "halt"}), encoding="utf-8")
    assert TrainingGuards._gate_halt_mode(fake) == "halt"


def test_bad_platform_file_falls_back_to_halt(tmp_path: Path) -> None:
    """平台文件坏 ⇒ halt（**不回落到 notify**）；缺失 ⇒ CLI 兜底；都没有 ⇒ 缺省 halt。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake.args.gate_halt_mode = "notify"
    _intent_file().write_text("garbage", encoding="utf-8")
    assert TrainingGuards._gate_halt_mode(fake) == "halt"  # 坏文件 ⇒ halt（红线 2）
    _intent_file().unlink()
    assert TrainingGuards._gate_halt_mode(fake) == "notify"  # 缺失 ⇒ CLI 兜底
    fake.args.gate_halt_mode = "whatever"
    assert TrainingGuards._gate_halt_mode(fake) == "halt"  # 都非法 ⇒ 缺省 halt


def test_legacy_txt_is_ignored_and_receipt_records_the_truth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧 txt 不生效但**要告警**；同时回执要把「实际生效」写出去（控制台第二栏的数据源）。"""
    monkeypatch.setenv("RL_COURSE_NAME", "c5-tick")  # 回执按课程分键（进程级课程身份）
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake._traj_root = tmp_path
    (tmp_path / "gate-halt-mode.txt").write_text("notify\n", encoding="utf-8")
    _intent_file().write_text(json.dumps({"mode": "notify", "until": None}), encoding="utf-8")
    assert TrainingGuards._gate_halt_mode(fake) == "notify"
    out = capsys.readouterr().out
    assert "忽略课程级 gate-halt-mode.txt" in out
    assert "gate_mode=notify source=platform" in out
    applied = json.loads(Path(os.environ["NN_GATE_HALT_APPLIED"]).read_text(encoding="utf-8"))
    ent = applied["courses"]["c5-tick"]
    assert (ent["effective_mode"], ent["source"]) == ("notify", "platform")


def test_pause_and_abort_not_affected_by_soft_logic(
    tmp_path: Path, halt_calls: list[bool]
) -> None:
    """PAUSE/ABORT 与 readings 无关，永远停机。"""
    for v in ("PAUSE", "ABORT"):
        fake = _fake(jsonl=tmp_path / "tl.jsonl")
        readings = (_reading("G4", "plateau", released=True),)
        TrainingGuards._apply_verdict(fake, 1, _res(v, readings))
    assert halt_calls == [True, True]


# ---- I2：提示类门 REMEDIATE N 次即停（2026-09-13 roadmap）----


def test_soft_remediate_n_times_stops_leg(tmp_path: Path, halt_calls: list[bool]) -> None:
    """★ I2 核心回归：plateau REMEDIATE 连续 N 次（默认 4）⇒ 第 N 次停腿。

    c6-pickup3 6 次 / c6-bonus 10 次 cloud halt 的教训：平台期每 5 轮必然复现
    REMEDIATE——反复确认的「边际收益枯竭」就是停腿信号，不是继续烧钱的理由。
    """
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake.args.gate_remediate_stop_after = 4
    readings = (_reading("G4", "plateau", released=True),)
    for i in range(3):
        assert TrainingGuards._apply_verdict(fake, 25 + i * 5, _res("REMEDIATE", readings)) is False
    assert getattr(fake, "_leg_abort", False) is False  # 未达阈值：照常继续
    # 第 4 次：停腿（返回 True = 训练该停）+ ABORT 落账
    assert TrainingGuards._apply_verdict(fake, 40, _res("REMEDIATE", readings)) is True
    assert fake._leg_abort is True
    txt = (tmp_path / "tl.jsonl").read_text(encoding="utf-8")
    assert '"verdict": "ABORT"' in txt  # 停腿落账（write_gate_verdict 为 ensure_ascii，
    # 中文 reason 是 \u 转义——按结构断言，不按字面子串）


def test_soft_remediate_counter_resets_on_hard_verdict(
    tmp_path: Path, halt_calls: list[bool]
) -> None:
    """硬门 REMEDIATE 不进软计数（_is_soft_verdict=False），计数不被稀释。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake.args.gate_remediate_stop_after = 2
    soft = (_reading("G4", "plateau", released=True),)
    hard = (_reading("G7", "course_valid", released=True),)
    TrainingGuards._apply_verdict(fake, 10, _res("REMEDIATE", soft))  # 计数=1
    TrainingGuards._apply_verdict(fake, 15, _res("REMEDIATE", hard))  # 硬门：不计数
    assert getattr(fake, "_soft_remediate_count", 0) == 1
    assert getattr(fake, "_leg_abort", False) is False
    assert TrainingGuards._apply_verdict(fake, 20, _res("REMEDIATE", soft)) is True  # 计数=2 → 停


def test_soft_remediate_stop_disabled_with_zero(tmp_path: Path, halt_calls: list[bool]) -> None:
    """0 = 关（旧行为）：任意多次软 REMEDIATE 都不停腿。"""
    fake = _fake(jsonl=tmp_path / "tl.jsonl")
    fake.args.gate_remediate_stop_after = 0
    readings = (_reading("G4", "plateau", released=True),)
    for i in range(10):
        assert TrainingGuards._apply_verdict(fake, i, _res("REMEDIATE", readings)) is False
    assert getattr(fake, "_leg_abort", False) is False


def test_soft_remediate_counter_is_per_leg(tmp_path: Path) -> None:
    """计数挂在 TrainingGuards 实例上——一腿一实例，换腿自然清零（无跨腿污染）。"""
    a = _fake(jsonl=tmp_path / "a.jsonl")
    b = _fake(jsonl=tmp_path / "b.jsonl")
    a.args.gate_remediate_stop_after = 2
    readings = (_reading("G4", "plateau", released=True),)
    TrainingGuards._apply_verdict(a, 5, _res("REMEDIATE", readings))
    TrainingGuards._apply_verdict(a, 10, _res("REMEDIATE", readings))
    assert a._leg_abort is True
    assert getattr(b, "_soft_remediate_count", 0) == 0  # 新腿从零起算
    assert getattr(b, "_leg_abort", False) is False
