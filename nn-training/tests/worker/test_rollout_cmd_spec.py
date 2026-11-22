"""`RolloutCmdSpec` 的形状守卫（`python.refactor.md` §P0-3，批 D2b，2026-11-22）。

批 D2b：`worker/cmd.build_rollout_cmd`(2 位置 + 8 关键字 → 1 个 `RolloutCmdSpec`)。
调用面 15 处（`queue_local` 3 · `plan` 1 · `iter_job` 1 · 用例 10）。

坏点：`stage` / `seed` 是两个相邻 int，互换后命令里 `--stages` 拿到种子号、`--seeds` 拿到关卡号
—— 导出器只会报「这组对局没有可用关卡」，或者更坏：产出一批「关卡对、种子错」的轨迹而没人察觉
（同一批对局会重放成另一份随机序列）。`weights` / `out_dir` 是两个相邻路径 str：互换后 `--out`
指进权重文件。`wver` / `node_label` / `code_sha` 三个身份串相邻：节点互斥与血缘归属静默错位。

除形状外钉住三件事：`--stages`/`--seeds` 各自吃到对的那个数、`node_side=True` 时不带
`--code-sha`（本机腿独有）、缺省 `wver`/`node_label`/`code_sha` 为空。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest

from worker.cmd import RolloutCmdSpec, build_rollout_cmd

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


#: 旧 `build_rollout_cmd` 的 10 个形参（前 2 个位置）。
FIELDS: list[tuple[str, object]] = [
    ("bun", MISSING),
    ("args", MISSING),
    ("weights", MISSING),
    ("out_dir", MISSING),
    ("stage", MISSING),
    ("seed", MISSING),
    ("wver", ""),
    ("node_label", ""),
    ("node_side", False),
    ("code_sha", ""),
]


def _args() -> Any:
    """最小 `args`：rl 模式（`export-rl-rollout.ts`）+ replay 语料（本机腿才带 `--code-sha`）。"""
    return SimpleNamespace(
        goal_rollout=False,
        intent_rollout=False,
        max_ticks=1800,
        difficulty="classic",
        heartbeat=240,
        corpus_fmt="replay",
        course_obj=None,
    )


def _cmd(**over: object) -> list[str]:
    base: dict[str, object] = {
        "bun": "bun",
        "args": _args(),
        "weights": "w.json",
        "out_dir": "w0",
        "stage": 16,
        "seed": 7,
        "wver": "abc",
        "node_label": "local",
    }
    base.update(over)
    return build_rollout_cmd(RolloutCmdSpec(**cast(dict[str, Any], base)))


def test_field_table_matches_the_old_signature() -> None:
    assert _table(RolloutCmdSpec) == FIELDS


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(RolloutCmdSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = RolloutCmdSpec(
        bun="bun", args=_args(), weights="w.json", out_dir="w0", stage=16, seed=7
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, rows[-1].name, "x")


def test_entry_takes_exactly_one_spec() -> None:
    assert list(inspect.signature(build_rollout_cmd).parameters) == ["spec"]


def test_old_positional_form_is_rejected() -> None:
    # 走 Callable 视图：旧面在类型层就不存在，运行时必须响亮地 TypeError。
    call: Callable[..., object] = build_rollout_cmd
    with pytest.raises(TypeError):
        call("bun", _args(), weights="w.json")


def test_stage_and_seed_land_in_their_own_flags() -> None:
    """`--stages` 吃 stage、`--seeds` 吃 seed（互换是静默事故）。"""
    cmd = _cmd(stage=22, seed=3)
    assert cmd[cmd.index("--stages") + 1] == "22"
    assert cmd[cmd.index("--seeds") + 1] == "3"


def test_node_side_drops_local_only_code_sha() -> None:
    """本机腿才带 `--code-sha`（节点用自己的 bun 与快照）—— 集合判据，不挂措辞。"""
    local = _cmd()
    node = _cmd(node_side=True, node_label="iter")
    assert not {"--code-sha"}.isdisjoint(local)
    assert {"--code-sha"}.isdisjoint(node)


def test_defaults_keep_empty_identities() -> None:
    spec = RolloutCmdSpec(
        bun="bun", args=_args(), weights="w.json", out_dir="w0", stage=16, seed=7
    )
    assert (spec.wver, spec.node_label, spec.code_sha) == ("", "", "")
    assert spec.node_side is False
