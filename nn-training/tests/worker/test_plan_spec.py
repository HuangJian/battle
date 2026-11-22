"""`worker/plan.PlanSpec` 的形状护栏（`python.refactor.md` §P0-3）。

`build_plan` 原来是 1 个位置位（`args`）+ 13 个关键字，而且全部由 hub 侧导出时手写。
关键字漏写不报错，先落缺省，再被下面的自检掩护过去 —— 自检比的是**同一份计划**，
所以它抓不住「计划本身就是按错的旋钮组装的」：

  * `max_iters` / `max_segment_iters` 丢 = 计划覆盖整段课程（后者是硬上界的再降档）；
  * `volume` / `rotation` 丢 = 静默退成「固定局数」老口径（云端采样量与本地集群不一致）；
  * `template_wver` / `node_label` 丢 = 模板 argv 指向错的权重版本名 / 错节点标签。

这里钉住：spec 是 frozen + kw_only · 14 个字段与缺省逐项对齐旧形参 · `build_plan` 只收
一份 spec。计划**语义**（区间钳制 / 开区间段 / 轮换）仍在 `tests/worker/test_plan.py`。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import trainer.loop_export as lx
from worker.plan import PlanSpec, build_plan

MISSING = dataclasses.MISSING

#: `(字段名, 缺省值)` —— 与收进 spec 之前的 14 个形参逐一对齐（前 4 个无缺省）。
EXPECTED_FIELDS = [
    ("args", MISSING),
    ("it", MISSING),
    ("iters_total", MISSING),
    ("rotate_seed", MISSING),
    ("max_iters", 0),
    ("workers", 0),
    ("game_timeout_sec", 0.0),
    ("budget_sec", 0.0),
    ("node_label", "run"),  # `RUN_NODE_LABEL` 的实际值
    ("template_wver", ""),
    ("volume", None),
    ("max_segment_iters", 0),
    ("rotation", None),
    ("log", None),  # 函数缺省：下面单独比对「可调用 + 静默」
]


def _args(**over: object) -> SimpleNamespace:
    base = dict(
        curriculum_stages="0-5",
        curriculum_start=1,
        curriculum_every=3,
        curriculum_grow=1,
        seeds_per_stage=2,
        rotate_stages=0,
        seed_rotate=0,
        stages=[0],
        seeds=[1],
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_spec_is_frozen_and_fully_keyword_only() -> None:
    assert dataclasses.is_dataclass(PlanSpec)
    rows = dataclasses.fields(PlanSpec)
    field = rows[0].name  # 变量而非字面量（ruff B010）
    spec = PlanSpec(args=_args(), it=1, iters_total=2, rotate_seed=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]


def test_field_table_matches_the_old_signature() -> None:
    """字段名 / 顺序 / 缺省逐项等价（`log` 的函数缺省单独兜）。"""
    got: list[tuple[str, object]] = []
    for f in dataclasses.fields(PlanSpec):
        if f.default is not MISSING:
            if f.name == "log":
                assert callable(f.default)
                f.default("")  # no-op：不许抛
                got.append((f.name, None))
            else:
                got.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            got.append((f.name, f.default_factory()))
        else:
            got.append((f.name, MISSING))
    assert got == EXPECTED_FIELDS


def test_build_plan_takes_exactly_one_spec() -> None:
    params = inspect.signature(build_plan).parameters
    assert [p.name for p in params.values()] == ["spec"]
    assert all(p.default is inspect.Parameter.empty for p in params.values())


def test_minimal_spec_is_constructible() -> None:
    """只给 4 个必填字段就能构造（工具/冒烟面依赖这个缺省）。

    计划**语义**（`workers` 0 钳 1 / 区间钳制 / 开区间段）在 `tests/worker/test_plan.py`，
    这里只管「缺省把 10 个旋钮填成与旧形参同值的对象」。
    """
    spec = PlanSpec(args=_args(), it=1, iters_total=3, rotate_seed=0)
    assert spec.workers == 0 and spec.node_label == "run"
    assert spec.game_timeout_sec == 0.0 and spec.budget_sec == 0.0
    assert spec.volume is None and spec.rotation is None


def test_unknown_field_is_a_type_error() -> None:
    """拼错的字段名不许静默落缺省（旧形参下多余关键字也是 `TypeError`）。"""
    make: Callable[..., PlanSpec] = PlanSpec
    with pytest.raises(TypeError):
        make(args=_args(), it=1, iters_total=2, rotate_seed=0, max_iter=5)


def test_production_call_site_passes_every_field_by_name() -> None:
    """★ 生产侧唯一的调用点（`trainer/loop_export.py`）：必须**逐个按名**交齐，不许位置传。

    这是本刀最可能的失误形态：13 个关键字手抄进 `PlanSpec(...)` 时漏一个 —— 运行时不会报错，
    只是计划少了那个旋钮。用真调用 + 一个列出全部字段的假 `PlanSpec` 做不到（参数对象是
    `worker.plan` 的真类），所以改成「AST 看关键字集合 == 字段集合」。
    """
    import ast

    src = Path(lx.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "PlanSpec"
    ]
    assert len(calls) == 1, f"`PlanSpec(...)` 出现 {len(calls)} 次（应恰好 1 次）"
    got = {k.arg for k in calls[0].keywords if k.arg is not None}
    want = {f.name for f in dataclasses.fields(PlanSpec)}
    # 生产调用点故意落缺省的两项：`node_label`（常量 `RUN_NODE_LABEL`）与 `template_wver`
    # （空串 = 模板 argv 不写 wver 段）。其余字段必须**逐个按名**交齐。
    rely_on_default = {"node_label", "template_wver"}
    assert got == want - rely_on_default, (
        f"漏交 {sorted(want - rely_on_default - got)} / 多交 {sorted(got - want)}"
    )
    assert not calls[0].args, "生产调用点用位置实参构造 spec（kw_only 就是为了不许这样）"
