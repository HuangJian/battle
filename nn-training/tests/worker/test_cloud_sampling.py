"""tests/worker/test_cloud_sampling.py —— 设备选档 + 档计划的纯逻辑。

钉三件事：

  ① **device 归一**：`cuda*` → gpu，`tpu`/`xla*` → tpu，CPU/未知 → None；
  ② **缺档不回落**：块里没有当前设备的档 ⇒ `ValueError`（调用方转 ProtocolError）；
  ③ **档计划**：target/topup 按档改，rotation 换成档配方；真正的窗口/配额重解仍由
     `worker.plan.volume_for_round` 完成（同一份 `volume_block_for_window`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.course_spec import CourseConfig
from worker.cloud_sampling import (
    CloudProfile,
    device_profile,
    plan_for_profile,
    profile_for_course,
)
from worker.plan import volume_for_round

BASE: dict = {
    "name": "cloud-probe",
    "mode": "per-tick",
    "stages": "2000-2002",
    "target_transitions": 24576,
    "rotation_mode": "even",
    "rotation_per_it": 3,
}


def _course(block: dict) -> CourseConfig:
    return CourseConfig.model_validate({**BASE, "autonomous_cloud_overrides": block})


def _plan() -> dict:
    return {
        "rotate_seed": 4242,
        "pair_args": {"stages": "2000-2002"},
        "volume": {
            "target_transitions": 24576,
            "stages": [2000, 2001, 2002],
            "games_per_stage": 1,
            "per_stage_quota": 8192,
            "est_samples_per_game": 100,
            "est_hi_factor": 1.0,
            "est_s_all": {2000: 100, 2001: 100, 2002: 100},
            "est_s_by_stage": {2000: 100, 2001: 100, 2002: 100},
            "games_per_stage_by_stage": {2000: 1, 2001: 1, 2002: 1},
            "topup": {"max_games_per_stage": 24},
        },
    }


@pytest.mark.parametrize(
    ("device", "want"),
    [
        ("cuda", "gpu"),
        ("cuda:0", "gpu"),
        ("cuda-dp", "gpu"),
        ("gpu", "gpu"),
        ("tpu", "tpu"),
        ("xla", "tpu"),
        ("xla:0", "tpu"),
        ("cpu", None),
        ("", None),
        (None, None),
    ],
)
def test_device_profile_normalizes_resolved_devices(device, want) -> None:
    assert device_profile(device) == want


def test_profile_for_course_selects_by_device_and_refuses_a_missing_tier() -> None:
    both = _course(
        {
            "gpu": {"target_transitions": 49152, "rotation_mode": "even", "rotation_per_it": 3},
            "tpu": {"target_transitions": 107520, "rotation_mode": "even", "rotation_per_it": 2},
        }
    )
    tpu = profile_for_course(both, "tpu")
    assert tpu is not None and tpu["name"] == "tpu" and tpu["target_transitions"] == 107520
    gpu = profile_for_course(both, "cuda:0")
    assert gpu is not None and gpu["name"] == "gpu"
    # CPU 不选档（notebook 倍数照旧）
    assert profile_for_course(both, "cpu") is None

    tpu_only = _course(
        {"tpu": {"target_transitions": 107520, "rotation_mode": "even", "rotation_per_it": 2}}
    )
    with pytest.raises(ValueError, match="没有 gpu 档"):
        profile_for_course(tpu_only, "cuda")
    assert profile_for_course(None, "tpu") is None


def test_plan_for_profile_scales_target_cap_and_rotation() -> None:
    profile: CloudProfile = {
        "name": "tpu",
        "target_transitions": 30000,
        "rotation": {"mode": "even", "per_it": 2, "pinned": []},
    }
    out = plan_for_profile(_plan(), profile)
    vol = out["volume"]
    assert vol["target_transitions"] == 30000
    assert vol["topup"]["max_games_per_stage"] == 30  # ceil(24 × 30000 / 24576)
    assert out["rotation"] == profile["rotation"]

    # 真正的窗口/配额重解仍走同一份 volume_for_round：even/2 ⇒ 2 关、quota=15000
    block = volume_for_round(out, 1)
    assert block is not None
    assert len(block["stages"]) == 2
    assert block["per_stage_quota"] == 15000
    assert block["target_transitions"] == 30000


def test_plan_for_profile_requires_volume_and_full_est_table() -> None:
    profile: CloudProfile = {
        "name": "tpu",
        "target_transitions": 30000,
        "rotation": {"mode": "even", "per_it": 2, "pinned": []},
    }
    with pytest.raises(ValueError, match="volume 块"):
        plan_for_profile({}, profile)
    plan = _plan()
    plan["volume"].pop("est_s_all")
    with pytest.raises(ValueError, match="est_s_all"):
        plan_for_profile(plan, profile)
