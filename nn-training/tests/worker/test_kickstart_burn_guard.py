"""tests/worker/test_kickstart_burn_guard.py —— 干烧熔断的三个执行面缺陷（2026-10-23）。

现场：y32-tempo it31 / y32-nohp it41 双双被 `CRITICAL KICKSTART-BURN` 收官 ⇒ trainer 不再
发布 PPO job ⇒ 云端 worker 整整 2 小时领不到任务。三条缺陷各一个用例：

1. **缰绳已退火到期（kk≈0）仍在判**「回锚」——判据的定义域是「锚主导更新把起点洗回去」。
2. **平台开关 = notify 时仍本地停车**——`_sync_cloud_halt` 的契约写着「TrainingLoop 永不停车」。
3. **auto/paired 解析不出对端时回退 baseline**——baseline 参照的零假设假阳性在
   `worker/kickstart_burn.py` 头注的回测里是 49.3%（paired 0.56%），拿它停腿 = 定时炸弹。

判据本体（`burn_verdict`）一个字没动：本次只改**执行面**「什么时候允许判 / 判中了停不停」。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from worker import loop_guards_leg as guard_mod
from worker.loop_guards_leg import TrainingGuardsLeg

BASE = 0.9025
LOW = 0.79  # 比 BASE 低 11.25pp > 5pp 门限


def _write_trend(traj, rows: list[tuple[int, float | None]]) -> None:
    p = traj / "eval_log.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for it, wr in rows:
            f.write(json.dumps({"event": "eval_summary", "iter": it, "winRate": wr}) + "\n")


class _Leg(TrainingGuardsLeg):
    """最小可跑实例：只喂 `_kickstart_burn` 真正会碰的那几个属性。"""

    def __init__(self, traj, *, gate_mode="halt", peer=None, block=None, ref=True):
        self.args = SimpleNamespace(kickstart_ref=ref, kickstart_burn=block, out="tmp/x/weights.json")
        self._traj_root = traj
        self._jsonl_path = traj / "training_log.jsonl"
        self._ledger: list[Any] = []
        self._ledger_apply = lambda rec: self._ledger.append(rec)
        self._halt_calls: list[str] = []
        self._sync_cloud_halt = lambda it, verdict, readings=None: self._halt_calls.append(verdict)
        self._gate_halt_mode = lambda: gate_mode
        self._peer = peer
        self._burn_streak = 0
        self._burn_fallback_logged = False
        self._burn_notified = False
        self._stop_cause = ""
        self._stop_detail = ""

    def _burn_paired_rows(self, peer_name):
        return self._peer


@pytest.fixture(autouse=True)
def _rein_alive(monkeypatch):
    """缺省：缰绳仍活跃（让用例显式打开「已退火」那一条）。

    `raising=False`：修复前的版本没有 `_rein_active`，桩子照样能装上 ⇒ 旧版的红来自
    **判据真的停腿了**，而不是「函数不存在」这种假红。
    """
    monkeypatch.setattr(guard_mod, "_rein_active", lambda args, it: True, raising=False)


def _tripped_leg(tmp_path, **kw):
    """账本：it0 = 起点，it5/10/15 连续三点低 ⇒ baseline/paired 判据都会 tripped。"""
    _write_trend(tmp_path, [(0, BASE), (5, LOW), (10, LOW), (15, LOW)])
    return _Leg(tmp_path, **kw)


def test_rein_exhausted_does_not_judge(tmp_path, monkeypatch):
    """缺陷 2：kk 归零后不再判「回锚」，并清空落后计数。"""
    monkeypatch.setattr(guard_mod, "_rein_active", lambda args, it: False)
    leg = _tripped_leg(tmp_path, block={"mode": "baseline"})
    leg._burn_streak = 2
    assert leg._kickstart_burn(15) is False
    assert leg._stop_cause == ""
    assert leg._burn_streak == 0


def test_notify_mode_logs_but_does_not_stop_leg(tmp_path, capsys):
    """缺陷 1：平台开关 notify ⇒ 只提示、不停腿（连呼两次只喊一次）。"""
    leg = _tripped_leg(tmp_path, gate_mode="notify", block={"mode": "baseline"})
    assert leg._kickstart_burn(15) is False
    assert leg._stop_cause == "", "notify 模式不许本地停车"
    out1 = capsys.readouterr().out
    assert "CRITICAL KICKSTART-BURN" in out1
    assert "不停车" in out1
    # 结论账照写（控制台要能看到判决），但带 notify 标记
    gate = [rec for rec in leg._ledger if rec.get("event") == "gate_verdict"]
    assert len(gate) == 1
    assert (gate[0]["verdict"], gate[0]["decider"]) == ("ABORT", "loop")
    assert gate[0]["reason"].startswith("kickstart-burn(notify")
    # 第二次不再刷屏
    assert leg._kickstart_burn(15) is False
    assert capsys.readouterr().out == "", "notify 只喊一次——第二次一条输出都不该有"


def test_halt_mode_still_stops_leg(tmp_path):
    """回归：halt 模式语义不变（显式选择要认）。"""
    leg = _tripped_leg(tmp_path, gate_mode="halt", block={"mode": "baseline"})
    assert leg._kickstart_burn(15) is True
    assert leg._stop_cause == "kickstart_burn"
    assert leg._halt_calls == ["ABORT"]


def test_auto_without_peer_does_not_fall_back_to_baseline(tmp_path):
    """缺陷 3：mode 缺省（auto）且解析不出唯一对端 ⇒ **不判**，不拿 baseline 代打。"""
    leg = _tripped_leg(tmp_path, gate_mode="halt", block=None, peer=None)
    assert leg._kickstart_burn(15) is False
    assert leg._stop_cause == ""
    assert leg._burn_streak == 0


def test_paired_with_peer_still_stops(tmp_path):
    """回归：paired 解析出对端时照判（本臂低 11pp ⇒ 杀臂）。"""
    peer_rows = tuple({"event": "eval_summary", "iter": i, "winRate": BASE} for i in (5, 10, 15))
    leg = _tripped_leg(tmp_path, gate_mode="halt", block={"mode": "paired"}, peer=("peer-leg", peer_rows))
    assert leg._kickstart_burn(15) is True
    assert leg._stop_cause == "kickstart_burn"


def test_kickstart_ref_off_short_circuits(tmp_path):
    """回归：没开缰绳 = 一条都不判（历史行为）。"""
    leg = _tripped_leg(tmp_path, ref=False, block={"mode": "baseline"})
    assert leg._kickstart_burn(15) is False
    assert leg._burn_streak == 0


def test_notify_keeps_streak_for_instant_halt_on_switch(tmp_path):
    """notify 不停腿，但 streak 要留着：平台开关拨到 halt 后**下一次判定立即停**。"""
    leg = _tripped_leg(tmp_path, gate_mode="notify", block={"mode": "baseline"})
    assert leg._kickstart_burn(15) is False
    assert leg._burn_streak == 3
    leg._gate_halt_mode = lambda: "halt"
    assert leg._kickstart_burn(15) is True
    assert leg._stop_cause == "kickstart_burn"
