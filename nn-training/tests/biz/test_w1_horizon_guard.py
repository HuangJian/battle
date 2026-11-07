"""C5 视界防呆：`w1-*` 课程必须显式写 gamma/lam/decision_k（plan §5 C5）。

当前还没有 `w1-*` 课程；本用例对将来的文件生效。同时钉住当前 shard 口径：
metrics 没有 `dt` 列，per-tick 调用 `compute_gae` 不传 `dt`（默认 None）⇒ gamma 是
**每决策步**，不是每拍；漏写课程键会回落到 `GAMMA=0.995`（视界 18.3 步 / 183 拍）。
"""

from __future__ import annotations

import inspect
import math
import re
from pathlib import Path
from typing import Any

from biz.course_resolve import load_course
from biz.reward_library import METRIC_INDEX
from worker.ppo.np_core import compute_gae

NN_ROOT = Path(__file__).resolve().parents[2]
CURRICULA = NN_ROOT / "curricula"


def _horizon(course: Any) -> tuple[float, float]:
    gamma = float(course.gamma)
    lam = float(course.lam)
    k = int(course.decision_k)
    eff = gamma * lam
    return 1.0 / (1.0 - eff), math.log(0.5) / math.log(eff) * k


def test_metric_schema_has_no_dt_and_gae_defaults_to_fixed_step() -> None:
    assert "dt" not in METRIC_INDEX
    assert inspect.signature(compute_gae).parameters["dt"].default is None


def test_reference_z11_null_horizon_is_193_ticks() -> None:
    course = load_course(str(CURRICULA / "z11-null.jsonc"))
    horizon_steps, half_ticks = _horizon(course)
    assert abs(horizon_steps - 19.27) < 0.02
    assert round(horizon_steps * int(course.decision_k)) == 193
    assert abs(half_ticks - 130.06) < 0.1


def test_w1_courses_explicitly_set_horizon_keys() -> None:
    files = sorted(CURRICULA.glob("w1-*.jsonc"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        for key in ("gamma", "lam", "decision_k"):
            assert re.search(rf'^\s*"{key}"\s*:', text, re.MULTILINE), (
                f"{path.name} 缺显式 {key}；漏写会静默用 GAMMA=0.995"
            )
        course = load_course(str(path))
        assert 0.0 < float(course.gamma) < 1.0
        assert 0.0 < float(course.lam) < 1.0
        assert int(course.decision_k) >= 1
