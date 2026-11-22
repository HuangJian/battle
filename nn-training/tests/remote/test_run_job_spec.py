"""`remote/job_spec.RunJobSpec` 的形状护栏（`python.refactor.md` §P0-3）。

`run_job` 原来是 3 个身份位 + 14 个按名旋钮，而这组实参在「宿主 → 注入点」之间手抄了
两遍（`job_round` 只见 `run_job_fn`，它不能 import `remote.worker` 的 torch 链）。关键字
拼错不报错，只是静默落缺省：`device` 落成 `cpu` = 云机白拿 GPU、`epoch_mult` 落成 1.0 =
训练侧放大失效、`preloaded` 落丢 = 全离线节点回头向空 `base_url` 发 GET、
`should_cancel` / `on_ppo_start` 落丢 = 取消帧与 `computing_at` 打点双双静默。

这里钉住：spec 是 frozen + kw_only · 17 个字段与缺省逐项对齐 · `run_job` 只收一份 spec ·
缺省日志出口**延迟解析**（`log=None` ⇒ 宿主 `_log`，patch 仍生效）。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest

import remote.worker as W
from remote.job_spec import RunJobSpec

#: `(字段名, 缺省值)` —— 与收进 spec 之前的 17 个参数逐一对齐（前 5 个无缺省）。
EXPECTED_FIELDS = [
    ("base_url", dataclasses.MISSING),
    ("token", dataclasses.MISSING),
    ("job", dataclasses.MISSING),
    ("work_dir", dataclasses.MISSING),
    ("device", "cpu"),
    ("torch_threads", 0),
    ("backend", ""),
    ("epoch_mult", 1.0),
    ("echo", False),
    ("preloaded", None),
    ("code_cache_dir", None),
    ("ts_code_cache_dir", None),
    ("lease_token", ""),
    ("prep", None),
    ("should_cancel", None),
    ("on_ppo_start", None),
    ("log", None),
]


def _minimal() -> RunJobSpec:
    return RunJobSpec(base_url="http://hub", token="t", job={"job_id": "j"}, work_dir=Path("tmp"))


def test_spec_is_frozen_and_keyword_only() -> None:
    assert dataclasses.is_dataclass(RunJobSpec)
    spec = _minimal()
    field = sorted(f.name for f in dataclasses.fields(RunJobSpec))[0]  # 变量而非字面量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(RunJobSpec)), (
        "`RunJobSpec` 不是 kw_only ⇒ 17 个参数只是换了个地方写"
    )


def test_spec_field_table() -> None:
    rows = [(f.name, f.default) for f in dataclasses.fields(RunJobSpec)]
    assert [name for name, _ in rows] == [name for name, _ in EXPECTED_FIELDS]
    for (name, default), (_, expected) in zip(rows, EXPECTED_FIELDS, strict=True):
        assert default == expected, name


def test_defaults_keep_the_old_semantics() -> None:
    """缺省不只是「能构造」：`device` 仍是 cpu、`backend=""` 仍是 auto、注入点缺省关。"""
    spec = _minimal()
    assert spec.device == "cpu" and spec.backend == "" and spec.epoch_mult == 1.0
    assert spec.should_cancel is None and spec.on_ppo_start is None and spec.preloaded is None


def test_run_job_takes_only_the_spec() -> None:
    params = list(inspect.signature(W.run_job).parameters.values())
    assert [p.name for p in params] == ["spec"]
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_unknown_knob_is_a_type_error() -> None:
    """旧形状的静默缺省 ⇒ 现在构造 spec 当场炸（`epoch_mul` 漏个 t 不再训一轮 1.0）。"""
    builder: Callable[..., RunJobSpec] = RunJobSpec
    with pytest.raises(TypeError, match="epoch_mul"):
        builder(
            base_url="http://hub",
            token="t",
            job={},
            work_dir=Path("tmp"),
            epoch_mul=2.0,
        )
