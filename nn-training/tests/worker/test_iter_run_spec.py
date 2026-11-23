"""`IterRunSpec`：`run_iter_rollout` 的 9 个参数收成一个对象（`python.refactor.md` §P0-3 批 J）。

判据四条：

1. 入口就是 `run_iter_rollout(spec: IterRunSpec)` —— 没有第二个参数，也没有 `**kwargs` 后门；
2. 字段表是闭集（9 个名字，与旧签名逐字同名）且 `channel` 之外的缺省值不变
   （`ts_dir=None` / `it=0` / 三个回调 `None`）；`log` **不再有缺省** —— 57 处调用点全部显式传，
   留着缺省只会让「谁在打这轮日志」说不清；
3. `frozen` + `kw_only`：位置面不许泄露 —— `job_dir` 与 `spec` 是相邻的 `str|Path` / `dict`，
   `run_iter_rollout(d, spec)` 这种写法一旦能拼出来，互换就只是「跑进错目录」；
4. `IterRun.from_spec` 是唯一一次搬运，九个名字逐位对账（多抄/少抄一格都要被抓住）。
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, cast

import pytest

from worker.iter_rollout import run_iter_rollout
from worker.iter_run import IterRun, IterRunSpec

#: 9 个字段的闭集（顺序 = 旧签名顺序；全仓唯一的字段表）。
FIELDS = (
    "job_dir",
    "spec",
    "ts_dir",
    "log",
    "ctx",
    "it",
    "next_game",
    "on_game_settled",
    "volume_line",
)


def _spec(**over: Any) -> IterRunSpec:
    calls: list[str] = []
    base: dict[str, Any] = {
        "job_dir": "/tmp/job",
        "spec": {"argv": ["--stage", "1"]},
        "log": calls.append,
    }
    base.update(over)
    return IterRunSpec(**base)


# ────────────────────────────── 入口形状


def test_entry_takes_exactly_one_spec() -> None:
    params = inspect.signature(run_iter_rollout).parameters
    assert list(params) == ["spec"], list(params)
    assert all(p.kind is not inspect.Parameter.VAR_KEYWORD for p in params.values())
    assert all(p.kind is not inspect.Parameter.VAR_POSITIONAL for p in params.values())


def test_field_table_is_closed_and_ordered() -> None:
    assert tuple(f.name for f in dataclasses.fields(IterRunSpec)) == FIELDS


def test_spec_is_keyword_only_and_frozen() -> None:
    params = inspect.signature(IterRunSpec).parameters
    assert {p.kind for p in params.values()} == {inspect.Parameter.KEYWORD_ONLY}
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.__setattr__("it", 7)


def test_log_has_no_default_and_others_do() -> None:
    """`log` 必须显式给（57 处调用点本来就全部传）；其余旋钮沿用旧缺省值。"""
    params = inspect.signature(IterRunSpec).parameters
    assert params["log"].default is inspect.Parameter.empty
    assert params["ts_dir"].default is None
    assert params["ctx"].default is None
    assert params["it"].default == 0
    assert params["next_game"].default is None
    assert params["on_game_settled"].default is None
    assert params["volume_line"].default is None


# ────────────────────────────── 搬运对账


def test_from_spec_copies_all_nine_names() -> None:
    """`IterRun.from_spec` 的九个名字逐位对账（互换/漏抄在这里红，而不是在 torch 里红）。"""
    calls: list[str] = []
    settled: list[tuple[list[str], int]] = []

    def next_game() -> list[str]:
        return ["--stage", "2"]

    def on_settled(argv: list[str], n: int) -> None:
        settled.append((argv, n))

    def volume() -> str:
        return "vol"

    ctx = cast(Any, object())
    log = calls.append
    spec = IterRunSpec(
        job_dir="/tmp/job-9",
        spec={"argv": ["--stage", "9"]},
        ts_dir="/tmp/ts-9",
        log=log,
        ctx=ctx,
        it=9,
        next_game=next_game,
        on_game_settled=on_settled,
        volume_line=volume,
    )
    run = IterRun.from_spec(spec)

    assert run.job_dir == "/tmp/job-9"
    assert run.spec == {"argv": ["--stage", "9"]}
    assert run.ts_dir == "/tmp/ts-9"
    assert run.log == log
    assert run.ctx is ctx
    assert run.it == 9
    assert run.next_game is next_game
    assert run.on_game_settled is on_settled
    assert run.volume_line is volume
    # 批级可变状态仍是新造的（不是从输入对象借来的）
    assert run.game_secs == {} and run.argvs == [] and run.pool is None


def test_from_spec_does_not_share_mutable_state_between_runs() -> None:
    """同一份 spec 开两轮：两轮的批级容器互不相干（原先每轮各自 `IterRun(...)` 的语义不变）。"""
    spec = _spec()
    a = IterRun.from_spec(spec)
    b = IterRun.from_spec(spec)
    a.game_secs[0] = 1.5
    a.argvs.append(["x"])
    assert b.game_secs == {} and b.argvs == []
    assert a.spec is b.spec  # 输入字典是按引用传的（老行为：只读）
