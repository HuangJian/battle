"""tests/biz/test_autonomous_cloud_overrides.py —— 云端采样覆盖块的**课程侧契约**。

2026-11-15 用户更正：同一门课要在 GPU / TPU 云机交替训练 ⇒ 块按设备分档；每档自包含
（target + 窗口），缺一在加载期拒。本文件钉的是**加载期**面：

  ① 形状：只认 gpu/tpu、每档必填 target>0 与 rotation_mode、even 要求 per_it≥1；
  ② 课程前置：块在场 ⇒ per-tick + 顶层 target>0 + 顶层 rotation 非空（hub 基线必须能导出
     `est_s_all`）；
  ③ **不进** flat_overrides / corpus_identity_fp（它是执行变体，不是语料身份）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.corpus_fp import corpus_identity_fp
from biz.course_spec import CourseConfig

BASE: dict = {
    "name": "cloud-probe",
    "mode": "per-tick",
    "stages": "0-34",
    "target_transitions": 24576,
    "rotation_mode": "even",
    "rotation_per_it": 7,
}


def _course(**kw) -> CourseConfig:
    # model_validate：嵌套 dict 由 pydantic 转模型，静态类型也过（field 类型不收裸 dict）。
    return CourseConfig.model_validate({**BASE, **kw})


def _gpu(**kw):
    return {"target_transitions": 49152, "rotation_mode": "even", "rotation_per_it": 7, **kw}


def _tpu(**kw):
    return {"target_transitions": 107520, "rotation_mode": "even", "rotation_per_it": 35, **kw}


def test_two_device_profiles_parse_and_stay_self_contained() -> None:
    c = _course(autonomous_cloud_overrides={"gpu": _gpu(), "tpu": _tpu()})
    block = c.autonomous_cloud_overrides
    assert block is not None
    assert block.gpu is not None and block.gpu.target_transitions == 49152
    assert block.tpu is not None and block.tpu.rotation_per_it == 35


def test_empty_block_and_unknown_device_keys_are_loud() -> None:
    with pytest.raises(ValueError, match="不能是空块"):
        _course(autonomous_cloud_overrides={})
    with pytest.raises(ValueError):
        _course(autonomous_cloud_overrides={"cpu": _gpu()})
    with pytest.raises(ValueError):
        _course(autonomous_cloud_overrides={"gpu": _gpu(), "tpu": _tpu(), "extra": {}})


@pytest.mark.parametrize(
    "bad",
    [
        {"rotation_mode": "even", "rotation_per_it": 7},  # 缺 target
        {"target_transitions": 0, "rotation_mode": "even", "rotation_per_it": 7},
        {"target_transitions": True, "rotation_mode": "even", "rotation_per_it": 7},
        {"target_transitions": -1, "rotation_mode": "even", "rotation_per_it": 7},
        {"target_transitions": 100, "rotation_mode": "", "rotation_per_it": 1},
        {"target_transitions": 100, "rotation_mode": "even", "rotation_per_it": 0},
        {"target_transitions": 100, "rotation_mode": "pinned", "rotation_per_it": -1},
    ],
)
def test_bad_profiles_are_rejected(bad) -> None:
    with pytest.raises(ValueError):
        _course(autonomous_cloud_overrides={"tpu": bad})


def test_pinned_profile_allows_zero_and_keeps_the_spec_string() -> None:
    c = _course(
        autonomous_cloud_overrides={
            "tpu": {
                "target_transitions": 107520,
                "rotation_mode": "pinned",
                "rotation_per_it": 0,
                "rotation_pinned": "2033-2034",
            }
        }
    )
    block = c.autonomous_cloud_overrides
    assert block is not None and block.tpu is not None
    assert block.tpu.rotation_pinned == "2033-2034"


def test_course_needs_a_hub_baseline_plan() -> None:
    # 没有顶层 target ⇒ 导不出 volume/est_s_all，云端无从重解
    # 顶层 rotation 的既有校验会先响（同一个前置）；只要求「缺基线就拒」，不钉哪一条。
    with pytest.raises(ValueError, match="target_transitions > 0"):
        _course(target_transitions=0, autonomous_cloud_overrides={"tpu": _tpu()})
    # 没有顶层 rotation ⇒ 导出的计划没有 est_s_all
    with pytest.raises(ValueError, match="rotation_mode 非空"):
        _course(rotation_mode="", rotation_per_it=0, autonomous_cloud_overrides={"tpu": _tpu()})
    # 非 per-tick
    with pytest.raises(ValueError, match="per-tick"):
        _course(mode="intent", autonomous_cloud_overrides={"tpu": _tpu()})


def test_block_is_not_an_arg_override_and_not_in_corpus_identity() -> None:
    plain = _course()
    with_block = _course(autonomous_cloud_overrides={"gpu": _gpu(), "tpu": _tpu()})
    assert with_block.flat_overrides() == plain.flat_overrides()
    # 块只是执行面写法：同一份顶层基线 ⇒ 同一语料身份（course_fp 文件字节另算）
    assert corpus_identity_fp(with_block) == corpus_identity_fp(plain)
