"""`BcJobSpec` / `TrainRun` 的形状守卫（`python.refactor.md` §P0-3，批 C，2026-11-22）。

批 C 收的是「BC 链上四个宽入口」——一件活的**执行**（`remote.bc_job._run_bc_job`）、
**发布**（`trainer.bc_loop.publish_bc_job`）、**评估**（`worker.bc_eval.dispatch_bc_eval`）、
**本机训练**（`remote.colab_bc.run_training`）。本文件管前两个（同住 `remote/`）：

| 函数 | 旧形参 | 现在 |
|---|---|---|
| `remote.bc_job._run_bc_job` | 12 关键字 | `BcJobSpec` |
| `remote.colab_bc.run_training` | **10 个位置参数** | `TrainRun` |

`run_training` 是这四个里最要命的一个：旧签名十个参数全是 str/Path/int，`run_dir` 与
`log_path` 互换就是把权重写进日志文件、日志写进权重目录；`total_epochs` 与 `seed` 互换
（int 与 int）只会让这次跑「不是你点的那次」。

判据三件（与 `tests/hub/test_push_spec.py` 同口径）：**字段表**（旧形参逐一对齐，漏抄/
多抄/换序即红）· **冻结 + kw_only**（没有位置歧义）· **入口只收一个对象**。
`BcJobSpec` 为什么住 L1 的 `remote/job_spec` 而不是 `bc_job`：`job_shell`(L4) 要装配它却
不许 import `bc_job`(L4，同秩成环) —— 拓扑面由 `tests/remote/test_job_shell_split.py` 管。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import remote.bc_job as BC
import remote.colab_bc as CB
from remote.job_spec import BcJobSpec

MISSING = dataclasses.MISSING


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


#: `_run_bc_job` 的 12 个形参（旧签名全是关键字；次序 = 语义相邻，不是随意）。
BC_JOB_FIELDS = [
    ("jid", MISSING),
    ("manifest", MISSING),
    ("job_dir", MISSING),
    ("shard_dirs", MISSING),
    ("device", MISSING),
    ("torch_threads", MISSING),
    ("echo", MISSING),
    ("base_url", ""),
    ("token", ""),
    ("lease_token", ""),
    ("backend", "torch"),
    # 延迟解析（不是 `_log`）：默认值在 import 时固化会让 `setattr(bc_job, "_log", …)`
    # 对默认路径失效 —— 与 `RunJobSpec.log` 同规。
    ("log", None),
]

#: `run_training` 的 10 个**位置**形参（旧签名一个缺省都没有）。
TRAIN_RUN_FIELDS = [
    ("py", MISSING),
    ("nn", MISSING),
    ("corpus", MISSING),
    ("run_dir", MISSING),
    ("log_path", MISSING),
    ("state_f", MISSING),
    ("device", MISSING),
    ("total_epochs", MISSING),
    ("seed", MISSING),
    ("tag", MISSING),
]


# ---------------------------------------------------------------- 字段表


@pytest.mark.parametrize(
    ("cls", "want"),
    [(BcJobSpec, BC_JOB_FIELDS), (CB.TrainRun, TRAIN_RUN_FIELDS)],
)
def test_field_table_matches_the_old_signature(cls: type, want: list[tuple[str, object]]) -> None:
    assert _table(cls) == want


_MINIMAL: dict[type, Callable[[], object]] = {
    BcJobSpec: lambda: BcJobSpec(
        jid="j",
        manifest={},
        job_dir=Path("."),
        shard_dirs=[],
        device="cpu",
        torch_threads=0,
        echo=False,
    ),
    CB.TrainRun: lambda: CB.TrainRun(
        py="python",
        nn=Path("."),
        corpus=Path("c"),
        run_dir=Path("r"),
        log_path=Path("l"),
        state_f=Path("s"),
        device="cpu",
        total_epochs=1,
        seed=1,
        tag="t",
    ),
}


@pytest.mark.parametrize("cls", [BcJobSpec, CB.TrainRun])
def test_specs_are_frozen(cls: type) -> None:
    field = dataclasses.fields(cls)[0].name  # 变量而非字面量（ruff B010）
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(_MINIMAL[cls](), field, None)


@pytest.mark.parametrize("cls", [BcJobSpec, CB.TrainRun])
def test_specs_are_keyword_only(cls: type) -> None:
    rows = dataclasses.fields(cls)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]


# ---------------------------------------------------------------- 入口形状


def test_run_bc_job_takes_exactly_one_spec() -> None:
    params = list(inspect.signature(BC._run_bc_job).parameters)
    assert params == ["spec"]


def test_run_training_takes_exactly_one_spec() -> None:
    params = list(inspect.signature(CB.run_training).parameters)
    assert params == ["run"]


def test_old_positional_form_is_rejected() -> None:
    """旧调用面（`run_training(py, nn, corpus, …)` 十个位置）必须响亮地拒。

    冻结 + kw_only 已经封死；这条钉的是**现场表现**（`TypeError`，而不是按序绑定）。
    """
    call = cast(Callable[..., object], CB.TrainRun)
    with pytest.raises(TypeError):
        call("python", Path("."), Path("c"), Path("r"), Path("l"), Path("s"), "cpu", 1, 1, "t")


def test_unknown_field_is_a_type_error() -> None:
    """拼错的旋钮名不许静默落缺省（错名只在运行期是 `TypeError`，mypy 当场拒）。"""
    make = cast(Callable[..., object], BcJobSpec)
    with pytest.raises(TypeError):
        make(jid="j", manifest={}, job_dir=Path("."), shard_dirs=[], devices="cpu")
