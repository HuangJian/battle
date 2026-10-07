"""决策周期 K 接线测试（plan/k5-rhythm.plan.md）。

覆盖课程字段 → argparse 映射 → corpus 指纹 → rollout 命令 → 分发参数
→ 节点能力门，缺一即静默（旧 agent 跑 K=10 混入 K=5 批 / 老课程指纹漂移）。
"""
from __future__ import annotations

import types

import pytest
from pydantic import ValidationError

from trainer.batch_plan import node_supports_decision_k
from worker.cmd import build_rollout_cmd
from worker.config import CourseConfig, corpus_identity_fp


def _course(**kw) -> CourseConfig:
    return CourseConfig(name="k5", mode="per-tick", **kw)


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


def test_course_field_default_and_validation() -> None:
    """缺省 = 10（历史行为）；脏值拒课（bool 不算 int，<1 拒绝）。"""
    assert _course().decision_k == 10
    assert _course(decision_k=5).decision_k == 5
    with pytest.raises(ValidationError):
        _course(decision_k=0)
    with pytest.raises(ValidationError):
        _course(decision_k=True)


def test_mapping_present_only_when_explicit() -> None:
    """漏映射 = 静默失效（ent_break / paired_rotate_seed 前科同款）。"""
    assert "decision_k" not in _course().flat_overrides()
    assert _course(decision_k=5).flat_overrides()["decision_k"] == 5


def test_corpus_fp_changes_only_when_non_default() -> None:
    """缺席/10 = 老课程指纹不动（在跑的腿不断血缘）；≠10 = 身份变（决策粒度变）。"""
    plain = _course()
    assert corpus_identity_fp(plain) == corpus_identity_fp(_course())
    assert corpus_identity_fp(plain) == corpus_identity_fp(_course(decision_k=10))
    assert corpus_identity_fp(_course(decision_k=5)) != corpus_identity_fp(plain)


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


def test_rollout_cmd_flag_only_when_non_default() -> None:
    """缺省/10 = 命令逐字节不变；≠10 才透传（键名连字符，值与课程一致）。"""
    assert "--decision-k" not in _cmd()
    assert "--decision-k" not in _cmd(decision_k=10)
    cmd = _cmd(decision_k=5)
    assert cmd[cmd.index("--decision-k") + 1] == "5"
    assert "--decision_k" not in cmd


def test_node_gate_predicate() -> None:
    assert node_supports_decision_k({}) is False
    assert node_supports_decision_k({"decisionKSupport": False}) is False
    assert node_supports_decision_k({"decisionKSupport": True}) is True


def test_fetch_task_encodes_k(monkeypatch) -> None:
    """透传不断：K≠10 ⇒ 查询串带 decisionK=5；缺省/10 ⇒ 不带（旧 agent 照旧）。"""
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
            max_ticks=100, difficulty="hard", timeout=5.0, decision_k=5,
        )
    assert "decisionK=5" in seen["url"]
    with pytest.raises(common.distribution.DistError):
        common.distribution.fetch_task(
            "http://x/", "k", iter_id="i", wver="w", stage=2000, seed=7,
            max_ticks=100, difficulty="hard", timeout=5.0,
        )
    assert "decisionK" not in seen["url"]


def test_poll_url_keeps_gate_components(monkeypatch) -> None:
    """轮询 URL 与提交端同配方（de/k 分量都在）——漏传 = 异步任务永远 404。

    2026-10-07 审计修复：`decisionEvents` 此前漏在这层；`decisionK` 是新通道，
    一并钉住（agent 的 /v1/result 按同配方重算 taskKey）。
    """
    import common.distribution

    seen: dict = {}

    def fake_request(url, auth_key, timeout=30.0, **kw):
        seen["url"] = url
        return 202, b'{"status": "running"}'

    monkeypatch.setattr(common.distribution, "_request", fake_request)
    monkeypatch.setattr(common.distribution, "POLL_MIN_BUDGET_SEC", 0.05)
    with pytest.raises(common.distribution.DistError):
        common.distribution._poll_result(
            "http://node",
            "tok",
            {"iterId": "r.1", "stage": 0, "seed": 1, "decisionEvents": "1", "decisionK": "5"},
            budget=0.05,
            poll_s=0.02,
        )
    assert "decisionEvents=1" in seen["url"]
    assert "decisionK=5" in seen["url"]
