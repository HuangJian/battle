"""R2 事件 rung 接线测试（plan/new-era-stop.plan.md §6 R2）。

覆盖课程字段 → argparse 映射 → corpus 指纹 → rollout 命令 → 分发参数
→ 节点能力门，缺一即静默（旧 agent 跑均匀局混入事件批 / 事件批跑均匀局）。
"""
from __future__ import annotations

import types

import pytest

from trainer.batch_plan import node_supports_decision_events
from worker.cmd import build_rollout_cmd
from worker.config import CourseConfig, corpus_identity_fp


def _course(**kw) -> CourseConfig:
    return CourseConfig(name="h5e", mode="per-tick", **kw)


def _args(**kw) -> types.SimpleNamespace:
    base = {
        "goal_rollout": False,
        "intent_rollout": False,
        "max_ticks": 2400,
        "difficulty": "hard",
        "dodge": "",
        "max_hours": 0,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_course_field_defaults_false() -> None:
    assert _course().decision_events is False
    assert _course(decision_events=True).decision_events is True


def test_mapping_present_only_when_explicit() -> None:
    """漏映射 = 静默失效（ent_break / paired_rotate_seed 前科同款）。"""
    assert "decision_events" not in _course().flat_overrides()
    assert _course(decision_events=True).flat_overrides()["decision_events"] is True


def test_corpus_fp_changes_only_when_active() -> None:
    """缺席 = 老课程指纹不动（在跑的腿不断血缘）；激活 = 身份变。"""
    plain = _course()
    assert corpus_identity_fp(plain) == corpus_identity_fp(_course())
    active = _course(decision_events=True)
    assert corpus_identity_fp(active) != corpus_identity_fp(plain)


def _cmd(**kw) -> list[str]:
    return build_rollout_cmd(
        "bun",
        _args(**kw),
        weights="tmp/w.json",
        out_dir="tmp/out",
        stage=2000,
        seed=7,
        wver="abc",
        node_label="local",
    )


def test_rollout_cmd_flag_only_when_set() -> None:
    assert "--decision-events" not in _cmd()
    cmd = _cmd(decision_events=True)
    assert "--decision-events" in cmd
    assert "--decision_events" not in cmd


def test_node_gate_predicate() -> None:
    assert node_supports_decision_events({}) is False
    assert node_supports_decision_events({"decisionEventsSupport": False}) is False
    assert node_supports_decision_events({"decisionEventsSupport": True}) is True


def test_fetch_task_encodes_flag(monkeypatch) -> None:
    """透传不断：flag 开 ⇒ 查询串带 decisionEvents=1；关 ⇒ 不带（旧 agent 照旧）。"""
    import common.distribution

    seen: dict = {}

    class _BoomError(Exception):
        pass

    def fake_request(url, auth_key, timeout, headers=None):
        seen["url"] = url
        raise _BoomError()

    monkeypatch.setattr(common.distribution, "_request", fake_request)
    # _request 的异常会被包成 DistError——URL 已在抛错前捕获，断它即可。
    with pytest.raises(common.distribution.DistError):
        common.distribution.fetch_task(
            "http://x/", "k", iter_id="i", wver="w", stage=2000, seed=7,
            max_ticks=100, difficulty="hard", timeout=5.0, decision_events=True,
        )
    assert "decisionEvents=1" in seen["url"]
    with pytest.raises(common.distribution.DistError):
        common.distribution.fetch_task(
            "http://x/", "k", iter_id="i", wver="w", stage=2000, seed=7,
            max_ticks=100, difficulty="hard", timeout=5.0,
        )
    assert "decisionEvents" not in seen["url"]
