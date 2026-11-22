"""`CriticReportSpec` / `AucReportSpec` 的形状守卫（`python.refactor.md` §P0-3，批 H，2026-11-22）。

批 H 是**工具面**（不被生产 import，升级风险最低）：两个 `build_report` 同族同名，各自收一份
值对象。

| 函数 | 旧形参 | 现在 |
|---|---|---|
| `tools/c9_critic_calibration.build_report` | 1 位置 + 9 关键字 | `CriticReportSpec` |
| `tools/p0_auc_probe.build_report` | 2 位置 + 9 关键字 | `AucReportSpec` |

坏点：两处都排着**三个 int 挨着**（`it` / `n_boot` / `boot_seed`；`n_folds` / `boot` / `seed`）——
`boot` 与 `seed` 互换只会让 bootstrap 的数值全变，而报告形状不变：两份「同一语料不同结论」的
json 长得一模一样。`stages_expected` 传错则 `missing_expected_stages` 永远为空（缺关看不出来）。

两个脚本都用 `importlib` 直接装（文件名/目录不是包），与同目录既有用例同一套装载方式。
"""

from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

MISSING = dataclasses.MISSING


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, NN_ROOT / "tools" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


C9 = _load("c9_critic_calibration")
AUC = _load("p0_auc_probe")


def _table(cls: type) -> list[tuple[str, object]]:
    """`(字段名, 缺省值)`；无缺省记 `MISSING`（与 `dataclasses.fields` 同口径）。"""
    out: list[tuple[str, object]] = []
    for f in dataclasses.fields(cls):
        if f.default is not MISSING:
            out.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            out.append((f.name, f.default_factory()))
        else:
            out.append((f.name, MISSING))
    return out


#: `c9_critic_calibration.build_report` 的 10 个形参。
CRITIC_FIELDS: list[tuple[str, object]] = [
    ("games", MISSING),
    ("course_name", MISSING),
    ("it", MISSING),
    ("gamma", MISSING),
    ("reward_identity", MISSING),
    ("stages_expected", MISSING),
    ("n_boot", MISSING),
    ("boot_seed", MISSING),
    ("value_source", "value.npy"),
    ("value_weights_sha16", ""),
]

#: `p0_auc_probe.build_report` 的 11 个形参（前 2 个旧签名是位置）。
AUC_FIELDS: list[tuple[str, object]] = [
    ("games", MISSING),
    ("folds", MISSING),
    ("shards_root", MISSING),
    ("n_shards_total", MISSING),
    ("weights_path", MISSING),
    ("pf_dim", MISSING),
    ("ablate", MISSING),
    ("neg_frac", MISSING),
    ("n_folds", MISSING),
    ("boot", MISSING),
    ("seed", MISSING),
]


def test_field_tables_match_the_old_signatures() -> None:
    assert _table(C9.CriticReportSpec) == CRITIC_FIELDS
    assert _table(AUC.AucReportSpec) == AUC_FIELDS


@pytest.mark.parametrize("owner", [C9, AUC])
def test_specs_are_frozen_and_keyword_only(owner: Any) -> None:
    cls = owner.CriticReportSpec if owner is C9 else owner.AucReportSpec
    rows = dataclasses.fields(cls)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    # 冻结核对只看「赋值即抛」：两个 spec 的必填字段都要真造得出来（下面那条覆盖）。
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec = _minimal_critic() if owner is C9 else _minimal_auc()
        setattr(spec, rows[0].name, None)


def _minimal_critic() -> Any:
    return C9.CriticReportSpec(
        games=[],
        course_name="z10-maze",
        it=1,
        gamma=0.998,
        reward_identity="x",
        stages_expected=(16,),
        n_boot=1,
        boot_seed=1,
    )


def _minimal_auc() -> Any:
    import numpy as np

    return AUC.AucReportSpec(
        games=[],
        folds=np.zeros((0, 0)),
        shards_root="tmp",
        n_shards_total=0,
        weights_path="w.json",
        pf_dim=1,
        ablate=slice(0, 1),
        neg_frac=0.0,
        n_folds=2,
        boot=1,
        seed=1,
    )


def test_both_entries_take_exactly_one_spec() -> None:
    assert list(inspect.signature(C9.build_report).parameters) == ["spec"]
    assert list(inspect.signature(AUC.build_report).parameters) == ["spec"]


def test_old_keyword_form_is_rejected() -> None:
    """旧调用面（`build_report(games, course_name=…)`）必须响亮地拒。"""
    critic = cast(Callable[..., object], C9.build_report)
    with pytest.raises(TypeError):
        critic([], course_name="z10-maze", it=1)
    auc = cast(Callable[..., object], AUC.build_report)
    with pytest.raises(TypeError):
        auc([], None, shards_root="tmp")


def test_unknown_field_is_a_type_error() -> None:
    """拼错的旋钮名不许静默落缺省（`boot_seed` 写成 `seed` 会让 CI 换一颗种子）。"""
    make = cast(Callable[..., object], C9.CriticReportSpec)
    with pytest.raises(TypeError):
        make(games=[], course_name="z10-maze", it=1, seed=7)
