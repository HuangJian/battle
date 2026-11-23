"""三份落位/合并 spec：`LandSpec` / `BcLandSpec` / `ShardMergeSpec`（`python.refactor.md` §P0-3 批 K）。

判据三条：

1. 三个入口都只吃一份对象（`verify_and_land(ask)` / `verify_and_land_bc(ask)` /
   `merge_memory_shards(ask)`），没有位置面、也没有 `**kwargs` 后门；
2. 字段表是闭集，且全 `KEYWORD_ONLY` —— PPO 与 BC 的 `result` / `manifest` 是两份**同型 dict**，
   原先排在位置口上：互换不抛，只会让三重校验全部失配后报「云端跑错了 job」；
3. `merge_memory_shards` 的行为不变：`cands`（已收集）与 `mem`（回传账）按**名字**各自就位 ——
   顺序反了就不再补任何 shard（这条差异是这条 spec 存在的全部理由）。
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from remote.hub_client import verify_and_land, verify_and_land_bc
from remote.publish_spec import BcLandSpec, LandSpec
from worker.resume import ShardMergeSpec, merge_memory_shards

LAND_FIELDS = (
    "result",
    "manifest",
    "init_weights_path",
    "traj_dir",
    "it",
    "out_weights",
    "state_init",
    "mem",
    "log",
)
BC_LAND_FIELDS = (
    "result",
    "manifest",
    "traj_dir",
    "it",
    "out_weights",
    "round_name",
    "data_root",
    "fixed_source",
    "log",
)
MERGE_FIELDS = (
    "it_dir",
    "cands",
    "mem",
    "counters",
    "mver_bad",
    "course_fp",
    "corpus_fp",
    "state_init",
    "metrics_version",
)


# ────────────────────────────── 入口形状


def test_entries_take_exactly_one_spec() -> None:
    for func in (verify_and_land, verify_and_land_bc, merge_memory_shards):
        params = inspect.signature(func).parameters
        assert list(params) == ["ask"], (func.__name__, list(params))
        assert all(p.kind is not inspect.Parameter.VAR_KEYWORD for p in params.values())


def test_field_tables_are_closed() -> None:
    assert tuple(f.name for f in dataclasses.fields(LandSpec)) == LAND_FIELDS
    assert tuple(f.name for f in dataclasses.fields(BcLandSpec)) == BC_LAND_FIELDS
    assert tuple(f.name for f in dataclasses.fields(ShardMergeSpec)) == MERGE_FIELDS


def test_specs_are_keyword_only_and_frozen() -> None:
    """位置面就是泄露面：三个 spec 一律 kw-only + frozen。"""
    for cls in (LandSpec, BcLandSpec, ShardMergeSpec):
        params = inspect.signature(cls).parameters
        assert {p.kind for p in params.values()} == {inspect.Parameter.KEYWORD_ONLY}, cls
        assert len(dataclasses.fields(cls)) == len(params), cls


def test_ppo_and_bc_specs_do_not_share_a_shape() -> None:
    """同名不同形：BC 没有 init-weights fencing / `mem`，PPO 没有 `round_name` 那三件。"""
    land = {f.name for f in dataclasses.fields(LandSpec)}
    bc = {f.name for f in dataclasses.fields(BcLandSpec)}
    assert {"init_weights_path", "state_init", "mem"} <= land
    assert not {"init_weights_path", "state_init", "mem"} & bc
    assert {"round_name", "data_root", "fixed_source"} <= bc
    assert not {"round_name", "data_root", "fixed_source"} & land


# ────────────────────────────── `cands` / `mem` 各就各位


def _manifest(name: str, **extra: object) -> dict:
    m: dict = {"name": name, "metrics_version": 1}
    m.update(extra)
    return m


def test_merge_adds_memory_shards_under_the_right_names() -> None:
    it_dir = Path("/tmp/it1")
    cands: dict[str, list[Path]] = {}
    mem = {"a": _manifest("a")}
    counters = (0, 0, 0, "", "")

    out = merge_memory_shards(
        ShardMergeSpec(it_dir=it_dir, cands=cands, mem=mem, counters=counters, mver_bad={})
    )

    assert out == counters, "一个都没被过滤 ⇒ 计数原样返回"
    assert list(cands) == ["a"], "回传的 shard 应当并进 cands"
    assert cands["a"][0].name == "a"
    placeholder = cands["a"][0]
    assert placeholder.parent.name == "@mem", "占位路径的节点名固定（永不创建）"
    assert placeholder.parent.parent.parent == it_dir, "挂在 it{it}/dist/@mem 下（零落盘）"


def test_merge_keeps_disk_side_when_names_collide() -> None:
    """同名以盘上为准（老口径）：回传的账不得覆盖已收集的路径。"""
    disk = Path("/tmp/it1/w1/rl_s0_seed1")
    cands: dict[str, list[Path]] = {disk.name: [disk]}
    out = merge_memory_shards(
        ShardMergeSpec(
            it_dir=Path("/tmp/it1"),
            cands=cands,
            mem={disk.name: _manifest(disk.name)},
            counters=(0, 0, 0, "", ""),
            mver_bad={},
        )
    )
    assert out == (0, 0, 0, "", "")
    assert cands[disk.name] == [disk]


def test_merge_filters_by_state_init_and_metrics_version() -> None:
    """两个开关各管一路：state_init 口径不符 / metrics 版本不符都要计数并跳过。"""
    mem = {
        # `state_init=True` 要求 manifest 带正整数 `initTick`（P1 只在真注入快照时才写）
        "old_state": _manifest("old_state"),
        "bad_mver": _manifest("bad_mver", initTick=5, metrics_version=0),
        "good": _manifest("good", initTick=5, metrics_version=1),
    }
    cands: dict[str, list[Path]] = {}
    mver_bad: dict[int | None, int] = {}
    out = merge_memory_shards(
        ShardMergeSpec(
            it_dir=Path("/tmp/it1"),
            cands=cands,
            mem=mem,
            counters=(0, 0, 0, "", ""),
            mver_bad=mver_bad,
            state_init=True,
            metrics_version=1,
        )
    )
    skipped, skipped_init, skipped_mver, first_init, first_mver = out
    assert skipped == 0
    assert skipped_init == 1 and first_init == "old_state"
    assert skipped_mver == 1 and first_mver == "bad_mver"
    assert mver_bad == {0: 1}
    assert list(cands) == ["good"]
