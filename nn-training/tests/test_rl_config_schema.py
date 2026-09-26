"""`rl_config_schema.py` 契约（plan/rl-config-cleanup.plan.md §3.4、E8）。

三条要钉住的性质：
  · **只告警不拒**：未知 / 已退役键只出现在返回列表里，绝不抛（未知键 ≠ 训练起不来）；
  · **已退役比未知更具体**：`rl.stream` 这类给的是「已退役 + 原因」，不是泛泛的「未知键」；
  · **自由形状段不下钻**：`nodes` / `courses` / `rl.remote_hubs` / `rl.intent` / `rl.goal`
    是每课/每节点/每模式自定的，键名不在白名单里也**不算**未知。
"""

from __future__ import annotations

import pytest

import rl_config_schema as S


def _clean_cfg() -> dict:
    """今天（清洗后）真机 rl-config 的形状——应零告警。"""
    return {
        "version": 1,
        "policy": {"taskTimeoutSec": 900, "queueWindowSec": 1800, "streamKlCap": 0.18},
        "nodes": [{"id": "self", "url": "http://127.0.0.1:8443", "authKey": "k"}],
        "rl": {
            "hub_port": 8787,
            "agent_port": 8443,
            "mb": 512,
            "workers": 8,
            "remote_token": "t",
            "remote_hubs": {"x1-rebirth": "http://u"},
            "rollout_src": "local",
        },
        "courses": {"x20-dodge-l2a": {"rollout_src": "run", "run_iters": -1}},
    }


def test_clean_config_has_no_warnings() -> None:
    assert S.check_rl_config(_clean_cfg()) == []


def test_retired_keys_are_flagged_with_a_reason() -> None:
    """已退役键给「已退役 + 原因」，而不是泛泛的未知——这是 §3.1 那些删除的可回归钉子。"""
    cfg = {
        "intent_rl": {"iters": 0},
        "policy": {
            "upgradeBranch": "",
            "minDiskFreeMB": 2048,
            "streamKlCapIntent": 0.5,
            "streamWaveGamesIntent": 200,
        },
        "rl": {"stream": 0, "double_buffer": 0, "precollect_early": 0},
    }
    out = " | ".join(S.check_rl_config(cfg))
    for dotted in (
        "intent_rl",
        "policy.upgradeBranch",
        "policy.minDiskFreeMB",
        "policy.streamKlCapIntent",
        "policy.streamWaveGamesIntent",
        "rl.stream",
        "rl.double_buffer",
        "rl.precollect_early",
    ):
        assert dotted in out, f"{dotted} 未被点出"
    assert out.count("已退役") == len(S.retired_keys())


def test_unknown_keys_warn_but_never_raise() -> None:
    cfg = {"policy": {"taskTmeoutSec": 1}, "rl": {"mb_": 1}, "mystery": {"x": 1}}
    out = S.check_rl_config(cfg)
    assert any("policy.taskTmeoutSec" in line for line in out)
    assert any("rl.mb_" in line for line in out)
    assert any("mystery" in line for line in out)


def test_freeform_sections_are_not_drilled() -> None:
    cfg = {
        "nodes": [{"id": "a", "authKey": "k", "whatever_field": 1}],
        "courses": {"c": {"any_course_knob": 1}},
        "rl": {"remote_hubs": {"whatever": "u"}, "intent": {"nested": 1}, "goal": {"nested": 2}},
    }
    assert S.check_rl_config(cfg) == []


@pytest.mark.parametrize("cfg", [{}, {"version": 1}])
def test_empty_or_shape_broken_input_is_silent(cfg: dict) -> None:
    assert S.check_rl_config(cfg) == []


def test_schema_data_is_self_consistent() -> None:
    """退役键不得同时出现在该段白名单里（否则一会告警一会放行）。"""
    retired = S.retired_keys()
    assert retired, "白名单数据没加载到（rl_config.schema.json 缺失？）"
    for dotted in retired:
        parts = dotted.split(".", 1)
        if len(parts) == 2:
            assert parts[1] not in S.allowed_keys(parts[0]), f"{dotted} 同时在退役表与白名单里"


def test_retired_reason_is_non_empty() -> None:
    assert all(reason.strip() for reason in S.retired_keys().values())
