"""`BundleSpec` / `ExportStatusSpec` 的形状守卫（`python.refactor.md` §P0-3，批 G2，2026-11-22）。

批 G2 收掉 `remote/bundle.py` 两个导出面宽签名（各 1 位置 + 9 关键字）：

| 函数 | 现在 | 谁在用 |
|---|---|---|
| `export_bundle` | `BundleSpec` | `trainer/loop_remote_job`（`--export-bundle`）· e2e · hub 离线包/回灌用例 |
| `write_export_status` | `ExportStatusSpec` | `run_export_pack` 的启动/收尾两处 + 状态用例 |

坏点：

* `export_bundle` 的 `code_zip_path` / `ts_code_zip_path` / `opt_tar_path` 是**三条挨着的路径**
  （代码包 / TS 运行时树 / Adam 动量）。互换后包照样打得出来 —— 云机上才以「代码不是这个
  commit」「缺动量 ⇒ 续训静默从零惯性」的形态出现，而这两种都是**静默**的。
* `write_export_status` 的 `iters`（本段目标）与 `run_iters`（整跑累计）互换后状态文件照样
  合法，只是进度静默地多/少算一截；`ok` 与 `running` 是「成功/失败」与「进行中」两个 bool ——
  顺序一错，「进行中」被读成成功（2026-10-21「13:53 只看到上一次 ok:true」那起事故的形态）。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import cast

import pytest

from remote.bundle import BundleSpec, ExportStatusSpec, export_bundle, write_export_status

MISSING = dataclasses.MISSING


def _table(cls: type) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for f in dataclasses.fields(cls):
        if f.default is not MISSING:
            out.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            out.append((f.name, f.default_factory()))
        else:
            out.append((f.name, MISSING))
    return out


#: 旧 `export_bundle` 的 10 个形参（第 1 个是位置）。
BUNDLE_FIELDS: list[tuple[str, object]] = [
    ("out_zip", MISSING),
    ("manifest", MISSING),
    ("plan_bytes", MISSING),
    ("init_weights_path", MISSING),
    ("code_zip_path", MISSING),
    ("job_dir", None),
    ("ts_code_zip_path", None),
    ("opt_tar_path", None),
    ("hub_url", ""),
    ("note", ""),
]

#: 旧 `write_export_status` 的 10 个形参（第 1 个是位置）。
STATUS_FIELDS: list[tuple[str, object]] = [
    ("pack_dir", MISSING),
    ("ok", MISSING),
    ("exit_code", MISSING),
    ("reason", ""),
    ("running", False),
    ("course", ""),
    ("iters", 0),
    ("run_iters", 0),
    ("pid", 0),
    ("pack", ""),
]


def test_field_tables_match_the_old_signatures() -> None:
    assert _table(BundleSpec) == BUNDLE_FIELDS
    assert _table(ExportStatusSpec) == STATUS_FIELDS


@pytest.mark.parametrize("cls", [BundleSpec, ExportStatusSpec])
def test_specs_are_frozen_and_keyword_only(cls: type) -> None:
    rows = dataclasses.fields(cls)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    params = "__dataclass_params__"  # 变量化的名字：直接 getattr(x, "常量") 会撞 B009
    assert getattr(cls, params).frozen
    # 变量化的字段名：直接 `setattr(x, "note", …)` 会撞 ruff B010。
    name = next(f.name for f in rows if f.default is not MISSING)
    dump = export_spec_dummy() if cls is BundleSpec else status_spec_dummy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(dump, name, None)


def export_spec_dummy() -> BundleSpec:
    return BundleSpec(
        out_zip="packs/task.zip",
        manifest={"kind": "run"},
        plan_bytes=b"{}",
        init_weights_path="init.json",
        code_zip_path="code.zip",
    )


def status_spec_dummy() -> ExportStatusSpec:
    return ExportStatusSpec(pack_dir="packs", ok=True, exit_code=0)


def test_entries_take_exactly_one_spec() -> None:
    assert list(inspect.signature(export_bundle).parameters) == ["spec"]
    assert list(inspect.signature(write_export_status).parameters) == ["spec"]


def test_old_keyword_form_is_rejected() -> None:
    export = cast(Callable[..., object], export_bundle)
    with pytest.raises(TypeError):
        export("task.zip", manifest={"kind": "run"})
    status = cast(Callable[..., object], write_export_status)
    with pytest.raises(TypeError):
        status("packs", ok=True, exit_code=0)


def test_adjacent_path_typos_are_type_errors() -> None:
    """三条相邻路径写错名字（`ts_zip` / `code_zip`）⇒ 构造期 TypeError，不落缺省。"""
    make = cast(Callable[..., object], BundleSpec)
    base: dict[str, object] = {
        "out_zip": "packs/task.zip",
        "manifest": {},
        "plan_bytes": b"",
        "init_weights_path": "i.json",
        "code_zip_path": "code.zip",
    }
    with pytest.raises(TypeError):
        make(**base, ts_zip_path="ts_code.zip")
    with pytest.raises(TypeError):
        make(**base, opt_tar="opt.tar")


def test_status_defaults_say_unknown_not_success() -> None:
    """缺省 `running=False` / `ok` 必填：状态文件不能靠缺省表达「成功」。"""
    spec = status_spec_dummy()
    assert spec.running is False
    assert spec.reason == "" and spec.course == "" and spec.pack == ""
    assert (spec.iters, spec.run_iters, spec.pid) == (0, 0, 0)
