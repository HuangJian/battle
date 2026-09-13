"""remote/protocol.py — BC（kind=bc）任务契约测试（plan/bc-cloud-integration.plan.md §1）。

红线：
  * kind 缺省 = ppo（旧 manifest wire 兼容；PPO 行为零变化）；
  * kind=bc 必填基础字段 + arch，免除 reward/γ/λ；mode 必须 "bc"（串型拒收）；
  * bc 结果校验走 metrics（无 agg）；幂等键/公式不变。
"""

from __future__ import annotations

import pytest

from remote.protocol import (
    MANIFEST_BC_EXTRA,
    ProtocolError,
    normalize_manifest,
    validate_result,
)


def _bc_manifest(**over):
    m = {
        "proto": 1,
        "runId": "bc-run",
        "it": 1,
        "job_id": "j" * 16,
        "commit": "c" * 40,
        "code_sha256": "s" * 64,
        "course": '{"name":"bc-c4"}',
        "course_fp": "f" * 64,
        "corpus_fp": "p" * 64,
        "mode": "bc",
        "seed": "abcd1234",
        "epochs": 3,
        "mb": 128,
        "lr": 3e-3,
        "init_weights_fp": "bc",
        "data_fp": "d" * 64,
        "payload_sha256": "h" * 64,
        "kind": "bc",
        "arch": "student",
    }
    m.update(over)
    return m


def _bc_result(**over):
    r = {
        "job_id": "j" * 16,
        "data_fp": "d" * 64,
        "init_weights_fp": "bc",
        "weights_json": "e30=",
        "commit_echo": "c" * 40,
        "metrics": {
            "epochs": 3,
            "train_samples": 100,
            "val_samples": 10,
            "best_val_loss": 0.42,
        },
    }
    r.update(over)
    return r


def test_bc_manifest_normalizes() -> None:
    m = normalize_manifest(_bc_manifest())
    assert m["kind"] == "bc"
    assert m["mode"] == "bc"
    assert "gamma" not in m  # PPO 专有键不入 bc manifest 语义（缺失即可，缺省不注入）
    assert m["kl_coef"] == 0.0  # 可选缺省仍归一化（worker 读它安全）


def test_bc_manifest_requires_arch() -> None:
    bad = _bc_manifest()
    del bad["arch"]
    with pytest.raises(ProtocolError, match="arch"):
        normalize_manifest(bad)
    assert set(MANIFEST_BC_EXTRA) == {"arch"}


def test_bc_arch_validated() -> None:
    with pytest.raises(ProtocolError, match="arch"):
        normalize_manifest(_bc_manifest(arch="resnet"))


def test_bc_mode_red_line() -> None:
    # bc 任务 mode 必须 "bc"——per-tick 串型拒收
    with pytest.raises(ProtocolError, match="mode='bc'"):
        normalize_manifest(_bc_manifest(mode="per-tick"))


def test_bc_missing_base_field_rejected() -> None:
    bad = _bc_manifest()
    del bad["data_fp"]
    with pytest.raises(ProtocolError, match="data_fp"):
        normalize_manifest(bad)


def test_bc_reward_fields_exempt_but_validated_if_present() -> None:
    # γ/λ 对 bc **免必填**（缺席合法）；但显式携带时统一校验仍生效（fail fast 优先）。
    m = _bc_manifest()
    assert "gamma" not in normalize_manifest(m)  # 缺席 = 合法
    with pytest.raises(ProtocolError, match="gamma"):
        normalize_manifest(_bc_manifest(gamma=-1))


def test_legacy_manifest_defaults_to_ppo() -> None:
    # 无 kind 键 = 旧 hub 产物 → ppo 语义（mode 红线照旧）
    ppo = {
        "proto": 1,
        "runId": "r",
        "it": 1,
        "job_id": "j",
        "commit": "c",
        "code_sha256": "s",
        "course": "{}",
        "course_fp": "f",
        "reward_formula": "",
        "formula_hash": "h",
        "metrics_version": 2,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s",
        "epochs": 2,
        "mb": 64,
        "lr": 3e-4,
        "init_weights_fp": "w",
        "data_fp": "d",
        "payload_sha256": "p",
    }
    m = normalize_manifest(ppo)
    assert m["kind"] == "ppo"
    with pytest.raises(ProtocolError, match="per-tick"):
        normalize_manifest({**ppo, "mode": "bc"})  # 无 kind 的 bc mode = 旧红线拒收


def test_bc_result_validates() -> None:
    m = normalize_manifest(_bc_manifest())
    r = validate_result(_bc_result(), m, commit_echo_must_match=False)
    assert r["metrics"]["epochs"] == 3


def test_bc_result_requires_metrics() -> None:
    m = normalize_manifest(_bc_manifest())
    bad = _bc_result()
    del bad["metrics"]
    with pytest.raises(ProtocolError, match="metrics"):
        validate_result(bad, m, commit_echo_must_match=False)
    bad2 = _bc_result(metrics={"epochs": 1})  # 缺 train/val/best_val
    with pytest.raises(ProtocolError, match="metrics"):
        validate_result(bad2, m, commit_echo_must_match=False)


def test_bc_result_fencing() -> None:
    m = normalize_manifest(_bc_manifest())
    with pytest.raises(ProtocolError, match="data_fp"):
        validate_result(_bc_result(data_fp="x" * 64), m, commit_echo_must_match=False)
    with pytest.raises(ProtocolError, match="init_weights_fp"):
        validate_result(_bc_result(init_weights_fp="zzz"), m, commit_echo_must_match=False)


def test_bc_job_id_stable_and_kind_independent_of_key() -> None:
    from remote.protocol import job_id

    m1 = normalize_manifest(_bc_manifest())
    m2 = normalize_manifest(_bc_manifest())
    assert job_id(m1) == job_id(m2)  # 同幂等键 → 同 job_id（幂等重发布）
