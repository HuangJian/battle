"""tests/remote/test_plan_run_cloud_sampling.py —— 云端采样覆盖在**执行面**的接线。

2026-11-15 用户更正 ②：块生效时 `CFG.sample_mult` / `CFG.epoch_mult` 不再生效。本文件钉：

  ① 选档成功 ⇒ 两个倍数被归 1 + 一行「忽略」日志；缓存后不重复解析/刷屏；
  ② 块里缺当前设备档 ⇒ 首轮前 `ProtocolError`（不静默回落）；
  ③ 账本搬运：`sampling_profile` 进产物行 → 课程账本事件（旧行无此键）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.course_spec import CourseConfig
from common.protocol import ProtocolError
from remote.artifacts import ledger_row_from_metrics
from remote.plan_run import _row_extra, _sampling_profile

BASE: dict = {
    "name": "cloud-probe",
    "mode": "per-tick",
    "stages": "2000-2002",
    "target_transitions": 24576,
    "rotation_mode": "even",
    "rotation_per_it": 3,
}
TPU = {"target_transitions": 107520, "rotation_mode": "even", "rotation_per_it": 2}


def _course(block: dict) -> CourseConfig:
    return CourseConfig.model_validate({**BASE, "autonomous_cloud_overrides": block})


def _ctx(course, device: str, **kw):
    logs: list[str] = []
    ctx = SimpleNamespace(
        course=course,
        device=device,
        sample_mult=kw.get("sample_mult", 1.0),
        epoch_mult=kw.get("epoch_mult", 1.0),
        sampling_profile=None,
        sampling_profile_checked=False,
        log=logs.append,
    )
    return ctx, logs


def test_profile_activation_zeroes_notebook_multipliers_and_logs() -> None:
    ctx, logs = _ctx(_course({"tpu": TPU}), "tpu", sample_mult=2.0, epoch_mult=1.5)
    profile = _sampling_profile(ctx)
    assert profile is not None and profile["name"] == "tpu"
    assert ctx.sample_mult == 1.0 and ctx.epoch_mult == 1.0
    assert len(logs) == 1  # 交付物 = 恰好一行「已忽略」读数


def test_profile_is_resolved_once() -> None:
    ctx, logs = _ctx(_course({"tpu": TPU}), "xla:0")
    _sampling_profile(ctx)
    n = len(logs)
    assert _sampling_profile(ctx) is not None
    assert len(logs) == n  # 缓存命中：不重复解析/打日志


def test_missing_device_tier_is_loud_before_the_first_round() -> None:
    ctx, _ = _ctx(_course({"tpu": TPU}), "cuda:0")
    with pytest.raises(ProtocolError, match="没有 gpu 档"):
        _sampling_profile(ctx)
    # CPU 不选档（块未激活；倍数照旧由 notebook 决定）
    cpu_ctx, _ = _ctx(_course({"tpu": TPU}), "cpu", sample_mult=2.0)
    assert _sampling_profile(cpu_ctx) is None
    assert cpu_ctx.sample_mult == 2.0


def test_sampling_profile_moves_into_the_ledger() -> None:
    profile = {
        "name": "tpu",
        "target_transitions": 107520,
        "rotation": {"mode": "even", "per_it": 2, "pinned": []},
    }
    extra = _row_extra({"sampling_profile": profile}, "train")
    assert extra == {"sampling_profile": profile}
    assert _row_extra({"sampling_profile": {}}, "train") is None  # 空形状不写键

    ev = ledger_row_from_metrics(
        {"it": 7, "report": {"winRate": 0.5}, "agg": {}, "sampling_profile": profile},
        run_id="r1",
        source="offline_backfeed",
    )
    assert ev is not None and ev["sampling_profile"] == profile
    old = ledger_row_from_metrics({"it": 7, "report": {}, "agg": {}}, run_id="r1", source="x")
    assert old is not None and old.get("sampling_profile") is None
